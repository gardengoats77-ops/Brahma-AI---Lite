from __future__ import annotations

import math
import random
import time

from PyQt6.QtCore import Qt, QTimer, QPoint, QRect, QSize, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPixmap, QBrush
from PyQt6.QtWidgets import QWidget, QSizePolicy

from .styles import C


# ── Gesture Rendering Canvas ────────────────────────────────────────────────

class _GestureRenderCanvas(QWidget):
    CONNECTIONS = [
        (0, 1), (1, 2), (2, 3), (3, 4),
        (0, 5), (5, 6), (6, 7), (7, 8),
        (5, 9), (9, 10), (10, 11), (11, 12),
        (9, 13), (13, 14), (14, 15), (15, 16),
        (13, 17), (17, 18), (18, 19), (19, 20),
        (0, 17)
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAutoFillBackground(False)
        self._landmarks: list[tuple[float, float, float]] = []
        self._hand_visible = False
        self._search_phase = 0
        self._target_opacity = 0.0
        self._skeleton_opacity = 0.0

    def set_landmarks(self, landmarks: list[tuple[float, float, float]]):
        self._landmarks = landmarks or []
        self.update()

    def set_hand_visible(self, visible: bool):
        self._hand_visible = visible
        self._target_opacity = 1.0 if visible else 0.0
        self.update()

    def set_search_phase(self, phase: int):
        self._search_phase = phase
        self.update()

    def _normalized_points(self, rect: QRectF) -> list[QPointF]:
        if not self._landmarks:
            return []
        xs = [p[0] for p in self._landmarks]
        ys = [p[1] for p in self._landmarks]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        bbox_w = max_x - min_x
        bbox_h = max_y - min_y
        if bbox_w < 1e-4:
            bbox_w = 1e-4
        if bbox_h < 1e-4:
            bbox_h = 1e-4
        avail_w = rect.width() * 0.82
        avail_h = rect.height() * 0.82
        scale = min(avail_w / bbox_w, avail_h / bbox_h)
        center_x = rect.center().x()
        center_y = rect.center().y()
        mid_x = (min_x + max_x) / 2.0
        mid_y = (min_y + max_y) / 2.0
        points: list[QPointF] = []
        for x, y, _ in self._landmarks:
            px = center_x + (x - mid_x) * scale
            py = center_y + (y - mid_y) * scale
            points.append(QPointF(px, py))
        return points

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        painter.fillRect(rect, QColor(3, 4, 7))

        if self._hand_visible and len(self._landmarks) >= 21:
            # animate opacity toward target
            self._skeleton_opacity += (self._target_opacity - self._skeleton_opacity) * 0.24
            pts = self._normalized_points(rect)
            if pts:
                # soft glow
                glow_pen = QPen(QColor(255, 70, 70, int(120 * self._skeleton_opacity)), 18,
                                Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
                painter.setPen(glow_pen)
                for a, b in self.CONNECTIONS:
                    painter.drawLine(pts[a], pts[b])

                edge_pen = QPen(QColor(255, 110, 110, int(220 * self._skeleton_opacity)), 4,
                               Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
                painter.setPen(edge_pen)
                for a, b in self.CONNECTIONS:
                    painter.drawLine(pts[a], pts[b])

                for point in pts:
                    radius = 7.0
                    grad = QRadialGradient(point, radius * 2.2)
                    grad.setColorAt(0.0, QColor(255, 255, 255, int(240 * self._skeleton_opacity)))
                    grad.setColorAt(0.15, QColor(255, 130, 130, int(180 * self._skeleton_opacity)))
                    grad.setColorAt(1.0, QColor(255, 30, 30, int(16 * self._skeleton_opacity)))
                    painter.setBrush(QBrush(grad))
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.drawEllipse(point, radius * 1.4, radius * 1.4)
                    painter.setBrush(QColor(255, 255, 255, int(230 * self._skeleton_opacity)))
                    painter.drawEllipse(point, 3.5, 3.5)
        else:
            self._skeleton_opacity += (self._target_opacity - self._skeleton_opacity) * 0.24
            dot_count = (self._search_phase // 8) % 4
            message = "Searching for hand" + ("." * dot_count)
            painter.setPen(QColor(200, 200, 220, 180))
            painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Medium))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, message)

        painter.end()


