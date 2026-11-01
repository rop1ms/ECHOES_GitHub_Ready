# net_tastemap.py
"""
Карта вкусов друзей: кто к кому ближе по музыке.

Каждый друг — кружок с аватаркой; чем похожее вкусы (жанры и исполнители, та же совместимость, что в
профиле), тем ближе кружки и толще линия между ними. Раскладка — «пружины»: похожие притягиваются,
непохожие расходятся. Наведение — совместимость и общее; клик — профиль друга.
Вкусы друзей приходят вместе с их «я в сети» (если они делятся вкусом в настройках).
"""
from __future__ import annotations

import json
import math
import random

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QDialog, QLabel, QVBoxLayout, QWidget


def gather(net):
    """[(onion, имя, вкус)] — я и друзья, у кого известен вкус."""
    from net_page import taste_from_stats
    nd = net.node
    mine = getattr(net, "_my_taste", None) or taste_from_stats(net.win)
    out = [(nd.me.onion, nd._name() + " (вы)", mine)] if mine else []
    for c in nd.store.contacts("friend"):
        try:
            t = json.loads(c.get("taste") or "null")
        except ValueError:
            t = None
        if t:
            out.append((c["onion"], c.get("name") or c["onion"][:8], t))
    return out


def similarity(people):
    from net_core import taste_match
    n = len(people)
    sim = [[0.0] * n for _ in range(n)]
    common = {}
    for i in range(n):
        for j in range(i + 1, n):
            pct, com = taste_match(people[i][2], people[j][2])
            sim[i][j] = sim[j][i] = pct / 100.0
            common[(i, j)] = com
    return sim, common


def layout(sim, iters=300, seed=7):
    """Пружинная раскладка в квадрате [-1, 1]: желаемое расстояние = 1.6 · (1 − похожесть)."""
    n = len(sim)
    rnd = random.Random(seed)
    pos = [[math.cos(2 * math.pi * i / max(1, n)) * 0.8 + rnd.uniform(-0.05, 0.05),
            math.sin(2 * math.pi * i / max(1, n)) * 0.8 + rnd.uniform(-0.05, 0.05)] for i in range(n)]
    for it in range(iters):
        step = 0.08 * (1 - it / iters) + 0.005
        for i in range(n):
            fx = fy = 0.0
            for j in range(n):
                if i == j:
                    continue
                dx, dy = pos[i][0] - pos[j][0], pos[i][1] - pos[j][1]
                d = math.hypot(dx, dy) or 1e-3
                want = 0.25 + 1.4 * (1 - sim[i][j])
                f = (want - d) / d
                fx += dx * f
                fy += dy * f
            pos[i][0] += step * fx / max(1, n - 1)
            pos[i][1] += step * fy / max(1, n - 1)
    xs = [p[0] for p in pos] or [0]
    ys = [p[1] for p in pos] or [0]
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    sc = max(max(xs) - min(xs), max(ys) - min(ys), 1e-3) / 2
    return [((p[0] - cx) / sc, (p[1] - cy) / sc) for p in pos]


