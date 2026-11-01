# theme_kit.py
"""
Конструктор тем ECHOES — модель темы, хранилище и инструменты палитры.

Тема — папка ~/.neon_player/themes/<id>/ c theme.json и подпапкой assets/
(картинки, GIF, видео, шрифты, спрайты частиц). Поэтому тему можно целиком
выгрузить одним файлом .echoestheme (zip) и отдать другу — откроется у него
со всеми картинками.

Здесь нет виджетов: только данные и чистые функции (палитра из картинки,
гармонии цветов, автоподбор служебных цветов, генератор «Удиви меня»,
шаблоны). Отрисовка — theme_layers.py, редактор — theme_studio.py.
"""
from __future__ import annotations

import colorsys
import copy
import json
import math
import os
import random
import re
import shutil
import time
import uuid
import zipfile
from pathlib import Path

FORMAT = "echoes-theme"
VERSION = 1
PACKAGE_EXT = ".echoestheme"

# Перевод имён и описаний, которые попадают в саму тему (шаблоны, «Удиви меня»).
# Конструктор подставляет сюда i18n.tr, чтобы в английском интерфейсе темы назывались по-английски.
TR = lambda s: s                                        # noqa: E731

# ------------------------------------------------------------------ #
#  Схема и значения по умолчанию                                      #
# ------------------------------------------------------------------ #

BACKGROUND_TYPES = [
    ("glow", "Свечение (как в Vinyl glass)"),
    ("color", "Сплошной цвет"),
    ("gradient", "Градиент"),
    ("media", "Картинка или GIF"),
    ("video", "Видео-обои"),
    ("cover", "Обложка играющего трека"),
    ("aurora", "Живые обои: северное сияние"),
    ("synthwave", "Живые обои: ретровейв"),
    ("plasma", "Живые обои: лава-плазма"),
    ("starfield", "Живые обои: варп-звёзды"),
    ("matrix", "Живые обои: цифровой дождь"),
    ("nebula", "Живые обои: туманность «Моей волны»"),
]
LIVE_BACKGROUNDS = {"aurora", "synthwave", "plasma", "starfield", "matrix", "nebula"}
SCRIPT_MOVED: set = set()      # id слоёв, которые двигает/прячет скрипт другого слоя (layer("…") в EchoScript)
ANIMATED_BACKGROUNDS = LIVE_BACKGROUNDS | {"video", "media", "gradient"}

FIT_MODES = [("cover", "Заполнить"), ("contain", "Вписать"), ("stretch", "Растянуть"),
             ("tile", "Плиткой"), ("center", "По центру")]
GRADIENT_KINDS = [("linear", "Линейный"), ("radial", "Радиальный"), ("conical", "Конический")]

LAYER_KINDS = [("media", "Картинка / GIF"), ("video", "Видео"), ("text", "Текст"), ("shape", "Фигура"),
               ("script", "Виджет / скрипт"), ("effect", "Эффект темы")]
SHAPES = [("circle", "Круг"), ("rect", "Прямоугольник"), ("rounded", "Скруглённый"),
          ("ring", "Кольцо"), ("star", "Звезда"), ("heart", "Сердце"), ("triangle", "Треугольник"),
          ("blob", "Клякса")]
ANIMATIONS = [("none", "Без анимации"), ("float", "Парение"), ("sway", "Покачивание"),
              ("spin", "Вращение"), ("pulse", "Пульсация"), ("bounce", "Подпрыгивание"),
              ("orbit", "Орбита"), ("wobble", "Желе"), ("beat", "Удар в бит"),
              ("level", "Громкость → размер"), ("bass", "Бас → размер"), ("shake", "Тряска на бит"),
              ("glitch", "Глитч"), ("fade", "Мерцание"), ("breathe", "Дыхание"), ("drift", "Дрейф по кругу"),
              ("flip3d", "3D-переворот"), ("swing3d", "3D-качание"), ("tilt_beat", "3D-наклон на бит")]
AUDIO_ANIMS = {"beat", "level", "bass", "shake", "glitch", "tilt_beat"}
# Эффекты из встроенных тем — живут как обычные слои (двигаются, тянутся, складываются в стопку)
EFFECTS = [("milkdrop", "MilkDrop (из Winamp)"), ("fluid", "Жидкость (из Fluid)"),
           ("aurora", "Северное сияние"), ("synthwave", "Ретровейв"), ("plasma", "Лава-плазма"),
           ("starfield", "Варп-звёзды"), ("matrix", "Цифровой дождь"), ("nebula", "Туманность «Моей волны»")]
UI_MODES = [("standard", "Обычный интерфейс плеера + слои"),
            ("layers", "Только слои — свой интерфейс с нуля")]
MAX_SCRIPT = 64000
BLEND_MODES = [("normal", "Обычный"), ("screen", "Осветление"), ("add", "Сложение (свет)"),
               ("multiply", "Умножение"), ("overlay", "Перекрытие"), ("lighten", "Светлее"),
               ("darken", "Темнее"), ("difference", "Разница")]
PARTICLES = [("none", "Нет"), ("dust", "Пыль"), ("snow", "Снег"), ("rain", "Дождь"),
             ("stars", "Мерцающие звёзды"), ("fireflies", "Светлячки"), ("bubbles", "Пузыри"),
             ("hearts", "Сердечки"), ("sakura", "Лепестки сакуры"), ("confetti", "Конфетти"),
             ("image", "Своя картинка")]
VIZ_STYLES = [("bars", "Столбики"), ("mirror", "Зеркальные столбики"), ("blocks", "Сегменты (как в Winamp)"),
              ("line", "Линия"), ("wave", "Волна с заливкой"), ("dots", "Точки"), ("none", "Скрыть")]
VINYL_STYLES = [("real", "Настоящая пластинка"), ("graphic", "Графичная (точки)"), ("hidden", "Скрыть")]
BUTTON_STYLES = [("glass", "Стекло"), ("solid", "Сплошные"), ("outline", "Контур"),
                 ("neon", "Неон"), ("flat", "Плоские"), ("winamp", "Winamp (объёмные серые)")]

