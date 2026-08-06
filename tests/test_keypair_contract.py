"""Keypair-contract pin for commercial licensing.

The app embeds a *public* key in config/profile.py and the vendor signs keys
with the matching *private* key in config/license_private.pem (gitignored).
These tests pin that contract: if you rotate the keypair with
``scripts/make_license.py --genkey`` and forget to update the embedded public
key, every issued key stops validating — this suite fails loudly instead of
silently breaking shipped builds.

Skipped automatically on machines without the private key (CI / fresh clones),
since the private key is never committed.
"""

import base64
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import profile  # noqa: E402

PRIVATE_KEY_FILE = ROOT / "config" / "license_private.pem"

pytestmark = pytest.mark.skipif(
    not PRIVATE_KEY_FILE.exists(),
    reason="vendor private key not present on this machine",
)


def _load_private_key():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    key = serialization.load_pem_private_key(PRIVATE_KEY_FILE.read_bytes(), password=None)
    assert isinstance(key, ed25519.Ed25519PrivateKey), "private key is not Ed25519"
    return key


def test_embedded_public_key_matches_private_key():
    """The public key baked into the app must be derived from the real key."""
    from cryptography.hazmat.primitives import serialization

    pub_pem = _load_private_key().public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    assert profile._LICENSE_PUBLIC_KEY_PEM.strip() == pub_pem.strip()


def test_key_issued_with_private_key_validates():
    """A key signed by the vendor private key passes the app validator."""
    priv = _load_private_key()
    payload = {"licensee": "Contract Test", "tier": "pro", "issued": "2026-08-05"}
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def _b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    key = f"{_b64(payload_bytes)}.{_b64(priv.sign(payload_bytes))}"
    state = profile._validate_key(key)
    assert state.valid is True
    assert state.tier == "pro"
    assert state.licensee == "Contract Test"
