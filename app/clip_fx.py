# clip_fx.py
"""
Психоделические эффекты для «Режима клипа» — поверх кадров видео, в реальном времени.

Каждый эффект — функция над кадром (numpy, B-G-R, uint8) и общим контекстом: время,
бас / громкость / бит (из того же анализа звука, что у тем), сила эффекта, состояние
(прошлый кадр для шлейфов и туннеля). Эффекты складываются в стопку: сначала геометрия
(калейдоскоп, волны, зеркало…), потом цвет (радуга, кислота, тепловизор…), потом
«память» (шлейф, туннель), и последними — вспышки (стробоскоп, мигающий негатив).

Кадр перед эффектами уменьшается до ~640 px по ширине: так даже стопка из десятка
эффектов успевает за 30 кадров в секунду, а на экране картинка растягивается обратно.

ВНИМАНИЕ: стробоскоп и мигающий негатив — быстрые вспышки. Перед их включением режим
клипа показывает предупреждение для людей со светочувствительной эпилепсией.
"""
from __future__ import annotations

import math

import numpy as np

try:                                                       # C++-ядро (echoes_fx.dll)
    import native_fx as _NF
    NATIVE = _NF.available()
except Exception:                                          # noqa: BLE001
    _NF, NATIVE = None, False

# ключ, название, «опасная вспышка» (нужно предупреждение)
EFFECTS = [
    ("kaleido", "Калейдоскоп", False), ("mirror", "Зеркало ×4", False), ("waves", "Волны", False),
    ("melt", "Плавление", False), ("droste", "Бесконечность", False), ("zoom", "Пульс-зум", False),
    ("shake", "Тряска на бит", False), ("pixel", "Пиксели", False), ("glitch", "Глитч", False),
    ("rgb", "RGB-сдвиг", False), ("echo", "Двоение", False), ("rainbow", "Радуга", False),
    ("acid", "Кислота", False), ("thermal", "Тепловизор", False), ("edges", "Неон-контуры", False),
    ("poster", "Постер", False), ("solar", "Соляризация", False), ("matrix", "Матрица", False),
    ("vhs", "VHS", False), ("trails", "Шлейф", False), ("tunnel", "Туннель", False),
    ("invert", "Негатив на бит", True), ("flicker", "Мигающий негатив", True), ("strobe", "Стробоскоп", True),
]
TITLES = {k: t for k, t, _d in EFFECTS}
FLASHY = {k for k, _t, d in EFFECTS if d}
ORDER = [k for k, _t, _d in EFFECTS]

PRESETS = [
    ("nightmare", "Кошмар эпилептика", ["kaleido", "shake", "glitch", "rgb", "rainbow", "invert", "flicker", "strobe"]),
    ("acid", "Кислотный трип", ["kaleido", "waves", "acid", "tunnel"]),
    ("dream", "Сон наяву", ["waves", "rainbow", "trails", "echo"]),
    ("vhs", "Кассета 1989", ["vhs", "rgb", "glitch"]),
    ("thermal", "Хищник", ["thermal", "edges", "shake"]),
    ("arcade", "Аркада", ["pixel", "poster", "zoom"]),
    ("void", "Бездна", ["droste", "tunnel", "solar", "mirror"]),
    ("matrix", "Цифровой мир", ["matrix", "melt", "glitch"]),
]


def _hue_matrix(deg: float) -> np.ndarray:
    """Поворот оттенка (матрица для B-G-R)."""
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    m = np.array([[0.213 + c * 0.787 - s * 0.213, 0.715 - c * 0.715 - s * 0.715, 0.072 - c * 0.072 + s * 0.928],
                  [0.213 - c * 0.213 + s * 0.143, 0.715 + c * 0.285 + s * 0.140, 0.072 - c * 0.072 - s * 0.283],
                  [0.213 - c * 0.213 - s * 0.787, 0.715 - c * 0.715 + s * 0.715, 0.072 + c * 0.928 + s * 0.072]],
                 np.float32)
    p = np.array([2, 1, 0])
    return m[p][:, p]                                     # RGB → BGR


