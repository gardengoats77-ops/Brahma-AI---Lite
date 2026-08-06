import asyncio
import threading
import json
import re
import shutil
import socket
import subprocess
import sys
import time
import random
import traceback
import os
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception as _e:
    print(f"WARN: stdout/stderr reconfigure failed: {_e}")

import sounddevice as sd
from google import genai
from google.genai import types
from ui import REXUI
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
    should_extract_memory, extract_memory
)

from actions.meeting_assistant import MeetingAssistant
from actions.website_builder   import website_builder
from actions.office_builder     import create_presentation, create_spreadsheet
from PyQt6.QtCore import QTimer
from actions.attention_monitor import AttentionMonitor, speak_native, stop_native_speech, handle_call_action, read_event_preview
from actions.daily_briefing import compile_daily_briefing
from actions.screen_processor import screen_process
from actions.send_message import send_message
from or_client import client as openrouter_client
from workspace_store import store as workspace_store
from smart_home.service import SmartHomeService
from plugin_manager import PluginManager
from core.error_handler import log_error, handle_errors, get_logger
from core.dispatcher import dispatcher
from core.action_registry import register_all_actions

try:
    from dashboard.server import DashboardServer
except Exception:
    DashboardServer = None


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"
STARTUP_LOG     = Path(os.environ.get("LOCALAPPDATA", str(BASE_DIR))) / "RexAI" / "startup.log"
LIVE_MODEL          = "models/gemini-2.5-flash-native-audio-preview-12-2025"
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024
LIVE_CONNECT_TIMEOUT = 12


def _device_rate() -> int:
    """Return the output device's native sample rate (e.g. 44100) so we can
    downsample/upsample around it. Falls back to 44100 on any probe error."""
    try:
        import sounddevice as _sd
        out = _sd.default.device
        idx = out[1] if isinstance(out, (tuple, list)) else out
        info = _sd.query_devices(idx, "output")
        return int(info.get("default_samplerate") or 44100)
    except Exception:
        return 44100


DEVICE_RATE = _device_rate()


