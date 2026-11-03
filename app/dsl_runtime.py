# dsl_runtime.py
"""
EchoScript — рантайм: всё, что скрипт может видеть и делать.

  AudioAnalyzer — из PCM, который реально звучит (AudioEngine.get_visual_samples),
                  считает спектр (64 полосы), бас/середину/верх, уровень, пик, «бит»
                  и форму волны; автоусиление (AGC), поэтому значения всегда 0..1.
  PlayerBridge  — состояние плеера для скрипта (трек, позиция, громкость, плейлист)
                  и действия (play/pause/next/seek/…).
  Gfx           — рисование через QPainter (фигуры, текст, картинки, градиенты,
                  трансформации, режимы смешивания).
  Scene         — скомпилированный скрипт: тема, экземпляры виджетов, события,
                  ввод мышью, горячая перезагрузка с сохранением состояния.
  ScriptCanvas  — QWidget, на котором живёт сцена (60 кадров/с).
"""
from __future__ import annotations

import data_home
import math
import random
import time
from frameclock import animation_delta
from pathlib import Path

import numpy as np
from img_load import load_pixmap
from PyQt6.QtCore import Qt, QLineF, QPointF, QRectF, QTimer, pyqtSignal
from PyQt6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter, QPainterPath,
                         QPen, QPixmap, QRadialGradient, QConicalGradient)
from PyQt6.QtWidgets import QWidget

import dsl_lang as L
import dsl_shapes as S
from dsl_lang import EchoError

BANDS = 64


class _MediaPool:
    """Анимированные картинки (GIF/WEBP) и видео для image()/video(). Источник живёт, пока его рисуют:
    через несколько секунд без рисования декодер останавливается (видео не тратит CPU в фоне)."""
    ANIM_EXT = (".gif", ".webp", ".apng")

    def __init__(self):
        self.items = {}                                    # (kind, path) -> [источник, когда рисовали, когда создан]

    def get(self, kind, path):
        key = (kind, path)
        now = time.monotonic()
        it = self.items.get(key)
        if it is not None and it[0] is None and now - it[2] > 5.0:
            self.items.pop(key, None)                      # не открылся — через 5 с попробуем ещё раз
            it = None
        if it is None:
            src = None
            try:
                import theme_layers as TL
                if kind == "video":
                    src = TL.VideoSource(path)
                    if not src.ok:
                        src.stop()
                        src = None
                else:
                    src = TL.MediaSource(path)
                    if not src.valid():
                        src = None
            except Exception as e:                         # noqa: BLE001
                print("[echoscript] media:", e)
                src = None
            it = [src, now, now]
            self.items[key] = it
        it[1] = now
        return it[0]

    def sweep(self, idle=4.0):
        now = time.monotonic()
        for k, it in list(self.items.items()):
            if now - it[1] > idle:
                try:
                    if it[0] is not None:
                        it[0].stop()
                except Exception:                          # noqa: BLE001
                    pass
                self.items.pop(k, None)

    def clear(self):
        self.sweep(-1.0)


MEDIA = _MediaPool()

_THUMBS: dict = {}                                         # путь → QImage (готово) | None (грузится/нет файла)
_THUMB_LOCK = __import__("threading").Lock()
_THUMB_POOL = None
_THUMB_PX = 128


