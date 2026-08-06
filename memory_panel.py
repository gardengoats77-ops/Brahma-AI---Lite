"""
memory_panel.py — Memory tab for the REX sidebar UI

Lets chuckee view, edit, and delete what REX remembers about them.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QColor
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QVBoxLayout, QWidget, QSizePolicy, QDialog,
    QLineEdit, QTextEdit, QMessageBox,
)

from memory.memory_manager import load_memory, save_memory, update_memory, forget

# ---------------------------------------------------------------------------
# Theme constants (matching ui.py C class)
# ---------------------------------------------------------------------------

BG        = "#020305"
PANEL     = "#07080b"
PANEL2    = "#0d0f14"
BORDER    = "#22252d"
BORDER_B  = "#41454f"
PRI       = "#ff4545"
GREEN     = "#37ff5f"
TEXT      = "#f4f6f8"
TEXT_DIM  = "#8e949d"
TEXT_MED  = "#c5cad2"
WHITE     = "#ffffff"

CATEGORY_ICONS = {
    "identity":      "ID",
    "preferences":   "PF",
    "projects":      "PJ",
    "relationships": "RL",
    "wishes":        "WH",
    "notes":         "NT",
}

CATEGORY_COLORS = {
    "identity":      "#ff6b6b",
    "preferences":   "#ffd93d",
    "projects":      "#6bcb77",
    "relationships": "#4d96ff",
    "wishes":        "#c689c6",
    "notes":         "#ff922b",
}

CATEGORY_LABELS = {
    "identity":      "Identity",
    "preferences":   "Preferences",
    "projects":      "Projects",
    "relationships": "Relationships",
    "wishes":        "Wishes",
    "notes":         "Notes",
}


# ---------------------------------------------------------------------------
# Edit dialog
# ---------------------------------------------------------------------------

class EditMemoryDialog(QDialog):
    """Dialog for editing a single memory entry."""

    def __init__(self, category: str, key: str, value: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Edit Memory — {category}/{key}")
        self.setMinimumSize(420, 260)
        self.setStyleSheet(f"""
            QDialog {{ background: {BG}; color: {TEXT}; }}
            QLabel {{ background: transparent; color: {TEXT_MED}; }}
            QLineEdit, QTextEdit {{
                background: rgba(255,255,255,0.04);
                color: {TEXT};
                border: 1px solid {BORDER};
                border-radius: 8px;
                padding: 8px;
                font-size: 13px;
            }}
            QLineEdit:focus, QTextEdit:focus {{
                border: 1px solid {PRI};
            }}
            QPushButton {{
                background: rgba(255,69,69,20);
                color: {WHITE};
                border: 1px solid {PRI};
                border-radius: 8px;
                padding: 8px 20px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background: rgba(255,69,69,40);
            }}
        """)

        lay = QVBoxLayout(self)
        lay.setSpacing(12)

        # Category + Key header
        header = QLabel(f"{CATEGORY_LABELS.get(category, category)}  /  {key.replace('_', ' ')}")
        header.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        header.setStyleSheet(f"color: {CATEGORY_COLORS.get(category, PRI)};")
        lay.addWidget(header)

        # Value editor
        lay.addWidget(QLabel("Value:"))
        self._value_edit = QTextEdit()
        self._value_edit.setPlainText(value)
        self._value_edit.setMinimumHeight(100)
        lay.addWidget(self._value_edit, 1)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {TEXT_DIM};
                border: 1px solid {BORDER};
            }}
            QPushButton:hover {{ color: {WHITE}; border-color: {PRI}; }}
        """)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self.accept)
        btn_row.addWidget(save_btn)

        lay.addLayout(btn_row)

    def get_value(self) -> str:
        return self._value_edit.toPlainText().strip()


# ---------------------------------------------------------------------------
# Memory entry card
# ---------------------------------------------------------------------------

