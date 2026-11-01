"""Без мелькающих окон: ECHOES работает без консоли (pythonw), и каждый запуск консольной программы
(ffmpeg, ffprobe, yt-dlp, python-помощники, tor, pip) Windows по умолчанию показывает отдельным окном,
которое тут же закрывается. install() один раз в начале main.py — после этого любой subprocess.Popen
(а значит и run/call/check_output) без явных флагов запускается невидимым.

Qt: системные эффекты «выезжающих» меню и подсказок (Qt.UIEffect) рисуются отдельными временными окнами —
на Windows они мелькают; их выключает ui_anim, а меню плавно проявляет само.
"""
from __future__ import annotations

import os
import subprocess
import sys

CREATE_NO_WINDOW = 0x08000000
_VISIBLE = 0x00000010 | 0x00000008                             # CREATE_NEW_CONSOLE | DETACHED_PROCESS — задано явно
_done = False


def install():
    global _done
    if _done or os.name != "nt":
        return
    _done = True
    orig = subprocess.Popen.__init__

    def init(self, *args, **kw):
        flags = int(kw.get("creationflags") or 0)
        if not flags & _VISIBLE:                               # нужное окно (новая консоль) просили явно — не трогать
            kw["creationflags"] = flags | CREATE_NO_WINDOW     # окна-программы (explorer) это не прячет
        return orig(self, *args, **kw)

    subprocess.Popen.__init__ = init


def hide_qprocess(proc):
    """QProcess (монтаж): тоже без окна консоли."""
    if os.name != "nt":
        return
    try:
        def mod(args):
            args.flags |= CREATE_NO_WINDOW
        proc.setCreateProcessArgumentsModifier(mod)
    except Exception as e:                                     # noqa: BLE001
        print("[noflash]", e, file=sys.stderr)


# ── Qt: виджет без родителя, которому сказали «покажись» (setVisible(True) до вставки в раскладку), Windows
# на мгновение показывает отдельным маленьким окном. Ловим такие показы до того, как окно попадёт на экран:
# QShowEvent приходит раньше, чем системное окно становится видимым, и WA_DontShowOnScreen отменяет показ.
# Когда виджет потом вставят в страницу (ParentChange), флаг снимается — он рисуется как обычно.

_GUARD = None


def _accidental(w) -> bool:
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import (QAbstractButton, QLabel, QLineEdit, QProgressBar, QAbstractSlider, QComboBox,
                                 QAbstractSpinBox, QFrame, QAbstractScrollArea, QDialog, QMenu)
    if w.parent() is not None or w.windowType() not in (Qt.WindowType.Widget, Qt.WindowType.Window):
        return False                                           # Tool / Popup / Dialog… — окно задумано
    if isinstance(w, (QDialog, QMenu, QAbstractScrollArea)) or w.property("echoes_window"):
        return False
    return isinstance(w, (QAbstractButton, QLabel, QLineEdit, QProgressBar, QAbstractSlider, QComboBox,
                          QAbstractSpinBox, QFrame))


def install_qt_guard(app):
    """Без фильтра на всё приложение (он получал бы каждое событие программы): перехватываем только
    show()/setVisible(True) — и следим за вставкой в страницу лишь у тех виджетов, что поймали."""
    global _GUARD
    if _GUARD is not None:
        return
    from PyQt6.QtCore import QObject, QEvent, Qt
    from PyQt6.QtWidgets import QWidget

    class Guard(QObject):
        n = 0

        def eventFilter(self, obj, ev):
            if ev.type() == QEvent.Type.ParentChange:
                try:
                    if obj.property("echoes_hidden_window"):
                        obj.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, False)
                        obj.setProperty("echoes_hidden_window", None)
                    obj.removeEventFilter(self)
                except RuntimeError:
                    pass
            return False
    _GUARD = Guard(app)
    orig_sv, orig_show = QWidget.setVisible, QWidget.show

    def guard(w):
        try:
            if w.parent() is None and _accidental(w):
                w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
                w.setProperty("echoes_hidden_window", True)
                w.installEventFilter(_GUARD)
                Guard.n += 1
        except RuntimeError:
            pass

    def set_visible(self, visible):
        if visible:
            guard(self)
        return orig_sv(self, visible)

    def show(self):
        guard(self)
        return orig_show(self)
    QWidget.setVisible = set_visible
    QWidget.show = show
