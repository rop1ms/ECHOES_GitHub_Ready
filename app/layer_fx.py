# layer_fx.py
"""
Единый движок конструктора тем: LOOM (EchoScript) живёт внутри слоёв.

Раньше было два мира: конструктор тем (картинки, текст, фигуры поверх плеера) и
отдельный режим LOOM (скрипты EchoScript со своим холстом и студией). Теперь это
один конструктор, а у слоя есть всё, что умел LOOM:

  * слой-виджет (kind "script") — любой виджет EchoScript: кнопки, перемотка,
    громкость, пластинка, обложка, текст песни, список треков, спектры, свои
    шейдер-подобные эффекты… Слой можно двигать, тянуть, крутить и наклонять в 3D,
    как картинку, а код виджета править прямо в конструкторе;
  * поведение (поле "behavior") — обработчики EchoScript у ЛЮБОГО слоя (картинка,
    видео, GIF, текст, фигура, эффект, виджет): on frame(dt) { rot += … },
    on beat(p) { … }, on click { … } — своя логика, физика и реакция на звук;
  * эффекты из встроенных тем (kind "effect") — настоящие MilkDrop из Winamp,
    жидкость из Fluid, живые обои — тоже слоями, их можно складывать в стопку;
  * предустановки (PRESETS) — одним нажатием скопировать эффекты Fluid, Winamp,
    Echoes Music, LOOM и любых своих тем/скриптов в текущую тему;
  * перевод старых тем LOOM (*.echo) в темы конструктора (script_theme).

  ScriptHost  — один анализ звука и один снимок плеера на кадр для всех скриптов
                темы; компиляция по требованию, кэш по исходнику.
  LayerInput  — мышь для слоёв: клики, перетаскивание, колесо, наведение.
"""
from __future__ import annotations

import copy
import json
import math
import random
import re
import time

import numpy as np
from PyQt6.QtCore import QEvent, QObject, QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QWidget

import dsl_layout as DL
import theme_kit as tk
from dsl_lang import EchoError

REF_W, REF_H = 1280.0, 760.0                      # окно, под которое заданы доли в каталоге виджетов
MOUSE_EVENTS = ("press", "release", "click", "drag", "move", "wheel", "enter", "leave", "dblclick")
# слой ловит мышь/клавиши: свои обработчики, вложенные виджеты (у них бывают клики), клавиатура, мышь
_INTERACTIVE_RE = re.compile(r"\bon\s+(press|release|click|drag|move|wheel|enter|leave|dblclick|key|keyup)\b|"
                             r"\bchild\s+[A-Za-z_]\w*\s*=|\bspawn\s*\(|\bkey_down\s*\(|\bcapture_keys\s*\(|"
                             r"\bmouse_in\s*\(|\bon_(click|press|wheel|drag|key)\b")


# ══════════════════════════════════════════════════════════════════════════ #
#  Каталог виджетов (бывшие блоки LOOM + новые)
# ══════════════════════════════════════════════════════════════════════════ #

LIB: dict[str, dict] = {}
GROUP_ORDER = ["Плеер", "Текст", "Визуализаторы", "Из тем", "Фигуры", "Фон"]


def _lib(key, title, group, cls, code, props=None, geom=(0.4, 0.4, 0.2, 0.2), hint="", z="front", clip=True,
         hidden=False):
    gx, gy, gw, gh = geom
    LIB[key] = {"key": key, "title": title, "group": group, "cls": cls, "code": code.strip("\n"),
                "props": dict(props or {}), "geom": (gx, gy, gw, gh), "hint": hint, "z": z, "clip": clip,
                "hidden": hidden}


for _b in DL.BLOCKS.values():
    _spec = dict(_b.get("spec") or {})
    _z = "back" if str(_spec.get("z", "0")).strip().startswith("-") else "front"
    _clip = str(_spec.get("clip", "true")).strip() != "false"
    _lib(_b["key"], _b["title"], _b["group"], _b["cls"], _b["code"],
         {k: v for k, v in _spec.items() if k not in ("z", "clip", "cache")}, _b["geom"], _b["hint"], _z, _clip)

_lib("vinyl", "Пластинка с тонармом", "Плеер", "Vinyl", """
widget Vinyl {          // блок: vinyl
  prop label = true     // обложка трека в центре
  prop arm = true       // тонарм
  prop rpm = 33         // оборотов в минуту
  prop color = "#0c0c10"
  prop shine = theme.accent
  state angle = 0
  state speed = 0
  state arm_a = 0
  on frame(dt) {
    speed = approach(speed, player.playing ? rpm * 6 : 0, 2.5, dt)
    angle += speed * dt
    arm_a = approach(arm_a, track.loaded and player.playing ? 20 + track.progress * 16 : 0, 3, dt)
  }
  on click { toggle() }
  on draw {
    let s = min(w, h)
    let cx = w / 2 - (arm ? s * 0.07 : 0)
    let cy = h / 2
    let R = s * (arm ? 0.43 : 0.48)
    nostroke()
    blend("add")
    glow(cx, cy, R * 1.3, with_alpha(shine, 0.16 + audio.bass * 0.3))
    blend("normal")
    fill(color)
    circle(cx, cy, R)
    nofill()
    for i in 0..14 {
      stroke(with_alpha("#ffffff", i % 3 == 0 ? 0.07 : 0.035), 1)
      circle(cx, cy, R * (0.42 + i * 0.04))
    }
    save()
    translate(cx, cy)
    rotate(angle)
    for k in 0..4 {
      stroke(with_alpha("#ffffff", 0.05 - k * 0.01), 1.5)
      arc(0, 0, R * (0.6 + k * 0.08), -24 + k * 3, 24 - k * 3)
      arc(0, 0, R * (0.6 + k * 0.08), 156 + k * 3, 204 - k * 3)
    }
    nostroke()
    let lr = R * 0.36
    if label {
      clip(-lr, -lr, lr * 2, lr * 2, lr)
      if not image(track.cover, -lr, -lr, lr * 2, lr * 2) {
        fill(radial(0, 0, lr, [theme.accent, theme.accent2]))
        circle(0, 0, lr)
      }
    }
    restore()
    fill(theme.bg)
    circle(cx, cy, max(2, R * 0.035))
    if arm {
      let px = cx + R * 1.04
      let py = cy - R * 0.9
      save()
      translate(px, py)
      rotate(arm_a)
      stroke("#c9ccd6", max(2, s * 0.012))
      line(0, 0, -R * 0.1, R * 1.12)
      nostroke()
      fill("#d9dce6")
      rect(-R * 0.1 - s * 0.022, R * 1.1, s * 0.045, s * 0.075, 3)
      restore()
      fill("#2a2c36")
      circle(px, py, s * 0.035)
      fill("#5b5f70")
      circle(px, py, s * 0.015)
    }
  }
}""", geom=(0.3, 0.12, 0.36, 0.6), hint="Вращающаяся пластинка с обложкой и тонармом; клик — пауза/воспроизведение",
     hidden=True)   # в библиотеке вместо неё — настоящий винил плеера (vinyl.py); код остаётся для старых тем

_lib("lyrics", "Текст песни", "Текст", "Lyrics", """
widget Lyrics {         // блок: lyrics
  prop size = 26
  prop align = "center" // left | center | right
  prop around = 1       // сколько строк показывать до и после текущей
  prop color = theme.text
  prop dim = theme.muted
  state shown = -1
  state fade = 1
  on frame(dt) {
    if lyrics.index != shown {
      shown = lyrics.index
      fade = 0
    }
    fade = approach(fade, 1, 7, dt)
  }
  on draw {
    let tx = align == "center" ? w / 2 : (align == "right" ? w : 0)
    if lyrics.count == 0 {
      fill(dim)
      text(track.loaded ? "текста нет" : "", tx, h / 2, size * 0.6, align: align, maxw: w)
      return
    }
    let i = max(0, lyrics.index)
    for k in (0 - around)..(around + 1) {
      let j = i + k
      if j >= 0 and j < lyrics.count {
        let cur = k == 0
        fill(cur ? with_alpha(color, 0.35 + fade * 0.65) : with_alpha(dim, 0.75))
        text(lyrics.lines[j], tx, h / 2 + k * size * 1.45 + (cur ? (1 - fade) * 10 : 0), cur ? size : size * 0.6,
             align: align, weight: cur ? 800 : 500, maxw: w)
      }
    }
  }
}""", geom=(0.15, 0.4, 0.7, 0.22), hint="Текущая строка текста (с таймингами) и соседние")

_lib("winamp_spectrum", "Спектр Winamp", "Из тем", "WinampSpectrum", """
widget WinampSpectrum { // блок: winamp_spectrum
  prop bars = 19
  prop segs = 16
  prop peaks = true
  prop bg = "#000000"
  state lv = []
  state pk = []
  state pv = []
  on frame(dt) {
    if len(lv) != bars {
      lv = fill_list(bars, 0)
      pk = fill_list(bars, 0)
      pv = fill_list(bars, 0)
    }
    for i in 0..bars {
      let v = audio.fft[floor(i * 60 / bars)]
      lv[i] = approach(lv[i], v, v > lv[i] ? 40 : 6, dt)
      if lv[i] >= pk[i] {
        pk[i] = lv[i]
        pv[i] = 0
      } else {
        pv[i] += dt * 1.8
        pk[i] = max(0, pk[i] - pv[i] * dt)
      }
    }
  }
  on draw {
    nostroke()
    fill(bg)
    rect(0, 0, w, h)
    if len(lv) == bars {
      let bw = w / bars
      let sh = h / segs
      for i in 0..bars {
        let n = floor(lv[i] * segs)
        for s in 0..n {
          let t = s / max(1, segs - 1)
          fill(t < 0.5 ? mix("#00c000", "#d8d800", t * 2) : mix("#d8d800", "#e00000", (t - 0.5) * 2))
          rect(i * bw + 1, h - (s + 1) * sh + 1, bw - 2, sh - 2)
        }
        if peaks {
          fill("#c8c8c8")
          rect(i * bw + 1, h - pk[i] * h - 2, bw - 2, 2)
        }
      }
    }
  }
}""", geom=(0.05, 0.05, 0.25, 0.14), hint="Сегментный спектр с пиками — как в Winamp 2")

_lib("echoes_wave", "«Моя волна»", "Из тем", "EchoesWave", """
widget EchoesWave {     // блок: echoes_wave
  prop color = theme.accent
  prop color2 = theme.accent2
  prop rings = 5
  state t = 0
  state lv = 0
  on frame(dt) {
    lv = approach(lv, audio.level, 6, dt)
    t += dt * (0.35 + lv * 1.6)
  }
  on click { toggle() }
  on draw {
    let cx = w / 2
    let cy = h / 2
    let R = min(w, h) * 0.3
    blend("add")
    glow(cx, cy, R * (1.7 + lv * 0.6), with_alpha(color, 0.22 + lv * 0.3))
    nofill()
    for k in 0..rings {
      let pts = []
      let rr = R * (0.55 + k * 0.13) * (1 + lv * 0.25)
      for i in 0..64 {
        let a = i * TAU / 64
        let n = noise(cos(a) * 1.2 + t + k, sin(a) * 1.2 + t * 0.7, k * 0.3)
        let r = rr * (1 + n * (0.08 + lv * 0.22))
        push(pts, cx + cos(a) * r, cy + sin(a) * r)
      }
      stroke(with_alpha(mix(color, color2, k / max(1, rings - 1)), 0.6 - k * 0.08), 2 + lv * 2)
      curve(pts, true)
    }
    blend("normal")
  }
}""", geom=(0.3, 0.15, 0.4, 0.62), hint="Дышащие кольца «Моей волны» из Echoes Music; клик — пауза")

