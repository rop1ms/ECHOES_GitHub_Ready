# daw_project.py
"""
Проект студии (тема «Echoes Studio»): каналы (стойка каналов), паттерны с нотами, плейлист
(клипы паттернов и аудиоклипы), микшер (мастер + 20 дорожек с цепочками эффектов),
аудио-источники. Всё — обычный словарь, который сохраняется в JSON.

Позиции нот и клипов — в долях (четвертях); паттерн по умолчанию — такт (4 доли, 16 шагов).
Аудио-источник — «рецепт»: файл, при необходимости — только вокал или только бит
(разделение нейросетью), обрезка, смена скорости, растяжение по долям и сдвиг тона.
Готовый звук кэшируется.

Здесь же — сборка «аранжировки» для движка: какие куски звука с какого сэмпла звучат
на каких каналах (ноты рендерятся генераторами и кэшируются).
"""
from __future__ import annotations

import data_home
import hashlib
import json
import math
import os
import re
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path

import numpy as np

import daw_dsp as D
import daw_fx as FX
import daw_inst as I
from daw_dsp import SR

STUDIO = data_home.root() / "studio"
PROJECTS = STUDIO / "projects"
RECS = STUDIO / "recordings"
CACHE = STUDIO / "cache"
EXPORTS = STUDIO / "exports"
N_INSERTS = 20
N_LANES = 24
CACHE_LIMIT = 3 * 1024 ** 3                 # кэш обработанного звука на диске


def uid() -> str:
    return uuid.uuid4().hex[:8]


def safe_name(s: str) -> str:
    s = re.sub(r'[\\/:*?"<>|]+', " ", str(s)).strip()
    return (s or "Проект")[:80]


# ------------------------------------------------------------------ #
#  Создание                                                           #
# ------------------------------------------------------------------ #

def new_channel(name, gen="sampler", params=None, insert=0, color=None) -> dict:
    return {"id": uid(), "name": name, "gen": gen, "params": dict(params or {}), "vol": 0.78, "pan": 0.0,
            "mute": False, "insert": int(insert), "color": color or I.DEFAULT_COLORS[0], "root": 60}


def new_pattern(name, length=4.0) -> dict:
    return {"id": uid(), "name": name, "len": float(length), "notes": {}, "color": "#6f8f4f"}


def new_insert(i) -> dict:
    return {"name": "Мастер" if i == 0 else f"Дорожка {i}", "vol": 1.0, "pan": 0.0, "mute": False,
            "solo": False, "fx": [None] * 10, "color": None}


def new_project(name="Без названия", template="drums") -> dict:
    p = {"v": 1, "name": name, "bpm": 130.0, "swing": 0.0, "master_vol": 0.85, "channels": [], "patterns": [],
         "sources": [], "clips": [], "lanes": [{"name": f"Дорожка {i + 1}", "mute": False, "color": None}
                                               for i in range(N_LANES)],
         "mixer": [new_insert(i) for i in range(N_INSERTS + 1)], "mode": "pat", "sel_pattern": None,
         "sel_channel": None, "created": time.time()}
    p["mixer"][0]["fx"][0] = FX.new_slot("limiter", {"ceil": -0.3})
    if template == "drums":
        for i, (nm, smp) in enumerate((("Бочка", "Бочка Punch"), ("Хлопок", "Хлопок"),
                                       ("Хэт", "Хэт закрытый"), ("Малый", "Малый Tight")), 1):
            c = new_channel(nm, "sampler", {"sample": "kit:" + smp}, insert=i, color=I.DEFAULT_COLORS[i - 1])
            p["channels"].append(c)
            p["mixer"][i]["name"] = nm
    pat = new_pattern("Паттерн 1")
    p["patterns"].append(pat)
    p["sel_pattern"] = pat["id"]
    if p["channels"]:
        p["sel_channel"] = p["channels"][0]["id"]
    return p


