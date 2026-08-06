from __future__ import annotations

import json
import html as html_lib
import math
import os
import platform
import random
import re
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

import psutil
from core.error_handler import log_error

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
MODEL_DOWNLOAD_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/1/hand_landmarker.task"
)

from PyQt6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint, QPointF, QRect, QRectF, QSize,
    QParallelAnimationGroup, QSequentialAnimationGroup, QUrl, QThread,
    pyqtProperty,
    pyqtSignal, pyqtSlot, QMetaObject, Q_ARG, QByteArray)
from PyQt6.QtGui import (
    QAction,
    QColor, QFont, QFontMetrics, QIcon, QImage, QLinearGradient,
    QPainter, QPainterPath, QPalette, QPen, QPixmap, QPolygon,
    QRadialGradient, QRegion, QBrush, QTransform, QFontDatabase,
    QDragEnterEvent, QDropEvent, QTextOption)
from PyQt6.QtWidgets import (
    QApplication, QFrame, QGraphicsDropShadowEffect, QGraphicsOpacityEffect,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget, QDialog, QPushButton, QFileDialog, QSlider,
    QStackedWidget, QTextEdit, QToolButton, QMessageBox, QProgressBar,
    QSpinBox, QComboBox, QCheckBox, QTabWidget, QMenu, QSystemTrayIcon, QListWidget, QListWidgetItem, QGridLayout,
    QFormLayout, QGroupBox, QSplitter, QPlainTextEdit,
    QInputDialog, QTextBrowser)

from discord_bot import DiscordBotService
from gesture_utils import estimate_gesture_state
from smart_home import SmartHomeService
from smart_home_page_new import REXHomePage, _DeviceTile
from memory_panel import MemoryPanel
from workspace_store import store as workspace_store

from .styles import (
    C, _base_dir, qcol, _logo_icon, _logo_pixmap, _framed_logo,
    _icon_pixmap, _attach_pulse_glow, _fmt_time_stamp, _markdown_to_html,
    _file_category, _fmt_size)
from .gesture_canvas import _GestureRenderCanvas
from .windows_integration import (
    _quiet_run, _quote_cmd_arg, _hidden_launch_args, _startup_run_value,
    _startup_registry_key, _current_boot_stamp, _launched_from_windows_startup,
    _default_app_settings, _default_discord_settings, _camera_available,
    _active_net_label, _OS)


