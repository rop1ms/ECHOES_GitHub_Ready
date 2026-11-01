# fluid_theme.py
"""
Qt-часть темы «Fluid» — композиция в духе главного меню Bomb Rush Cyberfunk.

Силуэт сцены (а не только цвета):

    ┌───────────────────────────────┬──────────────┐
    │ большой строчный заголовок     │ ▓▓▓▓▓▓▓▓▓▓▓▓ │ ← верхняя полоска жидкости
    │ исполнитель · альбом     · 045 │ сейчас играет│ ← кремовая планка-вырез
    │                                │ ▓▓▓▓▓▓▓▓▓▓▓▓ │
    │ текст                          │ ▓▓  (диск) ▓ │   высокий «постер»
    │ очередь          ˜тег-граффити │ ▓▓▓▓▓▓▓▓▓▓▓▓ │   с живой жидкостью
    │ ▂▅▇▅▂ визуализатор             │ ▓▓▓▓▓▓▓▓ ✺  │ ← оранжевый стикер-эквалайзер
    │ ТРЕК: 045                      │ ▓▓▓▓▓▓▓▓▓▓▓▓ │
    │ (  кремовая полоса-транспорт  )││▓▓▓▓▓▓▓▓▓▓▓▓ │ ← полосы-«стыки» к постеру
    └───────────────────────────────┴──────────────┘

Раскладку виджетов (колонка + слот постера) строит main.py, а FluidPanel
лишь узнаёт через set_hosts(), где лежат слот постера и полоса транспорта,
и рисует под ними всю графику.

Производительность
──────────────────
  • симуляция и раскраска — numpy на грубой сетке (~2–4 мс на кадр);
  • кадр рендерится в uint32 0xFFRRGGBB → QImage.Format_RGB32 без
    конвертаций, далее один smooth-скейл до размера постера;
  • всё статичное (рамка с вырезом, тег, счётчик, полосы) — в кэшированных
    QPixmap, перестраиваются только при ресайзе/смене трека;
  • каждый тик перерисовывается ТОЛЬКО прямоугольник постера, а не вся
    сцена; при частых перерисовках от вращающегося диска Qt просто блитит
    готовые пиксмапы;
  • адаптивное разрешение: если кадр дорожает — рендер-масштаб снижается;
  • в свёрнутом окне и под overlay текста — ничего не рисуется.
"""

from __future__ import annotations

import math
import threading
import time
from pathlib import Path

import numpy as np
from frameclock import FrameTimer, animation_delta
from PyQt6.QtCore import (
    Qt, QObject, QRunnable, QThreadPool, QTimer, QRectF, QRect, QPoint, QPointF,
    QSize, QVariantAnimation, QEasingCurve, QEvent, pyqtSignal,
)
from PyQt6.QtGui import (
    QColor, QFont, QFontDatabase, QImage, QPainter, QPainterPath, QPixmap,
    QPen, QPolygonF,
)
from PyQt6.QtWidgets import QFrame, QWidget

import fluid_core as fc


# ────────────────────────────────────────────────────────────────────────── #
#  Шрифты
# ────────────────────────────────────────────────────────────────────────── #

def load_fonts(fonts_dir: str | Path | None = None) -> list[str]:
    """
    Регистрирует все .ttf/.otf из папки fonts/ рядом с программой.
    Вызывать ПОСЛЕ создания QApplication и ДО создания MainWindow.

    Рекомендуемый шрифт под референс (широкий, с кириллицей, бесплатный, OFL):
    «Unbounded» — https://fonts.google.com/specimen/Unbounded
    Положите Unbounded-*.ttf в папку fonts/. Если файлов нет — QSS сам
    откатится на Syncopate / Michroma / Russo One / Arial Black.
    """
    d = Path(fonts_dir) if fonts_dir else Path(__file__).resolve().with_name("fonts")
    families: list[str] = []
    if d.is_dir():
        for f in sorted(list(d.glob("*.ttf")) + list(d.glob("*.otf"))):
            fid = QFontDatabase.addApplicationFont(str(f))
            if fid >= 0:
                families += QFontDatabase.applicationFontFamilies(fid)
    return families


_WIDE_FAMILIES = ["Unbounded", "Syncopate", "Michroma", "Russo One",
                  "Segoe UI Black", "Arial Black"]


def _wide_font(px: int, weight=QFont.Weight.Black, italic: bool = False) -> QFont:
    f = QFont()
    f.setFamilies(_WIDE_FAMILIES)
    f.setPixelSize(max(6, int(px)))
    f.setWeight(weight)
    f.setItalic(italic)
    return f


