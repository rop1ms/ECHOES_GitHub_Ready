# tray.py
"""
ECHOES в трее.

Крестик окна (если включено «Сворачивать в трей при закрытии», по умолчанию да) прячет плеер
в область уведомлений — музыка играет дальше, Echoes.NET остаётся в сети. Значок в трее:
  * клик — показать / спрятать окно, средняя кнопка — пауза / играть;
  * меню: что играет, пауза, следующий / предыдущий, «Моя волна», случайный трек из коллекции,
    Echoes.NET в сети / не в сети, «Открыть Echoes.NET», громкость тише / громче,
    настройка сворачивания и «Выход» (настоящий выход из плеера).
Спрятанное окно отдаёт память: кэши обложек и картинок очищаются, рабочий набор урезается,
если музыка не играет (урезание во время звука могло бы дать щелчок).
Этот же значок использует Echoes.NET для уведомлений (NetPage.notify).
"""
from __future__ import annotations

import random

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

MENU_QSS = """
QMenu { background: #1d1d20; color: #f2f2f2; border: 1px solid #333; border-radius: 10px; padding: 6px; }
QMenu::item { padding: 7px 22px 7px 14px; border-radius: 7px; }
QMenu::item:selected { background: #2e2e33; }
QMenu::item:disabled { color: #9a9aa2; }
QMenu::separator { height: 1px; background: #333; margin: 5px 8px; }
"""


