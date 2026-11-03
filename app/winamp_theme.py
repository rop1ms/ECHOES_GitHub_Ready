# winamp_theme.py
"""
Тема «Winamp» — классический Winamp 2.x + окно MilkDrop.

Силуэт (как на скриншоте Winamp 2 + MilkDrop):

    ┌─ WINAMP ────────────┐┌─ MILKDROP ───────────────────────────────┐
    │ ▶ 00:04  ▂▅▇▅▂ ▶▶▶ ││                                          │
    │ 1. ARTIST - TITLE…  ││     буфер обратной связи, волна звука,   │
    │ 192 kbps 44 kHz     ││     зум/поворот/варп от баса/средних/ВЧ,  │
    │ [===seek=========]  ││     вспышки на удары, смена пресетов     │
    │ |◀ ▶ ▶▶|  shuffle  ││                                          │
    ├─ WINAMP EQUALIZER ──┤│                                          │
    │  ▮▮▮▮▮▮▮▮▮▮         ││                                          │
    ├─ WINAMP PLAYLIST ───┤│                                          │
    │ 1. track   3:34     ││                                          │
    │ 2. track   5:22     ││                                          │
    └─────────────────────┘└──────────────────────────────────────────┘

Все виджеты те же, что в main.py — раскладку перестраивает
MainWindow._enter_winamp_layout(); здесь — стили, хром окон, ЖК-дисплей
и визуализатор MilkDrop.

Производительность MilkDrop: вся тяжёлая numpy-математика
(milkdrop_core) крутится в ФОНОВОМ потоке — numpy на больших массивах
отпускает GIL, так что интерфейс не ждёт кадр. GUI-поток только забирает
готовый буфер, оборачивает его в QImage (без копий) и рисует со
сглаживанием. Разрешение адаптивное: если кадр дорожает — сетка уменьшается.
"""

from __future__ import annotations

import math
import threading
import time

import numpy as np
from PyQt6.QtCore import Qt, QTimer, QRectF, QRect, QPoint, QPointF, pyqtSignal
from frameclock import FrameTimer, animation_delta
from PyQt6.QtGui import (
    QColor, QFont, QImage, QPainter, QPixmap, QPen, QLinearGradient, QBrush,
    QPolygonF,
)
from PyQt6.QtWidgets import QFrame, QVBoxLayout, QWidget

from milkdrop_core import MilkdropCore


# ══════════════════════════════════════════════════════════════════════════ #
#  Палитра и QSS
# ══════════════════════════════════════════════════════════════════════════ #

DESKTOP = "#3a6ea5"       # «рабочий стол» за окнами
BODY = "#262638"          # тело окон
BODY_DARK = "#1b1b29"
LCD_BG = "#000000"
LCD_GREEN = "#00e000"
LCD_DIM = "#0b3a0b"
BTN_HI = "#e6e6ee"
BTN_LO = "#8a8aa0"
TEXT = "#d6d6e4"
PL_GREEN = "#00ff00"
PL_SEL = "#0000c6"


def winamp_theme_dict() -> dict:
    return {
        "winamp": True,
        "bg": DESKTOP,
        "panel": BODY,
        "panel2": BODY_DARK,
        "glass": "rgba(0,0,0,0.25)",
        "glass2": "rgba(255,255,255,0.06)",
        "text": TEXT,
        "muted": "#8f8fa8",
        "accent": LCD_GREEN,
        "accent2": "#f0c020",
        "glow": "#000000",
        "border": "#4a4a64",
        "border2": "#6a6a88",
        "danger": "#ff4040",
    }


_BEVEL_BTN = f"""
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {BTN_HI}, stop:1 {BTN_LO});
    color: #10101a;
    border-top: 1px solid #ffffff; border-left: 1px solid #ffffff;
    border-bottom: 1px solid #2a2a36; border-right: 1px solid #2a2a36;
    border-radius: 0px;
"""
_BEVEL_BTN_DOWN = f"""
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {BTN_LO}, stop:1 {BTN_HI});
    border-top: 1px solid #2a2a36; border-left: 1px solid #2a2a36;
    border-bottom: 1px solid #ffffff; border-right: 1px solid #ffffff;
"""


def winamp_qss(t: dict) -> str:
    font = '"Tahoma", "Verdana", "Arial", sans-serif'
    return f"""
    * {{ font-family: {font}; font-size: 11px; color: {TEXT}; }}
    QMainWindow, QWidget#Root {{ background: {DESKTOP}; }}
    QLabel {{ background: transparent; color: {TEXT}; }}

    /* Окна рисует WinampFrame сам — панели внутри прозрачные */
    QFrame#Panel, QFrame#SubPanel, QFrame#CenterStage, QFrame#DockCard
        {{ background: transparent; border: none; border-radius: 0px; }}
    QFrame#WinampFrame {{ background: transparent; border: none; }}

    QLabel#Section {{ color: #e8e8f4; font-size: 10px; font-weight: bold; }}
    QLabel#Big  {{ color: {LCD_GREEN}; font-size: 12px; font-weight: bold; }}
    QLabel#Sub  {{ color: #9a9ab4; font-size: 10px; }}
    QLabel#Time {{ color: {LCD_GREEN}; font-size: 10px; }}
    QLabel#Title, QLabel#Artist {{ color: {LCD_GREEN}; }}

    /* ── Кнопки: выпуклая «железная» фаска ── */
    QPushButton {{ {_BEVEL_BTN} padding: 3px 8px; font-size: 10px; font-weight: bold; }}
    QPushButton:hover {{ color: #000000; }}
    QPushButton:pressed {{ {_BEVEL_BTN_DOWN} padding: 4px 7px 2px 9px; }}
    QPushButton:checked {{ background: #14141e; color: {LCD_GREEN};
        border-top: 1px solid #000; border-left: 1px solid #000;
        border-bottom: 1px solid #6a6a88; border-right: 1px solid #6a6a88; }}
    QPushButton:disabled {{ color: #5a5a6a; }}

    QPushButton#PlayBtn {{ {_BEVEL_BTN} min-width: 34px; max-width: 34px;
                          min-height: 24px; max-height: 24px; padding: 0; }}
    QPushButton#PlayBtn:pressed {{ {_BEVEL_BTN_DOWN} }}
    QPushButton#IconBtn {{ {_BEVEL_BTN} min-width: 30px; max-width: 90px;
                          min-height: 24px; max-height: 24px; padding: 0 6px;
                          font-size: 10px; font-weight: bold; }}
    QPushButton#IconBtn:pressed {{ {_BEVEL_BTN_DOWN} }}
    QPushButton#IconBtn:checked {{ background: #14141e; color: {LCD_GREEN}; }}
    QPushButton#GhostBtn, QPushButton#AccentBtn {{ {_BEVEL_BTN} padding: 3px 8px; }}
    QPushButton#GhostBtn:pressed, QPushButton#AccentBtn:pressed {{ {_BEVEL_BTN_DOWN} }}
    QPushButton#GhostBtn:checked {{ background: #14141e; color: {LCD_GREEN}; }}

    /* ── Поля: чёрный ЖК с зелёным текстом ── */
    QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox {{
        background: {LCD_BG}; color: {LCD_GREEN};
        border-top: 1px solid #0c0c12; border-left: 1px solid #0c0c12;
        border-bottom: 1px solid #5e5e7a; border-right: 1px solid #5e5e7a;
        border-radius: 0px; padding: 3px 6px; selection-background-color: {PL_SEL};
        selection-color: #ffffff; }}
    QComboBox::drop-down {{ border: none; width: 16px; background: {BTN_LO}; }}
    QComboBox QAbstractItemView {{ background: {LCD_BG}; color: {LCD_GREEN};
        border: 1px solid #5e5e7a; selection-background-color: {PL_SEL};
        selection-color: #ffffff; }}

    /* ── Плейлист: зелёный по чёрному, выделение синим ── */
    QListWidget {{ background: {LCD_BG}; color: {PL_GREEN}; outline: none;
        border-top: 1px solid #0c0c12; border-left: 1px solid #0c0c12;
        border-bottom: 1px solid #5e5e7a; border-right: 1px solid #5e5e7a;
        font-family: "Arial", sans-serif; font-size: 11px; }}
    QListWidget::item {{ padding: 1px 4px; margin: 0px; border: none; min-height: 0px; }}
    QListWidget::item:hover {{ color: #ffffff; }}
    QListWidget::item:selected {{ background: {PL_SEL}; color: #ffffff; }}

    /* ── Слайдеры: утопленный жёлоб + «железный» ползунок ── */
    QSlider::groove:horizontal {{ height: 6px; background: #0a0a10;
        border-top: 1px solid #000; border-bottom: 1px solid #5e5e7a; }}
    QSlider::sub-page:horizontal {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #1d6b1d, stop:0.6 #2cc82c, stop:0.85 #d8d020, stop:1 #e03020); }}
    QSlider::handle:horizontal {{ {_BEVEL_BTN} width: 22px; height: 10px; margin: -4px 0; }}
    QSlider::groove:vertical {{ width: 6px; background: #0a0a10;
        border-left: 1px solid #000; border-right: 1px solid #5e5e7a; }}
    QSlider::add-page:vertical {{ background: qlineargradient(x1:0, y1:1, x2:0, y2:0,
        stop:0 #1d6b1d, stop:0.5 #2cc82c, stop:0.8 #d8d020, stop:1 #e03020); }}
    QSlider::sub-page:vertical {{ background: #0a0a10; }}
    QSlider::handle:vertical {{ {_BEVEL_BTN} height: 8px; width: 14px; margin: 0 -5px; }}

    QCheckBox {{ color: {TEXT}; spacing: 6px; }}
    QCheckBox::indicator {{ width: 10px; height: 10px; background: {LCD_BG};
        border-top: 1px solid #0c0c12; border-left: 1px solid #0c0c12;
        border-bottom: 1px solid #6a6a88; border-right: 1px solid #6a6a88; }}
    QCheckBox::indicator:checked {{ background: {LCD_GREEN}; }}

    QProgressBar {{ background: {LCD_BG}; border: 1px solid #5e5e7a; color: {LCD_GREEN};
        text-align: center; height: 12px; }}
    QProgressBar::chunk {{ background: {LCD_GREEN}; }}

    QTabWidget::pane {{ border: none; }}
    QTabBar::tab {{ {_BEVEL_BTN} padding: 3px 10px; margin: 1px; }}
    QTabBar::tab:selected {{ background: #14141e; color: {LCD_GREEN}; }}

    QScrollBar:vertical {{ background: #14141e; width: 10px; margin: 0; }}
    QScrollBar::handle:vertical {{ {_BEVEL_BTN} min-height: 18px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: #14141e; }}
    QScrollBar:horizontal {{ height: 0; }}

    QDialog {{ background: {BODY}; }}
    QMenu {{ background: {BODY}; color: {TEXT}; border: 1px solid #6a6a88; padding: 2px; }}
    QMenu::item {{ padding: 3px 16px; }}
    QMenu::item:selected {{ background: {PL_SEL}; color: #ffffff; }}
    QToolTip {{ background: #ffffe1; color: #000000; border: 1px solid #000; padding: 2px 4px; }}
    """