def _thumb(path):
    global _THUMB_POOL
    if not path:
        return None
    with _THUMB_LOCK:
        if path in _THUMBS:
            img = _THUMBS.pop(path)                        # в конец — недавно нужна
            _THUMBS[path] = img
            return img
        _THUMBS[path] = None
        while len(_THUMBS) > 1200:
            _THUMBS.pop(next(iter(_THUMBS)))
    if _THUMB_POOL is None:
        from concurrent.futures import ThreadPoolExecutor
        _THUMB_POOL = ThreadPoolExecutor(2, thread_name_prefix="echo-thumb")

    def work():
        from img_load import read_image
        img = read_image(path, _THUMB_PX * 2) if Path(path).is_file() else QImage()
        if not img.isNull():
            side = min(img.width(), img.height())
            img = img.copy((img.width() - side) // 2, (img.height() - side) // 2, side, side)
            if side > _THUMB_PX:
                img = img.scaled(_THUMB_PX, _THUMB_PX, Qt.AspectRatioMode.IgnoreAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
            img = img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        with _THUMB_LOCK:
            if path in _THUMBS:
                _THUMBS[path] = img if not img.isNull() else None
    _THUMB_POOL.submit(work)
    return None

EV_FIELD = {ev: "on_" + ev for ev in ("init", "frame", "draw", "press", "release", "click", "move", "drag", "wheel",
                                       "beat", "track", "key", "enter", "leave", "resize")}


def svg_path(d: str) -> QPainterPath:
    """Разбор SVG path data в QPainterPath (команды M L H V C S Q T A Z, строчные — относительные)."""
    import re as _re
    toks = _re.findall(r"[MmLlHhVvCcSsQqTtAaZz]|-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", d or "")
    pp = QPainterPath()
    i = 0
    cmd = None
    cx = cy = sx = sy = 0.0
    lc = None                                              # последняя контрольная точка (для S/T)

    def num():
        nonlocal i
        v = float(toks[i])
        i += 1
        return v
    try:
        while i < len(toks):
            t = toks[i]
            if t.isalpha():
                cmd = t
                i += 1
                if cmd in "Zz":
                    pp.closeSubpath()
                    cx, cy = sx, sy
                    lc = None
                    continue
            elif cmd is None:
                break
            rel = cmd.islower()
            C = cmd.upper()
            ox, oy = (cx, cy) if rel else (0.0, 0.0)
            if C == "M":
                cx, cy = ox + num(), oy + num()
                pp.moveTo(cx, cy)
                sx, sy = cx, cy
                cmd = "l" if rel else "L"                  # дальше пары — это lineto
                lc = None
            elif C == "L":
                cx, cy = ox + num(), oy + num()
                pp.lineTo(cx, cy)
                lc = None
            elif C == "H":
                cx = (cx if rel else 0.0) + num()
                pp.lineTo(cx, cy)
                lc = None
            elif C == "V":
                cy = (cy if rel else 0.0) + num()
                pp.lineTo(cx, cy)
                lc = None
            elif C == "C":
                x1, y1, x2, y2 = ox + num(), oy + num(), ox + num(), oy + num()
                cx, cy = ox + num(), oy + num()
                pp.cubicTo(x1, y1, x2, y2, cx, cy)
                lc = ("c", x2, y2)
            elif C == "S":
                x1, y1 = (2 * cx - lc[1], 2 * cy - lc[2]) if lc and lc[0] == "c" else (cx, cy)
                x2, y2 = ox + num(), oy + num()
                cx, cy = ox + num(), oy + num()
                pp.cubicTo(x1, y1, x2, y2, cx, cy)
                lc = ("c", x2, y2)
            elif C == "Q":
                x1, y1 = ox + num(), oy + num()
                cx, cy = ox + num(), oy + num()
                pp.quadTo(x1, y1, cx, cy)
                lc = ("q", x1, y1)
            elif C == "T":
                x1, y1 = (2 * cx - lc[1], 2 * cy - lc[2]) if lc and lc[0] == "q" else (cx, cy)
                cx, cy = ox + num(), oy + num()
                pp.quadTo(x1, y1, cx, cy)
                lc = ("q", x1, y1)
            elif C == "A":
                rx, ry, rot, large, sweep = abs(num()), abs(num()), num(), num(), num()
                ex, ey = ox + num(), oy + num()
                _svg_arc(pp, cx, cy, rx, ry, rot, bool(large), bool(sweep), ex, ey)
                cx, cy = ex, ey
                lc = None
            else:
                i += 1
    except (IndexError, ValueError):
        pass                                               # обрывок в конце — рисуем то, что разобрали
    return pp


def _svg_arc(pp, x1, y1, rx, ry, phi, large, sweep, x2, y2):
    """Дуга SVG (endpoint parameterization) → кубические кривые."""
    if rx == 0 or ry == 0:
        pp.lineTo(x2, y2)
        return
    ph = math.radians(phi)
    cp, sp = math.cos(ph), math.sin(ph)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p, y1p = cp * dx + sp * dy, -sp * dx + cp * dy
    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1:
        k = math.sqrt(lam)
        rx, ry = rx * k, ry * k
    num_ = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    co = math.sqrt(max(0.0, num_ / den)) if den else 0.0
    if large == sweep:
        co = -co
    cxp, cyp = co * rx * y1p / ry, -co * ry * x1p / rx
    ccx = cp * cxp - sp * cyp + (x1 + x2) / 2
    ccy = sp * cxp + cp * cyp + (y1 + y2) / 2

    def ang(ux, uy, vx, vy):
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a
    t1 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dt = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and dt > 0:
        dt -= 2 * math.pi
    elif sweep and dt < 0:
        dt += 2 * math.pi
    n = max(1, int(math.ceil(abs(dt) / (math.pi / 2))))
    step = dt / n
    k = 4 / 3 * math.tan(step / 4)
    t = t1
    for _ in range(n):
        c1, s1 = math.cos(t), math.sin(t)
        c2, s2 = math.cos(t + step), math.sin(t + step)
        p1 = (c1 - k * s1, s1 + k * c1)
        p2 = (c2 + k * s2, s2 - k * c2)
        p3 = (c2, s2)

        def tr(px, py):
            return ccx + rx * px * cp - ry * py * sp, ccy + rx * px * sp + ry * py * cp
        a1, b1 = tr(*p1)
        a2, b2 = tr(*p2)
        a3, b3 = tr(*p3)
        pp.cubicTo(a1, b1, a2, b2, a3, b3)
        t += step


# ══════════════════════════════════════════════════════════════════════════ #
#  Звук
# ══════════════════════════════════════════════════════════════════════════ #

class AudioAnalyzer:
    N = 2048

    def __init__(self, engine):
        self.engine = engine
        self.fft = [0.0] * BANDS
        self.wave = [0.0] * 128
        self.bass = self.mid = self.high = self.level = self.peak = 0.0
        self.beat = False
        self.beat_power = 0.0
        self._rmax = 1e-3
        self._bmax = {"bass": 1e-3, "mid": 1e-3, "high": 1e-3}
        self._lvl_max = 1e-3
        self._bass_avg = 0.0
        self._cool = 0.0
        self._edges = None
        self._sr = None
        self._win = np.hanning(self.N).astype(np.float32)

    def _setup(self, sr):
        self._sr = sr
        freqs = np.fft.rfftfreq(self.N, 1.0 / sr)
        edges_hz = np.geomspace(32.0, min(16000.0, sr / 2 - 1), BANDS + 1)
        idx = np.searchsorted(freqs, edges_hz)
        idx = np.maximum.accumulate(np.maximum(idx, 1))
        for i in range(1, len(idx)):                      # каждая полоса — хотя бы 1 бин
            if idx[i] <= idx[i - 1]:
                idx[i] = idx[i - 1] + 1
        self._edges = np.minimum(idx, len(freqs) - 1)
        self._freqs = freqs

        def rng(lo, hi):
            return slice(int(np.searchsorted(freqs, lo)), max(int(np.searchsorted(freqs, hi)), 1))
        self._sl = {"bass": rng(30, 160), "mid": rng(250, 2500), "high": rng(4000, 13000)}

    def update(self, dt: float, playing: bool):
        s = None
        try:
            if self.engine is not None and playing:
                s = self.engine.get_visual_samples(self.N)
        except Exception:                                  # noqa: BLE001
            s = None
        decay = 0.0 if s is not None else min(1.0, dt * 4.0)
        if s is None or len(s) < self.N:
            self.fft = [v * (1 - decay) for v in self.fft]
            self.bass *= 1 - decay
            self.mid *= 1 - decay
            self.high *= 1 - decay
            self.level *= 1 - decay
            self.peak *= 1 - decay
            self.wave = [v * (1 - decay) for v in self.wave]
            self.beat = False
            return
        try:
            sr = int(getattr(self.engine, "visual_sample_rate", 44100) or 44100)
        except Exception:                                  # noqa: BLE001
            sr = 44100
        if sr != self._sr:
            self._setup(sr)
        x = np.asarray(s, dtype=np.float32)
        mag = np.abs(np.fft.rfft(x * self._win)).astype(np.float32)
        csum = np.concatenate([[0.0], np.cumsum(mag)])
        e = self._edges
        band = (csum[e[1:]] - csum[e[:-1]]) / np.maximum(e[1:] - e[:-1], 1)
        band = np.log1p(band * 4.0)
        m = float(band.max()) if band.size else 0.0
        self._rmax = max(self._rmax * (1 - 0.35 * dt), m, 1e-3)       # плавное автоусиление
        tgt = np.clip(band / self._rmax, 0.0, 1.0)
        cur = np.asarray(self.fft, dtype=np.float32)
        up = tgt > cur
        k_up, k_dn = min(1.0, dt * 28.0), min(1.0, dt * 9.0)
        cur = np.where(up, cur + (tgt - cur) * k_up, cur + (tgt - cur) * k_dn)
        self.fft = [float(v) for v in cur]
        vals = {}
        for name, sl in self._sl.items():
            v = float(np.log1p(mag[sl].mean() * 4.0)) if sl.stop > sl.start else 0.0
            self._bmax[name] = max(self._bmax[name] * (1 - 0.3 * dt), v, 1e-3)
            vals[name] = min(1.0, v / self._bmax[name])
        for name in ("bass", "mid", "high"):
            old = getattr(self, name)
            t = vals[name]
            setattr(self, name, old + (t - old) * (k_up if t > old else k_dn))
        rms = float(np.sqrt(np.mean(x * x)))
        self._lvl_max = max(self._lvl_max * (1 - 0.25 * dt), rms, 1e-4)
        lvl = min(1.0, rms / self._lvl_max)
        self.level += (lvl - self.level) * (k_up if lvl > self.level else k_dn)
        pk = min(1.0, float(np.max(np.abs(x))))
        self.peak = max(pk, self.peak - dt * 0.8)
        step = len(x) // 128
        self.wave = [float(v) for v in x[: step * 128: step]]
        # бит: всплеск баса над его средним
        self._bass_avg += (vals["bass"] - self._bass_avg) * min(1.0, dt * 2.5)
        self._cool = max(0.0, self._cool - dt)
        self.beat = False
        if vals["bass"] > 0.35 and vals["bass"] > self._bass_avg * 1.32 and self._cool <= 0:
            self.beat = True
            self.beat_power = min(1.0, (vals["bass"] - self._bass_avg) * 2.5 + 0.35)
            self._cool = 0.22

    def as_dict(self):
        return {"fft": self.fft, "wave": self.wave, "bass": self.bass, "mid": self.mid, "high": self.high,
                "level": self.level, "peak": self.peak, "beat": self.beat, "beat_power": self.beat_power,
                "bands": BANDS}


# ══════════════════════════════════════════════════════════════════════════ #
#  Плеер
# ══════════════════════════════════════════════════════════════════════════ #

class PlayerBridge:
    """Чтение состояния плеера и действия. win — MainWindow (может быть None в тестах)."""

    def __init__(self, win):
        self.win = win
        self._pl_key = None
        self._pl_cache = []

    def _tracks(self):
        w = self.win
        if w is None:
            return []
        if w.queue_context == "library":
            return w.library
        return w.playlists.get(w.queue_context, {}).get("tracks", [])

    def current(self):
        w = self.win
        tr = self._tracks()
        if w is not None and 0 <= w.current_index < len(tr):
            return tr[w.current_index]
        return None

    def track(self):
        t = self.current() or {}
        eng = getattr(self.win, "engine", None)
        pos = dur = 0.0
        try:
            pos = float(eng.get_position() or 0) if eng is not None else 0.0
            dur = float(eng.duration or t.get("duration") or 0) if eng is not None else 0.0
        except Exception:                                  # noqa: BLE001
            pass
        title = t.get("title", "") or ""
        try:
            from lyrics import sanitize_title
            title = sanitize_title(title)
        except Exception:                                  # noqa: BLE001
            pass
        return {"title": title, "artist": t.get("artist", "") or "", "album": t.get("album", "") or "",
                "cover": t.get("cover", "") or "", "path": str(t.get("path", "") or ""),
                "duration": dur, "position": pos, "progress": (pos / dur) if dur > 0 else 0.0,
                "loaded": bool(t)}

    def player(self):
        w = self.win
        if w is None:
            return {"playing": False, "volume": 0.0, "shuffle": False, "repeat": 0}
        eng = w.engine
        try:
            playing = bool(eng.is_playing())
        except Exception:                                  # noqa: BLE001
            playing = False
        return {"playing": playing, "volume": float(w.settings.get("volume", 80)) / 100.0,
                "shuffle": bool(getattr(w, "shuffle", False)), "repeat": int(getattr(w, "repeat_mode", 0) or 0)}

    def playlist(self):
        w = self.win
        tr = self._tracks()
        name = "Библиотека" if (w is None or w.queue_context == "library") else w.queue_context
        key = (name, len(tr), id(tr))
        if key != self._pl_key:
            self._pl_key = key
            try:
                from lyrics import sanitize_title
            except Exception:                              # noqa: BLE001
                def sanitize_title(s):
                    return s
            self._pl_cache = [{"title": sanitize_title(t.get("title", "") or ""), "artist": t.get("artist", "") or "",
                               "duration": float(t.get("duration") or 0), "cover": t.get("cover", "") or ""}
                              for t in tr]
        return {"name": name, "tracks": self._pl_cache, "index": getattr(w, "current_index", -1) if w else -1,
                "count": len(self._pl_cache)}

    def lyrics(self):
        """Текст песни: строки, номер текущей (по таймингам) и сама строка."""
        lv = getattr(self.win, "lyrics_view", None)
        lines = getattr(lv, "_lines", None) or []
        if id(lines) != getattr(self, "_ly_key", None):
            self._ly_key = id(lines)
            self._ly_cache = [str(ln.get("text", "")) if isinstance(ln, dict) else str(ln) for ln in lines]
        idx = -1
        try:
            idx = int(getattr(getattr(lv, "_text_panel", None), "_active_idx", -1))
        except Exception:                                  # noqa: BLE001
            pass
        txt = self._ly_cache[idx] if 0 <= idx < len(self._ly_cache) else ""
        return {"lines": self._ly_cache, "index": idx, "line": txt, "count": len(self._ly_cache),
                "timed": bool(getattr(lv, "_has_timings", False))}

    # ── вся медиатека и плейлисты ──
    @staticmethod
    def _san():
        try:
            from lyrics import sanitize_title
            return sanitize_title
        except Exception:                                  # noqa: BLE001
            return lambda s: s

    def _pack(self, tracks, cache_attr):
        key = (len(tracks), id(tracks), id(tracks[-1]) if tracks else 0)
        ent = getattr(self, cache_attr, None)
        if ent is not None and ent[0] == key:
            return ent[1]
        san = self._san()
        out = [{"title": san(t.get("title", "") or ""), "artist": t.get("artist", "") or "",
                "album": t.get("album", "") or "", "duration": float(t.get("duration") or 0),
                "cover": t.get("cover", "") or "", "path": str(t.get("path", "") or ""), "index": i,
                "year": t.get("year", "") or "", "genre": t.get("genre", "") or ""}
               for i, t in enumerate(tracks)]
        setattr(self, cache_attr, (key, out))
        return out

    def library(self):
        w = self.win
        return self._pack(w.library if w is not None else [], "_lib_cache")

    def library_api(self) -> dict:
        w = self.win

        def guard(fn):
            def f(*a, **k):
                if w is None:
                    return None
                try:
                    return fn(*a, **k)
                except EchoError:
                    raise
                except Exception as e:                     # noqa: BLE001
                    raise EchoError(f"не получилось: {e}")
            return f

        def playlists():
            return [n for n in (w.playlists or {}).keys()]

        def playlist_tracks(name):
            if name in (None, "", "library", "Библиотека"):
                return self.library()
            pl = (w.playlists or {}).get(str(name))
            if pl is None:
                return []
            return self._pack(pl.get("tracks", []), "_plc_" + str(abs(hash(str(name))) % 10 ** 8))

        def play_track(i, context="library"):
            ctx = "library" if context in (None, "", "Библиотека") else str(context)
            w._play_index(int(i), ctx)

        def play_path(path):
            path = str(path or "")
            for i, t in enumerate(w.library):
                if str(t.get("path", "")) == path:
                    w._play_index(i, "library")
                    return True
            for name, pl in (w.playlists or {}).items():
                for i, t in enumerate(pl.get("tracks", [])):
                    if str(t.get("path", "")) == path:
                        w._play_index(i, name)
                        return True
            return False

        def _raw(item):
            if isinstance(item, dict) and item.get("path"):
                p = str(item["path"])
                for t in w.library:
                    if str(t.get("path", "")) == p:
                        return t
                return None
            if isinstance(item, (int, float)) and 0 <= int(item) < len(w.library):
                return w.library[int(item)]
            if isinstance(item, str):
                for t in w.library:
                    if str(t.get("path", "")) == item:
                        return t
            return None

        def queue_add(item, next=False):                     # noqa: A002
            t = _raw(item)
            if t is not None:
                w.queue_add([t], next_=bool(next))
                return True
            return False

        def search_tracks(q, limit=200):
            q = str(q or "").lower().strip()
            lib = self.library()
            if not q:
                return lib[:int(limit)]
            words = q.split()
            out = []
            for t in lib:
                hay = f"{t['title']} {t['artist']} {t['album']}".lower()
                if all(wd in hay for wd in words):
                    out.append(t)
                    if len(out) >= int(limit):
                        break
            return out

        def _group(key):
            cnt = {}
            for t in self.library():
                k = t[key] or "—"
                cnt[k] = cnt.get(k, 0) + 1
            return [{"name": k, "count": n} for k, n in sorted(cnt.items(), key=lambda kv: kv[0].lower())]

        def by_artist(name):
            return [t for t in self.library() if t["artist"] == name]

        def by_album(name):
            return [t for t in self.library() if t["album"] == name]

        def set_shuffle(on=True):
            if bool(getattr(w, "shuffle", False)) != bool(on):
                w.btn_shuffle.click()

        def set_repeat(mode=1):
            for _ in range(3):
                if int(getattr(w, "repeat_mode", 0) or 0) == int(mode) % 3:
                    return
                w._cycle_repeat()

        def seek_to(sec):
            w.engine.seek(max(0.0, float(sec)))

        def set_theme(name):
            w._apply_theme(str(name))

        def themes():
            try:
                from themes import THEMES
                return list(THEMES.keys())
            except Exception:                              # noqa: BLE001
                return []

        def toast(text):
            w._toast(L._to_str(text))

        def queue():
            return self._pack(list(getattr(w, "user_queue", []) or []), "_q_cache")

        return {
            "library": guard(lambda: self.library()), "playlists": guard(playlists),
            "playlist_tracks": guard(playlist_tracks), "play_track": guard(play_track),
            "play_path": guard(play_path), "queue_add": guard(queue_add), "queue": guard(queue),
            "search_tracks": guard(search_tracks), "artists": guard(lambda: _group("artist")),
            "albums": guard(lambda: _group("album")), "by_artist": guard(by_artist), "by_album": guard(by_album),
            "set_shuffle": guard(set_shuffle), "set_repeat": guard(set_repeat), "seek_to": guard(seek_to),
            "set_theme": guard(set_theme), "themes": guard(themes), "toast": guard(toast),
        }

    # действия
    def actions(self):
        w = self.win

        def guard(fn):
            def f(*a):
                if w is None:
                    return None
                try:
                    return fn(*a)
                except Exception as e:                     # noqa: BLE001
                    raise EchoError(f"действие не выполнилось: {e}")
            return f

        def seek(frac):
            dur = float(w.engine.duration or 0)
            if dur > 0:
                w.engine.seek(max(0.0, min(1.0, float(frac))) * dur)

        def set_volume(v):
            w.vol.setValue(int(max(0.0, min(1.0, float(v))) * 100))

        def play_index(i):
            ctx = w.queue_context or "library"
            w._play_index(int(i), ctx)

        def mute():
            v = w.vol.value()
            if v > 0:
                self._unmute = v
                w.vol.setValue(0)
            else:
                w.vol.setValue(getattr(self, "_unmute", 80))

        def export_fx():
            if hasattr(w, "export_effects"):
                t = self.current()
                if t:
                    w.export_effects([t])

        return {
            "toggle": guard(lambda: w.toggle_play()),
            "play": guard(lambda: None if w.engine.is_playing() else w.toggle_play()),
            "pause": guard(lambda: w.toggle_play() if w.engine.is_playing() else None),
            "next": guard(lambda: w.next_track()),
            "prev": guard(lambda: w.prev_track()),
            "seek": guard(seek),
            "set_volume": guard(set_volume),
            "play_index": guard(play_index),
            "toggle_shuffle": guard(lambda: w.btn_shuffle.click()),
            # шаффл по исполнителю: без имени — исполнитель текущего трека; artist_menu() — выбор из списка
            "artist_shuffle": guard(lambda name="": w.artist_shuffle(L._to_str(name) if name else None)),
            "artist_menu": guard(lambda: w.artist_shuffle_menu()),
            # трек на компьютере: меню (играть, проводник, теги) и «показать в проводнике»
            "track_menu": guard(lambda path: w.track_menu_for(L._to_str(path))),
            "reveal": guard(lambda path: w.reveal_in_explorer(L._to_str(path))),
            "cycle_repeat": guard(lambda: w._cycle_repeat()),
            "export_fx": guard(export_fx),
            # кнопки интерфейса плеера — для своих кнопок в конструкторе тем
            "lyrics_mode": guard(lambda: w._toggle_lyrics_mode()),
            "show_queue": guard(lambda: w._show_queue_popup()),
            "cover_mode": guard(lambda: w._toggle_cover_mode()),
            "clip_mode": guard(lambda: w._toggle_clip_mode()),
            "music_hub": guard(lambda: w._open_hub()),
            "theme_builder": guard(lambda: w._open_theme_studio()),
            "fullscreen": guard(lambda: w._toggle_fullscreen()),
            "theme_menu": guard(lambda: w._ui_layers_menu(__import__("PyQt6.QtGui", fromlist=["QCursor"]).QCursor.pos())),
            "add_files": guard(lambda: w._add_files()),
            "downloader": guard(lambda: w._open_downloader()),
            "profile": guard(lambda: w._open_profile()),
            "mute": guard(mute),
        }


# ══════════════════════════════════════════════════════════════════════════ #
#  Рисование
# ══════════════════════════════════════════════════════════════════════════ #

_COLOR_CACHE: dict = {}


def to_qcolor(v) -> QColor:
    if v.__class__ is QColor:
        return v
    c = _COLOR_CACHE.get(v)
    if c is not None:
        return c
    if isinstance(v, str):
        s = v.strip()
        if s.startswith("#") and len(s) == 9:              # #rrggbbaa (как в CSS)
            c = QColor(int(s[1:3], 16), int(s[3:5], 16), int(s[5:7], 16), int(s[7:9], 16))
        else:
            c = QColor(s)
    elif isinstance(v, (int, float)):
        g = int(max(0, min(255, v)))
        c = QColor(g, g, g)
    else:
        raise EchoError(f"это не цвет: {v!r}")
    if not c.isValid():
        raise EchoError(f"непонятный цвет «{v}»")
    if len(_COLOR_CACHE) > 4096:
        _COLOR_CACHE.clear()
    _COLOR_CACHE[v] = c
    return c


def _hex(c: QColor) -> str:
    return "#%02x%02x%02x%02x" % (c.red(), c.green(), c.blue(), c.alpha())


# Цветовые функции возвращают готовый QColor (а не строку «#…»): цвет, меняющийся каждый кадр,
# не нужно заново разбирать из текста — это заметно ускоряет визуализаторы.

def _cl(x):
    return 0 if x < 0 else 255 if x > 255 else int(x + 0.5)


def rgb(r, g, b, a=1.0):
    return QColor(_cl(r), _cl(g), _cl(b), _cl(a * 255))


def hsv(h, s=1.0, v=1.0, a=1.0):
    return QColor.fromHsvF((float(h) % 360) / 360.0, max(0.0, min(1.0, float(s))), max(0.0, min(1.0, float(v))),
                           max(0.0, min(1.0, float(a))))


def mix(c1, c2, t=0.5):
    a, b = to_qcolor(c1), to_qcolor(c2)
    t = 0.0 if t < 0 else 1.0 if t > 1 else float(t)
    return QColor(_cl(a.red() + (b.red() - a.red()) * t), _cl(a.green() + (b.green() - a.green()) * t),
                  _cl(a.blue() + (b.blue() - a.blue()) * t), _cl(a.alpha() + (b.alpha() - a.alpha()) * t))


def with_alpha(c, a):
    q = to_qcolor(c)
    a = 0.0 if a < 0 else 1.0 if a > 1 else float(a)
    return QColor(q.red(), q.green(), q.blue(), _cl(a * 255))


def color_hex(c):
    return _hex(to_qcolor(c))


_BLEND = {"normal": QPainter.CompositionMode.CompositionMode_SourceOver,
          "add": QPainter.CompositionMode.CompositionMode_Plus,
          "screen": QPainter.CompositionMode.CompositionMode_Screen,
          "multiply": QPainter.CompositionMode.CompositionMode_Multiply,
          "overlay": QPainter.CompositionMode.CompositionMode_Overlay,
          "lighten": QPainter.CompositionMode.CompositionMode_Lighten,
          "darken": QPainter.CompositionMode.CompositionMode_Darken,
          "difference": QPainter.CompositionMode.CompositionMode_Difference}


class Gfx:
    """Состояние кисти/пера и функции рисования для текущего QPainter."""

    def __init__(self, scene):
        self.scene = scene
        self.p: QPainter | None = None
        self._img_cache: dict = {}
        self._font_cache: dict = {}
        self.reset()

    def reset(self):
        self.brush = QBrush(QColor(255, 255, 255))
        self.pen = Qt.PenStyle.NoPen
        self.stack = []

    def need(self):
        if self.p is None:
            raise EchoError("рисовать можно только в обработчике on draw")
        return self.p

    def _apply(self):
        p = self.p
        p.setBrush(self.brush)
        p.setPen(self.pen)

    # ── кисть/перо ──
    def _brush_of(self, v):
        if isinstance(v, dict) and v.get("__grad__"):
            return QBrush(self._make_grad(v))
        return QBrush(to_qcolor(v))

    def _make_grad(self, d):
        k = d["__grad__"]
        if k == "linear":
            g = QLinearGradient(*d["pts"])
        elif k == "radial":
            cx, cy, r = d["pts"]
            g = QRadialGradient(cx, cy, r)
        else:
            cx, cy, ang = d["pts"]
            g = QConicalGradient(cx, cy, ang)
        stops = d["stops"]
        n = len(stops)
        for i, s in enumerate(stops):
            if isinstance(s, list) and len(s) == 2:
                g.setColorAt(max(0.0, min(1.0, float(s[0]))), to_qcolor(s[1]))
            else:
                g.setColorAt(i / max(1, n - 1), to_qcolor(s))
        return g

    def fill(self, c):
        self.brush = self._brush_of(c)

    def nofill(self):
        self.brush = QBrush(Qt.BrushStyle.NoBrush)

    def stroke(self, c, width=1.0, cap="round"):
        pen = QPen(self._brush_of(c), float(width))
        pen.setCapStyle({"flat": Qt.PenCapStyle.FlatCap, "square": Qt.PenCapStyle.SquareCap}
                        .get(cap, Qt.PenCapStyle.RoundCap))
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        self.pen = pen

    def nostroke(self):
        self.pen = Qt.PenStyle.NoPen

    def alpha(self, a):
        self.need().setOpacity(max(0.0, min(1.0, float(a))))

    def blend(self, mode="normal"):
        m = _BLEND.get(str(mode))
        if m is None:
            raise EchoError(f"неизвестный режим смешивания «{mode}» (есть: {', '.join(_BLEND)})")
        self.need().setCompositionMode(m)

    # ── трансформации ──
    def save(self):
        self.need().save()
        self.stack.append((self.brush, self.pen))

    def restore(self):
        p = self.need()
        if not self.stack:
            raise EchoError("restore() без парного save()")
        p.restore()
        self.brush, self.pen = self.stack.pop()

    def translate(self, x, y):
        self.need().translate(float(x), float(y))

    def rotate(self, deg):
        self.need().rotate(float(deg))

    def scale(self, sx, sy=None):
        self.need().scale(float(sx), float(sx if sy is None else sy))

    def clip(self, x, y, w, h, r=0):
        path = QPainterPath()
        path.addRoundedRect(QRectF(float(x), float(y), float(w), float(h)), float(r), float(r))
        self.need().setClipPath(path, Qt.ClipOperation.IntersectClip)

    # ── фигуры ──
    def clear(self, c):
        p = self.need()
        p.save()
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        r = p.clipBoundingRect() if p.hasClipping() else QRectF(-1e5, -1e5, 2e5, 2e5)
        p.fillRect(r, to_qcolor(c))
        p.restore()

    def rect(self, x, y, w, h, r=0):
        p = self.need()
        self._apply()
        if r:
            p.drawRoundedRect(QRectF(float(x), float(y), float(w), float(h)), float(r), float(r))
        else:
            p.drawRect(QRectF(float(x), float(y), float(w), float(h)))

    def circle(self, x, y, r):
        p = self.need()
        self._apply()
        r = float(r)
        p.drawEllipse(QPointF(float(x), float(y)), r, r)

    def ellipse(self, x, y, rx, ry):
        p = self.need()
        self._apply()
        p.drawEllipse(QPointF(float(x), float(y)), float(rx), float(ry))

    def line(self, x1, y1, x2, y2):
        p = self.need()
        p.setPen(self.pen if self.pen != Qt.PenStyle.NoPen else QPen(self.brush, 1.0))
        p.drawLine(QPointF(float(x1), float(y1)), QPointF(float(x2), float(y2)))

    def lines(self, coords):
        """Много отрезков одним вызовом: [x1, y1, x2, y2,  x1, y1, x2, y2, …] — намного быстрее line() в цикле."""
        p = self.need()
        if not isinstance(coords, list):
            raise EchoError("lines(): нужен список [x1, y1, x2, y2, …]")
        n = len(coords) // 4
        if n <= 0:
            return
        p.setPen(self.pen if self.pen != Qt.PenStyle.NoPen else QPen(self.brush, 1.0))
        c = coords
        p.drawLines([QLineF(c[i], c[i + 1], c[i + 2], c[i + 3]) for i in range(0, n * 4, 4)])

    @staticmethod
    def _pts(points):
        if not isinstance(points, list):
            raise EchoError("точки — список [x1, y1, x2, y2, …]")
        if points and isinstance(points[0], list):
            return [QPointF(float(a[0]), float(a[1])) for a in points]
        return [QPointF(float(points[i]), float(points[i + 1])) for i in range(0, len(points) - 1, 2)]

    def poly(self, points, closed=True):
        p = self.need()
        pts = self._pts(points)
        if len(pts) < 2:
            return
        path = QPainterPath(pts[0])
        for q in pts[1:]:
            path.lineTo(q)
        if closed:
            path.closeSubpath()
            self._apply()
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(self.pen if self.pen != Qt.PenStyle.NoPen else QPen(self.brush, 1.0))
        p.drawPath(path)

    def curve(self, points, closed=False, tension=0.5):
        """Плавная кривая через точки (Catmull-Rom → кубические Безье)."""
        p = self.need()
        pts = self._pts(points)
        n = len(pts)
        if n < 2:
            return
        t = float(tension)
        path = QPainterPath(pts[0])
        rng = range(n) if closed else range(n - 1)
        for i in rng:
            p0 = pts[(i - 1) % n] if (closed or i > 0) else pts[i]
            p1 = pts[i]
            p2 = pts[(i + 1) % n]
            p3 = pts[(i + 2) % n] if (closed or i + 2 < n) else p2
            c1 = QPointF(p1.x() + (p2.x() - p0.x()) * t / 3, p1.y() + (p2.y() - p0.y()) * t / 3)
            c2 = QPointF(p2.x() - (p3.x() - p1.x()) * t / 3, p2.y() - (p3.y() - p1.y()) * t / 3)
            path.cubicTo(c1, c2, p2)
        if closed:
            path.closeSubpath()
            self._apply()
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(self.pen if self.pen != Qt.PenStyle.NoPen else QPen(self.brush, 1.0))
        p.drawPath(path)

    def arc(self, x, y, r, a0, a1):
        p = self.need()
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(self.pen if self.pen != Qt.PenStyle.NoPen else QPen(self.brush, 1.0))
        r = float(r)
        p.drawArc(QRectF(float(x) - r, float(y) - r, 2 * r, 2 * r), int(-float(a0) * 16),
                  int(-(float(a1) - float(a0)) * 16))

    def ring(self, x, y, r1, r2):
        p = self.need()
        path = QPainterPath()
        path.addEllipse(QPointF(float(x), float(y)), float(r2), float(r2))
        path.addEllipse(QPointF(float(x), float(y)), float(r1), float(r1))
        path.setFillRule(Qt.FillRule.OddEvenFill)
        self._apply()
        p.drawPath(path)

    def star(self, x, y, r1, r2, n=5, rot=0):
        n = max(2, int(n))
        pts = []
        for i in range(n * 2):
            a = math.radians(float(rot)) - math.pi / 2 + i * math.pi / n
            rr = float(r1) if i % 2 == 0 else float(r2)
            pts += [float(x) + math.cos(a) * rr, float(y) + math.sin(a) * rr]
        self.poly(pts, True)

    def ngon(self, x, y, r, n=6, rot=0):
        n = max(3, int(n))
        pts = []
        for i in range(n):
            a = math.radians(float(rot)) - math.pi / 2 + i * 2 * math.pi / n
            pts += [float(x) + math.cos(a) * float(r), float(y) + math.sin(a) * float(r)]
        self.poly(pts, True)

    _GLOW: dict = {}

    def glow(self, x, y, r, c, strength=1.0):
        """Мягкое свечение. Рисуется готовым спрайтом (кэш по цвету), масштабируется под радиус —
        в десятки раз быстрее, чем радиальный градиент на каждый вызов."""
        p = self.need()
        r = float(r)
        if r < 0.5:
            return
        q = to_qcolor(c)
        a = max(0.0, min(1.0, q.alphaF() * float(strength)))
        if a <= 0.003:
            return
        key = (q.red(), q.green(), q.blue())
        pm = self._GLOW.get(key)
        if pm is None:
            S = 128
            pm = QPixmap(S, S)
            pm.fill(Qt.GlobalColor.transparent)
            gp = QPainter(pm)
            gp.setRenderHint(QPainter.RenderHint.Antialiasing)
            g = QRadialGradient(S / 2, S / 2, S / 2)
            base = QColor(q.red(), q.green(), q.blue(), 255)
            g.setColorAt(0.0, base)
            mid = QColor(base)
            mid.setAlphaF(0.35)
            g.setColorAt(0.35, mid)
            end = QColor(base)
            end.setAlpha(0)
            g.setColorAt(1.0, end)
            gp.setPen(Qt.PenStyle.NoPen)
            gp.setBrush(QBrush(g))
            gp.drawEllipse(0, 0, S, S)
            gp.end()
            if len(self._GLOW) > 64:
                self._GLOW.clear()
            self._GLOW[key] = pm
        op = p.opacity()
        p.setOpacity(op * a)
        p.drawPixmap(QRectF(float(x) - r, float(y) - r, 2 * r, 2 * r), pm, QRectF(0, 0, pm.width(), pm.height()))
        p.setOpacity(op)

    # ── текст ──
    def _font(self, size, weight, family):
        key = (round(float(size), 1), int(weight), family)
        f = self._font_cache.get(key)
        if f is None:
            f = QFont()
            fam = family or self.scene.theme.get("font")
            if fam:
                f.setFamilies([fam, "Segoe UI", "Arial"])
            f.setPixelSize(max(1, int(round(float(size)))))
            f.setWeight(QFont.Weight(max(100, min(900, int(weight)))))
            if len(self._font_cache) > 256:
                self._font_cache.clear()
            self._font_cache[key] = f
        return f

    def text(self, s, x, y, size=14, align="left", weight=500, font=None, maxw=0):
        p = self.need()
        f = self._font(size, weight, font)
        p.setFont(f)
        col = self.brush.color() if self.brush.style() != Qt.BrushStyle.NoBrush else QColor(255, 255, 255)
        p.setPen(col)
        s = L._to_str(s)
        fm = QFontMetricsF(f)
        if maxw and float(maxw) > 0:
            s = fm.elidedText(s, Qt.TextElideMode.ElideRight, float(maxw))
        w = fm.horizontalAdvance(s)
        x = float(x)
        if align == "center":
            x -= w / 2
        elif align == "right":
            x -= w
        p.drawText(QPointF(x, float(y) + fm.ascent() * 0.5 - fm.descent() * 0.5), s)

    def textwidth(self, s, size=14, weight=500, font=None):
        return QFontMetricsF(self._font(size, weight, font)).horizontalAdvance(L._to_str(s))

    # ── картинки ──
    def _pix(self, path):
        pm = self._img_cache.pop(path, None)
        if pm is None:
            pm = load_pixmap(path, 2048) if path and Path(path).exists() else QPixmap()
            # бюджет по памяти, а не по штукам: 48 обложек 1200² — это ~280 МБ
            total = sum(q.width() * q.height() * 4 for q in self._img_cache.values()) + pm.width() * pm.height() * 4
            while self._img_cache and (len(self._img_cache) >= 48 or total > 64 * 2 ** 20):
                old = self._img_cache.pop(next(iter(self._img_cache)))
                total -= old.width() * old.height() * 4
        self._img_cache[path] = pm                         # в конец — недавно нужна
        return pm

    def _blit(self, p, pic, x, y, w, h, r, is_image=False):
        """Нарисовать pic в прямоугольник с обрезкой по центру (без искажений) и скруглением r."""
        target = QRectF(float(x), float(y), float(w), float(h))
        sw, sh = pic.width(), pic.height()
        s = min(sw / max(1e-6, target.width()), sh / max(1e-6, target.height()))
        src = QRectF((sw - target.width() * s) / 2, (sh - target.height() * s) / 2,
                     target.width() * s, target.height() * s)
        draw = p.drawImage if is_image else p.drawPixmap
        if r:
            p.save()
            path = QPainterPath()
            path.addRoundedRect(target, float(r), float(r))
            p.setClipPath(path, Qt.ClipOperation.IntersectClip)
            draw(target, pic, src)
            p.restore()
        else:
            draw(target, pic, src)

    def image(self, path, x, y, w, h, r=0):
        p = self.need()
        path = self.scene.resolve(str(path or ""))
        if path.lower().endswith(MEDIA.ANIM_EXT) and Path(path).exists():
            src = MEDIA.get("anim", path)
            pm = src.pix if src is not None else QPixmap()
        else:
            pm = self._pix(path)
        if pm.isNull():
            return False
        self._blit(p, pm, x, y, w, h, r)
        return True

    def thumb(self, path, x, y, w, h, r=0):
        """Маленькая картинка (обложка в списке): грузится в фоне, кэш на сотни штук — прокрутка
        длинной медиатеки не тормозит. false — картинки нет или она ещё грузится."""
        p = self.need()
        img = _thumb(self.scene.resolve(str(path or "")))
        if img is None:
            return False
        self._blit(p, img, x, y, w, h, r, is_image=True)
        return True

    def video(self, path, x, y, w, h, r=0, rate=1.0):
        """Видео без звука, по кругу. false — файла нет или не открылся (пока грузится — true, но пусто)."""
        p = self.need()
        path = self.scene.resolve(str(path or ""))
        if not path or not Path(path).exists():
            return False
        src = MEDIA.get("video", path)
        if src is None:
            return False
        try:
            if getattr(src, "_target", 1) is None and hasattr(src, "set_target"):
                # кадр нужного размера декодирует VLC (а не 4K ради маленького окошка)
                dpr = float(p.device().devicePixelRatioF()) if p.device() is not None else 1.0
                k = math.hypot(p.transform().m11(), p.transform().m12()) or 1.0
                src.set_target(abs(w) * k * dpr, abs(h) * k * dpr)
            if abs(float(rate) - getattr(src, "_echo_rate", 1.0)) > 1e-3:
                src.set_rate(float(rate))
                src._echo_rate = float(rate)
        except Exception:                                  # noqa: BLE001
            pass
        img = src.img
        if img.isNull():
            return True
        self._blit(p, img, x, y, w, h, r, is_image=True)
        return True

    _PATHS: dict = {}

    def path(self, d, x=0, y=0, w=0, h=0):
        """Векторный контур в синтаксисе SVG (M L H V C S Q T A Z). w, h > 0 — вписать в прямоугольник
        с сохранением пропорций (по центру); иначе — как есть, со сдвигом x, y."""
        p = self.need()
        d = L._to_str(d)
        pp = self._PATHS.get(d)
        if pp is None:
            pp = svg_path(d)
            if len(self._PATHS) > 128:
                self._PATHS.clear()
            self._PATHS[d] = pp
        if pp.isEmpty():
            return False
        self._apply()
        w, h = float(w), float(h)
        if w > 0 and h > 0:
            br = pp.boundingRect()
            k = min(w / max(1e-6, br.width()), h / max(1e-6, br.height()))
            p.save()
            p.translate(float(x) + (w - br.width() * k) / 2, float(y) + (h - br.height() * k) / 2)
            p.scale(k, k)
            p.translate(-br.left(), -br.top())
            pen = p.pen()
            if pen.style() != Qt.PenStyle.NoPen:
                pen.setWidthF(pen.widthF() / k)          # толщина контура — в пикселях экрана, не масштабируется
                p.setPen(pen)
            p.drawPath(pp)
            p.restore()
        else:
            p.save()
            p.translate(float(x), float(y))
            p.drawPath(pp)
            p.restore()
        return True

    # ── контуры любой формы и маски ──
    def draw_path(self, path):
        """Нарисовать контур (path_new / shape / path_svg) текущими fill и stroke."""
        p = self.need()
        self._apply()
        p.drawPath(S._pp(path))

    def clip_path(self, path):
        """Дальше рисовать только внутри контура (до restore()). Так делаются маски любой формы."""
        self.need().setClipPath(S._pp(path), Qt.ClipOperation.IntersectClip)

    def draw_shape(self, kind, x, y, w, h, opts=None):
        p = self.need()
        self._apply()
        p.drawPath(S.make_shape(kind, x, y, w, h, opts, noise, svg_path))

    def clip_shape(self, kind, x, y, w, h, opts=None):
        self.need().setClipPath(S.make_shape(kind, x, y, w, h, opts, noise, svg_path), Qt.ClipOperation.IntersectClip)

    def image_shape(self, path, kind, x, y, w, h, opts=None):
        """Картинка, обрезанная по форме: image_shape(track.cover, "heart", 0, 0, w, h)."""
        p = self.need()
        p.save()
        try:
            p.setClipPath(S.make_shape(kind, x, y, w, h, opts, noise, svg_path), Qt.ClipOperation.IntersectClip)
            return self.image(path, x, y, w, h)
        finally:
            p.restore()

    # ── свои холсты ──
    def draw_on(self, canvas, fn):
        """Рисовать в холст canvas(w, h): всё рисование внутри fn попадает в холст и остаётся там."""
        if not isinstance(canvas, S.ECanvas):
            raise EchoError("draw_on(): первый аргумент — холст из canvas(w, h)")
        saved = (self.p, self.brush, self.pen, self.stack)
        cp = QPainter(canvas.img)
        cp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        cp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.p = cp
        self.brush, self.pen, self.stack = QBrush(QColor(255, 255, 255)), Qt.PenStyle.NoPen, []
        try:
            import dsl_lib
            dsl_lib._call(fn)
        finally:
            while self.stack:
                cp.restore()
                self.stack.pop()
            cp.end()
            self.p, self.brush, self.pen, self.stack = saved
            canvas.gen += 1
        return canvas

    def draw_canvas(self, canvas, x=0, y=0, w=None, h=None, smooth=True):
        p = self.need()
        if not isinstance(canvas, S.ECanvas):
            raise EchoError("draw_canvas(): нужен холст из canvas(w, h)")
        img = canvas.img
        w = img.width() if w is None else float(w)
        h = img.height() if h is None else float(h)
        p.save()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, bool(smooth))
        p.drawImage(QRectF(float(x), float(y), w, h), img, QRectF(0, 0, img.width(), img.height()))
        p.restore()

    def canvas_clear(self, canvas, color=None):
        return canvas.clear(color, to_qcolor)

    def pixel(self, canvas, x, y, color):
        canvas.pixel(x, y, to_qcolor(color))

    def get_pixel(self, canvas, x, y):
        return canvas.get(x, y)

    def blur_canvas(self, canvas, sigma=4.0):
        try:
            from blur_fx import blur_image
            out = blur_image(canvas.img, float(sigma))
            if not out.isNull():
                canvas.img = out.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
                canvas.gen += 1
        except Exception as e:                             # noqa: BLE001
            raise EchoError(f"blur_canvas(): {e}")
        return canvas

    # ── картинки: части, спрайты, размеры ──
    def image_size(self, path):
        pm = self._pix(self.scene.resolve(str(path or "")))
        return {"w": pm.width(), "h": pm.height()}

    def image_ex(self, path, x, y, w, h, opts=None):
        """Картинка с настройками: {fit: "cover"|"contain"|"stretch", r, flip_x, flip_y, opacity,
        sx, sy, sw, sh (часть картинки в её пикселях), smooth}."""
        p = self.need()
        o = opts if isinstance(opts, dict) else {}
        pm = self._pix(self.scene.resolve(str(path or "")))
        if pm.isNull():
            return False
        x, y, w, h = float(x), float(y), float(w), float(h)
        pw, ph = pm.width(), pm.height()
        src = QRectF(float(o.get("sx", 0)), float(o.get("sy", 0)), float(o.get("sw", pw)), float(o.get("sh", ph)))
        fit = str(o.get("fit", "cover"))
        tgt = QRectF(x, y, w, h)
        if fit == "cover":
            s = min(src.width() / max(1e-6, w), src.height() / max(1e-6, h))
            src = QRectF(src.x() + (src.width() - w * s) / 2, src.y() + (src.height() - h * s) / 2, w * s, h * s)
        elif fit == "contain":
            s = min(w / max(1e-6, src.width()), h / max(1e-6, src.height()))
            tw, th = src.width() * s, src.height() * s
            tgt = QRectF(x + (w - tw) / 2, y + (h - th) / 2, tw, th)
        p.save()
        try:
            if "opacity" in o:
                p.setOpacity(p.opacity() * max(0.0, min(1.0, float(o["opacity"]))))
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, bool(o.get("smooth", True)))
            r = float(o.get("r", 0) or 0)
            if r > 0:
                cpath = QPainterPath()
                cpath.addRoundedRect(QRectF(x, y, w, h), r, r)
                p.setClipPath(cpath, Qt.ClipOperation.IntersectClip)
            if o.get("flip_x") or o.get("flip_y"):
                c = tgt.center()
                p.translate(c)
                p.scale(-1.0 if o.get("flip_x") else 1.0, -1.0 if o.get("flip_y") else 1.0)
                p.translate(-c)
            p.drawPixmap(tgt, pm, src)
        finally:
            p.restore()
        return True

    def image_part(self, path, sx, sy, sw, sh, x, y, w, h):
        return self.image_ex(path, x, y, w, h, {"sx": sx, "sy": sy, "sw": sw, "sh": sh, "fit": "stretch"})

    def sprite(self, path, frame, cols, rows, x, y, w, h, smooth=True):
        """Кадр из листа спрайтов (сетка cols × rows, кадры слева направо, сверху вниз)."""
        pm = self._pix(self.scene.resolve(str(path or "")))
        if pm.isNull():
            return False
        cols, rows = max(1, int(cols)), max(1, int(rows))
        fw, fh = pm.width() / cols, pm.height() / rows
        i = int(frame) % (cols * rows)
        return self.image_ex(path, x, y, w, h, {"sx": (i % cols) * fw, "sy": (i // cols) * fh, "sw": fw, "sh": fh,
                                                "fit": "stretch", "smooth": smooth})

    # ── текст в рамке ──
    _ALIGN_H = {"left": Qt.AlignmentFlag.AlignLeft, "center": Qt.AlignmentFlag.AlignHCenter,
                "right": Qt.AlignmentFlag.AlignRight, "justify": Qt.AlignmentFlag.AlignJustify}
    _ALIGN_V = {"top": Qt.AlignmentFlag.AlignTop, "middle": Qt.AlignmentFlag.AlignVCenter,
                "center": Qt.AlignmentFlag.AlignVCenter, "bottom": Qt.AlignmentFlag.AlignBottom}

    def text_box(self, s, x, y, w, h, size=14, align="left", valign="top", weight=500, font=None, wrap=True):
        """Многострочный текст в прямоугольнике с переносом по словам. → высота текста."""
        p = self.need()
        f = self._font(size, weight, font)
        p.setFont(f)
        p.setPen(self.brush.color() if self.brush.style() != Qt.BrushStyle.NoBrush else QColor(255, 255, 255))
        flags = int(self._ALIGN_H.get(str(align), Qt.AlignmentFlag.AlignLeft) |
                    self._ALIGN_V.get(str(valign), Qt.AlignmentFlag.AlignTop))
        if wrap:
            flags |= int(Qt.TextFlag.TextWordWrap)
        br = p.boundingRect(QRectF(float(x), float(y), float(w), float(h)), flags, L._to_str(s))
        p.drawText(QRectF(float(x), float(y), float(w), float(h)), flags, L._to_str(s))
        return br.height()

    def text_height(self, s, w, size=14, weight=500, font=None):
        fm = QFontMetricsF(self._font(size, weight, font))
        r = fm.boundingRect(QRectF(0, 0, float(w), 1e6), int(Qt.TextFlag.TextWordWrap), L._to_str(s))
        return r.height()

    # ── эффекты ──
    _SHADOWS: dict = {}

    def shadow(self, x, y, w, h, r=12, blur=18, color="#00000099", dx=0, dy=6):
        """Мягкая тень под прямоугольником (кэшируется по размеру)."""
        p = self.need()
        q = to_qcolor(color)
        b = max(0.0, float(blur))
        key = (int(w), int(h), round(float(r), 1), round(b, 1), q.rgba())
        pm = self._SHADOWS.get(key)
        if pm is None:
            pad = int(b * 2 + 2)
            img = QImage(int(w) + pad * 2, int(h) + pad * 2, QImage.Format.Format_ARGB32_Premultiplied)
            img.fill(0)
            gp = QPainter(img)
            gp.setRenderHint(QPainter.RenderHint.Antialiasing)
            gp.setPen(Qt.PenStyle.NoPen)
            gp.setBrush(q)
            gp.drawRoundedRect(QRectF(pad, pad, float(w), float(h)), float(r), float(r))
            gp.end()
            if b > 0.5:
                try:
                    from blur_fx import blur_image
                    img = blur_image(img, b / 2)
                except Exception:                          # noqa: BLE001
                    pass
            pm = (QPixmap.fromImage(img), pad)
            if len(self._SHADOWS) > 64:
                self._SHADOWS.clear()
            self._SHADOWS[key] = pm
        p.drawPixmap(QPointF(float(x) + float(dx) - pm[1], float(y) + float(dy) - pm[1]), pm[0])

    _NOISE: list = []

    def static_noise(self, x, y, w, h, amount=1.0, color=False):
        """Телевизионные помехи (как на камерах наблюдения). amount — 0…1."""
        p = self.need()
        a = max(0.0, min(1.0, float(amount)))
        if a <= 0.003:
            return
        key = "c" if color else "m"
        frames = [f for f in self._NOISE if f[0] == key]
        if not frames:
            rng = np.random.default_rng()
            for _ in range(6):
                if color:
                    arr = rng.integers(0, 256, (144, 256, 4), dtype=np.uint8)
                else:
                    g = rng.integers(0, 256, (144, 256), dtype=np.uint8)
                    arr = np.stack([g, g, g, g], 2)
                arr[..., 3] = 255
                img = QImage(arr.tobytes(), 256, 144, 256 * 4, QImage.Format.Format_RGB32).copy()
                self._NOISE.append((key, img))
            frames = [f for f in self._NOISE if f[0] == key]
        img = random.choice(frames)[1]
        p.save()
        p.setOpacity(p.opacity() * a)
        ox, oy = random.randint(0, 64), random.randint(0, 36)
        p.drawImage(QRectF(float(x), float(y), float(w), float(h)), img, QRectF(ox, oy, 192, 108))
        p.restore()

    def scanlines(self, x, y, w, h, alpha=0.18, gap=3):
        p = self.need()
        gap = max(2, int(gap))
        p.save()
        p.setPen(QPen(QColor(0, 0, 0, int(max(0.0, min(1.0, float(alpha))) * 255)), 1))
        x, y, w, h = float(x), float(y), float(w), float(h)
        p.drawLines([QLineF(x, yy + 0.5, x + w, yy + 0.5) for yy in np.arange(y, y + h, gap)])
        p.restore()

    def vignette(self, x, y, w, h, strength=0.65, color="#000000"):
        p = self.need()
        q = to_qcolor(color)
        x, y, w, h = float(x), float(y), float(w), float(h)
        g = QRadialGradient(x + w / 2, y + h / 2, max(w, h) * 0.72)
        e = QColor(q)
        e.setAlpha(0)
        g.setColorAt(0.45, e)
        e2 = QColor(q)
        e2.setAlphaF(max(0.0, min(1.0, float(strength))))
        g.setColorAt(1.0, e2)
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawRect(QRectF(x, y, w, h))
        p.restore()

    def rect4(self, x, y, w, h, tl=0, tr=0, br=0, bl=0):
        """Прямоугольник с разным скруглением каждого угла."""
        p = self.need()
        x, y, w, h = float(x), float(y), float(w), float(h)
        tl, tr, br, bl = (max(0.0, min(float(v), w / 2, h / 2)) for v in (tl, tr, br, bl))
        pp = QPainterPath(QPointF(x + tl, y))
        pp.lineTo(x + w - tr, y)
        pp.quadTo(x + w, y, x + w, y + tr)
        pp.lineTo(x + w, y + h - br)
        pp.quadTo(x + w, y + h, x + w - br, y + h)
        pp.lineTo(x + bl, y + h)
        pp.quadTo(x, y + h, x, y + h - bl)
        pp.lineTo(x, y + tl)
        pp.quadTo(x, y, x + tl, y)
        pp.closeSubpath()
        self._apply()
        p.drawPath(pp)

    def stroke_dash(self, pattern=(6, 4), offset=0.0):
        """Пунктир для текущего контура: stroke(…) затем stroke_dash([6, 4])."""
        if self.pen == Qt.PenStyle.NoPen:
            return
        pen = QPen(self.pen)
        w = max(0.1, pen.widthF())
        pen.setDashPattern([max(0.01, float(v) / w) for v in (pattern or [6, 4])])
        pen.setDashOffset(float(offset) / w)
        self.pen = pen

    def antialias(self, on=True):
        self.need().setRenderHint(QPainter.RenderHint.Antialiasing, bool(on))

    def smooth_images(self, on=True):
        self.need().setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, bool(on))

    _FONTS: dict = {}

    def font_load(self, path):
        """Подключить свой шрифт (.ttf / .otf) → имя семейства для text(…, font: имя)."""
        path = self.scene.resolve(str(path or ""))
        fam = self._FONTS.get(path)
        if fam is None:
            from PyQt6.QtGui import QFontDatabase
            fid = QFontDatabase.addApplicationFont(path)
            fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
            fam = fams[0] if fams else ""
            self._FONTS[path] = fam
        return fam

    # ── градиенты (значения для fill/stroke) ──
    @staticmethod
    def linear(x1, y1, x2, y2, stops):
        return {"__grad__": "linear", "pts": (float(x1), float(y1), float(x2), float(y2)), "stops": list(stops)}

    @staticmethod
    def radial(cx, cy, r, stops):
        return {"__grad__": "radial", "pts": (float(cx), float(cy), float(r)), "stops": list(stops)}

    @staticmethod
    def conic(cx, cy, angle, stops):
        return {"__grad__": "conic", "pts": (float(cx), float(cy), float(angle)), "stops": list(stops)}

    def export(self) -> dict:
        names = ("fill nofill stroke nostroke alpha blend save restore translate rotate scale clip clear rect circle "
                 "ellipse line lines poly curve arc ring star ngon glow text textwidth image video path linear "
                 "radial conic thumb draw_path clip_path draw_shape clip_shape image_shape draw_on draw_canvas "
                 "canvas_clear pixel get_pixel blur_canvas image_size image_ex image_part sprite text_box "
                 "text_height shadow static_noise scanlines vignette rect4 stroke_dash antialias smooth_images "
                 "font_load").split()
        out = {n: getattr(self, n) for n in names}
        out.update({
            "path_new": lambda: S.EPath(),
            "path_svg": lambda d: S.EPath(QPainterPath(svg_path(L._to_str(d)))),
            "shape": lambda kind, x, y, w, h, opts=None: S.EPath(S.make_shape(kind, x, y, w, h, opts, noise, svg_path)),
            "canvas": lambda w, h: S.ECanvas(w, h),
            "SHAPES": list(S.SHAPES),
        })
        return out


