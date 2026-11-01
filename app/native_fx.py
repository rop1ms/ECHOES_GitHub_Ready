# native_fx.py
"""
Нативное (C++) ядро визуальных эффектов ECHOES — echoes_fx.dll.

Что считается в C++ (исходники: tools/native, сборка tools/native/build.sh):
  * жидкость тем Fluid и Fluid Dark (и слоя «жидкость» в редакторе тем) — FluidSim;
  * MilkDrop темы Winamp (и слоя MilkDrop) — варп буфера обратной связи, волны, плазма,
    штампы слов, цветовой ремап, растяжение кадра до размера окна;
  * сияние «Моей волны» темы Echoes Music;
  * гауссово размытие (blur_fx);
  * компоновщик спрайтов esu! — круги, слайдеры, курсор, частицы, фон и HUD-картинки
    рисуются пачкой за один вызов (BatchPainter).

Все вызовы идут через ctypes: на время работы C++ GIL отпущен (звук и интерфейс не ждут),
внутри — свой пул потоков. Если DLL не загрузилась (или ECHOES_NO_NATIVE=1), всё работает
по-старому на numpy/QPainter: available() == False.
"""
from __future__ import annotations

import ctypes
import math
import os
import sys

import numpy as np
from PyQt6.QtGui import QImage

_PREMUL = QImage.Format.Format_ARGB32_Premultiplied
_DIR = os.path.dirname(os.path.abspath(__file__))
_lib = None
_err = ""


def _cpu_avx2() -> bool:
    try:
        # PF_AVX2_INSTRUCTIONS_AVAILABLE = 40 (Windows 10+)
        return bool(ctypes.windll.kernel32.IsProcessorFeaturePresent(40))
    except Exception:                                      # noqa: BLE001
        return False


def _load():
    global _lib, _err
    if os.environ.get("ECHOES_NO_NATIVE") == "1":
        _err = "disabled by ECHOES_NO_NATIVE"
        return None
    names = (["echoes_fx_avx2.dll"] if _cpu_avx2() else []) + ["echoes_fx.dll"]
    for n in names:
        p = os.path.join(_DIR, n)
        if not os.path.exists(p):
            continue
        try:
            lib = ctypes.CDLL(p)
            if lib.fx_version() < 1:
                continue
            _setup(lib)
            _lib = lib
            return lib
        except Exception as e:                             # noqa: BLE001
            _err = f"{n}: {e}"
    if not _err:
        _err = "echoes_fx.dll not found"
    return None


_f = ctypes.c_float
_i = ctypes.c_int
_p = ctypes.c_void_p


def _setup(lib):
    def sig(name, res, *args):
        fn = getattr(lib, name)
        fn.restype = res
        fn.argtypes = list(args)
    sig("fx_version", _i)
    sig("fx_threads", _i)
    sig("fx_set_threads", None, _i)
    sig("fx_upscale_u32", None, _p, _i, _i, _i, _p, _i, _i, _i)
    sig("fluid_new", _p)
    sig("fluid_free", None, _p)
    sig("fluid_set_seed", None, _p, _p, _p, _p, _f, _i)
    sig("fluid_resize", None, _p, _i, _i)
    sig("fluid_regrid", None, _p, _i, _i)
    sig("fluid_set_lut", None, _p, _p)
    sig("fluid_splash", None, _p, _f, _f, _f)
    sig("fluid_step", None, _p, _f, _f)
    sig("fluid_render", None, _p, _p, _i, _i, _i)
    sig("fluid_time", _f, _p)
    sig("fluid_get_dye", None, _p, _p)
    sig("milk_warp", None, _p, _p, _i, _i, _p)
    sig("milk_plot", None, _p, _i, _i, _f, _p, _p, _i, _p, _i, _i, _i)
    sig("milk_plasma", None, _p, _i, _i, _f, _f, _f, _f, _f, _p)
    sig("milk_stamp", None, _p, _i, _i, _p, _i, _i, _i, _i, _p)
    sig("milk_clip", None, _p, _i)
    sig("milk_remap", None, _p, _i, _i, _i, _p, _i, _f, _p)
    sig("glow_render", None, _p, _p, _i)
    sig("nebula_render", None, _p, _p, _i)
    sig("blur_argb32", None, _p, _i, _i, _i, _f, _i)
    sig("spr_register", _i, _p, _i, _i, _i)
    sig("spr_unregister", None, _i)
    sig("spr_register_copy", _i, _p, _i, _i, _i)
    sig("vizm_plasma", None, _p, _p)
    sig("vizm_compose", None, _p, _p, _p, _p, _i)
    sig("fx_capsules", None, _p, _i, _i, _i, _p, _p, _i, _i)
    sig("lw_plasma", None, _p, _i, _i, _i, _f, _f, _p)
    sig("clipfx_process", None, _p, _i, _p, _p, _i, _p, _p, _p, _p, _p, _p, _p)
    sig("slider_body", None, _p, _i, _i, _i, _p, _i, _f, _p, _i)
    sig("spr_render", _i, _p, _i, _i, _i, _p, _i, _p)
    try:                                                  # звук студии (studio.cpp) — в новых сборках DLL
        sig("st_vfilter", None, _p, _p, _i, _i, _p, _f, _i, _i)
        sig("st_wt_read", None, _p, _i, _i, _p, _p, _p, _i)
    except AttributeError:
        pass


_load()


def available() -> bool:
    return _lib is not None


def has_studio() -> bool:
    return _lib is not None and hasattr(_lib, "st_vfilter") and getattr(_lib.st_vfilter, "argtypes", None) is not None