DEFAULT = {
    "format": FORMAT,
    "version": VERSION,
    "id": "",
    "name": "Моя тема",
    "author": "",
    "description": "",
    "created": 0,
    "palette": {
        "bg": "#07080c", "panel": "#0e1018", "panel2": "#13161f",
        "text": "#f2f4fa", "muted": "#6b7280",
        "accent": "#ffffff", "accent2": "#c9ccd6",
        "danger": "#f87171", "glow": "#5b3fd6",
        "cover_accent": False,          # акцент подстраивается под обложку трека
    },
    "font": {
        "family": "", "file": "", "size": 13, "weight": 500, "spacing": 0.0,
        "title_family": "", "title_file": "", "title_size": 19, "title_weight": 700,
    },
    "shape": {
        "radius": 32,           # скругление панелей (остальное — пропорционально)
        "btn_radius": 16,
        "border": 1,
        "glass": 0.07,          # непрозрачность панелей (0 — прозрачные, 1 — сплошные)
        "btn_style": "glass",
        "margin": 12, "spacing": 10,
        "frosted": True,        # матовое стекло: размытый фон под панелями
        "blur": 22,
        "shadow": 0.45,         # тень под панелями
    },
    "background": {
        "type": "glow",
        "color": "#07080c",
        "gradient": {"kind": "linear", "angle": 135.0, "spin": 0.0,
                     "stops": [[0.0, "#1c1236"], [0.55, "#0b0d18"], [1.0, "#050608"]]},
        "media": "", "fit": "cover", "zoom": 1.0, "align_x": 0.5, "align_y": 0.5,
        "blur": 0, "dim": 0.25, "tint": "#000000", "tint_amount": 0.0, "saturation": 1.0,
        "speed": 1.0, "react": 0.6,
        "colors": ["#7c5cff", "#00e5ff", "#ff3d9a"],
        "parallax": 0.0,
        "vignette": 0.25,
        # скрипт фона (EchoScript): on frame(dt) { speed = 1 + audio.bass } — скорость видео/GIF/обоев,
        # zoom, dx/dy (сдвиг, px), dim (затемнение 0…1, -1 — как в теме)
        "script": "",
    },
    "layers": [],
    # родные элементы интерфейса (пластинка, визуализатор, кнопки, панели…): ключ →
    # {"free": вынут из раскладки, "x", "y", "w", "h" — доли центральной области, "hidden": убран}
    "native": {},
    "effects": {
        "particles": {"kind": "none", "count": 60, "speed": 1.0, "size": 1.0, "color": "#ffffff",
                      "image": "", "react": 0.5},
        "grain": 0.0, "scanlines": 0.0, "vignette": 0.0, "flash": 0.0, "flicker": 0.0,
        "behind": False,                # частицы и эффекты экрана — за интерфейсом (на фоне), а не поверх
    },
    "components": {
        # height — высота визуализатора в пикселях (0 — как раньше, по раскладке)
        "visualizer": {"style": "bars", "color1": "", "color2": "", "peaks": True, "curve": True, "height": 0},
        # bg — фон с обложкой за диском: показывать, сдвиг (доли виджета), размер
        "vinyl": {"style": "real", "scale": 1.0, "tonearm": True,
                  "bg": {"on": True, "dx": 0.0, "dy": 0.0, "scale": 1.0}},
        "panels": {"left": True, "right": True, "swap": False, "profile": True, "hints": True,
                   "dock": False},          # стеклянная плашка под кнопками управления
        "seek": {"color1": "", "color2": ""},
        "play": {"style": "text"},          # text — цвет текста, accent — акцент
        "titlebar": {"bg": "", "fg": "", "line": True},
        # standard — обычный интерфейс плеера, слои вокруг него;
        # layers — интерфейс плеера спрятан, всё (кнопки, перемотка, пластинка…) — слои-виджеты
        "ui": "standard",
    },
}

LAYER_DEFAULT = {
    "id": "", "name": "Слой", "kind": "media",
    "src": "", "text": "ECHOES", "font": "", "bold": True, "italic": False,
    "color": "#ffffff", "color2": "", "outline": "", "glow": 0.0,
    "shape": "circle",
    "x": 0.5, "y": 0.5, "size": 0.25, "aspect": 1.0, "rot": 0.0, "opacity": 1.0,
    # свободное растяжение по осям (у картинок и видео — поверх их пропорций) и 3D-наклон, градусы
    "stretch_x": 1.0, "stretch_y": 1.0, "tilt_x": 0.0, "tilt_y": 0.0,
    # min — размер от меньшей стороны окна (пропорции не меняются при любом окне);
    # window — ширина в долях ширины окна, высота — в долях высоты (тянется вместе с окном, как в LOOM)
    "box": "min",
    "z": "front", "blend": "normal", "flip": False, "visible": True, "locked": False,
    "group": "",                        # слои с одной группой выделяются и двигаются вместе
    # стопка анимаций: [{"type": "spin", "speed": 1, "amount": 1, "react": 0.6}, …] — работают все сразу
    "anims": [],
    "anim": "none", "anim_speed": 1.0, "anim_amount": 1.0, "react": 0.6,   # старый формат (одна анимация)
    # видео-слой
    "speed": 1.0,
    # виджет/скрипт (EchoScript, бывший LOOM): исходник, имя виджета, свойства экземпляра (литералы)
    "script": "", "widget": "", "props": {}, "clip": True,
    "fps": 0,                           # частота кадров виджета (0 — как у экрана); тяжёлым хватает 30

    # поведение — обработчики EchoScript у ЛЮБОГО слоя: on frame(dt) { rot += … }, on click { … }
    "behavior": "",
    # эффект из встроенной темы (EFFECTS)
    "effect": "milkdrop", "colors": [], "res": 0.5,
}
LAYER_NUMS = (("x", -0.5, 1.5, 0.5), ("y", -0.5, 1.5, 0.5), ("size", 0.01, 3, 0.25),
              ("aspect", 0.05, 20, 1.0), ("rot", -720, 720, 0.0), ("opacity", 0, 1, 1.0),
              ("stretch_x", 0.05, 20, 1.0), ("stretch_y", 0.05, 20, 1.0),
              ("tilt_x", -85, 85, 0.0), ("tilt_y", -85, 85, 0.0),
              ("anim_speed", 0, 5, 1.0), ("anim_amount", 0, 3, 1.0), ("react", 0, 1, 0.6),
              ("glow", 0, 1, 0.0), ("speed", 0.25, 4, 1.0), ("res", 0.15, 1, 0.5), ("fps", 0, 240, 0))


def _deep_merge(base, over):
    if not isinstance(base, dict):
        return copy.deepcopy(over if over is not None else base)
    out = copy.deepcopy(base)
    if not isinstance(over, dict):
        return out
    for k, v in over.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _num(v, lo, hi, default):
    try:
        f = float(v)
        if math.isnan(f):
            return default
        return max(lo, min(hi, f))
    except (TypeError, ValueError):
        return default


