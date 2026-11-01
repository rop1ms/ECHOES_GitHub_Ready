# overlay_demo.py
"""
Игровой оверлей ECHOES (как в Discord) — виджеты, их вид (жидкое стекло ECHOES) и настройки.

  * OverlayCanvas рисует виджеты: «Сейчас играет» (обложка, прогресс, ⏮ ⏯ ⏭ — нажимаются), «Голосовой чат»
    (кто говорит — подсвечен), «Уведомления» (сообщения друзей), «Текст песни». Он же — и в предпросмотре
    («игровой экран»), и в настоящем окне поверх игр (overlay_live);
  * открытый оверлей — затемнение, виджеты таскаются, 📌 — оставить поверх игры, когда оверлей закрыт;
  * бинды: открыть/закрыть, микрофон, пауза, следующий трек, закрепить всё — записываются нажатием.

Включается в Настройки → Оформление → «Игровой оверлей» (по умолчанию выключен), там же бинды.
Настройки — settings["overlay"].
"""
from __future__ import annotations

import copy
import math
import random
import time

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (QColor, QFont, QFontMetrics, QKeySequence, QLinearGradient, QPainter, QPainterPath, QPen,
                         QRadialGradient)
from PyQt6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSlider,
                             QVBoxLayout, QWidget)

ACTIONS = (("toggle", "Открыть / закрыть оверлей", "Shift+`"), ("mute", "Микрофон вкл/выкл", "Ctrl+Shift+M"),
           ("play", "Пауза / играть", "Ctrl+Shift+Space"), ("next", "Следующий трек", "Ctrl+Shift+Right"),
           ("pin", "Закрепить всё / открепить", ""))
WIDGETS = (("now", "Сейчас играет"), ("voice", "Голосовой чат"), ("notif", "Уведомления"), ("lyric", "Текст песни"),
           ("game", "FPS и время в игре"))
DEFAULT = {"enabled": False, "binds": {k: d for k, _n, d in ACTIONS}, "opacity": 92, "scale": 100, "corner": "tr",
           "only_games": True,
           "show": {"msg": True, "call": True, "friends_now": True},
           "w": {"now": {"x": 0.015, "y": 0.04, "pin": True}, "voice": {"x": 0.015, "y": 0.2, "pin": True},
                 "notif": {"x": 0.78, "y": 0.04, "pin": True}, "lyric": {"x": 0.33, "y": 0.86, "pin": False},
                 "game": {"x": 0.86, "y": 0.88, "pin": False}}}
WSIZE = {"now": (320, 100), "voice": (250, 196), "notif": (280, 70), "lyric": (520, 46), "game": (200, 52)}


def load(settings) -> dict:
    d = copy.deepcopy(DEFAULT)
    raw = (settings or {}).get("overlay")
    if isinstance(raw, dict):
        for k, v in raw.items():
            if k in ("binds", "show", "w") and isinstance(v, dict):
                for kk, vv in v.items():
                    if kk in d[k]:
                        d[k][kk] = dict(d[k][kk], **vv) if isinstance(d[k][kk], dict) and isinstance(vv, dict) else vv
            elif k in d:
                d[k] = v
    return d


def save(win, cfg):
    win.settings["overlay"] = cfg
    try:
        win.save_settings()
    except Exception:                                          # noqa: BLE001
        pass
    try:
        import overlay_live
        overlay_live.apply(win)
    except Exception as e:                                     # noqa: BLE001
        print("[overlay]", e)


def seq_of(ev) -> str:
    """Нажатие → «Ctrl+Shift+M» (одни модификаторы — пусто)."""
    k = ev.key()
    if k in (Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta, Qt.Key.Key_unknown):
        return ""
    mods = ev.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier |
                             Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier)
    if k == Qt.Key.Key_QuoteLeft or k == Qt.Key.Key_AsciiTilde:   # Shift+` даёт «~» — храним как `
        k = Qt.Key.Key_QuoteLeft
    return QKeySequence(mods.value | k).toString(QKeySequence.SequenceFormat.PortableText)


def _accent() -> QColor:
    try:
        import ya_decor
        return ya_decor.accent()
    except Exception:                                          # noqa: BLE001
        return QColor("#ffdb1a")


