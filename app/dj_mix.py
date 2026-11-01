# dj_mix.py
"""
Умный кроссфейд «как у DJ».

Для каждого трека в фоне (один раз, кэш на диске) считается:
  * темп (BPM) и сетка долей — по огибающей атак (onset), автокорреляцией в диапазоне 70–180 BPM;
  * «точка ухода» — где трек на самом деле заканчивается (дальше — тихий хвост / затухание);
  * «точка входа» — первая доля после тихого вступления следующего трека.
План перехода: начать сведение за N долей (16, но 5–12 с) до точки ухода, ровно на доле текущего трека, а
следующий запустить с его точки входа — доли совпадают. Если темпы сильно разные (больше 8 %) — обычный
переход, но всё равно по энергии (не на тишине в конце и не с тишины в начале).
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import numpy as np

SR = 11025
HOP = 128
WIN = 60.0                    # анализируем первую и последнюю минуту
CACHE = Path.home() / ".neon_player" / "dj_cache"
_MEM: dict = {}
_LOCK = threading.Lock()


def _key(path: str) -> str:
    try:
        st = Path(path).stat()
        sig = f"{path}|{st.st_size}|{int(st.st_mtime)}|v2"
    except OSError:
        sig = path
    return hashlib.sha1(sig.encode("utf-8", "ignore")).hexdigest()[:20]


def _onset(x: np.ndarray) -> np.ndarray:
    n = (x.size - 512) // HOP
    if n < 8:
        return np.zeros(1)
    idx = np.arange(512)[None, :] + HOP * np.arange(n)[:, None]
    fr = x[idx] * np.hanning(512)[None, :]
    sp = np.abs(np.fft.rfft(fr, axis=1))
    lg = np.log1p(sp * 10)
    flux = np.maximum(0.0, np.diff(lg, axis=0)).sum(axis=1)
    flux = np.concatenate([[0.0], flux])
    flux -= np.convolve(flux, np.ones(16) / 16, mode="same")
    return np.maximum(0.0, flux)


def _tempo(env: np.ndarray):
    """(BPM, фаза первой доли в секундах) или (0, 0). Пик автокорреляции уточняется параболой — точнее кадра."""
    fps = SR / HOP
    if env.size < fps * 8:
        return 0.0, 0.0
    e = env - env.mean()
    n = 1 << int(np.ceil(np.log2(e.size * 2)))
    f = np.fft.rfft(e, n)
    ac = np.fft.irfft(f * np.conj(f), n)[: e.size]
    lo, hi = int(fps * 60 / 180), int(fps * 60 / 70)
    if hi + 1 >= ac.size or ac[0] <= 0:
        return 0.0, 0.0
    lag = lo + int(np.argmax(ac[lo:hi + 1]))
    if ac[lag] / ac[0] < 0.05:
        return 0.0, 0.0
    y0, y1, y2 = ac[lag - 1], ac[lag], ac[lag + 1]
    den = y0 - 2 * y1 + y2
    lag_f = lag + (0.5 * (y0 - y2) / den if den else 0.0)
    bpm = 60.0 * fps / lag_f
    # фаза: сетка с дробным периодом, сдвиг с наибольшей суммой атак
    best, ph = -1.0, 0.0
    k = np.arange(int((env.size - 1) / lag_f))
    for o in np.arange(0, lag_f, 0.5):
        idx = np.round(o + k * lag_f).astype(int)
        idx = idx[idx < env.size]
        sc = float(env[idx].sum())
        if sc > best:
            best, ph = sc, o
    return round(bpm, 2), ph / fps


def _rms(x: np.ndarray, step: float = 0.25) -> np.ndarray:
    k = int(SR * step)
    n = x.size // k
    if n < 1:
        return np.zeros(1)
    return np.sqrt(np.mean(np.square(x[: n * k].reshape(n, k)), axis=1))


def analyze(path: str, duration: float = 0.0) -> dict | None:
    """Анализ трека (кэш). duration — длина в секундах, если известна."""
    k = _key(path)
    with _LOCK:
        hit = _MEM.get(k)
    if hit is not None:
        return hit
    f = CACHE / f"{k}.json"
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        with _LOCK:
            _MEM[k] = d
        return d
    except (OSError, ValueError):
        pass
    try:
        from daw_dsp import decode
        head = decode(path, sr=SR, channels=1, dur=WIN, timeout=60)[:, 0]
        if not duration:
            try:
                import mutagen
                duration = float(mutagen.File(path).info.length)
            except Exception:                                  # noqa: BLE001
                duration = 0.0
        start_tail = max(0.0, duration - WIN) if duration else 0.0
        tail = decode(path, sr=SR, channels=1, start=start_tail, timeout=60)[:, 0] if duration > WIN else head
    except Exception as e:                                     # noqa: BLE001
        print("[dj] analyze:", e)
        return None
    if not duration:
        duration = start_tail + tail.size / SR
    bpm_h, ph_h = _tempo(_onset(head))
    bpm_t, ph_t = _tempo(_onset(tail))
    rh, rt = _rms(head), _rms(tail)
    peak = max(float(rh.max()), float(rt.max()), 1e-6)
    thr = peak * 0.22
    # вход: первый «громкий» отрезок
    loud_in = np.nonzero(rh > thr)[0]
    t_in = float(loud_in[0]) * 0.25 if loud_in.size else 0.0
    if bpm_h:
        beat = 60.0 / bpm_h
        n = max(0, int(np.ceil((t_in - ph_h) / beat - 1e-6)))
        t_in = ph_h + n * beat                                 # ближайшая доля не раньше начала звука
    # уход: последний громкий отрезок хвоста
    loud_out = np.nonzero(rt > thr)[0]
    t_out = start_tail + (float(loud_out[-1]) + 1) * 0.25 if loud_out.size else duration
    d = {"bpm_in": float(bpm_h), "phase_in": round(float(ph_h), 4), "bpm_out": float(bpm_t),
         "phase_out": round(float(start_tail + ph_t), 4), "in": round(float(t_in), 3),
         "out": round(float(min(duration, t_out)), 3), "dur": round(float(duration), 3)}
    try:
        CACHE.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(d), encoding="utf-8")
    except OSError:
        pass
    with _LOCK:
        _MEM[k] = d
    return d


def analyze_async(path: str, duration: float = 0.0, done=None):
    def work():
        r = analyze(path, duration)
        if done is not None:
            done(path, r)
    threading.Thread(target=work, name="echoes-dj", daemon=True).start()


def plan(cur: dict, nxt: dict, fallback_s: float = 6.0) -> dict | None:
    """Когда начинать переход (секунда текущего), с какой секунды пустить следующий, сколько сводить."""
    if not cur or not nxt:
        return None
    bo, bi = cur.get("bpm_out") or 0.0, nxt.get("bpm_in") or 0.0
    matched = bool(bo and bi and abs(bo - bi) / max(bo, bi) <= 0.08)
    if matched:
        beat = 60.0 / bo
        length = beat * 16
        while length > 12.0:
            length /= 2
        while length < 5.0:
            length *= 2
    else:
        length = max(3.0, min(12.0, fallback_s))
    start = cur["out"] - length
    if bo:                                                     # начало — ровно на доле текущего
        beat = 60.0 / bo
        n = round((start - cur["phase_out"]) / beat)
        start = cur["phase_out"] + n * beat
    start = max(cur["dur"] * 0.5, min(start, cur["dur"] - 1.0))
    return {"start": round(start, 3), "next_at": nxt["in"], "length": round(length, 3), "beatmatch": matched,
            "bpm": bo}
