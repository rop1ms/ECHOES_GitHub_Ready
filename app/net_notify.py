"""Уведомления Echoes.NET в углу экрана: тёмные стеклянные карточки в стиле ECHOES, со звуком.

Notifier(win, settings_fn, save_fn).show(kind, title, text, pixmap=None, on_click=None, key=None)
  kind — один из KINDS (каждый включается/выключается в настройках);
  key  — карточка с тем же ключом не дублируется, а обновляется («3 новых сообщения»).
Настройки лежат в settings["notif"] (см. DEFAULTS, cfg()).
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from PyQt6.QtCore import (Qt, QTimer, QRectF, QPoint, QPropertyAnimation, QEasingCurve, QObject, QUrl,
                          pyqtProperty)
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPixmap, QFont, QPen, QFontMetrics, QLinearGradient
from PyQt6.QtWidgets import QWidget, QApplication

SOUND = Path(__file__).with_name("sounds") / "notify.mp3"

KINDS = (
    ("message", "Сообщения", "личные и в группах"),
    ("request", "Заявки в друзья", "новые заявки и «теперь вы друзья»"),
    ("post", "Посты подписок", "новые посты людей, на которых вы подписаны"),
    ("comment", "Ответы на мои посты", "комментарии, ответы в тредах, угаданные треки"),
    ("online", "Друг появился в сети", "когда друг включает ECHOES"),
    ("room", "Комнаты друзей", "друг открыл «Слушать вместе»"),
    ("update", "Обновления ECHOES", "друг прислал новую версию плеера"),
    ("release", "Новые релизы подписок", "альбомы, синглы и EP исполнителей из «Подписок»"),
    ("info", "Прочие события сети", "служебные сообщения, пока плеер свёрнут"),
)
KIND_LABEL = {"message": "Сообщение", "request": "Друзья", "post": "Новый пост", "comment": "Ответ",
              "online": "В сети", "room": "Слушать вместе", "update": "Обновление", "release": "Новый релиз", "info": "Echoes.NET"}
KIND_ICON = {"message": "chat", "request": "users", "post": "note", "comment": "chat", "online": "user",
             "room": "wave", "update": "download", "release": "note", "info": "settings"}
POSITIONS = (("br", "Справа внизу"), ("tr", "Справа вверху"), ("bl", "Слева внизу"), ("tl", "Слева вверху"))
DEFAULTS = {"enabled": True, "sound": True, "volume": 70, "sound_file": "", "pos": "br", "duration": 6,
            "background_only": False, "preview": True, "dnd": False, "style": "echoes", "max": 4,
            "kinds": {k: k != "info" for k, _t, _s in KINDS}}
W, H = 360, 92


def cfg(settings) -> dict:
    """Настройки уведомлений с подставленными значениями по умолчанию (settings — словарь настроек сети)."""
    raw = settings.get("notif") if isinstance(settings, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULTS)
    out.update({k: v for k, v in raw.items() if k in DEFAULTS and k != "kinds"})
    kinds = dict(DEFAULTS["kinds"])
    if isinstance(raw.get("kinds"), dict):
        kinds.update({k: bool(v) for k, v in raw["kinds"].items() if k in kinds})
    if "notify" in settings and "kinds" not in raw and not settings.get("notify", True):
        out["enabled"] = False                                 # старая галочка «Уведомления Windows»
    out["kinds"] = kinds
    out["duration"] = max(2, min(30, int(out.get("duration") or 6)))
    out["volume"] = max(0, min(100, int(out.get("volume") or 0)))
    out["max"] = max(1, min(8, int(out.get("max") or 4)))
    return out


def _accent() -> QColor:
    try:
        import yamusic_theme
        return QColor(yamusic_theme.YELLOW)
    except Exception:                                          # noqa: BLE001
        return QColor("#ffdb1a")


def _kind_pix(kind, size, color) -> QPixmap:
    try:
        from yamusic_theme import ya_icon
        return ya_icon(KIND_ICON.get(kind, "settings"), size, color).pixmap(size, size)
    except Exception:                                          # noqa: BLE001
        return QPixmap()


def _font(px, bold=False) -> QFont:
    try:
        from yamusic_theme import ya_font
        return ya_font(px, QFont.Weight.Bold if bold else QFont.Weight.Normal)
    except Exception:                                          # noqa: BLE001
        f = QFont("Segoe UI")
        f.setPixelSize(px)
        f.setBold(bold)
        return f


def _round_pix(src: QPixmap, size: int) -> QPixmap:
    out = QPixmap(size, size)
    out.fill(Qt.GlobalColor.transparent)
    if src is None or src.isNull():
        return out
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    path = QPainterPath()
    path.addEllipse(0, 0, size, size)
    p.setClipPath(path)
    s = src.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
    p.drawPixmap((size - s.width()) // 2, (size - s.height()) // 2, s)
    p.end()
    return out


class Toast(QWidget):
    """Одна карточка. Сама уезжает через duration секунд; наведение мыши ставит таймер на паузу."""

    def __init__(self, owner, kind, title, text, pixmap, on_click, duration):
        flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint
                 | Qt.WindowType.WindowDoesNotAcceptFocus)
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setObjectName("EchoesToast")
        self.setFixedSize(W, H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.owner, self.kind, self.on_click = owner, kind, on_click
        self.title, self.text, self.count = str(title or ""), str(text or ""), 1
        self.avatar = _round_pix(pixmap, 46) if pixmap is not None and not pixmap.isNull() else None
        self.duration = float(duration)
        self.left = self.duration
        self.born = time.time()
        self.hover = False
        self._prog = 1.0
        self._closing = False
        self.tick = QTimer(self)
        self.tick.setInterval(40)
        self.tick.timeout.connect(self._step)
        self.anim = None

    # ── жизнь карточки ── #

    def bump(self, title, text, pixmap=None):
        """Ещё одно событие с тем же ключом: обновить текст, «N новых», таймер — заново."""
        self.count += 1
        self.title, self.text = str(title or self.title), str(text or self.text)
        if pixmap is not None and not pixmap.isNull():
            self.avatar = _round_pix(pixmap, 46)
        self.left = self.duration
        self.born = time.time()
        self.update()

    def _step(self):
        if not self.hover:
            self.left -= 0.04
        self._prog = max(0.0, self.left / self.duration)
        self.update()
        if self.left <= 0:
            self.dismiss()

    def appear(self, end: QPoint, from_right: bool):
        start = QPoint(end.x() + (40 if from_right else -40), end.y())
        self.move(start)
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        a = QPropertyAnimation(self, b"pos", self)
        a.setDuration(320)
        a.setStartValue(start)
        a.setEndValue(end)
        a.setEasingCurve(QEasingCurve.Type.OutCubic)
        a.start()
        f = QPropertyAnimation(self, b"windowOpacity", self)
        f.setDuration(260)
        f.setStartValue(0.0)
        f.setEndValue(1.0)
        f.start()
        self.anim = (a, f)
        self.tick.start()

    def slide_to(self, end: QPoint):
        if self.pos() == end:
            return
        a = QPropertyAnimation(self, b"pos", self)
        a.setDuration(220)
        a.setStartValue(self.pos())
        a.setEndValue(end)
        a.setEasingCurve(QEasingCurve.Type.OutCubic)
        a.start()
        self._slide = a

    def dismiss(self):
        if self._closing:
            return
        self._closing = True
        self.tick.stop()
        f = QPropertyAnimation(self, b"windowOpacity", self)
        f.setDuration(200)
        f.setStartValue(self.windowOpacity())
        f.setEndValue(0.0)
        f.finished.connect(self.close)
        f.start()
        self._fade = f
        self.owner._gone(self)

    # ── мышь ── #

    def enterEvent(self, e):
        self.hover = True
        self.update()

    def leaveEvent(self, e):
        self.hover = False
        self.update()

    def _close_rect(self) -> QRectF:
        return QRectF(W - 30, 10, 18, 18)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and not self._close_rect().contains(e.position()):
            cb = self.on_click
            self.dismiss()
            if cb:
                try:
                    cb()
                except Exception as ex:                        # noqa: BLE001
                    print("[notify] click:", ex)
        else:
            self.dismiss()

    # ── рисование ── #

    def paintEvent(self, e):
        acc = _accent()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(4, 4, W - 8, H - 8)
        shade = QPainterPath()
        shade.addRoundedRect(QRectF(1, 3, W - 2, H - 4), 18, 18)
        p.fillPath(shade, QColor(0, 0, 0, 70))                 # мягкая тень
        path = QPainterPath()
        path.addRoundedRect(r, 16, 16)
        g = QLinearGradient(r.topLeft(), r.bottomRight())
        g.setColorAt(0, QColor(30, 30, 34, 246))
        g.setColorAt(1, QColor(18, 18, 20, 246))
        p.fillPath(path, g)
        glow = QLinearGradient(r.left(), 0, r.left() + 120, 0)  # отсвет акцента слева
        c0 = QColor(acc); c0.setAlpha(46)
        c1 = QColor(acc); c1.setAlpha(0)
        glow.setColorAt(0, c0)
        glow.setColorAt(1, c1)
        p.fillPath(path, glow)
        p.setPen(QPen(QColor(255, 255, 255, 34 if not self.hover else 60), 1))
        p.drawPath(path)
        p.save()
        p.setClipPath(path)
        p.fillRect(QRectF(r.left(), r.top() + 14, 3, r.height() - 28), acc)       # полоска акцента
        pr = QRectF(r.left() + 16, r.bottom() - 3, (r.width() - 32) * self._prog, 2)
        cp = QColor(acc); cp.setAlpha(150)
        p.fillRect(pr, cp)                                     # сколько осталось показывать
        p.restore()
        # аватар или значок
        ar = QRectF(r.left() + 16, r.top() + (r.height() - 46) / 2, 46, 46)
        if self.avatar is not None:
            p.drawPixmap(ar.topLeft().toPoint(), self.avatar)
        else:
            circ = QPainterPath(); circ.addEllipse(ar)
            bg = QColor(acc); bg.setAlpha(40)
            p.fillPath(circ, bg)
            ip = _kind_pix(self.kind, 22, acc.name())
            if not ip.isNull():
                p.drawPixmap(int(ar.center().x() - 11), int(ar.center().y() - 11), ip)
        if self.avatar is not None:                            # маленький значок типа на аватаре
            br = QRectF(ar.right() - 16, ar.bottom() - 16, 20, 20)
            bp = QPainterPath(); bp.addEllipse(br)
            p.fillPath(bp, acc)
            p.setPen(QPen(QColor(20, 20, 22), 2))
            p.drawPath(bp)
            ip = _kind_pix(self.kind, 12, "#000000")
            if not ip.isNull():
                p.drawPixmap(int(br.center().x() - 6), int(br.center().y() - 6), ip)
        x = ar.right() + 14
        tw = r.right() - x - 30
        f = _font(10, True); f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.8)
        p.setFont(f)
        p.setPen(acc)
        label = KIND_LABEL.get(self.kind, "Echoes.NET").upper()
        if self.count > 1:
            label += f"  ·  {self.count} НОВЫХ"
        p.drawText(QRectF(x, r.top() + 13, tw, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
        ago = int(time.time() - self.born)
        f2 = _font(10)
        p.setFont(f2)
        p.setPen(QColor(255, 255, 255, 90))
        lw = QFontMetrics(f).horizontalAdvance(label)
        if lw + 60 < tw:
            p.drawText(QRectF(x + lw + 8, r.top() + 13, 60, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       "сейчас" if ago < 50 else f"{ago // 60 or 1} мин")
        f3 = _font(14, True)
        p.setFont(f3)
        p.setPen(QColor(255, 255, 255))
        p.drawText(QRectF(x, r.top() + 29, tw + 18, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(f3).elidedText(self.title, Qt.TextElideMode.ElideRight, int(tw + 18)))
        f4 = _font(12)
        p.setFont(f4)
        p.setPen(QColor(255, 255, 255, 165))
        fm = QFontMetrics(f4)
        body = " ".join(self.text.split())
        p.drawText(QRectF(x, r.top() + 50, tw + 18, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(body, Qt.TextElideMode.ElideRight, int(tw + 18)))
        if self.hover:                                         # крестик
            cr = self._close_rect()
            p.setPen(QPen(QColor(255, 255, 255, 150), 1.6))
            p.drawLine(cr.topLeft() + QPoint(5, 5).toPointF(), cr.bottomRight() - QPoint(5, 5).toPointF())
            p.drawLine(QRectF(cr).topRight() + QPoint(-5, 5).toPointF(), QRectF(cr).bottomLeft() + QPoint(5, -5).toPointF())
        p.end()


class Notifier(QObject):
    """Стопка карточек в выбранном углу экрана + звук."""

    def __init__(self, win, settings_fn, tray_fn=None):
        super().__init__(win)
        self.win = win
        self.settings_fn = settings_fn                         # () -> словарь настроек сети
        self.tray_fn = tray_fn                                 # () -> QSystemTrayIcon (стиль «Windows»)
        self.toasts: list[Toast] = []
        self.keys: dict = {}
        self._player = None
        self._audio = None
        self._last_sound = 0.0

    def conf(self) -> dict:
        try:
            return cfg(self.settings_fn() or {})
        except Exception:                                      # noqa: BLE001
            return cfg({})

    def wants(self, kind) -> bool:
        c = self.conf()
        if not c["enabled"] or c["dnd"] or not c["kinds"].get(kind, True):
            return False
        active = QApplication.applicationState() == Qt.ApplicationState.ApplicationActive
        if kind == "info" and active:
            return False                                       # в открытом плеере это и так видно тостом
        return not (c["background_only"] and active)

    def show(self, kind, title, text="", pixmap=None, on_click=None, key=None, force=False) -> bool:
        if not force and not self.wants(kind):
            return False
        c = self.conf()
        if not c["preview"] and kind in ("message", "comment"):
            text = "Новое сообщение" if kind == "message" else "Новый ответ"
        self.sound(c)
        if c["style"] == "windows":
            return self._tray(title, text)
        t = self.keys.get(key) if key else None
        if t is not None and t in self.toasts and not t._closing:
            t.bump(title, text, pixmap)
            return True
        t = Toast(self, kind, title, text, pixmap, on_click, c["duration"])
        if key:
            self.keys[key] = t
        self.toasts.insert(0, t)
        while len([x for x in self.toasts if not x._closing]) > c["max"]:
            old = [x for x in self.toasts if not x._closing][-1]
            old.dismiss()
        self._layout(new=t)
        return True

    def _tray(self, title, text) -> bool:
        try:
            from PyQt6.QtWidgets import QSystemTrayIcon
            tr = self.tray_fn() if self.tray_fn else None
            if tr is None:
                return False
            tr.showMessage(str(title), str(text)[:200], QSystemTrayIcon.MessageIcon.Information, 6000)
            return True
        except Exception as e:                                 # noqa: BLE001
            print("[notify] tray:", e)
            return False

    def _screen_rect(self):
        scr = None
        try:
            if self.win is not None and self.win.isVisible() and not self.win.isMinimized():
                scr = self.win.screen()
        except Exception:                                      # noqa: BLE001
            scr = None
        scr = scr or QApplication.primaryScreen()
        return scr.availableGeometry()

    def _layout(self, new=None):
        pos = self.conf()["pos"]
        g = self._screen_rect()
        right, bottom = pos in ("br", "tr"), pos in ("br", "bl")
        x = g.right() - W - 12 if right else g.left() + 12
        y = g.bottom() - H - 12 if bottom else g.top() + 12
        step = (H + 4) * (-1 if bottom else 1)
        for t in [x_ for x_ in self.toasts if not x_._closing]:
            end = QPoint(x, y)
            if t is new:
                t.appear(end, right)
            else:
                t.slide_to(end)
            y += step

    def _gone(self, t):
        if t in self.toasts:
            self.toasts.remove(t)
        for k, v in list(self.keys.items()):
            if v is t:
                self.keys.pop(k, None)
        QTimer.singleShot(0, self._layout)

    def clear(self):
        for t in list(self.toasts):
            t.dismiss()

    # ── звук ── #

    def sound_path(self, c=None) -> str:
        c = c or self.conf()
        f = str(c.get("sound_file") or "")
        return f if f and os.path.isfile(f) else str(SOUND)

    def sound(self, c=None, force=False):
        c = c or self.conf()
        if not force and (not c["sound"] or time.time() - self._last_sound < 1.2):
            return                                             # пачка событий — один звук
        self._last_sound = time.time()
        try:
            from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
            if self._player is None:
                self._player = QMediaPlayer(self)
                self._audio = QAudioOutput(self)
                self._player.setAudioOutput(self._audio)
            path = self.sound_path(c)
            if self._player.source().toLocalFile() != path:
                self._player.setSource(QUrl.fromLocalFile(path))
            self._audio.setVolume(c["volume"] / 100.0)
            self._player.stop()
            self._player.setPosition(0)
            self._player.play()
        except Exception as e:                                 # noqa: BLE001
            print("[notify] sound:", e)
