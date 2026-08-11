# tests/test_calendar_sync.py
"""Tests for pi/calendar_sync.py — Google/Outlook calendar integration.

Tests that calendar functions degrade gracefully when no credentials are
configured, and that the voice output format is correct when events exist.
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock


def _tmp_config_dir(tmp_path: Path) -> Path:
    """Create a temporary config dir and patch the calendar module to use it."""
    cfg_dir = tmp_path / "rex-remote"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    return cfg_dir


def test_calendar_today_no_credentials():
    """calendar_today must return 'Calendar not configured' when no config exists."""
    from pi import calendar_sync

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        cfg_dir = _tmp_config_dir(tmp_path)
        config_path = cfg_dir / "calendar_config.json"

        # Ensure config does NOT exist
        if config_path.exists():
            config_path.unlink()

        with mock.patch.object(calendar_sync, "CONFIG_PATH", config_path):
            result = calendar_sync.calendar_today()

        assert result == "Calendar not configured", f"Expected 'Calendar not configured', got {result!r}"


def test_calendar_today_empty_events():
    """calendar_today must say 'no events' when calendar returns empty list."""
    from pi import calendar_sync

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        cfg_dir = _tmp_config_dir(tmp_path)
        config_path = cfg_dir / "calendar_config.json"

        # Write a minimal config with dummy provider
        config_path.write_text(json.dumps({
            "provider": "google",
            "google": {"credentials_json": "/dummy/creds.json"}
        }))

        with mock.patch.object(calendar_sync, "CONFIG_PATH", config_path):
            with mock.patch.object(calendar_sync, "_fetch_events", return_value=[]):
                result = calendar_sync.calendar_today()

        assert "no events" in result.lower(), f"Expected 'no events' in output, got {result!r}"


def test_calendar_today_with_events():
    """calendar_today must format events into a voice-friendly string."""
    from pi import calendar_sync

    fake_events = [
        {"summary": "Standup", "start": "2026-08-11T09:00:00", "end": "2026-08-11T09:30:00"},
        {"summary": "Lunch with John", "start": "2026-08-11T13:00:00", "end": "2026-08-11T14:00:00"},
    ]

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        cfg_dir = _tmp_config_dir(tmp_path)
        config_path = cfg_dir / "calendar_config.json"

        config_path.write_text(json.dumps({
            "provider": "google",
            "google": {"credentials_json": "/dummy/creds.json"}
        }))

        with mock.patch.object(calendar_sync, "CONFIG_PATH", config_path):
            with mock.patch.object(calendar_sync, "_fetch_events", return_value=fake_events):
                result = calendar_sync.calendar_today()

        assert "3 events" in result or "2 events" in result or "events today" in result, \
            f"Expected event count in output, got {result!r}"
        assert "Standup" in result, f"Expected 'Standup' in output, got {result!r}"
        assert "Lunch with John" in result, f"Expected 'Lunch with John' in output, got {result!r}"


def test_calendar_tomorrow_no_credentials():
    """calendar_tomorrow must return 'Calendar not configured' when no config exists."""
    from pi import calendar_sync

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        cfg_dir = _tmp_config_dir(tmp_path)
        config_path = cfg_dir / "calendar_config.json"

        if config_path.exists():
            config_path.unlink()

        with mock.patch.object(calendar_sync, "CONFIG_PATH", config_path):
            result = calendar_sync.calendar_tomorrow()

        assert result == "Calendar not configured", f"Expected 'Calendar not configured', got {result!r}"


def test_calendar_add_no_credentials():
    """calendar_add must return 'Calendar not configured' when no config exists."""
    from pi import calendar_sync

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        cfg_dir = _tmp_config_dir(tmp_path)
        config_path = cfg_dir / "calendar_config.json"

        if config_path.exists():
            config_path.unlink()

        with mock.patch.object(calendar_sync, "CONFIG_PATH", config_path):
            result = calendar_sync.calendar_add("Meeting", "2026-08-12T10:00:00", "2026-08-12T11:00:00")

        assert result == "Calendar not configured", f"Expected 'Calendar not configured', got {result!r}"


def test_voice_format_with_time():
    """Events should include formatted time (e.g. '9am', '1pm') in voice output."""
    from pi import calendar_sync

    fake_events = [
        {"summary": "Standup", "start": "2026-08-11T09:00:00", "end": "2026-08-11T09:30:00"},
        {"summary": "Lunch", "start": "2026-08-11T13:00:00", "end": "2026-08-11T14:00:00"},
        {"summary": "Review", "start": "2026-08-11T15:00:00", "end": "2026-08-11T16:00:00"},
    ]

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        cfg_dir = _tmp_config_dir(tmp_path)
        config_path = cfg_dir / "calendar_config.json"

        config_path.write_text(json.dumps({
            "provider": "google",
            "google": {"credentials_json": "/dummy/creds.json"}
        }))

        with mock.patch.object(calendar_sync, "CONFIG_PATH", config_path):
            with mock.patch.object(calendar_sync, "_fetch_events", return_value=fake_events):
                result = calendar_sync.calendar_today()

        # Should mention time info (am/pm format)
        assert "am" in result.lower() or "pm" in result.lower(), \
            f"Expected time format (am/pm) in output, got {result!r}"