def ensure(p: dict) -> dict:
    """Дополнить старый/битый проект до текущего вида."""
    p.setdefault("v", 1)
    p.setdefault("name", "Без названия")
    p.setdefault("bpm", 130.0)
    p.setdefault("swing", 0.0)
    p.setdefault("master_vol", 0.85)
    for k in ("channels", "patterns", "sources", "clips"):
        p.setdefault(k, [])
    lanes = p.setdefault("lanes", [])
    while len(lanes) < N_LANES:
        lanes.append({"name": f"Дорожка {len(lanes) + 1}", "mute": False, "color": None})
    mix = p.setdefault("mixer", [])
    while len(mix) < N_INSERTS + 1:
        mix.append(new_insert(len(mix)))
    for m in mix:
        fx = m.setdefault("fx", [])
        while len(fx) < 10:
            fx.append(None)
        m.setdefault("vol", 1.0)
        m.setdefault("pan", 0.0)
    if not p["patterns"]:
        p["patterns"].append(new_pattern("Паттерн 1"))
    if not any(pt["id"] == p.get("sel_pattern") for pt in p["patterns"]):
        p["sel_pattern"] = p["patterns"][0]["id"]
    p.setdefault("mode", "pat")
    for c in p["channels"]:
        c.setdefault("vol", 0.78)
        c.setdefault("pan", 0.0)
        c.setdefault("mute", False)
        c.setdefault("insert", 0)
        c.setdefault("root", 60)
        c.setdefault("params", {})
        c.setdefault("color", I.DEFAULT_COLORS[0])
    return p


# ------------------------------------------------------------------ #
#  Поиск                                                              #
# ------------------------------------------------------------------ #

def by_id(items, i):
    for x in items:
        if x.get("id") == i:
            return x
    return None


def chan(p, cid):
    return by_id(p["channels"], cid)


def pattern(p, pid):
    return by_id(p["patterns"], pid)


def source(p, sid):
    return by_id(p["sources"], sid)


def cur_pattern(p):
    return pattern(p, p.get("sel_pattern")) or (p["patterns"][0] if p["patterns"] else None)


def cur_channel(p):
    return chan(p, p.get("sel_channel")) or (p["channels"][0] if p["channels"] else None)


def spb(p) -> float:
    """Сэмплов на долю."""
    return SR * 60.0 / max(20.0, float(p.get("bpm") or 120))


def song_beats(p) -> float:
    end = 0.0
    for k in p["clips"]:
        end = max(end, float(k["start"]) + float(k["len"]))
    return max(16.0, math.ceil(end / 4.0) * 4.0)


def pattern_used_len(pat) -> float:
    """Длина паттерна по нотам (кратно такту), не меньше заданной."""
    end = float(pat.get("len", 4.0))
    for ns in pat.get("notes", {}).values():
        for n in ns:
            end = max(end, n[0] + n[1])
    return max(4.0, math.ceil(end / 4.0 - 1e-6) * 4.0)


def free_insert(p) -> int:
    used = {c.get("insert", 0) for c in p["channels"]}
    for i in range(1, N_INSERTS + 1):
        if i not in used:
            return i
    return 0


def free_lane(p, start=0.0, length=4.0) -> int:
    """Первая дорожка плейлиста, где в [start, start+length) ничего нет."""
    for li in range(N_LANES):
        if all(not (k["lane"] == li and k["start"] < start + length and start < k["start"] + k["len"])
               for k in p["clips"]):
            return li
    return 0


# ── шаги и ноты ── #

def steps_of(pat, cid) -> set:
    out = set()
    for n in pat.get("notes", {}).get(cid, []):
        q = n[0] * 4
        if abs(q - round(q)) < 1e-6:
            out.add(int(round(q)))
    return out


def toggle_step(pat, cid, step, root=60, vel=0.78, force=None) -> bool:
    """Включить/выключить шаг (1/16). force=True/False — задать явно. Возвращает итоговое состояние."""
    ns = pat.setdefault("notes", {}).setdefault(cid, [])
    t = step / 4.0
    hit = [n for n in ns if abs(n[0] - t) < 1e-6]
    if hit and force is not True:
        for n in hit:
            ns.remove(n)
        if not ns:
            pat["notes"].pop(cid, None)
        return False
    if not hit and force is not False:
        ns.append([t, 0.25, int(root), float(vel)])
        ns.sort(key=lambda n: (n[0], n[2]))
        return True
    return bool(hit)


