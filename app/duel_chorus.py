"""Bounded, offline chorus/highlight detection on a single background worker."""
from __future__ import annotations
import data_home
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess

from PyQt6.QtCore import QObject, pyqtSignal
from duel_settings import duration


def stamp(track):
    path = str(track.get('path') or track.get('url') or '')
    try:
        stat = Path(path).stat(); path += f'|{stat.st_size}|{stat.st_mtime_ns}'
    except OSError: pass
    return hashlib.sha256(('duel-chorus-v1|'+path).encode('utf-8')).hexdigest()


def explicit_start(track):
    for key in ('chorus_start', 'chorus_start_sec', 'preview_start'):
        value = track.get(key)
        try:
            if isinstance(value, bool): continue
            parts = str(value).split(':'); seconds = 0.
            for part in parts: seconds = seconds * 60 + float(part)
            if math.isfinite(seconds) and 0 <= seconds < (duration(track) - 4 if duration(track) > 4 else 7200):
                return seconds
        except (ValueError, TypeError): pass
    return None


def detect_samples(samples, sample_rate=8000):
    """Find an energetic repeated section; return a highlight when none repeats.

    Two-second chroma/timbre summaries keep the repetition matrix below 300²
    for the maximum ten-minute input. No model downloads or lyrics are needed.
    """
    import numpy as np
    samples = np.asarray(samples, dtype=np.float32)
    if not np.isfinite(samples).all():samples=np.nan_to_num(samples,copy=True)
    seconds = len(samples) / sample_rate
    if seconds < 20 or float(np.max(np.abs(samples), initial=0)) < 1e-5: return (0., 'start')
    window, hop = (2048 if sample_rate>=8000 else 1024), sample_rate // 4
    blocks = np.lib.stride_tricks.sliding_window_view(samples, window)[::hop]
    if len(blocks) < 32: return (0., 'start')
    frequencies = np.fft.rfftfreq(window, 1 / sample_rate)
    chroma = np.zeros((len(blocks), 12), dtype=np.float32)
    masks=[]
    for note in range(36, 96):
        hz = 440 * 2 ** ((note - 69) / 12)
        mask = (frequencies >= hz * .972) & (frequencies < hz * 1.029)
        if np.any(mask):masks.append((note%12,mask))
    edges = np.geomspace(60, sample_rate / 2, 9)
    band_masks=[(frequencies>=a)&(frequencies<b) for a,b in zip(edges,edges[1:])]
    timbre=np.zeros((len(blocks),8),dtype=np.float32);energy=np.zeros(len(blocks),dtype=np.float32);hann=np.hanning(window)
    # Process only 64 FFT windows at a time instead of retaining the full spectrum.
    for offset in range(0,len(blocks),64):
        chunk=blocks[offset:offset+64];spectrum=np.abs(np.fft.rfft(chunk*hann,axis=1));end=offset+len(chunk)
        for note,mask in masks:chroma[offset:end,note]+=spectrum[:,mask].sum(axis=1)
        for index,mask in enumerate(band_masks):timbre[offset:end,index]=spectrum[:,mask].sum(axis=1)
        energy[offset:end]=np.sqrt(np.mean(chunk*chunk,axis=1))
    count = len(blocks) // 8
    features = np.log1p(np.concatenate([chroma, timbre], axis=1)[:count*8].reshape(count,8,20).mean(axis=1))
    features -= features.mean(axis=1, keepdims=True)
    features /= np.maximum(np.linalg.norm(features, axis=1, keepdims=True), 1e-6)
    loudness = energy[:count*8].reshape(count,8).mean(axis=1)
    similarity = np.einsum('ik,jk->ij', features, features)
    # Repetition must last eight seconds and be separated by at least 16 seconds.
    repeated = sum(similarity[k:count-4+k, k:count-4+k] for k in range(4)) / 4
    indices = np.arange(len(repeated)); repeated[np.abs(indices[:,None]-indices[None,:]) < 8] = -1
    confidence = repeated.max(axis=1)
    energy_rank = (loudness - loudness.min()) / max(float(np.ptp(loudness)), 1e-6)
    valid = (indices*2 >= max(8, seconds*.12)) & (indices*2 <= seconds*.72)
    chorus = valid & (confidence >= .84) & (energy_rank[:len(indices)] >= .45)
    score = confidence*.7 + energy_rank[:len(indices)]*.3
    if np.any(chorus):
        best = float(score[chorus].max())
        candidates = np.flatnonzero(chorus & (score >= best-.035))
        return (max(0., float(candidates[0]*2)-.35), 'chorus')
    choices = np.flatnonzero(valid)
    if not len(choices): return (0., 'start')
    best = choices[int(np.argmax(loudness[choices]))]
    return (max(0., float(best*2)-.35), 'highlight')


