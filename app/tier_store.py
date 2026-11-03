"""Persistent personal music rankings, independent from playlists and playback.

TierStore(path) exposes defensive-copy ``tiers`` and ``assignments`` properties.
Each tier is ``{id: UUID string, name: str, color: '#RRGGBB'}``; assignments map
``track_key(local_path)`` to a tier ID. Missing media files are retained.

``assign(path, tier_id_or_None)``, ``bulk_assign(mapping)``, ``update_tiers(list)``
and ``restore(snapshot)`` validate everything before an atomic save. They return
whether state changed and raise ValueError/OSError without changing the previous
state on validation or write failure. New tiers may omit ``id``; existing tiers
must retain their IDs when renamed or reordered. Deleting a tier unassigns only
its tracks. ``snapshot()`` / ``restore()`` support UI-managed undo and redo.

``reload()`` returns True on successful load and False for a missing or invalid
file. Invalid loads retain the current state, expose ``load_error``, and preserve
corrupt bytes in ``backup_path``. A failed backup prevents subsequent writes.
"""
from __future__ import annotations

import data_home
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import uuid


FORMAT = "echoes-music-tierlist"
VERSION = 1
MAX_TIERS = 64
MAX_NAME = 100
_NAMESPACE = uuid.UUID("b74b978e-265f-4d92-91b1-df541208109a")
_COLOR = re.compile(r"#[0-9a-fA-F]{6}\Z")
_URI = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


def track_key(path: str | os.PathLike[str]) -> str:
    """Normalize a local path without checking existence or resolving symlinks.

    Windows uses normcase (case-insensitive paths); other systems preserve case.
    This also works for temporarily missing files and removable drives.
    """
    try:
        value = os.fspath(path)
    except TypeError as exc:
        raise ValueError("У трека должен быть локальный путь к файлу.") from exc
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ValueError("У трека должен быть непустой локальный путь к файлу.")
    # A Windows drive prefix is a path, not a URI scheme.
    drive = len(value) >= 3 and value[0].isalpha() and value[1] == ":" and value[2] in "/\\"
    if _URI.match(value) and not drive:
        raise ValueError("Тирлист сохраняет оценки локальных файлов, а не интернет-ссылок.")
    return os.path.normcase(os.path.abspath(os.path.expanduser(value)))


def default_tiers() -> list[dict[str, str]]:
    """Fresh default rows with stable IDs, independent of display names/order."""
    return [
        {"id": str(uuid.uuid5(_NAMESPACE, name)), "name": name, "color": color}
        for name, color in (("S", "#ff7f7f"), ("A", "#ffbf7f"), ("B", "#ffdf7f"),
                            ("C", "#ffff7f"), ("D", "#bfff7f"))
    ]


def _tier_id(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Уровню нужен корректный постоянный идентификатор.")
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError) as exc:
        raise ValueError("Некорректный идентификатор уровня тирлиста.") from exc


def _tiers(rows: object, *, allow_new: bool) -> list[dict[str, str]]:
    if not isinstance(rows, (list, tuple)) or not 1 <= len(rows) <= MAX_TIERS:
        raise ValueError(f"В тирлисте должно быть от 1 до {MAX_TIERS} уровней.")
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("Некорректная запись уровня тирлиста.")
        identifier = str(uuid.uuid4()) if allow_new and "id" not in row else _tier_id(row.get("id"))
        if identifier in seen:
            raise ValueError("Идентификаторы уровней не должны повторяться.")
        seen.add(identifier)
        name = row.get("name")
        color = row.get("color")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= MAX_NAME:
            raise ValueError(f"Название уровня должно содержать от 1 до {MAX_NAME} символов.")
        if any(ord(char) < 32 for char in name):
            raise ValueError("В названии уровня нельзя использовать управляющие символы.")
        if not isinstance(color, str) or not _COLOR.fullmatch(color):
            raise ValueError("Цвет уровня должен быть указан в формате #RRGGBB.")
        result.append({"id": identifier, "name": name.strip(), "color": color.lower()})
    return result


def _validated(snapshot: object) -> dict:
    if not isinstance(snapshot, Mapping) or snapshot.get("format") != FORMAT or snapshot.get("version") != VERSION:
        raise ValueError("Неподдерживаемый формат файла тирлиста.")
    tiers = _tiers(snapshot.get("tiers"), allow_new=False)
    ids = {tier["id"] for tier in tiers}
    assignments = snapshot.get("assignments")
    if not isinstance(assignments, Mapping):
        raise ValueError("Некорректный список оценок тирлиста.")
    clean = {}
    for path, tier in assignments.items():
        key = track_key(path)
        identifier = _tier_id(tier)
        if identifier not in ids:
            raise ValueError("Оценка ссылается на неизвестный уровень тирлиста.")
        if key in clean and clean[key] != identifier:
            raise ValueError("Один файл получил несколько разных оценок.")
        clean[key] = identifier
    return {"format": FORMAT, "version": VERSION, "tiers": tiers, "assignments": clean}


