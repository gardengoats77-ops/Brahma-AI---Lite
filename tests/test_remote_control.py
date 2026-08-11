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


# ─── Tailscale SSH fallback transport ───────────────────────────────────────

def test_tailscale_ssh_fallback(tmp_path, monkeypatch):
    """When standard SSH fails with auth error, _ssh_cmd should fall back to
    `tailscale ssh user@host command` automatically."""
    _fake_devices(tmp_path, monkeypatch)

    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        # First call (standard SSH) fails with permission denied.
        if isinstance(cmd, list) and cmd[0] == "ssh" and "tailscale" not in cmd:
            return subprocess.CompletedProcess(
                args=cmd, returncode=255,
                stdout="",
                stderr="Permission denied (publickey,password).",
            )
        # Second call (tailscale ssh) succeeds.
        if isinstance(cmd, list) and cmd[0] == "tailscale":
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="ok\n", stderr=""
            )
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    dev = {"name": "omnibook", "host": "100.118.212.8", "user": "gwuap"}
    result = rc._ssh_cmd(dev, "whoami")

    # Should have tried standard SSH first, then tailscale SSH.
    ssh_calls = [c for c in calls if isinstance(c, list) and c[0] == "ssh"]
    ts_calls = [c for c in calls if isinstance(c, list) and c[0] == "tailscale"]
    assert len(ssh_calls) >= 1, "should attempt standard SSH first"
    assert len(ts_calls) >= 1, "should fall back to tailscale SSH"
    # The tailscale call must include 'ssh' and the user@host target.
    assert "ssh" in ts_calls[0]
    assert "gwuap@100.118.212.8" in ts_calls[0]
    # The command must be passed through.
    assert "whoami" in ts_calls[0]
    # Final result should reflect the tailscale success.
    assert result["ok"] is True
    assert result["rc"] == 0


def test_tailscale_transport_field_uses_tailscale_directly(tmp_path, monkeypatch):
    """When device.transport == 'tailscale', skip standard SSH and use tailscale
    SSH directly — no auth-error fallback needed."""
    _fake_devices(tmp_path, monkeypatch)

    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if isinstance(cmd, list) and cmd[0] == "tailscale":
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="done\n", stderr=""
            )
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    dev = {"name": "tablet", "host": "100.100.134.82",
           "user": "user", "transport": "tailscale"}
    result = rc._ssh_cmd(dev, "uname -a")

    # Must NOT have tried plain SSH — go straight to tailscale.
    ssh_calls = [c for c in calls if isinstance(c, list) and c[0] == "ssh"]
    ts_calls = [c for c in calls if isinstance(c, list) and c[0] == "tailscale"]
    assert len(ssh_calls) == 0, "plain SSH should be skipped for tailscale transport"
    assert len(ts_calls) == 1
    assert result["ok"] is True


def test_standard_ssh_success_no_fallback(tmp_path, monkeypatch):
    """When standard SSH succeeds, no tailscale fallback should happen."""
    _fake_devices(tmp_path, monkeypatch)

    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="ok\n", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    dev = {"name": "desktop", "host": "100.97.24.91", "user": "gwuap"}
    result = rc._ssh_cmd(dev, "uptime")

    ssh_calls = [c for c in calls if isinstance(c, list) and c[0] == "ssh"]
    ts_calls = [c for c in calls if isinstance(c, list) and c[0] == "tailscale"]
    assert len(ssh_calls) == 1
    assert len(ts_calls) == 0
    assert result["ok"] is True


# ─── File transfer between devices ────────────────────────────────────────

