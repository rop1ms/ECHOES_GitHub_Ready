# genre_ai.py
"""
Жанры ECHOES: признаки звука, сведения о конкретной записи и исправления пользователя.

1. extract_features вычисляет 92 тембровых/ритмических признака с numpy/scipy.
   Громкость записи нормализуется; тишина и нечисловые данные отбрасываются.
   analyze_file у длинного трека берёт три удалённых фрагмента в пределах общего
   бюджета ~75 секунд и объединяет признаки медианой. Превью анализируются целиком.

2. GenreModel объединяет softmax-регрессию и нормированные евклидовы kNN.
   Обучение учитывает исправления пользователя, согласие независимых каталогов
   и превью из тематических плейлистов (их метки слабее, чем ручные).
   Дубликаты/конфликтующие автоматические метки удаляются, влияние одного
   артиста и объём класса ограничиваются. В проверке группы исполнителей
   разделены, стандартизация обучается только на тренировочной части.
   По отложенным предсказаниям смягчается излишняя уверенность; незнакомое
   звучание за пределами обучающей выборки не получает принудительный класс.
   Такая проверка характеризует только собранную выборку, не все музыкальные жанры.

3. fuse сводит теги файла, Deezer, iTunes, MusicBrainz, Last.fm и звук.
   У каждого источника ограниченный бюджет голосов. Теги артиста отделены от
   тегов записи, близкая длительность не спасает несовпадение артиста/версии.
   Слабые теги артиста не перетягивают голоса точного жанра в поджанр.
   При конфликте или недостатке сведений результат остаётся неопределённым.
   Несколько жанров допустимы; dist — доли взвешенных свидетельств, а не
   измеренная вероятность правильности. Необученная эвристика даёт только подсказки.

Ручные исправления хранятся отдельно в genre_corrections.json, не меняют файл
музыки, имеют приоритет в выдаче и используются при следующем обучении.
"""
from __future__ import annotations

import json
import math
import os
import random
import re
import hashlib
import tempfile
import threading
import time
from pathlib import Path

import numpy as np

_MEDIAN = []


def _median_filter():
    """scipy.ndimage грузится, только когда реально слушаем новый трек (не при каждом запуске плеера)."""
    if not _MEDIAN:
        try:
            from scipy.ndimage import median_filter as mf
        except Exception:                            # noqa: BLE001
            mf = None
        _MEDIAN.append(mf)
    return _MEDIAN[0]

import music_intel as mi

FEAT_VERSION = 2
MODEL_VERSION = 2
SR, N_FFT, HOP = mi.SR, mi.N_FFT, mi.HOP

FAMILIES = [name for _, name, *_ in mi.GENRE_FAMILIES]

# Поджанр → родитель. Поджанр забирает голоса родителя, если и сам заметен.
PARENT = {
    "metalcore": "metal", "metal": "rock", "punk": "rock", "emo": "rock", "indie": "rock",
    "dreampop": "indie", "phonk": "hiphop", "cloudrap": "hiphop", "drill": "hiphop",
    "trap": "hiphop", "dnb": "electronic", "dubstep": "electronic", "dance": "electronic",
    "synthwave": "electronic", "ambient": "electronic", "hyperpop": "pop", "jpop": "pop",
}

# Надёжность источников жанра.
SOURCE_WEIGHT = {
    "tag": 0.9,          # тег жанра в самом файле
    "deezer": 0.75,      # жанр АЛЬБОМА — грубый
    "itunes": 0.8,       # primaryGenreName — грубый, но точный
    "mb_rec": 1.3,       # MusicBrainz: жанры конкретной записи (голоса)
    "mb_rg": 1.0,        # MusicBrainz: жанры релиза
    "lastfm": 1.1,       # Last.fm: теги трека (+ артиста)
    "mb_artist": 0.18,   # контекст артиста, не доказательство жанра трека
    "lastfm_artist": 0.18,
    "legacy": 0.35,      # старый кэш без надёжного происхождения
    "audio": 1.2,        # модель по звуку (× доверие к модели)
    "prior": 0.12,       # эвристика не умеет доказать семантический жанр
}

SOURCE_RU = {"tag": "тег файла", "deezer": "Deezer", "itunes": "iTunes", "mb_rec": "MusicBrainz",
             "mb_rg": "MusicBrainz (релиз)", "lastfm": "Last.fm", "mb_artist": "MusicBrainz (артист)",
             "legacy": "онлайн-кэш", "audio": "ИИ по звуку", "prior": "эвристика по звуку",
             "lastfm_artist": "Last.fm (артист)", "manual": "ваша оценка жанра"}


def _log(msg):
    try:
        print(f"[genre-ai] {msg}")
    except Exception:                                # noqa: BLE001
        pass


# ------------------------------------------------------------------ #
#  1. Признаки звука                                                  #
# ------------------------------------------------------------------ #

N_MELS, N_MFCC = 40, 20
_CONTRAST_EDGES = [0, 200, 400, 800, 1600, 3200, 6400, SR / 2]
_BANDS = [(0, 60), (60, 250), (250, 2000), (2000, 6000), (6000, SR / 2)]
FEATURE_NAMES = (
    [f"mfcc{i}_m" for i in range(N_MFCC)] + [f"mfcc{i}_s" for i in range(N_MFCC)]
    + [f"dmfcc{i}_s" for i in range(N_MFCC)] + [f"contrast{i}" for i in range(7)]
    + ["cent_m", "cent_s", "bw_m", "roll_m", "flat_m", "flux_m", "flux_s", "zcr_m", "zcr_s"]
    + ["e_sub", "e_bass", "e_lowmid", "e_highmid", "e_air"]
    + ["bpm_l2", "pulse", "onsets", "tempo_stab", "perc", "loud", "dyn", "crest",
       "chroma_ent", "tonal", "mode"]
)
N_FEAT = len(FEATURE_NAMES)                          # 92

_cache = {}


def _mel_fb():
    if "mel" not in _cache:
        freqs = np.fft.rfftfreq(N_FFT, 1.0 / SR)
        mel = lambda f: 2595.0 * np.log10(1.0 + f / 700.0)
        imel = lambda m: 700.0 * (10 ** (m / 2595.0) - 1.0)
        pts = imel(np.linspace(mel(30.0), mel(11000.0), N_MELS + 2))
        fb = np.zeros((N_MELS, freqs.size), np.float32)
        for i in range(N_MELS):
            l, c, r = pts[i:i + 3]
            up = (freqs - l) / max(c - l, 1e-6)
            down = (r - freqs) / max(r - c, 1e-6)
            fb[i] = np.maximum(0.0, np.minimum(up, down))
        fb /= fb.sum(axis=1, keepdims=True) + 1e-12
        n = np.arange(N_MELS)
        dct = np.cos(np.pi / N_MELS * (n + 0.5)[None, :] * np.arange(N_MFCC)[:, None])
        dct *= np.sqrt(2.0 / N_MELS)
        dct[0] *= 1.0 / np.sqrt(2.0)
        _cache["mel"] = (fb, dct.astype(np.float32), freqs)
    return _cache["mel"]


