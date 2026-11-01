# ya_wave_tune.py
"""
«Настроить Мою волну» — окошко как в Яндекс Музыке: под занятие (просыпаюсь, в дороге,
работаю, тренируюсь, засыпаю), по характеру (любимое, незнакомое, популярное), под
настроение (бодрое, весёлое, спокойное, грустное) и по языку (русский, иностранный,
без слов). В каждом разделе — один выбор (повторный щелчок снимает).

build_pool() — отбор треков «Моей волны» по этим настройкам: звучание трека (энергия,
позитив, танцевальность, акустика, темп из music_intel), сколько раз его слушали,
лайки, популярность исполнителя в коллекции, язык по названию. Порядок — взвешенно-
случайный: подходящее выпадает чаще, но волна каждый раз новая.
"""
from __future__ import annotations

import math
import re

from PyQt6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PyQt6.QtWidgets import (QAbstractButton, QApplication, QFrame, QHBoxLayout, QLabel, QPushButton,
                             QVBoxLayout, QWidget)

YELLOW = "#ffdb4d"

SECTIONS = [
    ("activity", "Под занятие", [("wake", "Просыпаюсь"), ("road", "В дороге"), ("work", "Работаю"),
                                  ("sport", "Тренируюсь"), ("sleep", "Засыпаю")]),
    ("character", "По характеру", [("fav", "Любимое"), ("fresh", "Незнакомое"), ("popular", "Популярное")]),
    ("mood", "Под настроение", [("energy", "Бодрое"), ("happy", "Весёлое"), ("calm", "Спокойное"),
                                ("sad", "Грустное")]),
    ("lang", "По языку", [("ru", "Русский"), ("foreign", "Иностранный"), ("instrumental", "Без слов")]),
]
NAMES = {k: dict(items) for k, _t, items in SECTIONS}
MOOD_COLORS = {"energy": ("#ffb21e", "#ff4d1a"), "happy": ("#dfff4a", "#3fcf2a"), "calm": ("#43f0d2", "#1a8fb0"),
               "sad": ("#6e7dff", "#2a2fb8")}


def summary(tune: dict) -> str:
    """«Тренируюсь · Бодрое · Русский» — для подсказки под плеером."""
    return " · ".join(NAMES[k][tune[k]] for k, _t, _i in SECTIONS if tune.get(k) in NAMES[k])


# ══════════════════════════════════════════════════════════════════════════ #
#  Отбор треков
# ══════════════════════════════════════════════════════════════════════════ #

_CYR = re.compile("[а-яёіїєґА-ЯЁІЇЄҐ]")
_INSTR = re.compile(r"instrumental|инструментал|\binst\b|type beat|\bbeats?\b|минус|\bminus\b|karaoke|караоке"
                    r"|\bbgm\b|lo-?fi|ambient|эмбиент|piano version|без слов|\bost\b", re.I)
_NO_WORDS_FAMILIES = {"classical", "ambient", "lofi", "soundtrack"}


def lang_of(t: dict, pr: dict | None) -> str:
    s = f"{t.get('title', '')} {t.get('artist', '')} {t.get('album', '')}"
    if _INSTR.search(s) or (pr and set((pr.get("families") or [])[:1]) & _NO_WORDS_FAMILIES):
        return "instrumental"
    return "ru" if _CYR.search(f"{t.get('title', '')} {t.get('artist', '')}") else "foreign"


def _fit(x, lo, hi):
    """1 внутри [lo, hi], плавно падает снаружи."""
    if lo <= x <= hi:
        return 1.0
    d = (lo - x) if x < lo else (x - hi)
    return max(0.0, 1.0 - d * 2.5)


