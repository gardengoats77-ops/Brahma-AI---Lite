# wake_word.py — on-device "Hey Rex" wake-word detection (Vosk keyword spotting).
#
# Why Vosk instead of the app's existing wake word: the built-in one is
# *cloud-based* — when the mic is muted the app still streams audio to Gemini
# and scans the transcription for "Almighty"/"hey"/"hi".  That leaks audio to
# the network while "muted".  This module detects the phrase entirely on the
# local machine with Vosk's keyword-spotting grammar, so mute can become a
# TRUE mute (nothing leaves the box) and "Hey Rex" still re-activates the mic.
#
# Design:
#   * No second PortAudio handle.  The app already opens a 16 kHz mono int16
#     InputStream for Gemini Live; WakeWordListener.feed(bytes) is called from
#     that SAME callback, so device conflicts are impossible.
#   * feed() is cheap (append to a bounded queue).  A dedicated daemon thread
#     drains the queue into Vosk's KaldiRecognizer.
#   * On a confirmed "hey rex", the on_trigger callback fires (the app unmutes
#     and writes a log line).
#   * Sensitivity maps to the Vosk keyword weight (grammar `"hey rex /1e-N"`).
#   * Missing vosk or model = graceful no-op: available() is False and feed()
#     simply drops the audio.
#
# Usage:
#   from wake_word import WakeWordListener
#   w = WakeWordListener(model_dir="config/models/vosk-model-small-en-us-0.15",
#                        on_trigger=lambda: ui.set_muted_state(False, wakeword=True))
#   w.set_enabled(True)
#   # ...from the mic callback:
#   w.feed(indata.tobytes())

from __future__ import annotations

import json
import logging
import queue
import threading
from pathlib import Path

log = logging.getLogger("wake_word")

# Vosk keyword weight exponent bounds.  LOWER = more sensitive (fires more
# easily, more false positives).  Default 1e-20 is a good middle ground.
_SENS_MIN_EXP = 30  # most sensitive  -> /1e-30
_SENS_MAX_EXP = 12  # least sensitive -> /1e-12
_DEFAULT_EXP   = 20

_PHRASE = "hey rex"