_lib("action_button", "Кнопка (любое действие)", "Плеер", "ActionButton", """
widget ActionButton {   // блок: action_button
  prop action = "play"  // play | prev | next | shuffle | artist | repeat | lyrics | queue | cover | clip | music | builder | themes | fullscreen | export | files | download | profile | mute
  prop style = "round"  // round | winamp | glass | flat
  prop label = ""       // свой текст вместо значка
  prop color = theme.accent
  state flash = 0
  on click(x, y, b = 1) {
    if action == "play" { toggle() } elif action == "prev" { prev() } elif action == "next" { next() }
    elif action == "shuffle" { toggle_shuffle() } elif action == "repeat" { cycle_repeat() }
    elif action == "artist" { if b == 2 { artist_menu() } else { artist_shuffle() } }
    elif action == "lyrics" { lyrics_mode() } elif action == "queue" { show_queue() }
    elif action == "cover" { cover_mode() } elif action == "clip" { clip_mode() } elif action == "music" { music_hub() }
    elif action == "builder" { theme_builder() } elif action == "themes" { theme_menu() }
    elif action == "fullscreen" { fullscreen() } elif action == "export" { export_fx() }
    elif action == "files" { add_files() } elif action == "download" { downloader() }
    elif action == "profile" { profile() } else { mute() }
    flash = 1
  }
  on frame(dt) { flash = approach(flash, 0, 6, dt) }
  on draw {
    let on_ = (action == "shuffle" and player.shuffle) or (action == "repeat" and player.repeat > 0)
    let t = label
    if t == "" {
      if action == "play" { t = player.playing ? "❚❚" : "▶" } elif action == "prev" { t = "◀◀" }
      elif action == "next" { t = "▶▶" } elif action == "shuffle" { t = "⤮" } elif action == "artist" { t = "⤮ Артист" }
      elif action == "repeat" { t = player.repeat == 2 ? "↻1" : "↻" } elif action == "lyrics" { t = "Текст" }
      elif action == "queue" { t = "Очередь" } elif action == "cover" { t = "Обложка" } elif action == "clip" { t = "Клип" }
      elif action == "music" { t = "Музыка" } elif action == "builder" { t = "✦" } elif action == "themes" { t = "Темы" }
      elif action == "fullscreen" { t = "⛶" } elif action == "export" { t = "FX" } elif action == "files" { t = "+ Файлы" }
      elif action == "download" { t = "Скачать" } elif action == "profile" { t = "Профиль" }
      else { t = player.volume > 0 ? "Звук" : "Тихо" }
    }
    let ts = min(h * 0.42, w / max(2, len(t)) * 1.35)
    if style == "winamp" {
      let dn = pressed or on_
      nostroke()
      let top = dn ? "#8f8fa3" : (hover ? "#ececf4" : "#dcdce6")
      let bot = dn ? "#c4c4d2" : (hover ? "#a0a0b4" : "#8f8fa3")
      fill(linear(0, 0, 0, h, [top, bot]))
      rect(0, 0, w, h)
      stroke(dn ? "#3c3c4c" : "#f6f6fc", 1)
      line(0.5, 0.5, w - 0.5, 0.5)
      line(0.5, 0.5, 0.5, h - 0.5)
      stroke(dn ? "#f6f6fc" : "#3c3c4c", 1)
      line(0.5, h - 0.5, w - 0.5, h - 0.5)
      line(w - 0.5, 0.5, w - 0.5, h - 0.5)
      nostroke()
      fill(on_ ? "#007a00" : "#10101a")
      text(t, w / 2 + (dn ? 1 : 0), h / 2 + (dn ? 1 : 0), ts, align: "center", weight: 800)
    } elif style == "glass" {
      fill(with_alpha("#ffffff", pressed ? 0.24 : (hover ? 0.16 : 0.08)))
      stroke(with_alpha("#ffffff", 0.2), 1)
      rect(0.5, 0.5, w - 1, h - 1, min(w, h) * 0.3)
      nostroke()
      fill(on_ ? color : theme.text)
      text(t, w / 2, h / 2, ts, align: "center", weight: 700)
    } elif style == "flat" {
      nostroke()
      fill(on_ or hover ? color : theme.text)
      text(t, w / 2, h / 2, ts * (1 + flash * 0.1), align: "center", weight: 700)
    } else {
      let big = action == "play"
      let r = min(w, h) / 2 * (pressed ? 0.92 : 1) * (1 + flash * 0.05)
      nostroke()
      fill(big ? (hover ? mix(color, "#ffffff", 0.15) : color) : (hover ? "#ffffff24" : "#ffffff10"))
      circle(w / 2, h / 2, r)
      fill(big ? theme.bg : (on_ ? color : theme.text))
      text(t, w / 2, h / 2, min(ts, r * 0.9), align: "center", weight: 700)
    }
  }
}""", {"action": '"play"', "style": '"round"'}, geom=(0.45, 0.8, 0.07, 0.1),
     hint="Любая кнопка плеера (play, перемотка, шаффл по исполнителю, повтор, текст, очередь, обложка, темы, конструктор, "
          "полный экран, экспорт…) в стиле: круглая, Winamp, стекло или плоская")

_lib("glass", "Стеклянная плашка", "Фигуры", "GlassCard", """
widget GlassCard {      // блок: glass
  prop radius = 22
  prop tint = "#ffffff"
  prop opacity = 0.08
  prop border = true
  on draw {
    nostroke()
    fill(with_alpha(tint, opacity))
    rect(0, 0, w, h, radius)
    if border {
      nofill()
      stroke(with_alpha("#ffffff", 0.12), 1)
      rect(0.5, 0.5, w - 1, h - 1, radius)
    }
  }
}""", geom=(0.1, 0.1, 0.35, 0.4), hint="Полупрозрачная подложка для группы виджетов", z="back")

_lib("cover_shape", "Обложка любой формы", "Плеер", "CoverShape", """
widget CoverShape {     // блок: cover_shape
  prop shape = "circle"   // circle | rounded | square | squircle | ellipse | hexagon | octagon | pentagon | triangle | diamond | star | heart | blob | flower | wave | gear | drop | leaf | pill | shield | cross | ring | arch | cloud | moon | badge | parallelogram | trapezoid | ngon | svg | points
  prop sides = 6          // стороны, лучи, лепестки, зубцы 3…32
  prop radius = 28        // скругление (rounded) 0…200
  prop inner = 0.5        // глубина звезды, дырка кольца 0.05…0.95
  prop rotate = 0         // поворот формы, ° -180…180
  prop spin = 0           // вращение, °/с -360…360
  prop spin_paused = false // крутить и на паузе
  prop pulse = 0.12       // пульс от баса 0…0.5
  prop wobble = 0.18      // колыхание капли (blob) 0…0.5
  prop zoom = 1           // приближение картинки 1…3
  prop border = 0         // толщина контура 0…24
  prop border_color = theme.accent
  prop glow = true        // свечение вокруг
  prop shadow = 0.35      // тень 0…1
  prop d = ""             // свой контур SVG (для shape = svg)
  prop points = ""        // свои точки «x,y x,y …» в долях 0…1 (для shape = points)
  prop smooth = false     // сгладить свои точки
  prop src = ""           // своя картинка вместо обложки трека
  prop fallback = true    // градиент, когда обложки нет
  state angle = 0
  state kick = 0
  on beat(p) { kick = p }
  on frame(dt) {
    kick = approach(kick, 0, 7, dt)
    if spin != 0 and (player.playing or spin_paused) { angle += spin * dt }
  }
  // контур формы размером s по центру виджета — его можно взять снаружи: cover.outline(100)
  fn outline(s) {
    let many = shape == "star" or shape == "badge"
    let o = {sides: sides, points: many ? sides : points, radius: radius * s / max(1, min(w, h)), inner: inner,
             petals: sides, teeth: sides, waves: sides, wobble: wobble, t: time * 0.6, d: d, smooth: smooth,
             rot: rotate + angle}
    return shape(shape, (w - s) / 2, (h - s) / 2, s, s, o)
  }
  on draw {
    let s = min(w, h) * 0.92 / (1 + pulse) * (1 + (audio.bass * 0.6 + kick * 0.4) * pulse)
    let p = outline(s)
    nostroke()
    if glow {
      blend("add")
      glow(w / 2, h / 2, s * 0.64, with_alpha(theme.accent, 0.2 + audio.bass * 0.25))
      blend("normal")
    }
    if shadow > 0 {
      fill(with_alpha("#000000", shadow * 0.55))
      draw_path(p.moved(0, s * 0.035))
    }
    save()
    clip_path(p)
    translate(w / 2, h / 2)
    rotate(angle)
    scale(zoom)
    let pic = src != "" ? src : track.cover
    if not image(pic, -s / 2, -s / 2, s, s) {
      if fallback {
        fill(linear(-s / 2, -s / 2, s / 2, s / 2, [theme.accent, theme.accent2]))
        rect(-s / 2, -s / 2, s, s)
      }
    }
    restore()
    if border > 0 {
      nofill()
      stroke(border_color, border)
      draw_path(p)
    }
  }
}""", {"shape": '"circle"'}, geom=(0.36, 0.2, 0.28, 0.45),
     hint="Обложка играющего трека любой формы: круг, сердце, звезда, капля, шестерёнка, цветок, свой SVG-контур "
          "или свои точки; вращение, пульс от баса, контур, свечение")

_lib("library", "Медиатека", "Плеер", "Library", """
widget Library {        // блок: library
  prop row = 40           // высота строки 24…80
  prop covers = true      // маленькие обложки
  prop show_search = true // строка поиска сверху (щелчок — ввести запрос)
  prop artist_btn = true  // кнопка «Шаффл по исполнителю» справа от поиска
  prop color = theme.accent
  state scroll = 0
  state sel = -1
  state over = -1
  state over_art = false
  state query = ""
  state items = []
  state key = ""
  // методы — ими управляют снаружи (iPod, кнопки, клавиши): lib.move_sel(1), lib.open(), lib.set_filter("rock")
  fn refresh() {
    items = query == "" ? library() : search_tracks(query, 5000)
    key = query + "|" + len(library())
    clamp_scroll()
  }
  fn top() { return show_search ? 34 : 0 }
  fn rows() { return max(1, floor((h - top()) / row)) }
  fn clamp_scroll() { scroll = clamp(scroll, 0, max(0, len(items) - rows())) }
  fn scroll_by(n) {
    scroll += n
    clamp_scroll()
  }
  fn select(i) {
    if len(items) == 0 { return }
    sel = clamp(i, 0, len(items) - 1)
    if sel < scroll { scroll = sel }
    if sel >= scroll + rows() { scroll = sel - rows() + 1 }
    clamp_scroll()
  }
  fn move_sel(d) { select((sel < 0 ? floor(scroll) : sel) + d) }
  fn selected() { return sel >= 0 and sel < len(items) ? items[sel] : nil }
  fn open(i = -1) {
    let k = i >= 0 ? i : sel
    if k >= 0 and k < len(items) {
      play_track(items[k].index)
      emit("play", items[k])
    }
  }
  fn art_w() { return show_search and artist_btn ? min(118, w * 0.42) : 0 }
  fn in_art(x, y) { return art_w() > 0 and y < top() and x > w - art_w() - 4 }
  // шаффл по исполнителю: выделенного трека, а если ничего не выделено — того, что играет
  fn artist_shuffle_sel() {
    let it = selected()
    if it != nil and it.artist != "" { artist_shuffle(it.artist) } else { artist_shuffle() }
  }
  fn set_filter(q) {
    query = q
    scroll = 0
    sel = -1
    refresh()
  }
  on init { refresh() }
  on frame(dt) { if key != query + "|" + len(library()) { refresh() } }
  on wheel(d) { scroll_by(-d * 3) }
  on move(x, y) {
    over = y < top() ? -1 : floor((y - top()) / row) + floor(scroll)
    over_art = in_art(x, y)
  }
  on leave {
    over = -1
    over_art = false
  }
  on click(x, y, b = 1) {
    if in_art(x, y) {
      if b == 2 { artist_menu() } else { artist_shuffle_sel() }
      return
    }
    if y < top() {
      prompt("Поиск в медиатеке", "Название, исполнитель или альбом:", query, fn(q) { if q != nil { set_filter(q) } })
      return
    }
    let i = floor((y - top()) / row) + floor(scroll)
    if i < len(items) {
      select(i)
      if b == 2 { track_menu(items[i].path) } else { open(i) }
    }
  }
  on draw {
    let t0 = top()
    nostroke()
    if show_search {
      let aw = art_w()
      let sw = w - 8 - (aw > 0 ? aw + 6 : 0)
      fill(with_alpha("#ffffff", 0.08))
      rect(4, 4, sw, t0 - 8, (t0 - 8) / 2)
      fill(query == "" ? theme.muted : theme.text)
      text(query == "" ? "Поиск…" : query, 16, t0 / 2, 12, maxw: sw - 60)
      fill(theme.muted)
      text(len(items), 4 + sw - 10, t0 / 2, 11, align: "right")
      if aw > 0 {
        fill(over_art ? color : with_alpha(color, 0.22))
        rect(w - aw - 4, 4, aw, t0 - 8, (t0 - 8) / 2)
        fill(over_art ? theme.bg : theme.text)
        text("⤮ Исполнитель", w - aw / 2 - 4, t0 / 2, 11, align: "center", weight: 700, maxw: aw - 10)
      }
    }
    save()
    clip(0, t0, w, h - t0)
    let first = floor(scroll)
    let n = min(len(items) - first, ceil((h - t0) / row) + 1)
    let cur = track.path
    let fs = max(10, row * 0.32)
    for k in 0..n {
      let i = first + k
      let it = items[i]
      let ry = t0 + k * row
      if i == sel {
        fill(with_alpha(color, 0.24))
        rect(2, ry + 2, w - 4, row - 4, 8)
      } elif i == over {
        fill("#ffffff10")
        rect(2, ry + 2, w - 4, row - 4, 8)
      }
      let tx = 12
      if covers {
        let cs = row - 10
        if not thumb(it.cover, 8, ry + 5, cs, cs, 5) {
          fill(with_alpha(color, 0.25))
          rect(8, ry + 5, cs, cs, 5)
        }
        tx = 8 + cs + 10
      }
      fill(it.path == cur ? color : theme.text)
      text(it.title, tx, ry + row * 0.36, fs, weight: 600, maxw: w - tx - 54)
      fill(theme.muted)
      text(it.artist, tx, ry + row * 0.7, fs * 0.82, maxw: w - tx - 54)
      text(fmt_time(it.duration), w - 10, ry + row / 2, fs * 0.82, align: "right")
    }
    restore()
    if len(items) == 0 {
      fill(theme.muted)
      text(query == "" ? "Медиатека пуста" : "Ничего не найдено", w / 2, t0 + 30, 13, align: "center")
    }
    let vis = rows()
    if len(items) > vis {
      let th = max(20, (h - t0) * vis / len(items))
      let ty = t0 + (h - t0 - th) * scroll / max(1, len(items) - vis)
      fill(with_alpha(theme.text, 0.2))
      rect(w - 4, ty, 3, th, 2)
    }
  }
}""", geom=(0.6, 0.08, 0.36, 0.7),
     hint="Вся медиатека с обложками и поиском: колесо — прокрутка, клик — включить; кнопка «Исполнитель» — "
          "шаффл по исполнителю (правый клик — выбрать). Управляется из других виджетов: move_sel, open, "
          "set_filter, artist_shuffle_sel")

