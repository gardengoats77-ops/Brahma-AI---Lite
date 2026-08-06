# tests/test_pi_platform.py
import os
import platform
from unittest import mock


def test_is_raspberry_pi_true_when_arm_linux_with_rpi_kernel():
    """On a Pi, uname reports aarch64 + rpi in the kernel release."""
    with mock.patch.object(platform, "machine", return_value="aarch64"), \
         mock.patch.object(os, "name", "posix"), \
         mock.patch.object(platform, "system", return_value="Linux"), \
         mock.patch.object(os, "uname", return_value=mock.Mock(
             machine="aarch64", release="6.18.39+rpt-rpi-2712",
             sysname="Linux", nodename="star-server", version="#1 SMP PREEMPT")):
        from pi.platform import is_raspberry_pi, get_platform_name
        assert is_raspberry_pi() is True
        assert get_platform_name() == "raspberry-pi"


def test_is_raspberry_pi_false_on_x86():
    """An x86 Linux desktop must not be detected as a Pi."""
    with mock.patch.object(platform, "machine", return_value="x86_64"), \
         mock.patch.object(os, "name", "posix"), \
         mock.patch.object(platform, "system", return_value="Linux"), \
         mock.patch.object(os, "uname", return_value=mock.Mock(
             machine="x86_64", release="6.0.0-generic",
             sysname="Linux", nodename="gwuap", version="#1")):
        from pi.platform import is_raspberry_pi
        assert is_raspberry_pi() is False


def test_is_raspberry_pi_false_on_windows():
    """Windows must not be detected as a Pi."""
    with mock.patch.object(platform, "machine", return_value="AMD64"), \
         mock.patch.object(os, "name", "nt"), \
         mock.patch.object(platform, "system", return_value="Windows"):
        from pi.platform import is_raspberry_pi
        assert is_raspberry_pi() is False


def test_get_platform_name_desktop():
    with mock.patch.object(platform, "machine", return_value="x86_64"), \
         mock.patch.object(os, "name", "posix"), \
         mock.patch.object(platform, "system", return_value="Linux"), \
         mock.patch.object(os, "uname", return_value=mock.Mock(
             machine="x86_64", release="6.0.0-generic",
             sysname="Linux", nodename="gwuap", version="#1")):
        from pi.platform import get_platform_name
        assert get_platform_name() == "desktop"