def activity_score(key: str, pr: dict) -> float:
    E, V = float(pr.get("energy", 0.5)), float(pr.get("valence", 0.5))
    D, A = float(pr.get("dance", 0.5)), float(pr.get("acoustic", 0.3))
    bpm = float(pr.get("bpm") or 0) or 110.0
    if key == "wake":                                  # мягкий подъём: средняя энергия, светлое
        return 0.5 * _fit(E, 0.35, 0.62) + 0.35 * V + 0.15 * A
    if key == "road":                                  # в дороге: ровный драйв и грув
        return 0.45 * D + 0.4 * _fit(E, 0.55, 0.85) + 0.15 * V
    if key == "work":                                  # работа: спокойно, не отвлекает
        return 0.45 * _fit(E, 0.18, 0.5) + 0.3 * A + 0.25 * (1.0 - D)
    if key == "sport":                                 # тренировка: энергия и быстрый темп
        return 0.55 * E + 0.3 * _fit(bpm / 200.0, 0.6, 0.88) + 0.15 * D
    if key == "sleep":                                 # засыпаю: тихо, медленно, акустика
        return 0.55 * (1.0 - E) + 0.3 * A + 0.15 * _fit(bpm / 200.0, 0.0, 0.5)
    return 0.5


def mood_score(key: str, pr: dict) -> float:
    E, V = float(pr.get("energy", 0.5)), float(pr.get("valence", 0.5))
    return {"energy": E, "calm": 1.0 - E, "happy": V, "sad": 1.0 - V}.get(key, 0.5)


