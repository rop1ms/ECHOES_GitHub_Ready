# auto_tags.py
"""
Недостающие теги трека из интернета: исполнитель, альбом, жанр, обложка.

Когда для трека нашёлся текст, а в тегах файла одно название (без исполнителя или без обложки),
main.py зовёт fill_missing() в фоне. Исполнитель и название берутся из найденного текста (источник
текста уже сверил их с длительностью), дальше Deezer, iTunes и SoundCloud ищут этот трек; лучший вариант с
совпадением ≥ 75 % записывается в файл НАВСЕГДА (теги + встроенная обложка). Заполняется только пустое —
то, что вы вписали сами, не трогается.
"""

import concurrent.futures as cf
from pathlib import Path

UA = {"User-Agent": "ECHOES-player/1.0"}
MIN_SCORE = 0.75
UNKNOWN_ARTISTS = {"", "unknown artist", "неизвестный исполнитель", "unknown", "various artists"}


def _score(want, artist, title, dur, want_dur):
    from track_identity import match_score
    try:
        return float(match_score(want, artist or "", title or "", float(dur or 0), float(want_dur or 0)))
    except Exception:                                      # noqa: BLE001
        return 0.0


def _deezer(want, q, dur):
    import requests
    r = requests.get("https://api.deezer.com/search", params={"q": q, "limit": 8}, headers=UA, timeout=6)
    out = []
    for d in (r.json() or {}).get("data") or []:
        alb = d.get("album") or {}
        sc = _score(want, (d.get("artist") or {}).get("name"), d.get("title"), d.get("duration"), dur)
        out.append((sc, {"artist": (d.get("artist") or {}).get("name") or "", "title": d.get("title") or "",
                         "album": alb.get("title") or "", "genre": "", "year": "",
                         "cover": alb.get("cover_xl") or alb.get("cover_big") or "", "src": "Deezer"}))
    return out


def _itunes(want, q, dur):
    import requests
    r = requests.get("https://itunes.apple.com/search", params={"term": q, "entity": "song", "limit": 8},
                     headers=UA, timeout=6)
    out = []
    for d in (r.json() or {}).get("results") or []:
        sc = _score(want, d.get("artistName"), d.get("trackName"), (d.get("trackTimeMillis") or 0) / 1000, dur)
        art = d.get("artworkUrl100") or ""
        out.append((sc, {"artist": d.get("artistName") or "", "title": d.get("trackName") or "",
                         "album": d.get("collectionName") or "", "genre": d.get("primaryGenreName") or "",
                         "year": (d.get("releaseDate") or "")[:4],
                         "cover": art.replace("100x100bb", "1000x1000bb") if art else "", "src": "iTunes"}))
    return out


def _soundcloud(want, q, dur):
    """SoundCloud — для андеграунда, которого нет на Deezer / iTunes (альбома там нет, есть автор и обложка)."""
    import online_search
    out = []
    for d in online_search.sc_search(q, 8) or []:
        cover = (d.get("cover_url") or "").replace("-t300x300.", "-t500x500.").replace("-large.", "-t500x500.")
        if "default_avatar" in cover:
            cover = ""
        sc = _score(want, d.get("artist"), d.get("title"), d.get("duration"), dur)
        out.append((sc - 0.03, {"artist": d.get("artist") or "", "title": d.get("title") or "", "album": "",
                                "genre": "", "year": "", "cover": cover, "src": "SoundCloud"}))
    return out


def lookup(artist: str, title: str, duration: float = 0.0) -> dict | None:
    """Лучшее совпадение на Deezer / iTunes: {artist, title, album, genre, year, cover, src, score} или None.
    Жанр и год Deezer в поиске не отдаёт — их добираем у iTunes, если там тот же трек."""
    artist, title = (artist or "").strip(), (title or "").strip()
    if not title:
        return None
    want = [(artist, title)]
    q = f"{artist} {title}".strip()
    found = []
    with cf.ThreadPoolExecutor(max_workers=3) as ex:
        for f in [ex.submit(_deezer, want, q, duration), ex.submit(_itunes, want, q, duration),
                  ex.submit(_soundcloud, want, q, duration)]:
            try:
                found += f.result(timeout=8)
            except Exception as e:                         # noqa: BLE001
                print("[auto_tags]", e)
    found = [x for x in found if x[1]["cover"] or x[1]["album"]]
    if not found:
        return None
    found.sort(key=lambda x: -x[0])
    sc, best = found[0]
    if sc < MIN_SCORE:
        return None
    best = dict(best, score=round(sc, 3))
    for sc2, other in found[1:]:                           # жанр / год — с другого источника, если трек тот же
        if sc2 >= MIN_SCORE:
            for k in ("genre", "year"):
                if not best.get(k) and other.get(k):
                    best[k] = other[k]
    return best


