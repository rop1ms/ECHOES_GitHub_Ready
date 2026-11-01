# net_playlist_ui.py
"""
Echoes.NET: плейлисты и треки из коллекции в личке и группах.

  * PlaylistCard   — плейлист внутри сообщения: маленький список, который листается колесом;
                     клик по треку — включить плейлист с него. «Слушать», «Вперемешку»,
                     «Сохранить себе». Трек, который есть в вашей коллекции, играет из файла,
                     остальные — потоком (как и всё в Echoes.NET).
  * TrackSearch    — найти трек в своей коллекции (а не только играющий) и отправить:
                     карточкой (друг включит у себя) или самим файлом.
  * PlaylistPicker — выбрать свой плейлист (или собрать из коллекции) и отправить.
"""
from __future__ import annotations

import random

from PyQt6.QtCore import Qt, QSize, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QAbstractItemView, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                             QListWidgetItem, QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from net_music import fmt_sec, library_match
from yamusic_theme import YELLOW, sanitize_title

HIDDEN = "⁣"                                   # служебные очереди плеера (онлайн, волна) — не плейлисты
PLAYLIST_MAX = 100

LIST_QSS = """
QListWidget { background: transparent; border: none; outline: 0; }
QListWidget::item { border-radius: 8px; padding: 0px 4px; }
QListWidget::item:hover { background: rgba(255,255,255,0.08); }
QListWidget::item:selected { background: rgba(255,255,255,0.14); }
QScrollBar:vertical { background: transparent; width: 6px; margin: 2px; }
QScrollBar::handle:vertical { background: rgba(255,255,255,0.28); border-radius: 3px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
"""


def _label(t) -> str:
    a = str(t.get("artist") or "").strip()
    n = sanitize_title(str(t.get("title") or "").strip())
    return f"{a} — {n}" if a and n else (n or a or "без названия")


def _meta(t) -> dict:
    """Трек коллекции → описание для сети (без путей к файлам)."""
    return {"artist": t.get("artist") or "", "title": sanitize_title(t.get("title") or ""),
            "album": t.get("album") or "", "duration": int(t.get("duration") or 0), "url": t.get("url") or ""}


def user_playlists(win) -> list:
    """[(название, треки)] — свои плейлисты, без служебных очередей."""
    out = []
    for name, pl in (getattr(win, "playlists", None) or {}).items():
        if str(name).startswith(HIDDEN) or not isinstance(pl, dict):
            continue
        tr = [t for t in pl.get("tracks") or [] if isinstance(t, dict)]
        if tr:
            out.append((str(name), tr))
    return out


# ══════════════════════════ плейлист в сообщении ══════════════════════════ #

