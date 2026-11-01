# osu_skin_import.py
"""
Скины osu! для темы esu!: внешний вид из официальных файлов скина osu!.

Откуда:
  * файл .osk (так скины скачиваются с сайта osu! и экспортируются из osu!lazer: Настройки → Скин → Экспорт);
  * папка скина (например, из osu!/Skins);
  * список скинов установленного osu! (stable) — одной кнопкой.
Скин копируется к себе (~/.neon_player/osu/skins/<имя>), оригинал не трогается.

Что берётся (стандартные имена файлов osu!, @2x — если есть):
  ноты — hitcircle (+ hitcircleoverlay), approachcircle, цифры default-0…9 (HitCirclePrefix/HitCircleOverlap
  из skin.ini), lighting; слайдеры — sliderb (sliderb0), sliderfollowcircle, reversearrow, sliderscorepoint,
  цвета тела SliderBorder / SliderTrackOverride; спиннер — spinner-circle или spinner-bottom/top,
  spinner-approachcircle; followpoint; оценки попаданий hit300/100/50/0; star2; курсор — cursor (+ cursormiddle),
  cursortrail; счёт — score-0…9 (ScorePrefix), комбо (ComboPrefix); полоска здоровья — scorebar-bg,
  scorebar-colour; буквы оценок ranking-X/XH/S/SH/A/B/C/D; фон меню menu-background; цвета комбо Combo1…8;
  хитсаунды и звуки меню (normal-hitnormal.wav и т. д.).
Чего в скине нет — остаётся своим, нарисованным esu!.

Сама отрисовка: FileSkin — наследник osu_skin.Skin с теми же методами (игра, превью и редактор
ничего не знают о файлах): спрайт масштабируется под радиус круга один раз и кэшируется как QPixmap.
"""
from __future__ import annotations

import os
import re
import shutil
import zipfile
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap

import osu_skin as SK

SKINS_DIR = Path.home() / ".neon_player" / "osu" / "skins"
IMG_EXT = (".png", ".jpg", ".jpeg")
SND_EXT = (".wav", ".ogg", ".mp3")
KEEP_EXT = IMG_EXT + SND_EXT + (".ini", ".txt", ".json")    # .json — раскладка интерфейса скинов osu!lazer
MAX_FILE = 30 * 1024 * 1024


# ------------------------------------------------------------------ #
#  skin.ini                                                           #
# ------------------------------------------------------------------ #

def _rgb(s: str):
    try:
        v = [int(float(x)) for x in re.split(r"[,\s]+", s.strip())[:3]]
        return QColor(*[max(0, min(255, c)) for c in v]) if len(v) == 3 else None
    except (ValueError, TypeError):
        return None


def parse_ini(text: str) -> dict:
    """skin.ini → {"general": {...}, "colours": {...}, "fonts": {...}} (ключи — в нижнем регистре)."""
    out = {"general": {}, "colours": {}, "fonts": {}}
    sec = None
    for raw in text.replace("﻿", "").splitlines():
        ln = raw.split("//", 1)[0].strip()
        if not ln:
            continue
        if ln.startswith("[") and ln.endswith("]"):
            sec = ln[1:-1].strip().lower()
            continue
        if sec in out and ":" in ln:
            k, v = ln.split(":", 1)
            out[sec].setdefault(k.strip().lower(), v.strip())
    return out


# ------------------------------------------------------------------ #
#  Файлы скина                                                        #
# ------------------------------------------------------------------ #

