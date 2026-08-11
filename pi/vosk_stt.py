# pi/vosk_stt.py — Vosk full speech-to-text fallback.
#
# When Gemini Live is unreachable, this module provides offline STT using
# the same Vosk KaldiRecognizer that the wake-word listener uses. Instead
# of keyword-spotting grammar, we use full-grammar mode so Vosk returns
# the complete spoken phrase, not just the wake word.
#
# Design:
#   * Wraps vosk.KaldiRecognizer in continuous (non-keyword) mode.
#   * Same audio pipeline as wake_word.py: 16 kHz mono int16 PCM chunks.
#   * Returns dicts: {"type": "partial"|"final", "text": "..."}
#   * Graceful: if vosk or the model is missing, available() is False
#     and feed() returns None (audio dropped, no crash).
#
# Usage:
#   from pi.vosk_stt import VoskSTT
#   stt = VoskSTT(model_dir="config/models/vosk-model-small-en-us-0.15")
#   if stt.available:
#       stt.start()
#       result = stt.feed(pcm_bytes)  # None | {"type": ..., "text": ...}
#       if result and result["type"] == "final":
#           handle_command(result["text"])
#       stt.stop()

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger("vosk_stt")

# 16 kHz mono — matches the rest of the Pi audio pipeline.
SAMPLE_RATE = 16000
AUDIO_DEVICE = "plughw:0,0"


class VoskSTT:
    """Offline continuous speech-to-text via Vosk KaldiRecognizer.

    Attributes:
        available: True when vosk + a usable model were found.
        running: True when the recognizer is actively processing audio.
    """

    def __init__(
        self,
        model_dir: str | Path,
        sample_rate: int = SAMPLE_RATE,
    ):
        self._model_dir = Path(model_dir)
        self._sample_rate = sample_rate
        self._model = None
        self._rec = None
        self._available = False
        self._running = False
        self._load_model()

    # ── model lifecycle ──────────────────────────────────────────────────

    def _load_model(self) -> None:
        try:
            from vosk import KaldiRecognizer, Model

            if not self._model_dir.is_dir():
                log.warning("vosk_stt: model dir missing: %s", self._model_dir)
                return
            try:
                self._model = Model(str(self._model_dir))
            except Exception as exc:
                log.warning("vosk_stt: model load failed: %s", exc)
                return
            # No grammar arg = full recognition mode (not keyword spotting)
            self._rec = KaldiRecognizer(self._model, self._sample_rate)
            self._rec.SetWords(False)
            self._available = True
            log.info("vosk_stt: model loaded from %s", self._model_dir)
        except Exception as exc:
            log.warning("vosk_stt: unavailable (%s)", exc)
            self._available = False

    @property
    def available(self) -> bool:
        return self._available

    @property
    def running(self) -> bool:
        return self._running

    # ── public API ───────────────────────────────────────────────────────

    def start(self) -> None:
        """Prepare the recognizer for a fresh utterance. Safe to call repeatedly."""
        if not self._available:
            log.debug("vosk_stt: start() ignored — unavailable")
            return
        self._running = True
        # Reset recognizer so we start with a clean state
        try:
            self._rec.Reset()
        except Exception:
            pass

    def stop(self) -> None:
        """Stop the recognizer. Safe to call repeatedly."""
        self._running = False

    def feed(self, data: bytes) -> Optional[dict]:
        """Feed PCM audio and return a transcript chunk if available.

        Synchronous: calls KaldiRecognizer directly so the result is
        returned in the same call (no worker thread).

        Returns:
            None if no result yet, or STT is unavailable/stopped.
            {"type": "partial", "text": "..."} for mid-utterance.
            {"type": "final", "text": "..."} when utterance is complete.
        """
        if not self._available or not self._running or not data:
            return None
        try:
            if self._rec.AcceptWaveform(data):
                result = json.loads(self._rec.Result())
                text = (result.get("text") or "").strip()
                if text:
                    return {"type": "final", "text": text}
            else:
                partial = json.loads(self._rec.PartialResult())
                text = (partial.get("partial") or "").strip()
                if text:
                    return {"type": "partial", "text": text}
        except Exception as exc:
            log.debug("vosk_stt: recognizer error: %s", exc)
        return None