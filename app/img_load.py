# img_load.py
"""
Загрузка картинок (обложек) так, чтобы не рвался звук.

QPixmap(путь) в PyQt6 держит GIL всё время декодирования: обложка 2–3 МБ — это 70–130 мс,
и всё это время поток вывода звука (он на Python) не может отдать устройству следующий
блок — при прокрутке списка с обложками звук заикался. QImageReader.read() GIL отпускает,
а с setScaledSize JPEG уменьшается прямо при декодировании — ещё и в ~4 раза быстрее.
"""
from __future__ import annotations

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QImage, QImageReader, QPixmap


def read_image(path, max_side: int = 0) -> QImage:
    """QImage из файла; max_side > 0 — большая сторона не больше max_side (с сохранением пропорций).
    Пустой QImage, если файла нет или он не читается."""
    if not path:
        return QImage()
    r = QImageReader(str(path))
    if max_side and max_side > 0:
        sz = r.size()
        if sz.isValid() and max(sz.width(), sz.height()) > max_side:
            k = max_side / max(sz.width(), sz.height())
            r.setScaledSize(QSize(max(1, round(sz.width() * k)), max(1, round(sz.height() * k))))
    img = r.read()
    return img


def load_pixmap(path, max_side: int = 0) -> QPixmap:
    """Замена QPixmap(путь): то же, но без удержания GIL и (с max_side) в разы быстрее."""
    img = read_image(path, max_side)
    return QPixmap.fromImage(img) if not img.isNull() else QPixmap()
