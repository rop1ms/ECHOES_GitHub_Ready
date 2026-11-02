# discord_rpc.py
"""
Discord Rich Presence — в профиле Discord видно, что вы слушаете в ECHOES, как у Spotify:

    Слушает ECHOES                      ← тип активности «Слушает», а не «Играет»
    ┌────────┐  Исполнитель            ← первая строка — исполнитель (кликабельна)
    │обложка │  Название трека         ← вторая — трек (кликабельна)
    │        │  Альбом                 ← третья — альбом (подпись обложки)
    └────────┘  1:12 ━━━━━━─────── 3:45 ← полоса прогресса
                [ Слушать на YouTube Music ]

В списке участников сервера — «Слушает <исполнитель>» (status_display_type = DETAILS).

Обложка: Discord показывает только публичную https-картинку или ассет приложения. Локальные обложки
он не видит, поэтому обложка ищется на Deezer и iTunes одновременно (кто первый нашёл; название чистится
от «(Official Video)», feat., хэштегов), следующий трек очереди — заранее; кэш в
~/.neon_player/discord_covers.json; не нашлась — ассет «vinyl». Поиск можно выключить в настройках.

Требует пакет `pypresence` (4.4+ для «Слушает»; старые версии — обычный статус) и запущенный
десктопный Discord на этом же компьютере. Всё мягко: нет пакета / Discord закрыт — плеер работает как
обычно, фоновый поток раз в RETRY_SEC переподключается.

Свой Client ID приложения Discord (бесплатно):
  1. https://discord.com/developers/applications → New Application (имя — например, ECHOES)
  2. Скопируйте «Application ID» → «Вставить Discord Client ID» в настройках плеера
  3. Rich Presence → Art Assets — картинки с ключами «vinyl», «play», «pause»
"""

import json
import re
import os
import threading
import time
import urllib.parse

try:
    from pypresence import Presence
    from pypresence.exceptions import PyPresenceException
    PYPRESENCE_AVAILABLE = True
except ImportError:
    Presence = None
    PyPresenceException = Exception
    PYPRESENCE_AVAILABLE = False

try:                                    # pypresence 4.4+: «Слушает …» и что показывать в статусе
    from pypresence.types import ActivityType, StatusDisplayType
except Exception:                       # noqa: BLE001
    ActivityType = StatusDisplayType = None

# ── Настройка ──────────────────────────────────────────────────────────
# Впишите сюда свой Client ID из Discord Developer Portal (см. докстринг выше).
CLIENT_ID = "1550141403106119782"

APP_NAME = "ECHOES"                     # «Слушает ECHOES»
DEFAULT_LARGE_IMAGE = "vinyl"
DEFAULT_LARGE_TEXT = "ECHOES"

RETRY_SEC = 15                          # пауза между попытками переподключения к Discord
RATE_MAX, RATE_WINDOW = 5, 20.5         # лимит Discord: 5 обновлений активности за 20 с
DEBOUNCE = 0.6                          # быстрые переключения треков — одним обновлением

_COVER_FILE = os.path.join(os.path.expanduser("~"), ".neon_player", "discord_covers.json")


def _fit(s, n=128):
    """Discord: строки 2…128 символов."""
    s = (s or "").strip()
    if len(s) > n:
        s = s[:n - 1] + "…"
    if len(s) == 1:
        s += " ·"
    return s


def _t(s: str) -> str:
    """Подписи в Discord — на языке интерфейса плеера."""
    try:
        import i18n
        return i18n.tr(s)
    except Exception:                                      # noqa: BLE001
        return s


URL_MAX = 256                           # Discord: details_url / state_url / large_url / картинка — не длиннее 256