_HEX = re.compile(r"^#([0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


def _color(v, default):
    return v if isinstance(v, str) and _HEX.match(v.strip()) else default


def normalize(t: dict | None, _sub: bool = False) -> dict:
    """Тема из файла (возможно старая/битая) → полная корректная тема.
    _sub — внутренняя часть (оформление режима текста): без своего режима текста."""
    t = _deep_merge(DEFAULT, t or {})
    t["format"], t["version"] = FORMAT, VERSION
    t["name"] = (str(t.get("name") or "Моя тема").strip() or "Моя тема")[:60]
    p = t["palette"]
    for k in ("bg", "panel", "panel2", "text", "muted", "accent", "accent2", "danger", "glow"):
        p[k] = _color(p.get(k), DEFAULT["palette"][k])
    f = t["font"]
    f["size"] = int(_num(f.get("size"), 9, 22, 13))
    f["title_size"] = int(_num(f.get("title_size"), 12, 64, 19))
    f["weight"] = int(_num(f.get("weight"), 100, 900, 500))
    f["title_weight"] = int(_num(f.get("title_weight"), 100, 900, 700))
    f["spacing"] = _num(f.get("spacing"), -2, 8, 0.0)
    s = t["shape"]
    s["radius"] = int(_num(s.get("radius"), 0, 48, 32))
    s["btn_radius"] = int(_num(s.get("btn_radius"), 0, 30, 16))
    s["border"] = int(_num(s.get("border"), 0, 4, 1))
    s["glass"] = _num(s.get("glass"), 0, 1, 0.07)
    s["margin"] = int(_num(s.get("margin"), 0, 48, 12))
    s["spacing"] = int(_num(s.get("spacing"), 0, 40, 10))
    s["blur"] = int(_num(s.get("blur"), 0, 60, 22))
    s["shadow"] = _num(s.get("shadow"), 0, 1, 0.45)
    if s.get("btn_style") not in dict(BUTTON_STYLES):
        s["btn_style"] = "glass"
    b = t["background"]
    if b.get("type") not in dict(BACKGROUND_TYPES):
        b["type"] = "glow"
    for k, lo, hi, d in (("zoom", 0.5, 4, 1.0), ("align_x", 0, 1, 0.5), ("align_y", 0, 1, 0.5),
                         ("blur", 0, 80, 0), ("dim", 0, 1, 0.25), ("tint_amount", 0, 1, 0.0),
                         ("saturation", 0, 2, 1.0), ("speed", 0, 4, 1.0), ("react", 0, 1, 0.6),
                         ("parallax", 0, 1, 0.0), ("vignette", 0, 1, 0.25)):
        b[k] = _num(b.get(k), lo, hi, d)
    cols = [c for c in (b.get("colors") or []) if isinstance(c, str) and _HEX.match(c)]
    b["colors"] = (cols + DEFAULT["background"]["colors"])[:max(3, len(cols))][:6]
    g = b["gradient"]
    stops = []
    for st in g.get("stops") or []:
        try:
            stops.append([_num(st[0], 0, 1, 0.0), _color(st[1], "#000000")])
        except (TypeError, IndexError):
            pass
    if len(stops) < 2:
        stops = copy.deepcopy(DEFAULT["background"]["gradient"]["stops"])
    g["stops"] = sorted(stops, key=lambda s_: s_[0])[:8]
    if g.get("kind") not in dict(GRADIENT_KINDS):
        g["kind"] = "linear"
    g["angle"] = _num(g.get("angle"), -360, 360, 135.0)
    g["spin"] = _num(g.get("spin"), -2, 2, 0.0)
    layers = []
    seen = set()
    for raw in t.get("layers") or []:
        if not isinstance(raw, dict):
            continue
        L = _deep_merge(LAYER_DEFAULT, raw)
        if not L.get("id") or L["id"] in seen:
            L["id"] = uuid.uuid4().hex[:8]
        seen.add(L["id"])
        if L.get("kind") not in dict(LAYER_KINDS):
            L["kind"] = "media"
        for k, lo, hi, d in LAYER_NUMS:
            L[k] = _num(L.get(k), lo, hi, d)
        if L.get("z") not in ("back", "front"):
            L["z"] = "front"
        if L.get("blend") not in dict(BLEND_MODES):
            L["blend"] = "normal"
        if L.get("kind") == "effect" and "opaque" not in L:
            L["opaque"] = L.get("effect") in ("milkdrop", "fluid")    # как в своих темах — сплошные
        if L.get("shape") not in dict(SHAPES):
            L["shape"] = "circle"
        L["anims"] = normalize_anims(L)
        L["anim"] = "none"                              # одна анимация старого формата — уже в стопке
        L["color"] = _color(L.get("color"), "#ffffff")
        L["text"] = str(L.get("text") or "")[:200]
        L["group"] = str(L.get("group") or "")[:24]
        L["script"] = str(L.get("script") or "")[:MAX_SCRIPT]
        L["behavior"] = str(L.get("behavior") or "")[:MAX_SCRIPT]
        L["widget"] = re.sub(r"[^\w]", "", str(L.get("widget") or ""))[:40]
        props = L.get("props") if isinstance(L.get("props"), dict) else {}
        L["props"] = {re.sub(r"[^\w]", "", str(k))[:32]: str(v)[:2000] for k, v in props.items()
                      if re.sub(r"[^\w]", "", str(k))}
        L["clip"] = bool(L.get("clip", True))
        if L.get("box") not in ("min", "window"):
            L["box"] = "min"
        if L.get("effect") not in dict(EFFECTS):
            L["effect"] = "milkdrop"
        L["colors"] = [c for c in (L.get("colors") or []) if isinstance(c, str) and _HEX.match(c)][:6]
        layers.append(L)
    t["layers"] = layers[:128]
    e = t["effects"]
    pt = e["particles"]
    if pt.get("kind") not in dict(PARTICLES):
        pt["kind"] = "none"
    pt["count"] = int(_num(pt.get("count"), 0, 400, 60))
    for k, lo, hi, d in (("speed", 0, 5, 1.0), ("size", 0.2, 5, 1.0), ("react", 0, 1, 0.5)):
        pt[k] = _num(pt.get(k), lo, hi, d)
    pt["color"] = _color(pt.get("color"), "#ffffff")
    for k in ("grain", "scanlines", "vignette", "flash", "flicker"):
        e[k] = _num(e.get(k), 0, 1, 0.0)
    e["behind"] = bool(e.get("behind"))
    c = t["components"]
    v = c["visualizer"]
    if v.get("style") not in dict(VIZ_STYLES):
        v["style"] = "bars"
    vn = c["vinyl"]
    if vn.get("style") not in dict(VINYL_STYLES):
        vn["style"] = "real"
    vn["scale"] = _num(vn.get("scale"), 0.4, 1.3, 1.0)
    vn["image"] = str(vn.get("image") or "")[:300]        # своя картинка (PNG) вместо диска
    vb = vn["bg"] if isinstance(vn.get("bg"), dict) else {}
    vn["bg"] = {"on": bool(vb.get("on", True)), "dx": _num(vb.get("dx"), -2, 2, 0.0),
                "dy": _num(vb.get("dy"), -2, 2, 0.0), "scale": _num(vb.get("scale"), 0.3, 3, 1.0)}
    v["height"] = int(_num(v.get("height"), 0, 1200, 0))
    if c.get("ui") not in dict(UI_MODES):
        c["ui"] = "standard"
    b["script"] = str(b.get("script") or "")[:MAX_SCRIPT]
    nat = {}
    for k, s in (t.get("native") or {}).items():
        if not isinstance(s, dict) or not re.match(r"^\w{1,40}$", str(k)):
            continue
        nat[k] = {"free": bool(s.get("free")), "hidden": bool(s.get("hidden")),
                  "x": _num(s.get("x"), -1, 2, 0.1), "y": _num(s.get("y"), -1, 2, 0.1),
                  "w": _num(s.get("w"), 0.005, 3, 0.2), "h": _num(s.get("h"), 0.005, 3, 0.2)}
        if s.get("collapse"):
            nat[k]["collapse"] = True
    t["native"] = nat
    if not _sub:
        t["lyrics"] = _normalize_lyrics(t.get("lyrics"))
        t["clip"] = normalize_clip(t.get("clip"))
    return t


# ------------------------------------------------------------------ #
#  Режим текста — своё оформление в той же теме                        #
# ------------------------------------------------------------------ #

# что у режима текста своё (остальное — цвета, шрифты окна, формы — общее с основным видом)
LYRICS_PARTS = ("background", "layers", "native", "effects")
LYRICS_ALIGN = [("center", "По центру"), ("left", "Слева"), ("right", "Справа")]
# элементы экрана текста (управляются как родные: двигать, тянуть, убрать)
LYRICS_ITEMS = [("lyr_text", "Текст песни"), ("lyr_vinyl", "Пластинка (в режиме текста)"),
                ("lyr_caption", "Подпись: название и прогресс"), ("lyr_close", "Кнопка «Закрыть»")]
LYRICS_TEXT = {
    "family": "", "file": "",           # шрифт (пусто — как в Яндекс Музыке)
    "size": 0,                          # px; 0 — по ширине панели
    "active_scale": 1.0,                # текущая строка крупнее остальных
    "weight": 700, "italic": False, "upper": False,
    "letter": 0.0,                      # межбуквенный интервал, px
    "align": "center",
    "line_gap": 1.0,                    # воздух между строками
    "manual_scroll": False,
    "scroll_speed": 7.0,
    "translation_scale": 0.75,
    "translation_color": "",
    "anchor": 0.40,                     # где стоит текущая строка (доля высоты панели)
    "color": "",                        # текущая строка (пусто — белый/чёрный по фону)
    "dim_color": "",                    # остальные строки (пусто — тот же цвет, прозрачнее)
    "dim": 1.0,                         # яркость остальных строк (множитель)
    "glow": 0.0, "glow_color": "",      # свечение текущей строки
    "shadow": 0.0,                      # тень под текстом
    "karaoke": False,                   # текущая строка заливается по мере пения
    "fade": 0.22,                       # таяние строк к краям панели
    "panel": "", "panel_radius": 18,    # подложка под текстом (цвет с прозрачностью, #RRGGBBAA)
    "time": True,                       # время «0:00 / 3:12» в углу
    "cap_size": 14,                     # подпись: размер названия
    "cap_progress": True,               # подпись: полоска прогресса
}


# ------------------------------------------------------------------ #
#  Режим клипа — своё оформление в той же теме                         #
# ------------------------------------------------------------------ #

CLIP_BG = [("cover", "Размытая обложка"), ("ambient", "Цвета клипа (эмбилайт)"), ("color", "Сплошной цвет"),
           ("theme", "Фон темы (со слоями)")]
CLIP_FRAMES = [("rounded", "Скруглённая"), ("square", "Прямые углы"), ("none", "Без рамки"), ("tv", "Старый телевизор"),
               ("neon", "Неоновая"), ("polaroid", "Полароид"), ("film", "Киноплёнка")]
CLIP_CONTROLS = [("full", "Полная панель"), ("minimal", "Только кнопки"), ("autohide", "Прятать, пока не нужна")]
CLIP_DEFAULT = {
    "on": False,                        # своё оформление (выключено — по палитре темы)
    "bg": "cover", "color": "", "dim": 0.55,
    "frame": "rounded", "radius": 18, "border": "", "border_w": 0,
    "glow": 0.5, "glow_color": "",      # свечение вокруг видео (пусто — цвета самого клипа)
    "scale": 0.82,                      # размер видео (доля области)
    "fit": "contain",                   # contain — целиком, cover — заполнить, обрезая края
    "accent": "", "text": "", "panel": "",
    "controls": "full", "title": True,
    "fx": [], "fx_amount": 0.7,         # эффекты, включённые сразу при открытии клипа
}


def normalize_clip(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    c = _deep_merge(CLIP_DEFAULT, raw)
    c["on"] = bool(c.get("on"))
    if c.get("bg") not in dict(CLIP_BG):
        c["bg"] = "cover"
    if c.get("frame") not in dict(CLIP_FRAMES):
        c["frame"] = "rounded"
    if c.get("controls") not in dict(CLIP_CONTROLS):
        c["controls"] = "full"
    if c.get("fit") not in ("contain", "cover"):
        c["fit"] = "contain"
    for k, lo, hi, d in (("dim", 0, 1, 0.55), ("radius", 0, 80, 18), ("border_w", 0, 24, 0), ("glow", 0, 1, 0.5),
                         ("scale", 0.3, 1.0, 0.82), ("fx_amount", 0, 1, 0.7)):
        c[k] = _num(c.get(k), lo, hi, d)
    for k in ("color", "border", "glow_color", "accent", "text", "panel"):
        c[k] = _color(c.get(k), "")
    c["title"] = bool(c.get("title", True))
    c["fx"] = [str(x) for x in (c.get("fx") or []) if isinstance(x, str)][:24]
    return c


def _normalize_lyrics(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    sub = normalize({k: raw.get(k) for k in LYRICS_PARTS if raw.get(k) is not None}, _sub=True)
    out = {"on": bool(raw.get("on"))}
    for k in LYRICS_PARTS:
        out[k] = sub[k]
    tx = _deep_merge(LYRICS_TEXT, raw.get("text") if isinstance(raw.get("text"), dict) else {})
    tx["size"] = int(_num(tx.get("size"), 0, 120, 0))
    tx["weight"] = int(_num(tx.get("weight"), 100, 900, 700))
    for k, lo, hi, d in (("active_scale", 0.6, 2.5, 1.0), ("letter", -4, 20, 0.0), ("line_gap", 0.2, 4, 1.0),
                         ("anchor", 0.05, 0.95, 0.40), ("dim", 0, 2.5, 1.0), ("glow", 0, 1, 0.0),
                         ("shadow", 0, 1, 0.0), ("fade", 0, 0.5, 0.22), ("panel_radius", 0, 80, 18),
                         ("cap_size", 8, 40, 14)):
        tx[k] = _num(tx.get(k), lo, hi, d)
    tx["panel_radius"] = int(tx["panel_radius"])
    tx["cap_size"] = int(tx["cap_size"])
    if tx.get("align") not in dict(LYRICS_ALIGN):
        tx["align"] = "center"
    for k in ("color", "dim_color", "glow_color", "panel"):
        tx[k] = _color(tx.get(k), "")
    tx["scroll_speed"] = _num(tx.get("scroll_speed"), 1, 20, 7)
    tx["translation_scale"] = _num(tx.get("translation_scale"), 0.4, 1.5, 0.75)
    tx["translation_color"] = _color(tx.get("translation_color"), "")
    for k in ("italic", "upper", "karaoke", "time", "cap_progress", "manual_scroll"):
        tx[k] = bool(tx.get(k))
    tx["family"] = str(tx.get("family") or "")[:80]
    tx["file"] = str(tx.get("file") or "")[:260]
    out["text"] = tx
    return out


def lyrics_start(t: dict) -> dict:
    """Включить своё оформление режима текста: фон и эффекты — копия основного вида, слоёв нет."""
    ly = t.setdefault("lyrics", _normalize_lyrics(None))
    if not ly.get("on"):
        ly["on"] = True
        if not ly.get("layers") and not ly.get("native"):
            ly["background"] = copy.deepcopy(t["background"])
            ly["effects"] = copy.deepcopy(t["effects"])
    return ly


def lyrics_view(t: dict) -> dict:
    """Тема «как её видно в режиме текста»: фон, слои, эффекты и раскладка — из t["lyrics"].
    Части — те же объекты (не копии): правка вида — правка темы."""
    ly = t.get("lyrics") or {}
    if not ly.get("on"):
        return t
    v = dict(t)
    for k in LYRICS_PARTS:
        if k in ly:
            v[k] = ly[k]
    return v


def normalize_anims(L: dict) -> list[dict]:
    """Стопка анимаций слоя. Старое поле anim (одна анимация) становится первой в стопке."""
    out = []
    raw = L.get("anims") if isinstance(L.get("anims"), list) else []
    legacy = L.get("anim")
    if legacy and legacy != "none" and legacy in dict(ANIMATIONS):
        raw = [{"type": legacy, "speed": L.get("anim_speed", 1.0), "amount": L.get("anim_amount", 1.0),
                "react": L.get("react", 0.6)}] + list(raw)
    for a in raw[:8]:
        if not isinstance(a, dict) or a.get("type") not in dict(ANIMATIONS) or a.get("type") == "none":
            continue
        out.append({"type": a["type"], "speed": _num(a.get("speed"), 0, 5, 1.0),
                    "amount": _num(a.get("amount"), 0, 3, 1.0), "react": _num(a.get("react"), 0, 1, 0.6),
                    "on": bool(a.get("on", True))})
    return out


def layer_animated(L: dict) -> bool:
    return any(a.get("on", True) for a in L.get("anims") or [])


def new_layer(kind="media", **over) -> dict:
    L = _deep_merge(LAYER_DEFAULT, over)
    L["kind"] = kind
    L["id"] = uuid.uuid4().hex[:8]
    L["anims"] = normalize_anims(L)
    L["anim"] = "none"
    return L


# ------------------------------------------------------------------ #
#  Цвет                                                               #
# ------------------------------------------------------------------ #

def hex_to_rgb(h: str):
    h = (h or "#000000").lstrip("#")
    if len(h) >= 6:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return 0, 0, 0


def rgb_to_hex(r, g, b) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, int(round(v)))) for v in (r, g, b)))