def analyze(track):
    known = explicit_start(track)
    if known is not None: return (known, 'chorus')
    path = str(track.get('path') or '')
    if not path or path.startswith('stream:') or not Path(path).is_file(): return (0., 'start')
    executable = shutil.which('ffmpeg')
    if not executable:
        for base in (Path(__file__).resolve().parent.parent, Path(os.environ.get('LOCALAPPDATA',''))/'Programs/ECHOES'):
            candidate = base/'ffmpeg/ffmpeg.exe'
            if candidate.is_file(): executable = str(candidate); break
    if not executable: return (0., 'start')
    flags = 0x08000000 | 0x00004000 if os.name == 'nt' else 0
    try:
        from performance import config
        budget=config()['chorus_seconds']
        result = subprocess.run([executable,'-nostdin','-hide_banner','-loglevel','error','-threads','1','-i',path,
                                 '-t',str(budget),'-vn','-ac','1','-ar','4000','-f','f32le','pipe:1'],
                                capture_output=True,timeout=12,creationflags=flags)
        if result.returncode or not result.stdout: return (0., 'start')
        import numpy as np
        return detect_samples(np.frombuffer(result.stdout,dtype='<f4'),sample_rate=4000)
    except (OSError, subprocess.TimeoutExpired, ValueError): return (0., 'start')


class ChorusAnalyzer(QObject):
    ready = pyqtSignal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='duel-chorus')
        self.pending = {}; self.values = {}; self.closed = False
        self.path = data_home.root() / 'duel_chorus.json'
        self.ready.connect(self._finished)
        self.destroyed.connect(lambda: self.shutdown())

    def shutdown(self):
        self.closed = True; self.executor.shutdown(wait=False,cancel_futures=True)

    def value(self, track):
        known = explicit_start(track)
        return (known,'chorus') if known is not None else self.values.get(stamp(track))

    def prepare(self, tracks):
        wanted = {stamp(track) for track in tracks}
        for key,future in list(self.pending.items()):
            if key not in wanted and future.cancel(): self.pending.pop(key,None)
        for track in tracks:
            key = stamp(track)
            if key in self.pending or self.value(track) is not None: continue
            if len(self.pending) >= 3: break
            future = self.executor.submit(self._work,dict(track),key); self.pending[key] = future
            def done(future, key=key):
                if future.cancelled() or self.closed:return
                try:self.ready.emit(key,future.result())
                except Exception:pass
            future.add_done_callback(done)

    def _work(self, track, key):
        try: cache = json.loads(self.path.read_text('utf-8'))
        except (OSError,ValueError): cache = {}
        cached = cache.get(key) if isinstance(cache,dict) else None
        if isinstance(cached,list) and len(cached)==2 and isinstance(cached[0],(int,float)) and math.isfinite(cached[0]) and cached[0]>=0:
            return tuple(cached)
        result = analyze(track)
        if result[1] != 'start':
            cache = cache if isinstance(cache,dict) else {}
            cache[key] = result; cache = dict(list(cache.items())[-1000:])
            try:
                self.path.parent.mkdir(parents=True,exist_ok=True)
                temporary = self.path.with_suffix('.tmp'); temporary.write_text(json.dumps(cache),'utf-8'); temporary.replace(self.path)
            except OSError:pass
        return result

    def _finished(self, key, result):
        self.pending.pop(key,None);self.values[key] = result
        if len(self.values)>256:self.values.pop(next(iter(self.values)))
