# osu_editor.py
"""
Редактор карт темы osu! — как редактор в самой osu! (режим Compose).

Сверху таймлайн: доли (белые — сильные, красные — 1/2, синие — 1/4…), объекты, текущее время
по центру; щёлкнуть — перейти, тянуть метку выбранного объекта — сдвинуть его по времени.
В центре поле 512×384 с сеткой: круги, слайдеры, спиннеры, круги подхода и номера комбо.
Инструменты (1–4): выбор, круг, слайдер (щелчки — опорные точки, правая кнопка / Enter —
готово), спиннер. Q — новое комбо, W / E / R — свист / финиш / хлопок, Delete — удалить,
колесо и стрелки — шаг по сетке долей ([ ] — делитель 1/1…1/8), G — сетка поля, пробел —
играть, Ctrl+Z / Ctrl+Y — отмена, Ctrl+S — сохранить, F5 — проверить (Shift+F5 — смотреть
Auto) с текущего места, Esc — выйти.

Правки хранятся отдельно от сгенерированных карт (~/.neon_player/osu/edited): перегенерация
трека их не трогает, «Вернуть сгенерированную» удаляет правку.
"""
from __future__ import annotations

import copy
import math
import time

import numpy as np
from PyQt6.QtCore import QEvent, QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen

import osu_mapgen as G
import osu_skin as SK
from osu_game import blit, key_from_event
from osu_theme import PINK, BgCache, Screen, draw_bg, rrect, text

TOOLS = [("select", "Выбор", "1"), ("circle", "Круг", "2"), ("slider", "Слайдер", "3"), ("spinner", "Спиннер", "4")]
DIVS = [1, 2, 3, 4, 6, 8]
GRIDS = [0, 4, 8, 16, 32]
RATES = [0.25, 0.5, 0.75, 1.0]
TICK_COL = {1: QColor(255, 255, 255), 2: QColor(255, 70, 70), 4: QColor(80, 140, 255), 3: QColor(190, 90, 255),
            6: QColor(190, 90, 255), 8: QColor(255, 220, 90)}
_COMPUTED = ("ticks", "repeats", "ci", "num")


def _fmt(ms: float) -> str:
    ms = max(0, int(ms))
    return f"{ms // 60000:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"


def _natural_len(ct, pts) -> float:
    """Длина пути слайдера по опорным точкам (до округления по сетке долей)."""
    P = np.asarray(pts, float)
    if len(P) < 2:
        return 0.0
    if ct == "L" or len(P) == 2:
        return float(np.linalg.norm(P[-1] - P[0]))
    if ct == "P" and len(P) == 3:
        a, b, c = P
        d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
        if abs(d) > 1e-3:
            ux = ((a @ a) * (b[1] - c[1]) + (b @ b) * (c[1] - a[1]) + (c @ c) * (a[1] - b[1])) / d
            uy = ((a @ a) * (c[0] - b[0]) + (b @ b) * (a[0] - c[0]) + (c @ c) * (b[0] - a[0])) / d
            r = math.hypot(a[0] - ux, a[1] - uy)
            if r < 5000:
                t0 = math.atan2(a[1] - uy, a[0] - ux)
                tc = math.atan2(c[1] - uy, c[0] - ux)
                cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
                sgn = 1.0 if cross > 0 else -1.0
                sweep = ((tc - t0) * sgn) % (2 * math.pi)
                return r * sweep
    B = G._bezier(P, 120)
    return float(np.linalg.norm(np.diff(B, axis=0), axis=1).sum())