def mix(a: str, b: str, t: float) -> str:
    ra, ga, ba = hex_to_rgb(a)
    rb, gb, bb = hex_to_rgb(b)
    return rgb_to_hex(ra + (rb - ra) * t, ga + (gb - ga) * t, ba + (bb - ba) * t)


def rgba(h: str, a: float) -> str:
    r, g, b = hex_to_rgb(h)
    return f"rgba({r},{g},{b},{max(0.0, min(1.0, a)):.3f})"


def luminance(h: str) -> float:
    def lin(c):
        c /= 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = hex_to_rgb(h)
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def is_light(h: str) -> bool:
    return luminance(h) > 0.4


def hsl(h: str):
    r, g, b = (v / 255.0 for v in hex_to_rgb(h))
    hh, ll, ss = colorsys.rgb_to_hls(r, g, b)
    return hh * 360.0, ss, ll


def from_hsl(h: float, s: float, l: float) -> str:
    r, g, b = colorsys.hls_to_rgb((h % 360) / 360.0, max(0, min(1, l)), max(0, min(1, s)))
    return rgb_to_hex(r * 255, g * 255, b * 255)


def shift_hue(h: str, deg: float, sat=None, light=None) -> str:
    hh, s, l = hsl(h)
    return from_hsl(hh + deg, s if sat is None else sat, l if light is None else light)


