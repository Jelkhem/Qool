"""عناصر واجهة حديثة مرسومة يدويًا: زر تسجيل دائري، مفتاح تبديل، التقاط اختصار، بطاقات."""
from __future__ import annotations

import math

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractButton, QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QSizePolicy,
                               QVBoxLayout, QWidget)


# ---------- رسومات ----------

def draw_mic(p: QPainter, rect: QRectF, color: QColor, stroke: float) -> None:
    """أيقونة ميكروفون بخطوط (بدون خطوط نصية)."""
    w, h = rect.width(), rect.height()
    cx = rect.center().x()
    pen = QPen(color, stroke, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    body = QRectF(cx - w * 0.17, rect.top() + h * 0.08, w * 0.34, h * 0.52)
    p.drawRoundedRect(body, w * 0.17, w * 0.17)
    arc = QRectF(cx - w * 0.30, rect.top() + h * 0.22, w * 0.60, h * 0.55)
    p.drawArc(arc, 200 * 16, 140 * 16)
    p.drawLine(int(cx), int(rect.top() + h * 0.77), int(cx), int(rect.top() + h * 0.92))
    p.drawLine(int(cx - w * 0.16), int(rect.top() + h * 0.92), int(cx + w * 0.16), int(rect.top() + h * 0.92))


def mic_icon(bg: str, fg: str = "#FFFFFF", size: int = 64, dot: str | None = None) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(bg))
    p.drawRoundedRect(QRectF(2, 2, size - 4, size - 4), size * 0.28, size * 0.28)
    draw_mic(p, QRectF(size * 0.2, size * 0.16, size * 0.6, size * 0.68), QColor(fg), size * 0.075)
    if dot:
        p.setPen(QPen(QColor("#FFFFFF"), size * 0.04))
        p.setBrush(QColor(dot))
        p.drawEllipse(QRectF(size * 0.62, size * 0.02, size * 0.36, size * 0.36))
    p.end()
    return QIcon(pm)


# ---------- زر التسجيل ----------

class RecordButton(QAbstractButton):
    """دائرة كبيرة: ميكروفون (جاهز) / مربع إيقاف مع حلقة مستوى الصوت (تسجيل) / قوس دوّار (معالجة)."""

    def __init__(self, t: dict, parent=None):
        super().__init__(parent)
        self.t = t
        self.state = "idle"
        self.level = 0.0
        self._shown_level = 0.0
        self._phase = 0.0
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(150, 150)
        self._anim = QTimer(self, interval=33, timeout=self._tick)  # يعمل فقط أثناء التسجيل/المعالجة

    def set_theme(self, t: dict):
        self.t = t
        self.update()

    def set_state(self, state: str):
        self.state = state
        if state in ("recording", "processing"):
            self._anim.start()
        else:
            self._anim.stop()
            self.level = self._shown_level = 0.0
        self.update()

    def set_level(self, level: float):
        self.level = max(0.0, min(1.0, level))

    def _tick(self):
        self._phase = (self._phase + 0.06) % (2 * math.pi)
        self._shown_level += (self.level - self._shown_level) * 0.35
        self.update()

    def sizeHint(self):
        return QSize(150, 150)

    def paintEvent(self, _):
        t = self.t
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QRectF(self.rect()).center()
        base_r = 46.0
        max_r = self.width() / 2 - 1  # الهالة لا تتجاوز حدود العنصر
        color = {"recording": t["red"], "processing": t["amber"], "error": t["danger"]}.get(self.state, t["accent"])
        col = QColor(color)
        if not self.isEnabled():
            col.setAlpha(140)

        if self.state == "recording":
            # هالة تتنفس + حلقة مستوى الصوت
            glow = QColor(color)
            for i, extra in enumerate((20 + 8 * math.sin(self._phase), 8 + 20 * self._shown_level)):
                glow.setAlpha(40 if i == 0 else 70)
                p.setPen(Qt.NoPen)
                p.setBrush(glow)
                r = min(max_r, base_r + max(0.0, extra))
                p.drawEllipse(c, r, r)
        elif self.underMouse() and self.isEnabled():
            halo = QColor(color)
            halo.setAlpha(45)
            p.setPen(Qt.NoPen)
            p.setBrush(halo)
            p.drawEllipse(c, base_r + 10, base_r + 10)

        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawEllipse(c, base_r, base_r)

        white = QColor("#FFFFFF")
        if self.state == "recording":
            p.setBrush(white)
            s = 26.0
            p.drawRoundedRect(QRectF(c.x() - s / 2, c.y() - s / 2, s, s), 6, 6)
        elif self.state == "processing":
            p.setPen(QPen(white, 4.5, Qt.SolidLine, Qt.RoundCap))
            p.setBrush(Qt.NoBrush)
            r = 19.0
            start = int(-math.degrees(self._phase * 2) * 16)
            p.drawArc(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), start, 250 * 16)
        else:
            draw_mic(p, QRectF(c.x() - 21, c.y() - 24, 42, 48), white, 4.0)
        p.end()

    def enterEvent(self, e):
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.update()
        super().leaveEvent(e)


# ---------- مفتاح تبديل ----------