_lib("ipod", "Плеер в стиле iPod", "Плеер", "IPod", """
widget IPod {           // блок: ipod
  prop body = "#ececf1"       // корпус
  prop body2 = "#c4c5ce"      // низ корпуса
  prop screen_color = "#0d0e13"
  prop wheel_color = "#fbfbfd"
  prop center_color = "#dcdde4"
  prop label_color = "#9a9aa6"
  prop accent = "#3d9bff"
  prop clicks = true          // щелчки колеса
  // вложенные виджеты: медиатека и обложка из каталога, их можно настроить как обычные блоки
  child lib = Library { row: 30, covers: true, show_search: false, color: "#3d9bff" }
  child art = CoverShape { shape: "rounded", radius: 12, pulse: 0.03, glow: false, shadow: 0 }
  state mode = "music"
  state grab = nil
  state acc = 0
  state fx = 0
  fn screen() { return {x: w * 0.08, y: h * 0.05, w: w * 0.84, h: h * 0.43} }
  fn wheel() { return {x: w / 2, y: h * 0.53 + h * 0.235, r: min(w * 0.37, h * 0.2)} }
  fn turn(d) {
    if mode == "music" { lib.move_sel(d) } else { set_volume(clamp(player.volume + d * 0.04)) }
    if clicks { beep(2200, 0.006, 0.06, "square") }
    fx = 1
  }
  on frame(dt) {
    lib.visible = mode == "music"
    art.visible = mode == "now"
    fx = approach(fx, 0, 10, dt)
  }
  on message(name, data) { if name == "play" { mode = "now" } }
  on press(x, y) {
    let c = wheel()
    let d = dist(x, y, c.x, c.y)
    grab = (d <= c.r and d > c.r * 0.38) ? angle_to(c.x, c.y, x, y) : nil
    acc = 0
  }
  on drag(x, y) {
    if grab == nil { return }
    let c = wheel()
    let a = angle_to(c.x, c.y, x, y)
    acc += angle_diff(grab, a)
    grab = a
    while acc >= 16 {
      acc -= 16
      turn(1)
    }
    while acc <= -16 {
      acc += 16
      turn(-1)
    }
  }
  on release { grab = nil }
  on wheel(d) { turn(-d) }
  on click(x, y) {
    let c = wheel()
    let d = dist(x, y, c.x, c.y)
    if d <= c.r * 0.38 {
      if mode == "music" { lib.open() } else { toggle() }
    } elif d <= c.r {
      let a = angle_to(c.x, c.y, x, y)
      if a > -135 and a < -45 { mode = mode == "music" ? "now" : "music" }
      elif a >= -45 and a <= 45 { next() }
      elif a > 45 and a < 135 { toggle() }
      else { prev() }
    }
  }
  on draw {
    let s = screen()
    nostroke()
    fill(linear(0, 0, 0, h, [body, body2]))
    rect(0, 0, w, h, min(w, h) * 0.12)
    fill(with_alpha("#ffffff", 0.5))
    rect(2, 2, w - 4, h * 0.3, min(w, h) * 0.12)
    fill(linear(0, 0, 0, h, [body, body2]))
    rect(2, 6, w - 4, h - 8, min(w, h) * 0.12)
    fill("#00000040")
    rect(s.x - 3, s.y - 3, s.w + 6, s.h + 6, 10)
    fill(screen_color)
    rect(s.x, s.y, s.w, s.h, 8)
    save()
    clip(s.x, s.y, s.w, s.h, 8)
    fill(linear(0, s.y, 0, s.y + 22, ["#f6f6fa", "#c8c9d3"]))
    rect(s.x, s.y, s.w, 22)
    fill("#1c1c24")
    text(mode == "music" ? "Музыка" : "Сейчас играет", s.x + s.w / 2, s.y + 11, 11, align: "center", weight: 700)
    text(player.playing ? "▶" : "❚❚", s.x + 10, s.y + 11, 9)
    fill("#1c1c24")
    rect(s.x + s.w - 26, s.y + 7, 16, 8, 2)
    fill("#33c26b")
    rect(s.x + s.w - 25, s.y + 8, 14 * clamp(0.4 + player.volume * 0.6), 6, 1)
    if mode == "music" {
      draw_child(lib, s.x, s.y + 22, s.w, s.h - 22)
    } else {
      let cs = min(s.w * 0.42, s.h - 56)
      draw_child(art, s.x + 10, s.y + 32, cs, cs)
      let tx = s.x + cs + 22
      fill("#f4f4f8")
      text(track.loaded ? track.title : "Ничего не играет", tx, s.y + 46, 13, weight: 700, maxw: s.w - cs - 32)
      fill("#a6a6b4")
      text(track.artist, tx, s.y + 66, 11, maxw: s.w - cs - 32)
      text(track.album, tx, s.y + 84, 10, maxw: s.w - cs - 32)
      let py = s.y + s.h - 24
      fill("#ffffff22")
      rect(s.x + 12, py, s.w - 24, 6, 3)
      fill(accent)
      rect(s.x + 12, py, (s.w - 24) * track.progress, 6, 3)
      fill("#c8c8d4")
      text(fmt_time(track.position), s.x + 12, py + 14, 9)
      text("-" + fmt_time(track.duration - track.position), s.x + s.w - 12, py + 14, 9, align: "right")
    }
    restore()
    let c = wheel()
    fill("#00000014")
    circle(c.x, c.y + 2, c.r + 1)
    fill(wheel_color)
    circle(c.x, c.y, c.r)
    if grab != nil {
      fill(with_alpha(accent, 0.12 + fx * 0.1))
      circle(c.x, c.y, c.r)
    }
    fill(label_color)
    text("MENU", c.x, c.y - c.r * 0.72, max(9, c.r * 0.14), align: "center", weight: 800)
    text("▶▶", c.x + c.r * 0.72, c.y, max(9, c.r * 0.13), align: "center")
    text("◀◀", c.x - c.r * 0.72, c.y, max(9, c.r * 0.13), align: "center")
    text("▶ ❚❚", c.x, c.y + c.r * 0.72, max(9, c.r * 0.12), align: "center")
    fill(linear(0, c.y - c.r * 0.38, 0, c.y + c.r * 0.38, [center_color, mix(center_color, "#000000", 0.08)]))
    stroke("#00000020", 1)
    circle(c.x, c.y, c.r * 0.38)
  }
}""", geom=(0.38, 0.1, 0.24, 0.78),
     hint="iPod: внутри — настоящая медиатека и обложка (вложенные виджеты). Колесо крутится мышью, "
          "MENU — экран, центр — включить/пауза, края — перемотка и пауза")


def lib_groups() -> list[tuple[str, list[dict]]]:
    out = {}
    for b in LIB.values():
        if b.get("hidden"):
            continue
        out.setdefault(b["group"], []).append(b)
    order = GROUP_ORDER + [g for g in out if g not in GROUP_ORDER]
    return [(g, out[g]) for g in order if g in out]


def _geom_layer(gx, gy, gw, gh) -> dict:
    """Доли окна (как в LOOM) → положение/размер слоя, который тянется вместе с окном."""
    gw, gh = max(0.005, gw), max(0.005, gh)
    return {"x": round(gx + gw / 2, 4), "y": round(gy + gh / 2, 4), "size": round(gw, 4),
            "aspect": round(gw / gh, 4), "box": "window", "stretch_x": 1.0, "stretch_y": 1.0}


def widget_layer(key: str, pos=None, **over) -> dict:
    """Новый слой-виджет из каталога."""
    b = LIB[key]
    g = _geom_layer(*b["geom"])
    if pos is not None:
        g["x"], g["y"] = pos
    L = tk.new_layer("script", name=b["title"], script=b["code"], widget=b["cls"], props=dict(b["props"]),
                     z=b["z"], clip=b["clip"], **g)
    L.update(over)
    return L


def effect_layer(effect: str, **over) -> dict:
    title = dict(tk.EFFECTS).get(effect, effect)
    L = tk.new_layer("effect", name=title, effect=effect, z="back", x=0.5, y=0.5, size=1.0, aspect=1.0,
                     box="window", opaque=effect in ("milkdrop", "fluid"))
    L.update(over)
    return L


# ══════════════════════════════════════════════════════════════════════════ #
#  Скрипты слоёв
# ══════════════════════════════════════════════════════════════════════════ #

BEHAVIOR_FIELDS = {"dx": 0.0, "dy": 0.0, "rot": 0.0, "scale": 1.0, "sx": 1.0, "sy": 1.0, "opacity": 1.0,
                   "tilt_x": 0.0, "tilt_y": 0.0, "hidden": False, "tint": "", "label": ""}

BG_FIELDS = {"speed": 1.0, "zoom": 1.0, "dx": 0.0, "dy": 0.0, "dim": -1.0}

BG_TEMPLATE = """// Скрипт фона темы на EchoScript. Можно менять:
//   speed — скорость видео / GIF / живых обоев (1 — как в теме, 2 — вдвое быстрее, 0.5 — медленнее)
//   zoom  — приближение (1 — как в теме), dx, dy — сдвиг в пикселях,
//   dim   — затемнение 0…1 (-1 — как в теме).
// Видно: audio.bass / mid / high / level / fft / beat, track, player, time, mouse.
state kick = 0
on beat(p) { kick = 1 }
on frame(dt) {
  kick = approach(kick, 0, 4, dt)
  speed = 1 + kick * 1.5
  zoom = 1 + audio.bass * 0.05
}
"""

BG_SNIPPETS = [
    ("Видео быстрее на бит", "state kick = 0\non beat(p) { kick = 1 }\non frame(dt) {\n"
                             "  kick = approach(kick, 0, 4, dt)\n  speed = 1 + kick * 1.5\n}"),
    ("Скорость от громкости", "on frame(dt) { speed = 0.4 + audio.level * 1.8 }"),
    ("Замедление на паузе", "on frame(dt) { speed = player.playing ? 1 : 0.25 }"),
    ("Зум от баса", "on frame(dt) { zoom = 1 + audio.bass * 0.08 }"),
    ("Тряска на бит", "state k = 0\non beat(p) { k = p }\non frame(dt) {\n  k = approach(k, 0, 8, dt)\n"
                      "  dx = (rand() - 0.5) * 30 * k\n  dy = (rand() - 0.5) * 30 * k\n}"),
    ("Медленное покачивание", "on frame(dt) {\n  dx = sin(time * 0.3) * 20\n  dy = cos(time * 0.23) * 12\n"
                              "  zoom = 1.05\n}"),
    ("Темнее на паузе", "on frame(dt) { dim = player.playing ? -1 : 0.7 }"),
    ("Вспышка на бит (светлее)", "state f = 0\non beat(p) { f = p }\non frame(dt) {\n"
                                 "  f = approach(f, 0, 6, dt)\n  dim = max(0, 0.3 - f * 0.3)\n}"),
]

