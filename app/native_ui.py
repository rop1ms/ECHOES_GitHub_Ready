# native_ui.py
"""
Виджеты интерфейса на C++ — echoes_ui.dll (исходники и сборка: tools/native_ui).

DLL собрана против того же Qt, что у PyQt6, и живёт в том же процессе: C++ создаёт QWidget,
здесь он оборачивается sip.wrapinstance и дальше это обычный виджет PyQt6. Если DLL нет,
версия Qt не совпала или ECHOES_NO_NATIVE_UI=1 — available() == False, и вызывающий код
остаётся на прежних виджетах Python.
"""
from __future__ import annotations

import ctypes
import os

from PyQt6 import sip
from PyQt6.QtCore import qVersion
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QWidget

_DIR = os.path.dirname(os.path.abspath(__file__))
_lib = None
_err = ""
_tried = False


def _ver(s):
    return tuple(int(x) for x in (s.split(".") + ["0", "0"])[:3] if x.isdigit())


def _load():
    global _lib, _err, _tried
    _tried = True
    if os.environ.get("ECHOES_NO_NATIVE_UI") == "1":
        _err = "disabled by ECHOES_NO_NATIVE_UI"
        return None
    path = os.path.join(_DIR, "echoes_ui.dll")
    if not os.path.exists(path):
        _err = "echoes_ui.dll not found"
        return None
    try:
        import PyQt6
        qt_bin = os.path.join(os.path.dirname(PyQt6.__file__), "Qt6", "bin")
        if os.path.isdir(qt_bin):
            os.add_dll_directory(qt_bin)                   # Qt6*.dll — те же, что уже загрузил PyQt6
        # PyDLL: вызовы короткие и только из потока интерфейса — GIL не отпускаем. С CDLL каждый вызов
        # отдавал GIL фоновым потокам (обложки, анализ) и ждал его обратно до 5 мс — дольше самой работы.
        lib = ctypes.PyDLL(path)
        lib.eui_qt_version.restype = ctypes.c_char_p
        built = lib.eui_qt_version().decode()
        # Qt 6 держит двоичную совместимость вперёд: DLL под 6.11 работает и на 6.11.x/6.12,
        # но не на более старом Qt и не на Qt 7
        b, r = _ver(built), _ver(qVersion())
        if b[0] != r[0] or r < b:
            _err = f"Qt mismatch: dll {built}, runtime {qVersion()}"
            return None
        _setup(lib)
        _lib = lib
        return lib
    except Exception as e:                                 # noqa: BLE001
        _err = f"echoes_ui.dll: {e}"
        return None


_STRIP_CB = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_int)


def _setup(lib):
    P, I, U, W, D = ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_double
    sig = {
        "eui_version": (I, []),
        "eui_probe_new": (P, [P]),
        "eui_strip_callbacks": (None, [_STRIP_CB, _STRIP_CB, _STRIP_CB, _STRIP_CB]),
        "eui_strip_invalidate": (None, [P, I]),
        "eui_strip_new": (P, [I]),
        "eui_strip_style": (None, [P, P, P, W, I]),
        "eui_strip_add": (I, [P, W, W, I, D, I, P, P]),
        "eui_strip_text": (None, [P, I, W, W]),
        "eui_strip_cover": (None, [P, I, P]),
        "eui_strip_mix_covers": (None, [P, I, ctypes.POINTER(P), I, I]),
        "eui_strip_set_hover": (None, [P, I, I, P, P, P, P]),
        "eui_strip_count": (I, [P]),
        "eui_strip_card_pos": (None, [P, I, ctypes.POINTER(I), ctypes.POINTER(I)]),
        "eui_strip_card_pixmap": (None, [P, I, P]),
    }
    for name, (res, args) in sig.items():
        fn = getattr(lib, name)
        fn.restype, fn.argtypes = res, args
    lib.eui_strip_callbacks(_on_click, _on_hover, _on_gone, _on_need)


def available() -> bool:
    if not _tried:
        _load()
    return _lib is not None


def error() -> str:
    return _err


def _wrap(ptr) -> QWidget:
    # Виджет создаётся без родителя и принадлежит Python, как обычный QWidget(): addWidget/
    # setParent передают владение Qt (sip это отслеживает), иначе удаляется вместе с обёрткой.
    w = sip.wrapinstance(ptr, QWidget)
    sip.transferback(w)
    return w


