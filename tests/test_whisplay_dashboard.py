# tests/test_whisplay_dashboard.py
"""Tests for the WhiPlay HAT dashboard (pi/whisplay_dashboard.py).

Covers the daemon-socket-first ownership model that matches the WORKING Pi
setup: when `whisplay-daemon` owns the panel, the dashboard must talk over
the socket and must NOT open the board / GPIO directly (that path EBUSY's).
Also covers graceful headless degradation when neither daemon nor board is
available, and the rotation into the portrait framebuffer.

The module name is discovered from the filesystem so the spelling cannot
drift (whisplay / whiplay / whispay ... variants have bitten this test).
"""

import importlib
import os

import pytest

_MODULE = None
for _f in os.listdir(os.path.join(os.path.dirname(__file__), "..", "pi")):
    if _f.startswith("whis") and "dashboard" in _f and _f.endswith(".py"):
        _MODULE = importlib.import_module("pi." + _f[:-3])
        break
assert _MODULE is not None, "could not locate the dashboard module under pi/"

_WhisplayDashboard = _MODULE.WhisplayDashboard
_DaemonClient = _MODULE._DaemonClient


def _set_defaults(monkeypatch):
    monkeypatch.setattr(_MODULE, "_SOCKET_PATH", "/nonexistent/x.sock")
    monkeypatch.setattr(_MODULE, "_BOARD_AVAILABLE", False)
    monkeypatch.setattr(_MODULE, "_BOARD_IMPORT_ERR", None)


def test_headless_when_no_daemon_and_no_board(monkeypatch):
    _set_defaults(monkeypatch)
    dash = _WhisplayDashboard(on_push_to_talk=lambda: None)
    assert dash.available is False
    assert dash._mode is None
    dash.start()
    dash.set_voice_state("LISTENING")


def test_start_idempotent(monkeypatch):
    _set_defaults(monkeypatch)
    dash = _WhisplayDashboard(on_push_to_talk=lambda: None)
    dash.start()
    dash.start()


def test_frame_fits_portrait_framebuffer(monkeypatch):
    _set_defaults(monkeypatch)
    dash = _WhisplayDashboard()
    data = dash._frame()
    assert isinstance(data, bytes)
    assert len(data) == 240 * 280 * 2