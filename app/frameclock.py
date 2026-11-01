# frameclock.py
"""
Общие «часы кадров» для всех анимаций плеера.

Раньше каждый эффект (пластинка, визуализатор, пыль, жидкость Fluid, MilkDrop,
«Моя волна», текст…) крутился на собственном QTimer. Таймеры срабатывали в
разные моменты, и каждый update() вызывал отдельную перерисовку окна: всё, что
лежит под прозрачным эффектом, перерисовывалось по нескольку раз за кадр.

Здесь один ритм кадров на всех (отдельный поток _Pacer: VSync через DwmFlush или
сон высокого разрешения), а FrameTimer — замена QTimer с тем же API: все анимации
обновляются в ОДНОЙ итерации цикла событий, и Qt сливает их update() в одну
перерисовку за кадр.

Частота кадров больше не прибита к 60:
  * «как у монитора» (по умолчанию) — часы идут с частотой обновления экрана,
    на котором окно (60/75/120/144/165/240 Гц…), — аналог VSync для отрисовки
    виджетов (у растрового Qt нет настоящего VSync, поэтому кадр подгоняется под
    период экрана);
  * фиксированный предел: 30, 60, 90, 120, 144, 165, 240 или «без ограничения»
    (до 1000 кадров/с — только для замеров).
Если кадры не успевают (тяжёлая тема, слабый ПК), регулятор временно снижает
частоту ступенями, но не ниже 60, и возвращает её, когда появляется запас —
процессор не уходит в 100 % ради кадров, которых всё равно не видно.

Все анимации считают движение по реальному времени (dt), поэтому при 144 Гц
они идут с той же скоростью, что и при 60, — только плавнее.
"""
from __future__ import annotations

import threading
import time
import weakref

from PyQt6.QtCore import QObject, Qt, pyqtSignal

FPS = 60.0                      # текущая частота часов (меняется set_rate / configure)
MIN_FPS = 30.0
MAX_FPS = 1000.0
FLOOR_FPS = 60.0                # ниже этого регулятор частоту не опускает
_PERIOD = 1.0 / FPS
EVERY_FRAME_MS = 17             # FrameTimer с интервалом ≤ 17 мс тикает каждый кадр часов

RATE_CHOICES = [(0, "Как у монитора (VSync)"), (30, "30"), (60, "60"), (90, "90"), (120, "120"),
                (144, "144"), (165, "165"), (240, "240"), (-1, "Без ограничения")]


def _win_timer_resolution(on: bool):
    """Windows: шаг системного таймера 1 мс (иначе ~15.6 мс — кадры 120+ Гц невозможны)."""
    try:
        import ctypes
        winmm = ctypes.WinDLL("winmm")
        (winmm.timeBeginPeriod if on else winmm.timeEndPeriod)(1)
        return True
    except Exception:                                      # noqa: BLE001  (не Windows)
        return False


