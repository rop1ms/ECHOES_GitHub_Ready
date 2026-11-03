# lyrics.py
"""
Поиск текстов песен v3 — в первую очередь СИНХРОНИЗИРОВАННЫХ (LRC с таймингами).

Как работает:
  1. track_identity.parse_candidates() разбирает теги и имя файла на гипотезы
     (исполнитель, название): «Title - Artist - SoundLoadMate», артист в
     названии вместо тега, номера дорожек, приписки YouTube и т.д.
  2. Параллельно опрашиваются источники с таймингами:
        LRCLIB (get по длительности + search), Musixmatch (desktop API),
        NetEase Cloud Music, Kugou, + библиотека syncedlyrics, если стоит.
     Каждый найденный вариант проверяется match_score(): похожесть названия,
     исполнителя и ДЛИТЕЛЬНОСТИ — так не подтягивается чужая песня или
     лайв-версия с другими таймингами.
  3. Если тайминги не нашлись нигде — обычный текст: LRCLIB plain, Musixmatch,
     Genius (публичный поиск, без токена; с токеном — ещё и API), lyrics.ovh.

Публичный API (совместим со старым):
    fetch_lyrics(title, artist, genius_token="", duration=0, path="", album="")
        -> (text | None, source)
    fetch_lyrics_ex(...) -> dict(text, source, synced, score, artist, title)
    sanitize_title(title), scrape_genius_lyrics(url), _clean_lyrics_text(text)
"""
from __future__ import annotations

import data_home
import base64
import html
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED
from pathlib import Path

import requests

from track_identity import (parse_candidates, match_score, basic_clean, strip_extras,
                            primary_artist, similarity, norm)

GENIUS_API = "https://api.genius.com"
LRCLIB = "https://lrclib.net/api"
UA = "NeonPlayer/5.0 (https://github.com/neon-player)"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

ACCEPT = 0.72          # минимальная уверенность, что это та самая песня
STRONG = 1.02          # «точно она» (название+артист+длительность) — можно не ждать остальных
INSTRUMENTAL_TEXT = "[Инструментал]\nВ этом треке нет слов."

_DATA_DIR = data_home.root()


def _log(msg):
    try:
        print(f"[lyrics] {msg}")
    except Exception:
        pass


# ------------------------------------------------------------------ #
#  Совместимость: старый sanitize_title                               #
# ------------------------------------------------------------------ #

def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def sanitize_title(title: str) -> str:
    """Удаляет рекламный мусор из названия трека."""
    t = basic_clean(title or "")
    return t or (title or "")


# ------------------------------------------------------------------ #
#  Результат поиска                                                   #
# ------------------------------------------------------------------ #

class Hit:
    __slots__ = ("text", "synced", "source", "score", "artist", "title", "duration", "instrumental")

    def __init__(self, text, synced, source, score, artist="", title="", duration=0.0,
                 instrumental=False):
        self.text = text
        self.synced = synced
        self.source = source
        self.score = score
        self.artist = artist
        self.title = title
        self.duration = duration
        self.instrumental = instrumental

    def __repr__(self):
        return (f"Hit({self.source}, synced={self.synced}, score={self.score:.2f}, "
                f"{self.artist!r} - {self.title!r})")


# ------------------------------------------------------------------ #
#  LRC: проверка и чистка                                             #
# ------------------------------------------------------------------ #

_TS = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")
_CREDIT = re.compile(
    r"^\s*(?:作词|作詞|作曲|编曲|編曲|制作人|製作人|制作|監製|监制|混音|母带|母帶|录音|錄音|"
    r"和声|和聲|吉他|贝斯|鼓|键盘|弦乐|出品|发行|發行|企划|企劃|统筹|OP|SP|ISRC|"
    r"词|詞|曲|演唱|原唱|翻唱|配唱|Lyrics?\s*by|Lyricist|Written\s*by|Writer|Composer|"
    r"Composed\s*by|Arranged\s*by|Arranger|Produced\s*by|Producer|Mixed\s*by|Mastered\s*by|"
    r"Mixing|Mastering|Recording|Vocals?\s*by)\s*[:：]", re.I)
_NETEASE_PURE = re.compile(r"纯音乐|純音樂|请欣赏|請欣賞|此歌曲为没有填词的纯音乐", re.I)


def _ts_seconds(m) -> float:
    mm, ss, frac = m.group(1), m.group(2), m.group(3) or "0"
    return int(mm) * 60 + int(ss) + int(frac) / (10 ** len(frac))


def lrc_stats(text: str):
    """(кол-во строк с таймингом и текстом, последний таймстемп)."""
    n, last = 0, 0.0
    for line in (text or "").splitlines():
        ms = list(_TS.finditer(line))
        if not ms:
            continue
        body = _TS.sub("", line).strip()
        body = re.sub(r"<\d{1,3}:\d{2}\.\d{2,3}>", "", body).strip()
        for m in ms:
            last = max(last, _ts_seconds(m))
        if body:
            n += len(ms)
    return n, last


def is_synced(text: str) -> bool:
    n, _ = lrc_stats(text)
    return n >= 3


