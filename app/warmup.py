"""Прогрев после запуска: чтобы первое открытие любого раздела было мгновенным, а не с подлагиванием.

1) В фоновом потоке (с уступками интерфейсу, bgproc.throttle) — разбор названий и подписи файлов всей
   коллекции (music_intel.IntelStore.ident / _file_sig): это и было самым дорогим при первом открытии.
2) В главном потоке, по одному разделу и только пока пользователь ничего не делает, — собрать страницы
   заранее (Моя волна, Альбомы, Коллекция, Профиль, Echoes.NET): при переходе останется только показать.
"""
from __future__ import annotations

import threading
import time

from PyQt6.QtCore import QObject, QTimer, QEvent
from PyQt6.QtWidgets import QApplication

START_AFTER_MS = 2500                                          # после показа окна (и заставки)
STEP_MS = 450                                                  # между страницами
IDLE_S = 1.2                                                   # столько секунд без ввода — можно строить
KEEP_MS = 4000                                                 # потом — проверять устаревшие разделы так часто


class _Idle(QObject):
    """Время последнего ввода (мышь/клавиатура) — на окне, не на всём приложении."""

    def __init__(self, win):
        super().__init__(win)
        self.t = time.monotonic()
        win.installEventFilter(self)

    def eventFilter(self, obj, ev):
        if ev.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.KeyPress, QEvent.Type.Wheel):
            self.t = time.monotonic()
        return False


def _warm_intel(library):
    try:
        import bgproc
        import music_intel
    except Exception:                                          # noqa: BLE001
        return
    for t in list(library):
        try:
            music_intel.IntelStore.ident(t)
            music_intel._file_sig(str(t.get("path", "")))
        except Exception:                                      # noqa: BLE001
            pass
        bgproc.throttle(0.35)


def start(win):
    """Запустить прогрев (вызывать один раз после показа окна)."""
    if getattr(win, "_warmup_started", False):
        return
    win._warmup_started = True
    idle = _Idle(win)
    threading.Thread(target=_warm_intel, args=(list(getattr(win, "library", []) or []),),
                     name="echoes-warmup", daemon=True).start()
    order = ["foryou", "wave", "collection", "albums", "profile", "net", "tierlist", "duel"]

    def step():
        sh = getattr(win, "_ya", None)
        if sh is None or not order:
            return
        if time.monotonic() - idle.t < IDLE_S or QApplication.mouseButtons() != 0:
            QTimer.singleShot(STEP_MS, step)                   # пользователь что-то делает — подождём
            return
        key = order.pop(0)
        try:
            if key == sh._cur_page:
                pass
            elif key in ("foryou", "collection", "wave", "search", "albums"):
                if key in sh._dirty or (key == "foryou" and not sh.pages["foryou"].v.count()):
                    sh._rebuild(key)
            elif key == "profile":
                sh.pages["profile"].refresh()
            elif key == "net":
                page = sh.pages["net"]
                page._refresh_tab(page._tab or "feed")
            elif key == "tierlist":
                sh.pages["tierlist"].refresh()
            for w in (sh.pages.get(key),):                     # раскладка и стили — тоже заранее
                if w is not None:
                    w.ensurePolished()
                    lay = w.layout()
                    if lay is not None:
                        lay.activate()
        except Exception as e:                                 # noqa: BLE001
            print("[warmup]", key, e)
        if order:
            QTimer.singleShot(STEP_MS, step)
        else:
            QTimer.singleShot(KEEP_MS, keep)
    QTimer.singleShot(START_AFTER_MS, step)

    def keep():
        """Дальше — в простое: разделы, которые устарели (новые треки, прослушивания), собрать заранее."""
        sh = getattr(win, "_ya", None)
        if sh is None:
            return
        try:
            if time.monotonic() - idle.t >= IDLE_S * 2 and QApplication.mouseButtons() == 0 and win.isVisible():
                for key in ("foryou", "wave", "collection", "albums"):
                    if key in sh._dirty and key != sh._cur_page:
                        sh._rebuild(key)
                        break                                  # по одному за раз — без рывков
        except Exception as e:                                 # noqa: BLE001
            print("[warmup]", e)
        QTimer.singleShot(KEEP_MS, keep)