def extract_features(x: np.ndarray):
    """Сигнал (моно, SR) → (вектор N_FEAT, словарь понятных признаков) или (None, None)."""
    if x is None or x.size < SR * 5:
        return None, None
    x = np.asarray(x, np.float32)
    if not np.isfinite(x).all():
        return None, None
    x = (x - float(np.mean(x))).astype(np.float32)
    rms = float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))
    if rms < 1e-5:
        return None, None
    # Recording/mastering gain is not a genre. Retain timbre, rhythm and
    # within-track dynamics while removing absolute volume from the features.
    x *= .1 / rms
    peak = float(np.max(np.abs(x))) + 1e-9
    S = mi._stft_mag(x)
    if S.shape[0] < 40:
        return None, None
    fb, dct, freqs = _mel_fb()
    P = S.astype(np.float64) ** 2
    tot = P.sum(axis=1) + 1e-12
    frame_db = 10 * np.log10(tot / N_FFT + 1e-12)
    active = frame_db > (frame_db.max() - 40)
    if active.sum() < 20:
        return None, None
    Pa, Sa, tota = P[active], S[active], tot[active]

    melspec = Pa @ fb.T.astype(np.float64)
    logmel = 10 * np.log10(melspec + 1e-10)
    mfcc = logmel @ dct.T.astype(np.float64)
    dm = np.diff(mfcc, axis=0)

    contrast = []
    power_total = float(Pa.sum())
    for lo, hi in zip(_CONTRAST_EDGES[:-1], _CONTRAST_EDGES[1:]):
        sel = (freqs >= lo) & (freqs < hi)
        if not np.any(sel) or float(Pa[:, sel].sum()) < power_total * 1e-7:
            # Contrast in a practically silent band is floating-point noise,
            # not evidence of distortion, cymbals or a musical style.
            contrast.append(0.)
            continue
        band = np.sort(Sa[:, sel], axis=1)
        k = max(1, int(round(band.shape[1] * 0.2)))
        valley = np.log(band[:, :k].mean(axis=1) + 1e-6)
        peakb = np.log(band[:, -k:].mean(axis=1) + 1e-6)
        contrast.append(float(np.mean(peakb - valley)))

    cent = (Pa * freqs).sum(axis=1) / tota
    bw = np.sqrt((Pa * (freqs[None, :] - cent[:, None]) ** 2).sum(axis=1) / tota)
    cum = np.cumsum(Pa, axis=1)
    roll = freqs[np.minimum(np.argmax(cum >= 0.85 * cum[:, -1:], axis=1), freqs.size - 1)]
    flat = np.exp(np.mean(np.log(Sa + 1e-6), axis=1)) / (Sa.mean(axis=1) + 1e-9)

    bandsel = freqs < 8000
    L = np.log1p(100.0 * S[:, bandsel] / (S[:, bandsel].max() + 1e-9))
    flux = np.concatenate([[0.0], np.maximum(0.0, np.diff(L, axis=0)).sum(axis=1)])
    flux_n = flux / (flux.mean() + 1e-9)
    bpm, pulse = mi._tempo(flux)
    half = flux.size // 2
    b1, _ = mi._tempo(flux[:half]) if half > 200 else (bpm, 0)
    b2, _ = mi._tempo(flux[half:]) if half > 200 else (bpm, 0)
    tempo_stab = float(1.0 - min(1.0, abs(math.log2(max(b1, 1) / max(b2, 1)))))

    frames = np.lib.stride_tricks.sliding_window_view(x, N_FFT)[::HOP][:S.shape[0]]
    zcr = np.mean(np.abs(np.diff(np.sign(frames), axis=1)) > 0, axis=1)[active]

    energies = []
    for lo, hi in _BANDS:
        sel = (freqs >= lo) & (freqs < hi)
        energies.append(float(np.mean(Pa[:, sel].sum(axis=1) / tota)))

    # доля ударных: медианный фильтр по времени (гармоники) против частоты (атаки)
    perc = 0.5
    median_filter = _median_filter()
    if median_filter is not None:
        try:
            M = melspec[: min(melspec.shape[0], 2600)]
            H = median_filter(M, size=(17, 1), mode="nearest")
            Pm = median_filter(M, size=(1, 7), mode="nearest")
            perc = float(np.sum(Pm ** 2 / (H ** 2 + Pm ** 2 + 1e-20) * M) / (np.sum(M) + 1e-20))
        except Exception:                            # noqa: BLE001
            pass

    rms = np.sqrt(tot / N_FFT)
    rms_db = 20 * np.log10(rms[active] + 1e-9)
    loud = float(10 * np.log10(np.mean(rms[active] ** 2) + 1e-12))
    dyn = float(np.percentile(rms_db, 95) - np.percentile(rms_db, 10))
    crest = float(20 * np.log10(peak / (np.sqrt(np.mean(x ** 2)) + 1e-9)))

    sel = (freqs >= 55) & (freqs <= 4200)
    pcs = ((np.round(12 * np.log2(freqs[sel] / 440.0)) + 9) % 12).astype(int)
    chroma = np.zeros(12)
    np.add.at(chroma, pcs, Sa[:, sel].mean(axis=0))
    chroma /= chroma.sum() + 1e-9
    chroma_ent = float(-(chroma * np.log(chroma + 1e-12)).sum() / math.log(12))
    cmaj = [np.corrcoef(chroma, np.roll(mi._KK_MAJOR, k))[0, 1] for k in range(12)]
    cmin = [np.corrcoef(chroma, np.roll(mi._KK_MINOR, k))[0, 1] for k in range(12)]
    tonal = float(max(0.0, max(max(cmaj), max(cmin))))
    mode = float(max(cmaj) - max(cmin))

    vec = np.concatenate([
        mfcc.mean(axis=0), mfcc.std(axis=0), dm.std(axis=0), contrast,
        [math.log(cent.mean() + 1), cent.std() / (cent.mean() + 1), math.log(bw.mean() + 1),
         math.log(roll.mean() + 1), flat.mean(), flux.mean() / bandsel.sum(), flux_n.std(),
         zcr.mean(), zcr.std()],
        energies,
        [math.log2(max(bpm, 30) / 120.0), pulse, float(np.mean(flux_n > 1.6)), tempo_stab, perc,
         loud, dyn, crest, chroma_ent, tonal, mode],
    ]).astype(np.float32)
    if vec.size != N_FEAT or not np.all(np.isfinite(vec)):
        return None, None
    info = {"bpm": float(bpm), "pulse": float(pulse), "low": energies[0] + energies[1] * 0.6,
            "sub": energies[0], "centroid": float(cent.mean()), "perc": perc,
            "flat": float(flat.mean()), "dyn": dyn, "air": energies[4]}
    return vec, info


