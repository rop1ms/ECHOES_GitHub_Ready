"""Перенос плейлистов с площадок по ссылке: SoundCloud, YouTube Music, YouTube, Deezer, Apple Music
(Spotify, Яндекс Музыка и VK — свои модули: online_search.spotify_tracks, service_playlist_import).

fetch(provider, url) → (название, {"tracks": [...], "warning": ""}); трек — {title, artist, duration, album, cover}
и, если он сам лежит на площадке, откуда качает ECHOES (SoundCloud, YouTube), ещё url/source — тогда его не ищут
заново, а скачивают по этой ссылке. detect(url) — площадка по ссылке (для общего окна «Перенести плейлист»).
Без входа в аккаунты и без ключей: только публичные плейлисты.
"""
from __future__ import annotations

import html
import json
import re
import urllib.parse

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36", "Accept-Language": "ru,en;q=0.8"}
LIMIT = 1500
PROVIDERS = {                                    # ключ: (название, пример ссылки)
    "spotify": ("Spotify", "https://open.spotify.com/playlist/…"),
    "yandex": ("Яндекс Музыка", "https://music.yandex.ru/users/…/playlists/…"),
    "vk": ("VK Музыка", "https://vk.com/music/playlist/…"),
    "soundcloud": ("SoundCloud", "https://soundcloud.com/…/sets/…"),
    "ytmusic": ("YouTube Music", "https://music.youtube.com/playlist?list=…"),
    "youtube": ("YouTube", "https://www.youtube.com/playlist?list=…"),
    "deezer": ("Deezer", "https://www.deezer.com/playlist/…"),
    "apple": ("Apple Music", "https://music.apple.com/…/playlist/…"),
}
OWN = ("soundcloud", "ytmusic", "youtube", "deezer", "apple")      # площадки этого модуля


