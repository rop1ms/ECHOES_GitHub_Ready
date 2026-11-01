# theme_studio.py
"""
Конструктор тем ECHOES: окно редактора и расстановка слоёв прямо на плеере.

Всё, что меняется, сразу видно в плеере (живой предпросмотр), а сохраняется по
кнопке «Сохранить». Есть отмена/повтор (Ctrl+Z / Ctrl+Y), шаблоны с живыми
миниатюрами, «Удиви меня», палитра из картинки или обложки, гармонии цветов,
проверка контраста, свои шрифты файлом, импорт/экспорт темы одним файлом
.echoestheme (вместе со всеми картинками).

Расстановка слоёв (LayerCanvas) — прозрачный слой поверх окна плеера: слои
таскаются мышью, тянутся за углы (размер), за кружок (поворот), крутятся
колесом; картинки и GIF можно бросить на окно прямо из проводника.
"""
from __future__ import annotations

import copy
import json
import math
import re
import shutil
import uuid
from pathlib import Path

from PyQt6.QtCore import QEvent, QPointF, QRect, QRectF, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import (QColor, QDesktopServices, QFont, QFontDatabase, QIcon, QImage, QImageReader,
                         QKeySequence, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient,
                         QShortcut)
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QColorDialog, QComboBox,
                             QFileDialog, QFontComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit, QPushButton,
                             QRadioButton, QScrollArea, QSlider, QSplitter, QStackedWidget, QTabBar, QTabWidget,
                             QTextBrowser, QToolButton, QVBoxLayout, QWidget)

import dsl_layout as DL
import layer_fx
import theme_kit as tk
import theme_layers as tl

try:                                                       # имена из шаблонов — на языке интерфейса
    import i18n as _i18n
    _tr = _i18n.tr
except Exception:                                          # noqa: BLE001
    def _tr(s):
        return s
tk.TR = _tr

# ------------------------------------------------------------------ #
#  Оформление самого конструктора (не зависит от темы плеера)         #
# ------------------------------------------------------------------ #

_BG, _PANEL, _PANEL2, _LINE = "#0c0d12", "#14161f", "#1c1f2b", "#2a2e3e"
_TEXT, _MUTED, _ACC, _ACC2 = "#eef0f6", "#8d93a5", "#8b7bff", "#38d6ff"

STUDIO_QSS = f"""
QWidget#Studio {{ background: {_BG}; }}
QWidget {{ color: {_TEXT}; font-family: "Segoe UI Variable", "Segoe UI", "Inter", "Helvetica Neue", sans-serif;
          font-size: 13px; }}
QLabel {{ background: transparent; }}
QLabel#H1 {{ font-size: 19px; font-weight: 800; }}
QLabel#H2 {{ font-size: 11px; font-weight: 700; color: {_MUTED}; }}
QLabel#Hint {{ color: {_MUTED}; font-size: 11px; }}
QLabel#Status {{ color: {_MUTED}; font-size: 12px; }}
QLabel#Good {{ color: #5ee6a0; font-size: 12px; }}
QLabel#Warn {{ color: #ffb454; font-size: 12px; }}
QFrame#Card {{ background: {_PANEL}; border: 1px solid {_LINE}; border-radius: 14px; }}
QFrame#Header, QFrame#Footer {{ background: {_PANEL}; border: none; }}
QFrame#Header {{ border-bottom: 1px solid {_LINE}; }}
QFrame#Footer {{ border-top: 1px solid {_LINE}; }}
QListWidget#Nav {{ background: {_PANEL}; border: none; border-right: 1px solid {_LINE}; outline: none; padding: 8px 4px; }}
QListWidget#Nav::item {{ padding: 9px 10px; margin: 2px 4px; border-radius: 10px; color: {_MUTED}; }}
QListWidget#Nav::item:hover {{ background: {_PANEL2}; color: {_TEXT}; }}
QListWidget#Nav::item:selected {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3a3170, stop:1 #1b2a40);
                                  color: {_TEXT}; font-weight: 700; }}
QListWidget#Layers {{ background: {_PANEL2}; border: 1px solid {_LINE}; border-radius: 10px; outline: none; padding: 4px; }}
QListWidget#Layers::item {{ padding: 7px 8px; border-radius: 8px; }}
QListWidget#Layers::item:selected {{ background: #2d2a55; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {_LINE}; border-radius: 3px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ height: 0; }}
QPushButton {{ background: {_PANEL2}; border: 1px solid {_LINE}; border-radius: 10px; padding: 7px 12px; }}
QPushButton:hover {{ border-color: {_ACC}; }}
QPushButton:pressed {{ background: {_LINE}; }}
QPushButton:checked {{ background: #2d2a55; border-color: {_ACC}; }}
QPushButton:disabled {{ color: #555a6a; border-color: {_PANEL2}; }}
QPushButton#Primary {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {_ACC}, stop:1 {_ACC2});
                       color: #0b0b12; border: none; font-weight: 800; padding: 9px 16px; }}
QPushButton#Primary:hover {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #a194ff, stop:1 #6fe2ff); }}
QPushButton#Primary:checked {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ffb454, stop:1 #ff6b9a); }}
QPushButton#Danger {{ color: #ff7a8a; }}
QPushButton#Tool {{ padding: 5px 9px; min-width: 22px; }}
QFrame#ModeBar {{ background: {_PANEL}; border: none; border-bottom: 1px solid {_LINE}; }}
QFrame#ModeBar[lyrics="true"] {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3a1f3a, stop:1 #1b2236); }}
QPushButton#Seg {{ border-radius: 9px; padding: 6px 12px; font-weight: 600; color: {_MUTED}; }}
QPushButton#Seg:checked {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {_ACC}, stop:1 {_ACC2});
                          color: #0b0b12; border: none; font-weight: 800; }}
QToolButton {{ background: {_PANEL2}; border: 1px solid {_LINE}; border-radius: 12px; padding: 6px; color: {_TEXT}; }}
QToolButton:hover {{ border-color: {_ACC}; }}
QToolButton:checked {{ border: 2px solid {_ACC}; background: #231f45; }}
QLineEdit, QPlainTextEdit, QComboBox, QFontComboBox {{ background: {_PANEL2}; border: 1px solid {_LINE}; border-radius: 9px;
    padding: 6px 9px; selection-background-color: {_ACC}; }}
QLineEdit:focus, QPlainTextEdit:focus {{ border-color: {_ACC}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {_PANEL2}; border: 1px solid {_LINE}; selection-background-color: #2d2a55;
    outline: none; }}
QSlider::groove:horizontal {{ height: 4px; background: {_LINE}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {_ACC}, stop:1 {_ACC2});
    border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 14px; height: 14px; margin: -5px 0; background: {_TEXT}; border-radius: 7px; }}
QCheckBox, QRadioButton {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 6px; border: 1px solid {_LINE}; background: {_PANEL2}; }}
QCheckBox::indicator:checked {{ background: {_ACC}; border-color: {_ACC}; }}
QRadioButton::indicator {{ width: 16px; height: 16px; border-radius: 8px; border: 1px solid {_LINE}; background: {_PANEL2}; }}
QRadioButton::indicator:checked {{ background: {_ACC}; border-color: {_ACC}; }}
QMenu {{ background: {_PANEL2}; border: 1px solid {_LINE}; padding: 6px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: #2d2a55; }}
QToolTip {{ background: {_PANEL2}; color: {_TEXT}; border: 1px solid {_LINE}; padding: 6px; }}
QTabWidget#EditorTabs::pane {{ border: none; }}
QTabWidget#EditorTabs QTabBar::tab {{ background: {_PANEL}; color: {_MUTED}; padding: 7px 14px; border: none;
    border-bottom: 2px solid transparent; }}
QTabWidget#EditorTabs QTabBar::tab:selected {{ color: {_TEXT}; border-bottom: 2px solid {_ACC}; background: {_PANEL2}; }}
QTabWidget#EditorTabs QTabBar::tab:hover {{ color: {_TEXT}; }}
"""

IMAGE_FILTER = "Картинки и анимации (*.png *.jpg *.jpeg *.gif *.webp *.bmp)"
VIDEO_FILTER = "Видео (*.mp4 *.webm *.mkv *.mov *.avi *.m4v)"
FONT_FILTER = "Шрифты (*.ttf *.otf *.ttc)"
ECHO_FILTER = "Темы LOOM / EchoScript (*.echo)"
_IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_VID_EXT = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v"}
_FONT_EXT = {".ttf", ".otf", ".ttc"}

WEIGHTS = [(300, "Тонкий"), (400, "Обычный"), (500, "Средний"), (600, "Полужирный"), (700, "Жирный"),
           (800, "Очень жирный"), (900, "Чёрный")]

GRADIENT_PRESETS = [
    ("Закат", [[0, "#ff5f6d"], [0.5, "#ffc371"], [1, "#2b1055"]]),
    ("Океан", [[0, "#00c6ff"], [0.6, "#0072ff"], [1, "#021b3a"]]),
    ("Мята", [[0, "#a8ff78"], [1, "#0f3443"]]),
    ("Неон", [[0, "#f72585"], [0.5, "#7209b7"], [1, "#4cc9f0"]]),
    ("Персик", [[0, "#ffecd2"], [1, "#fcb69f"]]),
    ("Ночь", [[0, "#1c1236"], [0.55, "#0b0d18"], [1, "#050608"]]),
]


def _qc(c, alpha=None) -> QColor:
    q = QColor(c or "#000000")
    if alpha is not None:
        q.setAlphaF(max(0.0, min(1.0, alpha)))
    return q


def _rgba_color(s: str) -> QColor:
    """'rgba(r,g,b,a)' или '#hex' → QColor."""
    s = (s or "").strip()
    if s.startswith("rgba"):
        try:
            r, g, b, a = (float(x) for x in s[s.index("(") + 1:s.index(")")].split(","))
            c = QColor(int(r), int(g), int(b))
            c.setAlphaF(max(0.0, min(1.0, a)))
            return c
        except ValueError:
            return QColor(0, 0, 0, 0)
    return QColor(s)


# ------------------------------------------------------------------ #
#  Маленькие виджеты                                                  #
# ------------------------------------------------------------------ #

class ColorButton(QPushButton):
    """Образец цвета: клик — выбор (есть пипетка «с экрана»), правый клик — скопировать/вставить/сбросить."""

    changed = pyqtSignal(str)

    def __init__(self, color="", allow_empty=False, empty_text="авто", parent=None):
        super().__init__(parent)
        self.allow_empty = allow_empty
        self.empty_text = empty_text
        self._color = ""
        self.setFixedSize(52, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.set_color(color)
        self.clicked.connect(self._pick)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)

    def color(self) -> str:
        return self._color

    def set_color(self, c):
        self._color = c or ""
        self.setToolTip((self._color.upper() if self._color else self.empty_text) +
                        "\nКлик — выбрать цвет, правый клик — копировать / вставить")
        self.update()

    def _emit(self, c):
        self.set_color(c)
        self.changed.emit(self._color)

    def _pick(self):
        start = QColor(self._color) if self._color else QColor("#ffffff")
        c = QColorDialog.getColor(start, self.window(), _tr("Цвет"))
        if c.isValid():
            self._emit(c.name())

    def _menu(self, pos):
        m = QMenu(self)
        m.addAction("Копировать цвет", lambda: QApplication.clipboard().setText(self._color or ""))
        clip = (QApplication.clipboard().text() or "").strip()
        if not clip.startswith("#") and len(clip) in (6, 8):
            clip = "#" + clip
        a = m.addAction(f"Вставить цвет {clip[:9]}", lambda: self._emit(QColor(clip).name()))
        a.setEnabled(QColor.isValidColorName(clip))
        if self.allow_empty:
            m.addAction(f"Сбросить ({self.empty_text})", lambda: self._emit(""))
        m.exec(self.mapToGlobal(pos))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        hov = self.underMouse()
        if self._color:
            p.setPen(QPen(QColor(_ACC) if hov else QColor(255, 255, 255, 60), 1.5))
            p.setBrush(QColor(self._color))
            p.drawRoundedRect(r, 8, 8)
        else:
            p.setPen(QPen(QColor(_ACC) if hov else QColor(_LINE), 1.2, Qt.PenStyle.DashLine))
            p.setBrush(QColor(_PANEL2))
            p.drawRoundedRect(r, 8, 8)
            p.setPen(QColor(_MUTED))
            f = p.font()
            f.setPixelSize(10)
            p.setFont(f)
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.empty_text)
        p.end()


class SliderRow(QWidget):
    """Подпись + ползунок + значение. Двойной клик по подписи — значение по умолчанию."""

    changed = pyqtSignal(float)
    released = pyqtSignal()

    def __init__(self, label, lo, hi, value, step=0.01, fmt="{:.2f}", default=None, tip="", parent=None):
        super().__init__(parent)
        self.lo, self.hi, self.step, self.fmt = lo, hi, step, fmt
        self.default = value if default is None else default
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.lbl = QLabel(label)
        self.lbl.setMinimumWidth(118)
        if tip:
            self.lbl.setToolTip(tip)
            self.setToolTip(tip)
        self.sl = QSlider(Qt.Orientation.Horizontal)
        self.sl.setRange(0, max(1, int(round((hi - lo) / step))))
        self.val = QLabel()
        self.val.setMinimumWidth(48)
        self.val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.val.setObjectName("Hint")
        lay.addWidget(self.lbl)
        lay.addWidget(self.sl, 1)
        lay.addWidget(self.val)
        self.set_value(value)
        self.sl.valueChanged.connect(self._on)
        self.sl.sliderReleased.connect(self.released.emit)
        self.lbl.mouseDoubleClickEvent = lambda _e: self._reset()

    def _reset(self):
        self.set_value(self.default)
        self.changed.emit(self.value())
        self.released.emit()

    def value(self) -> float:
        return self.lo + self.sl.value() * self.step

    def set_value(self, v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            v = self.lo
        self.sl.blockSignals(True)
        self.sl.setValue(int(round((max(self.lo, min(self.hi, v)) - self.lo) / self.step)))
        self.sl.blockSignals(False)
        self.val.setText(self.fmt.format(self.value()))

    def _on(self, _v):
        self.val.setText(self.fmt.format(self.value()))
        self.changed.emit(self.value())


class CardGrid(QWidget):
    """Плитки-варианты с картинкой и подписью (один выбор)."""

    chosen = pyqtSignal(object)

    def __init__(self, options, cols=3, icon=QSize(88, 54), parent=None):
        super().__init__(parent)
        self._btn = {}
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(8)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        for i, (value, text, pm, tip) in enumerate(options):
            b = QToolButton()
            b.setCheckable(True)
            b.setText(text)
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            b.setIconSize(icon)
            if pm is not None:
                b.setIcon(QIcon(pm))
            if tip:
                b.setToolTip(tip)
            b.setMinimumWidth(icon.width() + 16)
            b.clicked.connect(lambda _=False, v=value: self.chosen.emit(v))
            self.group.addButton(b)
            grid.addWidget(b, i // cols, i % cols)
            self._btn[value] = b
        for c in range(cols):
            grid.setColumnStretch(c, 1)

    def set_value(self, v):
        b = self._btn.get(v)
        if b is not None:
            b.setChecked(True)

    def set_icon(self, value, pm):
        b = self._btn.get(value)
        if b is not None and pm is not None:
            b.setIcon(QIcon(pm))


def _card(title=None, hint=None):
    f = QFrame()
    f.setObjectName("Card")
    v = QVBoxLayout(f)
    v.setContentsMargins(14, 12, 14, 14)
    v.setSpacing(9)
    if title:
        h = QLabel(_tr(title).upper())
        h.setObjectName("H2")
        v.addWidget(h)
    if hint:
        lb = QLabel(hint)
        lb.setObjectName("Hint")
        lb.setWordWrap(True)
        v.addWidget(lb)
    return f, v


def _row(*widgets, stretch_last=False, spacing=8):
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(spacing)
    for x in widgets:
        if x is None:
            h.addStretch(1)
        elif isinstance(x, int):
            h.addSpacing(x)
        else:
            h.addWidget(x)
    if stretch_last:
        h.addStretch(1)
    return w


# ------------------------------------------------------------------ #
#  Превью: миниатюры тем и значки вариантов                           #
# ------------------------------------------------------------------ #

class _StaticMedia:
    animated = False
    frame_no = 0

    def __init__(self, path):
        self.path = path
        r = QImageReader(path)
        r.setAutoTransform(True)
        img = r.read()
        if not img.isNull() and max(img.width(), img.height()) > 800:
            img = img.scaled(800, 800, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.pix = QPixmap.fromImage(img) if not img.isNull() else QPixmap()

    def valid(self):
        return not self.pix.isNull()

    def size(self):
        return self.pix.size()


class _MiniRT:
    """Мини-«рантайм» для превью вне окна плеера (галерея, значки)."""

    font_family = tl.ThemeRuntime.font_family
    theme_font = tl.ThemeRuntime.theme_font

    def __init__(self, theme=None, store=None):
        self.pulse = tl.AudioPulse(None)
        self.dpr = 1.0
        self.theme = theme
        self.store = store
        self._media = {}
        self._fonts = {}
        self.painter = tl.LayerPainter(self)

    def asset(self, rel):
        if not rel or self.store is None or self.theme is None:
            return ""
        return self.store.asset(self.theme, rel)

    def media(self, rel):
        path = self.asset(rel)
        if not path:
            return None
        m = self._media.get(path)
        if m is None:
            m = self._media[path] = _StaticMedia(path)
        return m


def _live_frame(kind, theme, w, h) -> QImage:
    b = theme["background"]
    lw = tl.LiveWallpaper(kind, theme["palette"]["bg"], b.get("colors"), 1.0, 0.0, _MiniRT())
    lw.resize(w, h)
    lw.t = 7.0
    lw.prewarm()
    lw.step(1 / 30)
    return lw.img.copy()


def _paint_bg(p: QPainter, t, store, w, h, kind=None):
    """Фон темы для миниатюры (тип можно подменить — для значков вариантов)."""
    b, pal = t["background"], t["palette"]
    ty = kind or b["type"]
    full = QRectF(0, 0, w, h)
    p.fillRect(full, _qc(pal["bg"]))
    if ty == "color":
        p.fillRect(full, _qc(b.get("color") or pal["bg"]))
    elif ty == "glow":
        g = QRadialGradient(w * 0.15, 0, 0.75 * math.hypot(w, h))
        c = _qc(pal.get("glow") or pal["accent2"])
        c0 = QColor(c); c0.setAlpha(230)
        c1 = QColor(c); c1.setAlpha(112)
        g.setColorAt(0, c0); g.setColorAt(0.28, c1); g.setColorAt(0.6, QColor(0, 0, 0, 204)); g.setColorAt(1, _qc(pal["bg"]))
        p.fillRect(full, g)
    elif ty == "gradient":
        gd = b["gradient"]
        a = math.radians(gd.get("angle", 135))
        cx, cy = w / 2, h / 2
        if gd.get("kind") == "radial":
            g = QRadialGradient(cx, cy, math.hypot(w, h) * 0.6)
        elif gd.get("kind") == "conical":
            from PyQt6.QtGui import QConicalGradient
            g = QConicalGradient(cx, cy, -gd.get("angle", 135))
        else:
            d = (abs(w * math.cos(a)) + abs(h * math.sin(a))) / 2
            g = QLinearGradient(cx - math.cos(a) * d, cy - math.sin(a) * d, cx + math.cos(a) * d, cy + math.sin(a) * d)
        for pos, col in gd["stops"]:
            g.setColorAt(pos, _qc(col))
        if gd.get("kind") == "conical" and gd["stops"]:
            g.setColorAt(1.0, _qc(gd["stops"][0][1]))
        p.fillRect(full, g)
    elif ty in tk.LIVE_BACKGROUNDS:
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(full, _live_frame(ty, t, w, h))
    elif ty == "media" and store is not None and b.get("media") and kind is None:
        path = store.asset(t, b["media"])
        img = QImageReader(path).read() if path else QImage()
        if not img.isNull():
            r = tl.fit_rect(img.size(), w, h, b.get("fit", "cover") if b.get("fit") != "tile" else "cover",
                            b.get("zoom", 1.0), b.get("align_x", 0.5), b.get("align_y", 0.5))
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawImage(r, img)
        else:
            _glyph(p, "image", w, h, pal)
    elif ty == "media":
        _glyph(p, "image", w, h, pal)
    elif ty == "video":
        _glyph(p, "video", w, h, pal)
    elif ty == "cover":
        g = QLinearGradient(0, 0, w, h)
        g.setColorAt(0, _qc(pal["accent"]).darker(220))
        g.setColorAt(1, _qc(pal["accent2"]).darker(300))
        p.fillRect(full, g)
        _glyph(p, "cover", w, h, pal)


def _glyph(p, kind, w, h, pal):
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    ink = QColor(255, 255, 255, 170)
    p.setPen(QPen(ink, max(1.2, h * 0.035)))
    p.setBrush(Qt.BrushStyle.NoBrush)
    cx, cy, s = w / 2, h / 2, min(w, h) * 0.32
    if kind == "image":
        r = QRectF(cx - s * 1.2, cy - s * 0.85, s * 2.4, s * 1.7)
        p.drawRoundedRect(r, 4, 4)
        path = QPainterPath()
        path.moveTo(r.left() + 3, r.bottom() - 3)
        path.lineTo(r.left() + r.width() * 0.38, r.top() + r.height() * 0.45)
        path.lineTo(r.left() + r.width() * 0.6, r.bottom() - r.height() * 0.3)
        path.lineTo(r.left() + r.width() * 0.75, r.top() + r.height() * 0.6)
        path.lineTo(r.right() - 3, r.bottom() - 3)
        p.drawPath(path)
        p.setBrush(ink)
        p.drawEllipse(QPointF(r.right() - r.width() * 0.22, r.top() + r.height() * 0.28), s * 0.13, s * 0.13)
    elif kind == "video":
        r = QRectF(cx - s * 1.25, cy - s * 0.8, s * 2.5, s * 1.6)
        p.drawRoundedRect(r, 4, 4)
        tri = QPainterPath()
        tri.moveTo(cx - s * 0.3, cy - s * 0.42)
        tri.lineTo(cx + s * 0.45, cy)
        tri.lineTo(cx - s * 0.3, cy + s * 0.42)
        tri.closeSubpath()
        p.setBrush(ink)
        p.drawPath(tri)
    else:                                                  # обложка: квадрат + пластинка
        p.drawRoundedRect(QRectF(cx - s * 1.2, cy - s * 0.8, s * 1.6, s * 1.6), 3, 3)
        p.setBrush(QColor(0, 0, 0, 160))
        p.drawEllipse(QPointF(cx + s * 0.55, cy), s * 0.78, s * 0.78)
        p.setBrush(_qc(pal["accent"]))
        p.drawEllipse(QPointF(cx + s * 0.55, cy), s * 0.22, s * 0.22)
    p.restore()


def theme_thumbnail(theme, store=None, w=232, h=145) -> QPixmap:
    """Схематичная, но узнаваемая миниатюра темы: фон, стекло панелей, диск, визуализатор, слои."""
    t = tk.normalize(theme)
    t["id"] = theme.get("id", "")
    pal = tk.resolved_palette(t)
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    _paint_bg(p, t, store, w, h)
    b = t["background"]
    if b.get("dim", 0) > 0.01 and b["type"] not in ("color", "glow", "gradient"):
        p.fillRect(QRectF(0, 0, w, h), QColor(0, 0, 0, int(255 * b["dim"] * 0.9)))
    if b.get("tint_amount", 0) > 0.01:
        p.fillRect(QRectF(0, 0, w, h), _qc(b.get("tint"), b["tint_amount"] * 0.85))
    if b.get("vignette", 0) > 0.01:
        g = QRadialGradient(w / 2, h / 2, math.hypot(w, h) * 0.62)
        g.setColorAt(0.45, QColor(0, 0, 0, 0))
        g.setColorAt(1, QColor(0, 0, 0, int(255 * b["vignette"])))
        p.fillRect(QRectF(0, 0, w, h), g)
    c = t["components"]
    k = h / 800.0
    s = t["shape"]
    rad = max(2.0, s["radius"] * k * 1.6)
    m = max(3.0, s["margin"] * k * 1.6)
    top = 10.0
    pw = w * 0.21
    glass = _rgba_color(pal["glass2"])
    border = _rgba_color(pal["border"])
    panels = []
    if c["panels"]["left"]:
        panels.append(QRectF(m, top + m, pw, h - top - 2 * m))
    if c["panels"]["right"]:
        panels.append(QRectF(w - m - pw, top + m, pw, h - top - 2 * m))
    if c["panels"]["swap"]:
        panels = [QRectF(w - r.right(), r.y(), r.width(), r.height()) for r in panels]
    for r in panels:
        if s.get("shadow", 0) > 0.05:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, int(60 * s["shadow"])))
            p.drawRoundedRect(r.translated(0, 2), rad, rad)
        p.setPen(QPen(border, max(0.6, s["border"] * 0.7)) if s["border"] else Qt.PenStyle.NoPen)
        p.setBrush(glass)
        p.drawRoundedRect(r, rad, rad)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_qc(pal["text"], 0.18))
        for i in range(4):
            p.drawRoundedRect(QRectF(r.x() + 5, r.y() + 10 + i * 11, r.width() - 10, 6), 3, 3)
        p.setBrush(_qc(pal["accent"]))
        p.drawRoundedRect(QRectF(r.x() + 5, r.bottom() - 14, r.width() - 10, 7), 3.5, 3.5)
    # центр: пластинка, визуализатор, кнопка
    cx = w / 2
    vn = c["vinyl"]
    if vn["style"] != "hidden":
        rr = h * 0.2 * vn.get("scale", 1.0)
        cy = h * 0.38
        if vn["style"] == "graphic":
            p.setBrush(QColor(240, 238, 230) if not tk.is_light(t["palette"]["bg"]) else QColor(20, 20, 24))
            p.drawEllipse(QPointF(cx, cy), rr, rr)
            p.setBrush(_qc(pal["accent"]))
            p.drawEllipse(QPointF(cx, cy), rr * 0.32, rr * 0.32)
        else:
            g = QRadialGradient(cx, cy, rr)
            g.setColorAt(0, QColor(40, 40, 46)); g.setColorAt(1, QColor(8, 8, 10))
            p.setBrush(g)
            p.drawEllipse(QPointF(cx, cy), rr, rr)
            p.setBrush(_qc(pal["accent2"]))
            p.drawEllipse(QPointF(cx, cy), rr * 0.33, rr * 0.33)
    v = c["visualizer"]
    if v["style"] != "none":
        c1, c2 = _qc(v.get("color1") or pal["accent"]), _qc(v.get("color2") or pal["accent2"])
        g = QLinearGradient(0, h * 0.75, 0, h * 0.62)
        g.setColorAt(0, c2); g.setColorAt(1, c1)
        p.setBrush(g)
        x0, x1 = w * 0.32, w * 0.68
        n = 18
        bw = (x1 - x0) / n
        for i in range(n):
            vh = h * (0.03 + 0.09 * abs(math.sin(i * 0.9 + 0.6)) * (1 - i / n * 0.5))
            p.drawRect(QRectF(x0 + i * bw, h * 0.75 - vh, bw * 0.7, vh))
    play = _qc(pal["accent"] if c["play"].get("style") == "accent" else pal["text"])
    p.setBrush(play)
    p.drawEllipse(QPointF(cx, h * 0.86), h * 0.05, h * 0.05)
    # слои
    if t["layers"] and store is not None:
        rt = _MiniRT(t, store)
        for z in ("back", "front"):
            for i, L in enumerate(t["layers"]):
                if L["z"] == z and L.get("visible", True):
                    try:
                        geo = rt.painter.geometry(L, w, h, 0.0, rt.pulse, i, still=True)
                        rt.painter.paint(p, L, geo, w, h)
                    except Exception:                      # noqa: BLE001
                        pass
    # частицы
    pt = t["effects"]["particles"]
    if pt["kind"] != "none":
        col = _qc(pt.get("color") or "#ffffff", 0.85)
        p.setBrush(col)
        for i in range(14):
            x = (math.sin(i * 12.9898) * 43758.5453) % 1.0
            y = (math.sin(i * 78.233) * 12345.678) % 1.0
            p.drawEllipse(QPointF(abs(x) * w, abs(y) * h), 1.6, 1.6)
    # заголовок
    if c["titlebar"].get("line", True):
        g = QLinearGradient(0, 0, w, 0)
        g.setColorAt(0, _qc(pal["accent"], 0)); g.setColorAt(0.3, _qc(pal["accent"], 0.9))
        g.setColorAt(0.7, _qc(pal["accent2"], 0.9)); g.setColorAt(1, _qc(pal["accent2"], 0))
        p.fillRect(QRectF(0, top - 1.5, w, 1.5), g)
    p.end()
    pm = QPixmap.fromImage(img)
    out = QPixmap(w, h)
    out.fill(Qt.GlobalColor.transparent)
    q = QPainter(out)
    q.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, w, h), 10, 10)
    q.setClipPath(path)
    q.drawPixmap(0, 0, pm)
    q.end()
    return out


def bg_icon(kind, theme, w=92, h=58) -> QPixmap:
    t = tk.normalize(theme)
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, w, h), 8, 8)
    p.setClipPath(path)
    _paint_bg(p, t, None, w, h, kind)
    p.end()
    return QPixmap.fromImage(img)


def particle_icon(kind, color="#ffffff", w=92, h=58) -> QPixmap:
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor("#11131b"))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if kind != "none":
        rt = _MiniRT()
        pt = tl.Particles({"kind": kind, "count": 9, "size": 0.9 if kind != "dust" else 2.2, "color": color,
                           "speed": 1.0, "react": 0.0, "image": ""},
                          {"accent": _ACC, "accent2": _ACC2}, rt)
        if kind == "image":
            p.setPen(QPen(QColor(255, 255, 255, 150), 1.4))
            _glyph(p, "image", w, h, {"accent": _ACC})
        else:
            pt.paint(p, w, h)
    else:
        p.setPen(QPen(QColor(255, 255, 255, 90), 2))
        p.drawLine(QPointF(w * 0.4, h * 0.3), QPointF(w * 0.6, h * 0.7))
        p.drawLine(QPointF(w * 0.6, h * 0.3), QPointF(w * 0.4, h * 0.7))
    p.end()
    out = QPixmap(w, h)
    out.fill(Qt.GlobalColor.transparent)
    q = QPainter(out)
    q.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, w, h), 8, 8)
    q.setClipPath(path)
    q.drawImage(0, 0, img)
    q.end()
    return out


