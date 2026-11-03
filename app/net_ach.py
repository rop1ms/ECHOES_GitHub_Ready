# net_ach.py
"""
Ачивки Echoes.NET: прогресс (сколько уже есть из нужного), вкладка «Ачивки» в настройках Echoes.NET
и маленькое окошко по клику на ачивку в профиле — что это, как получить, у кого она есть и какая рамка открыта.
"""
from __future__ import annotations

from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QFont, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient
from PyQt6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

NEED = {"first_post": 1, "author": 10, "social": 5, "dj": 1, "together": 1, "inseparable": 3, "sleuth": 3,
        "hitmaker": 1, "melomane": 100, "collector": 500, "group": 1, "champion": 1,
        "night_owl": 50, "early_bird": 50, "streak7": 7, "explorer": 100, "loyal": 100, "marathon": 5}
UNIT = {"first_post": "пост", "author": "постов", "social": "друзей", "dj": "комната", "together": "комната",
        "inseparable": "дня подряд", "sleuth": "угадано", "hitmaker": "№1 в чарте", "melomane": "часов",
        "collector": "треков", "group": "группа", "champion": "победа", "night_owl": "ночных прослушиваний",
        "early_bird": "утренних прослушиваний", "streak7": "дней подряд", "explorer": "исполнителей",
        "loyal": "прослушиваний любимого исполнителя", "marathon": "часов за день"}


