# seekbar.py
"""
Собственная полоска воспроизведения: скруглённая дорожка, заливка с градиентом цветов темы,
«призрак» перемотки под курсором, светящийся ползунок, плавное утолщение при наведении и
всплывающее время над курсором. Клик — сразу перемотка в это место, можно тянуть.

  • FancySlider — замена QSlider (тот же API: value/setValue/sliderReleased/…), для обычных тем;
  • TimeBubble  — плавающая подсказка со временем (используется и в теме Я.Музыки).
"""
from __future__ import annotations

import re

from PyQt6.QtCore import Qt, QRectF, QPointF, QVariantAnimation, QEasingCurve, QPoint
from PyQt6.QtGui import QColor, QPainter, QLinearGradient, QRadialGradient
from PyQt6.QtWidgets import QSlider, QLabel, QApplication


class TimeBubble(QLabel):
    """Маленькая «капля» со временем над курсором (не забирает фокус и мышь)."""

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setStyleSheet("QLabel{background:#2b2b2b;color:#ffffff;border-radius:8px;"
                           "padding:4px 9px;font-size:12px;font-weight:700;}")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def show_at(self, global_pt: QPoint, text: str):
        self.setText(text)
        self.adjustSize()
        self.move(global_pt.x() - self.width() // 2, global_pt.y() - self.height() - 12)
        if not self.isVisible():
            self.show()

    def hide_now(self):
        self.hide()


_COLOR = re.compile(r"#[0-9a-fA-F]{6,8}|rgba?\([^)]*\)")


class FancySlider(QSlider):
    PAD = 8

    def __init__(self, orientation=Qt.Orientation.Horizontal, parent=None):
        super().__init__(orientation, parent)
        self.accent = QColor("#a78bfa")
        self.accent2 = QColor("#ff4fa3")
        self.time_provider = None                  # callable(доля 0..1) -> "1:23"
        self._hover_x = None
        self._grow = 0.0                           # 0..1 — «толщина» при наведении/перетаскивании
        self._bubble = None
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(lambda v: (setattr(self, "_grow", float(v)), self.update()))
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(22)
        self.setFixedHeight(24)

    # ── цвета ── #

    def set_colors(self, accent, accent2=None):
        self.accent = QColor(accent)
        self.accent2 = QColor(accent2) if accent2 is not None else QColor(accent)
        self.update()

    def setStyleSheet(self, ss: str):
        """Совместимость: раньше цвет заливки задавали QSS-строкой «sub-page{background:#xxxxxx}»."""
        m = _COLOR.search(ss or "")
        if m:
            c = QColor(m.group(0))
            if c.isValid():
                self.accent = self.accent2 = c
        self.update()

    # ── геометрия ── #

    def _x_to_value(self, x: float) -> int:
        w = max(1, self.width() - 2 * self.PAD)
        f = max(0.0, min(1.0, (x - self.PAD) / w))
        return int(round(self.minimum() + f * (self.maximum() - self.minimum())))

    def _frac(self) -> float:
        rng = max(1, self.maximum() - self.minimum())
        return (self.value() - self.minimum()) / rng

    def _animate(self, to: float):
        import motion
        self._anim.stop()
        if motion.strength() <= 0:
            self._grow = to
            self.update()
            return
        self._anim.setDuration(150)
        self._anim.setStartValue(self._grow)
        self._anim.setEndValue(to)
        self._anim.start()

    # ── мышь ── #

    def enterEvent(self, e):
        self._animate(1.0)

    def leaveEvent(self, e):
        self._hover_x = None
        if not self.isSliderDown():
            self._animate(0.0)
        if self._bubble is not None:
            self._bubble.hide_now()
        self.update()

    def _show_bubble(self, x):
        if self.time_provider is None:
            return
        if self._bubble is None:
            self._bubble = TimeBubble()
        f = max(0.0, min(1.0, (x - self.PAD) / max(1, self.width() - 2 * self.PAD)))
        try:
            txt = self.time_provider(f)
        except Exception:                        # noqa: BLE001
            return
        self._bubble.show_at(self.mapToGlobal(QPoint(int(x), 0)), txt)

    def mouseMoveEvent(self, e):
        x = e.position().x()
        self._hover_x = x
        if self.isSliderDown():
            v = self._x_to_value(x)
            self.setValue(v)
            self.sliderMoved.emit(v)
        self._show_bubble(x)
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.setSliderDown(True)                 # sliderPressed
            v = self._x_to_value(e.position().x())
            self.setValue(v)
            self.sliderMoved.emit(v)
            self._animate(1.0)
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.isSliderDown():
            self.setSliderDown(False)                # sliderReleased
            if not self.underMouse():
                self._animate(0.0)
            e.accept()
        else:
            super().mouseReleaseEvent(e)

    # ── рисование ── #

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        pad = self.PAD
        g = self._grow
        h = 5 + 4 * g                                  # толщина дорожки
        y = (H - h) / 2
        x0, x1 = pad, W - pad
        track = QRectF(x0, y, x1 - x0, h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(128, 128, 128, 70))
        p.drawRoundedRect(track, h / 2, h / 2)
        frac = self._frac()
        fx = x0 + (x1 - x0) * frac
        # «призрак» — куда перемотает клик
        if self._hover_x is not None and not self.isSliderDown():
            hx = max(x0, min(x1, self._hover_x))
            if hx > fx:
                c = QColor(self.accent)
                c.setAlpha(60)
                p.setBrush(c)
                p.drawRoundedRect(QRectF(x0, y, hx - x0, h), h / 2, h / 2)
        if frac > 0:
            grad = QLinearGradient(x0, 0, max(x0 + 1, fx), 0)
            grad.setColorAt(0.0, self.accent)
            grad.setColorAt(1.0, self.accent2 if self.accent2 != self.accent else self.accent.lighter(125))
            p.setBrush(grad)
            p.drawRoundedRect(QRectF(x0, y, max(h, fx - x0), h), h / 2, h / 2)
        # ползунок
        r = 7.0 * g
        if r > 0.5:
            glow = QRadialGradient(QPointF(fx, H / 2), r * 2.4)
            gc = QColor(self.accent)
            gc.setAlpha(int(110 * g))
            glow.setColorAt(0.0, gc)
            gc.setAlpha(0)
            glow.setColorAt(1.0, gc)
            p.setBrush(glow)
            p.drawEllipse(QPointF(fx, H / 2), r * 2.4, r * 2.4)
            p.setBrush(QColor("#ffffff"))
            p.drawEllipse(QPointF(fx, H / 2), r, r)
            p.setBrush(self.accent)
            p.drawEllipse(QPointF(fx, H / 2), r * 0.42, r * 0.42)
        p.end()