def analyze_file(path: str, duration: float = 0.0, seconds: float = 75.0, *, return_audio=False, stop=None):
    """Sample several musical sections within the same total decoding budget."""
    if duration > max(60., seconds):
        length = min(25., seconds / 3)
        starts = [max(0., min(duration - length, duration * p - length / 2)) for p in (.2, .5, .8)]
    else:
        length, starts = seconds, [None]
    vectors, infos, audio = [], [], []
    for start in starts:
        if stop is not None and stop.is_set():
            return (None, None, None) if return_audio else (None, None)
        x = mi.decode_excerpt(path, duration, seconds=length, start=start)
        vector, info = extract_features(x)
        if vector is not None:
            vectors.append(vector); infos.append(info); audio.append(x)
    if not vectors:
        return (None, None, None) if return_audio else (None, None)
    # Median resists silence/breakdowns or an unusually loud chorus in one section.
    vector = np.median(np.stack(vectors), axis=0).astype(np.float32)
    info = {key: float(np.median([i[key] for i in infos])) for key in infos[0]}
    info["sections"] = float(len(vectors))
    return (vector, info, np.concatenate(audio)) if return_audio else (vector, info)


# ------------------------------------------------------------------ #
#  Экспертная эвристика (пока модель не обучена)                       #
# ------------------------------------------------------------------ #

# (bpm, разброс bpm, бас, яркость Гц, ударные, шумность/перегруз, динамика дБ)
PROTOTYPES = {
    "metalcore": (140, 40, .25, 2800, .45, .25, 8), "metal": (130, 35, .22, 2600, .40, .22, 9),
    "phonk": (130, 25, .45, 1800, .50, .12, 8), "dnb": (172, 8, .30, 2200, .55, .12, 9),
    "dubstep": (140, 6, .40, 2000, .50, .15, 10), "hyperpop": (150, 30, .30, 2800, .45, .12, 7),
    "cloudrap": (140, 20, .40, 1500, .40, .08, 9), "drill": (142, 6, .45, 1700, .50, .10, 9),
    "trap": (140, 15, .45, 1800, .50, .10, 9), "hiphop": (90, 12, .38, 1700, .50, .08, 10),
    "emo": (150, 30, .20, 2300, .40, .15, 10), "punk": (170, 25, .18, 2700, .40, .20, 8),
    "dreampop": (110, 25, .20, 1800, .30, .10, 12), "synthwave": (110, 15, .30, 2000, .45, .07, 9),
    "lofi": (80, 10, .30, 1200, .45, .07, 10), "ambient": (90, 40, .20, 1200, .15, .05, 18),
    "dance": (124, 6, .35, 2200, .55, .08, 8), "electronic": (128, 12, .32, 2200, .50, .08, 9),
    "funk": (108, 12, .28, 2000, .50, .06, 12), "jpop": (130, 25, .22, 2400, .40, .08, 9),
    "rnb": (95, 15, .32, 1600, .40, .06, 11), "jazz": (120, 40, .18, 1600, .30, .05, 16),
    "classical": (100, 40, .12, 1300, .12, .04, 22), "soundtrack": (110, 40, .20, 1500, .20, .05, 18),
    "folk": (110, 30, .15, 1600, .25, .05, 14), "latin": (98, 10, .38, 2000, .55, .07, 9),
    "indie": (120, 25, .20, 2200, .40, .10, 11), "rock": (125, 25, .20, 2400, .40, .15, 10),
    "pop": (115, 20, .28, 2100, .45, .07, 9),
}


def prior_predict(info: dict) -> dict:
    """Грубое распределение по жанрам из понятных признаков (без обучения)."""
    if not info:
        return {}
    bpm = max(40.0, float(info.get("bpm") or 0) or 120.0)
    scores = {}
    for fam, (pb, wb, low, cent, perc, flat, dyn) in PROTOTYPES.items():
        # темп с учётом ошибки «в два раза» (у трэпа 70 и 140 — одно и то же)
        dt = min(abs(math.log2(bpm / pb)), abs(math.log2(bpm * 2 / pb)) + 0.15,
                 abs(math.log2(bpm / 2 / pb)) + 0.15)
        d = (dt / max(0.05, math.log2(1 + wb / pb))) ** 2
        d += ((info.get("low", .25) - low) / 0.12) ** 2
        # центроид здесь взвешен по МОЩНОСТИ (бас доминирует) — реальные значения
        # примерно в 2.5 раза ниже «классических» по амплитуде, отсюда 0.4
        d += (math.log(max(info.get("centroid", 800), 80) / (cent * 0.4)) / 0.45) ** 2
        d += ((info.get("perc", .4) - perc) / 0.15) ** 2
        d += ((info.get("flat", .08) - flat) / 0.08) ** 2
        d += ((info.get("dyn", 10) - dyn) / 5.0) ** 2
        scores[fam] = -0.5 * d
    m = max(scores.values())
    ex = {f: math.exp((s - m) / 3.0) for f, s in scores.items()}     # мягко: эвристика
    z = sum(ex.values())
    return {f: v / z for f, v in ex.items()}


# ------------------------------------------------------------------ #
#  2. Модель                                                          #
# ------------------------------------------------------------------ #

