# tests/test_briefing.py
"""Tests for pi/briefing.py — morning briefing aggregation (Phase 13.2).

Aggregates: calendar_today + weather + news + scheduler list_reminders.
Tests that the briefing degrades gracefully when API keys are missing.
"""
import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest


@pytest.fixture
def tmp_config_dir(tmp_path):
    """Create a temporary config dir and patch relevant modules."""
    from pi import calendar_sync
    cfg_dir = tmp_path / "rex-remote"
    cfg_dir.mkdir(parents=True, exist_ok=True)

    # Patch calendar config
    cal_config_path = cfg_dir / "calendar_config.json"
    original_cal_config = calendar_sync.CONFIG_PATH

    # Patch scheduler reminders file
    from pi import scheduler
    original_reminders = scheduler.REMINDERS_FILE
    reminders_file = cfg_dir / "reminders.json"
    scheduler.REMINDERS_FILE = reminders_file

    yield cfg_dir, cal_config_path

    calendar_sync.CONFIG_PATH = original_cal_config
    scheduler.REMINDERS_FILE = original_reminders


def test_morning_briefing(tmp_config_dir):
    """morning_briefing() aggregates calendar + weather + news + reminders
    into a single voice-friendly briefing string."""
    cfg_dir, cal_config_path = tmp_config_dir

    # Setup: calendar config with fake events
    from pi import calendar_sync
    cal_config_path.write_text(json.dumps({
        "provider": "google",
        "google": {"credentials_json": "/dummy/creds.json"}
    }))
    calendar_sync.CONFIG_PATH = cal_config_path

    fake_events = [
        {"summary": "Standup", "start": "2026-08-11T09:00:00", "end": "2026-08-11T09:30:00"},
        {"summary": "Team Meeting", "start": "2026-08-11T14:00:00", "end": "2026-08-11T15:00:00"},
    ]

    # Setup: reminders
    from pi import scheduler
    reminder_data = [
        {"task_id": "rem-abc123", "message": "check the build", "minutes": 30,
         "trigger_time": 9999999999.0, "status": "scheduled",
         "unit_name": "rex-reminder-rem-abc123", "created_at": 9999999999.0}
    ]
    scheduler.REMINDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(scheduler.REMINDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(reminder_data, f)

    from pi import briefing

    with mock.patch.object(calendar_sync, "_fetch_events", return_value=fake_events):
        with mock.patch.object(briefing, "_fetch_weather", return_value="72°F and sunny"):
            with mock.patch.object(briefing, "_fetch_news", return_value=["AI breakthrough", "Space launch"]):
                result = briefing.morning_briefing()

    # Verify result is a string
    assert isinstance(result, str)

    # Verify it contains "Good morning"
    assert "Good morning" in result or "morning" in result.lower()

    # Verify weather is included
    assert "72" in result or "sunny" in result.lower()

    # Verify calendar events are mentioned
    assert "Standup" in result or "event" in result.lower()

    # Verify news is included
    assert "AI breakthrough" in result or "news" in result.lower()

    # Verify reminders are included
    assert "check the build" in result or "reminder" in result.lower()


def test_morning_briefing_no_weather_key(tmp_config_dir):
    """When WEATHER_API_KEY is not set, weather should be skipped gracefully."""
    cfg_dir, cal_config_path = tmp_config_dir

    # Setup: calendar config
    from pi import calendar_sync
    cal_config_path.write_text(json.dumps({
        "provider": "google",
        "google": {"credentials_json": "/dummy/creds.json"}
    }))
    calendar_sync.CONFIG_PATH = cal_config_path

    # Setup: no reminders
    from pi import scheduler
    scheduler.REMINDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(scheduler.REMINDERS_FILE, "w", encoding="utf-8") as f:
        json.dump([], f)

    from pi import briefing

    with mock.patch.object(calendar_sync, "_fetch_events", return_value=[]):
        with mock.patch.object(briefing, "_fetch_weather", return_value=None):
            with mock.patch.object(briefing, "_fetch_news", return_value=[]):
                with mock.patch.dict(os.environ, {}, clear=False):
                    # Ensure WEATHER_API_KEY is not set
                    os.environ.pop("WEATHER_API_KEY", None)
                    os.environ.pop("NEWS_API_KEY", None)
                    result = briefing.morning_briefing()

    assert isinstance(result, str)
    assert "Good morning" in result or "morning" in result.lower()
    # Should say weather not configured or skip gracefully
    assert "weather" in result.lower() or "no events" in result.lower() or "free day" in result.lower()


def test_morning_briefing_no_calendar(tmp_config_dir):
    """When calendar is not configured, should say so."""
    cfg_dir, cal_config_path = tmp_config_dir

    # Ensure calendar config does NOT exist
    from pi import calendar_sync
    if cal_config_path.exists():
        cal_config_path.unlink()
    calendar_sync.CONFIG_PATH = cal_config_path

    # Setup: no reminders
    from pi import scheduler
    scheduler.REMINDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(scheduler.REMINDERS_FILE, "w", encoding="utf-8") as f:
        json.dump([], f)

    from pi import briefing

    with mock.patch.object(briefing, "_fetch_weather", return_value="65°F and cloudy"):
        with mock.patch.object(briefing, "_fetch_news", return_value=[]):
            result = briefing.morning_briefing()

    assert isinstance(result, str)
    assert "Calendar not configured" in result or "calendar" in result.lower()


def test_fetch_weather_no_key():
    """_fetch_weather returns None when WEATHER_API_KEY is not set."""
    from pi import briefing

    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("WEATHER_API_KEY", None)
        result = briefing._fetch_weather()

    assert result is None or result == "Weather not configured"


def test_fetch_news_no_key():
    """_fetch_news returns empty list when NEWS_API_KEY is not set."""
    from pi import briefing

    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("NEWS_API_KEY", None)
        result = briefing._fetch_news()

    assert result == []


def test_morning_briefing_voice_format(tmp_config_dir):
    """Briefing should be a single voice-friendly string, not a dict or list."""
    cfg_dir, cal_config_path = tmp_config_dir

    from pi import calendar_sync
    cal_config_path.write_text(json.dumps({
        "provider": "google",
        "google": {"credentials_json": "/dummy/creds.json"}
    }))
    calendar_sync.CONFIG_PATH = cal_config_path

    from pi import scheduler
    scheduler.REMINDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(scheduler.REMINDERS_FILE, "w", encoding="utf-8") as f:
        json.dump([], f)

    from pi import briefing

    with mock.patch.object(calendar_sync, "_fetch_events", return_value=[]):
        with mock.patch.object(briefing, "_fetch_weather", return_value="72°F and sunny"):
            with mock.patch.object(briefing, "_fetch_news", return_value=["Tech news"]):
                result = briefing.morning_briefing()

    # Must be a string suitable for TTS
    assert isinstance(result, str)
    assert len(result) > 20  # Not empty/tiny
    # Should mention temperature
    assert "72" in result or "sunny" in result.lower()