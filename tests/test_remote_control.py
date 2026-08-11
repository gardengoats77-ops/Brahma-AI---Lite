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


def test_ssh_key_rollout(tmp_path, monkeypatch):
    """rollout_ssh_key.py should copy the Pi's SSH key, verify connection,
    and append the device to the registry file."""
    import sys
    from pathlib import Path as _P

    scripts_dir = _P(__file__).parent.parent / "scripts"
    sys.path.insert(0, str(scripts_dir))

    from rollout_ssh_key import rollout_ssh_key
    import rollout_ssh_key as rsk

    # Redirect the registry to a temp file.
    monkeypatch.setattr(rsk, "DEVICES_FILE", tmp_path / "devices.json")

    # Capture subprocess invocations.
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        # Return "ok" for the verify command so the check passes.
        if isinstance(cmd, list) and any("echo" in str(a) for a in cmd):
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="ok\n", stderr=""
            )
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="", stderr=""
        )

    import subprocess as _sp
    monkeypatch.setattr(_sp, "run", fake_run)

    # Monkeypatch the module's _public_key_content to avoid real key read.
    monkeypatch.setattr(rsk, "_public_key_content", lambda: "ssh-rsa AAAA test@pi")

    # Mock SSH config path so we don't touch real ~/.ssh/config.
    monkeypatch.setattr(rsk, "SSH_CONFIG", tmp_path / "ssh_config")

    # Roll out to a new device.
    result = rsk.rollout_ssh_key("gwuap", "100.97.24.91", alias=None)

    assert result["ok"] is True
    assert result["name"] == "100.97.24.91"

    # Verify ssh-copy-id was invoked (or fallback append).
    ssh_copy_calls = [c for c in calls if isinstance(c, list) and "ssh-copy-id" in c]
    ssh_verify_calls = [c for c in calls if isinstance(c, list) and "ssh" in c and any("echo" in str(a) for a in c)]

    # Either ssh-copy-id worked, or we fell back to manual append.
    assert len(ssh_copy_calls) >= 1 or any(
        "cat" in str(c) and "authorized_keys" in str(c) for c in calls
    )
    assert len(ssh_verify_calls) >= 1

    # Registry file must contain the new device.
    reg = json.loads(rsk.DEVICES_FILE.read_text())
    assert any(d["host"] == "100.97.24.91" and d["user"] == "gwuap" for d in reg)
    assert any(d["name"] == "100.97.24.91" for d in reg)


def test_ssh_key_rollout_with_alias(tmp_path, monkeypatch):
    """When --alias is given, rollout_ssh_key should also create an SSH config
    entry and use the alias as the device name."""
    import sys
    from pathlib import Path as _P

    scripts_dir = _P(__file__).parent.parent / "scripts"
    sys.path.insert(0, str(scripts_dir))

    import rollout_ssh_key as rsk

    monkeypatch.setattr(rsk, "DEVICES_FILE", tmp_path / "devices.json")
    monkeypatch.setattr(rsk, "SSH_CONFIG", tmp_path / "ssh_config")
    monkeypatch.setattr(rsk, "_public_key_content", lambda: "ssh-rsa AAAA test@pi")

    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if isinstance(cmd, list) and any("echo" in str(a) for a in cmd):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok\n", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    import subprocess as _sp
    monkeypatch.setattr(_sp, "run", fake_run)

    result = rsk.rollout_ssh_key("gwuap", "100.97.24.91", alias="desktop")

    assert result["ok"] is True
    assert result["name"] == "desktop"

    # SSH config should have been written.
    ssh_cfg = rsk.SSH_CONFIG.read_text()
    assert "Host desktop" in ssh_cfg
    assert "100.97.24.91" in ssh_cfg
    assert "gwuap" in ssh_cfg

    # Registry should use alias as name.
    reg = json.loads(rsk.DEVICES_FILE.read_text())
    names = {d["name"] for d in reg}
    assert "desktop" in names