def _ytm_search(q: str) -> str:
    """Ссылка на поиск YouTube Music, которая влезает в лимит Discord. Кириллица кодируется по 6 символов
    на букву, и длинные русские названия давали ссылку > 256 — такую активность Discord принимает у вас
    локально, но сервер её отбрасывает, и другие люди не видят, что вы слушаете."""
    base = "https://music.youtube.com/search?q="
    q = (q or "").strip()
    while q:
        u = base + urllib.parse.quote_plus(q)
        if len(u) <= URL_MAX:
            return u
        cut = q[:-1].rsplit(" ", 1)[0] if " " in q.strip() else q[:-1]
        q = cut.strip() if len(cut) < len(q) else q[:-1]
    return ""


def _url_ok(u) -> bool:
    return isinstance(u, str) and u.startswith("https://") and len(u) <= URL_MAX and " " not in u


def _sanitize(payload: dict) -> dict:
    """Всё, что может не пройти проверку сервера Discord, — убрать: тогда активность хоть и без ссылки,
    но видна всем. Строки 2…128, ссылки https ≤ 256, картинка — ключ ассета или https ≤ 256, кнопки ≤ 2."""
    p = {k: v for k, v in payload.items() if v not in (None, "")}
    for k in ("details_url", "state_url", "large_url", "small_url"):
        if k in p and not _url_ok(p[k]):
            p.pop(k)
    for k in ("large_image", "small_image"):
        v = p.get(k)
        if isinstance(v, str) and v.startswith("http") and not _url_ok(v):
            p[k] = DEFAULT_LARGE_IMAGE if k == "large_image" else None
            if p[k] is None:
                p.pop(k)
    for k in ("details", "state", "large_text", "small_text", "name"):
        if k in p:
            v = _fit(str(p[k]))
            if len(v) < 2:
                p.pop(k)
            else:
                p[k] = v
    if "small_text" in p and "small_image" not in p:
        p.pop("small_text")
    if "buttons" in p:
        btns = [{"label": (b.get("label") or "")[:32], "url": b.get("url")} for b in p["buttons"][:2]
                if (b.get("label") or "").strip() and isinstance(b.get("url"), str)
                and b["url"].startswith("https://") and len(b["url"]) <= 512]
        if btns:
            p["buttons"] = btns
        else:
            p.pop("buttons")
    ps = p.get("party_size")
    if ps is not None and not (isinstance(ps, (list, tuple)) and len(ps) == 2 and 0 < int(ps[0]) <= int(ps[1])):
        p.pop("party_size", None)
        p.pop("party_id", None)
    st, en = p.get("start"), p.get("end")
    if en is not None and (st is None or en <= st):
        p.pop("end")
    return p


_JUNK = re.compile(r"\s*[\(\[\{][^\)\]\}]*(official|video|audio|lyric|visuali[sz]er|clip|клип|mv|hd|hq|4k|remaster|"
                   r"prod\.?|текст|премьера|music video)[^\)\]\}]*[\)\]\}]", re.I)
_FEAT = re.compile(r"\s*[\(\[]?\s*\b(feat\.?|ft\.?|featuring)\s+[^\)\]]*[\)\]]?", re.I)


def _clean(artist, title):
    """«Исполнитель», «Трек» без мусора видео: (Official Video), [HD], feat. …, #хэштеги, «A - B» в названии."""
    a = (artist or "").replace(" - Topic", "").strip()
    t = (title or "").strip()
    if not a and " - " in t:
        a, t = t.split(" - ", 1)
    elif a and t.lower().startswith(a.lower() + " - "):
        t = t[len(a) + 3:]
    t = _FEAT.sub("", _JUNK.sub("", t))
    t = re.sub(r"#\S+", "", t).strip(" -–—|·")
    a = re.split(r",| & | x | X |;| feat\.? | ft\.? ", a)[0].strip()
    return a, (t or (title or "").strip())