def clean_lrc(text: str, artist: str = "", title: str = "") -> str:
    """Убирает титры (作词/作曲, Lyrics by…), служебные теги, «Артист - Название»
    в начале (Kugou), BOM/CRLF; сортирует строки по времени."""
    if not text:
        return ""
    text = text.replace("﻿", "").replace("\r\n", "\n").replace("\r", "\n")
    out = []
    a_n, t_n = norm(artist), norm(title)
    for line in text.split("\n"):
        raw = line.strip()
        if not raw:
            continue
        if re.match(r"^\[(?:id|hash|sign|qq|total|offset|by|kana|language|re|ve|length):", raw, re.I):
            continue
        ms = list(_TS.finditer(raw))
        body = _TS.sub("", raw).strip()
        if ms and _CREDIT.match(body):
            continue
        if ms and body and _ts_seconds(ms[0]) < 1.5 and a_n and t_n:
            nb = norm(body)
            if t_n in nb and (a_n in nb or " - " in body):
                continue                              # «Linkin Park - Faint» в 0:00
        if ms and body and _NETEASE_PURE.search(body):
            continue
        out.append(raw)

    # сортировка по первому таймстемпу (у некоторых источников строки вперемешку)
    def key(l):
        m = _TS.search(l)
        return _ts_seconds(m) if m else -1.0
    head = [l for l in out if not _TS.search(l)]
    body = sorted((l for l in out if _TS.search(l)), key=key)
    return "\n".join(head + body).strip()


def _plausible_synced(text: str, duration: float) -> bool:
    n, last = lrc_stats(text)
    if n < 3:
        return False
    if duration and duration > 20 and last > duration + 12:
        return False                                  # тайминги от более длинной версии
    return True