def detect(url: str) -> str:
    host = (urllib.parse.urlsplit(url.strip()).hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    if host.endswith("spotify.com") or host == "spotify.link":
        return "spotify"
    if host.startswith("music.yandex."):
        return "yandex"
    if host in ("vk.com", "vk.ru", "m.vk.com", "m.vk.ru", "vk.cc"):
        return "vk"
    if host.endswith("soundcloud.com") or host == "on.soundcloud.com":
        return "soundcloud"
    if host == "music.youtube.com":
        return "ytmusic"
    if host in ("youtube.com", "m.youtube.com", "youtu.be"):
        return "youtube"
    if host.endswith("deezer.com") or host in ("deezer.page.link", "link.deezer.com"):
        return "deezer"
    if host in ("music.apple.com", "itunes.apple.com"):
        return "apple"
    return ""


def fetch(provider: str, url: str):
    provider = provider if provider in OWN else detect(url)
    if provider not in OWN:
        raise ValueError("Ссылка не похожа на плейлист SoundCloud, YouTube, Deezer или Apple Music")
    fn = {"soundcloud": _soundcloud, "ytmusic": _youtube, "youtube": _youtube, "deezer": _deezer,
          "apple": _apple}[provider]
    name, tracks, warning = fn(url.strip())
    tracks = [t for t in tracks if t.get("title")][:LIMIT]
    if not tracks:
        raise RuntimeError("Площадка не отдала треки: плейлист приватный, пустой или ссылка неверная")
    return name or PROVIDERS[provider][0], {"tracks": tracks, "warning": warning}


def _track(title, artist, duration=0, album="", cover="", url="", source=""):
    t = {"title": str(title or "").strip(), "artist": str(artist or "").strip(), "duration": int(duration or 0),
         "album": str(album or ""), "cover": str(cover or "")}
    if url:
        t["url"], t["source"] = url, source
    return t


# ── SoundCloud: /resolve по ссылке (плейлист, альбом, трек, пользователь, лайки) ──

def _soundcloud(url):
    import online_search as os_
    if "on.soundcloud.com" in url:                               # короткая ссылка из приложения
        url = requests.get(url, headers=UA, timeout=15, allow_redirects=True).url
    obj = os_._sc_get("/resolve", {"url": url.split("?")[0]})
    if not obj:
        raise RuntimeError("SoundCloud не нашёл эту ссылку")
    kind = obj.get("kind")
    if kind == "playlist":
        d = os_.sc_album(obj["id"], force=True)
        tracks = [_track(t["title"], t["artist"], t.get("duration"), d.get("title", ""), t.get("thumb_url"),
                         t.get("url"), "sc") for t in d.get("tracks", [])]
        skipped = len(obj.get("tracks") or []) - len(tracks)
        warn = f"Пропущено превью (Go+) без полной версии: {skipped}" if skipped > 0 else ""
        return obj.get("title", ""), tracks, warn
    if kind == "track":
        t = os_._sc_track(obj)
        return t["title"], [_track(t["title"], t["artist"], t["duration"], "", t["cover_url"], t["url"], "sc")], ""
    if kind == "user":
        songs = os_.sc_all_songs(obj["id"], force=True)
        return obj.get("username", ""), [_track(t["title"], t["artist"], t.get("duration"), t.get("album"),
                                                t.get("thumb_url"), t.get("url"), "sc") for t in songs], ""
    raise RuntimeError("Это не плейлист, трек или страница SoundCloud")


# ── YouTube Music / YouTube: get_playlist (альбомы OLAK… тоже плейлисты), иначе yt-dlp ──

def _list_id(url):
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    if q.get("list"):
        return q["list"][0]
    m = re.search(r"/browse/(MPREb_[\w-]+|VL[\w-]+)", url)
    return m.group(1) if m else ""


def _youtube(url):
    import online_search as os_
    lid = _list_id(url)
    if not lid:
        raise ValueError("В ссылке нет плейлиста (нужен адрес вида …/playlist?list=…)")
    yt = os_.ytm_client()
    if yt is not None:
        try:
            if lid.startswith("MPREb_"):                         # страница альбома YouTube Music
                d = os_.ytm_album(lid, force=True)
                return d.get("title", ""), [_track(t["title"], t["artist"], t.get("duration"), d.get("title", ""),
                                                   t.get("thumb_url"), t.get("url"), "ytm")
                                            for t in d.get("tracks", [])], ""
            pl = yt.get_playlist(lid[2:] if lid.startswith("VL") else lid, limit=LIMIT)
            tracks = []
            for s in pl.get("tracks") or []:
                vid = s.get("videoId")
                if not vid or s.get("isAvailable") is False:
                    continue
                t = os_._ytm_song(s)
                tracks.append(_track(_clean(t["title"]), t["artist"], t["duration"], t.get("album"), t["cover_url"],
                                     t["url"], "ytm"))
            if tracks:
                gone = len(pl.get("tracks") or []) - len(tracks)
                return pl.get("title", ""), tracks, (f"Недоступно на YouTube: {gone}" if gone > 0 else "")
        except Exception as e:                                   # noqa: BLE001
            os_._log("ytm playlist:", e)
    import yt_dlp                                                # запасной путь — обычный YouTube
    with yt_dlp.YoutubeDL({"extract_flat": "in_playlist", "quiet": True, "skip_download": True,
                           "noprogress": True, "playlistend": LIMIT}) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/playlist?list={lid}", download=False) or {}
    tracks = []
    for e in info.get("entries") or []:
        if not e or not e.get("id"):
            continue
        title, artist = _split_title(e.get("title", ""), e.get("channel") or e.get("uploader") or "")
        tracks.append(_track(title, artist, e.get("duration"), "", "", f"https://www.youtube.com/watch?v={e['id']}",
                             "yt"))
    return info.get("title", ""), tracks, ""


_TAGS = re.compile(r"\s*[\(\[](official|music|lyric|audio|video|visuali[sz]er|клип|премьера|mood video)[^\)\]]*[\)\]]",
                   re.I)


def _clean(title):
    """«Песня (Official Music Video)» → «Песня»."""
    return _TAGS.sub("", title or "").strip() or title


def _split_title(title, channel):
    """«Исполнитель - Название (Official Video)» → (название, исполнитель)."""
    t = _clean(title)
    m = re.match(r"\s*(.+?)\s+[-–—]\s+(.+)", t)
    if m:
        return m.group(2).strip(), m.group(1).strip()
    return t.strip(), re.sub(r"\s*-\s*Topic$", "", channel or "")


# ── Deezer: открытое API (плейлист, альбом, трек) ──

def _deezer(url):
    if "page.link" in url or "link.deezer.com" in url:
        url = requests.get(url, headers=UA, timeout=15, allow_redirects=True).url
    m = re.search(r"deezer\.com/(?:\w{2}/)?(playlist|album|track)/(\d+)", url)
    if not m:
        raise ValueError("Нужна ссылка на плейлист или альбом Deezer")
    kind, did = m.groups()
    base = f"https://api.deezer.com/{kind}/{did}"
    j = requests.get(base, headers=UA, timeout=20).json()
    if j.get("error"):
        raise RuntimeError(f"Deezer: {j['error'].get('message', 'ошибка')}")
    if kind == "track":
        return j.get("title", ""), [_track(j.get("title"), (j.get("artist") or {}).get("name"), j.get("duration"),
                                           (j.get("album") or {}).get("title"),
                                           (j.get("album") or {}).get("cover_xl"))], ""
    items = list((j.get("tracks") or {}).get("data") or [])
    nxt = (j.get("tracks") or {}).get("next")
    while nxt and len(items) < LIMIT:                            # длинные плейлисты — постранично
        page = requests.get(nxt, headers=UA, timeout=20).json()
        items += page.get("data") or []
        nxt = page.get("next")
    alb = j.get("title", "") if kind == "album" else ""
    cover = j.get("cover_xl") if kind == "album" else ""
    tracks = [_track(t.get("title"), (t.get("artist") or {}).get("name"), t.get("duration"),
                     alb or (t.get("album") or {}).get("title"), cover or (t.get("album") or {}).get("cover_xl"))
              for t in items]
    return j.get("title", ""), tracks, ""


# ── Apple Music: страница плейлиста/альбома отдаёт список в JSON-LD и в данных страницы ──

def _iso_dur(s):
    m = re.match(r"P(?:T)?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", str(s or ""))
    return (int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0)) if m else 0


