#!/usr/bin/env bash
# /usr/local/sbin/hailo-prime-check.sh — Hailo NPU firmware prime guard.
#
# Called once at boot by hailort-priming.service. If the NPU firmware
# handshake is already established, this is a no-op (exit 0). Only when
# the firmware is NOT reachable does it re-handshake the driver with a
# rmmod + modprobe cycle — that's exactly the soc_connect failure the
# h10-hailort 5.1.1 package leaves behind at boot (no hailort.service).
#
# Rationale for the guard: h10-hailort 5.1.1 ships no systemd unit to
# re-prime the NPU at boot. The kernel driver loads but the device cannot
# soc_connect until a rmmod + modprobe cycle re-handshakes the firmware.
# But blasting `modprobe -r` unconditionally breaks a healthy, consumer-held
# module (FATAL "module in use", or worse, kills an active brahma-pi
# consumer). So only re-prime when identify fails.

set -euo pipefail

LOG_TAG="hailo-prime"
identify_ok() {
  # Firmware handshake OK when hailortcli can identify the board.
  output=$(hailortcli fw-control identify 2>&1) || return 1
  if echo "$output" | grep -q "Firmware Version"; then
    return 0
  fi
  return 1
}

if identify_ok; then
  echo "$LOG_TAG: NPU firmware already healthy — no action needed"
  exit 0
fi

echo "$LOG_TAG: NPU firmware not reachable — re-handshaking driver"
if ! /sbin/modprobe -r hailo1x_pci 2>/dev/null; then
  echo "$LOG_TAG: modprobe -r failed (module in use?) — aborting re-prime" >&2
  exit 1
fi
sleep 1
/sbin/modprobe hailo1x_pci
sleep 1

if identify_ok; then
  echo "$LOG_TAG: re-prime OK — firmware reachable"
  exit 0
fi

echo "$LOG_TAG: re-prime FAILED — firmware still unreachable" >&2
exit 1