# smart_features.py
"""
"Автономка" плеера — фичи, не завязанные напрямую на UI-виджеты:

  * Настроение дня (time-of-day + погода → авто-плейлист)
  * Таймер сна с плавным затуханием громкости
  * Pomodoro-фокус (авто play/pause музыки на work/break фазах)
  * Генератор розового/коричневого/белого шума (для атмосферы дождя)
  * "Трек дня" — детерминированный выбор давно не игравшего трека

Модуль сознательно не зависит от PyQt-виджетов (кроме QObject/QTimer для
таймеров) — вся эта логика легко покрывается юнит-тестами и не тянет
librosa/sounddevice: RMS и FFT уже считаются в visualizer.py/audio_engine.py
через numpy, поэтому здесь используется тот же подход без лишних тяжёлых
зависимостей.
"""

import datetime
import hashlib
import math
import random
import wave
from pathlib import Path

import numpy as np
import requests
from PyQt6.QtCore import QObject, QTimer, pyqtSignal


# ------------------------------------------------------------------ #
#  Время суток + погода                                              #
# ------------------------------------------------------------------ #

DAYPART_RU = {"morning": "Утро", "day": "День", "evening": "Вечер", "night": "Ночь"}
WEATHER_RU = {
    "Clear": "ясно", "Clouds": "облачно", "Rain": "дождь", "Drizzle": "морось",
    "Thunderstorm": "гроза", "Snow": "снег", "Mist": "туман", "Fog": "туман",
    "Haze": "дымка",
}

OWM_URL = "https://api.openweathermap.org/data/2.5/weather"


def get_daypart(now: datetime.datetime | None = None) -> str:
    """Возвращает 'morning' / 'day' / 'evening' / 'night' по текущему часу."""
    h = (now or datetime.datetime.now()).hour
    if 5 <= h < 11:
        return "morning"
    if 11 <= h < 17:
        return "day"
    if 17 <= h < 23:
        return "evening"
    return "night"


def fetch_weather(api_key: str, city: str = "", lat: float | None = None, lon: float | None = None) -> dict | None:
    """
    Запрашивает текущую погоду через OpenWeatherMap (бесплатный тариф).

    Как получить ключ: зарегистрироваться на https://openweathermap.org/api,
    создать API key (активируется обычно в течение ~10-60 минут) и вставить
    его через кнопку "Ключ погоды" в интерфейсе плеера. Без ключа функция
    просто возвращает None — тогда используется fallback_weather().
    """
    if not api_key:
        return None
    params = {"appid": api_key, "units": "metric", "lang": "ru"}
    if lat is not None and lon is not None:
        params["lat"] = lat
        params["lon"] = lon
    elif city:
        params["q"] = city
    else:
        return None
    try:
        r = requests.get(OWM_URL, params=params, timeout=6)
        if r.status_code == 200:
            data = r.json()
            w = (data.get("weather") or [{}])[0]
            return {
                "main": w.get("main", "Clear"),
                "description": w.get("description", ""),
                "temp": data.get("main", {}).get("temp"),
                "source": "openweathermap",
            }
    except Exception as e:
        print("[weather]", e)
    return None


def fallback_weather() -> dict:
    """
    Без API-ключа/сети — детерминированная (стабильная в течение дня)
    имитация погоды на основе даты и текущего месяца. Не претендует на
    точность, только чтобы "Настроение дня" работало и без интернета.
    """
    seed = hashlib.sha1(datetime.date.today().isoformat().encode()).hexdigest()
    n = int(seed[:4], 16)
    month = datetime.date.today().month
    if month in (12, 1, 2):
        options = ["Snow", "Clouds", "Clear"]
    elif month in (6, 7, 8):
        options = ["Clear", "Clouds", "Rain"]
    else:
        options = ["Clouds", "Rain", "Clear"]
    main = options[n % len(options)]
    return {"main": main, "description": "имитация (нет API-ключа)", "temp": None, "source": "fallback"}


_MOOD_KEYWORDS = {
    ("morning", "clear"): ["pop", "dance", "electro", "funk", "indie", "acoustic"],
    ("morning", "rain"):  ["acoustic", "jazz", "lofi", "chill", "soul"],
    ("day", "clear"):     ["pop", "dance", "house", "funk", "hip-hop", "rap"],
    ("day", "rain"):      ["lofi", "chill", "jazz", "rnb"],
    ("evening", "clear"): ["indie", "rnb", "soul", "synthwave", "chill"],
    ("evening", "rain"):  ["lofi", "jazz", "ambient", "chill", "piano"],
    ("night", "clear"):   ["ambient", "chill", "lofi", "synthwave"],
    ("night", "rain"):    ["ambient", "lofi", "piano", "sleep", "rain"],
}


