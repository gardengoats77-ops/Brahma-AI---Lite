-- 51-almighty-audio.lua — WirePlumber 0.4 override for Almighty AI.
--
-- On this board, the Realtek ALC897 analog output on "HDA Intel PCH"
-- (alsa_card.pci-0000_00_1f.3) is hidden by PipeWire jack detection: the
-- codec's jack sense reports the analog ports "not available", so the
-- analog sink is never created and any `pactl set-card-profile` attempt
-- silently fails.
--
-- Disabling ACP auto-profile / auto-port for this specific card stops
-- WirePlumber from re-evaluating the profile/port based on jack state, so
-- scripts/setup-audio.sh can explicitly activate the analog profile and
-- pin the lineout port (when speakers are physically wired) and otherwise
-- fall back to the HDMI sink.
--
-- Installed by scripts/setup-audio.sh to:
--   ~/.config/wireplumber/main.lua.d/51-almighty-audio.lua
-- Takes effect the next time wireplumber starts (i.e. at login).

rule = {
  matches = {
    {
      { "device.name", "matches", "alsa_card.pci-0000_00_1f.3" },
    },
  },
  apply_properties = {
    ["api.acp.auto-profile"] = false,
    ["api.acp.auto-port"] = false,
  },
}

table.insert(alsa_monitor.rules, rule)
