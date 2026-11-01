# daw_engine.py
"""
Звуковой движок студии (тема «Echoes Studio»).

Ноты и аудиоклипы заранее собраны в «аранжировку» (daw_project.Renderer) — в потоке звука
остаётся только сложить нужные куски, прогнать дорожки микшера через эффекты и выдать блок.
Громкость/панорама каналов и дорожек, mute/solo, эффекты — всё в реальном времени.

Компенсация задержки плагинов: дорожка с автотюном (задержка 2048) читается на 2048 сэмплов
раньше — на выходе всё совпадает по времени.

Свой поток вывода (sounddevice, 44.1 кГц, блок 1024). ECHOES_AUDIO_BACKEND=null — без
звуковой карты (тесты): поток идёт по часам, звук никуда не выводится.
Запись с микрофона — отдельный входной поток; задержка ввода/вывода вычитается.
"""
from __future__ import annotations

import math
import os
import threading
import time

import numpy as np

import daw_dsp as D
import daw_fx as FX
import daw_project as PR
from daw_dsp import SR

BLOCK = FX.BLOCK


def _log(msg):
    try:
        print("[studio audio]", msg)
    except Exception:                                    # noqa: BLE001
        pass


def _click(accent: bool) -> np.ndarray:
    n = int(0.03 * SR)
    t = np.arange(n) / SR
    f = 1760.0 if accent else 1175.0
    s = np.sin(2 * np.pi * f * t) * np.exp(-t / 0.008) * (0.55 if accent else 0.4)
    return np.stack([s, s], 1).astype(np.float32)


_CLICKS = None


def clicks():
    global _CLICKS
    if _CLICKS is None:
        _CLICKS = (_click(True), _click(False))
    return _CLICKS


