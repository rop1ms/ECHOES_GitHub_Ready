# dsl_layout.py
"""Визуальная правка скриптов EchoScript: всё, что делается мышью, записывается обратно в код.

  • set_geom      — расстановка: x/y/w/h внутри «add Имя { … }» на нужной строке
  • remove_add    — удалить виджет со сцены
  • add_block     — вставить готовый блок (класс виджета + строку add в scene)
  • theme_get/set — ключи блока theme { … }
  • BLOCKS        — каталог готовых блоков (фигуры, текст, картинка, видео, обложка, спектр, кнопки…)
  • script_from_theme — новый скрипт из любой темы плеера (цвета + фон-картинка/видео из конструктора)
"""
from __future__ import annotations

import re

NUM_RE = re.compile(r"^-?\d+(\.\d+)?([eE][+-]?\d+)?$")


# ── разбор исходника без учёта строк и комментариев ──
def mask(src: str) -> str:
    """Содержимое строк и комментарии → пробелы (длина и переводы строк сохраняются)."""
    out = list(src)
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == "`":                                       # шаблонная строка — может быть в несколько строк
            j = i + 1
            while j < n and src[j] != "`":
                if src[j] == "\\":
                    j += 1
                j += 1
            for k in range(i + 1, min(j, n)):
                if out[k] != "\n":
                    out[k] = " "
            i = j + 1
            continue
        if c in "\"'":
            j = i + 1
            while j < n and src[j] != c and src[j] != "\n":
                if src[j] == "\\":
                    j += 1
                j += 1
            for k in range(i + 1, min(j, n)):
                if out[k] != "\n":
                    out[k] = " "
            i = j + 1
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
            continue
        i += 1
    return "".join(out)


def _line_start(src: str, line: int) -> int:
    pos = 0
    for _ in range(max(0, line - 1)):
        j = src.find("\n", pos)
        if j < 0:
            return len(src)
        pos = j + 1
    return pos


def _match_brace(m: str, open_i: int) -> int:
    depth = 0
    for i in range(open_i, len(m)):
        ch = m[i]
        if ch in "{[(":
            depth += 1
        elif ch in "}])":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _entries(src: str, m: str, a: int, b: int):
    """Пары «ключ: значение» верхнего уровня внутри {…} (a — «{», b — «}») → [(key, v0, v1)]."""
    out = []
    i = a + 1
    depth = 0
    start = i
    parts = []
    while i < b:
        ch = m[i]
        if ch in "{[(":
            depth += 1
        elif ch in "}])":
            depth -= 1
        elif depth == 0 and ch in ",\n":
            parts.append((start, i))
            start = i + 1
        i += 1
    parts.append((start, b))
    for s, e in parts:
        seg = m[s:e]
        c = seg.find(":")
        if c < 0:
            continue
        key = seg[:c].strip().strip("\"'")
        if not key:
            # ключ-строка замаскирован — берём из исходника
            key = src[s:s + c].strip().strip("\"'")
        v0 = s + c + 1
        while v0 < e and src[v0] in " \t":
            v0 += 1
        v1 = e
        while v1 > v0 and src[v1 - 1] in " \t\r":
            v1 -= 1
        if key:
            out.append((key, v0, v1))
    return out


def find_add(src: str, line: int, name: str):
    """(start, open, close) для «add name {…}» на строке line; open/close = None, если без {…}."""
    m = mask(src)
    ls = _line_start(src, line)
    le = m.find("\n", ls)
    le = len(m) if le < 0 else le
    rx = re.compile(r"\badd\s+" + re.escape(name) + r"\b")
    mt = rx.search(m, ls, le)
    if mt is None:
        return None
    j = mt.end()
    while j < len(m) and m[j] in " \t":
        j += 1
    if j < len(m) and m[j] == "{":
        k = _match_brace(m, j)
        if k < 0:
            return None
        return mt.start(), j, k
    return mt.start(), None, mt.end()


