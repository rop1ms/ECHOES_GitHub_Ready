# autoscroll.py
"""
Автопрокрутка средней кнопкой мыши — как в браузерах.

Нажали колесо над чем-то прокручиваемым (список, страница, ряд карточек) — на месте нажатия появляется
значок, и содержимое едет туда, куда отведена мышь: чем дальше от значка, тем быстрее.
  * нажали и сразу отпустили — режим держится, пока не щёлкнете любой кнопкой (или Esc / колесо);
  * нажали, потянули и отпустили — прокрутка только пока кнопка зажата.
Работает во всём приложении: один фильтр событий, ничего в виджетах менять не нужно.
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QCursor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QAbstractItemView, QAbstractScrollArea, QApplication, QWidget

DEAD = 10.0                       # «мёртвая зона» вокруг значка, px
SPEED = 6.0                       # px/с на каждый px отклонения за мёртвой зоной


class _Anchor(QWidget):
    """Значок в точке нажатия: кружок со стрелками в те стороны, куда можно ехать."""
    SIZE = 34

    def __init__(self, parent, v, h):
        super().__init__(parent)
        self.v, self.h = v, h
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.resize(self.SIZE, self.SIZE)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self.SIZE
        c = s / 2
        p.setPen(QPen(QColor(255, 255, 255, 200), 1.5))
        p.setBrush(QColor(20, 20, 26, 200))
        p.drawEllipse(QRectF(1, 1, s - 2, s - 2))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 230))
        p.drawEllipse(QPointF(c, c), 2.2, 2.2)

        def tri(dx, dy):
            path = QPainterPath()
            tip = QPointF(c + dx * 11, c + dy * 11)
            base = QPointF(c + dx * 6, c + dy * 6)
            side = QPointF(-dy * 4, dx * 4)
            path.moveTo(tip)
            path.lineTo(base + side)
            path.lineTo(base - side)
            path.closeSubpath()
            p.drawPath(path)
        if self.v:
            tri(0, -1)
            tri(0, 1)
        if self.h:
            tri(-1, 0)
            tri(1, 0)
        p.end()


class AutoScroll(QObject):
    def __init__(self, app):
        super().__init__(app)
        self.area = None
        self.anchor = QPoint()
        self.icon = None
        self.sticky = False
        self.moved = False
        self.acc = [0.0, 0.0]
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        app.installEventFilter(self)

    # ── что прокручивать ──
    @staticmethod
    def _bars(area):
        vb, hb = area.verticalScrollBar(), area.horizontalScrollBar()
        v = vb is not None and vb.maximum() > vb.minimum()
        h = hb is not None and hb.maximum() > hb.minimum()
        return v, h

    def _find(self, w):
        """(что листать вверх-вниз, что листать вбок) — ближайшие прокручиваемые предки по каждой оси:
        над рядом карточек вбок едет ряд, а вверх-вниз — страница, на которой он лежит."""
        va = ha = None
        while w is not None:
            if isinstance(w, QAbstractScrollArea) and not getattr(w,'_auto_height',False):
                v, h = self._bars(w)
                if v and va is None:
                    va = w
                if h and ha is None:
                    ha = w
                if va is not None and ha is not None:
                    break
            if w.isWindow():
                break
            w = w.parentWidget()
        return (va, ha) if (va is not None or ha is not None) else None

    # ── режим ──
    def _start(self, areas, gpos: QPoint):
        self.area = areas
        self.anchor = gpos
        self.sticky = False
        self.moved = False
        self.acc = [0.0, 0.0]
        v, h = areas[0] is not None, areas[1] is not None
        win = (areas[0] or areas[1]).window()
        self.icon = _Anchor(win, v, h)
        lp = win.mapFromGlobal(gpos)
        self.icon.move(lp.x() - _Anchor.SIZE // 2, lp.y() - _Anchor.SIZE // 2)
        self.icon.show()
        self.icon.raise_()
        QApplication.setOverrideCursor(Qt.CursorShape.SizeAllCursor if (v and h) else
                                       (Qt.CursorShape.SizeVerCursor if v else Qt.CursorShape.SizeHorCursor))
        self.timer.start()

    def _stop(self):
        if self.area is None:
            return
        self.timer.stop()
        self.area = None
        try:
            if self.icon is not None:
                self.icon.hide()
                self.icon.deleteLater()
        except RuntimeError:
            pass
        self.icon = None
        QApplication.restoreOverrideCursor()

    def _tick(self):
        areas = self.area
        if areas is None:
            return
        try:
            if not any(a is not None and a.isVisible() for a in areas):
                self._stop()
                return
            d = QCursor.pos() - self.anchor
            dt = self.timer.interval() / 1000.0
            va, ha = areas
            for k, (dist, area) in enumerate(((d.y(), va), (d.x(), ha))):
                if area is None:
                    continue
                bar = area.verticalScrollBar() if k == 0 else area.horizontalScrollBar()
                if abs(dist) <= DEAD:
                    self.acc[k] = 0.0
                    continue
                over = abs(dist) - DEAD
                px = math.copysign(over * SPEED * (1.0 + over / 220.0), dist) * dt
                # списки с прокруткой «по строке»: единица полосы — строка, а не пиксель
                if isinstance(area, QAbstractItemView):
                    mode = area.verticalScrollMode() if k == 0 else area.horizontalScrollMode()
                    if mode == QAbstractItemView.ScrollMode.ScrollPerItem:
                        px /= 28.0
                self.acc[k] += px
                step = int(self.acc[k])
                if step:
                    self.acc[k] -= step
                    bar.setValue(bar.value() + step)
        except RuntimeError:                               # область удалили
            self.area = None
            self._stop()

    # ── события ──
    def eventFilter(self, obj, ev):
        t = ev.type()
        if self.area is not None:
            if t == QEvent.Type.MouseMove:
                if (QCursor.pos() - self.anchor).manhattanLength() > DEAD * 1.5:
                    self.moved = True
                return False
            if t in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
                self._stop()                               # любой щелчок — выйти (сам щелчок не нужен)
                return True
            if t == QEvent.Type.MouseButtonRelease:
                if ev.button() == Qt.MouseButton.MiddleButton:
                    if self.moved:
                        self._stop()                       # тянули с зажатой кнопкой — отпустили, стоп
                    else:
                        self.sticky = True                 # щёлкнули — режим держится
                return True
            if t == QEvent.Type.Wheel or (t == QEvent.Type.KeyPress and ev.key() == Qt.Key.Key_Escape):
                self._stop()
                return True
            if t in (QEvent.Type.WindowDeactivate, QEvent.Type.ApplicationDeactivate):
                self._stop()
            return False
        if t == QEvent.Type.MouseButtonPress and isinstance(obj, QWidget):
            try:
                if ev.button() != Qt.MouseButton.MiddleButton:
                    return False
            except AttributeError:
                return False
            area = self._find(obj)
            if area is None:
                return False
            self._start(area, ev.globalPosition().toPoint())
            return True
        return False


_INSTANCE = None


def install(app):
    global _INSTANCE
    if _INSTANCE is None and app is not None:
        _INSTANCE = AutoScroll(app)
    return _INSTANCE
