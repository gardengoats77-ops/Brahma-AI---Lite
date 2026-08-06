"""updater.py — signed, versioned update channel for Almighty AI.

Replaces the old commit-SHA updater with a **signed release channel**:

1. The release channel serves a single signed manifest:

       base64url(manifest_json) . base64url(ed25519_signature)

   manifest_json = {"version": "1.1.0", "url": "<zip download>",
                    "sha256": "<hex sha256 of the zip>",
                    "published": "YYYY-MM-DD"}

2. The app verifies the signature against the embedded public key (below),
   rejects any tampered/malformed manifest, downloads the archive, verifies
   its SHA-256, then applies it **atomically**: files being replaced are
   first copied into a backup dir, and any failure during the copy restores
   them (rollback). ``rollback_last()`` can also undo an applied update.

3. Auto-updates are a **Pro feature** (LICENSE §3 product gate): downloading
   or applying refuses without an active license, and the startup check only
   runs when a valid key is active. Checking version status is read-only and
   free, so the Settings panel can always show what is available.

The vendor signs manifests with ``scripts/sign_update.py`` using
``config/update_private.pem`` (gitignored — never commit or ship it). The
channel URL defaults to the GitHub Releases "latest" asset and can be
overridden per-run with ``ALMIGHTY_UPDATE_URL``.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
import os
import re
import shutil
import sys
import threading
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional

APP_VERSION = "1.0.0"
APP_BUILD = "2026.08.05"


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR = _base_dir()

# Release channel: URL that serves the signed manifest. Override at runtime
# with ALMIGHTY_UPDATE_URL (also honored per-call by AutoUpdater.check()).
UPDATE_MANIFEST_URL = os.getenv(
    "ALMIGHTY_UPDATE_URL",
    "https://github.com/titechprabhasolutions/Brahma-AI---Lite/releases/latest/download/almighty-update.signed.txt",
)

# Public key for verifying signed update manifests. The matching private key
# lives in config/update_private.pem (gitignored) and is used only by
# scripts/sign_update.py — never commit, ship, or embed it.
_UPDATE_PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAh8ZrDjFlft77Gm3T2xoleFloGlvi3rhvMpAVLPVBZLI=
-----END PUBLIC KEY-----
"""

# Paths that are user data / runtime state — never replaced by an update.
_PRESERVE_DIRS = {
    "config", "downloads", "memory", ".git", "build", "dist", "__pycache__",
    ".update-backup", ".update-tmp",
}

CHECK_TIMEOUT_S = 20
DOWNLOAD_TIMEOUT_S = 300


# ── manifest signing / verification ────────────────────────────────────────
def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text.encode("ascii") + b"==")


def _update_public_key():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    return serialization.load_pem_public_key(_UPDATE_PUBLIC_KEY_PEM)


def sign_manifest(payload: dict, private_key_pem: bytes) -> str:
    """Vendor-side helper: sign a manifest payload -> signed manifest string."""
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    key = serialization.load_pem_private_key(private_key_pem, password=None)
    if not isinstance(key, ed25519.Ed25519PrivateKey):
        raise ValueError("private key is not Ed25519")
    return f"{_b64encode(payload_bytes)}.{_b64encode(key.sign(payload_bytes))}"


def verify_manifest(signed: str) -> dict:
    """Verify a signed manifest string and return its dict. Raises ValueError."""
    signed = (signed or "").strip()
    try:
        payload_b64, sig_b64 = signed.split(".", 1)
        payload_bytes = _b64decode(payload_b64)
        signature = _b64decode(sig_b64)
    except Exception as exc:
        raise ValueError("Malformed update manifest") from exc
    try:
        _update_public_key().verify(signature, payload_bytes)
    except Exception as exc:
        raise ValueError("Update manifest signature is invalid") from exc
    try:
        manifest = json.loads(payload_bytes.decode("utf-8"))
    except Exception as exc:
        raise ValueError("Update manifest payload is not JSON") from exc
    if not isinstance(manifest, dict):
        raise ValueError("Update manifest payload is not an object")
    for field in ("version", "url", "sha256"):
        if not str(manifest.get(field) or "").strip():
            raise ValueError(f"Update manifest is missing required field '{field}'")
    return manifest


# ── version comparison ─────────────────────────────────────────────────────
def _version_key(version: str) -> tuple:
    m = re.match(r"\D*(\d+(?:\.\d+)*)", version or "")
    if not m:
        return (0,)
    return tuple(int(x) for x in m.group(1).split("."))


