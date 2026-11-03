# wave_veil.py
"""
«Моя волна» во весь экран: волна заполняет весь интерфейс, ничто её не обрезает.

Под всем окном лежит своя туманность на весь размер окна (backdrop, NebulaGL из echoes_ui.dll) — с теми же
параметрами, что у страницы, только центр сдвинут на место страницы в окне, поэтому она продолжает
туманность страницы без шва. Входишь в «Мою волну» — от неё наружу расходится фронт, и внутри него
стекло панелей (верх, разделы, плеер, угловые блоки, заголовок окна) стирается до прозрачности: волна
разливается по всему окну, где её нет — чернота. Когда фронт почти дошёл (VOID_AT), у кнопок пропадают
фоны и рамки, значки и текст белеют — остаются только символы (плей в плеере — тоже: ya_decor.PlayButton
смотрит void()). Подписи-украшения («РАЗДЕЛЫ», карточка исполнителя) растворяются, обложки плейлистов —
картинки, их не трогаем. Выходишь — кнопки сразу как были, фронт сходится обратно, стекло возвращается.

Стирает AmbientFrame.paintEvent и TitleBar.paintEvent (paint): DestinationOut по своему стеклу.
"""
from __future__ import annotations

import ctypes

from PyQt6.QtCore import QEasingCurve, QEvent, QObject, QPointF, QRectF, QSize, Qt, QVariantAnimation
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap, QRadialGradient
from PyQt6.QtWidgets import QAbstractButton, QGraphicsOpacityEffect, QWidget

IN_MS = 950
OUT_MS = 700
VOID_AT = 0.6                                                  # с этой черноты кнопки — только белые символы
SKIP = ("ListenerCard",)                                       # карточки настроек — не панели окна
NO_VOID = ("YaContent",)                                       # кнопки самой «Моей волны» — как есть
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


def feed(page, P, burst: float, scale: float):
    """Кадр туманности страницы → та же туманность на всё окно (центр сдвинут на место страницы)."""
    v = _VEIL
    if v is None or v.level <= 0.001 or v.backdrop is None:
        return
    try:
        bd = v.backdrop
        if not bd.ok():
            return
        win = v.shell.window()
        o = page.mapTo(win, page.rect().topLeft())
        dpr = page.devicePixelRatioF()
        P2 = type(P)()
        ctypes.memmove(ctypes.byref(P2), ctypes.byref(P), ctypes.sizeof(P))
        P2.cx += o.x() * dpr
        P2.cy += o.y() * dpr
        P2.W, P2.H = int(win.width() * dpr), int(win.height() * dpr)
        P2.gain = P.gain * min(1.0, v.level * 1.3)            # снаружи страницы волна разгорается с фронтом
        bd.scale(scale)
        bd.burst(burst)
        bd.params(P2)
    except RuntimeError:
        pass


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
        self.backdrop = None                                   # туманность на всё окно (под всем)
        self._panels = None                                    # панели окна (на время перехода)
        self._root_qss = None                                  # стиль Root до прозрачности
        self._amb_hidden = False

    def panels(self):
        """Панели окна — список один на переход (findChildren по всему окну на каждом кадре стоил секунды)."""
        if self._panels is None:
            from yamusic_theme import AmbientFrame
            out = [f for f in AmbientFrame.live(self.shell) if f.objectName() not in SKIP]
            tb = getattr(self.shell.window(), "titlebar", None)
            if tb is not None:
                out.append(tb)
            self._panels = out
        alive = []
        for f in self._panels:
            try:
                if f.isVisible():
                    alive.append(f)
            except RuntimeError:
                pass
        return alive

    # ── туманность под всем окном ──
    def _ensure_backdrop(self):
        if self.backdrop is not None:
            return
        try:
            import ya_wave
            win = self.shell.window()
            anchor = getattr(win, "_gpu_anchor", None)
            if not ya_wave.gpu_enabled() or anchor is None or not anchor.ok():
                return
            import native_ui
            bd = native_ui.NebulaGL()
            bd.widget.setParent(win)
            bd.widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            bd.widget.setGeometry(win.rect())
            bd.widget.lower()
            bd.widget.hide()
            self.backdrop = bd
            win.installEventFilter(self)
        except Exception as e:                                 # noqa: BLE001
            print("[veil] backdrop:", e)
            self.backdrop = None

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.Resize and self.backdrop is not None and obj is self.shell.window():
            self.backdrop.widget.setGeometry(obj.rect())
        return False

    def _window_layers(self, on: bool):
        """Фон Root прозрачен (между панелями видно волну), «дыхание» фона спрятано — или как было."""
        win = self.shell.window()
        root = win.centralWidget() if hasattr(win, "centralWidget") else None
        if on:
            if self.backdrop is not None and not self.backdrop.widget.isVisible():
                self.backdrop.widget.setGeometry(win.rect())
                self.backdrop.widget.lower()
                self.backdrop.widget.show()
            if root is not None and self._root_qss is None:
                # фон Root — из стиля; не менять стиль (это перестиливает ВСЁ окно — секунды), а просто
                # не рисовать его: атрибут, без перестилизации
                self._root_qss = root.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)
                root.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
                root.update()
            amb = getattr(win, "_ambience", None)
            if amb is not None and amb.isVisible() and not self._amb_hidden:
                self._amb_hidden = True
                amb.hide()
        else:
            if root is not None and self._root_qss is not None:
                root.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, bool(self._root_qss))
                self._root_qss = None
                root.update()
            amb = getattr(win, "_ambience", None)
            if amb is not None and self._amb_hidden:
                self._amb_hidden = False
                amb.show()
            if self.backdrop is not None:
                self.backdrop.widget.hide()

    def decor(self):
        return [w for w in (getattr(self.shell, "_side_head", None), getattr(self.shell, "artist_card", None))
                if isinstance(w, QWidget)]

    def set_on(self, on: bool):
        on = bool(on)
        if on == self.on:
            return
        self.on = on
        self._panels = None                                    # раскладка могла смениться — собрать заново
        if on:
            self._ensure_backdrop()
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
        self._window_layers(self.level > 0.001)
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
            if f.objectName() in NO_VOID:
                continue
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
    """Стекло панели стирается до прозрачности внутри фронта, расходящегося от «Моей волны» —
    под ним видно туманность на всё окно (или черноту)."""
    v = level()
    if v <= 0.001 or frame.objectName() in SKIP or _VEIL is None:
        return
    W, H = frame.width(), frame.height()
    p.save()
    if v >= 0.999:
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        p.fillRect(QRectF(0, 0, W, H), Qt.GlobalColor.transparent)
        p.restore()
        return
    content = getattr(_VEIL.shell, "content", None)
    win = frame.window()
    if content is not None:
        c = content.mapTo(win, QPointF(content.width() * 0.62, content.height() / 2).toPoint())
        centre = QPointF(frame.mapFrom(win, c))
    else:
        centre = QPointF(W / 2, H / 2)
    reach = (win.width() ** 2 + win.height() ** 2) ** 0.5
    front = min(1.0, v * 1.2)                                  # фронт растёт от «Моей волны» наружу
    edge = max(0.0, front - 0.22)
    ink = QColor(0, 0, 0)
    g = QRadialGradient(centre, reach)
    g.setColorAt(0.0, ink)
    g.setColorAt(edge, ink)
    if front < 1.0:
        g.setColorAt(front, QColor(0, 0, 0, 0))
        g.setColorAt(1.0, QColor(0, 0, 0, 0))
    else:
        g.setColorAt(1.0, ink)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut)
    p.fillRect(QRectF(0, 0, W, H), g)
    p.restore()
