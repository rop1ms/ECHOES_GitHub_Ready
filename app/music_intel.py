# music_intel.py
"""
Жанры и настроение треков — для «Настроения дня».

Два источника знаний о треке:

1. Звук (офлайн, ffmpeg + numpy). Из ~75 секунд в разных частях трека считаются:
     громкость и динамика, «яркость» (спектральный центроид), плотность
     атак (spectral flux), темп и чёткость ритма (автокорреляция огибающей
     атак), бас, высокие, шумность, тональность и лад (мажор/минор по
     хрома-профилю Крумхансла).
   Из них выводятся energy / valence (позитивность) / danceability /
   acousticness. Абсолютные значения потом переводятся в ранги внутри
   библиотеки — так шкала подстраивается под конкретную коллекцию.

2. Сеть (жанры и теги). Исполнитель и название берутся через
   track_identity (понимает «Title - Artist - SoundLoadMate», артиста в
   названии и т.д.), найденное проверяется по названию/артисту/длительности:
     Deezer (жанры альбома), iTunes (primaryGenreName),
     MusicBrainz (запись, релиз и отдельно артист), Last.fm (раздельные теги трека и исполнителя,
     включая настроение: sad, chill, aggressive… — если указан ключ).
   Жанры сводятся к семействам (metal, hyperpop, lofi, rap…) с априорными
   energy/valence, которые смешиваются со звуковыми.

Всё кэшируется в ~/Echoes/track_intel.json. Анализ идёт в фоне
(IntelWorker), с пониженным приоритетом ffmpeg и паузами, чтобы не мешать
воспроизведению.
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import threading
import time
from pathlib import Path

import numpy as np
import requests

from track_identity import parse_candidates, match_score, norm, primary_artist

INTEL_VERSION = 2
META_VERSION = 3          # Last.fm: теги трека и исполнителя больше не смешиваются
UA = "NeonPlayer/5.0 ( neon-player-music-intel )"
SR = 22050
N_FFT = 2048
HOP = 512


def _log(msg):
    try:
        print(f"[intel] {msg}")
    except Exception:
        pass


# ------------------------------------------------------------------ #
#  Семейства жанров: (regex, имя, energy, valence, dance, acoustic)   #
# ------------------------------------------------------------------ #

GENRE_FAMILIES = [
    (r"metalcore|deathcore|hardcore|grindcore|mathcore|post-?hardcore|screamo", "metalcore", .92, .28, .35, .05),
    (r"\bmetal|метал|djent|thrash|nu[- ]?metal", "metal", .88, .30, .35, .05),
    (r"phonk|фонк", "phonk", .82, .35, .78, .05),
    (r"breakcore|drum ?(?:and|&|n|'n'?) ?bass|\bdnb\b|jungle", "dnb", .90, .45, .55, .05),
    (r"dubstep|riddim|brostep|trap edm|bass music", "dubstep", .88, .40, .60, .05),
    (r"hyperpop|digicore|glitchcore|krushclub|sigilkore|krushcore", "hyperpop", .80, .55, .70, .05),
    (r"emo rap|sad ?rap|cloud ?rap|клауд|pluggnb|\bplugg\b", "cloudrap", .45, .30, .62, .10),
    (r"\bdrill\b", "drill", .74, .30, .70, .05),
    (r"\btrap\b|трэп|треп|\brage(?: rap| trap)?\b|\bjerk(?: rap)?\b", "trap", .70, .40, .74, .05),
    (r"hip[- ]?hop|\brap\b|рэп|реп|хип[- ]?хоп|grime|boom ?bap", "hiphop", .64, .50, .76, .10),
    (r"\bemo\b|эмо|midwest", "emo", .62, .28, .40, .15),
    (r"(?<!post-)(?<!post )(?<!post)punk|панк|\bska\b", "punk", .85, .58, .50, .05),
    (r"shoegaze|dream ?pop|slowcore", "dreampop", .42, .40, .40, .25),
    (r"synthwave|retrowave|outrun|darksynth|vaporwave|chillwave|future funk", "synthwave", .58, .55, .62, .05),
    (r"lo-?fi|chillhop|chill ?out|downtempo|trip[- ]?hop", "lofi", .30, .50, .62, .35),
    (r"ambient|эмбиент|drone|new age", "ambient", .12, .40, .15, .45),
    (r"\bhouse\b|хаус|disco|nu[- ]disco|eurodance|dance|edm|electro ?pop|\bclub\b|танц|hardstyle|gabber|speedcore|hard dance", "dance", .78, .72, .85, .05),
    (r"techno|техно|trance|транс|hardgroove|idm|electronica|electronic|электрон|synth|8-?bit|chiptune", "electronic", .70, .52, .70, .05),
    (r"funk|фанк|groove|boogie", "funk", .74, .80, .85, .20),
    (r"k-?pop|j-?pop|j-?rock|anime|vocaloid|аниме|city ?pop|c-?pop|mandopop|cantopop", "jpop", .72, .70, .65, .10),
    (r"r&b|rnb|r'n'b|soul|neo[- ]soul|соул|ритм-?н-?блюз", "rnb", .45, .55, .68, .30),
    (r"jazz|джаз|swing|bossa|blues|блюз", "jazz", .38, .60, .50, .70),
    (r"classical|классик|orchestra|оркестр|baroque|neoclassical", "classical", .25, .45, .20, .85),
    (r"soundtrack|\bost\b|score|game|video game|саундтрек|film|cinematic", "soundtrack", .55, .45, .35, .30),
    (r"folk|фолк|acoustic|акуст|singer[- ]songwriter|bard|бард|country|кантри|americana", "folk", .32, .52, .35, .85),
    (r"reggae|регги|dancehall|afro|latin|латин|reggaeton|salsa|samba|cumbia", "latin", .72, .80, .85, .20),
    (r"indie|инди|alternative|alt[- ]rock|альтернатив|vaihtoehto|post[- ]?punk|grunge|гранж|new wave", "indie", .56, .46, .48, .25),
    (r"\brock\b|рок|hard rock", "rock", .76, .50, .45, .12),
    (r"\bpop\b|поп|эстрад|schlager|chanson|шансон", "pop", .62, .66, .70, .15),
]
_GENRE_RX = [(re.compile(rx, re.I), name, e, v, d, a) for rx, name, e, v, d, a in GENRE_FAMILIES]

# Теги настроения (Last.fm, MusicBrainz): (regex, d_energy, d_valence, вес)
MOOD_TAGS = [
    (r"\bsad\b|грус|melanchol|depress|heartbreak|lonely|crying|тоск|печал|sorrow|suicid", -.10, -.35),
    (r"\bdark\b|мрач|gloomy|haunting|sinister|horror|evil|occult", .00, -.28),
    (r"\bhappy\b|весел|joy|cheerful|uplifting|feel ?good|sunny|summer|fun\b", .08, .32),
    (r"\bchill\b|relax|calm|mellow|спокой|smooth|soft|peaceful|lazy|sleep", -.28, .05),
    (r"aggressive|агресс|angry|rage|brutal|heavy|intense|злой|violent", .30, -.20),
    (r"energetic|энерг|upbeat|party|вечерин|hype\b|banger|workout|gym|fast", .28, .15),
    (r"romantic|романт|love|любов|sensual|sexy", -.05, .12),
    (r"dreamy|atmospheric|ethereal|мечт|nostalg|ностальг", -.12, .00),
    (r"epic|эпич|triumphant|anthem", .18, .10),
]
_MOOD_RX = [(re.compile(rx, re.I), de, dv) for rx, de, dv in MOOD_TAGS]

GENRE_RU = {
    "metalcore": "металкор", "metal": "метал", "phonk": "фонк", "dnb": "драм-н-бейс/брейккор",
    "dubstep": "дабстеп", "hyperpop": "гиперпоп/диджикор", "cloudrap": "клауд/эмо-рэп",
    "drill": "дрилл", "trap": "трэп", "hiphop": "хип-хоп", "emo": "эмо", "punk": "панк",
    "dreampop": "дрим-поп", "synthwave": "синтвейв", "lofi": "lo-fi", "ambient": "эмбиент",
    "dance": "танцевальная", "electronic": "электроника", "funk": "фанк", "jpop": "J-pop/аниме",
    "rnb": "R&B/соул", "jazz": "джаз", "classical": "классика", "soundtrack": "саундтреки",
    "folk": "акустика/фолк", "latin": "латино/регги", "indie": "инди/альтернатива",
    "rock": "рок", "pop": "поп",
}
_FAMILY = {name: (e, v, d, a) for _, name, e, v, d, a in _GENRE_RX}
_JUNK_GENRES = {"", "music", "other", "unknown", "genre", "misc", "none", "various", "soundcloud",
                "youtube", "seen live", "favorites", "favourite", "spotify", "my music"}


def genre_families(names) -> list[str]:
    """Сырые жанры/теги → семейства (по убыванию значимости, без повторов)."""
    out = []
    for raw in names or []:
        s = str(raw or "").strip().lower()
        if s in _JUNK_GENRES or len(s) < 2:
            continue
        # Compound tags describe several genres; keep that information. A slash
        # in R&B/Soul or Hip-Hop/Rap is a separator, not a new made-up genre.
        parts = re.split(r"\s*(?:/|;|,|\|)\s*", s)
        for part in parts:
            for rx, name, *_ in _GENRE_RX:
                if rx.search(part):
                    if name not in out:
                        out.append(name)
                    break
    return out


def mood_shift(tags) -> tuple[float, float, list[str]]:
    """Сдвиг (energy, valence) по тегам настроения + какие сработали."""
    de = dv = 0.0
    hits = []
    for t in tags or []:
        s = str(t).lower()
        for rx, e, v in _MOOD_RX:
            if rx.search(s):
                de += e
                dv += v
                hits.append(s)
                break
    k = 1.0 / max(1.0, len(hits) ** 0.5)
    return max(-.4, min(.4, de * k)), max(-.45, min(.45, dv * k)), hits


# ------------------------------------------------------------------ #
#  Анализ звука                                                       #
# ------------------------------------------------------------------ #

_KK_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_KK_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
_KEYS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _ffmpeg_flags():
    if os.name == "nt":
        # без окна консоли и с пониженным приоритетом — не мешать воспроизведению
        return subprocess.CREATE_NO_WINDOW | 0x00004000      # BELOW_NORMAL_PRIORITY_CLASS
    return 0


def decode_excerpt(path: str, duration: float = 0.0, seconds: float = 75.0, start=None) -> np.ndarray | None:
    if start is None:
        start = max(0.0, min(duration * .32, duration - seconds - 5)) if duration and duration > seconds + 20 else 0.
    start = max(0., float(start))
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error"]
    if start > 0:
        cmd += ["-ss", f"{start:.2f}"]
    cmd += ["-t", f"{seconds:.1f}", "-i", path, "-vn", "-ac", "1", "-ar", str(SR),
            "-f", "f32le", "-acodec", "pcm_f32le", "-"]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=60, creationflags=_ffmpeg_flags())
    except Exception as e:
        _log(f"ffmpeg: {e!r}")
        return None
    if p.returncode != 0 or not p.stdout:
        return None
    x = np.frombuffer(p.stdout, dtype=np.float32)
    if x.size < SR * 5:
        return None
    return x.astype(np.float32, copy=True)


def _stft_mag(x: np.ndarray) -> np.ndarray:
    n = 1 + (x.size - N_FFT) // HOP
    if n <= 4:
        return np.zeros((0, N_FFT // 2 + 1), np.float32)
    idx = np.lib.stride_tricks.sliding_window_view(x, N_FFT)[::HOP][:n]
    win = np.hanning(N_FFT).astype(np.float32)
    out = np.empty((n, N_FFT // 2 + 1), np.float32)
    for i in range(0, n, 256):                         # блоками — меньше пик памяти
        out[i:i + 256] = np.abs(np.fft.rfft(idx[i:i + 256] * win, axis=1))
    return out


def _tempo(onset: np.ndarray):
    fps = SR / HOP
    o = onset - onset.mean()
    if not np.any(o):
        return 0.0, 0.0
    nfft = 1 << int(math.ceil(math.log2(o.size * 2)))
    f = np.fft.rfft(o, nfft)
    ac = np.fft.irfft(f * np.conj(f), nfft)[: o.size]
    ac /= ac[0] + 1e-9
    idx = np.arange(ac.size, dtype=float)

    def at(lag):
        return np.interp(lag, idx, ac, right=0.0)

    bpm = np.arange(60.0, 200.5, 0.5)
    lag = fps * 60.0 / bpm
    # гребёнка: сам период + кратные + половина (доли) — так «полтора удара»
    # от хэтов на восьмых не выигрывает у настоящего темпа
    comb = at(lag) + 0.5 * at(2 * lag) + 0.33 * at(3 * lag) + 0.35 * at(lag / 2)
    prior = np.exp(-0.5 * (np.log2(bpm / 120.0) / 0.9) ** 2)
    i = int(np.argmax(comb * prior))
    if bpm[i] <= 0 or lag[i] >= ac.size:
        return 0.0, 0.0
    return float(bpm[i]), float(max(0.0, at(lag[i])))


def analyze_signal(x: np.ndarray) -> dict:
    """Сырые признаки звука (не зависят от библиотеки)."""
    x = x - float(np.mean(x))
    peak = float(np.max(np.abs(x))) + 1e-9
    S = _stft_mag(x)
    if S.shape[0] < 20:
        return {}
    freqs = np.fft.rfftfreq(N_FFT, 1.0 / SR)
    power = S ** 2
    tot = power.sum(axis=1) + 1e-12

    frame_rms = np.sqrt(np.mean(np.lib.stride_tricks.sliding_window_view(x, N_FFT)[::HOP][:S.shape[0]] ** 2, axis=1))
    rms_db = 20 * np.log10(frame_rms + 1e-7)
    active = rms_db > (np.max(rms_db) - 45)
    loud_db = float(10 * np.log10(np.mean(frame_rms[active] ** 2) + 1e-12)) if active.any() else -60.0
    dyn_db = float(np.percentile(rms_db[active], 95) - np.percentile(rms_db[active], 10)) if active.any() else 0.0
    crest_db = float(20 * np.log10(peak / (np.sqrt(np.mean(x ** 2)) + 1e-9)))

    centroid = float(np.sum((power * freqs).sum(axis=1)[active] / tot[active]) / max(1, active.sum()))
    low = float(np.mean(power[:, freqs < 150].sum(axis=1)[active] / tot[active]))
    high = float(np.mean(power[:, freqs > 4000].sum(axis=1)[active] / tot[active]))
    logS = np.log(S + 1e-6)
    flat = float(np.mean(np.exp(logS.mean(axis=1)) / (S.mean(axis=1) + 1e-9)))

    # атаки: положительная разность лог-спектра (в полосах до 8 кГц)
    band = freqs < 8000
    L = np.log1p(100.0 * S[:, band] / (S[:, band].max() + 1e-9))
    flux = np.maximum(0.0, np.diff(L, axis=0)).sum(axis=1)
    flux = np.concatenate([[0.0], flux])
    flux_n = flux / (np.mean(flux) + 1e-9)
    onset_density = float(np.mean(flux_n > 1.6))
    flux_level = float(np.mean(flux) / band.sum())
    bpm, pulse = _tempo(flux)

    # хрома и лад
    sel = (freqs >= 55) & (freqs <= 4200)
    pcs = (np.round(12 * np.log2(freqs[sel] / 440.0)) + 9) % 12
    chroma = np.zeros(12)
    spec_mean = np.mean(S[active][:, sel] if active.any() else S[:, sel], axis=0)
    np.add.at(chroma, pcs.astype(int), spec_mean)
    chroma = chroma / (chroma.sum() + 1e-9)
    best = (-2.0, 0, "major")
    corr_maj, corr_min = [], []
    for k in range(12):
        cm = np.corrcoef(chroma, np.roll(_KK_MAJOR, k))[0, 1]
        cn = np.corrcoef(chroma, np.roll(_KK_MINOR, k))[0, 1]
        corr_maj.append(cm)
        corr_min.append(cn)
        if cm > best[0]:
            best = (cm, k, "major")
        if cn > best[0]:
            best = (cn, k, "minor")
    mode_score = float(np.max(corr_maj) - np.max(corr_min))            # >0 — мажорнее
    tonal = float(max(best[0], 0.0))

    return {
        "loud_db": round(loud_db, 2), "dyn_db": round(dyn_db, 2), "crest_db": round(crest_db, 2),
        "centroid": round(centroid, 1), "low": round(low, 4), "high": round(high, 4),
        "flat": round(flat, 4), "flux": round(flux_level, 5), "onsets": round(onset_density, 4),
        "bpm": round(bpm, 1), "pulse": round(pulse, 4),
        "key": _KEYS[best[1]], "mode": best[2], "mode_score": round(mode_score, 4),
        "tonal": round(tonal, 4),
    }


def _sig(x, lo, hi):
    return float(min(1.0, max(0.0, (x - lo) / (hi - lo))))


def audio_mood(f: dict) -> dict:
    """Абсолютные (до ранжирования) energy/valence/dance/acoustic 0..1."""
    if not f:
        return {}
    loud = _sig(f["loud_db"], -26, -7)
    bright = _sig(f["centroid"], 900, 3600)
    flux = _sig(f["flux"], 0.004, 0.03)
    dens = _sig(f["onsets"], 0.05, 0.22)
    high = _sig(f["high"], 0.01, 0.12)
    noisy = _sig(f["flat"], 0.05, 0.35)
    tempo = _sig(f["bpm"], 70, 165)
    pulse = _sig(f["pulse"], 0.08, 0.45)
    calm_dyn = _sig(f["dyn_db"], 6, 22)                     # большая динамика = «живой»/тихий
    tempo = tempo * (0.3 + 0.7 * flux)          # темп «пэдов» без атак мало значит

    energy = (0.30 * loud + 0.20 * flux + 0.14 * dens + 0.12 * bright + 0.12 * high
              + 0.07 * tempo + 0.05 * noisy) - 0.08 * calm_dyn
    major = _sig(f["mode_score"], -0.12, 0.12)
    valence = (0.42 * major + 0.20 * bright + 0.16 * tempo + 0.12 * pulse
               - 0.14 * noisy - 0.06 * _sig(f["low"], 0.25, 0.6)) + 0.12
    groove = max(0.0, 1 - abs(f["bpm"] - 118) / 60) if f["bpm"] else 0.0
    dance = (0.55 * pulse + 0.25 * groove + 0.2 * _sig(f["low"], 0.08, 0.4)) * (0.3 + 0.7 * flux)
    acoustic = 1.0 - (0.35 * flux + 0.25 * noisy + 0.2 * high + 0.2 * loud)
    clip = lambda v: round(float(min(1.0, max(0.0, v))), 4)
    return {"energy": clip(energy), "valence": clip(valence), "dance": clip(dance),
            "acoustic": clip(acoustic)}


def analyze_file(path: str, duration: float = 0.0) -> dict | None:
    x = decode_excerpt(path, duration)
    if x is None:
        return None
    f = analyze_signal(x)
    if not f:
        return None
    f.update(audio_mood(f))
    return f


# ------------------------------------------------------------------ #
#  Сеть: жанры и теги                                                 #
# ------------------------------------------------------------------ #

class _Throttle:
    def __init__(self, interval):
        self.interval = interval
        self.t = 0.0
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            now = time.monotonic()
            d = self.t + self.interval - now
            if d > 0:
                time.sleep(d)
            self.t = time.monotonic()


_TH = {"deezer": _Throttle(0.15), "itunes": _Throttle(3.2), "mb": _Throttle(1.1), "lastfm": _Throttle(0.25)}


def _jget(kind, url, params=None, timeout=8):
    _TH[kind].wait()
    try:
        r = requests.get(url, params=params, timeout=timeout,
                         headers={"User-Agent": UA, "Accept": "application/json"})
        if r.status_code == 200:
            return r.json()
        if r.status_code in (403, 429, 503):
            _TH[kind].interval = min(30.0, _TH[kind].interval * 2)
    except Exception as e:
        _log(f"{kind}: {e.__class__.__name__}")
    return None


_album_genres_cache: dict = {}


def _genre_match_score(cands, artist, title, found_duration=0, duration=0):
    """Matching duration must not rescue the wrong artist or a different song."""
    variant = lambda text: set(re.findall(r"\b(?:remix|bootleg|mashup|cover|live|acoustic|instrumental)\b", str(text).casefold()))
    if cands and variant(cands[0][1]) != variant(title):
        return 0.0
    identity = match_score(cands, artist, title)
    if identity < .80:
        return 0.0
    return match_score(cands, artist, title, found_duration, duration)


def lookup_deezer(cands, duration):
    best = None
    for a, t in cands[:3]:
        q = f'artist:"{a}" track:"{t}"' if a else t
        j = _jget("deezer", "https://api.deezer.com/search", {"q": q, "limit": 8})
        data = (j or {}).get("data") or []
        if not data and a:
            j = _jget("deezer", "https://api.deezer.com/search", {"q": f"{a} {t}", "limit": 8})
            data = (j or {}).get("data") or []
        for it in data:
            sc = _genre_match_score(cands, (it.get("artist") or {}).get("name", ""), it.get("title", ""),
                             float(it.get("duration") or 0), duration)
            if not best or sc > best[0]:
                best = (sc, it)
        if best and best[0] >= 0.95:
            break
    if not best or best[0] < 0.72:
        return None
    sc, it = best
    alb = (it.get("album") or {}).get("id")
    genres = []
    if alb:
        if alb in _album_genres_cache:
            genres = _album_genres_cache[alb]
        else:
            j = _jget("deezer", f"https://api.deezer.com/album/{alb}")
            genres = [g.get("name", "") for g in ((j or {}).get("genres") or {}).get("data", [])]
            _album_genres_cache[alb] = genres
    return {"genres": genres, "bpm": 0, "score": round(sc, 3),
            "artist": (it.get("artist") or {}).get("name", ""), "title": it.get("title", ""),
            "explicit": bool(it.get("explicit_lyrics"))}


def lookup_itunes(cands, duration):
    a, t = cands[0]
    j = _jget("itunes", "https://itunes.apple.com/search",
              {"term": f"{a} {t}".strip(), "entity": "song", "limit": 8, "media": "music"})
    best = None
    for it in (j or {}).get("results") or []:
        sc = _genre_match_score(cands, it.get("artistName", ""), it.get("trackName", ""),
                         float(it.get("trackTimeMillis") or 0) / 1000, duration)
        if not best or sc > best[0]:
            best = (sc, it)
    if not best or best[0] < 0.72:
        return None
    return {"genres": [best[1].get("primaryGenreName", "")], "score": round(best[0], 3)}


def lookup_musicbrainz_artist(artist):
    if not artist:
        return None
    j = _jget("mb", "https://musicbrainz.org/ws/2/artist/",
              {"query": f'artist:"{artist}"', "fmt": "json", "limit": 3})
    for it in (j or {}).get("artists") or []:
        names = [it.get("name", "")] + [x.get("name", "") for x in it.get("aliases") or []]
        from track_identity import similarity
        if max(similarity(artist, n) for n in names if n) < 0.9 or int(it.get("score", 0)) < 85:
            continue
        tags = sorted(it.get("tags") or [], key=lambda x: -int(x.get("count", 0)))
        return {"tags": [x.get("name", "") for x in tags[:12]], "country": it.get("country", "")}
    return None


def lookup_musicbrainz_recording(cands, duration):
    """Жанры КОНКРЕТНОЙ записи и её релиза (голоса сообщества MusicBrainz).
    → {"rec": ([жанры], [голоса]), "rg": ([жанры], [голоса]), "score": ..} или None."""
    a, t = cands[0] if cands else ("", "")
    if not t:
        return None
    q = f'recording:"{t}"' + (f' AND artist:"{a}"' if a else "")
    j = _jget("mb", "https://musicbrainz.org/ws/2/recording/", {"query": q, "fmt": "json", "limit": 6})
    best = None
    for it in (j or {}).get("recordings") or []:
        artist = " ".join(((c.get("name") or (c.get("artist") or {}).get("name", "")) + (c.get("joinphrase") or ""))
                          for c in it.get("artist-credit") or [])
        sc = _genre_match_score(cands, artist, it.get("title", ""), float(it.get("length") or 0) / 1000, duration)
        if not best or sc > best[0]:
            best = (sc, it)
    if not best or best[0] < 0.8:
        return None
    sc, it = best

    def gl(obj):
        items = sorted((obj or {}).get("genres") or [], key=lambda x: -int(x.get("count", 0)))
        if not items:                                   # жанров нет — берём теги
            items = sorted((obj or {}).get("tags") or [], key=lambda x: -int(x.get("count", 0)))
        items = [x for x in items if int(x.get("count", 0)) > 0][:8]
        return [x.get("name", "") for x in items], [int(x.get("count", 0)) for x in items]

    rec = _jget("mb", f"https://musicbrainz.org/ws/2/recording/{it['id']}",
                {"inc": "genres+tags+releases+release-groups", "fmt": "json"})
    if rec is None:
        rec = _jget("mb", f"https://musicbrainz.org/ws/2/recording/{it['id']}",
                    {"inc": "genres+tags", "fmt": "json"})
    out = {"rec": gl(rec), "rg": ([], []), "score": round(sc, 3)}
    rg_id = None
    for rel in (rec or {}).get("releases") or []:
        rg = rel.get("release-group") or {}
        if rg.get("id"):
            rg_id = rg["id"]
            if (rg.get("primary-type") or "").lower() in ("album", "ep", "single"):
                break
    if rg_id:
        out["rg"] = gl(_jget("mb", f"https://musicbrainz.org/ws/2/release-group/{rg_id}",
                             {"inc": "genres+tags", "fmt": "json"}))
    if not out["rec"][0] and not out["rg"][0]:
        return None
    return out


def lookup_lastfm(api_key, artist, title):
    if not api_key:
        return None
    base = "https://ws.audioscrobbler.com/2.0/"
    tags, artist_tags = [], []
    if artist and title:
        j = _jget("lastfm", base, {"method": "track.gettoptags", "artist": artist, "track": title,
                                   "autocorrect": 1, "api_key": api_key, "format": "json"})
        tags += [(x.get("name", ""), int(x.get("count", 0))) for x in
                 ((j or {}).get("toptags") or {}).get("tag", []) if int(x.get("count", 0)) >= 5]
    if artist:
        j = _jget("lastfm", base, {"method": "artist.gettoptags", "artist": artist,
                                   "autocorrect": 1, "api_key": api_key, "format": "json"})
        artist_tags += [(x.get("name", ""), int(x.get("count", 0))) for x in
                 ((j or {}).get("toptags") or {}).get("tag", [])[:12] if int(x.get("count", 0)) >= 10]
    if not tags and not artist_tags:
        return None
    def ranked(items):
        seen, out = set(), []
        for n, c in sorted(items, key=lambda x: -x[1]):
            k = n.lower().strip()
            if k and k not in seen:
                seen.add(k)
                out.append(k)
        return out[:15]
    return {"tags": ranked(tags), "artist_tags": ranked(artist_tags)}


# ------------------------------------------------------------------ #
#  Кэш                                                                #
# ------------------------------------------------------------------ #

_SIG_CACHE: dict = {}                                          # path → (когда смотрели, подпись)
_SIG_TTL = 20.0


def _file_sig(path):
    """Размер+время изменения файла; в пределах 20 с — из памяти (страницы спрашивают сотни раз подряд)."""
    now = time.monotonic()
    hit = _SIG_CACHE.get(path)
    if hit is not None and now - hit[0] < _SIG_TTL:
        return hit[1]
    try:
        st = os.stat(path)
        sig = f"{st.st_size}:{st.st_mtime_ns}"
    except Exception:
        sig = ""
    if len(_SIG_CACHE) > 20000:
        _SIG_CACHE.clear()
    _SIG_CACHE[path] = (now, sig)
    return sig


class IntelStore:
    """Кэш признаков: tracks[path] = звук; meta["artist|title"] = жанры;
    artists[artist] = теги исполнителя."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.data = {"v": INTEL_VERSION, "tracks": {}, "meta": {}, "artists": {}}
        try:
            d = json.loads(self.path.read_text("utf-8"))
            if isinstance(d, dict) and d.get("v") == INTEL_VERSION:
                self.data.update(d)
        except Exception:
            pass
        self._dirty = 0

    def save(self, force=False):
        with self.lock:
            if not force and not self._dirty:
                return
            try:
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(self.data, ensure_ascii=False), "utf-8")
                os.replace(tmp, self.path)
                self._dirty = 0
            except Exception as e:
                _log(f"save: {e!r}")

    # ключи
    _IDENT: dict = {}

    @staticmethod
    def ident(track) -> tuple[str, str, list]:
        key = (track.get("title", ""), track.get("artist", ""), track.get("path", ""), track.get("album", ""))
        hit = IntelStore._IDENT.get(key)                       # разбор названия дорогой — запоминаем
        if hit is not None:
            return hit
        cands = parse_candidates(*key)
        a, t = cands[0] if cands else ("", "")
        res = (a, t, cands)
        if len(IntelStore._IDENT) > 30000:
            IntelStore._IDENT.clear()
        IntelStore._IDENT[key] = res
        return res

    @staticmethod
    def meta_key(a, t):
        return f"{norm(a)}|{norm(t)}"

    def audio(self, track):
        p = str(track.get("path", ""))
        with self.lock:
            e = self.data["tracks"].get(p)
        if e and e.get("sig") != _file_sig(p):
            return None
        return (e or {}).get("audio")

    def needs_audio(self, track):
        p = str(track.get("path", ""))
        with self.lock:
            e = self.data["tracks"].get(p)
        if not e:
            return True
        if e.get("failed"):
            return e.get("sig") != _file_sig(p) and bool(_file_sig(p))
        return e.get("sig") != _file_sig(p) or not e.get("audio")

    def put_audio(self, track, feats):
        p = str(track.get("path", ""))
        with self.lock:
            old = self.data["tracks"].get(p) or {}
            e = {"sig": _file_sig(p), "audio": feats, "failed": feats is None, "ts": int(time.time())}
            if old.get("sig") == e["sig"] and old.get("ai"):
                e["ai"] = old["ai"]
            self.data["tracks"][p] = e
            self._dirty += 1

    # признаки для ИИ жанров (genre_ai) — отдельно от старых, без пересчёта старых
    def ai_entry(self, track):
        import genre_ai
        p = str(track.get("path", ""))
        with self.lock:
            e = self.data["tracks"].get(p) or {}
            a = e.get("ai") or {}
        if e.get("sig") != _file_sig(p) or a.get("v") != genre_ai.FEAT_VERSION:
            return None
        return a

    def needs_ai(self, track):
        import genre_ai
        p = str(track.get("path", ""))
        with self.lock:
            e = self.data["tracks"].get(p)
        if not e or e.get("sig") != _file_sig(p):
            return True
        a = e.get("ai")
        if a is None:
            return True
        if a.get("v") != genre_ai.FEAT_VERSION:
            return True
        return not a.get("failed") and not a.get("x")

    def put_ai(self, track, vec, info):
        import genre_ai
        p = str(track.get("path", ""))
        with self.lock:
            e = self.data["tracks"].setdefault(p, {"sig": _file_sig(p), "ts": int(time.time())})
            signature = _file_sig(p)
            if e.get("sig") != signature:
                e.pop("audio", None)
                e.pop("failed", None)
            e["sig"] = signature
            e["ai"] = ({"v": genre_ai.FEAT_VERSION, "x": [round(float(v), 4) for v in vec], "info":
                        {k: round(float(v), 4) for k, v in (info or {}).items()}}
                       if vec is not None else {"v": genre_ai.FEAT_VERSION, "failed": True})
            self._dirty += 1

    def meta(self, track):
        a, t, _ = self.ident(track)
        with self.lock:
            m = self.data["meta"].get(self.meta_key(a, t))
            ar = self.data["artists"].get(norm(a)) if a else None
        return m, ar

    def needs_meta(self, track, max_age_days=45):
        a, t, _ = self.ident(track)
        with self.lock:
            m = self.data["meta"].get(self.meta_key(a, t))
        if not m:
            return True
        if m.get("v", 1) < META_VERSION:                   # новые источники (MusicBrainz-запись)
            return True
        age = time.time() - m.get("ts", 0)
        if not m.get("genres") and not m.get("tags"):
            return age > 7 * 86400                         # не нашли — повторим через неделю
        return age > max_age_days * 86400

    def put_meta(self, a, t, m):
        with self.lock:
            m["ts"] = int(time.time())
            self.data["meta"][self.meta_key(a, t)] = m
            self._dirty += 1

    def artist_tags(self, a):
        with self.lock:
            return self.data["artists"].get(norm(a))

    def put_artist(self, a, info):
        with self.lock:
            info = dict(info or {})
            info["ts"] = int(time.time())
            self.data["artists"][norm(a)] = info
            self._dirty += 1

    def stats(self, library):
        n = len(library)
        a = sum(1 for t in library if self.audio(t))
        g = 0
        for t in library:
            m, ar = self.meta(t)
            if (m and (m.get("genres") or m.get("tags"))) or (ar and ar.get("tags")):
                g += 1
        return {"total": n, "audio": a, "genre": g}


