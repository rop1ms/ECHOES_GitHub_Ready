# manual_search.py
"""
Ручной поиск текста песни и клипа с результатами в реальном времени.

Пишете запрос — через мгновение после остановки ввода идёт поиск, и результаты появляются по мере
того, как отвечают источники (у текста — каждый сайт отдельно). Старый запрос при новом вводе
отменяется: его результаты не перемешиваются с новыми.

  LyricsSearchDialog — источник, тег «С таймингами» / «Без таймингов» / «Инструментал», совпадение,
                       начало текста; справа — весь текст выбранного результата.
  ClipSearchDialog   — превью ролика, название, канал, длительность; «Открыть на YouTube».
"""
from __future__ import annotations

import re
import threading

from PyQt6.QtCore import QObject, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QImage, QPixmap
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
                             QPushButton, QSplitter, QVBoxLayout, QWidget)

_TS = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:]\d{1,3})?\]")

QSS = """
* { font-family: "Segoe UI Variable Text", "Segoe UI", Arial; }
QDialog { background: #121318; color: #ececf2; }
QLabel { color: #ececf2; background: transparent; }
QLineEdit { background: #1d1f27; color: #f4f4f8; border: 1px solid #2f3240; border-radius: 10px; padding: 8px 12px;
            font-size: 14px; selection-background-color: #ff3d81; }
QLineEdit:focus { border-color: #ff3d81; }
QListWidget { background: #16171d; border: 1px solid #262833; border-radius: 12px; outline: none; }
QListWidget::item { border-bottom: 1px solid #20222b; }
QListWidget::item:selected { background: #2a2233; }
QListWidget::item:hover { background: #1f2029; }
QPlainTextEdit { background: #16171d; color: #e6e6ee; border: 1px solid #262833; border-radius: 12px;
                 padding: 8px; font-size: 13px; }
QPushButton { background: #262833; color: #f0f0f6; border: none; border-radius: 10px; padding: 8px 16px;
              font-weight: 600; }
QPushButton:hover { background: #30333f; }
QPushButton#Primary { background: #ff3d81; color: #ffffff; }
QPushButton#Primary:hover { background: #ff5a95; }
QPushButton:disabled { background: #1d1e25; color: #6a6a78; }
QLabel#Muted { color: #8d8da0; }
"""


def _pill(text, bg, fg="#ffffff"):
    lb = QLabel(text)
    lb.setStyleSheet(f"background:{bg};color:{fg};border-radius:8px;padding:1px 8px;font-size:11px;font-weight:700;")
    return lb


def _plain(text: str) -> str:
    return "\n".join(_TS.sub("", ln).strip() for ln in (text or "").splitlines())


def _with_times(text: str) -> str:
    """LRC → «0:12  строка» (тайминги видно, но читать удобно)."""
    out = []
    for ln in (text or "").splitlines():
        m = _TS.match(ln.strip())
        if m:
            out.append(f"{int(m.group(1))}:{m.group(2)}  {_TS.sub('', ln).strip()}")
        elif not ln.strip().startswith("[") or not ln.strip().endswith("]"):
            out.append(ln)
    return "\n".join(out)


def _fmt(sec) -> str:
    s = int(float(sec or 0))
    return f"{s // 60}:{s % 60:02d}" if s > 0 else ""


class _Bus(QObject):
    hits = pyqtSignal(int, object)
    done = pyqtSignal(int, str)
    thumb = pyqtSignal(str, object)


