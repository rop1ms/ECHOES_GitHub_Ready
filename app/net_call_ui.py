"""Звонки Echoes.NET в стиле Discord: панель звонка с плитками участников (зелёное кольцо у говорящего),
круглые кнопки «микрофон / звук / шумодав / настройки / завершить», окно входящего звонка с пульсирующими
кольцами, мини-панель «Голосовая связь подключена» внизу слева и мелодия вызова.

CallController(page) — связывает net_core (call_* / voice_out) с голосовым движком net_voice и интерфейсом.
"""
from __future__ import annotations

import math
import time

from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF, QSize, QEvent, QObject
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QFont, QFontMetrics, QPixmap, QRadialGradient
from PyQt6.QtWidgets import (QWidget, QFrame, QHBoxLayout, QVBoxLayout, QLabel, QAbstractButton, QGridLayout,
                             QSizePolicy, QMenu)

GREEN = "#23a55a"                                              # «говорит» / «принять» — как в Discord
RED = "#da373c"
BG = "#111214"
PANEL = "#1e1f22"
BTN = "#2b2d31"
BTN_HOVER = "#35373c"


# ══════════════════════════ значки (рисуем сами) ══════════════════════════ #

def _mic(p, c, off=False):
    p.setPen(QPen(c, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(9, 3, 6, 11), 3, 3)
    path = QPainterPath()
    path.moveTo(6, 11)
    path.cubicTo(6, 17, 18, 17, 18, 11)
    p.drawPath(path)
    p.drawLine(QPointF(12, 17), QPointF(12, 20.5))
    if off:
        p.setPen(QPen(c, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(4, 4), QPointF(20, 20))


def _head(p, c, off=False):
    p.setPen(QPen(c, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    path.moveTo(4, 15)
    path.cubicTo(4, 2, 20, 2, 20, 15)
    p.drawPath(path)
    p.setBrush(c)
    p.drawRoundedRect(QRectF(3.5, 13, 4.5, 7), 1.5, 1.5)
    p.drawRoundedRect(QRectF(16, 13, 4.5, 7), 1.5, 1.5)
    if off:
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(c, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(4, 4), QPointF(20, 20))


def _phone(p, c, down=False):
    p.save()
    if down:
        p.translate(12, 12)
        p.rotate(135)
        p.translate(-12, -12)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c)
    path = QPainterPath()
    path.moveTo(6.5, 3.5)
    path.cubicTo(5, 3.5, 3.5, 5, 3.8, 7.2)
    path.cubicTo(4.8, 13.5, 10.5, 19.2, 16.8, 20.2)
    path.cubicTo(19, 20.5, 20.5, 19, 20.5, 17.5)
    path.lineTo(20.5, 15.6)
    path.cubicTo(20.5, 15, 20.1, 14.5, 19.5, 14.3)
    path.lineTo(16.2, 13.3)
    path.cubicTo(15.7, 13.1, 15.1, 13.3, 14.8, 13.7)
    path.lineTo(13.6, 15.2)
    path.cubicTo(11.4, 14.1, 9.9, 12.6, 8.8, 10.4)
    path.lineTo(10.3, 9.2)
    path.cubicTo(10.7, 8.9, 10.9, 8.3, 10.7, 7.8)
    path.lineTo(9.7, 4.5)
    path.cubicTo(9.5, 3.9, 9, 3.5, 8.4, 3.5)
    path.closeSubpath()
    p.drawPath(path)
    p.restore()


def _waves(p, c, off=False):
    p.setPen(QPen(c, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    for i, h in enumerate((5, 10, 14, 10, 5)):
        x = 5 + i * 3.5
        p.drawLine(QPointF(x, 12 - h / 2), QPointF(x, 12 + h / 2))
    if off:
        p.drawLine(QPointF(4, 4), QPointF(20, 20))


def _gear(p, c, off=False):
    p.setPen(QPen(c, 2))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QPointF(12, 12), 3.2, 3.2)
    for i in range(8):
        a = math.radians(i * 45)
        p.drawLine(QPointF(12 + 6 * math.cos(a), 12 + 6 * math.sin(a)), QPointF(12 + 8.6 * math.cos(a),
                                                                               12 + 8.6 * math.sin(a)))
    p.drawEllipse(QPointF(12, 12), 6, 6)


ICONS = {"mic": _mic, "head": _head, "phone": _phone, "waves": _waves, "gear": _gear}


def icon_pm(name, size, color, off=False) -> QPixmap:
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.GlobalColor.transparent)
    pm.setDevicePixelRatio(2)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 24.0, size / 24.0)
    fn = ICONS[name]
    if name == "phone":
        fn(p, QColor(color), down=off)
    else:
        fn(p, QColor(color), off)
    p.end()
    return pm


def _round_pm(src: QPixmap, d: int) -> QPixmap:
    out = QPixmap(d, d)
    out.fill(Qt.GlobalColor.transparent)
    if src is None or src.isNull():
        return out
    src = QPixmap(src)
    src.setDevicePixelRatio(1.0)                               # аватары бывают в 2× — считать в пикселях
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    path = QPainterPath()
    path.addEllipse(0, 0, d, d)
    p.setClipPath(path)
    s = src.scaled(d, d, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
    p.drawPixmap((d - s.width()) // 2, (d - s.height()) // 2, s)
    p.end()
    return out


# ══════════════════════════ кнопки ══════════════════════════ #

class RoundBtn(QAbstractButton):
    """Круглая кнопка Discord: серая, при «выключено» — красный значок/фон; «завершить» — красная пилюля."""

    def __init__(self, icon, tip, kind="normal", size=48, parent=None):
        super().__init__(parent)
        self.icon_name, self.kind, self.d = icon, kind, size
        self.off = False
        self.hover = False
        self.setToolTip(tip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(int(size * (1.45 if kind in ("hang", "accept_wide") else 1)), size)

    def set_off(self, off):
        self.off = bool(off)
        self.update()

    def enterEvent(self, e):
        self.hover = True
        self.update()

    def leaveEvent(self, e):
        self.hover = False
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self.kind == "hang":
            bg = QColor(RED).lighter(112 if self.hover else 100)
        elif self.kind == "accept":
            bg = QColor(GREEN).lighter(112 if self.hover else 100)
        elif self.off:
            bg = QColor("#3b2326") if not self.hover else QColor("#4a2a2e")
        else:
            bg = QColor(BTN_HOVER if self.hover else BTN)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        col = "#ffffff" if self.kind in ("hang", "accept") else (("#f23f42") if self.off else "#dbdee1")
        s = int(self.d * 0.48)
        pm = icon_pm(self.icon_name, s, col, off=self.off if self.kind == "normal" else self.kind == "hang")
        p.drawPixmap(int((self.width() - s) / 2), int((self.height() - s) / 2), pm)
        p.end()


# ══════════════════════════ плитка участника ══════════════════════════ #

class Tile(QWidget):
    def __init__(self, ctrl, onion, name, pix):
        super().__init__()
        self.ctrl, self.onion, self.name = ctrl, onion, name
        self.av = _round_pm(pix, 88)
        self.level = 0.0
        self.speaking = False
        self.mute = self.deaf = False
        self.ringing = False
        self.link = "udp"
        self.setFixedSize(170, 150)
        self.setToolTip(name)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W = self.width()
        bg = QRectF(2, 2, W - 4, self.height() - 4)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(PANEL))
        p.drawRoundedRect(bg, 14, 14)
        cx, cy = W / 2, 62
        t = time.time()
        if self.ringing:                                       # «звоним…» — расходящиеся кольца
            for k in range(2):
                ph = ((t * 0.9 + k * 0.5) % 1.0)
                c = QColor(GREEN)
                c.setAlpha(int(140 * (1 - ph)))
                p.setPen(QPen(c, 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(cx, cy), 44 + 18 * ph, 44 + 18 * ph)
        if self.speaking:                                      # говорит — зелёное кольцо со свечением
            g = QRadialGradient(QPointF(cx, cy), 60)
            c0 = QColor(GREEN)
            c0.setAlpha(int(70 + 90 * min(1.0, self.level * 2)))
            c1 = QColor(GREEN)
            c1.setAlpha(0)
            g.setColorAt(0.70, c0)
            g.setColorAt(1.0, c1)
            p.setBrush(g)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(cx, cy), 60, 60)
            p.setPen(QPen(QColor(GREEN), 3.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(cx, cy), 46.5, 46.5)
        p.setOpacity(0.45 if self.ringing else 1.0)
        p.drawPixmap(int(cx - 44), int(cy - 44), self.av)
        p.setOpacity(1.0)
        bx = cx + 26
        for kind, on in (("head", self.deaf), ("mic", self.mute)):
            if not on:
                continue
            p.setPen(QPen(QColor(PANEL), 3))
            p.setBrush(QColor(RED))
            p.drawEllipse(QPointF(bx, cy + 30), 12, 12)
            p.drawPixmap(int(bx - 8), int(cy + 22), icon_pm(kind, 16, "#ffffff", off=True))
            bx -= 22
        f = QFont(self.font())
        f.setPixelSize(13)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#f2f3f5"))
        nm = QFontMetrics(f).elidedText(self.name, Qt.TextElideMode.ElideRight, W - 20)
        p.drawText(QRectF(10, 114, W - 20, 18), Qt.AlignmentFlag.AlignHCenter, nm)
        sub = "звоним…" if self.ringing else ("нет прямого канала — голос не дойдёт" if self.link == "none" else "")
        if sub:
            f.setPixelSize(10)
            f.setBold(False)
            p.setFont(f)
            p.setPen(QColor("#f0b232") if self.link == "none" and not self.ringing else QColor("#b5bac1"))
            p.drawText(QRectF(6, 131, W - 12, 14), Qt.AlignmentFlag.AlignHCenter,
                       QFontMetrics(f).elidedText(sub, Qt.TextElideMode.ElideRight, W - 12))
        p.end()


# ══════════════════════════ панель звонка ══════════════════════════ #

class CallPanel(QFrame):
    """Над перепиской: плитки участников, таймер, кнопки управления."""

    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        self.setObjectName("EchoesCallPanel")
        self.setStyleSheet(f"QFrame#EchoesCallPanel{{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                           f"stop:0 #0b0c0e,stop:1 {BG});border-radius:14px;}}"
                           "QLabel{background:transparent;}")
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 12, 16, 14)
        v.setSpacing(8)
        top = QHBoxLayout()
        self.title = QLabel("")
        self.title.setStyleSheet("color:#f2f3f5;font-size:14px;font-weight:800;")
        top.addWidget(self.title, 1)
        self.timer_lbl = QLabel("")
        self.timer_lbl.setStyleSheet(f"color:{GREEN};font-size:13px;font-weight:800;")
        top.addWidget(self.timer_lbl)
        v.addLayout(top)
        self.tiles_w = QWidget()
        self.tiles = QHBoxLayout(self.tiles_w)
        self.tiles.setContentsMargins(0, 4, 0, 4)
        self.tiles.setSpacing(12)
        v.addWidget(self.tiles_w, 0, Qt.AlignmentFlag.AlignHCenter)
        bar = QHBoxLayout()
        bar.setSpacing(12)
        bar.addStretch(1)
        self.b_mic = RoundBtn("mic", "Выключить микрофон")
        self.b_mic.clicked.connect(ctrl.toggle_mute)
        self.b_head = RoundBtn("head", "Выключить звук")
        self.b_head.clicked.connect(ctrl.toggle_deaf)
        self.b_ns = RoundBtn("waves", "Шумодав")
        self.b_ns.clicked.connect(ctrl.ns_menu)
        self.b_set = RoundBtn("gear", "Настройки голоса")
        self.b_set.clicked.connect(ctrl.open_settings)
        self.b_hang = RoundBtn("phone", "Завершить звонок", "hang")
        self.b_hang.clicked.connect(ctrl.hang_up)
        for b in (self.b_mic, self.b_head, self.b_ns, self.b_set, self.b_hang):
            bar.addWidget(b)
        bar.addStretch(1)
        v.addLayout(bar)
        self._key = None
        self.tile_map = {}

    def set_view(self, view):
        c = self.ctrl
        nd = c.page.node
        self.title.setText(("Созвон · " if view["kind"] == "group" else "Голосовой звонок · ") + view["title"])
        key = tuple(sorted(p["onion"] for p in view["parts"])) + tuple(view["ringing"])
        if key != self._key:
            self._key = key
            while self.tiles.count():
                it = self.tiles.takeAt(0)
                if it.widget():
                    it.widget().deleteLater()
            self.tile_map = {}
            people = [p["onion"] for p in view["parts"]] + [o for o in view["ringing"]
                                                           if o not in {p["onion"] for p in view["parts"]}]
            for o in people:
                me = o == nd.me.onion
                name = "Вы" if me else c.page._name(o)
                t = Tile(c, o, name, c.page._av(o, name, 88))
                self.tiles.addWidget(t)
                self.tile_map[o] = t
        for pinfo in view["parts"]:
            t = self.tile_map.get(pinfo["onion"])
            if t is not None:
                t.mute, t.deaf, t.link, t.ringing = pinfo["mute"], pinfo["deaf"], pinfo["link"], False
        for o in view["ringing"]:
            t = self.tile_map.get(o)
            if t is not None:
                t.ringing = True
        self.b_mic.set_off(c.muted)
        self.b_head.set_off(c.deaf)
        self.b_ns.set_off(c.engine_settings().get("voice_ns", 1) == 0)

    def tick(self, view):
        if not view:
            return
        el = int(time.time() - view["t0"])
        self.timer_lbl.setText(f"{el // 60:02d}:{el % 60:02d}" if not view["ringing"] or len(view["parts"]) > 1
                               else "вызов…")
        v = self.ctrl.engine
        nd = self.ctrl.page.node
        talk = set(v.speaking()) if v else set()
        for o, t in self.tile_map.items():
            if o == nd.me.onion:
                t.speaking = bool(v and v.talking and not self.ctrl.muted)
                t.level = v.level if v else 0
            else:
                t.speaking = o in talk and not self.ctrl.deaf
                s = v.srcs.get(o) if v else None
                t.level = s.level if s else 0
            t.update()


# ══════════════════════════ входящий звонок ══════════════════════════ #

class IncomingCall(QWidget):
    """Поверх окна: затемнение и карточка — аватар с пульсирующими кольцами, «Принять» / «Отклонить»."""

    def __init__(self, ctrl, ring):
        win = ctrl.page.win
        super().__init__(win)
        self.ctrl, self.ring = ctrl, ring
        self.setGeometry(win.rect())
        win.installEventFilter(self)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        name = ring.get("name") or "Звонок"
        title = ring.get("title") if ring.get("kind") == "group" else name
        self.av = _round_pm(ctrl.page._av(ring["from"], name, 120), 120)
        self.title = title
        self.sub = (f"{name} зовёт в созвон" if ring.get("kind") == "group" else "Входящий звонок")
        self.t0 = time.time()
        self.acc = RoundBtn("phone", "Принять (Enter)", "accept", 60, self)
        self.dec = RoundBtn("phone", "Отклонить (Esc)", "hang", 60, self)
        self.dec.setFixedSize(60, 60)
        self.acc.clicked.connect(lambda: ctrl.accept(ring["cid"]))
        self.dec.clicked.connect(lambda: ctrl.decline(ring["cid"]))
        self.anim = QTimer(self)
        self.anim.setInterval(16)
        self.anim.timeout.connect(self.update)
        self.anim.start()
        self._place()
        self.show()
        self.raise_()
        self.setFocus()

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.Resize:
            self.setGeometry(self.parentWidget().rect())
            self._place()
        return False

    def _place(self):
        cx, cy = self.width() / 2, self.height() / 2
        self.dec.move(int(cx - 30 - 60), int(cy + 120))
        self.acc.move(int(cx + 30), int(cy + 120))

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.ctrl.accept(self.ring["cid"])
        elif e.key() == Qt.Key.Key_Escape:
            self.ctrl.decline(self.ring["cid"])

    def close_card(self):
        self.anim.stop()
        try:
            self.parentWidget().removeEventFilter(self)
        except RuntimeError:
            pass
        self.hide()
        self.deleteLater()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        k = min(1.0, (time.time() - self.t0) / 0.22)
        e = 1 - (1 - k) ** 3
        p.fillRect(self.rect(), QColor(0, 0, 0, int(170 * e)))
        W, H = self.width(), self.height()
        cw, ch = 320, 380
        card = QRectF((W - cw) / 2, (H - ch) / 2 + 24 * (1 - e), cw, ch)
        p.setOpacity(e)
        p.setPen(QPen(QColor(255, 255, 255, 20), 1))
        p.setBrush(QColor(BG))
        p.drawRoundedRect(card, 18, 18)
        cx, cy = card.center().x(), card.top() + 120
        t = time.time()
        for i in range(3):                                     # пульсирующие кольца вокруг аватара
            ph = (t * 0.8 + i / 3) % 1.0
            c = QColor(GREEN)
            c.setAlpha(int(120 * (1 - ph)))
            p.setPen(QPen(c, 2.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(cx, cy), 60 + 34 * ph, 60 + 34 * ph)
        p.drawPixmap(int(cx - 60), int(cy - 60), self.av)
        f = QFont(self.font())
        f.setPixelSize(20)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#f2f3f5"))
        p.drawText(QRectF(card.left() + 10, cy + 74, cw - 20, 28), Qt.AlignmentFlag.AlignHCenter,
                   QFontMetrics(f).elidedText(self.title, Qt.TextElideMode.ElideRight, cw - 20))
        f.setPixelSize(13)
        f.setBold(False)
        p.setFont(f)
        p.setPen(QColor("#b5bac1"))
        p.drawText(QRectF(card.left() + 10, cy + 104, cw - 20, 20), Qt.AlignmentFlag.AlignHCenter, self.sub)
        p.end()
        self.acc.move(int(card.center().x() + 22), int(card.bottom() - 84))
        self.dec.move(int(card.center().x() - 82), int(card.bottom() - 84))


# ══════════════════════════ мини-панель «Голосовая связь» ══════════════════════════ #

class MiniBar(QFrame):
    """Внизу слева (как в Discord): «Голосовая связь подключена», название, микрофон, звук, завершить."""

    def __init__(self, ctrl):
        win = ctrl.page.win
        super().__init__(win)
        self.ctrl = ctrl
        self.setObjectName("EchoesVoiceBar")
        self.setStyleSheet("QFrame#EchoesVoiceBar{background:#232428;border:1px solid rgba(255,255,255,0.06);"
                           "border-radius:12px;} QLabel{background:transparent;}")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(6)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.state = QLabel("Голосовая связь подключена")
        self.state.setStyleSheet(f"color:{GREEN};font-size:11px;font-weight:800;")
        self.state.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)   # за надпись — тоже тянется
        self.title = QLabel("")
        self.title.setStyleSheet("color:#b5bac1;font-size:11px;")
        self.title.setCursor(Qt.CursorShape.PointingHandCursor)
        self.title.mousePressEvent = lambda e: ctrl.open_call_chat()
        col.addWidget(self.state)
        col.addWidget(self.title)
        outer.addLayout(col)
        h = QHBoxLayout()
        h.setSpacing(6)
        outer.addLayout(h)
        self.b_mic = RoundBtn("mic", "Микрофон", size=32)
        self.b_mic.clicked.connect(ctrl.toggle_mute)
        self.b_head = RoundBtn("head", "Звук", size=32)
        self.b_head.clicked.connect(ctrl.toggle_deaf)
        self.b_hang = RoundBtn("phone", "Завершить", "hang", 32)
        self.b_hang.setFixedSize(40, 32)
        self.b_hang.clicked.connect(ctrl.hang_up)
        for b in (self.b_mic, self.b_head):
            h.addWidget(b)
        h.addStretch(1)
        h.addWidget(self.b_hang)
        self.setFixedSize(188, 88)
        self.setToolTip("Перетащите плашку куда удобно · двойной клик — вернуть на место")
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._drag = None
        win.installEventFilter(self)
        self.hide()

    # ── перетаскивание: место запоминается (доля окна — переживает смену размера) ── #

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self._move_to(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            self._drag = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            win = self.parentWidget()
            fx = self.x() / max(1, win.width() - self.width())
            fy = self.y() / max(1, win.height() - self.height())
            win.settings["voice_bar_pos"] = [round(fx, 4), round(fy, 4)]
            try:
                win.save_settings()
            except Exception:                                  # noqa: BLE001
                pass

    def mouseDoubleClickEvent(self, e):
        win = self.parentWidget()
        win.settings.pop("voice_bar_pos", None)
        self.place()

    def _move_to(self, pt):
        win = self.parentWidget()
        self.move(max(0, min(pt.x(), win.width() - self.width())), max(30, min(pt.y(), win.height() - self.height())))

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.Resize:
            self.place()
        return False

    def place(self):
        win = self.parentWidget()
        pos = getattr(win, "settings", {}).get("voice_bar_pos")
        if isinstance(pos, list) and len(pos) == 2:            # куда перетащили
            from PyQt6.QtCore import QPoint
            self._move_to(QPoint(int(pos[0] * (win.width() - self.width())), int(pos[1] * (win.height() - self.height()))))
        else:
            self.move(12, max(60, win.height() - self.height() - 64))   # над своим профилем внизу слева
        self.raise_()

    def set_view(self, view, muted, deaf):
        n = len(view["parts"])
        self.state.setText("Голосовая связь подключена" if n > 1 else "Вызов…")
        self.state.setStyleSheet(f"color:{GREEN if n > 1 else '#f0b232'};font-size:11px;font-weight:800;")
        t = view["title"] + (f" · {n} в звонке" if view["kind"] == "group" else "")
        self.title.setText(QFontMetrics(self.title.font()).elidedText(t, Qt.TextElideMode.ElideRight, 165))
        self.title.setToolTip("Открыть переписку звонка")
        self.b_mic.set_off(muted)
        self.b_head.set_off(deaf)


# ══════════════════════════ контроллер ══════════════════════════ #

class CallController(QObject):
    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.view = None
        self.muted = False
        self.deaf = False
        self.panel = None
        self.bar = None
        self.incoming = None
        self.ring_timer = QTimer(self)
        self.ring_timer.setInterval(2300)
        self.ring_timer.timeout.connect(self._ring_sound)
        self.tick_t = QTimer(self)
        self.tick_t.setInterval(70)
        self.tick_t.timeout.connect(self._tick)

    # ── голос ── #

    @property
    def engine(self):
        return getattr(self.page, "_voice", None)

    def engine_settings(self) -> dict:
        nd = self.page.node
        return nd.settings if nd is not None else {}

    def ensure_engine(self):
        if self.page._voice is None:
            from net_voice import VoiceEngine
            self.page._voice = VoiceEngine(self.engine_settings())
        return self.page._voice

    # ── события сети ── #

    def on_ring(self, ring):
        nd = self.page.node
        if ring is None:                                       # звонок закончился / отвечен / пропущен
            if self.incoming is not None and self.incoming.ring["cid"] not in nd.rings:
                self.incoming.close_card()
                self.incoming = None
            if not nd.rings and not (self.view and self.view["ringing"]):
                self.ring_timer.stop()
            return
        if self.incoming is not None:
            return
        self.incoming = IncomingCall(self, ring)
        self._ring_sound()
        self.ring_timer.start()
        try:
            from PyQt6.QtWidgets import QApplication
            QApplication.alert(self.page.win, 0)               # мигать в панели задач, пока не ответят
        except Exception:                                      # noqa: BLE001
            pass
        name = ring.get("name") or "Звонок"
        self.page.alert("message", ring["title"] if ring.get("kind") == "group" else name,
                        "зовёт в созвон" if ring.get("kind") == "group" else "звонит вам", onion=ring["from"],
                        force=True)

    def on_call(self, view):
        nd = self.page.node
        was = self.view
        self.view = view
        if view is None:
            self.ring_timer.stop() if not nd.rings else None
            self.tick_t.stop()
            v = self.engine
            if v is not None and was:
                for p in was["parts"]:
                    v.drop(p["onion"])
                if not (nd.room_host is not None or nd.room_guest):
                    v.mic_off()
            self.muted = self.deaf = False
            if v is not None:
                v.muted = v.deaf = False
            if self.panel is not None:
                self.panel.hide()
            if self.bar is not None:
                self.bar.hide()
            self.page._call_ui_changed()
            return
        v = self.ensure_engine()
        v.sink = nd.voice_out
        v.want = {view.get("codec") or "adpcm"}
        if not v.mic_active:
            try:
                v.mic_on()
            except Exception as e:                             # noqa: BLE001
                self.page.toast(f"Микрофон не включился: {e}")
        if view["ringing"] and len(view["parts"]) <= 1:
            if not self.ring_timer.isActive():
                self._ring_sound()
                self.ring_timer.start()
        elif not nd.rings:
            self.ring_timer.stop()
        if self.panel is None:
            self.panel = CallPanel(self)
        self.panel.set_view(view)
        if self.bar is None:
            self.bar = MiniBar(self)
        self.bar.set_view(view, self.muted, self.deaf)
        self.tick_t.start()
        self.page._call_ui_changed()

    def on_vc(self, data):
        if self.deaf:
            return
        src, codec, seq, frames = data
        try:
            self.ensure_engine().play(src, codec, seq, frames)
        except Exception as e:                                 # noqa: BLE001
            print("[call] play:", e)

    # ── действия ── #

    def start(self, target):
        nd = self.page.node
        if nd is None or not self.page.running():
            self.page.toast("Сначала включите сеть")
            return
        nd.call(nd.call_start, target)

    def accept(self, cid):
        nd = self.page.node
        if self.incoming is not None:
            self.incoming.close_card()
            self.incoming = None
        self.ring_timer.stop()
        ring = dict(nd.rings.get(cid) or {})
        nd.call(nd.call_accept, cid)
        peer = ("g:" + ring["gid"]) if ring.get("kind") == "group" and ring.get("gid") else ring.get("from")
        if peer:
            QTimer.singleShot(150, lambda: self.page.open_chat(peer))

    def decline(self, cid):
        nd = self.page.node
        if self.incoming is not None:
            self.incoming.close_card()
            self.incoming = None
        self.ring_timer.stop()
        nd.call(nd.call_decline, cid)

    def hang_up(self):
        nd = self.page.node
        nd.call(nd.call_leave)

    def toggle_mute(self):
        self.muted = not self.muted
        v = self.engine
        if v is not None:
            v.muted = self.muted
        nd = self.page.node
        nd.call(nd.call_set, self.muted, None)
        self._refresh_buttons()

    def toggle_deaf(self):
        self.deaf = not self.deaf
        v = self.engine
        if v is not None:
            v.deaf = self.deaf
            if self.deaf:                                      # как в Discord: без звука — и без микрофона
                self.muted = True
                v.muted = True
        nd = self.page.node
        nd.call(nd.call_set, self.muted, self.deaf)
        self._refresh_buttons()

    def _refresh_buttons(self):
        if self.panel is not None:
            self.panel.b_mic.set_off(self.muted)
            self.panel.b_head.set_off(self.deaf)
        if self.bar is not None and self.view:
            self.bar.set_view(self.view, self.muted, self.deaf)

    def ns_menu(self):
        m = QMenu(self.page)
        cur = int(self.engine_settings().get("voice_ns", 1))
        for lvl, text in ((0, "Шумодав выключен"), (1, "Шумодав: обычный"), (2, "Шумодав: сильный")):
            a = m.addAction(("● " if lvl == cur else "   ") + text)
            a.triggered.connect(lambda _=False, lvl=lvl: self.set_ns(lvl))
        b = self.panel.b_ns if self.panel is not None else None
        m.exec(b.mapToGlobal(b.rect().topLeft()) if b is not None else self.page.mapToGlobal(self.page.rect().center()))

    def set_ns(self, lvl):
        v = self.ensure_engine()
        v.set_ns(lvl)
        nd = self.page.node
        nd.save_settings()
        if self.panel is not None:
            self.panel.b_ns.set_off(lvl == 0)
        self.page.toast({0: "Шумодав выключен", 1: "Шумодав: обычный", 2: "Шумодав: сильный"}[lvl])

    def open_settings(self):
        self.page.shell.show_page("net")
        self.page.show_tab("net")
        w = getattr(self.page, "_voice_anchor", None)
        if w is not None:
            QTimer.singleShot(150, lambda: self.page.net_page.ensureWidgetVisible(w, 0, 40))

    def open_call_chat(self):
        v = self.view
        if not v:
            return
        self.page.open_chat(("g:" + v["gid"]) if v["kind"] == "group" else v["peer"])

    def call_chat(self) -> str:
        v = self.view
        if not v:
            return ""
        return ("g:" + v["gid"]) if v["kind"] == "group" else v["peer"]

    # ── время ── #

    def _ring_sound(self):
        try:
            self.page.notifier.sound(force=True)
        except Exception:                                      # noqa: BLE001
            pass

    def _tick(self):
        if self.view and self.panel is not None and self.panel.isVisible():
            self.panel.tick(self.view)
        if self.view and self.bar is not None:
            in_chat = self.page.isVisible() and self.page._tab == "chats" and \
                self.page._chat_peer == self.call_chat() and self.page.shell._cur_page == "net"
            if in_chat == self.bar.isVisible():
                self.bar.setVisible(not in_chat)
                if not in_chat:
                    self.bar.place()