def build_pool(lib: list, prof: dict, hist: dict, liked: set, tune: dict, rng, artist_of=None) -> list:
    """Треки «Моей волны» под настройки, по убыванию «подходит + случайность»."""
    artist_of = artist_of or (lambda t: (t.get("artist") or "").split(",")[0].strip().lower())
    act, char, mood, lang = (tune.get(k) for k in ("activity", "character", "mood", "lang"))

    def plays(t):
        try:
            return int((hist.get(str(t.get("path", ""))) or {}).get("count", 0))
        except (TypeError, ValueError):
            return 0
    pool = list(lib)
    # язык и «незнакомое» — фильтры (если подходящих совсем мало — мягкая надбавка вместо фильтра)
    soft_lang = False
    if lang:
        same = [t for t in pool if lang_of(t, prof.get(str(t.get("path", "")))) == lang]
        if len(same) >= 8:
            pool = same
        else:
            soft_lang = True
    if char == "fresh":
        new = [t for t in pool if plays(t) == 0]
        pool = new if len(new) >= 8 else sorted(pool, key=plays)[:max(8, len(pool) // 3)]
    if not pool:
        pool = list(lib)
    max_p = max([plays(t) for t in pool] + [1])
    art_n: dict = {}
    if char == "popular":
        for t in lib:                                  # популярность исполнителя в коллекции: треки + прослушивания
            a = artist_of(t)
            art_n[a] = art_n.get(a, 0) + 1 + plays(t)
        top_a = max(art_n.values()) if art_n else 1
    scored = []
    for t in pool:
        p = str(t.get("path", ""))
        pr = prof.get(p)
        parts = []
        if act:
            parts.append(activity_score(act, pr) if pr else 0.3)
        if mood:
            parts.append(mood_score(mood, pr) if pr else 0.3)
        if char == "fav":
            parts.append(min(1.0, math.log1p(plays(t)) / math.log1p(max_p) + (0.6 if p in liked else 0.0)))
        elif char == "popular":
            parts.append(math.log1p(art_n.get(artist_of(t), 0)) / math.log1p(top_a))
        if soft_lang:
            parts.append(1.0 if lang_of(t, pr) == lang else 0.2)
        if parts:
            s = sum(parts) / len(parts)
        else:                                          # ничего не выбрано — как обычная «Моя волна»
            s = 0.35 + 0.65 * math.sqrt(plays(t) / max_p) + (0.15 if p in liked else 0.0)
        scored.append((s, t))
    # оценки — в ранги внутри подборки: у многих треков профиль почти одинаковый (жанровый), и без
    # этого «Тренируюсь» почти не отличалось от «Засыпаю»; одинаковые оценки — одинаковый ранг
    order = sorted(range(len(scored)), key=lambda i: scored[i][0])
    pct = [0.0] * len(scored)
    i = 0
    n = max(1, len(scored) - 1)
    while i < len(order):
        j = i
        while j + 1 < len(order) and scored[order[j + 1]][0] == scored[order[i]][0]:
            j += 1
        for k in range(i, j + 1):
            pct[order[k]] = ((i + j) / 2) / n
        i = j + 1
    keyed = []
    for (s, t), q in zip(scored, pct):
        w = math.exp(6.0 * q)                          # лучшие по настройкам — в разы чаще, но порядок случайный
        keyed.append((rng.random() ** (1.0 / w), t))
    keyed.sort(key=lambda x: -x[0])
    return [t for _, t in keyed]


# ══════════════════════════════════════════════════════════════════════════ #
#  Окошко
# ══════════════════════════════════════════════════════════════════════════ #

QSS = """
QFrame#WaveTune { background: #2a2d2f; border: 1px solid rgba(255,255,255,0.07); border-radius: 18px; }
QFrame#WaveTune QLabel { background: transparent; color: #ffffff; font-family: 'Segoe UI'; }
QFrame#WaveTune QLabel#Title { font-size: 17px; font-weight: 800; }
QFrame#WaveTune QLabel#Sec { font-size: 12px; color: rgba(255,255,255,0.62); font-weight: 600; }
QFrame#WaveTune QPushButton#Round { background: #3b3f42; border: none; border-radius: 15px; }
QFrame#WaveTune QPushButton#Round:hover { background: #4a4e51; }
"""
# у пилюль — свой стиль без селектора окошка: ширина меряется до того, как кнопка попала в окошко
PILL_QSS = ("QPushButton { background: #3b3f42; color: #ffffff; border: 2px solid transparent; border-radius: 15px;"
            " padding: 5px 12px; font-family: 'Segoe UI'; font-size: 13px; font-weight: 600; }"
            " QPushButton:hover { background: #464a4d; }"
            " QPushButton:checked { border: 2px solid " + YELLOW + "; }")


def _draw_char_icon(p: QPainter, key: str, c: QPointF, s: float):
    """Значки характера рисуются кодом: сердце, звезда-искра, молния (без эмодзи)."""
    p.setPen(Qt.PenStyle.NoPen)
    if key == "fav":
        path = QPainterPath()
        x, y = c.x(), c.y() + s * 0.05
        path.moveTo(x, y + s * 0.42)
        path.cubicTo(x - s * 0.62, y + s * 0.02, x - s * 0.55, y - s * 0.52, x - s * 0.26, y - s * 0.48)
        path.cubicTo(x - s * 0.12, y - s * 0.47, x - s * 0.03, y - s * 0.38, x, y - s * 0.28)
        path.cubicTo(x + s * 0.03, y - s * 0.38, x + s * 0.12, y - s * 0.47, x + s * 0.26, y - s * 0.48)
        path.cubicTo(x + s * 0.55, y - s * 0.52, x + s * 0.62, y + s * 0.02, x, y + s * 0.42)
        g = QLinearGradient(x - s * 0.5, y - s * 0.5, x + s * 0.5, y + s * 0.5)
        g.setColorAt(0, QColor("#ff6b6b"))
        g.setColorAt(1, QColor("#c8102e"))
        p.setBrush(g)
        p.drawPath(path)
    elif key == "fresh":
        path = QPainterPath()
        x, y, r = c.x(), c.y(), s * 0.5
        k = 0.16
        path.moveTo(x, y - r)
        path.quadTo(x + r * k, y - r * k, x + r, y)
        path.quadTo(x + r * k, y + r * k, x, y + r)
        path.quadTo(x - r * k, y + r * k, x - r, y)
        path.quadTo(x - r * k, y - r * k, x, y - r)
        g = QRadialGradient(QPointF(x, y), r)
        g.setColorAt(0, QColor("#fff3a0"))
        g.setColorAt(1, QColor("#ffb300"))
        p.setBrush(g)
        p.drawPath(path)
    else:                                              # popular — молния
        path = QPainterPath()
        x, y = c.x(), c.y()
        pts = [(0.12, -0.5), (-0.34, 0.06), (-0.02, 0.06), (-0.14, 0.5), (0.34, -0.08), (0.03, -0.08), (0.12, -0.5)]
        path.moveTo(x + pts[0][0] * s, y + pts[0][1] * s)
        for dx, dy in pts[1:]:
            path.lineTo(x + dx * s, y + dy * s)
        g = QLinearGradient(x, y - s * 0.5, x, y + s * 0.5)
        g.setColorAt(0, QColor("#b8ff5c"))
        g.setColorAt(1, QColor("#1fbf4a"))
        p.setBrush(g)
        p.drawPath(path)


class _Card(QAbstractButton):
    """Карточка «характера»: значок и подпись; выбранная — в жёлтой рамке."""

    def __init__(self, key, text, parent=None):
        super().__init__(parent)
        self.key = key
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(84, 80)
        self._hover = False

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        p.setPen(QPen(QColor(YELLOW), 2.2) if self.isChecked() else Qt.PenStyle.NoPen)
        p.setBrush(QColor("#464a4d" if self._hover else "#3b3f42"))
        p.drawRoundedRect(r, 14, 14)
        _draw_char_icon(p, self.key, QPointF(r.center().x(), r.top() + 28), 30)
        p.setPen(QColor(255, 255, 255))
        f = QFont("Segoe UI")
        f.setPixelSize(12)
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.drawText(QRectF(r.left(), r.bottom() - 26, r.width(), 20), Qt.AlignmentFlag.AlignCenter, self.text())
        p.end()


class _Dot(QAbstractButton):
    """Кружок настроения с градиентом; выбранный — в жёлтом кольце."""

    def __init__(self, key, text, parent=None):
        super().__init__(parent)
        self.key = key
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(64, 78)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(self.width() / 2, 27)
        c1, c2 = MOOD_COLORS[self.key]
        g = QLinearGradient(c.x() - 20, c.y() - 20, c.x() + 20, c.y() + 20)
        g.setColorAt(0, QColor(c1))
        g.setColorAt(1, QColor(c2))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawEllipse(c, 21, 21)
        if self.isChecked():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(YELLOW), 2.2))
            p.drawEllipse(c, 25.5, 25.5)
        p.setPen(QColor(255, 255, 255))
        f = QFont("Segoe UI")
        f.setPixelSize(12)
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.drawText(QRectF(0, 56, self.width(), 20), Qt.AlignmentFlag.AlignCenter, self.text())
        p.end()


