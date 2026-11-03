# net_moments.py
"""
Реакции на момент трека: «🔥 на 1:23» — видят друзья.

  * в плеере кнопка реакции: эмодзи на текущую секунду трека; друзья в сети получают сразу, остальные — позже
    (свои последние реакции переотправляются друзьям раз в 10 минут, повторы отбрасываются);
  * когда вы слушаете трек, на который друзья реагировали, в нужную секунду над плеером всплывает «кент 🔥»,
    а на полоске прогресса — метки этих моментов.
Хранится в ~/Echoes/net/moments.json: {трек: [{id, who, name, sec, emo, ts}]}.
Сеть: mreact (только между друзьями).
"""
from __future__ import annotations

import data_home
import json
import math
import secrets
import time
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath
from PyQt6.QtWidgets import QWidget

EMOJI = ("🔥", "❤️", "😮", "😂", "💀", "🎧")
FILE = data_home.root() / "net" / "moments.json"
_DATA = None


def _tk(meta) -> str:
    from net_page import tkey
    a, t = tkey(meta)
    return f"{a}|{t}"


def data() -> dict:
    global _DATA
    if _DATA is None:
        try:
            _DATA = json.loads(FILE.read_text(encoding="utf-8"))
            if not isinstance(_DATA, dict):
                _DATA = {}
        except (OSError, ValueError):
            _DATA = {}
    return _DATA


def _save():
    try:
        FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data(), ensure_ascii=False), encoding="utf-8")
        tmp.replace(FILE)
    except OSError:
        pass


def add(meta, r) -> bool:
    """Запомнить реакцию (повтор по id — нет)."""
    lst = data().setdefault(_tk(meta), [])
    if any(x.get("id") == r.get("id") for x in lst):
        return False
    lst.append(r)
    lst.sort(key=lambda x: x.get("sec", 0))
    del lst[:-200]
    _save()
    return True


def for_track(meta) -> list:
    return list(data().get(_tk(meta)) or [])


def react(net, track, sec, emo):
    """Своя реакция на момент: запомнить и разослать друзьям."""
    from net_page import track_meta
    meta = track_meta(track) or {}
    me = net.node.me.onion if net.node else ""
    r = {"id": secrets.token_hex(6), "who": me, "name": net.node._name() if net.node else "", "sec": round(float(sec), 1),
         "emo": emo if emo in EMOJI else "🔥", "ts": time.time()}
    add(meta, r)
    mine = data().setdefault("_mine", [])
    mine.append({"track": meta, "r": r})
    del mine[:-50]
    _save()
    _send_all(net, [{"track": meta, "r": r}])
    m, s = divmod(int(sec), 60)
    net.toast(f"{emo} на {m}:{s:02d} — друзья увидят, когда будут слушать этот трек")


def _send_all(net, items):
    if net.node is None or not net.running():
        return
    for c in net.node.store.contacts("friend"):
        for it in items:
            net.node.call(net.node.quiz_send, c["onion"], {"t": "mreact", "track": it["track"], **it["r"]})


def resend(net):
    """Раз в 10 минут — свои последние реакции друзьям (кто был не в сети, догонит)."""
    _send_all(net, (data().get("_mine") or [])[-20:])


def on_message(net, o, d):
    meta = d.get("track")
    if not meta or d.get("emo") not in EMOJI:
        return
    r = {"id": d.get("id", ""), "who": o, "name": d.get("name") or net._contact_label(o), "sec": float(d.get("sec") or 0),
         "emo": d["emo"], "ts": float(d.get("ts") or time.time())}
    if add(meta, r):
        cur = net.shell.current_track()
        from net_page import tkey
        if cur and tkey(cur) == tkey(meta):
            refresh_marks(net.shell)


# ── во время прослушивания ── #

