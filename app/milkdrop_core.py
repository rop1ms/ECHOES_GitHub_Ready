# milkdrop_core.py
"""
Ядро MilkDrop-визуализации — ТОЛЬКО numpy (без Qt): можно гонять и
тестировать отдельно.

Как устроен настоящий MilkDrop и что повторено здесь
────────────────────────────────────────────────────
  1. Буфер обратной связи (feedback). Каждый кадр ПРОШЛЫЙ кадр
     перерисовывается через «сетку движения»: зум, поворот, сдвиг и
     варп координат. Всё, что когда-то было нарисовано, тянется,
     закручивается и «плавится».
  2. Затухание (decay): буфер чуть темнеет каждый кадр — хвосты гаснут.
  3. Волна (waveform): реальные сэмплы звука рисуются в буфер — это
     единственный «источник света», картинка буквально сделана из музыки.
  4. Звук управляет движением: bass / mid / treb относительно их долгого
     среднего (≈1.0 = как обычно, 1.6 = удар).
  5. Пресеты плавно перетекают друг в друга. Режимы смены: auto (раз в
     20–32 с), beat (на сильных ударах, не чаще раза в 9 с), lock (никогда).
  6. Цветовой ремап на выходе («composite shader»).

v2: ВСЕ пресеты асимметричные — без зеркал и калейдоскопов. Асимметрию
дают смещённый и блуждающий центр движения (cx/cy/orbit), анизотропный
зум (aniso), режимы варпа (вихрь, поток, рябь, турбулентность) и волны,
нарисованные не по центру. Отдельная группа — «глобальные» пресеты на
весь кадр: занавесы волн, спектр-пол, плазма, решётка точек, диагонали.

Бюджет: сетка ~288×180 → 5–9 мс на кадр в numpy в фоновом потоке.
"""

from __future__ import annotations

import colorsys
import math
import random

import numpy as np

try:                                                       # C++-ядро (echoes_fx.dll) — если есть
    import native_fx as _NF
    _NATIVE = _NF.available()
except Exception:                                          # noqa: BLE001
    _NF, _NATIVE = None, False


# ── параметры пресета ──────────────────────────────────────────────────────── #
#  zoom      — зум за кадр (>1 — затягивает к центру)
#  rot       — поворот за кадр, рад
#  warp      — сила варпа · wscale — частота · wspeed — скорость течения
#  wmode     — варп: 0 синус · 1 вихрь · 2 диагональный поток · 3 рябь · 4 турбулентность
#  decay     — затухание буфера за кадр
#  zexp      — зум зависит от радиуса (края быстрее/медленнее)
#  dx, dy    — дрейф за кадр
#  cx, cy    — центр движения (смещён от середины — асимметрия)
#  orbit     — амплитуда блуждания центра · ospeed — скорость блуждания
#  aniso     — растяжение зума по X относительно Y (1 — изотропно)
#  wave      — вид волны (см. _draw_wave): 0 круг · 1 линия · 2 XY · 3 две линии ·
#              4 лучи · 5 спираль · 6 искры · 7 боковой спектр · 8 орбиты ·
#              9 комета · ГЛОБАЛЬНЫЕ: 10 занавес · 11 спектр-пол · 12 плазма ·
#              13 решётка · 14 диагонали
#  wsize     — размер волны · wx, wy — положение волны
#  hue_speed — скорость смены цвета · hue0 — начальный оттенок
#  remap     — 0 чистый · 1 радуга · 2 соляризация · 3 хром · 4 дуотон ·
#              5 огонь · 6 лёд · 7 неон-полосы

_DEFAULTS = dict(zoom=1.02, rot=0.0, warp=0.4, wscale=2.0, wspeed=1.0, wmode=0,
                 decay=0.968, zexp=0.5, dx=0.0, dy=0.0, cx=0.0, cy=0.0, orbit=0.25,
                 ospeed=0.25, aniso=1.0, wave=0, wsize=0.30, wx=0.0, wy=0.0,
                 hue_speed=0.08, hue0=0.0, remap=1, glob=0)


def _P(name, **kw):
    d = dict(_DEFAULTS)
    d.update(kw)
    d["name"] = name
    d["glob"] = 1 if d["wave"] >= 10 else d.get("glob", 0)
    return d


