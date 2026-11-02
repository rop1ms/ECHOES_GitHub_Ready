# artist_net.py
"""
Онлайн-данные об исполнителях для темы Echoes Music (как карточка артиста
в Яндекс Музыке): фото, поклонники, популярные треки, альбомы, похожие
исполнители, биография, слушатели/прослушивания.

Источники (без ключей, кроме Last.fm):
  • Deezer API      — фото, поклонники, число альбомов, топ-треки (с «рангом»),
                      альбомы с годами, похожие исполнители;
  • Wikipedia REST  — краткая биография (ru, затем en), с проверкой, что
                      статья именно про музыканта;
  • Last.fm         — слушатели, прослушивания, био, теги (если в настройках
                      указан ключ — тот же, что для «Настроения дня»).

Всё кэшируется в ~/.neon_player/net_cache (данные — на 3 дня, картинки —
навсегда), так что повторное открытие мгновенное и работает офлайн.
Модуль без Qt: fetch_artist() вызывается из фонового потока.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import requests

try:
    from track_identity import similarity, norm
except Exception:                                  # noqa: BLE001
    from difflib import SequenceMatcher

    def norm(s):
        return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (s or "").casefold())).strip()

    def similarity(a, b):
        return SequenceMatcher(None, norm(a), norm(b)).ratio()

CACHE = Path.home() / ".neon_player" / "net_cache"
IMG_DIR = CACHE / "img"
ART_DIR = CACHE / "artists"
TTL = 3 * 86400
ARTIST_SCHEMA = 4                                  # 3: SoundCloud для малоизвестных и однофамильцев
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) EchoesPlayer/1.0"}
_TLS = threading.local()
_FAILED = {}                                       # имя → когда не ответил ни один сервер (не долбить сеть)


def _session() -> requests.Session:
    """Своя сессия на поток: соединения переиспользуются (keep-alive). Без неё каждый запрос — новое
    TLS-рукопожатие; карточка артиста — десятки запросов, и несколько карточек грузили процессор на 2–3 ядра."""
    s = getattr(_TLS, "s", None)
    if s is None:
        s = requests.Session()
        s.headers.update(UA)
        _TLS.s = s
    return s


_DZ_LOCK = threading.Lock()
_DZ_TIMES: list = []


def _dz_wait():
    """Deezer пускает ~50 запросов за 5 с с одного адреса — дальше отвечает ошибкой квоты. Держимся ниже."""
    while True:
        with _DZ_LOCK:
            now = time.time()
            _DZ_TIMES[:] = [t for t in _DZ_TIMES if now - t < 5.0]
            if len(_DZ_TIMES) < 40:
                _DZ_TIMES.append(now)
                return
            delay = 5.0 - (now - _DZ_TIMES[0]) + 0.05
        time.sleep(max(0.05, delay))


def _get(url, params=None, timeout=8):
    if "api.deezer.com" in url:
        _dz_wait()
    try:
        r = _session().get(url, params=params, timeout=timeout)
        if r.status_code == 200:
            j = r.json()
            if isinstance(j, dict) and isinstance(j.get("error"), dict) and j["error"].get("code") == 4:
                time.sleep(2.0)                                # квота всё-таки кончилась — одна повторная попытка
                j = _session().get(url, params=params, timeout=timeout).json()
            return j
    except Exception as e:                         # noqa: BLE001
        print("[artist_net]", url.split("/")[2], e.__class__.__name__)
    return None


def _img_path(url: str) -> Path:
    ext = ".png" if url.lower().split("?")[0].endswith(".png") else ".jpg"
    return IMG_DIR / (hashlib.md5(url.encode()).hexdigest() + ext)


_FOUND: dict = {}                                  # url → путь уже скачанной картинки (без stat на каждый показ)
_MISS: dict = {}                                   # url → когда проверяли, что картинки ещё нет
_MISS_S = 30.0


def cached_image(url: str) -> str:
    """Путь к уже скачанной картинке ('' — ещё нет), без сети."""
    if not url:
        return ""
    hit = _FOUND.get(url)
    if hit:
        return hit
    import time
    now = time.monotonic()
    if now - _MISS.get(url, -1e9) < _MISS_S:             # недавно не было — не трогать диск снова
        return ""
    path = _img_path(url)
    try:
        if path.stat().st_size > 200:
            _FOUND[url] = str(path)
            _MISS.pop(url, None)
            return str(path)
    except OSError:
        pass
    _MISS[url] = now
    return ""


def image(url: str) -> str:
    """Скачать картинку в кэш (если ещё нет) и вернуть путь к файлу ('' — не вышло)."""
    if not url:
        return ""
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    path = _img_path(url)
    if path.exists() and path.stat().st_size > 200:
        return str(path)
    try:
        r = _session().get(url, timeout=10)
        if r.status_code == 200 and len(r.content) > 200:
            tmp = path.with_suffix(".part")
            tmp.write_bytes(r.content)
            tmp.replace(path)
            _FOUND[url] = str(path)
            _MISS.pop(url, None)
            return str(path)
    except Exception:                              # noqa: BLE001
        pass
    return ""


def _slug(name):
    return hashlib.md5(norm(name).encode()).hexdigest()


_ART_MEMO: dict = {}                               # slug → данные (или None — файла нет); пишет только _save


def cached(name):
    """Сохранённые сведения об исполнителе. В памяти: профиль и «Для вас» спрашивают каждого исполнителя
    при каждом показе — раньше это было чтение и разбор JSON с диска на каждый вызов (сотни мс на клик)."""
    slug = _slug(name)
    if slug in _ART_MEMO:
        return _ART_MEMO[slug]
    p = ART_DIR / f"{slug}.json"
    try:
        d = json.loads(p.read_text("utf-8"))
    except Exception:                              # noqa: BLE001
        d = None
    if len(_ART_MEMO) > 4000:
        _ART_MEMO.clear()
    _ART_MEMO[slug] = d
    return d


def _save(name, data):
    try:
        ART_DIR.mkdir(parents=True, exist_ok=True)
        (ART_DIR / f"{_slug(name)}.json").write_text(json.dumps(data, ensure_ascii=False), "utf-8")
        _ART_MEMO[_slug(name)] = data
    except Exception:                              # noqa: BLE001
        pass


# ── Deezer ──────────────────────────────────────────────────────────────── #

def _deezer(name, titles=()):
    j = _get("https://api.deezer.com/search/artist", {"q": name, "limit": 8})
    cands = (j or {}).get("data") or []
    best, bs = None, 0.0
    for a in cands:
        s = similarity(name, a.get("name", ""))
        s += min(0.08, (a.get("nb_fan") or 0) / 5e6)          # при равенстве — популярнее
        if s > bs:
            best, bs = a, s
    if not best or bs < 0.82:
        return None
    aid = best["id"]
    info = _get(f"https://api.deezer.com/artist/{aid}") or best
    top = (_get(f"https://api.deezer.com/artist/{aid}/top", {"limit": 10}) or {}).get("data") or []
    albums = (_get(f"https://api.deezer.com/artist/{aid}/albums", {"limit": 30}) or {}).get("data") or []
    related = (_get(f"https://api.deezer.com/artist/{aid}/related", {"limit": 12}) or {}).get("data") or []
    if int(info.get("nb_fan") or 0) < 50000:
        albums, top = _split_homonyms(albums, top, titles)
    seen, alb = set(), []
    for a in sorted(albums, key=lambda x: x.get("release_date") or "", reverse=True):
        key = norm(a.get("title", ""))
        if key in seen:
            continue
        seen.add(key)
        alb.append({"title": a.get("title", ""), "year": (a.get("release_date") or "")[:4],
                    "type": a.get("record_type", ""), "cover_url": a.get("cover_big") or a.get("cover_medium"),
                    "fans": a.get("fans", 0)})
    return {
        "deezer_id": aid,
        "name": info.get("name") or name,
        "picture_url": info.get("picture_xl") or info.get("picture_big") or "",
        "fans": int(info.get("nb_fan") or 0),
        "albums_count": int(info.get("nb_album") or 0),
        "top": [{"title": t.get("title_short") or t.get("title", ""), "rank": int(t.get("rank") or 0),
                 "duration": int(t.get("duration") or 0), "album": (t.get("album") or {}).get("title", ""),
                 "cover_url": (t.get("album") or {}).get("cover_medium", ""),
                 "artists": ", ".join(c.get("name", "") for c in (t.get("contributors") or [])) or name}
                for t in top],
        "albums": alb[:16],
        "related": [{"name": r.get("name", ""), "fans": int(r.get("nb_fan") or 0),
                     "picture_url": r.get("picture_medium") or ""} for r in related],
    }


def _split_homonyms(albums, top, titles):
    """Deezer склеивает тёзок в один профиль («quinn»: хайперпоп и флейта для релакса в одной дискографии).
    Опора — альбомы популярных треков и релизы с треками из вашей коллекции. Чужой релиз — у которого сразу
    другой лейбл, другой жанр и мало поклонников: у независимых артистов с кучей мелких лейблов (m1v) жанр
    тот же, и их релизы остаются."""
    ids = list(dict.fromkeys([a["id"] for a in albums] + [(t.get("album") or {}).get("id") for t in top]))
    ids = [i for i in ids if i][:36]
    if len(ids) < 3:
        return albums, top
    want = {norm(t) for t in titles if norm(t)}
    anchors = {a["id"] for a in albums if norm(a.get("title", "")) in want}
    anchors |= {(t.get("album") or {}).get("id") for t in top
                if norm(t.get("title_short") or t.get("title", "")) in want}
    if not anchors:
        anchors = {(t.get("album") or {}).get("id") for t in top}
    anchors.discard(None)
    if not anchors:
        return albums, top

    def info(i):
        j = _get(f"https://api.deezer.com/album/{i}") or {}
        return str(j.get("label") or "").strip().casefold(), int(j.get("fans") or 0), int(j.get("genre_id") or -1)
    with ThreadPoolExecutor(max_workers=4) as ex:
        meta = dict(zip(ids, ex.map(info, ids)))
    labels = {meta[i][0] for i in anchors if i in meta and meta[i][0] not in ("", "independent", "self-released")}
    genres = {meta[i][2] for i in anchors if i in meta and meta[i][2] > 0}
    # опора по лейблу шире: релизы того же лейбла — тоже свои (их жанры — тоже «свои»)
    genres |= {m[2] for m in meta.values() if m[0] in labels and m[2] > 0}
    if not labels or not genres:
        return albums, top
    top_fans = max(m[1] for i, m in meta.items() if i in anchors or m[0] in labels)

    def mine(i):
        lb, fans, g = meta.get(i, ("", 0, -1))
        return i in anchors or lb in labels or g in genres or fans >= max(20, 0.3 * top_fans)
    return [a for a in albums if mine(a["id"])], [t for t in top if mine((t.get("album") or {}).get("id"))]


# ── YouTube Music: «слушателей за месяц» (без ключей, ytmusicapi) ───────── #

_YTM = None
_YTM_LOCK = threading.Lock()


def _count(s) -> int:
    """«125K» / «5.95M» / «7,24 тыс.» / «373,726,240 views» → число."""
    t = str(s or "").strip().lower().replace("\xa0", " ")
    m = re.search(r"([\d][\d\s.,]*)\s*(k|m|b|тыс|млн|млрд)?", t)
    if not m:
        return 0
    num, suf = m.group(1).strip(), m.group(2) or ""
    if suf:
        num = num.replace(" ", "").replace(",", ".")
        try:
            v = float(num)
        except ValueError:
            return 0
        return int(v * {"k": 1e3, "тыс": 1e3, "m": 1e6, "млн": 1e6, "b": 1e9, "млрд": 1e9}[suf])
    digits = re.sub(r"\D", "", num)
    return int(digits) if digits else 0


def _ytm(name):
    """Исполнитель на YouTube Music: слушатели за месяц и подписчики (имя должно совпасть)."""
    global _YTM
    try:
        with _YTM_LOCK:
            if _YTM is None:
                from ytmusicapi import YTMusic
                _YTM = YTMusic()
            yt = _YTM
        res = yt.search(name, filter="artists", limit=5) or []
    except Exception as e:                                     # noqa: BLE001
        print("[artist YTM]", type(e).__name__, e)
        return None
    cands = [a for a in res[:5] if a.get("browseId") and similarity(name, a.get("artist") or "") >= 0.86][:3]
    if not cands:
        return {"ytm_checked": True, "ytm_related": [], "ytm_bio": ""}
    best = None                                                # однофамильцы: берём того, у кого аудитория больше
    for a in cands:
        try:
            d = yt.get_artist(a["browseId"])
        except Exception as e:                                 # noqa: BLE001
            print("[artist YTM]", type(e).__name__, e)
            continue
        cur = {"ytm_checked": True, "ytm_monthly": _count(d.get("monthlyListeners")),
               "ytm_subs": _count(d.get("subscribers")), "ytm_id": a["browseId"],
               "ytm_views": _count(d.get("views")),
               "ytm_bio": " ".join(str(d.get("description") or "").split()),
               "ytm_related": [{"name": r.get("title", ""), "subs": _count(r.get("subscribers")),
                                "picture_url": ((r.get("thumbnails") or [{}])[-1] or {}).get("url", "")}
                               for r in ((d.get("related") or {}).get("results") or [])[:14] if r.get("title")]}
        if best is None or (cur["ytm_monthly"], cur["ytm_subs"]) > (best["ytm_monthly"], best["ytm_subs"]):
            best = cur
    return best or None


def audience(d: dict) -> tuple[int, str]:
    """Самая показательная цифра аудитории и подпись: слушатели за месяц (YouTube Music) → слушатели
    (Last.fm) → больше из поклонников Deezer / подписчиков SoundCloud / YouTube."""
    d = d or {}
    if int(d.get("ytm_monthly") or 0) > 0:
        return int(d["ytm_monthly"]), "слушателей за месяц"
    if int(d.get("listeners") or 0) > 0:
        return int(d["listeners"]), "слушателей"
    best = max((int(d.get("fans") or 0), "поклонников"), (int(d.get("sc_followers") or 0), "подписчиков"),
               (int(d.get("ytm_subs") or 0), "подписчиков"))
    return best if best[0] > 0 else (0, "")


def ensure_audience(name: str, data: dict) -> dict:
    """Старая карточка из кэша без данных YouTube Music / MusicBrainz — дополнить и сохранить."""
    if not data or data.get("error"):
        return data
    changed = False
    if not data.get("ytm_checked") or "ytm_related" not in data or "ytm_bio" not in data:
        y = _ytm(name)
        if y:
            data.update(y)
            changed = True
    if "mb_checked" not in data:
        data.update(_mb(data.get("name") or name) or {"mb_checked": True})
        changed = True
    if changed:
        _fill_bio(data)
        _save(name, data)
    return data


# ── MusicBrainz (без ключей): откуда, с какого года, состав, ссылки на соцсети ── #

_MB_LINKS = (("instagram.com", "Instagram"), ("twitter.com", "X / Twitter"), ("x.com", "X / Twitter"),
             ("vk.com", "ВКонтакте"), ("t.me", "Telegram"), ("tiktok.com", "TikTok"),
             ("open.spotify.com", "Spotify"), ("music.apple.com", "Apple Music"), ("bandcamp.com", "Bandcamp"),
             ("music.yandex", "Яндекс Музыка"), ("genius.com", "Genius"), ("youtube.com", "YouTube"))


def _mb(name):
    j = _get("https://musicbrainz.org/ws/2/artist/", {"query": f'artist:"{name}"', "fmt": "json", "limit": 5})
    best = None
    for a in (j or {}).get("artists") or []:
        if similarity(name, a.get("name", "")) >= 0.9 and int(a.get("score") or 0) >= 90:
            best = a
            break
    if not best:
        return {"mb_checked": True}
    time.sleep(1.0)                                            # MusicBrainz просит не чаще раза в секунду
    d = _get(f"https://musicbrainz.org/ws/2/artist/{best['id']}",
             {"inc": "url-rels+genres+tags+artist-rels+aliases", "fmt": "json"}) or best
    life = d.get("life-span") or {}
    area = (d.get("begin-area") or {}).get("name") or ""
    country = (d.get("area") or {}).get("name") or ""
    links, seen = [], set()
    for r in d.get("relations") or []:
        url = ((r.get("url") or {}).get("resource")) or ""
        if not url:
            continue
        for dom, title in _MB_LINKS:
            if dom in url and title not in seen:
                seen.add(title)
                links.append({"title": title, "url": url})
        if r.get("type") == "official homepage" and "Сайт" not in seen:
            seen.add("Сайт")
            links.append({"title": "Сайт", "url": url})
    members = [((r.get("artist") or {}).get("name") or "") for r in d.get("relations") or []
               if r.get("type") == "member of band" and r.get("direction") == "backward"]
    genres = [g.get("name", "") for g in sorted(d.get("genres") or [], key=lambda g: -int(g.get("count") or 0))]
    if not genres:
        genres = [t.get("name", "") for t in sorted(d.get("tags") or [], key=lambda t: -int(t.get("count") or 0))]
    return {"mb_checked": True, "mb_id": best["id"], "mb_type": d.get("type") or "",
            "mb_country": country, "mb_area": area if area != country else "",
            "mb_begin": life.get("begin") or "", "mb_ended": bool(life.get("ended")),
            "mb_end": life.get("end") or "", "mb_disamb": d.get("disambiguation") or "",
            "mb_aliases": [x.get("name", "") for x in d.get("aliases") or []][:6],
            "mb_members": list(dict.fromkeys(m for m in members if m))[:10], "mb_genres": [g for g in genres if g][:8],
            "links": links[:10]}


_RU_PLACES = {"Russia": "Россия", "Germany": "Германия", "United States": "США", "United Kingdom": "Великобритания",
              "Ukraine": "Украина", "Belarus": "Беларусь", "Kazakhstan": "Казахстан", "Japan": "Япония",
              "France": "Франция", "Canada": "Канада", "Sweden": "Швеция", "Italy": "Италия", "Spain": "Испания",
              "Poland": "Польша", "Finland": "Финляндия", "Norway": "Норвегия", "Australia": "Австралия",
              "South Korea": "Южная Корея", "Netherlands": "Нидерланды", "Brazil": "Бразилия", "Moscow": "Москва",
              "Saint Petersburg": "Санкт-Петербург", "Bryansk": "Брянск", "London": "Лондон",
              "Los Angeles": "Лос-Анджелес", "New York": "Нью-Йорк", "Berlin": "Берлин", "Kyiv": "Киев"}


def _ru_place(x):
    return _RU_PLACES.get(x, x)


def facts(d: dict) -> list:
    """Короткие факты для «Об исполнителе»: [(подпись, значение)]."""
    d = d or {}
    out = []
    t = d.get("mb_type")
    begin = str(d.get("mb_begin") or "")[:4]
    if begin:
        end = str(d.get("mb_end") or "")[:4] if d.get("mb_ended") else ""
        out.append(("Основана" if t == "Group" else "Год рождения" if t == "Person" else "Начало",
                    begin + (f" — {end}" if end else "")))
    place = ", ".join(_ru_place(x) for x in (d.get("mb_area"), d.get("mb_country")) if x)
    if place:
        out.append(("Откуда", place))
    alb = d.get("albums") or []
    years = sorted(int(a["year"]) for a in alb if str(a.get("year") or "").isdigit())
    if years and not (begin and t == "Group"):           # у групп со стажем Deezer отдаёт не всю дискографию
        out.append(("Первый релиз", str(years[0])))
        last = max(alb, key=lambda a: a.get("year") or "")
        out.append(("Последний релиз", f"{last.get('title', '')} ({last.get('year', '')})"))
    kinds = {}
    for a in alb:
        k = a.get("type") or "album"
        kinds[k] = kinds.get(k, 0) + 1
    if kinds:
        names = {"album": "альбомов", "single": "синглов", "ep": "EP", "compile": "сборников"}
        out.append(("Дискография", ", ".join(f"{n} {names.get(k, k)}" for k, n in kinds.items())))
    top = d.get("top") or []
    if top and top[0].get("title"):
        out.append(("Самый популярный трек", top[0]["title"]))
    if d.get("mb_members"):
        out.append(("Состав", ", ".join(dict.fromkeys(d["mb_members"]))))
    al = [a for a in d.get("mb_aliases") or [] if a and norm(a) != norm(d.get("name") or "")]
    if al:
        out.append(("Также известен как", ", ".join(al[:4])))
    return out


def _fill_bio(d: dict):
    """Описания нигде нет (малоизвестный исполнитель) — берём с YouTube Music, иначе собираем из фактов."""
    if d.get("bio") and d.get("bio_src") != "Echoes":
        return
    if d.get("ytm_bio"):
        d["bio"], d["bio_src"] = d["ytm_bio"], "YouTube Music"
        return
    name = d.get("name") or ""
    who = "группа" if d.get("mb_type") == "Group" else "исполнитель"
    s = f"{name} — {who}"
    place = ", ".join(_ru_place(x) for x in (d.get("mb_area"), d.get("mb_country")) if x)
    if place:
        s += f" ({place})"
    tags = d.get("mb_genres") or d.get("tags") or []
    if tags:
        s += f", жанры: {', '.join(tags[:3])}"
    parts = [s + "."]
    fa = dict(facts(d))
    if fa.get("Первый релиз"):
        parts.append(f"Выпускает музыку с {fa['Первый релиз']} года"
                     + (f" — {fa['Дискография']}." if fa.get("Дискография") else "."))
    if fa.get("Последний релиз"):
        parts.append(f"Последний релиз — {fa['Последний релиз']}.")
    if fa.get("Самый популярный трек"):
        parts.append(f"Самый популярный трек — «{fa['Самый популярный трек']}».")
    n, what = audience(d)
    if n:
        parts.append(f"Аудитория: {human(n)} {what}.")
    rel = [r.get("name") for r in (d.get("related") or d.get("ytm_related") or [])[:4] if r.get("name")]
    if rel:
        parts.append(f"Поклонники также слушают {', '.join(rel)}.")
    if len(parts) > 1:
        d["bio"], d["bio_src"] = " ".join(parts), "Echoes"


# ── Wikipedia ───────────────────────────────────────────────────────────── #

_MUSIC_WORDS = re.compile(r"музык|певец|певиц|рэпер|исполнит|групп|продюсер|композит|диджей|"
                          r"musician|singer|rapper|band|songwriter|producer|dj|group|artist|composer",
                          re.I)


def _wiki(name):
    for lang, titles in (("ru", [name, f"{name} (музыкант)", f"{name} (рэпер)", f"{name} (группа)"]),
                         ("en", [name, f"{name} (musician)", f"{name} (rapper)", f"{name} (band)"])):
        for t in titles:
            j = _get(f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{quote(t.replace(' ', '_'))}")
            if not j or j.get("type") == "disambiguation":
                continue
            desc = (j.get("description") or "") + " " + (j.get("extract") or "")[:300]
            if _MUSIC_WORDS.search(desc):
                return {"bio": j.get("extract", ""), "bio_src": f"Википедия ({lang})",
                        "wiki_url": ((j.get("content_urls") or {}).get("desktop") or {}).get("page", ""),
                        "wiki_img": ((j.get("originalimage") or j.get("thumbnail") or {}).get("source", ""))}
    return None


# ── Last.fm ─────────────────────────────────────────────────────────────── #

def _lastfm(name, key):
    if not key:
        return None
    j = _get("https://ws.audioscrobbler.com/2.0/", {"method": "artist.getinfo", "artist": name,
                                                    "autocorrect": 1, "api_key": key, "format": "json",
                                                    "lang": "ru"})
    a = (j or {}).get("artist")
    if not a:
        return None
    bio = re.sub(r"<a [^>]*>.*?</a>", "", (a.get("bio") or {}).get("summary", ""), flags=re.S)
    bio = html.unescape(re.sub(r"<[^>]+>", "", bio)).strip()
    st = a.get("stats") or {}
    tt = _get("https://ws.audioscrobbler.com/2.0/", {"method": "artist.gettoptracks", "artist": name,
                                                     "autocorrect": 1, "limit": 15, "api_key": key,
                                                     "format": "json"})
    top = [{"title": t.get("name", ""), "playcount": int(t.get("playcount") or 0),
            "listeners": int(t.get("listeners") or 0)}
           for t in ((tt or {}).get("toptracks") or {}).get("track", [])]
    return {"listeners": int(st.get("listeners") or 0), "playcount": int(st.get("playcount") or 0),
            "lastfm_top": top,
            "tags": [t.get("name", "") for t in ((a.get("tags") or {}).get("tag") or [])][:6],
            "lastfm_bio": bio}


# ── всё вместе ──────────────────────────────────────────────────────────── #

def _attach_images(data: dict, full: bool) -> bool:
    """Пути к картинкам карточки; full=False — только фото артиста (для плиток профиля), остальные
    обложки — когда откроют страницу артиста. → были ли докачки."""
    pic_url = data.get("picture_url") or data.get("wiki_img", "")
    groups = [(data.get("top", []), "cover_url", "cover"), (data.get("albums", []), "cover_url", "cover"),
              (data.get("related", []), "picture_url", "picture")]
    if not full:
        data["picture"] = image(pic_url)
        for items, uk, pk in groups:
            for it in items:
                it[pk] = cached_image(it.get(uk, ""))
        data["partial"] = True
        return True
    jobs = [pic_url] + [it.get(uk, "") for items, uk, _pk in groups for it in items]
    need = [u for u in jobs if u and not cached_image(u)]
    if need:
        with ThreadPoolExecutor(max_workers=4) as ex:
            list(ex.map(image, need))
    data["picture"] = cached_image(pic_url)
    for items, uk, pk in groups:
        for it in items:
            it[pk] = cached_image(it.get(uk, ""))
    changed = bool(need) or bool(data.pop("partial", False))
    return changed


def fetch_artist(name: str, lastfm_key: str = "", force: bool = False, images: bool = True, titles=()) -> dict:
    """Собрать карточку артиста (в фоне). Возвращает dict; ключ 'sources' —
    какие серверы ответили, 'error' — если ни один. images=False — из картинок только фото."""
    c = cached(name)
    if c and c.get('schema') == ARTIST_SCHEMA and not force and time.time() - c.get("ts", 0) < TTL:
        if images and c.get("partial"):
            if _attach_images(c, True):
                _save(name, c)
        return ensure_audience(name, c)
    if not force and time.time() - _FAILED.get(name, 0) < 600:
        if c:
            c["stale"] = True
            return c
        return {"name": name, "sources": [], "error": "Серверы не ответили — проверьте интернет"}
    data = {"name": name, "ts": int(time.time()), "sources": [], 'schema': ARTIST_SCHEMA}
    with ThreadPoolExecutor(max_workers=5) as ex:
        fd = ex.submit(_deezer, name, titles)
        fw = ex.submit(_wiki, name)
        fl = ex.submit(_lastfm, name, lastfm_key)
        fy = ex.submit(_ytm, name)
        fm = ex.submit(_mb, name)
        dz, wk, lf, yt, mb = fd.result(), fw.result(), fl.result(), fy.result(), fm.result()
    data.update(mb or {"mb_checked": True})
    if (mb or {}).get("mb_id"):
        data["sources"].append("MusicBrainz")
    if yt:
        data.update(yt)
        if yt.get("ytm_monthly") or yt.get("ytm_subs"):
            data["sources"].append("YouTube Music")
    if dz:
        data.update(dz)
        data["sources"].append("Deezer")
    if wk:
        data.update(wk)
        data["sources"].append("Wikipedia")
    if lf:
        data.update(lf)
        pc = {norm(t["title"]): t for t in lf.get("lastfm_top", [])}
        if data.get("top"):
            for t in data["top"]:
                m = pc.get(norm(t["title"]))
                if m:
                    t["playcount"], t["listeners"] = m["playcount"], m["listeners"]
        elif lf.get("lastfm_top"):
            data["top"] = [{"title": t["title"], "playcount": t["playcount"], "listeners": t["listeners"],
                            "rank": t["playcount"], "duration": 0, "album": "", "cover_url": "",
                            "artists": name} for t in lf["lastfm_top"][:10]]
        data["sources"].append("Last.fm")
        if not data.get("bio") and lf.get("lastfm_bio"):
            data["bio"], data["bio_src"] = lf["lastfm_bio"], "Last.fm"
    # Малоизвестные исполнители: SoundCloud. Спрашиваем, если Deezer никого не нашёл, нашёл кого-то с малым числом
    # поклонников или его популярные треки не совпадают ни с одним вашим треком этого исполнителя (однофамилец).
    def _hits(tops):
        want = {norm(t) for t in titles if norm(t)}
        return sum(1 for t in tops or [] if norm(t.get("title", "")) and
                   any(norm(t["title"]) in w or w in norm(t["title"]) for w in want))
    dz_hits = _hits((dz or {}).get("top"))
    obscure = not dz or int(dz.get("fans") or 0) < 20000 or (titles and dz_hits == 0)
    if obscure:
        try:
            from artist_soundcloud import fetch
            sc = fetch(name, titles)
        except Exception as e:                             # noqa: BLE001
            print('[artist SoundCloud]', type(e).__name__, e)
            sc = None
        if sc:
            wrong_dz = bool(dz) and bool(titles) and dz_hits == 0 and bool(sc.get("sc_matched"))
            sc_main = not dz or wrong_dz or int(dz.get("fans") or 0) * 2 < int(sc.get("fans") or 0)
            if wrong_dz:                                   # Deezer нашёл однофамильца — его данные убираем
                for k in ("picture_url", "fans", "albums", "albums_count", "top", "related", "deezer_id"):
                    data.pop(k, None)
                if "Deezer" in data["sources"]:
                    data["sources"].remove("Deezer")
            if sc_main:                                    # артист SoundCloud: имя, аватар, топ и прослушивания оттуда
                keep = {k: data[k] for k in ("albums", "albums_count", "related") if data.get(k)}
                data.update(sc)
                data.update(keep)
                if not sc.get("picture_url") and dz and not wrong_dz:
                    data["picture_url"] = dz.get("picture_url", "")
            else:                                          # каталожный артист — плюс прослушивания SoundCloud
                data.update({k: sc[k] for k in ("playcount", "sc_profile", "sc_counted", "sc_complete")})
            data['sources'].append('SoundCloud')
    # картинки — параллельно в кэш
    _attach_images(data, images)
    if not data["sources"]:
        _FAILED[name] = time.time()
        data["error"] = "Серверы не ответили — проверьте интернет"
        if c:
            c["stale"] = True
            return c                               # лучше старые данные, чем ничего
    else:
        _fill_bio(data)
        _save(name, data)
    return data


def human(n: int) -> str:
    """1234567 → «1,2 млн», 45300 → «45,3 тыс.»"""
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".").replace(".", ",") + " млн"
    if n >= 10_000:
        return f"{n / 1000:.0f} тыс."
    if n >= 1000:
        return f"{n / 1000:.1f}".rstrip("0").rstrip(".").replace(".", ",") + " тыс."
    return str(n)