class GenreModel:
    """softmax-регрессия + kNN на стандартизованных признаках."""

    def __init__(self):
        self.classes: list[str] = []
        self.mu = self.sd = None
        self.W = self.b = None
        self.X = None                   # обучающие векторы (стандартизованные) — для kNN
        self.y = None
        self.cv = None                  # {"acc": .., "top2": .., "n": .., "folds": ..}
        self.n_ref = self.n_lib = 0
        self.trained_ts = 0
        self.temperature = 1.0
        self.support_radius = 2.0

    @property
    def ready(self) -> bool:
        return self.W is not None and len(self.classes) >= 2

    # ── обучение ── #

    @staticmethod
    def _fit_softmax(X, y, C, sw, epochs=400, lr=0.05, l2=2e-3, seed=0):
        rng = np.random.default_rng(seed)
        n, d = X.shape
        W = rng.normal(0, 0.01, (d, C))
        b = np.zeros(C)
        Y = np.eye(C)[y]
        mW, vW, mb, vb = np.zeros_like(W), np.zeros_like(W), np.zeros_like(b), np.zeros_like(b)
        b1, b2 = 0.9, 0.999
        swn = sw / sw.sum()
        for t in range(1, epochs + 1):
            Z = X @ W + b
            Z -= Z.max(axis=1, keepdims=True)
            P = np.exp(Z)
            P /= P.sum(axis=1, keepdims=True)
            G = (P - Y) * swn[:, None]
            gW = X.T @ G + l2 * W
            gb = G.sum(axis=0)
            mW = b1 * mW + (1 - b1) * gW; vW = b2 * vW + (1 - b2) * gW ** 2
            mb = b1 * mb + (1 - b1) * gb; vb = b2 * vb + (1 - b2) * gb ** 2
            W -= lr * (mW / (1 - b1 ** t)) / (np.sqrt(vW / (1 - b2 ** t)) + 1e-8)
            b -= lr * (mb / (1 - b1 ** t)) / (np.sqrt(vb / (1 - b2 ** t)) + 1e-8)
        return W, b

    def fit(self, X, labels, weights=None, cv=True, groups=None):
        X = np.asarray(X, np.float64)
        labels = list(labels)
        weights = np.ones(len(labels)) if weights is None else np.asarray(weights, float)
        self.W = None
        self.cv = None
        self.temperature, self.support_radius = 1., 2.
        if X.ndim != 2 or not X.shape[1] or X.shape[0] != len(labels) or weights.shape != (len(labels),):
            return False
        valid = np.isfinite(X).all(axis=1) & np.isfinite(weights) & (weights > 0)
        if not valid.any():
            return False
        # Exact duplicates must never land in different validation folds.
        if groups is None:
            groups = [hashlib.sha256(np.round(row, 5).tobytes()).hexdigest() for row in X]
        if len(groups) != len(labels):
            return False
        groups = np.asarray([str(g) for g in groups], object)[valid]
        labels = [l for l, keep in zip(labels, valid) if keep]
        X, weights = X[valid], weights[valid]
        counts = {}
        for l in labels:
            counts[l] = counts.get(l, 0) + 1
        self.classes = sorted(c for c, n in counts.items() if n >= 5)
        keep = np.array([l in self.classes for l in labels])
        if len(self.classes) < 2 or keep.sum() < 12:
            self.W = None
            return False
        X, w, groups = X[keep], weights[keep], groups[keep]
        y = np.array([self.classes.index(l) for l, k in zip(labels, keep) if k])
        self.mu = X.mean(axis=0)
        self.sd = np.maximum(X.std(axis=0), 1e-3)
        Xs = np.clip((X - self.mu) / self.sd, -6, 6)
        cls_w = np.array([1.0 / np.sum(y == c) for c in range(len(self.classes))])
        sw = w * cls_w[y]
        if cv and len(y) >= 30 and len(set(groups)) >= 2:
            self.cv = self._cross_validate(X, y, w, groups)
        self.W, self.b = self._fit_softmax(Xs, y, len(self.classes), sw)
        self.X, self.y = Xs.astype(np.float32), y
        # A forced choice among a few known classes is meaningless for an
        # unfamiliar sound. Learn a support radius from distinct neighbours.
        nearest = np.empty(len(Xs))
        norm = (Xs ** 2).sum(axis=1)
        for first in range(0, len(Xs), 128):
            block = Xs[first:first + 128]
            distance = np.maximum(0., ((block ** 2).sum(axis=1)[:, None] + norm[None, :] - 2 * block @ Xs.T) / Xs.shape[1])
            distance[np.arange(len(block)), first + np.arange(len(block))] = np.inf
            nearest[first:first + len(block)] = np.sqrt(distance.min(axis=1))
        self.support_radius = max(.5, float(np.percentile(nearest, 95)) * 2.5)
        self.trained_ts = int(time.time())
        return True

    def _cross_validate(self, X, y, weights, groups, folds=5):
        idx = np.arange(len(y))
        rng = np.random.default_rng(42)
        unique = list(dict.fromkeys(groups))
        rng.shuffle(unique)
        unique.sort(key=lambda g: -int(np.sum(groups == g)))
        folds = min(folds, len(unique))
        fold_of = np.empty(len(y), int)
        loads = np.zeros((folds, len(self.classes)))
        counts = np.bincount(y, minlength=len(self.classes))
        for g in unique:
            gi = idx[groups == g]
            classes = np.bincount(y[gi], minlength=len(self.classes))
            cost = (loads / np.maximum(counts, 1)) @ classes + loads.sum(axis=1) / max(1, len(y))
            f = int(np.argmin(cost))
            fold_of[gi] = f
            loads[f] += classes
        P_all = np.zeros((len(y), len(self.classes)))
        tested = np.zeros(len(y), bool)
        for f in range(folds):
            tr, te = fold_of != f, fold_of == f
            if te.sum() == 0 or len(np.unique(y[tr])) < 2:
                continue
            # Fit preprocessing on TRAIN only; held-out artists are genuinely unseen.
            mu, sd = X[tr].mean(axis=0), np.maximum(X[tr].std(axis=0), 1e-3)
            train, test = np.clip((X[tr] - mu) / sd, -6, 6), np.clip((X[te] - mu) / sd, -6, 6)
            cw = 1 / np.maximum(np.bincount(y[tr], minlength=len(self.classes)), 1)
            W, b = self._fit_softmax(train, y[tr], len(self.classes), weights[tr] * cw[y[tr]], epochs=250)
            P_all[te] = self._proba(test, W, b, train, y[tr])
            tested[te] = True
        if tested.sum() < 12:
            return None
        P, actual = P_all[tested], y[tested]
        # Only flatten overconfident scores; never sharpen a small dataset.
        losses = []
        for temperature in (1., 1.25, 1.5, 2., 3.):
            calibrated = self._temper(P, temperature)
            losses.append((-np.log(np.maximum(calibrated[np.arange(len(actual)), actual], 1e-12)).mean(), temperature))
        self.temperature = min(losses)[1]
        order = np.argsort(-P, axis=1)
        per_class = {c: round(float(np.mean(order[actual == i, 0] == i)), 4)
                     for i, c in enumerate(self.classes) if np.any(actual == i)}
        return {"acc": round(float(np.mean(order[:, 0] == actual)), 4),
                "top2": round(float(np.mean((order[:, :2] == actual[:, None]).any(axis=1))), 4),
                "n": int(tested.sum()), "folds": folds, "groups": len(unique), "grouped": True,
                "per_class": per_class, "chance": round(1.0 / len(self.classes), 4)}

    @staticmethod
    def _temper(p, temperature):
        p = np.maximum(p, 1e-12) ** (1. / max(1., temperature))
        return p / p.sum(axis=-1, keepdims=True)

    def _proba(self, Xs, W, b, Xtr, ytr, k=9):
        Z = Xs @ W + b
        Z -= Z.max(axis=1, keepdims=True)
        Ps = np.exp(Z)
        Ps /= Ps.sum(axis=1, keepdims=True)
        # Euclidean neighbours retain radial/energy-shape information that
        # cosine similarity discards, after gain-invariant feature extraction.
        distance = np.maximum(0., ((Xs ** 2).sum(axis=1)[:, None] + (Xtr ** 2).sum(axis=1)[None, :] - 2 * Xs @ Xtr.T) / Xs.shape[1])
        k = min(k, Xtr.shape[0])
        nn = np.argpartition(distance, k - 1, axis=1)[:, :k]
        Pk = np.full_like(Ps, 1e-3)
        counts = np.maximum(np.bincount(ytr, minlength=len(self.classes)), 1)
        for i in range(Xs.shape[0]):
            minimum = distance[i, nn[i]].min()
            bandwidth = max(.05, float(np.median(distance[i, nn[i]] - minimum)))
            for j in nn[i]:
                Pk[i, ytr[j]] += math.exp(-(float(distance[i, j]) - minimum) / bandwidth) / math.sqrt(counts[ytr[j]])
        Pk /= Pk.sum(axis=1, keepdims=True)
        return 0.6 * Ps + 0.4 * Pk

    # ── предсказание ── #

    def predict(self, vec) -> dict:
        if not self.ready or vec is None:
            return {}
        try:
            v = np.asarray(vec, np.float64).reshape(1, -1)
        except (TypeError, ValueError):
            return {}
        if v.shape[1] != self.mu.size or not np.isfinite(v).all():
            return {}
        raw = (v - self.mu) / self.sd
        if np.sqrt(np.mean(raw ** 2)) > 6:
            return {}
        Xs = np.clip(raw, -6, 6)
        distance = float(np.sqrt(((self.X.astype(float) - Xs) ** 2).mean(axis=1)).min())
        if distance > self.support_radius:
            return {}
        p = self._proba(Xs, self.W, self.b, self.X.astype(np.float64), self.y)[0]
        p = self._temper(p, self.temperature)
        return {c: float(p[i]) for i, c in enumerate(self.classes)}

    def trust(self) -> float:
        """Доверие 0.1..0.9: проверка на отложенных группах и объём выборки."""
        if not self.ready:
            return 0.0
        if not self.cv or not self.cv.get("grouped"):
            return 0.15
        ch = self.cv.get("chance", 0.1)
        skill = max(0., (self.cv["acc"] - ch) / max(1e-6, 1 - ch))
        size = min(1., math.sqrt(self.cv.get("n", 0) / 150))
        return float(min(.9, max(.1, skill * size)))

    # ── сохранение ── #

    def to_json(self) -> dict:
        if not self.ready:
            return {"v": MODEL_VERSION, "ready": False}
        r = lambda a, n=4: np.round(np.asarray(a, float), n).tolist()
        return {"v": MODEL_VERSION, "feat_v": FEAT_VERSION, "ready": True, "classes": self.classes,
                "mu": r(self.mu, 5), "sd": r(self.sd, 5), "W": r(self.W), "b": r(self.b),
                "X": r(self.X, 3), "y": self.y.tolist(), "cv": self.cv, "n_ref": self.n_ref,
                "n_lib": self.n_lib, "ts": self.trained_ts, "temperature": self.temperature,
                "support_radius": self.support_radius}

    @classmethod
    def from_json(cls, d) -> "GenreModel":
        m = cls()
        if not d or not d.get("ready") or d.get("v") != MODEL_VERSION or d.get("feat_v") != FEAT_VERSION:
            return m
        m.classes = list(d["classes"])
        m.mu, m.sd = np.array(d["mu"]), np.array(d["sd"])
        m.W, m.b = np.array(d["W"]), np.array(d["b"])
        m.X, m.y = np.array(d["X"], np.float32), np.array(d["y"], int)
        m.cv, m.n_ref, m.n_lib, m.trained_ts = d.get("cv"), d.get("n_ref", 0), d.get("n_lib", 0), d.get("ts", 0)
        m.temperature = max(1., float(d.get("temperature", 1)))
        m.support_radius = max(.5, float(d.get("support_radius", 2)))
        if (not m.mu.size or m.mu.ndim != 1 or m.sd.shape != m.mu.shape
                or len(set(m.classes)) != len(m.classes) or m.b.shape != (len(m.classes),)
                or m.W.shape != (m.mu.size, len(m.classes)) or m.X.ndim != 2 or m.X.shape[0] < 12
                or m.X.shape[1] != m.mu.size or m.y.size != m.X.shape[0]
                or not np.isfinite(m.W).all() or not np.isfinite(m.X).all()
                or not np.isfinite(m.mu).all() or not np.isfinite(m.b).all()
                or not np.isfinite(m.sd).all() or np.any(m.sd <= 0)
                or np.any(m.y < 0) or np.any(m.y >= len(m.classes))
                or not math.isfinite(m.temperature) or not math.isfinite(m.support_radius)):
            return cls()
        return m


