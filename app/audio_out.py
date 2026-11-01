# audio_out.py
"""
Собственный аудиовывод ECHOES: сэмпл-точный gapless, мгновенная громкость,
мягкий лимитер.

Раньше каждая дека VLC сама открывала аудиоустройство, а gapless пытался
запустить следующую деку «за N мс до конца» предыдущей. N приходилось угадывать
(задержка старта VLC плавает 25–60 мс, время VLC обновляется ступеньками) —
на стыке оставались паузы в десятки миллисекунд.

Теперь деки VLC ничего не играют сами: через колбэки libvlc (amem) они отдают
готовый PCM (уже с эквалайзером и скоростью) сюда, в «деки» микшера, а один
непрерывный поток PortAudio (sounddevice) забирает из них звук:

  * обычный старт трека — по меткам времени (pts) VLC, поэтому позиция, текст и
    визуализация совпадают с тем, что слышно;
  * gapless — следующая дека «прицеплена» к текущей: когда у текущей кончились
    данные и VLC сообщил конец потока (drain), следующая продолжает С ТОГО ЖЕ
    СЭМПЛА в том же аудиоблоке. Таймеров, угадываний и зависимости от FPS нет;
  * громкость (в т.ч. кроссфейд и экстремальный буст) применяется здесь, на
    выходе, с плавной рампой — без задержки VLC; сверху мягкий лимитер, чтобы
    1000–10000 % звучали громко, а не трещали.

VLC выдаёт звук примерно на секунду раньше, чем его надо играть, — эта очередь
и есть запас для стыка. Насколько дека «отстаёт» от часов VLC (lag), меряется
на каждом блоке, и движок вычитает это из get_time() — позиция всегда честная.

Если sounddevice не установлен или устройства нет — create_mixer() вернёт None,
и движок работает по-старому (VLC выводит звук сам).
"""
from __future__ import annotations

import collections
import ctypes
import os
import threading
import time

import numpy as np

import vlc

SR = 48000
CH = 2
_US = 1_000_000.0


def _log(msg):
    try:
        print(f"[audio-out] {msg}")
    except Exception:                                    # noqa: BLE001
        pass


# ------------------------------------------------------------------ #
#  Дека: очередь PCM одной копии VLC                                  #
# ------------------------------------------------------------------ #

