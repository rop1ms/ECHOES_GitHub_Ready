# osu_skin.py
"""
Скин темы «esu!»: все элементы рисуются кодом и кэшируются в QPixmap — во время игры на кадр
остаются только drawPixmap. Плюс синтез хитсаундов (numpy, 48 кГц).
Если выбран скин из файлов osu! (ACTIVE, osu_skin_import), make_skin() отдаёт его FileSkin —
с теми же методами; чего в скине нет, рисуется отсюда.
"""
from __future__ import annotations

import math

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QPolygonF,
                         QRadialGradient)

COMBO_COLORS = [QColor(255, 192, 0), QColor(0, 202, 0), QColor(18, 124, 255), QColor(242, 24, 57)]
PINK = QColor(255, 102, 170)

# Палитры цветов комбо (классический стиль): ключ → (название, цвета). Цвет меняется с каждым новым комбо.
PALETTES = {
    "bright": ("Яркие", [(255, 94, 98), (255, 157, 66), (255, 211, 64), (122, 214, 92), (46, 204, 170),
                         (64, 170, 255), (128, 120, 255), (222, 102, 230)]),
    "rainbow": ("Радуга", [(255, 72, 72), (255, 128, 48), (255, 186, 40), (226, 226, 56), (140, 222, 64),
                           (56, 210, 120), (40, 204, 200), (48, 160, 255), (82, 108, 255), (146, 92, 255),
                           (206, 84, 240), (255, 84, 170)]),
    "neon": ("Неон", [(255, 46, 136), (0, 238, 255), (178, 255, 46), (255, 230, 0), (170, 70, 255),
                      (255, 120, 30), (40, 255, 160), (90, 140, 255)]),
    "pastel": ("Пастель", [(255, 170, 180), (255, 204, 160), (250, 232, 150), (186, 232, 168), (160, 226, 214),
                           (164, 200, 255), (198, 180, 255), (240, 176, 230)]),
    "sunset": ("Закат", [(255, 99, 72), (255, 145, 77), (255, 196, 92), (240, 98, 146), (186, 85, 211),
                         (120, 90, 220)]),
    "ocean": ("Океан", [(0, 180, 216), (72, 202, 228), (0, 150, 199), (144, 224, 239), (46, 196, 182),
                        (90, 130, 255)]),
    "classic": ("osu! (4 цвета)", [(255, 192, 0), (0, 202, 0), (18, 124, 255), (242, 24, 57)]),
}
DEFAULT_PALETTE = "classic"
DARK_NUMBERS = True                     # тёмная цифра на светлых кругах (стиль Y2K выключает — там стекло)
SKIN_PALETTE = "skin"                   # «цвета из скина osu!» (Combo1…8 из skin.ini активного скина)

# Вид нот встроенного скина: "osu" — как стандартный скин osu! (круг с серебристым ободом, белые цифры,
# оранжевый круг слежения, плоский шар слайдера — без «3D-шара»), "minimal" — плоский минимализм esu!.
LOOKS = {"osu": "Как в osu! (стандартный скин)", "minimal": "Минимализм esu!"}
LOOK = "osu"
SKIN_BALL = False                       # шар слайдера из скина osu! (там он часто объёмный, «3D») — по желанию


def set_look(name):
    global LOOK
    LOOK = name if name in LOOKS else "osu"

# Активный скин osu! (osu_skin_import.SkinFiles) или None — нарисованный esu!.
ACTIVE = None


def palette_colors(name) -> list:
    if name == SKIN_PALETTE:
        if ACTIVE is not None and ACTIVE.combo:
            return [QColor(c) for c in ACTIVE.combo]
        name = DEFAULT_PALETTE
    return [QColor(*c) for c in PALETTES.get(name, PALETTES[DEFAULT_PALETTE])[1]]


def set_palette(name):
    """Цвета комбо для новых скинов (Skin() без своих цветов берёт COMBO_COLORS)."""
    global COMBO_COLORS
    COMBO_COLORS = palette_colors(name)


def make_skin(R: float, dpr: float = 1.0, colors=None, ps: float | None = None, map_folder=None):
    """Скин нот под радиус R: из файлов активного скина osu!, если он выбран, иначе нарисованный.
    ps — экранных пикселей на пиксель osu! (для того, что в osu! не зависит от размера кругов).
    map_folder — папка карты osu!: её собственный скин (картинки нот рядом с .osu) — поверх выбранного."""
    if ACTIVE is not None or map_folder:
        try:
            import osu_skin_import as SI
            files = ACTIVE
            mf = SI.map_files(map_folder) if map_folder else None
            if mf is not None:
                files = SI.ChainFiles(mf, ACTIVE)
            if files is not None:
                return SI.FileSkin(files, R, dpr, colors, ps)
        except Exception as e:                             # noqa: BLE001
            print("[esu skin]", e)
    return Skin(R, dpr, colors)


def is_light(c: QColor) -> bool:
    """Светлый цвет — на нём белая цифра теряется, нужна тёмная."""
    return 0.2126 * c.redF() + 0.7152 * c.greenF() + 0.0722 * c.blueF() > 0.74
FONT = "Segoe UI"

J_COLORS = {300: QColor(80, 190, 255), 100: QColor(110, 230, 70), 50: QColor(255, 196, 64), 0: QColor(255, 40, 40)}


def _pm(w, h, dpr=1.0) -> QPixmap:
    pm = QPixmap(max(1, int(math.ceil(w * dpr))), max(1, int(math.ceil(h * dpr))))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    return pm


def _painter(pm) -> QPainter:
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    return p


try:                                                       # C++-ядро (echoes_fx.dll): тела слайдеров
    import native_fx as _NF
    _NATIVE_BODY = _NF.available()
except Exception:                                          # noqa: BLE001
    _NF, _NATIVE_BODY = None, False


def native_bodies() -> bool:
    """Тела слайдеров — полем расстояний в C++ (одно прохождение вместо 10–26 обводок)."""
    if not _NATIVE_BODY:
        return False
    try:
        import osu_game
        return osu_game.NATIVE_FRAMES[0]
    except Exception:                                      # noqa: BLE001
        return True


def _rgbf(c: QColor):
    return (c.redF(), c.greenF(), c.blueF())


def body_pixmap(pts, x0, y0, w, h, R, dpr, prof):
    """Картинка тела слайдера из C++ (те же размеры и угол, что у QPainter-версии)."""
    W, H = max(1, int(math.ceil(w * dpr))), max(1, int(math.ceil(h * dpr)))
    p = np.asarray(pts, np.float32)
    dev = np.empty_like(p)
    dev[:, 0] = (p[:, 0] - x0) * dpr
    dev[:, 1] = (p[:, 1] - y0) * dpr
    img = _NF.slider_body_image(dev, W, H, R * dpr, prof)
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(dpr)
    return pm, QPointF(x0, y0)


def lighter(c: QColor, k: float) -> QColor:
    return QColor(min(255, int(c.red() + (255 - c.red()) * k)), min(255, int(c.green() + (255 - c.green()) * k)),
                  min(255, int(c.blue() + (255 - c.blue()) * k)), c.alpha())


def darker(c: QColor, k: float) -> QColor:
    return QColor(int(c.red() * (1 - k)), int(c.green() * (1 - k)), int(c.blue() * (1 - k)), c.alpha())


