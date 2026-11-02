# daw_dsp.py
"""
Обработка звука студии (тема «Echoes Studio»): чтение и запись файлов, STFT, растяжение во
времени и сдвиг тона (фазовый вокодер с фиксацией фаз вокруг пиков и сохранением формант),
фильтры, громкость, тональность, высота тона.

Всё на numpy/scipy, без внешних библиотек. Частота студии — 44.1 кГц (как у модели
разделения на вокал и бит).
"""
from __future__ import annotations

import math
import os
import shutil
import subprocess
import wave
from fractions import Fraction

import numpy as np
from scipy import signal as sps

SR = 44100
_NOWIN = 0x08000000 if os.name == "nt" else 0          # CREATE_NO_WINDOW
_LOWPRI = 0x00004000 if os.name == "nt" else 0         # BELOW_NORMAL_PRIORITY_CLASS


# ------------------------------------------------------------------ #
#  Файлы                                                              #
# ------------------------------------------------------------------ #

def ffmpeg_exe() -> str | None:
    try:
        from beat_analysis import _ffmpeg
        p = _ffmpeg()
        if p:
            return p
    except Exception:                                    # noqa: BLE001
        pass
    return shutil.which("ffmpeg")


def decode(path, sr: int = SR, channels: int = 2, start: float = 0.0, dur: float | None = None,
           timeout: float = 300) -> np.ndarray:
    """Файл → float32 (n, channels). Любой формат, который читает ffmpeg."""
    ff = ffmpeg_exe()
    if not ff:
        raise RuntimeError("нет ffmpeg — нечем прочитать файл")
    cmd = [ff, "-v", "error", "-nostdin"]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(path)]
    if dur:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-vn", "-ac", str(channels), "-ar", str(sr), "-f", "f32le", "-"]
    r = subprocess.run(cmd, capture_output=True, creationflags=_NOWIN | _LOWPRI, timeout=timeout)
    x = np.frombuffer(r.stdout, np.float32)
    if x.size < channels * 64:
        err = (r.stderr or b"").decode("utf-8", "ignore").strip().splitlines()
        raise RuntimeError("файл не прочитан" + (f": {err[-1]}" if err else ""))
    n = x.size // channels
    return x[: n * channels].reshape(n, channels).copy()


