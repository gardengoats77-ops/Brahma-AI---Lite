"""
Email Monitor Plugin for REX
Background email monitoring with alerts for important messages.
Runs as a daemon thread, checks emails periodically, and triggers notifications.
"""

import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Callable

from core.error_handler import log_error

from actions.email_manager import (
    _detect_provider,
    _load_oauth_tokens,
    email_read,
    email_search,
    email_unread_count
)

# Try to import from centralized speak module, fallback to attention_monitor
try:
    from speak import speak_native
except ImportError:
    try:
        from actions.attention_monitor import speak_native
    except ImportError:
        def speak_native(text: str) -> None:
            """Fallback if speak_native isn't available."""
            print(f"[EmailMonitor] Voice alert: {text}")

BASE_DIR = Path(__file__).parent.parent
CONFIG_DIR = BASE_DIR / "config"
MONITOR_CONFIG = CONFIG_DIR / "email_monitor.json"
ALERT_STATE_FILE = CONFIG_DIR / "email_monitor_state.json"

# Default configuration
DEFAULT_CONFIG = {
    "enabled": False,
    "interval_minutes": 5,
    "important_senders": [],
    "important_subjects": [],
    "important_keywords": [],
    "alert_on_high_importance": True,
    "alert_on_attachments": False,
    "max_alerts_per_hour": 10,
    "quiet_hours_start": 22,
    "quiet_hours_end": 7,
    "voice_alerts": True,
    "desktop_alerts": True,
    "auto_mark_read": False,
    "last_check_time": None,
    "last_alert_time": None,
    "alerts_this_hour": 0
}


