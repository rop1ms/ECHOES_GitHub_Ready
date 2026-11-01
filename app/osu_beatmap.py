# osu_beatmap.py
"""
Карты из настоящего osu! в esu!: разбор .osu (форматы v3–v14), импорт .osz и папки Songs
установленного osu! (stable).

  * parse_osu — секции файла: General, Metadata, Difficulty, Events (фон, видео, перерывы),
    TimingPoints (красные и зелёные линии, kiai, наборы звуков, громкость), Colours, HitObjects;
  * convert — в формат карт esu! (тот же, что у генератора osu_mapgen): круги, слайдеры (B / P / L / C,
    скорость по зелёным линиям, тики по SliderTickRate), спиннеры, новые комбо с пропуском цветов,
    хитсаунды объектов и краёв слайдеров, свои сэмплы карты, kiai, перерывы, цвета комбо;
  * apply_stacking — «стопки» нот, как в osu! (StackLeniency, старый алгоритм для v < 6);
  * import_* — тяжёлая работа (разбор, пути слайдеров, звёзды) идёт в отдельном процессе
    (bgproc), результат: ~/.neon_player/osu/imported/<md5>.json и индекс beatmaps.json.

Папка Songs установленного osu! не копируется — карты читаются на месте. .osz распаковываются
в ~/.neon_player/osu/songs.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
import zipfile
from pathlib import Path

import numpy as np

import osu_mapgen as G

ROOT = G.OSU_DIR
SONGS = ROOT / "songs"
CONV = ROOT / "imported"
INDEX = ROOT / "beatmaps.json"
OFFSETS = ROOT / "beatmap_offsets.json"
CONV_VERSION = 3                      # меняется — карты переразбираются
AUDIO_EXT = (".mp3", ".ogg", ".wav", ".flac", ".m4a", ".opus")
IMG_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
VIDEO_EXT = (".mp4", ".avi", ".flv", ".wmv", ".mkv", ".webm", ".mov", ".m4v", ".mpg", ".mpeg")
SETS = {0: None, 1: "normal", 2: "soft", 3: "drum"}
SET_NAMES = {"normal": 1, "soft": 2, "drum": 3, "none": 0}
_lock = threading.Lock()


# ------------------------------------------------------------------ #
#  Разбор .osu                                                        #
# ------------------------------------------------------------------ #

def _num(s, default=0.0) -> float:
    try:
        v = float(str(s).strip())
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def parse_osu(text: str) -> dict:
    lines = text.replace("﻿", "").splitlines()
    ver = 14
    if lines:
        m = re.search(r"osu file format v(\d+)", lines[0])
        if m:
            ver = int(m.group(1))
    out = {"version": ver, "general": {}, "metadata": {}, "difficulty": {}, "events": [], "timing": [],
           "colours": {}, "objects": []}
    sec = None
    for raw in lines[1:]:
        ln = raw.strip()
        if not ln or ln.startswith("//"):
            continue
        if ln.startswith("[") and ln.endswith("]"):
            sec = ln[1:-1].strip().lower()
            continue
        if sec in ("general", "metadata", "difficulty", "editor"):
            if ":" in ln:
                k, v = ln.split(":", 1)
                if sec != "editor":
                    out[sec][k.strip()] = v.strip()
        elif sec == "colours":
            if ":" in ln:
                k, v = ln.split(":", 1)
                out["colours"][k.strip()] = v.strip()
        elif sec == "events":
            out["events"].append(raw.rstrip())
        elif sec == "timingpoints":
            out["timing"].append(ln)
        elif sec == "hitobjects":
            out["objects"].append(ln)
    return out


def _timing(lines, ver) -> list[dict]:
    tps = []
    for ln in lines:
        p = [x.strip() for x in ln.split(",")]
        if len(p) < 2:
            continue
        t = _num(p[0])
        bl = _num(p[1])
        meter = int(_num(p[2], 4)) if len(p) > 2 and p[2] else 4
        ss = int(_num(p[3], 0)) if len(p) > 3 and p[3] else 0
        si = int(_num(p[4], 0)) if len(p) > 4 and p[4] else 0
        vol = int(_num(p[5], 100)) if len(p) > 5 and p[5] else 100
        unin = (p[6].strip() != "0") if len(p) > 6 and p[6] else bl > 0
        fx = int(_num(p[7], 0)) if len(p) > 7 and p[7] else 0
        if not math.isfinite(bl):
            continue
        tps.append({"t": t, "bl": bl, "meter": meter or 4, "ss": ss, "si": si, "vol": max(0, min(100, vol)),
                    "red": bool(unin and bl > 0), "kiai": bool(fx & 1)})
    tps.sort(key=lambda x: (x["t"], not x["red"]))        # красная линия раньше зелёной в тот же миг
    return tps


class _TimingIndex:
    """Быстрый поиск действующих линий на момент t (как в osu!: последняя линия с time ≤ t)."""

    def __init__(self, tps: list[dict]):
        self.tps = tps
        self.reds = [x for x in tps if x["red"]] or [{"t": 0.0, "bl": 500.0, "meter": 4, "ss": 0, "si": 0,
                                                      "vol": 100, "red": True, "kiai": False}]
        self.red_t = [x["t"] for x in self.reds]
        self.all_t = [x["t"] for x in tps]

    def red(self, t):
        import bisect
        i = bisect.bisect_right(self.red_t, t + 1e-6) - 1
        return self.reds[max(0, i)]

    def sv(self, t) -> float:
        """Множитель скорости слайдера: зелёная линия после последней красной (до неё — 1.0)."""
        import bisect
        i = bisect.bisect_right(self.all_t, t + 1e-6) - 1
        red = self.red(t)
        while i >= 0:
            x = self.tps[i]
            if x["red"]:
                return 1.0
            if x["t"] >= red["t"] - 1e-6:
                return max(0.1, min(10.0, -100.0 / x["bl"])) if x["bl"] < 0 else 1.0
            return 1.0
        return 1.0

    def point(self, t):
        """Действующая линия (любая) — для набора звуков и громкости."""
        import bisect
        i = bisect.bisect_right(self.all_t, t + 5) - 1          # osu!: звук берётся с допуском 5 мс
        return self.tps[max(0, i)] if self.tps else self.reds[0]


def _hit_sample(s: str) -> dict:
    p = (s or "").split(":")
    out = {"ns": int(_num(p[0], 0)) if len(p) > 0 and p[0] else 0,
           "as": int(_num(p[1], 0)) if len(p) > 1 and p[1] else 0,
           "idx": int(_num(p[2], 0)) if len(p) > 2 and p[2] else 0,
           "vol": int(_num(p[3], 0)) if len(p) > 3 and p[3] else 0,
           "file": p[4].strip() if len(p) > 4 else ""}
    return out


def _resolve(hs_sample: dict, tp: dict, default_set: int) -> tuple[int, int, int, int]:
    """(набор нормального звука, набор добавок, индекс сэмпла, громкость) по правилам osu!."""
    ns = hs_sample.get("ns", 0) or tp.get("ss", 0) or default_set or 1
    add = hs_sample.get("as", 0) or ns
    idx = hs_sample.get("idx", 0) or tp.get("si", 0) or 1
    vol = hs_sample.get("vol", 0) or tp.get("vol", 100)
    return ns, add, idx, vol


def _colour(s: str):
    try:
        r, g, b = [int(float(x)) for x in s.split(",")[:3]]
        return [max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b))]
    except (ValueError, TypeError):
        return None


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        s = s[1:-1]
    return s.replace("\\", "/")


def _find_file(folder: Path, name: str) -> str:
    """Файл в папке карты без учёта регистра (в .osz имена часто в другом регистре)."""
    if not name:
        return ""
    f = folder / name
    if f.exists():
        return str(f)
    low = name.replace("\\", "/").lower()
    try:
        for p in folder.rglob("*"):
            if p.is_file() and str(p.relative_to(folder)).replace("\\", "/").lower() == low:
                return str(p)
    except OSError:
        pass
    return ""


def path_for(o: dict, step: float = 2.0) -> np.ndarray:
    """Путь слайдера (B / P / L / C — всё умеет osu_mapgen.slider_path)."""
    return G.slider_path(o["ct"], o["pts"], o["len"], step)


# ------------------------------------------------------------------ #
#  Преобразование в карту esu!                                        #
# ------------------------------------------------------------------ #

class Unsupported(ValueError):
    pass


def convert(P: dict, folder: Path, src_file: str = "") -> dict:
    gen, meta, dif = P["general"], P["metadata"], P["difficulty"]
    mode = int(_num(gen.get("Mode", 0)))
    if mode != 0:
        raise Unsupported({1: "osu!taiko", 2: "osu!catch", 3: "osu!mania"}.get(mode, f"режим {mode}"))
    ver = P["version"]
    od = _num(dif.get("OverallDifficulty", 5), 5)
    cs = _num(dif.get("CircleSize", 5), 5)
    hp = _num(dif.get("HPDrainRate", 5), 5)
    ar = _num(dif.get("ApproachRate", od), od)
    smul = _num(dif.get("SliderMultiplier", 1.4), 1.4) or 1.4
    tick_rate = _num(dif.get("SliderTickRate", 1), 1) or 1
    default_set = SET_NAMES.get(str(gen.get("SampleSet", "Normal")).strip().lower(), 1) or 1
    tps = _timing(P["timing"], ver)
    TI = _TimingIndex(tps)
    objs = []
    force_nc = False
    for ln in P["objects"]:
        p = ln.split(",")
        if len(p) < 4:
            continue
        x, y, t = _num(p[0]), _num(p[1]), _num(p[2])
        typ = int(_num(p[3]))
        hs = int(_num(p[4])) if len(p) > 4 else 0
        nc = bool(typ & 4) or force_nc or not objs
        skip = (typ >> 4) & 7
        force_nc = False
        red = TI.red(t)
        if typ & 1:                                   # круг
            smp = _hit_sample(p[5]) if len(p) > 5 else _hit_sample("")
            ns, add, idx, vol = _resolve(smp, TI.point(t), default_set)
            objs.append({"k": 0, "t": round(t), "x": int(x), "y": int(y), "hs": hs, "ss": ns, "as": add, "si": idx,
                         "vol": vol, "file": smp["file"], "nc": nc, "skip": skip})
        elif typ & 2:                                 # слайдер
            if len(p) < 8:
                continue
            curve = p[5].split("|")
            ct = curve[0].strip().upper() or "B"
            pts = [[x, y]]
            for c in curve[1:]:
                if ":" in c:
                    a, b = c.split(":", 1)
                    pts.append([_num(a), _num(b)])
            if len(pts) < 2:
                pts.append([x + 1, y])
            if ct == "P" and len(pts) != 3:
                ct = "B"
            if ct not in ("B", "P", "L", "C"):
                ct = "B"
            slides = max(1, int(_num(p[6], 1)))
            length = _num(p[7], 0)
            sv = TI.sv(t)
            bl = red["bl"]
            if length <= 0:                           # длина не задана (очень старые карты) — по самой кривой
                try:
                    length = max(10.0, _natural(ct, pts))
                except Exception:                     # noqa: BLE001
                    length = 100.0
            px_per_beat = smul * 100.0 * sv
            span = length / px_per_beat * bl
            edge_hs = [int(_num(v)) for v in p[8].split("|")] if len(p) > 8 and p[8] else []
            edge_ss = p[9].split("|") if len(p) > 9 and p[9] else []
            smp = _hit_sample(p[10]) if len(p) > 10 else _hit_sample("")
            ns, add, idx, vol = _resolve(smp, TI.point(t), default_set)
            edges = []
            for e in range(slides + 1):
                et = t + span * e
                tp = TI.point(et)
                eh = edge_hs[e] if e < len(edge_hs) else hs
                es = edge_ss[e].split(":") if e < len(edge_ss) else ["0", "0"]
                esmp = {"ns": int(_num(es[0], 0)) if es and es[0] else 0,
                        "as": int(_num(es[1], 0)) if len(es) > 1 and es[1] else 0,
                        "idx": smp["idx"], "vol": smp["vol"]}
                e_ns, e_add, e_idx, e_vol = _resolve(esmp, tp, default_set)
                edges.append([eh, e_ns, e_add, e_idx, e_vol])
            tick_beat = bl * (1.0 / sv if ver < 8 else 1.0)
            o = {"k": 1, "t": round(t), "x": int(x), "y": int(y), "hs": hs, "ss": ns, "as": add, "si": idx,
                 "vol": vol, "nc": nc, "skip": skip, "ct": ct, "pts": [[round(a, 2), round(b, 2)] for a, b in pts],
                 "slides": slides, "len": round(length, 3), "span": round(span, 4), "beat": round(tick_beat, 4),
                 "sv": round(px_per_beat, 4), "edges": edges, "tick_vol": TI.point(t)["vol"]}
            objs.append(o)
        elif typ & 8:                                 # спиннер
            end = _num(p[5], t + 1000) if len(p) > 5 else t + 1000
            smp = _hit_sample(p[6]) if len(p) > 6 else _hit_sample("")
            ns, add, idx, vol = _resolve(smp, TI.point(end), default_set)
            objs.append({"k": 2, "t": round(t), "end": round(max(t + 50, end)), "x": 256, "y": 192, "hs": hs,
                         "ss": ns, "as": add, "si": idx, "vol": vol, "nc": True, "skip": 0})
            force_nc = True                           # после спиннера — всегда новое комбо
        # 128 — удержание osu!mania: в osu!standard не бывает
    if not objs:
        raise Unsupported("в карте нет объектов")
    objs.sort(key=lambda o: o["t"])
    # события: фон, видео, перерывы
    bg, video, video_off, breaks = "", "", 0.0, []
    for ev in P["events"]:
        s = ev.strip()
        if not s or s.startswith("//") or ev.startswith((" ", "_")):
            continue
        q = [x.strip() for x in s.split(",")]
        kind = q[0]
        if kind in ("0", "Background") and len(q) >= 3 and not bg:
            bg = _find_file(folder, _unquote(q[2]))
        elif kind in ("1", "Video") and len(q) >= 3 and not video:
            f = _find_file(folder, _unquote(q[2]))
            if f and Path(f).suffix.lower() in VIDEO_EXT:
                video, video_off = f, _num(q[1])
        elif kind in ("2", "Break") and len(q) >= 3:
            a, b = _num(q[1]), _num(q[2])
            if b - a > 650:
                breaks.append([a, b])
    # kiai, доли
    kiai, cur = [], None
    for x in tps:
        if x["kiai"] and cur is None:
            cur = x["t"]
        elif not x["kiai"] and cur is not None:
            if x["t"] > cur:
                kiai.append([cur, x["t"]])
            cur = None
    last_end = max(o.get("end", o["t"]) if o["k"] != 1 else o["t"] + o["span"] * o["slides"] for o in objs)
    if cur is not None:
        kiai.append([cur, last_end + 1000])
    beats = _beats(TI.reds, last_end + 4000)
    # основной BPM — по самой долгой красной линии (как в osu!)
    reds = TI.reds
    durs = {}
    for i, r in enumerate(reds):
        a = max(r["t"], objs[0]["t"]) if i == 0 else r["t"]
        b = reds[i + 1]["t"] if i + 1 < len(reds) else last_end
        bpm = round(60000.0 / r["bl"], 2)
        durs[bpm] = durs.get(bpm, 0) + max(0.0, b - a)
    bpm_main = max(durs, key=durs.get) if durs else 120.0
    bpms = sorted(durs) if durs else [bpm_main]
    colours = []
    for k in sorted((k for k in P["colours"] if re.match(r"(?i)combo\d+", k)), key=lambda k: int(re.sub(r"\D", "", k) or 0)):
        c = _colour(P["colours"][k])
        if c:
            colours.append(c)
    audio = _find_file(folder, gen.get("AudioFilename", "").strip())
    m = {"cs": cs, "ar": ar, "od": od, "hp": hp, "sv": smul * 100.0, "tick_rate": tick_rate,
         "objects": objs, "bpm": bpm_main, "bpm_min": bpms[0], "bpm_max": bpms[-1],
         "beats": beats, "phase": 0, "kiai": kiai, "breaks": breaks,
         "length": last_end, "preview": max(0.0, _num(gen.get("PreviewTime", -1), -1)) if _num(
             gen.get("PreviewTime", -1), -1) >= 0 else last_end * 0.4,
         "name": meta.get("Version", "") or "Normal", "title": meta.get("Title", "") or meta.get("TitleUnicode", ""),
         "title_u": meta.get("TitleUnicode", ""), "artist": meta.get("Artist", "") or meta.get("ArtistUnicode", ""),
         "artist_u": meta.get("ArtistUnicode", ""), "creator": meta.get("Creator", ""),
         "source": meta.get("Source", ""), "tags": meta.get("Tags", ""),
         "beatmap_id": int(_num(meta.get("BeatmapID", 0))), "set_id": int(_num(meta.get("BeatmapSetID", -1), -1)),
         "stack_leniency": _num(gen.get("StackLeniency", 0.7), 0.7), "file_version": ver,
         "countdown": int(_num(gen.get("Countdown", 1), 1)), "lead_in": _num(gen.get("AudioLeadIn", 0)),
         "colours": colours, "bg": bg, "video": video, "video_offset": video_off, "audio": audio,
         "folder": str(folder), "osu_file": src_file, "osu": True, "default_set": default_set,
         "timing": [[x["t"], x["bl"], x["meter"], x["ss"], x["si"], x["vol"], int(x["red"]), int(x["kiai"])]
                    for x in tps],
         "letterbox": bool(int(_num(gen.get("LetterboxInBreaks", 0)))),
         "epilepsy": bool(int(_num(gen.get("EpilepsyWarning", 0))))}
    return m


def _natural(ct, pts) -> float:
    """Длина кривой по опорным точкам (без обрезки по длине слайдера)."""
    P = np.asarray(pts, float)
    if len(P) < 2:
        return 100.0
    if ct == "L":
        return float(np.linalg.norm(np.diff(P, axis=0), axis=1).sum())
    if ct == "P" and len(P) == 3:
        a, b, c = P
        d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
        if abs(d) > 1e-3:
            ux = ((a @ a) * (b[1] - c[1]) + (b @ b) * (c[1] - a[1]) + (c @ c) * (a[1] - b[1])) / d
            uy = ((a @ a) * (c[0] - b[0]) + (b @ b) * (a[0] - c[0]) + (c @ c) * (b[0] - a[0])) / d
            r = math.hypot(a[0] - ux, a[1] - uy)
            t0, tc = math.atan2(a[1] - uy, a[0] - ux), math.atan2(c[1] - uy, c[0] - ux)
            cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
            return r * (((tc - t0) * (1.0 if cross > 0 else -1.0)) % (2 * math.pi))
    poly = G._catmull(P) if ct == "C" else G._bezier(P, 120)
    return float(np.linalg.norm(np.diff(poly, axis=0), axis=1).sum())


def _beats(reds, until) -> list:
    out = []
    first = reds[0]
    bl = first["bl"]
    t = first["t"]
    while t > 0:                                      # доли до первой красной линии
        t -= bl
    for i, r in enumerate(reds):
        end = reds[i + 1]["t"] if i + 1 < len(reds) else until
        t = r["t"] if i else t
        bl = max(10.0, r["bl"])
        while t < end - 1 and len(out) < 40000:
            out.append(round(t, 2))
            t += bl
    return out


# ------------------------------------------------------------------ #
#  Стопки (stacking) — как в osu!                                     #
# ------------------------------------------------------------------ #

STACK_DISTANCE = 3.0


def stack_scale(cs: float) -> float:
    return (1.0 - 0.7 * (cs - 5) / 5) / 2


def apply_stacking(objs: list, preempt: float, leniency: float, version: int = 14):
    """objs — объекты Play (с _path у слайдеров). Ставит o["stack"] (высота стопки)."""
    for o in objs:
        o["stack"] = 0
    if not objs or leniency <= 0:
        return

    def pos(o):
        return (float(o["x"]), float(o["y"]))

    def end_pos(o):
        if o["k"] == 1 and "_path" in o:
            pth = o["_path"]
            q = pth[-1] if o["slides"] % 2 == 1 else pth[0]
            return (float(q[0]), float(q[1]))
        return pos(o)

    def end_time(o):
        if o["k"] == 1:
            return o["t"] + o["span"] * o["slides"]
        if o["k"] == 2:
            return o["end"]
        return o["t"]

    def dist(a, b):
        return math.hypot(a[0] - b[0], a[1] - b[1])
    thr = preempt * leniency
    n = len(objs)
    if version < 6:
        for i in range(n):
            cur = objs[i]
            if cur["stack"] != 0 and cur["k"] != 1:
                continue
            start = end_time(cur)
            slider_stack = 0
            p2 = end_pos(cur) if cur["k"] == 1 else pos(cur)
            for j in range(i + 1, n):
                if objs[j]["t"] - thr > start:
                    break
                if dist(pos(objs[j]), pos(cur)) < STACK_DISTANCE:
                    cur["stack"] += 1
                    start = objs[j]["t"]
                elif dist(pos(objs[j]), p2) < STACK_DISTANCE:
                    slider_stack += 1
                    objs[j]["stack"] -= slider_stack
                    start = objs[j]["t"]
        return
    start_i, end_i = 0, n - 1
    ext_end = end_i
    ext_start = start_i
    for i in range(end_i, start_i, -1):
        k = i
        oi = objs[i]
        if oi["stack"] != 0 or oi["k"] == 2:
            continue
        if oi["k"] == 0:
            while k - 1 >= 0:
                k -= 1
                on = objs[k]
                if on["k"] == 2:
                    continue
                if oi["t"] - end_time(on) > thr:
                    break
                if k < ext_start:
                    on["stack"] = 0
                    ext_start = k
                if on["k"] == 1 and dist(end_pos(on), pos(oi)) < STACK_DISTANCE:
                    off = oi["stack"] - on["stack"] + 1
                    for j in range(k + 1, i + 1):
                        oj = objs[j]
                        if dist(end_pos(on), pos(oj)) < STACK_DISTANCE:
                            oj["stack"] -= off
                    break
                if dist(pos(on), pos(oi)) < STACK_DISTANCE:
                    on["stack"] = oi["stack"] + 1
                    oi = on
        elif oi["k"] == 1:
            while k - 1 >= start_i:
                k -= 1
                on = objs[k]
                if on["k"] == 2:
                    continue
                if oi["t"] - on["t"] > thr:
                    break
                if dist(end_pos(on), pos(oi)) < STACK_DISTANCE:
                    on["stack"] = oi["stack"] + 1
                    oi = on
    _ = ext_end


def offset_stacks(objs: list, cs: float):
    """Сдвинуть объекты по высоте стопки (вверх-влево на 6.4·масштаб за ступень, как в osu!)."""
    sc = stack_scale(cs)
    for o in objs:
        h = o.get("stack", 0)
        if not h:
            continue
        d = h * sc * -6.4
        o["x"] = o["x"] + d
        o["y"] = o["y"] + d
        if o["k"] == 1 and "_path" in o:
            o["_path"] = o["_path"] + d


# ------------------------------------------------------------------ #
#  Звёзды и сведения                                                  #
# ------------------------------------------------------------------ #

def finish_map(m: dict) -> dict:
    """Пути слайдеров → звёзды, счётчики, макс. комбо (как у сгенерированных карт)."""
    objs = m["objects"]
    for o in objs:
        if o["k"] == 1:
            try:
                path_for(o, 4.0)
            except Exception:                          # noqa: BLE001  кривая не строится — прямая
                o["ct"], o["pts"] = "L", [[o["x"], o["y"]], [o["x"] + 1, o["y"]]]
    prepared = {"objects": [{k: v for k, v in o.items() if k not in ("_path", "ticks", "repeats", "end")}
                            for o in objs], "tick_rate": m.get("tick_rate", 1)}
    G.prepare(prepared)
    try:
        sr = G.star_rating({"objects": prepared["objects"], "cs": m["cs"]})
    except Exception:                                  # noqa: BLE001
        sr = {"stars": 0.0, "aim": 0.0, "speed": 0.0}
    m.update({"stars": sr["stars"], "aim": sr["aim"], "speed": sr["speed"],
              "n_circles": sum(1 for o in objs if o["k"] == 0), "n_sliders": sum(1 for o in objs if o["k"] == 1),
              "n_spinners": sum(1 for o in objs if o["k"] == 2)})
    c = 0
    for o in prepared["objects"]:
        c += (1 + len(o["ticks"]) + len(o["repeats"]) + 1) if o["k"] == 1 else 1
    m["max_combo"] = c
    first = objs[0]["t"]
    drain = max(1.0, m["length"] - first - sum(b - a for a, b in m.get("breaks", [])))
    m["drain"] = drain
    return m


# ------------------------------------------------------------------ #
#  Импорт (в дочернем процессе)                                       #
# ------------------------------------------------------------------ #

def _md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def conv_file(md5: str) -> Path:
    return CONV / f"{md5}.json"


def _save_json(path: Path, d):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), "utf-8")
    os.replace(tmp, path)


def import_set_dir(folder: str, force: bool = False) -> dict | None:
    """Все .osu одной папки → карты esu! на диске и запись набора для индекса."""
    folder = Path(folder)
    files = sorted(folder.glob("*.osu"))
    diffs = []
    head = None
    skipped = []
    for f in files:
        try:
            raw = f.read_bytes()
        except OSError:
            continue
        md5 = _md5(raw)
        cf = conv_file(md5)
        m = None
        if cf.exists() and not force:
            try:
                m = json.loads(cf.read_text("utf-8"))
                if m.get("conv") != CONV_VERSION:
                    m = None
            except Exception:                          # noqa: BLE001
                m = None
        if m is None:
            try:
                P = parse_osu(raw.decode("utf-8", "replace"))
                m = convert(P, folder, f.name)
            except Unsupported as e:
                skipped.append(f"{f.name}: {e}")
                continue
            except Exception as e:                     # noqa: BLE001
                skipped.append(f"{f.name}: {e}")
                continue
            m = finish_map(m)
            m["conv"] = CONV_VERSION
            m["osu_md5"] = md5
            m["diff"] = md5
            _save_json(cf, m)
        diffs.append({"md5": md5, "file": f.name, "version": m["name"], "stars": m["stars"], "cs": m["cs"],
                      "ar": m["ar"], "od": m["od"], "hp": m["hp"], "length": m["length"], "bpm": m["bpm"],
                      "n": len(m["objects"]), "max_combo": m["max_combo"], "creator": m.get("creator", ""),
                      "audio": m.get("audio", ""), "bg": m.get("bg", "")})
        if head is None or (m.get("bg") and not head.get("bg")):
            head = m
    if not diffs or head is None:
        return {"dir": str(folder), "skipped": skipped, "diffs": []} if skipped else None
    diffs.sort(key=lambda d: (d["stars"], d["version"]))
    audio = head.get("audio") or next((d["audio"] for d in diffs if d.get("audio")), "")
    try:
        mtime = folder.stat().st_mtime
    except OSError:
        mtime = 0
    return {"id": hashlib.sha1(str(folder).lower().encode("utf-8", "ignore")).hexdigest()[:16], "dir": str(folder),
            "audio": audio, "title": head.get("title", ""), "title_u": head.get("title_u", ""),
            "artist": head.get("artist", ""), "artist_u": head.get("artist_u", ""),
            "creator": head.get("creator", ""), "source": head.get("source", ""), "tags": head.get("tags", ""),
            "bg": head.get("bg", ""), "video": head.get("video", ""), "preview": head.get("preview", 0),
            "length": max(d["length"] for d in diffs), "bpm": head.get("bpm", 120), "set_id": head.get("set_id", -1),
            "diffs": diffs, "mtime": mtime, "skipped": skipped, "added": time.time()}


def import_dirs_job(dirs: list, force: bool = False, progress=None) -> list:
    """Для bgproc: несколько папок наборов → записи индекса."""
    out = []
    n = max(1, len(dirs))
    for i, d in enumerate(dirs):
        if progress:
            progress(i / n, f"Разбираю карты: {Path(d).name[:60]}")
        try:
            e = import_set_dir(d, force)
        except Exception as ex:                        # noqa: BLE001
            e = {"dir": str(d), "skipped": [str(ex)], "diffs": []}
        if e:
            out.append(e)
    if progress:
        progress(1.0, "Готово")
    return out


def _safe_name(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', " ", s or "").strip()[:90] or "beatmap"


def extract_osz(path: str) -> str:
    """.osz → папка в ~/.neon_player/osu/songs (только нужные файлы, без выхода за папку)."""
    p = Path(path)
    SONGS.mkdir(parents=True, exist_ok=True)
    dest = SONGS / _safe_name(p.stem)
    k = 2
    # Same-named archives from different folders must not merge their songs.
    while dest.exists():
        try:
            previous = (dest / ".src").read_text("utf-8")
            if Path(previous).resolve() == p.resolve():
                break
        except (OSError, ValueError):
            pass
        dest = SONGS / f"{_safe_name(p.stem)} ({k})"
        k += 1
    dest.mkdir(parents=True, exist_ok=True)
    ok_ext = (".osu", ".osb") + AUDIO_EXT + IMG_EXT + VIDEO_EXT
    with zipfile.ZipFile(p) as z:
        for info in z.infolist():
            name = info.filename.replace("\\", "/")
            if info.is_dir() or name.startswith("/") or ".." in name.split("/"):
                continue
            if not name.lower().endswith(ok_ext):
                continue
            if info.file_size > 300 * 1024 * 1024:
                continue
            out = dest / name
            # Windows drive paths (C:/...), UNC paths and existing symlinks can
            # escape even when the archive name has no '..' component.
            try:
                if not out.resolve().is_relative_to(dest.resolve()):
                    continue
            except (OSError, ValueError):
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(out, "wb") as dst:
                while True:
                    buf = src.read(1 << 20)
                    if not buf:
                        break
                    dst.write(buf)
    (dest / ".src").write_text(str(p), "utf-8")
    return str(dest)


def import_files_job(paths: list, progress=None) -> list:
    """Для bgproc: файлы .osz / .osu (или папки) → записи индекса."""
    dirs = []
    for i, f in enumerate(paths):
        f = Path(f)
        if progress:
            progress(i / max(1, len(paths)) * 0.3, f"Распаковываю {f.name[:60]}")
        try:
            if f.is_dir():
                dirs.append(str(f))
            elif f.suffix.lower() == ".osz":
                dirs.append(extract_osz(str(f)))
            elif f.suffix.lower() == ".osu":
                dirs.append(str(f.parent))
        except Exception as e:                         # noqa: BLE001
            print("[osu import]", f, e)
    dirs = list(dict.fromkeys(dirs))
    return import_dirs_job(dirs, False, (lambda fr, s: progress(0.3 + 0.7 * fr, s)) if progress else None)


def scan_songs_job(songs_dir: str, known: dict, progress=None) -> dict:
    """Для bgproc: папка Songs osu! → новые/изменённые наборы. known: {папка: mtime}."""
    root = Path(songs_dir)
    todo, seen = [], []
    try:
        subs = [d for d in root.iterdir() if d.is_dir()]
    except OSError:
        subs = []
    for d in subs:
        try:
            if not any(d.glob("*.osu")):
                continue
            mt = d.stat().st_mtime
        except OSError:
            continue
        seen.append(str(d))
        if known.get(str(d)) != mt:
            todo.append(str(d))
    entries = import_dirs_job(todo, False, progress)
    return {"entries": entries, "seen": seen}


# ------------------------------------------------------------------ #
#  Индекс (в процессе интерфейса)                                     #
# ------------------------------------------------------------------ #

_idx_cache = {"sig": None, "sets": [], "by_id": {}, "checked": 0.0, "tracks": None}


def load_index() -> list:
    """Индекс наборов. Файл проверяется не чаще раза в 2 с (зовётся из отрисовки выбора песни)."""
    now = time.monotonic()
    if _idx_cache["sig"] is not None and now - _idx_cache["checked"] < 2.0:
        return _idx_cache["sets"]
    _idx_cache["checked"] = now
    try:
        st = INDEX.stat()
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        if _idx_cache["sig"] is not None:
            _idx_cache.update(sig=None, sets=[], by_id={}, tracks=None)
        return []
    if sig != _idx_cache["sig"]:
        try:
            d = json.loads(INDEX.read_text("utf-8"))
            sets = [s for s in d.get("sets", []) if s.get("diffs")]
        except Exception:                              # noqa: BLE001
            sets = []
        _idx_cache.update(sets=sets, by_id={s.get("id"): s for s in sets}, sig=sig, tracks=None)
    return _idx_cache["sets"]


def merge_index(entries: list, seen_root: str | None = None, seen: list | None = None) -> int:
    """Добавить/обновить наборы. seen_root+seen — убрать наборы из этой папки, которых больше нет."""
    with _lock:
        try:
            d = json.loads(INDEX.read_text("utf-8"))
        except Exception:                              # noqa: BLE001
            d = {"sets": []}
        by_dir = {s["dir"]: s for s in d.get("sets", [])}
        added = 0
        for e in entries:
            if not e.get("diffs"):
                continue
            if e["dir"] not in by_dir:
                added += 1
            old = by_dir.get(e["dir"])
            if old and old.get("added"):
                e["added"] = old["added"]
            by_dir[e["dir"]] = e
        if seen_root is not None and seen is not None:
            keep = set(seen)
            root = str(Path(seen_root)).lower()
            for k in list(by_dir):
                if k.lower().startswith(root) and k not in keep:
                    del by_dir[k]
        d["sets"] = sorted(by_dir.values(), key=lambda s: (s.get("artist", "").lower(), s.get("title", "").lower()))
        d["v"] = CONV_VERSION
        _save_json(INDEX, d)
        _idx_cache.update(sig=None, checked=0.0)
        return added


def remove_set(set_dir: str):
    with _lock:
        try:
            d = json.loads(INDEX.read_text("utf-8"))
        except Exception:                              # noqa: BLE001
            return
        d["sets"] = [s for s in d.get("sets", []) if s.get("dir") != set_dir]
        _save_json(INDEX, d)
        _idx_cache.update(sig=None, checked=0.0)


def known_dirs() -> dict:
    return {s["dir"]: s.get("mtime", 0) for s in load_index()}


def set_track(s: dict) -> dict:
    """Набор карт → «трек» для выбора песни (путь — звук карты)."""
    return {"path": s.get("audio", ""), "title": s.get("title", "") or Path(s.get("dir", "")).name,
            "artist": s.get("artist", ""), "album": s.get("source", ""), "cover": s.get("bg", ""),
            "duration": float(s.get("length", 0)) / 1000.0, "esu_set": s.get("id"), "esu_dir": s.get("dir"),
            "creator": s.get("creator", ""), "tags": s.get("tags", "")}


def library_tracks() -> list:
    """Наборы как «треки» выбора песни (список строится один раз на версию индекса)."""
    load_index()
    if _idx_cache.get("tracks") is None:
        _idx_cache["tracks"] = [set_track(s) for s in _idx_cache["sets"]
                                if s.get("audio") and os.path.exists(s["audio"])]
    return list(_idx_cache["tracks"])


def set_by_id(sid: str) -> dict | None:
    load_index()
    return _idx_cache["by_id"].get(sid)


def load_set_maps(s: dict) -> dict:
    """Карты набора из кэша на диске: {md5: карта}."""
    out = {}
    for dd in s.get("diffs", []):
        try:
            m = json.loads(conv_file(dd["md5"]).read_text("utf-8"))
        except Exception:                              # noqa: BLE001
            continue
        out[dd["md5"]] = m
    return out


def find_osu_songs() -> str | None:
    """Папка Songs установленного osu! (stable)."""
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "osu!"
    cands = []
    try:
        for cfg in base.glob("osu!.*.cfg"):
            for ln in cfg.read_text("utf-8", "replace").splitlines():
                if ln.strip().lower().startswith("beatmapdirectory"):
                    v = ln.split("=", 1)[1].strip()
                    if v:
                        p = Path(v)
                        cands.append(p if p.is_absolute() else base / p)
    except OSError:
        pass
    cands.append(base / "Songs")
    for c in cands:
        try:
            if c.is_dir():
                return str(c)
        except OSError:
            continue
    return None


# ── смещение карт (как «локальное смещение» в osu!) ── #

_off_cache = {"sig": None, "d": {}}


def _offsets() -> dict:
    try:
        st = OFFSETS.stat()
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        return {}
    if sig != _off_cache["sig"]:
        try:
            _off_cache["d"] = json.loads(OFFSETS.read_text("utf-8"))
        except Exception:                              # noqa: BLE001
            _off_cache["d"] = {}
        _off_cache["sig"] = sig
    return _off_cache["d"]


def get_offset(md5: str) -> float:
    try:
        return float(_offsets().get(md5, 0.0))
    except (TypeError, ValueError):
        return 0.0


def set_offset(md5: str, ms: float):
    with _lock:
        d = dict(_offsets())
        d[md5] = float(ms)
        _save_json(OFFSETS, d)
        _off_cache["sig"] = None


def auto_offset_job(audio: str, notes: list) -> float | None:
    """Для bgproc: насколько начала ударов в звуке (как его декодирует ECHOES) позже нот карты, мс.
    Для каждой ноты — начало ближайшей сильной атаки в окне −40…+60 мс; медиана по сильным.
    None — если ноты с ударами не сходятся (карта по вокалу, «плывущий» темп): тогда без подгонки."""
    x = G.decode(audio)
    A = G._td_env(x)
    t = np.asarray(sorted(notes), float)
    idx = np.round(t / 1000.0 * G.FFR).astype(np.int64)
    lo, hi = int(-0.040 * G.FFR), int(0.060 * G.FFR)
    ok = (idx + lo > 2) & (idx + hi < len(A) - 2)
    idx = idx[ok]
    if len(idx) < 40:
        return None
    v, st = G._attack_start(A, idx, lo, hi)
    lag = (st - idx) / G.FFR * 1000.0
    strong = v >= np.percentile(v, 55)
    lag = lag[strong]
    if len(lag) < 20:
        return None
    med = float(np.median(lag))
    spread = float(np.median(np.abs(lag - med)))
    if spread > 22.0 or abs(med) > 45.0:
        return None
    return round(med, 1)


# ------------------------------------------------------------------ #
#  Свои сэмплы карты (хитсаунды из папки набора)                      #
# ------------------------------------------------------------------ #

_SAMPLE_RE = re.compile(r"^(normal|soft|drum)-(hitnormal|hitwhistle|hitfinish|hitclap|slidertick|sliderslide|"
                        r"sliderwhistle)(\d*)\.(wav|ogg|mp3)$", re.I)


def sample_files(folder: str) -> dict:
    """{ключ «soft-hitclap2»: путь} — сэмплы в папке карты (индекс 1 = без номера)."""
    out = {}
    try:
        for f in Path(folder).iterdir():
            m = _SAMPLE_RE.match(f.name)
            if m:
                idx = m.group(3) or "1"
                if idx == "0":
                    idx = "1"
                out[f"{m.group(1).lower()}-{m.group(2).lower()}{idx}"] = str(f)
    except OSError:
        pass
    return out


def decode_sample(path: str, sr: int = 48000) -> np.ndarray | None:
    """Короткий звук → моно float32 sr Гц. WAV читается сам, остальное — через ffmpeg."""
    p = Path(path)
    try:
        if p.suffix.lower() == ".wav":
            import wave
            with wave.open(str(p), "rb") as w:
                ch, sw, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
                raw = w.readframes(min(n, rate * 6))
            if sw == 2:
                a = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
            elif sw == 1:
                a = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128) / 128.0
            elif sw == 3:
                b = np.frombuffer(raw, np.uint8).reshape(-1, 3)
                a = ((b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16))
                     << 8 >> 8).astype(np.float32) / 8388608.0
            elif sw == 4:
                a = np.frombuffer(raw, np.int32).astype(np.float32) / 2147483648.0
            else:
                return None
            if ch > 1:
                a = a[: len(a) // ch * ch].reshape(-1, ch).mean(1)
            if rate != sr and len(a) > 1:
                x = np.arange(0, len(a), rate / sr)
                a = np.interp(x, np.arange(len(a)), a).astype(np.float32)
            return a.astype(np.float32)
    except Exception:                                  # noqa: BLE001
        pass
    try:
        import subprocess
        ff = G._ffmpeg()
        if not ff:
            return None
        r = subprocess.run([ff, "-v", "error", "-nostdin", "-i", str(p), "-t", "6", "-ac", "1", "-ar", str(sr),
                            "-f", "f32le", "-"], capture_output=True, timeout=20,
                           creationflags=0x08000000 if os.name == "nt" else 0)
        a = np.frombuffer(r.stdout, np.float32)
        return a.copy() if len(a) else None
    except Exception:                                  # noqa: BLE001
        return None
