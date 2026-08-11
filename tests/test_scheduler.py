# tests/test_scheduler.py
"""Tests for pi/scheduler.py — systemd-timer voice-scheduled reminders.

Voice flow: "Hey Rex, remind me in 20 minutes to check the build"
  -> schedule_reminder(message="check the build", minutes=20)
  -> systemd-run --user --on-active=20m creates a transient timer
  -> JSON store at ~/.config/rex-remote/reminders.json tracks all reminders.
"""
import json
from pathlib import Path
from unittest import mock

import pytest


@pytest.fixture
def tmp_reminders(tmp_path):
    """Create a temporary reminders.json and patch REMINDERS_FILE."""
    from pi import scheduler
    original = scheduler.REMINDERS_FILE
    reminders_file = tmp_path / "reminders.json"
    scheduler.REMINDERS_FILE = reminders_file
    yield reminders_file
    scheduler.REMINDERS_FILE = original


def test_voice_schedules_timer(tmp_reminders):
    """schedule_reminder() creates a systemd timer and stores the reminder.

    Verifies:
      - systemd-run is invoked with --user --on-active=<minutes>m
      - The unit name follows rex-reminder-<task_id> convention
      - A reminder record with task_id, message, trigger_time, status is stored
      - The reminder is persisted to the JSON file
    """
    from pi.scheduler import schedule_reminder, list_reminders

    with mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = mock.MagicMock(returncode=0, stderr="")

        result = schedule_reminder("check the build", 20)

        # Verify systemd-run was called with correct arguments
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert "systemd-run" in call_args
        assert "--user" in call_args
        assert "--on-active=20m" in call_args
        assert any("rex-reminder-" in arg for arg in call_args)
        assert any("check the build" in arg for arg in call_args)

    # Verify the returned reminder record
    assert result["message"] == "check the build"
    assert result["minutes"] == 20
    assert result["status"] == "scheduled"
    assert "task_id" in result
    assert "trigger_time" in result
    assert isinstance(result["trigger_time"], float)

    # Verify it was saved to disk and list_reminders returns it
    reminders = list_reminders()
    assert len(reminders) == 1
    assert reminders[0]["message"] == "check the build"
    assert reminders[0]["status"] == "scheduled"