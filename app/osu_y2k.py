# osu_y2k.py
"""Тема esu!: необязательное оформление в стиле Y2K + свои хитсаунды.

Подключается при запуске (main.py → install()) и подменяет отрисовку osu_theme / osu_skin снаружи,
не меняя их файлов. Включается настройкой «Оформление» темы esu! (по умолчанию — классика, как в
самой osu!).

Y2K: тёмно-синий фон с тонкой перспективной сеткой, редкие мерцающие звёздочки ✦ вместо треугольников,
хромированный шар с перламутровым ободком вместо «печеньки», глянцевые таблетки-кнопки, ноты —
стеклянные пузыри с радужным ободком, пастельные цвета комбо, курсор-звёздочка, LCD-подписи.
"""
from __future__ import annotations

import math
import os
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np
from PyQt6.QtCore import Qt, QPointF, QRectF
from PyQt6.QtGui import (QBrush, QColor, QConicalGradient, QFont, QLinearGradient, QPainter, QPainterPath, QPen,
                         QPixmap, QPolygonF, QRadialGradient)
from PyQt6.QtWidgets import (QDialog, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
                             QComboBox)

DATA = Path(os.path.expanduser("~")) / ".neon_player"
HS_DIR = DATA / "hitsounds"

# ── палитра ──
INK = QColor(8, 9, 20)
NAVY = QColor(14, 16, 38)
LILAC = QColor(199, 179, 255)
BABY = QColor(166, 233, 255)
PINKY = QColor(255, 194, 236)
MINT = QColor(184, 255, 224)
SILVER = QColor(220, 226, 238)
PASTEL_COMBO = [QColor(166, 233, 255), QColor(255, 194, 236), QColor(199, 179, 255), QColor(184, 255, 224)]
WIDE = "Verdana"
LCD = "Consolas"


def _on(shell_or_none=None) -> bool:
    """Включён ли Y2K (настройка темы osu «style»)."""
    try:
        S = _STATE.get("settings")
        return (S or {}).get("style", "classic") == "y2k"
    except Exception:                                      # noqa: BLE001
        return True


_STATE = {"settings": None, "orig": {}}


def yfont(px, weight=QFont.Weight.Bold, family=WIDE, spacing=0.0):
    f = QFont(family)
    f.setPixelSize(max(6, int(px)))
    f.setWeight(weight)
    if spacing:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return f


def ytext(p, r, s, px, color=QColor(255, 255, 255), weight=QFont.Weight.Bold, align=None, family=WIDE, spacing=0.0,
          elide=True):
    p.setFont(yfont(px, weight, family, spacing))
    p.setPen(color)
    if elide:
        s = p.fontMetrics().elidedText(s, Qt.TextElideMode.ElideRight, int(r.width()))
    p.drawText(r, align or (Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), s)


def chrome(r: QRectF, vertical=True):
    g = QLinearGradient(r.topLeft(), r.bottomLeft() if vertical else r.topRight())
    g.setColorAt(0.0, QColor(255, 255, 255))
    g.setColorAt(0.42, QColor(196, 204, 220))
    g.setColorAt(0.5, QColor(118, 126, 148))
    g.setColorAt(0.62, QColor(214, 220, 234))
    g.setColorAt(1.0, QColor(150, 158, 178))
    return QBrush(g)


def iridescent(c: QPointF, angle=0.0, alpha=255):
    g = QConicalGradient(c, angle)
    for k, col in enumerate((BABY, PINKY, LILAC, MINT, BABY)):
        q = QColor(col)
        q.setAlpha(alpha)
        g.setColorAt(k / 4, q)
    return QBrush(g)


def sparkle(p, x, y, r, color, alpha=1.0):
    """Четырёхлучевая звёздочка ✦ (символ Y2K)."""
    c = QColor(color)
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    path = QPainterPath()
    path.moveTo(x, y - r)
    path.quadTo(x, y, x + r, y)
    path.quadTo(x, y, x, y + r)
    path.quadTo(x, y, x - r, y)
    path.quadTo(x, y, x, y - r)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c)
    p.drawPath(path)