def _clean_plain(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Musixmatch-обрезка для бесплатного API
    text = re.sub(r"\*{5,}.*?This Lyrics is NOT for Commercial use.*$", "", text, flags=re.S | re.I)
    text = re.sub(r"\.\.\.\s*\n\s*\*{3,}.*$", "", text, flags=re.S)
    return text.strip()


# ------------------------------------------------------------------ #
#  HTTP                                                               #
# ------------------------------------------------------------------ #

def _get(url, params=None, headers=None, timeout=7.0, **kw):
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    return requests.get(url, params=params, headers=h, timeout=timeout, **kw)


def _get_json(url, params=None, headers=None, timeout=7.0):
    try:
        r = _get(url, params=params, headers=headers, timeout=timeout)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception as e:
        _log(f"{url.split('/')[2]}: {e.__class__.__name__}")
        return None


def _queries(cands, n=3):
    """Пары (artist, title) для запросов: лучшие гипотезы + «голые» названия."""
    out, seen = [], set()
    for a, t in cands:
        for tt in (t, strip_extras(t)):
            k = (norm(a), norm(tt))
            if k in seen or not k[1]:
                continue
            seen.add(k)
            out.append((a, tt))
        if len(out) >= n:
            break
    return out[:n]


# ------------------------------------------------------------------ #
#  LRCLIB                                                             #
# ------------------------------------------------------------------ #

def _lrclib_hit(item, cands, duration):
    if not isinstance(item, dict):
        return None
    fa, ft = item.get("artistName") or "", item.get("trackName") or item.get("name") or ""
    fd = float(item.get("duration") or 0)
    sc = match_score(cands, fa, ft, fd, duration)
    if item.get("instrumental"):
        return Hit(INSTRUMENTAL_TEXT, False, "LRCLIB", sc, fa, ft, fd, instrumental=True)
    syn = item.get("syncedLyrics") or ""
    if syn and _plausible_synced(syn, duration):
        return Hit(clean_lrc(syn, fa, ft), True, "LRCLIB", sc, fa, ft, fd)
    pl = item.get("plainLyrics") or ""
    if pl.strip():
        return Hit(_clean_plain(pl), False, "LRCLIB", sc, fa, ft, fd)
    return None


def src_lrclib(cands, duration, album):
    hits, seen = [], set()

    def take(items):
        for it in items or []:
            if not isinstance(it, dict):
                continue
            iid = it.get("id")
            if iid in seen:
                continue
            seen.add(iid)
            h = _lrclib_hit(it, cands, duration)
            if h:
                hits.append(h)

    # 1) точный get: по длительности LRCLIB сам выбирает нужную версию
    for a, t in _queries([c for c in cands if c[0]], 2):
        p = {"artist_name": a, "track_name": t}
        if duration:
            p["duration"] = int(round(duration))
        if album:
            j = _get_json(f"{LRCLIB}/get", dict(p, album_name=album))
            if j:
                take([j])
        j = _get_json(f"{LRCLIB}/get", p)
        if j:
            take([j])
        if any(h.synced and h.score >= STRONG for h in hits):
            return hits

    # 2) поиск: по полям и свободным текстом (порядок артист/название не важен)
    for a, t in _queries(cands, 3):
        if a:
            take(_get_json(f"{LRCLIB}/search", {"track_name": t, "artist_name": a}))
            take(_get_json(f"{LRCLIB}/search", {"q": f"{a} {t}"}))
        else:
            take(_get_json(f"{LRCLIB}/search", {"q": t}))
        if any(h.synced and h.score >= STRONG for h in hits):
            break
    return hits


# ------------------------------------------------------------------ #
#  NetEase Cloud Music                                                #
# ------------------------------------------------------------------ #

_NE_HEADERS = {"Referer": "https://music.163.com/", "User-Agent": BROWSER_UA,
               "Cookie": "appver=2.0.2; os=pc"}


def src_netease(cands, duration, album):
    songs = {}
    for a, t in _queries(cands, 2):
        q = f"{a} {t}".strip()
        for url in ("https://music.163.com/api/cloudsearch/pc",
                    "https://music.163.com/api/search/get/web"):
            j = _get_json(url, {"s": q, "type": 1, "limit": 10, "offset": 0}, _NE_HEADERS)
            res = (j or {}).get("result")
            if isinstance(res, dict) and res.get("songs"):
                for s in res["songs"]:
                    songs.setdefault(s.get("id"), s)
                break                                 # второй адрес — только запасной

    scored = []
    for sid, s in songs.items():
        ar = s.get("ar") or s.get("artists") or []
        fa = ", ".join(x.get("name", "") for x in ar if isinstance(x, dict))
        ft = s.get("name") or ""
        fd = float(s.get("dt") or s.get("duration") or 0) / 1000.0
        sc = match_score(cands, fa, ft, fd, duration)
        scored.append((sc, sid, fa, ft, fd))
    scored.sort(reverse=True)

    hits = []
    for sc, sid, fa, ft, fd in scored[:3]:
        if sc < ACCEPT - 0.1:
            break
        j = _get_json("https://music.163.com/api/song/lyric",
                      {"id": sid, "lv": 1, "kv": 1, "tv": -1}, _NE_HEADERS)
        if not j:
            continue
        if j.get("nolyric") or j.get("pureMusic"):
            hits.append(Hit(INSTRUMENTAL_TEXT, False, "NetEase", sc, fa, ft, fd, instrumental=True))
            continue
        lrc = ((j.get("lrc") or {}).get("lyric") or "")
        if _NETEASE_PURE.search(lrc[:200]) and lrc_stats(lrc)[0] <= 2:
            hits.append(Hit(INSTRUMENTAL_TEXT, False, "NetEase", sc, fa, ft, fd, instrumental=True))
            continue
        lrc = clean_lrc(lrc, fa, ft)
        if lrc and _plausible_synced(lrc, duration):
            hits.append(Hit(lrc, True, "NetEase", sc, fa, ft, fd))
            if sc >= STRONG:
                break
    return hits


# ------------------------------------------------------------------ #
#  Kugou                                                              #
# ------------------------------------------------------------------ #

def src_kugou(cands, duration, album):
    hits, tried = [], set()
    for a, t in _queries(cands, 2):
        kw = f"{a} - {t}" if a else t
        p = {"ver": 1, "man": "yes", "client": "pc", "keyword": kw, "hash": ""}
        if duration:
            p["duration"] = int(duration * 1000)
        j = _get_json("https://lyrics.kugou.com/search", p, {"User-Agent": BROWSER_UA})
        cl = (j or {}).get("candidates") or []
        scored = []
        for c in cl:
            fa, ft = c.get("singer") or "", c.get("song") or ""
            fd = float(c.get("duration") or 0) / 1000.0
            scored.append((match_score(cands, fa, ft, fd, duration), c, fa, ft, fd))
        scored.sort(key=lambda x: -x[0])
        for sc, c, fa, ft, fd in scored[:2]:
            if sc < ACCEPT or (c.get("id"), c.get("accesskey")) in tried:
                continue
            tried.add((c.get("id"), c.get("accesskey")))
            d = _get_json("https://lyrics.kugou.com/download",
                          {"ver": 1, "client": "pc", "id": c.get("id"),
                           "accesskey": c.get("accesskey"), "fmt": "lrc", "charset": "utf8"},
                          {"User-Agent": BROWSER_UA})
            content = (d or {}).get("content") or ""
            if not content:
                continue
            try:
                lrc = base64.b64decode(content).decode("utf-8", "replace")
            except Exception:
                continue
            lrc = clean_lrc(lrc, fa, ft)
            if _plausible_synced(lrc, duration):
                hits.append(Hit(lrc, True, "Kugou", sc, fa, ft, fd))
        if any(h.score >= STRONG for h in hits):
            break
    return hits


# ------------------------------------------------------------------ #
#  Musixmatch (desktop API — тот же, что использует Spotify-плагин)   #
# ------------------------------------------------------------------ #

_MXM_ROOT = "https://apic-desktop.musixmatch.com/ws/1.1/"
_MXM_HEADERS = {"authority": "apic-desktop.musixmatch.com", "cookie": "AWSELBCORS=0; AWSELB=0",
                "User-Agent": BROWSER_UA}
_mxm_lock = threading.Lock()
_mxm_token = {"t": "", "exp": 0.0, "fail_until": 0.0}


def _mxm_token_file() -> Path:
    return _DATA_DIR / "mxm_token.json"


def _mxm_get_token() -> str:
    with _mxm_lock:
        now = time.time()
        if _mxm_token["t"] and _mxm_token["exp"] > now:
            return _mxm_token["t"]
        if _mxm_token["fail_until"] > now:
            return ""
        try:
            d = json.loads(_mxm_token_file().read_text("utf-8"))
            if d.get("token") and d.get("exp", 0) > now:
                _mxm_token.update(t=d["token"], exp=d["exp"])
                return d["token"]
        except Exception:
            pass
        j = _get_json(_MXM_ROOT + "token.get",
                      {"app_id": "web-desktop-app-v1.0", "t": str(int(now * 1000))},
                      _MXM_HEADERS)
        tok = ""
        try:
            if j["message"]["header"]["status_code"] == 200:
                tok = j["message"]["body"]["user_token"]
        except Exception:
            tok = ""
        if not tok or "UpgradeOnlyUpgradeOnly" in tok:
            _mxm_token["fail_until"] = now + 300       # капча/лимит — не долбим 5 минут
            return ""
        _mxm_token.update(t=tok, exp=now + 600)
        try:
            _DATA_DIR.mkdir(parents=True, exist_ok=True)
            _mxm_token_file().write_text(json.dumps({"token": tok, "exp": now + 600}), "utf-8")
        except Exception:
            pass
        return tok


def _mxm(action, params):
    tok = _mxm_get_token()
    if not tok:
        return None
    p = dict(params)
    p.update(app_id="web-desktop-app-v1.0", usertoken=tok, t=str(int(time.time() * 1000)))
    j = _get_json(_MXM_ROOT + action, p, _MXM_HEADERS)
    try:
        hdr = j["message"]["header"]
        if hdr.get("status_code") == 401:          # токен протух / капча
            with _mxm_lock:
                _mxm_token.update(t="", exp=0.0, fail_until=time.time() + 120)
            return None
        if hdr.get("status_code") != 200:
            return None
        return j["message"]["body"]
    except Exception:
        return None


def src_musixmatch(cands, duration, album):
    tracks = {}
    for a, t in _queries(cands, 2):
        p = {"page_size": 6, "page": 1, "s_track_rating": "desc", "quorum_factor": "1.0",
             "f_has_lyrics": 1}
        if a:
            p.update(q_track=t, q_artist=a)
        else:
            p.update(q=t)
        body = _mxm("track.search", p)
        if body is None and not _mxm_token["t"]:
            return []
        for it in (body or {}).get("track_list") or []:
            tr = it.get("track") or {}
            tracks.setdefault(tr.get("track_id"), tr)

    scored = []
    for tid, tr in tracks.items():
        fa, ft = tr.get("artist_name") or "", tr.get("track_name") or ""
        fd = float(tr.get("track_length") or 0)
        scored.append((match_score(cands, fa, ft, fd, duration), tid, tr, fa, ft, fd))
    scored.sort(key=lambda x: -x[0])

    hits = []
    for sc, tid, tr, fa, ft, fd in scored[:2]:
        if sc < ACCEPT:
            break
        if tr.get("instrumental"):
            hits.append(Hit(INSTRUMENTAL_TEXT, False, "Musixmatch", sc, fa, ft, fd, instrumental=True))
            continue
        if tr.get("has_subtitles"):
            body = _mxm("track.subtitle.get", {"track_id": tid, "subtitle_format": "lrc"})
            lrc = (((body or {}).get("subtitle") or {}).get("subtitle_body") or "")
            lrc = clean_lrc(lrc, fa, ft)
            if lrc and _plausible_synced(lrc, duration):
                hits.append(Hit(lrc, True, "Musixmatch", sc, fa, ft, fd))
                continue
        if tr.get("has_lyrics"):
            body = _mxm("track.lyrics.get", {"track_id": tid})
            txt = _clean_plain(((body or {}).get("lyrics") or {}).get("lyrics_body") or "")
            if txt:
                hits.append(Hit(txt, False, "Musixmatch", sc, fa, ft, fd))
    return hits


# ------------------------------------------------------------------ #
#  syncedlyrics (опционально, если установлен pip install syncedlyrics)#
# ------------------------------------------------------------------ #

def src_syncedlyrics(cands, duration, album):
    try:
        import syncedlyrics
    except Exception:
        return []
    hits = []
    for a, t in _queries([c for c in cands if c[0]], 1):
        try:
            lrc = syncedlyrics.search(f"{a} {t}", synced_only=True,
                                      providers=["Megalobiz", "Lrclib", "NetEase"])
        except TypeError:
            try:
                lrc = syncedlyrics.search(f"{a} {t}", synced_only=True)
            except Exception:
                lrc = None
        except Exception:
            lrc = None
        if lrc:
            lrc = clean_lrc(lrc, a, t)
            if _plausible_synced(lrc, duration):
                # внешних метаданных нет — доверяем меньше, чем проверенным источникам
                hits.append(Hit(lrc, True, "syncedlyrics", ACCEPT + 0.01, a, t))
    return hits


# ------------------------------------------------------------------ #
#  Genius (публичный поиск без токена + API с токеном)                #
# ------------------------------------------------------------------ #

def _genius_hits_from(j):
    out = []
    resp = (j or {}).get("response") or {}
    for sec in resp.get("sections") or []:
        for h in sec.get("hits") or []:
            if (h.get("index") or h.get("type")) == "song":
                out.append(h.get("result") or {})
    for h in resp.get("hits") or []:
        if h.get("type", "song") == "song":
            out.append(h.get("result") or {})
    return out


def src_genius(cands, duration, album, token=""):
    results = {}
    for a, t in _queries(cands, 2):
        q = f"{a} {t}".strip()
        j = _get_json("https://genius.com/api/search/song", {"q": q, "per_page": 5},
                      {"User-Agent": BROWSER_UA})
        for r in _genius_hits_from(j):
            results.setdefault(r.get("id"), r)
        if token and not results:
            j = _get_json(f"{GENIUS_API}/search", {"q": q},
                          {"Authorization": f"Bearer {token}"})
            for r in _genius_hits_from(j):
                results.setdefault(r.get("id"), r)

    scored = []
    for rid, r in results.items():
        fa = ((r.get("primary_artist") or {}).get("name") or r.get("artist_names") or "")
        ft = r.get("title") or ""
        if re.search(r"\b(?:romanized|translation|перевод|traducción|übersetzung)\b",
                     (r.get("full_title") or "") + " " + fa, re.I):
            continue
        sc = match_score(cands, fa, ft)
        alt = r.get("artist_names") or ""
        if alt and alt != fa:
            sc = max(sc, match_score(cands, alt, ft))
        scored.append((sc, r, fa, ft))
    scored.sort(key=lambda x: -x[0])
    for sc, r, fa, ft in scored[:1]:
        if sc < ACCEPT or not r.get("url"):
            break
        if (r.get("lyrics_state") or "complete") not in ("complete", "unreleased"):
            continue
        txt = scrape_genius_lyrics(r["url"])
        if txt:
            return [Hit(txt, False, "Genius", sc, fa, ft)]
    return []


def src_ovh(cands, duration, album):
    for a, t in _queries([c for c in cands if c[0]], 2):
        try:
            r = _get(f"https://api.lyrics.ovh/v1/{requests.utils.quote(a)}/{requests.utils.quote(t)}",
                     timeout=6)
            if r.status_code == 200:
                txt = _clean_plain((r.json() or {}).get("lyrics") or "")
                txt = re.sub(r"^Paroles de la chanson .*?\n", "", txt)
                if len(txt) > 40:
                    return [Hit(txt, False, "lyrics.ovh", ACCEPT + 0.02, a, t)]
        except Exception:
            pass
    return []


# ------------------------------------------------------------------ #
#  Дополнительные источники: QQ Music и lrc.cx (тайминги),           #
#  AZLyrics и YouTube Music (обычный текст)                                #
# ------------------------------------------------------------------ #

_QQ_HEADERS = {"Referer": "https://y.qq.com/", "User-Agent": BROWSER_UA}


def src_qqmusic(cands, duration, album):
    songs = {}
    for a, t in _queries(cands, 2):
        j = _get_json("https://c.y.qq.com/soso/fcgi-bin/client_search_cp",
                      {"w": f"{a} {t}".strip(), "format": "json", "p": 1, "n": 8, "cr": 1, "new_json": 1},
                      _QQ_HEADERS)
        for s in (((j or {}).get("data") or {}).get("song") or {}).get("list") or []:
            songs.setdefault(s.get("mid") or s.get("songmid"), s)
    scored = []
    for mid, s in songs.items():
        if not mid:
            continue
        fa = ", ".join(x.get("name", "") for x in (s.get("singer") or []) if isinstance(x, dict))
        ft = s.get("title") or s.get("songname") or s.get("name") or ""
        fd = float(s.get("interval") or 0)
        scored.append((match_score(cands, fa, ft, fd, duration), mid, fa, ft, fd))
    scored.sort(reverse=True)
    hits = []
    for sc, mid, fa, ft, fd in scored[:3]:
        if sc < ACCEPT - 0.1:
            break
        j = _get_json("https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg",
                      {"songmid": mid, "format": "json", "nobase64": 0, "g_tk": 5381}, _QQ_HEADERS)
        raw = (j or {}).get("lyric") or ""
        if not raw:
            continue
        try:
            lrc = base64.b64decode(raw).decode("utf-8", "replace")
        except Exception:
            continue
        lrc = clean_lrc(html.unescape(lrc), fa, ft)
        if lrc and _plausible_synced(lrc, duration):
            hits.append(Hit(lrc, True, "QQ Music", sc, fa, ft, fd))
            if sc >= STRONG:
                break
    return hits


def _slug(s: str, sep: str = "") -> str:
    s = re.sub(r"[^a-z0-9]+", sep, (s or "").lower().replace("&", " and "))
    return s.strip(sep) if sep else s


def _html_text(block: str) -> str:
    block = re.sub(r"(?i)<br\s*/?>", "\n", block)
    block = re.sub(r"(?s)<!--.*?-->", "", block)
    return _clean_plain(html.unescape(re.sub(r"<[^>]+>", "", block)))


def src_azlyrics(cands, duration, album):
    for a, t in _queries([c for c in cands if c[0]], 2):
        a1 = re.sub(r"^the\s+", "", primary_artist(a).lower())
        ua, ut = _slug(a1), _slug(t)
        if not ua or not ut:
            continue
        try:
            r = _get(f"https://www.azlyrics.com/lyrics/{ua}/{ut}.html",
                     headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                            "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"}, timeout=8)
        except Exception:
            continue
        if r.status_code != 200:
            continue
        r.encoding = "utf-8"                            # сервер не указывает кодировку — иначе кириллица «ломается»
        m = re.search(r"Sorry about that\. -->(.*?)</div>", r.text, re.S)
        tm = re.search(r"<title>\s*(.*?)\s+-\s+(.*?)\s+Lyrics", r.text, re.S)
        if m and tm:
            sc = match_score(cands, html.unescape(tm.group(1)), html.unescape(tm.group(2)))
            txt = _html_text(m.group(1))
            if len(txt) > 60 and sc >= ACCEPT:          # страница та самая, а не однофамильная песня
                return [Hit(txt, False, "AZLyrics", sc, a, t)]
    return []


