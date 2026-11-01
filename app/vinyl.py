from pathlib import Path
import math
from img_load import load_pixmap
from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QTimer, QPointF, QRectF, pyqtSignal
from frameclock import FrameTimer, animation_delta
from PyQt6.QtGui import (
    QPainter, QColor, QBrush, QPen, QPixmap, QPainterPath,
    QRadialGradient, QConicalGradient, QLinearGradient, QCursor
)

class VinylWidget(QWidget):
    """Винил + бесшовная атмосфера без квадратных артефактов + скретч."""

    # Сигналы для скретча — подключаются в main.py к AudioEngine
    scratch_started  = pyqtSignal()          # мышь нажата на диске
    scratch_moved    = pyqtSignal(float)     # delta позиции в секундах (+ вперёд / − назад)
    scratch_released = pyqtSignal()          # мышь отпущена

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(280, 280)
        from PyQt6.QtWidgets import QSizePolicy
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # Виджет прозрачный — фон рисуем сами, никаких системных заливок
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setMouseTracking(True)

        self.angle       = 0.0
        self.spin_speed  = 0.0   # текущая угловая скорость — плавно догоняет целевую
        self.playing     = False
        self.cover       = QPixmap()

        # Два пре-blur уровня: очень маленький (сильный blur) и средний (мягкий)
        self._blur_tiny  = QPixmap()   # 8×8  → сильный blur
        self._blur_mid   = QPixmap()   # 32×32 → средний blur

        # Доминирующий цвет обложки (для ambient glow)
        self._glow_color = QColor(120, 100, 200)
        # Принудительный цвет ambient-фона позади винила (например, для
        # светлых тем вроде Snow — там цветное пятно из обложки смотрится
        # аляповато). None = обычное поведение, цвет берётся из обложки.
        self._ambient_override = None

        self.tonearm_pos    = 0.0
        self._target_tonearm = 0.0
        self._last   = __import__("time").monotonic()
        self.bg_time = 0.0

        # ── Скретч-состояние ─────────────────────────────────────────────
        self._scratching      = False   # зажата ли мышь на диске
        self._scratch_angle   = 0.0    # угол курсора в момент последнего события (градусы)
        self._scratch_speed   = 0.0    # угловая скорость во время скретча (°/с)
        # Одна «единица» поворота диска = сколько секунд трека
        # Стандартный LP: 33⅓ об/мин → полный оборот = ~1.8 сек при нормальной скорости.
        # Здесь выбираем "чувствительность" скретча: 360° ≈ 2 сек.
        self._scratch_sec_per_deg = 2.0 / 360.0
        # Для инерции после отпускания мыши
        self._scratch_momentum    = 0.0   # остаточная угловая скорость после отпускания

        # ── Реактивная подсветка (по RMS реально звучащего аудио) ────────
        self._engine = None
        self._audio_level = 0.0   # сглаженный 0..1, обновляется из AudioEngine.get_level()

        # ── Компактный режим (Milkdrop нуждается в месте под UI) ─────────
        self._compact = False

        # ── Графичный режим (тема Fluid): halftone-диск вместо реалистичного
        #    винила — точечная «гравюра» с радиальными лучами, как на референсе
        self._graphic = False
        self._graphic_dark = False
        self._graphic_pm = QPixmap()
        self._graphic_size = 0
        # конструктор тем: размер диска и тонарм
        self._disc_scale = 1.0
        self._show_tonearm = True
        # конструктор тем: фон с обложкой за диском (свечение + круг из размытой обложки) —
        # сдвиг в долях виджета, размер и показ
        self._bg_dx = self._bg_dy = 0.0
        self._bg_scale = 1.0
        self._bg_on = True
        self._cover_label_pm = QPixmap()
        self._cover_label_key = 0
        self._cover_label_d = 0

        # ── Статичная текстура царапин/зерна — генерируется один раз и
        # переиспользуется, чтобы не считать шум каждый кадр ──────────────
        self._scratches = self._build_scratch_texture()

        # кэши «запечённых» слоёв реалистичного диска (см. _real_layers)
        self._real_key = None
        self._real_static = self._real_spin = self._real_sheen = QPixmap()
        self._beat = 0.0           # 0..1 — вспышка на сильной доле (бас/атака)
        self._level_slow = 0.0

        # PreciseTimer: обычный таймер Windows тикает по ~15.6 мс, и кадры
        # шли неровно (15/31 мс) — диск заметно «дёргался»
        self.timer = FrameTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self.timer.setInterval(16)

    def _build_scratch_texture(self):
        """Набор случайных (но фиксированных на время жизни виджета)
        тонких царапин по поверхности пластинки — угол, длина, яркость."""
        import random
        rng = random.Random(1234)
        scratches = []
        for _ in range(22):
            angle = rng.uniform(0, 360)
            r0 = rng.uniform(0.25, 0.55)
            r1 = r0 + rng.uniform(0.08, 0.4)
            alpha = rng.uniform(6, 22)
            scratches.append((angle, r0, r1, alpha))
        return scratches

    # ── публичные методы ──────────────────────────────────────────────────

    def set_cover(self, path_or_pixmap):
        if isinstance(path_or_pixmap, QPixmap):
            self.cover = path_or_pixmap
        elif path_or_pixmap:
            pm = load_pixmap(str(path_or_pixmap), 1600)
            self.cover = pm if not pm.isNull() else QPixmap()
        else:
            self.cover = QPixmap()

        self._blur_tiny = QPixmap()
        self._blur_mid  = QPixmap()
        self._glow_color = QColor(120, 100, 200)
        self._cover_label_pm = QPixmap()
        self._cover_label_key = 0
        self._cover_label_d = 0

        if not self.cover.isNull():
            # настоящее гауссово размытие (раньше 8×8/24×24 растягивались обратно — ромбы и ступеньки)
            import blur_fx
            self._blur_tiny = QPixmap.fromImage(blur_fx.blurred_cover(self.cover, 256, 256, 18.0))
            self._blur_mid = QPixmap.fromImage(blur_fx.blurred_cover(self.cover, 256, 256, 6.5))

            # Доминирующий цвет из 1×1 пикселя
            c1 = self.cover.scaled(1, 1,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            img = c1.toImage()
            self._glow_color = QColor(img.pixel(0, 0))

        self.update()

    def set_playing(self, playing):
        self.playing = bool(playing)
        self._target_tonearm = 1.0 if self.playing else 0.0

    def set_engine(self, engine):
        """Подключить AudioEngine — источник RMS-уровня для реактивной
        подсветки диска/иглы (см. _tick)."""
        self._engine = engine

    def set_compact(self, compact: bool):
        """Milkdrop-визуализатору нужно место под UI — в компактном режиме
        пластинка ограничивается в размере, а не растягивается на всё
        доступное пространство."""
        compact = bool(compact)
        if compact == self._compact:
            return
        self._compact = compact
        if compact:
            self.setMaximumSize(230, 230)
        else:
            self.setMaximumSize(16777215, 16777215)  # QWIDGETSIZE_MAX — снять ограничение
        self.updateGeometry()

    def set_ambient_override(self, color):
        """Принудительно задать цвет ambient-фона позади винила вместо
        цвета, извлечённого из обложки. Передайте None, чтобы вернуть
        обычное поведение (цвет из обложки)."""
        new = QColor(color) if color is not None else None
        if new == self._ambient_override:
            return
        self._ambient_override = new
        self.update()

    def _ambient_color(self) -> QColor:
        """Эффективный "цвет обложки" для ambient-эффектов — override,
        если он задан (например, для светлых тем), иначе реальный цвет
        обложки."""
        return self._ambient_override if self._ambient_override is not None else self._glow_color

    def set_graphic(self, on: bool, dark: bool = False):
        """Графичный halftone-диск (тема Fluid) вместо реалистичного винила.
        dark=True — инверсия под чёрный вариант темы."""
        on, dark = bool(on), bool(dark)
        if on == self._graphic and dark == self._graphic_dark:
            return
        self._graphic, self._graphic_dark = on, dark
        self._graphic_pm = QPixmap()
        self._graphic_size = 0
        self.update()

    # ── Скретч: мышиные события ───────────────────────────────────────────

    def set_disc_scale(self, k: float):
        """Размер диска относительно места (конструктор тем): 0.4…1.3."""
        k = max(0.4, min(1.3, float(k)))
        if abs(k - self._disc_scale) > 1e-3:
            self._disc_scale = k
            self.update()

    def set_tonearm(self, on: bool):
        if bool(on) != self._show_tonearm:
            self._show_tonearm = bool(on)
            self.update()

    def set_bg_layout(self, dx=0.0, dy=0.0, scale=1.0, on=True):
        """Фон с обложкой за диском: сдвиг (доли ширины/высоты виджета), размер (0.3…3), показывать ли."""
        v = (float(dx), float(dy), max(0.3, min(3.0, float(scale))), bool(on))
        if v != (self._bg_dx, self._bg_dy, self._bg_scale, self._bg_on):
            self._bg_dx, self._bg_dy, self._bg_scale, self._bg_on = v
            self._bg_key = None
            self.update()

    def bg_rect(self):
        """Где фон с обложкой (координаты виджета) — для расстановки в конструкторе тем."""
        from PyQt6.QtCore import QRectF
        w, h = self.width(), self.height()
        rr = self._disc_radius() * 1.35 * self._bg_scale
        cx, cy = w * (0.5 + self._bg_dx), h * (0.5 + self._bg_dy)
        return QRectF(cx - rr, cy - rr, rr * 2, rr * 2)

    def _disc_radius(self) -> float:
        pad = 34.0 if self._graphic else 26.0
        base = max(80.0, min(self.width(), self.height()) / 2.0 - pad)
        if self._disc_scale == 1.0:
            return base
        return max(36.0, min(base * self._disc_scale, min(self.width(), self.height()) / 2.0 - 6.0))

    def _cursor_angle(self, x: float, y: float) -> float:
        """Угол курсора относительно центра диска в градусах [0..360)."""
        cx, cy = self.width() / 2.0, self.height() / 2.0
        return math.degrees(math.atan2(y - cy, x - cx)) % 360.0

    def _is_on_disc(self, x: float, y: float) -> bool:
        cx, cy = self.width() / 2.0, self.height() / 2.0
        r = self._disc_radius()
        return (x - cx) ** 2 + (y - cy) ** 2 <= r * r

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position()
            if self._is_on_disc(pos.x(), pos.y()):
                self._scratching    = True
                self._scratch_speed = 0.0
                self._scratch_momentum = 0.0
                self._scratch_angle = self._cursor_angle(pos.x(), pos.y())
                self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
                self.scratch_started.emit()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.position()
        if not self._scratching:
            # Показываем «указатель руки» при наведении на диск
            if self._is_on_disc(pos.x(), pos.y()):
                self.setCursor(QCursor(Qt.CursorShape.OpenHandCursor))
            else:
                self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
            super().mouseMoveEvent(event)
            return

        new_angle = self._cursor_angle(pos.x(), pos.y())
        # Разница углов с учётом перехода через 0/360
        delta = new_angle - self._scratch_angle
        if delta > 180:
            delta -= 360
        elif delta < -180:
            delta += 360

        self._scratch_angle = new_angle

        # Мгновенная угловая скорость для анимации (°/с при 60fps ≈ delta*60)
        # Используем сглаженное значение, чтобы не было дёрганья
        instant_speed = delta * 62.5   # 1000ms / 16ms tick
        self._scratch_speed = self._scratch_speed * 0.4 + instant_speed * 0.6

        # Отправляем delta в секундах в главное окно → AudioEngine.seek
        if abs(delta) > 0.05:
            self.scratch_moved.emit(delta * self._scratch_sec_per_deg)

        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._scratching:
            self._scratching = False
            # Передаём инерцию: пластинка «доворачивается» после отпускания
            self._scratch_momentum = self._scratch_speed
            self._scratch_speed    = 0.0
            pos = event.position()
            if self._is_on_disc(pos.x(), pos.y()):
                self.setCursor(QCursor(Qt.CursorShape.OpenHandCursor))
            else:
                self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
            self.scratch_released.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        if not self._scratching:
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        super().leaveEvent(event)

    # ── внутренние методы ─────────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        self._last = __import__('time').monotonic()
        self.timer.start(16)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def _tick(self):
        now = __import__("time").monotonic()
        dt  = animation_delta(now - self._last, .05)
        self._last = now

        if self._scratching:
            # Во время скретча — диск вращается со скоростью, которую задаёт мышь.
            # spin_speed обновляется в mouseMoveEvent; здесь просто крутим угол.
            self.angle = (self.angle + self._scratch_speed * dt) % 360.0
            # Постепенно затухаем scratch_speed к нулю (трение), если мышь не двигается
            self._scratch_speed *= math.exp(-dt * 8.0)
        else:
            # Инерция после скретча: momentum → плавно тормозит до нормального вращения
            if abs(self._scratch_momentum) > 0.5:
                self._scratch_momentum *= math.exp(-dt * 4.0)
                self.angle = (self.angle + self._scratch_momentum * dt) % 360.0
            else:
                self._scratch_momentum = 0.0
                # Плавный разгон/торможение пластинки вместо мгновенного старта.
                target_speed = 205.0 if self.playing else 0.0
                response = 3.4 if target_speed > self.spin_speed else 1.8
                self.spin_speed += (target_speed - self.spin_speed) * (1.0 - math.exp(-response * dt))
                self.angle = (self.angle + self.spin_speed * dt) % 360.0

        self.bg_time += dt
        # сглаживание по времени, а не «за кадр» — одинаково при любом FPS
        self.tonearm_pos += (self._target_tonearm - self.tonearm_pos) * (1.0 - math.exp(-dt * 6.0))

        # Реактивная подсветка: тянем RMS из движка только пока играет,
        # иначе плавно гасим до нуля — без резких скачков подсветки.
        target_level = 0.0
        if self.playing and self._engine is not None:
            try:
                target_level = min(1.0, self._engine.get_level() * 4.0)
            except Exception:
                target_level = 0.0
        k_up, k_down = 1.0 - math.exp(-dt * 26.0), 1.0 - math.exp(-dt * 5.0)
        self._audio_level += (target_level - self._audio_level) * (k_up if target_level > self._audio_level else k_down)
        # бит: резкий всплеск громкости над её медленным средним
        self._level_slow += (target_level - self._level_slow) * (1.0 - math.exp(-dt * 1.6))
        onset = max(0.0, target_level - self._level_slow * 1.25)
        if onset > 0.06 and onset * 2.2 > self._beat:
            self._beat = min(1.0, onset * 2.2)
        self._beat *= math.exp(-dt * 7.0)

        # Графичный диск (Fluid) не зависит от времени, пока стоит на месте:
        # на паузе не перерисовываемся 60 раз в секунду впустую — это же
        # избавляет сцену под диском от лишних перерисовок постера.
        if self._graphic and not self._needs_frame():
            return
        self.update()

    def _needs_frame(self) -> bool:
        return (self.playing or self._scratching
                or abs(self._scratch_momentum) > 0.5
                or abs(self.spin_speed) > 0.05
                or abs(self._target_tonearm - self.tonearm_pos) > 0.002
                or self._audio_level > 0.003)

    def _bg_layers(self, w: int, h: int, disc_r: float):
        """Фон за диском, «запечённый» в пиксмапы: (свечение, круг с размытой
        обложкой, верхний слой: тень+блик). Строятся раз на размер/обложку/тему."""
        dpr = max(1.0, float(self.devicePixelRatioF()))
        gc = self._ambient_color()
        key = (w, h, int(disc_r), dpr, gc.rgb(), self._ambient_override is not None,
               self._blur_mid.cacheKey(), self.playing, self._bg_scale, self._bg_dx, self._bg_dy)
        if key == getattr(self, "_bg_key", None):
            return self._bg_cache
        ring_r = disc_r * 1.35 * self._bg_scale
        max_allowed_r = min(w, h) * 0.5 - 10.0
        if self._bg_scale != 1.0 or self._bg_dx or self._bg_dy:
            max_allowed_r = max(w, h) * 1.5               # свой размер/сдвиг из конструктора — без ограничения
        halo_r = min(ring_r * 1.25, max_allowed_r)
        ring_r = min(ring_r, halo_r)

        def canvas(rad):
            S = int(rad * 2) + 4
            pm = QPixmap(int(S * dpr), int(S * dpr))
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.GlobalColor.transparent)
            q = QPainter(pm)
            q.setRenderHint(QPainter.RenderHint.Antialiasing)
            q.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            q.translate(S / 2.0, S / 2.0)
            q.setPen(Qt.PenStyle.NoPen)
            return pm, q

        # свечение (альфа «на максимум» — живую яркость задаёт opacity при рисовании)
        glow_pm, q = canvas(halo_r)
        g = QRadialGradient(0, 0, halo_r)
        g.setColorAt(0.00, QColor(gc.red(), gc.green(), gc.blue(), 255))
        g.setColorAt(0.60, QColor(gc.red(), gc.green(), gc.blue(), 102))
        g.setColorAt(0.90, QColor(gc.red(), gc.green(), gc.blue(), 13))
        g.setColorAt(1.00, QColor(0, 0, 0, 0))
        q.setBrush(QBrush(g))
        q.drawEllipse(QPointF(0, 0), halo_r, halo_r)
        q.end()

        # круг с размытой обложкой (или тёмный диск для тем без цветного пятна)
        cover_pm = None
        if self._ambient_override is not None or not self._blur_mid.isNull():
            cover_pm, q = canvas(ring_r)
            if self._ambient_override is not None:
                solid = QRadialGradient(0, 0, ring_r)
                solid.setColorAt(0.0, QColor(0, 0, 0, 235))
                solid.setColorAt(0.85, QColor(0, 0, 0, 205))
                solid.setColorAt(1.0, QColor(0, 0, 0, 0))
                q.setBrush(QBrush(solid))
                q.drawEllipse(QPointF(0, 0), ring_r, ring_r)
            else:
                clip = QPainterPath(); clip.addEllipse(QPointF(0, 0), ring_r, ring_r)
                q.save(); q.setClipPath(clip)
                d = int(ring_r * 2.2 * dpr)
                st = self._blur_mid.scaled(d, d, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                           Qt.TransformationMode.SmoothTransformation)
                st.setDevicePixelRatio(dpr)
                q.drawPixmap(QPointF(-st.width() / (2 * dpr), -st.height() / (2 * dpr)), st)
                q.restore()
                shade = QRadialGradient(0, 0, ring_r)
                shade.setColorAt(0.0, QColor(0, 0, 0, 30))
                shade.setColorAt(0.7, QColor(0, 0, 0, 60))
                shade.setColorAt(1.0, QColor(0, 0, 0, 140))
                q.setBrush(QBrush(shade))
                q.drawEllipse(QPointF(0, 0), ring_r, ring_r)
                q.setPen(QPen(QColor(255, 255, 255, 35), 1.2))
                q.setBrush(Qt.BrushStyle.NoBrush)
                q.drawEllipse(QPointF(0, 0), ring_r - 0.6, ring_r - 0.6)
            q.end()

        # верхний слой: блик над кругом + тень и подсветка под диском
        over_pm, q = canvas(ring_r)
        sa = 45 if self.playing else 30
        spec = QRadialGradient(-ring_r * 0.3, -ring_r * 0.55, ring_r * 0.8)
        spec.setColorAt(0.0, QColor(255, 255, 255, sa))
        spec.setColorAt(0.6, QColor(255, 255, 255, sa // 4))
        spec.setColorAt(1.0, QColor(255, 255, 255, 0))
        q.setBrush(QBrush(spec))
        q.drawEllipse(QPointF(0, 0), ring_r, ring_r)
        sh = QRadialGradient(0, disc_r * 0.05, disc_r * 1.08)
        sh.setColorAt(0.0, QColor(0, 0, 0, 90))
        sh.setColorAt(0.75, QColor(0, 0, 0, 40))
        sh.setColorAt(1.0, QColor(0, 0, 0, 0))
        q.setBrush(QBrush(sh))
        q.drawEllipse(QPointF(0, disc_r * 0.05), disc_r * 1.08, disc_r * 1.08)
        tint = QRadialGradient(0, 0, disc_r * 1.2)
        tint.setColorAt(0, QColor(gc.red(), gc.green(), gc.blue(), 55 if self.playing else 28))
        tint.setColorAt(1, QColor(0, 0, 0, 0))
        q.setBrush(QBrush(tint))
        q.drawEllipse(QPointF(0, 0), disc_r * 1.2, disc_r * 1.2)
        q.end()

        self._bg_key = key
        self._bg_cache = (glow_pm, cover_pm, over_pm)
        return self._bg_cache

    @staticmethod
    def _blit_centered(p, pm, x, y):
        half = pm.width() / (2.0 * pm.devicePixelRatio())
        p.drawPixmap(QPointF(x - half, y - half), pm)

    def _draw_background(self, p: QPainter, w: int, h: int, disc_r: float):
        """Обложка позади винила — выступает, но гарантированно уходит в 0 до краев виджета."""
        cx, cy = w * 0.5, h * 0.5
        glow_pm, cover_pm, over_pm = self._bg_layers(w, h, disc_r)
        drift_x = math.sin(self.bg_time * 0.33) * w * 0.008
        drift_y = math.cos(self.bg_time * 0.27) * h * 0.006
        rot = math.sin(self.bg_time * 0.12) * 5.0
        if self._bg_on:
            bx, by = cx + w * self._bg_dx, cy + h * self._bg_dy    # фон можно сдвинуть от диска
            # «дыхание» — яркостью, а не масштабом: масштабировать большой слой дорого
            breath = 0.5 + 0.5 * math.sin(self.bg_time * 0.7)
            glow_a = (120 if self.playing else 80) + self._audio_level * 90 + self._beat * 40 + breath * 10
            p.setOpacity(min(1.0, glow_a / 255.0))
            self._blit_centered(p, glow_pm, bx + drift_x, by + drift_y)
            if cover_pm is not None:
                p.setOpacity(0.95 if self.playing else 0.85)
                p.save()
                p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                p.translate(bx + drift_x, by + drift_y)
                p.rotate(rot)
                self._blit_centered(p, cover_pm, 0, 0)
                p.restore()
        p.setOpacity(1.0)
        self._blit_centered(p, over_pm, cx, cy)

    def paintEvent(self, _):
        if self._graphic:
            self._paint_graphic()
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        r = self._disc_radius()
        self._draw_background(p, w, h, r)

        lift = 1.0 + 0.02 * self.tonearm_pos  # чуть "приподнимается" при игре

        # ── Скретч: пульсирующий обод при зажатой мыши ──────────────────
        if self._scratching:
            pulse = 0.5 + 0.5 * math.sin(self.bg_time * 18.0)
            scratch_glow = QRadialGradient(cx, cy, r * 1.05)
            scratch_glow.setColorAt(0.82, QColor(255, 255, 255, 0))
            scratch_glow.setColorAt(0.92, QColor(255, 220, 100, int(60 * pulse)))
            scratch_glow.setColorAt(0.98, QColor(255, 180, 50, int(120 * pulse)))
            scratch_glow.setColorAt(1.00, QColor(255, 160, 0, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(scratch_glow))
            p.drawEllipse(QPointF(cx, cy), r * 1.05, r * 1.05)

        # ── пульс-кольцо от бита (одна заливка градиентом) ──
        if self._beat > 0.02:
            gc = self._ambient_color()
            ring_r = r * (1.03 + 0.07 * self._beat)
            ring = QRadialGradient(cx, cy, ring_r)
            ring.setColorAt(0.86, QColor(gc.red(), gc.green(), gc.blue(), 0))
            ring.setColorAt(0.95, QColor(gc.red(), gc.green(), gc.blue(), int(150 * self._beat)))
            ring.setColorAt(1.0, QColor(gc.red(), gc.green(), gc.blue(), 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(ring))
            p.drawEllipse(QPointF(cx, cy), ring_r, ring_r)

        # ── сам диск: два готовых слоя вместо ~70 сглаженных окружностей за кадр ──
        static, spin, sheen = self._real_layers(r)
        half = static.width() / (2.0 * static.devicePixelRatio())
        p.save(); p.translate(cx, cy); p.scale(lift, lift)
        p.drawPixmap(QPointF(-half, -half), static)
        # блик не вращается (как у настоящей пластинки под лампой), а разгорается от звука
        p.setOpacity(min(1.0, 0.55 + self._audio_level * 0.6 + self._beat * 0.35))
        p.drawPixmap(QPointF(-half, -half), sheen)
        p.setOpacity(1.0)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.rotate(self.angle)
        hs = spin.width() / (2.0 * spin.devicePixelRatio())
        p.drawPixmap(QPointF(-hs, -hs), spin)
        p.restore()
        if self._show_tonearm:
            self._draw_tonearm(p, cx, cy, r)

    # ── графичный halftone-диск (тема Fluid) ────────────────────────────────

    def _build_graphic_pm(self, size: int) -> QPixmap:
        """
        Стилизованный диск в технике halftone, как на референсе: бумага +
        концентрические кольца точек, чей размер несёт радиальные лучи-блики
        (как у CD), тёмная ступица с белым отверстием и тонкий ободок.
        Пиксельная маска строится один раз на размер и крутится бесплатно.
        """
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)

        paper = QColor(246, 244, 238) if not self._graphic_dark else QColor(10, 10, 12)
        ink = QColor(17, 17, 17) if not self._graphic_dark else QColor(232, 230, 222)
        c = size / 2.0
        R = size / 2.0 - 2.0

        p.setBrush(paper)
        p.drawEllipse(QPointF(c, c), R, R)

        path = QPainterPath()
        path.addEllipse(QPointF(c, c), R, R)
        p.setClipPath(path)
        p.setBrush(ink)

        s = max(4.0, size / 84.0)          # шаг точечной решётки
        r = s * 0.9
        while r < R * 0.985:
            n = max(10, int(2 * math.pi * r / s))
            rr = r / R
            for j in range(n):
                th = 2 * math.pi * j / n
                # широкие радиальные лучи-блики + мелкая «каркасная» волна
                b = 0.55 + 0.45 * math.sin(th * 3.0 + 1.1 * math.sin(th + 0.6))
                b = 0.62 * b + 0.38 * (0.5 + 0.5 * math.sin(th * 7.0 + rr * 4.0))
                b = 0.85 * b + 0.15 * (1.0 - rr)
                dot = (1.0 - b) * s * 0.60
                if dot > 0.45:
                    x = c + r * math.cos(th)
                    y = c + r * math.sin(th)
                    p.drawEllipse(QPointF(x, y), dot, dot)
            r += s * 0.9

        # ступица: тёмное кольцо + бумажное отверстие + тонкая обводка
        p.setClipping(False)
        hub = QColor(ink)
        hub.setAlpha(235)
        p.setBrush(hub)
        p.drawEllipse(QPointF(c, c), R * 0.335, R * 0.335)
        p.setBrush(paper)
        p.drawEllipse(QPointF(c, c), R * 0.145, R * 0.145)
        p.setPen(QPen(ink, max(1.0, size * 0.012)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(c, c), R * 0.175, R * 0.175)
        p.drawEllipse(QPointF(c, c), R - 0.6, R - 0.6)
        p.end()
        return pm

    def _graphic_colors(self):
        paper = QColor(246, 244, 238) if not self._graphic_dark else QColor(10, 10, 12)
        ink = QColor(17, 17, 17) if not self._graphic_dark else QColor(232, 230, 222)
        return paper, ink

    def _build_static_layer(self, r: float) -> QPixmap:
        """Всё, что НЕ вращается: плотная тень + кайма-«стикер» вокруг диска
        (кремовое кольцо с тёмным контуром). Кайма отделяет диск от любого
        цвета жидкости — и от светлого, и от тёмного. Строится один раз на размер."""
        paper, ink = self._graphic_colors()
        dpr = max(1.0, float(self.devicePixelRatioF()))
        S = int(r * 2.5) + 8
        pm = QPixmap(int(S * dpr), int(S * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = S / 2.0
        rim = max(5.0, r * 0.05)
        # тень: смещённая вниз, плотнее у края диска
        sh_a = 120 if not self._graphic_dark else 170
        g = QRadialGradient(c, c + r * 0.07, r + rim + r * 0.16)
        g.setColorAt(0.0, QColor(0, 0, 0, sh_a))
        g.setColorAt(0.80, QColor(0, 0, 0, int(sh_a * 0.75)))
        g.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(QPointF(c, c + r * 0.07), r + rim + r * 0.16, r + rim + r * 0.16)
        # кайма: контур цвета «чернил» + бумажное кольцо
        p.setBrush(ink)
        p.drawEllipse(QPointF(c, c), r + rim + 2.0, r + rim + 2.0)
        p.setBrush(paper)
        p.drawEllipse(QPointF(c, c), r + rim, r + rim)
        p.setBrush(ink)
        p.drawEllipse(QPointF(c, c), r + 1.2, r + 1.2)
        p.end()
        return pm

    def _build_disc_with_label(self, size: int) -> QPixmap:
        """Вращающаяся часть: halftone-диск + этикетка с обложкой, «запечённые»
        в один пиксмап — кадр = один поворот одного изображения."""
        if self._graphic_size != size or self._graphic_pm.isNull():
            self._graphic_pm = self._build_graphic_pm(size)      # тяжёлый — только на смену размера
            self._graphic_size = size
        if self.cover.isNull():
            return self._graphic_pm
        pm = self._graphic_pm.copy()
        paper, ink = self._graphic_colors()
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = size / 2.0
        R = size / 2.0 - 2.0
        rl = R * 0.335
        lab = self._cover_label(max(8, int(rl * 2.0)))
        if not lab.isNull():
            path = QPainterPath()
            path.addEllipse(QPointF(c, c), rl, rl)
            p.setClipPath(path)
            p.drawPixmap(int(c - rl), int(c - rl), lab)
            p.setClipping(False)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(paper)
            p.drawEllipse(QPointF(c, c), rl * 0.16, rl * 0.16)
            p.setPen(QPen(ink, max(1.0, size * 0.012)))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(c, c), rl, rl)
        p.end()
        return pm

    def set_fast_mode(self, on: bool):
        """Пока диск летит (анимация режима текста), его размер меняется
        каждый кадр. Перестраивать под каждый размер тяжёлый точечный
        рисунок — это десятки мс на кадр; вместо этого рисуем из уже готового
        кэша с масштабированием, а по прилёту — один раз перестраиваем."""
        on = bool(on)
        if on != getattr(self, "_fast", False):
            self._fast = on
            if not on:
                self.update()

    def _paint_graphic(self):
        p = QPainter(self)
        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        # чуть меньше диск — чтобы кайма и тень целиком помещались в виджет
        r = self._disc_radius()
        size = int(r * 2.0) + 4

        fast = getattr(self, "_fast", False) and getattr(self, "_disc_pm", None) is not None \
            and getattr(self, "_static_pm", None) is not None
        k = 1.0
        if fast:
            k = size / float(self._disc_key[0])          # масштаб относительно кэша
        else:
            key = (size, self._graphic_dark)
            if getattr(self, "_static_key", None) != key or getattr(self, "_static_pm", None) is None:
                self._static_pm = self._build_static_layer(r)
                self._static_key = key
            dkey = (size, self._graphic_dark, self.cover.cacheKey())
            if getattr(self, "_disc_key", None) != dkey or getattr(self, "_disc_pm", None) is None:
                self._disc_pm = self._build_disc_with_label(size)
                self._disc_key = dkey

        sp = self._static_pm
        sw = sp.width() / sp.devicePixelRatio()
        if fast:
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.save()
            p.translate(cx, cy)
            p.scale(k, k)
            p.drawPixmap(QPointF(-sw / 2.0, -sw / 2.0), sp)
            p.restore()
        else:
            p.drawPixmap(QPointF(cx - sw / 2.0, cy - sw / 2.0), sp)

        # скретч: пульсирующий обод при зажатой мыши
        if self._scratching:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            pulse = 0.5 + 0.5 * math.sin(self.bg_time * 18.0)
            ring = QRadialGradient(cx, cy, r * 1.12)
            ring.setColorAt(0.84, QColor(255, 255, 255, 0))
            ring.setColorAt(0.93, QColor(255, 220, 100, int(70 * pulse)))
            ring.setColorAt(0.99, QColor(255, 180, 50, int(140 * pulse)))
            ring.setColorAt(1.00, QColor(255, 160, 0, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(ring))
            p.drawEllipse(QPointF(cx, cy), r * 1.12, r * 1.12)

        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.translate(cx, cy)
        p.rotate(self.angle)
        if fast:
            p.scale(k, k)
            ds = self._disc_key[0]
            p.drawPixmap(-ds // 2, -ds // 2, self._disc_pm)
        else:
            p.drawPixmap(-size // 2, -size // 2, self._disc_pm)
        p.end()

    def _cover_label(self, d: int) -> QPixmap:
        """Квадратный кроп обложки d×d для этикетки (кэш на размер и обложку)."""
        key = self.cover.cacheKey()
        if (self._cover_label_pm.isNull()
                or self._cover_label_key != key
                or self._cover_label_d != d):
            pm = self.cover.scaled(d, d,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation)
            x = (pm.width() - d) // 2
            y = (pm.height() - d) // 2
            self._cover_label_pm = pm.copy(x, y, d, d)
            self._cover_label_key = key
            self._cover_label_d = d
        return self._cover_label_pm

    def _real_layers(self, r: float):
        """(неподвижный диск, вращающаяся этикетка+царапины, блик) — строятся
        один раз на размер/обложку, дальше кадр = 3 drawPixmap."""
        dpr = max(1.0, float(self.devicePixelRatioF()))
        key = (int(r), dpr, self.cover.cacheKey())
        if key == self._real_key:
            return self._real_static, self._real_spin, self._real_sheen
        S = int(r * 2) + 4
        c = S / 2.0

        def canvas(size):
            pm = QPixmap(int(size * dpr), int(size * dpr))
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.GlobalColor.transparent)
            q = QPainter(pm)
            q.setRenderHint(QPainter.RenderHint.Antialiasing)
            q.translate(size / 2.0, size / 2.0)
            return pm, q

        # неподвижный слой: диск, бортик, бороздки (вращение их не меняет)
        static, q = canvas(S)
        disc = QRadialGradient(0, 0, r)
        disc.setColorAt(0.0, QColor(38, 38, 43))
        disc.setColorAt(0.55, QColor(17, 17, 20))
        disc.setColorAt(0.92, QColor(8, 8, 10))
        disc.setColorAt(1.0, QColor(2, 2, 3))
        q.setBrush(QBrush(disc)); q.setPen(Qt.PenStyle.NoPen)
        q.drawEllipse(QPointF(0, 0), r, r)
        rim = QRadialGradient(0, 0, r)
        rim.setColorAt(0.965, QColor(0, 0, 0, 0))
        rim.setColorAt(0.985, QColor(255, 255, 255, 30))
        rim.setColorAt(1.0, QColor(0, 0, 0, 110))
        q.setBrush(QBrush(rim))
        q.drawEllipse(QPointF(0, 0), r, r)
        q.setBrush(Qt.BrushStyle.NoBrush)
        rr, i = r - 18, 0
        while rr > r * 0.46:
            # чуть разная яркость + «паузы между треками» — живее, чем ровный штрихкод
            gap = (i % 9 == 8)
            q.setPen(QPen(QColor(90, 90, 98, 14 if gap else (34 if i % 2 else 26)), 1.6 if gap else 1))
            q.drawEllipse(QPointF(0, 0), rr, rr)
            rr -= 7 if not gap else 10
            i += 1
        q.end()

        # блик: две мягкие дуги, неподвижные в мире
        sheen, q = canvas(S)
        hl = QConicalGradient(0, 0, 35)
        hl.setColorAt(0.0, QColor(255, 255, 255, 34))
        hl.setColorAt(0.12, QColor(255, 255, 255, 0))
        hl.setColorAt(0.5, QColor(255, 255, 255, 18))
        hl.setColorAt(0.6, QColor(255, 255, 255, 0))
        hl.setColorAt(0.88, QColor(255, 255, 255, 0))
        hl.setColorAt(1.0, QColor(255, 255, 255, 34))
        q.setBrush(QBrush(hl)); q.setPen(Qt.PenStyle.NoPen)
        q.drawEllipse(QPointF(0, 0), r * 0.985, r * 0.985)
        q.end()

        # вращающийся слой: царапины + этикетка с обложкой + шпиндель
        spin, q = canvas(S)
        for s_angle, r0, r1, alpha in self._scratches:
            rad = math.radians(s_angle)
            q.setPen(QPen(QColor(210, 210, 218, int(alpha)), 0.8))
            q.drawLine(QPointF(math.cos(rad) * r0 * r, math.sin(rad) * r0 * r),
                       QPointF(math.cos(rad) * r1 * r, math.sin(rad) * r1 * r))
        cr = r * 0.355
        clip = QPainterPath(); clip.addEllipse(QPointF(0, 0), cr, cr)
        q.save(); q.setClipPath(clip)
        if not self.cover.isNull():
            d = max(2, int(cr * 2.0 * dpr))
            scaled = self.cover.scaled(d, d, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                       Qt.TransformationMode.SmoothTransformation)
            scaled.setDevicePixelRatio(dpr)
            q.drawPixmap(QPointF(-scaled.width() / (2 * dpr), -scaled.height() / (2 * dpr)), scaled)
        else:
            fallback = QRadialGradient(0, 0, cr)
            fallback.setColorAt(0, QColor(70, 70, 78)); fallback.setColorAt(1, QColor(20, 20, 24))
            q.setBrush(QBrush(fallback)); q.setPen(Qt.PenStyle.NoPen)
            q.drawEllipse(QPointF(0, 0), cr, cr)
        q.restore()
        q.setBrush(Qt.BrushStyle.NoBrush); q.setPen(QPen(QColor(8, 8, 10, 220), 2))
        q.drawEllipse(QPointF(0, 0), cr, cr)
        q.setBrush(QColor(10, 10, 11)); q.setPen(Qt.PenStyle.NoPen)
        q.drawEllipse(QPointF(0, 0), 4.6, 4.6)
        q.end()

        self._real_key = key
        self._real_static, self._real_spin, self._real_sheen = static, spin, sheen
        return static, spin, sheen

    def _draw_tonearm(self, p, cx, cy, r):
        pivot = QPointF(cx + r * 0.92, cy - r * 0.92)
        rest_angle, play_angle = -115.0, -150.0
        # Лёгкое физическое дрожание иглы во время игры — амплитуда чуть
        # растёт вместе с громкостью звука (реактивность), но остаётся
        # почти незаметной в тишине.
        jitter = 0.0
        if self.playing:
            jitter = math.sin(self.bg_time * 14.0) * (0.35 + self._audio_level * 1.2)
        angle = rest_angle + (play_angle - rest_angle) * self.tonearm_pos + jitter

        p.save(); p.translate(pivot); p.rotate(angle)

        # Мягкая тень тонарма под ним (лёгкий сдвиг)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 45))
        p.drawEllipse(QPointF(1.2, 1.6), 17, 17)

        # Основание — гладкий металлик, без жёсткого контура
        base = QRadialGradient(-3, -3, 20)
        base.setColorAt(0.0, QColor(226, 226, 232))
        base.setColorAt(0.55, QColor(150, 150, 158))
        base.setColorAt(1.0, QColor(64, 64, 72))
        p.setBrush(QBrush(base)); p.setPen(QPen(QColor(20, 20, 24, 160), 1))
        p.drawEllipse(QPointF(0, 0), 16, 16)
        p.setBrush(QColor(60, 60, 68)); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(0, 0), 4.5, 4.5)

        # Тонкая штанга со скруглёнными краями вместо толстой линии
        shaft_len = r * 1.46
        shaft = QLinearGradient(0, -3, 0, 3)
        shaft.setColorAt(0.0, QColor(232, 232, 238))
        shaft.setColorAt(0.5, QColor(150, 150, 158))
        shaft.setColorAt(1.0, QColor(90, 90, 98))
        p.setBrush(QBrush(shaft)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(0, -1.6, -shaft_len, 3.2), 1.6, 1.6)

        # Головка-картридж — скруглённая, с лёгким акцентным бликом под цвет обложки
        head_x = -shaft_len
        head = QLinearGradient(head_x - 15, -8, head_x + 15, 8)
        head.setColorAt(0.0, QColor(224, 224, 230))
        head.setColorAt(1.0, QColor(58, 58, 66))
        p.setBrush(QBrush(head)); p.setPen(QPen(QColor(18, 18, 22, 180), 1))
        p.drawRoundedRect(QRectF(head_x - 16, -8, 32, 16), 5, 5)

        tip = QColor(self._ambient_color())
        tip.setAlpha(min(255, int(180 + self._audio_level * 75)))
        p.setBrush(tip); p.setPen(Qt.PenStyle.NoPen)
        tip_r = 2.6 + self._audio_level * 1.4
        p.drawEllipse(QPointF(head_x - 15, 0), tip_r, tip_r)

        p.restore()
