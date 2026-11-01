# osu_game.py
"""
Игра темы «esu!»: режим osu!standard по картам генератора (osu_mapgen) и по картам из
настоящего osu! (osu_beatmap).

Play — правила (без Qt): окна попаданий по OD, notelock, слайдеры (голова, тики, повторы,
конец, слежение 2.4R), спиннеры (обороты, бонусы, предел 477 об/мин), счёт ScoreV1,
точность, HP с пассивной утечкой, «стопки» нот как в osu! (StackLeniency), пропуск цветов
комбо, моды EZ NF HT HR SD PF DT NC HD FL RX AP AU CN SO.

OsuGame — виджет: часы игры по звуку плеера (с мягкой подстройкой, без рывков назад),
ввод (клавиши K1/K2, кнопки мыши, raw input Windows с чувствительностью, захват курсора),
отрисовка из кэша спрайтов (osu_skin): подход, взрывы, подсветка, частицы, follow points,
змейка слайдеров, kiai-вспышки и фонтаны звёзд, след и волны курсора, HUD (счёт, точность,
комбо, HP, прогресс, клавиши, шкала ошибок), перерывы, отсчёт, пропуск вступления, пауза,
провал с замедлением музыки, запись и просмотр повтора.
"""
from __future__ import annotations

import bisect
import ctypes
import math
import os
import random
import time

import numpy as np
from img_load import load_pixmap
from PyQt6.QtCore import QAbstractNativeEventFilter, QEvent, QPoint, QPointF, QRect, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (QBrush, QColor, QCursor, QFont, QFontMetricsF, QLinearGradient, QPainter, QPainterPath, QPen,
                         QPixmap, QPolygonF, QRadialGradient)
from PyQt6.QtWidgets import QApplication, QWidget

import osu_mapgen as G
import osu_skin as SK
from frameclock import FrameTimer

try:                                                       # C++-компоновщик спрайтов (echoes_fx.dll)
    import native_fx as NF
    _BP = NF.BatchPainter if NF.available() else None
except Exception:                                          # noqa: BLE001
    NF, _BP = None, None


def native_frames_enabled() -> bool:
    """Кадры esu! собираются C++-компоновщиком (настройка «C++-ускорение отрисовки»)."""
    return _BP is not None and NATIVE_FRAMES[0]


NATIVE_FRAMES = [True]

_REDIRECT: dict = {}                    # id(виджета) → BatchPainter текущего кадра


class RedirectPainter(QPainter):
    """Подставляется вместо QPainter в модулях экранов esu! (osu_theme, osu_y2k, osu_classic):
    QPainter(экран) во время его кадра возвращает общий BatchPainter (C++), всё остальное —
    обычный QPainter. Так ускоряются и стили, которые подменяют paintEvent целиком."""

    def __new__(cls, *a, **k):
        if len(a) == 1 and not k and _REDIRECT:
            bp = _REDIRECT.get(id(a[0]))
            if bp is not None:
                return bp
        return QPainter(*a, **k)


def redirect_modules(*mods):
    for m in mods:
        if getattr(m, "QPainter", None) is QPainter:
            m.QPainter = RedirectPainter


def paint_redirected(widget, dispatch, e):
    """Обработать событие отрисовки widget так, чтобы его QPainter(widget) рисовал в кадр C++.

    Выгода зависит от экрана: где много картинок — C++ быстрее, где сплошь текст и векторные
    фигуры (стиль Y2K) — прямой QPainter дешевле на копию кадра. Поэтому экран раз в ~600 кадров
    20 кадров через один пробует оба способа (картинка одинаковая) и дальше рисует более быстрым."""
    if not native_frames_enabled():
        return dispatch(e)
    st = widget.__dict__.get("_nf_stat")
    if st is None:
        st = widget.__dict__["_nf_stat"] = {"n": 0, "mode": True, "acc": [0.0, 0, 0.0, 0]}
    st["n"] += 1
    # пробы второго способа — сами по себе подтормаживания; когда C++ заметно быстрее, пробуем редко
    period = st.get("period", 600)
    phase = st["n"] % period
    trial = phase < 40
    native = (phase % 2 == 0) if trial else st["mode"]
    t0 = time.perf_counter()
    try:
        if not native:
            return dispatch(e)
        p = frame_painter(widget)
        p.shared = True
        _REDIRECT[id(widget)] = p
        try:
            return dispatch(e)
        finally:
            _REDIRECT.pop(id(widget), None)
            frame_present(widget, p)
    finally:
        if trial:
            a = st["acc"]
            k = 0 if native else 2
            a[k] += time.perf_counter() - t0
            a[k + 1] += 1
            if phase == 39 and a[1] and a[3]:
                nat, qp = a[0] / a[1], a[2] / a[3]
                st["mode"] = nat <= qp
                st["period"] = 30000 if nat < qp * 0.75 else 600
                st["acc"] = [0.0, 0, 0.0, 0]


def frame_painter(widget, holder: str = "_ft"):
    """Начать кадр виджета: BatchPainter поверх собственного буфера (C++) или обычный QPainter.
    Закончить — frame_present(widget, p)."""
    if native_frames_enabled():
        ft = getattr(widget, holder, None)
        if ft is None:
            ft = NF.FrameTarget()
            setattr(widget, holder, ft)
        return ft.begin(widget.width(), widget.height(), widget.devicePixelRatioF())
    ft = getattr(widget, holder, None)
    if ft is not None:                                     # ускорение выключили — отпустить память
        ft.release()
        setattr(widget, holder, None)
    return QPainter(widget)


def frame_present(widget, p, holder: str = "_ft"):
    if _BP is not None and p.__class__ is _BP:
        p.finish()
    else:
        p.end()
    if _BP is not None and p.__class__ is _BP:
        wp = QPainter(widget)
        getattr(widget, holder).present(wp)
        wp.end()

OSU_DEFAULTS = {
    "sens": 1.0, "raw": True, "confine": True, "k1": "Z", "k2": "X", "mouse_buttons": True,
    "dim": 0.7, "bg": "cover", "blur": False, "hud": True, "hit_error": True, "key_overlay": True,
    "show300": True, "hit_light": True, "particles": True, "snaking": True, "followpoints": True,
    "kiai_fx": True, "cursor_size": 1.0, "cursor_trail": True, "ripples": True, "countdown": True,
    "sfx_vol": 0.6, "music_vol": None, "offset": 0, "fail_fx": True, "bg_pulse": True, "combo_burst": True,
    "menu_parallax": True, "mapgen": {}, "mods": [], "diff": "hard", "player": "",
    "leaderboard": True, "map_hitsounds": True, "map_colours": True, "map_video": True, "auto_offset": True,
    "osu_scan": True, "palette": "classic", "skin": "", "skin_menu_bg": True, "map_skin": True,
    # вид как в osu!: стандартный скин нот, шар слайдера из скина — по желанию (часто «3D»), заставка welcome,
    # музыка при входе в esu! ("keep" — что играло, "random" — случайный трек, "track" — выбранный menu_track)
    "note_style": "osu", "skin_ball": False, "intro": True, "menu_music": "keep", "menu_track": "",
    "menu_track_chorus": True,
}

MOD_MULT = {"EZ": 0.5, "NF": 0.5, "HT": 0.3, "HR": 1.06, "SD": 1.0, "PF": 1.0, "DT": 1.12, "NC": 1.12, "HD": 1.06,
            "FL": 1.12, "RX": 0.0, "AP": 0.0, "AU": 1.0, "CN": 1.0, "SO": 0.9}
MOD_NAMES = {"EZ": "Easy", "NF": "No Fail", "HT": "Half Time", "HR": "Hard Rock", "SD": "Sudden Death",
             "PF": "Perfect", "DT": "Double Time", "NC": "Nightcore", "HD": "Hidden", "FL": "Flashlight",
             "RX": "Relax", "AP": "Autopilot", "AU": "Auto", "CN": "Cinema", "SO": "Spun Out"}
WATCH_MODS = {"AU", "CN"}                 # смотреть, как карту проходит автоигра


def mods_rate(mods) -> float:
    return 1.5 if ("DT" in mods or "NC" in mods) else 0.75 if "HT" in mods else 1.0


def mods_mult(mods) -> float:
    k = 1.0
    for m in mods:
        k *= MOD_MULT.get(m, 1.0)
    return k


def grade_of(c300, c100, c50, c0, mods=()) -> str:
    n = c300 + c100 + c50 + c0
    if n == 0:
        return "D"
    acc = (300 * c300 + 100 * c100 + 50 * c50) / (300 * n)
    r300, r50 = c300 / n, c50 / n
    silver = "HD" in mods or "FL" in mods
    if acc >= 1.0:
        g = "SS"
    elif r300 > 0.9 and r50 < 0.01 and c0 == 0:
        g = "S"
    elif (r300 > 0.8 and c0 == 0) or r300 > 0.9:
        g = "A"
    elif (r300 > 0.7 and c0 == 0) or r300 > 0.8:
        g = "B"
    elif r300 > 0.6:
        g = "C"
    else:
        g = "D"
    if silver and g in ("SS", "S"):
        g += "H"
    return g


# ------------------------------------------------------------------ #
#  Правила                                                            #
# ------------------------------------------------------------------ #

class Play:
    MAX_SPIN = 477 / 60000 * 2 * math.pi             # рад/мс

    def __init__(self, m: dict, mods):
        self.m = m
        self.mods = set(mods)
        self.rate = mods_rate(self.mods)
        self.cs, self.ar, self.od, self.hp_set = G.apply_mods(m["cs"], m["ar"], m["od"], m["hp"], self.mods)
        self.r = G.radius(self.cs)
        self.pre = G.preempt(self.ar)
        self.fin = G.fade_in(self.ar)
        self.w300, self.w100, self.w50 = G.hit_windows(self.od)
        objs = [dict(o) for o in m["objects"]]
        G.prepare({"objects": objs, "tick_rate": m.get("tick_rate", 1)})
        # «стопки»: ноты в одной точке в пределах StackLeniency чуть сдвигаются вверх-влево, как в osu!
        try:
            import osu_beatmap as OB
            OB.apply_stacking(objs, self.pre, float(m.get("stack_leniency", 0.7)), int(m.get("file_version", 14)))
            OB.offset_stacks(objs, self.cs)
        except Exception as e:                         # noqa: BLE001
            print("[esu] stacking:", e)
        ci, num = -1, 0
        for k, o in enumerate(objs):
            if "HR" in self.mods:
                o["y"] = 384 - o["y"]
                if o["k"] == 1:
                    o["_path"] = o["_path"].copy()
                    o["_path"][:, 1] = 384 - o["_path"][:, 1]
            if o["k"] == 2 or (k > 0 and objs[k - 1]["k"] == 2):
                o["nc"] = True                         # спиннер и нота после него — всегда новое комбо
            if o.get("nc") or ci < 0:
                ci += 1 + (int(o.get("skip", 0)) if ci >= 0 else 0)   # пропуск цветов комбо (карты osu!)
                num = 0
            num += 1
            o["ci"], o["num"] = ci, num
            o["j"] = None                              # итог
            o["hj"] = None                             # голова (круг/слайдер): время попадания или False
            if o["k"] == 1:
                o["ti"] = 0                            # следующий тик/повтор
                o["parts"] = []                        # [(время, вид, x, y)] — тики, повторы, конец
                for tt in o["ticks"]:
                    o["parts"].append((tt, "tick"))
                for tt in o["repeats"]:
                    o["parts"].append((tt, "rep"))
                # конец слайдера проверяется чуть раньше настоящего конца (как в osu!: −36 мс)
                o["parts"].append((max(o["t"] + o["span"] * (o["slides"] - 0.5), o["end"] - 36), "end"))
                o["parts"].sort()
                o["got"] = 0
                o["track"] = False
                o["track_t"] = -1e9
            elif o["k"] == 2:
                o["rot"] = 0.0
                o["last_a"] = None
                o["spins"] = 0
                o["rpm"] = 0.0
                o["need"] = G.spins_needed(self.od, (o["end"] - o["t"]) / 1.0)
                o["bonus"] = 0
        for k, o in enumerate(objs):                   # последний в комбо — для гэки/кацу
            o["last"] = k == len(objs) - 1 or objs[k + 1].get("nc", False)
        self.objs = objs
        self.heads = [k for k, o in enumerate(objs) if o["k"] != 2]
        self.hq = 0
        self.lo = 0
        self.score = 0
        self.combo = 0
        self.max_combo = 0
        self.counts = {300: 0, 100: 0, 50: 0, 0: 0}
        self.geki = self.katu = 0
        self._combo_ok300 = True
        self._combo_ok = True
        self.hp = 1.0
        self.failed = False
        self.errors: list = []
        self.events: list = []
        self.hp_hist: list = []
        n = len(objs)
        first = objs[0]["t"] if objs else 0
        last = max((o.get("end", o["t"]) for o in objs), default=0)
        brk = sum(b - a for a, b in m.get("breaks", []))
        drain_s = max(1.0, (last - first - brk) / 1000.0)
        self.diff_mult = round((self.hp_set + self.cs + self.od + max(0, min(16, n / drain_s * 8))) / 38 * 5)
        self.mod_mult = mods_mult(self.mods)
        h = self.hp_set
        self.drain = (0.004 + 0.0042 * h) / 1000.0
        self.gain = {300: 0.065 - 0.003 * h, 100: 0.02 - 0.0012 * h, 50: -0.002 * h}
        self.miss_pen = 0.05 + 0.018 * h
        self.first_t = first
        self.last_t = last
        self.breaks = m.get("breaks", [])
        self.no_fail = bool(self.mods & {"NF", "AU", "CN", "RX", "AP"}) or False
        self.done = False

    # ── итоги ── #
    @property
    def total(self):
        return sum(self.counts.values())

    @property
    def acc(self) -> float:
        n = self.total
        if n == 0:
            return 1.0
        c = self.counts
        return (300 * c[300] + 100 * c[100] + 50 * c[50]) / (300 * n)

    def in_break(self, t) -> bool:
        return any(a <= t <= b for a, b in self.breaks)

    def _ev(self, *e):
        self.events.append(e)

    def _hp(self, d):
        self.hp = max(0.0, min(1.0, self.hp + d))
        if self.hp <= 0 and not self.no_fail:
            self.failed = True

    def _combo_up(self):
        self.combo += 1
        self.max_combo = max(self.max_combo, self.combo)

    def _break_combo(self, t, x, y):
        if self.combo >= 20:
            self._ev("combobreak", t, x, y)
        self.combo = 0
        if "SD" in self.mods or "PF" in self.mods:
            self.failed = True

    def judge(self, o, kind, t, x, y, combo=True):
        o["j"] = kind
        self.counts[kind] += 1
        if kind == 0:
            self._break_combo(t, x, y)
            self._hp(-self.miss_pen)
            self._combo_ok = self._combo_ok300 = False
        else:
            if combo:
                self._combo_up()
            self.score += kind + int(kind * max(0, self.combo - 1) * self.diff_mult * self.mod_mult / 25)
            self._hp(self.gain[kind])
            if kind != 300:
                self._combo_ok300 = False
                if "PF" in self.mods:
                    self.failed = True
        if o.get("last"):
            if self._combo_ok300 and kind == 300:
                self.geki += 1
                o["_gk"] = "300g"                      # конец комбо: оценка — 激 вместо «300»
            elif self._combo_ok and kind > 0:
                self.katu += 1
                o["_gk"] = {300: "300k", 100: "100k"}.get(kind)
            self._combo_ok = self._combo_ok300 = True
        self._ev("judge", o, kind, t, x, y)

    # ── нажатие ── #
    def _first_head(self):
        while self.hq < len(self.heads) and self.objs[self.heads[self.hq]]["hj"] is not None:
            self.hq += 1
        return self.objs[self.heads[self.hq]] if self.hq < len(self.heads) else None

    def press(self, t, x, y) -> bool:
        first = self._first_head()
        if first is None:
            return False
        r2 = (self.r * 1.0) ** 2
        for k in range(self.hq, min(len(self.heads), self.hq + 10)):
            o = self.objs[self.heads[k]]
            if o["hj"] is not None:
                continue
            if t < o["t"] - self.pre:
                break
            if (x - o["x"]) ** 2 + (y - o["y"]) ** 2 > r2:
                continue
            if o is not first:
                self._ev("shake", o, t)
                return False
            d = t - o["t"]
            if abs(d) <= self.w50:
                self.errors.append((t, d))
                o["hj"] = t
                if o["k"] == 0:
                    kind = 300 if abs(d) <= self.w300 else 100 if abs(d) <= self.w100 else 50
                    self.judge(o, kind, t, o["x"], o["y"])
                    self._ev("hit", o, t)
                else:
                    o["got"] += 1
                    self._combo_up()
                    self.score += 30
                    self._hp(self.gain[300] * 0.5)
                    self._ev("head", o, t)
                return True
            if d < -self.w50:
                if d > -400:
                    self._ev("shake", o, t)
                return False
            return False
        return False

    # ── шаг времени ── #
    def update(self, t, dt, x, y, held: bool):
        objs = self.objs
        while self.lo < len(objs) and objs[self.lo]["j"] is not None:
            self.lo += 1
        if not self.in_break(t) and self.first_t - 500 <= t <= self.last_t and not self.failed:
            self._hp(-self.drain * dt)
        for k in range(self.lo, len(objs)):
            o = objs[k]
            if o["t"] - self.pre > t:
                break
            if o["j"] is not None:
                continue
            if o["k"] == 0:
                if t > o["t"] + self.w50:
                    o["hj"] = False
                    self.judge(o, 0, t, o["x"], o["y"])
            elif o["k"] == 1:
                self._slider(o, t, x, y, held)
            else:
                self._spinner(o, t, dt, x, y, held)
        self.hp_hist.append((t, self.hp)) if (not self.hp_hist or t - self.hp_hist[-1][0] > 250) else None
        if not self.done and self.lo >= len(objs) and objs:
            self.done = True

    def _slider(self, o, t, x, y, held):
        if o["hj"] is None and t > o["t"] + self.w50:
            o["hj"] = False
            self._break_combo(t, o["x"], o["y"])
            self._hp(-self.miss_pen * 0.5)
            self._ev("headmiss", o, t)
        if t < o["t"]:
            return
        bx, by = G.slider_pos(o, min(t, o["end"]))
        rad = self.r * 2.4 if o["track"] else self.r
        tracking = held and (x - bx) ** 2 + (y - by) ** 2 <= rad * rad
        if tracking and not o["track"]:
            o["track_t"] = t
        if not tracking and o["track"]:
            o["track_t"] = t
        o["track"] = tracking
        while o["ti"] < len(o["parts"]) and o["parts"][o["ti"]][0] <= t:
            pt, kind = o["parts"][o["ti"]]
            o["ti"] += 1
            px, py = G.slider_pos(o, pt)
            if tracking:
                o["got"] += 1
                self._combo_up()
                self.score += 10 if kind == "tick" else 30
                self._hp(0.012 if kind == "tick" else 0.025)
                self._ev("part", o, kind, pt, px, py)
            else:
                if kind != "end":
                    self._break_combo(pt, px, py)
                    self._hp(-(0.02 + 0.005 * self.hp_set))
                self._ev("partmiss", o, kind, pt, px, py)
        if t >= o["end"]:
            tot = 1 + len(o["parts"])
            f = o["got"] / tot
            kind = 300 if f >= 1 else 100 if f >= 0.5 else 50 if f > 0 else 0
            ex, ey = G.slider_pos(o, o["end"])
            if kind == 0:
                o["j"] = 0
                self.counts[0] += 1
                self._hp(-self.miss_pen * 0.5)
                self._combo_ok = self._combo_ok300 = False
                self._ev("judge", o, 0, t, ex, ey)
            else:
                self.judge(o, kind, t, ex, ey, combo=False)

    def _spinner(self, o, t, dt, x, y, held):
        if t < o["t"]:
            return
        if t <= o["end"]:
            auto = bool(self.mods & {"SO", "AU", "CN", "AP"})
            if auto:
                o["rot"] += self.MAX_SPIN * 0.92 * dt
                o["rpm"] = 440.0
            elif held:
                a = math.atan2(y - 192, x - 256)
                if o["last_a"] is not None and dt > 0:
                    d = a - o["last_a"]
                    d = (d + math.pi) % (2 * math.pi) - math.pi
                    d = max(-self.MAX_SPIN * dt, min(self.MAX_SPIN * dt, d))
                    o["rot"] += abs(d)
                    inst = abs(d) / max(1e-6, dt) * 60000 / (2 * math.pi)
                    o["rpm"] += (inst - o["rpm"]) * min(1.0, dt / 250)
                o["last_a"] = a
            else:
                o["last_a"] = None
                o["rpm"] *= max(0.0, 1 - dt / 400)
            spins = int(o["rot"] / (2 * math.pi))
            while o["spins"] < spins:
                o["spins"] += 1
                if o["spins"] > o["need"]:
                    o["bonus"] += 1
                    self.score += 1000
                    self._ev("bonus", o, t)
                else:
                    self.score += 100
                    self._ev("spin", o, t)
                self._hp(0.01)
        if t > o["end"]:
            f = o["spins"] / max(1e-6, o["need"])
            kind = 300 if f >= 1 else 100 if f >= 0.9 else 50 if f >= 0.75 else 0
            self.judge(o, kind, t, 256, 192)

    def result(self) -> dict:
        c = self.counts
        errs = [d for _, d in self.errors]
        ur = float(np.std(errs)) * 10 / self.rate if len(errs) > 1 else 0.0
        mean = float(np.mean(errs)) if errs else 0.0
        g = grade_of(c[300], c[100], c[50], c[0], self.mods)
        try:
            sr = G.star_rating({"objects": self.objs, "cs": self.cs}, self.rate)
        except Exception:                              # noqa: BLE001
            sr = {"stars": self.m.get("stars", 0), "aim": self.m.get("aim", 0), "speed": self.m.get("speed", 0)}
        ar2, od2 = G.effective_ar_od(self.ar, self.od, self.rate)
        pp = G.pp_value(sr["aim"], sr["speed"], self.acc, len(self.objs), self.max_combo,
                        self.m.get("max_combo", self.max_combo), c[0], c[50], od2, ar2, self.mods)
        return {"score": self.score, "acc": self.acc, "combo": self.max_combo, "max_combo": self.m.get("max_combo", 0),
                "c300": c[300], "c100": c[100], "c50": c[50], "c0": c[0], "geki": self.geki, "katu": self.katu,
                "grade": g, "mods": sorted(self.mods), "pp": pp, "ur": round(ur, 1), "mean_err": round(mean, 1),
                "stars": sr["stars"], "hp_hist": self.hp_hist[::2], "errors": errs[-400:],
                "failed": self.failed, "time": time.time()}


