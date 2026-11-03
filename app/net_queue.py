# net_queue.py
"""
Очередь от друзей: друг «подкидывает» трек в вашу очередь (если вы разрешили).

  * разрешение — Echoes.NET → Настройки: «спрашивать» (по умолчанию) / «сразу добавлять» / «никому»;
  * трек есть в вашей коллекции — встаёт в очередь следующим; нет — в плейлист «Подкинули друзья»
    (поток), его можно сразу включить;
  * другу приходит ответ: добавлено или нет.
Сеть: сообщения q_add / q_res (только между друзьями).
"""
from __future__ import annotations

import secrets
import time

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

PL_NAME = "Подкинули друзья"
MODES = (("ask", "Спрашивать"), ("auto", "Сразу добавлять"), ("off", "Никому"))


def mode(net) -> str:
    m = (net.node.settings.get("friend_queue") if net.node else None) or "ask"
    return m if m in dict(MODES) else "ask"


def send(net, onion, track_meta, note=""):
    if net.node is None or not net.running():
        net.toast("Включите сеть, чтобы подкинуть трек")
        return
    msg = {"t": "q_add", "id": secrets.token_hex(6), "track": track_meta, "note": note}
    net.node.call(net.node.quiz_send, onion, msg)
    net.toast(f"Трек отправлен: {net._contact_label(onion)} увидит его в очереди")


def _add(net, track) -> str:
    """Добавить у себя: в очередь (свой файл) или в плейлист «Подкинули друзья». Возвращает, куда."""
    import net_music
    win = net.win
    _i, local = net_music.library_match(win, track)
    if local is not None:
        win.queue_add([local], next_=True)
        return "queue"
    import stream_core
    st = stream_core.make_track(track)
    pl = win.playlists.setdefault(PL_NAME, {"tracks": [], "desc": "auto: треки, которые подкинули друзья"})
    tr = pl.setdefault("tracks", [])
    if not any((t.get("artist"), t.get("title")) == (st.get("artist"), st.get("title")) for t in tr):
        tr.insert(0, st)
    try:
        win._save_playlists()
    except Exception:                                          # noqa: BLE001
        pass
    return "playlist"


def _reply(net, onion, mid, ok, how=""):
    net.node.call(net.node.quiz_send, onion, {"t": "q_res", "id": mid, "ok": ok, "how": how})


class Offer(QFrame):
    """«кент подкидывает трек» — в правом верхнем углу окна: Добавить / Включить / Нет."""

    def __init__(self, net, onion, track, note, mid):
        super().__init__(net.win)
        self.net, self.onion, self.track, self.mid = net, onion, track, mid
        self.setObjectName("QOffer")
        self.setStyleSheet("QFrame#QOffer{background:rgba(24,24,28,0.97);border:1px solid rgba(255,255,255,0.12);"
                           "border-radius:16px;} QLabel{background:transparent;color:#f0f0f0;}"
                           "QPushButton{background:rgba(255,255,255,0.1);color:#fff;border:none;border-radius:12px;"
                           "padding:7px 12px;font-weight:800;} QPushButton:hover{background:rgba(255,255,255,0.18);}"
                           "QPushButton#Acc{background:#ffdb1a;color:#000;}")
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(6)
        who = net._contact_label(onion)
        import html as _h                                # имя и трек — от собеседника: экранируем, не разметка
        t = QLabel(f"<b>{_h.escape(str(who))}</b> подкидывает трек")
        v.addWidget(t)
        tr = QLabel(f"<b>{_h.escape(str(track.get('title', '')))}</b> — {_h.escape(str(track.get('artist', '')))}")
        tr.setWordWrap(True)
        v.addWidget(tr)
        if note:
            n = QLabel(f"«{note}»")
            n.setWordWrap(True)
            n.setStyleSheet("color:#bdbdbd;")
            v.addWidget(n)
        h = QHBoxLayout()
        a = QPushButton("В очередь")
        a.setObjectName("Acc")
        a.clicked.connect(self.accept)
        p = QPushButton("Включить сейчас")
        p.clicked.connect(self.play_now)
        r = QPushButton("Нет")
        r.clicked.connect(self.reject)
        for b in (a, p, r):
            h.addWidget(b)
        v.addLayout(h)
        self.setFixedWidth(360)
        self.adjustSize()
        w = net.win
        self.move(w.width() - self.width() - 20, 70)
        self.show()
        self.raise_()
        QTimer.singleShot(60000, self._expire)                 # минуту ждём ответа

    def accept(self):
        how = _add(self.net, self.track)
        self.net.toast("Добавлено следующим в очередь" if how == "queue" else
                       f"Этого трека нет в коллекции — он в плейлисте «{PL_NAME}»")
        _reply(self.net, self.onion, self.mid, True, how)
        self.hide()
        self.deleteLater()

    def play_now(self):
        self.net.play_meta(self.track)
        _reply(self.net, self.onion, self.mid, True, "now")
        self.hide()
        self.deleteLater()

    def reject(self):
        _reply(self.net, self.onion, self.mid, False)
        self.hide()
        self.deleteLater()

    def _expire(self):
        try:
            if self.isVisible():
                self.reject()
        except RuntimeError:
            pass


