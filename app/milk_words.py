# milk_words.py
"""
«Слова в MilkDrop»: на треках с синхронизированным текстом слова в такт
вылетают в случайных местах окна MilkDrop крупными стильными шрифтами,
а их силуэт ещё и «вжигается» в буфер обратной связи — дальше MilkDrop сам
растягивает и плавит буквы, как в настоящих пресетах с текстом.

Шрифты подбираются под настроение трека (music_intel: энергия/позитив/жанр):
  агрессивное — Rubik Glitch / Burned / Distressed, Black Ops One…
  хайп/вечеринка — Rubik Mono One, Russo One, Bungee, Monoton, Press Start 2P…
  грустное — Bad Script, Marck Script, Caveat, Amatic SC, Rubik Puddles…
  мечтательное — Pacifico, Lobster, Comfortaa, Rubik Bubbles, Yeseva One…
Для кириллицы берутся только шрифты, где она реально есть (проверка через
QFontDatabase.writingSystems), латиница может брать любые.

Шрифты (OFL, Google Fonts) один раз скачиваются в ~/.neon_player/fonts в
фоне. Пока не скачались — используются системные (Impact, Segoe Script,
Bahnschrift, Ink Free…), так что режим работает сразу.
"""
from __future__ import annotations

import math
import random
import re
import threading
import time
from pathlib import Path

import numpy as np
from PyQt6.QtCore import Qt, QPointF, QRectF
from PyQt6.QtGui import (QColor, QFont, QFontDatabase, QFontMetricsF, QImage, QPainter,
                         QPainterPath, QPen, QBrush, QLinearGradient, QTransform)

FONT_DIR = Path.home() / ".neon_player" / "fonts"
USER_FONT_DIR = FONT_DIR / "user"          # шрифты, загруженные пользователем

# настройки режима «Слова» по умолчанию
ZONES = ("tl", "t", "tr", "l", "c", "r", "bl", "b", "br")
DEFAULT_CFG = {
    "zones": list(ZONES),       # где появляются слова (сетка 3×3)
    "fly": "none",              # откуда влетают: none/random/left/right/top/bottom/center
    "anim": "random",           # random/pop/slam/rise/spin/glitch/stretch/fade
    "font_main": "auto",        # шрифт обычных слов ("auto" — под настроение трека)
    "font_accent": "auto",      # шрифт последнего слова строки
    "upper": "auto",            # auto/upper/as_is/lower
    "color": "visual",          # visual/mood/custom
    "color_custom": "#ff3fa4",
    "size": 100,                # % размера
    "life": 100,                # % времени на экране
    "max_words": 6,
    "tilt": True,               # наклон слов
    "stamp": True,              # «вжигать» слова в картинку MilkDrop
}


def merged_cfg(cfg: dict | None) -> dict:
    out = dict(DEFAULT_CFG)
    if isinstance(cfg, dict):
        out.update({k: v for k, v in cfg.items() if k in DEFAULT_CFG})
    return out

# семейство → настроения
# семейство → роли: настроение + primary (основной, читаемый) / accent (для акцентов)
FONT_CATALOG = {
    # агрессивное: рубленые, плакатные; рваный Rubik Glitch — только для акцентов
    "Russo One": "aggr hype neutral primary",
    "Bebas Neue": "aggr neutral hype primary",
    "Oswald": "aggr neutral primary",
    "Black Ops One": "aggr primary",
    "Rubik Distressed": "aggr accent",
    "Rubik Glitch": "aggr hyper accent",
    # хайп / вечеринка
    "Rubik Mono One": "hype neutral primary",
    "Unbounded": "hype hyper neutral primary",
    "Bungee": "hype primary",
    "Press Start 2P": "hyper hype accent",
    "Rubik 80s Fade": "hyper dream accent",
    # грустное: рукописные
    "Caveat": "sad neutral primary",
    "Bad Script": "sad primary",
    "Marck Script": "sad dream accent",
    "Amatic SC": "sad accent",
    "Neucha": "sad primary",
    # мечтательное: мягкие, с засечками
    "Comfortaa": "dream hyper primary",
    "Cormorant Garamond": "dream sad primary",
    "Yeseva One": "dream accent",
    "Pacifico": "dream accent",
    "Lobster": "dream accent",
}

# шрифтам-«плакатам» идёт ВЕРХНИЙ регистр
UPPER_FONTS = {"Russo One", "Bebas Neue", "Oswald", "Black Ops One", "Rubik Mono One",
               "Unbounded", "Bungee", "Rubik Distressed", "Rubik Glitch", "Impact",
               "Bahnschrift", "Arial Black", "Segoe UI Black", "Franklin Gothic Heavy"}

# системные запасные (Windows): есть сразу, без скачивания
SYSTEM_FONTS = {
    "Impact": "aggr hype neutral primary", "Bahnschrift": "neutral hype dream primary",
    "Segoe Script": "sad dream primary", "Ink Free": "sad dream accent",
    "Segoe UI Black": "hype neutral hyper primary", "Arial Black": "hype aggr accent",
    "Franklin Gothic Heavy": "aggr neutral accent", "Gabriola": "dream accent",
    "Segoe Print": "sad accent",
}

_GH_FALLBACK = {   # если Google Fonts CSS не отдал TTF — прямые файлы репозитория google/fonts
    "Rubik Glitch": "ofl/rubikglitch/RubikGlitch-Regular.ttf",
    "Rubik Burned": "ofl/rubikburned/RubikBurned-Regular.ttf",
    "Rubik Mono One": "ofl/rubikmonoone/RubikMonoOne-Regular.ttf",
    "Russo One": "ofl/russoone/RussoOne-Regular.ttf",
    "Press Start 2P": "ofl/pressstart2p/PressStart2P-Regular.ttf",
    "Caveat": "ofl/caveat/Caveat[wght].ttf",
    "Comfortaa": "ofl/comfortaa/Comfortaa[wght].ttf",
    "Unbounded": "ofl/unbounded/Unbounded[wght].ttf",
    "Oswald": "ofl/oswald/Oswald[wght].ttf",
    "Bebas Neue": "ofl/bebasneue/BebasNeue-Regular.ttf",
    "Bad Script": "ofl/badscript/BadScript-Regular.ttf",
    "Marck Script": "ofl/marckscript/MarckScript-Regular.ttf",
    "Pacifico": "ofl/pacifico/Pacifico-Regular.ttf",
    "Lobster": "ofl/lobster/Lobster-Regular.ttf",
    "Monoton": "ofl/monoton/Monoton-Regular.ttf",
    "Black Ops One": "ofl/blackopsone/BlackOpsOne-Regular.ttf",
    "Bungee": "ofl/bungee/Bungee-Regular.ttf",
}