class PlaylistCard(QFrame):
    ROW_H = 30
    ROWS = 5

    def __init__(self, page, ex: dict, out: bool, max_w=400, author="", author_name=""):
        super().__init__()
        self.page = page
        self.author, self.author_name = author, author_name     # кто прислал — для «Сохранить себе»
        self.tracks = list(ex.get("tracks") or [])
        self.name = str(ex.get("title") or "Плейлист")
        ink = "#111" if out else "#f2f2f2"
        sub = "rgba(0,0,0,0.6)" if out else "#9a9aa2"
        self.setObjectName("NetPlCard")
        self.setMaximumWidth(max_w)
        self.setStyleSheet("QFrame#NetPlCard{background:%s;border-radius:12px;}"
                           % ("rgba(0,0,0,0.10)" if out else "rgba(255,255,255,0.05)"))
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(10)
        art = QLabel("♫")
        art.setFixedSize(44, 44)
        art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        c1 = QColor.fromHsvF((hash(self.name) % 360) / 360.0, 0.55, 0.95).name()
        art.setStyleSheet(f"background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {c1},stop:1 #2a2a2e);"
                          "border-radius:9px;font-size:20px;font-weight:900;color:#fff;")
        top.addWidget(art)
        col = QVBoxLayout()
        col.setSpacing(0)
        h = QLabel("ПЛЕЙЛИСТ")
        h.setStyleSheet(f"font-size:10px;font-weight:900;letter-spacing:1px;color:{'rgba(0,0,0,0.6)' if out else YELLOW};")
        col.addWidget(h)
        t = QLabel()
        t.setStyleSheet(f"font-size:15px;font-weight:900;color:{ink};")
        t.setText(t.fontMetrics().elidedText(self.name, Qt.TextElideMode.ElideRight, max_w - 110))
        t.setToolTip(self.name)
        col.addWidget(t)
        total = sum(int(x.get("duration") or 0) for x in self.tracks)
        have = sum(1 for x in self.tracks if library_match(page.win, x)[1] is not None)
        info = f"{len(self.tracks)} тр." + (f" · {total // 60} мин" if total else "")
        if have and not out:
            info += f" · {have} есть у вас"
        s = QLabel(info)
        s.setStyleSheet(f"font-size:11px;color:{sub};")
        col.addWidget(s)
        top.addLayout(col, 1)
        v.addLayout(top)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        self.list = QListWidget()
        self.list.setStyleSheet(LIST_QSS + f"QListWidget{{color:{ink};font-size:12px;}}")
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setUniformItemSizes(True)
        self.list.setCursor(Qt.CursorShape.PointingHandCursor)
        self.list.setToolTip("Нажмите на трек — плейлист включится с него")
        for i, x in enumerate(self.tracks):
            d = int(x.get("duration") or 0)
            it = QListWidgetItem(f"{i + 1:>2}.  {_label(x)}" + (f"   {fmt_sec(d)}" if d else ""))
            it.setSizeHint(QSize(0, self.ROW_H))
            it.setToolTip(_label(x))
            self.list.addItem(it)
        self.list.setFixedHeight(self.ROW_H * min(self.ROWS, max(1, len(self.tracks))) + 4)
        self.list.itemClicked.connect(lambda it: self.play(self.list.row(it)))
        v.addWidget(self.list)

        row = QHBoxLayout()
        row.setSpacing(6)
        for text, fn in (("▶  Слушать", lambda: self.play(0)), ("Вперемешку", self.shuffle),
                         ("Сохранить себе", self.save)):
            b = QPushButton(text)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedHeight(30)
            b.setObjectName("AccentBtn" if text.startswith("▶") else "GhostBtn")   # стеклянный стиль плеера
            b.clicked.connect(lambda _=False, fn=fn: fn())
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)

    def play(self, start=0):
        self.page.play_many(self.tracks, self.name, start=start)

    def shuffle(self):
        tr = list(self.tracks)
        random.shuffle(tr)
        self.page.play_many(tr, self.name + " · вперемешку")

    def save(self):
        self.page._save_playlist({"title": self.name, "author": self.author, "name": self.author_name}, self.tracks)


# ══════════════════════════ поиск трека в коллекции ══════════════════════════ #