def src_lrccx(cands, duration, album):
    """lrc.cx — открытый агрегатор синхронизированных текстов (несколько стриминговых баз)."""
    for a, t in _queries([c for c in cands if c[0]], 2):
        try:
            r = _get("https://api.lrc.cx/lyrics", {"title": t, "artist": a},
                     headers={"User-Agent": BROWSER_UA}, timeout=12)
        except Exception:
            continue
        if r.status_code != 200 or "[" not in r.text:
            continue
        body = "\n".join(l for l in r.text.splitlines() if not re.match(r"^\s*\[[A-Za-z][^\]\d:]*\]\s*$", l))
        lrc = clean_lrc(body, a, t)
        if lrc and _plausible_synced(lrc, duration):
            return [Hit(lrc, True, "lrc.cx", ACCEPT + 0.01, a, t)]
    return []


def _ytm_lyrics_for(yt, vid, a, t, sc, fa, ft, duration):
    try:
        wp = yt.get_watch_playlist(videoId=vid)
        bid = (wp or {}).get("lyrics")
        if not bid:
            return None
        try:
            lyr = yt.get_lyrics(bid, timestamps=True)
        except TypeError:                               # старая версия ytmusicapi
            lyr = yt.get_lyrics(bid)
    except Exception as e:
        _log(f"YouTube Music: {e.__class__.__name__}")
        return None
    if not lyr:
        return None
    body = lyr.get("lyrics") if isinstance(lyr, dict) else getattr(lyr, "lyrics", None)
    if isinstance(body, list):                          # с таймингами: [LyricLine(text, start_time, ...)]
        rows = []
        for ln in body:
            st = ln.get("start_time") if isinstance(ln, dict) else getattr(ln, "start_time", None)
            tx = ln.get("text") if isinstance(ln, dict) else getattr(ln, "text", None)
            if tx and st is not None:
                m_, s_ = divmod(float(st) / 1000.0, 60)
                rows.append(f"[{int(m_):02d}:{s_:05.2f}]{tx}")
        lrc = clean_lrc("\n".join(rows), fa, ft)
        if lrc and _plausible_synced(lrc, duration):
            return Hit(lrc, True, "YouTube Music", sc, fa, ft)
    elif isinstance(body, str) and len(body) > 40:
        return Hit(_clean_plain(body), False, "YouTube Music", sc, fa, ft)
    return None


