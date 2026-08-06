#!/usr/bin/env python3
"""Issue and manage Almighty AI Pro license keys (vendor-side tooling).

The app only carries the *public* key (embedded in config/profile.py). This
script uses the matching *private* key — which lives in
``config/license_private.pem`` (gitignored) and must never be committed,
shipped, or embedded in the app. Anyone with the private key can issue keys.

Usage:
  python scripts/make_license.py --genkey                       # create keypair
  python scripts/make_license.py --issue "Acme Corp"            # 1-year Pro key
  python scripts/make_license.py --issue "Acme Corp" --days 30
  python scripts/make_license.py --issue "Acme Corp" --email billing@acme.com

Options:
  --genkey        Generate a fresh Ed25519 keypair, write the private key to
                  config/license_private.pem and print the public key (paste it
                  into the _LICENSE_PUBLIC_KEY_PEM constant in config/profile.py).
  --issue NAME    Sign a Pro license key for licensee NAME and print it.
  --days N        Validity period in days (default 365). Omit --days to make a
                  non-expiring key.
  --email E       Optional contact email stored in the payload.
  --private PATH  Path to the private key PEM (default config/license_private.pem).

Keys are single-line strings: ``base64url(payload).base64url(signature)``.
"""

from __future__ import annotations

import argparse
import base64
import datetime
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
PRIVATE_KEY_FILE = BASE_DIR / "config" / "license_private.pem"


def _load_private_key(path: Path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    pem = path.read_bytes()
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, ed25519.Ed25519PrivateKey):
        raise SystemExit(f"{path} is not an Ed25519 private key")
    return key


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def genkey() -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    priv = ed25519.Ed25519PrivateKey.generate()
    PRIVATE_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    PRIVATE_KEY_FILE.write_bytes(
        priv.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    pub = priv.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    print(f"Private key written to {PRIVATE_KEY_FILE}")
    print("\nPublic key — paste into _LICENSE_PUBLIC_KEY_PEM in config/profile.py:\n")
    print(pub.decode())


def issue(name: str, days: int | None, email: str, private_path: Path) -> str:
    key = _load_private_key(private_path)
    today = datetime.date.today()
    payload = {
        "licensee": name,
        "tier": "pro",
        "issued": today.isoformat(),
    }
    if email:
        payload["email"] = email
    if days is not None:
        if days <= 0:
            raise SystemExit("--days must be a positive integer")
        payload["expires"] = (today + datetime.timedelta(days=days)).isoformat()

    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = key.sign(payload_bytes)
    return f"{_b64(payload_bytes)}.{_b64(signature)}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Almighty AI Pro license tool")
    parser.add_argument("--genkey", action="store_true", help="generate a new keypair")
    parser.add_argument("--issue", metavar="NAME", help="issue a Pro key for this licensee")
    parser.add_argument("--days", type=int, default=None, help="validity in days (omit = never expires)")
    parser.add_argument("--email", default="", help="optional contact email")
    parser.add_argument("--private", default=str(PRIVATE_KEY_FILE), help="private key PEM path")
    args = parser.parse_args()

    if args.genkey:
        genkey()
        return 0
    if args.issue:
        print(issue(args.issue, args.days, args.email, Path(args.private)))
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
