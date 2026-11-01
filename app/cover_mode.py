# cover_mode.py
"""
Режим «Обложка» — для всех тем.

Поверх всего окна: по центру просто обложка (квадрат с чуть скруглёнными
углами, без винила и эффектов), под ней полоска длительности и кнопки
«назад / пауза / вперёд». Фон — размытая и притемнённая та же обложка.
Кнопка выхода появляется на обложке при наведении (крестик в углу);
ещё выйти можно по Esc (F11 — полный экран).

Всё рисуется вручную одним виджетом — без дочерних кнопок и эффектов
прозрачности, поэтому дёшево и плавно.
"""
from __future__ import annotations

import math
import time

from img_load import load_pixmap
from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF, QVariantAnimation, QEasingCurve, pyqtSignal
from frameclock import FrameTimer
from PyQt6.QtGui import (QColor, QPainter, QPainterPath, QPixmap, QFont, QLinearGradient,
                         QRadialGradient, QPen, QImage)
from PyQt6.QtWidgets import QWidget


def _fmt(sec: float) -> str:
    sec = max(0, int(sec))
    return f"{sec // 60}:{sec % 60:02d}"


class CoverMode(QWidget):
    """state_fn() → dict(cover, title, artist, pos, dur, playing).
    Действия: on_prev(), on_toggle(), on_next(), on_seek(sec)."""

    closed = pyqtSignal()

    def __init__(self, parent, state_fn, on_prev, on_toggle, on_next, on_seek):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._state_fn = state_fn
        self._on_prev, self._on_toggle, self._on_next, self._on_seek = on_prev, on_toggle, on_next, on_seek
        self._cover_path = None
        self._cover: QPixmap | None = None
        self._bg: QPixmap | None = None
        self._bg_size = None
        self._st = {}
        self._op = 0.0
        self._hover = None             # "cover" / "close" / "prev" / "play" / "next" / "bar"
        self._cover_hover = 0.0
        self._drag = None              # доля при перетаскивании ползунка
        self._anim = None
        self.winamp = False            # стиль Winamp (ставит MainWindow в теме Winamp)
        self._t0 = time.monotonic()
        self._timer = FrameTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        self.hide()

    # ── открытие / закрытие ── #

    def open(self):
        p = self.parentWidget()
        if p is not None:
            self.setGeometry(p.rect())
        self._refresh()
        self.show()
        self.raise_()
        self.setFocus()
        self._fade(1.0)
        self._timer.start()

    def close_mode(self):
        self._fade(0.0, done=self._finish_close)

    def is_open(self) -> bool:
        return self.isVisible() and self._op > 0.0

    def _finish_close(self):
        self.hide()
        self._timer.stop()
        self.closed.emit()

    def _fade(self, to, done=None):
        if self._anim is not None:
            self._anim.stop()
        a = QVariantAnimation(self)
        a.setDuration(280)
        a.setStartValue(float(self._op))
        a.setEndValue(float(to))
        a.setEasingCurve(QEasingCurve.Type.OutCubic)

        def step(v):
            self._op = float(v)
            self._set_opaque()
            self.update()
        a.valueChanged.connect(step)
        if done:
            a.finished.connect(done)
        a.start()
        self._anim = a

    # ── данные ── #

    def _refresh(self):
        try:
            st = self._state_fn() or {}
        except Exception:                            # noqa: BLE001
            st = {}
        self._st = st
        path = st.get("cover") or ""
        if path != self._cover_path:
            self._cover_path = path
            pm = load_pixmap(path, 2048) if path else QPixmap()
            self._cover = pm if not pm.isNull() else None
            self._bg = None
        if self._bg is None or self._bg_size != (self.width(), self.height()):
            self._bg = self._make_bg()
            self._bg_size = (self.width(), self.height())

    def _make_bg(self) -> QPixmap:
        W, H = max(1, self.width()), max(1, self.height())
        pm = QPixmap(W, H)
        pm.fill(QColor(12, 12, 14))
        if self._cover is None:
            return pm
        # гауссово размытие (раньше — 14×14, растянутые обратно: квадраты и ступеньки)
        import blur_fx
        big = blur_fx.blurred_cover(self._cover, W, H, max(W, H) / 26.0)
        p = QPainter(pm)
        p.drawImage(0, 0, big)
        p.fillRect(pm.rect(), QColor(0, 0, 0, 150))
        g = QRadialGradient(QPointF(W / 2, H / 2), max(W, H) * 0.75)
        g.setColorAt(0.0, QColor(0, 0, 0, 0))
        g.setColorAt(1.0, QColor(0, 0, 0, 170))
        p.fillRect(pm.rect(), g)
        p.end()
        return pm

    def _set_opaque(self):
        """Открыт полностью (и не в стиле Winamp) — фон сплошной: окно под режимом не перерисовывать."""
        on = self._op >= 0.999 and not self.winamp
        if on != self.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent):
            self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, on)

    def _tick(self):
        self._refresh()
        want = 1.0 if self._hover in ("cover", "close") else 0.0
        d = want - self._cover_hover
        self._cover_hover = want if abs(d) < 0.01 else self._cover_hover + d * 0.25
        # перерисовка — только если что-то изменилось (раньше — весь экран 30 раз в секунду);
        # движется лишь полоска времени — обновляется только её полоса
        st = self._st
        sig = (st.get("playing"), st.get("title"), st.get("artist"), self._cover_path, round(self._cover_hover, 2),
               self._hover, self._drag, self.width(), self.height())
        try:
            _c, bar, *_ = self._layout()
            dur = float(st.get("dur") or 0)
            px = int(bar.width() * float(st.get("pos") or 0) / dur) if dur > 0 else 0
            sec = int(float(st.get("pos") or 0))
        except Exception:                            # noqa: BLE001
            bar, px, sec = None, 0, 0
        if sig != getattr(self, "_sig", None):
            self._sig = sig
            self._bar_sig = (px, sec)
            self.update()
        elif bar is not None and (px, sec) != getattr(self, "_bar_sig", None):
            self._bar_sig = (px, sec)
            self.update(bar.adjusted(-90, -14, 90, 30).toAlignedRect())

    # ── геометрия ── #

    def _layout(self):
        W, H = self.width(), self.height()
        size = int(max(160, min(W * 0.46, H - 230)))
        x = (W - size) / 2
        y = max(40, (H - size - 150) / 2)
        cover = QRectF(x, y, size, size)
        bar_w = size * 1.15
        bar = QRectF((W - bar_w) / 2, cover.bottom() + 44, bar_w, 6)
        cy = bar.bottom() + 52
        play = QRectF(W / 2 - 30, cy - 30, 60, 60)
        prev = QRectF(play.left() - 70, cy - 22, 44, 44)
        nxt = QRectF(play.right() + 26, cy - 22, 44, 44)
        close = QRectF(cover.right() - 50, cover.top() + 12, 38, 38)
        if self.winamp:
            fr = self._wa_frame(cover, bar, play)
            close = QRectF(fr.right() - 22, fr.top() + 5, 14, 12)
        return cover, bar, play, prev, nxt, close

    def _hit(self, pos):
        cover, bar, play, prev, nxt, close = self._layout()
        if close.contains(pos) and (self.winamp or self._cover_hover > 0.3):
            return "close"
        if cover.contains(pos):
            return "cover"
        if bar.adjusted(-4, -12, 4, 12).contains(pos):
            return "bar"
        for name, r in (("play", play), ("prev", prev), ("next", nxt)):
            if r.contains(pos):
                return name
        return None

    # ── ввод ── #

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self._drag is not None:
            _, bar, *_ = self._layout()
            self._drag = max(0.0, min(1.0, (pos.x() - bar.left()) / bar.width()))
            self.update()
            return
        h = self._hit(pos)
        if h != self._hover:
            self._hover = h
            self.setCursor(Qt.CursorShape.PointingHandCursor if h in ("close", "bar", "play", "prev", "next")
                           else Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, e):
        self._hover = None
        self.update()

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        h = self._hit(e.position())
        if h == "close":
            self.close_mode()
        elif h == "bar":
            _, bar, *_ = self._layout()
            self._drag = max(0.0, min(1.0, (e.position().x() - bar.left()) / bar.width()))
        elif h == "play":
            self._on_toggle()
        elif h == "prev":
            self._on_prev()
        elif h == "next":
            self._on_next()
        e.accept()

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            dur = float(self._st.get("dur") or 0)
            if dur > 0:
                self._on_seek(self._drag * dur)
            self._drag = None
            self.update()

    def mouseDoubleClickEvent(self, e):
        if self._hit(e.position()) == "cover":
            self._on_toggle()

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key.Key_Escape:
            self.close_mode()
        elif k == Qt.Key.Key_F11:
            fs = getattr(self.window(), "_toggle_fullscreen", None)
            if fs:
                fs()                                     # F11 — полный экран, режим обложки остаётся
        elif k == Qt.Key.Key_Space:
            self._on_toggle()
        elif k == Qt.Key.Key_Right:
            self._on_next()
        elif k == Qt.Key.Key_Left:
            self._on_prev()
        else:
            super().keyPressEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._bg = None

    # ── рисование ── #

    # ── стиль Winamp ── #

    @staticmethod
    def _wa_frame(cover, bar, play) -> QRectF:
        m = max(26.0, (bar.width() - cover.width()) / 2 + 16)
        return QRectF(cover.left() - m, cover.top() - 34, cover.width() + 2 * m, play.bottom() + 22 - (cover.top() - 34))

    @staticmethod
    def _bevel(p, r, raised=True, face=None):
        """Объёмная рамка в духе Winamp 2: светлый верх-лево, тёмный низ-право."""
        if face is not None:
            p.fillRect(r, face)
        hi, lo = (QColor(255, 255, 255, 170), QColor(0, 0, 0, 200)) if raised else \
                 (QColor(0, 0, 0, 200), QColor(255, 255, 255, 110))
        p.setPen(QPen(hi, 1))
        p.drawLine(r.topLeft(), r.topRight())
        p.drawLine(r.topLeft(), r.bottomLeft())
        p.setPen(QPen(lo, 1))
        p.drawLine(r.bottomLeft(), r.bottomRight())
        p.drawLine(r.topRight(), r.bottomRight())

    def _paint_winamp(self, p):
        W, H = self.width(), self.height()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        # «рабочий стол» за окном: размытая обложка, притемнённая в синеву
        if self._bg is not None:
            p.drawPixmap(0, 0, self._bg)
        p.fillRect(self.rect(), QColor(20, 40, 80, 140))
        cover, bar, play, prev, nxt, close = self._layout()
        fr = self._wa_frame(cover, bar, play)
        # корпус окна
        body = QLinearGradient(fr.topLeft(), fr.bottomLeft())
        body.setColorAt(0, QColor("#3b3b52")); body.setColorAt(1, QColor("#20202e"))
        p.fillRect(fr, body)
        self._bevel(p, fr, True)
        # заголовок: золотые полоски + название
        tb = QRectF(fr.left() + 4, fr.top() + 4, fr.width() - 8, 14)
        for i in range(4):
            y = tb.top() + 2 + i * 3
            p.setPen(QPen(QColor("#c8b46a") if i % 2 == 0 else QColor("#6a5c2a"), 1))
            p.drawLine(QPointF(tb.left() + 4, y), QPointF(tb.right() - 30, y))
        title = "ECHOES — ОБЛОЖКА"
        f = QFont(); f.setPixelSize(10); f.setBold(True); f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.5)
        p.setFont(f)
        tw = p.fontMetrics().horizontalAdvance(title) + 16
        tr = QRectF(fr.center().x() - tw / 2, tb.top(), tw, tb.height())
        p.fillRect(tr, QColor("#2b2b3c"))
        p.setPen(QColor("#e8e8f4"))
        p.drawText(tr, Qt.AlignmentFlag.AlignCenter, title)
        on = self._hover == "close"
        self._bevel(p, close, not on, QColor("#b8b8c8") if not on else QColor("#8a8aa0"))
        p.setPen(QPen(QColor("#10101a"), 1.6))
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        c = close.center()
        p.drawLine(QPointF(c.x() - 3, c.y() - 3), QPointF(c.x() + 3, c.y() + 3))
        p.drawLine(QPointF(c.x() + 3, c.y() - 3), QPointF(c.x() - 3, c.y() + 3))
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        # обложка в утопленной рамке
        p.fillRect(cover.adjusted(-3, -3, 3, 3), QColor("#000"))
        self._bevel(p, cover.adjusted(-4, -4, 4, 4), False)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        if self._cover is not None:
            src = self._cover
            sd = min(src.width(), src.height())
            p.drawPixmap(cover, src, QRectF((src.width() - sd) / 2, (src.height() - sd) / 2, sd, sd))
        else:
            p.fillRect(cover, QColor("#05050a"))
            p.setPen(QColor("#00a000"))
            f2 = QFont("Courier New"); f2.setPixelSize(int(cover.width() * 0.06)); f2.setBold(True)
            p.setFont(f2)
            p.drawText(cover, Qt.AlignmentFlag.AlignCenter, "НЕТ ОБЛОЖКИ")
        # ЖК-дисплей: время + бегущая строка
        lcd = QRectF(bar.left(), cover.bottom() + 8, bar.width(), 30)
        p.fillRect(lcd, QColor("#000"))
        self._bevel(p, lcd, False)
        dur = float(self._st.get("dur") or 0)
        pos = float(self._st.get("pos") or 0)
        frac = self._drag if self._drag is not None else (pos / dur if dur > 0 else 0.0)
        frac = max(0.0, min(1.0, frac))
        shown = frac * dur if self._drag is not None else pos
        playing = bool(self._st.get("playing"))
        fl = QFont("Courier New"); fl.setPixelSize(20); fl.setBold(True)
        p.setFont(fl)
        blink = playing or int((time.monotonic() - self._t0) * 2) % 2 == 0
        p.setPen(QColor("#00e000") if blink else QColor("#004400"))
        tw_ = QRectF(lcd.left() + 8, lcd.top(), 78, lcd.height())
        p.drawText(tw_, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, _fmt(shown).rjust(5))
        fm_ = QFont("Courier New"); fm_.setPixelSize(13); fm_.setBold(True)
        p.setFont(fm_)
        artist, ttl = self._st.get("artist") or "", self._st.get("title") or ""
        marquee = f"{artist + ' - ' if artist else ''}{ttl}  ({_fmt(dur)})   ***   "
        mr = QRectF(tw_.right() + 6, lcd.top() + 2, lcd.right() - tw_.right() - 12, lcd.height() - 4)
        fm = p.fontMetrics()
        mw = fm.horizontalAdvance(marquee)
        p.save()
        p.setClipRect(mr)
        p.setPen(QColor("#00e000"))
        off = ((time.monotonic() - self._t0) * 38) % max(1, mw) if mw > mr.width() else 0
        x = mr.left() - off
        while x < mr.right():
            p.drawText(QRectF(x, mr.top(), mw, mr.height()), Qt.AlignmentFlag.AlignVCenter, marquee)
            x += mw
            if mw <= mr.width():
                break
        p.restore()
        # полоска перемотки: желобок + выпуклый ползунок
        groove = QRectF(bar.left(), bar.center().y() - 4, bar.width(), 8)
        p.fillRect(groove, QColor("#0c0c14"))
        self._bevel(p, groove, False)
        fill = QRectF(groove.left() + 1, groove.top() + 1, (groove.width() - 2) * frac, groove.height() - 2)
        g = QLinearGradient(fill.topLeft(), fill.topRight())
        g.setColorAt(0, QColor("#1e8f1e")); g.setColorAt(1, QColor("#d8d020"))
        p.fillRect(fill, g)
        th = QRectF(groove.left() + (groove.width() - 28) * frac, groove.top() - 4, 28, 16)
        hot = self._hover == "bar" or self._drag is not None
        tg = QLinearGradient(th.topLeft(), th.bottomLeft())
        tg.setColorAt(0, QColor("#f0f0f8" if hot else "#e0e0ea")); tg.setColorAt(1, QColor("#8a8aa0"))
        p.fillRect(th, tg)
        self._bevel(p, th, True)
        # кнопки транспорта
        for name, r in (("prev", prev), ("play", play), ("next", nxt)):
            down = self._hover == name
            bg = QLinearGradient(r.topLeft(), r.bottomLeft())
            bg.setColorAt(0, QColor("#e6e6ee") if not down else QColor("#a8a8bc"))
            bg.setColorAt(1, QColor("#8a8aa0") if not down else QColor("#6c6c82"))
            p.fillRect(r, bg)
            self._bevel(p, r, not down)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#10101a"))

        def tri(cx, cy, s, left):
            d = -1 if left else 1
            path = QPainterPath()
            path.moveTo(cx - d * s * 0.5, cy - s * 0.6)
            path.lineTo(cx + d * s * 0.6, cy)
            path.lineTo(cx - d * s * 0.5, cy + s * 0.6)
            path.closeSubpath()
            p.drawPath(path)
        s_ = prev.height() * 0.3
        for r, left in ((prev, True), (nxt, False)):
            c = r.center()
            tri(c.x() - s_ * 0.55, c.y(), s_, left)
            tri(c.x() + s_ * 0.55, c.y(), s_, left)
        c = play.center()
        sp = play.height() * 0.32
        if playing:
            p.drawRect(QRectF(c.x() - sp * 0.75, c.y() - sp, sp * 0.5, sp * 2))
            p.drawRect(QRectF(c.x() + sp * 0.25, c.y() - sp, sp * 0.5, sp * 2))
        else:
            tri(c.x() + sp * 0.1, c.y(), sp * 1.6, False)

    def paintEvent(self, _):
        if self._op <= 0.0:
            return
        p = QPainter(self)
        if self.winamp:
            p.setOpacity(self._op)
            if self._bg is None:
                self._bg = self._make_bg()
            self._paint_winamp(p)
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setOpacity(self._op)
        if self._bg is None:
            self._bg = self._make_bg()
        p.drawPixmap(0, 0, self._bg)
        cover, bar, play, prev, nxt, close = self._layout()

        # обложка: мягкая тень + скруглённый квадрат
        rad = cover.width() * 0.035
        for i, a in ((26, 18), (16, 30), (8, 46)):
            sh = QPainterPath()
            sh.addRoundedRect(cover.adjusted(-i * 0.3, i * 0.2, i * 0.3, i * 0.9), rad + i * 0.4, rad + i * 0.4)
            p.fillPath(sh, QColor(0, 0, 0, a))
        path = QPainterPath()
        path.addRoundedRect(cover, rad, rad)
        p.save()
        p.setClipPath(path)
        if self._cover is not None:
            # обложка масштабируется один раз на размер (раньше — каждый кадр из оригинала до 2048 px)
            dpr = self.devicePixelRatioF()
            key = (self._cover.cacheKey(), int(cover.width()), round(dpr, 2))
            if getattr(self, "_cs_key", None) != key:
                src = self._cover
                s = min(src.width(), src.height())
                sq = src.copy((src.width() - s) // 2, (src.height() - s) // 2, s, s)
                n = max(1, int(round(cover.width() * dpr)))
                sc = sq.scaled(n, n, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
                sc.setDevicePixelRatio(dpr)
                self._cs_key, self._cs_pm = key, sc
            p.drawPixmap(cover.topLeft(), self._cs_pm)
        else:
            g = QLinearGradient(cover.topLeft(), cover.bottomRight())
            g.setColorAt(0, QColor("#3a3a46"))
            g.setColorAt(1, QColor("#16161c"))
            p.fillRect(cover, g)
            p.setPen(QColor(255, 255, 255, 60))
            f = QFont(); f.setPixelSize(int(cover.width() * 0.08)); f.setBold(True)
            p.setFont(f)
            p.drawText(cover, Qt.AlignmentFlag.AlignCenter, "нет обложки")
        if self._cover_hover > 0.01:
            p.fillRect(cover, QColor(0, 0, 0, int(70 * self._cover_hover)))
        p.restore()

        # крестик выхода — только при наведении на обложку
        if self._cover_hover > 0.01:
            a = self._cover_hover
            on = self._hover == "close"
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, int((170 if on else 120) * a)))
            p.drawEllipse(close)
            pen = QPen(QColor(255, 255, 255, int(235 * a)), 2.4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            c = close.center()
            d = close.width() * 0.2
            p.drawLine(QPointF(c.x() - d, c.y() - d), QPointF(c.x() + d, c.y() + d))
            p.drawLine(QPointF(c.x() + d, c.y() - d), QPointF(c.x() - d, c.y() + d))

        # полоска длительности
        dur = float(self._st.get("dur") or 0)
        pos = float(self._st.get("pos") or 0)
        frac = self._drag if self._drag is not None else (pos / dur if dur > 0 else 0.0)
        frac = max(0.0, min(1.0, frac))
        hot = self._hover == "bar" or self._drag is not None
        bh = 6 if hot else 4
        br = QRectF(bar.left(), bar.center().y() - bh / 2, bar.width(), bh)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 50))
        p.drawRoundedRect(br, bh / 2, bh / 2)
        p.setBrush(QColor(255, 255, 255, 235))
        p.drawRoundedRect(QRectF(br.left(), br.top(), max(bh, br.width() * frac), bh), bh / 2, bh / 2)
        if hot:
            p.drawEllipse(QPointF(br.left() + br.width() * frac, br.center().y()), 7, 7)
        f = QFont(); f.setPixelSize(12); f.setWeight(QFont.Weight.Medium)
        p.setFont(f)
        p.setPen(QColor(255, 255, 255, 150))
        shown = frac * dur if self._drag is not None else pos
        p.drawText(QRectF(bar.left(), bar.bottom() + 6, 80, 18), Qt.AlignmentFlag.AlignLeft, _fmt(shown))
        p.drawText(QRectF(bar.right() - 80, bar.bottom() + 6, 80, 18), Qt.AlignmentFlag.AlignRight, _fmt(dur))

        # кнопки
        playing = bool(self._st.get("playing"))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 255 if self._hover == "play" else 235))
        p.drawEllipse(play)
        p.setBrush(QColor(12, 12, 14))
        c = play.center()
        if playing:
            w, h, g = 6.0, 20.0, 5.0
            p.drawRoundedRect(QRectF(c.x() - g / 2 - w, c.y() - h / 2, w, h), 1.5, 1.5)
            p.drawRoundedRect(QRectF(c.x() + g / 2, c.y() - h / 2, w, h), 1.5, 1.5)
        else:
            tri = QPainterPath()
            tri.moveTo(c.x() - 7, c.y() - 11)
            tri.lineTo(c.x() - 7, c.y() + 11)
            tri.lineTo(c.x() + 12, c.y())
            tri.closeSubpath()
            p.drawPath(tri)
        for r, flip, name in ((prev, True, "prev"), (nxt, False, "next")):
            col = QColor(255, 255, 255, 255 if self._hover == name else 190)
            p.setBrush(col)
            c = r.center()
            s = -1 if flip else 1
            tri = QPainterPath()
            tri.moveTo(c.x() - 8 * s, c.y() - 9)
            tri.lineTo(c.x() - 8 * s, c.y() + 9)
            tri.lineTo(c.x() + 6 * s, c.y())
            tri.closeSubpath()
            p.drawPath(tri)
            bx = c.x() + 6 if not flip else c.x() - 9
            p.drawRoundedRect(QRectF(bx, c.y() - 9, 3, 18), 1, 1)
        p.end()