def src_ytmusic(cands, duration, album):
    """Тексты YouTube Music через ytmusicapi (если у трека есть — бывают и с таймингами)."""
    try:
        from online_search import ytm_client
        yt = ytm_client()
    except Exception:
        yt = None
    if yt is None:
        return []
    for a, t in _queries([c for c in cands if c[0]], 2):
        try:
            found = yt.search(f"{a} {t}".strip(), limit=8)      # filter="songs" у YouTube сейчас пуст
        except Exception:
            continue
        scored = []
        for f in found or []:
            if not f.get("videoId") or f.get("resultType") not in ("song", "video"):
                continue
            fa = ", ".join(x.get("name", "") for x in (f.get("artists") or []) if isinstance(x, dict))
            ft = f.get("title") or ""
            sc = match_score(cands, fa, ft, float(f.get("duration_seconds") or 0), duration)
            scored.append((sc, f["videoId"], fa, ft))
        scored.sort(reverse=True)
        for sc, vid, fa, ft in scored[:3]:
            if sc < ACCEPT + 0.1:                         # поиск YouTube нечёткий — берём только уверенные совпадения
                break
            h = _ytm_lyrics_for(yt, vid, a, t, sc, fa, ft, duration)
            if h:
                return [h]
    return []


# ------------------------------------------------------------------ #
#  Главная функция                                                    #
# ------------------------------------------------------------------ #

