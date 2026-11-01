# daw_inst.py
"""
Инструменты студии (генераторы каналов в стойке каналов) и встроенный драм-кит.

Каждый генератор рисует одну ноту целиком: render(нота, сила, длина в секундах) → (n, 2)
float32 с хвостом после отпускания. Движок кэширует готовые ноты, поэтому даже тяжёлый звук
(струна Карплуса — Стронга, рояль из 40 гармоник) считается один раз.

Генераторы: Синтезатор (три осциллятора, фильтр с огибающей, унисон), Супер-пила, Клавишные
(рояль / электропиано / орган / шкатулка / клавесин / маримба), Щипковые (Карплус — Стронг),
FM-синт, 808-бас, Сэмплер (встроенные звуки кита или любой файл). Кит — синтезированные
бочки, малые, хлопки, хэты, тарелки, томы, перкуссия, 808, эффекты.
"""
from __future__ import annotations

import hashlib
import json
import math

import numpy as np
from scipy import signal as sps

import daw_dsp as D
from daw_dsp import SR
from daw_fx import P

TWO_PI = 2 * np.pi


# ------------------------------------------------------------------ #
#  Строительные блоки                                                 #
# ------------------------------------------------------------------ #

def _phase(freq, n, ph0=0.0):
    """Фаза 0..1 для постоянной или меняющейся частоты (массив длины n)."""
    if np.ndim(freq) == 0:
        return (ph0 + np.arange(n) * (float(freq) / SR)) % 1.0
    return (ph0 + np.cumsum(np.asarray(freq, np.float64) / SR)) % 1.0


def _blep(t, dt):
    out = np.zeros_like(t)
    dt = np.broadcast_to(dt, t.shape)
    m = t < dt
    if m.any():
        x = t[m] / dt[m]
        out[m] = x + x - x * x - 1.0
    m = t > 1.0 - dt
    if m.any():
        x = (t[m] - 1.0) / dt[m]
        out[m] = x * x + x + x + 1.0
    return out


WAVES = ["Синус", "Треугольник", "Пила", "Квадрат", "Импульс 25%", "Шум"]


def osc(kind: int, freq, n, ph0=0.0, rng=None):
    ph = _phase(freq, n, ph0)
    dt = np.clip(np.asarray(freq, np.float64) / SR, 1e-6, 0.5)
    if kind == 0:
        return np.sin(TWO_PI * ph)
    if kind == 1:
        return 1.0 - 4.0 * np.abs(ph - 0.5)
    if kind == 2:
        return 2.0 * ph - 1.0 - _blep(ph, dt)
    if kind in (3, 4):
        w = 0.5 if kind == 3 else 0.25
        sq = np.where(ph < w, 1.0, -1.0)
        return sq + _blep(ph, dt) - _blep((ph + (1 - w)) % 1.0, dt)
    rng = rng or np.random.default_rng(1)
    return rng.uniform(-1, 1, n)


def env_adsr(n_note, n_total, a, d, s, r):
    """ADSR (сек; s 0..1): атака линейная, спад и отпускание — экспонента."""
    t = np.arange(n_total) / SR
    a = max(0.0005, a)
    d = max(0.001, d)
    r = max(0.003, r)
    e = np.where(t < a, t / a, s + (1 - s) * np.exp(-(t - a) / (d / 4.0)))
    toff = n_note / SR
    if n_note < n_total:
        lv = float(toff / a) if toff < a else s + (1 - s) * math.exp(-(toff - a) / (d / 4.0))
        rel = t >= toff
        e[rel] = lv * np.exp(-(t[rel] - toff) / (r / 5.0))
    k = min(64, n_total)
    e[-k:] *= np.linspace(1, 0, k)
    return e


_NATIVE = None


def _native() -> bool:
    """Есть ли звуковые функции C++ ядра (echoes_fx.dll со studio.cpp)."""
    global _NATIVE
    if _NATIVE is None:
        try:
            import native_fx
            _NATIVE = native_fx.has_studio()
        except Exception:                                # noqa: BLE001
            _NATIVE = False
    return _NATIVE