# ══════════════════════════════════════════════════════════════════════════ #
#  Стандартная библиотека (математика, списки, строки, физика)
# ══════════════════════════════════════════════════════════════════════════ #

def _perm(seed=1337):
    r = random.Random(seed)
    p = list(range(256))
    r.shuffle(p)
    return p + p


_P = _perm()


def _fade(t):
    return t * t * t * (t * (t * 6 - 15) + 10)


def _grad(h, x, y, z):
    h &= 15
    u = x if h < 8 else y
    v = y if h < 4 else (x if h in (12, 14) else z)
    return (u if (h & 1) == 0 else -u) + (v if (h & 2) == 0 else -v)


def noise(x, y=0.0, z=0.0):
    """Плавный шум Перлина, значения примерно -1..1."""
    x, y, z = float(x), float(y), float(z)
    X, Y, Z = int(math.floor(x)) & 255, int(math.floor(y)) & 255, int(math.floor(z)) & 255
    x -= math.floor(x)
    y -= math.floor(y)
    z -= math.floor(z)
    u, v, w = _fade(x), _fade(y), _fade(z)
    P = _P
    A = P[X] + Y
    AA, AB = P[A] + Z, P[A + 1] + Z
    B = P[X + 1] + Y
    BA, BB = P[B] + Z, P[B + 1] + Z

    def lerp(t, a, b):
        return a + t * (b - a)
    return lerp(w, lerp(v, lerp(u, _grad(P[AA], x, y, z), _grad(P[BA], x - 1, y, z)),
                        lerp(u, _grad(P[AB], x, y - 1, z), _grad(P[BB], x - 1, y - 1, z))),
                lerp(v, lerp(u, _grad(P[AA + 1], x, y, z - 1), _grad(P[BA + 1], x - 1, y, z - 1)),
                     lerp(u, _grad(P[AB + 1], x, y - 1, z - 1), _grad(P[BB + 1], x - 1, y - 1, z - 1))))