class SkinFiles:
    """Папка скина: поиск элементов по имени (без учёта регистра, @2x в приоритете), цвета и шрифты.
    recursive=False — только верхний уровень (папка карты osu!: её скин лежит рядом с .osu)."""

    def __init__(self, folder, recursive: bool = True):
        self.dir = Path(folder)
        self.files: dict[str, dict] = {}
        try:
            items = list(self.dir.rglob("*") if recursive else self.dir.iterdir())
        except OSError:
            items = []
        for p in items:
            if not p.is_file():
                continue
            ext = p.suffix.lower()
            if ext not in IMG_EXT + SND_EXT:
                continue
            rel = str(p.relative_to(self.dir).with_suffix("")).replace("\\", "/").lower()
            hd = rel.endswith("@2x")
            if hd:
                rel = rel[:-3]
            slot = self.files.setdefault(rel, {})
            kind = ("2x" if hd else "1x") if ext in IMG_EXT else "snd"
            old = slot.get(kind)
            if old is None or (ext == ".png" and old.suffix.lower() != ".png"):
                slot[kind] = p
        ini = next((p for p in self.dir.iterdir() if p.is_file() and p.name.lower() == "skin.ini"), None) \
            if self.dir.exists() else None
        try:
            self.ini = parse_ini(ini.read_text("utf-8", errors="ignore")) if ini else parse_ini("")
        except OSError:
            self.ini = parse_ini("")
        g, c, f = self.ini["general"], self.ini["colours"], self.ini["fonts"]
        self.name = g.get("name") or self.dir.name
        self.author = g.get("author", "")
        self.combo = [col for col in (_rgb(c[k]) for k in sorted(
            (k for k in c if re.fullmatch(r"combo\d+", k)), key=lambda k: int(k[5:]))) if col is not None]
        self.slider_border = _rgb(c.get("sliderborder", "")) or QColor(255, 255, 255)
        self.slider_track = _rgb(c.get("slidertrackoverride", ""))
        self.ball_tint = g.get("allowsliderballtint", "0").strip() == "1"
        self.overlay_above = g.get("hitcircleoverlayabovenumber", g.get("hitcircleoverlayabovenumer", "1")) != "0"

        def num(v, d):
            try:
                return float(v)
            except (TypeError, ValueError):
                return d
        self.hc_prefix = f.get("hitcircleprefix", "default").replace("\\", "/").strip().lower()
        self.hc_overlap = num(f.get("hitcircleoverlap"), -2.0)
        self.score_prefix = f.get("scoreprefix", "score").replace("\\", "/").strip().lower()
        self.score_overlap = num(f.get("scoreoverlap"), 0.0)
        self.combo_prefix = f.get("comboprefix", "score").replace("\\", "/").strip().lower()
        self.combo_overlap = num(f.get("combooverlap"), 0.0)
        self._img: dict = {}

    def has(self, *names) -> bool:
        return any(self._slot(n) for n in names)

    def _slot(self, name):
        s = self.files.get(name.lower())
        return s if s and ("1x" in s or "2x" in s) else None

    def image(self, *names) -> tuple[QImage | None, float]:
        """Первый найденный элемент → (картинка, сколько «пикселей 1x» в одном её пикселе)."""
        for n in names:
            s = self._slot(n)
            if not s:
                continue
            key = n.lower()
            if key not in self._img:
                p, k = (s["2x"], 0.5) if "2x" in s else (s["1x"], 1.0)
                img = QImage(str(p))                      # QImage, не QPixmap: декод не держит GIL
                # прозрачная пустышка 1×1 остаётся как есть: скин так «выключает» элемент — он и не виден
                self._img[key] = (img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
                                  if not img.isNull() else None, k)
            img, k = self._img[key]
            if img is not None:
                return img, k
        return None, 1.0

    def sound(self, *names) -> str | None:
        for n in names:
            s = self.files.get(n.lower())
            if s and "snd" in s:
                return str(s["snd"])
        return None

    def path(self, *names) -> str | None:
        for n in names:
            s = self._slot(n)
            if s:
                return str(s.get("2x") or s.get("1x"))
        return None

    def has_gameplay(self) -> bool:
        return self.has("hitcircle", "hitcircleoverlay", "approachcircle", "sliderb", "sliderb0", "default-1",
                        "cursor", "sliderfollowcircle", "reversearrow", "spinner-circle", "spinner-top")


