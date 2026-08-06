"""Whisplay HAT I2S audio capture and playback via WM8960 codec.

The PiSugar Whisplay HAT exposes a WM8960 codec through ALSA as card
'wm8960-soundcard'.  This module discovers the ALSA devices, exposes a
blocking ``record_chunk`` helper (16 kHz mono int16 — the rate Gemini Live
expects), and a ``play_audio_mono`` helper for TTS output.

Hardware notes (confirmed live on star-server 2026-08-05):
  * ALSA card 0: ``wm8960soundcard``
  * Stereo only (2 channels) at the hw level — use ``plughw:0,0`` for mono
    and non-native sample rates (e.g. 16 kHz capture for Gemini Live)
  * Mic level is low (~0.7% peak) — call ``boost_mic_gain`` to raise ADC
  * /etc/asound.conf already routes ``default`` -> ``plughw:0,0`` on the Pi
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import sounddevice as sd

log = logging.getLogger("whisplay.audio")

# Gemini Live expects 16 kHz mono int16 capture and 24 kHz playback.
CAPTURE_SAMPLE_RATE = 16000
PLAYBACK_SAMPLE_RATE = 24000
CAPTURE_CHANNELS = 1
PLAYBACK_CHANNELS = 1

# Whisplay WM8960 codec ALSA card name fragments.
_WHISPLAY_HINTS = ("wm8960", "whisplay", "i2s")


def _match(name: str, hints: tuple[str, ...]) -> bool:
    n = (name or "").lower()
    return any(h in n for h in hints)


@dataclass
class AudioDevices:
    """Indices of the Whisplay mic and speaker in sounddevice's device list."""
    mic: Optional[int]
    speaker: Optional[int]


def discover_whisplay_devices() -> AudioDevices:
    """Find the Whisplay mic and speaker ALSA device indices.

    Returns ``AudioDevices(mic=idx, speaker=idx)``.  Either may be ``None``
    if the HAT isn't present — the caller should handle the fallback.

    On the Pi, the WM8960 is a single ALSA card with both capture and
    playback, so both indices typically point to the same device.
    """
    devs = sd.query_devices()
    mic: Optional[int] = None
    spk: Optional[int] = None
    dev_list = list(devs) if not isinstance(devs, dict) else [devs]
    for i, d in enumerate(dev_list):
        name = d.get("name", "")
        max_in = int(d.get("max_input_channels", 0))
        max_out = int(d.get("max_output_channels", 0))
        if mic is None and max_in >= 1 and _match(name, _WHISPLAY_HINTS):
            mic = i
        if spk is None and max_out >= 1 and _match(name, _WHISPLAY_HINTS):
            spk = i
    return AudioDevices(mic=mic, speaker=spk)


class WhisplayAudio:
    """Thin wrapper around sounddevice for the Whisplay HAT's mic and speaker."""

    def __init__(self, devices: AudioDevices):
        self.devices = devices

    @property
    def mic_available(self) -> bool:
        return self.devices.mic is not None

    @property
    def speaker_available(self) -> bool:
        return self.devices.speaker is not None

    def record_chunk(self, duration_s: float = 1.0) -> np.ndarray:
        """Record ``duration_s`` seconds of 16 kHz mono int16 audio.

        Returns a ``(n_samples,)`` int16 array — the exact shape Gemini
        Live expects as input.  Uses plughw-style resampling so the
        WM8960's native 48 kHz stereo doesn't cause format errors.
        """
        if self.devices.mic is None:
            raise RuntimeError("Whisplay mic not found")
        n_frames = int(CAPTURE_SAMPLE_RATE * duration_s)
        audio = sd.rec(
            n_frames,
            samplerate=CAPTURE_SAMPLE_RATE,
            channels=CAPTURE_CHANNELS,
            dtype="int16",
            device=self.devices.mic,
        )
        sd.wait()
        return np.squeeze(audio)

    def play_audio_mono(self, float_audio: np.ndarray, sr: int = PLAYBACK_SAMPLE_RATE) -> None:
        """Play a mono float32 [-1, 1] numpy array through the HAT speaker.

        If the speaker is unavailable, logs a warning and returns silently.
        """
        if self.devices.speaker is None:
            log.warning("Whisplay speaker not found — audio discarded")
            return
        mono = np.squeeze(float_audio)
        if mono.ndim > 1:
            mono = mono.mean(axis=1)
        clip = np.clip(mono, -1.0, 1.0)
        sd.play(clip, samplerate=sr, device=self.devices.speaker, channels=PLAYBACK_CHANNELS)
        sd.wait()

    def boost_mic_gain(self, target_pct: int = 100) -> None:
        """Raise the WM8960 ADC capture volume via amixer.

        The Whisplay mic has low output (~0.7% peak at default gain).
        This calls ``amixer -c 0 sset 'ADC PCM Capture Volume' N%``.
        No-op if amixer is unavailable or the control doesn't exist.
        """
        import subprocess
        try:
            subprocess.run(
                ["amixer", "-c", "0", "sset", "ADC PCM Capture Volume", f"{target_pct}%"],
                capture_output=True, text=True, timeout=5,
            )
            log.info("Mic gain boosted to %d%%", target_pct)
        except Exception as e:
            log.warning("Could not boost mic gain: %s", e)