_SYNCED_SOURCES = (("LRCLIB", src_lrclib), ("Musixmatch", src_musixmatch),
                   ("NetEase", src_netease), ("Kugou", src_kugou), ("QQ Music", src_qqmusic),
                   ("syncedlyrics", src_syncedlyrics), ("lrc.cx", src_lrccx),
                   ("YouTube Music", src_ytmusic))

# обычный текст (без таймингов): запускаются сразу вместе с источниками таймингов
_PLAIN_SOURCES = (("lyrics.ovh", src_ovh), ("AZLyrics", src_azlyrics))


def _pick(hits):
    syn = [h for h in hits if h.synced and h.score >= ACCEPT]
    if syn:
        # при равной уверенности — LRCLIB/Musixmatch (точнее тайминги, меньше мусора)
        pref = {"LRCLIB": 0.03, "Musixmatch": 0.02, "NetEase": 0.0, "QQ Music": -0.005, "Kugou": -0.01, "lrc.cx": -0.015, "YouTube Music": -0.012}
        return max(syn, key=lambda h: h.score + pref.get(h.source, -0.02))
    inst = [h for h in hits if h.instrumental and h.score >= ACCEPT + 0.15]
    if inst:
        return max(inst, key=lambda h: h.score)
    plain = [h for h in hits if not h.synced and not h.instrumental and h.score >= ACCEPT]
    if plain:
        pref = {"Genius": 0.03, "Musixmatch": 0.02, "LRCLIB": 0.01, "AZLyrics": 0.005, "YouTube Music": 0.004}
        return max(plain, key=lambda h: h.score + pref.get(h.source, 0))
    return None


def fetch_lyrics_ex(title: str, artist: str, genius_token: str = "", duration: float = 0.0,
                    path: str = "", album: str = "", deadline: float = 14.0) -> dict | None:
    """Полный поиск. Возвращает dict(text, source, synced, score, artist, title)
    или None."""
    if str(path or "").startswith("stream:"):
        path = ""                                     # онлайн-трек: у «stream:…» нет имени файла
    cands = parse_candidates(title or "", artist or "", path or "", album or "")
    if not cands:
        return None
    try:
        duration = float(duration or 0)
    except Exception:
        duration = 0.0
    album = basic_clean(album or "")
    if album and norm(album) in {norm(t) for _, t in cands}:
        album = ""                                    # сингл: альбом = название, не мешаем get
    _log(f"кандидаты: {cands[:4]}  ({duration:.0f}s)")

    t0 = time.monotonic()
    hits: list[Hit] = []
    ex = ThreadPoolExecutor(max_workers=12, thread_name_prefix="lyrics")
    futs = {ex.submit(fn, cands, duration, album): name for name, fn in _SYNCED_SOURCES}
    # обычные тексты запускаем сразу — если таймингов не будет, не ждать ещё раз
    futs[ex.submit(src_genius, cands, duration, album, genius_token)] = "Genius"
    for name, fn in _PLAIN_SOURCES:
        futs[ex.submit(fn, cands, duration, album)] = name
    synced_names = {n for n, _ in _SYNCED_SOURCES}
    pending = set(futs)
    try:
        while pending:
            left = deadline - (time.monotonic() - t0)
            if left <= 0:
                break
            done, pending = wait(pending, timeout=left, return_when=FIRST_COMPLETED)
            for f in done:
                try:
                    got = f.result() or []
                except Exception as e:
                    _log(f"{futs[f]}: {e!r}")
                    got = []
                hits.extend(got)
                for h in got:
                    _log(repr(h))
            if any(h.synced and h.score >= STRONG for h in hits):
                break                                 # уверенное попадание с таймингами
            # все источники таймингов ответили: если ничего — ждём только обычные
            if not any(futs[p] in synced_names for p in pending):
                if _pick(hits) is not None:
                    break
    finally:
        ex.shutdown(wait=False, cancel_futures=True)

    try:                                              # проверка: тот ли текст (согласие источников, язык, слух…)
        import lyrics_verify
        best = lyrics_verify.choose(hits, cands, duration, path, ACCEPT, _log)
    except Exception as e:                            # noqa: BLE001
        _log(f"verify: {e!r}")
        best = _pick(hits)
    if not best:
        _log(f"не найдено за {time.monotonic() - t0:.1f}s")
        return None
    _log(f"выбран {best!r} за {time.monotonic() - t0:.1f}s")
    return {"text": uncensor(best.text), "source": best.source, "synced": bool(best.synced),
            "score": round(best.score, 3), "artist": best.artist, "title": best.title,
            "instrumental": best.instrumental}


