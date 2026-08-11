"""File transfer between fleet devices via rsync/scp over Tailscale.

Provides send_file() to transfer files between devices with:
- rsync over SSH (with Tailscale SSH fallback)
- SHA256 checksum verification after transfer
- Graceful error handling for offline devices, missing files, auth failures

Device registry format::
    {"name": "tablet", "host": "100.x.y.z", "user": "gwuap", "transport": "ssh"}

Transport is "ssh" (default) or "tailscale" (uses ``tailscale ssh``).
"""

from __future__ import annotations

import hashlib
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Dict

# SSH options consistent with remote_control.py
_SSH_BASE = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
             "-o", "StrictHostKeyChecking=accept-new"]


def _sha256(path: str) -> str:
    """Compute SHA256 hex digest of a local file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _target_str(dev: Dict[str, str]) -> str:
    """Build ``user@host`` from a device dict."""
    user = dev.get("user")
    host = (dev.get("host") or dev.get("ip_address")
            or dev.get("alias") or dev.get("name") or "")
    return f"{user}@{host}" if user else host


def _ssh_cmd(dev: Dict[str, str]) -> list[str]:
    """Build the SSH command prefix for rsync -e or standalone ssh."""
    transport = dev.get("transport", "ssh")
    if transport == "tailscale":
        return ["tailscale", "ssh", _target_str(dev)]
    alias = dev.get("alias")
    if alias and not dev.get("host") and not dev.get("ip_address"):
        return ["ssh", *_SSH_BASE, alias]
    return ["ssh", *_SSH_BASE, _target_str(dev)]


def send_file(
    device: Dict[str, str],
    local_path: str,
    remote_path: str,
    timeout: float = 120.0,
) -> Dict[str, Any]:
    """Send a file to a remote device and verify it arrived.

    Uses rsync over SSH for the transfer, then verifies integrity by
    comparing SHA256 checksums. Falls back to file-existence check if
    sha256sum is unavailable on the remote.

    Returns a dict with keys::

        ok, verified, local_path, remote_path, sha256, error, elapsed_s
    """
    t0 = time.monotonic()
    local = Path(local_path)

    # --- Pre-flight checks -----------------------------------------------
    if not local.exists():
        return {
            "ok": False, "verified": False,
            "local_path": local_path, "remote_path": remote_path,
            "sha256": "", "elapsed_s": 0.0,
            "error": f"local file not found: {local_path}",
        }
    if not local.is_file():
        return {
            "ok": False, "verified": False,
            "local_path": local_path, "remote_path": remote_path,
            "sha256": "", "elapsed_s": 0.0,
            "error": f"not a regular file: {local_path}",
        }

    local_hash = _sha256(local_path)
    target = _target_str(device)
    dest = f"{target}:{remote_path}"

    # --- Transfer phase --------------------------------------------------
    transport = device.get("transport", "ssh")
    if transport == "tailscale":
        # tailscale ssh doesn't work as rsync -e target; use scp.
        transfer_cmd = ["scp", *_SSH_BASE, str(local), dest]
    else:
        ssh_flags = " ".join(_SSH_BASE)
        transfer_cmd = [
            "rsync", "-avz", "--progress",
            "-e", f"ssh {ssh_flags}",
            str(local), dest,
        ]

    try:
        r = subprocess.run(
            transfer_cmd, capture_output=True, text=True, timeout=timeout,
        )
        if r.returncode != 0:
            return {
                "ok": False, "verified": False,
                "local_path": local_path, "remote_path": remote_path,
                "sha256": local_hash,
                "elapsed_s": round(time.monotonic() - t0, 2),
                "error": (
                    f"{transfer_cmd[0]} failed (rc={r.returncode}): "
                    f"{r.stderr.strip()[:500]}"
                ),
            }
    except subprocess.TimeoutExpired:
        return {
            "ok": False, "verified": False,
            "local_path": local_path, "remote_path": remote_path,
            "sha256": local_hash,
            "elapsed_s": round(time.monotonic() - t0, 2),
            "error": f"{transfer_cmd[0]} timeout after {timeout}s",
        }
    except OSError as e:
        return {
            "ok": False, "verified": False,
            "local_path": local_path, "remote_path": remote_path,
            "sha256": local_hash,
            "elapsed_s": round(time.monotonic() - t0, 2),
            "error": f"{transfer_cmd[0]} error: {e}",
        }

    # --- Verification phase ----------------------------------------------
    # Prefer SHA256 checksum comparison.
    try:
        r = subprocess.run(
            ["ssh", *_SSH_BASE, target, f"sha256sum {shlex_quote(remote_path)}"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            remote_hash = (r.stdout or "").strip().split()[0]
            verified = remote_hash == local_hash
            return {
                "ok": True, "verified": verified,
                "local_path": local_path, "remote_path": remote_path,
                "sha256": local_hash, "remote_sha256": remote_hash,
                "elapsed_s": round(time.monotonic() - t0, 2),
                "error": "" if verified else "checksum mismatch",
            }
    except Exception:  # noqa: BLE001
        pass

    # Fallback: check file exists.
    try:
        r = subprocess.run(
            ["ssh", *_SSH_BASE, target, f"test -f {shlex_quote(remote_path)}"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            return {
                "ok": True, "verified": False,
                "local_path": local_path, "remote_path": remote_path,
                "sha256": local_hash,
                "elapsed_s": round(time.monotonic() - t0, 2),
                "error": "",
            }
    except Exception:  # noqa: BLE001
        pass

    return {
        "ok": True, "verified": False,
        "local_path": local_path, "remote_path": remote_path,
        "sha256": local_hash,
        "elapsed_s": round(time.monotonic() - t0, 2),
        "error": "transfer succeeded but verification failed",
    }


def shlex_quote(s: str) -> str:
    """Quote a string for safe use in remote shell commands."""
    return shlex.quote(s)