class BackgroundWidget(QWidget):
    def __init__(self, image_path: Path | str | None = None, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self._image_path = Path(image_path) if image_path else None
        self._background_pixmap = None
        self._load_background()

    def _load_background(self) -> None:
        if not self._image_path:
            return
        try:
            pix = QPixmap(str(self._image_path))
            if not pix.isNull():
                self._background_pixmap = pix
        except Exception:
            self._background_pixmap = None

    def paintEvent(self, event):
        if not self._background_pixmap:
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = self.rect()
        pix = self._background_pixmap.scaled(
            rect.size(),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation)
        x = (rect.width() - pix.width()) // 2
        y = (rect.height() - pix.height()) // 2
        painter.drawPixmap(x, y, pix)
        painter.fillRect(rect, QColor(2, 3, 5, 28))
        painter.end()
        return



class RemoteKeyOverlay(QWidget):
    closed = pyqtSignal()

    def __init__(self, url: str, key: str, auto: str, manual: str, parent=None):
        super().__init__(parent)
        self._on_new_key = None
        self._manual_url = manual or url
        self._auto_login_url = auto or url
        self._expiry = time.time() + 600

        # larger opaque panel with neon red glow
        frame = QFrame(self)
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(18, 20, 18, 18)
        lay.setSpacing(12)
        # Make overlay large and opaque so it pops
        try:
            self.setFixedSize(560, 680)
            frame.setFixedSize(self.size())
        except Exception:
            self.setFixedSize(520, 640)
            frame.setFixedSize(self.size())
        frame.setStyleSheet(f"""
            QFrame {{
                background: rgba(8,10,12,245);
                border: 2px solid {C.PRI};
                border-radius: 16px;
            }}
        """)
        # neon glow effect
        try:
            glow = QGraphicsDropShadowEffect(self)
            glow.setBlurRadius(48)
            glow.setColor(QColor(255,69,69,200))
            glow.setOffset(0, 0)
            frame.setGraphicsEffect(glow)
        except Exception as _e:
            log_error(_e, context="ui", severity="debug")

        title = QLabel("Mobile Connect")
        title.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        title.setStyleSheet("color: #fff; background: transparent;")
        lay.addWidget(title)

        subtitle = QLabel("Scan the QR code with your phone to connect.")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setWordWrap(True)
        subtitle.setFont(QFont("Segoe UI", 9))
        subtitle.setStyleSheet(f"color: {C.TEXT_DIM};")
        lay.addWidget(subtitle)

        self._qr_label = QLabel()
        self._qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_label.setFixedSize(240, 240)
        self._qr_label.setStyleSheet("background: white; border-radius: 16px; padding: 8px;")
        qr_row = QHBoxLayout()
        qr_row.addStretch()
        qr_row.addWidget(self._qr_label)
        qr_row.addStretch()
        lay.addLayout(qr_row)

        manual_hint = QLabel("Manual address")
        manual_hint.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        manual_hint.setStyleSheet(f"color: {C.TEXT_DIM};")
        lay.addWidget(manual_hint)

        self._url_lbl = QLabel(self._manual_url)
        self._url_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._url_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._url_lbl.setFont(QFont("Consolas", 9))
        self._url_lbl.setStyleSheet(f"color: {C.TEXT_MED};")
        lay.addWidget(self._url_lbl)

        self._key_lbl = QLabel(key)
        self._key_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._key_lbl.setFont(QFont("Consolas", 34, QFont.Weight.Black))
        self._key_lbl.setStyleSheet(f"""
            color: {C.WHITE};
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(40,6,6,220), stop:1 rgba(60,8,8,220));
            border: 2px solid {C.PRI};
            border-radius: 12px;
            padding: 12px;
            letter-spacing: 12px;
            font-weight: 900;
        """)
        lay.addWidget(self._key_lbl)

        self._timer_lbl = QLabel("")
        self._timer_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._timer_lbl.setFont(QFont("Segoe UI", 8))
        self._timer_lbl.setStyleSheet(f"color: {C.TEXT_DIM};")
        lay.addWidget(self._timer_lbl)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self._new_btn = QPushButton("NEW KEY")
        self._new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._new_btn.setFixedHeight(34)
        self._new_btn.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self._new_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,69,69,22);
                color: {C.WHITE};
                border: 1px solid {C.PRI};
                border-radius: 8px;
            }}
            QPushButton:hover {{ background: rgba(255,69,69,44); }}
        """)
        self._new_btn.clicked.connect(self._refresh_key)
        btn_row.addWidget(self._new_btn)

        close_btn = QPushButton("CLOSE")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setFixedHeight(34)
        close_btn.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(12,14,18,238);
                color: {C.TEXT_MED};
                border: 1px solid {C.BORDER_B};
                border-radius: 8px;
            }}
            QPushButton:hover {{ color: {C.WHITE}; border: 1px solid {C.PRI}; }}
        """)
        close_btn.clicked.connect(self._do_close)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        self._ctimer = QTimer(self)
        self._ctimer.timeout.connect(self._tick)
        self._ctimer.start(1000)
        self._update_qr(self._auto_login_url)
        self._tick()

        # Ensure the overlay has a sensible default size so positioning works.
        self.adjustSize()
        try:
            self.setFixedSize(max(360, self.width()), max(360, self.height()))
        except Exception:
            self.setFixedSize(420, 520)

    def set_new_key_callback(self, fn) -> None:
        self._on_new_key = fn

    def _update_qr(self, url: str) -> None:
        if not url:
            self._qr_label.setText("NO URL")
            return
        try:
            import qrcode
            from io import BytesIO
            qr = qrcode.QRCode(box_size=5, border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = BytesIO()
            img.save(buf, format="PNG")
            pix = QPixmap()
            pix.loadFromData(buf.getvalue())
            self._qr_label.setPixmap(pix.scaled(172, 172, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        except ImportError:
            self._qr_label.setText("Install\nqrcode[pil]")
            self._qr_label.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            self._qr_label.setStyleSheet("color: #111; background: white; border-radius: 12px; padding: 6px;")
        except Exception:
            self._qr_label.setText("QR failed")

    def _tick(self):
        remaining = max(0, int(self._expiry - time.time()))
        mins, secs = divmod(remaining, 60)
        self._timer_lbl.setText(f"Key expires in {mins:02d}:{secs:02d}")
        if remaining <= 0:
            self._do_close()

    def mark_connected(self) -> None:
        self._ctimer.stop()
        self._key_lbl.setText("CONNECTED")
        self._key_lbl.setStyleSheet(f"""
            color: {C.GREEN};
            background: rgba(55,255,95,20);
            border: 1px solid rgba(55,255,95,150);
            border-radius: 10px;
            padding: 8px;
            letter-spacing: 4px;
        """)
        self._qr_label.setText("OK")
        self._qr_label.setFont(QFont("Segoe UI", 34, QFont.Weight.Black))
        self._qr_label.setStyleSheet("color: #37ff5f; background: #041006; border-radius: 12px;")
        self._timer_lbl.setText("Phone connected. REX remote is ready.")

    def _refresh_key(self):
        if not self._on_new_key:
            return
        result = self._on_new_key()
        if not result:
            return
        url = result[0]
        key = result[1]
        auto = result[2] if len(result) >= 3 else url
        manual = result[3] if len(result) >= 4 else url
        self._manual_url = manual or url
        self._auto_login_url = auto or url
        self._url_lbl.setText(self._manual_url)
        self._key_lbl.setText(key)
        self._key_lbl.setStyleSheet(f"""
            color: {C.WHITE};
            background: rgba(255,69,69,28);
            border: 1px solid {C.PRI};
            border-radius: 10px;
            padding: 8px;
            letter-spacing: 9px;
        """)
        self._update_qr(self._auto_login_url)
        self._expiry = time.time() + 600
        self._ctimer.start(1000)
        self._tick()

    def _do_close(self):
        self._ctimer.stop()
        self.hide()
        self.closed.emit()



class _SysMetrics:
    def __init__(self):
        self.cpu  = 0.0
        self.mem  = 0.0
        self.net  = 0.0   
        self.gpu  = -1.0  
        self.tmp  = -1.0  
        self._lock = threading.Lock()
        self._last_net = psutil.net_io_counters()
        self._last_net_t = time.time()
        self._running = True
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        while self._running:
            try:
                self._update()
            except Exception as _e:
                log_error(_e, context="ui", severity="debug")
            time.sleep(1.5)

    def _update(self):
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent

        nc  = psutil.net_io_counters()
        now = time.time()
        dt  = now - self._last_net_t
        if dt > 0:
            sent = (nc.bytes_sent - self._last_net.bytes_sent) / dt
            recv = (nc.bytes_recv - self._last_net.bytes_recv) / dt
            net  = (sent + recv) / (1024 * 1024)
        else:
            net = 0.0
        self._last_net   = nc
        self._last_net_t = now

        gpu = self._get_gpu()

        tmp = self._get_temp()

        with self._lock:
            self.cpu = cpu
            self.mem = mem
            self.net = net
            self.gpu = gpu
            self.tmp = tmp

    def _get_gpu(self) -> float:
        # NVIDIA
        try:
            r = _quiet_run(
                ["nvidia-smi", "--query-gpu=utilization.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2
            )
            if r.returncode == 0:
                vals = [float(v.strip()) for v in r.stdout.strip().split("\n") if v.strip()]
                if vals:
                    return sum(vals) / len(vals)
        except Exception as _e:
            log_error(_e, context="ui", severity="debug")

        # AMD (Linux)
        if _OS == "Linux":
            try:
                r = _quiet_run(
                    ["rocm-smi", "--showuse", "--csv"],
                    capture_output=True, text=True, timeout=2
                )
                if r.returncode == 0:
                    for line in r.stdout.strip().split("\n"):
                        parts = line.split(",")
                        if len(parts) >= 2:
                            try:
                                return float(parts[1].strip().replace("%", ""))
                            except ValueError:
                                pass
            except Exception as _e:
                log_error(_e, context="ui", severity="debug")

            # Intel GPU (Linux)
            try:
                r = _quiet_run(
                    ["intel_gpu_top", "-J", "-s", "500"],
                    capture_output=True, text=True, timeout=1
                )
                if r.returncode == 0 and "Render/3D" in r.stdout:
                    import re
                    m = re.search(r'"busy":\s*([\d.]+)', r.stdout)
                    if m:
                        return float(m.group(1))
            except Exception as _e:
                log_error(_e, context="ui", severity="debug")

        # macOS - powermetrics (GPU Engine)
        if _OS == "Darwin":
            try:
                r = _quiet_run(
                    ["sudo", "-n", "powermetrics", "-n", "1", "-i", "500",
                     "--samplers", "gpu_power"],
                    capture_output=True, text=True, timeout=2
                )
                if r.returncode == 0 and "GPU" in r.stdout:
                    import re
                    m = re.search(r'GPU\s+Active:\s+([\d.]+)%', r.stdout)
                    if m:
                        return float(m.group(1))
            except Exception as _e:
                log_error(_e, context="ui", severity="debug")

        return -1.0

    def _get_temp(self) -> float:
        try:
            temps = psutil.sensors_temperatures()
            candidates = ["coretemp", "k10temp", "cpu_thermal", "acpitz",
                          "cpu-thermal", "zenpower", "it8688"]
            for name in candidates:
                if name in temps:
                    entries = temps[name]
                    if entries:
                        return entries[0].current
            for entries in temps.values():
                if entries:
                    return entries[0].current
        except Exception as _e:
            log_error(_e, context="ui", severity="debug")
        if _OS == "Darwin":
            try:
                r = _quiet_run(
                    ["osx-cpu-temp"], capture_output=True, text=True, timeout=2
                )
                if r.returncode == 0:
                    import re
                    m = re.search(r"([\d.]+)", r.stdout)
                    if m:
                        return float(m.group(1))
            except Exception as _e:
                log_error(_e, context="ui", severity="debug")

        if _OS == "Windows":
            try:
                r = _quiet_run(
                    ["powershell", "-Command",
                     "(Get-WmiObject MSAcpi_ThermalZoneTemperature -Namespace root/wmi).CurrentTemperature"],
                    capture_output=True, text=True, timeout=3
                )
                if r.returncode == 0 and r.stdout.strip():
                    raw = float(r.stdout.strip().split("\n")[0])
                    return (raw / 10.0) - 273.15
            except Exception as _e:
                log_error(_e, context="ui", severity="debug")

        return -1.0

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "cpu": self.cpu,
                "mem": self.mem,
                "net": self.net,
                "gpu": self.gpu,
                "tmp": self.tmp,
            }


_metrics = _SysMetrics()

_CAM_OK_CACHE = {"ok": False, "ts": 0.0}



class GestureCameraPreview(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("GestureCameraPreview")
        self.setStyleSheet(
            f"""
            QFrame#GestureCameraPreview {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(9, 10, 14, 255),
                    stop:1 rgba(3, 4, 7, 255));
                border: 1px solid rgba(255, 69, 69, 0.24);
                border-radius: 16px;
            }}
            QLabel {{ background: transparent; }}
            """
        )
        self._cap = None
        self._timer = None
        self._hands = None
        self._use_tasks_api = False
        self._vision_module = None
        self._prev_pinch = False
        self._smoothed_cursor: tuple[float, float] | None = None
        self._smoothed_screen: tuple[float, float] | None = None
        self._smoothed_landmarks: list[tuple[float, float, float]] | None = None
        self._search_phase = 0
        self._smoothing_alpha = 0.8
        self._gesture_canvas_alpha = 0.0
        self._sensitivity = 1.0
        self._sensitivity_levels = {"Low": 0.8, "Medium": 1.0, "High": 1.4}
        self._invert_cursor_x = False
        self._invert_cursor_y = False
        self._cursor_calibration_x_min: float | None = None
        self._cursor_calibration_x_max: float | None = None
        self._cursor_calibration_y_min: float | None = None
        self._cursor_calibration_y_max: float | None = None
        self._last_screen_pos: tuple[int, int] | None = None
        self._cursor_anchor: tuple[float, float] | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(10)

        title = QLabel("HAND TRACKING")
        title.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.WHITE}; letter-spacing: 1px;")
        header_row.addWidget(title)
        header_row.addStretch(1)

        self._status_dot = QLabel()
        self._status_dot.setFixedSize(12, 12)
        self._status_dot.setStyleSheet("border-radius: 6px; background: #ffb347;")
        header_row.addWidget(self._status_dot)

        self._status_text = QLabel("SEARCHING")
        self._status_text.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self._status_text.setStyleSheet("color: #ffb347;")
        header_row.addWidget(self._status_text)

        self._sensitivity_select = QComboBox()
        self._sensitivity_select.addItems(["Low", "Medium", "High"])
        self._sensitivity_select.setCurrentText("Medium")
        self._sensitivity_select.setFixedWidth(84)
        self._sensitivity_select.setStyleSheet(
            "QComboBox { background: rgba(255,255,255,0.05); color: #f4f6f8; border: 1px solid rgba(255,69,69,0.24); border-radius: 8px; padding: 4px 8px; }"
            "QComboBox::drop-down { border: none; }")
        self._sensitivity_select.currentTextChanged.connect(self._set_sensitivity_level)
        header_row.addWidget(self._sensitivity_select)
        lay.addLayout(header_row)

        self._hand_canvas = _GestureRenderCanvas(self)
        self._hand_canvas.setFixedHeight(220)
        self._hand_canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay.addWidget(self._hand_canvas)

        self._status_hint_label = QLabel("Initializing hand detection...")
        self._status_hint_label.setFont(QFont("Segoe UI", 8))
        self._status_hint_label.setStyleSheet(f"color: {C.TEXT_DIM};")
        self._status_hint_label.setWordWrap(True)
        lay.addWidget(self._status_hint_label)

        footer = QGridLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setHorizontalSpacing(16)
        footer.setVerticalSpacing(8)

        self._status_value = QLabel("Searching")
        self._confidence_value = QLabel("0%")
        self._gesture_value = QLabel("None")
        self._cursor_value = QLabel("Inactive")

        for idx, (label_text, value_label) in enumerate([
            ("Status", self._status_value),
            ("Confidence", self._confidence_value),
            ("Gesture", self._gesture_value),
            ("Cursor", self._cursor_value),
        ]):
            label = QLabel(label_text.upper())
            label.setFont(QFont("Segoe UI", 7, QFont.Weight.DemiBold))
            label.setStyleSheet(f"color: {C.TEXT_DIM};")
            value_label.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            value_label.setStyleSheet(f"color: {C.WHITE};")
            footer.addWidget(label, idx, 0)
            footer.addWidget(value_label, idx, 1)

        lay.addLayout(footer)

        self._expanded_height = 320
        self._collapsed_height = 64

        try:
            self.setFixedHeight(self._expanded_height)
        except Exception as _e:
            log_error(_e, context="ui", severity="debug")

        self._set_status("Searching for hand...", "searching")
        self._start_camera()

    def closeEvent(self, event):
        self._stop_camera()
        super().closeEvent(event)

    def mousePressEvent(self, event):
        try:
            if event.button() == Qt.MouseButton.LeftButton:
                self._toggle_camera()
                event.accept()
                return
        except Exception as _e:
            log_error(_e, context="ui", severity="debug")
        return super().mousePressEvent(event)

    def _set_status(self, text: str, level: str = "searching"):
        self._status_hint_label.setText(text)
        self._status_value.setText(level.capitalize())
        colors = {
            "tracking": C.GREEN,
            "searching": "#ffb347",
            "lost": C.RED,
        }
        color = colors.get(level, "#ffb347")
        self._status_dot.setStyleSheet(f"border-radius: 6px; background: {color};")
        self._status_text.setText(level.upper())
        self._status_text.setStyleSheet(f"color: {color};")

    def _start_camera(self):
        if self._cap is not None:
            return
        try:
            import cv2
            cap = cv2.VideoCapture(0, cv2.CAP_DSHOW if hasattr(cv2, "CAP_DSHOW") else cv2.CAP_ANY)
            try:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
            except Exception as _e:
                log_error(_e, context="ui", severity="debug")
            if not cap.isOpened():
                cap.release()
                raise RuntimeError("camera unavailable")
            self._cap = cap
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._tick)
            self._timer.start(30)
            self._set_status("Camera ready. Move your hand to steer the cursor.", "searching")
            try:
                import pyautogui
                pyautogui.FAILSAFE = False
            except Exception as _e:
                log_error(_e, context="ui", severity="debug")
        except Exception as exc:
            self._set_status(f"Gesture camera is offline: {exc}", "lost")

        if self._cap is not None:
            try:
                self.setFixedHeight(self._expanded_height)
            except Exception as _e:
                log_error(_e, context="ui", severity="debug")

    def _stop_camera(self):
        if self._timer is not None:
            self._timer.stop()
            self._timer.deleteLater()
            self._timer = None
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception as _e:
                log_error(_e, context="ui", severity="debug")
            self._cap = None
        if self._hands is not None:
            try:
                self._hands.close()
            except Exception as _e:
                log_error(_e, context="ui", severity="debug")
            self._hands = None

        try:
            self.setFixedHeight(self._collapsed_height)
        except Exception as _e:
            log_error(_e, context="ui", severity="debug")
        self._set_status("Camera stopped", "lost")

    def _download_hand_landmarker_model(self, model_path: Path) -> bool:
        temp_path = model_path.with_suffix(model_path.suffix + ".download")
        try:
            import urllib.request

            self._set_status("Downloading gesture model...", "searching")
            model_path.parent.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(MODEL_DOWNLOAD_URL, timeout=60) as response:
                with open(temp_path, "wb") as out_file:
                    while True:
                        chunk = response.read(8192)
                        if not chunk:
                            break
                        out_file.write(chunk)
            temp_path.replace(model_path)
            return True
        except Exception as exc:
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except Exception as _e:
                log_error(_e, context="ui", severity="debug")
            self._set_status(
                f"Gesture model download failed: {exc}. "
                f"Put hand_landmarker.task into {model_path.parent} and restart.",
                "lost")
            return False

    def _tick(self):
        if self._cap is None:
            return
        try:
            ret, frame = self._cap.read()
            if not ret or frame is None:
                self._set_status("Camera feed dropped. Trying again…", "lost")
                return
            self._process_frame(frame)
        except Exception as exc:
            self._set_status(f"Gesture camera error: {exc}", "lost")

    def _process_frame(self, frame):
        try:
            import cv2
        except Exception as exc:
            self._set_status(f"Gesture camera unavailable: {exc}", "lost")
            return

        import importlib

        mp = None
        try:
            mp = importlib.import_module("mediapipe")
        except Exception as _e:
            log_error(_e, context="ui", severity="debug")

        if mp is None:
            self._set_status(
                "Gesture camera unavailable: mediapipe not found. "
                "Install it into the app venv: .venv\\Scripts\\python.exe -m pip install mediapipe",
                "lost")
            return

        if self._hands is None:
            HandsClass = None
            try:
                solutions = getattr(mp, "solutions", None)
                if solutions is not None and hasattr(solutions, "hands"):
                    HandsClass = solutions.hands.Hands
            except Exception:
                HandsClass = None

            if HandsClass is not None:
                try:
                    self._hands = HandsClass(
                        static_image_mode=False,
                        max_num_hands=1,
                        min_detection_confidence=0.5,
                        min_tracking_confidence=0.5)
                    self._use_tasks_api = False
                except Exception as exc:
                    self._set_status(f"Gesture init error: {exc}", "lost")
                    return
            else:
                vision = None
                try:
                    vision = importlib.import_module("mediapipe.tasks.python.vision")
                except Exception:
                    vision = None

                if vision is None or not hasattr(vision, "HandLandmarker"):
                    self._set_status(
                        "Gesture camera unavailable: mediapipe Tasks API not available. "
                        "Install mediapipe into the app venv and restart.",
                        "lost")
                    return

                model_dir = CONFIG_DIR / "models"
                model_dir.mkdir(parents=True, exist_ok=True)
                model_path = model_dir / "hand_landmarker.task"
                if not model_path.exists():
                    self._set_status("Downloading model", "searching")
                    if not self._download_hand_landmarker_model(model_path):
                        return

                try:
                    self._hands = vision.HandLandmarker.create_from_model_path(str(model_path))
                    self._use_tasks_api = True
                    self._vision_module = vision
                except Exception as exc:
                    self._set_status(f"Gesture init error: {exc}", "lost")
                    return

        height, width = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        landmarks: list[tuple[float, float, float]] = []
        confidence = 0.0

        if self._use_tasks_api:
            try:
                import numpy as np
                image_lib = importlib.import_module("mediapipe.tasks.python.vision.core.image")
                mp_image = image_lib.Image(image_lib.ImageFormat.SRGB, np.ascontiguousarray(rgb))
                results = self._hands.detect(mp_image)
            except Exception as exc:
                self._set_status(f"Gesture task detect error: {exc}", "lost")
                return
            if getattr(results, "hand_landmarks", None):
                hand_landmarks = results.hand_landmarks[0]
                for landmark in hand_landmarks:
                    landmarks.append((landmark.x, landmark.y, landmark.z))
                confidence = 1.0 if landmarks else 0.0
        else:
            results = self._hands.process(rgb)
            if getattr(results, "multi_hand_landmarks", None):
                hand_landmarks = results.multi_hand_landmarks[0]
                for landmark in hand_landmarks.landmark:
                    landmarks.append((landmark.x, landmark.y, landmark.z))
            if getattr(results, "multi_handedness", None) and results.multi_handedness:
                try:
                    confidence = float(results.multi_handedness[0].classification[0].score)
                except Exception:
                    confidence = 1.0 if landmarks else 0.0

        gesture = estimate_gesture_state(landmarks, self._prev_pinch)
        if gesture.get("cursor"):
            norm = self._calibrate_and_smooth_cursor(gesture["cursor"])
            self._move_cursor(norm)
        if gesture.get("pinch_triggered"):
            self._trigger_click()
        self._prev_pinch = bool(gesture.get("pinch", False))

        self._render_hand(landmarks, gesture, confidence)

    def _render_hand(self, landmarks: list[tuple[float, float, float]], gesture: dict, confidence: float):
        has_hand = bool(landmarks and len(landmarks) >= 21)
        if has_hand:
            if self._smoothed_landmarks is None or len(self._smoothed_landmarks) != len(landmarks):
                self._smoothed_landmarks = landmarks.copy()
            else:
                alpha = 0.32
                smoothed: list[tuple[float, float, float]] = []
                for prev, current in zip(self._smoothed_landmarks, landmarks):
                    sx, sy, sz = prev
                    tx, ty, tz = current
                    smoothed.append((sx + alpha * (tx - sx), sy + alpha * (ty - sy), sz + alpha * (tz - sz)))
                self._smoothed_landmarks = smoothed
            self._hand_canvas.set_landmarks(self._smoothed_landmarks)
            self._hand_canvas.set_hand_visible(True)
            self._hand_canvas.set_search_phase(0)
            self._set_status("Hand detected and tracking.", "tracking")
            self._confidence_value.setText(f"{int(confidence * 100)}%")
            self._gesture_value.setText("Pinch" if gesture.get("pinch") else "Open Hand")
            self._cursor_value.setText("Active" if gesture.get("cursor") else "Inactive")
        else:
            self._hand_canvas.set_hand_visible(False)
            self._search_phase = (self._search_phase + 1) % 32
            self._hand_canvas.set_search_phase(self._search_phase)
            self._set_status("Searching for hand...", "searching")
            self._confidence_value.setText("0%")
            self._gesture_value.setText("None")
            self._cursor_value.setText("Inactive")

    def _calibrate_and_smooth_cursor(self, cursor: tuple[float, float]) -> tuple[float, float]:
        raw_x = float(cursor[0])
        raw_y = float(cursor[1])

        if self._invert_cursor_x:
            raw_x = 1.0 - raw_x
        if self._invert_cursor_y:
            raw_y = 1.0 - raw_y

        raw_x = max(0.0, min(1.0, raw_x))
        raw_y = max(0.0, min(1.0, raw_y))

        if self._cursor_anchor is None:
            self._cursor_anchor = (raw_x, raw_y)
            return (raw_x, raw_y)

        anchor_x, anchor_y = self._cursor_anchor
        mapped_x = raw_x
        mapped_y = raw_y

        if self._smoothed_cursor is None:
            self._smoothed_cursor = (mapped_x, mapped_y)
        else:
            sx, sy = self._smoothed_cursor
            a = self._smoothing_alpha
            self._smoothed_cursor = (sx + a * (mapped_x - sx), sy + a * (mapped_y - sy))

        return self._smoothed_cursor

    def _set_sensitivity_level(self, level: str) -> None:
        self._sensitivity = self._sensitivity_levels.get(level, self._sensitivity_levels["Medium"])

    def _move_cursor(self, cursor):
        try:
            import pyautogui
            screen = QApplication.primaryScreen()
            if screen is None:
                return
            geom = screen.geometry()
            if not geom.isValid():
                return

            try:
                s = float(self._sensitivity)
            except Exception:
                s = 1.0

            nx = float(cursor[0])
            ny = float(cursor[1])
            nx = max(0.0, min(1.0, nx))
            ny = max(0.0, min(1.0, ny))

            x = int(geom.left() + nx * geom.width())
            y = int(geom.top() + ny * geom.height())

            if self._smoothed_screen is None:
                self._smoothed_screen = (float(x), float(y))
            else:
                sx, sy = self._smoothed_screen
                a = max(0.18, min(0.36, self._smoothing_alpha))
                self._smoothed_screen = (sx + a * (x - sx), sy + a * (y - sy))

            target_x = int(round(self._smoothed_screen[0]))
            target_y = int(round(self._smoothed_screen[1]))
            dead_zone = max(3, int(min(geom.width(), geom.height()) * 0.004))

            if self._last_screen_pos is not None:
                last_x, last_y = self._last_screen_pos
                if abs(target_x - last_x) <= dead_zone and abs(target_y - last_y) <= dead_zone:
                    return

            self._last_screen_pos = (target_x, target_y)
            try:
                pyautogui.moveTo(target_x, target_y, duration=0)
            except Exception:
                pyautogui.moveTo(target_x, target_y, duration=0.01)
        except Exception as _e:
            log_error(_e, context="ui", severity="debug")

    def _trigger_click(self):
        try:
            import pyautogui
            pyautogui.click(button="left")
        except Exception as _e:
            log_error(_e, context="ui", severity="debug")

    def _toggle_camera(self):
        if self._cap is None:
            self._start_camera()
        else:
            self._stop_camera()



class HudCanvas(QWidget):
    def __init__(self, face_path: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.muted    = False
        self.speaking = False
        self.state    = "INITIALISING"

        self._tick       = 0
        self._scale      = 1.0
        self._tgt_scale  = 1.0
        self._halo       = 55.0
        self._tgt_halo   = 55.0
        self._last_t     = time.time()
        self._scan       = 0.0
        self._scan2      = 180.0
        self._rings      = [0.0, 120.0, 240.0]
        self._pulses: list[float] = [0.0, 50.0, 100.0]
        self._blink      = True
        self._blink_tick = 0
        self._particles: list[list[float]] = []
        self._face_px: QPixmap | None = None
        self._load_face(face_path)

        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(16)

    def _load_face(self, path: str):
        try:
            from PIL import Image, ImageDraw
            import io
            img = Image.open(path).convert("RGBA")
            sz  = min(img.size)
            img = img.resize((sz, sz), Image.LANCZOS)
            mk  = Image.new("L", (sz, sz), 0)
            ImageDraw.Draw(mk).ellipse((2, 2, sz - 2, sz - 2), fill=255)
            img.putalpha(mk)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            px = QPixmap(); px.loadFromData(buf.getvalue())
            self._face_px = px
        except Exception:
            self._face_px = None

    def _step(self):
        self._tick += 1
        now = time.time()
        if now - self._last_t > (0.12 if self.speaking else 0.5):
            if self.speaking:
                self._tgt_scale = random.uniform(1.06, 1.14)
                self._tgt_halo  = random.uniform(145, 190)
            elif self.muted:
                self._tgt_scale = random.uniform(0.998, 1.002)
                self._tgt_halo  = random.uniform(15, 28)
            elif self.state == "LISTENING":
                self._tgt_scale = random.uniform(1.008, 1.018)
                self._tgt_halo  = random.uniform(90, 122)
            elif self.state == "THINKING":
                self._tgt_scale = random.uniform(1.012, 1.024)
                self._tgt_halo  = random.uniform(78, 105)
            elif self.state in ("EXECUTING", "PROCESSING"):
                self._tgt_scale = random.uniform(1.016, 1.032)
                self._tgt_halo  = random.uniform(110, 148)
            else:
                self._tgt_scale = random.uniform(1.001, 1.008)
                self._tgt_halo  = random.uniform(48, 68)
            self._last_t = now

        sp = 0.38 if self.speaking else 0.15
        self._scale += (self._tgt_scale - self._scale) * sp
        self._halo  += (self._tgt_halo  - self._halo)  * sp

        speeds = [1.3, -0.9, 2.0] if self.speaking else [0.55, -0.35, 0.9]
        for i, spd in enumerate(speeds):
            self._rings[i] = (self._rings[i] + spd) % 360

        self._scan  = (self._scan  + (3.0 if self.speaking else 1.3)) % 360
        self._scan2 = (self._scan2 + (-2.0 if self.speaking else -0.75)) % 360

        fw  = min(self.width(), self.height())
        lim = fw * 0.74
        spd = 4.2 if self.speaking else 2.0
        self._pulses = [r + spd for r in self._pulses if r + spd < lim]
        if len(self._pulses) < 3 and random.random() < (0.07 if self.speaking else 0.025):
            self._pulses.append(0.0)

        if self.speaking and random.random() < 0.28:
            cx, cy = self.width() / 2, self.height() / 2
            ang = random.uniform(0, 2 * math.pi)
            r_s = fw * 0.28
            self._particles.append([
                cx + math.cos(ang) * r_s, cy + math.sin(ang) * r_s,
                math.cos(ang) * random.uniform(0.9, 2.4),
                math.sin(ang) * random.uniform(0.9, 2.4) - 0.4, 1.0,
            ])
        self._particles = [
            [p[0]+p[2], p[1]+p[3], p[2]*0.97, p[3]*0.97, p[4]-0.028]
            for p in self._particles if p[4] > 0
        ]

        self._blink_tick += 1
        if self._blink_tick >= 38:
            self._blink = not self._blink
            self._blink_tick = 0
        self.update()

    def _accent_color(self) -> QColor:
        if self.muted:
            return QColor(255, 69, 69, 255)
        if self.speaking:
            return QColor(255, 69, 69, 255)
        if self.state == "LISTENING":
            return QColor(255, 195, 0, 255)
        if self.state == "THINKING":
            return QColor(255, 185, 96, 255)
        if self.state in ("EXECUTING", "PROCESSING"):
            return QColor(255, 69, 69, 255)
        return QColor(255, 69, 69, 255)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(2, 3, 5, 20))

        accent = self._accent_color()
        W, H = self.width(), self.height()
        cx, cy = W / 2, H / 2
        fw = min(W, H)

        # fine tactical grid and red signal noise
        p.setPen(QPen(QColor(255, 255, 255, 8), 1))
        for x in range(0, W, 48):
            for y in range(0, H, 48):
                p.drawPoint(x, y)
        p.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 48), 1))
        for side in (-1, 1):
            base_x = cx + side * fw * 0.37
            base_y = cy
            for i in range(68):
                x = base_x + side * (i * 1.4)
                h = 4 + abs(math.sin(self._tick * 0.04 + i * 0.35)) * (8 + (i % 9) * 2)
                if i % 11 == 0:
                    h *= 1.8
                p.drawLine(QPointF(x, base_y - h), QPointF(x, base_y + h))
            p.drawLine(QPointF(base_x - side * 130, base_y), QPointF(base_x + side * 155, base_y))

        r_face = fw * 0.34

        # halo glow
        for i in range(10):
            r   = r_face * (1.8 - i * 0.08)
            frc = 1.0 - i / 10
            a   = max(0, min(255, int(self._halo * 0.055 * frc)))
            col = QColor(255, 69, 69, a)
            p.setPen(QPen(col, 1.2)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

        # pulse rings
        for pr in self._pulses:
            a   = max(0, int(230 * (1.0 - pr / (fw * 0.74))))
            col = QColor(255, 69, 69, a)
            p.setPen(QPen(col, 1.5)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - pr, cy - pr, pr * 2, pr * 2))

        # spinning arc rings
        for idx, (r_frac, w_r, arc_l, gap) in enumerate(
            [(0.48, 1.9, 115, 78), (0.42, 1.4, 78, 55), (0.35, 1.0, 56, 40)]
        ):
            ring_r = fw * r_frac
            base   = self._rings[idx]
            a_val  = max(0, min(180, int(self._halo * 0.45 * (1.0 - idx * 0.18))))
            col    = QColor(accent.red(), accent.green(), accent.blue(), a_val)
            p.setPen(QPen(col, w_r)); p.setBrush(Qt.BrushStyle.NoBrush)
            angle = base
            rect  = QRectF(cx - ring_r, cy - ring_r, ring_r * 2, ring_r * 2)
            while angle < base + 360:
                p.drawArc(rect, int(angle * 16), int(arc_l * 16))
                angle += arc_l + gap

        # scanners
        sr = fw * 0.50
        sa = min(200, int(self._halo * 0.8))
        ex = 75 if self.speaking else 44
        p.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), sa), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        srect = QRectF(cx - sr, cy - sr, sr * 2, sr * 2)
        p.drawArc(srect, int(self._scan * 16), int(ex * 16))
        p.setPen(QPen(QColor(255, 255, 255, max(28, sa // 4)), 1.0))
        p.drawArc(srect, int(self._scan2 * 16), int(ex * 16))

        # tick marks
        t_out, t_in = fw * 0.497, fw * 0.474
        p.setPen(QPen(QColor(245, 248, 255, 145), 1))
        for deg in range(0, 360, 10):
            rad = math.radians(deg)
            inn = t_in if deg % 30 == 0 else t_in + 6
            p.drawLine(
                QPointF(cx + t_out * math.cos(rad), cy - t_out * math.sin(rad)),
                QPointF(cx + inn  * math.cos(rad), cy - inn  * math.sin(rad)))

        # crosshair
        ch_r, gap_h = fw * 0.51, fw * 0.16
        p.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), int(self._halo * 0.5)), 1))
        p.drawLine(QPointF(cx - ch_r, cy), QPointF(cx - gap_h, cy))
        p.drawLine(QPointF(cx + gap_h, cy), QPointF(cx + ch_r, cy))
        p.drawLine(QPointF(cx, cy - ch_r), QPointF(cx, cy - gap_h))
        p.drawLine(QPointF(cx, cy + gap_h), QPointF(cx, cy + ch_r))

        # corner brackets
        bl = 28
        bc = QColor(accent.red(), accent.green(), accent.blue(), 120)
        hl, hr = cx - fw // 2, cx + fw // 2
        ht, hb = cy - fw // 2, cy + fw // 2
        p.setPen(QPen(bc, 1.5))
        for bx, by, dx, dy in [(hl,ht,1,1),(hr,ht,-1,1),(hl,hb,1,-1),(hr,hb,-1,-1)]:
            p.drawLine(QPointF(bx, by), QPointF(bx + dx * bl, by))
            p.drawLine(QPointF(bx, by), QPointF(bx, by + dy * bl))

        # face text only: remove the center orb circle overlay
        title_font = QFont("Segoe UI", int(max(20, fw * 0.052)), QFont.Weight.Bold)
        p.setFont(title_font)
        y_title = cy - 25
        p.setPen(QColor(245, 248, 255, 235))
        p.drawText(QRectF(cx - 120, y_title, 130, 48), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "REX")
        p.setPen(QColor(255, 98, 98, 245))
        p.drawText(QRectF(cx + 8, y_title, 90, 48), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "")
        p.setFont(QFont("Segoe UI", int(max(8, fw * 0.018)), QFont.Weight.Bold))
        p.setPen(QColor(190, 196, 205, 190))
        p.drawText(QRectF(cx - 90, cy + 18, 180, 22), Qt.AlignmentFlag.AlignCenter, "AI COMMAND CENTER")

        # keep the center clean: no extra particles

        # status text
        sy = cy + fw * 0.40
        if self.muted:
            txt, col = "MIC STATUS\nMUTED", QColor(255, 69, 69, 235)
        elif self.speaking:
            txt, col = "MIC STATUS\nSPEAKING", QColor(255, 255, 255, 235)
        elif self.state == "THINKING":
            txt, col = "AI CORE\nTHINKING", QColor(255, 185, 96, 235)
        elif self.state in ("PROCESSING", "EXECUTING"):
            txt, col = "AI CORE\nEXECUTING", QColor(255, 69, 69, 235)
        elif self.state == "LISTENING":
            txt, col = "MIC STATUS\nLISTENING", QColor(255, 195, 0, 220)
        else:
            txt, col = f"AI CORE\n{self.state}", QColor(255, 255, 255, 220)

        p.setPen(QPen(col, 1))
        p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        top_status, bottom_status = txt.split("\n", 1)
        p.drawText(QRectF(0, sy, W, 18), Qt.AlignmentFlag.AlignCenter, top_status)
        p.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        p.drawText(QRectF(0, sy + 18, W, 24), Qt.AlignmentFlag.AlignCenter, bottom_status)

        # waveform
        wy = sy + 30
        N, bw = 36, 8
        wx0 = (W - N * bw) / 2
        for i in range(N):
            if self.muted:
                hgt, cl = 2, qcol(C.MUTED_C)
            elif self.speaking:
                hgt = random.randint(3, 20)
                cl  = qcol(C.PRI) if hgt > 12 else qcol(C.PRI_DIM)
            else:
                hgt = int(3 + 2 * math.sin(self._tick * 0.09 + i * 0.6))
                cl  = QColor(accent.red(), accent.green(), accent.blue(), 90 if self.state == "LISTENING" else 60)
            p.fillRect(QRectF(wx0 + i * bw, wy + 20 - hgt, bw - 1, hgt), cl)