class _LiveDialog(QDialog):
    """Общее: поле запроса с задержкой, поколения запросов, строка состояния, список результатов."""
    DELAY = 650

    def __init__(self, parent, title, hint, query):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setStyleSheet(QSS)
        self.resize(900, 600)
        self.gen = 0
        self.cancel = None
        self.result = None
        self.bus = _Bus(self)
        self.bus.hits.connect(self._on_hits)
        self.bus.done.connect(self._on_done)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 16, 16, 14)
        v.setSpacing(10)
        h = QLabel(hint)
        h.setObjectName("Muted")
        v.addWidget(h)
        self.edit = QLineEdit(query)
        self.edit.setClearButtonEnabled(True)
        self.edit.textChanged.connect(self._schedule)
        self.edit.returnPressed.connect(self._run_now)
        v.addWidget(self.edit)
        self.status = QLabel("")
        self.status.setObjectName("Muted")
        v.addWidget(self.status)
        self.list = QListWidget()
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.currentItemChanged.connect(lambda *_: self._on_select())
        self.list.itemDoubleClicked.connect(lambda *_: self._apply())
        self.body = QWidget()
        self.body_l = QHBoxLayout(self.body)
        self.body_l.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self.body, 1)
        row = QHBoxLayout()
        self.extra = QHBoxLayout()
        row.addLayout(self.extra)
        row.addStretch(1)
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        self.ok = QPushButton("Применить")
        self.ok.setObjectName("Primary")
        self.ok.setEnabled(False)
        self.ok.clicked.connect(self._apply)
        row.addWidget(cancel)
        row.addWidget(self.ok)
        v.addLayout(row)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._run_now)
        self.items: list = []
        QTimer.singleShot(0, self._run_now)

    # ── запросы ──
    def _schedule(self, *_):
        self.timer.start(self.DELAY)

    def _run_now(self):
        self.timer.stop()
        q = self.edit.text().strip()
        if self.cancel is not None:
            self.cancel.set()
        self.gen += 1
        self.items = []
        self.list.clear()
        self._on_select()
        if not q:
            self.status.setText("Введите запрос")
            return
        self.status.setText(f"Ищем «{q}»…")
        ev = self.cancel = threading.Event()
        gen = self.gen
        threading.Thread(target=self._work, args=(gen, q, ev), daemon=True, name="manual-search").start()

    def _work(self, gen, q, ev):                    # переопределяется
        pass

    def _on_hits(self, gen, hits):
        if gen != self.gen:
            return
        cur = self.list.currentRow()
        for d in hits:
            self.items.append(d)
            self._add_item(d)
        n = len(self.items)
        self.status.setText(f"Ищем «{self.edit.text().strip()}»…  найдено: {n}")
        if cur < 0 and self.list.count():
            self.list.setCurrentRow(0)

    def _on_done(self, gen, err):
        if gen != self.gen:
            return
        n = len(self.items)
        if n:
            self.status.setText(f"Готово — найдено: {n}. Двойной щелчок или кнопка справа внизу — выбрать.")
        else:
            self.status.setText("Ничего не нашлось" + (f": {err}" if err else ". Попробуйте другой запрос."))

    def _selected(self):
        it = self.list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it is not None else None

    def _on_select(self):
        self.ok.setEnabled(self._selected() is not None)

    def _apply(self):
        d = self._selected()
        if d is None:
            return
        self.result = d
        self.accept()

    def done(self, r):
        if self.cancel is not None:
            self.cancel.set()
        self.gen += 1
        super().done(r)

    def _add_item(self, d):                         # переопределяется
        pass


# ══════════════════════════════════════════════════════════════════════════ #
#  Текст песни
# ══════════════════════════════════════════════════════════════════════════ #

class LyricsSearchDialog(_LiveDialog):
    def __init__(self, parent, query, duration=0.0, token=""):
        super().__init__(parent, "Поиск текста", "Исполнитель - название (или что угодно). Результаты появляются "
                                                  "по мере ответа сайтов.", query)
        self.duration, self.token = duration, token
        sp = QSplitter(Qt.Orientation.Horizontal)
        sp.addWidget(self.list)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.prev_head = QLabel("")
        self.prev_head.setWordWrap(True)
        rv.addWidget(self.prev_head)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        rv.addWidget(self.preview, 1)
        sp.addWidget(right)
        sp.setSizes([470, 400])
        self.body_l.addWidget(sp)

    def _work(self, gen, q, ev):
        artist, title = "", q
        for sep in (" - ", " – ", " — "):
            if sep in q:
                artist, title = (x.strip() for x in q.split(sep, 1))
                break
        err = ""
        try:
            from lyrics import search_stream
            search_stream(title, artist, self.token, self.duration,
                          on_hits=lambda hs: self.bus.hits.emit(gen, hs), cancel=ev)
        except Exception as e:                                 # noqa: BLE001
            err = str(e)
        if not ev.is_set():
            self.bus.done.emit(gen, err)

    def _on_hits(self, gen, hits):
        if gen != self.gen:
            return
        # лучшие сверху: с таймингами, затем по совпадению с запросом
        sel = self._selected()
        self.items.extend(hits)
        self.items.sort(key=lambda d: (not d.get("synced"), d.get("instrumental", False), -float(d.get("match", 0))))
        self.list.clear()
        for d in self.items:
            self._add_item(d)
        row = self.items.index(sel) if sel in self.items else 0
        if self.list.count():
            self.list.setCurrentRow(row)
        self.status.setText(f"Ищем «{self.edit.text().strip()}»…  найдено: {len(self.items)}")

    def _add_item(self, d):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(12, 9, 12, 9)
        v.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(8)
        src = QLabel(d.get("source", ""))
        src.setStyleSheet("font-size:14px;font-weight:800;")
        top.addWidget(src)
        if d.get("instrumental"):
            top.addWidget(_pill("Инструментал", "#6a3cff"))
        elif d.get("synced"):
            top.addWidget(_pill("С таймингами", "#1f9d55"))
        else:
            top.addWidget(_pill("Без таймингов", "#4a4c58", "#e8e8f0"))
        m = float(d.get("match", 0) or 0)
        mp = QLabel(f"совпадение {int(round(min(1.0, max(0.0, m)) * 100))}%")
        mp.setObjectName("Muted")
        top.addWidget(mp)
        top.addStretch(1)
        v.addLayout(top)
        who = " — ".join(x for x in (d.get("artist", ""), d.get("title", "")) if x)
        if who:
            lb = QLabel(who)
            lb.setStyleSheet("color:#c8c8d6;font-size:12px;font-weight:600;")
            v.addWidget(lb)
        lines = [ln for ln in _plain(d.get("text", "")).splitlines() if ln.strip()][:2]
        if lines:
            pv = QLabel("  /  ".join(lines))
            pv.setObjectName("Muted")
            pv.setStyleSheet("font-size:12px;")
            v.addWidget(pv)
        it = QListWidgetItem()
        it.setData(Qt.ItemDataRole.UserRole, d)
        it.setSizeHint(QSize(100, w.sizeHint().height() + 4))
        self.list.addItem(it)
        self.list.setItemWidget(it, w)

    def _on_select(self):
        super()._on_select()
        d = self._selected()
        if d is None:
            self.prev_head.setText("")
            self.preview.setPlainText("")
            return
        tag = "инструментал" if d.get("instrumental") else ("с таймингами" if d.get("synced") else "без таймингов")
        self.prev_head.setText(f"<b>{d.get('source', '')}</b> · {tag}")
        self.preview.setPlainText(_with_times(d.get("text", "")) if d.get("synced") else d.get("text", ""))


