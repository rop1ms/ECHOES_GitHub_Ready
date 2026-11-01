# net_weektop.py
"""
Топ недели среди друзей: что больше всего слушала ваша компания за последние 7 дней.

Каждый ECHOES кладёт в свою карточку профиля (вместе со временем прослушивания) топ своих треков за 7 дней
(lt.wtrs: [название, исполнитель, прослушиваний]). Чарт складывает свои и друзей: место — по числу друзей,
которые слушали трек, потом по прослушиваниям. Карточка — во вкладке «Друзья»; по воскресеньям — уведомление
«Топ недели друзей готов» (один раз в неделю).
"""
from __future__ import annotations

import time
from collections import defaultdict

from PyQt6.QtCore import QObject, Qt, QTimer
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout


def my_week_tracks(win, n=15) -> list:
    st = getattr(win, "stats", None)
    if st is None or not hasattr(st, "history"):
        return []
    cut = time.time() - 7 * 86400
    y = time.localtime().tm_year
    cnt, meta = defaultdict(int), {}
    for yy in {y, time.localtime(cut).tm_year}:
        for r in st.history(yy):
            if r["ts"] >= cut and r.get("title"):
                k = (r.get("artist", ""), r.get("title", ""))
                cnt[k] += 1
                meta[k] = r
    top = sorted(cnt.items(), key=lambda kv: -kv[1])[:n]
    return [[t, a, c] for (a, t), c in top]


def chart(net, n=10) -> list:
    """[(название, исполнитель, прослушиваний, [имена друзей])] — у кого больше слушателей, выше."""
    from net_page import tkey
    nd = net.node
    people = [(nd._name() + " (вы)", (my_week_tracks(net.win)))]
    for c in nd.store.contacts("friend"):
        lt = (nd.contact_profile(c["onion"]) or {}).get("lt") or {}
        if lt.get("wtrs"):
            people.append((c.get("name") or c["onion"][:8], lt["wtrs"]))
    agg = {}
    for who, rows in people:
        for row in rows or []:
            if not isinstance(row, list) or len(row) < 3:
                continue
            t, a, c = row[0], row[1], int(row[2] or 0)
            k = tkey({"title": t, "artist": a})
            e = agg.setdefault(k, {"title": t, "artist": a, "plays": 0, "who": []})
            e["plays"] += c
            if who not in e["who"]:
                e["who"].append(who)
    rows = sorted(agg.values(), key=lambda e: (-len(e["who"]), -e["plays"]))[:n]
    return [(e["title"], e["artist"], e["plays"], e["who"]) for e in rows]


def card(net) -> QFrame:
    from net_page import TrackChip, lbl
    f = QFrame()
    f.setObjectName("WeekTop")
    f.setStyleSheet("QFrame#WeekTop{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 rgba(124,77,255,0.25),"
                    "stop:1 rgba(47,198,255,0.08));border-radius:18px;} QLabel{background:transparent;}")
    v = QVBoxLayout(f)
    v.setContentsMargins(18, 14, 18, 14)
    v.setSpacing(6)
    sunday = time.localtime().tm_wday == 6
    k = QLabel("ТОП НЕДЕЛИ СРЕДИ ДРУЗЕЙ" + ("  ·  свежий, воскресенье" if sunday else "  ·  последние 7 дней"))
    k.setStyleSheet("color:#c9b8ff;font-size:11px;font-weight:800;letter-spacing:1px;")
    v.addWidget(k)
    rows = chart(net)
    if not rows:
        v.addWidget(lbl("Пока пусто: топ соберётся, когда вы и друзья послушаете музыку (нужна новая версия ECHOES "
                        "у друзей).", "YaSub", wrap=True))
    for i, (t, a, plays, who) in enumerate(rows, 1):
        r = QHBoxLayout()
        n = QLabel(str(i))
        n.setFixedWidth(22)
        n.setStyleSheet("font-weight:900;color:#ffdb1a;font-size:15px;")
        r.addWidget(n)
        meta = {"title": t, "artist": a}
        r.addWidget(TrackChip(meta, net.play_meta, compact=True, cover=net._local_cover(meta)), 1)
        w = QLabel(f"{len(who)} слуш. · {plays}×")
        w.setToolTip("Слушали: " + ", ".join(who))
        w.setStyleSheet("color:#bdbdbd;font-weight:700;")
        r.addWidget(w)
        v.addLayout(r)
    return f


class SundayAlert(QObject):
    """По воскресеньям (один раз в неделю, днём) — «Топ недели друзей готов»."""

    def __init__(self, net):
        super().__init__(net)
        self.net = net
        self.t = QTimer(self)
        self.t.setInterval(15 * 60000)
        self.t.timeout.connect(self.check)
        self.t.start()

    def check(self):
        lt = time.localtime()
        nd = self.net.node
        if nd is None or lt.tm_wday != 6 or lt.tm_hour < 12:
            return
        week = time.strftime("%G-%V")
        if nd.settings.get("weektop_seen") == week:
            return
        rows = chart(self.net, 3)
        if not rows:
            return
        nd.settings["weektop_seen"] = week
        nd.save_settings()
        self.net.alert("info", "Топ недели среди друзей готов",
                       "№1 — " + f"{rows[0][1]} — {rows[0][0]}" + " · Echoes.NET → Друзья", tab="friends", force=True)