class WakeWordListener:
    """Detect a wake phrase from 16 kHz mono int16 audio chunks.

    Attributes:
        enabled:  whether the listener is active (feed() still safe when off).
        sensitivity: 0.0 (least) .. 1.0 (most); maps to the Vosk keyword weight.
        available:  True when vosk + a usable model directory were found.
    """

    # Voice-command presets ("Hey Rex, set wake sensitivity to high/low/medium").
    SENSITIVITY_PRESETS: "dict[str, float]" = {"high": 0.8, "medium": 0.5, "low": 0.2}

    def __init__(
        self,
        model_dir: str | Path,
        phrase: str = _PHRASE,
        on_trigger=None,
        sensitivity: float = 0.5,
    ):
        self._model_dir = Path(model_dir)
        self._phrase = (phrase or _PHRASE).strip().lower()
        self.on_trigger = on_trigger
        self._sensitivity = 0.5
        self._q: "queue.Queue[bytes]" = queue.Queue(maxsize=256)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._rec = None
        self._available = False
        self._enabled = False
        self._loaded_model = False
        self.set_sensitivity(sensitivity)
        self._load_model()

    # ── availability / lifecycle ──────────────────────────────────────────

    def _load_model(self) -> None:
        try:
            from vosk import KaldiRecognizer, Model

            if not self._model_dir.is_dir():
                log.warning("wake_word: model dir missing: %s", self._model_dir)
                self._recover_model_from_zip()
                if not self._model_dir.is_dir():
                    log.warning("wake_word: no usable model after recovery attempt")
                    return
            try:
                self._model = Model(str(self._model_dir))
            except Exception as exc:
                log.warning("wake_word: model load failed (%s) — attempting zip recovery", exc)
                if not self._recover_model_from_zip():
                    raise
                self._model = Model(str(self._model_dir))
            self._make_recognizer()
            self._available = True
            log.info("wake_word: model loaded from %s", self._model_dir)
        except Exception as exc:
            log.warning("wake_word: unavailable (%s)", exc)
            self._available = False

    # Vosk model download URL (small en-us 0.15, ~40 MB).
    _MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"

    def _recover_model_from_zip(self) -> bool:
        """Self-heal a corrupt/missing Vosk model from its sibling <name>.zip.

        Extracts to a temp sibling dir, verifies the fresh copy loads, then
        atomically swaps it into place. Returns True only when the swap
        succeeded and the new dir passes vosk.Model().

        If no local zip exists, attempts to download the model from
        alphacephei.com before extracting. No-op (False) when the download
        fails or the zip is itself broken — the caller degrades.
        """
        import shutil
        import tempfile
        import zipfile

        # The zip may sit beside the model as <dir>.zip, or under a generic
        # name (e.g. vosk-model.zip) in the same parent.
        zip_path: Path | None = None
        direct = Path(str(self._model_dir) + ".zip")
        if direct.is_file():
            zip_path = direct
        else:
            generic = self._model_dir.parent / "vosk-model.zip"
            if generic.is_file():
                zip_path = generic
        if zip_path is None:
            # Last resort: download the model zip from alphacephei.com.
            zip_path = self._download_model_zip()
            if zip_path is None:
                return False
        try:
            parent = self._model_dir.parent
            tmp_dir = Path(tempfile.mkdtemp(prefix="vosk-model-", dir=str(parent)))
            try:
                log.info("wake_word: extracting %s", zip_path)
                with zipfile.ZipFile(zip_path) as zf:
                    # Guard against zip-slip: only extract entries that stay
                    # inside the temp dir.
                    for member in zf.namelist():
                        target = (tmp_dir / member).resolve()
                        if not str(target).startswith(str(tmp_dir.resolve())):
                            raise RuntimeError(f"unsafe zip member: {member}")
                    zf.extractall(tmp_dir)

                # The zip normally contains the model as its own top-level
                # folder (e.g. vosk-model-small-en-us-0.15/) — pick it up.
                candidate = tmp_dir
                children = [p for p in tmp_dir.iterdir()]
                if len(children) == 1 and children[0].is_dir():
                    candidate = children[0]

                from vosk import Model  # verify before swapping
                Model(str(candidate))
            except Exception as exc:
                log.warning("wake_word: zip recovery failed validation: %s", exc)
                shutil.rmtree(tmp_dir, ignore_errors=True)
                return False

            # Swap: move broken dir aside, promote the fresh extraction.
            broken = self._model_dir.with_name(self._model_dir.name + ".bad")
            shutil.rmtree(broken, ignore_errors=True)
            try:
                if self._model_dir.exists():
                    self._model_dir.rename(broken)
                candidate.rename(self._model_dir)
            except Exception:
                # Promote failed — put the original back if it still exists.
                if broken.is_dir() and not self._model_dir.exists():
                    broken.rename(self._model_dir)
                raise
            shutil.rmtree(tmp_dir, ignore_errors=True)
            shutil.rmtree(broken, ignore_errors=True)
            log.info("wake_word: recovered model from %s", zip_path)
            return True
        except Exception as exc:
            log.warning("wake_word: zip recovery failed: %s", exc)
            return False

    def _download_model_zip(self) -> Path | None:
        """Download the Vosk model zip to a sibling of the model dir.

        Returns the path to the downloaded zip, or None on failure.
        The caller (_recover_model_from_zip) extracts and validates it.
        """
        import urllib.request

        target = Path(str(self._model_dir) + ".zip")
        try:
            self._model_dir.parent.mkdir(parents=True, exist_ok=True)
            log.info("wake_word: downloading model from %s", self._MODEL_URL)
            urllib.request.urlretrieve(self._MODEL_URL, str(target))
            if target.stat().st_size < 1_000_000:
                # Under 1 MB — almost certainly an error page, not a model.
                log.warning("wake_word: download too small (%d bytes), discarding",
                            target.stat().st_size)
                target.unlink(missing_ok=True)
                return None
            log.info("wake_word: model zip downloaded (%d bytes)", target.stat().st_size)
            return target
        except Exception as exc:
            log.warning("wake_word: model download failed: %s", exc)
            target.unlink(missing_ok=True)
            return None

    def _make_recognizer(self):
        from vosk import KaldiRecognizer

        weight = f"1e-{self._exp():d}"
        grammar = json.dumps([f"{self._phrase} /{weight}", "[unk]"])
        self._rec = KaldiRecognizer(self._model, 16000, grammar)
        self._rec.SetWords(False)

    def _exp(self) -> int:
        # sensitivity 0.0 -> 1e-12 (least), 1.0 -> 1e-30 (most)
        s = max(0.0, min(1.0, self._sensitivity))
        return int(round(_SENS_MAX_EXP + s * (_SENS_MIN_EXP - _SENS_MAX_EXP)))

    @property
    def available(self) -> bool:
        return self._available

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        """Start/stop the listener thread.  Safe to call repeatedly."""
        enabled = bool(enabled) and self._available
        with self._lock:
            if enabled == self._enabled:
                return
            self._enabled = enabled
        if enabled:
            self._start_thread()
        else:
            self._stop_thread()
            self._drain_queue()

    def set_sensitivity(self, value: float) -> None:
        """0.0 (least sensitive) .. 1.0 (most sensitive)."""
        value = max(0.0, min(1.0, float(value)))
        if value == self._sensitivity:
            return  # slider drags fire every tick; don't rebuild per tick
        self._sensitivity = value
        if self._available:
            try:
                self._make_recognizer()
            except Exception:
                pass

    # ── mic feed (called from the app's audio callback, must be cheap) ────

    def feed(self, data: bytes) -> None:
        if not self._enabled or not data:
            return
        try:
            self._q.put_nowait(bytes(data))
        except queue.Full:
            pass  # drop oldest-frame behavior: new audio is what matters

    # ── worker thread ─────────────────────────────────────────────────────

    def _start_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="WakeWordListener", daemon=True
        )
        self._thread.start()

    def _stop_thread(self) -> None:
        self._stop.set()
        t = self._thread
        self._thread = None
        if t is not None and t.is_alive():
            t.join(timeout=1.0)

    def _drain_queue(self) -> None:
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass

    def _run(self) -> None:
        while not self._stop.is_set():
            rec = self._rec  # re-read so sensitivity changes pick up live
            if rec is None:
                self._stop.wait(0.4)
                continue
            try:
                chunk = self._q.get(timeout=0.4)
            except queue.Empty:
                continue
            try:
                if rec.AcceptWaveform(chunk):
                    result = json.loads(rec.Result())
                    text = (result.get("text") or "").strip()
                    if self._phrase in text:
                        self._fire()
                # ignore partials — only confirmed utterances trigger
            except Exception as exc:
                log.debug("wake_word: recognizer error: %s", exc)

    def _fire(self) -> None:
        # Reset recognition state so a single "hey rex" fires once, then
        # tell the app to unmute.
        try:
            self._rec.Reset()
        except Exception:
            pass
        cb = self.on_trigger
        if cb is not None:
            try:
                cb()
            except Exception as exc:
                log.warning("wake_word: trigger callback failed: %s", exc)
