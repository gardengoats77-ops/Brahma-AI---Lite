"""Whisplay HAT live dashboard for the REX desktop GUI.

Drives the small SPI TFT (via :class:`WhisplayDisplay`) with live status:
voice state, CPU/RAM, temperature, IP, and Hailo NPU health. Also watches
a physical push button (lgpio) and fires a callback on press so the GUI can
toggle push-to-talk.

Everything is optional at runtime — missing display, missing GPIO, or
missing lgpio all degrade to logged no-ops. The GUI must never block on
this module.
"""
from __future__ import annotations

import logging
import subprocess
import threading
import time
from typing import Callable, Optional

log = logging.getLogger("whisplay.dashboard")

try:
    import lgpio
    _LGPIO_AVAILABLE = True
except Exception as e:  # noqa: BLE001
    log.info("lgpio not available — button disabled: %s", e)
    _LGPIO_AVAILABLE = False

try:
    from pi.whisplay_display import WhisplayDisplay
    _DISPLAY_AVAILABLE = True
except Exception as e:  # noqa: BLE001
    log.info("WhisplayDisplay unavailable — screen disabled: %s", e)
    _DISPLAY_AVAILABLE = False

# Default button GPIO (BCM). GPIO16 is free on this HAT (DC=17/RST=22 used
# by the SPI panel, backlight on 23 held by kernel). Active-low with the
# internal pull-up: pressed -> line reads 0.
_BUTTON_GPIO = 16
_DEBOUNCE_S = 0.12
_UPDATE_INTERVAL_S = 2.0


def _hailo_ok() -> bool:
    """Quick NPU health check via hailortcli (cheap, ~50ms)."""
    try:
        r = subprocess.run(
            ["hailortcli", "fw-control", "identify"],
            capture_output=True, text=True, timeout=3,
        )
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _local_ip() -> str:
    """Best-effort primary IPv4 (UDP connect trick — no external traffic)."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:  # noqa: BLE001
        return "127.0.0.1"


class WhisplayDashboard:
    """Background TFT status + button watcher with push-to-talk callback.

    Usage::

        dash = WhisplayDashboard(on_push_to_talk=callback)
        dash.set_voice_state("listening")   # update text on next tick
        dash.start()
        ...
        dash.stop()
    """

    def __init__(self, on_push_to_talk: Optional[Callable[[], None]] = None,
                 button_gpio: int = _BUTTON_GPIO):
        self._on_ptt = on_push_to_talk
        self._button_gpio = button_gpio
        self._display: Optional[WhisplayDisplay] = None
        self._chip: Optional[int] = None
        self._voice_state = "starting"
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._button_pressed_at = 0.0

        if _DISPLAY_AVAILABLE:
            try:
                self._display = WhisplayDisplay()
            except Exception as e:  # noqa: BLE001
                log.warning("display init failed: %s", e)
                self._display = None

        if _LGPIO_AVAILABLE:
            try:
                self._chip = lgpio.gpiochip_open(0)
                lgpio.gpio_claim_input(
                    self._chip, self._button_gpio,
                    lgpio.SET_PULL_UP,
                )
                log.info("button wired on GPIO%d", self._button_gpio)
            except Exception as e:  # noqa: BLE001
                log.warning("button init failed: %s", e)
                self._chip = None

    # ── public API ──────────────────────────────────────────────────────
    def set_voice_state(self, state: str) -> None:
        with self._lock:
            self._voice_state = state

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        log.info("Whisplay dashboard started")

    def stop(self) -> None:
        self._stop.set()
        if self._chip is not None:
            try:
                lgpio.gpiochip_close(self._chip)
            except Exception:  # noqa: BLE001
                pass
            self._chip = None

    # ── internals ───────────────────────────────────────────────────────
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick_display()
                self._tick_button()
            except Exception as e:  # noqa: BLE001
                log.debug("dashboard tick err: %s", e)
            self._stop.wait(_UPDATE_INTERVAL_S)

    def _tick_display(self) -> None:
        if self._display is None:
            return
        with self._lock:
            voice = self._voice_state
        cpu = psutil_cpu_percent()
        mem = psutil_mem_percent()
        tmp = psutil_temp()
        ip = _local_ip()
        hailo = "NPU OK" if _hailo_ok() else "NPU ERR"
        title = voice.upper()
        text = f"CPU {cpu:.0f}% RAM {mem:.0f}% {tmp:.0f}C {hailo} {ip}"
        try:
            self._display.update(text, title)
        except Exception as e:  # noqa: BLE001
            log.debug("display update err: %s", e)

    def _tick_button(self) -> None:
        if self._chip is None or self._on_ptt is None:
            return
        try:
            level = lgpio.gpio_read(self._chip, self._button_gpio)
        except Exception as e:  # noqa: BLE001
            log.debug("button read err: %s", e)
            return
        now = time.time()
        # Active-low: 0 = pressed. Debounce and require release before the
        # next press so a single click fires once.
        if level == 0 and (now - self._button_pressed_at) > _DEBOUNCE_S:
            self._button_pressed_at = now
            try:
                self._on_ptt()
            except Exception as e:  # noqa: BLE001
                log.warning("ptt callback err: %s", e)


# ── tiny psutil-free helpers (the GUI's venv has psutil, but keep this
#    module importable even in a minimal environment) ────────────────────
def psutil_cpu_percent() -> float:
    try:
        import psutil
        return psutil.cpu_percent(interval=None) or 0.0
    except Exception:  # noqa: BLE001
        try:
            with open("/proc/stat", "r") as f:
                line = f.readline().split()
            total = sum(int(x) for x in line[1:])
            idle = int(line[4])
            return max(0.0, min(100.0, 100.0 * (1.0 - idle / max(total, 1))))
        except Exception:  # noqa: BLE001
            return 0.0


def psutil_mem_percent() -> float:
    try:
        import psutil
        return psutil.virtual_memory().percent
    except Exception:  # noqa: BLE001
        try:
            with open("/proc/meminfo", "r") as f:
                lines = {k: int(v) for k, v in
                         (ln.split(":") for ln in f if ":" in ln)}
            total = lines.get("MemTotal", 0)
            avail = lines.get("MemAvailable", total)
            return 100.0 * (1.0 - avail / max(total, 1)) if total else 0.0
        except Exception:  # noqa: BLE001
            return 0.0


def psutil_temp() -> float:
    try:
        import psutil
        temps = psutil.sensors_temperatures()
        for name in ("cpu_thermal", "cpu-thermal", "k10temp", "coretemp"):
            if name in temps and temps[name]:
                return temps[name][0].current
        for entries in temps.values():
            if entries:
                return entries[0].current
    except Exception:  # noqa: BLE001
        pass
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            return int(f.read().strip()) / 1000.0
    except Exception:  # noqa: BLE001
        return 0.0
