# osu_sfx.py
"""
Звуки темы «osu!» (хитсаунды, меню) — отдельный короткий поток вывода с низкой задержкой
(~20 мс), поверх музыки плеера. Нет sounddevice / устройства — тихо выключено.
"""
from __future__ import annotations

import os
import threading

import numpy as np

SR = 48000
BANK_VERSION = 3
BANK_FILE = os.path.join(os.path.expanduser("~"), ".neon_player", "osu", f"sfx_bank_v{BANK_VERSION}.npz")


def _load_bank() -> dict:
    """Встроенные звуки: из кэша на диске (мгновенно), иначе синтез и сохранение."""
    try:
        with np.load(BANK_FILE) as z:
            return {k: z[k].astype(np.float32) for k in z.files}
    except Exception:                            # noqa: BLE001
        pass
    try:
        from osu_skin import make_sounds
        bank = make_sounds()
    except Exception as e:                       # noqa: BLE001
        print("[esu sfx] sounds:", e)
        return {}
    try:
        os.makedirs(os.path.dirname(BANK_FILE), exist_ok=True)
        tmp = BANK_FILE + ".tmp.npz"
        np.savez(tmp, **bank)
        os.replace(tmp, BANK_FILE)
    except Exception as e:                       # noqa: BLE001
        print("[esu sfx] cache:", e)
    return bank


class Sfx:
    def __init__(self):
        self.ok = False
        self.volume = 0.6
        self.lock = threading.Lock()
        self.voices: list = []                   # [моно, позиция, gL, gR]
        self.bank = {}
        self.stream = None
        self.bank = _load_bank()
        if os.environ.get("ECHOES_AUDIO_BACKEND", "").lower().startswith("null"):
            return                               # тесты без звуковой карты
        try:
            import sounddevice as sd
            self.stream = sd.OutputStream(samplerate=SR, channels=2, dtype="float32", blocksize=256,
                                          latency="low", callback=self._cb)
            self.stream.start()
            self.ok = True
        except Exception as e:                   # noqa: BLE001
            print("[osu sfx] output:", e)

    def _cb(self, out, frames, _time, _status):
        out.fill(0)
        with self.lock:
            vs = self.voices
            keep = []
            for v in vs:
                a, pos, gl, gr = v
                n = min(frames, len(a) - pos)
                if n > 0:
                    seg = a[pos:pos + n]
                    out[:n, 0] += seg * gl
                    out[:n, 1] += seg * gr
                    v[1] = pos + n
                if v[1] < len(a):
                    keep.append(v)
            self.voices = keep
        np.clip(out, -1.0, 1.0, out=out)

    def play(self, name: str, vol: float = 1.0, pan: float = 0.0):
        if not self.ok:
            return
        a = self.bank.get(name)
        if a is None:
            return
        g = self.volume * vol
        if g <= 0.001:
            return
        pan = max(-1.0, min(1.0, pan)) * 0.6
        gl, gr = g * min(1.0, 1 - pan), g * min(1.0, 1 + pan)
        with self.lock:
            if len(self.voices) > 48:
                self.voices = self.voices[-40:]
            self.voices.append([a, 0, gl, gr])

    def has(self, name: str) -> bool:
        return name in self.bank

    def register(self, name: str, a):
        """Свой звук (например, хитсаунд из папки карты osu!)."""
        if a is None or not len(a):
            return
        peak = float(np.abs(a).max()) or 1.0
        self.bank[name] = (np.asarray(a, np.float32) / max(1.0, peak)).astype(np.float32)

    def unregister_prefix(self, prefix: str):
        for k in [k for k in self.bank if k.startswith(prefix)]:
            del self.bank[k]

    def stop_all(self):
        with self.lock:
            self.voices = []

    def close(self):
        self.ok = False
        try:
            if self.stream is not None:
                self.stream.stop()
                self.stream.close()
        except Exception:                        # noqa: BLE001
            pass
        self.stream = None


_SFX = None


def release():
    """Тема osu! закрыта: поток вывода (он будил процессор ~190 раз в секунду) и банк звуков
    больше не нужны. При следующем входе в тему всё создаётся заново."""
    global _SFX
    s, _SFX = _SFX, None
    if s is not None:
        s.close()
        s.bank = {}
        s.voices = []


def get() -> Sfx:
    global _SFX
    if _SFX is None:
        _SFX = Sfx()
        try:
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                app.aboutToQuit.connect(_SFX.close)
        except Exception:                        # noqa: BLE001
            pass
    return _SFX
