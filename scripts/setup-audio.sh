#!/usr/bin/env bash
# scripts/setup-audio.sh — persist the Almighty AI audio fix across reboots.
#
# Problem (diagnosed on this machine):
#   * The only PipeWire sink by default is the NVidia HDMI output, which can
#     come up muted at low volume (29%) and may point at a monitor with no
#     speakers.
#   * The motherboard analog output (Realtek ALC897 on "HDA Intel PCH",
#     alsa_card.pci-0000_00_1f.3) is hidden by jack detection — the analog
#     ports report "not available", so PipeWire never creates the sink and
#     `pactl set-card-profile` silently fails.
#
# This script, run at login (optionally via the bundled systemd user unit):
#   1. installs a WirePlumber 0.4 Lua override (51-almighty-audio.lua) that
#      disables ACP auto-profile/auto-port for the PCH card;
#   2. force-enables the analog profile + lineout port (when the codec is
#      physically wired to speakers);
#   3. unmutes and raises the volume on EVERY sink — no silent output;
#   4. pins the default sink: analog first, then the preferred HDMI output;
#   5. `--install` also registers + enables a systemd user service so the
#      fix re-applies automatically at every login.
#
# Idempotent and safe to re-run.  No root required (user-scoped PipeWire +
# WirePlumber config, user systemd service).
#
# Usage:
#   scripts/setup-audio.sh            # apply the fix for this session
#   scripts/setup-audio.sh --install  # apply + install login service
#   scripts/setup-audio.sh --status   # show current cards/sinks/default
#   scripts/setup-audio.sh -h         # this help
#
# Environment overrides:
#   ALMIGHTY_AUDIO_VOLUME   target volume, default "85%"
#   ALMIGHTY_AUDIO_OUT      force a specific output sink substring, e.g. "analog"
#
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── config ──────────────────────────────────────────────────────────────
PCH_CARD="alsa_card.pci-0000_00_1f.3"        # HDA Intel PCH / Realtek ALC897
ANALOG_PROFILE="output:analog-stereo"
ANALOG_PROFILE_DUPLEX="output:analog-stereo+input:analog-stereo"
LINE_OUT_PORT="analog-output-lineout"
PREFERRED_HDMI="hdmi-stereo"                  # the Vizio on HDMI 0
VOLUME="${ALMIGHTY_AUDIO_VOLUME:-85%}"

WP_LUA_DST="$HOME/.config/wireplumber/main.lua.d"
WP_LUA_NAME="51-almighty-audio.lua"
WP_LUA_SRC="$SCRIPT_DIR/$WP_LUA_NAME"

SERVICE_NAME="almighty-audio.service"
SERVICE_SRC="$SCRIPT_DIR/$SERVICE_NAME"
SERVICE_DST="$HOME/.config/systemd/user/$SERVICE_NAME"

log()  { printf '\033[1;36m[audio]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[audio]\033[0m %s\n' "$*" >&2; }

need() { command -v "$1" >/dev/null 2>&1 || { warn "missing required tool: $1"; exit 1; }; }

# ── helpers ─────────────────────────────────────────────────────────────

pch_card_present() {
  pactl list cards short 2>/dev/null | awk '{print $2}' | grep -qx "$PCH_CARD"
}

best_sink() {
  # 1) user-forced substring
  if [ -n "${ALMIGHTY_AUDIO_OUT:-}" ]; then
    pactl list short sinks 2>/dev/null | awk '{print $2}' | grep "$ALMIGHTY_AUDIO_OUT" | head -1
    return
  fi
  # 2) analog (ALC897) if a sink actually exists
  local s
  s=$(pactl list short sinks 2>/dev/null | awk '{print $2}' | grep analog | head -1)
  [ -n "$s" ] && { echo "$s"; return; }
  # 3) preferred HDMI — the plain "hdmi-stereo" (Vizio) before any "-extra1"
  s=$(pactl list short sinks 2>/dev/null | awk '{print $2}' | grep '\.hdmi-stereo$' | head -1)
  [ -n "$s" ] && { echo "$s"; return; }
  s=$(pactl list short sinks 2>/dev/null | awk '{print $2}' | grep "$PREFERRED_HDMI" | head -1)
  [ -n "$s" ] && { echo "$s"; return; }
  # 4) any sink at all
  pactl list short sinks 2>/dev/null | awk '{print $2}' | head -1
}