def vfilter(x: np.ndarray, cut: np.ndarray, q: float, kind: int, sr: int = 44100) -> np.ndarray:
    """Биквад со срезом по отсчётам: x (n,) или (n, ch) → того же вида, float64."""
    a = np.ascontiguousarray(x, dtype=np.float64)
    shp = a.shape
    a2 = a.reshape(len(a), -1)
    y = np.empty_like(a2)
    c = _f32(cut)
    _lib.st_vfilter(_ptr(a2), _ptr(y), int(a2.shape[0]), int(a2.shape[1]), _ptr(c), float(q), int(kind), int(sr))
    return y.reshape(shp)


def wt_read(tab: np.ndarray, ph: np.ndarray, pos: np.ndarray) -> np.ndarray:
    t = _f32(tab)
    p = np.ascontiguousarray(ph, dtype=np.float64)
    q = _f32(pos)
    out = np.empty(len(p), np.float32)
    _lib.st_wt_read(_ptr(t), int(t.shape[0]), int(t.shape[1]), _ptr(p), _ptr(q), _ptr(out), int(len(p)))
    return out


def status() -> str:
    if _lib is None:
        return f"нет ({_err})"
    return f"C++ echoes_fx, потоков: {_lib.fx_threads()}"


def lib():
    return _lib


def _ptr(a: np.ndarray) -> int:
    return a.ctypes.data


def _f32(a) -> np.ndarray:
    return np.ascontiguousarray(a, dtype=np.float32)


def image_ptr(img) -> int:
    """Адрес пикселей QImage (без копии; QImage должна быть не общей — свежесозданной)."""
    return int(img.bits())


