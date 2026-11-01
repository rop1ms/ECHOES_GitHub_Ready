# net_threads.py
"""
Echoes.NET: вкладка «Треды» — обсуждения как на Reddit.

  * Список: слева колонка голосов (▲ счёт ▼), справа флер, заголовок, автор и время, начало текста,
    трек (если прикреплён), число комментариев. Сортировка: горячее / новое / топ / обсуждаемое,
    фильтр по флеру и поиск.
  * Тред: полный текст, голоса, дерево комментариев с линиями веток — клик по линии сворачивает
    ветку, «Ответить» открывает поле прямо под комментарием.
  * Создать тред: заголовок, флер, текст, трек из коллекции.

Тред — обычный подписанный пост вида «thread» (заголовок + флер): расходится друзьям так же, как
посты, голоса и комментарии — те же, что в ленте.
"""
from __future__ import annotations

import time

from PyQt6.QtCore import Qt, QRectF, QSize, QTimer
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                             QPlainTextEdit, QPushButton, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget)

UP, DOWN = "#ff4500", "#7193ff"                               # цвета голосов как на Reddit
FLAIR_COLORS = {"Обсуждение": "#3d8bfd", "Вопрос": "#ff8a3d", "Музыка": "#ffdb1a", "Подборка": "#a66bff",
                "Новинки": "#3ddc84", "Мемы": "#ff4fa3", "Помощь": "#46d1e0"}
SORTS = (("hot", "Горячее"), ("new", "Новое"), ("top", "Топ"), ("talk", "Обсуждаемое"))
PREVIEW_CHARS = 220


def _flair(text) -> QLabel:
    c = QColor(FLAIR_COLORS.get(text, "#9a9a9a"))
    f = QLabel(text)
    f.setStyleSheet(f"background:rgba({c.red()},{c.green()},{c.blue()},0.18);color:{c.name()};border-radius:6px;"
                    "padding:2px 8px;font-size:11px;font-weight:800;")
    f.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    return f


class VoteColumn(QWidget):
    """▲ счёт ▼ — свой голос подсвечен, повторный клик снимает."""

    def __init__(self, score: int, mine: int, on_vote, horizontal=False, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self) if horizontal else QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0 if not horizontal else 4)
        for val, glyph in ((1, "▲"), (0, None), (-1, "▼")):
            if glyph is None:
                n = QLabel(_short(score))
                n.setAlignment(Qt.AlignmentFlag.AlignCenter)
                col = UP if mine > 0 else DOWN if mine < 0 else "#e8e8e8"
                n.setStyleSheet(f"font-size:13px;font-weight:900;color:{col};min-width:28px;")
                lay.addWidget(n, 0, Qt.AlignmentFlag.AlignCenter)
                continue
            b = QPushButton(glyph)
            b.setObjectName("NetVoteArrow")                    # не стеклянная кнопка: своя, без полей
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedSize(30, 26)
            on = mine == val
            b.setToolTip(("Убрать голос" if on else "За") if val > 0 else ("Убрать голос" if on else "Против"))
            b.setStyleSheet("QPushButton#NetVoteArrow{background:transparent;border:none;border-radius:8px;"
                            "padding:0px;margin:0px;min-width:0px;min-height:0px;font-size:15px;font-weight:900;"
                            f"color:{(UP if val > 0 else DOWN) if on else '#8a8a8a'};}}"
                            f"QPushButton#NetVoteArrow:hover{{background:rgba(255,255,255,0.08);"
                            f"color:{UP if val > 0 else DOWN};}}")
            b.clicked.connect(lambda _=False, v=val, on=on: on_vote(0 if on else v))
            lay.addWidget(b, 0, Qt.AlignmentFlag.AlignCenter)
        if not horizontal:
            lay.addStretch(1)


def _short(n: int) -> str:
    return f"{n / 1000:.1f}k".replace(".0k", "k") if abs(n) >= 1000 else str(n)


