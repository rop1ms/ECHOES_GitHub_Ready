# daw_ui.py
"""
Общие виджеты темы «Echoes Studio»: палитра, ручки, фейдеры, индикаторы, кнопки с
нарисованными иконками (никаких эмодзи — всё рисуется QPainter'ом), поле темпа, табло
времени и плавающие окна внутри рабочей области (как окна в студийных программах).
"""
from __future__ import annotations

import math
import time

from PyQt6.QtCore import QEvent, QObject, QPointF, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (QBrush, QColor, QCursor, QFont, QLinearGradient, QPainter, QPainterPath, QPen,
                         QRadialGradient)
from PyQt6.QtWidgets import (QAbstractButton, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QSizePolicy,
                             QVBoxLayout, QWidget)

# ── палитра ── #
BG0 = QColor("#1c2023")
BG1 = QColor("#2a3035")
BG2 = QColor("#343b41")
BG3 = QColor("#414950")
BG4 = QColor("#4e575f")
LINE = QColor("#4a535b")
TEXT = QColor("#d2d9de")
DIM = QColor("#8d979f")
ACC = QColor("#f0a33a")
GRN = QColor("#9bd15c")
BLUE = QColor("#5ea8e8")
RED = QColor("#e8574f")
STEP_A = QColor("#424a51")
STEP_B = QColor("#4c3f42")

QSS = f"""
QWidget {{ color: {TEXT.name()}; font-family: 'Segoe UI'; font-size: 12px; }}
QMenu {{ background: #2b3136; color: #d8dee2; border: 1px solid #4f5860; padding: 4px; }}
QMenu::item {{ padding: 5px 22px 5px 14px; }}
QMenu::item:selected {{ background: #f0a33a; color: #1b1e21; }}
QMenu::item:disabled {{ color: #7d878f; }}
QMenu::separator {{ height: 1px; background: #48515a; margin: 4px 6px; }}
QToolTip {{ background: #2b3136; color: #e3e8eb; border: 1px solid #f0a33a; padding: 4px; }}
QLineEdit {{ background: #1e2326; border: 1px solid #4a535b; border-radius: 3px; padding: 3px 6px;
            selection-background-color: #f0a33a; selection-color: #111; }}
QLineEdit:focus {{ border-color: #f0a33a; }}
QComboBox {{ background: #3a4248; border: 1px solid #4f5860; border-radius: 3px; padding: 2px 8px; min-height: 18px; }}
QComboBox:hover {{ border-color: #6b757d; }}
QComboBox::drop-down {{ border: none; width: 16px; }}
QComboBox QAbstractItemView {{ background: #2b3136; border: 1px solid #4f5860; selection-background-color: #f0a33a;
                               selection-color: #111; outline: none; }}
QPushButton {{ background: #3e464d; border: 1px solid #535c64; border-radius: 3px; padding: 4px 10px; }}
QPushButton:hover {{ background: #4a535b; border-color: #6c767e; }}
QPushButton:pressed {{ background: #f0a33a; color: #111; }}
QPushButton:checked {{ background: #f0a33a; color: #111; border-color: #f0a33a; }}
QPushButton:disabled {{ color: #6d777f; }}
QCheckBox {{ spacing: 6px; }}
QCheckBox::indicator {{ width: 13px; height: 13px; border: 1px solid #5a646c; background: #1e2326; border-radius: 2px; }}
QCheckBox::indicator:checked {{ background: #9bd15c; border-color: #9bd15c; }}
QScrollBar:vertical {{ background: #22272b; width: 11px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #4a535b; min-height: 24px; border-radius: 4px; margin: 2px; }}
QScrollBar::handle:vertical:hover {{ background: #5d6770; }}
QScrollBar:horizontal {{ background: #22272b; height: 11px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: #4a535b; min-width: 24px; border-radius: 4px; margin: 2px; }}
QScrollBar::handle:horizontal:hover {{ background: #5d6770; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
QTreeWidget {{ background: #252a2e; border: none; outline: none; }}
QTreeWidget::item {{ padding: 2px 0; }}
QTreeWidget::item:selected {{ background: #3f474e; color: #f0a33a; }}
QTreeWidget::item:hover {{ background: #30363b; }}
QListWidget {{ background: #252a2e; border: none; outline: none; }}
QListWidget::item:selected {{ background: #3f474e; color: #f0a33a; }}
QSlider::groove:horizontal {{ height: 4px; background: #1e2326; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: #f0a33a; width: 10px; margin: -5px 0; border-radius: 5px; }}
QProgressBar {{ background: #1e2326; border: 1px solid #4a535b; border-radius: 3px; text-align: center; height: 14px; }}
QProgressBar::chunk {{ background: #f0a33a; border-radius: 2px; }}
QTextBrowser {{ background: #22272b; border: none; }}
"""


def font(px=12, bold=False, mono=False) -> QFont:
    f = QFont("Consolas" if mono else "Segoe UI")
    f.setPixelSize(px)
    f.setBold(bold)
    return f


def mix_col(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(int(a.red() + (b.red() - a.red()) * t), int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t))


# ------------------------------------------------------------------ #
#  Иконки                                                             #
# ------------------------------------------------------------------ #

