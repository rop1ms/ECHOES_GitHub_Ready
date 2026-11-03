"""Умный 8D: звук летает вокруг головы — в наушниках кажется, что музыка кружит вокруг вас.

Сначала трек «слушается» (analyze, доли секунды): темп и сильные доли — по всплескам энергии в спектре,
громкие и тихие части — по огибающей. По этому строится траектория (path):
  • один круг — за 2 такта (8 долей) и начинается на сильную долю, поэтому полёт идёт в такт музыке;
  • в припевах и громких местах круг быстрее и шире, в тихих — медленнее и ближе;
  • слишком быстрые/медленные круги ограничены (6…16 с), чтобы не укачивало и не было скучно.
Затем render: источник — середина микса (выше ~140 Гц) — облетает голову с бинауральными признаками:
  • громкость между ушами (равная мощность) и задержка дальнего уха до 0,66 мс (как реальная голова);
  • «тень головы»: дальнему уху меньше верхов; сзади — чуть глуше и тише, чем спереди;
  • басы остаются в центре (крутящийся бас неприятен и «дышит»), немного исходной стереобазы сохраняется;
  • лёгкая комната вокруг — звук «снаружи головы», а не внутри.
Всё — numpy/scipy по всему треку разом: 3–4 минуты музыки считаются за пару секунд.
"""
from __future__ import annotations

import numpy as np

try:
    from scipy import signal as _sig
except Exception:                                   # noqa: BLE001
    _sig = None

BASS_HZ = 140.0
ITD_SEC = 0.00066
MIN_PERIOD, MAX_PERIOD = 6.0, 16.0


def _lowpass(x, sr, hz, order=2, zero_phase=True):
    if _sig is None:
        return x
    sos = _sig.butter(order, hz / (sr / 2.0), btype="low", output="sos")
    f = _sig.sosfiltfilt if zero_phase else _sig.sosfilt
    return f(sos, x, axis=0).astype(np.float32)


def analyze(mono: np.ndarray, sr: int) -> dict:
    """Темп, первая сильная доля и энергия по времени."""
    step = 4                                        # анализ на 11 кГц — хватает для долей и энергии
    x = mono[::step].astype(np.float32)
    asr = sr / step
    n_fft, hop = 1024, 256
    if len(x) < n_fft * 4:
        return {"bpm": 120.0, "beat0": 0.0, "hop": hop / asr, "energy": np.ones(1, np.float32)}
    frames = np.lib.stride_tricks.sliding_window_view(x, n_fft)[::hop]
    win = np.hanning(n_fft).astype(np.float32)
    mag = np.abs(np.fft.rfft(frames * win, axis=1)).astype(np.float32)
    logm = np.log1p(mag * 10.0)
    flux = np.maximum(0.0, np.diff(logm, axis=0)).sum(axis=1)
    flux = np.concatenate([[0.0], flux]).astype(np.float32)
    flux -= np.convolve(flux, np.ones(16) / 16, mode="same")          # убрать медленный фон
    flux = np.maximum(flux, 0.0)
    fps = asr / hop
    # темп: автокорреляция всплесков на 70…180 BPM с лёгким предпочтением 100…130
    ac = np.correlate(flux, flux, mode="full")[len(flux) - 1:]
    best, bpm = -1.0, 120.0
    for b in np.arange(70.0, 180.0, 0.5):
        lag = fps * 60.0 / b
        i = int(lag)
        if i + 1 >= len(ac):
            continue
        v = ac[i] * (1 - (lag - i)) + ac[i + 1] * (lag - i)
        v *= np.exp(-0.5 * ((b - 118.0) / 45.0) ** 2)
        if v > best:
            best, bpm = v, float(b)
    # фаза долей: где решётка долей собирает больше всего всплесков
    period = fps * 60.0 / bpm
    phases = np.arange(0, period, max(1.0, period / 32))
    idx = np.arange(len(flux))
    scores = []
    for ph in phases:
        grid = np.round(np.arange(ph, len(flux), period)).astype(int)
        grid = grid[grid < len(flux)]
        scores.append(flux[grid].sum())
    beat0 = float(phases[int(np.argmax(scores))] / fps) if scores else 0.0
    rms = np.sqrt((frames[:, ::4] ** 2).mean(axis=1))
    k = max(1, int(fps * 2.0))
    env = np.convolve(rms, np.ones(k) / k, mode="same")
    lo, hi = np.percentile(env, 10), np.percentile(env, 95)
    energy = np.clip((env - lo) / max(1e-6, hi - lo), 0.0, 1.0).astype(np.float32)
    del idx
    return {"bpm": bpm, "beat0": beat0, "hop": hop / asr, "energy": energy}