def listening_counts(win) -> dict:
    """Сколько уже есть по «слушательским» ачивкам: ночь, утро, серия дней, исполнители, фанат, марафон."""
    import time as _t
    st = win.stats
    night = morning = 0
    y = _t.localtime().tm_year
    for yy in (y - 1, y):
        for r in (st.history(yy) if hasattr(st, "history") else []):
            h = _t.localtime(r["ts"]).tm_hour
            night += h < 5
            morning += 5 <= h < 9
    days = sorted(k for k, v in (st.data.get("days") or {}).items() if v >= 600)
    best = run = 0
    prev = None
    for d in days:
        ts = _t.mktime(_t.strptime(d, "%Y-%m-%d"))
        run = run + 1 if prev is not None and abs(ts - prev - 86400) < 7200 else 1
        best = max(best, run)
        prev = ts
    arts = st.data.get("artists") or {}
    return {"night_owl": night, "early_bird": morning, "streak7": best,
            "explorer": sum(1 for k, v in arts.items() if k and v.get("plays")),
            "loyal": max([int(v.get("plays", 0)) for v in arts.values()] or [0]),
            "marathon": int(max(list((st.data.get("days") or {}).values()) or [0]) // 3600)}


def progress(net) -> dict:
    """{ачивка: (сколько есть, сколько нужно)} — для своего профиля."""
    nd = net.node
    st = nd.store
    me = nd.me.onion
    stats = net._stats_data()
    posts = st.posts(me, 500)
    solved = st.q("SELECT COUNT(*) n FROM reacts WHERE who=? AND kind='solved' AND val='1'", (me,))[0]["n"]
    friends = st.contacts("friend")
    chart = net.friends_chart()
    from net_page import tkey
    hit = bool(chart) and any(tkey(p.get("track")) == tkey(chart[0][0]) for p in posts if p.get("track"))
    cur = {"first_post": len(posts), "author": len(posts), "social": len(friends),
           "dj": int(nd.settings.get("rooms_hosted", 0)), "together": int(nd.settings.get("rooms_joined", 0)),
           "inseparable": max([net.together_streak(c["onion"]) for c in friends] or [0]),
           "sleuth": int(solved), "hitmaker": int(hit), "melomane": int(float(stats.get("total") or 0) // 3600),
           "collector": len(net.win.library), "group": sum(1 for g in st.groups() if g.get("owner") == me),
           "champion": int(__import__("net_challenge").is_champion(net))}
    cur.update(listening_counts(net.win))
    return {k: (cur[k], NEED[k]) for k in NEED}


def medal(kind: str, size: int, have: bool) -> QPixmap:
    """Значок ачивки: её рамка вокруг звезды (не полученная — серая)."""
    import net_frames
    dpr = 2
    pm = QPixmap(size * dpr, size * dpr)
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    c = QPointF(size / 2, size / 2)
    g = QRadialGradient(c, size * 0.4)
    g.setColorAt(0, QColor("#3a3320") if have else QColor("#26262a"))
    g.setColorAt(1, QColor("#16161a"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(g)
    p.drawEllipse(c, size * 0.4, size * 0.4)
    from ach_icons import paint_ach                           # у каждой ачивки — свой значок
    s = size * 0.44
    paint_ach(p, kind, QRectF(c.x() - s / 2, c.y() - s / 2, s, s), QColor("#ffdb1a") if have else QColor("#55555c"))
    if not have:
        p.setOpacity(0.35)
    fr = net_frames.frame_pixmap({"id": "ach:" + kind}, size)
    if fr is not None:
        p.drawPixmap(QRectF(0, 0, size, size), fr, QRectF(fr.rect()))
    p.end()
    return pm


class _Bar(QWidget):
    def __init__(self, cur, need, parent=None):
        super().__init__(parent)
        self.cur, self.need = cur, need
        self.setFixedHeight(8)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 28))
        p.drawRoundedRect(QRectF(0, 0, W, H), H / 2, H / 2)
        f = max(0.0, min(1.0, self.cur / max(1, self.need)))
        if f > 0:
            p.setBrush(QColor("#ffdb1a") if f >= 1 else QColor("#bfa21a"))
            p.drawRoundedRect(QRectF(0, 0, max(H, W * f), H), H / 2, H / 2)
        p.end()


def _status(kind, have, prog) -> str:
    if have:
        return "★  Получена"
    if prog is None:
        return "Ещё не получена"
    cur, need = prog
    return f"{min(cur, need)} из {need} {UNIT[kind]}"


class AchPopup(QFrame):
    """Маленькое окошко ачивки по клику: значок, название, как получить, у кого есть, рамка."""

    def __init__(self, net, kind, have, owner_name="", me=True, parent=None):
        super().__init__(parent or net, Qt.WindowType.Popup)
        import net_frames
        self.setObjectName("AchPop")
        self.setStyleSheet("QFrame#AchPop{background:#18181b;border:1px solid rgba(255,255,255,0.12);border-radius:16px;}"
                           "QLabel{background:transparent;color:#f0f0f0;}")
        self.setFixedWidth(320)
        title, how = net.ACH.get(kind, (kind, ""))
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(6)
        top = QHBoxLayout()
        ic = QLabel()
        ic.setFixedSize(84, 84)
        ic.setPixmap(medal(kind, 84, have))
        top.addWidget(ic)
        col = QVBoxLayout()
        col.setSpacing(2)
        t = QLabel(title)
        t.setStyleSheet("font-size:18px;font-weight:900;")
        col.addWidget(t)
        st = QLabel("★  Получена" if have else "Не получена")
        st.setStyleSheet(f"font-size:12px;font-weight:800;color:{'#ffdb1a' if have else '#8a8a8a'};")
        col.addWidget(st)
        if not me and owner_name:
            o = QLabel(f"у {owner_name}")
            o.setStyleSheet("font-size:11px;color:#8a8a8a;")
            col.addWidget(o)
        col.addStretch(1)
        top.addLayout(col, 1)
        v.addLayout(top)
        h = QLabel("Как получить: " + how)
        h.setWordWrap(True)
        h.setStyleSheet("font-size:12px;color:#cfcfcf;")
        v.addWidget(h)
        mine = None
        try:
            mine = progress(net).get(kind)
        except Exception:                                      # noqa: BLE001
            pass
        if mine is not None:
            got = mine[0] >= mine[1]
            lab = QLabel(("Ваш прогресс: " if not me else "Прогресс: ") + _status(kind, got, mine))
            lab.setStyleSheet("font-size:11px;color:#b5b5b5;")
            v.addWidget(lab)
            v.addWidget(_Bar(*mine))
        fr = QLabel(f"Открывает рамку аватарки «{net_frames.ACH_FRAMES.get(kind, '')}»")
        fr.setStyleSheet("font-size:11px;color:#8a8a8a;")
        v.addWidget(fr)

    def show_at(self, gpos: QPoint):
        self.adjustSize()
        scr = self.screen().availableGeometry() if self.screen() else None
        x, y = gpos.x() + 8, gpos.y() + 8
        if scr is not None:
            x = min(x, scr.right() - self.width() - 8)
            y = min(y, scr.bottom() - self.height() - 8)
        self.move(x, y)
        self.show()


def popup(net, kind, have, owner_name="", me=True, at=None):
    p = AchPopup(net, kind, have, owner_name, me)
    p.show_at(at or QCursor.pos())
    net._ach_popup = p                                         # не дать сборщику мусора закрыть
    return p


class AchCard(QFrame):
    """Плитка на вкладке «Ачивки»: значок, название, как получить, прогресс. Клик — окошко подробнее."""
    clicked = pyqtSignal()

    def __init__(self, net, kind, prog):
        super().__init__()
        cur, need = prog
        have = cur >= need
        self.setObjectName("AchCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("QFrame#AchCard{background:%s;border-radius:16px;border:1px solid %s;}"
                           "QFrame#AchCard:hover{border-color:rgba(255,219,26,0.6);} QLabel{background:transparent;}" %
                           (("rgba(255,219,26,0.08)", "rgba(255,219,26,0.35)") if have else
                            ("rgba(255,255,255,0.04)", "rgba(255,255,255,0.06)")))
        title, how = net.ACH.get(kind, (kind, ""))
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 10, 14, 10)
        h.setSpacing(12)
        ic = QLabel()
        ic.setFixedSize(64, 64)
        ic.setPixmap(medal(kind, 64, have))
        h.addWidget(ic)
        col = QVBoxLayout()
        col.setSpacing(3)
        t = QLabel(title)
        t.setStyleSheet("font-size:15px;font-weight:900;color:%s;" % ("#ffdb1a" if have else "#f0f0f0"))
        col.addWidget(t)
        d = QLabel(how)
        d.setWordWrap(True)
        d.setStyleSheet("font-size:12px;color:#a8a8a8;")
        col.addWidget(d)
        col.addWidget(_Bar(cur, need))
        s = QLabel(_status(kind, have, prog))
        s.setStyleSheet("font-size:11px;font-weight:700;color:%s;" % ("#ffdb1a" if have else "#8a8a8a"))
        col.addWidget(s)
        h.addLayout(col, 1)
        self.clicked.connect(lambda: popup(net, kind, have))

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


def fill_page(net, v):
    """Вкладка «Ачивки» в настройках Echoes.NET."""
    from yamusic_theme import section_title
    try:
        prog = progress(net)
    except Exception as e:                                     # noqa: BLE001
        v.addWidget(QLabel(f"Не удалось посчитать ачивки: {e}"))
        return
    n = sum(1 for c, need in prog.values() if c >= need)
    v.addWidget(section_title(f"Ачивки · {n} из {len(prog)}"))
    sub = QLabel("Ачивки видны в профиле всем. Каждая открывает свою рамку аватарки (Профиль → «Оформить профиль» → "
                 "«Рамка аватарки»). Нажмите на ачивку — подробнее.")
    sub.setObjectName("YaSub")
    sub.setWordWrap(True)
    v.addWidget(sub)
    v.addSpacing(8)
    box = QWidget()
    box.setObjectName("YaPage")
    g = QGridLayout(box)
    g.setContentsMargins(0, 0, 0, 0)
    g.setSpacing(10)
    order = sorted(net.ACH, key=lambda k: (prog[k][0] < prog[k][1], -prog[k][0] / max(1, prog[k][1])))
    for i, k in enumerate(order):
        g.addWidget(AchCard(net, k, prog[k]), i // 2, i % 2)
    v.addWidget(box)