def needs_tags(path: str, track: dict | None = None, full: bool = False) -> dict:
    """Чего не хватает файлу: {'artist': bool, 'cover': bool, 'album': bool, 'genre': bool}; {} — всё на месте / не файл.
    full=False (текст нашёлся) — трогаем файл, только если нет исполнителя или обложки;
    full=True (теги всей медиатеки) — если не хватает хоть чего-то из четырёх."""
    if not path or str(path).startswith("stream:") or not Path(path).exists():
        return {}
    from lyrics_tags import read_metadata, read_cover
    meta = read_metadata(path)
    miss = {"artist": (meta.get("artist") or "").strip().lower() in UNKNOWN_ARTISTS,
            "album": not (meta.get("album") or "").strip(),
            "genre": not (meta.get("genre") or "").strip(),
            "cover": False}
    try:
        miss["cover"] = not read_cover(path)
    except Exception:                                      # noqa: BLE001
        miss["cover"] = True
    if not (miss["artist"] or miss["cover"] or (full and (miss["album"] or miss["genre"]))):
        return {}                                          # исполнитель и обложка есть — не трогаем файл
    return miss


def plan_missing(path: str, artist: str, title: str, duration: float = 0.0, miss: dict | None = None,
                 trust_artist: bool = False) -> dict | None:
    """Найти недостающее, НИЧЕГО не записывая в файл (сеть + скачать обложку).
    → {'new': {title/artist/album/genre}, 'cover': bytes|None, 'mime', 'src', 'score'} или None.
    trust_artist — исполнитель надёжный (его вернул источник текста): если каталоги трек не нашли, взять его.
    Запись — apply_plan(): файл, который сейчас читает плеер, переписывать нельзя (звук съезжает с текстом)."""
    miss = miss if miss is not None else needs_tags(path)
    if not miss:
        return None
    best = lookup(artist, title, duration)
    if not best:
        # каталоги трек не знают, но исполнитель известен (его дал источник текста, сверив длительность) —
        # записать хотя бы его, чтобы не искать заново при каждом запуске
        if trust_artist and miss.get("artist") and (artist or "").strip() and (artist or "").strip().lower() not in UNKNOWN_ARTISTS:
            return {"new": {"artist": artist.strip()}, "cover": None, "mime": "", "src": "текст песни", "score": 0.0}
        return None
    from lyrics_tags import read_metadata
    meta = read_metadata(path)
    new = {}
    if miss.get("artist") and best["artist"]:
        new["artist"] = best["artist"]
    if not (meta.get("album") or "").strip() and best["album"]:
        new["album"] = best["album"]
    if not (meta.get("genre") or "").strip() and best.get("genre"):
        new["genre"] = best["genre"]
    if not (meta.get("title") or "").strip() and best["title"]:
        new["title"] = best["title"]
    cover, mime = None, ""
    if miss.get("cover") and best["cover"]:
        try:
            import requests
            r = requests.get(best["cover"], headers=UA, timeout=10)
            if r.ok and len(r.content) > 2000:
                cover = r.content
                mime = "image/png" if r.content[:4] == b"\x89PNG" else "image/jpeg"
        except Exception as e:                             # noqa: BLE001
            print("[auto_tags] cover:", e)
    if not new and not cover:
        return None
    return {"new": new, "cover": cover, "mime": mime, "src": best["src"], "score": best["score"]}


def apply_plan(path: str, plan: dict) -> dict | None:
    """Записать найденное в файл. → {'artist','album','genre','title','cover_changed','src','score'} или None."""
    if not plan:
        return None
    from lyrics_tags import write_metadata, write_cover
    new = plan.get("new") or {}
    ok = True
    if new:
        ok = write_metadata(path, title=new.get("title", ""), artist=new.get("artist", ""),
                            album=new.get("album", ""), genre=new.get("genre", ""))
    cover_changed = False
    if plan.get("cover"):
        try:
            cover_changed = bool(write_cover(path, plan["cover"], plan.get("mime") or "image/jpeg"))
        except Exception as e:                             # noqa: BLE001
            print("[auto_tags] cover:", e)
    if not (ok and new) and not cover_changed:
        return None
    return dict(new, cover_changed=cover_changed, src=plan.get("src", ""), score=plan.get("score", 0.0))


def fill_missing(path: str, artist: str, title: str, duration: float = 0.0, miss: dict | None = None,
                 trust_artist: bool = False) -> dict | None:
    """Найти и сразу записать недостающее (plan_missing + apply_plan)."""
    return apply_plan(path, plan_missing(path, artist, title, duration, miss, trust_artist))
