"""
Extract ui.py into ui/ package with multiple modules.

This script reads the original ui.py and splits it into:
- ui/styles.py: C class, colors, logo helpers, formatting functions
- ui/windows_integration.py: startup, registry, system helpers
- ui/gesture_canvas.py: _GestureRenderCanvas
- ui/widgets.py: all widget classes
- ui/main_window.py: MainWindow, SystemConnectivity, REXUI
- ui/__init__.py: re-exports for backward compatibility
"""

import re
from pathlib import Path

UI_PY = Path("ui.py")
LEGACY_PY = Path("ui_legacy.py")

# Read the original file
lines = UI_PY.read_text(encoding="utf-8").split("\n")
total = len(lines)
print(f"Read ui.py: {total} lines")


def extract_lines(start, end):
    """Extract lines from start to end (1-indexed, inclusive)."""
    return "\n".join(lines[start-1:end])


def get_imports():
    """Get the imports section from ui.py."""
    return extract_lines(1, 43)


# ── styles.py ────────────────────────────────────────────────────────────────
# Contains: C class, qcol, logo helpers, formatting, pulse glow
# Lines: 1-43 (imports), 46-92 (C class), 356-427 (qcol, logos, pulse glow),
#         1910-1953 (_fmt_time_stamp, _markdown_to_html), 3872-3880 (_file_category, _fmt_size)

styles_imports = '''from __future__ import annotations

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
    Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint, QRect, QSize,
    QParallelAnimationGroup, QSequentialAnimationGroup, QUrl, QThread,
    pyqtSignal, pyqtSlot, QMetaObject, Q_ARG, QByteArray,
)
from PyQt6.QtGui import (
    QColor, QFont, QFontMetrics, QIcon, QImage, QLinearGradient,
    QPainter, QPainterPath, QPalette, QPen, QPixmap, QPolygon,
    QRadialGradient, QRegion, QBrush, QTransform, QFontDatabase,
)
from PyQt6.QtWidgets import (
    QApplication, QFrame, QGraphicsDropShadowEffect, QGraphicsOpacityEffect,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget, QDialog, QPushButton, QFileDialog, QSlider,
    QStackedWidget, QTextEdit, QToolButton, QMessageBox, QProgressBar,
    QSpinBox, QComboBox, QCheckBox, QTabWidget, QMenu, QAction,
    QSystemTrayIcon, QListWidget, QListWidgetItem, QGridLayout,
    QFormLayout, QGroupBox, QSplitter, QPlainTextEdit,
)
'''

styles_content = f'''{styles_imports}

# ── Base directory ────────────────────────────────────────────────────────────

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


# ── Color / Theme Constants ──────────────────────────────────────────────────

{extract_lines(69, 92)}


# ── Color Helpers ────────────────────────────────────────────────────────────

{extract_lines(356, 427)}


# ── Formatting Helpers ───────────────────────────────────────────────────────

{extract_lines(1910, 1953)}


# ── File Helpers ─────────────────────────────────────────────────────────────

{extract_lines(3872, 3880)}
'''


# ── windows_integration.py ──────────────────────────────────────────────────
# Contains: _quiet_run, startup helpers, system detection

windows_imports = '''from __future__ import annotations

import os
import platform
import subprocess
import sys
import time
from pathlib import Path

from core.error_handler import log_error

from .styles import C, _base_dir
'''

windows_content = f'''{windows_imports}

# ── System Helpers ───────────────────────────────────────────────────────────

{extract_lines(428, 502)}


# ── Camera Detection ─────────────────────────────────────────────────────────

{extract_lines(676, 718)}


# ── Network Label ────────────────────────────────────────────────────────────

{extract_lines(1310, 1322)}
'''


# ── gesture_canvas.py ───────────────────────────────────────────────────────
# Contains: _GestureRenderCanvas

gesture_imports = '''from __future__ import annotations

import math
import random
import time

from PyQt6.QtCore import Qt, QTimer, QPoint, QRect, QSize, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPixmap, QBrush
from PyQt6.QtWidgets import QWidget, QSizePolicy

from .styles import C
'''

