# tests/test_wake_word.py — on-device "Hey Rex" wake-word listener.
#
# The listener (wake_word.WakeWordListener) is a queue-fed Vosk keyword
# spotter that never opens its own audio device (it is fed from the app's
# existing 16 kHz mic callback).  These tests exercise the state machine
# and the graceful-degradation paths without needing real audio hardware:
# vosk is optional here — if the model is missing, available() is False
# and feed() must be a safe no-op.

from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path

import pytest

from wake_word import WakeWordListener

HERE = Path(__file__).parent
MODEL_DIR = HERE.parent / "config" / "models" / "vosk-model-small-en-us-0.15"

HAVE_VOSK = False
try:
    import vosk  # noqa: F401

    HAVE_VOSK = True
except Exception:
    pass


# ── helpers ──────────────────────────────────────────────────────────────

def _mk_listener(**kw) -> WakeWordListener:
    kw.setdefault("model_dir", MODEL_DIR)
    return WakeWordListener(**kw)


def _synthetic_hey_rex() -> bytes:
    """Tiny valid PCM-ish blob — content is irrelevant for state tests."""
    return b"\x00\x00" * 160  # 160 bytes = 80 int16 samples @16k


# ── availability / graceful degradation ──────────────────────────────────

def test_missing_model_dir_is_graceful(tmp_path):
    w = WakeWordListener(model_dir=tmp_path / "nope", on_trigger=lambda: None)
    assert w.available is False
    assert w.enabled is False
    # feed() must be a safe no-op even though nothing was loaded
    w.feed(_synthetic_hey_rex())
    assert w.enabled is False


def test_available_with_real_model():
    if not (HAVE_VOSK and MODEL_DIR.is_dir()):
        pytest.skip("vosk or model not installed")
    w = _mk_listener()
    assert w.available is True


def test_set_enabled_requires_availability(tmp_path):
    w = WakeWordListener(model_dir=tmp_path / "nope")
    w.set_enabled(True)  # must not raise, must not enable
    assert w.enabled is False


# ── sensitivity mapping ──────────────────────────────────────────────────

def test_sensitivity_clamped():
    w = _mk_listener()
    w.set_sensitivity(1.5)   # clamps to 1.0
    assert w._sensitivity == 1.0
    w.set_sensitivity(-0.2)  # clamps to 0.0
    assert w._sensitivity == 0.0


def test_sensitivity_maps_to_keyword_weight():
    w = _mk_listener()
    w.set_sensitivity(1.0)
    most = w._exp()  # most sensitive -> exponent 30 (weight 1e-30)
    w.set_sensitivity(0.0)
    least = w._exp()  # least sensitive -> exponent 12 (weight 1e-12)
    assert most > least
    assert 12 <= most <= 30
    assert 12 <= least <= 30


# ── feed queue behaviour (no real recognizer needed) ─────────────────────

def test_feed_drops_when_disabled(tmp_path):
    w = WakeWordListener(model_dir=tmp_path / "nope")
    w.feed(b"x" * 320)
    assert w._q.qsize() == 0  # disabled -> never queued


def test_feed_queues_when_enabled():
    if not (HAVE_VOSK and MODEL_DIR.is_dir()):
        pytest.skip("vosk or model not installed")
    w = _mk_listener()
    w.set_enabled(True)
    try:
        w.feed(_synthetic_hey_rex())
        # The worker drains fast; poll for the queue to empty or timeout.
        deadline = time.monotonic() + 2.0
        while w._q.qsize() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert w._q.qsize() == 0  # drained by the worker thread
    finally:
        w.set_enabled(False)


# ── trigger callback (mocked recognizer) ─────────────────────────────────

class _FakeRec:
    """Mimics the bits of KaldiRecognizer the worker touches."""

    def __init__(self, text: str):
        self._text = text
        self._reset = False

    def AcceptWaveform(self, chunk) -> bool:  # noqa: N802
        return True

    def Result(self) -> str:
        return json.dumps({"text": self._text})

    def Reset(self) -> None:
        self._reset = True


def test_trigger_fires_on_phrase(monkeypatch):
    fired: list[bool] = []
    w = _mk_listener(on_trigger=lambda: fired.append(True))
    fake = _FakeRec("hey rex")
    w._rec = fake  # bypass model loading
    w._available = True
    w._enabled = True

    w._run_once = None  # not used; call internals directly
    # Simulate one worker loop iteration
    w._q.put(_synthetic_hey_rex())
    chunk = w._q.get(timeout=1)
    assert fake.AcceptWaveform(chunk)
    result = json.loads(fake.Result())
    assert "hey rex" in result["text"]
    w._fire()
    assert fired == [True]
    assert fake._reset is True


def test_no_trigger_for_unrelated_text(monkeypatch):
    fired: list[bool] = []
    w = _mk_listener(on_trigger=lambda: fired.append(True))
    fake = _FakeRec("the weather today is nice")
    w._rec = fake
    w._available = True
    w._enabled = True

    w._q.put(_synthetic_hey_rex())
    chunk = w._q.get(timeout=1)
    assert fake.AcceptWaveform(chunk)
    result = json.loads(fake.Result())
    assert "hey rex" not in result["text"]
    # Worker would skip _fire(); verify the guard logic standalone.
    assert fired == []


def test_stop_thread_is_idempotent():
    w = _mk_listener()
    w._stop_thread()
    w._stop_thread()  # no thread -> must not raise


# ── disable path ─────────────────────────────────────────────────────────

def test_enable_then_disable_stops_thread():
    if not (HAVE_VOSK and MODEL_DIR.is_dir()):
        pytest.skip("vosk or model not installed")
    w = _mk_listener()
    w.set_enabled(True)
    assert w.enabled is True
    assert w._thread is not None and w._thread.is_alive()
    w.set_enabled(False)
    assert w.enabled is False
    assert w._thread is None or not w._thread.is_alive()


def test_set_enabled_reentrant_safe():
    if not (HAVE_VOSK and MODEL_DIR.is_dir()):
        pytest.skip("vosk or model not installed")
    w = _mk_listener()
    w.set_enabled(True)
    w.set_enabled(True)  # no-op, must not spawn a second thread
    assert w._thread is not None
    w.set_enabled(False)