def pill(p, r: QRectF, hov=False, active=False, label="", px=14, icon=""):
    """Глянцевая таблетка: стекло + хромовая обводка + блик сверху."""
    rad = r.height() / 2
    body = QLinearGradient(r.topLeft(), r.bottomLeft())
    if active:
        body.setColorAt(0, QColor(232, 222, 255, 240))
        body.setColorAt(1, QColor(168, 150, 236, 240))
    else:
        body.setColorAt(0, QColor(255, 255, 255, 46 if hov else 26))
        body.setColorAt(1, QColor(255, 255, 255, 12 if hov else 6))
    p.setBrush(QBrush(body))
    p.setPen(QPen(QBrush(chrome(r)), 1.4 if hov or active else 1.0))
    p.drawRoundedRect(r, rad, rad)
    hl = QRectF(r.left() + rad * 0.5, r.top() + 2, r.width() - rad, r.height() * 0.42)
    g = QLinearGradient(hl.topLeft(), hl.bottomLeft())
    g.setColorAt(0, QColor(255, 255, 255, 90 if hov or active else 55))
    g.setColorAt(1, QColor(255, 255, 255, 0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(g))
    p.drawRoundedRect(hl, hl.height() / 2, hl.height() / 2)
    if label:
        fg = QColor(30, 22, 60) if active else QColor(240, 244, 255)
        ytext(p, r, (icon + "  " if icon else "") + label, px, fg, QFont.Weight.Bold,
              Qt.AlignmentFlag.AlignCenter, WIDE, 0.6, elide=False)


def orb(p, cx, cy, R, label="esu!", beat=0.0, t=0.0):
    """Хромированный шар с перламутровым ободком вместо «печеньки»."""
    p.setPen(Qt.PenStyle.NoPen)
    halo = QRadialGradient(QPointF(cx, cy), R * 1.35)
    halo.setColorAt(0.70, QColor(199, 179, 255, int(50 + 70 * beat)))
    halo.setColorAt(1.0, QColor(199, 179, 255, 0))
    p.setBrush(QBrush(halo))
    p.drawEllipse(QPointF(cx, cy), R * 1.35, R * 1.35)
    p.setBrush(iridescent(QPointF(cx, cy), (t * 40) % 360))
    p.drawEllipse(QPointF(cx, cy), R, R)
    p.setBrush(chrome(QRectF(cx - R, cy - R, 2 * R, 2 * R)))
    p.drawEllipse(QPointF(cx, cy), R * 0.94, R * 0.94)
    body = QRadialGradient(QPointF(cx - R * 0.25, cy - R * 0.3), R * 1.1)
    body.setColorAt(0.0, QColor(46, 44, 92))
    body.setColorAt(0.7, QColor(14, 14, 34))
    body.setColorAt(1.0, QColor(6, 6, 16))
    p.setBrush(QBrush(body))
    p.drawEllipse(QPointF(cx, cy), R * 0.86, R * 0.86)
    rim = QRadialGradient(QPointF(cx, cy), R * 0.86)
    rim.setColorAt(0.78, QColor(166, 233, 255, 0))
    rim.setColorAt(0.97, QColor(166, 233, 255, int(70 + 90 * beat)))
    rim.setColorAt(1.0, QColor(166, 233, 255, 0))
    p.setBrush(QBrush(rim))
    p.drawEllipse(QPointF(cx, cy), R * 0.86, R * 0.86)
    hl = QRadialGradient(QPointF(cx - R * 0.2, cy - R * 0.55), R * 0.65)
    hl.setColorAt(0, QColor(255, 255, 255, 120))
    hl.setColorAt(1, QColor(255, 255, 255, 0))
    p.setBrush(QBrush(hl))
    p.drawEllipse(QPointF(cx - R * 0.08, cy - R * 0.42), R * 0.62, R * 0.32)
    if label:
        f = yfont(R * 0.42, QFont.Weight.Black, WIDE, -R * 0.01)
        path = QPainterPath()
        fm_w = QFont(f)
        from PyQt6.QtGui import QFontMetricsF
        fm = QFontMetricsF(fm_w)
        w = fm.horizontalAdvance(label)
        path.addText(QPointF(cx - w / 2, cy + fm.ascent() * 0.36), f, label)
        p.setBrush(chrome(path.boundingRect()))
        p.setPen(QPen(QColor(20, 20, 40, 160), max(1.0, R * 0.012)))
        p.drawPath(path)
    sparkle(p, cx + R * 0.52, cy - R * 0.58, R * 0.09, QColor(255, 255, 255), 0.6 + 0.4 * math.sin(t * 3))


_BG_CACHE: dict = {}


def y2k_bg(p, pm, W, H, dim=0.55, par=(0.0, 0.0)):
    """Тёмно-синий фон, тусклая обложка, мягкие перламутровые пятна и перспективная сетка внизу.
    Готовый фон кэшируется: перерисовывается, только когда сдвинулся параллакс (на 2 px), сменились
    обложка, размер или затемнение — раньше три полноэкранных градиента и сетка считались каждый кадр."""
    q = (round(par[0] / 2.0) * 2.0, round(par[1] / 2.0) * 2.0)
    dev = p.device()
    try:
        dpr = float(dev.devicePixelRatioF())
    except Exception:                                      # noqa: BLE001
        dpr = 1.0
    key = (int(W), int(H), pm.cacheKey() if pm is not None else 0, q, round(float(dim), 3), dpr)
    hit = _BG_CACHE.get("bg")
    if hit is None or hit[0] != key:
        img = QPixmap(max(1, int(math.ceil(W * dpr))), max(1, int(math.ceil(H * dpr))))
        img.setDevicePixelRatio(dpr)
        img.fill(QColor(0, 0, 0))
        q2 = QPainter(img)
        q2.setRenderHint(QPainter.RenderHint.Antialiasing)
        q2.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        _y2k_bg_draw(q2, pm, W, H, dim, q)
        q2.end()
        hit = _BG_CACHE["bg"] = (key, img)
    p.drawPixmap(QPointF(0, 0), hit[1])
    # состояние рисования — как после прямой отрисовки (вызывающий код мог на него полагаться)
    p.setPen(QPen(QColor(166, 233, 255, 26), 1))
    p.setBrush(QBrush(QColor(PINKY.red(), PINKY.green(), PINKY.blue(), 0)))


def _y2k_bg_draw(p, pm, W, H, dim=0.55, par=(0.0, 0.0)):
    g = QLinearGradient(0, 0, 0, H)
    g.setColorAt(0, INK)
    g.setColorAt(1, NAVY)
    p.fillRect(QRectF(0, 0, W, H), QBrush(g))
    if pm is not None:
        d = pm.devicePixelRatio()
        w, h = pm.width() / d, pm.height() / d
        p.setOpacity(0.28)
        p.drawPixmap(QRectF((W - w) / 2 + par[0], (H - h) / 2 + par[1], w, h), pm, QRectF(pm.rect()))
        p.setOpacity(1.0)
    p.setPen(Qt.PenStyle.NoPen)
    for cx, cy, rr, col in ((0.18, 0.2, 0.55, LILAC), (0.85, 0.75, 0.5, BABY), (0.6, 0.05, 0.35, PINKY)):
        gl = QRadialGradient(QPointF(W * cx + par[0] * 0.5, H * cy + par[1] * 0.5), max(W, H) * rr)
        c0 = QColor(col)
        c0.setAlpha(38)
        c1 = QColor(col)
        c1.setAlpha(0)
        gl.setColorAt(0, c0)
        gl.setColorAt(1, c1)
        p.setBrush(QBrush(gl))
        p.drawRect(QRectF(0, 0, W, H))
    # перспективная сетка (горизонт на 62 % высоты)
    hy = H * 0.62
    p.setPen(QPen(QColor(166, 233, 255, 26), 1))
    for i in range(1, 14):
        k = (i / 13) ** 2.2
        y = hy + (H - hy) * k
        p.drawLine(QPointF(0, y), QPointF(W, y))
    for i in range(-16, 17):
        x = W / 2 + i * W * 0.06
        p.drawLine(QPointF(W / 2 + i * W * 0.012, hy), QPointF(x + (x - W / 2) * 1.5, H))
    hz = QLinearGradient(0, hy - 30, 0, hy + 30)
    hz.setColorAt(0, QColor(199, 179, 255, 0))
    hz.setColorAt(0.5, QColor(199, 179, 255, 40))
    hz.setColorAt(1, QColor(199, 179, 255, 0))
    p.fillRect(QRectF(0, hy - 30, W, 60), QBrush(hz))
    p.fillRect(QRectF(0, 0, W, H), QColor(0, 0, 0, int(255 * max(0.0, dim - 0.35))))


# ══════════════════════════════════════════════════════════════════════════ #
#  Подмена отрисовки osu_theme
# ══════════════════════════════════════════════════════════════════════════ #

def _menu_paint(self, e):
    O = _STATE["orig"]
    if not _on():
        return O["menu_paint"](self, e)
    import osu_theme as T
    p = QPainter(self)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    W, H = self.width(), self.height()
    now = time.perf_counter()
    t = self.shell.cur_track()
    par = (0.0, 0.0)
    if self.shell.S().get("menu_parallax", True):
        par = (-(self.mouse.x() / max(1, W) - 0.5) * W * 0.02, -(self.mouse.y() / max(1, H) - 0.5) * H * 0.02)
    y2k_bg(p, self.bg.get(self.shell.cover_of(t), W, H, self.devicePixelRatioF(), blur=True), W, H, 0.5, par)
    # редкие мерцающие звёздочки вместо треугольников
    for k, (x, y, s, _v, a) in enumerate(self.tris):
        if k % 3:
            continue
        tw = 0.35 + 0.65 * abs(math.sin(now * (0.8 + (k % 5) * 0.3) + k))
        sparkle(p, x, y, max(2.0, s * 0.12) * (1 + 0.6 * self.beat), (BABY, PINKY, LILAC, SILVER)[k % 4], tw * 0.75)
    cx, cy, R = self._cookie()
    # пункты меню — глянцевые таблетки
    if self.ek > 0.01:
        p.setOpacity(self.ek)
        for i, (name, _icon, _col), r in self._items():
            hov = self.hover == ("item", i)
            rr = QRectF(r.left(), r.center().y() - r.height() * 0.2, r.width(), r.height() * 0.4)
            pill(p, rr, hov, False, name, rr.height() * 0.3)
        p.setOpacity(1.0)
    # визуализатор — тонкое пунктирное кольцо
    n = len(self.vis)
    for i in range(0, n, 2):
        v = float(self.vis[i])
        if v < 0.04:
            continue
        a = self.vis_rot + i / n * 2 * math.pi
        L = R * 0.35 * v
        c, s = math.cos(a), math.sin(a)
        x0, y0 = cx + c * R * 1.04, cy + s * R * 1.04
        p.setPen(QPen(QColor(166, 233, 255, 110), max(1.5, R * 0.012), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(x0, y0), QPointF(x0 + c * L, y0 + s * L))
    intro = 1.0
    if self.intro_t is not None:
        e2 = (now - self.intro_t) / 1.2
        if e2 < 1:
            intro = 1 - (1 - e2) ** 3 if e2 > 0 else 0.0
    sc = (1 + 0.03 * self.beat + 0.04 * self.cookie_hover) * max(0.05, intro)
    orb(p, cx, cy, R * sc, "esu!", self.beat, now)
    self._draw_toolbar(p, W, H, t)
    self._draw_user(p, W, H)
    ytext(p, QRectF(18, H - 32, W * 0.62, 22), "tip · " + self.tip, 11, QColor(220, 228, 255, 150),
          QFont.Weight.Normal, family=LCD)
    ytext(p, QRectF(W - 330, H - 32, 312, 22), "ECHOES · esu! y2k", 11, QColor(220, 228, 255, 120),
          QFont.Weight.Normal, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, LCD, 1.5)
    e2 = now - self.vol_t
    if e2 < 1.6:
        self._draw_volume(p, W, H, 1.0 if e2 < 1.2 else 1 - (e2 - 1.2) / 0.4)
    if self.intro_t is not None and now - self.intro_t < 0.8:
        p.fillRect(self.rect(), QColor(0, 0, 0, int(255 * max(0.0, 1 - (now - self.intro_t) / 0.8))))
    p.end()


def _menu_toolbar(self, p, W, H, t):
    O = _STATE["orig"]
    if not _on():
        return O["menu_toolbar"](self, p, W, H, t)
    h = self._toolbar()
    p.fillRect(QRectF(0, 0, W, h), QColor(6, 7, 18, 150))
    p.setPen(QPen(QBrush(chrome(QRectF(0, h - 1, W, 1), False)), 1))
    p.drawLine(QPointF(0, h - 0.5), QPointF(W, h - 0.5))
    b = self._tb_buttons()
    playing = False
    try:
        playing = self.win.engine.is_playing()
    except Exception:                                      # noqa: BLE001
        pass
    import osu_skin as SK
    for k, ico in (("settings", "settings"), ("home", "home"), ("prev", "prev"),
                   ("play", "pause" if playing else "play"), ("next", "next")):
        r = b[k]
        if self.hover == ("tb", k):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(199, 179, 255, 50))
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        c = r.center()
        SK.draw_icon(p, ico, QRectF(c.x() - h * 0.24, c.y() - h * 0.24, h * 0.48, h * 0.48), QColor(236, 240, 255))
    if "themes" in b:                                       # заметная кнопка смены темы плеера
        pill(p, b["themes"], self.hover == ("tb", "themes"), True, "Сменить тему ▾", 12)
    import osu_theme as T
    now_s = f"{(t or {}).get('artist', '')} — {T.track_title(t)}" if t else "тишина"
    ytext(p, QRectF(W * 0.25, 0, W - 140 - 140 - W * 0.25, h), now_s, 12, QColor(236, 240, 255, 210),
          QFont.Weight.Normal, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, LCD)
    ytext(p, QRectF(W - 130, 0, 118, h), time.strftime("%H:%M"), 16, BABY, QFont.Weight.Bold,
          Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, LCD, 2.0)


def _menu_user(self, p, W, H):
    O = _STATE["orig"]
    if not _on():
        return O["menu_user"](self, p, W, H)
    st = self._stats or {}
    x, y = 18, self._toolbar() + 16
    r = QRectF(x, y, 300, 74)
    p.setPen(QPen(QBrush(chrome(r)), 1))
    p.setBrush(QColor(255, 255, 255, 14))
    p.drawRoundedRect(r, 18, 18)
    ar = QRectF(x + 10, y + 10, 54, 54)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(iridescent(ar.center(), (time.perf_counter() * 30) % 360))
    p.drawEllipse(ar.adjusted(-2, -2, 2, 2))
    av = self._avatar_pm()
    p.save()
    path = QPainterPath()
    path.addEllipse(ar)
    p.setClipPath(path)
    if av is not None:
        p.drawPixmap(ar, av, QRectF(av.rect()))
    else:
        p.fillPath(path, QColor(20, 18, 44))
        ytext(p, ar, (self.shell.player_name()[:1] or "?").upper(), 24, SILVER, QFont.Weight.Black,
              Qt.AlignmentFlag.AlignCenter)
    p.restore()
    ytext(p, QRectF(x + 76, y + 8, 210, 22), self.shell.player_name(), 15, QColor(255, 255, 255), QFont.Weight.Bold)
    ytext(p, QRectF(x + 76, y + 30, 210, 16), f"{st.get('pp', 0):,.0f}pp  ·  {st.get('acc', 0) * 100:.2f}%  ·  "
          f"{st.get('plays', 0)} игр".replace(",", " "), 11, QColor(220, 228, 255, 190), QFont.Weight.Normal, family=LCD)
    lv = st.get("level", 1)
    ytext(p, QRectF(x + 76, y + 48, 40, 16), f"LV{lv}", 11, LILAC, QFont.Weight.Bold, family=LCD)
    bar = QRectF(x + 116, y + 54, 170, 5)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 255, 255, 30))
    p.drawRoundedRect(bar, 2.5, 2.5)
    fb = QRectF(bar.left(), bar.top(), bar.width() * st.get("frac", 0), bar.height())
    g = QLinearGradient(fb.topLeft(), fb.topRight())
    g.setColorAt(0, BABY)
    g.setColorAt(1, PINKY)
    p.setBrush(QBrush(g))
    p.drawRoundedRect(fb, 2.5, 2.5)


