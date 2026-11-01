# cover_crate.py
"""
Стопка обложек для тем Fluid / Fluid Dark (без ящика).

Обложки стоят в подставке, как конверты пластинок в ящике магазина:
спереди — то, что играет сейчас, за ней (выше и чуть глубже) — следующие
треки очереди. При смене трека передняя обложка красиво улетает вверх,
а следующая выезжает на её место; новая «последняя» проявляется сзади.
Если включили трек не по порядку — новая обложка падает сверху в ящик.
Клик по задней обложке — включить этот трек.

Рисование — QPainter, анимация — один QTimer, который работает только
пока что-то движется.
"""
from __future__ import annotations

import hashlib
import math
import time
from pathlib import Path

from img_load import read_image
from PyQt6.QtCore import Qt, QRectF, QPointF, QTimer, pyqtSignal, QObject, QRunnable, QThreadPool
from frameclock import FrameTimer
from PyQt6.QtGui import (QColor, QPainter, QPen, QPixmap, QImage, QFont, QFontMetrics,
                         QLinearGradient)
from PyQt6.QtWidgets import QWidget, QSizePolicy

INK_LIGHT = QColor(46, 44, 30)
INK_DARK = QColor(4, 4, 6)
CREAM = QColor(236, 231, 200)
ORANGE = QColor(255, 138, 0)

_PM_CACHE: dict = {}
_LOADING: set = set()
_FAILED: set = set()
_POOL = QThreadPool()
_POOL.setMaxThreadCount(2)
COVER_PX = 420              # обложка грузится один раз в таком размере, дальше — только масштаб при рисовании


class _Loader(QObject):
    done = pyqtSignal(str, object)


