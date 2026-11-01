# bg_remove.py
"""
Убрать фон у картинки или GIF — своя модель (без нейросетей и интернета).

Как она думает:
  1. Цвета. Картинка переводится в пространство «яркость + два цветовых контраста»
     (ближе к тому, как различает цвета глаз, чем RGB).
  2. Фон по краям. Полоса пикселей по краям кадра почти всегда фон: она раскладывается
     на несколько цветовых кластеров (k-means) — модель фона со своим разбросом у каждого.
  3. Объект. Пиксели, далёкие от всех цветов фона, — первые образцы объекта; у него тоже
     свои кластеры. Дальше несколько раундов: каждому пикселю — вероятность «объект»
     (насколько он ближе к кластерам объекта, чем фона), сглаживание соседями, и
     кластеры пересчитываются по уверенным пикселям.
  4. Связность. Настоящий фон связан с краями кадра. Островки «похожего на фон» внутри
     объекта (белая футболка на белом фоне) остаются объектом — если только не включено
     «убирать фон и внутри» (просвет между рукой и телом).
  5. Чистка. Мелкий мусор убирается, дырки в объекте заделываются.
  6. Края. Маска уточняется по самой картинке направленным фильтром (guided filter):
     край ложится на настоящую границу, волосы и мех — полупрозрачные. Из полупрозрачных
     краёв вычитается цвет фона, чтобы вокруг объекта не было светлого/цветного ореола.

GIF и анимированный WebP: модель учится на первых кадрах и применяется к каждому кадру
(одинаково — без мерцания краёв), результат — анимированный WebP с прозрачностью.
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage as ndi

MAX_WORK = 420                      # сторона, на которой модель думает (дальше маска растягивается)
MAX_OUT = 2048                      # больше — уменьшаем (слои тем не бывают крупнее)


def _space(rgb: np.ndarray) -> np.ndarray:
    """RGB (0..255) → яркость + красно-зелёный + жёлто-синий контрасты."""
    x = rgb.astype(np.float32) / 255.0
    r, g, b = x[..., 0], x[..., 1], x[..., 2]
    L = 0.299 * r + 0.587 * g + 0.114 * b
    return np.stack([L * 1.0, (r - g) * 1.6, ((r + g) * 0.5 - b) * 1.6], -1)


def _kmeans(x: np.ndarray, k: int, rng, iters: int = 10):
    """Центры кластеров и их разброс."""
    if len(x) > 6000:
        x = x[rng.choice(len(x), 6000, replace=False)]
    k = max(1, min(k, len(x)))
    c = x[rng.choice(len(x), k, replace=False)].copy()
    for _ in range(iters):
        d = ((x[:, None, :] - c[None, :, :]) ** 2).sum(-1)
        lab = d.argmin(1)
        for j in range(k):
            m = lab == j
            if m.any():
                c[j] = x[m].mean(0)
    d = ((x[:, None, :] - c[None, :, :]) ** 2).sum(-1)
    lab = d.argmin(1)
    sig = np.array([max(0.03, float(np.sqrt(d[lab == j, j].mean())) if (lab == j).any() else 0.08)
                    for j in range(k)], np.float32)
    return c.astype(np.float32), sig


def _dist(f: np.ndarray, c: np.ndarray, sig: np.ndarray) -> np.ndarray:
    """Расстояние каждого пикселя до ближайшего кластера (в его разбросах)."""
    flat = f.reshape(-1, 3)
    best = np.full(len(flat), np.inf, np.float32)
    for j in range(len(c)):
        d = np.sqrt(((flat - c[j]) ** 2).sum(1)) / (sig[j] * 0.7 + 0.04)
        np.minimum(best, d, out=best)
    return best.reshape(f.shape[:2])


def _box(x, r):
    return ndi.uniform_filter(x, size=2 * r + 1, mode="reflect")


def _guided(I: np.ndarray, p: np.ndarray, r: int, eps: float) -> np.ndarray:
    """Направленный фильтр (He et al.): маска p подтягивается к краям картинки I."""
    mI, mp = _box(I, r), _box(p, r)
    cov = _box(I * p, r) - mI * mp
    var = _box(I * I, r) - mI * mI
    a = cov / (var + eps)
    b = mp - a * mI
    return _box(a, r) * I + _box(b, r)


class Model:
    """Обученная модель одного изображения/анимации: кластеры фона и объекта + настройки."""

    def __init__(self, strength=0.5, softness=0.5, holes=False):
        self.strength = float(strength)        # 0 — бережно (меньше срезать), 1 — смело
        self.softness = float(softness)        # мягкость краёв
        self.holes = bool(holes)               # убирать фон и внутри объекта
        self.bg = self.fg = None
        self.rng = np.random.default_rng(3)

    # ── обучение ── #
    def fit(self, rgb: np.ndarray, rounds: int = 4):
        f = _space(self._work(rgb))
        h, w = f.shape[:2]
        band = max(2, int(min(h, w) * 0.04))
        edge = np.concatenate([f[:band].reshape(-1, 3), f[-band:].reshape(-1, 3),
                               f[:, :band].reshape(-1, 3), f[:, -band:].reshape(-1, 3)])
        self.bg = _kmeans(edge, 5, self.rng)
        dbg = _dist(f, *self.bg)
        seed = f[dbg > np.percentile(dbg, 70)].reshape(-1, 3)
        if len(seed) < 20:
            seed = f[h // 4:3 * h // 4, w // 4:3 * w // 4].reshape(-1, 3)
        self.fg = _kmeans(seed, 6, self.rng)
        for _ in range(rounds):
            p = self._prob(f)
            bg_s, fg_s = f[p < 0.15], f[p > 0.85]
            if len(bg_s) > 50:
                c, s = _kmeans(np.concatenate([edge, bg_s.reshape(-1, 3)]), 5, self.rng, 6)
                self.bg = (c, s)
            if len(fg_s) > 50:
                self.fg = _kmeans(fg_s.reshape(-1, 3), 6, self.rng, 6)
        return self

    @staticmethod
    def _work(rgb):
        h, w = rgb.shape[:2]
        k = MAX_WORK / max(h, w)
        if k >= 1:
            return rgb
        ys = (np.arange(int(h * k)) / k).astype(int)
        xs = (np.arange(int(w * k)) / k).astype(int)
        return rgb[ys][:, xs]

    def _prob(self, f):
        dbg = _dist(f, *self.bg)
        dfg = _dist(f, *self.fg)
        bias = (self.strength - 0.5) * 2.5                 # смелее — больше пикселей уходит в фон
        p = 1.0 / (1.0 + np.exp(-((dbg - dfg) * 1.6 - bias)))
        return ndi.uniform_filter(p.astype(np.float32), 3)

    # ── применение ── #
    def mask(self, rgb: np.ndarray, alpha_in: np.ndarray | None = None) -> np.ndarray:
        """Альфа-маска (0..1) того же размера, что rgb."""
        H, W = rgb.shape[:2]
        f = _space(self._work(rgb))
        h, w = f.shape[:2]
        p = self._prob(f)
        hard = p > 0.5
        # связность: фон — то, что соединено с краем кадра
        bgm = ~hard
        lab, n = ndi.label(bgm)
        if n:
            border = np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))
            touch = np.isin(lab, border[border > 0])
            if not self.holes:
                hard = ~touch                                # островки «как фон» внутри — это объект
            else:
                dbg = _dist(f, *self.bg)
                inner = bgm & ~touch & (dbg < 1.3)           # внутри — только явный фон
                hard = ~(touch | inner)
        # мусор: оставить крупные куски объекта
        lab, n = ndi.label(hard)
        if n > 1:
            sizes = ndi.sum(hard, lab, range(1, n + 1))
            keep = np.zeros(n + 1, bool)
            keep[1:] = sizes >= max(12, sizes.max() * 0.015)
            hard = keep[lab]
        if not self.holes:
            hard = ndi.binary_fill_holes(hard)
        hard = ndi.binary_opening(hard, iterations=1) | (hard & ndi.binary_erosion(hard, iterations=2))
        # к полному размеру и края по картинке
        soft = hard.astype(np.float32)
        if (h, w) != (H, W):
            soft = ndi.zoom(soft, (H / h, W / w), order=1)[:H, :W]
            if soft.shape != (H, W):
                soft = np.pad(soft, ((0, H - soft.shape[0]), (0, W - soft.shape[1])), mode="edge")
        gray = (rgb[..., :3].astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)) / 255.0
        r = int(1 + self.softness * max(2, min(H, W) / 90))
        a = _guided(gray, soft, r, 1e-3 + self.softness * 4e-3)
        k = 2.2 - self.softness * 1.2                        # контраст края: жёстче / мягче
        a = np.clip((a - 0.5) * k + 0.5, 0.0, 1.0)
        if alpha_in is not None:
            a = np.minimum(a, alpha_in.astype(np.float32) / 255.0)
        return a

    @staticmethod
    def decontaminate(rgb: np.ndarray, a: np.ndarray) -> np.ndarray:
        """Вычесть цвет фона из полупрозрачных краёв (без ореола)."""
        x = rgb[..., :3].astype(np.float32)
        w = (a < 0.05).astype(np.float32)
        r = max(3, int(min(a.shape) / 40))
        num = np.stack([ndi.uniform_filter(x[..., c] * w, 2 * r + 1) for c in range(3)], -1)
        den = ndi.uniform_filter(w, 2 * r + 1)[..., None]
        bg = num / np.maximum(den, 1e-4)
        edge = ((a > 0.02) & (a < 0.98))[..., None]
        aa = np.maximum(a, 0.05)[..., None]
        fg = np.clip((x - (1 - aa) * bg) / aa, 0, 255)
        return np.where(edge & (den > 1e-3), fg, x).astype(np.uint8)

    def cutout(self, rgba: np.ndarray) -> np.ndarray:
        """RGBA → RGBA без фона."""
        rgb = rgba[..., :3]
        alpha_in = rgba[..., 3] if rgba.shape[-1] == 4 and rgba[..., 3].min() < 250 else None
        a = self.mask(rgb, alpha_in)
        out = np.empty(rgb.shape[:2] + (4,), np.uint8)
        out[..., :3] = self.decontaminate(rgb, a)
        out[..., 3] = (a * 255 + 0.5).astype(np.uint8)
        return out


# ------------------------------------------------------------------ #
#  Файлы                                                              #
# ------------------------------------------------------------------ #

def load_frames(path: str):
    """→ (кадры RGBA, длительности мс, анимация ли)."""
    from PIL import Image, ImageSequence
    im = Image.open(path)
    frames, durs = [], []
    animated = bool(getattr(im, "is_animated", False)) and getattr(im, "n_frames", 1) > 1
    for fr in (ImageSequence.Iterator(im) if animated else [im]):
        f = fr.convert("RGBA")
        if max(f.size) > MAX_OUT:
            k = MAX_OUT / max(f.size)
            f = f.resize((max(1, int(f.width * k)), max(1, int(f.height * k))), Image.LANCZOS)
        frames.append(np.array(f))
        durs.append(int(fr.info.get("duration", im.info.get("duration", 80)) or 80))
        if len(frames) >= 600:
            break
    return frames, durs, animated


def fit_model(frames, strength=0.5, softness=0.5, holes=False) -> Model:
    m = Model(strength, softness, holes)
    if len(frames) == 1:
        return m.fit(frames[0][..., :3])
    pick = [frames[i] for i in sorted({0, len(frames) // 3, 2 * len(frames) // 3})]
    mosaic = np.concatenate([f[..., :3] for f in pick], axis=1)   # учимся сразу на нескольких кадрах
    return m.fit(mosaic, rounds=3)


def save(frames_out, durs, animated, out_path: str) -> str:
    from PIL import Image
    ims = [Image.fromarray(f, "RGBA") for f in frames_out]
    if animated and len(ims) > 1:
        out_path = str(out_path).rsplit(".", 1)[0] + ".webp"
        ims[0].save(out_path, "WEBP", save_all=True, append_images=ims[1:], duration=durs, loop=0,
                    quality=88, method=4, lossless=False)
    else:
        out_path = str(out_path).rsplit(".", 1)[0] + ".png"
        ims[0].save(out_path, "PNG", optimize=True)
    return out_path
