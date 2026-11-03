# data_home.py
"""
Папка данных ECHOES: ~/Echoes (раньше ~/.neon_player — имя от старого названия плеера).

root() — где данные сейчас (Echoes, а пока перенос не удался — прежняя папка). migrate() — один раз при запуске
плеера: переименовать прежнюю папку в Echoes и переписать в данных сохранённые пути (коллекция, плейлисты,
настройки, кэши, файлы Echoes.NET), иначе треки из папки загрузок и обложки «потерялись» бы. Если папка занята
(открыт старый ECHOES) — данные остаются где были, перенос повторится при следующем запуске.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

NAME = "Echoes"
OLD_NAME = ".neon_player"
_CACHE: dict = {}


def root() -> Path:
    home = Path.home()
    r = _CACHE.get(home)
    if r is None:
        new, old = home / NAME, home / OLD_NAME
        r = new if (new / "settings.json").exists() or not old.exists() else old
        _CACHE[home] = r
    return r


def _variants(old: Path, new: Path):
    """Как путь записан в файлах: JSON (двойные «\\»), обычный, с прямыми слэшами."""
    o, n = str(old), str(new)
    out = [(json.dumps(o)[1:-1], json.dumps(n)[1:-1]), (o, n), (o.replace("\\", "/"), n.replace("\\", "/"))]
    return [(a, b) for a, b in out if a]


def _rewrite_json(base: Path, pairs) -> int:
    changed = 0
    for f in base.rglob("*.json"):
        try:
            if f.stat().st_size > 64 * 2 ** 20:
                continue
            raw = f.read_text("utf-8-sig")
        except (OSError, UnicodeError):
            continue
        txt = raw
        for a, b in pairs:
            txt = txt.replace(a, b)
        if txt != raw:
            tmp = f.with_name(f.name + ".migrate.tmp")
            try:
                tmp.write_text(txt, "utf-8")
                os.replace(tmp, f)
                changed += 1
            except OSError:
                tmp.unlink(missing_ok=True)
    return changed


def _rewrite_db(db: Path, old: Path, new: Path):
    if not db.exists():
        return
    import sqlite3
    con = sqlite3.connect(str(db))
    try:
        for t, in con.execute("select name from sqlite_master where type='table'").fetchall():
            cols = [r[1] for r in con.execute(f'pragma table_info("{t}")') if str(r[2]).upper() in ("TEXT", "")]
            for c in cols:
                for a, b in ((str(old), str(new)), (str(old).replace("\\", "/"), str(new).replace("\\", "/"))):
                    con.execute(f'update "{t}" set "{c}" = replace("{c}", ?, ?) where "{c}" like ?', (a, b, f"%{a}%"))
        con.commit()
    finally:
        con.close()


def migrate() -> str:
    """Перенести ~/.neon_player → ~/Echoes. Возвращает, что сделано (для журнала)."""
    home = Path.home()
    old, new = home / OLD_NAME, home / NAME
    if not old.is_dir() or (new / "settings.json").exists():
        return ""
    if new.exists():
        try:
            new.rmdir()                                   # пустая папка с таким именем — не помеха
        except OSError:
            return "в ~/Echoes уже есть другие файлы — данные остаются в .neon_player"
    try:
        os.rename(old, new)
    except OSError as e:
        return f"папка данных занята ({e.__class__.__name__}) — перенос в следующий запуск"
    _CACHE.clear()
    pairs = _variants(old, new)
    n = _rewrite_json(new, pairs)
    try:
        _rewrite_db(new / "net" / "net.db", old, new)
    except Exception as e:                                # noqa: BLE001
        print("[data] net.db paths:", e)
    try:
        with open(new / "library_changes.log", "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + f"данные перенесены из {old} в {new}, файлов с путями: {n}\n")
    except OSError:
        pass
    return f"данные перенесены в {new} (переписаны пути в {n} файлах)"
