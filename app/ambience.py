# ambience.py
"""
Декоративные lo-fi слои поверх/позади интерфейса:

  * BackgroundAmbience — сильно размытая обложка на весь фон окна с
    медленной пульсацией масштаба/прозрачности ("дыхание"). Живёт
    самым нижним слоем (lower()).
  * DustParticles — плавающие пылинки + зерно (имитация шума старой
    плёнки/винила). Живёт самым верхним слоем, прозрачен для мыши.

Оба виджета не содержат бизнес-логики и не знают про AudioEngine —
чисто оформление, включается/выключается через set_enabled().

ОПТИМИЗАЦИИ (v2):
  * BackgroundAmbience больше НЕ вызывает pixmap.scaled() в каждом кадре.
    Вместо этого set_cover() один раз строит _base_pm фиксированного размера
    (MAX_BASE × MAX_BASE), а paintEvent рисует его через QTransform — только
    матричное умножение на GPU/QPainter, без ресэмплинга пикселей на CPU.
    Дополнительно paintEvent пропускает перерисовку, если breath/opacity
    изменились меньше чем на порог (_dirty-флаг).
  * DustParticles кешируют film-grain в отдельный QPixmap (_grain_cache),
    который пересчитывается раз в 4 тика, а не каждый кадр; частицы
    по-прежнему анимируются каждый тик, но зерно обходится без random в
    paintEvent.
  * Таймер BackgroundAmbience понижен до 50 мс (20 fps) — дыхание медленное,
    разницы на глаз нет, CPU-нагрузка падает вдвое.
"""

import math
import random
import time

from img_load import load_pixmap
from PyQt6.QtCore import Qt, QPointF, QRectF, QRect, QTimer
from frameclock import FrameTimer, animation_delta
from PyQt6.QtGui import QColor, QPainter, QPixmap, QTransform, QRegion
from PyQt6.QtWidgets import QWidget


# Максимальный размер кешированной базовой текстуры.
# 256×256 более чем достаточно для размытого фона — экономим память
# и исключаем пересчёт при ресайзе окна.
_MAX_BASE = 256

# Минимальное изменение breath/opacity, при котором стоит перерисовывать.
_BREATH_EPS = 0.0003
_OPACITY_EPS = 0.0003


