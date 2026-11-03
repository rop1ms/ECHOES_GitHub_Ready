# ya_history.py
"""
История прослушиваний календарём (вкладка «Время прослушивания» в профиле):
  * тепловая карта месяца — чем ярче день, тем больше музыки; стрелки — листать месяцы;
  * клик по дню — что звучало в этот день (по времени, клик по треку — включить);
  * «В этот день год назад» (если год назад журнала ещё нет — «месяц назад»): что вы тогда слушали.
Данные — журнал listen_stats.history и минуты по дням.
"""
from __future__ import annotations

import calendar
import time

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

MONTHS = ("Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь",
          "Декабрь")
WD = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


def _acc():
    try:
        import ya_decor
        return ya_decor.base_accent()
    except Exception:                                          # noqa: BLE001
        return QColor("#ffdb1a")


class MonthHeat(QWidget):
    """Месяц сеткой 7×6: яркость — минуты музыки за день."""
    picked = pyqtSignal(str)

    def __init__(self, days: dict, year: int, month: int, parent=None):
        super().__init__(parent)
        self.days, self.year, self.month = days, year, month
        self.sel = ""
        self.setFixedHeight(250)
        self.setMinimumWidth(330)
        self.setMouseTracking(True)
        self._hover = ""

    def _cells(self):
        W = self.width()
        cw = min(46.0, (W - 10) / 7)
        x0 = (W - cw * 7) / 2
        out = []
        for wi, week in enumerate(calendar.monthcalendar(self.year, self.month)):
            for di, d in enumerate(week):
                if d:
                    out.append((f"{self.year:04d}-{self.month:02d}-{d:02d}", d,
                                QRectF(x0 + di * cw + 2, 26 + wi * (cw * 0.82) + 2, cw - 4, cw * 0.82 - 4)))
        return out

    def _at(self, pos):
        return next((k for k, _d, r in self._cells() if r.contains(pos)), "")

    def mouseMoveEvent(self, e):
        h = self._at(e.position())
        if h != self._hover:
            self._hover = h
            mins = int(self.days.get(h, 0) // 60) if h else 0
            self.setToolTip(f"{h}: {mins} мин" if h else "")
            self.setCursor(Qt.CursorShape.PointingHandCursor if h else Qt.CursorShape.ArrowCursor)
            self.update()

    def mouseReleaseEvent(self, e):
        k = self._at(e.position())
        if k:
            self.sel = k
            self.update()
            self.picked.emit(k)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        acc = _acc()
        cells = self._cells()
        mx = max([self.days.get(k, 0) for k, _d, _r in cells] + [1])
        f = QFont()
        f.setPixelSize(11)
        f.setBold(True)
        p.setFont(f)
        if cells:
            cw = cells[0][2].width() + 4
            x0 = cells[0][2].x() - 2 - (time.strptime(cells[0][0], "%Y-%m-%d").tm_wday) * cw
            for i, w in enumerate(WD):
                p.setPen(QColor(255, 255, 255, 110))
                p.drawText(QRectF(x0 + i * cw, 4, cw, 18), Qt.AlignmentFlag.AlignCenter, w)
        today = time.strftime("%Y-%m-%d")
        for k, d, r in cells:
            v = self.days.get(k, 0)
            a = 0.0 if v < 30 else 0.18 + 0.82 * min(1.0, v / mx) ** 0.6
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 14))
            p.drawRoundedRect(r, 8, 8)
            if a > 0:
                c = QColor(acc)
                c.setAlphaF(a)
                p.setBrush(c)
                p.drawRoundedRect(r, 8, 8)
            if k == self.sel or k == self._hover or k == today:
                from PyQt6.QtGui import QPen
                p.setPen(QPen(QColor(255, 255, 255, 220 if k == self.sel else 110), 1.6))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 7, 7)
            p.setPen(QColor(0, 0, 0) if a > 0.6 else QColor(255, 255, 255, 200))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, str(d))
        p.end()


def _track_row(shell, r):
    """Строка трека дня: время, обложка, название — клик включает."""
    from yamusic_theme import cover_pixmap
    w = QFrame()
    w.setObjectName("HRow")
    w.setStyleSheet("QFrame#HRow{background:rgba(255,255,255,0.04);border-radius:10px;}"
                    "QFrame#HRow:hover{background:rgba(255,255,255,0.09);}")
    w.setCursor(Qt.CursorShape.PointingHandCursor)
    h = QHBoxLayout(w)
    h.setContentsMargins(8, 4, 10, 4)
    h.setSpacing(10)
    tm = QLabel(time.strftime("%H:%M", time.localtime(r["ts"])))
    tm.setStyleSheet("color:#8a8a8a;font-weight:700;background:transparent;")
    tm.setFixedWidth(40)
    h.addWidget(tm)
    lib = {str(t.get("path", "")): t for t in shell.win.library}
    t = lib.get(r.get("path", "")) or {}
    pic = QLabel()
    pic.setFixedSize(34, 34)
    pic.setPixmap(cover_pixmap(r.get("cover") or t.get("cover", ""), 34, 6, seed=r.get("title", "")))
    h.addWidget(pic)
    import html as _h                                    # названия из тегов и с площадок — текст, не разметка
    txt = QLabel(f"<b>{_h.escape(str(r.get('title', '')))}</b>  "
                 f"<span style='color:#9a9a9a'>{_h.escape(str(r.get('artist', '')))}</span>")
    txt.setStyleSheet("background:transparent;")
    h.addWidget(txt, 1)

    def play(_e, t=t, r=r):
        if t:
            shell.play_path(str(t.get("path")))
        else:
            net = shell.pages.get("net")
            if net is not None:
                net.play_meta({"artist": r.get("artist", ""), "title": r.get("title", "")})
    w.mouseReleaseEvent = play
    return w


