#!/usr/bin/env bash
# scripts/setup-pi.sh — provision the Brahma AI Lite build on a Raspberry Pi 5.
#
# Run this on the Pi (ssh star-server@100.94.30.18) after cloning the repo.
# Installs python deps, Vosk wake-word model, and checks for Hailo NPU.
set -euo pipefail

if [[ "$(uname -m)" != arm* ]] && [[ "$(uname -m)" != aarch64 ]]; then
  echo "[setup-pi] Not on ARM — nothing to do. Exiting."
  exit 0
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "[setup-pi] === Brahma AI Lite Pi Setup ==="
echo "[setup-pi] Platform: $(uname -m), $(cat /etc/os-release | grep PRETTY | cut -d= -f2)"

# ── Virtualenv ────────────────────────────────────────────────────────────
# Reuse the Star AI venv if it exists, else create one.
VENV="/opt/star-ai/.venv"
if [[ -d "$VENV" ]]; then
  echo "[setup-pi] Reusing existing Star AI venv at $VENV"
else
  echo "[setup-pi] Creating virtualenv (.venv)..."
  python3 -m venv .venv --system-site-packages
  VENV="$ROOT/.venv"
fi

echo "[setup-pi] Installing Python requirements..."
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install -r requirements-pi.txt

# ── Vosk wake-word model ─────────────────────────────────────────────────
echo "[setup-pi] Ensuring Vosk wake-word model..."
if [[ ! -d config/models/vosk-model-small-en-us-0.15 ]]; then
  mkdir -p config/models
  (
    cd config/models
    curl -sSLO https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
    unzip -q vosk-model-small-en-us-0.15.zip
    # Keep the zip as a recovery source for wake_word.py self-heal.
    # If the extracted model dir gets corrupted on the Pi, the listener
    # can rebuild it from this sibling zip without re-downloading.
  )
  echo "[setup-pi] Vosk model downloaded (zip kept for self-heal)."
else
  echo "[setup-pi] Vosk model already present."
fi

# ── Hailo NPU check ───────────────────────────────────────────────────────
echo
echo "[setup-pi] === Hailo NPU Check ==="
if command -v hailortcli &>/dev/null; then
  if hailortcli scan 2>&1 | grep -q "Device:"; then
    echo "[setup-pi] ✓ Hailo NPU detected and operational."
    hailortcli fw-control identify 2>&1 | grep -E "Device Architecture|Firmware" || true
  else
    echo "[setup-pi] ✗ Hailo NPU not found. Check driver + reboot."
    echo "[setup-pi]   sudo modprobe hailo1x_pci && hailortcli scan"
  fi
else
  echo "[setup-pi] ✗ hailortcli not installed. Install hailort package."
fi

# ── Whisplay audio check ──────────────────────────────────────────────────
echo
echo "[setup-pi] === Whisplay Audio Check ==="
if aplay -l 2>&1 | grep -q wm8960; then
  echo "[setup-pi] ✓ WM8960 soundcard detected."
else
  echo "[setup-pi] ✗ WM8960 not found. Add dtoverlay=wm8960-soundcard to /boot/firmware/config.txt and reboot."
fi

echo
echo "[setup-pi] === Setup Complete ==="
echo "[setup-pi] Start with: $VENV/bin/python pi_main.py"
echo "[setup-pi] Or install systemd service: bash scripts/install-pi-service.sh"