gesture_content = f'''{gesture_imports}

# ── Gesture Rendering Canvas ────────────────────────────────────────────────

{extract_lines(719, 824)}
'''


# ── widgets.py ──────────────────────────────────────────────────────────────
# Contains: All widget/UI component classes

widgets_imports = '''from __future__ import annotations

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
    Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint, QRect, QSize,
    QParallelAnimationGroup, QSequentialAnimationGroup, QUrl, QThread,
    pyqtSignal, pyqtSlot, QMetaObject, Q_ARG, QByteArray,
)
from PyQt6.QtGui import (
    QColor, QFont, QFontMetrics, QIcon, QImage, QLinearGradient,
    QPainter, QPainterPath, QPalette, QPen, QPixmap, QPolygon,
    QRadialGradient, QRegion, QBrush, QTransform, QFontDatabase,
)
from PyQt6.QtWidgets import (
    QApplication, QFrame, QGraphicsDropShadowEffect, QGraphicsOpacityEffect,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget, QDialog, QPushButton, QFileDialog, QSlider,
    QStackedWidget, QTextEdit, QToolButton, QMessageBox, QProgressBar,
    QSpinBox, QComboBox, QCheckBox, QTabWidget, QMenu, QAction,
    QSystemTrayIcon, QListWidget, QListWidgetItem, QGridLayout,
    QFormLayout, QGroupBox, QSplitter, QPlainTextEdit,
)

from discord_bot import DiscordBotService
from gesture_utils import estimate_gesture_state
from smart_home import SmartHomeService
from smart_home_page_new import REXHomePage, _DeviceTile
from memory_panel import MemoryPanel
from workspace_store import store as workspace_store

from .styles import (
    C, _base_dir, qcol, _logo_icon, _logo_pixmap, _framed_logo,
    _icon_pixmap, _attach_pulse_glow, _fmt_time_stamp, _markdown_to_html,
    _file_category, _fmt_size,
)
from .gesture_canvas import _GestureRenderCanvas
from .windows_integration import (
    _quiet_run, _quote_cmd_arg, _hidden_launch_args, _startup_run_value,
    _startup_registry_key, _current_boot_stamp, _launched_from_windows_startup,
    _default_app_settings, _default_discord_settings, _camera_available,
    _active_net_label,
)
'''

# Widget class line ranges (from the grep output)
widget_ranges = [
    (93, 132),    # BackgroundWidget
    (133, 355),   # RemoteKeyOverlay
    (503, 675),   # _SysMetrics
    (825, 1309),  # GestureCameraPreview
    (1323, 1596), # HudCanvas
    (1597, 1650), # MetricBar
    (1651, 1734), # MessageCard
    (1735, 1909), # TaskCard
    (1955, 1985), # AttachmentCard
    (1986, 2038), # EventCard
    (2039, 2127), # ArtifactCard
    (2128, 2287), # ChatBubble
    (2288, 2346), # HistoryConversationItem
    (2347, 2541), # ConversationFeed
    (2542, 2642), # TaskDock
    (2643, 3218), # WorkspaceSidebar
    (3219, 3530), # InlineChatWorkspace
    (3531, 3688), # LauncherControlPanel
    (3689, 3722), # SmallPanelCard
    (3723, 3780), # StatCard
    (3781, 3871), # LogWidget
    (3882, 3960), # FileDropZone
    (3961, 4063), # _DropCanvas
    (4064, 4232), # SetupOverlay
    (4233, 4408), # CommandBar
    (4409, 4494), # DeveloperModeDialog
    (4495, 4661), # ScanningOverlay
    (4662, 5094), # BootSequenceOverlay
    (5095, 5218), # IncomingAlertDialog
    (5219, 5380), # MeetingOverlay
    (5381, 5587), # FloatingLauncher
]

widget_sections = []
for start, end in widget_ranges:
    widget_sections.append(extract_lines(start, end))

widgets_content = widgets_imports + "\n\n" + "\n\n".join(widget_sections)


# ── main_window.py ──────────────────────────────────────────────────────────
# Contains: MainWindow, SystemConnectivity, REXUI, _RootShim