def viz_icon(style, c1=_ACC, c2=_ACC2, w=92, h=58) -> QPixmap:
    pm = QPixmap(w, h)
    pm.fill(QColor("#11131b"))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    n = 12
    vals = [0.35 + 0.6 * abs(math.sin(i * 0.8 + 0.5)) * (1 - i / n * 0.4) for i in range(n)]
    bw = (w - 12) / n
    g = QLinearGradient(0, h, 0, 0)
    g.setColorAt(0, QColor(c2)); g.setColorAt(1, QColor(c1))
    p.setPen(Qt.PenStyle.NoPen)
    if style in ("bars", "mirror", "blocks"):
        p.setBrush(g)
        for i, v in enumerate(vals):
            x = 6 + i * bw
            if style == "bars":
                p.drawRect(QRectF(x, h - 6 - v * (h - 14), bw * 0.72, v * (h - 14)))
            elif style == "mirror":
                vh = v * (h / 2 - 6)
                p.drawRect(QRectF(x, h / 2 - vh, bw * 0.72, vh * 2))
            else:
                k = int(v * 7)
                for j in range(k):
                    p.drawRect(QRectF(x, h - 6 - (j + 1) * 6 + 2, bw * 0.72, 4))
    elif style == "dots":
        p.setBrush(g)
        for i, v in enumerate(vals):
            p.drawEllipse(QPointF(6 + i * bw + bw * 0.36, h - 8 - v * (h - 18)), 2.6, 2.6)
    elif style in ("line", "wave"):
        path = QPainterPath()
        pts = [QPointF(6 + i * bw + bw * 0.36, h - 6 - v * (h - 16)) for i, v in enumerate(vals)]
        path.moveTo(pts[0])
        for a, b in zip(pts, pts[1:]):
            mid = (a + b) / 2
            path.quadTo(a, mid)
        path.lineTo(pts[-1])
        if style == "wave":
            fill = QPainterPath(path)
            fill.lineTo(pts[-1].x(), h)
            fill.lineTo(pts[0].x(), h)
            fill.closeSubpath()
            cc = QColor(c1); cc.setAlpha(110)
            p.fillPath(fill, cc)
        p.setPen(QPen(QColor(c1), 2.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
    else:
        p.setPen(QPen(QColor(255, 255, 255, 90), 2))
        p.drawLine(QPointF(w * 0.4, h * 0.3), QPointF(w * 0.6, h * 0.7))
        p.drawLine(QPointF(w * 0.6, h * 0.3), QPointF(w * 0.4, h * 0.7))
    p.end()
    return pm


def vinyl_icon(style, w=92, h=58) -> QPixmap:
    pm = QPixmap(w, h)
    pm.fill(QColor("#11131b"))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    cx, cy, r = w / 2, h / 2, h * 0.4
    if style == "real":
        g = QRadialGradient(cx, cy, r)
        g.setColorAt(0, QColor(50, 50, 58)); g.setColorAt(1, QColor(6, 6, 8))
        p.setBrush(g)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx, cy), r, r)
        p.setPen(QPen(QColor(255, 255, 255, 25), 1))
        for k in (0.55, 0.7, 0.85):
            p.drawEllipse(QPointF(cx, cy), r * k, r * k)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_ACC))
        p.drawEllipse(QPointF(cx, cy), r * 0.3, r * 0.3)
    elif style == "graphic":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(242, 240, 232))
        p.drawEllipse(QPointF(cx, cy), r, r)
        p.setBrush(QColor(20, 20, 20))
        for i in range(28):
            a = i / 28 * 2 * math.pi
            for rr in (0.5, 0.68, 0.86):
                p.drawEllipse(QPointF(cx + math.cos(a) * r * rr, cy + math.sin(a) * r * rr), 1.0, 1.0)
        p.drawEllipse(QPointF(cx, cy), r * 0.22, r * 0.22)
    else:
        p.setPen(QPen(QColor(255, 255, 255, 70), 1.5, Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(cx, cy), r, r)
    p.end()
    return pm


def button_icon(style, w=92, h=58) -> QPixmap:
    pm = QPixmap(w, h)
    pm.fill(QColor("#11131b"))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = QRectF(12, h / 2 - 12, w - 24, 24)
    if style == "winamp":                                   # серая объёмная кнопка Winamp 2
        g = QLinearGradient(r.topLeft(), r.bottomLeft())
        g.setColorAt(0, QColor("#dcdce6"))
        g.setColorAt(1, QColor("#8f8fa3"))
        p.fillRect(r, g)
        p.setPen(QPen(QColor("#f6f6fc"), 1))
        p.drawLine(r.topLeft(), r.topRight())
        p.drawLine(r.topLeft(), r.bottomLeft())
        p.setPen(QPen(QColor("#3c3c4c"), 1))
        p.drawLine(r.bottomLeft(), r.bottomRight())
        p.drawLine(r.topRight(), r.bottomRight())
        p.setPen(QColor("#10101a"))
        f = p.font()
        f.setPixelSize(11)
        f.setBold(True)
        p.setFont(f)
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, "PLAY")
        p.end()
        return pm
    if style == "solid":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#2b2f40"))
    elif style == "outline":
        p.setPen(QPen(QColor(255, 255, 255, 170), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
    elif style == "neon":
        for wdt, a in ((7, 40), (4, 80)):
            p.setPen(QPen(QColor(139, 123, 255, a), wdt))
            p.drawRoundedRect(r, 9, 9)
        p.setPen(QPen(QColor(_ACC), 1.6))
        p.setBrush(Qt.BrushStyle.NoBrush)
    elif style == "flat":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(Qt.BrushStyle.NoBrush)
    else:
        p.setPen(QPen(QColor(255, 255, 255, 50), 1))
        p.setBrush(QColor(255, 255, 255, 22))
    p.drawRoundedRect(r, 9, 9)
    p.setPen(QColor(_ACC) if style == "neon" else QColor(235, 238, 245))
    f = p.font()
    f.setPixelSize(11)
    f.setBold(True)
    p.setFont(f)
    p.drawText(r, Qt.AlignmentFlag.AlignCenter, "Play")
    p.end()
    return pm


# ------------------------------------------------------------------ #
#  Расстановка слоёв прямо на окне плеера                             #
# ------------------------------------------------------------------ #

class LayerCanvas(QWidget):
    """Прозрачный слой поверх плеера: любые слои (картинки, GIF, видео, текст, фигуры, виджеты —
    кнопки, перемотка, пластинка, спектр…, эффекты тем) выбираются и двигаются мышью.
    Углы — размер (Shift — свободно по осям), середины сторон — растяжение по одной оси,
    кружок — поворот, Alt+колесо — 3D-наклон. Ctrl+клик — выделить несколько, Ctrl+G — группа."""

    HANDLE = 9.0
    ROT_GAP = 26.0
    SNAP = 7.0

    def __init__(self, studio):
        host = studio.win
        super().__init__(host)
        self.studio = studio
        self.host = host
        self.setProperty("echoesEditor", True)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.grid = False
        self._drag = None
        self._guides = []
        self._hover = None
        self._editor = None
        self.bar = self._build_bar()
        host.installEventFilter(self)
        self._follow()
        self.show()
        self.raise_()
        self.setFocus()

    # ── панель инструментов ── #

    def _build_bar(self):
        bar = QFrame(self)
        bar.setObjectName("CanvasBar")
        bar.setStyleSheet(STUDIO_QSS + "QFrame#CanvasBar{background:rgba(14,15,22,0.94);"
                                       "border:1px solid #2f3446;border-radius:14px;}")
        outer = QVBoxLayout(bar)                           # узкое окно — кнопки в два ряда (см. _place_bar)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(6)
        self._bar_rows = (QHBoxLayout(), QHBoxLayout())
        for r in self._bar_rows:
            r.setSpacing(6)
            outer.addLayout(r)
        self._bar_items = []
        self._bar_split = None
        st = self.studio

        class _Lay:                                        # кнопки копятся в список, ряды раскладывает _place_bar
            @staticmethod
            def addWidget(w):
                self._bar_items.append(w)
        lay = _Lay()

        def btn(text, tip, fn=None, menu=None):
            b = QPushButton(text)
            b.setObjectName("Tool")
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            if fn is not None:
                b.clicked.connect(fn)
            if menu is not None:
                b.clicked.connect(lambda _=False, b=b, m=menu: m().exec(b.mapToGlobal(b.rect().bottomLeft())))
            lay.addWidget(b)
            return b
        btn("+ Текст", "Новая надпись в центре", lambda: st.add_layer("text"))
        btn("+ Фигура", "Круг, звезда, сердце…", lambda: st.add_layer("shape"))
        btn("+ Медиа ▾", "Картинка, GIF или видео (или просто перетащите файлы на окно)", menu=self._media_menu)
        btn("+ Плеер ▾", "Настоящие элементы плеера: винил, визуализатор, кнопки, библиотека, плейлисты…",
            menu=lambda: st.native_menu(self))
        btn("+ Виджет ▾", "Кнопки, перемотка, громкость, обложка, текст песни, спектры…",
            menu=lambda: st.widget_menu(self))
        btn("+ Кнопка…", "Нарисовать свою кнопку: форма, рисунок, надпись, символ, картинка",
            lambda: st.open_button_editor())
        btn("+ Эффект ▾", "MilkDrop, жидкость Fluid, живые обои и наборы из знаменитых тем",
            menu=lambda: st.effect_menu(self))
        btn("↶", "Отменить (Ctrl+Z)", st.undo)
        btn("↷", "Повторить (Ctrl+Y)", st.redo)
        g = QPushButton("Сетка")
        g.setObjectName("Tool")
        g.setCheckable(True)
        g.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        g.toggled.connect(self._set_grid)
        lay.addWidget(g)
        el = QPushButton("Элементы")
        el.setObjectName("Tool")
        el.setCheckable(True)
        el.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        el.setToolTip("Показать рамки всех элементов интерфейса: пластинка, визуализатор, кнопки, панели… — "
                      "их можно двигать, тянуть и убирать (правая кнопка)")
        el.toggled.connect(lambda on: (setattr(self, "show_natives", bool(on)), self.update()))
        lay.addWidget(el)
        done = QPushButton("Готово")
        done.setObjectName("Primary")
        done.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        done.setToolTip("Закончить расстановку (Esc)")
        done.clicked.connect(lambda: st.set_canvas(False))
        lay.addWidget(done)
        for w in self._bar_items:
            w.setParent(bar)
        return bar

    def _arrange_bar(self, split):
        """Кнопки панели — в один ряд (split=None) или в два (первые split штук — сверху)."""
        key = split if split is not None else -1
        if key == self._bar_split:
            return
        self._bar_split = key
        for r in self._bar_rows:
            while r.count():
                r.takeAt(0)
        for n, w in enumerate(self._bar_items):
            (self._bar_rows[1] if split is not None and n >= split else self._bar_rows[0]).addWidget(w)
        for r in self._bar_rows:
            r.addStretch(0)

    def _media_menu(self):
        st = self.studio
        m = QMenu(self)
        m.setStyleSheet(STUDIO_QSS)
        m.addAction("Картинка / GIF…", lambda: st.add_layer("media"))
        m.addAction("Видео…", lambda: st.add_layer("video"))
        return m

    def _set_grid(self, on):
        self.grid = bool(on)
        self.update()

    def _place_bar(self):
        one = sum(w.sizeHint().width() for w in self._bar_items) + 6 * (len(self._bar_items) - 1) + 18
        self._arrange_bar(None if one <= self.width() - 16 else (len(self._bar_items) + 1) // 2)
        self.bar.adjustSize()
        self.bar.move(max(8, (self.width() - self.bar.width()) // 2), 44)
        self.bar.raise_()

    def eventFilter(self, obj, ev):
        if obj is self.host and ev.type() == QEvent.Type.Resize:
            QTimer.singleShot(0, self._follow)
        return False

    def _area(self):
        """Где лежат слои темы (окно плеера без встроенной панели конструктора)."""
        try:
            bd = self.studio.win._custom_runtime().backdrop
            if bd is not None and bd.width() > 2:
                return bd.geometry()
        except Exception:                                  # noqa: BLE001
            pass
        return self.host.rect()

    def _follow(self):
        try:
            self.setGeometry(self._area())
            self._place_bar()
            self.raise_()
        except RuntimeError:
            pass

    def close_canvas(self):
        try:
            self.host.removeEventFilter(self)
        except RuntimeError:
            pass
        if self._editor is not None:
            self._editor.deleteLater()
        self.hide()
        self.deleteLater()

    # ── геометрия ── #

    def _rt(self):
        return self.studio.win._custom_runtime()

    def _layers(self):
        return self.studio.t["layers"]

    def _geo(self, L, i):
        """Геометрия слоя без анимаций и поведения (рамка не прыгает вместе с «парением» и т.п.)."""
        rt = self._rt()
        return rt.painter.geometry(L, self.width(), self.height(), 0.0, rt.pulse, i, still=True)

    @staticmethod
    def _corners(geo):
        w, h = geo["w"] * abs(geo["sx"]), geo["h"] * abs(geo["sy"])
        a = math.radians(geo["rot"])
        ca, sa = math.cos(a), math.sin(a)
        pts = []
        for dx, dy in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)):
            pts.append(QPointF(geo["cx"] + dx * ca - dy * sa, geo["cy"] + dx * sa + dy * ca))
        return pts

    @staticmethod
    def _mids(pts):
        """Середины сторон: верх, право, низ, лево."""
        return [(pts[0] + pts[1]) / 2, (pts[1] + pts[2]) / 2, (pts[2] + pts[3]) / 2, (pts[3] + pts[0]) / 2]

    def _rot_handle(self, geo):
        h = geo["h"] * abs(geo["sy"])
        a = math.radians(geo["rot"])
        d = h / 2 + self.ROT_GAP
        return QPointF(geo["cx"] + d * math.sin(a), geo["cy"] - d * math.cos(a))

    # ── родные элементы интерфейса (пластинка, кнопки, панели…) ── #

    def _nat(self):
        try:
            return self.studio.win.native_layout()
        except Exception:                                  # noqa: BLE001
            return None

    def _native_rects(self):
        """[(ключ, название, прямоугольник на холсте)] всех видимых родных элементов + фон пластинки."""
        nl = self._nat()
        out = []
        if nl is None:
            return out
        off = self.geometry().topLeft()
        for key, title, _g, _w in nl.items():
            r = nl.rect_in_window(key)
            if r is None or r.width() < 3 or r.height() < 3:
                continue
            out.append((key, title, QRectF(r).translated(-off.x(), -off.y())))
        vin = nl.widget("vinyl")
        try:
            if vin is not None and vin.isVisibleTo(self.host) and \
                    self.studio.t["components"]["vinyl"]["bg"].get("on", True):
                tl = vin.mapTo(self.host, vin.rect().topLeft()) - off
                out.append(("vinyl_bg", _tr("Фон пластинки с обложкой"), vin.bg_rect().translated(tl.x(), tl.y())))
        except RuntimeError:
            pass
        return out

    def _native_rect(self, key):
        for k, _t, r in self._native_rects():
            if k == key:
                return r
        return None

    @staticmethod
    def _rect_handles(r: QRectF):
        """Ручки прямоугольника: (что тянуть, точка) — углы и середины сторон."""
        c = r.center()
        return [("tl", r.topLeft()), ("tr", r.topRight()), ("br", r.bottomRight()), ("bl", r.bottomLeft()),
                ("t", QPointF(c.x(), r.top())), ("r", QPointF(r.right(), c.y())),
                ("b", QPointF(c.x(), r.bottom())), ("l", QPointF(r.left(), c.y()))]

    def _apply_native(self, key, r: QRectF):
        st = self.studio
        if key == "vinyl_bg":
            vin = self._nat().widget("vinyl")
            vr = self._native_rect("vinyl")
            if vin is None or vr is None:
                return
            base = vin._disc_radius() * 1.35
            st.set_vinyl_bg({"dx": round((r.center().x() - vr.center().x()) / max(1.0, vr.width()), 4),
                             "dy": round((r.center().y() - vr.center().y()) / max(1.0, vr.height()), 4),
                             "scale": round(max(0.3, min(3.0, r.width() / 2 / max(1.0, base))), 3)}, commit=False)
            return
        off = self.geometry().topLeft()
        spec = self._nat().frac_from_window(r.translated(off.x(), off.y()))
        spec.update(free=True, hidden=False)
        st.set_native(key, spec, commit=False)

    def _candidates(self, pos):
        """Всё, что лежит под курсором, сверху вниз: слои «поверх», родные элементы (вынутые и мелкие —
        выше крупных), слои «на фоне». id слоя или «native:ключ»."""
        layers = self._layers()
        P = QPointF(pos)
        out = []
        for i, L in [(i, L) for i, L in enumerate(layers) if L["z"] == "front"][::-1]:
            if L.get("visible", True) and tl.LayerPainter.contains(self._geo(L, i), P, 3.0):
                out.append(L["id"])
        nl = self._nat()
        rects = self._native_rects()
        nat = []
        ring = False
        for key, _t, r in rects:
            if key == "vinyl_bg":
                vin = nl.widget("vinyl") if nl is not None else None
                vr = next((rr for k, _t2, rr in rects if k == "vinyl"), None)
                if vin is None or vr is None:
                    continue
                dc = math.hypot(P.x() - vr.center().x(), P.y() - vr.center().y())
                db = math.hypot(P.x() - r.center().x(), P.y() - r.center().y())
                ring = db <= r.width() / 2 and dc > vin._disc_radius() * 1.02
                continue
            if r.contains(P):
                nat.append(((0 if nl is not None and nl.is_free(key) else 1, r.width() * r.height()), key))
        nat.sort(key=lambda x: x[0])
        keys = [k for _a, k in nat]
        if ring:                                           # кольцо фона вокруг диска — над пластинкой/областью
            j = next((n for n, k in enumerate(keys) if k in ("vinyl", "stage", "left", "right")), len(keys))
            keys.insert(j, "vinyl_bg")
        out += ["native:" + k for k in keys]
        for i, L in [(i, L) for i, L in enumerate(layers) if L["z"] == "back"][::-1]:
            if L.get("visible", True) and tl.LayerPainter.contains(self._geo(L, i), P, 3.0):
                out.append(L["id"])
        return out

    def _current(self):
        """Что сейчас выбрано: id слоя или «native:ключ» (None — ничего)."""
        st = self.studio
        if st._nsel:
            return "native:" + st._nsel
        return st._sel

    def _hit(self, pos):
        """(id слоя, что схватили: move/scale/rotate/stretch_x/stretch_y) под курсором.
        Родные элементы — id «native:ключ», что: n_move или n_<ручка>.
        Если под курсором есть уже выбранный элемент — берётся он, а не тот, что лежит поверх
        (выбрали нижнюю кнопку двойным кликом — её и тащим)."""
        sel = self.studio.selected_layer()
        layers = self._layers()
        P = QPointF(pos)
        nsel = self.studio._nsel
        if nsel:
            r = self._native_rect(nsel)
            if r is not None:
                for what, pt in self._rect_handles(r):
                    if nsel == "vinyl_bg" and len(what) == 1:
                        continue                           # фон пластинки — только целиком (круг)
                    if (P - pt).manhattanLength() <= self.HANDLE + 4:
                        return "native:" + nsel, "n_" + what
        if sel is not None:
            i = layers.index(sel)
            geo = self._geo(sel, i)
            if (P - self._rot_handle(geo)).manhattanLength() <= self.HANDLE + 4:
                return sel["id"], "rotate"
            pts = self._corners(geo)
            for c in pts:
                if (P - c).manhattanLength() <= self.HANDLE + 4:
                    return sel["id"], "scale"
            for k, c in enumerate(self._mids(pts)):
                if (P - c).manhattanLength() <= self.HANDLE + 3:
                    return sel["id"], ("stretch_y", "stretch_x")[k % 2]
        cands = self._candidates(pos)
        if not cands:
            return None, None
        chosen = set(self.studio.selection_ids())
        cur = self._current()
        if cur:
            chosen.add(cur)
        pick = next((c for c in cands if c in chosen), cands[0])
        return (pick, "n_move") if pick.startswith("native:") else (pick, "move")

    # ── рисование ── #

    def _poly(self, pts):
        poly = QPainterPath()
        poly.moveTo(pts[0])
        for q in pts[1:]:
            poly.lineTo(q)
        poly.closeSubpath()
        return poly

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        p.fillRect(self.rect(), QColor(8, 8, 14, 46))            # лёгкое затемнение — видно, что режим другой
        if self.grid:
            p.setPen(QPen(QColor(255, 255, 255, 34), 1))
            for k in range(1, 12):
                p.drawLine(QPointF(W * k / 12, 0), QPointF(W * k / 12, H))
                p.drawLine(QPointF(0, H * k / 12), QPointF(W, H * k / 12))
            p.setPen(QPen(QColor(255, 255, 255, 70), 1))
            p.drawLine(QPointF(W / 2, 0), QPointF(W / 2, H))
            p.drawLine(QPointF(0, H / 2), QPointF(W, H / 2))
        sel = self.studio.selected_layer()
        many = set(self.studio.selection_ids())
        for i, L in enumerate(self._layers()):
            if L is sel:
                continue
            poly = self._poly(self._corners(self._geo(L, i)))
            hidden = not L.get("visible", True)
            p.setPen(QPen(QColor(0, 0, 0, 120), 3))
            p.drawPath(poly)
            if L["id"] in many:
                p.setPen(QPen(QColor(_ACC), 1.6, Qt.PenStyle.DashLine))
            else:
                p.setPen(QPen(QColor(255, 255, 255, 70 if hidden else 150), 1.2, Qt.PenStyle.DashLine))
            p.drawPath(poly)
            if self._hover == L["id"]:
                p.setPen(QPen(QColor(_ACC2), 1.6))
                p.drawPath(poly)
        if sel is not None:
            i = self._layers().index(sel)
            geo = self._geo(sel, i)
            pts = self._corners(geo)
            poly = self._poly(pts)
            p.setPen(QPen(QColor(0, 0, 0, 150), 4))
            p.drawPath(poly)
            p.setPen(QPen(QColor(_ACC), 2))
            p.drawPath(poly)
            rh = self._rot_handle(geo)
            top_mid = (pts[0] + pts[1]) / 2
            p.drawLine(top_mid, rh)
            p.setBrush(QColor(_TEXT))
            p.drawEllipse(rh, 6, 6)
            locked = sel.get("locked")
            for c in pts:
                p.setBrush(QColor(_TEXT) if not locked else QColor(_MUTED))
                p.setPen(QPen(QColor(_ACC), 1.5))
                p.drawRect(QRectF(c.x() - 5, c.y() - 5, 10, 10))
            for c in self._mids(pts):
                p.setBrush(QColor(_ACC2) if not locked else QColor(_MUTED))
                p.setPen(QPen(QColor(0, 0, 0, 160), 1))
                p.drawEllipse(c, 4.5, 4.5)
            label = f"{sel.get('name') or _tr('Слой')} · {int(round(sel['size'] * 100))}% · {int(round(sel.get('rot', 0)))}°"
            sx_, sy_ = sel.get("stretch_x", 1.0), sel.get("stretch_y", 1.0)
            if abs(sx_ - 1) > 0.005 or abs(sy_ - 1) > 0.005:
                label += f" · ↔{sx_:.2f} ↕{sy_:.2f}"
            if sel.get("tilt_x") or sel.get("tilt_y"):
                label += f" · 3D {int(sel.get('tilt_x', 0))}°/{int(sel.get('tilt_y', 0))}°"
            if sel.get("group"):
                label += f" · {_tr('группа')} {sel['group']}"
            if locked:
                label += _tr(" · закреплён")
            f = p.font()
            f.setPixelSize(12)
            f.setBold(True)
            p.setFont(f)
            tw = p.fontMetrics().horizontalAdvance(label) + 16
            box = QRectF(min(max(4, geo["cx"] - tw / 2), W - tw - 4),
                         min(H - 28, max(pts[0].y(), pts[1].y(), pts[2].y(), pts[3].y()) + 10), tw, 22)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(14, 15, 22, 220))
            p.drawRoundedRect(box, 8, 8)
            p.setPen(QColor(_TEXT))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, label)
        self._paint_natives(p, W, H)
        if self._guides:
            p.setPen(QPen(QColor("#ff4fd8"), 1.2, Qt.PenStyle.DashLine))
            for kind, v in self._guides:
                if kind == "v":
                    p.drawLine(QPointF(v, 0), QPointF(v, H))
                else:
                    p.drawLine(QPointF(0, v), QPointF(W, v))
        hint = ("Тащите слой или элемент (оранжевая рамка) · двойной клик — то, что под ним · углы — размер "
                "(Shift — свободно, Alt — от центра) · стороны — растянуть в эту сторону · кружок — поворот · "
                "колесо — размер, Ctrl — поворот, Alt — 3D · Ctrl+клик — несколько · Enter — изменить · "
                "Del — убрать · Esc — готово")
        f = p.font()
        f.setPixelSize(11)
        f.setBold(False)
        p.setFont(f)
        tw = min(W - 24, p.fontMetrics().horizontalAdvance(hint) + 24)
        box = QRectF((W - tw) / 2, H - 34, tw, 24)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(14, 15, 22, 200))
        p.drawRoundedRect(box, 10, 10)
        p.setPen(QColor(_MUTED))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter,
                   p.fontMetrics().elidedText(hint, Qt.TextElideMode.ElideRight, int(tw - 16)))
        p.end()

    _NAT = "#ffa64d"                                       # цвет рамок родных элементов

    def _paint_natives(self, p, W, H):
        """Рамки родных элементов: выбранный — с ручками, под курсором и вынутые — пунктиром,
        «Элементы» на панели — показать рамки всех."""
        nl = self._nat()
        if nl is None:
            return
        nsel = self.studio._nsel
        show_all = getattr(self, "show_natives", False)
        f = p.font()
        f.setPixelSize(11)
        f.setBold(True)
        p.setFont(f)
        for key, title, r in self._native_rects():
            hov = self._hover == "native:" + key
            free = nl.is_free(key) or key == "vinyl_bg"
            if key == nsel:
                p.setPen(QPen(QColor(0, 0, 0, 160), 4))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(r) if key != "vinyl_bg" else p.drawEllipse(r)
                p.setPen(QPen(QColor(self._NAT), 2))
                p.drawRect(r) if key != "vinyl_bg" else p.drawEllipse(r)
                for what, pt in self._rect_handles(r):
                    if key == "vinyl_bg" and len(what) == 1:
                        continue
                    p.setBrush(QColor(_TEXT))
                    p.setPen(QPen(QColor(self._NAT), 1.5))
                    p.drawRect(QRectF(pt.x() - 5, pt.y() - 5, 10, 10))
                state = _tr("свободно") if nl.is_free(key) else (_tr("в раскладке") if key != "vinyl_bg" else "")
                label = f"{title}" + (f" · {state}" if state else "") + f" · {int(r.width())}×{int(r.height())}"
                tw = p.fontMetrics().horizontalAdvance(label) + 16
                box = QRectF(min(max(4, r.center().x() - tw / 2), W - tw - 4), min(H - 26, r.bottom() + 8), tw, 20)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(40, 24, 8, 230))
                p.drawRoundedRect(box, 7, 7)
                p.setPen(QColor("#ffd9b3"))
                p.drawText(box, Qt.AlignmentFlag.AlignCenter, label)
                continue
            if not (hov or free or show_all):
                continue
            c = QColor(self._NAT)
            c.setAlpha(230 if hov else (150 if free else 90))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(c, 1.6 if hov else 1.0, Qt.PenStyle.SolidLine if hov else Qt.PenStyle.DashLine))
            p.drawRect(r) if key != "vinyl_bg" else p.drawEllipse(r)
            if hov:
                tw = p.fontMetrics().horizontalAdvance(title) + 12
                box = QRectF(r.x(), max(0, r.y() - 20), tw, 18)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(40, 24, 8, 220))
                p.drawRoundedRect(box, 6, 6)
                p.setPen(QColor("#ffd9b3"))
                p.drawText(box, Qt.AlignmentFlag.AlignCenter, title)

    def _native_context(self, m, key):
        st = self.studio
        nl = self._nat()
        if key == "vinyl_bg":
            m.addAction("Фон по центру пластинки", lambda: st.set_vinyl_bg({"dx": 0.0, "dy": 0.0}))
            m.addAction("Размер фона как был", lambda: st.set_vinyl_bg({"scale": 1.0}))
            m.addAction("Убрать фон с обложкой", lambda: st.set_vinyl_bg({"on": False}))
        else:
            if nl is not None and nl.is_free(key):
                m.addAction("Вернуть на место (в раскладку)", lambda: st.set_native(key, {}))
            s = (st.t.get("native") or {}).get(key) or {}
            a = m.addAction("Отдать его место соседям", lambda: st.toggle_native_collapse(key))
            a.setCheckable(True)
            a.setChecked(bool(s.get("collapse")))
            a.setToolTip("Выключено — вынутый или убранный элемент оставляет пустое место, остальные элементы "
                         "не двигаются и не растягиваются")
            m.addAction("Убрать элемент (Del)", lambda: st.hide_native(key))
        m.addSeparator()
        self._hidden_menu(m)

    def _hidden_menu(self, m):
        st = self.studio
        nl = self._nat()
        hidden = [(k, t_) for k, t_, _g, _w in (nl.items() if nl is not None else [])
                  if (st.t.get("native") or {}).get(k, {}).get("hidden")]
        sub = m.addMenu("Убранные элементы")
        sub.setEnabled(bool(hidden) or not st.t["components"]["vinyl"]["bg"].get("on", True))
        for k, t_ in hidden:
            sub.addAction(_tr(t_), lambda k=k: st.show_native(k))
        if not st.t["components"]["vinyl"]["bg"].get("on", True):
            sub.addAction(_tr("Фон пластинки с обложкой"), lambda: st.set_vinyl_bg({"on": True}))
        m.addAction("Все элементы — как было", st.reset_natives)

    # ── мышь ── #

    def mousePressEvent(self, e):
        self.setFocus()
        pos = e.position()
        st = self.studio
        ctrl = bool(e.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if e.button() == Qt.MouseButton.RightButton:
            lid, _ = self._hit(pos)
            if lid and lid.startswith("native:"):
                st.select_native(lid[7:])
            elif lid and lid not in st.selection_ids():
                st.select_layer(lid)
            self._context(e.globalPosition().toPoint())
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if e.modifiers() & Qt.KeyboardModifier.AltModifier:   # Alt+клик — то, что лежит под выбранным
            self._cycle(pos)
            return
        lid, what = self._hit(pos)
        if lid is None:
            if not ctrl:
                st.select_layer(None)
            self.update()
            return
        if lid.startswith("native:"):                      # родной элемент интерфейса
            key = lid[7:]
            st.select_native(key)
            r = self._native_rect(key)
            self._drag = {"native": key, "what": what[2:], "start": QPointF(pos), "r0": QRectF(r)} \
                if r is not None else None
            self.update()
            return
        if ctrl and what == "move":
            st.toggle_multi(lid)
            self.update()
            return
        if what == "move" and lid in st.selection_ids():
            st.set_primary(lid)                            # тащим всё выделенное (группу)
        elif lid != st._sel:
            st.select_layer(lid)
        L = st.selected_layer()
        if L is None or L.get("locked"):
            self.update()
            return
        i = self._layers().index(L)
        geo = self._geo(L, i)
        many = {}
        if what == "move":
            for o in self._layers():
                if o["id"] in st.selection_ids() and o is not L and not o.get("locked"):
                    many[o["id"]] = (o["x"], o["y"])
        self._drag = {"what": what, "id": L["id"], "start": QPointF(pos), "L": dict(L), "geo": geo, "many": many}
        self.update()

    def mouseMoveEvent(self, e):
        pos = e.position()
        d = self._drag
        if d is not None and "native" in d:
            self._drag_native(d, pos, bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier))
            return
        if d is None:
            lid, what = self._hit(pos)
            self._hover = lid
            nc = {"n_move": Qt.CursorShape.SizeAllCursor, "n_tl": Qt.CursorShape.SizeFDiagCursor,
                  "n_br": Qt.CursorShape.SizeFDiagCursor, "n_tr": Qt.CursorShape.SizeBDiagCursor,
                  "n_bl": Qt.CursorShape.SizeBDiagCursor, "n_t": Qt.CursorShape.SizeVerCursor,
                  "n_b": Qt.CursorShape.SizeVerCursor, "n_l": Qt.CursorShape.SizeHorCursor,
                  "n_r": Qt.CursorShape.SizeHorCursor}
            if what in nc:
                self.setCursor(nc[what])
                self.update()
                return
            self.setCursor(Qt.CursorShape.SizeFDiagCursor if what == "scale" else
                           Qt.CursorShape.SizeHorCursor if what == "stretch_x" else
                           Qt.CursorShape.SizeVerCursor if what == "stretch_y" else
                           Qt.CursorShape.CrossCursor if what == "rotate" else
                           Qt.CursorShape.SizeAllCursor if what == "move" else Qt.CursorShape.ArrowCursor)
            self.update()
            return
        L = self.studio.selected_layer()
        if L is None or L["id"] != d["id"]:
            return
        W, H = max(1, self.width()), max(1, self.height())
        geo, L0 = d["geo"], d["L"]
        free = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        c = QPointF(geo["cx"], geo["cy"])
        a = math.radians(geo["rot"])
        self._guides = []
        what = d["what"]
        if what == "move":
            cx = geo["cx"] + pos.x() - d["start"].x()
            cy = geo["cy"] + pos.y() - d["start"].y()
            if not free:
                cx, cy = self._snap(L, cx, cy, geo)
            L["x"] = round(cx / W, 4)
            L["y"] = round(cy / H, 4)
            ddx, ddy = (cx - geo["cx"]) / W, (cy - geo["cy"]) / H
            for o in self._layers():
                if o["id"] in d["many"]:
                    x0, y0 = d["many"][o["id"]]
                    o["x"], o["y"] = round(x0 + ddx, 4), round(y0 + ddy, 4)
        elif what in ("stretch_x", "stretch_y", "scale") and \
                bool(e.modifiers() & Qt.KeyboardModifier.AltModifier):
            # Alt — от центра, в обе стороны сразу (как было раньше)
            def proj(pt, ux, uy):
                return abs((pt.x() - c.x()) * ux + (pt.y() - c.y()) * uy)
            if what == "scale" and not free:
                d0 = max(4.0, math.hypot(d["start"].x() - c.x(), d["start"].y() - c.y()))
                d1 = math.hypot(pos.x() - c.x(), pos.y() - c.y())
                L["size"] = round(max(0.01, min(3.0, L0["size"] * d1 / d0)), 4)
            else:
                axes = (("stretch_x", math.cos(a), math.sin(a)), ("stretch_y", -math.sin(a), math.cos(a)))
                for key, ux, uy in axes:
                    if what != "scale" and key != what:
                        continue
                    d0 = max(4.0, proj(d["start"], ux, uy))
                    L[key] = round(max(0.05, min(20.0, L0.get(key, 1.0) * proj(pos, ux, uy) / d0)), 4)
        elif what in ("stretch_x", "stretch_y", "scale"):
            self._resize_anchored(L, L0, geo, d["start"], pos, what, free, W, H)
        else:
            a0 = math.degrees(math.atan2(d["start"].y() - c.y(), d["start"].x() - c.x()))
            a1 = math.degrees(math.atan2(pos.y() - c.y(), pos.x() - c.x()))
            rot = L0.get("rot", 0.0) + a1 - a0
            rot = (rot + 180) % 360 - 180
            if not free:
                k = round(rot / 15) * 15
                if abs(rot - k) < 4:
                    rot = k
            L["rot"] = round(rot, 2)
        self.studio.layers_changed(commit=False)
        self.update()

    def _resize_anchored(self, L, L0, geo, start, pos, what, free, W, H):
        """Растяжение/размер за ручку, а противоположная сторона (угол) стоит на месте — слой растёт
        только туда, куда тянут. Середина стороны — одна ось; угол — пропорционально (Shift — свободно)."""
        c = QPointF(geo["cx"], geo["cy"])
        a = math.radians(geo["rot"])
        u = QPointF(math.cos(a), math.sin(a))                # ось ширины слоя на экране
        v = QPointF(-math.sin(a), math.cos(a))               # ось высоты
        hw = geo["w"] * abs(geo["sx"]) / 2
        hh = geo["h"] * abs(geo["sy"]) / 2

        def dot(p, q):
            return p.x() * q.x() + p.y() * q.y()
        s0 = start - c
        su = 1.0 if dot(s0, u) >= 0 else -1.0                 # за какую сторону взялись
        sv = 1.0 if dot(s0, v) >= 0 else -1.0
        if what == "stretch_x":
            A = c - u * (su * hw)                            # противоположная сторона — якорь
            new = max(4.0, su * dot(pos - A, u))
            k = new / max(1.0, 2 * hw)
            L["stretch_x"] = round(max(0.05, min(20.0, L0.get("stretch_x", 1.0) * k)), 4)
            new = 2 * hw * L["stretch_x"] / max(1e-6, L0.get("stretch_x", 1.0))
            nc = A + u * (su * new / 2)
        elif what == "stretch_y":
            A = c - v * (sv * hh)
            new = max(4.0, sv * dot(pos - A, v))
            k = new / max(1.0, 2 * hh)
            L["stretch_y"] = round(max(0.05, min(20.0, L0.get("stretch_y", 1.0) * k)), 4)
            new = 2 * hh * L["stretch_y"] / max(1e-6, L0.get("stretch_y", 1.0))
            nc = A + v * (sv * new / 2)
        else:
            A = c - u * (su * hw) - v * (sv * hh)            # противоположный угол — якорь
            lx = max(4.0, su * dot(pos - A, u))
            ly = max(4.0, sv * dot(pos - A, v))
            if free:                                         # Shift — ширина и высота по отдельности
                kx, ky = lx / max(1.0, 2 * hw), ly / max(1.0, 2 * hh)
                L["stretch_x"] = round(max(0.05, min(20.0, L0.get("stretch_x", 1.0) * kx)), 4)
                L["stretch_y"] = round(max(0.05, min(20.0, L0.get("stretch_y", 1.0) * ky)), 4)
                kx = L["stretch_x"] / max(1e-6, L0.get("stretch_x", 1.0))
                ky = L["stretch_y"] / max(1e-6, L0.get("stretch_y", 1.0))
            else:                                            # по диагонали — с пропорциями
                diag = QPointF(su * 2 * hw, sv * 2 * hh)
                dl = max(1.0, dot(diag, diag))
                k = max(0.02, dot(pos - A, u * (su * 2 * hw) + v * (sv * 2 * hh)) / dl)
                L["size"] = round(max(0.01, min(3.0, L0["size"] * k)), 4)
                kx = ky = L["size"] / max(1e-6, L0["size"])
            nc = A + u * (su * hw * kx) + v * (sv * hh * ky)
        L["x"] = round(L0["x"] + (nc.x() - c.x()) / max(1, W), 4)
        L["y"] = round(L0["y"] + (nc.y() - c.y()) / max(1, H), 4)

    def _cycle(self, pos):
        """Выбрать следующий элемент под курсором — тот, что лежит под выбранным (по кругу)."""
        cands = self._candidates(pos)
        if not cands:
            return False
        cur = self._current()
        nxt = cands[(cands.index(cur) + 1) % len(cands)] if cur in cands else cands[0]
        st = self.studio
        if nxt.startswith("native:"):
            st.select_native(nxt[7:])
        else:
            st.select_layer(nxt)
        name = nxt[7:] if nxt.startswith("native:") else ((st.selected_layer() or {}).get("name") or "")
        if nxt.startswith("native:"):
            nl = self._nat()
            name = next((_tr(t_) for k, t_, _g, _w in (nl.items() if nl else []) if k == nxt[7:]), name)
        if len(cands) > 1:
            st.status.setText(_tr("Выбрано: ") + f"{name} ({cands.index(nxt) + 1}/{len(cands)}) · " +
                              _tr("двойной клик — следующий под ним"))
        self.update()
        return True

    def _snap(self, L, cx, cy, geo):
        """Прилипание центра к центру окна, третям, центрам других слоёв; краёв — к краям окна."""
        W, H = self.width(), self.height()
        bw = tl.LayerPainter.bounds(dict(geo, cx=cx, cy=cy), 0.0)
        xs = [(W / 2, W / 2), (W / 3, W / 3), (2 * W / 3, 2 * W / 3)]
        ys = [(H / 2, H / 2), (H / 3, H / 3), (2 * H / 3, 2 * H / 3)]
        skip = set(self.studio.selection_ids())
        for i, o in enumerate(self._layers()):
            if o is L or o["id"] in skip:
                continue
            g = self._geo(o, i)
            xs.append((g["cx"], g["cx"]))
            ys.append((g["cy"], g["cy"]))
        best_x = min(((abs(cx - x), x, gx) for x, gx in xs), default=None)
        if best_x and best_x[0] <= self.SNAP:
            cx = best_x[1]
            self._guides.append(("v", best_x[2]))
        else:
            for edge, target in ((bw.left(), 0.0), (bw.right(), float(W))):
                if abs(edge - target) <= self.SNAP:
                    cx += target - edge
                    self._guides.append(("v", target))
                    break
        best_y = min(((abs(cy - y), y, gy) for y, gy in ys), default=None)
        if best_y and best_y[0] <= self.SNAP:
            cy = best_y[1]
            self._guides.append(("h", best_y[2]))
        else:
            for edge, target in ((bw.top(), 0.0), (bw.bottom(), float(H))):
                if abs(edge - target) <= self.SNAP:
                    cy += target - edge
                    self._guides.append(("h", target))
                    break
        return cx, cy

    def _drag_native(self, d, pos, keep_ratio=False):
        """Тащат/тянут родной элемент: прямоугольник на холсте → место в теме (вынуть из раскладки)."""
        key, what, r0 = d["native"], d["what"], d["r0"]
        dx, dy = pos.x() - d["start"].x(), pos.y() - d["start"].y()
        if what == "move":
            r = r0.translated(dx, dy)
        elif key == "vinyl_bg":                            # фон пластинки — круг: размер от центра
            c = r0.center()
            half = max(8.0, math.hypot(pos.x() - c.x(), pos.y() - c.y()) / math.sqrt(2))
            r = QRectF(c.x() - half, c.y() - half, half * 2, half * 2)
        else:
            r = QRectF(r0)
            if "l" in what:
                r.setLeft(min(r0.right() - 8, r0.left() + dx))
            if "r" in what:
                r.setRight(max(r0.left() + 8, r0.right() + dx))
            if "t" in what:
                r.setTop(min(r0.bottom() - 8, r0.top() + dy))
            if "b" in what:
                r.setBottom(max(r0.top() + 8, r0.bottom() + dy))
            if keep_ratio and len(what) == 2 and r0.height() > 0:   # Shift на углу — с пропорциями
                k = r0.width() / r0.height()
                r.setHeight(r.width() / k) if "b" in what else r.setTop(r.bottom() - r.width() / k)
        self._apply_native(key, r)
        self.update()

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            native = "native" in self._drag
            self._drag = None
            self._guides = []
            if native:
                self.studio.native_committed()
            else:
                self.studio.layers_changed(commit=True)
            self.update()

    def mouseDoubleClickEvent(self, e):
        """Двойной клик: если под курсором несколько элементов — выбрать тот, что лежит под выбранным
        (кнопка за кнопкой, слой под слоем); если элемент один — открыть его правку."""
        if e.button() != Qt.MouseButton.LeftButton:
            return
        pos = e.position()
        cands = self._candidates(pos)
        if len(cands) > 1:
            self._cycle(pos)
            return
        lid = cands[0] if cands else None
        if lid is None or lid.startswith("native:"):
            return
        self.studio.select_layer(lid)
        self._edit_selected()

    def _edit_selected(self):
        """Правка выбранного слоя: текст — прямо на окне, картинка — заменить файл, кнопка — её редактор,
        виджет — код, остальное — поведение (Enter / F2)."""
        st = self.studio
        L = st.selected_layer()
        if L is None:
            return
        k = L["kind"]
        if L.get("design"):
            st.open_button_editor(L["id"])
        elif k == "text":
            self._edit_text(L)
        elif k in ("media", "video"):
            st.pick_layer_media()
        elif k == "script":
            st.open_code(L["id"], "widget")
        else:
            st.open_code(L["id"], "behavior")

    def wheelEvent(self, e):
        st = self.studio
        if st._nsel == "vinyl_bg":                         # колесо над фоном пластинки — его размер
            bg = st.t["components"]["vinyl"]["bg"]
            steps = e.angleDelta().y() / 120.0
            st.set_vinyl_bg({"scale": round(max(0.3, min(3.0, bg.get("scale", 1.0) * (1.06 ** steps))), 3)})
            self.update()
            return
        L = st.selected_layer()
        if L is None:
            lid, _ = self._hit(e.position())
            if lid is None or lid.startswith("native:"):
                return
            st.select_layer(lid)
            L = st.selected_layer()
        if L.get("locked"):
            return
        steps = e.angleDelta().y() / 120.0 or e.angleDelta().x() / 120.0
        mods = e.modifiers()
        if mods & Qt.KeyboardModifier.AltModifier:
            key = "tilt_x" if mods & Qt.KeyboardModifier.ShiftModifier else "tilt_y"
            L[key] = round(max(-85.0, min(85.0, L.get(key, 0.0) + steps * 5)), 2)
        elif mods & Qt.KeyboardModifier.ControlModifier:
            L["rot"] = round(((L.get("rot", 0.0) + steps * 5) + 180) % 360 - 180, 2)
        else:
            L["size"] = round(max(0.01, min(3.0, L["size"] * (1.06 ** steps))), 4)
        st.layers_changed(commit=True)
        self.update()

    # ── клавиатура ── #

    def keyPressEvent(self, e):
        k = e.key()
        mods = e.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        st = self.studio
        L = st.selected_layer()
        if k == Qt.Key.Key_Escape:
            st.set_canvas(False)
            return
        if ctrl and k == Qt.Key.Key_Z:
            (st.redo if shift else st.undo)()
            return
        if ctrl and k == Qt.Key.Key_Y:
            st.redo()
            return
        if ctrl and k == Qt.Key.Key_A:
            st.select_all()
            self.update()
            return
        if ctrl and k == Qt.Key.Key_G:
            (st.ungroup_selected if shift else st.group_selected)()
            self.update()
            return
        if st._nsel and L is None:                         # родной элемент
            key = st._nsel
            if k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                if key == "vinyl_bg":
                    st.set_vinyl_bg({"on": False})
                else:
                    st.hide_native(key)
            elif k in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
                r = self._native_rect(key)
                if r is not None:
                    step = 10.0 if shift else 1.0
                    r.translate({Qt.Key.Key_Left: -step, Qt.Key.Key_Right: step}.get(k, 0.0),
                                {Qt.Key.Key_Up: -step, Qt.Key.Key_Down: step}.get(k, 0.0))
                    self._apply_native(key, r)
                    st.native_committed()
            self.update()
            return
        if L is None:
            return
        if k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            st.delete_layer()
            return
        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_F2):
            self._edit_selected()
            return
        if ctrl and k == Qt.Key.Key_D:
            st.duplicate_layer()
            return
        if k in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            step = 10.0 if shift else 1.0
            dx = {Qt.Key.Key_Left: -step, Qt.Key.Key_Right: step}.get(k, 0.0)
            dy = {Qt.Key.Key_Up: -step, Qt.Key.Key_Down: step}.get(k, 0.0)
            for o in st.selection():
                if not o.get("locked"):
                    o["x"] = round(o["x"] + dx / max(1, self.width()), 4)
                    o["y"] = round(o["y"] + dy / max(1, self.height()), 4)
            st.layers_changed(commit=True)
            self.update()
            return
        if k == Qt.Key.Key_PageUp:
            st.move_layer(1)
        elif k == Qt.Key.Key_PageDown:
            st.move_layer(-1)
        elif k == Qt.Key.Key_H:
            st.toggle_layer_flag("visible")
        elif k == Qt.Key.Key_L:
            st.toggle_layer_flag("locked")

    # ── меню и правка текста ── #

    def _context(self, gpos):
        st = self.studio
        L = st.selected_layer()
        m = QMenu(self)
        m.setStyleSheet(STUDIO_QSS)
        add = m.addMenu("Добавить")
        add.addAction("Текст", lambda: st.add_layer("text"))
        add.addAction("Фигуру", lambda: st.add_layer("shape"))
        add.addAction("Картинку / GIF…", lambda: st.add_layer("media"))
        add.addAction("Видео…", lambda: st.add_layer("video"))
        add.addAction("Свою кнопку…", lambda: st.open_button_editor())
        loc = self.mapFromGlobal(gpos)
        st.native_menu(self, add.addMenu("Элемент плеера"),
                       pos=(loc.x() / max(1, self.width()), loc.y() / max(1, self.height())))
        st.widget_menu(self, add.addMenu("Виджет"))
        st.effect_menu(self, add.addMenu("Эффект"))
        if st._nsel:
            m.addSeparator()
            self._native_context(m, st._nsel)
            m.exec(gpos)
            return
        if L is None:
            m.addSeparator()
            self._hidden_menu(m)
        if L is not None:
            m.addSeparator()
            k = L["kind"]
            if L.get("design"):
                m.addAction("Изменить вид кнопки…", lambda: st.open_button_editor(L["id"]))
            if k == "text":
                m.addAction("Изменить текст…", lambda: self._edit_text(L))
            if k in ("media", "video"):
                m.addAction("Заменить файл…", st.pick_layer_media)
            if k == "media":
                m.addAction("Убрать фон…", lambda: st.open_bg_remove(L["id"]))
            if k == "script":
                m.addAction("Код виджета…", lambda: st.open_code(L["id"], "widget"))
            m.addAction("Поведение (скрипт)…", lambda: st.open_code(L["id"], "behavior"))
            m.addAction("Копия (Ctrl+D)", st.duplicate_layer)
            m.addAction("Поверх интерфейса" if L["z"] == "back" else "Позади интерфейса (на фон)",
                        lambda: st.set_layer_value("z", "front" if L["z"] == "back" else "back"))
            m.addAction("Выше (PgUp)", lambda: st.move_layer(1))
            m.addAction("Ниже (PgDn)", lambda: st.move_layer(-1))
            anim = m.addMenu("Добавить анимацию")
            for val, text in tk.ANIMATIONS:
                if val != "none":
                    anim.addAction(text, lambda v=val: st.add_anim(v))
            if L.get("anims"):
                anim.addSeparator()
                anim.addAction("Убрать все анимации", st.clear_anims)
            blend = m.addMenu("Наложение")
            for val, text in tk.BLEND_MODES:
                a = blend.addAction(text, lambda v=val: st.set_layer_value("blend", v))
                a.setCheckable(True)
                a.setChecked(L.get("blend") == val)
            m.addAction("Отразить", lambda: st.set_layer_value("flip", not L.get("flip")))
            reset = m.addMenu("Сбросить")
            reset.addAction("Поворот", lambda: st.set_layer_value("rot", 0.0))
            reset.addAction("Растяжение", lambda: st.set_layer_values({"stretch_x": 1.0, "stretch_y": 1.0}))
            reset.addAction("3D-наклон", lambda: st.set_layer_values({"tilt_x": 0.0, "tilt_y": 0.0}))
            m.addAction("По центру", lambda: st.set_layer_values({"x": 0.5, "y": 0.5}))
            m.addAction("На всё окно", lambda: st.set_layer_values({"x": 0.5, "y": 0.5, "size": 1.0, "aspect": 1.0,
                                                                    "box": "window", "stretch_x": 1.0,
                                                                    "stretch_y": 1.0, "rot": 0.0}))
            grp = m.addMenu("Группа")
            grp.addAction("Сгруппировать выделенные (Ctrl+G)", st.group_selected)
            grp.addAction("Разгруппировать (Ctrl+Shift+G)", st.ungroup_selected)
            m.addAction("Открепить" if L.get("locked") else "Закрепить (L)", lambda: st.toggle_layer_flag("locked"))
            m.addAction("Скрыть (H)", lambda: st.toggle_layer_flag("visible"))
            m.addSeparator()
            m.addAction("Удалить (Del)", st.delete_layer)
        m.exec(gpos)

    def _edit_text(self, L):
        if self._editor is not None:
            self._editor.deleteLater()
        i = self._layers().index(L)
        geo = self._geo(L, i)
        ed = QPlainTextEdit(self)
        ed.setStyleSheet(STUDIO_QSS)
        ed.setPlainText(L.get("text", ""))
        w = max(220, min(520, int(geo["w"] + 40)))
        ed.setGeometry(int(geo["cx"] - w / 2), int(geo["cy"] - 40), w, 80)
        ed.setToolTip("Enter — готово, Shift+Enter — новая строка, Esc — отмена")
        ed.show()
        ed.setFocus()
        ed.selectAll()
        self._editor = ed

        def finish(apply=True):
            if self._editor is not ed:
                return
            self._editor = None
            if apply:
                self.studio.set_layer_value("text", ed.toPlainText()[:200], lid=L["id"])
            ed.deleteLater()
            self.setFocus()

        def key(ev, _orig=ed.keyPressEvent):
            if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not ev.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                finish(True)
            elif ev.key() == Qt.Key.Key_Escape:
                finish(False)
            else:
                _orig(ev)
        ed.keyPressEvent = key
        ed.focusOutEvent = lambda ev, _o=ed.focusOutEvent: (finish(True), _o(ev))

    # ── перетаскивание файлов ── #

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        files = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        pos = e.position()
        self.studio.drop_files(files, (pos.x() / max(1, self.width()), pos.y() / max(1, self.height())))
        e.acceptProposedAction()