PRESETS = [
    # ── смещённые туннели и вихри ──
    _P("off-axis tunnel", zoom=1.032, rot=0.008, warp=0.5, wscale=2.2, wspeed=0.9, cx=0.45, cy=-0.25,
       orbit=0.2, wave=0, wsize=0.30, wx=0.5, wy=-0.2, remap=1, zexp=0.6),
    _P("drain to the corner", zoom=1.045, rot=0.02, warp=0.3, wmode=1, cx=-0.9, cy=0.55, orbit=0.12,
       wave=5, wsize=0.45, wx=-0.6, wy=0.3, remap=4, hue0=0.75, decay=0.972),
    _P("lopsided vortex", zoom=1.018, rot=0.03, warp=0.9, wmode=1, wscale=1.6, cx=0.3, cy=0.35,
       orbit=0.35, wave=2, wsize=0.40, wx=0.35, wy=0.3, remap=2),
    _P("comet wake", zoom=1.012, rot=-0.006, warp=0.55, wmode=2, dx=0.004, dy=-0.003, cx=-0.5,
       wave=9, wsize=0.45, remap=1, decay=0.975),
    _P("orbit garden", zoom=1.022, rot=0.012, warp=0.35, wmode=4, cx=0.6, cy=-0.3, orbit=0.3,
       wave=8, wsize=0.5, wx=0.25, wy=-0.1, remap=6, hue_speed=0.05),
    _P("sideways spectrum", zoom=1.008, rot=0.0, warp=0.7, wmode=2, dx=0.012, cx=-1.2, orbit=0.1,
       aniso=1.04, wave=7, wsize=0.6, remap=7),
    _P("spiral off-center", zoom=1.028, rot=-0.025, warp=0.25, wmode=0, cx=-0.35, cy=0.4, orbit=0.25,
       wave=5, wsize=0.35, wx=-0.3, wy=0.35, remap=1, hue_speed=0.14),
    _P("ember drift", zoom=1.006, rot=0.004, warp=0.6, wmode=4, dy=-0.010, cx=0.2, cy=0.8, orbit=0.3,
       wave=6, wsize=0.6, wx=0.1, wy=0.6, remap=5, decay=0.976, hue0=0.02, hue_speed=0.02),
    _P("frost crawl", zoom=1.004, rot=-0.003, warp=0.8, wmode=3, wscale=2.6, cx=-0.7, cy=-0.5,
       orbit=0.2, wave=1, wsize=0.30, wy=-0.35, remap=6, decay=0.974),
    _P("acid scope askew", zoom=0.994, rot=0.018, warp=0.8, wscale=1.6, wspeed=0.7, cx=0.4,
       cy=0.2, orbit=0.3, wave=2, wsize=0.42, wx=0.4, wy=0.25, remap=2, decay=0.958),
    _P("chrome river bend", zoom=1.010, warp=1.1, wscale=2.6, wspeed=1.6, wmode=2, dx=0.006,
       dy=-0.002, cx=-0.3, wave=1, wsize=0.34, wy=0.3, remap=3, decay=0.972),
    _P("feathers leaning", zoom=1.024, rot=-0.004, warp=0.65, wscale=1.3, wspeed=0.5, zexp=1.5,
       dy=0.003, cx=0.5, cy=0.3, wave=3, wsize=0.30, wx=0.3, remap=1, decay=0.980),
    _P("plasma drain east", zoom=0.975, rot=0.030, warp=0.45, cx=0.8, cy=-0.1, zexp=-0.4,
       wave=2, wsize=0.38, wx=0.6, remap=2, decay=0.960),
    _P("neon spiral slip", zoom=1.035, rot=0.035, warp=0.15, wscale=3.5, wspeed=2.0, cx=-0.4,
       cy=-0.3, orbit=0.3, wave=4, wsize=0.24, wx=-0.45, wy=-0.3, remap=0, hue_speed=0.20),
    _P("heart of a red giant", zoom=1.050, rot=0.004, warp=0.4, wscale=2.8, cx=0.55, cy=0.45,
       orbit=0.15, wave=0, wsize=0.22, wx=0.5, wy=0.4, remap=5, hue0=0.0, hue_speed=0.03),
    _P("ripple pond", zoom=1.004, rot=0.002, warp=0.9, wmode=3, wscale=3.0, wspeed=1.4, cx=-0.5,
       cy=0.25, orbit=0.4, wave=8, wsize=0.35, wx=-0.4, wy=0.2, remap=4, hue0=0.55),
    _P("tilted horizon", zoom=1.015, rot=0.006, warp=0.5, wmode=2, aniso=1.03, cy=-0.6,
       wave=1, wsize=0.45, wy=-0.5, remap=7, hue_speed=0.11),
    _P("magnet sparks", zoom=1.03, rot=-0.015, warp=0.3, wmode=1, cx=0.7, cy=-0.5, orbit=0.2,
       wave=6, wsize=0.5, wx=0.5, wy=-0.4, remap=1, decay=0.966),
    _P("violet undertow", zoom=0.985, rot=-0.01, warp=0.7, wmode=4, cx=-0.6, cy=0.6, orbit=0.25,
       wave=5, wsize=0.4, wx=-0.4, wy=0.5, remap=4, hue0=0.78, hue_speed=0.03),
    _P("lazy lissajous", zoom=1.01, rot=0.009, warp=0.4, wmode=0, cx=0.25, cy=-0.45, orbit=0.45,
       ospeed=0.15, wave=9, wsize=0.55, remap=6, decay=0.978),
    _P("bent oscilloscope", zoom=1.02, rot=-0.02, warp=1.2, wmode=3, wscale=1.4, cx=-0.2, cy=0.5,
       wave=2, wsize=0.5, wx=-0.3, wy=0.4, remap=3),
    _P("hot pink current", zoom=1.012, warp=0.75, wmode=2, dx=-0.008, dy=0.004, cx=0.9,
       wave=3, wsize=0.32, wy=0.15, remap=4, hue0=0.9, hue_speed=0.02, decay=0.97),
    _P("cyclone eye", zoom=1.04, rot=0.045, warp=0.6, wmode=1, wscale=2.0, cx=-0.55, cy=-0.35,
       orbit=0.18, wave=4, wsize=0.28, wx=-0.55, wy=-0.35, remap=1, hue_speed=0.16),
    _P("liquid stairs", zoom=1.008, rot=0.0, warp=0.9, wmode=4, wscale=3.4, aniso=0.97, cx=0.4,
       cy=0.6, wave=7, wsize=0.5, remap=7, decay=0.97),
    _P("smoke signal", zoom=1.0, rot=0.003, warp=0.5, wmode=4, dy=-0.012, cx=-0.6, cy=0.7,
       orbit=0.2, wave=1, wsize=0.25, wx=-0.5, wy=0.7, remap=3, decay=0.982),
    _P("gravity well", zoom=1.06, rot=-0.008, warp=0.2, wmode=3, zexp=1.4, cx=0.75, cy=0.5,
       orbit=0.1, wave=6, wsize=0.7, remap=5, decay=0.962),

    # ── глобальные: картинка на весь кадр ──
    _P("[full] wave curtains", zoom=1.006, rot=0.0, warp=0.6, wmode=2, dy=-0.004, orbit=0.3,
       wave=10, wsize=0.18, remap=1, decay=0.962, hue_speed=0.07),
    _P("[full] spectrum floor", zoom=1.012, rot=0.0, warp=0.4, wmode=4, dy=-0.014, cx=0.3, cy=1.0,
       orbit=0.2, aniso=1.02, wave=11, wsize=0.9, remap=7, decay=0.968),
    _P("[full] plasma sea", zoom=1.004, rot=0.002, warp=0.9, wmode=4, wscale=2.2, cx=-0.4, cy=0.3,
       orbit=0.5, wave=12, wsize=1.0, remap=4, decay=0.95, hue_speed=0.05),
    _P("[full] lattice pulse", zoom=1.02, rot=0.006, warp=0.35, wmode=3, cx=0.5, cy=-0.4,
       orbit=0.4, wave=13, wsize=1.0, remap=1, decay=0.955, hue_speed=0.09),
    _P("[full] diagonal storm", zoom=1.01, rot=-0.004, warp=0.8, wmode=2, dx=0.006, dy=0.006,
       cx=-0.8, cy=-0.6, wave=14, wsize=0.25, remap=2, decay=0.96),
    _P("[full] aurora", zoom=1.003, rot=0.0, warp=1.0, wmode=4, wscale=1.4, dy=-0.006, cy=-0.9,
       orbit=0.4, wave=10, wsize=0.12, remap=6, decay=0.975, hue0=0.4, hue_speed=0.03),
    _P("[full] lava lamp", zoom=0.998, rot=0.0, warp=1.2, wmode=4, wscale=1.2, wspeed=0.6,
       dy=-0.004, cx=0.3, cy=0.5, orbit=0.6, ospeed=0.12, wave=12, wsize=1.0, remap=5, decay=0.955),
    _P("[full] equalizer rain", zoom=1.0, warp=0.3, wmode=2, dy=0.016, dx=-0.003, cy=-1.0,
       orbit=0.2, wave=11, wsize=0.7, remap=1, decay=0.962, hue_speed=0.12),
    _P("[full] starfield wind", zoom=1.035, rot=0.004, warp=0.2, wmode=0, cx=0.9, cy=-0.3,
       orbit=0.3, zexp=1.6, wave=13, wsize=1.0, remap=6, decay=0.958),
    _P("[full] neon grid melt", zoom=1.008, rot=-0.006, warp=0.9, wmode=3, wscale=2.8, cx=-0.5,
       cy=0.4, orbit=0.35, wave=13, wsize=1.0, remap=7, decay=0.962),
    _P("[full] crossfire", zoom=1.018, rot=0.012, warp=0.5, wmode=1, cx=0.6, cy=0.6, orbit=0.3,
       wave=14, wsize=0.3, remap=4, hue0=0.08, decay=0.962),
    _P("[full] tidal curtains", zoom=0.99, rot=0.0, warp=0.7, wmode=3, cx=-0.9, cy=0.2,
       orbit=0.25, wave=10, wsize=0.2, remap=3, decay=0.966),
    _P("[full] solar plasma", zoom=1.015, rot=0.01, warp=0.6, wmode=1, cx=0.7, cy=-0.5,
       orbit=0.3, wave=12, wsize=1.0, remap=1, decay=0.952, hue_speed=0.1),
]