# ------------------------------------------------------------------ #
#  3. Слияние источников                                              #
# ------------------------------------------------------------------ #

def _vote(acc: dict, src_hits: dict, names, src: str, weight: float, counts=None):
    """A provider has one vote budget, however many tags it returns."""
    if not names:
        return
    votes = {}
    for i, raw in enumerate(names):
        fams = mi.genre_families([raw])
        if not fams:
            continue
        value = max(0., float(counts[i])) if counts and i < len(counts) else 1 / (1 + .35 * i)
        for f in fams:
            votes[f] = max(votes.get(f, 0.), value / len(fams))
    total = sum(votes.values())
    if total <= 0:
        return
    for f, value in votes.items():
        acc[f] = acc.get(f, 0.0) + weight * value / total
        src_hits.setdefault(f, set()).add(src)


def _absorb_parents(acc: dict, hits=None):
    """Specific track evidence can refine a broad label; artist tags cannot."""
    def depth(f):
        d = 0
        while f in PARENT and d < 5:
            f = PARENT[f]; d += 1
        return d
    for child in sorted(acc, key=depth, reverse=True):
        p = PARENT.get(child)
        support = (hits or {}).get(child, set()) - {"mb_artist", "lastfm_artist", "prior", "legacy"}
        if p and support and acc.get(p, 0) > 0 and acc[child] >= 0.4 * acc[p]:
            moved = 0.4 * acc[p]
            acc[child] += moved
            acc[p] -= moved
    return acc


