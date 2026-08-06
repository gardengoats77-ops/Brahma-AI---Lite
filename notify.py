# notify.py — cross-platform desktop notifications.
#
# Replaces the stubbed win10toast path on Linux/macOS.  The linux_shim
# keeps its win10toast stub (the shim test asserts `ToastNotifier()` still
# raises), but real code paths never call win10toast off-Windows — they
# route through this module instead.
#
# Backend resolution, best-effort and never raising:
#
#   Windows : win10toast (the real package, when installed)
#   macOS   : osascript `display notification`
#   Linux   : 1) `dbus-send` to org.freedesktop.Notifications (typed args,
#                 immune to a broken `notify-send` binary / libnotify drift)
#             2) the `notify-send` CLI as a last resort
#             3) silent no-op
#
# Every failure degrades to the next backend; the final fallback is a
# no-op, so callers never crash on headless or unusual systems.

from __future__ import annotations

import platform
import subprocess

APP_NAME = "Almighty AI"


def _win32_notify(app_name: str, title: str, message: str, timeout_ms: int) -> bool:
    """Windows: real win10toast (not the shim)."""
    try:
        from win10toast import ToastNotifier

        ToastNotifier().show_toast(title, message, duration=max(3, timeout_ms // 1000), threaded=True)
        return True
    except Exception:
        return False


def _macos_notify(title: str, message: str) -> bool:
    """macOS: AppleScript display notification (app name is not settable here)."""
    try:
        script = f'display notification "{message}" with title "{title}"'
        subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, timeout=8, check=False,
        )
        return True
    except Exception:
        return False


def _dbus_send_notify(app_name: str, title: str, message: str, timeout_ms: int) -> bool:
    """Linux primary: typed `dbus-send` call to org.freedesktop.Notifications.

    Signature of Notify is (susssasa{sv}i): app_name, replaces_id, app_icon,
    summary, body, actions(as), hints(a{sv}), expire_timeout(i).
    """
    try:
        cmd = [
            "dbus-send", "--session", "--type=method_call", "--print-reply",
            "--dest=org.freedesktop.Notifications",
            "/org/freedesktop/Notifications",
            "org.freedesktop.Notifications.Notify",
            f"string:{app_name}",
            "uint32:0",
            "string:",
            f"string:{title}",
            f"string:{message}",
            "array:string:",
            "dict:string:variant:",
            f"int32:{int(timeout_ms)}",
        ]
        res = subprocess.run(cmd, capture_output=True, timeout=8)
        return res.returncode == 0
    except Exception:
        return False


def _notify_send_cli(app_name: str, title: str, message: str, timeout_ms: int) -> bool:
    """Linux last resort: the notify-send CLI (may be broken on some boxes)."""
    try:
        cmd = ["notify-send", "-a", app_name, "-t", str(int(timeout_ms)), title, message]
        res = subprocess.run(cmd, capture_output=True, timeout=8)
        return res.returncode == 0
    except Exception:
        return False


def notify(
    title: str,
    message: str,
    app_name: str = APP_NAME,
    timeout_ms: int = 10000,
) -> bool:
    """Show a desktop notification. Never raises.

    Args:
        title:     notification summary line.
        message:   notification body text.
        app_name:  override the application name shown by the daemon.
        timeout_ms: how long the notification stays visible (ms).

    Returns True if any backend delivered (or attempted without error).
    """
    title = str(title or "").strip()
    message = str(message or "").strip()
    if not title and not message:
        return False

    system = (platform.system() or "").lower()
    if system == "windows":
        return _win32_notify(app_name, title, message, timeout_ms)
    if system == "darwin":
        return _macos_notify(title, message)
    # Linux / other POSIX
    if _dbus_send_notify(app_name, title, message, timeout_ms):
        return True
    if _notify_send_cli(app_name, title, message, timeout_ms):
        return True
    return False
