from __future__ import annotations

import os
import platform
import subprocess
import sys
import time
from pathlib import Path

import psutil
from core.error_handler import log_error

from .styles import C, _base_dir

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"

# Lazy BUILD/BASE dir (defined below after imports of Path machinery).
BASE_DIR = Path(__file__).resolve().parent.parent

# Camera probe result cache: {ts, ok}
_CAM_OK_CACHE: dict = {"ts": 0.0, "ok": False}


# ── System Helpers ───────────────────────────────────────────────────────────

def _quiet_run(*args, **kwargs):
    if _OS == "Windows":
        kwargs.setdefault("creationflags", getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return subprocess.run(*args, **kwargs)


def _quote_cmd_arg(path: str) -> str:
    return f'"{path}"'


def _hidden_launch_args(*extra_args: str) -> list[str]:
    pythonw = Path(r"C:\Users\ravit\AppData\Local\Programs\Python\Python313\pythonw.exe")
    python = Path(sys.executable)
    main_py = BASE_DIR / "main.py"
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        return [str(exe), *extra_args]
    if pythonw.exists():
        return [str(pythonw), str(main_py), *extra_args]
    return [str(python), str(main_py), *extra_args]

def _startup_run_value() -> str:
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        return f'{_quote_cmd_arg(str(exe))} --startup'
    pythonw = Path(r"C:\Users\ravit\AppData\Local\Programs\Python\Python313\pythonw.exe")
    main_py = BASE_DIR / "main.py"
    if pythonw.exists():
        return f'{_quote_cmd_arg(str(pythonw))} {_quote_cmd_arg(str(main_py))} --startup'
    return f'{_quote_cmd_arg(sys.executable)} {_quote_cmd_arg(str(main_py))} --startup'


def _startup_registry_key():
    if platform.system() != "Windows":
        return None
    return r"Software\Microsoft\Windows\CurrentVersion\Run"


def _current_boot_stamp() -> int:
    try:
        return int(psutil.boot_time())
    except Exception:
        return int(time.time())


def _launched_from_windows_startup() -> bool:
    return any(str(arg).strip().lower() == "--startup" for arg in sys.argv[1:])


def _default_app_settings() -> dict:
    return {
        "startup_animation_enabled": True,
        "last_boot_stamp": 0,
        "boot_sequence_played": False,
        "show_workspace_on_startup": False,
        "launcher_pos": None,
        "launch_minimized": False,
        "check_updates_on_startup": True,
        "default_ai_provider": "Gemini",
        "auto_provider_switch": True,
        "attention_message_prompts": True,
        "attention_call_prompts": True,
        "developer_mode_enabled": False,
        "developer_mode_workspace": "",
        "speak_when_spoken_to": False,
    }


def _default_discord_settings() -> dict:
    return {
        "bot_token": "",
        "enabled": False,
        "channel_id": "",
    }



# ── Camera Detection ─────────────────────────────────────────────────────────

def _camera_available() -> bool:
    now = time.time()
    if now - _CAM_OK_CACHE["ts"] < 10.0:
        return bool(_CAM_OK_CACHE["ok"])

    ok = False
    cap = None
    try:
        import cv2  # optional dependency; used only for a quick camera probe

        indices = [0, 1, 2]
        if _OS == "Windows":
            for idx in indices:
                try:
                    cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
                    if cap.isOpened():
                        ret, frame = cap.read()
                        if ret and frame is not None:
                            ok = True
                            break
                finally:
                    if cap is not None:
                        cap.release()
                        cap = None
        else:
            cap = cv2.VideoCapture(0)
            if cap.isOpened():
                ret, frame = cap.read()
                ok = bool(ret and frame is not None)
    except Exception:
        ok = False
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception as _e:
                log_error(_e, context="ui", severity="debug")

    _CAM_OK_CACHE["ok"] = ok
    _CAM_OK_CACHE["ts"] = now
    return ok




# ── Network Label ────────────────────────────────────────────────────────────

def _active_net_label() -> str:
    try:
        stats = psutil.net_if_stats()
        active = []
        for name, info in stats.items():
            if getattr(info, "isup", False):
                active.append(name)
        if active:
            return active[0]
    except Exception as _e:
        log_error(_e, context="ui", severity="debug")
    return "No active adapter"