def readable_on(bg: str) -> str:
    return "#08090d" if luminance(bg) > 0.35 else "#ffffff"


def derive_palette(bg: str, accent: str, text: str | None = None, accent2: str | None = None,
                   glow: str | None = None) -> dict:
    """Из 2–4 главных цветов — вся служебная палитра (панели, рамки, приглушённый текст…)."""
    light = is_light(bg)
    if not text:
        text = "#16161c" if light else "#f2f4fa"
    if not accent2:
        hh, s, l = hsl(accent)
        accent2 = from_hsl(hh + 28, min(1.0, s * 0.95 + 0.05), l + (0.08 if l < 0.6 else -0.1)) \
            if s > 0.12 else mix(accent, bg, 0.25)
    return {
        "bg": bg,
        "panel": mix(bg, text, 0.035),
        "panel2": mix(bg, text, 0.075),
        "text": text,
        "muted": mix(text, bg, 0.48),
        "accent": accent,
        "accent2": accent2,
        "danger": "#e5484d" if light else "#f87171",
        "glow": glow or accent2,
        "cover_accent": False,
    }


HARMONIES = [("analog", "Соседние"), ("complement", "Контраст"), ("triad", "Триада"),
             ("split", "Расщеплённая"), ("mono", "Монохром")]


def harmony(base: str, mode: str) -> tuple[str, str]:
    """Пара акцентов (accent, accent2) по правилу гармонии от базового цвета."""
    h, s, l = hsl(base)
    s = max(s, 0.55)
    l = min(max(l, 0.45), 0.68)
    deg = {"analog": 32, "complement": 180, "triad": 120, "split": 150, "mono": 0}.get(mode, 32)
    a1 = from_hsl(h, s, l)
    a2 = from_hsl(h + deg, s * (0.9 if deg else 0.55), l + (0.14 if deg == 0 else 0.0))
    return a1, a2


def resolved_palette(t: dict) -> dict:
    """Полная палитра для QSS: плюс стекло и рамки, выведенные из настроек формы."""
    p = dict(t["palette"])
    light = is_light(p["bg"])
    ink = "#000000" if light else "#ffffff"
    g = float(t["shape"].get("glass", 0.07))
    # glass (поля, кнопки) и glass2 (панели): от прозрачного стекла до сплошной панели
    if g <= 0.5:
        p["glass"] = rgba(ink, 0.025 + g * 0.35)
        p["glass2"] = rgba(ink, 0.04 + g * 0.6)
    else:
        k = (g - 0.5) * 2
        p["glass"] = rgba(mix(p["panel"], ink, 0.06), 0.2 + k * 0.8)
        p["glass2"] = rgba(p["panel"], 0.35 + k * 0.65)
    p["border"] = rgba(ink if not light else "#000000", 0.08 if not light else 0.10)
    p["border2"] = rgba(ink if not light else "#000000", 0.14 if not light else 0.18)
    return p


# ------------------------------------------------------------------ #
#  Палитра из картинки                                                #
# ------------------------------------------------------------------ #