main_window_imports = '''from __future__ import annotations

import asyncio
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
    Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint, QRect, QSize,
    QParallelAnimationGroup, QSequentialAnimationGroup, QUrl, QThread,
    pyqtSignal, pyqtSlot, QMetaObject, Q_ARG, QByteArray,
)
from PyQt6.QtGui import (
    QColor, QFont, QFontMetrics, QIcon, QImage, QLinearGradient,
    QPainter, QPainterPath, QPalette, QPen, QPixmap, QPolygon,
    QRadialGradient, QRegion, QBrush, QTransform, QFontDatabase,
)
from PyQt6.QtWidgets import (
    QApplication, QFrame, QGraphicsDropShadowEffect, QGraphicsOpacityEffect,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget, QDialog, QPushButton, QFileDialog, QSlider,
    QStackedWidget, QTextEdit, QToolButton, QMessageBox, QProgressBar,
    QSpinBox, QComboBox, QCheckBox, QTabWidget, QMenu, QAction,
    QSystemTrayIcon, QListWidget, QListWidgetItem, QGridLayout,
    QFormLayout, QGroupBox, QSplitter, QPlainTextEdit,
)

from discord_bot import DiscordBotService
from gesture_utils import estimate_gesture_state
from smart_home import SmartHomeService
from smart_home_page_new import REXHomePage, _DeviceTile
from memory_panel import MemoryPanel
from workspace_store import store as workspace_store

from .styles import (
    C, _base_dir, qcol, _logo_icon, _logo_pixmap, _framed_logo,
    _icon_pixmap, _attach_pulse_glow, _fmt_time_stamp, _markdown_to_html,
    _file_category, _fmt_size,
)
from .widgets import (
    BackgroundWidget, RemoteKeyOverlay, _SysMetrics, HudCanvas,
    MetricBar, MessageCard, TaskCard, AttachmentCard, EventCard,
    ArtifactCard, ChatBubble, HistoryConversationItem, ConversationFeed,
    TaskDock, WorkspaceSidebar, InlineChatWorkspace, LauncherControlPanel,
    SmallPanelCard, StatCard, LogWidget, FileDropZone, _DropCanvas,
    SetupOverlay, CommandBar, DeveloperModeDialog, ScanningOverlay,
    BootSequenceOverlay, IncomingAlertDialog, MeetingOverlay,
    FloatingLauncher, GestureCameraPreview,
)
from .gesture_canvas import _GestureRenderCanvas
from .windows_integration import (
    _quiet_run, _quote_cmd_arg, _hidden_launch_args, _startup_run_value,
    _startup_registry_key, _current_boot_stamp, _launched_from_windows_startup,
    _default_app_settings, _default_discord_settings, _camera_available,
    _active_net_label,
)
'''

main_window_ranges = [
    (5588, 7459), # MainWindow
    (7460, 7624), # SystemConnectivitySidebar
    (7625, 8461), # SystemConnectivityPage
    (8462, 8967), # SmartDevicesSection
    (8968, 8976), # _RootShim
    (8977, total), # REXUI
]

main_window_sections = []
for start, end in main_window_ranges:
    main_window_sections.append(extract_lines(start, end))

main_window_content = main_window_imports + "\n\n" + "\n\n".join(main_window_sections)


# ── __init__.py ─────────────────────────────────────────────────────────────
# Re-exports for backward compatibility