# ------------------------------------------------------------------ #
#  Профиль трека: звук + жанры + теги                                 #
# ------------------------------------------------------------------ #

def _percentiles(values):
    arr = np.asarray(values, float)
    if arr.size < 2:
        return lambda v: 0.5
    s = np.sort(arr)

    def f(v):
        return float(np.searchsorted(s, v, side="right")) / s.size
    return f


def _genre_ai_of(store, track) -> dict | None:
    """None means unavailable; {} is a valid decision with no accepted evidence."""
    try:
        import genre_ai
        ai = genre_ai.get_ai(store.path.parent)
        return ai.genre_of(store, track) if ai is not None else None
    except Exception as e:                                # noqa: BLE001
        _log(f"genre-ai: {e!r}")
        return None


def build_profiles(library: list, store: IntelStore) -> dict:
    """path → {energy, valence, dance, acoustic, bpm, families, tags_mood, sources}.

    Звуковые energy/valence ранжируются внутри библиотеки (0..1), затем
    смешиваются с априорными значениями жанра и сдвигами тегов настроения.
    """
    audio = {}
    for t in library:
        a = store.audio(t)
        if a and "energy" in a:
            audio[str(t.get("path", ""))] = a
    pe = _percentiles([a["energy"] for a in audio.values()])
    pv = _percentiles([a["valence"] for a in audio.values()])
    pd = _percentiles([a["dance"] for a in audio.values()])

    out = {}
    for t in library:
        p = str(t.get("path", ""))
        a = audio.get(p)
        m, ar = store.meta(t)
        raw_genres = [t.get("genre", "")]
        tags = []
        if m:
            raw_genres += m.get("genres") or []
            tags += m.get("tags") or []
        if ar:
            tags += ar.get("tags") or []
        g = _genre_ai_of(store, t)
        fams = list(g.get("families") or []) if g is not None else genre_families([t.get("genre", "")])
        # Uncertain/conflicting results must stay uncertain. Reintroducing the
        # unfiltered artist tags here used to undo the classifier's abstention.
        de, dv, mood_hits = mood_shift(tags)

        E = V = D = AC = None
        if a:
            # ранги библиотеки + немного абсолютного значения (чтобы «вся
            # библиотека тихая» не превращалась в «половина энергичная»)
            E = 0.7 * pe(a["energy"]) + 0.3 * a["energy"]
            V = 0.7 * pv(a["valence"]) + 0.3 * a["valence"]
            D = 0.6 * pd(a["dance"]) + 0.4 * a["dance"]
            AC = a["acoustic"]
        if fams:
            ge = np.mean([_FAMILY[f][0] for f in fams[:2]])
            gv = np.mean([_FAMILY[f][1] for f in fams[:2]])
            gd = np.mean([_FAMILY[f][2] for f in fams[:2]])
            ga = np.mean([_FAMILY[f][3] for f in fams[:2]])
            if E is None:
                E, V, D, AC = ge, gv, gd, ga
            else:
                E = 0.62 * E + 0.38 * ge
                V = 0.55 * V + 0.45 * gv
                D = 0.7 * D + 0.3 * gd
                AC = 0.6 * AC + 0.4 * ga
        if E is None:
            continue                                     # ничего не знаем — решит запасной путь
        E = float(min(1, max(0, E + de)))
        V = float(min(1, max(0, V + dv)))
        out[p] = {"energy": round(E, 3), "valence": round(V, 3), "dance": round(float(D), 3),
                  "acoustic": round(float(AC), 3), "bpm": (a or {}).get("bpm", 0),
                  "families": fams[:3], "mood_tags": mood_hits[:4],
                  "genre_conf": (g or {}).get("conf", 0.0), "genre_dist": (g or {}).get("dist", {}),
                  "genre_src": (g or {}).get("sources", []),
                  "has_audio": bool(a), "has_genre": bool(fams)}
    return out