class Deck:
    """PCM одного плеера VLC. Колбэки VLC приходят из его потоков, render —
    из аудиопотока; всё общее — под self.lock."""

    def __init__(self, mixer: "Mixer", name: str):
        self.m = mixer
        self.name = name
        self.lock = threading.Lock()
        self.chunks: collections.deque = collections.deque()   # [pts_us, ndarray(n, 2), offset]
        self.frames = 0
        # idle — пусто; sync — ждёт своего pts; play — звучит; chained — ждёт
        # конца предыдущей деки (gapless); done — доиграла до конца потока
        self.state = "idle"
        self.eos = False
        self.paused = False
        self.amp = 1.0
        self.mute = False
        self.g = 0.0                 # текущее усиление (рампа к amp)
        self.lag_us = 0.0
        self.lag_valid = False
        self.done_evt = threading.Event()
        self.prev: Deck | None = None
        self.next: Deck | None = None
        self.gen = 0
        self.block = -1
        self._cbs = None
        # точная позиция: якорь (мс трека) + реально проигранные сэмплы
        self.anchor_ms = None
        self.played = 0
        self._expect_flush = 0.0                  # до какого момента flush — «наша» перемотка
        self._rt = (0.0, 0, False, 0.05, 480)     # (время рендера, played после, звучала ли, latency, n)
        # «пластинка»: плавное торможение/разгон прямо на выходе, по сэмплам (см. set_vari)
        self.vari = None
        self.vari_rate = 1.0

    # ── подключение к VLC ── #

    def attach(self, player):
        D = vlc.CallbackDecorators

        @D.AudioPlayCb
        def play(_op, samples, count, pts):
            if self.m.closed:
                return
            try:
                self._on_play(samples, count, pts)
            except Exception as e:                       # noqa: BLE001
                _log(f"{self.name} play: {e!r}")

        @D.AudioPauseCb
        def pause(_op, _pts):
            self.paused = True

        @D.AudioResumeCb
        def resume(_op, _pts):
            self.paused = False

        @D.AudioFlushCb
        def flush(_op, _pts):
            try:
                self._on_flush()
            except Exception as e:                       # noqa: BLE001
                _log(f"{self.name} flush: {e!r}")

        @D.AudioDrainCb
        def drain(_op):
            try:
                self._on_drain()
            except Exception as e:                       # noqa: BLE001
                _log(f"{self.name} drain: {e!r}")

        @D.AudioSetVolumeCb
        def setvol(_op, volume, mute):
            # Колбэк нужен, чтобы VLC НЕ применял громкость сам. Значения
            # игнорируем: при открытии вывода VLC присылает 1.0 и затёр бы уже
            # выставленный движком ноль — новый трек начинался бы со щелчка.
            # Громкость и mute деке задаёт только движок (amp/mute напрямую).
            pass

        self._cbs = (play, pause, resume, flush, drain, setvol)   # не дать GC собрать колбэки
        player.audio_set_callbacks(play, pause, resume, flush, drain, None)
        player.audio_set_volume_callback(setvol)
        player.audio_set_format("S16N", SR, CH)

    # ── колбэки VLC ── #

    def _on_play(self, samples, count, pts):
        if count <= 0:
            return
        raw = ctypes.string_at(samples, count * CH * 2)
        a = np.frombuffer(raw, dtype=np.int16).reshape(-1, CH).astype(np.float32)
        a *= 1.0 / 32768.0
        with self.lock:
            self.chunks.append([float(pts), a, 0])
            self.frames += a.shape[0]
            if self.state in ("idle", "done"):
                self.state = "chained" if self.prev is not None else "sync"
                self.done_evt.clear()
            elif self.state == "idle_play":
                self.done_evt.clear()

    def _on_flush(self):
        with self.lock:
            self.chunks.clear()
            self.frames = 0
            self.eos = False
            self.lag_valid = False
            self.played = 0
            if time.monotonic() < self._expect_flush:
                pass                                     # перемотка из движка (VLC шлёт flush 1–2 раза)
            else:
                self.anchor_ms = None                    # неизвестная перемотка — позиция по VLC
            if self.state == "play":
                self.state = "sync"                      # перемотка: заново по pts

    def _on_drain(self):
        """VLC отдал последний кусок. Ждём, пока он реально прозвучит, — иначе
        VLC объявит «трек кончился» примерно на секунду раньше, чем на самом деле."""
        with self.lock:
            self.eos = True
            gen = self.gen
            if self.state == "idle" and self.frames == 0:
                self.state = "done"
                self.done_evt.set()
                return
        while not self.done_evt.wait(0.05):
            if self.gen != gen or self.m.closed:
                return
            if not self.paused and not self.m.alive():
                return                                   # вывод умер — не держим VLC вечно

    # ── управление из движка ── #

    def set_vari(self, target: float, seconds: float):
        """Пластинка: скорость звучания плавно (smoothstep) идёт к target (0 — остановка, 1 — норма)
        за seconds. Торможение доигрывает уже готовый буфер, даже если VLC уже на паузе, —
        поэтому оно ровное и сразу слышно (раньше скорость меняла VLC, а его звук опережает
        вывод почти на секунду: эффект запаздывал и шёл рывками)."""
        with self.lock:
            r0 = self.vari["rate"] if self.vari is not None else self.vari_rate
            if target > 0.5 and self.vari is None and self.vari_rate >= 0.999:
                r0 = 0.0                                 # обычный старт с паузы — разгон с нуля
            self.vari = {"r0": float(r0), "r1": float(target), "n": max(1, int(seconds * SR)), "t": 0,
                         "phase": 0.0, "rate": float(r0)}

    def vari_busy(self) -> bool:
        with self.lock:
            return self.vari is not None

    def _peek(self, need):
        """Первые need кадров очереди (не забирая их)."""
        parts, got = [], 0
        for c in self.chunks:
            a = c[1][c[2]:]
            parts.append(a)
            got += a.shape[0]
            if got >= need:
                break
        if not parts:
            return np.zeros((0, CH), np.float32)
        b = np.concatenate(parts) if len(parts) > 1 else parts[0]
        return b[:need]

    def _consume_vari(self, out, pos, n_total):
        V = self.vari
        m = n_total - pos
        k = (np.arange(m, dtype=np.float64) + V["t"]) / V["n"]
        np.clip(k, 0.0, 1.0, out=k)
        s = k * k * (3.0 - 2.0 * k)
        rate = V["r0"] + (V["r1"] - V["r0"]) * s
        pin = V["phase"] + np.concatenate(([0.0], np.cumsum(rate[:-1])))
        need = int(pin[-1]) + 2
        buf = self._peek(need)
        n_in = buf.shape[0]
        ok = pin < n_in - 1                              # где для сэмпла уже есть звук
        c = int(np.count_nonzero(ok)) if n_in >= 2 else 0  # pin растёт — годные идут подряд с начала
        if c > 0:
            p_ = pin[:c]
            i0 = np.minimum(p_.astype(np.int64), n_in - 2)
            fr = (p_ - i0).astype(np.float32)[:, None]
            y = buf[i0] * (1.0 - fr) + buf[i0 + 1] * fr
            target = 0.0 if self.mute else self.amp
            g = (np.clip(rate[:c] / 0.28, 0.0, 1.0) * target).astype(np.float32)   # в самом низу — затихание
            out[pos:pos + c] += y * g[:, None]
            self.g = target
        if c < m and V["r1"] < 0.5:
            # торможение доиграло весь готовый звук — пластинка остановилась
            whole = max(0, n_in - 1)
            self._drop(whole)
            self.played += whole
            self.vari_rate = V["r1"]
            self.vari = None
            return
        # разгон: пока VLC не прислал новый звук — разгон замирает (а не «проскакивает» в тишине
        # и не включается потом рывком на полной скорости)
        adv = float(pin[c] if c < m else pin[-1] + rate[-1])
        whole = max(0, min(int(adv), n_in - 1 if c < m else n_in))
        self._drop(whole)
        self.played += whole
        V["phase"] = adv - whole
        V["t"] += c
        if c:
            V["rate"] = float(rate[c - 1])
        if V["t"] >= V["n"]:
            self.vari_rate = V["r1"]
            self.vari = None

    def reset(self):
        """Очистить деку (новый трек / стоп). Сначала отпускает ждущий drain —
        иначе player.stop() VLC ждал бы его и интерфейс завис бы."""
        with self.lock:
            self.vari = None
            self.vari_rate = 1.0
            self.gen += 1
            self.chunks.clear()
            self.frames = 0
            self.eos = False
            self.state = "idle"
            self.paused = False
            self.lag_valid = False
            self.anchor_ms = None
            self.played = 0
            self._expect_flush = 0.0
            prev, nxt = self.prev, self.next
            self.prev = self.next = None
            self.done_evt.set()
        if prev is not None:
            with prev.lock:
                if prev.next is self:
                    prev.next = None
        if nxt is not None:
            with nxt.lock:
                if nxt.prev is self:
                    nxt.prev = None
                    if nxt.state == "chained":
                        nxt.state = "sync"

    def set_anchor(self, ms: float, expect_flush: bool = False):
        """Отсюда считается позиция: «трек на ms мс» — для следующего сэмпла."""
        with self.lock:
            self.anchor_ms = float(ms)
            self.played = 0
            self._expect_flush = (time.monotonic() + 1.5) if expect_flush else 0.0

    def heard_seconds(self):
        """Сколько секунд этой деки уже реально прозвучало от якоря (или None)."""
        with self.lock:
            if self.anchor_ms is None:
                return None
            t_r, played, sounding, lat, n = self._rt
            if self.paused or not sounding:
                return self.anchor_ms / 1000.0, played / SR
            v = played - SR * (lat + n / SR) + SR * (time.perf_counter() - t_r)
            return self.anchor_ms / 1000.0, max(0.0, min(float(played), v)) / SR

    def finished(self) -> bool:
        with self.lock:
            return self.state == "done"

    def lag_seconds(self) -> float:
        """Насколько звучащее отстаёт от часов VLC этой деки (с)."""
        with self.lock:
            return self.lag_us / _US if self.lag_valid else 0.0

    # ── аудиопоток ── #

    def _drop(self, n):
        while n > 0 and self.chunks:
            c = self.chunks[0]
            left = c[1].shape[0] - c[2]
            if left <= n:
                self.chunks.popleft()
                self.frames -= left
                n -= left
            else:
                c[2] += n
                self.frames -= n
                n = 0

    def _consume(self, out, pos, n_total, dac0, fresh_lag):
        """Дописать в out[pos:] сколько есть; вернуть новую позицию."""
        target = 0.0 if self.mute else self.amp
        ramp = None if abs(target - self.g) < 1e-6 else \
            np.linspace(self.g, target, n_total, endpoint=False, dtype=np.float32)
        first = True
        while pos < n_total and self.chunks:
            c = self.chunks[0]
            pts, arr, off = c
            take = min(n_total - pos, arr.shape[0] - off)
            if first:
                lag = (dac0 + pos * _US / SR) - (pts + off * _US / SR)
                if fresh_lag or not self.lag_valid:
                    self.lag_us, self.lag_valid = lag, True
                else:
                    self.lag_us += (lag - self.lag_us) * 0.05
                first = False
            seg = arr[off:off + take]
            if ramp is None:
                if target != 1.0:
                    out[pos:pos + take] += seg * target
                else:
                    out[pos:pos + take] += seg
            else:
                out[pos:pos + take] += seg * ramp[pos:pos + take, None]
            pos += take
            off += take
            self.frames -= take
            self.played += take
            if off >= arr.shape[0]:
                self.chunks.popleft()
            else:
                c[2] = off
        self.g = target
        return pos

    def render(self, out, n, dac0, blk, start=0, handoff=False, t_r=0.0, lat=0.05):
        """Смешать свой звук в out. Возвращает (следующая_дека, позиция_стыка)
        если на этом блоке дека доиграла и к ней прицеплена следующая."""
        with self.lock:
            played0 = self.played
            vari = self.vari is not None
            r = self._render_locked(out, n, dac0, blk, start, handoff)
            # во время торможения/разгона позицию не досчитываем по часам — скорость не 1×
            self._rt = (t_r, self.played, self.played != played0 and not vari, lat, n)
            return r

    def _render_locked(self, out, n, dac0, blk, start, handoff):
        if True:
            self.block = blk
            pos = start
            fresh = False
            if handoff:
                # стык: звучим сразу, без ожидания pts (даже если данные ещё
                # не пришли — тогда начнём, как только придут)
                self.prev = None
                self.g = 0.0 if self.mute else self.amp
                self.state = "play" if (self.frames or self.eos) else "idle_play"
                fresh = True
            if self.state == "idle_play":
                if not self.frames:
                    return None, 0
                self.state = "play"
                fresh = True
            if self.vari is not None and self.state == "play":
                # пластинка тормозит/разгоняется: играем буфер с плавной скоростью (даже если VLC на паузе)
                self._consume_vari(out, pos, n)
                return None, 0
            if self.paused or self.state in ("idle", "done", "chained"):
                return None, 0
            if self.state == "sync":
                if not self.chunks:
                    return None, 0
                c = self.chunks[0]
                p0 = c[0] + c[2] * _US / SR
                delta = p0 - dac0
                if delta >= n * _US / SR:
                    return None, 0                       # рано — ждём своего pts
                if delta > 0:
                    pos = int(delta * SR / _US)
                elif delta < -20000:                     # опоздали больше 20 мс — догоняем
                    self._drop(int(-delta * SR / _US))
                self.state = "play"
                fresh = True
            self.vari_rate = 1.0                         # возобновили без эффекта — обычная скорость
            pos = self._consume(out, pos, n, dac0, fresh)
            if self.frames == 0 and self.eos and pos <= n:
                self.state = "done"
                self.done_evt.set()
                nxt, self.next = self.next, None
                return nxt, pos
            return None, 0