class Mixer:
    """Ядро сведения: одно и то же для воспроизведения и для экспорта в файл."""

    def __init__(self, project: dict, ctx: FX.Ctx):
        self.p = project
        self.ctx = ctx
        self.arr = PR.Arrangement()
        self.chains: list = [[None] * 10 for _ in range(PR.N_INSERTS + 1)]
        self.slot_ids: list = [[None] * 10 for _ in range(PR.N_INSERTS + 1)]
        self.lat = [0] * (PR.N_INSERTS + 1)
        self.peaks = np.zeros((PR.N_INSERTS + 1, 2), np.float32)
        self.chan_peak: dict = {}
        self.idle = [0] * (PR.N_INSERTS + 1)
        self.voices: list = []                   # [дорожка, данные, позиция, усиление]
        self.vlock = threading.Lock()
        self.monitor = None                      # (дорожка, функция → блок микрофона)

    # ── эффекты ── #
    def sync_fx(self):
        """Сверить процессоры с ячейками эффектов проекта (вызывать из окна, не из потока звука)."""
        mix = self.p["mixer"]
        chains = [list(c) for c in self.chains]
        ids = [list(c) for c in self.slot_ids]
        while len(chains) < len(mix):
            chains.append([None] * 10)
            ids.append([None] * 10)
        for i, ins in enumerate(mix):
            for s in range(10):
                slot = ins["fx"][s] if s < len(ins["fx"]) else None
                if slot is None:
                    chains[i][s] = None
                    ids[i][s] = None
                    continue
                cur = chains[i][s]
                if ids[i][s] is slot and cur is not None and cur.TYPE == slot.get("type"):
                    if cur.p is not slot.get("params"):
                        cur.p = slot["params"]
                    continue
                fx = FX.make(slot, self.ctx)
                if fx is not None:
                    try:
                        fx.prepare()
                    except Exception as e:           # noqa: BLE001
                        _log(f"prepare {slot.get('type')}: {e!r}")
                chains[i][s] = fx
                ids[i][s] = slot
        lat = []
        for i, ins in enumerate(mix):
            L = 0
            for s in range(10):
                fx = chains[i][s]
                slot = ins["fx"][s] if s < len(ins["fx"]) else None
                if fx is not None and slot and slot.get("on", True):
                    L += fx.latency()
            lat.append(L)
        self.chains, self.slot_ids = chains, ids
        self.lat = lat

    def reset_fx(self):
        for ch in self.chains:
            for fx in ch:
                if fx is not None:
                    try:
                        fx.reset()
                    except Exception:                # noqa: BLE001
                        pass

    def fx_of(self, i, s):
        try:
            return self.chains[i][s]
        except IndexError:
            return None

    # ── превью нот / сэмплов ── #
    def preview(self, insert: int, data: np.ndarray, gain=1.0):
        if data is None:
            return
        with self.vlock:
            if len(self.voices) > 24:
                self.voices = self.voices[-16:]
            self.voices.append([int(insert), data, 0, float(gain)])

    def stop_previews(self):
        with self.vlock:
            self.voices = []

    # ── блок ── #
    def block(self, pos: int, n: int, playing: bool, loop=True) -> np.ndarray:
        p = self.p
        mix = p["mixer"]
        NI = len(mix)
        arr = self.arr
        L = arr.length if loop else 0
        chans = {c["id"]: c for c in p["channels"]}
        lat_m = self.lat[0] if self.lat else 0
        buses = [None] * NI
        cpk = {}
        if playing and arr.events:
            for e in arr.events:
                c = chans.get(e.ch)
                if c is None or c.get("mute"):
                    continue
                ins = int(c.get("insert", 0))
                if ins >= NI or ins < 0:
                    ins = 0
                r = pos + lat_m + (self.lat[ins] if ins else 0)
                for a0, m, boff in self._pieces(r, n, L):
                    a = max(a0, e.start)
                    b = min(a0 + m, e.end)
                    if a >= b:
                        continue
                    i0 = e.off + (a - e.start)
                    seg = e.data[i0:i0 + (b - a)]
                    if len(seg) < b - a:
                        b = a + len(seg)
                        if b <= a:
                            continue
                    if seg.dtype == np.int16:
                        seg = seg.astype(np.float32) * D.I16
                    vol = float(c.get("vol", 0.78)) / 0.78 * e.gain
                    pan = float(c.get("pan", 0.0))
                    g = np.array([vol * min(1.0, 1 - pan), vol * min(1.0, 1 + pan)], np.float32)
                    if e.fin > 0 and a - e.start < e.fin:
                        k = (np.arange(a, b) - e.start) / max(1, e.fin)
                        seg = seg * np.clip(k, 0, 1)[:, None]
                    if e.fout > 0 and e.end - b < e.fout:
                        k = (e.end - np.arange(a, b)) / max(1, e.fout)
                        seg = seg * np.clip(k, 0, 1)[:, None]
                    seg = seg * g
                    bus = buses[ins]
                    if bus is None:
                        bus = buses[ins] = np.zeros((n, 2), np.float32)
                    o = boff + (a - a0)
                    bus[o:o + (b - a)] += seg
                    pk = float(np.abs(seg).max()) if len(seg) else 0.0
                    if pk > cpk.get(e.ch, 0.0):
                        cpk[e.ch] = pk
        self.chan_peak = cpk
        with self.vlock:
            keep = []
            for v in self.voices:
                ins, data, vp, g = v
                if ins >= NI:
                    ins = 0
                m = min(n, len(data) - vp)
                if m > 0:
                    bus = buses[ins]
                    if bus is None:
                        bus = buses[ins] = np.zeros((n, 2), np.float32)
                    bus[:m] += data[vp:vp + m] * g
                    v[2] = vp + m
                if v[2] < len(data):
                    keep.append(v)
            self.voices = keep
        mon = self.monitor
        if mon is not None:
            try:
                ins, fn = mon
                x = fn(n)
                if x is not None:
                    bus = buses[ins]
                    if bus is None:
                        bus = buses[ins] = np.zeros((n, 2), np.float32)
                    bus += x
            except Exception:                            # noqa: BLE001
                pass
        solo = any(m.get("solo") for m in mix[1:])
        master = buses[0] if buses[0] is not None else np.zeros((n, 2), np.float32)
        spb = self.ctx.spb()
        for i in range(1, NI):
            ins = mix[i]
            x = buses[i]
            if x is None:
                self.idle[i] += 1
                if self.idle[i] > 200 or not any(f is not None for f in self.chains[i]):
                    self.peaks[i] = 0.0
                    continue                                    # тишина и хвосты давно кончились
                x = np.zeros((n, 2), np.float32)
            else:
                self.idle[i] = 0
            self.ctx.beat = (pos + lat_m) / spb
            x = self._chain(i, x)
            if ins.get("mute") or (solo and not ins.get("solo")):
                self.peaks[i] = 0.0
                continue
            vol = float(ins.get("vol", 1.0))
            pan = float(ins.get("pan", 0.0))
            x = x * np.array([vol * min(1.0, 1 - pan), vol * min(1.0, 1 + pan)], np.float32)
            self.peaks[i] = np.abs(x).max(axis=0)
            master += x
        self.ctx.beat = pos / spb
        master = self._chain(0, master)
        mv = float(mix[0].get("vol", 1.0)) * float(p.get("master_vol", 0.85)) / 0.85
        pan = float(mix[0].get("pan", 0.0))
        master *= np.array([mv * min(1.0, 1 - pan), mv * min(1.0, 1 + pan)], np.float32)
        np.clip(master, -1.0, 1.0, out=master)
        self.peaks[0] = np.abs(master).max(axis=0)
        return master

    def _chain(self, i, x):
        ch = self.chains[i] if i < len(self.chains) else None
        if not ch:
            return x
        slots = self.p["mixer"][i]["fx"]
        for s, fx in enumerate(ch):
            if fx is None:
                continue
            slot = slots[s] if s < len(slots) else None
            if not slot or not slot.get("on", True):
                continue
            try:
                y = fx.process(x)
            except Exception as e:                       # noqa: BLE001
                _log(f"fx {slot.get('type')}: {e!r}")
                continue
            m = float(slot.get("mix", 1.0))
            if m < 0.999 and fx.latency() == 0:
                y = x * (1 - m) + y * m
            x = y
        return x

    @staticmethod
    def _pieces(r, n, L):
        """Окно чтения [r, r+n) с учётом цикла длины L → [(начало, длина, сдвиг в блоке)]."""
        if L <= 0:
            return [(r, n, 0)]
        a = r % L
        if a + n <= L:
            return [(a, n, 0)]
        k = L - a
        out = [(a, k, 0)]
        rest = n - k
        off = k
        while rest > 0:
            m = min(rest, L)
            out.append((0, m, off))
            off += m
            rest -= m
        return out


