# clip_mode.py
"""
Режим «Клип» — официальный клип песни, во всех темах (и встроенных, и своих из конструктора).

Поверх всего окна (как режим обложки): видео клипа в рамке, сверху — название и кнопки
(скачать / другой клип / эффекты / полный экран / закрыть), снизу — управление треком,
перемотка и синхронизация. Звук — по-прежнему трек плеера, клип идёт без звука и
держится вровень с ним: пауза, перемотка, скорость, сдвиг клипа относительно трека
(подбирается автоматически по звуку при скачивании, поправить — кнопками −/+).

Вид — под каждую тему:
  * Echoes Music — чёрный фон, жёлтые акценты, «таблетки»-кнопки, как в Яндекс Музыке;
  * Winamp       — окно с золотыми полосками в заголовке, объёмные серые кнопки, ЖК-строка;
  * Fluid        — тёплый бежевый (или тёмный) фон, кремовая карточка-рамка, оранжевый акцент;
  * Vinyl glass  — матовое стекло поверх размытой обложки;
  * темы из конструктора — их палитра, а со «своим оформлением режима клипа» — всё из
    t["clip"]: фон (обложка / цвета клипа / цвет / фон темы со слоями), рамка (скруглённая,
    телевизор, неон, полароид, киноплёнка…), свечение, размер, панель управления, эффекты.

Эффекты (clip_fx) — кнопка «Эффекты»: калейдоскоп, туннель, кислота, глитч, VHS,
тепловизор… и набор «Кошмар эпилептика». Перед вспышками — предупреждение.

ClipService находит клипы и без открытого режима (настройка «находить клипы сами»). По умолчанию клипы
ОНЛАЙН (clip_fetch.StreamFetch): видео не скачивается — VLC играет видеодорожку по ссылке; скачать клип
на компьютер можно из меню «Другой клип» → «Сохранить клип на компьютер».
"""
from __future__ import annotations

import time

import numpy as np
from img_load import load_pixmap
from PyQt6.QtCore import QObject, QPointF, QRectF, Qt, QVariantAnimation, QEasingCurve, pyqtSignal
from PyQt6.QtGui import (QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
                         QRadialGradient)
from PyQt6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QMenu, QMessageBox, QPushButton, QScrollArea,
                             QSizePolicy, QSlider, QVBoxLayout, QWidget)

import clip_fetch as CF
from clip_fx import EFFECTS, FLASHY, PRESETS, ClipFX
import clip_fx as _CFX
from frameclock import FrameTimer


