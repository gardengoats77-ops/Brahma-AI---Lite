# pi/memory.py
"""JSON-file conversation store for cross-session conversation memory.

Stores a list of conversation turns as {"role", "content", "timestamp"}
in a JSON file at ~/.config/rex-remote/conversation_memory.json. Keeps the
last 50 turns (configurable) so the Pi voice loop can hydrate recent
context on boot ("Welcome back, we were discussing X").
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List

log = logging.getLogger("brahma.pi.memory")

# Default path; tests patch this via CONVERSATION_MEMORY_FILE.
CONVERSATION_MEMORY_FILE: Path = (
    Path.home() / ".config" / "rex-remote" / "conversation_memory.json"
)
MAX_TURNS: int = 50


def _load() -> List[Dict[str, Any]]:
    """Load conversation memory from disk. Returns empty list on any error."""
    if not CONVERSATION_MEMORY_FILE.exists():
        return []
    try:
        with open(CONVERSATION_MEMORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return []
    except Exception as e:  # noqa: BLE001
        log.warning("failed to load conversation memory: %s", e)
        return []


def _save(turns: List[Dict[str, Any]]) -> None:
    """Save conversation memory to disk, creating parent dirs as needed."""
    try:
        CONVERSATION_MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CONVERSATION_MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(turns, f, indent=2, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        log.warning("failed to save conversation memory: %s", e)


def append_exchange(role: str, content: str) -> Dict[str, Any]:
    """Append a single conversation turn (user or assistant).

    Returns the stored record.
    """
    record = {
        "role": role,
        "content": content,
        "timestamp": time.time(),
    }
    turns = _load()
    turns.append(record)
    # Ring buffer: keep only the last MAX_TURNS entries
    if len(turns) > MAX_TURNS:
        turns = turns[-MAX_TURNS:]
    _save(turns)
    return record


def hydrate_recent(limit: int = MAX_TURNS) -> List[Dict[str, Any]]:
    """Return the last `limit` conversation turns, most recent last.

    Called on boot to hydrate Gemini Live context with recent conversation.
    """
    turns = _load()
    return turns[-limit:]


def clear_memory() -> None:
    """Clear all conversation memory."""
    _save([])


def get_turn_count() -> int:
    """Return the number of stored conversation turns."""
    return len(_load())