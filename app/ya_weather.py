# ya_weather.py
"""
Тема по реальной погоде за окном (Живое оформление → «Погода за окном», по умолчанию выключено).

Погода — с Open-Meteo (бесплатно, без ключей и регистрации): по названию города, раз в 30 минут.
Поверх окна — лёгкий прозрачный слой (отдельное окно-«стекло», не мешает кликам и не перерисовывает
интерфейс): снег — падающие хлопья, дождь — косые струи, гроза — дождь и редкие вспышки, солнце — тёплый
свет из угла, облачно/туман — дымка, ясная ночь — редкие мерцающие звёзды.
"""
from __future__ import annotations

import json
import math
import random
import threading
import time
import urllib.parse
import urllib.request

from PyQt6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPen, QRadialGradient
from PyQt6.QtWidgets import QWidget

KINDS = {"sun": "ясно", "night": "ясная ночь", "cloud": "облачно", "fog": "туман", "rain": "дождь",
         "snow": "снег", "storm": "гроза"}
UA = {"User-Agent": "ECHOES-player"}


def kind_of(code: int, is_day: bool = True) -> str:
    """Код погоды WMO → вид эффекта."""
    c = int(code)
    if c in (0, 1):
        return "sun" if is_day else "night"
    if c in (2, 3):
        return "cloud"
    if c in (45, 48):
        return "fog"
    if 71 <= c <= 77 or c in (85, 86):
        return "snow"
    if c >= 95:
        return "storm"
    if 51 <= c <= 67 or 80 <= c <= 82:
        return "rain"
    return "cloud"


def _get(url: str) -> dict:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=12) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch(city: str) -> dict | None:
    """{'city', 'kind', 'temp', 'code'} или None."""
    city = (city or "").strip()
    if not city:
        return None
    g = _get("https://geocoding-api.open-meteo.com/v1/search?count=1&language=ru&name=" + urllib.parse.quote(city))
    res = (g.get("results") or [None])[0]
    if not res:
        return {"error": "Город не найден"}
    f = _get("https://api.open-meteo.com/v1/forecast?current=weather_code,is_day,temperature_2m"
             f"&latitude={res['latitude']}&longitude={res['longitude']}")
    cur = f.get("current") or {}
    code = int(cur.get("weather_code") or 0)
    return {"city": res.get("name") or city, "kind": kind_of(code, bool(cur.get("is_day", 1))),
            "temp": cur.get("temperature_2m"), "code": code, "ts": time.time()}


def describe(w: dict | None) -> str:
    if not w:
        return "погода ещё не загружена"
    if w.get("error"):
        return w["error"]
    t = w.get("temp")
    ts = f", {round(t):+d}°".replace("+0", "0").replace("-", "−") if isinstance(t, (int, float)) else ""
    return f"{w.get('city', '')}: {KINDS.get(w.get('kind'), '')}{ts}"


# ══════════════════════════ слой поверх окна ══════════════════════════ #