class MetricBar(QWidget):

    def __init__(self, label: str, color: str = C.PRI, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._value = 0.0       # 0-100
        self._text  = "--"
        self.setFixedHeight(38)
        self.setMinimumWidth(80)

    def set_value(self, pct: float, text: str):
        self._value = max(0.0, min(100.0, pct))
        self._text  = text
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        p.setBrush(QBrush(qcol(C.PANEL2)))
        p.setPen(QPen(qcol(C.BORDER_A), 1))
        p.drawRoundedRect(QRectF(1, 1, W - 2, H - 2), 4, 4)

        bar_h   = 4
        bar_y   = H - bar_h - 5
        bar_w   = W - 12
        bar_x   = 6
        fill_w  = int(bar_w * self._value / 100)

        p.setBrush(QBrush(qcol(C.BAR_BG)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 2, 2)

        if self._value > 85:
            bar_col = qcol(C.RED)
        elif self._value > 65:
            bar_col = qcol(C.ACC)
        else:
            bar_col = qcol(self._color)

        if fill_w > 0:
            p.setBrush(QBrush(bar_col))
            p.drawRoundedRect(QRectF(bar_x, bar_y, fill_w, bar_h), 2, 2)

        p.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(8, 5, 50, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._label)

        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.setPen(QPen(bar_col if self._text != "--" else qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, 4, W - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._text)


class MessageCard(QFrame):
    def __init__(self, role: str, name: str, text: str, stamp: str, parent=None):
        super().__init__(parent)
        self.setObjectName("MessageCard")
        accent_map = {
            "user": (C.BORDER_B, C.WHITE),
            "assistant": (C.PRI, C.PRI),
            "system": ("#4b8cff", "#4b8cff"),
            "file": ("#35c96d", "#35c96d"),
            "error": ("#ff8b3d", "#ff8b3d"),
        }
        border_col, left_col = accent_map.get(role, accent_map["system"])
        self.setStyleSheet(
            f"""
            QFrame#MessageCard {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(13, 15, 20, 246),
                    stop:1 rgba(5, 6, 9, 238));
                border: 1px solid {border_col};
                border-left: 3px solid {left_col};
                border-radius: 12px;
            }}
            """
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(12)

        avatar = QLabel(name[:1].upper())
        avatar.setFixedSize(34, 34)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))

        palette = {
            "user": ("#0d0f14", C.WHITE, C.BORDER_B),
            "assistant": ("#12090a", C.RED, C.PRI),
            "system": ("#101525", "#7cb7ff", "#4b8cff"),
            "file": ("#0f1410", C.GREEN, "#35c96d"),
            "error": ("#1a0f10", "#ffb074", "#ff8b3d"),
        }
        bg, fg, border = palette.get(role, palette["system"])
        avatar.setStyleSheet(
            f"background: {bg}; color: {fg}; border: 1px solid {border}; border-radius: 20px;"
        )

        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(3)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)

        name_lbl = QLabel(name)
        name_lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        name_lbl.setStyleSheet(f"color: {C.WHITE}; background: transparent;")

        time_lbl = QLabel(stamp)
        time_lbl.setFont(QFont("Segoe UI", 7))
        time_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        time_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)

        top.addWidget(name_lbl)
        top.addStretch()
        top.addWidget(time_lbl)

        text_lbl = QLabel(text)
        text_lbl.setWordWrap(True)
        text_lbl.setFont(QFont("Segoe UI", 9))
        text_color = {
            "user": C.TEXT,
            "assistant": C.WHITE,
            "system": C.TEXT_MED,
            "file": C.GREEN,
            "error": C.RED,
        }.get(role, C.TEXT)
        text_lbl.setStyleSheet(f"color: {text_color}; background: transparent;")

        body.addLayout(top)
        body.addWidget(text_lbl)
        lay.addWidget(avatar)
        lay.addLayout(body)



