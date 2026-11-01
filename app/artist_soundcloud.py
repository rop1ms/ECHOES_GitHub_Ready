"""Исполнитель с SoundCloud: имя, аватарка и прослушивания — сумма прослушиваний ВСЕХ его треков.

Для андеграунда, которого нет в каталогах (или Deezer подсовывает однофамильца с парой поклонников).
Какой профиль «тот»: имя совпадает с учётом написания (artist_identity.same_artist), а если таких
несколько — тот, чьи треки совпадают с вашими треками этого исполнителя; при равенстве — больше подписчиков.
Сумма честно помечается неполной, если за отведённое время не удалось пройти все страницы треков.
"""
import time
from urllib.parse import urlparse, parse_qs

from artist_identity import key, same_artist


def _title_key(t):
    from track_identity import norm
    return norm(t or "")


def pick_user(name, titles=()):
    """Лучший профиль SoundCloud для имени: → (user, совпавших треков) или (None, 0)."""
    from online_search import sc_search_users, sc_search
    users = [u for u in sc_search_users(name, 12) if same_artist(u.get("name"), name)
             or same_artist(u.get("full_name"), name)]
    if not users:
        # профиль может не искаться по имени, но треки — находятся: берём автора треков
        seen = {}
        for t in sc_search(name, 20) or []:
            if same_artist(t.get("artist"), name) and t.get("artist_id"):
                seen.setdefault(t["artist_id"], {"id": t["artist_id"], "name": t["artist"], "followers": 0,
                                                 "cover_url": "", "url": ""})
        users = list(seen.values())
    if not users:
        return None, 0
    want = {_title_key(t) for t in titles if _title_key(t)}
    if len(users) == 1 and not want:
        return users[0], 0
    from online_search import _sc_get, _sc_track
    best, best_score, best_hits = None, -1.0, 0
    for u in users[:6]:
        hits = 0
        if want:
            page = _sc_get(f"/users/{u['id']}/tracks", {"limit": 80}, retry=False) or {}
            got = {_title_key(_sc_track(t)["title"]) for t in page.get("collection", [])}
            hits = sum(1 for w in want if any(w and (w in g or g in w) for g in got if g))
        exact = 1 if key(u.get("name")) == key(name) else 0
        score = hits * 10 + exact * 3 + min(2.0, (u.get("followers") or 0) / 50000)
        if score > best_score:
            best, best_score, best_hits = u, score, hits
    if want and best_hits == 0 and len(users) > 1:
        return None, 0                                     # однофамильцы и ни одного вашего трека — не гадаем
    return best, best_hits


def fetch(name, titles=()):
    from online_search import _sc_get, _sc_track, _sc_user
    u, hits = pick_user(name, titles)
    if not u:
        return None
    uid = u["id"]
    full = _sc_get(f"/users/{uid}", retry=False)
    if isinstance(full, dict) and full.get("id"):
        u = _sc_user(full)
    tracks, complete, deadline = {}, False, time.monotonic() + 25
    missing_counts=False
    path, params = f"/users/{uid}/tracks", {"limit": 200, "linked_partitioning": 1}
    seen = set()
    while time.monotonic() < deadline and len(tracks) < 20000:
        marker = (path, str(params))
        if marker in seen:
            break
        seen.add(marker)
        page = _sc_get(path, params, retry=False)
        if not isinstance(page, dict):
            break
        for raw in page.get("collection", []):
            owner=raw.get('user_id') or (raw.get('user') or {}).get('id')
            if owner is not None and str(owner)!=str(uid):continue
            if raw.get("id") is not None:
                missing_counts |= raw.get('playback_count') is None
                tracks[raw["id"]] = _sc_track(raw)
        nxt = page.get("next_href")
        if not nxt:
            complete = True
            break
        url = urlparse(nxt)
        if url.scheme != "https" or url.netloc != "api-v2.soundcloud.com":
            break
        path, params = url.path, {k: v[0] for k, v in parse_qs(url.query).items() if k != "client_id"}
    if not tracks:
        return None
    complete = complete and not missing_counts
    if isinstance(full,dict) and int(full.get('track_count') or 0)>len(tracks):complete=False
    top = sorted(tracks.values(), key=lambda t: -t["plays"])[:10]
    for t in top:
        t.update(rank=t["plays"], playcount=t["plays"], artists=t["artist"])
    pic = (u.get("cover_url") or "").replace("-large.", "-t500x500.")
    if "default_avatar" in pic:
        pic = ""
    return dict(name=u.get("name") or name, picture_url=pic, fans=int(u.get("followers") or 0),
                playcount=sum(t["plays"] for t in tracks.values()), top=top,
                sc_profile=u.get("url", ""), sc_counted=len(tracks), sc_complete=complete, sc_matched=hits,
                bio=u.get("description", ""), bio_src="SoundCloud")