def _resample_int16(data: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Linear-interp resample of int16 PCM to a target rate. Uses numpy."""
    import numpy as _np
    if src_rate == dst_rate:
        return data
    arr = _np.frombuffer(data, dtype=_np.int16).astype(_np.float32)
    src_len = arr.shape[0]
    dst_len = int(round(src_len * dst_rate / src_rate))
    x_old = _np.linspace(0.0, 1.0, src_len, dtype=_np.float32)
    x_new = _np.linspace(0.0, 1.0, dst_len, dtype=_np.float32)
    out = _np.interp(x_new, x_old, arr).astype(_np.int16)
    return out.tobytes()


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex((host, port)) == 0


def _startup_log(message: str) -> None:
    try:
        STARTUP_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(STARTUP_LOG, "a", encoding="utf-8") as f:
            f.write(message + "\n")
    except Exception as _e:
        log_error(_e, context="main._startup_log", severity="warning")


def _ensure_desktop_shortcut() -> None:
    if os.name != "nt":
        return

    marker_path = BASE_DIR / "config" / ".desktop_shortcut_created"
    if marker_path.exists():
        return

    try:
        import winreg
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders")
            desktop_raw, _ = winreg.QueryValueEx(key, "Desktop")
            winreg.CloseKey(key)
            desktop_dir = Path(os.path.expandvars(desktop_raw))
        except Exception:
            desktop_dir = Path(os.path.expanduser("~")) / "Desktop"
            
        desktop_dir.mkdir(parents=True, exist_ok=True)
        shortcut_path = desktop_dir / "REX.lnk"
        script_path = BASE_DIR / "main.py"
        icon_path = BASE_DIR / "assets" / "REX_Logo.ico"

        if not icon_path.exists():
            icon_path = None

        python_exe = sys.executable
        if not python_exe:
            python_exe = shutil.which("python") or shutil.which("py") or "python"

        shortcut_target = python_exe
        shortcut_args = f'"{script_path}"'
        if getattr(sys, "frozen", False):
            shortcut_target = python_exe
            shortcut_args = ""

        powershell_exe = shutil.which("powershell.exe") or shutil.which("powershell")
        if powershell_exe is None:
            raise RuntimeError("PowerShell is not available")

        def _ps_escape(value: str) -> str:
            return value.replace("'", "''")

        icon_value = str(icon_path) if icon_path and icon_path.exists() else ""
        ps1_path = BASE_DIR / "config" / "create_desktop_shortcut.ps1"
        ps1_script = "\n".join([
            "$WshShell = New-Object -ComObject WScript.Shell",
            f"$Shortcut = $WshShell.CreateShortcut('{_ps_escape(str(shortcut_path))}')",
            f"$Shortcut.TargetPath = '{_ps_escape(shortcut_target)}'",
            f"$Shortcut.Arguments = '{_ps_escape(shortcut_args)}'",
            f"$Shortcut.WorkingDirectory = '{_ps_escape(str(BASE_DIR))}'",
            "$Shortcut.WindowStyle = 7",
            "$Shortcut.Description = 'Launch REX'",
            f"if ('{_ps_escape(icon_value)}') {{ $Shortcut.IconLocation = '{_ps_escape(icon_value)},0' }}",
            "$Shortcut.Save()",
        ])
        ps1_path.write_text(ps1_script, encoding="utf-8")

        subprocess.run(
            [powershell_exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        marker_path.write_text("created", encoding="utf-8")
        _startup_log(f"desktop shortcut created at {shortcut_path}")
    except Exception as exc:
        _startup_log(f"desktop shortcut creation skipped: {exc}")


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are REX, a calm, direct, and professional AI assistant. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool. "
            "If the user asks to create, build, launch, or open a website, always use the selected workspace folder."
        )


def _speak_daily_briefing(ui=None) -> None:
    """Build and speak a fresh briefing after every application launch."""
    try:
        settings_path = BASE_DIR / "config" / "app_settings.json"
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except Exception:
            settings = {}
        briefing = compile_daily_briefing(settings)
        if briefing:
            print("[DailyBriefing] Speaking startup briefing")
            if ui is not None:
                ui.show_daily_briefing(briefing)
            speak_native(briefing)
            if ui is not None:
                ui.schedule_daily_briefing_hide()
    except Exception as exc:
        print(f"[DailyBriefing] Startup briefing failed: {exc}")
    
def _extract_gemini_text(response) -> str:
    text_parts: list[str] = []
    try:
        for candidate in getattr(response, "candidates", []) or []:
            content = getattr(candidate, "content", None)
            if not content:
                continue
            for part in getattr(content, "parts", []) or []:
                part_text = getattr(part, "text", None)
                if part_text:
                    text_parts.append(part_text)
    except Exception as _e:
        log_error(_e, context="main._extract_gemini_text", severity="warning")

    text = "".join(text_parts).strip()
    if text:
        return text

    try:
        return (getattr(response, "text", "") or "").strip()
    except Exception:
        return ""


def _gemini_text_reply(prompt: str) -> str:
    client = genai.Client(
        api_key=_get_api_key(),
        http_options={"api_version": "v1beta"},
    )
    system_prompt = (
        "You are REX, a concise, helpful desktop assistant. "
        "Reply naturally and briefly. Do not mention internal implementation details."
    )
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=f"{system_prompt}\n\nUser: {prompt}",
        config={"temperature": 0.6},
    )
    return _extract_gemini_text(response)


def _looks_like_code_request(text: str) -> bool:
    low = (text or "").lower()
    code_words = (
        "build", "create", "write", "implement", "code", "python", "app",
        "module", "function", "class", "project", "script", "api",
        "ui", "webpage", "bot", "server", "service"
    )
    return any(word in low for word in code_words)


def _looks_like_website_request(text: str) -> bool:
    low = (text or "").lower()
    website_words = (
        "website",
        "web site",
        "landing page",
        "homepage",
        "home page",
        "portfolio",
        "product site",
        "business site",
        "marketing site",
        "web app",
        "frontend",
        "site",
    )
    return any(word in low for word in website_words)


def _is_gemini_limit_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(token in msg for token in (
        "429",
        "resource_exhausted",
        "quota",
        "rate limit",
        "too many requests",
        "exceeded",
        "1008",
        "access denied",
        "permission denied",
    ))


def _dispatch_frequency_days(service: str) -> int | None:
    """Mirror of the dispatch PWA's getFrequencyDays()."""
    s = (service or "").lower()
    if "weekly" in s:
        return 7
    if "2 weeks" in s or "bi-week" in s or "biweekly" in s:
        return 14
    if "monthly" in s or "month " in s:
        return 30
    return None


def _dispatch_parse_job_date(value: str):
    """Mirror of the dispatch PWA's parseJobDate(). Returns a date or None."""
    import datetime as _dt
    if not value or value == "No jobs completed":
        return None
    m = re.search(r"([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})", str(value))
    if m:
        month_map = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
                     "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
        month = month_map.get(m.group(1).lower()[:3])
        if month:
            try:
                return _dt.date(int(m.group(3)), month, int(m.group(2)))
            except ValueError:
                return None
    try:
        return _dt.datetime.strptime(str(value).strip(), "%b %d, %Y").date()
    except ValueError:
        return None


def _dispatch_due_jobs(state: dict) -> list:
    """Return jobs that are overdue or due today, mirroring the PWA schedule logic."""
    import datetime as _dt
    today = _dt.date.today()
    due: list = []
    for job in (state.get("jobs") or []):
        freq = _dispatch_frequency_days(job.get("service") or "")
        if not freq:
            continue
        last_value = job.get("lastJob")
        if last_value == "No jobs completed":
            # Recurring service never serviced — first visit due now (mirrors the PWA)
            due.append(job)
            continue
        last = _dispatch_parse_job_date(last_value)
        if last is None:
            # Unparseable date — the PWA marks this 'unknown', not due; skip it
            continue
        next_due = last + _dt.timedelta(days=freq)
        days_overdue = 0
        while next_due < today:
            next_due += _dt.timedelta(days=freq)
            days_overdue += freq
        if days_overdue > 0 or (next_due - today).days == 0:
            due.append(job)
    return due


def _looks_like_screen_request(text: str) -> bool:
    t = (text or "").lower()
    if not t:
        return False
    direct_phrases = (
        "what's on my screen",
        "whats on my screen",
        "what is on my screen",
        "check my screen",
        "look at my screen",
        "analyze my screen",
        "analyse my screen",
        "tell me what's on my screen",
        "tell me what is on my screen",
        "read my screen",
        "what does my screen say",
    )
    if any(p in t for p in direct_phrases):
        return True
    screen_words = ("screen", "display", "monitor", "window")
    request_words = ("what", "check", "look", "analy", "analyse", "analyze", "read", "tell", "answer", "see")
    return any(sw in t for sw in screen_words) and any(rw in t for rw in request_words)


def _wakeword_detected(text: str) -> bool:
    t = re.sub(r"[^a-z0-9\s]+", " ", (text or "").lower())
    words = [w for w in t.split() if w]
    if not words:
        return False
    phrases = (
        "rex",
        "hey rex",
        "hi rex",
        "hello rex",
        "hey",
        "hi",
        "hello",
    )
    compact = " ".join(words)
    if compact in phrases or any(p in compact for p in phrases):
        return True
    return any(word in {"rex", "hey", "hi", "hello"} for word in words)


def _build_task_plan(text: str) -> list[str]:
    t = (text or "").lower()
    if any(word in t for word in ("presentation", "ppt", "slides", "deck")):
        return [
            "Understand the topic and goal",
            "Build a slide structure",
            "Generate and format the deck",
            "Open the finished presentation",
        ]
    if any(word in t for word in ("spreadsheet", "excel", "sheet", "table", "tracker", "budget")):
        return [
            "Read the data request",
            "Lay out sheets and columns",
            "Apply formulas and formatting",
            "Open the workbook",
        ]
    if any(word in t for word in ("word", "docx", "document", "report", "letter")):
        return [
            "Understand the document type",
            "Draft the structure and content",
            "Preserve formatting and polish",
            "Save the editable file",
        ]
    if any(word in t for word in ("website", "web site", "landing page", "saaS", "saas", "dashboard", "app")):
        return [
            "Interpret the brief",
            "Generate frontend and backend files",
            "Launch the local preview",
            "Debug and fix launch issues if needed",
        ]
    if any(word in t for word in ("browser", "website", "google", "search", "open url", "navigate")):
        return [
            "Open the browser",
            "Navigate to the target page",
            "Collect the needed information",
            "Return the result",
        ]
    if any(word in t for word in ("screen", "camera", "meeting", "call", "analyze", "analyse", "analyze")):
        return [
            "Capture the live screen or camera",
            "Inspect what is visible",
            "Answer with the important details",
            "Keep listening for follow-up commands",
        ]
    if any(word in t for word in ("fan", "light", "plug", "kasa", "atomberg", "smart home", "home device", "room", "bedroom", "living room", "kitchen", "office", "bathroom", "balcony")):
        return [
            "Identify the smart-home device or room",
            "Choose the correct action",
            "Send the command to the connected provider",
            "Confirm the result back to the user",
        ]
    return [
        "Understand the command",
        "Choose the right tool",
        "Execute the task",
        "Return the result",
    ]


_last_memory_input = ""

def _update_memory_async(user_text: str, rex_text: str) -> None:
    global _last_memory_input

    user_text   = (user_text   or "").strip()
    rex_text = (rex_text or "").strip()

    if len(user_text) < 5 or user_text == _last_memory_input:
        return
    _last_memory_input = user_text

    try:
        api_key = _get_api_key()
        if not should_extract_memory(user_text, rex_text, api_key):
            return
        data = extract_memory(user_text, rex_text, api_key)
        if data:
            update_memory(data)
            print(f"[Memory] ✅ {list(data.keys())}")
    except Exception as e:
        if "429" not in str(e):
            print(f"[Memory] ⚠️ {e}")

def _memory_context_for_request(text: str) -> str:
    try:
        return workspace_store().memory_context(text, limit=5)
    except Exception:
        return ""


# Register all actions with the central dispatcher
register_all_actions()
TOOL_DECLARATIONS = dispatcher.get_declarations()



class RexLive:

    def __init__(self, ui: REXUI, dashboard=None, dashboard_started: bool = False, enable_dashboard: bool = True):
        self.ui             = ui
        self._smart_home    = SmartHomeService()
        self.session        = None
        self.audio_in_queue = None
        self.out_queue      = None
        self._loop          = None
        self._is_speaking   = False
        self._speaking_lock = threading.Lock()
        self._use_openrouter_first = False
        self._pending_attention: dict | None = None
        self._pending_reply_event: dict | None = None
        self._reply_mode = False
        self._attention_lock = threading.Lock()
        self._attention_monitor = AttentionMonitor(on_event=self._on_external_notification)
        self._meeting_lock = threading.Lock()
        self._meeting_active = False
        self._meeting_event: dict | None = None
        self._meeting_assistant = MeetingAssistant(
            on_update=self._on_meeting_update,
            on_state=self._on_meeting_state,
        )
        self._phone_active = False
        self._dashboard = dashboard if dashboard is not None else (DashboardServer() if (enable_dashboard and DashboardServer is not None) else None)
        self._dashboard_started = bool(dashboard_started and self._dashboard is not None)
        self.ui.on_text_command = self._on_text_command
        self.ui.on_attention_action = self._on_attention_action
        self.ui.on_remote_clicked = self._make_remote_key
        self._last_activity = time.monotonic()
        self._idle_prompts = [
            "Hey, you there?",
            "Yo, get alive.",
            "How may I help, bro?",
            "Need anything?",
            "I'm here if you want me.",
        ]
        self._idle_speech_thread = threading.Thread(target=self._idle_speech_loop, daemon=True)
        self._idle_speech_thread.start()

    def _reset_idle_activity(self):
        self._last_activity = time.monotonic()

    def _should_announce_idle(self) -> bool:
        if self.ui.muted:
            return False
        if self._is_speaking:
            return False
        if self._meeting_active:
            return False
        if self._pending_attention:
            return False
        if time.monotonic() - self._last_activity < 240:
            return False
        return True

    def _idle_speech_loop(self):
        while True:
            time.sleep(random.uniform(240.0, 300.0))
            try:
                if self._should_announce_idle():
                    message = random.choice(self._idle_prompts)
                    self.ui.write_log(f"SYS: {message}")
                    threading.Thread(target=speak_native, args=(message,), daemon=True).start()
                    self._reset_idle_activity()
            except Exception as _e:
                log_error(_e, context="main._idle_speech_loop", severity="warning")

    def _make_remote_key(self):
        if self._dashboard is None:
            self.ui.write_log("ERR: Mobile Connect unavailable. Install fastapi, uvicorn, cryptography, and qrcode[pil].")
            return None
        key = self._dashboard.new_key()
        url = self._dashboard.get_url()
        manual = self._dashboard.get_manual_url()
        return url, key, f"{url}/auto-login?key={key}", manual

    def _on_phone_connected(self):
        try:
            self.ui.notify_phone_connected()
        except Exception as _e:
            log_error(_e, context="main._on_phone_connected", severity="warning")

    def _on_text_command(self, text: str, source: str = "local"):
        self._reset_idle_activity()
        text = (text or "").strip()
        if not text:
            return
        try:
            stop_native_speech()
        except Exception as _e:
            log_error(_e, context="main._on_text_command", severity="warning")
        # allow plugins to handle the incoming text command first
        try:
            pm = getattr(self, "plugin_manager", None)
            if pm is not None:
                handled = pm.dispatch("on_text_command", text, source)
                if handled:
                    return
        except Exception as _e:
            log_error(_e, context="main._on_text_command", severity="warning")
        if self._reply_mode:
            if self._handle_pending_reply(text):
                return
            # Still in reply mode but no pending reply event means reset and continue
            self._reply_mode = False

        try:
            from smart_home.smart_device_manager import SmartDeviceManager
            sd_mgr = SmartDeviceManager()
            devices = self._smart_home.list_devices()
            routed_text_home = sd_mgr.route_command(text, devices)
            if routed_text_home != text:
                print(f"[REX] Redirection: '{text}' -> '{routed_text_home}'")
                text = routed_text_home
        except Exception as e:
            print(f"[REX] Redirection error: {e}")

        # Dispatch shortcut first: the website-request matcher below would otherwise
        # hijack "open the dispatch app" (it matches the word "app").
        if self._handle_dispatch_command(text, source=source or "local"):
            return

        developer_settings = self.ui._load_app_settings() if hasattr(self.ui, "_load_app_settings") else {}
        developer_enabled = bool(developer_settings.get("developer_mode_enabled", False))
        developer_workspace = str(developer_settings.get("developer_mode_workspace", "")).strip()
        website_request = _looks_like_website_request(text)
        if website_request and not (developer_enabled and developer_workspace):
            message = "Website builds need developer mode enabled and a workspace folder selected first."
            self.ui.write_log(f"ERR: {message}")
            self.speak(message)
            return

        if website_request and developer_enabled and developer_workspace:
            try:
                if hasattr(self.ui, "_developer_status_lbl"):
                    self.ui._developer_status_lbl.setText("Building website with Gemini in the selected workspace")
                    self.ui._developer_card.show()
                    self.ui._developer_card.raise_()
                result = website_builder(
                    parameters={
                        "action": "create",
                        "description": text,
                        "title": text,
                        "output_dir": developer_workspace,
                        "auto_open": True,
                    },
                    player=self.ui,
                )
                self.ui.write_log(f"[WebsiteBuilder] {result[:400]}")
                self.speak(result[:800])
                return
            except Exception as exc:
                self.ui.write_log(f"ERR: Website build failed: {exc}")

        memory_ctx = _memory_context_for_request(text)
        routed_text = f"{memory_ctx}\n\nCurrent User Request:\n{text}" if memory_ctx else text
        if text.lower() in {"stop meeting mode", "end meeting mode", "close meeting mode"}:
            self._stop_meeting_mode("Meeting mode closed.")
            return
        if self._handle_attention_response(text):
            return
        try:
            self.ui.begin_task_workspace(text, _build_task_plan(text), source=source or "local")
        except Exception as _e:
            log_error(_e, context="main._on_text_command", severity="warning")
        if self._handle_smart_home_command(text, source=source or "local"):
            return
        if _looks_like_screen_request(text):
            try:
                self.ui.update_task_workspace(
                    status="Scanning screen",
                    output="REX is inspecting the screen for what you asked about.",
                    percent=40,
                )
            except Exception as _e:
                log_error(_e, context="main._on_text_command", severity="warning")
            threading.Thread(
                target=screen_process,
                kwargs={
                    "parameters": {"angle": "screen", "text": text},
                    "response": None,
                    "player": self.ui,
                    "session_memory": None,
                },
                daemon=True,
            ).start()
            return
        if self._use_openrouter_first or not self._loop or not self.session:
            threading.Thread(target=self._fallback_reply, args=(text, memory_ctx), daemon=True).start()
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": routed_text}]},
                turn_complete=True
            ),
            self._loop
        )


    def _handle_smart_home_command(self, text: str, source: str = "local") -> bool:
        normalized = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s%]", " ", text.lower())).strip()
        smart_home_words = ("fan", "light", "lights", "plug", "switch", "kasa", "atomberg", "room", "bedroom", "living room", "kitchen", "office", "balcony", "bathroom")
        action_words = ("turn on", "turn off", "switch on", "switch off", "power on", "power off", "set", "speed", "brightness", "restart", "reboot", "toggle")
        if not any(word in normalized for word in smart_home_words) and not any(word in normalized for word in action_words):
            return False
        try:
            result = self._smart_home.execute_command(text)
            detail = str(result.get("detail") or "Smart-home command completed.")
            title = f"Smart Home: {result.get('action', 'control')}"
            plan = [
                "Identify the target device or room",
                "Send the command to the smart-home provider",
                "Verify the new state",
                "Report the result",
            ]
            self.ui.update_task_workspace(
                title=title,
                command=text,
                plan=plan,
                status="Executing smart-home command",
                output=detail,
                percent=100,
                source=source,
            )
            self.ui.write_log(f"REX: {detail}")
            self.speak(detail)
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return True
        except Exception as exc:
            message = f"I couldn't control the smart home device: {exc}"
            self.ui.write_log(f"ERR: {message}")
            self.ui.update_task_workspace(
                title="Smart Home Control",
                command=text,
                plan=[
                    "Identify the target device or room",
                    "Send the command to the smart-home provider",
                    "Verify the new state",
                ],
                status="Smart-home command failed",
                output=message,
                percent=100,
                source=source,
            )
            self.speak(message)
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return True

    def _handle_dispatch_command(self, text: str, source: str = "local") -> bool:
        """Voice/typing shortcut: "open dispatch" / "start my route".

        Opens the Garden Goats Dispatch PWA in the browser and announces
        today's due jobs from the server-synced state file.
        """
        normalized = re.sub(r"\s+", " ", text.lower()).strip()
        triggers = (
            "open dispatch", "launch dispatch", "start my route",
            "start the route", "open the dispatch", "show dispatch",
            "dispatch app", "go to dispatch", "garden goats",
        )
        if not any(t in normalized for t in triggers):
            return False
        try:
            import webbrowser
            # Reuse the dashboard's LAN URL so the browser hits the same origin
            # (phone and desktop both get the PIN gate + sync endpoints).
            url = "http://localhost:8000/dispatch"
            if getattr(self, "_dashboard", None) is not None:
                try:
                    url = self._dashboard.get_url() + "/dispatch"
                except Exception:
                    pass
            webbrowser.open(url)

            # Read the synced dispatch state (REX data/dispatch_state.json) and
            # announce anything due today.
            due = []
            try:
                from dashboard.server import DISPATCH_STATE_FILE
                if DISPATCH_STATE_FILE.exists():
                    state = json.loads(DISPATCH_STATE_FILE.read_text(encoding="utf-8"))
                    due = _dispatch_due_jobs(state)
            except Exception as _e:
                log_error(_e, context="main._handle_dispatch_command", severity="warning")

            if due:
                names = ", ".join(
                    (j.get("address") or "a property") for j in due[:4]
                )
                more = f" and {len(due) - 4} more" if len(due) > 4 else ""
                message = f"Opening the dispatch app. You have {len(due)} job{'s' if len(due) != 1 else ''} due today: {names}{more}."
            else:
                message = "Opening the dispatch app. No jobs are due today."

            self.ui.write_log(f"SYS: {message}")
            self.ui.update_task_workspace(
                title="Dispatch",
                command=text,
                plan=["Open the dispatch app", "Check today's due jobs", "Build the route"],
                status="Opening dispatch",
                output=message,
                percent=100,
                source=source,
            )
            self.speak(message)
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return True
        except Exception as exc:
            self.ui.write_log(f"ERR: Could not open dispatch: {exc}")
            self.speak(f"I couldn't open the dispatch app: {exc}")
            return True

    def _attention_message(self, event: dict) -> str:
        app = (event.get("app") or "an app").strip()
        kind = (event.get("kind") or "message").strip().lower()
        if kind == "call":
            return f"Incoming call detected on {app}. Should I pick it up, ignore it, or cut the call?"
        title = (event.get("title") or "").strip()
        preview = (event.get("preview") or "").strip()
        if title:
            return f"You received a message on {app} from {title}. It says: {preview}"
        return f"You received a message on {app}. It says: {preview}"

    def _announce_attention(self, event: dict):
        msg = self._attention_message(event)
        self.ui.write_log(f"SYS: {msg}")
        threading.Thread(target=speak_native, args=(msg,), daemon=True).start()
        self.ui.show_attention_alert(event)

    def _on_external_notification(self, event: dict):
        if not isinstance(event, dict):
            return
        kind = (event.get("kind") or "message").strip().lower()
        app = (event.get("app") or "App").strip()
        preview = (event.get("preview") or "").strip()
        settings = {}
        try:
            settings = self.ui._load_app_settings()
        except Exception:
            settings = {}

        if kind == "call" and not bool(settings.get("attention_call_prompts", True)):
            return
        if kind == "message" and not bool(settings.get("attention_message_prompts", True)):
            return

        with self._attention_lock:
            current = self._pending_attention
            if current:
                same_app = (current.get("app") or "").strip().lower() == app.lower()
                same_kind = (current.get("kind") or "").strip().lower() == kind
                if same_app and same_kind:
                    return
            self._pending_attention = dict(event)

        self._announce_attention(event)
        if preview:
            self.ui.write_log(f"[Attention] {app}: {preview}")
        if kind == "call" and app.lower() in {"zoom", "teams", "whatsapp"}:
            self._start_meeting_mode(event)

    def _start_meeting_mode(self, event: dict):
        event = dict(event or {})
        app = (event.get("app") or "Meeting").strip()
        title = event.get("title") or f"{app} meeting"
        summary = f"Watching {app} for questions and answers."
        with self._meeting_lock:
            self._meeting_active = True
            self._meeting_event = event
        self.ui.set_meeting_mode(True, title, summary, "Listening for questions on screen...", self._meeting_assistant.latest_speech())
        self._meeting_assistant.start(title=title, context=summary)
        self.ui.write_log(f"SYS: Meeting mode enabled for {app}.")

    def _stop_meeting_mode(self, reason: str = "Meeting mode stopped."):
        with self._meeting_lock:
            was_active = self._meeting_active
            self._meeting_active = False
            self._meeting_event = None
        if was_active:
            self._meeting_assistant.stop()
            self.ui.set_meeting_mode(False, "", "", "")
            self.ui.write_log(f"SYS: {reason}")

    def _on_meeting_update(self, payload: dict):
        if not isinstance(payload, dict):
            return
        active = bool(payload.get("active"))
        title = payload.get("title") or "Meeting mode"
        summary = payload.get("summary") or ""
        answer = payload.get("answer") or ""
        speech = payload.get("speech") or ""
        self.ui.set_meeting_mode(active, title, summary, answer, speech)
        if summary:
            self.ui.write_log(f"[Meeting] {summary}")
        if answer:
            self.ui.write_log(f"REX: {answer}")

    def _on_meeting_state(self, state: str):
        if state == "LISTENING":
            self.ui.set_state("LISTENING")
        elif state == "MEETING":
            self.ui.set_state("THINKING")

    def _attention_matches(self, text: str, words: tuple[str, ...]) -> bool:
        t = (text or "").lower()
        return any(word in t for word in words)

    def _prompt_message_reply(self, event: dict) -> bool:
        if not isinstance(event, dict):
            return False
        with self._attention_lock:
            self._pending_reply_event = dict(event)
            self._pending_attention = None
            self._reply_mode = True

        message = "What would you like to say in reply?"
        self.ui.write_log(f"SYS: {message}")
        threading.Thread(target=speak_native, args=(message,), daemon=True).start()
        try:
            self.ui.begin_task_workspace(
                "Replying to message",
                [
                    "Type your response",
                    "I will reword it naturally",
                    f"Send via {event.get('app', 'the app')}",
                ],
                source="reply",
            )
            self.ui.update_task_workspace(
                status="Awaiting your reply",
                output="Type the message you want to send, and I will make it sound natural before sending it as you.",
                percent=10,
            )
        except Exception as _e:
            log_error(_e, context="main._prompt_message_reply", severity="warning")
        return True

    def _handle_pending_reply(self, text: str) -> bool:
        with self._attention_lock:
            event = dict(self._pending_reply_event or {})
            self._pending_reply_event = None
        if not event:
            return False

        lower = (text or "").lower()
        if self._attention_matches(lower, ("cancel", "never mind", "skip", "do not send", "don't send")):
            self._reply_mode = False
            self.ui.write_log("SYS: Reply cancelled.")
            try:
                self.ui.finish_task_workspace("Reply cancelled.", "Cancelled", 100)
            except Exception as _e:
                log_error(_e, context="main._handle_pending_reply", severity="warning")
            return True

        self.ui.write_log(f"SYS: Drafting reply to {event.get('title') or event.get('app')}.")
        threading.Thread(target=self._draft_and_send_reply, args=(event, text), daemon=True).start()
        return True

    def _rewrite_reply_text(self, user_text: str, event: dict) -> str:
        prompt = (
            "You are a friendly assistant helping a user rewrite their draft reply for a chat message. "
            "Keep the same meaning and intent, expand the wording slightly, and make it sound natural and human. "
            "Do not mention the notification, app, or any internal system details. "
            "Return only the rewritten reply text.\n\n"
            "Notification context:\n"
            f"App: {event.get('app', '')}\n"
            f"Sender: {event.get('title', '')}\n"
            f"Preview: {event.get('preview', '')}\n\n"
            "User draft reply:\n"
            f"{user_text}\n\n"
            "Reply text:"
        )
        try:
            return _gemini_text_reply(prompt) or user_text
        except Exception:
            try:
                return openrouter_client.chat(
                    prompt,
                    system="You are a friendly assistant. Rewrite the reply naturally and humanely.",
                )
            except Exception:
                return user_text

    def _draft_and_send_reply(self, event: dict, text: str):
        try:
            reply_text = self._rewrite_reply_text(text, event)
            if not reply_text:
                reply_text = text
            self.ui.update_task_workspace(
                status="Sending reply",
                output="Sending your expanded reply now...",
                percent=70,
            )
            receiver = (event.get("title") or "").strip()
            platform = (event.get("app") or "whatsapp").strip()
            if not receiver:
                self.ui.write_log("ERR: Could not determine recipient for reply.")
                try:
                    self.ui.finish_task_workspace("Reply failed: recipient not found.", "Reply failed", 100)
                except Exception as _e:
                    log_error(_e, context="main._draft_and_send_reply", severity="warning")
                return
            result = send_message(
                parameters={
                    "receiver": receiver,
                    "message_text": reply_text,
                    "platform": platform,
                },
                player=self.ui,
            )
            self._reply_mode = False
            self.ui.write_log(f"SYS: {result}")
            try:
                self.ui.finish_task_workspace(reply_text, "Reply delivered.", 100)
            except Exception as _e:
                log_error(_e, context="main._draft_and_send_reply", severity="warning")
        except Exception as e:
            self._reply_mode = False
            self.ui.write_log(f"ERR: Reply failed: {e}")
            try:
                self.ui.finish_task_workspace(f"Reply failed: {e}", "Reply failed", 100)
            except Exception as _e:
                log_error(_e, context="main._draft_and_send_reply", severity="warning")
        finally:
            if not self.ui.muted:
                self.ui.set_state("LISTENING")

    def _handle_attention_response(self, text: str) -> bool:
        with self._attention_lock:
            event = dict(self._pending_attention or {})
        if not event:
            return False

        kind = (event.get("kind") or "message").strip().lower()
        lower = (text or "").lower()

        if kind == "message":
            if self._attention_matches(lower, ("reply", "respond", "answer", "write back", "send reply", "send a reply")):
                return self._prompt_message_reply(event)
            if self._attention_matches(lower, ("hear", "read", "what is it", "tell me", "show it", "open it")):
                preview = read_event_preview(event)
                self.ui.write_log(f"REX: {preview}")
                threading.Thread(target=speak_native, args=(preview,), daemon=True).start()
                with self._attention_lock:
                    self._pending_attention = None
                return True
            if self._attention_matches(lower, ("ignore", "dismiss", "skip", "no", "not now")):
                self.ui.write_log("SYS: Message alert dismissed.")
                with self._attention_lock:
                    self._pending_attention = None
                return True
            return False

        if kind == "call":
            if self._attention_matches(lower, ("pick up", "answer", "accept", "take it", "join")):
                result = handle_call_action(event, "accept")
                self.ui.write_log(f"SYS: {result}")
                threading.Thread(target=speak_native, args=(result,), daemon=True).start()
                with self._attention_lock:
                    self._pending_attention = None
                return True
            if self._attention_matches(lower, ("ignore", "decline", "reject", "cut", "hang up", "end")):
                result = handle_call_action(event, "decline")
                self.ui.write_log(f"SYS: {result}")
                threading.Thread(target=speak_native, args=(result,), daemon=True).start()
                with self._attention_lock:
                    self._pending_attention = None
                return True
            if self._attention_matches(lower, ("x", "nothing", "do nothing", "close")):
                self.ui.write_log("SYS: Call alert dismissed.")
                with self._attention_lock:
                    self._pending_attention = None
                return True
            return False

        return False

    def _on_attention_action(self, event: dict, decision: str):
        if not isinstance(event, dict):
            return
        kind = (event.get("kind") or "message").strip().lower()
        decision = (decision or "").strip().lower()

        if kind == "meeting":
            if decision == "stop":
                self._stop_meeting_mode()
            return

        if kind == "message":
            if decision == "hear":
                preview = read_event_preview(event)
                self.ui.write_log(f"REX: {preview}")
                threading.Thread(target=speak_native, args=(preview,), daemon=True).start()
            elif decision == "reply":
                self._prompt_message_reply(event)
                return
            else:
                self.ui.write_log("SYS: Message alert dismissed.")
            with self._attention_lock:
                self._pending_attention = None
            return

        if kind == "call":
            if decision in {"accept", "answer", "pick_up"}:
                result = handle_call_action(event, "accept")
                self.ui.write_log(f"SYS: {result}")
                threading.Thread(target=speak_native, args=(result,), daemon=True).start()
            elif decision in {"noop", "x", "none"}:
                self.ui.write_log("SYS: Call alert dismissed.")
            else:
                result = handle_call_action(event, "decline")
                self.ui.write_log(f"SYS: {result}")
                threading.Thread(target=speak_native, args=(result,), daemon=True).start()
            with self._attention_lock:
                self._pending_attention = None


    def _fallback_reply(self, text: str, memory_ctx: str = ""):
        try:
            self.ui.set_state("THINKING")
            try:
                self.ui.update_task_workspace(
                    status="Thinking",
                    output="REX is drafting a direct reply.",
                    percent=35,
                )
            except Exception as _e:
                log_error(_e, context="main._fallback_reply", severity="warning")
            reply = ""
            gemini_first = not self._use_openrouter_first
            request_text = f"{memory_ctx}\n\nCurrent User Request:\n{text}" if memory_ctx else text

            if gemini_first:
                try:
                    reply = _gemini_text_reply(request_text)
                except Exception as e:
                    print(f"[REX] ⚠️ Gemini fallback failed: {e}")
                    if _is_gemini_limit_error(e):
                        self._use_openrouter_first = True

            if not reply:
                try:
                    reply = openrouter_client.chat(
                        request_text,
                        system=(
                            "You are REX, a concise, helpful desktop assistant. "
                            "Reply naturally and briefly. Do not mention internal implementation details."
                        ),
                    )
                except Exception as e:
                    print(f"[REX] ⚠️ OpenRouter fallback failed: {e}")
                    if gemini_first and not self._use_openrouter_first and _is_gemini_limit_error(e):
                        self._use_openrouter_first = True
            reply = (reply or "").strip()
            if not reply:
                reply = "I’m ready, sir."
            self.ui.write_log(f"REX: {reply}")
            try:
                self.ui.finish_task_workspace(reply, "Reply delivered.", 100)
            except Exception as _e:
                log_error(_e, context="main._fallback_reply", severity="warning")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
        except Exception as e:
            msg = f"Fallback reply failed: {e}"
            print(f"[REX] ⚠️ {msg}")
            self.ui.write_log(f"ERR: {msg}")
            try:
                self.ui.finish_task_workspace(msg, "Reply failed.", 100)
            except Exception as _e:
                log_error(_e, context="main._fallback_reply", severity="warning")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            self._is_speaking = value
        if value:
            self.ui.set_state("SPEAKING")
        elif not self.ui.muted:
            self.ui.set_state("LISTENING")

    def speak(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"Sir, {tool_name} encountered an error. {short}")

    def _build_config(self) -> types.LiveConnectConfig:
        from datetime import datetime

        memory     = load_memory()
        mem_str    = format_memory_for_prompt(memory)
        sys_prompt = _load_system_prompt()

        now      = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n"
            f"Use this to calculate exact times for reminders.\n\n"
        )

        parts = [time_ctx]
        if mem_str:
            parts.append(mem_str)
        parts.append(sys_prompt)
        parts.append(
            "Wake-word mode: if the microphone is muted, still listen for the words 'REX', 'hey', 'hi', and 'hello'. "
            "When you hear one of these activation cues, keep the session friendly and concise, "
            "and wait for the user's next command."
        )

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": TOOL_DECLARATIONS}],
            session_resumption=types.SessionResumptionConfig(),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Charon"
                    )
                )
            ),
        )

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})

        print(f"[REX] 🔧 {name}  {args}")
        self.ui.set_state("THINKING")
        try:
            self.ui.update_task_workspace(
                title=f"Running {name}",
                status=f"Executing {name}",
                output="Waiting for the tool to finish.",
                percent=45,
            )
        except Exception as _e:
            log_error(_e, context="main._execute_tool", severity="warning")
        if name == "save_memory":
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            if key and value:
                update_memory({category: {key: {"value": value}}})
                print(f"[Memory] 💾 save_memory: {category}/{key} = {value}")
                try:
                    self.ui.finish_task_workspace("Memory saved.", "Memory updated.", 100)
                except Exception as _e:
                    log_error(_e, context="main._execute_tool", severity="warning")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
            )

        loop   = asyncio.get_event_loop()
        result = "Done."

        try:
            pm = getattr(self, "plugin_manager", None)
            result = await dispatcher.dispatch(
                name, args,
                ui=self.ui,
                speak=self.speak,
                loop=loop,
                smart_home_service=self._smart_home,
                plugin_registry=pm,
            )


        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()
            self.speak_error(name, e)

        try:
            self.ui.finish_task_workspace(result, "Task completed.", 100)
        except Exception as _e:
            log_error(_e, context="main._execute_tool", severity="warning")

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        print(f"[REX] 📤 {name} → {str(result)[:80]}")

        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result}
        )

    async def _serve_dashboard(self):
        if self._dashboard is None:
            self.ui.write_log("ERR: Mobile Connect disabled because dashboard dependencies are missing.")
            return
        try:
            self._dashboard.set_connect_callback(self._on_phone_connected)
            self._dashboard.set_wake_callback(lambda: None)
            await self._dashboard.serve()
        except Exception as e:
            self.ui.write_log(f"ERR: Mobile Connect server failed: {e}")
            traceback.print_exc()

    async def _consume_remote_commands(self):
        if self._dashboard is None:
            return
        while True:
            text = await self._dashboard._command_queue.get()
            if text:
                try:
                    self.ui.submit_external_command(text, source="mobile")
                except Exception:
                    self._on_text_command(text, source="mobile")

    async def _relay_phone_audio(self):
        if self._dashboard is None:
            return
        while True:
            frame = await self._dashboard._phone_audio_queue.get()
            if not self.out_queue:
                continue
            self._phone_active = True
            try:
                await self.out_queue.put(frame)
            finally:
                await asyncio.sleep(0.08)
                if self._dashboard._phone_audio_queue.empty():
                    self._phone_active = False

    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            await self.session.send_realtime_input(media=msg)

    async def _listen_audio(self):
        print("[REX] 🎤 Mic started")
        loop = asyncio.get_event_loop()

        def callback(indata, frames, time_info, status):
            with self._speaking_lock:
                rex_speaking = self._is_speaking
            if self._phone_active:
                return
            if not rex_speaking and (not self.ui.muted or getattr(self.ui, "_wakeword_listening", False)):
                data = indata.tobytes()
                if DEVICE_RATE != SEND_SAMPLE_RATE:
                    data = _resample_int16(data, DEVICE_RATE, SEND_SAMPLE_RATE)
                loop.call_soon_threadsafe(
                    self.out_queue.put_nowait,
                    {"data": data, "mime_type": "audio/pcm"}
                )

        try:
            with sd.InputStream(
                samplerate=DEVICE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=CHUNK_SIZE,
                callback=callback,
            ):
                print("[REX] 🎤 Mic stream open")
                while True:
                    await asyncio.sleep(0.1)
        except Exception as e:
            print(f"[REX] ❌ Mic: {e}")
            raise

    async def _receive_audio(self):
        print("[REX] 👂 Recv started")
        out_buf, in_buf = [], []

        try:
            while True:
                async for response in self.session.receive():

                    if response.data:
                        self.audio_in_queue.put_nowait(response.data)

                    if response.server_content:
                        sc = response.server_content

                        if sc.output_transcription and sc.output_transcription.text:
                            self.set_speaking(True)
                            txt = sc.output_transcription.text.strip()
                            if txt:
                                out_buf.append(txt)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = sc.input_transcription.text.strip()
                            if txt:
                                try:
                                    from actions.attention_monitor import stop_native_speech
                                    stop_native_speech()
                                except Exception as _e:
                                    log_error(_e, context="main._receive_audio", severity="warning")
                                in_buf.append(txt)
                                if self.ui.muted and _wakeword_detected(txt):
                                    try:
                                        self.ui.set_muted_state(False, wakeword=True)
                                        self.ui.write_log("SYS: Wake word detected. Mic active.")
                                    except Exception as _e:
                                        log_error(_e, context="main._receive_audio", severity="warning")

                        if sc.turn_complete:
                            self.set_speaking(False)

                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self.ui.write_log(f"You: {full_in}")
                            in_buf = []

                            full_out = " ".join(out_buf).strip()
                            if full_out:
                                self.ui.write_log(f"REX: {full_out}")
                            out_buf = []

                            if full_in and len(full_in) > 5:
                                threading.Thread(
                                    target=_update_memory_async,
                                    args=(full_in, full_out),
                                    daemon=True
                                ).start()

                    if response.tool_call:
                        fn_responses = []
                        for fc in response.tool_call.function_calls:
                            print(f"[REX] 📞 {fc.name}")
                            fr = await self._execute_tool(fc)
                            fn_responses.append(fr)
                        await self.session.send_tool_response(
                            function_responses=fn_responses
                        )

        except Exception as e:
            print(f"[REX] ❌ Recv: {e}")
            traceback.print_exc()
            raise

    async def _play_audio(self):
        print("[REX] 🔊 Play started")
        loop = asyncio.get_event_loop()

        stream = sd.RawOutputStream(
            samplerate=DEVICE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=CHUNK_SIZE,
        )
        stream.start()
        try:
            while True:
                chunk = await self.audio_in_queue.get()
                self.set_speaking(True)
                if DEVICE_RATE != RECEIVE_SAMPLE_RATE:
                    chunk = _resample_int16(chunk, RECEIVE_SAMPLE_RATE, DEVICE_RATE)
                await asyncio.to_thread(stream.write, chunk)
        except Exception as e:
            print(f"[REX] ❌ Play: {e}")
            raise
        finally:
            self.set_speaking(False)
            stream.stop()
            stream.close()

    async def run(self):
        # announce boot steps to UI overlay (thread-safe wrappers)
        try:
            self.ui.boot_add_step("Load configuration")
            self.ui.boot_add_step("Start attention monitor")
            self.ui.boot_add_step("Start dashboard server")
            self.ui.boot_add_step("Initialize audio")
            self.ui.boot_add_step("Connect AI backend")
            self.ui.boot_add_step("Finalize startup")
            self.ui.boot_set_progress(3, "Preparing startup...")
        except Exception as _e:
            log_error(_e, context="main.run", severity="warning")

        self._attention_monitor.start()
        try:
            self.ui.boot_set_step_status("Start attention monitor", "done")
            self.ui.boot_set_progress(12, "Attention monitor online")
        except Exception as _e:
            log_error(_e, context="main.run", severity="warning")
        if self._dashboard is not None:
            if not self._dashboard_started:
                self._dashboard_started = True
                asyncio.create_task(self._serve_dashboard())
                try:
                    self.ui.boot_set_step_status("Start dashboard server", "done")
                    self.ui.boot_set_progress(22, "Mobile connect server running")
                except Exception as _e:
                    log_error(_e, context="main.run", severity="warning")
            asyncio.create_task(self._consume_remote_commands())
            asyncio.create_task(self._relay_phone_audio())
        try:
            self.ui.boot_set_progress(36, "Initializing AI client")
        except Exception as _e:
            log_error(_e, context="main.run", severity="warning")

        client = genai.Client(
            api_key=_get_api_key(),
            http_options={"api_version": "v1beta"}
        )

        while True:
            try:
                print("[REX] 🔌 Connecting...")
                self.ui.set_state("THINKING")
                config = self._build_config()

                connect_cm = client.aio.live.connect(model=LIVE_MODEL, config=config)
                session = await asyncio.wait_for(connect_cm.__aenter__(), timeout=LIVE_CONNECT_TIMEOUT)
                try:
                    async with asyncio.TaskGroup() as tg:
                        self.session        = session
                        self._loop          = asyncio.get_event_loop()
                        self.audio_in_queue = asyncio.Queue()
                        self.out_queue      = asyncio.Queue(maxsize=10)

                        print("[REX] ✅ Connected.")
                        try:
                            self.ui.boot_set_step_status("Connect AI backend", "done")
                            self.ui.boot_set_progress(75, "AI backend connected")
                        except Exception as _e:
                            log_error(_e, context="main.run", severity="warning")
                        self.ui.set_state("LISTENING")
                        self.ui.write_log("SYS: REX online.")

                        tg.create_task(self._send_realtime())
                        tg.create_task(self._listen_audio())
                        tg.create_task(self._relay_phone_audio())
                        tg.create_task(self._receive_audio())
                        tg.create_task(self._play_audio())
                        try:
                            self.ui.boot_set_step_status("Initialize audio", "done")
                            self.ui.boot_set_progress(92, "Audio subsystems online")
                        except Exception as _e:
                            log_error(_e, context="main.run", severity="warning")
                        # finalize
                        try:
                            self.ui.boot_set_step_status("Finalize startup", "done")
                            self.ui.boot_set_progress(100, "Startup complete")
                        except Exception as _e:
                            log_error(_e, context="main.run", severity="warning")
                finally:
                    try:
                        await connect_cm.__aexit__(None, None, None)
                    except Exception as _e:
                        log_error(_e, context="main.run", severity="warning")
                    
            except Exception as e:
                print(f"[REX] ⚠️ {e}")
                traceback.print_exc()
                if _is_gemini_limit_error(e):
                    self._use_openrouter_first = True
                self.session = None
                self._loop = None
            self.set_speaking(False)
            self.ui.set_state("LISTENING")
            print("[REX] 🔄 Reconnecting in 5s...")
            await asyncio.sleep(5)