class Engine:
    """Транспорт + поток вывода + запись."""

    def __init__(self, project: dict, device=None):
        self.ctx = FX.Ctx(project.get("bpm", 130))
        self.mixer = Mixer(project, self.ctx)
        self.pos = 0
        self.play_start = 0
        self.playing = False
        self.metronome = False
        self.loop = True
        self.cpu = 0.0
        self.closed = False
        self.out_latency = 0.1
        self.stream = None
        self.device = device
        self._pos_at = (0, time.perf_counter())
        self.rec = None
        self.blocks = 0
        self._null = os.environ.get("ECHOES_AUDIO_BACKEND", "").lower().startswith("null")
        self._open()

    # ── поток вывода ── #
    def _open(self):
        if self._null:
            self._start_null()
            return
        try:
            import sounddevice as sd
            self.stream = sd.OutputStream(samplerate=SR, channels=2, dtype="float32", blocksize=BLOCK,
                                          latency=0.09, callback=self._cb, device=self.device)
            self.stream.start()
            try:
                self.out_latency = float(self.stream.latency)
            except Exception:                            # noqa: BLE001
                pass
            _log(f"output open, latency {self.out_latency * 1000:.0f} ms")
        except Exception as e:                           # noqa: BLE001
            _log(f"output failed: {e!r} — без звука")
            self._null = True
            self._start_null()

    def _start_null(self):
        self.out_latency = 0.03
        self._thr = threading.Thread(target=self._null_run, daemon=True, name="studio-null")
        self._thr.start()

    def _null_run(self):
        period = BLOCK / SR
        nxt = time.perf_counter()
        buf = np.zeros((BLOCK, 2), np.float32)
        while not self.closed:
            self._cb(buf, BLOCK, None, None)
            nxt += period
            d = nxt - time.perf_counter()
            if d > 0:
                time.sleep(d)
            else:
                nxt = time.perf_counter()

    def reopen(self, device=None):
        """Сменить устройство вывода."""
        self.device = device
        try:
            if self.stream is not None:
                self.stream.stop()
                self.stream.close()
        except Exception:                                # noqa: BLE001
            pass
        self.stream = None
        if not self._null:
            self._open()

    def _cb(self, out, frames, _t, _status):
        t0 = time.perf_counter()
        try:
            y = self._render(frames)
        except Exception as e:                           # noqa: BLE001
            _log(f"render: {e!r}")
            y = np.zeros((frames, 2), np.float32)
        out[:] = y
        el = time.perf_counter() - t0
        self.cpu = self.cpu * 0.9 + 0.1 * el / (frames / SR)
        self.blocks += 1

    def _render(self, n):
        p = self.mixer.p
        self.ctx.bpm = float(p.get("bpm", 130))
        self.ctx.playing = self.playing
        pos = self.pos
        y = self.mixer.block(pos, n, self.playing, self.loop)
        self.last_out = y[::4, 0].copy()                # для осциллографа в верхней панели
        if self.playing:
            if self.metronome:
                self._metro(y, pos, n)
            L = self.mixer.arr.length
            np_ = pos + n
            self.pos = np_ % L if (self.loop and L > 0) else np_
            self._pos_at = (self.pos, time.perf_counter())
        return y

    def _metro(self, y, pos, n):
        spb = self.ctx.spb()
        acc, nrm = clicks()
        b0 = math.ceil(pos / spb - 1e-9)
        while True:
            s = int(round(b0 * spb))
            if s >= pos + n:
                break
            if s >= pos:
                c = acc if int(b0) % 4 == 0 else nrm
                m = min(len(c), pos + n - s)
                y[s - pos:s - pos + m] += c[:m]
            b0 += 1

    # ── транспорт ── #
    def play(self, from_pos=None):
        if from_pos is not None:
            self.pos = int(from_pos)
        self.play_start = self.pos
        self.mixer.reset_fx()
        self._pos_at = (self.pos, time.perf_counter())
        self.playing = True

    def stop(self):
        was = self.playing
        self.playing = False
        if not was:
            self.pos = 0
            self.play_start = 0
        else:
            self.pos = self.play_start

    def pause(self):
        self.playing = False

    def set_pos(self, s):
        self.pos = max(0, int(s))
        self._pos_at = (self.pos, time.perf_counter())
        if not self.playing:
            self.play_start = self.pos

    def heard_pos(self) -> float:
        """Позиция того, что слышно сейчас (с учётом задержки вывода), в сэмплах."""
        if not self.playing:
            return float(self.pos)
        p, t = self._pos_at
        L = self.mixer.arr.length
        v = p + (time.perf_counter() - t) * SR - self.out_latency * SR - BLOCK
        if self.loop and L > 0:
            v %= L
        return max(0.0, v)

    def close(self):
        self.closed = True
        self.playing = False
        self.stop_rec()
        try:
            if self.stream is not None:
                self.stream.stop()
                self.stream.close()
        except Exception:                                # noqa: BLE001
            pass
        self.stream = None

    # ── запись ── #
    def open_input(self, device=None):
        """Открыть микрофон (уровень виден сразу; данные копятся, когда запись взведена и идёт play)."""
        if self.rec is not None and self.rec.device == device and self.rec.ok:
            return True
        self.stop_rec()
        self.rec = Recorder(self, device)
        return self.rec.ok

    def stop_rec(self):
        r, self.rec = self.rec, None
        if r is not None:
            return r.finish()
        return None


