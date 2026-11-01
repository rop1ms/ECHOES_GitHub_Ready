# daw_split.py
"""
Разбор бита на звуки — для ремейков: из любого трека достаёт бочку, 808 (с нотами), малый,
хэт, открытый хэт и мелодию без ударных, кладёт их WAV-ами в «Мои сэмплы → Разобранные биты»
и собирает проект студии: каналы с этими звуками и паттерн из 4 тактов, где ноты стоят как
в оригинале (808 — на своих нотах и с нужной длиной).

Как:
1. Вокал прочь (нейросеть, как в мэшап-боте) — дальше работаем с минусом.
2. Спектр минуса делится на «удары» и «тянущееся» (медианные фильтры HPSS): бочка, малый и
   хэты — в ударной части, 808 и мелодия — в тянущейся.
3. Удары ищутся по полосам (низ / середина / верх) на сетке долей трека (1/16, у хэтов 1/32):
   рост энергии полосы в окне ±25 мс вокруг шага сетки.
4. Чистый звук: из всех ударов одного вида берутся те, где другие полосы тише всего, они
   выравниваются по атаке и усредняются — один и тот же сэмпл складывается, а музыка вокруг
   (у каждого удара своя) гасится. 808 — одна самая чистая нота, её высота определяется.
5. Паттерн — 4 такта с самого мощного места бита (обычно припев).
"""
from __future__ import annotations

import os
import re

import numpy as np

import daw_dsp as D
import daw_project as PR
from daw_dsp import SR

FS = 22050                       # анализ
NF, HOP = 1024, 256              # окно 46 мс: длиннее — удары «находятся» раньше, чем звучат
FPS = FS / HOP

LEN = {"Бочка": 0.55, "Малый": 0.5, "Хэт": 0.16, "Открытый хэт": 0.7, "808": 1.6}


def _prog(progress, f, s):
    if progress:
        try:
            progress(f, s)
        except Exception:                                # noqa: BLE001
            pass