# ------------------------------------------------------------------ #
#  Автоигра (мод Auto)                                                #
# ------------------------------------------------------------------ #

class AutoPilot:
    def __init__(self, play: Play):
        self.p = play
        self.idx = 0
        self.key = 0
        self.last = (256.0, 192.0, -1e9)

    def update(self, t):
        """→ (x, y, held, [время нажатия, …])."""
        P = self.p
        objs = P.objs
        presses = []
        while self.idx < len(objs) and objs[self.idx]["t"] <= t:
            o = objs[self.idx]
            if o["k"] != 2:
                presses.append(o)
            self.idx += 1
        cur = None
        for k in range(max(0, self.idx - 3), min(len(objs), self.idx + 1)):
            o = objs[k]
            if o["k"] != 0 and o["t"] <= t <= o.get("end", o["t"]):
                cur = o
        held = False
        if cur is not None and cur["k"] == 1:
            x, y = G.slider_pos(cur, t)
            held = True
            self.last = (x, y, t)
        elif cur is not None and cur["k"] == 2:
            a = (t - cur["t"]) * Play.MAX_SPIN * 0.92
            x, y = 256 + 60 * math.cos(a), 192 + 60 * math.sin(a)
            held = True
            self.last = (x, y, t)
        else:
            nxt = objs[self.idx] if self.idx < len(objs) else None
            lx, ly, lt = self.last
            if nxt is None:
                x, y = lx, ly
            else:
                nx, ny = (256.0, 192.0 + 60) if nxt["k"] == 2 else (float(nxt["x"]), float(nxt["y"]))
                span = max(1.0, nxt["t"] - max(lt, nxt["t"] - 1200))
                f = min(1.0, max(0.0, (t - max(lt, nxt["t"] - 1200)) / span))
                e = f * f * (3 - 2 * f)
                x, y = lx + (nx - lx) * e, ly + (ny - ly) * e
                if f >= 1.0:
                    self.last = (nx, ny, nxt["t"])
            if presses:
                o = presses[-1]
                self.last = (float(o["x"]), float(o["y"]), o["t"]) if o["k"] == 0 else self.last
            held = any(t - o["t"] < 60 for o in presses)
        return x, y, held, presses


# ------------------------------------------------------------------ #
#  Raw input (Windows)                                                #
# ------------------------------------------------------------------ #

class RawMouse(QAbstractNativeEventFilter):
    """WM_INPUT: «сырые» смещения мыши без ускорения Windows — для чувствительности как в osu!."""

    def __init__(self, cb):
        super().__init__()
        self.cb = cb
        self.ok = False
        self.hwnd = None
        try:
            from ctypes import wintypes as W
            self.W = W
            u = ctypes.windll.user32

            class RID(ctypes.Structure):
                _fields_ = [("usUsagePage", W.USHORT), ("usUsage", W.USHORT), ("dwFlags", W.DWORD),
                            ("hwndTarget", W.HWND)]

            class HDR(ctypes.Structure):
                _fields_ = [("dwType", W.DWORD), ("dwSize", W.DWORD), ("hDevice", W.HANDLE), ("wParam", W.WPARAM)]

            class BTN(ctypes.Structure):
                _fields_ = [("usButtonFlags", W.USHORT), ("usButtonData", W.USHORT)]

            class U(ctypes.Union):
                _fields_ = [("ulButtons", W.ULONG), ("b", BTN)]

            class MOUSE(ctypes.Structure):
                _fields_ = [("usFlags", W.USHORT), ("u", U), ("ulRawButtons", W.ULONG), ("lLastX", W.LONG),
                            ("lLastY", W.LONG), ("ulExtraInformation", W.ULONG)]

            class RAW(ctypes.Structure):
                _fields_ = [("header", HDR), ("mouse", MOUSE)]
            self.RID, self.HDR, self.RAW = RID, HDR, RAW
            self.u = u
            u.GetRawInputData.argtypes = [W.HANDLE, W.UINT, ctypes.c_void_p, ctypes.POINTER(W.UINT), W.UINT]
            u.GetRawInputData.restype = W.UINT
            self.buf = ctypes.create_string_buffer(max(256, ctypes.sizeof(RAW)))
        except Exception:                              # noqa: BLE001
            self.u = None

    def register(self, hwnd: int) -> bool:
        if self.u is None:
            return False
        try:
            r = self.RID(1, 2, 0, hwnd)
            self.ok = bool(self.u.RegisterRawInputDevices(ctypes.byref(r), 1, ctypes.sizeof(r)))
            self.hwnd = hwnd
        except Exception:                              # noqa: BLE001
            self.ok = False
        return self.ok

    def unregister(self):
        if self.u is None or not self.ok:
            return
        try:
            r = self.RID(1, 2, 0x00000001, None)       # RIDEV_REMOVE
            self.u.RegisterRawInputDevices(ctypes.byref(r), 1, ctypes.sizeof(r))
        except Exception:                              # noqa: BLE001
            pass
        self.ok = False

    def nativeEventFilter(self, eventType, message):
        try:
            if self.ok and bytes(eventType) == b"windows_generic_MSG":
                msg = self.W.MSG.from_address(int(message))
                if msg.message == 0x00FF:
                    sz = self.W.UINT(len(self.buf))
                    got = self.u.GetRawInputData(msg.lParam, 0x10000003, self.buf, ctypes.byref(sz),
                                                 ctypes.sizeof(self.HDR))
                    if got not in (0, 0xFFFFFFFF):
                        ri = self.RAW.from_buffer_copy(self.buf)
                        if ri.header.dwType == 0:
                            m = ri.mouse
                            self.cb(int(m.lLastX), int(m.lLastY), bool(m.usFlags & 1))
        except Exception:                              # noqa: BLE001
            pass
        return False, 0                                # (False, None) роняет PyQt6 — только 0


def _clip_cursor(rect: QRect | None):
    try:
        from ctypes import wintypes as W
        if rect is None:
            ctypes.windll.user32.ClipCursor(None)
        else:
            r = W.RECT(rect.left(), rect.top(), rect.right(), rect.bottom())
            ctypes.windll.user32.ClipCursor(ctypes.byref(r))
    except Exception:                                  # noqa: BLE001
        pass


# ------------------------------------------------------------------ #
#  Цифры HUD из кэша                                                  #
# ------------------------------------------------------------------ #

class Digits:
    """Цифры счёта/комбо: глифы из кэша на общей базовой линии."""

    def __init__(self, size, color=QColor(255, 255, 255), dpr=1.0, outline=QColor(0, 0, 0, 170), italic=False,
                 weight=QFont.Weight.Bold):
        self.size = size
        f = _font(size, weight, italic)
        fm = QFontMetricsF(f)
        asc, desc = fm.ascent(), fm.descent()
        ow = size * 0.07
        self.pad = pad = ow * 2 + 2
        self.h = asc + desc + pad * 2
        self.g, self.adv = {}, {}
        for ch in "0123456789.,%x+:-":
            adv = fm.horizontalAdvance(ch)
            pm = SK._pm(adv + pad * 2, self.h, dpr)
            p = SK._painter(pm)
            path = QPainterPath()
            path.addText(pad, pad + asc, f, ch)
            p.strokePath(path, QPen(outline, ow * 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                                    Qt.PenJoinStyle.RoundJoin))
            p.fillPath(path, color)
            p.end()
            self.g[ch] = pm
            self.adv[ch] = adv * 0.96

        self._dpr = dpr
        self._lines = {}

    def width(self, s):
        return sum(self.adv.get(c, self.size * 0.5) for c in s)

    def line(self, s: str) -> QPixmap:
        """Вся строка одной картинкой (кэш) — один drawPixmap за кадр вместо десятка."""
        pm = self._lines.get(s)
        if pm is None:
            if len(self._lines) > 48:
                self._lines.clear()
            w = self.width(s) + self.pad * 2 + self.size
            pm = SK._pm(w, self.h, self._dpr)
            q = QPainter(pm)
            x = self.pad
            for c in s:
                g = self.g.get(c)
                if g is not None:
                    q.drawPixmap(QPointF(x - self.pad, 0), g)
                x += self.adv.get(c, self.size * 0.5)
            q.end()
            self._lines[s] = pm
        return pm

    def draw(self, p: QPainter, s: str, x, y, align="l", scale=1.0, alpha=1.0):
        w = self.width(s) * scale
        if align == "r":
            x -= w
        elif align == "c":
            x -= w / 2
        pm = self.line(s)
        p.setOpacity(alpha)
        if scale == 1.0:
            p.drawPixmap(QPointF(round(x - self.pad), round(y - self.h / 2)), pm)
        else:
            d = pm.devicePixelRatio()
            p.drawPixmap(QRectF(x - self.pad * scale, y - self.h * scale / 2, pm.width() / d * scale,
                                pm.height() / d * scale), pm, QRectF(pm.rect()))
        p.setOpacity(1.0)


def blit(p: QPainter, pm: QPixmap, cx, cy, sc=1.0, a=1.0, rot=0.0):
    if a <= 0.004 or pm is None:
        return
    if p.__class__ is _BP and p.blit(pm, cx, cy, sc, a, rot):
        return
    d = pm.devicePixelRatio()
    w, h = pm.width() / d * sc, pm.height() / d * sc
    p.setOpacity(min(1.0, a))
    if rot:
        p.save()
        p.translate(cx, cy)
        p.rotate(rot)
        p.drawPixmap(QRectF(-w / 2, -h / 2, w, h), pm, QRectF(pm.rect()))
        p.restore()
    elif sc == 1.0:
        # без масштаба — в целые пиксели: растровый движок рисует такое в разы быстрее
        p.drawPixmap(QPointF(round(cx - w / 2), round(cy - h / 2)), pm)
    else:
        p.drawPixmap(QRectF(cx - w / 2, cy - h / 2, w, h), pm, QRectF(pm.rect()))


def _ease_out(x):
    x = min(1.0, max(0.0, x))
    return 1 - (1 - x) ** 3


def _font(px, weight=QFont.Weight.Bold, italic=False):
    f = QFont(SK.FONT)
    f.setPixelSize(max(1, int(px)))
    f.setWeight(weight)
    f.setItalic(italic)
    return f


def _qkey(name: str) -> int:
    name = norm_key((name or "").strip())
    if not name:
        return 0
    k = getattr(Qt.Key, "Key_" + name, None)
    if k is None and len(name) == 1:
        k = getattr(Qt.Key, "Key_" + name.upper(), None)
    return int(k.value) if k is not None else 0


def key_name(key: int, text: str = "") -> str:
    for n in dir(Qt.Key):
        if n.startswith("Key_") and int(getattr(Qt.Key, n).value) == key:
            return n[4:]
    return norm_key(text.upper()) or "?"


# Клавиши игры узнаются по физическому месту, а не по букве раскладки: в русской
# раскладке Z печатает «Я», и раньше K1/K2 просто не срабатывали.
_VK_NAMED = {"QuoteLeft": 0xC0, "Minus": 0xBD, "Equal": 0xBB, "BracketLeft": 0xDB, "BracketRight": 0xDD,
             "Semicolon": 0xBA, "Apostrophe": 0xDE, "Comma": 0xBC, "Period": 0xBE, "Slash": 0xBF,
             "Backslash": 0xDC}
_RU_KEYS = dict(zip("ЙЦУКЕНГШЩЗФЫВАПРОЛДЯЧСМИТЬ", "QWERTYUIOPASDFGHJKLZXCVBNM"))
_RU_KEYS.update({"Х": "BracketLeft", "Ъ": "BracketRight", "Ж": "Semicolon", "Э": "Apostrophe", "Б": "Comma",
                 "Ю": "Period", "Ё": "QuoteLeft", "І": "S", "Ї": "BracketRight", "Є": "Apostrophe"})
KEY_LABELS = {"QuoteLeft": "`", "Minus": "-", "Equal": "=", "BracketLeft": "[", "BracketRight": "]",
              "Semicolon": ";", "Apostrophe": "'", "Comma": ",", "Period": ".", "Slash": "/", "Backslash": "\\",
              "Escape": "Esc", "Space": "Пробел", "Return": "Enter", "Enter": "Enter", "Backspace": "Backspace",
              "Tab": "Tab", "Shift": "Shift", "Control": "Ctrl", "Alt": "Alt", "CapsLock": "Caps Lock",
              "Left": "Влево", "Right": "Вправо", "Up": "Вверх", "Down": "Вниз", "": "—"}

# (ключ в настройках, подпись, клавиша по умолчанию)
KEY_BINDS = [
    ("k1", "Левая кнопка (K1)", "Z"),
    ("k2", "Правая кнопка (K2)", "X"),
    ("key_pause", "Пауза / продолжить", "Escape"),
    ("key_retry", "Быстрый рестарт", "QuoteLeft"),
    ("key_skip", "Пропустить вступление", "Space"),
    ("key_off_up", "Смещение карты +5 мс", "Equal"),
    ("key_off_down", "Смещение карты −5 мс", "Minus"),
    ("key_hud", "Скрыть / показать интерфейс игры", "Tab"),
]
KEY_DEFAULTS = {k: d for k, _l, d in KEY_BINDS}


def norm_key(name: str) -> str:
    """Имя клавиши, сохранённое в русской раскладке («Я»), → место на клавиатуре («Z»)."""
    name = (name or "").strip()
    return _RU_KEYS.get(name.upper(), name) if len(name) == 1 else name


def key_label(name: str) -> str:
    name = norm_key(name)
    return KEY_LABELS.get(name, name)


