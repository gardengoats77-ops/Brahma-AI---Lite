# tests/test_proactive_reminders.py
"""Tests for proactive reminder notifications.

The Pi speaks "meeting in 10 min" without being asked by checking
upcoming reminders within a configurable lead time.
"""

import time
import json
from pathlib import Path

import pytest


def test_check_upcoming_returns_empty_when_no_reminders(tmp_path, monkeypatch):
    """No reminders → empty list."""
    from pi import scheduler
    monkeypatch.setattr(scheduler, "REMINDERS_FILE", tmp_path / "reminders.json")
    monkeypatch.setattr("pi.calendar_sync.calendar_today", lambda: "No events today.")

    result = scheduler.check_upcoming(lead_minutes=10)
    assert result == []


def test_check_upcoming_finds_reminder_within_lead_time(tmp_path, monkeypatch):
    """A reminder scheduled in 8 minutes should show up with 10-min lead."""
    from pi import scheduler
    reminders_file = tmp_path / "reminders.json"
    now = time.time()
    reminders = [
        {
            "task_id": "rem-test1",
            "message": "Check the build",
            "trigger_time": now + 8 * 60,  # 8 minutes from now
            "status": "scheduled",
        },
        {
            "task_id": "rem-test2",
            "message": "Far future event",
            "trigger_time": now + 120 * 60,  # 2 hours from now
            "status": "scheduled",
        },
    ]
    reminders_file.write_text(json.dumps(reminders))
    monkeypatch.setattr(scheduler, "REMINDERS_FILE", reminders_file)
    monkeypatch.setattr("pi.calendar_sync.calendar_today", lambda: "No events today.")

    result = scheduler.check_upcoming(lead_minutes=10)
    messages = [r["message"] for r in result]
    assert "Check the build" in messages
    assert "Far future event" not in messages


def test_check_upcoming_skips_fired_reminders(tmp_path, monkeypatch):
    """Already-fired reminders should not show up."""
    from pi import scheduler
    reminders_file = tmp_path / "reminders.json"
    now = time.time()
    reminders = [
        {
            "task_id": "rem-fired",
            "message": "Already done",
            "trigger_time": now + 5 * 60,
            "status": "fired",
        },
    ]
    reminders_file.write_text(json.dumps(reminders))
    monkeypatch.setattr(scheduler, "REMINDERS_FILE", reminders_file)
    monkeypatch.setattr("pi.calendar_sync.calendar_today", lambda: "No events today.")

    result = scheduler.check_upcoming(lead_minutes=10)
    assert result == []


def test_check_upcoming_respects_lead_time_boundary(tmp_path, monkeypatch):
    """A reminder exactly at lead_minutes boundary should be included."""
    from pi import scheduler
    reminders_file = tmp_path / "reminders.json"
    now = time.time()
    reminders = [
        {
            "task_id": "rem-boundary",
            "message": "Boundary test",
            "trigger_time": now + 10 * 60,  # exactly 10 minutes
            "status": "scheduled",
        },
    ]
    reminders_file.write_text(json.dumps(reminders))
    monkeypatch.setattr(scheduler, "REMINDERS_FILE", reminders_file)
    monkeypatch.setattr("pi.calendar_sync.calendar_today", lambda: "No events today.")

    result = scheduler.check_upcoming(lead_minutes=10)
    messages = [r["message"] for r in result]
    assert "Boundary test" in messages


def test_check_upcoming_skips_past_reminders(tmp_path, monkeypatch):
    """Reminders whose trigger_time is in the past should not show up."""
    from pi import scheduler
    reminders_file = tmp_path / "reminders.json"
    now = time.time()
    reminders = [
        {
            "task_id": "rem-past",
            "message": "Should have fired",
            "trigger_time": now - 60,  # 1 minute ago
            "status": "scheduled",
        },
    ]
    reminders_file.write_text(json.dumps(reminders))
    monkeypatch.setattr(scheduler, "REMINDERS_FILE", reminders_file)
    monkeypatch.setattr("pi.calendar_sync.calendar_today", lambda: "No events today.")

    result = scheduler.check_upcoming(lead_minutes=10)
    assert result == []


def test_format_upcoming_for_tts():
    """Format upcoming items for voice output."""
    from pi import scheduler
    now = time.time()
    items = [
        {"message": "Check the build", "trigger_time": now + 8 * 60, "source": "reminder"},
        {"message": "Team standup", "trigger_time": now + 7 * 60, "source": "reminder"},
    ]
    text = scheduler.format_upcoming(items)
    assert "Check the build" in text
    assert "standup" in text.lower()
    assert "2 things" in text


def test_format_upcoming_empty():
    """Empty list should return 'Nothing coming up.'"""
    from pi import scheduler
    text = scheduler.format_upcoming([])
    assert text == "Nothing coming up."


def test_format_upcoming_single_item():
    """Single item should use singular phrasing."""
    from pi import scheduler
    now = time.time()
    items = [{"message": "Call mom", "trigger_time": now + 5 * 60}]
    text = scheduler.format_upcoming(items)
    assert "1 thing" in text
    assert "Call mom" in text