def _stft_mag(x):
    from numpy.lib.stride_tricks import sliding_window_view
    pad = NF // 2
    xp = np.pad(x.astype(np.float32), (pad, pad))
    fr = sliding_window_view(xp, NF)[::HOP]
    win = np.hanning(NF).astype(np.float32)
    out = np.empty((len(fr), NF // 2 + 1), np.float32)
    for s in range(0, len(fr), 1024):
        out[s:s + 1024] = np.abs(np.fft.rfft(fr[s:s + 1024] * win, axis=1))
    return out


def _flux(E):
    """Рост энергии (лог) по кадрам — где начинаются удары."""
    L = np.log1p(E * 1e3)
    d = np.maximum(0.0, np.diff(L, prepend=L[:1]))
    return d


def _grid(an, step):
    """Шаги сетки (сек) с шагом step долей по долям трека."""
    b = np.asarray(an["beats"], float)
    if len(b) < 4:
        p = 60.0 / an["bpm"]
        b = np.arange(0, an["dur"], p)
    k = int(round(1 / step))
    out = []
    for i in range(len(b) - 1):
        out.extend(b[i] + (b[i + 1] - b[i]) * np.arange(k) / k)
    out.append(b[-1])
    return np.asarray(out)


def _pick_hits(fl, times, thr_rel=0.28, win=0.025):
    """Сила удара на каждом шаге сетки (макс. рост в окне) и где именно был пик."""
    fi = np.asarray(times * FPS, float)
    w = int(win * FPS) + 1
    n = len(fl)
    st, pk = np.zeros(len(times)), np.zeros(len(times), np.int64)
    for i, f in enumerate(fi):
        a, b = int(max(0, f - w)), int(min(n, f + w + 1))
        if b <= a:
            continue
        j = a + int(np.argmax(fl[a:b]))
        st[i], pk[i] = fl[j], j
    if not len(st) or st.max() <= 0:
        return st, pk, np.zeros(len(st), bool)
    top = np.percentile(st[st > 0], 97) if (st > 0).any() else st.max()
    med = np.median(st)
    on = st > max(med * 2.2, top * thr_rel)
    return st, pk, on


BANDS = {"Бочка": ("lp", 180), "Малый": ("bp", 2200), "Хэт": ("hp", 7000), "Открытый хэт": ("hp", 6000),
         "808": ("lp", 250)}


def _refine(xm, t, name):
    """Точная атака удара (сек) по форме волны в полосе этого звука: самый крутой рост огибающей
    в окне −70…+40 мс от найденного по спектру."""
    from scipy import signal as sps
    a = int(max(0, (t - 0.07) * SR))
    b = int(min(len(xm), (t + 0.04) * SR))
    if b - a < 256:
        return t
    kind, f = BANDS.get(name, ("hp", 20))
    y = sps.sosfilt(D.biquad(kind, f, 0.7), xm[max(0, a - 2048):b])[a - max(0, a - 2048):]
    k = int(0.002 * SR)
    env = np.convolve(np.abs(y), np.ones(k) / k, mode="same")
    d = np.diff(env, prepend=env[:1])
    i = int(np.argmax(d))
    # к началу подъёма: где огибающая ещё ниже 20 % от пика после атаки
    pk = env[i:i + int(0.02 * SR)].max() if i < len(env) else env.max()
    j = i
    while j > 0 and env[j] > pk * 0.2:
        j -= 1
    return (a + j) / SR


def _perc_seg(x, s, L, name):
    """Кусок [s, s+L) только с ударной частью: тянущиеся звуки (808, мелодия, пэды) прочь —
    медианные фильтры по своему окружению ±0.35 с."""
    from scipy import ndimage as ndi
    import daw_sep as SPm
    pad = int(0.35 * SR)
    a, b = s - pad, s + L + pad
    if s < 0 or s >= len(x) or L <= 0:
        return None
    # Retain first/last hits: pad analysis context instead of discarding them.
    seg = x[max(0,a):min(len(x),b)].astype(np.float64)
    seg = np.pad(seg, ((max(0,-a), max(0,b-len(x))), (0,0)))
    n_fft, hop = 2048, 256
    tl = 41 if name != "808" else 9                     # окно «тянущегося» ~240 мс (у 808 — наоборот, оставить его)
    spectra=[SPm._stft(seg[:,c],n_fft,hop) for c in range(seg.shape[1])]
    M=np.sqrt(sum(np.abs(X)**2 for X in spectra)/len(spectra))
    Hm=ndi.median_filter(M,size=(1,tl));Pm=ndi.median_filter(M,size=(17,1))
    mask=(Hm**2 if name=='808' else Pm**2)/(Hm**2+Pm**2+1e-12)
    # Shared stereo mask preserves L/R phase and avoids a wandering image.
    out=np.stack([SPm._istft(X*mask,n_fft,hop,len(seg)) for X in spectra],axis=1)
    return out[pad:pad + L].astype(np.float32)


def _avg_hit(x, starts, length, max_n=10, name=""):
    """Усреднить удары одного звука, выровняв по атаке (кросс-корреляция ±3 мс)."""
    L = int(length * SR)
    segs = []
    for s in list(dict.fromkeys(starts))[:max_n]:
        s = int(s)
        if s < 0 or s >= len(x):
            continue
        sg = _perc_seg(x, s, L, name) if name else np.pad(x[s:s + L], ((0,max(0,s+L-len(x))), (0,0)))
        if sg is not None:
            segs.append(sg.astype(np.float64))
    if not segs:
        return None
    if name in ('Хэт','Открытый хэт'):
        # Uncorrelated noise hits cancel when averaged; keep the best-ranked take.
        return segs[0].astype(np.float32)
    ref = segs[0].mean(axis=1)
    m = int(0.003 * SR)
    acc = segs[0].copy()
    cnt = 1
    for sg in segs[1:]:
        mono = sg.mean(axis=1)
        best, bd = -1e18, 0
        for d in range(-m, m + 1, 4):
            end=min(L,int(.08*SR))
            a = ref[max(0, d):end + min(0, d)]
            b = mono[max(0, -d):end - max(0, d)]
            v = float(np.dot(a,b)/(np.linalg.norm(a)*np.linalg.norm(b)+1e-12))
            if v > best:
                best, bd = v, d
        if best < .2:continue
        shifted = np.zeros_like(sg)
        if bd > 0: shifted[bd:] = sg[:-bd]
        elif bd < 0: shifted[:bd] = sg[-bd:]
        else: shifted[:] = sg
        acc += shifted
        cnt += 1
    return (acc / cnt).astype(np.float32)


def _steady(st, on, k, phase, keep_rel=0.45, strong=0.75):
    """Бит — петля: удар на месте такта, где в других тактах пусто, — скорее случайность (кусок
    мелодии, хвост). Оставляем места такта, где удары повторяются, и очень сильные удары."""
    if not on.any():
        return on
    per_bar = 4 * k
    slot = (np.arange(len(st)) - phase * k) % per_bar
    acc = np.zeros(per_bar)
    cnt = np.zeros(per_bar)
    for i in range(len(st)):
        acc[slot[i]] += 1.0 if on[i] else 0.0
        cnt[slot[i]] += 1
    rate = acc / np.maximum(1, cnt)
    good = rate >= keep_rel * rate.max()
    top = np.percentile(st[on], 90)
    return on & (good[slot] | (st > top * strong))


def _finish(s, fade_ms=20, hp=None, lp=None, drop_db=-26.0, min_len=0.03):
    """Нормировка, фильтры по виду звука, хвост без щелчка, тишина в начале убрана."""
    if s is None or not len(s):
        return None
    from scipy import signal as sps
    y = s.astype(np.float64)
    if hp:
        y = sps.sosfilt(D.biquad("hp", hp, 0.7), y, axis=0)
    if lp:
        y = sps.sosfilt(D.biquad("lp", lp, 0.7), y, axis=0)
    env = np.abs(y).max(axis=1)
    pk = env.max()
    if pk <= 1e-6:
        return None
    a = int(np.argmax(env > pk * 0.08))
    y = y[max(0, a - int(0.002 * SR)):]
    # конец — первый спад огибающей ниже drop_db от пика (дальше — следующие удары и фон бита)
    env = np.abs(y).max(axis=1)
    k = max(1, int(0.01 * SR))
    env = np.convolve(env, np.ones(k) / k, mode="same")
    ip = int(np.argmax(env))
    mn = int(min_len * SR)
    below = np.nonzero(env[ip + mn:] < env[ip] * 10 ** (drop_db / 20))[0]
    if len(below):
        y = y[:ip + mn + below[0] + int(0.01 * SR)]
        fade_ms = max(fade_ms, len(y) / SR * 1000 * 0.35)       # хвост мягко уходит в ноль
    k = min(len(y), int(fade_ms / 1000 * SR))
    if k > 0:
        y[-k:] *= np.linspace(1, 0, k)[:, None]
    y *= 0.89 / pk
    return y.astype(np.float32)


def safe(name):
    return re.sub(r'[\\/:*?"<>|]+', " ", name).strip()[:80] or "бит"


def split_beat(track: dict, progress=None, cancel=None) -> dict:
    """Трек → {'dir', 'files': {вид: путь}, 'project': проект студии, 'text': пояснение}."""
    import daw_mashup as M
    import daw_sep
    path = track["path"]
    title = M.track_title(track)
    _prog(progress, 0.0, "Слушаю темп и доли…")
    an = M.analyze(path, lambda f, s: _prog(progress, f * 0.1, s))
    if cancel is not None and cancel.is_set():
        raise RuntimeError("отменено")
    st = daw_sep.separate(path, lambda f, s: _prog(progress, 0.1 + f * 0.5, s), cancel, quality="cached")
    inst = st["inst"]
    del st
    if cancel is not None and cancel.is_set():
        raise RuntimeError("отменено")
    _prog(progress, 0.62, "Делю минус на удары и тянущиеся звуки…")
    from scipy import ndimage as ndi
    mono = D.resample(inst.mean(axis=1).astype(np.float32), FS / SR)
    S = _stft_mag(mono)                                   # кадры × бины
    Hm = ndi.median_filter(S, size=(17, 1))               # вдоль времени — тянущееся
    Pm = ndi.median_filter(S, size=(1, 17))               # вдоль частот — удары
    mask_p = Pm ** 2 / (Pm ** 2 + Hm ** 2 + 1e-12)
    P = S * mask_p
    Hh = S * (1 - mask_p)
    freqs = np.fft.rfftfreq(NF, 1 / FS)

    def band(X, lo, hi):
        sel = (freqs >= lo) & (freqs < hi)
        return (X[:, sel] ** 2).sum(axis=1)
    low_p, mid_p, high_p = band(P, 30, 160), band(P, 1200, 5000), band(P, 7000, 11000)
    body_p = band(P, 160, 450)
    high_s = band(S, 7000, 11000)
    low_h = band(Hh, 30, 140)
    fl_low, fl_mid, fl_high = _flux(low_p), _flux(mid_p), _flux(high_p)
    fl_body = _flux(body_p)
    fl_808 = _flux(low_h)
    _prog(progress, 0.72, "Ищу удары на сетке…")
    g16 = _grid(an, 0.25)
    g32 = _grid(an, 0.125)
    s_low, p_low, on_low = _pick_hits(fl_low, g16, 0.3)
    s_mid, p_mid, on_mid = _pick_hits(fl_mid, g16, 0.35)
    s_high, p_high, on_high = _pick_hits(fl_high, g32, 0.25)
    s_808, p_808, on_808 = _pick_hits(fl_808, g16, 0.3)
    ph = int(an.get("phase", 0))
    on_low = _steady(s_low, on_low, 4, ph)
    on_mid = _steady(s_mid, on_mid, 4, ph, 0.55)
    on_high = _steady(s_high, on_high, 8, ph, 0.35)
    # малый — широкополосный: вместе с «шумом» 1–5 кГц растёт и тело 160–450 Гц, а верх не главный
    # (иначе это хэт); и не одна только середина мелодии
    nb = len(fl_body)
    body_at = np.array([fl_body[max(0, p - 2):min(nb, p + 3)].max() for p in p_mid])
    hi_at = np.array([fl_high[max(0, p - 2):min(nb, p + 3)].max() for p in p_mid])
    on_mid = on_mid & (body_at > np.percentile(fl_body, 90) * 0.5) & (hi_at < s_mid * 2.5)
    # открытый хэт: верх звучит заметно дольше, чем у обычных хэтов этого бита
    sus = int(0.15 * FPS)
    ratio = np.array([high_s[min(len(high_s) - 1, p + sus)] / max(1e-12, high_s[p]) for p in p_high])
    typ = float(np.median(ratio[on_high])) if on_high.any() else 0.2
    hi_open = (ratio > max(0.35, typ * 2.2))
    # 808: низ тянется и тональный
    lo_sus = np.array([low_h[min(len(low_h) - 1, p + int(0.3 * FPS))] / max(1e-12, low_h[min(len(low_h) - 1, p + 2)])
                       for p in p_808]) > 0.3
    on_808 = on_808 & lo_sus
    # чистота удара: своя полоса громче чужих в окне
    def clean(st_own, pk, others):
        sc = []
        for v, p in zip(st_own, pk):
            o = max(float(fl[max(0, p - 2):p + 3].max()) if len(fl) else 0 for fl in others)
            sc.append(v - 0.8 * o)
        return np.asarray(sc)
    files = {}
    x44 = inst
    xm44 = inst.mean(axis=1)
    outdir = PR.STUDIO / "beat_kits" / safe(title)
    outdir.mkdir(parents=True, exist_ok=True)
    kinds = []
    _prog(progress, 0.8, "Вырезаю чистые звуки…")

    def take(name, on, st_own, pk, others, lp=None, hp=None, extra=None):
        if cancel is not None and cancel.is_set():raise RuntimeError('отменено')
        idx = np.nonzero(on if extra is None else on & extra)[0]
        if len(idx) < 2:
            return
        cl = clean(st_own[idx], pk[idx], others)
        order = idx[np.argsort(-cl)]
        starts = [max(0,int(_refine(xm44, pk[i] * HOP / FS, name) * SR) - int(0.003 * SR)) for i in order[:12]]
        s = _avg_hit(x44, starts, LEN[name], 10 if name != "Открытый хэт" else 6, name)
        s = _finish(s, 30 if name in ("Хэт",) else 60, hp=hp, lp=lp,
                    drop_db={"Бочка": -15, "Малый": -16, "Хэт": -10, "Открытый хэт": -14}.get(name, -16),
                    min_len={"Бочка": 0.14, "Малый": 0.09, "Хэт": 0.03, "Открытый хэт": 0.15}.get(name, 0.05))
        if s is None:
            return
        f = outdir / f"{name}.wav"
        D.write_wav(f, s)
        files[name] = str(f)
        kinds.append(name)
    take("Бочка", on_low & ~on_808 if on_808.sum() < on_low.sum() * 0.8 else on_low, s_low, p_low, [fl_mid, fl_high], lp=9000)
    take("Малый", on_mid, s_mid, p_mid, [fl_low, fl_808], hp=140)
    take("Хэт", on_high & ~hi_open, s_high, p_high, [fl_low, fl_mid], hp=4500)
    take("Открытый хэт", on_high & hi_open, s_high, p_high, [fl_low, fl_mid], hp=3500)
    # 808: самая чистая длинная нота, корневая нота по высоте
    notes_808 = []
    idx = np.nonzero(on_808)[0]
    root = None
    if len(idx) >= 2:
        cl = clean(s_808[idx], p_808[idx], [fl_mid, fl_high])
        best = idx[int(np.argmax(cl))]
        s0 = int(_refine(xm44, p_808[best] * HOP / FS, "808") * SR) - int(0.003 * SR)
        seg = _perc_seg(x44, max(0, s0), int(LEN["808"] * SR), "808")
        if seg is None:
            seg = x44[max(0, s0):s0 + int(LEN["808"] * SR)]
        s808 = _finish(seg, 120, lp=1800, drop_db=-30, min_len=0.25)
        if s808 is not None:
            f = outdir / "808.wav"
            D.write_wav(f, s808)
            files["808"] = str(f)
            kinds.append("808")
            import daw_samples as SM
            root = SM.root_note(str(f))
    if not kinds:
        raise RuntimeError("не нашёл ударных — это точно бит, а не голос или тишина?")
    # мелодия без ударных: тянущаяся часть минуса, 4 такта с самого мощного места
    bars = M.bar_times(an)
    secs = sorted(an.get("sections", []), key=lambda s: -float(s.get("int", 0)))
    t0 = float(secs[0]["s"]) if secs else float(bars[min(len(bars) - 1, 8)])
    bi = int(np.argmin(np.abs(bars - t0)))
    bi = min(bi, max(0, len(bars) - 6))
    w0, w1 = float(bars[bi]), float(bars[min(len(bars) - 1, bi + 4)])
    _prog(progress, 0.88, "Достаю мелодию без ударных…")
    try:
        mel = _melody(x44, w0, w1)
        if mel is not None:
            f = outdir / "Мелодия (4 такта, без ударных).wav"
            D.write_wav(f, mel)
            files["Мелодия"] = str(f)
    except Exception as e:                               # noqa: BLE001
        print("[studio split] melody:", repr(e))
    # паттерн: удары внутри окна 4 тактов
    _prog(progress, 0.94, "Расставляю ноты как в оригинале…")
    bpm = float(an["bpm"])
    beats = np.asarray(an["beats"], float)

    def beat_of(t):
        return float(np.interp(t, beats, np.arange(len(beats)))) if len(beats) > 1 else t * bpm / 60

    b0 = beat_of(w0)
    pat_notes = {}

    def put(name, on, pk, step, pitch_fn=None, length=0.25):
        if name not in files:
            return
        ns = []
        hits = [(beat_of(_refine(xm44, p * HOP / FS, name)) - b0) for p in pk[np.nonzero(on)[0]]
                if w0 - 0.2 <= p * HOP / FS <= w1 + 0.2]
        hits = sorted({round(round(h / step) * step, 4) for h in hits if -step / 2 <= h < 16 - step / 2})
        for i, h in enumerate(hits):
            if h < 0:
                h = 0.0
            nxt = hits[i + 1] if i + 1 < len(hits) else 16.0
            ln = length if pitch_fn is None else max(step, nxt - h)
            pitch = pitch_fn(h) if pitch_fn else 60
            ns.append([h, round(ln, 4), int(pitch), 0.8])
        pat_notes[name] = ns
    put("Бочка", on_low, p_low, 0.25)
    put("Малый", on_mid, p_mid, 0.25)
    put("Хэт", on_high & ~hi_open, p_high, 0.125, length=0.125)
    put("Открытый хэт", on_high & hi_open, p_high, 0.125, length=0.5)
    if "808" in files:
        def p808(h):
            t = float(np.interp(b0 + h, np.arange(len(beats)), beats)) if len(beats) > 1 else h * 60 / bpm
            a = int(t * SR) + int(0.04 * SR)
            seg = x44[a:a + 8192].mean(axis=1)
            if len(seg) < 4096:
                return root or 36
            from scipy import signal as sps
            seg = sps.sosfilt(D.biquad("lp", 400, 0.7), seg)
            f0, conf = D.yin(seg[:8192], SR, 28.0, 400.0, 0.2)
            if f0 > 0 and conf > 0.5:
                m = int(round(D.hz_to_midi(f0)))
                while m > 52:
                    m -= 12
                while m < 24:
                    m += 12
                return m
            return root or 36
        put("808", on_808, p_808, 0.25, p808)
    proj = _project(title, bpm, files, pat_notes, root)
    lines = [f"Разобрал «{title}» ({bpm:.1f} BPM) на звуки: " + ", ".join(k for k in files) + "."]
    if root is not None:
        lines.append(f"808 звучит на ноте {D.note_name(root)} — в пианоролле ставь ноты от неё.")
    lines.append("Файлы — в браузере: «Мои сэмплы → Разобранные биты». Проект-ремейк открыт: паттерн "
                 "«Бит» — 4 такта с самого мощного места, ноты стоят как в оригинале, мелодия — аудиоклипом.")
    return {"dir": str(outdir), "files": files, "project": proj, "text": "\n".join(lines)}


def _melody(x44, w0, w1):
    """Тянущаяся часть минуса (без ударных) на отрезке [w0, w1) — HPSS на 44.1 кГц."""
    from scipy import ndimage as ndi
    a, b = int(w0 * SR), int(w1 * SR)
    seg = x44[max(0, a):b]
    if len(seg) < SR:
        return None
    n_fft, hop = 4096, 1024
    out = np.zeros_like(seg, dtype=np.float32)
    import daw_sep as SP
    for c in range(2):
        X = SP._stft(seg[:, c].astype(np.float64), n_fft, hop)
        M = np.abs(X)
        Hm = ndi.median_filter(M, size=(1, 31))
        Pm = ndi.median_filter(M, size=(31, 1))
        mask = Hm ** 2 / (Hm ** 2 + Pm ** 2 + 1e-12)
        out[:, c] = SP._istft(X * mask, n_fft, hop, len(seg))
    k = int(0.01 * SR)
    out[:k] *= np.linspace(0, 1, k)[:, None]
    out[-k:] *= np.linspace(1, 0, k)[:, None]
    pk = np.abs(out).max()
    return out * (0.8 / pk) if pk > 1e-6 else None


def _project(title, bpm, files, pat_notes, root):
    import daw_fx as FX
    p = PR.new_project(f"Ремейк: {title}"[:120], template="none")
    p["bpm"] = round(bpm, 3)
    pat = PR.new_pattern("Бит", 16.0)
    cols = {"Бочка": "#d8a26c", "Малый": "#d86c8f", "Хэт": "#d8d36c", "Открытый хэт": "#c0b84a", "808": "#6c9ad8"}
    ins = 1
    for name in ("Бочка", "808", "Малый", "Хэт", "Открытый хэт"):
        if name not in files:
            continue
        prm = {"sample": files[name], "keytrack": 1 if name == "808" else 0}
        if name == "808":
            prm.update({"root": int(root or 36), "hold": 0, "rel": 0.06})
        c = PR.new_channel(name, "sampler", prm, insert=ins, color=cols.get(name))
        p["channels"].append(c)
        p["mixer"][ins]["name"] = name
        ins += 1
        if pat_notes.get(name):
            pat["notes"][c["id"]] = pat_notes[name]
    p["patterns"] = [pat]
    p["sel_pattern"] = pat["id"]
    p["clips"].append({"id": PR.uid(), "kind": "pat", "ref": pat["id"], "lane": 0, "start": 0.0, "len": 16.0})
    if "Мелодия" in files:
        ch = PR.new_channel("Мелодия", "audio", insert=ins, color="#8fc46c")
        p["channels"].append(ch)
        p["mixer"][ins]["name"] = "Мелодия"
        src = PR.new_source("Мелодия без ударных", files["Мелодия"], "mix")
        p["sources"].append(src)
        p["clips"].append({"id": PR.uid(), "kind": "audio", "ref": src["id"], "chan": ch["id"], "lane": 1,
                           "start": 0.0, "len": 16.0, "off": 0.0, "gain": 1.0, "fin": 0.0, "fout": 0.0})
    p["mixer"][0]["fx"][0] = FX.new_slot("limiter", {"gain": 1.0, "ceil": -0.5})
    p["mode"] = "song"
    if p["channels"]:
        p["sel_channel"] = p["channels"][0]["id"]
    return p
