"""Whisplay HAT live dashboard for the REX desktop GUI.

Uses the vendored PiSugar :class:`WhisplayBoard` driver (correct Pi 5 pin
mapping: backlight GPIO22 active-LOW, button GPIO17, gpiochip4, 240x280
RGB565) to paint a live status matrix and expose a physical push-to-talk
button.

Everything is optional at runtime — missing board, GPIO, or deps degrade to
logged no-ops. The GUI must never block on this module.
"""
from __future__ import annotations

import logging
import shutil
import socket
import threading
import time
from typing import Callable, Optional

import numpy as np

try:
    from pi.whisplay_board import WhisplayBoard  # type: ignore
    _BOARD_AVAILABLE = True
    _BOARD_IMPORT_ERR = None
except Exception as e:  # noqa: BLE001
    _BOARD_AVAILABLE = False
    _BOARD_IMPORT_ERR = e

log = logging.getLogger("whisplay.dashboard")

W = 240
H = 280
_BG = (7, 11, 18)
_FG = (0, 255, 160)
_DIM = (120, 140, 160)
_ERR = (255, 80, 80)
_OK = (40, 220, 120)
_FONT = ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 5)
_FONT_BIG = ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 13)


def _rgb565(image) -> bytes:
    """Convert a Pillow RGB image to raw big-endian RGB565 bytes."""
    arr = np.asarray(image.convert("RGB"), dtype=np.uint16)
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    rgb = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    return rgb.astype(">u2").tobytes()


def _cpu() -> float:
    try:
        s = [int(x) for x in __import__("os").popen(
            "grep 'cpu ' /proc/stat | head -1").read().split()[1:5]]
        time.sleep(0.2)
        s2 = [int(x) for x in __import__("os").popen(
            "grep 'cpu ' /proc/stat | head -1").read().split()[1:5]]
        d = [(a - b) for a, b in zip(s2, s)]
        total = sum(d)
        return 100.0 * (1.0 - d[3] / total) if total else 0.0
    except Exception:
        return 0.0


def _temp() -> float:
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as fh:
            return float(fh.read().strip()) / 1000.0
    except Exception:
        return 0.0


def _hailo() -> bool:
    return shutil.which("hailortcli") is not None


def _lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "n/a"


class WhisplayDashboard:
    """Background thread driving the Whisplay TFT + a PTT button."""

    def __init__(self, on_push_to_talk: Optional[Callable[[], None]] = None,
                 poll: float = 2.0):
        self._on_ptt = on_push_to_talk
        self._poll = poll
        self._voice_state = "IDLE"
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._board = None
        if _BOARD_AVAILABLE:
            try:
                self._board = WhisplayBoard()
                # Backlight is active-LOW: 0 = on.
                self._board.set_backlight(0)
                self._board.set_rgb(0, 255, 0)
                if self._on_ptt:
                    self._board.on_button_press(self._ptt_pressed)
            except Exception as e:  # noqa: BLE001
                log.warning("Whisplay board init failed: %s", e)
                self._board = None
        elif _BOARD_IMPORT_ERR:
            log.debug("Whisplay board driver unavailable: %s", _BOARD_IMPORT_ERR)

    @property
    def available(self) -> bool:
        return self._board is not None

    def start(self):
        if not self.available:
            log.info("Whisplay dashboard unavailable — running headless")
            return
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="whisplay-dash")
        self._thread.start()
        self.render()

    def stop(self):
        self._running = False

    def set_voice_state(self, label: str):
        self._voice_state = label
        self.render()

    def _ptt_pressed(self):
        if self._on_ptt:
            try:
                self._on_ptt()
            except Exception as e:  # noqa: BLE001
                log.warning("PTT callback error: %s", e)

    def _loop(self):
        while self._running:
            try:
                self.render()
            except Exception as e:  # noqa: BLE001
                log.warning("whisplay render error: %s", e)
            time.sleep(self._poll)

    def render(self):
        if not self.available:
            return
        try:
            self._board.draw_image(0, 0, W, H, self._frame())
        except Exception as e:  # noqa: BLE001
            log.warning("whisplay paint failed: %s", e)

    def _frame(self) -> bytes:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (W, H), _BG)
        d = ImageDraw.Draw(img)
        try:
            f1 = ImageFont.truetype(*_FONT)
            f2 = ImageFont.truetype(*_FONT_BIG)
        except Exception:
            f1 = f2 = ImageFont.load_default()

        d.text((10, 9), "REX", font=f2, fill=_FG)
        state = self._voice_state
        voice_colour = _OK if ("LISTEN" in state or "SPEAK" in state) else _DIM
        d.text((10, 34), f"voice: {state}", font=f1, fill=voice_colour)
        d.text((10, 56), f"cpu: {_cpu():5.0f}%   tmp: {_temp():4.0f}C",
               font=f1, fill=_FG)
        npn = "ok" if _hailo() else "off"
        d.text((10, 78), f"hailo: {npn}", font=f1,
               fill=_OK if _hailo() else _ERR)
        d.text((10, 100), f"ip: {_lan_ip()}", font=f1, fill=_DIM)
        d.text((10, 122), "<hold=PTT>", font=f1, fill=_DIM)

        return _rgb565(img)


_dash_singleton: Optional[WhisplayDashboard] = None


def get_dashboard(on_push_to_talk=None) -> WhisplayDashboard:
    """Return a process-wide singleton WhisplayDashboard."""
    global _dash_singleton
    if _dash_singleton is None:
        _dash_singleton = WhisplayDashboard(on_push_to_talk=on_push_to_talk)
    return _dash_singleton