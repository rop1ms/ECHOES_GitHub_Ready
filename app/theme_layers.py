# theme_layers.py
"""
Отрисовка пользовательских тем (конструктор тем).

  Backdrop  — самый нижний слой окна (под заголовком и всем интерфейсом): фон (цвет,
              градиент, картинка/GIF, видео, обложка трека, живые обои), оттенок,
              затемнение, виньетка, тени панелей и «матовое стекло» под ними, слои
              «позади интерфейса», вспышки на бит и мерцание.
  Overlay   — самый верхний слой (прозрачен для мыши): слои «поверх интерфейса»,
              частицы, сканлайны, зерно, виньетка поверх всего.
  ThemeRuntime — связывает всё с окном: одни часы кадров, один анализ звука на всех
              (AudioPulse), кэш картинок/GIF/шрифтов, видео-обои, автоподстройка качества.

Почему не тормозит. Виджеты Qt рисуются процессором, и любое обновление фона
перерисовывает весь интерфейс над ним. Поэтому:
  * статичный фон запекается целиком (фон + затемнение + стекло + тени) — кадр = блит;
  * живой фон (обои, видео, GIF, вращающийся градиент) обновляется ~30 раз/с только
    там, где нет боковых панелей. Под панелями лежит снимок фона (размытый для
    матового стекла), он обновляется реже и по очереди — кнопки и списки панелей
    не перерисовываются каждый кадр;
  * текст и фигуры слоёв рисуются один раз в спрайт, дальше — блит с поворотом и
    масштабом; живые обои считаются в низком разрешении и растягиваются;
  * если кадры всё же начинают опаздывать, частота живого фона и снимков под
    панелями снижается сама (и возвращается, когда запас появился).
"""
from __future__ import annotations

import ctypes
import math
import random
import threading
import time

import numpy as np
from img_load import read_image
from PyQt6.QtCore import QLineF, QObject, QPointF, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (QBrush, QColor, QConicalGradient, QCursor, QFont, QFontDatabase, QImage,
                         QImageReader, QLinearGradient, QMovie, QPainter, QPainterPath, QPen, QPixmap,
                         QRadialGradient, QRegion, QTransform)
from PyQt6.QtWidgets import QWidget, QApplication

import frameclock
import theme_kit as tk
from frameclock import FrameTimer

_BLEND = {
    "normal": QPainter.CompositionMode.CompositionMode_SourceOver,
    "screen": QPainter.CompositionMode.CompositionMode_Screen,
    "add": QPainter.CompositionMode.CompositionMode_Plus,
    "multiply": QPainter.CompositionMode.CompositionMode_Multiply,
    "overlay": QPainter.CompositionMode.CompositionMode_Overlay,
    "lighten": QPainter.CompositionMode.CompositionMode_Lighten,
    "darken": QPainter.CompositionMode.CompositionMode_Darken,
    "difference": QPainter.CompositionMode.CompositionMode_Difference,
}
_ARGB = QImage.Format.Format_ARGB32_Premultiplied
_SMOOTH = QPainter.RenderHint.SmoothPixmapTransform
_AA = QPainter.RenderHint.Antialiasing
_FONT_EXT = (".ttf", ".otf", ".ttc", ".woff", ".woff2")


def _qc(hex_color, alpha=None) -> QColor:
    c = QColor(hex_color or "#000000")
    if alpha is not None:
        c.setAlphaF(max(0.0, min(1.0, float(alpha))))
    return c


def _hrand(*k) -> float:
    """Детерминированное «случайное» 0..1 (одинаковое в advance() и paintEvent() одного кадра)."""
    x = math.sin(sum(v * m for v, m in zip(k, (12.9898, 78.233, 37.719, 4.581)))) * 43758.5453
    return x - math.floor(x)


def is_font_asset(rel) -> bool:
    return bool(rel) and str(rel).lower().endswith(_FONT_EXT)


# ------------------------------------------------------------------ #
#  Картинки: размытие, насыщенность, вписывание                       #
# ------------------------------------------------------------------ #

def _box1d(a, r, axis):
    n = a.shape[axis]
    pad = [(0, 0)] * a.ndim
    pad[axis] = (r + 1, r)
    c = np.cumsum(np.pad(a, pad, mode="edge"), axis=axis, dtype=np.float32)
    hi = np.take(c, np.arange(2 * r + 1, 2 * r + 1 + n), axis=axis)
    lo = np.take(c, np.arange(0, n), axis=axis)
    return (hi - lo) / float(2 * r + 1)


