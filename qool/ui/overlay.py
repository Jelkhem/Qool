"""مؤشر عائم حديث: كبسولة داكنة شبه شفافة لا تأخذ التركيز ولا تستقبل النقرات."""
from __future__ import annotations

import ctypes
import math

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TRANSPARENT = 0x00000020

COLORS = {"recording": "#FF4D5E", "processing": "#FFB020", "done": "#2BD576", "error": "#FF6B6B", "idle": "#9AA0A6"}


class Overlay(QWidget):
    H = 44

    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.kind = "idle"
        self.text = ""
        self.level = 0.0
        self._bars = [0.15] * 5
        self._phase = 0.0
        self.font_ = QFont("Segoe UI Variable Text", 10)
        self.font_.setWeight(QFont.DemiBold)
        self._hide_timer = QTimer(self, singleShot=True, timeout=self.hide)
        self._anim = QTimer(self, interval=40, timeout=self._tick)

    # توافق مع الكود السابق
    @property
    def label(self):
        return self

    def setText(self, text: str):
        self.text = text
        self._resize()
        self.update()

    def set_level(self, level: float):
        self.level = max(0.0, min(1.0, level))

    def _tick(self):
        self._phase = (self._phase + 0.25) % (2 * math.pi)
        for i in range(len(self._bars)):
            target = 0.15 + self.level * (0.55 + 0.45 * math.sin(self._phase + i * 1.3) ** 2)
            self._bars[i] += (target - self._bars[i]) * 0.45
        self.update()

    def _resize(self):
        fm = QFontMetrics(self.font_)
        w = 20 + 26 + 10 + fm.horizontalAdvance(self.text) + 20
        self.setFixedSize(max(150, w), self.H)
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.center().x() - self.width() // 2, screen.top() + 14)

    def _apply_noactivate(self):
        try:
            hwnd = int(self.winId())
            u = ctypes.windll.user32
            style = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
            u.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TRANSPARENT)
        except Exception:
            pass

    def show_status(self, kind: str, text: str, meter: bool = False, auto_hide_ms: int = 0):
        self.kind = kind
        self.text = text
        self._resize()
        if kind in ("recording", "processing"):
            self._anim.start()
        else:
            self._anim.stop()
        if not self.isVisible():
            self._apply_noactivate()
            self.show()
        self.update()
        if auto_hide_ms:
            self._hide_timer.start(auto_hide_ms)
        else:
            self._hide_timer.stop()

    def hideEvent(self, e):
        self._anim.stop()
        super().hideEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(255, 255, 255, 28), 1))
        p.setBrush(QColor(18, 20, 26, 235))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)

        color = QColor(COLORS.get(self.kind, COLORS["idle"]))
        # في RTL: المؤشر الرسومي على اليمين والنص على يساره
        icon_box = QRectF(r.right() - 20 - 26, r.top() + 9, 26, r.height() - 18)
        cy = icon_box.center().y()
        if self.kind == "recording":
            bw, gap = 3.2, 2.2
            x = icon_box.left()
            for b in self._bars:
                bh = max(4.0, b * icon_box.height())
                p.setPen(Qt.NoPen)
                p.setBrush(color)
                p.drawRoundedRect(QRectF(x, cy - bh / 2, bw, bh), 1.6, 1.6)
                x += bw + gap
        elif self.kind == "processing":
            p.setPen(QPen(color, 3, Qt.SolidLine, Qt.RoundCap))
            d = 16.0
            p.drawArc(QRectF(icon_box.center().x() - d / 2, cy - d / 2, d, d),
                      int(-math.degrees(self._phase) * 16), 260 * 16)
        elif self.kind == "done":
            p.setPen(QPen(color, 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            cx = icon_box.center().x()
            p.drawPolyline([QRectF(cx - 7, cy, 0, 0).topLeft(), QRectF(cx - 2, cy + 5, 0, 0).topLeft(),
                            QRectF(cx + 8, cy - 6, 0, 0).topLeft()])
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawEllipse(icon_box.center(), 5, 5)

        p.setPen(QColor("#F1F3F4"))
        p.setFont(self.font_)
        p.drawText(QRectF(r.left() + 20, r.top(), icon_box.left() - 10 - (r.left() + 20), r.height()),
                   Qt.AlignVCenter | Qt.AlignRight, self.text)
        p.end()