# ────────────────────────────────────────────────────────────────────────── #
#  QImage <-> numpy
# ────────────────────────────────────────────────────────────────────────── #

def _qimage_to_rgb_array(img: QImage) -> np.ndarray:
    """QImage → numpy HxWx3 uint8 (копия, безопасно после удаления QImage)."""
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = img.width(), img.height()
    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes())
    buf = np.frombuffer(ptr, dtype=np.uint8).reshape(h, img.bytesPerLine())
    return buf[:, : w * 4].reshape(h, w, 4)[:, :, :3].copy()


def _array_to_qimage(arr: np.ndarray) -> QImage:
    """numpy HxWx3 uint8 → QImage (оставлено для совместимости, напр. gif_export)."""
    h, w, _ = arr.shape
    return QImage(arr.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


# ────────────────────────────────────────────────────────────────────────── #
#  Фоновое извлечение доминантного цвета
# ────────────────────────────────────────────────────────────────────────── #

class _Signals(QObject):
    done = pyqtSignal(int, object, bool)     # job_id, dominant rgb, mono


class _CoverJob(QRunnable):
    def __init__(self, job_id: int, cover_path, signals: _Signals):
        super().__init__()
        self.setAutoDelete(True)
        self.job_id, self.cover_path, self.signals = job_id, cover_path, signals

    def run(self):
        try:
            dom, mono = fc.DEFAULT_DOMINANT, False
            if self.cover_path:
                img = QImage(str(self.cover_path))
                if not img.isNull():
                    small = img.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation)
                    dom, mono = fc.dominant_rgb(_qimage_to_rgb_array(small))
            self.signals.done.emit(self.job_id, dom, mono)
        except Exception as e:          # noqa: BLE001 — виджет мог быть уже удалён
            print("[fluid] cover job error:", e)


# ────────────────────────────────────────────────────────────────────────── #
#  Фоновый поток симуляции
# ────────────────────────────────────────────────────────────────────────── #

class _FluidWorker(threading.Thread):
    """Считает FluidSim, раскраску и масштабирование кадра ВНЕ GUI-потока.
    numpy на больших массивах отпускает GIL, QImage реентерабелен — интерфейс
    не ждёт кадр жидкости. Команды (палитра, новый узор, сетка, всплеск)
    копятся в очереди и применяются перед ближайшим шагом; в работе не больше
    одной задачи — если поток занят, кадр просто пропускается."""

    def __init__(self):
        super().__init__(daemon=True, name="fluid-sim")
        self._cond = threading.Condition()
        self._cmds: list = []
        self._job = None
        self._alive = True
        self._res_lock = threading.Lock()
        self.result = None
        self.sim: fc.FluidSim | None = None

    def cmd(self, *c):
        with self._cond:
            self._cmds.append(c)

    def submit(self, dt, level, iw, ih, ow, oh) -> bool:
        with self._cond:
            if self._job is not None:
                return False
            self._job = (dt, level, iw, ih, ow, oh)
            self._cond.notify()
            return True

    def take(self):
        with self._res_lock:
            r, self.result = self.result, None
            return r

    def stop(self):
        with self._cond:
            self._alive = False
            self._cond.notify()

    def _apply(self, c):
        kind = c[0]
        if kind == "create":
            _, gw, gh, seed, pal, level = c
            self.sim = fc.make_sim(gw, gh, seed=seed, pal=pal)
            self.sim.level = level
        elif self.sim is None:
            return
        elif kind == "palette":
            self.sim.set_palette(c[1])
        elif kind == "seed":
            self.sim.seed_dye(c[1])
        elif kind == "regrid":
            self.sim.regrid(c[1], c[2])
        elif kind == "splash":
            self.sim.splash(c[1], c[2], c[3])

    def run(self):
        while True:
            with self._cond:
                while self._job is None and self._alive:
                    self._cond.wait()
                if not self._alive:
                    return
                job = self._job
                cmds, self._cmds = self._cmds, []
            try:
                for c in cmds:
                    self._apply(c)
                sim = self.sim
                if sim is not None:
                    dt, level, iw, ih, ow, oh = job
                    # чистое процессорное время ЭТОГО потока: «настенное»
                    # время включало ожидание GIL, пока занят интерфейс, и
                    # адаптация зря роняла разрешение (жидкость «мылилась»)
                    t0 = time.thread_time()
                    sim.level = level
                    sim.step(dt)
                    if hasattr(sim, "render_into"):
                        # C++: кадр сразу в итоговом размере постера, прямо в память QImage
                        big = QImage(iw, ih, QImage.Format.Format_RGB32)
                        sim.render_into(big)
                        with self._res_lock:
                            self.result = (big, (time.thread_time() - t0) * 1000.0)
                        continue
                    buf = sim.frame_argb32(ow, oh).view(np.uint8)
                    img = QImage(buf.data, ow, oh, 4 * ow, QImage.Format.Format_RGB32)
                    # при том же размере scaled() НЕ копирует, а отдаёт ссылку на буфер numpy,
                    # который сейчас освободится — кадр со «слоя-жидкости» ронял плеер. Только копия.
                    if (iw, ih) == (ow, oh):
                        big = img.copy()
                    else:
                        big = img.scaled(iw, ih, Qt.AspectRatioMode.IgnoreAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation)
                    del img, buf
                    with self._res_lock:
                        self.result = (big, (time.thread_time() - t0) * 1000.0)
            except Exception as e:                   # noqa: BLE001
                print("[fluid] worker error:", e)
            finally:
                with self._cond:
                    self._job = None