def on_add(net, d):
    """Входящее q_add."""
    o, track = d.get("from", ""), d.get("track")
    if not track:
        return
    m = mode(net)
    if m == "off":
        _reply(net, o, d.get("id", ""), False, "off")
        return
    if m == "auto":
        how = _add(net, track)
        _reply(net, o, d.get("id", ""), True, how)
        net.alert("message", f"{net._contact_label(o)} подкинул трек",
                  f"{track.get('title', '')} — {track.get('artist', '')}" +
                  (" · следующим в очереди" if how == "queue" else f" · в плейлисте «{PL_NAME}»"), onion=o)
        return
    Offer(net, o, track, d.get("note", ""), d.get("id", ""))
    try:
        net.alert("message", f"{net._contact_label(o)} подкидывает трек",
                  f"{track.get('title', '')} — {track.get('artist', '')}", onion=o)
    except Exception:                                          # noqa: BLE001
        pass


def on_res(net, d):
    who = net._contact_label(d.get("from", ""))
    if d.get("ok"):
        net.toast({"queue": f"{who} поставил ваш трек следующим", "playlist": f"{who} сохранил ваш трек",
                   "now": f"{who} включил ваш трек прямо сейчас"}.get(d.get("how"), f"{who} принял трек"))
    else:
        net.toast(f"{who} не принимает треки в очередь" if d.get("how") == "off" else f"{who} отказался от трека")


def pick_and_send(net, onion):
    """Меню: играющий трек / выбрать из коллекции → другу в очередь."""
    from PyQt6.QtGui import QCursor
    from PyQt6.QtWidgets import QMenu
    from net_page import track_meta
    m = QMenu(net)
    cur = net.shell.current_track()
    if cur:
        m.addAction(f"Играющий: {cur.get('title', '')}", lambda: send(net, onion, track_meta(cur)))

    def choose():
        from net_playlist_ui import TrackSearch, track_to_meta
        d = TrackSearch(net.win, net, title="Подкинуть трек другу")
        d.file_btn.hide()
        d.ok.setText("Подкинуть")
        if d.exec() and d.result_tracks:
            send(net, onion, track_to_meta(d.result_tracks[0]))
    m.addAction("Выбрать из коллекции…", choose)
    m.exec(QCursor.pos())


def settings_card(net):
    """Карточка в настройках Echoes.NET: кто может подкидывать треки."""
    from net_page import card, lbl
    f, v = card()
    v.addWidget(lbl("ОЧЕРЕДЬ ОТ ДРУЗЕЙ", "Section"))
    v.addWidget(lbl("Друзья могут подкинуть трек в вашу очередь — он встанет следующим (или в плейлист "
                    f"«{PL_NAME}», если его нет в коллекции).", "YaSub", wrap=True))
    h = QHBoxLayout()
    from yamusic_theme import chip
    cur = mode(net)
    for k, name in MODES:
        b = chip(name)
        b.setChecked(k == cur)

        def pick(_=False, k=k):
            net.node.settings["friend_queue"] = k
            net.node.save_settings()
            net._fill_net()
        b.clicked.connect(pick)
        h.addWidget(b)
    h.addStretch(1)
    v.addLayout(h)
    return f
