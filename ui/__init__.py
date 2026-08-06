"""
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
