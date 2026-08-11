# pi/power_mgmt.py
"""Battery and power management for mobile Pi (PiSugar PMIC).

Reads battery voltage and charging status from the PiSugar PMIC via I2C
(address 0x34) and adjusts the power profile:
  - AC: full power (LED 100%, poll 2s)
  - BATTERY: dim LED 50%, poll 5s
  - LOW: critical battery, announce "Low battery, connect charger"

Gracefully degrades when no PMIC is available — returns AC defaults.
"""
from __future__ import annotations

import logging
import time
from enum import Enum
from typing import Optional, Protocol

log = logging.getLogger("brahma.power_mgmt")

# Power profile constants
POLL_INTERVAL_AC = 2.0
POLL_INTERVAL_BATTERY = 5.0
LOW_BATTERY_THRESHOLD = 15  # percent
LED_BRIGHTNESS_AC = 1.0
LED_BRIGHTNESS_BATTERY = 0.5

# PiSugar PMIC I2C constants
PMIC_I2C_ADDRESS = 0x34
PMIC_I2C_BUS = 1


class PowerProfile(Enum):
    """Power management profiles."""
    AC = "ac"
    BATTERY = "battery"
    LOW = "low"


class PMICInterface(Protocol):
    """Protocol for PMIC (Power Management IC) devices."""

    def is_charging(self) -> bool: ...
    def is_ac_power(self) -> bool: ...
    def get_battery_percentage(self) -> int: ...
    def get_battery_voltage(self) -> float: ...


class PiSugarPMIC:
    """PiSugar Whisplay HAT PMIC driver (I2C address 0x34).

    Reads battery percentage and charging status from the PiSugar PMIC.
    Falls back to estimation if reads fail.
    """

    def __init__(self, bus: int = PMIC_I2C_BUS, address: int = PMIC_I2C_ADDRESS) -> None:
        self.bus = bus
        self.address = address
        self._smbus = None
        self._available = False
        try:
            import smbus2 as smbus
            self._smbus = smbus.SMBus(bus)
            self._available = True
            log.info("PiSugar PMIC initialized on I2C bus %d, address 0x%02x", bus, address)
        except (ImportError, OSError) as e:
            log.warning("PiSugar PMIC not available: %s", e)
            self._available = False

    @property
    def available(self) -> bool:
        return self._available

    def is_charging(self) -> bool:
        """Return True if AC power is connected and charging."""
        if not self._available:
            return True  # Assume AC if no PMIC
        try:
            # Read charging status register
            val = self._smbus.read_byte_data(self.address, 0x01)
            return bool(val & 0x01)
        except OSError:
            return True  # Assume AC on read failure

    def is_ac_power(self) -> bool:
        """Return True if AC power is connected."""
        if not self._available:
            return True
        try:
            val = self._smbus.read_byte_data(self.address, 0x01)
            return bool(val & 0x02)
        except OSError:
            return True

    def get_battery_percentage(self) -> int:
        """Return battery percentage (0-100)."""
        if not self._available:
            return 100
        try:
            # Battery percentage register
            val = self._smbus.read_byte_data(self.address, 0x04)
            return max(0, min(100, val))
        except OSError:
            return 100

    def get_battery_voltage(self) -> float:
        """Return battery voltage in volts."""
        if not self.available:
            return 4.2
        try:
            # Voltage registers (16-bit)
            msb = self._smbus.read_byte_data(self.address, 0x06)
            lsb = self._smbus.read_byte_data(self.address, 0x07)
            raw = (msb << 8) | lsb
            return raw / 1000.0  # Convert mV to V
        except OSError:
            return 4.2


def _pmic_available() -> bool:
    """Check if the PMIC is available without creating a persistent instance."""
    try:
        import smbus2
        bus = smbus2.SMBus(PMIC_I2C_BUS)
        bus.close()
        return True
    except (ImportError, OSError):
        return False


def _create_pmic() -> Optional[PMICInterface]:
    """Factory: create a PMIC instance, return None if unavailable."""
    try:
        pmic = PiSugarPMIC()
        if pmic.available:
            return pmic
        return None
    except Exception:
        return None


class PowerManager:
    """Manages power profiles for the mobile Pi.

    Monitors battery state and adjusts LED brightness, poll frequency,
    and announces low battery via TTS.

    When no PMIC is available, gracefully degrades to AC defaults.
    """

    def __init__(self, pmic: Optional[PMICInterface] = None) -> None:
        self._pmic = pmic or _create_pmic()
        self._available = self._pmic is not None
        self._current_profile = PowerProfile.AC
        self._low_battery_announced = False
        self._last_check = 0.0
        self._check_interval = 10.0  # seconds between PMIC polls

    @property
    def available(self) -> bool:
        """True if PMIC is available."""
        return self._available

    @property
    def current_profile(self) -> PowerProfile:
        """Current power profile."""
        return self._current_profile

    def get_poll_interval(self) -> float:
        """Return the recommended poll interval in seconds."""
        if self._current_profile == PowerProfile.AC:
            return POLL_INTERVAL_AC
        return POLL_INTERVAL_BATTERY

    def get_led_brightness(self) -> float:
        """Return LED brightness (0.0-1.0) based on power profile."""
        if self._current_profile == PowerProfile.AC:
            return LED_BRIGHTNESS_AC
        return LED_BRIGHTNESS_BATTERY

    def get_battery_percentage(self) -> int:
        """Return battery percentage (0-100). Returns 100 if no PMIC."""
        if not self._available:
            return 100
        return self._pmic.get_battery_percentage()

    def _evaluate_power_state(self) -> None:
        """Evaluate power state and update profile."""
        if not self._available:
            self._current_profile = PowerProfile.AC
            return

        if self._pmic.is_charging() or self._pmic.is_ac_power():
            self._current_profile = PowerProfile.AC
            self._low_battery_announced = False
            return

        pct = self._pmic.get_battery_percentage()
        if pct <= LOW_BATTERY_THRESHOLD:
            self._current_profile = PowerProfile.LOW
        else:
            self._current_profile = PowerProfile.BATTERY
            self._low_battery_announced = False

    def monitor_and_adjust(self, tts=None) -> PowerProfile:
        """Monitor battery state and adjust power profile.

        Args:
            tts: Optional TTS object with .speak() method for announcements.

        Returns:
            Current PowerProfile after evaluation.
        """
        now = time.monotonic()
        if now - self._last_check < self._check_interval:
            return self._current_profile
        self._last_check = now

        self._evaluate_power_state()

        # Announce low battery
        if self._current_profile == PowerProfile.LOW and not self._low_battery_announced:
            log.warning("Low battery: %d%%", self.get_battery_percentage())
            if tts and hasattr(tts, "speak"):
                tts.speak("Low battery, connect charger")
            self._low_battery_announced = True

        return self._current_profile