class _Pacer(threading.Thread):
    """Отдельный поток отбивает ритм кадров и будит интерфейс (сигналом в его поток).

    Таймеры Qt на Windows идут с шагом системного таймера (~15.6 мс) — выше ~64 к/с с ними не
    подняться, даже с PreciseTimer. Здесь:
      * VSync (частота монитора) — ждём DwmFlush(): Windows отпускает поток ровно на кадровом
        гасящем импульсе композитора, кадры ложатся точно в обновление экрана;
      * фиксированная частота — time.sleep высокого разрешения (Python 3.11+ на Windows спит по
        таймеру высокого разрешения, точность ~0.5 мс).
    Следующий «тик» не посылается, пока интерфейс не обработал предыдущий: тяжёлый кадр не
    превращается в очередь из догоняющих кадров."""

    def __init__(self, clock):
        super().__init__(daemon=True, name="frame-pacer")
        self.clock = clock
        self.cond = threading.Condition()
        self.alive = True
        self.users = 0
        self.pending = False
        self._dwm = None
        self._dwm_bad = 0
        self._last_emit = 0.0

    def _dwm_flush(self) -> bool:
        if self._dwm is None:
            try:
                import ctypes
                self._dwm = ctypes.WinDLL("dwmapi").DwmFlush
            except Exception:                              # noqa: BLE001
                self._dwm = False
        if not self._dwm or self._dwm_bad > 30:
            return False
        t0 = time.perf_counter()
        try:
            ok = self._dwm() == 0
        except Exception:                                  # noqa: BLE001
            ok = False
        took = time.perf_counter() - t0
        # композиция выключена / нет экрана — DwmFlush возвращается сразу: тогда спим по таймеру
        if not ok or took < 0.0015:
            self._dwm_bad += 1
            return False
        self._dwm_bad = 0
        return True

    def run(self):
        nxt = time.perf_counter()
        while True:
            with self.cond:
                while self.alive and self.users <= 0:
                    self.cond.wait()
                    nxt = time.perf_counter()
                if not self.alive:
                    return
            c = self.clock
            per = _PERIOD
            if c.vsync and c.rate >= c.target - 0.5 and self._dwm_flush():
                nxt = time.perf_counter()                  # ритм задаёт экран
                if nxt - self._last_emit < per * 0.75:
                    continue                               # композитор быстрее цели (другой монитор) — пропуск
            else:
                nxt += per
                now = time.perf_counter()
                if nxt < now - per:                        # сильно отстали — не догоняем пачкой
                    nxt = now
                d = nxt - now
                if d > 0.0002:
                    time.sleep(d)
            with self.cond:
                if self.pending:
                    continue                               # интерфейс ещё рисует прошлый кадр
                self.pending = True
            self._last_emit = time.perf_counter()
            try:
                c._kick.emit()
            except RuntimeError:
                return