init_content = '''"""
ui package — Split from the original monolithic ui.py.

Backward-compatible: `from ui import REXUI` still works.
"""

from .styles import (
    C, _base_dir, qcol, _logo_icon, _logo_pixmap, _framed_logo,
    _icon_pixmap, _attach_pulse_glow, _fmt_time_stamp, _markdown_to_html,
    _file_category, _fmt_size,
)
from .windows_integration import (
    _quiet_run, _quote_cmd_arg, _hidden_launch_args, _startup_run_value,
    _startup_registry_key, _current_boot_stamp, _launched_from_windows_startup,
    _default_app_settings, _default_discord_settings, _camera_available,
    _active_net_label,
)
from .gesture_canvas import _GestureRenderCanvas
from .widgets import (
    BackgroundWidget, RemoteKeyOverlay, _SysMetrics, HudCanvas,
    MetricBar, MessageCard, TaskCard, AttachmentCard, EventCard,
    ArtifactCard, ChatBubble, HistoryConversationItem, ConversationFeed,
    TaskDock, WorkspaceSidebar, InlineChatWorkspace, LauncherControlPanel,
    SmallPanelCard, StatCard, LogWidget, FileDropZone, _DropCanvas,
    SetupOverlay, CommandBar, DeveloperModeDialog, ScanningOverlay,
    BootSequenceOverlay, IncomingAlertDialog, MeetingOverlay,
    FloatingLauncher, GestureCameraPreview,
)
from .main_window import (
    MainWindow, SystemConnectivitySidebar, SystemConnectivityPage,
    SmartDevicesSection, _RootShim, REXUI,
)

__all__ = [
    "C", "_base_dir", "qcol", "_logo_icon", "_logo_pixmap", "_framed_logo",
    "_icon_pixmap", "_attach_pulse_glow", "_fmt_time_stamp", "_markdown_to_html",
    "_file_category", "_fmt_size",
    "_quiet_run", "_quote_cmd_arg", "_hidden_launch_args", "_startup_run_value",
    "_startup_registry_key", "_current_boot_stamp", "_launched_from_windows_startup",
    "_default_app_settings", "_default_discord_settings", "_camera_available",
    "_active_net_label",
    "_GestureRenderCanvas",
    "BackgroundWidget", "RemoteKeyOverlay", "_SysMetrics", "HudCanvas",
    "MetricBar", "MessageCard", "TaskCard", "AttachmentCard", "EventCard",
    "ArtifactCard", "ChatBubble", "HistoryConversationItem", "ConversationFeed",
    "TaskDock", "WorkspaceSidebar", "InlineChatWorkspace", "LauncherControlPanel",
    "SmallPanelCard", "StatCard", "LogWidget", "FileDropZone", "_DropCanvas",
    "SetupOverlay", "CommandBar", "DeveloperModeDialog", "ScanningOverlay",
    "BootSequenceOverlay", "IncomingAlertDialog", "MeetingOverlay",
    "FloatingLauncher", "GestureCameraPreview",
    "MainWindow", "SystemConnectivitySidebar", "SystemConnectivityPage",
    "SmartDevicesSection", "_RootShim", "REXUI",
]
'''


# ── Write all files ─────────────────────────────────────────────────────────

ui_dir = Path("ui")
ui_dir.mkdir(exist_ok=True)

(ui_dir / "styles.py").write_text(styles_content, encoding="utf-8")
print(f"  Written ui/styles.py ({len(styles_content.splitlines())} lines)")

(ui_dir / "windows_integration.py").write_text(windows_content, encoding="utf-8")
print(f"  Written ui/windows_integration.py ({len(windows_content.splitlines())} lines)")

(ui_dir / "gesture_canvas.py").write_text(gesture_content, encoding="utf-8")
print(f"  Written ui/gesture_canvas.py ({len(gesture_content.splitlines())} lines)")

(ui_dir / "widgets.py").write_text(widgets_content, encoding="utf-8")
print(f"  Written ui/widgets.py ({len(widgets_content.splitlines())} lines)")

(ui_dir / "main_window.py").write_text(main_window_content, encoding="utf-8")
print(f"  Written ui/main_window.py ({len(main_window_content.splitlines())} lines)")

(ui_dir / "__init__.py").write_text(init_content, encoding="utf-8")
print(f"  Written ui/__init__.py ({len(init_content.splitlines())} lines)")

# Backup original ui.py
LEGACY_PY.write_text(UI_PY.read_text(encoding="utf-8"), encoding="utf-8")
print(f"  Backed up ui.py → ui_legacy.py")

# Remove original ui.py (replaced by ui/ package)
UI_PY.unlink()
print(f"  Removed ui.py (replaced by ui/ package)")

print("\nDone! ui.py split into 6 modules under ui/")