def _fmt_time(sec):
    sec = max(0, int(float(sec or 0)))
    return f"{sec // 60}:{sec % 60:02d}"


def stdlib(log_fn) -> dict:
    rnd = random.Random()

    def clamp(v, lo=0.0, hi=1.0):
        return lo if v < lo else hi if v > hi else v

    def lerp(a, b, t):
        return a + (b - a) * t

    def remap(v, a, b, c, d):
        return c + (d - c) * ((v - a) / (b - a) if b != a else 0.0)

    def smooth(t):
        t = clamp(t)
        return t * t * (3 - 2 * t)

    def approach(cur, target, rate, dt):
        """Плавное приближение (экспоненциальное сглаживание, не зависит от FPS)."""
        return target + (cur - target) * math.exp(-float(rate) * float(dt))

    def spring(pos, vel, target, k=120.0, damping=12.0, dt=1 / 60):
        """Пружина: возвращает [новая позиция, новая скорость]."""
        a = -float(k) * (pos - target) - float(damping) * vel
        vel = vel + a * dt
        return [pos + vel * dt, vel]

    def push(lst, *v):
        if not isinstance(lst, list):
            raise EchoError("push(): первый аргумент — список")
        lst.extend(v)
        return lst

    def pop(lst, i=-1):
        return lst.pop(int(i)) if lst else None

    def insert(lst, i, v):
        lst.insert(int(i), v)
        return lst

    def remove(lst, i):
        if 0 <= int(i) < len(lst):
            del lst[int(i)]
        return lst

    def slice_(v, a, b=None):
        return v[int(a):] if b is None else v[int(a):int(b)]

    def rng(a, b=None, step=1):
        if b is None:
            a, b = 0, a
        return list(range(int(a), int(b), int(step) or 1))

    def sort(lst, key=None, desc=False):
        if key is None:
            return sorted(lst, reverse=bool(desc))
        return sorted(lst, key=lambda x: key(x), reverse=bool(desc))

    def contains(c, v):
        return v in c

    def keys(d):
        return list(d.keys()) if isinstance(d, dict) else []

    def values(d):
        return list(d.values()) if isinstance(d, dict) else []

    def num(v, default=0):
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def to_int(v, default=0):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return default

    def log(*a):
        log_fn(" ".join(L._to_str(x) for x in a))

    def sum_(lst, a=0, b=None):
        seg = lst[int(a):] if b is None else lst[int(a):int(b)]
        return float(sum(seg))

    def avg(lst, a=0, b=None):
        seg = lst[int(a):] if b is None else lst[int(a):int(b)]
        return float(sum(seg)) / len(seg) if seg else 0.0

    return {
        "PI": math.pi, "TAU": math.tau, "E": math.e,
        "sin": math.sin, "cos": math.cos, "tan": math.tan, "asin": math.asin, "acos": math.acos,
        "atan": math.atan, "atan2": math.atan2, "sqrt": lambda v: math.sqrt(max(0.0, v)), "abs": abs,
        "min": min, "max": max, "floor": math.floor, "ceil": math.ceil, "round": round, "pow": pow,
        "exp": math.exp, "log10": math.log10, "ln": lambda v: math.log(max(1e-12, v)),
        "sign": lambda v: (v > 0) - (v < 0), "hypot": math.hypot, "deg": math.degrees, "rad": math.radians,
        "clamp": clamp, "lerp": lerp, "remap": remap, "smooth": smooth, "approach": approach, "spring": spring,
        "noise": noise, "rand": lambda a=0.0, b=1.0: rnd.uniform(a, b), "randint": lambda a, b: rnd.randint(int(a), int(b)),
        "seed": lambda s: rnd.seed(s), "choice": lambda lst: rnd.choice(lst) if lst else None,
        "len": lambda v: len(v) if v is not None else 0, "str": L._to_str, "num": num, "int": to_int,
        "push": push, "pop": pop, "insert": insert, "remove": remove, "slice": slice_, "range": rng,
        "sort": sort, "reverse": lambda lst: list(reversed(lst)), "contains": contains, "keys": keys,
        "values": values, "sum": sum_, "avg": avg, "fill_list": lambda n, v=0: [v] * int(n),
        "join": lambda lst, sep="": str(sep).join(L._to_str(x) for x in lst),
        "split": lambda s, sep=" ": str(s).split(sep), "upper": lambda s: str(s).upper(),
        "lower": lambda s: str(s).lower(), "fmt_time": _fmt_time,
        "fmt": lambda v, digits=2: f"{float(v):.{int(digits)}f}",
        "rgb": rgb, "hsv": hsv, "mix": mix, "with_alpha": with_alpha, "hex": color_hex,
        "log": log, "print": log,
    }


# ══════════════════════════════════════════════════════════════════════════ #
#  Сцена
# ══════════════════════════════════════════════════════════════════════════ #

GEOM = ("x", "y", "w", "h")


def snapshot(bridge: PlayerBridge, analyzer: AudioAnalyzer, player: dict | None = None) -> dict:
    """Всё, что скрипт видит о плеере и звуке в этом кадре (считается один раз на кадр)."""
    return {"audio": analyzer.as_dict(), "track": bridge.track(), "player": player or bridge.player(),
            "playlist": bridge.playlist(), "lyrics": bridge.lyrics()}


EV_FIELD.update({ev: "on_" + ev for ev in ("message", "keyup", "dblclick", "destroy")})


def handles(inst, ev) -> bool:
    """Есть ли у экземпляра обработчик события (в классе или своим полем on_<событие>)."""
    return ev in inst.cls.handlers or inst.fields.get(EV_FIELD.get(ev, "")).__class__ is L.UserFn


HANDLER_EVENTS = ("init", "frame", "draw", "press", "release", "click", "move", "drag", "wheel", "beat",
                  "track", "key", "enter", "leave", "resize", "message", "keyup", "dblclick", "destroy")


# ══════════════════════════════════════════════════════════════════════════ #
#  Клавиши, сохранения, диалоги
# ══════════════════════════════════════════════════════════════════════════ #

_KEYNAMES = {Qt.Key.Key_Space.value: "space", Qt.Key.Key_Left.value: "left", Qt.Key.Key_Right.value: "right",
             Qt.Key.Key_Up.value: "up", Qt.Key.Key_Down.value: "down", Qt.Key.Key_Escape.value: "escape",
             Qt.Key.Key_Return.value: "enter", Qt.Key.Key_Enter.value: "enter", Qt.Key.Key_Tab.value: "tab",
             Qt.Key.Key_Backspace.value: "backspace", Qt.Key.Key_Delete.value: "delete",
             Qt.Key.Key_Shift.value: "shift", Qt.Key.Key_Control.value: "ctrl", Qt.Key.Key_Alt.value: "alt",
             Qt.Key.Key_Home.value: "home", Qt.Key.Key_End.value: "end", Qt.Key.Key_PageUp.value: "pageup",
             Qt.Key.Key_PageDown.value: "pagedown", Qt.Key.Key_Insert.value: "insert"}


def key_name(e) -> str:
    """Имя клавиши для скриптов: "a"…"z" и "0"…"9" — по физической клавише (WASD работает и в русской
    раскладке), "space", "left", "enter", "escape", "shift", "f1"… — служебные."""
    k = int(e.key())
    n = _KEYNAMES.get(k)
    if n:
        return n
    if Qt.Key.Key_F1.value <= k <= Qt.Key.Key_F12.value:
        return f"f{k - Qt.Key.Key_F1.value + 1}"
    if 0x30 <= k <= 0x39 or 0x41 <= k <= 0x5A:
        return chr(k).lower()
    t = e.text()
    return t.lower() if t and t.isprintable() else ""


_DATA = None
_DATA_FILE = data_home.root() / "echoscript_data.json"
_DATA_DIRTY = [False]


def _data() -> dict:
    global _DATA
    if _DATA is None:
        try:
            import json
            _DATA = json.loads(_DATA_FILE.read_text("utf-8"))
        except Exception:                                  # noqa: BLE001
            _DATA = {}
        if not isinstance(_DATA, dict):
            _DATA = {}
    return _DATA


def _data_flush():
    if not _DATA_DIRTY[0] or _DATA is None:
        return
    _DATA_DIRTY[0] = False
    try:
        import json
        import os
        _DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _DATA_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_DATA, ensure_ascii=False), "utf-8")
        os.replace(tmp, _DATA_FILE)
    except Exception as e:                                 # noqa: BLE001
        print("[echoscript] сохранение данных:", e)


def _data_set(key, value):
    """save_data("рекорд", 12) — значение переживёт перезапуск плеера (числа, строки, списки, словари)."""
    import json
    import dsl_lib
    _data()[L._to_str(key)] = json.loads(dsl_lib.to_json(value))
    if not _DATA_DIRTY[0]:
        _DATA_DIRTY[0] = True
        try:
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                if not getattr(_data_flush, "_hooked", False):
                    app.aboutToQuit.connect(_data_flush)
                    _data_flush._hooked = True
                QTimer.singleShot(800, _data_flush)
                return value
        except Exception:                                  # noqa: BLE001
            pass
        _data_flush()
    return value


def _data_get(key, default=None):
    import dsl_lib
    v = _data().get(L._to_str(key))
    return default if v is None else dsl_lib.l_deep_copy(v)