def _menu_cookie(self, p, cx, cy, R):
    if not _on():
        return _STATE["orig"]["menu_cookie"](self, p, cx, cy, R)
    orb(p, cx, cy, R, "esu!", getattr(self, "beat", 0.0), time.perf_counter())


def _sel_bottom(self, p, W, H, bot):
    O = _STATE["orig"]
    if not _on():
        return O["sel_bottom"](self, p, W, H, bot)
    import osu_skin as SK
    p.fillRect(QRectF(0, H - bot, W, bot), QColor(6, 7, 18, 190))
    p.setPen(QPen(QBrush(chrome(QRectF(0, H - bot, W, 1), False)), 1))
    p.drawLine(QPointF(0, H - bot), QPointF(W, H - bot))
    b = self._bottom_buttons()
    for key, label in (("back", "Назад"), ("mods", "Моды F1"), ("random", "Случайно F2"), ("options", "Карта F3"),
                       ("themes", "Темы ▾")):
        if key not in b:
            continue
        r = b[key]
        rr = QRectF(r.left() + 10, r.center().y() - r.height() * 0.32, r.width() - 14, r.height() * 0.64)
        pill(p, rr, self.hover == ("bottom", key), (key == "mods" and bool(self.mods)) or key == "themes", label,
             rr.height() * 0.32)
    x = b["themes" if "themes" in b else "options"].right() + 30
    for m in sorted(self.mods):
        pm = SK.mod_icon(m, bot * 0.42, self.devicePixelRatioF())
        p.drawPixmap(QPointF(x, H - bot / 2 - bot * 0.24), pm)
        x += pm.width() / pm.devicePixelRatio() + 4
    r = b["play"]
    hov = self.hover == ("bottom", "play")
    c = r.center()
    R = r.width() / 2 * (1.06 if hov else 1.0) * (1 + 0.03 * self.shell.pulse.beat)
    orb(p, c.x(), c.y(), R, "play", self.shell.pulse.beat, time.perf_counter())


