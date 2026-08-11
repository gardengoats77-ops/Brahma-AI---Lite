# pi/dispatch_memory.py
"""JSON-backed ring buffer of the last N dispatches to REX-OMEGA's brain.

Stores each dispatch as a dict with task_id, assigned_agent, prompt, result,
and timestamp. Keeps the last 50 dispatches (configurable) so Rex can recall
prior work across sessions:

    "Hey Rex, what did that research task find?"
    → recall_dispatch("last research") → returns the last research dispatch

Recall modes:
  - "last"              → most recent dispatch
  - "last research"     → most recent dispatch whose agent matches the keyword
  - "last from <agent>" → most recent dispatch from a specific agent
  - "<task_id>"         → exact match by task_id
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("brahma.pi.dispatch_memory")

# Default path; tests patch this via DISPATCH_HISTORY_FILE.
DISPATCH_HISTORY_FILE: Path = (
    Path.home() / ".config" / "rex-remote" / "dispatch_history.json"
)
MAX_HISTORY: int = 50


def _load() -> List[Dict[str, Any]]:
    """Load the dispatch history from disk. Returns empty list on any error."""
    if not DISPATCH_HISTORY_FILE.exists():
        return []
    try:
        with open(DISPATCH_HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return []
    except Exception as e:  # noqa: BLE001
        log.warning("failed to load dispatch history: %s", e)
        return []


def _save(history: List[Dict[str, Any]]) -> None:
    """Save the dispatch history to disk, creating parent dirs as needed."""
    try:
        DISPATCH_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(DISPATCH_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        log.warning("failed to save dispatch history: %s", e)


def record_dispatch(
    task_id: str,
    assigned_agent: str,
    prompt: str,
    result: str,
) -> Dict[str, Any]:
    """Record a completed dispatch to the ring buffer.

    Returns the stored dispatch record.
    """
    entry = {
        "task_id": task_id,
        "assigned_agent": assigned_agent,
        "prompt": prompt,
        "result": result,
        "timestamp": time.time(),
    }
    history = _load()
    history.append(entry)
    # Ring buffer: keep only the last MAX_HISTORY entries
    if len(history) > MAX_HISTORY:
        history = history[-MAX_HISTORY:]
    _save(history)
    return entry


def recall_dispatch(query: str) -> Optional[Dict[str, Any]]:
    """Recall a dispatch from history.

    Query modes:
      - "last"              → most recent dispatch
      - "last research"     → most recent dispatch whose agent/name matches keyword
      - "last from <agent>" → most recent dispatch from a specific agent
      - "<task_id>"         → exact match by task_id

    Returns None if no match or history is empty.
    """
    history = _load()
    if not history:
        return None

    query = (query or "").strip().lower()
    if not query:
        query = "last"

    # 1. Exact task_id match first
    for entry in reversed(history):
        if entry.get("task_id", "").lower() == query:
            return entry

    # 2. "last from <agent>"
    if query.startswith("last from "):
        agent = query[len("last from ") :].strip()
        for entry in reversed(history):
            if entry.get("assigned_agent", "").lower() == agent:
                return entry
        return None

    # 3. "last <keyword>" (e.g., "last research")
    if query.startswith("last "):
        keyword = query[len("last ") :].strip()
        for entry in reversed(history):
            agent = entry.get("assigned_agent", "").lower()
            prompt = entry.get("prompt", "").lower()
            if keyword in agent or keyword in prompt:
                return entry
        return None

    # 4. Plain "last" → most recent
    if query == "last":
        return history[-1]

    return None


def get_history(limit: int = MAX_HISTORY) -> List[Dict[str, Any]]:
    """Return the last `limit` dispatches, most recent last."""
    history = _load()
    return history[-limit:]


def clear_history() -> None:
    """Clear all dispatch history."""
    _save([])