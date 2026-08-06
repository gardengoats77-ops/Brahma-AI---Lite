"""GPIO backend for luma.lcd using lgpio on Raspberry Pi 5.

RPi.GPIO can't access the Pi 5's RP1 GPIO controller's memory-mapped
registers, so luma.core's default GPIO backend fails with
"Cannot determine SOC peripheral base address".

This module provides the minimal GPIO interface luma.core expects
(setup, output, HIGH, LOW, cleanup) using lgpio's character device API,
which works on Pi 5.

Usage:
    from pi.lgpio_backend import LgpioBackend
    from luma.core.interface.serial import spi
    from luma.lcd.device import st7789

    gpio = LgpioBackend()
    serial = spi(gpio=gpio, port=0, device=0, bus_speed_hz=40000000,
                 gpio_DC=24, gpio_RST=25)
    device = st7789(serial, width=240, height=240)
"""
from __future__ import annotations

import logging
from typing import Optional

try:
    import lgpio
    _LGPIO_AVAILABLE = True
except Exception:
    _LGPIO_AVAILABLE = False

log = logging.getLogger("whisplay.lgpio")


class LgpioBackend:
    """Minimal GPIO backend for luma.core using lgpio (Pi 5 compatible).

    Implements the subset of RPi.GPIO's API that luma.core.interface.serial
    bitbang uses: HIGH, LOW, OUT, setup(pin, mode), output(pin, val),
    cleanup().
    """

    HIGH = 1
    LOW = 0
    OUT = "out"
    IN = "in"

    def __init__(self):
        if not _LGPIO_AVAILABLE:
            raise RuntimeError("lgpio not available — cannot drive SPI TFT on Pi 5")
        self._chip = lgpio.gpiochip_open(0)
        self._pins: set[int] = set()
        log.info("lgpio backend initialized on chip 0")

    def setup(self, pin: int, mode: str) -> None:
        if mode == self.OUT:
            lgpio.gpio_claim_output(self._chip, pin)
        else:
            lgpio.gpio_claim_input(self._chip, pin)
        self._pins.add(pin)

    def output(self, pin: int, value: int) -> None:
        lgpio.gpio_write(self._chip, pin, value)

    def input(self, pin: int) -> int:
        return lgpio.gpio_read(self._chip, pin)

    def cleanup(self, pin: Optional[int] = None) -> None:
        if pin is not None:
            if pin in self._pins:
                try:
                    lgpio.gpio_free(self._chip, pin)
                except Exception:
                    pass
                self._pins.discard(pin)
        else:
            for p in list(self._pins):
                try:
                    lgpio.gpio_free(self._chip, p)
                except Exception:
                    pass
            self._pins.clear()
            try:
                lgpio.gpiochip_close(self._chip)
            except Exception:
                pass
