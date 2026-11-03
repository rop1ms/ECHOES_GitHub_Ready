# ya_online.py
"""
Онлайн-поиск для темы Echoes Music: YouTube Music + SoundCloud без ссылок.

  • OnlineResults   — блок результатов на странице «Поиск» (режим «В интернете»):
                      профили исполнителей (круглые карточки) и треки с кнопками
                      «скачать в плейлист» и «скачать и слушать»;
  • OnlineTrackRow  — строка трека: обложка, название, исполнитель, значок
                      источника, прослушивания, длительность, состояние загрузки;
  • OnlineProfilePage — профиль исполнителя с YT Music / SoundCloud: аватар,
                      подписчики, описание, все треки, «скачать всё в плейлист».

Сеть — в фоновых потоках (online_search), результат в GUI через
win._ui_bridge. Скачивание — очередь MainWindow._quick_download().
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import Qt, QRect, QRectF, QPointF, QSize, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
                             QComboBox, QSizePolicy, QGridLayout)

import yamusic_theme as Y

SRC_NAME = {"ytm": "YouTube Music", "yt": "YouTube", "sc": "SoundCloud"}
SRC_COLOR = {"ytm": "#ff3b30", "yt": "#ff3b30", "sc": "#ff7a00"}
_IMG_POOL = ThreadPoolExecutor(max_workers=6, thread_name_prefix="thumbs")


_THUMB_Q: list = []                                    # (пачка, номер, win, url, cb)
_THUMB_CV = threading.Condition()
_THUMB_SEQ = [0]
_THUMB_THREADS: list = []


def _thumb_worker():
    from artist_net import image
    while True:
        with _THUMB_CV:
            while not _THUMB_Q:
                _THUMB_CV.wait()
            # сначала — самая свежая пачка (открытая сейчас страница), внутри неё — по порядку:
            # обложки нового альбома не ждут, пока докачаются 77 карточек со страницы артиста
            i = max(range(len(_THUMB_Q)), key=lambda k: (_THUMB_Q[k][0], -_THUMB_Q[k][1]))
            _b, _n, win, url, cb = _THUMB_Q.pop(i)
        try:
            path = image(url)
        except Exception:                              # noqa: BLE001
            path = ""
        if path:
            def done(cb=cb, path=path):
                try:
                    cb(path)
                except RuntimeError:
                    pass                               # виджет уже удалён
            win._ui_bridge.call.emit(done)


def load_thumb(win, url, cb):
    """Картинка по ссылке → путь в кэше (artist_net.image), колбэк в GUI-потоке.
    Уже скачанная — сразу, без очереди."""
    if not url:
        return
    try:
        from artist_net import cached_image
        path = cached_image(url)
    except Exception:                                  # noqa: BLE001
        path = ""
    if path:
        try:
            cb(path)
        except RuntimeError:
            pass
        return
    import time as _time
    with _THUMB_CV:
        _THUMB_SEQ[0] += 1
        _THUMB_Q.append((int(_time.monotonic() * 2), _THUMB_SEQ[0], win, url, cb))
        if len(_THUMB_Q) > 600:                        # очень длинная очередь — старое не нужно
            _THUMB_Q.sort(key=lambda x: (x[0], -x[1]))
            del _THUMB_Q[:len(_THUMB_Q) - 600]
        _THUMB_CV.notify()
    while len(_THUMB_THREADS) < 6:
        th = threading.Thread(target=_thumb_worker, daemon=True, name="thumbs")
        th.start()
        _THUMB_THREADS.append(th)


def _meta(t: dict) -> dict:
    """Что передать в очередь закачки: подписи, альбом и обложка из поиска."""
    return {"title": t.get("title"), "artist": t.get("artist"), "album": t.get("album") or "",
            "thumb_url": t.get("thumb_url") or "", "duration": t.get("duration") or 0}


def _fmt(sec):
    sec = int(sec or 0)
    return f"{sec // 60}:{sec % 60:02d}" if sec else ""


# ══════════════════════════════════════════════════════════════════════════ #

def stream_default(win) -> bool:
    """Онлайн-треки по умолчанию играют потоком; в настройках можно вернуть «сначала скачать»."""
    return bool((getattr(win, "settings", None) or {}).get("stream_default", True))


def sibling_rows(row) -> list:
    """Строки треков того же списка в порядке на экране (видимые)."""
    par = row.parentWidget()
    if par is None:
        return [row]
    lay = par.layout()
    out = []
    if lay is not None:
        for i in range(lay.count()):
            w = lay.itemAt(i).widget()
            if isinstance(w, OnlineTrackRow) and (w.isVisible() or w is row or not row.isVisible()):
                out.append(w)
    return out or [row]


def listen_rows(rows):
    """«Слушать» на странице: весь список потоком с первого трека."""
    rows = [r for r in rows if r.isVisible()] or list(rows)
    if rows:
        rows[0].listen()


class OnlineTrackRow(QWidget):
    def __init__(self, shell, t: dict, parent=None, num=None, local_path=None):
        super().__init__(parent)
        self.shell, self.t = shell, t
        self.num = num                 # номер трека в альбоме — вместо обложки
        self.cover = ""
        self.state = ""            # "" | queued | progress | done | error
        self.pct = 0.0
        self.err = ""
        self._hover = False
        self.setFixedHeight(58)
        self.setMouseTracking(True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.setSpacing(4)
        lay.addStretch(1)
        streaming = stream_default(shell.win)
        self.btn_dl = Y.icon_button("download", "Скачать в выбранный плейлист", 36, 20)
        self.btn_dl.clicked.connect(lambda: self._download(False))
        self.btn_play = Y.icon_button("play", "Слушать сейчас (онлайн, без скачивания)" if streaming
                                      else "Скачать и сразу слушать", 36, 20)
        self.btn_play.clicked.connect(self.listen)
        self.btn_more = Y.icon_button("dots", "Ещё", 36, 20)
        self.btn_more.clicked.connect(self._menu)
        lay.addWidget(self.btn_dl)
        lay.addWidget(self.btn_play)
        lay.addWidget(self.btn_more)
        self._local = local_path or self.shell.online_local_path(t.get("url", ""))
        if self._local:
            self.state = "done"
        if num is None:
            load_thumb(shell.win, t.get("thumb_url", ""), self._set_cover)

    def _set_cover(self, path):
        self.cover = path
        self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()
        if not self.t.get("snip") and (self.t.get("url") or self.t.get("path")):   # навели — поток заранее
            try:
                import stream_core
                t = self.t
                self.shell.win.streams.hint(t if str(t.get("path", "")).startswith("stream:") else stream_core.make_track(t))
            except Exception as ex:                            # noqa: BLE001
                print("[stream] hint:", ex)

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    # ── слушать онлайн (по умолчанию) ── #
    def listen(self):
        """Слушать: уже в коллекции — файл; иначе — потоком весь список с этого трека (альбом, поиск, профиль).
        Если стриминг выключен в настройках — как раньше: скачать и слушать."""
        if self.state == "done" and self._local:
            self.shell.play_path(self._local)
            return
        if not stream_default(self.shell.win):
            self._download(True)
            return
        rows = sibling_rows(self)
        items, start = [], 0
        for r in rows:
            if r is self:
                start = len(items)
            it = r.as_queue_item()
            if it:
                items.append(it)
        self.shell.win.stream_tracks(items or [self.as_queue_item()], start, album=getattr(self, "album", "") or "")

    def as_queue_item(self) -> dict:
        """Элемент очереди: файл из коллекции или онлайн-трек потоком."""
        if self.state == "done" and self._local:
            lt = next((x for x in self.shell.win.library if str(x.get("path", "")) == str(self._local)), None)
            if lt is not None:
                return lt
        import stream_core
        return stream_core.make_track(self.t, getattr(self, "album", "") or "")

    def _is_now_playing(self) -> bool:
        try:
            cur = self.shell.win._current_track() or {}
        except Exception:                            # noqa: BLE001
            return False
        u = self.t.get("url")
        return bool(u) and str(cur.get("path", "")).startswith("stream:") and cur.get("url") == u

    def _play_next(self):
        if self.state == "done" and self._local:
            self.shell.win.queue_add_path(self._local, True)
            return
        self.shell.win.stream_queue_add(self.as_queue_item(), True)

    def _download(self, play):
        if self.state == "done" and self._local:
            self.shell.play_path(self._local)
            return
        if self.state in ("queued", "progress"):
            return
        self.state = "queued"
        self.update()
        self.shell.win._quick_download(self.t["url"], playlist=self.shell.online_target_playlist(),
                                       play=play, cb=self._on_state,
                                       meta=_meta(self.t))

    def _queue_after_download(self):
        win = self.shell.win
        if self.state == "done" and self._local:
            win.queue_add_path(self._local)
            return
        if self.state in ("queued", "progress"):
            return

        def cb(state, value):
            self._on_state(state, value)
            if state == "done":
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(600, lambda: win.queue_add_path(value))
        self.state = "queued"
        self.update()
        win._quick_download(self.t["url"], playlist=self.shell.online_target_playlist(),
                            play=False, cb=cb, meta=_meta(self.t))

    def _on_state(self, state, value):
        if state == "progress":
            self.state, self.pct = "progress", float(value)
        elif state == "done":
            self.state, self._local = "done", value
        elif state == "error":
            self.state, self.err = "error", str(value)
            self.setToolTip(f"Не удалось скачать: {value}")
        elif state == "queued":
            self.state = "queued"
        self.update()

    def _menu(self):
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtGui import QCursor
        m = QMenu(self)
        m.addAction("Слушать сейчас", self.listen)
        m.addAction("Играть следующим", self._play_next)
        m.addSeparator()
        m.addAction("Скачать и слушать", lambda: self._download(True))
        m.addAction("Скачать и добавить в очередь", self._queue_after_download)
        sub = m.addMenu("Скачать в плейлист")
        for n in self.shell.user_playlists():
            sub.addAction(n, lambda n=n: self.shell.win._quick_download(
                self.t["url"], playlist=n, cb=self._on_state,
                meta=_meta(self.t)))
        if self.t.get("artist"):
            m.addAction(f"Найти исполнителя «{self.t['artist']}»",
                        lambda: self.shell.online_find_artist(self.t["artist"], self.t.get("source")))
        m.addAction("Скопировать ссылку", lambda: __import__("PyQt6.QtWidgets", fromlist=["QApplication"])
                    .QApplication.clipboard().setText(self.t.get("url", "")))
        m.exec(QCursor.pos())

    def mouseDoubleClickEvent(self, e):
        self.listen()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        if self._hover:
            path = QPainterPath()
            path.addRoundedRect(QRectF(0, 2, W, H - 4), 10, 10)
            p.fillPath(path, QColor(255, 255, 255, 14))
        cov = QRect(10, 9, 40, 40)
        if self.num is not None:                    # треклист альбома: номер, как в Яндекс Музыке
            p.setFont(Y.ya_font(14, QFont.Weight.DemiBold))
            p.setPen(QColor(150, 150, 150))
            p.drawText(cov, Qt.AlignmentFlag.AlignCenter, str(self.num))
        else:
            p.drawPixmap(cov, Y.cover_pixmap(self.cover, 40, 6, seed=self.t.get("url", ""), lazy=self))
        tx = 64
        tw = int(W * 0.55) - tx
        f = Y.ya_font(14, QFont.Weight.DemiBold)
        p.setFont(f)
        now = self._is_now_playing()
        p.setPen(QColor(Y.YELLOW) if self.state == "done" or now else QColor(255, 255, 255))
        p.drawText(QRect(tx, 9, tw, 20), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(f).elidedText(self.t.get("title", ""), Qt.TextElideMode.ElideRight, tw))
        # значок источника + исполнитель
        src = self.t.get("source", "ytm")
        f2 = Y.ya_font(12)
        p.setFont(Y.ya_font(10, QFont.Weight.Bold))
        badge = SRC_NAME.get(src, src)
        bw = QFontMetrics(p.font()).horizontalAdvance(badge) + 12
        br = QRectF(tx, 31, bw, 16)
        p.setPen(Qt.PenStyle.NoPen)
        c = QColor(SRC_COLOR.get(src, "#888"))
        c.setAlpha(60)
        p.setBrush(c)
        p.drawRoundedRect(br, 8, 8)
        p.setPen(QColor(SRC_COLOR.get(src, "#ccc")).lighter(140))
        p.drawText(br, Qt.AlignmentFlag.AlignCenter, badge)
        p.setFont(f2)
        p.setPen(QColor(140, 140, 140))
        aw = tw - bw - 8
        p.drawText(QRect(int(br.right() + 8), 29, aw, 20), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(f2).elidedText(self.t.get("artist", ""), Qt.TextElideMode.ElideRight, aw))
        # состояние / прослушивания
        mx = int(W * 0.56)
        p.setFont(Y.ya_font(12, QFont.Weight.DemiBold))
        if self.state == "progress":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 40))
            p.drawRoundedRect(QRectF(mx, H / 2 - 2, 120, 4), 2, 2)
            p.setBrush(QColor(Y.YELLOW))
            p.drawRoundedRect(QRectF(mx, H / 2 - 2, 120 * min(1.0, self.pct / 100.0), 4), 2, 2)
            p.setPen(QColor(220, 220, 220))
            p.drawText(QRect(mx + 128, 0, 60, H), Qt.AlignmentFlag.AlignVCenter, f"{int(self.pct)}%")
        elif self.state == "queued":
            p.setPen(QColor(Y.YELLOW))
            p.drawText(QRect(mx, 0, 200, H), Qt.AlignmentFlag.AlignVCenter, "в очереди…")
        elif self.state == "done":
            p.setPen(QColor("#3ddc84"))
            if self.t.get("plays"):
                from artist_net import human
                msg = f"в коллекции · {human(self.t['plays'])} прослушиваний"
            else:
                msg = "в коллекции — нажмите, чтобы слушать"
            p.drawText(QRect(mx, 0, 260, H), Qt.AlignmentFlag.AlignVCenter, msg)
        elif self.state == "error":
            p.setPen(QColor("#ff6b6b"))
            p.drawText(QRect(mx, 0, 200, H), Qt.AlignmentFlag.AlignVCenter, "ошибка загрузки")
        elif now:
            p.setPen(QColor(Y.YELLOW))
            p.drawText(QRect(mx, 0, 220, H), Qt.AlignmentFlag.AlignVCenter, "сейчас играет · онлайн")
        elif self.t.get("plays"):
            from artist_net import human
            p.setPen(QColor(150, 150, 150))
            p.drawText(QRect(mx, 0, 200, H), Qt.AlignmentFlag.AlignVCenter, f"{human(self.t['plays'])} прослушиваний")
        d = _fmt(self.t.get("duration"))
        if d:
            p.setPen(QColor(140, 140, 140))
            p.setFont(Y.ya_font(12))
            p.drawText(QRect(W - 190, 0, 50, H), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, d)
        p.end()


# ══════════════════════════════════════════════════════════════════════════ #

class OnlineResults(QWidget):
    """Результаты онлайн-поиска (встраивается в страницу «Поиск»)."""

    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self.shell = shell
        self.setObjectName("YaPage")
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(0, 0, 0, 0)
        self.v.setSpacing(4)
        self._req = 0
        self.status = QLabel("")
        self.status.setObjectName("YaSub")
        self.v.addWidget(self.status)
        self.box = QWidget()
        self.box.setObjectName("YaPage")
        self.bv = QVBoxLayout(self.box)
        self.bv.setContentsMargins(0, 0, 0, 0)
        self.bv.setSpacing(2)
        self.v.addWidget(self.box)

    def search(self, query, source):
        self._req += 1
        req = self._req
        Y._clear_layout(self.bv)
        if not query:
            self.status.setText("Введите название трека или исполнителя — поищем на YouTube Music и SoundCloud")
            return
        where = {"all": "YouTube Music и SoundCloud", "ytm": "YouTube Music", "sc": "SoundCloud"}[source]
        self.status.setText(f"Ищем «{query}» на {where}…")
        for i in range(6):
            sk = QFrame()
            sk.setFixedHeight(52)
            sk.setStyleSheet("background:rgba(255,255,255,0.05);border-radius:10px;")
            self.bv.addWidget(sk)
        win = self.shell.win

        def work():
            try:
                import online_search
                res = online_search.search(query, source)
            except Exception as e:                     # noqa: BLE001
                res = {"tracks": [], "artists": [], "errors": [str(e)]}
            win._ui_bridge.call.emit(lambda: req == self._req and self._show(query, res))
        threading.Thread(target=work, daemon=True).start()

    def _show(self, query, res):
        Y._clear_layout(self.bv)
        tracks, artists = res.get("tracks", []), res.get("artists", [])
        albums, playlists = res.get("albums", []), res.get("playlists", [])
        msg = f"Найдено в интернете: {len(tracks)} треков, {len(artists)} исполнителей"
        if albums or playlists:
            msg += f", {len(albums)} альбомов, {len(playlists)} плейлистов"
        if res.get("errors"):
            msg += "  ·  " + ", ".join(res["errors"])
        self.status.setText(msg)
        if artists:
            self.bv.addWidget(Y.section_title("Исполнители"))
            row = Y.HRow()
            from artist_net import human
            for a in artists:
                sub = SRC_NAME.get(a["source"], a["source"])
                if a.get("followers"):
                    sub += f" · {human(a['followers'])}"
                c = Y.Card(a.get("name", ""), sub, "", 128, circle=True, seed=a.get("url", ""))
                c.clicked.connect(lambda a=a: self.shell.open_online_artist(a))
                load_thumb(self.shell.win, a.get("thumb_url", ""),
                           lambda path, c=c: (setattr(c, "cover", path), c.update()))
                row.add(c)
            self.bv.addWidget(row)
        self._releases("Альбомы", albums, False)
        self._releases("Публичные плейлисты", playlists, True)
        if tracks:
            self.bv.addWidget(Y.section_title("Треки"))
            for t in tracks:
                self.bv.addWidget(OnlineTrackRow(self.shell, t))
            if stream_default(self.shell.win):               # первые результаты — заранее, клик играет сразу
                import stream_core
                for t in tracks[:3]:
                    if t.get("url") and not t.get("snip"):
                        self.shell.win.streams.prefetch(stream_core.make_track(t))
        if not tracks and not artists and not albums and not playlists:
            lb = QLabel("Ничего не нашлось. Попробуйте иначе написать название или выберите другой источник.")
            lb.setObjectName("YaSub")
            self.bv.addWidget(lb)

    def _releases(self, title, rels, playlists):
        """Строка карточек: альбомы или публичные плейлисты с площадок; щелчок — треклист (ReleasePage)."""
        if not rels:
            return
        self.bv.addWidget(Y.section_title(title))
        row = Y.HRow()
        for r in rels:
            if playlists:
                bits = [SRC_NAME.get(r.get("src", ""), ""), r.get("artist", "")]
                if r.get("count"):
                    bits.append(f"{r['count']} треков")
            else:
                bits = [r.get("artist", ""), r.get("year", ""), _type_ru(r.get("type"))]
            c = Y.Card(r.get("title", ""), " · ".join(b for b in bits if b), "", 150, seed=r.get("title", ""))
            c.setToolTip(("Плейлист" if playlists else "Альбом") + f" на {SRC_NAME.get(r.get('src', ''), 'площадке')}"
                         " — открыть треклист")
            c.clicked.connect(lambda r=r: self.shell.open_release("album", r.get("artist", ""), rel=r))
            if r.get("thumb_url"):
                load_thumb(self.shell.win, r["thumb_url"], lambda path, c=c: (setattr(c, "cover", path), c.update()))
            row.add(c)
        self.bv.addWidget(row)


# ══════════════════════════════════════════════════════════════════════════ #

class OnlineProfilePage(Y.Page):
    """Профиль исполнителя с YouTube Music / SoundCloud."""

    def __init__(self, shell, parent=None):
        super().__init__(parent, margins=(0, 0, 0, 24))
        self.shell = shell
        self.artist = None
        self._req = 0

    def load(self, artist: dict):
        self.artist = artist
        self._req += 1
        req = self._req
        self._render(None, loading=True)
        win = self.shell.win

        def work():
            try:
                import online_search
                d = online_search.profile(artist)
            except Exception as e:                     # noqa: BLE001
                d = {"name": artist.get("name", ""), "tracks": [], "error": str(e)}
            win._ui_bridge.call.emit(lambda: req == self._req and self._render(d, loading=False))
        threading.Thread(target=work, daemon=True).start()

    def _render(self, d, loading):
        from artist_net import human
        self.clear()
        a = self.artist or {}
        d = d or {}
        src = a.get("source", "ytm")
        hdr = Y.ArtistHeader()
        hdr.name = d.get("name") or a.get("name", "")
        hdr.kind = f"Исполнитель · {SRC_NAME.get(src, src)}" + (" · подтверждён" if d.get("verified") else "")
        parts = []
        fol = d.get("followers") or a.get("followers")
        if fol:
            parts.append(f"{human(fol)} подписчиков")
        n = len(d.get("tracks") or [])
        if d.get("tracks_count"):
            parts.append(f"{d['tracks_count']} треков")
        elif n:
            parts.append(f"{n} треков")
        if d.get("views"):
            parts.append(str(d["views"]))
        if d.get("city"):
            parts.append(d["city"])
        hdr.stats = "  ·  ".join(parts)
        if d.get("error"):
            hdr.stats = "Сервер не ответил: " + str(d["error"])[:80]
        hdr.sources = [(SRC_NAME.get(src, src), "wait" if loading else ("ok" if not d.get("error") else "off"))]
        hdr.set_loading(loading)
        load_thumb(self.shell.win, d.get("thumb_url") or a.get("thumb_url", ""), hdr.set_picture)
        if d.get("tracks"):
            bl = QPushButton("  Слушать")
            bl.setObjectName("YaYellow")
            bl.setIcon(Y.ya_icon("play", 18, "#000000"))
            bl.setMinimumHeight(40)
            bl.setToolTip("Все треки исполнителя онлайн, сразу — без скачивания")
            bl.clicked.connect(lambda: listen_rows(self.findChildren(OnlineTrackRow)))
            hdr.btn_row.addWidget(bl)
        b = QPushButton("  Скачать всё в коллекцию")
        b.setIcon(Y.ya_icon("download", 18))
        b.setMinimumHeight(40)
        b.setEnabled(bool(d.get("tracks")))
        b.clicked.connect(self._download_all)
        hdr.btn_row.addWidget(b)
        b2 = QPushButton("  Карточка исполнителя")
        b2.setIcon(Y.ya_icon("note", 18))
        b2.setMinimumHeight(40)
        b2.setToolTip("Поклонники, альбомы, похожие исполнители (Deezer / Wikipedia / Last.fm)")
        b2.clicked.connect(lambda: self.shell.open_artist(hdr.name))
        hdr.btn_row.addWidget(b2)
        hdr.btn_row.addStretch(1)
        back = Y.icon_button("back", "Назад", 36, 20)
        back.setParent(hdr)
        back.move(14, 14)
        back.setStyleSheet("QPushButton{border-radius:18px;}")
        back.clicked.connect(self.shell.go_back)
        self.v.addWidget(hdr)

        body = QWidget()
        body.setObjectName("YaPage")
        bv = QVBoxLayout(body)
        bv.setContentsMargins(32, 4, 32, 0)
        bv.setSpacing(2)
        self.v.addWidget(body)
        if loading:
            for i in range(6):
                sk = QFrame()
                sk.setFixedHeight(52)
                sk.setStyleSheet("background:rgba(255,255,255,0.05);border-radius:10px;")
                bv.addWidget(sk)
        else:
            if src == "ytm" and a.get("id"):           # альбомы и синглы — открываются с треками и прослушиваниями
                bv.addWidget(DiscographyBlock(self.shell, hdr.name, browse_id=a.get("id")))
            if d.get("tracks"):
                bv.addWidget(Y.section_title(f"Треки · {len(d['tracks'])}"))
                for t in d["tracks"]:
                    if not t.get("artist"):
                        t["artist"] = hdr.name
                    bv.addWidget(OnlineTrackRow(self.shell, t))
            if d.get("desc"):
                bv.addWidget(Y.section_title("Об исполнителе"))
                lb = QLabel(d["desc"][:1500])
                lb.setWordWrap(True)
                lb.setStyleSheet("font-size:14px;color:#d0d0d0;")
                bv.addWidget(lb)
        self.v.addStretch(1)
        self._data = d

    def _download_all(self):
        d = getattr(self, "_data", None) or {}
        name = None
        rows = self.findChildren(OnlineTrackRow)
        for r in rows:
            if r.state in ("", "error"):
                r.state = "queued"
                r.update()
                self.shell.win._quick_download(r.t["url"], playlist=name, cb=r._on_state,
                                               meta=_meta(r.t))


# ══════════════════════════════════════════════════════════════════════════ #
#  Дискография исполнителя: альбомы, синглы, все треки (YouTube Music)
# ══════════════════════════════════════════════════════════════════════════ #

_TYPE_RU = {"album": "Альбом", "single": "Сингл", "ep": "EP", "альбом": "Альбом", "сингл": "Сингл"}


def _type_ru(s) -> str:
    return _TYPE_RU.get(str(s or "").strip().lower(), str(s or "") or "Релиз")


def unreleased_sc(artist, ytm_bid=None, disco=None) -> list:
    """Анрелизы: треки исполнителя на SoundCloud, которых нет среди его песен и релизов на YouTube Music
    (сниппеты, демки, слитое, ремиксы, фриты). Самые прослушиваемые — первыми."""
    import online_search
    from track_identity import basic_clean, norm, similarity, strip_extras
    sid = online_search.sc_artist_id(artist)
    if not sid:
        return []
    sc = online_search.sc_all_songs(sid)
    if not sc:
        return []
    known = []
    if ytm_bid:
        known += [t.get("title", "") for t in online_search.ytm_all_songs(ytm_bid)]
        d = disco or online_search.ytm_discography(ytm_bid)
        known += [r.get("title", "") for r in (d.get("albums") or []) + (d.get("singles") or [])]
    import re as _re

    def core(x):                                           # без «x 9mice», «ft …», «prod …» — как выходит официально
        x = strip_extras(basic_clean(x)).lower()
        x = _re.split(r"\s(?:x|ft\.?|feat\.?|prod\.?|w/)(?=\s)", " " + x + " ")[0].strip() or x
        return norm(x)
    kn = [core(k) for k in known if k]
    kset = set(kn)
    out, seen = [], set()
    for t in sc:
        raw = t.get("title", "")
        # «Артист - Трек» в названии на SoundCloud
        ttl = raw.split(" - ", 1)[1] if " - " in raw and artist.lower() in raw.split(" - ", 1)[0].lower() else raw
        k = core(ttl)
        if not k or k in seen:
            continue
        seen.add(k)
        if k in kset or any(similarity(k, x) >= 0.88 for x in kn):
            continue                                       # вышло официально
        out.append(t)
    out.sort(key=lambda t: -(int(t.get("plays") or 0)))
    return out


def _local_index(shell, artist):
    """Треки исполнителя из коллекции: (название, трек) — чтобы отметить «в коллекции»."""
    al = (artist or "").lower()
    out = []
    for t in shell.win.library:
        a = (t.get("artist") or "").lower()
        if al and (al == Y._artist_main(t.get("artist", "")).lower() or al in a):
            out.append((Y.sanitize_title(t.get("title", "")), t))
    return out


def _local_path(index, title):
    if not index:
        return None
    try:
        from track_identity import similarity
    except Exception:                                  # noqa: BLE001
        return None
    best, bs = None, 0.0
    for name, t in index:
        s = similarity(title, name)
        if s > bs:
            best, bs = t, s
    return best.get("path") if best is not None and bs >= 0.84 else None


class DiscographyBlock(QWidget):
    """«Альбомы», «Синглы и EP» и «Все треки» исполнителя. Грузится в фоне; если YouTube Music
    не ответил — альбомы с Deezer (их треклист ищется по названию)."""

    def __init__(self, shell, artist, browse_id=None, fallback=None, parent=None):
        super().__init__(parent)
        self.shell, self.artist, self.browse_id = shell, artist, browse_id
        self.fallback = list(fallback or [])
        self.setObjectName("YaPage")
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(0, 0, 0, 0)
        self.v.setSpacing(4)
        self.sc_id = None
        self.status = QLabel("Загружаю альбомы и синглы (YouTube Music, SoundCloud)…")
        self.status.setObjectName("YaSub")
        self.v.addWidget(self.status)
        win = shell.win

        def work():
            d, bid, unrel = {}, browse_id, []
            try:
                import online_search
                bid = bid or online_search.ytm_artist_id(artist)
                if bid:
                    d = online_search.ytm_discography(bid)
            except Exception as e:                     # noqa: BLE001
                print("[discography]", e)
            try:
                unrel = unreleased_sc(artist, bid if (d.get("albums") or d.get("singles")) else None, d)
            except Exception as e:                     # noqa: BLE001
                print("[discography unreleased]", e)
            if not (d.get("albums") or d.get("singles")):
                # на YouTube Music исполнителя нет (или он не ответил) — его релизы на SoundCloud
                try:
                    import online_search
                    sid = online_search.sc_artist_id(artist)
                    if sid:
                        d = online_search.sc_discography(sid)
                except Exception as e:                 # noqa: BLE001
                    print("[discography sc]", e)

            def done():
                try:
                    self._fill(d, bid, unrel)
                except RuntimeError:
                    pass                               # страницу уже перерисовали
            win._ui_bridge.call.emit(done)
        threading.Thread(target=work, daemon=True).start()

    def _cards(self, title, rels, kind):
        if not rels:
            return
        self.v.addWidget(Y.section_title(f"{title} · {len(rels)}"))
        row = Y.HRow()
        for r in rels:
            sub = " · ".join(x for x in (r.get("year", ""), _type_ru(r.get("type") or kind)) if x)
            c = Y.Card(r.get("title", ""), sub, r.get("cover", ""), 150, seed=r.get("title", ""))
            c.setToolTip("Открыть: треки и прослушивания")
            c.clicked.connect(lambda r=r: self.shell.open_release("album", self.artist, rel=r))
            if r.get("thumb_url") and not r.get("cover"):
                load_thumb(self.shell.win, r["thumb_url"], lambda path, c=c: (setattr(c, "cover", path), c.update()))
            row.add(c)
        self.v.addWidget(row)

    def _unrel_tracks(self, tracks):
        """Анрелизы — обычный список треков с прямым воспроизведением."""
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 6, 0, 6)
        layout.setSpacing(2)
        index = _local_index(self.shell, self.artist)
        for track in tracks:
            item = dict(track)
            item["artist"] = item.get("artist") or self.artist
            item["source"] = item.get("source") or "sc"
            item["thumb_url"] = item.get("thumb_url") or item.get("cover_url") or ""
            layout.addWidget(OnlineTrackRow(self.shell, item, local_path=_local_path(index, item.get("title", ""))))
        return box

    def _fill(self, d, bid, unrel=None):
        self.browse_id = bid if d.get("src") != "sc" else None
        self.sc_id = d.get("sc_id") if d.get("src") == "sc" else None
        Y._clear_layout(self.v)
        unrel = list(unrel or [])
        if unrel:
            # две вкладки: официальные релизы и анрелизы с SoundCloud
            tabs = QHBoxLayout()
            tabs.setSpacing(8)
            b_rel = QPushButton("Релизы")
            b_un = QPushButton(f"Анрелизы · {len(unrel)}")
            for b in (b_rel, b_un):
                b.setObjectName("YaChip")
                b.setCheckable(True)
                b.setMinimumHeight(32)
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                tabs.addWidget(b)
            tabs.addStretch(1)
            self.v.addLayout(tabs)
            rel_box, un_box = QWidget(), QWidget()
            rv, uv = QVBoxLayout(rel_box), QVBoxLayout(un_box)
            for lay in (rv, uv):
                lay.setContentsMargins(0, 0, 0, 0)
                lay.setSpacing(4)
            note = QLabel("Треки с SoundCloud исполнителя, которых нет на YouTube Music: сниппеты, демки, слитое, фриты")
            note.setObjectName("YaSub")
            note.setWordWrap(True)
            uv.addWidget(note)
            uv.addWidget(self._unrel_tracks(unrel))
            self.v.addWidget(rel_box)
            self.v.addWidget(un_box)

            def show(un):
                b_un.setChecked(un)
                b_rel.setChecked(not un)
                un_box.setVisible(un)
                rel_box.setVisible(not un)
            b_rel.clicked.connect(lambda: show(False))
            b_un.clicked.connect(lambda: show(True))
            show(False)
            target = rv
        else:
            target = self.v
        self._fill_releases(d, target)

    def _fill_releases(self, d, v):
        """Альбомы и синглы (в свой столбец v: при вкладках — во вкладку «Релизы»)."""
        old_v, self.v = self.v, v
        albums, singles = d.get("albums") or [], d.get("singles") or []
        if not albums and not singles and self.fallback:  # YouTube Music не ответил — альбомы Deezer
            albums = [{"title": a.get("title", ""), "year": a.get("year", ""), "type": a.get("type", ""),
                       "cover": a.get("cover", ""), "browseId": None} for a in self.fallback]
        bar = QHBoxLayout()
        b = QPushButton("  Все треки исполнителя")
        b.setObjectName("YaYellow")
        b.setIcon(Y.ya_icon("note", 18, "#000000"))
        b.setMinimumHeight(38)
        b.setToolTip("Вся дискография: каждый трек, альбом и число прослушиваний")
        b.clicked.connect(lambda: self.shell.open_release("all", self.artist, browse_id=self.browse_id
                                                          or (("sc", self.sc_id) if self.sc_id else None)))
        bar.addWidget(b)
        bar.addStretch(1)
        if self.sc_id:
            src = QLabel("Релизы с SoundCloud — на YouTube Music исполнителя нет")
            src.setObjectName("YaSub")
            bar.addWidget(src)
        self.v.addLayout(bar)
        self._cards("Альбомы", albums, "album")
        self._cards("Синглы и EP", singles, "single")
        if not albums and not singles:
            lb = QLabel("Альбомы не нашлись ни на YouTube Music, ни на SoundCloud")
            lb.setObjectName("YaSub")
            self.v.addWidget(lb)
        self.v = old_v


class ReleasePage(Y.Page):
    """Альбом или все треки исполнителя: треклист с прослушиваниями, «в коллекции», скачать всё."""

    SORTS = [("popular", "По популярности"), ("title", "По названию"), ("album", "По альбому"),
             ("source", "Как на YouTube Music")]

    def __init__(self, shell, parent=None):
        super().__init__(parent, margins=(0, 0, 0, 24))
        self.shell = shell
        self._req = 0
        self.rows: list = []
        self.tracks: list = []
        self.mode = "album"
        self.artist = ""

    # ── загрузка ── #
    def open_album(self, artist, rel):
        self.mode, self.artist, self.rel = "album", artist, dict(rel)
        self._req += 1
        req = self._req
        self._render_album({"title": rel.get("title", ""), "year": rel.get("year", ""),
                            "type": rel.get("type", ""), "tracks": []}, loading=True, cover=rel.get("cover", ""))
        win = self.shell.win

        def work():
            d = {}
            try:
                import online_search
                if rel.get("sc_id"):                   # альбом/EP с SoundCloud
                    d = online_search.sc_album(rel["sc_id"])
                elif rel.get("sc_track"):              # отдельный трек SoundCloud
                    d = {"title": rel.get("title", ""), "year": rel.get("year", ""), "type": "Трек",
                         "tracks": [dict(rel["sc_track"], num=1)]}
                elif rel.get("ytm_pl"):                # публичный плейлист YouTube Music (из поиска)
                    d = online_search.ytm_playlist(rel["ytm_pl"])
                elif rel.get("browseId"):
                    d = online_search.ytm_album(rel["browseId"])
                else:                                  # альбом с Deezer — ищем его на YouTube Music
                    d = online_search.album_tracks(artist, rel.get("title", "")) or {}
                    for i, t in enumerate(d.get("tracks", [])):
                        t.setdefault("num", i + 1)
            except Exception as e:                     # noqa: BLE001
                d = {"error": str(e)}
            win._ui_bridge.call.emit(lambda: req == self._req and self._render_album(d, loading=False,
                                                                                  cover=rel.get("cover", "")))
        threading.Thread(target=work, daemon=True).start()

    def open_all(self, artist, browse_id=None):
        self.mode, self.artist = "all", artist
        self._req += 1
        req = self._req
        self.tracks = []
        self._render_all(loading=True)
        win = self.shell.win

        def work():
            tracks, bid, disco = [], browse_id, {}
            sc_uid = bid[1] if isinstance(bid, tuple) and bid and bid[0] == "sc" else None
            if sc_uid is not None:
                bid = None
            try:
                import online_search
                if sc_uid is None:
                    bid = bid or online_search.ytm_artist_id(artist)
                    if bid:
                        tracks = online_search.ytm_all_songs(bid)
                        disco = online_search.ytm_discography(bid)
            except Exception as e:                     # noqa: BLE001
                print("[all tracks]", e)
            if not tracks:                             # нет на YouTube Music — все треки с SoundCloud
                try:
                    import online_search
                    sc_uid = sc_uid or online_search.sc_artist_id(artist)
                    if sc_uid:
                        tracks = online_search.sc_all_songs(sc_uid)
                        disco = {}
                except Exception as e:                 # noqa: BLE001
                    print("[all tracks sc]", e)
            win._ui_bridge.call.emit(lambda: req == self._req and self._got_all(tracks, disco, req))
        threading.Thread(target=work, daemon=True).start()

    def _got_all(self, tracks, disco, req):
        for i, t in enumerate(tracks):
            t["_order"] = i
        self.tracks = tracks
        self._render_all(loading=False)
        rels = (disco.get("albums") or []) + (disco.get("singles") or [])
        if tracks and rels:
            self._count_plays(rels[:80], req)

    def _count_plays(self, rels, req):
        """Прослушивания есть только у треков внутри альбомов — собираем их по всем релизам в фоне."""
        win = self.shell.win
        total = len(rels)

        def work():
            import online_search
            from track_identity import norm
            got, n = {}, 0
            with ThreadPoolExecutor(max_workers=4) as ex:
                for alb in ex.map(lambda r: online_search.ytm_album(r["browseId"]), rels):
                    n += 1
                    for t in (alb or {}).get("tracks", []):
                        if t.get("plays"):
                            for k in (t.get("id"), norm(t.get("title", ""))):
                                if k:
                                    got[k] = max(got.get(k, 0), t["plays"])
                    if n % 6 == 0 or n == total:
                        snap, k_n = dict(got), n
                        win._ui_bridge.call.emit(lambda s=snap, k=k_n: req == self._req and self._apply_plays(s, k, total))
                    if req != self._req:
                        return
        threading.Thread(target=work, daemon=True).start()

    def _apply_plays(self, got, n, total):
        from track_identity import norm
        for t in self.tracks:
            v = got.get(t.get("id")) or got.get(norm(t.get("title", "")))
            if v:
                t["plays"] = v
        for r in self.rows:
            try:
                r.update()
            except RuntimeError:
                pass
        done = n >= total
        self.status.setText(f"{len(self.tracks)} треков" + ("" if done else f"  ·  считаю прослушивания: {n}/{total} релизов"))
        if done and self.sort_cb.currentData() == "popular":
            self._resort()

    # ── шапка ── #
    def _header(self, name, kind, stats, cover="", thumb=""):
        hdr = Y.ArtistHeader()
        hdr.name = name
        hdr.kind = kind
        hdr.stats = stats
        if cover:
            hdr.set_picture(cover)
        elif thumb:
            load_thumb(self.shell.win, thumb, hdr.set_picture)
        back = Y.icon_button("back", "Назад", 36, 20)
        back.setParent(hdr)
        back.move(14, 14)
        back.setStyleSheet("QPushButton{border-radius:18px;}")
        back.clicked.connect(self.shell.go_back)
        self.v.addWidget(hdr)
        return hdr

    def _body(self):
        body = QWidget()
        body.setObjectName("YaPage")
        bv = QVBoxLayout(body)
        bv.setContentsMargins(32, 4, 32, 0)
        bv.setSpacing(2)
        self.v.addWidget(body)
        return bv

    # ── альбом ── #
    def _render_album(self, d, loading, cover=""):
        from artist_net import human
        self.clear()
        self.rows = []
        tr = d.get("tracks") or []
        self.tracks = tr
        dur = sum(int(t.get("duration") or 0) for t in tr)
        plays = sum(int(t.get("plays") or 0) for t in tr)
        is_pl = (d.get("type") or self.rel.get("type")) == "Плейлист"
        parts = [x for x in (d.get("year", ""), _type_ru(d.get("type"))) if x]
        stats = [f"{len(tr)} треков"] if tr else []
        if dur:
            stats.append(f"{dur // 60} мин")
        if plays:
            stats.append(f"{human(plays)} прослушиваний")
        if d.get("error"):
            stats = ["Не удалось загрузить: " + str(d["error"])[:80]]
        elif not loading and not tr:
            stats = ["Плейлист пуст или недоступен" if is_pl else "Альбом не нашёлся на YouTube Music"]
        hdr = self._header(d.get("title") or self.rel.get("title", ""),
                           " · ".join([self.artist] + parts), "  ·  ".join(stats), cover, d.get("thumb_url", ""))
        hdr.set_loading(loading)
        if tr:
            b = QPushButton("  Слушать")
            b.setObjectName("YaYellow")
            b.setIcon(Y.ya_icon("play", 18, "#000000"))
            b.setMinimumHeight(40)
            b.clicked.connect(self._play_first)
            hdr.btn_row.addWidget(b)
            b = QPushButton("  Сохранить как плейлист" if is_pl else "  Скачать альбом")
            b.setIcon(Y.ya_icon("download", 18))
            b.setMinimumHeight(40)
            b.setToolTip("Скачать треки и собрать из них плейлист в ECHOES" if is_pl else
                         "Скачать треки в коллекцию без создания плейлиста")
            b.clicked.connect(lambda: self._download_all(d.get("title") or "Альбом", d, keep=is_pl))
            hdr.btn_row.addWidget(b)
        hdr.btn_row.addStretch(1)
        bv = self._body()
        if loading:
            for _ in range(8):
                sk = QFrame()
                sk.setFixedHeight(52)
                sk.setStyleSheet("background:rgba(255,255,255,0.05);border-radius:10px;")
                bv.addWidget(sk)
        idx = _local_index(self.shell, self.artist)
        for t in tr:
            r = OnlineTrackRow(self.shell, t, num=t.get("num"), local_path=_local_path(idx, t.get("title", "")))
            r.album = "" if is_pl else (d.get("title") or "")
            if not t.get("thumb_url") and d.get("thumb_url"):
                t["thumb_url"] = d["thumb_url"]
            self.rows.append(r)
            bv.addWidget(r)
        self.v.addStretch(1)
        self.verticalScrollBar().setValue(0)

    # ── все треки ── #
    def _render_all(self, loading):
        self.clear()
        self.rows = []
        tr = self.tracks
        stats = f"{len(tr)} треков" if tr else ("Загружаю все треки…" if loading else "Треки не нашлись на YouTube Music")
        hdr = self._header(self.artist, "Все треки исполнителя · YouTube Music", stats,
                           thumb=(tr[0].get("thumb_url") if tr else ""))
        hdr.set_loading(loading)
        if tr:
            bl = QPushButton("  Слушать")
            bl.setObjectName("YaYellow")
            bl.setIcon(Y.ya_icon("play", 18, "#000000"))
            bl.setMinimumHeight(40)
            bl.clicked.connect(lambda: listen_rows(self.rows))
            hdr.btn_row.addWidget(bl)
            b = QPushButton("  Скачать всё в коллекцию")
            b.setIcon(Y.ya_icon("download", 18))
            b.setMinimumHeight(40)
            b.clicked.connect(lambda: self._download_all(self.artist, {}))
            hdr.btn_row.addWidget(b)
        hdr.btn_row.addStretch(1)
        bv = self._body()
        tools = QHBoxLayout()
        from PyQt6.QtWidgets import QLineEdit
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Найти трек исполнителя…")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._apply_filter)
        tools.addWidget(self.filter, 1)
        self.sort_cb = QComboBox()
        for k, name in self.SORTS:
            self.sort_cb.addItem(name, k)
        self.sort_cb.currentIndexChanged.connect(self._resort)
        tools.addWidget(self.sort_cb)
        bv.addLayout(tools)
        self.status = QLabel("")
        self.status.setObjectName("YaSub")
        bv.addWidget(self.status)
        self.list_box = QWidget()
        self.list_box.setObjectName("YaPage")
        self.lv = QVBoxLayout(self.list_box)
        self.lv.setContentsMargins(0, 0, 0, 0)
        self.lv.setSpacing(2)
        bv.addWidget(self.list_box)
        if loading:
            for _ in range(8):
                sk = QFrame()
                sk.setFixedHeight(52)
                sk.setStyleSheet("background:rgba(255,255,255,0.05);border-radius:10px;")
                self.lv.addWidget(sk)
        idx = _local_index(self.shell, self.artist)
        for t in tr:
            r = OnlineTrackRow(self.shell, t, local_path=_local_path(idx, t.get("title", "")))
            self.rows.append(r)
            self.lv.addWidget(r)
        if tr:
            self.status.setText(f"{len(tr)} треков  ·  прослушивания подтягиваются из альбомов…")
        self.v.addStretch(1)
        self.verticalScrollBar().setValue(0)

    def _resort(self, *_):
        if self.mode != "all" or not self.rows:
            return
        k = self.sort_cb.currentData()
        key = {"popular": lambda r: -int(r.t.get("plays") or 0),
               "title": lambda r: (r.t.get("title") or "").lower(),
               "album": lambda r: ((r.t.get("album") or "").lower(), r.t.get("_order", 0)),
               "source": lambda r: r.t.get("_order", 0)}[k]
        self.rows.sort(key=key)
        for r in self.rows:
            self.lv.removeWidget(r)
        for r in self.rows:
            self.lv.addWidget(r)

    def _apply_filter(self, text):
        from search_util import matches
        for r in self.rows:
            r.setVisible(matches(text, r.t.get("title"), r.t.get("album")))

    # ── действия ── #
    def _play_first(self):
        for r in self.rows:
            if r.state == "done" and r._local:
                self.shell.play_path(r._local)
                return
        if self.rows:
            self.rows[0].listen()

    def _download_all(self, playlist, d, keep=False):
        playlist = playlist if keep else None              # публичный плейлист — своим плейлистом в ECHOES
        n = 0
        win = self.shell.win
        if playlist:                                       # что уже есть в коллекции — сразу в плейлист
            pl = win.playlists.setdefault(playlist, {"tracks": [], "desc": "Публичный плейлист из поиска", "cover": ""})
            have = {x.get("path") for x in pl["tracks"]}
            for r in self.rows:
                loc = getattr(r, "_local", None)
                if loc and loc not in have:
                    t = next((x for x in win.library if x.get("path") == loc), None)
                    if t is not None:
                        pl["tracks"].append(dict(t))
                        have.add(loc)
            win._ya_save_playlists()
        for r in self.rows:
            if r.state in ("", "error") and r.isVisible():
                r.state = "queued"
                r.update()
                meta = _meta(r.t)
                if d.get("thumb_url"):
                    meta["thumb_url"] = d["thumb_url"]
                self.shell.win._quick_download(r.t["url"], playlist=playlist, cb=r._on_state, meta=meta)
                n += 1
        self.shell.win._toast((f"Скачиваю {n} треков в плейлист «{playlist}»" if playlist else
                               f"Скачиваю {n} треков в коллекцию") if n else "Все треки уже в коллекции")
