#!/usr/bin/env bash
# pi-logrotate-install.sh — Install almighty-logrotate.conf on the Pi.
#
# The logrotate rule rotates ~/Brahma-AI---Lite/AlmightyAI/startup.log
# (was 2.6MB growing unbounded; / was at 87% disk). Run as star-server
# with passwordless sudo.

set -euo pipefail

SRC="/tmp/almighty-logrotate.conf"
DST="/etc/logrotate.d/almighty"

if [[ ! -f "$SRC" ]]; then
  echo "✗ $SRC not found — scp it from local first:"
  echo "  scp scripts/almighty-logrotate.conf star-server@100.94.30.18:/tmp/"
  exit 1
fi

sudo install -m 644 -o root -g root "$SRC" "$DST"
echo "✓ Installed $DST"

echo "--- dry-run verify ---"
sudo logrotate -d /etc/logrotate.d/almighty 2>&1 | tail -10

echo "--- forced rotate now ---"
sudo logrotate -f /etc/logrotate.d/almighty 2>&1 || true

echo "Done. Disk pressure relieved; rule runs weekly or when log > 5M."