class HistoryBlock(QWidget):
    """Календарь + список дня + «в этот день год назад»."""

    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self.shell = shell
        self.st = shell.win.stats
        self.setObjectName("YaPageInner")
        lt = time.localtime()
        self.y, self.m = lt.tm_year, lt.tm_mon
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        from yamusic_theme import section_title
        v.addWidget(section_title("История по дням"))
        self.ago = QVBoxLayout()
        v.addLayout(self.ago)
        row = QHBoxLayout()
        row.setSpacing(18)
        col = QVBoxLayout()
        nav = QHBoxLayout()
        b1 = QPushButton("‹")
        b1.setObjectName("YaChip")
        b1.setFixedWidth(44)
        b1.clicked.connect(lambda: self._shift(-1))
        self.title = QLabel()
        self.title.setStyleSheet("font-size:16px;font-weight:800;background:transparent;")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        b2 = QPushButton("›")
        b2.setObjectName("YaChip")
        b2.setFixedWidth(44)
        b2.clicked.connect(lambda: self._shift(1))
        nav.addWidget(b1)
        nav.addWidget(self.title, 1)
        nav.addWidget(b2)
        col.addLayout(nav)
        self.heat_box = QVBoxLayout()
        col.addLayout(self.heat_box)
        row.addLayout(col, 0)
        self.day_box = QVBoxLayout()
        self.day_box.setSpacing(4)
        dw = QWidget()
        dw.setObjectName("YaPageInner")
        dw.setLayout(self.day_box)
        row.addWidget(dw, 1)
        v.addLayout(row)
        self.heat = None
        self._month()
        self.show_day(time.strftime("%Y-%m-%d"))
        self._ago()

    def _shift(self, d):
        m = self.m + d
        y = self.y + (m - 1) // 12
        m = (m - 1) % 12 + 1
        lt = time.localtime()
        if (y, m) > (lt.tm_year, lt.tm_mon):
            return
        self.y, self.m = y, m
        self._month()

    def _month(self):
        if self.heat is not None:
            self.heat.deleteLater()
        self.heat = MonthHeat(self.st.data.get("days") or {}, self.y, self.m)
        self.heat.picked.connect(self.show_day)
        self.heat_box.addWidget(self.heat)
        mins = sum(v for k, v in (self.st.data.get("days") or {}).items() if k.startswith(f"{self.y:04d}-{self.m:02d}"))
        self.title.setText(f"{MONTHS[self.m - 1]} {self.y} · {int(mins // 3600)} ч {int(mins % 3600 // 60)} мин")

    def _clear(self, lay):
        while lay.count():
            it = lay.takeAt(0)
            if it.widget() is not None:
                it.widget().deleteLater()

    def show_day(self, date):
        self._clear(self.day_box)
        y, m, d = (int(x) for x in date.split("-"))
        mins = int((self.st.data.get("days") or {}).get(date, 0) // 60)
        h = QLabel(f"{d} {('января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря')[m - 1]}"
                   f"  ·  {mins} мин")
        h.setStyleSheet("font-size:15px;font-weight:800;background:transparent;")
        self.day_box.addWidget(h)
        plays = self.st.plays_on(date) if hasattr(self.st, "plays_on") else []
        if not plays:
            e = QLabel("В этот день журнал пуст" if mins else "В этот день музыки не было")
            e.setObjectName("YaSub")
            self.day_box.addWidget(e)
        for r in plays[-14:][::-1]:
            self.day_box.addWidget(_track_row(self.shell, r))
        if len(plays) > 14:
            more = QLabel(f"…и ещё {len(plays) - 14}")
            more.setObjectName("YaSub")
            self.day_box.addWidget(more)
        self.day_box.addStretch(1)

    def _ago(self):
        now = time.time()
        for back, label in ((365, "В этот день год назад"), (30, "Месяц назад")):
            date = time.strftime("%Y-%m-%d", time.localtime(now - back * 86400))
            plays = self.st.plays_on(date) if hasattr(self.st, "plays_on") else []
            if plays:
                card = QFrame()
                card.setObjectName("AgoCard")
                card.setStyleSheet("QFrame#AgoCard{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,"
                                   "stop:0 rgba(124,77,255,0.28),stop:1 rgba(47,198,255,0.10));border-radius:16px;}"
                                   "QLabel{background:transparent;}")
                cv = QVBoxLayout(card)
                cv.setContentsMargins(16, 12, 16, 12)
                t = QLabel(f"{label} вы слушали")
                t.setStyleSheet("font-size:15px;font-weight:800;")
                cv.addWidget(t)
                seen, shown = set(), 0
                for r in plays[::-1]:
                    k = (r.get("artist"), r.get("title"))
                    if k in seen:
                        continue
                    seen.add(k)
                    cv.addWidget(_track_row(self.shell, r))
                    shown += 1
                    if shown >= 3:
                        break
                self.ago.addWidget(card)
                return
