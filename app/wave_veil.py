# wave_veil.py
"""
«Моя волна» во весь экран: панели вокруг (верх, разделы, плеер, угловые) заливаются чернотой и туманом.

Входишь в «Мою волну» — от неё наружу по всем стеклянным панелям расходится темнота (фронт с мягким
краем и цветной кромкой), сквозь неё плывёт туман; остаются только кнопки и плеер. Подписи-украшения
(«РАЗДЕЛЫ», карточка исполнителя) растворяются. Выходишь — всё так же плавно возвращается.

Рисует AmbientFrame.paintEvent (paint): поверх своего стекла — затемнение и туман этой панели.
Туман — заготовка на размер панели (мягкие облака из шума, одна картинка), за кадр только сдвиг
и прозрачность — переход стоит пару миллисекунд на кадр.
"""
from __future__ import annotations

import random

from PyQt6.QtCore import QEasingCurve, QObject, QPointF, QRectF, Qt, QVariantAnimation
from PyQt6.QtGui import QColor, QImage, QPainter, QPainterPath, QPixmap, QRadialGradient
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QWidget

IN_MS = 950
OUT_MS = 700
DARK = 0.93                                                    # насколько черны панели в «Моей волне»
SKIP = ("YaContent", "ListenerCard")                           # сама «Моя волна» и карточки настроек

_VEIL = None


def level() -> float:
    return _VEIL.level if _VEIL is not None else 0.0


def install(shell):
    global _VEIL
    if _VEIL is None or _VEIL.shell is not shell:
        _VEIL = Veil(shell)
    return _VEIL


def _fog(w: int, h: int, seed: int) -> QPixmap:
    """Мягкие облака тумана: две октавы шума низкого разрешения, растянутые со сглаживанием."""
    rnd = random.Random(seed)
    out = QPixmap(max(1, w), max(1, h))
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    for cell, alpha in ((90, 0.75), (34, 0.45)):
        gw, gh = max(2, w // cell + 2), max(2, h // cell + 2)
        img = QImage(gw, gh, QImage.Format.Format_ARGB32_Premultiplied)
        for y in range(gh):
            for x in range(gw):
                v = rnd.random() ** 2.2
                a = int(255 * v * alpha)
                c = QColor(120 + int(60 * v), 128 + int(50 * v), 170 + int(60 * v), a)
                img.setPixelColor(x, y, c)
        p.drawImage(QRectF(-cell, -cell, w + 2 * cell, h + 2 * cell),
                    img.scaled(gw * 8, gh * 8, Qt.AspectRatioMode.IgnoreAspectRatio,
                               Qt.TransformationMode.SmoothTransformation))
    p.end()
    return out


class Veil(QObject):
    def __init__(self, shell):
        super().__init__(shell)
        self.shell = shell
        self.level = 0.0
        self.on = False
        self.anim = QVariantAnimation(self)
        self.anim.valueChanged.connect(self._set)
        self._fx = {}

    def panels(self):
        from yamusic_theme import AmbientFrame
        return [f for f in self.shell.findChildren(AmbientFrame) if f.objectName() not in SKIP and f.isVisible()]

    def decor(self):
        return [w for w in (getattr(self.shell, "_side_head", None), getattr(self.shell, "artist_card", None))
                if isinstance(w, QWidget)]

    def set_on(self, on: bool):
        on = bool(on)
        if on == self.on:
            return
        self.on = on
        import motion
        if motion.strength() <= 0:                             # «минимум движения» — сразу
            self.anim.stop()
            self._set(1.0 if on else 0.0)
            return
        self.anim.stop()
        self.anim.setStartValue(float(self.level))
        self.anim.setEndValue(1.0 if on else 0.0)
        self.anim.setDuration(int((IN_MS if on else OUT_MS) * abs((1.0 if on else 0.0) - self.level)) or 1)
        self.anim.setEasingCurve(QEasingCurve.Type.InOutCubic if on else QEasingCurve.Type.OutCubic)
        self.anim.start()

    def _set(self, v):
        self.level = float(v)
        for f in self.panels():
            f.update()
        for w in self.decor():                                 # подписи-украшения растворяются
            vis = max(0.0, 1.0 - self.level * 1.6)
            fx = self._fx.get(id(w))
            if vis >= 0.999:
                if fx is not None:
                    w.setGraphicsEffect(None)
                    self._fx.pop(id(w), None)
                if not w.isVisible() and w.__dict__.get("_veil_hidden"):
                    w._veil_hidden = False
                    w.show()
                continue
            if vis <= 0.001:
                if w.isVisible():
                    w._veil_hidden = True
                    w.hide()
                continue
            if not w.isVisible() and w.__dict__.get("_veil_hidden"):
                w._veil_hidden = False
                w.show()
            if fx is None:
                fx = QGraphicsOpacityEffect(w)
                w.setGraphicsEffect(fx)
                self._fx[id(w)] = fx
            fx.setOpacity(vis)


def paint(frame, p: QPainter, radius: float):
    """Поверх стекла панели — темнота с фронтом от «Моей волны» и туман."""
    v = level()
    if v <= 0.001 or frame.objectName() in SKIP or _VEIL is None:
        return
    W, H = frame.width(), frame.height()
    shape = QPainterPath()
    shape.addRoundedRect(QRectF(0, 0, W, H), radius, radius)
    p.save()
    p.setClipPath(shape)
    # фронт темноты: круг из центра «Моей волны» растёт до самых дальних панелей
    content = getattr(_VEIL.shell, "content", None)
    win = frame.window()
    if content is not None:
        c = content.mapTo(win, QPointF(content.width() / 2, content.height() / 2).toPoint())
        centre = QPointF(frame.mapFrom(win, c))
    else:
        centre = QPointF(W / 2, H / 2)
    reach = (win.width() ** 2 + win.height() ** 2) ** 0.5
    front = min(1.0, v * 1.15)
    g = QRadialGradient(centre, reach)
    ink = QColor(0, 0, 0, int(255 * DARK))
    edge = max(0.0, front - 0.18)
    g.setColorAt(0.0, ink)
    g.setColorAt(edge, ink)
    if front < 1.0:
        try:                                                   # кромка фронта — акцентом темы
            import ya_decor
            rim = QColor(ya_decor.accent())
        except Exception:                                      # noqa: BLE001
            rim = QColor(140, 120, 255)
        rim.setAlpha(int(90 * (1.0 - v)))
        g.setColorAt(min(1.0, edge + 0.12), rim)
        g.setColorAt(min(1.0, front + 0.02), QColor(0, 0, 0, 0))
        g.setColorAt(1.0, QColor(0, 0, 0, 0))
    else:
        g.setColorAt(1.0, ink)
    p.fillRect(QRectF(0, 0, W, H), g)
    # туман: гуще и быстрее, пока идёт переход, потом — лёгкая дымка
    key = (W, H)
    fog = frame.__dict__.get("_veil_fog")
    if fog is None or fog[0] != key:
        fog = (key, _fog(W, H, hash(frame.objectName()) & 0xffff))
        frame._veil_fog = fog
    moving = 4.0 * v * (1.0 - v)                               # 0 в начале и в конце, 1 посередине
    p.setOpacity(min(1.0, 0.22 + 0.55 * moving) * v)
    dx = (1.0 - v) * 140.0 - 40.0
    p.drawPixmap(QPointF(dx, 0), fog[1])
    p.drawPixmap(QPointF(dx - W, 0), fog[1])
    p.restore()
