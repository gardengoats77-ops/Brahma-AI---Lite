"""Whisplay HAT live dashboard for the REX desktop GUI.

Hardware-ownership model (Matched to the WORKING Pi 5 setup):

The PiSugar WhiPlay HAT panel is owned by a single resident process
(`whisplay-daemon.service`, a *system* unit). That daemon runs the real
vendor driver (consumer strings ``whisplay`` on GPIO22/23/24/25 and
``whisplay-btn`` on GPIO17 confirm this on the live Pi). Apps must NOT open
the board/GPIO/SPI directly -- doing that collides with the daemon's lines
and fails with "GPIO busy" / black screen.

This dashboard therefore talks to the daemon through its UNIX socket
(`/tmp/whisplay-daemon.sock`) using a thin inline equivalent of the
official :class:`WhiDaemonProxy` protocol: register the app, acquire
foreground, mmap the shared framebuffer, then paint stats frames with
``draw_image()`` and flip the RGB LED + backlight over the socket. If the
daemon socket is absent or unresponsive, it degrades to the previous
direct-driver mode so a lone board (no daemon) still works -- otherwise it
logs a no-op and the GUI never blocks on this module.

Exactly one process must own the panel. The daemon owns it; this dashboard
is that daemon's client.
"""

from __future__ import annotations

import json
import logging
import mmap
import os
import shutil
import socket
import threading
import time
from typing import Callable, Optional

import numpy as np

_SOCKET_PATH = "/tmp/whisplay-daemon.sock"

log = logging.getLogger("whisplay.dashboard")

try:
    # Direct vendor driver is only used when the daemon socket is absent.
    from pi.whisplay_board import WhisplayBoard  # type: ignore
    _BOARD_AVAILABLE = True
    _BOARD_IMPORT_ERR = None
except Exception as e:  # noqa: BLE001
    _BOARD_AVAILABLE = False
    _BOARD_IMPORT_ERR = e

W = 240
H = 280
_BG = (7, 11, 18)
_FG = (0, 255, 160)
_DIM = (120, 140, 160)
_ERR = (255, 80, 80)
_OK = (40, 220, 120)
_FONT = ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 26)
_FONT_BIG = ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 36)
# Rotation applied to the rendered frame so content reads correctly for the
# HAT's physical mounting (native panel is 240x280 portrait).
_ROTATE_DEG = 90


