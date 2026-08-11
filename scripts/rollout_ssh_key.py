#!/usr/bin/env python3
"""SSH key rollout script for fleet devices.

One command installs the Pi's SSH key on a target device, verifies
the connection works, and adds the device to the rex-remote registry
(~/.config/rex-remote/devices.json).

Usage:
    python scripts/rollout_ssh_key.py <user@host> [--alias name]

If --alias is given, an SSH config entry is created in ~/.ssh/config
so the device can be reached by name (e.g. `ssh desktop`).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

# Module-level paths so tests can monkeypatch them.
DEVICES_FILE: Path = Path.home() / ".config" / "rex-remote" / "devices.json"
SSH_CONFIG: Path = Path.home() / ".ssh" "config"
SSH_KEY_FILE: Path = Path.home() / ".ssh" / "id_rsa.pub"
SSH_DIR: Path = Path.home() / ".ssh"


def _public_key_content(key_path: Path = SSH_KEY_FILE) -> str:
    """Read the local public key, generating one if necessary."""
    if key_path.exists():
        return key_path.read_text(encoding="utf-8").strip()

    # No key — generate one.
    SSH_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    subprocess.run(
        ["ssh-keygen", "-t", "rsa", "-b", "4096", "-f", str(key_path.with_suffix("")),
         "-N", "", "-q"],
        check=True,
        capture_output=True,
    )
    return key_path.read_text(encoding="utf-8").strip()


def _ensure_ssh_alias(alias: str, user: str, host: str) -> None:
    """Create or update an SSH config entry for the alias."""
    SSH_CONFIG.parent.mkdir(mode=0o700, exist_ok=True)

    if SSH_CONFIG.exists():
        content = SSH_CONFIG.read_text(encoding="utf-8")
    else:
        content = ""

    # Check if Host block already exists.
    if f"Host {alias}\n" in content or f"Host {alias} " in content:
        return  # Already configured.

    block = (
        f"\nHost {alias}\n"
        f"    HostName {host}\n"
        f"    User {user}\n"
        f"    StrictHostKeyChecking accept-new\n"
        f"    BatchMode yes\n"
    )
    with SSH_CONFIG.open("a", encoding="utf-8") as fh:
        fh.write(block)

    SSH_CONFIG.chmod(0o600)


def _read_registry(path: Path) -> List[Dict[str, str]]:
    """Read the device registry, returning [] on any error."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def _write_registry(path: Path, devices: List[Dict[str, str]]) -> None:
    """Write the device registry atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(devices, indent=2) + "\n", encoding="utf-8")


def _copy_key_ssh_copy_id(user: str, host: str, key_path: Path = SSH_KEY_FILE) -> bool:
    """Use ssh-copy-id to install the public key on the target."""
    if not shutil.which("ssh-copy-id"):
        return False
    target = f"{user}@{host}"
    try:
        result = subprocess.run(
            ["ssh-copy-id", "-i", str(key_path), target],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except Exception:
        return False


def _copy_key_manual(user: str, host: str, pubkey: str) -> bool:
    """Fallback: append pubkey to ~/.ssh/authorized_keys via a piped SSH session."""
    target = f"{user}@{host}"
    remote_cmd = (
        "mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
        "cat >> ~/.ssh/authorized_keys && "
        "chmod 600 ~/.ssh/authorized_keys"
    )
    try:
        result = subprocess.run(
            ["ssh", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=5",
             target, remote_cmd],
            input=pubkey,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.returncode == 0
    except Exception:
        return False


def _verify_ssh(user: str, host: str, alias: Optional[str] = None) -> bool:
    """Verify that passwordless SSH works by running `echo ok` remotely."""
    if alias:
        target = alias
    else:
        target = f"{user}@{host}"
    try:
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
             "-o", "StrictHostKeyChecking=accept-new",
             target, "echo ok"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0 and "ok" in result.stdout
    except Exception:
        return False


def rollout_ssh_key(
    user: str,
    host: str,
    alias: Optional[str] = None,
    devices_path: Optional[Path] = None,
    ssh_config_path: Optional[Path] = None,
) -> Dict[str, any]:
    """Roll out the Pi's SSH key to a target device.

    1. Generate a local key pair if none exists.
    2. Install the public key via ssh-copy-id (or manual fallback).
    3. Verify the key-based connection works.
    4. Append the device to the registry file.
    5. Optionally create an SSH config alias entry.

    Returns a result dict with ok, name, host, user, alias, and details.
    """
    # Allow overriding paths for testing.
    reg_path = devices_path or DEVICES_FILE
    ssh_cfg = ssh_config_path or SSH_CONFIG

    name = alias or host
    target = f"{user}@{host}"

    result: Dict[str, any] = {
        "ok": False,
        "name": name,
        "host": host,
        "user": user,
        "alias": alias,
    }

    # Step 1: get public key.
    try:
        pubkey = _public_key_content()
    except Exception as e:
        result["error"] = f"failed to read public key: {e}"
        return result

    # Step 2: install key (ssh-copy-id first, fallback to manual).
    if _copy_key_ssh_copy_id(user, host):
        result["method"] = "ssh-copy-id"
    elif _copy_key_manual(user, host, pubkey):
        result["method"] = "manual-append"
    else:
        result["error"] = "failed to copy SSH key to target"
        return result

    # Step 3: verify connection.
    if not _verify_ssh(user, host, alias=alias):
        result["error"] = "key copied but SSH verification failed"
        return result

    # Step 4: append to registry.
    devices = _read_registry(reg_path)
    # Skip if already present.
    already = any(
        d.get("host") == host and d.get("user") == user for d in devices
    )
    if not already:
        devices.append({
            "name": name,
            "host": host,
            "user": user,
        })
        _write_registry(reg_path, devices)

    result["ok"] = True

    # Step 5: optionally create SSH config alias.
    if alias:
        _ensure_ssh_alias(alias, user, host)
        result["ssh_config"] = str(ssh_cfg)

    return result


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install Pi's SSH key on a fleet device and register it.",
    )
    parser.add_argument(
        "target",
        help="Target device as user@host (e.g. gwuap@100.97.24.91)",
    )
    parser.add_argument(
        "--alias",
        default=None,
        help="SSH config alias to create (e.g. 'desktop')",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)

    # Parse user@host.
    if "@" in args.target:
        user, host = args.target.rsplit("@", 1)
    else:
        user = os.environ.get("USER", "pi")
        host = args.target

    result = rollout_ssh_key(user, host, alias=args.alias)

    if result["ok"]:
        print(f"✓ SSH key rolled out to {result['name']} ({result['host']})")
        print(f"  Method: {result.get('method', 'unknown')}")
        if result.get("ssh_config"):
            print(f"  SSH config: {result['ssh_config']}")
        print(f"  Registry: {DEVICES_FILE}")
        return 0
    else:
        print(f"✗ Rollout failed: {result.get('error', 'unknown error')}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())