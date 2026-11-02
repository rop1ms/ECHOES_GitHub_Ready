# native_fx.py
"""
Нативное (C++) ядро визуальных эффектов ECHOES — echoes_fx.dll.

Что считается в C++ (исходники: tools/native, сборка tools/native/build.sh):
  * жидкость тем Fluid и Fluid Dark (и слоя «жидкость» в редакторе тем) — FluidSim;
  * MilkDrop темы Winamp (и слоя MilkDrop) — варп буфера обратной связи, волны, плазма,
    штампы слов, цветовой ремап, растяжение кадра до размера окна;
  * сияние «Моей волны» темы Echoes Music;
  * гауссово размытие (blur_fx).

Все вызовы идут через ctypes: на время работы C++ GIL отпущен (звук и интерфейс не ждут),
внутри — свой пул потоков. Если DLL не загрузилась (или ECHOES_NO_NATIVE=1), всё работает
по-старому на numpy/QPainter: available() == False.
"""
from __future__ import annotations

import ctypes
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
    sig("vizm_plasma", None, _p, _p)
    sig("vizm_compose", None, _p, _p, _p, _p, _i)
    sig("fx_capsules", None, _p, _i, _i, _i, _p, _p, _i, _i)
    sig("lw_plasma", None, _p, _i, _i, _i, _f, _f, _p)
    sig("clipfx_process", None, _p, _i, _p, _p, _i, _p, _p, _p, _p, _p, _p, _p)
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