class ToggleSwitch(QCheckBox):
    def __init__(self, text: str, t: dict, parent=None):
        super().__init__(text, parent)
        self.t = t
        self._offset = 1.0 if self.isChecked() else 0.0
        self._anim = QPropertyAnimation(self, b"offset", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def _get(self):
        return self._offset

    def _set(self, v):
        self._offset = v
        self.update()

    offset = Property(float, _get, _set)

    def setChecked(self, on):
        super().setChecked(on)
        self._anim.stop()
        self._offset = 1.0 if on else 0.0
        self.update()

    def _animate(self, on):
        self._anim.stop()
        self._anim.setStartValue(self._offset)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def sizeHint(self):
        fm = self.fontMetrics()
        return QSize(46 + 12 + fm.horizontalAdvance(self.text()), max(26, fm.height() + 8))

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, _):
        t = self.t
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rtl = self.layoutDirection() == Qt.RightToLeft
        h = self.height()
        tw, th = 42.0, 24.0
        tx = self.width() - tw if rtl else 0.0
        track = QRectF(tx, (h - th) / 2, tw, th)
        on_col, off_col = QColor(t["accent"]), QColor(t["border"])
        k = self._offset
        mix = QColor(int(off_col.red() + (on_col.red() - off_col.red()) * k),
                     int(off_col.green() + (on_col.green() - off_col.green()) * k),
                     int(off_col.blue() + (on_col.blue() - off_col.blue()) * k))
        if not self.isEnabled():
            mix.setAlpha(120)
        p.setPen(Qt.NoPen)
        p.setBrush(mix)
        p.drawRoundedRect(track, th / 2, th / 2)
        knob_d = th - 6
        travel = tw - knob_d - 6
        x = track.left() + 3 + (travel * (1 - k) if rtl else travel * k)
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QRectF(x, track.top() + 3, knob_d, knob_d))
        p.setPen(QColor(t["text"] if self.isEnabled() else t["muted"]))
        text_rect = QRectF(0, 0, self.width() - tw - 12, h) if rtl else QRectF(tw + 12, 0, self.width() - tw - 12, h)
        p.drawText(text_rect, Qt.AlignVCenter | (Qt.AlignRight if rtl else Qt.AlignLeft), self.text())
        p.end()


# ---------- التقاط اختصار ----------

_VK_NAMES = {0x20: "Space", 0x0D: "Enter", 0x08: "Backspace", 0x09: "Tab", 0x13: "Pause", 0x2D: "Insert",
             0x2E: "Delete", 0x24: "Home", 0x23: "End", 0x21: "PageUp", 0x22: "PageDown", 0x25: "Left",
             0x26: "Up", 0x27: "Right", 0x28: "Down", 0xC0: "`", 0x91: "ScrollLock"}
_VK_NAMES.update({c: chr(c) for c in range(0x41, 0x5B)})
_VK_NAMES.update({0x30 + d: str(d) for d in range(10)})
_VK_NAMES.update({0x6F + n: f"F{n}" for n in range(1, 25)})
_VK_NAMES.update({0x60 + d: f"Num{d}" for d in range(10)})
_MODIFIER_VKS = {0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5}


class HotkeyEdit(QLineEdit):
    """انقر ثم اضغط الاختصار. يعتمد على رمز المفتاح الفعلي فيعمل مع لوحة المفاتيح العربية."""

    captured = Signal(str)
    capture_started = Signal()
    capture_ended = Signal()

    def __init__(self, value: str, parent=None):
        super().__init__(value, parent)
        self._value = value
        self.setReadOnly(True)
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(Qt.PointingHandCursor)
        self.setLayoutDirection(Qt.LeftToRight)
        self.setToolTip("انقر ثم اضغط الاختصار الجديد. Esc للإلغاء.")

    def set_value(self, v: str):
        self._value = v
        self.setText(v)

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self.setText("")
        self.setPlaceholderText("اضغط الاختصار الآن…")
        self.capture_started.emit()

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self.setText(self._value)
        self.capture_ended.emit()

    def keyPressEvent(self, e):
        vk = e.nativeVirtualKey()
        if vk == 0x1B:  # Esc
            self.clearFocus()
            return
        if vk in _MODIFIER_VKS or vk not in _VK_NAMES:
            mods = self._mods(e.modifiers())
            self.setPlaceholderText("+".join(mods) + "+…" if mods else "اضغط الاختصار الآن…")
            return
        combo = "+".join(self._mods(e.modifiers()) + [_VK_NAMES[vk]])
        self._value = combo
        self.clearFocus()
        self.captured.emit(combo)

    @staticmethod
    def _mods(m) -> list[str]:
        out = []
        if m & Qt.ControlModifier:
            out.append("Ctrl")
        if m & Qt.AltModifier:
            out.append("Alt")
        if m & Qt.ShiftModifier:
            out.append("Shift")
        if m & Qt.MetaModifier:
            out.append("Win")
        return out


# ---------- حاويات ----------

class Card(QFrame):
    def __init__(self, title: str = "", subtitle: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(18, 16, 18, 16)
        self.body.setSpacing(10)
        if title:
            lbl = QLabel(title)
            lbl.setObjectName("h2")
            self.body.addWidget(lbl)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("small")
            sub.setWordWrap(True)
            self.body.addWidget(sub)


def row(label: str, widget: QWidget, stretch: bool = True) -> QWidget:
    w = QWidget()
    w.setObjectName("clear")
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(12)
    lbl = QLabel(label)
    lbl.setMinimumWidth(150)
    h.addWidget(lbl)
    h.addWidget(widget, 1 if stretch else 0)
    return w


def separator() -> QFrame:
    f = QFrame()
    f.setObjectName("sep")
    return f


def kbd(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("kbd")
    lbl.setLayoutDirection(Qt.LeftToRight)
    return lbl
