# fluid_core.py
"""
Ядро темы «Fluid» — БЕЗ зависимости от Qt (только numpy), поэтому его можно
гонять в фоновом потоке и тестировать отдельно.

Что здесь есть:
  1. dominant_rgb()      — доминирующий цвет обложки (numpy-массив HxWx3).
  2. make_palette()      — из одного цвета строит всю палитру текстуры
                           (доминанта, аналоговый, светлый, тёмный, беж).
  3. render_marble()     — процедурная «жидкая» текстура (domain-warped fBm с
                           плоскими цветовыми зонами — как на референсе).
  4. fluid_theme_dict()  — словарь темы, совместимый с THEMES из themes.py.
  5. fluid_qss()         — полный QSS темы (бежевый фон, оранжевый широкий
                           шрифт, крупные скругления).
"""

from __future__ import annotations

import colorsys
import hashlib
import math
from dataclasses import dataclass

import numpy as np

# ── Фирменные цвета референса ────────────────────────────────────────────── #
BEIGE = (222, 220, 188)     # фон окна (левая часть скриншота)
CREAM = (236, 231, 200)     # плашки/панели
ORANGE = (255, 138, 0)      # акцент: крупные заголовки, кнопки
INK = (46, 44, 30)          # основной тёмный текст
DEFAULT_DOMINANT = (17, 182, 208)   # бирюза с референса — пока обложки нет

# ── Тёмный («чёрный») вариант темы Fluid ─────────────────────────────────── #
DARK_BG = (11, 11, 14)      # фон окна вместо бежевого
DARK_PANEL = (17, 17, 21)   # плашки/панели
DARK_PANEL2 = (24, 24, 29)
DARK_TEXT = (236, 234, 226) # светлый текст на чёрном
DARK_ZONE = (6, 6, 8)       # «чёрные разводы» в текстуре
DEFAULT_DOMINANT_DARK = (24, 168, 196)  # приглушённая бирюза для тёмного флюида

RGB = tuple  # (r, g, b), 0..255


# ══════════════════════════════════════════════════════════════════════════ #
#  1. Доминирующий цвет
# ══════════════════════════════════════════════════════════════════════════ #

def _rgb_to_hsv(a: np.ndarray):
    """Векторизованный RGB→HSV. a: Nx3 float32 в [0,1]. Возвращает h,s,v (h в [0,1))."""
    mx = a.max(axis=1)
    mn = a.min(axis=1)
    d = mx - mn
    v = mx
    s = np.where(mx > 1e-6, d / np.maximum(mx, 1e-6), 0.0)
    r, g, b = a[:, 0], a[:, 1], a[:, 2]
    dd = np.maximum(d, 1e-6)
    h = np.where(mx == r, ((g - b) / dd) % 6.0,
        np.where(mx == g, (b - r) / dd + 2.0, (r - g) / dd + 4.0))
    h = np.where(d < 1e-6, 0.0, h / 6.0) % 1.0
    return h, s, v


def dominant_rgb(arr: np.ndarray) -> tuple[RGB, bool]:
    """
    Доминирующий ЦВЕТ обложки.

    Не среднее (оно даёт грязно-серый на пёстрых обложках) и не самый частый
    пиксель (шум), а «самая весомая зона тона»:
      * отбрасываем серые, почти чёрные и почти белые пиксели;
      * строим гистограмму по тону (24 сектора), вес пикселя = насыщенность ×
        (0.35 + яркость), т.е. сочные пиксели важнее тусклых;
      * сглаживаем гистограмму по кольцу, берём максимум;
      * усредняем пиксели этого сектора и его соседей;
      * «оживляем» итог: минимальная насыщенность/яркость, чтобы текстура
        не получалась блеклой.

    Возвращает ((r,g,b), is_mono). is_mono=True — обложка почти чёрно-белая
    (тогда возвращается нейтральный серый, подобранный по яркости обложки).
    """
    a = arr.reshape(-1, arr.shape[-1])[:, :3].astype(np.float32) / 255.0
    if a.size == 0:
        return DEFAULT_DOMINANT, False

    h, s, v = _rgb_to_hsv(a)
    colorful = (s > 0.28) & (v > 0.22) & (v < 0.98)

    # Монохромная обложка: цветных пикселей меньше 6 %
    if colorful.mean() < 0.06:
        g = float(np.clip(v.mean(), 0.16, 0.78))
        c = int(g * 255)
        return (c, c, min(255, c + 4)), True

    w = np.where(colorful, s * (0.35 + v), 0.0)
    bins = 24
    idx = np.minimum((h * bins).astype(np.int32), bins - 1)
    hist = np.bincount(idx, weights=w, minlength=bins)
    hist_s = hist + 0.5 * (np.roll(hist, 1) + np.roll(hist, -1))   # сглаживание по кольцу
    best = int(np.argmax(hist_s))

    dist = np.minimum((idx - best) % bins, (best - idx) % bins)    # круговое расстояние
    sel = colorful & (dist <= 1)
    ws = w[sel]
    rgb = (a[sel] * ws[:, None]).sum(axis=0) / max(float(ws.sum()), 1e-6)

    # «Оживляем»
    hh, ss, vv = colorsys.rgb_to_hsv(*[float(x) for x in rgb])
    ss = min(0.95, max(ss, 0.62))
    vv = min(0.92, max(vv, 0.62))
    r, g_, b = colorsys.hsv_to_rgb(hh, ss, vv)
    return (int(r * 255), int(g_ * 255), int(b * 255)), False