class Bubble(QWidget):
    """Всплывающее «кент 🔥» над плеером: поднимается и тает."""

    def __init__(self, host, text, x, y):
        super().__init__(host)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.text = text
        self.t0 = time.monotonic()
        self.base = (x, y)
        f = QFont()
        f.setPixelSize(15)
        f.setBold(True)
        self.f = f
        from PyQt6.QtGui import QFontMetrics
        w = QFontMetrics(f).horizontalAdvance(text) + 30
        self.resize(w, 40)
        self.move(int(x - w / 2), int(y))
        self.show()
        self.raise_()
        self.tm = QTimer(self)
        self.tm.setInterval(16)
        self.tm.timeout.connect(self._step)
        self.tm.start()

    def _step(self):
        t = time.monotonic() - self.t0
        if t > 3.0:
            self.tm.stop()
            self.deleteLater()
            return
        x, y = self.base
        self.move(int(x - self.width() / 2 + math.sin(t * 3) * 6), int(y - t * 26))
        self.update()

    def paintEvent(self, _):
        t = time.monotonic() - self.t0
        a = min(1.0, t / 0.25) * min(1.0, max(0.0, (3.0 - t) / 0.8))
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(a)
        r = QRectF(2, 2, self.width() - 4, self.height() - 6)
        path = QPainterPath()
        path.addRoundedRect(r, r.height() / 2, r.height() / 2)
        p.fillPath(path, QColor(20, 20, 24, 225))
        p.setFont(self.f)
        p.setPen(QColor(255, 255, 255))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.text)
        p.end()


def refresh_marks(shell):
    """Метки моментов на полоске прогресса играющего трека."""
    prog = getattr(shell, "progress", None)
    t = shell.current_track()
    if prog is None:
        return
    rs = [r for r in (for_track(t) if t else []) if r.get("who") != _me(shell)]
    prog.moments = rs
    prog.update()


def _me(shell):
    net = shell.pages.get("net")
    return net.node.me.onion if net is not None and net.node is not None else ""


def tick(shell, pos, dur):
    """Из таймера оболочки (10 раз/с): дошли до чужой реакции — всплывашка."""
    prog = getattr(shell, "progress", None)
    rs = getattr(prog, "moments", None) or []
    if not rs or dur <= 0:
        shell.__dict__["_mom_last"] = pos
        return
    last = shell.__dict__.get("_mom_last", pos)
    shell._mom_last = pos
    if not (0 <= pos - last < 1.5):                            # перемотка — не сыпать всплывашками
        return
    for r in rs:
        if last < r.get("sec", -1) <= pos:
            cov = getattr(shell, "p_cover", None)
            host = shell
            if cov is not None:
                c = cov.mapTo(host, cov.rect().center())
                x, y = c.x() + 120, c.y() - 70
            else:
                x, y = host.width() / 2, host.height() - 140
            Bubble(host, f"{r.get('name', '')} {r.get('emo', '')}", x, y)


def show_picker(shell, anchor):
    """Кнопка в плеере: выбрать эмодзи — реакция на текущую секунду."""
    from PyQt6.QtWidgets import QMenu
    net = shell.pages.get("net")
    t = shell.current_track()
    if net is None or net.node is None or not t:
        if net is not None:
            net.toast("Включите трек и сеть Echoes.NET")
        return
    try:
        sec = float(shell.win.engine.get_position() or 0)
    except Exception:                                          # noqa: BLE001
        sec = 0.0
    m = QMenu(shell)
    mm, ss = divmod(int(sec), 60)
    m.addSection(f"Реакция на {mm}:{ss:02d}")
    for e in EMOJI:
        m.addAction(e, lambda e=e: (react(net, t, sec, e), refresh_marks(shell)))
    m.exec(anchor.mapToGlobal(anchor.rect().topLeft()))


def paint_marks(prog, p, x0, x1, H):
    """Для ProgressLine: точки-метки чужих реакций над полоской."""
    rs = getattr(prog, "moments", None) or []
    if not rs or prog.dur <= 0:
        return
    p.setPen(Qt.PenStyle.NoPen)
    for r in rs:
        f = max(0.0, min(1.0, r.get("sec", 0) / prog.dur))
        x = x0 + (x1 - x0) * f
        p.setBrush(QColor(255, 120, 80, 230))
        p.drawEllipse(QPointF(x, 2.5), 2.2, 2.2)