# ------------------------------------------------------------------ #
#  Редактор кода слоя (EchoScript — бывший LOOM)                      #
# ------------------------------------------------------------------ #

class CodeDialog(QWidget):
    """Код виджета или поведение слоя: подсветка, горячая перезагрузка (правка видна в плеере через
    полсекунды, состояние не сбрасывается), ошибки со строкой, готовые кусочки и справка.
    Режим «Блоки» — тот же код блоками как в Scratch (dsl_blocks): все функции соединяются мышью."""

    LAST_BLOCKS = False                                    # последний выбранный режим — следующий редактор в нём

    def __init__(self, studio, lid, role):
        super().__init__()                                 # вкладкой в конструкторе или окном — решает studio
        self.studio, self.lid, self.role = studio, lid, role
        self.key = "script" if role in ("widget", "bg") else "behavior"
        self.setObjectName("Studio")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setStyleSheet(STUDIO_QSS)
        self.resize(760, 640)
        L = self._layer()
        kind = {"widget": "Код виджета", "bg": "Скрипт фона"}.get(role, "Поведение")
        self.tab_title = f"{kind}: {L.get('name') or self.studio.t.get('name') or 'слой'}"
        self.setWindowTitle(f"{kind} — {L.get('name') or self.studio.t.get('name') or 'слой'} — ECHOES")
        from dsl_studio import CodeEdit, EchoHighlighter
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)
        bar = QHBoxLayout()
        title = QLabel(kind)
        title.setObjectName("H1")
        bar.addWidget(title)
        bar.addStretch(1)
        if role in ("behavior", "bg"):
            snip = QPushButton("Готовое ▾")
            menu = QMenu(snip)
            menu.setStyleSheet(STUDIO_QSS)
            for t_, code in (layer_fx.BG_SNIPPETS if role == "bg" else layer_fx.BEHAVIOR_SNIPPETS):
                menu.addAction(_tr(t_), lambda c=code: self._insert(c))
            snip.clicked.connect(lambda: menu.exec(snip.mapToGlobal(snip.rect().bottomLeft())))
            bar.addWidget(snip)
        else:
            lib = QPushButton("Вставить блок ▾")
            lib.setToolTip("Заменить код готовым виджетом из библиотеки")
            menu = QMenu(lib)
            menu.setStyleSheet(STUDIO_QSS)
            for group, items in layer_fx.lib_groups():
                sub = menu.addMenu(_tr(group))
                for b in items:
                    sub.addAction(_tr(b["title"]), lambda b=b: self._replace_widget(b))
            lib.clicked.connect(lambda: menu.exec(lib.mapToGlobal(lib.rect().bottomLeft())))
            bar.addWidget(lib)
            snip = QPushButton("Готовое ▾")
            snip.setToolTip("Вставить кусочек кода в место курсора (внутри widget { … }): вложенные виджеты, "
                            "таймеры, звуки, клавиши, маски, холсты, сохранения, управление слоями…")
            smenu = QMenu(snip)
            smenu.setStyleSheet(STUDIO_QSS)
            for t_, code in layer_fx.WIDGET_SNIPPETS:
                smenu.addAction(_tr(t_), lambda c=code: self._insert_here(c))
            snip.clicked.connect(lambda: smenu.exec(snip.mapToGlobal(snip.rect().bottomLeft())))
            bar.addWidget(snip)
        # «Код | Блоки»: один и тот же код текстом или блоками как в Scratch
        self.b_code = QPushButton("Код")
        self.b_code.setCheckable(True)
        self.b_code.setChecked(True)
        self.b_code.setToolTip("Код EchoScript текстом")
        self.b_blocks = QPushButton("Блоки")
        self.b_blocks.setCheckable(True)
        self.b_blocks.setToolTip("Программирование блоками как в Scratch: все функции EchoScript — блоки, "
                                 "их тянут из палитры и соединяют мышью. Каждая правка сразу пишет код")
        self._mode_grp = QButtonGroup(self)
        self._mode_grp.setExclusive(True)
        self._mode_grp.addButton(self.b_code)
        self._mode_grp.addButton(self.b_blocks)
        self.b_code.clicked.connect(lambda: self._set_blocks(False))
        self.b_blocks.clicked.connect(lambda: self._set_blocks(True))
        bar.insertWidget(1, self.b_code)
        bar.insertWidget(2, self.b_blocks)
        self.b_help = QPushButton("Справка")
        self.b_help.setCheckable(True)
        self.b_help.toggled.connect(self._help)
        self.b_dock = QPushButton()
        self.b_dock.setToolTip("Показывать редактор вкладкой внутри конструктора или отдельным окном")
        self.b_dock.clicked.connect(lambda: self.studio.toggle_editor_dock(self))
        apply = QPushButton("Применить (Ctrl+S)")
        apply.setObjectName("Primary")
        apply.clicked.connect(self.apply)
        bar.addWidget(self.b_help)
        bar.addWidget(self.b_dock)
        bar.addWidget(apply)
        v.addLayout(bar)
        self.split = QSplitter(Qt.Orientation.Horizontal)
        self.ed = CodeEdit()
        self.ed.setStyleSheet("QPlainTextEdit { background: #0f1018; color: #e7e5f2; border: 1px solid #2a2e3e;"
                              " border-radius: 10px; padding: 6px; }")
        self._hl = EchoHighlighter(self.ed.document())
        self.code_stack = QStackedWidget()
        self.code_stack.addWidget(self.ed)
        self.split.addWidget(self.code_stack)
        self.blocks = None
        self.docs = None
        v.addWidget(self.split, 1)
        self.err = QLabel()
        self.err.setObjectName("Warn")
        self.err.setWordWrap(True)
        v.addWidget(self.err)
        tip = ("Видно: w, h — размер слоя; audio.bass/mid/high/level/fft[0..63]/wave/beat; track (title, artist, "
               "cover, progress…); player (playing, volume…); playlist; lyrics (line, lines, index); time, dt; mouse. "
               "Действия: toggle(), next(), prev(), seek(0..1), set_volume(0..1), play_index(i).")
        if role == "bg":
            tip = ("Меняйте поля фона: speed (скорость видео / GIF / живых обоев, 1 — как в теме), zoom, dx, dy "
                   "(сдвиг, px), dim (затемнение 0…1, -1 — как в теме). События: frame(dt), beat(p), track. ") + tip
        if role == "behavior":
            tip = ("Меняйте поля слоя: dx, dy (сдвиг, px), rot (°), scale / sx / sy, opacity, tilt_x / tilt_y (3D, °), "
                   "hidden, tint (цвет), label (текст надписи). События: frame(dt), beat(p), track, click, press, "
                   "drag, release, wheel(d), move, enter, leave. ") + tip
        hint = QLabel(tip)
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        v.addWidget(hint)
        self.reload()
        self._t = QTimer(self)
        self._t.setSingleShot(True)
        self._t.timeout.connect(self.apply)
        self.ed.textChanged.connect(lambda: self._t.start(650))
        self._poll = QTimer(self)
        self._poll.timeout.connect(self._show_error)
        self._poll.start(400)
        QShortcut(QKeySequence("Ctrl+S"), self, activated=self.apply)
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self.apply)
        QShortcut(QKeySequence("Ctrl+Z"), self.ed, activated=self.ed.undo)
        if CodeDialog.LAST_BLOCKS:
            QTimer.singleShot(0, lambda: self._set_blocks(True))

    # ── режим «Блоки» ── #
    def _blocks_on(self) -> bool:
        return self.blocks is not None and self.code_stack.currentWidget() is self.blocks

    def _set_blocks(self, on: bool):
        if on:
            if self.blocks is None:
                try:
                    import dsl_blocks
                    fields = [] if self.role == "widget" else list(
                        (layer_fx.BG_FIELDS if self.role == "bg" else layer_fx.BEHAVIOR_FIELDS).keys())
                    self.blocks = dsl_blocks.BlocksEditor(self.role, fields)
                except Exception as e:                     # noqa: BLE001
                    self.err.setText(f"Блоки недоступны: {e}")
                    self.b_code.setChecked(True)
                    return
                self.blocks.code_changed.connect(self._from_blocks)
                self.code_stack.addWidget(self.blocks)
            if self._t.isActive():
                self.apply()
            e = self.blocks.load(self.ed.toPlainText())
            if e:
                self.err.setText("Блоками не открыть, пока в коде ошибка: " + e)
                self.err.setObjectName("Warn")
                self.err.setStyleSheet(self.err.styleSheet())
                self.b_code.setChecked(True)
                self.code_stack.setCurrentWidget(self.ed)
                return
            self.code_stack.setCurrentWidget(self.blocks)
            self.b_blocks.setChecked(True)
            if self.isWindow() and self.width() < 1180:
                self.resize(1180, max(self.height(), 720))
        else:
            self.code_stack.setCurrentWidget(self.ed)
            self.b_code.setChecked(True)
            if self.blocks is not None:
                self.blocks.ws.error_blk = None
        CodeDialog.LAST_BLOCKS = bool(on)

    def _from_blocks(self, code):
        """Правка блоками → код слоя (применяется как обычная правка текста)."""
        self.ed.blockSignals(True)
        self.ed.setPlainText(code)
        self.ed.blockSignals(False)
        self._t.start(350)

    def _sync_blocks(self):
        if self._blocks_on():
            e = self.blocks.load(self.ed.toPlainText())
            if e:
                self._set_blocks(False)

    def _layer(self):
        if self.role == "bg":
            return self.studio.t["background"]
        return next((x for x in self.studio.t["layers"] if x["id"] == self.lid), None) or {}

    def reload(self):
        L = self._layer()
        txt = L.get(self.key) or ""
        if not txt and self.role == "behavior":
            txt = layer_fx.BEHAVIOR_TEMPLATE
        if not txt and self.role == "bg":
            txt = layer_fx.BG_TEMPLATE
        if txt != self.ed.toPlainText():
            self.ed.blockSignals(True)
            self.ed.setPlainText(txt)
            self.ed.blockSignals(False)
            self._sync_blocks()

    def _insert(self, code):
        c = self.ed.textCursor()
        c.movePosition(c.MoveOperation.End)
        self.ed.setTextCursor(c)
        self.ed.insertPlainText(("\n" if self.ed.toPlainText().strip() else "") + code + "\n")
        self._sync_blocks()

    def _insert_here(self, code):
        """Кусочек кода — в место курсора, с отступом текущей строки."""
        c = self.ed.textCursor()
        line = c.block().text()
        ind = line[:len(line) - len(line.lstrip())] if line.strip() else "  "
        body = "\n".join((ind + ln) if ln else ln for ln in code.split("\n"))
        if line.strip():
            c.movePosition(c.MoveOperation.EndOfBlock)
            body = "\n" + body
        else:
            c.movePosition(c.MoveOperation.StartOfBlock)
            c.movePosition(c.MoveOperation.EndOfBlock, c.MoveMode.KeepAnchor)
        if self._blocks_on():                              # в блоках «место курсора» — первый обработчик
            c.movePosition(c.MoveOperation.End)
            body = "\n" + code
        c.insertText(body)
        self.ed.setTextCursor(c)
        self.ed.setFocus()
        self._sync_blocks()

    def _replace_widget(self, b):
        L = self._layer()
        if not L:
            return
        L["widget"] = b["cls"]
        L["props"] = dict(b["props"])
        self.ed.setPlainText(b["code"] + "\n")
        self.apply()
        self._sync_blocks()
        self.studio._rebuild_layer_box_soon()

    def apply(self):
        self._t.stop()
        L = self._layer()
        if not L:
            return
        txt = self.ed.toPlainText()
        if L.get(self.key) == txt:
            return
        L[self.key] = txt[:tk.MAX_SCRIPT]
        if self.role == "widget":
            names = DL.widget_names(txt)
            if names and L.get("widget") not in names:
                L["widget"] = names[0]
        self.studio.changed("layers")
        self.studio._commit_soon()
        self.studio._refresh_layer_list()
        if self.role == "bg":
            self.studio._sync_bg_script_lbl()
        QTimer.singleShot(120, self._show_error)

    def _show_error(self):
        try:
            e = self.studio.script_error(self.lid)
        except RuntimeError:
            return
        if self.role == "behavior":
            e = "\n".join(x[len("Поведение: "):] for x in e.splitlines() if x.startswith("Поведение: "))
        elif self.role == "bg":
            pass
        else:
            e = "\n".join(x for x in e.splitlines() if not x.startswith("Поведение: "))
        self.err.setText(("Ошибка: " + e) if e else "Работает")
        self.err.setObjectName("Warn" if e else "Good")
        self.err.setStyleSheet(self.err.styleSheet())
        m = re.match(r"строка (\d+)", e or "")
        self.ed.mark_error(int(m.group(1)) if m else 0)
        if self._blocks_on():
            self.blocks.mark_error_line(int(m.group(1)) if m else 0)

    def _help(self, on):
        if on and self.docs is None:
            try:
                import dsl_docs
                self.docs = QTextBrowser()
                self.docs.setOpenExternalLinks(True)
                self.docs.setHtml(dsl_docs.html())
                self.docs.setStyleSheet("QTextBrowser { background: #12131b; border: 1px solid #2a2e3e;"
                                        " border-radius: 10px; }")
                self.split.addWidget(self.docs)
                self.split.setSizes([460, 300])
            except Exception as e:                         # noqa: BLE001
                self.err.setText(f"Справка недоступна: {e}")
                return
        if self.docs is not None:
            self.docs.setVisible(on)

    def set_docked(self, docked: bool):
        self.b_dock.setText("Отдельным окном" if docked else "Встроить")

    def closeEvent(self, e):
        try:
            if self._t.isActive():                         # недоприменённая правка (шаблон сам не включается)
                self.apply()
            self._poll.stop()
            self.studio._code.pop((self.lid, self.role), None)
            self.studio._editor_closed(self)
        except RuntimeError:
            pass
        super().closeEvent(e)


# ------------------------------------------------------------------ #
#  Убрать фон у картинки / GIF (своя модель — bg_remove)              #
# ------------------------------------------------------------------ #

