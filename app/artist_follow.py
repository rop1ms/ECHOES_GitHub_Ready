"""Подписки на исполнителей: подписаться / отписаться, лента новых релизов, уведомления о них.

Хранится в настройках плеера:
  followed_artists — [имя, …] (порядок — когда подписались, новые в конце);
  follow_seen      — {имя: [id релизов, которые уже видели]} — чтобы не уведомлять о старом;
  release_feed     — [{artist, title, type, date, cover_url, url, ts, new}] — последние релизы подписок.

ReleaseWatcher раз в 3 часа (и через минуту после запуска) в фоне смотрит у каждого исполнителя
последние релизы: Deezer (альбомы, синглы, EP с датой), если там нет — YouTube Music. Новые →
в ленту и уведомлением в углу экрана (вид «Новые релизы подписок»).
"""
from __future__ import annotations

import threading
import time

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

CHECK_EVERY_MS = 3 * 3600 * 1000
FIRST_CHECK_MS = 60 * 1000
FEED_MAX = 120


class _Bus(QObject):
    changed = pyqtSignal()                                     # подписки изменились (кнопки, страница)
    releases = pyqtSignal(object)                              # [новые релизы] — из фонового потока


BUS = None


def bus() -> _Bus:
    global BUS
    if BUS is None:
        BUS = _Bus()
    return BUS


def _k(name) -> str:
    """Один исполнитель — один ключ: «key vs. locket» = «keyvslocket», регистр, знаки и «- Topic» не важны."""
    try:
        from artist_identity import ident
        return ident(name) or str(name or "").strip().lower()
    except Exception:                                          # noqa: BLE001
        return str(name or "").strip().lower()


def followed(win) -> list:
    s = win.settings
    f = s.get("followed_artists")
    if not isinstance(f, list):
        f = s["followed_artists"] = []
    seen, out = set(), []
    for a in f:                                                # разные написания одного исполнителя — одна подписка
        k = _k(a)
        if k and k not in seen:
            seen.add(k)
            out.append(a)
    if len(out) != len(f):
        f[:] = out
    return f


def is_followed(win, name) -> bool:
    k = _k(name)
    return any(k == _k(a) for a in followed(win))


def set_follow(win, name, on: bool):
    name = (name or "").strip()
    if not name:
        return
    f = followed(win)
    k = _k(name)
    f[:] = [a for a in f if _k(a) != k]
    if on:
        f.append(name)
        threading.Thread(target=_seed_seen, args=(win, name), daemon=True).start()   # старое — не «новое»
    try:
        win.save_settings()
    except Exception:                                          # noqa: BLE001
        pass
    bus().changed.emit()
    try:                                                       # подписки видны в профиле Echoes.NET
        win._ya.pages["net"].push_profile()
    except Exception:                                          # noqa: BLE001
        pass


def feed(win) -> list:
    f = win.settings.get("release_feed")
    return f if isinstance(f, list) else []


def unread(win) -> int:
    return sum(1 for r in feed(win) if r.get("new"))


def mark_read(win):
    for r in feed(win):
        r["new"] = False
    try:
        win.save_settings()
    except Exception:                                          # noqa: BLE001
        pass
    bus().changed.emit()


# ── релизы исполнителя ── #