def image_colors(path: str, k: int = 6) -> list[tuple[str, float]]:
    """Главные цвета картинки (k-means): [(hex, доля)], по убыванию доли."""
    try:
        import numpy as np
        from PyQt6.QtGui import QImage
        img = QImage(str(path))
        if img.isNull():
            return []
        img = img.convertToFormat(QImage.Format.Format_RGB32).scaled(64, 64)
        w, h = img.width(), img.height()
        ptr = img.constBits()
        ptr.setsize(img.sizeInBytes())
        arr = np.frombuffer(ptr, np.uint8).reshape(h, img.bytesPerLine() // 4, 4)[:, :w, :3][..., ::-1]
        px = arr.reshape(-1, 3).astype(np.float32)
        lum = px @ np.array([0.299, 0.587, 0.114], np.float32)
        order = np.argsort(lum)
        cent = px[order[np.linspace(0, len(px) - 1, k).astype(int)]].copy()
        for _ in range(12):
            d = ((px[:, None, :] - cent[None, :, :]) ** 2).sum(-1)
            lab = d.argmin(1)
            for i in range(k):
                m = lab == i
                if m.any():
                    cent[i] = px[m].mean(0)
        share = np.bincount(lab, minlength=k) / float(len(px))
        out = sorted(((rgb_to_hex(*cent[i]), float(share[i])) for i in range(k)), key=lambda x: -x[1])
        return [c for c in out if c[1] > 0.005]
    except Exception:                                      # noqa: BLE001
        return []


def palette_from_image(path: str, prefer_dark: bool = True) -> dict | None:
    cols = image_colors(path, 6)
    if not cols:
        return None
    by_sat = sorted(cols, key=lambda c: -(hsl(c[0])[1] * (0.4 + min(1.0, c[1] * 5)) *
                                          (1 - abs(hsl(c[0])[2] - 0.55))))
    accent = by_sat[0][0]
    accent2 = by_sat[1][0] if len(by_sat) > 1 else None
    dark = sorted(cols, key=lambda c: luminance(c[0]))
    if prefer_dark:
        base = dark[0][0]
        h, s, l = hsl(base)
        bg = from_hsl(h, min(s, 0.5), min(l, 0.07))
    else:
        base = dark[-1][0]
        h, s, l = hsl(base)
        bg = from_hsl(h, min(s, 0.35), max(l, 0.93))
    # акцент должен читаться на фоне
    ah, as_, al = hsl(accent)
    if contrast(accent, bg) < 3:
        accent = from_hsl(ah, max(as_, 0.6), 0.65 if prefer_dark else 0.4)
    if accent2 and contrast(accent2, bg) < 2.2:
        accent2 = None
    return derive_palette(bg, accent, accent2=accent2)


# ------------------------------------------------------------------ #
#  Хранилище                                                          #
# ------------------------------------------------------------------ #

_TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                     ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p",
                      "r", "s", "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))


def slugify(name: str) -> str:
    s = "".join(_TRANSLIT.get(ch, ch) for ch in (name or "").lower())
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:32] or "theme"