class EmailMonitor:
    """
    Background email monitor that checks for important messages
    and triggers alerts through the REX notification system.
    """

    _SEEN_MAX = 200  # max tracked message keys to prevent unbounded growth

    def __init__(self, config: dict = None):
        self.config = self._load_config(config or {})
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._seen_message_ids: set = set()
        self._alert_callbacks: list[Callable] = []
        self._lock = threading.Lock()
        self._speak_fn: Optional[Callable] = None  # injected speak function

    def _load_config(self, overrides: dict) -> dict:
        """Load configuration from file with overrides."""
        file_config = {}
        try:
            if MONITOR_CONFIG.exists():
                with open(MONITOR_CONFIG, "r", encoding="utf-8") as f:
                    file_config = json.load(f)
        except Exception as _e:
            log_error(_e, context="actions.email_monitor", severity="warning")

        # Merge: default < file < overrides
        config = {**DEFAULT_CONFIG, **file_config}
        config.update(overrides)
        return config

    def save_config(self, config: dict = None):
        """Save current configuration to file."""
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with open(MONITOR_CONFIG, "w", encoding="utf-8") as f:
                json.dump(config or self.config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[EmailMonitor] Failed to save config: {e}")

    def add_alert_callback(self, callback: Callable):
        """Register a callback function to be called when an alert is triggered."""
        self._alert_callbacks.append(callback)

    def start(self):
        """Start the email monitor in a background thread."""
        if self._running:
            print("[EmailMonitor] Already running")
            return

        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._monitor_loop,
            name="EmailMonitorThread",
            daemon=True
        )
        self._thread.start()
        print("[EmailMonitor] Started")

    def stop(self):
        """Stop the email monitor."""
        self._running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        print("[EmailMonitor] Stopped")

    def _monitor_loop(self):
        """Main monitoring loop that runs in background."""
        print("[EmailMonitor] Monitoring loop started")
        while self._running and not self._stop_event.is_set():
            try:
                # Check if we should alert (not in quiet hours, under rate limit)
                if self._should_alert():
                    self._check_emails()
                else:
                    print("[EmailMonitor] Skipping check (quiet hours or rate limit)")
            except Exception as e:
                print(f"[EmailMonitor] Error in monitoring loop: {e}")

            # Wait for the configured interval or until stopped
            self._stop_event.wait(timeout=self.config["interval_minutes"] * 60)

        print("[EmailMonitor] Monitoring loop ended")

    def set_speak_function(self, speak_fn: Callable):
        """Inject the main speak function from REXLive."""
        self._speak_fn = speak_fn

    def _remember_seen(self, key: str) -> None:
        """Track a seen message key with bounded size."""
        self._seen_message_ids.add(key)
        if len(self._seen_message_ids) > self._SEEN_MAX:
            # Keep the most recent half
            self._seen_message_ids = set(list(self._seen_message_ids)[-self._SEEN_MAX // 2:])

    def _should_alert(self) -> bool:
        """Check if we should send alerts based on time and rate limits."""
        now = datetime.now()
        hour = now.hour

        # Check quiet hours
        quiet_start = self.config.get("quiet_hours_start", 22)
        quiet_end = self.config.get("quiet_hours_end", 7)

        if quiet_start > quiet_end:
            # Overnight quiet hours (e.g., 22-7)
            if hour >= quiet_start or hour < quiet_end:
                return False
        else:
            # Same-day quiet hours (e.g., 12-14)
            if quiet_start <= hour < quiet_end:
                return False

        # Check rate limit
        try:
            if ALERT_STATE_FILE.exists():
                with open(ALERT_STATE_FILE, "r", encoding="utf-8") as f:
                    state = json.load(f)
                last_alert = state.get("last_alert_time")
                if last_alert:
                    last_alert_dt = datetime.fromisoformat(last_alert)
                    if (now - last_alert_dt).total_seconds() < 3600:
                        # Within the last hour
                        if state.get("alerts_this_hour", 0) >= self.config.get("max_alerts_per_hour", 10):
                            return False
        except Exception as _e:
            log_error(_e, context="actions.email_monitor", severity="warning")

        return True

    def _check_emails(self):
        """Check emails for important messages."""
        provider = _detect_provider()
        if provider == "none":
            print("[EmailMonitor] No email provider configured")
            return

        print(f"[EmailMonitor] Checking emails via {provider}...")

        # Get unread emails
        try:
            # Use the email_read function to get recent unread emails
            result = email_read(provider=provider, query="is:unread", max_results=20)

            # Parse the result to extract message info
            messages = self._parse_email_result(result)

            # Check for important messages
            important_messages = []
            for msg in messages:
                if self._is_important(msg):
                    important_messages.append(msg)

            # Send alerts for important messages
            for msg in important_messages:
                self._send_alert(msg)

            # Update last check time
            self.config["last_check_time"] = datetime.now().isoformat()
            self.save_config()

        except Exception as e:
            print(f"[EmailMonitor] Error checking emails: {e}")

    def _parse_email_result(self, result: str) -> list:
        """Parse email_read result into structured message objects."""
        messages = []
        current_msg = {}

        for line in result.split("\n"):
            line = line.strip()
            if not line:
                if current_msg and current_msg.get("sender"):
                    messages.append(current_msg)
                    current_msg = {}
                continue

            # Parse different line types
            if any(line.startswith(e) for e in ("📧", "🔵", "⚪", "🔴", "📎")):
                # Status line with sender info
                parts = line.split("From:", 1)
                if len(parts) > 1:
                    current_msg["sender"] = parts[1].strip()
                    # Determine status from emoji
                    if "🔵" in line:
                        current_msg["status"] = "unread"
                    elif "⚪" in line:
                        current_msg["status"] = "read"
                    elif "🔴" in line:
                        current_msg["importance"] = "high"
            elif line.startswith("Subject:"):
                current_msg["subject"] = line.split("Subject:", 1)[1].strip()
            elif line.startswith("Date:"):
                current_msg["date"] = line.split("Date:", 1)[1].strip()
            elif line.startswith("Preview:"):
                current_msg["preview"] = line.split("Preview:", 1)[1].strip()

        if current_msg and current_msg.get("sender"):
            messages.append(current_msg)

        # Only warn if the raw result looks like it had content but we failed to parse
        if not messages and result and len(result.strip()) > 20:
            # Skip warning for expected empty/auth-failure responses
            lower_result = result.lower()
            has_content_hints = any(hint in lower_result for hint in ("from:", "subject:", "inbox", "emails"))
            is_error = any(err in lower_result for err in ("no emails", "not authenticated", "no email provider", "error"))
            if has_content_hints and not is_error:
                print("[EmailMonitor] Warning: email_read output had content but parsing yielded 0 messages. Output format may have changed.")

        return messages

    def _is_important(self, msg: dict) -> bool:
        """Determine if a message is important based on configuration."""
        sender = (msg.get("sender") or "").lower()
        subject = (msg.get("subject") or "").lower()
        preview = (msg.get("preview") or "").lower()
        importance = msg.get("importance", "")

        # Check if already alerted
        msg_key = f"{sender}|{subject}"
        if msg_key in self._seen_message_ids:
            return False

        # Check importance flag
        if importance == "high" and self.config.get("alert_on_high_importance", True):
            return True

        # Check important senders
        for important_sender in self.config.get("important_senders", []):
            if important_sender.lower() in sender:
                return True

        # Check important subjects
        for important_subject in self.config.get("important_subjects", []):
            if important_subject.lower() in subject:
                return True

        # Check important keywords in subject or preview
        for keyword in self.config.get("important_keywords", []):
            if keyword.lower() in subject or keyword.lower() in preview:
                return True

        return False

    def _send_alert(self, msg: dict):
        """Send alert for an important message."""
        sender = msg.get("sender", "Unknown")
        subject = msg.get("subject", "(No subject)")
        preview = msg.get("preview", "")

        # Create alert message
        alert_text = f"Important email from {sender}: {subject}"
        if preview:
            alert_text += f" - {preview[:100]}"

        print(f"[EmailMonitor] ALERT: {alert_text}")

        # Update state
        with self._lock:
            now = datetime.now()
            try:
                state = {}
                if ALERT_STATE_FILE.exists():
                    with open(ALERT_STATE_FILE, "r", encoding="utf-8") as f:
                        state = json.load(f)

                # Reset counter if it's a new hour
                last_alert = state.get("last_alert_time")
                if last_alert:
                    last_alert_dt = datetime.fromisoformat(last_alert)
                    if (now - last_alert_dt).total_seconds() >= 3600:
                        state["alerts_this_hour"] = 0

                state["last_alert_time"] = now.isoformat()
                state["alerts_this_hour"] = state.get("alerts_this_hour", 0) + 1

                with open(ALERT_STATE_FILE, "w", encoding="utf-8") as f:
                    json.dump(state, f, indent=2)
            except Exception as e:
                print(f"[EmailMonitor] Failed to update state: {e}")

        # Send voice alert (prefer injected speak, fallback to speak_native)
        if self.config.get("voice_alerts", True):
            try:
                if self._speak_fn:
                    self._speak_fn(alert_text)
                else:
                    speak_native(alert_text)
            except Exception as e:
                print(f"[EmailMonitor] Voice alert failed: {e}")

        # Send desktop notification via callbacks
        event_data = {
            "type": "email_alert",
            "kind": "message",
            "app": "Email",
            "title": sender,
            "preview": f"{subject} — {preview[:100]}" if preview else subject,
            "alert_text": alert_text,
            "sender": sender,
            "subject": subject,
            "timestamp": datetime.now().isoformat()
        }
        for callback in self._alert_callbacks:
            try:
                callback(event_data)
            except Exception as e:
                print(f"[EmailMonitor] Alert callback failed: {e}")

        # Mark as seen
        msg_key = f"{sender}|{subject}"
        self._seen_message_ids.add(msg_key)

    def get_status(self) -> dict:
        """Get current monitor status."""
        return {
            "running": self._running,
            "enabled": self.config.get("enabled", False),
            "interval_minutes": self.config.get("interval_minutes", 5),
            "provider": _detect_provider(),
            "last_check": self.config.get("last_check_time"),
            "alerts_today": self._get_alerts_today(),
            "quiet_hours": f"{self.config.get('quiet_hours_start', 22)}:00 - {self.config.get('quiet_hours_end', 7)}:00"
        }

    def _get_alerts_today(self) -> int:
        """Get number of alerts sent today."""
        try:
            if ALERT_STATE_FILE.exists():
                with open(ALERT_STATE_FILE, "r", encoding="utf-8") as f:
                    state = json.load(f)
                last_alert = state.get("last_alert_time")
                if last_alert:
                    last_alert_dt = datetime.fromisoformat(last_alert)
                    if last_alert_dt.date() == datetime.now().date():
                        return state.get("alerts_this_hour", 0)
        except Exception as _e:
            log_error(_e, context="actions.email_monitor", severity="warning")
        return 0


# Singleton instance
_email_monitor: Optional[EmailMonitor] = None


def get_email_monitor() -> EmailMonitor:
    """Get or create the singleton email monitor instance."""
    global _email_monitor
    if _email_monitor is None:
        _email_monitor = EmailMonitor()
    return _email_monitor


def start_email_monitor(config: dict = None):
    """Start the email monitor with optional config overrides."""
    monitor = get_email_monitor()
    if config:
        monitor.config.update(config)
        monitor.save_config()
    monitor.start()
    return "Email monitor started"


def stop_email_monitor():
    """Stop the email monitor."""
    monitor = get_email_monitor()
    monitor.stop()
    return "Email monitor stopped"


def get_email_monitor_status():
    """Get email monitor status."""
    monitor = get_email_monitor()
    return json.dumps(monitor.get_status(), indent=2)


def update_email_monitor_config(
    enabled: bool = None,
    interval_minutes: int = None,
    important_senders: list = None,
    important_subjects: list = None,
    important_keywords: list = None,
    alert_on_high_importance: bool = None,
    quiet_hours_start: int = None,
    quiet_hours_end: int = None,
    voice_alerts: bool = None,
    desktop_alerts: bool = None
):
    """Update email monitor configuration."""
    monitor = get_email_monitor()

    updates = {}
    if enabled is not None:
        updates["enabled"] = enabled
    if interval_minutes is not None:
        updates["interval_minutes"] = max(1, interval_minutes)
    if important_senders is not None:
        updates["important_senders"] = important_senders
    if important_subjects is not None:
        updates["important_subjects"] = important_subjects
    if important_keywords is not None:
        updates["important_keywords"] = important_keywords
    if alert_on_high_importance is not None:
        updates["alert_on_high_importance"] = alert_on_high_importance
    if quiet_hours_start is not None:
        updates["quiet_hours_start"] = quiet_hours_start
    if quiet_hours_end is not None:
        updates["quiet_hours_end"] = quiet_hours_end
    if voice_alerts is not None:
        updates["voice_alerts"] = voice_alerts
    if desktop_alerts is not None:
        updates["desktop_alerts"] = desktop_alerts

    monitor.config.update(updates)
    monitor.save_config()

    return json.dumps(monitor.config, indent=2)


# Tool definitions for registration
EMAIL_MONITOR_TOOLS = [
    {
        "name": "email_monitor_start",
        "description": "Start the background email monitor that alerts you to important messages.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "interval_minutes": {
                    "type": "INTEGER",
                    "description": "Check interval in minutes (default: 5)"
                }
            },
            "required": []
        }
    },
    {
        "name": "email_monitor_stop",
        "description": "Stop the background email monitor.",
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "email_monitor_status",
        "description": "Get current status of the email monitor.",
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "email_monitor_config",
        "description": (
            "Configure email monitoring rules. "
            "Set important senders, subjects, keywords, quiet hours, and alert preferences."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "enabled": {"type": "BOOLEAN", "description": "Enable/disable monitoring"},
                "interval_minutes": {"type": "INTEGER", "description": "Check interval in minutes"},
                "important_senders": {
                    "type": "ARRAY",
                    "items": {"type": "STRING"},
                    "description": "Email addresses to always alert on (e.g., ['boss@company.com'])"
                },
                "important_subjects": {
                    "type": "ARRAY",
                    "items": {"type": "STRING"},
                    "description": "Subject keywords to always alert on (e.g., ['urgent', 'deadline'])"
                },
                "important_keywords": {
                    "type": "ARRAY",
                    "items": {"type": "STRING"},
                    "description": "Keywords to search in subject/body (e.g., ['payment', 'invoice'])"
                },
                "alert_on_high_importance": {
                    "type": "BOOLEAN",
                    "description": "Alert on high importance emails (default: true)"
                },
                "quiet_hours_start": {
                    "type": "INTEGER",
                    "description": "Quiet hours start (0-23, default: 22)"
                },
                "quiet_hours_end": {
                    "type": "INTEGER",
                    "description": "Quiet hours end (0-23, default: 7)"
                },
                "voice_alerts": {
                    "type": "BOOLEAN",
                    "description": "Enable voice alerts (default: true)"
                }
            },
            "required": []
        }
    },
    {
        "name": "email_monitor_add_sender",
        "description": "Add an important sender to always alert on.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "email": {"type": "STRING", "description": "Email address to add"}
            },
            "required": ["email"]
        }
    },
    {
        "name": "email_monitor_remove_sender",
        "description": "Remove a sender from the important senders list.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "email": {"type": "STRING", "description": "Email address to remove"}
            },
            "required": ["email"]
        }
    }
]


