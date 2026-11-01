# net_gifts.py
"""
Подарки друзьям (в чате: «+» → «🎁 Подарок…»):
  * рамка — своя нарисованная/загруженная (за ачивки не дарятся — их надо заработать);
  * витрина — копия вашей витрины как шаблон (друг ставит себе и правит);
  * «трек дня» — трек с открыткой.
К любому подарку — открытка (фон из 6 вариантов) с пожеланием. Подарок — обычное сообщение чата
(extra {"kind": "gift", "gift": {...}}): доходит, даже если друг не в сети. Подаренные рамки копятся
в «Рамка профиля → ПОДАРЕННЫЕ».
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter, QLinearGradient, QPixmap
from PyQt6.QtWidgets import (QButtonGroup, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                             QPushButton, QVBoxLayout, QWidget)

CARDS = [("Ночь", "#2b1055", "#7597de"), ("Закат", "#ff512f", "#dd2476"), ("Лето", "#f7971e", "#ffd200"),
         ("Мята", "#11998e", "#38ef7d"), ("Неон", "#7c4dff", "#2fc6ff"), ("Вишня", "#3a0a1e", "#ff4d7a")]
KINDS = {"frame": "рамку", "showcase": "витрину", "track": "трек дня"}
SC_LIMIT = 60000                     # витрина в подарке — как публичная копия (влезает и в почту Nostr)
GIFT_PL = "Подарки"


def clean(g) -> dict | None:
    """Проверка входящего подарка."""
    if not isinstance(g, dict) or g.get("g") not in KINDS:
        return None
    try:
        card = max(0, min(len(CARDS) - 1, int(g.get("card") or 0)))
    except (TypeError, ValueError):
        card = 0
    out = {"g": g["g"], "card": card}
    if g["g"] == "frame":
        import net_frames
        fr = net_frames.clean_spec(g.get("frame"))
        if fr.get("id") != "custom":
            return None
        out["frame"] = fr
    elif g["g"] == "showcase":
        import json
        import net_showcase
        sc = net_showcase.clean_sc(g.get("sc"))
        if not sc.get("items") or len(json.dumps(sc, ensure_ascii=False)) > SC_LIMIT * 1.1:
            return None
        out["sc"] = sc
    return out


def pack_showcase(sc: dict) -> dict:
    import net_showcase
    return (net_showcase.for_public({"sc": sc}).get("sc")) or {}


def card_brush(i, w, h) -> QLinearGradient:
    _n, a, b = CARDS[max(0, min(len(CARDS) - 1, int(i)))]
    g = QLinearGradient(0, 0, w, h)
    g.setColorAt(0, QColor(a))
    g.setColorAt(1, QColor(b))
    return g


def gifted_frames(win) -> list:
    return list(win.settings.get("ya_gift_frames") or [])


def keep_frame(win, frame: dict, who: str):
    lst = [f for f in gifted_frames(win) if f.get("png") != frame.get("png")]
    lst.insert(0, {"png": frame.get("png"), "from": who})
    win.settings["ya_gift_frames"] = lst[:12]


# ══════════════════════════ отправка ══════════════════════════ #

class GiftDialog(QDialog):
    def __init__(self, net, parent=None):
        super().__init__(parent or net.win)
        import net_frames
        self.net = net
        self.setWindowTitle("Подарок другу")
        self.setStyleSheet(net_frames._QSS + "QLineEdit{background:#1d1d20;color:#f0f0f0;border:1px solid #2c2c30;"
                           "border-radius:10px;padding:8px 10px;}")
        self.resize(560, 520)
        deco = dict(net.win.settings.get("ya_profile_deco") or {})
        fr = deco.get("frame") or {}
        self.frame = fr if fr.get("id") == "custom" else None
        self.sc = deco.get("sc") if (deco.get("sc") or {}).get("items") else None
        self.track = None
        cur = net.shell.current_track()
        if cur:
            from net_page import track_meta
            self.track = track_meta(cur)
        self.kind = "track"
        self.card = 4
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(10)
        t = QLabel("\U000f1004  Подарок другу")
        t.setStyleSheet("font-size:22px;font-weight:900;")
        v.addWidget(t)
        kinds = QHBoxLayout()
        self.kg = QButtonGroup(self)
        for k, title in (("track", "Трек дня"), ("frame", "Рамка"), ("showcase", "Шаблон витрины")):
            b = QPushButton(title)
            b.setCheckable(True)
            b.setChecked(k == self.kind)
            b.clicked.connect(lambda _=False, k=k: self._set_kind(k))
            self.kg.addButton(b)
            kinds.addWidget(b)
        kinds.addStretch(1)
        v.addLayout(kinds)
        self.what = QLabel()
        self.what.setWordWrap(True)
        v.addWidget(self.what)
        src = QHBoxLayout()
        self.b1, self.b2 = QPushButton(), QPushButton()
        self.b1.clicked.connect(lambda: self._source(1))
        self.b2.clicked.connect(lambda: self._source(2))
        src.addWidget(self.b1)
        src.addWidget(self.b2)
        src.addStretch(1)
        v.addLayout(src)
        v.addWidget(QLabel("Открытка:"))
        cards = QHBoxLayout()
        self.cg = QButtonGroup(self)
        for i, (n, a, b) in enumerate(CARDS):
            c = QPushButton(n)
            c.setCheckable(True)
            c.setChecked(i == self.card)
            c.setFixedHeight(34)
            c.setStyleSheet(f"QPushButton{{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {a},stop:1 {b});"
                            "color:#fff;border-radius:10px;font-weight:800;padding:4px 10px;border:2px solid transparent;}"
                            "QPushButton:checked{border:2px solid #fff;}")
            c.clicked.connect(lambda _=False, i=i: self._set_card(i))
            self.cg.addButton(c)
            cards.addWidget(c)
        v.addLayout(cards)
        self.note = QLineEdit()
        self.note.setMaxLength(300)
        self.note.setPlaceholderText("Пожелание на открытке (необязательно)")
        self.note.textChanged.connect(lambda _: self.prev.update())
        v.addWidget(self.note)
        self.prev = _Preview(self)
        v.addWidget(self.prev, 1)
        bot = QHBoxLayout()
        bot.addStretch(1)
        c = QPushButton("Отмена")
        c.clicked.connect(self.reject)
        self.ok = QPushButton("Подарить")
        self.ok.setObjectName("Accent")
        self.ok.clicked.connect(self._ok)
        bot.addWidget(c)
        bot.addWidget(self.ok)
        v.addLayout(bot)
        self._set_kind(self.kind)

    def _set_card(self, i):
        self.card = i
        self.prev.update()

    def _set_kind(self, k):
        self.kind = k
        if k == "track":
            t = self.track
            self.what.setText(f"Трек: <b>{t.get('title', '')}</b> — {t.get('artist', '')}" if t else
                              "Трек не выбран")
            self.b1.setText("Играющий")
            self.b2.setText("Из коллекции…")
        elif k == "frame":
            self.what.setText("Ваша рамка (своя, нарисованная или загруженная)" if self.frame else
                              "Своей рамки пока нет — нарисуйте или загрузите для друга")
            self.b1.setText("Нарисовать…")
            self.b2.setText("Загрузить картинку…")
        else:
            self.what.setText("Копия вашей витрины — друг поставит её себе и переделает как захочет" if self.sc else
                              "Витрины пока нет — соберите её в своём профиле («Оформить профиль → Витрина»)")
            self.b1.setText("")
            self.b2.setText("")
        self.b1.setVisible(bool(self.b1.text()))
        self.b2.setVisible(bool(self.b2.text()))
        self.prev.update()

    def _source(self, n):
        import net_frames
        if self.kind == "track":
            if n == 1:
                cur = self.net.shell.current_track()
                if cur:
                    from net_page import track_meta
                    self.track = track_meta(cur)
            else:
                from net_playlist_ui import TrackSearch, track_to_meta
                d = TrackSearch(self.net.win, self, title="Трек в подарок")
                d.file_btn.hide()
                d.ok.setText("Выбрать")
                if d.exec() and d.result_tracks:
                    self.track = track_to_meta(d.result_tracks[0])
        elif self.kind == "frame":
            av = self.net._my_avatar_pm(160)
            if n == 1:
                d = net_frames.FrameDrawer(av, None, self)
                if d.exec() and getattr(d, "result_png", ""):
                    self.frame = {"id": "custom", "png": d.result_png}
            else:
                from PyQt6.QtGui import QImage
                from PyQt6.QtWidgets import QFileDialog
                fn, _ = QFileDialog.getOpenFileName(self, "Картинка рамки", "", "Картинки (*.png *.jpg *.jpeg *.webp)")
                img = QImage(fn) if fn else QImage()
                if not img.isNull():
                    png = net_frames.png_b64(net_frames.cut_center(img))
                    if png:
                        self.frame = {"id": "custom", "png": png}
        self._set_kind(self.kind)

    def gift(self) -> dict | None:
        g = {"g": self.kind, "card": self.card}
        if self.kind == "frame":
            if not self.frame:
                return None
            g["frame"] = self.frame
        elif self.kind == "showcase":
            if not self.sc:
                return None
            g["sc"] = pack_showcase(self.sc)
        elif not self.track:
            return None
        return g

    def _ok(self):
        if self.gift() is None:
            self.what.setText("<span style='color:#ff6b6b'>Сначала выберите, что подарить</span>")
            return
        self.accept()


class _Preview(QWidget):
    """Открытка, как её увидит друг."""

    def __init__(self, dlg):
        super().__init__(dlg)
        self.dlg = dlg
        self.setMinimumHeight(140)

    def paintEvent(self, _):
        d = self.dlg
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect().adjusted(1, 1, -1, -1)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(card_brush(d.card, r.width(), r.height()))
        p.drawRoundedRect(r, 18, 18)
        p.setPen(QColor(255, 255, 255))
        f = p.font()
        f.setPixelSize(15)
        f.setBold(True)
        p.setFont(f)
        p.drawText(r.adjusted(18, 14, -18, -14), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
                   f"\U000f1004  Вам подарок — {KINDS[d.kind]}")
        f.setPixelSize(19)
        f.setItalic(True)
        p.setFont(f)
        p.drawText(r.adjusted(18, 40, -18, -14), Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap,
                   d.note.text() or "…")
        p.end()


def send_dialog(net):
    if not getattr(net, "_chat_peer", None):
        return
    d = GiftDialog(net)
    if d.exec():
        g = d.gift()
        net._send_special(d.note.text().strip(), d.track if d.kind == "track" else None, {"kind": "gift", "gift": g})
        net.toast("Подарок отправлен \U000f1004")


# ══════════════════════════ получение ══════════════════════════ #

class GiftCard(QFrame):
    """Открытка в чате: пожелание, превью подарка и кнопка «Надеть / Поставить / В плейлист»."""

    def __init__(self, net, gift: dict, note: str, track, out: bool, sender: str):
        super().__init__()
        self.net, self.gift, self.track, self.sender = net, gift, track, sender
        _n, a, b = CARDS[gift.get("card", 0)]
        self.setObjectName("GiftCard")
        self.setStyleSheet(f"QFrame#GiftCard{{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {a},stop:1 {b});"
                           "border-radius:16px;} QLabel{background:transparent;color:#fff;}"
                           "QPushButton{background:rgba(0,0,0,0.35);color:#fff;border:none;border-radius:12px;"
                           "padding:7px 14px;font-weight:800;} QPushButton:hover{background:rgba(0,0,0,0.5);}")
        self.setMinimumWidth(300)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 12, 16, 12)
        v.setSpacing(8)
        k = gift.get("g")
        t = QLabel(("\U000f1004  Вы подарили " if out else "\U000f1004  Вам подарили ") + KINDS.get(k, "подарок"))
        t.setStyleSheet("font-size:14px;font-weight:900;")
        v.addWidget(t)
        if note:
            n = QLabel(note)
            n.setWordWrap(True)
            n.setStyleSheet("font-size:18px;font-weight:800;font-style:italic;")
            v.addWidget(n)
        if k == "frame":
            pv = QLabel()
            pv.setFixedSize(96, 96)
            pv.setPixmap(self._framed(96))
            v.addWidget(pv)
        elif k == "showcase":
            import net_showcase
            view = net_showcase.ShowcaseView(gift["sc"], net_showcase.Ctx(name=net.node._name() if net.node else ""),
                                             max_w=380, min_w=300)
            view.setFixedWidth(380)
            v.addWidget(view)
        self.status = QLabel()
        self.status.setStyleSheet("font-size:12px;font-weight:700;")
        if not out:
            act = {"frame": "Надеть рамку", "showcase": "Поставить в профиль", "track": f"В плейлист «{GIFT_PL}»"}[k]
            self.btn = QPushButton(act)
            self.btn.clicked.connect(self.apply)
            row = QHBoxLayout()
            row.addWidget(self.btn)
            row.addWidget(self.status, 1)
            v.addLayout(row)

    def _framed(self, size) -> QPixmap:
        import net_frames
        return net_frames.framed(self.net._my_avatar_pm(size), self.gift["frame"], size)

    def apply(self):
        win = self.net.win
        k = self.gift.get("g")
        if k == "track":
            if self.track:
                _to_playlist(win, self.track)
            self.status.setText(f"Добавлено в «{GIFT_PL}»")
            return
        d = dict(win.settings.get("ya_profile_deco") or {})
        if k == "frame":
            keep_frame(win, self.gift["frame"], self.sender)
            d["frame"] = dict(self.gift["frame"])
            self.status.setText("Рамка надета — она теперь и в «Рамка профиля»")
        else:
            if (d.get("sc") or {}).get("items") and not getattr(self, "_force", False):
                r = QMessageBox.question(self, "Витрина", "Заменить вашу витрину подаренной?")
                if r != QMessageBox.StandardButton.Yes:
                    return
            d["sc"] = dict(self.gift["sc"])
            self.status.setText("Витрина в профиле — её можно переделать")
        win.settings["ya_profile_deco"] = d
        try:
            win.save_settings()
        except Exception:                                      # noqa: BLE001
            pass
        try:
            self.net._deco_changed()
        except Exception:                                      # noqa: BLE001
            pass


def _to_playlist(win, track):
    import stream_core
    st = stream_core.make_track(track)
    pl = win.playlists.setdefault(GIFT_PL, {"tracks": [], "desc": "auto: треки, которые подарили друзья"})
    tr = pl.setdefault("tracks", [])
    if not any((t.get("artist"), t.get("title")) == (st.get("artist"), st.get("title")) for t in tr):
        tr.insert(0, st)
    try:
        win._save_playlists()
    except Exception:                                          # noqa: BLE001
        pass