def _vk_of(name: str) -> int:
    name = norm_key(name)
    if len(name) == 1 and name.isascii() and name.isalnum():
        return ord(name.upper())
    return _VK_NAMED.get(name, 0)


def key_from_event(e) -> str:
    """Имя нажатой клавиши для настроек — одинаковое в любой раскладке."""
    try:
        vk = int(e.nativeVirtualKey())
    except Exception:                                  # noqa: BLE001
        vk = 0
    if 0x41 <= vk <= 0x5A or (0x30 <= vk <= 0x39 and not (e.modifiers() & Qt.KeyboardModifier.KeypadModifier)):
        return chr(vk)
    for n, v in _VK_NAMED.items():
        if v == vk:
            return n
    return key_name(e.key(), e.text())


def key_is(e, name: str) -> bool:
    """Нажатая клавиша — та, что в настройках? Сначала по коду Qt, потом по физическому месту (VK)."""
    name = norm_key(name)
    if not name:
        return False
    q = _qkey(name)
    if q and e.key() == q:
        return True
    vk = _vk_of(name)
    if not vk:
        return False
    try:
        return int(e.nativeVirtualKey()) == vk
    except Exception:                                  # noqa: BLE001
        return False


def bind_of(S: dict, key: str) -> str:
    v = S.get(key)
    return norm_key(KEY_DEFAULTS.get(key, "") if v is None else str(v))


# ------------------------------------------------------------------ #
#  Виджет игры                                                        #
# ------------------------------------------------------------------ #

