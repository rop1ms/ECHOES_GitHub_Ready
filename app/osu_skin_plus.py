# osu_skin_plus.py
"""
Всё, что меняют «большие» скины osu!, — и чего раньше esu! из скина не брал:

  игра:   combo burst (comboburst*.png + звук comboburst, ComboBurstRandom), секция пройдена/провалена
          (section-pass / section-fail), отсчёт ready / count3 / count2 / count1 / go, стрелки перед концом
          перерыва (play-warningarrow / arrow-warning), спиннер — spinner-background, spinner-spin, spinner-clear,
          spinner-rpm, spinner-metre; полоска здоровья — маркер (scorebar-marker / scorebar-ki / kidanger /
          kidanger2); фон паузы pause-overlay и провала fail-background; дым курсора (держите C — cursor-smoke);
  выбор:  значки модов selection-mod-*.png; без скина — цветные значки модов, как в osu!.

Чего в скине нет — рисуется как раньше. Подключается при запуске (main.py → install()) и обёртывает методы
osu_game.OsuGame / osu_theme.OsuShell снаружи.
"""
from __future__ import annotations

import math
import random
import time

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPen, QPixmap, QPolygonF, QRadialGradient

_O: dict = {}
SK = None
GM = None


def _files():
    return getattr(SK, "ACTIVE", None)


def _ini(key, default=""):
    sf = _files()
    try:
        return (sf.ini.get("general", {}) if sf else {}).get(key, default)
    except Exception:                                      # noqa: BLE001
        return default


def _pm(self, names, scale_h, cache_key=None):
    """Спрайт скина (первое найденное имя), масштаб «как в osu!» — от высоты окна 768."""
    sf = _files()
    if sf is None:
        return None
    H = self.height()
    key = ("sp+", tuple(names), int(H), round(scale_h, 3), id(sf), cache_key)
    c = self.__dict__.setdefault("_sp_cache", {})
    if key in c:
        return c[key]
    img, k = sf.image(*names)
    pm = None
    if img is not None:
        dpr = self.devicePixelRatioF()
        s = H / 768.0 * k * scale_h
        w, h = max(1, int(img.width() * s * dpr)), max(1, int(img.height() * s * dpr))
        pm = QPixmap.fromImage(img.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation))
        pm.setDevicePixelRatio(dpr)
    if len(c) > 120:
        c.clear()
    c[key] = pm
    return pm


def _frames(self, base, scale_h=1.0):
    """Кадры анимации base-0…base-N (или один base)."""
    sf = _files()
    if sf is None:
        return []
    out = []
    for i in range(60):
        if not sf.has(f"{base}-{i}"):
            break
        out.append(_pm(self, [f"{base}-{i}"], scale_h))
    if not out:
        pm = _pm(self, [base], scale_h)
        if pm is not None:
            out = [pm]
    return [x for x in out if x is not None]


# ------------------------------------------------------------------ #
#  Combo burst                                                        #
# ------------------------------------------------------------------ #

BURST_AT = (30, 60, 100)