def text_path(text: str, size: float, weight=QFont.Weight.Black, family=FONT, italic=False) -> QPainterPath:
    f = QFont(family)
    f.setPixelSize(max(1, int(size)))
    f.setWeight(weight)
    f.setItalic(italic)
    path = QPainterPath()
    path.addText(0, 0, f, text)
    return path


def outlined_text(text: str, size: float, fill, outline=QColor(0, 0, 0, 200), ow=None, dpr=1.0, glow: QColor | None = None,
                  weight=QFont.Weight.Black, italic=False, grad: tuple | None = None) -> QPixmap:
    path = text_path(text, size, weight, italic=italic)
    br = path.boundingRect()
    ow = size * 0.09 if ow is None else ow
    pad = ow * 2 + (size * 0.35 if glow is not None else 2)
    pm = _pm(br.width() + pad * 2, br.height() + pad * 2, dpr)
    if glow is not None:
        # мягкое свечение: силуэт текста, размытый гауссом
        sil = _pm(br.width() + pad * 2, br.height() + pad * 2, 1.0)
        q = _painter(sil)
        q.translate(pad - br.left(), pad - br.top())
        q.strokePath(path, QPen(glow, ow * 2 + size * 0.12, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                                Qt.PenJoinStyle.RoundJoin))
        q.fillPath(path, glow)
        q.end()
        import blur_fx                                     # гауссово свечение — мягкое, без «квадратов»
        blur = blur_fx.blur_image(sil.toImage(), max(1.5, size / 14.0))
    p = _painter(pm)
    if glow is not None:
        p.drawImage(QRectF(0, 0, pm.width() / dpr, pm.height() / dpr), blur)
        p.drawImage(QRectF(0, 0, pm.width() / dpr, pm.height() / dpr), blur)
    p.translate(pad - br.left(), pad - br.top())
    if ow > 0:
        p.strokePath(path, QPen(outline, ow * 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    if grad:
        lg = QLinearGradient(0, br.top(), 0, br.bottom())
        lg.setColorAt(0, grad[0])
        lg.setColorAt(1, grad[1])
        p.fillPath(path, QBrush(lg))
    else:
        p.fillPath(path, fill)
    p.end()
    return pm


def glow_dot(r: float, color: QColor, dpr=1.0, core=0.25) -> QPixmap:
    pm = _pm(r * 2, r * 2, dpr)
    p = _painter(pm)
    g = QRadialGradient(r, r, r)
    c0 = QColor(color)
    g.setColorAt(0, c0)
    c1 = QColor(color)
    c1.setAlpha(int(color.alpha() * 0.55))
    g.setColorAt(core, c1)
    c2 = QColor(color)
    c2.setAlpha(0)
    g.setColorAt(1, c2)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(g))
    p.drawEllipse(QRectF(0, 0, r * 2, r * 2))
    p.end()
    return pm


def star_shape(cx, cy, r_out, r_in, n=4, rot=0.0) -> QPolygonF:
    pts = []
    for i in range(n * 2):
        a = rot + math.pi * i / n
        r = r_out if i % 2 == 0 else r_in
        pts.append(QPointF(cx + math.cos(a) * r, cy + math.sin(a) * r))
    return QPolygonF(pts)


def draw_icon(p: QPainter, kind: str, r: QRectF, color: QColor):
    """Векторные значки (играть, пауза, назад/вперёд, домой, настройки, музыка, темы).
    Символы шрифта ⚙ ⏮ ⏭ ⏸ на Windows рисуются цветными эмодзи — поэтому рисуем сами."""
    u = min(r.width(), r.height()) / 2
    cx, cy = r.center().x(), r.center().y()
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.OddEvenFill)

    def tri(x0, x1, h):
        path.moveTo(x0, cy - h)
        path.lineTo(x1, cy)
        path.lineTo(x0, cy + h)
        path.closeSubpath()
    if kind == "play":
        tri(cx - u * 0.42, cx + u * 0.58, u * 0.6)
    elif kind == "pause":
        w, h = u * 0.26, u * 1.1
        path.addRoundedRect(QRectF(cx - u * 0.42, cy - h / 2, w, h), w * 0.25, w * 0.25)
        path.addRoundedRect(QRectF(cx + u * 0.16, cy - h / 2, w, h), w * 0.25, w * 0.25)
    elif kind in ("next", "prev"):
        s = 1 if kind == "next" else -1
        h = u * 0.5
        tri(cx - s * u * 0.62, cx + s * u * 0.02, h)
        tri(cx - s * u * 0.08, cx + s * u * 0.52, h)
        bw = u * 0.14
        bx = cx + s * u * 0.52 - (0 if s > 0 else bw)
        path.addRect(QRectF(bx, cy - h, bw, 2 * h))
    elif kind == "home":
        path.moveTo(cx, cy - u * 0.72)
        path.lineTo(cx + u * 0.72, cy - u * 0.04)
        path.lineTo(cx + u * 0.5, cy - u * 0.04)
        path.lineTo(cx + u * 0.5, cy + u * 0.62)
        path.lineTo(cx - u * 0.5, cy + u * 0.62)
        path.lineTo(cx - u * 0.5, cy - u * 0.04)
        path.lineTo(cx - u * 0.72, cy - u * 0.04)
        path.closeSubpath()
        path.addRect(QRectF(cx - u * 0.14, cy + u * 0.18, u * 0.28, u * 0.44))     # дверь — дырка
    elif kind == "settings":
        teeth, ro, ri = 8, u * 0.72, u * 0.56
        pts = []
        for i in range(teeth * 4):
            a = 2 * math.pi * (i - 0.5) / (teeth * 4)
            rr = ro if (i % 4) in (0, 1) else ri
            pts.append(QPointF(cx + math.cos(a) * rr, cy + math.sin(a) * rr))
        path.addPolygon(QPolygonF(pts))
        path.closeSubpath()
        path.addEllipse(QPointF(cx, cy), u * 0.24, u * 0.24)
    elif kind == "music":
        path.addEllipse(QPointF(cx - u * 0.22, cy + u * 0.42), u * 0.26, u * 0.2)
        path.addRect(QRectF(cx - u * 0.01, cy - u * 0.62, u * 0.12, u * 1.04))
        path.moveTo(cx + u * 0.11, cy - u * 0.62)
        path.cubicTo(cx + u * 0.3, cy - u * 0.36, cx + u * 0.62, cy - u * 0.3, cx + u * 0.42, cy + u * 0.06)
        path.cubicTo(cx + u * 0.46, cy - u * 0.2, cx + u * 0.3, cy - u * 0.26, cx + u * 0.11, cy - u * 0.36)
        path.closeSubpath()
        path.setFillRule(Qt.FillRule.WindingFill)
    elif kind == "themes":
        path.addEllipse(QPointF(cx, cy), u * 0.72, u * 0.62)                        # палитра
        path.addEllipse(QPointF(cx + u * 0.3, cy + u * 0.24), u * 0.14, u * 0.12)  # отверстие для пальца
        for dx, dy in ((-0.36, -0.12), (-0.08, -0.34), (0.26, -0.24)):              # краски — тоже дырки
            path.addEllipse(QPointF(cx + u * dx, cy + u * dy), u * 0.11, u * 0.11)
    elif kind == "edit":                                                             # карандаш
        path.moveTo(cx - u * 0.62, cy + u * 0.62)
        path.lineTo(cx - u * 0.52, cy + u * 0.24)
        path.lineTo(cx + u * 0.32, cy - u * 0.6)
        path.lineTo(cx + u * 0.62, cy - u * 0.3)
        path.lineTo(cx - u * 0.22, cy + u * 0.54)
        path.closeSubpath()
    elif kind == "exit":                                                             # дверь со стрелкой
        path.addRect(QRectF(cx - u * 0.62, cy - u * 0.66, u * 0.16, u * 1.32))
        path.addRect(QRectF(cx - u * 0.62, cy - u * 0.66, u * 0.6, u * 0.14))
        path.addRect(QRectF(cx - u * 0.62, cy + u * 0.52, u * 0.6, u * 0.14))
        path.addRect(QRectF(cx - u * 0.18, cy - u * 0.09, u * 0.5, u * 0.18))
        tri(cx + u * 0.3, cx + u * 0.68, u * 0.3)
        path.setFillRule(Qt.FillRule.WindingFill)
    elif kind == "solo":                                                             # человек
        path.addEllipse(QPointF(cx, cy - u * 0.32), u * 0.27, u * 0.27)
        path.moveTo(cx - u * 0.56, cy + u * 0.66)
        path.cubicTo(cx - u * 0.56, cy + u * 0.05, cx + u * 0.56, cy + u * 0.05, cx + u * 0.56, cy + u * 0.66)
        path.closeSubpath()
        path.setFillRule(Qt.FillRule.WindingFill)
    elif kind == "import":                                                           # стрелка вниз в лоток
        path.addRect(QRectF(cx - u * 0.1, cy - u * 0.66, u * 0.2, u * 0.62))
        path.moveTo(cx - u * 0.36, cy - u * 0.12)
        path.lineTo(cx + u * 0.36, cy - u * 0.12)
        path.lineTo(cx, cy + u * 0.26)
        path.closeSubpath()
        path.addRect(QRectF(cx - u * 0.62, cy + u * 0.42, u * 1.24, u * 0.16))
        path.addRect(QRectF(cx - u * 0.62, cy + u * 0.14, u * 0.16, u * 0.44))
        path.addRect(QRectF(cx + u * 0.46, cy + u * 0.14, u * 0.16, u * 0.44))
        path.setFillRule(Qt.FillRule.WindingFill)
    elif kind == "back":                                                             # стрелка влево
        path.moveTo(cx - u * 0.62, cy)
        path.lineTo(cx - u * 0.1, cy - u * 0.52)
        path.lineTo(cx - u * 0.1, cy - u * 0.16)
        path.lineTo(cx + u * 0.62, cy - u * 0.16)
        path.lineTo(cx + u * 0.62, cy + u * 0.16)
        path.lineTo(cx - u * 0.1, cy + u * 0.16)
        path.lineTo(cx - u * 0.1, cy + u * 0.52)
        path.closeSubpath()
    p.drawPath(path)
    p.restore()