def _weather_bucket(main: str) -> str:
    m = (main or "").lower()
    if m in ("rain", "drizzle", "thunderstorm", "snow", "mist", "fog", "haze"):
        return "rain"
    return "clear"


# ------------------------------------------------------------------ #
#  Реальная погода без ключа: Open-Meteo                               #
# ------------------------------------------------------------------ #

_GEO_CACHE: dict = {}


def _wmo_main(code: int) -> tuple[str, str]:
    c = int(code or 0)
    if c == 0:
        return "Clear", "ясно"
    if c in (1, 2):
        return "Clouds", "переменная облачность"
    if c == 3:
        return "Clouds", "пасмурно"
    if c in (45, 48):
        return "Fog", "туман"
    if 51 <= c <= 57:
        return "Drizzle", "морось"
    if 61 <= c <= 67 or 80 <= c <= 82:
        return "Rain", "дождь"
    if 71 <= c <= 77 or c in (85, 86):
        return "Snow", "снег"
    if c >= 95:
        return "Thunderstorm", "гроза"
    return "Clouds", "облачно"


def fetch_weather_open_meteo(city: str = "", lat: float | None = None,
                             lon: float | None = None) -> dict | None:
    """Текущая погода без API-ключа (open-meteo.com): город → координаты →
    код погоды WMO, температура, день/ночь, восход/закат."""
    try:
        if lat is None or lon is None:
            key = (city or "").strip().lower()
            if not key:
                return None
            if key not in _GEO_CACHE:
                r = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                                 params={"name": city, "count": 1, "language": "ru"}, timeout=6)
                res = (r.json() or {}).get("results") or [] if r.status_code == 200 else []
                if not res:
                    return None
                _GEO_CACHE[key] = (res[0]["latitude"], res[0]["longitude"])
            lat, lon = _GEO_CACHE[key]
        r = requests.get("https://api.open-meteo.com/v1/forecast", params={
            "latitude": lat, "longitude": lon, "timezone": "auto",
            "current": "temperature_2m,weather_code,is_day,cloud_cover,precipitation,wind_speed_10m",
            "daily": "sunrise,sunset", "forecast_days": 1}, timeout=6)
        if r.status_code != 200:
            return None
        d = r.json()
        cur = d.get("current") or {}
        main, desc = _wmo_main(cur.get("weather_code"))
        daily = d.get("daily") or {}
        return {"main": main, "description": desc, "temp": cur.get("temperature_2m"),
                "is_day": bool(cur.get("is_day", 1)), "wind": cur.get("wind_speed_10m"),
                "clouds": cur.get("cloud_cover"),
                "sunrise": (daily.get("sunrise") or [None])[0],
                "sunset": (daily.get("sunset") or [None])[0],
                "source": "open-meteo"}
    except Exception as e:
        print("[weather/open-meteo]", e)
        return None


def get_weather(owm_key: str = "", city: str = "Moscow") -> dict:
    """OpenWeatherMap (если есть ключ) → Open-Meteo (без ключа) → имитация."""
    w = fetch_weather(owm_key, city=city) if owm_key else None
    if not w:
        w = fetch_weather_open_meteo(city)
    if not w:
        w = fallback_weather()
    return w


# ------------------------------------------------------------------ #
#  Цель настроения: время суток × погода × температура × день недели   #
# ------------------------------------------------------------------ #

