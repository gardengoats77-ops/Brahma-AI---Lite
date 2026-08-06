# tests/test_whisplay_display.py
"""Tests for the Whisplay HAT SPI TFT display module.

The Whisplay HAT has a small SPI TFT (accessed via /dev/spidev0.0).
Tests verify the fail-safe behavior when luma.lcd is missing or the
SPI device doesn't exist — the display must never block the voice loop.
"""
import os
from unittest import mock


def test_display_does_not_crash_without_hardware():
    """When luma is missing or /dev/spidev is absent, display must be a no-op."""
    with mock.patch.dict("sys.modules", {"luma.lcd": None, "luma.core": None, "luma.oled": None}):
        from pi.whisplay_display import WhisplayDisplay
        d = WhisplayDisplay()
        assert d.available is False
        d.update("hello")  # must not raise
        d.clear()


def test_wrap_line_respects_width():
    """Long lines must be word-wrapped to fit within the width."""
    from pi.whisplay_display import _wrap_line
    lines = _wrap_line("The quick brown fox jumps over the lazy dog", width=16)
    assert all(len(l) <= 16 for l in lines)
    assert len(lines) >= 2


def test_wrap_line_handles_long_words():
    """Words longer than the width must be hard-split."""
    from pi.whisplay_display import _wrap_line
    lines = _wrap_line("supercalifragilisticexpialidocious", width=10)
    assert all(len(l) <= 10 for l in lines)
    assert len(lines) >= 4


def test_wrap_line_empty_string():
    from pi.whisplay_display import _wrap_line
    assert _wrap_line("") == [""]
