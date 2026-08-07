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


def test_link_probe_unreachable(monkeypatch):
    """_desktop_link() returns False (no raise) when desktop is unreachable."""
    monkeypatch.setattr(_MODULE, "_DESKTOP_HOST", "192.0.2.1")  # TEST-NET
    monkeypatch.setattr(_MODULE, "_DESKTOP_PORT", 9)
    monkeypatch.setattr(_MODULE, "_link_cache", {"t": 0.0, "ok": None})
    assert _MODULE._desktop_link() is False


def test_led_sync_daemon(monkeypatch):
    """Voice state and mute push the right LED colors over the client."""
    _set_defaults(monkeypatch)
    dash = _WhisplayDashboard(on_push_to_talk=lambda: None)
    dash._mode = "daemon"
    calls = []

    class FakeClient:
        def led(self, r, g, b):
            calls.append((r, g, b))

        def button_pressed(self):
            return False

    dash._client = FakeClient()
    dash.set_voice_state("LISTENING")
    assert calls[-1] == (0, 255, 80)
    dash.set_voice_state("SPEAKING")
    assert calls[-1] == (0, 160, 255)
    dash.set_muted(True)
    assert calls[-1] == (255, 30, 30)