def _slug(family: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", family)


class FontBank:
    """Скачивание, регистрация и выбор шрифтов. Регистрация — только в GUI-потоке
    (poll() вызывается из таймера виджета)."""

    _inst = None

    @classmethod
    def get(cls) -> "FontBank":
        if cls._inst is None:
            cls._inst = FontBank()
        return cls._inst

    def __init__(self):
        self._ready_files: list[Path] = []
        self._lock = threading.Lock()
        self._registered: set[str] = set()
        self._user_fams: dict[str, list] = {}
        self._families: dict[str, str] = {}        # семейство → настроения
        self._cyr: dict[str, bool] = {}
        self._started = False
        self._scanned = False
        self.rng = random.Random()

    # ── загрузка ── #

    def start(self):
        if self._started:
            return
        self._started = True
        threading.Thread(target=self._download_all, daemon=True, name="font-dl").start()

    def _download_all(self):
        try:
            import requests
        except Exception:
            return
        FONT_DIR.mkdir(parents=True, exist_ok=True)
        for fam in FONT_CATALOG:
            path = FONT_DIR / f"{_slug(fam)}.ttf"
            if path.exists() and path.stat().st_size > 1000:
                with self._lock:
                    self._ready_files.append(path)
                continue
            data = None
            try:
                css = requests.get("https://fonts.googleapis.com/css2",
                                   params={"family": fam}, timeout=10,
                                   headers={"User-Agent": "Wget/1.21"}).text
                urls = re.findall(r"url\((https://[^)]+?\.(?:ttf|otf))\)", css)
                if urls:
                    r = requests.get(urls[0], timeout=20)
                    if r.status_code == 200 and len(r.content) > 1000:
                        data = r.content
            except Exception:
                data = None
            if data is None and fam in _GH_FALLBACK:
                try:
                    r = requests.get("https://raw.githubusercontent.com/google/fonts/main/"
                                     + _GH_FALLBACK[fam], timeout=20)
                    if r.status_code == 200 and len(r.content) > 1000:
                        data = r.content
                except Exception:
                    data = None
            if data:
                try:
                    tmp = path.with_suffix(".part")
                    tmp.write_bytes(data)
                    tmp.replace(path)
                    with self._lock:
                        self._ready_files.append(path)
                except Exception:
                    pass
            time.sleep(0.05)

    def poll(self):
        """GUI-поток: зарегистрировать скачанное, один раз — системные шрифты."""
        if not self._scanned:
            self._scanned = True
            have = set(QFontDatabase.families())
            for fam, moods in SYSTEM_FONTS.items():
                if fam in have:
                    self._add_family(fam, moods)
            # уже лежащие в папке (с прошлых запусков)
            if FONT_DIR.is_dir():
                with self._lock:
                    self._ready_files += [p for p in FONT_DIR.glob("*.ttf")]
            # свои шрифты пользователя — регистрируем сразу (их немного)
            if USER_FONT_DIR.is_dir():
                for f in sorted(list(USER_FONT_DIR.glob("*.ttf")) + list(USER_FONT_DIR.glob("*.otf"))):
                    self._register_user(f)
        with self._lock:
            # не больше двух за вызов — регистрация шрифта стоит десятки мс
            files, self._ready_files = self._ready_files[:2], self._ready_files[2:]
        for f in files:
            if str(f) in self._registered:
                continue
            self._registered.add(str(f))
            fid = QFontDatabase.addApplicationFont(str(f))
            if fid < 0:
                continue
            for fam in QFontDatabase.applicationFontFamilies(fid):
                moods = FONT_CATALOG.get(fam)
                if moods is None:
                    # имя семейства может отличаться регистром/пробелами
                    key = next((k for k in FONT_CATALOG if _slug(k).lower() == _slug(fam).lower()), None)
                    moods = FONT_CATALOG.get(key)
                if moods:                    # шрифты, убранные из каталога, не используем
                    self._add_family(fam, moods)

    def _register_user(self, f: Path) -> list[str]:
        if str(f) in self._registered:
            return self._user_fams.get(str(f), [])
        self._registered.add(str(f))
        fid = QFontDatabase.addApplicationFont(str(f))
        if fid < 0:
            return []
        fams = list(QFontDatabase.applicationFontFamilies(fid))
        self._user_fams[str(f)] = fams
        for fam in fams:
            self._add_family(fam, "user")
        return fams

    def add_user_font(self, src: str) -> list[str]:
        """Скопировать .ttf/.otf в ~/.neon_player/fonts/user и подключить.
        Возвращает имена семейств (пусто — файл не шрифт)."""
        import shutil
        srcp = Path(src)
        USER_FONT_DIR.mkdir(parents=True, exist_ok=True)
        dst = USER_FONT_DIR / srcp.name
        if srcp.resolve() != dst.resolve():
            shutil.copyfile(srcp, dst)
        fams = self._register_user(dst)
        if not fams:
            try:
                dst.unlink()
            except OSError:
                pass
        return fams

    def families(self) -> dict:
        """Подключённые шрифты: семейство → роли («user» — свои)."""
        self.poll()
        return dict(self._families)

    def has_cyrillic(self, fam: str) -> bool:
        if fam not in self._cyr:
            try:
                self._cyr[fam] = QFontDatabase.WritingSystem.Cyrillic in QFontDatabase.writingSystems(fam)
            except Exception:
                self._cyr[fam] = False
        return self._cyr[fam]

    def _add_family(self, fam, moods):
        self._families[fam] = moods
        try:
            ws = QFontDatabase.writingSystems(fam)
            self._cyr[fam] = QFontDatabase.WritingSystem.Cyrillic in ws
        except Exception:
            self._cyr[fam] = False

    # ── выбор ── #

    def pick(self, mood: str, role: str, cyr: bool, avoid: str = "") -> str:
        """Шрифт под настроение и роль (primary/accent). cyr — нужна кириллица."""
        def ok(f):
            return not cyr or self._cyr.get(f)
        fams = self._families
        for cond in (lambda m: mood in m and role in m, lambda m: mood in m,
                     lambda m: role in m and "neutral" in m, lambda m: "primary" in m):
            pool = [f for f, m in fams.items() if m != "user" and cond(m.split()) and ok(f) and f != avoid]
            if pool:
                nice = [f for f in pool if f not in SYSTEM_FONTS]
                return self.rng.choice(nice or pool)
        return "Arial Black"

    def generation(self) -> int:
        return len(self._families)


# ── настроение трека → стиль ─────────────────────────────────────────────── #

PALETTES = {
    "aggr":   [("#ffffff", "#ff2244"), ("#ff3355", "#000000"), ("#e6ff00", "#ff0055"),
               ("#ffffff", "#7a00ff")],
    "hype":   [("#ffffff", "#ff00d4"), ("#fff200", "#ff3d00"), ("#00ffd5", "#2b00ff"),
               ("#ffffff", "#00b7ff")],
    "hyper":  [("#ff8afc", "#5b00ff"), ("#b6ff00", "#ff00aa"), ("#ffffff", "#00e1ff"),
               ("#fffb00", "#ff00f2")],
    "sad":    [("#e8f0ff", "#3a6dff"), ("#ffffff", "#5d7a99"), ("#c9d6ff", "#7b5cff"),
               ("#f2f2f2", "#2c3e66")],
    "dream":  [("#fff0fb", "#ff7ad9"), ("#e8fffd", "#5ad7ff"), ("#fff7d6", "#ff9f5a"),
               ("#f4e8ff", "#a45cff")],
    "neutral": [("#ffffff", "#00a2ff"), ("#ffffff", "#ff3fa4"), ("#fff4c2", "#ff7a00"),
                ("#e5fff0", "#00c27a")],
}


def mood_bucket(prof: dict | None) -> str:
    if not prof:
        return "neutral"
    fams = prof.get("families") or []
    if fams:
        f = fams[0]
        if f in ("hyperpop",):
            return "hyper"
        if f in ("metal", "metalcore", "phonk", "dnb", "dubstep", "drill", "punk"):
            return "aggr"
        if f in ("lofi", "ambient", "dreampop", "synthwave", "jpop"):
            return "dream"
        if f in ("cloudrap", "emo") and prof.get("energy", 0.5) < 0.6:
            return "sad"
    e, v = prof.get("energy", 0.5), prof.get("valence", 0.5)
    if e >= 0.62 and v < 0.45:
        return "aggr"
    if e >= 0.58:
        return "hype"
    if e < 0.42 and v < 0.48:
        return "sad"
    if e < 0.48 and v >= 0.5:
        return "dream"
    return "neutral"


# ── одно летающее слово ──────────────────────────────────────────────────── #

_SMALL = re.compile(r"^[\w']{1,2}$|^(?:the|and|of|to|in|a|an|on|for|is|it|не|на|и|в|с|по|за|от|до|из|я|ты|мы|он|же|бы|ли|но|а)$",
                    re.I)




class FlyingWord:
    STYLES = ("pop", "slam", "rise", "spin", "glitch", "stretch")

    def __init__(self, img: QImage, cx: float, cy: float, angle: float, life: float, style: str,
                 drift: tuple[float, float], born: float, fly: tuple[float, float] = (0.0, 0.0)):
        self.fx, self.fy = fly                   # смещение старта влёта (в долях окна)
        self.img = img
        self.cx, self.cy = cx, cy               # центр в долях окна
        self.angle = angle
        self.life = life
        self.style = style
        self.vx, self.vy = drift
        self.born = born

    def alive(self, now):
        return now - self.born < self.life

    def draw(self, p: QPainter, W: int, H: int, now: float, beat: float):
        t = now - self.born
        if t < 0:
            return
        inn, out = 0.12, 0.40                    # быстрое появление — слово «попадает» в долю
        flying = bool(self.fx or self.fy)
        if flying or self.style == "fade":
            inn = 0.24
        k_in = min(1.0, t / inn)
        e_in = 1 - (1 - k_in) ** 3
        k_out = max(0.0, (t - (self.life - out)) / out)
        alpha = min(1.0, k_in * 1.6) * (1 - k_out)
        sc = 1.0
        dy = 0.0
        rot = self.angle
        st = self.style
        if st == "pop":
            sc = 0.55 + 0.45 * e_in + 0.06 * math.sin(min(1, t / 0.3) * math.pi)
        elif st == "slam":
            sc = 1.9 - 0.9 * e_in
        elif st == "rise":
            dy = (1 - e_in) * 0.06
        elif st == "spin":
            rot = self.angle + (1 - e_in) * 90
            sc = 0.7 + 0.3 * e_in
        sc *= 1.0 + 0.18 * k_out + 0.05 * beat
        x = (self.cx + self.vx * t + self.fx * (1 - e_in)) * W
        y = (self.cy + self.vy * t + dy + self.fy * (1 - e_in)) * H
        iw, ih = self.img.width(), self.img.height()
        p.save()
        p.setOpacity(max(0.0, min(1.0, alpha)))
        p.translate(x, y)
        if rot:
            p.rotate(rot)
        if st == "stretch":
            p.scale(sc * (1.5 - 0.5 * e_in), sc * (0.5 + 0.5 * e_in))
        elif sc != 1.0:
            p.scale(sc, sc)
        if st == "glitch" and t < 0.3 and int(t * 40) % 3 != 0:
            j = (1 - t / 0.3) * ih * 0.10
            p.setOpacity(alpha * 0.55)
            p.drawImage(QPointF(-iw / 2 - j, -ih / 2), self.img)
            p.drawImage(QPointF(-iw / 2 + j, -ih / 2 + j * 0.3), self.img)
            p.setOpacity(alpha)
        p.drawImage(QPointF(-iw / 2, -ih / 2), self.img)
        p.restore()


def render_word(text: str, family: str, px: int, fill: str, glow: str) -> QImage:
    """Картинка слова: мягкое свечение + тонкая обводка + градиентная заливка.
    Свечение — уменьшенная и растянутая обратно копия силуэта (дешёвый блюр),
    а не толстые обводки: на «рваных» шрифтах те стоили сотни мс.
    Безопасно вызывать из фонового потока (рисуем только в QImage)."""
    f = QFont(family)
    f.setPixelSize(max(10, int(px)))
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    path = QPainterPath()
    path.addText(0, 0, f, text)
    br = path.boundingRect()
    pad = int(px * 0.30) + 6
    W = max(1, int(br.width()) + 2 * pad)
    H = max(1, int(br.height()) + 2 * pad)
    path.translate(-br.left() + pad, -br.top() + pad)

    core = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
    core.fill(0)
    p = QPainter(core)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = path.boundingRect()
    grad = QLinearGradient(r.topLeft(), r.bottomLeft())
    fc = QColor(fill)
    grad.setColorAt(0.0, fc.lighter(118))
    grad.setColorAt(1.0, fc if fc.lightness() < 200 else QColor(glow).lighter(165))
    p.fillPath(path, QBrush(grad))
    p.setPen(QPen(QColor(0, 0, 0, 200), max(1.2, px * 0.035)))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)
    p.end()

    # свечение: силуэт → цвет glow → маленький → обратно большой (блюр)
    sil = QImage(core)
    q = QPainter(sil)
    q.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    q.fillRect(sil.rect(), QColor(glow))
    q.end()
    import blur_fx                                   # гауссово свечение, без ступенек
    blur = blur_fx.blur_image(sil, max(2.0, px / 9.0))

    out = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(0)
    p = QPainter(out)
    p.setOpacity(0.95)
    p.drawImage(0, 0, blur)
    p.drawImage(0, 0, blur)
    p.setOpacity(1.0)
    p.drawImage(0, 0, core)
    p.end()
    return out