_DAYPART_TARGET = {
    #           energy valence dance acoustic
    "morning": (0.52, 0.64, 0.50, None),
    "day":     (0.64, 0.60, 0.62, None),
    "evening": (0.50, 0.50, 0.55, None),
    "night":   (0.28, 0.40, 0.35, 0.50),
}
_DAYPART_FAV = {
    "morning": {"pop": .06, "indie": .06, "funk": .06, "jpop": .05, "folk": .04, "rnb": .04, "dreampop": .03},
    "day":     {"hiphop": .05, "pop": .05, "dance": .06, "electronic": .05, "rock": .04,
                "hyperpop": .05, "trap": .04, "punk": .03, "jpop": .04},
    "evening": {"rnb": .06, "synthwave": .06, "indie": .05, "cloudrap": .05, "dreampop": .05,
                "lofi": .04, "hiphop": .03},
    "night":   {"ambient": .08, "lofi": .08, "dreampop": .06, "cloudrap": .05, "classical": .05,
                "synthwave": .04, "jazz": .05, "rnb": .03},
}
_DAYPART_AVOID = {
    "morning": {"metalcore": -.05, "dnb": -.04},
    "night":   {"metalcore": -.12, "metal": -.10, "dnb": -.10, "dubstep": -.10, "phonk": -.06,
                "punk": -.06},
}
_WEATHER_SHIFT = {
    #               dE    dV    dAcoustic  любимые семейства
    "Clear":        (.03, .08, 0.0, {"pop": .03, "funk": .04, "dance": .03, "latin": .04}),
    "Clouds":       (-.03, -.04, 0.0, {"indie": .03, "dreampop": .02}),
    "Rain":         (-.12, -.12, .15, {"lofi": .07, "dreampop": .06, "indie": .05, "emo": .05,
                                       "rnb": .04, "jazz": .05, "cloudrap": .05, "folk": .04}),
    "Drizzle":      (-.08, -.08, .10, {"lofi": .06, "dreampop": .05, "indie": .05, "jazz": .04}),
    "Thunderstorm": (.08, -.18, 0.0, {"metal": .07, "metalcore": .06, "phonk": .07, "dnb": .05,
                                      "dubstep": .05, "emo": .03}),
    "Snow":         (-.08, .02, .05, {"dreampop": .06, "ambient": .05, "jpop": .05, "lofi": .05,
                                      "synthwave": .04, "classical": .04}),
    "Mist":         (-.12, -.06, .05, {"ambient": .06, "dreampop": .06, "synthwave": .04, "cloudrap": .04}),
    "Fog":          (-.12, -.06, .05, {"ambient": .06, "dreampop": .06, "synthwave": .04, "cloudrap": .04}),
    "Haze":         (-.08, -.04, .03, {"dreampop": .05, "synthwave": .04}),
}


def mood_target(daypart: str, weather: dict | None, now: datetime.datetime | None = None) -> dict:
    now = now or datetime.datetime.now()
    weather = weather or {}
    e, v, d, ac = _DAYPART_TARGET.get(daypart, _DAYPART_TARGET["day"])
    fav = dict(_DAYPART_FAV.get(daypart, {}))
    avoid = dict(_DAYPART_AVOID.get(daypart, {}))
    reasons = []
    main = weather.get("main", "Clear")
    de, dv, dac, wfav = _WEATHER_SHIFT.get(main, (0, 0, 0, {}))
    if main == "Clear" and daypart == "night":
        de, dv = 0.0, 0.03                            # ясная ночь — не повод для бодрости
    e += de
    v += dv
    if dac:
        ac = (ac or 0.35) + dac
    for k, w in wfav.items():
        fav[k] = fav.get(k, 0) + w
    temp = weather.get("temp")
    if isinstance(temp, (int, float)):
        if temp >= 26:
            e += .05
            v += .07
            for k in ("latin", "dance", "pop", "funk"):
                fav[k] = fav.get(k, 0) + .04
            reasons.append("жарко")
        elif temp <= -8:
            e -= .04
            v -= .02
            reasons.append("мороз")
    if weather.get("is_day") is False and daypart in ("day", "evening"):
        e -= .04
        v -= .03                                       # рано темнеет
        reasons.append("уже темно")
    wd = now.weekday()
    if (wd == 4 and daypart in ("evening", "night")) or (wd == 5 and daypart in ("day", "evening", "night")):
        e += .12 if daypart != "night" else .06
        v += .08
        d += .12
        for k in ("dance", "hyperpop", "hiphop", "pop", "phonk"):
            fav[k] = fav.get(k, 0) + .03
        reasons.append("выходные")
    elif wd < 4 and daypart == "morning":
        reasons.append("будний день")
    elif wd == 6 and daypart in ("morning", "day"):
        e -= .06
        ac = (ac or 0.3) + .05
        reasons.append("воскресенье")
    clip = lambda x: max(0.05, min(0.95, x))
    return {"energy": clip(e), "valence": clip(v), "dance": clip(d),
            "acoustic": None if ac is None else clip(ac),
            "fav": fav, "avoid": avoid, "reasons": reasons}