def box_blur(img: QImage, r: int, passes: int = 3) -> QImage:
    """Размытие маленькой картинки: гаусс с тем же радиусом, что дали бы passes проходов коробкой r
    (раньше — 2 прохода коробкой: треугольное ядро давало ромбы и полосы). Крупные — сначала уменьшить."""
    if img.isNull() or r < 1:
        return img
    import blur_fx
    w, h = img.width(), img.height()
    r = int(min(r, max(1, max(w, h) // 2)))
    sigma = math.sqrt(max(1, passes) * ((2 * r + 1) ** 2 - 1) / 12.0)
    return blur_fx.from_array(blur_fx.gauss_array(blur_fx.to_array(img), sigma))


def soft_blur(img: QImage, radius: float) -> QImage:
    """Размытие любой картинки: настоящий гаусс (уменьшение → гаусс → обратно, без ступенек)."""
    if img.isNull() or radius < 1:
        return img
    import blur_fx
    return blur_fx.blur_image(img, radius * 0.9)


def saturate(img: QImage, s: float) -> QImage:
    if abs(s - 1.0) < 0.02 or img.isNull():
        return img
    img = img.convertToFormat(QImage.Format.Format_ARGB32)
    w, h = img.width(), img.height()
    ptr = img.bits()
    ptr.setsize(img.sizeInBytes())
    a = np.frombuffer(ptr, np.uint8).reshape(h, img.bytesPerLine() // 4, 4)[:, :w, :]
    rgb = a[..., :3].astype(np.float32)
    gray = rgb @ np.array([0.114, 0.587, 0.299], np.float32)
    a[..., :3] = np.clip(gray[..., None] + (rgb - gray[..., None]) * s, 0, 255).astype(np.uint8)
    return img.copy()


def fit_rect(src: QSize, W, H, fit, zoom=1.0, ax=0.5, ay=0.5) -> QRectF:
    sw, sh = max(1, src.width()), max(1, src.height())
    if fit == "stretch":
        w, h = W * zoom, H * zoom
    elif fit == "contain":
        k = min(W / sw, H / sh) * zoom
        w, h = sw * k, sh * k
    elif fit == "center":
        w, h = sw * zoom, sh * zoom
    else:                                                  # cover
        k = max(W / sw, H / sh) * zoom
        w, h = sw * k, sh * k
    return QRectF((W - w) * ax, (H - h) * ay, w, h)


# ------------------------------------------------------------------ #
#  Звук: один анализ на всех                                          #
# ------------------------------------------------------------------ #

class AudioPulse:
    """level — громкость, bass — бас (0..1, сглажено), beat — вспышка на сильной доле."""

    def __init__(self, engine):
        self.engine = engine
        self.level = self.bass = self.beat = 0.0
        self.t = 0.0
        self.frame = 0
        self._slow = 0.0
        self._bmax = 1e-3
        self._cool = 0.0
        self._win = np.hanning(1024).astype(np.float32)

    def update(self, dt):
        self.t += dt
        self.frame += 1
        lvl = bass = 0.0
        e = self.engine
        try:
            if e is not None and e.is_playing():
                s = e.get_visual_samples(1024)
                if s is not None and len(s) == 1024:
                    a = np.asarray(s, np.float32)
                    lvl = float(np.sqrt(np.mean(a * a))) * 4.0
                    spec = np.abs(np.fft.rfft(a * self._win))
                    bin_hz = float(getattr(e, "visual_sample_rate", 44100) or 44100) / 1024.0
                    lo = max(1, int(30 / bin_hz))
                    hi = max(lo + 1, int(170 / bin_hz))
                    b = float(spec[lo:hi].mean())
                    self._bmax = max(b, self._bmax * (0.995 ** (dt * 60)), 1e-3)
                    bass = min(1.0, b / self._bmax)
        except Exception:                                  # noqa: BLE001
            pass
        lvl = min(1.0, lvl)
        up, dn = 1 - math.exp(-dt * 25), 1 - math.exp(-dt * 6)
        self.level += (lvl - self.level) * (up if lvl > self.level else dn)
        self.bass += (bass - self.bass) * (up if bass > self.bass else 1 - math.exp(-dt * 8))
        self._slow += (bass - self._slow) * (1 - math.exp(-dt * 1.5))
        self._cool = max(0.0, self._cool - dt)
        if bass > 0.35 and bass > self._slow * 1.35 and self._cool <= 0:
            self.beat = 1.0
            self._cool = 0.18
        self.beat *= math.exp(-dt * 6.0)


# ------------------------------------------------------------------ #
#  Источники картинок: статичные, GIF, видео                          #
# ------------------------------------------------------------------ #

class MediaSource(QObject):
    """Картинка или анимация (GIF/WebP). changed — пришёл новый кадр, frame_no — его номер."""

    changed = pyqtSignal()

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.path = path
        self.movie = None
        self.pix = QPixmap()
        self.frame_no = 0
        try:
            r = QImageReader(path)
            animated = r.supportsAnimation() and r.imageCount() != 1
        except Exception:                                  # noqa: BLE001
            animated = False
        if animated:
            m = QMovie(path)
            if m.isValid():
                # все кадры в памяти — только у небольших GIF (до ~48 МБ); большие декодируются на лету,
                # раньше порог был 300 МБ, и пара гифок в теме съедала полгигабайта
                sz = r.size()
                n = r.imageCount()
                small = n > 0 and sz.width() > 0 and n * sz.width() * sz.height() * 4 <= 48_000_000
                m.setCacheMode(QMovie.CacheMode.CacheAll if small else QMovie.CacheMode.CacheNone)
                m.frameChanged.connect(self._frame)
                self.movie = m
                m.start()
                self.pix = m.currentPixmap()
        if self.movie is None:
            r = QImageReader(path)
            r.setAutoTransform(True)                               # поворот из EXIF (фото с телефона)
            img = r.read()
            if not img.isNull() and max(img.width(), img.height()) > 4096:
                img = img.scaled(4096, 4096, Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
            self.pix = QPixmap.fromImage(img) if not img.isNull() else QPixmap()

    @property
    def animated(self) -> bool:
        return self.movie is not None

    def _frame(self, _n):
        self.pix = self.movie.currentPixmap()
        self.frame_no += 1
        self.changed.emit()

    def valid(self) -> bool:
        return not self.pix.isNull()

    def size(self) -> QSize:
        return self.pix.size()

    def set_paused(self, paused: bool):
        if self.movie is not None:
            self.movie.setPaused(bool(paused))

    def set_speed(self, k: float):
        if self.movie is not None:
            self.movie.setSpeed(int(max(10, min(400, k * 100))))

    def stop(self):
        """Источник больше не нужен: отпустить декодер и все закэшированные кадры GIF.
        (Объект может ещё жить у родителя-QObject — память при этом уже свободна.)"""
        if self.movie is not None:
            self.movie.stop()
            try:
                self.movie.frameChanged.disconnect(self._frame)
            except (TypeError, RuntimeError):
                pass
            self.movie.deleteLater()
            self.movie = None
        self.pix = QPixmap()


_SETUP_CB = ctypes.CFUNCTYPE(ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
                             ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint),
                             ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint))
_CLEANUP_CB = ctypes.CFUNCTYPE(None, ctypes.c_void_p)


class VideoSource(QObject):
    """Видео (обои и слои): VLC декодирует без окна и без звука, по кругу.

    Раньше кадр уменьшался до 1280×720, копировался целиком в память Python, а показывался
    не чаще 30 раз в секунду — отсюда «мыло», рывки и пропуски кадров. Теперь:
      * аппаратное декодирование (D3D11VA / DXVA2), процессор не тратится на распаковку;
      * кадр приходит сразу в том размере, в котором его покажут (VLC масштабирует в своём
        потоке, set_target), до 3840×2160 — без потолка 720p и без растяжения в интерфейсе;
      * четыре буфера по кругу: VLC пишет в свободный, интерфейс рисует готовый без копий;
      * показывается каждый кадр ролика (25/30/50/60 к/с), а не каждый второй.
    frame_no — номер показанного кадра (для точечной перерисовки слоёв)."""

    changed = pyqtSignal()
    _arrived = pyqtSignal()
    MAX_W, MAX_H = 3840, 2160
    NBUF = 4

    def __init__(self, path: str, parent=None, hw: bool = True, target=None):
        super().__init__(parent)
        self.path = path
        self.img = QImage()
        self._bufs = []
        self._retired = []                                 # старые буферы: на них ещё могут смотреть QImage
        self._w = self._h = 0
        self._src_w = self._src_h = 0
        self._ready = self._shown = self._prev = self._writing = -1
        self._pending = False
        self._mx = threading.Lock()
        self._target = tuple(int(v) for v in target) if target else None
        self.frame_no = 0
        self.decoded = 0
        self.fps = 0.0
        self.ok = False
        self.inst = self.p = None
        self._arrived.connect(self._on_frame, Qt.ConnectionType.QueuedConnection)
        try:
            import vlc
            args = ("--quiet --no-audio --no-xlib --no-video-title-show --no-osd --no-snapshot-preview --no-spu "
                    "--no-stats --file-caching=600 --drop-late-frames --skip-frames")
            if hw:
                args += " --avcodec-hw=any"                # D3D11VA/DXVA2; нет — VLC тихо декодирует сам
            self.inst = vlc.Instance(args)
            m = self.inst.media_new(path)
            for opt in (":no-audio", ":input-repeat=65535", ":avcodec-threads=0"):
                m.add_option(opt)
            if str(path).startswith(("http://", "https://")):
                m.add_option(":network-caching=1200")      # клип онлайн (clip_fetch.StreamFetch)
                m.add_option(":http-reconnect")
            self.p = self.inst.media_player_new()
            self.p.set_media(m)
            self._setup = _SETUP_CB(self._on_setup)
            self._cleanup = _CLEANUP_CB(lambda _op: None)
            f = vlc.dll.libvlc_video_set_format_callbacks
            f.argtypes = [ctypes.c_void_p, _SETUP_CB, _CLEANUP_CB]
            f.restype = None
            f(self.p, self._setup, self._cleanup)
            D = vlc.CallbackDecorators
            self._lock_cb = D.VideoLockCb(self._on_lock)
            self._unlock_cb = D.VideoUnlockCb(lambda _op, _pic, _planes: None)
            self._display_cb = D.VideoDisplayCb(self._on_display)
            self.p.video_set_callbacks(self._lock_cb, self._unlock_cb, self._display_cb, None)
            self.p.play()
            self.ok = True
        except Exception as e:                             # noqa: BLE001
            print("[theme] video:", e)

    # ── потоки VLC ── #

    def _out_size(self, sw, sh):
        if self._target:
            tw, th = self._target
            k = max(tw / sw, th / sh)                      # сохранить пропорции ролика, покрыть цель
        else:
            k = 1.0
        k = min(k, self.MAX_W / sw, self.MAX_H / sh)
        return max(16, int(sw * k + 0.5)) // 2 * 2, max(16, int(sh * k + 0.5)) // 2 * 2

    def _on_setup(self, _opaque, chroma, width, height, pitches, lines):
        sw, sh = max(1, width[0]), max(1, height[0])
        self._src_w, self._src_h = sw, sh
        w, h = self._out_size(sw, sh)
        ctypes.memmove(chroma, b"RV32", 4)
        width[0], height[0], pitches[0], lines[0] = w, h, w * 4, h
        with self._mx:
            if self._bufs:
                self._retired = (self._retired + [self._bufs])[-2:]
            self._bufs = [ctypes.create_string_buffer(w * h * 4) for _ in range(self.NBUF)]
            self._w, self._h = w, h
            self._ready = self._shown = self._prev = self._writing = -1
        return 1

    def _on_lock(self, _opaque, planes):
        with self._mx:
            busy = (self._ready, self._shown, self._prev)
            i = next((k for k in range(self.NBUF) if k not in busy), 0)
            self._writing = i
            planes[0] = ctypes.addressof(self._bufs[i])
        return None

    def _on_display(self, _opaque, _picture):
        with self._mx:
            self._ready = self._writing
            self.decoded += 1
            if self._pending:
                return                                     # интерфейс ещё не забрал прошлый — заберёт этот
            self._pending = True
        try:
            self._arrived.emit()
        except RuntimeError:
            pass

    # ── поток интерфейса ── #

    def _on_frame(self):
        from PyQt6 import sip
        with self._mx:
            self._pending = False
            i = self._ready
            if i < 0 or not self._bufs:
                return
            self._prev, self._shown, self._ready = self._shown, i, -1
            buf, w, h = self._bufs[i], self._w, self._h
        self.img = QImage(sip.voidptr(ctypes.addressof(buf)), w, h, w * 4, QImage.Format.Format_RGB32)
        self.frame_no += 1
        if self.fps <= 0 and self.frame_no == 30:
            try:
                self.fps = float(self.p.get_fps() or 0.0)
            except Exception:                              # noqa: BLE001
                pass
        self.changed.emit()

    def size(self) -> QSize:
        return QSize(self._w, self._h)

    def source_size(self) -> QSize:
        return QSize(self._src_w, self._src_h)

    def valid(self) -> bool:
        return not self.img.isNull()

    def set_target(self, w, h):
        """Показывают в w×h пикселей устройства: попросить у VLC кадр такого размера.
        Перезапуск вывода — только если размер заметно (>12 %) отличается от текущего."""
        if not self.ok or w < 16 or h < 16:
            return
        self._target = (int(w), int(h))
        if not self._src_w:
            return
        nw, nh = self._out_size(self._src_w, self._src_h)
        if self._w and abs(nw - self._w) <= self._w * 0.12 and abs(nh - self._h) <= self._h * 0.12:
            return
        try:
            t = self.p.get_time()
            self.p.stop()
            self.p.play()
            if t and t > 0:
                self.p.set_time(int(t))
        except Exception:                                  # noqa: BLE001
            pass

    def set_rate(self, r):
        try:
            self.p.set_rate(max(0.25, min(4.0, float(r))))
        except Exception:                                  # noqa: BLE001
            pass

    def set_paused(self, paused: bool):
        try:
            self.p.set_pause(1 if paused else 0)
        except Exception:                                  # noqa: BLE001
            pass

    def stop(self):
        try:
            if self.p is not None:
                self.p.stop()
                self.p.release()
            if self.inst is not None:
                self.inst.release()
        except Exception:                                  # noqa: BLE001
            pass
        self.p = self.inst = None
        # VLC остановлен — кадровые буферы (до 4 × 4K) больше не нужны. Отпускаем их через пару секунд:
        # кадр, взятый на отрисовку прямо сейчас, ещё может смотреть в буфер.
        with self._mx:
            old = (self._bufs, self._retired)
            self.img = QImage()
            self._bufs, self._retired = [], []
            self._ready = self._shown = self._prev = self._writing = -1
        if old[0] or old[1]:
            QTimer.singleShot(3000, lambda _keep=old: None)


# ------------------------------------------------------------------ #
#  Слои: геометрия, анимация, рисование                               #
# ------------------------------------------------------------------ #

def layer_base_size(L, W, H, src_size: QSize | None, text_rect: QRectF | None = None):
    """Размер слоя без анимации (ширина, высота) в пикселях окна — с учётом свободного растяжения."""
    sx, sy = L.get("stretch_x", 1.0), L.get("stretch_y", 1.0)
    k = L["kind"]
    if L.get("box") == "window":                           # доли окна по каждой оси (тянется вместе с окном)
        return L["size"] * W * sx, L["size"] / max(0.05, L.get("aspect", 1.0)) * H * sy
    m = min(W, H)
    if k in ("media", "video"):
        w = L["size"] * m
        if src_size is not None and src_size.width() > 0:
            return w * sx, w * src_size.height() / src_size.width() * sy
        return w * sx, (w if k == "media" else w * 9 / 16) * sy
    if k == "text" and text_rect is not None:
        return text_rect.width() * sx, text_rect.height() * sy
    w = L["size"] * m
    return w * sx, w / max(0.05, L.get("aspect", 1.0)) * sy


def _one_motion(a, spd, amt, react, t, pulse, W, H, seed, ph, out):
    """Одна анимация из стопки — добавляется к out (сдвиги и углы складываются, масштабы умножаются)."""
    if a == "float":
        out["dy"] += math.sin(t * spd * 1.3 + ph) * amt * 0.02 * H
        out["dx"] += math.sin(t * spd * 0.7 + ph * 2) * amt * 0.006 * W
    elif a == "sway":
        out["rot"] += math.sin(t * spd * 1.6 + ph) * amt * 12
    elif a == "spin":
        out["rot"] += (t * spd * 50 * max(0.1, amt)) % 360
    elif a == "pulse":
        k = 1 + math.sin(t * spd * 3.2 + ph) * 0.07 * amt
        out["sx"] *= k
        out["sy"] *= k
    elif a == "bounce":
        out["dy"] += -abs(math.sin(t * spd * 2.6 + ph)) * amt * 0.05 * H
    elif a == "orbit":
        out["dx"] += math.cos(t * spd * 0.9 + ph) * amt * 0.05 * W
        out["dy"] += math.sin(t * spd * 0.9 + ph) * amt * 0.05 * H
    elif a == "drift":
        r = amt * 0.12
        out["dx"] += math.cos(t * spd * 0.25 + ph) * r * W
        out["dy"] += math.sin(t * spd * 0.37 + ph) * r * 0.6 * H
    elif a == "wobble":
        k = math.sin(t * spd * 5.5 + ph) * 0.07 * amt
        out["sx"] *= 1 + k
        out["sy"] *= 1 - k
    elif a == "breathe":
        k = 0.5 + 0.5 * math.sin(t * spd * 1.1 + ph)
        out["sx"] *= 1 + k * 0.05 * amt
        out["sy"] *= 1 + k * 0.05 * amt
        out["op"] *= 1 - (1 - k) * 0.35 * min(1.0, amt)
    elif a == "beat":
        k = 1 + pulse.beat * 0.28 * amt * (0.3 + react)
        out["sx"] *= k
        out["sy"] *= k
    elif a == "level":
        k = 1 + pulse.level * 0.7 * amt * (0.3 + react)
        out["sx"] *= k
        out["sy"] *= k
    elif a == "bass":
        k = 1 + pulse.bass * 0.45 * amt * (0.3 + react)
        out["sx"] *= k
        out["sy"] *= k
    elif a == "shake":
        j = pulse.beat * amt * (0.3 + react) * 0.012 * min(W, H)
        fr = pulse.frame
        out["dx"] += (_hrand(fr, seed, 1) - 0.5) * 2 * j
        out["dy"] += (_hrand(fr, seed, 2) - 0.5) * 2 * j
    elif a == "glitch":
        fr = pulse.frame
        if (math.sin(t * spd * 17 + ph) > 0.93) or (pulse.beat > 0.7 and react > 0.2):
            out["dx"] += (_hrand(fr, seed, 3) - 0.5) * 0.04 * W * amt
            out["op"] *= 0.55 + _hrand(fr, seed, 4) * 0.45
    elif a == "fade":
        out["op"] *= 0.55 + 0.45 * math.sin(t * spd * 2.0 + ph)
    elif a == "flip3d":
        out["ty"] += (t * spd * 60 * max(0.1, amt)) % 360
    elif a == "swing3d":
        out["ty"] += math.sin(t * spd * 1.2 + ph) * 28 * amt
        out["tx"] += math.cos(t * spd * 0.9 + ph) * 10 * amt
    elif a == "tilt_beat":
        out["tx"] += pulse.beat * 22 * amt * (0.3 + react)


def layer_motion(L, t, pulse, W, H, seed) -> dict:
    """Все анимации слоя сразу (стопка): dx, dy, rot, sx, sy, op, tx/ty (3D-наклон, °)."""
    out = {"dx": 0.0, "dy": 0.0, "rot": 0.0, "sx": 1.0, "sy": 1.0, "op": 1.0, "tx": 0.0, "ty": 0.0}
    anims = L.get("anims")
    if anims is None and L.get("anim", "none") != "none":            # тема старого формата без normalize
        anims = [{"type": L["anim"], "speed": L.get("anim_speed", 1.0), "amount": L.get("anim_amount", 1.0),
                  "react": L.get("react", 0.6)}]
    for j, a in enumerate(anims or ()):
        if not a.get("on", True):
            continue
        _one_motion(a.get("type"), a.get("speed", 1.0), a.get("amount", 1.0), a.get("react", 0.6), t, pulse,
                    W, H, seed + j * 7, seed * 2.399 + j * 1.618, out)
    return out


def _frames_of(rt, L, src):
    """Номер кадра содержимого, которое меняется само (GIF, видео, эффект) — или None."""
    k = L["kind"]
    if k == "effect":
        sh = getattr(rt, "scripts", None)
        ent = sh.effects.get(L["id"]) if sh is not None else None
        return ent[1].gen if ent is not None else None
    if k == "script":
        sh = getattr(rt, "scripts", None)
        return sh.widget_gen(L) if sh is not None else None
    if src is not None and (k == "video" or getattr(src, "animated", False)):
        return src.frame_no
    return None


def layer_animation(L, src) -> str:
    """'motion' — двигается/перерисовывается каждый кадр, 'frames' — меняются только кадры
    (GIF, видео, эффект), '' — статичный."""
    if tk.layer_animated(L) or (L.get("anims") is None and L.get("anim", "none") != "none"):
        return "motion"
    if (L.get("behavior") or "").strip() or L.get("id") in tk.SCRIPT_MOVED:
        return "motion"
    if L["kind"] in ("video", "effect", "script"):         # перерисовать, когда готов новый кадр содержимого
        return "frames"
    if src is not None and getattr(src, "animated", False):
        return "frames"
    return ""


class LayerPainter:
    """Рисует слои (картинка/GIF, текст, фигура) — общий для фона, верхнего слоя и редактора.
    Текст и фигуры сначала рисуются в спрайт (со свечением, градиентом, контуром) —
    дальше каждый кадр только блит с поворотом/масштабом."""

    def __init__(self, runtime):
        self.rt = runtime
        self._text_cache = {}
        self._sprites = {}
        self._fit = {}                                     # id слоя → (ключ, кадр в размер слоя)

    def clear(self):
        self._text_cache.clear()
        self._sprites.clear()
        self._fit.clear()

    def text_family(self, L) -> str:
        f = L.get("font") or ""
        if is_font_asset(f):
            return self.rt.font_family(f) or self.rt.theme_font()
        return f or self.rt.theme_font()

    def text_path(self, L, W, H):
        px = max(6.0, L["size"] * min(W, H) * 0.3)
        fam = self.text_family(L)
        key = (L["text"], fam, round(px, 1), bool(L.get("bold")), bool(L.get("italic")))
        path = self._text_cache.get(key)
        if path is None:
            f = QFont(fam) if fam else QFont()
            f.setPixelSize(int(px))
            f.setBold(bool(L.get("bold")))
            f.setItalic(bool(L.get("italic")))
            path = QPainterPath()
            for i, line in enumerate((L["text"] or " ").split("\n")):
                path.addText(QPointF(0, i * px * 1.15), f, line or " ")
            br = path.boundingRect()
            path.translate(-br.center())
            if len(self._text_cache) > 200:
                self._text_cache.clear()
            self._text_cache[key] = path
        return path

    def source(self, L):
        """Картинка/GIF или видео слоя (или None)."""
        k = L["kind"]
        if k == "media":
            return self.rt.media(L.get("src"))
        if k == "video":
            fn = getattr(self.rt, "video_layer", None)
            return fn(L.get("src"), L.get("speed", 1.0)) if fn is not None else None
        return None

    def geometry(self, L, W, H, t, pulse, seed, still=False):
        """Где и как рисовать слой в этом кадре. still — без анимаций и поведения (рамка в редакторе)."""
        sh = getattr(self.rt, "scripts", None)
        mo = sh.motion.get(L["id"]) if (sh is not None and not still) else None
        eff = None
        if mo is not None and (mo.get("label") or mo.get("tint")):
            eff = dict(L)                                  # поведение подменило текст/цвет
            if mo.get("label") and L["kind"] == "text":
                eff["text"] = mo["label"][:200]
            if mo.get("tint"):
                eff["color"] = mo["tint"]
        LL = eff or L
        src = self.source(L)
        tr = self.text_path(LL, W, H).boundingRect() if L["kind"] == "text" else None
        ss = src.size() if src is not None and src.valid() else None
        if L["kind"] == "video" and src is not None and hasattr(src, "source_size") and src.source_size().width():
            ss = src.source_size()
        w, h = layer_base_size(L, W, H, ss, tr)
        if still:
            m = {"dx": 0.0, "dy": 0.0, "rot": 0.0, "sx": 1.0, "sy": 1.0, "op": 1.0, "tx": 0.0, "ty": 0.0}
        else:
            m = layer_motion(L, t, pulse, W, H, seed)
        hidden = False
        if mo is not None:
            m["dx"] += mo["dx"]
            m["dy"] += mo["dy"]
            m["rot"] += mo["rot"]
            m["sx"] *= mo["scale"] * mo["sx"]
            m["sy"] *= mo["scale"] * mo["sy"]
            m["op"] *= max(0.0, min(1.0, mo["opacity"]))
            m["tx"] += mo["tilt_x"]
            m["ty"] += mo["tilt_y"]
            hidden = mo["hidden"]
        return {"cx": L["x"] * W + m["dx"], "cy": L["y"] * H + m["dy"], "w": w, "h": h,
                "rot": L.get("rot", 0.0) + m["rot"], "sx": m["sx"], "sy": m["sy"],
                "op": L.get("opacity", 1.0) * m["op"], "tx": L.get("tilt_x", 0.0) + m["tx"],
                "ty": L.get("tilt_y", 0.0) + m["ty"], "src": src, "m": self.margin(L, w, h), "hidden": hidden,
                "L": eff}

    @staticmethod
    def margin(L, w, h) -> float:
        k = L["kind"]
        if k in ("media", "video", "effect"):
            return 2.0
        if k == "script":
            return 2.0 if L.get("clip", True) else max(w, h) * 0.35 + 8
        m = 3.0
        if L.get("glow", 0) > 0.01:
            m += max(w, h) * 0.16 * (0.5 + L["glow"]) + 6
        if L.get("outline"):
            m += max(1.0, h * 0.03)
        return m

    @staticmethod
    def bounds(geo, margin=2.0) -> QRectF:
        """Прямоугольник на экране, который слой может закрасить (с учётом поворота, свечения и 3D-наклона)."""
        m = geo.get("m", 0.0)
        w, h = (geo["w"] + 2 * m) * abs(geo["sx"]), (geo["h"] + 2 * m) * abs(geo["sy"])
        tx, ty = geo.get("tx", 0.0), geo.get("ty", 0.0)
        if tx or ty:                                       # перспектива увеличивает ближний край
            k = 1.0 + max(abs(math.sin(math.radians(tx))), abs(math.sin(math.radians(ty)))) * max(w, h) / 1500.0
            w, h = w * k + 8, h * k + 8
        a = math.radians(geo["rot"])
        ca, sa = abs(math.cos(a)), abs(math.sin(a))
        bw, bh = w * ca + h * sa, w * sa + h * ca
        return QRectF(geo["cx"] - bw / 2 - margin, geo["cy"] - bh / 2 - margin, bw + 2 * margin, bh + 2 * margin)

    @staticmethod
    def contains(geo, pt: QPointF, pad=4.0) -> bool:
        """Попадание точки в сам слой (с поворотом), для редактора."""
        dx, dy = pt.x() - geo["cx"], pt.y() - geo["cy"]
        a = -math.radians(geo["rot"])
        x = dx * math.cos(a) - dy * math.sin(a)
        y = dx * math.sin(a) + dy * math.cos(a)
        w, h = geo["w"] * abs(geo["sx"]), geo["h"] * abs(geo["sy"])
        return abs(x) <= w / 2 + pad and abs(y) <= h / 2 + pad

    def paint(self, p: QPainter, L, geo, W, H):
        if geo["op"] <= 0.003 or geo["w"] < 0.5 or geo["h"] < 0.5 or geo.get("hidden"):
            return
        L = geo.get("L") or L
        p.save()
        p.translate(geo["cx"], geo["cy"])
        if geo["rot"]:
            p.rotate(geo["rot"])
        tx, ty = geo.get("tx", 0.0), geo.get("ty", 0.0)
        if tx or ty:                                       # 3D-наклон: поворот вокруг осей X/Y с перспективой
            tr = QTransform()
            if ty:
                tr.rotate(ty, Qt.Axis.YAxis, 1400.0)
            if tx:
                tr.rotate(tx, Qt.Axis.XAxis, 1400.0)
            p.setTransform(tr, True)
        p.scale(geo["sx"] * (-1 if L.get("flip") else 1), geo["sy"])
        p.setOpacity(max(0.0, min(1.0, geo["op"])))
        p.setCompositionMode(_BLEND.get(L.get("blend"), _BLEND["normal"]))
        p.setRenderHint(_SMOOTH)
        w, h = geo["w"], geo["h"]
        k = L["kind"]
        if k == "media":
            src = geo["src"]
            if src is not None and src.valid():
                pm = self._fitted(L, src, src.pix, w, h)
                if pm is not None:
                    p.drawPixmap(QRectF(-w / 2, -h / 2, w, h), pm, QRectF(pm.rect()))
                else:
                    p.drawPixmap(QRectF(-w / 2, -h / 2, w, h), src.pix, QRectF(src.pix.rect()))
            else:                                          # файла нет — пунктирная рамка-заглушка
                self._stub(p, w, h)
        elif k == "video":
            src = geo["src"]
            if src is not None and src.valid():
                img = self._fitted(L, src, src.img, w, h)
                p.drawImage(QRectF(-w / 2, -h / 2, w, h), img if img is not None else src.img)
            else:
                self._stub(p, w, h, L.get("name") or "видео")
        elif k == "script":
            sh = getattr(self.rt, "scripts", None)
            err = sh.error(L) if sh is not None else ""
            if sh is None or not sh.paint_widget(p, L, w, h):
                import layer_fx
                layer_fx.placeholder(p, w, h, err or ("◈ " + (L.get("name") or L.get("widget") or "виджет")), bool(err))
            elif err:
                p.resetTransform()
                import layer_fx
                p.translate(geo["cx"], geo["cy"])
                layer_fx.placeholder(p, min(w, 320), min(h, 60), "Ошибка: " + err, True)
        elif k == "effect":
            sh = getattr(self.rt, "scripts", None)
            ent = sh.effects.get(L["id"]) if sh is not None else None
            img = ent[1].img if ent is not None else None
            if img is not None and not img.isNull():
                # MilkDrop и Fluid по умолчанию сплошные, как в своих темах (раньше просвечивали фоном)
                solid = L.get("opaque", L.get("effect") in ("milkdrop", "fluid"))
                if not solid and L.get("blend", "normal") in ("normal", None, ""):
                    # эффекты считаются на чёрном фоне — «экраном» чёрное становится прозрачным,
                    # и эффект не закрывает видео/фон сплошным прямоугольником
                    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Screen)
                p.drawImage(QRectF(-w / 2, -h / 2, w, h), img)
            else:
                import layer_fx
                layer_fx.placeholder(p, w, h, dict(tk.EFFECTS).get(L.get("effect"), "эффект"))
        else:
            if (L.get("behavior") or "").strip():
                sh = getattr(self.rt, "scripts", None)
                err = sh.error(L) if sh is not None else ""
                if err:
                    import layer_fx
                    layer_fx.placeholder(p, max(w, 160), min(max(h, 40), 80), "Ошибка: " + err, True)
            pm, m = self.sprite(L, w, h, W, H)
            if pm is not None:
                p.drawPixmap(QRectF(-w / 2 - m, -h / 2 - m, w + 2 * m, h + 2 * m), pm, QRectF(pm.rect()))
        p.restore()

    def _fitted(self, L, src, img, w, h):
        """Кадр картинки/GIF/видео, заранее уменьшенный (увеличенный) до размера слоя на экране.
        Масштабируется один раз на кадр содержимого, а не при каждой перерисовке: над слоем
        перерисовываются пластинка, спектр, кнопки — 60 раз в секунду, и каждый раз плавно
        масштабировать большую гифку или кадр видео заново было главным тормозом.
        None — рисовать как есть (кадр уже нужного размера или слой гигантский)."""
        dpr = self.rt.dpr
        tw, th = max(1, int(round(w * dpr))), max(1, int(round(h * dpr)))
        iw, ih = img.width(), img.height()
        if iw < 1 or ih < 1 or tw * th > 16_000_000:
            return None
        if abs(iw - tw) <= 1 and abs(ih - th) <= 1:        # видео от VLC обычно уже в размер
            return None
        key = (id(src), getattr(src, "frame_no", 0), tw, th, id(img) if L["kind"] == "media" and
               not getattr(src, "animated", False) else 0)
        c = self._fit.get(L["id"])
        if c is not None and c[0] == key:
            return c[1]
        out = img.scaled(tw, th, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        out.setDevicePixelRatio(dpr)
        if len(self._fit) > 64:
            self._fit.clear()
        self._fit[L["id"]] = (key, out)
        return out

    @staticmethod
    def _stub(p, w, h, text=""):
        p.setRenderHint(_AA)
        p.setPen(QPen(QColor(255, 255, 255, 150), 2, Qt.PenStyle.DashLine))
        p.setBrush(QColor(0, 0, 0, 60))
        p.drawRect(QRectF(-w / 2, -h / 2, w, h))
        if text and w > 30 and h > 16:
            p.setPen(QColor(255, 255, 255, 190))
            p.drawText(QRectF(-w / 2, -h / 2, w, h), int(Qt.AlignmentFlag.AlignCenter), text)

    # ── спрайты ── #

    def sprite(self, L, w, h, W, H):
        dpr = self.rt.dpr
        key = (L["kind"], L.get("text"), L.get("font"), bool(L.get("bold")), bool(L.get("italic")),
               L.get("shape"), L.get("color"), L.get("color2"), L.get("outline"), round(L.get("glow", 0.0), 3),
               round(w, 1), round(h, 1), dpr, self.text_family(L))
        c = self._sprites.get(L["id"])
        if c is not None and c[0] == key:
            return c[1], c[2]
        if L["kind"] == "text":
            path = QPainterPath(self.text_path(L, W, H))
            br = path.boundingRect()
            if br.width() > 0.5 and br.height() > 0.5 and (abs(w - br.width()) > 0.5 or abs(h - br.height()) > 0.5):
                path = QTransform.fromScale(w / br.width(), h / br.height()).map(path)   # свободное растяжение
        else:
            path = self.shape_path(L.get("shape", "circle"), w, h)
        m = self.margin(L, w, h)
        sw, sh = w + 2 * m, h + 2 * m
        if sw * sh * dpr * dpr > 36_000_000:               # гигантский слой не кэшируем
            return None, 0.0
        pm = QPixmap(max(1, int(math.ceil(sw * dpr))), max(1, int(math.ceil(sh * dpr))))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        q = QPainter(pm)
        q.setRenderHint(_AA)
        q.setRenderHint(_SMOOTH)
        col = _qc(L.get("color"))
        glow = float(L.get("glow", 0.0))
        if glow > 0.01:
            halo = self._halo(path, sw, sh, _qc(L.get("color2") or L.get("color")), glow)
            q.setOpacity(min(1.0, 0.55 + 0.5 * glow))
            q.drawImage(QRectF(0, 0, sw, sh), halo)
            if glow > 0.55:
                q.setOpacity(min(1.0, (glow - 0.55) * 2.0))
                q.drawImage(QRectF(0, 0, sw, sh), halo)
            q.setOpacity(1.0)
        q.translate(sw / 2, sh / 2)
        rect = path.boundingRect() if L["kind"] == "text" else QRectF(-w / 2, -h / 2, w, h)
        if L.get("color2"):
            g = QLinearGradient(rect.topLeft(), rect.bottomRight())
            g.setColorAt(0, col)
            g.setColorAt(1, _qc(L["color2"]))
            brush = QBrush(g)
        else:
            brush = QBrush(col)
        outline = L.get("outline")
        q.setPen(QPen(_qc(outline), max(1.0, rect.height() * 0.03), Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin) if outline else Qt.PenStyle.NoPen)
        q.setBrush(brush)
        q.drawPath(path)
        q.end()
        if len(self._sprites) > 96:
            self._sprites.clear()
        self._sprites[L["id"]] = (key, pm, m)
        return pm, m

    @staticmethod
    def _halo(path, sw, sh, color, strength) -> QImage:
        """Мягкое свечение: силуэт в 1/4 размера, размытый — потом растягивается под фигуру."""
        k = 4.0
        iw, ih = max(4, int(sw / k)), max(4, int(sh / k))
        img = QImage(iw, ih, _ARGB)
        img.fill(Qt.GlobalColor.transparent)
        q = QPainter(img)
        q.setRenderHint(_AA)
        q.scale(iw / sw, ih / sh)
        q.translate(sw / 2, sh / 2)
        q.setPen(QPen(color, max(sw, sh) * 0.015 + 3))
        q.setBrush(color)
        q.drawPath(path)
        q.end()
        r = max(1, int(min(iw, ih) * 0.07 * (0.6 + strength)))
        return box_blur(img, r, 3)

    @staticmethod
    def shape_path(kind, w, h) -> QPainterPath:
        path = QPainterPath()
        r = QRectF(-w / 2, -h / 2, w, h)
        if kind == "circle":
            path.addEllipse(r)
        elif kind == "rect":
            path.addRect(r)
        elif kind == "rounded":
            path.addRoundedRect(r, min(w, h) * 0.2, min(w, h) * 0.2)
        elif kind == "ring":
            path.addEllipse(r)
            inner = QPainterPath()
            inner.addEllipse(r.adjusted(w * 0.16, h * 0.16, -w * 0.16, -h * 0.16))
            path = path.subtracted(inner)
        elif kind == "star":
            n = 5
            for i in range(n * 2):
                a = i * math.pi / n - math.pi / 2
                rr = 0.5 if i % 2 == 0 else 0.21
                pt = QPointF(math.cos(a) * rr * w, math.sin(a) * rr * h)
                if i == 0:
                    path.moveTo(pt)
                else:
                    path.lineTo(pt)
            path.closeSubpath()
        elif kind == "heart":
            path.moveTo(0, h * 0.32)
            path.cubicTo(-w * 0.62, -h * 0.12, -w * 0.32, -h * 0.62, 0, -h * 0.26)
            path.cubicTo(w * 0.32, -h * 0.62, w * 0.62, -h * 0.12, 0, h * 0.32)
        elif kind == "triangle":
            path.moveTo(0, -h / 2)
            path.lineTo(w / 2, h / 2)
            path.lineTo(-w / 2, h / 2)
            path.closeSubpath()
        else:                                              # blob
            rnd = random.Random(int(w * 7 + h))
            pts = []
            for i in range(8):
                a = i / 8 * 2 * math.pi
                rr = 0.38 + rnd.random() * 0.12
                pts.append(QPointF(math.cos(a) * rr * w, math.sin(a) * rr * h))
            path.moveTo((pts[0] + pts[-1]) / 2)
            for i in range(8):
                nxt = pts[(i + 1) % 8]
                path.quadTo(pts[i], (pts[i] + nxt) / 2)
            path.closeSubpath()
        return path


class _LayerTracker:
    """Какие прямоугольники перерисовать для анимированных слоёв (старое + новое положение)."""

    def __init__(self):
        self.rects = {}
        self.frames = {}

    def reset(self):
        self.rects.clear()
        self.frames.clear()

    def region(self, rt, layers, z, W, H, hidden) -> QRegion:
        reg = QRegion()
        t = rt.pulse.t
        for i, L in enumerate(layers):
            if L["z"] != z or not L.get("visible", True) or L["id"] in hidden:
                continue
            src = rt.painter.source(L)
            how = layer_animation(L, src)
            if not how:
                continue
            if how == "frames":
                fno = _frames_of(rt, L, src)
                if fno is None or self.frames.get(L["id"]) == fno:
                    continue
                self.frames[L["id"]] = fno
            geo = rt.painter.geometry(L, W, H, t, rt.pulse, i)
            r = LayerPainter.bounds(geo).toAlignedRect()
            reg = reg.united(r)
            old = self.rects.get(L["id"])
            if old is not None and old != r:
                reg = reg.united(old)
            self.rects[L["id"]] = r
        return reg


def paint_layers(p: QPainter, rt, layers, z, W, H, region: QRegion, hidden=()):
    t = rt.pulse.t
    for i, L in enumerate(layers):
        if L["z"] != z or not L.get("visible", True) or L["id"] in hidden:
            continue
        geo = rt.painter.geometry(L, W, H, t, rt.pulse, i)
        if region.intersects(LayerPainter.bounds(geo).toAlignedRect()):
            rt.painter.paint(p, L, geo, W, H)


# ------------------------------------------------------------------ #
#  Частицы                                                            #
# ------------------------------------------------------------------ #

_P_BASE = {"dust": 16, "snow": 16, "stars": 24, "fireflies": 32, "hearts": 32, "bubbles": 40,
           "sakura": 28, "confetti": 16, "image": 40, "rain": 28}


class Particles:
    """Снег, дождь, звёзды, светлячки, пузыри, сердечки, сакура, конфетти, пыль, своя картинка."""

    def __init__(self, cfg, palette, runtime):
        self.rt = runtime
        self.rnd = random.Random(7)
        self.items = []
        self.sprites = {}
        self._scaled = {}
        self.cfg = {}
        self.kind = "none"
        self._pal = palette
        self.configure(cfg, palette)

    def configure(self, cfg, palette):
        """Новые настройки. Частицы того же вида не пересоздаются — меняются вид и количество."""
        old_kind, old = self.kind, self.cfg
        self.cfg = dict(cfg)
        self.kind = cfg.get("kind", "none")
        pal_changed = self.kind == "confetti" and (palette.get("accent"), palette.get("accent2")) != \
            (self._pal.get("accent"), self._pal.get("accent2"))
        self._pal = palette
        if not self.sprites or pal_changed or (old_kind, old.get("color"), old.get("image")) != \
                (self.kind, cfg.get("color"), cfg.get("image")):
            self.sprites = {}
            self._scaled = {}
            self._build_sprites()
        n = int(cfg.get("count", 60))
        if old_kind != self.kind or old.get("size") != cfg.get("size"):
            self.items = [self._spawn(initial=True) for _ in range(n)]
        elif len(self.items) < n:
            self.items += [self._spawn(initial=True) for _ in range(n - len(self.items))]
        else:
            del self.items[n:]

    def _sprite(self, color, size, painter_fn):
        dpr = self.rt.dpr
        pm = QPixmap(int(size * dpr), int(size * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        q = QPainter(pm)
        q.setRenderHint(_AA)
        painter_fn(q, size, QColor(color))
        q.end()
        return pm

    def _build_sprites(self):
        k = self.kind
        col = self.cfg.get("color", "#ffffff")

        def soft(q, s, c):
            g = QRadialGradient(s / 2, s / 2, s / 2)
            c0 = QColor(c); c0.setAlpha(230)
            c1 = QColor(c); c1.setAlpha(0)
            g.setColorAt(0, c0); g.setColorAt(0.35, c0); g.setColorAt(1, c1)
            q.setPen(Qt.PenStyle.NoPen); q.setBrush(g); q.drawEllipse(QRectF(0, 0, s, s))

        def glow(q, s, c):
            g = QRadialGradient(s / 2, s / 2, s / 2)
            c0 = QColor(c); c0.setAlpha(255)
            c1 = QColor(c); c1.setAlpha(70)
            c2 = QColor(c); c2.setAlpha(0)
            g.setColorAt(0, QColor(255, 255, 255, 255)); g.setColorAt(0.12, c0); g.setColorAt(0.35, c1)
            g.setColorAt(1, c2)
            q.setPen(Qt.PenStyle.NoPen); q.setBrush(g); q.drawEllipse(QRectF(0, 0, s, s))

        def sparkle(q, s, c):
            glow(q, s, c)
            q.setPen(QPen(QColor(c), s * 0.06))
            q.drawLine(QPointF(s / 2, s * 0.05), QPointF(s / 2, s * 0.95))
            q.drawLine(QPointF(s * 0.05, s / 2), QPointF(s * 0.95, s / 2))

        def bubble(q, s, c):
            cc = QColor(c); cc.setAlpha(150)
            q.setPen(QPen(cc, max(1.0, s * 0.05)))
            fill = QColor(c); fill.setAlpha(25)
            q.setBrush(fill)
            q.drawEllipse(QRectF(s * 0.06, s * 0.06, s * 0.88, s * 0.88))
            q.setPen(Qt.PenStyle.NoPen); q.setBrush(QColor(255, 255, 255, 170))
            q.drawEllipse(QRectF(s * 0.25, s * 0.22, s * 0.18, s * 0.12))

        def heart(q, s, c):
            path = LayerPainter.shape_path("heart", s * 0.9, s * 0.9)
            path.translate(s / 2, s / 2)
            q.setPen(Qt.PenStyle.NoPen); q.setBrush(QColor(c)); q.drawPath(path)

        def petal(q, s, c):
            path = QPainterPath()
            path.moveTo(s * 0.5, s * 0.05)
            path.cubicTo(s * 0.95, s * 0.3, s * 0.8, s * 0.85, s * 0.5, s * 0.95)
            path.cubicTo(s * 0.2, s * 0.85, s * 0.05, s * 0.3, s * 0.5, s * 0.05)
            g = QLinearGradient(0, 0, s, s)
            g.setColorAt(0, QColor(c).lighter(130)); g.setColorAt(1, QColor(c))
            q.setPen(Qt.PenStyle.NoPen); q.setBrush(g); q.drawPath(path)

        def rect(q, s, c):
            q.fillRect(QRectF(s * 0.25, 0, s * 0.5, s), QColor(c))

        def drop(q, s, c):
            g = QLinearGradient(0, 0, 0, s)
            c0 = QColor(c); c0.setAlpha(0)
            c1 = QColor(c); c1.setAlpha(170)
            g.setColorAt(0, c0); g.setColorAt(1, c1)
            q.fillRect(QRectF(s * 0.42, 0, s * 0.16, s), g)

        white = col.lower() in ("#ffffff", "#fff")
        if k in ("dust", "snow"):
            self.sprites["a"] = self._sprite(col, 16, soft)
        elif k == "rain":
            self.sprites["a"] = self._sprite(col if not white else "#bcd7ff", 28, drop)
        elif k == "stars":
            self.sprites["a"] = self._sprite(col, 24, sparkle)
        elif k == "fireflies":
            self.sprites["a"] = self._sprite(col if not white else "#d8ff6a", 32, glow)
        elif k == "bubbles":
            self.sprites["a"] = self._sprite(col, 40, bubble)
        elif k == "hearts":
            self.sprites["a"] = self._sprite(col if not white else "#ff5c8a", 32, heart)
        elif k == "sakura":
            self.sprites["a"] = self._sprite(col if not white else "#ffb7cf", 28, petal)
        elif k == "confetti":
            cols = [self._pal.get("accent", "#fff"), self._pal.get("accent2", "#aaa"), "#ffd60a", "#3a86ff",
                    "#ff006e", "#06d6a0"]
            for i, c in enumerate(cols):
                self.sprites[i] = self._sprite(c, 16, rect)
        elif k == "image":
            src = self.rt.media(self.cfg.get("image"))
            pm = src.pix if src is not None and src.valid() else QPixmap()
            if not pm.isNull():
                s = int(64 * self.rt.dpr)
                sp = pm.scaled(s, s, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                sp.setDevicePixelRatio(self.rt.dpr)
                self.sprites["a"] = sp

    def _spawn(self, initial=False):
        r = self.rnd
        k = self.kind
        size = self.cfg.get("size", 1.0)
        it = {"x": r.random(), "y": r.random() if initial else -0.05, "ph": r.random() * 6.28,
              "rot": r.random() * 360, "vr": (r.random() - 0.5) * 120, "s": size, "a": 1.0,
              "vx": 0.0, "vy": 0.0, "c": r.randrange(6), "r": None}
        if k == "dust":
            it.update(vx=(r.random() - 0.5) * 0.01, vy=r.random() * 0.012 + 0.002, s=size * r.uniform(0.15, 0.4))
        elif k == "snow":
            it.update(vy=r.uniform(0.03, 0.09), s=size * r.uniform(0.25, 0.8))
        elif k == "rain":
            it.update(vy=r.uniform(0.8, 1.4), vx=0.1, s=size * r.uniform(0.6, 1.2))
        elif k == "stars":
            it.update(s=size * r.uniform(0.3, 0.9))
        elif k == "fireflies":
            it.update(vx=(r.random() - 0.5) * 0.03, vy=(r.random() - 0.5) * 0.03, s=size * r.uniform(0.4, 0.9))
        elif k in ("bubbles", "hearts"):
            it.update(vy=-r.uniform(0.025, 0.07), s=size * r.uniform(0.35, 1.0))
            if not initial:
                it["y"] = 1.05
        elif k in ("sakura", "image"):
            it.update(vy=r.uniform(0.04, 0.09), vx=r.uniform(0.01, 0.04), s=size * r.uniform(0.5, 1.0))
        elif k == "confetti":
            it.update(vy=r.uniform(0.06, 0.14), vx=(r.random() - 0.5) * 0.03, s=size * r.uniform(0.5, 1.0),
                      vr=(r.random() - 0.5) * 540)
        return it

    def _rect(self, it, W, H) -> QRectF:
        grow = round(0.35 * self.rt.pulse.level * self.cfg.get("react", 0.5), 2)
        s = _P_BASE.get(self.kind, 32) * it["s"] * (1 + grow)
        if self.kind == "rain":
            return QRectF(it["x"] * W - s * 0.2, it["y"] * H - s, s * 0.4, s)
        return QRectF(it["x"] * W - s / 2, it["y"] * H - s / 2, s, s)

    def step(self, dt, W, H) -> QRegion:
        """Сдвинуть частицы. Возвращает область для перерисовки (старые и новые места)."""
        if not self.items or self.kind == "none":
            return QRegion()
        pulse = self.rt.pulse
        sp = self.cfg.get("speed", 1.0) * (1 + self.cfg.get("react", 0.5) * pulse.level * 1.6)
        k = self.kind
        reg = QRegion()
        t = pulse.t
        rnd = self.rnd
        G = 48
        cells = set() if len(self.items) > 100 else None
        for i, it in enumerate(self.items):
            if k == "stars":
                a = 0.25 + 0.75 * (0.5 + 0.5 * math.sin(t * (1.2 + it["ph"] * 0.3) + it["ph"])) \
                    * (0.7 + 0.6 * pulse.beat)
                if abs(a - it["a"]) < 0.04 and it["r"] is not None:
                    continue                               # звезда почти не изменилась — не трогаем
                it["a"] = a
            elif k == "fireflies":
                it["vx"] = max(-0.03, min(0.03, it["vx"] + (rnd.random() - 0.5) * dt * 0.04))
                it["vy"] = max(-0.03, min(0.03, it["vy"] + (rnd.random() - 0.5) * dt * 0.04))
                it["x"] = (it["x"] + it["vx"] * dt * sp) % 1.0
                it["y"] = (it["y"] + it["vy"] * dt * sp) % 1.0
                it["a"] = 0.4 + 0.6 * (0.5 + 0.5 * math.sin(t * 2 + it["ph"]))
            else:
                sway = 0.0
                if k in ("snow", "bubbles", "hearts", "sakura", "image", "confetti"):
                    sway = math.sin(t * 1.3 + it["ph"]) * 0.015
                it["x"] += (it["vx"] + sway) * dt * sp
                it["y"] += it["vy"] * dt * sp
                it["rot"] += it["vr"] * dt * sp
                if k == "hearts":
                    it["a"] = max(0.0, min(1.0, it["y"] * 1.6))
                if it["y"] > 1.08 or it["y"] < -0.1 or it["x"] > 1.1 or it["x"] < -0.1:
                    if k == "dust":
                        it["y"] %= 1.0
                        it["x"] %= 1.0
                    else:
                        old = it["r"]
                        self.items[i] = it = self._spawn()
                        it["r"] = old
            pr = self._rect(it, W, H)
            it["pr"] = pr                                  # рисуем ровно там, где посчитали область
            m = 2
            if k in ("sakura", "confetti", "image"):
                # повёрнутый квадрат выходит за свой прямоугольник на (√2−1)/2 стороны — иначе
                # уголки картинки не стирались и за частицами тянулись чёрные шлейфы
                m += int(math.ceil(max(pr.width(), pr.height()) * 0.21))
            r = pr.toAlignedRect().adjusted(-m, -m, m, m)
            if cells is not None:
                for rr in (it["r"], r):
                    if rr is not None:
                        for cy in range(max(0, rr.top()) // G, max(0, rr.bottom()) // G + 1):
                            for cx in range(max(0, rr.left()) // G, max(0, rr.right()) // G + 1):
                                cells.add((cy, cx))
            else:
                if it["r"] is not None:
                    reg = reg.united(it["r"])
                reg = reg.united(r)
            it["r"] = r
        if cells is not None:
            # много частиц: клетки сетки вместо сотен отдельных прямоугольников — область строится одним вызовом
            rects = []                                     # строки сетки; соседние клетки — одной полосой
            run = None
            for cy, cx in sorted(cells):
                if run is not None and run[0] == cy and run[2] == cx:
                    run[2] = cx + 1
                    continue
                if run is not None:
                    rects.append(QRect(run[1] * G, run[0] * G, (run[2] - run[1]) * G, G))
                run = [cy, cx, cx + 1]
            if run is not None:
                rects.append(QRect(run[1] * G, run[0] * G, (run[2] - run[1]) * G, G))
            try:
                reg = QRegion()
                reg.setRects(rects)
            except (TypeError, AttributeError):
                reg = QRegion()
                for q in rects:
                    reg = reg.united(q)
        return reg

    def _sized(self, key, w, h):
        """Спрайт ровно нужного размера: рисуется простым копированием, без масштаба в QPainter
        (масштабирование при каждом рисовании в разы дороже)."""
        w, h = max(2, int(round(w))), max(2, int(round(h)))
        k = (key, w, h)
        pm = self._scaled.get(k)
        if pm is None:
            base = self.sprites.get(key)
            if base is None:
                return None
            dpr = self.rt.dpr
            pm = base.scaled(int(w * dpr), int(h * dpr), Qt.AspectRatioMode.IgnoreAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
            pm.setDevicePixelRatio(dpr)
            if len(self._scaled) > 320:
                self._scaled.clear()
            self._scaled[k] = pm
        return pm

    def paint(self, p: QPainter, W, H, region: QRegion | None = None):
        k = self.kind
        if k == "none" or not self.items:
            return
        p.setRenderHint(_SMOOTH)
        rot = k in ("sakura", "confetti", "image")
        for it in self.items:
            key = it["c"] if k == "confetti" else "a"
            spr = self.sprites.get(key)
            if spr is None:
                continue
            r = it.get("pr") or self._rect(it, W, H)
            if region is not None:
                m = int(math.ceil(max(r.width(), r.height())))
                if not region.intersects(r.toAlignedRect().adjusted(-m, -m, m, m)):
                    continue
            p.setOpacity(max(0.0, min(1.0, it.get("a", 1.0))))
            if not rot:
                pm = self._sized(key, r.width(), r.height())
                if pm is not None:
                    p.drawPixmap(QPointF(round(r.x()), round(r.y())), pm)
                continue
            if rot:
                p.save()
                p.translate(r.center())
                p.rotate(it["rot"])
                if k == "confetti":
                    p.scale(1.0, abs(math.sin(math.radians(it["rot"] * 1.7))) + 0.15)
                p.drawPixmap(QRectF(-r.width() / 2, -r.height() / 2, r.width(), r.height()), spr,
                             QRectF(spr.rect()))
                p.restore()
            else:
                p.drawPixmap(r, spr, QRectF(spr.rect()))
        p.setOpacity(1.0)


# ------------------------------------------------------------------ #
#  Живые обои (низкое разрешение → растягиваются)                     #
# ------------------------------------------------------------------ #

try:                                                       # C++-ядро (echoes_fx.dll): плазма живых обоев
    import native_fx as _NF
    _NATIVE_FX = _NF.available()
except Exception:                                          # noqa: BLE001
    _NF, _NATIVE_FX = None, False


class LiveWallpaper:
    """aurora / synthwave / plasma / starfield / matrix — кадр в QImage малого размера."""

    TARGET = {"aurora": 320, "plasma": 480 if _NATIVE_FX else 200, "starfield": 560, "synthwave": 640, "matrix": 900,
              "nebula": 640 if _NATIVE_FX else 260}

    def __init__(self, kind, bg, colors, speed, react, runtime):
        self.kind = kind
        self.rt = runtime
        self.t = 0.0
        self.img = QImage()
        self.size = QSize(0, 0)
        self.rnd = random.Random(3)
        self._stars = None
        self._grid_off = 0.0
        self._drops = None
        self._lut = None
        self._static = None
        self._sun = None
        self.configure(bg, colors, speed, react)

    def configure(self, bg, colors, speed, react):
        """Смена цветов/скорости без перезапуска анимации."""
        self.bg = bg
        self.colors = [QColor(c) for c in (colors or ["#7c5cff", "#00e5ff", "#ff3d9a"])]
        while len(self.colors) < 3:
            self.colors.append(QColor("#ffffff"))
        self.speed = speed
        self.react = react
        self._lut = None
        self._static = None
        self._sun = None

    def resize(self, W, H):
        s = max(W, H) / float(self.TARGET.get(self.kind, 400))
        w, h = max(32, int(W / max(1.0, s))), max(18, int(H / max(1.0, s)))
        if QSize(w, h) != self.size:
            old = self.img
            self.size = QSize(w, h)
            if self.kind in ("matrix", "starfield") and not old.isNull():
                # следы дождя/звёзд не теряем — растягиваем прошлый кадр
                self.img = old.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                                      Qt.TransformationMode.SmoothTransformation).convertToFormat(_ARGB)
            else:
                self.img = QImage(w, h, _ARGB)
                self.img.fill(QColor(self.bg))
            if self._drops is not None:
                cols = max(4, w // 11)
                d = self._drops[:cols]
                self._drops = d + [self.rnd.uniform(-h * 0.3, h) for _ in range(cols - len(d))]
                self._speeds = (self._speeds + [self.rnd.uniform(0.6, 1.4) for _ in range(cols)])[:cols]
            self._static = None
            self._sun = None
            self._lut = None

    def prewarm(self):
        """Дождь и звёзды сразу «в разгаре», а не с пустого экрана."""
        n = {"matrix": 45, "starfield": 12}.get(self.kind, 0)
        for _ in range(n):
            self.step(1 / 30.0)

    def step(self, dt):
        pulse = self.rt.pulse
        boost = 1 + self.react * (pulse.bass * 1.5 + pulse.beat * 1.5)
        self.t += dt * self.speed * (0.6 + 0.4 * boost)
        getattr(self, "_" + self.kind)(dt, boost)

    # ── туманность «Моей волны» (C++: nebula.cpp) ── #
    def _nebula(self, dt, boost):
        import ya_wave
        w, h = self.size.width(), self.size.height()
        cols = tuple(c.name() for c in (self.colors * 4)[:4])
        nb = getattr(self, "_neb", None)
        if nb is None:
            nb = self._neb = ya_wave.NebulaField()
            nb.set_scheme(cols, True)
            self._neb_cols, self._neb_cool = cols, 0.0
        elif cols != self._neb_cols:
            nb.set_scheme(cols)
            self._neb_cols = cols
        pulse = self.rt.pulse
        self._neb_cool = max(0.0, self._neb_cool - dt)
        if pulse.beat > 0.6 and self._neb_cool <= 0 and self.react > 0.05:
            nb.hit(min(1.0, pulse.beat * self.react * 1.3))
            self._neb_cool = 0.3
        img = nb.render(w, h, w * 0.5, h * 0.48, min(w, h) * 0.62, min(1.0, pulse.level * (0.4 + self.react)),
                        dt * self.speed)
        self.img = img.convertToFormat(_ARGB)

    # ── северное сияние ── #
    def _aurora(self, dt, boost):
        img, (w, h) = self.img, (self.size.width(), self.size.height())
        img.fill(QColor(self.bg))
        p = QPainter(img)
        p.setRenderHint(_AA)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        t = self.t
        for i, c in enumerate(self.colors[:4]):
            path = QPainterPath()
            base_y = h * (0.28 + 0.13 * i) + math.sin(t * 0.3 + i) * h * 0.06
            amp = h * (0.06 + 0.04 * math.sin(t * 0.21 + i * 1.7)) * (0.8 + 0.4 * boost)
            path.moveTo(0, base_y)
            n = 20
            for k in range(n + 1):
                x = w * k / n
                path.lineTo(x, base_y + math.sin(x / w * 6.3 * (1 + 0.25 * i) + t * (0.6 + 0.15 * i)) * amp)
            thick = h * (0.22 + 0.05 * boost)
            path.lineTo(w, base_y + thick)
            path.lineTo(0, base_y + thick)
            path.closeSubpath()
            g = QLinearGradient(0, base_y - amp, 0, base_y + thick)
            c0 = QColor(c); c0.setAlpha(0)
            c1 = QColor(c); c1.setAlpha(int(min(255, 110 * (0.7 + 0.3 * boost))))
            g.setColorAt(0.0, c0); g.setColorAt(0.25, c1); g.setColorAt(1.0, c0)
            p.fillPath(path, g)
        for i, c in enumerate(self.colors[:3]):
            x = w * (0.5 + 0.38 * math.sin(t * 0.17 + i * 2.1))
            y = h * (0.5 + 0.32 * math.cos(t * 0.13 + i * 1.3))
            r = max(w, h) * (0.32 + 0.06 * boost)
            g = QRadialGradient(x, y, r)
            c0 = QColor(c); c0.setAlpha(int(min(255, 60 * boost)))
            c1 = QColor(c); c1.setAlpha(0)
            g.setColorAt(0, c0); g.setColorAt(1, c1)
            p.fillRect(QRectF(x - r, y - r, 2 * r, 2 * r), g)
        p.end()

    # ── ретровейв ── #
    def _synthwave(self, dt, boost):
        img, (w, h) = self.img, (self.size.width(), self.size.height())
        hz = h * 0.58
        if self._static is None:                           # небо, ореол солнца, пол и диск солнца — один раз
            st = QImage(w, h, _ARGB)
            st.fill(QColor(self.bg))
            q = QPainter(st)
            q.setRenderHint(_AA)
            sky = QLinearGradient(0, 0, 0, hz)
            sky.setColorAt(0, QColor(self.bg))
            c0 = QColor(self.colors[0]); c0.setAlpha(140)
            sky.setColorAt(1, c0)
            q.fillRect(QRectF(0, 0, w, hz), sky)
            sr = h * 0.24
            glow = QRadialGradient(w / 2, hz - sr * 0.35, sr * 1.8)
            gc = QColor(self.colors[0]); gc.setAlpha(70)
            gz = QColor(self.colors[0]); gz.setAlpha(0)
            glow.setColorAt(0.5, gc); glow.setColorAt(1, gz)
            q.fillRect(QRectF(0, 0, w, hz), glow)
            floor = QLinearGradient(0, hz, 0, h)
            floor.setColorAt(0, QColor(self.bg).darker(130)); floor.setColorAt(1, QColor(self.bg))
            q.fillRect(QRectF(0, hz, w, h - hz), floor)
            q.end()
            self._static = st
            s = max(8, int(sr * 2))
            sun = QImage(s, s, _ARGB)
            sun.fill(Qt.GlobalColor.transparent)
            q = QPainter(sun)
            q.setRenderHint(_AA)
            g = QLinearGradient(0, 0, 0, s)
            g.setColorAt(0, self.colors[1]); g.setColorAt(1, self.colors[0])
            q.setPen(Qt.PenStyle.NoPen); q.setBrush(g); q.drawEllipse(QRectF(0, 0, s, s))
            q.end()
            self._sun = sun
        p = QPainter(img)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.drawImage(0, 0, self._static)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        p.setRenderHint(_AA)
        p.setRenderHint(_SMOOTH)
        # солнце: полоски «уезжают» вниз (вырезаются из готового диска)
        base = self._sun.width() / 2
        sr = base * (1 + 0.08 * self.rt.pulse.bass * self.react)
        sun = QImage(self._sun)
        q = QPainter(sun)
        q.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut)
        stripe = (self.t * 6) % 10 * 0.6
        for k in range(7):
            y = base + base * (0.05 + k * 0.15) + stripe
            q.fillRect(QRectF(0, y, base * 2, 1.2 + k * 1.1), QColor(0, 0, 0, 255))
        q.end()
        cx, cy = w / 2, hz - base * 0.35
        p.drawImage(QRectF(cx - sr, cy - sr, sr * 2, sr * 2), sun)
        # сетка, бегущая на зрителя
        self._grid_off = (self._grid_off + dt * self.speed * 0.6 * boost) % 1.0
        gcol = QColor(self.colors[2]); gcol.setAlpha(200)
        p.setPen(QPen(gcol, 1.1))
        lines = []
        for k in range(14):
            y = hz + (h - hz) * (((k + 1 - self._grid_off) / 14.0) ** 2.2)
            lines.append(QLineF(0, y, w, y))
        vx = w / 2
        for k in range(-12, 13):
            lines.append(QLineF(vx + k * w * 0.012, hz, vx + k * w * 0.16, h))
        p.drawLines(lines)
        hl = QColor(self.colors[2]); hl.setAlpha(255)
        p.setPen(QPen(hl, 1.6))
        p.drawLine(QPointF(0, hz), QPointF(w, hz))
        p.end()

    # ── плазма ── #
    def _plasma(self, dt, boost):
        w, h = self.size.width(), self.size.height()
        if self._lut is None or self._xx.shape != (h, w):
            cols = self.colors[:4] + [self.colors[0]]
            lut = np.zeros((256, 3), np.float32)
            seg = 256 / (len(cols) - 1)
            for i in range(256):
                k = min(len(cols) - 2, int(i / seg))
                f = (i - k * seg) / seg
                a, b = cols[k], cols[k + 1]
                lut[i] = [a.red() + (b.red() - a.red()) * f, a.green() + (b.green() - a.green()) * f,
                          a.blue() + (b.blue() - a.blue()) * f]
            self._lut = lut.astype(np.uint8)
            yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
            self._xx = xx / max(1, w) * 6.0
            self._yy = yy / max(1, h) * 6.0 * h / max(1, w)
        t = self.t
        if _NATIVE_FX:
            # C++: та же формула в несколько потоков, прямо в память кадра
            lut32 = getattr(self, "_lut32", None)
            if lut32 is None or getattr(self, "_lut32_src", None) is not self._lut:
                c = self._lut.astype(np.uint32)
                lut32 = self._lut32 = np.ascontiguousarray(
                    np.uint32(0xFF000000) | (c[:, 0] << 16) | (c[:, 1] << 8) | c[:, 2], dtype=np.uint32)
                self._lut32_src = self._lut
            img = QImage(w, h, _ARGB)
            _NF.lw_plasma(img, t, boost, lut32)
            self.img = img
            return
        x, y = self._xx, self._yy
        v = (np.sin(x + t) + np.sin(y * 1.3 + t * 1.1) + np.sin((x + y) * 0.7 + t * 0.7)
             + np.sin(np.sqrt((x - 3 + np.sin(t * 0.3)) ** 2 + (y - 2) ** 2) * 1.6 - t * 1.4))
        v = (v * (0.25 + 0.05 * boost) + 0.5 + t * 0.03) % 1.0
        rgb = self._lut[(v * 255).astype(np.uint8)]
        out = np.empty((h, w, 4), np.uint8)
        out[..., 0] = rgb[..., 2]
        out[..., 1] = rgb[..., 1]
        out[..., 2] = rgb[..., 0]
        out[..., 3] = 255
        self._buf = out
        self.img = QImage(out.data, w, h, w * 4, _ARGB)

    # ── звёзды ── #
    def _starfield(self, dt, boost):
        img, (w, h) = self.img, (self.size.width(), self.size.height())
        n = 360
        if self._stars is None:
            r = np.random.default_rng(3)
            self._stars = np.column_stack([r.uniform(-1, 1, n), r.uniform(-1, 1, n), r.uniform(0.05, 1, n)])
            self._ci = r.integers(0, 3, n)
            self._rng = r
        st = self._stars
        v = dt * self.speed * 0.25 * (1.0 + 1.6 * (boost - 1))
        z_old = st[:, 2].copy()
        st[:, 2] -= v
        dead = st[:, 2] <= 0.02
        if dead.any():
            k = int(dead.sum())
            st[dead, 0] = self._rng.uniform(-1, 1, k)
            st[dead, 1] = self._rng.uniform(-1, 1, k)
            st[dead, 2] = 1.0
            z_old[dead] = 1.0
        fade = QColor(self.bg)
        fade.setAlpha(150)
        p = QPainter(img)
        p.fillRect(QRectF(0, 0, w, h), fade)                 # следы-«варп»
        p.setRenderHint(_AA)
        cx, cy, f = w / 2, h / 2, min(w, h) * 0.5
        x1 = cx + st[:, 0] / st[:, 2] * f
        y1 = cy + st[:, 1] / st[:, 2] * f
        x0 = cx + st[:, 0] / z_old * f
        y0 = cy + st[:, 1] / z_old * f
        bright = np.clip(1.15 - st[:, 2], 0.0, 1.0)
        bucket = np.minimum(3, (bright * 4).astype(int))
        for ci in range(3):
            for b in range(4):
                idx = np.nonzero((self._ci == ci) & (bucket == b))[0]
                if not len(idx):
                    continue
                a = (b + 0.5) / 4.0
                c = QColor(self.colors[ci])
                c.setAlphaF(a)
                p.setPen(QPen(c, 0.6 + 1.6 * a))
                p.drawLines([QLineF(x0[i], y0[i], x1[i], y1[i]) for i in idx])
        p.end()

    # ── цифровой дождь ── #
    def _matrix(self, dt, boost):
        img, (w, h) = self.img, (self.size.width(), self.size.height())
        cell = 11
        cols = max(4, w // cell)
        if self._drops is None or len(self._drops) != cols:
            self._drops = [self.rnd.uniform(-h * 0.3, h) for _ in range(cols)]
            self._speeds = [self.rnd.uniform(0.6, 1.4) for _ in range(cols)]
            self._glyphs = "アイウエオカキクケコサシスセソタチツテトナニヌネノ0123456789ﾊﾋﾌﾍﾎ$#@*+=<>"
            self._font = QFont("Consolas")
            self._font.setStyleHint(QFont.StyleHint.Monospace)
            self._font.setPixelSize(cell)
        fade = QColor(self.bg)
        fade.setAlpha(int(min(255, 40 + 30 * self.speed)))
        p = QPainter(img)
        p.fillRect(QRectF(0, 0, w, h), fade)
        p.setFont(self._font)
        head = QColor(255, 255, 255, 235)
        body = QColor(self.colors[0])
        g = self._glyphs
        rnd = self.rnd
        step = cell * 0.6 * self.speed * (0.7 + 0.6 * boost) * (dt * 30)
        for i in range(cols):
            y = self._drops[i]
            if -cell < y < h + cell * 2:
                p.setPen(body)
                p.drawText(QPointF(i * cell, y - cell), g[rnd.randrange(len(g))])
                p.setPen(head)
                p.drawText(QPointF(i * cell, y), g[rnd.randrange(len(g))])
            self._drops[i] = y + step * self._speeds[i]
            if self._drops[i] > h + cell * 2 and rnd.random() < 0.06:
                self._drops[i] = rnd.uniform(-h * 0.3, 0)
        p.end()


# ------------------------------------------------------------------ #
#  Нижний слой: фон, стекло, тени, слои позади                        #
# ------------------------------------------------------------------ #

class _ScreenFx:
    """Частицы и эффекты экрана (сканлайны, зерно, виньетка). Рисуются либо поверх интерфейса
    (Overlay), либо за ним, на фоне (Backdrop) — как выбрано в теме (effects.behind)."""

    def __init__(self, runtime, behind: bool):
        self.rt = runtime
        self.behind = behind                 # этот экземпляр рисует эффекты «за интерфейсом»
        self.on = False
        self.t = None
        self.particles = None
        self._fx_key = None
        self._scan = self._grain = self._vig = None

    def set_theme(self, t):
        self.t = t
        e = t["effects"]
        self.on = bool(e.get("behind")) == self.behind
        pc = e["particles"]
        if self.on and pc["kind"] != "none" and pc["count"] > 0:
            if self.particles is None:
                self.particles = Particles(pc, t["palette"], self.rt)
            else:
                self.particles.configure(pc, t["palette"])
        else:
            self.particles = None
        self._fx_key = None

    def needed(self) -> bool:
        if not self.on or self.t is None:
            return False
        e = self.t["effects"]
        return bool(self.particles is not None or e["scanlines"] > 0.003 or e["grain"] > 0.003
                    or e["vignette"] > 0.003)

    def resized(self):
        self._fx_key = None

    def step(self, dt, W, H) -> QRegion:
        if self.particles is None:
            return QRegion()
        return self.particles.step(dt, W, H)

    def _cache(self, W, H):
        e = self.t["effects"]
        key = (W, H, self.rt.dpr, round(e["scanlines"], 2), round(e["grain"], 2), round(e["vignette"], 2))
        if key == self._fx_key:
            return
        self._fx_key = key
        self._scan = self._grain = self._vig = None
        if e["scanlines"] > 0.003:
            tile = QPixmap(4, 3)
            tile.fill(Qt.GlobalColor.transparent)
            q = QPainter(tile)
            q.fillRect(0, 2, 4, 1, QColor(0, 0, 0, int(200 * e["scanlines"])))
            q.end()
            self._scan = QBrush(tile)
        if e["grain"] > 0.003:
            rnd = np.random.default_rng(5)
            n = 160
            v = rnd.integers(0, 2, (n, n), dtype=np.uint16) * 255
            alpha = (rnd.random((n, n)) * 70 * e["grain"]).astype(np.uint16)
            arr = np.empty((n, n, 4), np.uint8)
            pv = (v * alpha // 255).astype(np.uint8)              # premultiplied: rgb ≤ alpha
            arr[..., 0] = arr[..., 1] = arr[..., 2] = pv
            arr[..., 3] = alpha.astype(np.uint8)
            img = QImage(arr.data, n, n, n * 4, _ARGB).copy()
            self._grain = QBrush(QPixmap.fromImage(img))
        if e["vignette"] > 0.003:
            dpr = self.rt.dpr
            pm = QPixmap(max(1, int(W * dpr)), max(1, int(H * dpr)))
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.GlobalColor.transparent)
            q = QPainter(pm)
            g = QRadialGradient(W / 2, H / 2, math.hypot(W, H) * 0.6)
            g.setColorAt(0.5, QColor(0, 0, 0, 0))
            g.setColorAt(1.0, QColor(0, 0, 0, int(230 * e["vignette"])))
            q.fillRect(QRectF(0, 0, W, H), g)
            q.end()
            self._vig = pm

    def paint(self, p: QPainter, W, H, reg: QRegion):
        if not self.on or self.t is None:
            return
        if self.particles is not None:
            self.particles.paint(p, W, H, reg)
        self._cache(W, H)
        full = QRectF(0, 0, W, H)
        if self._scan is not None:
            p.fillRect(full, self._scan)
        if self._grain is not None:
            p.fillRect(full, self._grain)
        if self._vig is not None:
            p.drawPixmap(0, 0, self._vig)


class Backdrop(QWidget):
    """Нижний слой. Кадр фона собирается целиком (фон + затемнение/виньетка + тени) один раз:
    для статичного фона — один раз вообще, для живого — один раз на кадр анимации (≤30/с).
    paintEvent только копирует нужный кусок готового кадра и снимки фона под панелями."""

    def __init__(self, runtime, parent):
        super().__init__(parent)
        self.rt = runtime
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.t = None
        self.live = None
        self.cover_path = ""
        self.hidden_layers = set()
        self._layers = _LayerTracker()
        self.fx = _ScreenFx(runtime, behind=True)     # частицы/эффекты экрана «за интерфейсом»
        self._reset()

    def _reset(self):
        self._src_img = None             # живой фон: картинка текущего кадра (маленькая, растягивается при рисовании)
        self._src_rect = QRectF()        # куда её рисовать в окне
        self._src_fill = True            # подложка под картинкой (если она не закрывает окно)
        self._src_veiled = False         # затемнение уже наложено на саму картинку
        self._src_tile = False
        self._src_dirty = True
        self._src_gen = 0                # номер кадра живого фона
        self._cache = None               # кэш растянутого кадра: заполняется лениво, только где рисовали
        self._cache_gen = -1
        self._cache_valid = QRegion()
        self._static = None              # то же для статичного фона, уже со стеклом (QPixmap)
        self._geo_key = None
        self._rects = []
        self._masks = []
        self._shadows = []
        self._glass = []
        self._due = []
        self._open = QRegion()
        self._veil = None
        self._veil_color = None
        self._veil_key = None
        self._frame = None               # маленький кадр живых обоев / градиента
        self._jobs = []                  # конвейер живого фона: "show", ("glass", i)
        self._pending = False
        self._acc = 0.0
        self._since = 1.0
        self._par = QPointF()
        self._flash = 0.0
        self._flash_on = False
        self._flash_acc = 0.0
        self._flick = 0.0
        self._fx_acc = 0.0
        self._img = self._img_key = None
        self._cov = self._cov_key = None
        self._tile = self._tile_key = None
        self._gif = self._gif_key = None
        self._layers.reset()

    # ── настройка ── #

    def set_theme(self, t):
        self.t = t
        self.fx.set_theme(t)
        b = t["background"]
        keep_live = self.live is not None and self.live.kind == b["type"]
        self._reset()
        if b["type"] in tk.LIVE_BACKGROUNDS:
            if keep_live:
                self.live.configure(t["palette"]["bg"], b.get("colors"), b.get("speed", 1.0), b.get("react", 0.6))
                self.live.resize(max(2, self.width()), max(2, self.height()))
                self.live.step(0.0)
            else:
                self.live = LiveWallpaper(b["type"], t["palette"]["bg"], b.get("colors"), b.get("speed", 1.0),
                                          b.get("react", 0.6), self.rt)
                self.live.resize(max(2, self.width()), max(2, self.height()))
                self.live.prewarm()
                self.live.step(0.016)
            self._frame = self.live.img
        else:
            self.live = None
            if self._spin():
                self._frame = self._gradient_small(max(2, self.width()), max(2, self.height()))
        self.update()

    def set_cover(self, path):
        path = path or ""
        if path == self.cover_path:
            return
        self.cover_path = path
        if self.t is not None and self.t["background"]["type"] == "cover":
            self._static = None
            self._src_dirty = True
            self._glass = [None] * len(self._rects)
            self.update()

    def invalidate(self):
        self._static = None
        self._src_dirty = True
        self._geo_key = None
        self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.fx.resized()
        if self.t is None:
            return
        if self.live is not None:
            self.live.resize(max(2, self.width()), max(2, self.height()))
            self.live.step(0.0)
            self._frame = self.live.img
        elif self._spin():
            self._frame = self._gradient_small(max(2, self.width()), max(2, self.height()))
        self._src_dirty = True

    # ── свойства темы ── #

    def _spin(self) -> bool:
        b = self.t["background"]
        return b["type"] == "gradient" and abs(b["gradient"].get("spin", 0.0)) > 0.001

    def _frosted(self) -> bool:
        s = self.t["shape"]
        return bool(s.get("frosted")) and s.get("blur", 0) > 0

    def _see_through(self) -> bool:
        """Фон виден сквозь панели (панели не сплошные)."""
        return self.t["shape"].get("glass", 0.07) < 0.97

    def dynamic(self) -> bool:
        """Фон меняется со временем (иначе — один запечённый кадр)."""
        b = self.t["background"]
        ty = b["type"]
        if ty in tk.LIVE_BACKGROUNDS or ty == "video" or self._spin():
            return True
        if ty == "media":
            src = self.rt.media(b.get("media"))
            if src is not None and src.animated:
                return True
        return ty in ("media", "cover") and b.get("parallax", 0) > 0.001

    # ── геометрия панелей: маски, тени, «открытая» область ── #

    def _geometry(self, W, H):
        rects = [QRectF(r).toAlignedRect() for r in self.rt.panel_rects()]
        rects = [r for r in rects if r.width() > 8 and r.height() > 8]
        s = self.t["shape"]
        key = (W, H, tuple((r.x(), r.y(), r.width(), r.height()) for r in rects), s["radius"],
               round(s["shadow"], 2), self.rt.dpr)
        if key == self._geo_key:
            return
        self._geo_key = key
        self._rects = rects
        dpr = self.rt.dpr
        self._masks = []
        self._shadows = []
        open_reg = QRegion(0, 0, W, H)
        for r in rects:
            rad = min(float(s["radius"]), r.width() / 2.0, r.height() / 2.0)
            m = QImage(max(1, int(r.width() * dpr)), max(1, int(r.height() * dpr)), _ARGB)
            m.setDevicePixelRatio(dpr)
            m.fill(Qt.GlobalColor.transparent)
            q = QPainter(m)
            q.setRenderHint(_AA)
            q.setPen(Qt.PenStyle.NoPen)
            q.setBrush(QColor(255, 255, 255))
            q.drawRoundedRect(QRectF(0, 0, r.width(), r.height()), rad, rad)
            q.end()
            self._masks.append(m)
            # открытая область = окно без панелей; квадратики в углах панелей — «открытые»
            # (там скругление, сквозь него виден живой фон). Только прямоугольники: сложная
            # область с сотнями полосок сильно замедляет каждую отрисовку.
            c = int(math.ceil(rad * 0.72))
            open_reg = open_reg.subtracted(QRegion(r.adjusted(0, c, 0, -c)))
            open_reg = open_reg.subtracted(QRegion(r.adjusted(c, 0, -c, 0)))
            if s["shadow"] > 0.01:
                self._shadows += self._make_shadow(r, rad, s["shadow"])
        self._open = open_reg
        self._glass = [None] * len(rects)
        now = self.rt.pulse.t
        self._due = [now + 0.06 * i for i in range(len(rects))]
        self._static = None
        self._src_dirty = True

    def _make_shadow(self, r: QRect, rad, strength):
        pad = 28
        dpr = self.rt.dpr
        box = r.adjusted(-pad, -pad, pad, pad + 12)
        pm = QPixmap(int(box.width() * dpr), int(box.height() * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        q = QPainter(pm)
        q.setRenderHint(_AA)
        q.setPen(Qt.PenStyle.NoPen)
        q.translate(-box.x(), -box.y())
        for i in range(10, 0, -1):
            path = QPainterPath()
            rr = QRectF(r).adjusted(-i * 1.6, -i * 1.2 + 6, i * 1.6, i * 2.2 + 6)
            path.addRoundedRect(rr, rad + i * 1.6, rad + i * 1.6)
            q.setBrush(QColor(0, 0, 0, int(14 * strength)))
            q.drawPath(path)
        q.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        hole = QPainterPath()
        hole.addRoundedRect(QRectF(r), rad, rad)
        q.fillPath(hole, Qt.GlobalColor.transparent)
        q.end()
        # режем на 4 полосы вокруг панели: середина пустая, её незачем смешивать каждый кадр
        c = int(math.ceil(rad)) + 2
        parts = []
        top = QRect(box.x(), box.y(), box.width(), r.y() - box.y() + c)
        bot = QRect(box.x(), r.bottom() - c, box.width(), box.bottom() - r.bottom() + c + 1)
        mid_y, mid_h = top.bottom() + 1, max(0, bot.y() - top.bottom() - 1)
        left = QRect(box.x(), mid_y, r.x() - box.x() + 2, mid_h)
        right = QRect(r.right() - 1, mid_y, box.right() - r.right() + 2, mid_h)
        for part in (top, bot, left, right):
            if part.width() > 0 and part.height() > 0:
                src = QRect(int((part.x() - box.x()) * dpr), int((part.y() - box.y()) * dpr),
                            int(math.ceil(part.width() * dpr)), int(math.ceil(part.height() * dpr)))
                piece = pm.copy(src)
                piece.setDevicePixelRatio(dpr)
                parts.append((part.topLeft(), piece))
        return parts

    # ── обновление по кадрам ── #

    def _kind(self) -> str:
        b = self.t["background"]
        return "gradient" if self._spin() else b["type"]

    def advance(self, dt):
        """Кадр часов. Живой фон идёт конвейером, по одному тяжёлому шагу за кадр:
        собрать кадр фона → показать открытую область → снимки под панелями (по одной).
        Так кадр интерфейса никогда не тянет сразу и сборку, и перерисовку всего."""
        if self.t is None:
            return
        W, H = self.width(), self.height()
        if W < 2 or H < 2:
            return
        self._geometry(W, H)
        b = self.t["background"]
        ty = b["type"]
        reg = QRegion()
        self._since += dt
        self._acc += dt
        moved = self._parallax(dt, W, H, b, ty)
        fx = self._effects(dt)
        if self.dynamic() and ty == "video":
            # Видео — каждый новый кадр ролика сразу и целиком (кадр уже нужного размера, рисуется
            # копированием без масштаба); снимки под панелями — по очереди, не чаще их интервала.
            if self._pending or moved:
                self._pending = False
                self._prepare_src(W, H)
                self._src_dirty = False
                reg = reg.united(self._open)
                if self._see_through() and self._rects:
                    if not self._frosted():
                        # прозрачные панели: под ними тот же кадр, что и вокруг, — каждый кадр ролика
                        # (раньше там висел снимок раз в 0.1–0.25 с: видео под интерфейсом «тормозило»
                        # и было видно прямоугольники панелей)
                        for r in self._rects:
                            reg = reg.united(QRegion(r))
                    else:
                        now = self.rt.pulse.t
                        iv = self.rt.panel_interval(True)
                        for i in range(len(self._rects)):
                            if now >= self._due[i]:
                                self._due[i] = now + iv
                                self._glass[i] = self._make_glass(i)
                                reg = reg.united(QRegion(self._rects[i]))
        elif self.dynamic():
            if self._jobs:
                kind, arg = self._jobs.pop(0)
                if kind == "show":                             # полоса открытой области
                    reg = reg.united(arg)
                elif kind == "glass":                          # новый снимок под матовой панелью
                    if arg < len(self._rects) and self._src_img is not None:
                        self._glass[arg] = self._make_glass(arg)
                        reg = reg.united(QRegion(self._rects[arg]))
                else:                                          # "area"
                    reg = reg.united(arg)
            # новый кадр фона — независимо от очереди снимков (раньше снимки панелей задерживали кадры фона)
            period = 1.0 / self.rt.bg_fps(self._kind()) - 0.002
            live = ty in tk.LIVE_BACKGROUNDS or self._spin()
            due = self._acc >= period if live else ((self._pending or moved) and self._since >= period)
            if due:
                if self.live is not None:
                    self.live.step(min(self._acc, 0.12) * self.rt.bg_speed())   # скрипт фона: скорость обоев
                    self._frame = self.live.img
                elif self._spin():
                    self._frame = self._gradient_small(W, H)
                self._acc = self._since = 0.0
                self._pending = False
                self._prepare_src(W, H)
                self._src_dirty = False
                # новый кадр показываем СРАЗУ и целиком: раньше он шёл двумя полосами за два кадра,
                # а анимированные слои успевали перерисоваться уже с новым кадром — вокруг каждого
                # виджета/эффекта мелькал прямоугольник «другого» кадра. Под прозрачными (не матовыми)
                # панелями — тот же кадр и с той же частотой.
                clear = self._see_through() and not self._frosted()
                reg = reg.united(QRegion(0, 0, W, H) if clear else self._open)
                if self._see_through() and self._frosted():
                    now = self.rt.pulse.t
                    iv = self.rt.panel_interval(True)
                    for i in range(len(self._rects)):
                        if now >= self._due[i]:
                            self._due[i] = now + iv
                            self._jobs.append(("glass", i))
            # вспышки/мерцание на живом фоне видны со следующим показом кадра — отдельно не перерисовываем
        elif fx:
            reg = reg.united(self._fx_region(W, H))
        reg = reg.united(self._layers.region(self.rt, self.t["layers"], "back", W, H, self.hidden_layers))
        if self.fx.on:
            reg = reg.united(self.fx.step(dt, W, H))
        if not reg.isEmpty():
            self.update(reg)

    def _fx_region(self, W, H) -> QRegion:
        """Где видны вспышки/мерцание фона: сквозь прозрачные панели — везде (иначе панели
        выделялись прямоугольниками без вспышки)."""
        if self._see_through() and not self._frosted():
            return QRegion(0, 0, W, H)
        return self._open

    def _parallax(self, dt, W, H, b, ty) -> bool:
        if b.get("parallax", 0) <= 0.001 or not (ty in tk.LIVE_BACKGROUNDS or ty in ("media", "video", "cover")):
            return False
        try:
            pos = self.mapFromGlobal(QCursor.pos())
        except RuntimeError:
            return False
        nx = max(-1.0, min(1.0, (pos.x() / max(1, W) - 0.5) * 2))
        ny = max(-1.0, min(1.0, (pos.y() / max(1, H) - 0.5) * 2))
        tgt = QPointF(-nx * b["parallax"] * 28, -ny * b["parallax"] * 20)
        np_ = self._par + (tgt - self._par) * min(1.0, dt * 6)
        if abs(np_.x() - self._par.x()) > 0.35 or abs(np_.y() - self._par.y()) > 0.35:
            self._par = np_
            return True
        return False

    def _effects(self, dt) -> bool:
        """Вспышка на бит и мерцание. True — для статичного фона пора перерисовать открытую область."""
        e = self.t["effects"]
        out = False
        if e["flash"] > 0.003:
            target = self.rt.pulse.beat * e["flash"]
            if target > self._flash + 0.05:
                self._flash = target
            else:
                self._flash *= math.exp(-dt * 9)
            self._flash_acc += dt
            on = self._flash > 0.006
            if (on or self._flash_on) and self._flash_acc >= 1 / 20.0:
                self._flash_acc = 0.0
                self._flash_on = on
                out = True
        if e["flicker"] > 0.003:
            self._fx_acc += dt
            if self._fx_acc > 1 / 15.0:
                self._fx_acc = 0.0
                self._flick = random.random() * e["flicker"]
                out = True
        return out

    def media_changed(self):
        """Новый кадр GIF/видео фона — соберём на ближайшем кадре часов."""
        self._pending = True

    # ── скрипт фона: зум, сдвиг, затемнение ── #

    def bg_script_changed(self):
        self._static = None
        self._src_dirty = True
        self._pending = True
        self._veil_key = None
        self.update()

    def _bg_xform(self, r: QRectF) -> QRectF:
        """Прямоугольник картинки фона с зумом и сдвигом из скрипта фона."""
        m = self.rt.bg_motion()
        if m is None:
            return r
        z = m["zoom"]
        if abs(z - 1.0) > 1e-4:
            c = r.center()
            r = QRectF(c.x() - r.width() * z / 2, c.y() - r.height() * z / 2, r.width() * z, r.height() * z)
        return r.translated(m["dx"], m["dy"])

    def _dim(self) -> float:
        m = self.rt.bg_motion()
        if m is not None and m["dim"] >= 0:
            return m["dim"]
        return self.t["background"].get("dim", 0)

    # ── содержимое фона ── #

    def _gradient_small(self, W, H) -> QImage:
        w, h = max(8, W // 4), max(8, H // 4)
        img = QImage(w, h, QImage.Format.Format_RGB32)
        q = QPainter(img)
        q.scale(w / max(1, W), h / max(1, H))
        self._paint_gradient(q, W, H)
        q.end()
        return img

    def _paint_gradient(self, p, W, H):
        gd = self.t["background"]["gradient"]
        ang = gd.get("angle", 135) + self.rt.pulse.t * gd.get("spin", 0) * 40
        kind = gd.get("kind", "linear")
        cx, cy = W / 2, H / 2
        if kind == "radial":
            g = QRadialGradient(cx + math.cos(math.radians(ang)) * W * 0.15,
                                cy + math.sin(math.radians(ang)) * H * 0.15, math.hypot(W, H) * 0.6)
        elif kind == "conical":
            g = QConicalGradient(cx, cy, -ang)
        else:
            a = math.radians(ang)
            d = (abs(W * math.cos(a)) + abs(H * math.sin(a))) / 2
            g = QLinearGradient(cx - math.cos(a) * d, cy - math.sin(a) * d, cx + math.cos(a) * d, cy + math.sin(a) * d)
        stops = gd["stops"]
        for pos, col in stops:
            g.setColorAt(pos, _qc(col))
        if kind == "conical" and stops:
            g.setColorAt(1.0, _qc(stops[0][1]))
        p.fillRect(QRectF(0, 0, W, H), g)

    def _source(self):
        """Картинка фона (QImage/QPixmap) для типов media/cover/video или None."""
        b = self.t["background"]
        ty = b["type"]
        if ty == "video":
            v = self.rt.video
            return v.img if v is not None and not v.img.isNull() else None
        if ty == "cover":
            if not self.cover_path:
                return None
            key = (self.cover_path, b.get("blur"), b.get("saturation"))
            if self._cov_key != key:
                img = read_image(self.cover_path, 1024)
                if not img.isNull():
                    img = img.scaled(512, 512, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
                    img = soft_blur(img, max(10.0, float(b.get("blur", 0)) * 0.5 + 10))
                    img = saturate(img, b.get("saturation", 1.0))
                self._cov, self._cov_key = img, key
            return self._cov if self._cov is not None and not self._cov.isNull() else None
        src = self.rt.media(b.get("media"))
        if src is None or not src.valid():
            return None
        if src.animated:
            if b.get("blur", 0) < 1 and abs(b.get("saturation", 1.0) - 1) < 0.02:
                return src.pix
            key = (src.frame_no, b.get("blur"), b.get("saturation"))
            if self._gif is None or self._gif_key != key:
                img = src.pix.toImage()
                if b.get("blur", 0) >= 1:                  # размытая гифка: хватит маленькой копии
                    k = max(1.0, float(b["blur"]) / 4.0)
                    img = img.scaled(max(2, int(img.width() / k)), max(2, int(img.height() / k)),
                                     Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
                    img = box_blur(img, 2, 2)
                self._gif, self._gif_key = saturate(img, b.get("saturation", 1.0)), key
            return self._gif
        key = (src.path, b.get("blur"), b.get("saturation"))
        if self._img_key != key:
            img = src.pix.toImage()
            if max(img.width(), img.height()) > 2560:
                img = img.scaled(2560, 2560, Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
            img = saturate(img, b.get("saturation", 1.0))
            if b.get("blur", 0) >= 1:
                img = soft_blur(img, float(b["blur"]))
            self._img, self._img_key = img, key
        return self._img

    def _blit_scaled(self, p: QPainter, img, r: QRectF, W, H):
        """Нарисовать картинку в прямоугольник r: масштабируется только видимая часть и один раз
        быстрым scaled() — сам рисунок потом простой блит (плавный масштаб в QPainter в разы дороже)."""
        vis = r.intersected(QRectF(0, 0, W, H))
        if vis.isEmpty() or img.width() < 1 or img.height() < 1:
            return
        dpr = self.rt.dpr
        kx, ky = img.width() / r.width(), img.height() / r.height()
        sr = QRectF((vis.x() - r.x()) * kx, (vis.y() - r.y()) * ky, vis.width() * kx, vis.height() * ky)
        cr = sr.toAlignedRect().intersected(img.rect())
        if cr.isEmpty():
            return
        crop = img if cr == img.rect() else img.copy(cr)
        tx, ty = r.x() + cr.x() / kx, r.y() + cr.y() / ky
        tw, th = cr.width() / kx, cr.height() / ky
        pw, ph = max(1, int(round(tw * dpr))), max(1, int(round(th * dpr)))
        if (crop.width(), crop.height()) != (pw, ph):
            crop = crop.scaled(pw, ph, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        if crop.devicePixelRatio() != dpr:
            crop = QPixmap(crop) if isinstance(crop, QPixmap) else QImage(crop)
            crop.setDevicePixelRatio(dpr)
        if isinstance(crop, QPixmap):
            p.drawPixmap(QPointF(tx, ty), crop)
        else:
            p.drawImage(QPointF(tx, ty), crop)

    def _draw_bg(self, p: QPainter, W, H):
        """Сам фон (без затемнения и стекла) на весь размер окна."""
        t = self.t
        b, pal = t["background"], t["palette"]
        ty = b["type"]
        full = QRectF(0, 0, W, H)
        if ty == "color":
            p.fillRect(full, _qc(b.get("color") or pal["bg"]))
            return
        if ty == "glow":
            glow = _qc(pal.get("glow") or pal["accent2"])
            g = QRadialGradient(W * 0.15, 0, 0.75 * math.hypot(W, H))
            c0 = QColor(glow); c0.setAlpha(230)
            c1 = QColor(glow); c1.setAlpha(112)
            g.setColorAt(0, c0); g.setColorAt(0.28, c1); g.setColorAt(0.6, QColor(0, 0, 0, 204))
            g.setColorAt(1, _qc(pal["bg"]))
            p.fillRect(full, _qc(pal["bg"]))
            p.fillRect(full, g)
            return
        if ty == "gradient":
            if self._spin() and self._frame is not None:
                self._blit_scaled(p, self._frame, full, W, H)
            else:
                self._paint_gradient(p, W, H)
            return
        par = b.get("parallax", 0) > 0.001
        if ty in tk.LIVE_BACKGROUNDS:
            img = self._frame
            if img is not None and not img.isNull():
                if par:
                    p.fillRect(full, _qc(pal["bg"]))
                self._blit_scaled(p, img, QRectF(self._par.x() - 14, self._par.y() - 10, W + 28, H + 20)
                                  if par else full, W, H)
            else:
                p.fillRect(full, _qc(pal["bg"]))
            return
        p.fillRect(full, _qc(pal["bg"]))
        img = self._source()
        if img is None:
            return
        fit = b.get("fit", "cover") if ty in ("media", "video") else "cover"
        if fit == "tile":
            pm = img if isinstance(img, QPixmap) else None
            if pm is None:
                key = (self._img_key, self._gif_key, ty, id(img))
                if self._tile_key != key:
                    self._tile, self._tile_key = QPixmap.fromImage(img), key
                pm = self._tile
            p.save()
            p.translate(self._par)
            p.fillRect(full.translated(-self._par), QBrush(pm))
            p.restore()
            return
        zoom = b.get("zoom", 1.0) * (1 + (b.get("parallax", 0) * 0.06 if par else 0))
        r = fit_rect(img.size(), W, H, fit, zoom, b.get("align_x", 0.5), b.get("align_y", 0.5))
        r.translate(self._par)
        self._blit_scaled(p, img, self._bg_xform(r), W, H)

    def _paint_veil(self, p: QPainter, W, H, cache=True):
        """Оттенок + затемнение + виньетка — одним слоем (cache=False — рисовать напрямую, для малых кадров)."""
        b = self.t["background"]
        ty = b["type"]
        ta = b.get("tint_amount", 0) * 0.85
        da = self._dim() * 0.9 if (ty not in ("color", "glow", "gradient") or self.rt.bg_motion()) else 0.0
        vg = b.get("vignette", 0)
        if not cache:
            if ta > 0.003 or da > 0.003:
                A = 1 - (1 - ta) * (1 - da)
                tc = QColor(b.get("tint") or "#000000")
                k = ta * (1 - da) / A
                p.fillRect(QRectF(0, 0, W, H), QColor.fromRgbF(tc.redF() * k, tc.greenF() * k, tc.blueF() * k, A))
            if vg > 0.003:
                g = QRadialGradient(W / 2, H / 2, math.hypot(W, H) * 0.62)
                g.setColorAt(0.45, QColor(0, 0, 0, 0))
                g.setColorAt(1.0, QColor(0, 0, 0, int(255 * min(1.0, vg))))
                p.fillRect(QRectF(0, 0, W, H), g)
            return
        key = (W, H, self.rt.dpr, b.get("tint"), round(ta, 3), round(da, 3), round(vg, 3))
        if key != self._veil_key:
            self._veil_key = key
            self._veil = self._veil_color = None
            if ta > 0.003 or da > 0.003:
                A = 1 - (1 - ta) * (1 - da)
                tc = QColor(b.get("tint") or "#000000")
                k = ta * (1 - da) / A
                self._veil_color = QColor.fromRgbF(tc.redF() * k, tc.greenF() * k, tc.blueF() * k, A)
            if vg > 0.003:
                dpr = self.rt.dpr
                pm = QPixmap(int(W * dpr), int(H * dpr))
                pm.setDevicePixelRatio(dpr)
                pm.fill(Qt.GlobalColor.transparent)
                q = QPainter(pm)
                if self._veil_color is not None:
                    q.fillRect(QRectF(0, 0, W, H), self._veil_color)
                g = QRadialGradient(W / 2, H / 2, math.hypot(W, H) * 0.62)
                g.setColorAt(0.45, QColor(0, 0, 0, 0))
                g.setColorAt(1.0, QColor(0, 0, 0, int(255 * min(1.0, vg))))
                q.fillRect(QRectF(0, 0, W, H), g)
                q.end()
                self._veil = pm
        if self._veil is not None:
            p.drawPixmap(0, 0, self._veil)
        elif self._veil_color is not None:
            p.fillRect(QRectF(0, 0, W, H), self._veil_color)

    def _compose(self, W, H) -> QImage:
        """Статичный фон целиком: фон + затемнение/виньетка + тени панелей — в пикселях устройства."""
        dpr = self.rt.dpr
        img = QImage(max(1, int(round(W * dpr))), max(1, int(round(H * dpr))), _ARGB)
        img.setDevicePixelRatio(dpr)
        q = QPainter(img)
        q.setRenderHint(_SMOOTH)
        self._draw_bg(q, W, H)
        self._paint_veil(q, W, H)
        for org, pm in self._shadows:
            q.drawPixmap(org, pm)
        q.end()
        return img

    def _prepare_src(self, W, H):
        """Живой фон: картинка текущего кадра и куда её рисовать. Масштаб — при рисовании и только в
        перерисовываемой области: растягивать каждый кадр на всё окно заранее в разы дороже."""
        b = self.t["background"]
        ty = b["type"]
        full = QRectF(0, 0, W, H)
        par = b.get("parallax", 0) > 0.001
        self._src_img = None
        self._src_fill = True
        self._src_veiled = False
        self._src_tile = False
        self._src_gen += 1
        if ty in tk.LIVE_BACKGROUNDS or self._spin():
            small = self._frame
            if small is None or small.isNull():
                return
            src = small.convertToFormat(_ARGB) if small.format() != _ARGB else small.copy()
            q = QPainter(src)                              # затемнение — сразу на маленький кадр
            q.scale(src.width() / max(1, W), src.height() / max(1, H))
            self._paint_veil(q, W, H, cache=False)
            q.end()
            self._src_img = src
            self._src_veiled = True
            self._src_rect = self._bg_xform(QRectF(self._par.x() - 14, self._par.y() - 10, W + 28, H + 20)
                                            if par else full)
            self._src_fill = not self._src_rect.contains(full)
            return
        img = self._source()
        if img is None:
            return
        fit = b.get("fit", "cover") if ty in ("media", "video") else "cover"
        self._src_img = img
        if fit == "tile":
            self._src_tile = True
            self._src_rect = full
            return
        zoom = b.get("zoom", 1.0) * (1 + (b.get("parallax", 0) * 0.06 if par else 0))
        size = img.size()
        if ty == "video" and self.rt.video is not None and self.rt.video.source_size().width():
            size = self.rt.video.source_size()             # пропорции ролика (кадр мог прийти другого размера)
        r = fit_rect(size, W, H, fit, zoom, b.get("align_x", 0.5), b.get("align_y", 0.5))
        r.translate(self._par)
        if ty == "video" and fit != "center":              # размер кадра — без зума скрипта (он меняется на бит)
            dpr = self.rt.dpr
            self.rt.video_target(self.rt.video, r.width() * dpr, r.height() * dpr)
        r = self._bg_xform(r)
        self._src_rect = r
        self._src_fill = not r.contains(full)

    def _paint_src(self, p: QPainter, W, H):
        if self._src_fill or self._src_img is None:
            p.fillRect(QRectF(0, 0, W, H), _qc(self.t["palette"]["bg"]))
        img = self._src_img
        if img is None:
            return
        if self._src_tile:
            pm = img if isinstance(img, QPixmap) else None
            if pm is None:
                if self._tile_key != id(img):
                    self._tile, self._tile_key = QPixmap.fromImage(img), id(img)
                pm = self._tile
            p.save()
            p.translate(self._par)
            p.fillRect(QRectF(0, 0, W, H).translated(-self._par), QBrush(pm))
            p.restore()
            return
        p.setRenderHint(_SMOOTH)
        r = self._src_rect
        dpr = self.rt.dpr
        if abs(r.width() * dpr - img.width()) < 1.5 and abs(r.height() * dpr - img.height()) < 1.5 \
                and not isinstance(img, QPixmap):
            # кадр уже ровно в пикселях экрана — простое копирование, без сглаживающего масштаба
            if abs(img.devicePixelRatio() - dpr) > 1e-3:
                img.setDevicePixelRatio(dpr)
            p.drawImage(r.topLeft(), img)
        elif isinstance(img, QPixmap):
            p.drawPixmap(r, img, QRectF(img.rect()))
        else:
            p.drawImage(r, img)

    def _make_glass(self, i, frame: QImage | None = None):
        """Снимок фона под панелью i — размытый (матовое стекло) или чёткий, со скруглёнными углами.
        frame — готовый статичный кадр; без него — из текущего кадра живого фона."""
        r = self._rects[i]
        W, H = self.width(), self.height()
        dpr = self.rt.dpr
        pr = QRect(int(round(r.x() * dpr)), int(round(r.y() * dpr)),
                   max(1, int(round(r.width() * dpr))), max(1, int(round(r.height() * dpr))))
        if self._frosted():
            k = 6.0
            sw, sh = max(2, int(r.width() / k)), max(2, int(r.height() / k))
            if frame is not None:
                small = frame.copy(pr).scaled(sw, sh, Qt.AspectRatioMode.IgnoreAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation)
            else:
                small = QImage(sw, sh, _ARGB)
                small.fill(_qc(self.t["palette"]["bg"]))
                q = QPainter(small)
                q.scale(sw / r.width(), sh / r.height())
                q.translate(-r.x(), -r.y())
                self._paint_src(q, W, H)
                if not self._src_veiled:
                    self._paint_veil(q, W, H, cache=False)
                q.end()
            small = box_blur(small, max(1, int(round(self.t["shape"]["blur"] / k * 0.8))), 2)
            crop = small.scaled(pr.width(), pr.height(), Qt.AspectRatioMode.IgnoreAspectRatio,
                                Qt.TransformationMode.SmoothTransformation).convertToFormat(_ARGB)
        elif frame is not None:
            crop = frame.copy(pr).convertToFormat(_ARGB)
        else:
            crop = QImage(pr.size(), _ARGB)
            crop.setDevicePixelRatio(dpr)
            q = QPainter(crop)
            q.translate(-r.x(), -r.y())
            self._paint_src(q, W, H)
            if not self._src_veiled:
                self._paint_veil(q, W, H)
            q.end()
        crop.setDevicePixelRatio(dpr)
        q = QPainter(crop)
        q.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        q.drawImage(QRectF(0, 0, r.width(), r.height()), self._masks[i])
        q.end()
        return QPixmap.fromImage(crop)

    # ── рисование ── #

    def paintEvent(self, ev):
        if self.t is None:
            return
        W, H = self.width(), self.height()
        self._geometry(W, H)
        p = QPainter(self)
        if not self.dynamic():
            if self._static is None:
                img = self._compose(W, H)
                if self._frosted() and self._see_through() and self._rects:
                    glass = [self._make_glass(i, img) for i in range(len(self._rects))]
                    q = QPainter(img)
                    for r, g in zip(self._rects, glass):
                        q.drawPixmap(r.topLeft(), g)
                    q.end()
                self._static = QPixmap.fromImage(img)
            p.drawPixmap(0, 0, self._static)
        else:
            if self._src_img is None or self._src_dirty:
                self._prepare_src(W, H)
                self._src_dirty = False
            # растянутый кадр кэшируется: пластинка и визуализатор перерисовываются 60 раз/с, а фон
            # меняется реже — плавный масштаб делаем один раз на кадр фона, дальше простое копирование
            dpr = self.rt.dpr
            size = QSize(max(1, int(round(W * dpr))), max(1, int(round(H * dpr))))
            if self._cache is None or self._cache.size() != size:
                self._cache = QImage(size, _ARGB)
                self._cache.setDevicePixelRatio(dpr)
                self._cache_gen = -1
            if self._cache_gen != self._src_gen:
                self._cache_gen = self._src_gen
                self._cache_valid = QRegion()
            need = ev.region().subtracted(self._cache_valid)
            if not need.isEmpty():
                q = QPainter(self._cache)
                q.setClipRegion(need)
                self._paint_src(q, W, H)
                if not self._src_veiled:
                    self._paint_veil(q, W, H)
                for org, pm in self._shadows:
                    q.drawPixmap(org, pm)
                q.end()
                self._cache_valid = self._cache_valid.united(need)
            p.drawImage(0, 0, self._cache)
            if self._see_through() and self._frosted():      # прозрачным панелям снимок не нужен — кадр уже в кэше
                for i, r in enumerate(self._rects):
                    if not ev.region().intersects(r):
                        continue
                    if self._glass[i] is None:
                        self._glass[i] = self._make_glass(i)
                    p.drawPixmap(r.topLeft(), self._glass[i])
        if self._flash > 0.006 or self._flick > 0.002:
            p.save()
            p.setClipRegion(self._fx_region(W, H), Qt.ClipOperation.IntersectClip)
            if self._flash > 0.006:
                p.fillRect(QRectF(0, 0, W, H), _qc(self.t["palette"]["accent"], min(0.35, self._flash * 0.4)))
            if self._flick > 0.002:
                p.fillRect(QRectF(0, 0, W, H), QColor(0, 0, 0, int(255 * self._flick * 0.25)))
            p.restore()
        paint_layers(p, self.rt, self.t["layers"], "back", W, H, ev.region(), self.hidden_layers)
        self.fx.paint(p, W, H, ev.region())
        p.end()


# ------------------------------------------------------------------ #
#  Верхний слой: слои поверх, частицы, эффекты                        #
# ------------------------------------------------------------------ #

class Overlay(QWidget):
    def __init__(self, runtime, parent):
        super().__init__(parent)
        self.rt = runtime
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.t = None
        self.hidden_layers = set()       # редактор прячет слой, пока тащит его «призрак»
        self._layers = _LayerTracker()
        self.fx = _ScreenFx(runtime, behind=False)    # частицы/эффекты экрана «поверх интерфейса»

    @property
    def particles(self):
        return self.fx.particles

    @particles.setter
    def particles(self, v):
        self.fx.particles = v

    def set_theme(self, t):
        self.t = t
        self.fx.set_theme(t)
        self._layers.reset()
        self.setVisible(self.needed())
        self.update()

    def needed(self) -> bool:
        t = self.t
        if t is None:
            return False
        return bool(self.fx.needed() or any(L["z"] == "front" and L.get("visible", True) for L in t["layers"]))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.fx.resized()

    def advance(self, dt):
        if self.t is None or not self.isVisible():
            return
        W, H = self.width(), self.height()
        reg = self._layers.region(self.rt, self.t["layers"], "front", W, H, self.hidden_layers)
        reg = reg.united(self.fx.step(dt, W, H))
        if not reg.isEmpty():
            self.update(reg)

    def paintEvent(self, ev):
        if self.t is None:
            return
        W, H = self.width(), self.height()
        p = QPainter(self)
        reg = ev.region()
        paint_layers(p, self.rt, self.t["layers"], "front", W, H, reg, self.hidden_layers)
        self.fx.paint(p, W, H, reg)
        p.end()


# ------------------------------------------------------------------ #
#  Связка с окном                                                     #
# ------------------------------------------------------------------ #

class ThemeRuntime(QObject):
    """Создаёт/обновляет Backdrop и Overlay для пользовательской темы.

    host — виджет, который они закрывают целиком (главное окно: фон заходит и под заголовок);
    panel_rects_fn() — прямоугольники видимых боковых панелей в координатах host."""

    # кадров/с живого фона при полном качестве (медленное — реже, быстрое движение — чаще)
    BG_BASE = {"aurora": 20, "plasma": 20, "matrix": 20, "gradient": 15, "starfield": 30, "synthwave": 30, "nebula": 30,
               "video": 30, "media": 30, "cover": 24}
    QUALITY = (0.5, 0.75, 1.0)                # экономно / сниженно / полностью
    PANEL_IV = {True: (0.12, 0.07, 0.04),     # снимок фона под матовым стеклом, сек (≈8 / 14 / 25 к/с)
                False: (0.25, 0.15, 0.1)}     # прозрачным панелям снимок больше не нужен (кадр общий)

    def __init__(self, host: QWidget, engine, store: tk.ThemeStore, panel_rects_fn):
        super().__init__(host)
        self.host = host
        self.engine = engine
        self.store = store
        self.panel_rects = panel_rects_fn
        self.pulse = AudioPulse(engine)
        self.painter = LayerPainter(self)
        self.theme = None
        self.backdrop = None
        self.overlay = None
        self.video = None
        self.dpr = 1.0
        self.quality = 2
        self._late = 0.0
        self._good = 0
        self._q_acc = 0.0
        self._paused = False
        self._video_path = None
        self._media = {}
        self._videos = {}                    # видео-слои: путь → VideoSource
        self._vtargets = {}                  # id(VideoSource) → [источник, (w, h), когда попросили]
        self._fonts = {}
        self.scripts = None                  # layer_fx.ScriptHost — виджеты/скрипты/эффекты слоёв
        self.input = None                    # layer_fx.LayerInput — мышь для слоёв
        self._timer = FrameTimer(self)
        self._timer.timeout.connect(self._tick)
        self._last = time.perf_counter()

    def _settings(self) -> dict:
        return getattr(self.host, "settings", None) or {}

    def area(self) -> QRect:
        """Где живёт тема: окно целиком или без встроенной панели конструктора."""
        fn = getattr(self.host, "_custom_area", None)
        try:
            return fn() if fn is not None else self.host.rect()
        except Exception:                                  # noqa: BLE001
            return self.host.rect()

    def size(self):
        """Ширина и высота области темы (слои считаются в её долях)."""
        if self.backdrop is not None and self.backdrop.width() > 2:
            return self.backdrop.width(), self.backdrop.height()
        r = self.area()
        return r.width(), r.height()

    # ── качество ── #

    def bg_fps(self, kind=None) -> float:
        base = self.BG_BASE.get(kind, 24) * self.QUALITY[self.quality]
        if self._settings().get("bg_smooth", False):      # «плавный живой фон»: до 60 к/с при высокой частоте
            base = max(base, min(60.0, frameclock.rate()) * self.QUALITY[self.quality])
        return max(8.0, base)

    # ── видео-слои ── #

    def video_layer(self, rel, speed=1.0):
        if not rel:
            return None
        path = self.asset(rel)
        if not path:
            return None
        src = self._videos.get(path)
        if src is None:
            src = VideoSource(path, self, hw=bool(self._settings().get("video_hw", True)), target=(640, 360))
            if not src.ok:
                src.stop()
                src = None
            elif self._paused:
                src.set_paused(True)
            self._videos[path] = src
        if src is not None and abs(getattr(src, "_rate", 1.0) - speed) > 1e-3:
            src.set_rate(speed)
            src._rate = speed
        return src

    def video_target(self, src, w, h):
        """Попросить кадр размера w×h (пиксели устройства). Применяется, когда размер полсекунды не меняется
        (тянут окно или слой — VLC не перезапускается на каждом пикселе)."""
        if src is None:
            return
        w, h = int(min(w, VideoSource.MAX_W * 2)), int(min(h, VideoSource.MAX_H * 2))
        ent = self._vtargets.get(id(src))
        if ent is not None and ent[1] == (w, h):
            return
        self._vtargets[id(src)] = [src, (w, h), time.perf_counter()]

    def _apply_video_targets(self):
        now = time.perf_counter()
        for k, (src, (w, h), t0) in list(self._vtargets.items()):
            if now - t0 >= 0.45:
                del self._vtargets[k]
                try:
                    src.set_target(w, h)
                except RuntimeError:
                    pass

    def panel_interval(self, frosted: bool) -> float:
        return self.PANEL_IV[bool(frosted)][self.quality]

    # ── ассеты ── #

    def asset(self, rel) -> str:
        if not rel or self.theme is None:
            return ""
        return self.store.asset(self.theme, rel)

    def media(self, rel) -> MediaSource | None:
        if not rel:
            return None
        path = self.asset(rel)
        if not path:
            return None
        src = self._media.get(path)
        if src is None:
            src = MediaSource(path, self)
            src.changed.connect(lambda s=src: self._media_frame(s))
            if self._paused:
                src.set_paused(True)
            self._media[path] = src
        return src

    def _media_frame(self, src):
        t = self.theme
        if t is None or self.backdrop is None:
            return
        b = t["background"]
        if b["type"] == "media" and self.asset(b.get("media")) == src.path:
            self.backdrop.media_changed()
        # кадры GIF-слоёв подхватывает advance() по номеру кадра

    def font_family(self, rel) -> str:
        if not rel:
            return ""
        path = self.asset(rel)
        if not path:
            return ""
        fam = self._fonts.get(path)
        if fam is None:
            fid = QFontDatabase.addApplicationFont(path)
            fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
            fam = fams[0] if fams else ""
            self._fonts[path] = fam
        return fam

    def theme_font(self) -> str:
        if self.theme is None:
            return ""
        f = self.theme["font"]
        return self.font_family(f.get("title_file")) or f.get("title_family") or \
            self.font_family(f.get("file")) or f.get("family") or ""

    # ── жизненный цикл ── #

    def apply(self, theme: dict):
        """Применить тему. Можно вызывать часто (правка в редакторе): анимации не перезапускаются."""
        self.theme = theme
        self.dpr = max(1.0, float(self.host.devicePixelRatioF()))
        if self.backdrop is None:
            self.backdrop = Backdrop(self, self.host)
            self.overlay = Overlay(self, self.host)
        r = self.area()
        self.backdrop.setGeometry(r)
        self.overlay.setGeometry(r)
        self.backdrop.lower()
        self.overlay.raise_()
        # медиа: оставить только нужные
        need = set()
        b = theme["background"]
        if b["type"] == "media" and b.get("media"):
            need.add(self.asset(b["media"]))
        pi = theme["effects"]["particles"].get("image")
        if pi:
            need.add(self.asset(pi))
        for L in theme["layers"]:
            if L["kind"] == "media" and L.get("src"):
                need.add(self.asset(L["src"]))
        for path in list(self._media):
            if path not in need:
                src = self._media.pop(path)
                src.stop()
                src.deleteLater()
        self._drop_videos(theme)
        if b["type"] == "media":
            src = self.media(b.get("media"))
            if src is not None:
                src.set_speed(b.get("speed", 1.0))
        # видео-обои
        vpath = self.asset(b.get("media")) if b["type"] == "video" else ""
        if vpath != self._video_path:
            if self.video is not None:
                self.video.stop()
                self.video.deleteLater()
                self.video = None
            self._video_path = vpath
            if vpath:
                W, H = self.size()
                self.video = VideoSource(vpath, self, hw=bool(self._settings().get("video_hw", True)),
                                         target=(W * self.dpr, H * self.dpr))
                self.video.changed.connect(self._video_frame)
        if self.video is not None:
            self.video.set_rate(b.get("speed", 1.0))
        self._sync_scripts(theme)
        self.backdrop.set_theme(theme)
        self.overlay.set_theme(theme)
        self.backdrop.show()
        self._last = time.perf_counter()
        self._timer.start(16)

    def _drop_videos(self, theme):
        need = {self.asset(L.get("src")) for L in theme["layers"] if L["kind"] == "video" and L.get("src")}
        for path in list(self._videos):
            if path not in need:
                src = self._videos.pop(path)
                if src is not None:
                    self._vtargets.pop(id(src), None)
                    src.stop()
                    src.deleteLater()

    def _sync_scripts(self, theme):
        """Скрипты, виджеты и эффекты слоёв: создать хозяина по требованию, мышь — только если нужна."""
        try:
            import layer_fx
        except Exception as e:                             # noqa: BLE001
            print("[theme] layer_fx:", e)
            return
        if self.scripts is None:
            if not layer_fx.ScriptHost.needed(theme):
                return
            self.scripts = layer_fx.ScriptHost(self)
            self.input = layer_fx.LayerInput(self)
        self.scripts.sync(theme)
        self.input.enable(self.scripts.interactive(theme))

    def _video_frame(self):
        if self.backdrop is not None:
            self.backdrop.media_changed()

    def layer_bounds(self, theme, W=None, H=None) -> QRegion:
        """Где на экране лежат слои темы (с запасом под анимацию) — для точечной перерисовки."""
        reg = QRegion()
        if theme is None:
            return reg
        sw, sh = self.size()
        W = W or sw
        H = H or sh
        t = self.pulse.t
        for i, L in enumerate(theme.get("layers", [])):
            geo = self.painter.geometry(L, W, H, t, self.pulse, i)
            r = LayerPainter.bounds(geo, 4.0)
            moving = {a.get("type") for a in L.get("anims") or []} - {"spin", "sway", "fade"}
            if moving or (L.get("behavior") or "").strip():
                r = r.adjusted(-0.06 * W, -0.06 * H, 0.06 * W, 0.06 * H)
            reg = reg.united(r.toAlignedRect())
        return reg

    def update_layers(self, theme: dict):
        """Изменились только слои (редактор двигает/правит слой) — без пересборки фона и стилей."""
        if self.backdrop is None or self.theme is None:
            self.apply(theme)
            return
        old = self.theme
        reg = self.layer_bounds(old)
        self.theme = theme
        self.backdrop.t = theme
        self.overlay.t = theme
        for L in theme["layers"]:
            if L["kind"] == "media" and L.get("src"):
                self.media(L["src"])
        self._drop_videos(theme)
        self._sync_scripts(theme)
        self.backdrop._layers.reset()
        self.overlay._layers.reset()
        reg = reg.united(self.layer_bounds(theme))
        self.overlay.setVisible(self.overlay.needed())
        self.backdrop.update(reg)
        self.overlay.update(reg)

    def refresh(self):
        """Перерисовать всё (сменились панели, шрифт и т.п.)."""
        if self.backdrop is not None:
            self.backdrop.invalidate()
            self.overlay.update()

    def clear(self):
        self._timer.stop()
        for src in self._media.values():
            src.stop()
            src.deleteLater()
        self._media.clear()
        for src in self._videos.values():
            if src is not None:
                src.stop()
                src.deleteLater()
        self._videos.clear()
        self._vtargets.clear()
        if self.scripts is not None:
            self.scripts.clear()
        if self.input is not None:
            self.input.enable(False)
        if self.video is not None:
            self.video.stop()
            self.video.deleteLater()
            self.video = None
        self._video_path = None
        for w in (self.backdrop, self.overlay):
            if w is not None:
                w.hide()
                w.deleteLater()
        self.backdrop = self.overlay = None
        self.painter.clear()
        self.theme = None

    def active(self) -> bool:
        return self.theme is not None

    def resize(self, rect=None):
        if self.backdrop is not None:
            r = rect if rect is not None else self.area()
            self.backdrop.setGeometry(r)
            self.overlay.setGeometry(r)
            self.backdrop.lower()
            self.overlay.raise_()

    def raise_overlay(self):
        if self.overlay is not None:
            self.overlay.raise_()

    def panels_changed(self):
        if self.backdrop is not None:
            self.backdrop.update()

    def set_cover(self, path):
        if self.backdrop is not None:
            self.backdrop.set_cover(path)

    def _set_paused(self, paused):
        if paused == self._paused:
            return
        self._paused = paused
        for src in self._media.values():
            src.set_paused(paused)
        for src in self._videos.values():
            if src is not None:
                src.set_paused(paused)
        if self.video is not None:
            self.video.set_paused(paused)

    def _tick(self):
        now = time.perf_counter()
        raw = now - self._last
        dt = frameclock.animation_delta(raw)
        self._last = now
        win = self.host.window()
        hidden = win is None or win.isMinimized() or not win.isVisible()
        hidden = hidden or (QApplication.applicationState() != Qt.ApplicationState.ApplicationActive
                            and getattr(win, "settings", {}).get("pause_unfocused", True))
        self._set_paused(hidden)
        if hidden:
            return
        dpr = max(1.0, float(self.host.devicePixelRatioF()))
        if abs(dpr - self.dpr) > 0.01 and self.theme is not None:   # окно переехало на экран с другим масштабом
            self.painter.clear()
            if self.overlay is not None and self.overlay.particles is not None:
                self.overlay.particles = None
            if self.backdrop is not None:
                self.backdrop.fx.particles = None
            self.apply(self.theme)
            return
        # автокачество: кадры опаздывают — живой фон и снимки под панелями реже
        # (порог — от текущей частоты часов: при 144 Гц кадр «опоздал» уже через ~10 мс)
        self._late += ((1.0 if raw > 1.45 * frameclock.period() else 0.0) - self._late) * 0.03
        self._q_acc += dt
        if self._q_acc >= 1.5:
            self._q_acc = 0.0
            if self._late > 0.22 and self.quality > 0:
                self.quality -= 1
                self._late = 0.1
                self._good = 0
            elif self._late < 0.04 and self.quality < 2:
                self._good += 1
                if self._good >= 3:
                    self.quality += 1
                    self._good = 0
            else:
                self._good = 0
        self.pulse.update(dt)
        if self._vtargets:
            self._apply_video_targets()
        if self.scripts is not None and self.theme is not None:
            W, H = self.size()
            try:
                self.scripts.step(dt, self.theme, W, H)
            except Exception as e:                         # noqa: BLE001  (скрипт не должен ронять плеер)
                print("[theme] scripts:", e)
            self._layer_video_targets(W, H)
            self._apply_bg_motion()
        if self.backdrop is not None:
            self.backdrop.advance(dt)
        if self.overlay is not None:
            self.overlay.advance(dt)

    def bg_motion(self):
        """Поля скрипта фона (speed, zoom, dx, dy, dim) или None."""
        return self.scripts.bg_motion if self.scripts is not None else None

    def _apply_bg_motion(self):
        """Скрипт фона → скорость видео/GIF (не чаще ~12 раз/с — VLC не любит дёргать скорость каждый кадр)
        и пересборка кадра фона, если поменялись зум/сдвиг/затемнение."""
        m = self.bg_motion()
        key = None if m is None else (round(m["zoom"], 3), round(m["dx"], 1), round(m["dy"], 1), round(m["dim"], 3))
        if key != getattr(self, "_bg_key", None):
            self._bg_key = key
            if self.backdrop is not None:
                self.backdrop.bg_script_changed()
        sp = m["speed"] if m is not None else 1.0
        now = time.perf_counter()
        if abs(sp - getattr(self, "_bg_speed", 1.0)) > 0.02 and now - getattr(self, "_bg_speed_t", 0.0) > 0.08:
            self._bg_speed, self._bg_speed_t = sp, now
            b = self.theme["background"]
            base = b.get("speed", 1.0)
            if self.video is not None:
                self.video.set_rate(base * sp)
            if b["type"] == "media":
                src = self.media(b.get("media"))
                if src is not None:
                    src.set_speed(base * sp)

    def bg_speed(self) -> float:
        m = self.bg_motion()
        return m["speed"] if m is not None else 1.0

    def _layer_video_targets(self, W, H):
        """Видео-слои: раз в ~полсекунды — кадр под их размер на экране."""
        self._vt_acc = getattr(self, "_vt_acc", 0.0) + 1
        if not self._videos or self._vt_acc < 30:
            return
        self._vt_acc = 0
        for i, L in enumerate(self.theme["layers"]):
            if L["kind"] != "video" or not L.get("visible", True):
                continue
            src = self.video_layer(L.get("src"), L.get("speed", 1.0))
            if src is None:
                continue
            geo = self.painter.geometry(L, W, H, self.pulse.t, self.pulse, i, still=True)
            k = max(abs(geo["sx"]), abs(geo["sy"]), 1.0)
            self.video_target(src, geo["w"] * k * self.dpr, geo["h"] * k * self.dpr)