def notes_of(pat, cid):
    return pat.get("notes", {}).get(cid, [])


# ------------------------------------------------------------------ #
#  История правок                                                     #
# ------------------------------------------------------------------ #

class History:
    def __init__(self, limit=80):
        self.undo, self.redo = [], []
        self.limit = limit

    def push(self, p, label=""):
        self.undo.append((label, json.dumps(p, ensure_ascii=False)))
        if len(self.undo) > self.limit:
            self.undo.pop(0)
        self.redo.clear()

    def do_undo(self, p):
        if not self.undo:
            return None, ""
        label, s = self.undo.pop()
        self.redo.append((label, json.dumps(p, ensure_ascii=False)))
        return json.loads(s), label

    def do_redo(self, p):
        if not self.redo:
            return None, ""
        label, s = self.redo.pop()
        self.undo.append((label, json.dumps(p, ensure_ascii=False)))
        return json.loads(s), label


# ------------------------------------------------------------------ #
#  Сохранение                                                         #
# ------------------------------------------------------------------ #

def project_path(name: str) -> Path:
    return PROJECTS / (safe_name(name) + ".echoproj")


def save(p: dict, path=None) -> Path:
    PROJECTS.mkdir(parents=True, exist_ok=True)
    path = Path(path) if path else project_path(p.get("name", "Проект"))
    p["saved"] = time.time()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(p, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, path)
    return path


def load(path) -> dict:
    return ensure(json.loads(Path(path).read_text("utf-8")))


def list_projects():
    PROJECTS.mkdir(parents=True, exist_ok=True)
    out = []
    for f in PROJECTS.glob("*.echoproj"):
        try:
            out.append((f.stat().st_mtime, f))
        except OSError:
            pass
    return [f for _t, f in sorted(out, reverse=True)]


# ------------------------------------------------------------------ #
#  Аудио-источники                                                    #
# ------------------------------------------------------------------ #

def new_source(name, path, stem="mix", warp=None, semis=0.0, formant=True, gain=1.0, **kw) -> dict:
    s = {"id": uid(), "name": name, "path": str(path), "stem": stem, "warp": warp, "semis": float(semis),
         "formant": bool(formant), "gain": float(gain)}
    s.update(kw)
    return s