BEHAVIOR_TEMPLATE = """// Поведение слоя на EchoScript. Можно менять: dx, dy (сдвиг, px), rot (поворот, °),
// scale / sx / sy (масштаб), opacity, tilt_x / tilt_y (3D-наклон, °), hidden, tint (цвет), label (текст).
// Видно: w, h (размер слоя), audio.bass / mid / high / level / fft[0..63] / beat, track, player, time.
state vy = 0
on frame(dt) {
  scale = 1 + audio.bass * 0.25
  rot += dt * (10 + audio.mid * 90)
}
on beat(p) { vy = -260 * p }
on click { toggle() }
"""

BEHAVIOR_SNIPPETS = [
    ("Пульс от баса", "on frame(dt) { scale = 1 + audio.bass * 0.3 }"),
    ("Вращение под середину", "on frame(dt) { rot += dt * (15 + audio.mid * 120) }"),
    ("Прыжок на бит (физика)", "state vy = 0\non beat(p) { vy -= 420 * p }\n"
                              "on frame(dt) {\n  vy += 1400 * dt\n  dy += vy * dt\n"
                              "  if dy > 0 { dy = 0\n    vy = -vy * 0.35 }\n}"),
    ("Пружина к мыши", "state vx = 0\nstate vy = 0\non frame(dt) {\n  let s = spring(dx, vx, (mouse.x - W / 2) * 0.05, 90, 10, dt)\n"
                       "  dx = s[0]\n  vx = s[1]\n  let t = spring(dy, vy, (mouse.y - H / 2) * 0.05, 90, 10, dt)\n"
                       "  dy = t[0]\n  vy = t[1]\n}"),
    ("3D-покачивание", "on frame(dt) {\n  tilt_y = sin(time * 1.4) * 25\n  tilt_x = cos(time * 1.1) * 12\n}"),
    ("Мигает на бит", "state f = 0\non beat(p) { f = 1 }\non frame(dt) {\n  f = approach(f, 0, 5, dt)\n"
                      "  opacity = 0.45 + f * 0.55\n}"),
    ("Цвет по громкости", "on frame(dt) { tint = hex(hsv(200 + audio.level * 160, 0.8, 1)) }"),
    ("Название трека", "on frame(dt) { label = track.loaded ? track.title : \"ECHOES\" }"),
    ("Клик — пауза", "on click { toggle() }"),
    ("Колесо — громкость", "on wheel(d) { set_volume(clamp(player.volume + d * 0.05)) }"),
]


WIDGET_SNIPPETS = [
    ("Вложенный виджет (child)", "child list = Library { x: 0, y: 0.15, w: 1, h: 0.85, row: 34 }"),
    ("Нарисовать вложенный виджет здесь (в on draw)", "draw_child(list, 10, 40, w - 20, h - 50)"),
    ("Создать виджет кодом (spawn)", "on click(x, y) {\n  let b = spawn(\"CoverShape\", {x: x - 40, y: y - 40, w: 80, "
                                     "h: 80, shape: \"star\"})\n  after(2, fn() { kill(b) })\n}"),
    ("Сообщение родителю и ответ", "on click { emit(\"выбрано\", 1) }\n// у родителя:\n"
                                   "on message(name, data, from) { log(name, data) }"),
    ("Сообщение всем слоям (post)", "on beat(p) { post(\"удар\", p) }\n// в любом другом виджете:\n"
                                    "on message(name, data) { if name == \"удар\" { log(\"бум\", data) } }"),
    ("Найти виджет и вызвать его метод", "on click {\n  let lib = find_widget(\"Library\")\n"
                                         "  if lib != nil { lib.move_sel(1) }\n}"),
    ("Управлять другим слоем", "on beat(p) {\n  let l = layer(\"Логотип\")\n  if l != nil { l.rot += 15 * p }\n}"),
    ("Таймеры", "on init {\n  every(1, fn() { log(\"прошла секунда\") })\n  after(3, fn() { log(\"через 3 секунды\") })\n}"),
    ("Плавная анимация (tween)", "state k = 0\non click { tween(self, \"k\", k > 0.5 ? 0 : 1, 0.5, \"out_back\") }"),
    ("Клавиши WASD", "state px = 100\nstate py = 100\non init { capture_keys(true, [\"w\", \"a\", \"s\", \"d\"]) }\n"
                     "on frame(dt) {\n  if key_down(\"a\") { px -= 200 * dt }\n  if key_down(\"d\") { px += 200 * dt }\n"
                     "  if key_down(\"w\") { py -= 200 * dt }\n  if key_down(\"s\") { py += 200 * dt }\n}"),
    ("Звук по клику", "on click { beep(880, 0.12, 0.4) }\n// свой файл: sound(\"C:/sounds/door.wav\", 0.8)"),
    ("Мелодия", "on click { tone([[note(\"C5\"), 0.1], [note(\"E5\"), 0.1], [note(\"G5\"), 0.2]]) }"),
    ("Картинка в форме сердца", "on draw { image_shape(track.cover, \"heart\", 0, 0, w, h) }"),
    ("Маска любой формы", "on draw {\n  save()\n  clip_path(shape(\"star\", 0, 0, w, h, {points: 6, inner: 0.55}))\n"
                          "  fill(linear(0, 0, w, h, [theme.accent, theme.accent2]))\n  rect(0, 0, w, h)\n  restore()\n}"),
    ("Клик только по форме (hit_shape)", "fn mask() { self.hit_shape = shape(\"circle\", 0, 0, w, h) }\n"
                                         "on init { mask() }\non resize { mask() }\non click { toggle() }"),
    ("Холст-рисовалка", "state c = nil\non init { c = canvas(w, h) }\non drag(x, y) {\n"
                        "  draw_on(c, fn() { fill(theme.accent); circle(x, y, 6) })\n}\n"
                        "on draw { draw_canvas(c, 0, 0, w, h) }"),
    ("Помехи камеры наблюдения", "on draw {\n  fill(\"#101418\")\n  rect(0, 0, w, h)\n"
                                 "  static_noise(0, 0, w, h, 0.25)\n  scanlines(0, 0, w, h, 0.2)\n  vignette(0, 0, w, h, 0.7)\n}"),
    ("Сохранить рекорд", "state best = load_data(\"рекорд\", 0)\nfn win(score) {\n  if score > best {\n"
                         "    best = score\n    save_data(\"рекорд\", best)\n  }\n}"),
    ("Выбор варианта (match)", "fn label(n) {\n  match n {\n    0 => { return \"ноль\" }\n    1, 2, 3 => { return \"мало\" }\n"
                               "    4..10 => { return \"средне\" }\n    else => { return \"много\" }\n  }\n}"),
    ("Защита от ошибки (try)", "on click {\n  try {\n    let data = from_json(\"{\\\"a\\\": 1}\")\n    log(data.a)\n"
                               "  } catch e {\n    log(\"ошибка:\", e)\n  }\n}"),
    ("Строка с подстановкой", "on draw { text(`{track.title} — {fmt_time(track.position)}`, 10, h / 2, 14) }"),
]


def interactive_src(src: str) -> bool:
    return bool(src) and _INTERACTIVE_RE.search(DL.mask(src)) is not None


def _lit_str(s: str) -> str:
    return '"' + str(s).replace("\\", "/").replace('"', '\\"') + '"'


def palette_block(t: dict) -> str:
    """Палитра темы конструктора → блок theme { } для скриптов (theme.accent и т.д.)."""
    pal = tk.resolved_palette(t) if t else {}
    keys = {"bg": "bg", "accent": "accent", "accent2": "accent2", "accent3": "glow", "text": "text",
            "muted": "muted", "panel": "panel", "danger": "danger"}
    parts = [f'name: {_lit_str(t.get("name", "") if t else "")}']
    for k, src in keys.items():
        v = pal.get(src) if isinstance(pal, dict) else None
        if isinstance(v, str) and v.startswith("#"):
            parts.append(f"{k}: {_lit_str(v[:7])}")
    fam = (t.get("font") or {}).get("family") if t else ""
    if fam:
        parts.append(f"font: {_lit_str(fam)}")
    return "theme { " + ", ".join(parts) + " }\n"


def _strip_scene(code: str) -> str:
    for _ in range(8):
        sp = DL._block_span(code, "scene")
        if sp is None:
            break
        code = code[:sp[0]] + code[sp[2] + 1:]
    return code


class LayerScript:
    """Скомпилированный скрипт одного слоя: виджет (role="widget") или поведение (role="behavior")."""

    def __init__(self, host: "ScriptHost", L: dict, role: str, key):
        from dsl_runtime import Scene
        from dsl_lang import EchoError
        self.host, self.role, self.key = host, role, key
        self.lid = L["id"]
        self.lname = str(L.get("name") or "")
        self.error = None
        self.scene = None
        self.inst = None
        self.started = False
        self._iact = None
        self.drawable = False
        prefix = palette_block(host.rt.theme)
        if role == "widget":
            code = _strip_scene(L.get("script") or "")
            names = DL.widget_names(code)
            cls = L.get("widget") if L.get("widget") in names else (names[0] if names else "")
            if not cls:
                self.error = "в скрипте нет ни одного widget { … }"
                return
            spec = [f"{k}: {self._resolve(v)}" for k, v in (L.get("props") or {}).items()
                    if k not in ("x", "y", "w", "h")]
            spec.append(f"clip: {'true' if L.get('clip', True) else 'false'}")
            body = code
            scene = f"\nscene {{\n  add {cls} {{ x: 0, y: 0, w: 1, h: 1, {', '.join(spec)} }}\n}}\n"
        else:
            fields = BG_FIELDS if role == "bg" else BEHAVIOR_FIELDS
            cls = "__Background" if role == "bg" else "__Behavior"
            body = f"widget {cls} {{\n" + (L.get("behavior") or "") + "\n}\n"
            init = ", ".join(f"{k}: {self._val(v)}" for k, v in fields.items())
            scene = f"\nscene {{\n  add {cls} {{ x: 0, y: 0, w: 1, h: 1, {init} }}\n}}\n"
        # номера строк в ошибках — как в редакторе (без служебного theme { } и строки «widget …»)
        self.offset = prefix.count("\n") + (0 if role == "widget" else 1)
        try:
            sc = Scene(prefix + body + scene, host.bridge, host.analyzer, log_fn=host.log_line, world=host,
                       name=self.lname)
        except EchoError as e:
            self.error = self._fix(e)
            return
        except Exception as e:                             # noqa: BLE001
            self.error = f"ошибка: {e}"
            return
        sc.gpu = False
        self.scene = sc
        self.inst = next((i for i in sc.instances if i.cls.name == cls), None)
        if self.inst is None:
            self.error = f"виджет «{cls}» не создан"
            self.scene = None
            return
        self._iact = None
        self.drawable = "draw" in self.inst.cls.handlers or bool(self.inst.kids) or role == "widget"

    @property
    def interactive(self) -> bool:
        """Ловит ли мышь сам виджет или любой вложенный (пересчёт, когда в сцене появились новые виджеты)."""
        sc = self.scene
        if sc is None:
            return False
        if self._iact is None or self._iact[0] != sc._uid:
            from dsl_runtime import handles
            self._iact = (sc._uid, any(handles(t, ev) for t in sc._walk() for ev in MOUSE_EVENTS))
        return self._iact[1]

    def _fix(self, e) -> str:
        line = getattr(e, "line", 0) or 0
        if line > self.offset:
            return f"строка {line - self.offset}: {e.msg}"
        return getattr(e, "msg", str(e))

    @staticmethod
    def _val(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, str):
            return _lit_str(v)
        return DL.fmt_num(float(v))

    def _resolve(self, v: str) -> str:
        """Свойство-строка «assets/…» → полный путь файла темы."""
        s = str(v).strip()
        if len(s) >= 2 and s[0] in "\"'" and s[-1] == s[0] and s[1:-1].startswith("assets/"):
            p = self.host.rt.asset(s[1:-1])
            return _lit_str(p) if p else s
        return s or "nil"

    def frame(self, sh, dt, w, h):
        sc = self.scene
        if sc is None or self.inst is None:
            return
        w, h = max(1, int(round(w))), max(1, int(round(h)))
        if not self.started:
            sc.W = sc.H = -1
            sc.resize(w, h)
            sc.start()
            self.started = True
        else:
            sc.resize(w, h)
        sc.frame_shared(dt, sh)
        if self.inst.error is not None and self.error is None:
            self.error = self._fix(self.inst.error)

    def paint(self, p: QPainter):
        sc = self.scene
        if sc is not None and self.inst is not None and self.inst.error is None and self.started:
            n = len(sc.errors)
            sc.paint(p)
            if len(sc.errors) != n and self.error is None:
                self.error = self._fix(self.inst.error) if self.inst.error is not None else sc.errors[-1]

    def motion(self) -> dict | None:
        if self.inst is None or self.inst.error is not None or not self.started:
            return None
        f = self.inst.fields

        def num(k, d):
            v = f.get(k, d)
            return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else d
        if self.role == "bg":
            m = {k: num(k, d) for k, d in BG_FIELDS.items()}
            m["speed"] = max(0.05, min(8.0, m["speed"]))
            m["zoom"] = max(0.2, min(5.0, m["zoom"]))
            m["dim"] = min(1.0, m["dim"])
            return m
        out = {k: num(k, d) for k, d in BEHAVIOR_FIELDS.items() if isinstance(d, float)}
        out["hidden"] = bool(f.get("hidden"))
        tint = f.get("tint")
        out["tint"] = ""
        if tint not in ("", None):
            try:
                from dsl_runtime import to_qcolor
                c = to_qcolor(tint)
                # QColor понимает 8 цифр как #AARRGGBB — отдаём именно так (у EchoScript — #RRGGBBAA)
                out["tint"] = c.name(QColor.NameFormat.HexArgb if c.alpha() < 255 else QColor.NameFormat.HexRgb)
            except Exception:                              # noqa: BLE001
                pass
        lab = f.get("label")
        out["label"] = lab if isinstance(lab, str) else ("" if lab is None else str(lab))
        return out