class TierStore:
    def __init__(self, path: str | os.PathLike[str] | None = None):
        self.path = Path(path).expanduser() if path is not None else data_home.root() / "music_tierlist.json"
        self._lock = threading.RLock()
        self._state = {"format": FORMAT, "version": VERSION, "tiers": default_tiers(), "assignments": {}}
        self.load_error: str | None = None
        self.backup_path: Path | None = None
        self._write_blocked = False
        self.reload()

    @property
    def tiers(self) -> list[dict[str, str]]:
        with self._lock:
            return deepcopy(self._state["tiers"])

    @property
    def assignments(self) -> dict[str, str]:
        with self._lock:
            return dict(self._state["assignments"])

    def snapshot(self) -> dict:
        with self._lock:
            return deepcopy(self._state)

    def tier_for(self, path: str | os.PathLike[str]) -> str | None:
        key = track_key(path)
        with self._lock:
            return self._state["assignments"].get(key)

    def _backup_corrupt(self, raw: bytes) -> None:
        if self.backup_path is not None and self.backup_path.exists() and self.backup_path.read_bytes() == raw:
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        backup = self.path.with_name(f"{self.path.stem}.corrupt-{stamp}-{uuid.uuid4().hex[:8]}{self.path.suffix}")
        # Exclusive creation avoids overwriting a previous recovery copy.
        with backup.open("xb") as file:
            file.write(raw)
            file.flush()
            os.fsync(file.fileno())
        self.backup_path = backup

    def reload(self) -> bool:
        with self._lock:
            try:
                raw = self.path.read_bytes()
            except FileNotFoundError:
                self.load_error = None
                self._write_blocked = False
                return False
            except OSError as exc:
                self.load_error = f"Не удалось прочитать тирлист: {exc}"
                self._write_blocked = True
                return False
            try:
                state = _validated(json.loads(raw.decode("utf-8-sig")))
            except (ValueError, TypeError, UnicodeError) as exc:
                self.load_error = f"Файл тирлиста повреждён или имеет неподдерживаемый формат: {exc}"
                try:
                    self._backup_corrupt(raw)
                except OSError as backup_error:
                    self.load_error += f" Не удалось сохранить резервную копию: {backup_error}"
                    self._write_blocked = True
                else:
                    self._write_blocked = False
                return False
            self._state = state
            self.load_error = None
            self._write_blocked = False
            return True

    def _commit(self, candidate: dict) -> bool:
        state = _validated(candidate)
        if state == self._state and self.load_error is None and self.path.is_file():
            return False
        if self._write_blocked:
            raise OSError("Сначала восстановите доступ к файлу тирлиста: его предыдущие данные нельзя сохранить в резервную копию.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent,
                                             prefix=f".{self.path.name}.", suffix=".tmp", delete=False) as file:
                temporary = Path(file.name)
                json.dump(state, file, ensure_ascii=False, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
        self._state = state
        self.load_error = None
        return True

    def restore(self, snapshot: Mapping) -> bool:
        """Validate and atomically persist an earlier snapshot (for undo/redo)."""
        with self._lock:
            return self._commit(deepcopy(snapshot))

    def assign(self, path: str | os.PathLike[str], tier_id: str | None) -> bool:
        return self.bulk_assign({track_key(path): tier_id})

    def remap_path(self, old: str | os.PathLike[str], new: str | os.PathLike[str]) -> bool:
        """Move a replaced file's rank, retaining an already ranked target's choice.

        After a collision the new path keeps its existing rating and the old path
        is removed. Other missing-file assignments remain untouched.
        """
        old_key, new_key = track_key(old), track_key(new)
        with self._lock:
            state = self.snapshot()
            if old_key == new_key or old_key not in state["assignments"]:
                return False
            rating = state["assignments"].pop(old_key)
            state["assignments"].setdefault(new_key, rating)
            return self._commit(state)

    def bulk_assign(self, assignments: Mapping) -> bool:
        if not isinstance(assignments, Mapping):
            raise ValueError("Нужно передать соответствия треков и уровней.")
        with self._lock:
            state = self.snapshot()
            ids = {tier["id"] for tier in state["tiers"]}
            for path, tier_id in assignments.items():
                key = track_key(path)
                if tier_id is None:
                    state["assignments"].pop(key, None)
                else:
                    identifier = _tier_id(tier_id)
                    if identifier not in ids:
                        raise ValueError("Выбранный уровень больше не существует.")
                    state["assignments"][key] = identifier
            return self._commit(state)

    def update_tiers(self, tiers: list[Mapping]) -> bool:
        with self._lock:
            clean = _tiers(tiers, allow_new=True)
            ids = {tier["id"] for tier in clean}
            state = self.snapshot()
            state["tiers"] = clean
            state["assignments"] = {path: tier for path, tier in state["assignments"].items() if tier in ids}
            return self._commit(state)