class _LoadJob(QRunnable):
    """Чтение и уменьшение картинки — в фоне, чтобы смена трека не дёргала кадр."""

    def __init__(self, path, loader):
        super().__init__()
        self.path, self.loader = path, loader

    def run(self):
        img = read_image(self.path, COVER_PX * 2)
        if not img.isNull():
            side = min(img.width(), img.height())
            img = img.copy((img.width() - side) // 2, (img.height() - side) // 2, side, side)
            if side > COVER_PX:
                img = img.scaled(COVER_PX, COVER_PX, Qt.AspectRatioMode.IgnoreAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
        try:
            self.loader.done.emit(self.path, img)
        except RuntimeError:
            pass


def _ease_out(t):
    return 1 - (1 - t) ** 3


def _ease_in_out(t):
    return 4 * t * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


class _Item:
    __slots__ = ("key", "track", "pos", "target", "alpha", "alpha_t", "fly", "drop", "rise")

    def __init__(self, key, track, pos):
        self.key, self.track = key, track
        self.pos = self.target = float(pos)
        self.alpha, self.alpha_t = 0.0, 1.0
        self.fly = -1.0          # ≥0 — улетает вверх (0→1)
        self.drop = -1.0         # ≥0 — падает сверху на место (1→0)
        self.rise = -1.0         # ≥0 — выезжает из ящика снизу (1→0)


class CoverCrate(QWidget):
    play_ahead = pyqtSignal(int)       # клик по k-й обложке очереди (1 — следующая)

    DEPTH = 4                          # сколько обложек очереди видно сзади

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(170)
        self.setMouseTracking(True)
        self.items: list[_Item] = []
        self.flying: list[_Item] = []
        self.dark = False
        self.dom = QColor(17, 182, 208)
        self.font_family = ""
        self._hover = -1
        self._last = time.monotonic()
        self._timer = FrameTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._step)
        self._loader = _Loader(self)
        self._loader.done.connect(self._on_loaded)

    # ── внешний вид ── #

    def set_dark(self, on: bool):
        self.dark = bool(on)
        self.update()

    def set_dominant(self, color: QColor):
        self.dom = QColor(color)
        self.update()

    def set_font_family(self, fam: str):
        self.font_family = fam or ""
        self.update()

    # ── данные ── #

    @staticmethod
    def _key(t):
        return str(t.get("path") or t.get("title") or id(t))

    def set_tracks(self, current: dict | None, upcoming: list):
        """current — играющий трек, upcoming — следующие по очереди."""
        new = ([current] if current else []) + list(upcoming[: self.DEPTH])
        keys = [self._key(t) for t in new]
        old_keys = [it.key for it in self.items]
        if keys == old_keys:
            return
        first = not self.items and not self.flying
        old_front = self.items[0] if self.items else None
        by_key = {}
        for it in self.items:
            by_key.setdefault(it.key, it)
        out = []
        for i, (k, t) in enumerate(zip(keys, new)):
            it = by_key.pop(k, None)
            if it is None:
                it = _Item(k, t, i + 0.9 if i > 0 else 0)
                if first:
                    it.alpha = 1.0
                    it.pos = i
                elif i == 0:
                    # трек включили не по порядку — обложка падает сверху
                    # (или, если это был предыдущий, «возвращается» тем же путём)
                    it.drop = 1.0
                    it.alpha = 1.0
            else:
                it.track = t
            it.target = float(i)
            it.alpha_t = 1.0
            out.append(it)
        # то, что осталось без места
        for it in by_key.values():
            if it is old_front and (not keys or keys[0] != it.key):
                it.fly = 0.0
                self.flying.append(it)
            else:
                it.alpha_t = 0.0
                it.target = it.pos + 0.8
                self.flying.append(it)          # дорисуем исчезновение
        self.items = out
        self._kick()

    def _kick(self):
        self._last = time.monotonic()
        if not self._timer.isActive():
            self._timer.start()
        self.update()

    # ── анимация ── #

    def _step(self):
        now = time.monotonic()
        dt = min(0.05, now - self._last)
        self._last = now
        moving = False
        k = 1 - math.exp(-dt * 9.0)
        for it in self.items + self.flying:
            d = it.target - it.pos
            if abs(d) > 0.002:
                it.pos += d * k
                moving = True
            else:
                it.pos = it.target
            da = it.alpha_t - it.alpha
            if abs(da) > 0.01:
                it.alpha += da * (1 - math.exp(-dt * 7.0))
                moving = True
            else:
                it.alpha = it.alpha_t
            if it.fly >= 0:
                it.fly = min(1.0, it.fly + dt / 0.75)
                moving = moving or it.fly < 1.0
            if it.drop >= 0:
                it.drop = max(0.0, it.drop - dt / 0.6)
                if it.drop <= 0:
                    it.drop = -1.0
                moving = True
        self.flying = [it for it in self.flying
                       if (it.fly >= 0 and it.fly < 1.0) or (it.fly < 0 and it.alpha > 0.02)]
        if self.flying:
            moving = True
        self.update()
        if not moving:
            self._timer.stop()

    # ── геометрия ── #

    def _geom(self):
        W, H = self.width(), self.height()
        S = min((H - 12) / 1.17, (W - 14) / 1.22, 340.0)
        return W, H, max(40.0, S), 10.0, H - 8.0

    def _slot_rect(self, pos, S, x0, bottom0):
        """Прямоугольник обложки в «ячейке» pos (0 — передняя, дальше — выше и правее)."""
        s = S * (0.965 ** pos)
        cx = x0 + S * 0.5 + pos * S * 0.07
        bottom = bottom0 - pos * S * 0.075
        return QRectF(cx - s / 2, bottom - s, s, s)

    # ── обложки: грузятся в фоне один раз, рисуются масштабированием ── #

    def _cover_pm(self, t):
        path = str(t.get("cover") or "")
        key = path if path else ("ph", self._key(t))
        pm = _PM_CACHE.get(key)
        if pm is not None:
            return pm
        if path in _FAILED:
            path = ""
        if path and path not in _LOADING and Path(path).exists():
            _LOADING.add(path)
            _POOL.start(_LoadJob(path, self._loader))
        if path and path in _LOADING:
            ph = _PM_CACHE.get(("ph", self._key(t)))
            if ph is None:
                ph = self._placeholder(t, 256)
                _PM_CACHE[("ph", self._key(t))] = ph
            return ph
        ph = _PM_CACHE.get(("ph", self._key(t)))
        if ph is None:
            ph = self._placeholder(t, 256)
            _PM_CACHE[("ph", self._key(t))] = ph
        return ph

    def _on_loaded(self, path, img):
        _LOADING.discard(path)
        if img is not None and not img.isNull():
            if len(_PM_CACHE) > 32:                     # в ящике видно 5 обложек — 120 штук по 420² было ~85 МБ
                for k in list(_PM_CACHE)[:16]:
                    _PM_CACHE.pop(k, None)
            _PM_CACHE[path] = QPixmap.fromImage(img)
        else:
            _FAILED.add(path)
        self.update()

    def _placeholder(self, t, px):
        h = int(hashlib.md5(self._key(t).encode("utf-8", "ignore")).hexdigest()[:6], 16)
        c1 = QColor.fromHsv(h % 360, 150, 210)
        c2 = QColor.fromHsv((h // 7) % 360, 190, 120)
        pm = QPixmap(px, px)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        g = QLinearGradient(0, 0, px, px)
        g.setColorAt(0, c1)
        g.setColorAt(1, c2)
        p.fillRect(0, 0, px, px, g)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(12, 12, 14, 220))
        r = px * 0.34
        p.drawEllipse(QPointF(px * 0.5, px * 0.5), r, r)
        p.setBrush(c1.lighter(120))
        p.drawEllipse(QPointF(px * 0.5, px * 0.5), r * 0.32, r * 0.32)
        f = QFont(self.font_family) if self.font_family else QFont()
        f.setPixelSize(max(9, int(px * 0.085)))
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(255, 255, 255, 235))
        title = str(t.get("title") or "")
        p.drawText(QRectF(px * 0.07, px * 0.04, px * 0.86, px * 0.2),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                   QFontMetrics(f).elidedText(title, Qt.TextElideMode.ElideRight, int(px * 0.86)))
        p.end()
        return pm

    def _draw_cover(self, p, it, r, ink, extra_dark=0.0, angle=0.0, scale=1.0, opacity=1.0):
        if opacity <= 0.01:
            return
        pm = self._cover_pm(it.track)
        p.save()
        p.setOpacity(opacity)
        c = r.center()
        p.translate(c)
        if angle:
            p.rotate(angle)
        if scale != 1.0:
            p.scale(scale, scale)
        rr = QRectF(-r.width() / 2, -r.height() / 2, r.width(), r.height())
        rad = max(3.0, r.width() * 0.025)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 70 if self.dark else 45))
        p.drawRoundedRect(rr.translated(r.width() * 0.025, r.width() * 0.03), rad, rad)
        # обрезка до квадрата по центру (без клипа — он дорогой)
        sw, sh = pm.width(), pm.height()
        side = min(sw, sh)
        src = QRectF((sw - side) / 2, (sh - side) / 2, side, side)
        p.drawPixmap(rr, pm, src)
        if extra_dark > 0:
            p.setBrush(QColor(0, 0, 0, int(255 * min(0.75, extra_dark))))
            p.drawRoundedRect(rr, rad, rad)
        p.setPen(QPen(ink, max(2.0, r.width() * 0.011)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rr, rad, rad)
        p.restore()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        W, H, S, x0, bottom0 = self._geom()
        ink = INK_DARK if self.dark else INK_LIGHT
        for it in sorted(self.items + [f for f in self.flying if f.fly < 0], key=lambda i: -i.pos):
            r = self._slot_rect(it.pos, S, x0, bottom0)
            op = it.alpha
            if it.drop >= 0:
                e = _ease_out(1 - it.drop)
                r.translate(0, -(1 - e) * (H + S))
                op = 1.0
            ed = 0.10 * it.pos
            if it.pos > 0.5 and self._hover == round(it.pos):
                ed = max(0.0, ed - 0.14)               # наведённая задняя — светлее
            self._draw_cover(p, it, r, ink, extra_dark=ed, opacity=op)
        for it in self.flying:                         # улетающая передняя — поверх всех
            if it.fly < 0:
                continue
            e = _ease_in_out(it.fly)
            r = self._slot_rect(0, S, x0, bottom0)
            r.translate(S * 0.18 * e, -(H + S * 1.1) * e)
            self._draw_cover(p, it, r, ink, angle=-14 * e, scale=1 + 0.08 * e, opacity=1.0 - e ** 3)
        p.end()

    # ── мышь ── #

    def _hit(self, pt):
        W, H, S, x0, bottom0 = self._geom()
        # сверху видны только «хвостики» задних обложек — проверяем от ближней к дальней
        for it in sorted(self.items, key=lambda i: i.pos):
            r = self._slot_rect(it.target, S, x0, bottom0)
            if it.target < 0.5:
                if r.contains(pt):
                    return 0
                continue
            if r.contains(pt):
                return int(round(it.target))
        return -1

    def mouseMoveEvent(self, e):
        h = self._hit(e.position())
        if h != self._hover:
            self._hover = h
            self.setCursor(Qt.CursorShape.PointingHandCursor if h > 0 else Qt.CursorShape.ArrowCursor)
            if h > 0 and h < len(self.items):
                t = self.items[h].track
                self.setToolTip(f"Включить: {t.get('artist', '')} — {t.get('title', '')}".strip(" —"))
            else:
                self.setToolTip("")
            self.update()

    def leaveEvent(self, e):
        self._hover = -1
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            h = self._hit(e.position())
            if h > 0:
                self.play_ahead.emit(h)