def describe_mood(e: float, v: float) -> str:
    if e < 0.33:
        return "тихое и меланхоличное" if v < 0.42 else "спокойное и тёплое" if v >= 0.55 else "тихое, задумчивое"
    if e < 0.55:
        return "мягкое и грустноватое" if v < 0.42 else "лёгкое и светлое" if v >= 0.58 else "ровное, расслабленное"
    if e < 0.72:
        return "напряжённое и мрачное" if v < 0.4 else "бодрое и солнечное" if v >= 0.6 else "в меру бодрое"
    return "агрессивное, на взводе" if v < 0.4 else "энергичное и праздничное" if v >= 0.6 else "энергичное"


def _keyword_score(t, keywords):
    genre = str(t.get("genre", "")).lower()
    title = str(t.get("title", "")).lower()
    artist = str(t.get("artist", "")).lower()
    sc = 0
    for kw in keywords:
        if kw in genre:
            sc += 3
        if kw in title or kw in artist:
            sc += 1
    return sc


def build_smart_playlist_v2(library: list, profiles: dict, daypart: str, weather: dict | None,
                            limit: int = 25, history: dict | None = None,
                            now: datetime.datetime | None = None, rng: random.Random | None = None):
    """Подбор по звуковому профилю (energy/valence/dance/acoustic) + жанрам.

    Возвращает (tracks, info). info: target, mood, families, coverage, reasons.
    """
    rng = rng or random.Random()
    now = now or datetime.datetime.now()
    history = history or {}
    tgt = mood_target(daypart, weather, now)
    tE, tV, tD, tA = tgt["energy"], tgt["valence"], tgt["dance"], tgt["acoustic"]
    today = now.date().isoformat()
    kw = _MOOD_KEYWORDS.get((daypart, _weather_bucket((weather or {}).get("main", ""))), ["chill"])

    scored = []
    known = 0
    for t in library:
        p = str(t.get("path", ""))
        pr = profiles.get(p)
        h = history.get(p) or {}
        rec = 0.0
        if h.get("last") == today:
            rec -= 0.10                                # уже звучал сегодня
        cnt = int(h.get("count", 0) or 0)
        rec -= min(0.06, 0.006 * cnt)                  # не крутить одно и то же
        if pr:
            known += 1
            dist = math.sqrt(1.0 * (pr["energy"] - tE) ** 2 + 0.85 * (pr["valence"] - tV) ** 2
                             + 0.25 * (pr["dance"] - tD) ** 2
                             + (0.25 * (pr["acoustic"] - tA) ** 2 if tA is not None else 0.0))
            fam = pr.get("families") or []
            bonus = sum(tgt["fav"].get(f, 0) * (1.0 if i == 0 else 0.5) for i, f in enumerate(fam[:2]))
            bonus += sum(tgt["avoid"].get(f, 0) for f in fam[:2])
            sc = -dist + bonus + rec + rng.uniform(0, 0.05)
            scored.append((sc, t, pr))
        else:
            sc = -0.45 + 0.04 * _keyword_score(t, kw) + rec + rng.uniform(0, 0.08)
            scored.append((sc, t, None))
    if not scored:
        return [], {"target": tgt, "mood": describe_mood(tE, tV), "families": [], "coverage": 0.0,
                    "reasons": tgt["reasons"]}

    scored.sort(key=lambda x: -x[0])
    n_art = {}
    for _, t, _ in scored:
        a = str(t.get("artist", "")).lower().split(",")[0].strip()
        n_art[a] = n_art.get(a, 0) + 1
    many = max(n_art.values()) / max(1, len(scored))
    cap = 2 if many < 0.15 else 3 if many < 0.3 else 5

    pick, per_artist = [], {}
    for sc, t, pr in scored:
        a = str(t.get("artist", "")).lower().split(",")[0].strip()
        if a and per_artist.get(a, 0) >= cap:
            continue
        per_artist[a] = per_artist.get(a, 0) + 1
        pick.append((sc, t, pr))
        if limit and len(pick) >= limit:
            break

    # порядок: плавный «поток» — каждый следующий близок к предыдущему по
    # энергии/настроению/темпу, без двух подряд одного исполнителя
    def vec(pr):
        if not pr:
            return (tE, tV, 0.5)
        return (pr["energy"], pr["valence"], min(1.0, (pr.get("bpm") or 110) / 200))
    rest = pick[:]
    start = min(rest, key=lambda x: abs(vec(x[2])[0] - (tE - 0.08)) + abs(vec(x[2])[1] - tV) * 0.5)
    order = [start]
    rest.remove(start)
    while rest:
        le = vec(order[-1][2])
        la = str(order[-1][1].get("artist", "")).lower()
        def cost(x):
            v = vec(x[2])
            c = abs(v[0] - le[0]) + 0.7 * abs(v[1] - le[1]) + 0.4 * abs(v[2] - le[2])
            if str(x[1].get("artist", "")).lower() == la:
                c += 0.5
            return c - 0.15 * x[0]
        nxt = min(rest, key=cost)
        order.append(nxt)
        rest.remove(nxt)

    tracks = [t for _, t, _ in order]
    fam_count = {}
    for _, _, pr in order:
        for f in (pr or {}).get("families", [])[:1]:
            fam_count[f] = fam_count.get(f, 0) + 1
    fams = [f for f, _ in sorted(fam_count.items(), key=lambda x: -x[1])][:3]
    got = [pr for _, _, pr in order if pr]
    avgE = sum(p["energy"] for p in got) / len(got) if got else tE
    avgV = sum(p["valence"] for p in got) / len(got) if got else tV
    info = {"target": tgt, "mood": describe_mood(tE, tV), "families": fams,
            "coverage": known / max(1, len(library)), "avg": (avgE, avgV),
            "reasons": tgt["reasons"]}
    return tracks, info


