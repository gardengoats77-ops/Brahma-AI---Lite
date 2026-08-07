#!/usr/bin/env python3
"""Manual Whisplay HAT backlight + panel test.

Renders a solid bright (cyan) frame to the SPI TFT so any real content is
visible regardless of the backlight, then sweeps candidate backlight pins
driving each HIGH for a few seconds. Watch the screen:
  * If the screen lights on a particular pin, that pin is the backlight.
  * If NO pin lights it AND the content is black even so, the panel
    controller/SPI wiring may be wrong.
Run with the GUI stopped (this owns the SPI + GPIO).
"""
import sys
import time

from pi.lgpio_backend import LgpioBackend
from luma.core.interface.serial import spi
from luma.core.render import canvas
from luma.lcd.device import st7789

BACKLIGHT_PINS = [18, 23, 24, 25, 12, 13, 26, 27]
PINS = [18, 23, 24, 25]

import lgpio
CHIP = lgpio.gpiochip_open(0)


def main():
    g = LgpioBackend()
    serial = spi(gpio=g, port=0, device=0, bus_speed_hz=40000000,
                 gpio_DC=17, gpio_RST=22)
    # Create with NO backlight so luma doesn't touch any pin; we drive the
    # backlight manually below.
    dev = st7789(serial, width=240, height=240, gpio=g, backlight=False)

    # Bright cyan frame — unmistakable if the panel or backlight lives.
    with canvas(dev) as draw:
        draw.rectangle([0, 0, 239, 239], fill="cyan")

    print("Panel initialized; driving each backlight pin HIGH for 4s...")
    for pin in BACKLIGHT_PINS:
        try:
            lgpio.gpio_claim_output(CHIP, pin, 1)
        except Exception as e:
            print(f"pin {pin}: {str(e)[:50]}; skipping")
            continue
        print(f"--- GPIO{pin} HIGH (4s) ---", flush=True)
        time.sleep(4)
        try:
            lgpio.gpio_write(CHIP, pin, 0)
            lgpio.gpio_free(CHIP, pin)
        except Exception as e:
            print(f"pin {pin} release: {str(e)[:40]}")

    print("Sweep done. Which pin (if any) lit the screen?", flush=True)
    lgpio.gpiochip_close(CHIP)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("ERR:", e, flush=True)
        sys.exit(1)