# ------------------------------------------------------------------ #
#  Фоновый анализ                                                      #
# ------------------------------------------------------------------ #

class IntelWorker(threading.Thread):
    """Сначала звук (офлайн, быстро), потом сеть (жанры/теги, с паузами).
    progress(stage, done, total, label) вызывается из ЭТОГО потока —
    в UI передавать через сигнал."""

    def __init__(self, library, store: IntelStore, lastfm_key="", progress=None, finished=None,
                 network=True):
        super().__init__(daemon=True, name="music-intel")
        self.library = list(library)
        self.store = store
        self.lastfm_key = lastfm_key or ""
        self.progress = progress or (lambda *a: None)
        self.finished_cb = finished or (lambda *a: None)
        self.network = network
        self.stop_event = threading.Event()

    def stop(self):
        self.stop_event.set()

    def _emit(self, *a):
        try:
            self.progress(*a)
        except Exception:
            pass

    def run(self):
        try:
            self._run()
        except Exception as e:
            _log(f"worker: {e!r}")
        finally:
            self.store.save(force=True)
            try:
                import genre_ai
                ai = genre_ai.get_ai(self.store.path.parent)
                if ai is not None and not self.stop_event.is_set():
                    import bgproc
                    bgproc.idle_wait()
                    self._emit("train", 0, 1, "")
                    ai.train(self.library, self.store)
            except Exception as e:
                _log(f"genre-ai train: {e!r}")
            try:
                self.finished_cb(self.store.stats(self.library))
            except Exception:
                pass

    def _run(self):
        import genre_ai
        todo = [t for t in self.library if t.get("path") and os.path.exists(str(t["path"]))
                and (self.store.needs_audio(t) or self.store.needs_ai(t))]
        import bgproc
        for i, t in enumerate(todo):
            if self.stop_event.is_set():
                return
            bgproc.idle_wait()                            # интерфейс занят — анализ подождёт
            self._emit("audio", i, len(todo), t.get("title", ""))
            need_audio = self.store.needs_audio(t)
            feats, vec, info = None, None, None
            try:
                vec, info, x = genre_ai.analyze_file(str(t["path"]), float(t.get("duration") or 0),
                                                  return_audio=True, stop=self.stop_event)
                if self.stop_event.is_set():
                    return
                if x is not None:
                    if need_audio:
                        feats = analyze_signal(x) or None
                        if feats:
                            feats.update(audio_mood(feats))
            except Exception as e:
                _log(f"analyze {t.get('path')}: {e!r}")
            if need_audio:
                self.store.put_audio(t, feats)
            self.store.put_ai(t, vec, info)
            if i % 15 == 14:
                self.store.save()
            self.stop_event.wait(0.08)                    # дать дышать воспроизведению
        self.store.save()
        if not self.network:
            return

        todo = [t for t in self.library if self.store.needs_meta(t)]
        done_artists = set()
        for i, t in enumerate(todo):
            if self.stop_event.is_set():
                return
            bgproc.idle_wait()
            self._emit("genre", i, len(todo), t.get("title", ""))
            a, ti, cands = IntelStore.ident(t)
            if not ti:
                continue
            dur = float(t.get("duration") or 0)
            meta = {"genres": [], "tags": [], "src": [], "by_src": {}, "v": META_VERSION}
            r = lookup_deezer(cands, dur)
            if r and r.get("genres"):
                meta["genres"] += r["genres"]
                meta["src"].append("Deezer")
                meta["by_src"]["deezer"] = {"g": r["genres"], "score": r.get("score", 1.0)}
            r = lookup_itunes(cands, dur)                 # теперь всегда: второй голос
            if r and r.get("genres") and r["genres"][0]:
                meta["genres"] += [g for g in r["genres"] if g not in meta["genres"]]
                meta["src"].append("iTunes")
                meta["by_src"]["itunes"] = {"g": r["genres"], "score": r.get("score", 1.0)}
            r = lookup_musicbrainz_recording(cands, dur)
            if r:
                meta["src"].append("MusicBrainz")
                if r["rec"][0]:
                    meta["by_src"]["mb_rec"] = {"g": r["rec"][0], "c": r["rec"][1], "score": r["score"]}
                if r["rg"][0]:
                    meta["by_src"]["mb_rg"] = {"g": r["rg"][0], "c": r["rg"][1], "score": r["score"]}
                meta["genres"] += [g for g in r["rec"][0] + r["rg"][0] if g not in meta["genres"]]
            if self.lastfm_key and a:
                r = lookup_lastfm(self.lastfm_key, a, ti)
                if r:
                    meta["tags"] += r["tags"]
                    meta["src"].append("Last.fm")
                    meta["by_src"]["lastfm"] = {"g": r["tags"], "score": 1.0}
                    if r.get("artist_tags"):
                        meta["by_src"]["lastfm_artist"] = {"g": r["artist_tags"], "score": 1.0}
            self.store.put_meta(a, ti, meta)

            pa = primary_artist(a) if a else ""
            if pa and norm(pa) not in done_artists:
                done_artists.add(norm(pa))
                info = self.store.artist_tags(pa)
                if not info or time.time() - info.get("ts", 0) > 60 * 86400:
                    r = lookup_musicbrainz_artist(pa) or {}
                    self.store.put_artist(pa, {"tags": r.get("tags", []),
                                               "country": r.get("country", "")})
            if i % 10 == 9:
                self.store.save()