def build_smart_playlist(library: list, daypart: str, weather_main: str, limit: int = 25,
                         profiles: dict | None = None, weather: dict | None = None,
                         history: dict | None = None) -> list:
    """Совместимая обёртка. С profiles — новый подбор по звуку и жанрам,
    без них — подбор по ключевым словам (старое поведение)."""
    if profiles:
        tracks, _ = build_smart_playlist_v2(library, profiles, daypart,
                                            weather or {"main": weather_main}, limit, history)
        return tracks
    bucket = _weather_bucket(weather_main)
    keywords = _MOOD_KEYWORDS.get((daypart, bucket), ["chill", "lofi"])
    scored = [(_keyword_score(t, keywords), t) for t in library]
    matched = [t for s, t in scored if s > 0]
    pool = matched if len(matched) >= 5 else [t for _, t in scored]
    pool = list(pool)
    random.shuffle(pool)
    return pool[:limit] if limit else pool


# ------------------------------------------------------------------ #
#  Таймер сна — плавное затухание громкости, затем пауза              #
# ------------------------------------------------------------------ #

class SleepTimer(QObject):
    tick = pyqtSignal(int)      # оставшиеся секунды
    finished = pyqtSignal()

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._on_tick)
        self._remaining = 0
        self._fade_seconds = 20
        self._base_volume = 1.0
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    def start(self, minutes: float, fade_seconds: int = 20):
        self._remaining = int(minutes * 60)
        self._fade_seconds = max(1, int(fade_seconds))
        self._base_volume = self.engine.volume
        self._active = True
        self._timer.start()

    def cancel(self):
        if not self._active:
            return
        self._active = False
        self._timer.stop()
        self.engine.set_volume(self._base_volume)

    def _on_tick(self):
        if not self._active:
            return
        self._remaining -= 1
        self.tick.emit(max(0, self._remaining))
        if self._remaining <= self._fade_seconds:
            frac = max(0.0, self._remaining / self._fade_seconds)
            self.engine.set_volume(self._base_volume * frac)
        if self._remaining <= 0:
            self._timer.stop()
            self._active = False
            self.engine.pause()
            self.engine.set_volume(self._base_volume)
            self.finished.emit()


# ------------------------------------------------------------------ #
#  Pomodoro — фокус-режим с авто play/pause                          #
# ------------------------------------------------------------------ #

class PomodoroTimer(QObject):
    phase_changed = pyqtSignal(str, int)   # "work" / "break", длительность фазы в сек
    tick = pyqtSignal(int)                  # оставшиеся секунды текущей фазы
    stopped = pyqtSignal()

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.auto_control_playback = True
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._on_tick)
        self.work_sec = 25 * 60
        self.break_sec = 5 * 60
        self.phase = "idle"
        self.remaining = 0

    def start(self, work_minutes: float = 25, break_minutes: float = 5):
        self.work_sec = max(1, int(work_minutes * 60))
        self.break_sec = max(1, int(break_minutes * 60))
        self._enter_phase("work")
        self._timer.start()

    def stop(self):
        self._timer.stop()
        self.phase = "idle"
        self.stopped.emit()

    def _enter_phase(self, phase: str):
        self.phase = phase
        self.remaining = self.work_sec if phase == "work" else self.break_sec
        self.phase_changed.emit(phase, self.remaining)
        if self.auto_control_playback:
            if phase == "work":
                self.engine.play()
            else:
                self.engine.pause()

    def _on_tick(self):
        if self.phase == "idle":
            return
        self.remaining -= 1
        self.tick.emit(max(0, self.remaining))
        if self.remaining <= 0:
            self._enter_phase("break" if self.phase == "work" else "work")