class _Clock(QObject):
    tick = pyqtSignal(float)
    rate_changed = pyqtSignal(float)
    _kick = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._timer = None
        self._pacer = _Pacer(self)
        self._kick.connect(self._fire, Qt.ConnectionType.QueuedConnection)
        self._pacer.start()
        self._users = 0
        self._next = 0.0
        self.target = 60.0                     # что просили (монитор / предел пользователя)
        self.rate = 60.0                       # с какой частотой идём сейчас (≤ target, регулятор)
        self.adaptive = True
        self.vsync = True
        self._hires = False
        # регулятор: доля «опоздавших» кадров
        self._late = 0.0
        self._acc = 0.0
        self._good = 0
        self._last_fire = 0.0
        self._recover_until = 0.0
        self.recoveries = 0
        self.measured = 0.0                    # реальная частота кадров (для статуса в настройках)
        self._m_n = 0
        self._m_t = time.perf_counter()

    # ── настройка ──
    def set_target(self, fps: float, vsync: bool | None = None, adaptive: bool | None = None):
        fps = max(MIN_FPS, min(MAX_FPS, float(fps or 60.0)))
        if vsync is not None:
            self.vsync = bool(vsync)
        if adaptive is not None:
            self.adaptive = bool(adaptive)
        self.target = fps
        self._late = self._acc = 0.0
        self._good = 0
        self._recover_until = 0.0
        self._last_fire = time.perf_counter()
        self._apply_rate(fps)
        want_hires = fps > 64.5
        if want_hires != self._hires:
            self._hires = want_hires and _win_timer_resolution(True)
            if not want_hires:
                _win_timer_resolution(False)

    def _apply_rate(self, fps):
        global FPS, _PERIOD
        fps = max(MIN_FPS, min(self.target, float(fps)))
        if abs(fps - self.rate) < 0.01 and abs(FPS - fps) < 0.01:
            return
        self.rate = fps
        FPS = fps
        _PERIOD = 1.0 / fps
        try:
            self.rate_changed.emit(fps)
        except RuntimeError:
            pass

    def period(self) -> float:
        return _PERIOD

    # ── жизненный цикл ──
    def acquire(self):
        self._users += 1
        if self._users == 1 and not self.paused:
            self._last_fire = time.perf_counter()
            with self._pacer.cond:
                self._pacer.users = 1
                self._pacer.cond.notify()

    def release(self):
        self._users = max(0, self._users - 1)
        if self._users == 0:
            with self._pacer.cond:
                self._pacer.users = 0

    paused = False

    def set_paused(self, on: bool):
        """Окно не в фокусе / свёрнуто — живые фоны и анимации стоят, кадры не идут."""
        self.paused = bool(on)
        self._last_fire = time.perf_counter()
        with self._pacer.cond:
            self._pacer.users = 0 if self.paused else (1 if self._users > 0 else 0)
            self._pacer.cond.notify()

    def _govern(self, now):
        """Кадры опаздывают больше чем на полпериода — частота ступенью ниже; запас — обратно вверх."""
        gap = now - self._last_fire
        self._last_fire = now
        self._m_n += 1
        if now - self._m_t >= 0.5:
            self.measured = self._m_n / (now - self._m_t)
            self._m_n = 0
            self._m_t = now
        if gap > max(.1, _PERIOD * 6):
            # Resume from a system/GUI stall without asking the renderer to
            # immediately sustain the previous high rate or catch up frames.
            self.recoveries += 1
            self._late = self._acc = 0.0
            self._good = 0
            self._recover_until = now + 1.0
            if self.adaptive:
                self._apply_rate(min(self.target, FLOOR_FPS))
            return
        if not self.adaptive or now < self._recover_until:
            return
        late = 1.0 if gap > _PERIOD * 1.5 else 0.0
        self._late += (late - self._late) * 0.05
        self._acc += gap
        if self._acc < 1.0:
            return
        self._acc = 0.0
        if self._late > 0.25 and self.rate > FLOOR_FPS + 0.5:
            self._apply_rate(max(FLOOR_FPS, self.rate * 0.8))
            self._late = 0.1
            self._good = 0
        elif self._late < 0.03 and self.rate < self.target - 0.5:
            self._good += 1
            if self._good >= 3:
                self._apply_rate(min(self.target, self.rate * 1.2))
                self._good = 0
        else:
            self._good = 0

    def _fire(self):
        try:
            now = time.perf_counter()
            if self._users > 0 and not self.paused:
                self._govern(now)
                try:
                    self.tick.emit(now)
                except RuntimeError:
                    return
        finally:
            with self._pacer.cond:                     # можно присылать следующий кадр
                self._pacer.pending = False

    def shutdown(self):
        with self._pacer.cond:
            self._pacer.alive = False
            self._pacer.cond.notify()
        if threading.current_thread() is not self._pacer:
            self._pacer.join(timeout=2)
        if self._hires:
            _win_timer_resolution(False)
            self._hires = False


_CLOCK = None


def clock() -> _Clock:
    global _CLOCK
    if _CLOCK is None:
        _CLOCK = _Clock()
    return _CLOCK


def monitor_hz(widget=None) -> float:
    """Частота обновления экрана, на котором окно (или главного экрана)."""
    try:
        scr = widget.screen() if widget is not None else None
        if scr is None:
            from PyQt6.QtGui import QGuiApplication
            scr = QGuiApplication.primaryScreen()
        hz = float(scr.refreshRate()) if scr is not None else 60.0
        return hz if 23.0 <= hz <= 1000.0 else 60.0
    except Exception:                                      # noqa: BLE001
        return 60.0


