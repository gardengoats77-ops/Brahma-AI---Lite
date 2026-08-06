"""Shared SQLite-backed conversation store + session token machinery.

Imported by both the root REX backend (``server.py``) and the dashboard
(``dashboard/server.py``) so the two processes share the same database via
WAL mode without duplicating the persistence layer.

Usage::

    from core.repository import ConversationRepository, encode_session, decode_session

    store = ConversationRepository(Path(".jarvis-data/history.db"))
    token = encode_session("device-1", "alice")
    session = decode_session("Bearer " + token, "device-1")
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator

IDENTITY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ConversationMessage(BaseModel):
    """A message shared across every device linked to a user."""

    id: str = Field(default_factory=lambda: f"msg-{uuid.uuid4().hex}")
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=16_000)
    timestamp: float = Field(default_factory=time.time)
    device_id: str = Field(min_length=1, max_length=128)
    source: Literal["browser", "agent", "system"] = "browser"

    @field_validator("content")
    @classmethod
    def strip_content(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("content must not be empty")
        return cleaned


class MessageBatch(BaseModel):
    messages: list[ConversationMessage] = Field(min_length=1, max_length=50)


class DeviceClaim(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)

    @field_validator("user_id")
    @classmethod
    def normalize_user_id(cls, value: str) -> str:
        return value.strip()


class ConnectionLease(BaseModel):
    device_id: str = Field(min_length=1, max_length=128)
    connection_id: str = Field(min_length=1, max_length=128)
    identity: str = Field(min_length=1, max_length=128)


# ---------------------------------------------------------------------------
# Session tokens (HMAC-SHA256)
# ---------------------------------------------------------------------------

_SESSION_SECRET_CACHE: str | None = None


def get_session_secret() -> str:
    global _SESSION_SECRET_CACHE
    if _SESSION_SECRET_CACHE is not None:
        return _SESSION_SECRET_CACHE
    from_env = os.getenv("REX_SESSION_SECRET") or os.getenv("JARVIS_SESSION_SECRET")
    if from_env:
        _SESSION_SECRET_CACHE = from_env
    else:
        _SESSION_SECRET_CACHE = secrets.token_urlsafe(32)
    return _SESSION_SECRET_CACHE


def encode_session(device_id: str, user_id: str) -> str:
    payload = json.dumps(
        {"device_id": device_id, "user_id": user_id}, separators=(",", ":")
    ).encode()
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
    secret = get_session_secret()
    signature = hmac.new(secret.encode(), encoded, hashlib.sha256).digest()
    return f"{encoded.decode()}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"


def decode_session(
    authorization: str | None, device_id: str, user_id: str | None = None
) -> dict[str, str]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401, detail="A REX session token is required"
        )
    try:
        encoded_text, signature_text = authorization[7:].split(".", 1)
        encoded = encoded_text.encode()
        secret = get_session_secret()
        expected = hmac.new(secret.encode(), encoded, hashlib.sha256).digest()
        actual = base64.urlsafe_b64decode(
            signature_text + "=" * (-len(signature_text) % 4)
        )
        if not hmac.compare_digest(actual, expected):
            raise ValueError("invalid signature")
        payload = json.loads(
            base64.urlsafe_b64decode(encoded + b"=" * (-len(encoded) % 4))
        )
        if payload.get("device_id") != device_id or (
            user_id and payload.get("user_id") != user_id
        ):
            raise ValueError("session scope mismatch")
        return {
            "device_id": str(payload["device_id"]),
            "user_id": str(payload["user_id"]),
        }
    except (ValueError, KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(
            status_code=401, detail="Invalid REX session token"
        ) from None


# ---------------------------------------------------------------------------
# SQLite-backed conversation store
# ---------------------------------------------------------------------------

class ConversationRepository:
    """SQLite-backed conversation store with a stable interface.

    Mirrors the shared-context SQLite conventions (WAL, busy_timeout,
    thread-local connections, indexed timestamp reads, per-user revision) so
    the FastAPI server and the agent worker — separate processes, each with
    their own repository instance — can write the same database without
    losing each other's updates (the legacy JSON read-modify-write race is
    gone). A legacy ``history.json`` is imported exactly once when the DB is
    empty, preserving existing conversations across the migration.
    """

    def __init__(self, path: Path) -> None:
        path = Path(path)
        if path.suffix == ".json":
            self.legacy_json_path = path
            self.db_path = path.with_suffix(".db")
        else:
            self.db_path = path
            self.legacy_json_path = path.with_suffix(".json")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._lock = threading.RLock()
        self._init_schema()
        self._migrate_legacy_json()

    @property
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                str(self.db_path), timeout=30, check_same_thread=False
            )
            conn.row_factory = sqlite3.Row
            conn.isolation_level = None
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    def _init_schema(self) -> None:
        conn = self._conn
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                revision INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp REAL NOT NULL,
                device_id TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'browser',
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );
            CREATE INDEX IF NOT EXISTS idx_messages_user_time
                ON messages(user_id, timestamp, id);
            CREATE TABLE IF NOT EXISTS devices (
                device_id TEXT PRIMARY KEY,
                user_id TEXT,
                active_connection TEXT
            );
            """
        )
        has_identities = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='identities'"
        ).fetchone()
        if has_identities:
            conn.execute(
                "INSERT OR IGNORE INTO devices (device_id, user_id) "
                "SELECT identity, COALESCE(NULLIF(user_id, ''), "
                "(SELECT user_id FROM devices WHERE device_id = identities.device_id)) "
                "FROM identities"
            )
            conn.execute("DROP TABLE identities")

    def _migrate_legacy_json(self) -> None:
        legacy = self.legacy_json_path
        if not legacy.exists():
            return
        conn = self._conn
        row = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
        if row and row["c"]:
            return
        try:
            data = json.loads(legacy.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return
        if not isinstance(data, dict):
            return
        with self._lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for user_id, record in (data.get("users") or {}).items():
                    try:
                        revision = int(record.get("revision", 0) or 0)
                    except (TypeError, ValueError):
                        revision = 0
                    conn.execute(
                        "INSERT OR IGNORE INTO users (user_id, revision) VALUES (?, ?)",
                        (str(user_id), revision),
                    )
                    for message in record.get("messages") or []:
                        try:
                            timestamp = float(message.get("timestamp", 0) or 0)
                        except (TypeError, ValueError):
                            timestamp = 0.0
                        conn.execute(
                            "INSERT OR IGNORE INTO messages "
                            "(id, user_id, role, content, timestamp, device_id, source) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (
                                str(message.get("id", "")),
                                str(user_id),
                                message.get("role", "user"),
                                message.get("content", ""),
                                timestamp,
                                str(message.get("device_id", "") or user_id),
                                message.get("source", "browser") or "browser",
                            ),
                        )
                for device_id, record in (data.get("devices") or {}).items():
                    active = record.get("active_connection")
                    conn.execute(
                        "INSERT OR IGNORE INTO devices (device_id, user_id, active_connection) "
                        "VALUES (?, ?, ?)",
                        (
                            str(device_id),
                            str(record.get("user_id")) if record.get("user_id") else None,
                            json.dumps(active) if active else None,
                        ),
                    )
                for identity, record in (data.get("identities") or {}).items():
                    user_id = record.get("user_id")
                    conn.execute(
                        "INSERT OR IGNORE INTO devices (device_id, user_id) VALUES (?, ?)",
                        (str(identity), str(user_id) if user_id else None),
                    )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    @staticmethod
    def _check_identifier(value: str, label: str) -> str:
        normalized = value.strip()
        if not IDENTITY_RE.fullmatch(normalized):
            raise HTTPException(status_code=400, detail=f"Invalid {label}")
        return normalized

    def resolve_user(
        self, identity: str, device_id: str | None, user_id: str | None
    ) -> str:
        identity = self._check_identifier(identity, "identity")
        resolved_device = self._check_identifier(
            device_id or identity, "device_id"
        )
        conn = self._conn
        row = conn.execute(
            "SELECT user_id FROM devices WHERE device_id = ?", (resolved_device,)
        ).fetchone()
        mapped_user = row["user_id"] if row else None
        resolved_user = (
            user_id or mapped_user or f"guest-{resolved_device}"
        ).strip()
        if not IDENTITY_RE.fullmatch(resolved_user):
            raise HTTPException(status_code=400, detail="Invalid user_id")
        with self._lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT INTO users (user_id, revision) VALUES (?, 0) ON CONFLICT(user_id) DO NOTHING",
                    (resolved_user,),
                )
                conn.execute(
                    "INSERT INTO devices (device_id, user_id) VALUES (?, ?) "
                    "ON CONFLICT(device_id) DO UPDATE SET user_id = excluded.user_id",
                    (resolved_device, resolved_user),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return resolved_user

    def claim_device(self, device_id: str, user_id: str) -> None:
        device_id = self._check_identifier(device_id, "device_id")
        user_id = self._check_identifier(user_id, "user_id")
        conn = self._conn
        with self._lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT INTO devices (device_id, user_id) VALUES (?, ?) "
                    "ON CONFLICT(device_id) DO UPDATE SET user_id = excluded.user_id",
                    (device_id, user_id),
                )
                conn.execute(
                    "INSERT INTO users (user_id, revision) VALUES (?, 0) ON CONFLICT(user_id) DO NOTHING",
                    (user_id,),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def user_history(self, user_id: str) -> dict[str, Any]:
        conn = self._conn
        row = conn.execute(
            "SELECT revision FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        revision = int(row["revision"]) if row else 0
        rows = conn.execute(
            "SELECT id, role, content, timestamp, device_id, source FROM messages "
            "WHERE user_id = ? ORDER BY timestamp, id",
            (user_id,),
        ).fetchall()
        return {
            "user_id": user_id,
            "revision": revision,
            "messages": [
                {
                    "id": message["id"],
                    "role": message["role"],
                    "content": message["content"],
                    "timestamp": message["timestamp"],
                    "device_id": message["device_id"],
                    "source": message["source"],
                }
                for message in rows
            ],
        }

    def history(
        self, identity: str, device_id: str | None, user_id: str | None
    ) -> dict[str, Any]:
        resolved_user = self.resolve_user(identity, device_id, user_id)
        return self.user_history(resolved_user)

    def add_messages(
        self,
        identity: str,
        device_id: str | None,
        user_id: str | None,
        messages: list[ConversationMessage],
    ) -> dict[str, Any]:
        resolved_user = self.resolve_user(identity, device_id, user_id)
        conn = self._conn
        with self._lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                known = {
                    row["id"]
                    for row in conn.execute(
                        "SELECT id FROM messages WHERE user_id = ?", (resolved_user,)
                    ).fetchall()
                }
                added = 0
                for message in messages:
                    if message.id in known:
                        continue
                    conn.execute(
                        "INSERT INTO messages (id, user_id, role, content, timestamp, device_id, source) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            message.id,
                            resolved_user,
                            message.role,
                            message.content,
                            message.timestamp,
                            message.device_id,
                            message.source,
                        ),
                    )
                    known.add(message.id)
                    added += 1
                if added:
                    conn.execute(
                        "UPDATE users SET revision = revision + 1 WHERE user_id = ?",
                        (resolved_user,),
                    )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        row = conn.execute(
            "SELECT revision FROM users WHERE user_id = ?", (resolved_user,)
        ).fetchone()
        revision = int(row["revision"]) if row else 0
        rows = conn.execute(
            "SELECT id, role, content, timestamp, device_id, source FROM messages "
            "WHERE user_id = ? ORDER BY timestamp, id",
            (resolved_user,),
        ).fetchall()
        return {
            "user_id": resolved_user,
            "revision": revision,
            "added": added,
            "messages": [
                {
                    "id": message["id"],
                    "role": message["role"],
                    "content": message["content"],
                    "timestamp": message["timestamp"],
                    "device_id": message["device_id"],
                    "source": message["source"],
                }
                for message in rows
            ],
        }

    def activate_connection(self, lease: ConnectionLease) -> dict[str, Any]:
        conn = self._conn
        with self._lock:
            row = conn.execute(
                "SELECT active_connection FROM devices WHERE device_id = ?",
                (lease.device_id,),
            ).fetchone()
            previous = (
                json.loads(row["active_connection"])
                if row and row["active_connection"]
                else None
            )
            active = lease.model_dump() | {"activated_at": time.time()}
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT INTO devices (device_id, user_id, active_connection) VALUES (?, NULL, ?) "
                    "ON CONFLICT(device_id) DO UPDATE SET active_connection = excluded.active_connection",
                    (lease.device_id, json.dumps(active)),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            return {"previous": previous, "active": active}

    def get_active_connection(self, device_id: str) -> dict[str, Any] | None:
        conn = self._conn
        row = conn.execute(
            "SELECT active_connection FROM devices WHERE device_id = ?",
            (device_id,),
        ).fetchone()
        if not row or not row["active_connection"]:
            return None
        try:
            active = json.loads(row["active_connection"])
        except (TypeError, json.JSONDecodeError):
            return None
        return active if isinstance(active, dict) else None

    def release_connection(self, device_id: str, connection_id: str) -> bool:
        conn = self._conn
        with self._lock:
            row = conn.execute(
                "SELECT active_connection FROM devices WHERE device_id = ?",
                (device_id,),
            ).fetchone()
            if not row or not row["active_connection"]:
                return False
            try:
                active = json.loads(row["active_connection"])
            except (TypeError, json.JSONDecodeError):
                return False
            if active.get("connection_id") != connection_id:
                return False
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "UPDATE devices SET active_connection = NULL WHERE device_id = ?",
                    (device_id,),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            return True


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------

def get_store_path() -> Path:
    """Return the configured SQLite store path, relative to the project root."""
    project_root = Path(__file__).resolve().parent.parent
    raw = os.getenv("REX_STORE_PATH") or os.getenv("JARVIS_STORE_PATH") or ".jarvis-data/history.db"
    path = Path(raw)
    if not path.is_absolute():
        path = project_root / path
    return path


# Module-level singleton — constructed once per process, shared across all
# endpoints. WAL + busy_timeout keeps cross-process writes safe.
_store: ConversationRepository | None = None


def get_repository() -> ConversationRepository:
    global _store
    if _store is None:
        _store = ConversationRepository(get_store_path())
    return _store