def search_stream(title: str, artist: str, genius_token: str = "", duration: float = 0.0,
                  on_hits=None, cancel=None, deadline: float = 25.0) -> list:
    """Ручной поиск: опрашивает все источники сразу и отдаёт найденное по мере ответов —
    on_hits(список новых dict) зовётся из рабочего потока. cancel — threading.Event (новый запрос).
    → все находки (dict как у fetch_lyrics_ex + "match" — насколько похоже на запрос)."""
    cands = parse_candidates(title or "", artist or "", "", "")
    if not cands:
        return []
    try:
        duration = float(duration or 0)
    except Exception:
        duration = 0.0
    out, seen = [], set()
    ex = ThreadPoolExecutor(max_workers=12, thread_name_prefix="lyrics-live")
    futs = {ex.submit(fn, cands, duration, ""): name for name, fn in _SYNCED_SOURCES}
    futs[ex.submit(src_genius, cands, duration, "", genius_token)] = "Genius"
    for name, fn in _PLAIN_SOURCES:
        futs[ex.submit(fn, cands, duration, "")] = name
    pending = set(futs)
    t0 = time.monotonic()
    try:
        while pending:
            if cancel is not None and cancel.is_set():
                break
            left = deadline - (time.monotonic() - t0)
            if left <= 0:
                break
            done, pending = wait(pending, timeout=min(0.25, left), return_when=FIRST_COMPLETED)
            new = []
            for f in done:
                try:
                    got = f.result() or []
                except Exception as e:
                    _log(f"{futs[f]}: {e!r}")
                    got = []
                for h in got:
                    if not h.text or not h.text.strip():
                        continue
                    key = (h.source, norm(h.artist), norm(h.title), h.synced, h.text[:120])
                    if key in seen:
                        continue
                    seen.add(key)
                    d = {"text": uncensor(h.text), "source": h.source, "synced": bool(h.synced),
                         "score": round(h.score, 3), "match": round(h.score, 3), "artist": h.artist,
                         "title": h.title, "instrumental": h.instrumental, "duration": h.duration}
                    new.append(d)
            if new:
                out.extend(new)
                if on_hits is not None and not (cancel is not None and cancel.is_set()):
                    on_hits(new)
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    return out


def fetch_lyrics(title: str, artist: str, genius_token: str = "", duration: float = 0.0,
                 path: str = "", album: str = "") -> tuple[str | None, str]:
    """Совместимая обёртка: (текст, источник) или (None, "")."""
    try:
        r = fetch_lyrics_ex(title, artist, genius_token, duration, path, album)
    except Exception as e:
        _log(f"ошибка: {e!r}")
        r = None
    if not r:
        return None, ""
    src = r["source"] + (" · синхронизированный" if r["synced"] else "")
    return r["text"], src


# ------------------------------------------------------------------ #
#  Очистка текста Genius и скрейпер (из v2)                           #
# ------------------------------------------------------------------ #

def _clean_lyrics_text(text: str) -> str:
    """
    Убирает типичный мусор, из-за которого показывается только часть текста:

    1. Genius-шапка вида «SongTitle Lyrics\n[Verse 1]» — lyricsgenius
       добавляет строку «<Title> Lyrics» в самое начало.
    2. Хвостовая реклама Genius: «...Embed», «N Contributors», «You might also like».
    3. Пустые строки в начале и конце.
    4. Более двух подряд идущих пустых строк (артефакт скрейпинга).
    """
    if not text:
        return text

    lines = text.splitlines()

    # Убираем первую строку вида «<что угодно> Lyrics» (lyricsgenius-шапка)
    if lines and re.match(r"^.{1,120}\s+Lyrics\s*$", lines[0], re.IGNORECASE):
        lines = lines[1:]

    # Убираем хвостовые строки-мусор
    _TAIL_NOISE = re.compile(
        r"^\s*(?:\d+\s+Contributors?|You might also like|Embed|"
        r"See\s+\w+\s+LiveGet\s+tickets|"
        r"\d+K?\s*$)\s*$",
        re.IGNORECASE,
    )
    while lines and _TAIL_NOISE.match(lines[-1]):
        lines.pop()

    # Схлопываем тройные+ пустые строки в двойные
    result = []
    blank_count = 0
    for line in lines:
        if line.strip() == "":
            blank_count += 1
            if blank_count <= 2:
                result.append(line)
        else:
            blank_count = 0
            result.append(line)

    return "\n".join(result).strip()


# ------------------------------------------------------------------ #


