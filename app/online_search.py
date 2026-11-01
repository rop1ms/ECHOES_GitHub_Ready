# online_search.py
"""
Поиск музыки В ИНТЕРНЕТЕ (без ссылок): YouTube Music и SoundCloud.

  • YouTube Music — через ytmusicapi (тот же внутренний API, что у сайта
    music.youtube.com: песни, исполнители, подписчики, страницы артистов).
    Если библиотеки нет — плеер один раз сам ставит её через pip (в .exe —
    нельзя, тогда запасной путь: поиск yt-dlp «ytsearch»).
  • SoundCloud — через api-v2.soundcloud.com (как сайт): треки с числом
    прослушиваний, профили с подписчиками, все треки профиля. Ключ
    client_id вытаскивается из JS сайта (как это делает yt-dlp) и кэшируется.

Модуль без Qt — всё вызывается из фоновых потоков.
Результаты нормализованы:
  трек:   {src, title, artist, url, cover_url, duration, plays, id}
  артист: {src, name, id, cover_url, followers, url, subtitle}
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

DATA = Path.home() / ".neon_player"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
SRC_NAMES = {"ytm": "YouTube Music", "yt": "YouTube", "sc": "SoundCloud"}


def _log(*a):
    """В консоль и в ~/.neon_player/online.log (у pythonw консоли нет)."""
    msg = " ".join(str(x) for x in a)
    try:
        print("[online]", msg)
    except Exception:                              # noqa: BLE001
        pass
    try:
        p = DATA / "online.log"
        if p.exists() and p.stat().st_size > 300_000:
            p.write_text("", encoding="utf-8")
        with open(p, "a", encoding="utf-8") as f:
            f.write(time.strftime("%m-%d %H:%M:%S ") + msg + "\n")
    except Exception:                              # noqa: BLE001
        pass


# ══════════════════════════════════════════════════════════════════════════ #
#  SoundCloud
# ══════════════════════════════════════════════════════════════════════════ #

_SC_API = "https://api-v2.soundcloud.com"
_sc_lock = threading.Lock()
_sc_id = {"id": "", "ts": 0.0}


def _sc_id_file():
    return DATA / "sc_client_id.json"


def sc_client_id(force=False) -> str:
    with _sc_lock:
        now = time.time()
        if not force and _sc_id["id"] and now - _sc_id["ts"] < 6 * 3600:
            return _sc_id["id"]
        if not force and now - _sc_id.get("fail", 0) < 60:
            return ""                              # только что не вышло — не долбим сайт
        if not force:
            try:
                d = json.loads(_sc_id_file().read_text("utf-8"))
                if d.get("id") and now - d.get("ts", 0) < 24 * 3600:
                    _sc_id.update(id=d["id"], ts=d["ts"])
                    return d["id"]
            except Exception:                      # noqa: BLE001
                pass
        cid = ""
        try:
            page = requests.get("https://soundcloud.com/", headers=UA, timeout=10).text
            scripts = re.findall(r'<script[^>]+src="(https://a-v2\.sndcdn\.com/assets/[^"]+\.js)"', page)
            for src in reversed(scripts):
                js = requests.get(src, headers=UA, timeout=10).text
                m = re.search(r'client_id\s*:\s*"([0-9a-zA-Z]{32})"', js)
                if m:
                    cid = m.group(1)
                    break
        except Exception as e:                     # noqa: BLE001
            _log("sc client_id:", e)
        if not cid:
            # запасной путь — тот же механизм внутри yt-dlp
            try:
                import yt_dlp
                with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
                    ie = ydl.get_info_extractor("Soundcloud")
                    ie.initialize()
                    ie._update_client_id()
                    cid = ydl.cache.load("soundcloud", "client_id") or ""
            except Exception as e:                 # noqa: BLE001
                _log("sc client_id via yt-dlp:", e)
        if not cid:
            _sc_id["fail"] = now
        if cid:
            _sc_id.update(id=cid, ts=now)
            try:
                DATA.mkdir(parents=True, exist_ok=True)
                _sc_id_file().write_text(json.dumps({"id": cid, "ts": now}), "utf-8")
            except Exception:                      # noqa: BLE001
                pass
        return cid


def _sc_get(path, params=None, retry=True):
    cid = sc_client_id()
    if not cid:
        return None
    p = dict(params or {})
    p["client_id"] = cid
    try:
        r = requests.get(_SC_API + path, params=p, headers=UA, timeout=10)
        if r.status_code in (401, 403) and retry:
            sc_client_id(force=True)
            return _sc_get(path, params, retry=False)
        if r.status_code == 200:
            return r.json()
    except Exception as e:                         # noqa: BLE001
        _log("sc", path, e)
    return None


def _sc_art(url, size="t300x300"):
    return (url or "").replace("-large.", f"-{size}.")


def _sc_track(t) -> dict:
    u = t.get("user") or {}
    return {"src": "sc", "id": t.get("id"), "title": t.get("title", ""),
            "artist": u.get("username", ""), "artist_id": u.get("id"),
            "url": t.get("permalink_url", ""),
            "cover_url": _sc_art(t.get("artwork_url") or u.get("avatar_url", "")),
            "duration": int((t.get("full_duration") or t.get("duration") or 0) / 1000),
            "plays": int(t.get("playback_count") or 0), "likes": int(t.get("likes_count") or 0),
            # Go+/платные треки SoundCloud без подписки отдают только 30-сек. превью
            "snip": _sc_is_snip(t)}


def _sc_is_snip(t) -> bool:
    if str(t.get("policy") or "").upper() in ("SNIP", "BLOCK"):
        return True
    full, dur = t.get("full_duration") or 0, t.get("duration") or 0
    if full and dur and dur < full - 5000:
        return True
    if t.get("streamable") is False:
        return True
    return False


def _sc_user(u) -> dict:
    tc = int(u.get("track_count") or 0)
    return {"src": "sc", "id": u.get("id"), "name": u.get("username", ""),
            "full_name": u.get("full_name", ""), "url": u.get("permalink_url", ""),
            "cover_url": _sc_art(u.get("avatar_url", ""), "t500x500"),
            "followers": int(u.get("followers_count") or 0), "tracks": tc,
            "city": u.get("city") or "", "description": u.get("description") or "",
            "subtitle": f"{tc} треков" if tc else ""}


def sc_search(q, limit=20):
    j = _sc_get("/search/tracks", {"q": q, "limit": limit})
    return [_sc_track(t) for t in (j or {}).get("collection", []) if t.get("kind", "track") == "track"]


def sc_search_users(q, limit=10):
    j = _sc_get("/search/users", {"q": q, "limit": limit})
    return [_sc_user(u) for u in (j or {}).get("collection", [])]


def sc_profile(user_id, limit=60):
    u = _sc_get(f"/users/{user_id}")
    tr = _sc_get(f"/users/{user_id}/tracks", {"limit": limit, "linked_partitioning": 1})
    info = _sc_user(u) if u else {"src": "sc", "id": user_id, "name": ""}
    info["track_list"] = [_sc_track(t) for t in (tr or {}).get("collection", [])]
    return info


# ══════════════════════════════════════════════════════════════════════════ #
#  YouTube Music
# ══════════════════════════════════════════════════════════════════════════ #

_ytm_clients: dict = {}
_ytm_lock = threading.Lock()
_install_state = {"tried": False}


class _TimeoutSession(requests.Session):
    """ytmusicapi ходит в сеть без таймаута — повисший запрос «вешал» весь поиск."""

    def request(self, *a, **k):
        k.setdefault("timeout", 15)
        return super().request(*a, **k)


def _ytm_import():
    """from ytmusicapi import YTMusic; если пакета нет — один раз ставим его
    в текущее окружение (не в собранном .exe)."""
    try:
        from ytmusicapi import YTMusic
        return YTMusic
    except ImportError:
        pass
    if getattr(sys, "frozen", False) or _install_state["tried"]:
        return None
    _install_state["tried"] = True
    _log("ставлю ytmusicapi…")
    try:
        flags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith("win") else 0
        exe = sys.executable
        if exe.lower().endswith("pythonw.exe"):
            exe = exe[:-5] + ".exe"           # pythonw → python для pip
        subprocess.run([exe, "-m", "pip", "install", "--quiet", "--upgrade", "ytmusicapi"],
                       timeout=180, creationflags=flags, capture_output=True)
        from ytmusicapi import YTMusic      # noqa: F811
        return YTMusic
    except Exception as e:                 # noqa: BLE001
        _log("ytmusicapi не установился:", e)
        return None


def _make_ytm(YTMusic, language):
    # ВАЖНО: без location="RU" — в России YouTube Music официально не работает,
    # и с этой страной сервер отдавал пустую выдачу (поиск «не отвечал»).
    try:
        return YTMusic(language=language, requests_session=_TimeoutSession())
    except TypeError:
        return YTMusic(language=language)


def ytm_client(lang="ru"):
    """ytmusicapi.YTMusic() или None. Клиенты кэшируются по языку."""
    with _ytm_lock:
        if lang in _ytm_clients:
            return _ytm_clients[lang]
        YTMusic = _ytm_import()
        yt = None
        if YTMusic is not None:
            for lg in (lang, "en"):
                try:
                    yt = _make_ytm(YTMusic, lg)
                    break
                except Exception as e:             # noqa: BLE001
                    _log("YTMusic:", lg, e)
            if yt is None:
                try:
                    yt = YTMusic()
                except Exception as e:             # noqa: BLE001
                    _log("YTMusic:", e)
        _ytm_clients[lang] = yt
        return yt


def _ytm_query(kind, q, limit):
    """Поиск на YT Music с запасом: русский клиент → английский клиент."""
    last = None
    for lang in ("ru", "en"):
        yt = ytm_client(lang)
        if yt is None:
            continue
        try:
            res = yt.search(q, filter=kind, limit=limit) or []
        except Exception as e:                     # noqa: BLE001
            last = e
            _log("ytm search", kind, lang, e)
            continue
        if res:
            return res
    if last is not None:
        raise last
    return []


def _thumb(thumbs):
    if not thumbs:
        return ""
    t = sorted(thumbs, key=lambda x: x.get("width", 0))[-1].get("url", "")
    # у YT Music можно попросить картинку побольше
    return re.sub(r"=w\d+-h\d+", "=w400-h400", t)


def _parse_views(s) -> int:
    if not s:
        return 0
    s = str(s).lower().replace("\xa0", " ").replace(",", ".")
    m = re.search(r"([\d.]+)\s*(k|m|b|тыс|млн|млрд)?", s)
    if not m:
        return 0
    try:
        v = float(m.group(1))
    except ValueError:
        return 0
    mul = {"k": 1e3, "тыс": 1e3, "m": 1e6, "млн": 1e6, "b": 1e9, "млрд": 1e9}.get(m.group(2) or "", 1)
    return int(v * mul)


def _ytm_song(s) -> dict:
    arts = s.get("artists") or []
    vid = s.get("videoId")
    return {"src": "ytm", "id": vid, "title": s.get("title", ""),
            "artist": ", ".join(a.get("name", "") for a in arts if a.get("name")),
            "artist_id": next((a.get("id") for a in arts if a.get("id")), None),
            "album": (s.get("album") or {}).get("name", "") if isinstance(s.get("album"), dict) else "",
            "url": f"https://music.youtube.com/watch?v={vid}" if vid else "",
            "cover_url": _thumb(s.get("thumbnails")),
            "duration": int(s.get("duration_seconds") or 0),
            "plays": _parse_views(s.get("views") or s.get("plays"))}


def ytm_search(q, limit=20):
    try:
        res = _ytm_query("songs", q, limit)
        out = [_ytm_song(x) for x in res if x.get("videoId")][:limit]
        if out:
            return out
    except Exception as e:                         # noqa: BLE001
        _log("ytm search:", e)
    # YT Music ничего не дал — ищем на обычном YouTube через yt-dlp
    return yt_dlp_search(q, limit)


def ytm_search_artists(q, limit=10):
    try:
        res = _ytm_query("artists", q, limit)
    except Exception as e:                         # noqa: BLE001
        _log("ytm artists:", e)
        return []
    out = []
    for a in res[:limit]:
        bid = a.get("browseId")
        if not bid:
            continue
        subs = a.get("subscribers") or ""
        out.append({"src": "ytm", "id": bid, "name": a.get("artist") or a.get("title", ""),
                    "url": f"https://music.youtube.com/channel/{bid}",
                    "cover_url": _thumb(a.get("thumbnails")),
                    "followers": _parse_views(subs), "subtitle": ""})
    return out


def ytm_profile(browse_id, limit=None):                 # None — все песни, а не первые 60
    yt = ytm_client()
    info = {"src": "ytm", "id": browse_id, "name": "", "track_list": []}
    if yt is None:
        return info
    try:
        a = yt.get_artist(browse_id)
    except Exception as e:                         # noqa: BLE001
        _log("ytm artist:", e)
        return info
    info.update({"name": a.get("name", ""), "description": a.get("description") or "",
                 "cover_url": _thumb(a.get("thumbnails")),
                 "followers": _parse_views(a.get("subscribers")),
                 "listeners": _parse_views(a.get("monthlyListeners")),
                 "views": _parse_views(a.get("views")),
                 "url": f"https://music.youtube.com/channel/{browse_id}"})
    songs = a.get("songs") or {}
    items = songs.get("results") or []
    # полный список песен артиста — отдельный плейлист
    if songs.get("browseId"):
        try:
            pl = yt.get_playlist(songs["browseId"], limit=limit)
            items = pl.get("tracks") or items
        except Exception as e:                     # noqa: BLE001
            _log("ytm artist songs:", e)
    info["track_list"] = [_ytm_song(s) for s in items if s.get("videoId")][:limit]
    for t in info["track_list"]:
        if not t["artist"]:
            t["artist"] = info["name"]
    info["tracks"] = len(info["track_list"])
    return info


def yt_dlp_search(q, limit=15):
    """Запасной поиск без ytmusicapi: обычный YouTube через yt-dlp."""
    try:
        import yt_dlp
        with yt_dlp.YoutubeDL({"quiet": True, "extract_flat": "in_playlist", "skip_download": True,
                               "noplaylist": False}) as ydl:
            info = ydl.extract_info(f"ytsearch{limit}:{q}", download=False)
    except Exception as e:                         # noqa: BLE001
        _log("yt-dlp search:", e)
        return []
    out = []
    for e in (info or {}).get("entries") or []:
        vid = e.get("id")
        if not vid:
            continue
        out.append({"src": "yt", "id": vid, "title": e.get("title", ""),
                    "artist": e.get("channel") or e.get("uploader") or "",
                    "url": f"https://www.youtube.com/watch?v={vid}",
                    "cover_url": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                    "duration": int(e.get("duration") or 0), "plays": int(e.get("view_count") or 0)})
    return out


# ══════════════════════════════════════════════════════════════════════════ #
#  Всё сразу
# ══════════════════════════════════════════════════════════════════════════ #

def search_all(q: str, sources=("ytm", "sc")) -> dict:
    """Параллельно по всем источникам. Возвращает tracks/artists/errors/sources."""
    jobs = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        if "ytm" in sources:
            jobs["ytm_t"] = ex.submit(ytm_search, q, 20)
            jobs["ytm_a"] = ex.submit(ytm_search_artists, q, 8)
        if "sc" in sources:
            jobs["sc_t"] = ex.submit(sc_search, q, 20)
            jobs["sc_a"] = ex.submit(sc_search_users, q, 8)
        res = {k: (f.result() or []) for k, f in jobs.items()}
    # треки: чередуем источники, чтобы сверху были лучшие с каждого
    yt_t, sc_t = res.get("ytm_t", []), res.get("sc_t", [])
    # YouTube Music в приоритете: сначала его треки, SoundCloud — ниже
    tracks = list(yt_t) + list(sc_t)
    yt_a, sc_a = res.get("ytm_a", []), res.get("sc_a", [])
    artists = []
    for i in range(max(len(yt_a), len(sc_a))):
        if i < len(yt_a):
            artists.append(yt_a[i])
        if i < len(sc_a):
            artists.append(sc_a[i])
    ok = []
    if yt_t or yt_a:
        ok.append(SRC_NAMES["yt"] if (yt_t and yt_t[0]["src"] == "yt") else SRC_NAMES["ytm"])
    if sc_t or sc_a:
        ok.append(SRC_NAMES["sc"])
    return {"tracks": tracks, "artists": artists, "sources": ok}


def profile_raw(artist: dict) -> dict:
    if artist.get("src") == "sc":
        return sc_profile(artist["id"])
    return ytm_profile(artist["id"])


# ══════════════════════════════════════════════════════════════════════════ #
#  Формат для интерфейса темы (ya_online): source / thumb_url / tracks / desc
# ══════════════════════════════════════════════════════════════════════════ #

def _ui_track(t: dict) -> dict:
    return {"title": t.get("title", ""), "artist": t.get("artist", ""), "url": t.get("url", ""),
            "thumb_url": t.get("cover_url", ""), "source": t.get("src", "ytm"),
            "plays": t.get("plays", 0), "duration": t.get("duration", 0),
            "album": t.get("album", ""), "id": t.get("id"), "snip": bool(t.get("snip"))}


def _ui_artist(a: dict) -> dict:
    return {"name": a.get("name", ""), "url": a.get("url", ""), "thumb_url": a.get("cover_url", ""),
            "source": a.get("src", "ytm"), "followers": a.get("followers", 0), "id": a.get("id"),
            "src": a.get("src", "ytm")}


def search(query: str, source: str = "all") -> dict:
    """Онлайн-поиск для интерфейса: source = all / ytm / sc."""
    srcs = ("ytm", "sc") if source == "all" else (source,)
    res = search_all(query, srcs)
    errors = []
    if "ytm" in srcs and not any(t["src"] in ("ytm", "yt") for t in res["tracks"]) \
            and not any(a["src"] == "ytm" for a in res["artists"]):
        errors.append("YouTube Music не ответил")
    if "sc" in srcs and not any(t["src"] == "sc" for t in res["tracks"]) \
            and not any(a["src"] == "sc" for a in res["artists"]):
        errors.append("SoundCloud не ответил")
    return {"tracks": [_ui_track(t) for t in res["tracks"]],
            "artists": [_ui_artist(a) for a in res["artists"]],
            "errors": errors}


def profile(artist: dict) -> dict:
    """Профиль исполнителя в формате интерфейса."""
    src = artist.get("source") or artist.get("src") or "ytm"
    aid = artist.get("id")
    try:
        d = sc_profile(aid) if src == "sc" else ytm_profile(aid)
    except Exception as e:                         # noqa: BLE001
        return {"name": artist.get("name", ""), "tracks": [], "error": str(e)}
    if not d.get("name") and not d.get("track_list"):
        return {"name": artist.get("name", ""), "tracks": [],
                "error": "профиль не загрузился"}
    views = d.get("views")
    listeners = d.get("listeners")
    extra = []
    if listeners:
        extra.append(f"{listeners:,}".replace(",", " ") + " слушателей в месяц")
    if views:
        extra.append(f"{views:,}".replace(",", " ") + " просмотров")
    return {"name": d.get("name") or artist.get("name", ""),
            "followers": d.get("followers", 0),
            "tracks": [_ui_track(t) for t in d.get("track_list", [])],
            "tracks_count": d.get("tracks") if src == "sc" else 0,
            "views": "  ·  ".join(extra),
            "city": d.get("city", ""),
            "thumb_url": d.get("cover_url") or artist.get("thumb_url", ""),
            "desc": d.get("description", ""),
            "verified": False}


# ══════════════════════════════════════════════════════════════════════════ #
#  Подбор лучшего совпадения (для импорта Spotify) и «радио» похожих треков
# ══════════════════════════════════════════════════════════════════════════ #

def find_best(title: str, artist: str, duration: float = 0.0, prefer=("ytm", "sc")):
    """Найти трек «artist — title» сначала на YouTube Music, потом на SoundCloud.
    Возвращает трек в формате интерфейса (с ключом 'score') или None."""
    try:
        from track_identity import match_score
    except Exception:                              # noqa: BLE001
        return None
    cands = [(artist or "", title or "")]
    q = f"{artist} {title}".strip()
    for src in prefer:
        try:
            res = ytm_search(q, 6) if src == "ytm" else sc_search(q, 8)
        except Exception as e:                     # noqa: BLE001
            _log("find_best", src, e)
            res = []
        best, bs = None, 0.0
        for t in res:
            if t.get("snip"):
                continue
            sc = match_score(cands, t.get("artist", ""), t.get("title", ""), t.get("duration", 0), duration)
            # на SoundCloud ремиксы/«slowed» и т.п. — штраф, если их нет в запросе
            low = (t.get("title") or "").lower()
            for junk in ("remix", "slowed", "sped up", "nightcore", "cover", "8d", "reverb"):
                if junk in low and junk not in (title or "").lower():
                    sc -= 0.25
            if sc > bs:
                best, bs = t, sc
        if best is not None and bs >= 0.72:
            out = _ui_track(best)
            out["score"] = round(bs, 3)
            return out
    return None


def find_candidates(title: str, artist: str, duration: float = 0.0, limit: int = 8,
                    allow_sc: bool = True) -> list:
    """Все подходящие варианты трека, лучшие первыми. Порядок источников строгий:
    YouTube Music (песни) → YouTube (видео) → SoundCloud (только полные версии,
    без 30-секундных превью). SoundCloud ищется, только если на YouTube ничего нет."""
    try:
        from track_identity import match_score
    except Exception:                              # noqa: BLE001
        match_score = None
    cands = [(artist or "", title or "")]
    tl = (title or "").lower()
    junk_words = ("remix", "slowed", "sped up", "nightcore", "cover", "8d", "reverb", "karaoke",
                  "instrumental", "live", "acoustic", "minus", "минус", "speed up", "bass boosted")

    def score(t):
        if match_score is not None:
            sc = match_score(cands, t.get("artist", ""), t.get("title", ""), t.get("duration", 0), duration)
        else:
            sc = 0.6
        low = (t.get("title") or "").lower()
        for junk in junk_words:
            if junk in low and junk not in tl:
                sc -= 0.25
        d = t.get("duration") or 0
        if duration and d:
            diff = abs(d - duration)
            if diff <= 3:
                sc += 0.08                         # длительность совпала — почти наверняка оно
            elif diff > 30:
                sc -= 0.3
        if d and d < 45 and (not duration or duration > 60):
            sc -= 1.0                              # превью/обрезок
        return sc

    def collect(res, min_sc):
        out = []
        for t in res or []:
            if not t.get("url") or t.get("snip"):
                continue
            sc = score(t)
            if sc >= min_sc:
                u = _ui_track(t)
                u["score"] = round(sc, 3)
                out.append(u)
        out.sort(key=lambda x: -x["score"])
        return out

    def safe(fn, *a):
        for _ in range(2):
            try:
                return fn(*a) or []
            except Exception as e:                 # noqa: BLE001
                _log("find_candidates", getattr(fn, "__name__", fn), e)
                time.sleep(1.0)
        return []

    a1 = (artist or "").strip()
    queries = [f"{a1} {title}".strip(), f"{title} {a1}".strip(), title or ""]
    ytm, seen_q = [], set()
    yt = ytm_client()
    for q in queries:
        if not q or q in seen_q:
            continue
        seen_q.add(q)
        if yt is not None:
            ytm += collect(safe(lambda q=q: [_ytm_song(x) for x in _ytm_query("songs", q, 10)
                                             if x.get("videoId")]), 0.5)
        if ytm and ytm[0]["score"] >= 0.8:
            break
    if yt is not None and (not ytm or max(x["score"] for x in ytm) < 0.7):
        # бывает, что трек на YT Music лежит как «видео», а не «песня»
        def vids(q=queries[0]):
            out = []
            for x in _ytm_query("videos", q, 8):
                if not x.get("videoId"):
                    continue
                d = _ytm_song(x)
                d["duration"] = int(x.get("duration_seconds") or _len_to_sec(x.get("duration", "")) or 0)
                out.append(d)
            return out
        ytm += collect(safe(vids), 0.5)
    ytv = []
    if not ytm or max(x["score"] for x in ytm) < 0.7:
        ytv = collect(safe(yt_dlp_search, f"{a1} - {title}".strip(" -"), 8), 0.5)
    sc = []
    if allow_sc and not ytm and not ytv:
        sc = collect(safe(sc_search, queries[0], 10), 0.55)
    res, seen = [], set()
    for group in (sorted(ytm, key=lambda x: -x["score"]), ytv, sc):
        for t in group:
            if t["url"] in seen:
                continue
            seen.add(t["url"])
            res.append(t)
    return res[:limit]


def album_tracks(artist: str, title: str) -> dict | None:
    """Найти альбом «artist — title» на YouTube Music и вернуть его треки
    (формат интерфейса + album/thumb_url у каждого трека)."""
    yt = ytm_client()
    if yt is None:
        return None
    try:
        from track_identity import similarity
    except Exception:                              # noqa: BLE001
        def similarity(a, b):
            return 1.0 if (a or "").lower() == (b or "").lower() else 0.0
    res = []
    for q in (f"{artist} {title}".strip(), title):
        try:
            res = yt.search(q, filter="albums", limit=10) or []
        except Exception as e:                     # noqa: BLE001
            _log("ytm albums:", e)
            res = []
        if not res:                                # фильтрованный поиск YouTube сейчас часто пуст — берём общий
            try:
                res = [x for x in (yt.search(q, limit=25) or []) if x.get("resultType") == "album"]
            except Exception as e:                 # noqa: BLE001
                _log("ytm albums (общий поиск):", e)
                res = []
        if res:
            break
    best, bs = None, 0.0
    for a in res:
        if not a.get("browseId"):
            continue
        arts = ", ".join(x.get("name", "") for x in (a.get("artists") or []))
        sc = similarity(title, a.get("title", "")) * 0.7 + (similarity(artist, arts) if artist else 0.6) * 0.3
        if sc > bs:
            best, bs = a, sc
    if best is None or bs < 0.6:
        return None
    try:
        alb = yt.get_album(best["browseId"])
    except Exception as e:                         # noqa: BLE001
        _log("ytm album:", e)
        return None
    name = alb.get("title") or best.get("title", "")
    cover = _thumb(alb.get("thumbnails") or best.get("thumbnails"))
    aart = ", ".join(x.get("name", "") for x in (alb.get("artists") or [])) or artist
    out = []
    for s in alb.get("tracks") or []:
        if not s.get("videoId"):
            continue
        t = _ytm_song({"videoId": s["videoId"], "title": s.get("title", ""),
                       "artists": s.get("artists") or alb.get("artists") or [],
                       "thumbnails": alb.get("thumbnails") or [],
                       "duration_seconds": s.get("duration_seconds") or _len_to_sec(s.get("duration", "")),
                       "album": {"name": name}})
        u = _ui_track(t)
        u["album"], u["thumb_url"] = name, cover
        if not u["artist"]:
            u["artist"] = aart
        out.append(u)
    return {"title": name, "artist": aart, "thumb_url": cover, "year": alb.get("year", ""), "tracks": out}


# ══════════════════════════════════════════════════════════════════════════ #
#  Дискография исполнителя (YouTube Music): все альбомы и синглы, треклисты
#  с прослушиваниями, все песни. Кэш на диске — 3 дня (альбомы почти не меняются).
# ══════════════════════════════════════════════════════════════════════════ #

_DISCO_DIR = Path.home() / ".neon_player" / "net_cache" / "ytm"
_DISCO_TTL = 3 * 86400


def _disco_cache(key, fn, force=False):
    f = _DISCO_DIR / (re.sub(r"[^\w-]+", "_", key)[:120] + ".json")
    if not force:
        try:
            d = json.loads(f.read_text("utf-8"))
            if time.time() - d.get("_ts", 0) < _DISCO_TTL:
                return d
        except Exception:                          # noqa: BLE001
            pass
    d = fn()
    if d:
        d["_ts"] = time.time()
        try:
            _DISCO_DIR.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(d, ensure_ascii=False), "utf-8")
        except Exception:                          # noqa: BLE001
            pass
    return d


def ytm_artist_id(name: str) -> str | None:
    """browseId исполнителя на YouTube Music по имени (лучшее совпадение)."""
    try:
        from track_identity import similarity
    except Exception:                              # noqa: BLE001
        def similarity(a, b):
            return 1.0 if (a or "").lower() == (b or "").lower() else 0.0
    best, bs = None, 0.0
    for a in ytm_search_artists(name, 6):
        s = similarity(name, a.get("name", ""))
        if s > bs:
            best, bs = a, s
    return best["id"] if best and bs >= 0.8 else None


def _release(r, kind) -> dict:
    return {"title": r.get("title", ""), "year": str(r.get("year") or ""), "browseId": r.get("browseId"),
            "type": (r.get("type") or kind), "thumb_url": _thumb(r.get("thumbnails"))}


def ytm_discography(browse_id: str, force=False) -> dict:
    """{"name", "albums": [...], "singles": [...], "songs_id"} — все релизы, а не первые 10."""
    def load():
        yt = ytm_client()
        if yt is None:
            return None
        a = yt.get_artist(browse_id)
        out = {"name": a.get("name", ""), "albums": [], "singles": [],
               "songs_id": (a.get("songs") or {}).get("browseId")}
        for sec, kind in (("albums", "Альбом"), ("singles", "Сингл")):
            s = a.get(sec) or {}
            items = s.get("results") or []
            if s.get("browseId") and s.get("params"):
                try:                               # полный список, если у артиста больше 10 релизов
                    items = yt.get_artist_albums(s["browseId"], s["params"], limit=None) or items
                except Exception as e:             # noqa: BLE001
                    _log("ytm artist albums:", e)
            out[sec] = [_release(r, kind) for r in items if r.get("browseId")]
        return out
    try:
        return _disco_cache(f"disco_{browse_id}", load, force) or {}
    except Exception as e:                         # noqa: BLE001
        _log("ytm discography:", e)
        return {}


def ytm_album(browse_id: str, force=False) -> dict:
    """Альбом: название, год, тип, обложка и треки (с номером и прослушиваниями)."""
    def load():
        yt = ytm_client()
        if yt is None:
            return None
        alb = yt.get_album(browse_id)
        name = alb.get("title", "")
        cover = _thumb(alb.get("thumbnails"))
        aart = ", ".join(x.get("name", "") for x in (alb.get("artists") or []))
        tracks = []
        for s in alb.get("tracks") or []:
            if not s.get("videoId"):
                continue
            t = _ytm_song({"videoId": s["videoId"], "title": s.get("title", ""),
                           "artists": s.get("artists") or alb.get("artists") or [], "thumbnails": alb.get("thumbnails"),
                           "duration_seconds": s.get("duration_seconds") or _len_to_sec(s.get("duration", "")),
                           "album": {"name": name}, "views": s.get("views")})
            u = _ui_track(t)
            u["album"], u["thumb_url"], u["num"] = name, cover, s.get("trackNumber") or len(tracks) + 1
            if not u["artist"]:
                u["artist"] = aart
            tracks.append(u)
        return {"title": name, "artist": aart, "year": str(alb.get("year") or ""), "type": alb.get("type") or "",
                "thumb_url": cover, "tracks": tracks, "browseId": browse_id}
    try:
        return _disco_cache(f"album_{browse_id}", load, force) or {}
    except Exception as e:                         # noqa: BLE001
        _log("ytm album:", e)
        return {}


def ytm_all_songs(browse_id: str, force=False) -> list:
    """Все песни исполнителя (плейлист «Песни» на его странице YouTube Music)."""
    def load():
        d = ytm_discography(browse_id)
        sid = d.get("songs_id")
        yt = ytm_client()
        if yt is None or not sid:
            return None
        pl = yt.get_playlist(sid, limit=None)
        out = [_ui_track(_ytm_song(s)) for s in (pl.get("tracks") or []) if s.get("videoId")]
        for t in out:
            t["artist"] = t["artist"] or d.get("name", "")
        return {"tracks": out}
    try:
        return (_disco_cache(f"songs_{browse_id}", load, force) or {}).get("tracks", [])
    except Exception as e:                         # noqa: BLE001
        _log("ytm all songs:", e)
        return []


# ── SoundCloud: дискография, когда исполнителя нет на YouTube Music ── #

def sc_artist_id(name: str):
    """id исполнителя на SoundCloud по имени: лучшее совпадение имени, при равенстве — больше подписчиков."""
    try:
        from track_identity import similarity
    except Exception:                              # noqa: BLE001
        def similarity(a, b):
            return 1.0 if (a or "").lower() == (b or "").lower() else 0.0
    best, bs = None, 0.0
    for u in sc_search_users(name, 10):
        s = max(similarity(name, u.get("name", "")), similarity(name, u.get("full_name", "") or ""))
        s += min(0.05, (u.get("followers") or 0) / 2_000_000)
        if s > bs:
            best, bs = u, s
    return best["id"] if best and bs >= 0.8 else None


_SC_KIND = {"album": "Альбом", "ep": "EP", "single": "Сингл", "compilation": "Сборник", "playlist": "Плейлист"}


def _sc_release(p) -> dict:
    kind = str(p.get("set_type") or ("album" if p.get("is_album") else "playlist")).lower()
    date = str(p.get("release_date") or p.get("display_date") or p.get("created_at") or "")
    return {"title": p.get("title", ""), "year": date[:4], "browseId": None, "sc_id": p.get("id"),
            "type": _SC_KIND.get(kind, "Альбом"), "kind": kind, "src": "sc",
            "thumb_url": _sc_art(p.get("artwork_url") or (p.get("user") or {}).get("avatar_url", ""), "t500x500")}


def sc_discography(user_id, force=False) -> dict:
    """{"name", "albums", "singles", "src": "sc"}: альбомы и EP пользователя SoundCloud; если их нет —
    его треки как синглы (на SoundCloud многие выкладывают только отдельные треки)."""
    def load():
        u = _sc_get(f"/users/{user_id}") or {}
        alb = _sc_get(f"/users/{user_id}/albums", {"limit": 50, "linked_partitioning": 1}) or {}
        rels = [_sc_release(p) for p in alb.get("collection", []) if p.get("id")]
        albums = [r for r in rels if r["kind"] in ("album", "compilation")]
        eps = [r for r in rels if r["kind"] not in ("album", "compilation")]
        singles = list(eps)
        if len(singles) < 4:
            tr = _sc_get(f"/users/{user_id}/tracks", {"limit": 40, "linked_partitioning": 1}) or {}
            for t in tr.get("collection", []):
                if not t.get("id") or _sc_is_snip(t):
                    continue
                st = _sc_track(t)
                singles.append({"title": st["title"], "year": str(t.get("display_date") or t.get("created_at") or "")[:4],
                                "browseId": None, "sc_track": _ui_track(st), "type": "Трек", "kind": "track",
                                "src": "sc", "thumb_url": st["cover_url"]})
        return {"name": u.get("username", ""), "albums": albums, "singles": singles, "src": "sc",
                "sc_id": user_id}
    try:
        return _disco_cache(f"scdisco_{user_id}", load, force) or {}
    except Exception as e:                         # noqa: BLE001
        _log("sc discography:", e)
        return {}


def sc_album(playlist_id, force=False) -> dict:
    """Альбом/EP SoundCloud с треками (у неполных треков в ответе только id — дозапрашиваем)."""
    def load():
        p = _sc_get(f"/playlists/{playlist_id}") or {}
        if not p:
            return None
        raw = p.get("tracks") or []
        full = [t for t in raw if t.get("title")]
        missing = [str(t["id"]) for t in raw if not t.get("title") and t.get("id")]
        for i in range(0, len(missing), 40):
            got = _sc_get("/tracks", {"ids": ",".join(missing[i:i + 40])}) or []
            full += got if isinstance(got, list) else []
        order = {t.get("id"): k for k, t in enumerate(raw)}
        full.sort(key=lambda t: order.get(t.get("id"), 9999))
        r = _sc_release(p)
        tracks = []
        for k, t in enumerate(full):
            if _sc_is_snip(t):
                continue
            u = _ui_track(_sc_track(t))
            u["album"], u["num"] = r["title"], k + 1
            u["thumb_url"] = u.get("thumb_url") or r["thumb_url"]
            tracks.append(u)
        return {"title": r["title"], "artist": (p.get("user") or {}).get("username", ""), "year": r["year"],
                "type": r["type"], "thumb_url": r["thumb_url"], "tracks": tracks, "sc_id": playlist_id}
    try:
        return _disco_cache(f"scalbum_{playlist_id}", load, force) or {}
    except Exception as e:                         # noqa: BLE001
        _log("sc album:", e)
        return {}


def sc_all_songs(user_id, force=False, limit=500) -> list:
    """Все треки пользователя SoundCloud (постранично, без 30-секундных превью)."""
    def load():
        out, nxt = [], None
        params = {"limit": 100, "linked_partitioning": 1}
        j = _sc_get(f"/users/{user_id}/tracks", params) or {}
        while True:
            for t in j.get("collection", []):
                if t.get("id") and not _sc_is_snip(t):
                    out.append(_ui_track(_sc_track(t)))
            nxt = j.get("next_href")
            if not nxt or len(out) >= limit:
                break
            try:
                cid = sc_client_id()
                r = requests.get(nxt, params={"client_id": cid}, headers=UA, timeout=10)
                j = r.json() if r.status_code == 200 else {}
            except Exception:                      # noqa: BLE001
                break
        return {"tracks": out}
    try:
        return (_disco_cache(f"scsongs_{user_id}", load, force) or {}).get("tracks", [])
    except Exception as e:                         # noqa: BLE001
        _log("sc all songs:", e)
        return []


def _len_to_sec(s) -> int:
    try:
        parts = [int(x) for x in str(s).split(":")]
    except ValueError:
        return 0
    sec = 0
    for p in parts:
        sec = sec * 60 + p
    return sec


def radio(seed_title: str, seed_artist: str, limit: int = 25) -> list:
    """Похожие треки на основе одного («радио» YouTube Music, иначе
    «похожие» SoundCloud). Формат — как у треков интерфейса."""
    q = f"{seed_artist} {seed_title}".strip()
    yt = ytm_client()
    if yt is not None:
        try:
            found = yt.search(q, filter="songs", limit=3)
            vid = next((s.get("videoId") for s in found if s.get("videoId")), None)
            if vid:
                wp = yt.get_watch_playlist(videoId=vid, radio=True, limit=limit)
                out = []
                for s in (wp or {}).get("tracks", [])[1:]:          # первый — сам «семя»
                    if not s.get("videoId"):
                        continue
                    t = _ytm_song({"videoId": s["videoId"], "title": s.get("title", ""),
                                   "artists": s.get("artists") or [],
                                   "thumbnails": s.get("thumbnail") or s.get("thumbnails") or [],
                                   "duration_seconds": _len_to_sec(s.get("length", "")),
                                   "album": s.get("album") if isinstance(s.get("album"), dict) else None})
                    out.append(_ui_track(t))
                if out:
                    return out
        except Exception as e:                     # noqa: BLE001
            _log("ytm radio:", e)
    try:
        seeds = sc_search(q, 3)
        if seeds:
            j = _sc_get(f"/tracks/{seeds[0]['id']}/related", {"limit": limit})
            return [_ui_track(_sc_track(t)) for t in (j or {}).get("collection", [])]
    except Exception as e:                         # noqa: BLE001
        _log("sc related:", e)
    return []


# ══════════════════════════════════════════════════════════════════════════ #
#  Spotify: список треков плейлиста/альбома по ссылке
# ══════════════════════════════════════════════════════════════════════════ #

def _spotify_id(url: str):
    m = re.search(r"(playlist|album|track)[/:]([A-Za-z0-9]{16,})", url or "")
    return (m.group(1), m.group(2)) if m else (None, None)


def _spotify_api(kind, sid, client_id, secret):
    import base64
    tok = requests.post("https://accounts.spotify.com/api/token", data={"grant_type": "client_credentials"},
                        headers={"Authorization": "Basic " + base64.b64encode(
                            f"{client_id}:{secret}".encode()).decode()}, timeout=10).json().get("access_token")
    if not tok:
        return None
    return _spotify_fetch(kind, sid, tok)


def _spotify_fetch(kind, sid, tok):
    """Весь плейлист/альбом через Web API (постранично, без лимита в 100)."""
    h = {"Authorization": f"Bearer {tok}", **UA}
    base = "https://api.spotify.com/v1"
    tracks = []
    if kind == "playlist":
        meta = requests.get(f"{base}/playlists/{sid}", params={"fields": "name"}, headers=h, timeout=10).json()
        if "name" not in meta:
            return None
        name = meta.get("name") or "Spotify"
        url = f"{base}/playlists/{sid}/tracks?limit=100"
        waits = 0
        while url:
            r = requests.get(url, headers=h, timeout=20)
            if r.status_code == 429 and waits < 8:        # лимит запросов — ждём и повторяем
                waits += 1
                try:
                    pause = int(r.headers.get("Retry-After", "2") or 2)
                except ValueError:
                    pause = 2
                time.sleep(min(10, max(1, pause)))
                continue
            j = r.json()
            if "items" not in j:
                return None if not tracks else (name, tracks)
            for it in j["items"]:
                t = it.get("track") or {}
                if t.get("name"):
                    alb = t.get("album") or {}
                    tracks.append({"title": t["name"],
                                   "artist": ", ".join(a.get("name", "") for a in t.get("artists", [])),
                                   "duration": int((t.get("duration_ms") or 0) / 1000),
                                   "album": alb.get("name", ""),
                                   "cover": ((alb.get("images") or [{}])[0]).get("url", "")})
            url = j.get("next")
    elif kind == "album":
        j = requests.get(f"{base}/albums/{sid}", headers=h, timeout=10).json()
        if "name" not in j:
            return None
        name = j.get("name") or "Spotify"
        acov = ((j.get("images") or [{}])[0]).get("url", "")
        page = j.get("tracks") or {}
        while True:
            for t in page.get("items", []):
                tracks.append({"title": t.get("name", ""),
                               "artist": ", ".join(a.get("name", "") for a in t.get("artists", [])),
                               "duration": int((t.get("duration_ms") or 0) / 1000),
                               "album": name, "cover": acov})
            nxt = page.get("next")
            if not nxt:
                break
            page = requests.get(nxt, headers=h, timeout=15).json()
    else:
        t = requests.get(f"{base}/tracks/{sid}", headers=h, timeout=10).json()
        name = t.get("name") or "Spotify"
        tracks.append({"title": t.get("name", ""),
                       "artist": ", ".join(a.get("name", "") for a in t.get("artists", [])),
                       "duration": int((t.get("duration_ms") or 0) / 1000)})
    return name, tracks


def _find_key(obj, key):
    """Первое значение ключа key где угодно во вложенном JSON."""
    stack = [obj]
    while stack:
        o = stack.pop()
        if isinstance(o, dict):
            if key in o and isinstance(o[key], str) and o[key]:
                return o[key]
            stack.extend(o.values())
        elif isinstance(o, list):
            stack.extend(o)
    return ""


def _spotify_embed_data(kind, sid):
    r = requests.get(f"https://open.spotify.com/embed/{kind}/{sid}", headers=UA, timeout=12)
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', r.text, re.S)
    if not m:
        return None
    return json.loads(m.group(1))


def _spotify_embed(kind, sid, data=None):
    data = data if data is not None else _spotify_embed_data(kind, sid)
    if not data:
        return None
    ent = (((data.get("props") or {}).get("pageProps") or {}).get("state") or {}).get("data", {}).get("entity") or {}
    name = ent.get("name") or ent.get("title") or "Spotify"
    tracks = []
    acov = ""
    try:
        srcs = ((ent.get("coverArt") or {}).get("sources") or ent.get("images") or [])
        if srcs:
            acov = max(srcs, key=lambda x: x.get("width") or 0).get("url", "")
    except Exception:                              # noqa: BLE001
        acov = ""
    for t in ent.get("trackList") or []:
        tracks.append({"title": t.get("title", ""), "artist": t.get("subtitle", ""),
                       "duration": int((t.get("duration") or 0) / 1000),
                       "album": name if kind == "album" else "",
                       "cover": acov if kind == "album" else ""})
    if not tracks and kind == "track":
        tracks.append({"title": ent.get("name") or ent.get("title", ""),
                       "artist": ", ".join(a.get("name", "") for a in ent.get("artists") or []),
                       "duration": int((ent.get("duration") or 0) / 1000)})
    return name, tracks


def _spotify_free(kind, sid):
    """Полный плейлист без ключей: SpotipyFree (идёт вместе со spotdl) листает
    его постранично через веб-плеер Spotify, лимита в 100 треков нет."""
    if kind != "playlist":
        return None
    from SpotipyFree import Spotify
    sp = Spotify()
    name = "Spotify"
    try:
        name = (sp.playlist(sid) or {}).get("name") or name
    except Exception as e:                         # noqa: BLE001
        _log("spotipyfree name:", e)
    tracks = []
    for it in (sp.playlist_items(sid) or {}).get("items", []):
        t = it.get("track") or {}
        if t.get("type") == "track" and t.get("name"):
            tracks.append({"title": t["name"],
                           "artist": ", ".join(a.get("name", "") for a in t.get("artists") or []),
                           "duration": int((t.get("duration_ms") or 0) / 1000),
                           "album": "", "cover": ""})
    return name, tracks


def _spotdl_keys():
    """Ключи Spotify API, которые поставляются вместе с установленным spotdl."""
    try:
        from spotdl.utils.config import DEFAULT_CONFIG
        cid, sec = DEFAULT_CONFIG.get("client_id"), DEFAULT_CONFIG.get("client_secret")
        if cid and sec:
            return cid, sec
    except Exception:                              # noqa: BLE001
        pass
    return None


def spotify_tracks(url: str, client_id: str = "", secret: str = ""):
    """(название, [{title, artist, duration, album, cover}]) по ссылке на
    плейлист/альбом/трек. Всегда старается получить ВЕСЬ список:
      1) ваши ключи Spotify API (если указаны);
      2) ключи из установленного spotdl;
      3) анонимный токен со страницы встраиваемого плеера Spotify;
      4) только если всё это не сработало — список со встраиваемой
         страницы (там Spotify отдаёт не больше ~100 треков)."""
    kind, sid = _spotify_id(url)
    if not sid:
        raise ValueError("Это не ссылка на плейлист, альбом или трек Spotify")
    best = None

    def better(r):
        nonlocal best
        if r and r[1] and (best is None or len(r[1]) > len(best[1])):
            best = r

    keys = []
    if client_id and secret:
        keys.append((client_id, secret))
    sk = _spotdl_keys()
    if sk and sk not in keys:
        keys.append(sk)
    for cid, sec in keys:
        try:
            r = _spotify_api(kind, sid, cid, sec)
            better(r)
            if r and r[1]:
                _log(f"spotify api: {len(r[1])} треков")
                return r
        except Exception as e:                     # noqa: BLE001
            _log("spotify api:", e)
    try:
        r = _spotify_free(kind, sid)
        better(r)
        if r and r[1]:
            _log(f"spotipyfree: {len(r[1])} треков")
            return r
    except Exception as e:                         # noqa: BLE001
        _log("spotipyfree:", e)
    data = None
    try:
        data = _spotify_embed_data(kind, sid)
    except Exception as e:                         # noqa: BLE001
        _log("spotify embed:", e)
    if data:
        tok = _find_key(data, "accessToken")
        if tok:
            try:
                r = _spotify_fetch(kind, sid, tok)
                better(r)
                if r and r[1]:
                    _log(f"spotify embed token: {len(r[1])} треков")
            except Exception as e:                 # noqa: BLE001
                _log("spotify embed token:", e)
        try:
            better(_spotify_embed(kind, sid, data))
        except Exception as e:                     # noqa: BLE001
            _log("spotify embed list:", e)
    if not best:
        raise RuntimeError("Spotify не отдал список треков (плейлист приватный или ссылка неверная)")
    return best