# ------------------------------------------------------------------ #
#  Генератор розового / коричневого / белого шума                    #
# ------------------------------------------------------------------ #

class NoiseGenerator:
    """
    Рендерит WAV-петлю шума один раз в кэш (~1 сек генерации на диск) и
    зацикленно проигрывает через отдельный vlc.MediaPlayer, использующий
    тот же vlc.Instance, что и основной AudioEngine — без повторной
    инициализации libvlc и без конфликта с основным плеером (это
    полностью независимая "дека", со своей громкостью).
    """

    KINDS = ("pink", "brown", "white")

    def __init__(self, vlc_instance, cache_dir: Path):
        self._instance = vlc_instance
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._player = vlc_instance.media_player_new()
        self._player.audio_set_volume(0)
        self._current_kind = None

    def _wav_path(self, kind: str, seconds: int, rate: int) -> Path:
        return self._cache_dir / f"noise_{kind}_{seconds}s.wav"

    def _generate(self, kind: str, seconds: int = 30, rate: int = 44100) -> Path:
        path = self._wav_path(kind, seconds, rate)
        if path.exists():
            return path
        n = seconds * rate
        rng = np.random.default_rng(42)
        white = rng.normal(0, 1, n).astype(np.float64)
        if kind == "brown":
            sig = np.cumsum(white)
        elif kind == "pink":
            # Простой 1/f фильтр через FFT — достаточно правдоподобный "розовый" шум.
            spec = np.fft.rfft(white)
            freqs = np.fft.rfftfreq(n, 1.0 / rate)
            freqs[0] = freqs[1] if len(freqs) > 1 else 1.0
            spec = spec / np.sqrt(freqs)
            sig = np.fft.irfft(spec, n)
        else:
            sig = white
        sig = sig / (np.max(np.abs(sig)) + 1e-9)
        # Лёгкий fade на стыках петли, чтобы луп не щёлкал при повторе.
        fade_n = max(1, int(0.01 * rate))
        env = np.ones(n)
        env[:fade_n] = np.linspace(0, 1, fade_n)
        env[-fade_n:] = np.linspace(1, 0, fade_n)
        pcm = (sig * env * 0.5 * 32767).astype(np.int16)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm.tobytes())
        return path

    def play(self, kind: str = "pink", volume: int = 30):
        if kind not in self.KINDS:
            kind = "pink"
        path = self._generate(kind)
        media = self._instance.media_new(str(path))
        media.add_option("input-repeat=65535")  # практически бесконечный луп
        self._player.set_media(media)
        self._player.audio_set_volume(max(0, min(100, int(volume))))
        self._player.play()
        self._current_kind = kind

    def set_volume(self, volume: int):
        self._player.audio_set_volume(max(0, min(100, int(volume))))

    def stop(self):
        self._player.stop()
        self._current_kind = None

    @property
    def is_playing(self) -> bool:
        return self._player.is_playing() == 1

    @property
    def current_kind(self):
        return self._current_kind


# ------------------------------------------------------------------ #
#  "Трек дня" — детерминированное открытие подзабытых треков          #
# ------------------------------------------------------------------ #

def pick_track_of_the_day(library: list, history: dict) -> dict | None:
    """
    history: {path: {"count": int, "last": "YYYY-MM-DD"}} — ведётся в
    settings.json (см. MainWindow._play_index). Выбирает случайный трек
    из трети самых "залежавшихся" (давно не игравших/редко играющих),
    но результат стабилен в течение суток — используется дата как seed.
    """
    if not library:
        return None
    today = datetime.date.today()

    def score(t):
        h = history.get(str(t.get("path", "")), {})
        last = h.get("last")
        try:
            days_since = (today - datetime.date.fromisoformat(last)).days if last else 9999
        except ValueError:
            days_since = 9999
        return days_since - h.get("count", 0) * 0.5

    ranked = sorted(library, key=score, reverse=True)
    pool = ranked[: max(1, len(ranked) // 3)]
    rng = random.Random(today.isoformat())
    return rng.choice(pool)
