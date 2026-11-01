# daw_fx.py
"""
Эффекты микшера студии (тема «Echoes Studio»). Каждый эффект — потоковый процессор: process(x)
получает блок (n, 2) float32 и возвращает обработанный блок той же длины, состояние (хвосты,
фильтры, огибающие) живёт между блоками. Один и тот же код работает и при воспроизведении,
и при экспорте в файл — звучит одинаково.

Параметры эффекта — словарь из проекта (общий с окном плагина): покрутил ручку — со следующего
блока слышно. Задержку (у лимитера, автотюна, питча, робота) движок компенсирует сам —
читает дорожку раньше на эту задержку.
"""
from __future__ import annotations

import math
import threading

import numpy as np
from scipy import signal as sps
from scipy.ndimage import minimum_filter1d

import daw_dsp as D
from daw_dsp import SR

BLOCK = 1024


class Ctx:
    """Общее для всех эффектов: темп, позиция блока в долях, идёт ли воспроизведение."""

    def __init__(self, bpm=130.0):
        self.bpm = float(bpm)
        self.beat = 0.0
        self.playing = False

    def spb(self) -> float:
        return SR * 60.0 / max(20.0, self.bpm)


# ------------------------------------------------------------------ #
#  Параметры                                                          #
# ------------------------------------------------------------------ #

class P:
    """Описание параметра: ключ, имя, диапазон, по умолчанию, единицы, шкала."""

    def __init__(self, key, name, lo=0.0, hi=1.0, default=0.0, unit="", kind="lin", choices=None, hint=""):
        self.key, self.name, self.lo, self.hi, self.default = key, name, lo, hi, default
        self.unit, self.kind, self.choices, self.hint = unit, kind, choices, hint
        if kind == "choice":
            self.lo, self.hi = 0, len(choices) - 1

    def norm(self, v) -> float:
        try:
            v = float(v)
        except (TypeError, ValueError):
            v = float(self.default)
        if self.kind == "log":
            lo, hi = math.log(max(1e-9, self.lo)), math.log(max(1e-9, self.hi))
            return min(1.0, max(0.0, (math.log(max(1e-9, v)) - lo) / (hi - lo)))
        return min(1.0, max(0.0, (v - self.lo) / ((self.hi - self.lo) or 1)))

    def value(self, t: float):
        t = min(1.0, max(0.0, float(t)))
        if self.kind == "log":
            lo, hi = math.log(max(1e-9, self.lo)), math.log(max(1e-9, self.hi))
            return math.exp(lo + (hi - lo) * t)
        v = self.lo + (self.hi - self.lo) * t
        if self.kind in ("int", "choice", "bool"):
            return int(round(v))
        return v

    def fmt(self, v) -> str:
        if self.kind == "choice":
            try:
                return str(self.choices[int(v)])
            except (IndexError, ValueError, TypeError):
                return "?"
        if self.kind == "bool":
            return "вкл" if v else "выкл"
        try:
            v = float(v)
        except (TypeError, ValueError):
            return str(v)
        u = self.unit
        if u == "Hz":
            return f"{v / 1000:.2f} кГц" if v >= 1000 else f"{v:.0f} Гц"
        if u == "dB":
            return f"{v:+.1f} дБ"
        if u == "ms":
            return f"{v:.0f} мс" if v >= 10 else f"{v:.1f} мс"
        if u == "%":
            return f"{v * 100:.0f} %"
        if u == "st":
            return f"{v:+.0f} пт" if abs(v - round(v)) < 1e-6 else f"{v:+.2f} пт"
        if u == "x":
            return f"{v:.1f}:1"
        if self.kind == "int":
            return f"{int(round(v))}{(' ' + u) if u else ''}"
        return f"{v:.2f}{(' ' + u) if u else ''}"


SYNC = ["1/16", "1/8", "1/8.", "1/4", "1/4.", "1/2", "1/1", "2/1"]
SYNC_BEATS = [0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 4.0, 8.0]


class FX:
    TYPE = "base"
    NAME = ""
    GROUP = ""
    INFO = ""
    PARAMS: list = []
    LATENCY = 0

    def __init__(self, params: dict | None = None, ctx: Ctx | None = None):
        self.p = params if params is not None else {}
        for s in self.PARAMS:
            self.p.setdefault(s.key, s.default)
        self.ctx = ctx or Ctx()
        self._key = None
        self.meter = 0.0                              # для окна плагина (подавление и т.п.)
        self.reset()

    @classmethod
    def spec(cls, key):
        return next((s for s in cls.PARAMS if s.key == key), None)

    def latency(self) -> int:
        return self.LATENCY

    def reset(self):
        pass

    def prepare(self):
        """Тяжёлая подготовка (импульс ревербератора) — заранее, не в потоке звука."""

    def g(self, k):
        v = self.p.get(k)
        if v is None:
            s = self.spec(k)
            return s.default if s else 0.0
        return v

    def _changed(self) -> bool:
        k = tuple(self.p.get(s.key) for s in self.PARAMS)
        if k != self._key:
            self._key = k
            return True
        return False

    def process(self, x: np.ndarray) -> np.ndarray:
        return x


def _mix(dry, wet, m):
    if m >= 0.999:
        return wet
    if m <= 0.001:
        return dry
    return dry * (1.0 - m) + wet * m


# ------------------------------------------------------------------ #
#  Эквалайзер                                                         #
# ------------------------------------------------------------------ #

EQ_BANDS = [("lowcut", 20.0), ("lowshelf", 90.0), ("peak", 250.0), ("peak", 900.0), ("peak", 2800.0),
            ("highshelf", 7500.0), ("highcut", 20000.0)]
EQ_NAMES = ["Срез низа", "Низы", "Низкая середина", "Середина", "Верхняя середина", "Верха", "Срез верха"]


def _eq_params():
    ps = []
    for i, (kind, f) in enumerate(EQ_BANDS, 1):
        ps.append(P(f"f{i}", f"{EQ_NAMES[i - 1]}: частота", 20, 20000, f, "Hz", "log"))
        if kind not in ("lowcut", "highcut"):
            ps.append(P(f"g{i}", f"{EQ_NAMES[i - 1]}: усиление", -18, 18, 0.0, "dB"))
            ps.append(P(f"q{i}", f"{EQ_NAMES[i - 1]}: ширина (Q)", 0.2, 8, 0.9 if kind == "peak" else 0.7, "", "log"))
    ps.append(P("slope", "Крутизна срезов", 0, 2, 1, "", "choice", ["12 дБ/окт", "24 дБ/окт", "48 дБ/окт"]))
    ps.append(P("out", "Выход", -24, 12, 0.0, "dB"))
    return ps


class EQ(FX):
    TYPE = "eq"
    NAME = "Параметрический EQ"
    GROUP = "Эквалайзеры и фильтры"
    INFO = "7 полос: срезы, полки и колокола. Точки на графике можно тянуть мышью, колесо — ширина."
    PARAMS = _eq_params()

    def reset(self):
        self.sos = None
        self.zi = None
        self.gain = 1.0

    def build(self):
        secs = []
        slope = [12, 24, 48][int(self.g("slope"))]
        for i, (kind, _f) in enumerate(EQ_BANDS, 1):
            f = float(self.g(f"f{i}"))
            if kind == "lowcut":
                if f > 21:
                    secs.append(D.cut("hp", f, slope))
            elif kind == "highcut":
                if f < 19800:
                    secs.append(D.cut("lp", f, slope))
            else:
                gdb = float(self.g(f"g{i}"))
                if abs(gdb) > 0.05:
                    secs.append(D.biquad(kind, f, float(self.g(f"q{i}")), gdb))
        return np.vstack(secs) if secs else None

    def response(self, freqs: np.ndarray) -> np.ndarray:
        """АЧХ в дБ на частотах freqs (для графика)."""
        sos = self.build()
        out = np.full(len(freqs), float(self.g("out")))
        if sos is None:
            return out
        _w, h = sps.sosfreqz(sos, worN=np.asarray(freqs), fs=SR)
        return out + 20 * np.log10(np.abs(h) + 1e-9)

    def process(self, x):
        if self._changed():
            sos = self.build()
            if sos is None or self.sos is None or sos.shape != self.sos.shape:
                self.zi = None if sos is None else np.zeros((sos.shape[0], 2, 2))
            self.sos = sos
            self.gain = D.undb(self.g("out"))
        y = x
        if self.sos is not None:
            y, self.zi = sps.sosfilt(self.sos, x, axis=0, zi=self.zi)
        return (y * self.gain).astype(np.float32)


# ------------------------------------------------------------------ #
#  Динамика                                                           #
# ------------------------------------------------------------------ #

_W = 32                                               # окно огибающей (сэмплов)


def _peaks(x, w=_W):
    a = np.abs(x).max(axis=1) if x.ndim == 2 else np.abs(x)
    n = len(a) // w
    if n * w < len(a):
        a = np.concatenate([a, np.zeros(w * (n + 1) - len(a), a.dtype)])
        n += 1
    return a.reshape(n, w).max(axis=1)


def _expand(gw, n, w=_W, prev=None):
    """Значения по окнам → по сэмплам (линейно между центрами окон)."""
    k = len(gw)
    xs = (np.arange(k) + 0.5) * w
    if prev is not None:
        xs = np.concatenate([[-0.5 * w], xs])
        gw = np.concatenate([[prev], gw])
    return np.interp(np.arange(n), xs, gw).astype(np.float32)


class Compressor(FX):
    TYPE = "comp"
    NAME = "Компрессор"
    GROUP = "Динамика"
    INFO = "Выравнивает громкость: всё, что громче порога, сжимается в «соотношение» раз."
    PARAMS = [P("thr", "Порог", -60, 0, -18, "dB"), P("ratio", "Соотношение", 1, 20, 4, "x", "log"),
              P("att", "Атака", 0.1, 100, 8, "ms", "log"), P("rel", "Спад", 10, 1500, 140, "ms", "log"),
              P("knee", "Мягкость колена", 0, 18, 6, "dB"), P("makeup", "Компенсация", -12, 24, 2, "dB"),
              P("mix", "Микс", 0, 1, 1, "%")]

    def reset(self):
        self.env = 0.0
        self.prev_g = 1.0

    def process(self, x):
        thr, ratio = float(self.g("thr")), max(1.0, float(self.g("ratio")))
        knee = float(self.g("knee"))
        aa = 1 - math.exp(-_W / (max(0.05, float(self.g("att"))) * SR / 1000))
        ar = 1 - math.exp(-_W / (max(1.0, float(self.g("rel"))) * SR / 1000))
        lv = 20 * np.log10(_peaks(x) + 1e-9)
        over = lv - thr
        slope = 1.0 / ratio - 1.0
        if knee > 0.01:
            gr = np.where(2 * over < -knee, 0.0,
                          np.where(2 * np.abs(over) <= knee, slope * (over + knee / 2) ** 2 / (2 * knee), slope * over))
        else:
            gr = np.where(over > 0, slope * over, 0.0)
        e = self.env
        out = np.empty(len(gr))
        for i, t in enumerate(gr):
            e += (t - e) * (aa if t < e else ar)
            out[i] = e
        self.env = e
        self.meter = float(out.min()) if len(out) else 0.0
        gw = 10 ** ((out + float(self.g("makeup"))) / 20)
        gs = _expand(gw, len(x), prev=self.prev_g)
        self.prev_g = float(gw[-1])
        y = x * gs[:, None]
        return _mix(x, y, float(self.g("mix"))).astype(np.float32)