def recipe_key(src: dict, semis=0.0, ratio=1.0) -> str:
    r = {k: src.get(k) for k in ("path", "stem", "warp", "semis", "formant", "trim", "fx", "resample", "rev")}
    r["xs"], r["xr"] = round(float(semis), 3), round(float(ratio), 5)
    try:
        st = os.stat(src.get("path", ""))
        r["sig"] = [st.st_size, int(st.st_mtime)]
    except OSError:
        pass
    return hashlib.sha1(json.dumps(r, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]


class SourceCache:
    """Готовый звук источников (int16 стерео в памяти + .npy на диске для обработанных)."""

    def __init__(self):
        self.mem: OrderedDict = OrderedDict()
        self.lock = threading.RLock()

    def get_loaded(self, src, semis=0.0, ratio=1.0):
        with self.lock:
            return self.mem.get(recipe_key(src, semis, ratio))

    def load(self, src: dict, semis=0.0, ratio=1.0, progress=None, cancel=None) -> np.ndarray:
        key = recipe_key(src, semis, ratio)
        with self.lock:
            a = self.mem.get(key)
            if a is not None:
                self.mem.move_to_end(key)
                return a
        processed = bool(src.get("warp")) or abs(float(src.get("semis") or 0) + semis) > 1e-3 or \
            abs(ratio - 1) > 1e-4 or src.get("stem", "mix") != "mix" or bool(src.get("trim")) or \
            bool(src.get("fx")) or bool(src.get("resample")) or bool(src.get("rev"))
        f = CACHE / f"{key}.npy"
        a = None
        if processed and f.exists():
            try:
                a = np.load(f)
                os.utime(f, None)
            except Exception:                    # noqa: BLE001
                a = None
        if a is None:
            a = self._make(src, semis, ratio, progress, cancel)
            if processed:
                try:
                    CACHE.mkdir(parents=True, exist_ok=True)
                    np.save(f, a)
                    _prune_cache()
                except Exception:                # noqa: BLE001
                    pass
        with self.lock:
            self.mem[key] = a
            self.mem.move_to_end(key)
            self._limit()
        return a

    def _limit(self):
        tot = sum(v.nbytes for v in self.mem.values())
        while tot > 1200 * 1024 ** 2 and len(self.mem) > 2:
            _k, v = self.mem.popitem(last=False)
            tot -= v.nbytes

    def keep_only(self, keys: set):
        with self.lock:
            for k in [k for k in self.mem if k not in keys]:
                self.mem.pop(k, None)

    def clear(self):
        with self.lock:
            self.mem.clear()

    def _make(self, src, semis, ratio, progress, cancel) -> np.ndarray:
        path = src.get("path", "")
        stem = src.get("stem", "mix")
        if stem in ("vocals", "inst"):
            import daw_sep
            r = daw_sep.separate(path, progress, cancel, quality="cached")
            x = r[stem]
            del r
        else:
            x = D.decode(path, SR, 2)
        tr = src.get("trim")
        if tr:
            a, b = int(round(tr[0] * SR)), int(round(tr[1] * SR))
            if a < 0:                                    # начало раньше файла — тишина спереди
                x = np.concatenate([np.zeros((-a, x.shape[1]), np.float32), x])
                b -= a
                a = 0
            x = x[a:max(a + 1, b)]
        rs = src.get("resample")
        if rs and abs(float(rs) - 1) > 1e-6:             # скорость как у диджея: тон едет вместе с темпом
            x = D.resample(x, 1.0 / float(rs))
        w = src.get("warp")
        tot_semis = float(src.get("semis") or 0) + float(semis)
        if w:
            pairs = np.asarray(w["pairs"], np.float64)
            out_len = int(float(w["len"]) * SR)
            fn = D.beat_map(pairs[:, 1], pairs[:, 0])
            nf = int(w.get("nfft", 2048))
            x = D.warp(x, out_len, fn, tot_semis, bool(src.get("formant", True)),
                       n_fft=nf, hop=nf // 4, cancel=cancel)
        elif abs(ratio - 1) > 1e-4 or abs(tot_semis) > 1e-3:
            x = D.warp(x, int(round(len(x) * ratio)), None, tot_semis, bool(src.get("formant", True)), cancel=cancel)
        if src.get("rev"):
            x = x[::-1].copy()
        fxs = src.get("fx")
        if fxs:
            x = apply_clip_fx(x, fxs)
        g = float(src.get("gain", 1.0))
        if abs(g - 1) > 1e-4:
            x = x * g
        return D.to_i16(x)


def apply_clip_fx(x, fxs: dict) -> np.ndarray:
    """Запечённые в источник фильтры: {'hp': Гц, 'lp': Гц}."""
    from scipy import signal as sps
    sos = []
    if fxs.get("hp"):
        sos.append(D.cut("hp", float(fxs["hp"]), 24))
    if fxs.get("lp"):
        sos.append(D.cut("lp", float(fxs["lp"]), 24))
    if sos:
        x = sps.sosfilt(np.vstack(sos), x, axis=0).astype(np.float32)
    return x


def _prune_cache():
    try:
        fs = [(f.stat().st_mtime, f.stat().st_size, f) for f in CACHE.glob("*.npy")]
        fs.sort(reverse=True)
        tot = 0
        for _t, sz, f in fs:
            tot += sz
            if tot > CACHE_LIMIT:
                f.unlink(missing_ok=True)
    except Exception:                            # noqa: BLE001
        pass


def clip_ratio(p, k, src) -> float:
    """Во сколько раз растянут звук клипа (подгонка под темп проекта)."""
    if k.get("stretch") and src.get("bpm"):
        return float(src["bpm"]) / float(p["bpm"])
    return 1.0


# ------------------------------------------------------------------ #
#  Аранжировка для движка                                             #
# ------------------------------------------------------------------ #

class Event:
    __slots__ = ("start", "data", "ch", "gain", "off", "length", "fin", "fout", "clip")

    def __init__(self, start, data, ch, gain=1.0, off=0, length=None, fin=0, fout=0, clip=None):
        self.start = int(start)
        self.data = data
        self.ch = ch                      # id канала
        self.gain = float(gain)
        self.off = int(off)
        self.length = int(len(data) - off if length is None else length)
        self.fin = int(fin)
        self.fout = int(fout)
        self.clip = clip

    @property
    def end(self):
        return self.start + self.length

    def shifted(self, d):
        return Event(self.start + d, self.data, self.ch, self.gain, self.off, self.length, self.fin, self.fout,
                     self.clip)


class Arrangement:
    def __init__(self, events=None, length=0, mode="pat", bpm=120.0, pending=0):
        self.events = events or []
        self.length = int(length)         # длина цикла в сэмплах
        self.mode = mode
        self.bpm = bpm
        self.pending = pending            # сколько источников ещё готовится


class Renderer:
    """Рендер нот генераторами с кэшем и сборка аранжировки."""

    def __init__(self, sources: SourceCache):
        self.notes: OrderedDict = OrderedDict()
        self.segs: OrderedDict = OrderedDict()
        self.nbytes = 0
        self.sbytes = 0
        self.sources = sources
        self.lock = threading.RLock()

    def note(self, g, pitch, vel, n_len):
        key = (g.key(), int(pitch), round(float(vel), 3), int(n_len))
        with self.lock:
            a = self.notes.get(key)
            if a is not None:
                self.notes.move_to_end(key)
                return a
        a = g.render(int(pitch), float(vel), n_len / SR)
        with self.lock:
            self.notes[key] = a
            self.nbytes += a.nbytes
            while self.nbytes > 350 * 1024 ** 2 and len(self.notes) > 8:
                _k, v = self.notes.popitem(last=False)
                self.nbytes -= v.nbytes
        return a

    def preview_note(self, c, pitch, vel=0.8, beats=1.0, bpm=130.0):
        """Нота канала для прослушивания (клавиша пианоролла, шаг)."""
        g = I.make(c.get("gen", "sampler"), c.setdefault("params", {}))
        if g is None:
            return None
        n = int(beats * SR * 60.0 / bpm)
        return self.note(g, pitch, vel, max(1, n))

    def segment(self, p, c, pat, clip_len_beats, swing=0.0):
        """Звук канала c в паттерне pat для клипа длиной clip_len долей (паттерн повторяется)."""
        ns = pat.get("notes", {}).get(c["id"])
        if not ns:
            return None
        g = I.make(c.get("gen", "sampler"), c.setdefault("params", {}))
        if g is None:                                  # аудиоканал — нот не играет
            return None
        sp = spb(p)
        plen = pattern_used_len(pat)
        key = (g.key(), json.dumps(ns), round(clip_len_beats, 4), round(sp, 3), round(swing, 3), plen)
        with self.lock:
            a = self.segs.get(key)
            if a is not None:
                self.segs.move_to_end(key)
                return a
        evs = []
        reps = int(math.ceil(clip_len_beats / plen - 1e-9))
        for r in range(max(1, reps)):
            for n in ns:
                t = r * plen + n[0]
                if t >= clip_len_beats - 1e-9:
                    continue
                st = t * 4
                if swing > 0 and abs(st - round(st)) < 1e-6 and int(round(st)) % 2 == 1:
                    t += swing * 0.25 * 0.5
                ln = min(n[1], clip_len_beats - t) if not g.ONE_SHOT else n[1]
                evs.append((t, max(0.01, ln), n[2], n[3]))
        if not evs:
            return None
        tail = int(max(0.05, g.tail()) * SR) + 64
        total = int(clip_len_beats * sp) + tail
        buf = np.zeros((total, 2), np.float32)
        for t, ln, pitch, vel in evs:
            a = self.note(g, pitch, vel, max(1, int(ln * sp)))
            s = int(round(t * sp))
            m = min(len(a), total - s)
            if m > 0:
                buf[s:s + m] += a[:m]
        nz = np.nonzero(np.abs(buf).max(axis=1) > 1e-5)[0]
        if len(nz):
            buf = buf[:max(int(clip_len_beats * sp), nz[-1] + 1)]
        with self.lock:
            self.segs[key] = buf
            self.sbytes += buf.nbytes
            while self.sbytes > 400 * 1024 ** 2 and len(self.segs) > 4:
                _k, v = self.segs.popitem(last=False)
                self.sbytes -= v.nbytes
        return buf

    def build(self, p, mode=None, cancel=None, wait_sources=False, on_source=None) -> Arrangement | None:
        mode = mode or p.get("mode", "pat")
        sp = spb(p)
        sw = float(p.get("swing") or 0)
        events = []
        pending = 0
        if mode == "pat":
            pat = cur_pattern(p)
            if pat is None:
                return Arrangement([], int(4 * sp), mode, p["bpm"])
            L = pattern_used_len(pat)
            length = int(round(L * sp))
            for c in p["channels"]:
                if cancel is not None and cancel.is_set():
                    return None
                a = self.segment(p, c, pat, L, sw)
                if a is None:
                    continue
                e = Event(0, a, c["id"])
                events.append(e)
                if e.end > length:                         # хвост последних нот — в начало цикла
                    events.append(e.shifted(-length))
            return Arrangement(events, length, mode, p["bpm"])
        for k in p["clips"]:
            if cancel is not None and cancel.is_set():
                return None
            if k.get("mute") or p["lanes"][min(len(p["lanes"]) - 1, k.get("lane", 0))].get("mute"):
                continue
            st = int(round(float(k["start"]) * sp))
            if k["kind"] == "pat":
                pat = pattern(p, k["ref"])
                if pat is None:
                    continue
                for c in p["channels"]:
                    a = self.segment(p, c, pat, float(k["len"]), sw)
                    if a is not None:
                        events.append(Event(st, a, c["id"], clip=k["id"]))
            else:
                src = source(p, k["ref"])
                if src is None:
                    continue
                semis = float(k.get("semis") or 0)
                ratio = clip_ratio(p, k, src)
                a = self.sources.get_loaded(src, semis, ratio)
                if a is None:
                    if wait_sources:
                        a = self.sources.load(src, semis, ratio, cancel=cancel)
                    else:
                        pending += 1
                        if on_source:
                            on_source(src, semis, ratio)
                        continue
                off = int(round(float(k.get("off") or 0) * SR * ratio))
                ln = int(round(float(k["len"]) * sp))
                ln = max(0, min(ln, len(a) - off))
                if ln <= 0:
                    continue
                fin = int(float(k.get("fin") or 0) * sp)
                fout = int(float(k.get("fout") or 0) * sp)
                events.append(Event(st, a, k.get("chan"), float(k.get("gain", 1.0)), off, ln, fin, fout,
                                    clip=k["id"]))
        length = int(round(song_beats(p) * sp))
        events.sort(key=lambda e: e.start)
        return Arrangement(events, length, mode, p["bpm"], pending)

    def clear(self):
        with self.lock:
            self.notes.clear()
            self.segs.clear()
            self.nbytes = self.sbytes = 0


# ------------------------------------------------------------------ #
#  Волна для отрисовки                                                #
# ------------------------------------------------------------------ #

_PEAKS: OrderedDict = OrderedDict()


def peaks(key, a: np.ndarray, hop=512) -> np.ndarray:
    """(n//hop, 2): мин и макс по моно — для рисования формы волны аудиоклипов."""
    p = _PEAKS.get(key)
    if p is not None:
        return p
    m = D.as_float(a).mean(axis=1) if a.ndim == 2 else D.as_float(a)
    n = len(m) // hop
    if n < 1:
        p = np.zeros((1, 2), np.float32)
    else:
        r = m[:n * hop].reshape(n, hop)
        p = np.stack([r.min(axis=1), r.max(axis=1)], 1).astype(np.float32)
    _PEAKS[key] = p
    if len(_PEAKS) > 80:
        _PEAKS.popitem(last=False)
    return p
