# beat_analysis.py
"""
Слух ECHOES: разбор трека для мэшапов студии (daw_mashup) и других функций, которым нужен ритм.

Трек декодируется ffmpeg'ом в 22 кГц моно, затем:
  * спектр по 40 логарифмическим полосам → «поток атак» (spectral flux) — где в музыке удары,
    отдельно по басу / середине / верху (бочка, малый барабан, тарелки, голос);
  * темп и сетка: складывание огибающей атак, МНК по началам ударов (±2 мс), триоли;
  * HPSS — ударные отдельно от мелодии/голоса: атаки ударных и новые ноты мелодии, держится ли
    тональный звук, смена высоты (хрома), «высота» мелодии;
  * сильная доля такта, части песни (новизна по самоподобию тактов), припевы, перерывы, повторы тактов.

analyze(path) → словарь с темпом, долями, частями песни и признаками по тикам сетки.
"""
from __future__ import annotations

import hashlib
import math
import os
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np

VERSION = 7                       # меняется — старые результаты анализа пересчитываются

SR = 22050
HOP = 256
NFFT = 1024
FPS = SR / HOP                    # ≈ 86 кадров анализа в секунду
NBANDS = 40

def _ffmpeg() -> str | None:
    # свой ffmpeg — первым: в PATH бывает древний (2013 г.), который не понимает современные ключи
    for base in (Path(__file__).resolve().parent.parent / "ffmpeg",
                 Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "ECHOES" / "ffmpeg"):
        f = base / "ffmpeg.exe"
        if f.exists():
            return str(f)
    return shutil.which("ffmpeg")


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
# Проверено на 33 треках с размеченными вручную долями: темп совпал в 27 (ещё 4 — ровно вдвое
# быстрее), фаза сетки 1/4 — в пределах нескольких мс от начала удара. Старый способ (пик
# «складывания» + сдвиг ±50 мс) часто вставал на 1/4 доли мимо.

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
    """Темп в привычной октаве: быстрая, если слабые доли почти как сильные."""
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
        print("[beat] vocals:", e)
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
    """Признаки голоса на тиках: vo — слог здесь, va — поют, vh — голос тянется дальше."""
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
    Повторяющиеся места песни звучат одинаковым ритмом — по ним видно строение песни."""
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
    """grid_ms — готовая сетка долей (мс): тогда темп не ищется.
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
    # где атак нет, но звук идёт — слабый след (протяжные места)
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


def preview_time(an: dict) -> float:
    """Откуда играть превью (мс): первый припев не в самом начале трека, иначе ~40 % длины."""
    dur = float(an.get("dur") or 0)
    for a, _b in an.get("kiai") or []:
        if a >= min(8000.0, dur * 0.08):
            return float(a)
    return dur * 0.4


def _win_max(v: np.ndarray, k: int) -> np.ndarray:
    """max(v[i-k … i+k])"""
    out = v.copy()
    for d in range(1, k + 1):
        out[d:] = np.maximum(out[d:], v[:-d])
        out[:-d] = np.maximum(out[:-d], v[d:])
    return out
