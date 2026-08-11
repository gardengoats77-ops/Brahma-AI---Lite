# tests/test_home_auto.py
"""Tests for the MQTT Home Assistant bridge (pi/home_auto.py).

Covers topic construction, payload serialization, broker connection
graceful degradation, and the home_control voice tool.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from pi import home_auto


# ---------------------------------------------------------------------------
# Test: mqtt_publish constructs correct topic and payload
# ---------------------------------------------------------------------------

class TestMqttPublish:
    """Tests for home_auto.mqtt_publish()."""

    def test_mqtt_publish_light_on(self):
        """Publishing a light ON should hit homeassistant/light/<name>/set
        with a JSON payload containing state=ON."""
        mock_client = MagicMock()
        mock_client.is_connected.return_value = True
        mock_client.connect.return_value = 0  # MQTT_ERR_SUCCESS
        mock_client.publish.return_value.rc = 0  # MQTT_ERR_SUCCESS

        with patch.object(home_auto, "_get_mqtt_client", return_value=mock_client):
            result = home_auto.mqtt_publish("light", "living_room", "ON")

        assert result is True
        # Verify publish was called with the right topic
        mock_client.publish.assert_called_once()
        call_args = mock_client.publish.call_args
        assert call_args[0][0] == "homeassistant/light/living_room/set"
        payload = json.loads(call_args[0][1])
        assert payload["state"] == "ON"

    def test_mqtt_publish_switch_off(self):
        """Publishing a switch OFF should hit homeassistant/switch/<name>/set."""
        mock_client = MagicMock()
        mock_client.is_connected.return_value = True
        mock_client.connect.return_value = 0
        mock_client.publish.return_value.rc = 0  # MQTT_ERR_SUCCESS

        with patch.object(home_auto, "_get_mqtt_client", return_value=mock_client):
            result = home_auto.mqtt_publish("switch", "bedroom_fan", "OFF")

        assert result is True
        call_args = mock_client.publish.call_args
        assert call_args[0][0] == "homeassistant/switch/bedroom_fan/set"
        payload = json.loads(call_args[0][1])
        assert payload["state"] == "OFF"

    def test_mqtt_publish_with_brightness(self):
        """Brightness should be included in the payload when provided."""
        mock_client = MagicMock()
        mock_client.is_connected.return_value = True
        mock_client.connect.return_value = 0
        mock_client.publish.return_value.rc = 0  # MQTT_ERR_SUCCESS

        with patch.object(home_auto, "_get_mqtt_client", return_value=mock_client):
            result = home_auto.mqtt_publish("light", "kitchen", "ON", brightness=128)

        assert result is True
        payload = json.loads(mock_client.publish.call_args[0][1])
        assert payload["state"] == "ON"
        assert payload["brightness"] == 128

    def test_mqtt_publish_with_color_temp(self):
        """Color temp should be included in the payload when provided."""
        mock_client = MagicMock()
        mock_client.is_connected.return_value = True
        mock_client.connect.return_value = 0
        mock_client.publish.return_value.rc = 0  # MQTT_ERR_SUCCESS

        with patch.object(home_auto, "_get_mqtt_client", return_value=mock_client):
            result = home_auto.mqtt_publish("light", "office", "ON", color_temp=370)

        assert result is True
        payload = json.loads(mock_client.publish.call_args[0][1])
        assert payload["color_temp"] == 370

    def test_mqtt_publish_no_broker_returns_false(self):
        """When no broker is configured, mqtt_publish should return False
        without raising."""
        with patch.object(home_auto, "_get_mqtt_client", return_value=None):
            result = home_auto.mqtt_publish("light", "living_room", "ON")

        assert result is False

    def test_mqtt_publish_not_connected_returns_false(self):
        """When client exists but is not connected, should attempt connect
        and return False if connect fails."""
        mock_client = MagicMock()
        mock_client.is_connected.return_value = False
        mock_client.connect.return_value = 1  # failure

        with patch.object(home_auto, "_get_mqtt_client", return_value=mock_client):
            result = home_auto.mqtt_publish("light", "living_room", "ON")

        assert result is False


# ---------------------------------------------------------------------------
# Test: home_control voice tool
# ---------------------------------------------------------------------------

class TestHomeControlTool:
    """Tests for home_auto.home_control() — the voice tool wrapper."""

    def test_home_control_on(self, monkeypatch):
        """home_control('living_room', 'on') should publish ON."""
        calls = []

        def fake_publish(dev_type, name, state, **kwargs):
            calls.append((dev_type, name, state, kwargs))
            return True

        monkeypatch.setattr(home_auto, "mqtt_publish", fake_publish)

        result = home_auto.home_control("living_room", "on")
        assert result is True
        assert len(calls) == 1
        assert calls[0][0] == "light"
        assert calls[0][1] == "living_room"
        assert calls[0][2] == "ON"

    def test_home_control_off(self, monkeypatch):
        """home_control('bedroom', 'off') should publish OFF."""
        calls = []

        def fake_publish(dev_type, name, state, **kwargs):
            calls.append((dev_type, name, state, kwargs))
            return True

        monkeypatch.setattr(home_auto, "mqtt_publish", fake_publish)

        result = home_auto.home_control("bedroom", "off")
        assert result is True
        assert calls[0][2] == "OFF"

    def test_home_control_toggle(self, monkeypatch):
        """home_control with 'toggle' action should publish TOGGLE."""
        calls = []

        def fake_publish(dev_type, name, state, **kwargs):
            calls.append((dev_type, name, state, kwargs))
            return True

        monkeypatch.setattr(home_auto, "mqtt_publish", fake_publish)

        result = home_auto.home_control("porch", "toggle")
        assert result is True
        assert calls[0][2] == "TOGGLE"

    def test_home_control_with_brightness(self, monkeypatch):
        """home_control with brightness kwarg should pass it through."""
        calls = []

        def fake_publish(dev_type, name, state, **kwargs):
            calls.append((dev_type, name, state, kwargs))
            return True

        monkeypatch.setattr(home_auto, "mqtt_publish", fake_publish)

        result = home_auto.home_control("living_room", "on", brightness=200)
        assert result is True
        assert calls[0][3]["brightness"] == 200

    def test_home_control_with_color_temp(self, monkeypatch):
        """home_control with color_temp kwarg should pass it through."""
        calls = []

        def fake_publish(dev_type, name, state, **kwargs):
            calls.append((dev_type, name, state, kwargs))
            return True

        monkeypatch.setattr(home_auto, "mqtt_publish", fake_publish)

        result = home_auto.home_control("office", "on", color_temp=300)
        assert result is True
        assert calls[0][3]["color_temp"] == 300

    def test_home_control_mqtt_not_configured(self, monkeypatch):
        """When MQTT is not available, home_control should return a
        user-friendly string."""
        monkeypatch.setattr(home_auto, "mqtt_publish", lambda *a, **kw: False)

        result = home_auto.home_control("living_room", "on")
        assert result == "MQTT not configured"

    def test_home_control_switch_type(self, monkeypatch):
        """home_control with device_type='switch' should use switch topic."""
        calls = []

        def fake_publish(dev_type, name, state, **kwargs):
            calls.append((dev_type, name, state, kwargs))
            return True

        monkeypatch.setattr(home_auto, "mqtt_publish", fake_publish)

        result = home_auto.home_control("garage", "on", device_type="switch")
        assert result is True
        assert calls[0][0] == "switch"


# ---------------------------------------------------------------------------
# Test: topic construction helper
# ---------------------------------------------------------------------------

class TestTopicConstruction:
    """Tests for home_auto._build_topic()."""

    def test_light_topic(self):
        assert home_auto._build_topic("light", "living_room") == \
            "homeassistant/light/living_room/set"

    def test_switch_topic(self):
        assert home_auto._build_topic("switch", "garage") == \
            "homeassistant/switch/garage/set"

    def test_topic_with_spaces(self):
        """Device names with spaces should be lowercased and use underscores."""
        topic = home_auto._build_topic("light", "Living Room")
        assert topic == "homeassistant/light/living_room/set"


# ---------------------------------------------------------------------------
# Test: payload construction
# ---------------------------------------------------------------------------

class TestPayloadConstruction:
    """Tests for home_auto._build_payload()."""

    def test_payload_on(self):
        payload = home_auto._build_payload("ON")
        assert payload == {"state": "ON"}

    def test_payload_off(self):
        payload = home_auto._build_payload("OFF")
        assert payload == {"state": "OFF"}

    def test_payload_with_brightness(self):
        payload = home_auto._build_payload("ON", brightness=255)
        assert payload == {"state": "ON", "brightness": 255}

    def test_payload_with_color_temp(self):
        payload = home_auto._build_payload("ON", color_temp=370)
        assert payload == {"state": "ON", "color_temp": 370}

    def test_payload_with_both(self):
        payload = home_auto._build_payload("ON", brightness=128, color_temp=400)
        assert payload == {"state": "ON", "brightness": 128, "color_temp": 400}