def image_mask(img: QImage, sx: float, sy: float, angle: float) -> np.ndarray:
    """Альфа слова, масштабированная в сетку MilkDrop и повёрнутая (0..1)."""
    w, h = max(1, int(img.width() * sx)), max(1, int(img.height() * sy))
    im = img.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
    if angle:
        im = im.transformed(QTransform().rotate(angle), Qt.TransformationMode.SmoothTransformation)
    im = im.convertToFormat(QImage.Format.Format_ARGB32)
    w, h = im.width(), im.height()
    ptr = im.constBits()
    ptr.setsize(im.bytesPerLine() * h)
    arr = np.frombuffer(ptr, dtype=np.uint8).reshape(h, im.bytesPerLine() // 4, 4)[:, :w, 3]
    return (arr.astype(np.float32) / 255.0) ** 1.5      # только «тело» букв, без ореола


class _Renderer(threading.Thread):
    """Фоновая отрисовка слов заранее (за ~2 с до появления) — GUI не ждёт."""

    def __init__(self):
        super().__init__(daemon=True, name="milk-words")
        self._cond = threading.Condition()
        self._jobs: list = []
        self.done: dict = {}
        self._alive = True

    def request(self, key, job):
        with self._cond:
            self._jobs.append((key, job))
            self._cond.notify()

    def clear(self):
        with self._cond:
            self._jobs.clear()
            self.done.clear()

    def stop(self):
        with self._cond:
            self._alive = False
            self._cond.notify()

    def take(self, key):
        with self._cond:
            return self.done.pop(key, None)

    def run(self):
        while True:
            with self._cond:
                while not self._jobs and self._alive:
                    self._cond.wait()
                if not self._alive:
                    return
                key, job = self._jobs.pop(0)
            try:
                text, fam, px, fill, glow, ang, sx, sy, Wmax = job
                img = render_word(text, fam, px, fill, glow)
                if img.width() > Wmax:
                    img = render_word(text, fam, px * Wmax / img.width(), fill, glow)
                mask = image_mask(img, sx, sy, ang) if sx > 0 else None
                res = (img, mask)
            except Exception as e:                   # noqa: BLE001
                print("[milk words] render:", e)
                res = None
            with self._cond:
                self.done[key] = res
                if len(self.done) > 64:
                    for k in list(self.done)[:16]:
                        self.done.pop(k, None)


# ── планировщик слов по таймингам ────────────────────────────────────────── #

_VOWELS = re.compile(r"[aeiouyаеёиоуыэюяAEIOUYАЕЁИОУЫЭЮЯ]+")


def _syllables(w: str) -> int:
    return max(1, len(_VOWELS.findall(w)))


class LyricWords:
    """Следит за позицией трека и строками LRC, рождает FlyingWord.
    stamp_cb(mask, cx, cy, rgb) — вжечь силуэт в буфер MilkDrop
    (cx, cy — центр в долях окна)."""

    MAX_LIVE = 6
    LEAD_MS = 60          # слово появляется чуть раньше доли: глаз ловит его к моменту звука
    PREFETCH_MS = 1200      # ближе к моменту — цвет совпадает с текущей картинкой

    def __init__(self, stamp_cb=None, grid_fn=None, color_fn=None):
        self.bank = FontBank.get()
        self.bank.start()
        self.rng = random.Random()
        self.stamp_cb = stamp_cb
        self.grid_fn = grid_fn            # () → (gw, gh) сетки MilkDrop
        self.color_fn = color_fn          # () → [(r,g,b) главный, (r,g,b) второй] из кадра
        self.words: list[FlyingWord] = []
        self._src = None
        self._sched: list = []            # dict(ms, text, li, emph, key)
        self._next = 0
        self._req = 0                     # до какого индекса уже заказана отрисовка
        self._last_pos = -1
        self._mood = "neutral"
        self._pal = PALETTES["neutral"]
        self._fonts = None                # (primary_lat, accent_lat, primary_cyr, accent_cyr)
        self._fonts_gen = -1
        self._size = (0, 0)
        self._r = _Renderer()
        self._r.start()
        self.enabled = False
        self.timed = False
        self.cfg = dict(DEFAULT_CFG)

    def set_config(self, cfg: dict | None):
        self.cfg = merged_cfg(cfg)
        self.MAX_LIVE = max(1, min(12, int(self.cfg["max_words"])))
        self._reset_render()

    def shutdown(self):
        self._r.stop()

    # ── настройка ── #

    def set_mood(self, prof: dict | None):
        m = mood_bucket(prof)
        if m != self._mood:
            self._mood = m
            self._pal = PALETTES[m]
            self._reset_render()

    @property
    def mood(self):
        return self._mood

    def _reset_render(self):
        self._fonts = None
        self._r.clear()
        self._req = self._next

    def _track_fonts(self):
        """Два шрифта на трек (основной + акцентный) для латиницы и кириллицы —
        единый стиль вместо винегрета; обновляется, когда докачались новые."""
        gen = self.bank.generation()
        if self._fonts is None or gen != self._fonts_gen:
            self._fonts_gen = gen
            b, m = self.bank, self._mood
            pl = b.pick(m, "primary", False)
            al = b.pick(m, "accent", False, avoid=pl)
            pc = b.pick(m, "primary", True)
            ac = b.pick(m, "accent", True, avoid=pc)
            fm, fa = self.cfg.get("font_main", "auto"), self.cfg.get("font_accent", "auto")
            if fm and fm != "auto":
                pl = pc = fm
                if not fa or fa == "auto":
                    al = ac = fm                     # выбран только основной — им всё
            if fa and fa != "auto":
                al = ac = fa
            self._fonts = (pl, al, pc, ac)
        return self._fonts

    def set_lines(self, lines: list, timed: bool):
        if lines is self._src:
            return
        self._src = lines
        self.timed = bool(timed)
        self.words.clear()
        sched = []
        if timed:
            tl = [(i, ln) for i, ln in enumerate(lines) if ln.get("ms", -1) >= 0]
            for j, (i, ln) in enumerate(tl):
                text = (ln.get("text") or "").strip()
                if not text:
                    continue
                start = ln["ms"]
                gap = (tl[j + 1][1]["ms"] - start) if j + 1 < len(tl) else 4000
                if ln.get("words"):
                    toks = [(ms, w) for ms, w in ln["words"] if w.strip()]
                else:
                    raw = [w for w in re.split(r"\s+", text) if w]
                    syl = [_syllables(w) for w in raw]
                    # оценка длительности пропевания строки: ~0.2 с на слог;
                    # в длинную паузу перед следующей строкой не растягиваем
                    est = sum(syl) * 200 + 150
                    span = min(gap * 0.92, max(est, gap * 0.55) if gap < est * 1.6 else est * 1.25)
                    tot = float(sum(syl))
                    acc, toks = 0.0, []
                    for w, sy in zip(raw, syl):
                        toks.append((int(start + span * acc / tot), w))
                        acc += sy
                merged, buf_t, buf_w = [], None, []
                for ms, w in toks:
                    if buf_t is None:
                        buf_t = ms
                    buf_w.append(w)
                    if not _SMALL.match(re.sub(r"[^\w']", "", w)) or len(buf_w) >= 3:
                        merged.append((buf_t, " ".join(buf_w)))
                        buf_t, buf_w = None, []
                if buf_w:
                    if merged:
                        merged[-1] = (merged[-1][0], merged[-1][1] + " " + " ".join(buf_w))
                    else:
                        merged.append((buf_t, " ".join(buf_w)))
                for k, (ms, w) in enumerate(merged):
                    w = re.sub(r"^[\"'«(\[]+|[\"'»)\],.;:!?]+$", "", w)
                    if w:
                        sched.append({"ms": ms, "text": w, "li": i, "emph": k == len(merged) - 1})
        sched.sort(key=lambda x: x["ms"])
        for n, it in enumerate(sched):
            it["key"] = (id(lines), n)
        self._sched = sched
        self._next = 0
        self._req = 0
        self._last_pos = -1
        self._r.clear()

    # ── заказ отрисовки ── #

    def _job_for(self, it, W, H):
        n = len(it["text"])
        cyr = bool(re.search(r"[А-Яа-яЁё]", it["text"]))
        pl, al, pc, ac = self._track_fonts()
        cfg = self.cfg
        fixed = cfg.get("font_accent", "auto") != "auto"
        # свой шрифт для последних слов — всегда им; авто — через раз
        accent = it["emph"] and (fixed or self.rng.random() < 0.45)
        fam = (ac if accent else pc) if cyr else (al if accent else pl)
        text = it["text"]
        up = cfg.get("upper", "auto")
        if up == "upper" or (up == "auto" and (fam in UPPER_FONTS or self._mood in ("aggr", "hype"))):
            text = text.upper()
        elif up == "lower":
            text = text.lower()
        base = min(W, H * 1.6)
        px = base * (0.080 if n <= 4 else 0.068 if n <= 8 else 0.056 if n <= 13 else 0.045)
        if it["emph"]:
            px *= 1.18
        if fam == "Press Start 2P":
            px *= 0.62                               # пиксельный шрифт очень широкий
        px *= max(0.3, min(3.0, cfg.get("size", 100) / 100.0))
        fill, glow = self._colors(it["emph"])
        if not cfg.get("tilt", True):
            ang = 0.0
        else:
            ang = self.rng.uniform(-9, 9) if self._mood not in ("sad", "dream") else self.rng.uniform(-4, 4)
        gw, gh = self.grid_fn() if self.grid_fn else (0, 0)
        sx, sy = (gw / W, gh / H) if gw else (0.0, 0.0)
        it["_ang"], it["_glow"] = ang, glow
        return (text, fam, px, fill, glow, ang, sx, sy, W * 0.8)

    def _colors(self, emph: bool):
        """Цвета слова из палитры текущего кадра MilkDrop: ореол — насыщенный
        цвет картинки, заливка — почти белая с его оттенком (читается на любом
        фоне и не спорит с визуалом). Акцентные слова берут второй цвет кадра."""
        mode = self.cfg.get("color", "visual")
        if mode == "custom":
            g = QColor(self.cfg.get("color_custom", "#ff3fa4"))
            if not g.isValid():
                g = QColor("#ff3fa4")
            h = g.hsvHueF()
            fill = QColor.fromHsvF(h, 0.14, 1.0) if h >= 0 else QColor("#ffffff")
            return fill.name(), g.name()
        dom = self.color_fn() if (self.color_fn and mode == "visual") else None
        if not dom:
            return self.rng.choice(self._pal)
        main, alt = dom
        base = alt if (emph and alt and self.rng.random() < 0.6) else main
        c = QColor.fromRgbF(*[max(0.0, min(1.0, x)) for x in base])
        h, sat, v, _ = c.getHsvF()
        if h < 0:                                  # серый кадр — берём оттенок из палитры настроения
            return self.rng.choice(self._pal)
        calm = self._mood in ("sad", "dream")
        glow = QColor.fromHsvF(h, min(1.0, max(0.55, sat * 1.25)) * (0.8 if calm else 1.0), 1.0)
        fill = QColor.fromHsvF(h, 0.10 if calm else 0.16, 1.0)
        return fill.name(), glow.name()

    def _prefetch(self, pos, W, H):
        if (W, H) != self._size:
            self._size = (W, H)
            self._r.clear()
            self._req = self._next
        while self._req < len(self._sched) and self._sched[self._req]["ms"] <= pos + self.PREFETCH_MS:
            it = self._sched[self._req]
            self._req += 1
            if it["ms"] < pos - 400:
                continue
            self._r.request(it["key"], self._job_for(it, W, H))

    def _seek(self, pos):
        lo, hi = 0, len(self._sched)
        while lo < hi:
            mid = (lo + hi) // 2
            if self._sched[mid]["ms"] < pos:
                lo = mid + 1
            else:
                hi = mid
        self._next = lo
        self._req = lo
        self._r.clear()

    def _place(self, wn, hn):
        """Позиция с наибольшим отступом от живых слов (в долях окна),
        только в разрешённых зонах сетки 3×3."""
        zones = [z for z in (self.cfg.get("zones") or ZONES) if z in ZONES] or list(ZONES)
        best, bd = (0.5, 0.5), -1.0
        for _ in range(12):
            z = ZONES.index(self.rng.choice(zones))
            col, row = z % 3, z // 3
            # клетка зоны с полями; центр слова не вылезает за окно
            x0, x1 = 0.06 + col * 0.293, 0.06 + (col + 1) * 0.293
            y0, y1 = 0.10 + row * 0.267, 0.10 + (row + 1) * 0.267
            x = self.rng.uniform(x0, x1)
            y = self.rng.uniform(y0, y1)
            x = min(max(x, 0.03 + wn / 2), 0.97 - wn / 2) if wn < 0.94 else 0.5
            y = min(max(y, 0.06 + hn / 2), 0.94 - hn / 2) if hn < 0.88 else 0.5
            d = min([math.hypot((x - w.cx) * 1.6, y - w.cy) for w in self.words] or [1.0])
            if d > bd:
                best, bd = (x, y), d
        return best

    # ── кадр ── #

    def update(self, pos_ms: int, W: int, H: int, now: float, beat: float):
        self.bank.poll()
        self.words = [w for w in self.words if w.alive(now)]
        if not (self.enabled and self.timed and self._sched) or W < 50 or H < 50:
            return
        if self._last_pos < 0 or pos_ms < self._last_pos - 600 or pos_ms > self._last_pos + 2500:
            self._seek(pos_ms - 200)                 # перемотка — не вываливать всё сразу
        self._last_pos = pos_ms
        self._prefetch(pos_ms, W, H)
        t = pos_ms + self.LEAD_MS
        while self._next < len(self._sched) and self._sched[self._next]["ms"] <= t:
            it = self._sched[self._next]
            res = self._r.take(it["key"])
            if res is None and t - it["ms"] < 250:
                break                                # ещё рисуется — подождём пару кадров
            self._next += 1
            if res is None or t - it["ms"] > 700:
                continue                             # опоздали — пропускаем, не ломая ритм
            self._spawn(it, res, W, H, now)

    def _spawn(self, it, res, W, H, now):
        img, mask = res
        while len(self.words) >= self.MAX_LIVE:
            self.words.pop(0)
        wn, hn = img.width() / W, img.height() / H
        x, y = self._place(wn, hn)
        calm = self._mood in ("sad", "dream")
        cfg = self.cfg
        anim = cfg.get("anim", "random")
        if anim == "random":
            style = self.rng.choice(("pop", "rise", "rise", "stretch") if calm else FlyingWord.STYLES)
        else:
            style = anim
        n = len(it["text"])
        life = 1.3 + min(1.0, n * 0.06) + (0.35 if it["emph"] else 0.0) + (0.5 if calm else 0.0)
        life *= max(0.4, min(3.0, cfg.get("life", 100) / 100.0))
        drift = (self.rng.uniform(-0.02, 0.02), self.rng.uniform(-0.025, 0.006))
        fly = cfg.get("fly", "none")
        if fly == "random":
            fly = self.rng.choice(("left", "right", "top", "bottom"))
        d = 0.22
        fxy = {"left": (-d, 0.0), "right": (d, 0.0), "top": (0.0, -d), "bottom": (0.0, d),
               "center": ((0.5 - x) * 0.8, (0.5 - y) * 0.8)}.get(fly, (0.0, 0.0))
        self.words.append(FlyingWord(img, x, y, it.get("_ang", 0.0), life, style, drift, now, fxy))
        if (cfg.get("stamp", True) and self.stamp_cb is not None and mask is not None
                and mask.size > 4):
            c = QColor(it.get("_glow", "#ffffff"))
            try:
                self.stamp_cb(mask, x, y, (c.redF() * 0.8, c.greenF() * 0.8, c.blueF() * 0.8))
            except Exception:                        # noqa: BLE001
                pass

    def draw(self, p: QPainter, W: int, H: int, now: float, beat: float):
        if not self.words:
            return
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        for w in self.words:
            w.draw(p, W, H, now, beat)


# ══════════════════════════════════════════════════════════════════════════ #
#  Окно настроек режима «Слова»
# ══════════════════════════════════════════════════════════════════════════ #

from PyQt6.QtCore import pyqtSignal  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QComboBox,
    QSlider, QSpinBox, QCheckBox, QFileDialog, QColorDialog, QGroupBox, QMessageBox,
)

