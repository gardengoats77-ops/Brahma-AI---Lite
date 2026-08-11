# pi/home_auto.py
"""MQTT Home Assistant bridge for voice-controlled home automation.

Voice flow: "Hey Rex, turn on the living room lights"
  -> home_control(device="living_room", action="on")
  -> mqtt_publish("light", "living_room", "ON")
  -> publish to homeassistant/light/living_room/set
  -> Home Assistant picks up the MQTT message and controls the light.

Configuration via environment variables:
  MQTT_BROKER   — broker hostname (default: localhost)
  MQTT_PORT     — broker port (default: 1883)
  MQTT_USER     — optional username for auth
  MQTT_PASS     — optional password for auth
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

log = logging.getLogger("brahma.home_auto")

MQTT_BROKER = os.environ.get("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USER", "")
MQTT_PASS = os.environ.get("MQTT_PASS", "")

# Module-level client (lazy-initialized)
_client: Optional[object] = None


def _build_topic(device_type: str, name: str) -> str:
    """Build the MQTT topic for a device set command.

    Device names with spaces are lowercased and spaces replaced with underscores.
    """
    safe_name = name.lower().strip().replace(" ", "_")
    return f"homeassistant/{device_type}/{safe_name}/set"


def _build_payload(state: str, brightness: Optional[int] = None,
                   color_temp: Optional[int] = None) -> dict:
    """Build the JSON payload for an MQTT set command."""
    payload: dict = {"state": state}
    if brightness is not None:
        payload["brightness"] = brightness
    if color_temp is not None:
        payload["color_temp"] = color_temp
    return payload


def _get_mqtt_client() -> Optional[object]:
    """Get or create the paho-mqtt client singleton.

    Returns None if paho-mqtt is not installed or if MQTT_BROKER is
    explicitly set to an empty string (meaning MQTT is disabled).
    """
    global _client

    # If user explicitly disables MQTT, return None
    if os.environ.get("MQTT_BROKER") == "":
        return None

    if _client is not None:
        return _client

    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        log.warning("paho-mqtt not installed — home automation disabled")
        return None

    try:
        c = mqtt.Client(protocol=mqtt.MQTTv311)
        if MQTT_USER:
            c.username_pw_set(MQTT_USER, MQTT_PASS or None)
        c.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        c.loop_start()
        _client = c
        log.info("Connected to MQTT broker at %s:%d", MQTT_BROKER, MQTT_PORT)
        return c
    except Exception as e:  # noqa: BLE001
        log.warning("MQTT connection failed: %s", e)
        return None


def mqtt_publish(device_type: str, name: str, state: str,
                 brightness: Optional[int] = None,
                 color_temp: Optional[int] = None) -> bool:
    """Publish a command to an MQTT device topic.

    Returns True if publish succeeded, False otherwise (no broker,
    not connected, paho-mqtt missing).
    """
    client = _get_mqtt_client()
    if client is None:
        return False

    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        return False

    topic = _build_topic(device_type, name)
    payload = json.dumps(_build_payload(state, brightness, color_temp))

    try:
        result = client.publish(topic, payload, qos=1)
        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            log.info("Published to %s: %s", topic, payload)
            return True
        else:
            log.warning("Publish returned rc=%s for %s", result.rc, topic)
            return False
    except Exception as e:  # noqa: BLE001
        log.error("Publish failed: %s", e)
        return False


def home_control(device: str, action: str, *,
                 brightness: Optional[int] = None,
                 color_temp: Optional[int] = None,
                 device_type: str = "light") -> str | bool:
    """Voice tool: control a home automation device.

    Args:
        device: device name (e.g., "living_room", "bedroom")
        action: "on", "off", or "toggle"
        brightness: optional 0-255 brightness level
        color_temp: optional color temperature in mireds
        device_type: "light" or "switch" (default: "light")

    Returns:
        True on success, or "MQTT not configured" string on failure.
    """
    # Normalize action
    action_upper = action.upper().strip()
    if action_upper not in ("ON", "OFF", "TOGGLE"):
        return f"unknown action '{action}'"

    success = mqtt_publish(device_type, device, action_upper,
                          brightness=brightness, color_temp=color_temp)
    if success:
        return True
    return "MQTT not configured"