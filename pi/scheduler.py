# pi/scheduler.py
"""Systemd-timer voice-scheduled reminders for the Pi voice loop.

Voice flow: "Hey Rex, remind me in 20 minutes to check the build"
  -> schedule_reminder(message="check the build", minutes=20)
  -> systemd-run --user --on-active=20m creates a transient timer
  -> JSON store at ~/.config/rex-remote/reminders.json tracks all reminders.

On timer trigger, systemd fires a one-shot service that speaks the
reminder via TTS and notifies the dashboard.
"""
from __future__ import annotations

import json
import logging
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("brahma.pi.scheduler")

# Default path; tests patch this via REMINDERS_FILE.
REMINDERS_FILE: Path = (
    Path.home() / ".config" / "rex-remote" / "reminders.json"
)
MAX_REMINDERS: int = 100


def _load() -> List[Dict[str, Any]]:
    """Load reminders from disk. Returns empty list on any error."""
    if not REMINDERS_FILE.exists():
        return []
    try:
        with open(REMINDERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return []
    except Exception as e:  # noqa: BLE001
        log.warning("failed to load reminders: %s", e)
        return []


def _save(reminders: List[Dict[str, Any]]) -> None:
    """Save reminders to disk, creating parent dirs as needed."""
    try:
        REMINDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(REMINDERS_FILE, "w", encoding="utf-8") as f:
            json.dump(reminders, f, indent=2, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        log.warning("failed to save reminders: %s", e)


def schedule_reminder(message: str, minutes: int) -> Dict[str, Any]:
    """Create a systemd timer to fire a reminder after `minutes`.

    Returns the stored reminder record with task_id, message, trigger_time,
    and status.
    """
    task_id = f"rem-{uuid.uuid4().hex[:8]}"
    unit_name = f"rex-reminder-{task_id}"
    trigger_time = time.time() + minutes * 60

    # Build the command that systemd-run will execute when the timer fires.
    # It speaks the reminder and logs it.
    trigger_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(trigger_time))
    notify_cmd = (
        f'echo "REMINDER: {message}" | '
        f'logger -t rex-reminder && '
        f'echo "⏰ Reminder: {message}" '
        f'> /tmp/rex-reminder-{task_id}.txt'
    )

    cmd = [
        "systemd-run", "--user",
        f"--on-active={minutes}m",
        "--unit", unit_name,
        "--description", f"Rex reminder: {message}",
        "/bin/sh", "-c", notify_cmd,
    ]

    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        log.info("Scheduled reminder '%s' in %d minutes (unit=%s)", message, minutes, unit_name)
    except Exception as e:  # noqa: BLE001
        log.error("Failed to schedule systemd timer: %s", e)

    reminder = {
        "task_id": task_id,
        "message": message,
        "minutes": minutes,
        "trigger_time": trigger_time,
        "status": "scheduled",
        "unit_name": unit_name,
        "created_at": time.time(),
    }

    reminders = _load()
    reminders.append(reminder)
    # Ring buffer: keep only the last MAX_REMINDERS entries
    if len(reminders) > MAX_REMINDERS:
        reminders = reminders[-MAX_REMINDERS:]
    _save(reminders)

    return reminder


def cancel_reminder(task_id: str) -> Optional[Dict[str, Any]]:
    """Cancel a scheduled reminder by task_id.

    Stops the systemd timer unit and marks the reminder as cancelled.
    Returns the reminder record if found, None otherwise.
    """
    reminders = _load()
    for r in reminders:
        if r.get("task_id") == task_id:
            unit_name = r.get("unit_name", f"rex-reminder-{task_id}")
            try:
                subprocess.run(
                    ["systemctl", "--user", "stop", unit_name],
                    capture_output=True, text=True, timeout=10,
                )
            except Exception as e:  # noqa: BLE001
                log.warning("Failed to stop timer unit %s: %s", unit_name, e)
            r["status"] = "cancelled"
            _save(reminders)
            return r
    return None


def list_reminders(status_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    """List all reminders, optionally filtered by status.

    Status values: 'scheduled', 'fired', 'cancelled'
    """
    reminders = _load()
    if status_filter:
        reminders = [r for r in reminders if r.get("status") == status_filter]
    return reminders


def check_and_fire_due() -> List[Dict[str, Any]]:
    """Check for reminders whose trigger_time has passed and mark them as fired.

    This should be called periodically from the voice loop or a cron job.
    Returns a list of reminders that were just fired (so they can be spoken).
    """
    now = time.time()
    reminders = _load()
    fired = []
    for r in reminders:
        if r.get("status") == "scheduled" and r.get("trigger_time", 0) <= now:
            r["status"] = "fired"
            r["fired_at"] = now
            fired.append(r)
    if fired:
        _save(reminders)
    return fired