def _lum(a: np.ndarray) -> np.ndarray:
    return (a[..., 0] * 0.114 + a[..., 1] * 0.587 + a[..., 2] * 0.299).astype(np.float32)


def _palette(stops) -> np.ndarray:
    """LUT 256×3 (B,G,R) из опорных цветов #RRGGBB."""
    xs = np.linspace(0, 1, len(stops))
    cols = np.array([[int(c[5:7], 16), int(c[3:5], 16), int(c[1:3], 16)] for c in stops], np.float32)
    t = np.linspace(0, 1, 256)
    return np.stack([np.interp(t, xs, cols[:, k]) for k in range(3)], 1).astype(np.uint8)


_THERMAL = _palette(["#000000", "#1b0a5c", "#7a0ca8", "#e0245c", "#ff7a00", "#ffe100", "#ffffff"])
_MATRIX = _palette(["#000000", "#002b0c", "#00a32a", "#5dff7a", "#e8ffe8"])


_PAL32 = None


class ClipFX:
    """Стопка эффектов. process(кадр B-G-R uint8, t, dt, pulse) → новый кадр того же размера."""

    def __init__(self):
        self.on: list[str] = []
        self.amount = 0.7
        self.react = 0.7
        self._prev = None
        self._maps = {}
        self._melt = None
        self._frame = 0
        self._rng = np.random.default_rng(7)

    # ── управление ── #
    def set(self, key, on: bool):
        if on and key not in self.on:
            self.on.append(key)
        elif not on and key in self.on:
            self.on.remove(key)
        if key in ("trails", "tunnel") and not on:
            self._prev = None

    def toggle(self, key):
        self.set(key, key not in self.on)

    def clear(self):
        self.on = []
        self._prev = None

    def preset(self, keys):
        self.on = [k for k in ORDER if k in keys]
        self._prev = None

    def active(self) -> bool:
        return bool(self.on)

    def flashy(self) -> bool:
        return any(k in FLASHY for k in self.on)

    # ── кэш карт координат ── #
    def _grid(self, h, w):
        key = ("grid", h, w)
        g = self._maps.get(key)
        if g is None:
            ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
            cx, cy = w / 2.0, h / 2.0
            r = np.hypot(xs - cx, ys - cy)
            th = np.arctan2(ys - cy, xs - cx)
            g = (xs, ys, r, th)
            if len(self._maps) > 12:
                self._maps.clear()
            self._maps[key] = g
        return g

    def _rotozoom(self, h, w, scale, deg):
        key = ("rz", h, w, round(scale, 3), round(deg, 2))
        m = self._maps.get(key)
        if m is None:
            xs, ys, _r, _t = self._grid(h, w)
            a = math.radians(deg)
            ca, sa = math.cos(a), math.sin(a)
            cx, cy = w / 2.0, h / 2.0
            dx, dy = (xs - cx) / scale, (ys - cy) / scale
            sx = np.clip(cx + dx * ca - dy * sa, 0, w - 1).astype(np.int32)
            sy = np.clip(cy + dx * sa + dy * ca, 0, h - 1).astype(np.int32)
            m = (sy, sx)
            if len(self._maps) > 12:
                self._maps.clear()
            self._maps[key] = m
        return m

    # ── кадр: C++ ── #
    _BITS = {k: i for i, k in enumerate(("kaleido", "mirror", "waves", "melt", "droste", "zoom", "shake", "pixel",
                                         "glitch", "rgb", "echo", "rainbow", "acid", "thermal", "edges", "poster",
                                         "solar", "matrix", "vhs", "trails", "tunnel", "invert", "flicker", "strobe"))}

    def process_image(self, img, t: float, dt: float, bass=0.0, level=0.0, beat=0.0):
        """То же, что process(), но в C++ прямо над QImage (RGB32, обрабатывается на месте) — в полном
        разрешении кадра и без GIL. Случайные решения кадра принимаются здесь, как в numpy-версии."""
        self._frame += 1
        w, h = img.width(), img.height()
        k = self.amount
        r = self.react
        bass, beat = bass * r, beat * r
        on = set(self.on)
        rng = self._rng
        P = _NF.ClipParams()
        P.w, P.h, P.t, P.k, P.bass, P.beat = w, h, t, k, bass, beat
        bits = 0
        for key in on:
            bits |= 1 << self._BITS[key]
        if "shake" in on and beat > 0.25:
            j = int(min(w, h) * 0.06 * k * beat)
            if j:
                P.shake_dy, P.shake_dx = int(rng.integers(-j, j + 1)), int(rng.integers(-j, j + 1))
        P.zoom_s = round(1.0 + (0.08 + bass * 0.35) * k, 2)
        P.kal_n = 6 if k < 0.5 else 8
        P.kal_rot = t * (0.25 + bass * 1.2) * (0.5 + k)
        P.kal_zoom = 0.85 + 0.15 * math.sin(t * 0.7)
        P.droste_flip = int(t * (0.5 + 2 * k + bass * 2)) % 2
        wave_dx = wave_dy = melt = vhs_jit = None
        if "waves" in on:
            amp = (6 + 26 * k) * (0.6 + bass)
            wave_dx = np.ascontiguousarray((amp * np.sin(np.arange(h, dtype=np.float32) * 0.045 + t * 3.1)).astype(np.int32))
            wave_dy = np.ascontiguousarray((amp * 0.6 * np.sin(np.arange(w, dtype=np.float32) * 0.035 + t * 2.3)).astype(np.int32))
        if "melt" in on:
            if self._melt is None or len(self._melt) != w:
                self._melt = np.zeros(w, np.float32)
            speed = (40 + 260 * k) * (0.4 + bass) * dt
            self._melt += speed * (0.5 + rng.random(w).astype(np.float32))
            self._melt = np.where(self._melt > h * 0.6 * k + 20, 0, self._melt)
            melt = np.ascontiguousarray(np.convolve(self._melt, np.ones(9, np.float32) / 9, "same").astype(np.int32))
        P.pixel_b = int(3 + 22 * k * (0.5 + bass))
        if "glitch" in on and (beat > 0.3 or rng.random() < 0.25 * k):
            n = min(16, int(3 + 10 * k))
            P.glitch_n = n
            for g in range(n):
                y = int(rng.integers(0, h))
                bh = int(rng.integers(2, max(3, h // 12)))
                sh = int(rng.integers(-w // 6, w // 6 + 1))
                c1 = c2 = -1
                if rng.random() < 0.4:
                    c1, c2 = (int(x) for x in rng.choice(3, 2, replace=False))
                P.glitch[g * 5:g * 5 + 5] = (y, bh, sh, c1, c2)
        elif "glitch" in on:
            bits &= ~(1 << self._BITS["glitch"])
        P.rgb_s = int((3 + 28 * bass + 10 * beat) * k) + 1
        P.echo_s = int((10 + 40 * bass) * k) + 2
        if "rainbow" in on:
            P.hue_m[:] = [float(x) for x in _hue_matrix(t * (60 + 240 * k) + bass * 120).ravel()]
        acid = None
        if "acid" in on:
            f = 2 + 5 * k
            x = np.arange(256, dtype=np.float32) / 255.0 * math.pi * f
            acid = np.ascontiguousarray(np.stack([127.5 + 127.5 * np.sin(x + t * 1.7 + c * 2.1 + bass * 3)
                                                  for c in range(3)]).astype(np.uint8))
        P.edge_gain = 2 + 4 * k
        P.edge_col[:] = [(math.sin(t * 2) * 127 + 128) / 255, (math.sin(t * 2 + 2) * 127 + 128) / 255,
                         (math.sin(t * 2 + 4) * 127 + 128) / 255]
        lv = max(2, int(6 - 4 * k))
        P.poster_step = 256 // lv
        P.solar_thr = int(200 - 120 * k)
        P.vhs_band_y = -1
        if "vhs" in on:
            vhs_jit = np.ascontiguousarray((rng.normal(0, 1.5 + 4 * k, h) * (1 + beat * 3)).astype(np.int32))
            P.vhs_shift = int(2 + 6 * k)
            if rng.random() < 0.3:
                P.vhs_band_y = int(rng.integers(0, h))
                P.vhs_band_h = int(rng.integers(1, 5))
                P.vhs_band_v = int(rng.integers(120, 255))
            P.vhs_noise_max = int(18 + 30 * k)
            P.seed = int(rng.integers(0, 2 ** 31))
        prev_valid = False
        prev = None
        if "tunnel" in on or "trails" in on:
            if self._prev is None or getattr(self._prev, "shape", None) != (h, w) or self._prev.dtype != np.uint32:
                self._prev = np.zeros((h, w), np.uint32)
            else:
                prev_valid = True
            prev = self._prev
            P.tunnel_s, P.tunnel_deg = 1.04 + 0.05 * k, 2.0 + 4 * bass
            P.tunnel_keep = 0.80 + 0.15 * k
            P.trails_keep = 0.55 + 0.4 * k
        else:
            self._prev = None
        inv = 0
        if "invert" in on and beat > 0.45:
            inv += 1
        if "flicker" in on and rng.random() < 0.25 + 0.35 * k:
            inv += 1
        P.invert = inv
        P.strobe_v = -1
        if "strobe" in on:
            hz = 6 + 8 * k
            if (t * hz) % 1.0 < 0.5:
                P.strobe_v = 255 if int(t * hz) % 2 == 0 else 0
                P.strobe_mix = 0.55 + 0.45 * k
        P.on = bits
        global _PAL32
        if _PAL32 is None:
            def to32(pal):
                c = pal.astype(np.uint32)                  # B, G, R
                return np.ascontiguousarray(np.uint32(0xFF000000) | (c[:, 2] << 16) | (c[:, 1] << 8) | c[:, 0])
            _PAL32 = (to32(_THERMAL), to32(_MATRIX))
        _NF.clipfx_process(img, P, prev, prev_valid, wave_dx, wave_dy, melt, acid, _PAL32[0], _PAL32[1], vhs_jit)
        return img

    # ── кадр ── #
    def process(self, a: np.ndarray, t: float, dt: float, bass=0.0, level=0.0, beat=0.0) -> np.ndarray:
        if not self.on:
            return a
        self._frame += 1
        h, w = a.shape[:2]
        k = self.amount
        r = self.react
        bass, beat = bass * r, beat * r
        a = np.ascontiguousarray(a)
        on = set(self.on)
        rng = self._rng

        # ── геометрия ──
        if "shake" in on and beat > 0.25:
            j = int(min(w, h) * 0.06 * k * beat)
            if j:
                a = np.roll(a, (int(rng.integers(-j, j + 1)), int(rng.integers(-j, j + 1))), (0, 1))
        if "zoom" in on:
            s = 1.0 + (0.08 + bass * 0.35) * k
            sy, sx = self._rotozoom(h, w, round(s, 2), 0.0)
            a = a[sy, sx]
        if "kaleido" in on:
            xs, ys, rr, th = self._grid(h, w)
            n = 6 if k < 0.5 else 8
            seg = 2 * math.pi / n
            rot = t * (0.25 + bass * 1.2) * (0.5 + k)
            tt = np.abs(np.mod(th + rot, seg) - seg / 2)
            zoom = 0.85 + 0.15 * math.sin(t * 0.7)
            sx = np.clip(w / 2 + rr * zoom * np.cos(tt), 0, w - 1).astype(np.int32)
            sy = np.clip(h / 2 + rr * zoom * np.sin(tt), 0, h - 1).astype(np.int32)
            a = a[sy, sx]
        if "mirror" in on:
            hw, hh = w // 2, h // 2
            a = a.copy()
            a[:, w - hw:] = a[:, :hw][:, ::-1]
            a[h - hh:, :] = a[:hh, :][::-1]
        if "droste" in on:                                 # картинка в картинке в картинке…
            out = a.copy()
            flip = int(t * (0.5 + 2 * k + bass * 2)) % 2      # каждая ступень то прямо, то вверх ногами
            for lvl in range(1, 5):
                f = 2 ** lvl
                piece = a[::f, ::f]
                if (lvl + flip) % 2:
                    piece = piece[::-1, ::-1]
                ph, pw = piece.shape[:2]
                if ph < 4 or pw < 4:
                    break
                y0, x0 = (h - ph) // 2, (w - pw) // 2
                out[y0:y0 + ph, x0:x0 + pw] = piece
            a = out
        if "waves" in on:
            amp = (6 + 26 * k) * (0.6 + bass)
            yy = np.arange(h, dtype=np.float32)
            xx = np.arange(w, dtype=np.float32)
            dx = (amp * np.sin(yy * 0.045 + t * 3.1)).astype(np.int32)
            dy = (amp * 0.6 * np.sin(xx * 0.035 + t * 2.3)).astype(np.int32)
            a = a[np.arange(h)[:, None], (np.arange(w)[None, :] + dx[:, None]) % w]
            a = a[(np.arange(h)[:, None] + dy[None, :]) % h, np.arange(w)[None, :]]
        if "melt" in on:
            if self._melt is None or len(self._melt) != w:
                self._melt = np.zeros(w, np.float32)
            speed = (40 + 260 * k) * (0.4 + bass) * dt
            self._melt += speed * (0.5 + rng.random(w).astype(np.float32))
            self._melt = np.where(self._melt > h * 0.6 * k + 20, 0, self._melt)
            sm = np.convolve(self._melt, np.ones(9, np.float32) / 9, "same").astype(np.int32)
            ys = np.clip(np.arange(h)[:, None] - sm[None, :], 0, h - 1)
            a = a[ys, np.arange(w)[None, :]]
        if "pixel" in on:
            b = int(3 + 22 * k * (0.5 + bass))
            small = a[::b, ::b]
            a = np.repeat(np.repeat(small, b, 0), b, 1)[:h, :w]
        if "glitch" in on and (beat > 0.3 or rng.random() < 0.25 * k):
            a = a.copy()
            for _ in range(int(3 + 10 * k)):
                y = int(rng.integers(0, h))
                bh = int(rng.integers(2, max(3, h // 12)))
                sh = int(rng.integers(-w // 6, w // 6 + 1))
                a[y:y + bh] = np.roll(a[y:y + bh], sh, 1)
                if rng.random() < 0.4:
                    c1, c2 = rng.choice(3, 2, replace=False)
                    a[y:y + bh, :, [c1, c2]] = a[y:y + bh, :, [c2, c1]]
        if "rgb" in on:
            s = int((3 + 28 * bass + 10 * beat) * k) + 1
            a = a.copy()
            a[..., 0] = np.roll(a[..., 0], s, 1)
            a[..., 2] = np.roll(a[..., 2], -s, 1)
            a[..., 1] = np.roll(a[..., 1], s // 2, 0)
        if "echo" in on:
            s = int((10 + 40 * bass) * k) + 2
            a = np.maximum(a, np.maximum(np.roll(a, s, 1) // 2 * 1, np.roll(a, -s, 0) // 2))

        # ── цвет ──
        if "rainbow" in on:
            m = _hue_matrix(t * (60 + 240 * k) + bass * 120)
            a = np.clip(a.reshape(-1, 3).astype(np.float32) @ m.T, 0, 255).astype(np.uint8).reshape(h, w, 3)
        if "acid" in on:
            f = 2 + 5 * k
            x = np.arange(256, dtype=np.float32) / 255.0 * math.pi * f
            lut = np.stack([127.5 + 127.5 * np.sin(x + t * 1.7 + c * 2.1 + bass * 3) for c in range(3)])
            lut = lut.astype(np.uint8)
            a = np.stack([lut[c][a[..., c]] for c in range(3)], -1)
        if "thermal" in on:
            a = _THERMAL[_lum(a).astype(np.uint8)]
        if "matrix" in on:
            lum = _lum(a)
            lum[1::3, :] *= 0.55                           # строки «экрана»
            a = _MATRIX[np.clip(lum * (1.1 + bass), 0, 255).astype(np.uint8)]
        if "edges" in on:
            lum = _lum(a)
            gx = np.abs(np.diff(lum, axis=1, append=lum[:, -1:]))
            gy = np.abs(np.diff(lum, axis=0, append=lum[-1:, :]))
            e = np.clip((gx + gy) * (2 + 4 * k), 0, 255)
            col = np.array([math.sin(t * 2) * 127 + 128, math.sin(t * 2 + 2) * 127 + 128,
                            math.sin(t * 2 + 4) * 127 + 128], np.float32) / 255
            a = np.clip(e[..., None] * col[None, None, :] * (1 + bass) + a * 0.15, 0, 255).astype(np.uint8)
        if "poster" in on:
            lv = max(2, int(6 - 4 * k))
            step = 256 // lv
            a = (a // step * step + step // 2).astype(np.uint8)
        if "solar" in on:
            thr = int(200 - 120 * k)
            a = np.where(a > thr, 255 - a, a).astype(np.uint8)
        if "vhs" in on:
            a = a.copy()
            jit = (rng.normal(0, 1.5 + 4 * k, h) * (1 + beat * 3)).astype(np.int32)
            a = a[np.arange(h)[:, None], (np.arange(w)[None, :] + jit[:, None]) % w]
            a[..., 0] = np.roll(a[..., 0], int(2 + 6 * k), 1)          # цвет «плывёт» относительно яркости
            a[::2] = (a[::2] * 0.78).astype(np.uint8)                  # строки кинескопа
            if rng.random() < 0.3:
                y = int(rng.integers(0, h))
                a[y:y + int(rng.integers(1, 5))] = rng.integers(120, 255)
            noise = rng.integers(0, int(18 + 30 * k), (h, w, 1), dtype=np.uint8)
            a = np.clip(a.astype(np.int16) + noise - 10, 0, 255).astype(np.uint8)

        # ── память кадров ──
        if "tunnel" in on:
            if self._prev is not None and self._prev.shape == a.shape:
                sy, sx = self._rotozoom(h, w, 1.04 + 0.05 * k, 2.0 + 4 * bass)
                warped = self._prev[sy, sx]
                a = np.maximum(a, (warped.astype(np.uint16) * (0.80 + 0.15 * k)).astype(np.uint8))
            self._prev = a
        elif "trails" in on:
            if self._prev is not None and self._prev.shape == a.shape:
                keep = 0.55 + 0.4 * k
                a = (a.astype(np.float32) * (1 - keep) + self._prev.astype(np.float32) * keep).astype(np.uint8)
            self._prev = a
        else:
            self._prev = None

        # ── вспышки (предупреждение перед включением) ──
        if "invert" in on and beat > 0.45:
            a = 255 - a
        if "flicker" in on and rng.random() < 0.25 + 0.35 * k:
            a = 255 - a
        if "strobe" in on:
            hz = 6 + 8 * k
            ph = (t * hz) % 1.0
            if ph < 0.5:
                v = 255 if int(t * hz) % 2 == 0 else 0
                mix = 0.55 + 0.45 * k
                a = (a.astype(np.float32) * (1 - mix) + v * mix).astype(np.uint8)
        return a