# ══════════════════════════════════════════════════════════════════════════ #
#  Эффекты из встроенных тем
# ══════════════════════════════════════════════════════════════════════════ #

class _Effect:
    img = None
    gen = 0

    def step(self, dt, w, h, L):
        pass

    def stop(self):
        pass


class _LiveEffect(_Effect):
    """Живые обои конструктора (сияние, ретровейв, плазма, звёзды, дождь) — слоем."""

    def __init__(self, rt, L):
        from theme_layers import LiveWallpaper
        t = rt.theme
        cols = L.get("colors") or t["background"].get("colors")
        self.lw = LiveWallpaper(L["effect"], t["palette"]["bg"], cols, L.get("speed", 1.0), L.get("react", 0.6), rt)
        self.size = None
        self.acc = 1.0
        self.cfg = None

    def step(self, dt, w, h, L):
        sz = (max(8, int(w)), max(8, int(h)))
        if sz != self.size:
            self.size = sz
            self.lw.resize(*sz)
            self.lw.prewarm()
        self.acc += dt
        if self.acc >= 1 / 45.0:                           # обои считаются в низком разрешении, 45 кадров хватает
            self.lw.step(min(self.acc, 0.1))
            self.acc = 0.0
            self.img = self.lw.img
            self.gen += 1


class _MilkEffect(_Effect):
    """MilkDrop из темы Winamp: кадры считает фоновый поток, интерфейс не ждёт."""

    def __init__(self, rt, L):
        from winamp_theme import _MilkWorker
        self.rt = rt
        self.w = _MilkWorker()
        self.w.start()
        self._arr = None
        self._last = time.monotonic()
        self.cost = 8.0
        self.k = 1.0

    def step(self, dt, w, h, L):
        res = self.w.take()
        if res is not None:
            if res.get("img") is not None:
                self.img, self._arr = res["img"], None
            else:
                arr = res["frame"]
                hh, ww = arr.shape
                self._arr = arr.view(np.uint8)
                self.img = QImage(self._arr.data, ww, hh, 4 * ww, QImage.Format.Format_RGB32)
            self.gen += 1
            self.cost = self.cost * 0.85 + res["cost"] * 0.15
            if self.cost > 11.5:
                self.k = max(0.6, self.k - 0.05)
            elif self.cost < 6.0:
                self.k = min(1.0, self.k + 0.02)
        eng = self.rt.engine
        samples, rate = None, 44100
        try:
            if eng is not None and eng.is_playing():
                s = eng.get_visual_samples(1024)
                rate = int(getattr(eng, "visual_sample_rate", 44100) or 44100)
                if s is not None:
                    samples = np.array(s, dtype=np.float32, copy=True)
        except Exception:                                  # noqa: BLE001
            samples = None
        now = time.monotonic()
        native = self.w.core.native
        top = 1100 if native else 520                      # C++: сетка крупнее, кадр сразу в размер слоя
        gw = int(max(200, min(top, w * float(L.get("res", 0.5)) * self.k * (1.6 if native else 1.0))))
        gh = max(24, int(gw * max(1.0, h) / max(1.0, w)))
        ow, oh = (int(min(2560, w)), int(min(1600, h))) if native else (0, 0)
        if self.w.submit(samples, rate, min(0.1, now - self._last), gw, gh, ow, oh):
            self._last = now

    def command(self, *cmd):
        self.w.command(*cmd)

    def stop(self):
        self.w.stop()


class _FluidEffect(_Effect):
    """Жидкость из темы Fluid в цветах обложки: всплески на бит, фоновый поток."""

    def __init__(self, rt, L):
        import fluid_core as fc
        from fluid_theme import _FluidWorker
        self.rt, self.fc = rt, fc
        self.wk = _FluidWorker()
        self.wk.start()
        self.dark = not tk.is_light(rt.theme["palette"]["bg"])
        self.dom = fc.DEFAULT_DOMINANT_DARK if self.dark else fc.DEFAULT_DOMINANT
        self.grid = None
        self.cover = None
        self.cool = 0.0
        self.acc = 0.0
        self.scale = 0.7                                   # доля размера слоя, в которой красится кадр
        self.cost = 8.0

    def _cover_palette(self, path):
        fc = self.fc
        dom, mono = (fc.DEFAULT_DOMINANT_DARK if self.dark else fc.DEFAULT_DOMINANT), False
        try:
            from fluid_theme import _qimage_to_rgb_array
            from img_load import read_image
            img = read_image(path, 128) if path else QImage()
            if not img.isNull():
                small = img.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation)
                dom, mono = fc.dominant_rgb(_qimage_to_rgb_array(small))
        except Exception:                                  # noqa: BLE001
            pass
        return fc.make_palette(dom, mono, dark=self.dark)

    def step(self, dt, w, h, L):
        r = self.wk.take()
        if r is not None:
            self.img = r[0]
            self.gen += 1
            # качество как в теме Fluid: кадр укладывается в ~11 мс фонового времени
            self.cost = self.cost * 0.85 + r[1] * 0.15
            if self.cost > 11.5 and self.scale > 0.45:
                self.scale -= 0.03
            elif self.cost < 6.5 and self.scale < 0.85:
                self.scale += 0.02
        res = float(L.get("res", 0.5))
        gh = int(max(32, min(160, 190 * res)))
        gw = int(max(24, min(240, gh * max(1.0, w) / max(1.0, h))))
        cover = getattr(self.rt.backdrop, "cover_path", "") if self.rt.backdrop is not None else ""
        if self.grid is None:
            self.grid = (gw, gh)
            self.cover = cover
            self.wk.cmd("create", gw, gh, self.fc.seed_from_key(cover or "echoes"), self._cover_palette(cover), 0.0)
        elif (gw, gh) != self.grid:
            self.grid = (gw, gh)
            self.wk.cmd("regrid", gw, gh)
        if cover != self.cover:
            self.cover = cover
            self.wk.cmd("palette", self._cover_palette(cover))
            self.wk.cmd("seed", self.fc.seed_from_key(cover or "echoes"))
        pulse = self.rt.pulse
        self.cool = max(0.0, self.cool - dt)
        if pulse.beat > 0.55 and self.cool <= 0:
            self.cool = 0.25
            self.wk.cmd("splash", random.uniform(0.2, 0.8) * gw, random.uniform(0.2, 0.8) * gh,
                        0.6 + pulse.beat * float(L.get("react", 0.6)))
        self.acc += dt
        if self.acc >= 1 / 40.0:
            # краска растягивается до почти полного размера слоя и только потом раскрашивается
            # (в фоновом потоке): края зон — гладкие кривые. Раньше кадр был сетка ×3 и потом
            # ещё растягивался на окно — края шли «лесенкой»
            iw, ih = max(16, min(2560, int(w))), max(16, min(1600, int(h)))
            ow, oh = max(16, int(iw * self.scale)), max(16, int(ih * self.scale))
            if self.wk.submit(min(0.1, self.acc), pulse.level, iw, ih, ow, oh):
                self.acc = 0.0

    def splash(self, fx, fy, power=1.0):
        if self.grid is not None:
            self.wk.cmd("splash", fx * self.grid[0], fy * self.grid[1], power)

    def stop(self):
        self.wk.stop()


def make_effect(rt, L):
    kind = L.get("effect")
    try:
        if kind == "milkdrop":
            return _MilkEffect(rt, L)
        if kind == "fluid":
            return _FluidEffect(rt, L)
        if kind in tk.LIVE_BACKGROUNDS:
            return _LiveEffect(rt, L)
    except Exception as e:                                 # noqa: BLE001
        print("[layers] effect:", kind, e)
    return _Effect()


# ══════════════════════════════════════════════════════════════════════════ #
#  Хозяин скриптов и эффектов темы
# ══════════════════════════════════════════════════════════════════════════ #

def geo_local(geo, x, y):
    """Точка окна → координаты внутри слоя (с учётом поворота и растяжения слоя)."""
    dx, dy = x - geo["cx"], y - geo["cy"]
    a = -math.radians(geo["rot"])
    lx = dx * math.cos(a) - dy * math.sin(a)
    ly = dx * math.sin(a) + dy * math.cos(a)
    sx = geo["sx"] or 1e-6
    sy = geo["sy"] or 1e-6
    return lx / sx + geo["w"] / 2, ly / sy + geo["h"] / 2


class LayerRef:
    """Слой темы для скрипта: layer("Логотип").rot = 30, .hidden = true, .opacity = 0.5, .show() …
    Значения накладываются поверх анимаций и поведения слоя (сдвиг/поворот — прибавляются,
    масштаб/непрозрачность — умножаются). Для слоя-виджета .widget — его виджет (поля, методы)."""
    echo_type = "слой"
    echo_kind = "layer"
    FIELDS = ("dx", "dy", "rot", "scale", "sx", "sy", "opacity", "tilt_x", "tilt_y", "hidden", "tint", "label")
    __slots__ = ("host", "lid")

    def __init__(self, host, lid):
        self.host, self.lid = host, lid

    def __repr__(self):
        L = self._L()
        return f"<слой {L.get('name') if L else self.lid}>"

    def __eq__(self, other):
        return isinstance(other, LayerRef) and other.lid == self.lid

    def __hash__(self):
        return hash(self.lid)

    def _L(self):
        t = self.host.rt.theme
        for L in (t or {}).get("layers", []):
            if L["id"] == self.lid:
                return L
        return None

    def _set(self, name, v):
        self.host.overrides.setdefault(self.lid, {})[name] = v
        tk.SCRIPT_MOVED.add(self.lid)

    def show(self):
        self._set("hidden", False)

    def hide(self):
        self._set("hidden", True)

    def toggle(self):
        self._set("hidden", not bool(self.host.overrides.get(self.lid, {}).get("hidden", False)))

    def reset(self):
        self.host.overrides.pop(self.lid, None)
        self.host.motion.pop(self.lid, None)

    def echo_get(self, name):
        ov = self.host.overrides.get(self.lid, {})
        if name in self.FIELDS:
            return ov.get(name, BEHAVIOR_FIELDS.get(name))
        if name == "visible":
            return not bool(ov.get("hidden", False))
        L = self._L()
        if name in ("name", "kind", "id"):
            return L.get(name) if L else None
        if name in ("x", "y"):
            return float(L.get(name, 0.5)) if L else 0.0
        if name in ("w", "h"):
            if L is None:
                return 0.0
            from theme_layers import layer_base_size
            W, H = self.host.rt.size()
            return float(layer_base_size(L, W, H, None)[0 if name == "w" else 1])
        if name == "widget":
            ls = self.host.items.get((self.lid, "widget"))
            return ls.inst if ls is not None else None
        if name in ("show", "hide", "toggle", "reset"):
            return getattr(self, name)
        raise EchoError(f"у слоя нет «{name}» (есть: {', '.join(self.FIELDS)}, visible, name, kind, x, y, w, h, "
                        f"widget, show(), hide(), toggle(), reset())")

    def echo_set(self, name, v):
        if name == "visible":
            name, v = "hidden", not v
        if name not in self.FIELDS:
            raise EchoError(f"у слоя можно менять: {', '.join(self.FIELDS)}, visible")
        self._set(name, v)