class _Covers:
    """Публичные обложки по «исполнитель — трек»: Deezer и iTunes параллельно, кто первый нашёл; кэш на диске."""

    def __init__(self):
        import concurrent.futures as cf
        self.lock = threading.Lock()
        self.cache = {}
        self.busy = {}                                     # ключ → [колбэки]
        self.pool = cf.ThreadPoolExecutor(max_workers=6, thread_name_prefix="discord-cover")
        self._sess = None
        try:
            with open(_COVER_FILE, encoding="utf-8") as f:
                raw = json.load(f) or {}
            # промахи старой версии (искала только на Deezer) — переискать
            self.cache = {k: v for k, v in raw.items() if v.get("v") == 3}   # старое (без проверки совпадения) — заново
        except Exception:                                  # noqa: BLE001
            self.cache = {}

    def session(self):
        if self._sess is None:
            import requests
            s = requests.Session()
            s.headers["User-Agent"] = "ECHOES-player/1.0"
            self._sess = s
        return self._sess

    @staticmethod
    def key(artist, title):
        return f"{(artist or '').strip().lower()}\x1f{(title or '').strip().lower()}"

    def get(self, artist, title):
        with self.lock:
            return self.cache.get(self.key(artist, title))

    # ── источники: несколько вариантов, каждый с оценкой совпадения ──
    @staticmethod
    def _score(want, artist, title, dur, want_dur):
        try:
            from track_identity import match_score
            return float(match_score(want, artist or "", title or "", float(dur or 0), float(want_dur or 0)))
        except Exception:                                  # noqa: BLE001
            return 0.75 if (title or "").lower() in " ".join(t for _a, t in want).lower() else 0.0

    def _deezer(self, want, q, want_dur):
        r = self.session().get("https://api.deezer.com/search", params={"q": q, "limit": 5}, timeout=4)
        out = []
        for d in (r.json() or {}).get("data") or []:
            alb = d.get("album") or {}
            cover = alb.get("cover_big") or alb.get("cover_xl") or ""
            if cover:
                sc = self._score(want, (d.get("artist") or {}).get("name"), d.get("title"), d.get("duration"),
                                 want_dur)
                out.append((sc, {"cover": cover, "album": alb.get("title") or "", "link": d.get("link") or ""}))
        return out

    def _itunes(self, want, q, want_dur):
        r = self.session().get("https://itunes.apple.com/search",
                               params={"term": q, "entity": "song", "limit": 5}, timeout=4)
        out = []
        for d in (r.json() or {}).get("results") or []:
            art = d.get("artworkUrl100") or ""
            if art:
                sc = self._score(want, d.get("artistName"), d.get("trackName"),
                                 (d.get("trackTimeMillis") or 0) / 1000, want_dur)
                out.append((sc, {"cover": art.replace("100x100bb", "600x600bb"), "album": d.get("collectionName") or "",
                                 "link": d.get("trackViewUrl") or ""}))
        return out

    def _soundcloud(self, want, q, want_dur):
        """SoundCloud — для андеграунда, которого нет на Deezer / iTunes (название ищем как есть, с #хэштегами)."""
        import online_search
        out = []
        for d in online_search.sc_search(q, 6) or []:
            cover = (d.get("cover_url") or "").replace("-t300x300.", "-t500x500.")
            if cover and "default_avatar" not in cover:
                sc = self._score(want, d.get("artist"), d.get("title"), d.get("duration"), want_dur)
                out.append((sc, {"cover": cover, "album": "", "link": d.get("url") or ""}))
        return out

    def fetch(self, artist, title, done=None, duration=0):
        """В фоне найти обложку и альбом; done() — когда нашлась (или точно нет).
        Deezer, iTunes и SoundCloud — параллельно; берётся вариант с лучшим совпадением (≥ 70 %),
        уверенный (≥ 85 %) — сразу, не дожидаясь остальных."""
        k = self.key(artist, title)
        with self.lock:
            if k in self.cache:
                return
            if k in self.busy:
                if done:
                    self.busy[k].append(done)
                return
            self.busy[k] = [done] if done else []
        a, t = _clean(artist, title)
        raw_a = (artist or "").replace(" - Topic", "").strip()
        want = [(a, t), (raw_a, (title or "").strip())]

        def work():
            import concurrent.futures as cf
            futs = [self.pool.submit(self._deezer, want, f"{a} {t}".strip(), duration),
                    self.pool.submit(self._itunes, want, f"{a} {t}".strip(), duration),
                    self.pool.submit(self._soundcloud, want, f"{raw_a} {(title or '').strip()}".strip(), duration)]
            best, best_sc, errors = None, 0.0, 0
            try:
                for f in cf.as_completed(futs, timeout=7):
                    try:
                        found = f.result()
                    except Exception:                      # noqa: BLE001
                        errors += 1
                        continue
                    for sc, r in found:
                        if sc > best_sc:
                            best, best_sc = r, sc
                    if best is not None and best_sc >= 0.85:
                        break                              # уверенно — не ждём остальных
            except cf.TimeoutError:
                errors += 1
            if best is not None and best_sc >= 0.7:
                res = dict(best)
            else:
                res = None if errors == len(futs) else {"cover": "", "album": "", "link": ""}
            with self.lock:
                cbs = self.busy.pop(k, [])
                if res is not None:
                    res["v"] = 3
                    self.cache[k] = res
                    if len(self.cache) > 3000:
                        for kk in list(self.cache)[:500]:
                            self.cache.pop(kk, None)
                    snap = dict(self.cache)
            if res is not None:
                try:
                    os.makedirs(os.path.dirname(_COVER_FILE), exist_ok=True)
                    with open(_COVER_FILE, "w", encoding="utf-8") as f:
                        json.dump(snap, f, ensure_ascii=False)
                except Exception:                          # noqa: BLE001
                    pass
                for cb in cbs:
                    try:
                        cb()
                    except Exception:                      # noqa: BLE001
                        pass
        threading.Thread(target=work, daemon=True, name="discord-cover-lookup").start()


