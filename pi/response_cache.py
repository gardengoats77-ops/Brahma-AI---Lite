# pi/response_cache.py
"""SQLite-backed LRU response cache for Gemini prompts.

Caches the last N responses keyed by SHA256(prompt). Serves from cache
when the network is unreachable (offline operation).

Schema:
    CREATE TABLE response_cache (
        key TEXT PRIMARY KEY,          -- SHA256 hex digest of prompt
        prompt TEXT NOT NULL,          -- original prompt (for debugging)
        response_json TEXT NOT NULL,   -- JSON-serialized response dict
        created_at REAL NOT NULL,      -- time.time() when first stored
        accessed_at REAL NOT NULL      -- time.time() of last access (LRU)
    );

LRU eviction: after put(), delete rows where rowid NOT IN
(SELECT rowid FROM response_cache ORDER BY accessed_at DESC LIMIT N).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any, Dict, Optional

log = logging.getLogger("brahma.response_cache")

DEFAULT_DB_PATH = "~/.config/rex-remote/response_cache.db"
DEFAULT_MAX_SIZE = 500
DEFAULT_TTL_SECONDS = 24 * 3600  # 24 hours


def _make_key(prompt: str) -> str:
    """Derive the cache key from the prompt: SHA256 hex digest."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


class ResponseCache:
    """SQLite-backed LRU response cache.

    Parameters
    ----------
    db_path : str
        Filesystem path to the SQLite database. Created on first use.
    max_size : int
        Maximum number of entries to retain. Oldest by last-access time
        are evicted once the limit is exceeded.
    ttl_seconds : float
        Entries older than this (by ``accessed_at``) are treated as
        cache misses. 0 disables TTL.
    """

    def __init__(
        self,
        db_path: str = DEFAULT_DB_PATH,
        max_size: int = DEFAULT_MAX_SIZE,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._db_path = os.path.expanduser(db_path)
        self._max_size = max_size
        self._ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._ensure_table()

    # ── public API ────────────────────────────────────────────────────────

    def get(self, prompt: str) -> Optional[Dict[str, Any]]:
        """Return the cached response for *prompt*, or ``None``.

        On a hit, the ``accessed_at`` column is bumped (LRU) and the
        row is returned. On a miss (key not found, or TTL expired),
        ``None`` is returned.
        """
        key = _make_key(prompt)
        with self._lock:
            cur = self._conn.execute(
                "SELECT response_json FROM response_cache WHERE key = ?",
                (key,),
            )
            row = cur.fetchone()
            if row is None:
                return None

            # TTL check
            if self._ttl_seconds > 0:
                age = time.time() - self._conn.execute(
                    "SELECT accessed_at FROM response_cache WHERE key = ?",
                    (key,),
                ).fetchone()[0]
                if age > self._ttl_seconds:
                    self._conn.execute(
                        "DELETE FROM response_cache WHERE key = ?", (key,)
                    )
                    self._conn.commit()
                    return None

            # Bump LRU timestamp
            self._conn.execute(
                "UPDATE response_cache SET accessed_at = ? WHERE key = ?",
                (time.time(), key),
            )
            self._conn.commit()

        try:
            return json.loads(row[0])
        except (json.JSONDecodeError, ValueError) as exc:
            log.warning("cached response for %s… is corrupt: %s", key[:16], exc)
            return None

    def put(self, prompt: str, response: Dict[str, Any]) -> None:
        """Store *response* under *prompt*.

        LRU eviction runs after insert so the table never exceeds
        ``_max_size`` rows.
        """
        key = _make_key(prompt)
        now = time.time()
        try:
            response_json = json.dumps(response, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            log.warning("cannot cache response for %s…: %s", key[:16], exc)
            return

        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO response_cache
                   (key, prompt, response_json, created_at, accessed_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (key, prompt[:500], response_json, now, now),
            )
            self._evict_lru()
            self._conn.commit()

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        if self._conn:
            self._conn.close()
            self._conn = None  # type: ignore[assignment]

    # ── internals ─────────────────────────────────────────────────────────

    def _ensure_table(self) -> None:
        """Create the response_cache table if it doesn't exist."""
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS response_cache (
                key TEXT PRIMARY KEY,
                prompt TEXT NOT NULL,
                response_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                accessed_at REAL NOT NULL
            )"""
        )
        self._conn.commit()

    def _evict_lru(self) -> None:
        """Remove rows beyond ``max_size`` ordered by ``accessed_at`` desc."""
        if self._max_size <= 0:
            return
        self._conn.execute(
            """DELETE FROM response_cache WHERE key NOT IN (
                SELECT key FROM response_cache
                ORDER BY accessed_at DESC
                LIMIT ?
            )""",
            (self._max_size,),
        )

    def __enter__(self) -> "ResponseCache":
        return self

    def __exit__(self, *exc) -> None:
        self.close()