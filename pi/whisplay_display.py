"""Whisplay HAT SPI TFT status display.

Uses ``luma.lcd`` (spidev) to push text lines to the small IPS panel.
All hardware calls are wrapped so a missing panel or missing luma package
degrades to a log-only no-op — the voice loop must never block on
display IO.

Hardware notes (confirmed live on star-server 2026-08-05):
  * /dev/spidev0.0 and /dev/spidev0.1 exist on the Pi
  * No /dev/fb1 — the TFT is raw SPI, so luma.lcd (not framebuffer) is needed
  * Panel is likely ST7789 or ST7735 (standard SPI TFT in PiSugar Whisplay)
  * Pi 5 GPIO requires lgpio backend (RPi.GPIO can't access RP1 controller)

If luma.lcd is not installed (ARM pip), the module silently logs and
``available`` is False — updates just go to the logger.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

log = logging.getLogger("whisplay.display")

# Visible chars per line on a ~1.14" panel at the default font size.
_COLS = 16
# Visible text rows we render.
_ROWS = 6
_CHAR_HEIGHT = 10

# Try to import luma — optional dependency. If missing, module is a no-op.
try:
    from luma.core.interface.serial import spi as _spi_iface
    from luma.core.render import canvas as _canvas
    # luma.oled re-exports some device drivers; luma.lcd is the right import
    # but may not be installed.
    try:
        from luma.lcd.device import st7789 as _device_cls
    except ImportError:
        from luma.oled.device import st7789 as _device_cls
    _LUMA_AVAILABLE = True
except Exception as e:  # noqa: BLE001
    log.info("luma.lcd not available — display disabled: %s", e)
    _LUMA_AVAILABLE = False

# Try to import lgpio backend for Pi 5 GPIO access
try:
    from pi.lgpio_backend import LgpioBackend
    _LGPIO_AVAILABLE = True
except Exception as e:  # noqa: BLE001
    log.info("lgpio backend not available: %s", e)
    _LGPIO_AVAILABLE = False


def _wrap_line(text: str, width: int = _COLS) -> list[str]:
    """Word-wrap a line so it fits on the narrow panel.

    Words longer than ``width`` are hard-split across multiple lines.
    An empty input returns ``[""]`` so callers always get at least one line.
    """
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    cur = ""
    for w in words:
        if len(cur) + len(w) + (1 if cur else 0) <= width:
            cur = (cur + " " + w) if cur else w
        else:
            if cur:
                lines.append(cur)
            # Word longer than width — hard-split.
            while len(w) > width:
                lines.append(w[:width])
                w = w[width:]
            cur = w
    if cur:
        lines.append(cur)
    return lines


class WhisplayDisplay:
    """SPI TFT text renderer with fail-safe fallback."""

    def __init__(self, spi_port: int = 0, cs: int = 0, width: int = 240, height: int = 240):
        self.width = width
        self.height = height
        self._device: Optional[object] = None
        self._gpio_backend = None
        if not _LUMA_AVAILABLE:
            return
        spi_path = f"/dev/spidev{spi_port}.{cs}"
        if not os.path.exists(spi_path):
            log.warning("%s not found — display disabled", spi_path)
            return
        try:
            # Use lgpio backend for Pi 5 GPIO access (RPi.GPIO can't access RP1 controller)
            if _LGPIO_AVAILABLE:
                self._gpio_backend = LgpioBackend()
                # Use free GPIOs: 17 (DC), 22 (RST). Disable backlight since
                # gpio 23 is claimed by kernel driver on this Whisplay HAT.
                serial = _spi_iface(
                    gpio=self._gpio_backend,
                    port=spi_port,
                    device=cs,
                    bus_speed_hz=40000000,
                    gpio_DC=17,
                    gpio_RST=22,
                )
                # Pass the gpio backend to the device, disable backlight
                self._device = _device_cls(
                    serial,
                    width=width,
                    height=height,
                    gpio=self._gpio_backend,
                    backlight=False,
                )
            else:
                # Fallback to default (RPi.GPIO) — works on Pi 4 and earlier
                serial = _spi_iface(port=spi_port, device=cs, bus_speed_hz=40000000)
                self._device = _device_cls(serial, width=width, height=height)
            log.info("Whisplay SPI display initialized on %s", spi_path)
        except Exception as e:  # noqa: BLE001
            log.warning("Whisplay display init failed: %s", e)
            self._device = None

    @property
    def available(self) -> bool:
        return self._device is not None

    def update(self, text: str, title: str = "") -> None:
        """Render up to ``_ROWS`` lines of wrapped text.

        When the display is unavailable, text is logged at INFO level
        so console output on a headless Pi shows the same information.
        """
        if not self._device:
            if title:
                log.info("display: %s | %s", title, text)
            else:
                log.info("display: %s", text)
            return
        try:
            with _canvas(self._device) as draw:
                y = 0
                if title:
                    draw.text((0, y), title[:_COLS], fill="white")
                    y += _CHAR_HEIGHT + 2
                for ln in _wrap_line(text, _COLS)[:_ROWS]:
                    draw.text((0, y), ln, fill="white")
                    y += _CHAR_HEIGHT
        except Exception as e:  # noqa: BLE001
            log.debug("display render err: %s", e)

    def clear(self) -> None:
        if not self._device:
            return
        try:
            with _canvas(self._device) as draw:
                pass  # blank canvas clears the device
        except Exception:
            pass
