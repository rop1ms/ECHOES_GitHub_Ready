"""Что реально выходит из звукового движка: громкость (RMS/пик) и бас при «Экстремальной громкости» и басс бусте.

Настоящий AudioEngine + VLC, вывод — echoes_audio.dll без звуковой карты (кадры забираются вручную в реальном
времени). Трек — громкий «мастер» (пик 0 дБ, как современные релизы). python qa/probe_loudness.py [--json out.json]
"""
import json
import math
import os
import sys
import tempfile
import time
import wave
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))
os.chdir(APP)
inst = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "ECHOES"
for sub in ("ffmpeg", "vlc"):
    if (inst / sub).is_dir():
        os.environ["PATH"] = str(inst / sub) + os.pathsep + os.environ["PATH"]
import numpy as np
from PyQt6.QtWidgets import QApplication

app = QApplication(sys.argv)
import audio_native

_orig = audio_native.NativeMixer.__init__
audio_native.NativeMixer.__init__ = lambda self, open_stream=True: _orig(self, open_stream=False)
import audio_engine


def master(path, sec=60, sr=48000):
    t = np.arange(sr * sec) / sr
    rng = np.random.default_rng(1)
    noise = rng.standard_normal(len(t))
    b = np.cumsum(noise)
    b -= np.convolve(b, np.ones(400) / 400, "same")
    mids = noise * 0.25 + b * 0.02
    kick = np.zeros_like(t)
    for k in np.arange(0, sec, 0.5):
        i = int(k * sr)
        m = min(len(t) - i, int(0.15 * sr))
        kick[i:i + m] += np.sin(2 * np.pi * 55 * t[:m]) * np.exp(-t[:m] * 18)
    x = mids + 0.8 * kick + 0.25 * np.sin(2 * np.pi * 45 * t)
    x = np.tanh(x * 1.6)                                        # «мастеринг» — пики у 0 дБ
    x /= np.abs(x).max() / 0.99
    pcm = (np.stack([x, x], 1) * 32767).astype("<i2")
    with wave.open(path, "w") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(sr); w.writeframes(pcm.tobytes())


def capture(eng, seconds=3.0):
    lib, h = eng.mixer.lib, eng.mixer.handle
    n = 480
    buf = (np.zeros(n * 2, np.float32))
    out = []
    t0 = time.perf_counter()
    k = 0
    while time.perf_counter() - t0 < seconds:
        target = t0 + k * n / 48000
        dt = target - time.perf_counter()
        if dt > 0:
            time.sleep(dt)
        buf[:] = 0
        lib.na_render(h, buf.ctypes.data, n, 0.02, 0)
        out.append(buf.copy())
        app.processEvents()
        k += 1
    a = np.concatenate(out).reshape(-1, 2)
    return a[int(len(a) * 0.35):]                             # без разгона


def low(x, sr=48000, hz=120):
    from scipy import signal as sg
    return sg.sosfilt(sg.butter(4, hz / (sr / 2), "low", output="sos"), x)


def band(x, sr=48000):
    from scipy import signal as sg
    return sg.sosfilt(sg.butter(2, [400 / (sr / 2), 3000 / (sr / 2)], "band", output="sos"), x)


def db(v):
    return 20 * math.log10(max(1e-9, v))


path = os.path.join(tempfile.gettempdir(), "echoes_master_probe.wav")
master(path)
eng = audio_engine.AudioEngine()
eng.set_volume(1.0)
eng.load(path, autoplay=True)
time.sleep(0.6)
res = {}
for boost, bass in ((1, 0), (2, 0), (10, 0), (100, 0), (1, 50), (1, 100)):
    eng.set_bass(bass / 100)                                 # умный бас в своём выводе (как кнопки «Басс буст»)
    eng.set_boost(boost)
    time.sleep(0.4)
    a = capture(eng, 2.5)
    m = a.mean(1)
    r = {"rms_db": round(db(float(np.sqrt((m * m).mean()))), 2), "peak": round(float(np.abs(a).max()), 3),
         "low_db": round(db(float(np.sqrt((low(m) ** 2).mean()))), 2),
         "mid_db": round(db(float(np.sqrt((band(m) ** 2).mean()))), 2)}
    r["bass_vs_mid"] = round(r["low_db"] - r["mid_db"], 2)
    res[f"boost x{boost} bass {bass}%"] = r
    print(f"boost x{boost:<4} bass {bass:>3}% RMS {r['rms_db']:7.2f} dB  peak {r['peak']:.3f}  bass {r['low_db']:7.2f} dB  бас к середине {r['bass_vs_mid']:+6.2f} dB",
          flush=True)
eng.stop() if hasattr(eng, "stop") else None
if "--json" in sys.argv:
    Path(sys.argv[sys.argv.index("--json") + 1]).write_text(json.dumps(res, indent=1), "utf-8")
os._exit(0)
