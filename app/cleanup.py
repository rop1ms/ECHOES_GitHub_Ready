# cleanup.py
"""
«Очистка медиатеки и кэша»:

  Кривые файлы — треки с плохими тегами (нет исполнителя, «(1)», «official video», «www.…», номер
                 дорожки в названии, имя файла вместо названия, нет обложки…). Для каждого в фоне
                 ищется нормальная версия на площадках (YouTube Music → YouTube → SoundCloud, как в
                 поиске Echoes), её можно поменять в списке. «Заменить отмеченные» скачивает их
                 очередью закачек, и новый файл встаёт на место старого: в коллекции, плейлистах,
                 очереди, истории прослушиваний; старый — в Корзину (по желанию).
  Клипы        — скачанные клипы: к какому треку, размер; удалить выбранные или все.
  Кэш          — сколько места занимает каждый кэш, кнопка «Очистить».
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton,
                             QScrollArea, QTabWidget, QVBoxLayout, QWidget)

DATA = Path.home() / ".neon_player"

QSS = """
* { font-family: "Segoe UI Variable Text", "Segoe UI", Arial; }
QDialog, QWidget#Page { background: #121318; color: #ececf2; }
QLabel { color: #ececf2; background: transparent; }
QLabel#Muted { color: #8d8da0; }
QLabel#H { font-size: 15px; font-weight: 800; }
QTabWidget::pane { border: none; }
QTabBar::tab { background: transparent; color: #a0a0b4; padding: 8px 16px; font-size: 14px; font-weight: 700;
               border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #ffffff; border-bottom-color: #ff3d81; }
QScrollArea { border: none; background: transparent; }
QFrame#Row { background: #191a21; border-radius: 12px; }
QFrame#Row:hover { background: #1e1f27; }
QPushButton { background: #262833; color: #f0f0f6; border: none; border-radius: 10px; padding: 7px 14px;
              font-weight: 600; }
QPushButton:hover { background: #30333f; }
QPushButton#Primary { background: #ff3d81; color: #ffffff; }
QPushButton#Primary:hover { background: #ff5a95; }
QPushButton#Danger { background: #3a1e26; color: #ff9db5; }
QPushButton#Danger:hover { background: #4a2430; }
QPushButton:disabled { background: #1d1e25; color: #6a6a78; }
QComboBox { background: #1f2029; color: #f0f0f6; border: 1px solid #2f3240; border-radius: 8px; padding: 5px 10px; }
QComboBox QAbstractItemView { background: #1f2029; color: #f0f0f6; selection-background-color: #ff3d81; }
QCheckBox { color: #ececf2; spacing: 8px; }
"""


# ══════════════════════════════════════════════════════════════════════════ #
#  Распознать «кривой» файл
# ══════════════════════════════════════════════════════════════════════════ #

_JUNK = [
    (re.compile(r"\(\s*\d{1,2}\s*\)\s*$|\bcopy\b|\bкопия\b", re.I), "копия файла"),
    (re.compile(r"official\s*(music\s*)?video|official\s*audio|lyric(s)?\s*video|\blyrics?\b|"
                r"\bvisuali[sz]er\b|\bклип\b|\bвидео\b", re.I), "приписка из YouTube"),
    (re.compile(r"\b(hq|hd|4k|1080p|720p|320\s*kbps|\d{3}\s*kbps|kbps|mp3|flac|wav|m4a)\b", re.I), "качество в названии"),
    (re.compile(r"www\.|https?://|\.(com|ru|net|org|me|cc|fm|info)\b|скачать|download|free\s*dl|zaycev|muzofond|"
                r"hitmo|mp3party|lightaudio|drivemusic", re.I), "адрес сайта"),
    (re.compile(r"^\s*(track\s*)?\d{1,3}\s*[\.\-_)]\s*|^\s*\d{1,3}\s+(?=\D)", re.I), "номер дорожки"),
    (re.compile(r"_{1,}|\s{2,}"), "подчёркивания"),
    (re.compile(r"[\[{].*?[\]}]"), "мусор в скобках"),
]
_UNKNOWN = {"", "unknown", "unknown artist", "неизвестный исполнитель", "various artists", "va", "artist", "исполнитель",
            "<unknown>", "без исполнителя"}


def junk_reasons(t: dict) -> list[str]:
    title = str(t.get("title") or "")
    artist = str(t.get("artist") or "").strip()
    stem = Path(str(t.get("path") or "")).stem
    out = []
    if artist.lower() in _UNKNOWN:
        out.append("нет исполнителя")
    seen = set()
    for rx, why in _JUNK:
        if why in seen:
            continue
        if rx.search(title) or (title.strip().lower() == stem.strip().lower() and rx.search(stem)):
            out.append(why)
            seen.add(why)
    if not artist and " - " in title:
        out.append("исполнитель в названии")
    if not str(t.get("cover") or "").strip():
        out.append("нет обложки")
    if is_random_name(title):
        out.insert(0, "случайное имя файла")
    return out


def is_random_name(s: str) -> bool:
    """«65wXsoL3sTv0IrIU2axI+GaGOwFA…» — имя-хэш, по нему ничего не найти."""
    s = (s or "").strip()
    if " " in s or len(s) < 14:
        return False
    alnum = sum(c.isalnum() for c in s)
    mixed = any(c.isdigit() for c in s) and any(c.isupper() for c in s) and any(c.islower() for c in s)
    return alnum >= len(s) * 0.8 and (mixed or "+" in s)


def is_junk(t: dict) -> bool:
    r = junk_reasons(t)
    weight = sum(2 if x == "нет исполнителя" else 1 for x in r)
    return weight >= 2


def guess_query(t: dict) -> tuple[str, str]:
    """(название, исполнитель) для поиска: без мусора."""
    title = str(t.get("title") or "") or Path(str(t.get("path") or "")).stem
    artist = str(t.get("artist") or "").strip()
    if artist.lower() in _UNKNOWN:
        artist = ""
    s = title
    s = re.sub(r"[\[{(][^\]})]*(official|video|audio|lyric|hq|hd|kbps|mp3|www|\.com|скачать|клип)[^\]})]*[\]})]", " ", s,
               flags=re.I)
    for rx, why in _JUNK:
        if why in ("мусор в скобках",):
            continue
        s = rx.sub(" ", s)
    s = re.sub(r"\s+", " ", s.replace("_", " ")).strip(" -–—.")
    if not artist:
        for sep in (" - ", " – ", " — "):
            if sep in s:
                a, b = s.split(sep, 1)
                artist, s = a.strip(), b.strip()
                break
    return s or title, artist


def _fmt(sec) -> str:
    s = int(float(sec or 0))
    return f"{s // 60}:{s % 60:02d}" if s > 0 else "—"


def _size(n) -> str:
    n = float(n)
    for u in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024 or u == "ГБ":
            return f"{n:.0f} {u}" if u in ("Б", "КБ") else f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} ГБ"


NET_MEDIA = ("n_image", "n_video", "n_audio", "n_other")


def _net_files(kind: str) -> list:
    """Скачанные в Echoes.NET файлы одного типа (свои отправленные — нет: друзья качают их у вас)."""
    d = DATA / "net" / "files" / "in"
    if not d.exists():
        return []
    try:
        from net_files import kind_of
    except Exception:                                              # noqa: BLE001
        return []
    out = []
    for f in d.iterdir():
        if not f.is_file():
            continue
        k = kind_of(f.name)
        k = k if k in ("image", "video", "audio") else "other"
        if k == kind:
            out.append(f)
    return out


def dir_size(p: Path) -> int:
    total = 0
    if p.is_file():
        try:
            return p.stat().st_size
        except OSError:
            return 0
    for root, _dirs, files in os.walk(p):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


class _Bus(QObject):
    found = pyqtSignal(int, object)          # номер строки, кандидаты
    sized = pyqtSignal(str, int)
    clips = pyqtSignal(object)


def _pill(text, bg="#2a2c38", fg="#c8c8d8"):
    lb = QLabel(text)
    lb.setStyleSheet(f"background:{bg};color:{fg};border-radius:7px;padding:1px 7px;font-size:11px;font-weight:700;")
    return lb


def _scroll_list():
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    host = QWidget()
    host.setObjectName("Page")
    v = QVBoxLayout(host)
    v.setContentsMargins(0, 0, 6, 0)
    v.setSpacing(8)
    v.addStretch(1)
    sa.setWidget(host)
    return sa, v


# ══════════════════════════════════════════════════════════════════════════ #
#  Окно
# ══════════════════════════════════════════════════════════════════════════ #

class CleanupDialog(QDialog):
    def __init__(self, win, tab=0):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Очистка медиатеки и кэша")
        self.setStyleSheet(QSS)
        self.resize(1040, 720)
        self.bus = _Bus(self)
        self.bus.found.connect(self._on_found)
        self.bus.sized.connect(self._on_sized)
        self.bus.clips.connect(self._fill_clips)
        self._stop = threading.Event()
        self._pool = None
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 14)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_tracks(), "Кривые файлы")
        self.tabs.addTab(self._tab_clips(), "Клипы")
        self.tabs.addTab(self._tab_cache(), "Кэш")
        v.addWidget(self.tabs, 1)
        self.tabs.setCurrentIndex(tab)
        QTimer.singleShot(50, self._start_scan)

    def done(self, r):
        self._stop.set()
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
        super().done(r)

    # ══ кривые файлы ══
    def _tab_tracks(self):
        w = QWidget()
        w.setObjectName("Page")
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 10, 0, 0)
        v.setSpacing(8)
        tip = QLabel("Треки с плохими тегами и мусором в названиях. Для каждого Echoes сам ищет нормальную версию "
                     "на площадках (YouTube Music, YouTube, SoundCloud). Отметьте нужные и нажмите «Заменить» — "
                     "скачается чистый файл с тегами и обложкой и встанет на место старого во всех плейлистах.")
        tip.setObjectName("Muted")
        tip.setWordWrap(True)
        v.addWidget(tip)
        top = QHBoxLayout()
        self.t_status = QLabel("")
        self.t_status.setObjectName("H")
        top.addWidget(self.t_status, 1)
        self.chk_all = QCheckBox("Показать все треки")
        self.chk_all.setToolTip("Не только найденные кривые: отметьте любой трек — для него тоже найдётся версия")
        self.chk_all.toggled.connect(lambda _on: self._start_scan())
        top.addWidget(self.chk_all)
        v.addLayout(top)
        sa, self.t_list = _scroll_list()
        v.addWidget(sa, 1)
        bot = QHBoxLayout()
        b_sel = QPushButton("Отметить уверенные")
        b_sel.setToolTip("Отметить все, где найденная версия совпала уверенно")
        b_sel.clicked.connect(lambda: self._mark(lambda r: r.get("best", 0) >= 0.75))
        b_none = QPushButton("Снять все")
        b_none.clicked.connect(lambda: self._mark(lambda r: False))
        bot.addWidget(b_sel)
        bot.addWidget(b_none)
        bot.addStretch(1)
        self.chk_trash = QCheckBox("Старые файлы — в Корзину")
        self.chk_trash.setChecked(True)
        bot.addWidget(self.chk_trash)
        self.b_go = QPushButton("Заменить отмеченные")
        self.b_go.setObjectName("Primary")
        self.b_go.clicked.connect(self._replace)
        bot.addWidget(self.b_go)
        v.addLayout(bot)
        self.rows: list[dict] = []
        return w

    def _start_scan(self):
        self._refresh_cache_sizes()
        self._stop.set()
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
        self._stop = threading.Event()
        lay = self.t_list
        while lay.count() > 1:
            it = lay.takeAt(0)
            if it.widget() is not None:
                it.widget().deleteLater()
        self.rows = []
        lib = list(self.win.library)
        show_all = self.chk_all.isChecked()
        for t in lib:
            reasons = junk_reasons(t)
            junk = is_junk(t)
            if not junk and not show_all:
                continue
            self._add_row(t, reasons, junk)
        n_junk = sum(1 for r in self.rows if r["junk"])
        self.t_status.setText(f"Найдено кривых файлов: {n_junk}" + (f" из {len(lib)}" if lib else "")
                              + ("  ·  ищу версии на площадках…" if n_junk else ""))
        self._pool = ThreadPoolExecutor(3, thread_name_prefix="cleanup-search")
        ev = self._stop
        for i, r in enumerate(self.rows):
            if r["junk"]:
                self._pool.submit(self._search, i, r["t"], ev)

    def _search(self, i, t, ev, query=None):
        if ev.is_set():
            return
        if query is None and is_random_name(str(t.get("title") or "")):
            self.bus.found.emit(i, None)                # нечего искать — пусть впишут свой запрос
            return
        title, artist = guess_query(t) if query is None else (query, "")
        if query is not None:
            for sep in (" - ", " – ", " — "):
                if sep in query:
                    artist, title = (x.strip() for x in query.split(sep, 1))
                    break
        res = []
        try:
            import online_search
            res = online_search.find_candidates(title, artist, float(t.get("duration") or 0), limit=6)
        except Exception as e:                                     # noqa: BLE001
            print("[cleanup] search:", e)
        if not ev.is_set():
            self.bus.found.emit(i, res)

    def _add_row(self, t, reasons, junk):
        fr = QFrame()
        fr.setObjectName("Row")
        h = QHBoxLayout(fr)
        h.setContentsMargins(12, 10, 12, 10)
        h.setSpacing(12)
        chk = QCheckBox()
        chk.setEnabled(False)
        h.addWidget(chk, 0, Qt.AlignmentFlag.AlignTop)
        left = QVBoxLayout()
        left.setSpacing(3)
        nm = QLabel(str(t.get("title") or "—"))
        nm.setStyleSheet("font-size:14px;font-weight:700;")
        nm.setWordWrap(True)
        left.addWidget(nm)
        sub = QLabel(f"{t.get('artist') or 'нет исполнителя'}  ·  {Path(str(t.get('path', ''))).name}")
        sub.setObjectName("Muted")
        sub.setWordWrap(True)
        left.addWidget(sub)
        if reasons:
            pr = QHBoxLayout()
            pr.setSpacing(5)
            for x in reasons[:5]:
                pr.addWidget(_pill(x, "#3a2a1e", "#ffc48a") if junk else _pill(x))
            pr.addStretch(1)
            left.addLayout(pr)
        h.addLayout(left, 5)
        arrow = QLabel("→")
        arrow.setStyleSheet("font-size:18px;color:#6a6a7e;")
        h.addWidget(arrow)
        right = QVBoxLayout()
        right.setSpacing(4)
        cb = QComboBox()
        cb.setMinimumWidth(260)
        cb.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        cb.setMinimumContentsLength(24)
        if junk:
            cb.addItem("ищу версию на площадках…")
            cb.setEnabled(False)
        else:
            cb.addItem("отметьте, чтобы найти версию")
            cb.setEnabled(False)
        right.addWidget(cb)
        srow = QHBoxLayout()
        st = QLabel("")
        st.setObjectName("Muted")
        srow.addWidget(st, 1)
        own = QPushButton("Свой запрос…")
        own.setToolTip("Найти по своему запросу: «исполнитель - название»")
        srow.addWidget(own)
        right.addLayout(srow)
        h.addLayout(right, 5)
        row = {"t": t, "frame": fr, "chk": chk, "cb": cb, "st": st, "cands": [], "junk": junk, "best": 0.0,
               "searched": junk, "done": False}
        if not junk:
            chk.setEnabled(True)
            chk.toggled.connect(lambda on, r=row: self._lazy_search(r) if on else None)
        own.clicked.connect(lambda _=False, r=row: self._own_query(r))
        self.rows.append(row)
        self.t_list.insertWidget(self.t_list.count() - 1, fr)

    def _own_query(self, r):
        from PyQt6.QtWidgets import QInputDialog
        title, artist = guess_query(r["t"])
        guess = "" if is_random_name(title) else (f"{artist} - {title}" if artist else title)
        q, ok = QInputDialog.getText(self, "Свой запрос", "Исполнитель - название:", text=guess)
        q = (q or "").strip()
        if not ok or not q or r["done"]:
            return
        r["searched"] = True
        r["cb"].clear()
        r["cb"].addItem(f"ищу «{q}»…")
        r["cb"].setEnabled(False)
        if self._pool is None:
            self._pool = ThreadPoolExecutor(3, thread_name_prefix="cleanup-search")
        self._pool.submit(self._search, self.rows.index(r), r["t"], self._stop, q)

    def _lazy_search(self, r):
        if r["searched"]:
            return
        r["searched"] = True
        r["cb"].clear()
        r["cb"].addItem("ищу версию на площадках…")
        i = self.rows.index(r)
        if self._pool is None:
            self._pool = ThreadPoolExecutor(3, thread_name_prefix="cleanup-search")
        self._pool.submit(self._search, i, r["t"], self._stop)

    def _on_found(self, i, res):
        if not (0 <= i < len(self.rows)):
            return
        r = self.rows[i]
        cb = r["cb"]
        try:
            cb.clear()
        except RuntimeError:
            return
        if res is None:
            r["cands"] = []
            cb.addItem("случайное имя — нажмите «Свой запрос…»")
            cb.setEnabled(False)
            r["chk"].setChecked(False)
            r["chk"].setEnabled(False)
            return
        r["cands"] = res
        if not res:
            cb.addItem("на площадках не нашлось — попробуйте «Свой запрос…»")
            cb.setEnabled(False)
            r["chk"].setChecked(False)
            r["chk"].setEnabled(False)
        else:
            src = {"ytm": "YouTube Music", "yt": "YouTube", "sc": "SoundCloud"}
            for c in res:
                cb.addItem(f"{c.get('title', '')} — {c.get('artist', '')}   ·   {src.get(c.get('source'), c.get('source', ''))}"
                           f"   ·   {_fmt(c.get('duration'))}   ·   {int(round(max(0.0, min(1.0, c.get('score', 0))) * 100))}%")
            cb.setEnabled(True)
            r["best"] = float(res[0].get("score", 0))
            r["chk"].setEnabled(True)
            r["chk"].setChecked(r["best"] >= 0.75 or not r["junk"])
        left = sum(1 for x in self.rows if x["searched"] and not x["cands"] and x["cb"].itemText(0).startswith("ищу"))
        n_ok = sum(1 for x in self.rows if x["cands"])
        n_junk = sum(1 for x in self.rows if x["junk"])
        self.t_status.setText(f"Найдено кривых файлов: {n_junk}  ·  версии найдены: {n_ok}"
                              + ("  ·  ищу…" if left else ""))

    def _mark(self, pred):
        for r in self.rows:
            if r["chk"].isEnabled():
                r["chk"].setChecked(bool(pred(r)) and bool(r["cands"]))

    def _replace(self):
        todo = [r for r in self.rows if r["chk"].isChecked() and r["cands"] and not r["done"]]
        if not todo:
            QMessageBox.information(self, "Очистка", "Отметьте треки, для которых найдена версия.")
            return
        if QMessageBox.question(self, "Заменить файлы",
                                f"Скачать {len(todo)} трек(ов) с площадок и заменить ими старые файлы?\n"
                                + ("Старые файлы уйдут в Корзину." if self.chk_trash.isChecked()
                                   else "Старые файлы останутся на диске.")) != QMessageBox.StandardButton.Yes:
            return
        try:
            from ya_online import _meta
        except Exception:                                          # noqa: BLE001
            def _meta(c):
                return {"title": c.get("title"), "artist": c.get("artist"), "album": c.get("album") or "",
                        "thumb_url": c.get("thumb_url") or "", "duration": c.get("duration") or 0}
        trash = self.chk_trash.isChecked()
        for r in todo:
            c = r["cands"][max(0, r["cb"].currentIndex())]
            r["chk"].setEnabled(False)
            r["cb"].setEnabled(False)
            r["st"].setText("в очереди…")
            old = str(r["t"].get("path", ""))

            def cb(state, val, r=r, old=old):
                try:
                    if state == "progress":
                        r["st"].setText(f"скачиваю… {int(val)}%")
                    elif state == "retry":
                        r["st"].setText("пробую другой источник…")
                    elif state == "error":
                        r["st"].setText(f"не скачалось: {val}")
                        r["chk"].setEnabled(True)
                        r["cb"].setEnabled(True)
                    elif state == "done":
                        prob = self.win.replace_track_file(old, val, delete_old=trash)
                        r["done"] = True
                        r["st"].setText("заменено" + (f" ({prob})" if prob and prob != "это тот же файл" else "")
                                        if prob != "это тот же файл" else "этот файл уже скачан с площадки")
                        r["st"].setStyleSheet("color:#5fd38d;")
                except RuntimeError:
                    pass
            self.win._quick_download(c["url"], playlist=None, cb=cb, meta=_meta(c))

    # ══ клипы ══
    def _tab_clips(self):
        w = QWidget()
        w.setObjectName("Page")
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 10, 0, 0)
        v.setSpacing(8)
        top = QHBoxLayout()
        self.c_status = QLabel("Считаю клипы…")
        self.c_status.setObjectName("H")
        top.addWidget(self.c_status, 1)
        b_open = QPushButton("Открыть папку")
        b_open.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(DATA / "clips"))))
        top.addWidget(b_open)
        v.addLayout(top)
        sa, self.c_list = _scroll_list()
        v.addWidget(sa, 1)
        bot = QHBoxLayout()
        b_all = QPushButton("Отметить все")
        b_all.clicked.connect(lambda: [c["chk"].setChecked(True) for c in self.clip_rows])
        b_none = QPushButton("Снять все")
        b_none.clicked.connect(lambda: [c["chk"].setChecked(False) for c in self.clip_rows])
        bot.addWidget(b_all)
        bot.addWidget(b_none)
        bot.addStretch(1)
        b_del = QPushButton("Удалить отмеченные клипы")
        b_del.setObjectName("Danger")
        b_del.clicked.connect(self._delete_clips)
        bot.addWidget(b_del)
        v.addLayout(bot)
        self.clip_rows: list = []
        threading.Thread(target=self._scan_clips, daemon=True).start()
        return w

    def _scan_clips(self):
        import clip_fetch as CF
        try:
            idx = CF._load()
        except Exception:                                          # noqa: BLE001
            idx = {}
        by_path = {str(t.get("path", "")): t for t in self.win.library}
        used, out = set(), []
        for key, e in idx.items():
            f = e.get("file")
            if not f:
                continue
            p = CF.CLIP_DIR / f
            if not p.exists():
                continue
            used.add(p.name)
            t = by_path.get(key)
            name = f"{t.get('artist', '')} — {t.get('title', '')}".strip(" —") if t else key.replace("|", " — ")
            out.append({"key": key, "path": p, "name": name or f, "clip": e.get("title") or "", "size": dir_size(p)})
        if CF.CLIP_DIR.exists():
            for p in CF.CLIP_DIR.iterdir():
                if p.is_file() and p.suffix.lower() in CF.VIDEO_EXT and p.name not in used:
                    out.append({"key": None, "path": p, "name": "без трека (остался от старого поиска)",
                                "clip": p.name, "size": dir_size(p)})
        out.sort(key=lambda x: -x["size"])
        self.bus.clips.emit(out)

    def _fill_clips(self, items):
        lay = self.c_list
        while lay.count() > 1:
            it = lay.takeAt(0)
            if it.widget() is not None:
                it.widget().deleteLater()
        self.clip_rows = []
        total = sum(x["size"] for x in items)
        self.c_status.setText(f"Клипов: {len(items)}  ·  {_size(total)}")
        for x in items:
            fr = QFrame()
            fr.setObjectName("Row")
            h = QHBoxLayout(fr)
            h.setContentsMargins(12, 8, 12, 8)
            chk = QCheckBox()
            h.addWidget(chk)
            col = QVBoxLayout()
            col.setSpacing(2)
            a = QLabel(x["name"])
            a.setStyleSheet("font-size:13px;font-weight:700;")
            col.addWidget(a)
            if x["clip"]:
                b = QLabel(x["clip"])
                b.setObjectName("Muted")
                col.addWidget(b)
            h.addLayout(col, 1)
            sz = QLabel(_size(x["size"]))
            sz.setObjectName("Muted")
            h.addWidget(sz)
            one = QPushButton("Удалить")
            one.setObjectName("Danger")
            row = dict(x, chk=chk, frame=fr)
            one.clicked.connect(lambda _=False, r=row: self._delete_clips([r]))
            h.addWidget(one)
            self.clip_rows.append(row)
            lay.insertWidget(lay.count() - 1, fr)

    def _delete_clips(self, rows=None):
        rows = rows if rows is not None else [r for r in self.clip_rows if r["chk"].isChecked()]
        if not rows:
            return
        total = sum(r["size"] for r in rows)
        if QMessageBox.question(self, "Удалить клипы", f"Удалить {len(rows)} клип(ов), {_size(total)}?\n"
                                "Их можно будет скачать заново в режиме клипа.") != QMessageBox.StandardButton.Yes:
            return
        import clip_fetch as CF
        self._close_clip_player()
        failed = 0
        with CF._lock:
            d = CF._load()
            for r in rows:
                try:
                    r["path"].unlink()
                except OSError:
                    failed += 1
                    continue
                if r["key"] is not None:
                    d.pop(r["key"], None)
            CF._save(d)
        for r in rows:
            if not r["path"].exists():
                try:
                    r["frame"].deleteLater()
                except RuntimeError:
                    pass
                if r in self.clip_rows:
                    self.clip_rows.remove(r)
        left = sum(r["size"] for r in self.clip_rows)
        self.c_status.setText(f"Клипов: {len(self.clip_rows)}  ·  {_size(left)}"
                              + (f"  ·  {failed} не удалось (заняты)" if failed else ""))
        self._refresh_cache_sizes()

    def _close_clip_player(self):
        """Открытый режим клипа держит файл — отпустить его перед удалением."""
        cm = self.win.__dict__.get("_clip_mode")
        try:
            if cm is not None:
                cm._stop_video()
        except Exception:                                          # noqa: BLE001
            pass

    # ══ кэш ══
    def _caches(self):
        return [
            ("clips", "Клипы", [DATA / "clips"], "Все скачанные клипы. Скачаются заново, когда откроете режим клипа."),
            ("dl_tmp", "Временные файлы закачек", [DATA / "dl_tmp"], "Остатки прерванных закачек — не нужны."),
            ("img", "Картинки из интернета", [DATA / "net_cache" / "img"],
             "Фото исполнителей, обложки альбомов и онлайн-треков. Загрузятся заново, когда понадобятся."),
            ("net", "Данные исполнителей и альбомов", [DATA / "net_cache" / "artists", DATA / "net_cache" / "ytm"],
             "Биографии, дискографии, списки треков из интернета. Загрузятся заново при открытии."),
            ("n_image", "Echoes.NET · фото", _net_files("image"),
             "Фото, которые вы открывали в чатах и постах. Скачаются снова, если нажать на них."),
            ("n_video", "Echoes.NET · видео", _net_files("video"), "Скачанные видео из чатов и постов."),
            ("n_audio", "Echoes.NET · аудио", _net_files("audio"), "Аудиофайлы и треки, присланные друзьями."),
            ("n_other", "Echoes.NET · документы и другие файлы", _net_files("other"),
             "PDF, тексты, архивы и всё остальное, что вы скачивали в Echoes.NET."),
            ("n_gif", "Echoes.NET · GIF", [DATA / "net" / "gif_cache"],
             "Анимации из Tenor и Giphy, которые вы видели. Сохранённые вами GIF не трогаются."),
            ("covers", "Ненужные обложки", [DATA / "covers"], "Обложки треков, которых больше нет в коллекции и "
                                                              "плейлистах. Обложки ваших треков не трогаются."),
            ("ui", "Кэш интерфейса", [DATA / "ui_cache"], "Готовые картинки тем — пересоздадутся сами."),
            ("noise", "Шумы для Lo-Fi", [DATA / "noise_cache"], "Сгенерированный шум пластинки и дождя — "
                                                               "пересоздастся при включении."),
            ("lyrics", "Найденные тексты песен", [DATA / "lyrics_cache.json"],
             "Тексты, найденные в интернете (и выбранные вручную) — найдутся заново. Свои тексты в тегах "
             "файлов не трогаются."),
            ("logs", "Журналы", [DATA / n for n in ("crash.log", "bgproc.log", "gapless.log", "online.log")],
             "Файлы журналов для отладки."),
        ]

    def _tab_cache(self):
        w = QWidget()
        w.setObjectName("Page")
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 10, 0, 0)
        v.setSpacing(8)
        self.k_status = QLabel("Считаю…")
        self.k_status.setObjectName("H")
        v.addWidget(self.k_status)
        self.k_rows = {}
        self.k_checks = {}
        top = QHBoxLayout()
        allc = QCheckBox("Выбрать всё")
        allc.toggled.connect(lambda on: [c.setChecked(on) for c in self.k_checks.values()])
        top.addWidget(allc)
        top.addStretch(1)
        self.k_sel = QPushButton("Очистить выбранное")
        self.k_sel.setObjectName("Danger")
        self.k_sel.setEnabled(False)
        self.k_sel.clicked.connect(lambda: self._clear_many([k for k, c in self.k_checks.items() if c.isChecked()]))
        top.addWidget(self.k_sel)
        v.addLayout(top)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner.setObjectName("Page")
        outer_v, v = v, QVBoxLayout(inner)
        v.setContentsMargins(0, 0, 6, 0)
        v.setSpacing(8)
        sc.setWidget(inner)
        outer_v.addWidget(sc, 1)
        for key, name, _paths, desc in self._caches():
            fr = QFrame()
            fr.setObjectName("Row")
            h = QHBoxLayout(fr)
            h.setContentsMargins(14, 10, 14, 10)
            ck = QCheckBox()
            ck.toggled.connect(lambda _on: self._sync_sel())
            self.k_checks[key] = ck
            h.addWidget(ck)
            col = QVBoxLayout()
            col.setSpacing(2)
            a = QLabel(name)
            a.setStyleSheet("font-size:14px;font-weight:700;")
            col.addWidget(a)
            d = QLabel(desc)
            d.setObjectName("Muted")
            d.setWordWrap(True)
            col.addWidget(d)
            h.addLayout(col, 1)
            sz = QLabel("…")
            sz.setStyleSheet("font-size:14px;font-weight:700;color:#c8c8d8;")
            sz.setMinimumWidth(80)
            sz.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            h.addWidget(sz)
            b = QPushButton("Очистить")
            b.setObjectName("Danger")
            b.clicked.connect(lambda _=False, k=key: self._clear(k))
            h.addWidget(b)
            self.k_rows[key] = sz
            v.addWidget(fr)
        v.addStretch(1)
        return w

    def _sync_sel(self):
        keys = [k for k, c in self.k_checks.items() if c.isChecked()]
        size = sum(int(self.k_rows[k].property("bytes") or 0) for k in keys)
        self.k_sel.setEnabled(bool(keys))
        self.k_sel.setText(f"Очистить выбранное · {_size(size)}" if keys else "Очистить выбранное")

    def _orphan_covers(self) -> list:
        d = DATA / "covers"
        if not d.exists():
            return []
        used = set()
        for t in self.win.library:
            if t.get("cover"):
                used.add(Path(str(t["cover"])).name)
        for pl in self.win.playlists.values():
            if pl.get("cover"):
                used.add(Path(str(pl["cover"])).name)
            for t in pl.get("tracks", []):
                if t.get("cover"):
                    used.add(Path(str(t["cover"])).name)
        return [p for p in d.iterdir() if p.is_file() and p.name not in used]

    def _refresh_cache_sizes(self):
        caches = self._caches()

        def work():
            for key, _n, paths, _d in caches:
                if key == "covers":
                    try:
                        size = sum(p.stat().st_size for p in self._orphan_covers())
                    except Exception:                              # noqa: BLE001
                        size = 0
                else:
                    size = sum(dir_size(p) for p in paths if p.exists())
                self.bus.sized.emit(key, size)
        threading.Thread(target=work, daemon=True).start()

    def _on_sized(self, key, size):
        lb = self.k_rows.get(key)
        if lb is not None:
            try:
                lb.setText(_size(size))
                lb.setProperty("bytes", size)
            except RuntimeError:
                return
        total = sum(int(l.property("bytes") or 0) for l in self.k_rows.values())
        self.k_status.setText(f"Можно освободить: {_size(total)}")
        self._sync_sel()

    def _clear(self, key):
        self._clear_many([key])

    def _clear_many(self, keys):
        ents = [c for c in self._caches() if c[0] in keys]
        if not ents:
            return
        if len(ents) == 1:
            q = f"Очистить «{ents[0][1]}»?\n{ents[0][3]}"
        else:
            q = "Очистить:\n" + "\n".join(f"  • {e[1]}" for e in ents)
        if QMessageBox.question(self, "Очистить", q) != QMessageBox.StandardButton.Yes:
            return
        for ent in ents:
            self._wipe(ent)
        if any(e[0] == "clips" for e in ents):
            threading.Thread(target=self._scan_clips, daemon=True).start()
        if any(e[0] == "img" for e in ents):
            try:
                import yamusic_theme
                yamusic_theme.clear_cover_cache()
            except Exception:                                      # noqa: BLE001
                pass
        for c in self.k_checks.values():
            c.setChecked(False)
        self._refresh_cache_sizes()

    def _wipe(self, ent):
        key = ent[0]
        if key in NET_MEDIA:                                       # отдельные файлы — удалить
            for p in ent[2]:
                try:
                    p.unlink()
                except OSError:
                    pass
            return
        if key == "clips":
            self._close_clip_player()
        if key == "covers":
            for p in self._orphan_covers():
                try:
                    p.unlink()
                except OSError:
                    pass
        else:
            for p in ent[2]:
                if not p.exists():
                    continue
                if p.is_dir():
                    for c in list(p.iterdir()):
                        try:
                            shutil.rmtree(c) if c.is_dir() else c.unlink()
                        except OSError:
                            pass
                else:
                    try:
                        if key == "lyrics":
                            p.write_text("{}", "utf-8")
                            self._reset_lyrics_cache()
                        else:
                            p.write_text("", "utf-8")
                    except OSError:
                        pass

    def _reset_lyrics_cache(self):
        c = self.win.__dict__.get("_lyr_cache")
        if isinstance(c, dict):
            c.clear()


def open_cleanup(win, tab=0):
    try:
        dlg = CleanupDialog(win, tab)
        dlg.exec()
    except Exception as e:                                         # noqa: BLE001
        QMessageBox.warning(win, "Очистка", f"Не открылось: {e}")