class Recorder:
    def __init__(self, engine: Engine, device=None):
        self.eng = engine
        self.device = device
        self.chunks = []
        self.level = 0.0
        self.ok = False
        self.err = ""
        self.started_at = None
        self.in_latency = 0.02
        self.stream = None
        self.armed = False
        self.lock = threading.Lock()
        self.fake = engine._null
        if self.fake:
            self.ok = True
            return
        try:
            import sounddevice as sd
            self.stream = sd.InputStream(samplerate=SR, channels=1, dtype="float32", blocksize=BLOCK,
                                         latency="low", callback=self._cb, device=device)
            self.stream.start()
            try:
                self.in_latency = float(self.stream.latency)
            except Exception:                            # noqa: BLE001
                pass
            self.ok = True
        except Exception as e:                           # noqa: BLE001
            self.err = str(e)
            _log(f"input failed: {e!r}")

    def _cb(self, data, frames, _t, _status):
        self.feed(data[:, 0].copy())

    def feed(self, x):
        self.level = max(self.level * 0.85, float(np.abs(x).max()) if len(x) else 0.0)
        if self.eng.playing and self.armed:
            with self.lock:
                if self.started_at is None:
                    self.started_at = self.eng.pos
                self.chunks.append(x)

    def take(self):
        """Забрать записанное (запись остаётся открытой для следующего дубля)."""
        with self.lock:
            if not self.chunks:
                self.started_at = None
                return None
            x = np.concatenate(self.chunks)
            st = self.started_at or 0
            self.chunks = []
            self.started_at = None
        # слышим вывод с задержкой и поём вслед — запись опаздывает на вывод + ввод
        shift = int((self.eng.out_latency + self.in_latency) * SR) + BLOCK
        return {"data": x, "start": st, "shift": shift}

    def finish(self):
        try:
            if self.stream is not None:
                self.stream.stop()
                self.stream.close()
        except Exception:                                # noqa: BLE001
            pass
        self.stream = None
        return self.take()


