# paint_cache.py
"""
Кэш отрисовки обычных кнопок (QPushButton со стилями QSS).

При прокрутке страницы Qt каждый кадр заново рисует все видимые кнопки: скруглённые края, градиенты
«стекла», текст, иконку — на странице Echoes.NET их ~200, и это половина времени кадра. Здесь кнопка
рисуется один раз в картинку под экран, а в следующих кадрах просто копируется. Картинка пересобирается,
когда меняется то, от чего зависит вид: размер, текст, иконка, наведение, нажатие, «включена»,
доступность, фокус, свой стиль или палитра.

Только точный класс QPushButton: у подклассов бывает своя анимированная отрисовка — их не трогаем.
"""
from __future__ import annotations

import time
import weakref
from collections import OrderedDict

from PyQt6.QtCore import QEvent, QObject, QPointF, Qt, QTimer
from PyQt6.QtGui import QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QPushButton, QStyle, QStyleOptionButton
from i18n import display_text

_FILTER = None
SPRING = False          # пружинящие кнопки (ya_decor): при нажатии сжимаются на 4 %, отпускание — с отскоком
_PRESS_S, _DUR = 0.955, 0.32
_BUDGET=8*2**20
_BYTES=0
_ENTRIES=OrderedDict()


def _drop(key):
    global _BYTES
    entry=_ENTRIES.pop(key,None)
    if entry:
        _BYTES-=entry[1];widget=entry[0]()
        if widget is not None:widget.__dict__.pop('_echoes_pc',None)


def set_budget(size):
    global _BUDGET
    _BUDGET=max(1,int(size))
    while _BYTES>_BUDGET and _ENTRIES:_drop(next(iter(_ENTRIES)))


def clear_all():
    for key in list(_ENTRIES):_drop(key)


def _record(widget,pixmap):
    global _BYTES
    key=id(widget);old=_ENTRIES.pop(key,None)
    if old:_BYTES-=old[1]
    size=pixmap.width()*pixmap.height()*4
    _ENTRIES[key]=(weakref.ref(widget,lambda ref,key=key:_drop(key)),size);_BYTES+=size
    set_budget(_BUDGET)


def _spring_scale(b) -> float:
    st = b.__dict__.get("_echoes_spring")
    if not st:
        return 1.0
    t0, down = st
    dt = time.monotonic() - t0
    if down:
        k = min(1.0, dt / 0.09)
        return 1.0 + (_PRESS_S - 1.0) * k
    if dt >= _DUR:
        b.__dict__.pop("_echoes_spring", None)
        return 1.0
    x = dt / _DUR                                              # затухающая пружина от сжатия к 1
    import math
    return 1.0 - (1.0 - _PRESS_S) * math.exp(-5.5 * x) * math.cos(9.0 * x)


def _key(b: QPushButton):
    return (b.width(), b.height(), b.devicePixelRatioF(), display_text(b), b.icon().cacheKey(),
            b.iconSize().width(), b.iconSize().height(), b.isDown(), b.isChecked(),
            b.underMouse(), b.isEnabled(), b.hasFocus(), b.isDefault(), b.autoDefault(), b.isFlat(),
            b.menu() is not None,
            b.styleSheet(), b.palette().cacheKey(), b.font().key(), b.layoutDirection(),
            b.property("echoes_round"), b.objectName())


class _ButtonCache(QObject):
    def __init__(self):
        super().__init__()
        self._busy = False
        self._springs = []
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._spring_tick)

    def _spring_tick(self):
        live = []
        for b in self._springs:
            try:
                b.update()
                if b.__dict__.get("_echoes_spring"):
                    live.append(b)
            except RuntimeError:
                pass
        self._springs = live
        if not live:
            self._timer.stop()

    def _spring(self, b, down):
        b.__dict__["_echoes_spring"] = (time.monotonic(), down)
        if b not in self._springs:
            self._springs.append(b)
        if not self._timer.isActive():
            self._timer.start()

    def eventFilter(self, obj, ev):
        et = ev.type()
        if et==QEvent.Type.Hide and type(obj) is QPushButton:
            import performance
            if performance.config()['release_hidden']:_drop(id(obj))
            return False
        if SPRING and et in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease) and                 isinstance(obj, QPushButton) and type(obj) is QPushButton and obj.isEnabled():
            try:
                if ev.button() == Qt.MouseButton.LeftButton:
                    self._spring(obj, et == QEvent.Type.MouseButtonPress)
            except RuntimeError:
                pass
            return False
        if et != QEvent.Type.Paint or self._busy or type(obj) is not QPushButton:
            return False
        try:
            if obj.width() <= 0 or obj.height() <= 0 or obj.width() * obj.height() > 600_000:
                return False
            k = _key(obj)
            c = obj.__dict__.get("_echoes_pc")
            if c is None or c[0] != k:
                dpr = obj.devicePixelRatioF()
                pm = QPixmap(int(obj.width() * dpr + 0.5), int(obj.height() * dpr + 0.5))
                pm.setDevicePixelRatio(dpr)
                pm.fill(Qt.GlobalColor.transparent)
                # QWidget.render() here would send another Paint event while Qt is
                # already painting this widget. Draw its style directly instead.
                option = QStyleOptionButton()
                option.initFrom(obj)
                option.text = display_text(obj)
                option.icon = obj.icon()
                option.iconSize = obj.iconSize()
                if obj.isFlat():
                    option.features |= QStyleOptionButton.ButtonFeature.Flat
                if obj.isDefault():
                    option.features |= QStyleOptionButton.ButtonFeature.DefaultButton
                if obj.autoDefault():
                    option.features |= QStyleOptionButton.ButtonFeature.AutoDefaultButton
                if obj.menu() is not None:
                    option.features |= QStyleOptionButton.ButtonFeature.HasMenu
                if obj.isDown():
                    option.state |= QStyle.StateFlag.State_Sunken
                elif not obj.isFlat():
                    option.state |= QStyle.StateFlag.State_Raised
                if obj.isChecked():
                    option.state |= QStyle.StateFlag.State_On
                painter = QPainter(pm)
                try:
                    obj.style().drawControl(QStyle.ControlElement.CE_PushButton, option, painter, obj)
                finally:
                    painter.end()
                obj.__dict__["_echoes_pc"] = c = (k, pm)
                _record(obj,pm)
            elif id(obj) in _ENTRIES:_ENTRIES.move_to_end(id(obj))
            p = QPainter(obj)
            s = _spring_scale(obj) if SPRING else 1.0
            if s != 1.0:
                p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                cx, cy = obj.width() / 2, obj.height() / 2
                p.translate(QPointF(cx, cy))
                p.scale(s, s)
                p.translate(QPointF(-cx, -cy))
            p.drawPixmap(0, 0, c[1])
            p.end()
            return True
        except RuntimeError:
            return False


def install():
    global _FILTER
    if _FILTER is None:
        _FILTER = _ButtonCache()
        QApplication.instance().installEventFilter(_FILTER)