def _checker_pixmap(img: QImage, side: int) -> QPixmap:
    """Картинка с прозрачностью на шахматке, вписанная в квадрат side."""
    pm = QPixmap(side, side)
    pm.fill(QColor(_PANEL2))
    p = QPainter(pm)
    c = 12
    for y in range(0, side, c):
        for x in range(0, side, c):
            p.fillRect(x, y, c, c, QColor("#2c2f3b") if (x // c + y // c) % 2 else QColor("#1f212b"))
    if img is not None and not img.isNull():
        s = img.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        p.drawImage((side - s.width()) // 2, (side - s.height()) // 2, s)
    p.end()
    return pm


def _np_rgba_to_qimage(a) -> QImage:
    h, w = a.shape[:2]
    return QImage(a.tobytes(), w, h, w * 4, QImage.Format.Format_RGBA8888).copy()


class BgRemoveEditor(QWidget):
    """Убрать фон у слоя-картинки или GIF: предпросмотр «до / после», сила, мягкость краёв,
    «убирать и внутри». Готово — новый файл (PNG или анимированный WebP) в теме, слой его показывает;
    оригинал запоминается (кнопка «Вернуть фон»)."""

    def __init__(self, studio, lid):
        super().__init__()
        self.studio, self.lid = studio, lid
        self.setObjectName("Studio")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setStyleSheet(STUDIO_QSS)
        self.setWindowTitle("Убрать фон — ECHOES")
        self.resize(900, 620)
        L = self._layer()
        self.tab_title = _tr("Фон: ") + ((L or {}).get("name") or _tr("картинка"))
        self.frames, self.durs, self.animated = [], [], False
        self._busy = False
        self._gen = 0
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(10)
        bar = QHBoxLayout()
        t = QLabel("Убрать фон")
        t.setObjectName("H1")
        bar.addWidget(t)
        bar.addStretch(1)
        self.b_dock = QPushButton()
        self.b_dock.clicked.connect(lambda: self.studio.toggle_editor_dock(self))
        self.b_restore = QPushButton("Вернуть фон")
        self.b_restore.setToolTip("Показать исходную картинку, как была")
        self.b_restore.clicked.connect(self._restore)
        self.b_apply = QPushButton("Применить")
        self.b_apply.setObjectName("Primary")
        self.b_apply.clicked.connect(self._apply)
        for b in (self.b_dock, self.b_restore, self.b_apply):
            bar.addWidget(b)
        v.addLayout(bar)
        prev = QHBoxLayout()
        self.l_before, self.l_after = QLabel(), QLabel()
        for lb, cap in ((self.l_before, "Было"), (self.l_after, "Стало")):
            col = QVBoxLayout()
            h = QLabel(_tr(cap).upper())
            h.setObjectName("H2")
            col.addWidget(h)
            lb.setMinimumSize(300, 300)
            lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            col.addWidget(lb, 1)
            prev.addLayout(col, 1)
        v.addLayout(prev, 1)
        self.s_strength = SliderRow("Сила (смелее срезать фон)", 0, 1, 0.5, 0.01, "{:.2f}", default=0.5)
        self.s_soft = SliderRow("Мягкость краёв (волосы, мех)", 0, 1, 0.4, 0.01, "{:.2f}", default=0.4)
        self.c_holes = QCheckBox("Убирать фон и внутри (просветы между руками, в буквах…)")
        for w in (self.s_strength, self.s_soft):
            w.released.connect(self._recalc_soon)
            v.addWidget(w)
        self.c_holes.toggled.connect(self._recalc_soon)
        v.addWidget(self.c_holes)
        self.status = QLabel("")
        self.status.setObjectName("Hint")
        self.status.setWordWrap(True)
        v.addWidget(self.status)
        hint = QLabel("Своя модель: изучает цвета фона по краям кадра и цвета объекта, проверяет, что фон "
                      "связан с краями, и подгоняет края по самой картинке. Лучше всего — объект на "
                      "однотонном или спокойном фоне. Работает без интернета.")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        v.addWidget(hint)
        self._t = QTimer(self)
        self._t.setSingleShot(True)
        self._t.timeout.connect(self._recalc)
        self._load()

    def set_docked(self, docked: bool):
        self.b_dock.setText("Отдельным окном" if docked else "Встроить")

    def _layer(self):
        return next((x for x in self.studio.t["layers"] if x["id"] == self.lid), None)

    def _ui(self, fn):
        try:
            self.studio.win._ui_bridge.call.emit(fn)
        except RuntimeError:
            pass

    def _load(self):
        L = self._layer()
        if L is None:
            return
        src = L.get("src_orig") or L.get("src")
        path = self.studio.store.asset(self.studio.t, src)
        self.b_restore.setEnabled(bool(L.get("src_orig")))
        if not path:
            self.status.setText(_tr("Файл картинки не найден"))
            return
        import bg_remove as BR
        try:
            self.frames, self.durs, self.animated = BR.load_frames(path)
        except Exception as e:                             # noqa: BLE001
            self.status.setText(_tr("Не получилось открыть: ") + str(e))
            return
        self._src_rel = src
        self.l_before.setPixmap(_checker_pixmap(_np_rgba_to_qimage(self.frames[0]), 320))
        if self.animated:
            self.status.setText(_tr("Анимация: кадров ") + str(len(self.frames)) +
                                _tr(". В предпросмотре — первый кадр, «Применить» обработает все."))
        self._recalc()

    def _params(self):
        return self.s_strength.value(), self.s_soft.value(), self.c_holes.isChecked()

    def _recalc_soon(self, *_):
        self._t.start(250)

    def _recalc(self):
        if not self.frames:
            return
        self._gen += 1
        gen = self._gen
        frames = self.frames
        st, so, ho = self._params()
        self.status.setText(_tr("Думаю…"))

        def work():
            import numpy as np
            import bg_remove as BR
            try:
                f0 = frames[0]
                h, w = f0.shape[:2]
                k = min(1.0, 520 / max(h, w))                # предпросмотр — уменьшенный кадр
                if k < 1:
                    ys = (np.arange(int(h * k)) / k).astype(int)
                    xs = (np.arange(int(w * k)) / k).astype(int)
                    f0 = f0[ys][:, xs]
                m = BR.fit_model([f0], st, so, ho)
                out = m.cutout(f0)
                img = _np_rgba_to_qimage(out)

                def show():
                    if gen != self._gen:
                        return
                    self.l_after.setPixmap(_checker_pixmap(img, 320))
                    self.status.setText(_tr("Готово. Не нравится — подвиньте «Силу» или «Мягкость»."))
                self._ui(show)
            except Exception as e:                         # noqa: BLE001
                err = str(e)
                self._ui(lambda: self.status.setText(_tr("Ошибка модели: ") + err))
        import threading
        threading.Thread(target=work, daemon=True).start()

    def _apply(self):
        if not self.frames or self._busy:
            return
        self._busy = True
        self.b_apply.setEnabled(False)
        frames, durs, animated = self.frames, self.durs, self.animated
        st, so, ho = self._params()
        src_rel = self._src_rel

        def work():
            import os
            import tempfile
            import bg_remove as BR
            try:
                m = BR.fit_model(frames, st, so, ho)
                out = []
                for i, f in enumerate(frames):
                    out.append(m.cutout(f))
                    if i % 3 == 0:
                        n = i + 1
                        self._ui(lambda n=n: self.status.setText(_tr("Обрабатываю кадры: ") + f"{n}/{len(frames)}"))
                stem = os.path.splitext(os.path.basename(src_rel))[0][:30] + "_nobg"
                path = BR.save(out, durs, animated, os.path.join(tempfile.gettempdir(), stem + ".png"))
                self._ui(lambda: self._done(path, src_rel))
            except Exception as e:                         # noqa: BLE001
                err = str(e)
                self._ui(lambda: (self.status.setText(_tr("Ошибка: ") + err), self._unbusy()))
        import threading
        threading.Thread(target=work, daemon=True).start()

    def _unbusy(self):
        self._busy = False
        self.b_apply.setEnabled(True)

    def _done(self, path, src_rel):
        import os
        self._unbusy()
        L = self._layer()
        if L is None:
            return
        rel = self.studio.store.import_asset(self.studio.t, path)
        try:
            os.remove(path)
        except OSError:
            pass
        if not rel:
            self.status.setText(_tr("Не получилось сохранить результат"))
            return
        L["src_orig"] = src_rel
        self.studio.set_layer_value("src", rel, lid=self.lid)
        self.studio._commit_soon()
        self.b_restore.setEnabled(True)
        self.status.setText(_tr("Фон убран — слой уже показывает новую картинку."))

    def _restore(self):
        L = self._layer()
        if L is None or not L.get("src_orig"):
            return
        orig = L.pop("src_orig")
        self.studio.set_layer_value("src", orig, lid=self.lid)
        self.studio._commit_soon()
        self.b_restore.setEnabled(False)
        self.status.setText(_tr("Исходная картинка возвращена."))

    def closeEvent(self, e):
        try:
            self.studio._editor_closed(self)
            if getattr(self.studio, "_bg_ed", None) is self:
                self.studio._bg_ed = None
        except RuntimeError:
            pass
        super().closeEvent(e)


# ------------------------------------------------------------------ #
#  Окно конструктора                                                  #
# ------------------------------------------------------------------ #

class ThemeStudio(QWidget):
    PAGES = [("start", "Старт", "★"), ("colors", "Цвета", "◐"), ("text", "Текст", "Aa"), ("shape", "Формы", "▢"),
             ("bg", "Фон", "▦"), ("layers", "Слои", "❏"), ("library", "Библиотека", "◈"), ("fx", "Эффекты", "✶"),
             ("elements", "Элементы", "◎"), ("lyrics", "Режим текста", "♪"), ("clip", "Режим клипа", "▶"),
             ("save", "Сохранить", "✓"), ("help", "Справка", "?")]

    def __init__(self, win):
        super().__init__(win, Qt.WindowType.Window)
        self._multi = []                                   # ещё выделенные слои (кроме главного)
        self._nsel = None                                  # выбранный родной элемент интерфейса (ключ)
        self._code = {}                                    # открытые редакторы кода: (id слоя, роль) → окно
        self.win = win
        self.store = win.theme_store
        self.setObjectName("Studio")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setWindowTitle("Конструктор тем — ECHOES")
        self.setWindowIcon(win.windowIcon())
        self.setStyleSheet(STUDIO_QSS)
        self.setAcceptDrops(True)
        self.resize(640, 880)
        self.setMinimumSize(520, 560)
        # что правим: основной вид темы или режим текста (у него свои фон, слои, эффекты и раскладка).
        # В режиме текста self.t — «вид»: те же ключи темы, но эти части — из t["lyrics"] (см. full())
        self._mode = "main"
        self._main_parts = {}
        self.t = self._fresh(tk.templates()[0])
        self._saved = None
        self._undo, self._redo = [], []
        self._snap = self._json()
        self._sel = None
        self._ref = []
        self._thumbs = {}
        self._dragging = False
        self._styles_pending = False
        self.canvas = None
        self._pv = QTimer(self)
        self._pv.setSingleShot(True)
        self._pv.timeout.connect(self._do_preview)
        self._commit_t = QTimer(self)
        self._commit_t.setSingleShot(True)
        self._commit_t.timeout.connect(self._commit)
        self._build()
        for seq, fn in (("Ctrl+Z", self.undo), ("Ctrl+Y", self.redo), ("Ctrl+Shift+Z", self.redo),
                        ("Ctrl+S", lambda: self.save())):
            QShortcut(QKeySequence(seq), self, activated=fn)
        self._place()

    # ── состояние темы ── #

    def _fresh(self, tpl):
        t = tk.normalize(copy.deepcopy(tpl))
        t["id"] = ""
        t["created"] = 0
        names = {x["name"] for x in self.store.list()}
        t["name"] = tk.suggest_name(names, tpl.get("name") or _tr("Моя тема"))
        return t

    def _json(self):
        return json.dumps(self.full(), ensure_ascii=False, sort_keys=True)

    # ── основной вид / режим текста ── #

    def full(self) -> dict:
        """Вся тема целиком (в режиме текста self.t — только её «вид»)."""
        if self._mode != "lyrics":
            return self.t
        out = dict(self.t)
        ly = dict(self.t.get("lyrics") or {})
        for k in tk.LYRICS_PARTS:
            ly[k] = self.t[k]
            out[k] = self._main_parts[k]
        out["lyrics"] = ly
        return out

    def _set_full(self, full: dict, mode=None):
        """Новая тема целиком; режим правки — прежний (если у темы нет своего режима текста — основной)."""
        mode = mode or self._mode
        if mode == "lyrics" and not (full.get("lyrics") or {}).get("on"):
            mode = "main"
        self._mode = mode
        if mode == "lyrics":
            self._main_parts = {k: full[k] for k in tk.LYRICS_PARTS}
            self.t = tk.lyrics_view(full)
        else:
            self._main_parts = {}
            self.t = full
        self._sync_mode_ui()

    def lyrics_mode(self) -> bool:
        return self._mode == "lyrics"

    def set_mode(self, mode, sync_player=True):
        """Переключить, что правим: «main» — основной вид, «lyrics» — режим текста (плеер тоже переходит в него)."""
        mode = "lyrics" if mode == "lyrics" else "main"
        if mode == self._mode:
            self._sync_mode_ui()
            return
        full = self.full()
        if mode == "lyrics":
            tk.lyrics_start(full)                          # первый раз: фон и эффекты — копия основного вида
        self._set_full(full, mode)
        self._sel = self.t["layers"][-1]["id"] if self.t["layers"] else None
        self._multi = []
        self._nsel = None
        self._after_reload(preview=True)
        if sync_player:
            fn = getattr(self.win, "studio_lyrics_mode", None)
            if fn is not None:
                fn(mode == "lyrics")
        self._commit_soon()
        if self.canvas is not None:
            QTimer.singleShot(60, self.canvas.update)
            QTimer.singleShot(900, self.canvas.update)       # пластинка долетела — рамки на месте
        page = self.PAGES[self.nav.currentRow()][0] if hasattr(self, "nav") else ""
        if mode == "lyrics" and page in ("start", "colors", "text", "shape", "elements", "save"):
            self.go("lyrics")                              # эти страницы общие — показать настройки текста
        self.status.setText(_tr("Правите режим текста: свои фон, слои, эффекты и раскладка") if mode == "lyrics"
                            else _tr("Правите основной вид темы"))

    def player_lyrics_changed(self, on: bool):
        """Плеер сам открыл/закрыл режим текста — править то, что на экране."""
        if not on:
            self.set_mode("main", sync_player=False)
        elif (self.full().get("lyrics") or {}).get("on"):
            self.set_mode("lyrics", sync_player=False)

    def _sync_mode_ui(self):
        seg = getattr(self, "_mode_btns", None)
        if not seg:
            return
        for m, b in seg.items():
            b.blockSignals(True)
            b.setChecked(m == self._mode)
            b.blockSignals(False)
        bar = getattr(self, "_mode_bar", None)
        if bar is not None:
            bar.setProperty("lyrics", self._mode == "lyrics")
            bar.style().unpolish(bar)
            bar.style().polish(bar)

    def dirty(self) -> bool:
        return self._saved is None or self._json() != self._saved

    def selected_layer(self):
        for L in self.t["layers"]:
            if L["id"] == self._sel:
                return L
        return None

    def _obj(self, path):
        o = self.t
        for k in path[:-1]:
            o = self.selected_layer() if k == "@L" else o[k]
            if o is None:
                raise KeyError("no layer selected")
        return o, path[-1]

    def _get(self, path):
        o, k = self._obj(path)
        return o.get(k) if isinstance(o, dict) else o[k]

    def _set(self, path, v, scope=None):
        try:
            o, k = self._obj(path)
        except KeyError:
            return
        if isinstance(o, dict) and o.get(k) == v:
            return
        o[k] = v
        self.changed(scope or ("layers" if path[0] == "@L" else "full"))

    def changed(self, scope="full", commit=True):
        if scope == "native_bg":                           # фон пластинки — сразу, без пересборки темы
            bg = self.t["components"]["vinyl"]["bg"]
            self.win.vinyl.set_bg_layout(bg.get("dx", 0.0), bg.get("dy", 0.0), bg.get("scale", 1.0),
                                         bg.get("on", True))
            if self.canvas is not None:
                self.canvas.update()
            scope = "meta"
        if scope == "vinyl_img":                           # своя картинка пластинки — сразу на диск
            try:
                import player_prefs
                rel = (self.t["components"]["vinyl"].get("image") or "")
                player_prefs.apply_vinyl_image(self.win, self.store.asset(self.t, rel) if rel else "")
            except Exception as e:                         # noqa: BLE001
                print("[studio] vinyl image:", e)
            scope = "meta"
        if scope == "clip":                                # оформление режима клипа — сразу, без пересборки темы
            self._clip_restyle()
            scope = "meta"
        if scope == "lyrics_text":                         # шрифт/цвета текста — сразу в оверлей текста
            fn = getattr(self.win, "_lyrics_custom_style", None)
            if fn is not None and getattr(self.win, "_custom_on", False):
                fn(self.full())
            scope = "meta"
        if scope == "layers":
            self._preview(layers_only=True)
            if self.canvas is not None:
                self.canvas.update()
            if commit:
                self._refresh_layer_list()
        elif scope != "meta":
            self._pv.start(70)
        if commit:
            self._commit_t.start(450)
        self._update_status()

    def _do_preview(self):
        self._preview(False)

    def _preview(self, layers_only=False):
        dragging = getattr(self, "_dragging", False) and not layers_only
        self._styles_pending = dragging
        try:
            self.win.custom_preview(copy.deepcopy(self.full()), layers_only=layers_only, styles=not dragging)
        except Exception as e:                             # noqa: BLE001
            import traceback
            traceback.print_exc()
            self.status.setText(f"Ошибка предпросмотра: {e}")
        if not layers_only and not dragging:
            QTimer.singleShot(0, self._refresh_native_list)   # что из элементов плеера теперь на окне
        if self.canvas is not None:
            self.canvas.raise_()
            self.canvas.bar.raise_()

    def _commit(self):
        j = self._json()
        if j != self._snap:
            self._undo.append(self._snap)
            del self._undo[:-150]
            self._redo.clear()
            self._snap = j
        self._update_status()

    def _commit_soon(self):
        self._commit_t.start(120)

    def undo(self):
        self._commit_t.stop()
        self._commit()
        if not self._undo:
            return
        self._redo.append(self._snap)
        self._snap = self._undo.pop()
        self._load_json(self._snap)

    def redo(self):
        self._commit_t.stop()
        self._commit()
        if not self._redo:
            return
        self._undo.append(self._snap)
        self._snap = self._redo.pop()
        self._load_json(self._snap)

    def _load_json(self, j):
        mode = self._mode
        self._set_full(json.loads(j))
        if mode == "lyrics" and self._mode != mode:        # отменили включение режима текста
            fn = getattr(self.win, "studio_lyrics_mode", None)
            if fn is not None:
                fn(False)
        self._after_reload()

    def _after_reload(self, preview=True):
        if self.selected_layer() is None:
            self._sel = self.t["layers"][-1]["id"] if self.t["layers"] else None
        self._refresh_all()
        self._rebuild_bg_box()
        self._rebuild_layer_box()
        self._refresh_layer_list()
        self._refresh_contrast()
        self._refresh_font_labels()
        self._sync_particle_image_row()
        if preview:
            self._preview(False)
        if self.canvas is not None:
            self.canvas.update()
        self._refresh_native_list()
        ids = {L["id"] for L in self.t["layers"]}
        for (lid, _role), dlg in list(self._code.items()):           # отмена/повтор — и в открытых редакторах
            try:
                dlg.reload() if lid in ids else dlg.close()
            except RuntimeError:
                pass
        self._update_status()

    def _update_status(self):
        d = self.dirty()
        name = self.t.get("name") or "тема"
        self.setWindowTitle(f"{'● ' if d else ''}{name} — Конструктор тем ECHOES")
        if self._saved is None:
            self.status.setText("Новая тема — ещё не сохранена")
        else:
            self.status.setText("Есть несохранённые изменения" if d else "Все изменения сохранены")
        self.btn_undo.setEnabled(bool(self._undo) or self._json() != self._snap)
        self.btn_redo.setEnabled(bool(self._redo))
        self.btn_revert.setEnabled(self._saved is not None and d)

    # ── привязка контролов ── #

    def _bind(self, tag, fn):
        self._ref.append((tag, fn))

    def _unbind(self, tag):
        self._ref = [r for r in self._ref if r[0] != tag]

    def _refresh_all(self, tag=None):
        for tg, fn in list(self._ref):
            if tag is None or tg == tag:
                try:
                    fn()
                except (RuntimeError, KeyError, TypeError):
                    pass

    def _slider(self, path, label, lo, hi, step=0.01, fmt="{:.2f}", tag="", scope=None, cast=float, tip="",
                default=None):
        try:
            cur = self._get(path)
        except KeyError:
            cur = lo
        row = SliderRow(label, lo, hi, cur if cur is not None else lo, step, fmt, default=default, tip=tip)

        def on(v):
            self._set(path, int(round(v)) if cast is int else round(float(v), 4), scope)
        row.changed.connect(on)
        row.sl.sliderPressed.connect(self._drag_on)
        row.released.connect(self._drag_off)
        self._bind(tag, lambda: row.set_value(self._get(path)))
        return row

    def _drag_on(self):
        self._dragging = True

    def _drag_off(self):
        """Ползунок отпустили: применить всё, включая стили окна (во время перетаскивания — только фон и элементы)."""
        self._dragging = False
        self._commit_soon()
        if self._pv.isActive() or self._styles_pending:
            self._pv.start(10)

    def _check(self, path, label, tag="", scope=None, tip=""):
        cb = QCheckBox(label)
        if tip:
            cb.setToolTip(tip)
        try:
            cb.setChecked(bool(self._get(path)))
        except KeyError:
            pass
        cb.toggled.connect(lambda on: self._set(path, bool(on), scope))

        def ref():
            cb.blockSignals(True)
            cb.setChecked(bool(self._get(path)))
            cb.blockSignals(False)
        self._bind(tag, ref)
        return cb

    def _color(self, path, label, tag="", scope=None, allow_empty=False, empty_text="авто", tip="", on_change=None):
        try:
            cur = self._get(path) or ""
        except KeyError:
            cur = ""
        btn = ColorButton(cur, allow_empty, empty_text)

        def on(c):
            self._set(path, c, scope)
            if on_change:
                on_change()
        btn.changed.connect(on)
        self._bind(tag, lambda: btn.set_color(self._get(path) or ""))
        lb = QLabel(label)
        if tip:
            lb.setToolTip(tip)
            btn.setToolTip(tip + "\n" + btn.toolTip())
        return _row(lb, None, btn)

    def _combo(self, path, options, tag="", scope=None, on_change=None):
        cb = QComboBox()
        for val, text in options:
            cb.addItem(text, val)

        def ref():
            cb.blockSignals(True)
            i = cb.findData(self._get(path))
            cb.setCurrentIndex(max(0, i))
            cb.blockSignals(False)
        ref()

        def on(_i):
            self._set(path, cb.currentData(), scope)
            if on_change:
                on_change()
        cb.currentIndexChanged.connect(on)
        self._bind(tag, ref)
        return cb

    def _cards(self, path, options, cols=3, icon=QSize(88, 54), tag="", scope=None, on_change=None):
        grid = CardGrid(options, cols, icon)
        try:
            grid.set_value(self._get(path))
        except KeyError:
            pass

        def on(v):
            self._set(path, v, scope)
            if on_change:
                on_change()
        grid.chosen.connect(on)
        self._bind(tag, lambda: grid.set_value(self._get(path)))
        return grid

    def _file(self, path, kind, tag="", scope=None, on_change=None, empty="Файл не выбран"):
        name = QLabel()
        name.setObjectName("Hint")
        name.setWordWrap(True)
        pick = QPushButton("Выбрать файл…")
        clear = QPushButton("×")
        clear.setObjectName("Tool")
        clear.setToolTip("Убрать файл")
        flt = {"image": IMAGE_FILTER, "video": VIDEO_FILTER, "font": FONT_FILTER}[kind]

        def ref():
            try:
                v = self._get(path)
            except KeyError:
                v = ""
            name.setText(Path(v).name if v else empty)
            clear.setEnabled(bool(v))

        def do_pick():
            fn, _ = QFileDialog.getOpenFileName(self, "Выбрать файл", "", flt)
            if fn:
                rel = self.store.import_asset(self.t, fn)
                if rel:
                    self._set(path, rel, scope)
                    ref()
                    if on_change:
                        on_change()

        def do_clear():
            self._set(path, "", scope)
            ref()
            if on_change:
                on_change()
        pick.clicked.connect(do_pick)
        clear.clicked.connect(do_clear)
        ref()
        self._bind(tag, ref)
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        h.addWidget(name, 1)
        h.addWidget(pick)
        h.addWidget(clear)
        return w

    # ── каркас окна ── #

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        # шапка
        head = QFrame()
        head.setObjectName("Header")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(12, 10, 12, 10)
        hl.setSpacing(8)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Название темы")
        self.name_edit.setMaxLength(60)
        self.name_edit.setText(self.t["name"])
        self.name_edit.textEdited.connect(self._on_name)
        self._bind("", lambda: (self.name_edit.text() != self.t["name"]) and self.name_edit.setText(self.t["name"]))
        self.btn_undo = QPushButton("↶")
        self.btn_undo.setObjectName("Tool")
        self.btn_undo.setToolTip("Отменить (Ctrl+Z)")
        self.btn_undo.clicked.connect(self.undo)
        self.btn_redo = QPushButton("↷")
        self.btn_redo.setObjectName("Tool")
        self.btn_redo.setToolTip("Повторить (Ctrl+Y)")
        self.btn_redo.clicked.connect(self.redo)
        surprise = QPushButton("Удиви меня")
        surprise.setToolTip("Случайная, но гармоничная тема: цвета, фон, частицы, формы")
        surprise.clicked.connect(self.surprise)
        self.btn_dock = QPushButton("⇲")
        self.btn_dock.clicked.connect(self._toggle_dock)
        self.btn_close_dock = QPushButton("✕")
        self.btn_close_dock.setObjectName("Tool")
        self.btn_close_dock.setToolTip("Закрыть конструктор")
        self.btn_close_dock.clicked.connect(lambda: getattr(self.win, "close_studio", self.close)())
        hl.addWidget(self.name_edit, 1)
        help_btn = QPushButton("？ Справка")
        help_btn.setToolTip("Как пользоваться конструктором и писать скрипты — по шагам, с примерами")
        help_btn.clicked.connect(lambda: self.open_help())
        hl.addWidget(help_btn)
        hl.addWidget(self.btn_undo)
        hl.addWidget(self.btn_redo)
        hl.addWidget(surprise)
        hl.addWidget(self.btn_dock)
        hl.addWidget(self.btn_close_dock)
        self._sync_dock_button()
        root.addWidget(head)
        # что правим: основной вид или режим текста (у него всё своё — фон, слои, виджеты, раскладка)
        bar = self._mode_bar = QFrame()
        bar.setObjectName("ModeBar")
        ml = QHBoxLayout(bar)
        ml.setContentsMargins(12, 6, 12, 6)
        ml.setSpacing(6)
        self._mode_btns = {}
        for m, text, tip in (("main", "Основной вид", "Обычный экран плеера"),
                             ("lyrics", "Режим текста",
                              "Экран текста песни: свои фон, слои, виджеты, эффекты и раскладка, шрифты текста. "
                              "Плеер откроет текст, чтобы было видно, что вы правите")):
            b = QPushButton(text)
            b.setObjectName("Seg")
            b.setCheckable(True)
            b.setToolTip(tip)
            b.clicked.connect(lambda _c=False, m=m: self.set_mode(m))
            self._mode_btns[m] = b
            ml.addWidget(b)
        # режим клипа — своё оформление в этой же теме: страница настроек + живой показ на плеере
        self.btn_clip_mode = QPushButton("Режим клипа")
        self.btn_clip_mode.setObjectName("Seg")
        self.btn_clip_mode.setCheckable(True)
        self.btn_clip_mode.setToolTip("Оформление режима клипа: фон, рамка, свечение, панель, эффекты. "
                                      "Плеер покажет клип, чтобы было видно, что вы правите")
        self.btn_clip_mode.toggled.connect(self._toggle_clip_preview)
        ml.addWidget(self.btn_clip_mode)
        ml.addStretch(1)
        root.addWidget(bar)
        self._sync_mode_ui()
        # тело: навигация + страницы. Редакторы (код, кнопка) открываются рядом вкладками — внутри
        # конструктора, а не отдельными окнами (⇱ в редакторе — вынести в окно)
        main = QWidget()
        body = QHBoxLayout(main)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.nav = QListWidget()
        self.nav.setObjectName("Nav")
        self.nav.setFixedWidth(124)
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.stack = QStackedWidget()
        builders = {"start": self._page_start, "colors": self._page_colors, "text": self._page_text,
                    "shape": self._page_shape, "bg": self._page_bg, "layers": self._page_layers,
                    "library": self._page_library, "fx": self._page_fx, "elements": self._page_elements,
                    "lyrics": self._page_lyrics, "clip": self._page_clip, "save": self._page_save,
                    "help": self._page_help}
        self._page_index = {}
        for key, title, icon in self.PAGES:
            it = QListWidgetItem(f"{icon}   {_tr(title)}")
            self.nav.addItem(it)
            self._page_index[key] = self.stack.count()
            # справка прокручивается сама — без внешней прокрутки (иначе две полосы)
            self.stack.addWidget(builders[key]() if key == "help" else self._scroll(builders[key]()))
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)
        body.addWidget(self.nav)
        body.addWidget(self.stack, 1)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("EditorTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.setTabBarAutoHide(True)                 # пока редакторов нет — вкладок не видно
        self.tabs.addTab(main, _tr("Конструктор"))
        self.tabs.tabBar().setTabButton(0, QTabBar.ButtonPosition.RightSide, None)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        root.addWidget(self.tabs, 1)
        # подвал
        foot = QFrame()
        foot.setObjectName("Footer")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(12, 10, 12, 10)
        fl.setSpacing(8)
        self.status = QLabel()
        self.status.setObjectName("Status")
        self.btn_revert = QPushButton("Откатить")
        self.btn_revert.setToolTip("Вернуть тему к последнему сохранению")
        self.btn_revert.clicked.connect(self.revert)
        save = QPushButton("Сохранить и применить")
        save.setObjectName("Primary")
        save.setToolTip("Ctrl+S")
        save.clicked.connect(lambda: self.save())
        fl.addWidget(self.status, 1)
        fl.addWidget(self.btn_revert)
        fl.addWidget(save)
        root.addWidget(foot)
        self._update_status()

    # ── встроенная панель / отдельное окно ── #

    def docked(self) -> bool:
        return bool(getattr(self.win, "studio_docked", lambda: False)())

    def _sync_dock_button(self):
        d = self.docked()
        self.btn_dock.setText(_tr("Окном") if d else _tr("Встроить в плеер"))
        self.btn_dock.setToolTip("Открепить: конструктор в отдельном окне" if d else
                                 "Встроить конструктор панелью в окно плеера")
        if hasattr(self, "btn_close_dock"):
            self.btn_close_dock.setVisible(d)

    def _toggle_dock(self):
        fn = getattr(self.win, "set_studio_docked", None)
        if fn is not None:
            fn(not self.docked())
        self._sync_dock_button()
        if self.canvas is not None:
            QTimer.singleShot(50, self.canvas._follow)

    def _scroll(self, inner: QWidget):
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sa.setWidget(inner)
        return sa

    @staticmethod
    def _page():
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(14, 14, 14, 18)
        v.setSpacing(12)
        return w, v

    def go(self, key):
        self.nav.setCurrentRow(self._page_index.get(key, 0))

    def _place(self):
        """Рядом с окном плеера — справа, если есть место."""
        if self.docked():
            return
        try:
            g = self.win.frameGeometry()
            scr = self.win.screen().availableGeometry()
            w, h = self.width(), min(self.height(), scr.height() - 40)
            x = g.right() + 10 if g.right() + 10 + w <= scr.right() else max(scr.left(), g.right() - w - 10)
            y = max(scr.top() + 10, min(g.top(), scr.bottom() - h))
            self.setGeometry(x, y, w, h)
        except Exception:                                  # noqa: BLE001
            pass

    # ── страница «Старт» ── #

    def _page_start(self):
        w, v = self._page()
        hero, hv = _card()
        t1 = QLabel("Тема, которой нет ни у кого")
        t1.setObjectName("H1")
        hv.addWidget(t1)
        d = QLabel("Цвета, шрифты, форма кнопок и панелей, фон из картинки, GIF или видео, живые обои, "
                   "которые двигаются под музыку, стикеры и надписи где угодно, частицы и эффекты. "
                   "Всё сразу видно в плеере, а сохраняется кнопкой внизу.")
        d.setObjectName("Hint")
        d.setWordWrap(True)
        hv.addWidget(d)
        b1 = QPushButton("Удиви меня")
        b1.setObjectName("Primary")
        b1.clicked.connect(self.surprise)
        b2 = QPushButton("+ С чистого листа")
        b2.setToolTip("Тёмная основа обычного интерфейса плеера")
        b2.clicked.connect(lambda: self.new_from(tk.templates()[0]))
        b4 = QPushButton("Пустой холст")
        b4.setToolTip("Интерфейс плеера спрятан, слоёв нет — всё (кнопки, перемотку, пластинку…) "
                      "собираете сами из виджетов, картинок, видео и скриптов")
        b4.clicked.connect(lambda: self.new_from(tk.blank_canvas()))
        b3 = QPushButton("Открыть файл темы…")
        b3.setToolTip("Тема от друга: файл .echoestheme со всеми картинками, или тема LOOM (.echo)")
        b3.clicked.connect(self.import_theme)
        hv.addWidget(_row(b1, b2, b4, stretch_last=True))
        hv.addWidget(_row(b3, stretch_last=True))
        v.addWidget(hero)
        tc, tv = _card("Шаблоны", "Начните с готовой темы и переделайте под себя — шаблон не изменится.")
        grid = QGridLayout()
        grid.setSpacing(10)
        for i, tpl in enumerate(tk.templates()):
            b = QToolButton()
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            b.setIconSize(QSize(184, 115))
            b.setText(tpl["name"])
            b.setToolTip(tpl.get("description", ""))
            b.clicked.connect(lambda _=False, x=tpl: self.new_from(x))
            self._thumb_later(b, tpl, 184, 115)
            grid.addWidget(b, i // 2, i % 2)
        tv.addLayout(grid)
        v.addWidget(tc)
        mc, self._mine_v = _card("Мои темы")
        self._mine_box = QWidget()
        self._mine_l = QVBoxLayout(self._mine_box)
        self._mine_l.setContentsMargins(0, 0, 0, 0)
        self._mine_l.setSpacing(8)
        self._mine_v.addWidget(self._mine_box)
        v.addWidget(mc)
        v.addStretch(1)
        self._refresh_mine()
        return w

    def _thumb_later(self, btn, theme, w, h):
        """Миниатюры считаются после показа окна, по одной — окно открывается мгновенно."""
        key = (theme.get("id") or theme["name"], w, h, json.dumps(theme, sort_keys=True, ensure_ascii=False)[:2000])

        def make():
            try:
                pm = self._thumbs.get(key)
                if pm is None:
                    pm = self._thumbs[key] = theme_thumbnail(theme, self.store, w, h)
                btn.setIcon(QIcon(pm))
            except RuntimeError:
                pass
        QTimer.singleShot(30 + 25 * len(self._thumbs), make)

    def _refresh_mine(self):
        self._refresh_lib_mine()
        while self._mine_l.count():
            it = self._mine_l.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        themes = self.store.list()
        if not themes:
            e = QLabel("Пока нет сохранённых тем. Выберите шаблон или нажмите «Удиви меня».")
            e.setObjectName("Hint")
            e.setWordWrap(True)
            self._mine_l.addWidget(e)
            return
        cur = self.win.current_custom_theme_id()
        for t in themes:
            row = QFrame()
            row.setObjectName("Card")
            h = QHBoxLayout(row)
            h.setContentsMargins(8, 8, 8, 8)
            h.setSpacing(10)
            th = QLabel()
            th.setFixedSize(112, 70)
            self._thumb_label_later(th, t)
            col = QVBoxLayout()
            nm = QLabel(("● " if t["id"] == cur else "") + t["name"])
            nm.setStyleSheet("font-weight:700;")
            ds = QLabel(t.get("description") or f"Слоёв: {len(t['layers'])}")
            ds.setObjectName("Hint")
            ds.setWordWrap(True)
            col.addWidget(nm)
            col.addWidget(ds)
            col.addStretch(1)
            edit = QPushButton("Изменить")
            edit.clicked.connect(lambda _=False, i=t["id"]: self.open_theme(i))
            more = QPushButton("⋯")
            more.setObjectName("Tool")
            more.clicked.connect(lambda _=False, x=t, b=more: self._mine_menu(x, b))
            h.addWidget(th)
            h.addLayout(col, 1)
            h.addWidget(edit)
            h.addWidget(more)
            self._mine_l.addWidget(row)

    def _thumb_label_later(self, lbl, theme):
        def make():
            try:
                lbl.setPixmap(theme_thumbnail(theme, self.store, 112, 70))
            except RuntimeError:
                pass
        QTimer.singleShot(60, make)

    def _mine_menu(self, t, btn):
        m = QMenu(self)
        m.addAction("Применить в плеере", lambda: self._apply_saved(t["id"]))
        m.addAction("Сделать копию", lambda: self._duplicate_saved(t))
        m.addAction("Экспорт в файл…", lambda: self._export(t))
        m.addSeparator()
        m.addAction("Удалить", lambda: self._delete_saved(t))
        m.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _apply_saved(self, tid):
        self.win.custom_themes_changed(select_id=tid)
        self._refresh_mine()

    def _duplicate_saved(self, t):
        names = {x["name"] for x in self.store.list()}
        self.store.duplicate(t, tk.suggest_name(names, t["name"] + _tr(" (копия)")))
        self.win.custom_themes_changed()
        self._refresh_mine()

    def _delete_saved(self, t):
        if QMessageBox.question(self, "Удалить тему", f"Удалить тему «{t['name']}» вместе с её картинками?") != \
                QMessageBox.StandardButton.Yes:
            return
        self.store.delete(t["id"])
        if self.t.get("id") == t["id"]:
            self._saved = None
            self.t["id"] = ""
        self.win.custom_themes_changed()
        self._refresh_mine()
        self._update_status()

    # ── страница «Цвета» ── #

    def _page_colors(self):
        w, v = self._page()
        c, cv = _card("Главные цвета", "Пять цветов задают всю тему. Правый клик по образцу — копировать/вставить.")
        g = QGridLayout()
        g.setHorizontalSpacing(18)
        g.setVerticalSpacing(8)
        items = [("bg", "Фон"), ("text", "Текст"), ("accent", "Акцент"), ("accent2", "Второй акцент"),
                 ("glow", "Свечение")]
        tips = {"bg": "Основа: фон окна и тон панелей", "text": "Цвет текста",
                "accent": "Кнопки, выделение, ползунки", "accent2": "Градиенты и подсветки",
                "glow": "Свечение фона (тип «Свечение») и мягкие ореолы"}
        for i, (key, label) in enumerate(items):
            g.addWidget(self._color(("palette", key), label, tip=tips[key], on_change=self._refresh_contrast),
                        i // 2, i % 2)
        cv.addLayout(g)
        auto = QPushButton("Подобрать остальные цвета")
        auto.setToolTip("Панели, рамки, приглушённый текст — из фона, текста и акцентов")
        auto.clicked.connect(self._derive_palette)
        swap = QPushButton("Акценты")
        swap.setToolTip("Поменять акценты местами")
        swap.clicked.connect(self._swap_accents)
        cv.addWidget(_row(auto, swap, stretch_last=True))
        v.addWidget(c)
        h, hv = _card("Гармонии", "Второй акцент по правилам цветового круга от первого.")
        btns = []
        for mode, text in tk.HARMONIES:
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, m=mode: self._harmony(m))
            btns.append(b)
        hv.addWidget(_row(*btns[:3], stretch_last=True))
        hv.addWidget(_row(*btns[3:], stretch_last=True))
        v.addWidget(h)
        im, iv = _card("Палитра из картинки", "Главные цвета картинки → вся палитра темы.")
        b1 = QPushButton("Из картинки…")
        b1.clicked.connect(self._palette_from_file)
        b2 = QPushButton("Из обложки трека")
        b2.clicked.connect(self._palette_from_cover)
        b3 = QPushButton("Из фона темы")
        b3.setToolTip("Если фон — картинка или GIF")
        b3.clicked.connect(self._palette_from_bg)
        iv.addWidget(_row(b1, b2, b3, stretch_last=True))
        self._dark_pref = QCheckBox("Тёмная тема")
        self._dark_pref.setChecked(not tk.is_light(self.t["palette"]["bg"]))
        iv.addWidget(self._dark_pref)
        iv.addWidget(self._check(("palette", "cover_accent"), "Акцент подстраивается под обложку играющего трека",
                                 tip="Каждый трек — свои акцентные цвета, остальное из темы"))
        v.addWidget(im)
        k, self._contrast_v = _card("Читаемость")
        self._contrast_lbl = []
        for _ in range(3):
            lb = QLabel()
            lb.setWordWrap(True)
            self._contrast_v.addWidget(lb)
            self._contrast_lbl.append(lb)
        v.addWidget(k)
        a, av = _card("Тонкая настройка")
        for key, label in (("panel", "Панели"), ("panel2", "Списки, меню"), ("muted", "Приглушённый текст"),
                           ("danger", "Предупреждения")):
            av.addWidget(self._color(("palette", key), label, on_change=self._refresh_contrast))
        v.addWidget(a)
        v.addStretch(1)
        self._refresh_contrast()
        return w

    def _refresh_contrast(self):
        if not hasattr(self, "_contrast_lbl"):
            return
        p = self.t["palette"]
        rows = [("Текст на фоне", p["text"], p["bg"], 4.5), ("Акцент на фоне", p["accent"], p["bg"], 3.0),
                ("Приглушённый текст", p["muted"], p["bg"], 2.5)]
        for lb, (name, a, b, need) in zip(self._contrast_lbl, rows):
            r = tk.contrast(a, b)
            ok = r >= need
            lb.setObjectName("Good" if ok else "Warn")
            lb.setText(f"{_tr(name)}: {r:.1f}:1" + ("" if ok else _tr(" — может плохо читаться")))
            lb.style().unpolish(lb)
            lb.style().polish(lb)

    def _apply_palette(self, pal, keep=()):
        for k2, v2 in pal.items():
            if k2 in keep or k2 == "cover_accent":
                continue
            self.t["palette"][k2] = v2
        self._refresh_all()
        self._refresh_contrast()
        self.changed("full")

    def _derive_palette(self):
        p = self.t["palette"]
        self._apply_palette(tk.derive_palette(p["bg"], p["accent"], p["text"], p["accent2"], p["glow"]))

    def _swap_accents(self):
        p = self.t["palette"]
        p["accent"], p["accent2"] = p["accent2"], p["accent"]
        self._refresh_all()
        self._refresh_contrast()
        self.changed("full")

    def _harmony(self, mode):
        a1, a2 = tk.harmony(self.t["palette"]["accent"], mode)
        p = self.t["palette"]
        p["accent"], p["accent2"] = a1, a2
        self._refresh_all()
        self._refresh_contrast()
        self.changed("full")

    def _palette_from_path(self, path):
        pal = tk.palette_from_image(path, prefer_dark=self._dark_pref.isChecked()) if path else None
        if not pal:
            QMessageBox.information(self, "Палитра", "Не удалось взять цвета из картинки.")
            return
        self._apply_palette(pal)

    def _palette_from_file(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Картинка для палитры", "", IMAGE_FILTER)
        if fn:
            self._palette_from_path(fn)

    def _palette_from_cover(self):
        c = getattr(self.win, "_cur_cover", None)
        if not c:
            QMessageBox.information(self, "Палитра", "У играющего трека нет обложки.")
            return
        self._palette_from_path(c)

    def _palette_from_bg(self):
        b = self.t["background"]
        path = self.store.asset(self.t, b.get("media")) if b.get("media") else ""
        if not path or Path(path).suffix.lower() not in _IMG_EXT:
            QMessageBox.information(self, "Палитра", "Фон темы — не картинка. Выберите картинку на странице «Фон».")
            return
        self._palette_from_path(path)

    # ── страница «Текст» ── #

    def _page_text(self):
        w, v = self._page()
        c, cv = _card("Основной шрифт", "Весь интерфейс: списки, кнопки, подписи.")
        self.font_cb = QFontComboBox()
        self.font_cb.setEditable(False)
        self.font_cb.currentFontChanged.connect(lambda f: self._set_font_family("family", "file", f.family()))
        self._font_file_lbl = QLabel()
        self._font_file_lbl.setObjectName("Hint")
        up = QPushButton("Свой шрифт файлом…")
        up.setToolTip("TTF / OTF — шрифт сохранится внутри темы")
        up.clicked.connect(lambda: self._load_font("file", "family"))
        cv.addWidget(self.font_cb)
        cv.addWidget(_row(self._font_file_lbl, None, up))
        cv.addWidget(self._slider(("font", "size"), "Размер", 9, 22, 1, "{:.0f} px", cast=int))
        cv.addWidget(_row(QLabel("Насыщенность"), None, self._combo(("font", "weight"), WEIGHTS)))
        cv.addWidget(self._slider(("font", "spacing"), "Межбуквенный", -1, 6, 0.1, "{:.1f} px",
                                  tip="Расстояние между буквами"))
        v.addWidget(c)
        t, tv = _card("Заголовки", "Название трека, ник в профиле, заголовки разделов.")
        self.title_cb = QFontComboBox()
        self.title_cb.setEditable(False)
        self.title_cb.currentFontChanged.connect(lambda f: self._set_font_family("title_family", "title_file",
                                                                                  f.family()))
        self._title_file_lbl = QLabel()
        self._title_file_lbl.setObjectName("Hint")
        up2 = QPushButton("Свой шрифт файлом…")
        up2.clicked.connect(lambda: self._load_font("title_file", "title_family"))
        tv.addWidget(self.title_cb)
        tv.addWidget(_row(self._title_file_lbl, None, up2))
        tv.addWidget(self._slider(("font", "title_size"), "Размер", 12, 64, 1, "{:.0f} px", cast=int))
        tv.addWidget(_row(QLabel("Насыщенность"), None, self._combo(("font", "title_weight"), WEIGHTS)))
        v.addWidget(t)
        pc, pv = _card("Как это выглядит")
        self._font_demo_t = QLabel("Название трека")
        self._font_demo_b = QLabel("Исполнитель · Альбом · 3:42")
        pv.addWidget(self._font_demo_t)
        pv.addWidget(self._font_demo_b)
        v.addWidget(pc)
        v.addStretch(1)
        self._refresh_font_labels()
        return w

    def _set_font_family(self, fam_key, file_key, family):
        if getattr(self, "_font_sync", False):
            return
        f = self.t["font"]
        f[fam_key] = family
        f[file_key] = ""
        self._refresh_font_labels()
        self.changed("full")

    def _load_font(self, file_key, fam_key):
        fn, _ = QFileDialog.getOpenFileName(self, "Шрифт", "", FONT_FILTER)
        if not fn:
            return
        rel = self.store.import_asset(self.t, fn)
        fid = QFontDatabase.addApplicationFont(self.store.asset(self.t, rel))
        fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
        if not fams:
            QMessageBox.warning(self, "Шрифт", "Этот файл не похож на шрифт.")
            return
        self.t["font"][file_key] = rel
        self.t["font"][fam_key] = fams[0]
        self._refresh_font_labels()
        self.changed("full")

    def _refresh_font_labels(self):
        if not hasattr(self, "font_cb"):
            return
        f = self.t["font"]
        self._font_sync = True
        try:
            for cb, fam in ((self.font_cb, f.get("family")), (self.title_cb, f.get("title_family"))):
                cb.blockSignals(True)
                if fam:
                    cb.setCurrentFont(QFont(fam))
                cb.blockSignals(False)
        finally:
            self._font_sync = False
        self._font_file_lbl.setText(f"Файл: {Path(f['file']).name}" if f.get("file") else "Системный шрифт")
        self._title_file_lbl.setText(f"Файл: {Path(f['title_file']).name}" if f.get("title_file") else
                                     "Системный шрифт")
        tf = QFont(f.get("title_family") or f.get("family") or self.font().family())
        tf.setPixelSize(int(f["title_size"]))
        tf.setWeight(QFont.Weight(int(f["title_weight"])))
        self._font_demo_t.setFont(tf)
        bf = QFont(f.get("family") or self.font().family())
        bf.setPixelSize(int(f["size"]))
        bf.setWeight(QFont.Weight(int(f["weight"])))
        bf.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, float(f.get("spacing", 0)))
        self._font_demo_b.setFont(bf)

    # ── страница «Формы» ── #

    def _page_shape(self):
        w, v = self._page()
        c, cv = _card("Скругления и рамки")
        cv.addWidget(self._slider(("shape", "radius"), "Панели", 0, 48, 1, "{:.0f} px", cast=int))
        cv.addWidget(self._slider(("shape", "btn_radius"), "Кнопки и поля", 0, 30, 1, "{:.0f} px", cast=int))
        cv.addWidget(self._slider(("shape", "border"), "Толщина рамок", 0, 4, 1, "{:.0f} px", cast=int))
        v.addWidget(c)
        b, bv = _card("Кнопки")
        opts = [(k, t, button_icon(k), "") for k, t in tk.BUTTON_STYLES]
        bv.addWidget(self._cards(("shape", "btn_style"), opts, cols=3))
        v.addWidget(b)
        g, gv = _card("Панели", "Плотность: 0 — прозрачное стекло, 1 — сплошная панель.")
        gv.addWidget(self._slider(("shape", "glass"), "Плотность", 0, 1, 0.01, "{:.2f}"))
        gv.addWidget(self._check(("shape", "frosted"), "Матовое стекло (фон под панелями размыт)"))
        gv.addWidget(self._slider(("shape", "blur"), "Размытие", 0, 60, 1, "{:.0f}", cast=int))
        gv.addWidget(self._slider(("shape", "shadow"), "Тень", 0, 1, 0.01, "{:.2f}"))
        v.addWidget(g)
        s, sv = _card("Отступы")
        sv.addWidget(self._slider(("shape", "margin"), "От краёв окна", 0, 48, 1, "{:.0f} px", cast=int))
        sv.addWidget(self._slider(("shape", "spacing"), "Между панелями", 0, 40, 1, "{:.0f} px", cast=int))
        v.addWidget(s)
        v.addStretch(1)
        return w

    # ── страница «Фон» ── #

    def _page_bg(self):
        w, v = self._page()
        c, cv = _card("Тип фона", "Живые обои двигаются сами и реагируют на музыку. Картинку или GIF можно "
                                  "просто перетащить на это окно.")
        opts = []
        short = {"glow": "Свечение", "color": "Цвет", "gradient": "Градиент", "media": "Картинка",
                 "video": "Видео", "cover": "Обложка", "aurora": "Сияние", "synthwave": "Ретровейв",
                 "plasma": "Плазма", "starfield": "Варп", "matrix": "Матрица", "nebula": "Туманность"}
        for k2, text in tk.BACKGROUND_TYPES:
            opts.append((k2, short.get(k2, text), None, text))
        self._bg_cards = self._cards(("background", "type"), opts, cols=3, on_change=self._on_bg_type)
        cv.addWidget(self._bg_cards)
        v.addWidget(c)
        QTimer.singleShot(80, self._bg_icons)
        self._bg_box_card, self._bg_box_v = _card("Настройки фона")
        v.addWidget(self._bg_box_card)
        o, ov = _card("Поверх фона")
        ov.addWidget(self._slider(("background", "dim"), "Затемнение", 0, 1, 0.01, "{:.2f}",
                                  tip="Для картинок, видео и живых обоев"))
        ov.addWidget(self._color(("background", "tint"), "Оттенок"))
        ov.addWidget(self._slider(("background", "tint_amount"), "Сила оттенка", 0, 1, 0.01, "{:.2f}"))
        ov.addWidget(self._slider(("background", "vignette"), "Виньетка", 0, 1, 0.01, "{:.2f}"))
        ov.addWidget(self._slider(("background", "parallax"), "Параллакс", 0, 1, 0.01, "{:.2f}",
                                  tip="Фон чуть сдвигается за мышью — эффект глубины"))
        v.addWidget(o)
        s, sv = _card("Скрипт фона", "Своя логика фона на EchoScript: видео/GIF/живые обои быстрее или медленнее "
                                     "под бит, зум от баса, тряска, затемнение на паузе — что угодно.")
        self._bg_script_lbl = QLabel()
        self._bg_script_lbl.setObjectName("Hint")
        self._bg_script_lbl.setWordWrap(True)
        sv.addWidget(self._bg_script_lbl)
        ed = QPushButton("Скрипт фона…")
        ed.setObjectName("Primary")
        ed.clicked.connect(lambda: self.open_code("__bg__", "bg"))
        snip = QPushButton("Готовое ▾")
        menu = QMenu(snip)
        menu.setStyleSheet(STUDIO_QSS)
        for title, code in layer_fx.BG_SNIPPETS:
            menu.addAction(_tr(title), lambda c=code: self._add_bg_snippet(c))
        snip.clicked.connect(lambda: menu.exec(snip.mapToGlobal(snip.rect().bottomLeft())))
        rm = QPushButton("Убрать")
        rm.setObjectName("Danger")
        rm.clicked.connect(lambda: self._set_bg_script(""))
        sv.addWidget(_row(ed, snip, rm, stretch_last=True))
        self._bind("", self._sync_bg_script_lbl)
        self._sync_bg_script_lbl()
        v.addWidget(s)
        v.addStretch(1)
        self._rebuild_bg_box()
        return w

    def _sync_bg_script_lbl(self):
        if hasattr(self, "_bg_script_lbl"):
            on = bool((self.t["background"].get("script") or "").strip())
            self._bg_script_lbl.setText(_tr("Скрипт фона работает.") if on else
                                        _tr("Скрипта нет — фон как настроен выше."))

    def _set_bg_script(self, code):
        self.t["background"]["script"] = code[:tk.MAX_SCRIPT]
        self.changed("layers")
        self._commit_soon()
        self._sync_bg_script_lbl()
        dlg = self._code.get(("__bg__", "bg"))
        if dlg is not None:
            try:
                dlg.reload()
            except RuntimeError:
                pass

    def _add_bg_snippet(self, code):
        cur = (self.t["background"].get("script") or "").rstrip()
        self._set_bg_script((cur + "\n" if cur else "") + code + "\n")

    def _bg_icons(self):
        try:
            for k2, _ in tk.BACKGROUND_TYPES:
                self._bg_cards.set_icon(k2, bg_icon(k2, self.t))
        except RuntimeError:
            pass

    def _on_bg_type(self):
        b = self.t["background"]
        if b["type"] in ("media", "video") and b.get("media"):
            ext = Path(b["media"]).suffix.lower()
            if (b["type"] == "video") != (ext in _VID_EXT):
                b["media"] = ""                             # картинка не годится для видео и наоборот
        self._rebuild_bg_box()
        self.changed("full")

    def _rebuild_bg_box(self):
        if not hasattr(self, "_bg_box_v"):
            return
        self._unbind("bg")
        v = self._bg_box_v
        while v.count() > 1:
            it = v.takeAt(1)
            if it.widget():
                it.widget().deleteLater()
        b = self.t["background"]
        ty = b["type"]
        add = v.addWidget
        P = ("background",)
        if ty == "color":
            add(self._color(P + ("color",), "Цвет фона", tag="bg"))
        elif ty == "glow":
            add(self._hint("Мягкое свечение из угла, как в Vinyl glass. Цвет — «Свечение» на странице «Цвета»."))
            add(self._color(("palette", "glow"), "Цвет свечения", tag="bg"))
        elif ty == "gradient":
            add(_row(QLabel("Вид"), None, self._combo(P + ("gradient", "kind"), tk.GRADIENT_KINDS, tag="bg")))
            add(self._slider(P + ("gradient", "angle"), "Угол", -180, 180, 1, "{:.0f}°", tag="bg"))
            add(self._slider(P + ("gradient", "spin"), "Вращение", -1, 1, 0.01, "{:.2f}", tag="bg",
                             tip="Градиент медленно поворачивается"))
            add(self._stops_editor())
            pres = []
            for name, stops in GRADIENT_PRESETS:
                bt = QPushButton(name)
                bt.clicked.connect(lambda _=False, s=stops: self._set_stops(s))
                pres.append(bt)
            add(self._hint("Готовые:"))
            add(_row(*pres[:3], stretch_last=True))
            add(_row(*pres[3:], stretch_last=True))
        elif ty == "media":
            add(self._file(P + ("media",), "image", tag="bg", on_change=self._bg_icons_soon))
            add(_row(QLabel("Как вписать"), None, self._combo(P + ("fit",), tk.FIT_MODES, tag="bg")))
            add(self._slider(P + ("zoom",), "Масштаб", 0.5, 4, 0.01, "{:.2f}×", tag="bg"))
            add(self._slider(P + ("align_x",), "Сдвиг по X", 0, 1, 0.01, "{:.2f}", tag="bg"))
            add(self._slider(P + ("align_y",), "Сдвиг по Y", 0, 1, 0.01, "{:.2f}", tag="bg"))
            add(self._slider(P + ("blur",), "Размытие", 0, 80, 1, "{:.0f}", tag="bg"))
            add(self._slider(P + ("saturation",), "Насыщенность", 0, 2, 0.01, "{:.2f}", tag="bg"))
            add(self._slider(P + ("speed",), "Скорость GIF", 0.1, 4, 0.05, "{:.2f}×", tag="bg"))
        elif ty == "video":
            add(self._file(P + ("media",), "video", tag="bg"))
            add(self._hint("Видео играет по кругу без звука. Ролики 720p и меньше — легче для компьютера."))
            add(_row(QLabel("Как вписать"), None, self._combo(P + ("fit",), [f for f in tk.FIT_MODES
                                                                              if f[0] != "tile"], tag="bg")))
            add(self._slider(P + ("zoom",), "Масштаб", 0.5, 4, 0.01, "{:.2f}×", tag="bg"))
            add(self._slider(P + ("speed",), "Скорость", 0.25, 4, 0.05, "{:.2f}×", tag="bg"))
        elif ty == "cover":
            add(self._hint("Размытая обложка играющего трека — фон меняется вместе с музыкой."))
            add(self._slider(P + ("blur",), "Размытие", 0, 80, 1, "{:.0f}", tag="bg"))
            add(self._slider(P + ("saturation",), "Насыщенность", 0, 2, 0.01, "{:.2f}", tag="bg"))
        else:                                              # живые обои
            add(self._hint({"aurora": "Ленты северного сияния и мягкие пятна света.",
                            "synthwave": "Неоновое солнце и сетка, бегущая к горизонту.",
                            "plasma": "Тягучая плазма, вскипает от баса.",
                            "starfield": "Звёзды летят навстречу — чем громче, тем быстрее.",
                            "matrix": "Цифровой дождь из символов.",
                            "nebula": "Многоцветная туманность, как «Моя волна» в Яндекс Музыке: облака "
                                      "перетекают, по ним бегут нити света, на удар — вспышка. Цвета — до четырёх."
                            }.get(ty, "")))
            add(self._live_colors())
            add(self._slider(P + ("speed",), "Скорость", 0, 4, 0.05, "{:.2f}×", tag="bg"))
            add(self._slider(P + ("react",), "Реакция на музыку", 0, 1, 0.01, "{:.2f}", tag="bg"))

    def _bg_icons_soon(self):
        QTimer.singleShot(50, self._bg_icons)

    def _hint(self, text):
        lb = QLabel(text)
        lb.setObjectName("Hint")
        lb.setWordWrap(True)
        return lb

    def _stops_editor(self):
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        stops = self.t["background"]["gradient"]["stops"]
        for i, (pos, col) in enumerate(stops):
            cb = ColorButton(col)
            cb.changed.connect(lambda c, i=i: self._stop_set(i, 1, c))
            sl = SliderRow(f"Точка {i + 1}", 0, 1, pos, 0.01, "{:.2f}")
            sl.changed.connect(lambda val, i=i: self._stop_set(i, 0, round(val, 3), rebuild=False))
            sl.released.connect(self._commit_soon)
            rm = QPushButton("×")
            rm.setObjectName("Tool")
            rm.setEnabled(len(stops) > 2)
            rm.clicked.connect(lambda _=False, i=i: self._stop_remove(i))
            v.addWidget(_row(cb, sl, rm))
            v.itemAt(v.count() - 1).widget().layout().setStretch(1, 1)
        add = QPushButton("+ точка")
        add.setEnabled(len(stops) < 8)
        add.clicked.connect(self._stop_add)
        v.addWidget(_row(add, stretch_last=True))
        return box

    def _stop_set(self, i, j, val, rebuild=True):
        stops = self.t["background"]["gradient"]["stops"]
        if i < len(stops):
            stops[i][j] = val
            if j == 0:
                stops.sort(key=lambda s: s[0])
            self.changed("full")
            if rebuild and j == 1:
                self._bg_icons_soon()

    def _stop_add(self):
        stops = self.t["background"]["gradient"]["stops"]
        stops.append([1.0, self.t["palette"]["accent"]])
        stops.sort(key=lambda s: s[0])
        self._rebuild_bg_box()
        self.changed("full")

    def _stop_remove(self, i):
        stops = self.t["background"]["gradient"]["stops"]
        if len(stops) > 2:
            stops.pop(i)
            self._rebuild_bg_box()
            self.changed("full")

    def _set_stops(self, stops):
        self.t["background"]["gradient"]["stops"] = copy.deepcopy(stops)
        self._rebuild_bg_box()
        self._bg_icons_soon()
        self.changed("full")

    def _live_colors(self):
        box = QWidget()
        h = QHBoxLayout(box)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        h.addWidget(QLabel("Цвета"))
        h.addStretch(1)
        cols = self.t["background"]["colors"]
        for i, col in enumerate(cols):
            cb = ColorButton(col)
            cb.setFixedSize(40, 28)
            cb.changed.connect(lambda c, i=i: self._live_color_set(i, c))
            h.addWidget(cb)
        add = QPushButton("+")
        add.setObjectName("Tool")
        add.setEnabled(len(cols) < 6)
        add.clicked.connect(lambda: self._live_color_count(+1))
        rm = QPushButton("−")
        rm.setObjectName("Tool")
        rm.setEnabled(len(cols) > 3)
        rm.clicked.connect(lambda: self._live_color_count(-1))
        h.addWidget(add)
        h.addWidget(rm)
        return box

    def _live_color_set(self, i, c):
        cols = self.t["background"]["colors"]
        if i < len(cols) and c:
            cols[i] = c
            self.changed("full")
            self._bg_icons_soon()

    def _live_color_count(self, d):
        cols = self.t["background"]["colors"]
        if d > 0 and len(cols) < 6:
            cols.append(tk.shift_hue(cols[-1], 47))
        elif d < 0 and len(cols) > 3:
            cols.pop()
        self._rebuild_bg_box()
        self.changed("full")

    # ── страница «Слои» ── #

    def _page_layers(self):
        w, v = self._page()
        c, cv = _card("Слои", "Картинки, GIF, видео, надписи, фигуры, виджеты (кнопки, перемотка, пластинка, "
                              "спектр…) и эффекты тем — поверх интерфейса или на фоне. У каждого слоя может быть "
                              "несколько анимаций сразу и свой скрипт. Расставлять удобнее прямо на окне плеера.")
        self.btn_canvas = QPushButton("Расставить на экране")
        self.btn_canvas.setObjectName("Primary")
        self.btn_canvas.setCheckable(True)
        self.btn_canvas.setToolTip("Слои двигаются мышью прямо на плеере")
        self.btn_canvas.toggled.connect(self.set_canvas)
        cv.addWidget(self.btn_canvas)
        a1 = QPushButton("+ Картинка / GIF")
        a1.clicked.connect(lambda: self.add_layer("media"))
        a4 = QPushButton("+ Видео")
        a4.clicked.connect(lambda: self.add_layer("video"))
        a2 = QPushButton("+ Текст")
        a2.clicked.connect(lambda: self.add_layer("text"))
        a3 = QPushButton("+ Фигура")
        a3.clicked.connect(lambda: self.add_layer("shape"))
        cv.addWidget(_row(a1, a4, a2, a3, stretch_last=True))
        a5 = QPushButton("+ Виджет ▾")
        a5.setToolTip("Кнопки, перемотка, громкость, пластинка, обложка, текст песни, список треков, спектры…")
        a5.clicked.connect(lambda: self.widget_menu(self).exec(a5.mapToGlobal(a5.rect().bottomLeft())))
        a6 = QPushButton("+ Эффект ▾")
        a6.setToolTip("MilkDrop из Winamp, жидкость из Fluid, «Моя волна» из Echoes Music, живые обои…")
        a6.clicked.connect(lambda: self.effect_menu(self).exec(a6.mapToGlobal(a6.rect().bottomLeft())))
        a7 = QPushButton("+ Пустой скрипт")
        a7.setToolTip("Свой виджет на EchoScript (бывший LOOM): рисуйте что угодно, реагируйте на звук и мышь")
        a7.clicked.connect(self.add_blank_script)
        cv.addWidget(_row(a5, a6, a7, stretch_last=True))
        self.layer_list = QListWidget()
        self.layer_list.setObjectName("Layers")
        self.layer_list.setMinimumHeight(150)
        self.layer_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.layer_list.currentRowChanged.connect(self._on_layer_row)
        self.layer_list.model().rowsMoved.connect(self._on_layers_reordered)
        self.layer_list.itemDoubleClicked.connect(lambda _it: self.set_canvas(True))
        cv.addWidget(self.layer_list)
        tools = []
        for text, tip, fn in (("↑", "Выше", lambda: self.move_layer(1)), ("↓", "Ниже", lambda: self.move_layer(-1)),
                              ("⧉", "Копия", self.duplicate_layer),
                              ("◉", "Показать / скрыть", lambda: self.toggle_layer_flag("visible")),
                              ("Замок", "Закрепить (нельзя сдвинуть мышью)", lambda: self.toggle_layer_flag("locked")),
                              ("Удалить", "Удалить слой", self.delete_layer)):
            b = QPushButton(text)
            b.setObjectName("Tool" if text != "Удалить" else "Danger")
            b.setToolTip(tip)
            b.clicked.connect(fn)
            tools.append(b)
        cv.addWidget(_row(*tools, stretch_last=True, spacing=6))
        v.addWidget(c)
        n, nv = _card("Элементы плеера на окне", "Настоящие винил, визуализатор, кнопки, библиотека, плейлисты… — "
                                                 "добавляются как виджеты («+ Элемент плеера» или страница "
                                                 "«Библиотека»), клик — выбрать, Del на окне — убрать.")
        self.native_list = QListWidget()
        self.native_list.setObjectName("Layers")
        self.native_list.setMinimumHeight(150)
        self.native_list.itemClicked.connect(self._on_native_item)
        nv.addWidget(self.native_list)
        addn = QPushButton("+ Элемент плеера ▾")
        addn.clicked.connect(lambda: self.native_menu(self).exec(addn.mapToGlobal(addn.rect().bottomLeft())))
        rm = QPushButton("Убрать выбранный")
        rm.setObjectName("Danger")
        rm.clicked.connect(lambda: self._nsel and (self.set_vinyl_bg({"on": False}) if self._nsel == "vinyl_bg"
                                                   else self.hide_native(self._nsel)))
        rs = QPushButton("Все — как было")
        rs.setToolTip("Вернуть все элементы плеера на их обычные места")
        rs.clicked.connect(self.reset_natives)
        nv.addWidget(_row(addn, rm, rs, stretch_last=True))
        v.addWidget(n)
        QTimer.singleShot(0, self._refresh_native_list)
        self._layer_card, self._layer_v = _card("Свойства слоя")
        v.addWidget(self._layer_card)
        v.addStretch(1)
        self._refresh_layer_list()
        self._rebuild_layer_box()
        return w

    def _refresh_layer_list(self):
        if not hasattr(self, "layer_list"):
            return
        lw = self.layer_list
        lw.blockSignals(True)
        lw.clear()
        sel_row = -1
        kinds = {"media": _tr("картинка"), "text": _tr("текст"), "shape": _tr("фигура"), "video": _tr("видео"),
                 "script": _tr("виджет"), "effect": _tr("эффект")}
        many = set(self.selection_ids())
        # сверху — самые верхние слои (как в редакторах графики)
        for i, L in reversed(list(enumerate(self.t["layers"]))):
            vis = "◉" if L.get("visible", True) else "○"
            lock = _tr(" · закреплён") if L.get("locked") else ""
            where = _tr("поверх") if L["z"] == "front" else _tr("фон")
            n = len(L.get("anims") or [])
            anim = (" ✦" if n == 1 else f" ✦{n}") if n else ""
            code = " ƒ" if (L.get("behavior") or "").strip() else ""
            grp = f" · {_tr('группа')} {L['group']}" if L.get("group") else ""
            mark = "▸ " if L["id"] in many and L["id"] != self._sel else ""
            it = QListWidgetItem(f"{mark}{vis}  {L.get('name') or _tr('Слой')}  ·  {kinds.get(L['kind'], '')}, "
                                 f"{where}{anim}{code}{grp}{lock}")
            it.setData(Qt.ItemDataRole.UserRole, L["id"])
            lw.addItem(it)
            if L["id"] == self._sel:
                sel_row = lw.count() - 1
        lw.setCurrentRow(sel_row)
        lw.blockSignals(False)

    def _on_layer_row(self, row):
        it = self.layer_list.item(row)
        self.select_layer(it.data(Qt.ItemDataRole.UserRole) if it is not None else None, from_list=True)

    def _on_layers_reordered(self, *_):
        ids = [self.layer_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.layer_list.count())]
        by = {L["id"]: L for L in self.t["layers"]}
        self.t["layers"] = [by[i] for i in reversed(ids) if i in by]
        self.changed("layers")

    def select_layer(self, lid, from_list=False):
        """Выбрать слой (None — снять выделение). Слой в группе выделяет всю группу."""
        L = next((x for x in self.t["layers"] if x["id"] == lid), None) if lid else None
        multi = [x["id"] for x in self.t["layers"]
                 if L is not None and L.get("group") and x.get("group") == L["group"] and x["id"] != lid]
        if lid == self._sel and multi == self._multi and self._nsel is None:
            return
        self._sel = lid
        self._multi = multi
        self._nsel = None
        if not from_list:
            self._refresh_layer_list()
        self._rebuild_layer_box()
        if self.canvas is not None:
            self.canvas.update()

    # ── родные элементы интерфейса (пластинка, визуализатор, кнопки, панели…) ── #

    def _natives(self):
        try:
            return self.win.native_layout()
        except Exception:                                  # noqa: BLE001
            return None

    def select_native(self, key):
        self._nsel = key or None
        self._sel = None
        self._multi = []
        self._refresh_layer_list()
        self._refresh_native_list()
        self._rebuild_layer_box()
        if self.canvas is not None:
            self.canvas.update()

    def set_native(self, key, spec: dict, commit=True):
        """Место/размер/скрытие родного элемента. spec {} — вернуть как было."""
        nat = self.t.setdefault("native", {})
        if spec and (spec.get("free") or spec.get("hidden")):
            collapse = bool(spec.get("collapse", (nat.get(key) or {}).get("collapse", False)))
            nat[key] = {"free": bool(spec.get("free")), "hidden": bool(spec.get("hidden")),
                        **{k: float(spec.get(k, d)) for k, d in (("x", 0.1), ("y", 0.1), ("w", 0.2), ("h", 0.2))}}
            if collapse:
                nat[key]["collapse"] = True
        else:
            nat.pop(key, None)
        nl = self._natives()
        # на экране должен быть тот же вид, что правим (иначе раскладка режима текста легла бы на основной)
        same = getattr(self.win, "_lyrics_custom_on", lambda: False)() == (self._mode == "lyrics")
        if nl is not None and self.win._custom_on and same:
            nl.apply_one(key, nat.get(key) or {})
        if commit:
            self.native_committed()
        else:
            self._update_status()
            self._refresh_all("native")

    def native_committed(self):
        self._commit_soon()
        self._refresh_native_list()
        self._refresh_all("native")
        self._update_status()
        if self.canvas is not None:
            self.canvas.update()

    def hide_native(self, key):
        s = dict((self.t.get("native") or {}).get(key) or {})
        s["hidden"] = True
        self.set_native(key, s)
        if self._nsel == key:
            self._nsel = None
            self._rebuild_layer_box()

    def show_native(self, key):
        s = dict((self.t.get("native") or {}).get(key) or {})
        s["hidden"] = False
        self.set_native(key, s)

    def toggle_native_collapse(self, key):
        """Место вынутого/убранного элемента: держать пустым (соседи стоят) или отдать соседям."""
        s = dict((self.t.get("native") or {}).get(key) or {})
        if not (s.get("free") or s.get("hidden")):
            self.status.setText(_tr("Элемент стоит на своём месте — отдавать нечего"))
            return
        s["collapse"] = not s.get("collapse")
        self.set_native(key, s)

    # ── элементы плеера как виджеты библиотеки ── #

    # размер по умолчанию (доли окна), когда элемент ставят из библиотеки, а в раскладке его не видно
    NATIVE_SIZE = {"vinyl": (0.34, 0.5), "viz": (0.5, 0.16), "title": (0.4, 0.06), "artist": (0.3, 0.045),
                   "seek": (0.45, 0.05), "controls": (0.32, 0.09), "transport": (0.5, 0.2), "volume": (0.25, 0.05),
                   "lib_list": (0.26, 0.55), "lib_search": (0.24, 0.05), "lib_sort": (0.2, 0.05),
                   "playlists": (0.24, 0.3), "left": (0.26, 0.85), "right": (0.26, 0.85), "rp_nav": (0.26, 0.06),
                   "lyrics_box": (0.26, 0.3), "theme_cb": (0.2, 0.05), "lang": (0.16, 0.05),
                   "cover_colors": (0.22, 0.05), "btn_play": (0.06, 0.09)}
    NATIVE_GROUPS = [("Пластинка и звук", ("vinyl", "viz", "title", "artist")),
                     ("Управление", ("transport", "seek", "controls", "btn_play", "btn_prev", "btn_next",
                                     "btn_shuffle", "btn_artist_shuffle", "btn_repeat", "volume")),
                     ("Библиотека и плейлисты", ("left", "lib_list", "lib_search", "lib_sort", "playlists",
                                                 "btn_files", "btn_folder", "btn_download", "btn_profile")),
                     ("Кнопки режимов", ("btn_lyrics", "btn_queue", "btn_cover", "btn_hub")),
                     ("Правая панель", ("right", "rp_nav", "theme_cb", "btn_studio", "cover_colors", "lang",
                                        "lyrics_box", "btn_lyrics_edit"))]
    NATIVE_TITLES = {"vinyl": "Винил", "left": "Библиотека целиком (левая панель)", "lib_list": "Библиотека: список треков",
                     "transport": "Панель управления целиком", "controls": "Кнопки управления (ряд)"}

    def _native_title(self, key):
        if key in self.NATIVE_TITLES:
            return _tr(self.NATIVE_TITLES[key])
        nl = self._natives()
        return next((_tr(t_) for k, t_, _g, _w in (nl.items() if nl else []) if k == key), key)

    def native_on_screen(self, key) -> bool:
        """Элемент сейчас виден на окне (не убран темой и не спрятан режимом «только слои»)."""
        nl = self._natives()
        if nl is None:
            return False
        s = (self.t.get("native") or {}).get(key) or {}
        if s.get("hidden"):
            return False
        if s.get("free"):
            return True
        return nl.rect_in_window(key) is not None

    def native_menu(self, parent, menu=None, pos=None):
        """«+ Элемент плеера»: настоящие пластинка, визуализатор, кнопки, библиотека, плейлисты…"""
        m = menu or QMenu(parent)
        m.setStyleSheet(STUDIO_QSS)
        nl = self._natives()
        have = {k for k, _t, _g, _w in (nl.items() if nl else [])}
        for group, keys in self.NATIVE_GROUPS:
            keys = [k for k in keys if k in have]
            if not keys:
                continue
            sub = m.addMenu(_tr(group))
            for k in keys:
                on = self.native_on_screen(k)
                a = sub.addAction(self._native_title(k) + ("  ✓" if on else ""),
                                  lambda k=k: self.add_native(k, pos))
                a.setToolTip(_tr("Уже на окне — поставить свободно сюда") if on else _tr("Поставить на окно"))
        return m

    def add_native(self, key, pos=None):
        """Поставить родной элемент на окно (как виджет из библиотеки): показать его, вынуть из раскладки
        и поставить в pos (доли окна) или по центру. Уже стоит свободно — только выбрать."""
        nl = self._natives()
        if nl is None or nl.widget(key) is None:
            return
        if not self.win._custom_on:
            self.status.setText(_tr("Элементы ставятся в теме из конструктора — сначала откройте или создайте тему"))
            return
        same = getattr(self.win, "_lyrics_custom_on", lambda: False)() == (self._mode == "lyrics")
        cur = dict((self.t.get("native") or {}).get(key) or {})
        if cur.get("free") and not cur.get("hidden") and pos is None:
            self.select_native(key)
            return
        root = nl.root().geometry()
        r = nl.rect_in_window(key) if same else None
        if r is not None and r.width() > 8 and r.height() > 8:
            fw, fh = r.width() / max(1, root.width()), r.height() / max(1, root.height())
        else:
            dw, dh = self.NATIVE_SIZE.get(key, (0.08, 0.07))
            w = nl.widget(key)
            sh = w.sizeHint() if w is not None else QSize()
            if key not in self.NATIVE_SIZE and sh.isValid() and sh.width() > 8:
                dw, dh = min(0.4, sh.width() / max(1, root.width()) * 1.1), min(0.3, sh.height() / max(1, root.height()) * 1.1)
            fw, fh = dw, dh
        if pos is not None and self.canvas is not None:      # доли холста → доли центральной области окна
            off = self.canvas.geometry().topLeft()
            px = off.x() + pos[0] * self.canvas.width()
            py = off.y() + pos[1] * self.canvas.height()
            cx, cy = (px - root.x()) / max(1, root.width()), (py - root.y()) / max(1, root.height())
        elif r is not None and cur.get("hidden") is not True:
            cx = (r.center().x() - root.x()) / max(1, root.width())
            cy = (r.center().y() - root.y()) / max(1, root.height())
        else:                                              # не видно — по центру, лесенкой (не друг на друга)
            n = sum(1 for k, s in (self.t.get("native") or {}).items() if s.get("free") and k != key)
            cx = 0.5 + ((n % 5) - 2) * 0.06
            cy = 0.5 + ((n % 4) - 1.5) * 0.06
        spec = {"free": True, "hidden": False, "x": round(cx - fw / 2, 4), "y": round(cy - fh / 2, 4),
                "w": round(fw, 4), "h": round(fh, 4)}
        self.set_native(key, spec)
        if key == "vinyl" and self.t["components"]["vinyl"].get("style") == "hidden":
            self._set(("components", "vinyl", "style"), "real")
        if key == "viz" and self.t["components"]["visualizer"].get("style") == "none":
            self._set(("components", "visualizer", "style"), "bars")
        self.select_native(key)
        self._refresh_native_list()
        self.status.setText(self._native_title(key) + _tr(" — на окне. Двигайте и тяните его в «Расставить на экране»"))

    def reset_natives(self):
        self.t["native"] = {}
        nl = self._natives()
        if nl is not None and self.win._custom_on:
            nl.apply({})
        if self._mode != "lyrics":                         # фон пластинки — общий, его трогает только основной вид
            self.t["components"]["vinyl"]["bg"] = {"on": True, "dx": 0.0, "dy": 0.0, "scale": 1.0}
            self.win.vinyl.set_bg_layout()
        else:
            self.win._lyrics_refade()
        self._nsel = None
        self._rebuild_layer_box()
        self.native_committed()

    def set_vinyl_bg(self, values: dict, commit=True):
        bg = self.t["components"]["vinyl"].setdefault("bg", {"on": True, "dx": 0.0, "dy": 0.0, "scale": 1.0})
        bg.update(values)
        self.win.vinyl.set_bg_layout(bg.get("dx", 0.0), bg.get("dy", 0.0), bg.get("scale", 1.0), bg.get("on", True))
        if commit:
            self.native_committed()
        else:
            self._update_status()
            self._refresh_all("native")

    def _refresh_native_list(self):
        lw = getattr(self, "native_list", None)
        nl = self._natives()
        if lw is None or nl is None:
            return
        lw.blockSignals(True)
        lw.clear()
        nat = self.t.get("native") or {}
        group = None
        # только то, что сейчас на окне: добавляют элементы из библиотеки, убирают — Del / «Убрать выбранный»
        for key, title, grp, _w in nl.items():
            s = nat.get(key) or {}
            if s.get("hidden") or not (s.get("free") or nl.rect_in_window(key) is not None):
                continue
            if grp != group:
                group = grp
                h = QListWidgetItem(_tr(grp).upper())
                h.setFlags(Qt.ItemFlag.NoItemFlags)
                lw.addItem(h)
            mark = " · " + _tr("свободно") if s.get("free") else ""
            it = QListWidgetItem(f"   {_tr(title)}{mark}")
            it.setData(Qt.ItemDataRole.UserRole, key)
            lw.addItem(it)
            if key == self._nsel:
                lw.setCurrentItem(it)
            if key == "vinyl" and self.t["components"]["vinyl"]["bg"].get("on", True):
                it2 = QListWidgetItem("      " + _tr("Фон пластинки с обложкой"))
                it2.setData(Qt.ItemDataRole.UserRole, "vinyl_bg")
                lw.addItem(it2)
                if self._nsel == "vinyl_bg":
                    lw.setCurrentItem(it2)
        if lw.count() == 0:
            h = QListWidgetItem(_tr("Пусто — добавьте элементы из библиотеки"))
            h.setFlags(Qt.ItemFlag.NoItemFlags)
            lw.addItem(h)
        lw.blockSignals(False)

    def _on_native_item(self, it):
        key = it.data(Qt.ItemDataRole.UserRole) if it is not None else None
        if key:
            self.select_native(key)

    def _native_box(self, v, key):
        """Свойства выбранного родного элемента в панели «Свойства»."""
        nl = self._natives()
        if key == "vinyl_bg":
            v.addWidget(QLabel(_tr("Фон пластинки с обложкой")))
            V = ("components", "vinyl", "bg")
            v.addWidget(self._check(V + ("on",), "Показывать", "native", "native_bg"))
            v.addWidget(self._slider(V + ("dx",), "Сдвиг ↔", -1, 1, 0.005, "{:.3f}", "native", "native_bg", default=0))
            v.addWidget(self._slider(V + ("dy",), "Сдвиг ↕", -1, 1, 0.005, "{:.3f}", "native", "native_bg", default=0))
            v.addWidget(self._slider(V + ("scale",), "Размер", 0.3, 3, 0.01, "{:.2f}×", "native", "native_bg",
                                     default=1))
            v.addWidget(self._hint("На окне: тяните кольцо вокруг диска, колесо — размер. Если фон обрезается — "
                                   "растяните саму пластинку."))
            return
        title = next((t_ for k, t_, _g, _w in (nl.items() if nl else []) if k == key), key)
        v.addWidget(QLabel(_tr(title)))
        s = dict((self.t.get("native") or {}).get(key) or {})
        if not s.get("free"):
            v.addWidget(self._hint("Сейчас элемент стоит в обычной раскладке. Потяните его на окне — и он станет "
                                   "свободным: любое место и любой размер."))
            fr = QPushButton("Сделать свободным")
            fr.clicked.connect(lambda: self._free_native_now(key))
            v.addWidget(_row(fr, stretch_last=True))
        else:
            for k2, label in (("x", "Слева"), ("y", "Сверху"), ("w", "Ширина"), ("h", "Высота")):
                row = SliderRow(label, 0 if k2 in "wh" else -0.5, 1.5 if k2 in "wh" else 1.2, s.get(k2, 0.1), 0.001,
                                "{:.3f}")
                row.changed.connect(lambda val, k2=k2: self._native_field(key, k2, val))
                row.released.connect(self.native_committed)
                v.addWidget(row)
            back = QPushButton("Вернуть на место")
            back.clicked.connect(lambda: (self.set_native(key, {}), self._rebuild_layer_box_soon()))
            v.addWidget(_row(back, stretch_last=True))
        hide = QPushButton("Убрать элемент")
        hide.setObjectName("Danger")
        hide.clicked.connect(lambda: self.hide_native(key))
        v.addWidget(_row(hide, stretch_last=True))
        if key == "vinyl":
            N = ("components", "vinyl")
            v.addWidget(self._section_label("Пластинка"))
            v.addWidget(self._slider(N + ("scale",), "Размер диска", 0.4, 1.3, 0.01, "{:.2f}×", "native", default=1))
            v.addWidget(self._check(N + ("tonearm",), "Тонарм", "native"))
            b = QPushButton("Фон с обложкой…")
            b.clicked.connect(lambda: self.select_native("vinyl_bg"))
            v.addWidget(_row(b, stretch_last=True))
        elif key == "viz":
            V = ("components", "visualizer")
            v.addWidget(self._section_label("Визуализатор"))
            v.addWidget(self._combo(V + ("style",), tk.VIZ_STYLES, "native"))
            if not s.get("free"):
                v.addWidget(self._slider(V + ("height",), "Высота, px (0 — авто)", 0, 600, 1, "{:.0f}", "native",
                                         cast=int, default=0))

    def _free_native_now(self, key):
        nl = self._natives()
        r = nl.rect_in_window(key, faded_too=True) if nl is not None else None
        if r is None:
            w = nl.widget(key) if nl is not None else None
            if w is None:
                return
            root = nl.root().geometry()                    # спрятан раскладкой — в середину окна
            sz = w.sizeHint() if w.sizeHint().isValid() else w.size()
            r = QRect(root.center().x() - sz.width() // 2, root.center().y() - sz.height() // 2,
                      max(40, sz.width()), max(24, sz.height()))
        spec = nl.frac_from_window(QRectF(r))
        spec.update(free=True, hidden=False)
        self.set_native(key, spec)
        self._rebuild_layer_box_soon()

    def _native_field(self, key, field, val):
        s = dict((self.t.get("native") or {}).get(key) or {})
        if not s.get("free"):
            return
        s[field] = round(float(val), 4)
        self.set_native(key, s, commit=False)

    # ── несколько слоёв и группы ── #

    def selection_ids(self) -> list:
        ids = [x for x in self._multi if any(L["id"] == x for L in self.t["layers"])]
        return ids + ([self._sel] if self._sel else [])

    def selection(self) -> list:
        ids = set(self.selection_ids())
        return [L for L in self.t["layers"] if L["id"] in ids]

    def toggle_multi(self, lid):
        """Ctrl+клик: добавить слой к выделению или убрать (вместе с его группой)."""
        L = next((x for x in self.t["layers"] if x["id"] == lid), None)
        if L is None:
            return
        members = [x["id"] for x in self.t["layers"] if L.get("group") and x.get("group") == L["group"]] or [lid]
        if lid in self.selection_ids():
            keep = [x for x in self.selection_ids() if x not in members]
            self._sel = keep[-1] if keep else None
            self._multi = keep[:-1]
        else:
            if self._sel:
                self._multi = [x for x in self.selection_ids() if x not in members]
            self._multi += [m for m in members if m != lid]
            self._sel = lid
        self._refresh_layer_list()
        self._rebuild_layer_box()

    def set_primary(self, lid):
        """Главный (с ручками) — другой слой из уже выделенных."""
        if lid == self._sel or lid not in self.selection_ids():
            return
        ids = self.selection_ids()
        self._multi = [x for x in ids if x != lid]
        self._sel = lid
        self._refresh_layer_list()
        self._rebuild_layer_box()

    def select_all(self):
        ids = [L["id"] for L in self.t["layers"] if L.get("visible", True)]
        if ids:
            self._sel = ids[-1]
            self._multi = ids[:-1]
            self._refresh_layer_list()
            self._rebuild_layer_box()

    def group_selected(self):
        sel = self.selection()
        if len(sel) < 2:
            self.status.setText("Для группы выделите несколько слоёв (Ctrl+клик)")
            return
        used = {L.get("group") for L in self.t["layers"]}
        n = 1
        while f"G{n}" in used:
            n += 1
        for L in sel:
            L["group"] = f"G{n}"
        self.changed("layers")
        self._commit_soon()
        self.status.setText(f"Группа G{n}: {len(sel)} слоёв — двигаются вместе")

    def ungroup_selected(self):
        for L in self.selection():
            L["group"] = ""
        self._multi = []
        self.changed("layers")
        self._commit_soon()

    def set_layer_values(self, values: dict):
        """Сразу несколько свойств — у всех выделенных слоёв."""
        for L in self.selection():
            L.update(values)
        self.changed("layers")
        self._refresh_all("layer")
        self._commit_soon()

    def set_layer_value(self, key, value, lid=None):
        L = self.selected_layer() if lid is None else next((x for x in self.t["layers"] if x["id"] == lid), None)
        if L is None:
            return
        L[key] = value
        self.changed("layers")
        self._refresh_all("layer")
        if key in ("kind", "z", "anims", "box", "effect", "widget"):
            self._rebuild_layer_box()

    # ── стопка анимаций ── #

    def add_anim(self, kind):
        L = self.selected_layer()
        if L is None:
            return
        L.setdefault("anims", []).append({"type": kind, "speed": 1.0, "amount": 1.0, "react": 0.6, "on": True})
        del L["anims"][:-8]
        self.changed("layers")
        self._commit_soon()
        self._rebuild_layer_box()

    def clear_anims(self):
        L = self.selected_layer()
        if L is not None:
            L["anims"] = []
            self.changed("layers")
            self._commit_soon()
            self._rebuild_layer_box()

    # ── виджеты, эффекты, наборы ── #

    def widget_menu(self, parent, menu=None):
        m = menu or QMenu(parent)
        m.setStyleSheet(STUDIO_QSS)
        self.native_menu(parent, m.addMenu(_tr("Элементы плеера (винил, кнопки, библиотека…)")))
        m.addAction(_tr("Своя кнопка (редактор кнопок)…"), lambda: self.open_button_editor())
        m.addSeparator()
        for group, items in layer_fx.lib_groups():
            sub = m.addMenu(_tr(group))
            for b in items:
                a = sub.addAction(_tr(b["title"]), lambda k=b["key"]: self.add_widget(k))
                a.setToolTip(_tr(b.get("hint") or ""))
            if group == "Из тем":                          # настоящие визуализации тем — тоже виджеты
                sub.addSeparator()
                sub.addAction("MilkDrop (из Winamp)", lambda: self.apply_preset("milkdrop"))
                sub.addAction("Жидкость (из Fluid)", lambda: self.apply_preset("fluid"))
        m.addSeparator()
        m.addAction("MilkDrop (из Winamp)", lambda: self.apply_preset("milkdrop"))
        m.addAction("Жидкость (из Fluid)", lambda: self.apply_preset("fluid"))
        m.addSeparator()
        m.addAction("Пустой скрипт EchoScript…", self.add_blank_script)
        m.addAction("Импорт виджетов из темы LOOM (.echo)…", lambda: self.import_echo(as_layers=True))
        return m

    def effect_menu(self, parent, menu=None):
        m = menu or QMenu(parent)
        m.setStyleSheet(STUDIO_QSS)
        for group, items in layer_fx.preset_groups():
            sub = m.addMenu(_tr(group))
            for key, _g, title, hint, _fn in items:
                a = sub.addAction(_tr(title), lambda k=key: self.apply_preset(k))
                a.setToolTip(_tr(hint))
        mine = [x for x in self.store.list() if x["id"] != self.t.get("id") and x.get("layers")]
        if mine:
            sub = m.addMenu("Слои из моих тем")
            for x in mine:
                sub.addAction(x["name"], lambda x=x: self.copy_from_theme(x))
        return m

    def _added(self, new, msg=""):
        if not new:
            return
        self._sel = new[-1]["id"]
        self._multi = [L["id"] for L in new[:-1]]
        self._refresh_layer_list()
        self._rebuild_layer_box()
        self.changed("full" if msg == "ui" else "layers")
        self._commit_soon()
        if msg and msg != "ui":
            self.status.setText(msg)

    def add_widget(self, key, pos=None):
        L = layer_fx.widget_layer(key, pos=pos)
        self.t["layers"].append(L)
        self._added([L])
        return L

    def add_blank_script(self):
        code = ("widget MyWidget {\n  prop color = theme.accent\n  state t = 0\n"
                "  on frame(dt) { t += dt }\n  on click { toggle() }\n  on draw {\n"
                "    let r = min(w, h) * (0.3 + audio.bass * 0.15)\n    blend(\"add\")\n"
                "    glow(w / 2, h / 2, r * 1.6, with_alpha(color, 0.4))\n    blend(\"normal\")\n"
                "    fill(color)\n    circle(w / 2, h / 2, r * 0.6 + sin(t * 3) * 4)\n  }\n}\n")
        L = tk.new_layer("script", name=_tr("Мой виджет"), script=code, widget="MyWidget", x=0.5, y=0.5,
                         size=0.3, aspect=1.0)
        self.t["layers"].append(L)
        self._added([L])
        self.open_code(L["id"], "widget")

    def apply_preset(self, key):
        before_ui = self.t["components"].get("ui")
        before_nat = json.dumps(self.t.get("native") or {}, sort_keys=True)
        new = layer_fx.apply_preset(self.t, key)
        nat_changed = json.dumps(self.t.get("native") or {}, sort_keys=True) != before_nat
        if nat_changed:                                    # настоящий винил и др. родные элементы — сразу на окне
            if self.t["components"]["vinyl"].get("style") == "hidden" and (self.t.get("native") or {}).get("vinyl"):
                self.t["components"]["vinyl"]["style"] = "real"
            self.changed("full")
            self._commit_soon()
            self._refresh_native_list()
        if not new:
            if nat_changed:
                self.select_native(next(iter(k for k in ("vinyl",) if (self.t.get("native") or {}).get(k)), None))
                self.status.setText(_tr("Винил на окне — двигайте и тяните его в «Расставить на экране»"))
            return
        self._added(new, "ui" if (self.t["components"].get("ui") != before_ui or nat_changed) else
                    f"Добавлено слоёв: {len(new)} — их можно двигать, тянуть и складывать с другими эффектами")
        self._refresh_all()

    def copy_from_theme(self, other):
        new = layer_fx.copy_layers_from(self.t, other, self.store)
        self._added(new, f"Скопировано слоёв из «{other['name']}»: {len(new)}")

    def import_echo(self, path=None, as_layers=False):
        """Тема LOOM (.echo): целиком новой темой или её виджеты — слоями в текущую."""
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Тема LOOM", str(Path.home()), ECHO_FILTER)
        if not path:
            return
        try:
            src = Path(path).read_text("utf-8")
        except OSError as e:
            QMessageBox.warning(self, "LOOM", f"Не удалось прочитать файл:\n{e}")
            return
        if as_layers:
            new = layer_fx.script_layers(src)
            self.t["layers"].extend(new)
            self._added(new, f"Виджетов из «{Path(path).stem}»: {len(new)}")
            return
        if not self._confirm_discard():
            return
        self._discard_orphan()
        t = layer_fx.script_theme(Path(path).stem, src)
        self.t = self._fresh(t)
        self.t["name"] = tk.suggest_name({x["name"] for x in self.store.list()}, Path(path).stem)
        self._saved = None
        self._undo, self._redo = [], []
        self._snap = self._json()
        self._sel, self._multi = None, []
        self._after_reload()

    # ── код слоя ── #

    def open_code(self, lid, role):
        """Редактор EchoScript: код виджета (role="widget") или поведение любого слоя ("behavior")."""
        key = (lid, role)
        dlg = self._code.get(key)
        try:
            if dlg is not None:
                self._focus_editor(dlg)
                return dlg
        except RuntimeError:
            pass
        L = next((x for x in self.t["layers"] if x["id"] == lid), None)
        if L is None and role != "bg":
            return None
        dlg = CodeDialog(self, lid, role)
        self._code[key] = dlg
        self._show_editor(dlg)
        return dlg

    # ── редакторы (код, кнопка): вкладкой в конструкторе или отдельным окном ── #

    def editors_docked(self) -> bool:
        return bool((getattr(self.win, "settings", None) or {}).get("studio_editors_docked", True))

    def _show_editor(self, w):
        w.set_docked(self.editors_docked())
        if self.editors_docked():
            self._embed_editor(w)
        else:
            self._float_editor(w)

    def _embed_editor(self, w):
        if self.tabs.indexOf(w) < 0:
            w.hide()
            w.setParent(None)
            w.setWindowFlags(Qt.WindowType.Widget)
            self.tabs.addTab(w, getattr(w, "tab_title", _tr("Редактор")))
        w.show()
        self.tabs.setCurrentWidget(w)
        if self.docked():                                  # встроенная панель узкая — раздвинуть под редактор
            dock = getattr(self.win, "_studio_dock", None)
            try:
                if dock is not None and dock.width() < 640:
                    self.win.resizeDocks([dock], [min(760, max(640, self.win.width() // 2))],
                                         Qt.Orientation.Horizontal)
            except RuntimeError:
                pass

    def _float_editor(self, w):
        i = self.tabs.indexOf(w)
        if i >= 0:
            self.tabs.removeTab(i)
        w.setParent(self, Qt.WindowType.Window)
        w.setStyleSheet(STUDIO_QSS)
        w.show()
        w.raise_()
        w.activateWindow()

    def _focus_editor(self, w):
        if self.tabs.indexOf(w) >= 0:
            self.tabs.setCurrentWidget(w)
        else:
            w.show()
            w.raise_()
            w.activateWindow()

    def toggle_editor_dock(self, w):
        """Кнопка ⇱ / ⇲ в редакторе: все редакторы — вкладками в конструкторе или окнами."""
        on = not self.editors_docked()
        s = getattr(self.win, "settings", None)
        if s is not None:
            s["studio_editors_docked"] = on
            fn = getattr(self.win, "save_settings", None)
            if fn is not None:
                try:
                    fn()
                except Exception:                          # noqa: BLE001
                    pass
        eds = [x for x in list(self._code.values()) + [getattr(self, "_btn_ed", None), getattr(self, "_bg_ed", None)]
               if x is not None]
        for x in eds:
            try:
                x.set_docked(on)
                (self._embed_editor if on else self._float_editor)(x)
            except RuntimeError:
                pass
        try:
            self._focus_editor(w)
        except RuntimeError:
            pass

    def _close_tab(self, i):
        if i <= 0:
            return
        w = self.tabs.widget(i)
        if w is not None:
            w.close()

    def _editor_closed(self, w):
        i = self.tabs.indexOf(w)
        if i > 0:
            self.tabs.removeTab(i)
        if getattr(self, "_btn_ed", None) is w:
            self._btn_ed = None

    # ── убрать фон у картинки / GIF ── #

    def open_bg_remove(self, lid):
        L = next((x for x in self.t["layers"] if x["id"] == lid), None)
        if L is None or L["kind"] != "media" or not (L.get("src") or L.get("src_orig")):
            self.status.setText(_tr("Сначала выберите слой-картинку или GIF"))
            return
        old = getattr(self, "_bg_ed", None)
        if old is not None:
            try:
                if old.lid == lid:
                    self._focus_editor(old)
                    return old
                old.close()
            except RuntimeError:
                pass
        ed = BgRemoveEditor(self, lid)
        self._bg_ed = ed
        self._show_editor(ed)
        return ed

    # ── редактор кнопок ── #

    def open_button_editor(self, lid=None):
        """Нарисовать свою кнопку (lid=None) или изменить вид уже сделанной."""
        from button_editor import ButtonEditor
        L = next((x for x in self.t["layers"] if x["id"] == lid), None) if lid else None
        old = getattr(self, "_btn_ed", None)
        if old is not None:
            try:
                if old._lid == (L["id"] if L else None):
                    self._focus_editor(old)
                    return old
                old.close()
            except RuntimeError:
                pass
        ed = ButtonEditor(design=(L or {}).get("design"), editing=L is not None,
                          on_apply=self._button_applied,
                          import_image=lambda fn: self.store.import_asset(self.t, fn),
                          resolve=lambda ref: (self.store.asset(self.t, ref) if str(ref).startswith("assets/")
                                               else ref),
                          dock_toggle=lambda: self.toggle_editor_dock(ed), docked=self.editors_docked(),
                          on_close=self._editor_closed)
        ed.setStyleSheet(STUDIO_QSS)
        ed._lid = L["id"] if L else None
        ed.tab_title = _tr("Кнопка: ") + ((L.get("name") if L else "") or _tr("новая"))
        self._btn_ed = ed
        self._show_editor(ed)
        return ed

    def _button_applied(self, design, png):
        """Редактор кнопки нажал «Добавить» / «Обновить»: картинка — в файлы темы, слой — новый или тот же."""
        from button_editor import ACTIONS, behavior_code
        rel = self.store.import_asset(self.t, png)
        if not rel:
            self.status.setText(_tr("Не получилось сохранить картинку кнопки"))
            return
        ed = getattr(self, "_btn_ed", None)
        lid = getattr(ed, "_lid", None) if ed is not None else None
        L = next((x for x in self.t["layers"] if x["id"] == lid), None) if lid else None
        name = dict((a[0], a[1]) for a in ACTIONS).get(design.get("action"), _tr("Кнопка"))
        code = behavior_code(design)
        if L is None:
            aspect = design["w"] / max(1, design["h"])
            L = tk.new_layer("media", name=_tr("Кнопка") + f" · {_tr(name)}", src=rel, x=0.5, y=0.5,
                             size=round(0.11 * max(1.0, aspect), 3), z="front", behavior=code, design=design)
            self.t["layers"].append(L)
            self._sel, self._multi, self._nsel = L["id"], [], None
            if ed is not None:
                ed._lid = L["id"]
            self.status.setText(_tr("Кнопка добавлена — её можно двигать на окне («Расставить на экране»)"))
        else:
            L.update(src=rel, behavior=code, design=design)
            self.status.setText(_tr("Кнопка обновлена"))
        self._refresh_layer_list()
        self._rebuild_layer_box()
        self.changed("layers")
        self._commit_soon()

    def script_error(self, lid) -> str:
        rt = getattr(self.win, "_crt", None)
        sh = getattr(rt, "scripts", None) if rt is not None else None
        L = {"id": "__bg__"} if lid == "__bg__" else next((x for x in self.t["layers"] if x["id"] == lid), None)
        return sh.error(L) if (sh is not None and L is not None) else ""

    def toggle_layer_flag(self, key):
        L = self.selected_layer()
        if L is not None:
            self.set_layer_value(key, not L.get(key, key == "visible"))

    def add_layer(self, kind, src="", pos=None, select=True):
        if kind == "script":
            return self.add_widget("label", pos)
        if kind == "effect":
            return self.apply_preset("milkdrop")
        n = sum(1 for L in self.t["layers"] if L["kind"] == kind) + 1
        names = {"media": _tr("Картинка"), "text": _tr("Надпись"), "shape": _tr("Фигура"), "video": _tr("Видео")}
        over = {"name": f"{names[kind]} {n}", "x": pos[0] if pos else 0.5, "y": pos[1] if pos else 0.5,
                "z": "front"}
        if kind == "text":
            over.update(text="ECHOES", size=0.22, color=self.t["palette"]["text"], glow=0.4,
                        color2=self.t["palette"]["accent"])
        elif kind == "shape":
            over.update(shape="star", size=0.12, color=self.t["palette"]["accent"], glow=0.5, anim="pulse")
        elif kind in ("media", "video"):
            if not src:
                title, flt = ("Видео", VIDEO_FILTER) if kind == "video" else ("Картинка или GIF", IMAGE_FILTER)
                fn, _ = QFileDialog.getOpenFileName(self, title, "", flt)
                if not fn:
                    return None
                src = self.store.import_asset(self.t, fn)
                if not src:
                    return None
                over["name"] = Path(fn).stem[:30] or over["name"]
            over.update(src=src, size=0.3 if kind == "media" else 0.45)
        L = tk.new_layer(kind, **over)
        self.t["layers"].append(L)
        if select:
            self._sel = L["id"]
            self._multi = []
        self._refresh_layer_list()
        self._rebuild_layer_box()
        self.changed("layers")
        self._commit_soon()
        return L

    def pick_layer_media(self):
        L = self.selected_layer()
        if L is None or L["kind"] not in ("media", "video"):
            return
        if L["kind"] == "video":
            fn, _ = QFileDialog.getOpenFileName(self, "Видео", "", VIDEO_FILTER)
        else:
            fn, _ = QFileDialog.getOpenFileName(self, "Картинка или GIF", "", IMAGE_FILTER)
        if fn:
            rel = self.store.import_asset(self.t, fn)
            if rel:
                self.set_layer_value("src", rel)
                self._rebuild_layer_box()

    def duplicate_layer(self):
        L = self.selected_layer()
        if L is None:
            return
        D = copy.deepcopy(L)
        D["id"] = tk.new_layer(L["kind"])["id"]
        D["name"] = (L.get("name") or _tr("Слой")) + _tr(" (копия)")
        D["x"] = min(1.2, L["x"] + 0.03)
        D["y"] = min(1.2, L["y"] + 0.03)
        D["locked"] = False
        D["group"] = ""
        self._multi = []
        self.t["layers"].insert(self.t["layers"].index(L) + 1, D)
        self._sel = D["id"]
        self._refresh_layer_list()
        self._rebuild_layer_box()
        self.changed("layers")
        self._commit_soon()

    def delete_layer(self):
        L = self.selected_layer()
        if L is None:
            return
        i = self.t["layers"].index(L)
        gone = set(self.selection_ids())                   # удаляются все выделенные
        self.t["layers"] = [x for x in self.t["layers"] if x["id"] not in gone]
        for k in [k for k in self._code if k[0] in gone]:
            try:
                self._code.pop(k).close()
            except RuntimeError:
                pass
        rest = self.t["layers"]
        self._multi = []
        self._sel = rest[min(i, len(rest) - 1)]["id"] if rest else None
        self._refresh_layer_list()
        self._rebuild_layer_box()
        self.changed("layers")
        self._commit_soon()

    def move_layer(self, d):
        L = self.selected_layer()
        if L is None:
            return
        layers = self.t["layers"]
        i = layers.index(L)
        j = max(0, min(len(layers) - 1, i + d))
        if i != j:
            layers.insert(j, layers.pop(i))
            self._refresh_layer_list()
            self.changed("layers")
            self._commit_soon()

    def layers_changed(self, commit=True):
        self.changed("layers", commit=commit)
        self._refresh_all("layer")

    def _rebuild_layer_box(self):
        if not hasattr(self, "_layer_v"):
            return
        self._unbind("layer")
        self._unbind("native")
        v = self._layer_v
        while v.count() > 1:
            it = v.takeAt(1)
            if it.widget():
                it.widget().deleteLater()
        if self._nsel:
            self._native_box(v, self._nsel)
            return
        L = self.selected_layer()
        if L is None:
            v.addWidget(self._hint("Выберите слой в списке или добавьте новый."))
            return
        T, P = "layer", ("@L",)
        name = QLineEdit(L.get("name", ""))
        name.setMaxLength(40)
        name.textEdited.connect(lambda s: self._set(P + ("name",), s, "meta") or self._refresh_layer_list())
        self._bind(T, lambda: (name.text() != L.get("name", "")) and name.setText(L.get("name", "")))
        v.addWidget(_row(QLabel("Название"), name))
        k = L["kind"]
        if k == "media":
            v.addWidget(self._file(P + ("src",), "image", tag=T, scope="layers",
                                   on_change=self._refresh_layer_list))
            nb = QPushButton("Убрать фон…")
            nb.setToolTip("Своя модель вырежет объект из фона (картинки и GIF), края — мягкие")
            nb.clicked.connect(lambda: self.open_bg_remove(L["id"]))
            v.addWidget(_row(nb, stretch_last=True))
        elif k == "video":
            v.addWidget(self._file(P + ("src",), "video", tag=T, scope="layers",
                                   on_change=self._refresh_layer_list))
            v.addWidget(self._slider(P + ("speed",), "Скорость видео", 0.25, 4, 0.05, "{:.2f}×", T, "layers",
                                     default=1))
        elif k == "text":
            te = QPlainTextEdit(L.get("text", ""))
            te.setFixedHeight(64)
            te.textChanged.connect(lambda: self._set(P + ("text",), te.toPlainText()[:200], "layers"))
            v.addWidget(te)
            fc = QFontComboBox()
            fc.setEditable(False)
            if L.get("font") and not tl.is_font_asset(L["font"]):
                fc.setCurrentFont(QFont(L["font"]))
            fc.currentFontChanged.connect(lambda f: self._set(P + ("font",), f.family(), "layers"))
            ff = QPushButton("Шрифт файлом…")
            ff.clicked.connect(self._layer_font_file)
            v.addWidget(_row(fc, ff))
            v.itemAt(v.count() - 1).widget().layout().setStretch(0, 1)
            v.addWidget(_row(self._check(P + ("bold",), "Жирный", T, "layers"),
                             self._check(P + ("italic",), "Курсив", T, "layers"), stretch_last=True))
        elif k == "shape":
            v.addWidget(_row(QLabel("Фигура"), None, self._combo(P + ("shape",), tk.SHAPES, T, "layers")))
        elif k == "script":
            self._script_box(v, L)
        elif k == "effect":
            v.addWidget(_row(QLabel("Эффект"), None, self._combo(P + ("effect",), tk.EFFECTS, T, "layers",
                                                                 on_change=self._rebuild_layer_box_soon)))
            if L.get("effect") in ("milkdrop", "fluid"):
                v.addWidget(self._slider(P + ("res",), "Детализация", 0.15, 1, 0.05, "{:.2f}", T, "layers",
                                         tip="Выше — чётче, но тяжелее для процессора"))
            if L.get("effect") == "milkdrop":
                nb = QPushButton("Следующий пресет MilkDrop")
                nb.clicked.connect(lambda: self._effect_cmd("next"))
                v.addWidget(_row(nb, stretch_last=True))
            else:
                v.addWidget(self._slider(P + ("speed",), "Скорость", 0.25, 4, 0.05, "{:.2f}×", T, "layers", default=1))
            v.addWidget(self._slider(P + ("react",), "Реакция на музыку", 0, 1, 0.01, "{:.2f}", T, "layers"))
            v.addWidget(self._check(P + ("opaque",), "Сплошной фон эффекта (без него тёмный фон прозрачный)",
                                    T, "layers"))
        if k in ("text", "shape"):
            v.addWidget(self._color(P + ("color",), "Цвет", T, "layers"))
            v.addWidget(self._color(P + ("color2",), "Градиент до", T, "layers", allow_empty=True, empty_text="нет"))
            v.addWidget(self._color(P + ("outline",), "Контур", T, "layers", allow_empty=True, empty_text="нет"))
            v.addWidget(self._slider(P + ("glow",), "Свечение", 0, 1, 0.01, "{:.2f}", T, "layers"))
        v.addWidget(self._section_label("Положение и размер"))
        v.addWidget(self._slider(P + ("x",), "По горизонтали", -0.2, 1.2, 0.001, "{:.3f}", T, "layers"))
        v.addWidget(self._slider(P + ("y",), "По вертикали", -0.2, 1.2, 0.001, "{:.3f}", T, "layers"))
        v.addWidget(_row(QLabel("Размер"), None, self._combo(
            P + ("box",), [("min", "Пропорционально"), ("window", "Тянется вместе с окном")], T, "layers",
            on_change=self._rebuild_layer_box_soon)))
        win_box = L.get("box") == "window"
        v.addWidget(self._slider(P + ("size",), "Ширина, доля окна" if win_box else "Размер", 0.01, 2, 0.005,
                                 "{:.3f}", T, "layers"))
        if win_box or k in ("shape", "script", "effect"):
            v.addWidget(self._slider(P + ("aspect",), "Пропорции", 0.1, 10, 0.01, "{:.2f}", T, "layers",
                                     tip="Ширина к высоте"))
        v.addWidget(self._slider(P + ("stretch_x",), "Растянуть ↔", 0.05, 5, 0.01, "{:.2f}×", T, "layers", default=1,
                                 tip="Свободное растяжение по ширине (на окне — середины сторон рамки)"))
        v.addWidget(self._slider(P + ("stretch_y",), "Растянуть ↕", 0.05, 5, 0.01, "{:.2f}×", T, "layers", default=1))
        v.addWidget(self._slider(P + ("rot",), "Поворот", -180, 180, 1, "{:.0f}°", T, "layers", default=0))
        v.addWidget(self._slider(P + ("tilt_x",), "3D-наклон ↕", -85, 85, 1, "{:.0f}°", T, "layers", default=0,
                                 tip="Поворот вокруг горизонтальной оси с перспективой (на окне — Alt+Shift+колесо)"))
        v.addWidget(self._slider(P + ("tilt_y",), "3D-наклон ↔", -85, 85, 1, "{:.0f}°", T, "layers", default=0,
                                 tip="Поворот вокруг вертикальной оси (на окне — Alt+колесо)"))
        v.addWidget(self._slider(P + ("opacity",), "Непрозрачность", 0, 1, 0.01, "{:.2f}", T, "layers", default=1))
        zb = QWidget()
        zh = QHBoxLayout(zb)
        zh.setContentsMargins(0, 0, 0, 0)
        r1 = QRadioButton("Поверх интерфейса")
        r2 = QRadioButton("На фоне, за панелями")
        (r1 if L["z"] == "front" else r2).setChecked(True)
        r1.toggled.connect(lambda on: on and self.set_layer_value("z", "front"))
        r2.toggled.connect(lambda on: on and self.set_layer_value("z", "back"))
        zh.addWidget(r1)
        zh.addWidget(r2)
        zh.addStretch(1)
        v.addWidget(zb)
        v.addWidget(_row(QLabel("Наложение"), None, self._combo(P + ("blend",), tk.BLEND_MODES, T, "layers")))
        v.addWidget(self._check(P + ("flip",), "Отразить по горизонтали", T, "layers"))
        grp = QLineEdit(L.get("group", ""))
        grp.setMaxLength(24)
        grp.setPlaceholderText("нет — Ctrl+G на окне объединит выделенные")
        grp.textEdited.connect(lambda s: self._set(P + ("group",), s.strip(), "layers") or self._refresh_layer_list())
        v.addWidget(_row(QLabel("Группа"), grp))
        self._anim_box(v, L)
        self._behavior_box(v, L)

    # ── стопка анимаций в свойствах слоя ── #

    def _anim_box(self, v, L):
        v.addWidget(self._section_label("Анимации — работают все сразу"))
        opts = [(a, t_) for a, t_ in tk.ANIMATIONS if a != "none"]
        anims = L.setdefault("anims", [])

        def touch(rebuild=False):
            self.changed("layers")
            self._commit_soon()
            self._refresh_layer_list()
            if rebuild:
                self._rebuild_layer_box_soon()
        for j, a in enumerate(anims):
            card = QFrame()
            card.setObjectName("Card")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(8, 6, 8, 6)
            cl.setSpacing(4)
            on = QCheckBox()
            on.setChecked(a.get("on", True))
            on.setToolTip("Включена")
            on.toggled.connect(lambda s, a=a: (a.__setitem__("on", bool(s)), touch()))
            cb = QComboBox()
            for val, text in opts:
                cb.addItem(_tr(text), val)
            cb.setCurrentIndex(max(0, cb.findData(a["type"])))
            cb.currentIndexChanged.connect(lambda _i, a=a, cb=cb: (a.__setitem__("type", cb.currentData()), touch(True)))
            tools = []
            for txt, tip, d in (("↑", "Раньше в стопке", -1), ("↓", "Позже в стопке", 1)):
                b = QPushButton(txt)
                b.setObjectName("Tool")
                b.setToolTip(tip)
                b.clicked.connect(lambda _=False, j=j, d=d: self._move_anim(j, d))
                tools.append(b)
            rm = QPushButton("×")
            rm.setObjectName("Tool")
            rm.setToolTip("Убрать анимацию")
            rm.clicked.connect(lambda _=False, j=j: (anims.pop(j), touch(True)))
            head = _row(on, cb, *tools, rm)
            head.layout().setStretch(1, 1)
            cl.addWidget(head)
            for key, label, lo, hi, step, fmt in (("speed", "Скорость", 0, 5, 0.05, "{:.2f}×"),
                                                   ("amount", "Сила", 0, 3, 0.05, "{:.2f}"),
                                                   ("react", "Реакция на музыку", 0, 1, 0.01, "{:.2f}")):
                if key == "react" and a["type"] not in tk.AUDIO_ANIMS:
                    continue
                row = SliderRow(label, lo, hi, a.get(key, 1.0 if key != "react" else 0.6), step, fmt,
                                default=1.0 if key != "react" else 0.6)
                row.changed.connect(lambda val, a=a, key=key: (a.__setitem__(key, round(float(val), 3)),
                                                               self.changed("layers", commit=False)))
                row.released.connect(self._commit_soon)
                cl.addWidget(row)
            v.addWidget(card)
        add = QPushButton("+ Анимация ▾")
        add.setToolTip("Вращение + пульсация + движение + подпрыгивание… — сколько угодно сразу (до 8)")
        menu = QMenu(add)
        menu.setStyleSheet(STUDIO_QSS)
        for val, text in opts:
            menu.addAction(_tr(text), lambda v_=val: self.add_anim(v_))
        add.clicked.connect(lambda: menu.exec(add.mapToGlobal(add.rect().bottomLeft())))
        v.addWidget(_row(add, stretch_last=True))

    def _move_anim(self, j, d):
        L = self.selected_layer()
        if L is None:
            return
        a = L.get("anims") or []
        k = j + d
        if 0 <= k < len(a):
            a[j], a[k] = a[k], a[j]
            self.changed("layers")
            self._commit_soon()
            self._rebuild_layer_box_soon()

    # ── скрипты в свойствах слоя ── #

    def _err_label(self, lid):
        lb = QLabel()
        lb.setObjectName("Warn")
        lb.setWordWrap(True)
        lb.setVisible(False)

        def poll():
            try:
                e = self.script_error(lid)
                lb.setText("Ошибка: " + e if e else "")
                lb.setVisible(bool(e))
            except RuntimeError:
                pass
        poll()
        self._bind("layer", poll)
        self._err_polls = [x for x in getattr(self, "_err_polls", []) if x[0] is not None]
        self._err_polls.append((lb, poll))
        if not hasattr(self, "_err_timer"):
            self._err_timer = QTimer(self)
            self._err_timer.timeout.connect(self._poll_errors)
            self._err_timer.start(700)
        return lb

    def _poll_errors(self):
        alive = []
        for lb, fn in getattr(self, "_err_polls", []):
            try:
                if lb.isVisibleTo(self) or lb.parent() is not None:
                    fn()
                    alive.append((lb, fn))
            except RuntimeError:
                pass
        self._err_polls = alive

    def _script_box(self, v, L):
        code = L.get("script") or ""
        names = DL.widget_names(code)
        cls = L.get("widget") if L.get("widget") in names else (names[0] if names else "")
        if len(names) > 1:
            cb = QComboBox()
            for n in names:
                cb.addItem(n, n)
            cb.setCurrentIndex(max(0, cb.findData(cls)))
            cb.currentIndexChanged.connect(lambda _i: self.set_layer_value("widget", cb.currentData()))
            v.addWidget(_row(QLabel("Виджет"), None, cb))
        ed = QPushButton("Код виджета (EchoScript)…")
        ed.setObjectName("Primary")
        ed.clicked.connect(lambda: self.open_code(L["id"], "widget"))
        v.addWidget(_row(ed, stretch_last=True))
        v.addWidget(self._err_label(L["id"]))
        meta = DL.prop_meta(code, cls) if cls else {}
        if meta:
            v.addWidget(self._section_label("Свойства виджета"))
            for name, m in meta.items():
                w = self._prop_row(L, name, m)
                if w is not None:
                    v.addWidget(w)
        v.addWidget(self._check(("@L", "clip"), "Обрезать по рамке слоя", "layer", "layers",
                                tip="Выключите, если виджет рисует свечение/частицы за своими краями"))
        v.addWidget(self._slider(("@L", "fps"), "Кадров/с виджета", 0, 240, 5, "{:.0f}", "layer", "layers",
                                 cast=int, default=0,
                                 tip="0 — как у экрана. Тяжёлым визуализаторам хватает 30–60: остальной интерфейс "
                                     "при этом идёт на полной частоте"))

    def _set_prop(self, L, name, text):
        props = dict(L.get("props") or {})
        props[name] = text
        L["props"] = props
        self.changed("layers")
        self._commit_soon()

    def _prop_row(self, L, name, meta):
        try:
            from dsl_studio import OPTION_LABELS, PROP_LABELS, THEME_LABELS
        except Exception:                                  # noqa: BLE001
            PROP_LABELS, OPTION_LABELS, THEME_LABELS = {}, {}, {}
        cur = str((L.get("props") or {}).get(name, meta["default"])).strip()
        label = _tr(PROP_LABELS.get(name, name))
        tip = meta.get("comment") or ""
        lb = QLabel(label)
        lb.setToolTip(tip)
        if meta.get("options"):
            cb = QComboBox()
            for o in meta["options"]:
                cb.addItem(_tr(OPTION_LABELS.get(o, o)), o)
            cb.setCurrentIndex(max(0, cb.findData(cur.strip('"').strip("'"))))
            cb.currentIndexChanged.connect(lambda _i: self._set_prop(L, name, f'"{cb.currentData()}"'))
            return _row(lb, None, cb)
        if cur in ("true", "false"):
            ch = QCheckBox(label)
            ch.setToolTip(tip)
            ch.setChecked(cur == "true")
            ch.toggled.connect(lambda on: self._set_prop(L, name, "true" if on else "false"))
            return ch
        if name == "src" or name.endswith("_src"):
            kind = "video" if (L.get("widget") or "").lower().startswith("video") else "image"
            shown = QLabel(Path(cur.strip('"')).name if cur.strip('"') else "файл не выбран")
            shown.setObjectName("Hint")
            pick = QPushButton("Выбрать…")

            def do_pick():
                fn, _ = QFileDialog.getOpenFileName(self, "Файл", "", VIDEO_FILTER if kind == "video" else IMAGE_FILTER)
                if fn:
                    rel = self.store.import_asset(self.t, fn)
                    if rel:
                        self._set_prop(L, name, layer_fx._lit_str(rel))
                        shown.setText(Path(rel).name)
            pick.clicked.connect(do_pick)
            return _row(lb, shown, pick)
        is_color = DL.NUM_RE.match(cur) is None and (cur.startswith("theme.") or
                                                     re.match(r'^"#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?"$', cur))
        if is_color:
            theme_key = cur[6:] if cur.startswith("theme.") else ""
            btn = ColorButton("" if theme_key else cur.strip('"'), True,
                              _tr(THEME_LABELS.get(theme_key, "тема")) if theme_key else "тема")
            btn.setToolTip(tip)
            dflt = meta["default"]
            btn.changed.connect(lambda c: self._set_prop(L, name, f'"{c}"' if c else dflt))
            return _row(lb, None, btn)
        if DL.NUM_RE.match(cur):
            val = float(cur)
            lo, hi = meta.get("range") or (min(0.0, val), max(1.0, abs(val) * 2.5))
            step = 0.01 if hi - lo <= 5 else (0.1 if hi - lo <= 50 else 1)
            fmt = "{:.2f}" if step < 0.1 else ("{:.1f}" if step < 1 else "{:.0f}")
            row = SliderRow(label, lo, hi, val, step, fmt, default=float(meta["default"]) if DL.NUM_RE.match(
                meta["default"]) else None, tip=tip)
            row.changed.connect(lambda x: self._set_prop(L, name, DL.fmt_num(float(x))))
            return row
        le = QLineEdit(cur)
        le.setToolTip(tip + ("\n" if tip else "") + "Выражение EchoScript: \"текст\", число, true/false, theme.accent…")
        le.editingFinished.connect(lambda: self._set_prop(L, name, le.text().strip() or meta["default"]))
        return _row(lb, le)

    def _behavior_box(self, v, L):
        v.addWidget(self._section_label("Поведение — свой скрипт"))
        has = bool((L.get("behavior") or "").strip())
        hint = self._hint("Есть код: слой живёт по своим правилам." if has else
                          "Обработчики EchoScript у этого слоя: реакция на бас и бит, физика, клики, колесо, "
                          "движение за мышью… Меняйте rot, scale, dx, dy, opacity, tilt_x/y, tint, label.")
        v.addWidget(hint)
        ed = QPushButton("Изменить код…" if has else "+ Добавить поведение…")
        ed.clicked.connect(lambda: self.open_code(L["id"], "behavior"))
        snip = QPushButton("Готовое ▾")
        snip.setToolTip("Вставить готовый кусочек поведения")
        menu = QMenu(snip)
        menu.setStyleSheet(STUDIO_QSS)
        for title, code in layer_fx.BEHAVIOR_SNIPPETS:
            menu.addAction(_tr(title), lambda c=code: self._add_snippet(c))
        snip.clicked.connect(lambda: menu.exec(snip.mapToGlobal(snip.rect().bottomLeft())))
        how = QPushButton("？")
        how.setToolTip("Как писать поведение — справка с примерами")
        how.clicked.connect(lambda: self.open_help("script2"))
        items = [ed, snip, how]
        if has:
            rm = QPushButton("Убрать")
            rm.setObjectName("Danger")
            rm.clicked.connect(lambda: (self.set_layer_value("behavior", ""), self._commit_soon(),
                                        self._rebuild_layer_box_soon()))
            items.append(rm)
        v.addWidget(_row(*items, stretch_last=True))
        if has:
            v.addWidget(self._err_label(L["id"]))

    def _add_snippet(self, code):
        L = self.selected_layer()
        if L is None:
            return
        cur = (L.get("behavior") or "").rstrip()
        L["behavior"] = (cur + "\n" if cur else "") + code + "\n"
        self.changed("layers")
        self._commit_soon()
        self._rebuild_layer_box_soon()
        for (lid, role), dlg in list(self._code.items()):
            if lid == L["id"] and role == "behavior":
                try:
                    dlg.reload()
                except RuntimeError:
                    pass

    def _effect_cmd(self, *cmd):
        L = self.selected_layer()
        rt = getattr(self.win, "_crt", None)
        sh = getattr(rt, "scripts", None) if rt is not None else None
        ent = sh.effects.get(L["id"]) if (sh is not None and L is not None) else None
        if ent is not None and hasattr(ent[1], "command"):
            ent[1].command(*cmd)

    def _rebuild_layer_box_soon(self):
        QTimer.singleShot(0, self._rebuild_layer_box)

    def _section_label(self, text):
        lb = QLabel(_tr(text).upper())
        lb.setObjectName("H2")
        return lb

    def _layer_font_file(self):
        L = self.selected_layer()
        if L is None:
            return
        fn, _ = QFileDialog.getOpenFileName(self, "Шрифт", "", FONT_FILTER)
        if fn:
            rel = self.store.import_asset(self.t, fn)
            if rel:
                self.set_layer_value("font", rel)

    # ── страница «Библиотека» ── #

    def _grid_buttons(self, items, cols=2):
        box = QWidget()
        g = QGridLayout(box)
        g.setContentsMargins(0, 0, 0, 0)
        g.setSpacing(6)
        for n, (text, tip, fn) in enumerate(items):
            b = QPushButton(_tr(text))
            b.setToolTip(_tr(tip))
            b.clicked.connect(fn)
            g.addWidget(b, n // cols, n % cols)
        return box

    def _page_library(self):
        w, v = self._page()
        n, nv = _card("Элементы плеера", "Настоящие винил, визуализатор, кнопки, перемотка, громкость, библиотека и "
                                         "плейлисты — те же, что в обычном плеере. Ставятся на любой холст, даже "
                                         "на пустой; на окне их можно двигать, растягивать и убирать (Del).")
        nl = self._natives()
        have = {k for k, _t, _g, _w in (nl.items() if nl else [])}
        for group, keys in self.NATIVE_GROUPS:
            keys = [k for k in keys if k in have]
            if not keys:
                continue
            nv.addWidget(self._section_label(group))
            nv.addWidget(self._grid_buttons([(self._native_title(k), _tr("Поставить на окно"),
                                              lambda _=False, k=k: self.add_native(k)) for k in keys]))
        v.addWidget(n)
        b, bv = _card("Своя кнопка", "Нарисуйте вид кнопки: форма и заливка, кисть, надписи, символы ▶ ❚❚ ▶▶ ♥, "
                                     "картинки. Потом выберите, что она делает по клику.")
        bb = QPushButton("Редактор кнопок…")
        bb.setObjectName("Primary")
        bb.clicked.connect(lambda: self.open_button_editor())
        bv.addWidget(_row(bb, stretch_last=True))
        v.addWidget(b)
        c, cv = _card("Виджеты", "Каждый виджет — отдельный слой: двигайте, тяните, крутите, наклоняйте в 3D, "
                                 "складывайте в группы. Код любого виджета можно открыть и переписать.")
        for group, items in layer_fx.lib_groups():
            cv.addWidget(self._section_label(group))
            extra = []
            if group == "Из тем":
                extra = [("MilkDrop (из Winamp)", "Настоящие пресеты MilkDrop — слой, который можно растянуть и "
                                                  "повернуть; клик по нему — следующий пресет",
                          lambda _=False: self.apply_preset("milkdrop")),
                         ("Жидкость (из Fluid)", "Симуляция жидкости из Fluid в цветах обложки — слой любого "
                                                 "размера и формы", lambda _=False: self.apply_preset("fluid"))]
            cv.addWidget(self._grid_buttons(extra + [(b["title"], b.get("hint", ""),
                                                      lambda _=False, k=b["key"]: self.add_widget(k)) for b in items]))
        v.addWidget(c)
        e, ev = _card("Эффекты из знаменитых тем", "Копируются в текущую тему слоями — их можно складывать: "
                                                   "MilkDrop под жидкостью Fluid, поверх «Моя волна»…")
        for group, items in layer_fx.preset_groups():
            ev.addWidget(self._section_label(group))
            ev.addWidget(self._grid_buttons([(title, hint, lambda _=False, k=key: self.apply_preset(k))
                                             for key, _g, title, hint, _fn in items]))
        v.addWidget(e)
        m, self._lib_mine_v = _card("Слои из моих тем", "Перенести все слои другой своей темы (с картинками и видео).")
        self._lib_mine = QWidget()
        QVBoxLayout(self._lib_mine).setContentsMargins(0, 0, 0, 0)
        self._lib_mine_v.addWidget(self._lib_mine)
        v.addWidget(m)
        s, sv = _card("LOOM / EchoScript", "Отдельного режима LOOM больше нет — его скрипты живут в слоях. "
                                           "Тему LOOM можно открыть целиком или взять из неё отдельные виджеты.")
        b1 = QPushButton("Открыть тему LOOM (.echo)…")
        b1.clicked.connect(lambda: self.import_echo())
        b2 = QPushButton("Виджеты из .echo — слоями…")
        b2.clicked.connect(lambda: self.import_echo(as_layers=True))
        b3 = QPushButton("+ Пустой скрипт")
        b3.clicked.connect(self.add_blank_script)
        sv.addWidget(_row(b1, b2, stretch_last=True))
        sv.addWidget(_row(b3, stretch_last=True))
        try:
            import dsl_studio
            scripts = dsl_studio.list_scripts()
        except Exception:                                  # noqa: BLE001
            scripts = []
        if scripts:
            sv.addWidget(self._section_label("Скрипты на этом компьютере"))
            sv.addWidget(self._grid_buttons([(s_["name"], "Добавить все виджеты скрипта слоями",
                                              lambda _=False, p=s_["path"]: self.import_echo(p, as_layers=True))
                                             for s_ in scripts], cols=3))
        v.addWidget(s)
        v.addStretch(1)
        self._refresh_lib_mine()
        return w

    def _refresh_lib_mine(self):
        if not hasattr(self, "_lib_mine"):
            return
        lay = self._lib_mine.layout()
        while lay.count():
            it = lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        mine = [x for x in self.store.list() if x["id"] != self.t.get("id") and x.get("layers")]
        if not mine:
            lay.addWidget(self._hint("Пока нет других тем со слоями."))
            return
        lay.addWidget(self._grid_buttons([(f"{x['name']} · {len(x['layers'])}", "Скопировать слои этой темы",
                                           lambda _=False, x=x: self.copy_from_theme(x)) for x in mine]))

    # ── страница «Эффекты» ── #

    def _page_fx(self):
        w, v = self._page()
        z, zv = _card("Где эффекты", "Частицы, сканлайны, зерно и виньетка — поверх всего окна или за интерфейсом: "
                                     "на фоне, под панелями, кнопками и пластинкой.")
        zb = QWidget()
        zh = QHBoxLayout(zb)
        zh.setContentsMargins(0, 0, 0, 0)
        r_front = QRadioButton("Поверх интерфейса")
        r_back = QRadioButton("За интерфейсом (на фоне)")
        grp = QButtonGroup(zb)
        grp.addButton(r_front)
        grp.addButton(r_back)

        def ref_z():
            on = bool(self.t["effects"].get("behind"))
            for b_, val in ((r_front, not on), (r_back, on)):
                b_.blockSignals(True)
                b_.setChecked(val)
                b_.blockSignals(False)
        ref_z()
        r_front.toggled.connect(lambda on: on and self._set(("effects", "behind"), False))
        r_back.toggled.connect(lambda on: on and self._set(("effects", "behind"), True))
        self._bind("", ref_z)
        zh.addWidget(r_front)
        zh.addWidget(r_back)
        zh.addStretch(1)
        zv.addWidget(zb)
        v.addWidget(z)
        c, cv = _card("Частицы", "Летают по окну (поверх интерфейса или за ним — см. выше) и ускоряются под музыку.")
        short = {"stars": "Звёзды", "sakura": "Сакура", "image": "Своя"}
        opts = [(k2, short.get(k2, t2), particle_icon(k2), t2) for k2, t2 in tk.PARTICLES]
        cv.addWidget(self._cards(("effects", "particles", "kind"), opts, cols=3,
                                 on_change=self._sync_particle_image_row))
        self._pimg_row = self._file(("effects", "particles", "image"), "image")
        cv.addWidget(self._pimg_row)
        P = ("effects", "particles")
        cv.addWidget(self._slider(P + ("count",), "Количество", 0, 400, 1, "{:.0f}", cast=int))
        cv.addWidget(self._slider(P + ("speed",), "Скорость", 0, 5, 0.05, "{:.2f}×"))
        cv.addWidget(self._slider(P + ("size",), "Размер", 0.2, 5, 0.05, "{:.2f}×"))
        cv.addWidget(self._slider(P + ("react",), "Реакция на музыку", 0, 1, 0.01, "{:.2f}"))
        cv.addWidget(self._color(P + ("color",), "Цвет частиц"))
        v.addWidget(c)
        e, ev = _card("Эффекты экрана")
        E = ("effects",)
        ev.addWidget(self._slider(E + ("flash",), "Вспышка на бит", 0, 1, 0.01, "{:.2f}",
                                  tip="Фон вспыхивает акцентом на сильных долях"))
        ev.addWidget(self._slider(E + ("scanlines",), "Сканлайны", 0, 1, 0.01, "{:.2f}", tip="Строки старого монитора"))
        ev.addWidget(self._slider(E + ("grain",), "Зерно плёнки", 0, 1, 0.01, "{:.2f}"))
        ev.addWidget(self._slider(E + ("vignette",), "Виньетка", 0, 1, 0.01, "{:.2f}"))
        ev.addWidget(self._slider(E + ("flicker",), "Мерцание фона", 0, 1, 0.01, "{:.2f}", tip="Как у старой лампы"))
        v.addWidget(e)
        v.addStretch(1)
        self._sync_particle_image_row()
        return w

    def _sync_particle_image_row(self):
        if hasattr(self, "_pimg_row"):
            self._pimg_row.setVisible(self.t["effects"]["particles"]["kind"] == "image")

    # ── страница «Элементы» ── #

    def _page_elements(self):
        w, v = self._page()
        u, uv = _card("Интерфейс", "«Только слои» прячет обычный интерфейс плеера: кнопки, перемотка, громкость, "
                                   "пластинка, текст и список — виджеты-слои, которые можно поставить куда угодно.")
        uv.addWidget(self._combo(("components", "ui"), tk.UI_MODES, on_change=self._ui_mode_changed))
        conv = QPushButton("Собрать интерфейс из слоёв")
        conv.setToolTip("Добавить пластинку, название, перемотку, кнопки, громкость, спектр, текст песни и список "
                        "треков отдельными слоями и включить режим «Только слои»")
        conv.clicked.connect(lambda: self.apply_preset("player_ui"))
        uv.addWidget(_row(conv, stretch_last=True))
        v.addWidget(u)
        V = ("components", "visualizer")
        c, cv = _card("Визуализатор")
        short = {"bars": "Столбики", "mirror": "Зеркало", "blocks": "Сегменты", "line": "Линия", "wave": "Волна",
                 "dots": "Точки", "none": "Скрыть"}
        opts = [(k2, short.get(k2, t2), viz_icon(k2), t2) for k2, t2 in tk.VIZ_STYLES]
        cv.addWidget(self._cards(V + ("style",), opts, cols=3))
        cv.addWidget(self._color(V + ("color1",), "Цвет вершин", allow_empty=True, empty_text="акцент"))
        cv.addWidget(self._color(V + ("color2",), "Цвет основания", allow_empty=True, empty_text="акцент 2"))
        cv.addWidget(_row(self._check(V + ("peaks",), "Пики"), self._check(V + ("curve",), "Кривая поверх"),
                          stretch_last=True))
        cv.addWidget(self._slider(V + ("height",), "Высота, px (0 — авто)", 0, 600, 1, "{:.0f}", cast=int, default=0,
                                  tip="Высота обычного визуализатора. Место и любой размер — на окне "
                                      "(«Расставить на экране» → тяните визуализатор)"))
        v.addWidget(c)
        N = ("components", "vinyl")
        d, dv = _card("Пластинка")
        short = {"real": "Пластинка", "graphic": "Графичная", "hidden": "Скрыть"}
        opts = [(k2, short.get(k2, t2), vinyl_icon(k2), t2) for k2, t2 in tk.VINYL_STYLES]
        dv.addWidget(self._cards(N + ("style",), opts, cols=3))
        dv.addWidget(self._slider(N + ("scale",), "Размер", 0.4, 1.3, 0.01, "{:.2f}×", default=1.0))
        dv.addWidget(self._check(N + ("tonearm",), "Тонарм"))
        dv.addWidget(self._section_label("Своя картинка вместо диска"))
        self._vinyl_img_lbl = QLabel()
        self._vinyl_img_lbl.setObjectName("Hint")
        self._vinyl_img_lbl.setWordWrap(True)
        vb1 = QPushButton("Выбрать картинку (PNG)…")
        vb1.setToolTip("Картинка крутится вместо пластинки — только в этой теме")
        vb1.clicked.connect(self._pick_vinyl_image)
        vb2 = QPushButton("Обычная пластинка")
        vb2.clicked.connect(lambda: (self._set(N + ("image",), "", "vinyl_img"), self._vinyl_img_note()))
        dv.addWidget(_row(vb1, vb2, stretch_last=True))
        dv.addWidget(self._vinyl_img_lbl)
        self._vinyl_img_note()
        B = N + ("bg",)
        dv.addWidget(self._section_label("Фон с обложкой за диском"))
        dv.addWidget(self._check(B + ("on",), "Показывать", scope="native_bg"))
        dv.addWidget(self._slider(B + ("dx",), "Сдвиг ↔", -1, 1, 0.005, "{:.3f}", scope="native_bg", default=0))
        dv.addWidget(self._slider(B + ("dy",), "Сдвиг ↕", -1, 1, 0.005, "{:.3f}", scope="native_bg", default=0))
        dv.addWidget(self._slider(B + ("scale",), "Размер фона", 0.3, 3, 0.01, "{:.2f}×", scope="native_bg",
                                  default=1))
        dv.addWidget(self._hint("Саму пластинку и её фон можно двигать и растягивать прямо на окне: "
                                "«Слои» → «Расставить на экране»."))
        v.addWidget(d)
        s, sv = _card("Перемотка и кнопка Play")
        sv.addWidget(self._color(("components", "seek", "color1"), "Полоса: начало", allow_empty=True,
                                 empty_text="акцент"))
        sv.addWidget(self._color(("components", "seek", "color2"), "Полоса: конец", allow_empty=True,
                                 empty_text="акцент 2"))
        sv.addWidget(_row(QLabel("Кнопка Play"), None,
                          self._combo(("components", "play", "style"),
                                      [("text", "Цвета текста"), ("accent", "Цвета акцента")])))
        v.addWidget(s)
        P = ("components", "panels")
        p, pv = _card("Панели", "Спрятанную панель можно вернуть здесь же.")
        pv.addWidget(_row(self._check(P + ("left",), "Левая (библиотека)"), self._check(P + ("right",),
                                                                                      "Правая (настройки)"),
                          stretch_last=True))
        pv.addWidget(self._check(P + ("swap",), "Поменять панели местами"))
        pv.addWidget(self._check(P + ("dock",), "Стеклянная плашка под кнопками управления",
                                 tip="Кнопки и перемотка на матовом стекле. С живыми обоями ещё и легче для компьютера"))
        pv.addWidget(_row(self._check(P + ("profile",), "Профиль"), self._check(P + ("hints",), "Подсказки"),
                          stretch_last=True))
        v.addWidget(p)
        T = ("components", "titlebar")
        tb, tv = _card("Заголовок окна", "Без своего цвета заголовок прозрачный — фон темы заходит под него.")
        tv.addWidget(self._color(T + ("bg",), "Фон заголовка", allow_empty=True, empty_text="прозр."))
        tv.addWidget(self._color(T + ("fg",), "Текст заголовка", allow_empty=True, empty_text="как текст"))
        tv.addWidget(self._check(T + ("line",), "Линия акцента снизу"))
        v.addWidget(tb)
        v.addStretch(1)
        return w

    def _ui_mode_changed(self):
        if self.t["components"].get("ui") == "layers" and not any(L["kind"] == "script" for L in self.t["layers"]):
            r = QMessageBox.question(self, "Только слои",
                                     "Обычный интерфейс будет спрятан. Добавить кнопки, перемотку, пластинку и "
                                     "остальное слоями, чтобы плеером можно было пользоваться?")
            if r == QMessageBox.StandardButton.Yes:
                self.apply_preset("player_ui")

    # ── страница «Режим текста» ── #

    def _page_lyrics(self):
        w, v = self._page()
        L = ("lyrics", "text")
        c, cv = _card("Режим текста", "Экран с текстом песни — отдельный вид в этой же теме: свои фон, слои, "
                                       "виджеты, эффекты и раскладка, свои шрифты и цвета текста. Цвета окна, "
                                       "кнопок и формы — общие с основным видом.")
        self._lyr_on = QCheckBox("Своё оформление режима текста")
        self._lyr_on.setToolTip("Выключено — режим текста как обычно, на фоне основного вида темы")
        self._lyr_on.toggled.connect(self._toggle_lyrics_design)
        self._bind("lyrics", lambda: (self._lyr_on.blockSignals(True),
                                      self._lyr_on.setChecked(bool((self.t.get("lyrics") or {}).get("on"))),
                                      self._lyr_on.blockSignals(False)))
        cv.addWidget(self._lyr_on)
        ed = QPushButton("Править режим текста")
        ed.setObjectName("Primary")
        ed.setToolTip("Плеер откроет текст песни; страницы «Фон», «Слои», «Эффекты» и раскладка на окне — "
                      "теперь про режим текста")
        ed.clicked.connect(lambda: self.set_mode("lyrics"))
        cv.addWidget(_row(ed, stretch_last=True))
        cv.addWidget(self._hint("Двигать и растягивать текст, пластинку, подпись, кнопку «Закрыть» и любые "
                                "кнопки плеера — «Расставить на экране» (страница «Слои»). Кнопки, перемотка "
                                "и т.п., вынутые в режиме текста, остаются видны поверх него."))
        cp1 = QPushButton("Фон и эффекты — как в основном виде")
        cp1.clicked.connect(lambda: self._lyrics_copy_main(("background", "effects")))
        cp2 = QPushButton("Скопировать слои основного вида")
        cp2.clicked.connect(lambda: self._lyrics_copy_main(("layers",)))
        cv.addWidget(_row(cp1, cp2, stretch_last=True))
        rs = QPushButton("Раскладка режима текста — как было")
        rs.setToolTip("Текст, пластинка, подпись и кнопки — на стандартные места")
        rs.clicked.connect(self._lyrics_reset_layout)
        cv.addWidget(_row(rs, stretch_last=True))
        v.addWidget(c)

        f, fv = _card("Шрифт текста песни")
        self._lyr_font_cb = QFontComboBox()
        self._lyr_font_cb.setEditable(False)
        self._lyr_font_cb.currentFontChanged.connect(self._lyrics_font_family)
        self._lyr_font_lbl = QLabel()
        self._lyr_font_lbl.setObjectName("Hint")
        up = QPushButton("Свой шрифт файлом…")
        up.setToolTip("TTF / OTF — шрифт сохранится внутри темы")
        up.clicked.connect(self._lyrics_font_file)
        dflt = QPushButton("Как в Яндекс Музыке")
        dflt.setToolTip("Стандартный шрифт режима текста")
        dflt.clicked.connect(lambda: self._lyrics_font_set("", ""))
        fv.addWidget(self._lyr_font_cb)
        fv.addWidget(_row(self._lyr_font_lbl, None, up, dflt))
        self._bind("lyrics", self._refresh_lyrics_font)
        S = "lyrics_text"
        fv.addWidget(self._slider(L + ("translation_scale",), "Размер перевода под оригиналом", 0.4, 1.5,
                                  0.01, "{:.2f}×", "lyrics", S, default=0.75))
        fv.addWidget(self._color(L + ("translation_color",), "Цвет перевода", "lyrics", S,
                                 allow_empty=True, empty_text="как оригинал"))
        fv.addWidget(self._check(L + ("manual_scroll",), "Только ручная прокрутка", "lyrics", S))
        fv.addWidget(self._slider(L + ("scroll_speed",), "Скорость прокрутки", 1, 20, 0.1,
                                  "{:.1f}", "lyrics", S, default=7))
        fv.addWidget(self._slider(L + ("size",), "Размер, px (0 — авто)", 0, 96, 1, "{:.0f}", "lyrics", S,
                                  cast=int, default=0))
        fv.addWidget(self._slider(L + ("active_scale",), "Текущая строка крупнее", 0.6, 2.5, 0.01, "{:.2f}×",
                                  "lyrics", S, default=1))
        fv.addWidget(_row(QLabel("Насыщенность"), None, self._combo(L + ("weight",), WEIGHTS, "lyrics", S)))
        fv.addWidget(_row(self._check(L + ("italic",), "Курсив", "lyrics", S),
                          self._check(L + ("upper",), "ПРОПИСНЫЕ", "lyrics", S), stretch_last=True))
        fv.addWidget(self._slider(L + ("letter",), "Межбуквенный", -4, 20, 0.1, "{:.1f} px", "lyrics", S, default=0))
        fv.addWidget(self._slider(L + ("line_gap",), "Между строками", 0.2, 4, 0.01, "{:.2f}×", "lyrics", S,
                                  default=1))
        fv.addWidget(_row(QLabel("Выравнивание"), None, self._combo(L + ("align",), tk.LYRICS_ALIGN, "lyrics", S)))
        fv.addWidget(self._slider(L + ("anchor",), "Текущая строка — на высоте", 0.05, 0.95, 0.01, "{:.0%}",
                                  "lyrics", S, default=0.4, tip="Где по высоте панели стоит строка, которую поют"))
        v.addWidget(f)

        k, kv = _card("Цвет и эффекты текста")
        kv.addWidget(self._color(L + ("color",), "Текущая строка", "lyrics", S, allow_empty=True))
        kv.addWidget(self._color(L + ("dim_color",), "Остальные строки", "lyrics", S, allow_empty=True,
                                 empty_text="как текущая"))
        kv.addWidget(self._slider(L + ("dim",), "Яркость остальных строк", 0, 2.5, 0.01, "{:.2f}×", "lyrics", S,
                                  default=1))
        kv.addWidget(self._slider(L + ("glow",), "Свечение текущей строки", 0, 1, 0.01, "{:.2f}", "lyrics", S,
                                  default=0))
        kv.addWidget(self._color(L + ("glow_color",), "Цвет свечения", "lyrics", S, allow_empty=True,
                                 empty_text="цвет строки"))
        kv.addWidget(self._slider(L + ("shadow",), "Тень", 0, 1, 0.01, "{:.2f}", "lyrics", S, default=0))
        kv.addWidget(self._check(L + ("karaoke",), "Караоке: текущая строка заливается по мере пения", "lyrics", S,
                                 tip="Только для текста с таймингами (LRC)"))
        kv.addWidget(self._slider(L + ("fade",), "Таяние к краям", 0, 0.5, 0.01, "{:.2f}", "lyrics", S,
                                  default=0.22))
        kv.addWidget(self._color(L + ("panel",), "Подложка под текстом", "lyrics", S, allow_empty=True,
                                 empty_text="нет", tip="Цвет с прозрачностью — полупрозрачная плашка"))
        kv.addWidget(self._slider(L + ("panel_radius",), "Скругление подложки", 0, 80, 1, "{:.0f} px", "lyrics", S,
                                  cast=int, default=18))
        kv.addWidget(self._check(L + ("time",), "Время «0:00 / 3:12» в углу", "lyrics", S))
        v.addWidget(k)

        p, pv = _card("Подпись под пластинкой", "Название, исполнитель и полоска прогресса.")
        pv.addWidget(self._slider(L + ("cap_size",), "Размер названия", 8, 40, 1, "{:.0f} px", "lyrics", S,
                                  cast=int, default=14))
        pv.addWidget(self._check(L + ("cap_progress",), "Полоска прогресса", "lyrics", S))
        v.addWidget(p)
        v.addStretch(1)
        QTimer.singleShot(0, lambda: self._refresh_all("lyrics"))
        return w

    def _lyrics(self) -> dict:
        return self.t.setdefault("lyrics", tk._normalize_lyrics(None))

    def _toggle_lyrics_design(self, on):
        full = self.full()
        if on:
            tk.lyrics_start(full)
            self._set_full(full)
            self.set_mode("lyrics")
        else:
            if self._mode == "lyrics":
                self.set_mode("main")
            self._lyrics()["on"] = False
            self.changed("full")
        self._refresh_all("lyrics")

    def _lyrics_copy_main(self, parts):
        """Части основного вида → в режим текста (копией)."""
        full = self.full()
        ly = tk.lyrics_start(full)
        for k in parts:
            ly[k] = copy.deepcopy(full[k])
            if k == "layers":
                for L_ in ly[k]:
                    L_["id"] = uuid.uuid4().hex[:8]
        self._set_full(full)
        self._sel = None
        self._after_reload(preview=True)
        self._commit_soon()
        if self._mode != "lyrics":
            self.status.setText(_tr("Скопировано в режим текста"))

    def _lyrics_reset_layout(self):
        nat = self._lyrics().setdefault("native", {}) if self._mode != "lyrics" else self.t["native"]
        for key in [k for k in nat if k.startswith("lyr_")]:
            if self._mode == "lyrics":
                self.set_native(key, {}, commit=False)
            else:
                nat.pop(key, None)
        self.native_committed()
        self.changed("full")

    def _lyrics_font_set(self, family, rel):
        tx = self._lyrics()["text"]
        tx["family"], tx["file"] = family, rel
        self._refresh_lyrics_font()
        self.changed("lyrics_text")

    def _lyrics_font_family(self, f):
        if getattr(self, "_lyr_font_sync", False):
            return
        self._lyrics_font_set(f.family(), "")

    def _pick_vinyl_image(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Картинка вместо пластинки", "",
                                            "Картинки (*.png *.webp *.jpg *.jpeg *.bmp *.gif)")
        if not fn:
            return
        rel = self.store.import_asset(self.t, fn)
        self._set(("components", "vinyl", "image"), rel, "vinyl_img")
        self._vinyl_img_note()

    def _vinyl_img_note(self):
        lb = getattr(self, "_vinyl_img_lbl", None)
        if lb is None:
            return
        rel = (self.t or {}).get("components", {}).get("vinyl", {}).get("image") or ""
        lb.setText(("Сейчас: " + Path(rel).name + " — крутится вместо диска (стиль «Пластинка»)") if rel else
                   "Сейчас обычная пластинка. PNG с прозрачностью крутится вместе с музыкой.")

    def _lyrics_font_file(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Шрифт", "", FONT_FILTER)
        if not fn:
            return
        rel = self.store.import_asset(self.t, fn)
        fid = QFontDatabase.addApplicationFont(self.store.asset(self.t, rel))
        fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
        if not fams:
            QMessageBox.warning(self, "Шрифт", "Этот файл не похож на шрифт.")
            return
        self._lyrics_font_set(fams[0], rel)

    def _refresh_lyrics_font(self):
        if not hasattr(self, "_lyr_font_cb"):
            return
        tx = self._lyrics().get("text") or {}
        self._lyr_font_sync = True
        try:
            self._lyr_font_cb.blockSignals(True)
            if tx.get("family"):
                self._lyr_font_cb.setCurrentFont(QFont(tx["family"]))
            self._lyr_font_cb.blockSignals(False)
        finally:
            self._lyr_font_sync = False
        if tx.get("file"):
            self._lyr_font_lbl.setText(f"Файл: {Path(tx['file']).name}")
        else:
            self._lyr_font_lbl.setText(tx.get("family") or _tr("Стандартный (как в Яндекс Музыке)"))

    # ── страница «Сохранить» ── #

    # ── страница «Режим клипа» ── #

    def _clip_theme_data(self) -> dict:
        """Тема так, как её увидит режим клипа (без пересборки всего окна)."""
        full = copy.deepcopy(self.full())
        d = tk.to_theme_data(full)
        d["ct"] = full
        d["_font_family"] = full["font"].get("family") or ""
        d["_title_family"] = full["font"].get("title_family") or d["_font_family"]
        return d

    def _clip_restyle(self):
        fn = getattr(self.win, "_clip_restyle", None)
        if fn is not None:
            try:
                fn(self._clip_theme_data())
            except Exception:                              # noqa: BLE001
                import traceback
                traceback.print_exc()

    def _toggle_clip_preview(self, on):
        on = bool(on)
        self._clip_preview = on
        if on:
            self.go("clip")
            self._clip_restyle()
        fn = getattr(self.win, "open_clip_mode", None)
        if fn is not None:
            try:
                fn(on)
            except Exception as e:                         # noqa: BLE001
                self.status.setText(_tr("Режим клипа не открылся: ") + str(e))
        if on and self.canvas is not None:
            self.set_canvas(False)
        self.status.setText(_tr("Плеер показывает режим клипа — правки видны сразу") if on else
                            _tr("Правите основной вид темы"))

    def _page_help(self):
        """Справка для новичка: как собрать тему, что где, скрипты с примерами."""
        import studio_help
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 10, 10, 10)
        self._help_view = studio_help.HelpView(open_ref=lambda: studio_help.reference_dialog(self))
        v.addWidget(self._help_view, 1)
        return w

    def open_help(self, chapter=None):
        self.go("help")
        if chapter and getattr(self, "_help_view", None) is not None:
            self._help_view.open_chapter(chapter)

    def _page_clip(self):
        w, v = self._page()
        C = ("clip",)
        c, cv = _card("Режим клипа", "Официальный клип песни (кнопка «Клип» или Ctrl+Shift+K). Здесь — его оформление "
                                     "в этой теме: фон, рамка, свечение, панель управления и эффекты, которые "
                                     "включатся сразу. Выключено — клип оформлен по палитре темы.")
        cv.addWidget(self._check(C + ("on",), "Своё оформление режима клипа", "clip", "clip"))
        show = QPushButton("Показать режим клипа на плеере")
        show.setObjectName("Primary")
        show.setCheckable(True)
        show.toggled.connect(lambda on: self.btn_clip_mode.setChecked(on))
        self._bind("clip", lambda: (show.blockSignals(True), show.setChecked(self.btn_clip_mode.isChecked()),
                                    show.blockSignals(False)))
        self.btn_clip_mode.toggled.connect(lambda on: (show.blockSignals(True), show.setChecked(on),
                                                       show.blockSignals(False)))
        cv.addWidget(_row(show, stretch_last=True))
        v.addWidget(c)
        b, bv = _card("Фон")
        bv.addWidget(_row(QLabel("Фон"), None, self._combo(C + ("bg",), tk.CLIP_BG, "clip", "clip")))
        bv.addWidget(self._color(C + ("color",), "Цвет фона", "clip", "clip", allow_empty=True, empty_text="тема"))
        bv.addWidget(self._slider(C + ("dim",), "Затемнение", 0, 1, 0.01, "{:.2f}", "clip", "clip", default=0.55))
        bv.addWidget(self._hint("«Фон темы» — под клипом видны фон и слои этой темы. «Цвета клипа» — фон "
                                "переливается средним цветом кадра, как подсветка телевизора."))
        v.addWidget(b)
        f, fv = _card("Рамка и свечение")
        fv.addWidget(_row(QLabel("Рамка"), None, self._combo(C + ("frame",), tk.CLIP_FRAMES, "clip", "clip")))
        fv.addWidget(self._slider(C + ("radius",), "Скругление", 0, 80, 1, "{:.0f}", "clip", "clip", default=18))
        fv.addWidget(self._color(C + ("border",), "Цвет рамки / неона", "clip", "clip", allow_empty=True,
                                 empty_text="акцент"))
        fv.addWidget(self._slider(C + ("border_w",), "Толщина рамки", 0, 24, 1, "{:.0f}", "clip", "clip", default=0))
        fv.addWidget(self._slider(C + ("glow",), "Свечение вокруг видео", 0, 1, 0.01, "{:.2f}", "clip", "clip",
                                  default=0.5))
        fv.addWidget(self._color(C + ("glow_color",), "Цвет свечения", "clip", "clip", allow_empty=True,
                                 empty_text="из клипа"))
        v.addWidget(f)
        vd, vv = _card("Видео и панель")
        vv.addWidget(self._slider(C + ("scale",), "Размер видео", 0.3, 1, 0.01, "{:.2f}", "clip", "clip",
                                  default=0.82))
        vv.addWidget(_row(QLabel("Вписать"), None, self._combo(
            C + ("fit",), [("contain", "Целиком"), ("cover", "Заполнить (обрезать края)")], "clip", "clip")))
        vv.addWidget(_row(QLabel("Панель управления"), None,
                          self._combo(C + ("controls",), tk.CLIP_CONTROLS, "clip", "clip")))
        vv.addWidget(self._check(C + ("title",), "Название трека сверху", "clip", "clip"))
        vv.addWidget(self._color(C + ("accent",), "Акцент (кнопки, полоска)", "clip", "clip", allow_empty=True,
                                 empty_text="тема"))
        vv.addWidget(self._color(C + ("text",), "Текст", "clip", "clip", allow_empty=True, empty_text="тема"))
        vv.addWidget(self._color(C + ("panel",), "Панели", "clip", "clip", allow_empty=True, empty_text="стекло"))
        v.addWidget(vd)
        e, ev = _card("Эффекты сразу при открытии", "Психоделические эффекты включатся сами, когда откроют клип в "
                                                   "этой теме (их можно выключить в самом клипе). «!» — вспышки: "
                                                   "плеер предупредит перед ними.")
        from clip_fx import EFFECTS as CLIP_EFFECTS
        grid = QWidget()
        g = QGridLayout(grid)
        g.setContentsMargins(0, 0, 0, 0)
        g.setSpacing(5)
        btns = {}

        def toggle_fx(key, on):
            cur = list(self.t["clip"].get("fx") or [])
            if on and key not in cur:
                cur.append(key)
            elif not on and key in cur:
                cur.remove(key)
            self._set(C + ("fx",), cur, "clip")

        def ref_fx():
            cur = set(self.t["clip"].get("fx") or [])
            for k_, b_ in btns.items():
                b_.blockSignals(True)
                b_.setChecked(k_ in cur)
                b_.blockSignals(False)
        for n, (key, title, flash) in enumerate(CLIP_EFFECTS):
            bt = QPushButton(_tr(title) + (" !" if flash else ""))
            bt.setCheckable(True)
            bt.toggled.connect(lambda on, k_=key: toggle_fx(k_, on))
            g.addWidget(bt, n // 3, n % 3)
            btns[key] = bt
        ref_fx()
        self._bind("clip", ref_fx)
        ev.addWidget(grid)
        ev.addWidget(self._slider(C + ("fx_amount",), "Сила эффектов", 0, 1, 0.01, "{:.2f}", "clip", "clip",
                                  default=0.7))
        v.addWidget(e)
        v.addStretch(1)
        return w

    def _page_save(self):
        w, v = self._page()
        c, cv = _card("О теме")
        author = QLineEdit(self.t.get("author", ""))
        author.setPlaceholderText("Автор")
        author.setMaxLength(40)
        author.textEdited.connect(lambda s: self._set(("author",), s, "meta"))
        self._bind("", lambda: author.setText(self.t.get("author", "")) if author.text() != self.t.get("author", "")
                   else None)
        desc = QPlainTextEdit(self.t.get("description", ""))
        desc.setPlaceholderText("Пара слов о теме")
        desc.setFixedHeight(70)
        desc.textChanged.connect(lambda: self._set(("description",), desc.toPlainText()[:300], "meta"))

        def ref_desc():
            if desc.toPlainText() != self.t.get("description", ""):
                desc.blockSignals(True)
                desc.setPlainText(self.t.get("description", ""))
                desc.blockSignals(False)
        self._bind("", ref_desc)
        cv.addWidget(author)
        cv.addWidget(desc)
        v.addWidget(c)
        s, sv = _card("Сохранение")
        b1 = QPushButton("Сохранить и применить")
        b1.setObjectName("Primary")
        b1.clicked.connect(lambda: self.save())
        b2 = QPushButton("Сохранить как новую тему")
        b2.clicked.connect(lambda: self.save(as_new=True))
        sv.addWidget(_row(b1, b2, stretch_last=True))
        b3 = QPushButton("Экспорт в файл…")
        b3.setToolTip("Один файл .echoestheme со всеми картинками, GIF, видео и шрифтами — можно отправить другу")
        b3.clicked.connect(lambda: self._export(None))
        b4 = QPushButton("Импорт…")
        b4.clicked.connect(self.import_theme)
        sv.addWidget(_row(b3, b4, stretch_last=True))
        where = QLabel(f"Темы хранятся в папке:\n{self.store.root}")
        where.setObjectName("Hint")
        where.setWordWrap(True)
        where.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        op = QPushButton("Открыть папку тем")
        op.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.root))))
        sv.addWidget(where)
        sv.addWidget(_row(op, stretch_last=True))
        v.addWidget(s)
        d, dv = _card("Опасная зона")
        rm = QPushButton("Удалить эту тему")
        rm.setObjectName("Danger")
        rm.clicked.connect(lambda: self._delete_saved(self.full()) if self._saved is not None else None)
        dv.addWidget(_row(rm, stretch_last=True))
        v.addWidget(d)
        v.addStretch(1)
        return w

    # ── действия ── #

    def _on_name(self, s):
        self.t["name"] = s.strip()[:60] or _tr("Моя тема")
        self.changed("meta")

    def open_theme(self, theme_id=None):
        """Открыть сохранённую тему (или начать новую, если id нет)."""
        if self.isVisible() and not self._confirm_discard():
            return
        self._discard_orphan()
        t = self.store.load(theme_id) if theme_id else None
        if t is not None:
            self._set_full(t, "lyrics" if getattr(self.win, "_lyrics_mode", False) else "main")
            self._saved = self._json()
        else:
            self._set_full(self._fresh(tk.templates()[0]), "main")
            self._saved = None
        self._undo, self._redo = [], []
        self._snap = self._json()
        self._sel = self.t["layers"][-1]["id"] if self.t["layers"] else None
        self._multi = []
        self._after_reload()
        self._refresh_mine()
        if t is None:
            self.go("start")

    def new_from(self, tpl):
        if not self._confirm_discard():
            return
        self._discard_orphan()
        self._set_full(self._fresh(tpl), "main")
        self._saved = None
        self._undo, self._redo = [], []
        self._snap = self._json()
        self._sel = None
        self._multi = []
        self._after_reload()

    def surprise(self):
        if self._saved is not None and self.dirty() and not self._confirm_discard():
            return
        prev = self._json() if self._saved is None else None
        self._discard_orphan()
        t = tk.surprise()
        names = {x["name"] for x in self.store.list()}
        t["name"] = tk.suggest_name(names, t["name"])
        t["id"] = ""
        self._set_full(t, "main")
        self._saved = None
        if prev is not None:                               # «Удиви меня» ещё раз можно отменить
            self._undo.append(prev)
        else:
            self._undo = []
        self._redo = []
        self._snap = self._json()
        self._sel = None
        self._after_reload()

    def revert(self):
        if self._saved is None:
            return
        self._load_json(self._saved)
        self._snap = self._saved

    def save(self, as_new=False):
        self._commit_t.stop()
        self._commit()
        t = self.full()
        others = {x["name"] for x in self.store.list() if x["id"] != t.get("id")}
        if as_new:
            others = {x["name"] for x in self.store.list()}
        if t["name"] in others:
            t["name"] = tk.suggest_name(others, t["name"])
        try:
            if as_new and t.get("id") and self._saved is not None:
                saved = self.store.duplicate(t, t["name"])
            else:
                saved = self.store.save(t)
        except OSError as e:
            QMessageBox.warning(self, "Сохранение", f"Не удалось сохранить тему:\n{e}")
            return False
        self._set_full(saved)
        self._saved = self._json()
        self._snap = self._saved
        self.win.custom_themes_changed(select_id=saved["id"])
        self._after_reload(preview=False)
        self._refresh_mine()
        self.status.setText("Сохранено и применено")
        return True

    def _export(self, t):
        mine = t is None                                   # None — тема, открытая в конструкторе
        t = self.full() if mine else t
        name = tk.slugify(t.get("name") or "theme") + tk.PACKAGE_EXT
        fn, _ = QFileDialog.getSaveFileName(self, "Экспорт темы", str(Path.home() / name),
                                            f"Тема ECHOES (*{tk.PACKAGE_EXT})")
        if not fn:
            return
        try:
            if mine and self.dirty():
                if not self.save():
                    return
                t = self.full()
            out = self.store.export(t, fn)
            self.status.setText(f"Сохранено в файл: {Path(out).name}")
        except Exception as e:                             # noqa: BLE001
            QMessageBox.warning(self, "Экспорт", f"Не получилось:\n{e}")

    def import_theme(self, path=None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Импорт темы", str(Path.home()),
                                                  f"Тема ECHOES (*{tk.PACKAGE_EXT});;{ECHO_FILTER};;Все файлы (*)")
        if not path:
            return
        if path.lower().endswith(".echo"):
            self.import_echo(path)
            return
        try:
            t = self.store.import_package(path)
        except Exception as e:                             # noqa: BLE001
            QMessageBox.warning(self, "Импорт", f"Это не тема ECHOES или файл повреждён:\n{e}")
            return
        self.win.custom_themes_changed()
        self._refresh_mine()
        self.open_theme(t["id"])

    def drop_files(self, files, pos=None):
        """Файлы из проводника: картинки/GIF → слои (или фон на странице «Фон»), видео → фон, шрифт → текст."""
        page = self.PAGES[self.nav.currentRow()][0] if hasattr(self, "nav") else ""
        added = 0
        for fn in files:
            ext = Path(fn).suffix.lower()
            if ext == tk.PACKAGE_EXT:
                self.import_theme(fn)
                return
            if ext == ".echo":
                self.import_echo(fn, as_layers=self.canvas is not None or page in ("layers", "library"))
                return
            if ext in _VID_EXT and (self.canvas is not None or page == "layers"):
                rel = self.store.import_asset(self.t, fn)       # на окно / на страницу слоёв — видео-слоем
                if rel:
                    p = pos or (0.5, 0.5)
                    L = self.add_layer("video", src=rel, pos=(min(1.2, p[0] + added * 0.03),
                                                               min(1.2, p[1] + added * 0.03)))
                    if L is not None:
                        L["name"] = Path(fn).stem[:30] or L["name"]
                        added += 1
                continue
            if ext in _VID_EXT or (ext in _IMG_EXT and page == "bg" and self.canvas is None):
                rel = self.store.import_asset(self.t, fn)
                if rel:
                    self.t["background"]["type"] = "video" if ext in _VID_EXT else "media"
                    self.t["background"]["media"] = rel
                    self._refresh_all()
                    self._rebuild_bg_box()
                    self.changed("full")
            elif ext in _IMG_EXT:
                rel = self.store.import_asset(self.t, fn)
                if rel:
                    p = pos or (0.5, 0.5)
                    L = self.add_layer("media", src=rel, pos=(min(1.2, p[0] + added * 0.03),
                                                               min(1.2, p[1] + added * 0.03)))
                    if L is not None:
                        L["name"] = Path(fn).stem[:30] or L["name"]
                        added += 1
            elif ext in _FONT_EXT:
                rel = self.store.import_asset(self.t, fn)
                L = self.selected_layer()
                if rel and L is not None and L["kind"] == "text":
                    self.set_layer_value("font", rel)
                elif rel:
                    fid = QFontDatabase.addApplicationFont(self.store.asset(self.t, rel))
                    fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
                    if fams:
                        self.t["font"]["file"], self.t["font"]["family"] = rel, fams[0]
                        self._refresh_font_labels()
                        self.changed("full")
        if added:
            self._refresh_layer_list()
            self.go("layers")

    def set_canvas(self, on):
        on = bool(on)
        if on and self.canvas is None:
            if not self.win._custom_on:
                self._preview(False)
            self.canvas = LayerCanvas(self)
            self.win.raise_()
            self.win.activateWindow()
            self.canvas.setFocus()
        elif not on and self.canvas is not None:
            self.canvas.close_canvas()
            self.canvas = None
            self._commit()
        if hasattr(self, "btn_canvas") and self.btn_canvas.isChecked() != on:
            self.btn_canvas.blockSignals(True)
            self.btn_canvas.setChecked(on)
            self.btn_canvas.blockSignals(False)

    # ── закрытие ── #

    def _confirm_discard(self) -> bool:
        if not self.dirty():
            return True
        r = QMessageBox.question(self, "Конструктор тем", f"Сохранить тему «{self.t['name']}»?",
                                 QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard |
                                 QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Save)
        if r == QMessageBox.StandardButton.Save:
            return bool(self.save())
        return r == QMessageBox.StandardButton.Discard

    def _discard_orphan(self):
        """Несохранённая тема успела завести папку под картинки — убрать её."""
        tid = self.t.get("id")
        if self._saved is None and tid:
            d = self.store.dir_of(tid)
            if d.exists() and not (d / "theme.json").exists() and d.parent == self.store.root:
                shutil.rmtree(d, ignore_errors=True)

    def confirm_app_close(self) -> bool:
        """Плеер закрывается: сохранить тему? (False — пользователь передумал выходить)."""
        if not self._confirm_discard():
            return False
        self._app_closing = True
        self.set_canvas(False)
        self._discard_orphan()
        return True

    def closeEvent(self, e):
        if not getattr(self, "_app_closing", False) and not self._confirm_discard():
            e.ignore()
            return
        self.set_canvas(False)
        try:
            if getattr(self, "_clip_preview", False):
                self.win.open_clip_mode(False)
        except Exception:                                  # noqa: BLE001
            pass
        for dlg in list(self._code.values()) + [getattr(self, "_btn_ed", None), getattr(self, "_bg_ed", None)]:
            try:
                if dlg is not None:
                    dlg.close()
            except RuntimeError:
                pass
        self._discard_orphan()
        self._pv.stop()
        if not getattr(self, "_app_closing", False):
            self.win.custom_preview(None)                  # вернуть выбранную тему (предпросмотр закончен)
            QTimer.singleShot(0, self._dispose)
        e.accept()

    def _dispose(self):
        """Закрытый конструктор не держит память (~50 МБ виджетов и картинок): удаляем его,
        при следующем открытии он соберётся заново — темы и настройки хранятся на диске."""
        try:
            if self.isVisible():                           # успели открыть снова
                return
            if getattr(self.win, "_studio", None) is self:
                self.win._studio = None
            self.deleteLater()
            import mem_trim
            mem_trim.trim_later(2000)
        except RuntimeError:
            pass

    # ── перетаскивание на окно конструктора ── #

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.drop_files([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])
        e.acceptProposedAction()