# ─── Daemon socket client (inline, no GPIO ownership) ────────────────────────
class _DaemonClient:
    """Minimal WhiPlay daemon-socket client: paint frames over the socket."""

    LCD_WIDTH = W
    LCD_HEIGHT = H

    def __init__(self, socket_path: str = _SOCKET_PATH, app_id: str = "rex-gui"):
        self.socket_path = socket_path
        self.app_id = app_id
        self._mmap: Optional[mmap.mmap] = None
        self._fb = None
        self._fb_path = None
        self._stride = W * 2
        self._available = False

    def _send(self, cmd: str, payload: Optional[dict] = None) -> dict:
        body = {"version": 1, "cmd": cmd, "payload": payload or {}}
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2.0)
            client.connect(self.socket_path)
            client.sendall((json.dumps(body) + "\n").encode("utf-8"))
            line = client.makefile("r").readline().strip()
            if not line:
                raise RuntimeError("empty response")
            resp = json.loads(line)
            if not resp.get("ok"):
                raise RuntimeError(resp.get("error", "daemon request failed"))
            return resp

    def connect(self) -> bool:
        """Register + acquire foreground; attach the shared framebuffer."""
        if not os.path.exists(self.socket_path):
            return False
        try:
            self._send("health.ping")
            self._send("app.register", {"app_id": self.app_id})
            focus = self._send(
                "app.focus.acquire", {"app_id": self.app_id}
            )
            session_token = focus["payload"]["session_token"]
            fb = self._send(
                "framebuffer.acquire",
                {
                    "app_id": self.app_id,
                    "session_token": session_token,
                },
            )["payload"]
            handle = fb["buffer_handle"]
            stride = int(fb.get("stride", self._stride))
            if self._fb:
                self._fb.close()
            self._fb = open(handle, "r+b")
            self._mmap = mmap.mmap(self._fb.fileno(), 0)
            self._stride = stride
            self._session_token = session_token
            self._available = True
            return True
        except Exception as e:  # noqa: BLE001
            log.debug("whisplay daemon connect failed: %s", e)
            self.teardown()
            return False

    def teardown(self):
        self._available = False
        try:
            if self._mmap is not None:
                self._mmap.close()
        except Exception:
            pass
        self._mmap = None
        try:
            if self._fb is not None:
                self._fb.close()
        except Exception:
            pass
        self._fb = None

    def draw(self, x: int, y: int, width: int, height: int, data: bytes):
        if self._mmap is None or not self._available:
            raise RuntimeError("daemon framebuffer not attached")
        row_bytes = width * 2
        if row_bytes > self._stride:
            raise ValueError("row exceeds stride")
        for row in range(height):
            dst = (y + row) * self._stride + x * 2
            src = row * row_bytes
            self._mmap[dst:dst + row_bytes] = data[src:src + row_bytes]

    def fill(self, rgb565: int):
        hi = (rgb565 >> 8) & 0xFF
        lo = rgb565 & 0xFF
        if self._mmap is not None and self._available:
            self._mmap[:] = bytes([hi, lo]) * (W * H)

    def led(self, r: int, g: int, b: int):
        try:
            self._send("led.set", {"r": int(r), "g": int(g), "b": int(b)})
        except Exception:
            pass

    def button_pressed(self) -> bool:
        """Poll the physical button through the daemon (socket, no GPIO)."""
        try:
            resp = self._send("button.get_state", {})
            return bool(resp["payload"]["pressed"])
        except Exception:
            return False

    def health(self) -> bool:
        try:
            return self._send("health.ping") is not None
        except Exception:
            return False


def _rgb565(image) -> bytes:
    """Convert a Pillow RGB image to raw big-endian RGB565 bytes."""
    arr = np.asarray(image.convert("RGB"), dtype=np.uint16)
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    rgb = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    return rgb.astype(">u2").tobytes()


def _cpu() -> float:
    try:
        s = [int(x) for x in __import__("os").popen(
            "grep 'cpu ' /proc/stat | head -1").read().split()[1:5]]
        time.sleep(0.2)
        s2 = [int(x) for x in __import__("os").popen(
            "grep 'cpu ' /proc/stat | head -1").read().split()[1:5]]
        d = [(a - b) for a, b in zip(s2, s)]
        total = sum(d)
        return 100.0 * (1.0 - d[3] / total) if total else 0.0
    except Exception:
        return 0.0


def _temp() -> float:
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as fh:
            return float(fh.read().strip()) / 1000.0
    except Exception:
        return 0.0


def _hailo() -> bool:
    return shutil.which("hailortcli") is not None


def _lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "n/a"