class ThemeStore:
    """Папка тем: list/load/save/delete/duplicate, ассеты, экспорт/импорт."""

    def __init__(self, data_dir):
        self.root = Path(data_dir) / "themes"
        self.root.mkdir(parents=True, exist_ok=True)

    # ── пути ── #

    def dir_of(self, theme_or_id) -> Path:
        tid = theme_or_id["id"] if isinstance(theme_or_id, dict) else str(theme_or_id)
        return self.root / tid

    def asset(self, theme, rel: str) -> str:
        """Абсолютный путь ассета (или '' если нет)."""
        if not rel:
            return ""
        p = Path(rel)
        if p.is_absolute():
            return str(p) if p.exists() else ""
        ap = self.dir_of(theme) / rel
        return str(ap) if ap.exists() else ""

    # ── список / загрузка ── #

    def list(self) -> list[dict]:
        out = []
        for d in sorted(self.root.iterdir()) if self.root.exists() else []:
            f = d / "theme.json"
            if d.is_dir() and f.exists():
                try:
                    t = normalize(json.loads(f.read_text("utf-8")))
                    t["id"] = d.name
                    out.append(t)
                except Exception as e:                     # noqa: BLE001
                    print("[themes] bad theme", d.name, e)
        out.sort(key=lambda t: (t.get("created") or 0, t["name"]))
        return out

    def load(self, tid: str) -> dict | None:
        f = self.root / tid / "theme.json"
        try:
            t = normalize(json.loads(f.read_text("utf-8")))
            t["id"] = tid
            return t
        except Exception:                                  # noqa: BLE001
            return None

    def _unique_id(self, name: str) -> str:
        base = slugify(name)
        tid, n = base, 2
        while (self.root / tid).exists():
            tid = f"{base}-{n}"
            n += 1
        return tid

    def save(self, theme: dict) -> dict:
        t = normalize(theme)
        if not t.get("id"):
            t["id"] = self._unique_id(t["name"])
        if not t.get("created"):
            t["created"] = int(time.time())
        d = self.dir_of(t)
        (d / "assets").mkdir(parents=True, exist_ok=True)
        tmp = d / "theme.json.tmp"
        tmp.write_text(json.dumps(t, ensure_ascii=False, indent=1), "utf-8")
        os.replace(tmp, d / "theme.json")
        theme.update(id=t["id"], created=t["created"])
        return t

    def delete(self, tid: str):
        d = self.root / tid
        if d.exists() and d.parent == self.root:
            shutil.rmtree(d, ignore_errors=True)

    def duplicate(self, theme: dict, name: str | None = None) -> dict:
        src = self.dir_of(theme)
        t = copy.deepcopy(normalize(theme))
        t["name"] = name or (t["name"] + " (копия)")
        t["id"] = self._unique_id(t["name"])
        t["created"] = int(time.time())
        dst = self.dir_of(t)
        if src.exists():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        return self.save(t)

    # ── ассеты ── #

    def import_asset(self, theme: dict, src: str) -> str:
        """Скопировать файл в assets/ темы → относительный путь 'assets/…'."""
        srcp = Path(src)
        if not srcp.exists():
            return ""
        if not theme.get("id"):
            theme["id"] = self._unique_id(theme.get("name") or "theme")
        d = self.dir_of(theme) / "assets"
        d.mkdir(parents=True, exist_ok=True)
        try:
            if srcp.resolve().parent == d.resolve():
                return f"assets/{srcp.name}"
        except OSError:
            pass
        stem = re.sub(r"[^\w\-]+", "_", srcp.stem, flags=re.U)[:40] or "file"
        name = f"{stem}{srcp.suffix.lower()}"
        n = 2
        while (d / name).exists():
            if (d / name).stat().st_size == srcp.stat().st_size:
                return f"assets/{name}"              # тот же файл уже есть
            name = f"{stem}_{n}{srcp.suffix.lower()}"
            n += 1
        shutil.copy2(srcp, d / name)
        return f"assets/{name}"

    def unused_assets(self, theme: dict) -> list[Path]:
        d = self.dir_of(theme) / "assets"
        if not d.exists():
            return []
        used = set(_referenced_assets(theme))
        return [f for f in d.iterdir() if f.is_file() and f"assets/{f.name}" not in used]

    # ── экспорт / импорт ── #

    def export(self, theme: dict, out_path: str) -> str:
        t = self.save(theme)
        out = Path(out_path)
        if out.suffix.lower() != PACKAGE_EXT:
            out = out.with_suffix(PACKAGE_EXT)
        d = self.dir_of(t)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("theme.json", json.dumps(t, ensure_ascii=False, indent=1))
            for rel in sorted(set(_referenced_assets(t))):
                f = d / rel
                if f.is_file():
                    z.write(f, rel)
        return str(out)

    def import_package(self, path: str) -> dict:
        with zipfile.ZipFile(path) as z:
            meta = json.loads(z.read("theme.json").decode("utf-8"))
            if meta.get("format") != FORMAT:
                raise ValueError("Это не тема ECHOES")
            t = normalize(meta)
            t["name"] = suggest_name({x["name"] for x in self.list()}, t["name"])   # не путать с уже установленной
            t["id"] = self._unique_id(t["name"])
            t["created"] = int(time.time())
            d = self.dir_of(t)
            (d / "assets").mkdir(parents=True, exist_ok=True)
            for info in z.infolist():
                name = info.filename.replace("\\", "/")
                if not name.startswith("assets/") or name.endswith("/"):
                    continue
                base = Path(name).name                 # без подпапок и «..»: защита от zip-slip
                if not base or base.startswith("."):
                    continue
                with z.open(info) as src, open(d / "assets" / base, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        return self.save(t)


def _referenced_assets(t: dict):
    ly = t.get("lyrics") or {}
    if isinstance(ly, dict) and (ly.get("background") or ly.get("layers")):
        yield from _referenced_assets({k: ly.get(k) or {} for k in ("background", "effects")} |
                                      {"layers": ly.get("layers") or []})
        tf = (ly.get("text") or {}).get("file")
        if tf and str(tf).startswith("assets/"):
            yield tf
    b = t.get("background", {})
    for rel in (b.get("media"), t.get("font", {}).get("file"), t.get("font", {}).get("title_file"),
                t.get("effects", {}).get("particles", {}).get("image"),
                ((t.get("components") or {}).get("vinyl") or {}).get("image")):
        if rel and str(rel).startswith("assets/"):
            yield rel
    for L in t.get("layers", []):
        for rel in (L.get("src"), L.get("font")):
            if rel and str(rel).startswith("assets/"):
                yield rel
        for v in (L.get("props") or {}).values():          # свойства виджетов: src: "assets/…"
            s = str(v).strip().strip('"').strip("'")
            if s.startswith("assets/"):
                yield s
        design = L.get("design") if isinstance(L.get("design"), dict) else {}
        for it in design.get("items") or []:               # картинки внутри своей кнопки (редактор кнопок)
            s = str((it or {}).get("src") or "") if isinstance(it, dict) else ""
            if s.startswith("assets/"):
                yield s


# ------------------------------------------------------------------ #
#  Тема → данные для плеера                                           #
# ------------------------------------------------------------------ #

def to_theme_data(t: dict) -> dict:
    """Словарь, который понимает плеер (как THEMES[...]) + параметры QSS + сама тема."""
    t = normalize(t)
    d = resolved_palette(t)
    f, s = t["font"], t["shape"]
    d.update({
        "custom": True, "custom_id": t["id"], "ct": t,
        "_font_family": f.get("family") or "", "_font_size": f["size"], "_font_weight": f["weight"],
        "_letter_spacing": f["spacing"],
        "_title_family": f.get("title_family") or f.get("family") or "",
        "_title_size": f["title_size"], "_title_weight": f["title_weight"],
        "_radius": s["radius"], "_btn_radius": s["btn_radius"], "_border": s["border"],
        "_btn_style": s["btn_style"], "_dock": bool(t["components"]["panels"].get("dock")),
    })
    return d


# ------------------------------------------------------------------ #
#  Шаблоны                                                            #
# ------------------------------------------------------------------ #

def _t(name, desc, palette, **sections) -> dict:
    t = copy.deepcopy(DEFAULT)
    t["name"], t["description"] = TR(name), TR(desc)
    t["palette"].update(palette)
    for k, v in sections.items():
        t[k] = _deep_merge(t[k], v) if isinstance(v, dict) else copy.deepcopy(v)
    return normalize(t)


def blank_canvas() -> dict:
    """Пустой холст: интерфейс плеера спрятан, ни одного слоя, ровный тёмный фон — всё с нуля."""
    return _t("Пустой холст", "Ничего лишнего: ни панелей, ни кнопок. Добавляйте виджеты, картинки, видео, "
                              "эффекты и скрипты — интерфейс целиком ваш",
              {"bg": "#08080c", "accent": "#8b7bff", "accent2": "#38d6ff"},
              background={"type": "color", "color": "#08080c", "vignette": 0.0, "dim": 0.0},
              shape={"shadow": 0.0, "frosted": False},
              components={"ui": "layers", "titlebar": {"line": False}})


def templates() -> list[dict]:
    """Готовые стартовые темы (без внешних файлов — всё рисуется само)."""
    return [
        _t("Чистый лист", "Тёмная основа Vinyl glass — собирайте с нуля",
           {}),
        blank_canvas(),
        _t("Северное сияние", "Живое сияние за матовым стеклом, звёзды и мягкий свет",
           derive_palette("#04060c", "#7dfcc8", accent2="#9b8cff", glow="#00ffa3"),
           background={"type": "aurora", "colors": ["#00ffa3", "#7c5cff", "#00b3ff", "#ff5fd2"],
                       "react": 0.7, "vignette": 0.35, "dim": 0.1},
           shape={"glass": 0.12, "frosted": True, "blur": 26, "radius": 30, "shadow": 0.5},
           effects={"particles": {"kind": "stars", "count": 90, "speed": 0.6, "size": 1.0, "react": 0.6}},
           components={"visualizer": {"style": "mirror", "color1": "#7dfcc8", "color2": "#9b8cff"},
                       "panels": {"dock": True}}),
        _t("Ретровейв 1986", "Неоновое солнце и сетка, которые едут в такт музыке",
           derive_palette("#12001f", "#ff2bd6", accent2="#2de2e6", glow="#ff2bd6"),
           background={"type": "synthwave", "colors": ["#ff2bd6", "#ffb800", "#2de2e6"], "react": 0.8,
                       "vignette": 0.3},
           shape={"glass": 0.18, "radius": 10, "btn_radius": 6, "btn_style": "neon", "border": 1},
           effects={"scanlines": 0.22, "flash": 0.2},
           font={"title_size": 22, "title_weight": 900, "spacing": 0.6},
           components={"visualizer": {"style": "blocks", "color1": "#ff2bd6", "color2": "#ffb800"},
                       "titlebar": {"line": True}}),
        _t("Терминал", "Зелёный фосфор, цифровой дождь, сканлайны и моноширинный шрифт",
           {"bg": "#010801", "panel": "#031203", "panel2": "#051a05", "text": "#39ff6a",
            "muted": "#1d8c3a", "accent": "#39ff6a", "accent2": "#b6ff3b", "glow": "#00ff55",
            "danger": "#ff5555"},
           background={"type": "matrix", "colors": ["#39ff6a", "#b6ff3b", "#0d3d16"], "react": 0.5,
                       "dim": 0.35, "vignette": 0.55},
           font={"family": "Consolas", "title_family": "Consolas", "size": 13, "weight": 600,
                 "title_size": 20, "title_weight": 800},
           shape={"radius": 4, "btn_radius": 2, "border": 1, "glass": 0.35, "btn_style": "outline",
                  "frosted": False, "shadow": 0.0},
           effects={"scanlines": 0.45, "flicker": 0.12, "vignette": 0.35, "grain": 0.12},
           components={"visualizer": {"style": "blocks", "color1": "#39ff6a", "color2": "#b6ff3b",
                                      "curve": False},
                       "vinyl": {"style": "graphic"}}),
        _t("Лава-лампа", "Тягучая плазма, которая вскипает от баса, и пузыри",
           derive_palette("#14040a", "#ffbe0b", accent2="#ff006e", glow="#fb5607"),
           background={"type": "plasma", "colors": ["#ff5f1f", "#ff006e", "#3a0ca3", "#ffbe0b"],
                       "react": 0.85, "speed": 0.8, "dim": 0.2, "vignette": 0.3},
           shape={"glass": 0.14, "frosted": True, "blur": 30, "radius": 36, "btn_style": "solid"},
           effects={"particles": {"kind": "bubbles", "count": 40, "speed": 0.7, "size": 1.2, "react": 0.7}},
           components={"visualizer": {"style": "wave", "color1": "#ffbe0b", "color2": "#ff006e"},
                       "panels": {"dock": True}}),
        _t("Варп-прыжок", "Звёзды летят навстречу — чем громче трек, тем быстрее",
           derive_palette("#000000", "#9ad1ff", accent2="#ffffff", glow="#3a6bff"),
           background={"type": "starfield", "colors": ["#ffffff", "#9ad1ff", "#ffd29a"], "react": 0.9,
                       "speed": 1.0, "vignette": 0.45},
           shape={"glass": 0.06, "frosted": True, "blur": 18, "radius": 24, "btn_style": "flat"},
           effects={"flash": 0.15},
           components={"visualizer": {"style": "line", "color1": "#9ad1ff", "color2": "#ffffff"},
                       "panels": {"dock": True}}),
        _t("Сакура", "Светлая пастель, лепестки и большие мягкие скругления",
           {"bg": "#fff3f7", "panel": "#ffe9f0", "panel2": "#ffdde9", "text": "#3b2a33",
            "muted": "#9a7f8c", "accent": "#ff7aa8", "accent2": "#ffb3c7", "glow": "#ffc2d6",
            "danger": "#e5484d"},
           background={"type": "gradient", "gradient": {"kind": "linear", "angle": 160, "spin": 0.15,
                                                        "stops": [[0, "#ffe3ec"], [0.5, "#fff7e6"],
                                                                  [1, "#e8f3ff"]]},
                       "vignette": 0.0},
           shape={"radius": 42, "btn_radius": 22, "glass": 0.55, "btn_style": "solid", "shadow": 0.25,
                  "frosted": False, "border": 0},
           effects={"particles": {"kind": "sakura", "count": 45, "speed": 0.8, "size": 1.2, "react": 0.4}},
           components={"visualizer": {"style": "dots", "color1": "#ff7aa8", "color2": "#ffb3c7"},
                       "vinyl": {"style": "graphic"}}),
        _t("Брутализм", "Бетон, толстые рамки, ноль скруглений и один яркий цвет",
           {"bg": "#e7e2d8", "panel": "#ddd7cb", "panel2": "#d2cbbd", "text": "#111111",
            "muted": "#5a5650", "accent": "#ff3b00", "accent2": "#111111", "glow": "#ff3b00",
            "danger": "#d40000"},
           background={"type": "color", "color": "#e7e2d8", "vignette": 0.0},
           font={"weight": 700, "title_weight": 900, "title_size": 30, "spacing": 0.4},
           shape={"radius": 0, "btn_radius": 0, "border": 2, "glass": 0.8, "btn_style": "outline",
                  "frosted": False, "shadow": 0.0},
           effects={"grain": 0.22},
           components={"visualizer": {"style": "blocks", "color1": "#111111", "color2": "#ff3b00",
                                      "curve": False}}),
    ]


# ------------------------------------------------------------------ #
#  «Удиви меня»                                                       #
# ------------------------------------------------------------------ #

_ADJ = ["Неоновый", "Лунный", "Кислотный", "Бархатный", "Хрустальный", "Ночной", "Солнечный",
        "Ледяной", "Космический", "Призрачный", "Медовый", "Электрический", "Туманный", "Ржавый"]
_NOUN = ["сад", "шторм", "сон", "бульвар", "океан", "вокзал", "прилив", "кратер", "рассвет",
         "пульс", "мираж", "берег", "лабиринт", "фонарь"]


def surprise(seed=None) -> dict:
    """Случайная, но собранная по правилам тема (цвета, фон, частицы, формы, элементы)."""
    rnd = random.Random(seed)
    dark = rnd.random() < 0.8
    hue = rnd.uniform(0, 360)
    mode = rnd.choice([m for m, _ in HARMONIES])
    a1, a2 = harmony(from_hsl(hue, 0.85, 0.6), mode)
    bg = from_hsl(hue + rnd.uniform(-20, 20), rnd.uniform(0.25, 0.6), rnd.uniform(0.02, 0.07)) if dark \
        else from_hsl(hue, rnd.uniform(0.2, 0.5), rnd.uniform(0.92, 0.97))
    if not dark:
        a1 = from_hsl(hsl(a1)[0], 0.75, 0.45)
    t = copy.deepcopy(DEFAULT)
    adj, noun = rnd.choice(_ADJ), rnd.choice(_NOUN)
    t["name"] = f"{TR(adj)} {TR(noun)}"
    t["description"] = TR("Сгенерировано «Удиви меня»")
    t["palette"].update(derive_palette(bg, a1, accent2=a2))
    bgtype = rnd.choice(["aurora", "plasma", "starfield", "synthwave", "gradient", "aurora", "matrix"]) if dark \
        else rnd.choice(["gradient", "color", "aurora"])
    cols = [a1, a2, from_hsl(hue + rnd.choice([60, 90, 200]), 0.8, 0.55), from_hsl(hue + 300, 0.7, 0.6)]
    t["background"].update({
        "type": bgtype, "colors": cols, "react": round(rnd.uniform(0.4, 0.95), 2),
        "speed": round(rnd.uniform(0.6, 1.4), 2), "vignette": round(rnd.uniform(0.1, 0.5), 2),
        "dim": round(rnd.uniform(0.0, 0.3), 2), "color": bg,
        "gradient": {"kind": rnd.choice(["linear", "radial", "conical"]), "angle": rnd.uniform(0, 360),
                     "spin": rnd.choice([0, 0.1, 0.25]),
                     "stops": [[0, mix(a1, bg, 0.55)], [0.55, mix(a2, bg, 0.8)], [1, bg]]},
    })
    t["shape"].update({
        "radius": rnd.choice([0, 8, 16, 24, 32, 40]), "btn_radius": rnd.choice([0, 8, 14, 20]),
        "glass": round(rnd.uniform(0.04, 0.4), 2), "btn_style": rnd.choice([b for b, _ in BUTTON_STYLES]),
        "frosted": rnd.random() < 0.7, "border": rnd.choice([0, 1, 1, 2]), "shadow": round(rnd.uniform(0, 0.6), 2),
    })
    t["effects"]["particles"].update({
        "kind": rnd.choice(["none", "stars", "fireflies", "dust", "snow", "bubbles", "confetti"]),
        "count": rnd.choice([30, 50, 80]), "color": rnd.choice(["#ffffff", a1, a2]),
        "speed": round(rnd.uniform(0.5, 1.5), 2),
    })
    t["effects"].update({"scanlines": rnd.choice([0, 0, 0.2]), "grain": rnd.choice([0, 0, 0.12]),
                         "flash": rnd.choice([0, 0.15, 0.25])})
    t["components"]["visualizer"].update({"style": rnd.choice(["bars", "mirror", "blocks", "wave", "dots", "line"]),
                                          "color1": a1, "color2": a2})
    t["components"]["vinyl"]["style"] = rnd.choice(["real", "real", "graphic"])
    t["font"].update({"title_size": rnd.choice([19, 22, 26]), "title_weight": rnd.choice([700, 800, 900]),
                      "spacing": rnd.choice([0, 0, 0.5, 1.0])})
    return normalize(t)


def suggest_name(existing: set[str], base: str = "") -> str:
    base = base or TR("Моя тема")
    if base not in existing:
        return base
    n = 2
    while f"{base} {n}" in existing:
        n += 1
    return f"{base} {n}"
