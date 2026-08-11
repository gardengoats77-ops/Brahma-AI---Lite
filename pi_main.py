#!/usr/bin/env python3
"""Brahma AI Lite — headless voice entrypoint for Raspberry Pi 5 +
Hailo-10H + PiSugar Whisplay HAT.

This is the Pi-side replacement for main.py (which boots the PyQt6
desktop UI). On the Pi we have no mouse and a tiny panel, so the whole
interaction model is voice-first:

  1. discover Whisplay audio devices (WM8960 I2S mic + speaker)
  2. boot the Whisplay TFT driver (SPI)
  3. (optional) initialize Hailo NPU for local LLM/vision fallback
  4. arm the Vosk wake-word listener on the HAT mic
  5. on wake: open Gemini Live, stream HAT mic -> Live, stream Live out
     -> Whisplay speaker, run action tools via the existing agent executor
  6. on disconnect/idle: return to wake-word armed state

The existing dashboard/server.py FastAPI service can be started in a
separate thread for mobile/remote access. On the Pi, the Star AI FastAPI
service (port 8787) is already running — we just add a /api/health route.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import TYPE_CHECKING, Optional

# Keep Windows-only imports stubbed on non-Windows platforms.
import linux_shim  # noqa: F401

from pi.platform import is_raspberry_pi
from pi.whisplay_audio import WhisplayAudio, discover_whisplay_devices
from pi.whisplay_display import WhisplayDisplay
from pi.whisplay_dashboard import get_dashboard
from pi.hailo_engine import HailoEngine
from pi import dispatch_memory
from pi import memory as conversation_memory

if TYPE_CHECKING:
    from wake_word import WakeWordListener

log = logging.getLogger("brahma.pi")

HEF_PATH = os.environ.get(
    "BRAHMA_HEF_PATH",
    "/usr/share/hailo-models/yolov8s_pose_h10.hef",
)
NPU_MODEL_NAME = os.environ.get("BRAHMA_NPU_MODEL", "yolov8s_pose")

# Gemini Live constants (mirrors main.py)
LIVE_MODEL = "models/gemini-2.5-flash-native-audio-preview-12-2025"
CHANNELS = 1
SEND_SAMPLE_RATE = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE = 1024
LIVE_CONNECT_TIMEOUT = 12

VOSK_MODEL_DIR = os.environ.get(
    "BRAHMA_VOSK_MODEL",
    str(Path(__file__).resolve().parent / "config" / "models" / "vosk-model-small-en-us-0.15"),
)


# ── Remote control tools (Pi -> desktop / fleet over SSH) ──────────────────
# The Pi's Live loop declares exactly three tools: fleet status, open an
# app, and a voice "check" helper. `remote_run` (free shell) stays behind
# REX_REMOTE_ALLOW_SHELL=1 and is only usable from local CLI, never from
# the voice surface.
#
# _wakeword_ref is populated by _voice_loop() after the Vosk listener boots,
# so _remote_execute can adjust sensitivity live without reopening audio.
_wakeword_ref: "dict[str, WakeWordListener | None]" = {"listener": None}
_tts_ref: "dict[str, object | None]" = {"tts": None}


def _remote_tool_declarations() -> list[dict]:
    return [
        {
            "name": "fleet_status",
            "description": (
                "List known devices (desktop, omnibook, tablet, ...) and whether "
                "each is reachable right now."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
        {
            "name": "fleet_open_app",
            "description": (
                "Open a well-known app on a device. device: 'desktop' or a "
                "configured fleet name; app: browser/terminal/files/settings/"
                "code or an http(s) URL."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "device": {"type": "string", "description": "device name"},
                    "app": {"type": "string", "description": "app key or URL"},
                },
                "required": ["device", "app"],
            },
        },
        {
            "name": "brain_dispatch",
            "description": (
                "Dispatch a natural-language command to the desktop's REX-OMEGA "
                "brain — a 45-agent orchestrator that can research, code, send "
                "files, control apps, etc. Use this for anything that needs "
                "semantic understanding or multi-step work on the desktop."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "what the brain should do"},
                },
                "required": ["prompt"],
            },
        },
        {
            "name": "set_wake_sensitivity",
            "description": (
                "Adjust the Hey Rex wake-word sensitivity live. "
                "Use a preset ('high', 'medium', 'low') or a raw float 0.0–1.0."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "value": {
                        "anyOf": [
                            {"type": "string", "enum": ["high", "medium", "low"]},
                            {"type": "number", "minimum": 0.0, "maximum": 1.0},
                        ],
                        "description": "sensitivity preset or raw float",
                    },
                },
                "required": ["value"],
            },
        },
        {
            "name": "recall_dispatch",
            "description": (
                "Recall a previous brain dispatch from history. "
                "Use 'last' for the most recent, 'last research' for the most "
                "recent research task, 'last from <agent>' for a specific agent, "
                "or a task_id for an exact match."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "recall query: 'last', 'last research', 'last from <agent>', or a task_id",
                    },
                },
                "required": ["query"],
            },
        },
    ]


async def _remote_execute(fc, tts=None) -> dict:
    """Run one remote tool call. Mirrors main.py's _execute_tool shape.

    If ``tts`` is provided, a brief voice confirmation is spoken after
    brain_dispatch or fleet_open_app completes.
    """
    import pi.remote_control as rc

    name = fc.name
    args = dict(fc.args or {})
    if name == "fleet_status":
        rows = []
        for st in rc.fleet_status():
            rows.append(f"{st['name']}: {'ok' if st['reachable'] else 'down'}")
        return {"result": "; ".join(rows) or "no devices"}
    if name == "fleet_open_app":
        dev = args.get("device", "")
        app = args.get("app", "browser")
        if not dev:
            return {"result": "error: no device given"}
        r = rc.open_app(dev, app)
        if r.get("ok"):
            msg = f"opened {app} on {dev}"
            if tts:
                tts.speak(msg)
            return {"result": msg}
        return {"result": f"failed to open {app} on {dev}: {r.get('stderr') or r.get('rc')}"}
    if name == "brain_dispatch":
        prompt = args.get("prompt", "")
        if not prompt:
            return {"result": "error: no prompt given"}
        # Streaming dispatch: speak partial results at natural breakpoints
        # so the user hears updates while the agent keeps working.
        full_text = []
        if tts and getattr(tts, "enabled", False):
            stream = rc.dispatch_stream(prompt)
            for sentence in rc.accumulate_and_speak(stream):
                full_text.append(sentence)
                tts.speak(sentence)
            result_msg = " ".join(full_text) if full_text else "dispatch complete"
            # Record the dispatch for later recall
            dispatch_memory.record_dispatch(
                task_id="stream-" + str(int(time.time())),
                assigned_agent="brain",
                prompt=prompt,
                result=result_msg,
            )
            return {"result": result_msg}
        # No TTS — fallback to non-streaming dispatch
        r = rc.dispatch(prompt)
        if r.get("ok"):
            agent = r.get("assigned_agent", "?")
            task = r.get("task_id", "?")
            msg = f"dispatched to {agent} (task {task})"
            # Record the dispatch for later recall
            dispatch_memory.record_dispatch(
                task_id=task,
                assigned_agent=agent,
                prompt=prompt,
                result=msg,
            )
            return {"result": msg}
        return {"result": f"dispatch failed: {r.get('error', 'unknown')}"}
    if name == "set_wake_sensitivity":
        from wake_word import WakeWordListener

        raw = args.get("value")
        if raw is None:
            return {"result": "error: no value given"}
        listener = _wakeword_ref.get("listener")
        if listener is None or not listener.available:
            return {"result": "error: wake-word listener not available"}
        presets = WakeWordListener.SENSITIVITY_PRESETS
        if isinstance(raw, str):
            if raw not in presets:
                return {"result": f"error: unknown preset '{raw}'; use high/medium/low"}
            value = presets[raw]
        else:
            try:
                value = float(raw)
            except (TypeError, ValueError):
                return {"result": f"error: invalid numeric value {raw!r}"}
        listener.set_sensitivity(value)
        return {"result": f"wake sensitivity set to {value:.2f} ({listener._exp():d})"}
    if name == "recall_dispatch":
        query = args.get("query", "last")
        entry = dispatch_memory.recall_dispatch(query)
        if entry is None:
            return {"result": f"no dispatch found for query: {query!r}"}
        # Format a concise voice-friendly summary
        agent = entry.get("assigned_agent", "?")
        task = entry.get("task_id", "?")
        prompt = entry.get("prompt", "")
        result = entry.get("result", "")
        # Truncate result for voice output
        if len(result) > 200:
            result = result[:200] + "..."
        return {
            "result": f"Task {task} was assigned to {agent}. Prompt: {prompt}. Result: {result}",
            "entry": entry,
        }
    return {"result": f"unknown tool {name}"}


@dataclass
class BootState:
    """Snapshot of hardware discovery results, reported via /api/health."""
    platform_ok: bool
    mic_available: bool
    speaker_available: bool
    display_available: bool
    hailo_available: bool

    @staticmethod
    def as_dict() -> dict:
        return asdict(_STATE)


_STATE = BootState(
    platform_ok=False,
    mic_available=False,
    speaker_available=False,
    display_available=False,
    hailo_available=False,
)


def build_health_payload() -> dict:
    """Single JSON snapshot used by /api/health on mobile dashboards."""
    payload = {**BootState.as_dict()}
    payload["status"] = "online" if payload.get("mic_available") else "degraded"
    payload["hardware"] = "Raspberry Pi 5 + Hailo-10H + Whisplay HAT"
    return payload


def _boot_hardware() -> tuple[Optional[WhisplayAudio], WhisplayDisplay]:
    """Discover and initialize Pi-side audio + display devices.

    Returns (audio, display). Audio may be None if HAT isn't present.
    """
    _STATE.platform_ok = is_raspberry_pi()

    # Audio
    audio: Optional[WhisplayAudio] = None
    try:
        devs = discover_whisplay_devices()
        if devs.mic is not None or devs.speaker is not None:
            audio = WhisplayAudio(devs)
            _STATE.mic_available = devs.mic is not None
            _STATE.speaker_available = devs.speaker is not None
            log.info("Audio: mic=%s speaker=%s", devs.mic, devs.speaker)
            # Boost mic gain — Whisplay WM8960 is quiet at default volume
            if audio.mic_available:
                audio.boost_mic_gain(target_pct=100)
    except Exception as e:  # noqa: BLE001
        log.error("audio discovery failed: %s", e)

    # Display
    display = WhisplayDisplay()
    _STATE.display_available = display.available
    display.update("Brahma AI Lite", "booting...")

    return audio, display


def _boot_npu() -> Optional[HailoEngine]:
    """Initialize the Hailo NPU engine."""
    eng = HailoEngine(hef_path=HEF_PATH, model_name=NPU_MODEL_NAME)
    _STATE.hailo_available = eng.available
    return eng


def _load_api_key() -> str:
    """Read the Gemini API key from the config file."""
    cfg = Path(__file__).resolve().parent / "config" / "api_keys.json"
    with open(cfg, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _load_system_prompt() -> str:
    """Load the Rex system prompt from core/prompt.txt."""
    p = Path(__file__).resolve().parent / "core" / "prompt.txt"
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return "You are Rex, a concise AI assistant for Brahma AI Lite on Raspberry Pi."


async def _voice_loop(
    audio: Optional[WhisplayAudio],
    display: WhisplayDisplay,
    npu: Optional[HailoEngine],
) -> None:
    """Wake-word -> Gemini Live -> TTS loop using Whisplay HAT hardware.

    Architecture (mirrors main.py AlmightyLive but headless):
      1. Continuously record 16 kHz mono int16 chunks from the HAT mic.
      2. Feed chunks to Vosk wake-word listener ("Hey Rex").
      3. On wake: open a Gemini Live session, stream mic -> Live,
         stream Live -> HAT speaker.
      4. On idle/timeout: close the Live session, re-arm wake word.
    """
    if audio is None or not audio.mic_available:
        log.error("No mic available — voice loop cannot run")
        display.update("No mic found", "error")
        return

    # ── Initialize Vosk wake-word listener ──────────────────────────────
    wakeword: Optional[object] = None
    wake_event = asyncio.Event()
    wakeword_triggered = {"flag": False}

    # ── Boot hydration: load recent conversation from prior session ────
    recent_turns = conversation_memory.hydrate_recent(limit=10)
    if recent_turns:
        log.info("Hydrating with %d recent conversation turns", len(recent_turns))
    else:
        log.info("No prior conversation to hydrate")

    try:
        from wake_word import WakeWordListener

        def _on_trigger():
            log.info("Wake word 'Hey Rex' detected!")
            wakeword_triggered["flag"] = True
            wake_event.set()

        wakeword = WakeWordListener(
            model_dir=VOSK_MODEL_DIR,
            on_trigger=_on_trigger,
            sensitivity=0.5,
        )
        wakeword.set_enabled(True)
        _wakeword_ref["listener"] = wakeword
        log.info("Vosk wake-word listener armed: %s", wakeword.available)
        display.update("Hey Rex", "listen")
    except Exception as e:
        log.warning("Wake word unavailable: %s — using push-to-talk", e)
        wakeword = None

    # ── Initialize TTS for voice confirmations ──────────────────────────
    from pi.tts import get_tts
    _tts_ref["tts"] = get_tts()
    log.info("VoiceConfirm TTS initialized (enabled=%s)", getattr(_tts_ref["tts"], "enabled", False))

    # ── Start Whisplay dashboard (wired to wake word for double-press toggle)
    dash = get_dashboard(
        on_push_to_talk=lambda: log.info("PTT pressed"),
        wake_listener=wakeword,
    )
    dash.set_voice_state("IDLE")
    if wakeword and wakeword.enabled:
        dash.set_voice_state("LISTENING")
    else:
        dash.set_muted(True)
    log.info("Whisplay dashboard started (available=%s)", dash.available)

    # ── Main loop: wake -> Live session -> idle -> re-arm ────────────────
    while True:
        # Wait for wake word (or timeout -> push-to-talk mode)
        wakeword_triggered["flag"] = False
        wake_event.clear()

        if wakeword and wakeword.available:
            # Continuously feed mic audio to Vosk while waiting for wake
            display.update("Say Hey Rex", "listen")
            log.info("Armed — listening for wake word")
            while not wakeword_triggered["flag"]:
                try:
                    chunk = audio.record_chunk(duration_s=0.5)
                    chunk_bytes = chunk.tobytes() if hasattr(chunk, "tobytes") else bytes(chunk)
                    wakeword.feed(chunk_bytes)
                except Exception as e:
                    log.debug("Wake record err: %s", e)
                    await asyncio.sleep(0.1)
        else:
            # No Vosk — push-to-talk: record 5s as a "wake" trigger
            display.update("Recording...", "push-to-talk")
            await asyncio.sleep(5)

        # ── Wake triggered — open Gemini Live session ───────────────────
        log.info("Wake detected — opening Gemini Live session")
        display.update("Connecting...", "wake")

        try:
            await _run_live_session(audio, display, npu)
        except Exception as e:
            log.error("Live session error: %s", e)
            display.update(f"Error: {str(e)[:40]}", "error")
            await asyncio.sleep(3)

        # Re-arm for next cycle
        display.update("Re-arming...", "idle")
        log.info("Session ended — re-arming wake word")


async def _run_live_session(
    audio: WhisplayAudio,
    display: WhisplayDisplay,
    npu: Optional[HailoEngine],
) -> None:
    """Connect to Gemini Live and stream audio through the HAT.

    Runs the same four-task pattern as AlmightyLive:
      _send_realtime → _listen_audio → _receive_audio → _play_audio
    """
    from google import genai
    from google.genai import types

    # Build the LiveConnectConfig
    sys_prompt = _load_system_prompt()
    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        output_audio_transcription={},
        input_audio_transcription={},
        system_instruction=sys_prompt,
        tools=[{"function_declarations": _remote_tool_declarations()}],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name="Charon"
                )
            )
        ),
    )

    client = genai.Client(
        api_key=_load_api_key(),
        http_options={"api_version": "v1beta"},
    )

    log.info("Connecting to Gemini Live: %s", LIVE_MODEL)
    display.update("Live session", "connected")

    connect_cm = client.aio.live.connect(model=LIVE_MODEL, config=config)
    session = await asyncio.wait_for(
        connect_cm.__aenter__(), timeout=LIVE_CONNECT_TIMEOUT
    )

    try:
        audio_in_queue: asyncio.Queue = asyncio.Queue()
        out_queue: asyncio.Queue = asyncio.Queue(maxsize=10)
        is_speaking = {"flag": False}
        idle_timer = {"last": time.monotonic()}
        IDLE_TIMEOUT = 30.0  # seconds of silence before closing session

        async def _send_realtime():
            """Send mic audio chunks to Gemini Live."""
            while True:
                msg = await out_queue.get()
                await session.send_realtime_input(media=msg)

        async def _listen_audio():
            """Record from HAT mic and feed to Live + wake-word listener."""
            import numpy as np
            log.info("Mic started")
            while True:
                try:
                    chunk = audio.record_chunk(duration_s=0.064)  # 64ms chunks
                    data = chunk.tobytes() if hasattr(chunk, "tobytes") else bytes(chunk)
                    # Don't send audio while Rex is speaking (echo suppression)
                    if not is_speaking["flag"]:
                        out_queue.put_nowait({"data": data, "mime_type": "audio/pcm"})
                        idle_timer["last"] = time.monotonic()
                except Exception as e:
                    log.debug("listen err: %s", e)
                    await asyncio.sleep(0.05)

        async def _receive_audio():
            """Receive audio responses from Gemini Live."""
            try:
                async for response in session.receive():
                    if response.data:
                        audio_in_queue.put_nowait(response.data)
                        is_speaking["flag"] = True
                        idle_timer["last"] = time.monotonic()

                    if response.server_content:
                        sc = response.server_content
                        if sc.output_transcription and sc.output_transcription.text:
                            txt = sc.output_transcription.text.strip()
                            if txt:
                                log.info("Rex: %s", txt)
                                display.update(txt[:60], "speaking")
                                conversation_memory.append_exchange("assistant", txt)
                        if sc.input_transcription and sc.input_transcription.text:
                            txt = sc.input_transcription.text.strip()
                            if txt:
                                log.info("You: %s", txt)
                                display.update(txt[:60], "listening")
                                conversation_memory.append_exchange("user", txt)
                        if sc.turn_complete:
                            is_speaking["flag"] = False

                    if response.tool_call:
                        fn_responses = []
                        for fc in response.tool_call.function_calls:
                            log.info("Remote tool call: %s", fc.name)
                            try:
                                fr = await _remote_execute(fc, tts=_tts_ref.get("tts"))
                                fn_responses.append(
                                    types.FunctionResponse(
                                        id=fc.id, name=fc.name, response=fr
                                    )
                                )
                            except Exception as e:  # noqa: BLE001
                                log.warning("remote tool error: %s", e)
                                fn_responses.append(
                                    types.FunctionResponse(
                                        id=fc.id, name=fc.name,
                                        response={"result": f"error: {e}"},
                                    )
                                )
                        await session.send_tool_response(
                            function_responses=fn_responses
                        )
            except Exception as e:
                log.error("recv error: %s", e)
                raise

        async def _play_audio():
            """Play received audio through the HAT speaker."""
            while True:
                chunk = await audio_in_queue.get()
                try:
                    import numpy as np
                    raw = np.frombuffer(chunk, dtype=np.int16)
                    if raw.size > 0:
                        float_audio = raw.astype(np.float32) / 32768.0
                        await asyncio.to_thread(audio.play_audio_mono, float_audio, RECEIVE_SAMPLE_RATE)
                except Exception as e:
                    log.debug("play err: %s", e)

        async def _idle_watchdog():
            """Close the session after IDLE_TIMEOUT seconds of silence."""
            while True:
                await asyncio.sleep(1.0)
                elapsed = time.monotonic() - idle_timer["last"]
                if elapsed > IDLE_TIMEOUT:
                    log.info("Idle timeout (%.0fs) — closing session", elapsed)
                    display.update("Session idle", "timeout")
                    # Cancel all tasks by raising
                    for task in live_tasks:
                        task.cancel()
                    return

        # Run all four tasks + idle watchdog
        async with asyncio.TaskGroup() as tg:
            live_tasks = [
                tg.create_task(_send_realtime()),
                tg.create_task(_listen_audio()),
                tg.create_task(_receive_audio()),
                tg.create_task(_play_audio()),
                tg.create_task(_idle_watchdog()),
            ]

    finally:
        try:
            await connect_cm.__aexit__(None, None, None)
        except Exception:
            pass
        display.update("Session done", "idle")


def main() -> int:
    """Boot the Brahma AI Lite headless voice loop."""
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(name)s %(levelname)s %(message)s",
    )
    log.info("Boot Brahma AI Lite (Pi 5 + Hailo + Whisplay)")

    audio, display = _boot_hardware()
    npu = _boot_npu()

    log.info("BootState=%s", BootState.as_dict())

    # Start the existing dashboard FastAPI in a background thread so
    # mobile devices on Tailscale can reach /api/health.
    # Uses uvicorn directly — dashboard.server may not have a helper.
    if os.environ.get("BRAHMA_PI_DASHBOARD") != "0":
        try:
            import threading
            import uvicorn

            def _run_dashboard():
                try:
                    uvicorn.run(
                        "dashboard.server:app",
                        host="0.0.0.0",
                        port=8000,
                        log_level="warning",
                    )
                except Exception as e:  # noqa: BLE001
                    log.warning("Dashboard thread failed: %s", e)

            t = threading.Thread(target=_run_dashboard, daemon=True)
            t.start()
            log.info("Dashboard FastAPI started in background on :8000")
        except Exception as e:  # noqa: BLE001
            log.warning("Dashboard start failed: %s", e)

    # Graceful shutdown
    def _shutdown(signum, frame):
        log.info("Shutdown signal received")
        display.clear()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        asyncio.run(_voice_loop(audio, display, npu))
    except KeyboardInterrupt:
        log.info("Shutdown requested")
        display.clear()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