def _fmt(sec: float) -> str:
    sec = max(0, int(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def _qc(c, a=None) -> QColor:
    q = QColor(c or "#000000")
    if a is not None:
        q.setAlphaF(max(0.0, min(1.0, a)))
    return q


def _rgba(c, a) -> str:
    q = QColor(c or "#000000")
    return f"rgba({q.red()},{q.green()},{q.blue()},{max(0.0, min(1.0, a)):.3f})"


# ------------------------------------------------------------------ #
#  Стиль под тему                                                     #
# ------------------------------------------------------------------ #

def clip_style(d: dict | None) -> dict:
    """Данные темы плеера (theme_data) → как выглядит режим клипа."""
    d = d or {}
    bg, text = d.get("bg", "#07080c"), d.get("text", "#f2f4fa")
    acc, acc2 = d.get("accent", "#ffffff"), d.get("accent2", "#c9ccd6")
    light = QColor(bg).value() > 170
    st = {"family": "glass", "bg": bg, "text": text, "muted": d.get("muted", "#8d93a5"), "accent": acc,
          "accent2": acc2, "panel": _rgba("#ffffff", 0.07) if not light else _rgba("#000000", 0.06),
          "line": _rgba("#ffffff", 0.13) if not light else _rgba("#000000", 0.12), "radius": 16, "btn_radius": 12,
          "font": d.get("_font_family") or "Segoe UI", "frame": "rounded", "frame_radius": 18, "border": "",
          "border_w": 0, "glow": 0.55, "glow_color": "", "scale": 0.84, "fit": "contain", "bg_mode": "cover",
          "dim": 0.55, "controls": "full", "show_title": True, "fx": [], "fx_amount": 0.7, "light": light,
          "solid_color": bg}
    if d.get("winamp"):
        st.update(family="winamp", bg="#20202e", text="#00e000", muted="#00a000", accent="#00e000", accent2="#d8d020",
                  radius=0, btn_radius=0, font="Courier New", frame="square", frame_radius=0, glow=0.0,
                  bg_mode="cover", dim=0.6, scale=0.86)
    elif d.get("yamusic"):
        st.update(family="ya", bg="#0a0a0b", text="#ffffff", muted="#a0a0a6", accent=d.get("accent", "#ffdb4d"),
                  panel=_rgba("#ffffff", 0.06), line=_rgba("#ffffff", 0.12), radius=20, btn_radius=20,
                  frame_radius=20, glow=0.0, bg_mode="cover", dim=0.62, scale=0.86)   # стекло, без жёлтого свечения
    elif d.get("fluid"):
        dark = not light
        st.update(family="fluid", accent="#FF8A00", accent2=acc2, panel=d.get("panel2") or d.get("panel", bg),
                  line=_rgba("#000000" if light else "#ffffff", 0.10), radius=22, btn_radius=18,
                  frame="rounded", frame_radius=28, border=d.get("panel", "#f5efe4") if light else "#2a241c",
                  border_w=10, glow=0.25, bg_mode="color", dim=0.0, scale=0.8, solid_color=bg,
                  font=d.get("_title_family") or st["font"])
        st["light"] = not dark
    if d.get("custom"):
        st["family"] = "custom"
        ct = d.get("ct") or {}
        c = ct.get("clip") or {}
        if c.get("on"):
            st.update(bg_mode=c.get("bg", "cover"), solid_color=c.get("color") or bg, dim=float(c.get("dim", 0.55)),
                      frame=c.get("frame", "rounded"), frame_radius=float(c.get("radius", 18)),
                      border=c.get("border", ""), border_w=float(c.get("border_w", 0)), glow=float(c.get("glow", 0.5)),
                      glow_color=c.get("glow_color", ""), scale=float(c.get("scale", 0.82)),
                      fit=c.get("fit", "contain"), controls=c.get("controls", "full"),
                      show_title=bool(c.get("title", True)), fx=list(c.get("fx") or []),
                      fx_amount=float(c.get("fx_amount", 0.7)))
            if c.get("accent"):
                st["accent"] = c["accent"]
            if c.get("text"):
                st["text"] = c["text"]
            if c.get("panel"):
                st["panel"] = c["panel"]
    return st


def _qss(st: dict) -> str:
    f = st["font"]
    if st["family"] == "winamp":
        bevel = ("background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #e6e6ee,stop:1 #8a8aa0);color:#10101a;"
                 "border-top:1px solid #fff;border-left:1px solid #fff;border-bottom:1px solid #2a2a36;"
                 "border-right:1px solid #2a2a36;border-radius:0;")
        return f"""
        QWidget {{ font-family: "Tahoma", "Segoe UI"; font-size: 11px; }}
        QFrame#ClipBar, QFrame#ClipFx {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #3b3b52,stop:1 #20202e);
            border-top:1px solid #6a6a88; border-left:1px solid #6a6a88; border-bottom:1px solid #0a0a12;
            border-right:1px solid #0a0a12; border-radius:0; }}
        QLabel {{ background: transparent; color: #e8e8f4; }}
        QLabel#ClipTitle, QLabel#ClipLcd {{ background:#000; color:#00e000; font-family:"Courier New";
            font-weight:bold; font-size:13px; padding:3px 8px; border:1px solid #0a0a12; }}
        QLabel#ClipSub {{ color:#00a000; font-family:"Courier New"; font-size:11px; background:#000; padding:2px 6px; }}
        QLabel#ClipHead {{ color:#c8b46a; font-weight:bold; letter-spacing:1px; }}
        QPushButton {{ {bevel} padding:3px 9px; font-weight:bold; font-size:11px; min-height:18px; }}
        QPushButton:pressed, QPushButton:checked {{ background:#8a8aa0; border-top:1px solid #2a2a36;
            border-left:1px solid #2a2a36; border-bottom:1px solid #fff; border-right:1px solid #fff; color:#003a00; }}
        QSlider::groove:horizontal {{ height:6px; background:#0c0c14; border:1px solid #2a2a36; }}
        QSlider::sub-page:horizontal {{ background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #1e8f1e,stop:1 #d8d020); }}
        QSlider::handle:horizontal {{ width:24px; margin:-5px 0; {bevel} }}
        QScrollArea {{ background:transparent; border:none; }}
        QScrollArea > QWidget > QWidget {{ background:transparent; }}
        QMenu {{ background:#2b2b3c; color:#e8e8f4; border:1px solid #6a6a88; }}
        QMenu::item:selected {{ background:#00004a; color:#00e000; }}
        """
    acc, text, muted, panel, line = st["accent"], st["text"], st["muted"], st["panel"], st["line"]
    on_acc = "#0b0b12" if QColor(acc).value() > 150 and QColor(acc).saturation() < 120 or QColor(acc).lightness() > 150 \
        else "#ffffff"
    br = st["btn_radius"]
    big = "font-weight:800; text-transform:uppercase; letter-spacing:1px;" if st["family"] == "fluid" else ""
    return f"""
    QWidget {{ font-family: "{f}", "Segoe UI Variable", "Segoe UI"; font-size: 13px; color: {text}; }}
    QFrame#ClipBar, QFrame#ClipFx {{ background: {panel}; border: 1px solid {line}; border-radius: {st['radius']}px; }}
    QLabel {{ background: transparent; color: {text}; }}
    QLabel#ClipTitle {{ font-size: 15px; font-weight: 800; {big} }}
    QLabel#ClipSub, QLabel#ClipLcd {{ color: {muted}; font-size: 12px; }}
    QLabel#ClipHead {{ color: {muted}; font-size: 11px; font-weight: 700; letter-spacing: 1px; }}
    QPushButton {{ background: {_rgba(text, 0.07)}; border: 1px solid {line}; border-radius: {br}px;
        padding: 6px 12px; color: {text}; font-weight: 600; }}
    QPushButton:hover {{ border-color: {acc}; background: {_rgba(text, 0.12)}; }}
    QPushButton:checked {{ background: {acc}; color: {on_acc}; border-color: {acc}; }}
    QPushButton#ClipPrimary {{ background: {acc}; color: {on_acc}; border: none; font-weight: 800; }}
    QPushButton#ClipPrimary:hover {{ background: {QColor(acc).lighter(115).name()}; }}
    QPushButton#ClipFlash {{ border-color: #ff4d6d; }}
    QPushButton#ClipFlash:checked {{ background: #ff2d55; color: #ffffff; }}
    QSlider::groove:horizontal {{ height: 4px; background: {_rgba(text, 0.18)}; border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {acc}; border-radius: 2px; }}
    QSlider::handle:horizontal {{ width: 14px; height: 14px; margin: -5px 0; background: {text}; border-radius: 7px; }}
    QScrollArea {{ background: transparent; border: none; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 6px; }}
    QScrollBar::handle:vertical {{ background: {line}; border-radius: 3px; }}
    QMenu {{ background: {st['bg']}; color: {text}; border: 1px solid {line}; padding: 4px; }}
    QMenu::item {{ padding: 6px 14px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {_rgba(acc, 0.25)}; }}
    """


# ------------------------------------------------------------------ #
#  Скачивание клипов (в т.ч. без открытого режима)                    #
# ------------------------------------------------------------------ #

class ClipService(QObject):
    """Очередь скачивания клипов. changed(ключ трека) — прогресс/готово/ошибка."""

    changed = pyqtSignal(str)

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.jobs: dict = {}
        self.state: dict = {}                    # ключ → (доля, текст, ошибка)

    def _ui(self, fn):
        try:
            self.win._ui_bridge.call.emit(fn)
        except RuntimeError:
            pass

    def busy(self, t) -> bool:
        j = self.jobs.get(CF.track_key(t))
        return j is not None and j.is_alive()

    def streaming(self) -> bool:
        return bool((self.win.settings or {}).get("clips_stream", True))

    def start(self, t, cand=None, force=False, save=False):
        """Найти клип трека. save=True — скачать на компьютер; иначе (по умолчанию) — онлайн, без скачивания."""
        key = CF.track_key(t)
        if self.busy(t):
            return
        if not force and cand is None:
            e = CF.lookup(t)
            if e and (e.get("path") or e.get("none") or (e.get("stream") and not save)):
                return
        q = int((self.win.settings or {}).get("clips_quality", 720))
        tt = dict(t)
        if str(tt.get("path", "")).startswith("stream:"):  # трек сам играет онлайн — его звук для синхронизации
            page = tt.get("url")
            try:
                page = page or self.win.streams.info(tt["path"]).get("page")
            except Exception:                              # noqa: BLE001
                pass
            if page:
                tt["src_page"] = page

        def prog(f, s, k=key):
            self._ui(lambda: self._set(k, f, s, ""))

        def done(entry, err, k=key):
            self._ui(lambda: self._set(k, 1.0, "" if entry else err, err))
        cls = CF.Fetch if (save or not self.streaming()) else CF.StreamFetch
        job = cls(tt, q, cand, on_progress=prog, on_done=done)
        self.jobs[key] = job
        self.state[key] = (0.0, "Скачиваю клип…" if cls is CF.Fetch and cand else "Ищу официальный клип…", "")
        self.changed.emit(key)
        job.start()

    def cancel(self, t):
        j = self.jobs.get(CF.track_key(t))
        if j is not None:
            j.cancel.set()

    def _set(self, key, f, s, err):
        self.state[key] = (f, s, err)
        self.changed.emit(key)


# ------------------------------------------------------------------ #
#  Кадр видео ↔ numpy                                                 #
# ------------------------------------------------------------------ #

def _to_bgr(img: QImage) -> np.ndarray:
    if img.format() not in (QImage.Format.Format_RGB32, QImage.Format.Format_ARGB32,
                            QImage.Format.Format_ARGB32_Premultiplied):
        img = img.convertToFormat(QImage.Format.Format_RGB32)
    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes())
    a = np.frombuffer(ptr, np.uint8).reshape(img.height(), img.bytesPerLine() // 4, 4)
    return a[:, :img.width(), :3].copy()


def _from_bgr(a: np.ndarray) -> QImage:
    h, w = a.shape[:2]
    buf = np.empty((h, w, 4), np.uint8)
    buf[..., :3] = a
    buf[..., 3] = 255
    return QImage(buf.data, w, h, w * 4, QImage.Format.Format_RGB32).copy()


# ------------------------------------------------------------------ #
#  Режим клипа                                                        #
# ------------------------------------------------------------------ #

class ClipMode(QWidget):
    closed = pyqtSignal()

    BAR_H = 58

    def __init__(self, win, service: ClipService):
        super().__init__(win.centralWidget())
        self.win = win
        self.svc = service
        self.setObjectName("ClipMode")
        self.setProperty("echoesEditor", True)            # режим «только слои» не прячет этот оверлей
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.st = clip_style(None)
        self.fx = ClipFX()
        self.track = None
        self.entry = None
        self.src = None                          # theme_layers.VideoSource
        self._src_path = None
        self._frame = None                       # готовый кадр (QImage) для рисования
        self._frame_no = -1
        self._fx_t = 0.0
        self._ambient = QColor("#202028")
        self._amb_n = 0
        self._cover = None
        self._cover_path = None
        self._bg = None
        self._bg_key = None
        self._theme_snap = None
        self._theme_snap_t = 0.0
        self._op = 0.0
        self._anim = None
        self._vpaused = None
        self._rate = 1.0
        self._last_sync = 0.0
        self._last_move = time.monotonic()
        self._t0 = time.monotonic()
        self._last = time.perf_counter()
        self._flash_ok = False
        self._seeking = False
        from theme_layers import AudioPulse
        self.pulse = AudioPulse(win.engine)
        self._build()
        self.svc.changed.connect(self._svc_changed)
        self._timer = FrameTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        from PyQt6.QtWidgets import QApplication
        QApplication.instance().aboutToQuit.connect(self._stop_video)
        if self.parentWidget() is not None:
            self.parentWidget().installEventFilter(self)  # окно меняет размер — оверлей следом
        self.hide()

    def eventFilter(self, obj, ev):
        from PyQt6.QtCore import QEvent
        if obj is self.parentWidget() and ev.type() == QEvent.Type.Resize and self.isVisible():
            self.setGeometry(obj.rect())
        return False

    def _keep_on_top(self):
        """Темы Winamp и Echoes Music строят свои окна/оболочку позже — режим клипа должен быть над ними."""
        p = self.parentWidget()
        if p is None:
            return
        kids = [c for c in p.children() if isinstance(c, QWidget) and not c.isWindow() and c.isVisible()]
        if kids and kids[-1] is not self:
            self.raise_()
        if self.geometry() != p.rect():
            self.setGeometry(p.rect())

    # ── интерфейс ── #

    def _btn(self, text, tip="", fn=None, checkable=False, primary=False):
        b = QPushButton(text)
        b.setToolTip(tip)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setCheckable(checkable)
        if primary:
            b.setObjectName("ClipPrimary")
        if fn is not None:
            b.clicked.connect(fn)
        return b

    def _build(self):
        self.top = QFrame(self)
        self.top.setObjectName("ClipBar")
        th = QHBoxLayout(self.top)
        th.setContentsMargins(14, 8, 10, 8)
        th.setSpacing(8)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.l_title = QLabel("—")
        self.l_title.setObjectName("ClipTitle")
        self.l_status = QLabel("")
        self.l_status.setObjectName("ClipSub")
        col.addWidget(self.l_title)
        col.addWidget(self.l_status)
        th.addLayout(col, 1)
        self.b_dl = self._btn("Скачать клип", "Найти и скачать официальный клип этой песни", self._download,
                              primary=True)
        self.b_other = self._btn("Другой ▾", "Выбрать другой ролик из найденных или удалить этот")
        self.b_other.clicked.connect(self._other_menu)
        self.b_fx = self._btn("Эффекты", "Психоделические эффекты поверх клипа (E)", checkable=True)
        self.b_fx.toggled.connect(self._show_fx)
        self.b_full = self._btn("⛶", "Полный экран (F11)", lambda: getattr(self.win, "_toggle_fullscreen")())
        self.b_close = self._btn("✕", "Закрыть клип (Esc)", self.close_mode)
        for b in (self.b_dl, self.b_other, self.b_fx, self.b_full, self.b_close):
            th.addWidget(b)

        self.bottom = QFrame(self)
        self.bottom.setObjectName("ClipBar")
        bh = QHBoxLayout(self.bottom)
        bh.setContentsMargins(12, 8, 12, 8)
        bh.setSpacing(8)
        self.b_prev = self._btn("◀◀", "Предыдущий", lambda: self.win.prev_track())
        self.b_play = self._btn("▶", "Играть / пауза (пробел)", lambda: self.win.toggle_play(), primary=True)
        self.b_play.setFixedWidth(52)
        self.b_next = self._btn("▶▶", "Следующий", lambda: self.win.next_track())
        self.l_cur = QLabel("0:00")
        self.l_cur.setObjectName("ClipLcd")
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 1000)
        self.seek.sliderPressed.connect(lambda: setattr(self, "_seeking", True))
        self.seek.sliderReleased.connect(self._seek_release)
        self.l_tot = QLabel("0:00")
        self.l_tot.setObjectName("ClipLcd")
        self.l_sync = QLabel("")
        self.l_sync.setObjectName("ClipSub")
        self.l_sync.setToolTip("Сдвиг клипа относительно песни")
        self.b_sm = self._btn("−", "Клип раньше на 0,25 с", lambda: self._nudge(-0.25))
        self.b_sp = self._btn("+", "Клип позже на 0,25 с", lambda: self._nudge(0.25))
        self.b_sa = self._btn("⟲", "Подобрать сдвиг заново по звуку", self._realign)
        bh.addStretch(0)                                   # без перемотки («только кнопки») — кнопки по центру
        for w in (self.b_prev, self.b_play, self.b_next, self.l_cur):
            bh.addWidget(w)
        bh.addWidget(self.seek, 1)
        for w in (self.l_tot, self.l_sync, self.b_sm, self.b_sp, self.b_sa):
            bh.addWidget(w)
        bh.addStretch(0)
        for b in (self.b_prev, self.b_next, self.b_sm, self.b_sp, self.b_sa):
            b.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._sync_ws = (self.l_sync, self.b_sm, self.b_sp, self.b_sa)
        self._seek_ws = (self.l_cur, self.seek, self.l_tot)

        self.fxp = QFrame(self)
        self.fxp.setObjectName("ClipFx")
        fv = QVBoxLayout(self.fxp)
        fv.setContentsMargins(12, 10, 12, 10)
        fv.setSpacing(8)
        h = QLabel("ЭФФЕКТЫ")
        h.setObjectName("ClipHead")
        fv.addWidget(h)
        inner = QWidget()
        g = QGridLayout(inner)
        g.setContentsMargins(0, 0, 0, 0)
        g.setSpacing(5)
        self._fx_btns = {}
        for n, (key, title, flash) in enumerate(EFFECTS):
            b = self._btn(title + (" !" if flash else ""), "Быстрые вспышки!" if flash else "", checkable=True)
            if flash:
                b.setObjectName("ClipFlash")
            b.clicked.connect(lambda on, k=key: self._fx_toggle(k, on))
            b.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)   # длинное название не распирает
            g.addWidget(b, n // 2, n % 2)
            self._fx_btns[key] = b
        g.setColumnStretch(0, 1)
        g.setColumnStretch(1, 1)
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sa.setWidget(inner)
        fv.addWidget(sa, 1)
        h2 = QLabel("НАБОРЫ")
        h2.setObjectName("ClipHead")
        fv.addWidget(h2)
        pg = QGridLayout()
        pg.setSpacing(5)
        for n, (key, title, keys) in enumerate(PRESETS):
            b = self._btn(title, ", ".join(keys), lambda _=False, ks=keys: self._fx_preset(ks))
            if key == "nightmare":
                b.setObjectName("ClipFlash")
            b.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            pg.addWidget(b, n // 2, n % 2)
        fv.addLayout(pg)
        self.s_amount = self._slider_row(fv, "Сила", 70, lambda v: setattr(self.fx, "amount", v / 100))
        self.s_react = self._slider_row(fv, "Реакция на музыку", 70, lambda v: setattr(self.fx, "react", v / 100))
        off = self._btn("Выключить все эффекты", "", self._fx_clear)
        fv.addWidget(off)
        self.fxp.hide()

        self.b_big = self._btn("Скачать официальный клип", "", self._download, primary=True)
        self.b_big.setParent(self)
        self.b_big.hide()

    def _slider_row(self, lay, text, val, fn):
        row = QHBoxLayout()
        lb = QLabel(text)
        lb.setObjectName("ClipSub")
        s = QSlider(Qt.Orientation.Horizontal)
        s.setRange(0, 100)
        s.setValue(val)
        s.valueChanged.connect(fn)
        row.addWidget(lb)
        row.addWidget(s, 1)
        lay.addLayout(row)
        return s

    # ── тема ── #

    def apply_theme(self, theme_data):
        self.st = clip_style(theme_data)
        self.setStyleSheet(_qss(self.st))
        self._bg = None
        self._layout_children()
        if self.isVisible():
            self._keep_on_top()
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, self._keep_on_top)       # оболочка новой темы появится чуть позже
            self.update()

    # ── открытие / закрытие ── #

    def is_open(self) -> bool:
        return self.isVisible() and self._op > 0.0

    def open(self):
        p = self.parentWidget()
        if p is not None:
            self.setGeometry(p.rect())
        self.setStyleSheet(_qss(self.st))
        self._layout_children()
        self.show()
        self.raise_()
        self.setFocus()
        self.track_changed()
        if self.st.get("fx") and not self.fx.active():
            self.fx.amount = self.st.get("fx_amount", 0.7)
            self.s_amount.setValue(int(self.fx.amount * 100))
            if any(k in FLASHY for k in self.st["fx"]) and not self._warn_flash():
                self.fx.preset([k for k in self.st["fx"] if k not in FLASHY])
            else:
                self.fx.preset(self.st["fx"])
            self._sync_fx_btns()
        self._last = time.perf_counter()
        self._timer.start()
        self._fade(1.0)

    def close_mode(self):
        self._fade(0.0, done=self._finish_close)

    def _finish_close(self):
        self.hide()
        self._timer.stop()
        self._stop_video()
        self.closed.emit()

    def _fade(self, to, done=None):
        if self._anim is not None:
            self._anim.stop()
        a = QVariantAnimation(self)
        a.setDuration(260)
        a.setStartValue(float(self._op))
        a.setEndValue(float(to))
        a.setEasingCurve(QEasingCurve.Type.OutCubic)
        a.valueChanged.connect(lambda v: (setattr(self, "_op", float(v)), self.update()))
        if done:
            a.finished.connect(done)
        a.start()
        self._anim = a

    # ── трек и клип ── #

    def _current(self):
        fn = getattr(self.win, "_current_track", None)
        try:
            return fn() if fn else None
        except Exception:                                  # noqa: BLE001
            return None

    def track_changed(self):
        t = self._current()
        self.track = dict(t) if t else None
        self.entry = CF.lookup(t) if t else None
        cov = (t or {}).get("cover") or ""
        if cov != self._cover_path:
            self._cover_path = cov
            pm = load_pixmap(cov, 2048) if cov else QPixmap()
            self._cover = pm if not pm.isNull() else None
            self._bg = None
        self.l_title.setText(((t or {}).get("artist", "") + "  —  " if (t or {}).get("artist") else "") +
                             ((t or {}).get("title", "") or "—"))
        self._frame = None
        if self.entry and (self.entry.get("path") or self.entry.get("stream")):
            self._open_entry()
        else:
            self._stop_video()
            if t and (self.win.settings or {}).get("clips_auto") and not (self.entry or {}).get("none"):
                self.svc.start(t)
        self._refresh_status()
        self._layout_children()
        self.update()

    def _svc_changed(self, key):
        if not self.track or CF.track_key(self.track) != key:
            return
        f, s, err = self.svc.state.get(key, (0, "", ""))
        if f >= 1.0:
            self.entry = CF.lookup(self.track)
            if self.entry and (self.entry.get("path") or self.entry.get("stream")):
                self._open_entry(force=True)
        self._refresh_status()
        self._layout_children()
        self.update()

    def _refresh_status(self):
        t = self.track
        has = bool(self.entry and (self.entry.get("path") or self.entry.get("stream")))
        busy = bool(t) and self.svc.busy(t)
        if busy:
            f, s, _e = self.svc.state.get(CF.track_key(t), (0, "", ""))
            self.l_status.setText(f"{s}  {int(f * 100)}%" if f > 0.01 else s)
        elif has:
            off = float(self.entry.get("offset") or 0)
            self.l_status.setText((self.entry.get("title") or "")[:70] +
                                  ("" if self.entry.get("path") else "  ·  онлайн"))
            self.l_sync.setText(f"{off:+.2f} с")
        elif t:
            _f, s, err = self.svc.state.get(CF.track_key(t), (0, "", ""))
            none = (self.entry or {}).get("none")
            self.l_status.setText(err or ("Официальный клип не нашёлся — можно выбрать ролик вручную" if none else
                                          ("Клип ещё не найден" if self.svc.streaming() else
                                           "Клип ещё не скачан")))
        else:
            self.l_status.setText("Ничего не играет")
        self.b_dl.setVisible(not has and not busy and bool(t))
        self.b_dl.setText("Искать снова" if (self.entry or {}).get("none") else
                          ("Найти клип" if self.svc.streaming() else "Скачать клип"))
        self.b_other.setVisible(bool(t) and not busy)
        for w in self._sync_ws:
            w.setVisible(has and self.st["controls"] == "full")
        self.b_big.setVisible(not has and not busy and bool(t))

    def _download(self):
        if self.track:
            self.svc.start(self.track, force=True)
            self._refresh_status()

    def _other_menu(self):
        if not self.track:
            return
        m = QMenu(self)
        m.setStyleSheet(_qss(self.st))
        e = CF.lookup(self.track) or {}
        cands = e.get("cands") or []
        if cands:
            for c in cands[:6]:
                mark = "✓ " if c.get("url") == e.get("url") else ""
                a = m.addAction(f"{mark}{c['title'][:64]}  ·  {c.get('channel', '')[:24]}  ({_fmt(c.get('duration', 0))})")
                a.triggered.connect(lambda _=False, c=c: self._pick(c))
            m.addSeparator()
        m.addAction("Найти заново", self._research)
        m.addAction("Поиск клипа вручную…", self._manual_search)
        if e.get("stream") and not e.get("path") and e.get("url"):
            m.addAction("Сохранить клип на компьютер", self._save_local)
        if e.get("path"):
            m.addAction("Удалить скачанный клип", self._delete)
        m.exec(self.b_other.mapToGlobal(self.b_other.rect().bottomLeft()))

    def _manual_search(self):
        """Свой запрос → живой список роликов YouTube с превью → выбранный скачивается как клип трека."""
        t = self.track
        if not t:
            return
        a, ti = (t.get("artist") or "").strip(), (t.get("title") or "").strip()
        guess = f"{a} - {ti}" if a and a.lower() not in ("unknown artist", "неизвестный исполнитель") else ti
        from manual_search import ClipSearchDialog
        dlg = ClipSearchDialog(self.window(), guess, ti, a, float(t.get("duration") or 0))
        if dlg.exec() and dlg.result and self.track and CF.track_key(t) == CF.track_key(self.track):
            self._pick(dlg.result)                             # тот же трек (объект мог обновиться)

    def _save_local(self):
        """Онлайн-клип → скачать файлом (для офлайна)."""
        e = CF.lookup(self.track) or {}
        url = e.get("url") or ""
        import re as _re
        m = _re.search(r"(?:v=|youtu\.be/|/shorts/)([\w-]{6,})", url)
        if not m:
            return
        cand = {"id": m.group(1), "url": url, "title": e.get("title") or ""}
        self.svc.start(self.track, cand=cand, force=True, save=True)
        self._refresh_status()

    def _open_entry(self, force=False):
        """Клип: скачанный файл или онлайн-поток (ссылка устарела — достать новую в фоне)."""
        e = self.entry or {}
        src = CF.stream_source(e)
        if src:
            if force and src != self._src_path:
                self._stop_video()
            self._start_video(src)
            return
        if not e.get("stream"):
            return
        self._stop_video()
        self.l_status.setText("Подключаю клип…")
        t = dict(self.track or {})
        q = int((self.win.settings or {}).get("clips_quality", 720))

        def got(url):
            def apply():
                if url and self.track and CF.track_key(self.track) == CF.track_key(t):
                    self.entry = CF.lookup(self.track)
                    self._start_video(url)
                    self._refresh_status()
                elif not url:
                    self.l_status.setText("Клип сейчас недоступен — попробуйте «Другой клип»")
            self.svc._ui(apply)
        CF.refresh_stream_async(t, e, q, got)

    def _pick(self, cand):
        self._stop_video()
        self.svc.start(self.track, cand=cand, force=True)
        self._refresh_status()

    def _research(self):
        CF.update(self.track, none=False, cands=[])
        self._stop_video()
        self.entry = None
        self.svc.start(self.track, force=True)
        self._refresh_status()

    def _delete(self):
        self._stop_video()
        CF.forget(self.track)
        self.entry = None
        self._refresh_status()
        self.update()

    # ── видео ── #

    def _start_video(self, path):
        if path == self._src_path and self.src is not None:
            return
        self._stop_video()
        from theme_layers import VideoSource
        r = self._video_rect()
        dpr = self.devicePixelRatioF()
        hw = bool((self.win.settings or {}).get("video_hw", True))
        self.src = VideoSource(path, self, hw=hw, target=(max(64, r.width() * dpr), max(36, r.height() * dpr)))
        if not self.src.ok:
            self.src.stop()
            self.src = None
            self.l_status.setText("Не получилось открыть видео (нет VLC?)")
            return
        self._src_path = path
        self._vpaused = None
        self._frame_no = -1
        self._last_sync = 0.0

    def _stop_video(self):
        if self.src is not None:
            try:
                self.src.stop()
                self.src.deleteLater()
            except RuntimeError:
                pass
        self.src = None
        self._src_path = None
        self._frame = None

    def _offset(self) -> float:
        return float((self.entry or {}).get("offset") or 0.0)

    def _nudge(self, d):
        if not self.track or not self.entry:
            return
        off = round(self._offset() + d, 2)
        self.entry["offset"] = off
        CF.update(self.track, offset=off)
        self._last_sync = 0.0
        self._refresh_status()

    def _realign(self):
        if not self.track or not self.entry or not (self.entry.get("path") or self.entry.get("stream")):
            return
        t, path, ent = dict(self.track), self.entry.get("path"), dict(self.entry)
        if str(t.get("path", "")).startswith("stream:"):
            t["src_page"] = t.get("url") or ""
        self.l_status.setText("Слушаю клип и трек…")

        def work():
            try:
                off, conf = CF.align(str(t.get("path") or ""), path) if path else CF.realign_stream(t, ent)
            except Exception as e:                         # noqa: BLE001
                print("[clip] realign:", e)
                off, conf = 0.0, 0.0
            if conf < 4.0:
                self.svc._ui(lambda: self.l_status.setText("По звуку подобрать не вышло — подвиньте −/+"))
                return
            CF.update(t, offset=off, conf=round(conf, 1))

            def apply():
                if self.track and CF.track_key(self.track) == CF.track_key(t):
                    self.entry = CF.lookup(self.track)
                    self._last_sync = 0.0
                    self._refresh_status()
            self.svc._ui(apply)
        import threading
        threading.Thread(target=work, daemon=True).start()

    def _sync(self):
        """Клип — вровень с песней: пауза, перемотка, скорость, сдвиг."""
        src = self.src
        if src is None or src.p is None:
            return
        eng = self.win.engine
        try:
            pos = float(eng.get_position() or 0)
            playing = bool(eng.is_playing())
        except Exception:                                  # noqa: BLE001
            return
        want = pos + self._offset()
        p = src.p
        try:
            length = (p.get_length() or 0) / 1000.0
            vt = (p.get_time() or 0) / 1000.0
        except Exception:                                  # noqa: BLE001
            return
        play = playing and want >= 0 and not (length > 0 and want >= length - 0.15)
        tgt = min(max(0.0, want), max(0.0, length - 0.2)) if length > 0 else max(0.0, want)
        if play != (self._vpaused is False):
            src.set_paused(not play)
            self._vpaused = not play
        tol = 0.30 if play else 0.6
        net = str(self._src_path or "").startswith(("http://", "https://"))
        if net:
            tol += 0.15                                    # поток: перемотка по сети — не дёргать по мелочи
        now = time.perf_counter()
        if length > 0 and abs(vt - tgt) > tol and now >= getattr(self, "_seek_cool", 0.0):
            try:
                p.set_time(int(tgt * 1000))
                self._seek_cool = now + (1.0 if net else 0.0)
            except Exception:                              # noqa: BLE001
                pass
        try:
            rate = float(self.win.speed.value()) / 100.0
        except Exception:                                  # noqa: BLE001
            rate = 1.0
        if abs(rate - self._rate) > 0.01:
            self._rate = rate
            src.set_rate(rate)

    # ── кадры ── #

    def _tick(self):
        now = time.perf_counter()
        dt = min(0.1, now - self._last)
        self._last = now
        if self.win.isMinimized():
            return
        self._keep_on_top()
        self.pulse.update(dt)
        if self.src is not None and now - self._last_sync > 0.3:
            self._last_sync = now
            self._sync()
        src = self.src
        new = src is not None and src.valid() and src.frame_no != self._frame_no
        fx_due = self.fx.active() and now - self._fx_t >= (1 / 60.0 if _CFX.NATIVE else 1 / 40.0)
        if new or (fx_due and src is not None and src.valid()):
            self._frame_no = src.frame_no
            self._fx_t = now
            self._make_frame(src.img, dt)
        elif fx_due and src is None and self._cover is not None:      # эффекты и без клипа — по обложке
            self._fx_t = now
            self._make_frame(self._cover.toImage(), dt)
        # плавающие панели: «прятать, пока не нужна»
        if self.st["controls"] == "autohide":
            vis = time.monotonic() - self._last_move < 2.5 or self.fxp.isVisible()
            for w in (self.top, self.bottom):
                if w.isVisible() != vis:
                    w.setVisible(vis)
        self._update_transport()
        if self.st["bg_mode"] == "theme" and time.monotonic() - self._theme_snap_t > 2.0:
            self._snap_theme()
        self.update()

    def _make_frame(self, img: QImage, dt):
        if img is None or img.isNull():
            return
        if self.fx.active() and _CFX.NATIVE:
            # C++: эффекты над кадром почти полного разрешения, без GIL. Ширина подстраивается под
            # стопку: кадр эффектов должен укладываться в ~9 мс (640…1280 px)
            fw = int(getattr(self, "_fx_w", 1280))
            w = img.width()
            if w > fw:
                img = img.scaled(fw, max(2, int(img.height() * fw / w)), Qt.AspectRatioMode.IgnoreAspectRatio,
                                 Qt.TransformationMode.FastTransformation)
            frame = img.convertToFormat(QImage.Format.Format_RGB32)
            if frame.cacheKey() == img.cacheKey():
                frame = img.copy()
            pl = self.pulse
            t0 = time.perf_counter()
            try:
                self._frame = self.fx.process_image(frame, time.monotonic() - self._t0, dt, pl.bass, pl.level, pl.beat)
            except Exception as e:                         # noqa: BLE001
                print("[clip fx]", e)
                self._frame = img.copy()
            cost = self._fx_cost = getattr(self, "_fx_cost", 6.0) * 0.85 + (time.perf_counter() - t0) * 1000 * 0.15
            if cost > 9.0 and fw > 640:
                self._fx_w = fw - 32
            elif cost < 5.0 and fw < 1280:
                self._fx_w = fw + 16
        elif self.fx.active():
            w = img.width()
            if w > 640:
                img = img.scaled(640, max(2, int(img.height() * 640 / w)), Qt.AspectRatioMode.IgnoreAspectRatio,
                                 Qt.TransformationMode.FastTransformation)
            a = _to_bgr(img)
            pl = self.pulse
            try:
                a = self.fx.process(a, time.monotonic() - self._t0, dt, pl.bass, pl.level, pl.beat)
                self._frame = _from_bgr(a)
            except Exception as e:                         # noqa: BLE001
                print("[clip fx]", e)
                self._frame = img.copy()
        else:
            self._frame = img.copy() if img is not getattr(self.src, "img", None) else QImage(img)
        self._amb_n += 1
        if self._amb_n % 8 == 0:                           # средний цвет кадра — для свечения и фона
            s = self._frame.scaled(4, 4, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
            r = g = b = 0
            for y in range(4):
                for x in range(4):
                    c = s.pixelColor(x, y)
                    r, g, b = r + c.red(), g + c.green(), b + c.blue()
            nc = QColor(r // 16, g // 16, b // 16)
            k = 0.25
            self._ambient = QColor(int(self._ambient.red() + (nc.red() - self._ambient.red()) * k),
                                   int(self._ambient.green() + (nc.green() - self._ambient.green()) * k),
                                   int(self._ambient.blue() + (nc.blue() - self._ambient.blue()) * k))
            if self.st["bg_mode"] == "ambient":
                self._bg = None

    def _update_transport(self):
        eng = self.win.engine
        try:
            pos = float(eng.get_position() or 0)
            dur = float(eng.duration or 0)
            playing = bool(eng.is_playing())
        except Exception:                                  # noqa: BLE001
            return
        self.b_play.setText("❚❚" if playing else "▶")
        if not self._seeking:
            self.seek.blockSignals(True)
            self.seek.setValue(int(pos / dur * 1000) if dur > 0 else 0)
            self.seek.blockSignals(False)
        self.l_cur.setText(_fmt(self.seek.value() / 1000 * dur if self._seeking else pos))
        self.l_tot.setText(_fmt(dur))

    def _seek_release(self):
        self._seeking = False
        try:
            dur = float(self.win.engine.duration or 0)
            if dur > 0:
                self.win.engine.seek(self.seek.value() / 1000 * dur)
        except Exception:                                  # noqa: BLE001
            pass
        self._last_sync = 0.0

    # ── эффекты ── #

    def _warn_flash(self) -> bool:
        if self._flash_ok or (self.win.settings or {}).get("clip_flash_ok"):
            self._flash_ok = True
            return True
        r = QMessageBox.warning(
            self, "Вспышки",
            "Этот эффект — быстрые мигания и вспышки света.\n\n"
            "Они могут вызвать приступ у людей со светочувствительной эпилепсией и неприятны многим. "
            "Если вам или кому-то рядом это опасно — не включайте.\n\nВключить вспышки?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        self._flash_ok = r == QMessageBox.StandardButton.Yes
        return self._flash_ok

    def _fx_toggle(self, key, on):
        if on and key in FLASHY and not self._warn_flash():
            self._fx_btns[key].setChecked(False)
            return
        self.fx.set(key, on)

    def _fx_preset(self, keys):
        if any(k in FLASHY for k in keys) and not self._warn_flash():
            keys = [k for k in keys if k not in FLASHY]
        self.fx.preset(keys)
        self._sync_fx_btns()

    def _fx_clear(self):
        self.fx.clear()
        self._sync_fx_btns()
        self._frame_no = -1

    def _sync_fx_btns(self):
        for k, b in self._fx_btns.items():
            b.blockSignals(True)
            b.setChecked(k in self.fx.on)
            b.blockSignals(False)

    def _show_fx(self, on):
        self.fxp.setVisible(bool(on))
        self._layout_children()
        self.update()

    # ── геометрия ── #

    def _area(self) -> QRectF:
        """Место под видео: между панелями (и левее панели эффектов)."""
        W, H = self.width(), self.height()
        top = 14 + (self.BAR_H + 10 if self.top.isVisible() or self.st["controls"] != "autohide" else 0)
        bot = 14 + (self.BAR_H + 10 if self.bottom.isVisible() or self.st["controls"] != "autohide" else 0)
        right = (self.fxp.width() + 24) if self.fxp.isVisible() else 0
        return QRectF(14, top, max(40, W - 28 - right), max(40, H - top - bot))

    def _aspect(self) -> float:
        src = self.src
        if src is not None:
            ss = src.source_size()
            if ss.width() > 0 and ss.height() > 0:
                return ss.width() / ss.height()
        return 16 / 9

    def _video_rect(self) -> QRectF:
        area = self._area()
        k = float(self.st.get("scale", 0.84))
        fam = self.st["family"]
        pad = {"winamp": 30, "polaroid": 26}.get(fam if fam == "winamp" else self.st["frame"], 0)
        if self.st["frame"] == "tv":
            pad = max(pad, 34)
        aw, ah = area.width() * k - 2 * pad, area.height() * k - 2 * pad
        if self.st["frame"] == "polaroid":
            ah -= 40
        ar = self._aspect()
        w = min(aw, ah * ar)
        h = w / ar
        c = area.center()
        if self.st["frame"] == "polaroid":
            c = QPointF(c.x(), c.y() - 16)
        return QRectF(c.x() - w / 2, c.y() - h / 2, max(16, w), max(9, h))

    def _layout_children(self):
        W, H = self.width(), self.height()
        mode = self.st.get("controls", "full")
        self.top.setGeometry(14, 14, W - 28, self.BAR_H)
        self.bottom.setGeometry(14, H - 14 - self.BAR_H, W - 28, self.BAR_H)
        for w in self._seek_ws:
            w.setVisible(mode != "minimal")
        self.l_title.setVisible(bool(self.st.get("show_title", True)))
        fw = min(340, max(260, W // 4))
        self.fxp.setGeometry(W - 14 - fw, 14 + self.BAR_H + 10, fw, max(120, H - 2 * (14 + self.BAR_H + 10)))
        self.b_big.adjustSize()
        r = self._video_rect()
        self.b_big.move(int(r.center().x() - self.b_big.width() / 2), int(r.bottom() - self.b_big.height() - 24))
        for w in (self.top, self.bottom, self.fxp, self.b_big):
            w.raise_()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._bg = None
        self._layout_children()

    # ── ввод ── #

    def mouseMoveEvent(self, e):
        self._last_move = time.monotonic()
        super().mouseMoveEvent(e)

    def mouseDoubleClickEvent(self, e):
        if self._video_rect().contains(e.position()):
            fs = getattr(self.win, "_toggle_fullscreen", None)
            if fs:
                fs()

    def mousePressEvent(self, e):
        self.setFocus()
        self._last_move = time.monotonic()
        if e.button() == Qt.MouseButton.LeftButton and self._video_rect().contains(e.position()):
            self.win.toggle_play()

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key.Key_Escape:
            self.close_mode()
        elif k == Qt.Key.Key_F11:
            fs = getattr(self.win, "_toggle_fullscreen", None)
            if fs:
                fs()
        elif k == Qt.Key.Key_Space:
            self.win.toggle_play()
        elif k == Qt.Key.Key_Right:
            self.win.next_track()
        elif k == Qt.Key.Key_Left:
            self.win.prev_track()
        elif k == Qt.Key.Key_E:
            self.b_fx.toggle()
        else:
            super().keyPressEvent(e)

    # ── рисование ── #

    def _snap_theme(self):
        self._theme_snap_t = time.monotonic()
        crt = getattr(self.win, "_crt", None)
        bd = getattr(crt, "backdrop", None) if crt is not None else None
        try:
            if bd is not None and bd.isVisible():
                self._theme_snap = bd.grab()
                self._bg = None
        except RuntimeError:
            self._theme_snap = None

    def _make_bg(self) -> QPixmap:
        W, H = max(1, self.width()), max(1, self.height())
        st = self.st
        pm = QPixmap(W, H)
        pm.fill(_qc(st["bg"]))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        mode = st["bg_mode"]
        if mode == "theme" and self._theme_snap is not None:
            p.drawPixmap(self.rect(), self._theme_snap)
        elif mode == "color":
            p.fillRect(pm.rect(), _qc(st["solid_color"]))
            if st["family"] == "fluid":                    # мягкие пятна цвета клипа на бежевом
                for cx, cy, r, a in ((0.2, 0.25, 0.55, 0.30), (0.85, 0.8, 0.6, 0.25)):
                    g = QRadialGradient(QPointF(W * cx, H * cy), max(W, H) * r)
                    c = QColor(self._ambient if self.src is not None else QColor(st["accent"]))
                    c.setAlphaF(a)
                    g.setColorAt(0, c)
                    c2 = QColor(c)
                    c2.setAlphaF(0)
                    g.setColorAt(1, c2)
                    p.fillRect(pm.rect(), g)
        elif mode == "ambient":
            g = QRadialGradient(QPointF(W / 2, H * 0.45), max(W, H) * 0.75)
            g.setColorAt(0, self._ambient.lighter(120))
            g.setColorAt(1, _qc(st["bg"]))
            p.fillRect(pm.rect(), g)
        elif self._cover is not None:                       # размытая обложка (гаусс, без квадратов)
            import blur_fx
            p.drawImage(0, 0, blur_fx.blurred_cover(self._cover, W, H, max(W, H) / 28.0))
        dim = float(st.get("dim", 0.5))
        if dim > 0.01:
            p.fillRect(pm.rect(), _qc("#000000" if not (st["light"] and mode == "color") else "#ffffff", dim * 0.85))
        if st["family"] == "winamp":
            p.fillRect(pm.rect(), QColor(20, 40, 80, 140))
        v = QRadialGradient(QPointF(W / 2, H / 2), max(W, H) * 0.75)
        v.setColorAt(0.55, QColor(0, 0, 0, 0))
        v.setColorAt(1.0, QColor(0, 0, 0, 120 if not st["light"] else 40))
        p.fillRect(pm.rect(), v)
        p.end()
        return pm

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        key = (self.width(), self.height(), self.st["bg_mode"], self.st["family"], self._cover_path)
        if self._bg is None or self._bg_key != key:
            self._bg = self._make_bg()
            self._bg_key = key
        p.setOpacity(self._op)
        p.drawPixmap(0, 0, self._bg)
        r = self._video_rect()
        if self.st["family"] == "winamp":
            self._paint_winamp_window(p, r)
        else:
            self._paint_glow(p, r)
            self._paint_frame_under(p, r)
        self._paint_video(p, r)
        self._paint_frame_over(p, r)
        p.end()

    def _glow_color(self) -> QColor:
        gc = self.st.get("glow_color")
        if gc:
            return QColor(gc)
        return QColor(self._ambient) if (self.src is not None or self.fx.active()) else QColor(self.st["accent"])

    def _paint_glow(self, p, r):
        g = float(self.st.get("glow", 0.5))
        if g < 0.02 or self.st["frame"] == "none" and g < 0.05:
            return
        c = self._glow_color()
        boost = 1.0 + self.pulse.bass * 0.4
        for i in range(8, 0, -1):
            cc = QColor(c)
            cc.setAlphaF(min(1.0, 0.035 * g * boost * (9 - i) / 2))
            rr = r.adjusted(-i * 7 * g, -i * 7 * g, i * 7 * g, i * 7 * g)
            path = QPainterPath()
            rad = self.st["frame_radius"] + i * 7 * g
            path.addRoundedRect(rr, rad, rad)
            p.fillPath(path, cc)

    def _frame_path(self, r) -> QPainterPath:
        path = QPainterPath()
        f = self.st["frame"]
        rad = 0 if f in ("square", "film", "polaroid") else float(self.st["frame_radius"])
        if f == "tv":
            rad = max(rad, 22)
        path.addRoundedRect(r, rad, rad)
        return path

    def _paint_frame_under(self, p, r):
        f = self.st["frame"]
        if f == "tv":                                      # пластиковый корпус телевизора
            body = r.adjusted(-34, -30, 34, 44)
            g = QLinearGradient(body.topLeft(), body.bottomLeft())
            g.setColorAt(0, QColor("#3a3530"))
            g.setColorAt(1, QColor("#16130f"))
            path = QPainterPath()
            path.addRoundedRect(body, 34, 34)
            p.fillPath(path, g)
            p.setPen(QPen(QColor(255, 255, 255, 30), 2))
            p.drawPath(path)
            for k in range(2):
                p.setBrush(QColor("#0c0b09"))
                p.setPen(QPen(QColor("#5a5248"), 2))
                p.drawEllipse(QPointF(body.right() - 26 - k * 30, body.bottom() - 22), 9, 9)
            p.setPen(QColor(255, 255, 255, 120))
            f2 = QFont(self.st["font"])
            f2.setPixelSize(12)
            f2.setBold(True)
            f2.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 3)
            p.setFont(f2)
            p.drawText(QRectF(body.left() + 30, body.bottom() - 34, 200, 24), Qt.AlignmentFlag.AlignVCenter, "ECHOES")
        elif f == "polaroid":
            card = r.adjusted(-18, -18, 18, 70)
            for i, a in ((14, 25), (8, 40), (3, 60)):
                p.fillRect(card.adjusted(-i * 0.3, i * 0.4, i * 0.3, i), QColor(0, 0, 0, a))
            p.fillRect(card, QColor("#f7f5ef"))
            if self.track:
                p.setPen(QColor("#2a2a2a"))
                f2 = QFont("Segoe Print")
                f2.setPixelSize(int(max(14, min(26, card.width() / 26))))
                p.setFont(f2)
                p.drawText(QRectF(card.left() + 10, r.bottom() + 8, card.width() - 20, 56),
                           Qt.AlignmentFlag.AlignCenter, (self.track.get("title") or "")[:48])
        elif f == "film":
            strip = 26
            for y0 in (r.top() - strip, r.bottom()):
                band = QRectF(r.left() - 10, y0, r.width() + 20, strip)
                p.fillRect(band, QColor("#0a0a0a"))
                n = max(4, int(band.width() / 28))
                off = (time.monotonic() * 60) % 28 if self.src is not None and not self._vpaused else 0
                for i in range(-1, n + 1):
                    x = band.left() + 8 + i * 28 + off
                    if band.left() < x < band.right() - 14:
                        hole = QPainterPath()
                        hole.addRoundedRect(QRectF(x, band.top() + 7, 14, 12), 3, 3)
                        p.fillPath(hole, QColor("#e9e1cc"))
        bw = float(self.st.get("border_w", 0))
        if bw > 0.1 and f not in ("tv", "polaroid", "film", "none"):
            path = self._frame_path(r.adjusted(-bw, -bw, bw, bw))
            p.fillPath(path, _qc(self.st.get("border") or self.st["text"]))

    def _paint_video(self, p, r):
        path = self._frame_path(r)
        p.save()
        p.setClipPath(path)
        img = self._frame
        if img is not None and not img.isNull():
            fit = self.st.get("fit", "contain")
            iw, ih = img.width(), img.height()
            if fit == "cover" and iw > 0 and ih > 0:
                k = max(r.width() / iw, r.height() / ih)
                sw, sh = r.width() / k, r.height() / k
                p.drawImage(r, img, QRectF((iw - sw) / 2, (ih - sh) / 2, sw, sh))
            else:
                p.fillRect(r, QColor(0, 0, 0))
                p.drawImage(r, img)
        else:
            p.fillRect(r, QColor(0, 0, 0, 200))
            if self._cover is not None:                    # пока клипа нет — размытая обложка во всю рамку
                key = (int(r.width()), int(r.height()), self._cover_path)
                if getattr(self, "_wait_bg_key", None) != key:
                    try:
                        import blur_fx
                        self._wait_bg = blur_fx.blurred_cover(self._cover, int(r.width()), int(r.height()),
                                                              max(r.width(), r.height()) / 22.0)
                    except Exception:                      # noqa: BLE001
                        self._wait_bg = None
                    self._wait_bg_key = key
                if getattr(self, "_wait_bg", None) is not None:
                    p.drawImage(r.topLeft(), self._wait_bg)
                    p.fillRect(r, QColor(0, 0, 0, 110))
                side = min(r.width(), r.height()) * 0.66      # и чёткая — ровно по центру, с тенью
                cr = QRectF(r.center().x() - side / 2, r.center().y() - side / 2, side, side)
                for i in range(6, 0, -1):
                    sh_ = QPainterPath()
                    sh_.addRoundedRect(cr.adjusted(-i * 3, -i * 3 + 6, i * 3, i * 3 + 6), 14 + i * 3, 14 + i * 3)
                    p.fillPath(sh_, QColor(0, 0, 0, 14))
                src = self._cover
                s = min(src.width(), src.height())
                cp = QPainterPath()
                cp.addRoundedRect(cr, 14, 14)
                p.save()
                p.setClipPath(cp, Qt.ClipOperation.IntersectClip)
                p.drawPixmap(cr, src, QRectF((src.width() - s) / 2, (src.height() - s) / 2, s, s))
                p.restore()
            t = self.track
            if t and self.svc.busy(t):                     # полоска прогресса
                f, s, _e = self.svc.state.get(CF.track_key(t), (0, "", ""))
                bar = QRectF(r.left() + r.width() * 0.2, r.bottom() - 34, r.width() * 0.6, 6)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255, 50))
                p.drawRoundedRect(bar, 3, 3)
                p.setBrush(_qc(self.st["accent"]))
                ph = (time.monotonic() * 0.8) % 1.0
                if f > 0.02:
                    p.drawRoundedRect(QRectF(bar.left(), bar.top(), max(6, bar.width() * f), bar.height()), 3, 3)
                else:                                      # ищем — бегущий отрезок
                    p.drawRoundedRect(QRectF(bar.left() + bar.width() * ph * 0.75, bar.top(), bar.width() * 0.25,
                                             bar.height()), 3, 3)
                p.setPen(QColor(255, 255, 255, 210))
                f2 = QFont(self.st["font"])
                f2.setPixelSize(13)
                p.setFont(f2)
                p.drawText(QRectF(r.left(), bar.top() - 26, r.width(), 20), Qt.AlignmentFlag.AlignCenter, s)
        if self.st["frame"] == "tv":                       # кинескоп: строки и виньетка
            p.setPen(Qt.PenStyle.NoPen)
            y = r.top()
            while y < r.bottom():
                p.fillRect(QRectF(r.left(), y, r.width(), 1), QColor(0, 0, 0, 55))
                y += 3
            g = QRadialGradient(r.center(), max(r.width(), r.height()) * 0.7)
            g.setColorAt(0.6, QColor(0, 0, 0, 0))
            g.setColorAt(1.0, QColor(0, 0, 0, 170))
            p.fillRect(r, g)
        p.restore()

    def _paint_frame_over(self, p, r):
        f = self.st["frame"]
        if self.st["family"] == "winamp":
            return
        if f == "neon":
            c = QColor(self.st.get("border") or self.st["accent"])
            pulse = 0.6 + 0.4 * self.pulse.bass
            path = self._frame_path(r)
            for wdt, a in ((14, 0.10), (8, 0.22), (4, 0.5), (2, 1.0)):
                cc = QColor(c)
                cc.setAlphaF(a * pulse)
                p.setPen(QPen(cc, wdt))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(path)
        elif f in ("rounded", "square") and float(self.st.get("border_w", 0)) <= 0.1:
            p.setPen(QPen(QColor(255, 255, 255, 26), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(self._frame_path(r))

    # ── Winamp: окно вокруг видео ── #

    @staticmethod
    def _bevel(p, r, raised=True, face=None):
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

    def _paint_winamp_window(self, p, r):
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        fr = r.adjusted(-12, -30, 12, 12)
        body = QLinearGradient(fr.topLeft(), fr.bottomLeft())
        body.setColorAt(0, QColor("#3b3b52"))
        body.setColorAt(1, QColor("#20202e"))
        p.fillRect(fr, body)
        self._bevel(p, fr, True)
        tb = QRectF(fr.left() + 4, fr.top() + 5, fr.width() - 8, 14)
        for i in range(4):
            y = tb.top() + 2 + i * 3
            p.setPen(QPen(QColor("#c8b46a") if i % 2 == 0 else QColor("#6a5c2a"), 1))
            p.drawLine(QPointF(tb.left() + 4, y), QPointF(tb.right() - 4, y))
        title = "ECHOES — КЛИП" + ("  ·  FX" if self.fx.active() else "")
        f = QFont("Tahoma")
        f.setPixelSize(10)
        f.setBold(True)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.5)
        p.setFont(f)
        tw = p.fontMetrics().horizontalAdvance(title) + 16
        trr = QRectF(fr.center().x() - tw / 2, tb.top(), tw, tb.height())
        p.fillRect(trr, QColor("#2b2b3c"))
        p.setPen(QColor("#e8e8f4"))
        p.drawText(trr, Qt.AlignmentFlag.AlignCenter, title)
        p.fillRect(r.adjusted(-3, -3, 3, 3), QColor("#000"))
        self._bevel(p, r.adjusted(-4, -4, 4, 4), False)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)


# ------------------------------------------------------------------ #
#  Подключение к окну плеера                                          #
# ------------------------------------------------------------------ #

def service(win) -> ClipService:
    s = win.__dict__.get("_clip_service")
    if s is None:
        s = win._clip_service = ClipService(win)
    return s


def mode(win, create=True) -> ClipMode | None:
    cm = win.__dict__.get("_clip_mode")
    try:
        if cm is not None:
            cm.objectName()
    except RuntimeError:
        cm = None
    if cm is None and create:
        cm = win._clip_mode = ClipMode(win, service(win))
        cm.apply_theme(win.__dict__.get("_clip_theme_data"))
    return cm


def toggle(win):
    cm = mode(win)
    if cm.is_open() and cm._op > 0.5:
        cm.close_mode()
    else:
        cm.open()