def test_file_transfer(tmp_path, monkeypatch):
    """file_send should transfer a file via rsync/scp and verify via checksum."""
    import hashlib

    from pi import file_transfer as ft

    _fake_devices(tmp_path, monkeypatch)

    # Create a real source file.
    src = tmp_path / "test.pdf"
    src.write_bytes(b"fake pdf content\n" * 100)
    src_hash = hashlib.sha256(src.read_bytes()).hexdigest()

    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        # rsync success.
        if isinstance(cmd, list) and cmd[0] == "rsync":
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="", stderr=""
            )
        # ssh sha256sum verification success.
        if isinstance(cmd, list) and cmd[0] == "ssh":
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout=f"{src_hash}  /tmp/doc.pdf\n", stderr=""
            )
        # ssh test -f to check file existence.
        if isinstance(cmd, list) and "test" in cmd:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="", stderr=""
            )
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    device = {"name": "tablet", "host": "100.100.134.82", "user": "gwuap"}
    result = ft.send_file(device, str(src), "/tmp/doc.pdf")

    assert result["ok"] is True
    assert result["verified"] is True
    assert result["local_path"] == str(src)
    assert result["remote_path"] == "/tmp/doc.pdf"
    assert "sha256" in result
    assert result["sha256"] == src_hash

    # Verify rsync was called.
    rsync_calls = [c for c in calls if isinstance(c, list) and c[0] == "rsync"]
    assert len(rsync_calls) == 1
    assert "100.100.134.82" in rsync_calls[0][-1]


def test_file_transfer_missing_file(tmp_path, monkeypatch):
    """file_send should error gracefully when local file is missing."""
    from pi import file_transfer as ft

    _fake_devices(tmp_path, monkeypatch)

    device = {"name": "tablet", "host": "100.100.134.82", "user": "gwuap"}
    result = ft.send_file(device, "/nonexistent/file.pdf", "/tmp/doc.pdf")

    assert result["ok"] is False
    assert "not found" in result["error"].lower() or "no such" in result["error"].lower()


def test_file_send_bridge(tmp_path, monkeypatch):
    """remote_control.file_send should delegate to file_transfer.send_file."""
    from pi import file_transfer as ft
    from pi import remote_control as rc

    _fake_devices(tmp_path, monkeypatch)

    src = tmp_path / "bridge.pdf"
    src.write_bytes(b"bridge test\n" * 10)
    src_hash = __import__("hashlib").sha256(src.read_bytes()).hexdigest()

    monkeypatch.setattr(
        ft,
        "send_file",
        lambda dev, local_path, remote_path, **kw: {
            "ok": True,
            "verified": True,
            "sha256": src_hash,
            "local_path": local_path,
            "remote_path": remote_path,
        },
    )

    # Use "desktop" which is in the registry.
    result = rc.file_send("desktop", str(src), "/tmp/doc.pdf")
    assert result["ok"] is True
    assert result["sha256"] == src_hash


# ─── Streaming dispatch ──────────────────────────────────────────────────────

def test_streaming_dispatch(monkeypatch):
    """dispatch_stream should parse SSE events and yield partial text chunks."""
    import io

    # Fake SSE stream: server sends partial text deltas
    sse_body = (
        "data: {\"delta\": \"Checking\"}\n\n"
        "data: {\"delta\": \" the\"}\n\n"
        "data: {\"delta\": \" fleet\"}\n\n"
        "data: {\"delta\": \" status.\"}\n\n"
        "data: {\"delta\": \" All\"}\n\n"
        "data: {\"delta\": \" systems\"}\n\n"
        "data: {\"delta\": \" online.\"}\n\n"
        "data: [DONE]\n\n"
    )

    class FakeResp:
        def read(self):
            return sse_body.encode()

        def __iter__(self):
            # urllib HTTPResponse iterates line-by-line with trailing \n
            for line in sse_body.split("\n"):
                yield (line + "\n").encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    def fake_urlopen(req, timeout=None):
        return FakeResp()

    monkeypatch.setattr(rc, "_brain_token", lambda: "fake-token")
    monkeypatch.setattr(
        __import__("urllib.request", fromlist=["urlopen"]),
        "urlopen",
        fake_urlopen,
    )

    chunks = list(rc.dispatch_stream("check fleet"))
    # Should yield all the delta fragments
    assert chunks == [
        "Checking", " the", " fleet", " status.",
        " All", " systems", " online.",
    ]