def configure(settings: dict | None, widget=None) -> float:
    """Применить настройки частоты: fps_limit (0 — как у монитора, -1 — без ограничения, иначе число),
    fps_adaptive. Возвращает выбранную частоту."""
    s = settings or {}
    try:
        lim = int(s.get("fps_limit", 0))
    except (TypeError, ValueError):
        lim = 0
    if lim == 0:
        fps, vsync = monitor_hz(widget), True
    elif lim < 0:
        fps, vsync = MAX_FPS, False
    else:
        # кадров больше, чем показывает монитор, всё равно не видно: предел 240 на экране 60/144 Гц
        # только гонял интерфейс на 100 % одного ядра (рывки, «низкий фпс» во всех темах)
        hz = monitor_hz(widget)
        fps, vsync = float(lim), False
        if fps >= hz - 0.5:
            fps, vsync = hz, True
    if lim == 0:
        ceiling = {"vivid": 144, "balanced": 90, "eco": 60, "reduced": 60}.get(s.get("motion_profile", "vivid"), 144)
        fps = min(fps, ceiling)
    clock().set_target(fps, vsync=vsync, adaptive=bool(s.get("fps_adaptive", True)))
    return fps


def set_paused(on: bool):
    clock().set_paused(on)


def set_rate(fps: float):
    clock().set_target(fps)


def rate() -> float:
    return FPS


def period() -> float:
    return _PERIOD


def animation_delta(elapsed: float, maximum: float = .1) -> float:
    """Visual simulation step: discard stalled wall time, preserve normal pacing.

    Audio position and gameplay clocks continue to use their real clocks.
    A stalled GUI cannot render its missing frames; replaying their movement
    afterwards only causes a jump or a second overload.
    """
    elapsed = max(0.0, float(elapsed))
    if elapsed > max(.1, _PERIOD * 6):
        return min(maximum, _PERIOD)
    return min(maximum, max(.001, elapsed))


def _release_if_active(st):
    # При выходе из программы часы уже могли быть удалены — новые не создаём.
    try:
        if st["active"]:
            st["active"] = False
            c = _CLOCK
            if c is not None:
                c.release()
    except Exception:                                      # noqa: BLE001
        pass


class FrameTimer(QObject):
    """Совместим с QTimer по используемому API (start/stop/setInterval/interval/
    isActive/setSingleShot/setTimerType/timeout), но тикает по общим часам.
    Интервал ≤ 17 мс означает «каждый кадр» (при 144 Гц — 144 раза в секунду),
    больший — не чаще, чем раз в интервал (33 → ~30 раз/с при любой частоте)."""

    timeout = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._interval = 16
        self._active = False
        self._single = False
        self._last = 0.0
        # виджет-родитель удалён без stop() — всё равно отпустить часы.
        # Не через сигнал destroyed: Python-слот, вызванный из деструктора C++ посреди вложенного цикла
        # событий (вопрос «сохранить?» при закрытии конструктора тем), ронял программу (access violation).
        # weakref.finalize срабатывает, когда удаляется сам объект Python, — без вызова слота из C++.
        st = self._st = {"active": False}
        weakref.finalize(self, _release_if_active, st)

    def setInterval(self, ms):
        self._interval = max(1, int(ms))

    def interval(self) -> int:
        return self._interval

    def setSingleShot(self, on):
        self._single = bool(on)

    def isSingleShot(self) -> bool:
        return self._single

    def setTimerType(self, *_):
        pass

    def isActive(self) -> bool:
        return self._active

    def start(self, ms=None):
        if ms is not None:
            self.setInterval(ms)
        self._last = time.perf_counter()
        if not self._active:
            self._active = True
            self._st["active"] = True
            c = clock()
            c.tick.connect(self._on_tick)
            c.acquire()

    def stop(self):
        if self._active:
            self._active = False
            self._st["active"] = False
            c = clock()
            try:
                c.tick.disconnect(self._on_tick)
            except (TypeError, RuntimeError):
                pass
            c.release()

    def _on_tick(self, now):
        if self._interval > EVERY_FRAME_MS:
            # допуск в полкадра: интервал 33 мс срабатывает ~30 раз/с при любой частоте часов
            if now - self._last + _PERIOD * 0.5 < self._interval / 1000.0:
                return
        self._last = now
        if self._single:
            self.stop()
        self.timeout.emit()
