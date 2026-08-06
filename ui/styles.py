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

import psutil
from core.error_handler import log_error

from PyQt6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint, QPointF, QRect, QRectF, QSize,
    QParallelAnimationGroup, QSequentialAnimationGroup, QUrl, QThread,
    pyqtSignal, pyqtSlot, QMetaObject, Q_ARG, QByteArray)
from PyQt6.QtGui import (
    QAction,
    QColor, QFont, QFontMetrics, QIcon, QImage, QLinearGradient,
    QPainter, QPainterPath, QPalette, QPen, QPixmap, QPolygon,
    QRadialGradient, QRegion, QBrush, QTransform, QFontDatabase)
from PyQt6.QtWidgets import (
    QApplication, QFrame, QGraphicsDropShadowEffect, QGraphicsOpacityEffect,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget, QDialog, QPushButton, QFileDialog, QSlider,
    QStackedWidget, QTextEdit, QToolButton, QMessageBox, QProgressBar,
    QSpinBox, QComboBox, QCheckBox, QTabWidget, QMenu, QSystemTrayIcon, QListWidget, QListWidgetItem, QGridLayout,
    QFormLayout, QGroupBox, QSplitter, QPlainTextEdit)


# ── Base directory ────────────────────────────────────────────────────────────

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = _base_dir()
ASSETS   = BASE_DIR / "assets"
LOGO_ICO = ASSETS / "REX_Logo.ico"
LOGO_FILE = ASSETS / "REX_Logo.png"


# ── Color / Theme Constants ──────────────────────────────────────────────────

class C:
    BG        = "#020305"
    PANEL     = "#07080b"
    PANEL2    = "#0d0f14"
    BORDER    = "#22252d"
    BORDER_B  = "#41454f"
    BORDER_A  = "#2b2e36"
    PRI       = "#ff4545"
    PRI_DIM   = "#ff7777"
    PRI_GHO   = "#2a0b0d"
    ACC       = "#ff4545"
    ACC2      = "#f8fbff"
    GREEN     = "#37ff5f"
    GREEN_D   = "#1dcc43"
    RED       = "#ff4545"
    MUTED_C   = "#ff4545"
    TEXT      = "#f4f6f8"
    TEXT_DIM  = "#8e949d"
    TEXT_MED  = "#c5cad2"
    WHITE     = "#ffffff"
    DARK      = "#000000"
    BAR_BG    = "#222222"




# ── Color Helpers ────────────────────────────────────────────────────────────

def qcol(h: str, a: int = 255) -> QColor:
    c = QColor(h); c.setAlpha(a); return c


def _logo_icon() -> QIcon:
    return QIcon(str(LOGO_ICO if LOGO_ICO.exists() else LOGO_FILE))


def _logo_pixmap(size: int) -> QPixmap:
    pix = QPixmap(str(LOGO_FILE))
    if pix.isNull():
        return QPixmap(size, size)
    return pix.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)