def _data_del(key):
    if _data().pop(L._to_str(key), None) is not None:
        _DATA_DIRTY[0] = True
        _data_flush()


def _dlg_parent():
    from PyQt6.QtWidgets import QApplication
    return QApplication.activeWindow()


def _open_url(url):
    from PyQt6.QtCore import QUrl
    from PyQt6.QtGui import QDesktopServices
    u = L._to_str(url)
    if Path(u).exists():
        return QDesktopServices.openUrl(QUrl.fromLocalFile(u))
    if not u.lower().startswith(("http://", "https://", "mailto:")):
        raise EchoError("open_url(): ссылка должна начинаться с http://, https:// или быть путём к файлу")
    return QDesktopServices.openUrl(QUrl(u))


def _copy_text(s):
    from PyQt6.QtWidgets import QApplication
    QApplication.clipboard().setText(L._to_str(s))


def _paste_text():
    from PyQt6.QtWidgets import QApplication
    return QApplication.clipboard().text()


class _LocalWorld:
    """«Мир» для сцен вне конструктора тем (студия скриптов, тесты): общие переменные и клавиши."""

    def __init__(self):
        import weakref
        self.shared_vars: dict = {}
        self.keys_down: set = set()
        self.capture = None
        self._scenes = weakref.WeakSet()

    def add(self, sc):
        self._scenes.add(sc)

    def scenes(self):
        return [(s, "") for s in list(self._scenes)]

    def asset(self, p):
        return p

    def layer(self, name):
        return None

    def layer_names(self):
        return []

    def to_scene(self, scene, x, y):
        return x, y


LOCAL_WORLD = _LocalWorld()


def _interp(a, b, t):
    """Промежуточное значение для tween: числа, цвета, списки чисел."""
    if isinstance(b, bool) or b is None:
        return b if t >= 1 else a
    if isinstance(b, (int, float)):
        a = a if isinstance(a, (int, float)) and not isinstance(a, bool) else 0.0
        return a + (b - a) * t
    if isinstance(b, list):
        if isinstance(a, list) and len(a) == len(b):
            return [_interp(x, y, t) for x, y in zip(a, b)]
        return b if t >= 1 else a
    if isinstance(b, str) or b.__class__ is QColor:
        try:
            return mix(a if a is not None else b, b, t)
        except EchoError:
            return b if t >= 1 else a
    return b if t >= 1 else a


