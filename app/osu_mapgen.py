# osu_mapgen.py
"""
Генератор карт osu! по треку (тема «esu!»).

Две части: «слух» разбирает трек, «маппер» ставит ноты так, как их ставят живые мапперы, — его модели и
таблицы выучены по ranked/loved картам osu! (osu_mapgen_data.py: 107 карт, 5 уровней сложности).

1. Слух (analyze): трек декодируется ffmpeg'ом в 22 кГц моно,
   * спектр по 40 логарифмическим полосам → «поток атак» (spectral flux) — где в музыке удары,
     отдельно по басу / середине / верху (бочка, малый барабан, тарелки, голос);
   * темп и сетка: складывание огибающей атак, МНК по началам ударов (±2 мс), триоли;
   * HPSS — ударные отдельно от мелодии/голоса: атаки ударных и новые ноты мелодии, держится ли
     тональный звук (для слайдеров), смена высоты (хрома), «высота» мелодии;
   * сильная доля такта, части песни (новизна по самоподобию тактов), kiai (припевы), перерывы,
     повторы тактов (ритм + хрома + тембр ударных) — повтор маппится тем же ритмом и рисунком.

2. Маппер (generate): по каждой сложности
   * ритм — динамическим программированием по всем тикам разом: модели «здесь маппер нажимает»,
     «здесь кончается слайдер», «это слайдер» (логистические, по ~60 признакам тика) + грамматика
     интервалов, длин слайдеров и цепочек 1/4 этой сложности; на картах людей нажатия бота совпадают
     с маппером почти так же, как совпадают между собой два маппера;
   * спиннеры — в долгих протяжных местах без ударов, не чаще, чем у людей;
   * новые комбо — с первой ноты такта (на сложных — и с середины), на входе в припев, после пауз;
   * хитсаунды: clap — малый барабан, прежде всего на 2-й и 4-й долях; finish — тарелка на сильной доле;
     whistle — яркая нота мелодии; скорость слайдеров — по силе части песни;
   * расстановка: дистанции, стопки и повороты — по таблицам людей для каждого интервала (сильная нота —
     дальше), «фигура» комбо (треугольники, квадраты, зигзаги, прыжки туда-обратно, изогнутые стримы),
     повторы тактов — тем же рисунком (зеркально/поворотом), слайдеры — по потоку, без наложений;
   * звёзды — по алгоритму сложности osu! (aim / speed strain); разлёт подстраивается лишь слегка.

Карты кэшируются в ~/.neon_player/osu/maps; экспорт в .osz — export_osz().
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
import zipfile
from pathlib import Path

import numpy as np

import osu_mapgen_data as MD

OSU_DIR = Path.home() / ".neon_player" / "osu"
CACHE = OSU_DIR / "maps"
VERSION = 7                       # меняется — старые карты пересоздаются

SR = 22050
HOP = 256
NFFT = 1024
FPS = SR / HOP                    # ≈ 86 кадров анализа в секунду
NBANDS = 40
PLAY_W, PLAY_H = 512, 384

# ── сложности ── #
# sv — пикселей слайдера на долю; jump — есть ли прыжки (разлёт подгоняется под звёзды); stars — цель
DIFFS = [
    dict(key="easy", name="Easy", cs=3.0, ar=4.0, od=3.0, hp=3.0, sv=95, jump=0.0, stars=(1.3, 2.0)),
    dict(key="normal", name="Normal", cs=3.8, ar=6.0, od=5.0, hp=4.0, sv=135, jump=0.0, stars=(2.3, 3.0)),
    dict(key="hard", name="Hard", cs=4.0, ar=7.6, od=6.5, hp=5.0, sv=165, jump=0.55, stars=(2.9, 4.0)),
    dict(key="insane", name="Insane", cs=4.0, ar=9.0, od=8.0, hp=6.0, sv=200, jump=1.0, stars=(4.2, 5.3)),
    dict(key="expert", name="Expert", cs=4.2, ar=9.5, od=9.0, hp=6.0, sv=235, jump=1.3, stars=(5.4, 6.9)),
    # две сверхсложные — для сильных игроков: ритм и рисунок — от моделей Expert, но плотнее (длинные стримы,
    # тройки), меньше слайдеров, прыжки и стримы шире (dist — множитель дистанций людей, stack — доля стопок)
    dict(key="extra", name="Extra", cs=4.4, ar=9.7, od=9.4, hp=6.5, sv=270, jump=1.6, stars=(6.4, 7.6),
         base="expert", dist=1.6, stack=0.5, sp_clip=(0.8, 2.0)),
    dict(key="extreme", name="Extreme", cs=4.6, ar=10.0, od=9.8, hp=7.0, sv=310, jump=2.0, stars=(7.8, 9.4),
         base="expert", dist=2.0, stack=0.3, sp_clip=(0.8, 2.4)),
]
DIFF_KEYS = [d["key"] for d in DIFFS]

DEFAULT_OPTS = {"density": 1.0, "jumps": 1.0, "sliders": 1.0, "streams": True, "spinners": True,
                "symmetry": True, "seed": 0}


def diff_info(key: str) -> dict:
    return next((d for d in DIFFS if d["key"] == key), DIFFS[2])


# ------------------------------------------------------------------ #
#  Параметры сложности osu!                                           #
# ------------------------------------------------------------------ #

def radius(cs: float) -> float:
    return 54.4 - 4.48 * cs


def preempt(ar: float) -> float:
    return 1200 + 600 * (5 - ar) / 5 if ar < 5 else 1200 - 750 * (ar - 5) / 5


def fade_in(ar: float) -> float:
    return 800 + 400 * (5 - ar) / 5 if ar < 5 else 800 - 500 * (ar - 5) / 5


def hit_windows(od: float) -> tuple[float, float, float]:
    return 80 - 6 * od, 140 - 8 * od, 200 - 10 * od


def spins_needed(od: float, dur_ms: float) -> float:
    per_s = 3 + 0.4 * od if od < 5 else 2.5 + 0.5 * od
    return max(1.0, dur_ms / 1000.0 * per_s * 0.5)


def apply_mods(cs, ar, od, hp, mods) -> tuple[float, float, float, float]:
    if "EZ" in mods:
        cs, ar, od, hp = cs * 0.5, ar * 0.5, od * 0.5, hp * 0.5
    if "HR" in mods:
        cs, ar, od, hp = min(10, cs * 1.3), min(10, ar * 1.4), min(10, od * 1.4), min(10, hp * 1.4)
    return cs, ar, od, hp


def effective_ar_od(ar, od, rate) -> tuple[float, float]:
    """AR/OD с учётом DT/HT (как показывает osu!)."""
    if abs(rate - 1.0) < 1e-3:
        return ar, od
    pre = preempt(ar) / rate
    ar2 = 5 - (pre - 1200) / 120 if pre > 1200 else 5 + (1200 - pre) / 150
    w300 = (80 - 6 * od) / rate
    return ar2, (80 - w300) / 6


# ------------------------------------------------------------------ #
#  Декодирование и спектр                                             #
# ------------------------------------------------------------------ #

def _ffmpeg() -> str | None:
    p = shutil.which("ffmpeg")
    if p:
        return p
    for base in (Path(__file__).resolve().parent.parent / "ffmpeg",
                 Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "ECHOES" / "ffmpeg"):
        f = base / "ffmpeg.exe"
        if f.exists():
            return str(f)
    return None


def decode(path: str, seconds: float = 900.0) -> np.ndarray:
    ff = _ffmpeg()
    if not ff:
        raise RuntimeError("нет ffmpeg — нечем прочитать трек")
    flags = 0x08000000 if os.name == "nt" else 0          # CREATE_NO_WINDOW
    r = subprocess.run([ff, "-v", "error", "-nostdin", "-i", str(path), "-t", str(seconds), "-vn", "-ac", "1",
                        "-ar", str(SR), "-f", "f32le", "-"], capture_output=True, creationflags=flags, timeout=180)
    x = np.frombuffer(r.stdout, np.float32)
    if len(x) < SR * 5:
        raise RuntimeError("трек не прочитан или короче 5 секунд")
    return x.copy()


def _filterbank(n_fft=NFFT, nb=NBANDS, lo=35.0, hi=10000.0) -> tuple[np.ndarray, np.ndarray]:
    freqs = np.fft.rfftfreq(n_fft, 1.0 / SR)
    edges = np.geomspace(lo, hi, nb + 2)
    fb = np.zeros((nb, len(freqs)), np.float32)
    for i in range(nb):
        a, c, b = edges[i], edges[i + 1], edges[i + 2]
        up = (freqs - a) / max(1e-6, c - a)
        dn = (b - freqs) / max(1e-6, b - c)
        fb[i] = np.clip(np.minimum(up, dn), 0, None)
        if fb[i].sum() == 0:                                   # узкая полоса на низах — хотя бы один бин
            fb[i, int(np.argmin(np.abs(freqs - c)))] = 1.0
        fb[i] /= fb[i].sum()
    return fb, edges[1:-1]


def _bands(x: np.ndarray) -> np.ndarray:
    """Энергия по полосам: (кадры × NBANDS), кадр k — центр в k*HOP."""
    fb, _ = _filterbank()
    xp = np.concatenate([np.zeros(NFFT // 2, np.float32), x, np.zeros(NFFT, np.float32)])
    n = 1 + (len(x)) // HOP
    win = np.hanning(NFFT).astype(np.float32)
    out = np.empty((n, NBANDS), np.float32)
    idx0 = np.arange(NFFT)[None, :]
    step = 2048
    for s in range(0, n, step):
        k = np.arange(s, min(n, s + step))
        fr = xp[k[:, None] * HOP + idx0] * win
        mag = np.abs(np.fft.rfft(fr, axis=1)).astype(np.float32)
        out[s:s + len(k)] = (mag * mag) @ fb.T
    return out


def _smooth(a: np.ndarray, n: int) -> np.ndarray:
    if n <= 1:
        return a
    k = np.hanning(n + 2)[1:-1]
    k /= k.sum()
    return np.convolve(a, k, mode="same")


# ------------------------------------------------------------------ #
#  Темп и доли                                                        #
# ------------------------------------------------------------------ #

def _tempo(onset: np.ndarray) -> float:
    o = onset - _smooth(onset, int(FPS))
    o = np.maximum(o, 0)
    n = len(o)
    m = 1
    while m < 2 * n:
        m *= 2
    f = np.fft.rfft(o, m)
    ac = np.fft.irfft(f * np.conj(f), m)[:n]
    ac /= max(1e-9, ac[0])
    best, best_bpm = -1e9, 120.0
    for bpm in np.arange(60.0, 220.0, 0.25):
        lag = 60.0 * FPS / bpm
        sc = 0.0
        for h, w in ((1, 1.0), (2, 0.5), (3, 0.33), (4, 0.25), (0.5, 0.35)):
            L = lag * h
            i = int(L)
            if i + 1 >= n or L < 2:
                continue
            fr = L - i
            sc += w * (ac[i] * (1 - fr) + ac[i + 1] * fr)
        prior = math.exp(-0.5 * (math.log2(bpm / 128.0) / 0.9) ** 2)
        sc *= 0.35 + prior
        if sc > best:
            best, best_bpm = sc, bpm
    return best_bpm


def _fold(o: np.ndarray, P: float, nb: int | None = None) -> np.ndarray:
    """Огибающая атак, «сложенная» по периоду P кадров: масса атак по фазе доли."""
    nb = nb or max(8, int(round(P)))
    ph = (np.mod(np.arange(len(o), dtype=np.float64), P) * (nb / P)).astype(np.int64)
    np.minimum(ph, nb - 1, out=ph)
    h = np.bincount(ph, weights=o, minlength=nb)
    return h + 0.5 * np.roll(h, 1) + 0.5 * np.roll(h, -1)


def _sharp(h: np.ndarray) -> float:
    return float(h.max() / (h.mean() + 1e-9))


def _tempo_fold(onset: np.ndarray):
    """Ровный темп: перебор периода со «складыванием» огибающей. При верном темпе все доли
    ложатся в одну фазу — пик острый; при 3:2 и прочих ошибках он размазывается.
    Возвращает (bpm, фаза первой доли в секундах, ровный ли темп, острота)."""
    o = np.maximum(0, onset - _smooth(onset, int(FPS * 0.4)))
    n = len(o)
    seg_len = int(FPS * 50)
    if n > seg_len * 1.3:
        cs = np.concatenate([[0], np.cumsum(o)])
        act = cs[seg_len:] - cs[:-seg_len]
        s0 = int(np.argmax(act))
        seg = o[s0:s0 + seg_len]
    else:
        seg = o
    bpms = np.arange(70.0, 200.0, 0.1)
    sc = np.array([_sharp(_fold(seg, 60 * FPS / b)) for b in bpms])
    cands = []
    for i in np.argsort(-sc):
        b = bpms[i]
        if all(abs(b / c - 1) > 0.03 for c in cands):
            cands.append(b)
        if len(cands) >= 6:
            break
    best = None
    for b0 in cands:
        loc_best = (0.0, b0)
        for b in np.arange(b0 - 0.25, b0 + 0.25, 0.01):
            s = _sharp(_fold(o, 60 * FPS / b))
            if s > loc_best[0]:
                loc_best = (s, b)
        s, b = loc_best
        bb = b
        while bb < 88:
            bb *= 2
        while bb > 190:
            bb /= 2
        prior = math.exp(-0.5 * (math.log2(bb / 130.0) / 0.8) ** 2)
        val = s * (0.7 + 0.3 * prior)
        if best is None or val > best[0]:
            best = (val, b, s)
    _, bpm, sharp_full = best
    P = 60 * FPS / bpm
    h = _fold(o, P, nb=max(16, int(round(P)) * 2))
    phase = float(np.argmax(h)) * P / len(h) / FPS
    # ровный ли: фаза в первой и второй половине совпадает и пик в обеих острый
    half = n // 2
    steady = sharp_full > 1.6
    if half > FPS * 20:
        h1, h2 = _fold(o[:half], P, len(h)), _fold(np.concatenate([np.zeros(0), o[half:]]), P, len(h))
        p1 = np.argmax(h1)
        # фаза второй половины считается от кадра half — переводим в общую
        p2 = (np.argmax(h2) * P / len(h) + half) % P * len(h) / P
        dphase = min(abs(p1 - p2), len(h) - abs(p1 - p2)) * P / len(h) / FPS
        steady = steady and dphase < 0.03 and _sharp(h1) > 1.4 and _sharp(h2) > 1.4
    return bpm, phase, steady, sharp_full


def _track_beats(onset: np.ndarray, bpm: float, tightness=120.0) -> np.ndarray:
    period = 60.0 * FPS / bpm
    o = onset / max(1e-9, onset.std())
    ls = np.convolve(o, np.exp(-0.5 * ((np.arange(-int(period), int(period) + 1)) / (period / 32.0)) ** 2), "same")
    n = len(ls)
    cum = np.zeros(n)
    back = -np.ones(n, int)
    lo, hi = int(round(period / 2)), int(round(period * 2))
    offs = np.arange(-hi, -lo + 1)
    txwt = -tightness * np.log(-offs / period) ** 2
    for t in range(n):
        a = t + offs
        ok = a >= 0
        if not ok.any():
            cum[t] = ls[t]
            continue
        sc = np.where(ok, cum[np.maximum(a, 0)] + txwt, -1e18)
        j = int(np.argmax(sc))
        if sc[j] > -1e17:
            cum[t] = ls[t] + sc[j]
            back[t] = a[j]
        else:
            cum[t] = ls[t]
    # конец: максимум накопленного в последних двух периодах
    tail = max(0, n - hi)
    t = tail + int(np.argmax(cum[tail:]))
    beats = []
    while t >= 0:
        beats.append(t)
        t = back[t]
    return np.array(beats[::-1], float)


def _fine_env(x: np.ndarray) -> tuple[np.ndarray, float]:
    """Мелкая огибающая атак (шаг ~2.9 мс) для точной подгонки смещения."""
    h = 64
    n = len(x) // h
    e = np.sqrt(np.add.reduceat(x[: n * h] ** 2, np.arange(0, n * h, h)) / h + 1e-10)
    le = np.log(e + 1e-4)
    d = np.maximum(0, np.diff(le, prepend=le[0]))
    return _smooth(d, 3), SR / h


def _interp(a: np.ndarray, rate: float, t: np.ndarray) -> np.ndarray:
    return np.interp(t * rate, np.arange(len(a)), a, left=0, right=0)


# ------------------------------------------------------------------ #
#  Тайминг v6: точный темп, фаза по началу ударов, сетка 1/4          #
# ------------------------------------------------------------------ #
# Проверено на 33 картах из osu! (их доли расставляли мапперы): темп совпал в 27 (ещё 4 — ровно
# вдвое быстрее, для расстановки это то же самое), фаза сетки 1/4 — в пределах нескольких мс от
# начала удара. Старый способ (пик «складывания» + сдвиг ±50 мс) часто вставал на 1/4 доли мимо.

FHOP = 64
FFR = SR / FHOP                       # ≈ 344.5 кадров/с (2.9 мс)


def _td_env(x: np.ndarray) -> np.ndarray:
    """Огибающая атак во времени: лог-энергия кадров по 64 сэмпла → рост (масштаб — σ)."""
    n = len(x) // FHOP
    e = np.sqrt(np.add.reduceat(x[:n * FHOP].astype(np.float64) ** 2, np.arange(0, n * FHOP, FHOP)) / FHOP + 1e-12)
    floor = np.percentile(e, 15) + 1e-6
    le = np.log(e + floor)
    d = np.maximum(0, np.diff(le, prepend=le[0]))
    d = np.convolve(d, [0.25, 0.5, 0.25], "same")
    return (d / (d.std() + 1e-9)).astype(np.float32)


def _wmax(env, idx, lo, hi):
    """max(env[idx+lo … idx+hi]) и где он, для массива индексов."""
    n = len(env)
    best = np.full(len(idx), -1.0, np.float32)
    arg = np.zeros(len(idx), np.int64)
    for w in range(lo, hi + 1):
        v = env[np.clip(idx + w, 0, n - 1)]
        better = v > best
        best = np.where(better, v, best)
        arg = np.where(better, w, arg)
    return best, arg


def _attack_start(env, idx, lo, hi, frac=0.35):
    """Начало атаки: от пика в окне назад до первого кадра ниже frac·пик. → (сила, кадр начала)."""
    v, arg = _wmax(env, idx, lo, hi)
    cur = idx + arg
    thr = v * frac
    active = np.ones(len(cur), bool)
    for _ in range(hi - lo):
        nxt = np.clip(cur - 1, 0, len(env) - 1)
        go = active & (env[nxt] >= thr) & (nxt >= idx + lo)
        cur = np.where(go, nxt, cur)
        active = go
        if not active.any():
            break
    return v, cur.astype(np.float64)


def _env_peaks(env, rate, min_gap_s=0.035, k=1.0):
    """Пики огибающей выше скользящего порога (среднее за 0.5 с + k·σ)."""
    w = max(3, int(rate * 0.5))
    mean = np.convolve(env, np.ones(w, np.float32) / w, "same")
    thr = mean + k * float(env.std())
    c = np.nonzero((env[1:-1] > env[:-2]) & (env[1:-1] >= env[2:]) & (env[1:-1] > thr[1:-1]))[0] + 1
    if not len(c):
        return c
    keep = [int(c[0])]
    gap = min_gap_s * rate
    for i in c[1:]:
        if i - keep[-1] < gap:
            if env[i] > env[keep[-1]]:
                keep[-1] = int(i)
        else:
            keep.append(int(i))
    return np.array(keep, np.int64)


def _amp_flux(B: np.ndarray) -> np.ndarray:
    """Громкостный поток атак (без логарифма): бочка и малый «весят» больше хэтов."""
    S = np.sqrt(B)
    prev = np.maximum.reduce([np.roll(S, 1, 1), S, np.roll(S, -1, 1)])
    prev = np.vstack([prev[:1], prev[:-1]])
    d = np.maximum(0, S - prev)
    return (d * np.linspace(1.4, 0.7, S.shape[1])).sum(1)


def _fold_profile(env, rate, P_s, nb=48, t0=0.0):
    t = np.arange(len(env)) / rate - t0
    ph = (np.mod(t, P_s) / P_s * nb).astype(np.int64) % nb
    return np.bincount(ph, weights=env, minlength=nb) / np.maximum(1, np.bincount(ph, minlength=nb))


def _grid_strength(A, P, ph, n, lo=-1, hi=1):
    k0 = -int(ph // P)
    pos = ph + np.arange(k0, int((n - ph) / P) + 1) * P
    pos = pos[(pos > 20) & (pos < n - 20)]
    v, _ = _wmax(A, np.round(pos).astype(np.int64), lo, hi)
    return v


def _fit_grid(A, P, ph, n, lo, hi):
    """МНК по началам сильных атак у узлов сетки: t_k = a + k·P (без выбросов). → (P, a, k, t)."""
    k0 = -int(ph // P)
    ks = np.arange(k0, int((n - ph) / P) + 1)
    pos = ph + ks * P
    ok = (pos > 30) & (pos < n - 30)
    ks, pos = ks[ok], pos[ok]
    if len(ks) < 8:
        return P, ph, ks[:0], pos[:0]
    v, t = _attack_start(A, np.round(pos).astype(np.int64), lo, hi)
    strong = v >= np.percentile(v, 45)
    kk, tt, ww = ks[strong], t[strong], v[strong]
    sol = np.array([ph, P], float)
    for _ in range(4):
        if len(kk) < 8:
            break
        Wm = np.sqrt(ww)
        M = np.stack([np.ones_like(kk, dtype=float), kk.astype(float)], 1) * Wm[:, None]
        sol, *_ = np.linalg.lstsq(M, tt * Wm, rcond=None)
        res = tt - (sol[0] + sol[1] * kk)
        keep = np.abs(res) < max(3.0, 2.5 * np.median(np.abs(res)) + 1.0)
        if keep.all():
            break
        kk, tt, ww = kk[keep], tt[keep], ww[keep]
    return float(sol[1]), float(sol[0]), kk, tt


def _choose_octave(bpm, af):
    """Темп «как у мапперов»: быстрая октава, если слабые доли почти как сильные."""
    while bpm < 100:
        bpm *= 2
    while bpm > 235:
        bpm /= 2
    for _ in range(2):
        h = _fold_profile(af, FPS, 60.0 / bpm, 48)
        h = h + 0.5 * np.roll(h, 1) + 0.5 * np.roll(h, -1)
        i = int(np.argmax(h))
        r = h[(i + 24) % 48] / max(1e-9, h[i])
        if bpm < 140 and r > 0.8 and bpm * 2 <= 235:
            bpm *= 2
            continue
        if bpm > 205 and r < 0.45:
            bpm /= 2
            continue
        break
    return bpm


def _timing_v6(x: np.ndarray, B: np.ndarray, onset: np.ndarray, A: np.ndarray) -> dict:
    """Темп, фаза доли и сетка долей (сек). A — _td_env(x)."""
    n = len(A)
    af = _amp_flux(B)
    # 1) кандидаты темпа по «складыванию» потока атак (60 с самой плотной части)
    o = np.maximum(0, onset - _smooth(onset, int(FPS * 0.4)))
    seg_len = int(FPS * 60)
    if len(o) > seg_len * 1.3:
        cs = np.concatenate([[0], np.cumsum(o)])
        s0 = int(np.argmax(cs[seg_len:] - cs[:-seg_len]))
        seg = o[s0:s0 + seg_len]
    else:
        seg = o
    bpms = np.arange(60.0, 240.0, 0.1)
    sc = np.array([_sharp(_fold(seg, 60 * FPS / b)) for b in bpms])
    cands = []
    for i in np.argsort(-sc):
        if all(abs(bpms[i] / c[0] - 1) > 0.02 for c in cands):
            cands.append((float(bpms[i]), float(sc[i])))
        if len(cands) >= 6:
            break
    # 2) семейства (кандидат в «быстрой» октаве): резкость складывания + сколько сильных атак ложится на сетку 1/4
    fams = []
    for c, _s in cands:
        best = (0.0, c)
        for b in np.arange(c - 0.3, c + 0.3 + 1e-9, 0.02):
            s = _sharp(_fold(o, 60 * FPS / b))
            if s > best[0]:
                best = (s, float(b))
        b2 = _choose_octave(best[1], af)
        f = next((f for f in fams if abs(b2 / f[0] - 1) < 0.01), None)
        if f is None:
            fams.append([b2, best[0]])
        else:
            f[1] = max(f[1], best[0])
    top_sharp = max(f[1] for f in fams)
    pk = _env_peaks(A, FFR)
    strong = pk[A[pk] >= np.percentile(A[pk], 50)] if len(pk) else pk
    scored = []
    for b2, sharp in fams[:4]:
        Qc = 60.0 * FFR / b2 / 4
        phs = np.arange(0, Qc, 1.0)
        s_ = np.array([_grid_strength(A, Qc, q, n).mean() for q in phs])
        ph_c = float(phs[int(np.argmax(s_))])
        cov = 0.0
        if len(strong):
            d = (strong - ph_c) / Qc
            cov = float((np.abs(d - np.round(d)) * Qc < 0.012 * FFR).mean())
            chance = min(0.95, 2 * 0.012 * FFR / Qc)
            cov = (cov - chance) / (1 - chance)
        scored.append((sharp / max(1e-9, top_sharp) + 0.5 * cov, b2))
    scored.sort(key=lambda r: -r[0])
    bpm = scored[0][1]
    P = 60.0 * FFR / bpm
    # 3) фаза сетки 1/4 по всем атакам и уточнение периода (МНК по началам атак)
    Q = P / 4
    phs = np.arange(0, Q, 0.5)
    s_ = np.array([_grid_strength(A, Q, q, n).mean() for q in phs])
    ph16 = float(phs[int(np.argmax(s_))])
    kk16 = tt16 = np.zeros(0)
    for lo_w, hi_w in ((-12, 12), (-6, 8)):
        Q2, a16, kk16, tt16 = _fit_grid(A, Q, ph16, n, lo_w, hi_w)
        if len(kk16) >= 32 and abs(Q2 / Q - 1) < 0.004:
            Q, ph16 = Q2, a16 % Q2
    P = 4 * Q
    bpm = 60.0 * FFR / P
    if len(kk16) >= 32:                                # «круглый» темп, если сетка ложится не хуже
        r1 = np.median(np.abs(tt16 - (ph16 + Q * kk16) - np.round((tt16 - (ph16 + Q * kk16)) / Q) * Q))
        for nice in (round(bpm), round(bpm * 2) / 2):
            if 0 < abs(nice - bpm) < 0.03:
                Qn = 60.0 * FFR / nice / 4
                an_ = float(np.median(tt16 - Qn * kk16))
                r2 = np.median(np.abs(tt16 - (an_ + Qn * kk16)))
                if r2 <= r1 + 0.15:
                    Q, ph16, bpm, P = Qn, an_ % Qn, float(nice), 4 * Qn
                    break
    ph16 %= Q
    # 4) какая из четырёх четвертей — доля: в её «ячейке» самый громкий подъём (бочка/малый звучат
    #    громче всего на 10–90 мс позже начала удара — поэтому ячейка, а не точка)
    afs = af / (af.std() + 1e-9)
    t_af = np.arange(len(afs)) / FPS
    best = None
    for q in range(4):
        start = (ph16 + q * Q) / FFR - 0.008
        slot = np.mod(t_af - start, P / FFR) < (Q / FFR) * 1.15
        val = float(afs[slot].mean())
        if best is None or val > best[0]:
            best = (val, q)
    ph = (ph16 + best[1] * Q) % P
    dur = len(x) / SR
    beats = (ph + np.arange(0, int((n - ph) / P) + 2) * P) / FFR
    beats = beats[beats < dur]
    return {"bpm": float(bpm), "period": P / FFR, "phase": ph / FFR, "beats": beats, "steady": True,
            "q_phase": ph16 / FFR}


def _onset_list(A: np.ndarray, fl_low, fl_mid, fl_high, x_len: int):
    """Атаки трека: время начала удара (сек), сила (огибающая), доли полос низ/середина/верх."""
    pk = _env_peaks(A, FFR, 0.030, 0.6)
    if not len(pk):
        return np.zeros(0), np.zeros(0), np.zeros((0, 3))
    v, st = _attack_start(A, pk, -4, 0)
    t = st / FFR
    # полосы — из потока атак 86 к/с в ±2 кадрах вокруг атаки
    idx = np.clip(np.round(t * FPS).astype(int), 0, len(fl_low) - 1)
    bands = []
    for env in (fl_low, fl_mid, fl_high):
        e = env / (np.percentile(env, 95) + 1e-9)
        bands.append(np.max([e[np.clip(idx + k, 0, len(e) - 1)] for k in range(-1, 3)], axis=0))
    return t, A[pk].astype(np.float64), np.stack(bands, 1)


# ------------------------------------------------------------------ #
#  Анализ трека                                                       #
# ------------------------------------------------------------------ #

def _key(path: str) -> str:
    try:
        st = os.stat(path)
        sig = f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        sig = str(path)
    return hashlib.sha1(sig.encode("utf-8", "ignore")).hexdigest()[:20]


# ------------------------------------------------------------------ #
#  v7: ударные отдельно от мелодии/голоса (HPSS), протяжность звука,    #
#  смена высоты, повторы тактов                                        #
# ------------------------------------------------------------------ #

HP_HOP = 512
HP_FR = SR / HP_HOP                    # ≈ 43 кадра/с


def _median_filter(a: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    try:
        from scipy.ndimage import median_filter
        return median_filter(a, size=size, mode="nearest")
    except Exception:                                      # noqa: BLE001  (без scipy — медленнее, но то же)
        ax = 0 if size[0] > 1 else 1
        k = size[ax]
        pad = [(0, 0), (0, 0)]
        pad[ax] = (k // 2, k - 1 - k // 2)
        ap = np.pad(a, pad, mode="edge")
        out = np.empty_like(a)
        win = np.lib.stride_tricks.sliding_window_view(ap, k, axis=ax)
        for s in range(0, a.shape[0], 1024):
            out[s:s + 1024] = np.median(win[s:s + 1024], axis=-1)
        return out


def _spec256(x: np.ndarray) -> np.ndarray:
    """Амплитудный спектр (кадры по 512 сэмплов × 256 полос по ~43 Гц до 11 кГц)."""
    nf = 1024
    win = np.hanning(nf).astype(np.float32)
    n = 1 + len(x) // HP_HOP
    xp = np.concatenate([np.zeros(nf // 2, np.float32), x, np.zeros(nf, np.float32)])
    idx0 = np.arange(nf)[None, :]
    S = np.empty((n, 256), np.float32)
    for s in range(0, n, 2048):
        k = np.arange(s, min(n, s + 2048))
        mag = np.abs(np.fft.rfft(xp[k[:, None] * HP_HOP + idx0] * win, axis=1))
        S[s:s + len(k)] = mag[:, :512].reshape(len(k), 256, 2).mean(2)
    return S


def _logbands(S: np.ndarray, nb=32, lo=60.0, hi=10000.0) -> np.ndarray:
    freqs = (np.arange(256) * 2 + 0.5) * SR / 1024
    edges = np.geomspace(lo, hi, nb + 2)
    fb = np.zeros((nb, 256), np.float32)
    for i in range(nb):
        a, c, b = edges[i], edges[i + 1], edges[i + 2]
        fb[i] = np.clip(np.minimum((freqs - a) / max(1e-6, c - a), (b - freqs) / max(1e-6, b - c)), 0, None)
        if fb[i].sum() == 0:
            fb[i, int(np.argmin(np.abs(freqs - c)))] = 1.0
        fb[i] /= fb[i].sum()
    return (S * S) @ fb.T


def _chroma(S: np.ndarray) -> np.ndarray:
    freqs = (np.arange(256) * 2 + 0.5) * SR / 1024
    ok = (freqs > 90) & (freqs < 2200)
    pc = np.round(12 * np.log2(np.maximum(freqs, 1) / 440.0)).astype(int) % 12
    C = np.zeros((S.shape[0], 12), np.float32)
    P = S * S
    for k in range(12):
        m = ok & (pc == k)
        if m.any():
            C[:, k] = P[:, m].sum(1)
    return C / (np.linalg.norm(C, axis=1, keepdims=True) + 1e-9)


def _block_norm(v: np.ndarray, rate: float, win_s=4.0, q=92) -> np.ndarray:
    """v / местный «потолок» (перцентиль по секундным блокам, ±win_s) — тихий куплет не пустеет."""
    bl = max(1, int(rate))
    nb = max(1, len(v) // bl)
    blocks = v[:nb * bl].reshape(nb, bl)
    pk = np.percentile(blocks, q, axis=1)
    w = int(win_s)
    loc = np.array([pk[max(0, i - w):i + w + 1].mean() for i in range(nb)])
    g = float(np.percentile(v, 95)) + 1e-9
    scale = np.interp(np.arange(len(v)) / bl, np.arange(nb) + 0.5, 0.6 * loc + 0.4 * g)
    return np.clip(v / np.maximum(scale, g * 0.15), 0, 2.0)


def _hp_features(x: np.ndarray, tk: np.ndarray) -> dict:
    """Признаки тиков (tk — секунды): pc — удар ударных, hm — новая нота мелодии/голоса, he — громкость
    тональной части (держится ли звук), pe — громкость ударных, hn — смена высоты (хрома),
    ce — «высота» тональной части (центроид, 0 низ … 1 верх)."""
    S = _spec256(x)
    H = _median_filter(S, (13, 1))
    P = _median_filter(S, (1, 13))
    Mh = (H * H) / (H * H + P * P + 1e-12)
    Sh, Sp = S * Mh, S * (1 - Mh)
    Lh = np.log1p(_logbands(Sh) * 1e3)
    Lp = np.log1p(_logbands(Sp) * 1e3)
    # поток атак: тональная — со сдвигом 2 кадра и максимумом соседних полос (вибрато не считается атакой)
    ref = np.maximum.reduce([np.roll(Lh, 1, 1), Lh, np.roll(Lh, -1, 1)])
    ref = np.vstack([ref[:2], ref[:-2]])
    fh = np.maximum(0, Lh - ref).sum(1)
    fp = np.maximum(0, Lp - np.vstack([Lp[:1], Lp[:-1]]))
    fp = (fp * np.linspace(1.3, 0.8, fp.shape[1])).sum(1)
    fh, fp = _block_norm(fh, HP_FR), _block_norm(fp, HP_FR)
    freqs = (np.arange(256) * 2 + 0.5) * SR / 1024
    band = (freqs > 150) & (freqs < 5000)
    he = np.log10((Sh[:, band] ** 2).sum(1) + 1e-9)
    pe = np.log10((Sp ** 2).sum(1) + 1e-9)

    def unit(v):
        a, b = np.percentile(v, 5), np.percentile(v, 98)
        return np.clip((v - a) / max(1e-6, b - a), 0, 1)
    he, pe = unit(_smooth(he, 3)), unit(_smooth(pe, 3))
    C = _chroma(Sh)
    cs = np.cumsum(np.vstack([np.zeros((1, 12), np.float32), C]), 0)
    n = len(C)
    w = 5
    idx = np.arange(n)
    a0, a1 = np.clip(idx - w, 0, n), idx
    b0, b1 = idx, np.clip(idx + w, 0, n)
    before = (cs[a1] - cs[a0]) / np.maximum(1, a1 - a0)[:, None]
    after = (cs[b1] - cs[b0]) / np.maximum(1, b1 - b0)[:, None]
    hn = 1 - (before * after).sum(1) / (np.linalg.norm(before, axis=1) * np.linalg.norm(after, axis=1) + 1e-9)
    hn = np.clip(hn / (np.percentile(hn, 95) + 1e-9), 0, 1.5)
    pw = (Sh[:, band] ** 2)
    lf = np.log2(freqs[band])
    ce = (pw * lf).sum(1) / (pw.sum(1) + 1e-12)
    ce = unit(_smooth(ce, 5))
    f = np.clip(np.round(tk * HP_FR).astype(int), 0, n - 1)
    fm1, fp1 = np.clip(f - 1, 0, n - 1), np.clip(f + 1, 0, n - 1)
    fp2 = np.clip(f + 2, 0, n - 1)
    out = {"pc": np.maximum.reduce([fp[fm1], fp[f], fp[fp1]]),
           "hm": np.maximum.reduce([fh[fm1], fh[f], fh[fp1]]),
           "he": he[fp2], "pe": pe[fp1], "hn": np.maximum(hn[f], hn[fp1]), "ce": ce[fp1]}
    # повторы тактов считаются снаружи — им нужна хрома и ударные по кадрам
    out["_C"], out["_Lp"] = C, Lp
    return out


# ------------------------------------------------------------------ #
#  Голос: отдельная дорожка вокала → слоги («флоу»), где поют, протяжные ноты  #
# ------------------------------------------------------------------ #

def vocal_stem(path: str, progress=None, cancel=None, allow_download: bool = True):
    """Вокал трека отдельно (нейросеть MDX Kim Vocal 2 из студии ECHOES; без неё — стерео-центр).
    → (моно float32 на SR, "model"/"fallback") или (None, "") — не получилось. Результат кэшируется."""
    try:
        import daw_sep
        r = daw_sep.separate(path, progress=progress, cancel=cancel, allow_download=allow_download,
                             quality="fast" if daw_sep.cached(path) is None else "cached")
    except Exception as e:                                 # noqa: BLE001
        if "отменено" in str(e):
            raise
        print("[mapgen] vocals:", e)
        return None, ""
    v = np.asarray(r["vocals"], np.float32)
    if v.ndim == 2:
        v = v.mean(1)
    import daw_dsp
    k = int(round(daw_dsp.SR / SR))
    if k > 1:                                              # 44.1 → 22.05 кГц (для атак слогов — достаточно)
        n = len(v) // k * k
        v = v[:n].reshape(-1, k).mean(1)
    return np.ascontiguousarray(v, np.float32), str(r.get("method") or "")


def _vocal_frames(v: np.ndarray):
    """По кадрам HP_FR: fv — сила начала слога (атака голоса, вибрато не считается), va — поют ли (0…1)."""
    S = _spec256(v)
    freqs = (np.arange(256) * 2 + 0.5) * SR / 1024
    band = (freqs > 120) & (freqs < 7000)
    Lv = np.log1p(_logbands(S, 32, 120.0, 8000.0) * 1e3)
    ref = np.maximum.reduce([np.roll(Lv, 1, 1), Lv, np.roll(Lv, -1, 1)])
    ref = np.vstack([ref[:2], ref[:-2]])
    fv = np.maximum(0, Lv - ref).sum(1)
    ev = 10 * np.log10((S[:, band] ** 2).sum(1) + 1e-10)
    ev = _smooth(ev, 3)
    lo, hi = np.percentile(ev, 25), np.percentile(ev, 97)
    va = np.clip((ev - (lo + 0.35 * (hi - lo))) / max(1e-6, 0.45 * (hi - lo)), 0, 1)
    fv = _block_norm(fv * (0.25 + 0.75 * va), HP_FR)
    return fv, va


def _vocal_onsets(fv: np.ndarray, va: np.ndarray):
    """Начала слогов: локальные максимумы fv выше скользящего порога, где голос есть. → (секунды, сила)."""
    n = len(fv)
    if n < 8:
        return np.zeros(0), np.zeros(0)
    mx = _win_max(fv, 3)
    base = _smooth(fv, int(HP_FR * 0.4)) * 1.3 + 0.08
    pk = np.nonzero((fv >= mx - 1e-9) & (fv > base) & (va > 0.2))[0]
    return pk / HP_FR, np.clip(fv[pk], 0, 2.0)


def _vocal_ticks(fv: np.ndarray, va: np.ndarray, tk: np.ndarray) -> dict:
    """Признаки голоса на тиках: vo — слог здесь, va — поют, vh — голос тянется дальше (под слайдер)."""
    n = len(fv)
    f = np.clip(np.round(tk * HP_FR).astype(int), 0, n - 1)
    fm1, fp1 = np.clip(f - 1, 0, n - 1), np.clip(f + 1, 0, n - 1)
    vo = np.maximum.reduce([fv[fm1], fv[f], fv[fp1]])
    cs = np.concatenate([[0.0], np.cumsum(va)])
    a, b = np.clip(f + 3, 0, n), np.clip(f + 12, 0, n)    # ~70…280 мс после тика
    vh = (cs[b] - cs[a]) / np.maximum(1, b - a)
    # «держится»: голос звучит, а нового слога в ближайшие ~280 мс нет
    cf = np.concatenate([[0.0], np.cumsum(fv > 0.6)])
    new = (cf[b] - cf[a]) > 0
    vh = vh * np.where(new, 0.5, 1.0)
    return {"vo": np.clip(vo, 0, 2.0), "va": va[fp1], "vh": vh}


def _bar_repeats(bars: np.ndarray, tk: np.ndarray, s: np.ndarray, C: np.ndarray, Lp: np.ndarray) -> list:
    """Для каждого такта — самый похожий из более ранних (ритм ударов + хрома + тембр ударных), или −1.
    Повторяющиеся места песни маппер ставит одинаковым ритмом — генератор тоже."""
    nb = len(bars) - 1
    if nb < 3:
        return [-1] * max(0, nb)
    R, CH, PB = [], [], []
    for i in range(nb):
        a, b = bars[i], bars[i + 1]
        m = (tk >= a - 0.01) & (tk < b - 0.01)
        pos = np.clip(np.round((tk[m] - a) / max(1e-6, b - a) * 16).astype(int), 0, 15)
        r = np.zeros(16)
        np.maximum.at(r, pos, s[m])
        R.append(r)
        f0, f1 = int(a * HP_FR), max(int(a * HP_FR) + 1, int(b * HP_FR))
        CH.append(C[f0:f1].mean(0))
        PB.append(Lp[f0:f1].mean(0))
    R, CH, PB = np.array(R), np.array(CH), np.array(PB)

    def cos(M):
        M = M - M.mean(1, keepdims=True) * 0.5
        M = M / (np.linalg.norm(M, axis=1, keepdims=True) + 1e-9)
        return M @ M.T
    sim = 0.45 * cos(R) + 0.35 * cos(CH) + 0.2 * cos(PB)
    rep = []
    for i in range(nb):
        if i == 0:
            rep.append(-1)
            continue
        j = int(np.argmax(sim[i, :i]))
        rep.append(j if sim[i, j] > 0.86 else -1)
    return rep


def analyze(path: str, progress=None, grid_ms=None, vocals=None) -> dict:
    """grid_ms — готовая сетка долей (мс), например из карты osu!: тогда темп не ищется.
    vocals — дорожка голоса (моно, SR) из vocal_stem(): ноты учитывают слоги и протяжки голоса («флоу»)."""
    t0 = time.perf_counter()

    def prog(f, s):
        if progress:
            progress(f, s)
    prog(0.02, "Читаю трек…")
    x = decode(path)
    dur = len(x) / SR
    prog(0.15, "Слушаю: спектр…")
    B = _bands(x)
    L = np.log1p(B * 1e4)
    _, centers = _filterbank()
    # поток атак: рост энергии в полосе относительно максимума соседей (меньше ложных от вибрато)
    prev = np.maximum.reduce([np.roll(L, 1, 1), L, np.roll(L, -1, 1)])
    prev = np.vstack([prev[:1], prev[:1], prev[:-2]])
    fl = np.maximum(0, L - prev)
    low = fl[:, centers < 180].sum(1)
    mid = fl[:, (centers >= 180) & (centers < 2500)].sum(1)
    high = fl[:, centers >= 2500].sum(1)
    onset = low * 1.2 + mid + high * 0.8
    rms = np.sqrt(B.sum(1) + 1e-12)
    db = 20 * np.log10(rms + 1e-9)
    prog(0.4, "Ищу темп и начала ударов…")
    A = _td_env(x)
    fixed = grid_ms is not None and len(grid_ms) >= 8
    if fixed:
        grid = np.asarray(grid_ms, float) / 1000.0
        grid = grid[(grid >= 0) & (grid < dur)]
        bpm, steady = 60.0 / float(np.median(np.diff(grid))), True
    else:
        tm = _timing_v6(x, B, onset, A)
        bpm, steady = tm["bpm"], tm["steady"]
        grid = tm["beats"]
        if len(grid) < 4:
            p = 60.0 / bpm
            grid = np.arange(tm["phase"] % p, dur, p)
    prog(0.6, "Слушаю удары…")
    on_t, on_s, on_b = _onset_list(A, low, mid, high, len(x))
    vfr = None
    v_on_t, v_on_n = np.zeros(0), np.zeros(0)
    if vocals is not None and len(vocals) > SR:
        try:
            vfr = _vocal_frames(np.asarray(vocals, np.float32))
            v_on_t, v_on_n = _vocal_onsets(*vfr)
        except Exception as e:                             # noqa: BLE001
            print("[mapgen] vocal frames:", e)
            vfr = None
    # сверка: сильные атаки должны ложиться на сетку 1/4 — общий сдвиг сетки по медиане отклонений
    if len(on_t) > 32 and len(grid) > 8 and not fixed:
        Qs = float(np.median(np.diff(grid))) / 4
        strong_on = on_s >= np.percentile(on_s, 60)
        rel = (on_t[strong_on] - grid[0]) / Qs
        r = (rel - np.round(rel)) * Qs
        r = r[np.abs(r) < Qs * 0.3]
        if len(r) > 16:
            delta = float(np.median(r))
            if abs(delta) > 0.003:
                grid = grid + delta
                grid = grid[(grid >= 0) & (grid < dur)]
    prog(0.7, "Такты и части песни…")
    # сильная доля такта: бочка на «раз», малый на «два» и «четыре»
    lowb = _interp(_smooth(low, 3), FPS, grid + 0.03)
    midb = _interp(_smooth(mid, 3), FPS, grid + 0.03)
    ph = int(np.argmax([lowb[p::4].mean() + 0.15 * midb[(p + 2) % 4::4].mean() if len(lowb[p::4]) else 0
                        for p in range(4)]))
    # сила атак — относительно окрестности ±4 с (тихий куплет не пустеет, громкий припев не забивается)

    def lnorm_on(v):
        if not len(v):
            return v
        g = max(1e-6, np.percentile(v, 92))
        out = np.empty_like(v)
        j0 = j1 = 0
        for i in range(len(v)):
            while on_t[j0] < on_t[i] - 4.0:
                j0 += 1
            while j1 < len(v) and on_t[j1] <= on_t[i] + 4.0:
                j1 += 1
            loc = np.percentile(v[j0:j1], 92)
            out[i] = v[i] / max(1e-6, 0.65 * loc + 0.35 * g)
        return np.clip(out, 0, 2.0)
    on_n = lnorm_on(on_s)
    on_bn = np.stack([lnorm_on(on_b[:, k] * (0.5 + on_s / (on_s.max() + 1e-9))) for k in range(3)], 1) \
        if len(on_s) else np.zeros((0, 3))
    # тики: каждая доля делится на 4 или на 3 (триоли) — смотря где в ней атаки
    tk, met, div = [], [], []
    for i in range(len(grid) - 1):
        a, b = float(grid[i]), float(grid[i + 1])
        d = b - a
        j0 = np.searchsorted(on_t, a - 0.04)
        j1 = np.searchsorted(on_t, b - 0.04)
        f = (on_t[j0:j1] - a) / d
        w = on_n[j0:j1]
        if len(v_on_t):                                    # слоги голоса тоже решают: доля на 4 или триоли
            k0 = np.searchsorted(v_on_t, a - 0.04)
            k1 = np.searchsorted(v_on_t, b - 0.04)
            if k1 > k0:
                f = np.concatenate([f, (v_on_t[k0:k1] - a) / d])
                w = np.concatenate([w, 0.8 * v_on_n[k0:k1]])
        inner = (f > 0.1) & (f < 0.9)
        d4 = np.min(np.abs(f[:, None] - np.array([0.25, 0.5, 0.75])[None, :]), 1) * d if len(f) else f
        d3 = np.min(np.abs(f[:, None] - np.array([1 / 3, 2 / 3])[None, :]), 1) * d if len(f) else f
        tol = min(0.03, d / 12)
        sc4 = float(w[inner & (d4 < tol)].sum()) if len(f) else 0.0
        sc3 = float(w[inner & (d3 < tol) & (d4 >= tol)].sum()) if len(f) else 0.0
        n_div = 3 if (sc3 > 0.7 and sc3 > 1.3 * sc4) else 4
        for q in range(n_div):
            tk.append(a + d * q / n_div)
            if q == 0:
                met.append(0 if (i - ph) % 4 == 0 else 1)
                div.append(1)
            elif n_div == 4:
                met.append(2 if q == 2 else 3)
                div.append(2 if q == 2 else 4)
            else:
                met.append(3)
                div.append(3)
    tk.append(float(grid[-1]))
    met.append(0 if (len(grid) - 1 - ph) % 4 == 0 else 1)
    div.append(1)
    tk = np.array(tk)
    met = np.array(met, int)
    div = np.array(div, int)
    # атаки → ближайший тик (если близко): сила тика = сила атаки; где атак нет — почти ноль
    n_all = np.zeros(len(tk))
    n_low, n_mid, n_high = np.zeros(len(tk)), np.zeros(len(tk)), np.zeros(len(tk))
    if len(on_t) and len(tk) > 1:
        j = np.clip(np.searchsorted(tk, on_t), 1, len(tk) - 1)
        left = on_t - tk[j - 1] < tk[j] - on_t
        near = np.where(left, j - 1, j)
        spacing = np.diff(tk, append=tk[-1] + 1)
        sp_near = np.minimum(spacing[near], np.concatenate([[1.0], spacing])[near])
        dist = np.abs(on_t - tk[near])
        ok = dist <= np.minimum(0.032, 0.3 * sp_near)
        for k in np.nonzero(ok)[0]:
            ti = near[k]
            if on_n[k] > n_all[ti]:
                n_all[ti] = on_n[k]
                n_low[ti], n_mid[ti], n_high[ti] = on_bn[k]
        # местная подгонка: если сильные удары рядом стабильно чуть раньше/позже узлов сетки —
        # тики (и ноты на них) сдвигаются к ударам (не больше 25 мс, плавно по времени)
        ks = np.nonzero(ok)[0]
        if len(ks) > 20 and not fixed:
            r = np.clip(on_t[ks] - tk[near[ks]], -0.025, 0.025)
            w = on_n[ks] ** 2
            tt = on_t[ks]
            cw = np.concatenate([[0], np.cumsum(w)])
            cr = np.concatenate([[0], np.cumsum(w * r)])
            lo_i = np.searchsorted(tt, tk - 2.0)
            hi_i = np.searchsorted(tt, tk + 2.0)
            sw = cw[hi_i] - cw[lo_i]
            off = np.where(sw > 1e-6, (cr[hi_i] - cr[lo_i]) / np.maximum(sw, 1e-9), 0.0)
            off = _smooth(off, 9) if len(off) > 12 else off
            tk = tk + np.clip(off, -0.025, 0.025)
            grid = tk[div == 1]
    # где атак нет, но звук идёт — слабый след (для слайдеров и пустых мест лёгких сложностей)
    resid = _interp(_smooth(onset, 2), FPS, tk)
    resid = resid / (np.percentile(resid, 95) + 1e-9) * 0.12
    n_all = np.maximum(n_all, np.minimum(resid, 0.12))
    # громкость в тиках (0..1 по всей песне)
    dbt = np.interp(tk * FPS, np.arange(len(db)), _smooth(db, int(FPS * 0.25)))
    lo_db, hi_db = np.percentile(dbt, 5), np.percentile(dbt, 97)
    loud = np.clip((dbt - lo_db) / max(1.0, hi_db - lo_db), 0, 1)
    # части песни: признаки тактов → новизна
    bars = grid[ph::4]
    if len(bars) < 2:
        bars = np.array([0.0, dur])
    feats = []
    for i in range(len(bars) - 1):
        a, b = int(bars[i] * FPS), max(int(bars[i] * FPS) + 1, int(bars[i + 1] * FPS))
        v = L[a:b].mean(0)
        feats.append(np.concatenate([v / (np.linalg.norm(v) + 1e-9), [db[a:b].mean() / 30.0]]))
    F = np.array(feats) if feats else np.zeros((1, NBANDS + 1))
    nb = len(F)
    nov = np.zeros(nb)
    K = 4
    if nb > 2 * K:
        S = F @ F.T
        for i in range(K, nb - K):
            a = S[i - K:i, i - K:i].mean() + S[i:i + K, i:i + K].mean()
            c = S[i - K:i, i:i + K].mean()
            nov[i] = a - 2 * c
    bounds = [0]
    order = np.argsort(-nov)
    for i in order:
        if nov[i] <= np.percentile(nov, 70) or i == 0:
            continue
        j = int(round(i / 4) * 4) if abs(i - round(i / 4) * 4) <= 1 else int(i)
        if all(abs(j - b) >= 4 for b in bounds) and 0 < j < nb:
            bounds.append(j)
    bounds = sorted(set(bounds)) + [nb]
    sections = []
    for i in range(len(bounds) - 1):
        a, b = bounds[i], bounds[i + 1]
        if b <= a:
            continue
        ta, tb = float(bars[a]) if a < len(bars) else dur, float(bars[b]) if b < len(bars) else dur
        m = (tk >= ta) & (tk < tb)
        en = float(loud[m].mean()) if m.any() else 0.0
        dn = float((n_all[m] > 0.45).mean()) if m.any() else 0.0
        sections.append({"s": ta * 1000, "e": tb * 1000, "en": en, "dn": dn, "bars": b - a})
    if sections:
        sections[0]["s"] = 0.0
        sections[-1]["e"] = dur * 1000
    # интенсивность части: громкость + плотность атак
    if sections:
        sc = np.array([0.65 * s["en"] + 0.35 * s["dn"] for s in sections])
        lo_, hi_ = sc.min(), sc.max()
        for s, v in zip(sections, sc):
            s["int"] = float((v - lo_) / max(1e-6, hi_ - lo_)) if hi_ > lo_ else 0.6
        # kiai: самые мощные части не короче 8 тактов, не больше ~35 % песни
        total = dur * 1000
        used = 0.0
        for i in np.argsort(-sc):
            s = sections[i]
            ln = s["e"] - s["s"]
            if s["int"] < 0.62 or s["bars"] < 6:
                continue
            if used + ln > total * 0.38 and used > 0:
                continue
            s["kiai"] = True
            used += ln
        if used == 0:
            sections[int(np.argmax(sc))]["kiai"] = True
    # перерывы: тихо дольше 4 с
    quiet = dbt < (np.percentile(dbt, 50) - 16)
    breaks = []
    i = 0
    while i < len(tk):
        if quiet[i]:
            j = i
            while j < len(tk) and quiet[j]:
                j += 1
            if tk[min(j, len(tk) - 1)] - tk[i] >= 4.0:
                breaks.append([float(tk[i] * 1000), float(tk[min(j, len(tk) - 1)] * 1000)])
            i = j
        else:
            i += 1
    # где звук вообще есть (начало/конец)
    sound = np.nonzero(dbt > np.percentile(dbt, 50) - 24)[0]
    t_first = float(tk[sound[0]]) if len(sound) else 0.0
    t_last = float(tk[sound[-1]]) if len(sound) else dur
    kiai = [[s["s"], s["e"]] for s in sections if s.get("kiai")]
    prog(0.8, "Слушаю мелодию, голос и ударные…")
    hp = _hp_features(x, tk)
    rep = _bar_repeats(np.append(bars, dur) if bars[-1] < dur - 0.5 else bars, tk, n_all, hp.pop("_C"),
                       hp.pop("_Lp"))
    prog(0.95, "Готово")
    ticks = {"t": [round(float(v) * 1000, 2) for v in tk], "m": met.tolist(), "d": div.tolist(),
             "s": np.round(n_all, 3).tolist(), "lo": np.round(n_low, 3).tolist(),
             "mi": np.round(n_mid, 3).tolist(), "hi": np.round(n_high, 3).tolist(),
             "ld": np.round(loud, 3).tolist()}
    for k, v in hp.items():
        ticks[k] = np.round(v, 3).tolist()
    if vfr is not None:
        for k, v in _vocal_ticks(vfr[0], vfr[1], tk).items():
            ticks[k] = np.round(v, 3).tolist()
    return {
        "v": VERSION, "dur": dur * 1000, "bpm": round(float(bpm), 4), "steady": bool(steady),
        "beats": [round(float(b) * 1000, 2) for b in grid], "phase": ph, "ticks": ticks,
        "bars": {"t": [round(float(b) * 1000, 2) for b in bars], "rep": rep},
        "sections": sections, "kiai": kiai, "breaks": breaks,
        "first": t_first * 1000, "last": t_last * 1000,
        "preview": preview_time({"kiai": kiai, "dur": dur * 1000}),
        "vocals": bool(vfr is not None),
        "took": round(time.perf_counter() - t0, 2),
    }


def _win_max(v: np.ndarray, k: int) -> np.ndarray:
    """max(v[i-k … i+k])"""
    out = v.copy()
    for d in range(1, k + 1):
        out[d:] = np.maximum(out[d:], v[:-d])
        out[:-d] = np.maximum(out[:-d], v[d:])
    return out


def _shift(v: np.ndarray, d: int, fill=0.0) -> np.ndarray:
    """v[i + d] (за краем — fill)"""
    out = np.full_like(v, fill, dtype=float)
    if d > 0:
        out[:-d] = v[d:]
    elif d < 0:
        out[-d:] = v[:d]
    else:
        out[:] = v
    return out


FEAT_NAMES = ["bias", "s", "s2", "pc", "hm", "lo", "mi", "hi", "hn", "hm_hn", "he", "dhe", "pe",
              "s_rel", "pc_rel", "hm_rel", "down", "beat", "half", "quart", "trip", "s_beat", "s_half", "s_quart",
              "pc_quart", "hm_half", "ld", "sec", "kiai", "s_prev", "s_next", "bpm", "bpm_quart", "sus", "pc_next",
              "hm_next", "rep_s",
              # позиция в такте 4/4 (шестнадцатые; триоли — отдельным признаком trip)
              "p1", "p2", "p3", "p4", "p5", "p6", "p7", "p8", "p9", "p10", "p11", "p12", "p13", "p14", "p15",
              # контекст доли: сильнейший ли удар в своей доле, сколько атак в доле и рядом
              "s_beatmax", "is_max", "n_on_beat", "n_on_next", "s_prev2", "s_next2", "pc_prev", "hm_prev",
              # голос (vocal_stem): слог, слог относительно соседей, поют ли, тянется ли нота, слог вне доли
              # (синкопы флоу), слоги рядом, слог там, где ударных нет
              "vo", "vo_rel", "va", "vh", "vo_off", "vo_prev", "vo_next", "vo_bare"]


def tick_matrix(an: dict) -> np.ndarray:
    """Признаки каждого тика для моделей «куда маппер ставит ноту / конец слайдера» (FEAT_NAMES)."""
    T = an["ticks"]
    n = len(T["t"])
    t = np.asarray(T["t"], float)
    g = lambda k, d=0.0: np.asarray(T.get(k) or [d] * n, float)    # noqa: E731
    s, lo, mi, hi = g("s"), g("lo"), g("mi"), g("hi")
    pc, hm, he, pe, hn = g("pc", 0.3), g("hm", 0.3), g("he", 0.5), g("pe", 0.5), g("hn", 0.3)
    ld = g("ld", 0.5)
    m = np.asarray(T["m"], int)
    d = np.asarray(T.get("d") or [1 if v <= 1 else 2 if v == 2 else 4 for v in T["m"]], int)
    sec = np.full(n, 0.6)
    kia = np.zeros(n)
    for x in an.get("sections") or []:
        mk = (t >= x["s"]) & (t < x["e"])
        sec[mk] = x.get("int", 0.6)
        if x.get("kiai"):
            kia[mk] = 1.0
    down = (m == 0).astype(float)
    beat = (m == 1).astype(float)
    half = (d == 2).astype(float)
    quart = (d == 4).astype(float)
    trip = (d == 3).astype(float)
    rel = lambda v: v / (_win_max(v, 2) + 0.05)                     # noqa: E731
    bpm = float(an.get("bpm", 140)) / 200.0
    sus = np.minimum(_shift(he, 2, 0), _shift(he, 1, 0)) - he * 0.5   # держится ли тональный звук дальше
    # тот же такт в повторе: сила удара на той же позиции в похожем такте (-1 → своя)
    rep_s = s.copy()
    B = an.get("bars") or {}
    bt, rp = np.asarray(B.get("t") or [], float), B.get("rep") or []
    if len(bt) > 2 and rp:
        bi = np.clip(np.searchsorted(bt, t + 1, "right") - 1, 0, len(bt) - 1)
        for i in range(n):
            b = int(bi[i])
            if b < len(rp) and rp[b] >= 0 and b + 1 < len(bt):
                a = rp[b]
                tt = bt[a] + (t[i] - bt[b]) * (bt[min(a + 1, len(bt) - 1)] - bt[a]) / max(1.0, bt[b + 1] - bt[b])
                j = int(np.clip(np.searchsorted(t, tt), 1, n - 1))
                j = j - 1 if abs(t[j - 1] - tt) < abs(t[j] - tt) else j
                if abs(t[j] - tt) < 25:
                    rep_s[i] = s[j]
    # позиция в такте и контекст доли
    pos = np.full(n, -1)
    beat_id = np.zeros(n, int)
    bib, sub, bid = 0, 0, -1
    for i in range(n):
        if d[i] == 1:
            bid += 1
            sub = 0
            bib = 0 if m[i] == 0 else bib + 1
        else:
            sub += 1
        beat_id[i] = max(0, bid)
        if d[i] != 3 and bib < 4:
            pos[i] = bib * 4 + (sub if d[i] == 4 else 2 if d[i] == 2 else 0)
    P = np.zeros((n, 15))
    ok = pos >= 1
    P[np.nonzero(ok)[0], np.clip(pos[ok] - 1, 0, 14)] = 1.0
    nb = beat_id.max() + 1
    bmax = np.zeros(nb)
    np.maximum.at(bmax, beat_id, s)
    onb = np.zeros(nb)
    np.add.at(onb, beat_id, (s > 0.3).astype(float))
    s_beatmax = bmax[beat_id]
    is_max = (s >= s_beatmax - 1e-6).astype(float) * (s > 0.2)
    n_next = onb[np.clip(beat_id + 1, 0, nb - 1)]
    vo, va, vh = g("vo"), g("va"), g("vh")
    X = np.stack([np.ones(n), s, s * s, pc, hm, lo, mi, hi, hn, hm * hn, he, he - _shift(he, -1, 0.0), pe,
                  rel(s), rel(pc), rel(hm), down, beat, half, quart, trip, s * beat, s * half, s * quart,
                  pc * quart, hm * half, ld, sec, kia, _shift(s, -1), _shift(s, 1), np.full(n, bpm),
                  bpm * quart, sus, np.maximum(_shift(pc, 1), _shift(pc, 2)), np.maximum(_shift(hm, 1), _shift(hm, 2)),
                  rep_s] + [P[:, k] for k in range(15)] +
                 [s_beatmax, is_max, onb[beat_id] / 4.0, n_next / 4.0, _shift(s, -2), _shift(s, 2), _shift(pc, -1),
                  _shift(hm, -1),
                  vo, rel(vo), va, vh, vo * (half + quart + trip), _shift(vo, -1), _shift(vo, 1),
                  vo * np.clip(1 - 2 * pc, 0, 1)], 1)
    return X


def preview_time(an: dict) -> float:
    """Откуда играть превью (мс): первый припев не в самом начале трека, иначе ~40 % длины."""
    dur = float(an.get("dur") or 0)
    for a, _b in an.get("kiai") or []:
        if a >= min(8000.0, dur * 0.08):
            return float(a)
    return dur * 0.4


# ------------------------------------------------------------------ #
#  Пути слайдеров (как в osu!)                                        #
# ------------------------------------------------------------------ #

def _bezier(P: np.ndarray, n: int) -> np.ndarray:
    t = np.linspace(0, 1, n)[:, None]
    m = len(P) - 1
    if m > 24:
        # длинные сегменты карт osu! (десятки точек): де Кастельжо — без переполнения биномов
        B = np.repeat(np.asarray(P, float)[None, :, :], n, axis=0)
        tt = t[:, :, None]
        for k in range(m):
            B = B[:, :-1, :] * (1 - tt) + B[:, 1:, :] * tt
        return B[:, 0, :]
    out = np.zeros((n, 2))
    for i in range(m + 1):
        out += math.comb(m, i) * (t ** i) * ((1 - t) ** (m - i)) * P[i]
    return out


def _arc(P: np.ndarray, length: float) -> np.ndarray | None:
    a, b, c = P
    d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
    if abs(d) < 1e-3:
        return None
    ux = ((a @ a) * (b[1] - c[1]) + (b @ b) * (c[1] - a[1]) + (c @ c) * (a[1] - b[1])) / d
    uy = ((a @ a) * (c[0] - b[0]) + (b @ b) * (a[0] - c[0]) + (c @ c) * (b[0] - a[0])) / d
    cen = np.array([ux, uy])
    r = float(np.linalg.norm(a - cen))
    if r > 5000:
        return None
    t0 = math.atan2(a[1] - cen[1], a[0] - cen[0])
    cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    sgn = 1.0 if cross > 0 else -1.0
    n = max(8, int(length / 3))
    ang = t0 + sgn * np.linspace(0, length / r, n)
    return np.stack([cen[0] + r * np.cos(ang), cen[1] + r * np.sin(ang)], 1)


def _catmull(P: np.ndarray) -> np.ndarray:
    """Кривая Catmull-Rom (тип «C» старых карт osu!) через опорные точки."""
    out = []
    n = len(P)
    t = np.linspace(0, 1, 50, endpoint=False)[:, None]
    t2, t3 = t * t, t * t * t
    for i in range(n - 1):
        v1 = P[i - 1] if i > 0 else P[i]
        v2, v3 = P[i], P[i + 1]
        v4 = P[i + 2] if i + 2 < n else v3 + (v3 - v2)
        out.append(0.5 * (2 * v2 + (-v1 + v3) * t + (2 * v1 - 5 * v2 + 4 * v3 - v4) * t2
                          + (-v1 + 3 * v2 - 3 * v3 + v4) * t3))
    out.append(P[-1:])
    return np.vstack(out)


def slider_path(ct: str, pts, length: float, step: float = 2.0) -> np.ndarray:
    """Ломаная пути слайдера с шагом ~step px, ровно длины length (как в osu!: обрезка/продление)."""
    P = np.asarray(pts, float)
    poly = None
    if ct == "P" and len(P) == 3:
        poly = _arc(P, length)
    if ct == "C" and len(P) >= 2:
        poly = _catmull(P)
    if poly is None:
        if ct == "L" or len(P) == 2:
            poly = P
        else:
            segs, cur = [], [P[0]]
            for i in range(1, len(P)):
                cur.append(P[i])
                if i < len(P) - 1 and np.allclose(P[i], P[i + 1]):
                    segs.append(np.array(cur))
                    cur = []
            if len(cur) > 1:
                segs.append(np.array(cur))
            parts = []
            for s in segs:
                if len(s) < 2:
                    continue
                ln = float(np.linalg.norm(np.diff(s, axis=0), axis=1).sum())
                parts.append(_bezier(s, max(8, int(ln / 3))))
            poly = np.vstack(parts) if parts else P
    d = np.linalg.norm(np.diff(poly, axis=0), axis=1)
    cum = np.concatenate([[0], np.cumsum(d)])
    total = cum[-1]
    if total < length - 0.5:                               # продлить последнюю прямую
        v = poly[-1] - poly[-2] if len(poly) > 1 else np.array([1.0, 0.0])
        nv = np.linalg.norm(v)
        v = v / nv if nv > 1e-6 else np.array([1.0, 0.0])
        poly = np.vstack([poly, poly[-1] + v * (length - total)])
        cum = np.concatenate([cum, [length]])
    n = max(2, int(length / step) + 1)
    s = np.linspace(0, length, n)
    x = np.interp(s, cum, poly[:, 0])
    y = np.interp(s, cum, poly[:, 1])
    return np.stack([x, y], 1)


def path_at(path: np.ndarray, f: float) -> tuple[float, float]:
    f = min(1.0, max(0.0, f)) * (len(path) - 1)
    i = int(f)
    if i >= len(path) - 1:
        return float(path[-1, 0]), float(path[-1, 1])
    k = f - i
    return (float(path[i, 0] * (1 - k) + path[i + 1, 0] * k), float(path[i, 1] * (1 - k) + path[i + 1, 1] * k))


def slider_pos(o: dict, t: float) -> tuple[float, float]:
    """Позиция шара слайдера в момент t (мс)."""
    span = o["span"]
    el = max(0.0, min(o["slides"] * span, t - o["t"]))
    k = int(el // span) if span > 0 else 0
    f = (el - k * span) / span if span > 0 else 0
    if k >= o["slides"]:
        k, f = o["slides"] - 1, 1.0
    if k % 2 == 1:
        f = 1 - f
    return path_at(o["_path"], f)


def prepare(m: dict) -> dict:
    """Пути и тики слайдеров (в карте хранятся только опорные точки)."""
    for o in m["objects"]:
        if o["k"] == 1 and "_path" not in o:
            o["_path"] = slider_path(o["ct"], o["pts"], o["len"])
            o["end"] = o["t"] + o["span"] * o["slides"]
            # тики: каждую долю (tick rate 1) по длине слайдера
            beat = o.get("beat", 500.0)
            tick_ms = max(30.0, beat / max(1, int(m.get("tick_rate", 1))))   # 0 — вечный цикл
            ticks = []
            for s in range(o["slides"]):
                k = 1
                while True:
                    tt = k * tick_ms
                    if tt >= o["span"] - 10:
                        break
                    off = tt if s % 2 == 0 else o["span"] - tt
                    ticks.append(o["t"] + s * o["span"] + off)
                    k += 1
            o["ticks"] = sorted(ticks)
            o["repeats"] = [o["t"] + o["span"] * (s + 1) for s in range(o["slides"] - 1)]
    return m


# ------------------------------------------------------------------ #
#  Звёзды (алгоритм сложности osu!, aim/speed strain)                 #
# ------------------------------------------------------------------ #

def star_rating(m: dict, rate: float = 1.0, cs: float | None = None) -> dict:
    objs = m["objects"]
    if len(objs) < 2:
        return {"stars": 0.0, "aim": 0.0, "speed": 0.0}
    r = radius(m["cs"] if cs is None else cs)
    scale = 52.0 / r
    if r < 30:
        scale *= 1 + min(30 - r, 5) / 50
    aim_s = spd_s = 0.0
    sec = 400.0 * rate
    aim_peaks, spd_peaks = [], []
    cur_aim_peak = cur_spd_peak = 0.0
    sec_end = math.ceil(objs[0]["t"] / sec) * sec
    prev = objs[0]
    prev_end_pos = np.array([prev["x"], prev["y"]], float)
    for o in objs[1:]:
        t = o["t"]
        dt = max(50.0, (t - prev["t"]) / rate)
        p = np.array([o["x"], o["y"]], float)
        jump = float(np.linalg.norm(p - prev_end_pos)) * scale
        trav = 0.0
        if prev["k"] == 1:
            trav = prev["len"] * scale * 0.5 * min(prev["slides"], 2)
        d = jump + trav
        while t > sec_end:
            aim_peaks.append(cur_aim_peak)
            spd_peaks.append(cur_spd_peak)
            dec_a = 0.15 ** ((sec_end - prev["t"]) / 1000.0 / rate)
            dec_s = 0.3 ** ((sec_end - prev["t"]) / 1000.0 / rate)
            cur_aim_peak, cur_spd_peak = aim_s * dec_a, spd_s * dec_s
            sec_end += sec
        if o["k"] == 2:
            aim_v = spd_v = 0.0
        else:
            aim_v = d ** 0.99 / dt
            if jump > 125:
                sb = 2.5
            elif jump > 110:
                sb = 1.6 + 0.9 * (jump - 110) / 15
            elif jump > 90:
                sb = 1.2 + 0.4 * (jump - 90) / 20
            elif jump > 45:
                sb = 0.95 + 0.25 * (jump - 45) / 45
            else:
                sb = 0.95
            spd_v = sb / dt
        aim_s = aim_s * 0.15 ** (dt / 1000.0) + aim_v * 26.25
        spd_s = spd_s * 0.3 ** (dt / 1000.0) + spd_v * 1400.0
        cur_aim_peak = max(cur_aim_peak, aim_s)
        cur_spd_peak = max(cur_spd_peak, spd_s)
        prev = o
        if o["k"] == 1:
            ex, ey = (o["_path"][-1] if o["slides"] % 2 == 1 else o["_path"][0]) if "_path" in o else (o["x"], o["y"])
            prev_end_pos = np.array([ex, ey], float)
        else:
            prev_end_pos = p
    aim_peaks.append(cur_aim_peak)
    spd_peaks.append(cur_spd_peak)

    def weigh(peaks):
        v = sorted(peaks, reverse=True)
        return sum(x * 0.9 ** i for i, x in enumerate(v))
    aim = math.sqrt(weigh(aim_peaks)) * 0.0675
    spd = math.sqrt(weigh(spd_peaks)) * 0.0675
    return {"stars": round(aim + spd + abs(aim - spd) / 2, 2), "aim": round(aim, 3), "speed": round(spd, 3)}


def pp_value(stars_aim, stars_speed, acc, n_obj, combo, max_combo, misses, n50, od, ar, mods) -> float:
    """Приблизительный pp (формула ppv2 2018 года)."""
    def base(s):
        return (5 * max(1.0, s / 0.0675) - 4) ** 3 / 100000.0
    length = 0.95 + 0.4 * min(1.0, n_obj / 2000) + (math.log10(n_obj / 2000) * 0.5 if n_obj > 2000 else 0)
    cmb = min(1.0, (combo / max(1, max_combo)) ** 0.8) if max_combo else 1.0
    miss = 0.97 ** misses
    arf = 1.0 + (0.3 * (ar - 10.33) if ar > 10.33 else 0.01 * (8 - ar) if ar < 8 else 0)
    aim = base(stars_aim) * length * miss * cmb * arf * (0.5 + acc / 2) * (0.98 + od * od / 2500)
    if "HD" in mods:
        aim *= 1.0 + 0.04 * (12 - ar)
    if "FL" in mods:
        aim *= 1.45 * length
    spd = base(stars_speed) * length * miss * cmb * (0.02 + acc) / 1.02 * (0.96 + od * od / 1600)
    acc_pp = 1.52163 ** od * max(0.0, acc) ** 24 * 2.83 * min(1.15, (n_obj / 1000) ** 0.3)
    mult = 1.12 * (0.9 if "NF" in mods else 1.0) * (0.95 if "SO" in mods else 1.0)
    return round((aim ** 1.1 + spd ** 1.1 + acc_pp ** 1.1) ** (1 / 1.1) * mult, 1)


# ------------------------------------------------------------------ #
#  Маппер                                                             #
# ------------------------------------------------------------------ #

def _per_cand_overlap(C: np.ndarray, recent, lim: float, k: float) -> np.ndarray:
    """Штраф за наложение для каждого кандидата: Σ по недавним объектам (lim − мин. расстояние)+."""
    if not recent:
        return np.zeros(len(C))
    P = np.concatenate([p for _, p in recent])
    starts = np.cumsum([0] + [len(p) for _, p in recent[:-1]])
    Dm = np.linalg.norm(C[:, None, :] - P[None, :, :], axis=2)
    mins = np.minimum.reduceat(Dm, starts, axis=1)
    return np.clip(lim - mins, 0, None).sum(1) * k


GAP_GRID = [0.125, 0.25, 1 / 3, 0.5, 2 / 3, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0]
_GG = np.array(GAP_GRID)
_GTOL = 0.07 * np.maximum(1.0, _GG)

# веса ритма: lam — сдвиг логитов нажатий (плотность «как у людей» при 1.0), w_gap — грамматика интервалов,
# w_len — длины слайдеров, w_body — штраф за сильные удары под телом слайдера, w_type — «круг или слайдер»
RHY = {"lam": {"easy": 1.0, "normal": 0.6, "hard": 1.25, "insane": 1.0, "expert": 0.5, "extra": 0.6,
               "extreme": 0.85},
       "lam_e": {"easy": 0.0, "normal": 1.0, "hard": 1.0, "insane": 1.0, "expert": 1.0, "extra": 1.0, "extreme": 1.0},
       "sl_bias": {"easy": -0.15, "normal": -0.15, "hard": 0.0, "insane": 0.0, "expert": 0.0, "extra": 0.2,
                   "extreme": 0.0},
       "b_half": {"easy": 0.5, "normal": 0.0, "hard": 0.5, "insane": 0.0, "expert": 0.0, "extra": 0.4, "extreme": 0.4},
       "b_quart": {"easy": 0.0, "normal": 0.0, "hard": 0.0, "insane": 0.25, "expert": 0.0, "extra": 0.0,
                   "extreme": 0.0},
       # сдвиг за сильную долю такта (лёгкие сложности ставили ноту почти на каждую — механически)
       "b_down": {"easy": -1.5, "normal": 0.25, "hard": 0.0, "insane": 0.0, "expert": 0.0, "extra": 0.0, "extreme": 0.0},
       "w_gap": 1.0, "w_len": 0.5, "w_body": 0.3, "w_type": 1.125, "w_end": 1.0}
# длина серии 1/4 подряд у сверхсложных — длиннее, чем у людей на Expert (стримы)
CHAIN_CAP = {"extreme": 16}


def _gap_idx(g: np.ndarray) -> np.ndarray:
    """Интервал в долях → индекс в GAP_GRID (len(GAP_GRID) — «прочее/долгий»)."""
    ok = np.abs(np.asarray(g, float)[:, None] - _GG[None, :]) <= _GTOL[None, :]
    return np.where(ok.any(1), np.argmax(ok, 1), len(GAP_GRID))


class _Mapper:
    def __init__(self, an: dict, D: dict, opts: dict, seed: int, spacing: float, X: np.ndarray | None = None):
        self.an, self.D, self.o = an, D, opts
        self.dk = D.get("base", D["key"])          # чьи модели и таблицы людей (Extra/Extreme — от Expert)
        self.rng = np.random.default_rng(seed)
        self.spacing = spacing
        self.X = tick_matrix(an) if X is None else X
        T = an["ticks"]
        self.t = np.array(T["t"])
        self.m = np.array(T["m"])
        self.s = np.array(T["s"])
        self.lo, self.mi, self.hi = np.array(T["lo"]), np.array(T["mi"]), np.array(T["hi"])
        self.ld = np.array(T["ld"])
        self.d = np.array(T.get("d") or [1 if m <= 1 else 2 if m == 2 else 4 for m in T["m"]])
        self.beat_ms = 60000.0 / an["bpm"]
        self.beats_arr = np.array(an.get("beats") or [0.0, self.beat_ms], float)
        if len(self.beats_arr) < 2:
            self.beats_arr = np.array([0.0, self.beat_ms])
        self.r = radius(D["cs"])
        self.kiai = an.get("kiai", [])
        self.breaks = an.get("breaks", [])
        self.intensity = np.full(len(self.t), 0.6)
        for s in an.get("sections", []):
            mk = (self.t >= s["s"]) & (self.t < s["e"])
            self.intensity[mk] = s.get("int", 0.6)

    def in_kiai(self, t):
        return any(a <= t < b for a, b in self.kiai)

    def in_break(self, t):
        return any(a - 300 <= t < b for a, b in self.breaks)

    # ── 1. ритм ── #
    def _beat_at(self, i) -> float:
        """Длина доли (мс) в месте тика i (темп может немного плавать)."""
        b = self.beats_arr
        j = int(np.clip(np.searchsorted(b, self.t[i], "right") - 1, 0, len(b) - 2))
        return float(b[j + 1] - b[j]) if len(b) > 1 else self.beat_ms

    def _beat_len(self) -> np.ndarray:
        b = self.beats_arr
        j = np.clip(np.searchsorted(b, self.t, "right") - 1, 0, len(b) - 2)
        return np.maximum(30.0, b[j + 1] - b[j]) if len(b) > 1 else np.full(len(self.t), self.beat_ms)

    def _allowed(self) -> np.ndarray:
        t = self.t
        a0, a1 = self.an["first"] - 30, self.an["last"] + 30
        ok = (t >= a0) & (t <= a1)
        for a, b in self.breaks:
            ok &= ~((t >= a - 200) & (t < b))
        seg = self.o.get("seg")
        if seg:
            ok &= (t >= float(seg[0])) & (t <= float(seg[1]) - 250)
        return ok

    def rhythm(self) -> list[dict]:
        """Ритм всей карты разом — динамическим программированием по тикам.

        Каждое нажатие и каждый конец слайдера оцениваются моделями, выученными по картам живых
        мапперов (MD.MODELS: «здесь ставят ноту», «здесь кончается слайдер», «это слайдер»), а
        переходы между объектами — их же «грамматикой» (MD.PRIORS: какие интервалы после круга и
        после конца слайдера бывают на этой сложности, какие длины слайдеров, сколько 1/4 подряд).
        Ищется последовательность кругов и слайдеров с наибольшей суммой — то есть ритм, который
        лучше всего «объясняет» музыку так, как её объяснил бы маппер."""
        D, o = self.D, self.o
        key = D["key"]
        mod, pri = MD.MODELS[self.dk], MD.PRIORS[self.dk]
        t = self.t
        n = len(t)
        if n < 4:
            return []
        X = self.X
        def lin(w):                                # модели прежней версии — по своим первым признакам
            w = np.asarray(w, float)
            return X[:, :len(w)] @ w
        Lh = lin(mod["head"])
        Le = lin(mod["end"])
        sl_k = max(0.0, float(o.get("sliders", 1.0)))
        # «слайдер или круг»: логит модели (+ сдвиг по настройке «Слайдеры»), поровну на оба варианта
        zs = lin(mod["slider"]) + RHY["sl_bias"][key] + math.log(max(1e-3, sl_k))
        # плотность: сдвиг логитов (×2 нот ≈ +ln 2); калибровка — чтобы при 1.0 плотность была как у людей
        lam = math.log(max(0.2, float(o.get("density", 1.0)))) + RHY["lam"][key]
        allowed = self._allowed()
        fi = FEAT_NAMES.index
        Lh = Lh + RHY["b_half"][key] * X[:, fi("half")] + RHY["b_quart"][key] * (X[:, fi("quart")] + X[:, fi("trip")])             + RHY["b_down"][key] * X[:, fi("down")]
        Lh = np.where(allowed, Lh + lam, -1e9)
        Le = np.where(allowed, RHY["w_end"] * (Le + RHY["lam_e"][key]), -1e9)
        ws = RHY["w_type"]
        lc = -0.5 * ws * zs
        lsl = 0.5 * ws * zs if sl_k > 0.01 else np.full(n, -1e9)
        bm = self._beat_len()
        # грамматика: log P относительно самого частого (обычный интервал не стоит ничего, редкий — штраф)
        gc, gs = np.asarray(pri["gc"]), np.asarray(pri["gs"])
        gc, gs = gc - gc.max(), gs - gs.max()
        wg, wl, wb = RHY["w_gap"], RHY["w_len"], RHY["w_body"]
        chain_cap = int(np.clip(round(CHAIN_CAP.get(key, pri["chain_p90"]) if o.get("streams", True)
                                      else min(2, pri["chain_p90"])), 0, 16))
        C = max(1, chain_cap + 1)
        # варианты слайдеров: (доли, повторов, log P)
        opts = []
        lp_max = max(pri["len"].values())
        for k, lp in pri["len"].items():
            li, r = k.split("x")
            li, r = int(li), int(r)
            if li >= len(GAP_GRID) or lp < -5.2:
                continue
            opts.append((GAP_GRID[li], r, float(lp) - lp_max))
        ends = []                                  # для каждого варианта: индексы тиков концов (n × r), годность
        for L, r, lp in opts:
            E = np.zeros((n, r), np.int64)
            ok = np.ones(n, bool)
            for j in range(1, r + 1):
                tt = t + j * L * bm
                k = np.clip(np.searchsorted(t, tt), 1, n - 1)
                k = np.where(np.abs(t[k - 1] - tt) < np.abs(t[k] - tt), k - 1, k)
                ok &= np.abs(t[k] - tt) <= 6 + 0.03 * L * bm
                ok &= k > (E[:, j - 2] if j > 1 else np.arange(n))
                E[:, j - 1] = k
            ok &= allowed & allowed[E[:, -1]]
            ends.append((E, ok))
        relu = np.maximum(0, np.where(allowed, Lh, 0))
        cs_relu = np.concatenate([[0], np.cumsum(relu)])

        NEG = -1e18
        V = np.full((n, C, 2), NEG)
        # обратные ссылки: для V[k, c, ty] — (начало объекта, вариант) ; для inc[i, c] — откуда пришли
        bp_obj = np.full((n, C, 2, 2), -1, np.int64)
        inc_from = np.full((n, C, 3), -1, np.int64)
        pref = np.full(n, NEG)                    # max V[0..e] — для долгих пауз
        pref_arg = np.full((n, 3), -1, np.int64)
        lo_idx = np.searchsorted(t, t - 4.6 * bm)
        # пауза длиннее 4.6 доли — без штрафа: в тишине нот и так нет (иначе DP «начинал бы карту
        # заново» после долгой тишины и терял вступление)
        g_long = 0.0
        for i in range(n):
            if i > 0:
                if V[i - 1].max() > pref[i - 1]:
                    c_, ty_ = np.unravel_index(int(np.argmax(V[i - 1])), V[i - 1].shape)
                    pref[i] = V[i - 1, c_, ty_]
                    pref_arg[i] = (i - 1, c_, ty_)
                else:
                    pref[i], pref_arg[i] = pref[i - 1], pref_arg[i - 1]
            if Lh[i] < -1e8:
                continue
            inc = np.full(C, NEG)
            src = np.full((C, 3), -1, np.int64)
            inc[0] = 0.0                          # начало карты
            lo = int(lo_idx[i])
            if lo > 0 and pref[lo] > NEG / 2 and pref[lo] + g_long >= inc[0]:
                inc[0] = pref[lo] + g_long
                src[0] = pref_arg[lo]
            if lo < i:
                Vw = V[lo:i]
                g = (t[i] - t[lo:i]) / bm[i]
                gi = _gap_idx(g)
                cand = Vw + wg * np.stack([gc[gi], gs[gi]], 1)[:, None, :]
                fast = g < 0.3
                if (~fast).any():
                    sub = np.where((~fast)[:, None, None], cand, NEG)
                    a = int(np.argmax(sub))
                    e_, c_, ty_ = np.unravel_index(a, sub.shape)
                    if sub[e_, c_, ty_] > inc[0]:
                        inc[0] = sub[e_, c_, ty_]
                        src[0] = (lo + e_, c_, ty_)
                if fast.any() and C > 1:
                    sub = np.where(fast[:, None, None], cand, NEG)[:, :C - 1, :]
                    flat = sub.transpose(1, 0, 2).reshape(C - 1, -1)
                    am = np.argmax(flat, 1)
                    vals = flat[np.arange(C - 1), am]
                    for c in range(C - 1):
                        if vals[c] > inc[c + 1]:
                            e_, ty_ = divmod(int(am[c]), 2)
                            inc[c + 1] = vals[c]
                            src[c + 1] = (lo + e_, c, ty_)
            inc_from[i] = src
            # круг
            sc = Lh[i] + lc[i]
            nv = inc + sc
            better = nv > V[i, :, 0]
            V[i, better, 0] = nv[better]
            bp_obj[i, better, 0] = (i, -1)
            # слайдеры
            if lsl[i] < -1e8:
                continue
            for oi, ((L, r, lp), (E, ok)) in enumerate(zip(opts, ends)):
                if not ok[i]:
                    continue
                ke = E[i]
                k_last = int(ke[-1])
                body = cs_relu[k_last] - cs_relu[i + 1] - relu[ke[:-1]].sum()
                sc = Lh[i] + lsl[i] + Le[ke].sum() + wl * lp - wb * body
                nv = inc + sc
                better = nv > V[k_last, :, 1]
                if better.any():
                    V[k_last, better, 1] = nv[better]
                    bp_obj[k_last, better, 1] = (i, oi)
        # конец — лучшее из всех состояний
        e, c, ty = np.unravel_index(int(np.argmax(V)), V.shape)
        if V[e, c, ty] <= 0:
            return []
        objs = []
        while e >= 0:
            i, oi = bp_obj[e, c, ty]
            if i < 0:
                break
            if oi < 0:
                objs.append({"k": 0, "ti": int(i)})
            else:
                L, r, _lp = opts[oi]
                ke = ends[oi][0][i]
                objs.append({"k": 1, "ti": int(i), "te": [int(x) for x in ke], "slides": int(r)})
            e, c, ty = inc_from[i, c]
        objs.reverse()
        return objs

    def build(self) -> list[dict]:
        """Ритм → объекты карты (время, слайдеры, хитсаунды) + спиннеры в долгих протяжных паузах."""
        D, o = self.D, self.o
        key = D["key"]
        t_arr = self.t
        out = []
        for r in self.rhythm():
            a = r["ti"]
            t = float(t_arr[a])
            hs = self._hitsound(a)
            ss = 2 if self.intensity[a] < 0.35 else 3 if (self.in_kiai(t) and self.m[a] == 0) else 1
            obj = {"k": 0, "t": round(t), "hs": hs, "ss": ss, "ti": int(a)}
            if r["k"] == 1:
                obj = self._make_slider(obj, a, r["te"][-1], r["slides"])
            out.append(obj)
        if not o.get("spinners", True) or len(out) < 2:
            return out
        # спиннеры: долгая пауза в нажатиях при звучащей (тянущейся) музыке, не чаще, чем у людей
        pri = MD.PRIORS[self.dk]
        dur_min = max(0.5, (self.an["last"] - self.an["first"]) / 60000.0)
        budget = max(1, int(round(pri["spin_per_min"] * dur_min * 0.6)))
        rest = {"easy": 2.0, "normal": 1.5}.get(key, 1.0)
        cands = []
        ends_t = [x.get("t") + x.get("span", 0) * x.get("slides", 0) for x in out]
        for j in range(1, len(out) + 1):
            if j == len(out):                     # в конце песни: долгий последний звук
                a_t = ends_t[-1]
                b_t = min(float(self.an["last"]), float(t_arr[-1])) + self.beat_ms * (rest + 0.5)
            else:
                a_t = ends_t[j - 1]
                b_t = float(out[j]["t"])
            bm = self.beat_ms
            s0, s1 = a_t + bm * 1.0, min(b_t - bm * rest, a_t + bm * 1.0 + 10000)
            if (s1 - s0) / bm < 3.0:
                continue
            m = (t_arr >= s0) & (t_arr <= s1)
            if not m.any() or self.in_break(s0 + 1) or self.in_break(s1 - 1):
                continue
            if float(self.ld[m].mean()) < 0.3:
                continue
            seg = self.o.get("seg")
            if seg and not (float(seg[0]) <= s0 and s1 <= float(seg[1]) - 250):
                continue
            i0 = int(np.searchsorted(t_arr, s0))
            i1 = int(np.searchsorted(t_arr, s1, "right") - 1)
            while i0 < i1 and self.m[i0] > 1:      # спиннер начинается на доле
                i0 += 1
            if i1 <= i0 or (t_arr[i1] - t_arr[i0]) / bm < 2.0:
                continue
            cands.append((float(t_arr[i1] - t_arr[i0]) * float(self.ld[m].mean()), j, i0, i1))
        for _v, j, i0, i1 in sorted(cands, reverse=True)[:budget]:
            out.append({"k": 2, "t": round(float(t_arr[i0])), "end": round(float(t_arr[i1])), "hs": 0, "ti": int(i0)})
        out.sort(key=lambda x: x["t"])
        return out

    def _make_slider(self, obj, a, e, slides=1):
        D = self.D
        t0 = float(self.t[a])
        bm = self._beat_at(a)
        total = float(self.t[e]) - t0
        span_ms = total / slides
        sv = D["sv"] * self.sv_mult(t0)
        obj.update({"k": 1, "slides": int(slides), "span": round(span_ms, 2), "beat": round(bm, 2),
                    "len": round(sv * span_ms / bm, 1), "sv": round(sv, 2)})
        return obj

    def sv_mult(self, t) -> float:
        """Скорость слайдеров по части песни (у мапперов: тише — медленнее, припев — быстрее)."""
        if self.D["key"] in ("easy", "normal"):
            return 1.0
        i = int(np.clip(np.searchsorted(self.t, t), 0, len(self.t) - 1))
        inten = float(self.intensity[i])
        k = 0.9 + 0.2 * inten
        if self.in_kiai(t):
            k = max(k, 1.1)
        return round(k * 20) / 20

    def _hitsound(self, i) -> int:
        """Биты osu!: 2 whistle, 4 finish, 8 clap — как хитсаундят мапперы: clap — на малый барабан, прежде
        всего на 2-й и 4-й долях такта (у людей там почти половина всех clap); finish — тарелка с бочкой на
        сильной доле; whistle — яркая новая нота мелодии/голоса без ударных."""
        hs = 0
        f = FEAT_NAMES.index
        x = self.X[i]
        lo, mi, hi = self.lo[i], self.mi[i], self.hi[i]
        pc, hm, hn = x[f("pc")], x[f("hm")], x[f("hn")]
        backbeat = x[f("p4")] > 0 or x[f("p12")] > 0
        snare = pc > 0.45 and mi > 0.55 and mi >= 0.7 * lo
        if backbeat and snare:
            hs |= 8
        elif snare and mi > 1.0 and hi > 0.7 and lo < 0.45 and pc > 0.8:
            hs |= 8                                    # сбивка малым вне 2/4
        if x[f("down")] > 0 and pc > 0.85 and hi > 0.9 and lo > 0.6:
            hs |= 4
        if hs == 0 and hm > 0.6 and hm > 1.15 * pc and x[f("half")] + x[f("quart")] + x[f("trip")] == 0:
            hs |= 2
        return hs

    # ── 3. новые комбо ── #
    def combos(self, objs):
        """Новое комбо — как у мапперов: на первой ноте такта; на сложных — и с середины такта, если комбо
        уже длинное; на входе в припев, после спиннера и долгой паузы; не длиннее max_len."""
        key = self.D["key"]
        beats = np.asarray(self.an["beats"], float)
        ph = int(self.an["phase"])
        bars = beats[ph::4] if len(beats) > ph else beats[:1]
        halves = beats[(ph + 2) % 4::4] if len(beats) > 2 else beats[:0]
        half_after = {"easy": 99, "normal": 99, "hard": 6}.get(key, 4)
        max_len = {"easy": 8, "normal": 8, "hard": 10, "insane": 10, "expert": 12, "extra": 14}.get(key, 16)
        bi = hi_ = 0
        cnt = 0
        last_kiai = False
        prev_end = -1e9
        for k, o in enumerate(objs):
            nc = k == 0
            while bi < len(bars) and bars[bi] <= o["t"] + 15:
                bi += 1
                nc = nc or cnt > 0
            while hi_ < len(halves) and halves[hi_] <= o["t"] + 15:
                hi_ += 1
                nc = nc or cnt >= half_after
            kia = self.in_kiai(o["t"])
            if kia and not last_kiai:
                nc = True
                o["hs"] |= 4
            if o["k"] == 2 or (k > 0 and objs[k - 1]["k"] == 2) or o["t"] - prev_end > self.beat_ms * 6:
                nc = True
            if cnt >= max_len:
                nc = True
            o["nc"] = bool(nc)
            cnt = 1 if nc else cnt + 1
            last_kiai = kia
            prev_end = o.get("end", o["t"] + o.get("span", 0) * o.get("slides", 0))

    # ── 4. расстановка ── #
    def _space(self, g: float) -> dict:
        """Дистанция/угол/стопка у мапперов этой сложности для интервала g (доли): таблица MD.SPACING;
        для интервала, которого в таблице нет, — ближайший, с дистанцией пропорционально времени."""
        tab = MD.SPACING[self.dk]
        gi = int(_gap_idx(np.array([g]))[0])
        e = tab.get(str(gi))
        if e is None:
            best, bg = None, None
            for k, v in tab.items():
                gg = GAP_GRID[int(k)]
                if best is None or abs(math.log(gg / max(0.05, g))) < abs(math.log(bg / max(0.05, g))):
                    best, bg = v, gg
            f = float(np.clip((g / bg) ** 0.8, 0.5, 2.0))
            e = {"stack": 0.0, "p25": best["p25"] * f, "p50": best["p50"] * f, "p75": best["p75"] * f,
                 "ang": best["ang"]}
        k = float(self.D.get("dist", 1.0))
        if k != 1.0 or "stack" in self.D:              # Extra/Extreme: шире прыжки и стримы, меньше стопок
            e = dict(e, p25=e["p25"] * k, p50=e["p50"] * k, p75=e["p75"] * k,
                     stack=float(e.get("stack", 0.0)) * float(self.D.get("stack", 1.0)))
        return e

    def _emphasis(self, ti: int, nc: bool) -> float:
        """Насколько нота «сильная» (0…1): сила удара относительно соседних, ударные, сильная доля,
        припев, громкость части. Сильные — дальше (прыжок), слабые — ближе."""
        X = self.X
        f = FEAT_NAMES.index
        z = (0.55 * X[ti, f("s_rel")] + 0.35 * min(1.2, X[ti, f("pc")]) + 0.25 * X[ti, f("down")]
             + 0.1 * X[ti, f("beat")] + 0.2 * X[ti, f("kiai")] + 0.35 * (X[ti, f("sec")] - 0.5) + (0.1 if nc else 0.0)
             + 0.25 * min(1.2, X[ti, f("vo_rel")]))           # ударный слог голоса — тоже акцент
        return float(np.clip((z - 0.25) / 0.95, 0.0, 1.0))

    def place(self, objs):
        """Расстановка как у мапперов. Для каждой ноты: дистанция — из таблицы людей для этого интервала
        (сильная нота — дальше, слабая — ближе; стопки 1/4 и 1/2 — с той же частотой, что у людей),
        поворот — по «фигуре» комбо (треугольники, квадраты, зигзаги, прыжки туда-обратно, изогнутые
        стримы; углы — как в гистограммах людей), повторяющиеся такты повторяют рисунок (зеркально или
        поворотом). Плюс: поле, наложения на видимые ноты, плавный выход из слайдеров."""
        D, rng = self.D, self.rng
        key = D["key"]
        r = self.r
        box = (r + 4, r + 4, PLAY_W - r - 4, PLAY_H - r - 4)
        mx0, my0, mx1, my1 = box
        pre = preempt(D["ar"])
        pos = np.array([PLAY_W / 2 + rng.uniform(-70, 70), PLAY_H / 2 + rng.uniform(-50, 50)])
        head = float(rng.uniform(0, 2 * math.pi))
        prev_end_t = None
        prev_kind = None
        recent: list[tuple[float, np.ndarray]] = []
        motif = None
        stack_choice = {}
        sym = bool(self.o.get("symmetry", True))
        clip = D.get("sp_clip", (0.85, 1.2))
        spacing = float(np.clip(self.spacing, *clip)) * float(self.o.get("jumps", 1.0)) ** (
            0.0 if key in ("easy", "normal") else 1.0)
        # сила нот — по рангу внутри карты: распределение дистанций выходит как у людей (медиана — их медиана),
        # как бы громко ни был сведён трек
        emp = np.array([self._emphasis(x["ti"], bool(x.get("nc"))) if x["k"] != 2 else 0.0 for x in objs])
        rank = np.zeros(len(objs))
        if len(objs) > 1:
            rank[np.argsort(emp, kind="stable")] = np.linspace(0, 1, len(objs))
        # такты: для повтора рисунка
        bt = np.asarray((self.an.get("bars") or {}).get("t") or [], float)
        rep = (self.an.get("bars") or {}).get("rep") or []
        bar_log: dict[int, list] = {}
        copy_src, copy_T, copy_j = None, None, 0
        cur_bar = None
        angs = np.radians(np.array([15, 45, 75, 105, 135, 165], float))
        # лёгкие сложности: «привязка к дистанции» (distance snap), как у живых мапперов Easy/Normal —
        # расстояние строго пропорционально времени: DS × скорость слайдера × доли (DS — из их карт)
        low = key in ("easy", "normal")
        ds = None
        if low:
            tab = MD.SPACING[self.dk]
            vals = [v["p50"] / (D["sv"] * GAP_GRID[int(k_)]) for k_, v in tab.items()
                    if 0.9 <= GAP_GRID[int(k_)] <= 2.1]
            ds = float(np.clip(np.median(vals), 0.7, 1.3)) if vals else 1.0
        for k, o in enumerate(objs):
            t = o["t"]
            if o["k"] == 2:
                o["x"], o["y"] = 256, 192
                pos = np.array([256.0, 192.0])
                head = float(rng.uniform(0, 2 * math.pi))
                prev_end_t, prev_kind = o["end"], 2
                recent.append((o["end"] + 200, pos[None, :].copy()))
                motif = None
                continue
            ti = o["ti"]
            nc = bool(o.get("nc"))
            g = 8.0 if prev_end_t is None else max(0.05, (t - prev_end_t) / self._beat_at(ti))
            sp = self._space(g)
            # фигура комбо: базовый угол и правило знака (одинаковый — многоугольники/поток, чередование — зигзаг)
            if motif is None or nc:
                tab = MD.SPACING[self.dk].get(str(3)) or sp   # основа — интервал 1/2
                h = np.asarray(tab["ang"], float) + 1e-3
                b = int(rng.choice(6, p=h / h.sum()))
                motif = {"a": float(angs[b] + rng.uniform(-0.2, 0.2)), "sign": 1.0 if rng.random() < 0.5 else -1.0,
                         "alt": bool(rng.random() < (0.3 if key in ("easy", "normal") else 0.45)), "n": 0}
                stack_choice = {}
            # стопка: решение одно на комбо для каждого интервала (у людей стопки идут сериями)
            gi = int(_gap_idx(np.array([g]))[0])
            if gi not in stack_choice:
                stack_choice[gi] = bool(rng.random() < float(sp.get("stack", 0.0))) and g < 0.9
            stacked = stack_choice[gi] and prev_kind == 0
            e = float(rank[k])
            if low:
                dist = ds * D["sv"] * g if g >= 0.75 else sp["p50"]
                dist = max(dist, 2.05 * r)                 # лёгким — либо ровная стопка, либо без наложения
            else:
                dist = (sp["p25"] + (sp["p75"] - sp["p25"]) * e) * spacing
            if prev_kind == 1 and g > 0.3:
                dist = max(dist, 2.2 * r)                  # с конца слайдера — не на его хвост
            dist = float(np.clip(dist, 0, 380))
            # поворот
            if g < 0.3:                                    # стрим/бёрст: плавная дуга
                turn = motif["sign"] * float(rng.uniform(0.05, 0.4))
            else:
                if abs(g - 0.5) < 0.05 or g >= 0.9 or rng.random() < 0.5:
                    a = motif["a"]
                else:
                    h = np.asarray(sp["ang"], float) + 1e-3
                    a = float(angs[int(rng.choice(6, p=h / h.sum()))] + rng.uniform(-0.2, 0.2))
                sgn = motif["sign"] * (-1 if (motif["alt"] and motif["n"] % 2 == 1) else 1)
                turn = sgn * (a + float(rng.normal(0, 0.08)))
                motif["n"] += 1
            # такт и повтор рисунка
            bar_i = int(np.searchsorted(bt, t + 15, "right") - 1) if len(bt) else -1
            if bar_i != cur_bar:
                cur_bar = bar_i
                copy_src, copy_j = None, 0
                a_bar = rep[bar_i] if 0 <= bar_i < len(rep) else -1
                if a_bar >= 0 and a_bar in bar_log:
                    mine = [x for x in objs if bt[bar_i] - 15 <= x["t"] < (bt[bar_i + 1] if bar_i + 1 < len(bt)
                                                                            else 1e18) - 15]
                    src = bar_log[a_bar]
                    rel_m = [round((x["t"] - bt[bar_i]) / 10) for x in mine]
                    rel_s = [round(q[0] / 10) for q in src]
                    if len(src) >= 2 and len(rel_m) == len(rel_s) and all(abs(p - q) <= 2 for p, q in zip(rel_m, rel_s)):
                        copy_src = src
                        T = [np.eye(2), np.diag([-1.0, 1.0]), np.diag([1.0, -1.0]), -np.eye(2)]
                        w = [0.25, 0.35, 0.2, 0.2] if sym else [0.55, 0.15, 0.1, 0.2]
                        copy_T = T[int(rng.choice(4, p=w))]
            target = None
            if copy_src is not None and copy_j < len(copy_src):
                vec = copy_src[copy_j][1]
                if vec is not None:
                    target = pos + copy_T @ vec
            # кандидаты
            if stacked:
                p = pos.copy()
            elif prev_end_t is None:
                p = pos.copy()
            else:
                offs = np.radians(np.array([0, 15, -15, 30, -30, 50, -50, 80, -80, 120, -120, 180], float))
                # лёгким — расстояние не «гуляет»: только угол ищется (запасные масштабы — дорогие)
                scs = np.array([1.0, 0.97, 1.03, 0.85, 1.2, 0.7]) if low else np.array([1.0, 0.9, 1.1, 0.8, 0.7, 1.2])
                A = head + turn + offs[:, None]
                Dd = dist * scs[None, :]
                C = np.stack([pos[0] + Dd * np.cos(A), pos[1] + Dd * np.sin(A)], -1).reshape(-1, 2)
                cost = (np.abs(offs)[:, None] * 0.9 + np.abs(np.log(scs))[None, :] * (12.0 if low else 3.0)).reshape(-1)
                if target is not None:
                    C = np.vstack([target[None, :], C])
                    cost = np.concatenate([[-0.6], cost + 0.4])
                inb = (C[:, 0] >= mx0) & (C[:, 0] <= mx1) & (C[:, 1] >= my0) & (C[:, 1] <= my1)
                vis = [(te, pts) for te, pts in recent if te > t - pre - 100]
                recent = vis
                # предыдущая нота не считается (от неё и идём), но тело только что сыгранного слайдера —
                # считается, кроме его хвоста
                tail = 0 if (key in ("easy", "normal") or g > 0.3) else 3
                chk = vis[:-1] + ([(vis[-1][0], vis[-1][1][:len(vis[-1][1]) - tail])]
                                  if prev_kind == 1 and vis and len(vis[-1][1]) > tail else [])
                ov = _per_cand_overlap(C, chk, 2.1 * r, 0.15 if key in ("easy", "normal") else 0.07)
                edge = np.maximum(0, 24 - np.minimum.reduce([C[:, 0] - mx0, mx1 - C[:, 0], C[:, 1] - my0,
                                                              my1 - C[:, 1]])) * 0.02
                cen = np.hypot(C[:, 0] - 256, C[:, 1] - 192) * 0.0015
                tot = cost + ov + edge + cen + np.where(inb, 0, 1e6)
                j = int(np.argmin(tot))
                if tot[j] >= 1e6:                          # совсем тесно — к центру
                    ang = math.atan2(192 - pos[1], 256 - pos[0])
                    p = np.clip(pos + min(dist, 90) * np.array([math.cos(ang), math.sin(ang)]), [mx0, my0], [mx1, my1])
                else:
                    p = C[j]
            p = np.round(p)
            o["x"], o["y"] = int(p[0]), int(p[1])
            mv = p - pos
            if np.hypot(*mv) > 4:
                head = math.atan2(mv[1], mv[0])
            if bar_i >= 0:
                bar_log.setdefault(bar_i, []).append((t - bt[bar_i], mv.copy() if prev_end_t is not None else None))
            if copy_src is not None:
                copy_j += 1
            pos = p.astype(float)
            if o["k"] == 1:
                pts, path = self._slider_shape(o, pos, head, recent, box)
                o["pts"], o["ct"] = pts["pts"], pts["ct"]
                if o["slides"] % 2 == 1:
                    end = path[-1]
                    tang = path[-1] - path[max(0, len(path) - 5)]
                else:
                    end = path[0]
                    tang = path[0] - path[min(len(path) - 1, 4)]
                if np.hypot(*tang) > 1e-6:
                    head = math.atan2(tang[1], tang[0])
                pos = end.astype(float)
                prev_end_t = t + o["span"] * o["slides"]
                recent.append((prev_end_t, path[::4].copy()))
                prev_kind = 1
            else:
                recent.append((t, p[None, :].astype(float)))
                prev_end_t = t
                prev_kind = 0

    @staticmethod
    def _arc_pts(P, tau, B, L, n=9) -> np.ndarray:
        """Точки дуг длины L из P (m вариантов разом): начальное направление tau[m], поворот B[m] (рад).
        Прямая — тот же случай при B → 0 (через предел, без деления на ноль). → (m, n, 2)"""
        tau, B = np.asarray(tau, float)[:, None], np.asarray(B, float)[:, None]
        u = np.linspace(0, 1, n)[None, :]
        th = B * u
        small = np.abs(B) < 1e-4
        Bs = np.where(small, 1.0, B)
        # смещение вдоль пути: L·(sin(th)/B, (1−cos(th))/B) в осях (касательная, нормаль)
        a = np.where(small, L * u, L * np.sin(th) / Bs)
        b = np.where(small, 0.0, L * (1 - np.cos(th)) / Bs)
        ct, st = np.cos(tau), np.sin(tau)
        x = P[0] + a * ct - b * st
        y = P[1] + a * st + b * ct
        return np.stack([x, y], -1)

    def _slider_shape(self, o, start, head, recent, box):
        """Форма слайдера: прямая или дуга (как ставят мапперы), направление — по потоку (продолжает
        движение) с редкими «поперёк»; перебор ~50 вариантов разом, без наложений и в пределах поля."""
        rng = self.rng
        L = float(o["len"])
        mx0, my0, mx1, my1 = box
        key = self.D["key"]
        offs = np.radians(np.array([0, 25, -25, 50, -50, 80, -80, 110, -110, 150, -150, 180], float))
        if L < 45 or o["slides"] >= 3:
            bends = np.radians(np.array([0.0, 20.0, -20.0]))
        else:
            bmax = min(150.0, 40.0 + L * 0.35)
            bends = np.radians(np.array([0.0, 0.45, -0.45, 0.8, -0.8, 1.0, -1.0]) * bmax)
        jitter = float(rng.normal(0, 0.25))
        cands = []
        for d in offs:
            for B in bends:
                chord = head + jitter + d
                tau = chord - B / 2
                cands.append((d, B, tau))
        P = self._arc_pts(start, [c[2] for c in cands], [c[1] for c in cands], L)     # (m, 9, 2)
        inb = ((P[:, :, 0] >= mx0) & (P[:, :, 0] <= mx1) & (P[:, :, 1] >= my0) & (P[:, :, 1] <= my1)).all(1)
        prior_b = 0.0 if key in ("easy", "normal") else 0.15
        cost = np.array([abs(d) * (0.9 if key in ("easy", "normal", "hard") else 0.6) + abs(abs(B) - 0.6) * prior_b
                         + (0.25 if abs(B) < 1e-3 and L > 120 else 0.0) for d, B, _tau in cands])
        cost += rng.uniform(0, 0.35, len(cands))
        if recent:
            Q = np.concatenate([q for _t, q in recent[:-1]]) if len(recent) > 1 else np.zeros((0, 2))
            if len(Q):
                Dm = np.linalg.norm(P[:, 1:, None, :] - Q[None, None, :, :], axis=3).min(2)   # (m, 8)
                cost += np.clip(2.1 * self.r - Dm, 0, None).sum(1) * 0.05
        cost = np.where(inb, cost, 1e9)
        order = np.argsort(cost)
        for j in order[:6]:
            if cost[j] >= 1e9:
                break
            _d, B, tau = cands[j]
            Pj = P[j]
            if abs(B) < 1e-3:
                pts = {"ct": "L", "pts": [[int(round(start[0])), int(round(start[1]))],
                                          [int(round(Pj[-1, 0])), int(round(Pj[-1, 1]))]]}
            else:
                pts = {"ct": "P", "pts": [[int(round(start[0])), int(round(start[1]))],
                                          [int(round(Pj[4, 0])), int(round(Pj[4, 1]))],
                                          [int(round(Pj[-1, 0])), int(round(Pj[-1, 1]))]]}
            try:
                path = slider_path(pts["ct"], pts["pts"], L, step=4.0)
            except Exception:                              # noqa: BLE001
                continue
            if (path[:, 0].min() >= mx0 - 2 and path[:, 0].max() <= mx1 + 2 and path[:, 1].min() >= my0 - 2
                    and path[:, 1].max() <= my1 + 2):
                return pts, path
        # совсем тесно — короткая прямая к центру
        phi = math.atan2(192 - start[1], 256 - start[0])
        d = np.array([math.cos(phi), math.sin(phi)])
        best = {"ct": "L", "pts": [[int(start[0]), int(start[1])], [int(start[0] + d[0] * L), int(start[1] + d[1] * L)]]}
        return best, slider_path("L", best["pts"], L, step=4.0)


def generate(an: dict, dkey: str, opts: dict | None = None, meta: dict | None = None,
             X: np.ndarray | None = None) -> dict:
    """Карта одной сложности. Ритм — один раз; разлёт подгоняется, чтобы звёзды попали в диапазон сложности.
    X — tick_matrix(an), если уже посчитана (одна на все сложности)."""
    opts = dict(DEFAULT_OPTS, **(opts or {}))
    D = diff_info(dkey)
    seed = (int(opts.get("seed", 0)) * 7919 + DIFF_KEYS.index(D["key"]) * 104729) & 0x7FFFFFFF
    lo, hi = D["stars"]
    X = tick_matrix(an) if X is None else X
    best = None
    # Extra/Extreme: если на медленном треке прыжков не хватает до своих звёзд — ритм плотнее (до двух раз)
    boosts = 2 if "base" in D else 0
    dens = 1.0
    for _b in range(boosts + 1):
        o2 = dict(opts, density=float(opts.get("density", 1.0)) * dens)
        spacing = 1.0
        base = _Mapper(an, D, o2, seed, spacing, X).build()
        if not base:                                      # совсем тихий трек / пустой отрывок — хоть одна нота
            mp0 = _Mapper(an, D, o2, seed, spacing, X)
            al = np.nonzero(mp0._allowed())[0]
            i0 = int(al[0]) if len(al) else 0
            base = [{"k": 0, "t": round(float(mp0.t[i0])), "hs": 0, "ss": 1, "ti": i0}]
        for it in range(5 if boosts else 4):
            mp = _Mapper(an, D, o2, seed, spacing, X)
            objs = [dict(o) for o in base]
            mp.combos(objs)
            mp.place(objs)
            m = {"cs": D["cs"], "ar": D["ar"], "od": D["od"], "hp": D["hp"], "objects": objs}
            prepare(m)
            sr = star_rating(m)
            if not boosts or best is None or abs(sr["stars"] - (lo + hi) / 2) < abs(best[1]["stars"] - (lo + hi) / 2):
                best = (m, sr)
            if D["jump"] <= 0:
                break
            # разлёт подстраивается под звёзды лишь слегка (0.85…1.2): дистанции остаются как у мапперов,
            # сложность задаёт прежде всего ритм
            c0, c1 = D.get("sp_clip", (0.85, 1.2))
            if sr["stars"] > hi * 1.03 and spacing > c0 + 0.01:
                spacing = max(c0, spacing * max(0.8, (hi / sr["stars"]) ** 1.3))
            elif sr["stars"] < lo * 0.97 and spacing < c1 - 0.01:
                spacing = min(c1, spacing * min(1.25, (lo / max(0.1, sr["stars"])) ** 1.3))
            else:
                break
        if best[1]["stars"] >= lo * 0.93:
            break
        dens *= 1.3
    m, sr = best
    for o in m["objects"]:
        o.pop("_path", None)
        o.pop("ti", None)
        o.pop("ticks", None)
        o.pop("repeats", None)
    n_c = sum(1 for o in m["objects"] if o["k"] == 0)
    n_s = sum(1 for o in m["objects"] if o["k"] == 1)
    n_sp = sum(1 for o in m["objects"] if o["k"] == 2)
    m.update({"version": VERSION, "diff": D["key"], "name": D["name"], "bpm": an["bpm"], "beats": an["beats"],
              "phase": an["phase"], "kiai": an["kiai"], "breaks": _breaks_for(m["objects"], an),
              "stars": sr["stars"], "aim": sr["aim"], "speed": sr["speed"], "n_circles": n_c, "n_sliders": n_s,
              "n_spinners": n_sp, "length": an["dur"], "preview": preview_time(an), "tick_rate": 1,
              "opts": opts, "sv": D["sv"]})
    if meta:
        m.update(meta)
    m["max_combo"] = max_combo(m)
    return m


def _breaks_for(objs, an) -> list[list[float]]:
    """Перерывы osu!: промежутки без объектов дольше 5 с."""
    out = []
    for a, b in zip(objs[:-1], objs[1:]):
        e = a.get("end", a["t"] + a.get("span", 0) * a.get("slides", 0))
        if b["t"] - e >= 5000:
            out.append([e + 200, b["t"] - 900])
    return out


def max_combo(m) -> int:
    prepare(m)
    c = 0
    for o in m["objects"]:
        if o["k"] == 1:
            c += 1 + len(o["ticks"]) + len(o["repeats"]) + 1
        else:
            c += 1
    for o in m["objects"]:
        o.pop("_path", None)
    return c


# ------------------------------------------------------------------ #
#  Кэш и фоновая генерация                                            #
# ------------------------------------------------------------------ #

_lock = threading.Lock()
_analyze_slot = threading.Lock()


def _opts_sig(opts: dict) -> str:
    o = dict(DEFAULT_OPTS, **(opts or {}))
    return hashlib.md5(json.dumps(o, sort_keys=True).encode()).hexdigest()[:8]


def cache_file(path: str) -> Path:
    return CACHE / f"{_key(path)}.json"


def load_cached(path: str, opts: dict | None = None) -> dict | None:
    f = cache_file(path)
    try:
        d = json.loads(f.read_text("utf-8"))
    except Exception:                                      # noqa: BLE001
        return None
    if d.get("v") != VERSION or d.get("sig") != _opts_sig(opts):
        if d.get("v") == VERSION and d.get("an"):
            return {"an": d["an"], "maps": {}, "stale": True, "offset": d.get("offset", 0)}
        return None
    miss = [k for k in DIFF_KEYS if k not in (d.get("maps") or {})]
    if miss:                                               # набор из прежней версии без новых сложностей
        d["missing"] = miss
    return d


def save_cache(path: str, d: dict):
    CACHE.mkdir(parents=True, exist_ok=True)
    f = cache_file(path)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), "utf-8")
    os.replace(tmp, f)


def set_local_offset(path: str, ms: float):
    with _lock:
        f = cache_file(path)
        try:
            d = json.loads(f.read_text("utf-8"))
        except Exception:                                  # noqa: BLE001
            return
        d["offset"] = float(ms)
        save_cache(path, d)


# ── правки из редактора (osu_editor): хранятся отдельно от сгенерированных карт ── #
EDITED = OSU_DIR / "edited"


def edited_file(path: str, dkey: str) -> Path:
    return EDITED / f"{_key(path)}_{dkey}.json"


def finalize_map(m: dict) -> dict:
    """Карта после ручной правки: перерывы, звёзды, счётчики, макс. комбо — заново."""
    objs = m["objects"]
    objs.sort(key=lambda o: o["t"])
    for o in objs:
        if o["k"] == 1:
            o.pop("_path", None)
            o.pop("end", None)
    prepare(m)
    try:
        sr = star_rating(m) if len(objs) > 1 else {"stars": 0.0, "aim": 0.0, "speed": 0.0}
    except Exception:                                      # noqa: BLE001
        sr = {"stars": float(m.get("stars", 0)), "aim": float(m.get("aim", 0)), "speed": float(m.get("speed", 0))}
    m["breaks"] = _breaks_for(objs, None)
    m.update({"stars": sr["stars"], "aim": sr["aim"], "speed": sr["speed"],
              "n_circles": sum(1 for o in objs if o["k"] == 0), "n_sliders": sum(1 for o in objs if o["k"] == 1),
              "n_spinners": sum(1 for o in objs if o["k"] == 2)})
    m["max_combo"] = max_combo(m)
    for o in objs:
        for k in ("_path", "ticks", "repeats"):
            o.pop(k, None)
        if o["k"] == 1:
            o.pop("end", None)
    return m


def save_edited(path: str, dkey: str, m: dict):
    EDITED.mkdir(parents=True, exist_ok=True)
    data = {k: v for k, v in m.items() if not k.startswith("_")}
    f = edited_file(path, dkey)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), "utf-8")
    os.replace(tmp, f)


def load_edited(path: str, dkey: str) -> dict | None:
    try:
        m = json.loads(edited_file(path, dkey).read_text("utf-8"))
        return m if m.get("objects") else None
    except Exception:                                      # noqa: BLE001
        return None


def delete_edited(path: str, dkey: str):
    try:
        edited_file(path, dkey).unlink(missing_ok=True)
    except Exception:                                      # noqa: BLE001
        pass


def build_set(track: dict, opts: dict | None = None, progress=None, cancel: threading.Event | None = None) -> dict:
    """Анализ (из кэша, если есть) + все сложности. Возвращает {"an", "maps": {key: map}, ...}."""
    path = str(track.get("path") or "")
    with _lock:
        d = load_cached(path, opts)
    if d and not d.get("stale") and d.get("missing"):
        # готовый набор, но без новых сложностей (Extra/Extreme) — дорасставить только их, анализ — из кэша
        an, maps = d["an"], dict(d["maps"])
        meta = {"title": track.get("title") or Path(path).stem, "artist": track.get("artist") or "", "path": path}
        X = tick_matrix(an)
        miss = list(d["missing"])
        for i, key in enumerate(miss):
            if progress:
                progress(0.8 + 0.2 * i / len(miss), f"Расставляю ноты: {diff_info(key)['name']}…")
            maps[key] = generate(an, key, opts, meta, X)
        out = {"v": VERSION, "sig": _opts_sig(opts), "an": an, "maps": {k: maps[k] for k in DIFF_KEYS if k in maps},
               "offset": d.get("offset", 0.0), "ts": time.time()}
        with _lock:
            save_cache(path, out)
        return out
    if d and not d.get("stale"):
        return d
    if d:
        an = d["an"]
    else:
        # один анализ за раз: при быстрой прокрутке списка треки не слушаются параллельно
        # (каждый — это весь трек в памяти и полное ядро процессора)
        with _analyze_slot:
            if cancel is not None and cancel.is_set():
                raise RuntimeError("отменено")
            with _lock:
                d = load_cached(path, opts)               # пока ждали — мог досчитать прежний поток
            if d and not d.get("stale"):
                return d
            an = d["an"] if d else None
            want_voc = False   # отделение вокала выключено: слишком долго
            if an is not None and want_voc and "vocals" not in an:
                an = None                                  # анализ прежней версии — без голоса: слушаем заново
            if an is None:
                voc = None
                if want_voc:
                    def vp(f, s_):
                        if progress:
                            progress(0.55 * f, "Голос: " + s_)
                    voc, _how = vocal_stem(path, vp, cancel)
                an = analyze(path, progress=lambda f, s: progress and progress(0.55 + f * 0.25, s), vocals=voc)
    # анализ уже готов — карты дорасставляем и кладём в кэш, даже если трек успели пролистать
    meta = {"title": track.get("title") or Path(path).stem, "artist": track.get("artist") or "", "path": path}
    maps = {}
    X = tick_matrix(an)
    for i, D in enumerate(DIFFS):
        if progress:
            progress(0.8 + 0.2 * i / len(DIFFS), f"Расставляю ноты: {D['name']}…")
        maps[D["key"]] = generate(an, D["key"], opts, meta, X)
    out = {"v": VERSION, "sig": _opts_sig(opts), "an": an, "maps": maps, "offset": (d or {}).get("offset", 0.0),
           "ts": time.time()}
    with _lock:
        save_cache(path, out)
    return out


def build_set_job(track: dict, opts: dict | None = None, progress=None) -> bool:
    """Для bgproc: анализ и все сложности — в отдельном процессе, результат — в кэш на диске."""
    build_set(track, opts, progress)
    return True


class Builder(threading.Thread):
    """Фоновая генерация набора карт трека: on_progress(f, text), on_done(result | None, err).
    Сама работа — в отдельном процессе (bgproc): анализ и расстановка на Python больше не делят
    GIL с интерфейсом и звуком. Этот поток только ждёт ответа и читает готовый кэш."""

    NO_MAP = "__nomap__"

    def __init__(self, track, opts=None, on_progress=None, on_done=None, cache_only=False):
        super().__init__(daemon=True, name="esu-mapgen")
        self.track, self.opts = dict(track), dict(opts or {})
        self.on_progress, self.on_done = on_progress, on_done
        self.cache_only = cache_only              # только готовая карта из кэша, без генерации
        self.cancel = threading.Event()

    def run(self):
        path = str(self.track.get("path") or "")
        try:
            with _lock:
                d = load_cached(path, self.opts)
            if d and not d.get("stale") and not d.get("missing"):
                r = d
            elif self.cache_only and not (d and d.get("missing")):
                if self.on_done and not self.cancel.is_set():
                    self.on_done(None, self.NO_MAP)
                return
            else:
                try:
                    import bgproc
                    light = {k: self.track.get(k) for k in ("path", "title", "artist", "duration")
                             if self.track.get(k) is not None}
                    bgproc.call("osu_mapgen", "build_set_job", light, self.opts, progress=self.on_progress,
                                cancel=self.cancel)
                    with _lock:
                        r = load_cached(path, self.opts)
                    if r is None or r.get("stale") or r.get("missing"):
                        raise RuntimeError("карта не сохранилась")
                except Exception as e:                     # noqa: BLE001
                    if self.cancel.is_set() or "отменено" in str(e):
                        return
                    print("[esu mapgen] отдельный процесс не удался, считаю здесь:", e)
                    r = build_set(self.track, self.opts, self.on_progress, self.cancel)
            err = ""
        except Exception as e:                             # noqa: BLE001
            r, err = None, str(e) or e.__class__.__name__
        if self.on_done and not self.cancel.is_set():
            self.on_done(r, err)


# ------------------------------------------------------------------ #
#  Экспорт в настоящий osu! (.osz)                                     #
# ------------------------------------------------------------------ #

def _safe(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', " ", s or "").strip()[:80] or "track"


def to_osu_text(m: dict, audio_name: str, bg_name: str | None) -> str:
    beat = 60000.0 / m["bpm"]
    off = m["beats"][0] if m["beats"] else 0
    lines = ["osu file format v14", "", "[General]", f"AudioFilename: {audio_name}", "AudioLeadIn: 0",
             f"PreviewTime: {int(m.get('preview', 0))}", "Countdown: 0", "SampleSet: Normal", "StackLeniency: 0.7",
             "Mode: 0", "LetterboxInBreaks: 0", "WidescreenStoryboard: 0", "", "[Editor]", "DistanceSpacing: 1",
             "BeatDivisor: 4", "GridSize: 8", "", "[Metadata]", f"Title:{m.get('title', '')}",
             f"TitleUnicode:{m.get('title', '')}", f"Artist:{m.get('artist', '')}",
             f"ArtistUnicode:{m.get('artist', '')}", "Creator:ECHOES mapgen", f"Version:{m['name']}", "Source:",
             "Tags:echoes generated", "", "[Difficulty]", f"HPDrainRate:{m['hp']}", f"CircleSize:{m['cs']}",
             f"OverallDifficulty:{m['od']}", f"ApproachRate:{m['ar']}", f"SliderMultiplier:{m['sv'] / 100:.3f}",
             "SliderTickRate:1", "", "[Events]"]
    if bg_name:
        lines.append(f'0,0,"{bg_name}",0,0')
    for a, b in m.get("breaks", []):
        lines.append(f"2,{int(a)},{int(b)}")
    lines += ["", "[TimingPoints]"]
    tps = []
    beats = m["beats"]
    steady = len(beats) > 2 and np.std(np.diff(beats)) < 3
    if steady:
        tps.append((off, f"{off:.0f},{beat:.6f},4,1,0,70,1,0"))
    else:
        for i in range(0, len(beats) - 1, 4):
            tps.append((beats[i], f"{beats[i]:.0f},{beats[i + 1] - beats[i]:.6f},4,1,0,70,1,0"))
    for o in m["objects"]:
        if o["k"] == 1 and abs(o.get("sv", m["sv"]) / m["sv"] - 1) > 0.01:
            mult = o["sv"] / m["sv"]
            tps.append((o["t"] - 1, f"{o['t'] - 1},{-100 / mult:.4f},4,1,0,70,0,0"))
            tps.append((o["end"] + 1 if "end" in o else o["t"] + o["span"] * o["slides"] + 1,
                        f"{int(o['t'] + o['span'] * o['slides'] + 1)},-100,4,1,0,70,0,0"))
    for a, b in m.get("kiai", []):
        tps.append((a, f"{int(a)},-100,4,1,0,70,0,1"))
        tps.append((b, f"{int(b)},-100,4,1,0,70,0,0"))
    lines += [s for _, s in sorted(tps, key=lambda x: x[0])]
    lines += ["", "[HitObjects]"]
    for o in m["objects"]:
        typ = {0: 1, 1: 2, 2: 8}[o["k"]] | (4 if o.get("nc") else 0)
        ss = int(o.get("ss", 0))
        if o["k"] == 0:
            lines.append(f"{o['x']},{o['y']},{int(o['t'])},{typ},{o.get('hs', 0)},{ss}:0:0:0:")
        elif o["k"] == 1:
            pts = "|".join(f"{x}:{y}" for x, y in o["pts"][1:])
            lines.append(f"{o['x']},{o['y']},{int(o['t'])},{typ},{o.get('hs', 0)},{o['ct']}|{pts},{o['slides']},"
                         f"{o['len']},0|0,0:0|0:0,{ss}:0:0:0:")
        else:
            lines.append(f"256,192,{int(o['t'])},{typ},0,{int(o['end'])},0:0:0:0:")
    return "\n".join(lines) + "\n"


def export_osz(track: dict, maps: dict, out_dir: str | Path, cover: str | None = None) -> Path:
    path = str(track.get("path") or "")
    ext = Path(path).suffix.lower() or ".mp3"
    audio_name = "audio" + ext
    bg_name = ("bg" + Path(cover).suffix.lower()) if cover and Path(cover).exists() else None
    name = _safe(f"{track.get('artist', '')} - {track.get('title', '')}".strip(" -"))
    out = Path(out_dir) / f"{name} (ECHOES).osz"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(path, audio_name)
        if bg_name:
            z.write(cover, bg_name)
        for key in DIFF_KEYS:
            m = maps.get(key)
            if not m:
                continue
            z.writestr(_safe(f"{name} (ECHOES) [{m['name']}]") + ".osu", to_osu_text(m, audio_name, bg_name))
    return out
