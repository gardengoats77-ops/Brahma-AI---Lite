"""edge-tts wrapper for voice confirmation on the Pi.

Non-blocking playback: speak() spins off a thread so the Gemini Live
loop continues without waiting for the audio pipeline.
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Optional

log = logging.getLogger("brahma.tts")

DEFAULT_VOICE = "en-US-AndrewNeural"
PLAYER_CMD = ["mpv", "--no-video", "--really-quiet"]


def _try_edge_tts(text: str, out_path: str, voice: str) -> bool:
    """Use edge-tts CLI to generate an MP3. Returns True on success."""
    import asyncio

    async def _gen():
        import edge_tts
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(out_path)

    try:
        asyncio.run(_gen())
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("edge-tts generation failed: %s", e)
        return False


class VoiceConfirm:
    """Edge-tts TTS wrapper with non-blocking threaded playback.

    ``speak()`` returns immediately after launching the worker thread.
    If edge-tts or mpv is unavailable, the call is a no-op.
    """

    def __init__(
        self,
        voice: str = DEFAULT_VOICE,
        player_cmd: list[str] | None = None,
        enabled: bool | None = None,
    ):
        self.voice = voice
        self.player_cmd = player_cmd or PLAYER_CMD
        # Auto-detect: if mpv is missing on this host, disable.
        if enabled is None:
            self.enabled = self._player_available()
        else:
            self.enabled = enabled
        log.info("VoiceConfirm enabled=%s", self.enabled)

    @staticmethod
    def _player_available() -> bool:
        """Check if mpv binary is in PATH."""
        try:
            r = subprocess.run(
                ["mpv", "--version"],
                capture_output=True,
                timeout=5,
            )
            return r.returncode == 0
        except Exception:
            return False

    def speak(self, text: str) -> None:
        """Speak `text` in a background thread. No-op if disabled."""
        if not self.enabled or not text:
            return
        threading.Thread(
            target=self._speak_worker,
            args=(text,),
            daemon=True,
            name="tts-voice-confirm",
        ).start()

    def _speak_worker(self, text: str) -> None:
        """Generate + play audio. Runs in a background thread."""
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".mp3", delete=False
            ) as tmp:
                tmp_path = tmp.name
            if _try_edge_tts(text, tmp_path, self.voice):
                subprocess.run(
                    [*self.player_cmd, tmp_path],
                    timeout=30,
                    capture_output=True,
                )
        except Exception as e:  # noqa: BLE001
            log.warning("TTS playback error: %s", e)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except Exception:  # noqa: BLE001
                    pass


# Module-level convenience instance.
_default: Optional[VoiceConfirm] = None


def get_tts() -> VoiceConfirm:
    """Return the module-level TTS singleton."""
    global _default
    if _default is None:
        _default = VoiceConfirm()
    return _default