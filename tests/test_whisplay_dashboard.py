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
import time

import pytest
from unittest.mock import MagicMock

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


def test_double_press_toggles_wake_word(monkeypatch):
    """Double-press PTT within 500ms toggles wake word listener off."""
    _set_defaults(monkeypatch)
    dash = _WhisplayDashboard(on_push_to_talk=lambda: None)
    calls = []

    class FakeWakeListener:
        enabled = True

        def set_enabled(self, v):
            calls.append(v)
            self.enabled = v

    dash._wake_listener = FakeWakeListener()
    dash._btn_was_down = False
    # First press
    dash._on_button_down()
    time.sleep(0.1)
    dash._on_button_up()
    time.sleep(0.1)
    # Second press (within 500ms window)
    dash._on_button_down()
    time.sleep(0.1)
    dash._on_button_up()
    assert calls == [False]  # toggled off (was True, now False)


def test_double_press_outside_window_no_toggle(monkeypatch):
    """Single press or slow double-press does NOT toggle wake word."""
    _set_defaults(monkeypatch)
    dash = _WhisplayDashboard(on_push_to_talk=lambda: None)
    calls = []

    class FakeWakeListener:
        enabled = True

        def set_enabled(self, v):
            calls.append(v)
            self.enabled = v

    dash._wake_listener = FakeWakeListener()
    dash._btn_was_down = False
    # Single press only
    dash._on_button_down()
    time.sleep(0.1)
    dash._on_button_up()
    assert calls == []  # no toggle
    # Wait past the window
    time.sleep(0.5)
    dash._on_button_down()
    time.sleep(0.1)
    dash._on_button_up()
    assert calls == []  # still no toggle (outside window)


def test_single_press_still_triggers_ptt(monkeypatch):
    """Single press should still trigger PTT callback (not toggle)."""
    _set_defaults(monkeypatch)
    ptt_calls = []
    dash = _WhisplayDashboard(on_push_to_talk=lambda: ptt_calls.append(1))
    dash._btn_was_down = False

    # Simulate single press via poll button (edge detection)
    class FakeClient:
        def __init__(self):
            self._count = 0

        def led(self, r, g, b):
            pass

        def button_pressed(self):
            self._count += 1
            # Return True once, then False
            return self._count == 1

    dash._mode = "daemon"
    dash._client = FakeClient()
    dash._poll_button()
    assert ptt_calls == [1]


def test_breathing_starts_on_idle(monkeypatch):
    """Breathing animation runs when voice state is IDLE (sine wave on blue)."""
    _set_defaults(monkeypatch)
    led_calls = []
    dash = _WhisplayDashboard(on_push_to_talk=lambda: None)
    dash._mode = "daemon"
    dash._client = MagicMock(led=lambda *a: led_calls.append(a))
    dash.set_voice_state("IDLE")
    time.sleep(0.5)
    blue_values = [c[2] for c in led_calls if len(c) == 3]
    assert len(blue_values) > 1
    assert max(blue_values) > min(blue_values)