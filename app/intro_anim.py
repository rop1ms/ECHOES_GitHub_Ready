"""Заставка при запуске ECHOES: из тьмы загорается солнце, лучи по очереди вылетают из него
(с искрами), солнце вспыхивает — и из темноты буква за буквой выходит надпись ECHOES с бликом.

play(win) — поверх окна (дочерний виджет), ~1.8 с; клик или любая клавиша — пропустить.
Время считается по часам, а не по кадрам: если плеер в эти секунды занят, анимация не «тормозит», а пропускает кадры.
"""
from __future__ import annotations

import math

from PyQt6.QtCore import Qt, QTimer, QElapsedTimer, QPointF, QRectF, QEvent, QObject
from PyQt6.QtGui import (QColor, QPainter, QPainterPath, QRadialGradient, QLinearGradient, QFont, QFontMetricsF,
                         QPen)
from PyQt6.QtWidgets import QWidget

RAYS = 16
T_CORE = 0.0
T_RAYS = 0.16                       # первый луч
RAY_STEP = 0.034                    # следующий — через столько
RAY_GROW = 0.28
T_TEXT = 0.72
LETTER_STEP = 0.05
T_SWEEP = 1.08
T_OUT = 1.42
T_END = 1.80
T_FLASH = T_RAYS + RAYS * RAY_STEP + 0.12   # вспышка — когда вылетел последний луч
TEXT = "ECHOES"


def _clamp(x, a=0.0, b=1.0):
    return a if x < a else b if x > b else x


def _out_cubic(x):
    x = _clamp(x)
    return 1 - (1 - x) ** 3


def _out_back(x, s=2.2):
    x = _clamp(x) - 1
    return x * x * ((s + 1) * x + s) + 1


def _in_out(x):
    x = _clamp(x)
    return 3 * x * x - 2 * x * x * x


def _accent() -> QColor:
    try:
        import yamusic_theme
        return QColor(yamusic_theme.YELLOW)
    except Exception:                                          # noqa: BLE001
        return QColor("#ffdb1a")


def _font(px) -> QFont:
    try:
        from yamusic_theme import ya_font
        f = ya_font(px, QFont.Weight.Black)
    except Exception:                                          # noqa: BLE001
        f = QFont("Segoe UI", -1, QFont.Weight.Black)
        f.setPixelSize(px)
    return f


