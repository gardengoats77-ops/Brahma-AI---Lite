# tests/test_whisplay_watchdog.py
"""Tests for the WhisplayDashboard framebuffer watchdog.

Covers the auto-recovery logic: if a probe client steals focus and blanks
the panel, the watchdog detects the stale render timestamp and reacquires
focus on the next poll cycle.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from pi.whisplay_dashboard import (
    WhisplayDashboard,
    _DaemonClient,
    _STALE_THRESHOLD,
)


def _set_defaults(monkeypatch):
    monkeypatch.setattr(
        "pi.whisplay_dashboard._SOCKET_PATH", "/nonexistent/x.sock"
    )
    monkeypatch.setattr(
        "pi.whisplay_dashboard._BOARD_AVAILABLE", False
    )


def test_watchdog_reacquires_focus_when_stale(tmp_path, monkeypatch):
    _set_defaults(monkeypatch)
    dash = WhisplayDashboard(on_push_to_talk=lambda: None)
    dash._mode = "daemon"
    dash._client = MagicMock()
    # Pretend the last render was long ago
    dash._last_render_time = time.monotonic() - _STALE_THRESHOLD - 10
    # Mock _DaemonClient so fresh instance's connect() succeeds
    with patch("pi.whisplay_dashboard._DaemonClient") as mock_cls:
        fresh = MagicMock()
        fresh.connect.return_value = True
        mock_cls.return_value = fresh
        dash._watchdog_check()
        # Client should have been torn down and reconnected
        mock_cls.assert_called_once()
        fresh.connect.assert_called_once()
    assert dash._client is not None
    assert dash._mode == "daemon"


def test_watchdog_does_not_fire_when_recent(tmp_path, monkeypatch):
    _set_defaults(monkeypatch)
    dash = WhisplayDashboard(on_push_to_talk=lambda: None)
    dash._mode = "daemon"
    original_client = MagicMock()
    dash._client = original_client
    dash._last_render_time = time.monotonic()
    dash._watchdog_check()
    # Client should NOT have been replaced
    assert dash._client is original_client


def test_stale_threshold_is_reasonable():
    """The threshold should be long enough to avoid false positives during
    normal poll cycles, but short enough to recover quickly."""
    assert 10.0 < _STALE_THRESHOLD < 120.0


def test_disconnect_calls_teardown():
    """_DaemonClient.disconnect() should release framebuffer resources."""
    client = _DaemonClient(socket_path="/nonexistent/x.sock")
    client._mmap = MagicMock()
    client._fb = MagicMock()
    client._available = True
    client.disconnect()
    assert client._available is False
    assert client._mmap is None
    assert client._fb is None
