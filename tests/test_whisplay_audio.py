# tests/test_whisplay_audio.py
"""Tests for the Whisplay HAT I2S audio module.

The Whisplay HAT uses a WM8960 codec exposed as ALSA card 'wm8960soundcard'.
Tests mock sounddevice so they run without the HAT present.
"""
import numpy as np
from unittest import mock


def test_discover_whisplay_devices_finds_wm8960():
    """When a wm8960soundcard ALSA device exists, both mic and speaker should be found."""
    with mock.patch("sounddevice.query_devices") as qd:
        qd.return_value = [
            {"name": "wm8960soundcard", "max_input_channels": 2, "max_output_channels": 2},
            {"name": "HDMI", "max_input_channels": 0, "max_output_channels": 2},
        ]
        from pi.whisplay_audio import discover_whisplay_devices
        devs = discover_whisplay_devices()
        assert devs.mic is not None
        assert devs.speaker is not None


def test_discover_whisplay_devices_returns_none_when_absent():
    """When wm8960 card is missing, both should be None."""
    with mock.patch("sounddevice.query_devices") as qd:
        qd.return_value = [
            {"name": "HDMI", "max_input_channels": 0, "max_output_channels": 2},
        ]
        from pi.whisplay_audio import discover_whisplay_devices
        devs = discover_whisplay_devices()
        assert devs.mic is None
        assert devs.speaker is None


def test_record_chunk_returns_int16_mono():
    """record_chunk must return a 1-D int16 numpy array (Gemini Live input format)."""
    with mock.patch("sounddevice.query_devices") as qd, \
         mock.patch("sounddevice.rec") as rec, \
         mock.patch("sounddevice.wait"):
        qd.return_value = [{"name": "wm8960soundcard", "max_input_channels": 2, "max_output_channels": 2}]
        # Simulate 16000 samples (1 second at 16 kHz)
        fake_audio = np.zeros(16000, dtype=np.int16)
        rec.return_value = fake_audio
        from pi.whisplay_audio import WhisplayAudio, discover_whisplay_devices
        devs = discover_whisplay_devices()
        audio = WhisplayAudio(devs)
        chunk = audio.record_chunk(duration_s=1.0)
        assert chunk.dtype == np.int16
        assert chunk.ndim == 1  # mono (squeezed)
        assert len(chunk) == 16000


def test_play_audio_mono_does_not_crash_without_speaker():
    """When speaker is None, play_audio_mono should log and return without error."""
    with mock.patch("sounddevice.query_devices") as qd:
        qd.return_value = [{"name": "HDMI", "max_input_channels": 0, "max_output_channels": 2}]
        from pi.whisplay_audio import WhisplayAudio, discover_whisplay_devices
        devs = discover_whisplay_devices()
        audio = WhisplayAudio(devs)
        test_signal = np.zeros(24000, dtype=np.float32)
        audio.play_audio_mono(test_signal)  # must not raise
