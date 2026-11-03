"""Картинки из сети — без «бомб»: размер проверяется ДО декодирования.

Маленький PNG/GIF может объявить 20000×20000 точек: при декодировании это сотни мегабайт памяти на одну
картинку (а в ленте их десятки). Здесь: заголовок читается без декодирования; слишком большое — уменьшается
прямо при чтении (setScaledSize) или отклоняется; у GIF ещё ограничено число кадров.
"""
from __future__ import annotations

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QSize
from PyQt6.QtGui import QImage, QImageReader

MAX_SIDE = 2048                     # больше на экране всё равно не нужно
MAX_PIXELS = 4096 * 4096            # заявлено больше — не читаем вовсе (это бомба, а не фото)
MAX_GIF_FRAMES = 600


def _reader(data: bytes):
    buf = QBuffer()
    buf.setData(QByteArray(bytes(data)))
    buf.open(QIODevice.OpenModeFlag.ReadOnly)
    r = QImageReader(buf)
    r.setAutoTransform(True)
    return r, buf


def safe_size(data: bytes) -> QSize | None:
    """Заявленный размер картинки или None (не картинка / опасно большая)."""
    r, _buf = _reader(data)
    s = r.size()
    if not s.isValid() or s.width() <= 0 or s.height() <= 0:
        return None
    if s.width() * s.height() > MAX_PIXELS * 4:
        return None
    return s


def load(data: bytes, max_side: int = MAX_SIDE) -> QImage:
    """QImage из байтов из сети: не больше max_side по большей стороне; бомба — пустая картинка."""
    if not data:
        return QImage()
    r, _buf = _reader(data)
    s = r.size()
    if s.isValid() and s.width() > 0 and s.height() > 0:
        if s.width() * s.height() > MAX_PIXELS * 4:
            return QImage()
        big = max(s.width(), s.height())
        if big > max_side:
            k = max_side / big
            r.setScaledSize(QSize(max(1, int(s.width() * k)), max(1, int(s.height() * k))))
    img = r.read()
    return img if not img.isNull() else QImage()


def gif_ok(data: bytes) -> bool:
    """GIF можно анимировать: разумный размер кадра и число кадров."""
    s = safe_size(data)
    if s is None or s.width() * s.height() > MAX_PIXELS:
        return False
    r, _buf = _reader(data)
    n = r.imageCount()
    return 0 <= n <= MAX_GIF_FRAMES
