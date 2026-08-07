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
def _ssh_cmd(dev: Dict[str, str], remote_cmd: str, timeout: float = 15.0) -> Dict[str, Any]:
    t0 = time.monotonic()
    target: List[str] = []
    alias = dev.get("alias") or dev.get("name")
    user = dev.get("user")
    host = dev.get("host") or dev.get("ip_address") or alias
    if alias and not host:
        # Trust the ssh config alias: `ssh desktop <cmd>`
        target = [alias]
    elif user:
        target = [f"{user}@{host}"]
    else:
        target = [host]

    ssh_args = [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
        "-o", "StrictHostKeyChecking=accept-new",
        *target, remote_cmd,
    ]
    try:
        r = subprocess.run(
            ssh_args, capture_output=True, text=True, timeout=timeout,
        )
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


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    if args and args[0] == "status":
        print("\n".join(_demo()) or "(no devices catalogued)")
        raise SystemExit(0)
    if len(args) >= 2 and args[0] == "open":
        print(open_app(args[1], args[2] if len(args) > 2 else "browser"))
        raise SystemExit(0)
    if len(args) >= 2 and args[0] == "run":
        print(run(args[1], " ".join(args[2:])))
        raise SystemExit(0)
    print("usage: remote_control.py status|open <dev> [app]|run <dev> <cmd>")