class OsuGame(QWidget):
    finished = pyqtSignal(dict)          # прошли карту (или досмотрели повтор / Auto)
    quit = pyqtSignal()                  # вышли в меню (Esc → «Выйти», провал → «Выйти»)
    retry = pyqtSignal()

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.host = host                 # оболочка темы (win, sfx, settings)
        self.win = host.win
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.play: Play | None = None
        self.state = "idle"
        self.timer = FrameTimer(self)
        self.timer.setInterval(1)
        self.timer.timeout.connect(self._tick)
        self.raw = RawMouse(self._raw_move)
        self._raw_installed = False
        self.cx, self.cy = 256.0, 192.0          # курсор (osu px)
        self._mouse_last = None
        self.keys = {"K1": False, "K2": False, "M1": False, "M2": False}
        self.key_counts = {"K1": 0, "K2": 0, "M1": 0, "M2": 0}
        self.key_flash = {k: 0.0 for k in self.key_counts}
        self.skin = None
        self._layout_sig = None
        self.bg_pm = None
        self.bg_scaled = None
        self.video = None
        self.fx = []                              # частицы/анимации
        self.pops = []                            # всплывающие оценки
        self.ripples = []
        self.trail = []
        self.errors_vis = []
        self.flash = 0.0
        self.kiai = False
        self._beat_i = -1
        self.combo_pop = 0.0
        self.combo_ghost = []
        self.score_disp = 0.0
        self.hp_disp = 1.0
        self.hp_flash = 0.0
        self.paused_t = 0.0
        self.menu_hover = -1
        self.replay = None
        self.rec = None
        self.auto = None
        self.watch = False
        self.offset = 0.0
        self.t = 0.0
        self._hud_cache = {}

    # экран оболочки (как Screen в osu_theme)
    def enter(self):
        pass

    def leave(self):
        if self.state not in ("idle",):
            self.stop(silent=True)
        # экран игры ушёл: картинки тел слайдеров, спрайты, фон на всё окно и сама карта
        # (десятки мегабайт) не нужны до следующего запуска — start() создаёт их заново
        self.play = None
        self.skin = None
        self._body_cache = {}
        self._hud_cache = {}
        self.bg_scaled = None
        self.bg_pm = None
        self.fx, self.pops, self.ripples, self.trail, self.errors_vis = [], [], [], [], []
        try:
            import mem_trim
            mem_trim.trim_later(2500)
        except Exception:                              # noqa: BLE001
            pass

    def step(self, dt, now):
        pass

    # ── настройки ── #
    @property
    def S(self) -> dict:
        return self.host.osu_settings()

    def sfx(self, name, vol=1.0, x=256.0):
        self.host.sfx.play(name, vol, (x - 256) / 256)

    # ── запуск ── #
    def start(self, m: dict, track: dict, mods, replay=None):
        self.stop(silent=True)
        self.map = m
        self.track = track
        mods = set(mods)
        if replay is not None:
            mods = set(replay.get("mods", mods))
        self.play = Play(m, mods)
        self._fail_hi = None
        self.mods = mods
        self.watch = replay is not None or bool(mods & WATCH_MODS)
        self.auto = AutoPilot(self.play) if (mods & (WATCH_MODS | {"AP"}) and replay is None) else None
        self.autopilot = "AP" in mods and replay is None
        self.cinema = "CN" in mods
        self.replay = replay
        self._edge_seen = {}
        self._map_samples = {}
        self._board = []
        try:
            self._board = list(self.host.leaderboard_for(m) or [])[:6] if self.S.get("leaderboard", True) else []
        except Exception:                              # noqa: BLE001
            self._board = []
        self._board_rank = None
        if m.get("osu") and m.get("folder") and self.S.get("map_hitsounds", True):
            self._load_map_samples(m["folder"])
        self._rp_i = 0
        self._rp_f = 0
        self.rec = None if self.watch else {"frames": [], "presses": [], "mods": sorted(mods)}
        self.fx, self.pops, self.ripples, self.trail, self.errors_vis = [], [], [], [], []
        self.combo_ghost = []
        self.score_disp = 0.0
        self.hp_disp = 1.0
        self.flash = 0.0
        self.kiai = False
        self._beat_i = -1
        self._fountain_t = -1e9
        self._sections_done = set()
        self._count_done = set()
        self.key_counts = {k: 0 for k in self.key_counts}
        self.keys = {k: False for k in self.keys}
        self.offset = float(self.S.get("offset", 0)) + float(m.get("_local_offset", 0))
        if m.get("osu") and self.S.get("auto_offset", True):
            self.offset += float(m.get("_auto_offset", 0))       # карта osu! подогнана к звуку по атакам
        self._countdown_on = bool(self.S.get("countdown", True)) and int(m.get("countdown", 1)) != 0
        self._layout_sig = None
        self._body_cache = {}
        self._prepare_layout()
        self._load_background()
        P = self.play
        first = P.first_t
        beat = 60000.0 / max(1.0, m.get("bpm", 120))
        lead = max(P.pre + 600, (4 * beat + 300) if self._countdown_on else 0, float(m.get("lead_in", 0)))
        self.t = min(0.0, first - lead)
        self.skip_to = first - P.pre - 1500 if first - P.pre > 5500 else None
        eng = self.win.engine
        self.host.ensure_track(track)
        self._saved_speed = float(getattr(eng, "speed", 1.0) or 1.0)
        try:
            eng.set_speed(P.rate)
        except Exception:                              # noqa: BLE001
            pass
        try:
            eng.pause_hard()
            eng.seek(max(0.0, self.t / 1000.0))
        except Exception:                              # noqa: BLE001
            pass
        self.audio_on = False
        self._audio_moving = False
        self._audio_p0 = None
        self.state = "play"
        self._last = time.perf_counter()
        self._fail_t = None
        self._end_t = None
        self._resume_pos = None
        self.cx, self.cy = 256.0, 192.0
        self._grab_input(True)
        self.setFocus()
        self._perf_mode(True)
        self.timer.start()
        if self.t >= 0:
            self._start_audio()

    def _perf_mode(self, on):
        """Во время карты: фоновые потоки плеера ждут (bgproc.idle_wait), сборщик мусора Python не
        запускается посреди кадра (его полный проход — десятки мс; память освобождается подсчётом ссылок)."""
        import gc
        try:
            import bgproc
            bgproc.set_fg_busy("esu-game", on)
        except Exception:                              # noqa: BLE001
            pass
        if on and gc.isenabled():
            gc.collect()
            gc.disable()
            self._gc_off = True
        elif not on and getattr(self, "_gc_off", False):
            self._gc_off = False
            gc.enable()

    def _start_audio(self):
        eng = self.win.engine
        try:
            stopped = not eng.is_playing() and not eng.is_paused()       # трек доиграл / остановлен
            eng.seek(max(0.0, self.t / 1000.0 + self.offset / 1000.0))
            eng.play()
            if stopped:                                # после конца трека VLC начинает с нуля — догнать
                QTimer.singleShot(40, lambda: self.state == "play" and eng.seek(
                    max(0.0, (self.t + self.offset) / 1000.0)))
        except Exception:                              # noqa: BLE001
            pass
        self.audio_on = True
        self._audio_moving = False
        self._audio_p0 = None

    def stop(self, silent=False):
        self.timer.stop()
        self._perf_mode(False)
        self._panels(False)
        self._grab_input(False)
        if self.video is not None:
            try:
                self.video.stop()
            except Exception:                          # noqa: BLE001
                pass
            self.video = None
        if self.play is not None:
            try:
                self.win.engine.set_speed(getattr(self, "_saved_speed", 1.0))
            except Exception:                          # noqa: BLE001
                pass
        self.state = "idle"

    # ── ввод ── #
    def _grab_input(self, on):
        S = self.S
        if on:
            self.setCursor(Qt.CursorShape.BlankCursor)
            if S.get("raw", True) and not self._raw_installed:
                try:
                    hwnd = int(self.window().winId())
                    if self.raw.register(hwnd):
                        QApplication.instance().installNativeEventFilter(self.raw)
                        self._raw_installed = True
                except Exception:                      # noqa: BLE001
                    pass
            self._confine(True)
            p = self.mapFromGlobal(QCursor.pos())
            self._mouse_last = QPointF(p)
        else:
            self.unsetCursor()
            if self._raw_installed:
                try:
                    QApplication.instance().removeNativeEventFilter(self.raw)
                except Exception:                      # noqa: BLE001
                    pass
                self.raw.unregister()
                self._raw_installed = False
            self._confine(False)

    def _confine(self, on):
        if on and self.S.get("confine", True) and self.isVisible():
            tl = self.mapToGlobal(QPoint(0, 0))
            dpr = self.devicePixelRatioF()
            _clip_cursor(QRect(int(tl.x() * dpr), int(tl.y() * dpr), int(self.width() * dpr),
                               int(self.height() * dpr)))
        else:
            _clip_cursor(None)

    def _relative(self) -> bool:
        if abs(float(self.S.get("sens", 1.0)) - 1.0) > 0.01:
            return True
        # raw input и на 1.00× — как в osu!: без ускорения и множителя скорости указателя Windows
        # (раньше на 1.00 курсор шёл за системным указателем, а с 1.05 — по raw, и скорость скакала).
        # Планшет (абсолютные координаты) остаётся абсолютным.
        return self._raw_installed and time.perf_counter() - getattr(self, "_raw_abs_seen", -1e9) > 2.0

    def _raw_move(self, dx, dy, absolute):
        if absolute:
            self._raw_abs_seen = time.perf_counter()
            return
        if self.watch or self.state not in ("play", "resume") or (getattr(self, "autopilot", False)
                                                                  and self.state == "play"):
            return
        if not self._relative():
            return
        s = float(self.S.get("sens", 1.0)) / max(1e-3, self.devicePixelRatioF()) / max(1e-3, self.s)
        self._move_cursor(dx * s, dy * s)
        self._raw_seen = time.perf_counter()

    def _move_cursor(self, dx, dy):
        """Сдвиг игрового курсора (osu px) в пределах окна — как в osu!, до самых краёв экрана."""
        x0, y0 = self._to_osu(0, 0)
        x1, y1 = self._to_osu(self.width() - 1, self.height() - 1)
        self.cx = max(x0, min(x1, self.cx + dx))
        self.cy = max(y0, min(y1, self.cy + dy))

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self.play is None or self.state == "idle":
            return
        if self.state in ("paused", "failed"):
            self._menu_hover(pos)
            return
        if self.watch or (getattr(self, "autopilot", False) and self.state == "play"):
            return
        if self._relative():
            raw = self._raw_installed and time.perf_counter() - getattr(self, "_raw_seen", 0) < 0.5
            if not raw:                                # иначе курсор уже ведёт raw input
                last = self._mouse_last or pos
                d = pos - last
                s = float(self.S.get("sens", 1.0)) / max(1e-3, self.s)
                self._move_cursor(d.x() * s, d.y() * s)
            # системный курсор держим в середине окна: при чувствительности < 1 он обгонял
            # игровой и уходил за край (клик мимо окна — пауза), а прежняя точка не устаревает
            m = 60
            if pos.x() < m or pos.y() < m or pos.x() > self.width() - m or pos.y() > self.height() - m:
                c = QPoint(self.width() // 2, self.height() // 2)
                QCursor.setPos(self.mapToGlobal(c))
                self._mouse_last = QPointF(c)
            else:
                self._mouse_last = pos
        else:
            self.cx, self.cy = self._to_osu(pos.x(), pos.y())

    def _press_key(self, k):
        if self.state == "resume":
            if (self.cx - self._resume_pos[0]) ** 2 + (self.cy - self._resume_pos[1]) ** 2 <= (self.play.r * 1.3) ** 2:
                self._resume_now()
            return
        if self.state != "play" or self.watch:
            return
        if self.keys.get(k):
            return
        self.keys[k] = True
        self.key_counts[k] += 1
        self.key_flash[k] = 1.0
        t = self._now_t()
        self._do_press(t, self.cx, self.cy)
        if self.rec is not None:
            self.rec["presses"].append((round(t, 2), round(self.cx, 1), round(self.cy, 1)))
            self._rec_frame(t, True)
        if self.S.get("ripples", True):
            self.ripples.append([self.cx, self.cy, time.perf_counter()])

    def _do_press(self, t, x, y):
        if "RX" in self.mods:
            return
        self.play.press(t, x, y)

    def _release_key(self, k):
        was = self.keys.get(k)
        self.keys[k] = False
        if was and self.state == "play" and not self.watch:
            self._rec_frame(self._now_t(), True)

    def event(self, e):
        # клавиши игры важнее горячих клавиш плеера (пробел, стрелки…)
        if e.type() == QEvent.Type.ShortcutOverride and e.key() != Qt.Key.Key_F11 \
                and not (e.modifiers() & Qt.KeyboardModifier.ControlModifier):
            e.accept()
            return True
        # Tab не переключает фокус — это тоже клавиша игры (по умолчанию скрывает интерфейс)
        if e.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease) and e.key() in (Qt.Key.Key_Tab,
                                                                                     Qt.Key.Key_Backtab):
            (self.keyPressEvent if e.type() == QEvent.Type.KeyPress else self.keyReleaseEvent)(e)
            return True
        return super().event(e)

    def keyPressEvent(self, e):
        if e.isAutoRepeat():
            return
        k = e.key()
        S = self.S
        # кнопки нот — первыми: их могут назначить на любую клавишу
        if key_is(e, bind_of(S, "k1")):
            self._press_key("K1")
            return
        if key_is(e, bind_of(S, "k2")):
            self._press_key("K2")
            return
        if k == Qt.Key.Key_Escape or key_is(e, bind_of(S, "key_pause")):
            if self.state == "play":
                self._pause()
            elif self.state in ("paused", "resume"):
                self._resume()
            elif self.state == "failed":
                self._quit()
            return
        if key_is(e, bind_of(S, "key_retry")) and self.state in ("play", "paused", "failed"):
            self.retry.emit()
            return
        if key_is(e, bind_of(S, "key_skip")) and self.skip_to is not None and self.t < self.skip_to \
                and self.state == "play":
            self._skip()
            return
        if self.state == "play":
            up = key_is(e, bind_of(S, "key_off_up")) or (k == Qt.Key.Key_Plus and bind_of(S, "key_off_up") == "Equal")
            if up or key_is(e, bind_of(S, "key_off_down")):
                d = 5 if up else -5
                self.offset += d
                self.host.local_offset_changed(self.map, d)
                self._toast(f"Смещение карты: {self.offset - float(S.get('offset', 0)):+.0f} мс")
                return
            if key_is(e, bind_of(S, "key_hud")):
                S["hud"] = not S.get("hud", True)
                self.host.save_settings()
                self._toast("Интерфейс игры: " + ("показан" if S["hud"] else "скрыт"))
                return
        if self.state in ("paused", "failed") and k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._menu_click(max(0, self.menu_hover))

    def keyReleaseEvent(self, e):
        if e.isAutoRepeat():
            return
        S = self.S
        if key_is(e, bind_of(S, "k1")):
            self._release_key("K1")
        elif key_is(e, bind_of(S, "k2")):
            self._release_key("K2")

    def mousePressEvent(self, e):
        if self.state in ("paused", "failed"):
            self._menu_hover(e.position())
            if self.menu_hover >= 0:
                self._menu_click(self.menu_hover)
            return
        if self.skip_to is not None and self.t < self.skip_to and self.state == "play":
            at = QPointF(*self._to_px(self.cx, self.cy)) if self._relative() else e.position()
            if self._skip_rect().contains(at):                 # с чувствительностью — там, где игровой курсор
                self._skip()
                return
        if not self.S.get("mouse_buttons", True) and self.state == "play":
            return
        if e.button() == Qt.MouseButton.LeftButton:
            self._press_key("M1")
        elif e.button() == Qt.MouseButton.RightButton:
            self._press_key("M2")

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._release_key("M1")
        elif e.button() == Qt.MouseButton.RightButton:
            self._release_key("M2")

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        for k in self.keys:
            self.keys[k] = False
        if self.state == "play" and not self.watch:
            self._pause()

    def wheelEvent(self, e):
        e.accept()

    # ── геометрия ── #
    def _prepare_layout(self):
        W, H = max(1, self.width()), max(1, self.height())
        sig = (W, H, self.play.r if self.play else 0)
        if sig == self._layout_sig:
            return
        self._layout_sig = sig
        ph = H * 0.8
        self.s = ph / 384
        pw = 512 * self.s
        self.ox = (W - pw) / 2
        self.oy = (H - ph) / 2 + H * 0.02
        dpr = self.devicePixelRatioF()
        if self.play:
            cols = None
            mc = (getattr(self, "map", None) or {}).get("colours")
            if mc and self.S.get("map_colours", True):
                cols = [QColor(*c) for c in mc]             # цвета комбо из карты osu!
            m_ = getattr(self, "map", None) or {}
            mfold = m_.get("folder") if (m_.get("osu") and self.S.get("map_skin", True)) else None
            self.skin = SK.make_skin(self.play.r * self.s, dpr, cols, ps=self.s, map_folder=mfold)
            for o in self.play.objs:
                o.pop("_px", None)
        self._body_cache = {}
        if SK.LOOK == "osu":                           # цифры как в osu!: тоньше, с лёгкой тенью
            sw, so = QFont.Weight.DemiBold, QColor(0, 0, 0, 120)
            self.digits = Digits(H * 0.062, dpr=dpr, weight=sw, outline=so)
            self.digits_small = Digits(H * 0.036, dpr=dpr, weight=sw, outline=so)
            self.digits_combo = Digits(H * 0.075, dpr=dpr, weight=sw, outline=so)
        else:
            self.digits = Digits(H * 0.055, dpr=dpr)
            self.digits_small = Digits(H * 0.032, dpr=dpr)
            self.digits_combo = Digits(H * 0.07, dpr=dpr)
        files = getattr(getattr(self, "skin", None), "f", None) if self.play else None
        if files is not None:                          # цифры счёта и комбо из скина osu! (и скина карты)
            try:
                import osu_skin_import
                osu_skin_import.patch_digits(self.digits, "score", files)
                osu_skin_import.patch_digits(self.digits_small, "score", files)
                osu_skin_import.patch_digits(self.digits_combo, "combo", files)
            except Exception as e:                     # noqa: BLE001
                print("[esu skin] digits:", e)
        self.bg_scaled = None
        self._hud_cache = {}
        if self.video is not None:
            try:
                self.video.set_target(int(W * dpr), int(H * dpr))
            except Exception:                          # noqa: BLE001
                pass

    def _to_px(self, x, y):
        return self.ox + x * self.s, self.oy + y * self.s

    def _to_osu(self, px, py):
        return (px - self.ox) / self.s, (py - self.oy) / self.s

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.play is not None:
            self._prepare_layout()
            if self.state in ("play", "resume"):
                self._confine(True)
        pp = getattr(self, "_pause_panels", None)
        if pp is not None and self.state == "paused":
            pp.show()

    # ── фон ── #
    def _load_background(self):
        self.bg_pm = None
        self.bg_scaled = None
        if self.video is not None:
            self.video.stop()
            self.video = None
        self._bg_levels = {}
        S = self.S
        mode = S.get("bg", "cover")
        m = getattr(self, "map", None) or {}
        vid, voff = None, 0.0
        if mode != "none" and m.get("video") and S.get("map_video", True) and os.path.exists(m["video"]):
            vid, voff = m["video"], -float(m.get("video_offset", 0)) / 1000.0     # видео из карты osu!
        elif mode == "clip":
            ent = self.host.clip_entry(self.track)
            if ent and (ent.get("path") or ent.get("stream")):
                import clip_fetch
                vid = clip_fetch.stream_source(ent)            # файл или живая ссылка на поток
                voff = float(ent.get("offset") or 0.0)
                if vid is None and ent.get("stream"):           # ссылка устарела — к следующему разу обновим
                    clip_fetch.refresh_stream_async(dict(self.track or {}), ent,
                                                    int(self.S().get("clips_quality", 720)
                                                        if hasattr(self, "S") else 720), lambda _u: None)
        if vid:
            try:
                from theme_layers import VideoSource
                dpr = self.devicePixelRatioF()
                self.video = VideoSource(vid, self, target=(int(self.width() * dpr), int(self.height() * dpr)))
                self._clip_off = voff
                self._vpaused = None
                self._vrate = 1.0
            except Exception as e:                     # noqa: BLE001
                print("[esu] video bg:", e)
                self.video = None
        if mode != "none":
            cov = m.get("bg") if m.get("bg") and os.path.exists(m.get("bg")) else self.host.cover_of(self.track)
            if cov:
                pm = load_pixmap(cov, 2560)
                if not pm.isNull():
                    self.bg_pm = pm

    def _bg_pix(self):
        if self.bg_pm is None:
            return None
        W, H = self.width(), self.height()
        if self.bg_scaled is None or self.bg_scaled.size() != self.size() * self.devicePixelRatioF():
            dpr = self.devicePixelRatioF()
            pm = self.bg_pm
            Wd, Hd = int(W * dpr), int(H * dpr)
            if self.S.get("blur", False):
                import blur_fx                         # гауссово размытие, без «квадратов»
                img = blur_fx.blurred_cover(pm, Wd, Hd, max(Wd, Hd) / 60.0)
            else:
                k = max(Wd / pm.width(), Hd / pm.height())
                sc = pm.toImage().scaled(int(pm.width() * k) + 2, int(pm.height() * k) + 2,
                                         Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
                img = sc.copy((sc.width() - Wd) // 2, (sc.height() - Hd) // 2, Wd, Hd)
            out = QPixmap.fromImage(img)
            out.setDevicePixelRatio(dpr)
            self.bg_scaled = out
            self._bg_levels = {}
        return self.bg_scaled

    def _bg_level(self, dim):
        """Фон с уже наложенным затемнением: кадр — одно копирование вместо копирования
        и полупрозрачной заливки всего экрана поверх (на 1080p это несколько мс каждый кадр)."""
        bg = self._bg_pix()
        if bg is None:
            return None
        q = round(max(0.0, min(1.0, dim)) * 40) / 40
        lv = self.__dict__.setdefault("_bg_levels", {})
        pm = lv.get(q)
        if pm is None:
            if len(lv) >= 3:
                lv.pop(next(iter(lv)))
            pm = QPixmap(bg.size())
            pm.setDevicePixelRatio(bg.devicePixelRatio())
            p = QPainter(pm)
            p.drawPixmap(0, 0, bg)
            p.fillRect(QRectF(0, 0, pm.width(), pm.height()), QColor(0, 0, 0, int(255 * q)))
            p.end()
            lv[q] = pm
        return pm

    def _sync_video(self):
        src = self.video
        if src is None or src.p is None:
            return
        want = self.t / 1000.0 + self._clip_off
        playing = self.state in ("play", "resume") and self.audio_on and want >= 0
        if self.state == "resume":
            playing = False
        try:
            length = (src.p.get_length() or 0) / 1000.0
            vt = (src.p.get_time() or 0) / 1000.0
        except Exception:                              # noqa: BLE001
            return
        if length > 0 and want >= length - 0.2:
            playing = False
        if playing != (self._vpaused is False):
            src.set_paused(not playing)
            self._vpaused = not playing
        r = self.play.rate if self.state != "failing" else max(0.5, getattr(self, "_fail_rate", 1.0))
        if abs(r - self._vrate) > 0.01:
            self._vrate = r
            src.set_rate(r)
        if length > 0 and abs(vt - want) > (0.3 if playing else 0.8) and want >= 0:
            try:
                src.p.set_time(int(min(want, length - 0.3) * 1000))
            except Exception:                          # noqa: BLE001
                pass

    # ── время ── #
    def _audio_ms(self):
        try:
            return float(self.win.engine.get_position_smooth()) * 1000.0 - self.offset
        except Exception:                              # noqa: BLE001
            return self.t

    def _now_t(self):
        """Время нажатия: время кадра + сколько прошло с его начала."""
        if self.state != "play":
            return self.t
        return self.t + (time.perf_counter() - self._last) * 1000.0 * self.play.rate

    def _advance_clock(self, dt):
        rate = self.play.rate
        adv = dt * 1000.0 * rate
        if not self.audio_on:
            self.t += adv
            if self.t >= 0:
                self._start_audio()
            return
        a = self._audio_ms()
        if not self._audio_moving:
            if self._audio_p0 is None:
                self._audio_p0 = a
            elif abs(a - self._audio_p0) > 1.0:
                self._audio_moving = True
            self.t += adv
            if self._audio_moving:
                return
            return
        err = a - (self.t + adv)
        if err < -260 and not self._audio_alive(a):
            # трек доиграл раньше конца карты (или звук встал): часы идут сами — раньше время
            # откатывалось к концу звука, и последние доли карты крутились по кругу без конца
            self.t += adv
            return
        if abs(err) > 1500:
            # звук перемотал кто-то чужой (превью, медиаклавиши, клик по тексту песни) — карта главнее:
            # возвращаем звук на время игры, а не прыгаем по карте и не теряем все ноты разом
            now = time.perf_counter()
            if now - getattr(self, "_reseek_t", 0.0) > 1.0:
                self._reseek_t = now
                try:
                    self.win.engine.seek(max(0.0, (self.t + adv + self.offset) / 1000.0))
                except Exception:                      # noqa: BLE001
                    pass
                self._audio_moving = False
                self._audio_p0 = None
            self.t += adv
        elif err > 260:
            self.t = a
        elif err < -260:
            # звук отстал (после перемотки поток стартует чуть позже): карта притормаживает, пока он
            # не догонит, — назад время игры не прыгает, ноты не мигают повторно
            self.t += adv * 0.25
        else:
            self.t += adv + max(-adv * 0.6, min(40.0, err * 0.12))

    def _audio_alive(self, a) -> bool:
        """Звук идёт: плеер играет и позиция меняется (после конца трека она замирает)."""
        now = time.perf_counter()
        if a != getattr(self, "_alive_a", None):
            self._alive_a = a
            self._alive_t = now
        try:
            eng = self.win.engine
            if not eng.is_playing() or eng.has_finished():
                return False
        except Exception:                              # noqa: BLE001
            return True
        return now - getattr(self, "_alive_t", now) < 0.15

    # ── кадр ── #
    def _tick(self):
        now = time.perf_counter()
        dt = min(0.1, now - self._last)
        self._last = now
        if self.play is None:
            return
        if self.width() != (self._layout_sig or (0,))[0]:
            self._prepare_layout()
        st = self.state
        if st == "play" and self.audio_on and self._audio_moving:
            try:
                ext_pause = self.win.engine.is_paused()          # пауза из трея / медиаклавишей
            except Exception:                                  # noqa: BLE001
                ext_pause = False
            if ext_pause:
                self._pause()
                st = self.state
        if st == "play":
            prev_t = self.t
            self._advance_clock(dt)
            self._step(prev_t)
        elif st == "failing":
            self._fail_step(now)
        if self.video is not None:
            self._sync_video()
        self._decay(dt, now)
        self.update()

    def _step(self, prev_t):
        P = self.play
        t = self.t
        held = any(self.keys.values())
        if self.auto is not None and getattr(self, "autopilot", False):
            x, y, _h, _p = self.auto.update(t)           # Autopilot: курсор ведёт игра, нажимает игрок
            self.cx, self.cy = x, y
        elif self.auto is not None:
            x, y, held, presses = self.auto.update(t)
            self.cx, self.cy = x, y
            for o in presses:
                k = "K1" if self.key_counts["K1"] <= self.key_counts["K2"] else "K2"
                self.key_counts[k] += 1
                self.key_flash[k] = 1.0
                P.press(o["t"], float(o["x"]), float(o["y"]))
        elif self.replay is not None:
            held = self._replay_step(t)
        elif "RX" in self.mods:
            held = True
            for k in range(P.hq, min(len(P.heads), P.hq + 3)):
                o = P.objs[P.heads[k]]
                if o["hj"] is None and abs(t - o["t"]) <= P.w300 * 0.5 and \
                        (self.cx - o["x"]) ** 2 + (self.cy - o["y"]) ** 2 <= P.r ** 2:
                    P.press(t, self.cx, self.cy)
        P.update(t, max(0.0, t - prev_t), self.cx, self.cy, held)
        self._rec_frame(t)
        self._consume_events()
        self._kiai_step(t, prev_t)
        self._breaks_step(t, prev_t)
        self._countdown_step(t, prev_t)
        if self.trail is not None and self.S.get("cursor_trail", True):
            px, py = self._to_px(self.cx, self.cy)
            self.trail.append((px, py, time.perf_counter()))
            if len(self.trail) > 90:
                del self.trail[:len(self.trail) - 90]
        if P.failed and self.state == "play":
            self._start_fail()
            return
        if P.done and self._end_t is None:
            self._end_t = t
        if self._end_t is not None and t - self._end_t > 1200 and self.state == "play":
            self._finish()

    def _rec_frame(self, t, force=False):
        """Кадр повтора; force — сразу при нажатии/отпускании, чтобы удержание слайдеров
        в повторе совпадало с игрой до миллисекунды."""
        if self.rec is None:
            return
        fr = self.rec["frames"]
        if not force and fr and t - fr[-1][0] < 8:
            return
        if fr and t < fr[-1][0]:
            t = fr[-1][0]
        m = sum(1 << i for i, k in enumerate(("K1", "K2", "M1", "M2")) if self.keys[k])
        fr.append((round(t, 1), round(self.cx, 1), round(self.cy, 1), m))

    def _replay_step(self, t):
        rp = self.replay
        fr = rp["frames"]
        while self._rp_f + 1 < len(fr) and fr[self._rp_f + 1][0] <= t:
            self._rp_f += 1
        if fr:
            a = fr[self._rp_f]
            b = fr[min(len(fr) - 1, self._rp_f + 1)]
            k = 0 if b[0] <= a[0] else max(0.0, min(1.0, (t - a[0]) / (b[0] - a[0])))
            self.cx, self.cy = a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k
            mask = a[3]
            for i, kk in enumerate(("K1", "K2", "M1", "M2")):
                self.keys[kk] = bool(mask & (1 << i))
        pr = rp["presses"]
        while self._rp_i < len(pr) and pr[self._rp_i][0] <= t:
            pt, px, py = pr[self._rp_i]
            self._rp_i += 1
            k = "K1" if self.key_counts["K1"] <= self.key_counts["K2"] else "K2"
            self.key_counts[k] += 1
            self.key_flash[k] = 1.0
            self.play.press(pt, px, py)
        return any(self.keys.values())

    # ── события правил → эффекты и звуки ── #
    _SETS = {1: "normal", 2: "soft", 3: "drum"}
    _ADD_KEYS = {"hitwhistle": "whistle", "hitfinish": "finish", "hitclap": "clap", "slidertick": "tick"}

    def _sample_key(self, set_name, kind, idx) -> str | None:
        """Какой звук играть: свой сэмпл карты osu! → звук набора (soft/drum) → общий."""
        bank = self.host.sfx.bank
        if self._map_samples:
            k = f"map:{set_name}-{kind}{max(1, int(idx))}"
            if k in bank:
                return k
        if kind == "hitnormal":
            return set_name if set_name in bank else "normal"
        gen = self._ADD_KEYS.get(kind, kind)
        own = (self.win.settings or {}).get("osu_hitsounds") or {}
        if gen not in own:                           # свой звук пользователя важнее звука набора
            k = f"{set_name}-{gen}"
            if k in bank:
                return k
        return gen

    def _hitsound(self, o, x, edge=None):
        hs, ns, add = o.get("hs", 0), o.get("ss", 1), o.get("as", 0)
        idx, vol = o.get("si", 1), o.get("vol")
        if edge is not None and o.get("edges") and edge < len(o["edges"]):
            hs, ns, add, idx, vol = o["edges"][edge]
        v = 1.0 if vol is None else max(0.08, min(1.0, float(vol) / 100.0))
        base = self._SETS.get(ns, "normal")
        addn = self._SETS.get(add or ns, base)
        if o.get("file") and self._map_samples and f"map:file:{o['file'].lower()}" in self.host.sfx.bank:
            self.sfx(f"map:file:{o['file'].lower()}", v, x)
        else:
            self.sfx(self._sample_key(base, "hitnormal", idx), v, x)
            if base == "drum" and not o.get("edges") and vol is None:
                self.sfx("normal", 0.5, x)            # сгенерированные карты: барабан с щелчком
        for bit, kind in ((2, "hitwhistle"), (4, "hitfinish"), (8, "hitclap")):
            if hs & bit:
                self.sfx(self._sample_key(addn, kind, idx), 0.85 * v, x)

    def _tick_sound(self, o, x):
        base = self._SETS.get(o.get("ss", 1), "normal")
        vol = o.get("tick_vol", o.get("vol"))
        v = 0.8 if vol is None else max(0.08, min(1.0, float(vol) / 100.0)) * 0.8
        self.sfx(self._sample_key(base, "slidertick", o.get("si", 1)), v, x)

    def _load_map_samples(self, folder):
        """Хитсаунды из папки карты osu! (normal-hitclap2.wav и т. п.) — в фоне, пока идёт отсчёт."""
        import threading
        try:
            import osu_beatmap as OB
            files = OB.sample_files(folder)
        except Exception:                              # noqa: BLE001
            return
        sfx = self.host.sfx
        sfx.unregister_prefix("map:")
        extra = {}
        for o in self.map.get("objects", []) if hasattr(self, "map") else []:
            f = o.get("file")
            if f:
                p = os.path.join(folder, f)
                if os.path.exists(p):
                    extra[f"file:{f.lower()}"] = p
        files.update(extra)
        if not files:
            return
        self._map_samples = {k: False for k in files}

        def work(fs=dict(files), gen=id(self.play)):
            import osu_beatmap as OB
            for k, p in fs.items():
                if id(self.play) != gen:
                    return                             # уже другая карта
                a = OB.decode_sample(p)
                if a is not None and len(a):
                    sfx.register("map:" + k, a)
                    self._map_samples[k] = True
        threading.Thread(target=work, daemon=True, name="esu-samples").start()

    def _consume_events(self):
        P = self.play
        S = self.S
        now = time.perf_counter()
        for e in P.events:
            kind = e[0]
            if kind == "hit":
                o = e[1]
                self._hitsound(o, o["x"])
                self._burst(o, o["x"], o["y"])
            elif kind == "head":
                o = e[1]
                self._hitsound(o, o["x"], 0)
                o["_head_hit_rt"] = now
                self._burst(o, o["x"], o["y"], small=True)
            elif kind == "part":
                o, pk, pt, px, py = e[1:]
                if pk == "tick":
                    self._tick_sound(o, px)
                elif pk == "rep":
                    o["_rep_n"] = o.get("_rep_n", 0) + 1
                    self._hitsound(o, px, o["_rep_n"])
                    self._burst(o, px, py, small=True)
                else:
                    if not o.get("edges"):
                        self.sfx("slider_end", 0.9, px)
                    self._hitsound(o, px, int(o.get("slides", 1)))
                    self._burst(o, px, py, small=True)
                self.fx.append({"k": "tickpop", "x": px, "y": py, "t0": now, "life": 0.25})
            elif kind == "partmiss":
                if e[2] == "rep":                       # пропущенный повтор — следующий край со своим звуком
                    e[1]["_rep_n"] = e[1].get("_rep_n", 0) + 1
            elif kind == "judge":
                o, j, t, x, y = e[1:]
                gk = o.get("_gk") if j else None
                if j == 0 or j != 300 or gk or S.get("show300", True):
                    self.pops.append({"j": j, "x": x, "y": y, "t0": now, "gk": gk})
                if j and o["k"] == 1 and self.S.get("hit_light", True):
                    self.fx.append({"k": "light", "x": x, "y": y, "t0": now, "life": 0.5, "ci": o["ci"]})
                if j == 0:
                    o["_miss_rt"] = now
                    self.hp_flash = -1.0
                else:
                    self.hp_flash = 1.0
                    self.combo_pop = 1.0
                    if self.S.get("combo_burst", True):
                        self.combo_ghost.append(now)
                if o["k"] == 2 and j:
                    self.pops.append({"txt": "CLEAR!" if j == 300 else "", "x": 256, "y": 150, "t0": now})
            elif kind == "headmiss":
                self.pops.append({"j": 0, "x": e[1]["x"], "y": e[1]["y"], "t0": now, "small": True})
            elif kind == "combobreak":
                self.sfx("combobreak", 0.9)
            elif kind == "shake":
                e[1]["_shake_rt"] = now
            elif kind == "spin":
                self.sfx("spin", 0.5)
            elif kind == "bonus":
                self.sfx("bonus", 0.8)
                self.pops.append({"txt": f"+{1000}", "x": 256, "y": 270, "t0": now, "bonus": True})
        P.events.clear()
        if P.errors and (not self.errors_vis or self.errors_vis[-1][0] != P.errors[-1][0]):
            for t, d in P.errors[len(P.errors) - min(4, len(P.errors)):]:
                if not self.errors_vis or t > self.errors_vis[-1][0]:
                    self.errors_vis.append((t, d, now))
            self.errors_vis = self.errors_vis[-60:]

    def _burst(self, o, x, y, small=False):
        now = time.perf_counter()
        if not small:
            o["_hit_rt"] = now
        if self.S.get("hit_light", True):
            self.fx.append({"k": "light", "x": x, "y": y, "t0": now, "life": 0.55 if not small else 0.35, "ci": o["ci"]})
        if self.S.get("particles", True):
            n = 6 if small else 12
            for i in range(n):
                a = random.uniform(0, 2 * math.pi)
                sp = random.uniform(60, 220) * (0.6 if small else 1.0)
                self.fx.append({"k": "spark", "x": x, "y": y, "vx": math.cos(a) * sp, "vy": math.sin(a) * sp,
                                "t0": now, "life": random.uniform(0.35, 0.7), "ci": o["ci"],
                                "sc": random.uniform(0.5, 1.1)})

    # ── kiai, перерывы, отсчёт ── #
    def _kiai_step(self, t, prev_t):
        m = self.map
        kiai = any(a <= t < b for a, b in m.get("kiai", []))
        if kiai and not self.kiai and self.S.get("kiai_fx", True) and t - self._fountain_t > 4000:
            self._fountain_t = t
            self._fountain()
        self.kiai = kiai
        beats = m.get("beats") or []
        i = bisect.bisect_right(beats, t) - 1
        if i != self._beat_i:
            self._beat_i = i
            if kiai and self.S.get("kiai_fx", True):
                self.flash = 1.0

    def _fountain(self):
        now = time.perf_counter()
        W, H = self.width(), self.height()
        for side in (0, 1):
            for i in range(26):
                x = 0 if side == 0 else W
                ang = math.radians(random.uniform(55, 80))
                sp = random.uniform(H * 0.75, H * 1.25)
                vx = math.cos(ang) * sp * (1 if side == 0 else -1)
                self.fx.append({"k": "star", "px": x, "py": H + 10, "vx": vx, "vy": -math.sin(ang) * sp,
                                "t0": now + i * 0.018, "life": random.uniform(1.0, 1.6), "rot": random.uniform(0, 360),
                                "vr": random.uniform(-200, 200), "sc": random.uniform(0.6, 1.3)})

    def _breaks_step(self, t, prev_t):
        for i, (a, b) in enumerate(self.play.breaks):
            mid = (a + b) / 2
            if prev_t < mid <= t and i not in self._sections_done:
                self._sections_done.add(i)
                ok = self.play.hp >= 0.5
                self.sfx("pass" if ok else "fail_section", 0.9)
                self.pops.append({"section": ok, "t0": time.perf_counter()})

    def _countdown_step(self, t, prev_t):
        if not getattr(self, "_countdown_on", True):
            return
        beat = 60000.0 / max(1.0, self.map.get("bpm", 120))
        first = self.play.first_t
        for k, name in ((4, "count3"), (3, "count2"), (2, "count1"), (1, "go")):
            tt = first - k * beat
            if prev_t < tt <= t and k not in self._count_done and tt > self.t - 2000:
                self._count_done.add(k)
                self.sfx(name, 0.8)
                self.pops.append({"count": {4: "3", 3: "2", 2: "1", 1: "GO!"}[k], "t0": time.perf_counter()})

    def _skip(self):
        tgt = self.skip_to
        self.skip_to = None
        self.t = tgt
        self.sfx("menuhit", 0.7)
        if not self.audio_on:
            self._start_audio()
        else:
            try:
                self.win.engine.seek((tgt + self.offset) / 1000.0)
            except Exception:                          # noqa: BLE001
                pass
            self._audio_moving = False
            self._audio_p0 = None

    def _skip_rect(self) -> QRectF:
        W, H = self.width(), self.height()
        sfr = self._skin_skip_frames()
        if sfr:
            d = sfr[0].devicePixelRatio()
            w, h = sfr[0].width() / d, sfr[0].height() / d
            return QRectF(W - w, H - h, w, h)
        return QRectF(W - 250, H - 100, 220, 70)

    def _skin_skip_frames(self):
        if getattr(self.skin, "f", None) is None:
            return []
        try:
            import osu_skin_plus
            if SK.ACTIVE is None or not (SK.ACTIVE.has("play-skip") or SK.ACTIVE.has("play-skip-0")):
                return []
            return osu_skin_plus._frames(self, "play-skip", 1.0)
        except Exception:                              # noqa: BLE001
            return []

    # ── пауза / провал / конец ── #
    def _pause(self):
        if self.state != "play":
            return
        self.state = "paused"
        self.paused_t = self.t
        self.menu_hover = 0
        try:
            self.win.engine.pause_hard()
        except Exception:                              # noqa: BLE001
            pass
        self._grab_input(False)
        self.sfx("menuback", 0.6)
        self._panels(True)

    def _panels(self, on):
        """Эквалайзер, звук и фон по бокам меню паузы (osu_pause)."""
        pp = getattr(self, "_pause_panels", None)
        if on:
            if pp is None:
                try:
                    from osu_pause import PausePanels
                    pp = self._pause_panels = PausePanels(self)
                except Exception as e:                 # noqa: BLE001
                    print("[osu] pause panels:", e)
                    return
            pp.show()
        elif pp is not None and pp.visible():
            pp.hide()
            self.setFocus()

    def _resume(self):
        if self.state == "resume":
            self.state = "paused"
            self._panels(True)
            return
        self._panels(False)
        if self.watch:
            self._resume_now()
            return
        self.state = "resume"
        self._resume_pos = (self.cx, self.cy)
        self._grab_input(True)

    def _resume_now(self):
        self._panels(False)
        self.state = "play"
        self._last = time.perf_counter()
        try:
            if self.audio_on:
                self.win.engine.seek((self.t + self.offset) / 1000.0)
                self.win.engine.play()
        except Exception:                              # noqa: BLE001
            pass
        self._audio_moving = False
        self._audio_p0 = None
        self._grab_input(True)

    def _menu_items(self):
        if self.state == "failed":
            return [("Заново", QColor(255, 200, 40)), ("Выйти в меню", QColor(240, 80, 90))]
        return [("Продолжить", QColor(80, 210, 90)), ("Заново", QColor(255, 200, 40)),
                ("Выйти в меню", QColor(240, 80, 90))]

    def _menu_rects(self):
        W, H = self.width(), self.height()
        items = self._menu_items()
        if SK.LOOK == "osu":                           # как в osu!: три широкие кнопки столбиком
            w, h = min(W * 0.3, H * 0.5), H * 0.115
            gap = H * 0.115
            top = H * 0.52 - (len(items) * h + (len(items) - 1) * gap) / 2
            return [QRectF(W / 2 - w / 2, top + i * (h + gap), w, h) for i in range(len(items))]
        h = min(84, H * 0.09)
        gap = h * 0.35
        top = H / 2 - (len(items) * h + (len(items) - 1) * gap) / 2 + H * 0.06
        return [QRectF(W / 2 - W * 0.22, top + i * (h + gap), W * 0.44, h) for i in range(len(items))]

    def _menu_hover(self, pos):
        old = self.menu_hover
        self.menu_hover = next((i for i, r in enumerate(self._menu_rects()) if r.contains(pos)), -1)
        if self.menu_hover != old and self.menu_hover >= 0:
            self.sfx("hover", 0.6)
        self.update()

    def _menu_click(self, i):
        items = [n for n, _ in self._menu_items()]
        if not 0 <= i < len(items):
            return
        self.sfx("menuhit", 0.8)
        name = items[i]
        if name == "Продолжить":
            self._resume()
        elif name == "Заново":
            self.retry.emit()
        else:
            self._quit()

    def _quit(self):
        self.stop()
        self.quit.emit()

    def _start_fail(self):
        try:
            self.host.attempt_outcome("fail")          # счётчик попыток: эта — провал
        except Exception:                              # noqa: BLE001
            pass
        self.state = "failing"
        self._fail_t = time.perf_counter()
        self._fail_rate = self.play.rate
        self.sfx("fail", 1.0)
        self._grab_input(False)
        # падают только ноты, которые сейчас на экране: время ещё идёт (замедляясь), но новые уже не появляются
        self._fail_hi = None
        lo, hi = self._vis_range()
        self._fail_hi = hi
        for o in self.play.objs[lo:hi]:
            o["_fall"] = (random.uniform(-80, 80), random.uniform(-120, 0), random.uniform(-90, 90))
        if not self.S.get("fail_fx", True):
            self._fail_done()

    def _fail_step(self, now):
        e = now - self._fail_t
        k = min(1.0, e / 2.0)
        self.t += (now - getattr(self, "_fail_last", now)) * 1000 * self._fail_rate * (1 - k)
        self._fail_last = now
        try:
            self.win.engine.set_speed(max(0.5, self.play.rate * (1 - 0.55 * k)))
        except Exception:                              # noqa: BLE001
            pass
        self._fail_rate = max(0.5, self.play.rate * (1 - 0.55 * k))
        if e >= 2.0:
            self._fail_done()

    def _fail_done(self):
        try:
            self.win.engine.pause_hard()
            self.win.engine.set_speed(getattr(self, "_saved_speed", 1.0))
        except Exception:                              # noqa: BLE001
            pass
        self.state = "failed"
        self.menu_hover = 0
        self.unsetCursor()

    def _finish(self):
        P = self.play
        res = P.result()
        res["replay"] = self.rec if self.rec is not None else self.replay
        res["watched"] = self.watch
        res["auto"] = "AU" in self.mods
        self.stop()
        self.finished.emit(res)

    def _toast(self, text):
        self.pops.append({"toast": text, "t0": time.perf_counter()})

    # ── анимации ── #
    def _decay(self, dt, now):
        self.flash *= math.exp(-dt * 7)
        if self.play is not None:
            want = 1.0 if self.play.in_break(self.t) else 0.0
            self._brk_mix = getattr(self, "_brk_mix", 0.0) + (want - getattr(self, "_brk_mix", 0.0)) * min(1.0, dt * 4)
        self.combo_pop *= math.exp(-dt * 12)
        for k in self.key_flash:
            self.key_flash[k] *= math.exp(-dt * 6)
        if self.play is not None:
            self.hp_disp += (self.play.hp - self.hp_disp) * min(1.0, dt * 10)
            self.score_disp += (self.play.score - self.score_disp) * min(1.0, dt * 9)
            if abs(self.play.score - self.score_disp) < 1:
                self.score_disp = self.play.score
        self.hp_flash *= math.exp(-dt * 5)
        self.fx = [f for f in self.fx if now - f["t0"] < f["life"]]
        self.pops = [p_ for p_ in self.pops if now - p_["t0"] < (2.2 if "toast" in p_ or "section" in p_ else 1.0)]
        self.ripples = [r for r in self.ripples if now - r[2] < 0.45]
        self.combo_ghost = [g for g in self.combo_ghost if now - g < 0.4]
        self.trail = [tr for tr in self.trail if now - tr[2] < 0.16]

    # ------------------------------------------------------------------ #
    #  Отрисовка                                                          #
    # ------------------------------------------------------------------ #

    def paintEvent(self, _e):
        if self.play is None:
            p = QPainter(self)
            p.fillRect(self.rect(), QColor(0, 0, 0))
            p.end()
            return
        p = frame_painter(self)
        try:
            self._paint_frame(p)
        finally:
            frame_present(self, p)

    def _paint_frame(self, p):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        W, H = self.width(), self.height()
        self._draw_bg(p, W, H)
        now = time.perf_counter()
        cinema = getattr(self, "cinema", False) and self.state in ("play", "resume")
        if not cinema:                                   # Cinema: только фон/видео и музыка
            self._draw_followpoints(p)
            self._draw_objects(p, now)
            self._draw_fx(p, now, under=False)
            self._draw_pops(p, now)
        if "FL" in self.mods and self.state in ("play", "resume", "paused"):
            self._draw_flashlight(p, W, H)
        if (self.S.get("hud", True) or self.state != "play") and not cinema:
            if self._board:
                self._draw_board(p, W, H)
            self._draw_hud(p, W, H, now)
        if not cinema:
            self._draw_cursor(p, now)
        if self.state == "paused":
            self._draw_menu(p, W, H, "Пауза")
        elif self.state == "resume":
            self._draw_resume(p)
        elif self.state == "failing":
            k = min(1.0, (now - self._fail_t) / 2.0)
            p.setOpacity(1.0)
            p.fillRect(self.rect(), QColor(120, 0, 0, int(110 * k)))
        elif self.state == "failed":
            p.fillRect(self.rect(), QColor(80, 0, 0, 110))
            self._draw_menu(p, W, H, "Провал")

    def _draw_bg(self, p, W, H):
        S = self.S
        base = float(S.get("dim", 0.7))
        mix = getattr(self, "_brk_mix", 0.0)                  # перерыв: фон плавно светлеет, как в osu!
        if self.video is not None and self.video.valid():
            img = self.video.img
            dpr = self.devicePixelRatioF()
            iwd, ihd = img.width(), img.height()
            if iwd >= W * dpr - 1 and ihd >= H * dpr - 1 and max(W * dpr / iwd, H * dpr / ihd) > 0.985:
                # VLC уже отдаёт кадр «под обрез» окна: копируем 1:1 по центру, без масштабирования
                # (масштаб на весь экран — самая дорогая операция кадра)
                img.setDevicePixelRatio(dpr)
                p.drawImage(QPointF(round((W * dpr - iwd) / 2) / dpr, round((H * dpr - ihd) / 2) / dpr), img)
            else:
                iw, ih = img.width() / dpr, img.height() / dpr
                k = max(W / iw, H / ih)
                p.drawImage(QRectF((W - iw * k) / 2, (H - ih * k) / 2, iw * k, ih * k), img)
            dim = base * (1 - 0.45 * mix)
            if dim > 0.003:
                p.fillRect(self.rect(), QColor(0, 0, 0, int(255 * dim)))
        else:
            lv0 = self._bg_level(base) if self.bg_pm is not None else None
            if lv0 is not None:
                if mix < 0.01:
                    p.drawPixmap(0, 0, lv0)
                else:
                    lv1 = self._bg_level(base * 0.55)
                    if mix > 0.99:
                        p.drawPixmap(0, 0, lv1)
                    else:
                        p.drawPixmap(0, 0, lv0)
                        p.setOpacity(mix)
                        p.drawPixmap(0, 0, lv1)
                        p.setOpacity(1.0)
            else:
                g = QLinearGradient(0, 0, 0, H)
                g.setColorAt(0, QColor(40, 20, 50))
                g.setColorAt(1, QColor(10, 8, 20))
                p.fillRect(self.rect(), QBrush(g))
                p.fillRect(self.rect(), QColor(0, 0, 0, int(255 * base * (1 - 0.45 * mix))))
        # kiai: вспышка на каждую долю (раньше фон ещё и масштабировался — это перерисовка всего экрана)
        if self.flash > 0.01:
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            p.fillRect(self.rect(), QColor(255, 255, 255, int((34 if S.get("bg_pulse", True) else 22) * self.flash)))
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

    def _vis_range(self):
        P = self.play
        objs = P.objs
        t = self.t
        lo = P.lo
        while lo > 0 and objs[lo - 1].get("end", objs[lo - 1]["t"]) > t - 1200:
            lo -= 1
        hi = lo
        while hi < len(objs) and objs[hi]["t"] - P.pre <= t:
            hi += 1
        cap = getattr(self, "_fail_hi", None)
        if cap is not None and self.state in ("failing", "failed"):
            hi = min(hi, cap)
        return lo, hi

    def _alpha_in(self, o, t):
        P = self.play
        start = o["t"] - P.pre
        if "HD" in self.mods:
            fi = P.pre * 0.4
            a = min(1.0, max(0.0, (t - start) / fi))
            fo_start = start + fi
            fo = P.pre * 0.3
            if t > fo_start:
                a = max(0.0, 1 - (t - fo_start) / fo)
            return a
        return min(1.0, max(0.0, (t - start) / max(1.0, P.fin)))

    def _fall(self, o, x, y):
        if self.state not in ("failing", "failed") or "_fall" not in o:
            return x, y, 0.0
        e = min(2.0, time.perf_counter() - self._fail_t)
        vx, vy, vr = o["_fall"]
        return x + vx * e, y + vy * e + 0.5 * 900 * e * e, vr * e

    def _draw_objects(self, p, now):
        P = self.play
        sk = self.skin
        t = self.t
        lo, hi = self._vis_range()
        # спиннеры поверх затемнения — отдельно
        for k in range(hi - 1, lo - 1, -1):
            o = P.objs[k]
            if o["k"] == 0:
                self._draw_circle(p, o, t, now)
            elif o["k"] == 1:
                self._draw_slider(p, o, t, now)
            else:
                self._draw_spinner(p, o, t, now)
        p.setOpacity(1.0)
        _ = sk

    def _draw_circle_head(self, p, o, x, y, a, t, now, number=True, approach=True):
        sk = self.skin
        P = self.play
        sh = 0.0
        if "_shake_rt" in o and now - o["_shake_rt"] < 0.2:
            sh = math.sin((now - o["_shake_rt"]) * 80) * 6 * (1 - (now - o["_shake_rt"]) / 0.2)
        px, py = self._to_px(x, y)
        px += sh
        px, py, rot = self._fall(o, px, py)
        blit(p, sk.circle(o["ci"]), px, py, 1.0, a, rot)
        if self.kiai and self.flash > 0.02 and self.S.get("kiai_fx", True):
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            blit(p, sk.white_circle(), px, py, 0.9, a * self.flash * 0.22)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        if number:
            blit(p, sk.number(o["num"], o["ci"]), px, py, 1.0, a, rot)
        if approach and "HD" not in self.mods and t < o["t"]:
            f = (o["t"] - t) / P.pre
            sc = 1.0 + 3.0 * max(0.0, f)
            aa = min(0.9, a * 1.6)
            blit(p, sk.approach(o["ci"]), px, py, sc, aa)

    def _draw_explode(self, p, o, x, y, rt, now, ci):
        e = now - rt
        if e > 0.3:
            return
        k = e / 0.3
        px, py = self._to_px(x, y)
        full = getattr(self.skin, "circle_full", self.skin.circle)     # скин osu!: вместе с оверлеем
        blit(p, full(ci), px, py, 1.0 + 0.45 * _ease_out(k), (1 - k) ** 1.5)
        if e < 0.12:
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            blit(p, self.skin.white_circle(), px, py, 1.0 + 0.3 * k, (1 - e / 0.12) * 0.55)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

    def _draw_circle(self, p, o, t, now):
        if o["j"] is not None:
            if o["j"] > 0 and "_hit_rt" in o:
                self._draw_explode(p, o, o["x"], o["y"], o["_hit_rt"], now, o["ci"])
            elif o["j"] == 0:
                e = now - o.get("_miss_rt", now)
                if e < 0.15:
                    self._draw_circle_head(p, o, o["x"], o["y"], 1 - e / 0.15, t, now, approach=False)
            return
        a = self._alpha_in(o, t)
        if self.state in ("failing", "failed"):
            a *= max(0.0, 1 - (now - self._fail_t) / 2.0)
        self._draw_circle_head(p, o, o["x"], o["y"], a, t, now)

    def _slider_px(self, o):
        px = o.get("_px")
        if px is None:
            path = o["_path"]
            px = np.empty_like(path)
            px[:, 0] = self.ox + path[:, 0] * self.s
            px[:, 1] = self.oy + path[:, 1] * self.s
            o["_px"] = px
        return px

    def _body(self, o, a0, a1, cheap):
        """Тело слайдера от доли a0 до a1 пути (кэш по ступенькам)."""
        q = 40
        key = (id(o), int(a0 * q), int(math.ceil(a1 * q)))
        hit = self._body_cache.pop(key, None)
        if hit is not None:
            self._body_cache[key] = hit                # LRU: видимые тела остаются, ступеньки змейки уходят
            return hit
        px = self._slider_px(o)
        n = len(px)
        i0, i1 = int(a0 * (n - 1)), max(int(a0 * (n - 1)) + 1, int(math.ceil(a1 * (n - 1))))
        seg = px[i0:i1 + 1]
        if len(seg) < 2:
            seg = px[max(0, i1 - 1):i1 + 1]
        res = self.skin.slider_body(seg, o["ci"], cheap=cheap)
        if len(self._body_cache) >= 28:                # картинки тел крупные (до 1–2 МБ) — держим немного
            for kk in list(self._body_cache)[:8]:
                del self._body_cache[kk]
        self._body_cache[key] = res
        return res

    def _draw_slider(self, p, o, t, now):
        P = self.play
        sk = self.skin
        end = o["end"]
        done = o["j"] is not None
        if done:
            e = now - o.get("_done_rt", now)
            if "_done_rt" not in o:
                o["_done_rt"] = now
                e = 0.0
            if e > 0.25:
                return
            fade = 1 - e / 0.25
        else:
            fade = 1.0
        a = self._alpha_in(o, t) if t < o["t"] else 1.0
        if "HD" in self.mods and t >= o["t"]:
            a = max(0.0, 1 - (t - o["t"]) / max(1.0, end - o["t"]))
        a *= fade
        if self.state in ("failing", "failed"):
            a *= max(0.0, 1 - (now - self._fail_t) / 2.0)
        snaking = self.S.get("snaking", True)
        a0, a1 = 0.0, 1.0
        if snaking and t < o["t"] - P.pre * 0.66:
            a1 = max(0.02, (t - (o["t"] - P.pre)) / (P.pre * 0.34))
        if snaking and t >= o["t"] and o["slides"] >= 1:
            last_start = o["t"] + o["span"] * (o["slides"] - 1)
            if t > last_start:
                f = min(1.0, (t - last_start) / o["span"])
                if o["slides"] % 2 == 1:
                    a0 = f
                else:
                    a1 = 1 - f
        if a1 - a0 > 0.01:
            pm, org = self._body(o, a0, a1, cheap=(a0 > 0 or a1 < 1))
            ox, oy, rot = self._fall(o, org.x(), org.y())
            p.setOpacity(min(1.0, a * 0.95))
            if rot:
                blit(p, pm, ox + pm.width() / pm.devicePixelRatio() / 2, oy + pm.height() / pm.devicePixelRatio() / 2,
                     1.0, a * 0.95, rot)
            else:
                p.drawPixmap(QPointF(ox, oy), pm)
        px = self._slider_px(o)
        # тики
        for tt in o["ticks"]:
            if tt <= t or done:
                continue
            ff = ((tt - o["t"]) % (2 * o["span"])) / o["span"]
            ff = ff if ff <= 1 else 2 - ff
            if ff < a0 or ff > a1:
                continue
            x, y = G.path_at(o["_path"], ff)
            tx, ty = self._to_px(x, y)
            blit(p, sk.tick(), tx, ty, 1.0, a)
        # стрелки повторов
        rem = [r for r in o["repeats"] if r > t]
        if rem and not done:
            k = len(o["repeats"]) - len(rem)
            at_end = (k % 2 == 0)
            pts = px if at_end else px[::-1]
            ex, ey = pts[-1]
            bx, by = pts[max(0, len(pts) - 6)]
            ang = math.degrees(math.atan2(by - ey, bx - ex))
            beat = 60000.0 / max(1.0, self.map.get("bpm", 120))
            pulse = 1.0 + 0.25 * (1 - ((t - o["t"]) % beat) / beat) ** 3
            blit(p, sk.reverse_arrow(), ex, ey, pulse, a, ang)
        # голова
        if o["hj"] is None and not done:
            self._draw_circle_head(p, o, o["x"], o["y"], a, t, now)
        elif "_head_hit_rt" in o:
            self._draw_explode(p, o, o["x"], o["y"], o["_head_hit_rt"], now, o["ci"])
        # шар и круг слежения
        if o["t"] <= t <= end and not done:
            bx, by = G.slider_pos(o, t)
            nx, ny = G.slider_pos(o, min(end, t + 8))
            cx, cy = self._to_px(bx, by)
            rot = math.degrees(math.atan2(ny - by, nx - bx)) if (nx, ny) != (bx, by) else 0.0
            blit(p, sk.slider_ball(o["ci"]), cx, cy, 1.0, a, rot)
            k = min(1.0, (t - o["track_t"]) / 120.0) if o["track"] else 1 - min(1.0, (t - o["track_t"]) / 100.0)
            if k > 0.01:
                sc = (0.55 + 0.45 * _ease_out(k)) if o["track"] else 1.0 + 0.15 * (1 - k)
                blit(p, sk.follow_circle(), cx, cy, sc, k * a)

    def _draw_spinner(self, p, o, t, now):
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
        p.setOpacity(a * 0.45)
        p.fillRect(self.rect(), QColor(0, 0, 0))
        prog = min(1.0, o["spins"] / max(1e-6, o["need"]))
        disc = self.skin.spinner_disc(size)
        blit(p, disc, cx, cy, 1.0, a, math.degrees(o["rot"]))
        if prog > 0:
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            g = QRadialGradient(QPointF(cx, cy), size / 2)
            g.setColorAt(0, QColor(255, 102, 170, 0))
            g.setColorAt(0.85, QColor(255, 102, 170, int(120 * prog * a)))
            g.setColorAt(1, QColor(255, 102, 170, 0))
            p.setOpacity(1.0)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(g))
            p.drawEllipse(QPointF(cx, cy), size / 2, size / 2)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        if not done and t >= o["t"]:
            f = max(0.0, 1 - (t - o["t"]) / max(1.0, o["end"] - o["t"]))
            ring = self.skin.ring(size, QColor(255, 255, 255, 200), max(2.0, size * 0.01))
            blit(p, ring, cx, cy, max(0.02, f), a)
        if not done and t < o["t"] + 700:
            k = max(0.0, (t - o["t"] + 400) / 1100)
            p.setOpacity(a * (1 - k) if t > o["t"] else a)
            p.setFont(_font(H * 0.07, QFont.Weight.Black, True))
            p.setPen(QColor(255, 255, 255))
            p.drawText(QRectF(0, cy + size * 0.25, W, H * 0.1), Qt.AlignmentFlag.AlignCenter, "КРУТИ!")
        p.setOpacity(a)
        self.digits_small.draw(p, f"{int(o['rpm'])}", W / 2, H - H * 0.05, "c")
        p.setFont(_font(H * 0.02, QFont.Weight.Bold))
        p.setPen(QColor(255, 255, 255, 170))
        p.drawText(QRectF(W / 2 + 40, H - H * 0.07, 120, H * 0.04), Qt.AlignmentFlag.AlignVCenter, "об/мин")
        p.setOpacity(1.0)

    def _draw_followpoints(self, p):
        if not self.S.get("followpoints", True) or self.state in ("failing", "failed"):
            return
        P = self.play
        t = self.t
        lo, hi = self._vis_range()
        fp = self.skin.followpoint()
        for k in range(max(1, lo), min(len(P.objs), hi + 1)):
            a, b = P.objs[k - 1], P.objs[k]
            if b.get("nc") or a["k"] == 2 or b["k"] == 2:
                continue
            if a["k"] == 1:
                ex, ey = (a["_path"][-1] if a["slides"] % 2 == 1 else a["_path"][0])
                ta = a["end"]
            else:
                ex, ey, ta = a["x"], a["y"], a["t"]
            dx, dy = b["x"] - ex, b["y"] - ey
            dist = math.hypot(dx, dy)
            if dist < P.r * 3:
                continue
            dur = b["t"] - ta
            ang = math.degrees(math.atan2(dy, dx))
            fpr = self._fp_rotated(fp, ang)                 # повёрнутая стрелка — из кэша (поворот в кадре дорог)
            step = 32
            d = P.r * 1.5
            while d < dist - P.r * 1.5:
                f = d / dist
                t_in = ta + f * dur - P.pre * 0.8
                t_out = ta + f * dur
                if t_in - 200 <= t <= t_out + 200:
                    al = min(1.0, (t - (t_in - 200)) / 200) if t < t_in else max(0.0, 1 - (t - t_out) / 200) if t > t_out else 1.0
                    x, y = self._to_px(ex + dx * f, ey + dy * f)
                    blit(p, fpr, x, y, 1.0, al * 0.85)
                d += step
        p.setOpacity(1.0)

    def _fp_rotated(self, fp, ang):
        q = int(round(ang / 3.0)) % 120
        key = ("fp", q, fp.cacheKey())
        pm = self._hud_cache.get(key)
        if pm is None:
            d = fp.devicePixelRatio()
            w, h = fp.width() / d, fp.height() / d
            s = math.hypot(w, h) + 2
            pm = SK._pm(s, s, d)
            q2 = SK._painter(pm)
            q2.translate(s / 2, s / 2)
            q2.rotate(q * 3.0)
            q2.drawPixmap(QRectF(-w / 2, -h / 2, w, h), fp, QRectF(fp.rect()))
            q2.end()
            self._hud_cache[key] = pm
        return pm

    def _draw_fx(self, p, now, under=False):
        sk = self.skin
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        for f in self.fx:
            e = now - f["t0"]
            k = e / f["life"]
            if e < 0 or k >= 1.0:
                continue                                 # ещё не родилась / уже догорела (часы кадра чуть впереди)
            kind = f["k"]
            if kind == "light":
                px, py = self._to_px(f["x"], f["y"])
                blit(p, sk.hit_light(f["ci"]), px, py, 0.8 + 0.6 * _ease_out(k), (1 - k) ** 2 * 0.85)
            elif kind == "spark":
                px, py = self._to_px(f["x"] + f["vx"] * e * (1 - k * 0.5), f["y"] + f["vy"] * e * (1 - k * 0.5))
                blit(p, sk.spark(f["ci"]), px, py, f["sc"] * (1 - k * 0.6), (1 - k))
            elif kind == "tickpop":
                px, py = self._to_px(f["x"], f["y"])
                blit(p, sk.tick(), px, py, 1 + 1.5 * k, 1 - k)
            elif kind == "star":
                x = f["px"] + f["vx"] * e
                y = f["py"] + f["vy"] * e + 0.5 * self.height() * 1.1 * e * e
                blit(p, sk.star2(), x, y, f["sc"] * (1 - 0.4 * k), (1 - k) ** 0.7, f["rot"] + f["vr"] * e)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        p.setOpacity(1.0)

    def _draw_pops(self, p, now):
        W, H = self.width(), self.height()
        sk = self.skin
        for q in self.pops:
            e = now - q["t0"]
            if "j" in q:
                j = q["j"]
                px, py = self._to_px(q["x"], q["y"])
                if j == 0:
                    sc = (1.6 - 0.6 * _ease_out(e / 0.12)) * (0.7 if q.get("small") else 1.0)
                    a = 1.0 if e < 0.5 else max(0.0, 1 - (e - 0.5) / 0.4)
                    blit(p, sk.judgement(0), px, py + 40 * e * e, sc, a, 8 * e)
                else:
                    sc = 0.6 + 0.55 * _ease_out(e / 0.1) if e < 0.1 else 1.15 - 0.15 * min(1.0, (e - 0.1) / 0.2)
                    a = 1.0 if e < 0.45 else max(0.0, 1 - (e - 0.45) / 0.45)
                    blit(p, sk.judgement(q.get("gk") or j), px, py - 12 * e, sc, a)
            elif "txt" in q and q["txt"]:
                px, py = self._to_px(q["x"], q["y"])
                a = 1.0 if e < 0.6 else max(0.0, 1 - (e - 0.6) / 0.4)
                p.setOpacity(a)
                p.setFont(_font(H * (0.06 if q.get("bonus") else 0.08), QFont.Weight.Black, True))
                p.setPen(QColor(255, 240, 160) if q.get("bonus") else QColor(255, 255, 255))
                p.drawText(QRectF(px - 300, py - 60 - (30 * e if q.get("bonus") else 0), 600, 120),
                           Qt.AlignmentFlag.AlignCenter, q["txt"])
            elif "section" in q:
                a = min(1.0, e / 0.15) if e < 1.6 else max(0.0, 1 - (e - 1.6) / 0.6)
                ok = q["section"]
                r = H * 0.12 * (1 + 0.15 * (1 - _ease_out(e / 0.3)))
                c = QColor(90, 220, 110) if ok else QColor(240, 70, 80)
                p.setOpacity(a)
                p.setPen(QPen(c, H * 0.02, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(W / 2, H / 2), r, r)
                if ok:
                    p.drawPolyline([QPointF(W / 2 - r * 0.45, H / 2), QPointF(W / 2 - r * 0.1, H / 2 + r * 0.35),
                                    QPointF(W / 2 + r * 0.5, H / 2 - r * 0.35)])
                else:
                    p.drawLine(QPointF(W / 2 - r * 0.4, H / 2 - r * 0.4), QPointF(W / 2 + r * 0.4, H / 2 + r * 0.4))
                    p.drawLine(QPointF(W / 2 + r * 0.4, H / 2 - r * 0.4), QPointF(W / 2 - r * 0.4, H / 2 + r * 0.4))
            elif "count" in q:
                if e > 0.9:
                    continue
                a = 1 - e / 0.9
                sc = 1.4 - 0.4 * _ease_out(e / 0.2)
                p.setOpacity(a)
                p.setFont(_font(H * 0.16 * sc, QFont.Weight.Black, True))
                p.setPen(QColor(255, 255, 255))
                p.drawText(QRectF(0, 0, W, H), Qt.AlignmentFlag.AlignCenter, q["count"])
            elif "toast" in q:
                a = 1.0 if e < 1.6 else max(0.0, 1 - (e - 1.6) / 0.6)
                p.setOpacity(a * 0.85)
                r = QRectF(W / 2 - 220, H * 0.12, 440, 44)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(20, 20, 30, 220))
                p.drawRoundedRect(r, 22, 22)
                p.setOpacity(a)
                p.setFont(_font(16, QFont.Weight.DemiBold))
                p.setPen(QColor(255, 255, 255))
                p.drawText(r, Qt.AlignmentFlag.AlignCenter, q["toast"])
        p.setOpacity(1.0)

    def _draw_flashlight(self, p, W, H):
        x, y = self._to_px(self.cx, self.cy)
        c = self.play.combo
        r = H * (0.36 if c < 100 else 0.30 if c < 200 else 0.25)
        g = QRadialGradient(QPointF(x, y), r * 1.25)
        g.setColorAt(0.0, QColor(0, 0, 0, 0))
        g.setColorAt(0.7, QColor(0, 0, 0, 0))
        g.setColorAt(1.0, QColor(0, 0, 0, 255))
        p.setOpacity(1.0)
        p.fillRect(self.rect(), QBrush(g))

    def _draw_cursor(self, p, now):
        if self.state in ("failed",) or (self.state == "paused" and not self.watch):
            return
        S = self.S
        sk = self.skin
        cs = float(S.get("cursor_size", 1.0)) * self.height() / 900
        x, y = self._to_px(self.cx, self.cy)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        if S.get("cursor_trail", True) and self.trail:
            tr = sk.trail(round(cs, 2))
            pts = self.trail
            step = max(5.0, 14 * cs * 0.55)                  # шаг ~ полспрайта: линия без «бусин»
            budget = 90
            prev = None
            for (tx, ty, tt) in reversed(pts):               # от свежих к старым — старые отрезаются бюджетом
                if prev is not None:
                    dx, dy = prev[0] - tx, prev[1] - ty
                    n = max(1, min(30, int(math.hypot(dx, dy) / step)))
                    for i in range(n):
                        f = i / n
                        a = max(0.0, 1 - (now - (tt + (prev[2] - tt) * f)) / 0.16)
                        if a > 0.04 and budget > 0:
                            budget -= 1
                            blit(p, tr, tx + dx * f, ty + dy * f, 1.0, a * 0.7)
                prev = (tx, ty, tt)
                if budget <= 0:
                    break
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        for rx, ry, rt in self.ripples:
            e = (now - rt) / 0.45
            px, py = self._to_px(rx, ry)
            p.setOpacity((1 - e) * 0.6)
            p.setPen(QPen(QColor(255, 220, 140), 3 * (1 - e) + 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            rr = 10 + 70 * _ease_out(e) * cs
            p.drawEllipse(QPointF(px, py), rr, rr)
        held = any(self.keys.values())
        blit(p, sk.cursor(), x, y, cs * (1.22 if held else 1.0), 1.0)
        p.setOpacity(1.0)

    def _draw_hud(self, p, W, H, now):
        P = self.play
        S = self.S
        self._hud_lay_frame = self._hud_lay()
        # HP
        bw, bh = W * 0.42, H * 0.022
        p.setOpacity(1.0)
        hp = max(0.0, min(1.0, self.hp_disp))
        low = hp < 0.3 and int(now * 6) % 2 == 0
        skin_bar = self.skin.hp_bar(H) if hasattr(self.skin, "hp_bar") else None
        if skin_bar is not None:
            _b = skin_bar[0]
            _d = _b.devicePixelRatio()
            hbox = QRectF(0, 0, _b.width() / _d, _b.height() / _d)
        elif SK.LOOK == "osu":
            hbox = QRectF(0, 0, W * 0.5, H * 0.046)
        else:
            hbox = QRectF(16, 14, bw + 8, bh + 8)
        self._hud_begin(p, "hp", hbox)
        if skin_bar is not None:                       # полоска здоровья из скина osu! (scorebar-bg / -colour)
            bg, col, (bx, by) = skin_bar
            p.drawPixmap(QPointF(0, 0), bg)
            if hp > 0.005:
                d = col.devicePixelRatio()
                p.drawPixmap(QRectF(bx, by, col.width() / d * hp, col.height() / d), col,
                             QRectF(0, 0, col.width() * hp, col.height()))
        if skin_bar is None and SK.LOOK == "osu":
            self._draw_osu_hp(p, W, H, hp, low)
            skin_bar = True
        for key in (() if skin_bar is not None else ("hpbg", "hpok", "hplow")):
            if (key, W, H) not in self._hud_cache:
                dpr = self.devicePixelRatioF()
                if key == "hpbg":
                    pm = SK._pm(bw + 8, bh + 8, dpr)
                    q = SK._painter(pm)
                    q.setPen(Qt.PenStyle.NoPen)
                    q.setBrush(QColor(0, 0, 0, 140))
                    q.drawRoundedRect(QRectF(0, 0, bw + 8, bh + 8), (bh + 8) / 2, (bh + 8) / 2)
                else:
                    pm = SK._pm(bw, bh, dpr)
                    q = SK._painter(pm)
                    g = QLinearGradient(0, 0, bw, 0)
                    a, b = ((QColor(255, 60, 60), QColor(255, 140, 140)) if key == "hplow"
                            else (QColor(255, 102, 170), QColor(255, 236, 160)))
                    g.setColorAt(0, a)
                    g.setColorAt(1, b)
                    q.setPen(Qt.PenStyle.NoPen)
                    q.setBrush(QBrush(g))
                    q.drawRoundedRect(QRectF(0, 0, bw, bh), bh / 2, bh / 2)
                q.end()
                self._hud_cache[(key, W, H)] = pm
        if skin_bar is None:
            p.drawPixmap(QPointF(16, 14), self._hud_cache[("hpbg", W, H)])
            if hp > 0.005:
                pm = self._hud_cache[("hplow" if low else "hpok", W, H)]
                p.drawPixmap(QRectF(20, 18, bw * hp, bh), pm, QRectF(0, 0, pm.width() * hp, pm.height()))
            mx = 20 + bw * hp
            glow = SK.glow_dot(bh * 2.2, QColor(255, 240, 200, 230), self.devicePixelRatioF()) \
                if "hpglow" not in self._hud_cache else self._hud_cache["hpglow"]
            self._hud_cache["hpglow"] = glow
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            blit(p, glow, mx, 18 + bh / 2, 1.0 + 0.5 * max(0.0, self.hp_flash), 0.9)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        self._hud_end(p)
        # счёт, точность, прогресс
        osu = SK.LOOK == "osu"
        sy = H * 0.036 if osu else 40
        sw = self.digits.width("00000000")
        self._hud_begin(p, "score", QRectF(W - (10 if osu else 20) - sw, sy - self.digits.h / 2, sw, self.digits.h))
        self.digits.draw(p, f"{int(self.score_disp):08d}", W - (10 if osu else 20), sy, "r")
        self._hud_end(p)
        acc = P.acc * 100
        acc_s = f"{acc:.2f}%".replace(".", ",") if osu else f"{acc:.2f}%"
        ay = sy + H * (0.05 if osu else 0.055)
        first, last = P.first_t, P.last_t
        cx, cy, r = W - (12 if osu else 20) - self.digits_small.width(acc_s) - H * 0.026, ay, H * 0.016
        ah = max(self.digits_small.h, 2 * r)
        self._hud_begin(p, "acc", QRectF(cx - r, ay - ah / 2, W - (12 if osu else 20) - (cx - r), ah))
        self.digits_small.draw(p, acc_s, W - (12 if osu else 20), ay, "r")
        p.setOpacity(1.0)
        if self.t < first:
            f = max(0.0, min(1.0, (first - self.t) / max(1.0, first - min(0.0, self.t))))
        else:
            f = max(0.0, min(1.0, (self.t - first) / max(1.0, last - first)))
        blit(p, self._pie_pix(r, f, self.t < first), cx, cy, 1.0, 1.0)
        self._hud_end(p)
        # комбо
        cs = 1.0 + 0.12 * self.combo_pop
        cw = max(self.digits_combo.width(f"{P.combo}x"), self.digits_combo.width("000x"))
        self._hud_begin(p, "combo", QRectF(18, H - H * 0.06 - self.digits_combo.h / 2, cw, self.digits_combo.h))
        for gt in self.combo_ghost:
            e = (now - gt) / 0.4
            self.digits_combo.draw(p, f"{P.combo}x", 18, H - H * 0.06, "l", 1.0 + 0.5 * e, 0.45 * (1 - e))
        self.digits_combo.draw(p, f"{P.combo}x", 18, H - H * 0.06, "l", cs, 1.0)
        self._hud_end(p)
        # клавиши
        if S.get("key_overlay", True):
            kw = H * 0.05
            x0 = W - kw - 12
            y0 = H / 2 - 2 * (kw + 6)
            self._hud_begin(p, "keys", QRectF(x0, y0, kw, 4 * (kw + 6) - 6))
            if self._skin_keys(p, x0, y0, kw):
                kw = -1                                # клавиши нарисовал скин (inputoverlay-*)
            for i, k in enumerate(("K1", "K2", "M1", "M2") if kw > 0 else ()):
                on = self.keys[k]
                c = QColor(255, 220, 100) if k[0] == "K" else QColor(255, 102, 170)
                label = str(self.key_counts[k]) if self.key_counts[k] else k
                ck = ("key", k, on, label)
                pm = self._hud_cache.get(ck)
                if pm is None:
                    if len(self._hud_cache) > 80:
                        for kk in [kk for kk in self._hud_cache if kk[0] == "key"][:40]:
                            del self._hud_cache[kk]
                    pm = SK._pm(kw, kw, self.devicePixelRatioF())
                    q = SK._painter(pm)
                    q.setPen(QPen(QColor(255, 255, 255, 120), 1.5))
                    q.setBrush(c if on else QColor(0, 0, 0, 120))
                    q.drawRoundedRect(QRectF(1, 1, kw - 2, kw - 2), 6, 6)
                    q.setPen(QColor(10, 10, 10) if on else QColor(255, 255, 255))
                    q.setFont(_font(kw * 0.3, QFont.Weight.Bold))
                    q.drawText(QRectF(0, 0, kw, kw), Qt.AlignmentFlag.AlignCenter, label)
                    q.end()
                    self._hud_cache[ck] = pm
                p.setOpacity(1.0)
                p.drawPixmap(QPointF(round(x0), round(y0 + i * (kw + 6))), pm)
                if self.key_flash[k] > 0.02 and not on:
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(c.red(), c.green(), c.blue(), int(160 * self.key_flash[k])))
                    p.drawRoundedRect(QRectF(x0 + 1, y0 + i * (kw + 6) + 1, kw - 2, kw - 2), 6, 6)
            self._hud_end(p)
        # шкала ошибок
        if S.get("hit_error", True):
            cxm = W / 2
            y = H - 18
            sc = W * 0.16 / max(1.0, P.w50)
            key = ("herr", W, H, P.w50)
            band = self._hud_cache.get(key)
            if band is None:
                band = SK._pm(2 * P.w50 * sc + 4, 26, self.devicePixelRatioF())
                q = SK._painter(band)
                q.setPen(Qt.PenStyle.NoPen)
                c0 = P.w50 * sc + 2
                for w, c in ((P.w50, QColor(255, 196, 64)), (P.w100, QColor(110, 230, 70)),
                             (P.w300, QColor(80, 190, 255))):
                    q.setBrush(c)
                    q.drawRoundedRect(QRectF(c0 - w * sc, 10, 2 * w * sc, 6), 3, 3)
                q.setBrush(QColor(255, 255, 255))
                q.drawRect(QRectF(c0 - 1, 1, 2, 24))
                q.end()
                self._hud_cache[key] = band
            self._hud_begin(p, "hiterror", QRectF(cxm - P.w50 * sc - 2, y - 22, 2 * P.w50 * sc + 4, 35))
            p.setOpacity(0.85)
            p.drawPixmap(QPointF(round(cxm - P.w50 * sc - 2), round(y - 13)), band)
            p.setPen(Qt.PenStyle.NoPen)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            for (tt, d, rt) in self.errors_vis:
                a = max(0.0, 1 - (now - rt) / 4.0)
                if a <= 0:
                    continue
                c = QColor(80, 190, 255) if abs(d) <= P.w300 else QColor(110, 230, 70) if abs(d) <= P.w100 \
                    else QColor(255, 196, 64)
                c.setAlpha(int(255 * a))
                p.setBrush(c)
                p.fillRect(QRectF(round(cxm + d * sc - 1.5), y - 11, 3, 22), c)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            if P.errors:
                recent = [d for _, d in P.errors[-20:]]
                m = sum(recent) / len(recent)
                blit(p, self._herr_arrow(), cxm + m * sc, y - 18, 1.0, 1.0)
            self._hud_end(p)
        # моды
        x = W - 20
        mpms = []
        for m in sorted(self.mods, reverse=True):
            pm = self._hud_cache.get(("mod", m))
            if pm is None:
                pm = SK.mod_icon(m, H * 0.035, self.devicePixelRatioF())
                self._hud_cache[("mod", m)] = pm
            mpms.append(pm)
        if mpms:
            tw = sum(pm.width() / pm.devicePixelRatio() + 4 for pm in mpms) - 4
            mh = max(pm.height() / pm.devicePixelRatio() for pm in mpms)
            self._hud_begin(p, "mods", QRectF(x - tw, 40 + H * 0.09, tw, mh))
        for pm in mpms:
            w = pm.width() / pm.devicePixelRatio()
            p.setOpacity(0.9)
            p.drawPixmap(QPointF(x - w, 40 + H * 0.09), pm)
            x -= w + 4
        if mpms:
            self._hud_end(p)
        # перерыв: оставшееся время и стрелки
        for a, b in P.breaks:
            if a <= self.t <= b:
                f = (b - self.t) / max(1.0, b - a)
                p.setOpacity(min(1.0, (self.t - a) / 300))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255, 200))
                bw2 = W * 0.3 * f
                p.drawRoundedRect(QRectF(W / 2 - bw2 / 2, H * 0.62, bw2, 5), 2.5, 2.5)
                p.setFont(_font(H * 0.03, QFont.Weight.Bold))
                p.drawText(QRectF(0, H * 0.64, W, H * 0.05), Qt.AlignmentFlag.AlignCenter, f"{(b - self.t) / 1000:.0f}")
                if b - self.t < 1200 and int((b - self.t) / 150) % 2 == 0:
                    self._draw_arrows(p, W, H)
        # пропуск
        if self.skip_to is not None and self.t < self.skip_to and self.state == "play":
            r = self._skip_rect()
            pul = 0.75 + 0.25 * math.sin(now * 6)
            sfr = self._skin_skip_frames()
            if sfr:                                    # кнопка из скина osu! (play-skip / play-skip-0…N)
                fr = sfr[int(now * 30) % len(sfr)]
                p.setOpacity(1.0)
                p.drawPixmap(QPointF(r.left(), r.top()), fr)
            elif SK.LOOK == "osu":                     # как кнопка Skip в osu!: тёмная плашка, «Skip» и шевроны
                p.setOpacity(0.9)
                g = QLinearGradient(r.topLeft(), r.bottomLeft())
                g.setColorAt(0, QColor(60, 64, 84, 230))
                g.setColorAt(1, QColor(24, 26, 38, 230))
                p.setBrush(QBrush(g))
                p.setPen(QPen(QColor(255, 255, 255, int(120 + 100 * pul)), 2))
                p.drawRoundedRect(r, 10, 10)
                p.setOpacity(1.0)
                p.setPen(QColor(255, 255, 255))
                p.setFont(_font(r.height() * 0.42, QFont.Weight.DemiBold))
                p.drawText(r.adjusted(18, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "Skip")
                ch = r.height() * 0.2
                x0 = r.right() - r.height() * 0.95
                p.setPen(QPen(QColor(255, 255, 255, int(160 + 95 * pul)), r.height() * 0.08, Qt.PenStyle.SolidLine,
                              Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                for k in range(2):
                    xx = x0 + k * ch * 1.4
                    p.drawPolyline([QPointF(xx, r.center().y() - ch), QPointF(xx + ch, r.center().y()),
                                    QPointF(xx, r.center().y() + ch)])
            else:
                p.setOpacity(pul)
                g = QLinearGradient(r.topLeft(), r.bottomRight())
                g.setColorAt(0, QColor(255, 102, 170))
                g.setColorAt(1, QColor(170, 80, 255))
                p.setBrush(QBrush(g))
                p.setPen(QPen(QColor(255, 255, 255, 200), 2))
                p.drawRoundedRect(r, 14, 14)
                p.setOpacity(1.0)
                p.setPen(QColor(255, 255, 255))
                p.setFont(_font(22, QFont.Weight.Black, True))
                p.drawText(r, Qt.AlignmentFlag.AlignCenter, "ПРОПУСТИТЬ ▸▸")
        # повтор / авто
        if self.watch:
            lab = "АВТОИГРА" if self.auto is not None else "ПОВТОР"
            blit(p, self._text_pix(lab, H * 0.028), W / 2, H * 0.105, 1.0, 0.6 + 0.4 * math.sin(now * 3) ** 2)
        p.setOpacity(1.0)

    # ── расположение элементов интерфейса (osu_hud_layout: редактор, раскладки скинов) ── #
    def _hud_lay(self) -> dict:
        try:
            import osu_hud_layout
            return osu_hud_layout.current(self)
        except Exception:                              # noqa: BLE001
            return {}

    def _hud_begin(self, p, key, box: QRectF):
        """Элемент интерфейса key рисуется в box (место по умолчанию); своя раскладка сдвигает,
        масштабирует или прячет его (тогда — за край экрана)."""
        boxes = self.__dict__.setdefault("_hud_boxes", {})
        boxes[key] = QRectF(box)
        p.save()
        lay = (self.__dict__.get("_hud_lay_frame") or {}).get(key)
        if not lay:
            return
        if lay.get("hide") and self.state != "layout":
            p.translate(-100000, -100000)
            return
        W, H = self.width(), self.height()
        c = box.center()
        s = float(lay.get("s", 1.0) or 1.0)
        p.translate(c.x() + float(lay.get("x", 0)) * W, c.y() + float(lay.get("y", 0)) * H)
        if s != 1.0:
            p.scale(s, s)
        p.translate(-c.x(), -c.y())

    def _hud_end(self, p):
        p.restore()

    # ── картинки вместо векторной отрисовки на каждом кадре (сглаженные фигуры и текст — дорогие:
    #    каждая такая команда ещё и сбрасывает пачку спрайтов C++) ── #
    def _pie_pix(self, r, f, lead):
        """Круг прогресса карты: кольцо + сектор; доля f с шагом 1/240 (глазу не отличить)."""
        q_ = int(round(f * 240))
        key = ("pie", round(r, 1), q_, lead)
        pm = self._hud_cache.get(key)
        if pm is None:
            dead = [k for k in self._hud_cache if k[0] == "pie"]
            if len(dead) > 300:
                for k in dead[:150]:
                    del self._hud_cache[k]
            S = 2 * r + 4
            pm = SK._pm(S, S, self.devicePixelRatioF())
            q = SK._painter(pm)
            c = QPointF(S / 2, S / 2)
            q.setPen(QPen(QColor(255, 255, 255, 220), 2))
            q.setBrush(Qt.BrushStyle.NoBrush)
            q.drawEllipse(c, r, r)
            q.setPen(Qt.PenStyle.NoPen)
            q.setBrush(QColor(140, 200, 120, 200) if lead else QColor(255, 255, 255, 200))
            q.drawPie(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), 90 * 16, int(-360 * 16 * q_ / 240))
            q.end()
            self._hud_cache[key] = pm
        return pm

    def _herr_arrow(self):
        pm = self._hud_cache.get("herr_arrow")
        if pm is None:
            pm = SK._pm(14, 10, self.devicePixelRatioF())
            q = SK._painter(pm)
            q.setPen(Qt.PenStyle.NoPen)
            q.setBrush(QColor(255, 255, 255))
            q.drawPolygon([QPointF(7, 9), QPointF(1, 1), QPointF(13, 1)])
            q.end()
            self._hud_cache["herr_arrow"] = pm
        return pm

    def _text_pix(self, s, px, col=QColor(255, 255, 255), weight=QFont.Weight.Bold):
        key = ("txt", s, round(px, 1), col.rgba(), int(weight.value) if hasattr(weight, "value") else int(weight))
        pm = self._hud_cache.get(key)
        if pm is None:
            dead = [k for k in self._hud_cache if k[0] == "txt"]
            if len(dead) > 200:
                for k in dead[:100]:
                    del self._hud_cache[k]
            f = _font(px, weight)
            fm = QFontMetricsF(f)
            w, h = fm.horizontalAdvance(s) + 4, fm.height() + 2
            pm = SK._pm(w, h, self.devicePixelRatioF())
            q = SK._painter(pm)
            q.setFont(f)
            q.setPen(col)
            q.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, s)
            q.end()
            self._hud_cache[key] = pm
        return pm

    def _skin_keys(self, p, x0, y0, kw) -> bool:
        """Клавиши из скина osu!: inputoverlay-background + inputoverlay-key (число нажатий поверх)."""
        f = getattr(self.skin, "f", None)
        if f is None or not f.has("inputoverlay-key"):
            return False
        try:
            import osu_hud_layout
            return osu_hud_layout.draw_skin_keys(self, p, f, x0, y0, kw)
        except Exception as e:                         # noqa: BLE001
            print("[osu] inputoverlay:", e)
            return False

    def _draw_osu_hp(self, p, W, H, hp, low):
        """Полоска здоровья как в стандартном скине osu!: тёмная плашка сверху слева со скруглённым
        правым краем и белая светящаяся полоса (на малом HP — красноватая)."""
        dpr = self.devicePixelRatioF()
        pw, ph = W * 0.5, H * 0.046
        bx, by, bh = H * 0.016, H * 0.016, max(4.0, H * 0.011)
        L = pw - H * 0.05
        key = ("ohpbg", W, H)
        bg = self._hud_cache.get(key)
        if bg is None:
            bg = SK._pm(pw + 2, ph + 2, dpr)
            q = SK._painter(bg)
            path = QPainterPath()
            path.moveTo(0, 0)
            path.lineTo(pw - ph * 0.6, 0)
            path.quadTo(pw, 0, pw, ph * 0.5)
            path.quadTo(pw, ph, pw - ph * 0.6, ph)
            path.lineTo(0, ph)
            path.closeSubpath()
            g = QLinearGradient(0, 0, 0, ph)
            g.setColorAt(0, QColor(70, 74, 92, 215))
            g.setColorAt(1, QColor(28, 30, 42, 215))
            q.fillPath(path, QBrush(g))
            q.strokePath(path, QPen(QColor(255, 255, 255, 45), 1.2))
            q.setPen(Qt.PenStyle.NoPen)
            q.setBrush(QColor(0, 0, 0, 120))
            q.drawRoundedRect(QRectF(bx - 2, by - 2, L + 4, bh + 4), (bh + 4) / 2, (bh + 4) / 2)
            q.end()
            self._hud_cache[key] = bg
        p.drawPixmap(QPointF(0, 0), bg)
        if hp <= 0.005:
            return
        key = ("ohpbar", W, H, bool(low))
        bar = self._hud_cache.get(key)
        pad = bh * 1.6
        if bar is None:
            col = QColor(255, 120, 110) if low else QColor(255, 255, 255)
            glow = QColor(255, 60, 60) if low else QColor(120, 200, 255)
            bar = SK._pm(L + pad * 2, bh + pad * 2, dpr)
            sil = SK._pm(L + pad * 2, bh + pad * 2, 1.0)
            q = SK._painter(sil)
            q.setPen(Qt.PenStyle.NoPen)
            q.setBrush(glow)
            q.drawRoundedRect(QRectF(pad - bh * 0.3, pad - bh * 0.3, L + bh * 0.6, bh * 1.6), bh, bh)
            q.end()
            try:
                import blur_fx
                gimg = blur_fx.blur_image(sil.toImage(), max(1.5, bh * 0.7))
            except Exception:                          # noqa: BLE001
                gimg = sil.toImage()
            q = SK._painter(bar)
            q.drawImage(QRectF(0, 0, L + pad * 2, bh + pad * 2), gimg)
            q.setPen(Qt.PenStyle.NoPen)
            q.setBrush(col)
            q.drawRoundedRect(QRectF(pad, pad, L, bh), bh / 2, bh / 2)
            q.end()
            self._hud_cache[key] = bar
        d = bar.devicePixelRatio()
        w = (L * hp + pad * 2)
        p.drawPixmap(QRectF(bx - pad, by - pad, w, bh + pad * 2), bar, QRectF(0, 0, w * d, bar.height()))
        if self.hp_flash > 0.05:                       # вспышка конца полосы при попадании
            glow = self._hud_cache.get("hpglow2")
            if glow is None:
                glow = self._hud_cache["hpglow2"] = SK.glow_dot(bh * 2.4, QColor(220, 240, 255, 230), dpr)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            blit(p, glow, bx + L * hp, by + bh / 2, 1.0, 0.8 * self.hp_flash)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

    def _draw_board(self, p, W, H):
        """Таблица рекордов слева, как в osu!: лучшие результаты карты и ваш текущий счёт среди них."""
        P = self.play
        me = {"player": self.host.player_name() if not self.watch else ("Auto" if self.auto else "повтор"),
              "score": int(self.score_disp), "combo": P.max_combo, "me": True}
        rows = sorted(self._board + [me], key=lambda r: -int(r.get("score", 0)))
        my_i = next(i for i, r in enumerate(rows) if r.get("me"))
        if len(rows) > 6:                                  # своё место видно всегда
            keep = list(range(5)) + [my_i] if my_i >= 5 else list(range(6))
            rows = [rows[i] for i in sorted(set(keep))]
            ranks = sorted(set(keep))
        else:
            ranks = list(range(len(rows)))
        bh = max(30.0, H * 0.046)
        bw = max(170.0, W * 0.13)
        y0 = H * 0.5 - (len(rows) * (bh + 4)) / 2
        dpr = self.devicePixelRatioF()
        self._hud_lay_frame = self._hud_lay()
        self._hud_begin(p, "board", QRectF(12, y0, bw, len(rows) * (bh + 4) - 4))
        for k, (rank, r) in enumerate(zip(ranks, rows)):
            y = y0 + k * (bh + 4)
            mine = r.get("me")
            key = ("board", r.get("player", ""), mine, int(bh), int(bw), rank)
            pm = self._hud_cache.get(key)
            if pm is None:
                if len([kk for kk in self._hud_cache if kk[0] == "board"]) > 40:
                    for kk in [kk for kk in self._hud_cache if kk[0] == "board"][:20]:
                        del self._hud_cache[kk]
                pm = SK._pm(bw, bh, dpr)
                q = SK._painter(pm)
                g = QLinearGradient(0, 0, bw, 0)
                base = QColor(255, 255, 255, 90) if mine else QColor(0, 0, 0, 120)
                g.setColorAt(0, base)
                g.setColorAt(1, QColor(base.red(), base.green(), base.blue(), 20))
                q.setPen(Qt.PenStyle.NoPen)
                q.setBrush(QBrush(g))
                q.drawRoundedRect(QRectF(0, 0, bw, bh), 6, 6)
                q.setPen(QColor(255, 255, 255, 120))
                q.setFont(_font(bh * 0.55, QFont.Weight.Black))
                q.drawText(QRectF(6, 0, bh * 0.9, bh), Qt.AlignmentFlag.AlignCenter, str(rank + 1))
                q.setPen(QColor(255, 255, 255))
                q.setFont(_font(bh * 0.32, QFont.Weight.Bold))
                nm = q.fontMetrics().elidedText(str(r.get("player") or "игрок"), Qt.TextElideMode.ElideRight,
                                                int(bw - bh - 12))
                q.drawText(QRectF(bh + 4, 2, bw - bh - 8, bh * 0.5), Qt.AlignmentFlag.AlignVCenter, nm)
                q.end()
                self._hud_cache[key] = pm
            p.setOpacity(1.0 if mine else 0.85)
            p.drawPixmap(QPointF(12, round(y)), pm)
            self.digits_small.draw(p, f"{int(r.get('score', 0)):,}".replace(",", "."), 12 + bh + 4,
                                   y + bh * 0.72, "l", 0.62, 1.0 if mine else 0.85)
            self.digits_small.draw(p, f"{int(r.get('combo', 0))}x", 12 + bw - 6, y + bh * 0.72, "r", 0.55, 0.8)
        p.setOpacity(1.0)
        self._hud_end(p)

    def _draw_arrows(self, p, W, H):
        p.setOpacity(0.9)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 80, 80))
        s = H * 0.05
        for side in (-1, 1):
            for yy in (H * 0.3, H * 0.7):
                x = W / 2 + side * W * 0.38
                pts = [QPointF(x - side * s, yy - s), QPointF(x + side * s * 0.4, yy), QPointF(x - side * s, yy + s)]
                p.drawPolygon(pts)

    OSU_PAUSE = {"Продолжить": ("Continue", QColor(184, 206, 0), QColor(128, 160, 0), ("pause-continue",)),
                 "Заново": ("Retry", QColor(255, 172, 10), QColor(232, 120, 0), ("pause-retry",)),
                 "Выйти в меню": ("Back to Menu", QColor(255, 70, 84), QColor(218, 22, 52), ("pause-back",))}

    def _pause_btn(self, name, r, hov):
        """Кнопка паузы как в osu!: скошенные углы, градиент, белая надпись; из скина — pause-*.png."""
        dpr = self.devicePixelRatioF()
        key = ("pbtn", name, int(r.width()), int(r.height()), hov, round(dpr, 2))
        pm = self._hud_cache.get(key)
        if pm is not None:
            return pm
        label, c0, c1, names = self.OSU_PAUSE[name]
        img = None
        f = getattr(self.skin, "f", None)
        if f is not None:
            try:
                im, _k = f.image(*names)
                img = im
            except Exception:                          # noqa: BLE001
                img = None
        pad = r.height() * 0.25
        w, h = r.width(), r.height()
        pm = SK._pm(w + pad * 2, h + pad * 2, dpr)
        q = SK._painter(pm)
        if img is not None and not img.isNull():
            k = h / img.height() * 1.0
            iw = img.width() * k
            q.drawImage(QRectF(pad + (w - iw) / 2, pad, iw, h), img)
            if hov:
                q.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
                q.setOpacity(0.25)
                q.drawImage(QRectF(pad + (w - iw) / 2, pad, iw, h), img)
            q.end()
            self._hud_cache[key] = pm
            return pm
        ch = h * 0.16
        l, t = pad, pad
        poly = [QPointF(l + ch, t), QPointF(l + w, t), QPointF(l + w, t + h - ch), QPointF(l + w - ch, t + h),
                QPointF(l, t + h), QPointF(l, t + ch)]
        path = QPainterPath()
        path.addPolygon(QPolygonF(poly))
        path.closeSubpath()
        if hov:
            q.strokePath(path, QPen(QColor(c0.red(), c0.green(), c0.blue(), 120), pad * 0.7, Qt.PenStyle.SolidLine,
                                    Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        g = QLinearGradient(0, t, 0, t + h)
        g.setColorAt(0, SK.lighter(c0, 0.18 if hov else 0.0))
        g.setColorAt(1, SK.lighter(c1, 0.12 if hov else 0.0))
        q.fillPath(path, QBrush(g))
        q.strokePath(path, QPen(QColor(0, 0, 0, 90), 1.5))
        txt = SK.outlined_text(label, h * 0.5, QColor(255, 255, 255), QColor(0, 0, 0, 0), 0, dpr,
                               glow=QColor(255, 255, 255, 45), weight=QFont.Weight.Normal)
        tw, th = txt.width() / txt.devicePixelRatio(), txt.height() / txt.devicePixelRatio()
        q.drawPixmap(QRectF(pad + (w - tw) / 2, pad + (h - th) / 2, tw, th), txt, QRectF(txt.rect()))
        q.end()
        self._hud_cache[key] = pm
        return pm

    def _draw_menu(self, p, W, H, title):
        p.setOpacity(1.0)
        if SK.LOOK == "osu":
            p.fillRect(self.rect(), QColor(0, 0, 0, 170))
            if title == "Провал":
                p.setFont(_font(H * 0.045, QFont.Weight.DemiBold))
                p.setPen(QColor(255, 120, 120))
                p.drawText(QRectF(0, H * 0.06, W, H * 0.07), Qt.AlignmentFlag.AlignCenter, "Провал")
                P = self.play
                p.setFont(_font(H * 0.022, QFont.Weight.Normal))
                p.setPen(QColor(255, 220, 220))
                p.drawText(QRectF(0, H * 0.13, W, H * 0.04), Qt.AlignmentFlag.AlignCenter,
                           f"Счёт {P.score:,}  ·  точность {P.acc * 100:.2f}%  ·  комбо {P.max_combo}x".replace(",", " "))
            for i, ((name, _col), r) in enumerate(zip(self._menu_items(), self._menu_rects())):
                hov = i == self.menu_hover
                pm = self._pause_btn(name, r, hov)
                pad = r.height() * 0.25
                sc = 1.04 if hov else 1.0
                blit(p, pm, r.center().x() + (6 if hov else 0), r.center().y(), sc, 1.0)
                _ = pad
            p.setOpacity(1.0)
            p.setFont(_font(14))
            p.setPen(QColor(255, 255, 255, 140))
            rk = key_label(bind_of(self.S, "key_retry"))
            p.drawText(QRectF(0, H - 40, W, 30), Qt.AlignmentFlag.AlignCenter,
                       f"{rk} — заново  ·  Esc — выйти" if title == "Провал" else f"Esc — продолжить  ·  {rk} — заново")
            return
        p.fillRect(self.rect(), QColor(0, 0, 0, 150))
        p.setFont(_font(H * 0.09, QFont.Weight.Black, True))
        p.setPen(QColor(255, 255, 255))
        p.drawText(QRectF(0, H * 0.12, W, H * 0.14), Qt.AlignmentFlag.AlignCenter, title)
        if title == "Провал":
            p.setFont(_font(H * 0.025, QFont.Weight.DemiBold))
            p.setPen(QColor(255, 200, 200))
            P = self.play
            p.drawText(QRectF(0, H * 0.25, W, H * 0.05), Qt.AlignmentFlag.AlignCenter,
                       f"Счёт {P.score:,}  ·  точность {P.acc * 100:.2f}%  ·  комбо {P.max_combo}x".replace(",", " "))
        for i, ((name, col), r) in enumerate(zip(self._menu_items(), self._menu_rects())):
            hov = i == self.menu_hover
            rr = r.adjusted(-12, -4, 12, 4) if hov else r
            g = QLinearGradient(rr.topLeft(), rr.bottomLeft())
            g.setColorAt(0, SK.lighter(col, 0.25))
            g.setColorAt(1, SK.darker(col, 0.15))
            p.setPen(QPen(QColor(255, 255, 255, 230 if hov else 140), 3 if hov else 2))
            p.setBrush(QBrush(g))
            p.drawRoundedRect(rr, rr.height() / 2, rr.height() / 2)
            p.setPen(QColor(255, 255, 255))
            p.setFont(_font(rr.height() * 0.36, QFont.Weight.Black, True))
            p.drawText(rr, Qt.AlignmentFlag.AlignCenter, name)
        p.setFont(_font(14))
        p.setPen(QColor(255, 255, 255, 150))
        rk = key_label(bind_of(self.S, "key_retry"))          # подсказка — по настоящим биндам
        p.drawText(QRectF(0, H - 40, W, 30), Qt.AlignmentFlag.AlignCenter,
                   f"{rk} — заново  ·  Esc — выйти" if title == "Провал" else f"Esc — продолжить  ·  {rk} — заново")

    def _draw_resume(self, p):
        x, y = self._to_px(*self._resume_pos)
        r = self.play.r * self.s * 1.3
        now = time.perf_counter()
        p.fillRect(self.rect(), QColor(0, 0, 0, 90))
        p.setOpacity(0.7 + 0.3 * math.sin(now * 5))
        p.setPen(QPen(QColor(255, 220, 120), 4))
        p.setBrush(QColor(255, 220, 120, 40))
        p.drawEllipse(QPointF(x, y), r, r)
        p.setOpacity(1.0)
        p.setFont(_font(18, QFont.Weight.Bold))
        p.setPen(QColor(255, 255, 255))
        p.drawText(QRectF(x - 200, y + r + 6, 400, 30), Qt.AlignmentFlag.AlignCenter,
                   "Наведите курсор и нажмите клавишу")