# ══════════════════════════════════════════════════════════════════════════ #
#  Окно с хромом Winamp
# ══════════════════════════════════════════════════════════════════════════ #

class WinampFrame(QFrame):
    """Рамка-«окно» Winamp: тёмное тело с фаской, заголовок с золотыми
    полосками и кнопками свернуть/свёрнуть в полоску/закрыть (декор).
    Хром кэшируется в QPixmap и перестраивается только при ресайзе."""

    TITLE_H = 16
    menu_clicked = pyqtSignal(QPoint)       # клик по кнопке-меню в левом углу заголовка

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("WinampFrame")
        self._title = title.upper()
        self._pm: QPixmap | None = None
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(7, self.TITLE_H + 5, 7, 7)
        self._lay.setSpacing(4)

    def body(self) -> QVBoxLayout:
        return self._lay

    def set_title(self, title: str):
        self._title = title.upper()
        self._pm = None
        self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._pm = None

    def _build(self) -> QPixmap:
        w, h = self.width(), self.height()
        dpr = max(1.0, float(self.devicePixelRatioF()))
        pm = QPixmap(int(w * dpr), int(h * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        # тело
        g = QLinearGradient(0, 0, 0, h)
        g.setColorAt(0, QColor("#2e2e46"))
        g.setColorAt(1, QColor(BODY_DARK))
        p.fillRect(0, 0, w, h, QBrush(g))
        # фаска: светлая сверху/слева, тёмная снизу/справа
        p.setPen(QColor("#000000")); p.drawRect(0, 0, w - 1, h - 1)
        p.setPen(QColor("#6a6a90")); p.drawLine(1, 1, w - 2, 1); p.drawLine(1, 1, 1, h - 2)
        p.setPen(QColor("#0e0e16")); p.drawLine(1, h - 2, w - 2, h - 2); p.drawLine(w - 2, 1, w - 2, h - 2)

        # заголовок
        th = self.TITLE_H
        p.fillRect(2, 2, w - 4, th, QColor("#1c1c2c"))
        f = QFont("Arial")
        f.setPixelSize(9)
        f.setBold(True)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.6)
        p.setFont(f)
        fm = p.fontMetrics()
        tw = fm.horizontalAdvance(self._title) + 12
        tx = (w - tw) // 2
        # кнопка-меню слева и три кнопки справа
        self._mini_btn(p, 5, 5, "menu")
        bx = w - 5 - 9
        for kind in ("close", "shade", "min"):
            self._mini_btn(p, bx, 5, kind)
            bx -= 11
        # золотые двойные полоски по обе стороны от названия
        left0, left1 = 18, tx - 4
        right0, right1 = tx + tw + 4, bx + 6
        for y in (7, 11):
            for (x0, x1) in ((left0, left1), (right0, right1)):
                if x1 - x0 > 4:
                    p.setPen(QColor("#b9a45e")); p.drawLine(x0, y, x1, y)
                    p.setPen(QColor("#4a4020")); p.drawLine(x0, y + 1, x1, y + 1)
        p.setPen(QColor("#e8e8f4"))
        p.drawText(QRect(tx, 2, tw, th), Qt.AlignmentFlag.AlignCenter, self._title)
        p.end()
        return pm

    @staticmethod
    def _mini_btn(p: QPainter, x: int, y: int, kind: str):
        p.fillRect(x, y, 9, 9, QColor("#9a9ab4"))
        p.setPen(QColor("#ffffff")); p.drawLine(x, y, x + 8, y); p.drawLine(x, y, x, y + 8)
        p.setPen(QColor("#2a2a36")); p.drawLine(x, y + 8, x + 8, y + 8); p.drawLine(x + 8, y, x + 8, y + 8)
        p.setPen(QColor("#10101a"))
        if kind == "close":
            p.drawLine(x + 2, y + 2, x + 6, y + 6); p.drawLine(x + 6, y + 2, x + 2, y + 6)
        elif kind == "min":
            p.drawLine(x + 2, y + 6, x + 6, y + 6)
        elif kind == "shade":
            p.drawRect(x + 2, y + 2, 4, 2)
        else:
            p.drawLine(x + 2, y + 3, x + 6, y + 3); p.drawLine(x + 2, y + 5, x + 6, y + 5)

    def mousePressEvent(self, e):
        # левая мини-кнопка заголовка (как «главное меню» Winamp)
        if e.button() == Qt.MouseButton.LeftButton and QRect(2, 2, 16, 16).contains(e.position().toPoint()):
            self.menu_clicked.emit(self.mapToGlobal(QPoint(5, 16)))
            e.accept()
            return
        super().mousePressEvent(e)

    def paintEvent(self, ev):
        if self._pm is None or self._pm.width() == 0:
            self._pm = self._build()
        p = QPainter(self)
        p.drawPixmap(0, 0, self._pm)
        p.end()


# ══════════════════════════════════════════════════════════════════════════ #
#  ЖК-дисплей главного окна
# ══════════════════════════════════════════════════════════════════════════ #

#   a
#  f b
#   g
#  e c
#   d
_SEG = {
    "0": "abcdef", "1": "bc", "2": "abged", "3": "abgcd", "4": "fgbc",
    "5": "afgcd", "6": "afgedc", "7": "abc", "8": "abcdefg", "9": "abcdfg",
    "-": "g", " ": "",
}


def _draw_7seg(p: QPainter, ch: str, x: float, y: float, w: float, h: float,
               on: QColor, off: QColor):
    t = max(2.0, w * 0.2)
    hh = h / 2.0
    rects = {
        "a": QRectF(x + t, y, w - 2 * t, t),
        "b": QRectF(x + w - t, y + t, t, hh - 1.5 * t + 1),
        "c": QRectF(x + w - t, y + hh + 0.5 * t, t, hh - 1.5 * t),
        "d": QRectF(x + t, y + h - t, w - 2 * t, t),
        "e": QRectF(x, y + hh + 0.5 * t, t, hh - 1.5 * t),
        "f": QRectF(x, y + t, t, hh - 1.5 * t + 1),
        "g": QRectF(x + t, y + hh - t / 2, w - 2 * t, t),
    }
    lit = _SEG.get(ch, "")
    for k, r in rects.items():
        p.fillRect(r, on if k in lit else off)


class WinampDisplay(QWidget):
    """Чёрный ЖК: состояние ▶/‖/■, большие 7-сегментные минуты:секунды,
    спектроанализатор на 19 полос с «пиками», бегущая строка трека,
    kbps / kHz и индикаторы mono/stereo. Всё рисуется вручную (без
    дочерних виджетов), перерисовка — только своего прямоугольника."""

    BANDS = 19

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(74)
        self.setMinimumWidth(300)
        self._engine = None
        self._marquee = "ECHOES"
        self._kbps = ""
        self._khz = ""
        self._channels = 2
        self._scroll = 0.0
        self._bars = np.zeros(self.BANDS, np.float32)
        self._peaks = np.zeros(self.BANDS, np.float32)
        self._runmax = 1e-3
        self._win = np.hanning(1024).astype(np.float32)
        self._edges = None
        self._last = time.monotonic()
        self._blink = 0.0
        self._timer = FrameTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)

    def set_engine(self, engine):
        self._engine = engine

    def set_track(self, marquee: str, kbps: str = "", khz: str = "", channels: int = 2):
        self._marquee = (marquee or "").upper()
        self._kbps, self._khz, self._channels = kbps, khz, channels
        self._scroll = 0.0
        self.update()

    def _playing(self) -> bool:
        try:
            return bool(self._engine is not None and self._engine.is_playing())
        except Exception:                            # noqa: BLE001
            return False

    def _tick(self):
        now = time.monotonic()
        dt = animation_delta(now - self._last)
        self._last = now
        win = self.window()
        if win is not None and win.isMinimized():
            return
        playing = self._playing()
        self._scroll += dt * 28.0
        self._blink += dt
        target = np.zeros(self.BANDS, np.float32)
        if playing:
            try:
                s = self._engine.get_visual_samples(1024)
            except Exception:                        # noqa: BLE001
                s = None
            if s is not None and len(s) == 1024:
                mag = np.abs(np.fft.rfft(np.asarray(s, np.float32) * self._win))
                if self._edges is None:
                    self._edges = np.unique(np.logspace(np.log10(2), np.log10(len(mag) - 1),
                                                        self.BANDS + 1).astype(int))
                e = self._edges
                vals = np.array([mag[e[i]:max(e[i] + 1, e[min(i + 1, len(e) - 1)])].mean()
                                 for i in range(min(self.BANDS, len(e) - 1))], np.float32)
                vals = np.pad(vals, (0, self.BANDS - len(vals)))
                m = float(vals.max())
                self._runmax = max(m, self._runmax * (0.985 ** (dt * 30.0)), 1e-3)
                target = np.clip(np.log1p(vals * 10) / np.log1p(self._runmax * 10), 0, 1)
        up = target > self._bars
        self._bars = np.where(up, target, np.maximum(0, self._bars - dt * 2.2))
        self._peaks = np.where(self._bars > self._peaks, self._bars,
                               np.maximum(0, self._peaks - dt * 0.6))
        # тишина и пауза: бегущая строка и мигание — достаточно ~15 кадров/с
        if not playing and float(self._peaks.max()) < 0.002:
            self._idle_acc = getattr(self, "_idle_acc", 0.0) + dt
            if self._idle_acc < 0.066:
                return
            self._idle_acc = 0.0
        self.update()

    def _time_text(self) -> str:
        pos = 0.0
        try:
            if self._engine is not None and getattr(self._engine, "current_path", None):
                pos = float(self._engine.get_position() or 0.0)
        except Exception:                            # noqa: BLE001
            pos = 0.0
        pos = max(0, int(pos))
        return f"{min(99, pos // 60):02d}:{pos % 60:02d}"

    def paintEvent(self, _):
        p = QPainter(self)
        w, h = self.width(), self.height()
        green, dim = QColor(LCD_GREEN), QColor(LCD_DIM)
        playing = self._playing()

        # ── левый ЖК: состояние + время + спектр ──
        lw = 150
        self._inset(p, QRect(0, 0, lw, h))
        # индикатор состояния
        has_track = bool(self._engine is not None and getattr(self._engine, "current_path", None))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(green)
        if playing:
            p.drawPolygon(QPolygonF([QPointF(9, 9), QPointF(9, 19), QPointF(16, 14)]))
        elif has_track:
            p.fillRect(QRectF(9, 9, 3, 10), green); p.fillRect(QRectF(14, 9, 3, 10), green)
        else:
            p.fillRect(QRectF(9, 10, 8, 8), green)
        # время (на паузе мигает, как в Winamp)
        txt = self._time_text()
        show = playing or not has_track or (self._blink % 1.0) < 0.6
        x = 30.0
        for ch in txt:
            if ch == ":":
                c = green if show else dim
                p.fillRect(QRectF(x + 1, 13, 3, 3), c); p.fillRect(QRectF(x + 1, 23, 3, 3), c)
                x += 7
                continue
            _draw_7seg(p, ch if show else " ", x, 7, 14, 24, green, dim)
            x += 18
        # спектр
        sx, sy, sh = 30, 38, 28
        bw = 4
        grad = QLinearGradient(0, sy + sh, 0, sy)
        grad.setColorAt(0.0, QColor("#18842c"))
        grad.setColorAt(0.55, QColor("#d6b521"))
        grad.setColorAt(1.0, QColor("#ef3110"))
        for i in range(self.BANDS):
            bx = sx + i * (bw + 1)
            bh = int(self._bars[i] * sh)
            if bh > 0:
                p.fillRect(QRect(bx, sy + sh - bh, bw, bh), QBrush(grad))
            pk = int(self._peaks[i] * sh)
            if pk > 0:
                p.fillRect(QRect(bx, sy + sh - pk - 1, bw, 1), QColor("#a8a8b8"))

        # ── правая часть: бегущая строка + kbps/kHz + mono/stereo ──
        rx = lw + 6
        mr = QRect(rx, 0, w - rx, 20)
        self._inset(p, mr)
        f = QFont("Arial")
        f.setPixelSize(11)
        f.setBold(True)
        p.setFont(f)
        p.setPen(green)
        fm = p.fontMetrics()
        text = self._marquee + "  ***  "
        tw = max(1, fm.horizontalAdvance(text))
        off = int(self._scroll) % tw
        p.save()
        p.setClipRect(mr.adjusted(3, 1, -3, -1))
        xx = mr.left() + 4 - off
        while xx < mr.right():
            p.drawText(QRect(xx, mr.top(), tw, mr.height()),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
            xx += tw
        p.restore()

        f.setPixelSize(10)
        p.setFont(f)
        y2 = 26
        for label, val, bw_ in (("kbps", self._kbps, 30), ("kHz", self._khz, 22)):
            box = QRect(rx, y2, bw_, 15)
            self._inset(p, box)
            p.setPen(green)
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, val or "--")
            p.setPen(QColor("#c8c8d8"))
            p.drawText(QRect(box.right() + 4, y2, 30, 15), Qt.AlignmentFlag.AlignVCenter, label)
            rx += bw_ + 36
        stereo = self._channels >= 2
        p.setPen(green if (has_track and not stereo) else dim)
        p.drawText(QRect(rx, y2, 34, 15), Qt.AlignmentFlag.AlignVCenter, "mono")
        p.setPen(green if (has_track and stereo) else dim)
        p.drawText(QRect(rx + 34, y2, 44, 15), Qt.AlignmentFlag.AlignVCenter, "stereo")

        # «лампочки» громкости снизу справа — декоративная линейка уровня
        lvl = float(self._bars[:6].mean()) if playing else 0.0
        by = 48
        n = 18
        cw = max(3, (w - (lw + 6)) // n - 2)
        for i in range(n):
            on = (i + 0.5) / n < lvl
            c = QColor("#2cc82c") if i < n * 0.6 else (QColor("#d8d020") if i < n * 0.85 else QColor("#e03020"))
            if not on:
                c = QColor(c.red() // 6, c.green() // 6, c.blue() // 6)
            p.fillRect(QRect(lw + 6 + i * (cw + 2), by, cw, 8), c)
        p.end()

    @staticmethod
    def _inset(p: QPainter, r: QRect):
        p.fillRect(r, QColor(LCD_BG))
        p.setPen(QColor("#0c0c12"))
        p.drawLine(r.topLeft(), r.topRight()); p.drawLine(r.topLeft(), r.bottomLeft())
        p.setPen(QColor("#5e5e7a"))
        p.drawLine(r.bottomLeft(), r.bottomRight()); p.drawLine(r.topRight(), r.bottomRight())


# ══════════════════════════════════════════════════════════════════════════ #
#  MilkDrop
# ══════════════════════════════════════════════════════════════════════════ #

def _frame_colors(frame: np.ndarray):
    """Два главных цвета текущего кадра MilkDrop (по ярким пикселям, после
    цветового ремапа) — чтобы слова были в палитре визуализации."""
    sub = frame[::6, ::6].ravel()
    r = ((sub >> 16) & 255).astype(np.float32)
    g = ((sub >> 8) & 255).astype(np.float32)
    b = (sub & 255).astype(np.float32)
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    sat = (mx - mn) / (mx + 1e-3)
    score = mx * (0.35 + sat)                     # яркие и насыщенные
    if score.size < 8 or float(mx.max()) < 40:
        return None
    k = max(8, score.size // 12)
    idx = np.argpartition(score, -k)[-k:]
    rr, gg, bb = r[idx], g[idx], b[idx]
    # оттенки ярких пикселей; два кластера: средний и самый «далёкий» от него
    hue = np.arctan2(np.sqrt(3) * (gg - bb), 2 * rr - gg - bb)
    h0 = math.atan2(float(np.mean(np.sin(hue))), float(np.mean(np.cos(hue))))
    d = np.abs(np.angle(np.exp(1j * (hue - h0))))
    near, far = idx[d < 0.6], idx[d >= 0.9]
    def mean(ix):
        if ix.size == 0:
            return None
        return (float(r[ix].mean()) / 255, float(g[ix].mean()) / 255, float(b[ix].mean()) / 255)
    main = mean(near if near.size else idx)
    return (main, mean(far) if far.size >= 3 else None)


class _MilkWorker(threading.Thread):
    """Фоновый поток: считает кадры MilkdropCore. GUI-поток отдаёт ему
    сэмплы и забирает готовые буферы — ни один кадр не блокирует интерфейс.
    В очереди максимум одна задача: если поток занят, кадр просто пропускается.
    Команды (смена пресета, режим, «штампы» слов) копятся отдельно и
    применяются перед следующим кадром — не теряются."""

    def __init__(self):
        super().__init__(daemon=True, name="milkdrop")
        self.core = MilkdropCore(288, 180)
        self._cond = threading.Condition()
        self._job = None
        self._cmds: list = []
        self._alive = True
        self.result = None          # dict с кадром и состоянием
        self._res_lock = threading.Lock()

    def command(self, *cmd):
        with self._cond:
            self._cmds.append(cmd)

    def submit(self, samples, rate, dt, gw, gh, ow=0, oh=0) -> bool:
        """ow, oh — размер окна в пикселях устройства: с C++-ядром кадр сразу растягивается до
        него в фоне (GUI копирует 1:1 вместо сглаженного растяжения на каждом кадре)."""
        with self._cond:
            if self._job is not None:
                return False
            self._job = (samples, rate, dt, gw, gh, ow, oh)
            self._cond.notify()
            return True

    def stop(self):
        with self._cond:
            self._alive = False
            self._cond.notify()

    def take(self):
        with self._res_lock:
            r, self.result = self.result, None
            return r

    def _apply(self, cmds):
        c = self.core
        for cmd in cmds:
            k = cmd[0]
            if k == "next":
                c.next_preset()
            elif k == "prev":
                c.prev_preset()
            elif k == "mode":
                c.set_mode(cmd[1])
            elif k == "goto":
                i = c.find_preset(cmd[1])
                if i >= 0:
                    c.next_preset(i)
            elif k == "stamp":
                c.stamp(*cmd[1:])

    def run(self):
        while True:
            with self._cond:
                while self._job is None and self._alive:
                    self._cond.wait()
                if not self._alive:
                    return
                samples, rate, dt, gw, gh, ow, oh = self._job
                cmds, self._cmds = self._cmds, []
            t0 = time.thread_time()          # чистое CPU-время потока (без ожидания GIL)
            try:
                c = self.core
                c.resize(gw, gh)
                self._apply(cmds)
                c.feed(samples, rate)
                c.step(dt)
                frame = c.frame_argb32()
                a = c.audio
                res = {"frame": frame, "name": c.preset_name(), "changed_at": c.preset_changed_at,
                       "t": c.t, "cost": (time.thread_time() - t0) * 1000.0,
                       "hue": c.current_hue(), "beat": max(0.0, a.bass_att - 1.0),
                       "idx": c.preset_idx, "count": c.preset_count(), "mode": c.mode,
                       "w": c.w, "h": c.h, "dom": _frame_colors(frame)}
                if c.native and ow > 8 and oh > 8:
                    import native_fx
                    img = QImage(int(ow), int(oh), QImage.Format.Format_RGB32)
                    native_fx.upscale_into(frame, img)
                    res["img"] = img
                with self._res_lock:
                    self.result = res
            except Exception as e:                   # noqa: BLE001
                print("[milkdrop] frame error:", e)
            finally:
                with self._cond:
                    self._job = None


class MilkdropWidget(QWidget):
    """Окно MilkDrop: картинка целиком из звука (см. milkdrop_core).
    Клик — следующий пресет, двойной клик — во всё окно. На паузе картинка
    продолжает медленно течь, но с пониженной частотой кадров.
    Режим «слова»: на треках с таймингами слова текста вылетают в случайных
    местах крупными шрифтами под настроение трека (milk_words)."""

    preset_changed = pyqtSignal(str)
    fullscreen_requested = pyqtSignal()

    FPS_PLAY = 16       # мс между кадрами при воспроизведении (60 fps; на слабом ПК — 30, см. _tick)
    FPS_IDLE = 66       # на паузе (~15 fps)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(240, 180)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Клик — следующий пресет · двойной клик — во всё окно")
        self._engine = None
        self._worker = _MilkWorker()
        self._worker.start()
        self._img: QImage | None = None
        self._arr = None                 # держит память, на которую смотрит QImage
        self._preset = self._worker.core.preset_name()
        self._preset_info = ""
        self._changed_at = -10.0
        self._core_t = 0.0
        self._beat = 0.0
        self._grid = (288, 180)
        self._native = bool(self._worker.core.native)
        self._grid_w = 560 if self._native else 288
        self._cost = 8.0
        self._mode = "auto"
        self._last = time.monotonic()
        self._click_timer = None
        # слова
        self._words = None
        self._lyrics_src = None          # объект с _lines/_has_timings (LyricsView)
        self._mood_fn = None
        self._mood_path = None
        self._timer = FrameTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self._timer.start(self.FPS_IDLE)

    # ── API ── #

    def set_engine(self, engine):
        self._engine = engine

    def next_preset(self):
        self._worker.command("next")

    def prev_preset(self):
        self._worker.command("prev")

    def goto_preset(self, name: str):
        if name:
            self._worker.command("goto", name)

    def preset_name(self) -> str:
        return self._preset

    def set_mode(self, mode: str):
        """auto — смена раз в 20–30 с · beat — на сильных ударах · lock — не меняется."""
        self._mode = mode
        self._worker.command("mode", mode)
        self._changed_at = self._core_t - 0.01         # показать подпись режима
        self.update()

    def mode(self) -> str:
        return self._mode

    def set_lyrics_source(self, src, mood_fn=None):
        """src — LyricsView (его _lines/_has_timings), mood_fn() → профиль трека."""
        self._lyrics_src = src
        self._mood_fn = mood_fn

    def set_words(self, on: bool):
        if on and self._words is None:
            from milk_words import LyricWords
            self._words = LyricWords(stamp_cb=self._stamp_word, grid_fn=lambda: self._grid,
                                     color_fn=lambda: getattr(self, "_dom", None))
            self._words.set_config(getattr(self, "_words_cfg", None))
        if self._words is not None:
            self._words.enabled = bool(on)
            if not on:
                self._words.words.clear()
        self.update()

    def set_words_config(self, cfg: dict | None):
        """Настройки слов (зоны, шрифты, анимация…) — см. milk_words.DEFAULT_CFG."""
        self._words_cfg = dict(cfg or {})
        if self._words is not None:
            self._words.set_config(self._words_cfg)

    def words_on(self) -> bool:
        return bool(self._words is not None and self._words.enabled)

    def words_state(self) -> str:
        """'' — выключено, 'ok' — работает, 'notimed' — у трека нет таймингов."""
        if not self.words_on():
            return ""
        return "ok" if self._words.timed else "notimed"

    def shutdown(self):
        """Остановить фоновый поток (вызывать при уходе с темы)."""
        self._worker.stop()                 # поток — чистый Python, остановится всегда
        if self._words is not None:
            self._words.shutdown()
        try:
            self._timer.stop()
        except RuntimeError:                # виджет уже удалён Qt
            pass

    # ── слова → «штамп» в буфер обратной связи ── #

    def _stamp_word(self, mask, x, y, rgb):
        gw, gh = self._grid
        mh, mw = mask.shape
        self._worker.command("stamp", mask, (x * gw - mw / 2) / gw, (y * gh - mh / 2) / gh, rgb)

    # ── цикл ── #

    def _playing(self) -> bool:
        try:
            return bool(self._engine is not None and self._engine.is_playing())
        except Exception:                            # noqa: BLE001
            return False

    def _pos_ms(self) -> int:
        """Позиция в ОРИГИНАЛЬНОМ треке, мс — сглаженная.
        VLC отдаёт время рывками (шаг до ~250 мс), поэтому держим свои часы:
        якорь + монотонное время, а показания VLC лишь мягко подтягивают их.
        С пресетами Slowed/Speed Up позиция пересчитывается в оригинал."""
        try:
            raw = float(self._engine.get_position() or 0.0) * 1000.0
            factor = float(getattr(self._engine, "preset_factor", 1.0) or 1.0)
        except Exception:                            # noqa: BLE001
            return 0
        now = time.monotonic()
        clk = getattr(self, "_clk", None)
        if clk is None or clk[2] != factor or raw == 0.0:
            self._clk = [raw, now, factor, raw]
            return int(raw * factor)
        anchor, t0, _, last_raw = clk
        pred = anchor + (now - t0) * 1000.0
        if abs(raw - pred) > 350:                    # перемотка / смена трека
            self._clk = [raw, now, factor, raw]
            return int(raw * factor)
        if raw != last_raw:
            # новое показание VLC: подтягиваем часы на 15% расхождения
            clk[0] = pred + (raw - pred) * 0.15
            clk[1] = now
            clk[3] = raw
            pred = clk[0]
        return int(pred * factor)

    def _update_words(self, playing: bool):
        w = self._words
        if w is None:
            return
        src = self._lyrics_src
        if src is not None:
            w.set_lines(getattr(src, "_lines", None) or [], bool(getattr(src, "_has_timings", False)))
        if self._mood_fn is not None:
            try:
                path = getattr(self._engine, "current_path", None)
                if path != self._mood_path:
                    prof = self._mood_fn()
                    if prof is not None or path is None:
                        self._mood_path = path
                    w.set_mood(prof)
            except Exception:                        # noqa: BLE001
                pass
        now = time.monotonic()
        if playing:
            w.update(self._pos_ms(), self.width(), self.height(), now, self._beat)
        else:
            self._clk = None
            w.update(-10 ** 9, 0, 0, now, 0.0)   # только чистка устаревших

    def _tick(self):
        win = self.window()
        if not self.isVisible() or (win is not None and win.isMinimized()):
            return
        # 1) забрать готовый кадр
        res = self._worker.take()
        if res is not None:
            if res.get("img") is not None:
                self._img, self._arr = res["img"], None
            else:
                arr = res["frame"]
                h, w = arr.shape
                self._arr = arr.view(np.uint8)
                self._img = QImage(self._arr.data, w, h, 4 * w, QImage.Format.Format_RGB32)
            if res["name"] != self._preset:
                self._preset = res["name"]
                self.preset_changed.emit(res["name"])
            self._preset_info = f"{res['idx'] + 1}/{res['count']}"
            self._changed_at, self._core_t = res["changed_at"], res["t"]
            self._beat = self._beat * 0.6 + res["beat"] * 0.4
            self._grid = (res["w"], res["h"])
            if res.get("dom"):
                # сглаживаем, чтобы цвет слов не скакал от кадра к кадру
                main, alt = res["dom"]
                pm = getattr(self, "_dom", None)
                if pm is None:
                    self._dom = [main, alt or main]
                else:
                    k = 0.15
                    self._dom[0] = tuple(a * (1 - k) + b * k for a, b in zip(pm[0], main))
                    if alt:
                        self._dom[1] = tuple(a * (1 - k) + b * k for a, b in zip(pm[1], alt))
            # адаптивное разрешение: кадр должен укладываться в бюджет 60 fps (~11 мс фонового времени)
            self._cost = self._cost * 0.85 + res["cost"] * 0.15
            lo, hi = self._grid_range()
            if self._cost > 11.5 and self._grid_w > lo:
                self._grid_w -= 32 if self._native else 16
            elif self._cost < 6.0 and self._grid_w < hi:
                self._grid_w += 16 if self._native else 8
            self.update()

        # 2) отдать следующую задачу
        playing = self._playing()
        active = playing or (self._words and self._words.words)
        # не тянем 60 даже на минимальной сетке — честные 30, чем рваные 45
        play_ms = self.FPS_PLAY if (self._cost < 13.0 or self._grid_w > self._grid_range()[0]) else 33
        want = play_ms if active else self.FPS_IDLE
        if self._timer.interval() != want:
            self._timer.setInterval(want)
        self._update_words(playing)
        now = time.monotonic()
        dt = animation_delta(now - self._last)
        samples, rate = None, 44100
        if playing:
            try:
                samples = self._engine.get_visual_samples(1024)
                rate = int(getattr(self._engine, "visual_sample_rate", 44100) or 44100)
            except Exception:                        # noqa: BLE001
                samples = None
            if samples is not None:
                samples = np.array(samples, dtype=np.float32, copy=True)   # поток получает свою копию
        W, H = max(1, self.width()), max(1, self.height())
        gw = int(min(self._grid_w, self._grid_range()[1]))
        gh = max(24, int(gw * H / W))
        dpr = self.devicePixelRatioF()
        if self._worker.submit(samples, rate, dt, gw, gh, int(W * dpr), int(H * dpr)):
            self._last = now

    def _grid_range(self):
        """Ширина сетки обратной связи: numpy — 180…400, C++ — до размера окна (не больше 1280)."""
        if not self._native:
            return 180, 400
        return 240, int(max(320, min(1280, self.width() * self.devicePixelRatioF())))

    # ── ввод / рисование ── #

    def set_overlay(self, w: QWidget | None):
        """Дочерний слой поверх картинки (режим текста) — растягивается на всё окно."""
        self._overlay = w
        if w is not None:
            w.setGeometry(self.rect())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        ov = getattr(self, "_overlay", None)
        if ov is not None:
            ov.setGeometry(self.rect())

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            # одиночный клик ждёт, не окажется ли он двойным
            if self._click_timer is None:
                self._click_timer = QTimer(self)
                self._click_timer.setSingleShot(True)
                self._click_timer.timeout.connect(self.next_preset)
            self._click_timer.start(230)
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            if self._click_timer is not None:
                self._click_timer.stop()
            self.fullscreen_requested.emit()
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    def hideEvent(self, e):
        super().hideEvent(e)
        self._timer.stop()

    def showEvent(self, e):
        super().showEvent(e)
        self._last = time.monotonic()
        self._timer.start()

    _MODE_RU = {"auto": "АВТО", "beat": "ПО БИТАМ", "lock": "ЗАКРЕПЛЁН"}

    def paintEvent(self, _):
        p = QPainter(self)
        r = self.rect()
        if self._img is None:
            p.fillRect(r, QColor(0, 0, 0))
        else:
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawImage(r, self._img)
        if self._words is not None and self._words.words:
            self._words.draw(p, r.width(), r.height(), time.monotonic(), self._beat)
        if self.words_state() == "notimed":
            f = QFont("Arial"); f.setPixelSize(11); f.setBold(True)
            p.setFont(f)
            p.setPen(QColor(255, 255, 255, 110))
            p.drawText(QRect(10, r.height() - 22, r.width() - 20, 16),
                       Qt.AlignmentFlag.AlignRight, "СЛОВА: у этого трека нет текста с таймингами")
        # название пресета — первые 4 с после смены, как в MilkDrop
        age = self._core_t - self._changed_at
        if 0.0 <= age < 4.0:
            a = int(255 * min(1.0, (4.0 - age) / 1.0))
            f = QFont("Arial")
            f.setPixelSize(13)
            f.setBold(True)
            p.setFont(f)
            text = f"{self._preset}   [{self._preset_info}]   {self._MODE_RU.get(self._mode, '')}"
            p.setPen(QColor(0, 0, 0, a))
            p.drawText(QRect(11, 9, r.width() - 20, 20), Qt.AlignmentFlag.AlignLeft, text)
            p.setPen(QColor(255, 255, 255, a))
            p.drawText(QRect(10, 8, r.width() - 20, 20), Qt.AlignmentFlag.AlignLeft, text)
        p.end()


class MilkToolbar(QWidget):
    """Кнопки окна MilkDrop: пресеты, режимы смены, слова, во всё окно."""

    mode_changed = pyqtSignal(str)
    words_toggled = pyqtSignal(bool)
    fullscreen_toggled = pyqtSignal(bool)
    settings_requested = pyqtSignal()

    def __init__(self, milk: "MilkdropWidget", parent=None):
        super().__init__(parent)
        from PyQt6.QtWidgets import QHBoxLayout, QPushButton, QButtonGroup
        self._milk = milk
        self.setObjectName("MilkToolbar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(3)

        def btn(text, tip, checkable=False, w=None):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setCheckable(checkable)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            if w:
                b.setFixedWidth(w)
            lay.addWidget(b)
            return b

        self.b_prev = btn("<<", "Предыдущий пресет", w=34)
        self.b_next = btn(">>", "Следующий пресет", w=34)
        self.b_prev.clicked.connect(milk.prev_preset)
        self.b_next.clicked.connect(milk.next_preset)
        lay.addSpacing(8)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.b_auto = btn("АВТО", "Пресет меняется сам каждые 20–30 секунд", True)
        self.b_beat = btn("ПО БИТАМ", "Пресет меняется на сильных ударах (не чаще раза в 9 с)", True)
        self.b_lock = btn("ЗАКРЕП", "Пресет не меняется — только кнопками << >> или кликом", True)
        for b, m in ((self.b_auto, "auto"), (self.b_beat, "beat"), (self.b_lock, "lock")):
            self.group.addButton(b)
            b.clicked.connect(lambda _=False, m=m: self._set_mode(m))
        lay.addSpacing(8)
        self.b_words = btn("СЛОВА", "Слова текста вылетают в MilkDrop (нужен текст с таймингами)", True)
        self.b_words.toggled.connect(self._on_words)
        self.b_wset = btn("НАСТРОЙКИ СЛОВ", "Где появляются слова, шрифты, анимация, цвет, размер")
        self.b_wset.clicked.connect(self.settings_requested.emit)
        lay.addStretch(1)
        self.b_full = btn("ВО ВСЁ ОКНО", "MilkDrop на всё окно плеера (Esc или двойной клик — назад)", True)
        self.b_full.toggled.connect(self.fullscreen_toggled.emit)
        self.set_mode(milk.mode())

    def _set_mode(self, m):
        self._milk.set_mode(m)
        self.mode_changed.emit(m)

    def set_mode(self, m):
        {"auto": self.b_auto, "beat": self.b_beat, "lock": self.b_lock}.get(m, self.b_auto).setChecked(True)
        self._milk.set_mode(m)

    def _on_words(self, on):
        self._milk.set_words(on)
        self.words_toggled.emit(on)

    def set_words(self, on):
        self.b_words.setChecked(bool(on))

    def set_fullscreen(self, on):
        self.b_full.blockSignals(True)
        self.b_full.setChecked(bool(on))
        self.b_full.blockSignals(False)


# ══════════════════════════════════════════════════════════════════════════ #
#  Текст песни в стиле ЖК Winamp — поверх MilkDrop
# ══════════════════════════════════════════════════════════════════════════ #

class WinampLyrics(QWidget):
    """Режим текста темы Winamp: MilkDrop продолжает играть на фоне
    (притемнённый), а поверх — зелёный ЖК-«экран» с построчной лирикой:
    текущая строка крупная и светится, соседние — тусклее, всё плавно
    прокручивается за музыкой. Без синхронизации — прокрутка колесом.
    Источник строк — LyricsView (он уже парсит LRC/обычный текст)."""

    def __init__(self, lyrics_view, engine, title_fn=None, parent=None):
        super().__init__(parent)
        self._lv = lyrics_view
        self._engine = engine
        self._title_fn = title_fn
        self._lines = []
        self._ms = []
        self._timed = False
        self._src = None
        self._idx = -1
        self._scroll = 0.0
        self._manual = 0.0
        self._alpha = 0.0            # 0..1 — плавное появление
        self._target_alpha = 0.0
        self._scan: QPixmap | None = None
        self._last = time.monotonic()
        self.hide()
        self._timer = FrameTimer(self)
        self._timer.timeout.connect(self._tick)

    # ── API ── #

    def set_open(self, on: bool):
        self._target_alpha = 1.0 if on else 0.0
        if on:
            self._sync_lines(force=True)
            self.show()
            self.raise_()
        self._last = time.monotonic()
        self._timer.start(33)

    # ── данные ── #

    def _sync_lines(self, force=False):
        lines = getattr(self._lv, "_lines", None) or []
        if force or lines is not self._src:
            self._src = lines
            self._lines = [ln.get("text", "") for ln in lines]
            self._ms = [ln.get("ms", -1) for ln in lines]
            self._timed = bool(getattr(self._lv, "_has_timings", False))
            self._idx = -1
            self._manual = 0.0
            self._scroll = 0.0

    def _pos_ms(self) -> int:
        try:
            if self._engine is not None and getattr(self._engine, "current_path", None):
                f = float(getattr(self._engine, "preset_factor", 1.0) or 1.0)
                return int(float(self._engine.get_position() or 0.0) * 1000 * f)
        except Exception:                            # noqa: BLE001
            pass
        return 0

    def _tick(self):
        now = time.monotonic()
        dt = animation_delta(now - self._last)
        self._last = now
        self._alpha += (self._target_alpha - self._alpha) * min(1.0, dt * 7.0)
        if self._target_alpha == 0.0 and self._alpha < 0.02:
            self._alpha = 0.0
            self._timer.stop()
            self.hide()
            return
        self._sync_lines()
        if self._timed and self._lines:
            pos = self._pos_ms()
            idx = -1
            for i, ms in enumerate(self._ms):
                if 0 <= ms <= pos:
                    idx = i
            self._idx = idx
            target = max(0, idx)
        else:
            target = self._manual
        # мягкая «пружина» прокрутки
        self._scroll += (target - self._scroll) * min(1.0, dt * 6.0)
        self.update()

    # ── ввод ── #

    def wheelEvent(self, e):
        if not self._timed and self._lines:
            step = -e.angleDelta().y() / 120.0 * 2.0
            self._manual = max(0.0, min(len(self._lines) - 1, self._manual + step))
        e.accept()

    def mousePressEvent(self, e):
        e.accept()                                   # клики не листают пресеты MilkDrop

    # ── рисование ── #

    def _scanlines(self, w: int, h: int) -> QPixmap:
        if self._scan is None or self._scan.width() != w or self._scan.height() != h:
            pm = QPixmap(max(1, w), max(1, h))
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            p.setPen(QColor(0, 0, 0, 70))
            for y in range(0, h, 3):
                p.drawLine(0, y, w, y)
            p.end()
            self._scan = pm
        return self._scan

    def paintEvent(self, _):
        if self._alpha <= 0.0:
            return
        p = QPainter(self)
        p.setOpacity(self._alpha)
        W, H = self.width(), self.height()
        # притемняем MilkDrop, но оставляем его «дышать» за текстом
        p.fillRect(self.rect(), QColor(0, 0, 0, 150))

        m = max(18, int(min(W, H) * 0.05))
        box = QRect(m, m, W - 2 * m, H - 2 * m)
        p.fillRect(box, QColor(0, 0, 0, 185))
        p.setPen(QColor("#0c0c12")); p.drawLine(box.topLeft(), box.topRight()); p.drawLine(box.topLeft(), box.bottomLeft())
        p.setPen(QColor("#5e5e7a")); p.drawLine(box.bottomLeft(), box.bottomRight()); p.drawLine(box.topRight(), box.bottomRight())

        # шапка ЖК
        hf = QFont("Arial"); hf.setPixelSize(11); hf.setBold(True)
        hf.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.5)
        p.setFont(hf)
        head = QRect(box.left() + 12, box.top() + 8, box.width() - 24, 18)
        p.setPen(QColor(LCD_GREEN))
        p.drawText(head, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "ECHOES LYRICS")
        if self._title_fn is not None:
            try:
                artist, title = self._title_fn()
            except Exception:                        # noqa: BLE001
                artist, title = "", ""
            info = f"{artist}  —  {title}".upper() if title else ""
            p.setPen(QColor("#1b9a1b"))
            p.drawText(head, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       p.fontMetrics().elidedText(info, Qt.TextElideMode.ElideRight, int(head.width() * 0.62)))
        p.fillRect(QRect(box.left() + 12, head.bottom() + 5, box.width() - 24, 1), QColor(0, 120, 0, 160))

        area = QRect(box.left() + 16, head.bottom() + 12, box.width() - 32, box.bottom() - head.bottom() - 24)
        p.save()
        p.setClipRect(area)
        if not self._lines:
            f = QFont("Arial"); f.setPixelSize(16); f.setBold(True)
            p.setFont(f); p.setPen(QColor("#1b9a1b"))
            p.drawText(area, Qt.AlignmentFlag.AlignCenter, "ТЕКСТ НЕ НАЙДЕН")
        else:
            line_h = max(30, int(area.height() / 11))
            cy = area.center().y()
            first = max(0, int(self._scroll) - 8)
            last = min(len(self._lines), int(self._scroll) + 9)
            big = QFont("Arial"); big.setBold(True)
            small = QFont("Arial"); small.setBold(True)
            for i in range(first, last):
                y = cy + (i - self._scroll) * line_h
                text = self._lines[i] or "·"
                dist = abs(i - self._scroll)
                current = (i == self._idx) if self._timed else dist < 0.5
                r = QRect(area.left(), int(y - line_h / 2), area.width(), line_h)
                if current:
                    px = max(16, min(30, line_h - 6))
                    big.setPixelSize(px)
                    p.setFont(big)
                    fm = p.fontMetrics()
                    while fm.horizontalAdvance(text) > area.width() and px > 14:
                        px -= 1; big.setPixelSize(px); p.setFont(big); fm = p.fontMetrics()
                    # свечение ЖК: несколько полупрозрачных копий вокруг строки
                    glow = QColor(0, 255, 0, 55)
                    p.setPen(glow)
                    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-2, 0), (2, 0)):
                        p.drawText(r.translated(dx, dy), Qt.AlignmentFlag.AlignCenter, text)
                    p.setPen(QColor("#b8ffb8"))
                    p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
                else:
                    small.setPixelSize(max(12, min(18, line_h - 14)))
                    p.setFont(small)
                    fade = max(0.18, 1.0 - dist * 0.16)
                    g = int(200 * fade)
                    p.setPen(QColor(0, g, 0))
                    t = p.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, area.width())
                    p.drawText(r, Qt.AlignmentFlag.AlignCenter, t)
        p.restore()
        p.drawPixmap(box.topLeft(), self._scanlines(box.width(), box.height()))
        if not self._timed and self._lines:
            p.setFont(hf); p.setPen(QColor("#1b9a1b"))
            p.drawText(QRect(box.left() + 12, box.bottom() - 22, box.width() - 24, 16),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "КОЛЕСО — ПРОКРУТКА")
        p.end()