class _IconBtn(QPushButton):
    """Круглая кнопка шапки: «сбросить» (стрелка по кругу) или «закрыть» (крестик)."""

    def __init__(self, kind, tip, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setObjectName("Round")
        self.setToolTip(tip)
        self.setFixedSize(30, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(255, 255, 255), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        cx, cy = self.width() / 2, self.height() / 2
        if self.kind == "close":
            p.drawLine(QPointF(cx - 5, cy - 5), QPointF(cx + 5, cy + 5))
            p.drawLine(QPointF(cx + 5, cy - 5), QPointF(cx - 5, cy + 5))
        else:
            p.drawArc(QRectF(cx - 6.5, cy - 6.5, 13, 13), 60 * 16, 290 * 16)
            a = math.radians(60)
            tip = QPointF(cx + 6.5 * math.cos(a), cy - 6.5 * math.sin(a))
            p.drawLine(tip, QPointF(tip.x() - 4.2, tip.y() - 0.6))
            p.drawLine(tip, QPointF(tip.x() + 0.4, tip.y() + 4.2))
        p.end()


class WaveTunePopup(QFrame):
    changed = pyqtSignal(dict)

    def __init__(self, parent, tune: dict | None = None):
        super().__init__(parent)
        self.setObjectName("WaveTune")
        self.setStyleSheet(QSS)
        self.tune = {k: v for k, v in (tune or {}).items() if k in NAMES and v in NAMES[k]}
        self.btns: dict = {}
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 14, 14, 16)
        v.setSpacing(8)
        head = QHBoxLayout()
        t = QLabel("Настроить Мою волну")
        t.setObjectName("Title")
        head.addWidget(t, 1)
        rb = _IconBtn("reset", "Сбросить настройки волны")
        rb.clicked.connect(self.reset)
        cb = _IconBtn("close", "Закрыть")
        cb.clicked.connect(self.hide)
        head.addWidget(rb)
        head.addWidget(cb)
        v.addLayout(head)
        for sec, title, items in SECTIONS:
            lb = QLabel(title)
            lb.setObjectName("Sec")
            v.addSpacing(4)
            v.addWidget(lb)
            if sec == "character":
                row = QHBoxLayout()
                row.setSpacing(8)
                for k, name in items:
                    b = _Card(k, name)
                    self._hook(sec, k, b)
                    row.addWidget(b)
                row.addStretch(1)
                v.addLayout(row)
            elif sec == "mood":
                row = QHBoxLayout()
                row.setSpacing(4)
                for k, name in items:
                    b = _Dot(k, name)
                    self._hook(sec, k, b)
                    row.addWidget(b)
                row.addStretch(1)
                v.addLayout(row)
            else:
                # пилюли переносятся по строкам каждая своей ширины (сетка равняла колонки и резала текст)
                row = None
                width = 0
                for k, name in items:
                    b = QPushButton(name)
                    b.setObjectName("Pill")
                    b.setCheckable(True)
                    b.setCursor(Qt.CursorShape.PointingHandCursor)
                    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                    b.setStyleSheet(PILL_QSS)                  # отступы стиля — до замера ширины
                    b.ensurePolished()
                    w = b.sizeHint().width()
                    b.setMinimumWidth(w)
                    self._hook(sec, k, b)
                    if row is None or width + w > 308:
                        if row is not None:
                            row.addStretch(1)
                        row = QHBoxLayout()
                        row.setSpacing(6)
                        v.addLayout(row)
                        width = 0
                    row.addWidget(b)
                    width += w + 6
                if row is not None:
                    row.addStretch(1)
        self._sync_checks()
        self.setFixedWidth(342)
        self.adjustSize()
        self.hide()

    def _hook(self, sec, key, b):
        self.btns[(sec, key)] = b
        b.clicked.connect(lambda _=False, s=sec, k=key: self._pick(s, k))

    def _pick(self, sec, key):
        self.tune[sec] = None if self.tune.get(sec) == key else key   # повторный щелчок снимает выбор
        if self.tune[sec] is None:
            del self.tune[sec]
        self._sync_checks()
        self.changed.emit(dict(self.tune))

    def _sync_checks(self):
        for (sec, key), b in self.btns.items():
            b.blockSignals(True)
            b.setChecked(self.tune.get(sec) == key)
            b.blockSignals(False)
            b.update()

    def reset(self):
        if self.tune:
            self.tune = {}
            self._sync_checks()
            self.changed.emit({})

    # ── показ: рядом с кнопкой, закрывается щелчком мимо и Esc ── #
    def popup(self, anchor: QWidget | None = None):
        par = self.parentWidget()
        self._anchor = anchor
        self.adjustSize()
        W = par.width()
        x, y = W - self.width() - 24, 64
        if anchor is not None and anchor.isVisible():
            pt = anchor.mapTo(par, anchor.rect().bottomRight())
            x = min(W - self.width() - 12, max(12, pt.x() - self.width()))
            y = pt.y() + 8
        y = max(8, min(y, par.height() - self.height() - 8))
        self.move(int(x), int(y))
        self.show()
        self.raise_()
        QApplication.instance().installEventFilter(self)

    def hideEvent(self, e):
        super().hideEvent(e)
        try:
            QApplication.instance().removeEventFilter(self)
        except Exception:                              # noqa: BLE001
            pass

    def eventFilter(self, obj, e):
        t = e.type()
        if t == QEvent.Type.MouseButtonPress and self.isVisible():
            w = obj if isinstance(obj, QWidget) else None
            a = getattr(self, "_anchor", None)
            on_anchor = a is not None and w is not None and (w is a or a.isAncestorOf(w))
            if w is not None and w is not self and not self.isAncestorOf(w) and not on_anchor \
                    and w.window() is self.window():
                self.hide()                            # щелчок мимо окошка
        elif t == QEvent.Type.KeyPress and e.key() == Qt.Key.Key_Escape and self.isVisible():
            self.hide()
            return True
        return False

    def sizeHint(self):
        return QSize(342, super().sizeHint().height())