class _SimProxy:
    """Тот же API, что у FluidSim (для FluidPanel), но всё уходит командами
    в фоновый поток. gw/gh ведутся локально."""

    def __init__(self, worker: _FluidWorker, gw: int, gh: int, seed: int, pal, level: float):
        self._w = worker
        self.gw, self.gh = int(max(24, min(240, gw))), int(max(32, min(160, gh)))
        self.level = level
        worker.cmd("create", self.gw, self.gh, seed, pal, level)

    def set_palette(self, pal):
        self._w.cmd("palette", pal)

    def seed_dye(self, seed: int):
        self._w.cmd("seed", seed)

    def regrid(self, gw: int, gh: int):
        gw, gh = int(max(24, min(240, gw))), int(max(32, min(160, gh)))
        if (gw, gh) != (self.gw, self.gh):
            self.gw, self.gh = gw, gh
            self._w.cmd("regrid", gw, gh)

    def splash(self, gx: float, gy: float, power: float = 1.0):
        self._w.cmd("splash", gx, gy, power)


# ────────────────────────────────────────────────────────────────────────── #
#  Точечный (dot-matrix) шрифт для счётчика трека
# ────────────────────────────────────────────────────────────────────────── #

_DOT_FONT = {
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "01100"),
}


def _draw_dot_text(p: QPainter, text: str, x: float, y: float, pitch: float,
                   on: QColor, off: QColor):
    """Цифры «табло» из точек: горящие точки + едва заметные погасшие."""
    r = pitch * 0.36
    cx = x
    p.setPen(Qt.PenStyle.NoPen)
    for ch in text:
        rows = _DOT_FONT.get(ch)
        if rows is None:
            cx += pitch * 3
            continue
        for j, row in enumerate(rows):
            for i, bit in enumerate(row):
                p.setBrush(on if bit == "1" else off)
                p.drawEllipse(QPointF(cx + i * pitch + r, y + j * pitch + r), r, r)
        cx += pitch * 6.2


def dot_text_width(text: str, pitch: float) -> float:
    return len(text) * pitch * 6.2 - pitch * 1.2


# ────────────────────────────────────────────────────────────────────────── #
#  Сцена
# ────────────────────────────────────────────────────────────────────────── #

