#!/usr/bin/env python3
"""Build + sign the Almighty AI update manifest (vendor-side tooling).

Computes the SHA-256 of a release archive, wraps it in a manifest, and signs
the manifest with the update-channel private key (config/update_private.pem,
gitignored — never commit or ship it). The output is the single file the app
fetches from the release channel; host it wherever ALMIGHTY_UPDATE_URL points.

Usage:
  python scripts/sign_update.py --archive build/almighty-1.1.0.zip \
      --url https://github.com/titechprabhasolutions/Brahma-AI---Lite/releases/download/v1.1.0/almighty-1.1.0.zip \
      --version 1.1.0 [--out almighty-update.signed.txt]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
PRIVATE_KEY_FILE = BASE_DIR / "config" / "update_private.pem"


def main() -> int:
    parser = argparse.ArgumentParser(description="Sign an Almighty AI update manifest")
    parser.add_argument("--archive", required=True, help="path to the release zip")
    parser.add_argument("--url", required=True, help="public download URL for the zip")
    parser.add_argument("--version", required=True, help="new app version, e.g. 1.1.0")
    parser.add_argument("--out", default="", help="write the signed manifest here (default: stdout)")
    parser.add_argument("--private", default=str(PRIVATE_KEY_FILE), help="private key PEM path")
    args = parser.parse_args()

    archive = Path(args.archive)
    if not archive.is_file():
        raise SystemExit(f"archive not found: {archive}")

    payload = {
        "version": args.version,
        "url": args.url,
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "published": date.today().isoformat(),
    }

    try:
        from updater import sign_manifest
        signed = sign_manifest(payload, Path(args.private).read_bytes())
    except Exception as exc:
        raise SystemExit(f"signing failed: {exc}")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(signed + "\n", encoding="utf-8")
        print(f"signed manifest written to {out}")
        print(f"payload: {json.dumps(payload, indent=2)}")
    else:
        print(signed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
