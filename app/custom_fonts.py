# custom_fonts.py
"""
Свои шрифты из файлов (.ttf / .otf / .ttc / .woff / .woff2).

Файл копируется в ~/Echoes/fonts/user и регистрируется в Qt при запуске — шрифт виден во всех списках
шрифтов (тема Echoes Music, конструктор окна стримера) как обычный установленный, переустанавливать его
в Windows не нужно. Регистрация одна на процесс: повторные вызовы load_all() ничего не стоят.
"""
from __future__ import annotations

import data_home
import hashlib
import shutil
from pathlib import Path

from PyQt6.QtGui import QFontDatabase

FONT_DIR = data_home.root() / "fonts" / "user"     # та же папка, что у своих шрифтов режима «Слова» (milk_words)
EXTS = (".ttf", ".otf", ".ttc", ".woff", ".woff2")
FILTER = "Шрифты (*.ttf *.otf *.ttc *.woff *.woff2)"
_LOADED: dict[str, list[str]] = {}                       # путь -> семейства


def _register(path: Path) -> list[str]:
    key = str(path)
    if key not in _LOADED:
        fid = QFontDatabase.addApplicationFont(key)
        _LOADED[key] = list(QFontDatabase.applicationFontFamilies(fid)) if fid >= 0 else []
    return _LOADED[key]


def load_all() -> list[str]:
    """Зарегистрировать все сохранённые шрифты; вернуть их семейства."""
    fams: list[str] = []
    try:
        files = sorted(p for p in FONT_DIR.iterdir() if p.suffix.lower() in EXTS)
    except OSError:
        return fams
    for p in files:
        for f in _register(p):
            if f not in fams:
                fams.append(f)
    return fams


def import_font(src: str) -> str:
    """Скопировать файл шрифта к себе и зарегистрировать. Возвращает семейство; ValueError — не шрифт."""
    src_p = Path(src)
    if src_p.suffix.lower() not in EXTS:
        raise ValueError("Нужен файл шрифта: TTF, OTF, TTC, WOFF или WOFF2")
    data = src_p.read_bytes()
    if len(data) > 40 * 2**20:
        raise ValueError("Файл шрифта больше 40 МБ")
    FONT_DIR.mkdir(parents=True, exist_ok=True)
    stem = "".join(c for c in src_p.stem if c.isalnum() or c in " -_")[:60].strip() or "font"
    dst = FONT_DIR / f"{stem}-{hashlib.sha1(data).hexdigest()[:8]}{src_p.suffix.lower()}"
    if not dst.exists():
        shutil.copyfile(src_p, dst)
    fams = _register(dst)
    if not fams:
        _LOADED.pop(str(dst), None)
        dst.unlink(missing_ok=True)
        raise ValueError("Qt не смог прочитать этот файл шрифта")
    return fams[0]