class ScriptHost:
    """Все скрипты и эффекты слоёв одной темы: один анализ звука и один снимок плеера на кадр."""

    def __init__(self, rt):
        from dsl_runtime import AudioAnalyzer, PlayerBridge
        self.rt = rt
        win = rt.host if hasattr(rt.host, "queue_context") else None
        self.bridge = PlayerBridge(win)
        self.analyzer = AudioAnalyzer(rt.engine)
        self.items: dict = {}                              # (id слоя, роль) → LayerScript
        self.effects: dict = {}                            # id слоя → (ключ, эффект)
        self.motion: dict = {}                             # id слоя → поля поведения
        self.frames: dict = {}                             # id слоя → готовая картинка виджета
        self.bg_motion = None                              # поля скрипта фона (speed, zoom, dx, dy, dim)
        self.shared = None
        self.mouse = {"x": 0.0, "y": 0.0, "down": False}
        self.log: list[str] = []
        self.listeners = []                                # редактор: обновить ошибки скриптов
        self._pal_key = None
        # «мир» для скриптов всех слоёв темы: общие переменные, клавиши, управление чужими слоями
        self.shared_vars: dict = {}                        # shared в скриптах (self.shared — снимок кадра)
        self.keys_down: set = set()
        self.capture: object = None                        # capture_keys: None — нет, True — все, set — эти
        self.overrides: dict = {}                          # id слоя → поля, заданные скриптом через layer("…")

    # ── мир скриптов (dsl_runtime.Scene.world) ──
    def scenes(self):
        return [(ls.scene, ls.lname) for ls in list(self.items.values()) if ls.scene is not None]

    def asset(self, p):
        try:
            return self.rt.asset(p) or p
        except Exception:                                  # noqa: BLE001
            return p

    def _layer_by_name(self, name):
        t = self.rt.theme
        if not t:
            return None
        for L in t.get("layers", []):
            if L.get("name") == name or L.get("id") == name:
                return L
        return None

    def layer(self, name):
        L = self._layer_by_name(name)
        return LayerRef(self, L["id"]) if L is not None else None

    def layer_names(self):
        t = self.rt.theme
        return [str(L.get("name") or "") for L in (t or {}).get("layers", [])]

    def to_scene(self, scene, x, y):
        """Точка окна (mouse.x, mouse.y) → координаты внутри слоя, где живёт scene."""
        t = self.rt.theme
        for (lid, role), ls in list(self.items.items()):
            if ls.scene is not scene:
                continue
            if role == "bg" or not t:
                return x, y
            for i, L in enumerate(t.get("layers", [])):
                if L["id"] == lid:
                    W, H = self.rt.size()
                    geo = self.rt.painter.geometry(L, W, H, self.rt.pulse.t, self.rt.pulse, i)
                    return geo_local(geo, x, y)
        return x, y

    def wants_keys(self) -> bool:
        return self.capture is not None or any(sc.wants_keys() for sc, _n in self.scenes())

    def key_event(self, name, down) -> bool:
        if down:
            self.keys_down.add(name)
        else:
            self.keys_down.discard(name)
        used = False
        for sc, _n in self.scenes():
            if sc.key_event(name, down):
                used = True
        return used

    def captures(self, name) -> bool:
        c = self.capture
        return c is True or (isinstance(c, set) and name in c)

    def _apply_overrides(self):
        if not self.overrides:
            return
        from dsl_runtime import to_qcolor
        for lid, ov in self.overrides.items():
            m = dict(self.motion.get(lid) or {k: v for k, v in BEHAVIOR_FIELDS.items()})
            for k, v in ov.items():
                try:
                    if k in ("dx", "dy", "rot", "tilt_x", "tilt_y"):
                        m[k] = float(m.get(k, 0.0)) + float(v)
                    elif k in ("scale", "sx", "sy", "opacity"):
                        m[k] = float(m.get(k, 1.0)) * float(v)
                    elif k == "hidden":
                        m["hidden"] = bool(v) or bool(m.get("hidden"))
                    elif k == "tint":
                        c = to_qcolor(v) if v not in ("", None) else None
                        m["tint"] = "" if c is None else c.name(QColor.NameFormat.HexArgb if c.alpha() < 255
                                                                 else QColor.NameFormat.HexRgb)
                    elif k == "label":
                        m["label"] = "" if v is None else str(v)
                except Exception:                          # noqa: BLE001
                    continue
            self.motion[lid] = m

    def log_line(self, msg):
        self.log.append(str(msg))
        del self.log[:-80]

    # ── скрипты ──
    def _key(self, L, role):
        src = L.get("script") if role == "widget" else L.get("behavior")
        extra = (L.get("widget"), json.dumps(L.get("props") or {}, sort_keys=True), L.get("clip", True)) \
            if role == "widget" else ()
        return (src, extra, self._pal_key)

    def get(self, L, role) -> LayerScript | None:
        if role == "widget" and L.get("kind") != "script":
            return None
        if role in ("behavior", "bg") and not (L.get("behavior") or "").strip():
            return None
        k = (L["id"], role)
        key = self._key(L, role)
        ls = self.items.get(k)
        if ls is None or ls.key != key:
            old = ls
            ls = LayerScript(self, L, role, key)
            if old is not None and old.scene is not None and ls.scene is not None:
                try:
                    ls.scene.adopt_state(old.scene)              # правка кода — состояние не сбрасывается
                except Exception:                          # noqa: BLE001
                    pass
            self.items[k] = ls
            for fn in list(self.listeners):
                try:
                    fn()
                except Exception:                          # noqa: BLE001
                    pass
        return ls

    def error(self, L) -> str:
        out = []
        for role in ("widget", "behavior", "bg"):
            ls = self.items.get((L["id"], role))
            if ls is not None and ls.error:
                out.append(("Поведение: " if role == "behavior" else "") + ls.error)
        return "\n".join(out)

    def effect(self, L):
        key = (L.get("effect"), json.dumps(L.get("colors") or []), round(L.get("speed", 1.0), 3),
               round(L.get("react", 0.6), 3))
        ent = self.effects.get(L["id"])
        if ent is not None and ent[0] == key:
            return ent[1]
        if ent is not None and ent[0][0] == key[0] and key[0] in ("milkdrop", "fluid"):
            self.effects[L["id"]] = (key, ent[1])        # тяжёлые эффекты не пересоздаём из-за цвета
            return ent[1]
        if ent is not None:
            ent[1].stop()
        eff = make_effect(self.rt, L)
        self.effects[L["id"]] = (key, eff)
        return eff

    def sync(self, theme):
        """Слои удалили — их скрипты и эффекты тоже."""
        pk = json.dumps([theme.get("palette"), (theme.get("font") or {}).get("family")], sort_keys=True) \
            if theme else None
        self._pal_key = pk
        ids = {L["id"] for L in (theme or {}).get("layers", [])} | {"__bg__"}
        for k in [k for k in self.items if k[0] not in ids]:
            del self.items[k]
        for lid in [lid for lid in self.effects if lid not in ids]:
            self.effects.pop(lid)[1].stop()
        for lid in [lid for lid in self.motion if lid not in ids]:
            del self.motion[lid]
        for lid in [lid for lid in self.frames if lid not in ids]:
            del self.frames[lid]
        for lid in [lid for lid in self.overrides if lid not in ids]:
            del self.overrides[lid]
            tk.SCRIPT_MOVED.discard(lid)

    @staticmethod
    def needed(theme) -> bool:
        if ((theme or {}).get("background") or {}).get("script", "").strip():
            return True
        return any(L.get("visible", True) and (L["kind"] in ("script", "effect") or (L.get("behavior") or "").strip())
                   for L in (theme or {}).get("layers", []))

    @staticmethod
    def interactive(theme) -> bool:
        for L in (theme or {}).get("layers", []):
            if not L.get("visible", True):
                continue
            if L["kind"] == "effect" and L.get("effect") in ("milkdrop", "fluid"):
                return True                                # клик — пресет MilkDrop, мышь мешает жидкость
            if L["kind"] == "script" and interactive_src(L.get("script")):
                return True
            if interactive_src(L.get("behavior")):
                return True
        return False

    def step(self, dt, theme, W, H):
        from theme_layers import layer_base_size
        if not theme:
            return
        layers = [L for L in theme["layers"] if L.get("visible", True)]
        scripted = [L for L in layers if L["kind"] == "script" or (L.get("behavior") or "").strip()]
        bg_src = (theme["background"].get("script") or "").strip()
        if scripted or bg_src:
            pl = self.bridge.player()
            self.analyzer.update(dt, pl["playing"])
            from dsl_runtime import snapshot
            sh = snapshot(self.bridge, self.analyzer, pl)
            sh["mouse"] = self.mouse
            self.shared = sh
        self.bg_motion = None
        if bg_src:                                         # скрипт фона: скорость видео, зум, сдвиг, затемнение
            ls = self.get({"id": "__bg__", "behavior": bg_src, "kind": "bg"}, "bg")
            if ls is not None:
                ls.frame(self.shared, dt, W, H)
                self.bg_motion = ls.motion()
        for L in layers:
            k = L["kind"]
            if k in ("script", "effect") or (L.get("behavior") or "").strip():
                w, h = layer_base_size(L, W, H, None)
            else:
                if L["id"] in self.overrides:
                    self.motion.pop(L["id"], None)         # поля layer("…") накладываются заново ниже
                continue
            if (L.get("behavior") or "").strip():
                ls = self.get(L, "behavior")
                if ls is not None:
                    ls.frame(self.shared, dt, w, h)
                    m = ls.motion()
                    if m is not None:
                        self.motion[L["id"]] = m
                    else:
                        self.motion.pop(L["id"], None)
            else:
                self.motion.pop(L["id"], None)
            if k == "script":
                ls = self.get(L, "widget")
                if ls is not None:
                    self._widget_frame(L, ls, dt, w, h)
            elif k == "effect":
                self.effect(L).step(dt, w, h, L)
        self._apply_overrides()

    def _widget_frame(self, L, ls, dt, w, h):
        """Логика и рисование виджета — не чаще его частоты кадров (fps слоя; 0 — каждый кадр экрана).
        Рисуется ОДИН раз в картинку; перерисовки окна дальше только копируют её (с поворотом,
        растяжением, 3D-наклоном), а не выполняют draw заново на каждую перерисовку области."""
        ent = self.frames.get(L["id"])
        if ent is None:
            ent = self.frames[L["id"]] = {"img": None, "gen": 0, "acc": 0.0, "m": 0.0, "size": None}
        ent["acc"] += dt
        fps = float(L.get("fps", 0) or 0)
        size = (int(round(w)), int(round(h)), bool(L.get("clip", True)), self.rt.dpr)
        if fps > 0 and ent["img"] is not None and ent["size"] == size and ent["acc"] < 1.0 / fps - 0.002:
            return
        step, ent["acc"] = min(0.1, ent["acc"]), 0.0
        ls.frame(self.shared, step, w, h)
        if ls.scene is None or not ls.started or not getattr(ls, "drawable", True):
            ent["img"] = None
            return
        dpr = self.rt.dpr
        m = 0.0 if L.get("clip", True) else max(w, h) * 0.35 + 8
        iw, ih = max(1, int(math.ceil((w + 2 * m) * dpr))), max(1, int(math.ceil((h + 2 * m) * dpr)))
        if iw * ih > 40_000_000:                           # гигантский слой — без картинки, сразу в окно
            ent["img"] = None
            return
        img = ent["img"]
        if img is None or img.width() != iw or img.height() != ih:
            img = QImage(iw, ih, QImage.Format.Format_ARGB32_Premultiplied)
            img.setDevicePixelRatio(dpr)
        img.fill(0)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.translate(m, m)
        try:
            ls.paint(p)
        finally:
            p.end()
        ent.update(img=img, m=m, size=size)
        ent["gen"] += 1

    def widget_gen(self, L):
        ent = self.frames.get(L["id"])
        return ent["gen"] if ent is not None else None

    def paint_widget(self, p, L, w, h) -> bool:
        ent = self.frames.get(L["id"])
        if ent is not None and ent["img"] is not None:
            m = ent["m"]
            p.drawImage(QRectF(-w / 2 - m, -h / 2 - m, w + 2 * m, h + 2 * m), ent["img"])
            return True
        ls = self.items.get((L["id"], "widget"))
        if ls is None or ls.scene is None or not ls.started:
            return False
        p.translate(-w / 2, -h / 2)                        # без картинки (огромный слой) — прямо в окно
        ls.paint(p)
        return True

    def clear(self):
        for _key, eff in self.effects.values():
            try:
                eff.stop()
            except Exception:                              # noqa: BLE001
                pass
        self.effects.clear()
        self.items.clear()
        self.motion.clear()
        self.frames.clear()
        for lid in self.overrides:
            tk.SCRIPT_MOVED.discard(lid)
        self.overrides.clear()
        self.shared_vars.clear()
        self.keys_down.clear()
        self.capture = None


