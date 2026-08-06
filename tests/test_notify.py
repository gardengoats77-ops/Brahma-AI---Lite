"""Tests for notify.py — the cross-platform desktop notification helper.

The linux_shim keeps its win10toast stub (test_stub_members_raise_for_fallbacks
asserts `ToastNotifier()` still raises), so the real notification path on
Linux must never call win10toast.  These tests lock that contract:

  * on Linux, notify() calls `dbus-send` to org.freedesktop.Notifications
    (typed args — immune to a broken notify-send binary);
  * the fallback chain (dbus-send -> notify-send) still runs when the
    preferred tool is missing;
  * notify() never raises, even when every backend fails.
"""

from __future__ import annotations

import os
import sys

import pytest

# Import the module directly (it has no heavy deps and no win10toast import).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import notify  # noqa: E402


class _Result:
    returncode = 0
    stdout = b""
    stderr = b""


@pytest.fixture(autouse=True)
def _force_linux(monkeypatch):
    """Always exercise the Linux backend path regardless of the host OS."""
    monkeypatch.setattr(notify.platform, "system", lambda: "Linux")


def test_linux_uses_dbus_send_typed_args(monkeypatch):
    calls = []

    def _run(args, **kwargs):
        calls.append(args)
        return _Result()

    monkeypatch.setattr(notify.subprocess, "run", _run)

    ok = notify.notify("Reminder", "Drink water", timeout_ms=5000)

    assert ok is True
    assert len(calls) == 1
    cmd = calls[0]
    assert "dbus-send" in cmd[0]
    assert "org.freedesktop.Notifications.Notify" in cmd
    # typed args carry the payload
    assert "string:Reminder" in cmd
    assert "string:Drink water" in cmd
    assert "int32:5000" in cmd
    # never touches win10toast on Linux
    assert not any("win10toast" in c for c in cmd)


def test_linux_falls_back_to_notify_send(monkeypatch):
    calls = []

    def _run(args, **kwargs):
        calls.append(args)
        return _Result()

    monkeypatch.setattr(notify.subprocess, "run", _run)
    # force dbus-send to fail (backend returns False), then notify-send succeeds
    monkeypatch.setattr(notify, "_dbus_send_notify", lambda *a, **kw: False)

    ok = notify.notify("Hi", "There")

    assert ok is True
    assert calls, "notify-send fallback was never invoked"
    assert "notify-send" in calls[0][0]


def test_never_raises_when_all_backends_fail(monkeypatch):
    monkeypatch.setattr(
        notify.subprocess, "run",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    ok = notify.notify("Hi", "There")

    assert ok is False  # gracefully degrades, no exception


def test_empty_payload_is_noop():
    assert notify.notify("", "   ") is False
