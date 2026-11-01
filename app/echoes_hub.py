# echoes_hub.py
"""
«Музыка» — интерфейс Echoes Music (как в Яндекс Музыке) в отдельном окне,
доступный из ЛЮБОЙ темы: онлайн-поиск и скачивание с YouTube Music /
SoundCloud, профили и карточки исполнителей, альбомы, «Моя волна»,
«Для вас», коллекция, импорт из Spotify.

Внутри — тот же YaShell, что и в теме Echoes Music, со своим стилем
(не зависит от текущей темы плеера). Управляет тем же плеером.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QApplication


class EchoesHub(QWidget):
    def __init__(self, win):
        super().__init__(None, Qt.WindowType.Window)
        self.win = win
        from yamusic_theme import YaShell, ya_qss, ya_theme_dict, BG
        self.setObjectName("YaHub")
        self.setWindowTitle("ECHOES — Музыка")
        try:
            self.setWindowIcon(win.windowIcon())
        except Exception:                            # noqa: BLE001
            pass
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(ya_qss(ya_theme_dict()) + f"\nQWidget#YaHub {{ background:{BG}; }}\n")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 8, 0, 0)
        self.shell = YaShell(win, self)
        lay.addWidget(self.shell)
        scr = QApplication.primaryScreen()
        if scr is not None:
            g = scr.availableGeometry()
            w, h = min(1320, int(g.width() * 0.86)), min(860, int(g.height() * 0.88))
            self.resize(w, h)
            self.move(g.x() + (g.width() - w) // 2, g.y() + (g.height() - h) // 2)
        else:
            self.resize(1280, 820)

    def open_page(self, key):
        try:
            self.shell.show_page(key)
        except Exception:                            # noqa: BLE001
            pass

    def closeEvent(self, e):
        try:
            self.shell.shutdown()
        except Exception:                            # noqa: BLE001
            pass
        if getattr(self.win, "_hub", None) is self:
            self.win._hub = None
        self.deleteLater()
        super().closeEvent(e)
