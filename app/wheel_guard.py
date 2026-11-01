# wheel_guard.py
"""
Колесо мыши только листает страницы — ничего не меняет.

Раньше колесо над лентой карточек («Тренды», исполнители, плейлисты) листало ленту вбок вместо страницы,
а над ползунком громкости, выпадающим списком, числовым полем или вкладками — меняло их значение.
Один фильтр событий на всё приложение: если под курсором такой элемент (или лента, которая листается
только по горизонтали), прокрутка уходит ближайшей странице, которая листается вверх-вниз; если такой
страницы нет — колесо просто ничего не делает.

Плавно, как в Spotify: щелчок колеса не перескакивает на 3 строки, а добавляет ~100 px к цели, и страница
доезжает до неё за ~0.2 с (быстрая прокрутка складывается и разгоняется). Тачпад (точные пиксели) и
Ctrl/Shift+колесо — как раньше.

Не трогает: текст песни, список настроений «Моей волны», а также Echoes Studio и esu! — там колесо рабочий
инструмент (масштаб, ручки, выбор песни).
"""
from __future__ import annotations

import math
import time

from PyQt6.QtCore import QEvent, QObject, QPoint, QPointF, Qt, QTimer
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import (QAbstractItemView, QAbstractScrollArea, QAbstractSlider, QAbstractSpinBox, QApplication,
                             QComboBox, QScrollBar, QTabBar, QWidget)

_SKIP_MODULES = ("daw_", "osu_", "dsl_", "theme_studio", "button_editor", "esu")
_OWN_WHEEL = ("lyrics_view", "ya_wave", "net_files_ui")       # своя логика колеса — плавность не навязываем
STEP_PX = 100                                                  # за один щелчок колеса
TAU = 0.07                                                     # постоянная времени доводки, с


class _Smooth(QObject):
    """Плавная доводка полос прокрутки к цели (экспоненциально, по реальному времени — без рывков при лагах)."""

    def __init__(self):
        super().__init__()
        self.items: dict = {}                                  # id(bar) → [bar, цель, позиция, что выставили]
        try:                                                   # общие часы кадров (в такт экрану, VSync)
            from frameclock import FrameTimer
            self.timer = FrameTimer(self)
        except Exception:                                      # noqa: BLE001
            self.timer = QTimer(self)
            self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(8)
        self.timer.timeout.connect(self._tick)
        self.t = 0.0

    def add(self, bar, delta):
        st = self.items.get(id(bar))
        if st is None or st[0] is not bar or bar.value() != st[3]:      # полосу двигал кто-то ещё — с текущего места
            st = [bar, float(bar.value()), float(bar.value()), bar.value()]
        st[1] = max(bar.minimum(), min(bar.maximum(), st[1] + delta))
        self.items[id(bar)] = st
        if not self.timer.isActive():
            self.t = time.perf_counter()
            self.timer.start()

    def _tick(self):
        now = time.perf_counter()
        dt = min(0.05, now - self.t)
        self.t = now
        a = 1.0 - math.exp(-dt / TAU)
        for k, st in list(self.items.items()):
            bar = st[0]
            try:
                if bar.value() != st[3]:                       # перехватили (перетащили ползунок, перестроили)
                    self.items.pop(k, None)
                    continue
                st[2] += (st[1] - st[2]) * a
                if abs(st[1] - st[2]) < 0.6:
                    st[2] = st[1]
                v = int(round(st[2]))
                if v != bar.value():
                    bar.setValue(v)
                st[3] = bar.value()
                if st[2] == st[1] or st[3] in (bar.minimum(), bar.maximum()) and abs(st[1] - st[3]) < 1:
                    self.items.pop(k, None)
            except RuntimeError:                               # виджет удалён
                self.items.pop(k, None)
        if not self.items:
            self.timer.stop()


_SMOOTH = None


def _smooth_ok(area, ev) -> bool:
    if not ev.pixelDelta().isNull() or ev.modifiers() & (Qt.KeyboardModifier.ControlModifier |
                                                         Qt.KeyboardModifier.ShiftModifier):
        return False                                           # тачпад / зум / вбок — как было
    if isinstance(area, QAbstractItemView) and             area.verticalScrollMode() != QAbstractItemView.ScrollMode.ScrollPerPixel:
        return False                                           # список по строкам — значения не в пикселях
    mod = type(area).__module__ or ""
    return not mod.startswith(_OWN_WHEEL)


def smooth_scroll(area, ev) -> bool:
    """Плавно прокрутить area на щелчки колеса из ev. False — не взялись (пусть листает Qt)."""
    global _SMOOTH
    if not _smooth_ok(area, ev):
        return False
    bar = area.verticalScrollBar()
    dy = ev.angleDelta().y()
    if bar is None or not dy:
        return False
    if _SMOOTH is None:
        _SMOOTH = _Smooth()
    _SMOOTH.add(bar, -dy / 120.0 * STEP_PX)
    return True


def _value_widget(w) -> bool:
    return (isinstance(w, (QAbstractSpinBox, QComboBox, QTabBar))
            or (isinstance(w, QAbstractSlider) and not isinstance(w, QScrollBar)))


def _skipped(w) -> bool:
    mod = type(w).__module__ or ""
    return mod.startswith(_SKIP_MODULES)


class WheelGuard(QObject):
    def __init__(self):
        super().__init__()
        self._sending = False

    def eventFilter(self, obj, ev):
        if ev.type() != QEvent.Type.Wheel or self._sending or not isinstance(obj, QWidget):
            return False
        dy = ev.angleDelta().y()
        vertical = dy != 0 and abs(dy) >= abs(ev.angleDelta().x())
        redirect = False
        w = obj
        while w is not None:
            if _skipped(w):
                return False                               # студия / игра — колесо работает как задумано там
            if _value_widget(w):
                redirect = True
            elif isinstance(w, QAbstractScrollArea) and not isinstance(w, QComboBox):
                vbar = w.verticalScrollBar()
                if vertical and vbar is not None and vbar.maximum() > vbar.minimum():
                    if not redirect:
                        return smooth_scroll(w, ev)        # обычная прокрутка страницы / списка — плавно
                    return self._forward(w, ev)
                if vertical:                               # лента только вбок — колесо вверх-вниз не её
                    redirect = True
                elif not redirect:
                    return False
            w = w.parentWidget()
        return redirect                                    # некому листать — значение не меняем

    def _forward(self, area, ev):
        if smooth_scroll(area, ev):
            return True
        vp = area.viewport()
        gp = ev.globalPosition()
        lp = QPointF(vp.mapFromGlobal(gp.toPoint()))
        fwd = QWheelEvent(lp, gp, ev.pixelDelta(), QPoint(0, ev.angleDelta().y()), ev.buttons(),
                          ev.modifiers(), ev.phase(), ev.inverted())
        self._sending = True
        try:
            QApplication.sendEvent(vp, fwd)
        finally:
            self._sending = False
        return True


_GUARD = None


def install():
    global _GUARD
    if _GUARD is None:
        _GUARD = WheelGuard()
        QApplication.instance().installEventFilter(_GUARD)