class _Branch(QWidget):
    """Линия ветки слева от комментария (как на Reddit): клик — свернуть / развернуть ветку."""

    def __init__(self, depth, on_click, parent=None):
        super().__init__(parent)
        self.on_click = on_click
        self.setFixedWidth(16)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Свернуть ветку")
        self._hover = False
        self.col = QColor.fromHsvF((0.12 + depth * 0.17) % 1.0, 0.55, 0.95)

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.on_click()

    def paintEvent(self, _):
        p = QPainter(self)
        c = QColor(self.col) if self._hover else QColor(255, 255, 255, 38)
        p.setPen(QPen(c, 2))
        x = self.width() / 2
        p.drawLine(int(x), 2, int(x), self.height() - 2)
        p.end()


class ThreadsView(QWidget):
    def __init__(self, page):
        super().__init__()
        from yamusic_theme import Page
        self.page = page
        self.setObjectName("YaPage")
        self.sort = "hot"
        self.flair = ""
        self.query = ""
        self.open_id = None
        self.collapsed: set[str] = set()
        self.reply_to = None                                   # id комментария, под которым открыто поле
        self.drafts: dict[str, str] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack)
        self.list_page = Page(margins=(0, 4, 8, 24))
        self.detail_page = Page(margins=(0, 4, 8, 24))
        self.stack.addWidget(self.list_page)
        self.stack.addWidget(self.detail_page)
        self._deb = QTimer(self)
        self._deb.setSingleShot(True)
        self._deb.setInterval(220)
        self._deb.timeout.connect(self.fill)

    # ── данные ── #

    def threads(self) -> list:
        st = self.page.node.store
        out = [p for p in st.posts(limit=500) if p.get("kind") == "thread"]
        if self.flair:
            out = [p for p in out if p.get("flair") == self.flair]
        if self.query:
            q = self.query.casefold()
            out = [p for p in out if q in (p.get("title", "") + " " + p.get("text", "")).casefold()]
        now = time.time()

        def score(p):
            up, dn = st.score(p["id"])
            return up - dn
        if self.sort == "new":
            out.sort(key=lambda p: p["ts"], reverse=True)
        elif self.sort == "top":
            out.sort(key=lambda p: (score(p), p["ts"]), reverse=True)
        elif self.sort == "talk":
            out.sort(key=lambda p: (len(st.comments(p["id"])), p["ts"]), reverse=True)
        else:                                                  # горячее: голоса и комментарии, свежие выше
            def hot(p):
                age_h = max(0.0, (now - p["ts"]) / 3600)
                return (score(p) + 1 + 0.6 * len(st.comments(p["id"]))) / ((age_h + 2) ** 1.4)
            out.sort(key=hot, reverse=True)
        return out

    def _vote(self, pid, val):
        pg = self.page
        if not pg.running():
            pg.toast("Включите сеть, чтобы голосовать")
            return
        pg.node.call(pg.node.vote, pid, val)

    # ── список ── #

    def fill(self):
        QTimer.singleShot(0, lambda: self.page._roundify(self))
        if self.page.node is None:
            return
        if self.open_id and self.page.node.store.post(self.open_id):
            self.fill_detail()
            return
        self.open_id = None
        self.stack.setCurrentWidget(self.list_page)
        from net_page import accent_btn, lbl
        pg = self.list_page
        sb = pg.verticalScrollBar()
        keep = sb.value()
        pg.clear()
        head = QHBoxLayout()
        t = lbl("Треды")
        t.setStyleSheet("font-size:26px;font-weight:900;color:#ffffff;")
        head.addWidget(t)
        head.addSpacing(10)
        self.q = QLineEdit(self.query)
        self.q.setPlaceholderText("Поиск по тредам…")
        self.q.setClearButtonEnabled(True)
        self.q.setMaximumWidth(320)
        self.q.textChanged.connect(lambda s: (setattr(self, "query", s.strip()), self._deb.start()))
        head.addWidget(self.q, 1)
        head.addStretch(1)
        nb = accent_btn("  Создать тред")
        from yamusic_theme import ya_icon
        nb.setIcon(ya_icon("plus", 14, "#000000"))
        nb.clicked.connect(self.create)
        head.addWidget(nb)
        pg.v.addLayout(head)

        row = QHBoxLayout()
        row.setContentsMargins(0, 8, 0, 2)
        grp = QButtonGroup(pg.inner)
        for key, text in SORTS:
            b = QPushButton(text)
            b.setObjectName("YaChip")
            b.setCheckable(True)
            b.setChecked(self.sort == key)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: (setattr(self, "sort", k), self.fill()))
            grp.addButton(b)
            row.addWidget(b)
        row.addSpacing(14)
        from net_core import THREAD_FLAIRS
        fl = QComboBox()
        fl.addItem("Все флеры", "")
        for f in THREAD_FLAIRS:
            fl.addItem(f, f)
        fl.setCurrentIndex(max(0, fl.findData(self.flair)))
        fl.currentIndexChanged.connect(lambda _: (setattr(self, "flair", fl.currentData()), self.fill()))
        row.addWidget(fl)
        row.addStretch(1)
        pg.v.addLayout(row)
        pg.v.addSpacing(6)

        items = self.threads()
        if not items:
            msg = ("Ничего не нашлось." if self.query or self.flair else
                   "Тредов пока нет. Начните обсуждение — друзья увидят его здесь, проголосуют и ответят.")
            e = lbl(msg, "YaSub", wrap=True)
            e.setStyleSheet("padding:30px 4px;font-size:14px;")
            pg.v.addWidget(e)
        for p in items[:150]:
            pg.v.addWidget(self._row(p))
            pg.v.addSpacing(6)
        pg.v.addStretch(1)
        QTimer.singleShot(0, lambda: sb.setValue(keep))

    def _row(self, p) -> QWidget:
        from net_page import ago, lbl
        from net_music import safe_track, TrackChip
        pg = self.page
        st = pg.node.store
        me = pg.node.me.onion
        pid = p["id"]
        up, dn = st.score(pid)
        f = QFrame()
        f.setObjectName("NetThread")
        f.setStyleSheet("QFrame#NetThread{background:#1a1a1c;border:1px solid #262628;border-radius:14px;}"
                        "QFrame#NetThread:hover{border-color:#3a3a3e;}")
        f.setCursor(Qt.CursorShape.PointingHandCursor)
        h = QHBoxLayout(f)
        h.setContentsMargins(6, 10, 14, 10)
        h.setSpacing(8)
        h.addWidget(VoteColumn(up - dn, st.my_vote(pid, me), lambda v, pid=pid: self._vote(pid, v)), 0,
                    Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(4)
        meta = QHBoxLayout()
        meta.setSpacing(6)
        if p.get("flair"):
            meta.addWidget(_flair(p["flair"]))
        nm = pg._author_name(p["author"], p.get("name", ""))
        av = QLabel()
        av.setFixedSize(18, 18)
        av.setPixmap(pg._av(p["author"], nm, 18))
        meta.addWidget(av)
        meta.addWidget(lbl(f"{nm} · {ago(p['ts'])}", "YaSub"))
        bd = pg._badge(p["author"])
        if bd is not None:
            meta.addWidget(bd)
        meta.addStretch(1)
        col.addLayout(meta)
        t = lbl(p.get("title") or "Без заголовка", wrap=True)
        t.setStyleSheet("font-size:17px;font-weight:900;color:#f4f4f4;")
        col.addWidget(t)
        body = (p.get("text") or "").strip()
        why = "" if p["author"] == me else (pg.node.mod_hidden(p["author"], pid) or pg.node.mod_check(body, p.get("title")))
        if why:
            col.addWidget(lbl(f"\U000f1005 скрыто модерацией — {why}", "YaSub"))
        elif body:
            pv = body if len(body) <= PREVIEW_CHARS else body[:PREVIEW_CHARS].rsplit(" ", 1)[0] + "…"
            b = lbl(pv, wrap=True)
            b.setStyleSheet("font-size:13px;color:#bdbdc2;")
            col.addWidget(b)
        tr = safe_track(p.get("track"))
        if tr:
            col.addWidget(TrackChip(tr, pg.play_meta, compact=True, page=pg, cover=pg._local_cover(tr),
                                    owner=p["author"]))
        n = len(st.comments(pid))
        foot = QPushButton(f"  {n} " + _plural(n, "комментарий", "комментария", "комментариев"))
        foot.setObjectName("NetLink")
        from yamusic_theme import ya_icon
        foot.setIcon(ya_icon("chat", 15, "#9a9a9a"))
        foot.setCursor(Qt.CursorShape.PointingHandCursor)
        foot.clicked.connect(lambda: self.open(pid))
        fr = QHBoxLayout()
        fr.addWidget(foot)
        fr.addStretch(1)
        col.addLayout(fr)
        h.addLayout(col, 1)
        f.mouseReleaseEvent = lambda e, pid=pid: self.open(pid) if e.button() == Qt.MouseButton.LeftButton else None
        return f

    # ── тред ── #

    def open(self, pid):
        self.open_id = pid
        self.reply_to = None
        self.collapsed.clear()
        self.fill_detail(scroll_top=True)

    def back(self):
        self.open_id = None
        self.fill()

    def fill_detail(self, scroll_top=False):
        from net_page import ago, lbl, ghost_btn
        from net_music import safe_track, TrackChip
        pg = self.page
        st = pg.node.store
        me = pg.node.me.onion
        p = st.post(self.open_id)
        if not p:
            self.back()
            return
        pid = p["id"]
        page = self.detail_page
        self.stack.setCurrentWidget(page)
        sb = page.verticalScrollBar()
        keep = 0 if scroll_top else sb.value()
        page.clear()
        bk = ghost_btn("  Все треды")
        from yamusic_theme import ya_icon
        bk.setIcon(ya_icon("back", 14))
        bk.clicked.connect(self.back)
        r = QHBoxLayout()
        r.addWidget(bk)
        r.addStretch(1)
        page.v.addLayout(r)
        page.v.addSpacing(8)

        card = QFrame()
        card.setObjectName("NetThread")
        card.setStyleSheet("QFrame#NetThread{background:#1a1a1c;border:1px solid #262628;border-radius:16px;}")
        h = QHBoxLayout(card)
        h.setContentsMargins(8, 14, 18, 14)
        h.setSpacing(10)
        up, dn = st.score(pid)
        h.addWidget(VoteColumn(up - dn, st.my_vote(pid, me), lambda v: self._vote(pid, v)), 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(8)
        meta = QHBoxLayout()
        meta.setSpacing(6)
        if p.get("flair"):
            meta.addWidget(_flair(p["flair"]))
        nm = pg._author_name(p["author"], p.get("name", ""))
        av = QLabel()
        av.setFixedSize(22, 22)
        av.setPixmap(pg._av(p["author"], nm, 22))
        meta.addWidget(pg._clickable(av, p["author"]))
        meta.addWidget(lbl(f"{nm} · {ago(p['ts'])}", "YaSub"))
        bd = pg._badge(p["author"])
        if bd is not None:
            meta.addWidget(bd)
        meta.addStretch(1)
        if pg.node.my_role():                                  # модерация треда для всех
            from net_page import icon_button
            mb = icon_button("dots", "Модерация", 28, 14, "#9a9a9a")

            def staff_menu(mb=mb, p=p):
                from PyQt6.QtWidgets import QMenu
                m = QMenu(pg)
                pg._staff_menu(m, p["author"], p["id"], pg._author_name(p["author"], p.get("name", "")),
                               (p.get("title") or "")[:80])
                m.exec(mb.mapToGlobal(mb.rect().bottomLeft()))
            mb.clicked.connect(staff_menu)
            meta.addWidget(mb)
        if p["author"] == me:
            from net_page import icon_button
            d = icon_button("close", "Удалить тред", 28, 14, "#9a9a9a")
            d.clicked.connect(lambda: self._delete(pid))
            meta.addWidget(d)
        col.addLayout(meta)
        t = lbl(p.get("title") or "Без заголовка", wrap=True, sel=True)
        t.setStyleSheet("font-size:22px;font-weight:900;color:#f6f6f6;")
        col.addWidget(t)
        why = "" if p["author"] == me else (pg.node.mod_hidden(p["author"], p["id"]) or pg.node.mod_check(p.get("text"), p.get("title")))
        if why:
            col.addWidget(lbl(f"\U000f1005 скрыто модерацией — {why}", "YaSub"))
        elif p.get("text"):
            b = lbl(p["text"], wrap=True, sel=True)
            b.setStyleSheet("font-size:14px;color:#dcdce0;")
            col.addWidget(b)
        tr = safe_track(p.get("track"))
        if tr:
            col.addWidget(TrackChip(tr, pg.play_meta, page=pg, cover=pg._local_cover(tr), owner=p["author"]))
        h.addLayout(col, 1)
        page.v.addWidget(card)
        page.v.addSpacing(10)

        # поле комментария к треду
        page.v.addWidget(self._reply_box(pid, ""))
        coms = st.comments(pid)
        n = len(coms)
        hdr = lbl(f"{n} " + _plural(n, "комментарий", "комментария", "комментариев"), "Section")
        page.v.addSpacing(6)
        page.v.addWidget(hdr)
        ids = {c["id"] for c in coms}
        kids: dict[str, list] = {}
        for c in sorted(coms, key=lambda c: c["ts"]):
            par = c.get("parent") if c.get("parent") in ids else ""
            kids.setdefault(par, []).append(c)

        def count(cid):
            return sum(1 + count(k["id"]) for k in kids.get(cid, []))
        for c in kids.get("", []):
            page.v.addWidget(self._comment(p, c, kids, count, 0))
        if not coms:
            page.v.addWidget(lbl("Пока никто не ответил — будьте первым.", "YaSub"))
        page.v.addStretch(1)
        QTimer.singleShot(0, lambda: sb.setValue(keep))

    def _comment(self, p, c, kids, count, depth) -> QWidget:
        from net_page import ago, lbl
        from net_music import TrackChip
        pg = self.page
        me = pg.node.me.onion
        w = QWidget()
        w.setObjectName("YaPage")
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 4, 0, 0)
        h.setSpacing(6)
        cid = c["id"]
        nm = pg._author_name(c["author"], c.get("name", "")) or ("Вы" if c["author"] == me else c["author"][:8])
        av = QLabel()
        av.setFixedSize(26, 26)
        av.setPixmap(pg._av(c["author"], nm, 26))
        left = QVBoxLayout()
        left.setSpacing(2)
        left.addWidget(pg._clickable(av, c["author"]), 0, Qt.AlignmentFlag.AlignHCenter)
        folded = cid in self.collapsed
        if not folded:
            left.addWidget(_Branch(depth, lambda: self._fold(cid)), 1, Qt.AlignmentFlag.AlignHCenter)
        h.addLayout(left)
        col = QVBoxLayout()
        col.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(8)
        a = lbl(nm, "NetName")
        if c["author"] == p["author"]:
            a.setText(nm + "  · автор")
            a.setStyleSheet(f"color:{UP};font-weight:900;")
        top.addWidget(a)
        bd = pg._badge(c["author"])
        if bd is not None:
            top.addWidget(bd)
        top.addWidget(lbl(ago(c["ts"]), "YaSub"))
        if folded:
            n = count(cid)
            more = QPushButton(f"[+] развернуть" + (f" · ещё {n}" if n else ""))
            more.setObjectName("NetLink")
            more.setCursor(Qt.CursorShape.PointingHandCursor)
            more.clicked.connect(lambda: self._fold(cid))
            top.addWidget(more)
        top.addStretch(1)
        col.addLayout(top)
        if not folded:
            text = c.get("text", "")
            why = "" if c["author"] == me else (pg.node.mod_hidden(c["author"], c.get("id")) or pg.node.mod_check(text))
            if why:
                col.addWidget(lbl(f"\U000f1005 комментарий скрыт модерацией — {why}", "YaSub"))
            elif text:
                t = lbl(text, wrap=True, sel=True)
                t.setStyleSheet("font-size:13px;color:#e4e4e8;")
                col.addWidget(t)
            if c.get("track"):
                col.addWidget(TrackChip(c["track"], pg.play_meta, compact=True, page=pg))
            acts = QHBoxLayout()
            acts.setSpacing(10)
            rp = QPushButton("Ответить")
            rp.setObjectName("NetLink")
            rp.setCursor(Qt.CursorShape.PointingHandCursor)
            rp.clicked.connect(lambda: self._reply(cid))
            acts.addWidget(rp)
            if kids.get(cid):
                fb = QPushButton("Свернуть")
                fb.setObjectName("NetLink")
                fb.setCursor(Qt.CursorShape.PointingHandCursor)
                fb.clicked.connect(lambda: self._fold(cid))
                acts.addWidget(fb)
            acts.addStretch(1)
            col.addLayout(acts)
            if self.reply_to == cid:
                col.addWidget(self._reply_box(p["id"], cid, nm))
            for k in kids.get(cid, []):
                col.addWidget(self._comment(p, k, kids, count, depth + 1))
        h.addLayout(col, 1)
        return w

    def _fold(self, cid):
        self.collapsed.symmetric_difference_update({cid})
        self.fill_detail()

    def _reply(self, cid):
        self.reply_to = None if self.reply_to == cid else cid
        self.fill_detail()

    def _reply_box(self, pid, parent, to_name="") -> QWidget:
        from net_page import accent_btn, ghost_btn
        pg = self.page
        box = QFrame()
        box.setObjectName("NetReply")
        box.setStyleSheet("QFrame#NetReply{background:#151517;border:1px solid #2a2a2d;border-radius:12px;}")
        v = QVBoxLayout(box)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(6)
        key = f"{pid}/{parent}"
        e = QPlainTextEdit()
        e.setPlaceholderText(f"Ответ для {to_name}…" if to_name else "Что думаете? Напишите комментарий…")
        e.setFixedHeight(64 if parent else 76)
        e.setStyleSheet("QPlainTextEdit{background:transparent;border:none;color:#f2f2f2;font-size:13px;}")
        e.setPlainText(self.drafts.get(key, ""))
        e.textChanged.connect(lambda: self.drafts.__setitem__(key, e.toPlainText()))
        v.addWidget(e)
        r = QHBoxLayout()
        cur = pg.shell.current_track()
        tb = QPushButton("  + играющий трек")
        tb.setObjectName("YaChip")
        tb.setCheckable(True)
        tb.setEnabled(cur is not None)
        tb.setStyleSheet("QPushButton#YaChip{border-radius:12px;padding:4px 10px;font-size:12px;}")
        r.addWidget(tb)
        r.addStretch(1)
        if parent:
            cb = ghost_btn("Отмена")
            cb.clicked.connect(lambda: self._reply(parent))
            r.addWidget(cb)
        sb = accent_btn("Ответить" if parent else "Комментировать")

        def send():
            text = e.toPlainText().strip()
            from net_page import track_meta
            tr = track_meta(pg.shell.current_track()) if tb.isChecked() else None
            if not text and not tr:
                return
            if not pg.running():
                pg.toast("Включите сеть, чтобы комментировать")
                return
            pg.node.call(pg.node.comment, pid, text, parent, tr)
            self.drafts.pop(key, None)
            self.reply_to = None
            QTimer.singleShot(400, self.fill_detail)
        sb.clicked.connect(send)
        r.addWidget(sb)
        v.addLayout(r)
        if parent:
            QTimer.singleShot(0, e.setFocus)
        return box

    def _delete(self, pid):
        from PyQt6.QtWidgets import QMessageBox
        pg = self.page
        if QMessageBox.question(self, "Удалить тред", "Удалить тред у себя и у друзей?") != QMessageBox.StandardButton.Yes:
            return
        pg.node.call(pg.node.delete_post, pid)
        self.back()

    # ── создать ── #

    def create(self):
        d = CreateThread(self.page, self)
        if d.exec() and d.result:
            title, flair, text, track = d.result
            pg = self.page
            kw = {"title": title, "flair": flair}
            if pg.running():
                pg.node.call(pg.node.publish, text, track, "thread", **kw)
            else:
                import asyncio
                asyncio.run(pg.node.publish(text, track, "thread", **kw))
                pg.toast("Сеть выключена — тред сохранён, друзья получат его, когда вы будете в сети")
            self.sort = "new"
            QTimer.singleShot(400, self.fill)


class CreateThread(QDialog):
    def __init__(self, page, parent=None):
        super().__init__(parent)
        from net_core import THREAD_FLAIRS
        self.page = page
        self.result = None
        self.track = None
        self.setWindowTitle("Новый тред")
        self.setMinimumSize(560, 460)
        self.setStyleSheet(page._dlg_qss() + "QDialog{background:#161616;}")
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 18)
        v.setSpacing(10)
        h = QLabel("Новый тред")
        h.setStyleSheet("font-size:21px;font-weight:900;color:#fff;")
        v.addWidget(h)
        self.title = QLineEdit()
        self.title.setPlaceholderText("Заголовок — о чём поговорим?")
        self.title.setMaxLength(200)
        self.title.setStyleSheet("font-size:15px;font-weight:800;")
        v.addWidget(self.title)
        fr = QHBoxLayout()
        fr.setSpacing(6)
        self.flair = ""
        grp = QButtonGroup(self)
        grp.setExclusive(False)
        self._fbtns = []
        for f in THREAD_FLAIRS:
            b = QPushButton(f)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            c = QColor(FLAIR_COLORS.get(f, "#9a9a9a"))
            b.setStyleSheet(f"QPushButton{{background:rgba(255,255,255,0.06);color:#cfcfcf;border:none;border-radius:11px;"
                            f"padding:3px 10px;font-size:12px;font-weight:800;}}QPushButton:checked{{background:"
                            f"rgba({c.red()},{c.green()},{c.blue()},0.25);color:{c.name()};}}")
            b.clicked.connect(lambda on, f=f, b=b: self._pick_flair(f if on else "", b))
            self._fbtns.append(b)
            fr.addWidget(b)
        fr.addStretch(1)
        v.addLayout(fr)
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("Текст (необязательно): мысли, вопрос, подборка…")
        v.addWidget(self.text, 1)
        tr = QHBoxLayout()
        self.tbtn = QPushButton("  Трек из коллекции")
        self.tbtn.setObjectName("YaChip")
        from yamusic_theme import ya_icon
        self.tbtn.setIcon(ya_icon("note", 15, "#d0d0d0"))
        self.tbtn.clicked.connect(self._pick_track)
        tr.addWidget(self.tbtn)
        cur = page.shell.current_track()
        if cur is not None:
            pb = QPushButton("  Играющий")
            pb.setObjectName("YaChip")
            pb.clicked.connect(lambda: self._set_track(cur))
            tr.addWidget(pb)
        tr.addStretch(1)
        v.addLayout(tr)
        br = QHBoxLayout()
        br.addStretch(1)
        from net_page import accent_btn, ghost_btn
        c = ghost_btn("Отмена")
        c.clicked.connect(self.reject)
        br.addWidget(c)
        ok = accent_btn("Опубликовать")
        ok.clicked.connect(self._ok)
        br.addWidget(ok)
        v.addLayout(br)
        self.title.setFocus()

    def _pick_flair(self, f, btn):
        self.flair = f
        for b in self._fbtns:
            if b is not btn:
                b.setChecked(False)

    def _set_track(self, t):
        from net_playlist_ui import track_to_meta
        self.track = track_to_meta(t)
        self.tbtn.setText(f"  {self.track['artist']} — {self.track['title']}"[:48])

    def _pick_track(self):
        from net_playlist_ui import TrackSearch
        d = TrackSearch(self.page.win, self, title="Трек для треда")
        d.file_btn.hide()
        d.ok.setText("Выбрать")
        if d.exec() and d.result_tracks:
            self._set_track(d.result_tracks[0])

    def _ok(self):
        title = self.title.text().strip()
        if not title:
            self.title.setPlaceholderText("Нужен заголовок")
            self.title.setFocus()
            return
        self.result = (title, self.flair, self.text.toPlainText().strip(), self.track)
        self.accept()


def _plural(n, one, few, many) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many