# ------------------------------------------------------------------ #
#  Экспорт                                                            #
# ------------------------------------------------------------------ #

def render_song(project: dict, renderer: PR.Renderer, mode="song", progress=None, cancel=None,
                tail_s=4.0) -> np.ndarray:
    """Весь проект в float32 (n, 2) — тем же сведением, что и при воспроизведении."""
    p = project
    ctx = FX.Ctx(p.get("bpm", 130))
    ctx.playing = True
    mx = Mixer(p, ctx)
    mx.sync_fx()
    arr = renderer.build(p, mode, cancel=cancel, wait_sources=True)
    if arr is None:
        raise RuntimeError("отменено")
    mx.arr = arr
    if mode == "song":
        end = max((e.end for e in arr.events), default=0)
        if end <= 0:
            end = int(PR.song_beats(p) * PR.spb(p))
    else:
        end = arr.length
    total = end + int(tail_s * SR)
    out = np.zeros((total + BLOCK, 2), np.float32)
    lat = max(mx.lat) if mx.lat else 0
    pos = 0
    nblk = (total + BLOCK - 1) // BLOCK
    quiet = 0
    for b in range(nblk):
        if cancel is not None and cancel.is_set():
            raise RuntimeError("отменено")
        ctx.bpm = float(p.get("bpm", 130))
        y = mx.block(pos, BLOCK, True, loop=False)
        out[pos:pos + BLOCK] = y
        pos += BLOCK
        if pos > end + lat:
            quiet = quiet + 1 if np.abs(y).max() < 1e-4 else 0
            if quiet > 20:
                break
        if progress and b % 32 == 0:
            progress(b / nblk)
    out = out[:pos]
    nz = np.nonzero(np.abs(out).max(axis=1) > 1e-4)[0]
    if len(nz):
        out = out[:min(len(out), max(end, nz[-1] + int(0.05 * SR)))]
    return out
