import time
import math as _math
import numpy as np
from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF
from frameclock import FrameTimer, animation_delta
from PyQt6.QtWidgets import QWidget
from PyQt6.QtGui import QPainter, QColor, QLinearGradient, QPen, QBrush, QPainterPath

try:                                                       # C++-ядро (echoes_fx.dll): фон MilkdropVisualizer
    import native_fx as _NF
    _NATIVE = _NF.available()
except Exception:                                          # noqa: BLE001
    _NF, _NATIVE = None, False


class _VisibleAnimator(QWidget):
    """Hidden analyzers must not allocate FFT work buffers on every frame."""
    def showEvent(self, event):
        super().showEvent(event)
        self._last = self._last_t = time.perf_counter()
        self.timer.start(16)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)


class Visualizer(_VisibleAnimator):
    """Визуализатор реального звука из AudioEngine (VLC smem-захват).
    Никакого отдельного чтения/анализа аудиофайла — только то, что в
    данный момент реально выходит из плеера.

    Полосы + падающие «пики» над ними + мягкая кривая со свечением.
    Всё сглаживание — по реальному времени кадра (dt), поэтому скорость
    анимации одинакова при любом FPS; на паузе, когда всё затихло,
    виджет перестаёт перерисовываться."""

    BARS = 64
    WIN_SIZE = 2048  # семплов на кадр FFT

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(130)

        self.values = np.zeros(self.BARS, dtype=np.float32)
        self.targets = np.zeros(self.BARS, dtype=np.float32)
        self.peaks = np.zeros(self.BARS, dtype=np.float32)
        self._peak_vel = np.zeros(self.BARS, dtype=np.float32)
        self._peak_hold = np.zeros(self.BARS, dtype=np.float32)

        self.playing = False
        self.engine = None

        self.accent = QColor(0, 240, 255)
        self.accent2 = QColor(255, 43, 214)
        # вид (конструктор тем): bars / mirror / blocks / line / wave / dots
        self.style = "bars"
        self.show_peaks = True
        self.show_curve = True

        self._hanning = np.hanning(self.WIN_SIZE).astype(np.float32)
        self._bass_boost = np.linspace(2.0, 0.6, self.BARS).astype(np.float32)
        self._running_max = 1e-3
        self._freq_indices = None
        self._last = time.perf_counter()
        self._energy = 0.0

        self.timer = FrameTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self.timer.setInterval(16)

    def set_engine(self, engine):
        """Подключить AudioEngine — источник реальных PCM-семплов плеера."""
        self.engine = engine
        n_fft_bins = self.WIN_SIZE // 2 + 1
        idx = np.logspace(np.log10(2), np.log10(n_fft_bins - 1), self.BARS + 1).astype(int)
        ar = np.arange(idx.size)
        idx = np.maximum.accumulate(idx - ar) + ar               # строго растёт: полоса ≥ 1 бина
        self._freq_indices = np.minimum(idx, n_fft_bins)

    def set_playing(self, is_playing: bool):
        self.playing = bool(is_playing)

    def set_accent(self, c1: QColor, c2: QColor):
        self.accent = c1
        self.accent2 = c2
        self.update()

    STYLES = ("bars", "mirror", "blocks", "line", "wave", "dots")

    def set_style(self, style="bars", peaks=True, curve=True):
        """Вид визуализатора: столбики (по умолчанию), зеркальные, сегменты как в Winamp,
        линия, волна с заливкой, точки. peaks — «пики» над столбиками, curve — кривая сверху."""
        self.style = style if style in self.STYLES else "bars"
        self.show_peaks = bool(peaks)
        self.show_curve = bool(curve)
        self.update()

    def _compute_bars(self, samples: "np.ndarray"):
        fft_mag = np.abs(np.fft.rfft(samples * self._hanning))
        # среднее по полосам одним махом (кумулятивная сумма вместо 64 вызовов np.mean)
        cs = np.concatenate(([0.0], np.cumsum(fft_mag, dtype=np.float64)))
        i0, i1 = self._freq_indices[:-1], self._freq_indices[1:]
        bars = ((cs[i1] - cs[i0]) / (i1 - i0)).astype(np.float32) * self._bass_boost

        frame_max = float(bars.max())
        # плавающий потолок: быстро растёт, медленно опускается —
        # без него громкость трека "прыгала" бы по высоте баров
        if frame_max > self._running_max:
            self._running_max = frame_max
        else:
            self._running_max *= _math.exp(-getattr(self, '_analysis_dt', 1/60) * .907)
        self._running_max = max(self._running_max, 1e-3)

        bars = np.log1p(bars * 12) / np.log1p(self._running_max * 12)
        return np.clip(bars, 0.0, 1.0)

    def _tick(self):
        now = time.perf_counter()
        dt = animation_delta(now - self._last, .05)
        self._last = now
        self._analysis_dt = dt
        if self.playing and self.engine is not None and self._freq_indices is not None:
            samples = self.engine.get_visual_samples(self.WIN_SIZE)
            if samples is not None and len(samples) == self.WIN_SIZE:
                self.targets = self._compute_bars(samples)
            else:
                self.targets.fill(0)
        else:
            self.targets.fill(0)

        up = 1.0 - np.exp(-dt * 48.0)
        down = 1.0 - np.exp(-dt * 11.0)
        k = np.where(self.targets > self.values, up, down).astype(np.float32)
        self.values += (self.targets - self.values) * k

        # пики: подпрыгивают вместе с полосой, держатся ~0.25 с и падают с ускорением
        hit = self.values >= self.peaks
        self.peaks[hit] = self.values[hit]
        self._peak_vel[hit] = 0.0
        self._peak_hold[hit] = 0.25
        self._peak_hold[~hit] -= dt
        fall = (~hit) & (self._peak_hold <= 0)
        self._peak_vel[fall] += 2.4 * dt
        self.peaks[fall] -= self._peak_vel[fall] * dt
        np.maximum(self.peaks, self.values, out=self.peaks)

        self._energy += (float(self.values[:12].mean()) - self._energy) * (1.0 - _math.exp(-dt * 10.0))

        if not self.playing and float(self.peaks.max()) < 0.003:
            return                                # тишина — не перерисовываемся впустую
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        w, h = self.width(), self.height()
        n = self.BARS
        bar_w = w / n
        usable = h - 15
        bw = max(1.0, bar_w - 2)
        xs = np.arange(n) * bar_w + 1
        st = self.style

        grad = QLinearGradient(0, h, 0, 0)
        grad.setColorAt(0.0, self.accent2)
        grad.setColorAt(1.0, self.accent)
        p.setPen(Qt.PenStyle.NoPen)
        if st in ("line", "wave"):
            self._paint_wave(p, w, h, xs + bw / 2, usable, fill=(st == "wave"))
            return
        if st == "mirror":
            mid = h / 2.0
            half = h / 2.0 - 4
            vals = np.maximum(1.0, self.values * half)
            g = QLinearGradient(0, 0, 0, h)
            g.setColorAt(0.0, self.accent)
            g.setColorAt(0.5, self.accent2)
            g.setColorAt(1.0, self.accent)
            p.setBrush(QBrush(g))
            p.drawRects([QRectF(x, mid - v, bw, 2 * v) for x, v in zip(xs.tolist(), vals.tolist())])
            if self.show_peaks:
                pk = self.peaks * half
                cap_c = QColor(self.accent)
                cap_c.setAlpha(220)
                p.setBrush(cap_c)
                caps = []
                for x, v in zip(xs.tolist(), pk.tolist()):
                    if v > 3:
                        caps.append(QRectF(x, mid - v - 4, bw, 2.0))
                        caps.append(QRectF(x, mid + v + 2, bw, 2.0))
                p.drawRects(caps)
        elif st == "blocks":
            seg, gap = 4.0, 2.0
            step = seg + gap
            nseg = np.floor(self.values * usable / step).astype(int)
            p.setBrush(QBrush(grad))
            rects = []
            for x, k in zip(xs.tolist(), nseg.tolist()):
                for j in range(max(1, k)):
                    rects.append(QRectF(x, h - (j + 1) * step + gap, bw, seg))
            p.drawRects(rects)
            if self.show_peaks:
                pk = np.floor(self.peaks * usable / step).astype(int)
                cap_c = QColor(self.accent)
                cap_c.setAlpha(235)
                p.setBrush(cap_c)
                p.drawRects([QRectF(x, h - (k + 1) * step + gap, bw, seg)
                             for x, k in zip(xs.tolist(), pk.tolist()) if k > 1])
        elif st == "dots":
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            r = max(1.5, min(bw * 0.45, 5.0))
            ys = h - 3 - r - self.values * (usable - r)
            p.setBrush(QBrush(grad))
            for x, y in zip((xs + bw / 2).tolist(), ys.tolist()):
                p.drawEllipse(QPointF(x, y), r, r)
            if self.show_peaks:
                cap_c = QColor(self.accent)
                cap_c.setAlpha(120)
                p.setBrush(cap_c)
                pk = h - 3 - r - self.peaks * (usable - r)
                for x, y, v in zip((xs + bw / 2).tolist(), pk.tolist(), self.peaks.tolist()):
                    if v > 0.03:
                        p.drawEllipse(QPointF(x, y), r * 0.6, r * 0.6)
        else:                                       # bars
            vals = np.maximum(2.0, self.values * usable)
            # полосы и пики — прямоугольники одним вызовом, без сглаживания (им оно не нужно)
            p.setBrush(QBrush(grad))
            p.drawRects([QRectF(x, h - bh, bw, bh) for x, bh in zip(xs.tolist(), vals.tolist())])
            if self.show_peaks:
                pk = self.peaks * usable
                cap_c = QColor(self.accent)
                cap_c.setAlpha(230)
                p.setBrush(cap_c)
                p.drawRects([QRectF(x, h - v - 5, bw, 2.0) for x, v in zip(xs.tolist(), pk.tolist()) if v > 4])
        if not self.show_curve:
            return

        # кривая по вершинам: сглаживаем в numpy (×3 точек), рисуем одной ломаной
        ys = h * 0.5 - (self.values - 0.2) * h * 0.45
        cx = xs + bw / 2
        fine = np.linspace(0, n - 1, n * 3)
        fy = np.interp(fine, np.arange(n), np.convolve(np.pad(ys, 1, mode="edge"), [0.25, 0.5, 0.25], "valid"))
        fx = np.interp(fine, np.arange(n), cx)
        pts = [QPointF(0, h * 0.5)] + [QPointF(x, y) for x, y in zip(fx.tolist(), fy.tolist())]
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(Qt.BrushStyle.NoBrush)
        glow = QColor(self.accent)
        glow.setAlpha(int(35 + 90 * min(1.0, self._energy * 1.6)))
        p.setPen(QPen(glow, 3.5))
        p.drawPolyline(pts)
        p.setPen(QPen(self.accent, 1.5))
        p.drawPolyline(pts)

    def _paint_wave(self, p, w, h, cx, usable, fill):
        """Линия / волна с заливкой по вершинам полос (сглаженная)."""
        n = self.BARS
        ys = h - 3 - self.values * usable
        fine = np.linspace(0, n - 1, n * 3)
        fy = np.interp(fine, np.arange(n), np.convolve(np.pad(ys, 2, mode="edge"), [0.1, 0.2, 0.4, 0.2, 0.1], "valid"))
        fx = np.interp(fine, np.arange(n), cx)
        pts = [QPointF(0, float(fy[0]))] + [QPointF(x, y) for x, y in zip(fx.tolist(), fy.tolist())] + \
            [QPointF(w, float(fy[-1]))]
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if fill:
            path = QPainterPath()
            path.moveTo(0, h)
            for pt in pts:
                path.lineTo(pt)
            path.lineTo(w, h)
            path.closeSubpath()
            g = QLinearGradient(0, h - usable, 0, h)
            c1 = QColor(self.accent); c1.setAlpha(190)
            c2 = QColor(self.accent2); c2.setAlpha(40)
            g.setColorAt(0.0, c1)
            g.setColorAt(1.0, c2)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(g))
            p.drawPath(path)
        p.setBrush(Qt.BrushStyle.NoBrush)
        glow = QColor(self.accent)
        glow.setAlpha(int(45 + 100 * min(1.0, self._energy * 1.6)))
        p.setPen(QPen(glow, 5.0 if not fill else 3.5))
        p.drawPolyline(pts)
        p.setPen(QPen(self.accent, 2.0 if not fill else 1.5))
        p.drawPolyline(pts)


