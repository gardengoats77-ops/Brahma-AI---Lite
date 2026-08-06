"""
Centralized Voice Output for REX
Single entry point for all speech across the codebase.
Handles threading, deduplication, queue management, and prevents overlapping audio.

Usage:
    from speak import speak, speak_async, stop_speech
from core.error_handler import log_error
"""

import os
import sys
import uuid
import tempfile
import threading
import time
from collections import deque
from typing import Optional

try:
    import ctypes
    from ctypes import wintypes
except ImportError:
    ctypes = None

# ── State ───────────────────────────────────────────────────────
_lock = threading.RLock()  # RLock allows reentrant acquisition (stop_speech called inside speak)
_current_player_alias: Optional[str] = None
_current_audio_path: Optional[str] = None
_last_text: str = ""
_last_time: float = 0.0
_dedup_window: float = 3.0  # seconds — suppress same text within this window
_queue: deque = deque(maxlen=5)
_speaking = threading.Event()


def is_speaking() -> bool:
    """Check if REX is currently speaking."""
    return _speaking.is_set()


def stop_speech() -> None:
    """Stop any currently playing audio immediately."""
    global _current_player_alias, _current_audio_path
    with _lock:
        if _current_player_alias and ctypes:
            try:
                ctypes.windll.winmm.mciSendStringW(
                    f"stop {_current_player_alias}", None, 0, None
                )
            except Exception as _e:
                log_error(_e, context="speak", severity="debug")
            try:
                ctypes.windll.winmm.mciSendStringW(
                    f"close {_current_player_alias}", None, 0, None
                )
            except Exception as _e:
                log_error(_e, context="speak", severity="debug")
        _current_player_alias = None

        if _current_audio_path:
            try:
                if os.path.exists(_current_audio_path):
                    os.remove(_current_audio_path)
            except Exception as _e:
                log_error(_e, context="speak", severity="debug")
            _current_audio_path = None

        _speaking.clear()


def _generate_audio(text: str) -> Optional[str]:
    """Generate speech audio file from text using Edge TTS."""
    try:
        import edge_tts
    except ImportError:
        print("[speak] edge_tts not installed. Run: pip install edge-tts")
        return None

    audio_path = os.path.join(tempfile.gettempdir(), f"rex_speak_{uuid.uuid4().hex}.mp3")
    try:
        communicator = edge_tts.Communicate(text, voice="en-US-GuyNeural")
        communicator.save_sync(audio_path)
        return audio_path
    except Exception as exc:
        print(f"[speak] TTS generation failed: {exc}")
        return None


def _play_audio(audio_path: str) -> bool:
    """Play audio file via Windows MCI. Returns True on success."""
    if not ctypes:
        return False

    player_alias = f"rex_audio_{uuid.uuid4().hex}"
    try:
        result = ctypes.windll.winmm.mciSendStringW(
            f'open "{audio_path}" type mpegvideo alias {player_alias}',
            None, 0, None
        )
        if result != 0:
            return False

        result = ctypes.windll.winmm.mciSendStringW(
            f"play {player_alias} wait", None, 0, None
        )
        return result == 0
    except Exception:
        return False


def _speak_worker(text: str) -> None:
    """Internal worker: generate + play audio for a single text."""
    global _current_player_alias, _current_audio_path

    _speaking.set()
    try:
        audio_path = _generate_audio(text)
        if not audio_path:
            return

        with _lock:
            _current_audio_path = audio_path

        success = _play_audio(audio_path)

        with _lock:
            _current_player_alias = None
            if _current_audio_path == audio_path:
                try:
                    if os.path.exists(audio_path):
                        os.remove(audio_path)
                except Exception as _e:
                    log_error(_e, context="speak", severity="debug")
                _current_audio_path = None
    finally:
        _speaking.clear()


def speak(text: str, async_mode: bool = True, dedup: bool = True) -> None:
    """
    Speak text aloud.

    Args:
        text: The text to speak
        async_mode: If True (default), runs in a background thread
        dedup: If True (default), suppresses duplicate text within _dedup_window seconds
    """
    global _last_text, _last_time

    text = (text or "").strip()
    if not text:
        return

    # Atomic: dedup check + stop + thread launch under single lock
    thread = None
    with _lock:
        # Deduplication: skip if same text spoken recently
        now = time.monotonic()
        if dedup and text == _last_text and (now - _last_time) < _dedup_window:
            return
        _last_text = text
        _last_time = now

        # Stop any current speech before starting new
        stop_speech()

        # Launch thread while still holding lock to prevent race
        if async_mode:
            thread = threading.Thread(
                target=_speak_worker,
                args=(text,),
                daemon=True,
                name="REX-Speak"
            )
            thread.start()
        else:
            # For sync mode, we must release lock before blocking
            pass

    if not async_mode:
        _speak_worker(text)


def speak_async(text: str, dedup: bool = True) -> None:
    """Alias for speak(text, async_mode=True)."""
    speak(text, async_mode=True, dedup=dedup)


def speak_sync(text: str, dedup: bool = True) -> None:
    """Speak synchronously (blocks until finished)."""
    speak(text, async_mode=False, dedup=dedup)


# ── Backward Compatibility ──────────────────────────────────────

def speak_native(text: str) -> None:
    """
    Backward-compatible alias matching the old attention_monitor.speak_native signature.
    New code should use speak() instead.
    """
    speak(text, async_mode=True, dedup=False)


def stop_native_speech() -> None:
    """Backward-compatible alias matching the old attention_monitor.stop_native_speech."""
    stop_speech()