def filt(x, kind, cut, q=0.707, block=64):
    """Фильтр с постоянной или меняющейся частотой среза (массив) — по кусочкам, состояние переносится."""
    kind = {0: "lp", 1: "hp", 2: "bp"}.get(kind, kind)
    if np.ndim(cut) == 0:
        return sps.sosfilt(D.biquad(kind, float(cut), q), x, axis=0)
    nk = {"lp": 0, "hp": 1, "bp": 2}.get(kind)
    if nk is not None and _native():
        import native_fx
        return native_fx.vfilter(x, np.asarray(cut, np.float32), q, nk, SR)   # C++, без GIL
    out = np.empty_like(x)
    zi = np.zeros((1, 2) + x.shape[1:])
    for s in range(0, len(x), block):
        c = float(cut[min(len(cut) - 1, s + block // 2)])
        out[s:s + block], zi = sps.sosfilt(D.biquad(kind, c, q), x[s:s + block], axis=0, zi=zi)
    return out


def _st(m, pan=0.0):
    """Моно → стерео с панорамой."""
    gl, gr = min(1.0, 1 - pan), min(1.0, 1 + pan)
    return np.stack([m * gl, m * gr], 1)


def _vel(v):
    return 0.25 + 0.75 * float(v) ** 1.4


def _seed(*a):
    return int(hashlib.md5(repr(a).encode()).hexdigest()[:8], 16)


# ------------------------------------------------------------------ #
#  Генераторы                                                         #
# ------------------------------------------------------------------ #

class Gen:
    KIND = ""
    NAME = ""
    INFO = ""
    PARAMS: list = []
    PRESETS: dict = {}
    ONE_SHOT = False

    def __init__(self, params: dict | None = None):
        self.p = params if params is not None else {}
        for s in self.PARAMS:
            self.p.setdefault(s.key, s.default)

    def g(self, k):
        v = self.p.get(k)
        if v is None:
            s = next((x for x in self.PARAMS if x.key == k), None)
            return s.default if s else 0
        return v

    def tail(self) -> float:
        return float(self.g("rel")) if any(s.key == "rel" for s in self.PARAMS) else 0.3

    def key(self) -> str:
        return self.KIND + json.dumps({s.key: self.p.get(s.key) for s in self.PARAMS}, sort_keys=True,
                                      ensure_ascii=False, default=str)

    def render(self, pitch: int, vel: float, dur: float) -> np.ndarray:
        raise NotImplementedError


def _osc_params(i, w, v, c=0, f=0):
    return [P(f"o{i}w", f"Осц {i}: волна", 0, len(WAVES) - 1, w, "", "choice", WAVES),
            P(f"o{i}v", f"Осц {i}: громкость", 0, 1, v, "%"),
            P(f"o{i}c", f"Осц {i}: полутоны", -24, 24, c, "st", "int"),
            P(f"o{i}f", f"Осц {i}: подстройка", -100, 100, f, "ц")]


_FILTER_P = [P("ftype", "Фильтр", 0, 2, 0, "", "choice", ["Низких (LP)", "Высоких (HP)", "Полосовой"]),
             P("cut", "Срез", 40, 20000, 6000, "Hz", "log"), P("res", "Резонанс", 0.5, 10, 0.9, "", "log"),
             P("fenv", "Огибающая фильтра", 0, 7, 2.0, "окт"), P("fatt", "Ф: атака", 0.0005, 3, 0.002, "с", "log"),
             P("fdec", "Ф: спад", 0.01, 5, 0.35, "с", "log"), P("fsus", "Ф: уровень", 0, 1, 0.2, "%")]
_AMP_P = [P("att", "Атака", 0.0005, 5, 0.003, "с", "log"), P("dec", "Спад", 0.01, 8, 0.4, "с", "log"),
          P("sus", "Уровень", 0, 1, 0.75, "%"), P("rel", "Отпускание", 0.005, 8, 0.18, "с", "log")]


class Synth3x(Gen):
    KIND = "synth3x"
    NAME = "Синтезатор"
    INFO = "Три осциллятора, фильтр с огибающей, унисон и вибрато — бас, лиды, пэды."
    PARAMS = (_osc_params(1, 2, 0.8) + _osc_params(2, 3, 0.5, 0, 7) + _osc_params(3, 0, 0.4, -12) +
              [P("uni", "Унисон (голоса)", 1, 7, 1, "", "int"), P("det", "Расстройка унисона", 0, 60, 15, "ц"),
               P("wid", "Ширина стерео", 0, 1, 0.6, "%"), P("vib", "Вибрато", 0, 60, 0, "ц"),
               P("vibr", "Скорость вибрато", 0.5, 9, 5.5, "Hz"), P("glide", "Глиссандо от", -24, 0, 0, "st", "int"),
               P("drive", "Перегруз", 0, 1, 0.0, "%"), P("vol", "Громкость", 0, 1, 0.7, "%")] + _FILTER_P + _AMP_P)

    PRESETS = {
        "Init": {},
        "Пила-лид": {"o1w": 2, "o2w": 2, "o2f": 9, "o2v": 0.7, "o3v": 0.3, "uni": 3, "det": 18, "cut": 5000,
                     "fenv": 2.5, "fdec": 0.4, "rel": 0.25, "vib": 12},
        "Квадратный лид": {"o1w": 3, "o2w": 4, "o2c": 12, "o2v": 0.35, "o3v": 0.0, "cut": 4000, "fenv": 1.5},
        "Суб-бас": {"o1w": 0, "o1v": 1.0, "o2v": 0.0, "o3w": 1, "o3v": 0.25, "o3c": 0, "cut": 900, "fenv": 0,
                    "att": 0.004, "sus": 1.0, "rel": 0.08},
        "Reese-бас": {"o1w": 2, "o2w": 2, "o2f": 22, "o2v": 0.9, "o3w": 0, "o3c": -12, "o3v": 0.5, "cut": 700,
                      "res": 1.5, "fenv": 1.2, "sus": 0.9, "uni": 2, "det": 25, "drive": 0.3},
        "Синт-бас 80-х": {"o1w": 2, "o2w": 3, "o2c": -12, "o2v": 0.6, "o3v": 0, "cut": 350, "res": 3, "fenv": 3.5,
                          "fdec": 0.18, "fsus": 0.1, "sus": 0.6, "dec": 0.3},
        "Мягкий пэд": {"o1w": 2, "o2w": 1, "o2f": 7, "o3w": 0, "o3c": 12, "uni": 5, "det": 22, "wid": 1.0,
                       "cut": 2200, "fenv": 0.8, "fatt": 0.8, "fdec": 2.0, "att": 0.9, "dec": 1.5, "sus": 0.8,
                       "rel": 1.6, "vol": 0.55},
        "Стринги": {"o1w": 2, "o2w": 2, "o2f": 6, "o3w": 2, "o3c": 12, "o3v": 0.3, "uni": 4, "det": 14,
                    "cut": 3500, "att": 0.35, "sus": 0.9, "rel": 0.8, "vib": 8, "vibr": 5},
        "Плак": {"o1w": 2, "o2w": 3, "o2c": 12, "o2v": 0.4, "cut": 1200, "res": 1.4, "fenv": 4.0, "fdec": 0.15,
                 "fsus": 0.0, "dec": 0.3, "sus": 0.0, "rel": 0.2},
        "Чиптюн": {"o1w": 4, "o2w": 3, "o2c": 12, "o2v": 0.25, "o3v": 0, "cut": 18000, "fenv": 0, "sus": 0.8,
                   "rel": 0.04, "vib": 18, "vibr": 7},
        "Брасс": {"o1w": 2, "o2w": 2, "o2f": -8, "o3v": 0.0, "uni": 2, "det": 10, "cut": 800, "fenv": 2.5,
                  "fatt": 0.08, "fdec": 0.5, "fsus": 0.5, "att": 0.05, "sus": 0.85, "rel": 0.2},
        "Колокольчик": {"o1w": 0, "o2w": 0, "o2c": 24, "o2f": 3, "o2v": 0.35, "o3w": 1, "o3c": 19, "o3v": 0.2,
                        "cut": 12000, "fenv": 0, "dec": 1.2, "sus": 0.0, "rel": 1.2},
        "Глайд-бас": {"o1w": 2, "o2w": 0, "o2c": -12, "o2v": 0.8, "o3v": 0, "glide": -12, "cut": 1500,
                      "fenv": 1.5, "sus": 0.9, "drive": 0.35},
    }

    def render(self, pitch, vel, dur):
        n_note = max(1, int(dur * SR))
        rel = float(self.g("rel"))
        n = n_note + int(rel * SR) + 64
        rng = np.random.default_rng(_seed(pitch, self.key()))
        base = D.midi_to_hz(pitch)
        t = np.arange(n) / SR
        fm = np.ones(n)
        vib = float(self.g("vib"))
        if vib > 0:
            fm *= 2 ** (vib / 1200 * np.sin(TWO_PI * float(self.g("vibr")) * t) * np.clip(t / 0.25, 0, 1))
        gl = float(self.g("glide"))
        if gl:
            fm *= 2 ** (gl / 12 * np.exp(-t / 0.06))
        uni = max(1, int(self.g("uni")))
        det = float(self.g("det"))
        wid = float(self.g("wid"))
        out = np.zeros((n, 2))
        for i in (1, 2, 3):
            v = float(self.g(f"o{i}v"))
            if v <= 0.001:
                continue
            w = int(self.g(f"o{i}w"))
            f0 = base * 2 ** ((float(self.g(f"o{i}c")) + float(self.g(f"o{i}f")) / 100) / 12)
            for u in range(uni):
                off = (u / (uni - 1) - 0.5) * 2 if uni > 1 else 0.0
                fu = f0 * 2 ** (off * det / 1200)
                s = osc(w, fu * fm if (vib or gl) else float(fu), n, rng.random(), rng)
                out += _st(s, off * wid) * (v / math.sqrt(uni))
        dr = float(self.g("drive"))
        if dr > 0.01:
            k = 1 + dr * 6
            out = np.tanh(out * k) / math.tanh(k)
        env_f = env_adsr(n_note, n, float(self.g("fatt")), float(self.g("fdec")), float(self.g("fsus")), rel)
        fe = float(self.g("fenv"))
        cut = float(self.g("cut")) * (0.6 + 0.4 * _vel(vel))
        if fe > 0.01:
            cuts = np.clip(cut * 2 ** (fe * env_f), 30, SR * 0.45)
            out = filt(out, int(self.g("ftype")), cuts, float(self.g("res")))
        elif cut < 19000 or int(self.g("ftype")) != 0:
            out = filt(out, int(self.g("ftype")), cut, float(self.g("res")))
        amp = env_adsr(n_note, n, float(self.g("att")), float(self.g("dec")), float(self.g("sus")), rel)
        out *= (amp * _vel(vel) * float(self.g("vol")) * 0.5)[:, None]
        return out.astype(np.float32)


class Supersaw(Gen):
    KIND = "supersaw"
    NAME = "Супер-пила"
    INFO = "До 9 расстроенных пил — широкие лиды и пэды транса и EDM."
    PARAMS = [P("voices", "Голоса", 1, 9, 7, "", "int"), P("det", "Расстройка", 0, 1, 0.35, "%"),
              P("mixc", "Центр / края", 0, 1, 0.55, "%"), P("wid", "Ширина", 0, 1, 0.9, "%"),
              P("oct", "Октава вниз", 0, 1, 0.2, "%"), P("vol", "Громкость", 0, 1, 0.6, "%")] + _FILTER_P + _AMP_P
    PRESETS = {
        "Транс-лид": {"voices": 7, "det": 0.4, "cut": 9000, "fenv": 1.0, "rel": 0.3},
        "Широкий пэд": {"voices": 9, "det": 0.5, "cut": 2500, "att": 0.8, "rel": 1.8, "sus": 0.85, "dec": 1.0,
                        "fenv": 0.6, "fatt": 0.6, "fdec": 1.5},
        "Хардстайл": {"voices": 7, "det": 0.6, "cut": 12000, "res": 1.5, "rel": 0.15, "oct": 0.5},
        "Аккорды-стабы": {"voices": 5, "det": 0.3, "cut": 2000, "fenv": 3, "fdec": 0.2, "fsus": 0.1, "dec": 0.35,
                          "sus": 0.3, "rel": 0.2},
    }

    def render(self, pitch, vel, dur):
        n_note = max(1, int(dur * SR))
        rel = float(self.g("rel"))
        n = n_note + int(rel * SR) + 64
        rng = np.random.default_rng(_seed(pitch, self.key()))
        f0 = D.midi_to_hz(pitch)
        vo = max(1, int(self.g("voices")))
        det = float(self.g("det"))
        offs = np.array([0.0, -0.11, 0.11, -0.063, 0.063, -0.018, 0.018, -0.15, 0.15])[:vo] * det
        out = np.zeros((n, 2))
        mc = float(self.g("mixc"))
        for i, o in enumerate(offs):
            s = osc(2, f0 * (1 + o), n, rng.random())
            g = (1 - mc) if i == 0 else mc / max(1, vo - 1) * 2
            pan = 0 if i == 0 else (1 if i % 2 else -1) * float(self.g("wid")) * min(1, abs(o) / max(1e-6, det * 0.15))
            out += _st(s, pan) * g
        ov = float(self.g("oct"))
        if ov > 0.01:
            out += _st(osc(2, f0 / 2, n, rng.random())) * ov * 0.6
        env_f = env_adsr(n_note, n, float(self.g("fatt")), float(self.g("fdec")), float(self.g("fsus")), rel)
        fe = float(self.g("fenv"))
        cut = float(self.g("cut"))
        if fe > 0.01:
            out = filt(out, int(self.g("ftype")), np.clip(cut * 2 ** (fe * env_f), 30, SR * 0.45), float(self.g("res")))
        else:
            out = filt(out, int(self.g("ftype")), cut, float(self.g("res")))
        amp = env_adsr(n_note, n, float(self.g("att")), float(self.g("dec")), float(self.g("sus")), rel)
        out *= (amp * _vel(vel) * float(self.g("vol")) * 0.75)[:, None]
        return out.astype(np.float32)


KEYS_MODELS = ["Рояль", "Электропиано", "Орган", "Музыкальная шкатулка", "Клавесин", "Маримба"]


class Keys(Gen):
    KIND = "keys"
    NAME = "Клавишные"
    INFO = "Рояль, электропиано, орган, шкатулка, клавесин, маримба."
    PARAMS = [P("model", "Инструмент", 0, len(KEYS_MODELS) - 1, 0, "", "choice", KEYS_MODELS),
              P("bright", "Яркость", 0, 1, 0.5, "%"), P("decay", "Звучание", 0.2, 12, 4.0, "с", "log"),
              P("rel", "Отпускание", 0.02, 3, 0.35, "с", "log"), P("chorus", "Хорус", 0, 1, 0.25, "%"),
              P("vol", "Громкость", 0, 1, 0.75, "%")]
    PRESETS = {"Рояль": {"model": 0}, "Яркий рояль": {"model": 0, "bright": 0.85},
               "Электропиано": {"model": 1, "decay": 3.0, "chorus": 0.5},
               "Орган": {"model": 2, "rel": 0.06, "chorus": 0.6}, "Шкатулка": {"model": 3, "decay": 1.5},
               "Клавесин": {"model": 4, "decay": 2.5, "bright": 0.7}, "Маримба": {"model": 5, "decay": 0.8}}

    def tail(self):
        return float(self.g("rel")) + (0.0 if int(self.g("model")) == 2 else 0.2)

    def render(self, pitch, vel, dur):
        model = int(self.g("model"))
        f0 = D.midi_to_hz(pitch)
        rel = float(self.g("rel"))
        n_note = max(1, int(dur * SR))
        if model in (3, 5):                                         # звенит само, отпускание не глушит
            n_note = int(min(6.0, float(self.g("decay"))) * SR)
        n = max(n_note + int(rel * SR) + 64, 64)
        t = np.arange(n) / SR
        rng = np.random.default_rng(_seed(pitch, model))
        br = float(self.g("bright")) * (0.5 + 0.5 * vel)
        dec = float(self.g("decay")) * (1.6 - (pitch - 21) / 88)   # высокие ноты гаснут быстрее
        out = np.zeros(n)
        if model in (0, 4):
            B = 0.00012 if model == 0 else 0.00003
            H = int(min(40, (SR / 2 - 200) / f0))
            for h in range(1, H + 1):
                fh = f0 * h * math.sqrt(1 + B * h * h)
                if fh > SR * 0.45:
                    break
                a = (1.0 / h ** (1.9 - 1.0 * br)) * (1.2 if h == 1 else 1)
                if model == 4:
                    a = 1.0 / h ** (0.9 - 0.4 * br) * (0.5 + 0.5 * math.sin(h * 0.9) ** 2)
                if a < 0.004:
                    continue
                tau = dec / (1 + 0.35 * h)
                ex = np.exp(-t / tau)
                strings = (-0.6, 0.6) if (model == 0 and h <= 8) else (0.0,)
                for dtn in strings:                                  # две струны на ноту — живые биения
                    out += a / len(strings) * np.sin(TWO_PI * fh * 2 ** (dtn / 1200) * t + rng.random() * 6.28) * ex
            ham = filt(rng.uniform(-1, 1, min(n, int(0.012 * SR))), "lp", 2000 + 3000 * br) * 0.4 * vel
            out[:len(ham)] += ham * np.linspace(1, 0, len(ham))
        elif model == 1:
            ind = 2.2 * br + 0.6
            mod = np.sin(TWO_PI * f0 * t) * ind * np.exp(-t / 0.6)
            out = np.sin(TWO_PI * f0 * t + mod) * np.exp(-t / dec)
            out += 0.25 * br * np.sin(TWO_PI * f0 * 14 * t) * np.exp(-t / 0.05)       # «металл» молоточка
        elif model == 2:
            bars = [(0.5, 0.6), (1, 1.0), (1.5, 0.5), (2, 0.7), (3, 0.35 + 0.4 * br), (4, 0.3 + 0.3 * br),
                    (5, 0.12 * br), (6, 0.15 * br), (8, 0.2 * br)]
            for m, a in bars:
                if f0 * m < SR * 0.45:
                    out += a * np.sin(TWO_PI * f0 * m * t + rng.random() * 6.28)
            out *= 0.45
            out *= np.clip(t / 0.006, 0, 1)
            click = rng.uniform(-1, 1, min(n, 200)) * 0.1 * br
            out[:len(click)] += click
        elif model == 3:
            for m, a, tau in ((1, 1.0, 1.0), (3.0, 0.35, 0.35), (5.4, 0.15, 0.15), (8.9, 0.08, 0.08)):
                if f0 * m < SR * 0.45:
                    out += a * np.sin(TWO_PI * f0 * m * t) * np.exp(-t / (tau * float(self.g("decay"))))
        elif model == 5:
            for m, a, tau in ((1, 1.0, 0.5), (4.0, 0.35 * br + 0.1, 0.08), (9.2, 0.1 * br, 0.03)):
                if f0 * m < SR * 0.45:
                    out += a * np.sin(TWO_PI * f0 * m * t) * np.exp(-t / (tau * float(self.g("decay")) * 2))
            out *= np.clip(t / 0.002, 0, 1)
        if model not in (3, 5):
            e = np.ones(n)
            toff = n_note / SR
            r = t >= toff
            e[r] = np.exp(-(t[r] - toff) / (rel / 5))
            out *= e
        if model in (0, 4, 1):
            out *= np.clip(t / 0.002, 0, 1)
        k = min(64, n)
        out[-k:] *= np.linspace(1, 0, k)
        st = _st(out)
        ch = float(self.g("chorus"))
        if ch > 0.01:
            d = int(0.011 * SR)
            lfo = (np.sin(TWO_PI * 0.8 * t) * 0.002 * SR * ch).astype(int)
            idx = np.clip(np.arange(n) - d - lfo, 0, n - 1)
            st[:, 1] = out * (1 - 0.5 * ch) + out[idx] * 0.5 * ch
            st[:, 0] = out * (1 - 0.5 * ch) - out[idx] * 0.3 * ch
        return (st * _vel(vel) * float(self.g("vol")) * 0.8).astype(np.float32)


class Pluck(Gen):
    KIND = "pluck"
    NAME = "Щипковые"
    INFO = "Струна по Карплусу — Стронгу: гитара, арфа, кото, бас-гитара."
    PARAMS = [P("decay", "Звучание", 0.2, 12, 3.0, "с", "log"), P("bright", "Яркость", 0, 1, 0.6, "%"),
              P("body", "Корпус", 0, 1, 0.4, "%"), P("pick", "Место щипка", 0.05, 0.5, 0.18, "%"),
              P("rel", "Отпускание", 0.02, 2, 0.15, "с", "log"), P("vol", "Громкость", 0, 1, 0.8, "%")]
    PRESETS = {"Гитара": {}, "Арфа": {"decay": 4, "bright": 0.45, "body": 0.2},
               "Кото": {"decay": 2, "bright": 0.9, "pick": 0.08}, "Бас-гитара": {"decay": 2.5, "bright": 0.3, "body": 0.6},
               "Глухой щипок": {"decay": 0.5, "bright": 0.25}}

    def render(self, pitch, vel, dur):
        f0 = D.midi_to_hz(pitch)
        rel = float(self.g("rel"))
        n_note = max(1, int(dur * SR))
        n = n_note + int(rel * SR) + 64
        rng = np.random.default_rng(_seed(pitch, self.key()))
        Pd = SR / f0 - 0.5                                          # полный период минус задержка усреднения
        Pi = max(2, int(Pd))
        frac = Pd - Pi
        c = (1 - frac) / (1 + frac)                                 # аллпасс для дробной задержки
        T60 = float(self.g("decay")) * (1.4 - (pitch - 30) / 100)
        g = 10 ** (-3 * Pd / (max(0.05, T60) * SR))
        br = float(self.g("bright")) * (0.5 + 0.5 * vel)
        exc = rng.uniform(-1, 1, Pi + 1)
        exc = filt(exc, "lp", 800 + 9000 * br, 0.7)
        pk = max(1, int(float(self.g("pick")) * Pi))
        exc = exc - np.concatenate([np.zeros(pk), exc[:-pk]])       # гребёнка «место щипка»
        x = np.zeros(n)
        x[:len(exc)] = exc
        # y = x·(1 + c z⁻¹) / [(1 + c z⁻¹) − g·(c + z⁻¹)(1 + z⁻¹)/2·z^(−Pi)]
        b = np.array([1.0, c])
        a = np.zeros(Pi + 3)
        a[0], a[1] = 1.0, c
        fb = np.convolve([c, 1.0], [0.5, 0.5]) * g
        a[Pi:Pi + 3] -= fb
        y = sps.lfilter(b, a, x)
        bd = float(self.g("body"))
        if bd > 0.01:
            y = y + bd * sps.sosfilt(np.vstack([D.biquad("peak", 110, 1.5, 8), D.biquad("peak", 220, 2, 5)]), y) * 0.5
        e = np.ones(n)
        toff = n_note / SR
        t = np.arange(n) / SR
        r = t >= toff
        e[r] = np.exp(-(t[r] - toff) / (rel / 5))
        y *= e
        k = min(64, n)
        y[-k:] *= np.linspace(1, 0, k)
        y /= max(1e-6, np.abs(y[: min(n, int(0.2 * SR))]).max())
        return (_st(y) * _vel(vel) * float(self.g("vol")) * 0.45).astype(np.float32)


class FM(Gen):
    KIND = "fm"
    NAME = "FM-синт"
    INFO = "Частотная модуляция двух операторов: колокола, DX-пиано, металлический бас."
    PARAMS = [P("ratio", "Отношение модулятора", 0.25, 12, 3.5, "", "log"), P("index", "Глубина модуляции", 0, 12, 4, ""),
              P("idec", "Спад глубины", 0.01, 6, 0.8, "с", "log"), P("fb", "Обратная связь", 0, 1, 0, "%"),
              P("vol", "Громкость", 0, 1, 0.7, "%")] + _AMP_P
    PRESETS = {"Колокол": {"ratio": 3.5, "index": 6, "idec": 1.5, "dec": 3, "sus": 0.0, "rel": 2.0},
               "DX-пиано": {"ratio": 1.0, "index": 2.8, "idec": 0.7, "dec": 1.8, "sus": 0.2, "rel": 0.4},
               "FM-бас": {"ratio": 1.0, "index": 5, "idec": 0.12, "sus": 0.7, "rel": 0.1},
               "Металл": {"ratio": 1.41, "index": 8, "idec": 0.5, "dec": 1.0, "sus": 0.0},
               "Мягкий FM-пэд": {"ratio": 2.0, "index": 1.4, "idec": 4, "att": 0.6, "sus": 0.9, "rel": 1.5}}

    def render(self, pitch, vel, dur):
        n_note = max(1, int(dur * SR))
        rel = float(self.g("rel"))
        n = n_note + int(rel * SR) + 64
        t = np.arange(n) / SR
        f0 = D.midi_to_hz(pitch)
        fr = float(self.g("ratio"))
        idx = float(self.g("index")) * (0.4 + 0.6 * vel) * np.exp(-t / float(self.g("idec")))
        m = np.sin(TWO_PI * f0 * fr * t)
        fb = float(self.g("fb"))
        if fb > 0.01:
            m = np.sin(TWO_PI * f0 * fr * t + fb * 1.5 * m)
        s = np.sin(TWO_PI * f0 * t + idx * m)
        amp = env_adsr(n_note, n, float(self.g("att")), float(self.g("dec")), float(self.g("sus")), rel)
        return (_st(s * amp) * _vel(vel) * float(self.g("vol")) * 0.6).astype(np.float32)


class Bass808(Gen):
    KIND = "808"
    NAME = "808-бас"
    INFO = "Гудящий бас трэпа: синус со «щелчком» высоты, долгим хвостом и перегрузом."
    PARAMS = [P("decay", "Хвост", 0.15, 6, 1.6, "с", "log"), P("punch", "Удар высотой", 0, 24, 7, "st"),
              P("ptime", "Время удара", 0.005, 0.3, 0.04, "с", "log"), P("drive", "Перегруз", 0, 1, 0.35, "%"),
              P("tone", "Тон", 100, 8000, 2500, "Hz", "log"), P("click", "Щелчок", 0, 1, 0.3, "%"),
              P("rel", "Отпускание", 0.01, 1, 0.08, "с", "log"), P("vol", "Громкость", 0, 1, 0.85, "%")]
    PRESETS = {"808 классика": {}, "808 долгий": {"decay": 3.5, "drive": 0.2},
               "808 злой": {"drive": 0.8, "punch": 12, "tone": 4000}, "808 короткий": {"decay": 0.45, "punch": 10},
               "Дистортед 808": {"drive": 1.0, "tone": 6000, "click": 0.5}}

    def render(self, pitch, vel, dur):
        n_note = max(1, int(min(dur, 8.0) * SR))
        rel = float(self.g("rel"))
        n = n_note + int(rel * SR) + 64
        t = np.arange(n) / SR
        f0 = D.midi_to_hz(pitch)
        f = f0 * 2 ** (float(self.g("punch")) / 12 * np.exp(-t / float(self.g("ptime"))))
        s = np.sin(TWO_PI * np.cumsum(f) / SR)
        amp = np.exp(-t / float(self.g("decay"))) * np.clip(t / 0.002, 0, 1)
        toff = n_note / SR
        r = t >= toff
        amp[r] *= np.exp(-(t[r] - toff) / (rel / 5))
        s *= amp
        dr = float(self.g("drive"))
        if dr > 0.01:
            k = 1 + dr * 8
            s = np.tanh(s * k) / math.tanh(k) * (1 - 0.2 * dr)
        cl = float(self.g("click"))
        if cl > 0.01:
            c = np.random.default_rng(3).uniform(-1, 1, min(n, int(0.004 * SR)))
            s[:len(c)] += c * cl * 0.5 * np.linspace(1, 0, len(c))
        s = filt(s, "lp", float(self.g("tone")), 0.7)
        k = min(64, n)
        s[-k:] *= np.linspace(1, 0, k)
        return (_st(s) * _vel(vel) * float(self.g("vol")) * 0.8).astype(np.float32)


# ------------------------------------------------------------------ #
#  Встроенный драм-кит                                                #
# ------------------------------------------------------------------ #

def _noise(n, seed):
    return np.random.default_rng(seed).uniform(-1, 1, n)


def _kick(f1=55, f0=160, pdec=0.04, adec=0.35, click=0.4, drive=0.2, seed=1):
    n = int((adec * 5 + 0.05) * SR)
    t = np.arange(n) / SR
    f = f1 + (f0 - f1) * np.exp(-t / pdec)
    s = np.sin(TWO_PI * np.cumsum(f) / SR) * np.exp(-t / adec)
    c = filt(_noise(int(0.006 * SR), seed), "hp", 1500) * click
    s[:len(c)] += c * np.linspace(1, 0, len(c))
    if drive > 0:
        s = np.tanh(s * (1 + 4 * drive)) / math.tanh(1 + 4 * drive)
    return s


def _snare(tone=190, tdec=0.08, ndec=0.16, nmix=0.75, bp=4500, seed=2):
    n = int((ndec * 5 + 0.05) * SR)
    t = np.arange(n) / SR
    body = (np.sin(TWO_PI * tone * t) + 0.5 * np.sin(TWO_PI * tone * 1.6 * t)) * np.exp(-t / tdec)
    nz = filt(_noise(n, seed), "bp", bp, 0.6) * np.exp(-t / ndec)
    nz += filt(_noise(n, seed + 1), "hp", 6000) * np.exp(-t / (ndec * 0.6)) * 0.5
    return body * (1 - nmix) + nz * nmix * 2.2


def _clap(dec=0.18, bursts=4, spread=0.011, bp=1300, seed=3):
    n = int((dec * 5 + bursts * spread + 0.05) * SR)
    t = np.arange(n) / SR
    s = np.zeros(n)
    nz = filt(_noise(n, seed), "bp", bp, 1.2)
    for b in range(bursts):
        st = int(b * spread * SR)
        tt = t[st:] - t[st]
        d = dec if b == bursts - 1 else 0.008
        s[st:] += nz[st:] * np.exp(-tt / d)
    return s * 1.8


def _hat(dec=0.05, hp=7000, seed=4, metal=0.6):
    n = int((dec * 6 + 0.02) * SR)
    t = np.arange(n) / SR
    ratios = [2, 3, 4.16, 5.43, 6.79, 8.21]
    m = sum(np.sign(np.sin(TWO_PI * 40 * r * t + r)) for r in ratios) / 6
    s = m * metal + _noise(n, seed) * (1 - metal)
    s = filt(s, "hp", hp, 0.7)
    s = filt(s, "bp", 10000, 0.5) * 0.5 + s * 0.5
    return s * np.exp(-t / dec) * np.clip(t / 0.0008, 0, 1) * 1.6


def _cymbal(dec=1.2, seed=5, ride=False):
    n = int((dec * 4) * SR)
    t = np.arange(n) / SR
    ratios = [2, 3, 4.16, 5.43, 6.79, 8.21, 9.13, 11.3]
    m = sum(np.sign(np.sin(TWO_PI * 60 * r * t + r)) for r in ratios) / 8
    s = filt(m * 0.5 + _noise(n, seed) * 0.5, "hp", 4000 if not ride else 6000)
    if ride:
        s += np.sin(TWO_PI * 3200 * t) * 0.15
    return s * np.exp(-t / dec) * np.clip(t / 0.002, 0, 1) * 1.3


def _tom(f=110, dec=0.3, seed=6):
    n = int((dec * 5) * SR)
    t = np.arange(n) / SR
    fr = f * (1 + 0.6 * np.exp(-t / 0.05))
    s = np.sin(TWO_PI * np.cumsum(fr) / SR) * np.exp(-t / dec)
    s[:int(0.004 * SR)] += filt(_noise(int(0.004 * SR), seed), "lp", 3000) * 0.3
    return s


def _cowbell(seed=7):
    n = int(0.6 * SR)
    t = np.arange(n) / SR
    s = (np.sign(np.sin(TWO_PI * 540 * t)) + np.sign(np.sin(TWO_PI * 800 * t))) * 0.5
    s = filt(s, "bp", 2600, 1.2) * (0.6 * np.exp(-t / 0.03) + 0.4 * np.exp(-t / 0.18))
    return s * 2.0


def _rim(seed=8):
    n = int(0.12 * SR)
    t = np.arange(n) / SR
    return np.sin(TWO_PI * 1700 * t) * np.exp(-t / 0.01) + filt(_noise(n, seed), "bp", 3500, 2) * np.exp(-t / 0.012)


def _shaker(seed=9):
    n = int(0.25 * SR)
    t = np.arange(n) / SR
    env = np.clip(t / 0.03, 0, 1) * np.exp(-t / 0.05)
    return filt(_noise(n, seed), "hp", 6500) * env * 1.4


def _snap(seed=10):
    n = int(0.2 * SR)
    t = np.arange(n) / SR
    return filt(_noise(n, seed), "bp", 2200, 3) * np.exp(-t / 0.03) * 3


def _conga(f=220, seed=11):
    n = int(0.6 * SR)
    t = np.arange(n) / SR
    fr = f * (1 + 0.2 * np.exp(-t / 0.02))
    return np.sin(TWO_PI * np.cumsum(fr) / SR) * np.exp(-t / 0.16)


def _riser(seconds=4.0, seed=12, down=False):
    n = int(seconds * SR)
    t = np.arange(n) / SR
    k = t / seconds
    if down:
        k = 1 - k
    cut = 300 * 2 ** (k * 5.5)
    s = filt(_noise(n, seed), "bp", cut, 1.8)
    s += np.sin(TWO_PI * np.cumsum(200 * 2 ** (k * 3)) / SR) * 0.15
    amp = (k ** 1.6) if not down else (k ** 1.2)
    return s * amp * 1.2


def _impact(seed=13):
    n = int(3.0 * SR)
    t = np.arange(n) / SR
    s = np.sin(TWO_PI * np.cumsum(40 + 80 * np.exp(-t / 0.08)) / SR) * np.exp(-t / 0.9)
    s += filt(_noise(n, seed), "lp", 2500) * np.exp(-t / 0.4) * 0.6
    return np.tanh(s * 1.5)


def _rev_crash(seed=14):
    return _cymbal(1.6, seed)[::-1].copy()


KIT = [
    ("Бочки", [("Бочка Punch", lambda: _kick(55, 180, 0.035, 0.32, 0.5, 0.3)),
               ("Бочка Deep", lambda: _kick(45, 140, 0.05, 0.6, 0.25, 0.15)),
               ("Бочка House", lambda: _kick(52, 200, 0.03, 0.28, 0.7, 0.35)),
               ("Бочка Trap", lambda: _kick(48, 230, 0.025, 0.45, 0.6, 0.45)),
               ("Бочка Lo-fi", lambda: filt(_kick(58, 150, 0.04, 0.3, 0.2, 0.6), "lp", 2500)),
               ("Бочка Hardstyle", lambda: _kick(50, 400, 0.06, 0.5, 0.8, 1.0))]),
    ("Малые", [("Малый Tight", lambda: _snare(210, 0.06, 0.11, 0.7, 5000)),
               ("Малый Fat", lambda: _snare(170, 0.11, 0.22, 0.65, 3500)),
               ("Малый Trap", lambda: _snare(230, 0.05, 0.18, 0.85, 6500)),
               ("Римшот", _rim)]),
    ("Хлопки", [("Хлопок", lambda: _clap(0.16)), ("Хлопок большой", lambda: _clap(0.32, 5, 0.013, 1100)),
                ("Щелчок пальцами", _snap)]),
    ("Хэты и тарелки", [("Хэт закрытый", lambda: _hat(0.045)), ("Хэт Trap", lambda: _hat(0.028, 8500, metal=0.75)),
                        ("Хэт открытый", lambda: _hat(0.32, 6500)), ("Шейкер", _shaker),
                        ("Райд", lambda: _cymbal(1.5, ride=True)), ("Крэш", lambda: _cymbal(1.3))]),
    ("Томы и перкуссия", [("Том низкий", lambda: _tom(95)), ("Том средний", lambda: _tom(140)),
                          ("Том высокий", lambda: _tom(200)), ("Ковбелл", _cowbell),
                          ("Конга", lambda: _conga(230)), ("Конга низкая", lambda: _conga(160))]),
    ("808", [("808 Sub", lambda: Bass808({"decay": 1.4}).render(36, 0.9, 1.4)[:, 0]),
             ("808 Long", lambda: Bass808({"decay": 3}).render(36, 0.9, 3)[:, 0]),
             ("808 Dist", lambda: Bass808({"drive": 0.9, "tone": 5000}).render(36, 0.9, 1.5)[:, 0])]),
    ("Эффекты", [("Райзер", _riser), ("Даунлифтер", lambda: _riser(2.5, 15, True)), ("Импакт", _impact),
                 ("Обратная тарелка", _rev_crash)]),
]
KIT_INDEX = {name: fn for _cat, items in KIT for name, fn in items}
_KIT_CACHE: dict = {}
_FILE_CACHE: dict = {}


def kit_sample(name: str) -> np.ndarray:
    """Встроенный звук (стерео float32, нормирован к ~−1 дБ)."""
    s = _KIT_CACHE.get(name)
    if s is None:
        fn = KIT_INDEX.get(name)
        if fn is None:
            return np.zeros((64, 2), np.float32)
        m = np.asarray(fn(), np.float64)
        m /= max(1e-6, np.abs(m).max()) / 0.89
        k = min(64, len(m))
        m[-k:] *= np.linspace(1, 0, k)
        s = _st(m).astype(np.float32)
        _KIT_CACHE[name] = s
    return s


def sample_data(ref: str) -> np.ndarray:
    """Звук сэмплера: «kit:Имя» — встроенный, иначе путь к файлу (до 60 с)."""
    if not ref:
        return np.zeros((64, 2), np.float32)
    if ref.startswith("kit:"):
        return kit_sample(ref[4:])
    s = _FILE_CACHE.get(ref)
    if s is None:
        try:
            s = D.decode(ref, SR, 2, dur=60.0)
        except Exception:                        # noqa: BLE001
            s = np.zeros((64, 2), np.float32)
        if len(_FILE_CACHE) > 40:
            _FILE_CACHE.pop(next(iter(_FILE_CACHE)))
        _FILE_CACHE[ref] = s
    return s


class Sampler(Gen):
    KIND = "sampler"
    NAME = "Сэмплер"
    INFO = "Играет звук из кита или любой файл. Высота меняется нотой."
    ONE_SHOT = True
    PARAMS = [P("tune", "Тон", -24, 24, 0, "st", "int"), P("fine", "Подстройка", -100, 100, 0, "ц"),
              P("root", "Корневая нота", 24, 96, 60, "", "int"), P("keytrack", "Высота от ноты", 0, 1, 1, "", "bool"),
              P("att", "Атака", 0, 0.5, 0.0, "с"), P("hold", "Играть всю длину", 0, 1, 1, "", "bool"),
              P("rel", "Отпускание", 0.005, 2, 0.05, "с", "log"), P("start", "Начало", 0, 0.95, 0.0, "%"),
              P("rev", "Задом наперёд", 0, 1, 0, "", "bool"), P("vol", "Громкость", 0, 1, 0.8, "%")]
    PRESETS = {}

    def __init__(self, params=None):
        super().__init__(params)
        self.p.setdefault("sample", "kit:Бочка Punch")

    def key(self):
        return super().key() + "|" + str(self.p.get("sample"))

    def tail(self):
        return float(self.g("rel"))

    def render(self, pitch, vel, dur):
        s = sample_data(str(self.p.get("sample") or ""))
        st = float(self.g("start"))
        if st > 0:
            s = s[int(len(s) * st):]
        if self.g("rev"):
            s = s[::-1]
        semis = float(self.g("tune")) + float(self.g("fine")) / 100
        if self.g("keytrack"):
            semis += pitch - int(self.g("root"))
        r = 2 ** (semis / 12)
        if abs(r - 1) > 1e-4:
            n2 = max(16, int(len(s) / r))
            pos = np.arange(n2) * r
            s = np.stack([np.interp(pos, np.arange(len(s)), s[:, c]) for c in range(2)], 1)
        s = np.array(s, np.float64)
        n = len(s)
        if not self.g("hold"):
            n_note = int(dur * SR)
            if n_note < n:
                rel = float(self.g("rel"))
                m = min(n, n_note + int(rel * SR))
                s = s[:m]
                t = np.arange(m - n_note) / SR
                s[n_note:] *= np.exp(-t / (rel / 5))[:, None]
                n = m
        a = float(self.g("att"))
        if a > 0.0005:
            k = min(n, int(a * SR))
            s[:k] *= np.linspace(0, 1, k)[:, None]
        k = min(64, n)
        s[-k:] *= np.linspace(1, 0, k)[:, None]
        return (s * _vel(vel) * float(self.g("vol")) * 1.1).astype(np.float32)


GENERATORS = {c.KIND: c for c in (Synth3x, Supersaw, Keys, Pluck, FM, Bass808, Sampler)}


def make(kind: str, params: dict | None) -> Gen | None:
    cls = GENERATORS.get(kind)
    return cls(params) if cls else None


def instrument_presets():
    """Инструменты для браузера: (раздел, имя, генератор, параметры)."""
    out = []
    for kind, cls in GENERATORS.items():
        if kind == "sampler":
            continue
        for name, prm in cls.PRESETS.items():
            if name == "Init":
                continue
            out.append((cls.NAME, name, kind, dict(prm)))
    return out


DEFAULT_COLORS = ["#6c9ad8", "#d8a26c", "#8fc46c", "#d86c8f", "#b38fd8", "#6cd8c4", "#d8d36c", "#d87a6c",
                  "#7fb0a0", "#c08fd8", "#d8b46c", "#6cb4d8"]


import daw_wt  # noqa: E402,F401  — волновой синт регистрируется в GENERATORS
import daw_layers  # noqa: E402,F401