def _sel_info(self, p, W, H, top):
    O = _STATE["orig"]
    if not _on():
        return O["sel_info"](self, p, W, H, top)
    import osu_theme as T
    import osu_mapgen as G
    t = self.cur()
    p.fillRect(QRectF(0, 0, W, top), QColor(6, 7, 18, 175))
    p.setPen(QPen(QBrush(chrome(QRectF(0, top - 1, W, 1), False)), 1))
    p.drawLine(QPointF(0, top - 0.5), QPointF(W, top - 0.5))
    self._btn_rects = {}
    m = self._map() if t is not None else None
    D = {"name": (self._diff_meta(t, self._cur_diff()).get("name") or "?") if t is not None and hasattr(
        self, "_diff_meta") else G.diff_info(self.diff)["name"]}
    if t is None:
        # строка поиска и выбор коллекции остаются — иначе не видно, что напечатано, и не вернуться
        ytext(p, QRectF(22, 10, W * 0.6, 40), self.empty_text(), 17)
    else:
        ytext(p, QRectF(22, 8, W * 0.6, top * 0.34), f"{T.track_title(t)}", top * 0.21, QColor(255, 255, 255),
              QFont.Weight.Black)
        ytext(p, QRectF(22, top * 0.38, W * 0.6, top * 0.18), f"{t.get('artist') or '—'}  ·  {D['name']}",
              top * 0.115, QColor(220, 228, 255, 200), QFont.Weight.Normal, family=LCD)
    if t is not None and m:
        rate = 1.5 if (self.mods & {"DT", "NC"}) else 0.75 if "HT" in self.mods else 1.0
        cs, ar, od, hp = G.apply_mods(m["cs"], m["ar"], m["od"], m["hp"], self.mods)
        ar2, od2 = G.effective_ar_od(ar, od, rate)
        ytext(p, QRectF(22, top * 0.6, W * 0.6, top * 0.16),
              f"{T.fmt_time(m['length'] / rate)}  ·  {m['bpm'] * rate:.0f} BPM  ·  ★ {m['stars']:.2f}  ·  "
              f"CS {cs:.1f}  AR {ar2:.1f}  OD {od2:.1f}  HP {hp:.1f}", top * 0.105, BABY, QFont.Weight.Normal, family=LCD)
    else:
        st = self._set()
        if st and st.get("state") == "busy":
            f, s = st.get("prog", (0.0, ""))
            ytext(p, QRectF(22, top * 0.58, W * 0.6, top * 0.18), f"модель слушает трек: {s}", top * 0.105,
                  QColor(220, 228, 255, 200), QFont.Weight.Normal, family=LCD)
            bar = QRectF(22, top * 0.8, W * 0.3, 5)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 30))
            p.drawRoundedRect(bar, 2.5, 2.5)
            g = QLinearGradient(bar.topLeft(), bar.topRight())
            g.setColorAt(0, BABY)
            g.setColorAt(1, PINKY)
            p.setBrush(QBrush(g))
            p.drawRoundedRect(QRectF(bar.left(), bar.top(), bar.width() * f, 5), 2.5, 2.5)
    r = QRectF(W * 0.64, 12, W * 0.34, top * 0.3)
    pill(p, r, False, False)
    ytext(p, r.adjusted(18, 0, -14, 0), ("поиск: " + self.search + ("▏" if int(time.perf_counter() * 2) % 2 else ""))
          if self.search else "печатайте для поиска…", top * 0.11,
          QColor(236, 240, 255, 230 if self.search else 140), QFont.Weight.Normal, family=LCD)
    sort_names = {"title": "название", "artist": "исполнитель", "length": "длина", "added": "добавлено"}
    r1 = QRectF(W * 0.64, top * 0.5, W * 0.165, top * 0.3)
    r2 = QRectF(W * 0.64 + W * 0.175, top * 0.5, W * 0.165, top * 0.3)
    for rr, label, key in ((r1, "сорт: " + sort_names[self.sort] + " ▾", "sort"),
                           (r2, (self.coll or "вся библиотека") + " ▾", "coll")):
        pill(p, rr, self.hover == ("btn", key), False, label, top * 0.095)
        self._btn_rects[key] = rr
    ytext(p, QRectF(W * 0.64, top * 0.82, W * 0.34, top * 0.16), T.n_tracks(len(self.tracks)), top * 0.085,
          QColor(220, 228, 255, 140), QFont.Weight.Normal, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
          LCD)