class BackgroundAmbience(QWidget):
    """Размытая обложка на весь фон с медленным «дыханием».

    v3 (FPS): это НИЖНИЙ слой окна — каждая его перерисовка тянет за собой
    перерисовку всего интерфейса поверх. Поэтому:
      * кадр готовится в кэш-пиксмап (трансформ + затемнение) не чаще 8 раз/с
        и только если картинка сдвинулась хотя бы на ~1 px;
      * paintEvent лишь блитит нужный кусок кэша (без масштабирования) —
        когда поверх перерисовывается, например, крутящийся винил, фон
        под ним обходится почти бесплатно.
    """

    TICK_MS = 125

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

        self._base_pm: QPixmap = QPixmap()
        self._cache: QPixmap | None = None
        self._cache_params = None
        self._t = 0.0
        self._enabled = True

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(self.TICK_MS)

    # ------------------------------------------------------------------ #

    def set_enabled(self, v: bool):
        v = bool(v)
        if v == self._enabled:
            return
        self._enabled = v
        self._cache = None
        # выключенный слой прячем: невидимый виджет Qt не рисует вовсе, а «пустой» видимый нижний
        # слой перерисовывался под каждым обновлением интерфейса (в Echoes Music, студии)
        self.setVisible(v)
        if v:
            self.lower()
        self.update()

    def set_cover(self, path_or_pixmap):
        if isinstance(path_or_pixmap, QPixmap):
            pm = path_or_pixmap
        elif path_or_pixmap:
            pm = load_pixmap(str(path_or_pixmap), 256)
        else:
            pm = QPixmap()
        if not pm.isNull():
            # гауссово размытие вместо «8×8 → растянуть» (то давало квадраты и кресты)
            import blur_fx
            self._base_pm = QPixmap.fromImage(blur_fx.blurred_cover(pm, _MAX_BASE, _MAX_BASE, _MAX_BASE / 14.0))
        else:
            self._base_pm = QPixmap()
        self._cache = None
        self.update()

    # ------------------------------------------------------------------ #

    def _params(self):
        breath = 1.0 + 0.035 * math.sin(self._t * 0.35)
        opacity = 0.34 + 0.05 * math.sin(self._t * 0.35 + 1.1)
        return breath, opacity

    def _tick(self):
        if not self._enabled or self._base_pm.isNull() or not self.isVisible():
            return
        win = self.window()
        if win is not None and win.isMinimized():
            return
        self._t += 0.016 * 2.5 * (self.TICK_MS / 50.0)
        if self._cache_params is None:
            self._cache = None
            self.update()
            return
        b, o = self._params()
        b0, o0 = self._cache_params
        span = max(self.width(), self.height()) * 1.35
        if abs(b - b0) * span >= 1.0 or abs(o - o0) >= 0.006:
            self._cache = None
            self.update()

    def _build_cache(self):
        w, h = max(1, self.width()), max(1, self.height())
        dpr = max(1.0, float(self.devicePixelRatioF()))
        pm = QPixmap(int(w * dpr), int(h * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        breath, opacity = self._params()
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        bw, bh = self._base_pm.width(), self._base_pm.height()
        target = max(w, h) * 1.35 * breath
        scale = target / max(bw, bh)
        tr = QTransform()
        tr.translate((w - bw * scale) / 2.0, (h - bh * scale) / 2.0)
        tr.scale(scale, scale)
        p.setOpacity(max(0.0, min(1.0, opacity)))
        p.setTransform(tr)
        p.drawPixmap(0, 0, self._base_pm)
        p.resetTransform()
        p.setOpacity(1.0)
        p.fillRect(QRectF(0, 0, w, h), QColor(0, 0, 0, 120))
        p.end()
        self._cache = pm
        self._cache_params = (breath, opacity)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._cache = None

    def paintEvent(self, ev):
        if not self._enabled or self._base_pm.isNull():
            return
        if self._cache is None:
            self._build_cache()
        p = QPainter(self)
        p.drawPixmap(0, 0, self._cache)     # без трансформа: Qt блитит только область клипа
        p.end()


# ------------------------------------------------------------------ #

class DustParticles(QWidget):
    """Пылинки + зерно поверх всего окна.

    v3 (FPS): слой лежит ПОВЕРХ интерфейса и прозрачен, поэтому любая его
    перерисовка заставляет перерисоваться всё под ним. Раньше это был весь
    экран ~22 раза в секунду. Теперь:
      * перерисовываются только крошечные квадраты вокруг пылинок (старое и
        новое положение) — остальной интерфейс не трогается;
      * зерно статичное (строится один раз на размер окна) — мерцающее зерно
        требовало бы полной перерисовки экрана.
    """
    COUNT = 46
    # 30 кадров/с: пылинки медленные, а каждый их кадр перерисовывает всё, что под ними (на 144–240 Гц
    # это сотни перерисовок окна в секунду)
    TICK_MS = 33

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        rng = random.Random(7)
        self._x     = [rng.random() for _ in range(self.COUNT)]
        self._y     = [rng.random() for _ in range(self.COUNT)]
        self._vx    = [(rng.random() - 0.5) * 0.00035 for _ in range(self.COUNT)]
        self._vy    = [(rng.random() * 0.0006 + 0.0001) for _ in range(self.COUNT)]
        self._r     = [rng.uniform(0.6, 2.2) for _ in range(self.COUNT)]
        self._phase = [rng.uniform(0, 6.28) for _ in range(self.COUNT)]
        self._enabled = True
        self._grain_cache: QPixmap | None = None

        # 60 кадров/с по общим часам; движение — по реальному времени (раньше шаг
        # был «за тик» раз в 45 мс, и пылинки двигались рывками ~20 раз в секунду)
        self._last = time.perf_counter()
        self._timer = FrameTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(self.TICK_MS)

    def set_enabled(self, v: bool):
        v = bool(v)
        if v == self._enabled:
            return
        self._enabled = v
        # выключенный — спрятан (не перерисовывается поверх каждого обновления окна) и не держит
        # общие часы кадров: без него интерфейс в спокойных темах не просыпается 60 раз в секунду
        self.setVisible(v)
        if v:
            self.raise_()
            self._last = time.perf_counter()
            self._timer.start(self.TICK_MS)
        else:
            self._timer.stop()
        self.update()

    def _dot_rect(self, i: int, w: int, h: int) -> QRect:
        r = int(self._r[i]) + 2
        x, y = int(self._x[i] * w), int(self._y[i] * h)
        return QRect(x - r, y - r, 2 * r + 1, 2 * r + 1)

    def _tick(self):
        if not self._enabled or not self.isVisible():
            return
        win = self.window()
        if win is not None and win.isMinimized():
            return
        now = time.perf_counter()
        k = animation_delta(now - self._last) / 0.045      # скорости заданы «за 45 мс»
        self._last = now
        w, h = self.width(), self.height()
        region = QRegion()
        for i in range(self.COUNT):
            region = region.united(self._dot_rect(i, w, h))   # где была
            self._x[i] = (self._x[i] + self._vx[i] * k) % 1.0
            self._y[i] = (self._y[i] + self._vy[i] * k) % 1.0
            self._phase[i] += 0.05 * k
            region = region.united(self._dot_rect(i, w, h))   # где стала
        self.update(region)

    def _build_grain_cache(self, w: int, h: int) -> QPixmap:
        pm = QPixmap(max(1, w), max(1, h))
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 10))
        rng = random.Random(1)
        for _ in range(int(120 * (w * h) / (1600 * 900)) + 60):
            p.drawRect(QRectF(rng.random() * w, rng.random() * h, 1, 1))
        p.end()
        return pm

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._grain_cache = None

    def paintEvent(self, _):
        if not self._enabled:
            return
        w, h = self.width(), self.height()
        if self._grain_cache is None or self._grain_cache.size() != self.size():
            self._grain_cache = self._build_grain_cache(w, h)
        p = QPainter(self)
        p.drawPixmap(0, 0, self._grain_cache)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(self.COUNT):
            twinkle = 0.55 + 0.45 * math.sin(self._phase[i])
            p.setBrush(QColor(255, 255, 255, int(60 * twinkle)))
            p.drawEllipse(QPointF(self._x[i] * w, self._y[i] * h), self._r[i], self._r[i])
        p.end()