def main():
    _startup_log("main entered")
    _ensure_desktop_shortcut()
    ui = REXUI(str(BASE_DIR / "assets" / "REX_Logo.png"), show_immediately=True)
    dashboard = None
    dashboard_enabled = DashboardServer is not None and not _is_port_in_use(8000)
    if DashboardServer is not None and not dashboard_enabled:
        _startup_log("dashboard disabled: port 8000 already in use")
        try:
            ui.write_log("SYS: Mobile Connect is already running in another REX instance.")
        except Exception as _e:
            log_error(_e, context="main.main", severity="warning")
    if dashboard_enabled:
        dashboard = DashboardServer()

    if dashboard is not None:
        def _start_dashboard_server():
            try:
                _startup_log("dashboard thread started")
                asyncio.run(dashboard.serve())
            except Exception as exc:
                _startup_log(f"dashboard thread error: {exc}")
                try:
                    ui.write_log(f"ERR: Mobile Connect server failed: {exc}")
                except Exception as _e:
                    log_error(_e, context="main._start_dashboard_server", severity="warning")

        threading.Thread(target=_start_dashboard_server, daemon=True).start()
        _startup_log("dashboard thread spawned")

    ui.show_main()
    _startup_log("ui shown")

    # Initialize plugin manager and load any plugins from ./plugins
    try:
        plugin_manager = PluginManager(BASE_DIR)
        plugin_manager.load_plugins()
    except Exception:
        plugin_manager = None

    def runner():
        _startup_log("runner waiting api key")
        ui.wait_for_api_key()
        _startup_log("runner api key ready")
        rex = RexLive(
            ui,
            dashboard=dashboard,
            dashboard_started=dashboard is not None,
            enable_dashboard=dashboard_enabled,
        )
        try:
            if plugin_manager is not None:
                rex.plugin_manager = plugin_manager
                plugin_manager.register_rex(rex)
                # allow plugins to run a startup hook
                try:
                    plugin_manager.dispatch("on_startup", rex)
                except Exception as _e:
                    log_error(_e, context="main.runner", severity="warning")
        except Exception as _e:
            log_error(_e, context="main.runner", severity="warning")
        try:
            asyncio.run(rex.run())
        except KeyboardInterrupt:
            print("\n🔴 Shutting down...")

    def start_runner():
        threading.Thread(target=runner, daemon=True).start()

    start_runner()
    try:
        ui.play_boot_sequence(
            finished_callback=lambda: threading.Thread(
                target=_speak_daily_briefing,
                args=(ui,),
                daemon=True,
                name="daily-briefing",
            ).start()
        )
    except Exception:
        ui.show_main()
        start_runner()
        threading.Thread(
            target=_speak_daily_briefing,
            args=(ui,),
            daemon=True,
            name="daily-briefing",
        ).start()
    ui.root.mainloop()


if __name__ == "__main__":
    main()