class TaskCard(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TaskCard")
        self._active = False
        self._workspace_locked = False
        self.setStyleSheet(
            f"""
            QFrame#TaskCard {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(16, 18, 24, 230),
                    stop:0.6 rgba(10, 12, 18, 210),
                    stop:1 rgba(5, 7, 11, 200));
                border: 1px solid rgba(255, 255, 255, 0.10);
                border-radius: 16px;
            }}
            QFrame#TaskCard:hover {{
                border: 1px solid rgba(255, 69, 69, 0.28);
            }}
            """
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        row = QHBoxLayout()
        self._title = QLabel("Task Workspace")
        self._title.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self._title.setStyleSheet(f"color: {C.RED}; background: transparent;")

        self._pct = QLabel("0%")
        self._pct.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self._pct.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        self._pct.setAlignment(Qt.AlignmentFlag.AlignRight)
        row.addWidget(self._title)
        row.addStretch()
        row.addWidget(self._pct)
        lay.addLayout(row)

        self._command_lbl = QLabel("Command: waiting for input")
        self._command_lbl.setWordWrap(True)
        self._command_lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self._command_lbl.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        lay.addWidget(self._command_lbl)

        self._plan_lbl = QLabel("Plan: REX will generate a task plan after you send a command.")
        self._plan_lbl.setWordWrap(True)
        self._plan_lbl.setFont(QFont("Segoe UI", 9))
        self._plan_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        lay.addWidget(self._plan_lbl)

        self._status_lbl = QLabel("Status: Idle")
        self._status_lbl.setWordWrap(True)
        self._status_lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self._status_lbl.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        lay.addWidget(self._status_lbl)

        self._output_lbl = QLabel("Output: Ready to work.")
        self._output_lbl.setWordWrap(True)
        self._output_lbl.setFont(QFont("Segoe UI", 9))
        self._output_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        lay.addWidget(self._output_lbl)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(8)
        self._bar.setStyleSheet(
            f"""
            QProgressBar {{
                background: rgba(255,255,255,0.06);
                border: none;
                border-radius: 3px;
            }}
            QProgressBar::chunk {{
                background: {C.RED};
                border-radius: 3px;
            }}
            """
        )
        lay.addWidget(self._bar)

        self._foot = QLabel("Working on it...")
        self._foot.setFont(QFont("Segoe UI", 9))
        self._foot.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addWidget(self._foot)

    def set_task(self, title: str, desc: str, percent: int):
        if self._workspace_locked:
            return
        if self._active:
            self.update_workspace(title=title, status=desc, percent=percent)
            return
        self._title.setText(title)
        self._status_lbl.setText(desc)
        self._output_lbl.setText(desc)
        self._plan_lbl.setText("Plan: REX will generate a task plan after you send a command.")
        self._command_lbl.setText("Command: waiting for input")
        self._pct.setText(f"{percent}%")
        self._bar.setValue(max(0, min(100, percent)))

    def _format_plan(self, plan: list[str] | str | None) -> str:
        if not plan:
            return "Plan: • Understand the request\n       • Execute the right tools\n       • Return the result"
        if isinstance(plan, str):
            text = plan.strip()
            return f"Plan: {text}" if text.lower().startswith("plan:") else f"Plan: {text}"
        items = [str(item).strip() for item in plan if str(item).strip()]
        if not items:
            return "Plan: • Understand the request\n       • Execute the right tools\n       • Return the result"
        return "Plan:\n" + "\n".join(f"• {item}" for item in items)

    def start_workspace(self, command: str, plan: list[str] | str | None = None, source: str = "local"):
        self._active = True
        self._workspace_locked = False
        self._title.setText("Task Workspace")
        self._command_lbl.setText(f"Command: {command or 'waiting for input'}")
        self._plan_lbl.setText(self._format_plan(plan))
        self._status_lbl.setText("Status: Planning task...")
        self._output_lbl.setText("Output: Waiting for execution.")
        self._pct.setText("8%")
        self._bar.setValue(8)
        self._foot.setText(f"Source: {source}")
        self.show()

    def update_workspace(self, *, title: str | None = None, command: str | None = None, plan: list[str] | str | None = None,
                         status: str | None = None, output: str | None = None, percent: int | None = None,
                         footer: str | None = None):
        if title:
            self._title.setText(title)
        if command:
            self._command_lbl.setText(f"Command: {command}")
        if plan is not None:
            self._plan_lbl.setText(self._format_plan(plan))
        if status:
            self._status_lbl.setText(f"Status: {status}" if not status.lower().startswith("status:") else status)
        if output:
            self._output_lbl.setText(f"Output: {output}" if not output.lower().startswith("output:") else output)
        if percent is not None:
            pct = max(0, min(100, int(percent)))
            self._pct.setText(f"{pct}%")
            self._bar.setValue(pct)
        if footer:
            self._foot.setText(footer)
        self._active = True
        self._workspace_locked = False
        self.show()

    def finish_workspace(self, result: str, status: str = "Task completed.", percent: int = 100):
        self._active = False
        self._workspace_locked = True
        self._title.setText("Task Complete")
        self._status_lbl.setText(f"Status: {status}")
        self._output_lbl.setText(f"Output: {result or 'Done.'}")
        self._pct.setText(f"{max(0, min(100, percent))}%")
        self._bar.setValue(max(0, min(100, percent)))
        self._foot.setText("Resetting workspace shortly...")
        self.show()
        QTimer.singleShot(5000, self.clear_workspace)

    def clear_workspace(self):
        self._active = False
        self._workspace_locked = False
        self._title.setText("Ready")
        self._command_lbl.setText("Command: waiting for input")
        self._plan_lbl.setText("Plan: REX will generate a task plan after you send a command.")
        self._status_lbl.setText("Status: Idle")
        self._output_lbl.setText("Output: Ready to work.")
        self._pct.setText("0%")
        self._bar.setValue(0)
        self._foot.setText("Working on it...")
        self.hide()



class AttachmentCard(QFrame):
    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("AttachmentCard")
        self.setStyleSheet("""
            QFrame#AttachmentCard {
                background: rgba(255,255,255,0.04);
                border: 1px solid rgba(255,69,69,0.26);
                border-radius: 10px;
            }
        """)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(10)
        icon = QLabel("⎙")
        icon.setFixedSize(26, 26)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet("color: #ff7777; background: rgba(255,255,255,0.03); border-radius: 13px; font-size: 14px;")
        lay.addWidget(icon)
        txt = QVBoxLayout()
        txt.setContentsMargins(0, 0, 0, 0)
        txt.setSpacing(2)
        t = QLabel(title)
        t.setStyleSheet("color: #ffffff; background: transparent; font: 600 9pt 'Segoe UI';")
        s = QLabel(subtitle)
        s.setStyleSheet("color: rgba(255,255,255,0.62); background: transparent; font: 8pt 'Segoe UI';")
        txt.addWidget(t)
        txt.addWidget(s)
        lay.addLayout(txt, 1)



class EventCard(QFrame):
    def __init__(self, title: str, detail: str, stamp: str, icon: str = "●", accent: str = "#ff4545", parent=None):
        super().__init__(parent)
        self.setObjectName("EventCard")
        self.setStyleSheet(
            f"""
            QFrame#EventCard {{
                background: rgba(12, 13, 17, 220);
                border: 1px solid rgba(255,69,69,0.18);
                border-left: 3px solid {accent};
                border-radius: 12px;
            }}
            """
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(10)

        icon_lbl = QLabel(icon[:1])
        icon_lbl.setFixedSize(30, 30)
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        icon_lbl.setStyleSheet(
            f"background: rgba(255,69,69,0.08); color: {accent}; border: 1px solid rgba(255,69,69,0.22); border-radius: 15px;"
        )
        lay.addWidget(icon_lbl)

        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(3)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        title_lbl = QLabel(title)
        title_lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        title_lbl.setStyleSheet("color: #ffffff; background: transparent;")
        stamp_lbl = QLabel(stamp)
        stamp_lbl.setFont(QFont("Segoe UI", 7))
        stamp_lbl.setStyleSheet("color: rgba(255,255,255,0.55); background: transparent;")
        top.addWidget(title_lbl)
        top.addStretch(1)
        top.addWidget(stamp_lbl)
        body.addLayout(top)

        detail_lbl = QLabel(detail)
        detail_lbl.setWordWrap(True)
        detail_lbl.setFont(QFont("Segoe UI", 9))
        detail_lbl.setStyleSheet("color: rgba(255,255,255,0.78); background: transparent;")
        body.addWidget(detail_lbl)
        lay.addLayout(body, 1)



class ArtifactCard(QFrame):
    def __init__(self, title: str, file_type: str = "File", status: str = "Generated", path: str = "", parent=None):
        super().__init__(parent)
        self._path = path.strip()
        self.setObjectName("ArtifactCard")
        self.setStyleSheet(
            """
            QFrame#ArtifactCard {
                background: rgba(11, 12, 16, 230);
                border: 1px solid rgba(255,69,69,0.22);
                border-radius: 12px;
            }
            QPushButton {
                background: rgba(255,255,255,0.04);
                color: #ffffff;
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 8px;
                padding: 6px 10px;
            }
            QPushButton:hover {
                background: rgba(255,69,69,0.08);
                border: 1px solid rgba(255,69,69,0.35);
            }
            QPushButton:disabled {
                color: rgba(255,255,255,0.35);
            }
            """
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(10)
        badge = QLabel("↗")
        badge.setFixedSize(30, 30)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        badge.setStyleSheet("background: rgba(255,69,69,0.08); color: #ff7777; border: 1px solid rgba(255,69,69,0.24); border-radius: 15px;")
        head.addWidget(badge)

        meta = QVBoxLayout()
        meta.setContentsMargins(0, 0, 0, 0)
        meta.setSpacing(2)
        name_lbl = QLabel(title or "Generated file")
        name_lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        name_lbl.setStyleSheet("color: #ffffff; background: transparent;")
        type_lbl = QLabel(f"{file_type} • {status}")
        type_lbl.setFont(QFont("Segoe UI", 8))
        type_lbl.setStyleSheet("color: rgba(255,255,255,0.62); background: transparent;")
        meta.addWidget(name_lbl)
        meta.addWidget(type_lbl)
        head.addLayout(meta, 1)
        lay.addLayout(head)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._open_btn = QPushButton("Open")
        self._reveal_btn = QPushButton("Reveal Folder")
        self._open_btn.clicked.connect(self._open_file)
        self._reveal_btn.clicked.connect(self._reveal_file)
        if not self._path or not Path(self._path).exists():
            self._open_btn.setEnabled(False)
            self._reveal_btn.setEnabled(False)
        btn_row.addWidget(self._open_btn)
        btn_row.addWidget(self._reveal_btn)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)

    def _open_file(self):
        if not self._path or not Path(self._path).exists():
            return
        try:
            os.startfile(self._path)
        except Exception as _e:
            log_error(_e, context="ui", severity="debug")

    def _reveal_file(self):
        if not self._path or not Path(self._path).exists():
            return
        try:
            if platform.system() == "Windows":
                subprocess.Popen(["explorer", "/select,", self._path])
            else:
                os.startfile(str(Path(self._path).parent))
        except Exception as _e:
            log_error(_e, context="ui", severity="debug")



class ChatBubble(QFrame):
    def __init__(self, role: str, name: str, text: str, stamp: str, attachments: list[dict] | None = None, parent=None, animate: bool = False):
        super().__init__(parent)
        self._role = role
        self._full_text = text or ""
        self._typing_index = 0
        self._typing_timer: QTimer | None = None
        self.setObjectName("ChatBubble")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.setStyleSheet(
            """
            QFrame#ChatBubble {
                background: rgba(10, 11, 14, 220);
                border: 1px solid rgba(255,69,69,0.12);
                border-left: 2px solid rgba(255,69,69,0.65);
                border-radius: 12px;
            }
            """
        )
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        row = QHBoxLayout()
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(8)

        avatar = QLabel((name or role[:1]).strip()[:1].upper())
        avatar.setFixedSize(28, 28)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        avatar_bg = {
            "user": ("#0f1015", "#ffffff", "rgba(255,255,255,0.18)"),
            "assistant": ("#12090a", "#ff7777", "rgba(255,69,69,0.35)"),
            "system": ("#0f1015", "#ff7777", "rgba(255,69,69,0.25)"),
            "file": ("#0c120f", "#37ff5f", "rgba(55,255,95,0.35)"),
        }.get(role, ("#0f1015", "#ffffff", "rgba(255,255,255,0.18)"))
        avatar.setStyleSheet(
            f"background: {avatar_bg[0]}; color: {avatar_bg[1]}; border: 1px solid {avatar_bg[2]}; border-radius: 17px;"
        )

        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(4)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        name_lbl = QLabel(name)
        name_lbl.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        name_lbl.setStyleSheet("color: #ffffff; background: transparent;")
        time_lbl = QLabel(stamp)
        time_lbl.setFont(QFont("Segoe UI", 7))
        time_lbl.setStyleSheet("color: rgba(255,255,255,0.55); background: transparent;")
        top.addWidget(name_lbl)
        top.addStretch()
        top.addWidget(time_lbl)

        self._browser = QTextBrowser()
        self._browser.setFrameShape(QFrame.Shape.NoFrame)
        self._browser.setOpenExternalLinks(True)
        self._browser.setReadOnly(True)
        self._browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._browser.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._browser.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self._browser.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self._browser.setStyleSheet(
            """
            QTextBrowser {
                background: transparent;
                border: none;
                color: #f4f6f8;
                padding: 0;
            }
            """
        )
        self._browser.document().setDocumentMargin(0)
        self._render_text(text or "")

        body.addLayout(top)
        body.addWidget(self._browser, 1)

        if attachments:
            for attachment in attachments:
                title = str(attachment.get("name") or attachment.get("title") or attachment.get("path") or "Attachment")
                subtitle = str(attachment.get("path") or attachment.get("description") or "")
                body.addWidget(ArtifactCard(title, file_type=Path(title).suffix.lstrip(".").upper() or "File", status="Attached", path=subtitle or title))

        if role == "user":
            row.addStretch(1)
            row.addLayout(body, 0)
            row.addWidget(avatar)
        else:
            row.addWidget(avatar)
            row.addLayout(body, 1)
            row.addStretch(1)
        outer.addLayout(row)
        if animate and role == "assistant":
            self._start_typing_animation()
        else:
            QTimer.singleShot(0, self._fit_to_content)

    def _render_text(self, text: str, final: bool = True):
        if final:
            try:
                self._browser.setMarkdown(text or "")
                return
            except Exception as _e:
                log_error(_e, context="ui", severity="debug")
        self._browser.setHtml(_markdown_to_html(text or "", self._role))

    def _start_typing_animation(self):
        self._typing_timer = QTimer(self)
        self._typing_timer.setInterval(14)
        self._typing_timer.timeout.connect(self._tick_typing)
        self._typing_timer.start()

    def _tick_typing(self):
        self._typing_index = min(len(self._full_text), self._typing_index + 3)
        snippet = self._full_text[:self._typing_index]
        self._render_text(snippet, final=False)
        if self._typing_index >= len(self._full_text):
            try:
                self._typing_timer.stop()
            except Exception as _e:
                log_error(_e, context="ui", severity="debug")
            self._render_text(self._full_text, final=True)
            QTimer.singleShot(0, self._fit_to_content)

    def _fit_to_content(self):
        try:
            doc = self._browser.document()
            viewport = self.parentWidget()
            while viewport is not None and not hasattr(viewport, "viewport"):
                viewport = viewport.parentWidget()
            viewport_width = viewport.viewport().width() if viewport and hasattr(viewport, "viewport") else self.width()
            role_width = {
                "user": 0.62,
                "assistant": 0.78,
                "system": 0.72,
                "file": 0.80,
            }.get((getattr(self, "_role", "") or "").lower(), 0.76)
            width = max(260, min(int(viewport_width * role_width), max(280, viewport_width - 110)))
            doc.setTextWidth(width)
            doc.adjustSize()
            height = int(doc.size().height()) + 8
            self._browser.setFixedWidth(width)
            self._browser.setMinimumHeight(height)
            self._browser.setMaximumHeight(max(height, 24))
            self._browser.updateGeometry()
            self.updateGeometry()
        except Exception as _e:
            log_error(_e, context="ui", severity="debug")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_to_content()



class HistoryConversationItem(QFrame):
    clicked = pyqtSignal(str)

    def __init__(self, conversation_id: str, title: str, stamp: str, pinned: bool = False, parent=None):
        super().__init__(parent)
        self._conversation_id = conversation_id
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("HistoryConversationItem")
        self.setStyleSheet(
            """
            QFrame#HistoryConversationItem {
                background: rgba(255,255,255,0.03);
                border: 1px solid rgba(255,69,69,0.18);
                border-radius: 10px;
            }
            QFrame#HistoryConversationItem:hover {
                background: rgba(255,69,69,0.07);
                border: 1px solid rgba(255,69,69,0.32);
            }
            """
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(10)

        icon = QLabel("B")
        icon.setFixedSize(30, 30)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet("background: rgba(255,69,69,0.10); color: #ff7777; border: 1px solid rgba(255,69,69,0.28); border-radius: 15px; font: 700 11pt 'Segoe UI';")
        lay.addWidget(icon)

        meta = QVBoxLayout()
        meta.setContentsMargins(0, 0, 0, 0)
        meta.setSpacing(2)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        title_lbl = QLabel(title or "Conversation")
        title_lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        title_lbl.setStyleSheet("color: #ffffff; background: transparent;")
        row.addWidget(title_lbl)
        if pinned:
            pin = QLabel("PIN")
            pin.setStyleSheet("color: #37ff5f; background: transparent; font: 700 7pt 'Courier New';")
            row.addWidget(pin)
        row.addStretch()
        stamp_lbl = QLabel(stamp)
        stamp_lbl.setFont(QFont("Segoe UI", 7))
        stamp_lbl.setStyleSheet("color: rgba(255,255,255,0.50); background: transparent;")
        row.addWidget(stamp_lbl)
        meta.addLayout(row)
        lay.addLayout(meta, 1)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._conversation_id)



class ConversationFeed(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet(
            """
            QScrollArea { background: transparent; border: none; }
            QScrollBar:vertical {
                background: transparent;
                width: 8px;
                border: none;
                margin: 6px 0 6px 0;
            }
            QScrollBar::handle:vertical {
                background: rgba(255,69,69,0.45);
                border-radius: 4px;
                min-height: 24px;
            }
            """
        )
        self._content = QWidget()
        self._content.setStyleSheet("background: transparent;")
        self._layout = QVBoxLayout(self._content)
        self._layout.setContentsMargins(2, 2, 2, 2)
        self._layout.setSpacing(8)
        self._layout.addStretch(1)
        self.setWidget(self._content)
        self._empty_widget: QWidget | None = None
        self._message_count = 0

    def _ensure_empty_widget(self):
        if self._empty_widget is not None:
            return self._empty_widget
        frame = QFrame()
        frame.setStyleSheet(
            """
            QFrame {
                background: rgba(255,255,255,0.03);
                border: 1px solid rgba(255,69,69,0.16);
                border-radius: 14px;
            }
            QPushButton {
                background: rgba(255,255,255,0.04);
                color: #ffffff;
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 12px;
                padding: 8px 12px;
                text-align: left;
            }
            QPushButton:hover {
                background: rgba(255,69,69,0.08);
                border: 1px solid rgba(255,69,69,0.28);
            }
            """
        )
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)
        title = QLabel("Try asking REX")
        title.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        title.setStyleSheet("color: #ffffff; background: transparent;")
        subtitle = QLabel("Create a presentation, analyze a screen, build a website, organize files, or run browser automation.")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: rgba(255,255,255,0.64); background: transparent;")
        lay.addWidget(title)
        lay.addWidget(subtitle)
        grid = QGridLayout()
        grid.setSpacing(8)
        self._empty_widget_buttons = []
        for idx, suggestion in enumerate([
            "Create Presentation",
            "Analyze Screen",
            "Build Website",
            "Organize Downloads",
            "Browser Automation",
        ]):
            btn = QPushButton(suggestion)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, s=suggestion: self._emit_empty_suggestion(s))
            self._empty_widget_buttons.append(btn)
            grid.addWidget(btn, idx // 2, idx % 2)
        lay.addLayout(grid)
        self._empty_widget = frame
        return frame

    def _emit_empty_suggestion(self, text: str):
        root = self.parentWidget()
        while root is not None and not hasattr(root, "command_submitted"):
            root = root.parentWidget()
        if root is not None and hasattr(root, "command_submitted"):
            root.command_submitted.emit(text)

    def clear_messages(self):
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None and widget is not self._empty_widget:
                widget.deleteLater()
        if self._empty_widget is not None:
            self._empty_widget.hide()
        self._layout.addStretch(1)
        self._message_count = 0

    def add_message(self, role: str, name: str, text: str, stamp: str, attachments: list[dict] | None = None, animate: bool = False, event_type: str | None = None):
        if self._layout.count() and self._layout.itemAt(self._layout.count() - 1).spacerItem() is not None:
            self._layout.takeAt(self._layout.count() - 1)
        role = (role or "").strip().lower()
        if role == "system":
            bubble = self._build_event_card(text, stamp, event_type=event_type)
        elif role == "file":
            bubble = self._build_artifact_card(text, attachments=attachments, stamp=stamp)
        else:
            bubble = ChatBubble(role, name, text, stamp, attachments=attachments, parent=self, animate=animate)
        self._layout.addWidget(bubble)
        self._layout.addStretch(1)
        self._message_count += 1
        self._sync_empty_state()
        QTimer.singleShot(0, self.scroll_to_bottom)

    def _build_event_card(self, text: str, stamp: str, event_type: str | None = None) -> QWidget:
        low = (event_type or text or "").lower()
        title = "System Event"
        icon = "●"
        accent = "#ff4545"
        if "discord" in low and "connected" in low:
            title, icon, accent = "Discord Connected", "◉", "#5865F2"
        elif "presentation" in low:
            title, icon, accent = "Presentation Generated", "▣", "#ff7a45"
        elif "website" in low:
            title, icon, accent = "Website Created", "⌂", "#37ff5f"
        elif "spreadsheet" in low:
            title, icon, accent = "Spreadsheet Generated", "▦", "#6fd6ff"
        elif "browser" in low:
            title, icon, accent = "Browser Automation Completed", "↗", "#ffbf00"
        elif "organ" in low and "file" in low:
            title, icon, accent = "File Organization Completed", "[ ]", "#37ff5f"
        elif "screen" in low and "analysis" in low:
            title, icon, accent = "Screen Analysis Completed", "◫", "#6fd6ff"
        return EventCard(title, text, stamp, icon=icon, accent=accent, parent=self)

    def _build_artifact_card(self, text: str, attachments: list[dict] | None = None, stamp: str = "") -> QWidget:
        attachment = (attachments or [{}])[0] if attachments else {}
        path = str(attachment.get("path") or attachment.get("file") or attachment.get("name") or "").strip()
        title = str(attachment.get("name") or attachment.get("title") or Path(path).name or "Generated File").strip()
        suffix = Path(path or title).suffix.lstrip(".").upper() or (str(attachment.get("type") or "FILE")).upper()
        status = "Ready"
        return ArtifactCard(title, file_type=suffix, status=status, path=path or title, parent=self)

    def scroll_to_bottom(self):
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    def load_messages(self, messages: list[dict[str, Any]]):
        self.clear_messages()
        for msg in messages:
            role = (msg.get("role") or "assistant").strip().lower()
            content = msg.get("content") or ""
            stamp = _fmt_time_stamp(msg.get("timestamp"))
            attachments = msg.get("attachments") or []
            name = {
                "user": "You",
                "assistant": "REX",
                "system": "System",
                "file": "Files",
            }.get(role, "REX")
            self.add_message(role, name, content, stamp, attachments=attachments, animate=False)
        self._sync_empty_state()
        QTimer.singleShot(0, self.scroll_to_bottom)

    def _sync_empty_state(self):
        if self._empty_widget is None:
            self._ensure_empty_widget()
        if self._message_count <= 0:
            if self._layout.indexOf(self._empty_widget) == -1:
                self._layout.insertWidget(0, self._empty_widget)
            self._empty_widget.show()
        else:
            if self._layout.indexOf(self._empty_widget) != -1:
                self._layout.removeWidget(self._empty_widget)
            self._empty_widget.hide()

    def has_messages(self) -> bool:
        return self._message_count > 0

    def resizeEvent(self, event):
        super().resizeEvent(event)
        for i in range(self._layout.count()):
            widget = self._layout.itemAt(i).widget()
            if hasattr(widget, "_fit_to_content"):
                widget._fit_to_content()
        QTimer.singleShot(0, self.scroll_to_bottom)



class TaskDock(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TaskDock")
        self._collapsed = False
        self._expanded_w = 392
        self._collapsed_w = 44
        self.setFixedWidth(self._expanded_w)
        self.setStyleSheet(
            f"""
            QFrame#TaskDock {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(7, 8, 12, 250),
                    stop:1 rgba(3, 4, 6, 245));
                border-left: 1px solid rgba(255, 69, 69, 0.55);
            }}
            """
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 10, 8, 10)
        root.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(8)
        self._title = QLabel("TASK WORKSPACE")
        self._title.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        self._title.setStyleSheet(f"color: {C.PRI}; background: transparent; letter-spacing: 1px;")
        header.addWidget(self._title)
        header.addStretch()

        self._toggle_btn = QPushButton(">")
        self._toggle_btn.setFixedSize(34, 34)
        self._toggle_btn.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        self._toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_btn.setStyleSheet(
            f"QPushButton {{ background: rgba(12,14,18,245); color: {C.WHITE}; border: 1px solid {C.BORDER_B}; border-radius: 8px; }}"
            f"QPushButton:hover {{ color: {C.PRI}; border: 1px solid {C.PRI}; }}"
        )
        self._toggle_btn.clicked.connect(self.toggle_collapsed)
        header.addWidget(self._toggle_btn)
        root.addLayout(header)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.PRI_GHO}; margin: 2px 0;")
        root.addWidget(sep)

        self._content = QWidget()
        self._content.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(self._content)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        self._task_card = TaskCard()
        lay.addWidget(self._task_card)

        self._mini_hint = QLabel("TASK")
        self._mini_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._mini_hint.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self._mini_hint.setStyleSheet(
            f"color: {C.PRI}; background: rgba(12,14,18,245); border: 1px solid {C.BORDER_B}; border-radius: 8px; padding: 10px 4px;"
        )
        self._mini_hint.setVisible(False)
        lay.addWidget(self._mini_hint)

        root.addWidget(self._content, stretch=1)
        self._apply_state()

    def toggle_collapsed(self):
        self.set_collapsed(not self._collapsed)

    def set_collapsed(self, collapsed: bool):
        self._collapsed = bool(collapsed)
        self._apply_state()

    def is_collapsed(self) -> bool:
        return self._collapsed

    def _apply_state(self):
        self._content.setVisible(not self._collapsed)
        self._mini_hint.setVisible(self._collapsed)
        self._toggle_btn.setText(">" if self._collapsed else "<")
        self._toggle_btn.setToolTip("Open task workspace" if self._collapsed else "Collapse task workspace")
        self._title.setVisible(not self._collapsed)
        self.setFixedWidth(self._collapsed_w if self._collapsed else self._expanded_w)

    def start_workspace(self, command: str, plan: list[str] | str | None = None, source: str = "local"):
        self._task_card.start_workspace(command, plan, source)
        if self._collapsed:
            return

    def update_workspace(self, **kwargs):
        self._task_card.update_workspace(**kwargs)

    def finish_workspace(self, result: str, status: str = "Task completed.", percent: int = 100):
        self._task_card.finish_workspace(result, status, percent)

    def clear_workspace(self):
        self._task_card.clear_workspace()



class WorkspaceSidebar(QWidget):
    command_submitted = pyqtSignal(str)
    close_requested = pyqtSignal()
    attach_requested = pyqtSignal()
    mic_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._collapsed = True
        self._expanded_w = 468
        self._target_h = 860
        self._active_conversation_id: str | None = None
        self._store = workspace_store()
        self._anim = QPropertyAnimation(self, b"geometry", self)
        self._anim.setDuration(300)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._panel = QFrame(self)
        self._panel.setObjectName("WorkspaceSidebarPanel")
        self._panel.setStyleSheet(
            """
            QFrame#WorkspaceSidebarPanel {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(16, 18, 24, 230),
                stop:0.55 rgba(10, 12, 18, 210),
                stop:1 rgba(5, 7, 11, 200));
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 22px;
            }
            """
        )
        try:
            shadow = QGraphicsDropShadowEffect(self._panel)
            shadow.setBlurRadius(35)
            shadow.setColor(QColor(0, 0, 0, 72))
            shadow.setOffset(0, 12)
            self._panel.setGraphicsEffect(shadow)
        except Exception as _e:
            log_error(_e, context="ui", severity="debug")
        root = QHBoxLayout(self._panel)
        root.setContentsMargins(14, 14, 12, 14)
        root.setSpacing(10)

        self._content = QWidget()
        self._content.setStyleSheet("background: transparent;")
        content = QVBoxLayout(self._content)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(10)
        self._title = QLabel("CHAT + TASK WORKSPACE")
        self._title.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        self._title.setStyleSheet("color: #FFFFFF; background: transparent; letter-spacing: 1px;")
        header.addWidget(self._title)
        header.addStretch()
        self._close_btn = QPushButton("REX")
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.setFixedHeight(30)
        self._close_btn.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self._close_btn.setStyleSheet(
            """
            QPushButton {
                background: rgba(15,15,20,220);
                color: #FFFFFF;
                border: 1px solid rgba(255,69,69,120);
                border-radius: 10px;
                padding: 0 12px;
            }
            QPushButton:hover {
                border: 1px solid rgba(255,69,69,200);
            }
            """
        )
        self._close_btn.clicked.connect(self.close_requested.emit)
        header.addWidget(self._close_btn)
        content.addLayout(header)

        self._tab_row = QHBoxLayout()
        self._tab_row.setSpacing(8)
        self._chat_tab_btn = self._make_tab_button("CHAT", True)
        self._history_tab_btn = self._make_tab_button("HISTORY", False)
        self._chat_tab_btn.clicked.connect(lambda: self._set_tab(0))
        self._history_tab_btn.clicked.connect(lambda: self._set_tab(1))
        self._tab_row.addWidget(self._chat_tab_btn)
        self._tab_row.addWidget(self._history_tab_btn)
        self._tab_row.addStretch(1)
        content.addLayout(self._tab_row)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_chat_tab())
        self._stack.addWidget(self._build_history_tab())
        content.addWidget(self._stack, 1)

        root.addWidget(self._content, stretch=1)

        self._panel.hide()
        self.hide()
        self._set_tab(0)
        self._ensure_active_conversation()
        self._load_active_conversation()
        self._refresh_history()
        self._apply_state()

    def _make_tab_button(self, text: str, active: bool = False) -> QPushButton:
        btn = QPushButton(text)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedHeight(34)
        btn.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        btn.setCheckable(True)
        btn.setChecked(active)
        btn.setStyleSheet(self._tab_style(active))
        return btn

    def _tab_style(self, active: bool) -> str:
        if active:
            return """
                QPushButton {
                    background: rgba(255,69,69,0.16);
                    color: #FFFFFF;
                    border: 1px solid rgba(255,69,69,180);
                    border-radius: 10px;
                    padding: 0 14px;
                }
            """
        return """
            QPushButton {
                background: rgba(255,255,255,0.04);
                color: rgba(255,255,255,0.82);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 10px;
                padding: 0 14px;
            }
            QPushButton:hover {
                background: rgba(255,69,69,0.08);
                border: 1px solid rgba(255,69,69,120);
            }
        """

    def _set_tab(self, index: int):
        index = 0 if index == 0 else 1
        self._stack.setCurrentIndex(index)
        self._chat_tab_btn.setStyleSheet(self._tab_style(index == 0))
        self._history_tab_btn.setStyleSheet(self._tab_style(index == 1))
        self._chat_tab_btn.setChecked(index == 0)
        self._history_tab_btn.setChecked(index == 1)
        if index == 1:
            self._history_search.setFocus(Qt.FocusReason.TabFocusReason)

    def _build_chat_tab(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        self._task_card = TaskCard()
        lay.addWidget(self._task_card)

        self._memory_frame = QFrame()
        self._memory_frame.setVisible(False)
        self._memory_frame.setStyleSheet(
            """
            QFrame {
                background: rgba(255,69,69,0.05);
                border: 1px solid rgba(255,69,69,0.24);
                border-radius: 12px;
            }
            """
        )
        mem_lay = QVBoxLayout(self._memory_frame)
        mem_lay.setContentsMargins(12, 10, 12, 10)
        mem_lay.setSpacing(6)
        mem_title = QLabel("Using Memory:")
        mem_title.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        mem_title.setStyleSheet("color: #FFFFFF; background: transparent;")
        self._memory_items = QLabel("")
        self._memory_items.setWordWrap(True)
        self._memory_items.setStyleSheet("color: rgba(255,255,255,0.75); background: transparent;")
        mem_lay.addWidget(mem_title)
        mem_lay.addWidget(self._memory_items)
        lay.addWidget(self._memory_frame)

        self._feed = ConversationFeed()
        lay.addWidget(self._feed, 1)

        # Keep an internal input stub for signal safety, but do not render a command bar here.
        self._input = QLineEdit(self)
        self._input.hide()

        return page

    def _build_history_tab(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        self._history_search = QLineEdit()
        self._history_search.setPlaceholderText("Search conversations...")
        self._history_search.setFont(QFont("Segoe UI", 10))
        self._history_search.setFixedHeight(38)
        self._history_search.setStyleSheet(
            """
            QLineEdit {
                background: rgba(10,11,14,205);
                color: #FFFFFF;
                border: 1px solid rgba(255,69,69,100);
                border-radius: 12px;
                padding: 0 12px;
            }
            QLineEdit:focus {
                border: 1px solid rgba(255,69,69,190);
            }
            """
        )
        self._history_search.textChanged.connect(self._refresh_history)
        lay.addWidget(self._history_search)

        self._history_scroll = QScrollArea()
        self._history_scroll.setWidgetResizable(True)
        self._history_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._history_scroll.setStyleSheet(
            """
            QScrollArea { background: transparent; border: none; }
            QScrollBar:vertical {
                background: transparent;
                width: 8px;
                border: none;
                margin: 6px 0 6px 0;
            }
            QScrollBar::handle:vertical {
                background: rgba(255,69,69,0.45);
                border-radius: 4px;
                min-height: 24px;
            }
            """
        )
        self._history_content = QWidget()
        self._history_content.setStyleSheet("background: transparent;")
        self._history_layout = QVBoxLayout(self._history_content)
        self._history_layout.setContentsMargins(0, 0, 0, 0)
        self._history_layout.setSpacing(10)
        self._history_layout.addStretch(1)
        self._history_scroll.setWidget(self._history_content)
        lay.addWidget(self._history_scroll, 1)
        return page

    def _ensure_active_conversation(self, first_message: str | None = None) -> str:
        convo_id = self._active_conversation_id
        if convo_id:
            convo = self._store.get_conversation(convo_id)
            if convo:
                return convo_id
        convo_id = self._store.ensure_active_conversation(first_message or "")
        self._active_conversation_id = convo_id
        return convo_id

    def _group_label(self, title: str) -> QLabel:
        lbl = QLabel(title.upper())
        lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        lbl.setStyleSheet("color: rgba(255,255,255,0.58); background: transparent; letter-spacing: 1px;")
        return lbl

    def _clear_layout(self, layout: QVBoxLayout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _refresh_history(self, *_):
        search = self._history_search.text().strip() if hasattr(self, "_history_search") else ""
        self._clear_layout(self._history_layout)
        groups = self._store.grouped_conversations(search)
        current = self._active_conversation_id
        if not groups:
            empty = QLabel("No conversations yet.")
            empty.setStyleSheet("color: rgba(255,255,255,0.60); background: transparent;")
            self._history_layout.addWidget(empty)
            self._history_layout.addStretch(1)
            return
        for group_name, items in groups.items():
            self._history_layout.addWidget(self._group_label(group_name))
            for item in items:
                widget = HistoryConversationItem(
                    item["id"],
                    item["title"],
                    _fmt_time_stamp(item["updatedAt"]),
                    pinned=bool(item["pinned"]))
                if item["id"] == current:
                    widget.setStyleSheet(
                        """
                        QFrame#HistoryConversationItem {
                            background: rgba(255,69,69,0.10);
                            border: 1px solid rgba(255,69,69,0.55);
                            border-radius: 10px;
                        }
                        QFrame#HistoryConversationItem:hover {
                            background: rgba(255,69,69,0.14);
                            border: 1px solid rgba(255,69,69,0.70);
                        }
                        """
                    )
                widget.clicked.connect(self._load_conversation)
                self._history_layout.addWidget(widget)
            self._history_layout.addSpacing(4)
        self._history_layout.addStretch(1)

    def _show_memory_banner(self, memories: list[dict[str, object]]):
        texts = [str(m.get("content") or "").strip() for m in memories if str(m.get("content") or "").strip()]
        if not texts:
            self._memory_items.setText("")
            self._memory_frame.hide()
            return
        self._memory_items.setText("• " + "\n• ".join(texts[:4]))
        self._memory_frame.show()

    def _hide_memory_banner(self):
        self._memory_items.setText("")
        self._memory_frame.hide()

    def _load_active_conversation(self):
        convo_id = self._ensure_active_conversation()
        convo = self._store.get_conversation(convo_id)
        if convo:
            self._feed.load_messages(convo.get("messages") or [])
            self._active_conversation_id = convo_id
            self._hide_memory_banner()

    def _load_conversation(self, conversation_id: str):
        convo = self._store.get_conversation(conversation_id)
        if not convo:
            return
        self._active_conversation_id = conversation_id
        self._store.set_active_conversation_id(conversation_id)
        self._feed.load_messages(convo.get("messages") or [])
        self._hide_memory_banner()
        self._refresh_history()
        self._set_tab(0)

    def _new_conversation(self):
        self._active_conversation_id = self._store.create_conversation("New Conversation")
        self._feed.clear_messages()
        self._hide_memory_banner()
        self._refresh_history()
        self._set_tab(0)

    def _clear_current_conversation(self):
        self._new_conversation()

    def _rename_current_conversation(self):
        convo_id = self._ensure_active_conversation()
        convo = self._store.get_conversation(convo_id)
        current = (convo or {}).get("title") or "Conversation"
        title, ok = QInputDialog.getText(self, "Rename Conversation", "Conversation title:", text=current)
        if ok and title.strip():
            self._store.rename_conversation(convo_id, title.strip())
            self._refresh_history()

    def _export_current_conversation(self):
        convo_id = self._ensure_active_conversation()
        convo = self._store.get_conversation(convo_id)
        title = (convo or {}).get("title") or "conversation"
        default = str(BASE_DIR / "downloads" / f"{title}.json")
        path, _ = QFileDialog.getSaveFileName(self, "Export Conversation", default, "JSON Files (*.json)")
        if not path:
            return
        try:
            self._store.export_conversation(convo_id, path)
        except Exception as _e:
            log_error(_e, context="ui", severity="debug")

    def _pin_current_conversation(self):
        convo_id = self._ensure_active_conversation()
        convo = self._store.get_conversation(convo_id)
        pinned = not bool((convo or {}).get("pinned"))
        self._store.pin_conversation(convo_id, pinned)
        self._refresh_history()

    def _delete_current_conversation(self):
        convo_id = self._ensure_active_conversation()
        self._store.delete_conversation(convo_id)
        self._active_conversation_id = None
        self._new_conversation()

    def show_at(self):
        self.show_workspace(animate=False)

    def reposition(self):
        if not self.isVisible():
            return
        screen = QApplication.primaryScreen().availableGeometry()
        x = screen.right() - self._expanded_w + 1
        y = screen.top() + 18
        h = max(640, screen.height() - 36)
        self.setGeometry(x, y, self._expanded_w, h)
        self._target_h = h
        self._panel.setGeometry(0, 0, self._expanded_w, h)

    def _dock_rect(self) -> QRectF:
        screen = QApplication.primaryScreen().availableGeometry()
        h = max(640, screen.height() - 36)
        y = screen.top() + 18
        w = self._expanded_w
        x = screen.right() - w + 1
        return QRectF(x, y, w, h)

    def _collapsed_rect(self) -> QRectF:
        screen = QApplication.primaryScreen().availableGeometry()
        h = max(640, screen.height() - 36)
        y = screen.top() + 18
        w = self._expanded_w
        x = screen.right() + 8
        return QRectF(x, y, w, h)

    def show_workspace(self, animate: bool = True):
        self._collapsed = False
        self._panel.show()
        self._content.show()
        dock = self._dock_rect()
        start = self._collapsed_rect() if animate else dock
        self.setGeometry(start.toRect())
        self.show()
        self.raise_()
        self.activateWindow()
        self._apply_state()
        if animate:
            self._anim.stop()
            self._anim.setStartValue(start.toRect())
            self._anim.setEndValue(dock.toRect())
            self._anim.start()
        else:
            self.setGeometry(dock.toRect())
        self._panel.setGeometry(0, 0, self.width(), self.height())

    def hide_workspace(self, animate: bool = True):
        self._collapsed = True
        if not self.isVisible():
            self._panel.hide()
            self.hide()
            return
        if animate:
            dock = self.geometry()
            end = self._collapsed_rect()
            self._anim.stop()
            self._anim.setStartValue(dock)
            self._anim.setEndValue(end.toRect())
            self._anim.finished.connect(self._hide_after_anim)
            self._anim.start()
        else:
            self._hide_after_anim()

    def _hide_after_anim(self):
        try:
            self._anim.finished.disconnect(self._hide_after_anim)
        except Exception as _e:
            log_error(_e, context="ui", severity="debug")
        self._panel.hide()
        self.hide()

    def toggle_collapsed(self):
        self.set_collapsed(not self._collapsed)

    def set_collapsed(self, collapsed: bool):
        if bool(collapsed):
            self.hide_workspace(animate=True)
        else:
            self.show_workspace(animate=True)

    def is_collapsed(self) -> bool:
        return self._collapsed

    def _apply_state(self):
        self._panel.setVisible(not self._collapsed)
        self._content.setVisible(not self._collapsed)
        if self.isVisible() and not self._collapsed:
            self.reposition()

    def append_log(self, text: str):
        raw = (text or "").strip()
        if not raw:
            return
        low = raw.lower()
        if low.startswith(("you:", "rex ai:")):
            return
        if low.startswith("sys:"):
            self.record_chat_event({"role": "system", "text": raw.split(":", 1)[1].strip(), "source": "local"})

    def record_chat_event(self, event: object):
        data = event if isinstance(event, dict) else {}
        role = (data.get("role") or "").strip().lower()
        text = (data.get("text") or data.get("content") or "").strip()
        if not role or not text:
            return
        attachments = data.get("attachments") or []
        stamp = data.get("timestamp")
        convo_id = data.get("conversation_id") or self._active_conversation_id
        if role == "user":
            convo_id = self._store.record_chat("user", text, conversation_id=convo_id, attachments=attachments)
            self._active_conversation_id = convo_id
            self._feed.add_message("user", "You", text, _fmt_time_stamp(stamp), attachments=attachments)
            memories = self._store.search_memories(text)
            self._show_memory_banner(memories)
        elif role == "assistant":
            convo_id = self._store.record_chat("assistant", text, conversation_id=convo_id, attachments=attachments)
            self._active_conversation_id = convo_id
            self._feed.add_message("assistant", "REX", text, _fmt_time_stamp(stamp), attachments=attachments, animate=True)
            self._hide_memory_banner()
        elif role == "system":
            convo_id = self._store.record_chat("system", text, conversation_id=convo_id, attachments=attachments)
            self._active_conversation_id = convo_id
            self._feed.add_message("system", "System", text, _fmt_time_stamp(stamp), attachments=attachments, event_type=text)
        elif role == "file":
            convo_id = self._store.record_chat("assistant", text, conversation_id=convo_id, attachments=attachments)
            self._active_conversation_id = convo_id
            self._feed.add_message("file", "Files", text, _fmt_time_stamp(stamp), attachments=attachments)
        self._refresh_history()

    def apply_task_workspace(self, event: object):
        data = event if isinstance(event, dict) else {}
        action = (data.get("action") or "update").strip().lower()
        if action == "start":
            command = data.get("command") or ""
            plan = data.get("plan") or []
            plan_text = plan if isinstance(plan, str) else "\n".join(f"• {item}" for item in plan) if plan else ""
            self._task_card.show()
            self._task_card.start_workspace(command, plan, data.get("source") or "local")
            # Intentionally do not post a 'Task started' system message to the activity feed
            # because the workspace UI already shows the task status.
        elif action == "update":
            self._task_card.show()
            self._task_card.update_workspace(
                title=data.get("title"),
                command=data.get("command"),
                plan=data.get("plan"),
                status=data.get("status"),
                output=data.get("output"),
                percent=data.get("percent"),
                footer=data.get("footer"))
            chunks = [data.get("status") or "", data.get("output") or "", data.get("footer") or ""]
            text = "\n".join(chunk for chunk in chunks if chunk)
            if text:
                self.record_chat_event({"role": "system", "text": text, "source": data.get("source") or "local"})
        elif action == "finish":
            self._task_card.show()
            self._task_card.finish_workspace(
                data.get("result") or data.get("output") or "Done.",
                data.get("status") or "Task completed.",
                int(data.get("percent") or 100))
            self.record_chat_event({
                "role": "system",
                "text": data.get("result") or data.get("output") or "Done.",
                "source": data.get("source") or "local",
            })
        elif action == "clear":
            self._task_card.clear_workspace()

    def _send(self):
        text = self._input.text().strip()
        if not text:
            return
        self._input.clear()
        self._ensure_active_conversation(text)
        self.command_submitted.emit(text)



class InlineChatWorkspace(QFrame):
    command_submitted = pyqtSignal(str)
    attach_requested = pyqtSignal()
    mic_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("InlineChatWorkspace")
        self._store = workspace_store()
        self._active_conversation_id: str | None = None
        self.setStyleSheet(
            """
            QFrame#InlineChatWorkspace {
                background: transparent;
                border: none;
            }
            """
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("CHAT + TASK WORKSPACE")
        title.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        title.setStyleSheet("color: #FFFFFF; background: transparent; letter-spacing: 1px;")
        header.addWidget(title)
        header.addStretch(1)
        root.addLayout(header)

        tabs = QHBoxLayout()
        tabs.setSpacing(8)
        self._chat_btn = self._mk_tab("CHAT", True)
        self._history_btn = self._mk_tab("HISTORY", False)
        self._chat_btn.clicked.connect(lambda: self._set_tab(0))
        self._history_btn.clicked.connect(lambda: self._set_tab(1))
        tabs.addWidget(self._chat_btn)
        tabs.addWidget(self._history_btn)
        tabs.addStretch(1)
        root.addLayout(tabs)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_chat_tab())
        self._stack.addWidget(self._build_history_tab())
        root.addWidget(self._stack, 1)

        self._set_tab(0)
        self._ensure_conversation()
        self._load_active_conversation()
        self._refresh_history()

    def _mk_tab(self, text: str, active: bool) -> QPushButton:
        btn = QPushButton(text)
        btn.setCheckable(True)
        btn.setChecked(active)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedHeight(34)
        btn.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        btn.setStyleSheet(
            """
            QPushButton {
                background: rgba(255,255,255,0.04);
                color: rgba(255,255,255,0.82);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 10px;
                padding: 0 14px;
            }
            QPushButton:hover {
                background: rgba(255,69,69,0.08);
                border: 1px solid rgba(255,69,69,0.30);
            }
            """
        )
        return btn

    def _set_tab(self, index: int):
        self._stack.setCurrentIndex(0 if index == 0 else 1)
        self._chat_btn.setChecked(index == 0)
        self._history_btn.setChecked(index == 1)
        if index == 0:
            self._chat_btn.setStyleSheet("""
                QPushButton { background: rgba(255,69,69,0.16); color: #FFFFFF; border: 1px solid rgba(255,69,69,180); border-radius: 10px; padding: 0 14px; }
            """)
            self._history_btn.setStyleSheet("""
                QPushButton { background: rgba(255,255,255,0.04); color: rgba(255,255,255,0.82); border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; padding: 0 14px; }
                QPushButton:hover { background: rgba(255,69,69,0.08); border: 1px solid rgba(255,69,69,0.30); }
            """)
        else:
            self._history_btn.setStyleSheet("""
                QPushButton { background: rgba(255,69,69,0.16); color: #FFFFFF; border: 1px solid rgba(255,69,69,180); border-radius: 10px; padding: 0 14px; }
            """)
            self._chat_btn.setStyleSheet("""
                QPushButton { background: rgba(255,255,255,0.04); color: rgba(255,255,255,0.82); border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; padding: 0 14px; }
                QPushButton:hover { background: rgba(255,69,69,0.08); border: 1px solid rgba(255,69,69,0.30); }
            """)

    def _build_chat_tab(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        self._task_card = TaskCard()
        self._task_card.hide()
        lay.addWidget(self._task_card)

        self._memory_frame = QFrame()
        self._memory_frame.setVisible(False)
        self._memory_frame.setStyleSheet("""
            QFrame {
                background: rgba(255,69,69,0.05);
                border: 1px solid rgba(255,69,69,0.24);
                border-radius: 12px;
            }
        """)
        mlay = QVBoxLayout(self._memory_frame)
        mlay.setContentsMargins(12, 10, 12, 10)
        mlay.setSpacing(6)
        title = QLabel("Using Memory:")
        title.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        title.setStyleSheet("color: #FFFFFF; background: transparent;")
        self._memory_lbl = QLabel("")
        self._memory_lbl.setWordWrap(True)
        self._memory_lbl.setStyleSheet("color: rgba(255,255,255,0.75); background: transparent;")
        mlay.addWidget(title)
        mlay.addWidget(self._memory_lbl)
        lay.addWidget(self._memory_frame)

        self._feed = ConversationFeed()
        lay.addWidget(self._feed, 1)

        # Right sidebar chat is now read-only; commands are entered through the center dashboard command bar.
        return page

    def _build_history_tab(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search conversations...")
        self._search.setFont(QFont("Segoe UI", 10))
        self._search.setFixedHeight(38)
        self._search.setStyleSheet("QLineEdit { background: rgba(10,11,14,205); color: #FFFFFF; border: 1px solid rgba(255,69,69,100); border-radius: 12px; padding: 0 12px; }")
        self._search.textChanged.connect(self._refresh_history)
        lay.addWidget(self._search)
        self._history_scroll = QScrollArea()
        self._history_scroll.setWidgetResizable(True)
        self._history_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._history_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._history_content = QWidget()
        self._history_content.setStyleSheet("background: transparent;")
        self._history_layout = QVBoxLayout(self._history_content)
        self._history_layout.setContentsMargins(0, 0, 0, 0)
        self._history_layout.setSpacing(10)
        self._history_layout.addStretch(1)
        self._history_scroll.setWidget(self._history_content)
        lay.addWidget(self._history_scroll, 1)
        return page

    def _ensure_conversation(self, first_message: str | None = None):
        if self._active_conversation_id:
            convo = self._store.get_conversation(self._active_conversation_id)
            if convo:
                return self._active_conversation_id
        self._active_conversation_id = self._store.ensure_active_conversation(first_message or "")
        return self._active_conversation_id

    def _clear_layout(self, layout: QVBoxLayout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _refresh_history(self, *_):
        search = self._search.text().strip() if hasattr(self, "_search") else ""
        self._clear_layout(self._history_layout)
        groups = self._store.grouped_conversations(search)
        if not groups:
            empty = QLabel("No conversations yet.")
            empty.setStyleSheet("color: rgba(255,255,255,0.60); background: transparent;")
            self._history_layout.addWidget(empty)
            self._history_layout.addStretch(1)
            return
        for group_name, items in groups.items():
            self._history_layout.addWidget(self._group_label(group_name))
            for item in items:
                card = HistoryConversationItem(item["id"], item["title"], _fmt_time_stamp(item["updatedAt"]), pinned=bool(item["pinned"]))
                card.clicked.connect(self._open_conversation)
                self._history_layout.addWidget(card)
        self._history_layout.addStretch(1)

    def _group_label(self, title: str) -> QLabel:
        lbl = QLabel(title.upper())
        lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        lbl.setStyleSheet("color: rgba(255,255,255,0.58); background: transparent; letter-spacing: 1px;")
        return lbl

    def _show_memories(self, memories: list[dict[str, object]]):
        texts = [str(m.get("content") or "").strip() for m in memories if str(m.get("content") or "").strip()]
        if not texts:
            self._memory_lbl.setText("")
            self._memory_frame.hide()
            return
        self._memory_lbl.setText("• " + "\n• ".join(texts[:4]))
        self._memory_frame.show()

    def _hide_memories(self):
        self._memory_lbl.setText("")
        self._memory_frame.hide()

    def _open_conversation(self, conversation_id: str):
        convo = self._store.get_conversation(conversation_id)
        if not convo:
            return
        self._active_conversation_id = conversation_id
        self._store.set_active_conversation_id(conversation_id)
        self._feed.load_messages(convo.get("messages") or [])
        self._hide_memories()
        self._refresh_history()
        self._set_tab(0)

    def _load_active_conversation(self):
        convo_id = self._ensure_conversation()
        convo = self._store.get_conversation(convo_id)
        if convo:
            self._feed.load_messages(convo.get("messages") or [])
        self._refresh_history()

    def record_chat_event(self, event: object):
        data = event if isinstance(event, dict) else {}
        role = (data.get("role") or "").strip().lower()
        text = (data.get("text") or data.get("content") or "").strip()
        if not role or not text:
            return
        convo_id = data.get("conversation_id") or self._ensure_conversation(text if role == "user" else None)
        attachments = data.get("attachments") or []
        stamp = _fmt_time_stamp(data.get("timestamp"))
        if role == "user":
            self._store.record_chat("user", text, conversation_id=convo_id, attachments=attachments)
            self._feed.add_message("user", "You", text, stamp, attachments=attachments)
            self._show_memories(self._store.search_memories(text))
        elif role == "assistant":
            self._store.record_chat("assistant", text, conversation_id=convo_id, attachments=attachments)
            self._feed.add_message("assistant", "REX", text, stamp, attachments=attachments)
            self._hide_memories()
        elif role == "system":
            self._store.record_chat("system", text, conversation_id=convo_id, attachments=attachments)
            self._feed.add_message("system", "System", text, stamp, attachments=attachments)
        elif role == "file":
            self._store.record_chat("assistant", text, conversation_id=convo_id, attachments=attachments)
            self._feed.add_message("file", "Files", text, stamp, attachments=attachments)
        self._refresh_history()

    def append_log(self, text: str):
        raw = (text or "").strip()
        if not raw:
            return
        low = raw.lower()
        if low.startswith("sys:"):
            self.record_chat_event({"role": "system", "text": raw.split(":", 1)[1].strip()})

    def apply_task_workspace(self, event: object):
        data = event if isinstance(event, dict) else {}
        action = (data.get("action") or "update").strip().lower()
        if action == "start":
            command = data.get("command") or ""
            plan = data.get("plan") or []
            plan_text = plan if isinstance(plan, str) else "\n".join(f"• {item}" for item in plan) if plan else ""
            self._task_card.start_workspace(command, plan, data.get("source") or "local")
            # Do not emit a 'Task started' system event into the conversation feed.
        elif action == "update":
            self._task_card.update_workspace(
                title=data.get("title"),
                command=data.get("command"),
                plan=data.get("plan"),
                status=data.get("status"),
                output=data.get("output"),
                percent=data.get("percent"),
                footer=data.get("footer"))
            chunks = [data.get("status") or "", data.get("output") or "", data.get("footer") or ""]
            text = "\n".join(chunk for chunk in chunks if chunk)
            if text:
                self.record_chat_event({"role": "system", "text": text, "source": data.get("source") or "local"})
        elif action == "finish":
            self._task_card.finish_workspace(
                data.get("result") or data.get("output") or "Done.",
                data.get("status") or "Task completed.",
                int(data.get("percent") or 100))
            self.record_chat_event({
                "role": "system",
                "text": data.get("result") or data.get("output") or "Done.",
                "source": data.get("source") or "local",
            })
        elif action == "clear":
            self._task_card.clear_workspace()

    def _send(self):
        text = self._input.text().strip()
        if not text:
            return
        self._input.clear()
        self._ensure_conversation(text)
        self.command_submitted.emit(text)



class LauncherControlPanel(QDialog):
    def __init__(self, *, startup_workspace: bool = False, on_open=None, on_close=None,
                 on_toggle_startup=None, on_hide_icon=None, on_restart=None, on_quit=None,
                 on_open_app=None,
                 on_show_icon=None,
                 on_open_dev=None,
                 parent=None):
        super().__init__(parent)
        self._on_open = on_open
        self._on_close = on_close
        self._on_toggle_startup = on_toggle_startup
        self._on_hide_icon = on_hide_icon
        self._on_restart = on_restart
        self._on_quit = on_quit
        self._on_open_app = on_open_app
        self._on_show_icon = on_show_icon
        self._on_open_dev = on_open_dev

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(False)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        frame = QFrame()
        frame.setStyleSheet("""
            QFrame {
                background: rgba(15,15,20,235);
                border: 1px solid rgba(0,191,255,60);
                border-radius: 18px;
            }
        """)
        root.addWidget(frame)

        lay = QVBoxLayout(frame)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)

        title = QLabel("REX CONTROL")
        title.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        title.setStyleSheet("color: #FFFFFF; background: transparent; letter-spacing: 1px;")
        lay.addWidget(title)

        sub = QLabel("Desktop launcher controls")
        sub.setFont(QFont("Segoe UI", 9))
        sub.setStyleSheet("color: rgba(255,255,255,0.65); background: transparent;")
        lay.addWidget(sub)

        def mk_btn(text: str, *, checkable: bool = False, checked: bool = False) -> QPushButton:
            btn = QPushButton(text)
            btn.setCheckable(checkable)
            btn.setChecked(checked)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setMinimumHeight(36)
            btn.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
            btn.setStyleSheet("""
                QPushButton {
                    background: rgba(255,255,255,0.05);
                    color: #FFFFFF;
                    border: 1px solid rgba(255,255,255,0.08);
                    border-radius: 12px;
                    padding: 6px 12px;
                    text-align: left;
                }
                QPushButton:hover {
                    background: rgba(0,191,255,0.10);
                    border: 1px solid rgba(0,191,255,0.45);
                }
                QPushButton:checked {
                    background: rgba(0,191,255,0.16);
                    border: 1px solid rgba(0,191,255,0.60);
                }
            """)
            return btn

        self._open_btn = mk_btn("Open Workspace")
        self._close_btn = mk_btn("Close Workspace")
        self._startup_btn = mk_btn("Show Workspace On Startup", checkable=True, checked=bool(startup_workspace))
        self._show_icon_btn = mk_btn("Show Floating Icon")
        self._hide_icon_btn = mk_btn("Hide Floating Icon")
        self._restart_btn = mk_btn("Restart REX")
        self._quit_btn = mk_btn("Quit REX")
        self._open_app_btn = mk_btn("Open App")
        self._open_dev_btn = mk_btn("Open Developer Mode")

        self._open_btn.clicked.connect(lambda: self._invoke(self._on_open))
        self._close_btn.clicked.connect(lambda: self._invoke(self._on_close))
        self._startup_btn.clicked.connect(lambda: self._invoke(self._on_toggle_startup, self._startup_btn.isChecked()))
        self._show_icon_btn.clicked.connect(lambda: self._invoke(self._on_show_icon))
        self._hide_icon_btn.clicked.connect(self._hide_icon_confirm)
        self._restart_btn.clicked.connect(lambda: self._invoke(self._on_restart))
        self._quit_btn.clicked.connect(lambda: self._invoke(self._on_quit))
        self._open_app_btn.clicked.connect(lambda: self._invoke(self._on_open_app))
        self._open_dev_btn.clicked.connect(lambda: self._invoke(self._on_open_dev))

        for btn in (
            self._open_app_btn, self._open_btn, self._close_btn, self._startup_btn,
            self._show_icon_btn, self._hide_icon_btn, self._open_dev_btn,
            self._restart_btn, self._quit_btn
        ):
            lay.addWidget(btn)

        self.adjustSize()

    def _invoke(self, fn, *args):
        if fn:
            try:
                fn(*args)
            except Exception as _e:
                log_error(_e, context="ui", severity="debug")
        self.close()

    def _hide_icon_confirm(self):
        box = QDialog(self)
        box.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        box.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        frame = QFrame()
        frame.setStyleSheet("QFrame { background: rgba(15,15,20,240); border: 1px solid rgba(0,191,255,60); border-radius: 16px; }")
        lay.addWidget(frame)
        flay = QVBoxLayout(frame)
        flay.setContentsMargins(18, 16, 18, 16)
        flay.setSpacing(10)
        lbl = QLabel("Hide REX icon?")
        lbl.setStyleSheet("color: #FFFFFF; background: transparent; font: 700 11pt 'Segoe UI';")
        sub = QLabel("You can restore it from the system tray.")
        sub.setStyleSheet("color: rgba(255,255,255,0.65); background: transparent;")
        flay.addWidget(lbl)
        flay.addWidget(sub)
        row = QHBoxLayout()
        cancel = QPushButton("Cancel")
        hide = QPushButton("Hide")
        for btn in (cancel, hide):
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setMinimumHeight(34)
            btn.setStyleSheet("QPushButton { background: rgba(255,255,255,0.05); color: #FFFFFF; border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; } QPushButton:hover { border: 1px solid #00BFFF; }")
        cancel.clicked.connect(box.reject)
        hide.clicked.connect(box.accept)
        row.addWidget(cancel)
        row.addWidget(hide)
        flay.addLayout(row)
        box.adjustSize()
        screen = QApplication.primaryScreen().availableGeometry()
        box.move(screen.center().x() - box.width() // 2, screen.center().y() - box.height() // 2)
        if box.exec():
            self._invoke(self._on_hide_icon)

    def set_startup_workspace(self, enabled: bool):
        self._startup_btn.setChecked(bool(enabled))



class SmallPanelCard(QFrame):
    def __init__(self, title: str, body: str, *, accent: str = C.WHITE, parent=None):
        super().__init__(parent)
        self.setObjectName("SmallPanelCard")
        self.setStyleSheet(
            f"""
            QFrame#SmallPanelCard {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(16, 18, 24, 230),
                    stop:1 rgba(9, 11, 15, 210));
                border: 1px solid rgba(255, 255, 255, 0.16);
                border-radius: 14px;
            }}
            """
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(6)

        t = QLabel(title.upper())
        t.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        t.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent; letter-spacing: 1px;")
        lay.addWidget(t)

        self._body_lbl = QLabel(body)
        self._body_lbl.setWordWrap(True)
        self._body_lbl.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self._body_lbl.setStyleSheet(f"color: {accent}; background: transparent;")
        lay.addWidget(self._body_lbl)

    def set_body(self, body: str):
        self._body_lbl.setText(body)



class StatCard(QFrame):
    def __init__(self, label: str, value: str, parent=None):
        super().__init__(parent)
        self.setObjectName("StatCard")
        self.setStyleSheet(
            f"""
            QFrame#StatCard {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(18, 20, 26, 240),
                    stop:1 rgba(8, 10, 14, 220));
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 16px;
            }}
            """
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(4)
        lbl = QLabel(label.upper())
        lbl.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        val = QLabel(value)
        val.setFont(QFont("Segoe UI", 15, QFont.Weight.Bold))
        val.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        self._detail_lbl = QLabel("")
        self._detail_lbl.setFont(QFont("Segoe UI", 7))
        self._detail_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(5)
        self._bar.setStyleSheet(
            f"""
            QProgressBar {{
                background: rgba(255,255,255,0.05);
                border: none;
                border-radius: 2px;
            }}
            QProgressBar::chunk {{
                background: {C.WHITE};
                border-radius: 2px;
            }}
            """
        )
        lay.addWidget(lbl)
        lay.addWidget(val)
        lay.addWidget(self._detail_lbl)
        lay.addWidget(self._bar)
        self._value_lbl = val

    def set_value(self, value: str, level: int | None = None, detail: str | None = None):
        self._value_lbl.setText(value)
        if detail is not None:
            self._detail_lbl.setText(detail)
        if level is not None:
            self._bar.setValue(max(0, min(100, int(level))))



class LogWidget(QScrollArea):
    _sig = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(
            f"""
            QScrollArea {{
                background: transparent;
                border: none;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 8px;
                border: none;
                margin: 6px 0 6px 0;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B};
                border-radius: 4px;
                min-height: 24px;
            }}
            """
        )
        self._content = QWidget()
        self._content.setStyleSheet("background: transparent;")
        self._layout = QVBoxLayout(self._content)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(10)
        self._layout.addStretch(1)
        self.setWidget(self._content)

        self._sig.connect(self._enqueue)

    def append_log(self, text: str):
        self._sig.emit(text)

    def _enqueue(self, text: str):
        role, name, body = self._parse(text)
        stamp = time.strftime("%H:%M")

        card = MessageCard(role, name, body, stamp)
        self._layout.insertWidget(self._layout.count() - 1, card)
        QTimer.singleShot(0, self._scroll_bottom)

    def _scroll_bottom(self):
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _parse(self, text: str) -> tuple[str, str, str]:
        raw = (text or "").strip()
        tl = raw.lower()
        if tl.startswith("you:"):
            return "user", "You", raw[4:].strip()
        if tl.startswith("rex ai:"):
            return "assistant", "REX", raw[len("REX:"):].strip()
        if tl.startswith("rex:"):
            return "assistant", "REX", raw[len("REX:"):].strip()
        if tl.startswith("file:"):
            return "file", "File", raw[5:].strip()
        if tl.startswith("err:"):
            return "error", "System", raw[4:].strip()
        if tl.startswith("sys:"):
            return "system", "System", raw[4:].strip()
        return "system", "System", raw

_FILE_ICONS = {
    "image":   ("ðŸ-¼", "#00d4ff"), "video":   ("ðŸŽ¬", "#ff6b00"),
    "audio":   ("ðŸŽµ", "#cc44ff"), "pdf":     ("ðŸ“„", "#ff4444"),
    "word":    ("ðŸ“", "#4488ff"), "excel":   ("ðŸ“Š", "#44bb44"),
    "code":    ("ðŸ’»", "#ffcc00"), "archive": ("ðŸ“¦", "#ff8844"),
    "pptx":    ("ðŸ“Š", "#ff6622"), "text":    ("ðŸ“ƒ", "#aaaaaa"),
    "data":    ("ðŸ”§", "#88ddff"), "unknown": ("ðŸ“Ž", "#888888"),
}
_EXT_TO_CAT = {
    **dict.fromkeys(["jpg","jpeg","png","gif","webp","bmp","tiff","svg","ico"], "image"),
    **dict.fromkeys(["mp4","avi","mov","mkv","wmv","flv","webm","m4v"],         "video"),
    **dict.fromkeys(["mp3","wav","ogg","m4a","aac","flac","wma","opus"],        "audio"),
    **dict.fromkeys(["pdf"],                                                     "pdf"),
    **dict.fromkeys(["doc","docx"],                                              "word"),
    **dict.fromkeys(["xls","xlsx","ods"],                                        "excel"),
    **dict.fromkeys(["ppt","pptx"],                                              "pptx"),
    **dict.fromkeys(["py","js","ts","jsx","tsx","html","css","java","c","cpp",
                     "cs","go","rs","rb","php","swift","kt","sh","sql","lua"],   "code"),
    **dict.fromkeys(["zip","rar","tar","gz","7z","bz2","xz"],                   "archive"),
    **dict.fromkeys(["txt","md","rst","log"],                                    "text"),
    **dict.fromkeys(["csv","tsv","json","xml"],                                  "data"),
}


class FileDropZone(QWidget):
    file_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(100)
        self._current_file: str | None = None
        self._hovering  = False
        self._drag_over = False
        self._dash_offset = 0.0
        self._anim_tmr = QTimer(self)
        self._anim_tmr.timeout.connect(self._animate)
        self._anim_tmr.start(40)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._canvas = _DropCanvas(self)
        layout.addWidget(self._canvas)

    def _animate(self):
        self._dash_offset = (self._dash_offset + 0.8) % 20
        self._canvas.update()

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._drag_over = True; self._canvas.update()

    def dragLeaveEvent(self, e):
        self._drag_over = False; self._canvas.update()

    def dropEvent(self, e: QDropEvent):
        self._drag_over = False
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if Path(path).is_file():
                self._set_file(path)
        self._canvas.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._browse()

    def enterEvent(self, e):
        self._hovering = True; self._canvas.update()

    def leaveEvent(self, e):
        self._hovering = False; self._canvas.update()

    def current_file(self) -> str | None:
        return self._current_file

    def clear_file(self):
        self._current_file = None; self._canvas.update()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a file for REX", str(Path.home()),
            "All Files (*.*);;"
            "Images (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg);;"
            "Documents (*.pdf *.docx *.txt *.md *.pptx);;"
            "Data (*.csv *.xlsx *.json *.xml);;"
            "Code (*.py *.js *.ts *.html *.css *.java *.cpp *.go);;"
            "Audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac);;"
            "Video (*.mp4 *.avi *.mov *.mkv *.wmv *.webm);;"
            "Archives (*.zip *.rar *.tar *.gz *.7z)")
        if path:
            self._set_file(path)

    def _set_file(self, path: str):
        self._current_file = path
        self._canvas.update()
        self.file_selected.emit(path)



class _DropCanvas(QWidget):
    def __init__(self, zone: FileDropZone):
        super().__init__(zone)
        self._z = zone

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        z    = self._z
        W, H = self.width(), self.height()
        pad  = 6
        rect = QRectF(pad, pad, W - pad * 2, H - pad * 2)

        bg_col = qcol("#001a24" if z._drag_over else ("#001218" if z._hovering else C.PANEL))
        p.setBrush(QBrush(bg_col)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   border_col = qcol(C.GREEN, 200)
        elif z._drag_over:    border_col = qcol(C.PRI, 230)
        elif z._hovering:     border_col = qcol(C.BORDER_B, 200)
        else:                 border_col = qcol(C.BORDER, 160)

        pen = QPen(border_col, 1.5, Qt.PenStyle.DashLine)
        pen.setDashOffset(z._dash_offset)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   self._paint_file(p, W, H)
        elif z._drag_over:    self._paint_drag_over(p, W, H)
        else:                 self._paint_idle(p, W, H, z._hovering)

    def _paint_idle(self, p, W, H, hover):
        cx, cy = W / 2, H / 2
        col = qcol(C.PRI_DIM if not hover else C.PRI)
        p.setPen(QPen(col, 2)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(cx, cy - 14), QPointF(cx, cy + 4))
        p.drawLine(QPointF(cx - 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx + 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx - 14, cy + 4), QPointF(cx + 14, cy + 4))
        p.setFont(QFont("Courier New", 8))
        p.setPen(QPen(qcol(C.PRI_DIM if not hover else C.TEXT), 1))
        p.drawText(QRectF(0, cy + 8, W, 16), Qt.AlignmentFlag.AlignCenter,
                   "Drop file here  or  Click to Browse")
        p.setFont(QFont("Courier New", 7))
        p.setPen(QPen(qcol("#1a4a5a"), 1))
        p.drawText(QRectF(0, cy + 24, W, 14), Qt.AlignmentFlag.AlignCenter,
                   "Images Â· Video Â· Audio Â· PDF Â· Docs Â· Code Â· Data")

    def _paint_drag_over(self, p, W, H):
        cx, cy = W / 2, H / 2
        p.setFont(QFont("Courier New", 20))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy - 24, W, 32), Qt.AlignmentFlag.AlignCenter, ">")
        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy + 12, W, 16), Qt.AlignmentFlag.AlignCenter, "Release to load")

    def _paint_file(self, p, W, H):
        path = Path(self._z._current_file)
        cat  = _file_category(path)
        icon, icon_col = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size_str = _fmt_size(path.stat().st_size)
        ext_str  = path.suffix.upper().lstrip(".") or "FILE"

        block_x, block_w = 10, 60
        p.setFont(QFont("Segoe UI Emoji", 22) if _OS == "Windows" else QFont("Arial", 22))
        p.setPen(QPen(qcol(icon_col), 1))
        p.drawText(QRectF(block_x, 0, block_w, H), Qt.AlignmentFlag.AlignCenter, icon)

        tx = block_x + block_w + 6
        tw = W - tx - 38

        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.WHITE), 1))
        name = path.name if len(path.name) <= 34 else path.name[:31] + "..."
        p.drawText(QRectF(tx, H * 0.18, tw, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)

        p.setFont(QFont("Courier New", 7))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(tx, H * 0.18 + 18, tw, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{ext_str}  Â·  {size_str}")

        p.setFont(QFont("Courier New", 6))
        p.setPen(QPen(qcol("#1e5c6a"), 1))
        par = str(path.parent)
        if len(par) > 42: par = "..." + par[-41:]
        p.drawText(QRectF(tx, H * 0.18 + 34, tw, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, par)

        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.RED, 180), 1))
        p.drawText(QRectF(W - 34, 0, 28, H), Qt.AlignmentFlag.AlignCenter, "'")

    def mousePressEvent(self, e):
        z = self._z
        if z._current_file and e.pos().x() > self.width() - 34:
            z.clear_file()
        else:
            z.mousePressEvent(e)



class SetupOverlay(QWidget):
    done = pyqtSignal(str, str, str)

    def __init__(self, parent=None, defaults: dict | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            SetupOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)

        defaults = defaults or {}

        detected = {"darwin": "mac", "windows": "windows"}.get(
            _OS.lower(), "linux"
        )
        self._sel_os = detected

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 22, 30, 22)
        layout.setSpacing(8)

        def _lbl(txt, font_size=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Courier New", font_size,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        layout.addWidget(_lbl("*  INITIALIZATION REQUIRED", 13, True))
        layout.addWidget(_lbl("Configure REX before first boot.", 9, color=C.PRI_DIM))
        layout.addSpacing(6)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep)
        layout.addSpacing(4)

        layout.addWidget(_lbl("GEMINI API KEY", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        self._key_input = QLineEdit()
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setPlaceholderText("AIza...")
        self._key_input.setFont(QFont("Courier New", 10))
        self._key_input.setFixedHeight(32)
        self._key_input.setStyleSheet(f"""
            QLineEdit {{
                background: #000d12; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        layout.addWidget(self._key_input)
        self._key_input.setText((defaults.get("gemini_api_key") or "").strip())
        layout.addSpacing(8)

        layout.addWidget(_lbl("OPENROUTER API KEY", 8, color=C.TEXT_DIM,
                       align=Qt.AlignmentFlag.AlignLeft))
        self._or_input = QLineEdit()
        self._or_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._or_input.setPlaceholderText("sk-or-...")
        self._or_input.setFont(QFont("Courier New", 10))
        self._or_input.setFixedHeight(32)
        self._or_input.setStyleSheet(f"""
            QLineEdit {{
                background: #000d12; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.ACC2}; }}
        """)
        layout.addWidget(self._or_input)
        self._or_input.setText((defaults.get("openrouter_api_key") or "").strip())

        layout.addSpacing(12)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep2)
        layout.addSpacing(4)

        layout.addWidget(_lbl("OPERATING SYSTEM", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        os_default = (defaults.get("os_system") or detected).strip().lower()
        if os_default not in {"windows", "mac", "linux"}:
            os_default = detected
        det_name = {"windows": "Windows", "mac": "macOS", "linux": "Linux"}[detected]
        layout.addWidget(_lbl(f"Auto-detected: {det_name}", 8, color=C.ACC2,
                               align=Qt.AlignmentFlag.AlignLeft))

        os_row = QHBoxLayout(); os_row.setSpacing(6)
        self._os_btns: dict[str, QPushButton] = {}
        for key, label in [("windows","Windows"),("mac","macOS"),("linux","Linux")]:
            btn = QPushButton(label)
            btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
            btn.setFixedHeight(32)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._sel(k))
            os_row.addWidget(btn)
            self._os_btns[key] = btn
        layout.addLayout(os_row)
        self._sel(os_default)
        layout.addSpacing(12)

        self._status = QLabel("Enter your Gemini key to continue. OpenRouter remains optional; the assistant uses the shared app configuration.")
        self._status.setWordWrap(True)
        self._status.setFont(QFont("Courier New", 8))
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        layout.addWidget(self._status)
        layout.addSpacing(8)

        init_btn = QPushButton(">  INITIALIZE SYSTEMS")
        init_btn.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        init_btn.setFixedHeight(36)
        init_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        init_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO}; border: 1px solid {C.PRI};
            }}
        """)
        init_btn.clicked.connect(self._submit)
        layout.addWidget(init_btn)

    def _sel(self, key: str):
        self._sel_os = key
        pal = {"windows":(C.PRI,"#001a22"),"mac":(C.ACC2,"#1a1400"),"linux":(C.GREEN,"#001a0d")}
        for k, btn in self._os_btns.items():
            if k == key:
                fg, bg = pal[k]
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {fg}; color: {bg};
                        border: none; border-radius: 3px; font-weight: bold;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: #000d12; color: {C.TEXT_DIM};
                        border: 1px solid {C.BORDER}; border-radius: 3px;
                    }}
                    QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
                """)

    def _submit(self):
        key = self._key_input.text().strip()
        or_key = self._or_input.text().strip()
        if not key:
            self._key_input.setStyleSheet(
                self._key_input.styleSheet() +
                f" QLineEdit {{ border: 1px solid {C.RED}; }}"
            )
            self._status.setText("Gemini key is required.")
            return
        if or_key and not or_key.startswith("sk-or-"):
            self._status.setText("OpenRouter key looks invalid. Continuing with Gemini only.")
            or_key = ""
        else:
            self._status.setText("Saving settings...")
        self.done.emit(key, or_key, self._sel_os)



class CommandBar(QWidget):
    submitted = pyqtSignal(str)
    attach_clicked = pyqtSignal()
    mic_clicked = pyqtSignal()
    developer_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setObjectName("CommandBar")
        self.setFixedSize(410, 72)
        self.setStyleSheet("background: transparent;")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        frame = QFrame()
        frame.setObjectName("CommandBarFrame")
        frame.setStyleSheet(f"""
            QFrame#CommandBarFrame {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(8, 8, 8, 248),
                    stop:0.5 rgba(15, 15, 15, 248),
                    stop:1 rgba(8, 8, 8, 248));
                border: 1px solid {C.BORDER_B};
                border-radius: 18px;
            }}
        """)
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)

        lay.addWidget(_framed_logo(36, 24, bg="rgba(255,255,255,0.04)", border=C.BORDER_B, radius=18, inset=5))

        self._input = QLineEdit()
        self._input.setPlaceholderText("Tell REX what to do...")
        self._input.setFont(QFont("Segoe UI", 10))
        self._input.setFixedHeight(40)
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background: rgba(16,16,16,240);
                color: {C.WHITE};
                border: 1px solid {C.BORDER};
                border-radius: 14px;
                padding: 0 14px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.BORDER_B}; }}
        """)
        self._input.returnPressed.connect(self._submit)
        lay.addWidget(self._input, stretch=1)

        attach = QPushButton()
        attach.setFixedSize(40, 40)
        attach.setCursor(Qt.CursorShape.PointingHandCursor)
        attach.setToolTip("Attach file")
        attach.setIcon(QIcon(_icon_pixmap("attach", 18)))
        attach.setIconSize(QSize(18, 18))
        attach.setStyleSheet(f"""
            QPushButton {{
                background: rgba(18,18,18,240);
                color: {C.WHITE};
                border: 1px solid {C.BORDER_B};
                border-radius: 12px;
            }}
            QPushButton:hover {{
                background: rgba(28,28,28,245);
                border: 1px solid {C.WHITE};
            }}
        """)
        attach.clicked.connect(self.attach_clicked.emit)
        lay.addWidget(attach)

        mic = QPushButton()
        mic.setFixedSize(40, 40)
        mic.setCursor(Qt.CursorShape.PointingHandCursor)
        mic.setToolTip("Microphone")
        mic.setIcon(QIcon(_icon_pixmap("mic", 18)))
        mic.setIconSize(QSize(18, 18))
        mic.setStyleSheet(f"""
            QPushButton {{
                background: rgba(18,18,18,240);
                color: {C.WHITE};
                border: 1px solid {C.BORDER_B};
                border-radius: 12px;
            }}
            QPushButton:hover {{
                background: rgba(28,28,28,245);
                border: 1px solid {C.WHITE};
            }}
        """)
        mic.clicked.connect(self.mic_clicked.emit)
        lay.addWidget(mic)

        dev = QPushButton("DEV")
        dev.setFixedSize(60, 40)
        dev.setCursor(Qt.CursorShape.PointingHandCursor)
        dev.setToolTip("Developer mode")
        dev.setStyleSheet(f"""
            QPushButton {{
                background: rgba(18,18,18,240);
                color: {C.WHITE};
                border: 1px solid rgba(255,69,69,140);
                border-radius: 12px;
                font: 700 9px 'Segoe UI';
            }}
            QPushButton:hover {{
                background: rgba(34,18,18,245);
                border: 1px solid {C.PRI};
            }}
        """)
        dev.clicked.connect(self.developer_clicked.emit)
        lay.addWidget(dev)

        send = QPushButton()
        send.setFixedSize(40, 40)
        send.setCursor(Qt.CursorShape.PointingHandCursor)
        send.setToolTip("Send")
        send.setIcon(QIcon(_icon_pixmap("send", 18)))
        send.setIconSize(QSize(18, 18))
        send.setStyleSheet(f"""
            QPushButton {{
                background: rgba(24,24,24,240);
                color: {C.WHITE};
                border: 1px solid {C.BORDER_B};
                border-radius: 12px;
            }}
            QPushButton:hover {{
                background: rgba(34,34,34,245);
                border: 1px solid {C.WHITE};
            }}
        """)
        send.clicked.connect(self._submit)
        lay.addWidget(send)

        _attach_pulse_glow(frame, color=C.PRI, blur_min=16.0, blur_max=28.0, alpha=120, period_ms=2800)

        root.addWidget(frame)

    def show_near(self, anchor: QWidget):
        screen = QApplication.primaryScreen().availableGeometry()
        geo = anchor.geometry()
        x = geo.center().x() - (self.width() // 2)
        y = geo.bottom() + 14
        x = max(screen.left() + 12, min(x, screen.right() - self.width() - 12))
        y = max(screen.top() + 12, min(y, screen.bottom() - self.height() - 12))
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()
        self._input.setFocus()
        self._input.selectAll()

    def hideEvent(self, event):
        super().hideEvent(event)

    def _submit(self):
        txt = self._input.text().strip()
        if not txt:
            return
        self._input.clear()
        self.submitted.emit(txt)
        self.hide()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)