def path(info: dict, n: int, sr: int):
    """Угол (0 — спереди, π/2 — справа) и «радиус» (0…1) для каждого сэмпла."""
    bpm = info["bpm"]
    beats_per_turn = 8.0
    period = beats_per_turn * 60.0 / bpm
    while period < MIN_PERIOD:
        period *= 2.0
    while period > MAX_PERIOD:
        period /= 2.0
    e = info["energy"]
    hop = info["hop"]
    t_e = np.arange(len(e)) * hop
    speed = 0.8 + 0.45 * e                           # громче — быстрее
    omega = 2 * np.pi / period * speed
    ang_e = np.concatenate([[0.0], np.cumsum(omega[:-1] * hop)])
    t = np.arange(n, dtype=np.float64) / sr
    ang = np.interp(t, t_e, ang_e, right=ang_e[-1] + (t[-1] - t_e[-1]) * omega[-1] if len(t_e) else 0)
    start = info.get("beat0", 0.0) * 2 * np.pi / period
    ang = ang - start + np.pi / 2                    # на сильную долю — справа
    width = np.interp(t, t_e, 0.72 + 0.28 * e).astype(np.float32)
    return ang, width


def _delay(x, d):
    """Сдвиг сигнала на d (сэмплы, дробные, свои для каждого сэмпла): линейная интерполяция."""
    n = len(x)
    pos = np.arange(n, dtype=np.float32) - d.astype(np.float32)
    np.clip(pos, 0, n - 1, out=pos)
    i = pos.astype(np.int64)
    f = pos - i
    j = np.minimum(i + 1, n - 1)
    return (x[i] * (1.0 - f) + x[j] * f).astype(np.float32)


def _room(x, sr, seed):
    """Короткая мягкая комната (0,9 с) — звук снаружи головы."""
    if _sig is None:
        return np.zeros_like(x)
    n = int(sr * 0.9)
    t = np.arange(n, dtype=np.float32) / sr
    rng = np.random.default_rng(seed)
    ir = rng.standard_normal(n).astype(np.float32) * np.exp(-6.0 * t)
    ir = _lowpass(ir, sr, 5000.0, zero_phase=False)
    ir[: int(sr * 0.012)] = 0.0
    ir /= max(1e-9, float(np.sqrt((ir * ir).sum())))
    conv = getattr(_sig, "oaconvolve", _sig.fftconvolve)          # блоками — в разы быстрее на длинном треке
    return conv(x, ir, mode="full")[: len(x)].astype(np.float32)


def render(audio: np.ndarray, sr: int, info: dict | None = None) -> tuple[np.ndarray, dict]:
    """audio (n, 2) float32 → (8D-версия (n, 2), анализ)."""
    audio = audio.astype(np.float32)
    n = len(audio)
    L, R = audio[:, 0], audio[:, 1]
    mid = (L + R) * 0.5
    side = (L - R) * 0.5
    info = info or analyze(mid, sr)
    bass = _lowpass(mid, sr, BASS_HZ, 4)
    src = mid - bass                                 # что облетает голову
    ang, width = path(info, n, sr)
    pan = (np.sin(ang) * width).astype(np.float32)   # -1 слева … 1 справа
    front = np.cos(ang).astype(np.float32)           # 1 спереди … -1 сзади
    gl = np.sqrt(0.5 * (1.0 - 0.94 * pan)).astype(np.float32)
    gr = np.sqrt(0.5 * (1.0 + 0.94 * pan)).astype(np.float32)
    itd = ITD_SEC * sr
    sl = _delay(src, itd * np.maximum(0.0, pan))     # справа — левое ухо дальше и слышит позже
    sr_ = _delay(src, itd * np.maximum(0.0, -pan))
    dull_l = _lowpass(sl, sr, 2600.0, zero_phase=False)   # «тень головы» и звук сзади
    dull_r = _lowpass(sr_, sr, 2600.0, zero_phase=False)
    back = np.maximum(0.0, -front)
    shade_l = np.clip(0.6 * np.maximum(0.0, pan) + 0.3 * back, 0.0, 0.85)
    shade_r = np.clip(0.6 * np.maximum(0.0, -pan) + 0.3 * back, 0.0, 0.85)
    level = (1.0 - 0.16 * back).astype(np.float32)
    out_l = (sl * (1 - shade_l) + dull_l * shade_l) * gl * level * 1.38
    out_r = (sr_ * (1 - shade_r) + dull_r * shade_r) * gr * level * 1.38
    side_keep = side * 0.3
    wet_l, wet_r = _room(out_l, sr, 7), _room(out_r, sr, 19)   # комната — только для кружащего звука:
    out_l = out_l * 0.9 + wet_l * 0.16 + bass + side_keep     # у случайного отклика разный бас в ушах,
    out_r = out_r * 0.9 + wet_r * 0.16 + bass - side_keep     # а бас должен стоять ровно в центре
    out = np.stack([out_l, out_r], axis=1)
    # громкость как у оригинала (по RMS), без перегруза
    rms_in = float(np.sqrt((audio * audio).mean())) + 1e-9
    rms_out = float(np.sqrt((out * out).mean())) + 1e-9
    out *= rms_in / rms_out
    peak = float(np.abs(out).max()) if out.size else 0.0
    if peak > 0.98:
        out *= 0.98 / peak
    return out.astype(np.float32), info


def describe(info: dict) -> str:
    bpm = info.get("bpm", 120.0)
    period = 8 * 60.0 / bpm
    while period < MIN_PERIOD:
        period *= 2
    while period > MAX_PERIOD:
        period /= 2
    return f"{int(round(bpm))} BPM · круг за {period:.0f} с, в такт"
