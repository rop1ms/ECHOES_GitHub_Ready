# wave_veil.py
"""
«Моя волна» во весь экран: пустота поглощает панели вокруг (верх, разделы, плеер, угловые блоки).

Входишь в «Мою волну» — от неё наружу по всем стеклянным панелям расходится абсолютная чернота (фронт с
мягким краем). Когда панели почти чёрные (VOID_AT), у кнопок в них пропадают фоны и рамки, а их значки
и текст становятся белыми — остаются только символы (плей в плеере — тоже: ya_decor.PlayButton смотрит
void()). Подписи-украшения («РАЗДЕЛЫ», карточка исполнителя) растворяются. Обложки плейлистов наверху —
картинки, а не символы, их не трогаем. Выходишь — кнопки сразу становятся как были, чернота уходит.

Рисует AmbientFrame.paintEvent (paint): поверх своего стекла — черноту этой панели.
"""
from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QObject, QPointF, QRectF, QSize, Qt, QVariantAnimation
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap, QRadialGradient
from PyQt6.QtWidgets import QAbstractButton, QGraphicsOpacityEffect, QWidget

IN_MS = 950
OUT_MS = 700
VOID_AT = 0.6                                                  # с этой черноты кнопки — только белые символы
SKIP = ("YaContent", "ListenerCard")                           # сама «Моя волна» и карточки настроек
KEEP_ICONS = ("PlaylistStrip",)                                # обложки — картинки, не перекрашивать
VOID_QSS = ("QAbstractButton{background:transparent;border:1px solid transparent;color:#ffffff;}"
            "QAbstractButton:hover{background:rgba(255,255,255,0.08);}"
            "QAbstractButton:checked{background:transparent;border:1px solid rgba(255,255,255,0.32);}")

_VEIL = None


def level() -> float:
    return _VEIL.level if _VEIL is not None else 0.0


def void() -> bool:
    """Кнопки в панелях сейчас — только белые символы на черноте."""
    return _VEIL is not None and _VEIL.voided


def install(shell):
    global _VEIL
    if _VEIL is None or _VEIL.shell is not shell:
        _VEIL = Veil(shell)
    return _VEIL


def _white(icon: QIcon, size: QSize, dpr: float) -> QIcon:
    """Тот же значок, но белый (форма — по прозрачности оригинала)."""
    pm = icon.pixmap(QSize(max(1, size.width()), max(1, size.height())), dpr)
    if pm.isNull():
        return icon
    out = QPixmap(pm.size())
    out.setDevicePixelRatio(pm.devicePixelRatio())
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.drawPixmap(0, 0, pm)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    p.fillRect(out.rect(), QColor(255, 255, 255))
    p.end()
    return QIcon(out)


class Veil(QObject):
    def __init__(self, shell):
        super().__init__(shell)
        self.shell = shell
        self.level = 0.0
        self.on = False
        self.voided = False
        self.anim = QVariantAnimation(self)
        self.anim.valueChanged.connect(self._set)
        self._fx = {}
        self._saved = {}                                       # id(кнопки) → (кнопка, стиль, значок, белый значок)

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
        if not on:
            self._void(False)                                  # выходим — кнопки сразу как были
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
        if self.on and self.level >= VOID_AT:
            self._void(True)
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

    def _buttons(self):
        for f in self.panels():
            for b in f.findChildren(QAbstractButton):
                if b.isVisible():
                    yield b

    def _void(self, on: bool):
        """Кнопки в панелях: без фона и рамки, значок и текст белые — или всё как было."""
        if on == self.voided:
            return
        self.voided = on
        if on:
            for b in self._buttons():
                if id(b) in self._saved:
                    continue
                icon, white = b.icon(), None
                parent = b.parentWidget()               # обложка плейлиста — картинка; «⋯» рядом — символ
                keep = parent is not None and type(parent).__name__ in KEEP_ICONS and not icon.isNull()
                if not icon.isNull() and not keep:
                    white = _white(icon, b.iconSize(), b.devicePixelRatioF())
                    b.setIcon(white)
                self._saved[id(b)] = (b, b.styleSheet(), icon, white)
                if not keep:
                    b.setStyleSheet(b.styleSheet() + VOID_QSS)
                b.update()
        else:
            for b, qss, icon, white in self._saved.values():
                try:
                    b.setStyleSheet(qss)
                    if white is not None and b.icon().cacheKey() == white.cacheKey():
                        b.setIcon(icon)                        # значок не меняли, пока была пустота
                    b.update()
                except RuntimeError:
                    pass
            self._saved.clear()
        for f in self.panels():
            f.update()


def paint(frame, p: QPainter, radius: float):
    """Поверх стекла панели — чернота, расходящаяся от «Моей волны»."""
    v = level()
    if v <= 0.001 or frame.objectName() in SKIP or _VEIL is None:
        return
    W, H = frame.width(), frame.height()
    shape = QPainterPath()
    shape.addRoundedRect(QRectF(0, 0, W, H), radius, radius)
    p.save()
    p.setClipPath(shape)
    if v >= 0.999:
        p.fillRect(QRectF(0, 0, W, H), QColor(0, 0, 0))
        p.restore()
        return
    content = getattr(_VEIL.shell, "content", None)
    win = frame.window()
    if content is not None:
        c = content.mapTo(win, QPointF(content.width() / 2, content.height() / 2).toPoint())
        centre = QPointF(frame.mapFrom(win, c))
    else:
        centre = QPointF(W / 2, H / 2)
    reach = (win.width() ** 2 + win.height() ** 2) ** 0.5
    front = min(1.0, v * 1.2)                                  # фронт пустоты растёт от «Моей волны»
    edge = max(0.0, front - 0.2)
    ink = QColor(0, 0, 0)
    g = QRadialGradient(centre, reach)
    g.setColorAt(0.0, ink)
    g.setColorAt(edge, ink)
    if front < 1.0:
        g.setColorAt(front, QColor(0, 0, 0, 0))
        g.setColorAt(1.0, QColor(0, 0, 0, 0))
    else:
        g.setColorAt(1.0, ink)
    p.fillRect(QRectF(0, 0, W, H), g)
    p.restore()
