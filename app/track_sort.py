"""Сортировка треков в коллекции и плейлистах: как добавлены, наоборот, по названию, исполнителю, альбому,
длительности и по тому, что вы чаще слушаете. Выбор запоминается (коллекция — одна настройка, у каждого
плейлиста — своя), а порядок на экране — это и порядок, в котором дальше играют треки (MainWindow._view_order).
"""
from __future__ import annotations

import re

SORTS = [("default", "Как добавлены"), ("reverse", "В обратном порядке"), ("title", "По названию"),
         ("artist", "По исполнителю"), ("album", "По альбому"), ("duration", "По длительности"),
         ("plays", "Часто слушаю")]
NAMES = dict(SORTS)
COLL_NAMES = dict(NAMES, default="Сначала новые", reverse="Сначала старые")


def _txt(s) -> str:
    s = str(s or "").casefold().replace("ё", "е")
    s = re.sub(r"^(the|a)\s+", "", s)
    return re.sub(r"\d+", lambda m: m.group(0).zfill(6), s)          # «Трек 2» раньше «Трек 10»


def apply(rows: list, key: str, win=None) -> list:
    """rows — строки списка ({"t": трек, ...}) в порядке «как добавлены». Возвращает новый список."""
    if key in (None, "", "default"):
        return list(rows)
    if key == "reverse":
        return list(reversed(rows))
    t = lambda r: r.get("t") or {}
    if key == "title":
        return sorted(rows, key=lambda r: (_txt(t(r).get("title")), _txt(t(r).get("artist"))))
    if key == "artist":
        return sorted(rows, key=lambda r: (_txt(t(r).get("artist")) or "￿", _txt(t(r).get("album")),
                                           _txt(t(r).get("title"))))
    if key == "album":
        return sorted(rows, key=lambda r: (_txt(t(r).get("album")) or "￿", _txt(t(r).get("artist")),
                                           _txt(t(r).get("title"))))
    if key == "duration":
        return sorted(rows, key=lambda r: float(t(r).get("duration") or 0))
    if key == "plays":
        data = ((getattr(getattr(win, "stats", None), "data", None) or {}).get("tracks") or {}) if win else {}
        return sorted(rows, key=lambda r: (-int((data.get(str(t(r).get("path", ""))) or {}).get("plays", 0)),
                                           _txt(t(r).get("title"))))
    return list(rows)


def menu(parent, current: str, on_pick, collection=False):
    """Меню выбора сортировки у кнопки parent."""
    from PyQt6.QtWidgets import QMenu
    m = QMenu(parent)
    names = COLL_NAMES if collection else NAMES
    for k, _ in SORTS:
        a = m.addAction(names[k])
        a.setCheckable(True)
        a.setChecked(k == (current or "default"))
        a.triggered.connect(lambda _=False, k=k: on_pick(k))
    m.exec(parent.mapToGlobal(parent.rect().bottomLeft()))