def _sel_carousel(self, p, W, H):
    O = _STATE["orig"]
    if not _on():
        return O["sel_carousel"](self, p, W, H)
    import osu_theme as T
    import osu_skin as SK
    import osu_mapgen as G
    ph, dh, gap = self._sizes()
    st = self._set()
    for kind, key, r in self._entries():
        if kind == "track":
            tr = self.tracks[key]
            sel = key == self.sel
            hov = self.hover == ("track", key)
            p.setBrush(QColor(255, 255, 255, 30 if sel else (20 if hov else 10)))
            p.setPen(QPen(QBrush(chrome(r)), 1.6) if sel else QPen(QColor(255, 255, 255, 40), 1))
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
            th = self._thumb(tr)
            d = r.height() - 12
            ar = QRectF(r.left() + 8, r.top() + 6, d, d)
            if th is not None:
                p.save()
                path = QPainterPath()
                path.addEllipse(ar)
                p.setClipPath(path)
                p.drawPixmap(ar, th, QRectF(0, 0, min(th.width(), th.height()), min(th.width(), th.height())))
                p.restore()
            else:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(iridescent(ar.center(), key * 30, 160))
                p.drawEllipse(ar)
            if sel:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(iridescent(ar.center(), (time.perf_counter() * 60) % 360), 2))
                p.drawEllipse(ar.adjusted(-2, -2, 2, 2))
            x = ar.right() + 14
            ytext(p, QRectF(x, r.top() + r.height() * 0.12, W - x - 30, r.height() * 0.42), T.track_title(tr),
                  r.height() * 0.24, QColor(255, 255, 255), QFont.Weight.Bold)
            ytext(p, QRectF(x, r.top() + r.height() * 0.52, W - x - 30, r.height() * 0.3), tr.get("artist") or "—",
                  r.height() * 0.17, QColor(220, 228, 255, 170), QFont.Weight.Normal, family=LCD)
            s2 = self.shell.sets.get(str(tr.get("path")))
            if s2 and s2.get("data"):
                xx = W - 40 - 16 * len(T.DIFF_KEYS) if hasattr(T, "DIFF_KEYS") else W - 120
                for k2 in getattr(T, "DIFF_KEYS", []):
                    m = s2["data"]["maps"].get(k2)
                    if m:
                        sparkle(p, xx, r.center().y(), 5, T.star_color(m["stars"]), 0.9)
                        xx += 16
        else:
            m = st["data"]["maps"].get(key) if st and st.get("data") else None
            sel = key == (self._cur_diff() if hasattr(self, "_cur_diff") else self.diff)
            hov = self.hover == ("diff", key)
            D = {"name": T.diff_name(m, key) if m else (self._diff_meta(self.cur(), key).get("name") or "?")}
            col = T.star_color(m["stars"]) if m else QColor(140, 140, 170)
            rr = QRectF(r.left(), r.top() + 2, r.width(), r.height() - 4)
            if sel:
                pill(p, rr, hov, True)
            else:
                p.setBrush(QColor(255, 255, 255, 16 if hov else 8))
                p.setPen(QPen(QColor(255, 255, 255, 34), 1))
                p.drawRoundedRect(rr, rr.height() / 2, rr.height() / 2)
            sparkle(p, rr.left() + 26, rr.center().y(), rr.height() * 0.22, col, 1.0)
            fg = QColor(30, 22, 60) if sel else QColor(240, 244, 255)
            ytext(p, QRectF(rr.left() + 48, rr.top(), W * 0.22, rr.height()), D["name"], rr.height() * 0.3, fg,
                  QFont.Weight.Bold)
            if m:
                ytext(p, QRectF(rr.left() + 48 + W * 0.13, rr.top(), 160, rr.height()), f"★ {m['stars']:.2f}",
                      rr.height() * 0.26, fg if sel else col, QFont.Weight.Bold, family=LCD)
            elif st and st.get("state") == "busy":
                f, s = st.get("prog", (0.0, ""))
                ytext(p, QRectF(rr.left() + 48 + W * 0.13, rr.top(), W * 0.3, rr.height()), f"{s} {int(f * 100)}%",
                      rr.height() * 0.22, QColor(fg.red(), fg.green(), fg.blue(), 190), QFont.Weight.Normal, family=LCD)
            elif st and st.get("state") == "error":
                ytext(p, QRectF(rr.left() + 48 + W * 0.13, rr.top(), W * 0.3, rr.height()),
                      "не удалось: " + st.get("err", ""), rr.height() * 0.22, QColor(255, 140, 160),
                      QFont.Weight.Normal, family=LCD)
    _ = SK


def _draw_bg(p, pm, W, H, dim=0.55, par=(0.0, 0.0), cache=None):
    if not _on():
        return _STATE["orig"]["draw_bg"](p, pm, W, H, dim, par, cache)
    y2k_bg(p, pm, W, H, dim, par)


# ── ноты: стеклянные пузыри ── #

def _skin_circle(self, col: QColor):
    if not _on():
        return _STATE["orig"]["skin_circle"](self, col)
    import osu_skin as SK
    R = self.R
    pad = R * 0.14
    s = R * 2 + pad * 2
    pm = SK._pm(s, s, self.dpr)
    p = SK._painter(pm)
    c = QPointF(s / 2, s / 2)
    p.setPen(Qt.PenStyle.NoPen)
    glow = QRadialGradient(c, R * 1.12)
    g0 = QColor(col)
    g0.setAlpha(70)
    g1 = QColor(col)
    g1.setAlpha(0)
    glow.setColorAt(0.8, g0)
    glow.setColorAt(1.0, g1)
    p.setBrush(QBrush(glow))
    p.drawEllipse(c, R * 1.12, R * 1.12)
    p.setBrush(iridescent(c, 35))
    p.drawEllipse(c, R * 0.95, R * 0.95)
    body = QRadialGradient(QPointF(c.x() - R * 0.2, c.y() - R * 0.25), R)
    b0 = SK.lighter(col, 0.55)
    b0.setAlpha(235)
    b1 = QColor(col)
    b1.setAlpha(220)
    b2 = SK.darker(col, 0.55)
    b2.setAlpha(235)
    body.setColorAt(0, b0)
    body.setColorAt(0.6, b1)
    body.setColorAt(1, b2)
    p.setBrush(QBrush(body))
    p.drawEllipse(c, R * 0.84, R * 0.84)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(255, 255, 255, 220), max(1.0, R * 0.035)))
    p.drawEllipse(c, R * 0.95, R * 0.95)
    hl = QRadialGradient(QPointF(c.x() - R * 0.15, c.y() - R * 0.5), R * 0.6)
    hl.setColorAt(0, QColor(255, 255, 255, 170))
    hl.setColorAt(1, QColor(255, 255, 255, 0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(hl))
    p.drawEllipse(QPointF(c.x() - R * 0.1, c.y() - R * 0.42), R * 0.5, R * 0.26)
    sparkle(p, c.x() + R * 0.42, c.y() - R * 0.44, R * 0.09, QColor(255, 255, 255), 0.9)
    p.end()
    return pm


def _skin_approach(self, ci):
    if not _on():
        return _STATE["orig"]["skin_approach"](self, ci)
    import osu_skin as SK

    def mk():
        R = self.R
        s = R * 2 + 8
        pm = SK._pm(s, s, self.dpr)
        p = SK._painter(pm)
        c = QPointF(s / 2, s / 2)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(iridescent(c, 90), max(2.0, R * 0.06)))
        p.drawEllipse(c, R * 0.96, R * 0.96)
        p.end()
        return pm
    return self._get(("approach_y2k", ci % len(self.colors)), mk)