class TasteMap(QWidget):
    def __init__(self, net, parent=None):
        super().__init__(parent)
        self.net = net
        self.people = gather(net)
        self.sim, self.common = similarity(self.people)
        self.pos = layout(self.sim) if len(self.people) > 1 else [(0.0, 0.0)] * len(self.people)
        self.hover = -1
        self.setMinimumSize(560, 460)
        self.setMouseTracking(True)
        self._pix = {}
        self._t = 0.0
        self.tm = QTimer(self)
        self.tm.setInterval(16)
        self.tm.timeout.connect(self._grow)
        self.tm.start()

    def _grow(self):
        self._t = min(1.0, self._t + 0.04)
        self.update()
        if self._t >= 1.0:
            self.tm.stop()

    def _pt(self, i) -> QPointF:
        W, H = self.width(), self.height()
        r = min(W, H) / 2 - 60
        k = 1 - (1 - self._t) ** 3
        x, y = self.pos[i]
        return QPointF(W / 2 + x * r * k, H / 2 + y * r * k)

    def _at(self, p):
        for i in range(len(self.people)):
            if (self._pt(i) - p).manhattanLength() < 30:
                return i
        return -1

    def mouseMoveEvent(self, e):
        h = self._at(e.position())
        if h != self.hover:
            self.hover = h
            self.setCursor(Qt.CursorShape.PointingHandCursor if h > 0 else Qt.CursorShape.ArrowCursor)
            self.update()

    def mouseReleaseEvent(self, e):
        i = self._at(e.position())
        if i > 0 or (i == 0 and self.people[0][0] != self.net.node.me.onion):
            w = self.window()
            if isinstance(w, QDialog):
                w.accept()
            self.net.open_profile(self.people[i][0])

    def _avatar(self, i, size):
        if i not in self._pix:
            o, name, _t = self.people[i]
            self._pix[i] = self.net._av(o, name.replace(" (вы)", ""), size)
        return self._pix[i]

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        n = len(self.people)
        if n < 2:
            p.setPen(QColor(255, 255, 255, 160))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "Пока мало данных: карта появится, когда у друзей будет известен вкус\n"
                       "(они должны быть в сети и делиться вкусом в настройках).")
            p.end()
            return
        try:
            import ya_decor
            acc = ya_decor.base_accent()
        except Exception:                                      # noqa: BLE001
            acc = QColor("#ffdb1a")
        for i in range(n):                                     # линии между похожими
            for j in range(i + 1, n):
                s = self.sim[i][j]
                if s < 0.15:
                    continue
                hot = self.hover in (i, j)
                c = QColor(acc) if hot else QColor(255, 255, 255)
                c.setAlpha(int((200 if hot else 90) * s))
                p.setPen(QPen(c, 1 + 5 * s))
                p.drawLine(self._pt(i), self._pt(j))
        f = QFont()
        f.setPixelSize(12)
        f.setBold(True)
        p.setFont(f)
        for i in range(n):
            c = self._pt(i)
            me = self.people[i][0] == self.net.node.me.onion
            R = 26 if me else 22
            if self.hover == i or me:
                p.setPen(QPen(acc, 2.5))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(c, R + 3, R + 3)
            pm = self._avatar(i, R * 2)
            p.drawPixmap(QRectF(c.x() - R, c.y() - R, 2 * R, 2 * R), pm, QRectF(pm.rect()))
            p.setPen(QColor(255, 255, 255, 230))
            p.drawText(QRectF(c.x() - 80, c.y() + R + 4, 160, 18), Qt.AlignmentFlag.AlignCenter, self.people[i][1])
        if self.hover >= 0:                                    # подсказка: с кем совпадает сильнее всего
            i = self.hover
            best = sorted(((self.sim[i][j], j) for j in range(n) if j != i), reverse=True)[:3]
            lines = [self.people[i][1]] + [f"{self.people[j][1]}: {int(s * 100)} %" +
                                          (f" · {', '.join(self.common.get((min(i, j), max(i, j)), [])[:3])}"
                                           if self.common.get((min(i, j), max(i, j))) else "") for s, j in best]
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(15, 15, 18, 235))
            box = QRectF(14, self.height() - 36 - 20 * len(lines), 380, 22 + 20 * len(lines))   # внизу — не закрывает
            p.drawRoundedRect(box, 12, 12)
            p.setPen(QColor(255, 255, 255))
            for k, t in enumerate(lines):
                p.drawText(QRectF(28, box.y() + 10 + 20 * k, 360, 20), Qt.AlignmentFlag.AlignLeft, t)
        p.end()


class TasteMapDialog(QDialog):
    def __init__(self, net):
        super().__init__(net.win)
        import net_frames
        self.setWindowTitle("Карта вкусов друзей")
        self.setStyleSheet(net_frames._QSS)
        self.resize(760, 640)
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 16)
        t = QLabel("Карта вкусов друзей")
        t.setStyleSheet("font-size:22px;font-weight:900;")
        v.addWidget(t)
        s = QLabel("Ближе и толще линия — похожее музыка (жанры и исполнители). Наведите — совпадения, клик — профиль.")
        s.setObjectName("Sub")
        s.setWordWrap(True)
        v.addWidget(s)
        self.map = TasteMap(net)
        v.addWidget(self.map, 1)


def open_map(net):
    TasteMapDialog(net).exec()