_ARTIST_FILE = os.path.join(os.path.expanduser("~"), ".neon_player", "discord_artists.json")


class _Artists:
    """Фото исполнителя для маленькой круглой картинки на обложке (Deezer, по имени; кэш на диске)."""

    def __init__(self, covers: "_Covers"):
        self.covers = covers
        self.lock = threading.Lock()
        self.busy = set()
        try:
            with open(_ARTIST_FILE, encoding="utf-8") as f:
                self.cache = json.load(f) or {}
        except Exception:                                  # noqa: BLE001
            self.cache = {}

    @staticmethod
    def key(name):
        return re.split(r",| & | x | feat\.? | ft\.? ", (name or "").replace(" - Topic", ""), maxsplit=1)[0].strip().lower()

    def get(self, name):
        with self.lock:
            return self.cache.get(self.key(name))

    def fetch(self, name, done=None):
        k = self.key(name)
        if not k:
            return
        with self.lock:
            if k in self.cache or k in self.busy:
                return
            self.busy.add(k)

        def work():
            res = None
            try:
                from track_identity import similarity
                r = self.covers.session().get("https://api.deezer.com/search/artist", params={"q": k, "limit": 5},
                                              timeout=5)
                best, bs = None, 0.0
                for d in (r.json() or {}).get("data") or []:
                    sc = similarity(k, (d.get("name") or "").lower())
                    if sc > bs:
                        best, bs = d, sc
                pic = (best or {}).get("picture_medium") or ""
                if best and bs >= 0.8 and pic and "/artist//" not in pic:     # «пустое» фото у Deezer — без хэша
                    res = {"pic": pic, "fans": int(best.get("nb_fan") or 0), "link": best.get("link") or ""}
                else:
                    res = {"pic": "", "fans": 0, "link": ""}
            except Exception:                              # noqa: BLE001
                res = None                                 # сеть — попробуем в другой раз
            with self.lock:
                self.busy.discard(k)
                if res is not None:
                    self.cache[k] = res
                    snap = dict(self.cache)
            if res is not None:
                try:
                    with open(_ARTIST_FILE, "w", encoding="utf-8") as f:
                        json.dump(snap, f, ensure_ascii=False)
                except Exception:                          # noqa: BLE001
                    pass
                if done and res.get("pic"):
                    try:
                        done()
                    except Exception:                      # noqa: BLE001
                        pass
        threading.Thread(target=work, daemon=True, name="discord-artist").start()


