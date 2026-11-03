"""settings.json без балласта: тяжёлые разделы (сессия дуэли, история, дневник, правила треков…) лежат
отдельными файлами в <данные>/state/<ключ>.json и пишутся, только когда сами изменились.

Раньше settings.json весил ~0,6 МБ и целиком перезаписывался (вместе с копией) при каждом переключателе.
Код программы этого не замечает: в памяти win.settings — один словарь, как и был; разделение — только на диске.

Совместимость: если раздел есть прямо в settings.json (старый формат или после отката на прошлую версию) —
берётся он; пустой раздел из старой версии не затирает сохранённый в state/.
"""
from __future__ import annotations

import json
from pathlib import Path

HEAVY = ("duel_session", "play_history", "listening_track_rules", "listening_diary", "online_downloaded",
         "release_feed", "follow_seen")
_LAST: dict = {}                                           # ключ → последнее записанное содержимое (bytes)


def _dir(settings_path) -> Path:
    return Path(settings_path).parent / "state"


def _dumps(v) -> bytes:
    return json.dumps(v, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def load_into(settings: dict, settings_path) -> dict:
    """После загрузки settings.json — дочитать тяжёлые разделы из state/."""
    import atomic_json
    d = _dir(settings_path)
    for k in HEAVY:
        f = d / f"{k}.json"
        inline = settings.get(k)
        if inline not in (None, {}, []):                   # старый формат / откат — свежее то, что внутри
            _LAST.pop(k, None)
            continue
        v = atomic_json.load_json(f, None)
        if v is not None:
            settings[k] = v
            _LAST[k] = _dumps(v)
    return settings


def save(settings_path, settings: dict) -> None:
    """Записать настройки: лёгкая часть — в settings.json, тяжёлые разделы — только изменившиеся."""
    import atomic_json
    d = _dir(settings_path)
    slim = {}
    for k, v in settings.items():
        if k not in HEAVY:
            slim[k] = v
            continue
        b = _dumps(v)
        if _LAST.get(k) != b:
            d.mkdir(parents=True, exist_ok=True)
            atomic_json.save_bytes_async(d / f"{k}.json", b)
            _LAST[k] = b
    atomic_json.save_json_async(settings_path, slim)


def files(settings_path) -> list:
    """Файлы разделов (для поиска пропавших треков и резервных копий)."""
    d = _dir(settings_path)
    return sorted(d.glob("*.json")) if d.is_dir() else []
