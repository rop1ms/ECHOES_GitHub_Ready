# daw_wt.py
"""
Волновой синтезатор студии (в духе Serum / Vital): два осциллятора по волновым таблицам,
плавный переход по кадрам таблицы (позиция, её огибающая и LFO), унисон до 16 голосов,
суб-осциллятор, шум, изгиб волны, фильтр с перегрузом и огибающей, LFO на высоту / срез /
позицию / громкость, глиссандо. Плюс большая библиотека пресетов по категориям.

Таблица — 32 кадра по одному периоду волны. Кадры строятся один раз (по формулам: аналоговые,
ШИМ, гласные, FM, синхронизация, орган…), для каждой ноты гармоники выше Найквиста
обнуляются — без «цифровой грязи» на высоких нотах. Нота рисуется целиком и кэшируется
движком, как у остальных генераторов.
"""
from __future__ import annotations

import math

import numpy as np

import daw_dsp as D
from daw_dsp import SR
from daw_fx import P
import daw_inst as I

TWO_PI = 2 * np.pi
N = 2048                                  # отсчётов на период
FR = 32                                   # кадров в таблице
H = N // 2

TABLES = ["Аналог (синус → пила)", "Квадрат ШИМ", "Треугольник → квадрат", "Гласные (а-э-и-о-у)", "Хор «ааа»",
          "Гармонический свип", "Цифровой FM", "Жёсткая синхронизация", "Рык (гроул)", "Орган (регистры)",
          "Стекло", "Шумная пыль", "Сжатый синус", "Пила с вырезом", "Ступеньки (бит-краш)", "Супер-гармоники"]

LFO_SHAPES = ["Синус", "Треугольник", "Квадрат", "Пила вниз", "Случайный"]


