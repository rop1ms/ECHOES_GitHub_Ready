"""Обложки чужих треков (посты, профили, сообщения): трека нет в вашей коллекции — берём картинку из
описания трека (thumb_url) или ищем трек в Deezer (без ключей). В фоне, с кэшем на диске, каждый трек — один раз.

want(meta, cb) — cb(path) позовётся в потоке интерфейса, когда картинка скачана (сразу — если уже в кэше).
"""
from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

_CACHE_F = Path.home() / ".neon_player" / "net_cache" / "track_covers.json"
_LOCK = threading.Lock()
_MEM: dict | None = None
_WAIT: dict = {}                                               # ключ → [cb]
_POOL = ThreadPoolExecutor(max_workers=3, thread_name_prefix="echoes-covers")


class _Bus(QObject):
    done = pyqtSignal(str, str)                                # ключ, путь


_BUS = None


def _bus():
    global _BUS
    if _BUS is None:
        _BUS = _Bus()
        _BUS.done.connect(_deliver)
    return _BUS


def _key(meta) -> str:
    norm = lambda s: re.sub(r"\W+", " ", str(s or "").casefold()).strip()
    return f"{norm(meta.get('artist'))}|{norm(meta.get('title'))}"


def _mem() -> dict:
    global _MEM
    if _MEM is None:
        try:
            _MEM = json.loads(_CACHE_F.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _MEM = {}
    return _MEM


def _save():
    try:
        _CACHE_F.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_F.write_text(json.dumps(_mem(), ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def cached(meta) -> str:
    """Уже скачанная обложка трека (или "")."""
    import artist_net
    with _LOCK:
        url = _mem().get(_key(meta), "")
    url = url or str(meta.get("thumb_url") or "")
    return artist_net.cached_image(url) if url else ""


def _find_url(meta) -> str:
    import artist_net
    if meta.get("thumb_url"):
        return str(meta["thumb_url"])
    q = f"{meta.get('artist', '')} {meta.get('title', '')}".strip()
    if not q:
        return ""
    j = artist_net._get("https://api.deezer.com/search/track", {"q": q, "limit": 5}) or {}
    best, bs = "", 0.0
    for t in j.get("data") or []:
        s = artist_net.similarity(meta.get("title", ""), t.get("title", "")) + \
            artist_net.similarity(str(meta.get("artist", "")).split(",")[0], (t.get("artist") or {}).get("name", ""))
        if s > bs:
            best, bs = (t.get("album") or {}).get("cover_medium") or "", s
    return best if bs >= 1.3 else ""


def _work(k, meta):
    import artist_net
    path = ""
    try:
        url = _find_url(meta)
        if url:
            path = artist_net.image(url) or ""
        with _LOCK:
            _mem()[k] = url                                    # и «не нашли» — чтобы не искать снова
            _save()
    except Exception:                                          # noqa: BLE001
        pass
    _bus().done.emit(k, path)


def _deliver(k, path):
    with _LOCK:
        cbs = _WAIT.pop(k, [])
    if not path:
        return
    for cb in cbs:
        try:
            cb(path)
        except RuntimeError:                                   # виджет уже удалён
            pass


def want(meta, cb):
    if not meta or not (meta.get("title") or meta.get("artist")):
        return
    p = cached(meta)
    if p:
        cb(p)
        return
    k = _key(meta)
    with _LOCK:
        if k in _mem() and not _mem()[k] and not meta.get("thumb_url"):
            return                                             # уже искали — не нашли
        first = k not in _WAIT
        _WAIT.setdefault(k, []).append(cb)
    _bus()
    if first:
        _POOL.submit(_work, k, meta)