# ══════════════════════════════════════════════════════════════════════════ #
#  2. Палитра
# ══════════════════════════════════════════════════════════════════════════ #

def _mix(a: RGB, b: RGB, t: float) -> RGB:
    return tuple(int(round(x * (1 - t) + y * t)) for x, y in zip(a, b))


def _shift_hue(rgb: RGB, deg: float, s_min: float = 0.55, v_range=(0.66, 0.9)) -> RGB:
    h, s, v = colorsys.rgb_to_hsv(*[c / 255.0 for c in rgb])
    h = (h + deg / 360.0) % 1.0
    s = max(s, s_min)
    v = min(max(v, v_range[0]), v_range[1])
    return tuple(int(c * 255) for c in colorsys.hsv_to_rgb(h, s, v))


@dataclass(frozen=True)
class FluidPalette:
    dominant: RGB
    analog: RGB      # соседний оттенок (на референсе — зелёный рядом с бирюзой)
    mid: RGB         # доминанта, разбавленная фоном
    light: RGB       # осветлённая доминанта (блики)
    dark: RGB        # почти чёрный с лёгким оттенком (как чёрные разводы)
    beige: RGB       # цвет «фона кадра» (беж в светлой теме, почти чёрный в тёмной)
    mono: bool = False
    night: bool = False   # тёмный вариант темы

    def css(self, name: str) -> str:
        r, g, b = getattr(self, name)
        return f"#{r:02x}{g:02x}{b:02x}"


def make_palette(dom: RGB, mono: bool = False, dark: bool = False) -> FluidPalette:
    if dark:
        # Тёмный флюид: зоны приглушены и утоплены в чёрный, фон кадра — почти чёрный.
        dom = _dim(dom, 0.62, 0.80)
        dark_zone = _mix(DARK_ZONE, dom, 0.05)
        if mono:
            analog = _mix(dom, DARK_BG, 0.45)
        else:
            analog = _shift_hue(dom, -38, s_min=0.45, v_range=(0.34, 0.62))
        return FluidPalette(
            dominant=dom,
            analog=analog,
            mid=_mix(dom, DARK_BG, 0.55),
            light=_mix(dom, (255, 255, 255), 0.20),
            dark=dark_zone,
            beige=DARK_BG,
            mono=mono,
            night=True,
        )
    dark_zone = _mix((6, 6, 6), dom, 0.06)
    if mono:
        analog = _mix(dom, BEIGE, 0.35)
    else:
        analog = _shift_hue(dom, -38)          # на референсе: бирюза → зелёный
    return FluidPalette(
        dominant=dom,
        analog=analog,
        mid=_mix(dom, BEIGE, 0.5),
        light=_mix(dom, (255, 255, 255), 0.35),
        dark=dark_zone,
        beige=BEIGE,
        mono=mono,
    )


def _dim(rgb: RGB, v_max: float, v_min: float = 0.0) -> RGB:
    """Приглушает яркость цвета, сохраняя оттенок (для тёмной палитры)."""
    h, s, v = colorsys.rgb_to_hsv(*[c / 255.0 for c in rgb])
    v = min(max(v, v_min), v_max)
    s = min(max(s, 0.45), 0.9)
    return tuple(int(c * 255) for c in colorsys.hsv_to_rgb(h, s, v))


def seed_from_key(key: str) -> int:
    """Стабильный seed из пути трека: один и тот же трек → один и тот же узор."""
    return int.from_bytes(hashlib.md5((key or "default").encode("utf-8")).digest()[:4], "little")


# ══════════════════════════════════════════════════════════════════════════ #
#  3. Жидкая текстура
# ══════════════════════════════════════════════════════════════════════════ #

_LATTICE_N = 256


def _lerp_wrap(a, b, t):
    """Линейная интерполяция ЦИКЛИЧЕСКОЙ величины (координата зоны в [0,1)).
    0.98 и 0.02 — соседи по кольцу, а не концы отрезка: обычный lerp провёл бы
    между ними все промежуточные зоны и нарисовал ложные полосы на швах."""
    d = b - a
    d -= np.round(d)
    return a + t * d


def _lattice(seed: int) -> np.ndarray:
    return np.random.default_rng(seed).random((_LATTICE_N, _LATTICE_N)).astype(np.float32)