def test_streaming_dispatch_accumulate_and_breakpoints(monkeypatch):
    """accumulate_and_speak should buffer partial text and split at sentence
    boundaries, yielding complete sentences for TTS."""
    chunks = ["Hello", " world", ". ", "How", " are", " you", "? ", "Fine", "."]

    results = list(rc.accumulate_and_speak(iter(chunks)))
    # Should split at ". " and "? " boundaries
    assert "Hello world." in results
    assert "How are you?" in results
    assert "Fine." in results


def test_dispatch_stream_fallback_on_error(monkeypatch):
    """When streaming endpoint returns 404 or non-SSE, fallback to regular
    dispatch() which returns the final response."""
    import urllib.error

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 404, "Not Found", {}, io.BytesIO(b"no stream")
        )

    monkeypatch.setattr(rc, "_brain_token", lambda: "fake-token")
    monkeypatch.setattr(
        __import__("urllib.request", fromlist=["urlopen"]),
        "urlopen",
        fake_urlopen,
    )
    # Patch the non-streaming dispatch to return a known value
    monkeypatch.setattr(
        rc,
        "dispatch",
        lambda prompt, timeout=30.0: {
            "ok": True,
            "status": "completed",
            "assigned_agent": "fleet-agent",
            "task_id": "t-1",
            "result": "Fleet is online",
        },
    )

    # dispatch_stream should catch HTTPError and fallback to dispatch()
    result = rc.dispatch_with_fallback("check fleet")
    assert result.get("ok") is True
    assert result.get("result") == "Fleet is online"


def test_ollama_fallback(monkeypatch):
    """When brain dispatch fails with a connection error, dispatch() should
    fall back to local Ollama endpoint and return its response."""
    import urllib.error
    import urllib.request

    # Brain token available so we get past the token check
    monkeypatch.setattr(rc, "_brain_token", lambda: "fake-token")

    # Override Ollama env (read at import time — patch module attrs directly)
    monkeypatch.setattr(rc, "OLLAMA_HOST", "http://localhost:11434")
    monkeypatch.setattr(rc, "OLLAMA_MODEL", "llama3.2")

    def fake_urlopen(req, timeout=None):
        url = req.get_full_url()
        # Brain URL — simulate connection failure
        if ":8788" in url:
            raise urllib.error.URLError("Connection refused")
        # Ollama URL — return mock response
        if ":11434" in url or "ollama" in url.lower():
            class FakeResp:
                def read(self):
                    return json.dumps({
                        "model": "llama3.2",
                        "response": "Fallback from Ollama: fleet nominal",
                        "done": True,
                    }).encode()
                def __enter__(self):
                    return self
                def __exit__(self, *a):
                    pass
            return FakeResp()
        raise urllib.error.URLError(f"unexpected URL: {url}")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    result = rc.dispatch("check fleet")
    assert result["ok"] is True
    assert result.get("result") == "Fallback from Ollama: fleet nominal"
    assert result.get("fallback") == "ollama"
    assert result.get("model") == "llama3.2"


def test_ollama_fallback_also_fails(monkeypatch):
    """When BOTH brain and Ollama are unreachable, dispatch() should return
    an error dict (not crash)."""
    import urllib.error
    import urllib.request

    monkeypatch.setattr(rc, "_brain_token", lambda: "fake-token")
    monkeypatch.setattr(rc, "OLLAMA_HOST", "http://localhost:11434")
    monkeypatch.setattr(rc, "OLLAMA_MODEL", "llama3.2")

    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    result = rc.dispatch("check fleet")
    assert result["ok"] is False
    assert "ollama" in result.get("error", "").lower()


import io  # noqa: E402 — used in test_dispatch_stream_fallback_on_error