class WeatherLayer(QWidget):
    """Прозрачное окно поверх главного (принадлежит ему): не ловит мышь, двигается вместе с ним."""

    def __init__(self, win):
        super().__init__(win, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowTransparentForInput | Qt.WindowType.WindowDoesNotAcceptFocus |
                         Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.win = win
        self.kind = ""
        self.parts = []
        self.flash = 0.0
        self.t0 = time.monotonic()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._step)
        win.installEventFilter(self)

    def eventFilter(self, obj, e):
        if obj is self.win and e.type() in (QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.Show,
                                            QEvent.Type.WindowStateChange, QEvent.Type.Hide):
            self._follow()
        return False

    def _follow(self):
        w = self.win
        if not self.kind or not w.isVisible() or w.isMinimized():
            self.hide()
            return
        g = w.geometry()
        if self.geometry() != g:
            self.setGeometry(g)
            self._seed()
        if not self.isVisible():
            self.show()

    def set_kind(self, kind: str):
        self.kind = kind or ""
        self._seed()
        fps = {"snow": 30, "rain": 33, "storm": 33, "fog": 10, "night": 8, "cloud": 10}.get(self.kind, 0)
        try:
            from ya_decor import _motion
            if _motion() <= 0:
                fps = 0                                        # «меньше движения» — картинка без анимации
        except Exception:                                      # noqa: BLE001
            pass
        if fps:
            self.timer.start(int(1000 / fps))
        else:
            self.timer.stop()
        self._follow()
        self.update()

    def _seed(self):
        w, h = max(1, self.width()), max(1, self.height())
        rng = random.Random()
        if self.kind == "snow":
            n = max(30, min(110, w * h // 18000))
            self.parts = [[rng.random() * w, rng.random() * h, 1.2 + rng.random() ** 2 * 2.8, 0.4 + rng.random() * 0.9,
                           rng.random() * 6.28] for _ in range(n)]
        elif self.kind in ("rain", "storm"):
            n = max(40, min(140, w * h // 12000))
            self.parts = [[rng.random() * w, rng.random() * h, 9 + rng.random() * 14, 9 + rng.random() * 7]
                          for _ in range(n)]
        elif self.kind == "night":
            self.parts = [[rng.random() * w, rng.random() * h * 0.35, 0.6 + rng.random() * 1.2, rng.random() * 6.28]
                          for _ in range(max(14, w // 60))]
        else:
            self.parts = []

    def _step(self):
        if not self.isVisible():
            return
        w, h = self.width(), self.height()
        if self.kind == "snow":
            t = time.monotonic()
            for p in self.parts:
                p[1] += p[3] * 1.3
                p[0] += math.sin(t * 0.8 + p[4]) * 0.45
                if p[1] > h + 5:
                    p[1], p[0] = -5, random.random() * w
        elif self.kind in ("rain", "storm"):
            for p in self.parts:
                p[1] += p[3]
                p[0] -= p[3] * 0.22
                if p[1] > h + 20:
                    p[1], p[0] = -20, random.random() * (w + 80)
            if self.kind == "storm":
                if self.flash > 0:
                    self.flash = max(0.0, self.flash - 0.12)
                elif random.random() < 0.004:
                    self.flash = 1.0
        self.update()

    def paintEvent(self, _):
        if not self.kind:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        t = time.monotonic() - self.t0
        k = self.kind
        if k == "snow":
            p.setPen(Qt.PenStyle.NoPen)
            for x, y, r, _s, _ph in self.parts:
                p.setBrush(QColor(255, 255, 255, int(70 + r * 28)))
                p.drawEllipse(QPointF(x, y), r, r)
        elif k in ("rain", "storm"):
            for x, y, L, _s in self.parts:
                p.setPen(QPen(QColor(190, 215, 255, 46), 1.1))
                p.drawLine(QPointF(x, y), QPointF(x - L * 0.22, y + L))
            if k == "storm" and self.flash > 0:
                p.fillRect(self.rect(), QColor(220, 230, 255, int(55 * self.flash)))
        elif k == "sun":
            g = QRadialGradient(QPointF(w * 0.92, -h * 0.08), max(w, h) * 0.7)
            g.setColorAt(0, QColor(255, 196, 90, 46))
            g.setColorAt(0.5, QColor(255, 170, 60, 14))
            g.setColorAt(1, QColor(255, 170, 60, 0))
            p.fillRect(self.rect(), g)
        elif k in ("cloud", "fog"):
            a = 30 if k == "fog" else 16
            for i in range(3):
                y = h * (0.15 + 0.3 * i) + math.sin(t * 0.15 + i) * 18
                g = QLinearGradient(0, y - 90, 0, y + 90)
                g.setColorAt(0, QColor(200, 205, 215, 0))
                g.setColorAt(0.5, QColor(200, 205, 215, a))
                g.setColorAt(1, QColor(200, 205, 215, 0))
                p.fillRect(QRectF(0, y - 90, w, 180), g)
        elif k == "night":
            g = QLinearGradient(0, 0, 0, h * 0.5)
            g.setColorAt(0, QColor(40, 60, 140, 34))
            g.setColorAt(1, QColor(40, 60, 140, 0))
            p.fillRect(QRectF(0, 0, w, h * 0.5), g)
            p.setPen(Qt.PenStyle.NoPen)
            for x, y, r, ph in self.parts:
                p.setBrush(QColor(255, 255, 255, int(60 + 70 * (0.5 + 0.5 * math.sin(t * 1.3 + ph)))))
                p.drawEllipse(QPointF(x, y), r, r)
        p.end()


class Controller(QObject):
    """Раз в 30 минут — погода по городу из настроек; меняет слой."""
    got = pyqtSignal(object)

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.layer = None
        self.last = None
        self.got.connect(self._apply)
        self.t = QTimer(self)
        self.t.setInterval(30 * 60000)
        self.t.timeout.connect(self.refresh)

    def cfg(self):
        import ya_decor
        c = ya_decor.cfg()
        return bool(c.get("real_weather")), str(c.get("weather_city") or "").strip()

    def sync(self):
        on, city = self.cfg()
        if not on or not city:
            self.t.stop()
            if self.layer is not None:
                self.layer.set_kind("")
            return
        if not self.t.isActive():
            self.t.start()
        if self.last and self.last.get("q") == city and time.time() - self.last.get("ts", 0) < 1800 \
                and not self.last.get("error"):
            self._apply(self.last)
        else:
            self.refresh()

    def refresh(self):
        on, city = self.cfg()
        if not on or not city:
            return

        def run():
            try:
                w = fetch(city)
            except Exception as e:                             # noqa: BLE001
                w = {"error": f"Нет связи с сервисом погоды ({type(e).__name__})"}
            if w is not None:
                w["q"] = city
                w.setdefault("ts", time.time())
            try:
                self.got.emit(w)
            except RuntimeError:
                pass
        threading.Thread(target=run, daemon=True).start()

    def _apply(self, w):
        self.last = w
        on, _city = self.cfg()
        kind = (w or {}).get("kind", "") if on and not (w or {}).get("error") else ""
        if self.layer is None and kind:
            self.layer = WeatherLayer(self.win)
        if self.layer is not None:
            self.layer.set_kind(kind)


def install(win) -> Controller:
    c = getattr(win, "_weather_ctl", None)
    if c is None:
        c = Controller(win)
        win._weather_ctl = c
    c.sync()
    return c