def _value_noise(lat: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Гладкий value-noise с бесшовным заворачиванием решётки."""
    n = _LATTICE_N
    xi = np.floor(x).astype(np.int32)
    yi = np.floor(y).astype(np.int32)
    xf = x - xi
    yf = y - yi
    u = xf * xf * (3 - 2 * xf)          # smoothstep — убирает «квадратность» решётки
    v = yf * yf * (3 - 2 * yf)
    x0, x1 = xi % n, (xi + 1) % n
    y0, y1 = yi % n, (yi + 1) % n
    a, b = lat[y0, x0], lat[y0, x1]
    c, d = lat[y1, x0], lat[y1, x1]
    return (a * (1 - u) + b * u) * (1 - v) + (c * (1 - u) + d * u) * v


def _fbm(lat, x, y, octaves=3):
    s = np.zeros_like(x)
    amp, f, tot = 0.5, 1.0, 0.0
    for _ in range(octaves):
        s += amp * _value_noise(lat, x * f, y * f)
        tot += amp
        amp *= 0.5
        f *= 2.0
    return s / tot


def marble_field(w: int, h: int, seed: int = 1, drift: float = 0.0) -> np.ndarray:
    """
    Скалярное поле «жидкого» узора (HxW float32, значения — координата
    цветовой зоны в [0,1)).

    Приём — domain warping (искажение координат шумом, вложенное дважды):
        f = fbm(p + k·fbm(p + k·fbm(p)))
    Это даёт вихри и «затёки» как у мраморной бумаги.

    Координаты нормируются на ширину кадра 9:16 (unit = h·9/16), поэтому
    масштаб узора соответствует исходным пропорциям референса: без зума и
    растяжения при любой форме виджета.
    """
    rng = np.random.default_rng(seed)
    lat = _lattice(seed)

    # Небольшая вариативность параметров: разные треки — разный «характер»
    scale = float(rng.uniform(0.95, 1.30))
    warp = float(rng.uniform(2.8, 3.6))
    bands = float(rng.uniform(1.25, 1.75))
    off = rng.uniform(0, 40, size=6).astype(np.float32)

    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    unit = h * (9.0 / 16.0)
    # drift — медленный «дрейф» узора во времени (для подпитки симуляции)
    x = xs / unit * scale + drift * 0.37
    y = ys / unit * scale + drift * 0.21
    off = off + np.float32(drift) * np.array([0.11, -0.07, 0.05, 0.09, -0.06, 0.04], np.float32)

    q1 = _fbm(lat, x + off[0], y + off[1])
    q2 = _fbm(lat, x + off[2] + 5.2, y + off[3] + 1.3)
    r1 = _fbm(lat, x + warp * q1 + 1.7, y + warp * q2 + 9.2)
    r2 = _fbm(lat, x + warp * q1 + 8.3, y + warp * q2 + 2.8)
    f = _fbm(lat, x + warp * r1 + off[4], y + warp * r2 + off[5])
    # Выравнивание по рангу: fBm копится около среднего, и часть цветовых
    # зон почти не встречалась. Монотонное преобразование сохраняет форму
    # изолиний (сам рисунок), но даёт всем зонам равную долю площади.
    flat = f.ravel()
    ranks = np.empty(flat.size, np.float32)
    ranks[np.argsort(flat, kind="stable")] = np.arange(flat.size, dtype=np.float32) / flat.size
    f = ranks.reshape(f.shape)
    return ((f * bands) % 1.0).astype(np.float32)


_LUT_CACHE: dict = {}


def _band_lut(pal: FluidPalette) -> np.ndarray:
    """LUT 1024×3 uint8: вся раскраска зон одним table-lookup'ом по t."""
    lut = _LUT_CACHE.get(pal)
    if lut is None:
        seq = np.array(
            [pal.dominant, pal.analog, pal.dominant, pal.beige,
             pal.dark, pal.dominant, pal.mid, pal.light],
            dtype=np.float32,
        )
        k = len(seq)
        t = (np.arange(1024) + 0.5) / 1024.0
        pos = t * k
        i0 = np.floor(pos).astype(np.int32) % k
        frac = pos - np.floor(pos)
        i1 = (i0 + 1) % k
        edge = np.clip((frac - 0.9) / 0.1, 0.0, 1.0)   # переход только в последних 10 % зоны
        edge = edge * edge * (3 - 2 * edge)
        img = seq[i0] * (1 - edge[:, None]) + seq[i1] * edge[:, None]
        lut = np.ascontiguousarray(img.astype(np.uint8))
        _LUT_CACHE[pal] = lut
    return lut


_LUT32_CACHE: dict = {}


def _band_lut32(pal: FluidPalette) -> np.ndarray:
    """Та же LUT, но упакованная в uint32 0xFFRRGGBB — ровно формат
    QImage.Format_RGB32. Кадр получается ОДНИМ table-lookup'ом и отдаётся
    в Qt без конвертации форматов и без промежуточных копий."""
    lut = _LUT32_CACHE.get(pal)
    if lut is None:
        c = _band_lut(pal).astype(np.uint32)
        lut = (np.uint32(0xFF000000) | (c[:, 0] << 16) | (c[:, 1] << 8) | c[:, 2]).astype(np.uint32)
        _LUT32_CACHE[pal] = lut
    return lut


def colorize_bands32(t: np.ndarray, pal: FluidPalette) -> np.ndarray:
    idx = (t * 1023.999).astype(np.int32)        # t уже в [0,1) — clip не нужен
    np.clip(idx, 0, 1023, out=idx)
    return _band_lut32(pal)[idx]


def colorize_bands(t: np.ndarray, pal: FluidPalette) -> np.ndarray:
    """
    Раскрашивает поле координат зон t ([0,1)) в плоские цветовые области с
    резкими краями (переход только в последних 10 % зоны) — как на референсе.
    """
    lut = _band_lut(pal)
    idx = np.clip((t * 1024.0).astype(np.int32), 0, 1023)
    return np.ascontiguousarray(lut[idx])


def render_marble(w: int, h: int, pal: FluidPalette, seed: int = 1) -> np.ndarray:
    """Статичная «жидкая» текстура HxWx3 uint8 (используется gif_export и как
    стартовое состояние симуляции)."""
    return colorize_bands(marble_field(w, h, seed), pal)


# ══════════════════════════════════════════════════════════════════════════ #
#  3b. Живая симуляция жидкости
# ══════════════════════════════════════════════════════════════════════════ #

class FluidSim:
    """
    Настоящая процедурная симуляция жидкости на грубой сетке (numpy, без Qt).

    Модель:
      • «краска» d — скалярное поле зон узора (стартует с мраморного поля);
      • скорость = curl(ψ) — несжимаемое (дивергенция = 0) поле, полученное из
        медленно эволюционирующего во времени шумового потенциала ψ: это
        постоянный мягкий «ветер», который гоняет жидкость по вихрям;
      • плюс импульсное поле скорости (затухающее): в него пишутся воздействия
        мыши (помешивание курсором) и «удары» от звука (вихри по биту);
      • каждый шаг — полулагранжев перенос краски полем скорости (с заворотом
        краёв) и лёгкая перетяжка к центрам зон, чтобы границы оставались
        плоскими и резкими, как в референсе.

    Всё считается на сетке ~100×180 и масштабируется при отрисовке, поэтому
    шаг симуляции стоит несколько миллисекунд и живёт в GUI-потоке.
    """

    BANDS = 8                     # количество цветовых зон (как в colorize_bands)
    # Подпитка узора: без неё численная диффузия переноса за ~минуту
    # «съедает» мелкие цветовые зоны и остаётся 1–2 цвета. Краска мягко
    # тянется к эталонному мраморному полю, которое само медленно дрейфует.
    FEED_RATE = 0.030             # доля за кадр (при 30 fps)
    DRIFT_SPEED = 0.08            # скорость дрейфа эталона
    TARGET_EVERY = 45             # пересчёт эталона раз в N шагов (~1.5 с)

    def __init__(self, gw: int, gh: int, seed: int = 1,
                 pal: FluidPalette | None = None):
        self.pal = pal or make_palette(DEFAULT_DOMINANT)
        self.seed = seed
        self.t = 0.0
        self.level = 0.0          # сглаженный уровень звука 0..1 (извне)
        self._prev_level = 0.0
        self.gw = self.gh = 0
        self.resize(gw, gh, seed)

    # ── геометрия сетки ────────────────────────────────────────────────── #

    def resize(self, gw: int, gh: int, seed: int | None = None):
        gw = int(max(24, min(240, gw)))
        gh = int(max(32, min(160, gh)))
        if seed is not None:
            self.seed = seed
        self.gw, self.gh = gw, gh
        self._lat = _lattice(self.seed)
        self._lat2 = _lattice(self.seed + 0x9E37)
        ys, xs = np.mgrid[0:gh, 0:gw].astype(np.float32)
        self._X, self._Y = xs, ys
        # единица узора = ширина кадра 9:16 при данной высоте → масштаб узора
        # всегда как в оригинале, без зума и растяжения
        self._unit = gh * (9.0 / 16.0)
        self._Xn = xs / self._unit
        self._Yn = ys / self._unit
        self.d = marble_field(gw, gh, self.seed)
        self._target = self.d.copy()
        self._target_age = 0
        self._vx = np.zeros((gh, gw), np.float32)     # импульсная скорость
        self._vy = np.zeros((gh, gw), np.float32)
        self._psi = None
        self._psi_age = 0
        self._drift_ang = (self.seed % 628) / 100.0   # свой курс «течения» у трека
        self._up_key = None

    def regrid(self, gw: int, gh: int):
        """Смена размера сетки БЕЗ перезапуска узора: краска пересэмплируется
        (nearest — чтобы не смешивать зоны), поэтому при ресайзе окна рисунок
        плавно продолжается, а не прыгает на новый."""
        gw = int(max(24, min(240, gw)))
        gh = int(max(32, min(160, gh)))
        if (gw, gh) == (self.gw, self.gh):
            return
        old = self.d
        ys = np.minimum((np.arange(gh) * old.shape[0] / gh).astype(np.int32), old.shape[0] - 1)
        xs = np.minimum((np.arange(gw) * old.shape[1] / gw).astype(np.int32), old.shape[1] - 1)
        d = np.ascontiguousarray(old[ys][:, xs])
        self.gw, self.gh = gw, gh
        yy, xx = np.mgrid[0:gh, 0:gw].astype(np.float32)
        self._X, self._Y = xx, yy
        self._unit = gh * (9.0 / 16.0)
        self._Xn = xx / self._unit
        self._Yn = yy / self._unit
        self.d = d
        self._target = marble_field(gw, gh, self.seed, self.t * self.DRIFT_SPEED)
        self._target_age = 0
        self._vx = np.zeros((gh, gw), np.float32)
        self._vy = np.zeros((gh, gw), np.float32)
        self._psi = None
        self._psi_age = 0
        self._up_key = None

    def seed_dye(self, seed: int):
        """Новый трек → новый узор (перезапуск краски с того же размера сетки)."""
        self.seed = seed
        self._lat = _lattice(seed)
        self._lat2 = _lattice(seed + 0x9E37)
        self.d = marble_field(self.gw, self.gh, seed)
        self._target = self.d.copy()
        self._target_age = 0
        self._vx[:] = 0.0
        self._vy[:] = 0.0
        self._psi = None
        self._psi_age = 0
        self._drift_ang = (seed % 628) / 100.0

    def set_palette(self, pal: FluidPalette):
        self.pal = pal

    # ── внешние воздействия ────────────────────────────────────────────── #

    def splash(self, gx: float, gy: float, power: float = 1.0):
        """Радиальный всплеск (клик / удар бита): вихрь-кольцо из точки."""
        sig = max(4.0, min(self.gw, self.gh) / 7.0)
        g = self._gauss(gx, gy, sig)
        rx = (self._X - gx) / sig
        ry = (self._Y - gy) / sig
        amp = min(50.0, 16.0 * power + self.level * 26.0)
        self._vx += g * rx * amp + g * ry * amp * 0.6
        self._vy += g * ry * amp - g * rx * amp * 0.6

    def _gauss(self, gx: float, gy: float, sig: float) -> np.ndarray:
        r2 = ((self._X - gx) ** 2 + (self._Y - gy) ** 2) / (2.0 * sig * sig)
        return np.exp(-r2).astype(np.float32)

    # ── шаг симуляции ──────────────────────────────────────────────────── #

    def step(self, dt: float):
        dt = min(0.05, max(0.001, dt))
        self.t += dt
        gw, gh = self.gw, self.gh

        # Потенциал «ветра»: две октавы шума, чьи offsets медленно орбитируют
        # во времени → поле скорости непрерывно меняется (никакого лупа).
        # Сам потенциал пересчитывается раз в несколько шагов: он эволюционирует
        # на масштабе секунд, а пересчёт — самая дорогая часть шага.
        self._psi_age += 1
        if self._psi is None or self._psi_age >= 6:
            self._psi_age = 0
            t = self.t
            ox1 = 3.1 + 1.9 * math.sin(t * 0.190)
            oy1 = 7.7 + 1.9 * math.cos(t * 0.150)
            ox2 = 11.3 + 2.6 * math.sin(t * 0.110 + 1.3)
            oy2 = 5.9 + 2.6 * math.cos(t * 0.130 + 0.4)
            # крупномасштабный потенциал: большие медленные вихри, чтобы
            # плоские зоны узора не размалывались в мелкую «лапшу»
            psi = _fbm(self._lat, self._Xn * 0.55 + ox1, self._Yn * 0.55 + oy1, octaves=2)
            self._psi = psi + 0.35 * _fbm(self._lat2, self._Xn * 1.35 + ox2,
                                          self._Yn * 1.35 + oy2, octaves=1)
        psi = self._psi

        # curl ψ → несжимаемая скорость (px/сек): производные центральными разностями
        s = 0.13 * gh * (1.0 + self.level * 2.4)
        vx = (np.roll(psi, -1, axis=0) - np.roll(psi, 1, axis=0)) * s
        vy = -(np.roll(psi, -1, axis=1) - np.roll(psi, 1, axis=1)) * s
        # глобальное «течение»: весь узор медленно плывёт сам по себе в
        # сторону, которая плавно покачивается — без мыши и без звука
        ang = self._drift_ang + 0.9 * math.sin(self.t * 0.047)
        m = 0.055 * gh * (1.0 + self.level * 1.5)
        vx += self._vx + m * math.cos(ang)
        vy += self._vy + m * math.sin(ang) * 0.7

        # Полулагранжев перенос краски с заворотом сетки (жидкость без границ)
        px = (self._X - vx * dt) % gw
        py = (self._Y - vy * dt) % gh
        x0 = np.minimum(px.astype(np.int32), gw - 1)   # float32 может донести 159.999… до 160
        y0 = np.minimum(py.astype(np.int32), gh - 1)
        fx = px - x0
        fy = py - y0
        x1 = (x0 + 1) % gw
        y1 = (y0 + 1) % gh
        d = self.d
        top = _lerp_wrap(d[y0, x0], d[y0, x1], fx)
        bot = _lerp_wrap(d[y1, x0], d[y1, x1], fx)
        d = _lerp_wrap(top, bot, fy) % 1.0

        # Подпитка: тянем краску к дрейфующему эталону (по кольцу — без швов)
        self._target_age += 1
        if self._target_age >= self.TARGET_EVERY:
            self._target_age = 0
            self._target = marble_field(gw, gh, self.seed, self.t * self.DRIFT_SPEED)
        diff = self._target - d
        diff -= np.round(diff)
        d = (d + diff * (self.FEED_RATE * dt * 30.0)) % 1.0

        # Перетяжка к центрам зон: плоские области не «размываются» переносом
        pos = d * self.BANDS
        i0 = np.floor(pos)
        center = (i0 + 0.5) / self.BANDS
        d = d + 0.035 * (center - d)
        self.d = np.ascontiguousarray(d % 1.0, dtype=np.float32)

        # Затухание импульсов (вязкость)
        dec = math.exp(-dt * 1.7)
        self._vx *= dec
        self._vy *= dec

        # Авто-бит: резкий скачок уровня звука → вихрь в случайной точке
        if self.level - self._prev_level > 0.10:
            rng = np.random.default_rng()
            self.splash(float(rng.uniform(gw * 0.15, gw * 0.85)),
                        float(rng.uniform(gh * 0.15, gh * 0.85)),
                        power=0.6 + self.level)
        self._prev_level = self.level

    def _upscaled(self, out_w: int, out_h: int) -> np.ndarray:
        key = (out_w, out_h, self.gw, self.gh)
        if self._up_key != key:
            ys = np.linspace(0, self.gh - 1, out_h).astype(np.float32)
            xs = np.linspace(0, self.gw - 1, out_w).astype(np.float32)
            x0 = np.minimum(xs.astype(np.int32), self.gw - 1)
            y0 = np.minimum(ys.astype(np.int32), self.gh - 1)
            self._up_x0, self._up_x1 = x0, np.minimum(x0 + 1, self.gw - 1)
            self._up_y0, self._up_y1 = y0, np.minimum(y0 + 1, self.gh - 1)
            self._up_fx = (xs - x0).astype(np.float32)[None, :]
            self._up_fy = (ys - y0).astype(np.float32)[:, None]
            self._up_key = key
        d = self.d
        tmp = _lerp_wrap(d[:, self._up_x0], d[:, self._up_x1], self._up_fx)
        return _lerp_wrap(tmp[self._up_y0], tmp[self._up_y1], self._up_fy) % 1.0

    def frame_argb32(self, out_w: int, out_h: int) -> np.ndarray:
        """Кадр HxW uint32 (0xFFRRGGBB) — готовый буфер для QImage.Format_RGB32."""
        return np.ascontiguousarray(colorize_bands32(self._upscaled(out_w, out_h), self.pal))

    def frame_rgb(self, out_w: int = 0, out_h: int = 0) -> np.ndarray:
        """Текущий кадр симуляции: HxWx3 uint8 в палитре темы.

        Если заданы out_w/out_h — «краска» сначала билейно растягивается до
        этого размера и лишь потом раскрашивается: границы зон становятся
        гладкими кривыми, а не «лестницей» из клеток грубой сетки.
        """
        if out_w < 2 or out_h < 2:
            return colorize_bands(self.d, self.pal)
        return colorize_bands(self._upscaled(out_w, out_h), self.pal)


def make_sim(gw: int, gh: int, seed: int = 1, pal: FluidPalette | None = None):
    """Симуляция жидкости: C++-ядро (native_fx), если оно есть, иначе numpy FluidSim.
    API одинаковый; у нативной ещё есть render_into(QImage) — кадр сразу в память картинки."""
    try:
        import native_fx
        if native_fx.available():
            return native_fx.FluidSimNative(gw, gh, seed=seed, pal=pal)
    except Exception as e:                                 # noqa: BLE001
        print("[fluid] native:", e)
    return FluidSim(gw, gh, seed=seed, pal=pal)


# ══════════════════════════════════════════════════════════════════════════ #
#  4. Тема
# ══════════════════════════════════════════════════════════════════════════ #

def _hex(c: RGB) -> str:
    return "#%02x%02x%02x" % c


def fluid_theme_dict(pal: FluidPalette | None = None, dark: bool = False) -> dict:
    """
    Словарь темы в формате themes.THEMES. Ключ "fluid": True — маркер,
    по которому main.py/themes.py понимают, что нужен особый режим
    (плоский фон вместо радиального градиента, FluidPanel вместо пластины и т.д.).
    dark=True — «чёрный» вариант: тёмные панели, светлый текст, тёмный флюид.
    """
    if dark:
        pal = pal or make_palette(DEFAULT_DOMINANT_DARK, dark=True)
        return {
            "fluid":      True,
            "fluid_dark": True,
            "bg":      _hex(DARK_BG),
            "panel":   _hex(DARK_PANEL),
            "panel2":  _hex(DARK_PANEL2),
            "glass":   "rgba(255,255,255,0.05)",
            "glass2":  "rgba(255,255,255,0.09)",
            "text":    _hex(DARK_TEXT),
            "muted":   "#84837a",
            "accent":  _hex(ORANGE),
            "accent2": pal.css("dominant"),      # ← меняется вместе с обложкой
            "glow":    "#000000",
            "border":  "rgba(255,255,255,0.10)",
            "border2": "rgba(255,255,255,0.18)",
            "danger":  "#ff7a59",
        }
    pal = pal or make_palette(DEFAULT_DOMINANT)
    return {
        "fluid":   True,
        "bg":      _hex(BEIGE),
        "panel":   _hex(CREAM),
        "panel2":  "#e3ddb8",
        "glass":   "rgba(46,44,30,0.05)",
        "glass2":  "rgba(46,44,30,0.09)",
        "text":    _hex(INK),
        "muted":   "#8a866a",
        "accent":  _hex(ORANGE),
        "accent2": pal.css("dominant"),          # ← меняется вместе с обложкой
        "glow":    "#000000",
        "border":  "rgba(46,44,30,0.12)",
        "border2": "rgba(46,44,30,0.22)",
        "danger":  "#c2410c",
    }


FLUID_FONT_STACK = '"Unbounded", "Syncopate", "Michroma", "Russo One", "Segoe UI Black", "Arial Black", sans-serif'


def fluid_qss(t: dict) -> str:
    """
    Полный QSS темы в духе главного меню Bomb Rush Cyberfunk:
      • панели — НЕ карточки: контент лежит прямо на плоском беже;
      • списки/поля/кнопки — кремовые «таблетки» без рамок;
      • крупные оранжевые строчные пункты меню (property fluidMenu);
      • транспорт — кремовая полоса-док (DockCard), кнопки в ней — жирный
        оранжевый текст (property fluidBig).
    Qt не понимает text-transform/letter-spacing — строчные буквы включаются
    через QFont.Capitalization в main.py.
    """
    ink, panel, panel2, bg = t["text"], t["panel"], t["panel2"], t["bg"]
    orange, dom = t["accent"], t["accent2"]
    muted, b1, b2 = t["muted"], t["border"], t["border2"]
    glass, glass2 = t["glass"], t["glass2"]
    return f"""
    * {{ font-family: {FLUID_FONT_STACK}; font-size: 11px; color: {ink}; }}
    QMainWindow, QWidget#Root {{ background: {bg}; }}
    QLabel {{ background: transparent; color: {ink}; }}

    /* ── Панели: без подложки — силуэт окна задают плашки и постер ── */
    QFrame#Panel     {{ background: transparent; border: none; }}
    QFrame#SubPanel  {{ background: {panel}; border: none; border-radius: 20px; }}
    QFrame#CenterStage {{ background: transparent; border: none; }}
    /* Транспорт — кремовая «полоса меню», упирается в постер */
    QFrame#DockCard  {{ background: {panel}; border: none; border-radius: 30px; }}

    /* ── Типографика ── */
    QLabel#Title  {{ font-size: 26px; font-weight: 900; color: {ink};
                    background: transparent; padding: 0px 2px; }}
    QLabel#Artist {{ font-size: 12px; font-weight: 600; color: {muted};
                    background: transparent; padding: 0px 2px; }}
    QLabel#Section {{ font-size: 14px; font-weight: 900; color: {orange}; }}
    QLabel#Big  {{ font-size: 14px; font-weight: 900; color: {orange}; }}
    QLabel#Sub  {{ font-size: 10px; color: {muted}; }}
    QLabel#Time {{ font-size: 10px; font-weight: 700; color: {muted}; }}

    /* ── Кнопки: кремовые таблетки ──
       радиус ≤ половины высоты самой низкой кнопки (~28 px), иначе Qt рисует прямые углы */
    QPushButton {{ background: {panel}; color: {ink}; border: none;
                  border-radius: 13px; padding: 8px 16px; font-weight: 700; }}
    QPushButton:hover   {{ color: {orange}; background: {panel2}; }}
    QPushButton:pressed {{ background: {orange}; color: {panel}; }}
    QPushButton:checked {{ background: {orange}; color: {panel}; }}

    QPushButton#PlayBtn {{ background: {orange}; border: none; border-radius: 26px;
                          min-width: 72px; max-width: 72px; min-height: 52px; max-height: 52px; }}
    QPushButton#PlayBtn:hover   {{ background: {dom}; }}
    QPushButton#PlayBtn:pressed {{ background: {ink}; }}

    QPushButton#IconBtn {{ background: transparent; color: {orange}; border: none;
                          border-radius: 16px; min-width: 58px; max-width: 76px;
                          min-height: 34px; max-height: 34px; padding: 0 10px;
                          font-size: 11px; font-weight: 800; }}
    QPushButton#IconBtn:hover   {{ background: {glass2}; }}
    QPushButton#IconBtn:checked {{ background: {orange}; color: {panel}; }}

    /* Крупный текст в полосе транспорта: «пред / след / повтор» */
    QPushButton#IconBtn[fluidBig="true"] {{ background: transparent; color: {orange};
                          font-size: 16px; font-weight: 900; border-radius: 18px;
                          min-width: 40px; max-width: 160px; min-height: 40px; max-height: 40px;
                          padding: 0 12px; }}
    QPushButton#IconBtn[fluidBig="true"]:hover   {{ background: {panel2}; color: {orange}; }}
    QPushButton#IconBtn[fluidBig="true"]:checked {{ background: {orange}; color: {panel}; }}

    /* Пункты «главного меню»: огромный оранжевый строчный текст */
    QPushButton#GhostBtn[fluidMenu="true"] {{ background: transparent; color: {orange}; border: none;
                          font-size: 30px; font-weight: 900; text-align: left;
                          padding: 2px 18px; border-radius: 22px;
                          min-width: 0px; }}
    QPushButton#GhostBtn[fluidMenu="true"]:hover   {{ background: {panel}; color: {orange}; }}
    QPushButton#GhostBtn[fluidMenu="true"]:checked {{ background: {orange}; color: {panel}; }}

    QPushButton#AccentBtn {{ background: {orange}; color: {panel}; border: none;
                            border-radius: 13px; font-weight: 900; }}
    QPushButton#AccentBtn:hover   {{ background: {ink}; color: {orange}; }}
    QPushButton#GhostBtn {{ background: transparent; color: {ink};
                           border: 2px solid {b2}; border-radius: 13px;
                           padding: 6px 14px; font-weight: 700; }}
    QPushButton#GhostBtn:hover   {{ border-color: {orange}; color: {orange}; }}
    QPushButton#GhostBtn:checked {{ background: {orange}; border-color: {orange}; color: {panel}; }}

    /* ── Слайдеры: прогресс окрашивается доминантным цветом обложки ── */
    QSlider::groove:horizontal {{ height: 6px; background: {b1}; border-radius: 3px; }}
    QSlider::sub-page:horizontal {{ background: {dom}; border-radius: 3px; }}
    QSlider::handle:horizontal {{ width: 14px; height: 14px; margin: -4px 0;
                                 background: {orange}; border-radius: 7px; }}
    QSlider::groove:vertical {{ width: 6px; background: {b1}; border-radius: 3px; }}
    QSlider::sub-page:vertical {{ background: {dom}; border-radius: 3px; }}
    QSlider::handle:vertical {{ width: 14px; height: 14px; margin: 0 -4px;
                               background: {orange}; border-radius: 7px; }}

    /* ── Списки: кремовые плашки на беже ── */
    QListWidget {{ background: {panel}; border: none; border-radius: 22px;
                  outline: none; color: {ink}; padding: 6px; }}
    QListWidget::item {{ padding: 8px 10px; margin: 1px 0px; border-radius: 16px; min-height: 36px; }}
    QListWidget::item:hover    {{ background: {glass2}; }}
    QListWidget::item:selected {{ background: {orange}; color: {panel}; font-weight: 800; }}

    /* ── Поля ввода ── */
    QLineEdit, QTextEdit, QPlainTextEdit {{ background: {panel}; color: {ink}; border: none;
                       border-radius: 14px; padding: 8px 14px; selection-background-color: {orange}; }}
    QTextEdit, QPlainTextEdit {{ border-radius: 22px; padding: 14px; }}
    QLineEdit:focus {{ border: 2px solid {orange}; }}
    QComboBox, QSpinBox {{ background: {panel}; color: {ink}; border: none;
                          border-radius: 14px; padding: 8px 14px; font-weight: 700; }}
    QComboBox::drop-down {{ border: none; width: 24px; }}
    QComboBox QAbstractItemView {{ background: {panel}; color: {ink}; border: 1px solid {b2};
                                  border-radius: 14px; selection-background-color: {orange};
                                  selection-color: {panel}; padding: 4px; }}

    QCheckBox {{ color: {ink}; spacing: 10px; font-weight: 700; }}
    QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 9px;
                           border: 2px solid {b2}; background: {panel}; }}
    QCheckBox::indicator:checked {{ background: {orange}; border-color: {orange}; }}

    QProgressBar {{ background: {panel2}; border: none; border-radius: 8px; height: 14px;
                   text-align: center; color: {ink}; }}
    QProgressBar::chunk {{ background: {orange}; border-radius: 8px; }}

    QTabWidget::pane {{ border: none; background: transparent; }}
    QTabBar::tab {{ background: transparent; color: {muted}; padding: 7px 18px;
                   border-radius: 14px; margin: 2px; font-weight: 800; }}
    QTabBar::tab:selected {{ background: {orange}; color: {panel}; }}
    QTabBar::tab:hover {{ color: {orange}; }}

    QScrollBar:vertical {{ background: transparent; width: 6px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {b2}; border-radius: 3px; min-height: 28px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar:horizontal {{ height: 0; }}

    QDialog {{ background: {bg}; }}
    QMenu {{ background: {panel}; color: {ink}; border: 1px solid {b2}; border-radius: 12px; padding: 6px; }}
    QMenu::item {{ padding: 6px 18px; border-radius: 8px; }}
    QMenu::item:selected {{ background: {orange}; color: {panel}; }}
    QToolTip {{ background: {panel}; color: {ink}; border: 1px solid {b2}; padding: 4px 8px; }}
    """