class Limiter(FX):
    TYPE = "limiter"
    NAME = "Лимитер"
    GROUP = "Динамика"
    INFO = "Не даёт звуку перейти потолок: громко и без перегруза. Ставят последним на мастер."
    PARAMS = [P("gain", "Усиление на входе", 0, 24, 0, "dB"), P("ceil", "Потолок", -12, 0, -0.3, "dB"),
              P("rel", "Спад", 5, 800, 80, "ms", "log")]
    LATENCY = 64

    def reset(self):
        self.hist = np.zeros((self.LATENCY, 2), np.float32)
        self.gs = 1.0
        self.prev_g = 1.0

    def process(self, x):
        la = self.LATENCY
        gin = D.undb(self.g("gain"))
        ceil = D.undb(self.g("ceil"))
        xin = x * gin
        buf = np.concatenate([self.hist, xin])
        a = np.abs(buf).max(axis=1)
        req = np.minimum(1.0, ceil / np.maximum(a, 1e-9))
        # для выхода j (= buf[j]) — минимум требований на [j, j+la]
        gmin = minimum_filter1d(req, size=la + 1, origin=-(la // 2), mode="nearest")[: len(x)]
        ar = 1 - math.exp(-_W / (max(1.0, float(self.g("rel"))) * SR / 1000))
        nw = len(gmin) // _W
        gw = gmin[: nw * _W].reshape(nw, _W).min(axis=1) if nw else np.zeros(0)
        g = self.gs
        sm = np.empty(nw)
        for i, t in enumerate(gw):
            g = t if t < g else g + (t - g) * ar
            sm[i] = g
        self.gs = g
        self.meter = 20 * math.log10(max(1e-6, float(sm.min()) if nw else 1.0))
        gs = np.minimum(_expand(sm, len(x), prev=self.prev_g), gmin) if nw else gmin
        self.prev_g = float(sm[-1]) if nw else self.prev_g
        y = buf[: len(x)] * gs[:, None]
        self.hist = buf[len(x):].copy()
        return np.clip(y, -ceil, ceil).astype(np.float32)


class Gate(FX):
    TYPE = "gate"
    NAME = "Гейт"
    GROUP = "Динамика"
    INFO = "Глушит всё тише порога: убирает шум между фразами и хвосты."
    PARAMS = [P("thr", "Порог", -80, 0, -42, "dB"), P("att", "Атака", 0.1, 50, 1, "ms", "log"),
              P("hold", "Удержание", 0, 500, 40, "ms"), P("rel", "Спад", 5, 2000, 160, "ms", "log"),
              P("range", "Глубина", -80, 0, -60, "dB")]

    def reset(self):
        self.g_ = 0.0
        self.hold_left = 0
        self.prev_g = 1.0

    def process(self, x):
        thr = float(self.g("thr"))
        floor = D.undb(self.g("range"))
        aa = 1 - math.exp(-_W / (max(0.05, float(self.g("att"))) * SR / 1000))
        ar = 1 - math.exp(-_W / (max(1.0, float(self.g("rel"))) * SR / 1000))
        hold = int(float(self.g("hold")) * SR / 1000 / _W)
        lv = 20 * np.log10(_peaks(x) + 1e-9)
        g = self.g_
        out = np.empty(len(lv))
        for i, v in enumerate(lv):
            if v > thr:
                self.hold_left = hold
                g += (1.0 - g) * aa
            elif self.hold_left > 0:
                self.hold_left -= 1
            else:
                g += (0.0 - g) * ar
            out[i] = g
        self.g_ = g
        gw = floor + (1 - floor) * out
        gs = _expand(gw, len(x), prev=self.prev_g)
        self.prev_g = float(gw[-1])
        return (x * gs[:, None]).astype(np.float32)


class DeEsser(FX):
    TYPE = "deesser"
    NAME = "Де-эссер"
    GROUP = "Динамика"
    INFO = "Приглушает резкие «с», «ш», «ц» в голосе."
    PARAMS = [P("freq", "Частота", 2500, 12000, 6500, "Hz", "log"), P("thr", "Порог", -60, 0, -28, "dB"),
              P("range", "Глубина", 0, 24, 9, "dB")]

    def reset(self):
        self.sos = None
        self.zi = None
        self.env = 0.0
        self.prev_g = 1.0

    def process(self, x):
        if self._changed() or self.sos is None:
            self.sos = D.biquad("hp", float(self.g("freq")), 0.7)
            if self.zi is None:
                self.zi = np.zeros((1, 2, 2))
        hb, self.zi = sps.sosfilt(self.sos, x, axis=0, zi=self.zi)
        lb = x - hb
        lv = 20 * np.log10(_peaks(hb) + 1e-9)
        thr, rng = float(self.g("thr")), float(self.g("range"))
        tgt = -np.clip((lv - thr) * 0.8, 0, rng)
        aa, ar = 1 - math.exp(-_W / (0.5 * SR / 1000)), 1 - math.exp(-_W / (60 * SR / 1000))
        e = self.env
        out = np.empty(len(tgt))
        for i, t in enumerate(tgt):
            e += (t - e) * (aa if t < e else ar)
            out[i] = e
        self.env = e
        self.meter = float(out.min()) if len(out) else 0.0
        gw = 10 ** (out / 20)
        gs = _expand(gw, len(x), prev=self.prev_g)
        self.prev_g = float(gw[-1])
        return (lb + hb * gs[:, None]).astype(np.float32)


class Pump(FX):
    TYPE = "pump"
    NAME = "Сайдчейн-качка"
    GROUP = "Динамика"
    INFO = "Громкость «качается» в такт, как будто бочка давит остальное — танцевальный сайдчейн."
    PARAMS = [P("rate", "Шаг", 0, 3, 1, "", "choice", ["1/8", "1/4", "1/2", "1/1"]),
              P("depth", "Глубина", 0, 1, 0.75, "%"), P("curve", "Восстановление", 0.05, 1, 0.45, "%"),
              P("offset", "Сдвиг", 0, 1, 0.0, "%")]

    def reset(self):
        self.free = 0.0

    def process(self, x):
        n = len(x)
        rb = [0.5, 1.0, 2.0, 4.0][int(self.g("rate"))]
        spb = self.ctx.spb()
        if self.ctx.playing:
            b0 = self.ctx.beat
        else:
            b0 = self.free
            self.free += n / spb
        b = b0 + np.arange(n) / spb
        ph = np.mod(b / rb + float(self.g("offset")), 1.0)
        c = max(0.02, float(self.g("curve")))
        k = np.clip(ph / c, 0, 1)
        gain = 1 - float(self.g("depth")) * (1 - k) ** 2
        self.meter = float(20 * np.log10(max(1e-6, gain.min())))
        return (x * gain[:, None].astype(np.float32)).astype(np.float32)


# ------------------------------------------------------------------ #
#  Пространство                                                       #
# ------------------------------------------------------------------ #

def make_ir(size, damp, predelay_ms, width, locut, hicut, seed=7) -> np.ndarray:
    """Импульс помещения (n, 2): ранние отражения + затухающий шум, верха гаснут быстрее."""
    rng = np.random.default_rng(seed)
    t60 = 0.25 + (size ** 1.6) * 7.0
    n = int(min(8.0, predelay_ms / 1000 + t60 * 0.95) * SR)
    t = np.arange(n) / SR
    out = np.zeros((n, 2), np.float32)
    bands = [(None, 400), (400, 2000), (2000, 6000), (6000, None)]
    decays = [1.0, 1.0 - 0.25 * damp, 1.0 - 0.55 * damp, 1.0 - 0.8 * damp]
    for (lo, hi), dk in zip(bands, decays):
        tb = max(0.08, t60 * dk)
        env = np.exp(-6.91 * t / tb).astype(np.float32)
        for c in range(2):
            nz = rng.standard_normal(n).astype(np.float32)
            if lo is None:
                sos = D.biquad("lp", hi, 0.7)
            elif hi is None:
                sos = D.biquad("hp", lo, 0.7)
            else:
                sos = np.vstack([D.biquad("hp", lo, 0.7), D.biquad("lp", hi, 0.7)])
            out[:, c] += sps.sosfilt(sos, nz).astype(np.float32) * env
    att = np.clip(t / (0.012 + 0.04 * size), 0, 1).astype(np.float32)
    out *= att[:, None]
    for _ in range(10):
        d = rng.uniform(0.004, 0.02 + 0.07 * size)
        i = int(d * SR)
        if i < n:
            gL, gR = rng.uniform(0.2, 0.7), rng.uniform(0.2, 0.7)
            out[i, 0] += gL * (1 - d * 6)
            out[i, 1] += gR * (1 - d * 6)
    m = 0.5 * (out[:, 0] + out[:, 1])
    s = 0.5 * (out[:, 0] - out[:, 1]) * width
    out = np.stack([m + s, m - s], 1)
    sos = []
    if locut > 21:
        sos.append(D.biquad("hp", locut, 0.7))
    if hicut < 19800:
        sos.append(D.biquad("lp", hicut, 0.7))
    if sos:
        out = sps.sosfilt(np.vstack(sos), out, axis=0).astype(np.float32)
    pd = int(predelay_ms / 1000 * SR)
    if pd > 0:
        out = np.concatenate([np.zeros((pd, 2), np.float32), out])
    e = float(np.sqrt((out ** 2).sum() / 2))
    return (out / max(1e-9, e) * 0.9).astype(np.float32)


class Reverb(FX):
    TYPE = "reverb"
    NAME = "Ревербератор"
    GROUP = "Пространство"
    INFO = "Звук в помещении: от маленькой комнаты до собора."
    PARAMS = [P("size", "Размер", 0, 1, 0.45, "%"), P("damp", "Глушение верхов", 0, 1, 0.5, "%"),
              P("pre", "Пред-задержка", 0, 200, 15, "ms"), P("width", "Ширина", 0, 1.5, 1.0, "%"),
              P("locut", "Срез низа", 20, 1000, 180, "Hz", "log"), P("hicut", "Срез верха", 1000, 20000, 9000, "Hz", "log"),
              P("wet", "Эффект", 0, 1, 0.22, "%"), P("dry", "Чистый звук", 0, 1, 1.0, "%")]

    def reset(self):
        self.parts = None
        self.fdl = None
        self.prev = None
        self.ir = None
        self._ir_key = None
        self._building = False
        self._pending = None

    def prepare(self):
        self._ensure(BLOCK, now=True)

    def _ir_params(self):
        return (round(float(self.g("size")), 3), round(float(self.g("damp")), 3), round(float(self.g("pre")), 1),
                round(float(self.g("width")), 3), round(float(self.g("locut"))), round(float(self.g("hicut"))))

    def _make(self, key, P_):
        ir = make_ir(*key)
        K = (len(ir) + P_ - 1) // P_
        pad = np.zeros((K * P_, 2), np.float32)
        pad[:len(ir)] = ir
        parts = np.stack([np.fft.rfft(pad[k * P_:(k + 1) * P_], 2 * P_, axis=0) for k in range(K)]).astype(np.complex64)
        return ir, parts

    def _ensure(self, n, now=False):
        key = self._ir_params()
        if key == self._ir_key and self.parts is not None and self.parts.shape[1] == n + 1:
            return
        if self.parts is None or now or self.parts.shape[1] != n + 1:
            ir, parts = self._make(key, n)
            self._apply(key, ir, parts, n)
            return
        if not self._building:                            # перестроить в фоне, пока звучит старый
            self._building = True

            def job(k=key, nn=n):
                try:
                    self._pending = (k, *self._make(k, nn), nn)
                finally:
                    self._building = False
            threading.Thread(target=job, daemon=True, name="studio-ir").start()

    def _apply(self, key, ir, parts, n):
        self.ir, self.parts, self._ir_key = ir, parts, key
        K = parts.shape[0]
        if self.fdl is None or self.fdl.shape[0] != K or self.fdl.shape[1] != n + 1:
            self.fdl = np.zeros((K, n + 1), np.complex64)
            self.prev = np.zeros(n, np.float32)

    def process(self, x):
        n = len(x)
        if self._pending is not None:
            k, ir, parts, nn = self._pending
            self._pending = None
            if nn == n:
                self._apply(k, ir, parts, n)
        self._ensure(n)
        m = x.mean(axis=1)
        X = np.fft.rfft(np.concatenate([self.prev, m]))
        self.prev = m.astype(np.float32)
        self.fdl = np.roll(self.fdl, 1, axis=0)
        self.fdl[0] = X
        Y = np.einsum("kb,kbc->bc", self.fdl, self.parts)
        wet = np.fft.irfft(Y, 2 * n, axis=0)[n:].astype(np.float32)
        return (x * float(self.g("dry")) + wet * float(self.g("wet"))).astype(np.float32)


class Delay(FX):
    TYPE = "delay"
    NAME = "Дилей (эхо)"
    GROUP = "Пространство"
    INFO = "Эхо в такт песне. Пинг-понг — повторы прыгают слева направо."
    PARAMS = [P("time", "Время", 0, len(SYNC), 3, "", "choice", SYNC + ["мс"]),
              P("ms", "Время в мс", 10, 2000, 300, "ms", "log"), P("fb", "Повторы", 0, 0.95, 0.4, "%"),
              P("pp", "Пинг-понг", 0, 1, 1, "", "bool"), P("damp", "Тон повторов", 500, 20000, 5000, "Hz", "log"),
              P("mix", "Эффект", 0, 1, 0.28, "%")]
    MAXS = 4 * SR

    def reset(self):
        self.ring = np.zeros((self.MAXS, 2), np.float32)
        self.w = 0
        self.zi = np.zeros((1, 2, 2))
        self.sos = None

    def _d(self):
        t = int(self.g("time"))
        if t >= len(SYNC):
            s = float(self.g("ms")) / 1000 * SR
        else:
            s = SYNC_BEATS[t] * self.ctx.spb()
        return int(min(self.MAXS - BLOCK * 2, max(64, s)))

    def process(self, x):
        if self._changed() or self.sos is None:
            self.sos = D.biquad("lp", float(self.g("damp")), 0.6)
        Dd = self._d()
        fb = float(self.g("fb"))
        pp = bool(self.g("pp"))
        n = len(x)
        wet = np.empty_like(x)
        pos = 0
        while pos < n:
            m = min(n - pos, Dd)
            ri = (self.w - Dd + np.arange(m)) % self.MAXS
            rd = self.ring[ri]
            rd, self.zi = sps.sosfilt(self.sos, rd, axis=0, zi=self.zi)
            xi = x[pos:pos + m]
            if pp:
                mono = xi.mean(axis=1)
                line = np.stack([mono + fb * rd[:, 1], fb * rd[:, 0]], 1)
            else:
                line = xi + fb * rd
            wi = (self.w + np.arange(m)) % self.MAXS
            self.ring[wi] = line
            self.w = (self.w + m) % self.MAXS
            wet[pos:pos + m] = rd
            pos += m
        return (x + wet * float(self.g("mix"))).astype(np.float32)


class _ModDelay:
    """Линия задержки с модуляцией (лоу-фай): чтение с дробным сдвигом."""

    def __init__(self, maxs):
        self.maxs = maxs
        self.hist = np.zeros((maxs, 2), np.float32)

    def read(self, x, delays):
        n = len(x)
        buf = np.concatenate([self.hist, x])
        base = self.maxs + np.arange(n)
        dl = delays if delays.ndim == 2 else np.repeat(delays[:, None], 2, 1)
        p = base[:, None] - dl
        i0 = np.floor(p).astype(np.int64)
        fr = (p - i0).astype(np.float32)
        i0 = np.clip(i0, 0, len(buf) - 2)
        c = np.arange(2)[None, :]
        y = buf[i0, c] * (1 - fr) + buf[i0 + 1, c] * fr
        self.hist = buf[-self.maxs:].copy()
        return y.astype(np.float32)


class Chorus(FX):
    TYPE = "chorus"
    NAME = "Хорус / даблер"
    GROUP = "Модуляция"
    INFO = "Несколько слегка расстроенных копий — звук шире и «гуще». Для голоса — эффект дабла."
    PARAMS = [P("rate", "Скорость", 0.05, 6, 0.7, "Hz", "log"), P("depth", "Глубина", 0, 10, 2.5, "ms"),
              P("delay", "Задержка", 4, 40, 14, "ms"), P("voices", "Голоса", 1, 4, 2, "", "int"),
              P("spread", "Ширина", 0, 1, 0.85, "%"), P("mix", "Эффект", 0, 1, 0.45, "%")]

    def reset(self):
        self.maxs = int(0.06 * SR)
        self.hist = np.zeros((self.maxs, 2), np.float32)
        self.ph = 0.0

    def process(self, x):
        n = len(x)
        rate = float(self.g("rate"))
        dep = float(self.g("depth")) / 1000 * SR
        base = float(self.g("delay")) / 1000 * SR
        vo = max(1, int(self.g("voices")))
        sp = float(self.g("spread"))
        t = self.ph + np.arange(n) * rate / SR
        self.ph = float((self.ph + n * rate / SR) % 1.0)
        buf = np.concatenate([self.hist, x])
        self.hist = buf[-self.maxs:].copy()
        pos = self.maxs + np.arange(n)
        c = np.arange(2)[None, :]
        wet = np.zeros_like(x)
        for v in range(vo):
            dl = np.stack([base + dep * 0.5 * (1 + np.sin(2 * np.pi * (t + v / vo))),
                           base + dep * 0.5 * (1 + np.sin(2 * np.pi * (t + v / vo + 0.25)))], 1)
            p = pos[:, None] - np.clip(dl, 1, self.maxs - 2)
            i0 = np.floor(p).astype(np.int64)
            fr = (p - i0).astype(np.float32)
            y = buf[i0, c] * (1 - fr) + buf[i0 + 1, c] * fr
            pan = (v / max(1, vo - 1) * 2 - 1) * sp if vo > 1 else 0.0
            wet[:, 0] += y[:, 0] * min(1, 1 - pan)
            wet[:, 1] += y[:, 1] * min(1, 1 + pan)
        wet /= vo
        m = float(self.g("mix"))
        return (x * (1 - 0.5 * m) + wet * m).astype(np.float32)


class Flanger(FX):
    TYPE = "flanger"
    NAME = "Флэнджер"
    GROUP = "Модуляция"
    INFO = "«Самолётный» свист: очень короткая задержка с обратной связью."
    PARAMS = [P("rate", "Скорость", 0.02, 5, 0.25, "Hz", "log"), P("depth", "Глубина", 0, 6, 2.0, "ms"),
              P("delay", "Задержка", 0.8, 10, 1.2, "ms"), P("fb", "Обратная связь", -0.95, 0.95, 0.55, "%"),
              P("mix", "Эффект", 0, 1, 0.5, "%")]
    SUB = 32

    def reset(self):
        self.maxs = int(0.03 * SR)
        self.line = np.zeros((self.maxs, 2), np.float32)
        self.ph = 0.0

    def process(self, x):
        n = len(x)
        rate = float(self.g("rate"))
        dep = float(self.g("depth")) / 1000 * SR
        base = max(self.SUB + 1.0, float(self.g("delay")) / 1000 * SR)
        fb = float(self.g("fb"))
        out = np.empty_like(x)
        c = np.arange(2)[None, :]
        for s in range(0, n, self.SUB):
            xi = x[s:s + self.SUB]
            m = len(xi)
            t = self.ph + (np.arange(m) + s) * rate / SR
            d = base + dep * 0.5 * (1 + np.sin(2 * np.pi * t))
            p = self.maxs + np.arange(m) - d
            i0 = np.floor(p).astype(np.int64)
            fr = (p - i0).astype(np.float32)[:, None]
            i0 = np.clip(i0, 0, self.maxs - 2)[:, None]
            rd = self.line[i0, c] * (1 - fr) + self.line[i0 + 1, c] * fr
            ln = xi + fb * rd
            self.line = np.concatenate([self.line[m:], ln.astype(np.float32)])
            out[s:s + m] = rd
        self.ph = float((self.ph + n * rate / SR) % 1.0)
        mm = float(self.g("mix"))
        return (x * (1 - 0.5 * mm) + out * mm).astype(np.float32)


class Phaser(FX):
    TYPE = "phaser"
    NAME = "Фейзер"
    GROUP = "Модуляция"
    INFO = "Плывущие провалы по частотам — «космический» перелив."
    PARAMS = [P("rate", "Скорость", 0.02, 5, 0.35, "Hz", "log"), P("depth", "Глубина", 0, 1, 0.8, "%"),
              P("freq", "Центр", 150, 5000, 900, "Hz", "log"), P("stages", "Стадии", 1, 6, 3, "", "int"),
              P("res", "Резонанс", 0.3, 4, 0.8, "", "log"), P("mix", "Эффект", 0, 1, 0.5, "%")]
    SUB = 64

    def reset(self):
        self.zi = None
        self.ph = 0.0

    def process(self, x):
        n = len(x)
        st = max(1, int(self.g("stages")))
        if self.zi is None or self.zi.shape[0] != st:
            self.zi = np.zeros((st, 2, 2))
        rate, dep = float(self.g("rate")), float(self.g("depth"))
        fc, q = float(self.g("freq")), float(self.g("res"))
        out = np.empty_like(x)
        for s in range(0, n, self.SUB):
            xi = x[s:s + self.SUB]
            t = self.ph + (s + len(xi) / 2) * rate / SR
            f = fc * 2 ** (dep * 2.2 * math.sin(2 * math.pi * t))
            sos = np.vstack([D.biquad("ap", f * (1.6 ** k), q) for k in range(st)])
            y, self.zi = sps.sosfilt(sos, xi, axis=0, zi=self.zi)
            out[s:s + len(xi)] = y
        self.ph = float((self.ph + n * rate / SR) % 1.0)
        m = float(self.g("mix"))
        return (x * (1 - m) + 0.5 * (x + out) * m).astype(np.float32)        # сумма с фазосдвинутым — провалы


# ------------------------------------------------------------------ #
#  Фильтр и искажения                                                 #
# ------------------------------------------------------------------ #

class Filter(FX):
    TYPE = "filter"
    NAME = "Фильтр"
    GROUP = "Эквалайзеры и фильтры"
    INFO = "Срезает низ или верх; с качанием в такт — классический «фильтр-свип»."
    PARAMS = [P("type", "Тип", 0, 3, 0, "", "choice", ["Низких частот (LP)", "Высоких частот (HP)",
                                                         "Полосовой (BP)", "Режекторный"]),
              P("cut", "Частота среза", 20, 20000, 2500, "Hz", "log"), P("res", "Резонанс", 0.5, 12, 0.9, "", "log"),
              P("lfo", "Качание", 0, 8, 0, "", "choice", ["выкл"] + SYNC[1:] + ["4/1"]),
              P("lfod", "Глубина качания", 0, 5, 2, "", "lin"), P("mix", "Микс", 0, 1, 1, "%")]
    SUB = 64

    def reset(self):
        self.zi = np.zeros((1, 2, 2))
        self.sos = None

    def process(self, x):
        kind = ["lp", "hp", "bp", "notch"][int(self.g("type"))]
        cut, q = float(self.g("cut")), float(self.g("res"))
        lfo = int(self.g("lfo"))
        m = float(self.g("mix"))
        if lfo == 0:
            if self._changed() or self.sos is None:
                self.sos = D.biquad(kind, cut, q)
            y, self.zi = sps.sosfilt(self.sos, x, axis=0, zi=self.zi)
            return _mix(x, y, m).astype(np.float32)
        beats = (SYNC_BEATS[1:] + [16.0])[lfo - 1]
        dep = float(self.g("lfod"))
        spb = self.ctx.spb()
        out = np.empty_like(x)
        for s in range(0, len(x), self.SUB):
            xi = x[s:s + self.SUB]
            b = self.ctx.beat + (s + len(xi) / 2) / spb
            sn = math.sin(2 * math.pi * b / beats)
            if kind == "lp":
                f = cut * 2 ** (dep * 0.5 * (sn - 1))
            elif kind == "hp":
                f = cut * 2 ** (dep * 0.5 * (sn + 1))
            else:
                f = cut * 2 ** (dep * 0.5 * sn)
            y, self.zi = sps.sosfilt(D.biquad(kind, f, q), xi, axis=0, zi=self.zi)
            out[s:s + len(xi)] = y
        return _mix(x, out, m).astype(np.float32)


class Distortion(FX):
    TYPE = "dist"
    NAME = "Дисторшн / сатуратор"
    GROUP = "Искажения"
    INFO = "От тёплого лампового перегруза до злого фузза."
    PARAMS = [P("type", "Характер", 0, 5, 0, "", "choice", ["Мягкий (лампа)", "Жёсткий клиппинг", "Фузз",
                                                              "Транзистор", "Складка (wavefold)", "Выпрямитель"]),
              P("drive", "Перегруз", 0, 40, 10, "dB"), P("tone", "Тон", 500, 20000, 9000, "Hz", "log"),
              P("out", "Выход", -30, 12, -6, "dB"), P("mix", "Микс", 0, 1, 1, "%")]

    def reset(self):
        self.zi = np.zeros((1, 2, 2))
        self.sos = None

    def process(self, x):
        if self._changed() or self.sos is None:
            self.sos = D.biquad("lp", float(self.g("tone")), 0.7)
        k = D.undb(self.g("drive"))
        t = int(self.g("type"))
        v = x * k
        if t == 0:
            y = np.tanh(v)
        elif t == 1:
            y = np.clip(v, -1, 1)
        elif t == 2:
            y = np.sign(v) * (1 - np.exp(-np.abs(v) * 1.5)) + 0.15 * np.tanh(v * 3) * (v > 0)
        elif t == 3:
            y = v / (1 + np.abs(v))
        elif t == 4:
            y = np.sin(np.clip(v, -20, 20) * 0.5 * np.pi)
        else:
            y = np.tanh(np.abs(v)) * 1.4 - 0.4
        y, self.zi = sps.sosfilt(self.sos, y, axis=0, zi=self.zi)
        y = y * D.undb(self.g("out"))
        return _mix(x, y, float(self.g("mix"))).astype(np.float32)


class Crusher(FX):
    TYPE = "crush"
    NAME = "Биткрашер"
    GROUP = "Искажения"
    INFO = "Звук старых приставок: меньше бит и частоты дискретизации."
    PARAMS = [P("bits", "Разрядность", 2, 16, 8, "бит", "int"), P("down", "Понижение частоты", 1, 40, 4, "x", "int"),
              P("mix", "Микс", 0, 1, 1, "%")]

    def reset(self):
        self.ph = 0
        self.last = np.zeros(2, np.float32)

    def process(self, x):
        n = len(x)
        dn = max(1, int(self.g("down")))
        q = 2 ** (max(2, int(self.g("bits"))) - 1)
        idx = (self.ph + np.arange(n)) // dn * dn - self.ph
        held = np.where((idx >= 0)[:, None], x[np.clip(idx, 0, n - 1)], self.last[None, :])
        self.ph = (self.ph + n) % dn
        self.last = held[-1].copy()
        y = np.round(held * q) / q
        return _mix(x, y, float(self.g("mix"))).astype(np.float32)


class LoFi(FX):
    TYPE = "lofi"
    NAME = "Лоу-фай (кассета)"
    GROUP = "Искажения"
    INFO = "Плавание плёнки, шипение, треск пластинки и мягкий завал верхов."
    PARAMS = [P("wow", "Плавание", 0, 1, 0.35, "%"), P("crackle", "Треск", 0, 1, 0.25, "%"),
              P("hiss", "Шипение", 0, 1, 0.15, "%"), P("tone", "Тон", 1500, 20000, 6000, "Hz", "log"),
              P("sat", "Насыщение", 0, 1, 0.3, "%"), P("mix", "Микс", 0, 1, 1, "%")]

    def reset(self):
        self.line = _ModDelay(int(0.03 * SR))
        self.ph = 0.0
        self.zi = np.zeros((1, 2, 2))
        self.sos = None
        self.rng = np.random.default_rng(5)

    def process(self, x):
        if self._changed() or self.sos is None:
            self.sos = D.biquad("lp", float(self.g("tone")), 0.6)
        n = len(x)
        w = float(self.g("wow"))
        t = self.ph + np.arange(n) / SR
        self.ph = float(self.ph + n / SR) % 1000.0
        d = 0.008 * SR + w * 0.0025 * SR * (np.sin(2 * np.pi * 0.55 * t) + 0.3 * np.sin(2 * np.pi * 7.3 * t))
        y = self.line.read(x, d)
        sat = float(self.g("sat"))
        if sat > 0.01:
            k = 1 + sat * 4
            y = np.tanh(y * k) / k * (1 + 0.25 * sat)               # тихое — как было, громкое мягко срезается
        y, self.zi = sps.sosfilt(self.sos, y, axis=0, zi=self.zi)
        hs = float(self.g("hiss"))
        if hs > 0.001:
            y = y + self.rng.standard_normal((n, 2)).astype(np.float32) * hs * 0.01
        cr = float(self.g("crackle"))
        if cr > 0.001:
            k = self.rng.random(n) < cr * 0.0015
            if k.any():
                imp = np.zeros(n, np.float32)
                imp[k] = self.rng.uniform(-0.5, 0.5, k.sum()) * cr
                y = y + imp[:, None]
        return _mix(x, y, float(self.g("mix"))).astype(np.float32)


class Stereo(FX):
    TYPE = "stereo"
    NAME = "Стерео-расширитель"
    GROUP = "Утилиты"
    INFO = "Шире или уже стерео, панорама, моно на басу."
    PARAMS = [P("width", "Ширина", 0, 2, 1.3, "%"), P("pan", "Панорама", -1, 1, 0, ""),
              P("monobass", "Моно ниже", 0, 400, 120, "Hz")]

    def reset(self):
        self.zi = np.zeros((1, 2))
        self.sos = None

    def process(self, x):
        mb = float(self.g("monobass"))
        if self._changed() or self.sos is None:
            self.sos = D.biquad("hp", max(20, mb), 0.7)
        m = 0.5 * (x[:, 0] + x[:, 1])
        s = 0.5 * (x[:, 0] - x[:, 1]) * float(self.g("width"))
        if mb > 21:
            s, self.zi = sps.sosfilt(self.sos, s, zi=self.zi)
        pan = float(self.g("pan"))
        gl, gr = min(1.0, 1 - pan), min(1.0, 1 + pan)
        return np.stack([(m + s) * gl, (m - s) * gr], 1).astype(np.float32)


class Utility(FX):
    TYPE = "gain"
    NAME = "Утилита (громкость)"
    GROUP = "Утилиты"
    INFO = "Громкость, панорама, моно, смена фазы."
    PARAMS = [P("gain", "Громкость", -48, 24, 0, "dB"), P("pan", "Панорама", -1, 1, 0, ""),
              P("mono", "Моно", 0, 1, 0, "", "bool"), P("inv", "Перевернуть фазу", 0, 1, 0, "", "bool")]

    def process(self, x):
        g = D.undb(self.g("gain")) * (-1 if self.g("inv") else 1)
        y = x * g
        if self.g("mono"):
            m = y.mean(axis=1, keepdims=True)
            y = np.repeat(m, 2, 1)
        pan = float(self.g("pan"))
        y = y * np.array([min(1.0, 1 - pan), min(1.0, 1 + pan)], np.float32)
        return y.astype(np.float32)


class Analyzer(FX):
    TYPE = "analyzer"
    NAME = "Анализатор (осциллограф и спектр)"
    GROUP = "Утилиты"
    INFO = "Ничего не меняет — показывает волну и спектр того, что проходит."
    PARAMS = []

    def reset(self):
        self.buf = np.zeros(4096, np.float32)

    def process(self, x):
        m = x.mean(axis=1)
        n = len(m)
        if n >= len(self.buf):
            self.buf = m[-len(self.buf):].copy()
        else:
            self.buf = np.concatenate([self.buf[n:], m])
        return x


# ------------------------------------------------------------------ #
#  Голос: сдвиг тона, автотюн, робот (потоковый спектр)                #
# ------------------------------------------------------------------ #

class _Spec:
    """Потоковый STFT: кадр 2048, шаг 512, окно Ханна при анализе и синтезе."""
    N = 2048
    H = 512

    def __init__(self):
        N = self.N
        self.win = D.hann(N)
        self.inb = np.zeros((N, 2), np.float32)
        self.fill = 0
        self.ola = np.zeros((N, 2), np.float32)
        self.q = [np.zeros((self.H, 2), np.float32)]          # предзаполнение — постоянный размер выхода
        self.norm = float((self.win * self.win).reshape(N // self.H, self.H).sum(0).mean())

    def run(self, x, frame):
        n = len(x)
        pos = 0
        H = self.H
        while pos < n:
            take = min(H - self.fill, n - pos)
            self.inb = np.concatenate([self.inb[take:], x[pos:pos + take]])
            self.fill += take
            pos += take
            if self.fill >= H:
                self.fill = 0
                X = np.fft.rfft(self.inb.T * self.win, axis=1)            # (2, bins)
                Y = frame(X, self.inb)
                y = np.fft.irfft(Y, self.N, axis=1).T * self.win[:, None] / self.norm
                self.ola += y.astype(np.float32)
                self.q.append(self.ola[:H].copy())
                self.ola = np.concatenate([self.ola[H:], np.zeros((H, 2), np.float32)])
        out = np.concatenate(self.q) if len(self.q) > 1 else self.q[0]
        res, rest = out[:n], out[n:]
        self.q = [rest] if len(rest) else [np.zeros((0, 2), np.float32)]
        if len(res) < n:
            res = np.concatenate([np.zeros((n - len(res), 2), np.float32), res])
        return res


class _Shifter:
    """Сдвиг тона по спектру методом Лароша — Долсона: каждый пик вместе со своей областью бинов
    переезжает целиком на новую частоту, фазы внутри области поворачиваются одинаково — соседние
    бины одной гармоники не гасят друг друга. Плюс сохранение формант (тембра)."""

    def __init__(self, N, H):
        self.N, self.H = N, H
        self.bins = N // 2 + 1
        self.k = np.arange(self.bins)
        self.omega = 2 * np.pi * self.k * H / N
        self.lifter = max(12, int(N * 0.012))
        self.last = np.zeros((2, self.bins))
        self.prev_pk = [np.zeros(0, np.int64), np.zeros(0, np.int64)]
        self.prev_th = [np.zeros(0), np.zeros(0)]

    def _one(self, X, ph, mag, c, ratio):
        bins = self.bins
        d = ph - self.last[c] - self.omega
        d = (d + np.pi) % (2 * np.pi) - np.pi
        true_bin = self.k + d * self.N / (2 * np.pi * self.H)
        m = mag
        pk = np.nonzero((m[2:-2] > m[1:-3]) & (m[2:-2] >= m[3:-1]) & (m[2:-2] > m[:-4]) & (m[2:-2] >= m[4:])
                        & (m[2:-2] > m.max() * 1e-4))[0] + 2
        if len(pk) == 0:
            self.prev_pk[c], self.prev_th[c] = pk, np.zeros(0)
            return np.zeros(bins, np.complex128)
        fp = true_bin[pk]
        dfp = fp * (ratio - 1.0)                            # сдвиг частоты пика (в бинах, дробный)
        shift = np.round(dfp).astype(np.int64)
        ppk, pth = self.prev_pk[c], self.prev_th[c]
        if len(ppk):
            j = np.clip(np.searchsorted(ppk, pk), 0, len(ppk) - 1)
            j0 = np.clip(j - 1, 0, len(ppk) - 1)
            jj = np.where(np.abs(ppk[j0] - pk) < np.abs(ppk[j] - pk), j0, j)
            th0 = pth[jj]
        else:
            th0 = np.zeros(len(pk))
        th = (th0 + 2 * np.pi * self.H * dfp / self.N) % (2 * np.pi)
        self.prev_pk[c], self.prev_th[c] = pk, th
        mids = (pk[:-1] + pk[1:]) // 2
        reg = np.searchsorted(mids, self.k, side="right")
        tgt = self.k + shift[reg]
        ok = (tgt >= 0) & (tgt < bins)
        Y = np.zeros(bins, np.complex128)
        np.add.at(Y, tgt[ok], X[ok] * np.exp(1j * th[reg[ok]]))
        return Y

    def shift(self, X, ratio, formant=True, fshift=1.0):
        mag = np.abs(X)
        ph = np.angle(X)
        fs_on = abs(fshift - 1) > 1e-4
        if abs(ratio - 1.0) < 1e-4 and not fs_on:
            self.last = ph
            for c in range(2):
                self.prev_th[c] = self.prev_th[c] * 0.0
            return X
        Y = np.stack([self._one(X[c], ph[c], mag[c], c, ratio) for c in range(2)])
        self.last = ph
        if formant or fs_on:
            # гармоника в бине k пришла из k/ratio и несёт огибающую env(k/ratio);
            # с сохранением тембра нужна env(k/fshift), без — огибающая едет вместе с тоном
            env = D.envelope(mag, self.N, self.lifter)
            src = D._interp_bins(env, self.k / max(1e-6, ratio))
            want = D._interp_bins(env, self.k / fshift) if formant else \
                D._interp_bins(env, self.k / max(1e-6, ratio * fshift))
            Y = Y * np.clip(want / np.maximum(src, 1e-7), 0.05, 20.0)
        return Y


class _SpecFX(FX):
    """Основа голосовых эффектов на потоковом спектре: задержка «сухого» сигнала на ту же
    величину и выравнивание громкости (обработка спектра не должна делать звук тише)."""
    LATENCY = _Spec.N
    LOUD = True

    def reset(self):
        self.sp = _Spec()
        self._dl = np.zeros((self.LATENCY, 2), np.float32)
        self._ein = self._eout = 0.0
        self._g = 1.0

    def finish(self, x, y):
        buf = np.concatenate([self._dl, x])
        self._dl = buf[len(x):]
        dry = buf[:len(x)]
        if self.LOUD:
            ei, eo = float(np.mean(dry * dry)), float(np.mean(y * y))
            self._ein = self._ein * 0.88 + ei * 0.12
            self._eout = self._eout * 0.88 + eo * 0.12
            tgt = self._g
            if self._ein > 1e-7 and self._eout > 1e-9:
                tgt = min(2.5, max(0.6, math.sqrt(self._ein / self._eout)))
            ramp = np.linspace(self._g, tgt, len(y), dtype=np.float32)[:, None]
            self._g = tgt
            y = y * ramp
        m = float(self.g("mix"))
        if m >= 0.999:
            return y.astype(np.float32)
        return (dry * (1 - m) + y * m).astype(np.float32)


class PitchShift(_SpecFX):
    TYPE = "pitch"
    NAME = "Питч-шифтер"
    GROUP = "Голос"
    INFO = "Выше или ниже без изменения темпа. «Сохранять тембр» — голос не становится мультяшным."
    PARAMS = [P("semi", "Полутоны", -24, 24, 0, "st", "int"), P("cents", "Центы", -100, 100, 0, "ц"),
              P("formant", "Сохранять тембр", 0, 1, 1, "", "bool"), P("fshift", "Сдвиг тембра", -12, 12, 0, "st"),
              P("mix", "Микс", 0, 1, 1, "%")]

    def reset(self):
        super().reset()
        self.sh = _Shifter(_Spec.N, _Spec.H)

    def process(self, x):
        r = 2 ** ((float(self.g("semi")) + float(self.g("cents")) / 100) / 12)
        fm = bool(self.g("formant"))
        fs = 2 ** (float(self.g("fshift")) / 12)
        y = self.sp.run(x, lambda X, inb: self.sh.shift(X, r, fm, fs))
        return self.finish(x, y)


SCALES = [("Мажор", [0, 2, 4, 5, 7, 9, 11]), ("Минор", [0, 2, 3, 5, 7, 8, 10]),
          ("Хроматическая", list(range(12))), ("Пентатоника мажор", [0, 2, 4, 7, 9]),
          ("Пентатоника минор", [0, 3, 5, 7, 10]), ("Гармонический минор", [0, 2, 3, 5, 7, 8, 11]),
          ("Блюз", [0, 3, 5, 6, 7, 10]), ("Дорийский", [0, 2, 3, 5, 7, 9, 10])]


class AutoTune(_SpecFX):
    TYPE = "autotune"
    NAME = "Автотюн"
    GROUP = "Голос"
    INFO = "Подтягивает голос к нотам лада. Скорость 0 — роботизированный эффект, как в хитах 2000-х."
    PARAMS = [P("key", "Тоника", 0, 11, 9, "", "choice", D.KEYS),
              P("scale", "Лад", 0, len(SCALES) - 1, 1, "", "choice", [s[0] for s in SCALES]),
              P("speed", "Скорость подтяжки", 0, 400, 15, "ms"), P("amount", "Сила", 0, 1, 1, "%"),
              P("formant", "Сохранять тембр", 0, 1, 1, "", "bool"), P("mix", "Микс", 0, 1, 1, "%")]

    def reset(self):
        super().reset()
        self.sh = _Shifter(_Spec.N, _Spec.H)
        self.cur = 0.0                          # текущая поправка, полутоны
        self.tgt_note = None
        self.detected = 0.0
        self.target = 0.0

    def _nearest(self, midi):
        key = int(self.g("key"))
        sc = SCALES[int(self.g("scale"))][1]
        best, bd = None, 99.0
        base = int(math.floor(midi)) - 12
        for m in range(base, base + 26):
            if (m - key) % 12 in sc:
                d = abs(m - midi)
                if d < bd:
                    best, bd = m, d
        return best

    def process(self, x):
        speed = float(self.g("speed"))
        a = 1.0 if speed < 1 else 1 - math.exp(-_Spec.H / (speed * SR / 1000))
        amt = float(self.g("amount"))
        fm = bool(self.g("formant"))

        def frame(X, inb):
            f0, conf = D.yin(inb.mean(axis=1), SR, 75.0, 900.0)
            want = 0.0
            if f0 > 0 and conf > 0.55:
                mi = D.hz_to_midi(f0)
                nt = self.tgt_note
                if nt is None or abs(mi - nt) > 0.62:            # гистерезис — нота не дребезжит
                    nt = self._nearest(mi)
                    self.tgt_note = nt
                want = (nt - mi) * amt
                self.detected, self.target = f0, D.midi_to_hz(nt)
            else:
                self.tgt_note = None
                self.detected = 0.0
            self.cur += (want - self.cur) * a
            self.meter = self.cur
            return self.sh.shift(X, 2 ** (self.cur / 12), fm)
        y = self.sp.run(x, frame)
        return self.finish(x, y)


class Robot(_SpecFX):
    TYPE = "robot"
    NAME = "Робот / вокодер"
    GROUP = "Голос"
    INFO = "Робот, монотонный вокодер, аккорд-вокодер или шёпот."
    PARAMS = [P("mode", "Режим", 0, 3, 0, "", "choice", ["Робот", "Вокодер (одна нота)", "Вокодер (аккорд)", "Шёпот"]),
              P("note", "Нота вокодера", 36, 72, 45, "", "int"), P("mix", "Микс", 0, 1, 1, "%")]

    def reset(self):
        super().reset()
        self.rng = np.random.default_rng(11)
        self.hph = {}
        self.bins = _Spec.N // 2 + 1
        self.sign = np.where(np.arange(self.bins) % 2 == 0, 1.0, -1.0)[None, :]

    def _carrier(self, notes):
        """Спектр пилы на нотах (гармоники в ближайших бинах, фазы бегут непрерывно)."""
        C = np.zeros(self.bins, np.complex128)
        for nt in notes:
            f0 = D.midi_to_hz(nt)
            hs = np.arange(1, int((SR / 2 - 100) / f0))
            fb = hs * f0
            bi = np.round(fb / SR * _Spec.N).astype(int)
            ok = bi < self.bins
            ph = self.hph.get(nt)
            if ph is None or len(ph) != len(hs):
                ph = self.rng.uniform(0, 2 * np.pi, len(hs))
            ph = ph + 2 * np.pi * fb * _Spec.H / SR
            self.hph[nt] = ph % (2 * np.pi)
            np.add.at(C, bi[ok], (1.0 / np.sqrt(hs[ok])) * np.exp(1j * ph[ok]))
        return C

    def process(self, x):
        mode = int(self.g("mode"))
        note = int(self.g("note"))

        def frame(X, inb):
            mag = np.abs(X)
            if mode == 0:
                return mag * self.sign                                 # нулевые фазы (импульс в центре кадра) → «робот»
            if mode == 3:
                return mag * np.exp(1j * self.rng.uniform(0, 2 * np.pi, mag.shape))
            env = D.envelope(mag, _Spec.N, 30)
            notes = [note] if mode == 1 else [note, note + 3, note + 7, note + 12]
            C = self._carrier(notes)
            Y = env * C[None, :]
            ex = (mag * mag).sum(axis=1, keepdims=True)
            ey = (np.abs(Y) ** 2).sum(axis=1, keepdims=True)
            return Y * np.sqrt(ex / np.maximum(ey, 1e-12))             # энергия кадра — как у голоса
        y = self.sp.run(x, frame)
        return self.finish(x, y)


class Formant(_SpecFX):
    TYPE = "formant"
    NAME = "Форманта (пол голоса)"
    GROUP = "Голос"
    INFO = "Меняет тембр без смены нот: вниз — голос грубее и «мужественнее», вверх — тоньше, моложе."
    PARAMS = [P("shift", "Сдвиг тембра", -12, 12, 3, "st"), P("mix", "Микс", 0, 1, 1, "%")]

    def reset(self):
        super().reset()
        self.sh = _Shifter(_Spec.N, _Spec.H)

    def process(self, x):
        fs = 2 ** (float(self.g("shift")) / 12)
        y = self.sp.run(x, lambda X, inb: self.sh.shift(X, 1.0, True, fs))
        return self.finish(x, y)


class Harmonizer(_SpecFX):
    TYPE = "harm"
    NAME = "Гармонайзер (бэк-голоса)"
    GROUP = "Голос"
    INFO = "Добавляет к голосу до трёх копий на других нотах — терция, квинта, октава: готовые гармонии."
    PARAMS = [P("v1", "Голос 1: интервал", -24, 24, 4, "st", "int"), P("l1", "Голос 1: громкость", 0, 1, 0.6, "%"),
              P("v2", "Голос 2: интервал", -24, 24, 7, "st", "int"), P("l2", "Голос 2: громкость", 0, 1, 0.45, "%"),
              P("v3", "Голос 3: интервал", -24, 24, -12, "st", "int"), P("l3", "Голос 3: громкость", 0, 1, 0.0, "%"),
              P("formant", "Сохранять тембр", 0, 1, 1, "", "bool"), P("dry", "Основной голос", 0, 1, 1.0, "%"),
              P("mix", "Микс", 0, 1, 1, "%")]
    LOUD = False

    def reset(self):
        super().reset()
        self.shs = [_Shifter(_Spec.N, _Spec.H) for _ in range(3)]

    def process(self, x):
        fm = bool(self.g("formant"))
        vs = [(int(self.g(f"v{i}")), float(self.g(f"l{i}"))) for i in (1, 2, 3)]
        dry = float(self.g("dry"))

        def frame(X, inb):
            Y = X * dry
            for (st, lv), sh in zip(vs, self.shs):
                if lv > 0.001:
                    sp = sh.shift(X, 2 ** (st / 12), fm)
                    if st:                                   # голоса чуть в стороны
                        side = 0.25 if st > 0 else -0.25
                        sp = sp * np.array([[1 - side], [1 + side]])
                    Y = Y + sp * lv
            return Y
        y = self.sp.run(x, frame)
        return self.finish(x, y)


class Doubler(FX):
    TYPE = "double"
    NAME = "Даблер (двойной голос)"
    GROUP = "Голос"
    INFO = "Как будто строчку спели дважды: две копии чуть позже и чуть мимо нот, слева и справа."
    PARAMS = [P("time", "Задержка", 5, 40, 16, "ms"), P("detune", "Расстройка", 0, 30, 9, "ц"),
              P("width", "Ширина", 0, 1, 0.9, "%"), P("mix", "Копии", 0, 1, 0.5, "%")]

    def reset(self):
        self.maxs = int(0.07 * SR)
        self.hist = np.zeros((self.maxs, 2), np.float32)
        self.t = 0.0

    def process(self, x):
        n = len(x)
        base = float(self.g("time")) / 1000 * SR
        cents = float(self.g("detune"))
        # медленно плавающая задержка = небольшой сдвиг высоты (как у второго дубля)
        dep = cents / 1200 * SR / (2 * np.pi * 0.35) * 0.7
        t = self.t + np.arange(n) / SR
        self.t = float((self.t + n / SR) % 1000.0)
        buf = np.concatenate([self.hist, x])
        self.hist = buf[-self.maxs:].copy()
        mono = buf.mean(axis=1)
        pos = self.maxs + np.arange(n)
        out = np.zeros_like(x)
        w = float(self.g("width"))
        for k, (rate, ph, mul) in enumerate(((0.35, 0.0, 1.0), (0.29, 1.7, 1.45))):
            dl = np.clip(base * mul + dep * (1 + np.sin(2 * np.pi * rate * t + ph)), 1, self.maxs - 2)
            p = pos - dl
            i0 = np.floor(p).astype(np.int64)
            fr = (p - i0).astype(np.float32)
            y = mono[i0] * (1 - fr) + mono[i0 + 1] * fr
            pan = w if k == 0 else -w
            out[:, 0] += y * min(1.0, 1 - pan)
            out[:, 1] += y * min(1.0, 1 + pan)
        m = float(self.g("mix"))
        return (x + out * 0.5 * m).astype(np.float32)


class Stutter(FX):
    TYPE = "stutter"
    NAME = "Трансгейт / статтер"
    GROUP = "Модуляция"
    INFO = "Рубит звук в ритм песни по рисунку из 16 шагов — «заикающийся» вокал, трансгейт, глитч."
    PATTERNS = ["Все шаги", "Через один", "Тройки", "Галоп", "Синкопа", "Рваный", "Каждый третий", "Заикание в конце"]
    _PAT = ["1111111111111111", "1010101010101010", "1101101101101101", "1011101110111011",
            "1001001010010010", "1100110100110111", "1001001001001001", "1111111110101111"]
    PARAMS = [P("rate", "Шаг", 0, 2, 1, "", "choice", ["1/8", "1/16", "1/32"]),
              P("pattern", "Рисунок", 0, 7, 1, "", "choice", PATTERNS),
              P("gate", "Длина шага", 0.1, 1, 0.6, "%"), P("smooth", "Сглаживание", 0.001, 0.02, 0.003, "с", "log"),
              P("depth", "Глубина", 0, 1, 1.0, "%"), P("mix", "Микс", 0, 1, 1, "%")]

    def reset(self):
        self.free = 0.0
        self.g_last = 1.0

    def process(self, x):
        n = len(x)
        step = [0.5, 0.25, 0.125][int(self.g("rate"))]
        spb = self.ctx.spb()
        if self.ctx.playing:
            b0 = self.ctx.beat
        else:
            b0 = self.free
            self.free += n / spb
        b = b0 + np.arange(n) / spb
        si = np.floor(b / step).astype(np.int64)
        pat = self._PAT[int(self.g("pattern")) % len(self._PAT)]
        on = np.array([c == "1" for c in pat])[si % 16]
        frac = (b / step) - si
        g = np.where(on & (frac < float(self.g("gate"))), 1.0, 0.0)
        # сглаживание щелчков: однополюсный фильтр по огибающей
        a = 1 - math.exp(-1.0 / (float(self.g("smooth")) * SR))
        g = sps.lfilter([a], [1, a - 1], g, zi=[self.g_last * (1 - a)])[0]
        self.g_last = float(g[-1])
        dep = float(self.g("depth"))
        gain = 1 - dep * (1 - g)
        y = x * gain[:, None].astype(np.float32)
        return _mix(x, y, float(self.g("mix"))).astype(np.float32)


class Tremolo(FX):
    TYPE = "trem"
    NAME = "Тремоло / автопанорама"
    GROUP = "Модуляция"
    INFO = "Громкость или панорама плавно качаются в такт (или с заданной скоростью)."
    PARAMS = [P("mode", "Режим", 0, 1, 0, "", "choice", ["Тремоло (громкость)", "Автопанорама"]),
              P("sync", "В такт", 0, 1, 1, "", "bool"), P("div", "Доля", 0, len(SYNC) - 1, 3, "", "choice", SYNC),
              P("rate", "Скорость (без такта)", 0.1, 20, 4, "Hz", "log"), P("depth", "Глубина", 0, 1, 0.6, "%"),
              P("shape", "Форма", 0, 2, 0, "", "choice", ["Синус", "Треугольник", "Квадрат"])]

    def reset(self):
        self.ph = 0.0

    def process(self, x):
        n = len(x)
        if self.g("sync"):
            spb = self.ctx.spb()
            beats = SYNC_BEATS[int(self.g("div"))]
            if self.ctx.playing:
                t = (self.ctx.beat + np.arange(n) / spb) / beats
            else:
                t = self.ph + np.arange(n) / spb / beats
                self.ph = float((self.ph + n / spb / beats) % 1.0)
        else:
            r = float(self.g("rate"))
            t = self.ph + np.arange(n) * r / SR
            self.ph = float((self.ph + n * r / SR) % 1.0)
        t = np.mod(t, 1.0)
        sh = int(self.g("shape"))
        if sh == 0:
            l = np.sin(2 * np.pi * t)
        elif sh == 1:
            l = 1 - 4 * np.abs(t - 0.5)
        else:
            l = np.tanh(np.sin(2 * np.pi * t) * 6)
        d = float(self.g("depth"))
        if int(self.g("mode")) == 0:
            g = 1 - d * 0.5 * (1 + l)
            return (x * g[:, None]).astype(np.float32)
        pan = d * l
        return np.stack([x[:, 0] * np.minimum(1, 1 - pan), x[:, 1] * np.minimum(1, 1 + pan)], 1).astype(np.float32)


class Exciter(FX):
    TYPE = "exciter"
    NAME = "Эксайтер (воздух)"
    GROUP = "Голос"
    INFO = "Добавляет верхние гармоники: голос «выходит вперёд», становится ярче и разборчивее."
    PARAMS = [P("freq", "От частоты", 1500, 12000, 4500, "Hz", "log"), P("drive", "Насыщение", 0, 1, 0.5, "%"),
              P("amount", "Количество", 0, 1, 0.35, "%")]

    def reset(self):
        self.zi = np.zeros((1, 2, 2))
        self.zi2 = np.zeros((1, 2, 2))
        self.sos = None

    def process(self, x):
        if self._changed() or self.sos is None:
            self.sos = D.biquad("hp", float(self.g("freq")), 0.7)
        hi, self.zi = sps.sosfilt(self.sos, x, axis=0, zi=self.zi)
        k = 1 + float(self.g("drive")) * 9
        h = np.tanh(hi * k) / math.tanh(k) * 1.6
        h, self.zi2 = sps.sosfilt(self.sos, h, axis=0, zi=self.zi2)   # оставить только новые верха
        return (x + h * float(self.g("amount"))).astype(np.float32)


# ------------------------------------------------------------------ #
#  Реестр                                                             #
# ------------------------------------------------------------------ #

FX_CLASSES = [EQ, Filter, Compressor, Limiter, Gate, DeEsser, Pump, Reverb, Delay, Chorus, Flanger, Phaser,
              Stutter, Tremolo, Distortion, Crusher, LoFi, AutoTune, PitchShift, Formant, Harmonizer, Doubler,
              Exciter, Robot, Stereo, Utility, Analyzer]
FX_TYPES = {c.TYPE: c for c in FX_CLASSES}
FX_GROUPS = ["Эквалайзеры и фильтры", "Динамика", "Пространство", "Модуляция", "Искажения", "Голос", "Утилиты"]


def make(slot: dict, ctx: Ctx) -> FX | None:
    cls = FX_TYPES.get((slot or {}).get("type"))
    if cls is None:
        return None
    slot.setdefault("params", {})
    return cls(slot["params"], ctx)


def new_slot(t: str, params: dict | None = None) -> dict:
    cls = FX_TYPES[t]
    p = {s.key: s.default for s in cls.PARAMS}
    if params:
        p.update(params)
    return {"type": t, "on": True, "mix": 1.0, "params": p}


# ── пресеты эффектов ── #
FX_PRESETS = {
    "eq": {"Вырез под вокал": {"g4": -2.5, "f4": 1200, "q4": 0.8, "g5": -2.0, "f5": 3000, "q5": 1.0},
           "Присутствие голоса": {"f1": 90, "g2": -2, "f2": 180, "g5": 3, "f5": 3500, "g6": 2.5, "f6": 10000},
           "Убрать гул": {"f1": 120, "g3": -3, "f3": 300, "q3": 1.2},
           "Телефон": {"f1": 450, "f7": 3200, "slope": 2, "g4": 4, "f4": 1500},
           "Радио AM": {"f1": 300, "f7": 4500, "slope": 1, "g4": 3, "f4": 1800, "q4": 0.6},
           "Больше баса": {"g2": 5, "f2": 80, "q2": 0.8},
           "Яркость": {"g6": 4, "f6": 9000}},
    "comp": {"Вокал": {"thr": -20, "ratio": 3.5, "att": 5, "rel": 120, "makeup": 4},
             "Рэп — плотно": {"thr": -26, "ratio": 6, "att": 2, "rel": 80, "makeup": 8},
             "Бочка": {"thr": -14, "ratio": 4, "att": 20, "rel": 90, "makeup": 2},
             "Склейка мастера": {"thr": -12, "ratio": 2, "att": 30, "rel": 200, "knee": 9, "makeup": 1.5}},
    "reverb": {"Комната": {"size": 0.2, "damp": 0.6, "pre": 5, "wet": 0.18},
               "Зал": {"size": 0.55, "damp": 0.45, "pre": 20, "wet": 0.25},
               "Пластина (вокал)": {"size": 0.38, "damp": 0.3, "pre": 30, "wet": 0.2, "locut": 250},
               "Собор": {"size": 0.9, "damp": 0.35, "pre": 40, "wet": 0.35},
               "Стадион": {"size": 0.75, "damp": 0.5, "pre": 60, "wet": 0.32}},
    "delay": {"Восьмые пинг-понг": {"time": 1, "fb": 0.35, "pp": 1, "mix": 0.25},
              "Четверть точкой": {"time": 4, "fb": 0.45, "pp": 1, "mix": 0.3},
              "Слэпбэк": {"time": 8, "ms": 110, "fb": 0.1, "pp": 0, "mix": 0.3},
              "Бесконечное эхо": {"time": 3, "fb": 0.82, "pp": 1, "mix": 0.35, "damp": 2500}},
    "autotune": {"Жёсткий (роботизированный)": {"speed": 0, "amount": 1},
                 "Естественно": {"speed": 60, "amount": 0.85},
                 "Мягкая подтяжка": {"speed": 150, "amount": 0.6}},
    "pitch": {"Демон": {"semi": -7, "formant": 0}, "Бурундук": {"semi": 8, "formant": 0},
              "Октава вниз": {"semi": -12, "formant": 1}, "Чуть выше": {"semi": 2, "formant": 1}},
    "dist": {"Тёплая лампа": {"type": 0, "drive": 6, "out": -3},
             "Мегафон": {"type": 3, "drive": 18, "tone": 3500, "out": -10},
             "Злой фузз": {"type": 2, "drive": 26, "out": -14}},
    "filter": {"Свип в такт": {"type": 0, "cut": 6000, "res": 2, "lfo": 6, "lfod": 3},
               "Под водой": {"type": 0, "cut": 450, "res": 1.2}},
    "pump": {"Классика 1/4": {"rate": 1, "depth": 0.8, "curve": 0.45},
             "Быстрый 1/8": {"rate": 0, "depth": 0.6, "curve": 0.5}},
    "formant": {"Девушка": {"shift": 3.5}, "Ребёнок": {"shift": 6}, "Мужчина": {"shift": -3},
                "Великан": {"shift": -7}, "Чуть мягче": {"shift": 1.2}},
    "harm": {"Терция вверх": {"v1": 4, "l1": 0.6, "l2": 0, "l3": 0}, "Терция минорная": {"v1": 3, "l1": 0.6, "l2": 0, "l3": 0},
             "Квинта": {"v1": 7, "l1": 0.55, "l2": 0, "l3": 0}, "Октава вниз": {"v1": -12, "l1": 0.6, "l2": 0, "l3": 0},
             "Октавы вверх и вниз": {"v1": 12, "l1": 0.4, "v2": -12, "l2": 0.5, "l3": 0},
             "Мажорный аккорд": {"v1": 4, "l1": 0.5, "v2": 7, "l2": 0.45, "v3": 12, "l3": 0.25},
             "Минорный аккорд": {"v1": 3, "l1": 0.5, "v2": 7, "l2": 0.45, "v3": -12, "l3": 0.3},
             "Хор демонов": {"v1": -12, "l1": 0.7, "v2": -7, "l2": 0.5, "v3": -24, "l3": 0.3, "formant": 0}},
    "double": {"Плотный дабл": {"time": 14, "detune": 7, "mix": 0.55}, "Широкий": {"time": 22, "detune": 12, "width": 1.0, "mix": 0.6},
               "Едва заметный": {"time": 10, "detune": 4, "mix": 0.3}},
    "stutter": {"Трансгейт 1/16": {"rate": 1, "pattern": 0, "gate": 0.5}, "Заикание": {"rate": 2, "pattern": 7, "gate": 0.7},
                "Синкопа": {"rate": 1, "pattern": 4, "gate": 0.8}, "Рваный глитч": {"rate": 2, "pattern": 5, "gate": 0.5}},
    "trem": {"Тремоло 1/8": {"mode": 0, "div": 1, "depth": 0.6}, "Автопан 1/4": {"mode": 1, "div": 3, "depth": 0.8},
             "Вертолёт": {"mode": 0, "sync": 0, "rate": 12, "depth": 0.9, "shape": 2}},
    "exciter": {"Воздух": {"freq": 6000, "drive": 0.4, "amount": 0.35}, "Яркий рэп": {"freq": 3500, "drive": 0.7, "amount": 0.45}},
}

# ── пресеты голоса: цепочка эффектов для дорожки микшера ── #
VOICE_PRESETS = [
    ("Студийный вокал", [("eq", FX_PRESETS["eq"]["Присутствие голоса"]), ("deesser", {}),
                         ("comp", FX_PRESETS["comp"]["Вокал"]), ("reverb", FX_PRESETS["reverb"]["Пластина (вокал)"])]),
    ("Рэп — плотный", [("eq", {"f1": 100, "g5": 3, "f5": 4000}), ("comp", FX_PRESETS["comp"]["Рэп — плотно"]),
                       ("dist", {"type": 0, "drive": 4, "out": -2, "mix": 0.4}),
                       ("delay", {"time": 1, "fb": 0.2, "mix": 0.12}), ("reverb", {"size": 0.2, "wet": 0.1})]),
    ("Автотюн жёсткий", [("autotune", FX_PRESETS["autotune"]["Жёсткий (роботизированный)"]),
                         ("eq", FX_PRESETS["eq"]["Присутствие голоса"]), ("comp", FX_PRESETS["comp"]["Вокал"]),
                         ("delay", FX_PRESETS["delay"]["Восьмые пинг-понг"]),
                         ("reverb", FX_PRESETS["reverb"]["Пластина (вокал)"])]),
    ("Автотюн естественный", [("autotune", FX_PRESETS["autotune"]["Естественно"]), ("comp", FX_PRESETS["comp"]["Вокал"]),
                              ("reverb", FX_PRESETS["reverb"]["Комната"])]),
    ("Телефон", [("eq", FX_PRESETS["eq"]["Телефон"]), ("dist", {"type": 3, "drive": 10, "tone": 4000, "out": -6}),
                 ("comp", {"thr": -24, "ratio": 8})]),
    ("Радио", [("eq", FX_PRESETS["eq"]["Радио AM"]), ("dist", {"type": 0, "drive": 8, "out": -5}), ("lofi", {"crackle": 0.15, "hiss": 0.3})]),
    ("Робот", [("robot", {"mode": 0}), ("eq", {"f1": 150}), ("reverb", {"size": 0.25, "wet": 0.12})]),
    ("Вокодер", [("robot", {"mode": 2, "note": 45}), ("chorus", {"mix": 0.4}), ("reverb", {"size": 0.4, "wet": 0.2})]),
    ("Демон", [("pitch", FX_PRESETS["pitch"]["Демон"]), ("dist", {"type": 0, "drive": 8, "out": -4}),
               ("reverb", {"size": 0.7, "wet": 0.3})]),
    ("Бурундук", [("pitch", FX_PRESETS["pitch"]["Бурундук"]), ("comp", FX_PRESETS["comp"]["Вокал"])]),
    ("Шёпот-призрак", [("robot", {"mode": 3}), ("reverb", {"size": 0.8, "wet": 0.45}), ("delay", {"time": 3, "fb": 0.5, "mix": 0.3})]),
    ("Хор / дабл", [("chorus", {"voices": 3, "depth": 4, "delay": 18, "mix": 0.55, "rate": 0.4}),
                    ("comp", FX_PRESETS["comp"]["Вокал"]), ("reverb", FX_PRESETS["reverb"]["Зал"])]),
    ("Мегафон", [("eq", {"f1": 600, "f7": 4000, "slope": 1}), ("dist", FX_PRESETS["dist"]["Мегафон"])]),
    ("Под водой", [("filter", FX_PRESETS["filter"]["Под водой"]), ("chorus", {"rate": 0.3, "depth": 6, "mix": 0.6}),
                   ("reverb", {"size": 0.6, "wet": 0.3})]),
    ("Стадион", [("comp", FX_PRESETS["comp"]["Вокал"]), ("delay", {"time": 4, "fb": 0.3, "mix": 0.18}),
                 ("reverb", FX_PRESETS["reverb"]["Стадион"])]),
    ("Лоу-фай", [("eq", {"f1": 200, "f7": 7000}), ("lofi", {"wow": 0.5, "crackle": 0.35, "hiss": 0.2}),
                 ("crush", {"bits": 12, "down": 2, "mix": 0.5})]),
    # ── жанры ──
    ("Хайперпоп", [("autotune", {"speed": 0, "amount": 1}), ("formant", {"shift": 2.5}), ("eq", {"f1": 160, "g5": 4, "f5": 4500, "g6": 3, "f6": 11000}),
                   ("dist", {"type": 0, "drive": 10, "out": -6, "mix": 0.35}), ("comp", FX_PRESETS["comp"]["Рэп — плотно"]),
                   ("delay", FX_PRESETS["delay"]["Восьмые пинг-понг"]), ("reverb", {"size": 0.45, "wet": 0.18})]),
    ("Рейдж (жёсткий)", [("autotune", {"speed": 0, "amount": 1}), ("dist", {"type": 2, "drive": 18, "out": -12, "mix": 0.45}),
                         ("eq", {"f1": 180, "g5": 4, "f5": 3500}), ("comp", {"thr": -28, "ratio": 8, "att": 2, "rel": 60, "makeup": 9}),
                         ("delay", {"time": 1, "fb": 0.3, "mix": 0.15}), ("reverb", {"size": 0.3, "wet": 0.12})]),
    ("Эмо-рэп", [("autotune", {"speed": 25, "amount": 0.9}), ("double", {"time": 14, "detune": 7, "mix": 0.4}),
                 ("eq", FX_PRESETS["eq"]["Присутствие голоса"]), ("comp", FX_PRESETS["comp"]["Вокал"]),
                 ("delay", FX_PRESETS["delay"]["Четверть точкой"]), ("reverb", {"size": 0.65, "wet": 0.28})]),
    ("Трэп (мелодичный)", [("autotune", {"speed": 10, "amount": 1}), ("eq", {"f1": 120, "g5": 3, "f5": 4000, "g6": 2, "f6": 10000}),
                           ("deesser", {}), ("comp", FX_PRESETS["comp"]["Рэп — плотно"]), ("exciter", {"amount": 0.25}),
                           ("delay", {"time": 3, "fb": 0.3, "mix": 0.14}), ("reverb", {"size": 0.4, "wet": 0.16})]),
    ("Плаггнб (мягкий)", [("autotune", {"speed": 40, "amount": 0.85}), ("eq", {"f1": 150, "g6": 3, "f6": 9000}),
                          ("comp", FX_PRESETS["comp"]["Вокал"]), ("chorus", {"mix": 0.25, "rate": 0.4}),
                          ("delay", {"time": 4, "fb": 0.4, "mix": 0.18, "damp": 3000}), ("reverb", {"size": 0.7, "wet": 0.3})]),
    ("Дрилл", [("eq", {"f1": 110, "g3": -2, "f3": 350, "g5": 4, "f5": 3000}), ("comp", {"thr": -24, "ratio": 6, "att": 3, "rel": 70, "makeup": 7}),
               ("dist", {"type": 3, "drive": 6, "out": -3, "mix": 0.3}), ("delay", {"time": 0, "fb": 0.15, "mix": 0.08}),
               ("reverb", {"size": 0.2, "wet": 0.08})]),
    ("Олдскул рэп", [("eq", {"f1": 90, "g2": 2, "f2": 200, "f7": 9000}), ("comp", {"thr": -18, "ratio": 4, "att": 8, "rel": 150, "makeup": 4}),
                     ("lofi", {"wow": 0.1, "crackle": 0.05, "hiss": 0.05, "tone": 9000}), ("reverb", {"size": 0.15, "wet": 0.08})]),
    ("Поп-вокал", [("eq", FX_PRESETS["eq"]["Присутствие голоса"]), ("deesser", {}), ("comp", FX_PRESETS["comp"]["Вокал"]),
                   ("exciter", FX_PRESETS["exciter"]["Воздух"]), ("double", FX_PRESETS["double"]["Едва заметный"]),
                   ("delay", {"time": 3, "fb": 0.25, "mix": 0.1}), ("reverb", FX_PRESETS["reverb"]["Пластина (вокал)"])]),
    ("R&B гладкий", [("autotune", {"speed": 80, "amount": 0.6}), ("eq", {"f1": 100, "g2": 1.5, "f2": 200, "g6": 3, "f6": 10000}),
                     ("comp", {"thr": -20, "ratio": 3, "att": 10, "rel": 180, "makeup": 4}), ("chorus", {"mix": 0.2, "rate": 0.3}),
                     ("reverb", {"size": 0.55, "wet": 0.22, "pre": 35})]),
    ("Рок-крик", [("eq", {"f1": 140, "g4": 3, "f4": 1500}), ("dist", {"type": 0, "drive": 14, "out": -8, "mix": 0.5}),
                  ("comp", {"thr": -22, "ratio": 6, "att": 4, "rel": 90, "makeup": 6}), ("delay", {"time": 8, "ms": 120, "fb": 0.1, "mix": 0.2}),
                  ("reverb", {"size": 0.35, "wet": 0.14})]),
    ("Клубный", [("comp", FX_PRESETS["comp"]["Вокал"]), ("pump", FX_PRESETS["pump"]["Классика 1/4"]),
                 ("delay", FX_PRESETS["delay"]["Восьмые пинг-понг"]), ("reverb", FX_PRESETS["reverb"]["Зал"])]),
    ("Дрим-поп (мечта)", [("chorus", {"voices": 3, "mix": 0.45, "rate": 0.25, "depth": 5}), ("eq", {"f1": 200, "g6": 4, "f6": 10000}),
                          ("delay", {"time": 4, "fb": 0.55, "mix": 0.3, "damp": 2500}), ("reverb", {"size": 0.9, "wet": 0.45})]),
    # ── гармонии и даблы ──
    ("Бэк-вокал (терция)", [("harm", FX_PRESETS["harm"]["Терция вверх"]), ("comp", FX_PRESETS["comp"]["Вокал"]),
                            ("reverb", FX_PRESETS["reverb"]["Пластина (вокал)"])]),
    ("Хор гармоний", [("harm", FX_PRESETS["harm"]["Мажорный аккорд"]), ("double", FX_PRESETS["double"]["Широкий"]),
                      ("reverb", FX_PRESETS["reverb"]["Зал"])]),
    ("Октавный дабл", [("harm", FX_PRESETS["harm"]["Октава вниз"]), ("double", FX_PRESETS["double"]["Плотный дабл"]),
                       ("comp", FX_PRESETS["comp"]["Рэп — плотно"])]),
    ("Широкий дабл", [("double", FX_PRESETS["double"]["Широкий"]), ("comp", FX_PRESETS["comp"]["Вокал"]),
                      ("reverb", {"size": 0.3, "wet": 0.12})]),
    ("Адлибы (фон)", [("eq", {"f1": 300, "f7": 8000, "g4": 3, "f4": 1600}), ("formant", {"shift": 1.5}),
                      ("delay", {"time": 3, "fb": 0.45, "mix": 0.35, "pp": 1}), ("reverb", {"size": 0.7, "wet": 0.4}),
                      ("stereo", {"width": 1.8})]),
    # ── характеры ──
    ("Девичий голос", [("formant", FX_PRESETS["formant"]["Девушка"]), ("pitch", {"semi": 4, "formant": 1}),
                       ("eq", {"f1": 200, "g6": 3, "f6": 9000})]),
    ("Мужской голос", [("formant", FX_PRESETS["formant"]["Мужчина"]), ("pitch", {"semi": -4, "formant": 1}),
                       ("eq", {"g2": 2, "f2": 150})]),
    ("Ребёнок", [("pitch", {"semi": 5, "formant": 1}), ("formant", FX_PRESETS["formant"]["Ребёнок"])]),
    ("Великан", [("pitch", {"semi": -5, "formant": 1}), ("formant", FX_PRESETS["formant"]["Великан"]),
                 ("reverb", {"size": 0.6, "wet": 0.25})]),
    ("Монстр", [("harm", FX_PRESETS["harm"]["Хор демонов"]), ("dist", {"type": 2, "drive": 12, "out": -8, "mix": 0.4}),
                ("reverb", {"size": 0.8, "wet": 0.35})]),
    ("Инопланетянин", [("pitch", {"semi": 3, "formant": 0}), ("flanger", {"rate": 0.5, "fb": 0.7, "mix": 0.5}),
                       ("trem", {"mode": 1, "div": 2, "depth": 0.5}), ("reverb", {"size": 0.6, "wet": 0.3})]),
    ("Рация", [("eq", {"f1": 600, "f7": 2800, "slope": 2, "g4": 6, "f4": 1400}), ("dist", {"type": 1, "drive": 16, "out": -10}),
               ("crush", {"bits": 8, "down": 3, "mix": 0.5}), ("gate", {})]),
    ("Винил", [("eq", {"f1": 150, "f7": 8000}), ("lofi", {"wow": 0.25, "crackle": 0.6, "hiss": 0.15, "tone": 7000}),
               ("reverb", {"size": 0.25, "wet": 0.1})]),
    ("Кассета 80-х", [("eq", {"f1": 120, "f7": 9000}), ("lofi", {"wow": 0.6, "crackle": 0.05, "hiss": 0.35, "sat": 0.5}),
                      ("chorus", {"mix": 0.3, "rate": 0.5}), ("reverb", {"size": 0.4, "wet": 0.18})]),
    # ── пространство и ритм ──
    ("Эхо в горах", [("comp", FX_PRESETS["comp"]["Вокал"]), ("delay", {"time": 5, "fb": 0.6, "mix": 0.35, "damp": 2200}),
                     ("reverb", FX_PRESETS["reverb"]["Собор"])]),
    ("Пещера", [("eq", {"f7": 6000}), ("reverb", {"size": 0.95, "damp": 0.6, "pre": 50, "wet": 0.5})]),
    ("Статтер 1/16", [("stutter", FX_PRESETS["stutter"]["Трансгейт 1/16"]), ("comp", FX_PRESETS["comp"]["Вокал"]),
                      ("reverb", {"size": 0.3, "wet": 0.12})]),
    ("Глитч", [("stutter", FX_PRESETS["stutter"]["Рваный глитч"]), ("crush", {"bits": 10, "down": 3, "mix": 0.4}),
               ("delay", {"time": 0, "fb": 0.4, "mix": 0.2})]),
    ("Вертолёт", [("trem", FX_PRESETS["trem"]["Вертолёт"]), ("reverb", {"size": 0.3, "wet": 0.12})]),
    ("Автопанорама", [("trem", FX_PRESETS["trem"]["Автопан 1/4"]), ("delay", {"time": 1, "fb": 0.3, "mix": 0.15})]),
    # ── речь ──
    ("Подкаст / речь", [("gate", {}), ("eq", {"f1": 80, "g2": 2, "f2": 150, "g5": 2, "f5": 3500}), ("deesser", {}),
                        ("comp", {"thr": -22, "ratio": 4, "att": 5, "rel": 150, "makeup": 6}), ("limiter", {"gain": 2.0, "ceil": -1.0})]),
    ("Радиоведущий", [("eq", {"f1": 70, "g2": 4, "f2": 120, "g5": 3, "f5": 4000}), ("comp", {"thr": -26, "ratio": 6, "att": 3, "rel": 100, "makeup": 8}),
                      ("exciter", {"amount": 0.3}), ("limiter", {"gain": 3.0, "ceil": -1.0})]),
    ("Чистый (только звук)", [("eq", {"f1": 80}), ("comp", {"thr": -16, "ratio": 2.5, "att": 10, "rel": 150, "makeup": 2})]),
]


def voice_chain(name: str) -> list:
    for n, chain in VOICE_PRESETS:
        if n == name:
            return [new_slot(t, p) for t, p in chain]
    return []