class Skin:
    """Спрайты под размер круга R (px) и плотность пикселей dpr."""

    def __init__(self, R: float, dpr: float = 1.0, colors=None):
        self.R = max(6.0, float(R))
        self.dpr = dpr
        self.colors = list(colors or COMBO_COLORS)
        self._c = {}

    def _get(self, key, fn):
        v = self._c.get(key)
        if v is None:
            v = fn()
            self._c[key] = v
        return v

    def color(self, i) -> QColor:
        return self.colors[i % len(self.colors)]

    # ── круги ── #
    def circle(self, ci) -> QPixmap:
        return self._get(("circle", ci % len(self.colors)), lambda: self._circle(self.color(ci)))

    def _circle(self, col: QColor) -> QPixmap:
        """Минималистичный круг: ровная заливка цветом комбо (лёгкий перелив сверху вниз), тонкое
        белое кольцо и мягкая тень — без бликов и тёмных ободков, цифра читается сразу."""
        if LOOK == "osu":
            return self._circle_osu(col)
        R = self.R
        pad = R * 0.12
        s = R * 2 + pad * 2
        pm = _pm(s, s, self.dpr)
        p = _painter(pm)
        c = QPointF(s / 2, s / 2)
        sh = QRadialGradient(QPointF(c.x(), c.y() + R * 0.04), R * 1.08)
        sh.setColorAt(0.84, QColor(0, 0, 0, 70))
        sh.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(sh))
        p.drawEllipse(c, R * 1.08, R * 1.08)
        g = QLinearGradient(0, c.y() - R, 0, c.y() + R)
        g.setColorAt(0, lighter(col, 0.10))
        g.setColorAt(1, darker(col, 0.12))
        p.setBrush(QBrush(g))
        p.drawEllipse(c, R * 0.93, R * 0.93)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255), R * 0.08))
        p.drawEllipse(c, R * 0.94, R * 0.94)
        p.end()
        return pm

    def _circle_osu(self, col: QColor) -> QPixmap:
        """Круг как в стандартном скине osu!: тень, заливка цветом комбо (светлее сверху, темнее к краю),
        широкий серебристо-белый обод с тонкими тёмными кантами."""
        R = self.R
        pad = R * 0.14
        s = R * 2 + pad * 2
        pm = _pm(s, s, self.dpr)
        p = _painter(pm)
        c = QPointF(s / 2, s / 2)
        sh = QRadialGradient(QPointF(c.x(), c.y() + R * 0.05), R * 1.12)
        sh.setColorAt(0.80, QColor(0, 0, 0, 110))
        sh.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(sh))
        p.drawEllipse(c, R * 1.12, R * 1.12)
        g = QRadialGradient(QPointF(c.x(), c.y() - R * 0.35), R * 1.15)
        g.setColorAt(0.0, lighter(col, 0.22))
        g.setColorAt(0.6, col)
        g.setColorAt(1.0, darker(col, 0.32))
        p.setBrush(QBrush(g))
        p.drawEllipse(c, R * 0.88, R * 0.88)
        ring = QLinearGradient(0, c.y() - R, 0, c.y() + R)
        ring.setColorAt(0.0, QColor(255, 255, 255))
        ring.setColorAt(1.0, QColor(196, 198, 206))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QBrush(ring), R * 0.12))
        p.drawEllipse(c, R * 0.925, R * 0.925)
        p.setPen(QPen(QColor(0, 0, 0, 70), max(1.0, R * 0.018)))
        p.drawEllipse(c, R * 0.862, R * 0.862)
        p.setPen(QPen(QColor(0, 0, 0, 110), max(1.0, R * 0.02)))
        p.drawEllipse(c, R * 0.99, R * 0.99)
        p.end()
        return pm

    def approach(self, ci) -> QPixmap:
        def mk():
            R = self.R * 1.0
            s = R * 2 + 8
            pm = _pm(s, s, self.dpr)
            p = _painter(pm)
            if LOOK == "osu":                          # как approachcircle osu!: цвет комбо, толще
                col = lighter(self.color(ci), 0.1)
                p.setPen(QPen(col, max(2.0, R * 0.085)))
            else:
                col = lighter(self.color(ci), 0.2)
                p.setPen(QPen(col, max(1.6, R * 0.065)))
            p.drawEllipse(QPointF(s / 2, s / 2), R * 0.96, R * 0.96)
            p.end()
            return pm
        return self._get(("approach", ci % len(self.colors)), mk)

    def number(self, n: int, ci=None) -> QPixmap:
        """Цифра комбо: белая с лёгкой тенью; на светлых цветах (жёлтый, пастель) — тёмная."""
        if LOOK == "osu":                              # в osu! цифры всегда белые, тонкие, с мягкой тенью
            return self._get(("num", n, 2), lambda: outlined_text(str(n), self.R * 0.92, QColor(255, 255, 255),
                                                                   QColor(0, 0, 0, 60), self.R * 0.035, self.dpr,
                                                                   weight=QFont.Weight.Normal))
        dark = DARK_NUMBERS and ci is not None and is_light(self.color(ci))
        if dark:
            return self._get(("num", n, 1), lambda: outlined_text(str(n), self.R * 0.8, QColor(28, 30, 38),
                                                                   QColor(255, 255, 255, 0), 0, self.dpr,
                                                                   weight=QFont.Weight.DemiBold))
        return self._get(("num", n, 0), lambda: outlined_text(str(n), self.R * 0.8, QColor(255, 255, 255),
                                                               QColor(0, 0, 0, 80), self.R * 0.03, self.dpr,
                                                               weight=QFont.Weight.DemiBold))

    def white_circle(self) -> QPixmap:
        def mk():
            R = self.R
            pm = _pm(R * 2, R * 2, self.dpr)
            p = _painter(pm)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255))
            p.drawEllipse(QPointF(R, R), R * 0.98, R * 0.98)
            p.end()
            return pm
        return self._get("white", mk)

    def hit_light(self, ci) -> QPixmap:
        return self._get(("light", ci % len(self.colors)), lambda: glow_dot(self.R * 2.2, lighter(self.color(ci), 0.3),
                                                                             self.dpr, 0.3))

    # ── слайдеры ── #
    def slider_body(self, pts: np.ndarray, ci, cheap=False) -> tuple[QPixmap, QPointF]:
        """Тело слайдера по точкам (px). Возвращает картинку и её левый верхний угол."""
        R = self.R
        col = self.color(ci)
        x0, y0 = float(pts[:, 0].min()) - R - 2, float(pts[:, 1].min()) - R - 2
        w, h = float(pts[:, 0].max()) - x0 + R + 2, float(pts[:, 1].max()) - y0 + R + 2
        if native_bodies():
            if LOOK == "osu":
                outer = darker(col, 0.1)
                inner = QColor(min(255, int(col.red() * 1.125 + 64)), min(255, int(col.green() * 1.125 + 64)),
                               min(255, int(col.blue() * 1.125 + 64)))
                prof = _NF.body_profile(70 / 255, (1.0, 1.0, 1.0, 1.0), 0.92, _rgbf(outer), _rgbf(inner),
                                        185 / 255, 185 / 255, 0.80, 0.9)
            else:
                edge, mid = darker(col, 0.55), lighter(col, 0.12)
                prof = _NF.body_profile(0.0, (1.0, 1.0, 1.0, 1.0), 0.98, _rgbf(edge), _rgbf(mid),
                                        200 / 255, 225 / 255, 0.86, 0.82)
            return body_pixmap(pts, x0, y0, w, h, R, self.dpr, prof)
        pm = _pm(w, h, self.dpr)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath(QPointF(pts[0, 0] - x0, pts[0, 1] - y0))
        step = max(1, len(pts) // 160)
        for q in pts[step::step]:
            path.lineTo(QPointF(q[0] - x0, q[1] - y0))
        if len(pts) > 1:
            path.lineTo(QPointF(pts[-1, 0] - x0, pts[-1, 1] - y0))
        cap, join = Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin
        if LOOK == "osu":
            # как в osu!stable: тень, белая рамка, внутри — полупрозрачный градиент цвета комбо
            # от чуть затемнённого края к светлой середине
            p.strokePath(path, QPen(QColor(0, 0, 0, 70), R * 2, Qt.PenStyle.SolidLine, cap, join))
            p.strokePath(path, QPen(QColor(255, 255, 255), R * 2 * 0.92, Qt.PenStyle.SolidLine, cap, join))
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            outer = darker(col, 0.1)
            inner = QColor(min(255, int(col.red() * 1.125 + 64)), min(255, int(col.green() * 1.125 + 64)),
                           min(255, int(col.blue() * 1.125 + 64)))
            layers = 8 if cheap else 24
            for i in range(layers):
                k = i / max(1, layers - 1)
                c = QColor(int(outer.red() + (inner.red() - outer.red()) * k),
                           int(outer.green() + (inner.green() - outer.green()) * k),
                           int(outer.blue() + (inner.blue() - outer.blue()) * k), 185)
                wd = R * 2 * 0.80 * (1 - 0.9 * k)
                p.setRenderHint(QPainter.RenderHint.Antialiasing, i == 0)
                p.strokePath(path, QPen(c, max(1.0, wd), Qt.PenStyle.SolidLine, cap, join))
            p.end()
            return pm, QPointF(x0, y0)
        p.strokePath(path, QPen(QColor(255, 255, 255), R * 2 * 0.98, Qt.PenStyle.SolidLine, cap, join))
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        layers = 8 if cheap else 16
        edge = darker(col, 0.55)
        mid = lighter(col, 0.12)
        for i in range(layers):
            k = i / max(1, layers - 1)
            c = QColor(int(edge.red() + (mid.red() - edge.red()) * k), int(edge.green() + (mid.green() - edge.green()) * k),
                       int(edge.blue() + (mid.blue() - edge.blue()) * k), int(200 + 25 * k))
            wd = R * 2 * 0.86 * (1 - 0.82 * k)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, i == 0)       # сглаживать нужно только край
            p.strokePath(path, QPen(c, max(1.0, wd), Qt.PenStyle.SolidLine, cap, join))
        p.end()
        return pm, QPointF(x0, y0)

    def slider_ball(self, ci) -> QPixmap:
        if LOOK == "osu":
            return self._get(("ball_osu", ci % len(self.colors)), lambda: self._flat_ball(self.color(ci)))

        def mk():
            R = self.R * 0.9
            s = R * 2 + 4
            pm = _pm(s, s, self.dpr)
            p = _painter(pm)
            c = QPointF(s / 2, s / 2)
            col = self.color(ci)
            g = QRadialGradient(QPointF(c.x() - R * 0.3, c.y() - R * 0.3), R * 1.3)
            g.setColorAt(0, lighter(col, 0.6))
            g.setColorAt(0.5, col)
            g.setColorAt(1, darker(col, 0.5))
            p.setPen(QPen(QColor(255, 255, 255), R * 0.1))
            p.setBrush(QBrush(g))
            p.drawEllipse(c, R * 0.92, R * 0.92)
            # «шов» — видно вращение
            p.setPen(QPen(QColor(255, 255, 255, 120), R * 0.08))
            p.drawArc(QRectF(c.x() - R * 0.6, c.y() - R * 0.6, R * 1.2, R * 1.2), 30 * 16, 120 * 16)
            p.end()
            return pm
        return self._get(("ball", ci % len(self.colors)), mk)

    def _flat_ball(self, col: QColor) -> QPixmap:
        """Плоский шар слайдера (без объёма и вращающегося «3D-шара»): цвет комбо и белое кольцо."""
        R = self.R * 0.86
        s = R * 2 + 6
        pm = _pm(s, s, self.dpr)
        p = _painter(pm)
        c = QPointF(s / 2, s / 2)
        p.setPen(QPen(QColor(0, 0, 0, 90), R * 0.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(c, R * 0.95, R * 0.95)
        p.setPen(QPen(QColor(255, 255, 255), R * 0.12))
        p.setBrush(lighter(col, 0.12))
        p.drawEllipse(c, R * 0.9, R * 0.9)
        p.end()
        return pm

    def follow_circle(self) -> QPixmap:
        if LOOK == "osu":
            def mk_osu():
                R = self.R * 2.2
                s = R * 2 + 8
                pm = _pm(s, s, self.dpr)
                p = _painter(pm)
                c = QPointF(s / 2, s / 2)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor(120, 60, 0, 90), max(3.0, self.R * 0.2)))
                p.drawEllipse(c, R * 0.95, R * 0.95)
                p.setPen(QPen(QColor(255, 182, 20), max(2.0, self.R * 0.13)))
                p.drawEllipse(c, R * 0.95, R * 0.95)
                p.end()
                return pm
            return self._get("follow_osu", mk_osu)

        def mk():
            R = self.R * 2.4
            s = R * 2 + 8
            pm = _pm(s, s, self.dpr)
            p = _painter(pm)
            c = QPointF(s / 2, s / 2)
            g = QRadialGradient(c, R)
            g.setColorAt(0.80, QColor(255, 170, 60, 0))
            g.setColorAt(0.92, QColor(255, 190, 80, 110))
            g.setColorAt(1.0, QColor(255, 190, 80, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(g))
            p.drawEllipse(c, R, R)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(255, 210, 120, 230), max(2.0, self.R * 0.12)))
            p.drawEllipse(c, R * 0.9, R * 0.9)
            p.end()
            return pm
        return self._get("follow", mk)

    def reverse_arrow(self) -> QPixmap:
        if LOOK == "osu":
            def mk_osu():
                R = self.R * 0.62
                s = R * 2 + 8
                pm = _pm(s, s, self.dpr)
                p = _painter(pm)
                c = s / 2
                path = QPainterPath()                  # толстая стрелка: древко и наконечник
                path.moveTo(c - R * 0.78, c - R * 0.2)
                path.lineTo(c + R * 0.02, c - R * 0.2)
                path.lineTo(c + R * 0.02, c - R * 0.62)
                path.lineTo(c + R * 0.82, c)
                path.lineTo(c + R * 0.02, c + R * 0.62)
                path.lineTo(c + R * 0.02, c + R * 0.2)
                path.lineTo(c - R * 0.78, c + R * 0.2)
                path.closeSubpath()
                p.setPen(QPen(QColor(0, 0, 0, 150), R * 0.16, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                              Qt.PenJoinStyle.RoundJoin))
                p.setBrush(QColor(255, 255, 255))
                p.drawPath(path)
                p.end()
                return pm
            return self._get("rev_osu", mk_osu)

        def mk():
            R = self.R * 0.6
            s = R * 2 + 6
            pm = _pm(s, s, self.dpr)
            p = _painter(pm)
            path = QPainterPath()
            c = s / 2
            path.moveTo(c - R * 0.7, c - R * 0.75)
            path.lineTo(c + R * 0.35, c)
            path.lineTo(c - R * 0.7, c + R * 0.75)
            p.setPen(QPen(QColor(0, 0, 0, 110), R * 0.42, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                          Qt.PenJoinStyle.RoundJoin))
            p.drawPath(path)
            p.setPen(QPen(QColor(255, 255, 255), R * 0.26, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                          Qt.PenJoinStyle.RoundJoin))
            p.drawPath(path)
            p.end()
            return pm
        return self._get("rev", mk)

    def tick(self) -> QPixmap:
        def mk():
            r = max(3.0, self.R * 0.13)
            pm = _pm(r * 2 + 4, r * 2 + 4, self.dpr)
            p = _painter(pm)
            p.setPen(QPen(QColor(0, 0, 0, 120), r * 0.35))
            p.setBrush(QColor(255, 255, 255))
            p.drawEllipse(QPointF(r + 2, r + 2), r, r)
            p.end()
            return pm
        return self._get("tick", mk)

    def followpoint(self) -> QPixmap:
        def mk():
            r = max(4.0, self.R * 0.22)
            pm = _pm(r * 2, r * 2, self.dpr)
            p = _painter(pm)
            path = QPainterPath()
            path.moveTo(r * 0.45, r * 0.45)
            path.lineTo(r * 1.35, r)
            path.lineTo(r * 0.45, r * 1.55)
            p.setPen(QPen(QColor(255, 255, 255, 230), r * 0.32, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                          Qt.PenJoinStyle.RoundJoin))
            p.drawPath(path)
            p.end()
            return pm
        return self._get("fp", mk)

    # ── судейство и частицы ── #
    def judgement(self, kind) -> QPixmap:
        """Оценка попадания: 300 / 100 / 50 / промах; "300g" — «гэки» (всё комбо на 300, 激),
        "300k"/"100k" — «кацу» (комбо без 50 и промахов, 喝) — как в стандартном скине osu!."""
        if isinstance(kind, str):
            def mk_gk():
                col = J_COLORS[100] if kind == "100k" else J_COLORS[300]
                ch = "激" if kind == "300g" else "喝"
                f = QFont("Yu Gothic UI")
                f.setPixelSize(max(1, int(self.R * 1.0)))
                f.setWeight(QFont.Weight.Bold)
                path = QPainterPath()
                path.addText(0, 0, f, ch)
                br = path.boundingRect()
                pad = self.R * 0.45
                pm = _pm(br.width() + pad * 2, br.height() + pad * 2, self.dpr)
                sil = _pm(br.width() + pad * 2, br.height() + pad * 2, 1.0)
                q = _painter(sil)
                q.translate(pad - br.left(), pad - br.top())
                q.strokePath(path, QPen(col, self.R * 0.16, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                                        Qt.PenJoinStyle.RoundJoin))
                q.end()
                import blur_fx
                glow = blur_fx.blur_image(sil.toImage(), max(1.5, self.R / 10.0))
                p = _painter(pm)
                p.drawImage(QRectF(0, 0, pm.width() / self.dpr, pm.height() / self.dpr), glow)
                p.translate(pad - br.left(), pad - br.top())
                lg = QLinearGradient(0, br.top(), 0, br.bottom())
                lg.setColorAt(0, lighter(col, 0.7))
                lg.setColorAt(1, lighter(col, 0.15))
                p.fillPath(path, QBrush(lg))
                p.end()
                return pm
            return self._get(("j", kind), mk_gk)

        def mk():
            if kind == 0:
                s = self.R * 1.4
                pm = _pm(s, s, self.dpr)
                p = _painter(pm)
                for w, c in ((s * 0.26, QColor(90, 0, 0, 160)), (s * 0.16, QColor(255, 40, 40))):
                    p.setPen(QPen(c, w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                    p.drawLine(QPointF(s * 0.2, s * 0.2), QPointF(s * 0.8, s * 0.8))
                    p.drawLine(QPointF(s * 0.8, s * 0.2), QPointF(s * 0.2, s * 0.8))
                p.end()
                return pm
            col = J_COLORS[kind]
            if LOOK == "osu":                          # как в osu!: прямые, светлые сверху, с мягким свечением
                return outlined_text(str(kind), self.R * 0.95, col, darker(col, 0.5), self.R * 0.035, self.dpr,
                                     glow=QColor(col.red(), col.green(), col.blue(), 190), italic=False,
                                     weight=QFont.Weight.DemiBold, grad=(lighter(col, 0.7), lighter(col, 0.1)))
            return outlined_text(str(kind), self.R * 0.95, col, darker(col, 0.65), self.R * 0.06, self.dpr,
                                 glow=QColor(col.red(), col.green(), col.blue(), 160), italic=True,
                                 grad=(lighter(col, 0.55), col))
        return self._get(("j", kind), mk)

    def spark(self, ci=None) -> QPixmap:
        def mk():
            r = max(5.0, self.R * 0.28)
            pm = _pm(r * 2, r * 2, self.dpr)
            p = _painter(pm)
            col = QColor(255, 255, 255) if ci is None else lighter(self.color(ci), 0.4)
            g = QRadialGradient(r, r, r)
            g.setColorAt(0, col)
            c2 = QColor(col)
            c2.setAlpha(0)
            g.setColorAt(1, c2)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(g))
            p.drawPolygon(star_shape(r, r, r, r * 0.22, 4))
            p.end()
            return pm
        return self._get(("spark", ci), mk)

    def star2(self) -> QPixmap:
        def mk():
            r = max(10.0, self.R * 0.45)
            pm = _pm(r * 2, r * 2, self.dpr)
            p = _painter(pm)
            g = QRadialGradient(r, r, r)
            g.setColorAt(0, QColor(255, 255, 255, 255))
            g.setColorAt(0.35, QColor(255, 230, 160, 220))
            g.setColorAt(1, QColor(255, 180, 80, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(g))
            p.drawPolygon(star_shape(r, r, r, r * 0.38, 5, -math.pi / 2))
            p.end()
            return pm
        return self._get("star2", mk)

    # ── курсор ── #
    def cursor(self, scale=1.0) -> QPixmap:
        if LOOK == "osu":
            return self._get(("cursor_osu", scale), lambda: self._cursor_osu(scale))

        def mk():
            r = 26 * scale
            s = r * 2
            pm = _pm(s, s, self.dpr)
            p = _painter(pm)
            c = QPointF(r, r)
            gl = QRadialGradient(c, r)
            gl.setColorAt(0.35, QColor(255, 200, 80, 140))
            gl.setColorAt(1.0, QColor(255, 140, 40, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(gl))
            p.drawEllipse(c, r, r)
            g = QRadialGradient(QPointF(r - r * 0.12, r - r * 0.12), r * 0.5)
            g.setColorAt(0, QColor(255, 250, 220))
            g.setColorAt(0.6, QColor(255, 205, 70))
            g.setColorAt(1, QColor(240, 140, 20))
            p.setBrush(QBrush(g))
            p.setPen(QPen(QColor(255, 255, 255), r * 0.07))
            p.drawEllipse(c, r * 0.42, r * 0.42)
            p.end()
            return pm
        return self._get(("cursor", scale), mk)

    def _cursor_osu(self, scale) -> QPixmap:
        """Курсор как в osu!: белое кольцо с розовым свечением, тёмная середина, серая дуга и синий крестик."""
        r = 30 * scale
        s = r * 2
        pm = _pm(s, s, self.dpr)
        p = _painter(pm)
        c = QPointF(r, r)
        gl = QRadialGradient(c, r)
        gl.setColorAt(0.62, QColor(255, 130, 200, 0))
        gl.setColorAt(0.74, QColor(255, 130, 200, 150))
        gl.setColorAt(1.0, QColor(255, 120, 190, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(gl))
        p.drawEllipse(c, r, r)
        p.setBrush(QColor(28, 28, 36, 235))
        p.setPen(QPen(QColor(255, 255, 255), r * 0.11))
        p.drawEllipse(c, r * 0.62, r * 0.62)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(205, 205, 215), r * 0.09, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(QRectF(r - r * 0.38, r - r * 0.38, r * 0.76, r * 0.76), 40 * 16, 280 * 16)
        arm = r * 0.2
        p.setPen(QPen(QColor(255, 255, 255), r * 0.13, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(r - arm, r), QPointF(r + arm, r))
        p.drawLine(QPointF(r, r - arm), QPointF(r, r + arm))
        p.setPen(QPen(QColor(40, 120, 255), r * 0.07, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(r - arm, r), QPointF(r + arm, r))
        p.drawLine(QPointF(r, r - arm), QPointF(r, r + arm))
        p.end()
        return pm

    def trail(self, scale=1.0) -> QPixmap:
        if LOOK == "osu":                              # след — синяя лента, как в osu!
            return self._get(("trail_osu", scale), lambda: glow_dot(11 * scale, QColor(60, 130, 255, 220),
                                                                    self.dpr, 0.35))
        return self._get(("trail", scale), lambda: glow_dot(14 * scale, QColor(255, 210, 110, 210), self.dpr, 0.3))

    # ── спиннер ── #
    def spinner_disc(self, size) -> QPixmap:
        def mk():
            r = size / 2
            pm = _pm(size, size, self.dpr)
            p = _painter(pm)
            c = QPointF(r, r)
            g = QRadialGradient(c, r)
            g.setColorAt(0, QColor(30, 30, 50, 0))
            g.setColorAt(0.6, QColor(40, 40, 70, 90))
            g.setColorAt(0.97, QColor(80, 80, 130, 160))
            g.setColorAt(1, QColor(80, 80, 130, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(g))
            p.drawEllipse(c, r, r)
            for i in range(24):
                a = i / 24 * 2 * math.pi
                L = 0.07 if i % 3 else 0.12
                p.setPen(QPen(QColor(255, 255, 255, 200 if i % 3 == 0 else 110), max(2.0, size * 0.006),
                              Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawLine(QPointF(r + math.cos(a) * r * (0.93 - L), r + math.sin(a) * r * (0.93 - L)),
                           QPointF(r + math.cos(a) * r * 0.93, r + math.sin(a) * r * 0.93))
            p.setPen(QPen(QColor(255, 255, 255, 230), max(2.0, size * 0.008)))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, r * 0.95, r * 0.95)
            p.setPen(QPen(QColor(255, 255, 255, 120), max(1.0, size * 0.004)))
            p.drawEllipse(c, r * 0.6, r * 0.6)
            # середина
            p.setPen(QPen(QColor(255, 255, 255), size * 0.01))
            p.setBrush(QColor(255, 102, 170))
            p.drawEllipse(c, size * 0.035, size * 0.035)
            p.end()
            return pm
        return self._get(("disc", int(size)), mk)

    def ring(self, size, color: QColor, width) -> QPixmap:
        def mk():
            pm = _pm(size + width * 2, size + width * 2, self.dpr)
            p = _painter(pm)
            p.setPen(QPen(color, width))
            p.drawEllipse(QPointF(size / 2 + width, size / 2 + width), size / 2, size / 2)
            p.end()
            return pm
        return self._get(("ring", int(size), color.rgba(), int(width)), mk)


def mod_icon(acr: str, size: float, dpr=1.0, on=True) -> QPixmap:
    cat = {"EZ": QColor(120, 200, 60), "NF": QColor(120, 200, 60), "HT": QColor(120, 200, 60),
           "HR": QColor(255, 90, 90), "SD": QColor(255, 90, 90), "PF": QColor(255, 90, 90), "DT": QColor(255, 90, 90),
           "NC": QColor(255, 90, 90), "HD": QColor(255, 90, 90), "FL": QColor(255, 90, 90),
           "RX": QColor(100, 170, 255), "AP": QColor(100, 170, 255), "AU": QColor(100, 170, 255),
           "SO": QColor(100, 170, 255)}.get(acr, PINK)
    w, h = size * 1.25, size
    pm = _pm(w + 4, h + 4, dpr)
    p = _painter(pm)
    poly = QPolygonF([QPointF(2 + w * 0.12, 2), QPointF(2 + w * 0.88, 2), QPointF(2 + w, 2 + h / 2),
                      QPointF(2 + w * 0.88, 2 + h), QPointF(2 + w * 0.12, 2 + h), QPointF(2, 2 + h / 2)])
    g = QLinearGradient(0, 0, 0, h)
    base = cat if on else QColor(70, 70, 80)
    g.setColorAt(0, lighter(base, 0.25))
    g.setColorAt(1, darker(base, 0.2))
    p.setPen(QPen(QColor(255, 255, 255, 200 if on else 60), max(1.0, size * 0.05)))
    p.setBrush(QBrush(g))
    p.drawPolygon(poly)
    f = QFont(FONT)
    f.setPixelSize(int(size * 0.5))
    f.setWeight(QFont.Weight.Black)
    p.setFont(f)
    p.setPen(QColor(255, 255, 255) if on else QColor(160, 160, 170))
    p.drawText(QRectF(2, 2, w, h), Qt.AlignmentFlag.AlignCenter, acr)
    p.end()
    return pm


GRADE_COLORS = {"SS": (QColor(255, 236, 140), QColor(255, 176, 40)), "S": (QColor(255, 226, 120), QColor(255, 160, 30)),
                "SSH": (QColor(255, 255, 255), QColor(170, 190, 210)), "SH": (QColor(240, 245, 255), QColor(150, 170, 200)),
                "A": (QColor(140, 255, 140), QColor(40, 190, 60)), "B": (QColor(130, 200, 255), QColor(40, 120, 230)),
                "C": (QColor(230, 150, 255), QColor(160, 60, 220)), "D": (QColor(255, 120, 120), QColor(220, 40, 40))}


def grade_pix(g: str, size: float, dpr=1.0) -> QPixmap:
    c0, c1 = GRADE_COLORS.get(g, GRADE_COLORS["D"])
    txt = g.replace("H", "")
    return outlined_text(txt, size, c1, QColor(0, 0, 0, 160), size * 0.03, dpr,
                         glow=QColor(c1.red(), c1.green(), c1.blue(), 200), grad=(c0, c1), italic=False)


# ------------------------------------------------------------------ #
#  Звуки (синтез)                                                     #
# ------------------------------------------------------------------ #

SR = 48000


def _env(n, a=0.002, d=0.05):
    t = np.arange(n) / SR
    e = np.exp(-t / max(1e-4, d))
    na = max(1, int(a * SR))
    e[:na] *= np.linspace(0, 1, na)
    return e


def _band_noise(n, lo, hi, seed=0):
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(n)
    f = np.fft.rfft(x)
    fr = np.fft.rfftfreq(n, 1 / SR)
    f *= ((fr >= lo) & (fr <= hi)).astype(float) * 1.0
    y = np.fft.irfft(f, n)
    return y / (np.abs(y).max() + 1e-9)


def _sine(n, f0, f1=None):
    t = np.arange(n) / SR
    f = f0 if f1 is None else f0 * (f1 / f0) ** (t / max(1e-6, t[-1]))
    return np.sin(2 * np.pi * np.cumsum(np.full(n, f) if np.isscalar(f) else f) / SR)


def _lfilter(b, a, x):
    """IIR-фильтр: scipy (C, миллисекунды), без scipy — цикл на Python (медленно, но работает)."""
    try:
        from scipy.signal import lfilter
        return lfilter(b, a, x)
    except Exception:                                      # noqa: BLE001
        y = np.zeros_like(x)
        nb, na = len(b), len(a)
        for i in range(len(x)):
            v = sum(b[j] * x[i - j] for j in range(nb) if i - j >= 0)
            v -= sum(a[j] * y[i - j] for j in range(1, na) if i - j >= 0)
            y[i] = v / a[0]
        return y


def _res(x, f, q=12.0):
    """Резонатор (двухполюсный фильтр) — «тело» деревянного/кожаного звука."""
    w = 2 * np.pi * f / SR
    r = np.exp(-w / (2 * q))
    a1, a2 = -2 * r * np.cos(w), r * r
    return _lfilter([1.0], [1.0, a1, a2], np.asarray(x, np.float64))


def _hp(x, k=0.97):
    """Простой фильтр верхних частот: убирает гул/постоянную составляющую."""
    return _lfilter([k, -k], [1.0, -k], np.asarray(x, np.float64))


def _impulse(n, seed, ms=1.2):
    rng = np.random.default_rng(seed)
    x = np.zeros(n)
    m = max(4, int(ms / 1000 * SR))
    x[:m] = rng.standard_normal(m) * np.hanning(m * 2)[m:]
    return x


def make_sounds() -> dict:
    """Звуки в духе стандартного скина osu!: сухие короткие удары (палочка, обод, бочка),
    тарелка, хлопок; меню — тихие щелчки. Без мелодий и писка: всё — шумовые удары и резонаторы."""
    S = {}

    def add(name, y, gain=0.7):
        y = np.asarray(y, np.float64)
        y -= y.mean()
        fade = min(len(y), int(0.004 * SR))
        y[-fade:] *= np.linspace(1, 0, fade)
        y = y / (np.abs(y).max() + 1e-9) * gain
        S[name] = y.astype(np.float32)

    # normal-hitnormal: сухой щелчок палочкой по ободу — шумовой импульс в резонаторах ~1.1/2.4 кГц
    n = int(0.11 * SR)
    imp = _impulse(n, 1, 1.0)
    body = _res(imp, 1150, 9) * 0.9 + _res(imp, 2450, 14) * 0.45 + _res(imp, 420, 5) * 0.5
    add("normal", body * _env(n, 0.0003, 0.028) + 0.25 * _band_noise(n, 3000, 9000, 11) * _env(n, 0.0002, 0.004), 0.5)
    # soft-hitnormal: мягкий удар по коже, ниже и глуше
    n = int(0.14 * SR)
    imp = _impulse(n, 2, 2.5)
    add("soft", (_res(imp, 520, 6) + 0.35 * _res(imp, 1250, 8)) * _env(n, 0.001, 0.04), 0.45)
    # drum-hitnormal: короткая бочка с щелчком
    n = int(0.22 * SR)
    kick = _sine(n, 140, 52) * _env(n, 0.0008, 0.075)
    add("drum", kick + 0.35 * _res(_impulse(n, 3, 0.6), 3200, 6) * _env(n, 0.0002, 0.006), 0.7)
    # whistle: короткий металлический «дзынь» (как в скине — не свист-писк)
    n = int(0.25 * SR)
    t = np.arange(n) / SR
    w = sum(a * np.sin(2 * np.pi * f * t) * np.exp(-t / d) for f, a, d in
            ((2637, 1.0, 0.07), (3951, 0.45, 0.05), (5274, 0.2, 0.03)))
    add("whistle", w * _env(n, 0.002, 1.0), 0.28)
    # finish: тарелка — светлый шум с медленным спадом и неровными металлическими обертонами
    n = int(1.1 * SR)
    t = np.arange(n) / SR
    rng = np.random.default_rng(4)
    metal = sum(np.sin(2 * np.pi * f * t + rng.uniform(0, 6)) * np.exp(-t / 0.35)
                for f in (3120, 4340, 5210, 6670, 7980, 9410))
    cym = _band_noise(n, 5200, 15000, 4) * _env(n, 0.0008, 0.38) + 0.08 * metal / 6
    add("finish", _hp(cym) + 0.15 * _band_noise(n, 1800, 5000, 5) * _env(n, 0.0005, 0.05), 0.42)
    # clap: несколько коротких шумовых хлопков подряд + хвост
    n = int(0.2 * SR)
    clap = np.zeros(n)
    for k, d in enumerate((0.0, 0.007, 0.015, 0.021)):
        o = int(d * SR)
        m = n - o
        clap[o:] += _band_noise(m, 900, 5200, 6 + k) * _env(m, 0.0003, 0.006 if k < 3 else 0.06)
    add("clap", clap, 0.45)
    # тик слайдера — едва слышный щелчок
    n = int(0.04 * SR)
    add("tick", _res(_impulse(n, 9, 0.5), 3600, 10) * _env(n, 0.0002, 0.006), 0.18)
    n = int(0.11 * SR)
    imp = _impulse(n, 10, 0.8)
    add("slider_end", (_res(imp, 1300, 9) + 0.3 * _res(imp, 2700, 12)) * _env(n, 0.0003, 0.02), 0.32)
    # combobreak: глухой «срыв» — удар вниз по высоте с шумом
    n = int(0.42 * SR)
    add("combobreak", _sine(n, 260, 70) * _env(n, 0.002, 0.11) + 0.5 * _res(_impulse(n, 12, 3), 300, 3)
        * _env(n, 0.001, 0.05) + 0.25 * _band_noise(n, 150, 900, 13) * _env(n, 0.001, 0.09), 0.5)
    # бонус спиннера — короткий колокольчик (негромко)
    n = int(0.5 * SR)
    t = np.arange(n) / SR
    add("bonus", sum(a * np.sin(2 * np.pi * f * t) * np.exp(-t / d) for f, a, d in
                     ((1568, 1.0, 0.18), (3136, 0.3, 0.1), (4704, 0.12, 0.06))) * _env(n, 0.002, 1.0), 0.28)
    add("spin", np.zeros(32), 0.0)                       # вращение спиннера — без писка
    # меню: тихие щелчки
    n = int(0.05 * SR)
    add("menuclick", _res(_impulse(n, 14, 0.6), 2200, 10) * _env(n, 0.0002, 0.01), 0.22)
    n = int(0.12 * SR)
    imp = _impulse(n, 15, 1.5)
    add("menuhit", (_res(imp, 780, 7) + 0.4 * _res(imp, 1700, 9)) * _env(n, 0.0005, 0.035), 0.4)
    n = int(0.12 * SR)
    add("menuback", _res(_impulse(n, 16, 2.0), 380, 5) * _env(n, 0.0008, 0.04), 0.4)
    n = int(0.03 * SR)
    add("hover", _res(_impulse(n, 17, 0.4), 3000, 12) * _env(n, 0.0002, 0.004), 0.07)
    # отсчёт — деревянная коробочка (метроном), «GO» — удар с тарелкой
    for name, f in (("count3", 1250), ("count2", 1250), ("count1", 1250)):
        n = int(0.09 * SR)
        add(name, _res(_impulse(n, 18, 0.8), f, 16) * _env(n, 0.0002, 0.03), 0.4)
    n = int(0.8 * SR)
    add("go", 0.8 * _sine(n, 120, 50) * _env(n, 0.001, 0.08) + _band_noise(n, 5000, 14000, 19) * _env(n, 0.001, 0.25),
        0.4)
    # середина перерыва: прошёл — светлый короткий аккорд-«звон», нет — глухой низкий
    n = int(0.6 * SR)
    t = np.arange(n) / SR
    add("pass", sum(np.sin(2 * np.pi * f * t) * np.exp(-t / 0.22) for f in (880, 1318.5, 1760))
        * _env(n, 0.003, 1.0), 0.25)
    add("fail_section", sum(np.sin(2 * np.pi * f * t) * np.exp(-t / 0.25) for f in (220, 261.6))
        * _env(n, 0.004, 1.0), 0.3)
    # провал: «магнитофон остановился» — низкий гул с падающей высотой и шумом
    n = int(1.8 * SR)
    t = np.arange(n) / SR
    fall = _sine(n, 180, 30) * np.exp(-t / 0.7) + 0.4 * _band_noise(n, 60, 500, 20) * np.exp(-t / 0.5)
    add("fail", fall * _env(n, 0.01, 10.0), 0.45)
    # вступление меню — мягкий воздух
    n = int(1.2 * SR)
    t = np.arange(n) / SR
    add("whoosh", _band_noise(n, 400, 5000, 21) * np.sin(np.pi * t / t[-1]) ** 3, 0.18)
    # аплодисменты на результатах: сотни коротких хлопков толпы
    n = int(2.6 * SR)
    t = np.arange(n) / SR
    rng = np.random.default_rng(22)
    crowd = np.zeros(n)
    tmpl = [_band_noise(int(0.012 * SR), lo, lo * 4, 30 + i) * _env(int(0.012 * SR), 0.0004, 0.003)
            for i, lo in enumerate((700, 900, 1100, 1300))]
    for _ in range(900):
        o = int(rng.uniform(0, n - 600))
        c = tmpl[rng.integers(0, 4)]
        crowd[o:o + len(c)] += c * rng.uniform(0.3, 1.0)
    crowd *= np.minimum(1.0, t / 0.25) * np.minimum(1.0, (t[-1] - t) / 0.9)
    add("applause", crowd, 0.35)
    # добавки по наборам звуков карт osu!: soft — мягче и глуше, drum — с ударом снизу
    for base in ("whistle", "finish", "clap", "tick"):
        y = S[base].astype(np.float64)
        g = float(np.abs(y).max()) or 1.0
        S["normal-" + base] = S[base]
        soft = _lfilter([0.3], [1.0, -0.7], y)
        S["soft-" + base] = (soft / (np.abs(soft).max() + 1e-9) * g * 0.8).astype(np.float32)
        n = len(y)
        thump = _sine(n, 110, 60) * _env(n, 0.001, 0.03) * 0.6 if base != "tick" else np.zeros(n)
        dr = y + thump * g
        S["drum-" + base] = (dr / (np.abs(dr).max() + 1e-9) * g).astype(np.float32)
    return S