def is_newer_version(candidate: str, current: str) -> bool:
    """True when candidate is a strictly greater semantic-ish version."""
    return _version_key(candidate) > _version_key(current)


def _find_repo_root(extract_dir: Path) -> Optional[Path]:
    """Locate the project root inside a zip (strips a single top-level dir)."""
    try:
        entries = list(extract_dir.iterdir())
    except OSError:
        return None
    dirs = [p for p in entries if p.is_dir()]
    if len(entries) == 1 and len(dirs) == 1:
        return dirs[0]
    return extract_dir


class AutoUpdater:
    """Check, download, and atomically apply signed updates."""

    def __init__(self, base_dir: Optional[Path] = None, startup_log: Optional[Callable[[str], None]] = None):
        self.base_dir = Path(base_dir) if base_dir else BASE_DIR
        self.startup_log = startup_log or (lambda message: None)
        self.state_file = self.base_dir / ".update-state.json"
        self.backup_root = self.base_dir / ".update-backup"
        self.tmp_root = self.base_dir / ".update-tmp"

    def _log(self, message: str) -> None:
        try:
            self.startup_log(message)
        except Exception:
            pass
        print(f"[UPDATER] {message}")

    # ── state ──────────────────────────────────────────────────────────────
    def _read_state(self) -> dict:
        try:
            if self.state_file.exists():
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
        except Exception:
            pass
        return {}

    def _write_state(self, **updates) -> None:
        state = self._read_state()
        state.update(updates)
        try:
            self.state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except Exception:
            pass

    # ── gate ───────────────────────────────────────────────────────────────
    def pro_gated(self) -> bool:
        """Auto-updates are a Pro feature — download/apply is gated."""
        try:
            from config.profile import is_pro
            return not is_pro()
        except Exception:
            return False

    def _locked_message(self) -> str:
        try:
            from config.profile import PRO_LOCKED_MESSAGE
            return PRO_LOCKED_MESSAGE
        except Exception:
            return "Auto-updates require Almighty Pro."

    # ── fetch ──────────────────────────────────────────────────────────────
    def _fetch(self, url: str, timeout: float = CHECK_TIMEOUT_S) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "AlmightyAI-Updater/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read()

    # ── check ──────────────────────────────────────────────────────────────
    def check(self, url: Optional[str] = None) -> dict:
        """Fetch + verify the signed manifest. Returns a status dict, never raises."""
        url = url or os.getenv("ALMIGHTY_UPDATE_URL") or UPDATE_MANIFEST_URL
        try:
            signed = self._fetch(url).decode("utf-8")
            manifest = verify_manifest(signed)
        except Exception as exc:
            result = {"status": "error", "error": str(exc), "current": APP_VERSION}
            self._write_state(last_check=datetime.datetime.now().isoformat(timespec="seconds"), last_result=result)
            return result

        current = APP_VERSION
        latest = str(manifest.get("version") or "").strip()
        if is_newer_version(latest, current):
            result = {
                "status": "update-available",
                "current": current,
                "latest": latest,
                "url": str(manifest["url"]),
                "sha256": str(manifest["sha256"]).lower(),
                "manifest": manifest,
            }
        else:
            result = {"status": "up-to-date", "current": current, "latest": latest or current}
        self._write_state(last_check=datetime.datetime.now().isoformat(timespec="seconds"), last_result=result)
        return result

    def check_in_background(self, on_done: Optional[Callable[[dict], None]] = None) -> threading.Thread:
        def worker() -> None:
            try:
                result = self.check()
            except Exception as exc:
                result = {"status": "error", "error": str(exc), "current": APP_VERSION}
            if on_done:
                try:
                    on_done(result)
                except Exception:
                    pass
        thread = threading.Thread(target=worker, daemon=True, name="almighty-update-check")
        thread.start()
        return thread

    # ── apply ──────────────────────────────────────────────────────────────
    def apply(self, manifest: Optional[dict] = None, url: Optional[str] = None) -> dict:
        """Download, verify, atomically apply. Pro-gated; rolls back on failure."""
        if self.pro_gated():
            return {"ok": False, "message": self._locked_message(), "gated": True}
        if manifest is None:
            result = self.check(url=url)
            if result.get("status") != "update-available":
                return {"ok": False, "message": f"No update available ({result.get('status', 'unknown')})."}
            manifest = result["manifest"]

        version = str(manifest.get("version") or "").strip()
        sha256 = str(manifest.get("sha256") or "").lower()
        download_url = str(manifest.get("url") or "").strip()
        if not version or not sha256 or not download_url:
            return {"ok": False, "message": "Update manifest is incomplete."}

        # 1. download
        self._log(f"downloading v{version}")
        try:
            self.tmp_root.mkdir(parents=True, exist_ok=True)
            archive = self.tmp_root / f"update-{version}.zip"
            archive.write_bytes(self._fetch(download_url, timeout=DOWNLOAD_TIMEOUT_S))
        except Exception as exc:
            return {"ok": False, "message": f"Download failed: {exc}"}

        # 2. verify checksum (the manifest itself was signature-verified)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if digest != sha256:
            return {"ok": False, "message": f"SHA-256 mismatch — refusing to apply (got {digest[:16]}…)."}

        # 3. extract
        extract_dir = self.tmp_root / f"src-{version}"
        shutil.rmtree(extract_dir, ignore_errors=True)
        try:
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(extract_dir)
        except Exception as exc:
            return {"ok": False, "message": f"Archive is invalid: {exc}"}
        src = _find_repo_root(extract_dir)
        if src is None or not src.is_dir():
            return {"ok": False, "message": "Archive has no recognizable project root."}

        # 4. atomic apply (backup-then-copy, restore on failure)
        backup_dir = self.backup_root / f"v{version}"
        try:
            replaced = self._apply_tree(src, backup_dir)
        except Exception as exc:
            try:
                self._restore_tree(backup_dir)
                rollback_note = "rolled back"
            except Exception:
                rollback_note = "rollback also failed — backup kept in .update-backup"
            return {"ok": False, "message": f"Apply failed and {rollback_note}: {exc}"}

        self._write_state(
            applied_version=version,
            applied_at=datetime.datetime.now().isoformat(timespec="seconds"),
            backup_dir=str(backup_dir),
            last_result={"status": "applied", "version": version},
        )
        self._log(f"applied v{version} ({len(replaced)} files replaced)")
        return {"ok": True, "message": f"Updated to v{version}.", "version": version, "files": len(replaced)}

    def _apply_tree(self, src: Path, backup_dir: Path) -> list:
        """Copy src over base_dir; back up replaced files first. Raises on failure."""
        replaced: list[str] = []
        backup_dir.mkdir(parents=True, exist_ok=True)
        for path in sorted(src.rglob("*")):
            if path.is_dir():
                continue
            rel = path.relative_to(src)
            if rel.parts and rel.parts[0] in _PRESERVE_DIRS:
                continue
            dest = self.base_dir / rel
            if dest.exists():
                backup_file = backup_dir / rel
                backup_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest, backup_file)
                replaced.append(str(rel))
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
        return replaced

    def _restore_tree(self, backup_dir: Path) -> None:
        """Copy every backed-up file back over base_dir."""
        if not backup_dir.is_dir():
            return
        for path in sorted(backup_dir.rglob("*")):
            if path.is_dir():
                continue
            rel = path.relative_to(backup_dir)
            dest = self.base_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)

    # ── rollback ───────────────────────────────────────────────────────────
    def rollback_last(self) -> dict:
        """Restore the most recently applied update's backup."""
        state = self._read_state()
        backup_dir = Path(str(state.get("backup_dir") or ""))
        applied = str(state.get("applied_version") or "")
        if not backup_dir.is_dir():
            return {"ok": False, "message": "No rollback backup available."}
        try:
            self._restore_tree(backup_dir)
        except Exception as exc:
            return {"ok": False, "message": f"Rollback failed: {exc}"}
        self._write_state(
            rolled_back_version=applied,
            applied_version=APP_VERSION,
            applied_at="",
            backup_dir="",
            last_result={"status": "rolled-back", "version": applied},
        )
        return {"ok": True, "message": f"Rolled back to the previous version."}

    # ── startup ────────────────────────────────────────────────────────────
    def should_update_on_startup(self, setting_value: Optional[bool] = None) -> bool:
        """Startup auto-check: opt-in setting AND Pro AND not env-suppressed."""
        if os.environ.get("ALMIGHTY_SKIP_UPDATE", "").lower() in {"1", "true", "yes"}:
            return False
        if self.pro_gated():
            return False
        return bool(setting_value if setting_value is not None else True)
