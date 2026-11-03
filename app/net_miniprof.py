"""Мини-профили Echoes.NET: наводишь мышку на аватарку в ленте — всплывает карточка человека (как в Discord).

Карточка: баннер (картинка, GIF или кусочек видео — превращается в GIF), аватарка с рамкой, ник со стилем
(net_nick), местоимения, пара слов о себе, статус и до 6 выбранных ачивок со своими значками (ach_icons).
Тег сервера, как в Discord, не нужен. Всё хранится в settings["ya_profile_deco"]["mp"] и уходит вместе с
оформлением профиля (deco): друзьям — целиком, в публичную сеть — с ужатым баннером.
"""
import base64
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from PyQt6.QtCore import QBuffer, QByteArray, QEvent, QIODevice, QObject, QPoint, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import (QColor, QCursor, QImage, QLinearGradient, QMovie, QPainter, QPainterPath, QPen,
                         QPixmap)
from PyQt6.QtWidgets import (QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                             QVBoxLayout, QWidget, QGridLayout, QApplication)

W, BANNER_H = 320, 112
BANNER_LIMIT = 260000                 # base64 баннера (друзьям); в публичную сеть — меньше (for_public)
_GIF = b"GIF8"


def _kind(raw: bytes) -> str:
    if raw[:3] == b"\xff\xd8\xff":
        return "jpg"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if raw[:4] == _GIF:
        return "gif"
    return ""


def clean(mp) -> dict:
    """Чужой мини-профиль из сети — только то, что умеем показать."""
    if not isinstance(mp, dict):
        return {}
    from net_showcase import _hex
    out = {}
    pron = " ".join(str(mp.get("pron") or "").split())[:40]
    if pron:
        out["pron"] = pron
    about = " ".join(str(mp.get("about") or "").split())[:190]
    if about:
        out["about"] = about
    b = mp.get("banner")
    if isinstance(b, str) and b and len(b) <= BANNER_LIMIT:
        try:
            if _kind(base64.b64decode(b[:64] + "=" * (-len(b[:64]) % 4))[:8]):
                out["banner"] = b
        except ValueError:
            pass
    if mp.get("bc1"):
        out["bc1"] = _hex(mp.get("bc1"), "#5b3cff")
        out["bc2"] = _hex(mp.get("bc2") or mp.get("bc1"), "#ff5fa2")
    ach = [str(a)[:40] for a in (mp.get("ach") or [])[:6] if isinstance(a, str)]
    if ach:
        out["ach"] = ach
    return out


def shrink_for_public(mp: dict, budget: int) -> dict:
    """Баннер побольше — в публичную сеть ужатым (GIF не пережать — тогда первый кадр JPEG)."""
    mp = dict(mp or {})
    b = mp.get("banner") or ""
    if len(b) <= budget:
        return mp
    from net_showcase import jpeg_b64
    img = QImage()
    img.loadFromData(base64.b64decode(b))
    nb = jpeg_b64(img, 360, budget) if not img.isNull() else ""
    if nb:
        mp["banner"] = nb
    else:
        mp.pop("banner", None)
    return mp


# ── баннер из файла: картинка / GIF / видео ──

def _ffmpeg():
    return shutil.which("ffmpeg") or ""