def _skin_cursor(self, scale=1.0):
    if not _on():
        return _STATE["orig"]["skin_cursor"](self, scale)
    import osu_skin as SK

    def mk():
        r = 24 * scale
        pm = SK._pm(r * 2, r * 2, self.dpr)
        p = SK._painter(pm)
        g = QRadialGradient(QPointF(r, r), r)
        g.setColorAt(0.2, QColor(166, 233, 255, 120))
        g.setColorAt(1.0, QColor(199, 179, 255, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(QPointF(r, r), r, r)
        sparkle(p, r, r, r * 0.62, QColor(255, 255, 255), 1.0)
        sparkle(p, r, r, r * 0.3, PINKY, 1.0)
        p.end()
        return pm
    return self._get(("cursor_y2k", scale), mk)


def _skin_trail(self, scale=1.0):
    if not _on():
        return _STATE["orig"]["skin_trail"](self, scale)
    import osu_skin as SK
    return self._get(("trail_y2k", scale), lambda: SK.glow_dot(12 * scale, QColor(199, 179, 255, 200), self.dpr, 0.3))


# ══════════════════════════════════════════════════════════════════════════ #
#  Свои хитсаунды
# ══════════════════════════════════════════════════════════════════════════ #

HITSOUNDS = [  # (ключ в наборе звуков, название, имена файлов в скинах osu!)
    ("normal", "Удар (normal)", ["normal-hitnormal"]),
    ("soft", "Удар (soft)", ["soft-hitnormal"]),
    ("drum", "Удар (drum)", ["drum-hitnormal"]),
    ("whistle", "Свисток (whistle)", ["normal-hitwhistle", "soft-hitwhistle", "drum-hitwhistle"]),
    ("finish", "Тарелка (finish)", ["normal-hitfinish", "soft-hitfinish", "drum-hitfinish"]),
    ("clap", "Хлопок (clap)", ["normal-hitclap", "soft-hitclap", "drum-hitclap"]),
    ("tick", "Тик слайдера", ["normal-slidertick", "soft-slidertick", "drum-slidertick"]),
    ("slider_end", "Конец слайдера", ["normal-sliderslide", "soft-sliderslide"]),
    ("spin", "Вращение спиннера", ["spinnerspin"]),
    ("bonus", "Бонус спиннера", ["spinnerbonus"]),
    ("combobreak", "Сброс комбо", ["combobreak"]),
    ("count3", "Отсчёт «3»", ["count3s", "count3"]),
    ("count2", "Отсчёт «2»", ["count2s", "count2"]),
    ("count1", "Отсчёт «1»", ["count1s", "count1"]),
    ("go", "«Go!»", ["gos", "go"]),
    ("pass", "Секция пройдена", ["sectionpass"]),
    ("fail_section", "Секция провалена", ["sectionfail"]),
    ("fail", "Провал", ["failsound"]),
    ("applause", "Аплодисменты", ["applause"]),
    ("menuhit", "Меню: выбор", ["menuhit"]),
    ("menuclick", "Меню: клик", ["menuclick"]),
    ("menuback", "Меню: назад", ["menuback"]),
    ("hover", "Меню: наведение", ["menu-hover", "click-short"]),
    ("whoosh", "Вход в меню", ["welcome", "seeya"]),
    ("comboburst", "Комбо-бёрст", ["comboburst"]),
    ("check_on", "Мод включён", ["check-on"]),
    ("check_off", "Мод выключен", ["check-off"]),
    ("expand", "Выбор: раскрыть набор", ["select-expand"]),
    ("difficulty", "Выбор: сложность", ["select-difficulty"]),
    ("shutter", "Экран результатов", ["shutter"]),
    ("heartbeat", "Сердцебиение печеньки", ["heartbeat"]),
    ("menu_play", "Меню: Play", ["menu-play-click"]),
    ("menu_edit", "Меню: Edit", ["menu-edit-click"]),
    ("menu_options", "Меню: Options", ["menu-options-click"]),
    ("menu_exit", "Меню: Exit", ["menu-exit-click"]),
    ("menu_freeplay", "Меню: Solo", ["menu-freeplay-click"]),
    ("ready", "«Ready?»", ["readys"]),
]
AUDIO_EXT = (".wav", ".ogg", ".mp3", ".flac", ".m4a", ".opus")


def _decode(path: str, sr=48000):
    """Любой звуковой файл → моно float32 48 кГц (через ffmpeg; wav — и без него)."""
    try:
        r = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
                           capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if r.returncode == 0 and r.stdout:
            a = np.frombuffer(r.stdout, np.float32).copy()
            return a[: sr * 6]                              # не длиннее 6 с
    except Exception as e:                                 # noqa: BLE001
        print("[hitsounds] ffmpeg:", e)
    return None


def custom_map(settings) -> dict:
    return dict((settings or {}).get("osu_hitsounds") or {})


_DECODED: dict = {}                                        # (путь, размер, время) → звук


def _decode_cached(path: str):
    """_decode с кэшем в памяти и на диске (рядом, .npy): ffmpeg на каждый звук при каждом входе в
    тему держал интерфейс ~секунду."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    sig = (path, st.st_size, int(st.st_mtime))
    a = _DECODED.get(sig)
    if a is not None:
        return a
    npy = Path(path + f".{st.st_size}_{int(st.st_mtime)}.npy")
    try:
        a = np.load(npy)
    except Exception:                                      # noqa: BLE001
        a = _decode(path)
        if a is not None and len(a):
            try:
                for old in Path(path).parent.glob(Path(path).name + ".*.npy"):
                    old.unlink(missing_ok=True)
                np.save(npy, a)
            except Exception:                              # noqa: BLE001
                pass
    if a is not None:
        _DECODED[sig] = a
    return a


def apply_custom(sfx, settings):
    """Подменить звуки набора sfx файлами пользователя (встроенные — как запасные)."""
    if sfx is None:
        return
    base = sfx.__dict__.setdefault("_y2k_builtin", dict(sfx.bank))
    sfx.bank = dict(base)
    items = [(k, p) for k, p in custom_map(settings).items() if p and os.path.exists(p)]
    try:                                                   # звуки активного скина osu! — свои файлы поверх них
        import osu_skin_import
        own = {k for k, _p in items}
        items = [(k, p) for k, p in osu_skin_import.sound_map(settings).items() if k not in own] + items
    except Exception as e:                                 # noqa: BLE001
        print("[hitsounds] skin:", e)
    if not items:
        return
    from concurrent.futures import ThreadPoolExecutor      # ffmpeg — отдельные процессы, параллельно
    with ThreadPoolExecutor(max_workers=min(6, len(items))) as ex:
        res = list(ex.map(lambda kp: _decode_cached(kp[1]), items))
    for (key, _p), a in zip(items, res):
        if a is not None and len(a):
            peak = float(np.abs(a).max()) or 1.0
            sfx.bank[key] = (a / peak * 0.8).astype(np.float32)


def set_custom(win, key, src):
    """Скопировать файл к себе и запомнить; src=None — вернуть встроенный."""
    m = win.settings.setdefault("osu_hitsounds", {})
    for old in HS_DIR.glob(key + ".*"):
        old.unlink(missing_ok=True)
    if src:
        HS_DIR.mkdir(parents=True, exist_ok=True)
        dst = HS_DIR / (key + Path(src).suffix.lower())
        shutil.copyfile(src, dst)
        m[key] = str(dst)
    else:
        m.pop(key, None)
    _refresh(win)


def import_skin(win, folder) -> int:
    """Папка скина osu! → все подходящие хитсаунды разом. Возвращает, сколько нашлось."""
    files = {f.stem.lower(): f for f in Path(folder).iterdir() if f.suffix.lower() in AUDIO_EXT}
    n = 0
    for key, _title, names in HITSOUNDS:
        f = next((files[nm] for nm in names if nm in files), None)
        if f is not None:
            set_custom(win, key, str(f))
            n += 1
    return n


def _refresh(win):
    try:
        import osu_sfx
        if osu_sfx._SFX is not None:
            apply_custom(osu_sfx._SFX, win.settings)
    except Exception as e:                                 # noqa: BLE001
        print("[hitsounds]", e)
    try:
        mod = __import__("sys").modules.get(type(win).__module__)
        mod.save_json(mod.SETTINGS_FILE, win.settings)
    except Exception:                                      # noqa: BLE001
        pass


QSS = """
QDialog { background: #0b0c1a; }
QLabel { color: #e9ecff; background: transparent; }
QLabel#Sub { color: #8f94b8; font-size: 11px; }
QLabel#Own { color: #a6e9ff; font-size: 11px; }
QPushButton { background: rgba(255,255,255,0.06); color: #eef0ff; border: 1px solid #5a6080; border-radius: 13px;
    padding: 5px 12px; min-height: 16px; }
QPushButton:hover { border-color: #c7b3ff; background: rgba(199,179,255,0.15); }
QPushButton#Accent { background: #c7b3ff; color: #1a1430; border: none; font-weight: 700; }
"""


class HitsoundsDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Свои хитсаунды")
        self.setStyleSheet(QSS)
        self.setMinimumWidth(620)
        v = QVBoxLayout(self)
        top = QLabel("Любой звук темы esu! можно заменить своим файлом (WAV, OGG, MP3, FLAC). "
                     "Скин osu! целиком — кнопкой «Импорт скина» (папка с normal-hitnormal.wav и т. д.).")
        top.setObjectName("Sub")
        top.setWordWrap(True)
        v.addWidget(top)
        row = QHBoxLayout()
        b = QPushButton("Импорт скина osu!…")
        b.setObjectName("Accent")
        b.clicked.connect(self._import)
        row.addWidget(b)
        b2 = QPushButton("Все — встроенные")
        b2.clicked.connect(self._reset_all)
        row.addWidget(b2)
        row.addStretch(1)
        v.addLayout(row)
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(4)
        v.addLayout(self.grid)
        self.status = QLabel("")
        self.status.setObjectName("Own")
        v.addWidget(self.status)
        self._fill()

    def _fill(self):
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.deleteLater()
        own = custom_map(self.win.settings)
        try:
            import osu_skin_import
            from_skin = osu_skin_import.sound_map(self.win.settings)
        except Exception:                                  # noqa: BLE001
            from_skin = {}
        for r, (key, title, _n) in enumerate(HITSOUNDS):
            self.grid.addWidget(QLabel(title), r, 0)
            st = QLabel(("свой: " + Path(own[key]).name) if key in own else
                        ("из скина: " + Path(from_skin[key]).name) if key in from_skin else "встроенный")
            st.setObjectName("Own" if key in own or key in from_skin else "Sub")
            self.grid.addWidget(st, r, 1)
            pl = QPushButton("▶")
            pl.setToolTip("Послушать")
            pl.setFixedWidth(40)
            pl.clicked.connect(lambda _=False, k=key: self._play(k))
            self.grid.addWidget(pl, r, 2)
            ch = QPushButton("Файл…")
            ch.clicked.connect(lambda _=False, k=key: self._pick(k))
            self.grid.addWidget(ch, r, 3)
            rs = QPushButton("↺")
            rs.setToolTip("Встроенный звук")
            rs.setFixedWidth(40)
            rs.setEnabled(key in own)
            rs.clicked.connect(lambda _=False, k=key: (set_custom(self.win, k, None), self._fill()))
            self.grid.addWidget(rs, r, 4)

    def _play(self, key):
        try:
            import osu_sfx
            s = osu_sfx.get()
            apply_custom(s, self.win.settings) if "_y2k_builtin" not in s.__dict__ else None
            s.play(key, 1.0)
        except Exception as e:                             # noqa: BLE001
            self.status.setText(f"Не получилось проиграть: {e}")

    def _pick(self, key):
        path, _ = QFileDialog.getOpenFileName(self, "Звук", os.path.expanduser("~"),
                                              "Звук (*.wav *.ogg *.mp3 *.flac *.m4a *.opus)")
        if path:
            set_custom(self.win, key, path)
            self._fill()
            self._play(key)

    def _import(self):
        d = QFileDialog.getExistingDirectory(self, "Папка скина osu!", os.path.expanduser("~"))
        if d:
            n = import_skin(self.win, d)
            self._fill()
            self.status.setText(f"Из скина взято звуков: {n}" if n else "В папке не нашлось звуков скина osu!")

    def _reset_all(self):
        for key in list(custom_map(self.win.settings)):
            set_custom(self.win, key, None)
        self._fill()


def open_hitsounds(win):
    dlg = HitsoundsDialog(win)
    try:
        import inapp
        inapp.present(win, dlg, "Свои хитсаунды")
    except Exception:                                      # noqa: BLE001
        dlg.show()


# ── настройки темы osu: «Оформление» и «Свои хитсаунды» ── #

SETTINGS_Y2K = """
QFrame#OsuSettings, QFrame#OsuSettings QWidget#Body { background: rgba(10, 11, 26, 0.97); }
QFrame#OsuSettings QLabel#H1 { color: #f2f4ff; }
QFrame#OsuSettings QLabel#H2 { color: #c7b3ff; letter-spacing: 2px; }
QFrame#OsuSettings QLabel#Val { color: #a6e9ff; font-family: Consolas; }
QFrame#OsuSettings QSlider::sub-page:horizontal { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
    stop:0 #a6e9ff, stop:0.5 #c7b3ff, stop:1 #ffc2ec); border-radius: 2px; }
QFrame#OsuSettings QSlider::handle:horizontal { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
    stop:0 #ffffff, stop:0.5 #9aa3b5, stop:1 #e6eaf3); border: 1px solid #ffffff; }
QFrame#OsuSettings QCheckBox::indicator { width: 36px; height: 18px; border-radius: 9px; image: none;
    background: #23253f; border: 1px solid #5a6080; }
QFrame#OsuSettings QCheckBox::indicator:checked { image: none; border: 1px solid #ffffff;
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #a6e9ff, stop:1 #c7b3ff); }
QFrame#OsuSettings QPushButton { background: rgba(255,255,255,0.06); color: #eef0ff; border: 1px solid #8a92b0;
    border-radius: 14px; padding: 6px 14px; }
QFrame#OsuSettings QPushButton:hover { border-color: #c7b3ff; background: rgba(199,179,255,0.15); }
QFrame#OsuSettings QComboBox { border-radius: 12px; }
"""


def _settings_build(self):
    _STATE["orig"]["settings_build"](self)
    if _on():
        self.setStyleSheet(self.styleSheet() + SETTINGS_Y2K)
    try:
        sh = self.shell
        idx = None
        for i in range(self.v.count()):
            w = self.v.itemAt(i).widget()
            if isinstance(w, QLabel) and w.text() == "ГЕНЕРАТОР КАРТ":
                idx = i
                break
        if idx is None:
            idx = self.v.count()
        widgets = []
        h = QLabel("ОФОРМЛЕНИЕ")
        h.setObjectName("H2")
        widgets.append(h)
        row = QHBoxLayout()
        row.addWidget(QLabel("Стиль темы"))
        row.addStretch(1)
        cb = QComboBox()
        cb.addItem("Классика — как в osu!", "classic")
        cb.addItem("Y2K — минимал", "y2k")
        cb.setCurrentIndex(1 if sh.S().get("style", "classic") == "y2k" else 0)
        cb.currentIndexChanged.connect(lambda _i: set_style(sh, cb.currentData()))
        row.addWidget(cb)
        from PyQt6.QtWidgets import QWidget
        holder = QWidget()
        holder.setLayout(row)
        row.setContentsMargins(0, 2, 0, 2)
        widgets.append(holder)
        h2 = QLabel("СВОИ ХИТСАУНДЫ")
        h2.setObjectName("H2")
        widgets.append(h2)
        n = len(custom_map(sh.win.settings))
        note = QLabel(f"Своих звуков: {n}. Можно заменить любой звук или взять все из скина osu!.")
        note.setObjectName("Sub")
        note.setWordWrap(True)
        widgets.append(note)
        b = QPushButton("Хитсаунды…")
        b.clicked.connect(lambda: open_hitsounds(sh.win))
        widgets.append(b)
        for k, w in enumerate(widgets):
            self.v.insertWidget(idx + k, w)
    except Exception as e:                                 # noqa: BLE001
        print("[osu y2k] settings:", e)


def set_style(shell, style):
    shell.S()["style"] = style
    _STATE["settings"] = shell.S()
    _apply_palette()
    shell._skins = {}                                       # ноты перерисовать в новом стиле
    if hasattr(shell, "apply_look"):                        # вид нот: в Y2K — минимализм под стекло
        shell.apply_look()
    shell.save_settings()
    for s in shell.screens.values():
        s.update()


def _apply_palette():
    import osu_theme as T
    import osu_skin as SK
    O = _STATE["orig"]
    if _on():
        T.PINK = QColor(LILAC)
        SK.COMBO_COLORS = list(PASTEL_COMBO)
        SK.DARK_NUMBERS = False
    else:
        T.PINK = O.get("pink", T.PINK)
        SK.set_palette((_STATE.get("settings") or {}).get("palette", SK.DEFAULT_PALETTE))
        SK.DARK_NUMBERS = True


def _shell_init(self, win, parent=None):
    _STATE["settings"] = win.settings.setdefault("osu", {})
    _STATE["settings"].setdefault("style", "classic")
    _apply_palette()
    _STATE["orig"]["shell_init"](self, win, parent)
    try:
        apply_custom(self.sfx, win.settings)
    except Exception as e:                                 # noqa: BLE001
        print("[hitsounds]", e)


def install(win=None) -> bool:
    try:
        import osu_theme as T
        import osu_skin as SK
    except Exception as e:                                 # noqa: BLE001
        print("[osu y2k]", e)
        return False
    if getattr(T, "_y2k_installed", False):
        return False
    O = _STATE["orig"]
    O.update({"menu_paint": T.MenuScreen.paintEvent, "menu_toolbar": T.MenuScreen._draw_toolbar,
              "menu_user": T.MenuScreen._draw_user, "menu_cookie": T.MenuScreen._draw_cookie,
              "sel_bottom": T.SelectScreen._draw_bottom, "sel_info": T.SelectScreen._draw_info,
              "sel_carousel": T.SelectScreen._draw_carousel, "draw_bg": T.draw_bg,
              "skin_circle": SK.Skin._circle, "skin_approach": SK.Skin.approach, "skin_cursor": SK.Skin.cursor,
              "skin_trail": SK.Skin.trail, "settings_build": T.SettingsPanel._build,
              "shell_init": T.OsuShell.__init__, "pink": QColor(T.PINK), "combo": list(SK.COMBO_COLORS)})
    T.MenuScreen.paintEvent = _menu_paint
    T.MenuScreen._draw_toolbar = _menu_toolbar
    T.MenuScreen._draw_user = _menu_user
    T.MenuScreen._draw_cookie = _menu_cookie
    T.SelectScreen._draw_bottom = _sel_bottom
    T.SelectScreen._draw_info = _sel_info
    T.SelectScreen._draw_carousel = _sel_carousel
    T.draw_bg = _draw_bg
    SK.Skin._circle = _skin_circle
    SK.Skin.approach = _skin_approach
    SK.Skin.cursor = _skin_cursor
    SK.Skin.trail = _skin_trail
    T.SettingsPanel._build = _settings_build
    T.OsuShell.__init__ = _shell_init
    T._y2k_installed = True
    if win is not None:
        _STATE["settings"] = win.settings.setdefault("osu", {})
        _STATE["settings"].setdefault("style", "classic")
        _apply_palette()
    return True