class ChainFiles:
    """Скин карты osu! поверх выбранного скина (как в osu!: элементы из папки карты важнее).
    Настройки skin.ini (цвета тела слайдера, шрифты) — от выбранного скина, если он есть."""

    def __init__(self, first: SkinFiles, base: SkinFiles | None):
        self.chain = [first] + ([base] if base is not None else [])
        ini = base or first
        for k in ("name", "author", "combo", "slider_border", "slider_track", "ball_tint", "overlay_above",
                  "hc_overlap", "score_prefix", "score_overlap", "combo_prefix", "combo_overlap"):
            setattr(self, k, getattr(ini, k))
        self.hc_prefix = ini.hc_prefix
        # цифры нот карты (default-N) — если они есть в папке карты, а у скина свой префикс
        if base is not None and first.has("default-1") and not first.has(f"{ini.hc_prefix}-1"):
            self.hc_prefix = "default"

    def image(self, *names):
        for f in self.chain:
            img, k = f.image(*names)
            if img is not None:
                return img, k
        return None, 1.0

    def has(self, *names) -> bool:
        return any(f.has(*names) for f in self.chain)

    def sound(self, *names):
        for f in self.chain:
            p = f.sound(*names)
            if p:
                return p
        return None

    def path(self, *names):
        for f in self.chain:
            p = f.path(*names)
            if p:
                return p
        return None


# ------------------------------------------------------------------ #
#  Картинки                                                           #
# ------------------------------------------------------------------ #

def tint(img: QImage, col: QColor) -> QImage:
    """Умножение цвета (как osu! красит hitcircle цветом комбо): премультиплицированные каналы × цвет."""
    img = img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    w, h = img.width(), img.height()
    bpl = img.bytesPerLine()
    ptr = img.constBits()
    ptr.setsize(h * bpl)
    a = np.frombuffer(ptr, np.uint8).reshape(h, bpl)[:, :w * 4].reshape(h, w, 4).astype(np.float32)
    k = np.array([col.blue(), col.green(), col.red(), 255], np.float32) / 255.0      # BGRA
    out = np.ascontiguousarray(np.clip(a * k + 0.5, 0, 255).astype(np.uint8))
    return QImage(out.tobytes(), w, h, w * 4, QImage.Format.Format_ARGB32_Premultiplied).copy()


