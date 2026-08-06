#!/usr/bin/env bash
# scripts/install-pi-service.sh — install + enable brahma-pi.service
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
UNIT="brahma-pi.service"
DEST="$HOME/.config/systemd/user/$UNIT"

mkdir -p "$HOME/.config/systemd/user"
cp "$SCRIPT_DIR/$UNIT" "$DEST"
systemctl --user daemon-reload
systemctl --user enable --now "$UNIT"
echo "Brahma AI Pi service installed and started."
echo "Logs: journalctl --user -u $UNIT -f"
