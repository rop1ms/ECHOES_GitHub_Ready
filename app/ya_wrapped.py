# ya_wrapped.py
"""
Echoes Wrapped — итоги года сторис-слайдами (как Spotify Wrapped).

Слайды: вступление → сколько минут музыки → исполнитель года (с фото) → топ-5 исполнителей → трек года
(с обложкой) → топ-5 треков → жанр года → когда слушаете (час пик, самый музыкальный день) → итоговая карточка.
Листаются сами (6 с), клик справа/слева — вперёд/назад, удержание — пауза, Esc — закрыть.
Итоговую карточку можно сохранить картинкой или выложить постом в Echoes.NET.

Данные: журнал прослушиваний (listen_stats.history — по годам) и минуты по дням; если журнал за год
короткий (он появился недавно) — общая статистика.
"""
from __future__ import annotations

import math
import time
from collections import Counter
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import (QColor, QFont, QFontMetrics, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
                         QRadialGradient)
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

SLIDE_SEC = 6.0
MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября",
          "декабря")


def _first_artist(a):
    from listen_stats import _first_artist as f
    return f(a)


def compute(win, year: int | None = None) -> dict:
    """Итоги года из журнала и статистики."""
    st = win.stats
    year = year or time.localtime().tm_year
    days = {k: v for k, v in (st.data.get("days") or {}).items() if k.startswith(str(year))}
    seconds = sum(days.values())
    hist = st.history(year) if hasattr(st, "history") else []
    lib = {str(t.get("path", "")): t for t in win.library}
    tracks, artists = Counter(), Counter()
    meta = {}
    hours = Counter()
    for r in hist:
        k = r.get("path") or f"{r.get('artist')}|{r.get('title')}"
        tracks[k] += 1
        meta.setdefault(k, r)
        artists[_first_artist(r.get("artist"))] += 1
        hours[time.localtime(r["ts"]).tm_hour] += 1
    if len(hist) < 30:                                         # журнал ещё короткий — общая статистика
        tracks, artists = Counter(), Counter()
        for k, v in (st.data.get("tracks") or {}).items():
            if v.get("plays"):
                tracks[k] = int(v["plays"])
                meta.setdefault(k, {"path": k, "title": v.get("title", ""), "artist": v.get("artist", "")})
        for k, v in (st.data.get("artists") or {}).items():
            if v.get("plays") and k:
                artists[k] = int(v["plays"])
    art_sec = {k: float(v.get("sec", 0)) for k, v in (st.data.get("artists") or {}).items()}
    top_artists = [(a, n, art_sec.get(a, 0)) for a, n in artists.most_common(5) if a]
    top_tracks = []
    for k, n in tracks.most_common(5):
        m = dict(meta.get(k) or {})
        t = lib.get(k) or {}
        m["cover"] = m.get("cover") or t.get("cover", "")
        m["plays"] = n
        top_tracks.append(m)
    # жанр года — по жанрам треков (с весом прослушиваний)
    genre = ""
    try:
        sh = getattr(win, "_ya", None)
        prof = sh.profiles() if sh is not None else {}
        gc = Counter()
        for k, n in tracks.most_common(300):
            for f in ((prof or {}).get(k) or {}).get("families", [])[:1]:
                gc[f] += n
        if gc:
            from music_intel import GENRE_RU
            g = gc.most_common(1)[0][0]
            genre = GENRE_RU.get(g, g)
    except Exception:                                          # noqa: BLE001
        pass
    best_day = max(days.items(), key=lambda kv: kv[1]) if days else None
    peak = hours.most_common(1)[0][0] if hours else None
    total_plays = sum(tracks.values())
    return {"year": year, "minutes": int(seconds // 60), "days_active": sum(1 for v in days.values() if v > 60),
            "best_day": best_day, "peak_hour": peak, "top_artists": top_artists, "top_tracks": top_tracks,
            "genre": genre, "plays": total_plays, "artists_n": len(artists),
            "persona": persona(peak, genre, total_plays, len(artists))}


def persona(peak, genre, plays, n_artists) -> tuple[str, str]:
    """Кто вы как слушатель — заголовок и пояснение."""
    if peak is not None and (peak >= 23 or peak < 5):
        return "Ночной слушатель", "самая музыка у вас — после полуночи"
    if n_artists and plays and n_artists > plays * 0.45:
        return "Исследователь", "всё время ищете новое — исполнителей почти столько же, сколько прослушиваний"
    if n_artists and plays and n_artists < plays * 0.08:
        return "Преданный фанат", "вы знаете любимых наизусть и возвращаетесь к ним снова и снова"
    if peak is not None and 6 <= peak < 11:
        return "Утренний заряд", "музыка с самого утра"
    return "Меломан", f"{genre.capitalize()} и не только" if genre else "слушаете всё подряд — и это прекрасно"


def _accent():
    try:
        import ya_decor
        return ya_decor.base_accent()
    except Exception:                                          # noqa: BLE001
        return QColor("#ffdb1a")


def _font(px, w=QFont.Weight.Black):
    f = QFont()
    f.setFamilies(["YS Text", "Inter", "Segoe UI Variable Display", "Segoe UI"])
    f.setPixelSize(max(6, int(px)))
    f.setWeight(w)
    return f


def _ease(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def _human(n) -> str:
    return f"{n:,}".replace(",", " ")


class StoryCard(QWidget):
    """Карточка 9:16 со слайдами; анимация входа каждого слайда."""
    W, H = 450, 800

    def __init__(self, data, parent=None):
        super().__init__(parent)
        self.d = data
        self.setFixedSize(self.W, self.H)
        self.i = 0
        self.t0 = time.monotonic()
        self.paused = False
        self._paused_at = 0.0
        self.slides = ["intro", "minutes", "artist", "artists", "track", "tracks", "genre", "time", "summary"]
        if not data["top_artists"]:
            self.slides = [s for s in self.slides if s not in ("artist", "artists")]
        if not data["top_tracks"]:
            self.slides = [s for s in self.slides if s not in ("track", "tracks")]
        if not data["genre"]:
            self.slides.remove("genre")
        self._pics = {}
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self.on_change = None

    # ── управление ── #

    def go(self, i):
        self.i = max(0, min(len(self.slides) - 1, i))
        self.t0 = time.monotonic()
        if self.on_change:
            self.on_change(self.slides[self.i])
        self.update()

    def elapsed(self) -> float:
        return (self._paused_at if self.paused else time.monotonic()) - self.t0

    def _tick(self):
        if not self.paused and self.slides[self.i] != "summary" and self.elapsed() >= SLIDE_SEC:
            self.go(self.i + 1)
        self.update()

    def mousePressEvent(self, e):
        self.paused = True
        self._paused_at = time.monotonic()
        self._press = time.monotonic()

    def mouseReleaseEvent(self, e):
        held = time.monotonic() - getattr(self, "_press", 0)
        if self.paused:
            self.t0 += time.monotonic() - self._paused_at
            self.paused = False
        if held < 0.3:                                         # короткий клик — листать
            self.go(self.i + (1 if e.position().x() > self.width() * 0.35 else -1))

    # ── картинки ── #

    def _pic(self, path, size, circle=False):
        key = (path, size, circle)
        if key not in self._pics:
            from yamusic_theme import cover_pixmap
            self._pics[key] = cover_pixmap(path or "", size, size // 2 if circle else 14, seed=path or "e",
                                           circle=circle)
        return self._pics[key]

    @staticmethod
    def _draw(p, r, pm):
        p.drawPixmap(r, pm, QRectF(pm.rect()))

    def _artist_pic(self, name):
        try:
            from artist_net import cached
            return (cached(name) or {}).get("picture", "") or ""
        except Exception:                                      # noqa: BLE001
            return ""

    # ── рисование ── #

    def _bg(self, p, kind, t):
        W, H = self.W, self.H
        acc = _accent()
        pal = {"intro": (acc, QColor("#ff4fa3"), QColor("#2a0a4a")), "minutes": (QColor("#1ed760"), acc, QColor("#06301a")),
               "artist": (QColor("#ff4fa3"), QColor("#7c4dff"), QColor("#1a0626")),
               "artists": (QColor("#7c4dff"), QColor("#2fc6ff"), QColor("#0a0f2e")),
               "track": (QColor("#ff9d00"), QColor("#ff3b3b"), QColor("#2a0a04")),
               "tracks": (QColor("#ff3b3b"), acc, QColor("#2a0606")),
               "genre": (QColor("#2fc6ff"), QColor("#5ef2c2"), QColor("#04202a")),
               "time": (QColor("#1b1f6b"), QColor("#7c8cff"), QColor("#05061a")),
               "summary": (acc, QColor("#ff4fa3"), QColor("#0c0c10"))}[kind]
        p.fillRect(QRectF(0, 0, W, H), pal[2])
        for j, (cx, cy, rr, col) in enumerate(((0.2, 0.15, 0.7, pal[0]), (0.9, 0.7, 0.75, pal[1]),
                                              (0.3, 0.95, 0.5, pal[0]))):
            ox = math.sin(t * 0.6 + j * 2) * 30
            oy = math.cos(t * 0.5 + j) * 30
            g = QRadialGradient(QPointF(cx * W + ox, cy * H + oy), rr * W)
            c = QColor(col)
            c.setAlpha(170)
            g.setColorAt(0, c)
            c.setAlpha(0)
            g.setColorAt(1, c)
            p.fillRect(QRectF(0, 0, W, H), g)

    def _text(self, p, rect, text, px, a=1.0, color=QColor(255, 255, 255), align=Qt.AlignmentFlag.AlignLeft, w=None):
        p.save()
        p.setOpacity(max(0.0, min(1.0, a)))
        p.setFont(_font(px, w or QFont.Weight.Black))
        p.setPen(color)
        p.drawText(rect, align | Qt.TextFlag.TextWordWrap, text)
        p.restore()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, self.W, self.H), 26, 26)
        p.setClipPath(clip)
        kind = self.slides[self.i]
        t = self.elapsed()
        self._bg(p, kind, t)
        getattr(self, "_s_" + kind)(p, t)
        n = len(self.slides)                                   # полоски прогресса сверху
        gap, x0, w = 4, 14, (self.W - 28 - 4 * (n - 1)) / n
        for j in range(n):
            r = QRectF(x0 + j * (w + gap), 14, w, 4)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 70))
            p.drawRoundedRect(r, 2, 2)
            f = 1.0 if j < self.i else (min(1.0, t / SLIDE_SEC) if j == self.i and kind != "summary" else
                                        (1.0 if j == self.i else 0.0))
            if f > 0:
                p.setBrush(QColor(255, 255, 255, 235))
                p.drawRoundedRect(QRectF(r.x(), r.y(), r.width() * f, 4), 2, 2)
        self._text(p, QRectF(22, 28, 300, 24), f"ECHOES · {self.d['year']}", 13, 0.85, w=QFont.Weight.Bold)
        p.end()

    def _in(self, t, delay, dur=0.6):
        return _ease((t - delay) / dur)

    def _s_intro(self, p, t):
        a = self._in(t, 0.1)
        self._text(p, QRectF(30, 250 - 30 * (1 - a), 390, 80), "Твой", 64, a)
        a2 = self._in(t, 0.45)
        self._text(p, QRectF(30, 320 - 30 * (1 - a2), 390, 90), f"{self.d['year']}", 96, a2, _accent())
        a3 = self._in(t, 0.8)
        self._text(p, QRectF(30, 430, 390, 120), "в ECHOES — что звучало, кого любили и когда слушали.", 22, a3,
                   w=QFont.Weight.DemiBold)
        a4 = self._in(t, 1.6)
        self._text(p, QRectF(30, 700, 390, 40), "нажимайте справа — дальше", 14, a4 * 0.7, w=QFont.Weight.Bold)

    def _s_minutes(self, p, t):
        m = self.d["minutes"]
        k = _ease(t / 2.2)
        self._text(p, QRectF(30, 200, 390, 40), "Вы слушали музыку", 26, self._in(t, 0.1))
        self._text(p, QRectF(30, 250, 390, 120), _human(int(m * k)), 92, self._in(t, 0.3), _accent())
        self._text(p, QRectF(30, 370, 390, 40), "минут", 34, self._in(t, 0.5))
        dd = m / 60 / 24
        line = f"Это {dd:.1f} суток музыки без перерыва" if dd >= 1 else f"Это {m / 60:.1f} часа музыки"
        self._text(p, QRectF(30, 470, 390, 80), line, 22, self._in(t, 1.4), w=QFont.Weight.DemiBold)
        self._text(p, QRectF(30, 560, 390, 80), f"Музыка звучала {self.d['days_active']} дней · "
                   f"{_human(self.d['plays'])} прослушиваний", 18, self._in(t, 1.9), QColor(255, 255, 255, 200),
                   w=QFont.Weight.DemiBold)

    def _s_artist(self, p, t):
        a, n, sec = self.d["top_artists"][0]
        self._text(p, QRectF(30, 120, 390, 40), "Исполнитель года", 26, self._in(t, 0.1))
        s = 280
        k = self._in(t, 0.3, 0.8)
        r = QRectF((self.W - s * (0.85 + 0.15 * k)) / 2, 190 + 20 * (1 - k), s * (0.85 + 0.15 * k), s * (0.85 + 0.15 * k))
        p.save()
        p.setOpacity(k)
        g = QRadialGradient(r.center(), r.width() * 0.75)
        g.setColorAt(0.6, QColor(0, 0, 0, 120))
        g.setColorAt(1, QColor(0, 0, 0, 0))
        p.setBrush(g)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(r.adjusted(-30, -20, 30, 40))
        self._draw(p, r, self._pic(self._artist_pic(a), 300, True))
        p.restore()
        self._text(p, QRectF(20, 500, 410, 70), a, 44, self._in(t, 0.9), align=Qt.AlignmentFlag.AlignHCenter)
        sub = f"{n} прослушиваний" + (f" · {int(sec // 60)} минут" if sec >= 60 else "")
        self._text(p, QRectF(20, 575, 410, 40), sub, 20, self._in(t, 1.2), QColor(255, 255, 255, 210),
                   align=Qt.AlignmentFlag.AlignHCenter, w=QFont.Weight.DemiBold)

    def _list(self, p, t, title, rows):
        self._text(p, QRectF(30, 110, 390, 50), title, 34, self._in(t, 0.1))
        for j, (pic, circle, name, sub) in enumerate(rows):
            a = self._in(t, 0.35 + j * 0.18)
            y = 200 + j * 108
            x = 30 + 40 * (1 - a)
            p.save()
            p.setOpacity(a)
            self._text(p, QRectF(x, y + 26, 40, 40), str(j + 1), 30, 1, _accent())
            self._draw(p, QRectF(x + 44, y + 8, 84, 84), self._pic(pic, 84, circle))
            fm = QFontMetrics(_font(22))
            self._text(p, QRectF(x + 142, y + 18, 260, 32), fm.elidedText(name, Qt.TextElideMode.ElideRight, 250), 22, 1)
            self._text(p, QRectF(x + 142, y + 52, 260, 26), sub, 15, 1, QColor(255, 255, 255, 190),
                       w=QFont.Weight.DemiBold)
            p.restore()

    def _s_artists(self, p, t):
        self._list(p, t, "Топ исполнителей", [(self._artist_pic(a), True, a, f"{n} прослушиваний")
                                              for a, n, _s in self.d["top_artists"][:5]])

    def _s_track(self, p, t):
        tr = self.d["top_tracks"][0]
        self._text(p, QRectF(30, 120, 390, 40), "Трек года", 26, self._in(t, 0.1))
        s = 300
        k = self._in(t, 0.3, 0.8)
        p.save()
        p.setOpacity(k)
        p.translate(self.W / 2, 340)
        p.rotate(-8 * (1 - k))
        r = QRectF(-s / 2, -s / 2, s, s)
        sh = QPainterPath()
        sh.addRoundedRect(r.adjusted(-6, 6, 6, 18), 20, 20)
        p.fillPath(sh, QColor(0, 0, 0, 110))
        self._draw(p, r, self._pic(tr.get("cover", ""), 300))
        p.restore()
        self._text(p, QRectF(20, 515, 410, 70), tr.get("title", ""), 36, self._in(t, 0.9),
                   align=Qt.AlignmentFlag.AlignHCenter)
        self._text(p, QRectF(20, 590, 410, 40), tr.get("artist", ""), 20, self._in(t, 1.1), QColor(255, 255, 255, 220),
                   align=Qt.AlignmentFlag.AlignHCenter, w=QFont.Weight.Bold)
        self._text(p, QRectF(20, 640, 410, 40), f"включали {tr.get('plays', 0)} раз", 18, self._in(t, 1.4),
                   _accent(), align=Qt.AlignmentFlag.AlignHCenter)

    def _s_tracks(self, p, t):
        self._list(p, t, "Топ треков", [(tr.get("cover", ""), False, tr.get("title", ""),
                                         f"{tr.get('artist', '')} · {tr.get('plays', 0)}×")
                                        for tr in self.d["top_tracks"][:5]])

    def _s_genre(self, p, t):
        self._text(p, QRectF(30, 200, 390, 40), "Жанр года", 26, self._in(t, 0.1))
        k = self._in(t, 0.35, 0.9)
        p.save()
        p.setOpacity(k)
        p.translate(self.W / 2, 360)
        p.scale(0.7 + 0.3 * k, 0.7 + 0.3 * k)
        self._text(p, QRectF(-210, -60, 420, 120), self.d["genre"].capitalize(), 58, 1, _accent(),
                   align=Qt.AlignmentFlag.AlignHCenter)
        p.restore()
        name, why = self.d["persona"]
        self._text(p, QRectF(30, 500, 390, 40), "А вы —", 22, self._in(t, 1.1), w=QFont.Weight.Bold)
        self._text(p, QRectF(30, 535, 390, 60), name, 40, self._in(t, 1.3))
        self._text(p, QRectF(30, 600, 390, 90), why, 18, self._in(t, 1.6), QColor(255, 255, 255, 210),
                   w=QFont.Weight.DemiBold)

    def _s_time(self, p, t):
        ph = self.d["peak_hour"]
        self._text(p, QRectF(30, 150, 390, 40), "Ваш час музыки", 26, self._in(t, 0.1))
        if ph is not None:
            self._text(p, QRectF(30, 200, 390, 110), f"{ph:02d}:00", 88, self._in(t, 0.3), _accent())
            k = self._in(t, 0.5, 1.2)                          # часы-циферблат: прослушивания по часам
            c = QPointF(self.W / 2, 420)
            p.save()
            p.setOpacity(k)
            p.setPen(QPen(QColor(255, 255, 255, 50), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, 70, 70)
            ang = (ph / 24) * 2 * math.pi - math.pi / 2
            p.setPen(QPen(_accent(), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(c, QPointF(c.x() + math.cos(ang) * 60 * k, c.y() + math.sin(ang) * 60 * k))
            p.restore()
        else:
            self._text(p, QRectF(30, 220, 390, 60), "каждый час — понемногу", 32, self._in(t, 0.3))
        bd = self.d["best_day"]
        if bd:
            y, m, dd = bd[0].split("-")
            self._text(p, QRectF(30, 530, 390, 40), "Самый музыкальный день", 22, self._in(t, 1.1), w=QFont.Weight.Bold)
            self._text(p, QRectF(30, 565, 390, 60), f"{int(dd)} {MONTHS[int(m) - 1]}", 44, self._in(t, 1.3))
            self._text(p, QRectF(30, 625, 390, 40), f"{int(bd[1] // 60)} минут за день", 20, self._in(t, 1.5),
                       _accent())

    def paint_summary(self, p, t=10.0):
        d = self.d
        self._text(p, QRectF(30, 60, 390, 60), f"Мой {d['year']}", 46, 1)
        self._text(p, QRectF(30, 118, 390, 30), "в ECHOES", 20, 1, _accent(), w=QFont.Weight.Bold)
        if d["top_artists"]:
            self._draw(p, QRectF(30, 170, 150, 150), self._pic(self._artist_pic(d["top_artists"][0][0]), 150, True))
        self._text(p, QRectF(200, 175, 230, 30), "МИНУТ", 14, 0.8, w=QFont.Weight.Bold)
        self._text(p, QRectF(200, 198, 230, 60), _human(d["minutes"]), 42, 1, _accent())
        self._text(p, QRectF(200, 255, 230, 30), "ЖАНР", 14, 0.8, w=QFont.Weight.Bold)
        self._text(p, QRectF(200, 276, 230, 40), (d["genre"] or "—").capitalize(), 24, 1)
        self._text(p, QRectF(30, 345, 190, 26), "ИСПОЛНИТЕЛИ", 14, 0.8, w=QFont.Weight.Bold)
        self._text(p, QRectF(235, 345, 190, 26), "ТРЕКИ", 14, 0.8, w=QFont.Weight.Bold)
        fm = QFontMetrics(_font(16, QFont.Weight.Bold))
        for j, (a, _n, _s) in enumerate(d["top_artists"][:5]):
            self._text(p, QRectF(30, 375 + j * 32, 195, 30), f"{j + 1}  " + fm.elidedText(a, Qt.TextElideMode.ElideRight,
                                                                                         165), 16, 1,
                       w=QFont.Weight.Bold)
        for j, tr in enumerate(d["top_tracks"][:5]):
            self._text(p, QRectF(235, 375 + j * 32, 195, 30),
                       f"{j + 1}  " + fm.elidedText(tr.get("title", ""), Qt.TextElideMode.ElideRight, 165), 16, 1,
                       w=QFont.Weight.Bold)
        name, _why = d["persona"]
        r = QRectF(30, 560, 390, 70)
        path = QPainterPath()
        path.addRoundedRect(r, 18, 18)
        p.fillPath(path, QColor(0, 0, 0, 90))
        self._text(p, r.adjusted(18, 10, -10, 0), "ВЫ — " + name.upper(), 20, 1)
        self._text(p, r.adjusted(18, 38, -10, 0), f"{_human(d['plays'])} прослушиваний · {d['artists_n']} исполнителей",
                   13, 0.85, w=QFont.Weight.DemiBold)

    def _s_summary(self, p, t):
        p.save()
        p.setOpacity(self._in(t, 0.05, 0.5))
        self.paint_summary(p, t)
        p.restore()

    def summary_image(self) -> QImage:
        img = QImage(self.W * 2, self.H * 2, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0)
        p = QPainter(img)
        p.scale(2, 2)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, self.W, self.H), 26, 26)
        p.setClipPath(clip)
        self._bg(p, "summary", 1.0)
        self.paint_summary(p)
        p.end()
        return img


class WrappedDialog(QDialog):
    def __init__(self, win, year=None):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Echoes Wrapped")
        self.setStyleSheet("QDialog{background:#050506;} QPushButton{background:rgba(255,255,255,0.12);color:#fff;"
                           "border:none;border-radius:18px;padding:10px 18px;font-weight:800;}"
                           "QPushButton:hover{background:rgba(255,255,255,0.2);}"
                           "QPushButton#Accent{background:#ffdb1a;color:#000;}")
        self.data = compute(win, year)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(12)
        self.card = StoryCard(self.data)
        self.card.on_change = self._changed
        v.addWidget(self.card, 0, Qt.AlignmentFlag.AlignHCenter)
        row = QHBoxLayout()
        row.addStretch(1)
        self.b_again = QPushButton("Сначала")
        self.b_again.clicked.connect(lambda: self.card.go(0))
        self.b_save = QPushButton("Сохранить картинку")
        self.b_save.clicked.connect(self.save_png)
        self.b_post = QPushButton("Выложить в Echoes.NET")
        self.b_post.setObjectName("Accent")
        self.b_post.clicked.connect(self.post)
        for b in (self.b_again, self.b_save, self.b_post):
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        self._changed(self.card.slides[0])
        self.adjustSize()

    def _changed(self, kind):
        last = kind == "summary"
        for b in (self.b_save, self.b_post):
            b.setVisible(last)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Right:
            self.card.go(self.card.i + 1)
        elif e.key() == Qt.Key.Key_Left:
            self.card.go(self.card.i - 1)
        else:
            super().keyPressEvent(e)

    def _png_path(self) -> Path:
        d = Path.home() / ".neon_player" / "wrapped"
        d.mkdir(parents=True, exist_ok=True)
        return d / f"echoes_wrapped_{self.data['year']}.png"

    def save_png(self):
        from PyQt6.QtWidgets import QFileDialog
        fn, _ = QFileDialog.getSaveFileName(self, "Сохранить итоги", str(Path.home() / "Pictures" /
                                                                        f"ECHOES {self.data['year']}.png"),
                                            "PNG (*.png)")
        if fn:
            self.card.summary_image().save(fn)

    def post(self):
        net = getattr(getattr(self.win, "_ya", None), "pages", {}).get("net")
        if net is None or not net.running():
            if net is not None:
                net.toast("Включите сеть, чтобы выложить итоги")
            return
        f = self._png_path()
        self.card.summary_image().save(str(f))
        d = self.data
        top = d["top_artists"][0][0] if d["top_artists"] else ""
        text = (f"Мой {d['year']} в ECHOES: {_human(d['minutes'])} минут музыки" +
                (f", исполнитель года — {top}" if top else "") + (f", жанр — {d['genre']}" if d["genre"] else "") + " 🎧")
        net.node.call(net.node.publish_files, text, [str(f)], None, "post")
        net.toast("Итоги года выложены")
        self.b_post.setEnabled(False)
        self.b_post.setText("Выложено")


def open_wrapped(win, year=None):
    d = WrappedDialog(win, year)
    d.exec()