# ═════════════════════════════════════════════════════════════════════════ #
#  WINAMP PLAYLIST — нормальный плейлист: поиск, нумерованный список, время #
# ═════════════════════════════════════════════════════════════════════════ #

from PyQt6.QtCore import QSize, QEvent
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QStyledItemDelegate, QListWidget, QListWidgetItem, QLineEdit,
                             QComboBox, QMenu, QAbstractItemView, QStackedWidget, QSlider,
                             QHBoxLayout, QPushButton, QLabel, QApplication)

_ROLE_TRACK = Qt.ItemDataRole.UserRole
_ROLE_INDEX = Qt.ItemDataRole.UserRole + 1
_ROLE_DUR = Qt.ItemDataRole.UserRole + 2

_WA_GREEN = QColor("#00e000")
_WA_WHITE = QColor("#ffffff")
_WA_SEL = QColor("#0000c6")


def _fmt_dur(sec) -> str:
    try:
        s = int(round(float(sec or 0)))
    except (TypeError, ValueError):
        return ""
    if s <= 0:
        return ""
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


class _WaRowDelegate(QStyledItemDelegate):
    """Строка как в Winamp: «12. Артист - Название» слева, длительность справа;
    играющий трек — белым, выделение — синей плашкой."""

    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.current = -1

    def paint(self, p, option, index):
        from PyQt6.QtWidgets import QStyle
        r = option.rect
        p.save()
        if option.state & QStyle.StateFlag.State_Selected:
            p.fillRect(r, _WA_SEL)
        cur = index.data(_ROLE_INDEX) == self.current
        p.setPen(_WA_WHITE if cur else _WA_GREEN)
        f = QFont(option.font)
        f.setBold(cur)
        p.setFont(f)
        dur = index.data(_ROLE_DUR) or ""
        fm = p.fontMetrics()
        dw = fm.horizontalAdvance(dur) + 8 if dur else 0
        text = fm.elidedText(index.data(Qt.ItemDataRole.DisplayRole) or "", Qt.TextElideMode.ElideRight,
                             r.width() - dw - 8)
        p.drawText(r.adjusted(4, 0, -dw, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        if dur:
            p.drawText(r.adjusted(0, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, dur)
        p.restore()

    def sizeHint(self, option, index):
        s = super().sizeHint(option, index)
        return QSize(s.width(), max(18, option.fontMetrics.height() + 4))


class WinampPlaylist(QWidget):
    """Окно WINAMP PLAYLIST: источник (библиотека / плейлист), поиск по словам,
    весь список треков с номерами и временем. Двойной клик / Enter — играть."""

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        self.setObjectName("WaPlaylist")
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)

        top = QHBoxLayout(); top.setSpacing(4)
        self.source = QComboBox()
        self.source.setToolTip("Что показывать: вся библиотека или плейлист")
        self.source.currentIndexChanged.connect(lambda _i: self.refresh())
        top.addWidget(self.source, 2)
        v.addLayout(top)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск: название, исполнитель, альбом…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self._search_timer.start())
        self.search.returnPressed.connect(self._play_selected_or_first)
        self.search.installEventFilter(self)
        v.addWidget(self.search)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(120)
        self._search_timer.timeout.connect(self._fill)

        self.list = QListWidget()
        self.list.setObjectName("WaPlList")
        self.list.setUniformItemSizes(True)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setStyleSheet("QListWidget#WaPlList{background:#000;border:1px solid #2a2a36;outline:none;}")
        self._delegate = _WaRowDelegate(self.list)
        self.list.setItemDelegate(self._delegate)
        self.list.itemDoubleClicked.connect(self._play_item)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        v.addWidget(self.list, 1)

        bot = QHBoxLayout(); bot.setSpacing(3)
        b_add = QPushButton("+ ДОБАВИТЬ")
        b_add.setToolTip("Файлы, папка, скачать по ссылке")
        b_add.clicked.connect(lambda: self._add_menu(b_add))
        bot.addWidget(b_add)
        b_pl = QPushButton("ПЛЕЙЛИСТ")
        b_pl.setToolTip("Новый плейлист, добавить играющий трек, переименовать, удалить")
        b_pl.clicked.connect(lambda: self._pl_menu(b_pl))
        bot.addWidget(b_pl)
        bot.addStretch(1)
        self.info = QLabel("")
        self.info.setStyleSheet("color:#00e000;font-size:10px;font-weight:bold;background:transparent;")
        bot.addWidget(self.info)
        v.addLayout(bot)
        self._tracks = []
        self._ctx = "library"
        self.refresh_sources()

    # ── данные ── #

    def refresh_sources(self):
        cur = self.source.currentData() if self.source.count() else None
        want = cur or (self.win.queue_context if self.win.queue_context in self.win.playlists else "library")
        self.source.blockSignals(True)
        self.source.clear()
        self.source.addItem(f"Библиотека ({len(self.win.library)})", "library")
        for name, pl in self.win.playlists.items():
            self.source.addItem(f"{name} ({len(pl.get('tracks', []))})", name)
        i = self.source.findData(want)
        self.source.setCurrentIndex(i if i >= 0 else 0)
        self.source.blockSignals(False)
        self.refresh()

    def refresh(self):
        ctx = self.source.currentData() or "library"
        self._ctx = ctx
        self._tracks = self.win.library if ctx == "library" else self.win.playlists.get(ctx, {}).get("tracks", [])
        self._fill()

    def _fill(self):
        from search_util import tokens, matches
        toks = tokens(self.search.text())
        sel = {id(it.data(_ROLE_TRACK)) for it in self.list.selectedItems()}
        self.list.setUpdatesEnabled(False)
        self.list.clear()
        total = 0.0
        shown = 0
        for i, t in enumerate(self._tracks):
            if toks and not matches(toks, t.get("title"), t.get("artist"), t.get("album")):
                continue
            artist = (t.get("artist") or "").strip()
            title = (t.get("title") or "").strip() or "—"
            it = QListWidgetItem(f"{i + 1}. {artist + ' - ' if artist else ''}{title}")
            it.setData(_ROLE_TRACK, t)
            it.setData(_ROLE_INDEX, i)
            it.setData(_ROLE_DUR, _fmt_dur(t.get("duration")))
            self.list.addItem(it)
            if id(t) in sel:
                it.setSelected(True)
            total += float(t.get("duration") or 0)
            shown += 1
        self.list.setUpdatesEnabled(True)
        self.mark_current()
        n_all = len(self._tracks)
        cnt = f"{shown}/{n_all}" if toks else f"{n_all}"
        self.info.setText(f"{cnt} · {_fmt_dur(total) or '0:00'}")
        if toks and shown and not self.list.selectedItems():
            self.list.setCurrentRow(0)

    def mark_current(self):
        """Подсветить играющий трек (если он из показанного источника)."""
        w = self.win
        playing_here = (w.queue_context == self._ctx) and not getattr(w, "_queued_now", False)
        self._delegate.current = w.current_index if playing_here else -1
        if not playing_here and getattr(w, "_queued_now", False) and self._ctx == "library":
            self._delegate.current = w.current_index
        self.list.viewport().update()

    def scroll_to_current(self):
        cur = self._delegate.current
        for r in range(self.list.count()):
            if self.list.item(r).data(_ROLE_INDEX) == cur:
                self.list.scrollToItem(self.list.item(r), QAbstractItemView.ScrollHint.PositionAtCenter)
                break

    # ── действия ── #

    def _play_item(self, it):
        idx = it.data(_ROLE_INDEX)
        if idx is not None:
            self.win._play_index(int(idx), self._ctx)
            self.mark_current()

    def _play_selected_or_first(self):
        it = self.list.currentItem() or (self.list.item(0) if self.list.count() else None)
        if it is not None:
            self._play_item(it)

    def eventFilter(self, obj, ev):
        # стрелки в поле поиска двигают выделение в списке
        if obj is self.search and ev.type() == QEvent.Type.KeyPress and \
                ev.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up, Qt.Key.Key_PageDown, Qt.Key.Key_PageUp):
            self.list.setFocus()
            QApplication.sendEvent(self.list, ev)
            self.search.setFocus()
            return True
        return super().eventFilter(obj, ev)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._play_selected_or_first()
            return
        if e.key() == Qt.Key.Key_Delete and self.list.selectedItems():
            self._remove_selected()
            return
        super().keyPressEvent(e)

    def _selected_tracks(self):
        return [it.data(_ROLE_TRACK) for it in self.list.selectedItems() if it.data(_ROLE_TRACK)]

    def _remove_selected(self):
        tracks = self._selected_tracks()
        if not tracks:
            return
        if self._ctx == "library":
            self.win.remove_tracks_everywhere(tracks)
        else:
            rows = sorted(int(it.data(_ROLE_INDEX)) for it in self.list.selectedItems())
            self.win.playlist_remove_rows(self._ctx, rows)
        self.refresh_sources()

    def _menu(self, pos):
        it = self.list.itemAt(pos)
        if it is not None and not it.isSelected():
            self.list.clearSelection()
            it.setSelected(True)
        tracks = self._selected_tracks()
        if not tracks:
            return
        w = self.win
        n = len(tracks)
        sfx = f" ({n})" if n > 1 else ""
        m = QMenu(self)
        a_play = m.addAction("Играть")
        a_next = m.addAction("Играть следующим" + sfx)
        a_queue = m.addAction("＋  Добавить в очередь" + sfx)
        sub = m.addMenu("＋  Добавить в плейлист" + sfx)
        for name in w.playlists:
            if name != self._ctx:
                sub.addAction(name, lambda nm=name: (w.playlist_add_tracks(nm, tracks), self.refresh_sources()))
        sub.addSeparator()
        sub.addAction("Новый плейлист…", lambda: (w._new_playlist_with(tracks), self.refresh_sources()))
        a_tags = m.addAction("Теги, обложка и текст…")
        w.reveal_action(m, tracks[0])
        m.addSeparator()
        a_rm = m.addAction(("Убрать из этого плейлиста" if self._ctx != "library" else "Удалить из плеера…") + sfx)
        ch = m.exec(self.list.mapToGlobal(pos))
        if ch == a_play:
            self._play_item(self.list.selectedItems()[0])
        elif ch == a_next:
            w.queue_add(tracks, True)
        elif ch == a_queue:
            w.queue_add(tracks)
        elif ch == a_tags:
            w.edit_track_tags(tracks[0])
            self.refresh()
        elif ch == a_rm:
            self._remove_selected()

    def _add_menu(self, btn):
        w = self.win
        m = QMenu(self)
        m.addAction("Файлы…", lambda: (w._add_files(), self.refresh_sources()))
        m.addAction("Папка…", lambda: (w._add_folder(), self.refresh_sources()))
        m.addSeparator()
        m.addAction("Скачать трек/альбом/плейлист по ссылке", w._open_downloader)
        m.addAction("Музыка: поиск и скачивание", w._open_hub)
        m.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _pl_menu(self, btn):
        w = self.win
        m = QMenu(self)
        m.addAction("Новый плейлист…", lambda: (w._new_playlist(), self.refresh_sources()))
        cur = w._current_track()
        if cur and w.playlists:
            sub = m.addMenu("＋  Добавить играющий трек в…")
            for name in w.playlists:
                sub.addAction(name, lambda nm=name: (w.playlist_add_tracks(nm, [cur]), self.refresh_sources()))
        if self._ctx != "library":
            m.addSeparator()
            m.addAction("Переименовать…", lambda: self._rename_playlist())
            m.addAction("Удалить плейлист", lambda: self._delete_playlist())
        m.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _rename_playlist(self):
        from PyQt6.QtWidgets import QInputDialog
        w, old = self.win, self._ctx
        name, ok = QInputDialog.getText(self, "Переименовать плейлист", "Название:", text=old)
        name = (name or "").strip()
        if ok and name and name != old and name not in w.playlists:
            w.playlists = {(name if k == old else k): v for k, v in w.playlists.items()}
            if w.queue_context == old:
                w.queue_context = name
            self._persist()
            self.refresh_sources()
            i = self.source.findData(name)
            if i >= 0:
                self.source.setCurrentIndex(i)

    def _delete_playlist(self):
        from PyQt6.QtWidgets import QMessageBox
        w, name = self.win, self._ctx
        if QMessageBox.question(self, "Удалить плейлист",
                                f"Удалить плейлист «{name}»? Треки в библиотеке не удаляются.") \
                != QMessageBox.StandardButton.Yes:
            return
        w.playlists.pop(name, None)
        if w.queue_context == name:
            w.queue_context = "library"
        self._persist()
        self.source.setCurrentIndex(0)
        self.refresh_sources()

    def _persist(self):
        self.win._save_playlists()
        try:
            self.win._refresh_playlists()
        except Exception:                            # noqa: BLE001
            pass


