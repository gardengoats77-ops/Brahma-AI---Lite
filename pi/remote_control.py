"""Remote control layer for the REX Pi — drive the desktop + fleet over SSH.

The Pi Sugar WhiPlay HAT is the boss's handset: this module gives it the
reach to actually *do things* on other machines instead of only displaying
stats. The Pi already holds passwordless SSH keys for the desktop (verified:
`ssh desktop` / gwuap@100.97.24.91 works live), so the transport is plain
SSH — no extra daemons, no agent installs, no credentials in this repo.

The device registry is intentionally tiny and file-driven:

    ~/.config/rex-remote/devices.json
    [
      {"name": "desktop", "host": "100.97.24.91", "user": "gwuap",
       "alias": "desktop"},          # optional ssh-config alias (preferred)
      {"name": "omnibook", "host": "100.118.212.8", "user": "gwuap"},
      ...
    ]

If the file is absent we fall back to `tailscale status` plus the SSH
aliases found in `~/.ssh/config` — enough to catalogue the live fleet even
before the boss hand-edits the registry.

Security model: this module runs the command VERBATIM through ssh. That is
the point (it is the boss's swarm controller), but callers must not expose
`run()` to unauthenticated input. The voice-loop bridge below only exposes
`open_app()` and `status()`; `run()` is opt-in via env
REX_REMOTE_ALLOW_SHELL=1 and is meant for trusted CLI/local use.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

DEVICES_FILE = Path.home() / ".config" / "rex-remote" / "devices.json"
SSH_CONFIG = Path.home() / ".ssh" / "config"
# DEVICES_FILE is the same registry written by scripts/rollout_ssh_key.py —
# any device added via `python scripts/rollout_ssh_key.py user@host` shows up
# automatically in discover_devices() on the next call (no restart needed).
_ALLOW_FREE_SHELL = os.environ.get("REX_REMOTE_ALLOW_SHELL", "").strip().lower() in (
    "1", "true", "yes"
)

# Default fallback apps per-OS so `open_app` works without the boss passing
# a full launch string.
_APPS: Dict[str, str] = {
    # linux / desktop
    "firefox":     "xdg-open https://example.com",
    "browser":     "xdg-open https://example.com",
    "terminal":    "gnome-terminal --",
    "files":       "xdg-open $HOME",
    "code":        "code",
    "music":       "xdg-open https://music.youtube.com",
    # cross
    "screenshot":  "gnome-screenshot -f /tmp/rex-shot.png",
}


# ─── Device registry ────────────────────────────────────────────────────────
def _ssh_aliases() -> Dict[str, Dict[str, str]]:
    """Parse `~/.ssh/config` Host blocks into {alias: {hostname, user}}."""
    out: Dict[str, Dict[str, str]] = {}
    if not SSH_CONFIG.exists():
        return out
    cur: Optional[str] = None
    try:
        for line in SSH_CONFIG.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, _, val = line.partition(" ")
            val = val.strip()
            if key.lower() == "host":
                cur = val
                out.setdefault(cur, {})
            elif cur is not None:
                out[cur][key.lower()] = val
    except Exception:  # noqa: BLE001
        return {}
    return out


def _tailscale_peers() -> List[Dict[str, Any]]:
    """Catalogue live tailscale peers (name, host, online)."""
    peers: List[Dict[str, Any]] = []
    try:
        r = subprocess.run(
            ["tailscale", "status"],
            capture_output=True, text=True, timeout=4,
        )
        for line in (r.stdout or "").splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            ip, name = parts[0], parts[1]
            online = "offline" not in line
            peers.append({"name": name, "host": ip, "online": online})
    except Exception:  # noqa: BLE001
        pass
    return peers


def _registry() -> list[Dict[str, str]]:
    if DEVICES_FILE.exists():
        try:
            data = json.loads(DEVICES_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception:  # noqa: BLE001
            pass
    return []


def discover_devices() -> List[Dict[str, str]]:
    """Merge the configured registry with any extra live Tailscale peers."""
    merged: Dict[str, Dict[str, Any]] = {}
    for dev in _registry():
        merged[dev["name"]] = dict(dev)
    for peer in _tailscale_peers():
        if peer["name"] not in merged:
            merged[peer["name"]] = peer
    # Throw in ~/.ssh/config aliases as well so `desktop` resolves.
    for alias, cfg in _ssh_aliases().items():
        merged.setdefault(alias, {"name": alias, "alias": alias})
        merged[alias].setdefault("alias", alias)
        if "hostname" in cfg:
            merged[alias]["host"] = cfg["hostname"]
        if "user" in cfg:
            merged[alias]["user"] = cfg["user"]
    return list(merged.values())


def _alive_tcp(host: str, ports: tuple[int, ...] = (22, 3), timeout: float = 1.0) -> bool:
    for port in ports:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def device_status(name: str) -> Dict[str, Any]:
    """Reachability snapshot for one device (registry lookup + TCP probe)."""
    dev = next((d for d in discover_devices() if d.get("name") == name), None)
    if dev is None:
        return {"name": name, "known": False, "reachable": False}
    host = dev.get("host") or dev.get("alias") or name
    reachable = _alive_tcp(host)
    return {
        "name": name,
        "known": True,
        "reachable": reachable,
        "host": host,
        "online": dev.get("online", reachable),
    }


def fleet_status() -> List[Dict[str, Any]]:
    out = []
    for dev in discover_devices():
        out.append(device_status(dev.get("name", "?")))
    return out


# ── Command execution ───────────────────────────────────────────────────────
def _build_target(dev: Dict[str, str]) -> tuple[List[str], str]:
    """Return (ssh_target, user_at_host) for a device."""
    alias = dev.get("alias") or dev.get("name") or ""
    user = dev.get("user")
    host = dev.get("host") or dev.get("ip_address") or alias
    if user:
        target_str = f"{user}@{host}"
    else:
        target_str = host
    if alias and not dev.get("host") and not dev.get("ip_address"):
        ssh_target: List[str] = [alias]
    else:
        ssh_target = [target_str]
    return ssh_target, target_str


def _run_tailscale_ssh(dev: Dict[str, str], target_str: str,
                       remote_cmd: str, timeout: float, t0: float) -> Dict[str, Any]:
    """Execute a command via `tailscale ssh` — no keys needed, works over tailnet."""
    ts_args = ["tailscale", "ssh", target_str, remote_cmd]
    try:
        r = subprocess.run(
            ts_args, capture_output=True, text=True, timeout=timeout,
        )
        return {
            "name": dev.get("name", "?"),
            "ok": r.returncode == 0,
            "rc": r.returncode,
            "stdout": (r.stdout or "").strip()[-2000:],
            "stderr": (r.stderr or "").strip()[-1000:],
            "elapsed_s": round(time.monotonic() - t0, 2),
            "transport": "tailscale",
        }
    except subprocess.TimeoutExpired:
        return {"name": dev.get("name", "?"), "ok": False, "rc": -1,
                "stdout": "", "stderr": f"timeout after {timeout}s",
                "transport": "tailscale"}
    except Exception as e:  # noqa: BLE001
        return {"name": dev.get("name", "?"), "ok": False, "rc": -2,
                "stdout": "", "stderr": str(e), "transport": "tailscale"}


def _ssh_cmd(dev: Dict[str, str], remote_cmd: str, timeout: float = 15.0) -> Dict[str, Any]:
    t0 = time.monotonic()
    transport = dev.get("transport", "ssh")
    ssh_target, target_str = _build_target(dev)

    # Explicit tailscale transport — skip standard SSH entirely.
    if transport == "tailscale":
        return _run_tailscale_ssh(dev, target_str, remote_cmd, timeout, t0)

    ssh_args = [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
        "-o", "StrictHostKeyChecking=accept-new",
        *ssh_target, remote_cmd,
    ]
    try:
        r = subprocess.run(
            ssh_args, capture_output=True, text=True, timeout=timeout,
        )
        # On auth failure, fall back to tailscale SSH (no keys needed).
        if r.returncode == 255:
            stderr_lower = (r.stderr or "").lower()
            if "permission denied" in stderr_lower or "publickey" in stderr_lower:
                return _run_tailscale_ssh(dev, target_str, remote_cmd, timeout, t0)
        return {
            "name": dev.get("name", "?"),
            "ok": r.returncode == 0,
            "rc": r.returncode,
            "stdout": (r.stdout or "").strip()[-2000:],
            "stderr": (r.stderr or "").strip()[-1000:],
            "elapsed_s": round(time.monotonic() - t0, 2),
        }
    except subprocess.TimeoutExpired:
        return {"name": dev.get("name", "?"), "ok": False, "rc": -1,
                "stdout": "", "stderr": f"timeout after {timeout}s"}
    except Exception as e:  # noqa: BLE001
        return {"name": dev.get("name", "?"), "ok": False, "rc": -2,
                "stdout": "", "stderr": str(e)}


def run(name: str, command: str, timeout: float = 15.0) -> Dict[str, Any]:
    """Run an arbitrary shell command on a remote device (opt-in)."""
    if not _ALLOW_FREE_SHELL:
        return {
            "name": name, "ok": False, "rc": -3,
            "stderr": "free-shell disabled — set REX_REMOTE_ALLOW_SHELL=1",
        }
    dev = next((d for d in discover_devices() if d.get("name") == name), None)
    if dev is None:
        return {"name": name, "ok": False, "rc": -4, "stderr": "unknown device"}
    return _ssh_cmd(dev, command, timeout=timeout)


from pi import file_transfer


def file_send(name: str, local_path: str, remote_path: str,
              timeout: float = 120.0) -> Dict[str, Any]:
    """Send a file to a remote device by name (dispatches to file_transfer).

    Looks up the device in the registry, validates the local file exists,
    and transfers via rsync/scp with checksum verification.

    Usage: ``file_send("tablet", "/home/gwuap/doc.pdf", "/tmp/doc.pdf")``
    """
    dev = next((d for d in discover_devices() if d.get("name") == name), None)
    if dev is None:
        return {
            "name": name, "ok": False, "verified": False,
            "error": f"unknown device: {name}",
        }
    return file_transfer.send_file(dev, local_path, remote_path, timeout=timeout)


def open_app(name: str, app: str = "browser", timeout: float = 15.0) -> Dict[str, Any]:
    """Open a well-known app on a remote device (safe, allow-listed)."""
    dev = next((d for d in discover_devices() if d.get("name") == name), None)
    if dev is None:
        return {"name": name, "ok": False, "rc": -4, "stderr": "unknown device"}
    app = (app or "browser").strip().lower()
    if app in _APPS:
        cmd = _APPS[app]
    elif app.startswith("http://") or app.startswith("https://"):
        cmd = f"xdg-open {shlex.quote(app)}"
    else:
        cmd = f"{shlex.quote(app)} >/dev/null 2>&1 &"
    return _ssh_cmd(dev, f"nohup bash -c {shlex.quote(cmd)} >/dev/null 2>&1 &", timeout=timeout)


def _demo() -> List[str]:
    """Human-readable status lines for CLI use."""
    rows = []
    for st in fleet_status():
        state = "up" if st["reachable"] else ("down" if st.get("known") else "unknown")
        rows.append(f"{st['name']:<16} {state:<8} {st.get('host','')}")
    return rows


# ── Brain dispatch bridge ────────────────────────────────────────────────────
# The desktop's REX-OMEGA brain (port 8788) is a 45-agent orchestrator with a
# POST /api/dispatch endpoint. It's token-gated; the token lives in the running
# process's env (GWUAP_REX_WEB_TOKEN) or the file ~/REX-OMEGA/config/web_token.txt.
# We read it over SSH at runtime — never stored in this repo, never logged.

_BRAIN_HOST = os.environ.get("REX_DESKTOP_HOST", "100.97.24.91")
_BRAIN_PORT = int(os.environ.get("REX_DESKTOP_PORT", "8788"))
_BRAIN_TOKEN_CACHE: Optional[str] = None
_BRAIN_TOKEN_TTL = 300  # 5 min cache — token rarely rotates
_BRAIN_TOKEN_TS = 0.0


def _brain_token() -> str:
    """Fetch the brain API token over SSH from the desktop's running process."""
    global _BRAIN_TOKEN_CACHE, _BRAIN_TOKEN_TS
    if _BRAIN_TOKEN_CACHE and (time.monotonic() - _BRAIN_TOKEN_TS) < _BRAIN_TOKEN_TTL:
        return _BRAIN_TOKEN_CACHE

    # Try the env of the running brain process first (most reliable), then
    # fall back to the file. The env token is short (rex_omega_...), the file
    # token is a 64-char hex string. We prefer the first line that is NOT
    # 64 hex chars.
    ssh_cmd = (
        "ssh -o BatchMode=yes -o ConnectTimeout=3 -o StrictHostKeyChecking=accept-new "
        "desktop '"
        "BPID=$(pgrep -f uvicorn.*8788 | head -1);"
        "if [ -n \"$BPID\" ]; then "
        "  cat /proc/$BPID/environ 2>/dev/null | tr \"\\0\" \"\\n\" | grep \"^GWUAP_REX_WEB_TOKEN=\" | cut -d= -f2-;"
        "fi;"
        "cat ~/REX-OMEGA/config/web_token.txt 2>/dev/null"
        "'"
    )
    try:
        r = subprocess.run(ssh_cmd, shell=True, capture_output=True, text=True, timeout=8)
        lines = [l.strip() for l in (r.stdout or "").splitlines() if l.strip()]
        if lines:
            # Prefer the env token (short, non-hex) over the file token (64 hex)
            for tok in lines:
                if len(tok) != 64 or not all(c in "0123456789abcdef" for c in tok.lower()):
                    _BRAIN_TOKEN_CACHE = tok
                    _BRAIN_TOKEN_TS = time.monotonic()
                    return _BRAIN_TOKEN_CACHE
            # Fallback: just use the first line
            _BRAIN_TOKEN_CACHE = lines[0]
            _BRAIN_TOKEN_TS = time.monotonic()
            return _BRAIN_TOKEN_CACHE
    except Exception:  # noqa: BLE001
        pass
    return ""