class EchoesTray(QObject):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.icon = QSystemTrayIcon(win.windowIcon(), self)
        self.icon.setToolTip("ECHOES")
        self.menu = QMenu()
        self.menu.setStyleSheet(MENU_QSS)
        self.menu.aboutToShow.connect(self._fill)
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self._activated)
        self.icon.messageClicked.connect(self.show_window)
        self._hinted = False
        self._tip = QTimer(self)                         # подсказка «что играет» на значке
        self._tip.setInterval(4000)
        self._tip.timeout.connect(self._update_tip)
        self._tip.start()
        self.icon.show()
        self._update_tip()

    # ── окно ── #

    def show_window(self):
        import ui_anim                                         # в том же виде, что было (развёрнутым — так развёрнутым)
        ui_anim.bring_front(self.win)

    def hide_window(self):
        self.win.hide()
        if not self._hinted and not self.win.settings.get("tray_hinted"):
            self._hinted = True
            self.win.settings["tray_hinted"] = True
            self._save()
            self.icon.showMessage("ECHOES в трее", "Музыка играет дальше. Нажмите на значок, чтобы открыть плеер, "
                                  "правой кнопкой — меню. Выключить: меню значка → «Сворачивать в трей».",
                                  QSystemTrayIcon.MessageIcon.Information, 5000)
        QTimer.singleShot(1500, self._release_memory)

    def _release_memory(self):
        if self.win.isVisible():
            return
        try:
            from PyQt6.QtGui import QPixmapCache
            QPixmapCache.clear()
            import yamusic_theme
            yamusic_theme.clear_cover_cache()
        except Exception:                                # noqa: BLE001
            pass
        try:
            import mem_trim
            playing = bool(self.win.engine.is_playing())
            mem_trim.trim(working_set=not playing)
        except Exception:                                # noqa: BLE001
            pass

    def _activated(self, reason):
        R = QSystemTrayIcon.ActivationReason
        if reason == R.Trigger or reason == R.DoubleClick:
            if self.win.isVisible() and not self.win.isMinimized() and reason == R.Trigger:
                self.hide_window()
            else:
                self.show_window()
        elif reason == R.MiddleClick:
            self._do(self.win.toggle_play)

    def quit_app(self):
        self.win._really_quit = True
        self.win.close()

    # ── меню ── #

    def _now(self):
        try:
            t = self.win._current_track()
        except Exception:                                # noqa: BLE001
            t = None
        if not t:
            return ""
        title = str(t.get("title") or "").strip()
        artist = str(t.get("artist") or "").strip()
        return f"{artist} — {title}" if artist and title else (title or artist)

    def _playing(self) -> bool:
        try:
            return bool(self.win.engine.is_playing())
        except Exception:                                # noqa: BLE001
            return False

    def _update_tip(self):
        now = self._now()
        # This tooltip is painted by Windows, which cannot use app-local fonts.
        from i18n import tr
        self.icon.setToolTip((tr("Играет") if self._playing() else tr("Пауза")) + ": " + now[:110] if now else "ECHOES")

    def _net(self):
        ya = getattr(self.win, "_ya", None)
        return getattr(ya, "pages", {}).get("net") if ya is not None else None

    def _act(self, text, fn, enabled=True, checkable=False, checked=False):
        a = QAction(text, self.menu)
        a.setEnabled(enabled)
        if checkable:
            a.setCheckable(True)
            a.setChecked(checked)
        a.triggered.connect(lambda *_: self._do(fn))
        self.menu.addAction(a)
        return a

    def _do(self, fn):
        try:
            fn()
        except Exception as e:                           # noqa: BLE001
            print("[tray]", e)
        QTimer.singleShot(300, self._update_tip)

    def _fill(self):
        m = self.menu
        m.clear()
        now = self._now()
        if now:
            from ui_symbols import icon
            head = QAction(now if len(now) < 48 else now[:46] + "…", m)
            head.setIcon(icon("play_pause" if self._playing() else "pause", 16))
            head.setEnabled(False)
            m.addAction(head)
            m.addSeparator()
        vis = self.win.isVisible() and not self.win.isMinimized()
        self._act("Спрятать ECHOES" if vis else "Открыть ECHOES", self.hide_window if vis else self.show_window)
        m.addSeparator()
        self._act("Пауза" if self._playing() else "Играть", self.win.toggle_play)
        self._act("Следующий трек", self.win.next_track)
        self._act("Предыдущий трек", self.win.prev_track)
        self._act("Моя волна", self._wave, enabled=bool(getattr(self.win, "_ya", None)))
        self._act("Мини-плеер", lambda: __import__("ya_quick").toggle_mini(self.win))
        self._act("Случайный трек из коллекции", self._random, enabled=bool(getattr(self.win, "library", None)))
        vol = m.addMenu("Громкость")
        vol.setStyleSheet(MENU_QSS)
        for txt, d in (("Громче  +10", 10), ("Тише  −10", -10), ("Без звука / вернуть", 0)):
            a = vol.addAction(txt)
            a.triggered.connect(lambda *_, d=d: self._do(lambda: self._volume(d)))
        m.addSeparator()
        net = self._net()
        if net is not None:
            on = net.running()
            self._act("Echoes.NET: в сети" if on else "Echoes.NET: не в сети", net.toggle_power,
                      checkable=True, checked=on)
            self._act("Открыть Echoes.NET", self._open_net)
            m.addSeparator()
        self._act("Сворачивать в трей при закрытии", self._toggle_close, checkable=True,
                  checked=bool(self.win.settings.get("close_to_tray", True)))
        self._act("Выход", self.quit_app)

    # ── действия ── #

    def _wave(self):
        ya = self.win._ya
        from yamusic_theme import WAVE_PL
        if self.win.current_index >= 0 and getattr(self.win, "queue_context", None) == WAVE_PL:
            if not self._playing():
                self.win.toggle_play()                   # волна уже выбрана — просто продолжить
            return
        ya._wave_play_clicked()

    def _random(self):
        w = self.win
        lib = list(w.library)
        if not lib:
            return
        i = random.randrange(len(lib))
        w._play_index(i, "library")

    def _volume(self, d):
        w = self.win
        sl = getattr(w, "vol", None)
        if sl is None:
            return
        if d == 0:
            cur = sl.value()
            if cur > 0:
                self._muted_at = cur
                sl.setValue(0)
            else:
                sl.setValue(getattr(self, "_muted_at", 60) or 60)
        else:
            sl.setValue(max(sl.minimum(), min(sl.maximum(), sl.value() + d)))

    def _open_net(self):
        self.show_window()
        try:
            self.win._ya.show_page("net")
        except Exception:                                # noqa: BLE001
            pass

    def _toggle_close(self):
        s = self.win.settings
        s["close_to_tray"] = not bool(s.get("close_to_tray", True))
        self._save()

    def _save(self):
        try:
            self.win.save_settings()
        except Exception as e:                           # noqa: BLE001
            print("[tray] save:", e)


def install(win):
    """Значок в трее (если трей есть в системе). Возвращает EchoesTray или None."""
    if not QSystemTrayIcon.isSystemTrayAvailable():
        return None
    tray = EchoesTray(win)
    app = QApplication.instance()
    app.setQuitOnLastWindowClosed(False)                 # спрятанное окно + закрытый диалог ≠ выход
    try:                                                 # выход из Windows / перезагрузка — закрыться по-настоящему
        app.commitDataRequest.connect(lambda *_: setattr(win, "_really_quit", True))
    except Exception:                                    # noqa: BLE001
        pass
    return tray