def handle_email_monitor_tool(tool_name: str, parameters: dict, speak=None) -> str:
    """Route email monitor tool calls to appropriate functions."""
    try:
        if tool_name == "email_monitor_start":
            interval = parameters.get("interval_minutes", 5)
            return start_email_monitor({"interval_minutes": interval})

        elif tool_name == "email_monitor_stop":
            return stop_email_monitor()

        elif tool_name == "email_monitor_status":
            return get_email_monitor_status()

        elif tool_name == "email_monitor_config":
            return update_email_monitor_config(
                enabled=parameters.get("enabled"),
                interval_minutes=parameters.get("interval_minutes"),
                important_senders=parameters.get("important_senders"),
                important_subjects=parameters.get("important_subjects"),
                important_keywords=parameters.get("important_keywords"),
                alert_on_high_importance=parameters.get("alert_on_high_importance"),
                quiet_hours_start=parameters.get("quiet_hours_start"),
                quiet_hours_end=parameters.get("quiet_hours_end"),
                voice_alerts=parameters.get("voice_alerts"),
                desktop_alerts=parameters.get("desktop_alerts")
            )

        elif tool_name == "email_monitor_add_sender":
            email = parameters.get("email", "")
            if not email:
                return "❌ Please provide an email address"
            monitor = get_email_monitor()
            senders = monitor.config.get("important_senders", [])
            if email not in senders:
                senders.append(email)
                monitor.config["important_senders"] = senders
                monitor.save_config()
            return f"✅ Added {email} to important senders"

        elif tool_name == "email_monitor_remove_sender":
            email = parameters.get("email", "")
            if not email:
                return "❌ Please provide an email address"
            monitor = get_email_monitor()
            senders = monitor.config.get("important_senders", [])
            if email in senders:
                senders.remove(email)
                monitor.config["important_senders"] = senders
                monitor.save_config()
            return f"✅ Removed {email} from important senders"

        else:
            return f"❌ Unknown email monitor tool: {tool_name}"

    except Exception as e:
        return f"❌ Email monitor error: {e}"