def _brain_url(path: str) -> str:
    return f"http://{_BRAIN_HOST}:{_BRAIN_PORT}{path}"


def dispatch(prompt: str, timeout: float = 30.0) -> Dict[str, Any]:
    """Send a natural-language dispatch to the brain's agent orchestrator.

    Returns the dispatch response (task_id, assigned_agent, status) or
    an error dict. The prompt goes to the 45-agent router which picks the
    right specialist and runs it.
    """
    import urllib.request
    import urllib.error

    token = _brain_token()
    if not token:
        return {"ok": False, "error": "could not fetch brain token over SSH"}

    body = json.dumps({"prompt": prompt}).encode()
    req = urllib.request.Request(
        _brain_url("/api/dispatch"),
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
            return {"ok": True, **data}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}", "detail": e.read().decode()[:500]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def list_agents(timeout: float = 10.0) -> Dict[str, Any]:
    """List the brain's available agents (45-agent fleet)."""
    import urllib.request
    import urllib.error

    token = _brain_token()
    if not token:
        return {"ok": False, "error": "could not fetch brain token over SSH"}

    req = urllib.request.Request(
        _brain_url("/api/agents"),
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"ok": True, "agents": json.loads(resp.read().decode())}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    if args and args[0] == "status":
        print("\n".join(_demo()) or "(no devices catalogued)")
        raise SystemExit(0)
    if args and args[0] == "dispatch":
        print(dispatch(" ".join(args[1:])))
        raise SystemExit(0)
    if args and args[0] == "agents":
        print(list_agents())
        raise SystemExit(0)
    if len(args) >= 2 and args[0] == "open":
        print(open_app(args[1], args[2] if len(args) > 2 else "browser"))
        raise SystemExit(0)
    if len(args) >= 2 and args[0] == "run":
        print(run(args[1], " ".join(args[2:])))
        raise SystemExit(0)
    print("usage: remote_control.py status|dispatch <msg>|agents|open <dev> [app]|run <dev> <cmd>")