# stream_core.py
"""
Стриминг: онлайн-треки играют сразу, без скачивания (YouTube Music / SoundCloud).

Трек в очереди — обычный словарь, только путь «потоковый»:

    {"path": "stream:https://music.youtube.com/watch?v=…", "title", "artist", "album", "duration",
     "cover": локальная картинка (качается в кэш), "thumb_url", "url": страница трека, "stream": True,
     "match": 93}                       ← процент совпадения, если трек искали по «исполнитель — название»

Путь без ссылки — «stream:find:исполнитель␟название␟длительность»: трек ищется на YouTube Music,
потом на SoundCloud (online_search.find_best), берётся вариант с совпадением ≥ 72 %.

Ссылку на сам аудиопоток достаёт yt-dlp (1–2 с) — поэтому StreamResolver держит кэш готовых ссылок
(с учётом срока жизни: у YouTube в ссылке есть expire=) и заранее готовит следующие треки очереди —
переключение на них мгновенное. Движок (AudioEngine.url_resolver) переводит «stream:…» в ссылку сам,
весь остальной плеер видит обычный путь трека.

30-секундные превью SoundCloud (Go+) отбрасываются — тогда трек ищется на YouTube Music.
"""
from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlparse

PREFIX = "stream:"
FIND = "stream:find:"
_SEP = "\x1f"
MIN_MATCH = 0.72                     # как в online_search.find_best


def is_stream(path) -> bool:
    return isinstance(path, str) and path.startswith(PREFIX)


def path_for(url: str) -> str:
    return PREFIX + url


def find_path(artist: str, title: str, duration: float = 0) -> str:
    return FIND + _SEP.join(((artist or "").strip(), (title or "").strip(), str(int(duration or 0))))


def match_percent(score) -> int:
    try:
        return max(0, min(100, int(round(float(score) * 100))))
    except (TypeError, ValueError):
        return 0


def make_track(t: dict, album: str = "") -> dict:
    """Онлайн-трек интерфейса (online_search) → элемент очереди для потокового воспроизведения."""
    url = t.get("url") or ""
    path = path_for(url) if url else find_path(t.get("artist", ""), t.get("title", ""), t.get("duration", 0))
    out = {"path": path, "title": t.get("title", "") or "", "artist": t.get("artist", "") or "",
           "album": t.get("album") or album or "", "genre": "", "duration": int(t.get("duration") or 0),
           "cover": "", "thumb_url": t.get("thumb_url") or t.get("cover_url") or "", "url": url, "stream": True,
           "src": t.get("source") or t.get("src") or ""}
    if t.get("score") is not None:
        out["match"] = match_percent(t["score"])
    try:
        import artist_net
        out["cover"] = artist_net.cached_image(out["thumb_url"])
    except Exception:                                      # noqa: BLE001
        pass
    return out


def _expiry(direct: str) -> float:
    try:
        q = parse_qs(urlparse(direct).query)
        if "expire" in q:
            return float(q["expire"][0])
        m = re.search(r"/expire/(\d+)", direct)
        if m:
            return float(m.group(1))
    except Exception:                                      # noqa: BLE001
        pass
    return time.time() + 20 * 60                           # SoundCloud и прочие — недолго


def _is_preview(direct: str, info: dict) -> bool:
    if "/preview/" in direct or "cf-preview-media" in direct:
        return True
    d = float(info.get("duration") or 0)
    return 0 < d <= 31 and "soundcloud" in (info.get("webpage_url") or info.get("original_url") or "")


