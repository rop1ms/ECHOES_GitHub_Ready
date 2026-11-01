# window_corners.py
"""Скруглённые углы главного окна (Windows 11, средствами системы — гладко, без обрезки содержимого).

Безрамочное окно Qt в Windows 11 по умолчанию получает острые углы. DWM умеет скруглять их сам
(DWMWA_WINDOW_CORNER_PREFERENCE). Настройка привязана к системному окну (HWND), а Qt иногда
пересоздаёт его (например, когда в окне впервые появляется OpenGL-холст) — поэтому скругление
повторяется при каждом показе и смене HWND. Развёрнутое на весь экран окно система рисует с прямыми углами.
"""
from __future__ import annotations

import sys

from PyQt6.QtCore import QObject, QEvent, QTimer

DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2


def round_now(window) -> bool:
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes
        from ctypes import wintypes
        pref = ctypes.c_int(DWMWCP_ROUND)
        hr = ctypes.windll.dwmapi.DwmSetWindowAttribute(wintypes.HWND(int(window.winId())),
                                                        DWMWA_WINDOW_CORNER_PREFERENCE,
                                                        ctypes.byref(pref), ctypes.sizeof(pref))
        return hr == 0                                     # на Windows 10 атрибута нет — просто ничего не будет
    except Exception as e:                                 # noqa: BLE001
        print("[corners]", e)
        return False


class _Keeper(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window

    def eventFilter(self, obj, ev):
        if obj is self.window and ev.type() in (QEvent.Type.WinIdChange, QEvent.Type.Show):
            QTimer.singleShot(0, lambda: round_now(self.window))
        return False


def install(window) -> None:
    """Скруглить сейчас и держать скругление при пересоздании окна."""
    if getattr(window, "_corner_keeper", None) is not None:
        return
    k = _Keeper(window)
    window.installEventFilter(k)
    window._corner_keeper = k
    if window.isVisible():
        round_now(window)