_LINK_NAMES = (("deezer.com", "Открыть на Deezer"), ("music.apple.com", "Открыть в Apple Music"),
               ("itunes.apple.com", "Открыть в Apple Music"), ("soundcloud.com", "Открыть на SoundCloud"),
               ("youtube.com", "Открыть на YouTube"), ("youtu.be", "Открыть на YouTube"),
               ("spotify.com", "Открыть в Spotify"))


def _link_label(url: str) -> str:
    u = (url or "").lower()
    return next((_t(lbl) for dom, lbl in _LINK_NAMES if dom in u), _t("Открыть трек"))


class DiscordPresence:
    """
    Использование в main.py:

        self.discord_rpc = DiscordPresence(enabled=self.settings.get("discord_rpc", True))
        self.discord_rpc.start()
        # при смене трека / паузе / перемотке:
        self.discord_rpc.update_track(title, artist, duration, position, is_playing, album=album)
        # ничего не играет:
        self.discord_rpc.clear()
        # при закрытии окна:
        self.discord_rpc.stop()
    """

    def __init__(self, client_id: str = CLIENT_ID, enabled: bool = True, online_covers: bool = True,
                 show_paused: bool = True):
        self.client_id = client_id
        self._enabled = bool(enabled) and PYPRESENCE_AVAILABLE and bool(client_id) and client_id.strip("0") != ""
        self.online_covers = bool(online_covers)
        self.show_paused = bool(show_paused)
        self._rpc = None
        self._connected = False
        self._lock = threading.Lock()
        self._stop_flag = False
        self._thread = None
        self._last_update_ts = 0.0
        self._pending_state = None                         # последний presence, который нужно отправить
        self._last_args = None
        self._covers = _Covers()
        self._artists = _Artists(self._covers)
        # крутые штуки (настройки — set_features): живая строка текста, фото исполнителя, место в очереди,
        # кнопка-ссылка на трек, статистика в подсказке
        self.features = {"lyrics": True, "artist": True, "queue": True, "link": True, "stats": True}
        self._cur_key = None
        self._hold_until = 0.0
        self._pending_since = 0.0
        self._last_sent = None
        self._legacy = ActivityType is None                # старый pypresence — без «Слушает»

        if enabled and PYPRESENCE_AVAILABLE and (not client_id or client_id.strip("0") == ""):
            print("[discord_rpc] CLIENT_ID не задан — впишите свой Application ID из "
                  "Discord Developer Portal в настройках плеера, чтобы включить Rich Presence.")
        if enabled and not PYPRESENCE_AVAILABLE:
            print("[discord_rpc] Пакет 'pypresence' не установлен — "
                  "выполните: pip install pypresence")

    # ------------------------------------------------------------------ #
    #  Публичный API                                                      #
    # ------------------------------------------------------------------ #

    def is_available(self) -> bool:
        return self._enabled

    def set_enabled(self, value: bool):
        value = bool(value)
        if value == self._enabled:
            return
        self._enabled = value and PYPRESENCE_AVAILABLE and bool(self.client_id)
        if self._enabled:
            self.start()
            if self._last_args:
                self._refresh()
        else:
            self.clear()
            self.stop()

    def _refresh(self):
        """Ещё раз отправить последний трек (позиция — с поправкой на прошедшее время)."""
        la = self._last_args
        if not la:
            return
        (title, artist, duration, position, playing), kw, t0 = la
        if playing:
            position += time.time() - t0
        self.update_track(title, artist, duration, position, playing, **kw)

    def _release_and_refresh(self):
        self._hold_until = 0.0
        self._refresh()

    def prefetch(self, artist: str, title: str, duration: float = 0):
        """Заранее найти обложку (следующий трек очереди) — при смене трека она будет сразу."""
        if self._enabled and self.online_covers and (title or "").strip():
            self._covers.fetch((artist or "").strip(), title.strip(), duration=duration)

    def set_online_covers(self, on: bool):
        self.online_covers = bool(on)
        if self._last_args:
            self._refresh()

    def set_features(self, **kw):
        for k, v in kw.items():
            if k in self.features:
                self.features[k] = bool(v)
        if self._last_args:
            self._refresh()

    def set_show_paused(self, on: bool):
        self.show_paused = bool(on)
        if self._last_args:
            self._refresh()

    def start(self):
        if not self._enabled or self._thread is not None:
            return
        self._stop_flag = False
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_flag = True
        with self._lock:
            self._disconnect()
        self._thread = None

    def update_track(self, title: str, artist: str, duration: float, position: float, is_playing: bool,
                     cover_url: str = "", album: str = "", genre: str = "", lyric: str = "", queue=None,
                     stats_text: str = "", link: str = ""):
        """Ставит трек в очередь на публикацию в Discord: исполнитель → трек → альбом.
        lyric — строка текста, которую поют сейчас (третья строка статуса вместо альбома);
        queue — (номер, всего, название очереди) → «(3 из 12)»; stats_text — подсказка на фото исполнителя;
        link — своя ссылка на трек (онлайн-трек: его страница)."""
        self._last_args = ((title, artist, duration, position, is_playing),
                           {"cover_url": cover_url, "album": album, "genre": genre, "lyric": lyric, "queue": queue,
                            "stats_text": stats_text, "link": link}, time.time())
        if not self._enabled:
            return
        if not is_playing and not self.show_paused:
            self.clear()
            return
        title = (title or "").strip() or _t("Неизвестный трек")
        artist = (artist or "").strip()
        self._cur_key = self._covers.key(artist, title)
        cover_url = (cover_url or "").strip()
        if "googleusercontent.com" in cover_url:           # миниатюры YouTube Music — крупнее
            cover_url = re.sub(r"=w\d+-h\d+", "=w544-h544", cover_url)
        if cover_url and not _url_ok(cover_url):
            cover_url = ""                                 # слишком длинная ссылка — Discord отбросит; ищем свою
        found = None
        if not cover_url and self.online_covers:
            found = self._covers.get(artist, title)
            if found is None:
                key = self._covers.key(artist, title)
                self._hold_until = time.time() + 1.5       # подождать обложку: одно обновление вместо двух
                self._covers.fetch(artist, title, lambda: self._cur_key == key and self._release_and_refresh(),
                                   duration=duration)
        cover = cover_url or (found or {}).get("cover") or DEFAULT_LARGE_IMAGE
        album = (album or "").strip()
        if not album or album.lower() in ("unknown", "неизвестный альбом", title.lower()):
            album = (found or {}).get("album") or album
        now = time.time()
        q = f"{artist} {title}".strip()
        F = self.features
        # третья строка — альбом или сингл (живой текст убран: Discord обновляет статус раз в ~15 с — он отставал)
        kind = "Альбом"
        m = re.search(r"\s*[-–(\[]\s*(single|ep|сингл)\s*[)\]]?\s*$", album or "", re.I)
        if m:                                              # «Название - Single» / «Название (EP)»
            kind = "EP" if m.group(1).lower() == "ep" else "Сингл"
            album = album[:m.start()].strip()
        if album and (kind != "Альбом" or _clean(artist, album)[1].casefold() != _clean(artist, title)[1].casefold()):
            third = _t(kind) + f": {album}"
        elif artist or title:
            third = _t("Сингл") + f": {title}"
        else:
            third = DEFAULT_LARGE_TEXT
        payload = {
            # первая строка — исполнитель, вторая — трек, третья (подпись обложки) — альбом или строка текста
            "details": _fit(artist or title),
            "state": _fit(title if artist else (album or APP_NAME)),
            "large_image": cover,
            "large_text": _fit(third),
            "small_image": "play" if is_playing else "pause",
            "small_text": _t(f"Слушает в {APP_NAME}") if is_playing else _t("Пауза"),
        }
        # фото исполнителя — маленький кружок на обложке (на паузе — значок паузы)
        if F["artist"] and artist and is_playing:
            ap = self._artists.get(artist)
            if ap is None:
                key = self._covers.key(artist, title)
                self._artists.fetch(artist, lambda: self._cur_key == key and self._release_and_refresh())
            elif ap.get("pic"):
                payload["small_image"] = ap["pic"]
                payload["small_text"] = _fit(artist)
                if ap.get("link"):
                    payload["small_url"] = ap["link"]
        if F["stats"] and stats_text:
            payload["small_text"] = _fit(f"{payload['small_text']} · {stats_text}" if payload.get("small_text")
                                         else stats_text)
        # место в очереди: «(3 из 12)» рядом с названием
        if F["queue"] and queue and int(queue[1] or 0) > 1:
            i, n = int(queue[0]), int(queue[1])
            payload["party_id"] = _fit(f"echoes:{queue[2] or 'queue'}", 120)
            payload["party_size"] = [max(1, min(i, n)), n]
        if is_playing and duration and duration > 0:
            start_ts = int(now - max(0.0, position))
            payload["start"] = start_ts
            payload["end"] = int(start_ts + duration)      # полоса прогресса, как у Spotify
        if not self._legacy:
            payload.update({
                "activity_type": ActivityType.LISTENING,
                "status_display_type": StatusDisplayType.DETAILS,   # «Слушает <исполнитель>» в списке
                "name": APP_NAME,
                "details_url": _ytm_search(artist) if artist else _ytm_search(title),
                "state_url": _ytm_search(q),
                "buttons": [{"label": _t("Слушать на YouTube Music")[:32], "url": _ytm_search(q)}],
            })
            tlink = (link or "").strip() or (found or {}).get("link") or ""
            if tlink:
                payload["large_url"] = tlink
                if F["link"] and _url_ok(tlink) and "music.youtube.com" not in tlink:
                    payload["buttons"].append({"label": _link_label(tlink)[:32], "url": tlink})
        with self._lock:
            self._pending_state = ("update", payload)
            self._pending_since = time.time()

    def set_activity(self, name: str, details: str, state: str = "", image: str = DEFAULT_LARGE_IMAGE,
                     image_text: str = "", small_image: str = "", small_text: str = "", start: float = 0,
                     end: float = 0, buttons=None):
        """Своя активность «Играет в …» — вместо музыки, пока вы в студии или в конструкторе тем.
        image — ключ Art Asset приложения Discord (studio, theme_studio) или https-ссылка."""
        if not self._enabled:
            return
        details, state = _t(details), _t(state) if state else state
        payload = {"details": _fit(details), "large_image": image or DEFAULT_LARGE_IMAGE,
                   "large_text": _fit(image_text or name)}
        if state:
            payload["state"] = _fit(state)
        if small_image:
            payload["small_image"] = small_image
            payload["small_text"] = _fit(small_text or name)
        if start:
            payload["start"] = int(start)
        if end:
            payload["end"] = int(end)
        if not self._legacy:
            payload.update({"activity_type": ActivityType.PLAYING, "status_display_type": StatusDisplayType.NAME,
                            "name": name})
            if buttons:
                payload["buttons"] = [{"label": _t(b["label"])[:32], "url": b["url"]} for b in buttons[:2]]
        with self._lock:
            self._pending_state = ("update", payload)
            self._pending_since = time.time()

    def clear(self):
        """Убирает activity из профиля (когда ничего не играет)."""
        if not self._enabled:
            return
        with self._lock:
            self._pending_state = ("clear", None)
            self._pending_since = time.time()

    # ------------------------------------------------------------------ #
    #  Внутреннее: один поток на всё подключение + троттлинг отправки     #
    # ------------------------------------------------------------------ #

    def _connect(self) -> bool:
        try:
            self._rpc = Presence(self.client_id)
            self._rpc.connect()
            self._connected = True
            print("[discord_rpc] Подключено к Discord.")
            return True
        except Exception:                                  # noqa: BLE001
            self._connected = False
            self._rpc = None
            return False

    def _disconnect(self):
        if self._rpc is not None:
            try:
                self._rpc.clear()
            except Exception:                              # noqa: BLE001
                pass
            try:
                self._rpc.close()
            except Exception:                              # noqa: BLE001
                pass
        self._rpc = None
        self._connected = False

    def _send(self, payload):
        payload = _sanitize(payload)
        # друзья не видели статус с ссылками/кнопками/«очередью» (проверено 2026-10-18: «Слушает» без них виден) —
        # отправляем только надёжные поля
        for k in ("details_url", "state_url", "large_url", "small_url", "buttons", "party_id", "party_size"):
            payload.pop(k, None)
        try:
            self._rpc.update(**payload)
        except TypeError:                                  # старый pypresence: без новых полей
            self._legacy = True
            for k in ("activity_type", "status_display_type", "name", "details_url", "state_url", "large_url",
                      "small_url", "buttons"):
                payload.pop(k, None)
            self._rpc.update(**payload)

    def _same_as_sent(self, payload) -> bool:
        """То же, что уже показано (время старта ±2 с) — не тратить лимит Discord."""
        last = self._last_sent
        if last is None:
            return False
        a = {k: v for k, v in payload.items() if k not in ("start", "end")}
        b = {k: v for k, v in last.items() if k not in ("start", "end")}
        if a != b:
            return False
        return (abs((payload.get("start") or 0) - (last.get("start") or 0)) <= 2
                and ("end" in payload) == ("end" in last))

    def _run_loop(self):
        """Отправка в Discord. У Discord лимит: 5 обновлений за 20 с, лишние он молча выбрасывает —
        поэтому статус «застревал» на прошлом треке при быстром переключении. Здесь: быстрые смены
        склеиваются (DEBOUNCE), отправок не больше лимита, и САМОЕ ПОСЛЕДНЕЕ состояние уходит всегда,
        как только освобождается слот."""
        sent_ts = []
        while not self._stop_flag:
            if not self._connected:
                if not self._connect():
                    time.sleep(RETRY_SEC)
                    continue
                self._last_sent = None
                if self._last_args and self._pending_state is None:   # переподключились — показать снова
                    self._refresh()

            now = time.time()
            sent_ts = [t for t in sent_ts if now - t < RATE_WINDOW]
            pending = None
            with self._lock:
                ready = (self._pending_state is not None and now - self._pending_since >= DEBOUNCE
                         and now >= self._hold_until)
                if ready:
                    kind, payload = self._pending_state
                    if kind == "update" and self._same_as_sent(payload):
                        self._pending_state = None             # уже показано — лимит не тратим
                    elif len(sent_ts) < RATE_MAX:
                        pending = self._pending_state
                        self._pending_state = None
            if pending is not None:
                kind, payload = pending
                try:
                    if kind == "clear":
                        self._rpc.clear()
                        self._last_sent = None
                    else:
                        self._send(dict(payload))
                        self._last_sent = dict(payload)
                    sent_ts.append(now)
                    self._last_update_ts = now
                except (PyPresenceException, Exception) as e:    # noqa: BLE001
                    print("[discord_rpc] Ошибка отправки presence:", e)
                    self._connected = False
                    self._rpc = None
                    with self._lock:
                        if self._pending_state is None:
                            self._pending_state = pending

            time.sleep(0.2)

        self._disconnect()