def write_wav(path, x: np.ndarray, sr: int = SR):
    """float (n, ch) → 16-битный WAV."""
    x = np.asarray(x, np.float32)
    if x.ndim == 1:
        x = x[:, None]
    data = (np.clip(x, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(x.shape[1])
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(data.tobytes())


def encode(x: np.ndarray, path, sr: int = SR, fmt: str | None = None, timeout: float = 600):
    """float (n, 2) → файл: wav сам, mp3 / flac / ogg — через ffmpeg (из памяти, без временных файлов)."""
    fmt = (fmt or os.path.splitext(str(path))[1].lstrip(".") or "wav").lower()
    if fmt == "wav":
        write_wav(path, x, sr)
        return
    ff = ffmpeg_exe()
    if not ff:
        raise RuntimeError("нет ffmpeg — сохраню только WAV")
    x = np.ascontiguousarray(np.clip(np.asarray(x, np.float32), -1.0, 1.0))
    ch = 1 if x.ndim == 1 else x.shape[1]
    codec = {"mp3": ["-c:a", "libmp3lame", "-b:a", "320k"], "flac": ["-c:a", "flac", "-sample_fmt", "s16"],
             "ogg": ["-c:a", "libvorbis", "-q:a", "7"], "m4a": ["-c:a", "aac", "-b:a", "256k"]}.get(fmt)
    if codec is None:
        raise RuntimeError(f"формат {fmt} не поддерживается")
    cmd = [ff, "-v", "error", "-y", "-f", "f32le", "-ar", str(sr), "-ac", str(ch), "-i", "-", *codec, str(path)]
    r = subprocess.run(cmd, input=x.tobytes(), capture_output=True, creationflags=_NOWIN, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg: " + (r.stderr or b"").decode("utf-8", "ignore").strip()[-200:])


def to_i16(x: np.ndarray) -> np.ndarray:
    return (np.clip(x, -1.0, 1.0) * 32767.0).astype(np.int16)


I16 = 1.0 / 32767.0


def as_float(x: np.ndarray) -> np.ndarray:
    if x.dtype == np.int16:
        return x.astype(np.float32) * I16
    return x.astype(np.float32, copy=False)


def stereo(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, np.float32)
    if x.ndim == 1:
        return np.stack([x, x], 1)
    if x.shape[1] == 1:
        return np.repeat(x, 2, 1)
    return x[:, :2]


# ------------------------------------------------------------------ #
#  Громкость                                                          #
# ------------------------------------------------------------------ #

def db(v: float) -> float:
    return 20.0 * math.log10(max(1e-9, float(v)))


def undb(d: float) -> float:
    return 10.0 ** (float(d) / 20.0)


def rms_db(x: np.ndarray, gate_db: float = -50.0) -> float:
    """Громкость звучащих мест (тишина не считается), дБ."""
    x = np.asarray(x, np.float32)
    if x.ndim == 2:
        x = x.mean(1)
    if x.size < 512:
        return -90.0
    hop = 2048
    n = x.size // hop
    if n < 1:
        return db(float(np.sqrt(np.mean(x * x))))
    e = np.sqrt(np.mean(x[: n * hop].reshape(n, hop) ** 2, axis=1) + 1e-12)
    d = 20 * np.log10(e + 1e-9)
    act = d > max(gate_db, float(d.max()) - 40)
    if not act.any():
        return float(d.max())
    return float(10 * np.log10(np.mean(e[act] ** 2) + 1e-12))


# ------------------------------------------------------------------ #
#  Фильтры (RBJ cookbook)                                              #
# ------------------------------------------------------------------ #

def biquad(kind: str, f: float, q: float = 0.707, gain_db: float = 0.0, sr: int = SR) -> np.ndarray:
    """Одна секция sos (1×6)."""
    f = min(max(10.0, float(f)), sr * 0.49)
    q = max(0.05, float(q))
    w0 = 2 * math.pi * f / sr
    cw, sw = math.cos(w0), math.sin(w0)
    alpha = sw / (2 * q)
    A = 10 ** (gain_db / 40.0)
    if kind == "lp":
        b = [(1 - cw) / 2, 1 - cw, (1 - cw) / 2]
        a = [1 + alpha, -2 * cw, 1 - alpha]
    elif kind == "hp":
        b = [(1 + cw) / 2, -(1 + cw), (1 + cw) / 2]
        a = [1 + alpha, -2 * cw, 1 - alpha]
    elif kind == "bp":
        b = [alpha, 0.0, -alpha]
        a = [1 + alpha, -2 * cw, 1 - alpha]
    elif kind == "notch":
        b = [1.0, -2 * cw, 1.0]
        a = [1 + alpha, -2 * cw, 1 - alpha]
    elif kind == "peak":
        b = [1 + alpha * A, -2 * cw, 1 - alpha * A]
        a = [1 + alpha / A, -2 * cw, 1 - alpha / A]
    elif kind == "lowshelf":
        sa = 2 * math.sqrt(A) * alpha
        b = [A * ((A + 1) - (A - 1) * cw + sa), 2 * A * ((A - 1) - (A + 1) * cw), A * ((A + 1) - (A - 1) * cw - sa)]
        a = [(A + 1) + (A - 1) * cw + sa, -2 * ((A - 1) + (A + 1) * cw), (A + 1) + (A - 1) * cw - sa]
    elif kind == "highshelf":
        sa = 2 * math.sqrt(A) * alpha
        b = [A * ((A + 1) + (A - 1) * cw + sa), -2 * A * ((A - 1) + (A + 1) * cw), A * ((A + 1) + (A - 1) * cw - sa)]
        a = [(A + 1) - (A - 1) * cw + sa, 2 * ((A - 1) - (A + 1) * cw), (A + 1) - (A - 1) * cw - sa]
    elif kind == "ap":
        b = [1 - alpha, -2 * cw, 1 + alpha]
        a = [1 + alpha, -2 * cw, 1 - alpha]
    else:
        return np.array([[1.0, 0, 0, 1.0, 0, 0]])
    b = np.array(b) / a[0]
    a = np.array(a) / a[0]
    return np.array([[b[0], b[1], b[2], 1.0, a[1], a[2]]])


def cut(kind: str, f: float, slope: int = 24, sr: int = SR) -> np.ndarray:
    """Срез низа/верха Баттерворта 12/24/48 дБ на октаву."""
    order = max(1, slope // 12)
    if order == 1:
        return biquad(kind, f, 0.7071, sr=sr)
    qs = {2: (0.5412, 1.3066), 4: (0.5098, 0.6013, 0.9000, 2.5629)}[2 if order < 4 else 4]
    return np.vstack([biquad(kind, f, q, sr=sr) for q in qs])


# ------------------------------------------------------------------ #
#  STFT                                                               #
# ------------------------------------------------------------------ #

def hann(n: int) -> np.ndarray:
    """Периодическое окно Ханна (как torch.hann_window)."""
    return (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(n) / n)).astype(np.float32)


def _frames_at(x: np.ndarray, starts: np.ndarray, n: int) -> np.ndarray:
    """Кадры длины n с началами starts (за краями — нули). x: (len,) → (k, n)."""
    L = x.shape[0]
    idx = starts[:, None] + np.arange(n)[None, :]
    ok = (idx >= 0) & (idx < L)
    out = np.zeros(idx.shape, np.float32)
    out[ok] = x[idx[ok]]
    return out


def envelope(mag: np.ndarray, n_fft: int, lifter: int = 40) -> np.ndarray:
    """Спектральная огибающая (кепстральное сглаживание) — форманты. mag: (..., bins)."""
    lg = np.log(np.maximum(mag, 1e-7))
    c = np.fft.irfft(lg, n_fft, axis=-1)
    c[..., lifter:n_fft - lifter + 1] = 0.0                  # прямоугольный лифтер: только медленные изменения
    env = np.fft.rfft(c, n_fft, axis=-1).real
    return np.exp(env).astype(np.float32)


def _interp_bins(a: np.ndarray, pos: np.ndarray) -> np.ndarray:
    """Линейная интерполяция по оси бинов: a (k, B), pos (B,) или (k, B) дробные индексы."""
    B = a.shape[-1]
    p = np.clip(pos, 0, B - 1.000001)
    i0 = np.floor(p).astype(np.int64)
    fr = (p - i0).astype(np.float32)
    if i0.ndim == 1:
        return a[..., i0] * (1 - fr) + a[..., i0 + 1] * fr
    return np.take_along_axis(a, i0, -1) * (1 - fr) + np.take_along_axis(a, i0 + 1, -1) * fr


# ------------------------------------------------------------------ #
#  Растяжение во времени и сдвиг тона (офлайн)                         #
# ------------------------------------------------------------------ #

def resample(x: np.ndarray, ratio: float) -> np.ndarray:
    """Изменить длину в ratio раз (новая длина ≈ len·ratio), полифазный фильтр."""
    if abs(ratio - 1.0) < 1e-6:
        return x.astype(np.float32, copy=False)
    fr = Fraction(ratio).limit_denominator(400)
    up, down = fr.numerator, fr.denominator
    if up > 2000 or down > 2000:
        fr = Fraction(ratio).limit_denominator(60)
        up, down = fr.numerator, fr.denominator
    return sps.resample_poly(x, up, down, axis=0).astype(np.float32)


def warp(x: np.ndarray, out_len: int, in_pos=None, semis: float = 0.0, formant: bool = True,
         n_fft: int = 2048, hop: int = 512, progress=None, cancel=None) -> np.ndarray:
    """Фазовый вокодер с переменным темпом.

    x — (n, ch) float32; out_len — длина результата в сэмплах;
    in_pos(t) — для массива позиций результата (сэмплы, float) вернуть позиции исходника;
      None — равномерно: весь x на всю длину результата;
    semis — сдвиг тона в полутонах (через растяжение + передискретизацию);
    formant — сохранять тембр голоса при сдвиге тона.
    Фазы: мгновенная частота по паре кадров на расстоянии hop, у пиков — своя фаза, остальные
    бины привязаны к ближайшему пику (identity phase locking); стерео — общий сдвиг фаз по сумме
    каналов, разница фаз между каналами сохраняется."""
    x = np.asarray(x, np.float32)
    if x.ndim == 1:
        x = x[:, None]
    n_in, C = x.shape
    out_len = int(out_len)
    if out_len <= 0 or n_in == 0:
        return np.zeros((max(0, out_len), C), np.float32)
    p = 2.0 ** (semis / 12.0)
    if in_pos is None:
        k = n_in / float(out_len)
        in_pos = lambda t: t * k                                    # noqa: E731
        if abs(semis) < 1e-4 and abs(k - 1.0) < 1e-4:
            out = np.zeros((out_len, C), np.float32)
            m = min(out_len, n_in)
            out[:m] = x[:m]
            return out
    mid_len = int(round(out_len * p))                               # промежуточная длина до передискретизации
    win = hann(n_fft)
    bins = n_fft // 2 + 1
    omega = (2 * np.pi * np.arange(bins) / n_fft).astype(np.float64)
    n_frames = mid_len // hop + 2
    out_mid = np.zeros((mid_len + n_fft + hop, C), np.float32)
    acc = None                                                      # накопленная фаза синтеза (по бинам)
    lifter = max(12, int(n_fft * 0.012))
    chunk = 256
    half = n_fft // 2
    xs = [x[:, c] for c in range(C)]
    mono = x.mean(1) if C > 1 else x[:, 0]
    for f0 in range(0, n_frames, chunk):
        if cancel is not None and cancel.is_set():
            raise RuntimeError("отменено")
        fi = np.arange(f0, min(n_frames, f0 + chunk))
        t_mid = fi * hop                                            # центр кадра в промежуточном времени
        t_out = t_mid / p
        a = np.asarray(in_pos(t_out.astype(np.float64)), np.float64)
        a = np.round(a).astype(np.int64)
        st_cur = a - half
        st_prv = st_cur - hop
        Mc = np.fft.rfft(_frames_at(mono, st_cur, n_fft) * win, axis=1)
        Mp = np.fft.rfft(_frames_at(mono, st_prv, n_fft) * win, axis=1)
        ph_c = np.angle(Mc)
        dphi = ph_c - np.angle(Mp) - omega[None, :] * hop
        dphi = (dphi + np.pi) % (2 * np.pi) - np.pi
        inst = omega[None, :] + dphi / hop                          # рад/сэмпл
        steps = inst * hop
        if acc is None:
            steps[0] = ph_c[0]
            cum = np.cumsum(steps, axis=0)
        else:
            cum = acc[None, :] + np.cumsum(steps, axis=0)
        acc = cum[-1] % (2 * np.pi)
        # фиксация фаз вокруг пиков
        mag = np.abs(Mc)
        pk = (mag > np.roll(mag, 1, 1)) & (mag >= np.roll(mag, -1, 1)) & \
             (mag > np.roll(mag, 2, 1)) & (mag >= np.roll(mag, -2, 1))
        pk[:, 0] = pk[:, -1] = False
        idx = np.broadcast_to(np.arange(bins)[None, :], mag.shape)
        last = np.where(pk, idx, -1)
        np.maximum.accumulate(last, axis=1, out=last)
        nxt = np.where(pk, idx, bins * 4)
        nxt = np.minimum.accumulate(nxt[:, ::-1], axis=1)[:, ::-1]
        use_last = (last >= 0) & ((nxt >= bins) | ((idx - last) <= (nxt - idx)))
        peak = np.where(use_last, last, np.where(nxt < bins, nxt, idx))
        syn = np.take_along_axis(cum, peak, 1) + ph_c - np.take_along_axis(ph_c, peak, 1)
        rot = np.exp(1j * (syn - ph_c)).astype(np.complex64)
        corr = None
        if formant and abs(semis) > 0.05:
            env = envelope(mag, n_fft, lifter)
            corr = _interp_bins(env, np.arange(bins) * p) / np.maximum(env, 1e-7)
            corr = np.clip(corr, 0.05, 20.0).astype(np.float32)
        for c in range(C):
            X = np.fft.rfft(_frames_at(xs[c], st_cur, n_fft) * win, axis=1) * rot
            if corr is not None:
                X *= corr
            fr = np.fft.irfft(X, n_fft, axis=1).astype(np.float32) * win
            for j, kk in enumerate(fi):
                s = kk * hop
                out_mid[s:s + n_fft, c] += fr[j]
        if progress is not None:
            progress(min(1.0, (f0 + chunk) / n_frames))
    norm = float((win * win).reshape(n_fft // hop, hop).sum(0).mean()) if n_fft % hop == 0 else 1.5
    y = out_mid[half:half + mid_len] / max(1e-6, norm)
    if abs(p - 1.0) > 1e-6:
        y = resample(y, out_len / max(1, mid_len))
    out = np.zeros((out_len, C), np.float32)
    m = min(out_len, len(y))
    out[:m] = y[:m]
    return out


def stretch(x: np.ndarray, ratio: float, semis: float = 0.0, formant: bool = True, **kw) -> np.ndarray:
    """Растянуть в ratio раз (2 — вдвое длиннее) и сдвинуть тон."""
    return warp(x, int(round(len(x) * ratio)), None, semis, formant, **kw)


def beat_map(src_beats, dst_beats):
    """Отображение времени результата → время исходника по парам долей (сек → сек), кусочно-линейное
    с продолжением крайним темпом. Возвращает функцию для warp (в сэмплах)."""
    s = np.asarray(src_beats, np.float64)
    d = np.asarray(dst_beats, np.float64)
    if len(s) < 2:
        return lambda t: t                                          # noqa: E731
    k0 = (s[1] - s[0]) / max(1e-9, d[1] - d[0])
    k1 = (s[-1] - s[-2]) / max(1e-9, d[-1] - d[-2])

    def f(t_samples):
        t = np.asarray(t_samples, np.float64) / SR
        r = np.interp(t, d, s)
        lo = t < d[0]
        hi = t > d[-1]
        r[lo] = s[0] + (t[lo] - d[0]) * k0
        r[hi] = s[-1] + (t[hi] - d[-1]) * k1
        return r * SR
    return f


# ------------------------------------------------------------------ #
#  Тональность                                                        #
# ------------------------------------------------------------------ #

KEYS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
# профили Темперли — лучше для электроники; берём среднее с Крумхансл
_MAJOR2 = np.array([0.748, 0.060, 0.488, 0.082, 0.670, 0.460, 0.096, 0.715, 0.104, 0.366, 0.057, 0.400])
_MINOR2 = np.array([0.712, 0.084, 0.474, 0.618, 0.049, 0.460, 0.105, 0.747, 0.404, 0.067, 0.133, 0.330])
# Camelot: номер для мажора / минора по тонике
_CAM_MAJ = {0: 8, 7: 9, 2: 10, 9: 11, 4: 12, 11: 1, 6: 2, 1: 3, 8: 4, 3: 5, 10: 6, 5: 7}
_CAM_MIN = {9: 8, 4: 9, 11: 10, 6: 11, 1: 12, 8: 1, 3: 2, 10: 3, 5: 4, 0: 5, 7: 6, 2: 7}


def chroma(x: np.ndarray, sr: int = 22050, n_fft: int = 8192, hop: int = 2048, tune: float = 0.0) -> np.ndarray:
    """Средняя хрома (12) — где энергия по нотам. Только тональная часть: 55 Гц…2 кГц, лог-сжатие.
    tune — строй трека в долях полутона (сетка нот сдвигается, иначе энергия делится между соседними)."""
    x = np.asarray(x, np.float32)
    if x.ndim == 2:
        x = x.mean(1)
    n = 1 + max(0, (len(x) - n_fft)) // hop
    if n < 4:
        return np.ones(12) / 12
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    sel = (freqs >= 55) & (freqs <= 2000)
    pcs = (np.round(12 * np.log2(freqs[sel] / 440.0) - tune) + 9).astype(int) % 12
    cents = 1200 * np.log2(freqs[sel] / 440.0) - 100 * tune
    w = np.cos(np.clip(np.abs(((cents + 50) % 100) - 50) / 50.0, 0, 1) * np.pi / 2) ** 2
    win = np.hanning(n_fft).astype(np.float32)
    acc = np.zeros(12)
    for s in range(0, n, 128):
        k = np.arange(s, min(n, s + 128))
        fr = _frames_at(x, k * hop, n_fft) * win
        S = np.abs(np.fft.rfft(fr, axis=1))[:, sel]
        S = np.log1p(S * 50.0)
        S -= np.median(S, axis=1, keepdims=True)                   # шум и ударные (ровный спектр) — вниз
        S = np.maximum(S, 0) * w
        frame = np.zeros((len(k), 12))
        for pc in range(12):
            frame[:, pc] = S[:, pcs == pc].sum(1)
        tot = frame.sum(1, keepdims=True) + 1e-9
        acc += (frame / tot * np.sqrt(tot)).sum(0)
    return acc / (acc.sum() + 1e-9)


def chroma_frames(x: np.ndarray, sr: int = 22050, n_fft: int = 4096, hop: int = 2048,
                  tune: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """Хрома по кадрам: (кадры × 12, нормированы) и центры кадров в секундах."""
    x = np.asarray(x, np.float32)
    if x.ndim == 2:
        x = x.mean(1)
    n = 1 + max(0, (len(x) - n_fft)) // hop
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    sel = (freqs >= 60) & (freqs <= 2200)
    pcs = (np.round(12 * np.log2(freqs[sel] / 440.0) - tune) + 9).astype(int) % 12
    M = np.zeros((int(sel.sum()), 12), np.float32)
    M[np.arange(len(pcs)), pcs] = 1.0
    win = np.hanning(n_fft).astype(np.float32)
    out = np.zeros((n, 12), np.float32)
    for s in range(0, n, 256):
        k = np.arange(s, min(n, s + 256))
        S = np.log1p(np.abs(np.fft.rfft(_frames_at(x, k * hop, n_fft) * win, axis=1))[:, sel] * 30.0)
        S = np.maximum(S - np.median(S, axis=1, keepdims=True), 0.0)       # ровный фон (шум, удары) — прочь
        out[s:s + len(k)] = S @ M
    out /= np.linalg.norm(out, axis=1, keepdims=True) + 1e-9
    return out, (np.arange(n) * hop + n_fft / 2) / sr


def detect_key(ch: np.ndarray) -> dict:
    """Хрома → {tonic 0..11, mode 'major'/'minor', name, camelot, conf}."""
    ch = np.asarray(ch, np.float64)
    best = []
    pmaj = _MAJOR / _MAJOR.sum() + _MAJOR2 / _MAJOR2.sum()
    pmin = _MINOR / _MINOR.sum() + _MINOR2 / _MINOR2.sum()
    for k in range(12):
        for mode, prof in (("major", pmaj), ("minor", pmin)):
            r = float(np.corrcoef(ch, np.roll(prof, k))[0, 1])
            best.append((r, k, mode))
    best.sort(reverse=True)
    r, k, mode = best[0]
    second = best[1][0]
    return {"tonic": k, "mode": mode, "name": key_name(k, mode), "camelot": camelot(k, mode),
            "conf": round(max(0.0, r), 3), "margin": round(r - second, 3)}


def key_name(tonic: int, mode: str) -> str:
    return KEYS[tonic % 12] + ("m" if mode == "minor" else "")


def camelot(tonic: int, mode: str) -> str:
    return f"{_CAM_MIN[tonic % 12]}A" if mode == "minor" else f"{_CAM_MAJ[tonic % 12]}B"


def camelot_distance(k1: dict, k2: dict) -> float:
    """0 — та же тональность, 0.15 — параллельная (A↔B того же номера), 0.4 — соседняя по кругу,
    дальше — хуже."""
    c1, c2 = k1["camelot"], k2["camelot"]
    n1, l1 = int(c1[:-1]), c1[-1]
    n2, l2 = int(c2[:-1]), c2[-1]
    d = min((n1 - n2) % 12, (n2 - n1) % 12)
    if d == 0:
        return 0.0 if l1 == l2 else 0.15
    if d == 1:
        return 0.4 if l1 == l2 else 0.75
    return 0.8 + 0.25 * d + (0.1 if l1 != l2 else 0.0)


def shift_key(k: dict, semis: int) -> dict:
    t = (k["tonic"] + semis) % 12
    return {**k, "tonic": t, "name": key_name(t, k["mode"]), "camelot": camelot(t, k["mode"])}


# ------------------------------------------------------------------ #
#  Высота тона (YIN)                                                  #
# ------------------------------------------------------------------ #

def yin(frame: np.ndarray, sr: int = SR, fmin: float = 70.0, fmax: float = 1000.0, thr: float = 0.15):
    """Основной тон кадра: (Гц, уверенность 0..1) или (0, 0)."""
    x = np.asarray(frame, np.float64)
    x = x - x.mean()
    n = len(x)
    W = n // 2
    tmax = min(W - 1, int(sr / fmin))
    tmin = max(2, int(sr / fmax))
    if tmax <= tmin + 2 or np.dot(x, x) < 1e-7:
        return 0.0, 0.0
    m = 1
    while m < 2 * n:
        m *= 2
    a = np.zeros(m)
    a[:W] = x[:W]
    r = np.fft.irfft(np.conj(np.fft.rfft(a, m)) * np.fft.rfft(x, m), m)[: tmax + 1]   # Σ_{j<W} x_j·x_{j+τ}
    cs = np.concatenate([[0.0], np.cumsum(x * x)])
    e0 = cs[W]
    taus = np.arange(tmax + 1)
    e_t = cs[taus + W] - cs[taus]
    d = np.maximum(0.0, e0 + e_t - 2 * r)                            # разностная функция YIN
    d[0] = 0.0
    cm = np.cumsum(d[1:])
    dn = np.ones_like(d)
    dn[1:] = d[1:] * np.arange(1, tmax + 1) / np.maximum(cm, 1e-12)
    seg = dn[tmin:tmax]
    below = np.nonzero(seg < thr)[0]
    if len(below):
        i = below[0]
        while i + 1 < len(seg) and seg[i + 1] < seg[i]:
            i += 1
    else:
        i = int(np.argmin(seg))
        if seg[i] > 0.4:
            return 0.0, 0.0
    tau = i + tmin
    if 1 <= tau < tmax:
        a1, b1, c1 = dn[tau - 1], dn[tau], dn[tau + 1]
        den = a1 - 2 * b1 + c1
        tau = tau + (0.5 * (a1 - c1) / den if abs(den) > 1e-12 else 0.0)
    ti = min(tmax, int(round(tau)))
    return float(sr / tau), float(min(1.0, max(0.0, 1.0 - dn[ti])))


def midi_to_hz(m: float) -> float:
    return 440.0 * 2.0 ** ((m - 69) / 12.0)


def hz_to_midi(f: float) -> float:
    return 69 + 12 * math.log2(max(1e-6, f) / 440.0)


NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def note_name(m: int) -> str:
    return f"{NOTE_NAMES[m % 12]}{m // 12 - 1}"