class Scene:
    def __init__(self, src: str, bridge: PlayerBridge, analyzer: AudioAnalyzer, log_fn=print, world=None, name=""):
        self.bridge, self.analyzer, self.log_fn = bridge, analyzer, log_fn
        self.world = world if world is not None else LOCAL_WORLD
        if world is None:
            LOCAL_WORLD.add(self)
        self.name = name
        self.errors: list[str] = []
        self.instances: list[L.Instance] = []
        self.theme = {"name": "Без названия", "bg": "#07070b", "accent": "#ff3d81", "accent2": "#3dd6ff",
                      "text": "#f4f4f8", "muted": "#8a8aa0", "font": "Segoe UI"}
        self.t0 = time.monotonic()
        self.time = 0.0
        self.frame_no = 0
        self.W = self.H = 1
        self._uid = 0
        self._track_key = None
        self.started = False
        self.timers: list = []                              # [когда, период (0 — один раз), fn, виджет, id]
        self.tweens: list = []
        self._tid = 0
        self._extra: dict = {}                              # классы из каталога, подключённые через spawn/child
        self.gfx = Gfx(self)
        self.program = L.compile_program(src)               # синтаксические ошибки — сразу исключением
        import dsl_lib
        import dsl_sound
        g = {}
        std = stdlib(self._log)
        dsl_lib.install_methods(std)
        g.update(dsl_lib.library(noise, to_qcolor))
        g.update(std)
        g.update(self.gfx.export())
        g.update(bridge.actions())
        g.update(bridge.library_api())
        g.update(dsl_sound.api(self.resolve))
        g.update(self._api())
        g.update({"audio": analyzer.as_dict(), "track": bridge.track(), "player": bridge.player(),
                  "playlist": bridge.playlist(), "lyrics": bridge.lyrics(), "time": 0.0, "dt": 0.0, "frame": 0,
                  "W": 1, "H": 1, "mouse": {"x": 0, "y": 0, "down": False}, "theme": self.theme,
                  "shared": self.world.shared_vars})
        g["__user__"] = set()
        self.g = g
        builtins = dict(g)
        for cls in self.program.widgets.values():
            self._bind_class(cls)
        L.BUDGET[0] = L.DEFAULT_BUDGET
        top = L.Frame(g, None, None, g)
        self.program.top(top)                               # верхний уровень: let/fn
        # свои имена — новые или переобъявленные через let (let count = 0 поверх встроенной count)
        g["__user__"] = {k for k, v in g.items() if k != "__user__" and (k not in builtins or v is not builtins[k])}
        for tn in self.program.theme_nodes:
            L.BUDGET[0] = L.DEFAULT_BUDGET
            d = tn(top)
            if isinstance(d, dict):
                self.theme.update({k: v for k, v in d.items()})
        g["__add__"] = self._add
        for sb in self.program.scene_blocks:
            L.BUDGET[0] = L.DEFAULT_BUDGET
            sb(L.Frame({}, top, None, g))
        del g["__add__"]

    def _bind_class(self, cls):
        for fn in list(cls.methods.values()) + list(cls.handlers.values()):
            L.bind_globals(fn, self.g)
        unknown = [ev for ev in cls.handlers if ev not in HANDLER_EVENTS]
        if unknown:
            raise EchoError(f"виджет {cls.name}: неизвестное событие «{unknown[0]}» "
                            f"(есть: {', '.join(HANDLER_EVENTS)})", cls.line)

    # ── журнал, пути ──
    def _log(self, msg):
        try:
            self.log_fn(msg)
        except Exception:                                  # noqa: BLE001
            pass

    def resolve(self, path: str) -> str:
        """«assets/…» (файлы темы конструктора) → полный путь; остальное как есть."""
        p = str(path or "")
        if p.startswith("assets/"):
            try:
                return self.world.asset(p) or p
            except Exception:                              # noqa: BLE001
                return p
        return p

    def _err(self, where, e):
        msg = f"{where}: {e}"
        self.errors.append(msg)
        del self.errors[:-50]
        self._log("ОШИБКА " + msg)

    # ── классы: свои и из каталога ──
    def class_of(self, kind, line=0):
        if isinstance(kind, L.WidgetClass):
            return kind
        if isinstance(kind, L.Instance):
            return kind.cls
        name = L._to_str(kind)
        cls = self.program.widgets.get(name) or self._extra.get(name)
        if cls is not None:
            return cls
        try:
            import layer_fx
            lib = layer_fx.LIB
        except Exception:                                  # noqa: BLE001
            lib = {}
        b = lib.get(name) or next((b for b in lib.values() if b["cls"] == name), None)
        if b is None:
            mine = ", ".join(sorted(self.program.widgets)) or "нет"
            raise EchoError(f"нет виджета «{name}». Свои: {mine}. Из каталога: {', '.join(sorted(lib))}", line)
        if b["cls"] not in self._extra:
            prog = L.compile_program(b["code"])
            for cn, c in prog.widgets.items():
                if cn in self.program.widgets or cn in self._extra:
                    continue
                self._bind_class(c)
                self._extra[cn] = c
            L.BUDGET[0] = L.DEFAULT_BUDGET
            prog.top(L.Frame({}, None, None, self.g))
        cls = self._extra.get(b["cls"]) or self.program.widgets.get(b["cls"])
        if cls is None:
            raise EchoError(f"блок «{name}» не содержит виджета {b['cls']}", line)
        return cls

    # ── создание экземпляров ──
    def _make(self, cls, spec, line, parent=None):
        inst = L.Instance(cls, self._uid, line)
        self._uid += 1
        inst.scene = self
        inst.parent = parent
        f = inst.fields
        f.update({"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0, "z": 0, "visible": True, "clip": True,
                  "name": spec.get("name", cls.name), "hover": False, "pressed": False,
                  "cache": False, "cache_key": "", "resolution": 1.0, "auto": True})
        g = self.g
        fr = L.Frame({}, None, inst, g)
        L.BUDGET[0] = max(L.BUDGET[0], L.DEFAULT_BUDGET // 4)
        for pn, pe, _ln in cls.props:
            f[pn] = spec[pn] if pn in spec else pe(fr)
        for k, v in spec.items():
            if k not in f or k in GEOM or k in ("z", "visible", "clip", "name", "cache", "cache_key", "resolution",
                                                "auto"):
                f[k] = v

        def num(v, d):
            return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else d
        f["_geom"] = (num(f["x"], 0.0), num(f["y"], 0.0), num(f["w"], 1.0), num(f["h"], 1.0))
        for sn, se, _ln in cls.states:
            f[sn] = se(fr)
        return inst

    def _add(self, name, spec, line):
        cls = self.program.widgets.get(name)
        if cls is None:
            try:
                cls = self.class_of(name, line)
            except EchoError:
                raise EchoError(f"нет виджета «{name}» (объявите: widget {name} {{ … }})", line)
        if not isinstance(spec, dict):
            spec = {}
        self.instances.append(self._make(cls, spec, line))

    def _spawn(self, inst, kind, props=None):
        """spawn("Tracklist", {x: 10, y: 40, w: 200, h: 300}) — виджет внутри вызывающего виджета."""
        sc = inst.scene if inst is not None and inst.scene is not None else self
        if sc is not self:
            return sc._spawn(inst, kind, props)
        cls = self.class_of(kind, inst.line if inst is not None else 0)
        spec = dict(props) if isinstance(props, dict) else {}
        k = self._make(cls, spec, inst.line if inst is not None else 0, inst)
        if inst is None:
            self.instances.append(k)
        else:
            if sum(1 for _ in self._walk_from(inst)) > 4000:
                raise EchoError("слишком много вложенных виджетов (больше 4000)")
            inst.kids.append(k)
        if self.started:
            self._layout(k)
            self._init_tree(k)
        return k

    def _kill(self, inst, target=None):
        t = target if target is not None else inst
        if not isinstance(t, L.Instance) or t.dead:
            return False
        sc = t.scene or self
        sc._kill_tree(t)
        return True

    def _kill_tree(self, t):
        for k in list(t.kids):
            self._kill_tree(k)
        if handles(t, "destroy") and t.error is None:
            self._call(t, "destroy", ())
        t.dead = True
        if t.parent is not None:
            if t in t.parent.kids:
                t.parent.kids.remove(t)
        elif t in self.instances:
            self.instances.remove(t)
        self.timers = [x for x in self.timers if x[3] is not t]
        self.tweens = [x for x in self.tweens if x["inst"] is not t and x["t"] is not t]

    def _walk_from(self, inst):
        yield inst
        for k in inst.kids:
            yield from self._walk_from(k)

    def _walk(self):
        for inst in list(self.instances):
            yield from self._walk_from(inst)

    def adopt_state(self, old: "Scene"):
        """Горячая перезагрузка: состояние (state) переносится по совпадению класса и порядкового номера,
        у вложенных виджетов — так же, внутри своего родителя."""
        if old is None:
            return
        self._adopt(self.instances, old.instances)
        self.t0 = old.t0
        self.frame_no = old.frame_no

    def _adopt(self, new_list, old_list):
        buckets = {}
        for inst in old_list:
            buckets.setdefault(inst.cls.name, []).append(inst)
        used = {}

        def holds_inst(v):
            return isinstance(v, L.Instance) or (isinstance(v, list) and any(isinstance(x, L.Instance) for x in v[:64]))
        for inst in new_list:
            lst = buckets.get(inst.cls.name, [])
            k = used.get(inst.cls.name, 0)
            used[inst.cls.name] = k + 1
            if k < len(lst):
                prev = lst[k]
                for sn, _se, _ln in inst.cls.states:
                    if sn in prev.fields and not holds_inst(prev.fields[sn]):
                        inst.fields[sn] = prev.fields[sn]
                self._adopt(inst.kids, prev.kids)

    def start(self):
        self.started = True
        for inst in list(self.instances):
            self._layout(inst)
            self._init_tree(inst)

    def _init_tree(self, inst):
        self._call(inst, "init", ())
        for k in list(inst.kids):
            self._layout(k)
            self._init_tree(k)

    # ── геометрия ──
    def _layout(self, inst):
        gx, gy, gw, gh = inst.fields.get("_geom", (0, 0, 1, 1))
        if inst.parent is None:
            TW, TH = self.W, self.H
        else:
            pf = inst.parent.fields
            TW, TH = float(pf.get("w", 1.0)), float(pf.get("h", 1.0))

        def px(v, total):
            return v * total if abs(v) <= 1.0 else v
        f = inst.fields
        x = px(gx, TW)
        y = px(gy, TH)
        if x < 0:
            x += TW
        if y < 0:
            y += TH
        f["x"], f["y"], f["w"], f["h"] = x, y, max(1.0, px(gw, TW)), max(1.0, px(gh, TH))
        inst.psize = (TW, TH)

    def resize(self, W, H):
        if (W, H) == (self.W, self.H):
            return
        self.W, self.H = W, H
        self.g["W"], self.g["H"] = W, H
        for inst in self.instances:
            self._layout(inst)
            self._call(inst, "resize", (inst.fields["w"], inst.fields["h"]))

    def _place(self, inst, c, x, y, w=None, h=None):
        """place(c, x, y, w, h) — переставить вложенный виджет (доли родителя ≤ 1 или пиксели)."""
        if not isinstance(c, L.Instance):
            raise EchoError("place(): первый аргумент — виджет")
        f = c.fields
        gx, gy, gw, gh = f.get("_geom", (0, 0, 1, 1))
        f["_geom"] = (float(x), float(y), float(gw if w is None else w), float(gh if h is None else h))
        ow, oh = f["w"], f["h"]
        (c.scene or self)._layout(c)
        if (f["w"], f["h"]) != (ow, oh):
            (c.scene or self)._call(c, "resize", (f["w"], f["h"]))
        return c

    def _front(self, inst, c=None):
        c = c if c is not None else inst
        if not isinstance(c, L.Instance):
            return
        sib = c.parent.kids if c.parent is not None else (c.scene or self).instances
        c.fields["z"] = max((s.fields.get("z", 0) for s in sib), default=0) + 1

    def _back(self, inst, c=None):
        c = c if c is not None else inst
        if not isinstance(c, L.Instance):
            return
        sib = c.parent.kids if c.parent is not None else (c.scene or self).instances
        c.fields["z"] = min((s.fields.get("z", 0) for s in sib), default=0) - 1

    # ── вызов обработчиков ──
    def _call(self, inst, ev, args):
        if inst.error is not None:
            return None
        fn = inst.cls.handlers.get(ev)
        ex = inst.fields.get(EV_FIELD.get(ev, ""))           # событие экземпляра: add X { on_click: fn() { … } }
        if ex is not None and ex.__class__ is not L.UserFn:
            ex = None
        if fn is None and ex is None:
            return None
        try:
            res = None
            if fn is not None:
                # «on click { }» — можно не объявлять ненужные параметры
                res = L.run_handler(fn, inst, args[:len(fn.params)] if len(args) > len(fn.params) else args)
            if ex is not None:
                r2 = L.run_handler(ex, inst, args[:len(ex.params)] if len(args) > len(ex.params) else args)
                if res is None:
                    res = r2
            return res
        except EchoError as e:
            inst.error = e
            self._err(f"{inst.cls.name}, событие {ev}", e)
        except Exception as e:                             # noqa: BLE001
            inst.error = EchoError(str(e))
            self._err(f"{inst.cls.name}, событие {ev}", e)
        return None

    def _invoke(self, fn, where, *args):
        """Вызвать функцию скрипта (таймер, tween, диалог) со свежим бюджетом шагов."""
        L.BUDGET[0] = L.DEFAULT_BUDGET
        try:
            import dsl_lib
            return dsl_lib._call(fn, *args)
        except EchoError as e:
            self._err(where, e)
        except Exception as e:                             # noqa: BLE001
            self._err(where, e)
        return None

    def has(self, inst, ev):
        return ev in inst.cls.handlers

    # ── сообщения между виджетами ──
    def _deliver(self, target, name, data, sender):
        if not isinstance(target, L.Instance) or target.dead or not handles(target, "message"):
            return None
        return (target.scene or self)._call(target, "message", (L._to_str(name), data, sender))

    def _emit(self, inst, name, data=None):
        """emit("выбран", 3) — сообщение родителю (его on message(name, data, from))."""
        if inst is None or inst.parent is None:
            return None
        return self._deliver(inst.parent, name, data, inst)

    def _send(self, inst, target, name, data=None):
        """send(виджет или его имя, "команда", данные) → ответ его on message."""
        if not isinstance(target, L.Instance):
            target = self._find_widget(inst, target)
        return self._deliver(target, name, data, inst)

    def _post(self, inst, name, data=None):
        """post("пауза", nil) — всем виджетам всех слоёв темы, у кого есть on message. → сколько получили."""
        n = 0
        for sc, _lname in self.world.scenes():
            for t in list(sc._walk()):
                if t is inst or t.dead or not handles(t, "message"):
                    continue
                sc._call(t, "message", (L._to_str(name), data, inst))
                n += 1
        return n

    def _matches(self, t, name):
        return t.fields.get("name") == name or t.cls.name == name

    def _find_widgets(self, inst, name):
        name = L._to_str(name)
        out = [t for t in self._walk() if self._matches(t, name) and not t.dead]
        for sc, lname in self.world.scenes():
            if sc is self:
                continue
            for t in sc._walk():
                if t.dead:
                    continue
                if self._matches(t, name) or (lname == name and t.parent is None):
                    out.append(t)
        return out

    def _find_widget(self, inst, name):
        """find_widget("Медиатека") — виджет по имени (name в add/spawn), классу или имени слоя."""
        if isinstance(name, L.Instance):
            return name
        found = self._find_widgets(inst, name)
        return found[0] if found else None

    # ── таймеры и плавные изменения ──
    def _after(self, inst, sec, fn, repeat):
        if not callable(fn):
            raise EchoError("after()/every(): второй аргумент — функция, например fn() { … }")
        self._tid += 1
        sec = max(0.0, float(sec))
        if repeat:
            sec = max(1 / 240, sec)
        self.timers.append([self.time + sec, sec if repeat else 0.0, fn, inst, self._tid])
        if len(self.timers) > 5000:
            del self.timers[:len(self.timers) - 5000]
        return self._tid

    def _cancel(self, inst, tid=None):
        """cancel(id) — отменить таймер или tween; cancel() — все таймеры этого виджета."""
        if tid is None:
            self.timers = [t for t in self.timers if t[3] is not inst]
            return
        tid = int(tid)
        self.timers = [t for t in self.timers if t[4] != tid]
        self.tweens = [t for t in self.tweens if t["id"] != tid]

    def _run_timers(self):
        if not self.timers:
            return
        now = self.time
        for t in [t for t in self.timers if t[0] <= now]:
            if t not in self.timers:
                continue
            inst = t[3]
            if inst is not None and (inst.dead or inst.error is not None):
                self.timers.remove(t)
                continue
            if t[1] > 0:
                t[0] += t[1]
                if t[0] < now:
                    t[0] = now + t[1]
            else:
                self.timers.remove(t)
            self._invoke(t[2], f"{inst.cls.name if inst is not None else 'сцена'}, таймер")

    def _tween(self, inst, target, field, to, dur=0.5, ease="out_cubic", done=None):
        """tween(self, "x", 200, 0.4, "out_back") — плавно довести поле до значения за dur секунд."""
        target = inst if target is None else target
        field = L._to_str(field)
        if isinstance(target, L.Instance):
            cur = target.fields.get(field)
        elif isinstance(target, dict):
            cur = target.get(field)
        else:
            raise EchoError("tween(): цель — виджет (self) или словарь")
        import dsl_lib
        dsl_lib.ease(ease, 0.5)                              # проверка имени сразу, а не через кадр
        self.tweens = [t for t in self.tweens if not (t["t"] is target and t["f"] == field)]
        self._tid += 1
        self.tweens.append({"t": target, "f": field, "a": cur, "b": to, "t0": self.time, "d": max(1e-3, float(dur)),
                            "e": str(ease), "done": done, "id": self._tid, "inst": inst})
        return self._tid

    def _tween_stop(self, inst, target=None, field=None):
        target = inst if target is None else target
        self.tweens = [t for t in self.tweens if not (t["t"] is target and (field is None or t["f"] == field))]

    def _run_tweens(self):
        if not self.tweens:
            return
        import dsl_lib
        for tw in list(self.tweens):
            k = min(1.0, max(0.0, (self.time - tw["t0"]) / tw["d"]))
            v = _interp(tw["a"], tw["b"], dsl_lib.ease(tw["e"], k))
            t = tw["t"]
            if isinstance(t, L.Instance):
                t.fields[tw["f"]] = v
            else:
                t[tw["f"]] = v
            if k >= 1.0:
                if tw in self.tweens:
                    self.tweens.remove(tw)
                if tw["done"] is not None:
                    self._invoke(tw["done"], "tween")

    # ── клавиатура ──
    def key_event(self, name, down) -> bool:
        """Клавиша нажата/отпущена → on key / on keyup у всех виджетов. True — кто-то вернул true (поглотил)."""
        ev = "key" if down else "keyup"
        used = False
        for t in list(self._walk()):
            if t.dead or not handles(t, ev):
                continue
            if self._call(t, ev, (name,)) is True:
                used = True
        return used

    def wants_keys(self) -> bool:
        return any(handles(t, "key") or handles(t, "keyup") for t in self._walk())

    # ── мышь относительно виджета ──
    def _mouse_in(self, inst, target=None):
        t = target if target is not None else inst
        m = self.g.get("mouse") or {}
        sx, sy = self.world.to_scene(self, float(m.get("x", 0)), float(m.get("y", 0)))
        if t is None:
            return {"x": sx, "y": sy, "inside": True, "down": bool(m.get("down"))}
        lx, ly = self.local(t, sx, sy)
        f = t.fields
        return {"x": lx, "y": ly, "inside": 0 <= lx < f["w"] and 0 <= ly < f["h"], "down": bool(m.get("down"))}

    # ── диалоги (с функцией-ответом — после закрытия окна; без неё — сразу ответом) ──
    def _later(self, fn, cb):
        if cb is None:
            return fn()
        QTimer.singleShot(0, lambda: self._invoke(cb, "диалог", fn()))
        return None

    def _prompt(self, title="ECHOES", label="", default="", callback=None):
        def run():
            from PyQt6.QtWidgets import QInputDialog
            txt, ok = QInputDialog.getText(_dlg_parent(), L._to_str(title), L._to_str(label), text=L._to_str(default))
            return txt if ok else None
        return self._later(run, callback)

    def _ask(self, text, callback=None):
        def run():
            from PyQt6.QtWidgets import QMessageBox
            r = QMessageBox.question(_dlg_parent(), "ECHOES", L._to_str(text))
            return r == QMessageBox.StandardButton.Yes
        return self._later(run, callback)

    def _pick_file(self, title="Выберите файл", filter_="Все файлы (*)", callback=None):
        def run():
            from PyQt6.QtWidgets import QFileDialog
            path, _ = QFileDialog.getOpenFileName(_dlg_parent(), L._to_str(title), "", L._to_str(filter_))
            return path or None
        return self._later(run, callback)

    def _capture_keys(self, on=True, keys=None):
        """capture_keys() — клавиши достаются только скриптам (пробел не ставит паузу и т.п.);
        capture_keys(true, ["w", "a", "s", "d"]) — только эти; capture_keys(false) — вернуть плееру."""
        if not on:
            self.world.capture = None
        elif keys:
            self.world.capture = {L._to_str(k).lower() for k in keys}
        else:
            self.world.capture = True
        return None

    # ── встроенные функции сцены ──
    def _api(self) -> dict:
        IF = L.InstFn

        def children(inst, t=None):
            t = t if t is not None else inst
            return list(t.kids) if t is not None else list(self.instances)

        def parent_of(inst, t=None):
            t = t if t is not None else inst
            return t.parent if isinstance(t, L.Instance) else None

        def cursor(inst, kind="hand"):
            if inst is not None:
                inst.fields["cursor"] = L._to_str(kind)

        def widget_at(inst, x, y):
            return self._pick(float(x), float(y))

        return {
            "spawn": IF(self._spawn, "spawn"), "kill": IF(self._kill, "kill"),
            "draw_child": IF(self._draw_child, "draw_child"), "place": IF(self._place, "place"),
            "children": IF(children, "children"), "parent_of": IF(parent_of, "parent_of"),
            "emit": IF(self._emit, "emit"), "send": IF(self._send, "send"), "post": IF(self._post, "post"),
            "find_widget": IF(self._find_widget, "find_widget"), "find_widgets": IF(self._find_widgets, "find_widgets"),
            "widgets": lambda: [t for t in self._walk() if not t.dead], "widget_at": IF(widget_at, "widget_at"),
            "after": IF(lambda i, sec, fn: self._after(i, sec, fn, False), "after"),
            "every": IF(lambda i, sec, fn: self._after(i, sec, fn, True), "every"),
            "cancel": IF(self._cancel, "cancel"), "tween": IF(self._tween, "tween"),
            "tween_stop": IF(self._tween_stop, "tween_stop"),
            "key_down": lambda name: L._to_str(name).lower() in self.world.keys_down,
            "keys_pressed": lambda: sorted(self.world.keys_down),
            "capture_keys": self._capture_keys,
            "save_data": _data_set, "load_data": _data_get, "delete_data": _data_del,
            "data_keys": lambda: sorted(_data().keys()),
            "layer": lambda name: self.world.layer(L._to_str(name)), "layers": lambda: self.world.layer_names(),
            "bring_to_front": IF(self._front, "bring_to_front"), "send_to_back": IF(self._back, "send_to_back"),
            "cursor": IF(cursor, "cursor"), "mouse_in": IF(self._mouse_in, "mouse_in"),
            "prompt": self._prompt, "ask": self._ask, "pick_file": self._pick_file, "open_url": _open_url,
            "copy_text": _copy_text, "paste_text": _paste_text,
        }

    # ── кадр ──
    def frame(self, dt):
        b = self.bridge
        pl = b.player()
        self.analyzer.update(dt, pl["playing"])
        self.frame_shared(dt, snapshot(b, self.analyzer, pl))

    def frame_shared(self, dt, sh: dict):
        """Кадр по готовому снимку (snapshot): одна сцена или сразу много (слои конструктора тем)
        без повторного анализа звука и опроса плеера."""
        self.time = time.monotonic() - self.t0
        self.frame_no += 1
        g = self.g
        tr = sh["track"]
        g["audio"] = sh["audio"]
        g["track"] = tr
        g["player"] = sh["player"]
        g["playlist"] = sh["playlist"]
        g["lyrics"] = sh.get("lyrics") or g.get("lyrics")
        g["time"], g["dt"], g["frame"] = self.time, dt, self.frame_no
        if "mouse" in sh:
            g["mouse"] = sh["mouse"]
        key = tr["path"]
        changed = key != self._track_key
        self._track_key = key
        beat = sh["audio"]["beat"]
        power = sh["audio"]["beat_power"]
        self._run_timers()
        self._run_tweens()
        for inst in list(self.instances):
            self._frame_tree(inst, dt, tr, changed, beat, power)

    def _frame_tree(self, inst, dt, tr, changed, beat, power):
        if inst.error is not None or inst.dead:
            return
        if changed:
            self._call(inst, "track", (tr,))
        if beat:
            self._call(inst, "beat", (power,))
        self._call(inst, "frame", (dt,))
        if inst.kids and inst.error is None:
            f = inst.fields
            ps = (float(f["w"]), float(f["h"]))
            for k in list(inst.kids):
                if k.dead:
                    continue
                if k.psize != ps:
                    kf = k.fields
                    ow, oh = kf["w"], kf["h"]
                    self._layout(k)
                    if (kf["w"], kf["h"]) != (ow, oh):
                        self._call(k, "resize", (kf["w"], kf["h"]))
                self._frame_tree(k, dt, tr, changed, beat, power)

    gpu = False                                            # холст рисует видеокартой (ставит ScriptCanvas)

    @staticmethod
    def _drawable(inst):
        return "draw" in inst.cls.handlers or inst.fields.get("on_draw").__class__ is L.UserFn or bool(inst.kids)

    def paint(self, p: QPainter):
        gfx = self.gfx
        gfx.p = p
        gpu = self.gpu
        try:
            for inst in sorted(self.instances, key=lambda i: i.fields.get("z", 0)):
                f = inst.fields
                if not f.get("visible", True) or inst.error is not None or inst.dead or not self._drawable(inst):
                    continue
                if f.get("cache"):
                    self._paint_cached(p, inst)
                    continue
                res = f.get("resolution", 1.0)
                if not gpu and isinstance(res, (int, float)) and 0.1 <= res < 0.99:
                    self._paint_lowres(p, inst, float(res))
                    continue
                p.save()
                p.translate(float(f["x"]), float(f["y"]))
                if f.get("clip", True):
                    p.setClipRect(QRectF(0, 0, float(f["w"]), float(f["h"])))
                p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                gfx.reset()
                self._draw_tree(inst)
                while gfx.stack:                            # забытые save()
                    p.restore()
                    gfx.stack.pop()
                p.restore()
        finally:
            gfx.p = None

    def _draw_tree(self, inst):
        """Рисование виджета и (поверх) его вложенных виджетов, у которых auto не выключен."""
        gfx = self.gfx
        p = gfx.p
        if "draw" in inst.cls.handlers or inst.fields.get("on_draw").__class__ is L.UserFn:
            p.save()
            base = len(gfx.stack)
            self._call(inst, "draw", ())
            while len(gfx.stack) > base:
                p.restore()
                gfx.stack.pop()
            p.restore()
        if inst.kids and inst.error is None:
            for k in sorted(inst.kids, key=lambda i: i.fields.get("z", 0)):
                kf = k.fields
                if k.dead or k.error is not None or not kf.get("visible", True) or kf.get("auto", True) is False:
                    continue
                self._paint_kid(k)

    @staticmethod
    def _kid_tf(k):
        f = k.fields
        rot = f.get("rot", 0) or 0
        sc = f.get("scale", 1)
        rot = float(rot) if isinstance(rot, (int, float)) else 0.0
        sc = float(sc) if isinstance(sc, (int, float)) and not isinstance(sc, bool) and sc != 0 else 1.0
        return rot, sc

    def _paint_kid(self, k):
        gfx = self.gfx
        p = gfx.p
        saved = (gfx.brush, gfx.pen, gfx.stack)
        gfx.brush, gfx.pen, gfx.stack = QBrush(QColor(255, 255, 255)), Qt.PenStyle.NoPen, []
        p.save()
        try:
            f = k.fields
            w, h = float(f["w"]), float(f["h"])
            p.translate(float(f["x"]), float(f["y"]))
            rot, sc = self._kid_tf(k)
            if rot or sc != 1.0:
                p.translate(w / 2, h / 2)
                p.rotate(rot)
                p.scale(sc, sc)
                p.translate(-w / 2, -h / 2)
            op = f.get("opacity")
            if isinstance(op, (int, float)) and not isinstance(op, bool) and op < 1.0:
                p.setOpacity(p.opacity() * max(0.0, float(op)))
            if f.get("clip", True):
                p.setClipRect(QRectF(0, 0, w, h), Qt.ClipOperation.IntersectClip)
            self._draw_tree(k)
        finally:
            while gfx.stack:
                p.restore()
                gfx.stack.pop()
            p.restore()
            gfx.brush, gfx.pen, gfx.stack = saved

    def _draw_child(self, inst, c, x=None, y=None, w=None, h=None):
        """draw_child(c) или draw_child(c, x, y, w, h) в on draw родителя: нарисовать вложенный виджет здесь
        и сейчас (поверх уже нарисованного, под тем, что нарисуете дальше). После этого он больше не
        рисуется сам (auto = false)."""
        if self.gfx.p is None:
            raise EchoError("draw_child() можно вызывать только в on draw")
        if not isinstance(c, L.Instance):
            raise EchoError("draw_child(): первый аргумент — вложенный виджет (из child / spawn)")
        if c.parent is not inst:
            raise EchoError("draw_child(): рисовать можно только свои вложенные виджеты")
        f = c.fields
        f["auto"] = False
        if x is not None:
            ow, oh = f["w"], f["h"]
            f["x"], f["y"] = float(x), float(y if y is not None else f["y"])
            if w is not None:
                f["w"] = max(1.0, float(w))
            if h is not None:
                f["h"] = max(1.0, float(h))
            pf = inst.fields
            c.psize = (float(pf["w"]), float(pf["h"]))
            if (f["w"], f["h"]) != (ow, oh):
                self._call(c, "resize", (f["w"], f["h"]))
        if c.dead or c.error is not None or not f.get("visible", True):
            return False
        self._paint_kid(c)
        return True

    def _paint_cached(self, p, inst):
        """cache: true — статичный виджет (фон, рамки) запекается в картинку и дальше только копируется.
        Перерисовывается при смене размера или значения поля cache_key."""
        f = inst.fields
        w, h = max(1, int(f["w"])), max(1, int(f["h"]))
        key = (w, h, L._to_str(f.get("cache_key", "")), self.theme.get("bg"))
        store = self.__dict__.setdefault("_wcache", {})
        ent = store.get(inst.uid)
        if ent is None or ent[0] != key:
            pm = QPixmap(w, h)
            pm.fill(Qt.GlobalColor.transparent)
            cp = QPainter(pm)
            cp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            gfx = self.gfx
            gfx.p = cp
            gfx.reset()
            self._draw_tree(inst)
            while gfx.stack:
                cp.restore()
                gfx.stack.pop()
            cp.end()
            gfx.p = p
            ent = (key, pm)
            store[inst.uid] = ent
        p.drawPixmap(int(f["x"]), int(f["y"]), ent[1])

    def _paint_lowres(self, p, inst, res):
        """resolution < 1: виджет рисуется в уменьшенную картинку и растягивается со сглаживанием —
        для светящихся визуализаторов почти незаметно, а рисование дешевле в 1/res² раз."""
        f = inst.fields
        w, h = max(1, int(f["w"])), max(1, int(f["h"]))
        iw, ih = max(1, int(w * res)), max(1, int(h * res))
        store = self.__dict__.setdefault("_lowres", {})
        img = store.get(inst.uid)
        if img is None or img.width() != iw or img.height() != ih:
            img = QImage(iw, ih, QImage.Format.Format_ARGB32_Premultiplied)
            store[inst.uid] = img
        img.fill(0)
        cp = QPainter(img)
        cp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        cp.scale(iw / w, ih / h)
        gfx = self.gfx
        gfx.p = cp
        gfx.reset()
        self._draw_tree(inst)
        while gfx.stack:
            cp.restore()
            gfx.stack.pop()
        cp.end()
        gfx.p = p
        p.save()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.drawImage(QRectF(float(f["x"]), float(f["y"]), float(w), float(h)), img)
        p.restore()

    # ── мышь и клавиатура ──
    def _to_kid(self, k, lx, ly):
        """Координаты в родителе → координаты внутри вложенного виджета (с учётом rot/scale)."""
        f = k.fields
        x, y = lx - float(f["x"]), ly - float(f["y"])
        rot, sc = self._kid_tf(k)
        if rot or sc != 1.0:
            cx, cy = float(f["w"]) / 2, float(f["h"]) / 2
            dx, dy = x - cx, y - cy
            a = -math.radians(rot)
            ca, sa = math.cos(a), math.sin(a)
            x = (dx * ca - dy * sa) / sc + cx
            y = (dx * sa + dy * ca) / sc + cy
        return x, y

    def hit(self, x, y, events):
        """Самый глубокий (вложенный) виджет под точкой x, y сцены, у которого есть одно из событий."""
        for inst in sorted(self.instances, key=lambda i: -i.fields.get("z", 0)):
            r = self._hit_tree(inst, x - float(inst.fields["x"]), y - float(inst.fields["y"]), events)
            if r is not None:
                return r
        return None

    def _pick(self, x, y):
        return self.hit(x, y, None)

    def _hit_tree(self, inst, lx, ly, events):
        f = inst.fields
        if not f.get("visible", True) or inst.error is not None or inst.dead:
            return None
        w, h = float(f["w"]), float(f["h"])
        inside = 0 <= lx < w and 0 <= ly < h
        if not inside and f.get("clip", True):
            return None
        if inst.kids:
            for k in sorted(inst.kids, key=lambda i: -i.fields.get("z", 0)):
                kx, ky = self._to_kid(k, lx, ly)
                r = self._hit_tree(k, kx, ky, events)
                if r is not None:
                    return r
        if inside and (events is None or any(handles(inst, e) for e in events)):
            hs = f.get("hit_shape")
            if isinstance(hs, S.EPath) and not hs.contains(lx, ly):
                return None
            return inst
        return None

    def local(self, inst, x, y):
        """Точка сцены → координаты внутри виджета (через всех его родителей)."""
        chain = []
        i = inst
        while i is not None:
            chain.append(i)
            i = i.parent
        top = chain[-1]
        lx, ly = x - float(top.fields["x"]), y - float(top.fields["y"])
        for k in reversed(chain[:-1]):
            lx, ly = self._to_kid(k, lx, ly)
        return lx, ly


# ══════════════════════════════════════════════════════════════════════════ #
#  Холст
# ══════════════════════════════════════════════════════════════════════════ #

# ══════════════════════════════════════════════════════════════════════════ #
#  Рисование видеокартой
# ══════════════════════════════════════════════════════════════════════════ #

_GPU = None
_SOFT_GL = ("gdi generic", "basic render", "llvmpipe", "swiftshader", "softpipe", "software")


def gpu_available() -> tuple[bool, str]:
    """(можно ли рисовать сцену через OpenGL, название видеокарты). Проверяется один раз.
    Программный OpenGL (без видеокарты) не считается — на нём CPU-рисование быстрее."""
    global _GPU
    if _GPU is not None:
        return _GPU
    import os
    if os.environ.get("ECHOES_DSL_GPU", "1") == "0" or os.environ.get("QT_QPA_PLATFORM", "") == "offscreen":
        _GPU = (False, "")
        return _GPU
    try:
        from PyQt6.QtGui import QOffscreenSurface, QOpenGLContext, QSurfaceFormat
        from PyQt6.QtOpenGL import QOpenGLVersionFunctionsFactory, QOpenGLVersionProfile
        surf = QOffscreenSurface()
        surf.create()
        ctx = QOpenGLContext()
        if not ctx.create() or not ctx.makeCurrent(surf):
            _GPU = (False, "")
            return _GPU
        vp = QOpenGLVersionProfile()
        vp.setVersion(2, 0)
        f = QOpenGLVersionFunctionsFactory.get(vp, ctx)
        name = str(f.glGetString(0x1F01) or "") if f is not None else ""
        ver = ctx.format().majorVersion()
        ctx.doneCurrent()
        ok = ver >= 2 and bool(name) and not any(k in name.lower() for k in _SOFT_GL)
        _GPU = (ok, name)
    except Exception as e:                                 # noqa: BLE001
        print("[echoscript] OpenGL недоступен:", e)
        _GPU = (False, "")
    return _GPU


class _GLOffscreen:
    """Рисование сцены видеокартой в невидимый буфер (FBO, сглаживание 4×) и копия готовой картинки
    в обычный виджет. В само окно OpenGL не встраивается: иначе Windows переводит всё окно плеера
    на вывод через OpenGL (пересоздание окна, мигание, чёрный экран при разворачивании на весь экран)."""

    def __init__(self):
        from PyQt6.QtGui import QOffscreenSurface, QOpenGLContext
        self.surf = QOffscreenSurface()
        self.surf.create()
        self.ctx = QOpenGLContext()
        self.failed = not self.ctx.create() or not self.ctx.makeCurrent(self.surf)
        self.fbo = None
        self.size = None

    def render(self, canvas):
        """→ QImage кадра или None (тогда холст нарисует сам, процессором)."""
        from PyQt6.QtOpenGL import (QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat,
                                    QOpenGLPaintDevice)
        if self.failed or not self.ctx.makeCurrent(self.surf):
            self.failed = True
            return None
        dpr = max(1.0, float(canvas.devicePixelRatioF()))
        W, H = max(1, int(canvas.width() * dpr + 0.5)), max(1, int(canvas.height() * dpr + 0.5))
        if self.fbo is None or self.size != (W, H):
            ff = QOpenGLFramebufferObjectFormat()
            ff.setSamples(4)
            ff.setAttachment(QOpenGLFramebufferObject.Attachment.CombinedDepthStencil)
            self.fbo = QOpenGLFramebufferObject(W, H, ff)
            self.size = (W, H)
            if not self.fbo.isValid():
                self.failed = True
                return None
        self.fbo.bind()
        dev = QOpenGLPaintDevice(W, H)
        dev.setDevicePixelRatio(dpr)
        p = QPainter(dev)
        try:
            canvas._render(p)
        finally:
            p.end()
            self.fbo.release()
        img = self.fbo.toImage(True)                       # сглаживание + чтение в память (~3–5 мс);
        # True — перевернуть: у OpenGL строки идут снизу вверх, без этого сцена вверх ногами
        img.setDevicePixelRatio(dpr)
        return img

    def close(self):
        try:
            if self.ctx.makeCurrent(self.surf):
                self.fbo = None                            # буфер освобождается при текущем контексте
                self.ctx.doneCurrent()
        except Exception:                                  # noqa: BLE001
            pass


class ScriptCanvas(QWidget):
    """Живая сцена EchoScript (60 кадров/с), ввод мышью передаётся виджетам скрипта."""
    errors_changed = pyqtSignal(list)
    log_line = pyqtSignal(str)
    # режим расстановки: (строка add, класс, {x, y, w, h} в долях окна)
    geometry_committed = pyqtSignal(list)                    # [(строка add, класс, {x, y, w, h})]
    selection_changed = pyqtSignal(int, str)                 # 0, "" — ничего не выбрано
    delete_requested = pyqtSignal(list)                      # [(строка add, класс)]
    undo_requested = pyqtSignal(bool)                        # True — повторить
    block_dropped = pyqtSignal(str, float, float)            # блок перетащили из конструктора: ключ, x, y (пиксели)
    context_requested = pyqtSignal(object)                   # правый клик в расстановке: QPoint на экране

    def __init__(self, win=None, parent=None):
        super().__init__(parent)
        self.win = win
        self.bridge = PlayerBridge(win)
        self.analyzer = AudioAnalyzer(getattr(win, "engine", None))
        self.scene: Scene | None = None
        self.load_error: str | None = None
        self._last = time.monotonic()
        self._press = None
        self._hover = None
        self._drag_last = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        try:
            from frameclock import FrameTimer
            self.timer = FrameTimer(self)
        except Exception:                                  # noqa: BLE001
            self.timer = QTimer(self)
            self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self.timer.start(16)
        self.edit_mode = False
        self._sel = []                                     # [(класс, номер среди экземпляров класса)]
        self._ed = None                                    # текущее перетаскивание
        self._sweep_n = 0
        self._gl = None                                    # поверхность OpenGL (None — рисуем на CPU)
        self._ghost = None                                 # (ключ блока, x, y) — блок тащат над сценой
        self.setAcceptDrops(True)
        self._frame_ms = 0.0                               # среднее время кадра (логика + рисование), мс
        self._paint_ms = 0.0
        self._fps = 0.0
        self._fps_n = 0
        self._fps_t = time.monotonic()
        want = True
        try:
            want = bool((getattr(win, "settings", None) or {}).get("dsl_gpu", True))
        except Exception:                                  # noqa: BLE001
            pass
        self.set_gpu(want)

    # ── GPU ──
    @property
    def gpu(self) -> bool:
        return self._gl is not None

    def set_gpu(self, on: bool) -> bool:
        """Включить/выключить рисование видеокартой. Возвращает, включено ли оно на самом деле."""
        if on and self._gl is None and gpu_available()[0]:
            try:
                self._gl = _GLOffscreen()
                if self._gl.failed:
                    self._gl = None
            except Exception as e:                         # noqa: BLE001
                print("[echoscript] GPU:", e)
                self._gl = None
        elif not on and self._gl is not None:
            self._gl.close()
            self._gl = None
        if self.scene is not None:
            self.scene.gpu = self._gl is not None
        self.update()
        return self._gl is not None

    def stats(self) -> dict:
        return {"gpu": self.gpu, "fps": self._fps, "frame_ms": self._frame_ms, "paint_ms": self._paint_ms,
                "card": gpu_available()[1] if self.gpu else ""}

    def closeEvent(self, e):
        if self._gl is not None:
            self._gl.close()
        super().closeEvent(e)

    # ── загрузка / горячая перезагрузка ──
    def load_source(self, src: str, keep_state=True) -> str | None:
        """Возвращает текст ошибки или None. При ошибке остаётся работать прежняя сцена."""
        try:
            sc = Scene(src, self.bridge, self.analyzer, log_fn=lambda m: self.log_line.emit(m))
            sc.gpu = self._gl is not None
            sc.resize(max(1, self.width()), max(1, self.height()))
            if keep_state:
                sc.adopt_state(self.scene)
            sc.start()
        except EchoError as e:
            self.load_error = str(e)
            self.errors_changed.emit([self.load_error])
            self.update()
            return self.load_error
        except Exception as e:                             # noqa: BLE001
            self.load_error = f"ошибка: {e}"
            self.errors_changed.emit([self.load_error])
            self.update()
            return self.load_error
        self.scene = sc
        self.load_error = None
        self.errors_changed.emit(list(sc.errors))
        if self._sel:
            keep = [k for k in self._sel if self._by_key(k) is not None]
            if keep != self._sel:
                self._sel = keep
                self._emit_sel()
        self.update()
        return None

    def theme(self):
        return self.scene.theme if self.scene else {}

    # ── цикл ──
    def _tick(self):
        if not self.isVisible():
            return
        now = time.monotonic()
        dt = animation_delta(now - self._last)
        self._last = now
        sc = self.scene
        if sc is None:
            return
        self._sweep_n += 1
        if self._sweep_n % 90 == 0:
            MEDIA.sweep()
        if self._gl is not None and self._gl.failed:
            self.set_gpu(False)                            # видеокарта не дала контекст — на CPU
        n = len(sc.errors)
        t0 = time.perf_counter()
        sc.resize(max(1, self.width()), max(1, self.height()))
        sc.frame(dt)
        logic = (time.perf_counter() - t0) * 1000
        self._frame_ms += (logic + self._paint_ms - self._frame_ms) * 0.1
        self._fps_n += 1
        if now - self._fps_t >= 0.5:
            self._fps = self._fps_n / (now - self._fps_t)
            self._fps_n = 0
            self._fps_t = now
        if len(sc.errors) != n:
            self.errors_changed.emit(list(sc.errors))
        self.update()

    def paintEvent(self, _):
        if self._gl is not None:
            t0 = time.perf_counter()
            img = self._gl.render(self)
            if img is not None:
                p = QPainter(self)
                p.drawImage(0, 0, img)
                p.end()
                self._paint_ms += ((time.perf_counter() - t0) * 1000 - self._paint_ms) * 0.1
                return
            self.set_gpu(False)                            # видеокарта отказала — дальше процессором
        p = QPainter(self)
        try:
            self._render(p)
        finally:
            p.end()

    def _render(self, p):
        t0 = time.perf_counter()
        sc = self.scene
        bg = (sc.theme.get("bg") if sc else None) or "#07070b"
        try:
            p.fillRect(self.rect(), to_qcolor(bg))
        except EchoError:
            p.fillRect(self.rect(), QColor("#07070b"))
        if sc is not None:
            n = len(sc.errors)
            sc.paint(p)
            if len(sc.errors) != n:
                self.errors_changed.emit(list(sc.errors))
            if self.edit_mode:
                self._paint_edit(p)
        msg = self.load_error or (sc.errors[-1] if sc and sc.errors else None)
        if msg:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            f = QFont("Segoe UI")
            f.setPixelSize(12)
            f.setWeight(QFont.Weight.DemiBold)
            p.setFont(f)
            fm = QFontMetricsF(f)
            txt = fm.elidedText("Ошибка: " + msg, Qt.TextElideMode.ElideRight, self.width() - 60)
            r = QRectF(14, self.height() - 40, fm.horizontalAdvance(txt) + 24, 28)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(170, 30, 40, 230))
            p.drawRoundedRect(r, 10, 10)
            p.setPen(QColor("#ffffff"))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, txt)
        if self._gl is None:
            self._paint_ms += ((time.perf_counter() - t0) * 1000 - self._paint_ms) * 0.1

    # ══ режим расстановки ══
    HANDLE = 7

    def set_edit_mode(self, on: bool):
        self.edit_mode = bool(on)
        self._ed = None
        if not on and self._sel:
            self._sel = []
            self.selection_changed.emit(0, "")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus if on else Qt.FocusPolicy.ClickFocus)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def _ordinal(self, inst):
        k = 0
        for i in self.scene.instances:
            if i is inst:
                return k
            if i.cls.name == inst.cls.name:
                k += 1
        return k

    def _by_key(self, key):
        cls, k = key
        n = 0
        for i in self.scene.instances:
            if i.cls.name == cls:
                if n == k:
                    return i
                n += 1
        return None

    def selected_all(self) -> list:
        if not self._sel or self.scene is None:
            return []
        out = [self._by_key(k) for k in self._sel]
        return [i for i in out if i is not None]

    def selected(self):
        """Главный выбранный виджет (последний щёлкнутый)."""
        s = self.selected_all()
        return s[-1] if s else None

    def _emit_sel(self):
        p = self.selected()
        self.selection_changed.emit(p.line if p is not None else 0, p.cls.name if p is not None else "")
        self.update()

    def _group_of(self, inst):
        g = inst.fields.get("group") if inst is not None else None
        if not g:
            return [inst] if inst is not None else []
        return [i for i in self.scene.instances if i.fields.get("group") == g]

    def select(self, inst, add=False):
        """inst=None — снять выделение; add — Ctrl: добавить/убрать из выделения. Группа выделяется целиком."""
        if inst is None:
            if self._sel:
                self._sel = []
                self._emit_sel()
            return
        members = self._group_of(inst)
        keys = [(m.cls.name, self._ordinal(m)) for m in members]
        main = (inst.cls.name, self._ordinal(inst))
        if add:
            if main in self._sel:
                self._sel = [k for k in self._sel if k not in keys]
            else:
                self._sel = [k for k in self._sel if k not in keys] + [k for k in keys if k != main] + [main]
        else:
            self._sel = [k for k in keys if k != main] + [main]
        self._emit_sel()

    def select_many(self, insts):
        self._sel = []
        for inst in insts:
            for m in self._group_of(inst):
                k = (m.cls.name, self._ordinal(m))
                if k not in self._sel:
                    self._sel.append(k)
        self._emit_sel()

    def editable(self, inst) -> bool:
        """Двигать можно экземпляр, созданный отдельной строкой add (не в цикле)."""
        if inst is None or inst.line <= 0:
            return False
        return sum(1 for i in self.scene.instances if i.line == inst.line) == 1

    def _rect(self, inst):
        f = inst.fields
        return QRectF(float(f["x"]), float(f["y"]), float(f["w"]), float(f["h"]))

    def _handles(self, r):
        cx, cy = r.center().x(), r.center().y()
        return {"nw": QPointF(r.left(), r.top()), "n": QPointF(cx, r.top()), "ne": QPointF(r.right(), r.top()),
                "e": QPointF(r.right(), cy), "se": QPointF(r.right(), r.bottom()), "s": QPointF(cx, r.bottom()),
                "sw": QPointF(r.left(), r.bottom()), "w": QPointF(r.left(), cy)}

    def _edit_hit(self, x, y):
        sc = self.scene
        sel = self.selected_all()
        if len(sel) == 1 and self.editable(sel[0]):
            for k, pt in self._handles(self._rect(sel[0])).items():
                if abs(pt.x() - x) <= self.HANDLE + 2 and abs(pt.y() - y) <= self.HANDLE + 2:
                    return sel[0], k
        best = None
        for inst in sorted(sc.instances, key=lambda i: -i.fields.get("z", 0)):
            f = inst.fields
            if not f.get("visible", True):
                continue
            if self._rect(inst).contains(QPointF(x, y)):
                # среди перекрывающихся на одном слое — самый маленький (фон не мешает выбирать)
                if best is None or (inst.fields.get("z", 0) == best.fields.get("z", 0) and
                                    f["w"] * f["h"] < best.fields["w"] * best.fields["h"]):
                    best = inst
                elif inst.fields.get("z", 0) < best.fields.get("z", 0):
                    break
        return best, "move"

    def _paint_edit(self, p):
        sc = self.scene
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.setPen(QPen(QColor(255, 255, 255, 14), 1))
        for i in range(1, 20):
            x = self.width() * i / 20
            y = self.height() * i / 20
            p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
            p.drawLine(QPointF(0, y), QPointF(self.width(), y))
        p.setPen(QPen(QColor(255, 255, 255, 40), 1, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(self.width() / 2, 0), QPointF(self.width() / 2, self.height()))
        p.drawLine(QPointF(0, self.height() / 2), QPointF(self.width(), self.height() / 2))
        sel = self.selected_all()
        main = sel[-1] if sel else None
        f = QFont("Segoe UI")
        f.setPixelSize(11)
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        fm = QFontMetricsF(f)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        for inst in sc.instances:
            if not inst.fields.get("visible", True):
                continue
            r = self._rect(inst)
            is_sel = inst in sel
            ed = self.editable(inst)
            col = QColor("#ffb347") if is_sel else (QColor(255, 255, 255, 90) if ed else QColor(255, 255, 255, 45))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(col, 2 if is_sel else 1, Qt.PenStyle.SolidLine if is_sel else Qt.PenStyle.DashLine))
            p.drawRect(r.adjusted(0.5, 0.5, -0.5, -0.5))
            g = inst.fields.get("group")
            label = inst.cls.name + (f"  [{g}]" if g else "") + ("" if ed else "  (в цикле)")
            tw = fm.horizontalAdvance(label) + 12
            lr = QRectF(r.left(), max(0.0, r.top() - 18), tw, 17)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 179, 71, 230) if is_sel else QColor(20, 18, 32, 200))
            p.drawRoundedRect(lr, 4, 4)
            p.setPen(QColor("#1a1408") if is_sel else QColor(255, 255, 255, 200))
            p.drawText(lr, Qt.AlignmentFlag.AlignCenter, label)
        if len(sel) > 1:
            br = self._rect(sel[0])
            for s in sel[1:]:
                br = br.united(self._rect(s))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(255, 179, 71, 160), 1, Qt.PenStyle.DashDotLine))
            p.drawRect(br.adjusted(-4, -4, 4, 4))
        elif main is not None and self.editable(main):
            p.setPen(QPen(QColor("#1a1408"), 1))
            p.setBrush(QColor("#ffb347"))
            for pt in self._handles(self._rect(main)).values():
                p.drawRect(QRectF(pt.x() - self.HANDLE / 2, pt.y() - self.HANDLE / 2, self.HANDLE, self.HANDLE))
        ed = self._ed
        if ed is not None and ed.get("mode") == "band" and ed.get("moved"):
            r = QRectF(QPointF(ed["x0"], ed["y0"]), QPointF(ed["x1"], ed["y1"])).normalized()
            p.setPen(QPen(QColor(255, 179, 71, 200), 1, Qt.PenStyle.DashLine))
            p.setBrush(QColor(255, 179, 71, 30))
            p.drawRect(r)
        if self._ghost is not None:
            try:
                import dsl_layout as _DL
                b = _DL.BLOCKS.get(self._ghost[0])
            except Exception:                              # noqa: BLE001
                b = None
            if b is not None:
                gw, gh = b["geom"][2], b["geom"][3]
                gw = min(gw, 0.5) * self.width()
                gh = min(gh, 0.5) * self.height()
                r = QRectF(self._ghost[1] - gw / 2, self._ghost[2] - gh / 2, gw, gh)
                p.setPen(QPen(QColor("#ffb347"), 2, Qt.PenStyle.DashLine))
                p.setBrush(QColor(255, 179, 71, 40))
                p.drawRoundedRect(r, 8, 8)
                p.setPen(QColor("#ffb347"))
                p.drawText(r, Qt.AlignmentFlag.AlignCenter, "＋ " + b["title"])
        if main is None and self._ghost is None:
            txt = "Перетащите блок из конструктора на сцену  ·  щёлкните виджет, чтобы настроить его  ·  " \
                  "правая кнопка — меню"
            tw = fm.horizontalAdvance(txt) + 28
            r = QRectF((self.width() - tw) / 2, self.height() - 46, tw, 28)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(20, 18, 32, 220))
            p.drawRoundedRect(r, 14, 14)
            p.setPen(QColor("#ece9f6"))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, txt)
        if main is not None:
            if len(sel) > 1:
                txt = f"Выбрано: {len(sel)}  ·  Ctrl+клик — добавить/убрать  ·  стрелки — сдвиг"
            else:
                gg = self._frac(main)
                txt = f"{main.cls.name}   x {gg['x']:.3f}  y {gg['y']:.3f}  w {gg['w']:.3f}  h {gg['h']:.3f}"
                if not self.editable(main):
                    txt += "   — создан в цикле, двигается только в коде"
            tw = fm.horizontalAdvance(txt) + 20
            r = QRectF(14, 12, tw, 24)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(20, 18, 32, 220))
            p.drawRoundedRect(r, 8, 8)
            p.setPen(QColor("#ece9f6"))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, txt)
        p.restore()

    def _is_backdrop(self, inst) -> bool:
        """Виджет почти на весь холст (фон, видеофон)."""
        f = inst.fields
        return f["w"] * f["h"] >= 0.9 * max(1, self.width()) * max(1, self.height())

    def _frac(self, inst):
        W, H = max(1, self.width()), max(1, self.height())
        f = inst.fields
        return {"x": f["x"] / W, "y": f["y"] / H, "w": f["w"] / W, "h": f["h"] / H}

    def _snap(self, moving, r: QRectF, mode, free):
        """Прилипание к сетке 1% и к краям/центрам других виджетов и окна (Alt — без прилипания)."""
        if free:
            return r
        W, H = float(self.width()), float(self.height())
        tol = 6.0
        xs = [0.0, W / 2, W]
        ys = [0.0, H / 2, H]
        for o in self.scene.instances:
            if o in moving or not o.fields.get("visible", True):
                continue
            q = self._rect(o)
            xs += [q.left(), q.center().x(), q.right()]
            ys += [q.top(), q.center().y(), q.bottom()]

        def best(vals, targets):
            bd, bv = tol + 1, None
            for v in vals:
                for t in targets:
                    if abs(v - t) < bd:
                        bd, bv = abs(v - t), t - v
            return bv
        if mode == "move":
            dx = best([r.left(), r.center().x(), r.right()], xs)
            dy = best([r.top(), r.center().y(), r.bottom()], ys)
            dx = dx if dx is not None else round(r.left() / (W / 100)) * (W / 100) - r.left()
            dy = dy if dy is not None else round(r.top() / (H / 100)) * (H / 100) - r.top()
            return r.translated(dx, dy)
        l, t, rr, b = r.left(), r.top(), r.right(), r.bottom()
        if "w" in mode:
            d = best([l], xs)
            l = l + d if d is not None else round(l / (W / 100)) * (W / 100)
        if "e" in mode:
            d = best([rr], xs)
            rr = rr + d if d is not None else round(rr / (W / 100)) * (W / 100)
        if "n" in mode:
            d = best([t], ys)
            t = t + d if d is not None else round(t / (H / 100)) * (H / 100)
        if "s" in mode:
            d = best([b], ys)
            b = b + d if d is not None else round(b / (H / 100)) * (H / 100)
        return QRectF(QPointF(l, t), QPointF(rr, b))

    def _apply_rect(self, inst, r: QRectF):
        W, H = max(1, self.width()), max(1, self.height())
        x = min(max(r.left() / W, 0.0), 0.995)
        y = min(max(r.top() / H, 0.0), 0.995)
        w = min(max(r.width() / W, 0.005), 1.0)
        h = min(max(r.height() / H, 0.005), 1.0)
        inst.fields["_geom"] = (x, y, w, h)
        self.scene._layout(inst)
        self.scene._call(inst, "resize", (inst.fields["w"], inst.fields["h"]))
        return {"x": x, "y": y, "w": w, "h": h}

    def set_rects(self, items):
        """[(inst, QRectF)] → применить и отправить в код одним пакетом (для выравнивания)."""
        out = []
        for inst, r in items:
            if self.editable(inst):
                out.append((inst.line, inst.cls.name, self._apply_rect(inst, r)))
        if out:
            self.geometry_committed.emit(out)
        self.update()

    def _edit_press(self, e):
        x, y = e.position().x(), e.position().y()
        ctrl = bool(e.modifiers() & Qt.KeyboardModifier.ControlModifier)
        inst, mode = self._edit_hit(x, y)
        if e.button() != Qt.MouseButton.LeftButton:
            if inst is not None and inst not in self.selected_all():
                self.select(inst)
            self._ed = None
            if e.button() == Qt.MouseButton.RightButton and inst is not None:
                self.context_requested.emit(e.globalPosition().toPoint())
            return
        sel = self.selected_all()
        if inst is not None and mode == "move" and self._is_backdrop(inst) and inst not in sel and not ctrl:
            # фон на весь экран не мешает: тянуть по нему — рамка выделения, клик без движения — выбрать фон
            self._ed = {"mode": "band", "x0": x, "y0": y, "x1": x, "y1": y, "moved": False, "add": False,
                        "click": inst}
            return
        if inst is None:
            if not ctrl:
                self.select(None)
            self._ed = {"mode": "band", "x0": x, "y0": y, "x1": x, "y1": y, "moved": False, "add": ctrl}
            return
        if ctrl:
            self.select(inst, add=True)
            self._ed = None
            return
        if mode == "move" and inst in sel and len(sel) > 1:
            pass                                            # тянем всё выделение
        elif mode == "move" or inst not in sel:
            self.select(inst)
        movers = [i for i in self.selected_all() if self.editable(i)] if mode == "move" else [inst]
        if not movers or (mode != "move" and not self.editable(inst)):
            self._ed = None
            return
        self._ed = {"mode": mode, "x0": x, "y0": y, "moved": False, "inst": inst,
                    "items": [(i, self._rect(i)) for i in movers]}

    def _edit_move(self, e):
        x, y = e.position().x(), e.position().y()
        ed = self._ed
        if ed is None:
            inst, mode = self._edit_hit(x, y)
            cur = {"nw": Qt.CursorShape.SizeFDiagCursor, "se": Qt.CursorShape.SizeFDiagCursor,
                   "ne": Qt.CursorShape.SizeBDiagCursor, "sw": Qt.CursorShape.SizeBDiagCursor,
                   "n": Qt.CursorShape.SizeVerCursor, "s": Qt.CursorShape.SizeVerCursor,
                   "e": Qt.CursorShape.SizeHorCursor, "w": Qt.CursorShape.SizeHorCursor}.get(mode)
            if cur is None:
                cur = Qt.CursorShape.SizeAllCursor if inst is not None and self.editable(inst) else \
                    Qt.CursorShape.ArrowCursor
            self.setCursor(cur)
            return
        dx, dy = x - ed["x0"], y - ed["y0"]
        if not ed["moved"] and abs(dx) < 3 and abs(dy) < 3:
            return
        ed["moved"] = True
        mode = ed["mode"]
        if mode == "band":
            ed["x1"], ed["y1"] = x, y
            self.update()
            return
        free = bool(e.modifiers() & Qt.KeyboardModifier.AltModifier)
        if mode == "move":
            items = ed["items"]
            br = QRectF(items[0][1])
            for _i, r0 in items[1:]:
                br = br.united(r0)
            moved = br.translated(dx, dy)
            snapped = self._snap([i for i, _ in items], moved, "move", free)
            sx, sy = snapped.left() - br.left(), snapped.top() - br.top()
            ed["geoms"] = [(i.line, i.cls.name, self._apply_rect(i, r0.translated(sx, sy))) for i, r0 in items]
        else:
            inst, r0 = ed["items"][0]
            r = QRectF(r0)
            if "w" in mode:
                r.setLeft(min(r.left() + dx, r.right() - 8))
            if "e" in mode:
                r.setRight(max(r.right() + dx, r.left() + 8))
            if "n" in mode:
                r.setTop(min(r.top() + dy, r.bottom() - 8))
            if "s" in mode:
                r.setBottom(max(r.bottom() + dy, r.top() + 8))
            if e.modifiers() & Qt.KeyboardModifier.ShiftModifier and len(mode) == 2 and r0.height() > 0:
                k = r0.width() / r0.height()                  # Shift — сохранить пропорции
                r.setHeight(r.width() / k) if "s" in mode else r.setTop(r.bottom() - r.width() / k)
            r = self._snap([inst], r, mode, free)
            ed["geoms"] = [(inst.line, inst.cls.name, self._apply_rect(inst, r))]
        self.update()

    def _edit_release(self, e):
        ed, self._ed = self._ed, None
        if ed is None:
            return
        if ed["mode"] == "band":
            if not ed["moved"] and ed.get("click") is not None:
                self.select(ed["click"])
            if ed["moved"]:
                band = QRectF(QPointF(ed["x0"], ed["y0"]), QPointF(ed["x1"], ed["y1"])).normalized()
                hit = [i for i in self.scene.instances
                       if i.fields.get("visible", True) and band.intersects(self._rect(i))
                       and not self._is_backdrop(i) and not self._rect(i).contains(band)]
                if ed["add"]:
                    hit = self.selected_all() + [i for i in hit if i not in self.selected_all()]
                self.select_many(hit)
            self.update()
            return
        if ed["moved"] and ed.get("geoms"):
            self.geometry_committed.emit(ed["geoms"])

    def _edit_key(self, e):
        k = e.key()
        mods = e.modifiers()
        if mods & Qt.KeyboardModifier.ControlModifier:
            if k == Qt.Key.Key_Z:
                self.undo_requested.emit(bool(mods & Qt.KeyboardModifier.ShiftModifier))
                return True
            if k == Qt.Key.Key_Y:
                self.undo_requested.emit(True)
                return True
            if k == Qt.Key.Key_A:
                self.select_many([i for i in self.scene.instances if i.fields.get("visible", True)])
                return True
        if k == Qt.Key.Key_Escape:
            self.select(None)
            return True
        sel = [i for i in self.selected_all() if self.editable(i)]
        if not sel:
            return False
        if k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_requested.emit([(i.line, i.cls.name) for i in sel])
            return True
        step = 0.02 if mods & Qt.KeyboardModifier.ShiftModifier else 0.0025
        d = {Qt.Key.Key_Left: (-step, 0), Qt.Key.Key_Right: (step, 0),
             Qt.Key.Key_Up: (0, -step), Qt.Key.Key_Down: (0, step)}.get(k)
        if d is not None:
            self.set_rects([(i, self._rect(i).translated(d[0] * self.width(), d[1] * self.height())) for i in sel])
            return True
        return False

    # ── ввод ──
    # ── блоки, перетаскиваемые из конструктора ──
    def _drag_key(self, e):
        md = e.mimeData()
        if md is not None and md.hasFormat("application/x-echo-block"):
            return bytes(md.data("application/x-echo-block")).decode("utf-8", "ignore")
        return None

    def dragEnterEvent(self, e):
        key = self._drag_key(e)
        if key:
            e.acceptProposedAction()
            p = e.position()
            self._ghost = (key, p.x(), p.y())
            self.update()

    def dragMoveEvent(self, e):
        key = self._drag_key(e)
        if key:
            e.acceptProposedAction()
            p = e.position()
            self._ghost = (key, p.x(), p.y())
            self.update()

    def dragLeaveEvent(self, e):
        self._ghost = None
        self.update()

    def dropEvent(self, e):
        key = self._drag_key(e)
        self._ghost = None
        if key:
            e.acceptProposedAction()
            p = e.position()
            self.block_dropped.emit(key, p.x(), p.y())
        self.update()

    def mouseDoubleClickEvent(self, e):
        # быстрый второй щелчок в расстановке — обычное нажатие (иначе выделение «теряет» клик)
        self.mousePressEvent(e)

    def mousePressEvent(self, e):
        sc = self.scene
        if sc is None:
            return
        if self.edit_mode:
            self.setFocus()
            self._edit_press(e)
            return
        x, y = e.position().x(), e.position().y()
        sc.g["mouse"] = {"x": x, "y": y, "down": True}
        inst = sc.hit(x, y, ("press", "click", "drag", "release"))
        self._press = (inst, x, y, e.button())
        self._drag_last = (x, y)
        if inst is not None:
            inst.fields["pressed"] = True
            lx, ly = sc.local(inst, x, y)
            btn = {Qt.MouseButton.LeftButton: 1, Qt.MouseButton.RightButton: 2}.get(e.button(), 3)
            sc._call(inst, "press", (lx, ly, btn))

    def mouseMoveEvent(self, e):
        sc = self.scene
        if sc is None:
            return
        if self.edit_mode:
            self._edit_move(e)
            return
        x, y = e.position().x(), e.position().y()
        sc.g["mouse"] = {"x": x, "y": y, "down": bool(e.buttons())}
        if self._press is not None and self._press[0] is not None and e.buttons():
            inst = self._press[0]
            lx, ly = sc.local(inst, x, y)
            px, py = self._drag_last
            sc._call(inst, "drag", (lx, ly, x - px, y - py))
            self._drag_last = (x, y)
            return
        inst = sc.hit(x, y, ("move", "enter", "leave", "click", "press", "wheel", "drag"))
        if inst is not self._hover:
            if self._hover is not None:
                self._hover.fields["hover"] = False
                sc._call(self._hover, "leave", ())
            self._hover = inst
            if inst is not None:
                inst.fields["hover"] = True
                sc._call(inst, "enter", ())
        self.setCursor(Qt.CursorShape.PointingHandCursor if inst is not None and
                       any(handles(inst, ev) for ev in ("click", "press", "drag"))
                       else Qt.CursorShape.ArrowCursor)
        if inst is not None:
            lx, ly = sc.local(inst, x, y)
            sc._call(inst, "move", (lx, ly))

    def mouseReleaseEvent(self, e):
        sc = self.scene
        if sc is not None and self.edit_mode:
            self._edit_release(e)
            return
        if sc is None or self._press is None:
            return
        inst, x0, y0, btn0 = self._press
        self._press = None
        x, y = e.position().x(), e.position().y()
        sc.g["mouse"] = {"x": x, "y": y, "down": False}
        if inst is None:
            return
        inst.fields["pressed"] = False
        lx, ly = sc.local(inst, x, y)
        btn = {Qt.MouseButton.LeftButton: 1, Qt.MouseButton.RightButton: 2}.get(btn0, 3)
        sc._call(inst, "release", (lx, ly, btn))
        if abs(x - x0) < 6 and abs(y - y0) < 6:
            sc._call(inst, "click", (lx, ly, btn))

    def wheelEvent(self, e):
        sc = self.scene
        if sc is None or self.edit_mode:
            return
        x, y = e.position().x(), e.position().y()
        inst = sc.hit(x, y, ("wheel",))
        if inst is not None:
            lx, ly = sc.local(inst, x, y)
            sc._call(inst, "wheel", (e.angleDelta().y() / 120.0, lx, ly))

    def leaveEvent(self, e):
        if self.scene is not None and self._hover is not None:
            self._hover.fields["hover"] = False
            self.scene._call(self._hover, "leave", ())
        self._hover = None

    def keyPressEvent(self, e):
        sc = self.scene
        if sc is None:
            return
        if self.edit_mode:
            if not self._edit_key(e):
                super().keyPressEvent(e)
            return
        name = key_name(e)
        if not name:
            return
        sc.world.keys_down.add(name)
        if not e.isAutoRepeat():
            sc.key_event(name, True)

    def keyReleaseEvent(self, e):
        sc = self.scene
        if sc is None or self.edit_mode or e.isAutoRepeat():
            return
        name = key_name(e)
        if name:
            sc.world.keys_down.discard(name)
            sc.key_event(name, False)