def _burst_check(self, now):
    P = self.play
    if P is None:
        return
    c = int(P.combo)
    last = self.__dict__.get("_cb_last", 0)
    self._cb_last = c
    if c <= last:
        return
    hit = any(last < m <= c for m in BURST_AT) or (c >= 200 and c // 100 > last // 100)
    if not hit or not self.S.get("comboburst", True):
        return
    frames = _frames(self, "comboburst", 1.0)
    try:
        self.sfx("comboburst", 0.8)
    except Exception:                                      # noqa: BLE001
        pass
    if not frames:
        return
    if _ini("comboburstrandom", "0").strip() == "1":
        pm = random.choice(frames)
    else:
        i = self.__dict__.get("_cb_i", 0)
        pm = frames[i % len(frames)]
        self._cb_i = i + 1
    side = self.__dict__.get("_cb_side", 1) * -1
    self._cb_side = side
    self._cb = {"pm": pm, "t0": now, "side": side}


def _burst_draw(self, p, W, H, now):
    b = self.__dict__.get("_cb")
    if not b:
        return
    e = now - b["t0"]
    if e > 1.3:
        self._cb = None
        return
    pm = b["pm"]
    w = pm.width() / pm.devicePixelRatio()
    h = pm.height() / pm.devicePixelRatio()
    k = 1 - (1 - min(1.0, e / 0.35)) ** 3
    a = 1.0 if e < 0.8 else max(0.0, 1 - (e - 0.8) / 0.5)
    p.setOpacity(a)
    if b["side"] < 0:                                      # слева — выезжает из-за края
        p.drawPixmap(QPointF(-w + w * k, H - h), pm)
    else:                                                  # справа — зеркально, как в osu!
        p.save()
        p.translate(W + w - w * k, H - h)
        p.scale(-1, 1)
        p.drawPixmap(QPointF(0, 0), pm)
        p.restore()
    p.setOpacity(1.0)


# ------------------------------------------------------------------ #
#  Всплывающее: секции, отсчёт                                        #
# ------------------------------------------------------------------ #

def _draw_pops(self, p, now):
    sf = _files()
    if sf is None:
        return _O["pops"](self, p, now)
    W, H = self.width(), self.height()
    mine, rest = [], []
    for q in self.pops:
        if "section" in q and sf.has("section-pass" if q["section"] else "section-fail"):
            mine.append(q)
        elif "count" in q and sf.has({"3": "count3", "2": "count2", "1": "count1", "GO!": "go"}.get(q["count"], "go")):
            mine.append(q)
        else:
            rest.append(q)
    if mine:
        full = self.pops
        self.pops = rest
        try:
            _O["pops"](self, p, now)
        finally:
            self.pops = full
    else:
        _O["pops"](self, p, now)
    for q in mine:
        e = now - q["t0"]
        if "section" in q:
            pm = _pm(self, ["section-pass" if q["section"] else "section-fail"], 1.0)
            if pm is None:
                continue
            # как в osu!: мигает и гаснет
            a = (1.0 if int(e * 10) % 2 == 0 else 0.0) if e < 0.3 else (1.0 if e < 1.4 else max(0.0, 1 - (e - 1.4) / 0.4))
            T_blit(p, pm, W / 2, H / 2, 1.0, a)
        else:
            if e > 0.9:
                continue
            name = {"3": "count3", "2": "count2", "1": "count1", "GO!": "go"}.get(q["count"], "go")
            pm = _pm(self, [name], 1.0)
            if pm is None:
                continue
            sc = 1.25 - 0.25 * min(1.0, e / 0.2)
            T_blit(p, pm, W / 2, H / 2, sc, 1 - e / 0.9)
    p.setOpacity(1.0)


def T_blit(p, pm, cx, cy, sc=1.0, a=1.0, rot=0.0):
    GM.blit(p, pm, cx, cy, sc, a, rot)


# ------------------------------------------------------------------ #
#  Стрелки перед концом перерыва                                       #
# ------------------------------------------------------------------ #

def _draw_arrows(self, p, W, H):
    pm = _pm(self, ["play-warningarrow", "arrow-warning"], 1.0)
    if pm is None:
        return _O["arrows"](self, p, W, H)
    a = 1.0 if int(time.perf_counter() * 8) % 2 == 0 else 0.35
    for side in (-1, 1):
        for yy in (H * 0.3, H * 0.7):
            x = W / 2 + side * W * 0.38
            p.save()
            p.translate(x, yy)
            if side > 0:
                p.scale(-1, 1)                             # картинка смотрит вправо — справа зеркалим
            T_blit(p, pm, 0, 0, 1.0, a)
            p.restore()


# ------------------------------------------------------------------ #
#  Спиннер                                                            #
# ------------------------------------------------------------------ #

def _draw_spinner(self, p, o, t, now):
    sf = _files()
    if sf is None or not sf.has("spinner-spin", "spinner-clear", "spinner-rpm", "spinner-background", "spinner-metre"):
        return _O["spinner"](self, p, o, t, now)
    if t < o["t"] - 400:
        return
    W, H = self.width(), self.height()
    done = o["j"] is not None
    if done:
        e = now - o.setdefault("_done_rt", now)
        if e > 0.35:
            return
        a = 1 - e / 0.35
    else:
        a = min(1.0, (t - (o["t"] - 400)) / 400)
    cx, cy = self._to_px(256, 192)
    size = self.s * 384 * 0.95
    prog = min(1.0, o["spins"] / max(1e-6, o["need"]))
    bg = _pm(self, ["spinner-background"], 1.0)
    if bg is not None:
        T_blit(p, bg, cx, cy + H * 0.06, 1.0, a)
    else:
        p.setOpacity(a * 0.45)
        p.fillRect(self.rect(), QColor(0, 0, 0))
    me = _pm(self, ["spinner-metre"], 1.0)
    if me is not None and prog > 0:                        # старый «уровень»: снизу вверх по полоскам
        d = me.devicePixelRatio()
        mw, mh = me.width() / d, me.height() / d
        bars = 10
        f = math.floor(prog * bars) / bars
        top = mh * (1 - f)
        p.setOpacity(a)
        p.drawPixmap(QRectF(cx - mw / 2, cy - mh / 2 + top + H * 0.06, mw, mh - top), me,
                     QRectF(0, top * d, me.width(), (mh - top) * d))
    disc = self.skin.spinner_disc(size)
    T_blit(p, disc, cx, cy, 1.0, a, math.degrees(o["rot"]))
    if not done and t >= o["t"]:
        f = max(0.0, 1 - (t - o["t"]) / max(1.0, o["end"] - o["t"]))
        ring = self.skin.ring(size, QColor(255, 255, 255, 200), max(2.0, size * 0.01))
        T_blit(p, ring, cx, cy, max(0.02, f), a)
    if not done and t < o["t"] + 700:
        k = max(0.0, (t - o["t"] + 400) / 1100)
        aa = a * (1 - k) if t > o["t"] else a
        spin = _pm(self, ["spinner-spin"], 1.0)
        if spin is not None:
            T_blit(p, spin, cx, cy + size * 0.33, 1.0, aa)
        else:
            p.setOpacity(aa)
            p.setFont(GM._font(H * 0.07, QFont.Weight.Black, True))
            p.setPen(QColor(255, 255, 255))
            p.drawText(QRectF(0, cy + size * 0.25, W, H * 0.1), Qt.AlignmentFlag.AlignCenter, "КРУТИ!")
    if prog >= 1.0 and not done:
        cl = _pm(self, ["spinner-clear"], 1.0)
        if cl is not None:
            e2 = now - o.setdefault("_clear_rt", now)
            T_blit(p, cl, cx, cy - size * 0.22, 1.0 + 0.4 * max(0.0, 1 - e2 / 0.25), a)
    rpm_bg = _pm(self, ["spinner-rpm"], 1.0)
    p.setOpacity(a)
    if rpm_bg is not None:
        d = rpm_bg.devicePixelRatio()
        p.drawPixmap(QPointF(W / 2 - rpm_bg.width() / d / 2, H - rpm_bg.height() / d), rpm_bg)
    self.digits_small.draw(p, f"{int(o['rpm'])}", W / 2 + (W * 0.03 if rpm_bg is not None else 0), H - H * 0.05, "c")
    if rpm_bg is None:
        p.setFont(GM._font(H * 0.02, QFont.Weight.Bold))
        p.setPen(QColor(255, 255, 255, 170))
        p.drawText(QRectF(W / 2 + 40, H - H * 0.07, 120, H * 0.04), Qt.AlignmentFlag.AlignVCenter, "об/мин")
    p.setOpacity(1.0)


# ------------------------------------------------------------------ #
#  HUD: маркер здоровья, combo burst                                  #
# ------------------------------------------------------------------ #

def _draw_hud(self, p, W, H, now):
    _O["hud"](self, p, W, H, now)
    try:
        _burst_check(self, now)
        _burst_draw(self, p, W, H, now)
    except Exception as e:                                 # noqa: BLE001
        print("[skin+] burst:", e)
    sf = _files()
    if sf is None:
        return
    try:
        bar = self.skin.hp_bar(H) if hasattr(self.skin, "hp_bar") else None
        if bar is None:
            return
        _bg, col, (bx, by) = bar
        hp = max(0.0, min(1.0, self.hp_disp))
        d = col.devicePixelRatio()
        x, y = bx + col.width() / d * hp, by + col.height() / d / 2
        if sf.has("scorebar-marker"):
            mk = _pm(self, ["scorebar-marker"], 1.0)
        else:
            names = ["scorebar-kidanger2"] if hp < 0.2 else ["scorebar-kidanger"] if hp < 0.5 else ["scorebar-ki"]
            mk = _pm(self, names + ["scorebar-ki"], 1.0)
        if mk is not None and hp > 0.005:
            box = self.__dict__.get("_hud_boxes", {}).get("hp")
            if box is not None and hasattr(self, "_hud_begin"):
                self._hud_begin(p, "hp", box)              # маркер — вместе с перенесённой полоской
            T_blit(p, mk, x, y, 1.0 + 0.25 * max(0.0, getattr(self, "hp_flash", 0.0)), 1.0)
            if box is not None and hasattr(self, "_hud_end"):
                self._hud_end(p)
    except Exception as e:                                 # noqa: BLE001
        print("[skin+] hp:", e)


# ------------------------------------------------------------------ #
#  Пауза / провал: фон из скина                                       #
# ------------------------------------------------------------------ #

def _draw_menu(self, p, W, H, title):
    name = "fail-background" if title == "Провал" else "pause-overlay"
    pm = _pm(self, [name], 1.0)
    if pm is not None:
        d = pm.devicePixelRatio()
        w, h = pm.width() / d, pm.height() / d
        k = max(W / w, H / h)
        p.setOpacity(1.0)
        p.drawPixmap(QRectF((W - w * k) / 2, (H - h * k) / 2, w * k, h * k), pm, QRectF(pm.rect()))
    return _O["menu"](self, p, W, H, title)


# ------------------------------------------------------------------ #
#  Дым курсора (держите C)                                            #
# ------------------------------------------------------------------ #

SMOKE_LIFE = 6.0


def _smoke_key(self, e, down):
    try:
        k = GM.key_from_event(e)
    except Exception:                                      # noqa: BLE001
        k = ""
    want = str(self.S.get("key_smoke", "C") or "C").upper()
    if k.upper() == want and not e.isAutoRepeat() and self.S.get("smoke", True):
        self._smoke_on = down
        return True
    return False


def _key_press(self, e):
    _smoke_key(self, e, True)
    return _O["kp"](self, e)


def _key_release(self, e):
    _smoke_key(self, e, False)
    return _O["kr"](self, e)


def _smoke_pm(self):
    pm = _pm(self, ["cursor-smoke"], 1.0)
    if pm is not None:
        return pm
    c = self.__dict__.get("_smoke_dot")
    if c is None or c[0] != int(self.height()):
        r = max(3.0, self.height() * 0.006)
        dpr = self.devicePixelRatioF()
        pm = QPixmap(int(r * 4 * dpr), int(r * 4 * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        q = QPainter(pm)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        g = QRadialGradient(QPointF(r * 2, r * 2), r * 2)
        g.setColorAt(0, QColor(255, 255, 255, 170))
        g.setColorAt(1, QColor(255, 255, 255, 0))
        q.setPen(Qt.PenStyle.NoPen)
        q.setBrush(QBrush(g))
        q.drawEllipse(QPointF(r * 2, r * 2), r * 2, r * 2)
        q.end()
        c = self._smoke_dot = (int(self.height()), pm)
    return c[1]


def _draw_cursor(self, p, now):
    pts = self.__dict__.setdefault("_smoke", [])
    if self.__dict__.get("_smoke_on") and self.state in ("play", "resume"):
        x, y = self.cx, self.cy
        if not pts or math.hypot(pts[-1][0] - x, pts[-1][1] - y) > 2.5:
            pts.append((x, y, now))
            if len(pts) > 900:
                del pts[:100]
    if pts:
        while pts and now - pts[0][2] > SMOKE_LIFE:
            pts.pop(0)
        pm = _smoke_pm(self)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        for (x, y, t0) in pts:
            a = 1.0 - (now - t0) / SMOKE_LIFE
            px, py = self._to_px(x, y)
            T_blit(p, pm, px, py, 1.0, 0.55 * a * a)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        p.setOpacity(1.0)
    return _O["cursor"](self, p, now)


def _game_start(self, *a, **k):
    self._smoke = []
    self._cb = None
    self._cb_last = 0
    return _O["start"](self, *a, **k)


# ------------------------------------------------------------------ #
#  Значки модов                                                       #
# ------------------------------------------------------------------ #

MOD_FILES = {"EZ": "easy", "NF": "nofail", "HT": "halftime", "HR": "hardrock", "SD": "suddendeath",
             "PF": "perfect", "DT": "doubletime", "NC": "nightcore", "HD": "hidden", "FL": "flashlight",
             "RX": "relax", "AP": "relax2", "AU": "autoplay", "CN": "cinema", "SO": "spunout"}
MOD_FULL = {"EZ": "Easy", "NF": "No-Fail", "HT": "Half", "HR": "Hard Rock", "SD": "Sudden Death", "PF": "Perfect",
            "DT": "Double", "NC": "Nightcore", "HD": "Hidden", "FL": "Flashlight", "RX": "Relax", "AP": "Autopilot",
            "AU": "Auto", "CN": "Cinema", "SO": "Spun-out"}
MOD_COL = {"EZ": QColor(108, 190, 50), "NF": QColor(62, 116, 200), "HT": QColor(120, 120, 130),
           "HR": QColor(214, 50, 60), "SD": QColor(230, 110, 30), "PF": QColor(230, 160, 40),
           "DT": QColor(140, 70, 200), "NC": QColor(180, 60, 170), "HD": QColor(240, 170, 20),
           "FL": QColor(70, 70, 80), "RX": QColor(30, 150, 230), "AP": QColor(40, 110, 200),
           "AU": QColor(40, 130, 230), "CN": QColor(60, 60, 70), "SO": QColor(200, 40, 90)}


def _glyph(p, m, r, col):
    """Простые значки модов (звезда, нота, часы, глаз…), как на иконках osu!."""
    cx, cy, u = r.center().x(), r.center().y(), r.height() / 2
    p.setPen(QPen(col, max(1.5, u * 0.14), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    if m in ("EZ",):                                       # нота
        p.drawLine(QPointF(cx + u * 0.15, cy - u * 0.7), QPointF(cx + u * 0.15, cy + u * 0.35))
        p.setBrush(col)
        p.drawEllipse(QPointF(cx - u * 0.1, cy + u * 0.4), u * 0.3, u * 0.22)
        p.drawLine(QPointF(cx + u * 0.15, cy - u * 0.7), QPointF(cx + u * 0.6, cy - u * 0.45))
    elif m in ("HT", "DT", "NC"):                          # часы
        p.drawEllipse(QPointF(cx, cy), u * 0.65, u * 0.65)
        p.drawLine(QPointF(cx, cy), QPointF(cx, cy - u * 0.45))
        p.drawLine(QPointF(cx, cy), QPointF(cx + u * (0.4 if m != "HT" else -0.35), cy + u * 0.1))
    elif m in ("HD",):                                     # глаз
        p.drawEllipse(QRectF(cx - u * 0.7, cy - u * 0.35, u * 1.4, u * 0.7))
        p.setBrush(col)
        p.drawEllipse(QPointF(cx, cy), u * 0.2, u * 0.2)
    elif m in ("FL",):                                     # луна
        p.setBrush(col)
        p.drawEllipse(QPointF(cx, cy), u * 0.6, u * 0.6)
        p.setBrush(QColor(0, 0, 0, 160))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx + u * 0.3, cy - u * 0.15), u * 0.5, u * 0.5)
    elif m in ("RX", "AP", "AU"):                          # звезда / шестерня
        p.setBrush(col)
        p.drawPolygon(SK.star_shape(cx, cy, u * 0.7, u * 0.3, 5 if m != "AU" else 8, -math.pi / 2))
    elif m in ("SO",):                                     # спираль
        path_r = [u * 0.6, u * 0.42, u * 0.24]
        for i, rr in enumerate(path_r):
            p.drawArc(QRectF(cx - rr, cy - rr, rr * 2, rr * 2), (i * 60) * 16, 270 * 16)
    elif m in ("HR", "SD", "PF"):                          # молния / череп — восклицание
        p.drawPolyline(QPolygonF([QPointF(cx + u * 0.15, cy - u * 0.7), QPointF(cx - u * 0.3, cy + u * 0.05),
                                  QPointF(cx + u * 0.2, cy + u * 0.05), QPointF(cx - u * 0.15, cy + u * 0.7)]))
    elif m in ("NF",):                                     # сетка
        for i in range(-1, 2):
            p.drawLine(QPointF(cx - u * 0.6, cy + i * u * 0.3), QPointF(cx + u * 0.6, cy + i * u * 0.3))
            p.drawLine(QPointF(cx + i * u * 0.3, cy - u * 0.5), QPointF(cx + i * u * 0.3, cy + u * 0.5))
    else:
        p.drawEllipse(QPointF(cx, cy), u * 0.5, u * 0.5)


def osu_mod_icon(m, size, dpr, on=True):
    """Значок мода как в osu!: квадрат цвета мода с объёмом, значок и подпись."""
    s = float(size)
    pm = QPixmap(int((s + 4) * dpr), int((s + 4) * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    col = MOD_COL.get(m, QColor(255, 102, 170))
    if not on:                                             # как в osu!: невыбранные — того же цвета, чуть темнее
        col = SK.darker(col, 0.18)
    r = QRectF(2, 2, s, s)
    gr = QLinearGradient(r.topLeft(), r.bottomLeft())
    gr.setColorAt(0, SK.lighter(col, 0.3))
    gr.setColorAt(1, SK.darker(col, 0.15))
    p.setPen(QPen(QColor(255, 255, 255, 90 if on else 40), max(1.0, s * 0.025)))
    p.setBrush(QBrush(gr))
    p.drawRoundedRect(r, s * 0.08, s * 0.08)
    fg = QColor(255, 255, 255, 255 if on else 215)
    _glyph(p, m, QRectF(r.left() + s * 0.2, r.top() + s * 0.08, s * 0.6, s * 0.52), fg)
    f = QFont(SK.FONT)
    name = MOD_FULL.get(m, m)
    f.setPixelSize(max(6, int(s * (0.2 if len(name) <= 6 else 0.16))))
    f.setWeight(QFont.Weight.DemiBold)
    p.setFont(f)
    p.setPen(QColor(0, 0, 0, 120))
    p.drawText(QRectF(r.left(), r.top() + s * 0.62 + 1, s, s * 0.34), Qt.AlignmentFlag.AlignCenter, name)
    p.setPen(fg)
    p.drawText(QRectF(r.left(), r.top() + s * 0.62, s, s * 0.34), Qt.AlignmentFlag.AlignCenter, name)
    p.end()
    return pm


def _mod_pix(self, m, size, dpr, on=True):
    key = ("mod+", m, int(size), round(dpr, 2), bool(on), id(_files()), self.osu_look())
    pm = self._pix.get(key)
    if pm is not None:
        return pm
    sf = _files()
    if sf is not None and sf.has(f"selection-mod-{MOD_FILES.get(m, '')}"):
        img, _k = sf.image(f"selection-mod-{MOD_FILES[m]}")
        if img is not None:
            sc = size * dpr / max(1, img.height())
            pm = QPixmap.fromImage(img.scaled(int(img.width() * sc), int(img.height() * sc),
                                              Qt.AspectRatioMode.KeepAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation))
            pm.setDevicePixelRatio(dpr)
            if not on:
                q = QPainter(pm)
                q.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
                q.fillRect(pm.rect(), QColor(0, 0, 0, 110))
                q.end()
    if pm is None and self.osu_look():
        pm = osu_mod_icon(m, size, dpr, on)
    if pm is None:
        return _O["mod_pix"](self, m, size, dpr, on)
    if len(self._pix) > 300:
        self._pix.clear()
    self._pix[key] = pm
    return pm


# ------------------------------------------------------------------ #

def install() -> bool:
    global SK, GM
    try:
        import osu_game
        import osu_skin
        import osu_theme
    except Exception as e:                                 # noqa: BLE001
        print("[skin+]", e)
        return False
    SK, GM = osu_skin, osu_game
    G = osu_game.OsuGame
    if getattr(G, "_skin_plus", False):
        return False
    _O.update(pops=G._draw_pops, arrows=G._draw_arrows, spinner=G._draw_spinner, hud=G._draw_hud, menu=G._draw_menu,
              cursor=G._draw_cursor, kp=G.keyPressEvent, kr=G.keyReleaseEvent, start=G.start,
              mod_pix=osu_theme.OsuShell.mod_pix)
    G._draw_pops = _draw_pops
    G._draw_arrows = _draw_arrows
    G._draw_spinner = _draw_spinner
    G._draw_hud = _draw_hud
    G._draw_menu = _draw_menu
    G._draw_cursor = _draw_cursor
    G.keyPressEvent = _key_press
    G.keyReleaseEvent = _key_release
    G.start = _game_start
    osu_theme.OsuShell.mod_pix = _mod_pix
    G._skin_plus = True
    return True