# ══════════════════════════════════════════════════════════════════════════ #
#  Мышь для слоёв
# ══════════════════════════════════════════════════════════════════════════ #

_PASSIVE = {"QWidget", "QFrame", "QLabel", "FluidPanel", "QStackedWidget", "Backdrop", "Overlay"}


class LayerInput(QObject):
    """Фильтр событий приложения: клики/перетаскивание/колесо/наведение уходят виджетам и
    поведению слоёв (в том числе вложенным виджетам — самому глубокому под курсором). Слои поверх
    интерфейса перехватывают мышь всегда, слои на фоне — только там, где под курсором пустое место
    (не кнопка, не список, не поле ввода). Клавиши — скриптам с on key / on keyup (если фокус не в
    поле ввода); capture_keys() забирает их у горячих клавиш плеера."""

    _CURSORS = {"hand": Qt.CursorShape.PointingHandCursor, "arrow": Qt.CursorShape.ArrowCursor,
                "cross": Qt.CursorShape.CrossCursor, "none": Qt.CursorShape.BlankCursor,
                "text": Qt.CursorShape.IBeamCursor, "move": Qt.CursorShape.SizeAllCursor,
                "wait": Qt.CursorShape.WaitCursor, "grab": Qt.CursorShape.OpenHandCursor,
                "grabbing": Qt.CursorShape.ClosedHandCursor, "forbidden": Qt.CursorShape.ForbiddenCursor}

    def __init__(self, rt):
        super().__init__(rt)
        self.rt = rt
        self.on = False
        self.press = None                                  # (LayerScript, inst, L, x0, y0, кнопка)
        self.hover = None                                  # (LayerScript, inst)
        self._cursor = False
        self._seen = None
        self._kseen = None
        self._eaten: set = set()                           # клавиши, нажатие которых поглотил скрипт

    def enable(self, on: bool):
        app = QApplication.instance()
        if app is None:
            return
        if on and not self.on:
            app.installEventFilter(self)
            self.on = True
        elif not on and self.on:
            app.removeEventFilter(self)
            self.on = False
            self._leave()
            self.press = None
            sh = self.rt.scripts
            if sh is not None:
                sh.keys_down.clear()
            self._eaten.clear()

    # ── попадание ──
    def _local(self, geo, pos: QPointF):
        return geo_local(geo, pos.x(), pos.y())

    def _hit(self, pos, passive):
        rt = self.rt
        t = rt.theme
        host = self.rt.scripts
        if t is None or host is None:
            return None
        W, H = rt.size()
        ui_layers = t["components"].get("ui") == "layers"
        idx = list(enumerate(t["layers"]))
        order = [x for x in idx if x[1]["z"] == "front"][::-1]
        if passive or ui_layers:
            order += [x for x in idx if x[1]["z"] == "back"][::-1]
        for i, L in order:
            if not L.get("visible", True):
                continue
            cands = [host.items.get((L["id"], "widget")), host.items.get((L["id"], "behavior"))]
            cands = [c for c in cands if c is not None and c.scene is not None and c.interactive]
            if not cands:
                continue
            geo = rt.painter.geometry(L, W, H, rt.pulse.t, rt.pulse, i)
            if geo.get("hidden"):
                continue
            lx, ly = self._local(geo, pos)
            if not (0 <= lx <= geo["w"] and 0 <= ly <= geo["h"]):
                continue
            for ls in cands:
                inst = ls.scene.hit(lx, ly, MOUSE_EVENTS)
                if inst is not None:
                    return ls, inst, L, i
        return None

    def _hit_effect(self, pos, passive):
        """Слой-эффект MilkDrop/Fluid под курсором → (эффект, тип, доля x, доля y) или None."""
        rt = self.rt
        t = rt.theme
        if t is None or rt.scripts is None:
            return None
        W, H = rt.size()
        idx = list(enumerate(t["layers"]))
        order = [x for x in idx if x[1]["z"] == "front"][::-1]
        if passive or t["components"].get("ui") == "layers":
            order += [x for x in idx if x[1]["z"] == "back"][::-1]
        for i, L in order:
            if L["kind"] != "effect" or L.get("effect") not in ("milkdrop", "fluid") or not L.get("visible", True):
                continue
            ent = rt.scripts.effects.get(L["id"])
            if ent is None:
                continue
            geo = rt.painter.geometry(L, W, H, rt.pulse.t, rt.pulse, i)
            lx, ly = self._local(geo, pos)
            if 0 <= lx <= geo["w"] and 0 <= ly <= geo["h"]:
                return ent[1], L["effect"], lx / max(1.0, geo["w"]), ly / max(1.0, geo["h"])
        return None

    def _local_of(self, ls, inst, L, i, pos):
        """Точка окна → координаты внутри виджета inst (вложенного — через всех родителей)."""
        rt = self.rt
        W, H = rt.size()
        geo = rt.painter.geometry(L, W, H, rt.pulse.t, rt.pulse, i)
        lx, ly = self._local(geo, pos)
        if ls.scene is not None:
            return ls.scene.local(inst, lx, ly)
        return lx, ly

    def _leave(self):
        if self.hover is not None:
            ls, inst = self.hover
            inst.fields["hover"] = False
            if ls.scene is not None:
                ls.scene._call(inst, "leave", ())
            self.hover = None
        if self._cursor:
            QApplication.restoreOverrideCursor()
            self._cursor = False

    def _set_hover(self, hit):
        cur = (hit[0], hit[1]) if hit is not None else None
        if cur is not None and self.hover is not None and cur[1] is self.hover[1]:
            return
        self._leave()
        if cur is not None:
            self.hover = cur
            cur[1].fields["hover"] = True
            cur[0].scene._call(cur[1], "enter", ())
            from dsl_runtime import handles
            want = cur[1].fields.get("cursor")
            shape = self._CURSORS.get(str(want)) if want else None
            if shape is None and any(handles(cur[1], ev) for ev in ("click", "press", "drag", "dblclick")):
                shape = Qt.CursorShape.PointingHandCursor
            if shape is not None:
                QApplication.setOverrideCursor(shape)
                self._cursor = True

    def _editing(self) -> bool:
        st = getattr(self.rt.host, "_studio", None)
        return st is not None and getattr(st, "canvas", None) is not None

    # ── клавиатура ──
    @staticmethod
    def _typing() -> bool:
        from PyQt6.QtWidgets import QAbstractSpinBox, QComboBox, QLineEdit, QPlainTextEdit, QTextEdit
        fw = QApplication.focusWidget()
        if fw is None:
            return False
        if isinstance(fw, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)):
            return not (isinstance(fw, (QTextEdit, QPlainTextEdit)) and fw.isReadOnly())
        return isinstance(fw, QComboBox) and fw.isEditable()

    def _key(self, obj, ev, t) -> bool:
        sh = self.rt.scripts
        if sh is None or self._typing():
            return False
        try:
            if obj.window() is not self.rt.host or self._editing():
                return False
        except RuntimeError:
            return False
        from dsl_runtime import key_name
        name = key_name(ev)
        if not name:
            return False
        if t == QEvent.Type.ShortcutOverride:
            if sh.captures(name):
                ev.accept()                                # горячая клавиша плеера не сработает
                return True
            return False
        stamp = (int(t.value), ev.timestamp(), int(ev.key()))
        if stamp == self._kseen:                           # то же событие у родителя — уже обработано
            return name in self._eaten or sh.captures(name)
        self._kseen = stamp
        down = t == QEvent.Type.KeyPress
        if ev.isAutoRepeat():
            return name in self._eaten or sh.captures(name)
        if not sh.wants_keys() and not sh.keys_down and not sh.captures(name):
            return False
        used = sh.key_event(name, down)
        if down:
            if used:
                self._eaten.add(name)
            else:
                self._eaten.discard(name)
        else:
            used = used or name in self._eaten
            self._eaten.discard(name)
        return used or sh.captures(name)

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease, QEvent.Type.ShortcutOverride):
            if self.rt.theme is None or not isinstance(obj, QWidget):
                return False
            try:
                return self._key(obj, ev, t)
            except Exception as e:                         # noqa: BLE001
                print("[layers] клавиши:", e)
                return False
        if t == QEvent.Type.WindowDeactivate and self.rt.scripts is not None:
            self.rt.scripts.keys_down.clear()              # окно потеряло фокус — отпущенные клавиши не «залипают»
            self._eaten.clear()
            return False
        if t not in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease, QEvent.Type.MouseMove,
                     QEvent.Type.Wheel, QEvent.Type.MouseButtonDblClick):
            return False
        if not isinstance(obj, QWidget) or self.rt.theme is None or self.rt.scripts is None:
            return False
        host = self.rt.host
        try:
            if obj.window() is not host or self._editing():
                return False
        except RuntimeError:
            return False
        # одно событие приходит в фильтр по разу для виджета и каждого родителя — берём первый
        stamp = (int(t.value), ev.timestamp() if hasattr(ev, "timestamp") else 0)
        if stamp == self._seen:
            return False
        self._seen = stamp
        try:
            gp = ev.globalPosition()
        except AttributeError:
            return False
        area = self.rt.backdrop if self.rt.backdrop is not None else host
        pos = QPointF(area.mapFromGlobal(gp.toPoint()))
        self.rt.scripts.mouse = {"x": pos.x(), "y": pos.y(), "down": bool(QApplication.mouseButtons())}
        passive = type(obj).__name__ in _PASSIVE
        btn = {Qt.MouseButton.LeftButton: 1, Qt.MouseButton.RightButton: 2}

        if self.press is not None and t in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease):
            ls, inst, L, i, x0, y0, b0, last = self.press
            if ls.scene is None or inst.dead:
                self.press = None
                return False
            lx, ly = self._local_of(ls, inst, L, i, pos)
            if t == QEvent.Type.MouseMove:
                ls.scene._call(inst, "drag", (lx, ly, pos.x() - last[0], pos.y() - last[1]))
                self.press = (ls, inst, L, i, x0, y0, b0, (pos.x(), pos.y()))
                return True
            self.press = None
            inst.fields["pressed"] = False
            ls.scene._call(inst, "release", (lx, ly, btn.get(b0, 3)))
            if abs(pos.x() - x0) < 6 and abs(pos.y() - y0) < 6:
                ls.scene._call(inst, "click", (lx, ly, btn.get(b0, 3)))
            return True

        if t == QEvent.Type.MouseMove:
            if self.press is None and getattr(self, "_stir", None) is not None:
                if not QApplication.mouseButtons():
                    self._stir = None
                else:                                      # ведут по жидкости — всплески по следу
                    eh = self._hit_effect(pos, True)
                    if eh is not None and eh[1] == "fluid":
                        eh[0].splash(eh[2], eh[3], 0.5)
                    return True
            hit = self._hit(pos, passive) if not QApplication.mouseButtons() else None
            self._set_hover(hit)
            if hit is not None:
                ls, inst, L, i = hit
                lx, ly = self._local_of(ls, inst, L, i, pos)
                ls.scene._call(inst, "move", (lx, ly))
            return False
        if t == QEvent.Type.MouseButtonRelease:
            self._stir = None

        hit = self._hit(pos, passive)
        if hit is None:
            if t in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick) and \
                    ev.button() == Qt.MouseButton.LeftButton:
                eh = self._hit_effect(pos, passive)
                if eh is not None:
                    eff, kind, fx, fy = eh
                    if kind == "milkdrop":
                        eff.command("next")                # следующий пресет MilkDrop
                    else:
                        eff.splash(fx, fy, 1.2)            # всплеск жидкости, дальше — вести мышью
                        self._stir = eff
                    return True
            return False
        ls, inst, L, i = hit
        lx, ly = self._local_of(ls, inst, L, i, pos)
        if t == QEvent.Type.Wheel:
            d = ev.angleDelta().y() / 120.0
            ls.scene._call(inst, "wheel", (d, lx, ly))
            return True
        if t in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
            b = ev.button()
            if t == QEvent.Type.MouseButtonDblClick:
                from dsl_runtime import handles
                if handles(inst, "dblclick"):
                    ls.scene._call(inst, "dblclick", (lx, ly, btn.get(b, 3)))
            inst.fields["pressed"] = True
            ls.scene._call(inst, "press", (lx, ly, btn.get(b, 3)))
            self.press = (ls, inst, L, i, pos.x(), pos.y(), b, (pos.x(), pos.y()))
            return True
        return False