def fmt_num(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _set_entries(src: str, a: int, b: int, vals: dict) -> str:
    """Записать ключи в {…} [a, b]: существующие — заменить, новые — дописать в конец."""
    m = mask(src)
    ents = {k: (v0, v1) for k, v0, v1 in _entries(src, m, a, b)}
    edits = []
    missing = []
    for k, v in vals.items():
        if k in ents:
            edits.append((ents[k][0], ents[k][1], v))
        else:
            missing.append(f"{k}: {v}")
    if missing:
        inner = src[a + 1:b]
        stripped = inner.rstrip()
        if stripped.strip() == "":
            edits.append((a + 1, b, " " + ", ".join(missing) + " "))
        else:
            sep = "" if stripped.endswith(",") else ","
            if "\n" in inner.strip("\n ") or inner.rstrip(" ").endswith("\n"):
                # многострочная запись — новый ключ отдельной строкой с тем же отступом
                ind = re.search(r"\n([ \t]*)\S[^\n]*\s*$", inner)
                pad = ind.group(1) if ind else "  "
                ins = "".join(f"\n{pad}{x}" for x in missing)
                edits.append((a + 1 + len(stripped), a + 1 + len(stripped), ins))
            else:
                edits.append((a + 1 + len(stripped), a + 1 + len(stripped), sep + " " + ", ".join(missing)))
    for s, e, v in sorted(edits, key=lambda t: -t[0]):
        src = src[:s] + v + src[e:]
    return src


def set_geom(src: str, line: int, name: str, geom: dict) -> str | None:
    """geom: {"x": .., "y": .., "w": .., "h": ..} в долях окна. Возвращает новый исходник или None."""
    f = find_add(src, line, name)
    if f is None:
        return None
    start, a, b = f
    vals = {k: fmt_num(v) for k, v in geom.items()}
    if a is None:
        body = " { " + ", ".join(f"{k}: {v}" for k, v in vals.items()) + " }"
        return src[:b] + body + src[b:]
    return _set_entries(src, a, b, vals)


def set_props(src: str, line: int, name: str, props: dict) -> str | None:
    """Свойства экземпляра (значения — готовый текст выражения)."""
    f = find_add(src, line, name)
    if f is None:
        return None
    start, a, b = f
    if a is None:
        body = " { " + ", ".join(f"{k}: {v}" for k, v in props.items()) + " }"
        return src[:b] + body + src[b:]
    return _set_entries(src, a, b, props)


def get_props(src: str, line: int, name: str) -> dict:
    """Текст значений свойств экземпляра {ключ: выражение}."""
    f = find_add(src, line, name)
    if f is None or f[1] is None:
        return {}
    m = mask(src)
    return {k: src[v0:v1] for k, v0, v1 in _entries(src, m, f[1], f[2])}


def remove_add(src: str, line: int, name: str) -> str | None:
    f = find_add(src, line, name)
    if f is None:
        return None
    start, a, b = f
    end = (b + 1) if a is not None else b
    ls = src.rfind("\n", 0, start) + 1
    le = src.find("\n", end)
    le = len(src) if le < 0 else le
    if src[ls:start].strip() == "" and src[end:le].strip() in ("", ";"):
        return src[:ls] + src[le + 1:]                    # строка целиком
    return src[:start] + src[end:]


def move_z(src: str, line: int, name: str, z: int) -> str | None:
    return set_props(src, line, name, {"z": str(int(z))})


# ── блоки theme { } и scene { } ──
def _block_span(src: str, kw: str):
    m = mask(src)
    mt = re.search(r"(?m)^[ \t]*" + kw + r"\s*\{", m)
    if mt is None:
        return None
    a = m.index("{", mt.start())
    b = _match_brace(m, a)
    return (mt.start(), a, b) if b > 0 else None


def theme_get(src: str) -> dict:
    sp = _block_span(src, "theme")
    if sp is None:
        return {}
    m = mask(src)
    out = {}
    for k, v0, v1 in _entries(src, m, sp[1], sp[2]):
        t = src[v0:v1].strip()
        if len(t) >= 2 and t[0] in "\"'" and t[-1] == t[0]:
            out[k] = t[1:-1]
        elif NUM_RE.match(t):
            out[k] = float(t)
        else:
            out[k] = t
    return out


def _lit(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return fmt_num(float(v))
    s = str(v).replace("\\", "/").replace('"', '\\"')
    return f'"{s}"'


def theme_set(src: str, values: dict) -> str:
    vals = {k: _lit(v) for k, v in values.items()}
    sp = _block_span(src, "theme")
    if sp is None:
        body = "theme { " + ", ".join(f"{k}: {v}" for k, v in vals.items()) + " }\n\n"
        return body + src
    return _set_entries(src, sp[1], sp[2], vals)


def has_widget(src: str, cls: str) -> bool:
    return re.search(r"(?m)^[ \t]*widget\s+" + re.escape(cls) + r"\b", mask(src)) is not None


def widget_names(src: str) -> list[str]:
    return re.findall(r"(?m)^[ \t]*widget\s+([A-Za-z_]\w*)", mask(src))


def add_instance(src: str, cls: str, spec: str) -> str:
    """Дописать «add cls { spec }» в конец scene { } (или создать scene)."""
    line = f"  add {cls} {{ {spec} }}\n" if spec else f"  add {cls}\n"
    sp = _block_span(src, "scene")
    if sp is None:
        return src.rstrip() + "\n\nscene {\n" + line + "}\n"
    _s, a, b = sp
    before = src[:b]
    if not before.endswith("\n"):
        before = before.rstrip(" \t") + "\n"
    return before + line + src[b:]


def add_class(src: str, code: str) -> str:
    """Класс виджета — перед scene { } (или в конец файла)."""
    sp = _block_span(src, "scene")
    code = code.strip("\n") + "\n\n"
    if sp is None:
        return src.rstrip() + "\n\n" + code
    s = sp[0]
    return src[:s] + code + src[s:]


def add_block(src: str, key: str, geom: dict, extra: dict | None = None) -> str:
    b = BLOCKS[key]
    cls = b["cls"]
    if has_widget(src, cls):
        marker = f"// блок: {key}"
        if marker not in src:                              # у пользователя свой класс с тем же именем
            n = 2
            while has_widget(src, f"{cls}{n}"):
                n += 1
            new = f"{cls}{n}"
            src = add_class(src, re.sub(r"\bwidget\s+" + cls + r"\b", "widget " + new, b["code"], 1))
            cls = new
    else:
        src = add_class(src, b["code"])
    parts = [f"{k}: {fmt_num(v)}" for k, v in geom.items()]
    for k, v in (extra or b.get("spec") or {}).items():
        parts.append(f"{k}: {v}")
    return add_instance(src, cls, ", ".join(parts))


# ══════════════════════════════════════════════════════════════════════════ #
#  Каталог блоков
# ══════════════════════════════════════════════════════════════════════════ #

BLOCKS: dict[str, dict] = {}


def _block(key, title, group, cls, geom, code, spec=None, hint=""):
    BLOCKS[key] = {"key": key, "title": title, "group": group, "cls": cls, "geom": geom,
                   "code": code.strip("\n"), "spec": spec or {}, "hint": hint}


_block("backdrop", "Фон со свечением", "Фон", "Backdrop", (0, 0, 1, 1), """
widget Backdrop {       // блок: backdrop
  prop color = theme.bg
  prop light = theme.accent
  prop light2 = theme.accent2
  prop react = 0.5      // 0…1 — дыхание от баса
  on draw {
    fill(color)
    rect(0, 0, w, h)
    blend("add")
    glow(w * 0.22, h * 0.18, max(w, h) * (0.45 + audio.bass * react * 0.15), with_alpha(light, 0.35))
    glow(w * 0.82, h * 0.88, max(w, h) * 0.4, with_alpha(light2, 0.22))
    blend("normal")
  }
}""", {"z": "-10"}, "Залитый фон и два мягких пятна света, дышат от баса")

_block("picture", "Картинка / GIF", "Фон", "Picture", (0.3, 0.3, 0.3, 0.3), """
widget Picture {        // блок: picture
  prop src = ""         // путь к картинке: PNG, JPG, GIF, WEBP
  prop radius = 0
  prop opacity = 1
  prop dim = 0          // затемнение 0…1
  on draw {
    alpha(opacity)
    if not image(src, 0, 0, w, h, radius) {
      fill("#ffffff10")
      rect(0, 0, w, h, radius)
      fill(theme.muted)
      text("картинка: свойство src", w / 2, h / 2, 13, align: "center", maxw: w - 8)
    }
    if dim > 0 {
      fill(with_alpha("#000000", clamp(dim)))
      rect(0, 0, w, h, radius)
    }
  }
}""", hint="Картинка с обрезкой по центру; анимированные GIF/WEBP проигрываются")

_block("video", "Видео", "Фон", "Video", (0, 0, 1, 1), """
widget Video {          // блок: video
  prop src = ""         // путь к видео: MP4, WEBM, MOV… — без звука, по кругу
  prop radius = 0
  prop dim = 0.25       // затемнение 0…1
  prop react = 0.4      // 0…1 — светлеет на ударах
  on draw {
    if not video(src, 0, 0, w, h, radius) {
      fill("#ffffff0c")
      rect(0, 0, w, h, radius)
      fill(theme.muted)
      text("видео: свойство src", w / 2, h / 2, 13, align: "center", maxw: w - 8)
    }
    let d = clamp(dim - audio.bass * react * 0.4)
    if d > 0 {
      fill(with_alpha("#000000", d))
      rect(0, 0, w, h, radius)
    }
  }
}""", {"z": "-9"}, "Видеофон (без звука, по кругу), светлеет от баса")

_block("shape", "Фигура", "Фигуры", "Shape", (0.4, 0.4, 0.15, 0.2), """
widget Shape {          // блок: shape
  prop kind = "rect"    // rect | circle | ring | star | hex | triangle | polygon
  prop sides = 5        // для polygon — число сторон, для star — число лучей
  prop color = theme.accent
  prop color2 = nil     // второй цвет — градиент сверху вниз
  prop radius = 18      // скругление для rect
  prop border = 0       // толщина контура
  prop border_color = theme.text
  prop pulse = 0        // 0…1 — пульсация от баса
  prop spin = 0         // градусов в секунду
  state angle = 0
  on frame(dt) { angle += spin * dt }
  on draw {
    let s = 1 + audio.bass * pulse * 0.3
    let r = min(w, h) / 2 - border
    translate(w / 2, h / 2)
    rotate(angle)
    scale(s, s)
    if color2 != nil { fill(linear(0, -h / 2, 0, h / 2, [color, color2])) } else { fill(color) }
    if border > 0 { stroke(border_color, border) } else { nostroke() }
    if kind == "circle" { ellipse(0, 0, w / 2 - border, h / 2 - border) }
    elif kind == "ring" { ring(0, 0, r * 0.72, r) }
    elif kind == "star" { star(0, 0, r * 0.45, r, max(3, sides), -90) }
    elif kind == "polygon" { ngon(0, 0, r, max(3, sides), -90) }
    elif kind == "hex" { ngon(0, 0, r, 6, 0) }
    elif kind == "triangle" { ngon(0, 0, r, 3, -90) }
    else { rect(-w / 2 + border, -h / 2 + border, w - border * 2, h - border * 2, radius) }
  }
}""", {"kind": '"rect"'}, "Прямоугольник, круг, кольцо, звезда, многоугольник (sides), шестиугольник, треугольник")

_block("vector", "Векторный контур (SVG)", "Фигуры", "Vector", (0.4, 0.4, 0.12, 0.18), """
widget Vector {         // блок: vector
  prop d = "M12 21 C12 21 3 14 3 8.5 A4.5 4.5 0 0 1 12 6 A4.5 4.5 0 0 1 21 8.5 C21 14 12 21 12 21 Z"
  prop color = theme.accent
  prop color2 = nil     // второй цвет — градиент
  prop border = 0
  prop border_color = theme.text
  prop pulse = 0.3      // 0…1 — пульсация от баса
  prop spin = 0         // градусов в секунду
  state angle = 0
  on frame(dt) { angle += spin * dt }
  on draw {
    let s = 1 + audio.bass * pulse * 0.3
    translate(w / 2, h / 2)
    rotate(angle)
    scale(s, s)
    if color2 != nil { fill(linear(0, -h / 2, 0, h / 2, [color, color2])) } else { fill(color) }
    if border > 0 { stroke(border_color, border) } else { nostroke() }
    path(d, -w * 0.42, -h * 0.42, w * 0.84, h * 0.84)    // запас под пульсацию и контур
  }
}""", {}, "Любой контур в синтаксисе SVG (свойство d) — иконки, логотипы, свои фигуры")

_block("blob", "Живая капля", "Фигуры", "Blob", (0.35, 0.3, 0.3, 0.4), """
widget Blob {           // блок: blob
  prop color = theme.accent
  prop color2 = theme.accent2
  prop points = 48
  prop wobble = 0.18    // сила колыхания
  prop react = 0.6      // 0…1 — раздувается от баса
  prop speed = 0.6
  on draw {
    let cx = w / 2
    let cy = h / 2
    let r = min(w, h) * 0.38 * (1 + audio.bass * react * 0.25)
    let pts = []
    for i in 0..points {
      let a = i * TAU / points
      let n = noise(cos(a) * 1.3 + time * speed, sin(a) * 1.3 + time * speed * 0.7, audio.mid)
      let rr = r * (1 + n * wobble * 2 + audio.fft[i % 64] * react * 0.12)
      push(pts, cx + cos(a) * rr * w / min(w, h), cy + sin(a) * rr * h / min(w, h))
    }
    fill(radial(cx, cy, r * 1.2, [color, color2]))
    curve(pts, true)
  }
}""", {}, "Мягкая колышущаяся фигура, дышит от баса и середины")

_block("label", "Текст", "Текст", "Label", (0.1, 0.1, 0.4, 0.08), """
widget Label {          // блок: label
  prop label = "Текст"
  prop bind = ""        // "title" | "artist" | "album" | "time" — подставить данные трека
  prop size = 28
  prop color = theme.text
  prop align = "left"   // left | center | right
  prop weight = 700
  prop font = nil
  on draw {
    let s = label
    if bind == "title" { s = track.loaded ? track.title : "ECHOES" }
    elif bind == "artist" { s = track.artist }
    elif bind == "album" { s = track.album }
    elif bind == "time" { s = fmt_time(track.position) + " / " + fmt_time(track.duration) }
    fill(color)
    let tx = align == "center" ? w / 2 : (align == "right" ? w : 0)
    text(s, tx, h / 2, size, align: align, weight: weight, font: font, maxw: w)
  }
}""", {"label": '"Текст"'}, "Надпись; bind подставляет название, исполнителя, альбом или время")

_block("title", "Название и исполнитель", "Текст", "TrackInfo", (0.1, 0.62, 0.45, 0.12), """
widget TrackInfo {      // блок: title
  prop size = 30
  prop align = "left"
  on draw {
    let tx = align == "center" ? w / 2 : (align == "right" ? w : 0)
    fill(theme.text)
    text(track.loaded ? track.title : "ECHOES", tx, h * 0.36, size, align: align, weight: 800, maxw: w)
    fill(theme.muted)
    text(track.loaded ? track.artist : "выберите трек", tx, h * 0.8, size * 0.55, align: align, weight: 500, maxw: w)
  }
}""", hint="Две строки: название трека и исполнитель")

_block("cover", "Обложка", "Плеер", "Cover", (0.1, 0.15, 0.3, 0.42), """
widget Cover {          // блок: cover
  prop radius = 22
  prop spin = false     // вращать, как пластинку
  prop shadow = true
  state angle = 0
  on frame(dt) { if spin and player.playing { angle += dt * 24 } }
  on draw {
    let s = min(w, h)
    let ox = (w - s) / 2
    let oy = (h - s) / 2
    if shadow {
      blend("add")
      glow(w / 2, h / 2, s * (0.62 + audio.bass * 0.06), with_alpha(theme.accent, 0.28))
      blend("normal")
    }
    if spin {
      translate(w / 2, h / 2)
      rotate(angle)
      clip(-s / 2, -s / 2, s, s, s / 2)
      if not image(track.cover, -s / 2, -s / 2, s, s) {
        fill(radial(0, 0, s / 2, [theme.accent, theme.accent2]))
        circle(0, 0, s / 2)
      }
      fill(theme.bg)
      circle(0, 0, s * 0.06)
    } else {
      if not image(track.cover, ox, oy, s, s, radius) {
        fill(linear(ox, oy, ox + s, oy + s, [theme.accent, theme.accent2]))
        rect(ox, oy, s, s, radius)
      }
    }
  }
}""", hint="Обложка трека (квадрат или вращающийся диск)")

_block("spectrum", "Спектр", "Визуализаторы", "Spectrum", (0.05, 0.75, 0.9, 0.2), """
widget Spectrum {       // блок: spectrum
  prop bars = 48
  prop gap = 3
  prop color = theme.accent
  prop color2 = theme.accent2
  prop mirror = false   // столбики от центра
  state lv = []
  on frame(dt) {
    if len(lv) != bars { lv = fill_list(bars, 0) }
    for i in 0..bars {
      let v = audio.fft[floor(i * 64 / bars)]
      lv[i] = approach(lv[i], v, v > lv[i] ? 30 : 7, dt)
    }
  }
  on draw {
    if len(lv) == bars {
      let bw = max(1, (w - gap * (bars - 1)) / bars)
      fill(linear(0, h, 0, 0, [color, color2]))
      for i in 0..bars {
        let bh = max(2, lv[i] * h)
        let by = mirror ? (h - bh) / 2 : h - bh
        rect(i * (bw + gap), by, bw, bh, min(bw / 2, 4))
      }
    }
  }
}""", hint="Столбики спектра с плавным спадом")

_block("wave", "Волна", "Визуализаторы", "Waveform", (0.05, 0.4, 0.9, 0.2), """
widget Waveform {       // блок: wave
  prop color = theme.accent2
  prop thick = 2.5
  prop glow_on = true
  on draw {
    let n = len(audio.wave)
    if n > 1 {
      let pts = []
      for i in 0..n { push(pts, i * w / (n - 1), h / 2 + audio.wave[i] * h * 0.45) }
      nofill()
      if glow_on {
        blend("add")
        stroke(with_alpha(color, 0.25), thick * 4)
        curve(pts)
        blend("normal")
      }
      stroke(color, thick)
      curve(pts)
    }
  }
}""", hint="Форма звуковой волны")

_block("halo", "Круговой спектр", "Визуализаторы", "Halo", (0.35, 0.25, 0.3, 0.45), """
widget Halo {           // блок: halo
  prop rays = 72
  prop hue = 200        // начальный оттенок 0…360
  state spin = 0
  on frame(dt) { spin += dt * (8 + audio.mid * 90) }
  on draw {
    let cx = w / 2
    let cy = h / 2
    let r0 = min(w, h) * 0.26
    let segs = []
    for i in 0..rays {
      let a = rad(i * 360 / rays + spin)
      let v = audio.fft[floor(i * 64 / rays) % 64]
      let r1 = r0 + 6 + v * r0 * 0.9
      push(segs, cx + cos(a) * r0, cy + sin(a) * r0, cx + cos(a) * r1, cy + sin(a) * r1)
    }
    blend("add")
    stroke(hsv(hue + audio.level * 120, 0.75, 1), 3)
    lines(segs)
    glow(cx, cy, r0 * (1.1 + audio.bass * 0.4), with_alpha(hsv(hue + 40, 0.8, 1), 0.3))
    blend("normal")
  }
}""", hint="Лучи спектра по кругу, вращаются от середины")

_block("sparks", "Искры на бит", "Визуализаторы", "Sparks", (0.2, 0.2, 0.6, 0.6), """
widget Sparks {         // блок: sparks
  prop color = theme.accent
  prop amount = 1       // множитель количества
  state parts = []
  on beat(p) {
    for i in 0..floor((8 + p * 26) * amount) {
      let a = rand(0, TAU)
      let s = rand(60, 280) * (0.4 + p)
      push(parts, {x: w / 2, y: h / 2, vx: cos(a) * s, vy: sin(a) * s, life: 1})
    }
  }
  on frame(dt) {
    let keep = []
    for q in parts {
      q.vy += 160 * dt
      q.x += q.vx * dt
      q.y += q.vy * dt
      q.life -= dt * 0.9
      if q.life > 0 and len(keep) < 220 { push(keep, q) }
    }
    parts = keep
  }
  on draw {
    blend("add")
    for q in parts { glow(q.x, q.y, 4 + q.life * 10, with_alpha(color, q.life)) }
    blend("normal")
  }
}""", {"clip": "false"}, "Вспышки частиц на ударах (с гравитацией)")

_block("buttons", "Кнопки управления", "Плеер", "Button", (0.4, 0.82, 0.07, 0.1), """
widget Button {         // блок: buttons
  prop action = "play"  // play | prev | next | shuffle | repeat
  prop color = theme.accent
  on click {
    if action == "play" { toggle() }
    elif action == "prev" { prev() }
    elif action == "next" { next() }
    elif action == "shuffle" { toggle_shuffle() }
    else { cycle_repeat() }
  }
  on draw {
    let r = min(w, h) / 2 * (pressed ? 0.92 : 1)
    let cx = w / 2
    let cy = h / 2
    let big = action == "play"
    let on_ = (action == "shuffle" and player.shuffle) or (action == "repeat" and player.repeat > 0)
    fill(big ? (hover ? mix(color, "#ffffff", 0.15) : color) : (hover ? "#ffffff24" : "#ffffff10"))
    circle(cx, cy, r)
    fill(big ? theme.bg : (on_ ? color : theme.text))
    let s = r * 0.42
    if action == "play" {
      if player.playing {
        rect(cx - s * 0.75, cy - s, s * 0.5, s * 2, 2)
        rect(cx + s * 0.25, cy - s, s * 0.5, s * 2, 2)
      } else { poly([cx - s * 0.6, cy - s, cx - s * 0.6, cy + s, cx + s, cy]) }
    } elif action == "prev" {
      poly([cx + s * 0.7, cy - s * 0.8, cx + s * 0.7, cy + s * 0.8, cx - s * 0.4, cy])
      rect(cx - s * 0.75, cy - s * 0.8, s * 0.3, s * 1.6, 1)
    } elif action == "next" {
      poly([cx - s * 0.7, cy - s * 0.8, cx - s * 0.7, cy + s * 0.8, cx + s * 0.4, cy])
      rect(cx + s * 0.45, cy - s * 0.8, s * 0.3, s * 1.6, 1)
    } else {
      text(action == "shuffle" ? "⤮" : (player.repeat == 2 ? "↻1" : "↻"), cx, cy, r * 0.8, align: "center", weight: 700)
    }
  }
}""", {"action": '"play"'}, "Кнопка управления: play, prev, next, shuffle, repeat (свойство action)")

_block("seek", "Прогресс трека", "Плеер", "Seekbar", (0.1, 0.78, 0.8, 0.06), """
widget Seekbar {        // блок: seek
  prop color = theme.accent
  prop times = true     // показывать время
  state hold = -1
  on press(x) { hold = clamp(x / w) }
  on drag(x) { hold = clamp(x / w) }
  on release(x) {
    seek(clamp(x / w))
    hold = -1
  }
  on draw {
    let p = hold >= 0 ? hold : track.progress
    let ty = times ? h * 0.35 : h / 2
    let th = hover or hold >= 0 ? 6 : 4
    fill("#ffffff1c")
    rect(0, ty - th / 2, w, th, th / 2)
    fill(linear(0, 0, w, 0, [color, theme.accent2]))
    rect(0, ty - th / 2, w * p, th, th / 2)
    if hover or hold >= 0 {
      fill(theme.text)
      circle(w * p, ty, 7)
    }
    if times {
      fill(theme.muted)
      text(fmt_time(p * track.duration), 0, h * 0.82, 11)
      text(fmt_time(track.duration), w, h * 0.82, 11, align: "right")
    }
  }
}""", hint="Полоса перемотки: клик и перетаскивание")

_block("volume", "Громкость", "Плеер", "Volume", (0.7, 0.9, 0.2, 0.05), """
widget Volume {         // блок: volume
  prop color = theme.accent
  on press(x) { set_volume(clamp(x / w)) }
  on drag(x) { set_volume(clamp(x / w)) }
  on wheel(d) { set_volume(clamp(player.volume + d * 0.05)) }
  on draw {
    let v = player.volume
    fill("#ffffff1c")
    rect(0, h / 2 - 3, w, 6, 3)
    fill(color)
    rect(0, h / 2 - 3, w * v, 6, 3)
    fill(theme.text)
    circle(w * v, h / 2, hover ? 8 : 6)
  }
}""", hint="Ползунок громкости: клик, перетаскивание, колесо")

_block("tracks", "Список треков", "Плеер", "Tracklist", (0.62, 0.1, 0.33, 0.6), """
widget Tracklist {      // блок: tracks
  prop row = 36
  state scroll = 0
  state over = -1
  on wheel(d) { scroll = clamp(scroll - d * 3, 0, max(0, playlist.count - floor(h / row))) }
  on move(x, y) { over = floor(y / row) + floor(scroll) }
  on leave { over = -1 }
  on click(x, y) {
    let i = floor(y / row) + floor(scroll)
    if i < playlist.count { play_index(i) }
  }
  on track { scroll = clamp(playlist.index - 2, 0, max(0, playlist.count - floor(h / row))) }
  on draw {
    let first = floor(scroll)
    let n = min(playlist.count - first, ceil(h / row))
    for k in 0..n {
      let i = first + k
      let t = playlist.tracks[i]
      let ry = k * row
      if i == playlist.index {
        fill(with_alpha(theme.accent, 0.18))
        rect(0, ry + 2, w, row - 4, 9)
      } elif i == over {
        fill("#ffffff0d")
        rect(0, ry + 2, w, row - 4, 9)
      }
      fill(i == playlist.index ? theme.accent : theme.text)
      text(t.title, 12, ry + row * 0.38, 13, weight: 600, maxw: w - 70)
      fill(theme.muted)
      text(t.artist, 12, ry + row * 0.72, 11, maxw: w - 70)
      text(fmt_time(t.duration), w - 10, ry + row / 2, 11, align: "right")
    }
  }
}""", hint="Текущий плейлист: колесо — прокрутка, клик — включить")


BLANK = """// Пустой холст. Блоки — вкладка «Блоки»; «Расстановка» — двигать и тянуть их мышью.
theme { name: "%s", bg: "#0a0a12", accent: "#7c5cff", accent2: "#35e0c2", text: "#f2f2f7", muted: "#8b8ba3" }

scene {
}
"""


def starter(name: str) -> str:
    """Готовый набор: фон, обложка, название, прогресс, кнопки, громкость, спектр, список."""
    src = BLANK % name.replace('"', "'")
    for key, geom, extra in (
            ("backdrop", (0, 0, 1, 1), {"z": "-10", "cache": "false"}),
            ("cover", (0.05, 0.08, 0.32, 0.5), None),
            ("title", (0.05, 0.6, 0.5, 0.1), None),
            ("seek", (0.05, 0.72, 0.5, 0.06), None),
            ("buttons", (0.17, 0.8, 0.06, 0.1), {"action": '"prev"'}),
            ("buttons", (0.25, 0.79, 0.08, 0.12), {"action": '"play"'}),
            ("buttons", (0.35, 0.8, 0.06, 0.1), {"action": '"next"'}),
            ("volume", (0.43, 0.83, 0.12, 0.04), None),
            ("spectrum", (0.4, 0.1, 0.18, 0.45), {"mirror": "true", "bars": "24"}),
            ("tracks", (0.62, 0.08, 0.34, 0.84), None)):
        src = add_block(src, key, dict(zip("xywh", geom)), extra)
    return src


# ── импорт из тем ──
def palette_of(theme_data: dict) -> dict:
    """Цвета темы плеера (обычной или из конструктора) → ключи theme { }."""
    d = theme_data or {}
    pal = dict(d)
    ct = d.get("ct")
    if isinstance(ct, dict) and isinstance(ct.get("palette"), dict):
        pal.update(ct["palette"])
    out = {}
    for k in ("bg", "accent", "accent2", "text", "muted", "panel"):
        v = pal.get(k)
        if isinstance(v, str) and v.startswith("#"):
            out[k] = v
    fam = ""
    if isinstance(ct, dict):
        fam = (ct.get("font") or {}).get("family") or ""
    fam = fam or d.get("_font_family") or ""
    if fam:
        out["font"] = fam
    return out


def background_media(theme_data: dict, store=None) -> str:
    """Картинка/видео фона темы из конструктора (абсолютный путь) или ''."""
    ct = (theme_data or {}).get("ct")
    if not isinstance(ct, dict):
        return ""
    rel = (ct.get("background") or {}).get("media") or ""
    if not rel:
        return ""
    if store is not None:
        try:
            return store.asset(ct, rel) or ""
        except Exception:                                  # noqa: BLE001
            return ""
    return rel


VIDEO_EXT = (".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v", ".wmv")


def script_from_theme(name: str, theme_data: dict, store=None) -> str:
    src = starter(name)
    src = theme_set(src, {"name": name, **palette_of(theme_data)})
    media = background_media(theme_data, store)
    if media:
        key = "video" if media.lower().endswith(VIDEO_EXT) else "picture"
        extra = {"src": _lit(media), "z": "-9"}
        if key == "picture":
            extra["dim"] = "0.3"
        src = add_block(src, key, {"x": 0, "y": 0, "w": 1, "h": 1}, extra)
    return src


# ══════════════════════════════════════════════════════════════════════════ #
#  Свойства и события экземпляра
# ══════════════════════════════════════════════════════════════════════════ #

def remove_prop(src: str, line: int, name: str, key: str) -> str | None:
    """Убрать ключ из «add name { … }» (вместе с запятой)."""
    f = find_add(src, line, name)
    if f is None or f[1] is None:
        return None
    a, b = f[1], f[2]
    m = mask(src)
    for k, v0, v1 in _entries(src, m, a, b):
        if k != key:
            continue
        ks = src.rfind(k, a + 1, v0)                     # начало ключа
        ks = ks if ks > a else v0
        e = v1
        j = e
        while j < b and src[j] in " \t":
            j += 1
        if j < b and src[j] == ",":
            e = j + 1                                      # «k: v, » — с запятой после
        else:
            j = ks - 1
            while j > a and src[j] in " \t":
                j -= 1
            if src[j] == ",":
                ks = j                                     # последний ключ — убрать запятую перед ним
        out = src[:ks] + src[e:]
        f2 = find_add(out, line, name)
        if f2 is not None and f2[1] is not None and out[f2[1] + 1:f2[2]].strip() == "":
            out = out[:f2[1] + 1] + " " + out[f2[2]:]
        return out
    return src


EVENTS = [("click", "Клик"), ("press", "Нажатие"), ("wheel", "Колесо мыши"), ("beat", "Удар в музыке"),
          ("track", "Смена трека"), ("enter", "Курсор зашёл"), ("leave", "Курсор ушёл")]

EVENT_PRESETS = [
    ("Пауза / воспроизведение", "fn() { toggle() }"),
    ("Следующий трек", "fn() { next() }"),
    ("Предыдущий трек", "fn() { prev() }"),
    ("Перемешивание", "fn() { toggle_shuffle() }"),
    ("Шаффл по исполнителю", "fn() { artist_shuffle() }"),
    ("Режим повтора", "fn() { cycle_repeat() }"),
    ("Громкость +10%", "fn() { set_volume(clamp(player.volume + 0.1)) }"),
    ("Громкость −10%", "fn() { set_volume(clamp(player.volume - 0.1)) }"),
    ("Громкость колесом", "fn(d) { set_volume(clamp(player.volume + d * 0.05)) }"),
    ("Перемотка колесом", "fn(d) { seek(clamp(track.progress + d * 0.02)) }"),
    ("В начало трека", "fn() { seek(0) }"),
    ("Экспорт с эффектами", "fn() { export_fx() }"),
    ("Записать в журнал", 'fn() { log(name + ": событие") }'),
]


# ══════════════════════════════════════════════════════════════════════════ #
#  Импорт: виджеты из других скриптов, слои из тем конструктора
# ══════════════════════════════════════════════════════════════════════════ #

def widget_span(src: str, cls: str):
    """(start, end) кода «widget cls { … }» вместе с переводом строки после."""
    m = mask(src)
    mt = re.search(r"(?m)^[ \t]*widget\s+" + re.escape(cls) + r"\b", m)
    if mt is None:
        return None
    a = m.find("{", mt.end())
    b = _match_brace(m, a) if a >= 0 else -1
    if b < 0:
        return None
    e = b + 1
    if e < len(src) and src[e] == "\n":
        e += 1
    return mt.start(), e


def top_defs(src: str) -> dict:
    """Верхний уровень: {имя: (start, end)} для «fn имя(…) { … }» и «let имя = …»."""
    m = mask(src)
    out = {}
    depth = 0
    i = 0
    n = len(m)
    while i < n:
        ch = m[i]
        if ch in "{[(":
            depth += 1
        elif ch in "}])":
            depth -= 1
        elif depth == 0 and (i == 0 or m[i - 1] == "\n"):
            mt = re.match(r"[ \t]*(fn|let)\s+([A-Za-z_]\w*)", m[i:i + 200])
            if mt:
                kind, name = mt.group(1), mt.group(2)
                if kind == "fn":
                    a = m.find("{", i)
                    b = _match_brace(m, a) if a >= 0 else -1
                    e = b + 1 if b > 0 else m.find("\n", i)
                else:
                    e = i
                    d2 = 0
                    while e < n:                           # до конца строки, но с учётом скобок
                        c = m[e]
                        if c in "{[(":
                            d2 += 1
                        elif c in "}])":
                            d2 -= 1
                        elif c == "\n" and d2 <= 0:
                            break
                        e += 1
                if e < n and src[e] == "\n":
                    e += 1
                out[name] = (i, e)
                i = e
                continue
        i += 1
    return out


def instance_lines(src: str, cls: str) -> list[str]:
    """Тексты «add cls …» из scene { } (каждый — целиком, даже многострочный)."""
    sp = _block_span(src, "scene")
    if sp is None:
        return []
    m = mask(src)
    out = []
    for mt in re.finditer(r"\badd\s+" + re.escape(cls) + r"\b", m[sp[1]:sp[2]]):
        st = sp[1] + mt.start()
        j = sp[1] + mt.end()
        while j < len(m) and m[j] in " \t":
            j += 1
        if j < len(m) and m[j] == "{":
            k = _match_brace(m, j)
            out.append(src[st:k + 1])
        else:
            out.append(src[st:sp[1] + mt.end()])
    return out


def import_widgets(target: str, source: str, classes: list[str], with_instances: bool = True) -> tuple[str, list[str]]:
    """Скопировать классы виджетов (и нужные им функции/переменные верхнего уровня) из source в target.
    Конфликт имён — класс переименовывается (Name2…). Возвращает (новый текст, имена добавленных классов)."""
    defs = top_defs(source)
    have = top_defs(target)
    added = []
    need_defs = []
    for cls in classes:
        sp = widget_span(source, cls)
        if sp is None:
            continue
        code = source[sp[0]:sp[1]]
        new = cls
        if has_widget(target, cls):
            tsp = widget_span(target, cls)
            if tsp is not None and target[tsp[0]:tsp[1]].strip() == code.strip():
                code = None                                # такой же класс уже есть
            else:
                n = 2
                while has_widget(target, f"{cls}{n}"):
                    n += 1
                new = f"{cls}{n}"
                code = re.sub(r"\bwidget\s+" + re.escape(cls) + r"\b", "widget " + new, code, 1)
        if code is not None:
            # функции и переменные верхнего уровня, на которые ссылается класс (рекурсивно)
            queue = [code]
            while queue:
                txt = queue.pop()
                for name, (a, b) in defs.items():
                    if name in have or name in need_defs:
                        continue
                    if re.search(r"\b" + re.escape(name) + r"\b", mask(txt)):
                        need_defs.append(name)
                        queue.append(source[a:b])
            target = add_class(target, code)
        if with_instances:
            for ln in instance_lines(source, cls):
                body = re.sub(r"^add\s+" + re.escape(cls) + r"\b", "add " + new, ln)
                sp2 = _block_span(target, "scene")
                if sp2 is None:
                    target = target.rstrip() + "\n\nscene {\n}\n"
                    sp2 = _block_span(target, "scene")
                before = target[:sp2[2]]
                if not before.endswith("\n"):
                    before = before.rstrip(" \t") + "\n"
                target = before + "  " + body.strip() + "\n" + target[sp2[2]:]
        added.append(new)
    # ключи темы, которые используют импортированные классы (theme.accent3…), а в целевой теме их нет
    used = set()
    for cls in added:
        sp = widget_span(target, cls)
        if sp is not None:
            used |= set(re.findall(r"\btheme\.([A-Za-z_]\w*)", target[sp[0]:sp[1]]))
    src_theme, tgt_theme = theme_get(source), theme_get(target)
    missing = {k: src_theme[k] for k in used if k in src_theme and k not in tgt_theme}
    if missing:
        target = theme_set(target, missing)
    if need_defs:
        chunk = "".join(source[defs[n][0]:defs[n][1]] for n in sorted(need_defs, key=lambda n: defs[n][0]))
        first = re.search(r"(?m)^[ \t]*widget\s", mask(target))
        pos = first.start() if first else len(target)
        target = target[:pos] + chunk.rstrip("\n") + "\n\n" + target[pos:]
    return target, added


_SHAPE_KIND = {"circle": "circle", "ring": "ring", "star": "star", "rect": "rect", "square": "rect",
               "triangle": "triangle", "hex": "hex", "hexagon": "hex", "heart": "vector", "diamond": "polygon"}


def layers_to_blocks(ct: dict, store=None, W=1280, H=760) -> list[tuple]:
    """Слои темы из конструктора → [(ключ блока, geom, extra)]: картинки/GIF/видео, тексты, фигуры."""
    out = []
    m = float(min(W, H))
    for L in ct.get("layers") or []:
        if not L.get("visible", True):
            continue
        kind = L.get("kind")
        size = float(L.get("size", 0.25)) * m
        asp = float(L.get("aspect", 1.0) or 1.0)
        w_px, h_px = size, size / max(0.05, asp)
        cx, cy = float(L.get("x", 0.5)) * W, float(L.get("y", 0.5)) * H
        geom = {"x": max(0.0, (cx - w_px / 2) / W), "y": max(0.0, (cy - h_px / 2) / H),
                "w": min(1.0, w_px / W), "h": min(1.0, h_px / H)}
        z = "5" if L.get("z") == "front" else "-5"
        extra = {"z": z}
        op = float(L.get("opacity", 1.0))
        if kind == "media":
            src = L.get("src") or ""
            path = store.asset(ct, src) if store is not None else src
            if not path:
                continue
            key = "video" if path.lower().endswith(VIDEO_EXT) else "picture"
            extra["src"] = _lit(path)
            if key == "video":
                extra["dim"] = "0"
            elif op < 1:
                extra["opacity"] = fmt_num(op)
            out.append((key, geom, extra))
        elif kind == "text":
            extra.update({"label": _lit(L.get("text") or "ECHOES"), "color": _lit(L.get("color") or "#ffffff"),
                          "size": fmt_num(max(10.0, h_px * 0.7)), "align": '"center"',
                          "weight": "800" if L.get("bold", True) else "500"})
            if L.get("font"):
                extra["font"] = _lit(L["font"])
            out.append(("label", geom, extra))
        elif kind == "shape":
            k = _SHAPE_KIND.get(L.get("shape", "circle"), "circle")
            col = _lit(L.get("color") or "#ffffff")
            if k == "vector":
                out.append(("vector", geom, {**extra, "color": col}))
                continue
            extra.update({"kind": _lit(k), "color": col})
            if L.get("color2"):
                extra["color2"] = _lit(L["color2"])
            if k == "polygon":
                extra["sides"] = "4"
            if L.get("rot"):
                extra["spin"] = "0"
            out.append(("shape", geom, extra))
    return out


def import_theme_layers(src: str, theme_data: dict, store=None, W=1280, H=760, colors=True, background=True):
    """Тема из конструктора → в текущий скрипт: цвета, фон (картинка/видео) и слои."""
    ct = (theme_data or {}).get("ct") or {}
    if colors:
        src = theme_set(src, palette_of(theme_data))
    if background:
        media = background_media(theme_data, store)
        if media:
            key = "video" if media.lower().endswith(VIDEO_EXT) else "picture"
            extra = {"src": _lit(media), "z": "-9"}
            if key == "picture":
                extra["dim"] = fmt_num(float((ct.get("background") or {}).get("dim", 0.25)))
            src = add_block(src, key, {"x": 0, "y": 0, "w": 1, "h": 1}, extra)
    n = 0
    for key, geom, extra in layers_to_blocks(ct, store, W, H):
        src = add_block(src, key, geom, extra)
        n += 1
    return src, n


# ══════════════════════════════════════════════════════════════════════════ #
#  Описание свойств для понятного инспектора
# ══════════════════════════════════════════════════════════════════════════ #

_OPT_RE = re.compile(r"^\s*([A-Za-z_]\w*(?:\s*\|\s*[A-Za-z_]\w*)+)")
_RANGE_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*(?:…|\.\.\.?|-|–)\s*(-?\d+(?:\.\d+)?)")


def prop_meta(src: str, cls: str) -> dict:
    """{имя: {"default": текст, "comment": пояснение, "options": [варианты] | None, "range": (lo, hi) | None}}
    из строк «prop имя = значение  // пояснение» внутри widget cls { }."""
    sp = widget_span(src, cls)
    if sp is None:
        return {}
    out = {}
    for line in src[sp[0]:sp[1]].splitlines():
        mt = re.match(r"\s*prop\s+([A-Za-z_]\w*)\s*=\s*(.*)$", line)
        if not mt:
            continue
        name, rest = mt.group(1), mt.group(2)
        ci, q = -1, None                                   # «//» вне строк
        for i, ch in enumerate(rest):
            if q:
                if ch == q and rest[i - 1] != "\\":
                    q = None
            elif ch in "\"'":
                q = ch
            elif rest.startswith("//", i):
                ci = i
                break
        default, comment = (rest[:ci].strip(), rest[ci + 2:].strip()) if ci >= 0 else (rest.strip(), "")
        opts = None
        mo = _OPT_RE.match(comment)
        if mo:
            opts = [o.strip() for o in mo.group(1).split("|")]
        rng = None
        mr = _RANGE_RE.search(comment)
        if mr:
            lo, hi = float(mr.group(1)), float(mr.group(2))
            if hi > lo:
                rng = (lo, hi)
        out[name] = {"default": default, "comment": comment, "options": opts, "range": rng}
    return out


def block_of(cls: str):
    """Блок каталога, к которому относится класс (Shape, Shape2 … → «Фигура»)."""
    base = re.sub(r"\d+$", "", cls)
    for b in BLOCKS.values():
        if b["cls"] == base:
            return b
    return None