force_analog() {
  local i sink
  for i in 1 2 3 4 5; do
    pactl set-card-profile "$PCH_CARD" "$ANALOG_PROFILE" 2>/dev/null \
      || pactl set-card-profile "$PCH_CARD" "$ANALOG_PROFILE_DUPLEX" 2>/dev/null
    sink=$(pactl list short sinks 2>/dev/null | awk '{print $2}' | grep analog | head -1)
    [ -n "$sink" ] && { echo "$sink"; return 0; }
    sleep 1
  done
  return 1
}

# ── actions ─────────────────────────────────────────────────────────────

apply() {
  need pactl

  # 1) Ensure the WirePlumber override is installed (takes effect at next
  #    wireplumber start, i.e. next login — harmless to re-copy).
  mkdir -p "$WP_LUA_DST"
  if [ -f "$WP_LUA_SRC" ]; then
    cp -f "$WP_LUA_SRC" "$WP_LUA_DST/$WP_LUA_NAME"
    log "wireplumber override installed: $WP_LUA_DST/$WP_LUA_NAME"
  else
    warn "override source missing ($WP_LUA_SRC) — continuing with sink fixes"
  fi

  # 2) Force-enable the ALC897 analog output (only matters if speakers are
  #    wired to the green jack; the override makes this possible).
  local analog=""
  if pch_card_present; then
    if analog=$(force_analog); then
      log "ALC897 analog enabled: $analog"
      pactl set-sink-port "$analog" "$LINE_OUT_PORT" 2>/dev/null \
        && log "lineout port selected on $analog"
    else
      warn "ALC897 analog still unavailable (jack sense says nothing wired); using HDMI fallback"
    fi
  else
    warn "PCH card not found; using HDMI fallback"
  fi

  # 3) Unmute + volume on every sink — no silent output after reboot.
  local s
  while read -r s; do
    [ -z "$s" ] && continue
    pactl set-sink-mute "$s" 0 2>/dev/null || true
    pactl set-sink-volume "$s" "$VOLUME" 2>/dev/null || true
  done < <(pactl list short sinks 2>/dev/null | awk '{print $2}')

  # 4) Pin the default sink (analog first, then preferred HDMI).
  local def
  def=$(best_sink)
  if [ -n "$def" ]; then
    pactl set-default-sink "$def" 2>/dev/null
    log "default sink -> $def @ $VOLUME (unmuted)"
  else
    warn "no sinks available at all — nothing to pin"
  fi
}

install_service() {
  need systemctl
  mkdir -p "$HOME/.local/bin" "$HOME/.config/systemd/user"
  cp -f "$SCRIPT_DIR/setup-audio.sh" "$HOME/.local/bin/almighty-setup-audio.sh"
  chmod +x "$HOME/.local/bin/almighty-setup-audio.sh"
  # Keep the WirePlumber override beside the installed copy so the login
  # run refreshes it too (SCRIPT_DIR-based lookup must find it).
  if [ -f "$WP_LUA_SRC" ]; then
    cp -f "$WP_LUA_SRC" "$HOME/.local/bin/$WP_LUA_NAME"
  fi
  if [ -f "$SERVICE_SRC" ]; then
    cp -f "$SERVICE_SRC" "$SERVICE_DST"
  else
    warn "service source missing ($SERVICE_SRC) — not installing the login service"
    return 1
  fi
  systemctl --user daemon-reload
  systemctl --user enable "$SERVICE_NAME" 2>/dev/null
  log "systemd user service installed + enabled: $SERVICE_NAME"
  log "  -> runs $HOME/.local/bin/almighty-setup-audio.sh at every login"
}

status() {
  need pactl
  echo "── cards ──"
  pactl list cards short 2>/dev/null | awk '{print "  " $2}'
  echo "── sinks ──"
  pactl list short sinks 2>/dev/null | awk '{print "  " $2}'
  echo "── default ──"
  pactl info 2>/dev/null | grep -i "default sink"
  echo "── mute / volume ──"
  pactl list sinks 2>/dev/null | grep -e 'Mute:' -e 'Volume: front' | head -8
}

usage() {
  sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'
}

# ── main ────────────────────────────────────────────────────────────────
case "${1:-apply}" in
  apply)    apply ;;
  --apply)  apply ;;
  --install) apply && install_service ;;
  --status) status ;;
  -h|--help) usage ;;
  *) warn "unknown argument: $1"; usage; exit 2 ;;
esac
