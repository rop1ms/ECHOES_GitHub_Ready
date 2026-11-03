"""Прогрев после запуска: чтобы первое открытие любого раздела было мгновенным, а не с подлагиванием.

1) В фоновом потоке (с уступками интерфейсу, bgproc.throttle) — разбор названий и подписи файлов всей
   коллекции (music_intel.IntelStore.ident / _file_sig): это и было самым дорогим при первом открытии.
2) В главном потоке, по одному разделу и только пока пользователь ничего не делает, — собрать страницы
   заранее (Моя волна, Альбомы, Коллекция, Профиль, Echoes.NET): при переходе останется только показать.
"""
from __future__ import annotations

import threading
import time

from PyQt6.QtCore import QObject, QTimer, QEvent, Qt
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


PRELOAD_PAGES = ("foryou", "wave", "collection", "albums", "profile", "net", "follows", "tierlist", "duel", "queue")


def preload(win, budget_s: float = 14.0):
    """Пока идёт заставка (своё окно и поток — ей это не мешает): собрать все основные разделы и один раз
    показать каждый за заставкой — первая отрисовка, стили, шрифты, обложки, GL-контексты (туманность,
    огонь дуэли) случаются сейчас, а не при первом клике. Потом — обратно на стартовую страницу."""
    app = QApplication.instance()
    t0 = time.monotonic()

    def pump(sec):
        end = time.monotonic() + sec
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.004)
    pump(0.25)
    anchor = getattr(win, "_gpu_anchor", None)
    end = time.monotonic() + 1.5
    while anchor is not None and time.monotonic() < end:      # окно на GPU-совмещении готово
        try:
            if anchor.ok():
                break
        except RuntimeError:
            break
        pump(0.05)
    sh = getattr(win, "_ya", None)
    if sh is None:
        return
    start_page = getattr(sh, "_cur_page", None)
    for key in PRELOAD_PAGES:
        if time.monotonic() - t0 > budget_s:
            break
        page = sh.pages.get(key)
        if page is None:
            continue
        try:
            if key in ("foryou", "collection", "wave", "albums"):
                if key in sh._dirty or (key == "foryou" and not sh.pages["foryou"].v.count()):
                    sh._rebuild(key)
            elif key == "net" and hasattr(page, "_refresh_tab"):
                page._refresh_tab(page._tab or "feed")
            elif hasattr(page, "refresh"):
                page.refresh()
            sh.stack.setCurrentWidget(page)                    # первая отрисовка — за заставкой
            pump(0.15)
        except Exception as e:                                 # noqa: BLE001
            print("[preload]", key, e)
    try:
        if start_page in sh.pages:
            sh.stack.setCurrentWidget(sh.pages[start_page])
        pump(0.05)
        import gpu_scroll                                      # первый снимок + слой прокрутки
        gpu_scroll.warm(win)
        pump(0.1)
    except Exception as e:                                     # noqa: BLE001
        print("[preload] scroll:", e)
    win._warm_render_started = True                            # лёгкий прогрев уже сделан


def start(win):
    """Запустить прогрев (вызывать один раз после показа окна)."""
    from performance import config
    if not getattr(win, "_warm_render_started", False):         # лёгкий прогрев — всегда (см. gpu_scroll.warm)
        win._warm_render_started = True
        idle0 = _Idle(win)

        # и профиль с Echoes.NET — их первое открытие раньше стоило 0.5–1.5 с (создание и стили виджетов)
        steps = ["render", "profile", "net"]

        def light():
            if not steps:
                return
            if time.monotonic() - idle0.t < IDLE_S or QApplication.mouseButtons() != Qt.MouseButton.NoButton:
                QTimer.singleShot(STEP_MS, light)
                return
            step_ = steps.pop(0)
            try:
                sh = getattr(win, "_ya", None)
                if step_ == "render":
                    import gpu_scroll
                    gpu_scroll.warm(win)
                elif sh is not None and getattr(sh, "_cur_page", None) != step_:
                    page = sh.pages.get(step_)
                    if step_ == "profile":
                        page.refresh()
                    elif step_ == "net" and hasattr(page, "_refresh_tab"):
                        page._refresh_tab(page._tab or "feed")
                    if page is not None:
                        page.ensurePolished()
            except Exception as e:                             # noqa: BLE001
                print("[warmup]", step_, e)
            QTimer.singleShot(STEP_MS, light)
        QTimer.singleShot(START_AFTER_MS, light)
    if not config(win.settings)['warm_pages'] or getattr(win, "_warmup_started", False):
        return
    win._warmup_started = True
    idle = _Idle(win)
    threading.Thread(target=_warm_intel, args=(list(getattr(win, "library", []) or []),),
                     name="echoes-warmup", daemon=True).start()
    order = ["foryou", "wave", "collection", "albums", "profile", "net", "tierlist", "duel", "gpu_scroll"]

    def step():
        if not config(win.settings)['warm_pages']:
            win._warmup_started=False;return
        sh = getattr(win, "_ya", None)
        if sh is None or not order:
            return
        if time.monotonic() - idle.t < IDLE_S or QApplication.mouseButtons() != Qt.MouseButton.NoButton:
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
            elif key == "gpu_scroll":                          # первый снимок для прокрутки на GPU
                import gpu_scroll
                gpu_scroll.warm(win)
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
        if not config(win.settings)['warm_pages']:
            win._warmup_started=False;return
        sh = getattr(win, "_ya", None)
        if sh is None:
            return
        try:
            if time.monotonic() - idle.t >= IDLE_S * 2 and QApplication.mouseButtons() == Qt.MouseButton.NoButton and win.isVisible():
                for key in ("foryou", "wave", "collection", "albums"):
                    if key in sh._dirty and key != sh._cur_page:
                        sh._rebuild(key)
                        break                                  # по одному за раз — без рывков
        except Exception as e:                                 # noqa: BLE001
            print("[warmup]", e)
        QTimer.singleShot(KEEP_MS, keep)