# ------------------------------------------------------------------ #
#  Микшер                                                             #
# ------------------------------------------------------------------ #

def _soft_limit(x: np.ndarray):
    """Мягкое ограничение выше −1.4 дБ вместо жёсткого клиппинга."""
    k = 0.85
    a = np.abs(x)
    over = a > k
    if over.any():
        y = a[over] - k
        x[over] = np.sign(x[over]) * (k + (1.0 - k) * np.tanh(y / (1.0 - k)))
    return x


class Mixer:
    def __init__(self, backend_factory):
        self.decks: list[Deck] = []
        self.closed = False
        self.vis_cb = None                # callable(mono float32) — для визуализатора
        from listening_dsp import StereoProcessor
        self.listening = StereoProcessor()
        self._blk = 0
        self._last_render = 0.0
        self.latency_s = 0.05
        self._lock = threading.Lock()
        self.backend = backend_factory(self._render)
        self.latency_s = self.backend.latency_s

    def new_deck(self, name) -> Deck:
        d = Deck(self, name)
        with self._lock:
            self.decks.append(d)
        return d

    def chain(self, prev: Deck, nxt: Deck):
        """Gapless: nxt начнёт звучать ровно с сэмпла, на котором кончится prev."""
        with prev.lock:
            prev.next = nxt
        with nxt.lock:
            nxt.prev = prev
            if nxt.state in ("idle", "sync", "done"):
                nxt.state = "chained" if nxt.frames else "idle"
                nxt.done_evt.clear()

    def alive(self) -> bool:
        return (time.monotonic() - self._last_render) < 1.5

    def _render(self, n, dac_delay_s=None):
        self._last_render = time.monotonic()
        out = np.zeros((n, CH), dtype=np.float32)
        if self.closed:
            return out
        delay = self.latency_s if dac_delay_s is None or dac_delay_s <= 0 else dac_delay_s
        t_r = time.perf_counter()
        dac0 = float(vlc.libvlc_clock()) + delay * _US
        self._blk += 1
        blk = self._blk
        with self._lock:
            decks = list(self.decks)
        for d in decks:
            if d.block == blk:
                continue                                 # уже сыграла на этом блоке (стык)
            nxt, at = d.render(out, n, dac0, blk, t_r=t_r, lat=delay)
            hops = 0
            while nxt is not None and hops < 4:          # стык прямо внутри блока
                hops += 1
                cur = nxt
                nxt, at = cur.render(out, n, dac0, blk, start=at, handoff=True, t_r=t_r, lat=delay)
        self.listening.process(out)
        cb = self.vis_cb
        if cb is not None:
            try:
                cb(out.mean(axis=1))
            except Exception:                            # noqa: BLE001
                pass
        return _soft_limit(out)

    def close(self):
        self.closed = True
        with self._lock:
            decks = list(self.decks)
        for d in decks:
            d.done_evt.set()
        try:
            self.backend.close()
        except Exception:                                # noqa: BLE001
            pass