def draw_icon(p: QPainter, name: str, r: QRectF, col: QColor):
    """Простые векторные иконки (без шрифтов и эмодзи)."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    c = r.center()
    s = min(r.width(), r.height())
    pen = QPen(col, max(1.3, s * 0.09))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    u = s / 2

    def pt(x, y):
        return QPointF(c.x() + x * u, c.y() + y * u)
    if name == "play":
        path = QPainterPath(pt(-0.45, -0.6))
        path.lineTo(pt(0.6, 0))
        path.lineTo(pt(-0.45, 0.6))
        path.closeSubpath()
        p.setBrush(col)
        p.drawPath(path)
    elif name == "pause":
        p.setBrush(col)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRect(QRectF(pt(-0.5, -0.55), pt(-0.12, 0.55)))
        p.drawRect(QRectF(pt(0.12, -0.55), pt(0.5, 0.55)))
    elif name == "stop":
        p.setBrush(col)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRect(QRectF(pt(-0.48, -0.48), pt(0.48, 0.48)))
    elif name == "rec":
        p.setBrush(col)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(c, u * 0.55, u * 0.55)
    elif name == "metro":
        path = QPainterPath(pt(-0.45, 0.65))
        path.lineTo(pt(-0.18, -0.65))
        path.lineTo(pt(0.18, -0.65))
        path.lineTo(pt(0.45, 0.65))
        path.closeSubpath()
        p.drawPath(path)
        p.drawLine(pt(0, 0.45), pt(0.42, -0.35))
    elif name == "loop":
        p.drawArc(QRectF(pt(-0.6, -0.45), pt(0.6, 0.45)), 30 * 16, 300 * 16)
        p.drawLine(pt(0.5, -0.32), pt(0.62, -0.05))
        p.drawLine(pt(0.5, -0.32), pt(0.25, -0.22))
    elif name in ("undo", "redo"):
        sg = -1 if name == "undo" else 1
        p.drawArc(QRectF(pt(-0.5, -0.35), pt(0.5, 0.6)), 0 * 16, 180 * 16)
        x0 = 0.5 * sg                                   # конец дуги — стрелка вниз
        p.drawLine(pt(x0, 0.15), pt(x0 - 0.22, -0.08))
        p.drawLine(pt(x0, 0.15), pt(x0 + 0.22, -0.08))
    elif name == "save":
        p.drawRoundedRect(QRectF(pt(-0.55, -0.55), pt(0.55, 0.55)), 2, 2)
        p.drawRect(QRectF(pt(-0.3, -0.55), pt(0.3, -0.15)))
        p.drawRect(QRectF(pt(-0.32, 0.12), pt(0.32, 0.55)))
    elif name == "open":
        path = QPainterPath(pt(-0.6, 0.5))
        path.lineTo(pt(-0.6, -0.45))
        path.lineTo(pt(-0.2, -0.45))
        path.lineTo(pt(-0.05, -0.28))
        path.lineTo(pt(0.55, -0.28))
        path.lineTo(pt(0.55, 0.5))
        path.closeSubpath()
        p.drawPath(path)
    elif name == "new":
        path = QPainterPath(pt(-0.42, -0.6))
        path.lineTo(pt(0.18, -0.6))
        path.lineTo(pt(0.45, -0.32))
        path.lineTo(pt(0.45, 0.6))
        path.lineTo(pt(-0.42, 0.6))
        path.closeSubpath()
        p.drawPath(path)
    elif name == "export":
        p.drawLine(pt(0, -0.6), pt(0, 0.2))
        p.drawLine(pt(0, 0.2), pt(-0.3, -0.1))
        p.drawLine(pt(0, 0.2), pt(0.3, -0.1))
        p.drawLine(pt(-0.55, 0.3), pt(-0.55, 0.6))
        p.drawLine(pt(-0.55, 0.6), pt(0.55, 0.6))
        p.drawLine(pt(0.55, 0.6), pt(0.55, 0.3))
    elif name == "playlist":
        for i, (x0, x1) in enumerate(((-0.6, 0.1), (-0.3, 0.6), (-0.6, -0.05))):
            y = -0.45 + i * 0.45
            p.drawLine(pt(x0, y), pt(x1, y))
    elif name == "rack":
        for i in range(3):
            y = -0.45 + i * 0.45
            p.drawLine(pt(-0.6, y), pt(-0.35, y))
            for k in range(3):
                x = -0.15 + k * 0.28
                p.drawRect(QRectF(pt(x, y - 0.12), pt(x + 0.18, y + 0.12)))
    elif name == "roll":
        p.drawRect(QRectF(pt(-0.6, -0.55), pt(-0.3, 0.55)))
        p.drawLine(pt(-0.15, -0.3), pt(0.25, -0.3))
        p.drawLine(pt(0.05, 0.05), pt(0.6, 0.05))
        p.drawLine(pt(-0.15, 0.4), pt(0.2, 0.4))
    elif name == "mixer":
        for i, y in enumerate((-0.2, 0.25, -0.05)):
            x = -0.45 + i * 0.45
            p.drawLine(pt(x, -0.6), pt(x, 0.6))
            p.setBrush(col)
            p.drawRect(QRectF(pt(x - 0.13, y - 0.08), pt(x + 0.13, y + 0.08)))
            p.setBrush(Qt.BrushStyle.NoBrush)
    elif name == "browser":
        p.drawRect(QRectF(pt(-0.6, -0.55), pt(0.6, 0.55)))
        p.drawLine(pt(-0.15, -0.55), pt(-0.15, 0.55))
        p.drawLine(pt(-0.48, -0.25), pt(-0.28, -0.25))
        p.drawLine(pt(-0.48, 0.05), pt(-0.28, 0.05))
    elif name == "bot":
        p.drawRoundedRect(QRectF(pt(-0.55, -0.35), pt(0.55, 0.55)), 3, 3)
        p.drawLine(pt(0, -0.35), pt(0, -0.6))
        p.setBrush(col)
        p.drawEllipse(pt(-0.22, 0.08), u * 0.1, u * 0.1)
        p.drawEllipse(pt(0.22, 0.08), u * 0.1, u * 0.1)
    elif name == "plus":
        p.drawLine(pt(-0.5, 0), pt(0.5, 0))
        p.drawLine(pt(0, -0.5), pt(0, 0.5))
    elif name == "minus":
        p.drawLine(pt(-0.5, 0), pt(0.5, 0))
    elif name == "close":
        p.drawLine(pt(-0.42, -0.42), pt(0.42, 0.42))
        p.drawLine(pt(-0.42, 0.42), pt(0.42, -0.42))
    elif name == "max":
        p.drawRect(QRectF(pt(-0.45, -0.45), pt(0.45, 0.45)))
        p.drawLine(pt(-0.45, -0.25), pt(0.45, -0.25))
    elif name == "pencil":
        p.drawLine(pt(-0.5, 0.5), pt(0.45, -0.45))
        p.drawLine(pt(-0.5, 0.5), pt(-0.55, 0.15))
        p.drawLine(pt(-0.5, 0.5), pt(-0.15, 0.55))
    elif name == "brush":
        p.drawLine(pt(0.5, -0.5), pt(-0.1, 0.1))
        p.setBrush(col)
        p.drawEllipse(pt(-0.3, 0.3), u * 0.25, u * 0.25)
    elif name == "erase":
        p.drawRoundedRect(QRectF(pt(-0.55, -0.2), pt(0.4, 0.35)), 2, 2)
        p.drawLine(pt(-0.1, -0.2), pt(-0.1, 0.35))
    elif name == "slice":
        p.drawLine(pt(-0.3, -0.6), pt(0.3, 0.6))
        p.drawEllipse(pt(-0.35, 0.45), u * 0.15, u * 0.15)
    elif name == "select":
        pen2 = QPen(col, max(1.0, s * 0.07), Qt.PenStyle.DashLine)
        p.setPen(pen2)
        p.drawRect(QRectF(pt(-0.55, -0.45), pt(0.55, 0.45)))
    elif name == "mute":
        p.drawEllipse(c, u * 0.5, u * 0.5)
        p.drawLine(pt(-0.35, 0.35), pt(0.35, -0.35))
    elif name == "magnet":
        p.drawArc(QRectF(pt(-0.45, -0.5), pt(0.45, 0.4)), 180 * 16, 180 * 16)
        p.drawLine(pt(-0.45, -0.05), pt(-0.45, -0.5))
        p.drawLine(pt(0.45, -0.05), pt(0.45, -0.5))
    elif name == "mic":
        p.drawRoundedRect(QRectF(pt(-0.22, -0.65), pt(0.22, 0.15)), u * 0.2, u * 0.2)
        p.drawArc(QRectF(pt(-0.42, -0.25), pt(0.42, 0.35)), 180 * 16, 180 * 16)
        p.drawLine(pt(0, 0.35), pt(0, 0.6))
    elif name == "gear":
        p.drawEllipse(c, u * 0.3, u * 0.3)
        for k in range(8):
            a = k * math.pi / 4
            p.drawLine(pt(0.42 * math.cos(a), 0.42 * math.sin(a)), pt(0.62 * math.cos(a), 0.62 * math.sin(a)))
    elif name == "note":
        p.setBrush(col)
        p.drawEllipse(pt(-0.25, 0.4), u * 0.22, u * 0.17)
        p.drawLine(pt(-0.05, 0.38), pt(-0.05, -0.55))
        p.drawLine(pt(-0.05, -0.55), pt(0.35, -0.35))
    elif name == "wave":
        path = QPainterPath(pt(-0.65, 0))
        for k in range(1, 21):
            x = -0.65 + 1.3 * k / 20
            path.lineTo(pt(x, 0.5 * math.sin(k * 0.9) * (1 - abs(x))))
        p.drawPath(path)
    elif name == "drum":
        p.drawEllipse(QRectF(pt(-0.55, -0.45), pt(0.55, -0.1)))
        p.drawLine(pt(-0.55, -0.27), pt(-0.55, 0.35))
        p.drawLine(pt(0.55, -0.27), pt(0.55, 0.35))
        p.drawArc(QRectF(pt(-0.55, 0.15), pt(0.55, 0.5)), 180 * 16, 180 * 16)
    elif name == "fx":
        p.drawEllipse(pt(-0.25, -0.25), u * 0.25, u * 0.25)
        p.drawEllipse(pt(0.25, 0.25), u * 0.25, u * 0.25)
        p.drawLine(pt(-0.05, -0.05), pt(0.05, 0.05))
    elif name == "folder":
        draw_icon(p, "open", r, col)
    elif name == "star":
        path = QPainterPath()
        for k in range(10):
            a = -math.pi / 2 + k * math.pi / 5
            rr = 0.6 if k % 2 == 0 else 0.25
            q = pt(rr * math.cos(a), rr * math.sin(a))
            if k == 0:
                path.moveTo(q)
            else:
                path.lineTo(q)
        path.closeSubpath()
        p.drawPath(path)
    elif name == "send":
        path = QPainterPath(pt(-0.6, -0.5))
        path.lineTo(pt(0.6, 0))
        path.lineTo(pt(-0.6, 0.5))
        path.lineTo(pt(-0.3, 0))
        path.closeSubpath()
        p.drawPath(path)
    elif name == "chev_down":
        p.drawLine(pt(-0.4, -0.15), pt(0, 0.25))
        p.drawLine(pt(0, 0.25), pt(0.4, -0.15))
    elif name == "chev_right":
        p.drawLine(pt(-0.15, -0.4), pt(0.25, 0))
        p.drawLine(pt(0.25, 0), pt(-0.15, 0.4))
    elif name == "headphones":
        p.drawArc(QRectF(pt(-0.5, -0.55), pt(0.5, 0.45)), 0, 180 * 16)
        p.setBrush(col)
        p.drawRoundedRect(QRectF(pt(-0.6, 0), pt(-0.32, 0.5)), 2, 2)
        p.drawRoundedRect(QRectF(pt(0.32, 0), pt(0.6, 0.5)), 2, 2)
    p.restore()


class IconButton(QAbstractButton):
    """Плоская кнопка с нарисованной иконкой; checkable — подсвечивается акцентом."""

    def __init__(self, icon: str, tip: str = "", size=26, parent=None, checkable=False, color=None, text=""):
        super().__init__(parent)
        self.icon = icon
        self.txt = text
        self.col = color
        self.setCheckable(checkable)
        self._tip = tip
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        from PyQt6.QtGui import QFontMetrics
        w = size if not text else size + 10 + QFontMetrics(font(12, True)).horizontalAdvance(text) + 4
        self.setFixedSize(w, size)
        self.hover = False

    def sizeHint(self):
        return self.size()

    def enterEvent(self, e):
        self.hover = True
        self.update()
        hint_to(self, self._tip)

    def leaveEvent(self, e):
        self.hover = False
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        on = self.isChecked() or self.isDown()
        if on:
            g = QLinearGradient(r.topLeft(), r.bottomLeft())
            base = self.col or ACC
            g.setColorAt(0, base.lighter(115))
            g.setColorAt(1, base.darker(115))
            p.setBrush(g)
            p.setPen(QPen(base.darker(140), 1))
        else:
            g = QLinearGradient(r.topLeft(), r.bottomLeft())
            g.setColorAt(0, BG4 if self.hover else BG3)
            g.setColorAt(1, BG3 if self.hover else BG2)
            p.setBrush(g)
            p.setPen(QPen(LINE.lighter(115) if self.hover else LINE, 1))
        p.drawRoundedRect(r, 3, 3)
        ic = QColor("#16191b") if on else (self.col if (self.col and not on) else TEXT)
        if not self.isEnabled():
            ic = DIM
        h = r.height()
        ir = QRectF(r.left() + (h - h * 0.62) / 2, r.top() + h * 0.19, h * 0.62, h * 0.62) if self.txt else \
            QRectF(r.center().x() - h * 0.31, r.top() + h * 0.19, h * 0.62, h * 0.62)
        draw_icon(p, self.icon, ir, ic)
        if self.txt:
            p.setPen(QColor("#16191b") if on else TEXT)
            p.setFont(font(12, True))
            p.drawText(QRectF(ir.right() + 5, r.top(), r.width(), r.height()),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.txt)
        p.end()


class _TipFilter(QObject):
    """Пояснение элемента — в строку подсказки при наведении; всплывающие подсказки Qt выключены."""

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QEvent.Type.ToolTip:
            return True
        if t == QEvent.Type.Enter:
            txt = obj.property("echoes_tip")
            if txt:
                hint_to(obj, str(txt))
        return False


_TIPF = None


def tip(w: QWidget, text: str):
    """Пояснение к элементу: показывается в строке подсказки студии (без всплывашек)."""
    global _TIPF
    if _TIPF is None:
        _TIPF = _TipFilter()
    w.setProperty("echoes_tip", text)
    w.installEventFilter(_TIPF)


def hint_to(w: QWidget, text: str):
    """Строка подсказки наверху студии (как в студийных программах)."""
    sh = w
    while sh is not None and not hasattr(sh, "hint"):
        sh = sh.parentWidget()
    if sh is not None and text:
        try:
            sh.hint(text)
        except Exception:                                # noqa: BLE001
            pass


# ------------------------------------------------------------------ #
#  Ручка                                                              #
# ------------------------------------------------------------------ #

class Knob(QWidget):
    """Ручка: тянуть вверх/вниз (Ctrl — точно), колесо, двойной щелчок — по умолчанию,
    правая кнопка — меню (ввести значение, сброс)."""
    moved = pyqtSignal(float)
    pressed_ = pyqtSignal()
    released_ = pyqtSignal()

    def __init__(self, spec, get, setv, size=30, color=None, label="", bipolar=None, parent=None):
        super().__init__(parent)
        self.spec = spec
        self.get = get
        self.setv = setv
        self.color = color or ACC
        self.label = label
        self.bipolar = (spec.lo < 0 < spec.hi) if bipolar is None and spec is not None else bool(bipolar)
        self.setFixedSize(size, size + (14 if label else 0))
        self.ks = size
        self._drag = None
        self.setCursor(Qt.CursorShape.SizeVerCursor)

    def norm(self):
        try:
            return self.spec.norm(self.get())
        except Exception:                                # noqa: BLE001
            return 0.0

    def text(self):
        try:
            return f"{self.spec.name}: {self.spec.fmt(self.get())}"
        except Exception:                                # noqa: BLE001
            return self.label

    def set_norm(self, t):
        v = self.spec.value(t)
        self.setv(v)
        self.moved.emit(float(v))
        self.update()
        hint_to(self, self.text())

    def enterEvent(self, e):
        hint_to(self, self.text())

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = (e.position().y(), self.norm())
            self.pressed_.emit()
        elif e.button() == Qt.MouseButton.RightButton:
            self._menu(e.globalPosition().toPoint())

    def mouseMoveEvent(self, e):
        if self._drag is None:
            return
        y0, n0 = self._drag
        k = 1000.0 if e.modifiers() & Qt.KeyboardModifier.ControlModifier else 180.0
        t = max(0.0, min(1.0, n0 + (y0 - e.position().y()) / k))
        if self.spec.kind in ("int", "choice", "bool"):
            v = self.spec.value(t)
            if v == self.get():
                return
        self.set_norm(t)

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            self._drag = None
            self.released_.emit()

    def mouseDoubleClickEvent(self, e):
        self.pressed_.emit()
        self.setv(self.spec.default)
        self.moved.emit(float(self.spec.default))
        self.update()
        hint_to(self, self.text())
        self.released_.emit()

    def wheelEvent(self, e):
        d = 1 if e.angleDelta().y() > 0 else -1
        if self.spec.kind in ("int", "choice", "bool"):
            v = int(self.get()) + d
            v = max(int(self.spec.lo), min(int(self.spec.hi), v))
            self.pressed_.emit()
            self.setv(v)
            self.moved.emit(float(v))
            self.update()
            hint_to(self, self.text())
            self.released_.emit()
        else:
            self.pressed_.emit()
            self.set_norm(self.norm() + d * (0.01 if e.modifiers() & Qt.KeyboardModifier.ControlModifier else 0.04))
            self.released_.emit()
        e.accept()

    def _menu(self, gp):
        m = QMenu(self)
        if self.spec.kind == "choice":
            for i, ch in enumerate(self.spec.choices):
                a = m.addAction(("•  " if int(self.get()) == i else "    ") + str(ch))
                a.triggered.connect(lambda _=False, i=i: (self.pressed_.emit(), self.setv(i), self.moved.emit(float(i)),
                                                          self.update(), self.released_.emit()))
            m.addSeparator()
        m.addAction("Сбросить", lambda: self.mouseDoubleClickEvent(None))
        m.addAction("Ввести значение…", self._enter)
        m.exec(gp)

    def _enter(self):
        from PyQt6.QtWidgets import QInputDialog
        v, ok = QInputDialog.getText(self, self.spec.name, f"{self.spec.name} ({self.spec.lo}…{self.spec.hi}):",
                                     text=str(round(float(self.get()), 3)))
        if ok:
            try:
                x = float(v.replace(",", "."))
                x = max(self.spec.lo, min(self.spec.hi, x))
                if self.spec.kind in ("int", "choice", "bool"):
                    x = int(round(x))
                self.pressed_.emit()
                self.setv(x)
                self.moved.emit(float(x))
                self.update()
                self.released_.emit()
            except ValueError:
                pass

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self.ks
        r = QRectF(3, 3, s - 6, s - 6)
        c = r.center()
        rad = r.width() / 2
        t = self.norm()
        # дорожка
        p.setPen(QPen(QColor("#15181a"), max(2.0, s * 0.09), cap=Qt.PenCapStyle.FlatCap))
        p.drawArc(r, -45 * 16, 270 * 16)
        # значение
        col = self.color if self.isEnabled() else DIM
        p.setPen(QPen(col, max(2.0, s * 0.09), cap=Qt.PenCapStyle.FlatCap))
        if self.bipolar:
            t0 = self.spec.norm(0.0)
            a0 = 225 - 270 * t0
            a1 = 225 - 270 * t
            p.drawArc(r, int(min(a0, a1) * 16), int(abs(a1 - a0) * 16))
        else:
            p.drawArc(r, int((225 - 270 * t) * 16), int(270 * t * 16))
        # тело
        ir = r.adjusted(s * 0.12, s * 0.12, -s * 0.12, -s * 0.12)
        g = QRadialGradient(ir.center() - QPointF(ir.width() * 0.2, ir.height() * 0.25), ir.width() * 0.8)
        g.setColorAt(0, QColor("#6a737b"))
        g.setColorAt(1, QColor("#2c3237"))
        p.setBrush(g)
        p.setPen(QPen(QColor("#1a1d20"), 1))
        p.drawEllipse(ir)
        a = math.radians(225 - 270 * t)
        p.setPen(QPen(QColor("#f2f4f5"), max(1.5, s * 0.07), cap=Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(c.x() + math.cos(a) * rad * 0.18, c.y() - math.sin(a) * rad * 0.18),
                   QPointF(c.x() + math.cos(a) * rad * 0.62, c.y() - math.sin(a) * rad * 0.62))
        if self.label:
            p.setPen(DIM)
            p.setFont(font(10))
            p.drawText(QRectF(-10, s - 1, s + 20, 14), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                       self.label)
        p.end()


# ------------------------------------------------------------------ #
#  Фейдер и индикатор                                                 #
# ------------------------------------------------------------------ #

def gain_to_pos(g: float) -> float:
    if g <= 1e-4:
        return 0.0
    d = 20 * math.log10(g)
    return max(0.0, min(1.0, (d + 48.0) / 54.0))


def pos_to_gain(t: float) -> float:
    if t <= 0.002:
        return 0.0
    return 10 ** ((t * 54.0 - 48.0) / 20.0)


class Fader(QWidget):
    """Вертикальный фейдер громкости (0 дБ — двойной щелчок)."""
    moved = pyqtSignal(float)
    pressed_ = pyqtSignal()
    released_ = pyqtSignal()

    def __init__(self, get, setv, parent=None, w=22, h=120):
        super().__init__(parent)
        self.get, self.setv = get, setv
        self.setFixedSize(w, h)
        self._drag = None
        self.setCursor(Qt.CursorShape.SizeVerCursor)

    def text(self):
        g = float(self.get())
        return "Громкость: " + ("−∞ дБ" if g <= 1e-4 else f"{20 * math.log10(g):+.1f} дБ")

    def enterEvent(self, e):
        hint_to(self, self.text())

    def _y2t(self, y):
        top, bot = 8, self.height() - 8
        return max(0.0, min(1.0, (bot - y) / (bot - top)))

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.pressed_.emit()
            self._drag = (e.position().y(), gain_to_pos(float(self.get())))

    def mouseMoveEvent(self, e):
        if self._drag is None:
            return
        y0, t0 = self._drag
        k = 4.0 if e.modifiers() & Qt.KeyboardModifier.ControlModifier else 1.0
        t = max(0.0, min(1.0, t0 + (y0 - e.position().y()) / ((self.height() - 16) * k)))
        g = pos_to_gain(t)
        self.setv(g)
        self.moved.emit(g)
        self.update()
        hint_to(self, self.text())

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            self._drag = None
            self.released_.emit()

    def mouseDoubleClickEvent(self, e):
        self.pressed_.emit()
        self.setv(1.0)
        self.moved.emit(1.0)
        self.update()
        self.released_.emit()

    def wheelEvent(self, e):
        d = 1 if e.angleDelta().y() > 0 else -1
        self.pressed_.emit()
        t = gain_to_pos(float(self.get())) + d * 0.02
        g = pos_to_gain(max(0.0, min(1.0, t)))
        self.setv(g)
        self.moved.emit(g)
        self.update()
        hint_to(self, self.text())
        self.released_.emit()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        top, bot = 8, h - 8
        cx = w / 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#121416"))
        p.drawRoundedRect(QRectF(cx - 2, top, 4, bot - top), 2, 2)
        p.setPen(QPen(QColor("#5b646c"), 1))
        for dbv in (6, 0, -6, -12, -24, -36):
            y = bot - (dbv + 48) / 54 * (bot - top)
            p.drawLine(QPointF(cx - 7 if dbv == 0 else cx - 5, y), QPointF(cx - 3, y))
        t = gain_to_pos(float(self.get()))
        y = bot - t * (bot - top)
        r = QRectF(cx - w * 0.42, y - 6, w * 0.84, 12)
        g = QLinearGradient(r.topLeft(), r.bottomLeft())
        g.setColorAt(0, QColor("#9aa3aa"))
        g.setColorAt(0.5, QColor("#5d666d"))
        g.setColorAt(1, QColor("#3a4146"))
        p.setBrush(g)
        p.setPen(QPen(QColor("#202427"), 1))
        p.drawRoundedRect(r, 2, 2)
        p.setPen(QPen(ACC, 1.5))
        p.drawLine(QPointF(r.left() + 2, y), QPointF(r.right() - 2, y))
        p.end()


class Meter(QWidget):
    """Стерео-индикатор пиков с удержанием."""

    def __init__(self, parent=None, w=10, h=120, stereo=True):
        super().__init__(parent)
        self.setFixedSize(w, h)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)   # закрашивает себя целиком — родителя не трогать
        self.v = [0.0, 0.0]
        self.hold = [0.0, 0.0]
        self.stereo = stereo

    @staticmethod
    def _pos(a):
        if a <= 1e-5:
            return 0.0
        return max(0.0, min(1.0, (20 * math.log10(a) + 54) / 60))

    def set(self, l, r):
        now=time.perf_counter();dt=max(0.,min(.2,now-getattr(self,'_last_meter',now-1/30)))
        self._last_meter=now
        changed = False
        for i, a in enumerate((float(l), float(r))):
            t = self._pos(a)
            nv = max(t, self.v[i] - 1.05*dt)
            if abs(nv - self.v[i]) > 1e-3:
                changed = True
            self.v[i] = nv
            if t >= self.hold[i]:
                self.hold[i] = t
            else:
                self.hold[i] = max(0.0, self.hold[i] - .18*dt)
        if changed or any(h > 0 for h in self.hold):
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor("#111315"))
        n = 2 if self.stereo else 1
        bw = (w - 1) / n
        for i in range(n):
            x = int(i * bw) + 1
            t = self.v[i]
            hh = int((h - 2) * t)
            g = QLinearGradient(0, h, 0, 0)
            g.setColorAt(0.0, QColor("#4fb34f"))
            g.setColorAt(0.75, QColor("#c8d64a"))
            g.setColorAt(0.9, QColor("#f0a33a"))
            g.setColorAt(1.0, QColor("#e8574f"))
            p.fillRect(QRect(x, h - 1 - hh, int(bw) - 1, hh), QBrush(g))
            hy = h - 1 - int((h - 2) * self.hold[i])
            if self.hold[i] > 0.01:
                p.fillRect(QRect(x, hy, int(bw) - 1, 1), QColor("#e8eef2"))
        p.end()


class Led(QAbstractButton):
    """Маленький круглый переключатель (включён — зелёный)."""

    def __init__(self, tip="", parent=None, color=None, size=12):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(size + 4, size + 4)
        self.col = color or GRN
        self._tip = tip
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def enterEvent(self, e):
        hint_to(self, self._tip)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(2, 2, self.width() - 4, self.height() - 4)
        on = self.isChecked()
        g = QRadialGradient(r.center() - QPointF(1.5, 1.5), r.width() * 0.7)
        if on:
            g.setColorAt(0, self.col.lighter(150))
            g.setColorAt(1, self.col.darker(110))
        else:
            g.setColorAt(0, QColor("#4a5156"))
            g.setColorAt(1, QColor("#22272a"))
        p.setBrush(g)
        p.setPen(QPen(QColor("#141618"), 1))
        p.drawEllipse(r)
        p.end()


# ------------------------------------------------------------------ #
#  Поле числа (темп) и табло времени                                  #
# ------------------------------------------------------------------ #

class NumBox(QWidget):
    """Число в стиле табло: тянуть вверх/вниз, колесо, двойной щелчок — ввести."""
    changed = pyqtSignal(float)

    def __init__(self, get, setv, lo, hi, step=1.0, fmt="{:.3f}", tip="", parent=None, w=74, h=24, color=None):
        super().__init__(parent)
        self.get, self.setv = get, setv
        self.lo, self.hi, self.step, self.fmt = lo, hi, step, fmt
        self._tip = tip
        self.color = color or ACC
        self.setFixedSize(w, h)
        self._drag = None
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.edit = None

    def enterEvent(self, e):
        hint_to(self, self._tip)

    def _set(self, v):
        v = max(self.lo, min(self.hi, v))
        self.setv(v)
        self.changed.emit(v)
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = (e.position().y(), float(self.get()))

    def mouseMoveEvent(self, e):
        if self._drag is None:
            return
        y0, v0 = self._drag
        fine = bool(e.modifiers() & Qt.KeyboardModifier.ControlModifier)
        dv = (y0 - e.position().y()) / (8.0 if not fine else 30.0) * self.step
        self._set(round((v0 + dv) / (self.step * (0.1 if fine else 1))) * self.step * (0.1 if fine else 1))

    def mouseReleaseEvent(self, e):
        self._drag = None

    def wheelEvent(self, e):
        d = 1 if e.angleDelta().y() > 0 else -1
        self._set(float(self.get()) + d * self.step * (0.1 if e.modifiers() & Qt.KeyboardModifier.ControlModifier else 1))

    def mouseDoubleClickEvent(self, e):
        self.edit = QLineEdit(self)
        self.edit.setGeometry(self.rect())
        self.edit.setText(self.fmt.format(float(self.get())))
        self.edit.selectAll()
        self.edit.show()
        self.edit.setFocus()

        def done():
            ed, self.edit = self.edit, None
            if ed is None:
                return
            try:
                self._set(float(ed.text().replace(",", ".")))
            except ValueError:
                pass
            ed.deleteLater()
        self.edit.editingFinished.connect(done)

    def paintEvent(self, e):
        p = QPainter(self)
        if getattr(self, "bg", None) is not None:
            p.fillRect(self.rect(), self.bg)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(QColor("#15181a"))
        p.setPen(QPen(QColor("#3e464c"), 1))
        p.drawRoundedRect(r, 3, 3)
        p.setPen(self.color)
        p.setFont(font(15, True, mono=True))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.fmt.format(float(self.get())))
        p.end()


class Lcd(QWidget):
    """Табло: текст моноширинным шрифтом на тёмном фоне."""
    clicked = pyqtSignal()

    def __init__(self, parent=None, w=110, h=24, color=None, px=15, tip=""):
        super().__init__(parent)
        self.setFixedSize(w, h)
        self.text = ""
        self.sub = ""
        self.color = color or ACC
        self.px = px
        self._tip = tip
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.bg = QColor("#363d43")                       # цвет под скруглёнными углами (панель сверху)

    def enterEvent(self, e):
        hint_to(self, self._tip)

    def set_text(self, t, sub=""):
        if t != self.text or sub != self.sub:
            self.text, self.sub = t, sub
            self.update()

    def mousePressEvent(self, e):
        self.clicked.emit()

    def paintEvent(self, e):
        p = QPainter(self)
        if getattr(self, "bg", None) is not None:
            p.fillRect(self.rect(), self.bg)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(QColor("#15181a"))
        p.setPen(QPen(QColor("#3e464c"), 1))
        p.drawRoundedRect(r, 3, 3)
        p.setPen(self.color)
        p.setFont(font(self.px, True, mono=True))
        if self.sub:
            p.drawText(r.adjusted(0, 0, 0, -6), Qt.AlignmentFlag.AlignCenter, self.text)
            p.setPen(DIM)
            p.setFont(font(9))
            p.drawText(r.adjusted(0, 0, 0, -1), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom, self.sub)
        else:
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.text)
        p.end()


class Scope(QWidget):
    """Мини-осциллограф выхода (как в верхней панели студийных программ)."""

    def __init__(self, parent=None, w=90, h=24):
        super().__init__(parent)
        self.setFixedSize(w, h)
        self.data = None
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.bg = QColor("#363d43")

    def set_data(self, a):
        self.data = a
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), self.bg)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(QColor("#15181a"))
        p.setPen(QPen(QColor("#3e464c"), 1))
        p.drawRoundedRect(r, 3, 3)
        d = self.data
        if d is not None and len(d) > 2:
            n = len(d)
            w, h = self.width() - 4, self.height() - 4
            path = QPainterPath()
            step = max(1, n // w)
            for i in range(0, n, step):
                x = 2 + i / n * w
                y = 2 + h / 2 - max(-1.0, min(1.0, float(d[i]) * 1.5)) * h / 2
                if i == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            p.setPen(QPen(GRN, 1.2))
            p.drawPath(path)
        p.end()


# ------------------------------------------------------------------ #
#  Плавающее окно рабочей области                                     #
# ------------------------------------------------------------------ #

class DawWindow(QFrame):
    """Окно внутри рабочей области: заголовок (тащить — двигать, двойной щелчок — развернуть),
    края — менять размер, крестик — скрыть. Щелчок в любом месте окна поднимает его наверх."""
    closed = pyqtSignal()
    geometry_changed = pyqtSignal()
    activated = pyqtSignal(object)
    TITLE_H = 24
    EDGE = 6

    def __init__(self, key: str, title: str, icon: str, content: QWidget, parent=None, toolbar: QWidget | None = None):
        super().__init__(parent)
        self.key = key
        self.title = title
        self.icon = icon
        self.content = content
        self.setObjectName("DawWindow")
        self.setMouseTracking(True)
        self.setMinimumSize(220, 120)
        # окно закрашивает себя целиком (углы — цветом фона под ними): обновления внутри окна не
        # перерисовывают рабочую область и другие окна под ним
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(3, self.TITLE_H + 1, 3, 3)
        lay.setSpacing(0)
        if toolbar is not None:
            lay.addWidget(toolbar)
        lay.addWidget(content, 1)
        self.btn_close = IconButton("close", "Закрыть окно", 18, self)
        self.btn_max = IconButton("max", "Развернуть / вернуть", 18, self)
        self.btn_close.clicked.connect(self.close_win)
        self.btn_max.clicked.connect(self.toggle_max)
        self._mode = None
        self._press = None
        self._normal_geo = None
        self.active = False
        self._watch(content)
        if toolbar is not None:
            self._watch(toolbar)

    def _watch(self, w):
        """Фильтр событий на окно и всех потомков: щелчок — поднять окно; новые дети — тоже следить."""
        try:
            w.installEventFilter(self)
            for ch in w.findChildren(QWidget):
                ch.installEventFilter(self)
        except RuntimeError:
            pass

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QEvent.Type.MouseButtonPress:
            self.raise_()
            self.activated.emit(self)
        elif t == QEvent.Type.ChildPolished:                 # ребёнок уже собран целиком
            ch = ev.child()
            if ch is not None and ch.isWidgetType():
                self._watch(ch)
        return False

    def set_title(self, t):
        self.title = t
        self.update()

    def close_win(self):
        self.hide()
        self.closed.emit()

    def toggle_max(self):
        par = self.parentWidget()
        if par is None:
            return
        if self._normal_geo is not None and self.geometry() == par.rect():
            self.setGeometry(self._normal_geo)
            self._normal_geo = None
        else:
            self._normal_geo = self.geometry()
            self.setGeometry(par.rect())
        self.geometry_changed.emit()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        y = (self.TITLE_H - 18) // 2 + 1
        self.btn_close.move(self.width() - 22, y)
        self.btn_max.move(self.width() - 42, y)

    def _hit(self, pos):
        x, y = pos.x(), pos.y()
        w, h = self.width(), self.height()
        E = self.EDGE
        l, r, b = x < E, x > w - E, y > h - E
        t = y < 3
        if (r and b):
            return "rb"
        if (l and b):
            return "lb"
        if r:
            return "r"
        if l:
            return "l"
        if b:
            return "b"
        if t:
            return "t"
        if y < self.TITLE_H:
            return "move"
        return None

    def mousePressEvent(self, e):
        self.raise_()
        self.activated.emit(self)
        if e.button() == Qt.MouseButton.LeftButton:
            m = self._hit(e.position().toPoint())
            if m:
                self._mode = m
                self._press = (e.globalPosition().toPoint(), self.geometry())

    def mouseMoveEvent(self, e):
        if self._mode is None:
            m = self._hit(e.position().toPoint())
            cur = {"rb": Qt.CursorShape.SizeFDiagCursor, "lb": Qt.CursorShape.SizeBDiagCursor,
                   "r": Qt.CursorShape.SizeHorCursor, "l": Qt.CursorShape.SizeHorCursor,
                   "b": Qt.CursorShape.SizeVerCursor, "t": Qt.CursorShape.SizeVerCursor}.get(m)
            if cur:
                self.setCursor(cur)
            else:
                self.unsetCursor()
            return
        g0p, geo = self._press
        d = e.globalPosition().toPoint() - g0p
        par = self.parentWidget().rect() if self.parentWidget() else QRect(0, 0, 4000, 4000)
        g = QRect(geo)
        if self._mode == "move":
            g.moveTo(geo.topLeft() + d)
            x = max(-g.width() + 80, min(par.width() - 80, g.x()))
            y = max(0, min(par.height() - self.TITLE_H, g.y()))
            g.moveTo(x, y)
        else:
            mw, mh = self.minimumWidth(), self.minimumHeight()
            if "r" in self._mode:
                g.setWidth(max(mw, geo.width() + d.x()))
            if "b" in self._mode:
                g.setHeight(max(mh, geo.height() + d.y()))
            if "l" in self._mode:
                nl = min(geo.right() - mw, geo.left() + d.x())
                g.setLeft(nl)
            if self._mode == "t":
                nt = min(geo.bottom() - mh, max(0, geo.top() + d.y()))
                g.setTop(nt)
        self.setGeometry(g)

    def mouseReleaseEvent(self, e):
        if self._mode is not None:
            self._mode = None
            self.geometry_changed.emit()

    def mouseDoubleClickEvent(self, e):
        if e.position().y() < self.TITLE_H:
            self.toggle_max()

    def paintEvent(self, e):
        p = QPainter(self)
        par = self.parentWidget()
        if par is not None and hasattr(par, "bg_color_at"):
            y0 = self.y()
            for cy in (0, self.height() - 5):
                c = par.bg_color_at(y0 + cy)
                p.fillRect(QRectF(0, cy, 5, 5), c)
                p.fillRect(QRectF(self.width() - 5, cy, 5, 5), c)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(BG1)
        p.setPen(QPen(QColor("#5a646c") if self.active else QColor("#3c444a"), 1))
        p.drawRoundedRect(r, 4, 4)
        tr = QRectF(1, 1, self.width() - 2, self.TITLE_H)
        g = QLinearGradient(tr.topLeft(), tr.bottomLeft())
        g.setColorAt(0, QColor("#4a535b") if self.active else QColor("#3d454b"))
        g.setColorAt(1, QColor("#363d43") if self.active else QColor("#30363b"))
        path = QPainterPath()
        path.addRoundedRect(tr, 4, 4)
        p.fillPath(path, g)
        draw_icon(p, self.icon, QRectF(7, 5, 15, 15), ACC if self.active else DIM)
        p.setPen(TEXT if self.active else DIM)
        p.setFont(font(12, True))
        p.drawText(QRectF(28, 0, self.width() - 80, self.TITLE_H), Qt.AlignmentFlag.AlignVCenter, self.title)
        p.end()


class Toolbar(QWidget):
    """Полоса под заголовком окна: кнопки и выпадающие списки слева направо."""

    def __init__(self, parent=None, h=30):
        super().__init__(parent)
        self.setFixedHeight(h)
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(6, 3, 6, 3)
        self.lay.setSpacing(4)
        self.setAutoFillBackground(False)

    def add(self, w, stretch=0):
        self.lay.addWidget(w, stretch)
        return w

    def space(self, n=8):
        self.lay.addSpacing(n)

    def stretch(self):
        self.lay.addStretch(1)

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#2f353a"))
        p.setPen(QColor("#22272b"))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        p.end()


def label(text, px=12, bold=False, color=None) -> QLabel:
    lb = QLabel(text)
    lb.setFont(font(px, bold))
    lb.setStyleSheet(f"color: {(color or DIM).name()}; background: transparent;")
    return lb