def upscale_into(src_u32: np.ndarray, img) -> None:
    """Билинейно растянуть кадр 0xFFRRGGBB (h×w uint32) в QImage RGB32/ARGB32."""
    h, w = src_u32.shape
    _lib.fx_upscale_u32(_ptr(src_u32), w, h, w, image_ptr(img), img.width(), img.height(),
                        img.bytesPerLine() // 4)


# ══════════════════════════════════════════════════════════════════════════ #
#  Жидкость (Fluid / Fluid Dark)
# ══════════════════════════════════════════════════════════════════════════ #

class FluidSimNative:
    """Тот же API, что у fluid_core.FluidSim, но шаг и раскраска — в C++."""

    BANDS = 8

    def __init__(self, gw: int, gh: int, seed: int = 1, pal=None):
        import fluid_core as fc
        self._fc = fc
        self._h = _lib.fluid_new()
        self.pal = pal or fc.make_palette(fc.DEFAULT_DOMINANT)
        self.seed = seed
        self.level = 0.0
        self.gw = self.gh = 0
        self._lut_pal = None
        self._set_seed(seed, reset=False)
        self.resize(gw, gh, seed)

    def __del__(self):
        try:
            if self._h and _lib is not None:
                _lib.fluid_free(self._h)
                self._h = None
        except Exception:                                  # noqa: BLE001
            pass

    @property
    def t(self) -> float:
        return float(_lib.fluid_time(self._h))

    def _set_seed(self, seed: int, reset: bool):
        fc = self._fc
        rng = np.random.default_rng(seed)
        params = np.array([rng.uniform(0.95, 1.30), rng.uniform(2.8, 3.6), rng.uniform(1.25, 1.75)]
                          + list(rng.uniform(0, 40, size=6)), np.float32)
        self._lat1 = _f32(fc._lattice(seed))
        self._lat2 = _f32(fc._lattice(seed + 0x9E37))
        self._params = params
        _lib.fluid_set_seed(self._h, _ptr(self._lat1), _ptr(self._lat2), _ptr(params),
                            (seed % 628) / 100.0, 1 if reset else 0)

    def _sync_lut(self):
        if self._lut_pal is not self.pal:
            self._lut = np.ascontiguousarray(self._fc._band_lut32(self.pal), dtype=np.uint32)
            _lib.fluid_set_lut(self._h, _ptr(self._lut))
            self._lut_pal = self.pal

    def resize(self, gw: int, gh: int, seed: int | None = None):
        gw = int(max(24, min(240, gw)))
        gh = int(max(32, min(160, gh)))
        if seed is not None and seed != self.seed:
            self.seed = seed
            self._set_seed(seed, reset=False)
        self.gw, self.gh = gw, gh
        _lib.fluid_resize(self._h, gw, gh)

    def regrid(self, gw: int, gh: int):
        gw = int(max(24, min(240, gw)))
        gh = int(max(32, min(160, gh)))
        if (gw, gh) == (self.gw, self.gh):
            return
        self.gw, self.gh = gw, gh
        _lib.fluid_regrid(self._h, gw, gh)

    def seed_dye(self, seed: int):
        self.seed = seed
        self._set_seed(seed, reset=True)

    def set_palette(self, pal):
        self.pal = pal

    def splash(self, gx: float, gy: float, power: float = 1.0):
        _lib.fluid_splash(self._h, float(gx), float(gy), float(power))

    def step(self, dt: float):
        _lib.fluid_step(self._h, float(dt), float(self.level))

    @property
    def d(self) -> np.ndarray:
        out = np.empty((self.gh, self.gw), np.float32)
        _lib.fluid_get_dye(self._h, _ptr(out))
        return out

    def render_into(self, img) -> None:
        """Кадр прямо в память QImage (RGB32) её размера — без копий и растяжений Qt."""
        self._sync_lut()
        _lib.fluid_render(self._h, image_ptr(img), img.width(), img.height(), img.bytesPerLine() // 4)

    def frame_argb32(self, out_w: int, out_h: int) -> np.ndarray:
        self._sync_lut()
        out = np.empty((max(1, out_h), max(1, out_w)), np.uint32)
        _lib.fluid_render(self._h, _ptr(out), out.shape[1], out.shape[0], out.shape[1])
        return out

    def frame_rgb(self, out_w: int = 0, out_h: int = 0) -> np.ndarray:
        if out_w < 2 or out_h < 2:
            out_w, out_h = self.gw, self.gh
        a = self.frame_argb32(out_w, out_h)
        rgb = np.empty(a.shape + (3,), np.uint8)
        rgb[..., 0] = (a >> 16) & 255
        rgb[..., 1] = (a >> 8) & 255
        rgb[..., 2] = a & 255
        return rgb


# ══════════════════════════════════════════════════════════════════════════ #
#  MilkDrop
# ══════════════════════════════════════════════════════════════════════════ #

class _WarpParams(ctypes.Structure):
    _fields_ = [(n, _f) for n in ("aspect", "cx", "cy", "lz", "zexp", "rot_c", "rot_s", "an", "wa", "ws", "wt")] + \
               [("wmode", _i)] + [(n, _f) for n in ("addx", "addy", "decay", "sub", "flash")]


def milk_warp(src: np.ndarray, dst: np.ndarray, **kw):
    h, w = src.shape[:2]
    p = _WarpParams(**kw)
    _lib.milk_warp(_ptr(src), _ptr(dst), w, h, ctypes.byref(p))


def milk_plot(buf: np.ndarray, aspect: float, xs, ys, col, thick: int, dense: bool):
    h, w = buf.shape[:2]
    xs = _f32(xs).ravel()
    ys = _f32(ys).ravel()
    n = min(len(xs), len(ys))
    if n <= 0:
        return
    col = _f32(col)
    ncol = 1 if col.ndim == 1 else len(col)
    if ncol > 1 and ncol < n:
        n = ncol
    _lib.milk_plot(_ptr(buf), w, h, float(aspect), _ptr(xs), _ptr(ys), n, _ptr(col), ncol, int(thick),
                   1 if dense else 0)


def milk_plasma(buf, aspect, t, cx, cy, k, col):
    h, w = buf.shape[:2]
    col = _f32(col)
    _lib.milk_plasma(_ptr(buf), w, h, float(aspect), float(t), float(cx), float(cy), float(k), _ptr(col))


def milk_stamp(buf, mask, x0, y0, col):
    h, w = buf.shape[:2]
    mask = _f32(mask)
    mh, mw = mask.shape
    col = _f32(col)
    _lib.milk_stamp(_ptr(buf), w, h, _ptr(mask), mw, mh, int(x0), int(y0), _ptr(col))


def milk_clip(buf):
    _lib.milk_clip(_ptr(buf), int(buf.size))


def milk_remap(buf, mode: int, lut, addk: float) -> np.ndarray:
    h, w = buf.shape[:2]
    out = np.empty((h, w), np.uint32)
    if lut is None:
        lut = np.zeros((2, 3), np.float32)
    lut = _f32(lut)
    _lib.milk_remap(_ptr(buf), w, h, int(mode), _ptr(lut), len(lut), float(addk), _ptr(out))
    return out


# ══════════════════════════════════════════════════════════════════════════ #
#  Сияние «Моей волны»
# ══════════════════════════════════════════════════════════════════════════ #

class GlowParams(ctypes.Structure):
    _fields_ = [("W", _i), ("H", _i), ("x0", _i), ("gw", _i), ("gh", _i),
                ("aspect", _f), ("cxo", _f), ("cyo", _f), ("ox", _f), ("oy", _f),
                ("T", _f), ("energy", _f), ("kick", _f), ("beat", _f),
                ("core", _f * 3), ("halo", _f * 3), ("ray", _f * 3),
                ("nblobs", _i), ("blobs", _f * 24), ("nrings", _i), ("rings", _f * 16)]


def glow_render(params: GlowParams, img) -> None:
    _lib.glow_render(ctypes.byref(params), image_ptr(img), img.bytesPerLine() // 4)


class NebParams(ctypes.Structure):
    """«Моя волна» в стиле Яндекс Музыки (nebula.cpp)."""
    _fields_ = [("W", _i), ("H", _i), ("x0", _i), ("gw", _i), ("gh", _i),
                ("cx", _f), ("cy", _f), ("R", _f),
                ("T", _f), ("flow", _f), ("energy", _f), ("kick", _f),
                ("gain", _f), ("fil_amt", _f), ("ray_amt", _f), ("grain", _f),
                ("fil_col", _f * 3), ("nblobs", _i), ("blobs", _f * 84), ("nrings", _i), ("rings", _f * 12),
                ("ring_col", _f * 3), ("seed", _i), ("env_r", _f), ("lobe", _f)]


def nebula_render(params: NebParams, img) -> None:
    _lib.nebula_render(ctypes.byref(params), image_ptr(img), img.bytesPerLine() // 4)


# ══════════════════════════════════════════════════════════════════════════ #
#  Фон MilkdropVisualizer
# ══════════════════════════════════════════════════════════════════════════ #

class VizPlasmaParams(ctypes.Structure):
    _fields_ = [("pw", _i), ("ph", _i)] + [(n, _f) for n in (
        "t", "eb", "em", "eh", "warp", "swirl", "hue_speed", "ph0", "ph1", "ph2", "ph3", "ph4")]


class VizComposeParams(ctypes.Structure):
    _fields_ = [("W", _i), ("H", _i), ("pw", _i), ("ph", _i), ("zoom", _f), ("rot_deg", _f), ("radius", _f),
                ("has_prev", _i)]


def vizm_plasma(params: VizPlasmaParams) -> np.ndarray:
    out = np.empty((params.ph, params.pw), np.uint32)
    _lib.vizm_plasma(ctypes.byref(params), _ptr(out))
    return out


def vizm_compose(params: VizComposeParams, prev, cur, img) -> None:
    _lib.vizm_compose(ctypes.byref(params), _ptr(prev) if prev is not None else _ptr(cur), _ptr(cur),
                      image_ptr(img), img.bytesPerLine() // 4)


def hsv_premul(h, s, v, a) -> np.ndarray:
    """Массивы HSV (доли) + альфа (0..1) → премультиплицированные 0xAARRGGBB (uint32)."""
    h = (np.asarray(h, np.float32) % 1.0) * 6.0
    s = np.broadcast_to(np.asarray(s, np.float32), h.shape)
    v = np.broadcast_to(np.asarray(v, np.float32), h.shape)
    a = np.clip(np.broadcast_to(np.asarray(a, np.float32), h.shape), 0.0, 1.0)
    i = np.floor(h).astype(np.int32) % 6
    f = h - np.floor(h)
    p, q, t = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
    r = np.choose(i, [v, q, p, p, t, v])
    g = np.choose(i, [t, v, v, q, p, p])
    b = np.choose(i, [p, p, t, v, v, q])
    k = a * 255.0
    A_ = (k + 0.5).astype(np.uint32)
    R = (np.clip(r, 0, 1) * k + 0.5).astype(np.uint32)
    G = (np.clip(g, 0, 1) * k + 0.5).astype(np.uint32)
    B = (np.clip(b, 0, 1) * k + 0.5).astype(np.uint32)
    return np.ascontiguousarray((A_ << 24) | (R << 16) | (G << 8) | B, dtype=np.uint32)


def lw_plasma(img, t: float, boost: float, lut32: np.ndarray) -> None:
    """Плазма живых обоев прямо в QImage (32 бита) её размера; lut32 — 256 цветов 0xFFRRGGBB."""
    _lib.lw_plasma(image_ptr(img), img.width(), img.height(), img.bytesPerLine() // 4, float(t), float(boost),
                   _ptr(lut32))


def capsules(img, caps: np.ndarray, colors: np.ndarray, polyline: bool = False) -> None:
    """Сглаженные отрезки с круглыми концами/кружки (caps: n×5 x0,y0,x1,y1,радиус — пиксели устройства)."""
    caps = np.ascontiguousarray(caps, np.float32)
    colors = np.ascontiguousarray(colors, np.uint32)
    if len(caps) == 0:
        return
    _lib.fx_capsules(image_ptr(img), img.width(), img.height(), img.bytesPerLine() // 4, _ptr(caps), _ptr(colors),
                     len(caps), 1 if polyline else 0)


# ══════════════════════════════════════════════════════════════════════════ #
#  Тело слайдера esu!
# ══════════════════════════════════════════════════════════════════════════ #

def body_profile(shadow_a: float, border, border_ext: float, outer, inner, alpha0: float, alpha1: float,
                 hw_a: float, hw_b: float, n: int = 512) -> np.ndarray:
    """Таблица «цвет от расстояния до оси» (доли радиуса 0…1) в духе слоёв osu!: тень до края,
    рамка до border_ext, внутри — градиент outer → inner (слой k виден до hw_a·(1 − hw_b·k)).
    Цвета — (r, g, b, a) в 0…1, результат — премультиплицированный n×4 float32."""
    d = np.linspace(0.0, 1.0, n, dtype=np.float32)
    prof = np.zeros((n, 4), np.float32)
    if shadow_a > 0:
        prof[d <= 1.0] = (0.0, 0.0, 0.0, shadow_a)
    if border is not None:
        br, bg, bb, ba = border
        prof[d <= border_ext] = (br * ba, bg * ba, bb * ba, ba)
    m = d <= hw_a
    k = np.clip((1.0 - d[m] / hw_a) / hw_b, 0.0, 1.0)
    o, i_ = np.asarray(outer, np.float32), np.asarray(inner, np.float32)
    col = o[None, :] + (i_ - o)[None, :] * k[:, None]
    a = (alpha0 + (alpha1 - alpha0) * k)[:, None]
    prof[m, :3] = col * a
    prof[m, 3] = a[:, 0]
    return np.ascontiguousarray(prof)


def slider_body_image(pts_dev: np.ndarray, W: int, H: int, R_dev: float, prof: np.ndarray) -> QImage:
    img = QImage(max(1, W), max(1, H), _PREMUL)
    img.fill(0)
    pts = np.ascontiguousarray(pts_dev, np.float32)
    _lib.slider_body(image_ptr(img), img.width(), img.height(), img.bytesPerLine() // 4, _ptr(pts), len(pts),
                     float(R_dev), _ptr(prof), len(prof))
    return img


# ══════════════════════════════════════════════════════════════════════════ #
#  Эффекты режима клипа
# ══════════════════════════════════════════════════════════════════════════ #

class ClipParams(ctypes.Structure):
    _fields_ = [("w", _i), ("h", _i), ("on", ctypes.c_uint32), ("t", _f), ("k", _f), ("bass", _f), ("beat", _f),
                ("shake_dx", _i), ("shake_dy", _i), ("zoom_s", _f), ("kal_n", _i), ("kal_rot", _f), ("kal_zoom", _f),
                ("droste_flip", _i), ("pixel_b", _i), ("glitch_n", _i), ("glitch", _i * 80), ("rgb_s", _i),
                ("echo_s", _i), ("hue_m", _f * 9), ("edge_gain", _f), ("edge_col", _f * 3), ("poster_step", _i),
                ("solar_thr", _i), ("vhs_shift", _i), ("vhs_band_y", _i), ("vhs_band_h", _i), ("vhs_band_v", _i),
                ("vhs_noise_max", _i), ("seed", ctypes.c_uint32), ("tunnel_s", _f), ("tunnel_deg", _f),
                ("tunnel_keep", _f), ("trails_keep", _f), ("invert", _i), ("strobe_mix", _f), ("strobe_v", _i)]


def clipfx_process(img, params: ClipParams, prev, prev_valid: bool, wave_dx, wave_dy, melt, acid_lut, thermal,
                   matrix, vhs_jit) -> None:
    def p(a):
        return _ptr(a) if a is not None else None
    _lib.clipfx_process(image_ptr(img), img.bytesPerLine() // 4, ctypes.byref(params), p(prev), 1 if prev_valid else 0,
                        p(wave_dx), p(wave_dy), p(melt), p(acid_lut), p(thermal), p(matrix), p(vhs_jit))


# ══════════════════════════════════════════════════════════════════════════ #
#  Размытие
# ══════════════════════════════════════════════════════════════════════════ #

def blur_qimage(img, sigma: float, dither: bool = False) -> None:
    """Гаусс на месте; img — QImage ARGB32_Premultiplied (не общая)."""
    _lib.blur_argb32(image_ptr(img), img.width(), img.height(), img.bytesPerLine() // 4, float(sigma),
                     1 if dither else 0)


# ══════════════════════════════════════════════════════════════════════════ #
#  Компоновщик спрайтов: BatchPainter
# ══════════════════════════════════════════════════════════════════════════ #
#
# Замена QPainter для сцен из картинок (esu!): кадр рисуется в собственную QImage, картинки
# (drawPixmap/drawImage/blit) и сплошные заливки (fillRect цветом) копятся списком и
# отдаются C++ одним вызовом — с аффинным преобразованием, прозрачностью и режимом смешения
# (SourceOver / Plus / Source). Всё прочее (текст, фигуры, градиенты, клип, экзотические
# режимы) идёт в настоящий QPainter той же картинки — перед этим накопленное дорисовывается,
# так что порядок наложения не меняется. В конце кадр копируется в виджет 1:1.

from array import array as _array

from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap

_CM = QPainter.CompositionMode
_BLEND = {_CM.CompositionMode_SourceOver: 0, _CM.CompositionMode_Plus: 1, _CM.CompositionMode_Source: 2}
_FMT_OK = (QImage.Format.Format_ARGB32_Premultiplied, QImage.Format.Format_RGB32)
_PREMUL = QImage.Format.Format_ARGB32_Premultiplied
_GLOBAL_COLOR = type(Qt.GlobalColor.black)
_RECTS = (QRectF, QRect)
_POINTS = (QPointF, QPoint)


class SpriteCache:
    """QPixmap → картинка, зарегистрированная в C++.

    Впервые увиденная картинка регистрируется ВРЕМЕННО (до ближайшего сброса пачки): C++
    смотрит прямо в память QPixmap, ссылка отпускается сразу после отрисовки — поэтому
    картинки, которые сцена перерисовывает каждый кадр (служебные буферы), Qt не приходится
    копировать. Встреченная повторно (спрайты скина, фон) копируется в память C++ один раз
    и живёт в кэше; давно неиспользуемые выселяются."""

    MAX_BYTES = 256 << 20
    MAX_AGE = 240                 # кадров без использования

    def __init__(self):
        self.map: dict = {}       # cacheKey → [id, None, w, h, last_frame, bytes]
        self.seen: dict = {}      # cacheKey → кадр первой встречи
        self.frame = 0
        self.bytes = 0

    def get(self, pm, tmp: list):
        """id для отрисовки; tmp — список временных регистраций (отпускаются при сбросе)."""
        key = pm.cacheKey()
        e = self.map.get(key)
        if e is not None:
            e[4] = self.frame
            return e[0]
        img = pm.toImage()
        if img.isNull():
            return None
        if img.format() not in _FMT_OK:
            img = img.convertToFormat(_PREMUL)
        first = self.seen.get(key)
        if first is None or first == self.frame:
            if first is None:
                self.seen[key] = self.frame
            sid = _lib.spr_register(int(img.constBits()), img.width(), img.height(), img.bytesPerLine())
            tmp.append((sid, img))
            return sid
        self.seen.pop(key, None)
        nb = img.width() * img.height() * 4
        sid = _lib.spr_register_copy(int(img.constBits()), img.width(), img.height(), img.bytesPerLine())
        if sid < 0:
            return None
        self.map[key] = [sid, None, img.width(), img.height(), self.frame, nb]
        self.bytes += nb
        return sid

    def tick(self):
        """Раз в кадр: выселить давно неиспользуемые и лишние по памяти."""
        self.frame += 1
        if self.frame % 30 and self.bytes < self.MAX_BYTES:
            return
        old = self.frame - self.MAX_AGE
        drop = [k for k, e in self.map.items() if e[4] < old]
        if self.bytes - sum(self.map[k][5] for k in drop) > self.MAX_BYTES:
            rest = sorted((e[4], k) for k, e in self.map.items() if e[4] >= old)
            need = self.bytes - self.MAX_BYTES
            for _, k in rest:
                if need <= 0:
                    break
                drop.append(k)
                need -= self.map[k][5]
        for k in drop:
            e = self.map.pop(k, None)
            if e is not None:
                _lib.spr_unregister(e[0])
                self.bytes -= e[5]
        if len(self.seen) > 512:                       # одноразовые картинки не копим
            self.seen = {k: f for k, f in self.seen.items() if f >= self.frame - 30}

    def clear(self):
        for e in self.map.values():
            _lib.spr_unregister(e[0])
        self.map.clear()
        self.seen.clear()
        self.bytes = 0


_NOFLUSH = frozenset((
    "setPen", "setBrush", "setFont", "setRenderHint", "setRenderHints", "pen", "brush", "font",
    "fontMetrics", "fontInfo", "renderHints", "testRenderHint", "device", "isActive", "opacity",
    "compositionMode", "transform", "worldTransform", "deviceTransform", "hasClipping",
    "setBackground", "setBackgroundMode", "setBrushOrigin", "setLayoutDirection", "background",
    "viewport", "window", "clipBoundingRect", "boundingRect",
))
_TRANSFORMERS = frozenset(("translate", "rotate", "scale", "shear", "setTransform", "setWorldTransform",
                           "resetTransform", "setViewport", "setWindow", "setWorldMatrixEnabled",
                           "setViewTransformEnabled"))
_CLIPPERS = frozenset(("setClipRect", "setClipPath", "setClipRegion", "setClipping"))


class BatchPainter:
    """Совместим с QPainter по интерфейсу (то, чего здесь нет, пробрасывается через __getattr__)."""

    def __init__(self, img: QImage, cache: SpriteCache):
        self._img = img
        self._w, self._h = img.width(), img.height()
        self._bpl = img.bytesPerLine()
        self._ptr = int(img.bits())                  # до QPainter — память картинки не переедет
        self._cache = cache
        self._p = QPainter(img)
        self._rec = _array("f")
        self._n = 0
        self._op = 1.0
        self._blend = 0
        self._clip = False
        self._m = None                               # (m11, m12, m21, m22, dx, dy) устройства
        self._stack: list = []
        self._tmp: list = []                         # временные картинки (QImage) до сброса
        self.shared = False                          # отдан чужому коду: его end() только дорисовывает
        self.batched = 0
        self.flushes = 0

    # ── состояние ── #

    def setOpacity(self, a):
        self._op = float(a)
        self._p.setOpacity(a)

    def setCompositionMode(self, m):
        self._blend = _BLEND.get(m, -1)
        self._p.setCompositionMode(m)

    def save(self):
        self._p.save()
        self._stack.append((self._op, self._blend, self._m, self._clip))

    def restore(self):
        self._p.restore()
        if self._stack:
            self._op, self._blend, self._m, self._clip = self._stack.pop()
        else:
            self._op, self._m = self._p.opacity(), None
            self._blend = _BLEND.get(self._p.compositionMode(), -1)
            self._clip = self._p.hasClipping()

    def _matrix(self):
        m = self._m
        if m is None:
            t = self._p.deviceTransform()
            m = self._m = (t.m11(), t.m12(), t.m21(), t.m22(), t.dx(), t.dy())
        return m

    def __getattr__(self, name):
        fn = getattr(self._p, name)
        if name in _NOFLUSH:
            return fn
        if name in _TRANSFORMERS:
            def tf(*a, **k):
                r = fn(*a, **k)
                self._m = None
                return r
            return tf
        if name in _CLIPPERS:
            def cf(*a, **k):
                self.flush()
                r = fn(*a, **k)
                self._clip = self._p.hasClipping()
                return r
            return cf

        def call(*a, **k):
            self.flush()
            return fn(*a, **k)
        return call

    # ── команды ── #

    def _sid(self, src):
        if isinstance(src, QPixmap):
            return self._cache.get(src, self._tmp)
        if src.isNull():
            return None
        img = src if src.format() in _FMT_OK else src.convertToFormat(_PREMUL)
        sid = _lib.spr_register(int(img.constBits()), img.width(), img.height(), img.bytesPerLine())
        self._tmp.append((sid, img))
        return sid

    def _push_sprite(self, src, lx, ly, lw, lh, sx, sy, sw, sh) -> bool:
        """Картинка src (QPixmap/QImage), её часть (sx, sy, sw, sh) в пикселях — в логический
        прямоугольник (lx, ly, lw, lh) с текущими преобразованием/прозрачностью/смешением."""
        if self._blend < 0 or self._clip:
            return False
        op = self._op
        if op <= 0.002 or sw <= 0 or sh <= 0:
            return True
        sid = self._sid(src)
        if sid is None:
            return True
        m11, m12, m21, m22, dx, dy = self._matrix()
        kx, ky = lw / sw, lh / sh
        ox, oy = lx - sx * kx, ly - sy * ky
        self._rec.extend((0.0, sid, m11 * kx, m12 * kx, m21 * ky, m22 * ky,
                          m11 * ox + m21 * oy + dx, m12 * ox + m22 * oy + dy,
                          sx, sy, sw, sh, op, self._blend, 0.0, 0.0))
        self._n += 1
        return True

    def blit(self, pm, cx, cy, sc=1.0, a=1.0, rot=0.0) -> bool:
        """Как osu_game.blit, но без save/translate/rotate: поворот уходит прямо в матрицу.
        False — не получилось (клип/режим), рисуйте обычным путём."""
        if self._blend < 0 or self._clip:
            return False
        op = min(1.0, a)
        self._op = op
        self._p.setOpacity(op)
        if op <= 0.004 or pm is None:
            return True
        d = pm.devicePixelRatio()
        pw, ph = pm.width(), pm.height()
        w, h = pw / d * sc, ph / d * sc
        if not rot:
            if sc == 1.0:
                return self._push_sprite(pm, round(cx - w / 2), round(cy - h / 2), w, h, 0, 0, pw, ph)
            return self._push_sprite(pm, cx - w / 2, cy - h / 2, w, h, 0, 0, pw, ph)
        sid = self._sid(pm)
        if sid is None:
            return True
        r = math.radians(rot)
        c, s = math.cos(r), math.sin(r)
        m11, m12, m21, m22, dx, dy = self._matrix()
        # пиксель (u, v) → логика: центр + R·((u, v)·k − (w, h)/2)
        k = w / pw
        la, lb, lc, ld = c * k, s * k, -s * k, c * k
        ltx = cx - (c * w / 2 - s * h / 2)
        lty = cy - (s * w / 2 + c * h / 2)
        self._rec.extend((0.0, sid, m11 * la + m21 * lb, m12 * la + m22 * lb, m11 * lc + m21 * ld,
                          m12 * lc + m22 * ld, m11 * ltx + m21 * lty + dx, m12 * ltx + m22 * lty + dy,
                          0.0, 0.0, pw, ph, op, self._blend, 0.0, 0.0))
        self._n += 1
        return True

    def sprites_affine(self, pm, la, lb, lc, ld, tx, ty, op=None) -> bool:
        """Много копий картинки pm одной командой, у каждой своё аффинное преобразование (массивы numpy
        одной длины): пиксель (u, v) картинки → логическая точка (la·u + lc·v + tx, lb·u + ld·v + ty).
        Лучи визуализатора, частицы и т. п. — без цикла Python на каждую копию. False — рисуйте сами."""
        if self._blend < 0 or self._clip:
            return False
        n = len(la)
        if n == 0:
            return True
        op = self._op if op is None else op
        if np.ndim(op) == 0:
            op = float(op)
            if op <= 0.002:
                return True
        sid = self._sid(pm)
        if sid is None:
            return True
        m11, m12, m21, m22, dx, dy = self._matrix()
        rec = np.zeros((n, 16), np.float32)
        rec[:, 1] = sid
        rec[:, 2] = m11 * la + m21 * lb
        rec[:, 3] = m12 * la + m22 * lb
        rec[:, 4] = m11 * lc + m21 * ld
        rec[:, 5] = m12 * lc + m22 * ld
        rec[:, 6] = m11 * tx + m21 * ty + dx
        rec[:, 7] = m12 * tx + m22 * ty + dy
        rec[:, 10] = pm.width()
        rec[:, 11] = pm.height()
        rec[:, 12] = op
        rec[:, 13] = self._blend
        self._rec.frombytes(rec.tobytes())
        self._n += n
        return True

    def blits(self, pm, xs, ys, sc, a) -> bool:
        """Как много вызовов osu_game.blit одной картинки без поворота (центр xs, ys; масштаб sc;
        прозрачность a — числа или массивы numpy). False — рисуйте по одной."""
        xs = np.asarray(xs, np.float64)
        a = np.minimum(1.0, np.broadcast_to(np.asarray(a, np.float64), xs.shape))
        keep = a > 0.004
        if not keep.all():
            xs, a = xs[keep], a[keep]
            ys = np.asarray(ys, np.float64)[keep]
            sc = np.broadcast_to(np.asarray(sc, np.float64), keep.shape)[keep]
        k = np.broadcast_to(np.asarray(sc, np.float64), xs.shape) / pm.devicePixelRatio()
        w, h = pm.width() * k, pm.height() * k
        z = np.zeros_like(k)
        return self.sprites_affine(pm, k, z, z, k, xs - w / 2, np.asarray(ys, np.float64) - h / 2, a)

    def drawPixmap(self, *a):
        ok = False
        n = len(a)
        try:
            if n == 2 and isinstance(a[1], QPixmap):
                pt, pm = a
                if isinstance(pt, _POINTS):
                    d = pm.devicePixelRatio()
                    ok = self._push_sprite(pm, pt.x(), pt.y(), pm.width() / d, pm.height() / d,
                                           0, 0, pm.width(), pm.height())
                elif isinstance(pt, _RECTS):
                    ok = self._push_sprite(pm, pt.x(), pt.y(), pt.width(), pt.height(), 0, 0, pm.width(), pm.height())
            elif n == 3 and isinstance(a[1], QPixmap):
                t, pm, s = a
                if isinstance(t, _RECTS) and isinstance(s, _RECTS):
                    ok = self._push_sprite(pm, t.x(), t.y(), t.width(), t.height(), s.x(), s.y(), s.width(), s.height())
                elif isinstance(t, _POINTS) and isinstance(s, _RECTS):
                    d = pm.devicePixelRatio()
                    ok = self._push_sprite(pm, t.x(), t.y(), s.width() / d, s.height() / d,
                                           s.x(), s.y(), s.width(), s.height())
            elif n == 3 and isinstance(a[2], QPixmap) and isinstance(a[0], (int, float)):
                x, y, pm = a
                d = pm.devicePixelRatio()
                ok = self._push_sprite(pm, x, y, pm.width() / d, pm.height() / d, 0, 0, pm.width(), pm.height())
        except Exception:                                  # noqa: BLE001
            ok = False
        if not ok:
            self.flush()
            self._p.drawPixmap(*a)

    def drawImage(self, *a):
        ok = False
        n = len(a)
        try:
            if n == 2 and isinstance(a[1], QImage):
                pt, im = a
                d = im.devicePixelRatio()
                if isinstance(pt, _POINTS):
                    ok = self._push_sprite(im, pt.x(), pt.y(), im.width() / d, im.height() / d,
                                           0, 0, im.width(), im.height())
                elif isinstance(pt, _RECTS):
                    ok = self._push_sprite(im, pt.x(), pt.y(), pt.width(), pt.height(), 0, 0, im.width(), im.height())
            elif n == 3 and isinstance(a[1], QImage) and isinstance(a[0], _RECTS) and isinstance(a[2], _RECTS):
                t, im, s = a
                ok = self._push_sprite(im, t.x(), t.y(), t.width(), t.height(), s.x(), s.y(), s.width(), s.height())
        except Exception:                                  # noqa: BLE001
            ok = False
        if not ok:
            self.flush()
            self._p.drawImage(*a)

    def fillRect(self, *a):
        if self._blend >= 0 and not self._clip and len(a) == 2:
            r, c = a
            if isinstance(c, _GLOBAL_COLOR):
                c = QColor(c)
            if isinstance(c, QColor) and isinstance(r, _RECTS):
                m11, m12, m21, m22, dx, dy = self._matrix()
                if abs(m12) < 1e-6 and abs(m21) < 1e-6:          # без поворота — прямоугольник
                    x0, y0 = r.x() * m11 + dx, r.y() * m22 + dy
                    w, h = r.width() * m11, r.height() * m22
                    if w < 0:
                        x0, w = x0 + w, -w
                    if h < 0:
                        y0, h = y0 + h, -h
                    if self._op > 0.002:
                        self._rec.extend((1.0, 0.0, x0, y0, w, h, 0.0, 0.0, c.redF(), c.greenF(), c.blueF(),
                                          c.alphaF(), self._op, self._blend, 0.0, 0.0))
                        self._n += 1
                    return
        self.flush()
        self._p.fillRect(*a)

    # ── сброс ── #

    def flush(self):
        n = self._n
        if n:
            addr, _ = self._rec.buffer_info()
            _lib.spr_render(self._ptr, self._w, self._h, self._bpl, addr, n, None)
            self.batched += n
            self.flushes += 1
            self._rec = _array("f")
            self._n = 0
        if self._tmp:
            for sid, _img in self._tmp:
                _lib.spr_unregister(sid)
            self._tmp.clear()

    def end(self):
        self.flush()
        if self.shared:
            return True
        return self._p.end()

    def finish(self):
        self.flush()
        if self._p.isActive():
            self._p.end()

    def isActive(self):
        return self._p.isActive()

    @property
    def painter(self) -> QPainter:
        """Настоящий QPainter (сначала дорисовать накопленное)."""
        self.flush()
        return self._p


def _passthrough(name):
    def call(self, *a, **k):
        if self._n or self._tmp:
            self.flush()
        return getattr(self._p, name)(*a, **k)
    call.__name__ = name
    return call


for _name in ("drawText", "drawRoundedRect", "drawEllipse", "drawPath", "fillPath", "strokePath", "drawLine",
              "drawLines", "drawPolygon", "drawPolyline", "drawConvexPolygon", "drawRect", "drawRects", "drawPie",
              "drawArc", "drawChord", "drawPoint", "drawPoints", "drawStaticText", "drawTiledPixmap",
              "eraseRect", "drawGlyphRun", "drawPicture"):
    setattr(BatchPainter, _name, _passthrough(_name))
for _name in ("setPen", "setBrush", "setFont", "setRenderHint", "setRenderHints", "fontMetrics", "pen", "brush",
              "font", "opacity", "compositionMode", "device", "hasClipping"):
    setattr(BatchPainter, _name, (lambda n: lambda self, *a, **k: getattr(self._p, n)(*a, **k))(_name))
for _name in ("translate", "rotate", "scale", "shear", "setTransform", "setWorldTransform", "resetTransform"):
    def _tf(self, *a, _n=_name, **k):
        self._m = None
        return getattr(self._p, _n)(*a, **k)
    setattr(BatchPainter, _name, _tf)
del _name


class FrameTarget:
    """Буфер кадра виджета для BatchPainter: begin() → BatchPainter, present(painter виджета)."""

    def __init__(self):
        self.img = None
        self.cache = SpriteCache()

    def begin(self, w: int, h: int, dpr: float) -> BatchPainter:
        W, H = max(1, int(round(w * dpr))), max(1, int(round(h * dpr)))
        if self.img is None or self.img.width() != W or self.img.height() != H:
            self.img = QImage(W, H, _PREMUL)
            self.img.fill(0)
        self.img.setDevicePixelRatio(dpr)
        self.cache.tick()
        return BatchPainter(self.img, self.cache)

    def present(self, p: QPainter):
        p.setCompositionMode(_CM.CompositionMode_Source)
        p.drawImage(QPointF(0, 0), self.img)
        p.setCompositionMode(_CM.CompositionMode_SourceOver)

    def release(self):
        self.cache.clear()
        self.img = None