class WhisplayDashboard:
    """Background thread driving the Whisplay TFT + PTT through the daemon."""

    def __init__(self, on_push_to_talk: Optional[Callable[[], None]] = None,
                 poll: float = 2.0):
        self._on_ptt = on_push_to_talk
        self._poll = poll
        self._voice_state = "IDLE"
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._mode: Optional[str] = None  # "daemon" | "direct" | None
        self._client: Optional[_DaemonClient] = None
        self._board: Optional[WhisplayBoard] = None
        self._btn_was_down = False

        # Preferred: daemon socket (matches the working Pi architecture).
        client = _DaemonClient()
        if client.connect():
            self._client = client
            self._mode = "daemon"
            log.info("whisplay dashboard via daemon socket")
            return

        # Fallback: direct board, only when no daemon is present.
        if _BOARD_AVAILABLE:
            try:
                self._board = WhisplayBoard()
                self._board.set_backlight(0)
                self._board.set_rgb(0, 255, 0)
                if self._on_ptt:
                    self._board.on_button_press(self._ptt_pressed)
                self._mode = "direct"
                log.info("whisplay dashboard via direct driver (no daemon)")
            except Exception as e:  # noqa: BLE001
                log.warning("whisplay board init failed: %s", e)
                self._board = None
        elif _BOARD_IMPORT_ERR:
            log.debug("whisplay board driver unavailable: %s", _BOARD_IMPORT_ERR)

    @property
    def available(self) -> bool:
        return self._mode is not None

    def start(self):
        if not self.available:
            log.info("whisplay dashboard unavailable — running headless")
            return
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="whisplay-dash")
        self._thread.start()
        self.render()
        if self._mode == "daemon" and self._client is not None:
            self._client.led(0, 255, 0)

    def stop(self):
        self._running = False

    def set_voice_state(self, label: str):
        self._voice_state = label
        self.render()

    def _ptt_pressed(self):
        if self._on_ptt:
            try:
                self._on_ptt()
            except Exception as e:  # noqa: BLE001
                log.warning("PTT callback error: %s", e)

    def _loop(self):
        while self._running:
            try:
                self.render()
            except Exception as e:  # noqa: BLE001
                log.warning("whishered render error: %s", e)
            self._poll_button()
            time.sleep(self._poll)

    def _poll_button(self):
        """Debounced PTT edge detection for daemon mode (button owned by
        the daemon; we read state over its socket)."""
        if self._on_ptt is None or self._mode != "daemon" or \
                self._client is None:
            return
        try:
            down = self._client.button_pressed()
            if down and not self._btn_was_down:
                self._ptt_pressed()
            self._btn_was_down = down
        except Exception:  # noqa: BLE001
            pass

    def render(self):
        if not self.available:
            return
        try:
            data = self._frame()
            if self._mode == "daemon" and self._client is not None:
                self._client.draw(0, 0, W, H, data)
            elif self._mode == "direct" and self._board is not None:
                self._board.draw_image(0, 0, W, H, data)
        except Exception as e:  # noqa: BLE001
            log.warning("whisplay paint failed: %s", e)

    def _frame(self) -> bytes:
        """Render the stats card landscape, then rotate into the portrait
        framebuffer so it reads correctly on the physically-rotated HAT."""
        from PIL import Image, ImageDraw, ImageFont
        # Landscape canvas (280 wide x 240 tall) -> rotated 90deg -> 240x280.
        LW, LH = H, W
        img = Image.new("RGB", (LW, LH), _BG)
        d = ImageDraw.Draw(img)
        try:
            f1 = ImageFont.truetype(*_FONT)
            f2 = ImageFont.truetype(*_FONT_BIG)
        except Exception:
            f1 = f2 = ImageFont.load_default()

        d.text((12, 2), "REX", font=f2, fill=_FG)
        state = self._voice_state
        voice_colour = _OK if ("LISTEN" in state or "SPEAK" in state) else _DIM
        d.text((12, 44), f"voice: {state}", font=f1, fill=voice_colour)
        d.text((12, 76), f"cpu: {_cpu():4.0f}%", font=f1, fill=_FG)
        d.text((12, 108), f"tmp: {_temp():4.0f}C", font=f1, fill=_FG)
        npx = "ok" if _hailo() else "off"
        d.text((12, 140), f"hailo: {npx}", font=f1,
               fill=_OK if _hailo() else _ERR)
        d.text((12, 172), f"ip: {_lan_ip()}", font=f1, fill=_DIM)
        d.text((12, 204), "<hold PTT>", font=f1, fill=_DIM)

        # Rotate so the content matches the physical HAT orientation.
        img = img.rotate(-_ROTATE_DEG, expand=True)
        return _rgb565(img)


_dash_singleton: Optional[WhisplayDashboard] = None


def get_dashboard(on_push_to_talk=None) -> WhisplayDashboard:
    """Return a process-wide singleton WhisplayDashboard."""
    global _dash_singleton
    if _dash_singleton is None:
        _dash_singleton = WhisplayDashboard(on_push_to_talk=on_push_to_talk)
    return _dash_singleton