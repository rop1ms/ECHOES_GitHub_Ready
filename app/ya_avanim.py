# ya_avanim.py
"""
Анимированная аватарка: GIF или короткое видео (MP4 / WebM / MOV) → лёгкий квадратный GIF (до 4 с, ≤ 350 КБ).
Друзья получают его вместе с карточкой профиля и видят движение в шапке профиля; везде, где аватарка
маленькая (лента, чаты), и в публичной сети — обычная картинка (первый кадр).
"""
from __future__ import annotations

import base64
import subprocess
import tempfile
import time
from pathlib import Path

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QImage, QImageReader, QMovie, QPainter, QPainterPath, QPixmap

LIMIT = 350 * 1024
VIDEO = (".mp4", ".webm", ".mov", ".mkv", ".m4v")
FILTER = "Картинки и видео (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.mp4 *.webm *.mov *.mkv *.m4v)"


def _ff():
    try:
        from loudness import _ffmpeg
        return _ffmpeg()
    except Exception:                                          # noqa: BLE001
        return None


def is_animated(path: str) -> bool:
    p = str(path or "").lower()
    if p.endswith(VIDEO):
        return True
    if p.endswith((".gif", ".webp")):
        r = QImageReader(path)
        return r.supportsAnimation() and r.imageCount() != 1
    return False


def first_frame(path: str) -> QImage:
    """Первый кадр (для обычной аватарки и превью)."""
    if not str(path).lower().endswith(VIDEO):
        return QImage(path)
    ff = _ff()
    if not ff:
        return QImage()
    out = Path(tempfile.gettempdir()) / f"echoes_av_{int(time.time() * 1000)}.png"
    try:
        subprocess.run([ff, "-v", "error", "-y", "-ss", "0.3", "-i", str(path), "-frames:v", "1", str(out)],
                       capture_output=True, timeout=30, creationflags=0x08000000)
        img = QImage(str(out))
    except Exception:                                          # noqa: BLE001
        img = QImage()
    out.unlink(missing_ok=True)
    return img


def make_gif(src: str) -> bytes | None:
    """GIF/видео → квадратный GIF с палитрой; если не влезает — меньше и реже кадры."""
    ff = _ff()
    if not ff:
        try:                                                   # без ffmpeg — только готовый небольшой GIF
            b = Path(src).read_bytes()
            return b if b[:4] == b"GIF8" and len(b) <= LIMIT else None
        except OSError:
            return None
    tmp = Path(tempfile.gettempdir()) / f"echoes_av_{int(time.time() * 1000)}.gif"
    try:
        palette = True                                         # у урезанных сборок ffmpeg нет palettegen
        for side, fps, colors in ((180, 15, 128), (150, 12, 96), (120, 10, 64), (96, 8, 48)):
            base = f"crop='min(iw,ih)':'min(iw,ih)',scale={side}:{side}:flags=lanczos,fps={fps}"
            for pal in ((True, False) if palette else (False,)):
                vf = base + (f",split[a][b];[a]palettegen=max_colors={colors}:stats_mode=diff[p];"
                             f"[b][p]paletteuse=dither=bayer" if pal else "")
                r = subprocess.run([ff, "-v", "error", "-y", "-t", "4", "-i", str(src), "-vf", vf, "-loop", "0",
                                    str(tmp)], capture_output=True, timeout=90, creationflags=0x08000000)
                b = tmp.read_bytes() if r.returncode == 0 and tmp.exists() else b""
                if not b and pal:
                    palette = False
                    continue
                if 0 < len(b) <= LIMIT:
                    return b
                break
        return None
    except Exception:                                          # noqa: BLE001
        return None
    finally:
        tmp.unlink(missing_ok=True)


def store(data: bytes, folder: Path) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"avatar_anim_{int(time.time())}.gif"
    out.write_bytes(data)
    for old in folder.glob("avatar_anim_*.gif"):
        if old != out:
            old.unlink(missing_ok=True)
    return str(out).replace("\\", "/")


def b64_of(path: str) -> str:
    try:
        b = Path(path).read_bytes()
    except (OSError, TypeError):
        return ""
    return base64.b64encode(b).decode() if b[:4] == b"GIF8" and len(b) <= LIMIT else ""


def ok_b64(s) -> bool:
    if not isinstance(s, str) or len(s) > LIMIT * 4 // 3 + 8:
        return False
    try:
        return base64.b64decode(s[:16])[:4] == b"GIF8"
    except ValueError:
        return False


def circle(img: QImage, size: int) -> QPixmap:
    """Кадр → круглая аватарка size×size (2× для экрана)."""
    dpr = 2
    pm = QPixmap(size * dpr, size * dpr)
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    if img.isNull():
        return pm
    s = min(img.width(), img.height())
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    path = QPainterPath()
    path.addEllipse(QRectF(0, 0, size, size))
    p.setClipPath(path)
    p.drawImage(QRectF(0, 0, size, size), img, QRectF((img.width() - s) / 2, (img.height() - s) / 2, s, s))
    p.end()
    return pm


def movie(path: str, parent=None) -> QMovie | None:
    if not path or not Path(path).exists():
        return None
    m = QMovie(path, parent=parent)
    if not m.isValid():
        return None
    from performance import configure_movie
    configure_movie(m,parent)
    return m
