#!/usr/bin/env bash
# scripts/install-hailort-priming.sh — install hailort-priming.service on the Pi 5.
#
# Why: h10-hailort 5.1.1 (Debian trixie RPi repo) ships no systemd unit to
# re-prime the NPU firmware at boot. The kernel driver loads the device
# but hailort can't soc_connect until a rmmod + modprobe cycle re-handshakes
# the firmware. This unit automates that handshake at every boot.
#
# Run on the Pi (star-server) as the star-server user with passwordless sudo:
#   bash scripts/install-hailort-priming.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
UNIT="hailort-priming.service"
GUARD="hailo-prime-check.sh"
SRC="$SCRIPT_DIR/$UNIT"
GUARD_SRC="$SCRIPT_DIR/$GUARD"
DEST="/etc/systemd/system/$UNIT"
GUARD_DEST="/usr/local/sbin/$GUARD"

if [[ ! -f "$SRC" ]]; then
  echo "✗ $SRC not found — run from the repo root" >&2
  exit 1
fi
if [[ ! -f "$GUARD_SRC" ]]; then
  echo "✗ $GUARD_SRC not found — run from the repo root" >&2
  exit 1
fi

# Validate the modprobe paths before we install the unit.
for bin in /sbin/modprobe; do
  if [[ ! -x "$bin" ]]; then
    echo "✗ $bin not executable — fix ExecStart* paths in $UNIT" >&2
    exit 1
  fi
done

sudo install -m 644 -o root -g root "$SRC" "$DEST"
sudo install -m 755 -o root -g root "$GUARD_SRC" "$GUARD_DEST"
sudo systemctl daemon-reload
sudo systemctl enable --now "$UNIT"
echo "✓ Installed guard + enabled $UNIT"

echo "--- status ---"
systemctl status "$UNIT" --no-pager | head -12

echo "--- NPU verify ---"
sleep 1
if command -v hailortcli >/dev/null; then
  hailortcli fw-control identify 2>&1 | grep -E "Firmware|Architecture|Board" | head -3
else
  echo "hailortcli not in PATH — install h10-hailort apt package"
fi
