# daw_samples.py
"""
Свои сэмплы студии: папки с паками (808, бочки, малые, хэты, петли…) — добавляются в браузере,
файлы раскладываются по видам по имени файла и папки («808», «kick», «бочка», «hat», «snare»…).
Плюс папка «Разобранные биты» — туда кладёт звуки разбор бита (daw_split).

Корневая нота сэмпла (для 808 и мелодичных звуков) определяется по высоте — тогда в пианоролле
нота «До» звучит как «До», а не как попало.
"""
from __future__ import annotations

import os
import re
import threading

import numpy as np

import daw_dsp as D
import daw_project as PR

EXTS = {".wav", ".mp3", ".flac", ".ogg", ".aif", ".aiff", ".m4a", ".opus"}
SPLITS = PR.STUDIO / "beat_kits"                         # звуки из разобранных битов
MAX_FILES = 6000

# вид → слова в имени файла/папки (порядок важен: 808 раньше баса и бочки, открытый хэт раньше хэта)
KINDS = [
    ("808", ["808", "sub bass", "subbass"]),
    ("Бочки", ["kick", "kik", "bd", "бочк", "кик"]),
    ("Малые", ["snare", "snr", "sd", "rim", "малый", "снейр", "снер"]),
    ("Хлопки", ["clap", "clp", "хлоп", "snap"]),
    ("Открытые хэты", ["open hat", "openhat", "open hh", "ohh", "oh ", "open"]),
    ("Хэты", ["hat", "hh", "hihat", "hi-hat", "хэт", "хет", "shaker", "шейкер"]),
    ("Тарелки", ["crash", "ride", "cymbal", "крэш", "райд", "тарел"]),
    ("Перкуссия", ["perc", "tom", "conga", "bongo", "cowbell", "tamb", "clave", "wood", "перк", "том"]),
    ("Эффекты", ["fx", "riser", "impact", "sweep", "uplift", "downlift", "whoosh", "noise", "transition", "райзер", "эффект"]),
    ("Вокал", ["vox", "vocal", "chant", "adlib", "voice", "вокал", "голос"]),
    ("Петли", ["loop", "луп", "петл", "bpm"]),
    ("Бас", ["bass", "бас", "reese"]),
    ("Мелодии", ["melody", "keys", "piano", "synth", "lead", "pad", "pluck", "bell", "guitar", "мелод", "пиано", "гитар"]),
]
ORDER = [k for k, _w in KINDS] + ["Разное"]

_lock = threading.Lock()
_scan: dict = {}                                          # папка → {вид: [пути]}


def kind_of(path: str) -> str:
    low = path.lower().replace("\\", "/")
    name = os.path.basename(low)
    parts = low.split("/")[-3:]                            # имя + две папки сверху
    for hay in (name, " ".join(parts)):
        h = re.sub(r"[_\-.]+", " ", hay)
        for k, words in KINDS:
            for w in words:
                if len(w) <= 3 and w.strip().isalpha():
                    if re.search(rf"(^|[^a-zа-я]){re.escape(w.strip())}([^a-zа-я]|$)", h):
                        return k
                elif w in h:
                    return k
    return "Разное"


def scan(folder: str, force=False) -> dict:
    """{вид: [пути]} для папки (с подпапками), с кэшем."""
    folder = os.path.abspath(folder)
    with _lock:
        if not force and folder in _scan:
            return _scan[folder]
    out: dict = {}
    n = 0
    for root, _dirs, files in os.walk(folder):
        for f in sorted(files):
            if os.path.splitext(f)[1].lower() in EXTS:
                p = os.path.join(root, f)
                out.setdefault(kind_of(p), []).append(p)
                n += 1
                if n >= MAX_FILES:
                    break
        if n >= MAX_FILES:
            break
    with _lock:
        _scan[folder] = out
    return out


def folders(settings: dict) -> list:
    """Папки пользователя + папка разобранных битов (если там что-то есть)."""
    fs = [f for f in settings.get("sample_dirs", []) if isinstance(f, str) and os.path.isdir(f)]
    if SPLITS.exists() and any(SPLITS.iterdir()):
        fs.append(str(SPLITS))
    return fs


def add_folder(settings: dict, folder: str) -> bool:
    lst = settings.setdefault("sample_dirs", [])
    folder = os.path.abspath(folder)
    if folder in lst:
        return False
    lst.append(folder)
    return True


def remove_folder(settings: dict, folder: str):
    lst = settings.setdefault("sample_dirs", [])
    folder = os.path.abspath(folder)
    if folder in lst:
        lst.remove(folder)
    with _lock:
        _scan.pop(folder, None)


def root_note(path: str) -> int | None:
    """Нота сэмпла (MIDI) по высоте тона первых 1.5 с — для 808, баса, мелодий; None — нет высоты."""
    try:
        x = D.decode(path, D.SR, 2, dur=2.0).mean(axis=1)
    except Exception:                                     # noqa: BLE001
        return None
    if len(x) < 4096:
        return None
    a = int(np.argmax(np.abs(x) > np.abs(x).max() * 0.3))
    seg = x[a + int(0.03 * D.SR):a + int(0.6 * D.SR)]
    fs = []
    for s in range(0, max(1, len(seg) - 4096), 2048):
        f0, conf = D.yin(seg[s:s + 4096], D.SR, 28.0, 1000.0, 0.2)
        if f0 > 0 and conf > 0.6:
            fs.append(f0)
    if len(fs) < 2:
        return None
    m = D.hz_to_midi(float(np.median(fs)))
    return int(round(m))
