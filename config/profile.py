# config/profile.py
"""User identity profile — display name + default city.

Single source of truth for the user's display name and default city,
stored in ``config/app_settings.json`` (gitignored — safe to commit
the repo without leaking identity). Resolution order for the name:

1. ``ALMIGHTY_USER_NAME`` environment variable (if set)
2. ``user_name`` key in ``app_settings.json``
3. ``DEFAULT_USER_NAME`` fallback

The city is read from ``city`` in ``app_settings.json`` with
``DEFAULT_CITY`` as fallback.

Modules (ui, actions/*, dashboard) import these helpers instead of
hardcoding a name or city, so identity changes are a config edit —
no code changes, no forks.
"""

import base64
import datetime
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent
APP_SETTINGS_FILE = CONFIG_DIR / "app_settings.json"

DEFAULT_USER_NAME = "chuckee"
DEFAULT_CITY = "Saint Paul"


def _load() -> dict:
    try:
        data = json.loads(APP_SETTINGS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(settings: dict) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        APP_SETTINGS_FILE.write_text(json.dumps(settings, indent=4), encoding="utf-8")
    except Exception:
        pass


def get_user_name() -> str:
    """Return the user's display name (env override -> settings -> default)."""
    env = os.getenv("ALMIGHTY_USER_NAME", "").strip()
    if env:
        return env
    return str(_load().get("user_name") or DEFAULT_USER_NAME).strip() or DEFAULT_USER_NAME


def get_city() -> str:
    """Return the user's default city (settings -> default)."""
    return str(_load().get("city") or DEFAULT_CITY).strip() or DEFAULT_CITY


def set_user_name(name: str) -> None:
    name = (name or "").strip() or DEFAULT_USER_NAME
    settings = _load()
    settings["user_name"] = name
    _save(settings)


def set_city(city: str) -> None:
    city = (city or "").strip() or DEFAULT_CITY
    settings = _load()
    settings["city"] = city
    _save(settings)


def save_profile(user_name: str | None = None, city: str | None = None) -> dict:
    """Persist one or both profile fields. Returns the resolved profile."""
    if user_name is not None:
        set_user_name(user_name)
    if city is not None:
        set_city(city)
    return {"user_name": get_user_name(), "city": get_city()}


# ─────────────────────────────────────────────────────────────────────────────
# Commercial licensing (LICENSE §3)
#
# Offline, signed license keys. A Pro key is an Ed25519-signed JSON payload:
#
#     base64url(payload_json) . base64url(signature)
#
# payload_json = {"licensee": str, "tier": "pro", "issued": "YYYY-MM-DD",
#                 "expires": "YYYY-MM-DD" (optional)}
#
# The signature covers the exact payload bytes, so the verifier needs no
# canonical re-serialization. The private key stays with the copyright holder
# (config/license_private.pem, gitignored — never shipped); the app only
# carries the public key below. Issue keys with:
#
#     python scripts/make_license.py --issue "Acme Corp" [--days 365]
#
# Activation precedence: a valid ALMIGHTY_LICENSE_KEY env var wins (ephemeral
# — not persisted), otherwise config/license.json, otherwise Community.
# ─────────────────────────────────────────────────────────────────────────────

LICENSE_FILE = CONFIG_DIR / "license.json"

# Public key for verifying Pro license keys. The matching private key lives in
# config/license_private.pem (gitignored) and is used only by the vendor-side
# scripts/make_license.py — it must never be committed or embedded in the app.
_LICENSE_PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEA19r24GrjFGyOb49vsTsrNWicYI8CvoUrgWdEYhKF4cs=
-----END PUBLIC KEY-----
"""

PRO_LOCKED_MESSAGE = (
    "This feature requires Almighty Pro. Activate a Pro license key in "
    "Settings → System & Connect → Licensing."
)


@dataclass
class LicenseState:
    """Resolved license state for the current process."""
    valid: bool
    tier: str = "community"  # "community" | "pro"
    licensee: str = ""
    expires: str = ""        # ISO date or ""
    reason: str = ""         # "" when valid


# Community is always a valid state — Pro is an opt-in upgrade.
COMMUNITY = LicenseState(valid=True, tier="community", reason="")


def _public_key():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    return serialization.load_pem_public_key(_LICENSE_PUBLIC_KEY_PEM)


def _decode_key(key: str) -> tuple[bytes, bytes, dict] | None:
    """Split a license key into (payload_bytes, signature, payload_dict)."""
    key = (key or "").strip()
    try:
        payload_b64, sig_b64 = key.split(".", 1)
        payload_bytes = base64.urlsafe_b64decode(payload_b64.encode("ascii") + b"==")
        signature = base64.urlsafe_b64decode(sig_b64.encode("ascii") + b"==")
        payload = json.loads(payload_bytes.decode("utf-8"))
        if not isinstance(payload, dict):
            return None
        return payload_bytes, signature, payload
    except Exception:
        return None


def _validate_key(key: str) -> LicenseState:
    """Verify a raw license key string (no persistence, no caching)."""
    parsed = _decode_key(key)
    if parsed is None:
        return LicenseState(valid=False, reason="Malformed license key")
    payload_bytes, signature, payload = parsed
    try:
        _public_key().verify(signature, payload_bytes)
    except Exception:
        return LicenseState(valid=False, reason="Invalid license key signature")
    if payload.get("tier") != "pro":
        return LicenseState(valid=False, reason="License key does not grant the Pro tier")
    expires = str(payload.get("expires") or "").strip()
    if expires:
        try:
            if datetime.date.fromisoformat(expires) < datetime.date.today():
                return LicenseState(valid=False, reason=f"License key expired on {expires}")
        except ValueError:
            return LicenseState(valid=False, reason="License key has an invalid expiry date")
    return LicenseState(
        valid=True,
        tier="pro",
        licensee=str(payload.get("licensee") or "").strip(),
        expires=expires,
        reason="",
    )


_cache_lock = threading.Lock()
_cached: tuple[float, LicenseState] | None = None


def _file_mtime() -> float:
    try:
        return LICENSE_FILE.stat().st_mtime_ns
    except Exception:
        return 0.0


def load_license_state() -> LicenseState:
    """Resolve the current license (env key -> saved key -> community)."""
    global _cached
    env_key = os.getenv("ALMIGHTY_LICENSE_KEY", "").strip()
    if env_key:
        env_state = _validate_key(env_key)
        if env_state.valid:
            return env_state
        # An *invalid* env key must not silently downgrade a valid saved
        # license — fall through to config/license.json.
    with _cache_lock:
        mtime = _file_mtime()
        if _cached is not None and _cached[0] == mtime:
            return _cached[1]
        state = COMMUNITY
        try:
            saved = json.loads(LICENSE_FILE.read_text(encoding="utf-8"))
            key = str(saved.get("key") or "")
            if key:
                checked = _validate_key(key)
                state = checked if checked.valid else LicenseState(
                    valid=False, tier="community", reason=checked.reason
                )
        except Exception:
            state = COMMUNITY
        _cached = (mtime, state)
        return state


def is_pro() -> bool:
    """True when a valid Pro license is active (env key or saved key)."""
    return load_license_state().tier == "pro"


def activate_license(key: str) -> dict:
    """Validate and persist a license key. Returns {"ok", "message", ...}."""
    state = _validate_key(key)
    if not state.valid:
        return {"ok": False, "message": state.reason, "tier": "community"}
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        LICENSE_FILE.write_text(
            json.dumps({"key": (key or "").strip()}, indent=2), encoding="utf-8"
        )
    except Exception as exc:
        return {"ok": False, "message": f"Could not save license key: {exc}", "tier": "community"}
    with _cache_lock:
        global _cached
        _cached = (_file_mtime(), state)
    return {
        "ok": True,
        "message": f"Almighty Pro activated for {state.licensee or 'you'}.",
        "tier": "pro",
        "expires": state.expires,
    }


def deactivate_license() -> dict:
    """Remove the saved key and return to the Community Edition."""
    try:
        if LICENSE_FILE.exists():
            LICENSE_FILE.unlink()
    except Exception:
        pass
    with _cache_lock:
        global _cached
        _cached = (_file_mtime(), COMMUNITY)
    return {"ok": True, "message": "Almighty Pro deactivated — back to the Community Edition.", "tier": "community"}
