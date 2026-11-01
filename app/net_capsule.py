# net_capsule.py
"""
Капсулы времени (в чате): трек с посланием, который откроется у друга позже — через месяц, полгода, год
или в выбранный день (например, в день рождения). До открытия у друга видно только «капсула времени»;
когда время пришло — уведомление «Открылась капсула от …», даже если чат не открыт.
Сама капсула — обычное сообщение чата с extra {"kind": "capsule", "unlock": ts} (как и раньше).
"""
from __future__ import annotations

import json
import time

from PyQt6.QtCore import QDateTime, QObject, QTimer
from PyQt6.QtWidgets import (QDateTimeEdit, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout)


def more_times() -> list:
    now = time.time()
    return [("Через месяц", now + 30 * 86400), ("Через полгода", now + 182 * 86400), ("Через год", now + 365 * 86400)]


class CapsuleDialog(QDialog):
    """«Выбрать дату…»: когда откроется, послание, трек (играющий или из коллекции)."""

    def __init__(self, net):
        super().__init__(net.win)
        import net_frames
        self.net = net
        self.track = None
        cur = net.shell.current_track()
        if cur:
            from net_page import track_meta
            self.track = track_meta(cur)
        self.setWindowTitle("Капсула времени")
        self.setStyleSheet(net_frames._QSS + "QLineEdit, QDateTimeEdit{background:#1d1d20;color:#f0f0f0;"
                           "border:1px solid #2c2c30;border-radius:10px;padding:7px 10px;}")
        self.resize(460, 300)
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(8)
        t = QLabel("Капсула времени")
        t.setStyleSheet("font-size:20px;font-weight:900;")
        v.addWidget(t)
        s = QLabel("Друг увидит её только в выбранный момент — например, в свой день рождения.")
        s.setObjectName("Sub")
        s.setWordWrap(True)
        v.addWidget(s)
        v.addWidget(QLabel("Откроется:"))
        self.when = QDateTimeEdit(QDateTime.currentDateTime().addDays(30))
        self.when.setCalendarPopup(True)
        self.when.setDisplayFormat("dd.MM.yyyy  HH:mm")
        self.when.setMinimumDateTime(QDateTime.currentDateTime().addSecs(120))
        v.addWidget(self.when)
        self.text = QLineEdit()
        self.text.setPlaceholderText("Послание (необязательно)")
        v.addWidget(self.text)
        tr = QHBoxLayout()
        self.tr_lbl = QLabel()
        self.tr_lbl.setWordWrap(True)
        tr.addWidget(self.tr_lbl, 1)
        pick = QPushButton("Трек из коллекции…")
        pick.clicked.connect(self._pick)
        tr.addWidget(pick)
        v.addLayout(tr)
        self._show_track()
        v.addStretch(1)
        bot = QHBoxLayout()
        bot.addStretch(1)
        c = QPushButton("Отмена")
        c.clicked.connect(self.reject)
        ok = QPushButton("Отправить капсулу")
        ok.setObjectName("Accent")
        ok.clicked.connect(self._ok)
        bot.addWidget(c)
        bot.addWidget(ok)
        v.addLayout(bot)

    def _show_track(self):
        t = self.track
        self.tr_lbl.setText(f"Трек: <b>{t.get('title', '')}</b> — {t.get('artist', '')}" if t else "Без трека")

    def _pick(self):
        from net_playlist_ui import TrackSearch, track_to_meta
        d = TrackSearch(self.net.win, self, title="Трек для капсулы")
        d.file_btn.hide()
        d.ok.setText("Выбрать")
        if d.exec() and d.result_tracks:
            self.track = track_to_meta(d.result_tracks[0])
            self._show_track()

    def _ok(self):
        if not self.text.text().strip() and not self.track:
            self.text.setPlaceholderText("Напишите послание или выберите трек")
            return
        self.accept()

    def result(self):
        return self.text.text().strip(), self.track, float(self.when.dateTime().toSecsSinceEpoch())


def send_dialog(net):
    d = CapsuleDialog(net)
    if d.exec():
        text, tr, ts = d.result()
        net._send_special(text, tr, {"kind": "capsule", "unlock": ts})
        lt = time.localtime(ts)
        net.toast(f"Капсула откроется {lt.tm_mday:02d}.{lt.tm_mon:02d}.{lt.tm_year} в {lt.tm_hour:02d}:{lt.tm_min:02d}")


class Watch(QObject):
    """Раз в минуту: какие входящие капсулы уже открылись — уведомить (один раз)."""

    def __init__(self, net):
        super().__init__(net)
        self.net = net
        self.t = QTimer(self)
        self.t.setInterval(60000)
        self.t.timeout.connect(self.check)
        self.t.start()

    def check(self):
        nd = self.net.node
        if nd is None:
            return
        done = nd.settings.setdefault("capsules_seen", [])
        now = time.time()
        changed = False
        for c in nd.store.contacts("friend"):
            for m in nd.store.messages(c["onion"], 300):
                if m.get("dir") != "in":
                    continue
                try:
                    ex = m.get("extra") if isinstance(m.get("extra"), dict) else json.loads(m.get("extra") or "{}")
                except ValueError:
                    ex = {}
                if ex.get("kind") != "capsule":
                    continue
                key = str(m.get("id") or m.get("ts"))
                if float(ex.get("unlock") or 0) <= now and key not in done:
                    done.append(key)
                    changed = True
                    try:
                        self.net.alert("message", f"Открылась капсула времени от {c.get('name') or c['onion'][:8]}",
                                       (m.get("text") or "")[:120] or "Откройте чат — там трек", onion=c["onion"],
                                       force=True)
                    except Exception:                          # noqa: BLE001
                        pass
        if changed:
            del done[:-500]
            nd.save_settings()
