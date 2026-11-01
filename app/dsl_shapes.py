# dsl_shapes.py
"""
EchoScript — контуры любой формы и холсты.

  EPath    — контур (QPainterPath) как значение скрипта: строится по шагам (move/line/curve/arc…),
             из готовой фигуры shape(…) или из SVG; складывается/вычитается/пересекается с другими,
             двигается/крутится/масштабируется, умеет contains(x, y) (попадание мышью в любую форму)
             и point_at(t) (точка на контуре — для движения «по рельсам»).
  shape()  — готовые формы, вписанные в прямоугольник: круг, скруглённый квадрат, сквиркл, звезда,
             сердце, капля, шестерёнка, цветок, волна, облако, луна, щит, крест, арка, кольцо…
  ECanvas  — свой холст (картинка в памяти): рисовать в него draw_on(c, fn() { … }) без очистки
             между кадрами (следы, пиксель-арт, «краска»), читать/ставить пиксели, сохранять в PNG.
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainterPath, QPainterPathStroker, QTransform

import dsl_lang as L
from dsl_lang import EchoError


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


class EPath:
    """Контур для скрипта. Методы, меняющие контур, возвращают его же (можно цепочкой)."""
    echo_type = "контур"
    echo_kind = "path"
    __slots__ = ("pp",)

    def __init__(self, pp: QPainterPath | None = None):
        self.pp = pp if pp is not None else QPainterPath()

    def __repr__(self):
        b = self.pp.boundingRect()
        return f"<контур {b.width():.0f}×{b.height():.0f}>"

    # ── построение ──
    def move(self, x, y):
        self.pp.moveTo(_f(x), _f(y))
        return self

    def line(self, x, y):
        self.pp.lineTo(_f(x), _f(y))
        return self

    def curve(self, c1x, c1y, c2x, c2y, x, y):
        self.pp.cubicTo(_f(c1x), _f(c1y), _f(c2x), _f(c2y), _f(x), _f(y))
        return self

    def quad(self, cx, cy, x, y):
        self.pp.quadTo(_f(cx), _f(cy), _f(x), _f(y))
        return self

    def arc(self, cx, cy, r, a0, sweep):
        """Дуга окружности (центр, радиус, начальный угол и размах в градусах, по часовой)."""
        r = _f(r)
        self.pp.arcTo(QRectF(_f(cx) - r, _f(cy) - r, 2 * r, 2 * r), -_f(a0), -_f(sweep))
        return self

    def close(self):
        self.pp.closeSubpath()
        return self

    def rect(self, x, y, w, h, r=0):
        if _f(r) > 0:
            self.pp.addRoundedRect(QRectF(_f(x), _f(y), _f(w), _f(h)), _f(r), _f(r))
        else:
            self.pp.addRect(QRectF(_f(x), _f(y), _f(w), _f(h)))
        return self

    def circle(self, x, y, r):
        self.pp.addEllipse(QPointF(_f(x), _f(y)), _f(r), _f(r))
        return self

    def ellipse(self, x, y, rx, ry):
        self.pp.addEllipse(QPointF(_f(x), _f(y)), _f(rx), _f(ry))
        return self

    def poly(self, points, closed=True):
        pts = _points(points)
        if len(pts) >= 2:
            self.pp.moveTo(pts[0])
            for q in pts[1:]:
                self.pp.lineTo(q)
            if closed:
                self.pp.closeSubpath()
        return self

    def smooth(self, points, closed=True, tension=0.5):
        _catmull_into(self.pp, _points(points), bool(closed), _f(tension, 0.5))
        return self

    def text(self, s, x, y, size=32, font="Segoe UI", weight=700):
        f = QFont()
        f.setFamilies([str(font or "Segoe UI"), "Segoe UI", "Arial"])
        f.setPixelSize(max(1, int(_f(size, 32))))
        f.setWeight(QFont.Weight(max(100, min(900, int(_f(weight, 700))))))
        self.pp.addText(QPointF(_f(x), _f(y)), f, L._to_str(s))
        return self

    def add(self, other):
        if isinstance(other, EPath):
            self.pp.addPath(other.pp)
        return self

    # ── логика форм (новые контуры) ──
    def union(self, other):
        return EPath(self.pp.united(_pp(other)))

    def minus(self, other):
        return EPath(self.pp.subtracted(_pp(other)))

    def intersect(self, other):
        return EPath(self.pp.intersected(_pp(other)))

    def outline(self, width=4.0):
        """Контур-обводка заданной толщины (как фигура)."""
        st = QPainterPathStroker()
        st.setWidth(max(0.1, _f(width, 4.0)))
        st.setCapStyle(Qt.PenCapStyle.RoundCap)
        st.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        return EPath(st.createStroke(self.pp))

    # ── трансформации (новые контуры) ──
    def moved(self, dx, dy):
        return EPath(self.pp.translated(_f(dx), _f(dy)))

    def rotated(self, deg, cx=None, cy=None):
        b = self.pp.boundingRect()
        cx = b.center().x() if cx is None else _f(cx)
        cy = b.center().y() if cy is None else _f(cy)
        t = QTransform().translate(cx, cy).rotate(_f(deg)).translate(-cx, -cy)
        return EPath(t.map(self.pp))

    def scaled(self, sx, sy=None, cx=None, cy=None):
        b = self.pp.boundingRect()
        cx = b.center().x() if cx is None else _f(cx)
        cy = b.center().y() if cy is None else _f(cy)
        sy = sx if sy is None else sy
        t = QTransform().translate(cx, cy).scale(_f(sx, 1), _f(sy, 1)).translate(-cx, -cy)
        return EPath(t.map(self.pp))

    def fit(self, x, y, w, h, keep=True):
        """Вписать в прямоугольник (keep — сохранить пропорции, по центру)."""
        return EPath(fit_path(self.pp, _f(x), _f(y), _f(w), _f(h), bool(keep)))

    def copy(self):
        return EPath(QPainterPath(self.pp))

    # ── вопросы ──
    def contains(self, x, y):
        return self.pp.contains(QPointF(_f(x), _f(y)))

    def point_at(self, t):
        """Точка на контуре: t — 0…1 вдоль длины → {x, y, angle}."""
        if self.pp.isEmpty():
            return {"x": 0.0, "y": 0.0, "angle": 0.0}
        t = _f(t) % 1.0
        pt = self.pp.pointAtPercent(t)
        return {"x": pt.x(), "y": pt.y(), "angle": -self.pp.angleAtPercent(t)}

    def bounds(self):
        b = self.pp.boundingRect()
        return {"x": b.x(), "y": b.y(), "w": b.width(), "h": b.height()}

    _METHODS = ("move line curve quad arc close rect circle ellipse poly smooth text add union minus intersect "
                "outline moved rotated scaled fit copy contains point_at bounds").split()

    def echo_get(self, name):
        if name in ("length", "len"):
            return self.pp.length()
        if name == "empty":
            return self.pp.isEmpty()
        if name in ("x", "y", "w", "h"):
            return self.bounds()[name]
        if name == "subtract":
            name = "minus"
        if name in self._METHODS:
            return getattr(self, name)
        raise EchoError(f"у контура нет «{name}» (есть: {', '.join(self._METHODS)}, length, empty, x, y, w, h)")


def _pp(v) -> QPainterPath:
    if isinstance(v, EPath):
        return v.pp
    raise EchoError(f"ожидался контур (path_new(), shape(…), path_svg(…)), а передано «{L._type_name(v)}»")


def _points(points):
    if isinstance(points, str):                           # "0,0 10,0 5,8"
        out = []
        for pair in points.replace(";", " ").split():
            a, _, b = pair.partition(",")
            out.append(QPointF(_f(a), _f(b)))
        return out
    if not isinstance(points, (list, tuple)):
        raise EchoError("точки — список [x1, y1, x2, y2, …], [[x, y], …] или строка \"x,y x,y …\"")
    if points and isinstance(points[0], (list, tuple)):
        return [QPointF(_f(a[0]), _f(a[1])) for a in points if len(a) >= 2]
    if points and isinstance(points[0], dict):
        return [QPointF(_f(a.get("x")), _f(a.get("y"))) for a in points]
    return [QPointF(_f(points[i]), _f(points[i + 1])) for i in range(0, len(points) - 1, 2)]


def _catmull_into(pp, pts, closed, t=0.5):
    n = len(pts)
    if n < 2:
        return
    pp.moveTo(pts[0])
    rng = range(n) if closed else range(n - 1)
    for i in rng:
        p0 = pts[(i - 1) % n] if (closed or i > 0) else pts[i]
        p1 = pts[i]
        p2 = pts[(i + 1) % n]
        p3 = pts[(i + 2) % n] if (closed or i + 2 < n) else p2
        c1 = QPointF(p1.x() + (p2.x() - p0.x()) * t / 3, p1.y() + (p2.y() - p0.y()) * t / 3)
        c2 = QPointF(p2.x() - (p3.x() - p1.x()) * t / 3, p2.y() - (p3.y() - p1.y()) * t / 3)
        pp.cubicTo(c1, c2, p2)
    if closed:
        pp.closeSubpath()


def fit_path(pp: QPainterPath, x, y, w, h, keep=True) -> QPainterPath:
    br = pp.boundingRect()
    if br.width() <= 1e-9 or br.height() <= 1e-9 or w <= 0 or h <= 0:
        return QPainterPath(pp)
    kx, ky = w / br.width(), h / br.height()
    if keep:
        kx = ky = min(kx, ky)
    t = QTransform()
    t.translate(x + (w - br.width() * kx) / 2, y + (h - br.height() * ky) / 2)
    t.scale(kx, ky)
    t.translate(-br.left(), -br.top())
    return t.map(pp)


# ══════════════════════════════════════════════════════════════════════════ #
#  Готовые формы
# ══════════════════════════════════════════════════════════════════════════ #

SHAPES = ("rect", "square", "rounded", "circle", "ellipse", "squircle", "triangle", "diamond", "pentagon",
          "hexagon", "octagon", "ngon", "star", "heart", "blob", "flower", "wave", "gear", "drop", "leaf", "pill",
          "shield", "cross", "ring", "arch", "cloud", "moon", "badge", "parallelogram", "trapezoid", "svg",
          "points", "vinyl")


def _radial(cx, cy, rx, ry, fn, n=144, rot=0.0):
    pts = []
    for i in range(n):
        a = i / n * math.tau
        r = fn(a)
        aa = a + rot
        pts.append(QPointF(cx + math.cos(aa) * rx * r, cy + math.sin(aa) * ry * r))
    pp = QPainterPath(pts[0])
    for q in pts[1:]:
        pp.lineTo(q)
    pp.closeSubpath()
    return pp


def _poly(cx, cy, rx, ry, n, rot_deg=-90.0, inner=None):
    pp = QPainterPath()
    k = n * 2 if inner is not None else n
    for i in range(k):
        a = math.radians(rot_deg) + i * math.tau / k
        r = 1.0 if (inner is None or i % 2 == 0) else inner
        q = QPointF(cx + math.cos(a) * rx * r, cy + math.sin(a) * ry * r)
        if i == 0:
            pp.moveTo(q)
        else:
            pp.lineTo(q)
    pp.closeSubpath()
    return pp


def make_shape(kind, x, y, w, h, opts=None, noise=None, svg=None) -> QPainterPath:
    o = opts if isinstance(opts, dict) else {}
    x, y, w, h = _f(x), _f(y), _f(w), _f(h)
    k = str(kind or "rect").lower().strip()
    stretch = bool(o.get("stretch", False)) or k in ("ellipse",)
    cx, cy = x + w / 2, y + h / 2
    if stretch:
        rx, ry = w / 2, h / 2
    else:
        rx = ry = min(w, h) / 2
    sides = int(_f(o.get("sides", 6), 6))
    pp = QPainterPath()
    if k in ("rect",):
        pp.addRect(QRectF(x, y, w, h))
    elif k == "square":
        s = min(w, h)
        pp.addRect(QRectF(cx - s / 2, cy - s / 2, s, s))
    elif k == "rounded":
        s_w, s_h = (w, h) if stretch or o.get("fill", True) is False else (w, h)
        r = _f(o.get("radius", min(s_w, s_h) * 0.18))
        pp.addRoundedRect(QRectF(x, y, s_w, s_h), r, r)
    elif k in ("circle", "ellipse"):
        pp.addEllipse(QPointF(cx, cy), rx, ry)
    elif k == "squircle":
        nn = max(2.0, _f(o.get("n", 4.0), 4.0))
        pp = _radial(cx, cy, rx, ry, lambda a: (abs(math.cos(a)) ** nn + abs(math.sin(a)) ** nn) ** (-1 / nn),
                     n=160)
    elif k == "triangle":
        pp = _poly(cx, cy + ry * 0.12, rx * 1.08, ry * 1.08, 3)
    elif k == "diamond":
        pp = _poly(cx, cy, rx, ry, 4)
    elif k == "pentagon":
        pp = _poly(cx, cy, rx, ry, 5)
    elif k == "hexagon":
        pp = _poly(cx, cy, rx, ry, 6, -90.0 if not o.get("flat") else 0.0)
    elif k == "octagon":
        pp = _poly(cx, cy, rx, ry, 8, -112.5)
    elif k in ("ngon", "polygon"):
        pp = _poly(cx, cy, rx, ry, max(3, sides))
    elif k == "star":
        pts = max(2, int(_f(o.get("points", o.get("sides", 5)), 5)))
        pp = _poly(cx, cy, rx, ry, pts, -90.0, max(0.05, min(1.0, _f(o.get("inner", 0.5), 0.5))))
        if _f(o.get("round", 0)) > 0:
            pp = _rounded_poly(pp, _f(o.get("round")))
    elif k == "badge":
        pts = max(6, int(_f(o.get("points", 14), 14)))
        pp = _radial(cx, cy, rx, ry, lambda a: 0.9 + 0.1 * math.cos(pts * a), n=pts * 12)
    elif k == "heart":
        raw = []
        for i in range(160):
            t = i / 160 * math.tau
            hx = 16 * math.sin(t) ** 3
            hy = -(13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t))
            raw.append(QPointF(hx, hy))
        hp = QPainterPath(raw[0])
        for q in raw[1:]:
            hp.lineTo(q)
        hp.closeSubpath()
        pp = fit_path(hp, cx - rx, cy - ry, 2 * rx, 2 * ry, True)
    elif k == "blob":
        amp = max(0.0, min(0.6, _f(o.get("wobble", 0.18), 0.18)))
        tt = _f(o.get("t", 0.0))
        seed = _f(o.get("seed", 0.0))
        nz = noise or (lambda a, b, c: 0.0)
        pts = []
        n = 48
        for i in range(n):
            a = i / n * math.tau
            r = 1.0 - amp + amp * (nz(math.cos(a) * 1.3 + seed, math.sin(a) * 1.3 + seed, tt) + 1.0) / 2 * 1.6
            r = max(0.2, min(1.0, r))
            pts.append(QPointF(cx + math.cos(a) * rx * r, cy + math.sin(a) * ry * r))
        _catmull_into(pp, pts, True, 1.0)
    elif k == "flower":
        pet = max(2, int(_f(o.get("petals", o.get("sides", 6)), 6)))
        d = max(0.05, min(0.9, _f(o.get("depth", 0.3), 0.3)))
        pp = _radial(cx, cy, rx, ry, lambda a: (1 - d) + d * abs(math.cos(pet * a / 2)), n=pet * 40,
                     rot=-math.pi / 2)
    elif k == "wave":
        nw = max(3, int(_f(o.get("waves", o.get("sides", 12)), 12)))
        amp = max(0.0, min(0.4, _f(o.get("amp", 0.05), 0.05)))
        ph = _f(o.get("phase", 0.0))
        pp = _radial(cx, cy, rx, ry, lambda a: 1 - amp + amp * math.sin(nw * a + ph), n=nw * 24)
    elif k == "gear":
        teeth = max(4, int(_f(o.get("teeth", o.get("sides", 12)), 12)))
        depth = max(0.05, min(0.5, _f(o.get("depth", 0.16), 0.16)))
        pts = []
        for i in range(teeth * 4):
            a = i / (teeth * 4) * math.tau - math.pi / 2
            r = 1.0 if (i % 4) in (1, 2) else 1.0 - depth
            pts.append(QPointF(cx + math.cos(a) * rx * r, cy + math.sin(a) * ry * r))
        pp = QPainterPath(pts[0])
        for q in pts[1:]:
            pp.lineTo(q)
        pp.closeSubpath()
        hole = _f(o.get("hole", 0.25))
        if hole > 0:
            hp = QPainterPath()
            hp.addEllipse(QPointF(cx, cy), rx * hole, ry * hole)
            pp = pp.subtracted(hp)
    elif k == "drop":
        pp.moveTo(cx, cy - ry)
        pp.cubicTo(cx + rx * 0.2, cy - ry * 0.55, cx + rx * 0.85, cy - ry * 0.05, cx + rx * 0.85, cy + ry * 0.3)
        pp.cubicTo(cx + rx * 0.85, cy + ry * 0.75, cx + rx * 0.45, cy + ry, cx, cy + ry)
        pp.cubicTo(cx - rx * 0.45, cy + ry, cx - rx * 0.85, cy + ry * 0.75, cx - rx * 0.85, cy + ry * 0.3)
        pp.cubicTo(cx - rx * 0.85, cy - ry * 0.05, cx - rx * 0.2, cy - ry * 0.55, cx, cy - ry)
        pp.closeSubpath()
    elif k == "leaf":
        pp.moveTo(cx - rx, cy + ry)
        pp.cubicTo(cx - rx, cy - ry * 0.4, cx - rx * 0.1, cy - ry, cx + rx, cy - ry)
        pp.cubicTo(cx + rx, cy + ry * 0.4, cx + rx * 0.1, cy + ry, cx - rx, cy + ry)
        pp.closeSubpath()
    elif k == "pill":
        r = min(w, h) / 2
        pp.addRoundedRect(QRectF(x, y, w, h), r, r)
    elif k == "shield":
        pp.moveTo(cx, cy - ry)
        pp.lineTo(cx + rx * 0.9, cy - ry * 0.7)
        pp.cubicTo(cx + rx * 0.9, cy + ry * 0.2, cx + rx * 0.5, cy + ry * 0.7, cx, cy + ry)
        pp.cubicTo(cx - rx * 0.5, cy + ry * 0.7, cx - rx * 0.9, cy + ry * 0.2, cx - rx * 0.9, cy - ry * 0.7)
        pp.closeSubpath()
    elif k == "cross":
        t = max(0.1, min(0.9, _f(o.get("thick", 0.34), 0.34)))
        a = QPainterPath()
        a.addRect(QRectF(cx - rx * t, cy - ry, 2 * rx * t, 2 * ry))
        b = QPainterPath()
        b.addRect(QRectF(cx - rx, cy - ry * t, 2 * rx, 2 * ry * t))
        pp = a.united(b)
    elif k in ("ring", "vinyl"):
        inner = max(0.0, min(0.95, _f(o.get("inner", 0.55 if k == "ring" else 0.12), 0.5)))
        pp.addEllipse(QPointF(cx, cy), rx, ry)
        hp = QPainterPath()
        hp.addEllipse(QPointF(cx, cy), rx * inner, ry * inner)
        pp = pp.subtracted(hp)
    elif k == "arch":
        pp.moveTo(x, y + h)
        pp.lineTo(x, y + w / 2)
        pp.arcTo(QRectF(x, y, w, w), 180, -180)
        pp.lineTo(x + w, y + h)
        pp.closeSubpath()
    elif k == "cloud":
        for (fx, fy, fr) in ((0.3, 0.6, 0.28), (0.5, 0.42, 0.34), (0.72, 0.58, 0.27), (0.5, 0.66, 0.26)):
            c = QPainterPath()
            c.addEllipse(QPointF(x + w * fx, y + h * fy), min(w, h) * fr, min(w, h) * fr)
            pp = pp.united(c)
    elif k == "moon":
        pp.addEllipse(QPointF(cx, cy), rx, ry)
        hp = QPainterPath()
        off = _f(o.get("phase", 0.35), 0.35)
        hp.addEllipse(QPointF(cx + rx * off * 2, cy - ry * off * 0.6), rx * 0.95, ry * 0.95)
        pp = pp.subtracted(hp)
    elif k == "parallelogram":
        sk = _f(o.get("skew", 0.2), 0.2) * w
        pp.moveTo(x + sk, y)
        pp.lineTo(x + w, y)
        pp.lineTo(x + w - sk, y + h)
        pp.lineTo(x, y + h)
        pp.closeSubpath()
    elif k == "trapezoid":
        sk = _f(o.get("skew", 0.2), 0.2) * w
        pp.moveTo(x + sk, y)
        pp.lineTo(x + w - sk, y)
        pp.lineTo(x + w, y + h)
        pp.lineTo(x, y + h)
        pp.closeSubpath()
    elif k == "svg":
        d = str(o.get("d") or "")
        if svg is not None and d:
            pp = fit_path(svg(d), x, y, w, h, not stretch)
    elif k == "points":
        pts = _points(o.get("points") or [])
        if len(pts) >= 3:
            # доли 0…1 — относительно прямоугольника, иначе пиксели
            frac = all(abs(q.x()) <= 1.0 and abs(q.y()) <= 1.0 for q in pts)
            pts = [QPointF(x + q.x() * w, y + q.y() * h) if frac else q for q in pts]
            if o.get("smooth"):
                _catmull_into(pp, pts, True, 0.5)
            else:
                pp.moveTo(pts[0])
                for q in pts[1:]:
                    pp.lineTo(q)
                pp.closeSubpath()
    else:
        raise EchoError(f"неизвестная форма «{kind}» (есть: {', '.join(SHAPES)})")
    rot = _f(o.get("rot", 0.0))
    if rot:
        t = QTransform().translate(cx, cy).rotate(rot).translate(-cx, -cy)
        pp = t.map(pp)
    return pp


def _rounded_poly(pp: QPainterPath, r):
    """Скруглить углы многоугольника (обводкой и подрезкой)."""
    st = QPainterPathStroker()
    st.setWidth(max(0.1, r * 2))
    st.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pp.united(st.createStroke(pp)).simplified() if r > 0 else pp


# ══════════════════════════════════════════════════════════════════════════ #
#  Холст
# ══════════════════════════════════════════════════════════════════════════ #

class ECanvas:
    """Картинка в памяти, в которую можно рисовать (draw_on) — содержимое сохраняется между кадрами."""
    echo_type = "холст"
    echo_kind = "canvas"
    __slots__ = ("img", "gen")

    def __init__(self, w, h):
        w, h = max(1, min(4096, int(_f(w, 1)))), max(1, min(4096, int(_f(h, 1))))
        self.img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        self.img.fill(0)
        self.gen = 0

    def __repr__(self):
        return f"<холст {self.img.width()}×{self.img.height()}>"

    def clear(self, color=None, to_qcolor=None):
        if color is None:
            self.img.fill(0)
        else:
            self.img.fill(to_qcolor(color) if to_qcolor else QColor(str(color)))
        self.gen += 1
        return self

    def pixel(self, x, y, c):
        x, y = int(_f(x)), int(_f(y))
        if 0 <= x < self.img.width() and 0 <= y < self.img.height():
            self.img.setPixelColor(x, y, c)
            self.gen += 1

    def get(self, x, y):
        x, y = int(_f(x)), int(_f(y))
        if 0 <= x < self.img.width() and 0 <= y < self.img.height():
            return self.img.pixelColor(x, y)
        return QColor(0, 0, 0, 0)

    def resize(self, w, h):
        w, h = max(1, min(4096, int(_f(w, 1)))), max(1, min(4096, int(_f(h, 1))))
        if (w, h) != (self.img.width(), self.img.height()):
            old = self.img
            self.img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
            self.img.fill(0)
            from PyQt6.QtGui import QPainter
            p = QPainter(self.img)
            p.drawImage(0, 0, old)
            p.end()
            self.gen += 1
        return self

    def save(self, path):
        return bool(self.img.save(str(path)))

    def echo_get(self, name):
        if name in ("w", "width"):
            return self.img.width()
        if name in ("h", "height"):
            return self.img.height()
        if name in ("save", "resize", "get"):
            return getattr(self, name)
        raise EchoError(f"у холста нет «{name}» (есть: w, h, get(x, y), resize(w, h), save(путь))")