class MemoryEntryCard(QFrame):
    """A single memory entry with edit and delete buttons."""

    edited = pyqtSignal(str, str, str)    # category, key, new_value
    deleted = pyqtSignal(str, str)        # category, key

    def __init__(self, category: str, key: str, entry: dict, parent=None):
        super().__init__(parent)
        self._category = category
        self._key = key
        self._entry = entry

        value = entry.get("value", "") if isinstance(entry, dict) else str(entry)
        updated = entry.get("updated", "") if isinstance(entry, dict) else ""

        self.setStyleSheet(f"""
            QFrame {{
                background: rgba(255,255,255,0.02);
                border: 1px solid {BORDER};
                border-radius: 10px;
            }}
            QFrame:hover {{
                border: 1px solid rgba(255,69,69,0.3);
                background: rgba(255,255,255,0.04);
            }}
        """)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(10)

        # Category badge
        badge = QLabel(CATEGORY_ICONS.get(category, "??"))
        badge.setFixedSize(32, 32)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
        badge.setStyleSheet(f"""
            background: rgba(255,255,255,0.06);
            color: {CATEGORY_COLORS.get(category, PRI)};
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 8px;
        """)
        lay.addWidget(badge)

        # Content
        content = QVBoxLayout()
        content.setSpacing(2)

        key_label = QLabel(key.replace("_", " ").title())
        key_label.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        key_label.setStyleSheet(f"color: {WHITE};")
        content.addWidget(key_label)

        val_label = QLabel(value[:120] + ("..." if len(value) > 120 else ""))
        val_label.setFont(QFont("Segoe UI", 9))
        val_label.setStyleSheet(f"color: {TEXT_MED};")
        val_label.setWordWrap(True)
        content.addWidget(val_label)

        if updated:
            date_label = QLabel(f"Updated: {updated}")
            date_label.setFont(QFont("Segoe UI", 7))
            date_label.setStyleSheet(f"color: {TEXT_DIM};")
            content.addWidget(date_label)

        lay.addLayout(content, 1)

        # Action buttons
        btn_col = QVBoxLayout()
        btn_col.setSpacing(4)

        edit_btn = QPushButton("Edit")
        edit_btn.setFixedSize(52, 24)
        edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        edit_btn.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        edit_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,255,255,0.04);
                color: {TEXT_MED};
                border: 1px solid {BORDER};
                border-radius: 6px;
            }}
            QPushButton:hover {{ color: {WHITE}; border-color: {PRI}; }}
        """)
        edit_btn.clicked.connect(self._on_edit)
        btn_col.addWidget(edit_btn)

        del_btn = QPushButton("Del")
        del_btn.setFixedSize(52, 24)
        del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        del_btn.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        del_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,69,69,0.08);
                color: #ff7777;
                border: 1px solid rgba(255,69,69,0.2);
                border-radius: 6px;
            }}
            QPushButton:hover {{ background: rgba(255,69,69,0.2); color: #ff4545; }}
        """)
        del_btn.clicked.connect(self._on_delete)
        btn_col.addWidget(del_btn)

        btn_col.addStretch()
        lay.addLayout(btn_col)

    def _on_edit(self):
        value = self._entry.get("value", "") if isinstance(self._entry, dict) else str(self._entry)
        dlg = EditMemoryDialog(self._category, self._key, value, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_val = dlg.get_value()
            if new_val and new_val != value:
                self.edited.emit(self._category, self._key, new_val)

    def _on_delete(self):
        reply = QMessageBox.question(
            self,
            "Delete Memory",
            f"Delete '{self._key.replace('_', ' ')}' from {CATEGORY_LABELS.get(self._category, self._category)}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.deleted.emit(self._category, self._key)


# ---------------------------------------------------------------------------
# Category section
# ---------------------------------------------------------------------------

class MemoryCategorySection(QFrame):
    """A collapsible section for one memory category."""

    def __init__(self, category: str, entries: dict, parent=None):
        super().__init__(parent)
        self._category = category
        self._entries = dict(entries)

        self.setStyleSheet(f"""
            QFrame {{
                background: transparent;
                border: none;
            }}
        """)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        # Category header
        header = QHBoxLayout()
        header.setSpacing(8)

        icon = QLabel(CATEGORY_ICONS.get(category, "??"))
        icon.setFixedSize(24, 24)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
        icon.setStyleSheet(f"""
            background: rgba(255,255,255,0.06);
            color: {CATEGORY_COLORS.get(category, PRI)};
            border-radius: 6px;
        """)
        header.addWidget(icon)

        title = QLabel(CATEGORY_LABELS.get(category, category))
        title.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {WHITE};")
        header.addWidget(title)

        count = QLabel(f"({len(self._entries)})")
        count.setFont(QFont("Segoe UI", 9))
        count.setStyleSheet(f"color: {TEXT_DIM};")
        header.addWidget(count)

        header.addStretch()
        lay.addLayout(header)

        # Entries
        for key, entry in self._entries.items():
            card = MemoryEntryCard(category, key, entry)
            card.edited.connect(self._on_entry_edited)
            card.deleted.connect(self._on_entry_deleted)
            lay.addWidget(card)

    def _on_entry_edited(self, category: str, key: str, new_value: str):
        try:
            update_memory({category: {key: {"value": new_value}}})
        except Exception as e:
            print(f"[Memory] Edit failed: {e}")

    def _on_entry_deleted(self, category: str, key: str):
        try:
            forget(key, category)
        except Exception as e:
            print(f"[Memory] Delete failed: {e}")


# ---------------------------------------------------------------------------
# Main Memory Panel
# ---------------------------------------------------------------------------

class MemoryPanel(QWidget):
    """Full memory management panel for the REX sidebar."""

    memory_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header
        header = QFrame()
        header.setStyleSheet(f"""
            QFrame {{
                background: {PANEL};
                border-bottom: 1px solid {BORDER};
                padding: 14px;
            }}
        """)
        header_lay = QHBoxLayout(header)
        header_lay.setContentsMargins(14, 12, 14, 12)

        title = QLabel("MEMORY")
        title.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {WHITE}; letter-spacing: 1px;")
        header_lay.addWidget(title)

        header_lay.addStretch()

        self._count_label = QLabel("0 entries")
        self._count_label.setFont(QFont("Segoe UI", 8))
        self._count_label.setStyleSheet(f"color: {TEXT_DIM};")
        header_lay.addWidget(self._count_label)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_btn.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        refresh_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,255,255,0.04);
                color: {TEXT_MED};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 4px 12px;
            }}
            QPushButton:hover {{ color: {WHITE}; border-color: {PRI}; }}
        """)
        refresh_btn.clicked.connect(self.refresh)
        header_lay.addWidget(refresh_btn)

        root.addWidget(header)

        # Scroll area with memory content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(f"""
            QScrollArea {{
                background: transparent;
                border: none;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 6px;
                margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(255,255,255,0.12);
                border-radius: 3px;
                min-height: 30px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: rgba(255,255,255,0.2);
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
            }}
        """)

        self._content = QWidget()
        self._content.setStyleSheet("background: transparent;")
        self._content_lay = QVBoxLayout(self._content)
        self._content_lay.setContentsMargins(14, 14, 14, 14)
        self._content_lay.setSpacing(16)
        self._content_lay.addStretch()

        scroll.setWidget(self._content)
        root.addWidget(scroll, 1)

    def refresh(self):
        """Reload memory from disk and rebuild the UI."""
        # Clear existing content (keep the stretch at the end)
        while self._content_lay.count() > 1:
            item = self._content_lay.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        memory = load_memory()
        total = 0

        for category in ["identity", "preferences", "projects", "relationships", "wishes", "notes"]:
            entries = memory.get(category, {})
            if not entries:
                continue
            total += len(entries)
            section = MemoryCategorySection(category, entries)
            section.setStyleSheet(f"""
                QFrame {{
                    background: rgba(255,255,255,0.015);
                    border: 1px solid {BORDER};
                    border-radius: 12px;
                    padding: 12px;
                }}
            """)
            self._content_lay.insertWidget(self._content_lay.count() - 1, section)

        self._count_label.setText(f"{total} entr{'y' if total == 1 else 'ies'}")

        if total == 0:
            empty = QLabel("No memories yet.\nREX will remember things as you chat.")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setFont(QFont("Segoe UI", 10))
            empty.setStyleSheet(f"color: {TEXT_DIM}; padding: 40px;")
            self._content_lay.insertWidget(self._content_lay.count() - 1, empty)

        self.memory_changed.emit()