def banner_from_file(path: str) -> tuple[str, str]:
    """(base64, ошибка). Картинка — JPEG 640 px; GIF — как есть, если влезает, иначе ужимаем через ffmpeg;
    видео — первые 4 секунды в маленький GIF."""
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError as e:
        return "", f"Не открыть файл: {e}"
    k = _kind(raw)
    if k in ("jpg", "png"):
        from net_showcase import jpeg_b64
        img = QImage()
        img.loadFromData(raw)
        img = _crop_banner(img)
        b = jpeg_b64(img, 640, BANNER_LIMIT - 1000)
        return (b, "") if b else ("", "Картинку не удалось сжать")
    if k == "gif" and len(raw) * 4 // 3 < BANNER_LIMIT:
        return base64.b64encode(raw).decode(), ""
    ff = _ffmpeg()
    if not ff:
        return "", "Для видео и больших GIF нужен ffmpeg (ставится вместе с ECHOES)"
    out = Path(tempfile.gettempdir()) / f"echoes_banner_{os.getpid()}.gif"
    for fps, width in ((12, 360), (10, 300), (8, 260), (6, 220)):
        vf = (f"fps={fps},scale={width}:-1:flags=lanczos,crop=iw:min(ih\\,iw*0.35),split[a][b];"
              f"[a]palettegen=max_colors=64[p];[b][p]paletteuse=dither=bayer")
        try:
            subprocess.run([ff, "-y", "-v", "error", "-t", "4", "-i", str(p), "-vf", vf, "-loop", "0", str(out)],
                           timeout=60, check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            data = out.read_bytes()
        except (OSError, subprocess.SubprocessError) as e:
            return "", f"Не получилось сделать GIF: {e}"
        if len(data) * 4 // 3 < BANNER_LIMIT:
            out.unlink(missing_ok=True)
            return base64.b64encode(data).decode(), ""
    out.unlink(missing_ok=True)
    return "", "Даже короткий GIF получился слишком тяжёлым — возьмите видео покороче"


def _crop_banner(img: QImage) -> QImage:
    if img.isNull():
        return img
    want = img.width() / (W / BANNER_H)
    if img.height() > want:
        y = int((img.height() - want) / 2)
        img = img.copy(0, y, img.width(), int(want))
    return img


# ── сама карточка ──

class Banner(QWidget):
    """Баннер: GIF крутится, картинка — статично, без баннера — градиент из цветов профиля."""
    def __init__(self, mp, parent=None, h=BANNER_H, radius=14):
        super().__init__(parent)
        self.setFixedHeight(h)
        self.radius = radius
        self.mp = mp or {}
        self.pm = None
        self.movie = None
        self._buf = None
        self.set_mp(self.mp)

    def set_mp(self, mp):
        self.mp = mp or {}
        if self.movie is not None:
            self.movie.stop()
            self.movie = None
        self.pm = None
        b = self.mp.get("banner") or ""
        if b:
            try:
                raw = base64.b64decode(b)
            except ValueError:
                raw = b""
            import img_guard                          # баннер — от другого человека: без «картинок-бомб»
            if _kind(raw) == "gif" and not img_guard.gif_ok(raw):
                raw = b""
            if _kind(raw) == "gif":
                self._buf = QBuffer()
                self._buf.setData(QByteArray(raw))
                self._buf.open(QIODevice.OpenModeFlag.ReadOnly)
                self.movie = QMovie(self._buf, b"gif")
                self.movie.frameChanged.connect(lambda _i: self.update())
                self.movie.start()
            elif raw:
                img = img_guard.load(raw, 1280)
                self.pm = QPixmap.fromImage(img) if not img.isNull() else None
        self.update()

    def hideEvent(self, e):
        if self.movie is not None:
            self.movie.setPaused(True)
        super().hideEvent(e)

    def showEvent(self, e):
        if self.movie is not None:
            self.movie.setPaused(False)
        super().showEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(r.adjusted(0, 0, 0, self.radius), self.radius, self.radius)
        p.setClipPath(clip)
        pm = self.movie.currentPixmap() if self.movie is not None else self.pm
        if pm is not None and not pm.isNull():
            s = max(r.width() / pm.width(), r.height() / pm.height())
            w, h = pm.width() * s, pm.height() * s
            p.drawPixmap(QRectF((r.width() - w) / 2, (r.height() - h) / 2, w, h), pm, QRectF(pm.rect()))
        else:
            g = QLinearGradient(r.topLeft(), r.bottomRight())
            g.setColorAt(0, QColor(self.mp.get("bc1") or "#5b3cff"))
            g.setColorAt(1, QColor(self.mp.get("bc2") or "#ff5fa2"))
            p.fillRect(r, g)
        p.end()


class MiniCard(QFrame):
    """Карточка мини-профиля (её же показывает предпросмотр в редакторе)."""
    def __init__(self, info: dict, parent=None, popup=True):
        super().__init__(parent)
        if popup:
            self.setWindowFlags(Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedWidth(W + (16 if popup else 0))
        self.info = info
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8) if popup else outer.setContentsMargins(0, 0, 0, 0)
        self.body = QFrame()
        self.body.setObjectName("MiniBody")
        self.body.setStyleSheet("QFrame#MiniBody{background:#151518;border:1px solid rgba(255,255,255,0.08);"
                                "border-radius:14px;} QLabel{background:transparent;color:#e8e8e8;}")
        outer.addWidget(self.body)
        v = QVBoxLayout(self.body)
        v.setContentsMargins(0, 0, 0, 14)
        v.setSpacing(0)
        self.banner = Banner(info.get("mp") or {}, self.body)
        v.addWidget(self.banner)
        self.av = QLabel(self.body)
        self.av.setFixedSize(84, 84)
        self.av.setStyleSheet("background:#151518;border-radius:42px;")
        self.av.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addSpacing(40)
        inner = QVBoxLayout()
        inner.setContentsMargins(16, 0, 16, 0)
        inner.setSpacing(4)
        v.addLayout(inner)
        import net_nick
        self.nick = net_nick.NickLabel(info.get("name") or "", (info.get("deco") or {}).get("nick"), 22)
        if not (info.get("deco") or {}).get("nick"):
            self.nick.spec = {}
        inner.addWidget(self.nick)
        self.pron = QLabel("")
        self.pron.setStyleSheet("color:#9a9a9a;font-size:12px;font-weight:600;")
        inner.addWidget(self.pron)
        self.about = QLabel("")
        self.about.setWordWrap(True)
        self.about.setStyleSheet("font-size:12px;color:#d0d0d0;padding-top:4px;")
        inner.addWidget(self.about)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("font-size:11px;color:#8a8a8a;")
        inner.addWidget(self.status)
        self.ach_row = QHBoxLayout()
        self.ach_row.setSpacing(6)
        self.ach_head = QLabel("АЧИВКИ")
        self.ach_head.setStyleSheet("color:#7a7a7a;font-size:10px;font-weight:900;letter-spacing:1px;padding-top:8px;")
        inner.addWidget(self.ach_head)
        inner.addLayout(self.ach_row)
        self.open_btn = None
        if info.get("open"):
            b = QPushButton("Открыть профиль")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet("QPushButton{background:rgba(255,255,255,0.08);color:#fff;border:none;border-radius:9px;"
                            "padding:7px;font-weight:800;margin-top:10px;}"
                            "QPushButton:hover{background:rgba(255,255,255,0.16);}")
            b.clicked.connect(lambda: (self.hide(), info["open"]()))
            inner.addWidget(b)
            self.open_btn = b
        self.set_info(info)

    def set_info(self, info):
        self.info = info
        mp = info.get("mp") or {}
        self.banner.set_mp(mp)
        pm = info.get("avatar")
        if pm is not None:
            self.av.setPixmap(pm)
        self.nick.set_text(info.get("name") or "")
        self.nick.set_style((info.get("deco") or {}).get("nick"))
        self.pron.setText(mp.get("pron", ""))
        self.pron.setVisible(bool(mp.get("pron")))
        self.about.setText(mp.get("about", ""))
        self.about.setVisible(bool(mp.get("about")))
        self.status.setText(info.get("status", ""))
        self.status.setVisible(bool(info.get("status")))
        while self.ach_row.count():
            it = self.ach_row.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        have = [a for a in (mp.get("ach") or info.get("ach") or []) if a in (info.get("ach_names") or {})]
        have = [a for a in have if a in (info.get("ach") or [])][:6]
        import ach_icons
        for a in have:
            l = QLabel()
            l.setFixedSize(34, 34)
            l.setAlignment(Qt.AlignmentFlag.AlignCenter)
            l.setPixmap(ach_icons.pixmap(a, 20))
            l.setToolTip(info["ach_names"][a][0])
            l.setStyleSheet("background:rgba(255,219,26,0.13);border-radius:10px;")
            self.ach_row.addWidget(l)
        self.ach_row.addStretch(1)
        self.ach_head.setVisible(bool(have))
        self.adjustSize()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.av.move(16, BANNER_H - 42)
        self.av.raise_()


class _Hover(QObject):
    """Наведение на аватарку: через 0,35 с — мини-профиль; уходит, когда мышь ушла и с аватарки, и с карточки."""
    def __init__(self, net, w, onion):
        super().__init__(w)
        self.net, self.w, self.onion = net, w, onion
        self.t = QTimer(self)
        self.t.setSingleShot(True)
        self.t.setInterval(350)
        self.t.timeout.connect(self._show)
        w.installEventFilter(self)

    def eventFilter(self, obj, e):
        et = e.type()
        if et == QEvent.Type.Enter:
            self.t.start()
        elif et in (QEvent.Type.Leave, QEvent.Type.MouseButtonPress, QEvent.Type.Hide):
            self.t.stop()
            _Popup.later()
        return False

    def _show(self):
        try:
            if not self.w.isVisible() or not self.w.underMouse():
                return
        except RuntimeError:
            return
        _Popup.show_for(self.net, self.onion, self.w)


class _Popup:
    card = None
    onion = None
    hide_t = None

    @classmethod
    def show_for(cls, net, onion, anchor):
        info = info_of(net, onion)
        if info is None:
            return
        info["open"] = lambda o=onion: net.open_profile(o)
        if cls.card is None or cls.onion != onion:
            cls.close()
            cls.card = MiniCard(info)
            cls.card.installEventFilter(_Keeper.get())
            cls.onion = onion
        g = anchor.mapToGlobal(QPoint(anchor.width() + 6, -10))
        scr = (anchor.screen() or QApplication.primaryScreen()).availableGeometry()
        cls.card.adjustSize()
        x = min(g.x(), scr.right() - cls.card.width() - 6)
        y = max(scr.top() + 6, min(g.y(), scr.bottom() - cls.card.height() - 6))
        cls.card.move(x, y)
        cls.card.show()
        cls.card.raise_()
        if cls.hide_t is not None:
            cls.hide_t.stop()

    @classmethod
    def later(cls):
        if cls.card is None:
            return
        if cls.hide_t is None:
            cls.hide_t = QTimer()
            cls.hide_t.setSingleShot(True)
            cls.hide_t.timeout.connect(cls._maybe_hide)
        cls.hide_t.start(260)

    @classmethod
    def _maybe_hide(cls):
        c = cls.card
        if c is None:
            return
        try:
            if c.isVisible() and c.geometry().adjusted(-4, -4, 4, 4).contains(QCursor.pos()):
                cls.hide_t.start(260)
                return
        except RuntimeError:
            pass
        cls.close()

    @classmethod
    def close(cls):
        if cls.card is not None:
            try:
                cls.card.hide()
                cls.card.deleteLater()
            except RuntimeError:
                pass
        cls.card = None
        cls.onion = None


class _Keeper(QObject):
    _one = None

    @classmethod
    def get(cls):
        if cls._one is None:
            cls._one = _Keeper()
        return cls._one

    def eventFilter(self, obj, e):
        if e.type() == QEvent.Type.Leave:
            _Popup.later()
        elif e.type() == QEvent.Type.Enter and _Popup.hide_t is not None:
            _Popup.hide_t.stop()
        return False


def hover(net, w, onion):
    """Повесить мини-профиль на виджет-аватарку."""
    if onion:
        _Hover(net, w, onion)
    return w


def info_of(net, onion) -> dict | None:
    nd = getattr(net, "node", None)
    if nd is None:
        return None
    me = onion == nd.me.onion
    deco = net.deco_of(onion) or {}
    if me:
        name = nd._name()
        ach = net.achievements()
        status = str(net.win.profile.get("status") or "")
    else:
        prof = nd.contact_profile(onion) or {}
        c = nd.store.contact(onion) or {}
        name = prof.get("name") or c.get("name") or onion[:8]
        ach = prof.get("ach") or []
        status = prof.get("status") or ""
        try:
            now = net._now_of(onion) if hasattr(net, "_now_of") else None
        except Exception:                                      # noqa: BLE001
            now = None
        if now:
            status = f"слушает {now.get('artist', '')} — {now.get('title', '')}"
    av = net._av(onion, name, 76, None)
    try:
        import net_frames
        r = net._frame_of(onion)
        eff = net_frames.effective(*r) if r else {}
        if eff:
            fr = net_frames.frame_pixmap(eff, 84)
            if fr is not None:
                pm = QPixmap(84 * 2, 84 * 2)
                pm.setDevicePixelRatio(2)
                pm.fill(Qt.GlobalColor.transparent)
                p = QPainter(pm)
                p.drawPixmap(4, 4, av)
                p.drawPixmap(QRectF(0, 0, 84, 84), fr, QRectF(fr.rect()))
                p.end()
                av = pm
    except Exception:                                          # noqa: BLE001
        pass
    return {"name": name, "deco": deco, "mp": clean(deco.get("mp")), "ach": list(ach), "status": status,
            "avatar": av, "ach_names": net.ACH}


# ── редактор мини-профиля ──

class MiniEditor(QDialog):
    def __init__(self, net, parent=None):
        super().__init__(parent or net)
        self.net = net
        self.setWindowTitle("Мини-профиль")
        self.setModal(True)
        d = dict(net.win.settings.get("ya_profile_deco") or {})
        self.mp = clean(d.get("mp")) or {"bc1": "#5b3cff", "bc2": "#ff5fa2"}
        self.setStyleSheet("QDialog{background:#101012;} QLabel{color:#e8e8e8;background:transparent;}"
                           "QLabel#H{font-size:13px;font-weight:800;padding-top:6px;}"
                           "QLabel#S{color:#8a8a8a;font-size:11px;}"
                           "QLineEdit{background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.1);"
                           "border-radius:9px;padding:7px 10px;color:#fff;}"
                           "QPushButton{background:rgba(255,255,255,0.08);color:#fff;border:none;border-radius:9px;"
                           "padding:7px 12px;font-weight:700;} QPushButton:hover{background:rgba(255,255,255,0.15);}"
                           "QPushButton#Act{background:#ffdb1a;color:#000;font-weight:800;padding:9px 22px;}"
                           "QPushButton:checked{background:rgba(255,219,26,0.22);color:#ffdb1a;}")
        h = QHBoxLayout(self)
        h.setContentsMargins(20, 18, 20, 18)
        h.setSpacing(22)
        form = QVBoxLayout()
        form.setSpacing(6)
        h.addLayout(form, 1)
        form.addWidget(self._h("Баннер", "картинка, GIF или видео (возьмём первые 4 секунды)"))
        br = QHBoxLayout()
        pick = QPushButton("Выбрать файл…")
        pick.clicked.connect(self._pick)
        br.addWidget(pick)
        self.c1b = QPushButton("Цвет 1")
        self.c1b.clicked.connect(lambda: self._color("bc1"))
        self.c2b = QPushButton("Цвет 2")
        self.c2b.clicked.connect(lambda: self._color("bc2"))
        br.addWidget(self.c1b)
        br.addWidget(self.c2b)
        rm = QPushButton("Убрать картинку")
        rm.clicked.connect(lambda: (self.mp.pop("banner", None), self._sync()))
        br.addWidget(rm)
        br.addStretch(1)
        form.addLayout(br)
        self.err = QLabel("")
        self.err.setObjectName("S")
        self.err.setStyleSheet("color:#ff8a8a;")
        self.err.hide()
        form.addWidget(self.err)
        form.addWidget(self._h("Местоимения", "например: он/его, she/her"))
        self.pron = QLineEdit(self.mp.get("pron", ""))
        self.pron.setMaxLength(40)
        self.pron.textChanged.connect(lambda t: self._set("pron", t.strip()))
        form.addWidget(self.pron)
        form.addWidget(self._h("О себе", "пара слов на карточке — до 190 символов"))
        self.about = QLineEdit(self.mp.get("about", ""))
        self.about.setMaxLength(190)
        self.about.textChanged.connect(lambda t: self._set("about", t.strip()))
        form.addWidget(self.about)
        form.addWidget(self._h("Ачивки на карточке", "выберите до 6 из полученных"))
        g = QGridLayout()
        g.setSpacing(6)
        import ach_icons
        self.have = [a for a in net.achievements() if a in net.ACH]
        self.ach_btns = {}
        for i, a in enumerate(self.have):
            b = QPushButton("  " + net.ACH[a][0])
            b.setIcon(__import__("PyQt6.QtGui", fromlist=["QIcon"]).QIcon(ach_icons.pixmap(a, 16)))
            b.setCheckable(True)
            b.setChecked(a in (self.mp.get("ach") or self.have[:6]))
            b.clicked.connect(self._ach)
            g.addWidget(b, i // 2, i % 2)
            self.ach_btns[a] = b
        if not self.have:
            l = QLabel("Пока нет ачивок — они появятся здесь, как только получите")
            l.setObjectName("S")
            g.addWidget(l, 0, 0)
        form.addLayout(g)
        form.addWidget(self._h("Стиль ника", "шрифт, эффект и цвет — «Оформить профиль» → «Стиль ника»"))
        nb = QPushButton("Изменить стиль ника…")
        nb.clicked.connect(self._nick)
        form.addWidget(nb)
        form.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Сохранить")
        ok.setObjectName("Act")
        ok.clicked.connect(self._save)
        row.addWidget(cancel)
        row.addWidget(ok)
        form.addLayout(row)
        side = QVBoxLayout()
        side.addWidget(self._h("Как увидят другие"))
        info = info_of(net, net.node.me.onion) or {"name": "", "ach": [], "ach_names": net.ACH}
        self.info = info
        self.card = MiniCard(dict(info, mp=self.mp), popup=False)
        side.addWidget(self.card)
        side.addStretch(1)
        h.addLayout(side)
        self._ach()

    def _h(self, t, sub=""):
        w = QWidget()
        l = QHBoxLayout(w)
        l.setContentsMargins(0, 0, 0, 0)
        a = QLabel(t)
        a.setObjectName("H")
        l.addWidget(a)
        if sub:
            b = QLabel(sub)
            b.setObjectName("S")
            l.addWidget(b)
        l.addStretch(1)
        return w

    def _set(self, k, v):
        if v:
            self.mp[k] = v
        else:
            self.mp.pop(k, None)
        self._sync()

    def _ach(self):
        on = [a for a, b in self.ach_btns.items() if b.isChecked()]
        if len(on) > 6:
            snd = self.sender()
            if isinstance(snd, QPushButton):
                snd.setChecked(False)
            on = on[:6]
        self.mp["ach"] = on
        self._sync()

    def _color(self, k):
        from PyQt6.QtWidgets import QColorDialog
        c = QColorDialog.getColor(QColor(self.mp.get(k) or "#5b3cff"), self, "Цвет баннера")
        if c.isValid():
            self.mp[k] = c.name()
            self.mp.setdefault("bc1" if k == "bc2" else "bc2", c.name())
            self._sync()

    def _pick(self):
        f, _ = QFileDialog.getOpenFileName(self, "Баннер мини-профиля", "",
                                           "Картинки, GIF и видео (*.png *.jpg *.jpeg *.gif *.mp4 *.webm *.mov *.mkv)")
        if not f:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            b, err = banner_from_file(f)
        finally:
            QApplication.restoreOverrideCursor()
        self.err.setText(err)
        self.err.setVisible(bool(err))
        if b:
            self.mp["banner"] = b
            self._sync()

    def _nick(self):
        self.net.edit_nick()
        self.info = info_of(self.net, self.net.node.me.onion) or self.info
        self._sync()

    def _sync(self):
        self.card.set_info(dict(self.info, mp=clean(self.mp)))

    def _save(self):
        d = dict(self.net.win.settings.get("ya_profile_deco") or {})
        mp = clean(self.mp)
        if mp:
            d["mp"] = mp
        else:
            d.pop("mp", None)
        self.net.win.settings["ya_profile_deco"] = d
        try:
            self.net.win.save_settings()
        except Exception:                                      # noqa: BLE001
            pass
        self.accept()
