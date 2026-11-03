"""Свои иконки для каждой ачивки Echoes.NET (рисуются векторами, без картинок — чёткие на любом размере).

paint_ach(p, kind, rect, color) — значок ачивки в rect; pixmap(kind, size, color) — готовая картинка (с кэшем).
Рисуем на сетке 100×100: линии одной толщины, скруглённые концы — в одном стиле с остальными значками ECHOES.
"""
import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap

_CACHE: dict = {}


def _pen(p, c, w=7.0):
    p.setPen(QPen(c, w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)


def _fill(p, c, a=255):
    col = QColor(c)
    col.setAlpha(a)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(col)


def _star(cx, cy, r, n=5, inner=0.45, rot=-90):
    path = QPainterPath()
    for i in range(n * 2):
        rr = r if i % 2 == 0 else r * inner
        a = math.radians(rot + i * 180 / n)
        pt = QPointF(cx + math.cos(a) * rr, cy + math.sin(a) * rr)
        path.moveTo(pt) if i == 0 else path.lineTo(pt)
    path.closeSubpath()
    return path


def _note(p, c, x, y, s=1.0):
    """Маленькая нота: головка в (x, y)."""
    _fill(p, c)
    p.drawEllipse(QPointF(x, y), 7 * s, 5.5 * s)
    _pen(p, c, 5 * s)
    p.drawLine(QPointF(x + 6 * s, y), QPointF(x + 6 * s, y - 24 * s))
    p.drawLine(QPointF(x + 6 * s, y - 24 * s), QPointF(x + 15 * s, y - 18 * s))


def _first_post(p, c):            # перо
    _pen(p, c)
    path = QPainterPath(QPointF(24, 80))
    path.cubicTo(QPointF(30, 46), QPointF(52, 22), QPointF(80, 18))
    path.cubicTo(QPointF(78, 44), QPointF(58, 66), QPointF(24, 80))
    p.drawPath(path)
    p.drawLine(QPointF(24, 80), QPointF(60, 40))
    p.drawLine(QPointF(18, 86), QPointF(26, 78))


def _author(p, c):                # раскрытая книга
    _pen(p, c)
    for sx in (1, -1):
        path = QPainterPath(QPointF(50, 30))
        path.cubicTo(QPointF(50 - 12 * sx, 22), QPointF(50 - 28 * sx, 22), QPointF(50 - 38 * sx, 26))
        path.lineTo(QPointF(50 - 38 * sx, 74))
        path.cubicTo(QPointF(50 - 28 * sx, 70), QPointF(50 - 12 * sx, 70), QPointF(50, 78))
        p.drawPath(path)
    p.drawLine(QPointF(50, 30), QPointF(50, 78))


def _social(p, c):                # двое людей
    _pen(p, c)
    p.drawEllipse(QPointF(38, 36), 11, 11)
    p.drawArc(QRectF(16, 54, 44, 40), 0, 180 * 16)
    p.drawEllipse(QPointF(66, 40), 9, 9)
    p.drawArc(QRectF(54, 58, 32, 32), 0, 160 * 16)


def _dj(p, c):                    # пластинка с тонармом
    _pen(p, c)
    p.drawEllipse(QPointF(44, 56), 30, 30)
    _fill(p, c)
    p.drawEllipse(QPointF(44, 56), 7, 7)
    _pen(p, c, 6)
    p.drawLine(QPointF(84, 14), QPointF(84, 42))
    p.drawLine(QPointF(84, 42), QPointF(62, 60))


def _together(p, c):              # наушники с сердцем
    _pen(p, c)
    p.drawArc(QRectF(18, 18, 64, 64), 20 * 16, 140 * 16)
    p.drawRoundedRect(QRectF(16, 50, 14, 24), 6, 6)
    p.drawRoundedRect(QRectF(70, 50, 14, 24), 6, 6)
    _heart(p, c, 50, 60, 0.42)


def _heart(p, c, cx, cy, s):
    path = QPainterPath(QPointF(cx, cy + 26 * s))
    path.cubicTo(QPointF(cx - 34 * s, cy + 4 * s), QPointF(cx - 22 * s, cy - 28 * s), QPointF(cx, cy - 12 * s))
    path.cubicTo(QPointF(cx + 22 * s, cy - 28 * s), QPointF(cx + 34 * s, cy + 4 * s), QPointF(cx, cy + 26 * s))
    _fill(p, c)
    p.drawPath(path)


def _inseparable(p, c):           # сцепленные кольца
    _pen(p, c)
    p.drawEllipse(QPointF(38, 52), 22, 22)
    p.drawEllipse(QPointF(62, 52), 22, 22)
    _fill(p, c)
    p.drawPath(_star(50, 18, 8, 4, 0.4, -90))


def _sleuth(p, c):                # лупа и нота
    _pen(p, c)
    p.drawEllipse(QPointF(42, 42), 22, 22)
    p.drawLine(QPointF(58, 58), QPointF(82, 82))
    _note(p, c, 38, 50, 0.75)


def _hitmaker(p, c):              # корона «№1»
    _pen(p, c)
    path = QPainterPath(QPointF(20, 70))
    for x, y in ((16, 32), (36, 50), (50, 24), (64, 50), (84, 32), (80, 70)):
        path.lineTo(QPointF(x, y))
    path.closeSubpath()
    p.drawPath(path)
    p.drawLine(QPointF(22, 82), QPointF(78, 82))
    _fill(p, c)
    for x, y in ((16, 30), (50, 22), (84, 30)):
        p.drawEllipse(QPointF(x, y), 5, 5)


def _melomane(p, c):              # большие наушники и волна
    _pen(p, c)
    p.drawArc(QRectF(16, 16, 68, 68), 0, 180 * 16)
    p.drawRoundedRect(QRectF(12, 50, 16, 30), 7, 7)
    p.drawRoundedRect(QRectF(72, 50, 16, 30), 7, 7)
    _pen(p, c, 5)
    for i, h in enumerate((8, 18, 12, 22, 10)):
        x = 38 + i * 6
        p.drawLine(QPointF(x, 64 - h / 2), QPointF(x, 64 + h / 2))


def _collector(p, c):             # стопка пластинок
    _pen(p, c)
    for i, y in enumerate((70, 52, 34)):
        p.drawEllipse(QRectF(18 + i * 2, y - 10, 64 - i * 4, 20))
    _fill(p, c)
    p.drawEllipse(QPointF(50, 34), 5, 3)


def _group(p, c):                 # два облачка чата
    _pen(p, c)
    p.drawRoundedRect(QRectF(14, 20, 48, 34), 12, 12)
    p.drawLine(QPointF(26, 54), QPointF(22, 66))
    p.drawLine(QPointF(22, 66), QPointF(36, 54))
    p.drawRoundedRect(QRectF(44, 46, 42, 30), 12, 12)
    _fill(p, c)
    for x in (28, 38, 48):
        p.drawEllipse(QPointF(x, 37), 3.5, 3.5)


def _champion(p, c):              # кубок
    _pen(p, c)
    path = QPainterPath(QPointF(30, 18))
    path.lineTo(QPointF(70, 18))
    path.cubicTo(QPointF(70, 50), QPointF(60, 58), QPointF(50, 58))
    path.cubicTo(QPointF(40, 58), QPointF(30, 50), QPointF(30, 18))
    p.drawPath(path)
    p.drawArc(QRectF(14, 22, 20, 20), 90 * 16, 180 * 16)
    p.drawArc(QRectF(66, 22, 20, 20), 270 * 16, 180 * 16)
    p.drawLine(QPointF(50, 58), QPointF(50, 72))
    p.drawLine(QPointF(34, 82), QPointF(66, 82))
    p.drawLine(QPointF(40, 72), QPointF(60, 72))
    _fill(p, c)
    p.drawPath(_star(50, 34, 9))


def _night_owl(p, c):             # месяц и звёзды
    path = QPainterPath()
    path.addEllipse(QPointF(46, 54), 30, 30)
    cut = QPainterPath()
    cut.addEllipse(QPointF(62, 42), 26, 26)
    _fill(p, c)
    p.drawPath(path.subtracted(cut))
    p.drawPath(_star(72, 70, 7, 4, 0.4))
    p.drawPath(_star(80, 22, 9, 4, 0.4))


def _early_bird(p, c):            # восход
    _pen(p, c)
    p.drawArc(QRectF(28, 46, 44, 44), 0, 180 * 16)
    p.drawLine(QPointF(14, 68), QPointF(86, 68))
    p.drawLine(QPointF(26, 82), QPointF(74, 82))
    for a in (180, 225, 270, 315, 360):
        r = math.radians(a)
        p.drawLine(QPointF(50 + math.cos(r) * 30, 66 + math.sin(r) * 30),
                   QPointF(50 + math.cos(r) * 40, 66 + math.sin(r) * 40))


def _streak7(p, c):               # огонь с «7»
    path = QPainterPath(QPointF(50, 88))
    path.cubicTo(QPointF(24, 88), QPointF(16, 64), QPointF(28, 46))
    path.cubicTo(QPointF(34, 56), QPointF(40, 56), QPointF(40, 50))
    path.cubicTo(QPointF(40, 32), QPointF(48, 20), QPointF(58, 12))
    path.cubicTo(QPointF(60, 30), QPointF(84, 44), QPointF(80, 66))
    path.cubicTo(QPointF(78, 80), QPointF(66, 88), QPointF(50, 88))
    _fill(p, c)
    p.drawPath(path)
    bg = QColor(0, 0, 0, 200)
    _pen(p, bg, 6)
    p.drawLine(QPointF(42, 56), QPointF(60, 56))
    p.drawLine(QPointF(60, 56), QPointF(48, 78))


def _explorer(p, c):              # компас
    _pen(p, c)
    p.drawEllipse(QPointF(50, 50), 34, 34)
    _fill(p, c)
    needle = QPainterPath(QPointF(64, 30))
    needle.lineTo(QPointF(55, 55))
    needle.lineTo(QPointF(36, 70))
    needle.lineTo(QPointF(45, 45))
    needle.closeSubpath()
    p.drawPath(needle)


def _loyal(p, c):                 # сердце с нотой
    _heart(p, c, 50, 50, 1.2)
    bg = QColor(0, 0, 0, 200)
    _note(p, bg, 44, 60, 0.8)


def _marathon(p, c):              # секундомер
    _pen(p, c)
    p.drawEllipse(QPointF(50, 56), 30, 30)
    p.drawLine(QPointF(42, 14), QPointF(58, 14))
    p.drawLine(QPointF(50, 14), QPointF(50, 26))
    p.drawLine(QPointF(74, 30), QPointF(80, 24))
    p.drawLine(QPointF(50, 56), QPointF(50, 40))
    p.drawLine(QPointF(50, 56), QPointF(62, 64))


DRAW = {"first_post": _first_post, "author": _author, "social": _social, "dj": _dj, "together": _together,
        "inseparable": _inseparable, "sleuth": _sleuth, "hitmaker": _hitmaker, "melomane": _melomane,
        "collector": _collector, "group": _group, "champion": _champion, "night_owl": _night_owl,
        "early_bird": _early_bird, "streak7": _streak7, "explorer": _explorer, "loyal": _loyal,
        "marathon": _marathon}


def paint_ach(p: QPainter, kind: str, rect: QRectF, color) -> None:
    fn = DRAW.get(kind)
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.translate(rect.topLeft())
    p.scale(rect.width() / 100, rect.height() / 100)
    c = QColor(color)
    if fn is None:
        _fill(p, c)
        p.drawPath(_star(50, 52, 38))
    else:
        fn(p, c)
    p.restore()


def pixmap(kind: str, size: int, color="#ffdb1a") -> QPixmap:
    key = (kind, size, str(color))
    pm = _CACHE.get(key)
    if pm is None:
        dpr = 2
        pm = QPixmap(size * dpr, size * dpr)
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        paint_ach(p, kind, QRectF(0, 0, size, size), color)
        p.end()
        _CACHE[key] = pm
    return pm
