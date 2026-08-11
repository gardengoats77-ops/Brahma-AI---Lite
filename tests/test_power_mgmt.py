# tests/test_power_mgmt.py
"""Tests for Pi power management: battery monitoring and low-power mode.

Verifies:
  - Power profile selection based on AC/battery status
  - LED brightness reduction on battery
  - Poll interval increase on battery
  - Low battery announcement when below threshold
  - Graceful degradation when no PMIC is available
"""
from unittest.mock import MagicMock, patch

import pytest

from pi.power_mgmt import (
    POLL_INTERVAL_AC,
    POLL_INTERVAL_BATTERY,
    LOW_BATTERY_THRESHOLD,
    LED_BRIGHTNESS_AC,
    LED_BRIGHTNESS_BATTERY,
    PowerProfile,
    PowerManager,
)


class TestPowerProfiles:
    """Power profile constants and selection."""

    def test_poll_interval_ac_is_faster(self):
        assert POLL_INTERVAL_AC < POLL_INTERVAL_BATTERY

    def test_low_battery_threshold_positive(self):
        assert 0 < LOW_BATTERY_THRESHOLD < 100

    def test_led_brightness_battery_is_dimmer(self):
        assert LED_BRIGHTNESS_BATTERY < LED_BRIGHTNESS_AC

    def test_power_profile_enum_values(self):
        assert PowerProfile.AC.value == "ac"
        assert PowerProfile.BATTERY.value == "battery"
        assert PowerProfile.LOW.value == "low"


class TestPowerManagerNoPMIC:
    """Graceful degradation when no PMIC is available."""

    def test_available_is_false_without_pmic(self):
        with patch("pi.power_mgmt._pmic_available", return_value=False):
            pm = PowerManager()
            assert pm.available is False

    def test_profile_is_ac_without_pmic(self):
        with patch("pi.power_mgmt._pmic_available", return_value=False):
            pm = PowerManager()
            assert pm.current_profile == PowerProfile.AC

    def test_poll_interval_ac_without_pmic(self):
        with patch("pi.power_mgmt._pmic_available", return_value=False):
            pm = PowerManager()
            assert pm.get_poll_interval() == POLL_INTERVAL_AC

    def test_led_brightness_ac_without_pmic(self):
        with patch("pi.power_mgmt._pmic_available", return_value=False):
            pm = PowerManager()
            assert pm.get_led_brightness() == LED_BRIGHTNESS_AC

    def test_no_announcement_without_pmic(self):
        with patch("pi.power_mgmt._pmic_available", return_value=False):
            pm = PowerManager()
            tts = MagicMock()
            pm.monitor_and_adjust(tts)
            tts.speak.assert_not_called()


class TestPowerManagerOnBattery:
    """Low-power mode when running on battery."""

    def test_low_power_on_battery(self):
        """Primary acceptance test: on battery, dim LED and reduce poll frequency.

        When PMIC reports battery power (not charging), the manager should:
          - Switch to BATTERY profile
          - Reduce LED brightness to 50%
          - Increase poll interval to 5s
        """
        mock_pmic = MagicMock()
        mock_pmic.is_charging.return_value = False
        mock_pmic.is_ac_power.return_value = False
        mock_pmic.get_battery_percentage.return_value = 75

        with patch("pi.power_mgmt._pmic_available", return_value=True), \
             patch("pi.power_mgmt._create_pmic", return_value=mock_pmic):
            pm = PowerManager()
            assert pm.available is True

            # Trigger power state evaluation
            pm._evaluate_power_state()

            # Should be on battery profile
            assert pm.current_profile == PowerProfile.BATTERY

            # LED should be dimmed to 50%
            assert pm.get_led_brightness() == LED_BRIGHTNESS_BATTERY

            # Poll interval should be slower (5s)
            assert pm.get_poll_interval() == POLL_INTERVAL_BATTERY

    def test_battery_percentage_read(self):
        mock_pmic = MagicMock()
        mock_pmic.get_battery_percentage.return_value = 42

        with patch("pi.power_mgmt._pmic_available", return_value=True), \
             patch("pi.power_mgmt._create_pmic", return_value=mock_pmic):
            pm = PowerManager()
            assert pm.get_battery_percentage() == 42


class TestLowBatteryAnnouncement:
    """Announce low battery when below threshold."""

    def test_announce_low_battery(self):
        """When battery < threshold, announce 'Low battery, connect charger'."""
        mock_pmic = MagicMock()
        mock_pmic.is_charging.return_value = False
        mock_pmic.is_ac_power.return_value = False
        mock_pmic.get_battery_percentage.return_value = 10

        with patch("pi.power_mgmt._pmic_available", return_value=True), \
             patch("pi.power_mgmt._create_pmic", return_value=mock_pmic):
            pm = PowerManager()
            tts = MagicMock()
            pm.monitor_and_adjust(tts)

            # Should switch to LOW profile
            assert pm.current_profile == PowerProfile.LOW

            # Should announce low battery
            tts.speak.assert_called_once()
            spoken = tts.speak.call_args[0][0]
            assert "Low battery" in spoken
            assert "connect charger" in spoken

    def test_no_announce_when_charging_on_low_battery(self):
        """If plugged in, don't announce even if battery is low."""
        mock_pmic = MagicMock()
        mock_pmic.is_charging.return_value = True
        mock_pmic.is_ac_power.return_value = True
        mock_pmic.get_battery_percentage.return_value = 10

        with patch("pi.power_mgmt._pmic_available", return_value=True), \
             patch("pi.power_mgmt._create_pmic", return_value=mock_pmic):
            pm = PowerManager()
            tts = MagicMock()
            pm.monitor_and_adjust(tts)

            # Charging — should not announce
            tts.speak.assert_not_called()

    def test_no_announce_when_above_threshold(self):
        """If battery above threshold, don't announce."""
        mock_pmic = MagicMock()
        mock_pmic.is_charging.return_value = False
        mock_pmic.is_ac_power.return_value = False
        mock_pmic.get_battery_percentage.return_value = 50

        with patch("pi.power_mgmt._pmic_available", return_value=True), \
             patch("pi.power_mgmt._create_pmic", return_value=mock_pmic):
            pm = PowerManager()
            tts = MagicMock()
            pm.monitor_and_adjust(tts)

            # Above threshold — should not announce
            tts.speak.assert_not_called()