# ══════════════════════════════════════════════════════════════════════════ #
#  LOOM (*.echo) → тема конструктора
# ══════════════════════════════════════════════════════════════════════════ #

def _num_lit(s, default):
    s = str(s or "").strip()
    return float(s) if DL.NUM_RE.match(s) else default


def script_layers(src: str) -> list[dict]:
    """Каждое «add Виджет { … }» сцены скрипта → слой-виджет со своим кодом (класс + нужные ему функции)."""
    sp = DL._block_span(src, "scene")
    if sp is None:
        return []
    m = DL.mask(src)
    adds = []
    for mt in re.finditer(r"\badd\s+([A-Za-z_]\w*)", m[sp[1]:sp[2]]):
        cls = mt.group(1)
        pos = sp[1] + mt.start()
        line = src.count("\n", 0, pos) + 1
        adds.append((cls, DL.get_props(src, line, cls)))
    code_of = {}
    layers = []
    for n, (cls, props) in enumerate(adds):
        if cls not in code_of:
            try:
                code_of[cls] = DL.import_widgets("", src, [cls], with_instances=False)[0].strip() + "\n"
            except Exception:                              # noqa: BLE001
                code_of[cls] = _strip_scene(src)
        g = {}
        for k, d in (("x", 0.0), ("y", 0.0), ("w", 1.0), ("h", 1.0)):
            v = _num_lit(props.get(k), d)
            if abs(v) > 1.0:                               # пиксели → доли окна
                v /= REF_W if k in ("x", "w") else REF_H
            if v < 0 and k in ("x", "y"):
                v += 1.0
            g[k] = v
        z = _num_lit(props.get("z"), 0)
        clip = str(props.get("clip", "true")).strip() != "false"
        rest = {k: v for k, v in props.items() if k not in ("x", "y", "w", "h", "z", "clip", "name", "group")}
        L = tk.new_layer("script", name=str(props.get("name", "")).strip('"') or cls, script=code_of[cls], widget=cls,
                         props=rest, clip=clip, z="back" if z < 0 else "front", fps=60,   # как было в LOOM
                         **_geom_layer(g["x"], g["y"], g["w"], g["h"]))
        L["_order"] = (z, n)
        layers.append(L)
    layers.sort(key=lambda L: L.pop("_order"))
    return layers


def script_theme(name: str, src: str) -> dict:
    """Целая тема LOOM → тема конструктора: палитра из theme { }, интерфейс — слоями."""
    th = DL.theme_get(src)
    t = tk.blank_canvas()
    t["name"] = name
    t["description"] = tk.TR("Перенесено из LOOM: виджеты EchoScript стали слоями")
    pal = {}
    for k in ("bg", "accent", "accent2", "text", "muted", "panel"):
        v = th.get(k)
        if isinstance(v, str) and re.match(r"^#[0-9a-fA-F]{6}", v):
            pal[k] = v[:7]
    if isinstance(th.get("accent3"), str) and th["accent3"].startswith("#"):
        pal["glow"] = th["accent3"][:7]
    t["palette"].update(pal)
    t["background"]["color"] = pal.get("bg", t["background"]["color"])
    if isinstance(th.get("font"), str):
        t["font"]["family"] = th["font"]
    t["layers"] = script_layers(src)
    return tk.normalize(t)


# ══════════════════════════════════════════════════════════════════════════ #
#  Предустановки: эффекты из знаменитых тем
# ══════════════════════════════════════════════════════════════════════════ #

def _loom_source():
    try:
        import dsl_studio
        return dsl_studio.read_script("Loom") or ""
    except Exception:                                      # noqa: BLE001
        return ""


def native_free(x, y, w, h) -> dict:
    """Родной элемент плеера (настоящая пластинка, список, кнопки…) — вынут из раскладки и стоит в долях окна."""
    return {"free": True, "hidden": False, "x": x, "y": y, "w": w, "h": h}


def _p_player_ui():
    out = []
    for key, geom, props in (
            ("glass", (0.03, 0.05, 0.46, 0.9), None),
            ("title", (0.06, 0.6, 0.4, 0.1), None),
            ("seek", (0.06, 0.71, 0.4, 0.06), None),
            ("buttons", (0.15, 0.8, 0.06, 0.1), {"action": '"prev"'}),
            ("buttons", (0.23, 0.79, 0.08, 0.12), {"action": '"play"'}),
            ("buttons", (0.33, 0.8, 0.06, 0.1), {"action": '"next"'}),
            ("volume", (0.06, 0.92, 0.18, 0.04), None),
            ("spectrum", (0.52, 0.06, 0.45, 0.18), {"mirror": "true", "bars": "40"}),
            ("lyrics", (0.52, 0.26, 0.45, 0.2), None),
            ("tracks", (0.52, 0.48, 0.45, 0.47), None)):
        L = widget_layer(key, **_geom_layer(*geom))
        if props:
            L["props"].update(props)
            if key == "buttons":
                L["name"] = {"prev": "Назад", "play": "Пауза / играть", "next": "Вперёд"}[props["action"].strip('"')]
        L["group"] = "ui" if key != "glass" else ""
        out.append(L)
    # пластинка — настоящий винил плеера (vinyl.py), а не нарисованная скриптом копия
    return {"layers": out, "native": {"vinyl": native_free(0.06, 0.08, 0.4, 0.5)}}


PRESETS = [
    # key, тема-источник, название, подсказка, функция → {"layers": [...], "background": {...}?, "ui": ...?}
    ("fluid", "Fluid", "Жидкость в цветах обложки", "Живая жидкость из Fluid: цвета — из обложки, всплески — на бит",
     lambda: {"layers": [effect_layer("fluid", react=0.7)]}),
    ("milkdrop", "Winamp", "MilkDrop", "Настоящие пресеты MilkDrop из темы Winamp (клик по слою — следующий пресет)",
     lambda: {"layers": [effect_layer("milkdrop")]}),
    ("winamp_spectrum", "Winamp", "Сегментный спектр", "Зелёно-красный спектр с пиками, как в Winamp 2",
     lambda: {"layers": [widget_layer("winamp_spectrum")]}),
    ("winamp_full", "Winamp", "MilkDrop + спектр + кнопки", "Комбо из Winamp: визуализация на фоне, спектр и кнопки",
     lambda: {"layers": [effect_layer("milkdrop"), widget_layer("winamp_spectrum"),
                         widget_layer("buttons", **_geom_layer(0.07, 0.22, 0.05, 0.08), props={"action": '"prev"'}),
                         widget_layer("buttons", **_geom_layer(0.13, 0.21, 0.06, 0.1), props={"action": '"play"'}),
                         widget_layer("buttons", **_geom_layer(0.2, 0.22, 0.05, 0.08), props={"action": '"next"'})]}),
    ("echoes_wave", "Echoes Music", "«Моя волна»", "Дышащие кольца из Echoes Music",
     lambda: {"layers": [widget_layer("echoes_wave")]}),
    ("echoes_vinyl", "Echoes Music", "Винил", "Настоящая пластинка плеера: крутится, пока играет музыка; "
                                             "обложка — в центре",
     lambda: {"layers": [], "native": {"vinyl": native_free(0.3, 0.12, 0.36, 0.6)}}),
    ("echoes_lyrics", "Echoes Music", "Текст песни", "Строки текста с таймингами, плавная смена",
     lambda: {"layers": [widget_layer("lyrics")]}),
    ("loom", "LOOM", "Ткацкий станок целиком", "Вся тема LOOM: станок, веретено, нить-прогресс, кнопки, список",
     lambda: {"layers": script_layers(_loom_source())}),
    ("player_ui", "Интерфейс", "Плеер из слоёв", "Пластинка, название, перемотка, кнопки, громкость, спектр, текст, "
                                                "список — каждый элемент отдельный слой",
     lambda: dict(_p_player_ui(), ui="layers")),
] + [(k, "Живые обои", t, "Живые обои слоем: можно уменьшить, повернуть, наложить несколько",
      (lambda k=k: {"layers": [effect_layer(k)]})) for k, t in tk.EFFECTS if k in tk.LIVE_BACKGROUNDS]


def preset_groups():
    out = {}
    for p in PRESETS:
        out.setdefault(p[1], []).append(p)
    return list(out.items())


def apply_preset(theme: dict, key: str) -> list[dict]:
    """Добавить слои предустановки в тему (поверх уже имеющихся — эффекты складываются). → новые слои."""
    for k, _grp, _t, _h, fn in PRESETS:
        if k == key:
            data = fn()
            new = [copy.deepcopy(L) for L in data.get("layers", [])]
            for L in new:
                L["id"] = tk.new_layer(L["kind"])["id"]
            theme["layers"].extend(new)
            if data.get("ui"):
                theme["components"]["ui"] = data["ui"]
            for nk, spec in (data.get("native") or {}).items():   # родные элементы (настоящий винил и т.п.)
                theme.setdefault("native", {})[nk] = dict(spec)
            return new
    return []


def copy_layers_from(theme: dict, other: dict, store=None) -> list[dict]:
    """Слои другой своей темы (с их картинками/видео/шрифтами) → в текущую."""
    new = []
    for L in other.get("layers", []):
        D = copy.deepcopy(L)
        D["id"] = tk.new_layer(D["kind"])["id"]
        if store is not None:
            for key in ("src", "font"):
                rel = D.get(key)
                if rel and str(rel).startswith("assets/"):
                    path = store.asset(other, rel)
                    if path:
                        D[key] = store.import_asset(theme, path) or rel
            for pk, pv in list((D.get("props") or {}).items()):
                s = str(pv).strip().strip('"')
                if s.startswith("assets/"):
                    path = store.asset(other, s)
                    if path:
                        D["props"][pk] = _lit_str(store.import_asset(theme, path) or s)
        new.append(D)
    theme["layers"].extend(new)
    return new


# ══════════════════════════════════════════════════════════════════════════ #
#  Перенос старых тем LOOM в конструктор (один раз при запуске)
# ══════════════════════════════════════════════════════════════════════════ #

def migrate_loom_scripts(store, settings: dict) -> dict:
    """Темы LOOM (встроенная «Loom» и скрипты пользователя ~/.neon_player/echoscript/*.echo) → темы
    конструктора. Каждый скрипт переносится один раз (settings["loom_migrated"]).
    → {имя скрипта: имя новой темы}."""
    try:
        import dsl_studio
    except Exception:                                      # noqa: BLE001
        return {}
    done = set(settings.get("loom_migrated") or [])
    made = {}
    for s in dsl_studio.list_scripts():
        if s["path"] in done:
            continue
        try:
            with open(s["path"], encoding="utf-8") as f:
                src = f.read()
            t = script_theme(s["name"], src)
            if not t["layers"]:
                done.add(s["path"])
                continue
            t["name"] = tk.suggest_name({x["name"] for x in store.list()}, s["name"])
            store.save(t)
            made[s["name"]] = t["name"]
        except Exception as e:                             # noqa: BLE001
            print("[layers] перенос LOOM:", s.get("name"), e)
        done.add(s["path"])
    settings["loom_migrated"] = sorted(done)
    return made


def placeholder(p: QPainter, w, h, text, error=False):
    """Рамка-заглушка (нет кода, ошибка, эффект в миниатюре)."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = QRectF(-w / 2, -h / 2, w, h)
    p.setPen(QPen(QColor(255, 90, 110, 200) if error else QColor(255, 255, 255, 120), 1.5, Qt.PenStyle.DashLine))
    p.setBrush(QColor(170, 30, 45, 70) if error else QColor(0, 0, 0, 50))
    p.drawRoundedRect(r, 8, 8)
    if w > 40 and h > 18:
        f = QFont("Segoe UI")
        f.setPixelSize(int(max(9, min(13, h * 0.12))))
        p.setFont(f)
        p.setPen(QColor(255, 220, 225) if error else QColor(235, 235, 245, 200))
        p.drawText(r.adjusted(6, 4, -6, -4), int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap), text)
    p.restore()