_ZONE_RU = {"tl": "верх-лево", "t": "верх", "tr": "верх-право", "l": "лево", "c": "центр",
            "r": "право", "bl": "низ-лево", "b": "низ", "br": "низ-право"}
_FLY = [("none", "На месте (без полёта)"), ("random", "Со случайной стороны"),
        ("left", "Слева"), ("right", "Справа"), ("top", "Сверху"), ("bottom", "Снизу"),
        ("center", "Из центра")]
_ANIM = [("random", "Случайная"), ("pop", "Выпрыгивание"), ("slam", "Удар (из большого)"),
         ("rise", "Всплытие"), ("spin", "Поворот"), ("glitch", "Глитч"),
         ("stretch", "Растяжение"), ("fade", "Плавное проявление")]
_UPPER = [("auto", "Авто (по шрифту и настроению)"), ("upper", "ВСЕ ЗАГЛАВНЫЕ"),
          ("as_is", "Как в тексте"), ("lower", "все строчные")]
_COLOR = [("visual", "Из визуализации"), ("mood", "По настроению трека"), ("custom", "Свой цвет")]


class WordsSettingsDialog(QDialog):
    """Настройки появления слов. Изменения применяются сразу (сигнал changed)."""

    changed = pyqtSignal(dict)

    def __init__(self, cfg: dict | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Слова в MilkDrop — настройки")
        self.setMinimumWidth(520)
        self.cfg = merged_cfg(cfg)
        self.bank = FontBank.get()
        self.bank.start()
        self._loading = True
        root = QVBoxLayout(self)
        root.setSpacing(10)

        # ── где появляются ──
        g = QGroupBox("Где появляются слова")
        gl = QVBoxLayout(g)
        grid = QGridLayout()
        grid.setSpacing(4)
        self.zone_btn = {}
        for i, z in enumerate(ZONES):
            b = QPushButton(_ZONE_RU[z])
            b.setCheckable(True)
            b.setMinimumHeight(30)
            b.toggled.connect(self._emit)
            grid.addWidget(b, i // 3, i % 3)
            self.zone_btn[z] = b
        gl.addLayout(grid)
        row = QHBoxLayout()
        for text, zs in (("Везде", ZONES), ("Центр", ("c", "l", "r")),
                         ("По краям", ("tl", "t", "tr", "l", "r", "bl", "b", "br")),
                         ("Верх", ("tl", "t", "tr")), ("Низ", ("bl", "b", "br"))):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, zs=zs: self._set_zones(zs))
            row.addWidget(b)
        gl.addLayout(row)
        root.addWidget(g)

        # ── движение ──
        g = QGroupBox("Появление")
        gl = QGridLayout(g)
        self.fly = self._combo(_FLY)
        self.anim = self._combo(_ANIM)
        gl.addWidget(QLabel("Откуда влетают:"), 0, 0)
        gl.addWidget(self.fly, 0, 1)
        gl.addWidget(QLabel("Анимация:"), 1, 0)
        gl.addWidget(self.anim, 1, 1)
        self.tilt = QCheckBox("Наклонять слова")
        self.tilt.toggled.connect(self._emit)
        self.stamp = QCheckBox("Вжигать слова в картинку (MilkDrop растягивает их)")
        self.stamp.toggled.connect(self._emit)
        gl.addWidget(self.tilt, 2, 0, 1, 2)
        gl.addWidget(self.stamp, 3, 0, 1, 2)
        root.addWidget(g)

        # ── шрифты ──
        g = QGroupBox("Шрифты")
        gl = QGridLayout(g)
        self.font_main = QComboBox()
        self.font_accent = QComboBox()
        for cb in (self.font_main, self.font_accent):
            cb.setMaxVisibleItems(20)
            cb.currentIndexChanged.connect(self._emit)
        gl.addWidget(QLabel("Обычные слова:"), 0, 0)
        gl.addWidget(self.font_main, 0, 1)
        gl.addWidget(QLabel("Последнее слово строки:"), 1, 0)
        gl.addWidget(self.font_accent, 1, 1)
        self.preview_main = QLabel()
        self.preview_accent = QLabel()
        for lb in (self.preview_main, self.preview_accent):
            lb.setMinimumHeight(40)
        gl.addWidget(self.preview_main, 2, 0, 1, 2)
        gl.addWidget(self.preview_accent, 3, 0, 1, 2)
        self.cyr_warn = QLabel("")
        self.cyr_warn.setWordWrap(True)
        gl.addWidget(self.cyr_warn, 4, 0, 1, 2)
        b = QPushButton("Загрузить свой шрифт (.ttf / .otf)…")
        b.clicked.connect(self._load_font)
        gl.addWidget(b, 5, 0, 1, 2)
        self.upper = self._combo(_UPPER)
        gl.addWidget(QLabel("Регистр:"), 6, 0)
        gl.addWidget(self.upper, 6, 1)
        root.addWidget(g)

        # ── вид ──
        g = QGroupBox("Цвет и размер")
        gl = QGridLayout(g)
        self.color = self._combo(_COLOR)
        self.color_btn = QPushButton("Выбрать цвет…")
        self.color_btn.clicked.connect(self._pick_color)
        gl.addWidget(QLabel("Цвет свечения:"), 0, 0)
        gl.addWidget(self.color, 0, 1)
        gl.addWidget(self.color_btn, 0, 2)
        self.size = self._slider(50, 200)
        self.size_lbl = QLabel()
        gl.addWidget(QLabel("Размер:"), 1, 0)
        gl.addWidget(self.size, 1, 1)
        gl.addWidget(self.size_lbl, 1, 2)
        self.life = self._slider(50, 250)
        self.life_lbl = QLabel()
        gl.addWidget(QLabel("Время на экране:"), 2, 0)
        gl.addWidget(self.life, 2, 1)
        gl.addWidget(self.life_lbl, 2, 2)
        self.maxw = QSpinBox()
        self.maxw.setRange(1, 12)
        self.maxw.valueChanged.connect(self._emit)
        gl.addWidget(QLabel("Слов на экране, не больше:"), 3, 0)
        gl.addWidget(self.maxw, 3, 1)
        root.addWidget(g)

        row = QHBoxLayout()
        b = QPushButton("Сбросить всё")
        b.clicked.connect(self._reset)
        row.addWidget(b)
        row.addStretch(1)
        b = QPushButton("Готово")
        b.clicked.connect(self.accept)
        row.addWidget(b)
        root.addLayout(row)

        self._fill_fonts()
        self._load(self.cfg)

    # ── помощники ── #

    def _combo(self, items):
        cb = QComboBox()
        for key, text in items:
            cb.addItem(text, key)
        cb.currentIndexChanged.connect(self._emit)
        return cb

    def _slider(self, lo, hi):
        s = QSlider(Qt.Orientation.Horizontal)
        s.setRange(lo, hi)
        s.valueChanged.connect(self._emit)
        return s

    @staticmethod
    def _select(cb: QComboBox, key):
        i = cb.findData(key)
        cb.setCurrentIndex(i if i >= 0 else 0)

    def _fill_fonts(self):
        fams = self.bank.families()
        user = sorted(f for f, m in fams.items() if m == "user")
        nice = sorted(f for f, m in fams.items() if m != "user" and f not in SYSTEM_FONTS)
        system = sorted(f for f in fams if f in SYSTEM_FONTS)
        others = sorted(set(QFontDatabase.families()) - set(fams))
        for cb in (self.font_main, self.font_accent):
            cur = cb.currentData()
            cb.blockSignals(True)
            cb.clear()
            cb.addItem("Авто — под настроение трека", "auto")
            for title, lst in (("— свои —", user), ("— стильные —", nice),
                               ("— системные —", system), ("— все шрифты Windows —", others)):
                if not lst:
                    continue
                cb.insertSeparator(cb.count())
                cb.addItem(title, None)
                cb.model().item(cb.count() - 1).setEnabled(False)
                for f in lst:
                    cb.addItem(f, f)
                    if lst is not others:            # превью для сотни системных — тормоза
                        cb.setItemData(cb.count() - 1, QFont(f, 11), Qt.ItemDataRole.FontRole)
            if cur:
                self._select(cb, cur)
            cb.blockSignals(False)

    def _load(self, cfg):
        self._loading = True
        zs = set(cfg.get("zones") or ZONES)
        for z, b in self.zone_btn.items():
            b.setChecked(z in zs)
        self._select(self.fly, cfg["fly"])
        self._select(self.anim, cfg["anim"])
        self._select(self.upper, cfg["upper"])
        self._select(self.color, cfg["color"])
        self._select(self.font_main, cfg["font_main"])
        self._select(self.font_accent, cfg["font_accent"])
        self.tilt.setChecked(bool(cfg["tilt"]))
        self.stamp.setChecked(bool(cfg["stamp"]))
        self.size.setValue(int(cfg["size"]))
        self.life.setValue(int(cfg["life"]))
        self.maxw.setValue(int(cfg["max_words"]))
        self._loading = False
        self._refresh_labels()

    def _set_zones(self, zs):
        self._loading = True
        for z, b in self.zone_btn.items():
            b.setChecked(z in zs)
        self._loading = False
        self._emit()

    def _refresh_labels(self):
        self.size_lbl.setText(f"{self.size.value()}%")
        self.life_lbl.setText(f"{self.life.value()}%")
        self.color_btn.setEnabled(self.color.currentData() == "custom")
        c = QColor(self.cfg.get("color_custom", "#ff3fa4"))
        self.color_btn.setStyleSheet(f"QPushButton{{border-left:14px solid {c.name()};}}")
        warn = []
        for cb, lb, sample in ((self.font_main, self.preview_main, "Обычные слова · Words"),
                               (self.font_accent, self.preview_accent, "ПОСЛЕДНЕЕ СЛОВО · LAST")):
            fam = cb.currentData()
            if fam and fam != "auto":
                f = QFont(fam)
                f.setPixelSize(26)
                lb.setFont(f)
                lb.setText(sample)
                if not self.bank.has_cyrillic(fam):
                    warn.append(fam)
            else:
                f = QFont()
                f.setPixelSize(13)
                lb.setFont(f)
                lb.setText("Авто: шрифт подбирается под настроение трека")
        self.cyr_warn.setText(
            ("В шрифте " + ", ".join(f"«{w}»" for w in warn) +
             " нет кириллицы — русские буквы возьмутся из запасного шрифта.") if warn else "")

    def _gather(self) -> dict:
        cfg = dict(self.cfg)
        cfg["zones"] = [z for z, b in self.zone_btn.items() if b.isChecked()] or list(ZONES)
        cfg["fly"] = self.fly.currentData() or "none"
        cfg["anim"] = self.anim.currentData() or "random"
        cfg["upper"] = self.upper.currentData() or "auto"
        cfg["color"] = self.color.currentData() or "visual"
        cfg["font_main"] = self.font_main.currentData() or "auto"
        cfg["font_accent"] = self.font_accent.currentData() or "auto"
        cfg["tilt"] = self.tilt.isChecked()
        cfg["stamp"] = self.stamp.isChecked()
        cfg["size"] = self.size.value()
        cfg["life"] = self.life.value()
        cfg["max_words"] = self.maxw.value()
        return cfg

    def _emit(self, *_):
        if self._loading:
            return
        self.cfg = self._gather()
        self._refresh_labels()
        self.changed.emit(dict(self.cfg))

    # ── действия ── #

    def _load_font(self):
        path, _ = QFileDialog.getOpenFileName(self, "Шрифт", "", "Шрифты (*.ttf *.otf)")
        if not path:
            return
        try:
            fams = self.bank.add_user_font(path)
        except Exception as e:                       # noqa: BLE001
            fams = []
            print("[milk words] font:", e)
        if not fams:
            QMessageBox.warning(self, "Шрифт", "Не получилось подключить этот файл как шрифт.")
            return
        self._fill_fonts()
        # куда поставить: если основной на авто — в основной, иначе в акцентный
        target = self.font_main if self.font_main.currentData() in (None, "auto") else self.font_accent
        self._select(target, fams[0])
        self._emit()

    def _pick_color(self):
        c = QColorDialog.getColor(QColor(self.cfg.get("color_custom", "#ff3fa4")), self,
                                  "Цвет свечения слов")
        if c.isValid():
            self.cfg["color_custom"] = c.name()
            self._emit()

    def _reset(self):
        self.cfg = dict(DEFAULT_CFG)
        self._load(self.cfg)
        self.changed.emit(dict(self.cfg))