def glass(p: QPainter, r: QRectF, rad: float, acc: QColor | None = None, lift=1.0):
    """Жидкое стекло ECHOES: тень, тёмное стекло с тоном акцента, блик, светлый кант сверху."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    for i, a in ((10, 14), (6, 22), (3, 30)):                  # мягкая тень
        sh = QPainterPath()
        sh.addRoundedRect(r.adjusted(-i * 0.6, -i * 0.3 + 3, i * 0.6, i * 0.6 + 3), rad + i * 0.5, rad + i * 0.5)
        p.fillPath(sh, QColor(0, 0, 0, int(a * lift)))
    path = QPainterPath()
    path.addRoundedRect(r, rad, rad)
    g = QLinearGradient(r.topLeft(), r.bottomLeft())
    g.setColorAt(0, QColor(52, 54, 66, 178))
    g.setColorAt(0.5, QColor(24, 25, 32, 190))
    g.setColorAt(1, QColor(12, 12, 16, 205))
    p.fillPath(path, g)
    p.setClipPath(path)
    if acc is not None:                                        # тон акцента (обложки) из угла
        tg = QRadialGradient(QPointF(r.left() + r.width() * 0.15, r.top()), max(r.width(), r.height()) * 0.9)
        c = QColor(acc)
        c.setAlpha(46)
        tg.setColorAt(0, c)
        c.setAlpha(0)
        tg.setColorAt(1, c)
        p.fillRect(r, tg)
    sheen = QLinearGradient(r.topLeft(), r.bottomRight())     # косой блик
    for pos, a in ((0, 0), (0.06, 26), (0.13, 6), (0.2, 0), (0.78, 0), (0.86, 9), (0.92, 0), (1, 0)):
        sheen.setColorAt(pos, QColor(255, 255, 255, a))
    p.fillRect(r, sheen)
    p.setClipping(False)
    rim = QLinearGradient(r.topLeft(), r.bottomLeft())
    rim.setColorAt(0, QColor(255, 255, 255, 120))
    rim.setColorAt(0.25, QColor(255, 255, 255, 30))
    rim.setColorAt(1, QColor(255, 255, 255, 14))
    p.setPen(QPen(rim, 1.2))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)
    p.setPen(QPen(QColor(255, 255, 255, 70), 1))
    p.drawLine(QPointF(r.left() + rad, r.top() + 1.6), QPointF(r.right() - rad, r.top() + 1.6))
    p.restore()


def glass_button(p: QPainter, c: QPointF, R: float, icon: str, acc: QColor, primary=False, hover=False):
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if primary:
        g = QLinearGradient(c.x(), c.y() - R, c.x(), c.y() + R)
        g.setColorAt(0, acc.lighter(125))
        g.setColorAt(1, acc.darker(115))
        p.setBrush(g)
    else:
        p.setBrush(QColor(255, 255, 255, 40 if hover else 22))
    p.setPen(QPen(QColor(255, 255, 255, 90 if hover else 50), 1))
    p.drawEllipse(c, R, R)
    lum = 0.2126 * acc.redF() + 0.7152 * acc.greenF() + 0.0722 * acc.blueF()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(0, 0, 0) if primary and lum > 0.55 else QColor(255, 255, 255))
    s = R * 0.45
    path = QPainterPath()
    if icon == "play":
        path.moveTo(c.x() - s * 0.6, c.y() - s); path.lineTo(c.x() + s, c.y()); path.lineTo(c.x() - s * 0.6, c.y() + s)
    elif icon == "pause":
        path.addRoundedRect(QRectF(c.x() - s * 0.8, c.y() - s, s * 0.6, s * 2), 1, 1)
        path.addRoundedRect(QRectF(c.x() + s * 0.2, c.y() - s, s * 0.6, s * 2), 1, 1)
    elif icon == "prev":
        path.addRect(QRectF(c.x() - s, c.y() - s * 0.8, s * 0.25, s * 1.6))
        path.moveTo(c.x() + s * 0.8, c.y() - s * 0.8); path.lineTo(c.x() - s * 0.6, c.y()); path.lineTo(c.x() + s * 0.8, c.y() + s * 0.8)
    elif icon == "next":
        path.addRect(QRectF(c.x() + s * 0.75, c.y() - s * 0.8, s * 0.25, s * 1.6))
        path.moveTo(c.x() - s * 0.8, c.y() - s * 0.8); path.lineTo(c.x() + s * 0.6, c.y()); path.lineTo(c.x() - s * 0.8, c.y() + s * 0.8)
    path.closeSubpath()
    p.drawPath(path)
    p.restore()


class KeyRecorder(QPushButton):
    """Кнопка бинда: клик — «нажмите сочетание…», Esc — отмена, Backspace — убрать."""
    changed = pyqtSignal(str)

    def __init__(self, seq=""):
        super().__init__()
        self.seq = seq
        self.rec = False
        self.setMinimumWidth(170)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.clicked.connect(self._start)
        self._show()

    def _show(self):
        self.setText("Нажмите сочетание…" if self.rec else (self.seq or "не задано"))
        self.setStyleSheet("QPushButton{background:%s;color:%s;font-family:Consolas,monospace;}" %
                           (("#ffdb1a", "#000") if self.rec else ("rgba(255,255,255,0.08)", "#f0f0f0")))

    def _start(self):
        self.rec = True
        self.setFocus()
        self._show()

    def set_seq(self, s):
        self.seq = s
        self._show()

    def keyPressEvent(self, e):
        if not self.rec:
            return super().keyPressEvent(e)
        if e.key() == Qt.Key.Key_Escape:
            self.rec = False
        elif e.key() == Qt.Key.Key_Backspace and not e.modifiers():
            self.rec, self.seq = False, ""
            self.changed.emit("")
        else:
            s = seq_of(e)
            if not s:
                return
            self.rec, self.seq = False, s
            self.changed.emit(s)
        self._show()

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        if self.rec:
            self.rec = False
            self._show()


class OverlayCanvas(QWidget):
    """Виджеты оверлея. live=False — предпросмотр на «игровом экране» с придуманными данными."""
    action = pyqtSignal(str)                                   # нажата кнопка виджета: prev / play / next / mute
    reply = pyqtSignal(str, str)                               # ответ на сообщение: (onion, текст)
    changed = pyqtSignal()                                     # виджет перетащили / закрепили

    def __init__(self, cfg, data, live=False, parent=None):
        super().__init__(parent)
        self.cfg, self.data, self.live = cfg, data, live
        self.open = False
        self._k = 0.0
        self._drag = None
        self._hits = []                                        # [(QRectF, действие)] кнопок в виджетах
        self._hover = None
        self.toasts = []                                       # действия по биндам: (текст, время)
        self.notes = []                                        # уведомления: (заголовок, текст, время)
        self._t0 = time.monotonic()
        self.setMouseTracking(True)
        if not live:
            self.setMinimumSize(640, 360)
            self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            self._next_msg = time.monotonic() + 2.5
            self.timer = QTimer(self)
            self.timer.setInterval(33)
            self.timer.timeout.connect(self._tick)
            self.timer.start()

    def hasHeightForWidth(self):
        return not self.live

    def heightForWidth(self, w):
        return int(w * 9 / 16)

    def scale(self) -> float:
        base = self.height() / 1080 if self.live else self.width() / 1280
        return base * self.cfg["scale"] / 100 * (1.25 if self.live else 1.0)

    def set_open(self, on):
        self.open = on
        self.update()

    def toast(self, text):
        self.toasts.append((text, time.monotonic()))
        del self.toasts[:-3]
        self.update()

    def note(self, title, text, onion=""):
        self.notes.append((title, text, time.monotonic(), onion))
        del self.notes[:-3]
        self.update()

    def animating(self) -> bool:
        return abs(self._k - (1.0 if self.open else 0.0)) > 0.01

    def _tick(self):
        """Шаг анимации; в предпросмотре — ещё и придуманные сообщения друзей."""
        self._k += ((1.0 if self.open else 0.0) - self._k) * 0.3
        now = time.monotonic()
        if not self.live and now > self._next_msg and self.cfg["show"].get("msg"):
            who, txt = random.choice(self.data["msgs"])
            self.note(who, txt, "")
            self._next_msg = now + random.uniform(6, 11)
        self.toasts = [x for x in self.toasts if now - x[1] < 2.6]
        self.notes = [x for x in self.notes if now - x[2] < (60 if self.open else 8)]
        self.update()

    def _rect(self, key) -> QRectF:
        w = self.cfg["w"][key]
        sw, sh = WSIZE[key]
        k = self.scale()
        return QRectF(w["x"] * self.width(), w["y"] * self.height(), sw * k, sh * k)

    def _pin_rect(self, key) -> QRectF:
        r = self._rect(key)
        return QRectF(r.right() - 24, r.top() - 24, 22, 22)

    def visible_keys(self):
        out = []
        for k, _n in WIDGETS:
            if not (self.open or self.cfg["w"][k].get("pin")):
                continue
            if k == "notif" and not self.open and not self.notes:
                continue                                       # уведомлений нет — и виджета нет
            if k == "voice" and not self.open and self.live and not self.data.get("voice"):
                continue                                       # не в голосовом — не показывать
            if k == "game" and not self.open and self.live and not self.data.get("game"):
                continue                                       # не в игре — не показывать
            out.append(k)
        return out

    def widget_region(self) -> QRectF:
        r = QRectF()
        for k, _n in WIDGETS:
            r = r.united(self._rect(k).adjusted(-30, -30, 30, 30))
        return r

    # ── мышь (только в открытом оверлее; закрытый пропускает клики в игру) ── #

    def mousePressEvent(self, e):
        if not self.live:
            self.setFocus()
        if not self.open:
            return
        pos = e.position()
        for r, act in self._hits:
            if r.contains(pos):
                if act.startswith("reply:"):
                    self.start_reply(int(act[6:]))
                else:
                    self.action.emit(act)
                return
        for k in reversed(self.visible_keys()):
            if self._pin_rect(k).contains(pos):
                self.cfg["w"][k]["pin"] = not self.cfg["w"][k].get("pin")
                self.changed.emit()
                self.update()
                return
            if self._rect(k).contains(pos):
                self._drag = (k, pos - self._rect(k).topLeft())
                return

    def mouseMoveEvent(self, e):
        pos = e.position()
        hv = next((act for r, act in self._hits if r.contains(pos)), None)
        if hv != self._hover:
            self._hover = hv
            self.update()
        if self._drag is not None:
            k, off = self._drag
            r = self._rect(k)
            p = pos - off
            self.cfg["w"][k]["x"] = round(max(0.0, min(1 - r.width() / self.width(), p.x() / self.width())), 4)
            self.cfg["w"][k]["y"] = round(max(0.0, min(1 - r.height() / self.height(), p.y() / self.height())), 4)
            self.update()

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            self._drag = None
            self.changed.emit()

    # ── рисование ── #

    def _game(self, p, W, H, t):
        if self.live:
            return
        g = QLinearGradient(0, 0, 0, H)
        g.setColorAt(0, QColor("#2a3f6b"))
        g.setColorAt(0.55, QColor("#c86b4a"))
        g.setColorAt(0.56, QColor("#3a2a2e"))
        g.setColorAt(1, QColor("#151018"))
        p.fillRect(0, 0, W, H, g)
        p.setPen(Qt.PenStyle.NoPen)
        for layer, (col, amp, base) in enumerate((("#4a3550", 0.08, 0.5), ("#2e2236", 0.12, 0.58),
                                                  ("#1c1522", 0.06, 0.7))):
            path = QPainterPath(QPointF(0, H))
            for i in range(0, 41):
                path.lineTo(W * i / 40, H * (base - amp * abs(math.sin(i * 0.7 + layer * 2.1))))
            path.lineTo(W, H)
            p.fillPath(path, QColor(col))
        sun = QRadialGradient(QPointF(W * 0.66, H * 0.42), H * 0.18)
        sun.setColorAt(0, QColor(255, 220, 160, 220))
        sun.setColorAt(1, QColor(255, 220, 160, 0))
        p.fillRect(0, 0, W, H, sun)
        p.setPen(QPen(QColor(255, 255, 255, 200), 2))
        c = QPointF(W / 2, H / 2)
        for dx, dy in ((-14, 0), (6, 0), (0, -14), (0, 6)):
            p.drawLine(QPointF(c.x() + dx, c.y() + dy), QPointF(c.x() + dx + (8 if dy == 0 else 0),
                                                                c.y() + dy + (8 if dx == 0 else 0)))

    def _font(self, px, bold=False):
        f = QFont()
        f.setPixelSize(max(7, int(px)))
        f.setWeight(QFont.Weight.Black if bold else QFont.Weight.Medium)
        return f

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        W, H = self.width(), self.height()
        t = time.monotonic() - self._t0
        acc = _accent()
        self._hits = []
        self._game(p, W, H, t)
        if self._k > 0.01:                                     # открыт — затемнение и стеклянная полоса сверху
            p.fillRect(0, 0, W, H, QColor(0, 0, 0, int(130 * self._k)))
            p.setOpacity(self._k)
            k = self.scale()
            bar = QRectF(W / 2 - 330 * k, 14 * k, 660 * k, 44 * k)
            glass(p, bar, 22 * k, acc)
            p.setPen(acc)
            p.setFont(self._font(15 * k, True))
            p.drawText(bar.adjusted(20 * k, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, "\U000f100e ECHOES")
            p.setPen(QColor(255, 255, 255, 190))
            p.setFont(self._font(12 * k))
            b = self.cfg["binds"].get("toggle") or "бинд"
            p.drawText(bar.adjusted(0, 0, -20 * k, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                       f"тяните виджеты  ·  \U000f1008 — поверх игры  ·  {b} или Esc — закрыть")
            p.setOpacity(1.0)
        k = self.scale()
        op = self.cfg["opacity"] / 100
        for key in self.visible_keys():
            r = self._rect(key)
            p.setOpacity(1.0 if self.open else op)
            getattr(self, "_w_" + key)(p, r, k, t, acc)
            p.setOpacity(1.0)
            if self.open:
                pr = self._pin_rect(key)
                pin = self.cfg["w"][key].get("pin")
                glass(p, pr, pr.width() / 2, acc if pin else None, 0.5)
                if pin:
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(acc)
                    p.drawEllipse(pr.adjusted(3, 3, -3, -3))
                p.setPen(QColor("#000") if pin else QColor("#ddd"))
                p.setFont(self._font(11))
                p.drawText(pr, Qt.AlignmentFlag.AlignCenter, "\U000f1008")
        now = time.monotonic()                                 # отклики на бинды — снизу по центру
        y = H - 90 * k
        for text, t0 in reversed(self.toasts):
            a = max(0.0, min(1.0, (2.6 - (now - t0)) / 0.4))
            p.setOpacity(a)
            f = self._font(14 * k, True)
            fw = QFontMetrics(f).horizontalAdvance(text) + 40 * k
            r = QRectF((W - fw) / 2, y, fw, 40 * k)
            glass(p, r, 20 * k, acc)
            p.setFont(f)
            p.setPen(QColor(255, 255, 255))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
            p.setOpacity(1.0)
            y -= 50 * k
        p.end()

    def _w_now(self, p, r, k, t, acc):
        glass(p, r, 18 * k, acc)
        d = self.data
        cs = r.height() - 20 * k
        cr = QRectF(r.x() + 10 * k, r.y() + 10 * k, cs, cs)
        cp = QPainterPath()
        cp.addRoundedRect(cr, 10 * k, 10 * k)
        if d.get("cover_pm") is not None:
            p.save()
            p.setClipPath(cp)
            p.drawPixmap(cr, d["cover_pm"], QRectF(d["cover_pm"].rect()))
            p.restore()
        else:
            g = QLinearGradient(cr.topLeft(), cr.bottomRight())
            g.setColorAt(0, acc)
            g.setColorAt(1, QColor("#ff4fa3"))
            p.fillPath(cp, g)
        x = cr.right() + 12 * k
        w = r.right() - x - 12 * k
        p.setPen(QColor(255, 255, 255))
        f = self._font(14 * k, True)
        p.setFont(f)
        p.drawText(QRectF(x, r.y() + 10 * k, w, 18 * k), Qt.AlignmentFlag.AlignLeft,
                   QFontMetrics(f).elidedText(d.get("title", ""), Qt.TextElideMode.ElideRight, int(w)))
        f2 = self._font(12 * k)
        p.setFont(f2)
        p.setPen(QColor(255, 255, 255, 170))
        p.drawText(QRectF(x, r.y() + 29 * k, w, 16 * k), Qt.AlignmentFlag.AlignLeft,
                   QFontMetrics(f2).elidedText(d.get("artist", ""), Qt.TextElideMode.ElideRight, int(w)))
        frac = d.get("frac", 0.0) if self.live else (0.3 + t * 0.004) % 1
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 46))
        p.drawRoundedRect(QRectF(x, r.y() + 52 * k, w, 4 * k), 2, 2)
        p.setBrush(acc)
        p.drawRoundedRect(QRectF(x, r.y() + 52 * k, max(4 * k, w * frac), 4 * k), 2, 2)
        cy = r.y() + 76 * k
        for i, (act, icon) in enumerate((("prev", "prev"), ("play", "pause" if d.get("playing") else "play"),
                                         ("next", "next"))):
            c = QPointF(x + 14 * k + i * 38 * k, cy)
            R = (14 if act == "play" else 12) * k
            glass_button(p, c, R, icon, acc, primary=act == "play", hover=self._hover == act)
            self._hits.append((QRectF(c.x() - R, c.y() - R, 2 * R, 2 * R), act))

    def _pill(self, p, r: QRectF, text, act, acc, on=False, k=1.0):
        """Стеклянная кнопка-«таблетка» внутри виджета (нажимается в открытом оверлее)."""
        hov = self._hover == act
        p.save()
        p.setPen(QPen(QColor(255, 255, 255, 90 if hov else 46), 1))
        p.setBrush(acc if on else QColor(255, 255, 255, 46 if hov else 24))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setPen(QColor(0, 0, 0) if on else QColor(255, 255, 255, 235))
        p.setFont(self._font(11 * k, True))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
        p.restore()
        self._hits.append((r, act))

    def _w_voice(self, p, r, k, t, acc):
        glass(p, r, 18 * k, acc)
        p.setPen(QColor(255, 255, 255, 150))
        p.setFont(self._font(11 * k, True))
        title = "ГОЛОСОВОЙ ЧАТ" + (f" · {self.data.get('call_title')}" if self.data.get("call_title") else "")
        p.drawText(QRectF(r.x() + 14 * k, r.y() + 9 * k, r.width() - 28 * k, 16 * k), Qt.AlignmentFlag.AlignLeft,
                   QFontMetrics(p.font()).elidedText(title, Qt.TextElideMode.ElideRight, int(r.width() - 60 * k)))
        voice = self.data.get("voice") or []
        if not voice:
            p.setPen(QColor(255, 255, 255, 120))
            p.setFont(self._font(12 * k))
            p.drawText(r.adjusted(14 * k, 30 * k, -10, -10), Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap,
                       "Вы не в голосовом чате")
            if self.open:
                self._pill(p, QRectF(r.x() + 14 * k, r.bottom() - 36 * k, 130 * k, 24 * k), "+ Позвать друга",
                           "vinvite", acc, k=k)
            return
        for i, v in enumerate(voice[:4]):
            name, hue = v[0], v[1]
            speaking = v[2] if len(v) > 2 else ((math.sin(t * (2.2 + i) + i * 1.7) > 0.35) and
                                                not (i == 0 and self.data.get("muted")))
            muted = (v[3] if len(v) > 3 else False) or (i == 0 and self.data.get("muted"))
            onion = v[4] if len(v) > 4 else (f"demo{i}" if i else "")
            y = r.y() + (32 + i * 30) * k
            c = QPointF(r.x() + 26 * k, y + 11 * k)
            if speaking:
                p.setPen(Qt.PenStyle.NoPen)
                gl = QRadialGradient(c, 16 * k)
                gl.setColorAt(0.6, QColor(61, 220, 132, 120))
                gl.setColorAt(1, QColor(61, 220, 132, 0))
                p.setBrush(gl)
                p.drawEllipse(c, 16 * k, 16 * k)
                p.setPen(QPen(QColor("#3ddc84"), 2.2 * k))
            else:
                p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor.fromHsvF(hue % 1.0, 0.55, 0.9))
            p.drawEllipse(c, 10 * k, 10 * k)
            p.setPen(QColor(255, 255, 255, 240 if speaking else 175))
            p.setFont(self._font(12 * k, speaking))
            nw = r.width() - (150 if self.open and onion else 60) * k
            p.drawText(QRectF(c.x() + 18 * k, y, nw, 22 * k), Qt.AlignmentFlag.AlignVCenter,
                       QFontMetrics(p.font()).elidedText(name + ("  \U000f1009" if muted else ""), Qt.TextElideMode.ElideRight,
                                                        int(nw)))
            if self.open and onion and i > 0:                  # громкость каждого: − 100 % +
                vol = int(round(100 * float((self.data.get("vols") or {}).get(onion, 1.0))))
                bx = r.right() - 100 * k
                self._pill(p, QRectF(bx, y + 2 * k, 22 * k, 18 * k), "−", f"vol-:{onion}", acc, k=k)
                p.setPen(QColor(255, 255, 255, 200))
                p.setFont(self._font(10 * k, True))
                p.drawText(QRectF(bx + 22 * k, y, 44 * k, 22 * k), Qt.AlignmentFlag.AlignCenter, f"{vol}%")
                self._pill(p, QRectF(bx + 66 * k, y + 2 * k, 22 * k, 18 * k), "+", f"vol+:{onion}", acc, k=k)
        if self.open:                                          # микрофон, звук, позвать, выйти
            y = r.bottom() - 32 * k
            x = r.x() + 12 * k
            for text, act, on, w in (("\U000f1007" if not self.data.get("muted") else "\U000f1009", "vmute", self.data.get("muted"), 34),
                                     ("\U000f100a", "vdeaf", self.data.get("deaf"), 34), ("+ Позвать", "vinvite", False, 74),
                                     ("Выйти", "vleave", False, 56)):
                self._pill(p, QRectF(x, y, w * k, 22 * k), text, act, acc, on=bool(on), k=k)
                x += (w + 6) * k

    def _w_notif(self, p, r, k, t, acc):
        notes = self.notes
        if not notes:
            glass(p, r, 16 * k, acc)
            p.setPen(QColor(255, 255, 255, 140))
            p.setFont(self._font(12 * k))
            p.drawText(r.adjusted(14 * k, 0, -14 * k, 0), Qt.AlignmentFlag.AlignVCenter, "Здесь — сообщения друзей")
            return
        y = r.y()
        now = time.monotonic()
        for idx in range(len(notes) - 1, -1, -1):
            title, text, t0 = notes[idx][:3]
            onion = notes[idx][3] if len(notes[idx]) > 3 else ""
            a = 1.0 if self.open else max(0.0, min(1.0, (8 - (now - t0)) / 0.6, (now - t0) / 0.25))
            p.setOpacity(p.opacity() * a if not self.open else a)
            rr = QRectF(r.x(), y, r.width(), r.height())
            glass(p, rr, 16 * k, acc)
            p.setPen(acc)
            p.setFont(self._font(12 * k, True))
            p.drawText(QRectF(rr.x() + 14 * k, rr.y() + 9 * k, rr.width() - 28 * k, 18 * k), Qt.AlignmentFlag.AlignLeft,
                       QFontMetrics(p.font()).elidedText(title, Qt.TextElideMode.ElideRight, int(rr.width() - 110 * k)))
            f = self._font(12 * k)
            p.setFont(f)
            p.setPen(QColor(255, 255, 255, 225))
            p.drawText(QRectF(rr.x() + 14 * k, rr.y() + 30 * k, rr.width() - 28 * k, 34 * k),
                       Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap,
                       QFontMetrics(f).elidedText(text, Qt.TextElideMode.ElideRight, int(rr.width() * 1.6)))
            if self.open and (onion or not self.live):          # ответить прямо из оверлея
                self._pill(p, QRectF(rr.right() - 92 * k, rr.y() + 7 * k, 80 * k, 20 * k), "Ответить",
                           f"reply:{idx}", acc, k=k)
            p.setOpacity(1.0 if self.open else self.cfg["opacity"] / 100)
            y += r.height() + 8 * k

    def _w_game(self, p, r, k, t, acc):
        """FPS (кадры экрана Windows) и время в игре + часы."""
        d = self.data
        if self.live:
            name, secs, fps = d.get("game") or "", float(d.get("game_secs") or 0), d.get("fps")
        else:
            name, secs, fps = "Dota 2", 3725 + t, int(141 + 4 * math.sin(t * 1.3))
        glass(p, r, 16 * k, acc)
        p.setPen(acc)
        p.setFont(self._font(20 * k, True))
        p.drawText(QRectF(r.x() + 12 * k, r.y() + 6 * k, 90 * k, 28 * k), Qt.AlignmentFlag.AlignLeft,
                   f"{fps}" if fps else "—")
        p.setPen(QColor(255, 255, 255, 150))
        p.setFont(self._font(10 * k, True))
        p.drawText(QRectF(r.x() + 12 * k, r.y() + 33 * k, 90 * k, 14 * k), Qt.AlignmentFlag.AlignLeft, "FPS ≈")
        s = int(secs)
        p.setPen(QColor(255, 255, 255, 235))
        p.setFont(self._font(13 * k, True))
        p.drawText(QRectF(r.x() + 70 * k, r.y() + 8 * k, r.width() - 82 * k, 18 * k),
                   Qt.AlignmentFlag.AlignRight, f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}")
        p.setPen(QColor(255, 255, 255, 160))
        p.setFont(self._font(10 * k))
        lt = time.localtime()
        p.drawText(QRectF(r.x() + 70 * k, r.y() + 28 * k, r.width() - 82 * k, 14 * k), Qt.AlignmentFlag.AlignRight,
                   QFontMetrics(p.font()).elidedText(f"{name} · {lt.tm_hour:02d}:{lt.tm_min:02d}" if name else
                                                     f"{lt.tm_hour:02d}:{lt.tm_min:02d}", Qt.TextElideMode.ElideLeft,
                                                     int(r.width() - 82 * k)))

    # ── ответ на сообщение ── #

    def start_reply(self, idx):
        from PyQt6.QtWidgets import QLineEdit
        if not (0 <= idx < len(self.notes)):
            return
        note = self.notes[idx]
        self._reply_to = (note[0], note[3] if len(note) > 3 else "")
        ed = getattr(self, "_reply_ed", None)
        if ed is None:
            ed = self._reply_ed = QLineEdit(self)
            ed.setStyleSheet("QLineEdit{background:rgba(20,20,24,235);color:#fff;border:1px solid rgba(255,255,255,0.25);"
                             "border-radius:14px;padding:7px 12px;font-size:13px;}")
            ed.returnPressed.connect(self._send_reply)
            ed.installEventFilter(self)
        r = self._rect("notif")
        k = self.scale()
        ed.setGeometry(int(r.x()), int(r.y() - 40 * k), int(r.width()), int(32 * k))
        ed.setPlaceholderText(f"Ответ для {note[0]} — Enter отправить, Esc отмена")
        ed.clear()
        ed.show()
        ed.raise_()
        ed.setFocus()

    def eventFilter(self, obj, e):
        from PyQt6.QtCore import QEvent
        if obj is getattr(self, "_reply_ed", None) and e.type() == QEvent.Type.KeyPress and e.key() == Qt.Key.Key_Escape:
            obj.hide()
            return True
        return super().eventFilter(obj, e)

    def _send_reply(self):
        ed = self._reply_ed
        text = ed.text().strip()
        ed.hide()
        if not text:
            return
        who, onion = getattr(self, "_reply_to", ("", ""))
        self.reply.emit(onion, text)
        self.toast(f"\U000f100f Отправлено: {who}" if onion or self.live else "\U000f100f (демо) ответ ушёл бы другу")

    def _w_lyric(self, p, r, k, t, acc):
        if self.live:
            line = self.data.get("lyric") or ""
        else:
            lines = self.data["lyrics"]
            line = lines[int(t / 3.5) % len(lines)]
        if not line and not self.open:
            return
        f = self._font(22 * k, True)
        fw = min(r.width(), QFontMetrics(f).horizontalAdvance(line or "♪") + 44 * k)
        rr = QRectF(r.center().x() - fw / 2, r.y(), fw, r.height())
        glass(p, rr, r.height() / 2, acc, 0.6)
        p.setFont(f)
        p.setPen(acc.lighter(115))
        p.drawText(rr, Qt.AlignmentFlag.AlignCenter,
                   QFontMetrics(f).elidedText(line or "♪", Qt.TextElideMode.ElideRight, int(rr.width() - 30 * k)))


def preview_data(shell) -> dict:
    d = {"title": "Ничего не играет", "artist": "ECHOES", "cover_pm": None, "playing": False, "muted": False,
         "voice": [("Вы", 0.13), ("кент", 0.55), ("lieu", 0.85), ("m1v", 0.33)],
         "msgs": [("кент", "го катку?"), ("lieu", "что слушаешь? 🔥"), ("m1v", "скинь трек"), ("кент", "я в войсе")],
         "lyrics": ["♪ строка текста песни — прямо в игре", "♪ подсвечивается в такт", "♪ можно открепить"]}
    try:
        t = shell.current_track()
        if t:
            from yamusic_theme import cover_pixmap, sanitize_title
            d.update(title=sanitize_title(t.get("title", "")), artist=t.get("artist", ""),
                     cover_pm=cover_pixmap(t.get("cover", ""), 96, 8), playing=bool(shell.is_playing()))
        lv = getattr(shell.win, "lyrics_view", None)
        ls = [x.get("text") for x in (getattr(lv, "_lines", None) or []) if isinstance(x, dict) and x.get("text")]
        if len(ls) >= 3:
            d["lyrics"] = ["♪ " + x for x in ls[:12]]
        nd = getattr(shell.pages.get("net"), "node", None)
        if nd is not None:
            fr = [c.get("name") or c["onion"][:6] for c in nd.store.contacts("friend")][:3]
            if fr:
                d["voice"] = [("Вы", 0.13)] + [(n, (0.3 + i * 0.27) % 1) for i, n in enumerate(fr)]
                d["msgs"] = [(n, m) for n in fr for m in ("го катку?", "что слушаешь?", "я в войсе")]
    except Exception:                                          # noqa: BLE001
        pass
    return d


class OverlayDemo(QDialog):
    """«Виджеты и предпросмотр»: игровой экран с оверлеем — расставить виджеты, закрепить, проверить бинды."""

    def __init__(self, shell):
        super().__init__(shell.win)
        self.shell = shell
        self.win = shell.win
        self.cfg = load(self.win.settings)
        self.setWindowTitle("Игровой оверлей ECHOES — виджеты")
        self.resize(1320, 760)
        import net_frames
        self.setStyleSheet(net_frames._QSS)
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(14)
        left = QVBoxLayout()
        t = QLabel("Игровой оверлей — виджеты")
        t.setStyleSheet("font-size:22px;font-weight:900;")
        left.addWidget(t)
        self.hint = QLabel()
        self.hint.setObjectName("Sub")
        self.hint.setWordWrap(True)
        left.addWidget(self.hint)
        self.game = OverlayCanvas(self.cfg, preview_data(shell))
        self.game.action.connect(lambda a: self.run({"prev": "next", "play": "play", "next": "next"}.get(a, a)))
        left.addWidget(self.game, 1)
        root.addLayout(left, 1)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFixedWidth(360)
        inner = QWidget()
        inner.setStyleSheet("background:transparent;")
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 0, 8, 0)
        v.setSpacing(6)
        v.addWidget(net_frames._sec("ВИДЖЕТЫ (\U000f1008 — видны поверх игры всегда)"))
        self.pins = {}
        for key, name in WIDGETS:
            cb = QCheckBox(name)
            cb.setChecked(bool(self.cfg["w"][key].get("pin")))
            cb.toggled.connect(lambda on, key=key: self.cfg["w"][key].__setitem__("pin", on))
            self.pins[key] = cb
            v.addWidget(cb)
        v.addWidget(QLabel("Прозрачность закреплённых"))
        v.addWidget(self._slider(20, 100, self.cfg["opacity"], lambda x: self.cfg.__setitem__("opacity", x)))
        v.addWidget(QLabel("Размер виджетов"))
        v.addWidget(self._slider(70, 150, self.cfg["scale"], lambda x: self.cfg.__setitem__("scale", x)))
        og = QCheckBox("Закреплённые — только поверх игр на весь экран")
        og.setChecked(bool(self.cfg.get("only_games", True)))
        og.toggled.connect(lambda on: self.cfg.__setitem__("only_games", on))
        v.addWidget(og)
        v.addWidget(net_frames._sec("УВЕДОМЛЕНИЯ В ИГРЕ"))
        for key, name in (("msg", "Сообщения друзей"), ("call", "Входящие звонки"),
                          ("friends_now", "Друг в сети / включил трек")):
            cb = QCheckBox(name)
            cb.setChecked(bool(self.cfg["show"].get(key)))
            cb.toggled.connect(lambda on, key=key: self.cfg["show"].__setitem__(key, on))
            v.addWidget(cb)
        v.addWidget(QLabel("Угол уведомлений"))
        cr = QHBoxLayout()
        grp = QButtonGroup(self)
        for key, name, pos in (("tl", "↖", (0.015, 0.04)), ("tr", "↗", (0.78, 0.04)), ("bl", "↙", (0.015, 0.72)),
                               ("br", "↘", (0.78, 0.72))):
            b = QPushButton(name)
            b.setCheckable(True)
            b.setChecked(self.cfg["corner"] == key)
            b.setFixedWidth(52)
            b.clicked.connect(lambda _=False, key=key, pos=pos: self._corner(key, pos))
            grp.addButton(b)
            cr.addWidget(b)
        cr.addStretch(1)
        v.addLayout(cr)
        tip = QLabel("Бинды и включение — в Настройки → Оформление → «Игровой оверлей».")
        tip.setObjectName("Sub")
        tip.setWordWrap(True)
        v.addWidget(tip)
        v.addStretch(1)
        sc.setWidget(inner)
        right = QVBoxLayout()
        right.addWidget(sc, 1)
        bot = QHBoxLayout()
        test = QPushButton("Открыть / закрыть")
        test.clicked.connect(lambda: self.run("toggle"))
        bot.addWidget(test)
        bot.addStretch(1)
        ok = QPushButton("Сохранить")
        ok.setObjectName("Accent")
        ok.clicked.connect(self._save)
        bot.addWidget(ok)
        right.addLayout(bot)
        root.addLayout(right)
        b = self.cfg["binds"]
        self.hint.setText(f"Это «игра». {b.get('toggle') or 'Бинд'} или кнопка справа — открыть оверлей: виджеты можно "
                          "таскать и закреплять, кнопки \U000f1011 \U000f1012 \U000f1010 нажимаются. Бинды здесь тоже работают.")
        self.game.setFocus()

    def _slider(self, lo, hi, val, fn):
        s = QSlider(Qt.Orientation.Horizontal)
        s.setRange(lo, hi)
        s.setValue(int(val))
        s.valueChanged.connect(fn)
        return s

    def _corner(self, key, pos):
        self.cfg["corner"] = key
        self.cfg["w"]["notif"]["x"], self.cfg["w"]["notif"]["y"] = pos

    def keyPressEvent(self, e):
        s = seq_of(e)
        act = next((k for k, v in self.cfg["binds"].items() if v and v == s), None)
        if e.key() == Qt.Key.Key_Escape and self.game.open:
            act = "toggle"
        if act is None:
            return super().keyPressEvent(e)
        self.run(act)

    def run(self, act):
        g, d = self.game, self.game.data
        if act == "toggle":
            g.set_open(not g.open)
        elif act == "mute":
            d["muted"] = not d.get("muted")
            g.toast("\U000f1009 Микрофон выключен" if d["muted"] else "\U000f1007 Микрофон включён")
        elif act == "play":
            d["playing"] = not d.get("playing", True)
            g.toast("▶ Играет" if d["playing"] else "\U000f1013 Пауза")
        elif act == "next":
            g.toast("\U000f1010 Следующий трек")
        elif act == "vmute":
            self.run("mute")
        elif act == "vdeaf":
            d["deaf"] = not d.get("deaf")
            g.toast("\U000f100a Звук собеседников выключен" if d["deaf"] else "\U000f100a Звук включён")
        elif act == "vleave":
            g.toast("(демо) вы вышли бы из голосового")
        elif act == "vinvite":
            g.toast("(демо) список друзей в сети — позвать в голосовой")
        elif act.startswith(("vol+:", "vol-:")):
            vols = d.setdefault("vols", {})
            o = act[5:]
            vols[o] = max(0.0, min(2.0, vols.get(o, 1.0) + (0.1 if act.startswith("vol+") else -0.1)))
            g.update()
        elif act == "pin":
            on = not all(w.get("pin") for w in self.cfg["w"].values())
            for k, w in self.cfg["w"].items():
                w["pin"] = on
                self.pins[k].setChecked(on)
            g.toast("\U000f1008 Все виджеты закреплены" if on else "Виджеты откреплены")

    def _save(self):
        cfg = load(self.win.settings)                          # включение и бинды — из «Оформления»
        for k in ("w", "opacity", "scale", "corner", "show", "only_games"):
            cfg[k] = self.cfg[k]
        save(self.win, cfg)
        self.accept()


def settings_card(shell) -> QWidget:
    """Настройки → Оформление: включить оверлей (по умолчанию выключен), бинды, виджеты."""
    from PyQt6.QtWidgets import QFrame
    win = shell.win
    cfg = load(win.settings)
    card = QFrame()
    card.setObjectName("YaWaveCard")
    card.setStyleSheet("QFrame#YaWaveCard{background:#171717;border-radius:16px;}")
    card.setMaximumWidth(760)
    v = QVBoxLayout(card)
    v.setContentsMargins(18, 14, 18, 14)
    v.setSpacing(8)
    head = QHBoxLayout()
    col = QVBoxLayout()
    col.setSpacing(2)
    t = QLabel("Игровой оверлей")
    t.setStyleSheet("font-size:14px;font-weight:700;background:transparent;")
    d = QLabel("Как в Discord: поверх игры — что играет (с кнопками), кто говорит в голосовом, сообщения друзей, "
               "текст песни, FPS и время в игре. В открытом оверлее: микрофон, звук, «позвать», «выйти», громкость "
               "каждого, «Ответить» на сообщение. Открывается биндом. Работает в играх в окне и «без рамки».")
    d.setObjectName("YaSub")
    d.setWordWrap(True)
    col.addWidget(t)
    col.addWidget(d)
    head.addLayout(col, 1)
    from yamusic_theme import Switch
    sw = Switch(bool(cfg["enabled"]))
    sw.setToolTip("Включить игровой оверлей")
    head.addWidget(sw, 0, Qt.AlignmentFlag.AlignVCenter)
    v.addLayout(head)
    body = QWidget()
    body.setStyleSheet("background:transparent;")
    bv = QVBoxLayout(body)
    bv.setContentsMargins(0, 4, 0, 0)
    bv.setSpacing(6)
    recs = {}

    def bind(key, s):
        c = load(win.settings)
        for k, kr in recs.items():                             # одно сочетание — одно действие
            if k != key and s and kr.seq == s:
                kr.set_seq("")
                c["binds"][k] = ""
        c["binds"][key] = s
        save(win, c)
    for key, name, _d in ACTIONS:
        r = QHBoxLayout()
        l = QLabel(name)
        l.setStyleSheet("background:transparent;")
        r.addWidget(l, 1)
        kr = KeyRecorder(cfg["binds"].get(key, ""))
        kr.changed.connect(lambda s, key=key: bind(key, s))
        recs[key] = kr
        r.addWidget(kr)
        bv.addLayout(r)
    tip = QLabel("Клик по бинду — нажмите сочетание. Esc — отмена, Backspace — убрать бинд.")
    tip.setObjectName("YaSub")
    bv.addWidget(tip)
    br = QHBoxLayout()
    b = QPushButton("Виджеты и предпросмотр…")
    b.clicked.connect(lambda: OverlayDemo(shell).exec())
    br.addWidget(b)
    br.addStretch(1)
    bv.addLayout(br)
    v.addWidget(body)
    body.setVisible(bool(cfg["enabled"]))

    def toggled(on):
        c = load(win.settings)
        c["enabled"] = bool(on)
        save(win, c)
        body.setVisible(bool(on))
    sw.toggled.connect(toggled)
    card._recs = recs
    card._switch = sw
    return card