class DeveloperModeDialog(QDialog):
    def __init__(self, parent=None, settings: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Developer Mode")
        self.setMinimumWidth(420)
        self.setStyleSheet(f"""
            QDialog {{ background: rgba(8,10,14,235); color: {C.WHITE}; border: 1px solid {C.BORDER_B}; }}
            QLabel {{ color: {C.TEXT}; }}
            QLineEdit {{
                background: rgba(16,16,16,240);
                color: {C.WHITE};
                border: 1px solid {C.BORDER};
                border-radius: 10px;
                padding: 8px 10px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
            QPushButton {{
                background: rgba(18,18,18,240);
                color: {C.WHITE};
                border: 1px solid {C.BORDER_B};
                border-radius: 10px;
                padding: 8px 12px;
            }}
            QPushButton:hover {{ background: rgba(28,28,28,245); border: 1px solid {C.PRI}; }}
            QCheckBox {{ color: {C.TEXT}; }}
        """)

        self._settings = dict(settings or {})
        self._enabled = bool(self._settings.get("developer_mode_enabled", False))
        fallback_workspace = str(Path(__file__).resolve().parent)
        self._workspace = str(self._settings.get("developer_mode_workspace", "") or fallback_workspace)

        root = QVBoxLayout(self)
        root.setSpacing(12)
        root.setContentsMargins(16, 16, 16, 16)

        title = QLabel("Developer Co-pilot")
        title.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI};")
        root.addWidget(title)

        desc = QLabel("Pick a workspace folder REX should use when building websites or other workspace-based tasks.")
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {C.TEXT_DIM};")
        root.addWidget(desc)

        folder_row = QHBoxLayout()
        folder_row.setSpacing(8)
        self._workspace_edit = QLineEdit(self._workspace)
        self._workspace_edit.setPlaceholderText("Select a folder...")
        self._workspace_edit.setReadOnly(True)
        folder_row.addWidget(self._workspace_edit, stretch=1)

        browse = QPushButton("Browse")
        browse.clicked.connect(self._browse_folder)
        folder_row.addWidget(browse)
        root.addLayout(folder_row)

        self._enabled_box = QCheckBox("Turn developer mode on")
        self._enabled_box.setChecked(self._enabled)
        root.addWidget(self._enabled_box)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.clicked.connect(self._save_and_close)
        btn_row.addWidget(cancel)
        btn_row.addWidget(save)
        root.addLayout(btn_row)

    def _browse_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Select developer workspace", self._workspace or str(BASE_DIR))
        if path:
            self._workspace_edit.setText(path)

    def _save_and_close(self):
        self._settings["developer_mode_enabled"] = bool(self._enabled_box.isChecked())
        self._settings["developer_mode_workspace"] = self._workspace_edit.text().strip()
        self.accept()

    def get_settings(self) -> dict:
        return dict(self._settings)



class ScanningOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._phase = 0.0
        self._text = "SCANNING SCREEN"
        self._sub = "Analyzing display..."

        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._tick)
        self._tmr.start(16)

        # splash + boot UI state
        self._mode = "splash"  # splash -> boot
        self._steps: list[tuple[str, QLabel]] = []
        self._progress_val = 0
        self._progress_tip = ""

        self._center = QFrame(self)
        self._center.setStyleSheet("background: transparent; border: none;")
        self._center_lay = QVBoxLayout(self._center)
        self._center_lay.setContentsMargins(36, 36, 36, 36)
        self._center_lay.setSpacing(18)

        # Splash widgets
        self._splash_logo = QLabel()
        self._splash_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._splash_logo.setPixmap(_logo_pixmap(160))
        self._splash_title = QLabel("REX")
        self._splash_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._splash_title.setFont(QFont("Segoe UI", 18, QFont.Weight.Black))
        self._splash_title.setStyleSheet("color: #ffffff; letter-spacing: 2px;")
        self._splash_sub = QLabel("Your AI Command Center")
        self._splash_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._splash_sub.setStyleSheet(f"color: {C.TEXT_DIM};")
        self._splash_slogan = QLabel("Think. Command. Accomplish.")
        self._splash_slogan.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._splash_slogan.setStyleSheet(f"color: {C.PRI}; font-weight: 700;")
        self._splash_status = QLabel("Initializing REX...")
        self._splash_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._splash_status.setStyleSheet(f"color: {C.TEXT_MED};")

        # Boot widgets
        self._boot_title = QLabel("REX")
        self._boot_title.setFont(QFont("Segoe UI", 20, QFont.Weight.Black))
        self._boot_title.setStyleSheet(f"color: {C.WHITE};")
        self._boot_sub = QLabel("System Boot Sequence")
        self._boot_sub.setStyleSheet(f"color: {C.TEXT_DIM};")

        self._checklist_frame = QFrame()
        self._checklist_frame.setStyleSheet("background: transparent; border: none;")
        self._checklist_lay = QVBoxLayout(self._checklist_frame)
        self._checklist_lay.setSpacing(8)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setFixedHeight(14)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setStyleSheet(
            "QProgressBar { background: rgba(255,255,255,0.04); border-radius: 8px; }"
            "QProgressBar::chunk { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 rgba(255,69,69,220), stop:1 rgba(255,120,120,220)); border-radius: 8px; }"
        )

        self._progress_tip_lbl = QLabel("")
        self._progress_tip_lbl.setStyleSheet(f"color: {C.TEXT_DIM};")

        # Layout initial splash
        self._center_lay.addWidget(self._splash_logo)
        self._center_lay.addWidget(self._splash_title)
        self._center_lay.addWidget(self._splash_sub)
        self._center_lay.addWidget(self._splash_slogan)
        self._center_lay.addWidget(self._splash_status)

        self._center.setFixedWidth(820)
        self._center.adjustSize()

        # auto transition from splash to boot
        QTimer.singleShot(1700, self._enter_boot_mode)

    def set_message(self, text: str, sub: str | None = None):
        self._text = (text or "SCANNING SCREEN").upper()
        if sub is not None:
            self._sub = sub
        self.update()

    def show_fullscreen(self, text: str = "SCANNING SCREEN", sub: str = "Analyzing display..."):
        self.set_message(text, sub)
        screen = QApplication.primaryScreen()
        geo = screen.geometry() if screen else QRectF(0, 0, 1280, 720).toRect()
        self.setGeometry(geo)
        self.show()
        self.raise_()

    def hide_overlay(self):
        self.hide()

    def _tick(self):
        self._phase = (self._phase + 0.012) % 1.0
        if self.isVisible():
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect()
        p.fillRect(rect, QColor(0, 0, 0, 185))

        # subtle grid
        grid_pen = QPen(qcol(C.WHITE, 12), 1)
        p.setPen(grid_pen)
        step = 64
        for x in range(0, rect.width(), step):
            p.drawLine(x, 0, x, rect.height())
        for y in range(0, rect.height(), step):
            p.drawLine(0, y, rect.width(), y)

        # blue-white scan beam
        y = int(rect.height() * self._phase)
        beam = QLinearGradient(0, y - 140, 0, y + 140)
        beam.setColorAt(0.0, QColor(255, 69, 69, 0))
        beam.setColorAt(0.48, QColor(255, 69, 69, 90))
        beam.setColorAt(0.50, QColor(255, 255, 255, 180))
        beam.setColorAt(0.52, QColor(255, 69, 69, 90))
        beam.setColorAt(1.0, QColor(255, 69, 69, 0))
        p.fillRect(QRectF(0, y - 140, rect.width(), 280), beam)

        # corner brackets
        p.setPen(QPen(QColor(255, 255, 255, 220), 2))
        br = 28
        for x, y0, dx, dy in [
            (20, 20, 1, 1),
            (rect.width() - 20, 20, -1, 1),
            (20, rect.height() - 20, 1, -1),
            (rect.width() - 20, rect.height() - 20, -1, -1),
        ]:
            p.drawLine(QPointF(x, y0), QPointF(x + dx * br, y0))
            p.drawLine(QPointF(x, y0), QPointF(x, y0 + dy * br))

        # center orb glow
        cx, cy = rect.width() / 2, rect.height() / 2
        for i in range(6):
            r = 110 + i * 22
            alpha = 28 - i * 3
            p.setPen(QPen(QColor(255, 69, 69, max(0, alpha)), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

        # text
        title_font = QFont("Segoe UI", 20, QFont.Weight.Bold)
        sub_font = QFont("Segoe UI", 10)
        p.setPen(QColor(255, 255, 255, 235))
        p.setFont(title_font)
        p.drawText(QRectF(0, cy - 26, rect.width(), 40), Qt.AlignmentFlag.AlignCenter, self._text)
        p.setFont(sub_font)
        p.setPen(QColor(255, 120, 120, 210))
        p.drawText(QRectF(0, cy + 18, rect.width(), 28), Qt.AlignmentFlag.AlignCenter, self._sub)



class BootSequenceOverlay(QWidget):
    finished = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setWindowOpacity(0.0)

        self._device_name = "DEVICE"
        self._greeting_name = "chuckee"
        self._phase = 0
        self._phase_text = ""
        self._sub_text = ""
        self._scan_lines = [
            "CPU READY",
            "MEMORY READY",
            "NETWORK ONLINE",
            "AI CORE ONLINE",
        ]
        self._scan_active = False
        self._zoom = 1.0
        self._rotation = 0.0
        self._beam = 0.0
        self._particles: list[dict[str, float]] = []
        self._running = False
        self._skip_requested = False
        self._fade_in_anim: QPropertyAnimation | None = None
        self._fade_out_anim: QPropertyAnimation | None = None
        self._zoom_anim: QPropertyAnimation | None = None
        self._phase_timers: list[QTimer] = []

        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._tick)
        self._tmr.start(16)

    def zoom(self) -> float:
        return self._zoom

    def setZoom(self, value: float):
        self._zoom = max(0.08, float(value))
        self.update()

    zoom = pyqtProperty(float, fget=zoom, fset=setZoom)

    def _spawn_particles(self, count: int = 64):
        rect = self.rect()
        if rect.width() <= 0 or rect.height() <= 0:
            return
        cx, cy = rect.width() / 2, rect.height() / 2
        self._particles = []
        for _ in range(count):
            ang = random.uniform(0, math.tau)
            radius = random.uniform(56, 220)
            speed = random.uniform(0.3, 1.2)
            self._particles.append({
                "x": cx + math.cos(ang) * radius,
                "y": cy + math.sin(ang) * radius,
                "dx": math.cos(ang + random.uniform(-0.4, 0.4)) * speed,
                "dy": math.sin(ang + random.uniform(-0.4, 0.4)) * speed,
                "a": random.uniform(90, 220),
                "s": random.uniform(1.2, 2.2),
            })

    def start(self, device_name: str, greeting_name: str = "chuckee"):
        self._device_name = (device_name or "DEVICE").strip().upper()
        self._greeting_name = (greeting_name or "chuckee").strip() or "chuckee"
        self._phase = 0
        self._phase_text = "WELCOME"
        self._sub_text = f"WELCOME, {self._device_name}"
        self._scan_active = False
        self._running = True
        self._skip_requested = False
        self._zoom = 1.0
        self._rotation = 0.0
        self._beam = 0.0
        self._spawn_particles()

        screen = QApplication.primaryScreen()
        geo = screen.geometry() if screen else QRectF(0, 0, 1280, 720).toRect()
        self.setGeometry(geo)
        # position the center frame in the middle of the overlay
        try:
            self._center.setParent(self)
            ch = self._center.sizeHint().height() or self._center.height()
            cw = self._center.width() or self._center.sizeHint().width()
            x = max(0, int((geo.width() - cw) / 2))
            y = max(0, int((geo.height() - ch) / 2) - 40)
            self._center.move(x, y)
            self._center.show()
        except Exception as _e:
            log_error(_e, context="ui", severity="debug")
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

        self.setWindowOpacity(0.0)
        self._fade_in_anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade_in_anim.setDuration(260)
        self._fade_in_anim.setStartValue(0.0)
        self._fade_in_anim.setEndValue(1.0)
        self._fade_in_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade_in_anim.start()

        self._phase_timers.clear()
        self._schedule(1200, self._phase_initializing)
        self._schedule(2000, self._phase_loading)
        self._schedule(2700, self._phase_system_ready)
        self._schedule(3500, self._phase_scan)
        self._schedule(4300, self._phase_greeting)
        self._schedule(4850, self._finish_sequence)

    def _schedule(self, ms: int, fn):
        t = QTimer(self)
        t.setSingleShot(True)
        t.timeout.connect(fn)
        t.start(ms)
        self._phase_timers.append(t)

    def _set_phase(self, phase: int, title: str, sub: str = ""):
        self._phase = phase
        self._phase_text = title
        self._sub_text = sub
        self.update()

    def _phase_initializing(self):
        if self._skip_requested:
            return
        self._set_phase(1, "REX INITIALIZING...", "REX Core waking up.")

    def _phase_loading(self):
        if self._skip_requested:
            return
        self._set_phase(1, "LOADING MODULES...", "Preparing voice, memory, and vision.")

    def _phase_system_ready(self):
        if self._skip_requested:
            return
        self._set_phase(2, "SYSTEM READY", "CPU READY  -  MEMORY READY  -  NETWORK ONLINE  -  AI CORE ONLINE")

    def _phase_scan(self):
        if self._skip_requested:
            return
        self._phase = 2
        self._scan_active = True
        self._sub_text = "CPU READY  -  MEMORY READY  -  NETWORK ONLINE  -  AI CORE ONLINE"
        self.update()

    def _phase_greeting(self):
        if self._skip_requested:
            return
        hour = time.localtime().tm_hour
        if 5 <= hour < 12:
            greet = "Good Morning"
        elif 12 <= hour < 18:
            greet = "Good Afternoon"
        else:
            greet = "Good Evening"
        self._set_phase(3, f"{greet}, {self._greeting_name}", "REX is ready.")

    def _finish_sequence(self):
        if self._skip_requested:
            return
        self._running = False
        self._zoom_anim = QPropertyAnimation(self, b"zoom", self)
        self._zoom_anim.setDuration(380)
        self._zoom_anim.setStartValue(self._zoom)
        self._zoom_anim.setEndValue(0.22)
        self._zoom_anim.setEasingCurve(QEasingCurve.Type.InCubic)
        self._zoom_anim.start()

        self._fade_out_anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade_out_anim.setDuration(380)
        self._fade_out_anim.setStartValue(1.0)
        self._fade_out_anim.setEndValue(0.0)
        self._fade_out_anim.setEasingCurve(QEasingCurve.Type.InCubic)
        self._fade_out_anim.finished.connect(self._done)
        self._fade_out_anim.start()

    def _done(self):
        self.hide()
        self.finished.emit()

    def _skip(self):
        if self._skip_requested:
            return
        self._skip_requested = True
        for t in self._phase_timers:
            try:
                t.stop()
            except Exception as _e:
                log_error(_e, context="ui", severity="debug")
        self._running = False
        self.setWindowOpacity(0.0)
        self.hide()
        self.finished.emit()

    def _tick(self):
        if not self.isVisible():
            return
        self._rotation = (self._rotation + 0.7) % 360.0
        self._beam = (self._beam + 2.6) % max(1, self.height())
        for p in self._particles:
            p["x"] += p["dx"] * self._zoom
            p["y"] += p["dy"] * self._zoom
            p["a"] = max(40.0, min(220.0, p["a"] + random.uniform(-3, 3)))
        if self._scan_active:
            self._beam = (self._beam + 3.4) % max(1, self.height())
        self.update()

    def _enter_boot_mode(self):
        if not self._running:
            return
        if self._mode == "boot":
            return
        self._mode = "boot"
        # remove splash widgets
        for w in (self._splash_logo, self._splash_title, self._splash_sub, self._splash_slogan, self._splash_status):
            try:
                self._center_lay.removeWidget(w)
                w.hide()
            except Exception as _e:
                log_error(_e, context="ui", severity="debug")
        # add boot widgets
        self._center_lay.addWidget(self._boot_title)
        self._center_lay.addWidget(self._boot_sub)
        self._center_lay.addWidget(self._checklist_frame)
        self._center_lay.addWidget(self._progress_bar)
        self._center_lay.addWidget(self._progress_tip_lbl)
        self._center.adjustSize()

    # API to drive startup from backend
    def add_step(self, text: str):
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lbl.setFont(QFont("Segoe UI", 10))
        self._checklist_lay.addWidget(lbl)
        self._steps.append((text, lbl))
        return lbl

    def set_step_status(self, text: str, status: str):
        for t, lbl in self._steps:
            if t == text:
                if status == "done":
                    lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent;")
                    lbl.setText(f"✓ {t}")
                elif status == "in_progress":
                    lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")
                    lbl.setText(f"→ {t}")
                elif status == "failed":
                    lbl.setStyleSheet(f"color: {C.RED}; background: transparent;")
                    lbl.setText(f"✖ {t}")
                else:
                    lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
                    lbl.setText(f"  {t}")
                break

    def set_progress(self, percent: int, tip: str | None = None):
        self._progress_val = max(0, min(100, int(percent)))
        try:
            self._progress_bar.setValue(self._progress_val)
        except Exception as _e:
            log_error(_e, context="ui", severity="debug")
        if tip is not None:
            self._progress_tip = tip
            try:
                self._progress_tip_lbl.setText(tip)
            except Exception as _e:
                log_error(_e, context="ui", severity="debug")
        if self._progress_val >= 100:
            QTimer.singleShot(600, self._finish)

    def _finish(self):
        self._running = False
        self._fade_out_anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade_out_anim.setDuration(380)
        self._fade_out_anim.setStartValue(1.0)
        self._fade_out_anim.setEndValue(0.0)
        self._fade_out_anim.setEasingCurve(QEasingCurve.Type.InCubic)
        self._fade_out_anim.finished.connect(self._done)
        self._fade_out_anim.start()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()

        # subtle grid
        if self._phase >= 2:
            grid_pen = QPen(QColor(255, 255, 255, 10), 1)
            p.setPen(grid_pen)
            step = 72
            for x in range(0, rect.width(), step):
                p.drawLine(x, 0, x, rect.height())
            for y in range(0, rect.height(), step):
                p.drawLine(0, y, rect.width(), y)

        # scan beam
        if self._phase >= 2:
            y = int(self._beam)
            grad = QLinearGradient(0, y - 120, 0, y + 120)
            grad.setColorAt(0.0, QColor(255, 69, 69, 0))
            grad.setColorAt(0.48, QColor(255, 69, 69, 38))
            grad.setColorAt(0.50, QColor(255, 255, 255, 120))
            grad.setColorAt(0.52, QColor(255, 69, 69, 38))
            grad.setColorAt(1.0, QColor(255, 69, 69, 0))
            p.fillRect(QRectF(0, y - 120, rect.width(), 240), grad)

        # welcome / greeting texts
        text_color = QColor(255, 255, 255, 245)
        sub_color = QColor(245, 245, 245, 190)

        if self._phase == 0:
            title_font = QFont("Segoe UI", 70, QFont.Weight.Black)
            title_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2)
            p.setPen(text_color)
            p.setFont(title_font)
            p.drawText(rect.adjusted(0, -60, 0, 0), Qt.AlignmentFlag.AlignCenter, f"WELCOME, {self._device_name}")
        else:
            # reactor core
            cx, cy = rect.width() / 2, rect.height() / 2 - 18
            scale = self._zoom
            outer_r = 170 * scale
            core_r = 72 * scale

            for i in range(8):
                ring_r = outer_r + i * (14 * scale)
                alpha = max(8, 60 - i * 6)
                p.setPen(QPen(QColor(255, 255, 255, alpha), max(1.0, 1.8 * scale)))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QRectF(cx - ring_r, cy - ring_r, ring_r * 2, ring_r * 2))

            # rotating ring accents
            p.save()
            p.translate(cx, cy)
            p.rotate(self._rotation)
            for i in range(20):
                ang = (360 / 20) * i
                p.save()
                p.rotate(ang)
                p.setPen(QPen(QColor(255, 255, 255, 125), max(1.2, 1.5 * scale)))
                p.drawLine(QPointF(0, -outer_r - 4 * scale), QPointF(0, -outer_r + 16 * scale))
                p.restore()
            p.restore()

            # particles
            for pt in self._particles:
                px = pt["x"]
                py = pt["y"]
                if 0 <= px <= rect.width() and 0 <= py <= rect.height():
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(255, 255, 255, int(pt["a"])))
                    p.drawEllipse(QRectF(px, py, pt["s"], pt["s"]))

            # core glow
            glow = QRadialGradient(cx, cy, outer_r * 0.98)
            glow.setColorAt(0.0, QColor(180, 40, 40, 240))
            glow.setColorAt(0.45, QColor(100, 25, 25, 220))
            glow.setColorAt(0.7, QColor(255, 255, 255, 40))
            glow.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(glow)
            p.drawEllipse(QRectF(cx - outer_r * 0.72, cy - outer_r * 0.72, outer_r * 1.44, outer_r * 1.44))

            # inner core
            core_grad = QRadialGradient(cx, cy, core_r * 2.2)
            core_grad.setColorAt(0.0, QColor(255, 69, 69, 200))
            core_grad.setColorAt(0.35, QColor(80, 20, 30, 235))
            core_grad.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.setBrush(core_grad)
            p.drawEllipse(QRectF(cx - core_r * 2.0, cy - core_r * 2.0, core_r * 4.0, core_r * 4.0))

            # central label
            p.setPen(QColor(255, 255, 255, 220))
            p.setFont(QFont("Segoe UI", int(28 * scale), QFont.Weight.Bold))
            p.drawText(QRectF(cx - 160 * scale, cy - 40 * scale, 320 * scale, 80 * scale), Qt.AlignmentFlag.AlignCenter, "REX")

            # phase text
            if self._phase in {1, 2, 3}:
                p.setPen(text_color)
                p.setFont(QFont("Segoe UI", 26 if self._phase != 3 else 30, QFont.Weight.Bold))
                p.drawText(QRectF(0, cy + 168 * scale, rect.width(), 50), Qt.AlignmentFlag.AlignCenter, self._phase_text)
                if self._sub_text:
                    p.setPen(sub_color)
                    p.setFont(QFont("Segoe UI", 12))
                    if self._phase == 2:
                        p.drawText(QRectF(rect.width() * 0.16, cy + 220 * scale, rect.width() * 0.68, 60),
                                   Qt.AlignmentFlag.AlignCenter, self._sub_text)
                    else:
                        p.drawText(QRectF(0, cy + 214 * scale, rect.width(), 40), Qt.AlignmentFlag.AlignCenter, self._sub_text)

            # phase 2 info cards
            if self._phase >= 2:
                info_y = int(cy + 276 * scale)
                card_w = min(200, int(rect.width() * 0.18))
                gap = 16
                total = card_w * 4 + gap * 3
                start_x = int((rect.width() - total) / 2)
                info = [("CPU READY", 0), ("MEMORY READY", 1), ("NETWORK ONLINE", 2), ("AI CORE ONLINE", 3)]
                for i, (txt, _) in enumerate(info):
                    x = start_x + i * (card_w + gap)
                    rr = QRectF(x, info_y, card_w, 46)
                    p.setPen(QPen(QColor(255, 255, 255, 40), 1))
                    p.setBrush(QColor(10, 10, 10, 170))
                    p.drawRoundedRect(rr, 12, 12)
                    p.setPen(QColor(255, 255, 255, 230))
                    p.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
                    p.drawText(rr.adjusted(12, 0, -12, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, txt)

            # greeting phase
            if self._phase == 3:
                greeting_font = QFont("Segoe UI", 32, QFont.Weight.Bold)
                p.setPen(QColor(255, 255, 255, 245))
                p.setFont(greeting_font)
                p.drawText(QRectF(0, cy + 150 * scale, rect.width(), 48), Qt.AlignmentFlag.AlignCenter, self._phase_text)
                p.setPen(QColor(220, 220, 220, 200))
                p.setFont(QFont("Segoe UI", 14))
                p.drawText(QRectF(0, cy + 203 * scale, rect.width(), 36), Qt.AlignmentFlag.AlignCenter, "REX is ready.")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._skip()
            return
        super().keyPressEvent(event)



class IncomingAlertDialog(QDialog):
    decision = pyqtSignal(str)

    def __init__(self, event: dict, parent=None):
        super().__init__(parent)
        self._event = event or {}
        self._kind = (self._event.get("kind") or "message").strip().lower()
        self._app = (self._event.get("app") or "App").strip()
        self._title = (self._event.get("title") or "").strip()
        self._preview = (self._event.get("preview") or "").strip()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setObjectName("IncomingAlertDialog")
        self.setMinimumWidth(360)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        frame = QFrame()
        frame.setObjectName("IncomingAlertFrame")
        frame.setStyleSheet(f"""
            QFrame#IncomingAlertFrame {{
                background: rgba(8, 8, 8, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 16px;
            }}
        """)
        root.addWidget(frame)

        lay = QVBoxLayout(frame)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)

        heading = QLabel("Incoming Call" if self._kind == "call" else "Incoming Message")
        heading.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        heading.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        lay.addWidget(heading)

        app_lbl = QLabel(f"From {self._app}")
        app_lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        app_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        lay.addWidget(app_lbl)

        body = self._preview or self._title or "A notification was detected."
        body_lbl = QLabel(body)
        body_lbl.setWordWrap(True)
        body_lbl.setFont(QFont("Segoe UI", 10))
        body_lbl.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        lay.addWidget(body_lbl)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        def _btn(text: str, *, primary: bool = False, danger: bool = False) -> QPushButton:
            btn = QPushButton(text)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(34)
            btn.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            fg = C.WHITE
            border = C.BORDER_B if primary else C.BORDER
            bg = "rgba(255,255,255,0.10)" if primary else "rgba(14,14,14,235)"
            if danger:
                border = C.RED
                bg = "rgba(60,10,10,235)"
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {bg};
                    color: {fg};
                    border: 1px solid {border};
                    border-radius: 11px;
                    padding: 0 12px;
                }}
                QPushButton:hover {{
                    background: rgba(255,255,255,0.14);
                    border: 1px solid {C.WHITE};
                }}
            """)
            return btn

        if self._kind == "call":
            self._accept_btn = _btn("Pick up", primary=True)
            self._ignore_btn = _btn("Ignore")
            self._cut_btn = _btn("Cut call", danger=True)
            self._x_btn = _btn("X")
            self._accept_btn.clicked.connect(lambda: self._choose("accept"))
            self._ignore_btn.clicked.connect(lambda: self._choose("ignore"))
            self._cut_btn.clicked.connect(lambda: self._choose("cut"))
            self._x_btn.clicked.connect(lambda: self._choose("noop"))
            for btn in (self._accept_btn, self._ignore_btn, self._cut_btn, self._x_btn):
                btn_row.addWidget(btn)
        else:
            self._hear_btn = _btn("Hear it", primary=True)
            self._reply_btn = _btn("Reply")
            self._ignore_btn = _btn("Ignore")
            self._x_btn = _btn("X")
            self._hear_btn.clicked.connect(lambda: self._choose("hear"))
            self._reply_btn.clicked.connect(lambda: self._choose("reply"))
            self._ignore_btn.clicked.connect(lambda: self._choose("ignore"))
            self._x_btn.clicked.connect(lambda: self._choose("noop"))
            btn_row.addWidget(self._hear_btn)
            btn_row.addWidget(self._reply_btn)
            btn_row.addWidget(self._ignore_btn)
            btn_row.addWidget(self._x_btn)

        lay.addLayout(btn_row)

    def _choose(self, decision: str):
        self.decision.emit(decision)
        self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._choose("ignore")
            return
        super().keyPressEvent(event)



class MeetingOverlay(QWidget):
    stop_requested = pyqtSignal()
    minimize_requested = pyqtSignal()
    close_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setMouseTracking(True)
        self._expanded_height = 142
        self._collapsed_height = 58
        self._collapsed = False
        self.setFixedHeight(self._expanded_height)
        self.setStyleSheet("background: transparent;")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                background: rgba(5, 5, 5, 232);
                border: 1px solid {C.BORDER_B};
                border-radius: 18px;
            }}
        """)
        root.addWidget(frame)

        lay = QVBoxLayout(frame)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(10)

        self._badge = QLabel("MEETING MODE")
        self._badge.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self._badge.setStyleSheet(
            f"color: {C.WHITE}; background: rgba(255,255,255,0.06); border: 1px solid {C.BORDER_B}; border-radius: 10px; padding: 4px 10px;"
        )
        top.addWidget(self._badge)

        self._title = QLabel("Watching the meeting")
        self._title.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self._title.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        top.addWidget(self._title)
        top.addStretch()

        self._min_btn = QPushButton("-")
        self._min_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._min_btn.setFixedSize(28, 28)
        self._min_btn.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self._min_btn.setToolTip("Minimize meeting bar")
        self._min_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,255,255,0.06);
                color: {C.WHITE};
                border: 1px solid {C.BORDER_B};
                border-radius: 9px;
            }}
            QPushButton:hover {{
                background: rgba(255,255,255,0.10);
                border: 1px solid {C.WHITE};
            }}
        """)
        self._min_btn.clicked.connect(self._toggle_collapsed)
        top.addWidget(self._min_btn)

        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._stop_btn.setFixedHeight(28)
        self._stop_btn.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self._stop_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,255,255,0.06);
                color: {C.WHITE};
                border: 1px solid {C.BORDER_B};
                border-radius: 9px;
                padding: 0 12px;
            }}
            QPushButton:hover {{
                background: rgba(255,255,255,0.10);
                border: 1px solid {C.WHITE};
            }}
        """)
        self._stop_btn.clicked.connect(self.stop_requested.emit)
        top.addWidget(self._stop_btn)

        self._close_btn = QPushButton("x")
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.setFixedSize(28, 28)
        self._close_btn.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self._close_btn.setToolTip("Close meeting bar")
        self._close_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,255,255,0.06);
                color: {C.WHITE};
                border: 1px solid {C.BORDER_B};
                border-radius: 9px;
            }}
            QPushButton:hover {{
                background: rgba(255,255,255,0.10);
                border: 1px solid {C.WHITE};
            }}
        """)
        self._close_btn.clicked.connect(self.close_requested.emit)
        top.addWidget(self._close_btn)
        lay.addLayout(top)

        self._summary = QLabel("Waiting for a meeting to start...")
        self._summary.setWordWrap(True)
        self._summary.setFont(QFont("Segoe UI", 10))
        self._summary.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        lay.addWidget(self._summary)

        self._speech = QLabel("They said: nothing yet.")
        self._speech.setWordWrap(True)
        self._speech.setFont(QFont("Segoe UI", 10))
        self._speech.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        lay.addWidget(self._speech)

        self._answer = QLabel("REX will show the live answer here.")
        self._answer.setWordWrap(True)
        self._answer.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self._answer.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        lay.addWidget(self._answer)

        self._apply_collapsed_state(False)

    def set_content(self, title: str, summary: str, answer: str, active: bool = True, speech: str = ""):
        self._title.setText(title or "Watching the meeting")
        self._summary.setText(summary or "Watching the meeting screen.")
        self._speech.setText(f"They said: {speech or 'nothing yet.'}")
        self._answer.setText(answer or "No question detected yet.")
        self._badge.setText("MEETING LIVE" if active else "MEETING MODE")

    def _apply_collapsed_state(self, collapsed: bool):
        self._collapsed = bool(collapsed)
        for widget in (self._summary, self._speech, self._answer):
            widget.setVisible(not self._collapsed)
        self._min_btn.setText("?" if self._collapsed else "-")
        self._min_btn.setToolTip("Restore meeting bar" if self._collapsed else "Minimize meeting bar")
        self.setFixedHeight(self._collapsed_height if self._collapsed else self._expanded_height)

    def set_collapsed(self, collapsed: bool):
        self._apply_collapsed_state(collapsed)

    def is_collapsed(self) -> bool:
        return self._collapsed

    def _toggle_collapsed(self):
        self.minimize_requested.emit()



class FloatingLauncher(QWidget):
    single_clicked = pyqtSignal()
    double_clicked = pyqtSignal()
    action_requested = pyqtSignal(str)
    position_changed = pyqtSignal(int, int)

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(74, 74)
        self._state = "idle"
        self._status_line = "Ready"
        self._hovered = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._ring = QFrame()
        self._ring.setStyleSheet("")
        lay = QVBoxLayout(self._ring)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(0)

        lay.addWidget(_framed_logo(54, 36, bg="rgba(255,255,255,0.04)", border=C.BORDER, radius=26, inset=6))
        root.addWidget(self._ring)
        _attach_pulse_glow(self._ring, color=C.WHITE, blur_min=18.0, blur_max=34.0, alpha=135, period_ms=2300)

        self._single_timer = QTimer(self)
        self._single_timer.setSingleShot(True)
        self._single_timer.timeout.connect(self.single_clicked.emit)
        self._dragging = False
        self._drag_button = None
        self._drag_offset = QPoint(0, 0)
        self._press_pos = QPoint(0, 0)
        self._apply_state_style()

    def show_at(self, x: int | None = None, y: int | None = None):
        if x is None or y is None:
            screen = QApplication.primaryScreen().availableGeometry()
            x = screen.right() - self.width() - 18
            y = screen.bottom() - self.height() - 90
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()

    def set_state(self, state: str, detail: str | None = None):
        self._state = (state or "idle").strip().lower()
        self._status_line = (detail or self._default_status()).strip() or self._default_status()
        self._apply_state_style()

    def _default_status(self) -> str:
        return {
            "idle": "Ready",
            "listening": "Listening",
            "thinking": "Thinking...",
            "executing": "Executing task...",
            "error": "Error",
        }.get(self._state, "Ready")

    def _apply_state_style(self):
        state = self._state
        accent = {
            "idle": "#00BFFF",
            "listening": "#4ef0ff",
            "thinking": "#9fd8ff",
            "executing": "#ffb14a",
            "error": "#ff6b6b",
        }.get(state, "#00BFFF")
        glow = {
            "idle": "rgba(0,191,255,0.18)",
            "listening": "rgba(78,240,255,0.26)",
            "thinking": "rgba(159,216,255,0.26)",
            "executing": "rgba(255,177,74,0.22)",
            "error": "rgba(255,107,107,0.24)",
        }.get(state, "rgba(0,191,255,0.18)")
        self._ring.setStyleSheet(f"""
            QFrame {{
                background: rgba(4, 4, 8, 234);
                border: 1px solid {accent};
                border-radius: 37px;
            }}
            QFrame:hover {{
                border: 1px solid {accent};
            }}
        """)
        self.setToolTip(f"REX\n{self._status_line}")

    def _show_menu(self, global_pos):
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background: rgba(8, 8, 8, 245);
                color: {C.WHITE};
                border: 1px solid {C.BORDER_B};
                border-radius: 10px;
                padding: 6px;
            }}
            QMenu::item {{
                padding: 8px 18px;
                border-radius: 6px;
            }}
            QMenu::item:selected {{
                background: rgba(255,255,255,0.08);
            }}
        """)

        actions = [
            ("New Task", "new_task"),
            ("Voice Mode", "voice_mode"),
            ("Screen Analyzer", "screen_analyzer"),
            ("Browser Agent", "browser_agent"),
            ("Settings", "settings"),
            ("Open Workspace", "open_workspace"),
        ]
        for idx, (label, key) in enumerate(actions):
            action = QAction(label, self)
            action.triggered.connect(lambda _=False, k=key: self.action_requested.emit(k))
            menu.addAction(action)
            if idx != len(actions) - 1:
                menu.addSeparator()
        menu.exec(global_pos)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self._dragging:
            self._single_timer.start(180)
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self.position_changed.emit(self.x(), self.y())
        if event.button() == Qt.MouseButton.RightButton:
            if not self._dragging:
                self._show_menu(event.globalPosition().toPoint())
            self._dragging = False
            self._drag_button = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._single_timer.stop()
            self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self._press_pos = event.globalPosition().toPoint()
            self._drag_offset = self._press_pos - self.frameGeometry().topLeft()
            self._single_timer.stop()
            self._drag_button = Qt.MouseButton.LeftButton
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            self._dragging = False
            self._press_pos = event.globalPosition().toPoint()
            self._drag_offset = self._press_pos - self.frameGeometry().topLeft()
            self._single_timer.stop()
            self._drag_button = Qt.MouseButton.RightButton
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            pos = event.globalPosition().toPoint()
            if not self._dragging and (pos - self._press_pos).manhattanLength() > 6:
                self._dragging = True
            if self._dragging:
                screen = QApplication.primaryScreen().availableGeometry()
                new_pos = pos - self._drag_offset
                new_x = max(screen.left(), min(new_pos.x(), screen.right() - self.width()))
                new_y = max(screen.top(), min(new_pos.y(), screen.bottom() - self.height()))
                self.move(new_x, new_y)
                self.position_changed.emit(new_x, new_y)
            event.accept()
            return
        if event.buttons() & Qt.MouseButton.RightButton and self._drag_button == Qt.MouseButton.RightButton:
            pos = event.globalPosition().toPoint()
            if not self._dragging and (pos - self._press_pos).manhattanLength() > 6:
                self._dragging = True
            if self._dragging:
                screen = QApplication.primaryScreen().availableGeometry()
                new_pos = pos - self._drag_offset
                new_x = max(screen.left(), min(new_pos.x(), screen.right() - self.width()))
                new_y = max(screen.top(), min(new_pos.y(), screen.bottom() - self.height()))
                self.move(new_x, new_y)
                self.position_changed.emit(new_x, new_y)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def enterEvent(self, event):
        self._hovered = True
        self._apply_state_style()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._apply_state_style()
        super().leaveEvent(event)

