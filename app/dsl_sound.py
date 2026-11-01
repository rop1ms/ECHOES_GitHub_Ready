# dsl_sound.py
"""
Звуки для EchoScript: свои эффекты поверх музыки плеера (игры, кнопки, «скримеры»).

  sound(path, volume, pan, rate, loop) → номер звука; sound_stop(id), sound_volume(id, v), sound_rate(id, r)…
  beep(freq, dur, volume, wave)        — синтез без файлов: sine / square / saw / triangle / noise
  tone([[freq, dur], …])               — мелодия из нот

Файлы (wav, mp3, ogg, flac, m4a…) декодируются в фоне (WAV — сам, остальное — ffmpeg), кэш в памяти.
Свой поток вывода (sounddevice) открывается при первом звуке и закрывается через 8 с тишины,
чтобы не будить процессор, когда скрипты молчат. Нет звуковой карты — всё тихо выключено.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import numpy as np

SR = 48000
MAX_SEC = 600                                   # длиннее 10 минут не декодируем
_CACHE_BYTES = 256 * 2 ** 20


def _ffmpeg():
    try:
        import osu_game as G
        return G._ffmpeg()
    except Exception:                           # noqa: BLE001
        import shutil
        return shutil.which("ffmpeg")


def decode(path: str):
    """Файл → стерео float32 [n, 2] на 48 кГц или None."""
    p = Path(path)
    if not p.is_file():
        return None
    if p.suffix.lower() == ".wav":
        try:
            import wave
            with wave.open(str(p), "rb") as w:
                ch, sw, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
                raw = w.readframes(min(n, rate * MAX_SEC))
            if sw == 2:
                a = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
            elif sw == 1:
                a = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128) / 128.0
            elif sw == 4:
                a = np.frombuffer(raw, np.int32).astype(np.float32) / 2147483648.0
            elif sw == 3:
                b = np.frombuffer(raw, np.uint8).reshape(-1, 3)
                a = ((b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) |
                      (b[:, 2].astype(np.int32) << 16)) << 8 >> 8).astype(np.float32) / 8388608.0
            else:
                a = None
            if a is not None:
                a = a[: len(a) // ch * ch].reshape(-1, ch)
                if ch == 1:
                    a = np.repeat(a, 2, axis=1)
                elif ch > 2:
                    a = a[:, :2]
                if rate != SR and len(a) > 1:
                    x = np.arange(0, len(a), rate / SR)
                    idx = np.arange(len(a))
                    a = np.stack([np.interp(x, idx, a[:, 0]), np.interp(x, idx, a[:, 1])], 1)
                return np.ascontiguousarray(a, np.float32)
        except Exception:                       # noqa: BLE001
            pass
    ff = _ffmpeg()
    if not ff:
        return None
    try:
        import subprocess
        r = subprocess.run([ff, "-v", "error", "-nostdin", "-i", str(p), "-t", str(MAX_SEC), "-ac", "2",
                            "-ar", str(SR), "-f", "f32le", "-"], capture_output=True, timeout=120,
                           creationflags=0x08000000 if os.name == "nt" else 0)
        a = np.frombuffer(r.stdout, np.float32)
        if len(a) < 2:
            return None
        return a[: len(a) // 2 * 2].reshape(-1, 2).copy()
    except Exception as e:                      # noqa: BLE001
        print("[echoscript] звук:", e)
        return None


def synth(freq=880.0, dur=0.15, wave="sine", attack=0.005, release=0.06, sweep=0.0):
    """Короткий синтезированный звук → стерео float32."""
    dur = max(0.005, min(10.0, float(dur)))
    n = int(SR * dur)
    t = np.arange(n, dtype=np.float32) / SR
    f0 = max(1.0, float(freq))
    f1 = max(1.0, f0 + float(sweep))
    fr = f0 + (f1 - f0) * (t / dur)
    ph = 2 * np.pi * np.cumsum(fr) / SR
    w = str(wave).lower()
    if w == "square":
        a = np.sign(np.sin(ph)) * 0.6
    elif w in ("saw", "sawtooth"):
        a = (2 * ((ph / (2 * np.pi)) % 1.0) - 1) * 0.6
    elif w == "triangle":
        a = (2 * np.abs(2 * ((ph / (2 * np.pi)) % 1.0) - 1) - 1)
    elif w == "noise":
        a = np.random.default_rng().uniform(-1, 1, n).astype(np.float32) * 0.7
    else:
        a = np.sin(ph)
    env = np.ones(n, np.float32)
    na, nr = int(SR * max(0.0, attack)), int(SR * max(0.0, release))
    if na > 0:
        env[:min(na, n)] = np.linspace(0, 1, min(na, n), dtype=np.float32)
    if nr > 0:
        k = min(nr, n)
        env[n - k:] *= np.linspace(1, 0, k, dtype=np.float32)
    a = (a * env).astype(np.float32)
    return np.repeat(a[:, None], 2, axis=1)


class _Voice:
    __slots__ = ("id", "buf", "pos", "vol", "pan", "rate", "loop", "fade", "fade_to", "key", "paused")

    def __init__(self, vid, buf, vol, pan, rate, loop, key):
        self.id, self.buf, self.pos = vid, buf, 0.0
        self.vol, self.pan, self.rate, self.loop = vol, pan, rate, loop
        self.fade, self.fade_to, self.key, self.paused = 0.0, None, key, False


class Mixer:
    def __init__(self):
        self.lock = threading.Lock()
        self.voices: dict[int, _Voice] = {}
        self.pending: dict[int, tuple] = {}     # id → (путь, громкость, панорама, скорость, цикл)
        self.cache: dict[str, np.ndarray] = {}
        self.loading: set = set()
        self.failed: set = set()
        self.stream = None
        self.ok = None
        self._next = 1
        self.master = 1.0
        self._quiet_since = None
        self._closer = None

    # ── поток вывода ──
    def _ensure_stream(self):
        if self.stream is not None:
            return True
        if self.ok is False or os.environ.get("ECHOES_AUDIO_BACKEND", "").lower().startswith("null"):
            self.ok = False
            return False
        try:
            import sounddevice as sd
            self.stream = sd.OutputStream(samplerate=SR, channels=2, dtype="float32", blocksize=512,
                                          latency="low", callback=self._cb)
            self.stream.start()
            self.ok = True
            if self._closer is None:
                self._closer = threading.Thread(target=self._close_when_quiet, daemon=True, name="echo-sfx")
                self._closer.start()
            return True
        except Exception as e:                  # noqa: BLE001
            print("[echoscript] звук недоступен:", e)
            self.ok = False
            self.stream = None
            return False

    def _close_when_quiet(self):
        while True:
            time.sleep(1.0)
            with self.lock:
                busy = bool(self.voices) or bool(self.pending)
            if busy:
                self._quiet_since = None
                continue
            now = time.monotonic()
            if self._quiet_since is None:
                self._quiet_since = now
            elif now - self._quiet_since > 8.0 and self.stream is not None:
                s, self.stream = self.stream, None
                try:
                    s.stop()
                    s.close()
                except Exception:               # noqa: BLE001
                    pass
            if self.stream is None and now - (self._quiet_since or now) > 8.0:
                self._closer = None
                return

    def _cb(self, out, frames, _t, _st):
        out.fill(0)
        with self.lock:
            dead = []
            for v in self.voices.values():
                if v.paused:
                    continue
                buf = v.buf
                n = len(buf)
                if n == 0:
                    dead.append(v.id)
                    continue
                if v.rate == 1.0 and v.pos == int(v.pos):
                    p = int(v.pos)
                    if v.loop:
                        idx = (np.arange(frames) + p) % n
                        seg = buf[idx]
                        v.pos = float((p + frames) % n)
                    else:
                        seg = buf[p:p + frames]
                        v.pos = float(p + len(seg))
                else:
                    x = v.pos + np.arange(frames, dtype=np.float64) * v.rate
                    if v.loop:
                        x %= n
                    else:
                        x = x[x < n - 1]
                    i0 = x.astype(np.int64)
                    fr = (x - i0).astype(np.float32)[:, None]
                    i1 = np.minimum(i0 + 1, n - 1) if not v.loop else (i0 + 1) % n
                    seg = buf[i0] * (1 - fr) + buf[i1] * fr
                    v.pos = (v.pos + frames * v.rate) % n if v.loop else v.pos + frames * v.rate
                g = v.vol * self.master
                if v.fade_to is not None:
                    v.vol += (v.fade_to - v.vol) * min(1.0, v.fade * frames / SR)
                    if abs(v.vol - v.fade_to) < 0.003:
                        v.vol = v.fade_to
                        if v.fade_to <= 0.0:
                            dead.append(v.id)
                        v.fade_to = None
                pan = v.pan
                gl, gr = g * min(1.0, 1 - pan), g * min(1.0, 1 + pan)
                m = len(seg)
                out[:m, 0] += seg[:, 0] * gl
                out[:m, 1] += seg[:, 1] * gr
                if not v.loop and v.pos >= n - 1:
                    dead.append(v.id)
            for vid in dead:
                self.voices.pop(vid, None)
        np.clip(out, -1.0, 1.0, out=out)

    # ── загрузка ──
    def _load_async(self, path):
        if path in self.loading or path in self.cache or path in self.failed:
            return
        self.loading.add(path)

        def work():
            a = decode(path)
            with self.lock:
                self.loading.discard(path)
                if a is None:
                    self.failed.add(path)
                    for vid in [k for k, p in self.pending.items() if p[0] == path]:
                        self.pending.pop(vid, None)
                    return
                total = sum(x.nbytes for x in self.cache.values())
                while self.cache and total + a.nbytes > _CACHE_BYTES:
                    k = next(iter(self.cache))
                    total -= self.cache.pop(k).nbytes
                self.cache[path] = a
                for vid in [k for k, p in self.pending.items() if p[0] == path]:
                    _path, vol, pan, rate, loop = self.pending.pop(vid)
                    self.voices[vid] = _Voice(vid, a, vol, pan, rate, loop, path)
        threading.Thread(target=work, daemon=True, name="echo-sfx-load").start()

    def preload(self, path):
        path = str(path or "")
        if path:
            self._load_async(path)
        return path in self.cache

    # ── действия ──
    def play(self, path, vol=1.0, pan=0.0, rate=1.0, loop=False):
        path = str(path or "")
        if not path or path in self.failed:
            return 0
        if not self._ensure_stream():
            return 0
        vol = max(0.0, min(4.0, float(vol)))
        pan = max(-1.0, min(1.0, float(pan)))
        rate = max(0.05, min(8.0, float(rate)))
        with self.lock:
            vid = self._next
            self._next += 1
            if len(self.voices) > 64:                 # слишком много — гасим самые старые
                for k in sorted(self.voices)[:len(self.voices) - 56]:
                    self.voices.pop(k, None)
            a = self.cache.get(path)
            if a is not None:
                self.voices[vid] = _Voice(vid, a, vol, pan, rate, bool(loop), path)
                return vid
            self.pending[vid] = (path, vol, pan, rate, bool(loop))
        self._load_async(path)
        return vid

    def play_buf(self, buf, vol=1.0, pan=0.0, rate=1.0, loop=False):
        if not self._ensure_stream():
            return 0
        with self.lock:
            vid = self._next
            self._next += 1
            self.voices[vid] = _Voice(vid, buf, max(0.0, min(4.0, float(vol))), max(-1.0, min(1.0, float(pan))),
                                      max(0.05, min(8.0, float(rate))), bool(loop), None)
        return vid

    def stop(self, vid, fade=0.0):
        with self.lock:
            self.pending.pop(int(vid or 0), None)
            v = self.voices.get(int(vid or 0))
            if v is None:
                return
            if fade and float(fade) > 0:
                v.fade, v.fade_to = 1.0 / float(fade) * 4.0, 0.0
            else:
                self.voices.pop(v.id, None)

    def stop_all(self):
        with self.lock:
            self.voices.clear()
            self.pending.clear()

    def set(self, vid, **kw):
        with self.lock:
            v = self.voices.get(int(vid or 0))
            if v is None:
                p = self.pending.get(int(vid or 0))
                if p is not None and "vol" in kw:
                    self.pending[int(vid)] = (p[0], float(kw["vol"]), p[2], p[3], p[4])
                return
            if "vol" in kw:
                v.vol = max(0.0, min(4.0, float(kw["vol"])))
                v.fade_to = None
            if "pan" in kw:
                v.pan = max(-1.0, min(1.0, float(kw["pan"])))
            if "rate" in kw:
                v.rate = max(0.05, min(8.0, float(kw["rate"])))
            if "paused" in kw:
                v.paused = bool(kw["paused"])
            if "fade_to" in kw:
                v.fade_to = max(0.0, min(4.0, float(kw["fade_to"])))
                v.fade = 1.0 / max(0.01, float(kw.get("fade", 0.5))) * 4.0
            if "pos" in kw:
                v.pos = max(0.0, min(len(v.buf) - 1.0, float(kw["pos"]) * SR))

    def playing(self, vid):
        with self.lock:
            return int(vid or 0) in self.voices or int(vid or 0) in self.pending

    def position(self, vid):
        with self.lock:
            v = self.voices.get(int(vid or 0))
            return v.pos / SR if v is not None else 0.0

    def length(self, path):
        a = self.cache.get(str(path or ""))
        if a is None:
            self._load_async(str(path or ""))
            return 0.0
        return len(a) / SR

    def close(self):
        self.stop_all()
        s, self.stream = self.stream, None
        if s is not None:
            try:
                s.stop()
                s.close()
            except Exception:                   # noqa: BLE001
                pass


_MIX = None
_SYNTH_CACHE: dict = {}


def mixer() -> Mixer:
    global _MIX
    if _MIX is None:
        _MIX = Mixer()
        try:
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                app.aboutToQuit.connect(_MIX.close)
        except Exception:                       # noqa: BLE001
            pass
    return _MIX


def api(resolve=None) -> dict:
    """Функции для скриптов. resolve(path) — «assets/…» темы → полный путь."""
    def rp(p):
        p = str(p or "")
        if resolve is not None:
            try:
                q = resolve(p)
                if q:
                    return q
            except Exception:                   # noqa: BLE001
                pass
        return p

    def sound(path, volume=1.0, pan=0.0, rate=1.0, loop=False):
        return mixer().play(rp(path), volume, pan, rate, loop)

    def sound_loop(path, volume=1.0, pan=0.0, rate=1.0):
        return mixer().play(rp(path), volume, pan, rate, True)

    def beep(freq=880.0, dur=0.15, volume=0.5, wave="sine", sweep=0.0, pan=0.0):
        key = (round(float(freq), 2), round(float(dur), 3), str(wave), round(float(sweep), 2))
        buf = _SYNTH_CACHE.get(key)
        if buf is None:
            buf = synth(freq, dur, wave, sweep=sweep)
            if len(_SYNTH_CACHE) > 128:
                _SYNTH_CACHE.clear()
            _SYNTH_CACHE[key] = buf
        return mixer().play_buf(buf, volume, pan)

    def tone(notes, volume=0.4, wave="square", gap=0.02):
        """tone([[440, 0.1], [660, 0.1], [0, 0.05], [880, 0.2]]) — мелодия; частота 0 — пауза."""
        parts = []
        for nt in notes or []:
            if isinstance(nt, (list, tuple)) and len(nt) >= 2:
                f, d = float(nt[0]), float(nt[1])
            else:
                f, d = float(nt), 0.12
            if f <= 0:
                parts.append(np.zeros((int(SR * max(0.0, d)), 2), np.float32))
            else:
                parts.append(synth(f, d, wave))
            if gap > 0:
                parts.append(np.zeros((int(SR * float(gap)), 2), np.float32))
        if not parts:
            return 0
        return mixer().play_buf(np.concatenate(parts), volume)

    def note(name):
        """note("A4") → 440; note("C#5") → 554.37."""
        import re
        m = re.match(r"^\s*([A-Ga-g])([#b]?)(-?\d)\s*$", str(name))
        if not m:
            return 0.0
        n = {"c": -9, "d": -7, "e": -5, "f": -4, "g": -2, "a": 0, "b": 2}[m.group(1).lower()]
        n += 1 if m.group(2) == "#" else -1 if m.group(2) == "b" else 0
        n += (int(m.group(3)) - 4) * 12
        return 440.0 * 2 ** (n / 12)

    m = mixer
    return {
        "sound": sound, "sound_loop": sound_loop, "beep": beep, "tone": tone, "note": note,
        "sound_stop": lambda vid, fade=0.0: m().stop(vid, fade),
        "sound_stop_all": lambda: m().stop_all(),
        "sound_volume": lambda vid, v: m().set(vid, vol=v),
        "sound_fade": lambda vid, to=0.0, sec=0.5: m().set(vid, fade_to=to, fade=sec),
        "sound_pan": lambda vid, p: m().set(vid, pan=p),
        "sound_rate": lambda vid, r: m().set(vid, rate=r),
        "sound_pause": lambda vid, on=True: m().set(vid, paused=on),
        "sound_seek": lambda vid, sec: m().set(vid, pos=sec),
        "sound_playing": lambda vid: m().playing(vid),
        "sound_pos": lambda vid: m().position(vid),
        "sound_length": lambda path: m().length(rp(path)),
        "sound_preload": lambda path: m().preload(rp(path)),
        "sound_master": lambda v=None: setattr(m(), "master", max(0.0, min(2.0, float(v)))) if v is not None
        else m().master,
    }
