# tests/test_vosk_stt.py
"""Tests for pi/vosk_stt.py — Vosk full speech-to-text fallback.

Covers the offline STT path that kicks in when Gemini Live is unreachable.
Uses the same fake-vosk pattern as test_wake_word_recovery.py so we can
test the recognizer wrapper without an 81 MB model on the dev box.
"""
import json
import sys
import types
from pathlib import Path

import pytest


# ── Fake vosk module (shared with wake_word_recovery tests) ────────────────


class _FakeModel:
    """Fake vosk.Model: succeeds only when the target dir looks valid."""

    def __init__(self, path):
        p = Path(path)
        if not p.is_dir() or not (p / "README").exists():
            raise ValueError("Failed to create a model")


class _FakeRecognizer:
    """Fake KaldiRecognizer that emits scripted partial + final results.

    Script is fed via a class-level list of (is_final, text) tuples.
    Each AcceptWaveform() call pops the next scripted line.
    """

    _script: list[tuple[bool, str]] = []
    _partial: str = ""

    def __init__(self, model, sample_rate, grammar=None):
        self._rate = sample_rate
        self._grammar = grammar
        self._idx = 0
        self._words = False

    def SetWords(self, value):
        self._words = value

    def SetMaxAlternatives(self, *_):
        pass

    def AcceptWaveform(self, data):
        if self._idx >= len(self._script):
            return False
        is_final, text = self._script[self._idx]
        self._idx += 1
        if not is_final:
            self._partial = text
            return False
        # final: clear partial, store as the next Result()
        self._last_final = text
        self._partial = ""
        return True

    def PartialResult(self):
        return json.dumps({"partial": self._partial})

    def Result(self):
        return json.dumps({"text": getattr(self, "_last_final", "")})

    def FinalResult(self):
        return self.Result()


def _install_fake_vosk(monkeypatch, script):
    """Install a fake vosk module with a scripted recognizer response."""
    _FakeRecognizer._script = script
    _FakeRecognizer._partial = ""

    vosk_mod = types.ModuleType("vosk")
    vosk_mod.Model = _FakeModel
    vosk_mod.KaldiRecognizer = _FakeRecognizer
    monkeypatch.setitem(sys.modules, "vosk", vosk_mod)


# ── Tests ──────────────────────────────────────────────────────────────────


def test_vosk_stt_available_when_model_present(tmp_path, monkeypatch):
    """VoskSTT.available is True when the model dir is valid."""
    model_dir = tmp_path / "vosk-model-small-en-us-0.15"
    model_dir.mkdir()
    (model_dir / "README").write_text("ok\n")

    _install_fake_vosk(monkeypatch, script=[])

    from pi.vosk_stt import VoskSTT

    stt = VoskSTT(model_dir=str(model_dir))
    assert stt.available is True


def test_vosk_stt_unavailable_when_model_missing(tmp_path, monkeypatch):
    """VoskSTT.available is False when model dir is absent — graceful no-op."""
    missing = tmp_path / "nope"
    _install_fake_vosk(monkeypatch, script=[])

    from pi.vosk_stt import VoskSTT

    stt = VoskSTT(model_dir=str(missing))
    assert stt.available is False


def test_vosk_stt_unavailable_when_vosk_import_fails(tmp_path, monkeypatch):
    """VoskSTT.available is False when the vosk module itself is absent."""
    model_dir = tmp_path / "vosk-model-small-en-us-0.15"
    model_dir.mkdir()
    (model_dir / "README").write_text("ok\n")

    # Simulate vosk not installed at all
    monkeypatch.setitem(sys.modules, "vosk", None)

    from pi.vosk_stt import VoskSTT

    stt = VoskSTT(model_dir=str(model_dir))
    assert stt.available is False


def test_vosk_stt_feed_returns_partial(monkeypatch):
    """feed() returns a partial transcript mid-utterance."""
    _install_fake_vosk(monkeypatch, script=[
        (False, "hello"),
    ])

    from pi.vosk_stt import VoskSTT

    stt = VoskSTT(model_dir=str(_fake_model_dir(monkeypatch)))
    stt.start()
    result = stt.feed(b"\x00\x01" * 640)
    assert result is not None
    assert result["type"] == "partial"
    assert result["text"] == "hello"