class IntroOverlay(QWidget):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.acc = _accent()
        self.clock = QElapsedTimer()
        self.skip_at = None
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(15)
        self.timer.timeout.connect(self._tick)
        self.big = _font(66)
        win.installEventFilter(self)
        self.setGeometry(win.rect())
        self.show()                                            # чёрный кадр сразу — интерфейс не мелькает
        self.raise_()
        self._n = 0

    def _tick(self):
        self._n += 1
        if self._n % 8 == 0:
            self.raise_()                                      # плеер мог создать новые панели поверх
        self.update()

    def start(self):
        self.setGeometry(self.win.rect())
        self.show()
        self.raise_()
        self.setFocus()
        self.clock.start()
        self.timer.start()

    def now(self) -> float:
        t = self.clock.elapsed() / 1000.0 if self.clock.isValid() else 0.0
        if self.skip_at is not None:                           # пропуск — сразу к растворению
            t = T_OUT + (t - self.skip_at) * 1.6
        return t

    def skip(self):
        if self.skip_at is None and self.clock.isValid():
            t = self.clock.elapsed() / 1000.0
            if t < T_OUT:
                self.skip_at = t

    def eventFilter(self, obj, ev):
        if obj is self.win and ev.type() == QEvent.Type.Resize:
            self.setGeometry(self.win.rect())
        return False

    def mousePressEvent(self, e):
        self.skip()

    def keyPressEvent(self, e):
        self.skip()

    def finish(self):
        self.timer.stop()
        try:
            self.win.removeEventFilter(self)
        except RuntimeError:
            pass
        self.hide()
        self.deleteLater()

    # ── рисование ── #

    def paintEvent(self, _):
        t = self.now()
        if t >= T_END:
            QTimer.singleShot(0, self.finish)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        out = _in_out((t - T_OUT) / (T_END - T_OUT))           # 0 → 1 при растворении
        alpha = 1.0 - out
        p.fillRect(self.rect(), QColor(0, 0, 0, int(255 * alpha)))
        if alpha <= 0.01:
            p.end()
            return
        p.setOpacity(alpha)
        cx, cy = W / 2.0, H / 2.0 - 40
        zoom = 1.0 + 0.10 * out                                # на выходе — лёгкий «наезд»
        p.translate(cx, cy + 40)
        p.scale(zoom, zoom)
        p.translate(-cx, -(cy + 40))
        self._glow(p, cx, cy, t)
        self._sun(p, cx, cy, t)
        self._text(p, cx, cy + 182, t)
        p.end()

    def _glow(self, p, cx, cy, t):
        k = _out_cubic(t / 0.8)
        pulse = math.exp(-((t - T_FLASH) / 0.16) ** 2)            # вспышка, когда вылетел последний луч
        r = 120 + 260 * k + 60 * pulse
        g = QRadialGradient(QPointF(cx, cy), r)
        c0 = QColor(self.acc); c0.setAlpha(int(70 * k + 70 * pulse))
        c1 = QColor(self.acc); c1.setAlpha(int(18 * k))
        c2 = QColor(self.acc); c2.setAlpha(0)
        g.setColorAt(0, c0)
        g.setColorAt(0.35, c1)
        g.setColorAt(1, c2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawEllipse(QPointF(cx, cy), r, r)

    def _sun(self, p, cx, cy, t):
        core = _out_back((t - T_CORE) / 0.45, 1.8)
        pulse = math.exp(-((t - T_FLASH) / 0.12) ** 2)
        R = 34 * core * (1 + 0.10 * pulse)
        rot = 30 * _out_cubic(t / 1.0) + 6 * t                 # солнце поворачивается, пока раскрывается
        p.save()
        p.translate(cx, cy)
        p.rotate(rot)
        hot = QColor(self.acc).lighter(135)
        for i in range(RAYS):
            t0 = T_RAYS + i * RAY_STEP
            k = (t - t0) / RAY_GROW
            if k <= 0:
                continue
            ang = 360.0 * i / RAYS - 90
            long_ = i % 2 == 0
            L = (88 if long_ else 60) * (1 + 0.06 * pulse)
            grow = _out_back(k, 2.6)
            p.save()
            p.rotate(ang)
            base = R * 0.82
            tip = base + L * grow
            w = (11 if long_ else 8) * min(1.0, grow + 0.2)
            path = QPainterPath()                              # заострённый луч, как на логотипе
            path.moveTo(base, -w / 2)
            path.lineTo(tip, 0)
            path.lineTo(base, w / 2)
            path.closeSubpath()
            lg = QLinearGradient(QPointF(base, 0), QPointF(tip, 0))
            lg.setColorAt(0, hot)
            lg.setColorAt(1, self.acc)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(lg)
            p.drawPath(path)
            # искра: вылетает из кончика луча и гаснет
            sk = (t - t0) / 0.55
            if 0 < sk < 1:
                d = base + L + 120 * _out_cubic(sk)
                a = 1 - sk
                sc = QColor(hot); sc.setAlpha(int(230 * a))
                trail = QLinearGradient(QPointF(d - 46 * a, 0), QPointF(d, 0))
                tc = QColor(self.acc); tc.setAlpha(0)
                trail.setColorAt(0, tc)
                trail.setColorAt(1, sc)
                p.setPen(QPen(trail, 2.2 * a + 0.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawLine(QPointF(d - 46 * a, 0), QPointF(d, 0))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(sc)
                p.drawEllipse(QPointF(d, 0), 2.6 * a + 0.6, 2.6 * a + 0.6)
            p.restore()
        if R > 0.5:
            g = QRadialGradient(QPointF(-R * 0.3, -R * 0.3), R * 1.4)
            g.setColorAt(0, QColor(255, 255, 245))
            g.setColorAt(0.35, hot)
            g.setColorAt(1, self.acc)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawEllipse(QPointF(0, 0), R, R)
        ring = (t - 0.12) / 0.7                                # ударная волна от загорания
        if 0 < ring < 1:
            rc = QColor(self.acc); rc.setAlpha(int(150 * (1 - ring)))
            p.setPen(QPen(rc, 2.0 * (1 - ring) + 0.3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            rr = 30 + 190 * _out_cubic(ring)
            p.drawEllipse(QPointF(0, 0), rr, rr)
        p.restore()

    def _text(self, p, cx, base_y, t):
        if t < T_TEXT:
            return
        f = QFont(self.big)
        fm = QFontMetricsF(f)
        spread = 1 - _out_cubic((t - T_TEXT) / 0.55)            # буквы сходятся
        gap = 6 + 30 * spread
        widths = [fm.horizontalAdvance(ch) for ch in TEXT]
        total = sum(widths) + gap * (len(TEXT) - 1)
        x = cx - total / 2
        p.setFont(f)
        sweep = (t - T_SWEEP) / 0.38
        for j, ch in enumerate(TEXT):
            k = _out_cubic((t - T_TEXT - j * LETTER_STEP) / 0.32)
            if k <= 0:
                x += widths[j] + gap
                continue
            dy = 22 * (1 - k)
            v = int(40 + 215 * k)                              # из темноты — к белому
            col = QColor(v, v, v)
            col.setAlpha(int(255 * min(1.0, k * 1.3)))
            p.setPen(col)
            p.drawText(QPointF(x, base_y + dy), ch)
            x += widths[j] + gap
        if 0 < sweep < 1:                                      # блик пробегает по надписи
            band = QLinearGradient(QPointF(cx - total / 2 - 80 + (total + 160) * sweep - 60, 0),
                                   QPointF(cx - total / 2 - 80 + (total + 160) * sweep + 60, 0))
            c0 = QColor(self.acc); c0.setAlpha(0)
            c1 = QColor(self.acc).lighter(140); c1.setAlpha(255)
            band.setColorAt(0, c0)
            band.setColorAt(0.5, c1)
            band.setColorAt(1, c0)
            path = QPainterPath()
            x = cx - total / 2
            for j, ch in enumerate(TEXT):
                path.addText(QPointF(x, base_y), f, ch)
                x += widths[j] + gap
            p.save()
            p.setClipPath(path)
            p.fillRect(QRectF(cx - total / 2 - 10, base_y - fm.ascent() - 10, total + 20, fm.height() + 20), band)
            p.restore()
        # тонкая полоска акцента под надписью
        u = _out_cubic((t - (T_TEXT + 0.28)) / 0.4)
        if u > 0:
            uc = QColor(self.acc); uc.setAlpha(int(200 * u))
            w = 120 * u
            p.fillRect(QRectF(cx - w / 2, base_y + 22, w, 3), uc)


# ── заставка в своём потоке (echoes_ui.dll: intro_win.cpp) ── #

def start_native() -> bool:
    """Заставка в своём окне и потоке — сразу после QApplication, ещё до сборки окна плеера: она не
    дёргается, пока плеер строит окно и загружает всё заранее (warmup.preload). False — нет DLL."""
    try:
        import native_ui
        if not native_ui.available() or not hasattr(native_ui._lib, "eui_intro_start"):
            return False
        from PyQt6.QtGui import QGuiApplication
        scr = QGuiApplication.primaryScreen()
        ag = scr.availableGeometry()
        dpr = scr.devicePixelRatio()
        w, h = min(1120, ag.width()), min(720, ag.height())     # там, где откроется окно плеера
        x, y = ag.x() + (ag.width() - w) // 2, ag.y() + (ag.height() - h) // 2
        acc = _accent()
        fam = _font(66).family()
        ok = native_ui._lib.eui_intro_start(int(x * dpr), int(y * dpr), int(w * dpr), int(h * dpr), float(dpr),
                                            acc.red(), acc.green(), acc.blue(), TEXT, fam)
        return bool(ok)
    except Exception as e:                                     # noqa: BLE001
        print("[intro] native:", e)
        return False


def follow(win):
    """Окно плеера показано — заставка встаёт ровно поверх него."""
    try:
        import native_ui
        native_ui._lib.eui_intro_follow(int(win.winId()))
    except Exception as e:                                     # noqa: BLE001
        print("[intro] follow:", e)


def ready():
    """Плеер всё загрузил — заставка досматривает сюжет и растворяется."""
    try:
        import native_ui
        native_ui._lib.eui_intro_ready()
    except Exception as e:                                     # noqa: BLE001
        print("[intro] ready:", e)


def enabled(settings) -> bool:
    return bool((settings or {}).get("intro_anim", True))


def play(win) -> IntroOverlay | None:
    """Показать заставку поверх окна (вызывать сразу после win.show())."""
    try:
        ov = IntroOverlay(win)
        QTimer.singleShot(0, ov.start)
        return ov
    except Exception as e:                                     # noqa: BLE001
        print("[intro]", e)
        return None
