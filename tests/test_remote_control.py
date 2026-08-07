# tests/test_remote_control.py
"""Tests for the Pi remote-control layer (pi/remote_control.py).

The module shells out to ssh/tailscale, so we monkeypatch subprocess and
socket to avoid touching real fleet hardware in unit tests. Covers device
discovery, reachability, allow-list enforcement, and open_app launching.
"""

import json
import subprocess

import pytest

from pi import remote_control as rc


def _fake_devices(tmp_path, monkeypatch):
    f = tmp_path / "devices.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps([
        {"name": "desktop", "host": "100.97.24.91", "user": "gwuap"},
        {"name": "omnibook", "host": "100.118.212.8", "user": "gwuap"},
    ]))
    monkeypatch.setattr(rc, "DEVICES_FILE", f)
    monkeypatch.setattr(rc, "_ssh_aliases", lambda: {"desktop": {"hostname": "100.97.24.91", "user": "gwuap"}})


def test_discover_devices_merges_registry_and_aliases(tmp_path, monkeypatch):
    _fake_devices(tmp_path, monkeypatch)
    monkeypatch.setattr(rc, "_tailscale_peers", lambda: [
        {"name": "desktop", "host": "100.97.24.91", "online": True},
        {"name": "samsung-tab-a9", "host": "100.100.134.82", "online": False},
    ])
    devs = rc.discover_devices()
    names = {d["name"] for d in devs}
    assert "desktop" in names
    assert "samsung-tab-a9" in names  # from tailscale
    assert "omnibook" in names        # from registry file
    desktop = next(d for d in devs if d["name"] == "desktop")
    assert desktop["host"] == "100.97.24.91"


def test_device_status_reachable(tmp_path, monkeypatch):
    _fake_devices(tmp_path, monkeypatch)
    monkeypatch.setattr(rc, "_alive_tcp", lambda host, ports=(22, 3), timeout=1.0: True)
    st = rc.device_status("desktop")
    assert st["known"] is True
    assert st["reachable"] is True


def test_device_status_unreachable(tmp_path, monkeypatch):
    _fake_devices(tmp_path, monkeypatch)
    monkeypatch.setattr(rc, "_alive_tcp", lambda host, ports=(22, 3), timeout=1.0: False)
    st = rc.device_status("omnibook")
    assert st["known"] is True
    assert st["reachable"] is False


def test_run_blocked_without_allow_shell(tmp_path, monkeypatch):
    _fake_devices(tmp_path, monkeypatch)
    monkeypatch.setattr(rc, "_ALLOW_FREE_SHELL", False)
    r = rc.run("desktop", "whoami")
    assert r["ok"] is False
    assert "disabled" in r["stderr"]


def test_open_app_runs_ssh(tmp_path, monkeypatch):
    _fake_devices(tmp_path, monkeypatch)
    calls = []

    def _fake_ssh(dev, remote_cmd, timeout=15.0):
        calls.append((dev.get("name"), remote_cmd))
        return {"name": dev.get("name"), "ok": True, "rc": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(rc, "_ssh_cmd", _fake_ssh)
    r = rc.open_app("desktop", "browser")
    assert r["ok"] is True
    assert calls[0][0] == "desktop"
    assert "xdg-open" in calls[0][1]


def test_open_app_url_quoted(tmp_path, monkeypatch):
    _fake_devices(tmp_path, monkeypatch)
    captured = {}

    def _fake_ssh(dev, remote_cmd, timeout=15.0):
        captured["cmd"] = remote_cmd
        return {"name": dev.get("name"), "ok": True, "rc": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(rc, "_ssh_cmd", _fake_ssh)
    rc.open_app("desktop", "https://example.com/page?a=1&b=2")
    # The URL must be single-quoted (shlex.quote) so & is not a shell op.
    assert "'https://example.com/page?a=1&b=2'" in captured["cmd"]


def test_fleet_status_aggregates(tmp_path, monkeypatch):
    _fake_devices(tmp_path, monkeypatch)
    monkeypatch.setattr(rc, "_tailscale_peers", lambda: [])
    monkeypatch.setattr(rc, "_alive_tcp", lambda host, ports=(22, 3), timeout=1.0: True)
    rows = rc.fleet_status()
    assert any(r["name"] == "desktop" and r["reachable"] for r in rows)
    assert any(r["name"] == "omnibook" for r in rows)