class EditorScreen(Screen):
    def __init__(self, shell):
        super().__init__(shell)
        self.bg = BgCache()
        self.m = None
        self.track = None
        self.dk = "hard"
        self.path = ""
        self.objs: list = []
        self.t = 0.0
        self.playing = False
        self.tool = "select"
        self.div = 4
        self.grid = 16
        self.rate = 1.0
        self.zoom = 1.0
        self.sel: list = []
        self.nc_next = False
        self.drag = None                  # что тянем мышью
        self.slider_pts: list = []        # слайдер в процессе: опорные точки
        self.undo_st: list = []
        self.redo_st: list = []
        self.dirty = False
        self.hover = None
        self._skin = None
        self._skin_key = None
        self._bodies = {}
        self._last_t = 0.0
        self._stars_due = 0.0
        self._exit_armed = 0.0
        self._saved_speed = 1.0

    def event(self, e):
        # все клавиши — редактору: Ctrl+S здесь «сохранить», а не «перемешать» плеера, Ctrl+Z — отмена
        if e.type() == QEvent.Type.ShortcutOverride and e.key() != Qt.Key.Key_F11:
            e.accept()
            return True
        return super().event(e)

    # ── карта ── #
    @property
    def S(self) -> dict:
        return self.shell.S()

    def open(self, m: dict, track: dict, dk: str):
        self.m = copy.deepcopy({k: v for k, v in m.items() if k != "objects"})
        self.objs = [self._clean(o) for o in m["objects"]]
        for o in self.objs:
            self._prep(o)
        self.track, self.dk, self.path = track, dk, str(track.get("path", ""))
        self.sel, self.slider_pts, self.drag = [], [], None
        self.undo_st, self.redo_st = [], []
        self.dirty = False
        self.tool = "select"
        self._bodies = {}
        self._renumber()
        first = self.objs[0]["t"] if self.objs else 0.0
        self.t = max(0.0, first - 1000)
        self.m["stars"] = m.get("stars", 0)

    @staticmethod
    def _clean(o) -> dict:
        return {k: (list(map(list, v)) if k == "pts" else v) for k, v in o.items()
                if not k.startswith("_") and k not in _COMPUTED and not (k == "end" and o.get("k") == 1)}

    def _prep(self, o):
        if o["k"] == 1:
            o.pop("_path", None)
            o.pop("end", None)
            G.prepare({"objects": [o], "tick_rate": self.m.get("tick_rate", 1)})
        o["_v"] = o.get("_v", 0) + 1                     # версия: кэш картинки тела слайдера

    def _end(self, o) -> float:
        return float(o.get("end", o["t"]))

    def _renumber(self):
        """Цвета и номера комбо — по тем же правилам, что в игре (Play): новое комбо по флагу nc;
        спиннер и объект после него всегда начинают новое комбо (флаг ставится сам)."""
        self.objs.sort(key=lambda o: o["t"])
        ci, num = -1, 0
        for i, o in enumerate(self.objs):
            if o["k"] == 2 or (i > 0 and self.objs[i - 1]["k"] == 2):
                o["nc"] = True
            if o.get("nc") or ci < 0:
                ci += 1
                num = 0
            num += 1
            o["ci"], o["num"] = ci, num

    def _offset(self) -> float:
        return float(self.S.get("offset", 0)) + float(self.m.get("_local_offset", 0)) if self.m else 0.0

    # ── доли ── #
    def _beats(self):
        b = self.m.get("beats") or []
        if len(b) >= 2:
            return b
        bm = 60000.0 / max(1.0, float(self.m.get("bpm", 120)))
        return [i * bm for i in range(int(float(self.m.get("length", 180000)) / bm) + 8)]

    def _beat_at(self, t):
        b = self._beats()
        import bisect
        i = max(0, min(len(b) - 2, bisect.bisect_right(b, t) - 1))
        bm = b[i + 1] - b[i] if b[i + 1] > b[i] else 60000.0 / max(1.0, float(self.m.get("bpm", 120)))
        if t < b[0]:
            k = math.floor((t - b[0]) / bm)
            return b[0] + k * bm, bm
        if t >= b[-1]:
            k = math.floor((t - b[-1]) / bm)
            return b[-1] + k * bm, bm
        return b[i], bm

    def _snap(self, t) -> float:
        b0, bm = self._beat_at(t)
        step = bm / self.div
        return b0 + round((t - b0) / step) * step

    def _step(self, n):
        """Сдвиг на n делений сетки долей."""
        t = self._snap(self.t)
        for _ in range(abs(int(n))):
            b0, bm = self._beat_at(t + (0.5 if n > 0 else -0.5))
            t = self._snap(t + (bm / self.div) * (1 if n > 0 else -1))
        self._seek(t)

    def _length(self) -> float:
        L = float(self.m.get("length") or 0)
        try:
            d = float(self.shell.win.engine._duration or 0) * 1000
            if d > 0:
                L = d
        except Exception:                              # noqa: BLE001
            pass
        return max(L, max((self._end(o) for o in self.objs), default=0) + 2000)

    # ── время и звук ── #
    def _seek(self, t):
        self.t = max(0.0, min(self._length(), float(t)))
        self._last_t = self.t
        try:
            self.shell.win.engine.seek(max(0.0, (self.t + self._offset()) / 1000.0))
        except Exception:                              # noqa: BLE001
            pass

    def _toggle_play(self):
        eng = self.shell.win.engine
        if self.playing:
            self.playing = False
            try:
                eng.pause_hard()
            except Exception:                          # noqa: BLE001
                pass
            self._seek(self._snap(self.t))
        else:
            self.shell.ensure_track(self.track)
            try:
                eng.set_speed(self.rate)
                eng.seek(max(0.0, (self.t + self._offset()) / 1000.0))
                eng.play()
            except Exception:                          # noqa: BLE001
                pass
            self.playing = True
            self._play_t0 = time.perf_counter()
            self._last_t = self.t

    def on_track_end(self):
        self.playing = False

    def enter(self):
        super().enter()
        eng = self.shell.win.engine
        self._saved_speed = float(getattr(eng, "speed", 1.0) or 1.0)
        self.shell.ensure_track(self.track)
        self.playing = False
        try:
            eng.pause_hard()
        except Exception:                              # noqa: BLE001
            pass
        self._seek(self.t)
        self.setFocus()

    def leave(self):
        eng = self.shell.win.engine
        if self.playing:
            self.playing = False
            try:
                eng.pause_hard()
            except Exception:                          # noqa: BLE001
                pass
        try:
            eng.set_speed(self._saved_speed)
        except Exception:                              # noqa: BLE001
            pass
        self._bodies = {}

    def step(self, dt, now):
        if not self.playing or self.m is None:
            return
        eng = self.shell.win.engine
        try:
            a = float(eng.get_position_smooth()) * 1000.0 - self._offset()
            # плеер встал сам (трек кончился); первые полсекунды он ещё открывает поток
            if time.perf_counter() - getattr(self, "_play_t0", 0) > 0.6 and not eng.is_playing() \
                    and not eng.is_paused():
                self.playing = False
            if time.perf_counter() - getattr(self, "_play_t0", 0) < 0.25:
                a = max(a, self._last_t)               # пока поток открывается, время не прыгает назад
        except Exception:                              # noqa: BLE001
            a = self.t + dt * 1000 * self.rate
        prev, self.t = self._last_t, a
        self._last_t = a
        if a > prev and a - prev < 400:                # хитсаунды объектов, мимо которых прошли
            for o in self.objs:
                if prev < o["t"] <= a:
                    self._hit(o)
                elif o["k"] == 1:
                    for tt in o.get("repeats", []) + [self._end(o)]:
                        if prev < tt <= a:
                            self._hit(o)

    def _hit(self, o):
        sfx = self.shell.sfx
        base = {2: "soft", 3: "drum"}.get(o.get("ss", 1), "normal")
        sfx.play(base, 0.9)
        hs = o.get("hs", 0)
        for bit, name in ((2, "whistle"), (4, "finish"), (8, "clap")):
            if hs & bit:
                sfx.play(name, 0.75)

    # ── отмена ── #
    def _snapshot(self):
        return {"objs": [self._clean(o) for o in self.objs],
                "diff": {k: self.m.get(k) for k in ("cs", "ar", "od", "hp")}}

    def _push(self):
        self.undo_st.append(self._snapshot())
        del self.undo_st[:-120]
        self.redo_st.clear()
        self.dirty = True
        self._stars_due = time.perf_counter() + 0.8

    def _restore(self, snap):
        self.objs = [dict(o) for o in snap["objs"]]
        for o in self.objs:
            self._prep(o)
        self.m.update(snap["diff"])
        self.sel = []
        self._bodies = {}
        self._renumber()
        self.dirty = True
        self._stars_due = time.perf_counter() + 0.5

    def undo(self):
        if self.undo_st:
            self.redo_st.append(self._snapshot())
            self._restore(self.undo_st.pop())
            self.shell.toast("Отменено")

    def redo(self):
        if self.redo_st:
            self.undo_st.append(self._snapshot())
            self._restore(self.redo_st.pop())
            self.shell.toast("Возвращено")

    # ── правки ── #
    def _clamp_xy(self, x, y):
        g = self.grid
        if g:
            x, y = round(x / g) * g, round(y / g) * g
        return max(0, min(512, int(round(x)))), max(0, min(384, int(round(y))))

    def _at_time(self, t):
        return next((o for o in self.objs if abs(o["t"] - t) < 2), None)

    def place_circle(self, x, y):
        t = self._snap(self.t)
        x, y = self._clamp_xy(x, y)
        self._push()
        ex = self._at_time(t)
        if ex is not None and ex["k"] == 0:
            ex["x"], ex["y"] = x, y                    # на этом месте уже круг — просто переставить
            self.sel = [ex]
        else:
            o = {"k": 0, "t": round(t), "x": x, "y": y, "hs": 0, "ss": 1, "nc": bool(self.nc_next)}
            self.objs.append(o)
            self.sel = [o]
        self.nc_next = False
        self._renumber()
        self.shell.sfx.play("normal", 0.6)

    def add_slider_point(self, x, y):
        x, y = self._clamp_xy(x, y)
        if self.slider_pts and (x, y) == tuple(self.slider_pts[-1]):
            return
        if not self.slider_pts:
            self._slider_t = self._snap(self.t)
        self.slider_pts.append([x, y])

    def finish_slider(self):
        pts = self.slider_pts
        self.slider_pts = []
        if len(pts) < 2:
            if len(pts) == 1:
                self.shell.toast("Слайдеру нужно хотя бы две точки")
            return
        ct = "L" if len(pts) == 2 else "P" if len(pts) == 3 else "B"
        nat = _natural_len(ct, pts)
        t0 = getattr(self, "_slider_t", self._snap(self.t))
        b0, bm = self._beat_at(t0)
        sv = float(self.m.get("sv") or G.diff_info(self.dk)["sv"])
        beats = max(1.0 / self.div, round(nat / sv * self.div) / self.div)   # длина — по сетке долей
        self._push()
        o = {"k": 1, "t": round(t0), "x": int(pts[0][0]), "y": int(pts[0][1]), "hs": 0, "ss": 1,
             "nc": bool(self.nc_next), "slides": 1, "span": round(beats * bm, 2), "beat": round(bm, 2),
             "len": round(beats * sv, 1), "sv": sv, "ct": ct, "pts": [list(p) for p in pts]}
        self._prep(o)
        self.objs.append(o)
        self.sel = [o]
        self.nc_next = False
        self._renumber()
        self.shell.sfx.play("normal", 0.6)

    def place_spinner(self):
        t = self._snap(self.t)
        b0, bm = self._beat_at(t)
        self._push()
        o = {"k": 2, "t": round(t), "end": round(t + 4 * bm), "x": 256, "y": 192, "hs": 0, "nc": True}
        self.objs.append(o)
        self.sel = [o]
        self._renumber()

    def delete_sel(self):
        if not self.sel:
            return
        self._push()
        ids = {id(o) for o in self.sel}
        self.objs = [o for o in self.objs if id(o) not in ids]
        self.sel = []
        self._renumber()

    def toggle_nc(self):
        if not self.sel:
            self.nc_next = not self.nc_next
            self.shell.toast("Следующий объект — с нового комбо" if self.nc_next else "Новое комбо: выкл")
            return
        self._push()
        on = not all(o.get("nc") for o in self.sel)
        for o in self.sel:
            o["nc"] = on
        self._renumber()

    def toggle_hs(self, bit):
        if not self.sel:
            return
        self._push()
        on = not all(o.get("hs", 0) & bit for o in self.sel)
        for o in self.sel:
            o["hs"] = (o.get("hs", 0) | bit) if on else (o.get("hs", 0) & ~bit)
        self._hit(self.sel[0])

    def change_repeats(self, d):
        sl = [o for o in self.sel if o["k"] == 1]
        if not sl:
            return
        self._push()
        for o in sl:
            o["slides"] = max(1, min(16, int(o["slides"]) + d))
            self._prep(o)
        self._renumber()

    def change_length(self, d):
        """Слайдер — длина пути на одно деление долей; спиннер — длительность."""
        objs = [o for o in self.sel if o["k"] in (1, 2)]
        if not objs:
            return
        self._push()
        for o in objs:
            b0, bm = self._beat_at(o["t"])
            step = bm / self.div
            if o["k"] == 2:
                o["end"] = round(max(o["t"] + step, o["end"] + d * step))
            else:
                sv = float(o.get("sv") or self.m.get("sv") or 150)
                beats = max(1.0 / self.div, o["span"] / bm + d / self.div)
                o["span"] = round(beats * bm, 2)
                o["len"] = round(beats * sv, 1)
                self._prep(o)
        self._renumber()

    def move_sel(self, dx, dy):
        for o in self.sel:
            if o["k"] == 2:
                continue
            nx, ny = self._clamp_xy(o["_x0"] + dx, o["_y0"] + dy)
            ddx, ddy = nx - o["x"], ny - o["y"]
            o["x"], o["y"] = nx, ny
            if o["k"] == 1 and (ddx or ddy):
                o["pts"] = [[p[0] + ddx, p[1] + ddy] for p in o["pts"]]
                self._prep(o)

    def shift_time(self, o, t_new):
        t_new = round(self._snap(t_new))
        d = t_new - o["t"]
        if not d:
            return
        o["t"] = t_new
        if o["k"] == 2:
            o["end"] = round(o["end"] + d)
        elif o["k"] == 1:
            self._prep(o)
        self._renumber()

    def set_diff(self, key, d):
        lo, hi = 0.0, 10.0
        self._push()
        self.m[key] = round(max(lo, min(hi, float(self.m.get(key, 5)) + d)), 1)
        self._skin = None
        self._bodies = {}

    # ── сохранение и проверка ── #
    def _export_map(self) -> dict:
        m = copy.deepcopy(self.m)
        m["objects"] = [self._clean(o) for o in self.objs]
        for o in m["objects"]:
            if o["k"] == 1:
                o.pop("end", None)
        m["edited"] = True
        opts = dict(m.get("opts") or {})
        opts["edited"] = int(time.time())                # свой рекорд-лист у правленой карты
        m["opts"] = opts
        base = G.diff_info(self.dk)["name"]
        m["name"] = f"{base} (правка)"
        return G.finalize_map(m)

    def save(self):
        if self.m is None:
            return
        if not self.objs:
            self.shell.toast("Пустую карту не сохраняю — поставьте хотя бы один объект")
            return
        m = self._export_map()
        try:
            G.save_edited(self.path, self.dk, m)
        except Exception as e:                         # noqa: BLE001
            self.shell.toast(f"Не удалось сохранить: {e}")
            return
        m["_local_offset"] = float(self.m.get("_local_offset", 0))
        st = self.shell.sets.get(self.path)
        if st and st.get("data"):
            st["data"]["maps"][self.dk] = m
        self.m["stars"] = m["stars"]
        self.dirty = False
        self.shell.toast(f"Карта сохранена · ★ {m['stars']:.2f} · объектов {len(m['objects'])}")

    def revert(self):
        G.delete_edited(self.path, self.dk)
        self.shell.sets.pop(self.path, None)
        self.dirty = False
        self.shell.toast("Правки удалены — вернётся сгенерированная карта")
        self.shell.request_set(self.track, force=True)
        self.shell.show_screen("select")

    def test(self, auto=False):
        if not self.objs:
            self.shell.toast("На карте нет объектов")
            return
        m = self._export_map()
        m["_local_offset"] = float(self.m.get("_local_offset", 0))
        start = self._snap(self.t)
        self.shell.test_map(m, self.track, start, {"AU"} if auto else set())

    def exit(self):
        if self.slider_pts:
            self.slider_pts = []
            return
        if self.sel:
            self.sel = []
            return
        if self.dirty and time.perf_counter() - self._exit_armed > 3:
            self._exit_armed = time.perf_counter()
            self.shell.toast("Есть несохранённые правки: Ctrl+S — сохранить, Esc ещё раз — выйти без сохранения")
            return
        self.shell.show_screen("select")

    # ── раскладка ── #
    def _layout(self):
        W, H = self.width(), self.height()
        tl = QRectF(0, 0, W, 78)
        bar = QRectF(0, H - 92, W, 92)
        tools = QRectF(12, 96, 70, 4 * 64 + 150)
        panel = QRectF(W - 270, 96, 258, H - 96 - 104)
        area = QRectF(96, 92, W - 96 - 282, H - 92 - 100)
        s = min(area.width() / 512, area.height() / 384) * 0.94
        ox = area.center().x() - 256 * s
        oy = area.center().y() - 192 * s
        return {"tl": tl, "bar": bar, "tools": tools, "panel": panel, "area": area, "s": s, "ox": ox, "oy": oy}

    def _px(self, L, x, y):
        return L["ox"] + x * L["s"], L["oy"] + y * L["s"]

    def _osu(self, L, px, py):
        return (px - L["ox"]) / L["s"], (py - L["oy"]) / L["s"]

    def _skin_for(self, L):
        R = G.radius(float(self.m.get("cs", 4))) * L["s"]
        key = (round(R, 1), self.devicePixelRatioF(), id(SK.COMBO_COLORS), id(SK.ACTIVE), SK.LOOK,
               SK.SKIN_BALL)                                                               # сменили палитру/скин/вид
        if self._skin is None or self._skin_key != key:
            self._skin = SK.make_skin(R, self.devicePixelRatioF(), ps=L["s"])
            self._skin_key = key
            self._bodies = {}
        return self._skin

    def _tl_span(self):
        return 2600.0 / self.zoom                       # мс по обе стороны от текущего времени

    def _tl_x(self, L, t):
        r = L["tl"]
        return r.center().x() + (t - self.t) / self._tl_span() * (r.width() / 2)

    def _tl_t(self, L, x):
        r = L["tl"]
        return self.t + (x - r.center().x()) / (r.width() / 2) * self._tl_span()

    # кнопки (прямоугольник → действие)
    def _buttons(self, L):
        out = []
        tr = L["tools"]
        for i, (key, name, hk) in enumerate(TOOLS):
            out.append((QRectF(tr.left(), tr.top() + i * 64, 70, 56), ("tool", key), f"{name}", hk))
        y = tr.top() + 4 * 64 + 8
        out.append((QRectF(tr.left(), y, 70, 40), ("nc",), "Комбо", "Q"))
        out.append((QRectF(tr.left(), y + 46, 70, 40), ("grid",), f"Сетка {self.grid or 'нет'}", "G"))
        bar = L["bar"]
        bx = 18
        out.append((QRectF(bx, bar.top() + 22, 52, 52), ("play",), "", "Пробел"))
        rx = bar.left() + 360
        for j, r in enumerate(RATES):
            out.append((QRectF(rx + j * 54, bar.top() + 50, 50, 28), ("rate", r), f"{int(r * 100)}%", ""))
        dx = rx + 4 * 54 + 16
        out.append((QRectF(dx, bar.top() + 50, 28, 28), ("div", -1), "−", "["))
        out.append((QRectF(dx + 92, bar.top() + 50, 28, 28), ("div", 1), "+", "]"))
        W = self.width()
        acts = [("exit", "Выход"), ("save", "Сохранить"), ("test", "Проверить"), ("redo", "Повторить"),
                ("undo", "Отменить")]
        x = W - 18
        for key, name in acts:
            w = 112 if key in ("save", "test") else 100
            x -= w
            out.append((QRectF(x, bar.top() + 46, w, 34), ("act", key), name, ""))
            x -= 8
        pr = L["panel"]
        y = pr.top() + 60
        for key in ("cs", "ar", "od", "hp"):
            out.append((QRectF(pr.right() - 78, y, 30, 26), ("diff", key, -0.1), "−", ""))
            out.append((QRectF(pr.right() - 40, y, 30, 26), ("diff", key, 0.1), "+", ""))
            y += 32
        if self.sel:
            y = pr.top() + 60 + 4 * 32 + 70
            out.append((QRectF(pr.left() + 12, y, 110, 30), ("objnc",), "Новое комбо", "Q"))
            for j, (bit, name, hk) in enumerate(((2, "Свист", "W"), (4, "Финиш", "E"), (8, "Хлопок", "R"))):
                out.append((QRectF(pr.left() + 12 + j * 78, y + 38, 72, 30), ("hs", bit), name, hk))
            y += 80
            if any(o["k"] == 1 for o in self.sel):
                out.append((QRectF(pr.right() - 78, y, 30, 26), ("rep", -1), "−", ""))
                out.append((QRectF(pr.right() - 40, y, 30, 26), ("rep", 1), "+", ""))
                y += 32
            if any(o["k"] in (1, 2) for o in self.sel):
                out.append((QRectF(pr.right() - 78, y, 30, 26), ("len", -1), "−", ""))
                out.append((QRectF(pr.right() - 40, y, 30, 26), ("len", 1), "+", ""))
                y += 32
            out.append((QRectF(pr.left() + 12, y + 6, pr.width() - 24, 30), ("act", "delete"), "Удалить (Delete)", ""))
        out.append((QRectF(pr.left() + 12, pr.bottom() - 40, pr.width() - 24, 30), ("act", "revert"),
                    "Вернуть сгенерированную", ""))
        return out

    def _press_button(self, act):
        k = act[0]
        if k == "tool":
            self.tool = act[1]
            self.slider_pts = []
        elif k == "nc":
            self.toggle_nc()
        elif k == "objnc":
            self.toggle_nc()
        elif k == "grid":
            self.grid = GRIDS[(GRIDS.index(self.grid) + 1) % len(GRIDS)]
        elif k == "play":
            self._toggle_play()
        elif k == "rate":
            self.rate = act[1]
            if self.playing:
                try:
                    self.shell.win.engine.set_speed(self.rate)
                except Exception:                      # noqa: BLE001
                    pass
        elif k == "div":
            i = DIVS.index(self.div)
            self.div = DIVS[max(0, min(len(DIVS) - 1, i + act[1]))]
        elif k == "diff":
            self.set_diff(act[1], act[2])
        elif k == "hs":
            self.toggle_hs(act[1])
        elif k == "rep":
            self.change_repeats(act[1])
        elif k == "len":
            self.change_length(act[1])
        elif k == "act":
            {"exit": self.exit, "save": self.save, "test": self.test, "undo": self.undo, "redo": self.redo,
             "delete": self.delete_sel, "revert": self.revert}[act[1]]()
        self.sfx("menuclick", 0.5)

    # ── отрисовка ── #
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        W, H = self.width(), self.height()
        draw_bg(p, self.bg.get(self.shell.cover_of(self.track), W, H, self.devicePixelRatioF(), blur=True), W, H,
                0.78)
        if self.m is None:
            p.end()
            return
        L = self._layout()
        self._draw_field(p, L)
        self._draw_timeline(p, L)
        self._draw_tools(p, L)
        self._draw_panel(p, L)
        self._draw_bar(p, L)
        p.end()

    def _draw_field(self, p, L):
        s, ox, oy = L["s"], L["ox"], L["oy"]
        field = QRectF(ox, oy, 512 * s, 384 * s)
        rrect(p, field.adjusted(-6, -6, 6, 6), 10, QColor(0, 0, 0, 90), QPen(QColor(255, 255, 255, 40), 1))
        if self.grid:
            p.setPen(QPen(QColor(255, 255, 255, 22), 1))
            g = self.grid
            for x in range(0, 513, g):
                X = ox + x * s
                p.drawLine(QPointF(X, oy), QPointF(X, oy + 384 * s))
            for y in range(0, 385, g):
                Y = oy + y * s
                p.drawLine(QPointF(ox, Y), QPointF(ox + 512 * s, Y))
            p.setPen(QPen(QColor(255, 255, 255, 50), 1))
            p.drawLine(QPointF(ox + 256 * s, oy), QPointF(ox + 256 * s, oy + 384 * s))
            p.drawLine(QPointF(ox, oy + 192 * s), QPointF(ox + 512 * s, oy + 192 * s))
        sk = self._skin_for(L)
        t = self.t
        pre = G.preempt(float(self.m.get("ar", 8)))
        selected = {id(o) for o in self.sel}
        vis = [o for o in self.objs if o["t"] - pre <= t <= self._end(o) + 300]
        for o in reversed(vis):
            a = 1.0
            if t < o["t"]:
                a = min(1.0, (t - (o["t"] - pre)) / max(1.0, pre * 0.35))
            elif t > self._end(o):
                a = max(0.0, 1.0 - (t - self._end(o)) / 300.0)
            a = max(0.15, a)
            if o["k"] == 2:
                self._draw_spinner(p, L, o, a, id(o) in selected)
                continue
            if o["k"] == 1:
                self._draw_slider(p, L, sk, o, a)
            x, y = self._px(L, o["x"], o["y"])
            blit(p, sk.circle(o["ci"]), x, y, 1.0, a)
            blit(p, sk.number(o["num"], o["ci"]), x, y, 1.0, a)
            if t < o["t"]:
                k = (o["t"] - t) / pre
                blit(p, sk.approach(o["ci"]), x, y, 1.0 + 3.0 * k, a * 0.9)
            if id(o) in selected:
                p.setOpacity(1.0)
                p.setPen(QPen(QColor(255, 220, 80), 3))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(x, y), sk.R * 1.05, sk.R * 1.05)
        p.setOpacity(1.0)
        # слайдер в процессе
        if self.slider_pts:
            pts = [self._px(L, *q) for q in self.slider_pts]
            if len(self.slider_pts) >= 2:
                ct = "L" if len(self.slider_pts) == 2 else "P" if len(self.slider_pts) == 3 else "B"
                try:
                    path = G.slider_path(ct, self.slider_pts, max(4.0, _natural_len(ct, self.slider_pts)), 4.0)
                    pp = QPainterPath(QPointF(*self._px(L, *path[0])))
                    for q in path[1:]:
                        pp.lineTo(QPointF(*self._px(L, *q)))
                    p.setPen(QPen(QColor(255, 255, 255, 140), sk.R * 1.6, Qt.PenStyle.SolidLine,
                                  Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                    p.drawPath(pp)
                except Exception:                      # noqa: BLE001
                    pass
            p.setPen(QPen(QColor(255, 255, 255, 120), 1, Qt.PenStyle.DashLine))
            for a_, b_ in zip(pts, pts[1:]):
                p.drawLine(QPointF(*a_), QPointF(*b_))
            p.setPen(QPen(QColor(255, 255, 255), 2))
            for i, q in enumerate(pts):
                p.setBrush(QColor(255, 102, 170) if i == 0 else QColor(255, 255, 255))
                p.drawRect(QRectF(q[0] - 5, q[1] - 5, 10, 10))
        # курсор-превью круга
        if self.tool in ("circle", "slider") and self.hover is not None and self.hover[0] == "field":
            hx, hy = self._clamp_xy(*self.hover[1])
            x, y = self._px(L, hx, hy)
            p.setOpacity(0.45)
            blit(p, sk.white_circle(), x, y, 0.95, 0.45)
            p.setOpacity(1.0)

    def _draw_slider(self, p, L, sk, o, a):
        key = (id(o), o.get("_v"), round(L["s"], 3), round(L["ox"]), round(L["oy"]), o["ci"])
        hit = self._bodies.get(key)
        if hit is None:
            path = o["_path"]
            px = np.empty_like(path)
            px[:, 0] = L["ox"] + path[:, 0] * L["s"]
            px[:, 1] = L["oy"] + path[:, 1] * L["s"]
            hit = sk.slider_body(px, o["ci"], cheap=False)
            if len(self._bodies) > 40:
                self._bodies.clear()
            self._bodies[key] = hit
        pm, org = hit
        p.setOpacity(a * 0.95)
        p.drawPixmap(org, pm)
        p.setOpacity(a)
        if o["slides"] > 1:                            # стрелка повтора на конце
            path = o["_path"]
            ex, ey = self._px(L, *path[-1])
            bx, by = self._px(L, *path[max(0, len(path) - 6)])
            blit(p, sk.reverse_arrow(), ex, ey, 1.0, a, math.degrees(math.atan2(by - ey, bx - ex)))
        if o["t"] <= self.t <= self._end(o):           # шар слайдера
            bx, by = G.slider_pos(o, self.t)
            X, Y = self._px(L, bx, by)
            blit(p, sk.slider_ball(o["ci"]), X, Y, 1.0, a)

    def _draw_spinner(self, p, L, o, a, sel):
        cx, cy = self._px(L, 256, 192)
        r = 170 * L["s"]
        p.setOpacity(a)
        p.setPen(QPen(QColor(255, 220, 80) if sel else QColor(255, 255, 255, 180), 4 if sel else 3))
        p.setBrush(QColor(0, 0, 0, 60))
        p.drawEllipse(QPointF(cx, cy), r, r)
        k = (self.t - o["t"]) / max(1.0, o["end"] - o["t"])
        if 0 <= k <= 1:
            p.setPen(QPen(PINK, 6))
            p.drawArc(QRectF(cx - r, cy - r, 2 * r, 2 * r), 90 * 16, -int(360 * 16 * k))
        text(p, QRectF(cx - 150, cy - 18, 300, 36), "Спиннер", 22, QColor(255, 255, 255), QFont.Weight.Bold,
             Qt.AlignmentFlag.AlignCenter)
        p.setOpacity(1.0)

    def _draw_timeline(self, p, L):
        r = L["tl"]
        p.fillRect(r, QColor(10, 8, 14, 220))
        t0, t1 = self._tl_t(L, r.left()), self._tl_t(L, r.right())
        # доли
        b = self._beats()
        import bisect
        i = max(0, bisect.bisect_left(b, t0) - 1)
        while i < len(b) - 1 and b[i] <= t1:
            bm = b[i + 1] - b[i]
            for k in range(self.div):
                tt = b[i] + bm * k / self.div
                if tt < t0 or tt > t1:
                    continue
                x = self._tl_x(L, tt)
                if k == 0:
                    down = (i - int(self.m.get("phase", 0))) % 4 == 0
                    col, h = QColor(255, 255, 255), (30 if down else 22)
                else:
                    frac = k / self.div
                    den = next(d for d in (2, 3, 4, 6, 8) if abs(frac * d - round(frac * d)) < 1e-6) \
                        if any(abs(frac * d - round(frac * d)) < 1e-6 for d in (2, 3, 4, 6, 8)) else self.div
                    col, h = TICK_COL.get(den, QColor(200, 200, 200)), 14
                p.setPen(QPen(col, 2))
                p.drawLine(QPointF(x, r.bottom() - 4), QPointF(x, r.bottom() - 4 - h))
            i += 1
        # объекты
        selected = {id(o) for o in self.sel}
        sk_cols = SK.COMBO_COLORS
        for o in self.objs:
            if self._end(o) < t0 or o["t"] > t1:
                continue
            x0 = self._tl_x(L, o["t"])
            y = r.top() + 26
            col = QColor(150, 150, 150) if o["k"] == 2 else sk_cols[o["ci"] % len(sk_cols)]
            if o["k"] in (1, 2):
                x1 = self._tl_x(L, self._end(o))
                rrect(p, QRectF(x0, y - 9, max(2.0, x1 - x0), 18), 9, QColor(col.red(), col.green(), col.blue(), 140))
            pen = QPen(QColor(255, 220, 80), 3) if id(o) in selected else QPen(QColor(255, 255, 255), 2)
            p.setPen(pen)
            p.setBrush(col)
            p.drawEllipse(QPointF(x0, y), 10, 10)
        # текущее время
        cx = r.center().x()
        p.setPen(QPen(QColor(255, 255, 255), 2))
        p.drawLine(QPointF(cx, r.top()), QPointF(cx, r.bottom()))
        text(p, QRectF(r.left() + 12, r.top() + 4, 260, 18), f"{self.track.get('artist', '')} - "
             f"{self.track.get('title', '')}", 12, QColor(255, 255, 255, 170), QFont.Weight.DemiBold)

    def _btn(self, p, rect, label, on=False, hov=False, accent=None, size=13):
        col = accent if (accent is not None and not on) else (PINK if on else QColor(255, 255, 255, 30))
        if hov and not on:
            col = SK.lighter(col, 0.25) if accent is not None else QColor(255, 255, 255, 55)
        rrect(p, rect, min(12, rect.height() / 2), col)
        text(p, rect, label, size, QColor(255, 255, 255), QFont.Weight.Bold, Qt.AlignmentFlag.AlignCenter)

    def _draw_tools(self, p, L):
        hov = self.hover[1] if self.hover and self.hover[0] == "btn" else None
        for rect, act, label, hk in self._buttons(L):
            if act[0] == "tool":
                on = self.tool == act[1]
                self._btn(p, rect, "", on, hov == act)
                kinds = {"select": "select", "circle": "circle", "slider": "slider", "spinner": "spinner"}
                self._tool_icon(p, kinds[act[1]], QRectF(rect.left() + 20, rect.top() + 6, 30, 26))
                text(p, QRectF(rect.left(), rect.bottom() - 22, rect.width(), 18), f"{hk} {label}", 11,
                     QColor(255, 255, 255, 220), QFont.Weight.DemiBold, Qt.AlignmentFlag.AlignCenter)
            elif act[0] in ("nc", "grid"):
                on = act[0] == "nc" and self.nc_next
                self._btn(p, rect, label, on, hov == act, size=11)

    def _tool_icon(self, p, kind, r):
        p.setPen(QPen(QColor(255, 255, 255), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        c = r.center()
        if kind == "select":
            path = QPainterPath()
            path.moveTo(c.x() - 6, c.y() - 10)
            path.lineTo(c.x() + 8, c.y() + 2)
            path.lineTo(c.x() + 1, c.y() + 3)
            path.lineTo(c.x() + 4, c.y() + 10)
            path.lineTo(c.x(), c.y() + 11)
            path.lineTo(c.x() - 3, c.y() + 4)
            path.lineTo(c.x() - 6, c.y() + 8)
            path.closeSubpath()
            p.setBrush(QColor(255, 255, 255))
            p.drawPath(path)
        elif kind == "circle":
            p.drawEllipse(c, 10, 10)
        elif kind == "slider":
            p.setPen(QPen(QColor(255, 255, 255), 8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(c.x() - 9, c.y() + 5), QPointF(c.x() + 9, c.y() - 5))
            p.setPen(QPen(QColor(30, 22, 36), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(c.x() - 9, c.y() + 5), QPointF(c.x() + 9, c.y() - 5))
        else:
            for rr in (11, 6):
                p.drawEllipse(c, rr, rr)

    def _draw_panel(self, p, L):
        pr = L["panel"]
        rrect(p, pr, 14, QColor(16, 12, 22, 215), QPen(QColor(255, 255, 255, 30), 1))
        x = pr.left() + 14
        text(p, QRectF(x, pr.top() + 10, pr.width() - 28, 22), G.diff_info(self.dk)["name"] +
             ("  · правка" if self.dirty else ""), 16, QColor(255, 255, 255), QFont.Weight.Bold)
        n_c = sum(1 for o in self.objs if o["k"] == 0)
        n_s = sum(1 for o in self.objs if o["k"] == 1)
        n_sp = sum(1 for o in self.objs if o["k"] == 2)
        if self._stars_due and time.perf_counter() >= self._stars_due:
            self._stars_due = 0.0
            try:
                tmp = {"objects": [self._clean(o) for o in self.objs], "cs": self.m.get("cs", 4)}
                for o in tmp["objects"]:
                    if o["k"] == 1:
                        o.pop("end", None)
                G.prepare(tmp)
                self.m["stars"] = G.star_rating(tmp)["stars"] if len(tmp["objects"]) > 1 else 0.0
            except Exception:                          # noqa: BLE001
                pass
        text(p, QRectF(x, pr.top() + 30, pr.width() - 28, 16), f"★ {float(self.m.get('stars', 0)):.2f}", 12,
             QColor(255, 220, 140), QFont.Weight.Bold)
        text(p, QRectF(x + 60, pr.top() + 30, pr.width() - 88, 16),
             f"круги {n_c} · слайдеры {n_s} · спиннеры {n_sp}", 11, QColor(255, 255, 255, 160), QFont.Weight.Normal)
        y = pr.top() + 60
        names = {"cs": "Размер кругов (CS)", "ar": "Скорость появления (AR)", "od": "Точность (OD)",
                 "hp": "Здоровье (HP)"}
        hov = self.hover[1] if self.hover and self.hover[0] == "btn" else None
        for key in ("cs", "ar", "od", "hp"):
            text(p, QRectF(x, y, 150, 26), names[key], 12, QColor(255, 255, 255, 210), QFont.Weight.DemiBold)
            text(p, QRectF(pr.right() - 128, y, 46, 26), f"{float(self.m.get(key, 5)):.1f}", 13, QColor(255, 179, 212),
                 QFont.Weight.Bold, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            y += 32
        for rect, act, label, hk in self._buttons(L):
            if act[0] in ("diff", "rep", "len", "hs", "objnc") or (act[0] == "act" and act[1] in ("delete", "revert")):
                on = False
                if act[0] == "hs":
                    on = bool(self.sel) and all(o.get("hs", 0) & act[1] for o in self.sel)
                elif act[0] == "objnc":
                    on = bool(self.sel) and all(o.get("nc") for o in self.sel)
                acc = QColor(200, 60, 80) if act == ("act", "delete") else None
                self._btn(p, rect, label, on, hov == act, accent=acc, size=12 if len(label) > 2 else 15)
        if self.sel:
            y = pr.top() + 60 + 4 * 32 + 14
            o = self.sel[0]
            kind = {0: "Круг", 1: "Слайдер", 2: "Спиннер"}[o["k"]]
            more = f"  (+{len(self.sel) - 1})" if len(self.sel) > 1 else ""
            text(p, QRectF(x, y, pr.width() - 28, 20), f"{kind}{more} · {_fmt(o['t'])}", 13, QColor(255, 255, 255),
                 QFont.Weight.Bold)
            text(p, QRectF(x, y + 22, pr.width() - 28, 18),
                 f"x {o['x']} · y {o['y']} · комбо {o.get('num', '')}", 11, QColor(255, 255, 255, 170),
                 QFont.Weight.Normal)
            y = pr.top() + 60 + 4 * 32 + 70 + 80
            if any(q["k"] == 1 for q in self.sel):
                sl = next(q for q in self.sel if q["k"] == 1)
                text(p, QRectF(x, y, 150, 26), f"Проходов: {sl['slides']}", 12, QColor(255, 255, 255, 210),
                     QFont.Weight.DemiBold)
                y += 32
            if any(q["k"] in (1, 2) for q in self.sel):
                q = next(q for q in self.sel if q["k"] in (1, 2))
                b0, bm = self._beat_at(q["t"])
                dur = (q["span"] if q["k"] == 1 else q["end"] - q["t"]) / bm
                text(p, QRectF(x, y, 150, 26), f"Длина: {dur:.2f} доли", 12, QColor(255, 255, 255, 210),
                     QFont.Weight.DemiBold)
        else:
            y = pr.top() + 60 + 4 * 32 + 14
            help_lines = ["1–4 — инструменты", "Колесо, стрелки — шаг по долям", "[ ] — делитель долей",
                          "Q — новое комбо, W/E/R — звуки", "Слайдер: щелчки, правая кнопка — готово",
                          "Пробел — играть, F5 — проверить", "Ctrl+Z / Ctrl+Y — отмена", "Ctrl+S — сохранить"]
            for line in help_lines:
                text(p, QRectF(x, y, pr.width() - 28, 18), line, 11, QColor(255, 255, 255, 150), QFont.Weight.Normal)
                y += 20

    def _draw_bar(self, p, L):
        bar = L["bar"]
        p.fillRect(bar, QColor(10, 8, 14, 225))
        hov = self.hover[1] if self.hover and self.hover[0] == "btn" else None
        # полоса всей песни с объектами
        sr = self._scrub_rect(L)
        rrect(p, sr, 3, QColor(255, 255, 255, 30))
        Ln = self._length()
        p.setPen(QPen(QColor(255, 102, 170, 150), 1))
        step = max(1, len(self.objs) // 600)
        for o in self.objs[::step]:
            x = sr.left() + o["t"] / Ln * sr.width()
            p.drawLine(QPointF(x, sr.top()), QPointF(x, sr.bottom()))
        x = sr.left() + self.t / Ln * sr.width()
        rrect(p, QRectF(sr.left(), sr.top(), x - sr.left(), sr.height()), 3, QColor(255, 255, 255, 90))
        p.setPen(QPen(QColor(255, 255, 255), 3))
        p.drawLine(QPointF(x, sr.top() - 4), QPointF(x, sr.bottom() + 4))
        for rect, act, label, hk in self._buttons(L):
            if act[0] == "play":
                rrect(p, rect, rect.height() / 2, PINK if hov != act else SK.lighter(PINK, 0.2))
                SK.draw_icon(p, "pause" if self.playing else "play", rect.adjusted(14, 14, -14, -14),
                             QColor(255, 255, 255))
            elif act[0] == "rate":
                self._btn(p, rect, label, abs(self.rate - act[1]) < 1e-6, hov == act, size=12)
            elif act[0] == "div":
                self._btn(p, rect, label, False, hov == act, size=15)
            elif act[0] == "act" and act[1] not in ("delete", "revert"):
                acc = QColor(70, 170, 90) if act[1] == "save" else QColor(60, 140, 230) if act[1] == "test" else None
                self._btn(p, rect, label, False, hov == act, accent=acc, size=12)
        text(p, QRectF(84, bar.top() + 48, 200, 30), _fmt(self.t), 20, QColor(255, 255, 255), QFont.Weight.Bold)
        rx = bar.left() + 360 + 4 * 54 + 16
        text(p, QRectF(rx + 30, bar.top() + 50, 60, 28), f"1/{self.div}", 15, QColor(255, 255, 255),
             QFont.Weight.Bold, Qt.AlignmentFlag.AlignCenter)
        text(p, QRectF(bar.left() + 360, bar.top() + 32, 200, 16), "Скорость", 10, QColor(255, 255, 255, 140),
             QFont.Weight.DemiBold)
        text(p, QRectF(rx, bar.top() + 32, 120, 16), "Делитель долей", 10, QColor(255, 255, 255, 140),
             QFont.Weight.DemiBold, Qt.AlignmentFlag.AlignCenter)

    def _scrub_rect(self, L):
        bar = L["bar"]
        return QRectF(84, bar.top() + 12, self.width() - 84 - 18, 10)

    # ── ввод ── #
    def _hit_test(self, pos):
        L = self._layout()
        for rect, act, _l, _h in self._buttons(L):
            if rect.contains(pos):
                return ("btn", act)
        if self._scrub_rect(L).adjusted(0, -8, 0, 8).contains(pos):
            return ("scrub", None)
        if L["tl"].contains(pos):
            return ("tl", None)
        if L["area"].contains(pos):
            return ("field", self._osu(L, pos.x(), pos.y()))
        return None

    def _pick(self, L, x, y):
        """Объект под курсором (из видимых сейчас; верхний — более ранний)."""
        pre = G.preempt(float(self.m.get("ar", 8)))
        R = G.radius(float(self.m.get("cs", 4)))
        best = None
        for o in self.objs:
            if not (o["t"] - pre <= self.t <= self._end(o) + 300):
                continue
            if o["k"] == 2:
                if (x - 256) ** 2 + (y - 192) ** 2 <= 175 ** 2 and best is None:
                    best = o
                continue
            d2 = (x - o["x"]) ** 2 + (y - o["y"]) ** 2
            if d2 <= R * R:
                return o
            if o["k"] == 1 and best is None:
                path = o["_path"]
                if np.min((path[:, 0] - x) ** 2 + (path[:, 1] - y) ** 2) <= R * R:
                    best = o
        return best

    def _tl_pick(self, L, x):
        for o in self.objs:
            if abs(self._tl_x(L, o["t"]) - x) <= 11:
                return o
        return None

    def mouseMoveEvent(self, e):
        pos = e.position()
        L = self._layout()
        d = self.drag
        if d is not None:
            if d[0] == "move":
                x, y = self._osu(L, pos.x(), pos.y())
                if not d[3] and math.hypot(x - d[1], y - d[2]) > 2:
                    d[3] = True
                    self._push()
                if d[3]:
                    self.move_sel(x - d[1], y - d[2])
            elif d[0] == "time":
                o = d[1]
                if not d[3]:
                    d[3] = True
                    self._push()
                self.shift_time(o, self._tl_t(L, pos.x()))
            elif d[0] == "scrub":
                sr = self._scrub_rect(L)
                self._seek(max(0.0, min(1.0, (pos.x() - sr.left()) / sr.width())) * self._length())
            elif d[0] == "tl":
                dt = (d[1] - pos.x()) / (L["tl"].width() / 2) * self._tl_span()
                self._seek(d[2] + dt)
            elif d[0] == "rect":
                d[3] = pos
            self.update()
            return
        old = self.hover
        self.hover = self._hit_test(pos)
        if self.hover != old and self.hover and self.hover[0] == "btn" and (not old or old != self.hover):
            self.sfx("hover", 0.3)

    def mousePressEvent(self, e):
        pos = e.position()
        L = self._layout()
        h = self._hit_test(pos)
        right = e.button() == Qt.MouseButton.RightButton
        if self.tool == "slider" and self.slider_pts and right:
            self.finish_slider()
            return
        if h is None:
            return
        kind = h[0]
        if kind == "btn":
            self._press_button(h[1])
            return
        if kind == "scrub":
            self.drag = ["scrub"]
            self.mouseMoveEvent(e)
            return
        if kind == "tl":
            o = self._tl_pick(L, pos.x())
            if o is not None and not right:
                self.sel = [o]
                self.drag = ["time", o, pos.x(), False]
            else:
                self.drag = ["tl", pos.x(), self.t]
            return
        x, y = h[1]
        if right:                                       # правая кнопка по объекту — удалить (как в osu!)
            o = self._pick(L, x, y)
            if o is not None:
                self.sel = [o]
                self.delete_sel()
            return
        if self.tool == "circle":
            self.place_circle(x, y)
        elif self.tool == "slider":
            self.add_slider_point(x, y)
        elif self.tool == "spinner":
            self.place_spinner()
        else:
            o = self._pick(L, x, y)
            shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            if o is not None:
                if shift:
                    self.sel = [q for q in self.sel if q is not o] if o in self.sel else self.sel + [o]
                elif o not in self.sel:
                    self.sel = [o]
                for q in self.sel:
                    q["_x0"], q["_y0"] = q["x"], q["y"]
                self.drag = ["move", x, y, False]
            else:
                if not shift:
                    self.sel = []
                self.drag = ["rect", pos, shift, pos]
        self.update()

    def mouseDoubleClickEvent(self, e):
        if self.tool == "slider" and len(self.slider_pts) >= 2:
            self.finish_slider()
            return
        super().mouseDoubleClickEvent(e)

    def mouseReleaseEvent(self, e):
        d, self.drag = self.drag, None
        if d is None:
            return
        if d[0] == "move" and d[3]:
            self._renumber()
        elif d[0] == "rect":
            a, b = d[1], d[3]
            if (a - b).manhattanLength() > 6:
                L = self._layout()
                x0, y0 = self._osu(L, min(a.x(), b.x()), min(a.y(), b.y()))
                x1, y1 = self._osu(L, max(a.x(), b.x()), max(a.y(), b.y()))
                pre = G.preempt(float(self.m.get("ar", 8)))
                got = [o for o in self.objs if o["k"] != 2 and o["t"] - pre <= self.t <= self._end(o) + 300
                       and x0 <= o["x"] <= x1 and y0 <= o["y"] <= y1]
                self.sel = (self.sel + [o for o in got if o not in self.sel]) if d[2] else got
        self.update()

    def wheelEvent(self, e):
        dy = e.angleDelta().y()
        if not dy:
            return
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom = max(0.25, min(8.0, self.zoom * (1.2 if dy > 0 else 1 / 1.2)))
        else:
            if self.playing:
                self._toggle_play()
            self._step(-1 if dy > 0 else 1)
        self.update()

    def keyPressEvent(self, e):
        k = e.key()
        mod = e.modifiers()
        ctrl = bool(mod & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mod & Qt.KeyboardModifier.ShiftModifier)
        name = key_from_event(e)                         # буквы — по месту на клавиатуре (и в русской раскладке)
        if ctrl and name == "Z":
            self.undo()
        elif ctrl and name == "Y":
            self.redo()
        elif ctrl and name == "S":
            self.save()
        elif ctrl and name == "A":
            pre = G.preempt(float(self.m.get("ar", 8)))
            self.sel = [o for o in self.objs if o["t"] - pre <= self.t <= self._end(o) + 300]
        elif k == Qt.Key.Key_Escape:
            self.exit()
        elif k == Qt.Key.Key_F5:
            self.test(auto=shift)
        elif k == Qt.Key.Key_Space:
            self._toggle_play()
        elif k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.slider_pts:
                self.finish_slider()
        elif k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_sel()
        elif k == Qt.Key.Key_Left:
            self._step(-4 if shift else -1)
        elif k == Qt.Key.Key_Right:
            self._step(4 if shift else 1)
        elif k == Qt.Key.Key_PageUp:
            self._step(-4 * self.div)
        elif k == Qt.Key.Key_PageDown:
            self._step(4 * self.div)
        elif k == Qt.Key.Key_Home:
            self._seek(self.objs[0]["t"] if self.objs else 0)
        elif k == Qt.Key.Key_End:
            self._seek(self._end(self.objs[-1]) if self.objs else 0)
        elif name in ("1", "2", "3", "4"):
            self.tool = TOOLS[int(name) - 1][0]
            self.slider_pts = []
        elif name == "Q":
            self.toggle_nc()
        elif name == "W":
            self.toggle_hs(2)
        elif name == "E":
            self.toggle_hs(4)
        elif name == "R":
            self.toggle_hs(8)
        elif name == "G":
            self.grid = GRIDS[(GRIDS.index(self.grid) + 1) % len(GRIDS)]
        elif name == "BracketLeft":
            self._press_button(("div", -1))
        elif name == "BracketRight":
            self._press_button(("div", 1))
        elif name == "Comma":
            self.change_length(-1)
        elif name == "Period":
            self.change_length(1)
        self.update()