def online_evidence(track: dict, meta: dict | None, artist: dict | None):
    """Голоса онлайн-источников и тега файла (без звука) → (acc, src_hits, weight_sum)."""
    acc, hits = {}, {}
    tag = str(track.get("genre", "") or "").strip()
    if tag:
        _vote(acc, hits, [g.strip() for g in tag.replace(";", "/").split("/") if g.strip()],
              "tag", SOURCE_WEIGHT["tag"])
    m = meta or {}
    by = m.get("by_src")
    if by:
        for key, d in by.items():
            if key not in SOURCE_WEIGHT or not d:
                continue
            sc = float(d.get("score", 1.0))
            if not math.isfinite(sc) or sc < .8:
                continue
            source = "lastfm_artist" if key == "lastfm" and m.get("v", 1) < 3 else key
            w = SOURCE_WEIGHT[source] * min(1., max(0., (sc - .75) / .2))
            # Recording, release and artist from the same catalogue are not
            # three independent confirmations of a musical style.
            if key == "mb_rg" and by.get("mb_rec", {}).get("g"):
                w *= .45
            _vote(acc, hits, d.get("g") or [], source, w, d.get("c"))
    else:
        _vote(acc, hits, m.get("genres") or [], "legacy", SOURCE_WEIGHT["legacy"])
        _vote(acc, hits, m.get("tags") or [], "legacy", SOURCE_WEIGHT["legacy"] * .5)
    if artist and artist.get("tags"):
        _vote(acc, hits, artist["tags"], "mb_artist", SOURCE_WEIGHT["mb_artist"])
    return acc, hits, sum(acc.values())


def fuse(track: dict, meta: dict | None, artist: dict | None, audio_pred: dict | None = None,
         audio_src: str = "audio", audio_trust: float = 1.0) -> dict:
    """Итоговое решение по жанру трека.

    → {"family", "families", "conf", "dist", "sources"} (пусто, если ничего не известно)."""
    acc, hits, online_w = online_evidence(track, meta, artist)
    # поджанр поглощает родителя только среди ОНЛАЙН-голосов: модель по звуку
    # сама различает metal/metalcore, её вероятности не перераспределяем
    _absorb_parents(acc, hits)
    audio_sharpness = 0.
    if audio_pred:
        audio_pred = {f: float(p) for f, p in audio_pred.items()
                      if f in FAMILIES and math.isfinite(float(p)) and float(p) > 0}
        total = sum(audio_pred.values())
        audio_pred = {f: p / total for f, p in audio_pred.items()} if total else {}
        entropy = -sum(p * math.log(p) for p in audio_pred.values())
        audio_sharpness = 1 - entropy / math.log(max(2, len(audio_pred)))
        w = SOURCE_WEIGHT.get(audio_src, 0) * max(0., min(1., audio_trust)) * max(.05, audio_sharpness)
        # когда онлайн уверен, звук только уточняет; когда онлайн молчит — решает звук
        if online_w > 1.5:
            w *= 0.6
        for f, p in audio_pred.items():
            if p > 0.04:
                acc[f] = acc.get(f, 0.0) + w * p
                if p > 0.2:
                    hits.setdefault(f, set()).add(audio_src)
    acc = {f: v for f, v in acc.items() if v > 1e-6}
    if not acc:
        return {}
    z = sum(acc.values())
    dist = {f: v / z for f, v in sorted(acc.items(), key=lambda kv: -kv[1])}
    top = next(iter(dist))
    agree = len(hits.get(top, ()))
    conf = dist[top] * (1 - math.exp(-z / 1.2)) * min(1.0, 0.6 + 0.2 * agree)
    all_sources = set().union(*hits.values()) if hits else set()
    direct = all_sources - {"mb_artist", "lastfm_artist", "prior", "audio", "legacy"}
    provider = lambda s: "musicbrainz" if s.startswith("mb_") else "lastfm" if s.startswith("lastfm") else s
    independent = {provider(s) for s in hits.get(top, ()) if s not in {"mb_artist", "lastfm_artist", "prior", "audio", "legacy"}}
    ordered = list(dist.values())
    margin = ordered[0] - (ordered[1] if len(ordered) > 1 else 0)
    fams = [f for f, p in dist.items() if p >= .18 and p >= dist[top] * .5][:3] or [top]
    reason = "Жанры нескольких источников сопоставлены с конкретным треком."
    accepted = True
    if not direct:
        if audio_src == "audio" and audio_pred and audio_trust >= .45 and dist[top] >= .55 and margin >= .18 and audio_sharpness >= .25:
            conf = min(conf, .75)
            reason = "Предположение по звуку; подтверждения для этого трека в каталогах нет."
        else:
            accepted = False
            conf = min(conf, .12)
            reason = "Недостаточно данных о самом треке: теги артиста и общие звуковые признаки не доказывают жанр."
    elif direct == {"tag"} and len(fams) > 1:
        reason = "В теге файла указано несколько жанров."
        conf = min(.55, max(.3, conf))
    elif dist[top] < .5 or margin < .12:
        accepted = False
        conf = min(conf, .25)
        reason = "Источники расходятся; надёжно выбрать жанр пока нельзя."
    elif len(independent) < 2:
        conf = min(conf, .65 if set(hits.get(top, ())) & {"tag", "mb_rec", "lastfm"} else .5)
        reason = "Есть один источник жанра; результат требует подтверждения."
    if accepted and conf < .2:
        accepted = False
    if not accepted:
        fams = []
    return {"family": top, "families": fams, "conf": round(float(conf), 3),
            "dist": {f: round(p, 3) for f, p in list(dist.items())[:5]},
            "sources": sorted(hits.get(top, ())), "status": "uncertain" if not accepted else "mixed" if len(fams) > 1 else "identified",
            "reason": reason}


def training_label(track, meta, artist):
    """Метка для обучения: только если онлайн-источники надёжно согласны."""
    r = fuse(track, meta, artist)
    if not r:
        return None
    acc, hits, w = online_evidence(track, meta, artist)
    srcs = hits.get(r["family"], set()) - {"mb_artist", "lastfm_artist", "legacy"}
    providers = {"musicbrainz" if s.startswith("mb_") else s for s in srcs}
    if r.get("families") and len(providers) >= 2 and r["conf"] >= .45 and r["dist"][r["family"]] >= .65:
        return r["family"]
    return None


# ------------------------------------------------------------------ #
#  Хранилище модели и эталонов                                        #
# ------------------------------------------------------------------ #