# ------------------------------------------------------------------ #
#  Бэкенды вывода                                                     #
# ------------------------------------------------------------------ #

class SoundDeviceBackend:
    """PortAudio через sounddevice. При ошибке устройства (выдернули наушники)
    сам переоткрывает поток на текущем устройстве по умолчанию."""

    BLOCK = 480                     # 10 мс
    LATENCY = 0.15                  # запас против подвисаний интерфейса (GIL): 100 мс не хватало

    def __init__(self, render):
        import sounddevice as sd
        self.sd = sd
        self.render = render
        self.closed = False
        self.stream = None
        self.latency_s = self.LATENCY
        self._open()

    def _open(self):
        sd = self.sd
        self.stream = sd.OutputStream(samplerate=SR, channels=CH, dtype="float32",
                                      blocksize=self.BLOCK, latency=self.LATENCY,
                                      callback=self._cb, finished_callback=self._finished)
        self.stream.start()
        try:
            self.latency_s = float(self.stream.latency)
        except Exception:                                # noqa: BLE001
            pass
        _log(f"output open: {SR} Hz, latency {self.latency_s * 1000:.0f} ms")

    def _cb(self, outdata, frames, time_info, status):
        dac = None
        try:
            d = float(time_info.outputBufferDacTime) - float(time_info.currentTime)
            if 0.0 < d < 1.0:
                dac = d
        except Exception:                                # noqa: BLE001
            pass
        outdata[:] = self.render(frames, dac)

    def _finished(self):
        if self.closed:
            return
        _log("output stream stopped — reopening")
        threading.Thread(target=self._reopen, daemon=True, name="audio-reopen").start()

    def _reopen(self):
        sd = self.sd
        delay = 0.5
        while not self.closed:
            time.sleep(delay)
            try:
                try:
                    sd._terminate()
                    sd._initialize()                     # обновить список устройств
                except Exception:                        # noqa: BLE001
                    pass
                self._open()
                return
            except Exception as e:                       # noqa: BLE001
                _log(f"reopen failed: {e!r}")
                delay = min(3.0, delay * 1.6)

    def close(self):
        self.closed = True
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:                                # noqa: BLE001
            pass


