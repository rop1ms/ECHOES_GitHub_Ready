# profile_editor.py
"""
«Изменить профиль» — одно окно, как в Spotify, вместо трёх вопросов подряд.

Слева — большая круглая аватарка: клик — выбрать фото (обрезается в квадрат и копируется
в ~/.neon_player/avatar, так что исходный файл можно удалить), «Удалить фото».
Справа — ник, статус, «о себе» (видят друзья в Echoes.NET), любимые треки профиля
(поиск по коллекции, «играющий сейчас», порядок, удаление), что показывать друзьям.
Сохраняет profile.json плеера и настройки Echoes.NET (net/net.json или живой узел сети).
Свой стиль — окно одинаково выглядит в любой теме плеера.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from PyQt6.QtCore import Qt, QRectF, QSize
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPixmap, QPen
from PyQt6.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
                             QPlainTextEdit, QListWidget, QListWidgetItem, QFileDialog, QFrame, QCheckBox,
                             QScrollArea)

DATA_DIR = Path.home() / ".neon_player"
YELLOW = "#ffdb1a"
MAX_FAV = 8

QSS = f"""
QDialog {{ background:#141414; }}
QWidget {{ color:#ffffff; font-family:"YS Text","Inter","Segoe UI"; font-size:13px; }}
QLabel {{ background:transparent; }}
QLabel#Title {{ font-size:24px; font-weight:900; }}
QLabel#Sec {{ color:#8a8a8a; font-size:11px; font-weight:800; letter-spacing:1px; }}
QLabel#Sub {{ color:#8a8a8a; font-size:12px; }}
QLabel#Count {{ color:#6a6a6a; font-size:11px; }}
QLineEdit, QPlainTextEdit {{ background:#1f1f1f; border:1px solid #2c2c2c; border-radius:12px; padding:9px 14px;
    selection-background-color:{YELLOW}; selection-color:#000; }}
QLineEdit:focus, QPlainTextEdit:focus {{ border-color:#5a5a5a; }}
QPushButton {{ background:#262626; border:none; border-radius:14px; padding:8px 18px; font-weight:700; }}
QPushButton:hover {{ background:#303030; }}
QPushButton:disabled {{ color:#555; }}
QPushButton#Save {{ background:{YELLOW}; color:#000; padding:10px 26px; border-radius:16px; }}
QPushButton#Save:hover {{ background:#ffe55c; }}
QPushButton#Ghost {{ background:transparent; border:1px solid #333; }}
QPushButton#Mini {{ background:transparent; padding:0; border-radius:13px; font-size:14px; color:#bdbdbd; }}
QPushButton#Mini:hover {{ background:#2a2a2a; color:#fff; }}
QListWidget {{ background:#1b1b1b; border:1px solid #2a2a2a; border-radius:12px; outline:none; padding:4px; }}
QListWidget::item {{ padding:7px 8px; border-radius:8px; }}
QListWidget::item:hover {{ background:#262626; }}
QListWidget::item:selected {{ background:#2e2e2e; }}
QCheckBox {{ spacing:10px; }}
QScrollArea {{ background:transparent; border:none; }}
QWidget#Inner, QWidget#FavRow {{ background:transparent; }}
QScrollBar:vertical {{ background:transparent; width:8px; }}
QScrollBar::handle:vertical {{ background:#3a3a3a; border-radius:3px; min-height:30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height:0; }}
"""


def _meta(t) -> dict:
    try:
        from lyrics import sanitize_title
    except Exception:                                          # noqa: BLE001
        def sanitize_title(s):
            return s or ""
    return {"artist": t.get("artist") or "", "title": sanitize_title(t.get("title") or ""),
            "album": t.get("album") or "", "duration": int(t.get("duration") or 0),
            "url": t.get("url") or "", "thumb_url": t.get("thumb_url") or ""}


def _key(t) -> tuple:
    return ((t.get("artist") or "").casefold().strip(), (t.get("title") or "").casefold().strip())


class AvatarPick(QWidget):
    """Круглая аватарка; при наведении — «Выбрать фото». Клик — выбрать файл."""

    def __init__(self, path, seed, on_click, parent=None):
        super().__init__(parent)
        self.setFixedSize(190, 190)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.on_click = on_click
        self.seed = seed or "?"
        self._hover = False
        self.set_path(path)

    def set_path(self, path):
        self.path = path or ""
        img = QImage(self.path) if self.path else QImage()
        self.img = img if not img.isNull() else None
        self.update()

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
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(5, 5, 180, 180)
        clip = QPainterPath()
        clip.addEllipse(r)
        p.setClipPath(clip)
        if self.img is not None:
            s = min(self.img.width(), self.img.height())
            sq = self.img.copy((self.img.width() - s) // 2, (self.img.height() - s) // 2, s, s)
            p.drawImage(r, sq)
        else:
            p.fillRect(r, QColor("#2a2a2a"))
            p.setPen(QColor("#9a9a9a"))
            f = QFont()
            f.setPixelSize(72)
            f.setWeight(QFont.Weight.Black)
            p.setFont(f)
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.seed[:1].upper())
        if self._hover:
            p.fillRect(r, QColor(0, 0, 0, 150))
            p.setPen(QColor("#ffffff"))
            f = QFont()
            f.setPixelSize(14)
            f.setWeight(QFont.Weight.Bold)
            p.setFont(f)
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, "✎\nВыбрать фото")
        p.setClipping(False)
        p.setPen(QPen(QColor(255, 255, 255, 30), 2))
        p.drawEllipse(r)
        p.end()


class ProfileEditor(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Изменить профиль")
        self.setStyleSheet(QSS)
        self.setMinimumSize(780, 660)
        prof = dict(getattr(win, "profile", {}) or {})
        self.node = getattr(win, "_net_node", None)
        self.net = self._net_settings()
        self.avatar = str(prof.get("avatar") or "")
        self.fav = [dict(x) for x in self.net.get("fav", [])][:MAX_FAV]

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 22)
        root.setSpacing(14)
        t = QLabel("Изменить профиль")
        t.setObjectName("Title")
        root.addWidget(t)
        sub = QLabel("Так вас видят друзья в Echoes.NET. Изменения уйдут им сами, когда вы будете в сети.")
        sub.setObjectName("Sub")
        root.addWidget(sub)

        body = QHBoxLayout()
        body.setSpacing(28)
        left = QVBoxLayout()
        left.setSpacing(8)
        self.av = AvatarPick(self.avatar, prof.get("nickname") or "?", self._pick_avatar)
        left.addWidget(self.av, 0, Qt.AlignmentFlag.AlignHCenter)
        row = QHBoxLayout()
        pick = QPushButton("Фото, GIF или видео")
        pick.setToolTip("GIF или короткое видео (первые 4 с) — живая аватарка: друзья видят её в шапке профиля")
        pick.clicked.connect(self._pick_avatar)
        self.rm = QPushButton("Удалить")
        self.rm.setObjectName("Ghost")
        self.rm.clicked.connect(self._clear_avatar)
        row.addWidget(pick)
        row.addWidget(self.rm)
        left.addLayout(row)
        self.rm.setEnabled(bool(self.avatar))
        left.addStretch(1)
        body.addLayout(left)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("Inner")
        scroll.viewport().setStyleSheet("background:transparent;")
        rv = QVBoxLayout(inner)
        rv.setContentsMargins(0, 0, 10, 0)
        rv.setSpacing(8)

        self.nick = self._field(rv, "НИК", prof.get("nickname") or "", 40, "Как вас зовут друзья")
        self.status = self._field(rv, "СТАТУС", prof.get("status") or "", 120, "Например: слушаю эмо-рэп ночами")
        sec, cnt = self._sec_row(rv, "О СЕБЕ")
        self.about = QPlainTextEdit(self.net.get("about") or self.net.get("bio") or "")
        self.about.setPlaceholderText("Что слушаете, что любите, где вас найти…")
        self.about.setFixedHeight(96)
        self.about.textChanged.connect(lambda: self._count(self.about.toPlainText(), 1500, cnt))
        self._count(self.about.toPlainText(), 1500, cnt)
        rv.addWidget(self.about)

        self._sec_row(rv, f"ЛЮБИМЫЕ ТРЕКИ В ПРОФИЛЕ · ДО {MAX_FAV}")
        self.fav_list = QListWidget()
        self.fav_list.setFixedHeight(150)
        rv.addWidget(self.fav_list)
        fr = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Найти трек в коллекции…")
        self.search.textChanged.connect(self._search)
        fr.addWidget(self.search, 1)
        self.add_cur = QPushButton("＋ Играющий")
        self.add_cur.setToolTip("Добавить трек, который сейчас играет")
        self.add_cur.clicked.connect(self._add_current)
        fr.addWidget(self.add_cur)
        rv.addLayout(fr)
        self.results = QListWidget()
        self.results.setFixedHeight(130)
        self.results.hide()
        self.results.itemClicked.connect(lambda it: self._add(it.data(Qt.ItemDataRole.UserRole)))
        rv.addWidget(self.results)

        self._sec_row(rv, "ЧТО ВИДЯТ ДРУЗЬЯ")
        self.ch_tier = QCheckBox("Мой тирлист (друзья сравнивают с ним свой)")
        self.ch_tier.setChecked(bool(self.net.get("share_tier", True)))
        self.ch_now = QCheckBox("Что я сейчас слушаю")
        self.ch_now.setChecked(bool(self.net.get("share_now", True)))
        self.ch_taste = QCheckBox("Мой вкус — для совместимости и Blend")
        self.ch_taste.setChecked(bool(self.net.get("share_taste", True)))
        for c in (self.ch_tier, self.ch_now, self.ch_taste):
            rv.addWidget(c)
        rv.addStretch(1)
        scroll.setWidget(inner)
        body.addWidget(scroll, 1)
        root.addLayout(body, 1)

        foot = QHBoxLayout()
        foot.addStretch(1)
        cancel = QPushButton("Отмена")
        cancel.setObjectName("Ghost")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Сохранить")
        save.setObjectName("Save")
        save.setDefault(True)
        save.clicked.connect(self._save)
        foot.addWidget(cancel)
        foot.addWidget(save)
        root.addLayout(foot)
        self._fill_fav()

    # ── поля ── #

    def _sec_row(self, v, text):
        h = QHBoxLayout()
        s = QLabel(text)
        s.setObjectName("Sec")
        h.addWidget(s)
        h.addStretch(1)
        c = QLabel("")
        c.setObjectName("Count")
        h.addWidget(c)
        v.addSpacing(6)
        v.addLayout(h)
        return s, c

    def _field(self, v, title, text, limit, ph):
        _s, cnt = self._sec_row(v, title)
        e = QLineEdit(text)
        e.setMaxLength(limit)
        e.setPlaceholderText(ph)
        e.textChanged.connect(lambda s: self._count(s, limit, cnt))
        self._count(text, limit, cnt)
        v.addWidget(e)
        return e

    @staticmethod
    def _count(text, limit, lbl):
        lbl.setText(f"{len(text)}/{limit}")

    # ── аватарка ── #

    def _pick_avatar(self):
        import ya_avanim
        fn, _ = QFileDialog.getOpenFileName(self, "Фото профиля", str(Path.home()), ya_avanim.FILTER)
        if not fn:
            return
        anim = ya_avanim.is_animated(fn)
        img = ya_avanim.first_frame(fn) if anim else QImage(fn)
        if img.isNull():
            return
        self._anim_src = fn if anim else ""
        self._new_img = img
        self.av.img = img
        self.av.update()
        self.rm.setEnabled(True)

    def _clear_avatar(self):
        self._new_img = None
        self._anim_src = ""
        self._anim_clear = True
        self.avatar = ""
        self.av.set_path("")
        self.rm.setEnabled(False)

    def _store_avatar(self) -> str:
        """Выбранное фото → квадрат 512 px в ~/.neon_player/avatar (старые копии удаляются)."""
        img = getattr(self, "_new_img", None)
        if img is None:
            return self.avatar
        d = DATA_DIR / "avatar"
        d.mkdir(parents=True, exist_ok=True)
        s = min(img.width(), img.height())
        sq = img.copy((img.width() - s) // 2, (img.height() - s) // 2, s, s).scaled(
            512, 512, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        out = d / f"avatar_{int(time.time())}.jpg"
        sq.save(str(out), "JPG", 92)
        for old in d.glob("avatar_*.jpg"):
            if old != out:
                old.unlink(missing_ok=True)
        return str(out).replace("\\", "/")

    def _store_anim(self, w):
        """Живая аватарка: выбран GIF/видео — сжать в GIF; выбрано обычное фото или «Удалить» — убрать."""
        import ya_avanim
        src = getattr(self, "_anim_src", None)
        if src:
            data = ya_avanim.make_gif(src)
            if data:
                w.profile["avatar_anim"] = ya_avanim.store(data, DATA_DIR / "avatar")
                return
        if src is not None or getattr(self, "_anim_clear", False):
            old = w.profile.pop("avatar_anim", None)
            if old:
                Path(old).unlink(missing_ok=True)

    # ── любимые треки ── #

    def _fill_fav(self):
        self.fav_list.clear()
        for i, t in enumerate(self.fav):
            it = QListWidgetItem()
            it.setSizeHint(QSize(100, 38))
            self.fav_list.addItem(it)
            w = QWidget()
            w.setObjectName("FavRow")
            h = QHBoxLayout(w)
            h.setContentsMargins(6, 0, 4, 0)
            n = QLabel(f"{i + 1}.  {t.get('title')}  ·  {t.get('artist')}")
            n.setStyleSheet("font-weight:700;")
            from PyQt6.QtWidgets import QSizePolicy
            n.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            n.setMinimumWidth(10)
            h.addWidget(n, 1)
            for txt, tip, fn in (("↑", "Выше", lambda _=False, i=i: self._move(i, -1)),
                                 ("↓", "Ниже", lambda _=False, i=i: self._move(i, 1)),
                                 ("✕", "Убрать", lambda _=False, i=i: self._remove(i))):
                b = QPushButton(txt)
                b.setObjectName("Mini")
                b.setFixedSize(26, 26)
                b.setToolTip(tip)
                b.clicked.connect(fn)
                h.addWidget(b)
            self.fav_list.setItemWidget(it, w)
        if not self.fav:
            it = QListWidgetItem("Пока пусто — найдите трек ниже или добавьте играющий")
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.fav_list.addItem(it)
        full = len(self.fav) >= MAX_FAV
        self.search.setEnabled(not full)
        cur = self._current()
        self.add_cur.setEnabled(not full and cur is not None)

    def _current(self):
        try:
            ctx = self.win.queue_context
            tracks = self.win.library if ctx == "library" else self.win.playlists.get(ctx, {}).get("tracks", [])
            i = self.win.current_index
            return tracks[i] if 0 <= i < len(tracks) else None
        except Exception:                                      # noqa: BLE001
            return None

    def _add(self, t):
        if not t or len(self.fav) >= MAX_FAV:
            return
        m = _meta(t)
        if any(_key(m) == _key(x) for x in self.fav):
            return
        self.fav.append(m)
        self.search.clear()
        self._fill_fav()

    def _add_current(self):
        self._add(self._current())

    def _move(self, i, d):
        j = i + d
        if 0 <= j < len(self.fav):
            self.fav[i], self.fav[j] = self.fav[j], self.fav[i]
            self._fill_fav()

    def _remove(self, i):
        self.fav.pop(i)
        self._fill_fav()

    def _search(self, q):
        q = q.casefold().strip()
        self.results.clear()
        if len(q) < 2:
            self.results.hide()
            return
        n = 0
        for t in getattr(self.win, "library", []):
            hay = f"{t.get('title', '')} {t.get('artist', '')}".casefold()
            if q in hay:
                it = QListWidgetItem(f"{t.get('title') or '?'}  ·  {t.get('artist') or ''}")
                it.setData(Qt.ItemDataRole.UserRole, t)
                self.results.addItem(it)
                n += 1
                if n >= 30:
                    break
        if not n:
            it = QListWidgetItem("Ничего не нашлось в коллекции")
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.results.addItem(it)
        self.results.show()

    # ── настройки сети и сохранение ── #

    def _net_settings(self) -> dict:
        if self.node is not None:
            return self.node.settings
        try:
            return json.loads((DATA_DIR / "net" / "net.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self):
        w = self.win
        nick = self.nick.text().strip() or "user"
        w.profile.update({"nickname": nick, "status": self.status.text().strip(), "avatar": self._store_avatar()})
        self._store_anim(w)
        (DATA_DIR / "profile.json").write_text(json.dumps(w.profile, ensure_ascii=False, indent=2), encoding="utf-8")
        s = self.net
        s.update(name=nick[:40], about=self.about.toPlainText().strip()[:1500], fav=self.fav[:MAX_FAV],
                 share_tier=self.ch_tier.isChecked(), share_now=self.ch_now.isChecked(),
                 share_taste=self.ch_taste.isChecked())
        if self.node is not None:
            self.node.save_settings()
        else:
            try:
                (DATA_DIR / "net").mkdir(parents=True, exist_ok=True)
                (DATA_DIR / "net" / "net.json").write_text(json.dumps(s, ensure_ascii=False, indent=1),
                                                           encoding="utf-8")
            except OSError as e:
                print("[profile] net.json:", e)
        self.accept()