class GenreAI:
    """Модель + эталоны + обучение. Один экземпляр на приложение (get_ai)."""

    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir)
        self.model_path = self.dir / "genre_ai.json"
        self.refs_path = self.dir / "genre_refs.json"
        self.corrections_path = self.dir / "genre_corrections.json"
        self.lock = threading.RLock()
        self.model = GenreModel()
        self.refs: list = []              # [{"f": family, "x": [...], "id": deezer_id}]
        self.corrections = {}
        try:
            self.model = GenreModel.from_json(json.loads(self.model_path.read_text("utf-8")))
        except Exception:                            # noqa: BLE001
            pass
        try:
            d = json.loads(self.refs_path.read_text("utf-8"))
            if d.get("feat_v") == FEAT_VERSION:
                self.refs = list(d.get("refs") or [])
        except Exception:                            # noqa: BLE001
            pass

        try:
            data = json.loads(self.corrections_path.read_text("utf-8"))
            for key, entry in (data.get("tracks") or {}).items():
                families = [f for f in entry.get("families", []) if f in FAMILIES]
                if families:
                    self.corrections[str(key)] = {"families": list(dict.fromkeys(families)), "ts": entry.get("ts", 0)}
        except Exception:                            # noqa: BLE001
            pass

    def _save_json(self, path, obj):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(obj, ensure_ascii=False), "utf-8")
            os.replace(tmp, path)
            return True
        except Exception as e:                       # noqa: BLE001
            _log(f"save {path.name}: {e!r}")
            return False

    @staticmethod
    def _correction_key(track):
        path = str(track.get("path") or "")
        return path or "identity:" + mi.IntelStore.meta_key(track.get("artist", ""), track.get("title", ""))

    def correction(self, track) -> list[str]:
        with self.lock:
            return list((self.corrections.get(self._correction_key(track)) or {}).get("families") or [])

    def set_correction(self, track, families) -> bool:
        """User-confirmed labels; [] removes the correction without editing audio tags."""
        if isinstance(families, str):
            families = [families]
        families = list(dict.fromkeys(families or []))
        if any(f not in FAMILIES for f in families):
            raise ValueError("Unknown genre family")
        self._GCACHE.clear()                                   # ручной жанр — сразу виден везде
        with self.lock:
            key = self._correction_key(track)
            old = self.corrections.get(key)
            if families:
                self.corrections[key] = {"families": families, "ts": int(time.time())}
            else:
                self.corrections.pop(key, None)
            if not self._save_json(self.corrections_path, {"version": 1, "tracks": self.corrections}):
                if old is None:
                    self.corrections.pop(key, None)
                else:
                    self.corrections[key] = old
                raise OSError("Не удалось сохранить исправление жанра")
        return True

    def save_refs(self):
        with self.lock:
            self._save_json(self.refs_path, {"feat_v": FEAT_VERSION, "refs": self.refs})

    # ── предсказание для профиля трека ── #

    def predict_track(self, store, track) -> tuple[dict, str, float]:
        """(распределение, источник, доверие) по звуку трека — или ({}, "", 0)."""
        e = store.ai_entry(track)
        if not e:
            return {}, "", 0.0
        with self.lock:
            model = self.model
        if model.ready and e.get("x"):
            p = model.predict(e["x"])
            if p:
                return p, "audio", model.trust()
        if e.get("info"):
            return prior_predict(e["info"]), "prior", 1.0
        return {}, "", 0.0

    MIN_CONF = 0.20            # weak speculation must not steer genre waves/EQ

    _GCACHE: dict = {}

    def genre_of(self, store, track) -> dict:
        """Жанр трека; один и тот же трек в течение 30 с — из памяти (страницы спрашивают много раз)."""
        import time as _t
        key = (str(track.get("path", "")), track.get("title", ""), track.get("artist", ""),
               getattr(store, "_dirty", 0), id(store))
        hit = self._GCACHE.get(key)
        now = _t.monotonic()
        if hit is not None and now - hit[0] < 30:
            return hit[1]
        r = self._genre_of(store, track)
        if len(self._GCACHE) > 20000:
            self._GCACHE.clear()
        self._GCACHE[key] = (now, r)
        return r

    def _genre_of(self, store, track) -> dict:
        corrected = self.correction(track)
        if corrected:
            return {"family": corrected[0], "families": corrected, "conf": 1.,
                    "dist": {f: round(1 / len(corrected), 3) for f in corrected},
                    "sources": ["manual"], "status": "manual", "reason": "Жанр указан вами и имеет приоритет над автоматическим распознаванием."}
        meta, artist = store.meta(track)
        pred, src, trust = self.predict_track(store, track)
        r = fuse(track, meta, artist, pred or None, src or "audio", trust)
        if r and r["conf"] < self.MIN_CONF:
            r = dict(r, families=[])
        return r

    # ── обучение ── #

    def train(self, library, store) -> dict:
        rows = []
        with self.lock:
            refs = list(self.refs)
        for t in library:
            e = store.ai_entry(t)
            if not e or len(e.get("x") or []) != N_FEAT:
                continue
            meta, artist = store.meta(t)
            manual = self.correction(t)
            lab = manual[0] if len(manual) == 1 else training_label(t, meta, artist) if not manual else None
            if lab:
                group = mi.norm(mi.primary_artist(t.get("artist", ""))) or "track:" + str(t.get("path"))
                rows.append((e["x"], lab, 2.5 if manual else 1., group, "lib", bool(manual)))
        for r in refs:
            if r.get("f") in FAMILIES and len(r.get("x") or []) == N_FEAT:
                group = mi.norm(mi.primary_artist(r.get("artist", ""))) or "reference:" + str(r.get("id"))
                rows.append((r["x"], r["f"], .65, group, "ref", False))
        # Duplicate files/previews are not independent examples. A user's
        # correction wins; conflicting automatic labels are excluded.
        unique, conflicts = {}, set()
        for row in rows:
            vector = np.asarray(row[0], float)
            if not np.isfinite(vector).all():
                continue
            key = hashlib.sha256(np.round(vector, 4).tobytes()).hexdigest()
            old = unique.get(key)
            if old and old[1] != row[1]:
                if old[5]:
                    continue
                if not row[5]:
                    conflicts.add(key)
                    continue
            if row[5]:
                conflicts.discard(key)
            if old is None or row[2] > old[2] or old[1] != row[1]:
                unique[key] = row
        selected, per_class, per_artist = [], {}, {}
        for key, row in sorted(unique.items(), key=lambda pair: -pair[1][2]):
            if key in conflicts:
                continue
            label, group = row[1], row[3]
            if per_class.get(label, 0) >= 120 or per_artist.get((group, label), 0) >= 8 and not row[5]:
                continue
            per_class[label] = per_class.get(label, 0) + 1
            per_artist[group, label] = per_artist.get((group, label), 0) + 1
            selected.append(row)
        X = [row[0] for row in selected]
        labels = [row[1] for row in selected]
        weights = [row[2] for row in selected]
        groups = [row[3] for row in selected]
        n_ref = sum(row[4] == "ref" for row in selected)
        n_lib = len(selected) - n_ref
        m = GenreModel()
        ok = bool(X) and m.fit(np.array(X), labels, weights, groups=groups)
        m.n_ref, m.n_lib = n_ref, n_lib
        with self.lock:
            self.model = m
        self._save_json(self.model_path, m.to_json())
        _log(f"train: refs={n_ref} lib={n_lib} ok={ok} cv={m.cv}")
        return self.status()

    def status(self) -> dict:
        with self.lock:
            m = self.model
            n_refs = len(self.refs)
        return {"ready": m.ready, "classes": list(m.classes), "cv": m.cv, "n_ref": m.n_ref,
                "n_lib": m.n_lib, "refs": n_refs, "ts": m.trained_ts}

    def status_text(self) -> str:
        s = self.status()
        if not s["ready"]:
            base = "Модель по звуку ещё не обучена. Используются теги и каталоги; звук даёт только неподтверждённые подсказки."
            if s["refs"]:
                base += f" Эталонов: {s['refs']}."
            return base
        cv = s["cv"]
        acc = (f"проверка на отложенных исполнителях: {cv['acc'] * 100:.0f}% (топ-2: {cv['top2'] * 100:.0f}%, "
               f"случайно было бы {cv['chance'] * 100:.0f}%)") if cv else "точность ещё не измерена"
        return (f"Модель обучена: {len(s['classes'])} жанров, {s['n_ref']} эталонов + "
                f"{s['n_lib']} треков вашей библиотеки; {acc}.")

    # ── эталоны Deezer ── #

    def build_references(self, per_family=12, progress=None, stop=None) -> int:
        """Скачивает превью Deezer из плейлистов каждого жанра и превращает их
        в обучающие векторы. Превью удаляются сразу после разбора."""
        import requests
        progress = progress or (lambda *a: None)
        stop = stop or threading.Event()
        with self.lock:
            have = {(r.get("f"), r.get("id")) for r in self.refs}
            per_have = {}
            for r in self.refs:
                per_have[r.get("f")] = per_have.get(r.get("f"), 0) + 1
        jobs = []
        for fam in FAMILIES:
            need = per_family - per_have.get(fam, 0)
            if need <= 0:
                continue
            tracks = _reference_tracks(fam, need * 3)
            random.Random(fam).shuffle(tracks)
            artists = {}
            for t in tracks:
                if (fam, t["id"]) in have:
                    continue
                a = t.get("artist", "")
                if artists.get(a, 0) >= 2:           # не больше двух треков одного артиста
                    continue
                artists[a] = artists.get(a, 0) + 1
                jobs.append((fam, t))
                if sum(1 for f, _ in jobs if f == fam) >= need:
                    break
            if stop.is_set():
                return 0
        added = 0
        tmpdir = Path(tempfile.mkdtemp(prefix="echoes_refs_"))
        try:
            for i, (fam, t) in enumerate(jobs):
                if stop.is_set():
                    break
                progress(i, len(jobs), f"{mi.GENRE_RU.get(fam, fam)}: {t.get('title', '')}")
                f = tmpdir / f"{t['id']}.mp3"
                try:
                    r = requests.get(t["preview"], timeout=20, headers={"User-Agent": mi.UA})
                    if r.status_code != 200 or len(r.content) < 20000:
                        continue
                    f.write_bytes(r.content)
                    x = mi.decode_excerpt(str(f), 0.0, seconds=30.0)
                    vec, _ = extract_features(x)
                except Exception as e:               # noqa: BLE001
                    _log(f"ref {t.get('id')}: {e!r}")
                    vec = None
                finally:
                    try:
                        f.unlink()
                    except OSError:
                        pass
                if vec is None:
                    continue
                with self.lock:
                    self.refs.append({"f": fam, "id": t["id"], "artist": t.get("artist", ""), "x": np.round(vec, 4).tolist()})
                added += 1
                if added % 20 == 0:
                    self.save_refs()
        finally:
            try:
                for p in tmpdir.iterdir():
                    p.unlink()
                tmpdir.rmdir()
            except OSError:
                pass
        self.save_refs()
        return added