# ── генератор: сотня+ дополнительных вариаций из «генов» ручных пресетов ── #
_ADJ = ["burning", "drifting", "broken", "velvet", "toxic", "glass", "midnight", "electric",
        "sunken", "restless", "crooked", "silent", "molten", "frozen", "hollow", "wild",
        "lonely", "feral", "liquid", "static", "violet", "golden", "haunted", "distant"]
_NOUN = ["comet", "harbor", "nebula", "engine", "garden", "signal", "reef", "tide", "orbit",
         "cathedral", "highway", "swamp", "satellite", "prism", "furnace", "dune", "storm",
         "lantern", "glacier", "circuit", "mirage", "abyss", "meadow", "ribbon"]


def _generate(n: int, seed: int = 4242) -> list[dict]:
    rng = random.Random(seed)
    base = PRESETS[:]
    out, names = [], {p["name"] for p in PRESETS}
    while len(out) < n:
        a, b = rng.sample(base, 2)
        d = {}
        for k in _DEFAULTS:
            src = a if rng.random() < 0.5 else b
            d[k] = src[k]
        for k in ("zoom", "rot", "warp", "wscale", "wspeed", "zexp", "decay"):
            d[k] = a[k] * 0.5 + b[k] * 0.5 + rng.uniform(-1, 1) * {
                "zoom": 0.01, "rot": 0.012, "warp": 0.25, "wscale": 0.8, "wspeed": 0.4,
                "zexp": 0.4, "decay": 0.006}[k]
        # асимметрия обязательна: центр всегда смещён и блуждает
        ang = rng.uniform(0, 2 * math.pi)
        rad = rng.uniform(0.3, 0.9)
        d["cx"], d["cy"] = math.cos(ang) * rad * 1.3, math.sin(ang) * rad
        d["orbit"] = rng.uniform(0.12, 0.45)
        d["ospeed"] = rng.uniform(0.12, 0.4)
        if d["wave"] < 10:
            d["wx"] = d["cx"] * rng.uniform(0.4, 0.9)
            d["wy"] = d["cy"] * rng.uniform(0.4, 0.9)
        d["aniso"] = rng.choice([1.0, 1.0, rng.uniform(0.96, 1.04)])
        d["hue0"] = rng.random()
        d["zoom"] = min(1.06, max(0.975, d["zoom"]))
        d["decay"] = min(0.982, max(0.95, d["decay"]))
        d["warp"] = max(0.1, d["warp"])
        name = f"{rng.choice(_ADJ)} {rng.choice(_NOUN)}"
        if d["wave"] >= 10:
            name = "[full] " + name
        if name in names:
            continue
        names.add(name)
        out.append(_P(name, **{k: v for k, v in d.items() if k not in ("name", "glob")}))
    return out


PRESETS += _generate(96)

_NUM_KEYS = ("zoom", "rot", "warp", "wscale", "wspeed", "decay", "zexp", "dx", "dy",
             "cx", "cy", "orbit", "ospeed", "aniso", "wsize", "wx", "wy", "hue_speed")
_DISCRETE_KEYS = ("wave", "remap", "wmode", "glob", "hue0")

MODES = ("auto", "beat", "lock")


def _hsv(h: float, s: float = 1.0, v: float = 1.0) -> np.ndarray:
    return np.array(colorsys.hsv_to_rgb(h % 1.0, s, v), dtype=np.float32)


def _hsv_arr(h: np.ndarray, s: float = 0.9, v: float = 1.0) -> np.ndarray:
    """Векторный HSV→RGB для массива оттенков (N,) → (N,3)."""
    h = (np.asarray(h, dtype=np.float32) % 1.0) * 6.0
    i = np.floor(h).astype(np.int32) % 6
    f = h - np.floor(h)
    p, q, t = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
    vv = np.full_like(f, v)
    r = np.choose(i, [vv, q, p, p, t, vv])
    g = np.choose(i, [t, vv, vv, q, p, p])
    b = np.choose(i, [p, p, t, vv, vv, q])
    return np.stack([r, g, b], axis=1).astype(np.float32)


class AudioBands:
    """bass / mid / treb как в MilkDrop: энергия полосы, делённая на её
    долгое среднее. ≈1.0 — обычный уровень, >1.4 — удар. *_att —
    сглаженные версии для плавного движения."""

    def __init__(self):
        self.bass = self.mid = self.treb = 0.0
        self.bass_att = self.mid_att = self.treb_att = 0.0
        self.vol = 0.0
        self.spec = np.zeros(48, dtype=np.float32)       # нормированный спектр для глобальных
        self._avg = np.array([1e-3, 1e-3, 1e-3], dtype=np.float64)
        self._smax = 1e-3
        self._win = None
        self._n = 0

    def feed(self, samples: np.ndarray | None, rate: int = 44100):
        if samples is None or len(samples) < 256:
            for k in ("bass", "mid", "treb", "vol"):
                setattr(self, k, getattr(self, k) * 0.85)
            self.spec *= 0.85
            self._att()
            return
        s = np.asarray(samples[-1024:], dtype=np.float32)
        n = len(s)
        if self._n != n:
            self._win = np.hanning(n).astype(np.float32)
            self._n = n
        mag = np.abs(np.fft.rfft(s * self._win))
        hz = rate / n
        b = lambda f0, f1: slice(max(1, int(f0 / hz)), max(2, int(f1 / hz)))
        e = np.array([mag[b(20, 250)].mean(), mag[b(250, 2500)].mean(),
                      mag[b(2500, 11000)].mean()], dtype=np.float64)
        loud = float(np.sqrt(np.mean(s * s)))
        self.vol = min(1.0, loud * 4.0)
        up = e > self._avg
        self._avg = np.where(up, self._avg * 0.9 + e * 0.1, self._avg * 0.992 + e * 0.008)
        self._avg = np.maximum(self._avg, 1e-4)
        rel = np.zeros(3) if loud < 1e-4 else np.clip(e / self._avg, 0.0, 3.0)
        self.bass, self.mid, self.treb = (float(x) for x in rel)
        # лог-спектр 48 полос 40 Гц…12 кГц
        edges = np.geomspace(40, min(12000, rate / 2 - 1), 49) / hz
        idx = np.clip(edges.astype(np.int32), 1, len(mag) - 1)
        sp = np.array([mag[idx[i]:max(idx[i] + 1, idx[i + 1])].mean() for i in range(48)],
                      dtype=np.float32)
        sp = np.sqrt(sp)
        self._smax = max(self._smax * 0.995, float(sp.max()) + 1e-6)
        new = sp / self._smax
        self.spec = np.maximum(new, self.spec * 0.82)
        self._att()

    def _att(self):
        k = 0.25
        self.bass_att += (self.bass - self.bass_att) * k
        self.mid_att += (self.mid - self.mid_att) * k
        self.treb_att += (self.treb - self.treb_att) * k