def scrape_genius_lyrics(url: str) -> str | None:
    """
    Скрейпит текст песни со страницы Genius.

    Исправлена главная причина «половины текста»: старый regex
      r'<div[^>]*data-lyrics-container="true"[^>]*>(.*?)</div>'
    с re.DOTALL останавливался на ПЕРВОМ </div>, не учитывая вложенные теги —
    поэтому захватывалась только часть контейнера.

    Новая стратегия:
      1. Пробуем BeautifulSoup (если установлен) — он корректно парсит
         вложенный HTML и достаёт весь текст из data-lyrics-container блоков.
      2. Fallback: вручную считаем открывающие/закрывающие теги, чтобы найти
         границу каждого контейнера независимо от вложенности, — затем
         обрабатываем извлечённый HTML как раньше.
    """
    try:
        headers = {"User-Agent": "Mozilla/5.0 NeonPlayer/4.0"}
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return None
        page = r.text
    except Exception as e:
        print(f"[Lyrics/scrape] fetch error: {e}")
        return None

    # ── Способ 1: BeautifulSoup ──────────────────────────────────────
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(page, "html.parser")
        containers = soup.find_all("div", attrs={"data-lyrics-container": "true"})
        if containers:
            chunks = []
            for div in containers:
                # служебные вставки внутри контейнера (шапка «N Contributors…», реклама)
                for junk in div.find_all(attrs={"data-exclude-from-selection": "true"}):
                    junk.decompose()
                # <br> → \n перед извлечением текста
                for br in div.find_all("br"):
                    br.replace_with("\n")
                chunks.append(div.get_text())
            text = "\n\n".join(c.strip() for c in chunks if c.strip())
            if text:
                return _clean_lyrics_text(text)
    except ImportError:
        pass  # BeautifulSoup не установлен — идём к fallback
    except Exception as e:
        print(f"[Lyrics/scrape] bs4 error: {e}")

    # ── Способ 2: подсчёт вложенных тегов (fallback) ────────────────
    # Ищем все начала data-lyrics-container блоков
    starts = [m.start() for m in re.finditer(
        r'<div[^>]*data-lyrics-container="true"[^>]*>', page, re.IGNORECASE
    )]
    if not starts:
        return None

    chunks = []
    for start in starts:
        # Находим конец открывающего тега
        tag_end = page.index(">", start) + 1
        # Идём вперёд, считая <div> и </div>, пока не закроем корневой
        depth = 1
        pos = tag_end
        while pos < len(page) and depth > 0:
            open_m = re.search(r"<div[\s>]", page[pos:], re.IGNORECASE)
            close_m = re.search(r"</div\s*>", page[pos:], re.IGNORECASE)
            if open_m and (not close_m or open_m.start() < close_m.start()):
                depth += 1
                pos += open_m.end()
            elif close_m:
                depth -= 1
                pos += close_m.end()
            else:
                break  # структура сломана

        inner_html = page[tag_end:pos]
        chunk = re.sub(r"<br\s*/?>", "\n", inner_html, flags=re.I)
        chunk = re.sub(r"<[^>]+>", "", chunk)
        chunk = html.unescape(chunk).strip()
        if chunk:
            chunks.append(chunk)

    if not chunks:
        return None

    text = "\n\n".join(chunks)
    return _clean_lyrics_text(text) or None


# ------------------------------------------------------------------ #
#  Публичный API                                                      #


# ------------------------------------------------------------------ #
#  Снятие цензуры с английского мата: «f**k» → «fuck», «sh*t» → «shit»  #
# ------------------------------------------------------------------ #

_SWEARS = [
    "motherfucking", "motherfuckers", "motherfucker", "motherfuckin", "fucking", "fuckin", "fucked",
    "fucker", "fuckers", "fucks", "fuck", "bullshit", "shitty", "shits", "shit", "bitches", "bitchin",
    "bitch", "asshole", "assholes", "ass", "goddamn", "damn", "dicks", "dick", "pussy", "cunt",
    "cocks", "cock", "whore", "whores", "slut", "sluts", "niggas", "nigga", "hoes", "hoe",
    "bastard", "piss", "pissed", "crap", "tits", "titties", "jizz", "cum",
]
_CENSORED = re.compile(r"(?<![\w*#@$])([A-Za-z]*[*#@$%!]{1,}[A-Za-z*#@$%!]*[A-Za-z]|[A-Za-z]+[*#@$%]{1,}[*#@$%!]*)(?![\w*#@$])")


def _match_case(src: str, word: str) -> str:
    letters = [c for c in src if c.isalpha()]
    if letters and all(c.isupper() for c in letters) and len(letters) > 1:
        return word.upper()
    if src[:1].isupper():
        return word[:1].upper() + word[1:]
    return word


def uncensor(text: str) -> str:
    """Восстанавливает английский мат, «запиканный» звёздочками в текстах
    (Musixmatch/Genius и т.п.). Слова без звёздочек не трогаются; неоднозначные
    («****») остаются как есть."""
    if not text or not re.search(r"[A-Za-z][*#@$%]|[*#@$%][A-Za-z]", text):
        return text

    def fix(m):
        tok = m.group(1)
        if not re.search(r"[A-Za-z]", tok):
            return tok
        body = tok.rstrip("!")
        tail = tok[len(body):]
        pat = "^" + "".join("[a-z]" if ch in "*#@$%!" else re.escape(ch.lower()) for ch in body) + "$"
        rx = re.compile(pat)
        for w in _SWEARS:
            if len(w) == len(body) and rx.match(w):
                return _match_case(body, w) + tail
        # «f***ing», «m*********r» с другой длиной звёздочек — по первой/последней букве
        low = body.lower()
        first = low[0] if low[0].isalpha() else ""
        last_letters = re.search(r"([a-z]+)$", low)
        suffix = last_letters.group(1) if last_letters else ""
        for w in _SWEARS:
            if first and w.startswith(first) and (not suffix or w.endswith(suffix)) \
                    and abs(len(w) - len(body)) <= 2 and len(w) >= 3:
                return _match_case(body, w) + tail
        return tok

    return _CENSORED.sub(fix, text)