# ══════════════════════════════════════════════════════════════════════════ #
#  Клип
# ══════════════════════════════════════════════════════════════════════════ #

_THUMBS: dict = {}


class ClipSearchDialog(_LiveDialog):
    DELAY = 800
    TW, TH = 176, 99

    def __init__(self, parent, query, title="", artist="", dur=0.0):
        super().__init__(parent, "Поиск клипа", "Что искать на YouTube. Результаты обновляются, пока вы печатаете.",
                         query)
        self.t_title, self.t_artist, self.dur = title, artist, dur
        self.list.setIconSize(QSize(self.TW, self.TH))
        self.body_l.addWidget(self.list)
        self.bus.thumb.connect(self._on_thumb)
        self.labels: dict = {}
        self.ok.setText("Скачать этот клип")
        yt = QPushButton("Открыть на YouTube")
        yt.clicked.connect(self._open_yt)
        self.extra.addWidget(yt)

    def _work(self, gen, q, ev):
        err = ""
        res = []
        try:
            import clip_fetch as CF
            res = CF.search(self.t_title, self.t_artist, self.dur, n=14, query=q)
        except Exception as e:                                 # noqa: BLE001
            err = str(e)
        if ev.is_set():
            return
        if res:
            self.bus.hits.emit(gen, res)
        self.bus.done.emit(gen, err)
        for c in res:                                          # превью — по одному, в фоне
            if ev.is_set():
                return
            self._fetch_thumb(c["id"])

    def _fetch_thumb(self, vid):
        if vid in _THUMBS:
            self.bus.thumb.emit(vid, _THUMBS[vid])
            return
        data = None
        try:
            import urllib.request
            req = urllib.request.Request(f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
                                         headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                data = r.read()
        except Exception:                                      # noqa: BLE001
            data = None
        _THUMBS[vid] = data
        self.bus.thumb.emit(vid, data)

    def _on_thumb(self, vid, data):
        lb = self.labels.get(vid)
        if lb is None or not data:
            return
        try:
            img = QImage.fromData(data)
        except RuntimeError:
            return
        if img.isNull():
            return
        pm = QPixmap.fromImage(img.scaled(self.TW, self.TH, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                          Qt.TransformationMode.SmoothTransformation))
        try:
            lb.setPixmap(pm)
        except RuntimeError:
            pass

    def _run_now(self):
        self.labels = {}
        super()._run_now()

    def _add_item(self, c):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(10, 8, 10, 8)
        h.setSpacing(14)
        th = QLabel()
        th.setFixedSize(self.TW, self.TH)
        th.setAlignment(Qt.AlignmentFlag.AlignCenter)
        th.setStyleSheet("background:#0c0d11;border-radius:8px;color:#55556a;")
        th.setText("…")
        self.labels[c["id"]] = th
        if _THUMBS.get(c["id"]):
            self.bus.thumb.emit(c["id"], _THUMBS[c["id"]])
        h.addWidget(th)
        v = QVBoxLayout()
        v.setSpacing(4)
        t = QLabel(c.get("title", ""))
        t.setWordWrap(True)
        t.setStyleSheet("font-size:14px;font-weight:700;")
        v.addWidget(t)
        meta = " · ".join(x for x in (c.get("channel", ""), _fmt(c.get("duration", 0))) if x)
        m = QLabel(meta)
        m.setObjectName("Muted")
        v.addWidget(m)
        if self.dur and c.get("duration"):
            diff = abs(float(c["duration"]) - float(self.dur))
            if diff <= 6:
                pr = QHBoxLayout()
                pr.addWidget(_pill("длина совпадает с треком", "#1f9d55"))
                pr.addStretch(1)
                v.addLayout(pr)
        v.addStretch(1)
        h.addLayout(v, 1)
        it = QListWidgetItem()
        it.setData(Qt.ItemDataRole.UserRole, c)
        it.setSizeHint(QSize(100, self.TH + 18))
        self.list.addItem(it)
        self.list.setItemWidget(it, w)

    def _open_yt(self):
        c = self._selected()
        if c is not None:
            QDesktopServices.openUrl(QUrl(c.get("url") or f"https://www.youtube.com/watch?v={c['id']}"))