# ─────────────────────────────────────────────────────────────── #
#  MilkdropVisualizer — психоделическая FFT-визуализация          #
#  Вдохновлено Winamp Milkdrop: плазма + частицы + осциллоскоп   #
# ─────────────────────────────────────────────────────────────── #
import time
import math as _math
from PyQt6.QtGui import QRadialGradient, QFont


class MilkdropVisualizer(_VisibleAnimator):
    """Milkdrop-подобный визуализатор:
    • плазменный фон на базе FFT (смешение цветов по басу/средним/ВЧ)
    • «звёзды»-частицы, реагирующие на энергию
    • осциллоскоп-кольцо в центре
    • FFT-столбики по периметру (полярные)
    """

    BARS        = 128
    WIN_SIZE    = 4096
    MAX_STARS   = 200

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(200)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

        self.values  = np.zeros(self.BARS,    dtype=np.float32)
        self.targets = np.zeros(self.BARS,    dtype=np.float32)
        self.wave    = np.zeros(512,           dtype=np.float32)   # осциллоскоп

        self.playing = False
        self.engine  = None

        # Плазма — смещение фазы по времени
        self._t       = 0.0
        self._phase   = np.random.uniform(0, 2 * np.pi, 6).astype(np.float32)

        # Энергии частотных диапазонов (0..1, сглаженные)
        self._e_bass  = 0.0
        self._e_mid   = 0.0
        self._e_hi    = 0.0

        # Частицы
        rng = np.random.default_rng()
        self._stars_x   = rng.uniform(0, 1, self.MAX_STARS).astype(np.float32)
        self._stars_y   = rng.uniform(0, 1, self.MAX_STARS).astype(np.float32)
        self._stars_vx  = rng.uniform(-0.0015, 0.0015, self.MAX_STARS).astype(np.float32)
        self._stars_vy  = rng.uniform(-0.0015, 0.0015, self.MAX_STARS).astype(np.float32)
        self._stars_age = rng.uniform(0, 1, self.MAX_STARS).astype(np.float32)
        self._stars_hue = rng.uniform(0, 360, self.MAX_STARS).astype(np.float32)

        self._running_max = 1e-3
        self._hanning     = np.hanning(self.WIN_SIZE).astype(np.float32)
        self._bass_boost  = np.linspace(2.5, 0.5, self.BARS).astype(np.float32)

        # Буфер QImage для плазмы (пересоздаётся при resize)
        self._plasma_img: "QImage | None" = None
        self._plasma_w = 0
        self._plasma_h = 0
        self._plasma_scale = 6          # разрешение плазмы (1 пиксель = N экранных)

        # Предыдущий кадр плазмы — рисуется под новым с zoom+rotate и
        # понижённой непрозрачностью => классический Milkdrop "warp"/
        # feedback-эффект (тянущиеся, "плавящиеся" узоры) практически
        # бесплатно, без пересчёта фрактала на каждый пиксель.
        self._prev_img = None

        # "Пресеты": раз в ~14-20 сек случайно перекатываем набор
        # параметров плазмы (сила варпа, скорость смены оттенка, закрутка)
        # и плавно интерполируем к новой цели — картинка со временем сама
        # меняет "характер", как смена пресетов в настоящем Milkdrop.
        self._preset_timer = 0.0
        self._preset_interval = float(np.random.uniform(14.0, 20.0))
        self._warp_amt = 1.0
        self._warp_amt_target = 1.0
        self._hue_speed = 70.0
        self._hue_speed_target = 70.0
        self._swirl = 0.5
        self._swirl_target = 0.5

        self.timer = FrameTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.setInterval(16)

    # ── Публичный API (совместим с Visualizer) ── #

    def set_engine(self, engine):
        self.engine = engine
        rate = getattr(engine, "visual_sample_rate", 44100)
        n_fft_bins = self.WIN_SIZE // 2 + 1
        self._freq_indices = np.logspace(
            np.log10(2), np.log10(n_fft_bins - 1), self.BARS + 1
        ).astype(int)

    def set_playing(self, is_playing: bool):
        self.playing = bool(is_playing)

    def set_accent(self, c1: QColor, c2: QColor):
        # В Milkdrop акцент используется как оттенок частиц — применяем мягко
        self._accent_h = c1.hsvHueF() * 360
        self.update()

    # ── Вычисления ── #

    def _compute_bars(self, samples: np.ndarray):
        windowed   = samples * self._hanning
        fft_mag    = np.abs(np.fft.rfft(windowed))

        bars = np.zeros(self.BARS, dtype=np.float32)
        for b in range(self.BARS):
            i0 = self._freq_indices[b]
            i1 = max(i0 + 1, self._freq_indices[b + 1])
            bars[b] = np.mean(fft_mag[i0:i1]) * self._bass_boost[b]

        frame_max = float(bars.max())
        if frame_max > self._running_max:
            self._running_max = frame_max
        else:
            self._running_max *= _math.exp(-getattr(self, '_analysis_dt', 1/60) * 1.09)
        self._running_max = max(self._running_max, 1e-3)

        bars = np.log1p(bars * 14) / np.log1p(self._running_max * 14)
        return np.clip(bars, 0.0, 1.0)

    def _tick(self):
        # Шаги ниже подобраны «за кадр 60 Гц»; k — сколько таких кадров прошло на самом деле,
        # поэтому при 120/144 Гц всё движется с той же скоростью, только плавнее.
        now = time.perf_counter()
        dt = animation_delta(now - getattr(self, "_last_t", now - 1 / 60), .05)
        self._last_t = now
        self._analysis_dt = dt
        k = dt * 60.0

        def ease(a):                                 # сглаживание «a за кадр» → за прошедшее время
            return 1.0 - (1.0 - a) ** k

        # Базовый ход времени ускорен; бас дополнительно «пинает» темп
        self._t += (0.038 + self._e_bass * 0.055) * k

        # Смена "пресета" — раз в 14-20 сек выбираем новую случайную цель
        # для варпа/закрутки/скорости оттенка, плавно тянемся к ней каждый
        # кадр (никаких резких скачков картинки).
        self._preset_timer += dt
        if self._preset_timer >= self._preset_interval:
            self._preset_timer = 0.0
            self._preset_interval = float(np.random.uniform(14.0, 20.0))
            self._warp_amt_target = float(np.random.uniform(0.4, 2.6))
            self._hue_speed_target = float(np.random.uniform(30.0, 140.0))
            self._swirl_target = float(np.random.uniform(0.0, 1.4))
        e2 = ease(0.02)
        self._warp_amt += (self._warp_amt_target - self._warp_amt) * e2
        self._hue_speed += (self._hue_speed_target - self._hue_speed) * e2
        self._swirl += (self._swirl_target - self._swirl) * e2

        if self.playing and self.engine is not None:
            samples = self.engine.get_visual_samples(self.WIN_SIZE)
            if samples is not None:
                self.targets = self._compute_bars(samples)
                # осциллоскоп — берём 512 семплов из центра окна
                mid = len(samples) // 2
                chunk = samples[mid - 256: mid + 256]
                if len(chunk) == 512:
                    mx = float(np.max(np.abs(chunk))) or 1e-5
                    self.wave = (chunk / mx).astype(np.float32)
                # диапазоны: бас 0..12%, средние 12..50%, ВЧ 50..100%
                lo = self.BARS // 8
                mi = self.BARS // 2
                self._e_bass = float(np.mean(self.targets[:lo]))
                self._e_mid  = float(np.mean(self.targets[lo:mi]))
                self._e_hi   = float(np.mean(self.targets[mi:]))
            else:
                self.targets.fill(0)
                self._e_bass = self._e_mid = self._e_hi = 0.0
        else:
            self.targets.fill(0)
            # Плавно гасим энергии до нуля, не обнуляем мгновенно
            fade = 0.92 ** k
            self._e_bass *= fade
            self._e_mid  *= fade
            self._e_hi   *= fade

        # Сглаживание столбиков
        diff = self.targets - self.values
        up   = diff > 0
        self.values[up]  += diff[up]  * ease(0.6)
        self.values[~up] += diff[~up] * ease(0.15)
        np.clip(self.values, 0, 1, out=self.values)

        # Частицы
        speed = (1.0 + self._e_bass * 4.0) * k
        self._stars_x   += self._stars_vx * speed
        self._stars_y   += self._stars_vy * speed
        self._stars_age += (0.008 + self._e_hi * 0.04) * k
        self._stars_hue  = (self._stars_hue + self._e_bass * 3 * k) % 360

        dead = (
            (self._stars_x < 0) | (self._stars_x > 1) |
            (self._stars_y < 0) | (self._stars_y > 1) |
            (self._stars_age > 1)
        )
        n_dead = int(dead.sum())
        if n_dead:
            rng = np.random.default_rng()
            cx = 0.5 + rng.uniform(-0.1, 0.1, n_dead).astype(np.float32)
            cy = 0.5 + rng.uniform(-0.1, 0.1, n_dead).astype(np.float32)
            self._stars_x[dead] = cx
            self._stars_y[dead] = cy
            ang  = rng.uniform(0, 2 * np.pi, n_dead).astype(np.float32)
            spd  = rng.uniform(0.0005, 0.003, n_dead).astype(np.float32) * (1 + self._e_bass * 3)
            self._stars_vx[dead] = np.cos(ang) * spd
            self._stars_vy[dead] = np.sin(ang) * spd
            self._stars_age[dead] = rng.uniform(0, 0.2, n_dead).astype(np.float32)
            self._stars_hue[dead] = rng.uniform(0, 360, n_dead).astype(np.float32)

        self.update()

    # ── Плазма ── #

    def _rebuild_plasma(self, w: int, h: int):
        """Генерирует QImage плазменного фона низкого разрешения."""
        from PyQt6.QtGui import QImage
        s  = self._plasma_scale
        pw = max(1, w // s)
        ph = max(1, h // s)

        t  = self._t
        eb = self._e_bass
        em = self._e_mid
        eh = self._e_hi

        xs = np.linspace(0, 4 * np.pi, pw, dtype=np.float32)
        ys = np.linspace(0, 4 * np.pi, ph, dtype=np.float32)
        X, Y = np.meshgrid(xs, ys)

        # Domain warp: перед подстановкой в синусы искажаем сами координаты
        # волнами, зависящими от времени и текущего "пресета" (_warp_amt) —
        # это и даёт настоящий фрактально-перетекающий вид вместо статичной
        # ряби, характерной для простого наложения синусоид.
        warp = self._warp_amt * (0.9 + eb * 1.3)
        Xw = X + np.sin(Y * 0.5 + t * 0.37) * warp
        Yw = Y + np.cos(X * 0.5 + t * 0.31) * warp
        # Второй проход варпа от уже искажённых координат — усиливает
        # ощущение "плавящегося" фрактала при высоких значениях пресета.
        Xw2 = Xw + np.sin(Yw * 0.33 + t * 0.53) * (warp * 0.5)
        Yw2 = Yw + np.cos(Xw * 0.29 - t * 0.47) * (warp * 0.5)

        # Фазы дрейфуют от энергии — при ударах баса плазма «скачет»
        self._phase[0] += eb * 0.18 + em * 0.06
        self._phase[1] += eh * 0.14 + eb * 0.05
        self._phase[2] += (eb + em) * 0.09
        self._phase[3] += eh * 0.12
        self._phase[4] += eb * 0.20
        self._phase[5] += (em + eh) * 0.07

        # 6 синусоидальных слоёв (часть — по искажённым Xw/Yw координатам)
        p1 = (_math.sin(t * 1.6 + self._phase[0]) *
              np.sin(X * 1.1 + t * 0.55) + np.sin(Y * 0.85 + t * 0.7))
        p2 = (np.sin(X * 0.75 + _math.cos(t * 1.3 + self._phase[1]) * (1 + eb)) +
              np.sin(Y * 1.0 + t * 0.9 + eb * 0.8))
        p3 = np.sin(np.sqrt((Xw - np.pi * (1 + eb * 0.4)) ** 2 +
                             (Yw - np.pi * (1 + em * 0.4)) ** 2) * 1.1 + t * 1.9)
        p4 = (np.sin(Xw2 * 0.55 * (1 + eh * 1.5) + t * 1.1 + self._phase[2]) +
              np.cos(Yw2 * 0.65 * (1 + em * 1.2) + t * 0.95))
        p5 = np.sin((Xw + Yw) * 0.6 * (1 + self._swirl * 0.5) + t * 1.4 + self._phase[3] + eb * 1.2)
        p6 = np.cos(Xw2 * 0.38 * (1 + eb * 3) - Yw2 * 0.5 + t * 1.7 + self._phase[4])

        v = (p1 + p2 + p3 + p4 + p5 + p6) / 6.0   # -1..1
        # нормализуем в 0..1
        v = (v - v.min()) / ((v.max() - v.min()) + 1e-8)

        # Маппинг в RGB — психоделическая палитра
        # Hue крутится с изменяемой (пресетом) скоростью, бас дополнительно гонит цвет
        hue_base = (t * self._hue_speed + eb * 120) % 360
        h_arr    = (hue_base + v * 300 + em * 80) % 360
        s_arr    = np.clip(0.75 + em * 0.25 + eb * 0.1, 0, 1).astype(np.float32)
        l_arr    = np.clip(0.10 + v * 0.52 + eh * 0.20 + eb * 0.08, 0, 1).astype(np.float32)

        # HSL → RGB (векторизовано)
        def hsl2rgb(H, S, L):
            C = (1 - np.abs(2 * L - 1)) * S
            Hp = H / 60.0
            X_ = C * (1 - np.abs(Hp % 2 - 1))
            R = np.zeros_like(H); G = np.zeros_like(H); B = np.zeros_like(H)
            for seg, (ri, gi, bi) in enumerate([
                (C, X_, 0), (X_, C, 0), (0, C, X_),
                (0, X_, C), (X_, 0, C), (C, 0, X_)
            ]):
                mask = (Hp >= seg) & (Hp < seg + 1)
                R[mask] = ri if np.isscalar(ri) else ri[mask]
                G[mask] = gi if np.isscalar(gi) else gi[mask]
                B[mask] = bi if np.isscalar(bi) else bi[mask]
            m = L - C / 2
            return R + m, G + m, B + m

        R, G, B = hsl2rgb(h_arr.astype(np.float64),
                          s_arr.astype(np.float64),
                          l_arr.astype(np.float64))

        R = np.clip(R * 255, 0, 255).astype(np.uint8)
        G = np.clip(G * 255, 0, 255).astype(np.uint8)
        B = np.clip(B * 255, 0, 255).astype(np.uint8)
        A = np.full((ph, pw), 230, dtype=np.uint8)   # лёгкая прозрачность

        rgba = np.stack([R, G, B, A], axis=-1)
        raw  = rgba.tobytes()

        img = QImage(raw, pw, ph, pw * 4, QImage.Format.Format_RGBA8888).copy()
        self._plasma_img  = img
        self._plasma_w    = w
        self._plasma_h    = h

    # ── Отрисовка ── #

    def _paint_bg_native(self, p, w, h) -> bool:
        """C++: плазма, «обратная связь», виньетка и скругление — одним кадром размером с виджет
        (в пикселях устройства), GUI только копирует его. Повторяет шаги 1–3 numpy-версии."""
        if not _NATIVE:
            return False
        eb, em, eh = self._e_bass, self._e_mid, self._e_hi
        self._phase[0] += eb * 0.18 + em * 0.06
        self._phase[1] += eh * 0.14 + eb * 0.05
        self._phase[2] += (eb + em) * 0.09
        self._phase[3] += eh * 0.12
        self._phase[4] += eb * 0.20
        self._phase[5] += (em + eh) * 0.07
        dpr = self.devicePixelRatioF()
        W, H = max(2, int(w * dpr)), max(2, int(h * dpr))
        pp = _NF.VizPlasmaParams()
        pp.pw, pp.ph = max(8, W // 4), max(6, H // 4)
        pp.t, pp.eb, pp.em, pp.eh = self._t, eb, em, eh
        pp.warp, pp.swirl, pp.hue_speed = self._warp_amt, self._swirl, self._hue_speed
        pp.ph0, pp.ph1, pp.ph2, pp.ph3, pp.ph4 = (float(x) for x in self._phase[:5])
        cur = _NF.vizm_plasma(pp)
        prev = getattr(self, "_nat_prev", None)
        if prev is not None and prev.shape != cur.shape:
            prev = None
        cp = _NF.VizComposeParams()
        cp.W, cp.H, cp.pw, cp.ph = W, H, pp.pw, pp.ph
        cp.zoom = 1.012 + eb * 0.012
        cp.rot_deg = 0.5 + eh * 0.6
        cp.radius = 22 * dpr
        cp.has_prev = 1 if prev is not None else 0
        img = getattr(self, "_nat_img", None)
        if img is None or img.width() != W or img.height() != H:
            from PyQt6.QtGui import QImage
            img = self._nat_img = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
        _NF.vizm_compose(cp, prev, cur, img)
        self._nat_prev = cur
        self._overlay_native(img, w, h, dpr)
        img.setDevicePixelRatio(dpr)
        p.drawImage(QPointF(0, 0), img)
        return True

    def _overlay_native(self, img, w, h, dpr):
        """Шаги 4–7 (полярные столбики, кольцо-осциллограф, звёзды, пульс) — сглаженными капсулами
        в C++ вместо сотен вызовов QPainter."""
        n = self.BARS
        cx, cy = w / 2.0, h / 2.0
        r0, r_max = min(w, h) * 0.22, min(w, h) * 0.45
        eb, eh = self._e_bass, self._e_hi
        v = np.clip(self.values.astype(np.float32), 0, 1)
        ang = 2 * np.pi * np.arange(n, dtype=np.float32) / n - np.pi / 2
        ca, sa = np.cos(ang), np.sin(ang)
        r1 = r0 + v * (r_max - r0)
        th = np.maximum(1.5, (w / n) * 0.55 * (1 + v * 0.8))
        caps = np.stack([np.floor(cx + ca * r0), np.floor(cy + sa * r0), np.floor(cx + ca * r1), np.floor(cy + sa * r1),
                         th * 0.5], axis=1) * np.float32(dpr)
        caps[:, 4] = th * 0.5 * dpr
        hue = ((self._t * 30 + np.arange(n) / n * 360 + eb * 90) % 360) / 360.0
        cols = _NF.hsv_premul(hue, 0.85, np.clip(0.6 + v * 0.4, 0, 1), np.clip(180 + v * 75, 0, 255) / 255.0)
        _NF.capsules(img, caps, cols)
        # кольцо-осциллограф
        wave = np.asarray(self.wave, np.float32)
        if len(wave) > 2:
            ring_r = r0 - 4
            a2 = 2 * np.pi * np.arange(len(wave), dtype=np.float32) / len(wave) - np.pi / 2
            rr = ring_r * (1 + wave * 0.25)
            xs, ys = (cx + np.cos(a2) * rr) * dpr, (cy + np.sin(a2) * rr) * dpr
            seg = np.stack([xs, ys, np.roll(xs, -1), np.roll(ys, -1), np.full_like(xs, 0.75 * dpr)], axis=1)
            _NF.capsules(img, seg, np.array([0x78787878], np.uint32), polyline=True)
        # звёзды
        age = self._stars_age
        alive = age <= 1
        alpha = (1 - age) * np.minimum(1.0, age * 6)
        alive &= alpha * 255 >= 5
        if alive.any():
            size = np.maximum(1.0, 3.0 * (1 - age[alive]) * (1 + eb * 2))
            sx, sy = self._stars_x[alive] * w * dpr, self._stars_y[alive] * h * dpr
            st = np.stack([sx, sy, sx, sy, size * 0.5 * dpr], axis=1)
            sc = _NF.hsv_premul((self._stars_hue[alive] % 360) / 360.0, 0.9, min(1.0, 0.7 + eh * 0.5), alpha[alive])
            _NF.capsules(img, st, sc)
        # пульсирующий круг (бас)
        pr = r0 * (0.55 + eb * 0.5)
        pc = _NF.hsv_premul(np.array([(self._t * 25) % 360 / 360.0]), 1.0, 1.0, 80 / 255.0)
        _NF.capsules(img, np.array([[cx * dpr, cy * dpr, cx * dpr, cy * dpr, pr * dpr]], np.float32), pc)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        native_bg = self._paint_bg_native(p, w, h)

        # Клипинг по скруглённым углам (радиус 22px — как SubPanel); у C++-фона углы уже прозрачные,
        # а столбики и кольцо до углов не достают — дорогой путь отсечения не нужен
        if not native_bg:
            clip_path = QPainterPath()
            clip_path.addRoundedRect(QRectF(0, 0, w, h), 22, 22)
            p.setClipPath(clip_path)

        # 1. Тёмный фон
        if not native_bg:
            p.fillRect(0, 0, w, h, QColor(4, 2, 10))

        # 2. Плазма + feedback-хвосты (эффект "варпа" как в классическом
        # Milkdrop): предыдущий кадр рисуется чуть увеличенным и
        # повёрнутым, с пониженной непрозрачностью, под новым кадром —
        # это и создаёт впечатление "перетекания"/бесконечного зума.
        if not native_bg:
            self._rebuild_plasma(w, h)
        eb = self._e_bass
        if not native_bg and self._prev_img is not None:
            zoom = 1.012 + eb * 0.012
            rot = 0.5 + self._e_hi * 0.6
            pw_, ph_ = max(1, int(w * zoom)), max(1, int(h * zoom))
            prev_scaled = self._prev_img.scaled(
                pw_, ph_, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
            p.save()
            p.translate(cx, cy)
            p.rotate(rot)
            p.setOpacity(0.86)
            p.drawImage(QRectF(-pw_ / 2, -ph_ / 2, pw_, ph_), prev_scaled)
            p.restore()
        if not native_bg and self._plasma_img is not None:
            scaled = self._plasma_img.scaled(
                w, h,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            p.setOpacity(0.6)
            p.drawImage(0, 0, scaled)
            p.setOpacity(1.0)
        self._prev_img = self._plasma_img

        # 3. Радиальное затемнение по краям (виньетка)
        if not native_bg:
            vign = QRadialGradient(cx, cy, max(w, h) * 0.65)
            vign.setColorAt(0.0, QColor(0, 0, 0, 0))
            vign.setColorAt(1.0, QColor(0, 0, 0, 160))
            p.fillRect(0, 0, w, h, QBrush(vign))

        if native_bg:                                # столбики, кольцо, звёзды и пульс уже в кадре C++
            p.end()
            return

        # 4. Полярные FFT-столбики
        n    = self.BARS
        r0   = min(w, h) * 0.22        # внутренний радиус
        r_max = min(w, h) * 0.45        # максимальный радиус
        eb   = self._e_bass

        for i in range(n):
            angle   = 2 * _math.pi * i / n - _math.pi / 2
            blen    = float(self.values[i]) * (r_max - r0)
            r_inner = r0
            r_outer = r0 + blen

            x0 = cx + _math.cos(angle) * r_inner
            y0 = cy + _math.sin(angle) * r_inner
            x1 = cx + _math.cos(angle) * r_outer
            y1 = cy + _math.sin(angle) * r_outer

            # Цвет столбика зависит от его позиции (радуга)
            hue = (self._t * 30 + i / n * 360 + eb * 90) % 360
            alpha = max(0, min(255, int(180 + float(self.values[i]) * 75)))
            col   = QColor.fromHsvF(hue / 360.0,
                                    0.85,
                                    max(0.0, min(1.0, 0.6 + float(self.values[i]) * 0.4)))
            col.setAlpha(alpha)

            thickness = max(1.5, (w / n) * 0.55 * (1 + float(self.values[i]) * 0.8))
            pen = QPen(col, thickness)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawLine(int(x0), int(y0), int(x1), int(y1))

        # 5. Светящееся центральное кольцо (осциллоскоп)
        ring_r = r0 - 4
        n_wave  = len(self.wave)
        if n_wave > 2:
            glow_pen = QPen(QColor(255, 255, 255, 120), 1.5)
            p.setPen(glow_pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            path = QPainterPath()
            for j in range(n_wave):
                angle  = 2 * _math.pi * j / n_wave - _math.pi / 2
                rr     = ring_r * (1 + float(self.wave[j]) * 0.25)
                x      = cx + _math.cos(angle) * rr
                y      = cy + _math.sin(angle) * rr
                if j == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            path.closeSubpath()
            p.drawPath(path)

        # 6. Частицы-звёзды
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(self.MAX_STARS):
            age  = float(self._stars_age[i])
            if age > 1:
                continue
            alpha = int(255 * (1 - age) * min(1.0, age * 6))
            if alpha < 5:
                continue
            hue   = float(self._stars_hue[i])
            val   = min(1.0, 0.7 + self._e_hi * 0.5)
            # прозрачность во fromHsvF — доля 0..1 (раньше передавалось 0..255: звёзды не рисовались,
            # а Qt тысячами писал «HSV parameters out of range»)
            col   = QColor.fromHsvF((hue % 360) / 360.0, 0.9, max(0.0, val), min(255, alpha) / 255.0)
            size  = max(1.0, 3.0 * (1 - age) * (1 + self._e_bass * 2))
            px    = float(self._stars_x[i]) * w
            py    = float(self._stars_y[i]) * h
            p.setBrush(col)
            p.drawEllipse(QRectF(px - size / 2, py - size / 2, size, size))

        # 7. Пульсирующий центральный круг (бас)
        pulse_r  = r0 * (0.55 + self._e_bass * 0.5)
        pulse_col = QColor.fromHsvF((self._t * 25) % 360 / 360, 1.0, 1.0, 80 / 255.0)
        p.setBrush(pulse_col)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(cx - pulse_r, cy - pulse_r, pulse_r * 2, pulse_r * 2))

        p.end()