def _cycle(kind: int, f: float) -> np.ndarray:
    """Один период волны таблицы kind в кадре f (0..1)."""
    t = np.arange(N) / N
    h = np.arange(1, 161)
    if kind == 0:                                   # синус → пила
        a = np.where(h == 1, 1.0, f / h)
        return np.sin(TWO_PI * np.outer(t, h)) @ a
    if kind == 1:                                   # ШИМ 50 % → 5 %
        w = 0.5 - 0.45 * f
        return np.where(t < w, 1.0, -1.0) - (2 * w - 1)
    if kind == 2:                                   # треугольник → квадрат
        tri = 1 - 4 * np.abs(t - 0.5)
        sq = np.where(t < 0.5, 1.0, -1.0)
        return np.roll(tri, N // 4) * (1 - f) + sq * f
    if kind in (3, 4):                              # гласные: форманты в гармониках голоса ~130 Гц
        vowels = [(730, 1090, 2440), (530, 1840, 2480), (270, 2290, 3010), (570, 840, 2410), (300, 870, 2240)]
        if kind == 4:
            pos = 0.0 + 0.3 * f
        else:
            pos = f * (len(vowels) - 1)
        i0 = int(min(len(vowels) - 2, math.floor(pos)))
        fr = pos - i0
        F = [vowels[i0][j] * (1 - fr) + vowels[i0 + 1][j] * fr for j in range(3)]
        base = 130.0
        a = np.zeros(len(h))
        for j, fc in enumerate(F):
            bw = 80 + 40 * j
            a += (0.9 ** j) * np.exp(-0.5 * ((h * base - fc) / bw) ** 2)
        a = a / np.sqrt(h) + 0.02 / h
        ph = np.where(kind == 4, (h * 1.7) % TWO_PI, 0.0)
        return np.sin(TWO_PI * np.outer(t, h) + ph) @ a
    if kind == 5:                                   # пик гармоник бежит вверх
        c = 1 + f * 30
        a = np.exp(-0.5 * ((h - c) / (1.2 + f * 3)) ** 2) + np.where(h == 1, 0.3, 0.0)
        return np.sin(TWO_PI * np.outer(t, h)) @ a
    if kind == 6:                                   # FM: модулятор ×3
        return np.sin(TWO_PI * t + (0.2 + f * 6) * np.sin(TWO_PI * 3 * t))
    if kind == 7:                                   # жёсткая синхронизация: ведомая пила ×1…×8
        r = 1 + f * 7
        return 2 * ((t * r) % 1.0) - 1
    if kind == 8:                                   # рык: пила через гребёнку и складывание
        saw = 2 * t - 1
        x = saw * (1 + f * 4)
        return np.sin(np.pi * x) * 0.7 + 0.3 * np.sign(np.sin(TWO_PI * t * (2 + int(f * 5))))
    if kind == 9:                                   # регистры органа
        regs = [(1, 1.0), (2, 0.8), (3, 0.6), (4, 0.5), (6, 0.35), (8, 0.3), (0.5, 0.0)]
        a = np.zeros(len(h))
        for i, (hh, lv) in enumerate(regs[:-1]):
            on = np.clip(f * 6 - i + 1, 0, 1) if i else 1.0
            a[int(hh) - 1] += lv * on
        return np.sin(TWO_PI * np.outer(t, h)) @ a
    if kind == 10:                                  # стекло: редкие верхние гармоники
        a = np.zeros(len(h))
        for hh, lv in ((1, 1.0), (3, 0.5), (5, 0.4 * f + 0.1), (8, 0.35 * f), (13, 0.3 * f), (21, 0.25 * f)):
            a[hh - 1] = lv
        return np.sin(TWO_PI * np.outer(t, h)) @ a
    if kind == 11:                                  # шумная пыль: случайные фазы, цвет от кадра
        rng = np.random.default_rng(77)
        a = 1 / h ** (1.6 - f * 1.2)
        return np.sin(TWO_PI * np.outer(t, h) + rng.uniform(0, TWO_PI, len(h))) @ a
    if kind == 12:                                  # сжатый синус (фазовое искажение)
        k = 1 + f * 12
        ph = np.clip(t * k, 0, 0.5) + np.clip((t - 0.5) * k + 0.5, 0.5, 1) - 0.5
        return np.sin(TWO_PI * np.clip(ph, 0, 1))
    if kind == 13:                                  # пила с вырезом (notch) — «голосистая»
        saw = 2 * t - 1
        notch = np.sin(TWO_PI * t * (4 + f * 12)) * 0.5 * f
        return saw * (1 - 0.4 * f) + notch
    if kind == 14:                                  # ступеньки
        steps = int(64 - f * 60)
        return np.round(np.sin(TWO_PI * t) * steps / 2) / (steps / 2)
    # супер-гармоники: все гармоники, наклон от кадра
    a = 1 / h ** (2.0 - 1.6 * f)
    return np.sin(TWO_PI * np.outer(t, h)) @ a


_SPEC: dict = {}


def table_spectra(kind: int) -> np.ndarray:
    """(FR, H+1) спектры кадров таблицы (комплексные, нормированы)."""
    s = _SPEC.get(kind)
    if s is None:
        fr = np.stack([_cycle(kind, f) for f in np.linspace(0, 1, FR)])
        fr = fr - fr.mean(axis=1, keepdims=True)
        fr /= np.maximum(np.abs(fr).max(axis=1, keepdims=True), 1e-9)
        s = np.fft.rfft(fr, axis=1)
        _SPEC[kind] = s
    return s


def band_table(kind: int, f0: float) -> np.ndarray:
    """Кадры таблицы без гармоник выше Найквиста для частоты f0 → (FR, N)."""
    S = table_spectra(kind).copy()
    hmax = int(max(1, (SR * 0.47) / max(1.0, f0)))
    if hmax < S.shape[1] - 1:
        S[:, hmax + 1:] = 0
    return np.fft.irfft(S, N, axis=1).astype(np.float32)


def _read(tab: np.ndarray, ph: np.ndarray, pos: np.ndarray) -> np.ndarray:
    """Чтение таблицы: фаза 0..1 (по отсчётам), позиция 0..1 (по отсчётам), билинейно."""
    if I._native():
        import native_fx
        return native_fx.wt_read(tab, ph, pos)
    fp = np.clip(pos, 0, 1) * (FR - 1)
    f0 = np.minimum(fp.astype(np.int64), FR - 2)
    fa = (fp - f0).astype(np.float32)
    x = ph * N
    i0 = x.astype(np.int64) % N
    xa = (x - np.floor(x)).astype(np.float32)
    i1 = (i0 + 1) % N
    a = tab[f0, i0] * (1 - xa) + tab[f0, i1] * xa
    b = tab[f0 + 1, i0] * (1 - xa) + tab[f0 + 1, i1] * xa
    return a * (1 - fa) + b * fa


def _lfo(shape: int, rate: float, n: int, ph0: float, rng) -> np.ndarray:
    t = (ph0 + np.arange(n) * rate / SR) % 1.0
    if shape == 0:
        return np.sin(TWO_PI * t)
    if shape == 1:
        return 1 - 4 * np.abs(t - 0.5)
    if shape == 2:
        return np.where(t < 0.5, 1.0, -1.0)
    if shape == 3:
        return 1 - 2 * t
    steps = int(n * rate / SR) + 2
    vals = rng.uniform(-1, 1, steps)
    idx = np.minimum((np.arange(n) * rate / SR).astype(np.int64), steps - 1)
    return vals[idx]


def _osc_p(x, tab, pos, vol, oc=0, se=0):
    return [P(f"{x}tab", f"Осц {x.upper()}: таблица", 0, len(TABLES) - 1, tab, "", "choice", TABLES),
            P(f"{x}pos", f"Осц {x.upper()}: позиция", 0, 1, pos, "%"),
            P(f"{x}vol", f"Осц {x.upper()}: громкость", 0, 1, vol, "%"),
            P(f"{x}oct", f"Осц {x.upper()}: октава", -3, 3, oc, "", "int"),
            P(f"{x}semi", f"Осц {x.upper()}: полутоны", -12, 12, se, "st", "int"),
            P(f"{x}fine", f"Осц {x.upper()}: подстройка", -100, 100, 0, "ц")]


class Wavetable(I.Gen):
    KIND = "wavetable"
    NAME = "Волновой синт"
    INFO = ("Два осциллятора по волновым таблицам (как Serum / Vital): позиция в таблице, унисон до 16 голосов, "
            "суб, шум, фильтр с перегрузом, LFO и огибающие. Больше всего пресетов.")
    PARAMS = ([P("macro_bright", "Макро: яркость", -1, 1, 0, "%"),
               P("macro_motion", "Макро: движение", 0, 1, 0, "%"),
               P("macro_space", "Макро: ширина", 0, 1, 0, "%"),
               P("macro_grit", "Макро: характер", 0, 1, 0, "%")] +
              _osc_p("a", 0, 0.6, 0.8) + _osc_p("b", 1, 0.3, 0.0) +
              [P("penv", "Огибающая позиции", -1, 1, 0.0, "%"),
               P("uni", "Унисон (голоса)", 1, 16, 1, "", "int"), P("det", "Расстройка", 0, 1, 0.25, "%"),
               P("blend", "Центр / края", 0, 1, 0.6, "%"), P("wid", "Ширина стерео", 0, 1, 0.8, "%"),
               P("warp", "Изгиб волны", 0, 1, 0.0, "%"),
               P("sub", "Суб (октава вниз)", 0, 1, 0.0, "%"), P("noise", "Шум", 0, 1, 0.0, "%"),
               P("glide", "Глиссандо от", -24, 24, 0, "st", "int"), P("gtime", "Время глиссандо", 0.01, 1, 0.08, "с", "log"),
               P("lshape", "LFO: форма", 0, len(LFO_SHAPES) - 1, 0, "", "choice", LFO_SHAPES),
               P("lrate", "LFO: скорость", 0.05, 20, 5.0, "Hz", "log"),
               P("lpitch", "LFO → высота", 0, 100, 0, "ц"), P("lcut", "LFO → срез", 0, 4, 0, "окт"),
               P("lpos", "LFO → позиция", 0, 1, 0, "%"), P("lamp", "LFO → громкость", 0, 1, 0, "%"),
               P("ldelay", "LFO: задержка", 0, 2, 0.2, "с"),
               P("fdrive", "Перегруз фильтра", 0, 1, 0.0, "%"), P("drive", "Перегруз выхода", 0, 1, 0.0, "%"),
               P("vol", "Громкость", 0, 1, 0.7, "%")] + I._FILTER_P + I._AMP_P)
    PRESETS: dict = {}

    def tail(self):
        return float(self.g("rel"))

    def render(self, pitch, vel, dur):
        g = self.g
        n_note = max(1, int(dur * SR))
        rel = float(g("rel"))
        n = n_note + int(rel * SR) + 64
        rng = np.random.default_rng(I._seed(pitch, self.key()))
        t = np.arange(n) / SR
        base = D.midi_to_hz(pitch)
        # частотная модуляция: глиссандо и LFO высоты (с задержкой вступления)
        fm = np.ones(n)
        gl = float(g("glide"))
        if gl:
            fm *= 2 ** (gl / 12 * np.exp(-t / max(0.005, float(g("gtime")) / 3)))
        lfo = _lfo(int(g("lshape")), float(g("lrate")), n, rng.random(), rng)
        ld = float(g("ldelay"))
        lfo_g = lfo * (np.clip(t / ld, 0, 1) if ld > 0.01 else 1.0)
        lp = float(g("lpitch"))
        if lp > 0:
            fm *= 2 ** (lp / 1200 * lfo_g)
        env_f = I.env_adsr(n_note, n, float(g("fatt")), float(g("fdec")), float(g("fsus")), rel)
        pos_mod = float(g("penv")) * env_f + (float(g("lpos")) + float(g("macro_motion")))*.5*lfo_g
        uni = min(16,max(1, int(g("uni")), 1+int(float(g("macro_space"))*6)))
        det = float(g("det")) * 0.5                     # ±полтона при 100 %
        wid = max(float(g("wid")),float(g("macro_space")))
        blend = float(g("blend"))
        warp = float(g("warp"))
        out = np.zeros((n, 2), np.float64)
        for x in ("a", "b"):
            vol = float(g(f"{x}vol"))
            if vol <= 0.001:
                continue
            semis = 12 * float(g(f"{x}oct")) + float(g(f"{x}semi")) + float(g(f"{x}fine")) / 100
            f0 = base * 2 ** (semis / 12)
            tab = band_table(int(g(f"{x}tab")), f0 * (1 + det * 0.06) * (2 ** (lp / 1200) if lp else 1))
            pos = np.clip(float(g(f"{x}pos")) + pos_mod, 0, 1)
            for u in range(uni):
                off = (u / (uni - 1) - 0.5) * 2 if uni > 1 else 0.0
                fu = f0 * 2 ** (off * det / 12)
                ph = (rng.random() + np.cumsum(fu * fm / SR)) % 1.0
                if warp > 0.001:                        # изгиб: сжать первую половину периода
                    k = 1 + warp * 4
                    ph = np.where(ph < 0.5, 0.5 * (2 * ph) ** k, ph)
                s = _read(tab, ph, pos)
                gain = (1 - blend) * 1.4 if off == 0 and uni % 2 == 1 else blend / max(1, uni - 1) * 2
                if uni == 1:
                    gain = 1.0
                out += I._st(s, off * wid) * (vol * gain / math.sqrt(max(1, uni / 2)))
        sv = float(g("sub"))
        if sv > 0.001:
            out += I._st(np.sin(TWO_PI * np.cumsum(base / 2 * fm / SR))) * sv * 0.8
        nz = float(g("noise"))
        if nz > 0.001:
            out += I._st(rng.uniform(-1, 1, n)) * nz * 0.35
        # фильтр
        fd = float(g("fdrive"))
        if fd > 0.01:
            k = 1 + fd * 5
            out = np.tanh(out * k) / math.tanh(k)
        fe = float(g("fenv"))
        cut = np.clip(float(g("cut")) * 2**(float(g("macro_bright"))*3) * (0.6 + 0.4 * I._vel(vel)),30,20000)
        lc = float(g("lcut"))
        if fe > 0.01 or lc > 0.01:
            cuts = cut * 2 ** (fe * env_f + lc * lfo_g)
            out = I.filt(out, int(g("ftype")), np.clip(cuts, 30, SR * 0.45), float(g("res")))
        elif cut < 19000 or int(g("ftype")) != 0:
            out = I.filt(out, int(g("ftype")), cut, float(g("res")))
        dr = max(float(g("drive")),float(g("macro_grit")))
        if dr > 0.01:
            k = 1 + dr * 8
            out = np.tanh(out * k) / math.tanh(k)
        amp = I.env_adsr(n_note, n, float(g("att")), float(g("dec")), float(g("sus")), rel)
        la = float(g("lamp"))
        if la > 0.001:
            amp = amp * (1 - la * 0.5 * (1 + lfo_g))
        out *= (amp * I._vel(vel) * float(g("vol")) * 0.45)[:, None]
        return out.astype(np.float32)


def _p(**kw):
    return kw


# Пресеты: «Категория: имя». Таблицы — индексы TABLES.
Wavetable.PRESETS = {
    "Init": {},
    # ── басы ──
    "Бас: 808 чистый": _p(atab=0, apos=0.0, sub=0.5, cut=900, fenv=0.3, att=0.002, dec=2.5, sus=0.0, rel=0.3, glide=-5, gtime=0.06),
    "Бас: 808 рычащий": _p(atab=0, apos=0.15, sub=0.6, drive=0.6, fdrive=0.4, cut=2500, dec=2.0, sus=0.2, rel=0.3, glide=-7),
    "Бас: Рейдж 808": _p(atab=6, apos=0.15, sub=0.7, drive=0.75, cut=3000, fenv=0.8, fdec=0.3, dec=1.8, sus=0.3, rel=0.25, glide=-12, gtime=0.12),
    "Бас: Рис (Reese)": _p(atab=0, apos=1.0, btab=0, bpos=1.0, bvol=0.8, bfine=18, uni=4, det=0.18, cut=700, res=1.6, sus=1.0, rel=0.15, fdrive=0.3),
    "Бас: Вобл (воббл)": _p(atab=8, apos=0.3, lpos=0.8, lrate=4.0, lshape=0, lcut=2.5, cut=600, res=2.5, sus=1.0, rel=0.1, sub=0.5, drive=0.3, ldelay=0),
    "Бас: Гроул": _p(atab=8, apos=0.5, penv=0.4, fatt=0.01, fdec=0.25, fsus=0.2, cut=1200, res=2.0, fenv=1.5, sus=0.9, drive=0.5, sub=0.4),
    "Бас: Резиновый": _p(atab=1, apos=0.4, cut=500, res=2.5, fenv=2.5, fdec=0.15, fsus=0.1, dec=0.4, sus=0.6, rel=0.1, glide=-3),
    "Бас: Суб-синус": _p(atab=0, apos=0.0, sub=0.0, cut=600, att=0.003, sus=1.0, rel=0.08),
    "Бас: Хаус орган": _p(atab=9, apos=0.4, cut=1400, fenv=1.0, fdec=0.2, fsus=0.2, dec=0.3, sus=0.3, rel=0.1),
    "Бас: Фанк-слэп": _p(atab=7, apos=0.3, penv=0.4, cut=1800, res=1.5, fenv=2.0, fdec=0.12, dec=0.25, sus=0.2, rel=0.08),
    "Бас: Дисторшн-пила": _p(atab=0, apos=1.0, uni=3, det=0.1, drive=0.8, cut=3500, res=1.2, sus=1.0, rel=0.1, sub=0.4),
    "Бас: Дабстеп FM": _p(atab=6, apos=0.6, lpos=1.0, lrate=6.0, lshape=2, cut=2000, sus=1.0, rel=0.1, drive=0.6, ldelay=0),
    # ── лиды ──
    "Лид: Хайперпоп": _p(atab=0, apos=1.0, btab=1, bpos=0.4, bvol=0.5, boct=1, uni=7, det=0.3, cut=9000, fenv=1.2, fdec=0.3, sus=0.8, rel=0.2, lpitch=15, lrate=6, ldelay=0.2, glide=3),
    "Лид: Суперпила": _p(atab=0, apos=1.0, uni=9, det=0.35, blend=0.7, cut=8000, fenv=1.0, sus=0.9, rel=0.3),
    "Лид: Плаггнб": _p(atab=2, apos=0.2, btab=0, bpos=0.0, bvol=0.4, boct=1, cut=5000, att=0.01, dec=0.6, sus=0.4, rel=0.35, lpitch=8, lrate=5),
    "Лид: Свист": _p(atab=0, apos=0.0, btab=10, bpos=0.2, bvol=0.3, boct=1, cut=12000, sus=1.0, rel=0.3, lpitch=20, lrate=5.5, ldelay=0.3),
    "Лид: Чиптюн": _p(atab=1, apos=0.5, cut=18000, sus=0.9, rel=0.03, lpitch=25, lrate=7, lshape=2),
    "Лид: Квадрат ретро": _p(atab=1, apos=0.0, uni=2, det=0.08, cut=4500, fenv=1.0, sus=0.85, rel=0.15),
    "Лид: Синтвейв": _p(atab=0, apos=0.8, btab=0, bpos=0.8, bvol=0.6, bfine=10, uni=4, det=0.15, cut=3200, fenv=1.5, fdec=0.6, sus=0.8, rel=0.5),
    "Лид: Кричащий": _p(atab=7, apos=0.6, uni=5, det=0.25, cut=7000, res=1.6, drive=0.5, sus=0.9, rel=0.2, penv=0.3),
    "Лид: Флейта-синт": _p(atab=0, apos=0.1, noise=0.08, cut=4000, att=0.06, sus=0.9, rel=0.25, lpitch=12, lrate=5, ldelay=0.4),
    "Лид: Глайд-моно": _p(atab=0, apos=0.9, uni=3, det=0.12, cut=5000, glide=-12, gtime=0.15, sus=0.9, rel=0.2),
    "Лид: Вокальный": _p(atab=3, apos=0.4, lpos=0.4, lrate=1.5, uni=3, det=0.12, cut=6000, sus=0.9, rel=0.3),
    "Лид: Цифровой FM": _p(atab=6, apos=0.4, penv=0.3, fdec=0.5, cut=9000, dec=0.5, sus=0.6, rel=0.2),
    # ── плаки и аккорды ──
    "Плак: Кристальный": _p(atab=10, apos=0.6, uni=3, det=0.1, cut=6000, fenv=2.5, fdec=0.2, fsus=0.0, dec=0.5, sus=0.0, rel=0.4),
    "Плак: Трэп-белл": _p(atab=10, apos=0.9, btab=0, bvol=0.3, boct=1, cut=9000, dec=0.9, sus=0.0, rel=0.8),
    "Плак: Хаус-аккорд": _p(atab=0, apos=1.0, uni=5, det=0.2, cut=1500, res=1.3, fenv=3.0, fdec=0.18, fsus=0.0, dec=0.35, sus=0.0, rel=0.2),
    "Плак: Мягкий": _p(atab=2, apos=0.3, cut=2500, fenv=2.0, fdec=0.25, fsus=0.0, dec=0.6, sus=0.0, rel=0.4),
    "Плак: Маримба-синт": _p(atab=0, apos=0.0, btab=10, bvol=0.4, boct=2, cut=5000, dec=0.4, sus=0.0, rel=0.3),
    "Плак: Стаб": _p(atab=0, apos=1.0, btab=1, bvol=0.5, uni=6, det=0.25, cut=3000, fenv=2.0, fdec=0.12, dec=0.25, sus=0.1, rel=0.15),
    "Плак: Арп-цифра": _p(atab=14, apos=0.5, cut=7000, fenv=1.5, fdec=0.15, dec=0.3, sus=0.0, rel=0.15),
    "Аккорд: Лоу-фай пиано": _p(atab=12, apos=0.2, btab=0, bvol=0.3, noise=0.03, cut=2200, dec=1.2, sus=0.2, rel=0.6, lpitch=6, lrate=0.6, lshape=1),
    "Аккорд: Нео-соул": _p(atab=9, apos=0.5, uni=2, det=0.05, cut=2800, dec=1.5, sus=0.5, rel=0.5, lamp=0.15, lrate=5),
    # ── пэды ──
    "Пэд: Облако": _p(atab=0, apos=0.6, btab=4, bvol=0.4, uni=8, det=0.3, cut=2500, att=1.2, dec=2, sus=0.85, rel=2.2, lpos=0.3, lrate=0.2, vol=0.55),
    "Пэд: Тёмный": _p(atab=8, apos=0.2, uni=6, det=0.2, cut=900, res=1.2, att=1.0, sus=0.9, rel=2.0, lcut=0.8, lrate=0.15, vol=0.6),
    "Пэд: Хор ангелов": _p(atab=4, apos=0.5, btab=3, bpos=0.6, bvol=0.5, uni=6, det=0.18, cut=5000, att=0.9, sus=0.9, rel=2.0, vol=0.55),
    "Пэд: Стеклянный": _p(atab=10, apos=0.7, uni=5, det=0.15, cut=7000, att=0.6, sus=0.8, rel=2.5, lpos=0.4, lrate=0.3),
    "Пэд: Эмбиент-пыль": _p(atab=11, apos=0.5, noise=0.1, uni=4, det=0.2, cut=3000, att=2.0, sus=0.8, rel=3.0, lpos=0.5, lrate=0.1, vol=0.5),
    "Пэд: Синтвейв": _p(atab=0, apos=1.0, uni=7, det=0.25, cut=1800, fenv=1.0, fatt=1.5, fdec=2.0, att=0.6, sus=0.85, rel=1.5),
    "Пэд: Вздох": _p(atab=3, apos=0.0, penv=1.0, fatt=2.0, fdec=2.0, fsus=0.6, uni=4, det=0.15, cut=4000, att=1.5, sus=0.8, rel=2.0),
    "Пэд: Орган-хор": _p(atab=9, apos=0.8, btab=4, bvol=0.4, uni=3, det=0.08, cut=3500, att=0.3, sus=1.0, rel=1.0),
    # ── клавиши и колокола ──
    "Клавиши: Электропиано": _p(atab=6, apos=0.15, btab=0, bvol=0.4, cut=4000, dec=1.8, sus=0.2, rel=0.5, lamp=0.2, lrate=4.5),
    "Клавиши: Ретро-орган": _p(atab=9, apos=0.7, cut=6000, sus=1.0, rel=0.1, lamp=0.25, lrate=6.5),
    "Колокол: Музыкальная шкатулка": _p(atab=10, apos=1.0, aoct=1, cut=12000, dec=1.4, sus=0.0, rel=1.2),
    "Колокол: Трубчатый": _p(atab=6, apos=0.8, cut=10000, dec=2.5, sus=0.0, rel=2.0),
    "Колокол: Лёд": _p(atab=10, apos=0.5, btab=6, bpos=0.9, bvol=0.4, boct=1, cut=12000, dec=1.8, sus=0.0, rel=1.8),
    # ── струнные, медь, голос ──
    "Струнные: Ансамбль": _p(atab=0, apos=1.0, uni=8, det=0.15, cut=3500, att=0.4, sus=0.9, rel=0.9, lpitch=6, lrate=5),
    "Струнные: Пиццикато": _p(atab=0, apos=1.0, uni=3, det=0.1, cut=2500, fenv=2.0, fdec=0.1, dec=0.25, sus=0.0, rel=0.2),
    "Медь: Синт-брасс": _p(atab=0, apos=1.0, uni=3, det=0.1, cut=900, fenv=2.5, fatt=0.08, fdec=0.5, fsus=0.5, att=0.05, sus=0.85, rel=0.25),
    "Медь: Стаб 90-х": _p(atab=0, apos=1.0, btab=1, bvol=0.6, uni=5, det=0.2, cut=2000, fenv=2.0, fdec=0.2, dec=0.3, sus=0.3, rel=0.2),
    "Голос: Ууу": _p(atab=3, apos=1.0, uni=3, det=0.1, cut=3000, att=0.15, sus=0.9, rel=0.6, lpitch=10, lrate=5),
    "Голос: Аааа": _p(atab=4, apos=0.2, uni=5, det=0.12, cut=5000, att=0.2, sus=0.9, rel=0.8),
    "Голос: Говорящий синт": _p(atab=3, apos=0.0, lpos=1.0, lrate=2.5, lshape=1, cut=6000, sus=0.9, rel=0.2, ldelay=0),
    # ── эффекты и перкуссия ──
    "Эффект: Райзер": _p(atab=11, apos=0.0, penv=1.0, fatt=4.0, fdec=4.0, fsus=1.0, noise=0.3, cut=500, fenv=4.0, att=3.0, sus=1.0, rel=0.5, glide=-12, gtime=1.0),
    "Эффект: Лазер": _p(atab=0, apos=1.0, glide=24, gtime=0.1, cut=8000, dec=0.3, sus=0.0, rel=0.1),
    "Эффект: Сирена": _p(atab=0, apos=0.5, lpitch=100, lrate=1.5, lshape=1, cut=6000, sus=1.0, rel=0.3, ldelay=0),
    "Эффект: Сканер": _p(atab=5, apos=0.0, lpos=1.0, lrate=8.0, lshape=3, cut=9000, sus=1.0, rel=0.2, ldelay=0),
    "Эффект: Глитч": _p(atab=14, apos=0.5, lpos=1.0, lrate=12.0, lshape=4, lamp=0.6, cut=9000, sus=1.0, rel=0.1, ldelay=0),
    "Перкуссия: Синт-том": _p(atab=0, apos=0.0, glide=-12, gtime=0.08, cut=3000, dec=0.4, sus=0.0, rel=0.2, noise=0.05),
    "Перкуссия: Ковбелл-синт": _p(atab=1, apos=0.2, btab=1, bvol=0.6, bsemi=7, cut=4000, res=2, dec=0.25, sus=0.0, rel=0.1),
}

I.GENERATORS["wavetable"] = Wavetable