class MilkdropCore:
    PRESET_MIN_S = 20.0
    PRESET_MAX_S = 32.0
    BLEND_S = 4.0
    BEAT_MIN_S = 9.0

    def __init__(self, w: int = 320, h: int = 200, seed: int | None = None, native: bool | None = None):
        # native: попиксельная работа в C++ (сетка может быть в разы крупнее — вплоть до размера окна)
        self.native = _NATIVE if native is None else (bool(native) and _NATIVE)
        self.rng = random.Random(seed)
        self.nrng = np.random.default_rng(seed)
        self.audio = AudioBands()
        self.t = 0.0
        self.frame = 0
        self._wave = np.zeros(512, dtype=np.float32)
        self._beat_cool = 0.0
        self._flash = 0.0
        self._rings: list[list[float]] = []       # [x, y, radius, alpha, hue]
        self.mode = "auto"
        self._history: list[int] = []
        self._stamps: list = []

        self.preset_idx = self.rng.randrange(len(PRESETS))
        self.p = dict(PRESETS[self.preset_idx])
        self._from = dict(self.p)
        self._to = dict(self.p)
        self._blend = 1.0
        self._preset_t = 0.0
        self._preset_len = self.rng.uniform(self.PRESET_MIN_S, self.PRESET_MAX_S)
        self.preset_changed_at = 0.0

        self.w = self.h = 0
        self.resize(w, h)

    # ── геометрия ────────────────────────────────────────────────────── #

    def resize(self, w: int, h: int):
        w, h = int(max(32, w)), int(max(24, h))
        if (w, h) == (self.w, self.h):
            return
        old = getattr(self, "buf", None)
        self.w, self.h = w, h
        self.aspect = w / h
        ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
        self._X = (xs / (w - 1) * 2 - 1) * self.aspect     # [-aspect, aspect]
        self._Y = ys / (h - 1) * 2 - 1                      # [-1, 1]
        # низкое разрешение для плазмы (дальше растягивается) — дёшево
        self._pw, self._ph = max(8, w // 4), max(6, h // 4)
        py, px = np.mgrid[0:self._ph, 0:self._pw].astype(np.float32)
        self._PX = (px / (self._pw - 1) * 2 - 1) * self.aspect
        self._PY = py / (self._ph - 1) * 2 - 1
        if old is not None:
            yi = np.minimum((np.arange(h) * old.shape[0] / h).astype(np.int32), old.shape[0] - 1)
            xi = np.minimum((np.arange(w) * old.shape[1] / w).astype(np.int32), old.shape[1] - 1)
            self.buf = np.ascontiguousarray(old[yi][:, xi])
        else:
            self.buf = np.zeros((h, w, 3), dtype=np.float32)
        # толщина линий и плотность точек — относительно исходной сетки 288 точек:
        # на крупной сетке картинка та же, только чётче
        self._tk = max(1.0, w / 288.0)
        if self.native:
            self._buf2 = np.empty_like(self.buf)

    # ── пресеты ──────────────────────────────────────────────────────── #

    def preset_name(self) -> str:
        return PRESETS[self.preset_idx]["name"]

    def preset_count(self) -> int:
        return len(PRESETS)

    def set_mode(self, mode: str):
        if mode in MODES:
            self.mode = mode
            self._preset_t = 0.0

    def find_preset(self, name: str) -> int:
        for i, p in enumerate(PRESETS):
            if p["name"] == name:
                return i
        return -1

    def next_preset(self, idx: int | None = None, record: bool = True):
        if idx is None:
            choices = [i for i in range(len(PRESETS)) if i != self.preset_idx
                       and i not in self._history[-12:]]
            idx = self.rng.choice(choices or range(len(PRESETS)))
        if record:
            self._history.append(self.preset_idx)
            self._history = self._history[-40:]
        self.preset_idx = idx % len(PRESETS)
        self._from = dict(self.p)
        self._to = dict(PRESETS[self.preset_idx])
        for k, amt in (("zoom", 0.004), ("rot", 0.005), ("warp", 0.12), ("wscale", 0.3)):
            self._to[k] += self.rng.uniform(-amt, amt)
        self._blend = 0.0
        self._preset_t = 0.0
        self._preset_len = self.rng.uniform(self.PRESET_MIN_S, self.PRESET_MAX_S)
        self.preset_changed_at = self.t

    def prev_preset(self):
        if self._history:
            self.next_preset(self._history.pop(), record=False)

    def _update_preset(self, dt: float):
        self._preset_t += dt
        if self.mode == "auto" and self._preset_t >= self._preset_len:
            self.next_preset()
        elif self.mode == "beat" and self._preset_t >= self.BEAT_MIN_S:
            a = self.audio
            # сильный удар после затишья или просто очень сильный — смена «в долю»
            if (a.bass > 1.75 and a.bass > a.bass_att * 1.25) or self._preset_t > 45:
                self.next_preset()
        if self._blend < 1.0:
            self._blend = min(1.0, self._blend + dt / self.BLEND_S)
            k = self._blend * self._blend * (3 - 2 * self._blend)
            for key in _NUM_KEYS:
                self.p[key] = self._from[key] * (1 - k) + self._to[key] * k
            src = self._to if self._blend >= 0.5 else self._from
            for key in _DISCRETE_KEYS:
                self.p[key] = src[key]

    # ── внешние «штампы» (слова текста) ──────────────────────────────── #

    def stamp(self, mask: np.ndarray, x: float, y: float, color):
        """Вжечь маску (h×w, 0..1) в буфер: x,y — левый верх в долях кадра.
        Дальше обратная связь сама растянет и расплавит её — как в MilkDrop."""
        self._stamps.append((np.asarray(mask, dtype=np.float32), float(x), float(y),
                             np.asarray(color, dtype=np.float32)))

    def _apply_stamps(self):
        if self.native:
            for mask, x, y, col in self._stamps:
                _NF.milk_stamp(self.buf, mask, int(x * self.w), int(y * self.h), col)
            self._stamps.clear()
            return
        for mask, x, y, col in self._stamps:
            mh, mw = mask.shape
            x0, y0 = int(x * self.w), int(y * self.h)
            xa, ya = max(0, x0), max(0, y0)
            xb, yb = min(self.w, x0 + mw), min(self.h, y0 + mh)
            if xb <= xa or yb <= ya:
                continue
            m = mask[ya - y0:yb - y0, xa - x0:xb - x0, None]
            region = self.buf[ya:yb, xa:xb]
            np.maximum(region, m * col, out=region)
        self._stamps.clear()

    # ── шаг ──────────────────────────────────────────────────────────── #

    def feed(self, samples: np.ndarray | None, rate: int = 44100):
        self.audio.feed(samples, rate)
        if samples is not None and len(samples) >= 512:
            s = np.asarray(samples[-512:], dtype=np.float32)
            peak = float(np.max(np.abs(s)))
            self._wave = s / max(peak, 0.08)
        else:
            self._wave *= 0.8

    def _center(self):
        p = self.p
        t = self.t * p["ospeed"]
        # блуждание по несоизмеримым частотам — центр никогда не повторяет путь
        cx = p["cx"] + p["orbit"] * (math.sin(t * 1.0) * 0.8 + math.sin(t * 2.37 + 1.3) * 0.35)
        cy = p["cy"] + p["orbit"] * 0.7 * (math.cos(t * 0.83 + 0.4) * 0.8 + math.sin(t * 1.91) * 0.3)
        return cx, cy

    def step(self, dt: float):
        dt = float(min(0.1, max(0.001, dt)))
        f = dt * 30.0
        self.t += dt
        self.frame += 1
        a = self.audio
        p = self.p
        self._update_preset(dt)
        cx, cy = self._center()

        # ── удар баса: вспышка + кольцо в случайном месте ──
        self._beat_cool -= dt
        if a.bass > 1.45 and a.bass > a.bass_att * 1.12 and self._beat_cool <= 0:
            self._beat_cool = 0.22
            self._flash = min(0.35, 0.10 + (a.bass - 1.4) * 0.25)
            rx = cx * 0.5 + self.rng.uniform(-0.6, 0.6) * self.aspect
            ry = cy * 0.5 + self.rng.uniform(-0.6, 0.6)
            self._rings.append([rx, ry, 0.05, 0.9, self.t * p["hue_speed"] + p["hue0"] + 0.5])
            if len(self._rings) > 6:
                self._rings.pop(0)

        # ── 1) сетка движения вокруг смещённого блуждающего центра ──
        zoom = p["zoom"] + (a.bass_att - 1.0) * 0.018 * (a.bass_att > 0.2)
        lz = math.log(max(0.5, zoom)) * f
        decay = min(0.995, p["decay"] + 0.006 * min(1.0, a.vol))
        if self.native:
            rot = (p["rot"] + (a.mid_att - 1.0) * 0.008 * (a.mid_att > 0.2)) * f
            flash = 1.0
            if self._flash > 0.002:
                flash = 1.0 + self._flash * 0.5
                self._flash *= 0.55 ** f
            _NF.milk_warp(self.buf, self._buf2, aspect=self.aspect, cx=cx, cy=cy, lz=lz, zexp=p["zexp"],
                          rot_c=math.cos(rot), rot_s=math.sin(rot), an=p["aniso"] ** f,
                          wa=p["warp"] * (0.012 + 0.02 * min(2.0, a.treb_att)) * f,
                          ws=p["wscale"], wt=self.t * p["wspeed"], wmode=int(p["wmode"]),
                          addx=cx + p["dx"] * f * (0.6 + 0.4 * math.sin(self.t * 0.21)),
                          addy=cy + p["dy"] * f, decay=decay ** f, sub=0.011 * f, flash=flash)
            self.buf, self._buf2 = self._buf2, self.buf
        else:
            self._warp_numpy(a, p, cx, cy, f, lz, decay)
        # ── 3) волна, кольца, штампы ──
        self._draw_wave(cx, cy, f)
        self._draw_rings(dt)
        if self._stamps:
            self._apply_stamps()
        if self.native:
            _NF.milk_clip(self.buf)
        else:
            np.clip(self.buf, 0.0, 1.0, out=self.buf)

    def _warp_numpy(self, a, p, cx, cy, f, lz, decay):
        Xc = self._X - np.float32(cx)
        Yc = self._Y - np.float32(cy)
        R = np.sqrt(Xc * Xc + Yc * Yc)
        scale = np.exp(R * np.float32(-lz * p["zexp"]) + np.float32(-lz + lz * p["zexp"] * 0.6))
        rot = (p["rot"] + (a.mid_att - 1.0) * 0.008 * (a.mid_att > 0.2)) * f
        c, s = math.cos(rot), math.sin(rot)
        an = np.float32(p["aniso"] ** f)
        u = (Xc * c - Yc * s) * scale * an
        v = (Xc * s + Yc * c) * scale / an
        wa = p["warp"] * (0.012 + 0.02 * min(2.0, a.treb_att)) * f
        ws, wt = p["wscale"], self.t * p["wspeed"]
        wm = int(p["wmode"])
        if wm == 1:                                   # вихрь вокруг центра
            ang = (wa * 6.0) * np.exp(-R * R * 1.6)
            ca, sa = np.cos(ang), np.sin(ang)
            u, v = u * ca - v * sa, u * sa + v * ca
            u += wa * 0.4 * np.sin(Yc * ws + wt)
        elif wm == 2:                                 # диагональный поток
            u += wa * np.sin(self._Y * ws + self._X * 0.6 * ws + wt * 1.1)
            v += wa * 0.7 * np.cos(self._X * ws * 1.3 - wt * 0.6) + wa * 0.15
        elif wm == 3:                                 # рябь от центра
            rip = wa * 1.4 * np.sin(R * ws * 3.0 - wt * 3.0) / (R + 0.15)
            u += Xc * rip
            v += Yc * rip
        elif wm == 4:                                 # турбулентность (две октавы)
            u += wa * (np.sin(self._Y * ws * 1.17 + wt * 1.3 + 0.7)
                       + 0.5 * np.sin(self._X * ws * 2.71 - wt * 0.9 + self._Y * 1.3))
            v += wa * (np.cos(self._X * ws * 0.93 - wt * 1.07)
                       + 0.5 * np.cos(self._Y * ws * 2.33 + wt * 1.6 + self._X * 0.8))
        else:                                         # синус (несимметричные фазы)
            u += wa * np.sin(self._Y * ws + wt * 1.13 + 0.9)
            v += wa * np.cos(self._X * ws * 0.9 + wt * 0.87 + 0.3)
        u += np.float32(cx + p["dx"] * f * (0.6 + 0.4 * math.sin(self.t * 0.21)))
        v += np.float32(cy + p["dy"] * f)

        # в пиксели прошлого кадра + билинейная выборка (края — clamp)
        px = (u / self.aspect * 0.5 + 0.5) * (self.w - 1)
        py = (v * 0.5 + 0.5) * (self.h - 1)
        np.clip(px, 0, self.w - 1.001, out=px)
        np.clip(py, 0, self.h - 1.001, out=py)
        x0 = px.astype(np.int32)
        y0 = py.astype(np.int32)
        fx = (px - x0).reshape(-1, 1)
        fy = (py - y0).reshape(-1, 1)
        i00 = (y0 * self.w + x0).ravel()
        flat = self.buf.reshape(-1, 3)
        a00 = np.take(flat, i00, axis=0)
        a01 = np.take(flat, i00 + 1, axis=0)
        a10 = np.take(flat, i00 + self.w, axis=0)
        a11 = np.take(flat, i00 + self.w + 1, axis=0)
        a00 += (a01 - a00) * fx
        a10 += (a11 - a10) * fx
        a00 += (a10 - a00) * fy
        nb = a00.reshape(self.h, self.w, 3)

        # ── 2) затухание ──
        nb *= decay ** f
        nb -= 0.011 * f
        np.maximum(nb, 0.0, out=nb)

        if self._flash > 0.002:
            nb *= 1.0 + self._flash * 0.5
            self._flash *= 0.55 ** f

        self.buf = nb

    # ── рисование в буфер ────────────────────────────────────────────── #

    def _plot(self, xs: np.ndarray, ys: np.ndarray, col: np.ndarray, thick: int = 2,
              dense: bool = True):
        """Точки в нормированных координатах (x∈[-aspect,aspect], y∈[-1,1]).
        col — один цвет (3,) или цвет на каждую точку (N,3) (тогда dense=False)."""
        if self.native:
            th = max(thick, int(round(thick * self._tk)))
            _NF.milk_plot(self.buf, self.aspect, xs, ys, col, th, dense and col.ndim == 1)
            return
        if dense and len(xs) > 1:
            k = 3
            t = np.linspace(0, len(xs) - 1, len(xs) * k)
            i0 = np.floor(t).astype(np.int32)
            i1 = np.minimum(i0 + 1, len(xs) - 1)
            fr = (t - i0).astype(np.float32)
            xs = xs[i0] * (1 - fr) + xs[i1] * fr
            ys = ys[i0] * (1 - fr) + ys[i1] * fr
        pxi = ((xs / self.aspect * 0.5 + 0.5) * (self.w - 1)).astype(np.int32)
        pyi = ((ys * 0.5 + 0.5) * (self.h - 1)).astype(np.int32)
        ok = (pxi >= 0) & (pxi < self.w - thick) & (pyi >= 0) & (pyi < self.h - thick)
        pxi, pyi = pxi[ok], pyi[ok]
        if col.ndim == 2:
            col = col[ok]
        b = self.buf
        for oy in range(thick):
            for ox in range(thick):
                yy, xx = pyi + oy, pxi + ox
                b[yy, xx] = np.maximum(b[yy, xx], col)

    def _k(self, k: int) -> int:
        """Точек на луч спектра: на крупной сетке больше, чтобы лучи не рассыпались в пунктир."""
        return int(k * self._tk) if self.native else k

    def _draw_wave(self, cx: float, cy: float, f: float):
        a = self.audio
        p = self.p
        w = self._wave
        if not np.any(np.abs(w) > 1e-3) and a.vol < 0.01:
            w = 0.15 * np.sin(np.linspace(0, 6 * math.pi, 512) + self.t * 2).astype(np.float32)
        hue = self.t * p["hue_speed"] + p["hue0"]
        bright = 0.55 + 0.65 * min(1.0, a.vol * 1.5 + 0.2)
        col = _hsv(hue, 0.85, 1.0) * bright
        col2 = _hsv(hue + 0.37, 0.85, 1.0) * bright
        size = p["wsize"] * (1.0 + 0.25 * (a.bass_att - 1.0) * (a.bass_att > 0.2))
        mode = int(p["wave"])
        n = 256
        ww = w[:: max(1, len(w) // n)][:n]
        # волна «пристёгнута» к своему месту и немного тянется за центром движения
        ox = p["wx"] + 0.35 * (cx - p["cx"])
        oy = p["wy"] + 0.35 * (cy - p["cy"])
        A = self.aspect

        if mode == 0:                                    # круг (не в центре)
            ang = np.linspace(0, 2 * math.pi, n, dtype=np.float32) + self.t * 0.3
            r = size * (1.0 + 0.45 * ww)
            r[-8:] = r[-8:] * np.linspace(1, 0, 8) + r[0] * np.linspace(0, 1, 8)
            self._plot(ox + np.cos(ang) * r * 1.15, oy + np.sin(ang) * r * 0.85, col, 2)
        elif mode == 1:                                  # линия с наклоном
            xs = np.linspace(-A * 0.95, A * 0.95, n, dtype=np.float32)
            ys = ww * size
            ang = 0.25 + math.sin(self.t * 0.23) * 0.5
            c, s = math.cos(ang), math.sin(ang)
            self._plot(ox + xs * c - ys * s, oy + xs * s + ys * c, col, 2)
        elif mode == 2:                                  # XY-осциллограф
            lag = 13
            xs = w[:-lag:2] * size * 1.6
            ys = w[lag::2] * size * 1.2
            self._plot(ox + xs, oy + ys, col, 2)
        elif mode == 3:                                  # две несимметричные линии
            xs = np.linspace(-A * 0.95, A * 0.95, n, dtype=np.float32)
            off = 0.3 + 0.1 * math.sin(self.t * 0.5)
            self._plot(xs, oy + off + ww * size * 0.6 + xs * 0.12, col, 2)
            self._plot(xs * 0.8 + 0.2, oy - off * 0.6 - ww[::-1] * size * 0.4, col2, 2)
        elif mode == 4:                                  # лучи (спектр по кругу)
            mag = a.spec[::2][:24]
            nr, k = len(mag), self._k(20)
            ang = (np.linspace(0, 2 * math.pi, nr, endpoint=False) + self.t * 0.4).astype(np.float32)
            frac = np.linspace(0, 1, k, dtype=np.float32)[None, :]
            r0 = size * 0.25
            rr = r0 + frac * (size * (0.3 + 1.4 * mag)[:, None] - r0)
            xs = (ox + np.cos(ang)[:, None] * rr).ravel()
            ys = (oy + np.sin(ang)[:, None] * rr * 0.8).ravel()
            cols = _hsv_arr(hue + np.arange(nr, dtype=np.float32) / nr) * bright
            self._plot(xs, ys, np.repeat(cols, k, axis=0), 2, dense=False)
        elif mode == 5:                                  # спираль
            th = np.linspace(0, 5 * math.pi, n, dtype=np.float32)
            r = size * (0.08 + th / (5 * math.pi)) * (1.0 + 0.35 * ww)
            ang = th + self.t * 0.9
            self._plot(ox + np.cos(ang) * r * 1.2, oy + np.sin(ang) * r, col, 2)
        elif mode == 6:                                  # искры от излучателя
            cnt = int(30 + 140 * min(1.5, a.treb_att) * min(1.0, a.vol * 2 + 0.1))
            ex = ox + 0.25 * math.sin(self.t * 0.7)
            ey = oy + 0.2 * math.cos(self.t * 0.53)
            ang = self.nrng.uniform(0, 2 * math.pi, cnt).astype(np.float32)
            rad = (self.nrng.random(cnt).astype(np.float32) ** 2) * size * (0.6 + a.bass_att * 0.5)
            xs = ex + np.cos(ang) * rad * 1.3
            ys = ey + np.sin(ang) * rad
            cols = _hsv_arr(hue + self.nrng.random(cnt) * 0.25) * bright
            self._plot(xs, ys, cols, 2, dense=False)
        elif mode == 7:                                  # спектр от левого края
            mag = a.spec[:40]
            nb_, k = len(mag), self._k(16)
            ys0 = np.linspace(-0.9, 0.9, nb_, dtype=np.float32)
            frac = np.linspace(0, 1, k, dtype=np.float32)[None, :]
            x0 = -A * 0.98 + (ox + A) * 0.15
            xs = (x0 + frac * (size * 2.2 * mag)[:, None] * A).ravel()
            ys = np.repeat(ys0 + oy * 0.2, k)
            cols = _hsv_arr(hue + np.linspace(0, 0.5, nb_)) * bright
            self._plot(xs, ys, np.repeat(cols, k, axis=0), 2, dense=False)
        elif mode == 8:                                  # орбиты
            bands = (a.bass_att, a.mid_att, a.treb_att, a.bass, a.mid)
            for i, bnd in enumerate(bands):
                rad_o = size * (0.35 + i * 0.28)
                ph = self.t * (0.6 + i * 0.37) * (1 if i % 2 else -1) + i
                px_ = ox + math.cos(ph) * rad_o * 1.3
                py_ = oy + math.sin(ph) * rad_o * 0.8
                rr = 0.03 + 0.05 * min(2.0, bnd)
                ang = np.linspace(0, 2 * math.pi, 48, dtype=np.float32)
                self._plot(px_ + np.cos(ang) * rr, py_ + np.sin(ang) * rr,
                           _hsv(hue + i * 0.13, 0.85, 1.0) * bright, 2)
        elif mode == 9:                                  # комета-лиссажу
            tt = self.t + np.linspace(-0.6, 0, n, dtype=np.float32)
            xs = ox + np.sin(tt * 1.3) * size * 1.6 + ww * 0.05
            ys = oy + np.sin(tt * 1.9 + 0.7) * size + ww[::-1] * 0.05
            fade = np.linspace(0.15, 1.0, n, dtype=np.float32)[:, None]
            cols = _hsv_arr(hue + np.linspace(0, 0.3, n)) * bright * fade
            self._plot(xs, ys, cols, 2, dense=False)
            self._plot(xs[-40:], ys[-40:], col, 3)

        # ── ГЛОБАЛЬНЫЕ: рисунок на весь кадр ──
        elif mode == 10:                                 # занавес волн
            xs = np.linspace(-A, A, n, dtype=np.float32)
            rows = 6
            for i in range(rows):
                yb = -0.92 + 1.84 * (i + 0.5) / rows + 0.08 * math.sin(self.t * 0.4 + i * 1.7)
                seg = np.roll(ww, i * 37)
                amp = size * (0.6 + 0.8 * float(a.spec[i * 7 % 48]))
                ys = yb + seg * amp + 0.05 * np.sin(xs * (1.3 + i * 0.4) + self.t * (0.5 + i * 0.13))
                self._plot(xs, ys, _hsv(hue + i / rows * 0.6, 0.85, 1.0) * bright, 2)
        elif mode == 11:                                 # спектр-пол на всю ширину
            mag = a.spec
            nb_, k = len(mag), self._k(18)
            xs0 = np.linspace(-A * 0.98, A * 0.98, nb_, dtype=np.float32)
            frac = np.linspace(0, 1, k, dtype=np.float32)[None, :]
            ys = (0.98 - frac * (size * 1.9 * mag)[:, None]).ravel()
            xs = np.repeat(xs0, k)
            cols = _hsv_arr(hue + np.linspace(0, 0.7, nb_)) * bright
            self._plot(xs, ys, np.repeat(cols, k, axis=0), 2, dense=False)
        elif mode == 12 and self.native:                 # плазма (C++)
            k = 0.08 + 0.22 * min(1.5, a.vol * 2.0) + 0.1 * max(0.0, a.bass - 1.0)
            _NF.milk_plasma(self.buf, self.aspect, self.t, cx, cy, k, _hsv(hue, 0.8, 1.0))
            self._plot(ox + np.linspace(-A, A, n) * 0.9, oy + ww * size * 0.35, col * 0.8, 2)
        elif mode == 12:                                 # плазма
            PX, PY = self._PX, self._PY
            t = self.t
            fld = (np.sin(PX * 2.1 + t * 0.9) + np.sin(PY * 3.3 - t * 1.2 + PX * 0.7)
                   + np.sin((PX + PY * 1.4) * 2.6 + t * 0.6) + np.sin(np.sqrt(
                       (PX - cx) ** 2 + (PY - cy) ** 2) * 5.0 - t * 2.0))
            band = np.clip(1.0 - np.abs(fld - 1.2 * math.sin(t * 0.3)) * 2.2, 0.0, 1.0)
            k = 0.08 + 0.22 * min(1.5, a.vol * 2.0) + 0.1 * max(0.0, a.bass - 1.0)
            ry = np.minimum((np.arange(self.h) * self._ph // self.h), self._ph - 1)
            rx = np.minimum((np.arange(self.w) * self._pw // self.w), self._pw - 1)
            big = band[ry][:, rx][..., None]
            colf = _hsv(hue, 0.8, 1.0) * k
            np.maximum(self.buf, big * colf, out=self.buf)
            self._plot(ox + np.linspace(-A, A, n) * 0.9, oy + ww * size * 0.35, col * 0.8, 2)
        elif mode == 13:                                 # решётка точек
            gx, gy = 26, 15
            xs0 = np.linspace(-A * 0.95, A * 0.95, gx, dtype=np.float32)
            ys0 = np.linspace(-0.92, 0.92, gy, dtype=np.float32)
            Xg, Yg = np.meshgrid(xs0, ys0)
            bi = ((Xg + A) / (2 * A) * 47).astype(np.int32)
            lev = a.spec[bi]
            jit = 0.04 * np.sin(Yg * 5.0 + self.t * 2.0 + Xg * 1.7)
            show = lev > 0.25 + 0.15 * np.sin(Xg * 3 + Yg * 2 + self.t)
            xs = (Xg + jit)[show]
            ys = (Yg + jit * 0.7)[show]
            cols = _hsv_arr(hue + (Yg[show] + 1) * 0.25) * bright * np.clip(lev[show], 0.3, 1.0)[:, None]
            self._plot(xs, ys, cols, 3, dense=False)
        else:                                            # 14: диагонали через весь кадр
            m = 3
            for i in range(m):
                ang = 0.5 + i * 0.9 + math.sin(self.t * 0.17 + i) * 0.4
                L = np.linspace(-2.2, 2.2, n, dtype=np.float32)
                c, s = math.cos(ang), math.sin(ang)
                off = (i - 1) * 0.45 + 0.15 * math.sin(self.t * 0.31 + i * 2)
                seg = np.roll(ww, i * 61) * size
                xs = L * c - (off + seg) * s + cx * 0.3
                ys = L * s + (off + seg) * c + cy * 0.3
                self._plot(xs, ys, _hsv(hue + i * 0.21, 0.85, 1.0) * bright, 2)

    def _draw_rings(self, dt: float):
        keep = []
        for ring in self._rings:
            x, y, r, alpha, hue = ring
            ang = np.linspace(0, 2 * math.pi, 160, dtype=np.float32)
            self._plot(x + np.cos(ang) * r * 1.2, y + np.sin(ang) * r * 0.9,
                       _hsv(hue, 0.9, 1.0) * alpha, 2)
            ring[2] += dt * 1.1
            ring[3] *= 0.86 ** (dt * 30)
            if ring[3] > 0.05 and ring[2] < 1.6:
                keep.append(ring)
        self._rings = keep

    # ── выход ────────────────────────────────────────────────────────── #

    _LUT_N = 512

    def current_hue(self) -> float:
        return (self.t * self.p["hue_speed"] + self.p["hue0"]) % 1.0

    def _remap_lut(self, mode: int) -> np.ndarray:
        """Таблица «яркость → цвет» на текущий кадр (512×3)."""
        l = np.linspace(0.0, 1.0, self._LUT_N, dtype=np.float32)[:, None]
        if mode == 1:                                     # радужные контуры
            ph = np.array([0.0, 2.1, 4.2], dtype=np.float32) + np.float32(self.t * 0.35)
            return (0.5 + 0.5 * np.sin(l * 11.0 + ph)) * np.clip((l - 0.10) * 2.6, 0.0, 1.0) * 0.85
        if mode == 4:                                     # дуотон
            h = self.current_hue()
            c1, c2 = _hsv(h, 0.9, 1.0), _hsv(h + 0.42, 0.75, 1.0)
            k = np.clip(l * 1.6, 0, 1)
            return (c1 * (1 - k) + c2 * k) * np.clip(l * 2.4, 0, 1) * (0.75 + 0.25 * np.sin(l * 18 + self.t))
        if mode == 5:                                     # огонь
            r = np.clip(l * 3.0, 0, 1)
            g = np.clip(l * 3.0 - 0.9, 0, 1)
            b = np.clip(l * 3.0 - 2.0, 0, 1)
            return np.concatenate([r, g * 0.85, b * 0.7], axis=1)
        if mode == 6:                                     # лёд
            r = np.clip(l * 2.6 - 1.2, 0, 1) + 0.25 * np.clip(np.sin(l * 9 + self.t * 0.5), 0, 1) * l
            g = np.clip(l * 2.2 - 0.35, 0, 1)
            b = np.clip(l * 2.5, 0, 1)
            return np.concatenate([r, g, b], axis=1) * 0.95
        if mode == 7:                                     # неоновые полосы
            q = np.floor(l * 9.0) / 9.0
            ph = np.array([0.0, 2.5, 4.4], dtype=np.float32) + np.float32(self.t * 0.8)
            return (0.5 + 0.5 * np.sin(q * 17.0 + ph)) * np.clip((l - 0.06) * 3.0, 0.0, 1.0)
        edge = np.abs(np.sin(l * 9.0))                    # 3 — хром
        return edge * np.array([0.55, 0.62, 0.75], dtype=np.float32) * np.clip(l * 2.5 - 0.1, 0.0, 1.0)

    def frame_argb32(self) -> np.ndarray:
        """Кадр HxW uint32 0xFFRRGGBB (QImage.Format_RGB32) с цветовым ремапом."""
        if self.native:
            mode = int(self.p["remap"])
            if mode in (1, 3, 4, 5, 6, 7):
                lut = self._remap_lut(mode)
                addk = {1: 0.35, 3: 0.4, 4: 0.3, 5: 0.15, 6: 0.25, 7: 0.3}[mode]
                return _NF.milk_remap(self.buf, mode, lut, addk)
            return _NF.milk_remap(self.buf, 2 if mode == 2 else 0, None, 0.0)
        c = np.minimum(self.buf, 1.0)
        mode = int(self.p["remap"])
        if mode in (1, 3, 4, 5, 6, 7):
            lum = c[..., 0] + c[..., 1] + c[..., 2]
            idx = (lum * ((self._LUT_N - 1) / 3.0)).astype(np.int32)
            out = np.take(self._remap_lut(mode), idx, axis=0)
            out += c * {1: 0.35, 3: 0.4, 4: 0.3, 5: 0.15, 6: 0.25, 7: 0.3}[mode]
        elif mode == 2:
            out = 1.0 - np.abs(2.0 * c - 1.0)
            out *= 1.25
            out += c * 0.25
        else:
            out = np.sqrt(c) * 0.35 + c * 0.7
        np.clip(out * 255.0, 0, 255, out=out)
        u8 = out.astype(np.uint32)
        return (np.uint32(0xFF000000) | (u8[..., 0] << 16) | (u8[..., 1] << 8) | u8[..., 2])
