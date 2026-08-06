"""Platform detection for the Pi build of AlmightyAI (Brahma AI Lite).

Detects whether we're running on a Raspberry Pi so that the entrypoint can
decide whether to boot the PyQt6 desktop UI (x86 dev machine) or the
embedded headless voice loop (Pi 5 + Hailo + Whisplay HAT).

Detection logic:
  1. Must be Linux (os.name == 'posix', platform.system() == 'Linux')
  2. Must be ARM (platform.machine() starts with 'arm' or 'aarch64')
  3. Kernel release should contain 'rpi' or 'raspi' (Raspberry Pi OS kernels)

This avoids false positives on ARM servers (e.g. Ampere Altra) that don't
use the rpi kernel naming.
"""
from __future__ import annotations

import os
import platform


def _is_arm() -> bool:
    """Return True if the CPU architecture is ARM (armv7l or aarch64)."""
    return platform.machine().startswith(("arm", "aarch64"))


def _is_linux() -> bool:
    """Return True if running on a Linux OS."""
    return os.name == "posix" and platform.system() == "Linux"


def is_raspberry_pi() -> bool:
    """Return True if running on a Raspberry Pi.

    Checks for ARM architecture + Linux + rpi kernel release name.
    Returns False on Windows, Mac, x86 Linux desktops, or ARM servers
    with non-rpi kernels.
    """
    if not _is_linux() or not _is_arm():
        return False
    try:
        release = os.uname().release
    except (AttributeError, OSError):
        return False
    return "rpi" in release or "raspi" in release


def get_platform_name() -> str:
    """Return a short identifier for the current platform.

    Returns 'raspberry-pi' on a Pi, 'desktop' everywhere else.
    Used by the entrypoint to choose which UI to boot.
    """
    if is_raspberry_pi():
        return "raspberry-pi"
    return "desktop"
