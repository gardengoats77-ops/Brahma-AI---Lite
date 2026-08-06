#!/usr/bin/env bash
# pi-hailo-fix.sh — Diagnose and fix Hailo NPU on Raspberry Pi 5 + AI HAT 2.
#
# Run this directly on the Pi as the star-server user (passwordless sudo enabled).
# The NPU was enumerated on PCIe but hailortcli fw-control identify failed with
# HAILO_DRIVER_OPERATION_FAILED(36) Failed soc_connect. Root cause hypothesis:
# hailort.service is missing from systemd (h10-hailort apt package doesn't ship
# one). This script tries the likely fixes in order of increasing disruption.

set -euo pipefail

echo "=== [1/6] Hailo PCIe check ==="
lspci 2>/dev/null | grep -i hailo || echo "✗ Hailo not on PCIe bus — check HAT hardware seating + power"

echo
echo "=== [2/6] Kernel module + dmesg ==="
lsmod | grep hailo || echo "✗ hailo1x_pci not loaded; try: sudo modprobe hailo1x_pci"
echo "--- dmesg (last 20 hailo lines) ---"
sudo dmesg 2>&1 | grep -i hailo | tail -20

echo
echo "=== [3/6] hailortcli scan (pre-fix baseline) ==="
hailortcli scan 2>&1 | head -5

echo
echo "=== [4/6] Check for / try to enable hailort systemd service ==="
if sudo systemctl list-unit-files 2>/dev/null | grep -q hailort; then
  echo "✓ hailort.service exists in systemd — enabling + starting"
  sudo systemctl enable --now hailort
else
  echo "✗ hailort.service does NOT exist in systemd (typical of h10-hailort 5.1.1)"
  echo "  The h10-hailort apt package ships the runtime library but not a service"
  echo "  unit that primes the NPU firmware on boot. We will try a driver reload."
fi

echo
echo "=== [5/6] Driver reload (rmmod + modprobe, no reboot) ==="
# This forces the kernel to re-handshake with the NPU's firmware.
if lsmod | grep -q hailo1x_pci; then
  sudo rmmod hailo1x_pci 2>&1 || true
fi
sudo modprobe hailo1x_pci 2>&1 || true
sleep 2

echo
echo "=== [6/6] Post-fix verify ==="
echo "--- hailortcli scan ---"
hailortcli scan 2>&1 | head -5
echo "--- hailortcli fw-control identify ---"
if hailortcli fw-control identify 2>&1 | tee /tmp/hailo_identify.log | head -10; then
  echo "✓✓✓ Hailo NPU ALIVE — fw-control identify succeeded"
  echo "  Set BRAHMA_HEF_PATH in ~/Brahma-AI---Lite/config/api_keys.json"
  echo "  or as an env var in brahma-pi.service to enable local inference."
else
  echo "✗ fw-control identify STILL failing — escalate to reboot:"
  echo "  sudo reboot"
  echo "  After reboot, SSH back in and run:"
  echo "  hailortcli fw-control identify"
  echo "  If that STILL fails, check:"
  echo "  1. sudo dmesg | grep -A3 hailo  (PCIe link state)"
  echo "  2. sudo apt reinstall h10-hailort-pcie-driver  (driver/firmware mismatch)"
  echo "  3. Physical: reseat the AI HAT 2 on the PCIe connector (power off first)"
  echo "  4. Power: AI HAT 2 needs its own 5V/4A supply via the GPIO header —"
  echo "     verify the HAT is receiving adequate power (not just USB-Pi power)"
fi

echo
echo "=== Done. If hailo is alive, restart brahma-pi to pick up NPU: ==="
echo "  systemctl --user restart brahma-pi"
echo "  journalctl --user -u brahma-pi -f | grep -i hailo"