# Поисковые запросы плейлистов Deezer для эталонов каждого жанра.
REFERENCE_QUERIES = {
    "metalcore": ["metalcore", "deathcore"], "metal": ["heavy metal", "thrash metal"],
    "phonk": ["phonk", "drift phonk"], "dnb": ["drum and bass", "breakcore"],
    "dubstep": ["dubstep", "riddim"], "hyperpop": ["hyperpop", "digicore"],
    "cloudrap": ["cloud rap", "emo rap"], "drill": ["uk drill", "drill"],
    "trap": ["trap hits", "trap"], "hiphop": ["boom bap", "hip hop classics"],
    "emo": ["emo", "midwest emo"], "punk": ["punk rock", "pop punk"],
    "dreampop": ["dream pop", "shoegaze"], "synthwave": ["synthwave", "retrowave"],
    "lofi": ["lofi hip hop", "lofi beats"], "ambient": ["ambient", "ambient relax"],
    "dance": ["house music", "dance hits"], "electronic": ["techno", "electronic"],
    "funk": ["funk classics", "funk"], "jpop": ["j-pop", "anime songs"],
    "rnb": ["r&b", "neo soul"], "jazz": ["jazz classics", "jazz"],
    "classical": ["classical music", "piano classical"], "soundtrack": ["film soundtrack", "epic soundtrack"],
    "folk": ["folk acoustic", "indie folk"], "latin": ["reggaeton", "latin hits"],
    "indie": ["indie rock", "alternative indie"], "rock": ["rock classics", "hard rock"],
    "pop": ["pop hits", "top pop"],
}


def _reference_tracks(fam: str, want: int) -> list:
    out, seen = [], set()
    for q in REFERENCE_QUERIES.get(fam, [fam]):
        j = mi._jget("deezer", "https://api.deezer.com/search/playlist", {"q": q, "limit": 3})
        for pl in (j or {}).get("data") or []:
            pid = pl.get("id")
            if not pid:
                continue
            jt = mi._jget("deezer", f"https://api.deezer.com/playlist/{pid}/tracks", {"limit": 60})
            for t in (jt or {}).get("data") or []:
                if t.get("id") in seen or not t.get("preview"):
                    continue
                seen.add(t.get("id"))
                out.append({"id": t["id"], "preview": t["preview"], "title": t.get("title", ""),
                            "artist": (t.get("artist") or {}).get("name", "")})
            if len(out) >= want:
                return out
    return out


_AI = None
_AI_LOCK = threading.Lock()


def get_ai(data_dir=None) -> GenreAI | None:
    """Общий экземпляр (создаётся при первом вызове с data_dir)."""
    global _AI
    with _AI_LOCK:
        if _AI is None and data_dir is not None:
            _AI = GenreAI(Path(data_dir))
        return _AI
