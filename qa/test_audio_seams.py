"""Gapless seams through the real VLC + native mixer, with a simulated sound card.

Coded test tracks: left = track level, right = level * position / 60 — what the card plays tells which track
and which second is sounding. Regressions covered:
  * every second seam was silent forever (the chained deck came first in the deck list, its handoff was lost);
  * the next track jumped 1-2 s ahead (the chained deck's queue overflowed while it waited for the seam);
  * chaining after the previous deck already played out never started the next track."""
import ctypes
import os
import sys
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path

import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from PyQt6.QtWidgets import QApplication  # noqa: E402

APP = QApplication.instance() or QApplication([])
SR = 48000
LEVELS = [0.25, 0.35, 0.45, 0.55]
SEC = 4.0


class Card:
    """PortAudio stand-in: renders 480-frame blocks in real time and keeps block means."""

    def __init__(self, mixer):
        self.mixer, self.blocks, self.stop = mixer, [], threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        buf = np.zeros(960, np.float32)
        nxt = time.perf_counter()
        while not self.stop.is_set():
            self.mixer.lib.na_render(self.mixer.handle, buf.ctypes.data, 480, 0.15, 0)
            a = buf.reshape(-1, 2)
            self.blocks.append((time.perf_counter(), float(a[:, 0].mean()), float(a[:, 1].mean())))
            nxt += 0.01
            d = nxt - time.perf_counter()
            if d > 0:
                time.sleep(d)
            else:
                nxt = time.perf_counter()


class AudioSeams(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.paths = []
        for k, level in enumerate(LEVELS):
            p = Path(cls.tmp.name) / f'coded{k}.wav'
            t = np.arange(int(SEC * SR)) / SR
            a = np.stack([np.full_like(t, level), level * t / 60.0], 1)
            with wave.open(str(p), 'wb') as w:
                w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
                w.writeframes((a * 32767).astype('<i2').tobytes())
            cls.paths.append(str(p))
        import audio_out
        import audio_native
        cls.mixer = audio_native.NativeMixer(open_stream=False)
        audio_out.create_mixer = lambda *a, **k: cls.mixer
        import audio_engine
        cls.engine = audio_engine.AudioEngine()
        cls.engine.set_volume(1.0)
        cls.card = Card(cls.mixer)

    @classmethod
    def tearDownClass(cls):
        cls.card.stop.set()
        cls.engine.stop()

    def pump(self, sec):
        end = time.perf_counter() + sec
        while time.perf_counter() < end:
            APP.processEvents()
            time.sleep(0.002)

    def test_gapless_chain_plays_every_track_without_gaps_or_jumps(self):
        eng = self.engine
        eng.load(self.paths[0], autoplay=True)
        order = [0]
        start = time.perf_counter()
        while len(order) < len(self.paths) and time.perf_counter() - start < SEC * len(self.paths) + 6:
            self.pump(0.03)
            if not eng.is_crossfading and eng.duration > 0 and eng.duration - eng.get_position_smooth() <= 3.0:
                nxt = len(order)
                if eng.start_gapless(self.paths[nxt], (eng.duration - eng.get_position_smooth()) * 1000):
                    order.append(nxt)
        self.pump(SEC + 1.0)
        t, l, r = (np.array(x) for x in zip(*[b for b in self.card.blocks if b[0] >= start]))
        loud = np.abs(l) > 0.1
        first, last = np.argmax(loud), len(loud) - 1 - np.argmax(loud[::-1])
        self.assertLessEqual(int((~loud[first:last]).sum()), 2, 'silence inside the gapless chain')
        ids = np.abs(l[:, None] - np.array(LEVELS)[None, :]).argmin(1)
        heard = [int(i) for k, i in enumerate(ids[first:last + 1]) if k == 0 or i != ids[first + k - 1]]
        self.assertEqual([i for k, i in enumerate(heard) if k == 0 or i != heard[k - 1]][:len(LEVELS)],
                         list(range(len(LEVELS))))
        steady = loud & (np.abs(l - np.array(LEVELS)[ids]) < 0.004)
        pos = np.where(steady, r / np.maximum(l, 1e-6) * 60.0, np.nan)
        idx = np.where(steady)[0]
        jumps = [(pos[b] - pos[a]) - (t[b] - t[a]) for a, b in zip(idx[:-1], idx[1:])
                 if b - a <= 3 and ids[a] == ids[b]]
        self.assertLess(max(abs(j) for j in jumps), 0.06, 'a track jumped inside the chain')

    def test_chain_after_previous_already_finished_starts_next(self):
        lib = self.mixer.lib
        a, b = self.mixer.decks[0], self.mixer.decks[1]
        lib.na_reset(a.handle); lib.na_reset(b.handle)
        lib.na_drain(a.handle)                       # empty deck at end of stream -> Done
        self.assertTrue(a.finished())
        lib.na_chain(a.handle, b.handle)
        self.assertNotEqual(b.info()[6], 3, 'next deck must not wait for a predecessor that already ended')
        lib.na_reset(a.handle); lib.na_reset(b.handle)


if __name__ == '__main__':
    unittest.main()