# ═════════════════════════════════════════════════════════════════════════ #
#  WINAMP EQUALIZER — настоящие ползунки; полные настройки — по кнопке       #
# ═════════════════════════════════════════════════════════════════════════ #

class WinampEqualizer(QWidget):
    """10 вертикальных ползунков (зеркало ползунков эквалайзера плеера),
    AUTO (пресет по жанру), ПРЕСЕТЫ, СБРОС и НАСТРОЙКИ — полная панель."""

    LABELS = ["31", "62", "125", "250", "500", "1K", "2K", "4K", "8K", "16K"]   # реальные полосы VLC

    def __init__(self, win, settings_panel, parent=None):
        super().__init__(parent)
        self.win = win
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        row = QHBoxLayout(); row.setSpacing(3)
        self.b_auto = QPushButton("AUTO")
        self.b_auto.setCheckable(True)
        self.b_auto.setToolTip("Пресет по жанру трека")
        self.b_auto.setChecked(win.eq_auto_cb.isChecked())
        self.b_auto.toggled.connect(win.eq_auto_cb.setChecked)
        win.eq_auto_cb.toggled.connect(self._sync_auto)
        row.addWidget(self.b_auto)
        b_pre = QPushButton("ПРЕСЕТЫ")
        b_pre.clicked.connect(lambda: self._presets(b_pre))
        row.addWidget(b_pre)
        b_reset = QPushButton("СБРОС")
        b_reset.clicked.connect(win._reset_eq)
        row.addWidget(b_reset)
        row.addStretch(1)
        self.b_set = QPushButton("НАСТРОЙКИ")
        self.b_set.setCheckable(True)
        self.b_set.setToolTip("Все настройки плеера: кроссфейд, gapless, громкость, язык, атмосфера…")
        self.b_set.toggled.connect(self._toggle_settings)
        row.addWidget(self.b_set)
        v.addLayout(row)

        self.stack = QStackedWidget()
        eq = QWidget()
        h = QHBoxLayout(eq); h.setContentsMargins(4, 2, 4, 0); h.setSpacing(2)
        self.sliders = []
        for i, lbl in enumerate(self.LABELS):
            col = QVBoxLayout(); col.setSpacing(1)
            sl = QSlider(Qt.Orientation.Vertical)
            sl.setRange(-20, 20)
            sl.setValue(win._eq_sliders[i].value())
            sl.setFixedHeight(104)
            sl.setToolTip(f"{lbl} Hz")
            sl.valueChanged.connect(lambda val, k=i: self._to_player(k, val))
            win._eq_sliders[i].valueChanged.connect(lambda val, s=sl: self._from_player(s, val))
            t = QLabel(lbl)
            t.setAlignment(Qt.AlignmentFlag.AlignCenter)
            t.setStyleSheet("color:#00e000;font-size:9px;font-weight:bold;background:transparent;")
            col.addWidget(sl, 1, Qt.AlignmentFlag.AlignHCenter)
            col.addWidget(t)
            h.addLayout(col)
            self.sliders.append(sl)
        eq.setFixedHeight(128)                       # компактно, как в Winamp: место — плейлисту
        self._eq_page = eq
        self.stack.addWidget(eq)
        self.stack.addWidget(settings_panel)
        self.stack.setCurrentIndex(0)
        self.stack.setFixedHeight(eq.height())
        v.addWidget(self.stack, 1)

    def _to_player(self, k, val):
        s = self.win._eq_sliders[k]
        if s.value() != val:
            s.setValue(val)                          # дальше — обычная логика плеера (движок, «Вручную»)

    @staticmethod
    def _from_player(sl, val):
        try:
            if sl.value() != val:
                sl.blockSignals(True)
                sl.setValue(val)
                sl.blockSignals(False)
        except RuntimeError:
            pass

    def _sync_auto(self, on):
        try:
            self.b_auto.blockSignals(True)
            self.b_auto.setChecked(bool(on))
            self.b_auto.blockSignals(False)
        except RuntimeError:
            pass

    def _presets(self, btn):
        cb = self.win.eq_preset_cb
        m = QMenu(self)
        for i in range(cb.count()):
            a = m.addAction(cb.itemText(i))
            a.setCheckable(True)
            a.setChecked(i == cb.currentIndex())
            a.triggered.connect(lambda _=False, k=i: cb.setCurrentIndex(k))
        m.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _toggle_settings(self, on):
        self.stack.setCurrentIndex(1 if on else 0)
        self.stack.widget(1).setVisible(bool(on))
        # настройки длинные — даём окну место; ползунки — компактно
        self.stack.setFixedHeight(420 if on else self._eq_page.height())
        self.updateGeometry()