def probe() -> QWidget:
    return _wrap(_lib.eui_probe_new(None))


# ── лента карточек (card_strip.cpp) ────────────────────────────────────── #

_STRIPS: dict = {}                                         # id → Strip, пока C++-виджет жив
_next_id = [0]


def _guard(fn):
    def call(sid, index):
        try:
            fn(sid, index)
        except Exception:                                  # noqa: BLE001 — исключение в колбэке ctypes иначе теряется
            import sys
            sys.excepthook(*sys.exc_info())
    return _STRIP_CB(call)


def _click(sid, index):
    s = _STRIPS.get(sid)
    if s is not None and s.on_click is not None:
        s.on_click(index)


def _hover(sid, index):
    s = _STRIPS.get(sid)
    if s is not None and s.on_hover is not None:
        s.on_hover(index)


def _need(sid, index):
    s = _STRIPS.get(sid)
    if s is not None and s.on_need is not None:
        s.on_need(index)


def _gone(sid, _index):
    s = _STRIPS.pop(sid, None)
    if s is not None:
        s.alive = False
        try:
            sip.setdeleted(s.widget)                       # Qt удалил виджет — обёртка больше не годится
        except Exception:                                  # noqa: BLE001
            pass
        s.widget = None


_on_click, _on_hover, _on_gone, _on_need = _guard(_click), _guard(_hover), _guard(_gone), _guard(_need)


def _ptr(obj):
    return sip.unwrapinstance(obj) if obj is not None else None


class Strip:
    """Python-сторона одной ленты. widget — сам QWidget (кладётся в layout как обычно).
    on_click(i) / on_hover(i) задаёт владелец; после удаления виджета alive == False и все
    вызовы — пустые (поздняя обложка из фонового потока не обратится к удалённому объекту)."""

    def __init__(self):
        _next_id[0] += 1
        self.id = _next_id[0]
        self.alive = True
        self.on_click = None
        self.on_hover = None
        self.on_need = None                                # карточку рисуют впервые / после invalidate
        self._p = _lib.eui_strip_new(self.id)
        self.widget = _wrap(self._p)
        _STRIPS[self.id] = self

    def style(self, text, muted, families, cascade: bool):
        if self.alive:
            # QColor держим в переменных до конца вызова: _ptr() временного объекта — висячий указатель
            text, muted = QColor(text), QColor(muted)
            _lib.eui_strip_style(self._p, _ptr(text), _ptr(muted), "\n".join(families), int(cascade))

    def add(self, title, subtitle, size, rad, circle, mix=None) -> int:
        if not self.alive:
            return -1
        c1, c2 = (QColor(mix[0]), QColor(mix[1])) if mix else (None, None)
        return _lib.eui_strip_add(self._p, title, subtitle, int(size), float(rad), int(bool(circle)),
                                  _ptr(c1), _ptr(c2))

    def text(self, i, title, subtitle):
        if self.alive:
            _lib.eui_strip_text(self._p, i, title, subtitle)

    def invalidate(self, i):
        if self.alive:
            _lib.eui_strip_invalidate(self._p, i)

    def cover(self, i, pixmap):
        if self.alive:
            _lib.eui_strip_cover(self._p, i, _ptr(pixmap))

    def mix_covers(self, i, pixmaps, on):
        if self.alive:
            arr = (ctypes.c_void_p * max(1, len(pixmaps)))(*[_ptr(p) for p in pixmaps])
            _lib.eui_strip_mix_covers(self._p, i, arr, len(pixmaps), int(bool(on)))

    def hover_style(self, i, glow, glow_color, ring_color, accent, icon):
        if self.alive:
            cols = QColor(glow_color), QColor(ring_color), QColor(accent)
            _lib.eui_strip_set_hover(self._p, i, int(bool(glow)), *[_ptr(c) for c in cols], _ptr(icon))

    def card_pos(self, i):
        x, y = ctypes.c_int(), ctypes.c_int()
        if self.alive:
            _lib.eui_strip_card_pos(self._p, i, ctypes.byref(x), ctypes.byref(y))
        return x.value, y.value

    def card_pixmap(self, i):
        from PyQt6.QtGui import QPixmap
        pm = QPixmap()
        if self.alive:
            _lib.eui_strip_card_pixmap(self._p, i, _ptr(pm))
        return pm