class StreamResolver:
    """Ссылки на аудиопотоки: кэш + фоновые потоки + «приготовить заранее»."""

    def __init__(self, ui_call=None):
        self.ui_call = ui_call                             # fn(callable) — выполнить в GUI-потоке
        self.lock = threading.Lock()
        self.cache = {}                                    # путь → {"url", "exp", "page", "match", "duration"}
        self.waiting = {}                                  # путь → [колбэки]
        self.pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="stream-resolve")
        self.errors = {}                                   # путь → (время, текст)

    # ── кэш ──
    def cached(self, path: str) -> str | None:
        with self.lock:
            e = self.cache.get(path)
        if e and e["exp"] - 90 > time.time():
            return e["url"]
        return None

    def info(self, path: str) -> dict:
        with self.lock:
            return dict(self.cache.get(path) or {})

    # ── разрешение ──
    def resolve(self, track: dict, cb=None):
        """Достать ссылку в фоне. cb(ok: bool, info_or_error) — в GUI-потоке (если задан ui_call)."""
        path = str(track.get("path", ""))
        if self.cached(path):
            if cb:
                self._call(cb, True, self.info(path))
            return
        with self.lock:
            if path in self.waiting:
                if cb:
                    self.waiting[path].append(cb)
                return
            self.waiting[path] = [cb] if cb else []
        self.pool.submit(self._work, dict(track))

    def prefetch(self, track: dict):
        if is_stream(track.get("path")) and not self.cached(str(track.get("path"))):
            with self.lock:
                bad = self.errors.get(str(track.get("path")))
            if bad and time.time() - bad[0] < 120:
                return                                     # недавно не нашлось — не долбим сеть
            self.resolve(track)

    def _call(self, cb, ok, data):
        if self.ui_call is not None:
            self.ui_call(lambda: cb(ok, data))
        else:
            cb(ok, data)

    def _work(self, track):
        path = str(track.get("path", ""))
        try:
            res = self._resolve_now(track)
            ok = True
        except Exception as e:                             # noqa: BLE001
            res, ok = str(e) or e.__class__.__name__, False
            with self.lock:
                self.errors[path] = (time.time(), res)
            print("[stream]", path[:90], "→", res)
        with self.lock:
            if ok:
                self.cache[path] = res
                self.errors.pop(path, None)
                if len(self.cache) > 300:
                    for k in list(self.cache)[:60]:
                        self.cache.pop(k, None)
            cbs = self.waiting.pop(path, [])
        for cb in cbs:
            if cb:
                self._call(cb, ok, dict(res) if ok else res)

    def _resolve_now(self, track) -> dict:
        path = str(track.get("path", ""))
        page, match, found = track.get("url") or "", track.get("match"), None
        if path.startswith(FIND) or not page:
            a, t, d = (path[len(FIND):].split(_SEP) + ["", "", "0"])[:3] if path.startswith(FIND) else \
                (track.get("artist", ""), track.get("title", ""), track.get("duration", 0))
            found = self._find(a, t, float(d or 0))
            page, match = found["url"], match_percent(found.get("score"))
        direct, info = self._direct(page)
        if direct is None and "soundcloud.com" in page:    # превью/закрытый трек SoundCloud — ищем на YouTube
            found = self._find(track.get("artist", ""), track.get("title", ""), float(track.get("duration") or 0),
                               prefer=("ytm",))
            page, match = found["url"], match_percent(found.get("score"))
            direct, info = self._direct(page)
        if direct is None:
            raise RuntimeError("поток недоступен")
        out = {"url": direct, "exp": _expiry(direct), "page": page, "match": match,
               "duration": float(info.get("duration") or 0)}
        if found:
            out["found"] = {k: found.get(k) for k in ("title", "artist", "album", "thumb_url", "duration", "source")}
        # обложка — в локальный кэш (её видят пластинка, обложечные темы, Discord-иконка не нужна)
        thumb = track.get("thumb_url") or (found or {}).get("thumb_url") or info.get("thumbnail") or ""
        if thumb:
            try:
                import artist_net
                out["cover"] = artist_net.image(thumb)
            except Exception:                              # noqa: BLE001
                pass
        return out

    @staticmethod
    def _find(artist, title, duration, prefer=("ytm", "sc")) -> dict:
        import online_search
        best = online_search.find_best(title, artist, duration, prefer=prefer)
        if not best or float(best.get("score") or 0) < MIN_MATCH:
            raise RuntimeError(f"не нашёлся: {artist} — {title}")
        return best

    @staticmethod
    def _direct(page):
        import yt_dlp
        opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True,
                "format": "bestaudio[ext=m4a]/bestaudio[acodec=opus]/bestaudio/best", "socket_timeout": 10}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(page, download=False)
        if info and info.get("entries"):
            info = next((e for e in info["entries"] if e), None) or {}
        direct = (info or {}).get("url")
        if not direct or _is_preview(direct, info or {}):
            return None, info or {}
        return direct, info


_RESOLVER: StreamResolver | None = None


def resolver(ui_call=None) -> StreamResolver:
    global _RESOLVER
    if _RESOLVER is None:
        _RESOLVER = StreamResolver(ui_call)
    elif ui_call is not None and _RESOLVER.ui_call is None:
        _RESOLVER.ui_call = ui_call
    return _RESOLVER