def _apple(url):
    r = requests.get(url, headers=UA, timeout=25)
    r.raise_for_status()
    page = r.content.decode("utf-8", "replace")
    name, tracks = "", []
    for raw in re.findall(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', page, re.S):
        try:
            d = json.loads(html.unescape(raw))
        except ValueError:
            continue
        for obj in d if isinstance(d, list) else [d]:
            if not isinstance(obj, dict) or obj.get("@type") not in ("MusicPlaylist", "MusicAlbum"):
                continue
            name = obj.get("name", "") or name
            by = obj.get("byArtist")
            album_artist = ((by[0] if isinstance(by, list) and by else by) or {}).get("name", "")
            album = name if obj.get("@type") == "MusicAlbum" else ""
            for t in obj.get("track") or []:
                if not isinstance(t, dict):
                    continue
                tb = t.get("byArtist")
                artist = ((tb[0] if isinstance(tb, list) and tb else tb) or {}).get("name", "") or album_artist
                tracks.append(_track(t.get("name"), artist, _iso_dur(t.get("duration")), album))
    # исполнители у треков плейлиста — в данных приложения на странице (title + artistName)
    artists, extra = {}, []
    m = re.search(r'<script[^>]+id="serialized-server-data"[^>]*>(.*?)</script>', page, re.S)
    if m:
        try:
            data = json.loads(html.unescape(m.group(1)))
        except ValueError:
            data = None
        seen = set()

        def walk(x):
            if isinstance(x, dict):
                t, a = x.get("title"), x.get("artistName")
                if isinstance(t, str) and isinstance(a, str) and t and (t, a) not in seen:
                    seen.add((t, a))
                    artists.setdefault(t, a)
                    extra.append(_track(t, a, int((x.get("duration") or 0) / 1000)))
                for v in x.values():
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)
        walk(data)
    for t in tracks:
        if not t["artist"]:
            t["artist"] = artists.get(t["title"], "")
    if not tracks:
        tracks = extra
    if not name:
        mt = re.search(r'<meta property="og:title" content="([^"]+)"', page)
        name = html.unescape(mt.group(1)) if mt else ""
    warn = "Apple Music показал не весь список — остальное можно загрузить файлом" if len(tracks) in (100, 300) else ""
    return name, tracks, warn