class FluidPanel(QFrame):
    """
    Замена центральному QFrame#CenterStage. Пока тема не Fluid
    (set_active(False)) — ничего не рисует и не тратит CPU.
    """

    palette_changed = pyqtSignal(object)        # FluidPalette
    frame_ready = pyqtSignal()                  # новый кадр жидкости (для LyricsView)

    FPS = 60            # на слабом ПК само опускается до 30 (см. _tick)
    FRAME = 12          # толщина кремовой рамки постера
    OUTER_R = 34        # скругление постера
    INNER_R = 24        # скругление «окна» с жидкостью
    LABEL_H = 40        # высота планки-выреза с подписью
    STRIPES_GAP = 34    # зазор колонка ↔ постер, в нём рисуются полосы-стыки
    VIZ_H = 140         # высота визуализатора в колонке (над ним — зона тега)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("CenterStage")

        self._active = False
        self._animated = True
        self._dark = False
        self._covered = False
        self._engine = None
        self._sim: fc.FluidSim | None = None
        self._pal = fc.make_palette(fc.DEFAULT_DOMINANT)
        self._dom, self._mono = fc.DEFAULT_DOMINANT, False
        self._job = 0
        self._counter = ""
        self._tag = ""
        self._label = "тишина"
        self._last_cover = None
        self._last_key = ""
        self._last_t = time.monotonic()
        self._level = 0.0
        self._clock = 0.0

        # хосты раскладки (выставляет main.py)
        self._poster_host: QWidget | None = None
        self._strip_host: QWidget | None = None
        self._menu_host: QWidget | None = None     # последний пункт меню — верх зоны тега

        # геометрия (в координатах сцены)
        self._outer = QRect()
        self._inner = QRect()
        self._geom_dirty = True
        self._decor_dirty = True

        # кэши
        self._decor_pm: QPixmap | None = None     # тег, счётчик, полосы
        self._frame_pm: QPixmap | None = None     # рамка постера с вырезами
        self._fluid_pm: QImage | None = None      # текущий кадр жидкости (размер inner)
        self._fluid_prev: QImage | None = None    # гаснущий старый кадр (смена трека)
        self._worker: _FluidWorker | None = None
        self._pending_dt = 0.0
        self._fade = 1.0

        # адаптивное качество
        self._scale = 0.72
        self._cost = 0.0

        self._pool = QThreadPool.globalInstance()
        self._signals = _Signals(self)
        self._signals.done.connect(self._on_cover)

        self._fade_anim = QVariantAnimation(self)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.setDuration(750)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._fade_anim.valueChanged.connect(self._on_fade)
        self._fade_anim.finished.connect(self._on_fade_done)

        self._timer = FrameTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(1000 // self.FPS)
        self._timer.timeout.connect(self._tick)

    # ── публичный API ─────────────────────────────────────────────────── #

    @property
    def active(self) -> bool:
        return self._active

    def fluid_palette(self) -> fc.FluidPalette:
        return self._pal

    def fluid_pixmap(self) -> QImage | None:
        """Текущий кадр жидкости (для постера в режиме текста)."""
        return self._fluid_pm

    def is_dark(self) -> bool:
        return self._dark

    def set_hosts(self, poster: QWidget | None, strip: QWidget | None,
                  menu: QWidget | None = None):
        """Слот постера, полоса транспорта и последний пункт меню — сцена
        рисует под ними графику (постер, полосы-стыки, тег)."""
        for w in (self._poster_host, self._strip_host, self._menu_host):
            if w is not None:
                try:
                    w.removeEventFilter(self)
                except RuntimeError:                 # виджет уже удалён
                    pass
        self._poster_host, self._strip_host, self._menu_host = poster, strip, menu
        for w in (poster, strip, menu):
            if w is not None:
                w.installEventFilter(self)
        self._geom_dirty = True
        self._update_poster_width()
        self.update()

    def set_covered(self, on: bool):
        """Сцена закрыта overlay'ем (режим текста): кадры считаются (их берёт
        LyricsView), но сама сцена не перерисовывается."""
        self._covered = bool(on)
        if not on:
            self.update()

    def set_dark(self, on: bool):
        on = bool(on)
        if on == self._dark:
            return
        self._dark = on
        self._pal = fc.make_palette(self._dom, self._mono, dark=on)
        if self._sim is not None:
            self._sim.set_palette(self._pal)
            self._sim.splash(self._sim.gw * 0.5, self._sim.gh * 0.5, 0.8)
        self._frame_pm = None
        self._decor_dirty = True
        self.palette_changed.emit(self._pal)
        self.update()

    def set_engine(self, engine):
        self._engine = engine

    def set_active(self, on: bool):
        on = bool(on)
        if on == self._active:
            return
        self._active = on
        if on:
            self._geom_dirty = True
            if self._sim is None:
                self._sim = self._make_sim()
            self.set_cover(self._last_cover, self._last_key)
            self._last_t = time.monotonic()
            self._sync_timer()
        else:
            self._timer.stop()
            self._fluid_pm = self._fluid_prev = None
            self._decor_pm = self._frame_pm = None
            # тема уже не Fluid: поток симуляции с её сетками и последним кадром больше не нужен
            if self._worker is not None:
                self._worker.stop()
                self._worker = None
            self._sim = None
        self.update()

    def set_animated(self, on: bool):
        self._animated = bool(on)
        self._sync_timer()

    def set_counter(self, text: str):
        """Номер трека — точечное «табло» («045» на референсе)."""
        text = "".join(ch for ch in (text or "") if ch.isdigit())[-4:]
        if text != self._counter:
            self._counter = text
            self._decor_dirty = True
            self.update()

    def set_tag(self, text: str):
        """Название трека — огромный бледный «тег-граффити» на фоне."""
        text = ""                       # тег-граффити отключён
        if text != self._tag:
            self._tag = text
            self._decor_dirty = True
            self.update()

    def set_cover(self, cover_path, key: str = ""):
        self._last_cover, self._last_key = cover_path, key or ""
        if not self._active:
            return
        self._job += 1
        self._pool.start(_CoverJob(self._job, cover_path, self._signals))

    # ── цвета композиции ──────────────────────────────────────────────── #

    def _colors(self):
        if self._dark:
            return {
                "frame": QColor(24, 24, 29),
                "label": QColor(236, 234, 226),
                "stripe": QColor(24, 24, 29),
                "tag": QColor(34, 34, 40),
                "tag_shadow": QColor(0, 0, 0, 150),
                "dot_on": QColor(52, 52, 60),
                "dot_off": QColor(30, 30, 36),
                "meta": QColor(150, 136, 70),
                "sticker": QColor(*fc.ORANGE),
                "sticker_ink": QColor(11, 11, 14),
            }
        return {
            "frame": QColor(*fc.CREAM),
            "label": QColor(14, 14, 14),
            "stripe": QColor(*fc.CREAM),
            "tag": QColor(246, 242, 212),
            "tag_shadow": QColor(200, 196, 158, 150),
            "dot_on": QColor(238, 234, 206),
            "dot_off": QColor(229, 226, 196),
            "meta": QColor(240, 226, 96),
            "sticker": QColor(*fc.ORANGE),
            "sticker_ink": QColor(*fc.CREAM),
        }

    # ── геометрия ─────────────────────────────────────────────────────── #

    def eventFilter(self, obj, ev):
        if obj is self._poster_host or obj is self._strip_host or obj is self._menu_host:
            t = ev.type()
            if t in (QEvent.Type.Resize, QEvent.Type.Move,
                     QEvent.Type.Show, QEvent.Type.Hide):
                self._geom_dirty = True
                if self._active:
                    self.update()
        return False

    def _poster_visible(self) -> bool:
        h = self._poster_host
        return h is not None and h.isVisibleTo(self) and h.width() > 20 and h.height() > 40

    def _update_poster_width(self):
        """Постер держит пропорцию «вертикального плаката» ~9:16."""
        h = self._poster_host
        if h is None or not self._active:
            return
        H = max(1, self.height())
        W = max(1, self.width())
        target = int(H * 0.56)
        # не уже 340: внутри живёт VinylWidget (минимум 280) + поля слота
        target = max(340, min(target, int(W * 0.55)))
        if h.width() != target or h.minimumWidth() != target:
            h.setFixedWidth(target)

    def _ensure_geometry(self):
        if not self._geom_dirty:
            return
        self._geom_dirty = False
        old_outer = QRect(self._outer)
        if self._poster_visible():
            h = self._poster_host
            tl = h.mapTo(self, QPoint(0, 0))
            self._outer = QRect(tl, h.size())
            F = self.FRAME
            self._inner = self._outer.adjusted(F, F, -F, -F)
        else:
            self._outer = QRect()
            self._inner = QRect()
        if self._outer.size() != old_outer.size():
            self._frame_pm = None
            self._fluid_prev = None
            if self._sim is not None and not self._inner.isEmpty():
                gw, gh = self._grid_dims()
                self._sim.regrid(gw, gh)
        self._decor_dirty = True

    def _grid_dims(self) -> tuple[int, int]:
        iw = max(1, self._inner.width())
        ih = max(1, self._inner.height())
        gh = int(min(150, max(48, round(ih / 5.0))))
        gw = int(min(200, max(24, round(gh * iw / ih))))
        return gw, gh

    def _sticker_rect(self) -> QRect:
        if self._inner.isEmpty():
            return QRect()
        R = int(max(26, min(44, self._inner.width() * 0.13)))
        c = QPoint(self._inner.right() - R - 12, self._inner.bottom() - R - 12)
        pad = R + 8
        return QRect(c.x() - pad, c.y() - pad, pad * 2, pad * 2)

    # ── кэш-слои ──────────────────────────────────────────────────────── #

    def _dpr(self) -> float:
        try:
            return max(1.0, float(self.devicePixelRatioF()))
        except Exception:                            # noqa: BLE001
            return 1.0

    def _new_pm(self, size: QSize) -> QPixmap:
        dpr = self._dpr()
        pm = QPixmap(max(1, int(size.width() * dpr)), max(1, int(size.height() * dpr)))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        return pm

    def _label_geom(self):
        """Верхняя полоска жидкости + планка-вырез + основное окно (в координатах постера)."""
        w, h = self._outer.width(), self._outer.height()
        F = self.FRAME
        top_h = int(max(22, min(64, h * 0.065)))
        top = QRectF(F, F, w - 2 * F, top_h)
        bar = QRectF(F, F + top_h, w - 2 * F, self.LABEL_H)
        main = QRectF(F, F + top_h + self.LABEL_H, w - 2 * F, h - 2 * F - top_h - self.LABEL_H)
        return top, bar, main

    def _build_frame(self):
        """Рамка постера: кремовое тело с «окнами» (вырезаны через Clear)
        и подписью на планке — как «основное меню» на референсе."""
        if self._outer.isEmpty():
            self._frame_pm = None
            return
        col = self._colors()
        w, h = self._outer.width(), self._outer.height()
        pm = self._new_pm(self._outer.size())
        p = QPainter(pm)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        outer = QPainterPath()
        outer.addRoundedRect(QRectF(0, 0, w, h), self.OUTER_R, self.OUTER_R)
        p.fillPath(outer, col["frame"])

        top, bar, main = self._label_geom()
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        holes = QPainterPath()
        holes.addRoundedRect(top, 14, 14)
        holes.addRoundedRect(main, self.INNER_R, self.INNER_R)
        p.fillPath(holes, QColor(0, 0, 0, 255))
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        # подпись на планке — справа, крупно, чёрным широким шрифтом
        txt = self._label
        px = int(self.LABEL_H * 0.62)
        f = _wide_font(px)
        f.setCapitalization(QFont.Capitalization.AllLowercase)
        p.setFont(f)
        fm = p.fontMetrics()
        while fm.horizontalAdvance(txt) > bar.width() - 16 and px > 10:
            px -= 1
            f.setPixelSize(px)
            p.setFont(f)
            fm = p.fontMetrics()
        p.setPen(col["label"])
        p.drawText(bar.adjusted(8, 0, -2, 2),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, txt)
        p.end()
        self._frame_pm = pm

    def _build_decor(self):
        """Всё статичное вне постера: тег-граффити, табло, подпись, полосы-стыки."""
        self._decor_dirty = False
        W, H = self.width(), self.height()
        if W < 2 or H < 2:
            self._decor_pm = None
            return
        col = self._colors()
        pm = self._new_pm(QSize(W, H))
        p = QPainter(pm)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)

        poster = self._outer if not self._outer.isEmpty() else QRect(W, 0, 0, H)
        info_right = poster.left() - self.STRIPES_GAP
        info_w = max(60, info_right)

        strip = QRect()
        if self._strip_host is not None and self._strip_host.isVisibleTo(self):
            strip = QRect(self._strip_host.mapTo(self, QPoint(0, 0)), self._strip_host.size())

        # 1) полосы-стыки между полосой транспорта и постером
        if not strip.isEmpty() and not self._outer.isEmpty():
            gap_l, gap_r = strip.right() + 1, poster.left()
            bw = 7
            n = 2
            span = gap_r - gap_l
            if span > n * bw + 6:
                step = span / (n + 1)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(col["stripe"])
                for i in range(n):
                    x = gap_l + step * (i + 1) - bw / 2
                    p.drawRoundedRect(QRectF(x, strip.top() + 6, bw, strip.height() - 12), 3, 3)

        # 2) тег-граффити из названия трека убран (мешал и выглядел зачёркнутым)

        # 3) точечное табло с номером трека — правый верхний угол колонки
        if self._counter:
            pitch = max(4.0, min(7.0, H / 110.0))
            tw = dot_text_width(self._counter, pitch)
            _draw_dot_text(p, self._counter, info_right - tw - 6, 10, pitch,
                           col["dot_on"], col["dot_off"])

        # 4) бледная «служебная» строка над полосой — как «ВЕРСИЯ: …»
        if self._counter and not strip.isEmpty():
            f = QFont()
            f.setPixelSize(15)
            f.setWeight(QFont.Weight.Medium)
            p.setFont(f)
            p.setPen(col["meta"])
            p.drawText(QRectF(strip.left() + 14, strip.top() - 26, info_w, 22),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       f"ТРЕК: {self._counter}")
        p.end()
        self._decor_pm = pm

    # ── обложка / палитра ─────────────────────────────────────────────── #

    def _make_sim(self) -> "_SimProxy":
        if self._worker is None:
            self._worker = _FluidWorker()
            self._worker.start()
        gw, gh = self._grid_dims() if not self._inner.isEmpty() else (72, 128)
        seed = fc.seed_from_key(self._last_key or str(self._last_cover or ""))
        return _SimProxy(self._worker, gw, gh, seed,
                         fc.make_palette(self._dom, self._mono, dark=self._dark), self._level)

    def _on_cover(self, job_id: int, dom, mono: bool):
        if job_id != self._job or not self._active:
            return
        mono = bool(mono)
        same = (dom == self._dom and mono == self._mono)
        self._dom, self._mono = dom, mono
        self._pal = fc.make_palette(dom, mono, dark=self._dark)
        if self._sim is None:
            self._sim = self._make_sim()
        self._sim.set_palette(self._pal)
        if not same:
            if self._fluid_pm is not None:
                self._fluid_prev = self._fluid_pm
                self._fade = 0.0
                self._fade_anim.stop()
                self._fade_anim.start()
            self._sim.seed_dye(fc.seed_from_key(self._last_key or str(self._last_cover or "")))
        self._sync_timer()
        self.palette_changed.emit(self._pal)
        self.update()

    def _on_fade(self, v):
        self._fade = float(v)
        if not self._covered and not self._outer.isEmpty():
            self.update(self._outer)

    def _on_fade_done(self):
        self._fluid_prev, self._fade = None, 1.0

    # ── цикл ──────────────────────────────────────────────────────────── #

    def _sync_timer(self):
        run = self._active and self._animated and self._sim is not None and self.isVisible()
        if run and not self._timer.isActive():
            self._last_t = time.monotonic()
            self._timer.start()
        elif not run and self._timer.isActive():
            self._timer.stop()

    def _playing(self) -> bool:
        try:
            return bool(self._engine is not None and self._engine.is_playing())
        except Exception:                            # noqa: BLE001
            return False

    def _tick(self):
        now = time.monotonic()
        dt = animation_delta(now - self._last_t)
        self._last_t = now
        sim = self._sim
        if sim is None:
            return
        win = self.window()
        if win is not None and win.isMinimized():
            return                                   # свёрнуто — ноль работы

        playing = self._playing()
        target = 0.0
        if playing:
            try:
                target = min(1.0, self._engine.get_level() * 4.0)
            except Exception:                        # noqa: BLE001
                target = 0.0
        k = (1.0 - math.exp(-dt * 26.0)) if target > self._level else (1.0 - math.exp(-dt * 5.0))
        self._level += (target - self._level) * k
        self._clock += dt

        label = ("сейчас играет" if playing else "пауза") if self._tag else "тишина"
        if label != self._label:
            self._label = label
            self._frame_pm = None
            if not self._covered:
                self.update(self._outer)

        self._ensure_geometry()
        need_frame = (self._poster_visible() or self._covered) and not self._inner.isEmpty()
        if not need_frame:
            return

        w = self._worker
        # 1) забрать готовый кадр из фонового потока
        res = w.take() if w is not None else None
        if res is not None:
            img, cost = res
            self._fluid_pm = img
            # адаптивное качество: кадр должен укладываться в бюджет 60 fps (~11 мс фонового времени)
            self._cost = self._cost * 0.85 + cost * 0.15
            if self._cost > 11.5 and self._scale > 0.5:
                self._scale -= 0.03
            elif self._cost < 6.5 and self._scale < 0.8:
                self._scale += 0.02
            # даже на минимальном качестве не успеваем — честные 30 кадров вместо рваных
            want = 33 if (self._cost > 14.0 and self._scale <= 0.5) else 1000 // self.FPS
            if self._timer.interval() != want:
                self._timer.setInterval(want)
            self.frame_ready.emit()
            if not self._covered:
                self.update(self._outer.adjusted(-2, -2, 2, 2))
        # 2) отдать следующую задачу (время копится, пока поток занят)
        self._pending_dt = min(0.1, self._pending_dt + dt)
        if w is not None:
            iw, ih = self._inner.width(), self._inner.height()
            ow = max(8, int(iw * self._scale))
            oh = max(8, int(ih * self._scale))
            if w.submit(self._pending_dt, self._level, iw, ih, ow, oh):
                self._pending_dt = 0.0

    def showEvent(self, e):
        super().showEvent(e)
        self._last_t = time.monotonic()
        self._geom_dirty = True
        self._sync_timer()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._timer.stop()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._update_poster_width()
        self._geom_dirty = True
        self.update()

    # ── рисование ─────────────────────────────────────────────────────── #

    def _draw_sticker(self, p: QPainter):
        """Оранжевый зубчатый стикер-эквалайзер в углу постера: медленно
        крутится и «дышит» от громкости — вместо мозга с референса."""
        r = self._sticker_rect()
        if r.isEmpty():
            return
        col = self._colors()
        c = QPointF(r.center())
        R = (r.width() / 2 - 8) * (1.0 + 0.10 * self._level)
        rot = self._clock * 0.35
        n = 18
        pts = []
        for i in range(n * 2):
            a = rot + math.pi * i / n
            rr = R if i % 2 == 0 else R * 0.82
            pts.append(QPointF(c.x() + rr * math.cos(a), c.y() + rr * math.sin(a)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col["sticker"])
        p.drawPolygon(QPolygonF(pts))
        # три столбика эквалайзера
        p.setBrush(col["sticker_ink"])
        bw = R * 0.16
        for k in range(3):
            ph = self._clock * (5.0 + k * 1.7) + k * 1.9
            amp = 0.28 + 0.72 * (0.35 + 0.65 * self._level) * (0.5 + 0.5 * math.sin(ph))
            bh = R * 0.95 * amp
            x = c.x() + (k - 1) * bw * 1.7 - bw / 2
            p.drawRoundedRect(QRectF(x, c.y() + R * 0.42 - bh, bw, bh), bw / 2, bw / 2)

    def paintEvent(self, ev):
        if not self._active:
            super().paintEvent(ev)
            return
        self._ensure_geometry()
        if self._decor_dirty or self._decor_pm is None:
            self._build_decor()
        if self._frame_pm is None and not self._outer.isEmpty():
            self._build_frame()

        p = QPainter(self)
        if self._decor_pm is not None:
            p.drawPixmap(0, 0, self._decor_pm)

        if not self._outer.isEmpty() and ev.rect().intersects(self._outer):
            inner = self._inner
            if self._fluid_pm is not None:
                if self._fluid_pm.size() == inner.size():
                    p.drawImage(inner.topLeft(), self._fluid_pm)
                else:                                # ресайз между тиками
                    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                    p.drawImage(inner, self._fluid_pm)
                if self._fluid_prev is not None and self._fade < 1.0:
                    p.setOpacity(1.0 - self._fade)
                    p.drawImage(inner, self._fluid_prev)
                    p.setOpacity(1.0)
            else:
                p.fillRect(inner, QColor(*self._pal.dominant))
            if self._frame_pm is not None:
                p.drawPixmap(self._outer.topLeft(), self._frame_pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            self._draw_sticker(p)
        p.end()


# ────────────────────────────────────────────────────────────────────────── #
#  Постер для режима текста (берёт кадры у FluidPanel — вторая симуляция не нужна)
# ────────────────────────────────────────────────────────────────────────── #

class FluidPosterBackdrop(QWidget):
    """Левая половина LyricsView в теме Fluid: бежевый фон + постер с живой
    жидкостью (кадры приходят из FluidPanel.frame_ready) в кремовой рамке."""

    FRAME = 14
    MARGIN = 34

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._stage: FluidPanel | None = None
        self._dark = False

    def set_stage(self, stage: FluidPanel | None):
        if self._stage is stage:
            return
        if self._stage is not None:
            try:
                self._stage.frame_ready.disconnect(self._on_frame)
            except (TypeError, RuntimeError):
                pass
        self._stage = stage
        if stage is not None:
            stage.frame_ready.connect(self._on_frame)

    def set_dark(self, on: bool):
        self._dark = bool(on)
        self.update()

    def _poster_rect(self) -> QRect:
        m = self.MARGIN
        return self.rect().adjusted(m, m, -m, -m)

    def _on_frame(self):
        if self.isVisible():
            self.update(self._poster_rect())

    def paintEvent(self, _):
        p = QPainter(self)
        bg = QColor(*fc.DARK_BG) if self._dark else QColor(*fc.BEIGE)
        frame = QColor(24, 24, 29) if self._dark else QColor(*fc.CREAM)
        p.fillRect(self.rect(), bg)
        r = self._poster_rect()
        if r.width() < 40 or r.height() < 40:
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        outer = QPainterPath()
        outer.addRoundedRect(QRectF(r), 36, 36)
        p.fillPath(outer, frame)
        inner = QRectF(r).adjusted(self.FRAME, self.FRAME, -self.FRAME, -self.FRAME)
        clip = QPainterPath()
        clip.addRoundedRect(inner, 26, 26)
        pm = self._stage.fluid_pixmap() if self._stage is not None else None
        p.save()
        p.setClipPath(clip)
        if pm is not None and not pm.isNull():
            # cover-масштаб: узор абстрактный, обрезка краёв незаметна
            s = max(inner.width() / pm.width(), inner.height() / pm.height())
            w, h = pm.width() * s, pm.height() * s
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawImage(QRectF(inner.center().x() - w / 2, inner.center().y() - h / 2, w, h),
                        pm, QRectF(pm.rect()))
        else:
            p.fillPath(clip, frame.darker(115))
        p.restore()