def releases_of(name) -> list:
    """Последние релизы (новые первыми): [{id, title, type, date, cover_url, url}]."""
    out = []
    try:
        import artist_net
        j = artist_net._get("https://api.deezer.com/search/artist", {"q": name, "limit": 5}) or {}
        best = None
        for a in j.get("data") or []:
            if artist_net.similarity(name, a.get("name", "")) >= 0.86:
                best = a
                break
        if best:
            al = artist_net._get(f"https://api.deezer.com/artist/{best['id']}/albums", {"limit": 25}) or {}
            for a in al.get("data") or []:
                out.append({"id": f"dz:{a.get('id')}", "title": a.get("title", ""),
                            "type": {"album": "Альбом", "single": "Сингл", "ep": "EP"}.get(a.get("record_type", ""),
                                                                                         "Релиз"),
                            "date": a.get("release_date") or "", "cover_url": a.get("cover_big") or
                            a.get("cover_medium") or "", "url": a.get("link") or ""})
    except Exception as e:                                     # noqa: BLE001
        print("[follow] deezer", name, e)
    if not out:                                                # нет на Deezer — YouTube Music
        try:
            import artist_net
            y = artist_net._ytm(name) or {}
            if y.get("ytm_id"):
                yt = artist_net._YTM
                d = yt.get_artist(y["ytm_id"])
                for kind, label in (("albums", "Альбом"), ("singles", "Сингл")):
                    for a in ((d.get(kind) or {}).get("results") or [])[:12]:
                        th = (a.get("thumbnails") or [{}])[-1].get("url", "")
                        out.append({"id": f"yt:{a.get('browseId')}", "title": a.get("title", ""), "type": label,
                                    "date": str(a.get("year") or ""), "cover_url": th, "url": ""})
        except Exception as e:                                 # noqa: BLE001
            print("[follow] ytm", name, e)
    out.sort(key=lambda r: r.get("date") or "", reverse=True)
    return out


def _seed_seen(win, name):
    """Только подписались: всё, что вышло раньше, — уже «видели» (уведомления — только о новом)."""
    rel = releases_of(name)
    seen = win.settings.setdefault("follow_seen", {})
    seen[name] = [r["id"] for r in rel][:200]
    fd = feed(win)                                             # последние 2 релиза — в ленту (без «новое»)
    for r in rel[:2]:
        if not any(x.get("id") == r["id"] for x in fd):
            fd.append(dict(r, artist=name, ts=time.time(), new=False))
    fd.sort(key=lambda x: x.get("date") or "", reverse=True)
    win.settings["release_feed"] = fd[:FEED_MAX]
    try:
        bus().releases.emit([])
    except RuntimeError:
        pass


def check_now(win, done=None):
    """Проверить релизы всех подписок (в фоне)."""
    names = list(followed(win))

    def work():
        seen_all = win.settings.setdefault("follow_seen", {})
        fresh = []
        for name in names:
            rel = releases_of(name)
            if not rel:
                continue
            seen = set(seen_all.get(name) or [])
            if name not in seen_all:                           # подписались давно, но ещё не смотрели
                seen_all[name] = [r["id"] for r in rel][:200]
                continue
            for r in rel:
                if r["id"] not in seen:
                    fresh.append(dict(r, artist=name, ts=time.time(), new=True))
            seen_all[name] = list(dict.fromkeys([r["id"] for r in rel] + list(seen)))[:300]
            time.sleep(0.4)                                    # не частить к серверам
        if fresh:
            fd = feed(win)
            known = {x.get("id") for x in fd}
            fd.extend(r for r in fresh if r["id"] not in known)
            fd.sort(key=lambda x: (x.get("new", False), x.get("date") or ""), reverse=True)
            win.settings["release_feed"] = fd[:FEED_MAX]
        try:
            bus().releases.emit(fresh)
        except RuntimeError:
            pass
    threading.Thread(target=work, name="echoes-releases", daemon=True).start()


class ReleaseWatcher(QObject):
    """Раз в 3 часа — новые релизы подписок → лента и уведомления."""

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.t = QTimer(self)
        self.t.setInterval(CHECK_EVERY_MS)
        self.t.timeout.connect(self.check)
        self.t.start()
        QTimer.singleShot(FIRST_CHECK_MS, self.check)
        bus().releases.connect(self._on_new)

    def check(self):
        if followed(self.win):
            check_now(self.win)

    def _on_new(self, fresh):
        try:
            self.win.save_settings()
        except Exception:                                      # noqa: BLE001
            pass
        bus().changed.emit()
        if not fresh:
            return
        try:
            page = self.win._ya.pages["net"]
        except Exception:                                      # noqa: BLE001
            page = None
        for r in fresh[:5]:
            if page is not None:
                page.alert("release", r["artist"], f"{r.get('type', 'Релиз')}: {r.get('title', '')}",
                           tab="artist:" + r["artist"])
        if len(fresh) > 5 and page is not None:
            page.alert("release", "Новые релизы", f"и ещё {len(fresh) - 5}", tab="follows")
