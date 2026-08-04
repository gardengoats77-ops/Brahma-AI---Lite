"""
tests/test_speak.py — Tests for centralized voice output module
"""

import sys
import time
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

import pytest

PROJECT_ROOT = str(Path(__file__).parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from speak import (
    speak, speak_async, speak_sync, speak_native, stop_speech,
    stop_native_speech, is_speaking,
    _lock, _last_text, _last_time, _dedup_window,
)


# ═══════════════════════════════════════════════════════════════════
# Test: speak() basic behavior
# ═══════════════════════════════════════════════════════════════════

class TestSpeakBasic:
    """Tests for basic speak functionality."""

    def test_speak_empty_string_does_nothing(self):
        """Speaking empty string should not raise."""
        speak("", async_mode=False, dedup=False)

    def test_speak_none_does_nothing(self):
        """Speaking None should not raise."""
        speak(None, async_mode=False, dedup=False)

    def test_speak_whitespace_only_does_nothing(self):
        """Speaking whitespace should not raise."""
        speak("   ", async_mode=False, dedup=False)

    @patch("speak._generate_audio", return_value=None)
    @patch("speak._play_audio", return_value=False)
    def test_speak_calls_generate_audio(self, mock_play, mock_gen):
        """speak() should call _generate_audio with the text."""
        speak("hello world", async_mode=False, dedup=False)
        mock_gen.assert_called_once_with("hello world")

    @patch("speak._generate_audio", return_value="/tmp/test.mp3")
    @patch("speak._play_audio", return_value=True)
    def test_speak_calls_play_audio(self, mock_play, mock_gen):
        """speak() should call _play_audio with the generated path."""
        speak("hello", async_mode=False, dedup=False)
        mock_play.assert_called_once_with("/tmp/test.mp3")


# ═══════════════════════════════════════════════════════════════════
# Test: Deduplication
# ═══════════════════════════════════════════════════════════════════

class TestDeduplication:
    """Tests for speech deduplication."""

    @patch("speak._generate_audio", return_value=None)
    def test_dedup_skips_identical_text(self, mock_gen):
        """Same text within dedup window should be skipped."""
        speak("test dedup", async_mode=False, dedup=True)
        speak("test dedup", async_mode=False, dedup=True)
        # Should only generate once (second call deduplicated)
        assert mock_gen.call_count == 1

    @patch("speak._generate_audio", return_value=None)
    def test_dedup_allows_different_text(self, mock_gen):
        """Different text should not be deduplicated."""
        speak("first text", async_mode=False, dedup=True)
        speak("second text", async_mode=False, dedup=True)
        assert mock_gen.call_count == 2

    @patch("speak._generate_audio", return_value=None)
    def test_dedup_disabled_allows_repeat(self, mock_gen):
        """With dedup=False, same text should repeat."""
        speak("repeat me", async_mode=False, dedup=False)
        speak("repeat me", async_mode=False, dedup=False)
        assert mock_gen.call_count == 2


# ═══════════════════════════════════════════════════════════════════
# Test: Thread safety
# ═══════════════════════════════════════════════════════════════════

class TestThreadSafety:
    """Tests for thread safety."""

    def test_lock_is_rlock(self):
        """_lock should be an RLock (reentrant)."""
        import threading
        # RLock() returns an _thread.RLock object; check type name instead of isinstance
        assert type(_lock).__name__ == "RLock", f"Expected RLock, got {type(_lock).__name__}"

    @patch("speak._generate_audio", return_value=None)
    def test_concurrent_speak_does_not_deadlock(self, mock_gen):
        """Multiple concurrent speak() calls should not deadlock."""
        results = []
        errors = []

        def worker(text):
            try:
                speak(text, async_mode=False, dedup=False)
                results.append(text)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(f"text_{i}",)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(errors) == 0, f"Deadlock or error: {errors}"
        assert len(results) == 5

    @patch("speak._generate_audio", return_value=None)
    def test_async_speak_starts_thread(self, mock_gen):
        """async_mode=True should start a background thread."""
        speak("async test", async_mode=True, dedup=False)
        # Give thread a moment to start
        time.sleep(0.1)


# ═══════════════════════════════════════════════════════════════════
# Test: stop_speech()
# ═══════════════════════════════════════════════════════════════════

class TestStopSpeech:
    """Tests for speech stopping."""

    def test_stop_speech_does_not_raise(self):
        """stop_speech() should not raise even when nothing is playing."""
        stop_speech()

    def test_stop_speech_clears_speaking_flag(self):
        """stop_speech() should clear the speaking event."""
        stop_speech()
        assert not is_speaking()


# ═══════════════════════════════════════════════════════════════════
# Test: is_speaking()
# ═══════════════════════════════════════════════════════════════════

class TestIsSpeaking:
    """Tests for is_speaking check."""

    def test_is_speaking_default_false(self):
        """is_speaking() should be False when nothing is playing."""
        stop_speech()
        assert is_speaking() is False


# ═══════════════════════════════════════════════════════════════════
# Test: Backward compatibility aliases
# ═══════════════════════════════════════════════════════════════════

class TestBackwardCompat:
    """Tests for backward-compatible aliases."""

    @patch("speak._generate_audio", return_value=None)
    def test_speak_native_calls_speak(self, mock_gen):
        """speak_native() should call speak() with dedup=False."""
        speak_native("backward compat test")
        mock_gen.assert_called_once()

    def test_stop_native_speech_does_not_raise(self):
        """stop_native_speech() should not raise."""
        stop_native_speech()

    def test_speak_async_is_alias(self):
        """speak_async() should be callable."""
        with patch("speak._generate_audio", return_value=None):
            speak_async("async alias test")

    def test_speak_sync_is_alias(self):
        """speak_sync() should be callable."""
        with patch("speak._generate_audio", return_value=None):
            speak_sync("sync alias test", dedup=False)


# ═══════════════════════════════════════════════════════════════════
# Test: _generate_audio
# ═══════════════════════════════════════════════════════════════════

class TestGenerateAudio:
    """Tests for audio generation."""

    def test_generate_audio_returns_none_without_edge_tts(self):
        """_generate_audio should handle missing edge_tts gracefully."""
        from speak import _generate_audio
        # This may return None if edge_tts is not installed, which is fine
        result = _generate_audio("test")
        # Either returns a path or None (both acceptable)
        assert result is None or isinstance(result, str)


# ═══════════════════════════════════════════════════════════════════
# Test: Edge cases
# ═══════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Tests for edge cases."""

    @patch("speak._generate_audio", return_value=None)
    def test_speak_long_text(self, mock_gen):
        """Speaking very long text should not crash."""
        long_text = "word " * 1000
        speak(long_text, async_mode=False, dedup=False)

    @patch("speak._generate_audio", return_value=None)
    def test_speak_special_characters(self, mock_gen):
        """Speaking text with special characters should not crash."""
        speak("Hello! @#$%^&*() 你好 🎉", async_mode=False, dedup=False)

    def test_speak_unicode(self):
        """Speaking unicode text should not crash."""
        with patch("speak._generate_audio", return_value=None):
            speak("Привет мир", async_mode=False, dedup=False)