class NullBackend:
    """Без звуковой карты: забирает звук в реальном времени, как устройство
    (для тестов; ECHOES_AUDIO_BACKEND=null). record=True — копит всё сыгранное."""

    BLOCK = 480

    def __init__(self, render, record=False):
        self.render = render
        self.latency_s = 0.03
        self.closed = False
        self.record = record
        self.recorded: list = []
        self._t = threading.Thread(target=self._run, daemon=True, name="audio-null")
        self._t.start()

    def _run(self):
        period = self.BLOCK / SR
        nxt = time.perf_counter()
        while not self.closed:
            out = self.render(self.BLOCK, self.latency_s)
            if self.record:
                self.recorded.append(out.copy())
            nxt += period
            d = nxt - time.perf_counter()
            if d > 0:
                time.sleep(d)
            else:
                nxt = time.perf_counter()

    def close(self):
        self.closed = True


def create_mixer(prefer: str | None = None):
    """Mixer или None (тогда движок выводит звук через VLC, как раньше)."""
    prefer = (prefer or os.environ.get("ECHOES_AUDIO_BACKEND", "")).lower()
    if prefer in ("vlc", "off", "legacy"):
        return None
    if prefer.startswith("null"):
        return Mixer(lambda r: NullBackend(r, record=(prefer == "null-record")))
    if os.name == 'nt' and prefer != 'python':
        try:
            from audio_native import NativeMixer
            mixer = NativeMixer()
            _log(f"native output: {SR} Hz, latency {mixer.latency_s * 1000:.0f} ms; no Python audio callbacks")
            return mixer
        except Exception as e:
            _log(f"native output unavailable ({e!r}) — using Python output")
    try:
        import sounddevice as sd
        if sd.default.device[1] is None or sd.default.device[1] < 0:
            try:
                sd.query_devices(kind="output")
            except Exception:                            # noqa: BLE001
                _log("no output device — using VLC output")
                return None
        return Mixer(SoundDeviceBackend)
    except Exception as e:                               # noqa: BLE001
        _log(f"own output unavailable ({e.__class__.__name__}: {e}) — using VLC output")
        return None