def silhouette(img: QImage, col=QColor(255, 255, 255)) -> QImage:
    out = QImage(img.size(), QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.drawImage(0, 0, img)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    p.fillRect(out.rect(), col)
    p.end()
    return out


def to_pm(img: QImage, w: float, h: float, dpr: float) -> QPixmap:
    """Картинка → QPixmap логического размера w×h (сглаженное масштабирование один раз)."""
    W, H = max(1, int(round(w * dpr))), max(1, int(round(h * dpr)))
    if (W, H) != (img.width(), img.height()):
        img = img.scaled(W, H, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(dpr)
    return pm


def compose(layers, dpr) -> QPixmap:
    """Несколько QPixmap — по центру друг на друге (порядок — снизу вверх)."""
    layers = [x for x in layers if x is not None]
    w = max(x.width() / x.devicePixelRatio() for x in layers)
    h = max(x.height() / x.devicePixelRatio() for x in layers)
    pm = SK._pm(w, h, dpr)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    for x in layers:
        lw, lh = x.width() / x.devicePixelRatio(), x.height() / x.devicePixelRatio()
        p.drawPixmap(QRectF((w - lw) / 2, (h - lh) / 2, lw, lh), x, QRectF(x.rect()))
    p.end()
    return pm


# ------------------------------------------------------------------ #
#  Скин из файлов                                                     #
# ------------------------------------------------------------------ #

class FileSkin(SK.Skin):
    """Те же методы, что у нарисованного Skin, но из файлов скина osu! (чего нет — от родителя).
    Масштаб как в osu!: спрайт нот «1x» 128 px = диаметр круга (R/64 экранных px на px 1x)."""

    def __init__(self, files: SkinFiles, R: float, dpr: float = 1.0, colors=None, ps: float | None = None):
        super().__init__(R, dpr, colors)
        self.f = files
        self.k_obj = self.R / 64.0                         # экранных px на px 1x — для нот
        self.k_pf = ps if ps else self.R / 36.5           # для того, что в osu! не зависит от CS

    def _sprite(self, names, k, col=None):
        img, k1 = self.f.image(*names)
        if img is None:
            return None
        if col is not None:
            img = tint(img, col)
        return to_pm(img, img.width() * k1 * k, img.height() * k1 * k, self.dpr)

    # ── круги ── #
    def _overlay(self):
        return self._get("fover", lambda: self._sprite(["hitcircleoverlay", "hitcircleoverlay-0"], self.k_obj))

    def _split_overlay(self) -> bool:
        """Оверлей над цифрой (HitCircleOverlayAboveNumber, по умолчанию в osu! — да): тогда круг — только
        hitcircle, а оверлей кладётся поверх цифры (игра рисует круг → цифру)."""
        return self.f.overlay_above and self.f.has("hitcircle") and self._overlay() is not None

    def circle(self, ci):
        def mk():
            base = self._sprite(["hitcircle"], self.k_obj, self.color(ci))
            over = None if self._split_overlay() else self._overlay()
            if base is None and over is None:
                return SK.Skin.circle(self, ci)
            return compose([base, over], self.dpr)
        return self._get(("fcircle", ci % len(self.colors)), mk)

    def circle_full(self, ci):
        """Круг целиком (с оверлеем) — для вспышки попадания, где цифры уже нет."""
        if not self._split_overlay():
            return self.circle(ci)
        return self._get(("fcfull", ci % len(self.colors)), lambda: compose([self.circle(ci), self._overlay()],
                                                                              self.dpr))

    def approach(self, ci):
        return self._get(("fappr", ci % len(self.colors)),
                         lambda: self._sprite(["approachcircle"], self.k_obj, self.color(ci))
                         or SK.Skin.approach(self, ci))

    def number(self, n: int, ci=None):
        def mk():
            digits = self._digits(n, ci)
            if self._split_overlay():
                return compose([digits, self._overlay()], self.dpr)
            return digits
        return self._get(("fnum", n, None if ci is None else ci % len(self.colors)), mk)

    def _digits(self, n: int, ci=None) -> QPixmap:
        """Номер комбо из цифр скина (HitCirclePrefix-0…9, HitCircleOverlap), 0.8 размера круга — как в osu!."""
        pre = self.f.hc_prefix
        digs = []
        for ch in str(n):
            img, k1 = self.f.image(f"{pre}-{ch}")
            if img is None:
                return SK.Skin.number(self, n, ci)
            digs.append((img, k1))
        k = self.k_obj * 0.8
        sizes = [(img.width() * k1 * k, img.height() * k1 * k) for img, k1 in digs]
        ov = self.f.hc_overlap * k
        w = sum(s[0] for s in sizes) - ov * (len(sizes) - 1)
        h = max(s[1] for s in sizes)
        pm = SK._pm(max(1.0, w), max(1.0, h), self.dpr)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        x = 0.0
        for (img, _k1), (dw, dh) in zip(digs, sizes):
            p.drawImage(QRectF(x, (h - dh) / 2, dw, dh), img)
            x += dw - ov
        p.end()
        return pm

    def white_circle(self):
        def mk():
            img, k1 = self.f.image("hitcircle")
            if img is None:
                return SK.Skin.white_circle(self)
            return to_pm(silhouette(img), img.width() * k1 * self.k_obj, img.height() * k1 * self.k_obj, self.dpr)
        return self._get("fwhite", mk)

    def hit_light(self, ci):
        return self._get(("flight", ci % len(self.colors)),
                         lambda: self._sprite(["lighting"], self.k_obj * 1.6, self.color(ci))
                         or SK.Skin.hit_light(self, ci))

    # ── слайдеры ── #
    def slider_body(self, pts, ci, cheap=False):
        """Тело как в osu!stable: тень, рамка SliderBorder, внутри — градиент цвета дорожки
        (SliderTrackOverride или цвет комбо) от затемнённого края к светлой середине, 70 % непрозрачности."""
        R = self.R
        track = self.f.slider_track or self.color(ci)
        border = self.f.slider_border
        x0, y0 = float(pts[:, 0].min()) - R - 2, float(pts[:, 1].min()) - R - 2
        w, h = float(pts[:, 0].max()) - x0 + R + 2, float(pts[:, 1].max()) - y0 + R + 2
        if SK.native_bodies():
            outer = SK.darker(track, 0.1)
            inner = QColor(min(255, int(track.red() * 1.125 + 64)), min(255, int(track.green() * 1.125 + 64)),
                           min(255, int(track.blue() * 1.125 + 64)))
            bc = QColor(border)
            prof = SK._NF.body_profile(60 / 255, (bc.redF(), bc.greenF(), bc.blueF(), bc.alphaF()), 0.92,
                                       SK._rgbf(outer), SK._rgbf(inner), 180 / 255, 180 / 255, 0.80, 0.9)
            return SK.body_pixmap(pts, x0, y0, w, h, R, self.dpr, prof)
        pm = SK._pm(w, h, self.dpr)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath(QPointF(pts[0, 0] - x0, pts[0, 1] - y0))
        step = max(1, len(pts) // 160)
        for q in pts[step::step]:
            path.lineTo(QPointF(q[0] - x0, q[1] - y0))
        if len(pts) > 1:
            path.lineTo(QPointF(pts[-1, 0] - x0, pts[-1, 1] - y0))
        cap, join = Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin
        p.strokePath(path, QPen(QColor(0, 0, 0, 60), R * 2, Qt.PenStyle.SolidLine, cap, join))
        p.strokePath(path, QPen(border, R * 2 * 0.92, Qt.PenStyle.SolidLine, cap, join))
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        outer = SK.darker(track, 0.1)
        inner = QColor(min(255, int(track.red() * 1.125 + 64)), min(255, int(track.green() * 1.125 + 64)),
                       min(255, int(track.blue() * 1.125 + 64)))
        layers = 7 if cheap else 14
        for i in range(layers):
            k = i / max(1, layers - 1)
            c = QColor(int(outer.red() + (inner.red() - outer.red()) * k),
                       int(outer.green() + (inner.green() - outer.green()) * k),
                       int(outer.blue() + (inner.blue() - outer.blue()) * k), 180)
            wd = R * 2 * 0.80 * (1 - 0.9 * k)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, i == 0)
            p.strokePath(path, QPen(c, max(1.0, wd), Qt.PenStyle.SolidLine, cap, join))
        p.end()
        return pm, QPointF(x0, y0)

    def slider_ball(self, ci):
        if not SK.SKIN_BALL:                               # объёмный «шар» скина — только если включён
            return SK.Skin.slider_ball(self, ci)

        def mk():
            ball = self._sprite(["sliderb", "sliderb0"], self.k_obj, self.color(ci) if self.f.ball_tint else None)
            if ball is None:
                return SK.Skin.slider_ball(self, ci)
            nd = self._sprite(["sliderb-nd"], self.k_obj, QColor(5, 5, 5))
            return compose([nd, ball], self.dpr)
        return self._get(("fball", ci % len(self.colors)), mk)

    def follow_circle(self):
        return self._get("ffollow", lambda: self._sprite(["sliderfollowcircle", "sliderfollowcircle-0"], self.k_obj)
                         or SK.Skin.follow_circle(self))

    def reverse_arrow(self):
        return self._get("frev", lambda: self._sprite(["reversearrow"], self.k_obj) or SK.Skin.reverse_arrow(self))

    def tick(self):
        return self._get("ftick", lambda: self._sprite(["sliderscorepoint"], self.k_obj) or SK.Skin.tick(self))

    def followpoint(self):
        return self._get("ffp", lambda: self._sprite(["followpoint", "followpoint-0"], self.k_pf)
                         or SK.Skin.followpoint(self))

    # ── судейство ── #
    def judgement(self, kind: int):
        names = {300: ["hit300", "hit300-0"], 100: ["hit100", "hit100-0"], 50: ["hit50", "hit50-0"],
                 0: ["hit0", "hit0-0"], "300g": ["hit300g", "hit300g-0"], "300k": ["hit300k", "hit300k-0"],
                 "100k": ["hit100k", "hit100k-0"]}[kind]
        return self._get(("fj", kind), lambda: self._sprite(names, self.k_pf * 0.85) or SK.Skin.judgement(self, kind))

    def star2(self):
        return self._get("fstar2", lambda: self._sprite(["star2"], self.k_obj) or SK.Skin.star2(self))

    # ── курсор ── #
    def cursor(self, scale=1.0):
        def mk():
            k = 900.0 / 768.0 * scale                      # игра рисует курсор ×(высота/900): итог — как в osu!
            cur = self._sprite(["cursor"], k)
            if cur is None:
                return SK.Skin.cursor(self, scale)
            mid = self._sprite(["cursormiddle"], k)
            return compose([cur, mid], self.dpr)
        return self._get(("fcursor", scale), mk)

    def trail(self, scale=1.0):
        return self._get(("ftrail", scale), lambda: self._sprite(["cursortrail"], 900.0 / 768.0 * scale)
                         or SK.Skin.trail(self, scale))

    # ── спиннер ── #
    def spinner_disc(self, size):
        def mk():
            def fit(names, frac=1.0):
                img, _k = self.f.image(*names)
                if img is None:
                    return None
                s = size * frac / max(img.width(), img.height())
                return to_pm(img, img.width() * s, img.height() * s, self.dpr)
            old = fit(["spinner-circle"])
            if old is not None:
                return old
            layers = [fit(["spinner-bottom"]), fit(["spinner-top"], 0.98), fit(["spinner-middle2"], 0.12)]
            if any(x is not None for x in layers):
                return compose(layers, self.dpr)
            return SK.Skin.spinner_disc(self, size)
        return self._get(("fdisc", int(size)), mk)

    def ring(self, size, color, width):
        def mk():
            img, _k = self.f.image("spinner-approachcircle")
            if img is None:
                return SK.Skin.ring(self, size, color, width)
            s = size / max(img.width(), img.height())
            return to_pm(img, img.width() * s, img.height() * s, self.dpr)
        return self._get(("fring", int(size)), mk)

    # ── HUD ── #
    def hp_bar(self, H: float):
        """(фон, заливка, смещение заливки) полоски здоровья — scorebar-bg / scorebar-colour, или None."""
        def mk():
            k = H / 768.0
            bg = self._sprite(["scorebar-bg"], k)
            col = self._sprite(["scorebar-colour", "scorebar-colour-0"], k)
            if bg is None or col is None:
                return None
            new_style = self.f.has("scorebar-marker")
            off = (12 * k, 13 * k) if new_style else (5 * k, 16 * k)
            return bg, col, off
        return self._get(("fhp", int(H)), mk)


# ------------------------------------------------------------------ #
#  Активный скин                                                      #
# ------------------------------------------------------------------ #

_FILES_CACHE: dict = {}
_MAP_FILES: dict = {}


def map_files(folder) -> SkinFiles | None:
    """Скин карты osu! — картинки нот в папке карты (если есть), иначе None. Кэш по папке."""
    key = str(folder or "")
    if not key:
        return None
    if key not in _MAP_FILES:
        sf = SkinFiles(folder, recursive=False)
        if len(_MAP_FILES) >= 16:
            _MAP_FILES.pop(next(iter(_MAP_FILES)))
        _MAP_FILES[key] = sf if sf.has_gameplay() else None
    return _MAP_FILES[key]


def skin_dir(name: str) -> Path:
    return SKINS_DIR / name


def activate(name: str | None) -> SkinFiles | None:
    """Сделать скин активным (None/"" — встроенный esu!). Возвращает его файлы."""
    sf = None
    if name:
        d = skin_dir(name)
        if d.is_dir():
            sig = (str(d), _dir_sig(d))
            sf = _FILES_CACHE.get(sig)
            if sf is None:
                sf = SkinFiles(d)
                _FILES_CACHE.clear()
                _FILES_CACHE[sig] = sf
    SK.ACTIVE = sf
    return sf


def _dir_sig(d: Path):
    try:
        return int(d.stat().st_mtime), sum(1 for _ in d.iterdir())
    except OSError:
        return 0


def patch_digits(dg, kind: str = "score", files=None) -> bool:
    """Глифы счёта/комбо из скина (score-0…9, score-comma/dot/percent/x) — в готовый Digits игры.
    files — файлы скина (по умолчанию активный; в игре — вместе со скином карты)."""
    sf = files if files is not None else SK.ACTIVE
    if sf is None:
        return False
    pre = sf.combo_prefix if kind == "combo" else sf.score_prefix
    ov = sf.combo_overlap if kind == "combo" else sf.score_overlap
    names = {str(i): f"{pre}-{i}" for i in range(10)}
    names.update({".": f"{pre}-dot", ",": f"{pre}-comma", "%": f"{pre}-percent", "x": f"{pre}-x"})
    imgs = {}
    for ch, nm in names.items():
        img, k1 = sf.image(nm)
        if img is not None:
            imgs[ch] = (img, k1)
    if not all(str(i) in imgs for i in range(10)):
        return False
    h = dg.size * 1.25
    ref = imgs["0"][0].height() * imgs["0"][1]
    k = h / max(1.0, ref)
    dpr = getattr(dg, "_dpr", 1.0)
    g, adv = {}, {}
    for ch, (img, k1) in imgs.items():
        w, hh = img.width() * k1 * k, img.height() * k1 * k
        pm = SK._pm(w, h, dpr)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(QRectF(0, h - hh, w, hh), img)
        p.end()
        g[ch] = pm
        adv[ch] = max(1.0, w - ov * k)
    dg.g.update(g)
    dg.adv.update(adv)
    dg.h, dg.pad = h, 0.0
    dg._lines = {}
    return True


def grade(g: str, size: float, dpr: float) -> QPixmap | None:
    sf = SK.ACTIVE
    if sf is None:
        return None
    name = {"SS": "x", "SSH": "xh", "S": "s", "SH": "sh"}.get(g, g.lower())
    order = [f"ranking-{name}-small", f"ranking-{name}"] if size < 40 else [f"ranking-{name}", f"ranking-{name}-small"]
    img, k1 = sf.image(*order)
    if img is None:
        return None
    k = size * 1.15 / max(1.0, img.height() * k1)
    return to_pm(img, img.width() * k1 * k, img.height() * k1 * k, dpr)


def menu_background() -> str | None:
    sf = SK.ACTIVE
    return sf.path("menu-background") if sf is not None else None


def sound_map(settings=None) -> dict:
    """Звуки активного скина → {ключ банка esu!: путь} (имена — как в osu_y2k.HITSOUNDS)."""
    sf = SK.ACTIVE
    if sf is None:
        return {}
    try:
        from osu_y2k import HITSOUNDS
    except Exception:                                      # noqa: BLE001
        return {}
    out = {}
    for key, _title, names in HITSOUNDS:
        p = sf.sound(*names)
        if p:
            out[key] = p
    # по наборам, как в игре: soft-hitclap → «soft-clap», drum-hitnormal → «drum» и т. д.
    for st in ("normal", "soft", "drum"):
        p = sf.sound(f"{st}-hitnormal")
        if p:
            out[st] = p
        for add, fname in (("whistle", "hitwhistle"), ("finish", "hitfinish"), ("clap", "hitclap"),
                           ("tick", "slidertick")):
            p = sf.sound(f"{st}-{fname}")
            if p:
                out[f"{st}-{add}"] = p
    return out


# ------------------------------------------------------------------ #
#  Импорт                                                             #
# ------------------------------------------------------------------ #

def _safe(s: str) -> str:
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", s or "").strip().strip(".")
    return s[:60] or "skin"


def _unique(name: str) -> str:
    base = _safe(name)
    n, out = 1, base
    while (SKINS_DIR / out).exists():
        n += 1
        out = f"{base} ({n})"
    return out


def _ini_name(text: str, fallback: str) -> str:
    return parse_ini(text)["general"].get("name") or fallback


def import_osk(path: str) -> str:
    """Файл .osk (zip) → папка скина. Возвращает имя (папку) скина."""
    def safe(n: str) -> bool:                              # защита от путей «наружу» из архива
        parts = n.split("/")
        return bool(n) and not n.startswith("/") and ":" not in n and ".." not in parts
    with zipfile.ZipFile(path) as z:
        infos = [i for i in z.infolist() if not i.is_dir() and safe(i.filename.replace("\\", "/"))]
        names = [i.filename.replace("\\", "/") for i in infos]
        # весь скин в одной папке внутри архива — её убираем
        tops = {n.split("/", 1)[0] for n in names if "/" in n}
        strip = ""
        if len(tops) == 1 and all("/" in n for n in names):
            strip = next(iter(tops)) + "/"
        ini = next((i for i, n in zip(infos, names) if n[len(strip):].lower() == "skin.ini"), None)
        title = Path(path).stem
        if ini is not None:
            try:
                title = _ini_name(z.read(ini).decode("utf-8", errors="ignore"), title)
            except Exception:                              # noqa: BLE001
                pass
        name = _unique(title)
        dst = SKINS_DIR / name
        dst.mkdir(parents=True, exist_ok=True)
        n_ok = 0
        for i, n in zip(infos, names):
            rel = n[len(strip):] if n.startswith(strip) else n
            parts = [p for p in rel.split("/") if p not in ("", ".")]
            if not parts:
                continue
            if Path(parts[-1]).suffix.lower() not in KEEP_EXT or i.file_size > MAX_FILE:
                continue
            out = dst.joinpath(*parts)
            out.parent.mkdir(parents=True, exist_ok=True)
            with z.open(i) as src, open(out, "wb") as f:
                shutil.copyfileobj(src, f)
            n_ok += 1
    if n_ok == 0:
        shutil.rmtree(dst, ignore_errors=True)
        raise ValueError("в архиве нет файлов скина osu!")
    return name


def import_dir(folder: str) -> str:
    """Папка скина (например, osu!/Skins/<скин>) → копия у себя. Возвращает имя скина."""
    src = Path(folder)
    ini = next((p for p in src.iterdir() if p.is_file() and p.name.lower() == "skin.ini"), None)
    title = src.name
    if ini is not None:
        try:
            title = _ini_name(ini.read_text("utf-8", errors="ignore"), title)
        except OSError:
            pass
    name = _unique(title)
    dst = SKINS_DIR / name
    n_ok = 0
    for p in src.rglob("*"):
        if p.is_file() and p.suffix.lower() in KEEP_EXT:
            try:
                if p.stat().st_size > MAX_FILE:
                    continue
            except OSError:
                continue
            out = dst / p.relative_to(src)
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, out)
            n_ok += 1
    if n_ok == 0:
        raise ValueError("в папке нет файлов скина osu!")
    return name


def import_any(path: str) -> str:
    p = Path(path)
    if p.is_dir():
        return import_dir(str(p))
    if p.suffix.lower() in (".osk", ".zip"):
        return import_osk(str(p))
    raise ValueError("это не скин osu! (нужен файл .osk или папка скина)")


def list_skins() -> list[dict]:
    out = []
    if SKINS_DIR.is_dir():
        for d in sorted(SKINS_DIR.iterdir(), key=lambda x: x.name.lower()):
            if d.is_dir():
                ini = next((p for p in d.iterdir() if p.is_file() and p.name.lower() == "skin.ini"), None)
                g = {}
                if ini is not None:
                    try:
                        g = parse_ini(ini.read_text("utf-8", errors="ignore"))["general"]
                    except OSError:
                        pass
                out.append({"name": d.name, "title": g.get("name") or d.name, "author": g.get("author", "")})
    return out


def remove_skin(name: str):
    d = SKINS_DIR / name
    if d.is_dir() and d.parent == SKINS_DIR:
        shutil.rmtree(d, ignore_errors=True)


def osu_skins_dir() -> Path | None:
    """Папка Skins установленного osu! (stable)."""
    cands = []
    try:
        import osu_beatmap as OB
        sd = OB.find_osu_songs()
        if sd:
            cands.append(Path(sd).parent / "Skins")
    except Exception:                                      # noqa: BLE001
        pass
    cands.append(Path(os.environ.get("LOCALAPPDATA", "")) / "osu!" / "Skins")
    for c in cands:
        if c.is_dir():
            return c
    return None


def osu_installed_skins() -> list[Path]:
    d = osu_skins_dir()
    if d is None:
        return []
    out = []
    for p in sorted(d.iterdir(), key=lambda x: x.name.lower()):
        if p.is_dir() and any(f.suffix.lower() in IMG_EXT + (".ini",) for f in p.iterdir() if f.is_file()):
            out.append(p)
        elif p.is_file() and p.suffix.lower() == ".osk":
            out.append(p)
    return out