DIALOG_QSS = """
QDialog { background: #161616; }
QLabel { color: #f2f2f2; }
QLineEdit { background: #232326; color: #f2f2f2; border: 1px solid #333; border-radius: 12px; padding: 8px 12px;
            font-size: 14px; }
QListWidget { background: #1c1c1f; color: #f2f2f2; border: none; border-radius: 12px; outline: 0; font-size: 13px; }
QListWidget::item { padding: 6px 8px; border-radius: 8px; }
QListWidget::item:hover { background: #26262a; }
QListWidget::item:selected { background: #34343a; }
QPushButton { background: #2a2a2e; color: #f2f2f2; border: none; border-radius: 16px; padding: 0 16px;
              min-height: 32px; font-weight: 800; }
QPushButton:hover { background: #36363b; }
QPushButton:disabled { color: #6c6c72; }
QPushButton#Accent { background: #ffdb4d; color: #111; }
QPushButton#Accent:hover { background: #ffe766; }
QPushButton#Accent:disabled { background: #4a4632; color: #8a8672; }
QScrollBar:vertical { background: transparent; width: 8px; }
QScrollBar::handle:vertical { background: #444; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""

SHOW_MAX = 400                                       # строк в списке за раз — поиск сужает


class TrackSearch(QDialog):
    """Трек(и) из своей коллекции. multi=True — с галочками (собрать плейлист).
    После exec(): self.result_tracks — выбранные треки, self.as_file — отправить файлом."""

    def __init__(self, win, parent=None, multi=False, title="Трек из коллекции"):
        super().__init__(parent)
        self.win = win
        self.multi = multi
        self.as_file = False
        self.result_tracks = []
        self._checked = {}                                 # путь → трек (галочки не теряются при поиске)
        self.setWindowTitle(title)
        self.setMinimumSize(520, 540)
        self.setStyleSheet(DIALOG_QSS)
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(10)
        h = QLabel(title)
        h.setStyleSheet("font-size:20px;font-weight:900;")
        v.addWidget(h)
        self.q = QLineEdit()
        self.q.setPlaceholderText("Поиск: исполнитель, название, альбом…")
        self.q.setClearButtonEnabled(True)
        v.addWidget(self.q)
        self.list = QListWidget()
        self.list.setUniformItemSizes(True)
        v.addWidget(self.list, 1)
        self.count = QLabel("")
        self.count.setStyleSheet("color:#8a8a8a;font-size:12px;")
        v.addWidget(self.count)
        row = QHBoxLayout()
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        row.addStretch(1)
        if multi:
            self.ok = QPushButton("Готово")
            self.ok.setObjectName("Accent")
            self.ok.clicked.connect(self._done)
            row.addWidget(self.ok)
        else:
            self.file_btn = QPushButton("Отправить файлом")
            self.file_btn.setToolTip("Сам аудиофайл — друг скачает его к себе")
            self.file_btn.clicked.connect(lambda: self._done(as_file=True))
            row.addWidget(self.file_btn)
            self.ok = QPushButton("Отправить")
            self.ok.setObjectName("Accent")
            self.ok.setToolTip("Карточка трека — друг включит его у себя (из коллекции или потоком)")
            self.ok.clicked.connect(self._done)
            row.addWidget(self.ok)
        v.addLayout(row)

        self._lib = [t for t in (getattr(win, "library", None) or []) if isinstance(t, dict)]
        self._keys = [(" ".join(str(t.get(k) or "") for k in ("artist", "title", "album"))).casefold()
                      for t in self._lib]
        self._deb = QTimer(self)
        self._deb.setSingleShot(True)
        self._deb.setInterval(140)
        self._deb.timeout.connect(self._fill)
        self.q.textChanged.connect(lambda _: self._deb.start())
        self.list.itemDoubleClicked.connect(lambda _: None if multi else self._done())
        self.list.itemChanged.connect(self._on_check)
        self.list.currentRowChanged.connect(lambda _: self._sync())
        self._fill()
        self.q.setFocus()

    def _fill(self):
        words = self.q.text().casefold().split()
        self.list.blockSignals(True)
        self.list.clear()
        n = 0
        for t, key in zip(self._lib, self._keys):
            if words and not all(w in key for w in words):
                continue
            n += 1
            if n > SHOW_MAX:
                continue
            d = int(t.get("duration") or 0)
            it = QListWidgetItem(_label(t) + (f"   ·  {fmt_sec(d)}" if d else ""))
            it.setData(Qt.ItemDataRole.UserRole, t)
            if self.multi:
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(Qt.CheckState.Checked if str(t.get("path")) in self._checked
                                 else Qt.CheckState.Unchecked)
            self.list.addItem(it)
        self.list.blockSignals(False)
        if self.list.count() and not self.multi:
            self.list.setCurrentRow(0)
        more = f" (показаны первые {SHOW_MAX} — уточните поиск)" if n > SHOW_MAX else ""
        self.count.setText(f"Найдено: {n}{more}" if words else f"В коллекции: {n}{more}")
        self._sync()

    def _on_check(self, it):
        t = it.data(Qt.ItemDataRole.UserRole)
        if not t:
            return
        p = str(t.get("path"))
        if it.checkState() == Qt.CheckState.Checked:
            if len(self._checked) >= PLAYLIST_MAX:
                self.list.blockSignals(True)
                it.setCheckState(Qt.CheckState.Unchecked)
                self.list.blockSignals(False)
                self.count.setText(f"Не больше {PLAYLIST_MAX} треков в одном плейлисте")
                return
            self._checked[p] = t
        else:
            self._checked.pop(p, None)
        self._sync()

    def _sync(self):
        if self.multi:
            self.ok.setText(f"Готово · {len(self._checked)}" if self._checked else "Готово")
            self.ok.setEnabled(bool(self._checked))
        else:
            it = self.list.currentItem()
            t = it.data(Qt.ItemDataRole.UserRole) if it is not None else None
            self.ok.setEnabled(t is not None)
            p = str((t or {}).get("path") or "")
            self.file_btn.setEnabled(bool(p) and not p.startswith("stream:"))

    def _done(self, as_file=False):
        if self.multi:
            self.result_tracks = list(self._checked.values())
        else:
            it = self.list.currentItem()
            t = it.data(Qt.ItemDataRole.UserRole) if it is not None else None
            if t is None:
                return
            self.result_tracks = [t]
        self.as_file = bool(as_file)
        self.accept()


# ══════════════════════════ выбор плейлиста ══════════════════════════ #

class PlaylistPicker(QDialog):
    """Свой плейлист → (название, треки) в self.result. Или собрать из коллекции."""

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        self.result = None
        self.setWindowTitle("Отправить плейлист")
        self.setMinimumSize(460, 480)
        self.setStyleSheet(DIALOG_QSS)
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(10)
        h = QLabel("Отправить плейлист")
        h.setStyleSheet("font-size:20px;font-weight:900;")
        v.addWidget(h)
        sub = QLabel(f"Друг увидит список треков и сможет включить любой. Отправляются только названия "
                     f"(до {PLAYLIST_MAX} треков), не файлы.")
        sub.setWordWrap(True)
        sub.setStyleSheet("color:#9a9aa2;font-size:12px;")
        v.addWidget(sub)
        self.list = QListWidget()
        for name, tr in user_playlists(win):
            it = QListWidgetItem(f"{name}   ·  {len(tr)} тр.")
            it.setData(Qt.ItemDataRole.UserRole, name)
            self.list.addItem(it)
        if not self.list.count():
            it = QListWidgetItem("Своих плейлистов пока нет — соберите из коллекции")
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(it)
        self.list.itemDoubleClicked.connect(lambda _: self._pick())
        v.addWidget(self.list, 1)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Название (можно поменять)")
        self.list.currentItemChanged.connect(
            lambda it, _p: self.name.setText(str(it.data(Qt.ItemDataRole.UserRole) or "")) if it else None)
        v.addWidget(self.name)
        row = QHBoxLayout()
        build = QPushButton("Собрать из коллекции…")
        build.clicked.connect(self._build)
        row.addWidget(build)
        row.addStretch(1)
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        ok = QPushButton("Отправить")
        ok.setObjectName("Accent")
        ok.clicked.connect(self._pick)
        row.addWidget(ok)
        v.addLayout(row)

    def _pick(self):
        it = self.list.currentItem()
        name = it.data(Qt.ItemDataRole.UserRole) if it is not None else None
        if not name:
            return
        tr = (self.win.playlists.get(name) or {}).get("tracks") or []
        self.result = ((self.name.text().strip() or name)[:120], [_meta(t) for t in tr[:PLAYLIST_MAX]])
        self.accept()

    def _build(self):
        d = TrackSearch(self.win, self, multi=True, title="Собрать плейлист")
        if not d.exec() or not d.result_tracks:
            return
        self.result = ((self.name.text().strip() or "Подборка")[:120], [_meta(t) for t in d.result_tracks])
        self.accept()


def track_to_meta(t) -> dict:
    return _meta(t)