def _framed_logo(size: int, icon_size: int | None = None, *, bg: str = "rgba(18,18,18,240)",
                 border: str = None, radius: int | None = None, inset: int = 6) -> QFrame:
    border = border or C.BORDER_B
    radius = radius if radius is not None else max(10, size // 4)
    icon_size = icon_size or max(8, size - inset * 2)
    frame = QFrame()
    frame.setFixedSize(size, size)
    frame.setStyleSheet(
        f"background: {bg}; border: 1px solid {border}; border-radius: {radius}px;"
    )
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(inset, inset, inset, inset)
    lay.setSpacing(0)
    lbl = QLabel()
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setPixmap(_logo_pixmap(icon_size))
    lbl.setStyleSheet("background: transparent; border: none;")
    lay.addWidget(lbl)
    return frame


def _icon_pixmap(kind: str, size: int = 18) -> QPixmap:
    px = QPixmap(size, size)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(qcol(C.WHITE), max(2.2, size * 0.14), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)

    if kind == "attach":
        # More readable paperclip shape
        p.drawArc(QRectF(size*0.22, size*0.14, size*0.42, size*0.58), 35*16, 290*16)
        p.drawArc(QRectF(size*0.42, size*0.24, size*0.28, size*0.44), 35*16, 290*16)
        p.drawLine(QPointF(size*0.28, size*0.56), QPointF(size*0.38, size*0.66))
    elif kind == "mic":
        # Clearer microphone silhouette
        p.drawRoundedRect(QRectF(size*0.31, size*0.14, size*0.38, size*0.48), size*0.16, size*0.16)
        p.drawLine(QPointF(size*0.50, size*0.62), QPointF(size*0.50, size*0.83))
        p.drawLine(QPointF(size*0.36, size*0.83), QPointF(size*0.64, size*0.83))
        p.drawLine(QPointF(size*0.42, size*0.70), QPointF(size*0.58, size*0.70))
    elif kind == "send":
        p.drawLine(QPointF(size*0.20, size*0.50), QPointF(size*0.70, size*0.50))
        p.drawLine(QPointF(size*0.48, size*0.30), QPointF(size*0.70, size*0.50))
        p.drawLine(QPointF(size*0.48, size*0.70), QPointF(size*0.70, size*0.50))

    p.end()
    return px


def _attach_pulse_glow(widget: QWidget, *, color: str = C.WHITE, blur_min: float = 12.0,
                       blur_max: float = 28.0, alpha: int = 180, period_ms: int = 2400) -> None:
    # Intentionally disabled for performance. Kept as a no-op so existing calls
    # do not need to change across the UI.
    return




# ── Formatting Helpers ───────────────────────────────────────────────────────

def _fmt_time_stamp(value: int | float | None = None) -> str:
    try:
        from datetime import datetime
        if value is None:
            dt = datetime.now()
        else:
            stamp = float(value)
            if stamp > 10_000_000_000:
                stamp /= 1000.0
            dt = datetime.fromtimestamp(stamp)
        return dt.strftime("%H:%M")
    except Exception:
        return ""


def _markdown_to_html(text: str, role: str = "assistant") -> str:
    safe = html_lib.escape(text or "")
    safe = safe.replace("\r\n", "\n").replace("\r", "\n")

    def _code_block(match):
        code = html_lib.escape(match.group(1).rstrip("\n"))
        return (
            '<pre style="margin:10px 0; padding:10px 12px; border-radius:10px; '
            'background:rgba(0,0,0,0.35); color:#f4f6f8; border:1px solid rgba(255,255,255,0.10);">'
            f'<code>{code}</code></pre>'
        )

    safe = re.sub(r"```(?:[\w+-]+\n)?(.*?)```", _code_block, safe, flags=re.S)
    safe = re.sub(
        r"`([^`]+)`",
        r'<code style="padding:1px 5px; border-radius:5px; background:rgba(255,255,255,0.08); color:#fff;">\1</code>',
        safe)
    safe = re.sub(r"(?m)^### (.+)$", r'<h3 style="margin:10px 0 6px 0; font-size:13px;">\1</h3>', safe)
    safe = re.sub(r"(?m)^## (.+)$", r'<h2 style="margin:10px 0 8px 0; font-size:15px;">\1</h2>', safe)
    safe = re.sub(r"(?m)^# (.+)$", r'<h1 style="margin:10px 0 8px 0; font-size:17px;">\1</h1>', safe)
    safe = safe.replace("\n", "<br>")
    accent = C.WHITE if role == "assistant" else C.TEXT
    return (
        f'<div style="color:{accent}; font-size:12px; line-height:1.45; white-space:normal;">'
        f"{safe}"
        "</div>"
    )



# ── File Helpers ─────────────────────────────────────────────────────────────

def _file_category(path: Path) -> str:
    # Lazy import to avoid circular import (dict lives in widgets.py).
    try:
        from .widgets import _EXT_TO_CAT
    except Exception:
        return "unknown"
    return _EXT_TO_CAT.get(path.suffix.lower().lstrip("."), "unknown")

def _fmt_size(size: int) -> str:
    if   size < 1024:    return f"{size} B"
    elif size < 1024**2: return f"{size/1024:.1f} KB"
    elif size < 1024**3: return f"{size/1024**2:.1f} MB"
    else:                return f"{size/1024**3:.1f} GB"