def test_vosk_stt_feed_returns_final(monkeypatch):
    """feed() returns a final transcript when utterance is complete."""
    _install_fake_vosk(monkeypatch, script=[
        (True, "turn on the lights"),
    ])

    from pi.vosk_stt import VoskSTT

    stt = VoskSTT(model_dir=str(_fake_model_dir(monkeypatch)))
    stt.start()
    result = stt.feed(b"\x00\x01" * 640)
    assert result is not None
    assert result["type"] == "final"
    assert result["text"] == "turn on the lights"


def test_vosk_stt_restart_clears_state(monkeypatch):
    """start() clears partials and resets recognizer for a fresh utterance."""
    _install_fake_vosk(monkeypatch, script=[
        (False, "first phrase"),
        (True, "second phrase"),
    ])

    from pi.vosk_stt import VoskSTT

    stt = VoskSTT(model_dir=str(_fake_model_dir(monkeypatch)))
    stt.start()
    r = stt.feed(b"\x00\x01" * 640)  # consumes first partial
    assert r["type"] == "partial"
    assert r["text"] == "first phrase"
    stt.start()                       # reset
    result = stt.feed(b"\x00\x01" * 640)  # consumes the final
    assert result is not None
    assert result["type"] == "final"
    assert result["text"] == "second phrase"


def test_vosk_stt_stop_drops_audio(monkeypatch):
    """After stop(), feed() returns None (audio is dropped)."""
    _install_fake_vosk(monkeypatch, script=[
        (False, "should not fire"),
    ])

    from pi.vosk_stt import VoskSTT

    stt = VoskSTT(model_dir=str(_fake_model_dir(monkeypatch)))
    stt.start()
    stt.stop()
    result = stt.feed(b"\x00\x01" * 640)
    assert result is None


def test_vosk_stt_feed_when_unavailable(monkeypatch):
    """feed() returns None when STT is unavailable — never raises."""
    from pi.vosk_stt import VoskSTT

    stt = VoskSTT(model_dir="/nonexistent/path")
    assert stt.available is False
    stt.start()
    assert stt.feed(b"\x00\x01" * 640) is None


def test_offline_stt(tmp_path, monkeypatch):
    """End-to-end: feed PCM audio, get partial then final transcript.

    This is the primary test the task specifies. Simulates the real
    offline STT fallback path: feed 16kHz mono int16 audio, receive
    partials for display and a final transcript for command dispatch.
    """
    model_dir = tmp_path / "vosk-model-small-en-us-0.15"
    model_dir.mkdir()
    (model_dir / "README").write_text("ok\n")

    _install_fake_vosk(monkeypatch, script=[
        (False, "hey"),
        (False, "hey rex"),
        (True, "hey rex turn on the lights"),
    ])

    from pi.vosk_stt import VoskSTT

    stt = VoskSTT(model_dir=str(model_dir))
    assert stt.available is True

    stt.start()

    # Feed a chunk of fake PCM (silence — content doesn't matter for fake recognizer)
    chunk = b"\x00\x00" * 800  # 800 samples = 50ms @ 16kHz

    r1 = stt.feed(chunk)
    assert r1 is not None
    assert r1["type"] == "partial"
    assert r1["text"] == "hey"

    r2 = stt.feed(chunk)
    assert r2 is not None
    assert r2["type"] == "partial"
    assert r2["text"] == "hey rex"

    r3 = stt.feed(chunk)
    assert r3 is not None
    assert r3["type"] == "final"
    assert r3["text"] == "hey rex turn on the lights"

    stt.stop()


# ── Helpers ────────────────────────────────────────────────────────────────


def _fake_model_dir(monkeypatch, name="vosk-model-small-en-us-0.15"):
    """Create a minimal valid-looking model dir and patch the path.

    We use a class-level tmp because VoskSTT resolves the path at
    construction time. For the scripted tests we don't care about
    the actual path — the fake Model only checks for README.
    """
    import tempfile
    d = Path(tempfile.mkdtemp()) / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "README").write_text("ok\n")
    return d