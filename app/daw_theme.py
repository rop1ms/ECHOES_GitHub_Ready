# daw_theme.py
"""
Тема «Echoes Studio» — студия для битов, сведения, записи голоса и мэшапов прямо в плеере.

Вид как у настольных студий: сверху меню, транспорт (паттерн/песня, играть, стоп, запись),
темп, табло времени, метроном, громкость мастера, осциллограф и загрузка процессора, кнопки
окон; строка подсказки. Слева — браузер. Остальное — рабочая область с плавающими окнами:
плейлист, стойка каналов, пианоролл, микшер, мэшап-бот и окна плагинов.

Клавиши: пробел — играть/стоп, L — паттерн/песня, R — запись, Ctrl+Z / Ctrl+Y — отмена,
Ctrl+S — сохранить, Ctrl+N — новый, Ctrl+O — открыть, Ctrl+R — экспорт, F5 — плейлист,
F6 — стойка каналов, F7 — пианоролл, F8 — выбор плагина, Alt+F8 — браузер, F9 — микшер, F10 — мэшап-бот,
[ ] — соседний паттерн, Home — в начало, Ctrl+M — метроном.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QEvent, QObject, QRect, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QHBoxLayout,
                             QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox, QProgressBar, QPushButton,
                             QSpinBox, QSplitter, QVBoxLayout, QWidget)

import daw_dsp as D
import daw_engine as E
import daw_fx as FX
import daw_inst as I
import daw_project as PR
import daw_ui as U

from daw_meta import THEME_NAME, studio_qss, studio_theme_dict  # noqa: F401
from frameclock import FrameTimer


class _Bridge(QObject):
    arranged = pyqtSignal(object, int)
    sources_ready = pyqtSignal()
    exported = pyqtSignal(object)
    preview = pyqtSignal(object)
    hint = pyqtSignal(str)


# ------------------------------------------------------------------ #
#  Верхняя панель                                                     #
# ------------------------------------------------------------------ #

class MenuButton(QWidget):
    def __init__(self, text, fn, parent=None):
        super().__init__(parent)
        self.text = text
        self.fn = fn
        self.hover = False
        from PyQt6.QtGui import QFontMetrics
        self.setFixedHeight(26)
        self.setFixedWidth(QFontMetrics(U.font(11, True)).horizontalAdvance(text) + 18)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def enterEvent(self, e):
        self.hover = True
        self.update()

    def leaveEvent(self, e):
        self.hover = False
        self.update()

    def mousePressEvent(self, e):
        self.fn(self.mapToGlobal(self.rect().bottomLeft()))

    def paintEvent(self, e):
        p = QPainter(self)
        if self.hover:
            p.fillRect(self.rect(), QColor("#454e55"))
        p.setPen(U.TEXT if self.hover else QColor("#c2cad0"))
        p.setFont(U.font(11, True))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text)
        p.end()


class ModeSwitch(QWidget):
    """Переключатель ПАТТЕРН / ПЕСНЯ."""

    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self.sh = shell
        self.setFixedSize(112, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def enterEvent(self, e):
        U.hint_to(self, "Что играет: один паттерн по кругу или вся песня из плейлиста (L)")

    def mousePressEvent(self, e):
        self.sh.set_mode("song" if e.position().x() > self.width() / 2 else "pat")

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        song = self.sh.p.get("mode") == "song"
        for i, (txt, on) in enumerate((("ПАТ", not song), ("ПЕСНЯ", song))):
            r = QRectF(i * w / 2 + 1, 1, w / 2 - 2, h - 2)
            p.setBrush(U.ACC if on else QColor("#2f353a"))
            p.setPen(QPen(QColor("#1d2124"), 1))
            p.drawRoundedRect(r, 3, 3)
            p.setPen(QColor("#15181a") if on else U.DIM)
            p.setFont(U.font(11, True))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, txt)
        p.end()


class HintBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(20)
        self.text = ""
        self.right = ""

    def set(self, t):
        if t != self.text:
            self.text = t
            self.update()

    def set_right(self, t):
        if t != self.right:
            self.right = t
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#262b30"))
        p.setFont(U.font(11))
        p.setPen(QColor("#b6c0c7"))
        fm = p.fontMetrics()
        rw = fm.horizontalAdvance(self.right) + 16
        p.drawText(QRectF(8, 0, self.width() - rw - 16, self.height()), Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(self.text, Qt.TextElideMode.ElideRight, self.width() - rw - 20))
        p.setPen(U.ACC)
        p.drawText(QRectF(self.width() - rw, 0, rw - 8, self.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, self.right)
        p.end()


class TopBar(QWidget):
    """Два ряда: меню и строка подсказки; под ними — транспорт и кнопки окон."""

    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self.sh = shell
        self.setFixedHeight(98)
        vv = QVBoxLayout(self)
        vv.setContentsMargins(0, 0, 0, 0)
        vv.setSpacing(0)
        r1 = QHBoxLayout()
        r1.setContentsMargins(4, 0, 0, 0)
        r1.setSpacing(0)
        for txt, fn in (("ФАЙЛ", shell.menu_file), ("ПРАВКА", shell.menu_edit), ("ДОБАВИТЬ", shell.menu_add),
                        ("ПАТТЕРНЫ", shell.menu_patterns), ("ВИД", shell.menu_view),
                        ("ПАРАМЕТРЫ", shell.menu_options), ("ТЕМЫ", shell.menu_themes)):
            r1.addWidget(MenuButton(txt, fn))
        r1.addSpacing(10)
        self.hintw = HintBar(self)
        self.hintw.setFixedHeight(26)
        r1.addWidget(self.hintw, 1)
        vv.addLayout(r1)
        h = QHBoxLayout()
        h.setContentsMargins(6, 3, 6, 5)
        h.setSpacing(4)
        vv.addLayout(h)
        self.mode = ModeSwitch(shell)
        h.addWidget(self.mode)
        self.btn_play = U.IconButton("play", "Играть / стоп (пробел)", 28, checkable=True, color=U.GRN)
        self.btn_play.clicked.connect(shell.toggle_play)
        h.addWidget(self.btn_play)
        self.btn_stop = U.IconButton("stop", "Стоп — вернуться к началу", 28)
        self.btn_stop.clicked.connect(shell.stop)
        h.addWidget(self.btn_stop)
        self.btn_rec = U.IconButton("rec", "Запись с микрофона (R): взвести и нажать «играть»", 28, checkable=True,
                                    color=U.RED)
        self.btn_rec.clicked.connect(shell.toggle_rec)
        h.addWidget(self.btn_rec)
        self.mic = U.Meter(None, 6, 26, stereo=False)
        U.tip(self.mic, "Уровень микрофона")
        h.addWidget(self.mic)
        h.addSpacing(4)
        self.tempo = U.NumBox(lambda: shell.p.get("bpm", 130.0), shell.set_bpm, 20, 400, 1.0, "{:.2f}",
                              "Темп, BPM: тянуть вверх/вниз (Ctrl — точно), колесо, двойной щелчок — ввести", w=78)
        h.addWidget(self.tempo)
        self.time = U.Lcd(None, 104, 26, tip="Позиция (щелчок — такты или минуты)")
        self.time.clicked.connect(shell.toggle_time_fmt)
        h.addWidget(self.time)
        self.btn_metro = U.IconButton("metro", "Метроном (Ctrl+M)", 28, checkable=True)
        self.btn_metro.toggled.connect(shell.set_metronome)
        h.addWidget(self.btn_metro)
        self.mvol = U.Knob(FX.P("mv", "Громкость мастера", 0, 1.25, 0.85, "%"), lambda: shell.p.get("master_vol", 0.85),
                           lambda v: shell.p.__setitem__("master_vol", float(v)), 26, U.ACC)
        h.addWidget(self.mvol)
        self.meter = U.Meter(None, 10, 26)
        h.addWidget(self.meter)
        self.scope = U.Scope(None, 84, 26)
        h.addWidget(self.scope)
        self.cpu = U.Lcd(None, 64, 26, U.GRN, 11, "Загрузка звукового потока и память")
        h.addWidget(self.cpu)
        h.addStretch(1)
        # Transport and window navigation each have their own row; no squeezed labels.
        h=QHBoxLayout();h.setContentsMargins(6,0,6,4);h.setSpacing(4);vv.addLayout(h)
        self.win_btns = {}
        for key, ic, tip, txt in (("playlist", "playlist", "Плейлист (F5): вся песня — клипы по дорожкам", "Плейлист"),
                                  ("rack", "rack", "Стойка каналов (F6): удары по клеточкам", "Каналы"),
                                  ("roll", "roll", "Пианоролл (F7): ноты мелодии, баса, 808", "Пианоролл"),
                                  ("browser", "browser", "Браузер (Alt+F8): звуки, инструменты, эффекты, сэмплы", "Браузер"),
                                  ("mixer", "mixer", "Микшер (F9): громкость и эффекты дорожек", "Микшер")):
            b = U.IconButton(ic, tip, 28, checkable=True, text=txt)
            b.clicked.connect(lambda _=False, key=key: shell.toggle_window(key))
            self.win_btns[key] = b
            h.addWidget(b)
        self.btn_bot = U.IconButton("bot", "Мэшап-бот (F10): вокал одного трека на бите другого", 28, checkable=True,
                                    text="Мэшап-бот")
        self.btn_bot.clicked.connect(lambda: shell.toggle_window("bot"))
        self.win_btns["bot"] = self.btn_bot
        h.addWidget(self.btn_bot)
        picker=U.IconButton('note','Поиск инструментов, пресетов и эффектов (F8)',28,text='Плагины')
        picker.clicked.connect(self.sh.plugin_picker);h.addWidget(picker)
        layouts=QComboBox();layouts.addItem("Рабочее место…",None)
        for title,key in (("Аранжировка","arrange"),("Создание бита","beat"),("Ноты и аккорды","notes"),("Сведение","mix")):
            layouts.addItem(title,key)
        layouts.setToolTip("Готовые раскладки: окна рядом, без перекрытий. Сохраняются автоматически.")
        layouts.currentIndexChanged.connect(lambda: shell.workspace_preset(layouts.currentData()) if layouts.currentData() else None)
        h.addWidget(layouts)
        h.addStretch(1)
        b = U.IconButton("star", "С чего начать: главные действия студии одной кнопкой", 28, text="Начать")
        b.clicked.connect(lambda: shell.show_window("start"))
        h.addWidget(b)
        b = U.IconButton("note", "Короткая справка по студии (F1)", 28, text="Справка")
        b.clicked.connect(shell.help_dialog)
        h.addWidget(b)
        for ic, tip, fn in (("undo", "Отменить (Ctrl+Z)", shell.undo), ("redo", "Повторить (Ctrl+Y)", shell.redo),
                            ("save", "Сохранить проект (Ctrl+S)", lambda: shell.save_project())):
            b = U.IconButton(ic, tip, 28)
            b.clicked.connect(fn)
            h.addWidget(b)

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(QRect(0, 0, self.width(), 26), QColor("#2b3136"))
        g = QLinearGradient(0, 26, 0, self.height())
        g.setColorAt(0, QColor("#3d454c"))
        g.setColorAt(1, QColor("#30373c"))
        p.fillRect(QRect(0, 26, self.width(), self.height() - 26), g)
        p.setPen(QColor("#1c2023"))
        p.drawLine(0, 26, self.width(), 26)
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        p.end()


HELP_HTML = """
<h3>С чего начать</h3>
<p><b>Мэшап</b> — окно «Мэшап-бот»: выбери трек для голоса и трек для бита, нажми «Сделать мэшап».
Бот сам подгонит темп и тон, разложит всё по плейлисту. «Ещё вариант» — другое место вступления голоса.</p>
<p><b>Ремейк</b> — в боте «Разобрать бит»: из трека достаются бочка, 808, малый, хэты и мелодия;
открывается проект, где ноты стоят как в оригинале. Звуки — в браузере, «Мои сэмплы».</p>
<p><b>Свой бит</b> — «Каналы»: щёлкай по клеточкам — это удары. Пробел — слушать.
Мелодия, бас и 808 — в «Пианоролле»: щелчок по сетке — нота.</p>
<h3>Окна</h3>
<p><b>Плейлист</b> — вся песня: паттерны и аудио по дорожкам. <b>Каналы</b> — инструменты и удары.
<b>Пианоролл</b> — ноты выбранного канала (вверху — какой паттерн правишь и стоит ли он в песне).
<b>Браузер</b> слева — звуки кита, свои сэмплы, инструменты (волновой синт — больше всего пресетов),
эффекты и пресеты голоса: двойной щелчок — добавить. <b>Микшер</b> — громкость и эффекты дорожек.</p>
<h3>Пианоролл</h3>
<p>Щелчок — нота, тянуть ноту — двигать, тянуть правый край — длина, правая кнопка — удалить.
Ctrl + рамка — выделить, Shift + тянуть — копия, Alt — без сетки, средняя кнопка — двигать вид,
колесо — вверх/вниз, Ctrl + колесо — масштаб. Стрелки — сдвиг и транспонирование, Delete — удалить.</p>
<h3>Голос</h3>
<p>R — взвести запись, пробел — пишем. Дубль ложится на канал «Голос». Обработка — пресеты голоса в браузере
(хайперпоп, рейдж, эмо, гармонии, даблы, форманта, автотюн…) — двойной щелчок ставит цепочку на дорожку.</p>
<h3>Клавиши</h3>
<p>Пробел — играть/стоп, L — паттерн или песня, Ctrl+Z / Ctrl+Y — отменить/повторить, Ctrl+S — сохранить,
Ctrl+R — экспорт в MP3, F5–F10 — окна, F1 — эта справка.</p>
"""


class StartPanel(QWidget):
    """«С чего начать»: главные действия студии большими кнопками с одной строкой пояснения."""

    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self.sh = shell
        self.setStyleSheet("background: #2a3035;")
        from PyQt6.QtWidgets import QGridLayout
        g = QGridLayout(self)
        g.setContentsMargins(14, 12, 14, 12)
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(10)
        items = [
            ("bot", "Сделать мэшап", "Голос одной песни на бит другой — темп и тон бот подгонит сам", self._mashup),
            ("drum", "Разобрать бит на звуки", "Бочка, 808, хэты и мелодия из любого трека — для ремейка", self._split),
            ("rack", "Бит с нуля", "Щёлкай по клеточкам — это удары. Пробел — слушать", self._beat),
            ("roll", "Мелодия и 808", "Пианоролл: щелчок по сетке — нота. Звуки — в браузере слева", self._melody),
            ("folder", "Свои сэмплы", "Добавь папку с паками 808, драмов и петель", self._samples),
            ("mic", "Записать голос", "R — взвести запись, пробел — поехали. Эффекты — пресеты голоса", self._voice),
        ]
        for i, (ic, title, sub, fn) in enumerate(items):
            box = QVBoxLayout()
            box.setSpacing(3)
            b = U.IconButton(ic, sub, 40, text=title, color=U.ACC if i < 2 else None)
            b.clicked.connect(fn)
            box.addWidget(b)
            lab = U.label(sub, 11)
            lab.setWordWrap(True)
            box.addWidget(lab)
            g.addLayout(box, i // 2, i % 2)
        row = QHBoxLayout()
        lab = U.label("Подробнее — кнопка «Справка» сверху (F1). Это окно можно закрыть крестиком.", 11)
        row.addWidget(lab, 1)
        g.addLayout(row, 3, 0, 1, 2)

    def _close(self):
        w = self.sh.wins.get("start")
        if w is not None:
            w.close_win()

    def _mashup(self):
        self._close()
        self.sh.show_window("bot")

    def _split(self):
        self._close()
        self.sh.show_window("bot")
        self.sh.bot.say("Выбери трек в поле «Бит» (поиск по мере набора) и нажми «Разобрать бит».")
        self.sh.bot.beat.setFocus()

    def _beat(self):
        self._close()
        self.sh.set_mode("pat", quiet=True)
        self.sh.show_window("rack")
        self.sh.hint("Щёлкай по клеточкам каналов — это удары; пробел — слушать по кругу")

    def _melody(self):
        self._close()
        self.sh.browser.show()
        sec = self.sh.browser.sections.get("inst")
        if sec is not None:
            sec.setExpanded(True)
        self.sh.show_window("roll")
        self.sh.hint("Инструмент — двойной щелчок в браузере («Инструменты»), ноты — щелчком в пианоролле")

    def _samples(self):
        self._close()
        self.sh.browser.show()
        self.sh.browser.add_sample_dir()

    def _voice(self):
        self._close()
        self.sh.browser.show()
        sec = self.sh.browser.sections.get("voice")
        if sec is not None:
            sec.setExpanded(True)
        self.sh.hint("Нажми R, затем пробел — пой. Пресет голоса — двойной щелчок в браузере («Пресеты голоса»)")


class Workspace(QWidget):
    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self.sh = shell
        self.setAcceptDrops(True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self._bg = None                                  # фон готовой картинкой (раньше — тысячи точек на кадр)

    def bg_color_at(self, y):
        t = max(0.0, min(1.0, y / max(1, self.height())))
        a, b = QColor("#23282c"), QColor("#1a1e21")
        return QColor(int(a.red() + (b.red() - a.red()) * t), int(a.green() + (b.green() - a.green()) * t),
                      int(a.blue() + (b.blue() - a.blue()) * t))

    def _make_bg(self):
        from PyQt6.QtGui import QPixmap
        dpr = self.devicePixelRatioF()
        pm = QPixmap(max(1, int(self.width() * dpr)), max(1, int(self.height() * dpr)))
        pm.setDevicePixelRatio(dpr)
        p = QPainter(pm)
        g = QLinearGradient(0, 0, 0, self.height())
        g.setColorAt(0, QColor("#23282c"))
        g.setColorAt(1, QColor("#1a1e21"))
        p.fillRect(QRect(0, 0, self.width(), self.height()), g)
        p.setPen(QColor(255, 255, 255, 10))
        from PyQt6.QtCore import QPoint
        pts = [QPoint(x, y) for x in range(0, self.width(), 24) for y in range(0, self.height(), 24)]
        if pts:
            p.drawPoints(pts)
        p.setPen(QColor(255, 255, 255, 22))
        p.setFont(U.font(26, True))
        p.drawText(QRect(0, 0, self.width() - 20, self.height() - 14),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom, "ECHOES STUDIO")
        p.end()
        self._bg = (self.width(), self.height(), dpr, pm)

    def paintEvent(self, e):
        bg = self._bg
        if bg is None or bg[0] != self.width() or bg[1] != self.height() or bg[2] != self.devicePixelRatioF():
            self._make_bg()
        p = QPainter(self)
        r = e.rect()
        p.drawPixmap(r, self._bg[3], QRectF(r.x() * self._bg[2], r.y() * self._bg[2],
                                             r.width() * self._bg[2], r.height() * self._bg[2]).toRect())
        p.end()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.sh.keep_windows_inside()

    def mousePressEvent(self, e):
        self.sh.setFocus()

    def dragEnterEvent(self, e):
        if e.mimeData().hasText() and e.mimeData().text().startswith("echoes:"):
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.sh.drop_item(e.mimeData().text(), self.sh.song_beat(), None)
        e.acceptProposedAction()


# ------------------------------------------------------------------ #
#  Оболочка                                                           #
# ------------------------------------------------------------------ #

class StudioShell(QWidget):
    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        self.setObjectName("StudioShell")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(U.QSS + "QWidget#StudioShell { background: #1c2023; }")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._settings_host = None
        self._closing = False
        self._src_cancel = threading.Event()             # вышли из студии — бросить подготовку звука
        self.modified = False
        self.time_fmt = "bars"
        self.typing_keys = False
        self._song_pos = 0.0
        self._last_save = time.time()
        self.br = _Bridge()
        self.br.arranged.connect(self._on_arranged)
        self.br.sources_ready.connect(self._on_sources)
        self.br.preview.connect(self._on_preview_data)
        self.br.hint.connect(self.hint)
        self.hist = PR.History()
        self.sources = PR.SourceCache()
        self.renderer = PR.Renderer(self.sources)
        self.p = self._initial_project()
        self.eng = E.Engine(self.p, device=self.S().get("out_device"))
        self.eng.metronome = bool(self.S().get("metronome", False))
        self.eng.mixer.sync_fx()
        self._rb_busy = False
        self._rb_again = False
        self._rb_gen = 0
        self._loading = set()
        self._prev_file = None
        self._pause_player()
        # раскладка
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.top = TopBar(self)
        v.addWidget(self.top)
        self.hintbar = self.top.hintw
        self.split = QSplitter(Qt.Orientation.Horizontal)
        self.split.setHandleWidth(3)
        self.split.setStyleSheet("QSplitter::handle { background: #15181a; }")
        import daw_browser
        self.browser = daw_browser.Browser(self)
        self.workspace = Workspace(self)
        self.split.addWidget(self.browser)
        self.split.addWidget(self.workspace)
        self.split.setStretchFactor(1, 1)
        self.split.setSizes([230, 1200])
        v.addWidget(self.split, 1)
        # окна
        import daw_bot
        import daw_mixer
        import daw_playlist
        import daw_rack
        import daw_roll
        self.playlist = daw_playlist.Playlist(self)
        self.rack = daw_rack.ChannelRack(self)
        self.roll = daw_roll.PianoRoll(self)
        self.mixer_view = daw_mixer.MixerView(self)
        self.bot = daw_bot.BotView(self)
        self.wins = {}
        self._mk_win("playlist", "Плейлист", "playlist", self.playlist, self.playlist.toolbar)
        self._mk_win("rack", "Стойка каналов", "rack", self.rack, self.rack.toolbar)
        self._mk_win("roll", "Пианоролл", "roll", self.roll, self.roll.toolbar)
        self._mk_win("mixer", "Микшер", "mixer", self.mixer_view, None)
        self._mk_win("bot", "Мэшап-бот", "bot", self.bot, None)
        self.start_panel = StartPanel(self)
        self._mk_win("start", "С чего начать", "star", self.start_panel, None)
        self.plugins = {}
        self._layout_done = False
        self.rack.rebuild()
        self.roll.set_channel(PR.cur_channel(self.p)["id"] if PR.cur_channel(self.p) and
                              PR.cur_channel(self.p).get("gen") != "audio" else None)
        self.playlist.refresh()
        self.mixer_view.refresh()
        self.top.btn_metro.setChecked(self.eng.metronome)
        self.timer = FrameTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self._rb_timer = QTimer(self)
        self._rb_timer.setSingleShot(True)
        self._rb_timer.timeout.connect(self._rebuild)
        self._rebuild()
        self._mute_player_shortcuts(True)
        self.hint("Добро пожаловать в Echoes Studio. Пробел — играть, F10 — мэшап-бот, F1 — справка по клавишам.")
        self._update_title()

    # ── настройки ── #
    def S(self) -> dict:
        return self.win.settings.setdefault("studio", {})

    def save_settings(self):
        try:
            self.win.save_settings()
        except Exception:                                # noqa: BLE001
            pass

    def library(self):
        return [t for t in (getattr(self.win, "library", None) or []) if isinstance(t, dict)]

    def _initial_project(self):
        path = self.S().get("last_project")
        if path and os.path.exists(path):
            try:
                return PR.load(path)
            except Exception as e:                       # noqa: BLE001
                print("[studio] load:", e)
        return PR.new_project()

    def _pause_player(self):
        try:
            if self.win.engine.is_playing():
                self.win.engine.pause()
                self.win._set_playing_ui(False)
        except Exception:                                # noqa: BLE001
            pass

    # ── совместимость с main.py ── #
    def holds(self) -> bool:
        return True

    def on_track_end(self):
        pass

    def adopt_settings(self, panel):
        self._settings_host = panel
        panel.hide()

    def release_settings(self):
        p = self._settings_host
        self._settings_host = None
        return p

    def shutdown(self):
        if self._closing:
            return
        self._closing = True
        self._src_cancel.set()                           # нейросеть/растяжение в фоне грузили все ядра и
        self._mute_player_shortcuts(False)               # после выхода из студии
        self.timer.stop()
        try:
            self.bot.cancel.set()
        except Exception:                                # noqa: BLE001
            pass
        try:
            import daw_sep
            daw_sep.release()
        except Exception:                                # noqa: BLE001
            pass
        self._autosave(force=True)
        self._save_layout()
        self.eng.close()
        self.sources.clear()
        self.renderer.clear()
        try:
            import mem_trim
            mem_trim.trim_later(2000)
        except Exception:                                # noqa: BLE001
            pass

    def leave_theme(self):
        QTimer.singleShot(0, lambda: self.win._apply_theme("Echoes Music"))

    def _mute_player_shortcuts(self, on):
        """Горячие клавиши плеера (пробел, стрелки, Ctrl+S…) в студии свои — на время выключить."""
        from PyQt6.QtGui import QShortcut
        if on:
            self._muted_sc = []
            try:
                for sc in self.win.findChildren(QShortcut):
                    keyname = sc.key().toString()
                    if sc.isEnabled() and keyname not in ("F11", "Ctrl+Shift+T"):
                        sc.setEnabled(False)
                        self._muted_sc.append(sc)
            except RuntimeError:
                pass
        else:
            for sc in getattr(self, "_muted_sc", []):
                try:
                    sc.setEnabled(True)
                except RuntimeError:
                    pass
            self._muted_sc = []

    # ── подсказка ── #
    def hint(self, text):
        try:
            self.hintbar.set(text)
        except RuntimeError:
            pass

    def _update_title(self):
        nm = self.p.get("name", "Без названия")
        self.hintbar.set_right(f"{nm}{' *' if self.modified else ''}  ·  {self.p.get('bpm', 130):.2f} BPM")

    # ── окна ── #
    def _mk_win(self, key, title, icon, content, toolbar):
        w = U.DawWindow(key, title, icon, content, self.workspace, toolbar)
        w.hide()
        w.activated.connect(self._activate)
        w.closed.connect(lambda key=key: self._win_closed(key))
        w.geometry_changed.connect(self._save_layout_later)
        self.wins[key] = w
        return w

    def _default_geo(self, key):
        W, H = max(900, self.workspace.width()), max(500, self.workspace.height())
        return {"playlist": QRect(8, 8, W - 16, H - 16),
                "rack": QRect(30, 40, min(720, W - 60), min(330, H - 60)),
                "roll": QRect(60, int(H * 0.30), W - 120, int(H * 0.62)),
                "mixer": QRect(20, int(H * 0.36), W - 40, int(H * 0.6)),
                "bot": QRect(W - min(620, W - 40) - 20, 30, min(620, W - 40), min(560, H - 50)),
                "start": QRect(max(10, (W - 640) // 2), max(10, (H - 470) // 2), min(640, W - 20), min(470, H - 20))}.get(
            key, QRect(80, 60, 560, 380))

    def workspace_preset(self,kind):
        """Tiled arrangements in actual available space, with browser retained."""
        W,H=max(100,self.workspace.width()),max(100,self.workspace.height())
        gap=6;top=int(H*.58);left=int(W*.56)
        layouts={
            'arrange':{'playlist':(0,0,W,top),'rack':(0,top+gap,left,H-top-gap),'mixer':(left+gap,top+gap,W-left-gap,H-top-gap)},
            'beat':{'rack':(0,0,W,int(H*.43)),'roll':(0,int(H*.43)+gap,W,H-int(H*.43)-gap)},
            'notes':{'roll':(0,0,W,top),'rack':(0,top+gap,W,H-top-gap)},
            'mix':{'playlist':(0,0,W,int(H*.38)),'mixer':(0,int(H*.38)+gap,W,H-int(H*.38)-gap)}}
        if kind not in layouts:return
        self.browser.show()
        for key,w in self.wins.items():
            w.setVisible(key in layouts[kind])
            if key in layouts[kind]:w.setGeometry(QRect(*layouts[kind][key]))
        self.keep_windows_inside();self._sync_win_btns();self._save_layout()
        self.hint("F5 — плейлист · F6 — каналы · F7 — ноты · F9 — микшер. Раскладка сохранена.")

    def apply_layout(self, reset=False):
        geo = {} if reset else (self.S().get("geo") or {})
        for key, w in self.wins.items():
            g = geo.get(key)
            if g and len(g) == 5:
                w.setGeometry(QRect(*g[:4]))
                w.setVisible(bool(g[4]))
            else:
                w.setGeometry(self._default_geo(key))
                w.setVisible(key in ("playlist", "rack"))
        if not reset and not geo:
            self.wins["playlist"].show()
            self.wins["rack"].show()
        self.wins["playlist"].lower()
        self.keep_windows_inside()
        self._sync_win_btns()
        self._layout_done = True
        self._maybe_start()

    def _maybe_start(self):
        """Пустая песня (ничего в плейлисте) — показать «С чего начать»."""
        w = self.wins.get("start")
        if w is None:
            return
        if not self.p.get("clips") and not self.p.get("mashup"):
            w.setGeometry(self._default_geo("start"))
            self.show_window("start")
        elif w.isVisible():
            w.close_win()

    def showEvent(self, e):
        super().showEvent(e)
        if not self._layout_done:
            QTimer.singleShot(0, self.apply_layout)

    def keep_windows_inside(self):
        ws = self.workspace.rect()
        for w in list(self.wins.values()) + list(self.plugins.values()):
            try:
                g = w.geometry()
            except RuntimeError:
                continue
            if g.width() > ws.width() + 4 or g.height() > ws.height() + 4:
                g.setWidth(min(g.width(), ws.width()))
                g.setHeight(min(g.height(), ws.height()))
            if g.right() > ws.right():
                g.moveRight(max(g.width() - 40, ws.right()))
            if g.bottom() > ws.bottom():
                g.moveBottom(max(g.height(), ws.bottom()))
            g.moveLeft(max(0, g.left()) if g.left() > -g.width() + 80 else 0)
            g.moveTop(max(0, g.top()))
            w.setGeometry(g)

    def _save_layout_later(self):
        QTimer.singleShot(400, self._save_layout)

    def _save_layout(self):
        try:
            geo = {k: [w.x(), w.y(), w.width(), w.height(), int(w.isVisible())] for k, w in self.wins.items()}
            self.S()["geo"] = geo
            self.save_settings()
        except RuntimeError:
            pass

    def _win_closed(self, key):
        self._sync_win_btns()
        self._save_layout_later()

    def _sync_win_btns(self):
        for k, b in self.top.win_btns.items():
            if k == "browser":
                b.setChecked(self.browser.isVisible())
            else:
                w = self.wins.get(k)
                b.setChecked(bool(w and w.isVisible()))

    def show_window(self, key):
        if key == "browser":
            self.browser.show()
            self._sync_win_btns()
            return
        w = self.wins.get(key)
        if w is None:
            return
        if not w.isVisible():
            w.show()
        w.raise_()
        self._activate(w)
        if key == "bot":
            self.bot.refresh()
            self.bot.input.setFocus()
        self._sync_win_btns()
        self._save_layout_later()

    def toggle_window(self, key):
        if key == "browser":
            self.browser.setVisible(not self.browser.isVisible())
            self._sync_win_btns()
            return
        w = self.wins.get(key)
        if w is None:
            return
        if w.isVisible() and self._active is w:
            w.hide()
        else:
            self.show_window(key)
        self._sync_win_btns()
        self._save_layout_later()

    _active = None

    def _activate(self, w):
        if self._active is w:
            return
        if self._active is not None:
            try:
                self._active.active = False
                self._active.update()
            except RuntimeError:
                pass
        self._active = w
        w.active = True
        w.update()

    # ── плагины ── #
    def _plugin_win(self, key, title, kind, ref):
        import daw_plugins
        w = self.plugins.get(key)
        if w is not None:
            try:
                w.isVisible()
            except RuntimeError:
                w = None
        if w is None:
            panel = daw_plugins.PluginPanel(self, kind, ref)
            if not panel.valid():
                return None
            w = U.DawWindow(key, panel.title(), "fx" if kind == "fx" else "note", panel, self.workspace)
            w.activated.connect(self._activate)
            w.closed.connect(lambda key=key: self._plugin_closed(key))
            n = len(self.plugins)
            ws = self.workspace.rect()
            t = getattr(panel.cls(), "TYPE", "")
            ww = 600
            hh = 580 if kind == "gen" else (560 if t == "eq" else 460)
            w.setGeometry(min(ws.width() - ww - 10, 140 + 26 * (n % 8)), min(ws.height() - hh - 10, 50 + 26 * (n % 8)),
                          min(ww, ws.width() - 20), min(hh, ws.height() - 20))
            self.plugins[key] = w
        w.show()
        w.raise_()
        self._activate(w)
        return w

    def _plugin_closed(self, key):
        w = self.plugins.pop(key, None)
        if w is not None:
            QTimer.singleShot(0, w.deleteLater)

    def open_channel_plugin(self, cid, toggle=False):
        c = PR.chan(self.p, cid)
        if c is None or c.get("gen") == "audio":
            if c is not None:
                self.hint("Аудиоканал: громкость и эффекты — на его дорожке микшера, клипы — в плейлисте")
                self.show_window("mixer")
                self.mixer_view.select(int(c.get("insert", 0)))
            return
        key = f"gen:{cid}"
        w = self.plugins.get(key)
        if toggle and w is not None and w.isVisible() and self._active is w:
            w.close_win()
            return
        self._plugin_win(key, c["name"], "gen", cid)

    def open_fx_plugin(self, i, s, toggle=False):
        key = f"fx:{i}:{s}"
        w = self.plugins.get(key)
        if toggle and w is not None and w.isVisible() and self._active is w:
            w.close_win()
            return
        self._plugin_win(key, "", "fx", (i, s))

    def close_fx_plugin(self, i, s):
        w = self.plugins.pop(f"fx:{i}:{s}", None)
        if w is not None:
            w.hide()
            QTimer.singleShot(0, w.deleteLater)

    def _close_all_plugins(self):
        for key in list(self.plugins):
            w = self.plugins.pop(key)
            try:
                w.hide()
                w.deleteLater()
            except RuntimeError:
                pass

    def _refresh_plugins(self):
        for key, w in list(self.plugins.items()):
            try:
                panel = w.content
                if not panel.valid():
                    self.plugins.pop(key)
                    w.hide()
                    w.deleteLater()
                    continue
                w.set_title(panel.title())
                panel.sync_knobs()
            except RuntimeError:
                self.plugins.pop(key, None)

    # ── изменения проекта ── #
    def commit(self, label=""):
        self.hist.push(self.p, label)
        self.modified = True

    def touch(self, rebuild=True, fx=False, views=("rack", "roll", "playlist", "mixer", "browser")):
        self.modified = True
        if fx:
            self.eng.mixer.sync_fx()
        if rebuild:
            self._rb_timer.start(60)
        for v in views:
            try:
                if v == "rack":
                    self.rack.refresh()
                elif v == "roll":
                    self.roll.refresh()
                elif v == "playlist":
                    self.playlist.refresh()
                elif v == "mixer":
                    self.mixer_view.refresh()
                elif v == "browser":
                    self.browser.refresh_project()
                elif v == "plugins":
                    self._refresh_plugins()
            except RuntimeError:
                pass
        self._update_title()

    def _rebuild(self):
        if self._closing:
            return
        if self._rb_busy:
            self._rb_again = True
            return
        self._rb_busy = True
        self._rb_gen += 1
        gen = self._rb_gen
        snap = json.loads(json.dumps(self.p))
        need = []

        def job():
            arr = None
            try:
                arr = self.renderer.build(snap, snap.get("mode", "pat"), on_source=lambda s, se, r: need.append((s, se, r)))
            except Exception as e:                       # noqa: BLE001
                print("[studio] arrange:", repr(e))
            if need:
                self._load_sources(need)
            self.br.arranged.emit(arr, gen)
        threading.Thread(target=job, daemon=True, name="studio-arrange").start()

    def _on_arranged(self, arr, gen):
        self._rb_busy = False
        if arr is not None and not self._closing:
            self.eng.mixer.arr = arr
            if self.eng.pos > arr.length and arr.length > 0 and self.eng.loop:
                self.eng.pos %= arr.length
        if self._rb_again:
            self._rb_again = False
            self._rebuild()

    def _load_sources(self, need):
        todo = []
        for s, se, r in need:
            k = PR.recipe_key(s, se, r)
            if k not in self._loading:
                self._loading.add(k)
                todo.append((k, s, se, r))
        if not todo:
            return

        def job():
            for k, s, se, r in todo:
                if self._closing:
                    break
                try:
                    self._hint_safe(f"Готовлю звук: {s.get('name', '')}…")
                    self.sources.load(s, se, r, cancel=self._src_cancel)
                except Exception as e:                   # noqa: BLE001
                    if self._closing:
                        break
                    self._hint_safe(f"Не удалось подготовить «{s.get('name', '')}»: {e}")
                finally:
                    self._loading.discard(k)
            self.br.sources_ready.emit()
        threading.Thread(target=job, daemon=True, name="studio-sources").start()

    def _hint_safe(self, t):
        """Подсказка из фонового потока (таймеры Qt там заводить нельзя — только сигнал)."""
        try:
            self.br.hint.emit(str(t))
        except RuntimeError:
            pass

    def _on_sources(self):
        if self._closing:
            return
        self.hint("Звук готов")
        self._rebuild()
        self.playlist.canvas.cache.clear()
        self.playlist.refresh()

    def replace_project(self, newp, keep_history=False):
        was = self.eng.playing
        self.eng.playing = False
        self.eng.mixer.stop_previews()
        PR.ensure(newp)
        self.p.clear()
        self.p.update(newp)
        if not keep_history:
            self.hist = PR.History()
        self.eng.mixer.sync_fx()
        self._close_all_plugins()
        self.renderer.segs.clear()
        self.rack.rebuild()
        c = PR.cur_channel(self.p)
        self.roll.set_channel(c["id"] if c and c.get("gen") != "audio" else None)
        self.playlist.canvas.sel = []
        self.playlist.canvas.cache.clear()
        self.playlist.place = None
        self.touch(views=("rack", "roll", "playlist", "mixer", "browser"))
        if keep_history and was:
            self.eng.playing = True
        self._update_title()

    def undo(self):
        newp, label = self.hist.do_undo(self.p)
        if newp is None:
            self.hint("Отменять нечего")
            return
        self.replace_project(newp, keep_history=True)
        self.hint(f"Отменено: {label}")

    def redo(self):
        newp, label = self.hist.do_redo(self.p)
        if newp is None:
            self.hint("Повторять нечего")
            return
        self.replace_project(newp, keep_history=True)
        self.hint(f"Повторено: {label}")

    # ── транспорт ── #
    def spb(self):
        return PR.spb(self.p)

    def toggle_play(self):
        if self.eng.playing:
            self.stop()
        else:
            self.play()

    def play(self):
        if self.p.get("mode") == "song":
            self.eng.play(int(self._song_pos * self.spb()))
        else:
            self.eng.play(0)
        self.top.btn_play.setChecked(True)
        self.top.btn_play.icon = "pause"
        self.top.btn_play.update()

    def stop(self):
        was = self.eng.playing
        self.eng.stop()
        if not was:
            self._song_pos = 0.0
            self.eng.set_pos(0)
        self.top.btn_play.setChecked(False)
        self.top.btn_play.icon = "play"
        self.top.btn_play.update()
        for cv in (self.playlist.canvas, self.roll.canvas):  # убрать курсор воспроизведения
            cv.update_dynamic()
        self._collect_take()

    def set_mode(self, mode, quiet=False):
        if self.p.get("mode") == mode:
            return
        self.p["mode"] = mode
        playing = self.eng.playing
        self.eng.set_pos(int(self._song_pos * self.spb()) if mode == "song" else 0)
        if playing:
            self.eng.play_start = self.eng.pos
        self._rebuild()
        self.top.mode.update()
        self.playlist.canvas.update()
        if not quiet:
            self.hint("Играет вся песня из плейлиста" if mode == "song" else "Играет выбранный паттерн по кругу")

    def set_pos_beats(self, b):
        b = max(0.0, float(b))
        if self.p.get("mode") == "song":
            self._song_pos = b
        self.eng.set_pos(int(b * self.spb()))
        if self.eng.playing:
            self.eng.play_start = self.eng.pos
            self.eng.mixer.reset_fx()
        self.playlist.canvas.update()

    def set_bpm(self, v):
        v = round(max(20.0, min(400.0, float(v))), 3)
        if abs(v - float(self.p.get("bpm", 130))) < 1e-6:
            return
        old = float(self.p.get("bpm", 130))
        pos_b = self.eng.pos / (D.SR * 60.0 / old)
        self.p["bpm"] = v
        self.eng.pos = int(pos_b * self.spb())
        self.modified = True
        self._rb_timer.start(120)
        self._update_title()

    def set_metronome(self, on):
        self.eng.metronome = bool(on)
        self.S()["metronome"] = bool(on)

    def toggle_time_fmt(self):
        self.time_fmt = "min" if self.time_fmt == "bars" else "bars"

    def heard_beat(self):
        return self.eng.heard_pos() / self.spb()

    def song_beat(self):
        if self.p.get("mode") == "song":
            if self.eng.playing:
                return self.heard_beat()
            return self._song_pos
        return self._song_pos

    def song_beat_playing(self):
        if self.p.get("mode") == "song" and self.eng.playing:
            return self.heard_beat()
        return None

    def play_beat_in_pattern(self):
        if self.p.get("mode") == "pat" and self.eng.playing:
            return self.heard_beat()
        return None

    def cur_step(self):
        b = self.play_beat_in_pattern()
        return None if b is None else int(b * 4)

    # ── запись ── #
    def toggle_rec(self):
        on = self.top.btn_rec.isChecked()
        if on:
            ok = self.eng.open_input(self.S().get("in_device"))
            if not ok:
                self.top.btn_rec.setChecked(False)
                err = getattr(self.eng.rec, "err", "") if self.eng.rec else ""
                self.hint("Микрофон не открылся" + (f": {err}" if err else "") + " — проверьте его в «Параметры → Звук»")
                return
            self.eng.rec.armed = True
            self._voice_channel()
            if self.p.get("mode") != "song":
                self.set_mode("song", quiet=True)
            self.hint("Запись взведена: нажмите «играть» (пробел) и пойте, «стоп» — дубль ляжет в плейлист")
        else:
            self._collect_take()
            r = self.eng.rec
            if r is not None:
                r.armed = False
            self.eng.stop_rec()
            self.hint("Запись выключена")

    def _voice_channel(self):
        for c in self.p["channels"]:
            if c.get("gen") == "audio" and c.get("voice"):
                return c
        self.commit("Канал голоса")
        ins = PR.free_insert(self.p)
        c = PR.new_channel("Голос", "audio", insert=ins, color="#e8574f")
        c["voice"] = True
        self.p["channels"].append(c)
        if ins:
            self.p["mixer"][ins]["name"] = "Голос"
            self.p["mixer"][ins]["fx"] = FX.voice_chain("Студийный вокал") + [None] * 6
        self.touch(fx=True)
        return c

    def _collect_take(self):
        r = self.eng.rec
        if r is None:
            return
        take = r.take()
        if take is None or len(take["data"]) < D.SR * 0.3:
            return
        c = self._voice_channel()
        PR.RECS.mkdir(parents=True, exist_ok=True)
        n = len(list(PR.RECS.glob("*.wav"))) + 1
        name = f"Запись {n} — {time.strftime('%d.%m %H-%M')}"
        f = PR.RECS / (PR.safe_name(name) + ".wav")
        data = take["data"]
        shift = int(take["shift"])
        start = int(take["start"]) - shift
        if start < 0:
            data = data[-start:]
            start = 0
        D.write_wav(f, np.stack([data, data], 1))
        self.commit("Запись")
        src = PR.new_source(name, str(f), "mix")
        self.p["sources"].append(src)
        beat = start / self.spb()
        ln = len(data) / self.spb()
        lane = PR.free_lane(self.p, beat, ln)
        self.p["clips"].append({"id": PR.uid(), "kind": "audio", "ref": src["id"], "chan": c["id"], "lane": lane,
                                "start": round(beat, 4), "len": round(ln, 4), "off": 0.0, "gain": 1.0,
                                "fin": 0.0, "fout": 0.0})
        self.touch(views=("playlist", "browser"))
        self.browser.refresh_files()
        self.hint(f"Дубль записан: {name} ({len(data) / D.SR:.1f} с) — на дорожке {lane + 1}, эффекты — "
                  f"на дорожке микшера «Голос»")

    # ── каналы ── #
    def select_channel(self, cid, open_roll=False):
        if PR.chan(self.p, cid) is None:
            return
        self.p["sel_channel"] = cid
        self.rack.refresh()
        c = PR.chan(self.p, cid)
        if c.get("gen") != "audio":
            self.roll.set_channel(cid)
            if open_roll:
                self.show_window("roll")

    def open_roll(self, cid):
        self.roll.set_channel(cid)
        self.show_window("roll")

    def preview_channel(self, c, pitch=None, vel=0.8, beats=0.5):
        if c is None or c.get("gen") == "audio":
            return
        try:
            a = self.renderer.preview_note(c, int(pitch if pitch is not None else c.get("root", 60)), vel, beats,
                                           float(self.p.get("bpm", 130)))
        except Exception as e:                           # noqa: BLE001
            self.hint(f"Звук не получился: {e}")
            return
        if a is not None:
            self.eng.mixer.preview(int(c.get("insert", 0)), a, float(c.get("vol", 0.78)) / 0.78)

    def preview_sample(self, a):
        self.eng.mixer.stop_previews()
        self.eng.mixer.preview(0, a, 0.8)

    def preview_gen(self, kind, params):
        g = I.make(kind, dict(params))
        if g is None:
            return
        self.eng.mixer.stop_previews()
        a = g.render(60, 0.8, 0.6)
        self.eng.mixer.preview(0, a, 1.0)
        if kind not in ("808",):
            b = g.render(64, 0.7, 0.6)
            c = g.render(67, 0.7, 0.6)
            m = max(len(a), len(b), len(c))
            ch = np.zeros((m, 2), np.float32)
            for x in (a, b, c):
                ch[:len(x)] += x * 0.6
            self.eng.mixer.preview(0, np.concatenate([np.zeros((int(D.SR * 0.65), 2), np.float32), ch]), 1.0)

    def preview_file(self, path):
        self.eng.mixer.stop_previews()
        if self._prev_file == path:
            self._prev_file = None
            self.hint("Прослушивание остановлено")
            return
        self._prev_file = path
        self.hint("Слушаю: " + os.path.basename(path) + " (щелчок ещё раз — стоп)")

        def job():
            try:
                dur = 0.0
                for t in self.library():
                    if t.get("path") == path:
                        dur = float(t.get("duration") or 0)
                        break
                st = max(0.0, dur * 0.3) if dur > 40 else 0.0
                a = D.decode(path, D.SR, 2, start=st, dur=15.0)
                n = len(a)
                k = min(n, int(D.SR * 0.02))
                a[:k] *= np.linspace(0, 1, k)[:, None]
                k = min(n, int(D.SR * 1.5))
                a[-k:] *= np.linspace(1, 0, k)[:, None]
                self.br.preview.emit((path, a))
            except Exception as e:                       # noqa: BLE001
                self._hint_safe(f"Не прочитать файл: {e}")
        threading.Thread(target=job, daemon=True, name="studio-preview").start()

    def _on_preview_data(self, item):
        path, a = item
        if self._prev_file == path:
            self.eng.mixer.preview(0, a, 0.8)

    def add_channel(self, gen, params, name):
        self.commit("Новый канал")
        ins = PR.free_insert(self.p)
        col = I.DEFAULT_COLORS[len(self.p["channels"]) % len(I.DEFAULT_COLORS)]
        c = PR.new_channel(str(name)[:30] or "Канал", gen, params, insert=ins, color=col)
        self.p["channels"].append(c)
        if ins:
            self.p["mixer"][ins]["name"] = c["name"][:24]
        self.p["sel_channel"] = c["id"]
        self.touch(views=("rack", "roll", "mixer", "browser"))
        if gen != "audio":
            self.roll.set_channel(c["id"])
            self.preview_channel(c)
        self.show_window("rack")
        self.hint(f"Канал «{c['name']}» добавлен на дорожку микшера {ins}")
        return c

    def add_sample(self, path):
        """Свой сэмпл: петля и вокал — аудиоклипом в плейлист, звук — новым каналом сэмплера
        (у 808, баса и мелодий корневая нота — по высоте, длина ноты обрезает звук)."""
        import daw_samples as SM
        if not os.path.exists(path):
            self.hint("Файл не найден")
            return
        kind = SM.kind_of(path)
        if kind in ("Петли", "Вокал"):
            self.drop_item(f"echoes:track:{path}", self.song_beat(), None)
            return
        name = os.path.splitext(os.path.basename(path))[0]
        prm = {"sample": path}
        if kind in ("808", "Бас", "Мелодии"):
            rn = SM.root_note(path)
            if rn is not None:
                prm["root"] = int(max(24, min(96, rn)))
            prm["hold"] = 0
            prm["rel"] = 0.06
        else:
            prm["keytrack"] = 1
        c = self.add_channel("sampler", prm, name)
        if kind in ("808", "Бас", "Мелодии") and "root" in prm:
            self.hint(f"«{name}»: нота сэмпла — {D.note_name(prm['root'])}, играйте её в пианоролле")
        return c

    def delete_channel(self, cid):
        c = PR.chan(self.p, cid)
        if c is None:
            return
        self.commit("Удалить канал")
        self.p["channels"] = [x for x in self.p["channels"] if x["id"] != cid]
        for pt in self.p["patterns"]:
            pt.get("notes", {}).pop(cid, None)
        self.p["clips"] = [k for k in self.p["clips"] if not (k["kind"] == "audio" and k.get("chan") == cid)]
        w = self.plugins.pop(f"gen:{cid}", None)
        if w is not None:
            w.hide()
            w.deleteLater()
        if self.p.get("sel_channel") == cid:
            self.p["sel_channel"] = self.p["channels"][0]["id"] if self.p["channels"] else None
        if self.roll.cid == cid:
            c2 = PR.cur_channel(self.p)
            self.roll.set_channel(c2["id"] if c2 and c2.get("gen") != "audio" else None)
        self.touch()

    def clone_channel(self, cid):
        c = PR.chan(self.p, cid)
        if c is None:
            return
        self.commit("Клон канала")
        n = json.loads(json.dumps(c))
        n["id"] = PR.uid()
        n["name"] = (c["name"] + " 2")[:30]
        n["insert"] = PR.free_insert(self.p) if c.get("insert") else 0
        n.pop("voice", None)
        i = self.p["channels"].index(c)
        self.p["channels"].insert(i + 1, n)
        for pt in self.p["patterns"]:
            ns = pt.get("notes", {}).get(cid)
            if ns:
                pt["notes"][n["id"]] = [list(x) for x in ns]
        self.touch()

    def replace_gen(self, cid, kind):
        c = PR.chan(self.p, cid)
        if c is None:
            return
        self.commit("Заменить инструмент")
        c["gen"] = kind
        c["params"] = {"sample": "kit:Бочка Punch"} if kind == "sampler" else {}
        w = self.plugins.pop(f"gen:{cid}", None)
        if w is not None:
            w.hide()
            w.deleteLater()
        self.touch()

    def apply_gen_preset(self, cid, prm, name):
        c = PR.chan(self.p, cid)
        if c is None:
            return
        self.commit("Пресет инструмента")
        cls = I.GENERATORS.get(c.get("gen"))
        keep = {k: v for k, v in c.get("params", {}).items() if k == "sample"}
        c["params"] = {**({s.key: s.default for s in cls.PARAMS} if cls else {}), **keep, **dict(prm)}
        if cls and (c["name"] in cls.PRESETS or c["name"] == cls.NAME):
            c["name"] = name
        self.touch(views=("rack", "plugins"))
        self.preview_channel(c)

    def set_sample(self, cid, ref, name):
        c = PR.chan(self.p, cid)
        if c is None:
            return
        self.commit("Звук сэмплера")
        old = str(c.get("params", {}).get("sample") or "")
        oldname = old[4:] if old.startswith("kit:") else os.path.splitext(os.path.basename(old))[0][:30]
        c.setdefault("params", {})["sample"] = ref
        if c["name"] in (oldname, "Канал", "Сэмплер") or old == "":
            c["name"] = str(name)[:30]
        self.touch(views=("rack", "plugins"))
        self.preview_channel(c)

    # ── паттерны ── #
    def select_pattern(self, pid, quiet=False):
        if PR.pattern(self.p, pid) is None:
            return
        self.p["sel_pattern"] = pid
        if self.p.get("mode") == "pat":
            self._rebuild()
        self.rack.refresh()
        self.roll.on_pattern()
        if not quiet:
            self.playlist.place = ("pat", pid)
            self.playlist.refresh_place()

    def new_pattern(self):
        self.commit("Новый паттерн")
        pt = PR.new_pattern(f"Паттерн {len(self.p['patterns']) + 1}")
        self.p["patterns"].append(pt)
        self.p["sel_pattern"] = pt["id"]
        self.playlist.place = ("pat", pt["id"])
        self.touch()
        self.hint(f"Создан {pt['name']}")

    def clone_pattern(self):
        cur = PR.cur_pattern(self.p)
        if cur is None:
            return
        self.commit("Копия паттерна")
        pt = json.loads(json.dumps(cur))
        pt["id"] = PR.uid()
        pt["name"] = f"{cur['name']} (копия)"
        self.p["patterns"].insert(self.p["patterns"].index(cur) + 1, pt)
        self.p["sel_pattern"] = pt["id"]
        self.playlist.place = ("pat", pt["id"])
        self.touch()

    def rename_pattern(self):
        cur = PR.cur_pattern(self.p)
        if cur is None:
            return
        t, ok = QInputDialog.getText(self, "Паттерн", "Название паттерна:", text=cur["name"])
        if ok and t.strip():
            self.commit("Название паттерна")
            cur["name"] = t.strip()[:40]
            self.touch(rebuild=False)

    def delete_pattern(self):
        cur = PR.cur_pattern(self.p)
        if cur is None or len(self.p["patterns"]) <= 1:
            self.hint("Последний паттерн удалить нельзя")
            return
        self.commit("Удалить паттерн")
        self.p["patterns"].remove(cur)
        self.p["clips"] = [k for k in self.p["clips"] if not (k["kind"] == "pat" and k["ref"] == cur["id"])]
        self.p["sel_pattern"] = self.p["patterns"][0]["id"]
        self.playlist.place = None
        self.touch()

    def step_pattern(self, d):
        pats = self.p["patterns"]
        cur = PR.cur_pattern(self.p)
        if not pats or cur is None:
            return
        i = (pats.index(cur) + d) % len(pats)
        self.select_pattern(pats[i]["id"])
        self.hint(f"Паттерн: {pats[i]['name']}")

    # ── микшер ── #
    def add_fx_to_selected(self, t):
        i = self.mixer_view.cur
        ins = self.p["mixer"][i]
        s = next((k for k, x in enumerate(ins["fx"]) if x is None), None)
        if s is None:
            self.hint("На дорожке нет свободных ячеек")
            return
        self.commit("Эффект")
        ins["fx"][s] = FX.new_slot(t)
        self.touch(rebuild=False, fx=True, views=("mixer",))
        self.show_window("mixer")
        self.open_fx_plugin(i, s)

    def apply_voice_preset(self, i, name):
        chain = FX.voice_chain(name)
        if not chain:
            return
        self.commit("Пресет голоса")
        for s in range(10):
            self.close_fx_plugin(i, s)
        self.p["mixer"][i]["fx"] = chain + [None] * (10 - len(chain))
        self.touch(rebuild=False, fx=True, views=("mixer",))
        self.show_window("mixer")
        self.mixer_view.select(i)
        self.hint(f"Пресет голоса «{name}» на {'мастере' if i == 0 else 'дорожке ' + str(i)}")

    # ── перетаскивание ── #
    def drop_item(self, text, beat, lane):
        if not text.startswith("echoes:"):
            return
        _e, kind, val = text.split(":", 2)
        if kind in ("track", "rec"):
            self._add_audio_file(val, beat, lane)
        elif kind == "source":
            src = PR.source(self.p, val)
            if src is None:
                return
            k0 = next((k for k in self.p["clips"] if k["kind"] == "audio" and k["ref"] == val), None)
            if k0 is None:
                return
            self.commit("Аудиоклип")
            k = dict(k0, id=PR.uid(), start=round(beat, 4))
            k["lane"] = PR.free_lane(self.p, beat, k["len"]) if lane is None else int(lane)
            self.p["clips"].append(k)
            self.set_mode("song", quiet=True)
            self.touch(views=("playlist",))
        elif kind == "pattern":
            pt = PR.pattern(self.p, val)
            if pt is None:
                return
            self.commit("Клип паттерна")
            ln = PR.pattern_used_len(pt)
            self.p["clips"].append({"id": PR.uid(), "kind": "pat", "ref": val,
                                    "lane": PR.free_lane(self.p, beat, ln) if lane is None else int(lane),
                                    "start": round(beat, 4), "len": ln})
            self.set_mode("song", quiet=True)
            self.touch(views=("playlist",))
        elif kind == "kit":
            self.add_channel("sampler", {"sample": "kit:" + val}, val)
        elif kind == "sample":
            self.add_sample(val)
        elif kind == "inst":
            g, nm = val.split("|", 1)
            cls = I.GENERATORS.get(g)
            if cls:
                self.add_channel(g, dict(cls.PRESETS.get(nm, {})), nm or cls.NAME)
        elif kind == "fx":
            self.add_fx_to_selected(val)
        elif kind == "voice":
            self.apply_voice_preset(self.mixer_view.cur, val)

    def _add_audio_file(self, path, beat, lane):
        if not os.path.exists(path):
            self.hint("Файл не найден")
            return
        import daw_mashup as M
        tr = next((t for t in self.library() if t.get("path") == path), None)
        name = M.track_title(tr) if tr else os.path.splitext(os.path.basename(path))[0]
        self.commit("Аудиоклип")
        src = next((s for s in self.p["sources"] if s.get("path") == path and s.get("stem", "mix") == "mix"
                    and not s.get("warp") and not s.get("trim")), None)
        if src is None:
            src = PR.new_source(name[:60], path, "mix")
            self.p["sources"].append(src)
        ch = next((c for c in self.p["channels"] if c.get("gen") == "audio" and c.get("src") == src["id"]), None)
        if ch is None:
            ins = PR.free_insert(self.p)
            ch = PR.new_channel(name[:30], "audio", insert=ins,
                                color=I.DEFAULT_COLORS[len(self.p["channels"]) % len(I.DEFAULT_COLORS)])
            ch["src"] = src["id"]
            self.p["channels"].append(ch)
            if ins:
                self.p["mixer"][ins]["name"] = name[:24]
        dur = float((tr or {}).get("duration") or 0)
        if dur <= 0:
            try:
                import mutagen
                mf = mutagen.File(path)
                dur = float(mf.info.length) if mf is not None else 0.0
            except Exception:                            # noqa: BLE001
                dur = 0.0
        ln = max(4.0, dur * float(self.p["bpm"]) / 60.0) if dur > 0 else 64.0
        ln = round(ln, 4)
        if lane is None:
            lane = PR.free_lane(self.p, beat, ln)
        self.p["clips"].append({"id": PR.uid(), "kind": "audio", "ref": src["id"], "chan": ch["id"], "lane": int(lane),
                                "start": round(beat, 4), "len": ln, "off": 0.0, "gain": 1.0, "fin": 0.0, "fout": 0.0})
        self.set_mode("song", quiet=True)
        self.touch()
        self.show_window("playlist")
        self.hint(f"«{name}» — аудиоклип на дорожке {int(lane) + 1}")

    # ── свойства аудиоклипа ── #
    def clip_props(self, k):
        src = PR.source(self.p, k["ref"])
        if src is None:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Аудиоклип")
        dlg.setStyleSheet(U.QSS + "QDialog { background: #2a3035; }")
        v = QVBoxLayout(dlg)
        v.addWidget(U.label(src["name"], 13, True, U.TEXT))
        rows = []

        def spin(lbl, lo, hi, val, step, dec, suffix):
            h = QHBoxLayout()
            h.addWidget(U.label(lbl, 12, False, U.TEXT), 1)
            s = QDoubleSpinBox()
            s.setRange(lo, hi)
            s.setDecimals(dec)
            s.setSingleStep(step)
            s.setValue(val)
            s.setSuffix(suffix)
            h.addWidget(s)
            v.addLayout(h)
            rows.append(s)
            return s
        g_db = 20 * np.log10(max(1e-4, float(k.get("gain", 1.0))))
        sg = spin("Громкость", -36, 12, round(float(g_db), 1), 0.5, 1, " дБ")
        sfi = spin("Нарастание в начале", 0, 64, float(k.get("fin") or 0), 0.25, 2, " дол.")
        sfo = spin("Затухание в конце", 0, 64, float(k.get("fout") or 0), 0.25, 2, " дол.")
        sp = spin("Сдвиг тона", -12, 12, float(k.get("semis") or 0), 1, 0, " пт")
        st = QCheckBox("Подгонять под темп проекта (растяжение без смены тона)")
        st.setChecked(bool(k.get("stretch")))
        v.addWidget(st)
        sb = spin("Темп самого звука", 40, 240, float(src.get("bpm") or self.p["bpm"]), 0.1, 2, " BPM")
        det = QPushButton("Определить темп звука")
        v.addWidget(det)

        def detect():
            det.setEnabled(False)
            det.setText("Слушаю…")
            QApplication.processEvents()
            try:
                import daw_mashup as M
                an = M.analyze(src["path"])
                sb.setValue(float(an["bpm"]))
                det.setText(f"Темп: {an['bpm']:.2f} BPM")
            except Exception as e:                       # noqa: BLE001
                det.setText(f"Не вышло: {e}")
            det.setEnabled(True)
        det.clicked.connect(detect)
        rev = QCheckBox("Задом наперёд")
        rev.setChecked(bool(src.get("rev")))
        v.addWidget(rev)
        hb = QHBoxLayout()
        hb.addStretch(1)
        ok = QPushButton("Готово")
        ok.clicked.connect(dlg.accept)
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(dlg.reject)
        hb.addWidget(cancel)
        hb.addWidget(ok)
        v.addLayout(hb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self.commit("Свойства клипа")
        k["gain"] = round(float(10 ** (sg.value() / 20)), 4)
        k["fin"] = round(sfi.value(), 3)
        k["fout"] = round(sfo.value(), 3)
        k["semis"] = round(sp.value(), 2)
        k["stretch"] = bool(st.isChecked())
        if st.isChecked():
            src["bpm"] = round(sb.value(), 3)
        if bool(src.get("rev")) != rev.isChecked():
            src["rev"] = rev.isChecked()
        self.playlist.canvas.cache.clear()
        self.touch(views=("playlist",))

    # ── проекты ── #
    def _project_file(self):
        nm = self.p.get("name") or "Без названия"
        if nm == "Без названия":
            nm = "Автосохранение"
        return PR.project_path(nm)

    def _autosave(self, force=False):
        if not (self.modified or force):
            return
        try:
            path = PR.save(self.p, self._project_file())
            self.S()["last_project"] = str(path)
            self.save_settings()
            self.modified = False
            self._last_save = time.time()
            self._update_title()
        except Exception as e:                           # noqa: BLE001
            print("[studio] autosave:", e)

    def save_project(self, as_new=False):
        if as_new or (self.p.get("name") or "Без названия") == "Без названия":
            t, ok = QInputDialog.getText(self, "Сохранить проект", "Название проекта:",
                                         text="" if self.p.get("name") == "Без названия" else self.p.get("name", ""))
            if not ok or not t.strip():
                return
            self.p["name"] = t.strip()[:80]
        path = PR.save(self.p, self._project_file())
        self.S()["last_project"] = str(path)
        self.save_settings()
        self.modified = False
        self._update_title()
        self.browser.refresh_files()
        self.hint(f"Проект сохранён: {path.name}")

    def open_project(self, path=None):
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, "Открыть проект", str(PR.PROJECTS), "Проект студии (*.echoproj)")
            if not path:
                return
        try:
            newp = PR.load(path)
        except Exception as e:                           # noqa: BLE001
            self.hint(f"Не открыть: {e}")
            return
        self._autosave()
        self.stop()
        self.replace_project(newp)
        self.modified = False
        self.S()["last_project"] = str(path)
        self.save_settings()
        self.hint(f"Открыт проект «{self.p.get('name')}»")
        self._update_title()

    def new_project(self, template="drums"):
        self._autosave()
        self.stop()
        self.replace_project(PR.new_project(template=template))
        self.modified = False
        self.hint("Новый проект")
        self._maybe_start()

    def load_project(self, p, mashup=False):
        self._autosave()
        self.stop()
        self._song_pos = 0.0
        self.replace_project(p)
        self.modified = True
        if mashup:
            self.p["mode"] = "song"
            self._rebuild()
            self.top.mode.update()
            self.show_window("playlist")
            self.wins["playlist"].lower()
            self.playlist.canvas.sx = 0
            self.playlist.canvas.ppb = max(4.0, min(24.0, (self.workspace.width() - 200) /
                                                   max(16.0, PR.song_beats(self.p))))
            self.playlist.canvas.sync_bars()
            self._autosave(force=True)

    def import_audio(self):
        f, _ = QFileDialog.getOpenFileName(self, "Импорт звука", "", "Звук (*.mp3 *.wav *.flac *.ogg *.m4a *.opus *.aiff)")
        if f:
            self._add_audio_file(f, self.song_beat(), None)

    # ── экспорт ── #
    def export_dialog(self, to_library=False):
        dlg = QDialog(self)
        dlg.setWindowTitle("Экспорт")
        dlg.setStyleSheet(U.QSS + "QDialog { background: #2a3035; }")
        v = QVBoxLayout(dlg)
        v.addWidget(U.label("Сохранить в файл", 13, True, U.TEXT))
        h = QHBoxLayout()
        h.addWidget(U.label("Название", 12, False, U.TEXT))
        name = QLineEdit(self.p.get("name") or "Мой трек")
        h.addWidget(name, 1)
        v.addLayout(h)
        h2 = QHBoxLayout()
        h2.addWidget(U.label("Формат", 12, False, U.TEXT))
        fmt = QComboBox()
        fmt.addItem("MP3 320 кбит/с", "mp3")
        fmt.addItem("WAV 16 бит (без сжатия)", "wav")
        fmt.addItem("FLAC (без потерь)", "flac")
        h2.addWidget(fmt, 1)
        v.addLayout(h2)
        what = QComboBox()
        what.addItem("Вся песня (плейлист)", "song")
        what.addItem("Только текущий паттерн", "pat")
        if not self.p["clips"]:
            what.setCurrentIndex(1)
        v.addWidget(what)
        lib = QCheckBox("Добавить в библиотеку плеера")
        lib.setChecked(True if to_library else bool(self.S().get("export_to_lib", True)))
        v.addWidget(lib)
        bar = QProgressBar()
        bar.setRange(0, 1000)
        bar.hide()
        v.addWidget(bar)
        status = U.label("", 11)
        status.setWordWrap(True)
        v.addWidget(status)
        hb = QHBoxLayout()
        hb.addStretch(1)
        cancel = QPushButton("Закрыть")
        go = QPushButton("Экспорт")
        hb.addWidget(cancel)
        hb.addWidget(go)
        v.addLayout(hb)
        stop = threading.Event()
        state = {"busy": False}

        def run():
            if state["busy"]:
                return
            ext = fmt.currentData()
            PR.EXPORTS.mkdir(parents=True, exist_ok=True)
            out = PR.EXPORTS / f"{PR.safe_name(name.text() or 'Мой трек')}.{ext}"
            i = 2
            while out.exists():
                out = PR.EXPORTS / f"{PR.safe_name(name.text() or 'Мой трек')} ({i}).{ext}"
                i += 1
            state["busy"] = True
            go.setEnabled(False)
            bar.show()
            snap = json.loads(json.dumps(self.p))
            mode = what.currentData()
            add = lib.isChecked()
            self.S()["export_to_lib"] = add

            def job():
                try:
                    y = E.render_song(snap, self.renderer, mode, lambda f: self.br.exported.emit(("p", f)), stop)
                    self.br.exported.emit(("p", 0.98))
                    D.encode(y, out, fmt=ext)
                    self.br.exported.emit(("ok", str(out), add, len(y) / D.SR))
                except Exception as e:                   # noqa: BLE001
                    self.br.exported.emit(("err", str(e)))
            threading.Thread(target=job, daemon=True, name="studio-export").start()

        def on_ev(ev):
            try:
                if ev[0] == "p":
                    bar.setValue(int(ev[1] * 1000))
                    status.setText(f"Свожу… {int(ev[1] * 100)}%")
                elif ev[0] == "ok":
                    state["busy"] = False
                    bar.setValue(1000)
                    path, add, secs = ev[1], ev[2], ev[3]
                    msg = f"Готово: {os.path.basename(path)} ({int(secs // 60)}:{int(secs % 60):02d})"
                    if add:
                        self._add_to_library(path)
                        msg += " — добавлен в библиотеку плеера"
                    status.setText(msg + f"\nПапка: {os.path.dirname(path)}")
                    go.setEnabled(True)
                    self.hint(msg)
                elif ev[0] == "err":
                    state["busy"] = False
                    status.setText("Не получилось: " + ev[1])
                    go.setEnabled(True)
            except RuntimeError:
                pass
        self.br.exported.connect(on_ev)
        go.clicked.connect(run)
        cancel.clicked.connect(dlg.reject)
        dlg.exec()
        stop.set()
        try:
            self.br.exported.disconnect(on_ev)
        except Exception:                                # noqa: BLE001
            pass

    def _add_to_library(self, path):
        try:
            self.win._register_track(str(path))
            self.win._save_library()
            self.win._refresh_library_view()
        except Exception as e:                           # noqa: BLE001
            print("[studio] library:", e)
        try:
            self.browser.refresh_library()
            self.bot.refresh()
        except RuntimeError:
            pass

    # ── параметры ── #
    def audio_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Звук студии")
        dlg.setStyleSheet(U.QSS + "QDialog { background: #2a3035; }")
        v = QVBoxLayout(dlg)
        outs, ins = [("По умолчанию системы", None)], [("По умолчанию системы", None)]
        try:
            import sounddevice as sd
            apis = sd.query_hostapis()
            for i, d in enumerate(sd.query_devices()):
                api = apis[d["hostapi"]]["name"] if d["hostapi"] < len(apis) else ""
                if d["max_output_channels"] > 0:
                    outs.append((f"{d['name']} ({api})", i))
                if d["max_input_channels"] > 0:
                    ins.append((f"{d['name']} ({api})", i))
        except Exception:                                # noqa: BLE001
            pass
        v.addWidget(U.label("Вывод (колонки / наушники)", 12, True, U.TEXT))
        oc = QComboBox()
        for nm, i in outs:
            oc.addItem(nm, i)
        oc.setCurrentIndex(max(0, oc.findData(self.S().get("out_device"))))
        v.addWidget(oc)
        v.addWidget(U.label("Микрофон для записи", 12, True, U.TEXT))
        ic = QComboBox()
        for nm, i in ins:
            ic.addItem(nm, i)
        ic.setCurrentIndex(max(0, ic.findData(self.S().get("in_device"))))
        v.addWidget(ic)
        v.addWidget(U.label(f"Задержка вывода: {self.eng.out_latency * 1000:.0f} мс. Запись сдвигается на задержку "
                            f"автоматически.", 11))
        hb = QHBoxLayout()
        hb.addStretch(1)
        ok = QPushButton("Применить")
        ok.clicked.connect(dlg.accept)
        hb.addWidget(ok)
        v.addLayout(hb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        od, idv = oc.currentData(), ic.currentData()
        if od != self.S().get("out_device"):
            self.S()["out_device"] = od
            self.eng.reopen(od)
        if idv != self.S().get("in_device"):
            self.S()["in_device"] = idv
            if self.eng.rec is not None:
                self.eng.stop_rec()
                self.top.btn_rec.setChecked(False)
        self.save_settings()
        self.hint("Звук студии настроен")

    def help_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Echoes Studio — справка")
        dlg.setStyleSheet(U.QSS + "QDialog { background: #262b30; } QLabel { color: #d5dce1; }")
        v = QVBoxLayout(dlg)
        lab = QLabel(HELP_HTML)
        lab.setWordWrap(True)
        lab.setTextFormat(Qt.TextFormat.RichText)
        from PyQt6.QtWidgets import QScrollArea
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QScrollArea.Shape.NoFrame)
        inner = QWidget()
        iv = QVBoxLayout(inner)
        iv.addWidget(lab)
        iv.addStretch(1)
        sc.setWidget(inner)
        v.addWidget(sc, 1)
        b = U.IconButton("close", "Закрыть справку", 28, text="Понятно")
        b.clicked.connect(dlg.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(b)
        v.addLayout(row)
        dlg.resize(min(720, self.width() - 40), min(640, self.height() - 40))
        dlg.exec()

    # ── меню ── #
    def _menu(self):
        m = QMenu(self)
        m.setStyleSheet(U.QSS)
        return m

    def menu_file(self, gp):
        m = self._menu()
        m.addAction("Новый проект (Ctrl+N)", lambda: self.new_project("drums"))
        m.addAction("Новый пустой проект", lambda: self.new_project("none"))
        m.addAction("Открыть… (Ctrl+O)", lambda: self.open_project())
        rec = m.addMenu("Последние")
        for f in PR.list_projects()[:12]:
            rec.addAction(f.stem, lambda f=f: self.open_project(str(f)))
        m.addSeparator()
        m.addAction("Сохранить (Ctrl+S)", lambda: self.save_project())
        m.addAction("Сохранить как… (Ctrl+Shift+S)", lambda: self.save_project(as_new=True))
        m.addSeparator()
        m.addAction("Экспорт в MP3 / WAV / FLAC… (Ctrl+R)", lambda: self.export_dialog())
        m.addAction("Импорт звука в плейлист…", self.import_audio)
        m.addAction("Открыть папку студии", lambda: self._open_folder(PR.STUDIO))
        m.addSeparator()
        m.addAction("Выйти из студии (тема Echoes Music)", self.leave_theme)
        m.exec(gp)

    def _open_folder(self, path):
        try:
            Path(path).mkdir(parents=True, exist_ok=True)
            os.startfile(str(path))                      # noqa: S606
        except Exception as e:                           # noqa: BLE001
            self.hint(f"Не открыть папку: {e}")

    def menu_edit(self, gp):
        m = self._menu()
        a = m.addAction("Отменить (Ctrl+Z)" + (f": {self.hist.undo[-1][0]}" if self.hist.undo else ""), self.undo)
        a.setEnabled(bool(self.hist.undo))
        a = m.addAction("Повторить (Ctrl+Y)" + (f": {self.hist.redo[-1][0]}" if self.hist.redo else ""), self.redo)
        a.setEnabled(bool(self.hist.redo))
        m.addSeparator()
        m.addAction(f"Темп проекта… ({self.p['bpm']:.2f})", self._ask_bpm)
        m.addAction("Переименовать проект…", self._rename_project)
        m.exec(gp)

    def _ask_bpm(self):
        v, ok = QInputDialog.getText(self, "Темп", "Темп проекта, BPM:", text=f"{self.p['bpm']:.2f}")
        if ok:
            try:
                self.commit("Темп")
                self.set_bpm(float(v.replace(",", ".")))
                self.top.tempo.update()
            except ValueError:
                pass

    def _rename_project(self):
        t, ok = QInputDialog.getText(self, "Проект", "Название проекта:", text=self.p.get("name", ""))
        if ok and t.strip():
            self.p["name"] = t.strip()[:80]
            self.modified = True
            self._update_title()

    def menu_add(self, gp):
        self.rack.add_menu(gp)

    def plugin_picker(self):
        from studio_picker import Picker
        dialog=Picker(self);dialog.exec();dialog.deleteLater()

    def menu_patterns(self, gp):
        m = self._menu()
        m.addAction("Новый паттерн", self.new_pattern)
        m.addAction("Копия текущего", self.clone_pattern)
        m.addAction("Переименовать…", self.rename_pattern)
        m.addAction("Удалить текущий", self.delete_pattern)
        m.addSeparator()
        cur = PR.cur_pattern(self.p)
        for pt in self.p["patterns"]:
            m.addAction(("•  " if cur and pt["id"] == cur["id"] else "    ") + pt["name"],
                        lambda pid=pt["id"]: self.select_pattern(pid))
        m.exec(gp)

    def menu_view(self, gp):
        m = self._menu()
        for key, nm in (("playlist", "Плейлист (F5)"), ("rack", "Стойка каналов (F6)"), ("roll", "Пианоролл (F7)"),
                        ("browser", "Браузер (Alt+F8)"), ("mixer", "Микшер (F9)"), ("bot", "Мэшап-бот (F10)")):
            m.addAction(nm, lambda key=key: self.show_window(key))
        m.addSeparator()
        m.addAction("Расставить окна заново", lambda: self.apply_layout(reset=True))
        for title,key in (("Аранжировка","arrange"),("Создание бита","beat"),("Ноты и аккорды","notes"),("Сведение","mix")):
            m.addAction("Рабочее место: "+title,lambda key=key:self.workspace_preset(key))
        m.addAction("Закрыть все окна плагинов", self._close_all_plugins)
        m.exec(gp)

    def menu_options(self, gp):
        m = self._menu()
        m.addAction("Звук: вывод и микрофон…", self.audio_dialog)
        a = m.addAction("Метроном (Ctrl+M)")
        a.setCheckable(True)
        a.setChecked(self.eng.metronome)
        a.toggled.connect(lambda on: self.top.btn_metro.setChecked(on))
        a = m.addAction("Клавиатура компьютера как пианино")
        a.setCheckable(True)
        a.setChecked(self.typing_keys)
        a.toggled.connect(self._typing)
        m.addSeparator()
        m.addAction("Справка по клавишам (F1)", self.help_dialog)
        m.exec(gp)

    def _typing(self, on):
        self.typing_keys = bool(on)
        self.hint("Клавиатура как пианино: Z S X D C V G B H N J M — нижняя октава, Q 2 W 3 E… — верхняя"
                  if on else "Клавиатура как пианино выключена")

    def menu_themes(self, gp):
        try:
            from themes import theme_groups
            groups = theme_groups()
        except Exception:                                # noqa: BLE001
            groups = []
        m = self._menu()
        cur = self.win.settings.get("theme", "")
        for gi, (title, _hint, names) in enumerate(groups):
            if gi:
                m.addSeparator()
            h = m.addAction(title)
            h.setEnabled(False)
            for n in names:
                m.addAction(("•  " if n == cur else "    ") + n,
                            lambda n=n: QTimer.singleShot(0, lambda: self.win._apply_theme(n)))
        m.addSeparator()
        m.addAction("Конструктор тем…", lambda: self.win._open_theme_studio())
        m.exec(gp)

    # ── клавиши ── #
    _PIANO = {Qt.Key.Key_Z: 0, Qt.Key.Key_S: 1, Qt.Key.Key_X: 2, Qt.Key.Key_D: 3, Qt.Key.Key_C: 4, Qt.Key.Key_V: 5,
              Qt.Key.Key_G: 6, Qt.Key.Key_B: 7, Qt.Key.Key_H: 8, Qt.Key.Key_N: 9, Qt.Key.Key_J: 10, Qt.Key.Key_M: 11,
              Qt.Key.Key_Comma: 12, Qt.Key.Key_Q: 12, Qt.Key.Key_2: 13, Qt.Key.Key_W: 14, Qt.Key.Key_3: 15,
              Qt.Key.Key_E: 16, Qt.Key.Key_R: 17, Qt.Key.Key_5: 18, Qt.Key.Key_T: 19, Qt.Key.Key_6: 20,
              Qt.Key.Key_Y: 21, Qt.Key.Key_7: 22, Qt.Key.Key_U: 23, Qt.Key.Key_I: 24}

    def keyPressEvent(self, e):
        k = e.key()
        mods = e.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        if e.isAutoRepeat() and k == Qt.Key.Key_Space:
            return
        if self.typing_keys and not ctrl and k in self._PIANO:
            c = PR.cur_channel(self.p)
            self.preview_channel(c, 48 + self._PIANO[k], 0.85, 1.0)
            return
        if k == Qt.Key.Key_Space and not ctrl:
            self.toggle_play()
        elif ctrl and k == Qt.Key.Key_Z:
            self.redo() if shift else self.undo()
        elif ctrl and k == Qt.Key.Key_Y:
            self.redo()
        elif ctrl and k == Qt.Key.Key_S:
            self.save_project(as_new=shift)
        elif ctrl and k == Qt.Key.Key_N:
            self.new_project()
        elif ctrl and k == Qt.Key.Key_O:
            self.open_project()
        elif ctrl and k == Qt.Key.Key_R:
            self.export_dialog()
        elif ctrl and k == Qt.Key.Key_M:
            self.top.btn_metro.setChecked(not self.top.btn_metro.isChecked())
        elif k == Qt.Key.Key_F1:
            self.help_dialog()
        elif k == Qt.Key.Key_F5:
            self.toggle_window("playlist")
        elif k == Qt.Key.Key_F6:
            self.toggle_window("rack")
        elif k == Qt.Key.Key_F7:
            self.toggle_window("roll")
        elif k == Qt.Key.Key_F8:
            if mods & Qt.KeyboardModifier.AltModifier:self.toggle_window("browser")
            else:self.plugin_picker()
        elif k == Qt.Key.Key_F9:
            self.toggle_window("mixer")
        elif k == Qt.Key.Key_F10:
            self.toggle_window("bot")
        elif k == Qt.Key.Key_L and not ctrl:
            self.set_mode("pat" if self.p.get("mode") == "song" else "song")
        elif k == Qt.Key.Key_R and not ctrl:
            self.top.btn_rec.setChecked(not self.top.btn_rec.isChecked())
            self.toggle_rec()
        elif k in (Qt.Key.Key_BracketRight, Qt.Key.Key_Plus):
            self.step_pattern(1)
        elif k in (Qt.Key.Key_BracketLeft, Qt.Key.Key_Minus):
            self.step_pattern(-1)
        elif k == Qt.Key.Key_Home:
            self.set_pos_beats(0)
        else:
            super().keyPressEvent(e)

    # ── кадр ── #
    def _tick(self):
        if self._closing or self.win.isMinimized():
            return
        now=time.perf_counter();dt=max(0.,min(.2,now-getattr(self,'_frame_last',now-1/30)));self._frame_last=now
        eng = self.eng
        sp = self.spb()
        if eng.playing:
            pos = eng.heard_pos()
            if self.p.get("mode") == "song":
                self._song_pos = pos / sp
        else:
            pos = eng.pos if self.p.get("mode") == "pat" else self._song_pos * sp
        b = pos / sp
        if self.time_fmt == "bars":
            bar = int(b // 4) + 1
            beat = int(b % 4) + 1
            tick = int((b % 1) * 96)
            self.top.time.set_text(f"{bar:3d}:{beat:02d}:{tick:02d}", "ТАКТ:ДОЛЯ:ТИК")
        else:
            s = pos / D.SR
            self.top.time.set_text(f"{int(s // 60)}:{int(s % 60):02d}:{int((s % 1) * 100):02d}", "МИН:СЕК:СОТЫЕ")
        pk = eng.mixer.peaks[0]
        self.top.meter.set(pk[0], pk[1])
        self.top.cpu.set_text(f"{min(999, int(eng.cpu * 100)):3d}%")
        rec = eng.rec
        if rec is not None:
            self.top.mic.set(rec.level, rec.level)
            rec.level *= np.exp(-3.16*dt)
        else:
            self.top.mic.set(0, 0)
        if eng.playing != self.top.btn_play.isChecked():
            self.top.btn_play.setChecked(eng.playing)
            self.top.btn_play.icon = "pause" if eng.playing else "play"
            self.top.btn_play.update()
        st = self.cur_step()
        if st != getattr(self, "_last_step", None):
            self._last_step = st
            self.rack.tick()
        self.roll.tick()
        if eng.playing and self.p.get("mode") == "song":
            self.playlist.scroll_to_beat(self._song_pos)
            self.playlist.tick()
        self.mixer_view.tick()
        for w in list(self.plugins.values()):
            try:
                if w.isVisible():
                    w.content.tick()
            except RuntimeError:
                pass
        if not self.top.scope.isHidden():
            m = getattr(eng, "last_out", None)
            if m is not None:
                self.top.scope.set_data(m)
        if time.time() - self._last_save > 120 and self.modified:
            self._autosave()
