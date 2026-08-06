# tests/test_pi_main.py
"""Tests for the pi_main.py headless voice loop entrypoint.

Tests that BootState and build_health_payload are importable and return
the right shape even when no HAT hardware is present.
"""
import os
import sys
from unittest import mock


def test_boot_state_has_all_hardware_flags():
    """BootState.as_dict must include all expected hardware discovery flags."""
    from pi_main import BootState
    state = BootState.as_dict()
    assert "platform_ok" in state
    assert "mic_available" in state
    assert "speaker_available" in state
    assert "display_available" in state
    assert "hailo_available" in state


def test_build_health_payload_returns_status():
    """build_health_payload must return 'online' or 'degraded' with hardware info."""
    from pi_main import build_health_payload
    payload = build_health_payload()
    assert payload["status"] in ("online", "degraded")
    assert "mic_available" in payload
    assert "hailo_available" in payload
    assert "hardware" in payload


def test_build_health_payload_offline_when_no_mic():
    """When mic is unavailable, status should be 'degraded'."""
    from pi_main import BootState, build_health_payload, _STATE
    original = _STATE.mic_available
    _STATE.mic_available = False
    try:
        payload = build_health_payload()
        assert payload["status"] == "degraded"
    finally:
        _STATE.mic_available = original
