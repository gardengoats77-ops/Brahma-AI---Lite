#!/usr/bin/env bash
# scripts/deploy-pi.sh — deploy AlmightyAI / Brahma-AI-Lite to the RPi 5 "star-server".
#
# Usage:
#   scripts/deploy-pi.sh            # sync code + restart service + verify health
#   scripts/deploy-pi.sh --pull      # git pull fork/main on the Pi, then restart
#   scripts/deploy-pi.sh --sync-only # rsync code only, no service restart
#
# Target: star-server@100.94.30.18 (Tailscale). Reads ~/.ssh/id_ed25519.
# Pi venv at /opt/star-ai/.venv. Service at ~/.config/systemd/user/brahma-pi.service
# (user-level systemd, runs pi_main.py).
#
# The Pi tracks fork/main (https://github.com/gardengoats77-ops/Brahma-AI---Lite).
# Runtime-only files (config/api_keys.json, memory/, AlmightyAI/startup.log) are
# NOT overwritten — they're gitignored and rsync-excluded.

set -euo pipefail

PI_HOST="${PI_HOST:-star-server@100.94.30.18}"
PI_KEY="${PI_KEY:-$HOME/.ssh/id_ed25519}"
SSH="ssh -i $PI_KEY -o ConnectTimeout=12 -o StrictHostKeyChecking=accept-new $PI_HOST"
SCP="scp -i $PI_KEY -o ConnectTimeout=12"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ACTION="--pull"
[[ $# -gt 0 ]] && ACTION="$1"

echo "=== Pre-flight: Tailscale reachability ==="
tailscale ping -c 1 --timeout 4s 100.94.30.18 2>&1 | tail -2

case "$ACTION" in
  --pull)
    echo
    echo "=== Pull fork/main on Pi ==="
    $SSH 'cd ~/Brahma-AI---Lite && git fetch fork && git reset --mixed fork/main' 2>&1 | tail -5
    ;;
  --sync-only)
    echo
    echo "=== rsync code-only (no git pull) ==="
    rsync -az --delete \
      --exclude='.git/' --exclude='.venv/' --exclude='venv/' \
      --exclude='__pycache__/' --exclude='.pytest_cache/' \
      --exclude='config/api_keys.json' --exclude='config/app_settings.json' \
      --exclude='memory/' --exclude='AlmightyAI/' --exclude='BrahmaAI/' \
      --exclude='.agents/' --exclude='.hermes/' --exclude='.jarvis-data/' \
      --exclude='config/models/' --exclude='.understand-anything/' \
      -e "ssh -i $PI_KEY" \
      "$ROOT/" "$PI_HOST:~/Brahma-AI---Lite/" 2>&1 | tail -5
    ;;
  *)
    echo "✗ Unknown action: $ACTION (use --pull or --sync-only)" >&2
    exit 1
    ;;
esac

if [[ "$ACTION" == "--sync-only" ]]; then
  echo
  echo "✓ Sync done (service NOT restarted per --sync-only)"
  exit 0
fi

echo
echo "=== Restart brahma-pi.service ==="
$SSH 'systemctl --user daemon-reload; systemctl --user restart brahma-pi; sleep 4; systemctl --user is-active brahma-pi' 2>&1 | tail -3

echo
echo "=== Health probe ==="
$SSH 'echo "PID: $(systemctl --user show brahma-pi -p MainPID --value)"; echo "Uptime: $(systemctl --user show brahma-pi -p ActiveEnterTimestamp --value)"; echo "Whisplay: $(journalctl --user -u brahma-pi --since "20 seconds ago" --no-pager 2>/dev/null | grep -i "whisplay" | head -1 | sed -e "s/.*INFO//")"; echo "Hailo: $(journalctl --user -u brahma-pi --since "20 seconds ago" --no-pager 2>/dev/null | grep -i "hailo" | head -1 | sed -e "s/.*\(INFO\|ERROR\)//")"' 2>&1 | head -8

echo
echo "=== Hailo NPU state ==="
$SSH 'hailortcli fw-control identify 2>&1 | grep -E "Firmware|Architecture" | head -2' 2>&1 | head -3

echo
echo "=== Disk + temp ==="
$SSH 'df -h / | tail -1; vcgencmd measure_temp 2>&1' 2>&1 | head -3

echo
echo "✓ Deploy complete."
