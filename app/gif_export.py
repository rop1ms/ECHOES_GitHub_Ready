# gif_export.py
"""Экспорт анимации крутящейся пластинки (VinylWidget) в GIF."""

import numpy as np
from PyQt6.QtGui import QImage


def export_vinyl_gif(vinyl_widget, out_path, frames: int = 48, size: int = 320, fps: int = 24) -> bool:
    """
    Рендерит один полный оборот пластинки в зацикленный GIF, не трогая
    реальное воспроизведение — временно подменяет angle/playing виджета,
    грабит кадры через QWidget.grab() и восстанавливает состояние.

    Требует Pillow (см. requirements.txt).
    """
    from PIL import Image

    saved_angle = vinyl_widget.angle
    saved_playing = vinyl_widget.playing
    images = []
    try:
        vinyl_widget.playing = True  # чтобы тонарм/свечение были в "играющем" виде
        for i in range(frames):
            vinyl_widget.angle = (360.0 / frames) * i
            vinyl_widget.update()
            vinyl_widget.repaint()
            pixmap = vinyl_widget.grab()
            img = pixmap.scaled(size, size).toImage().convertToFormat(QImage.Format.Format_RGBA8888)
            w, h = img.width(), img.height()
            ptr = img.bits()
            ptr.setsize(h * img.bytesPerLine())
            arr = np.frombuffer(ptr, dtype=np.uint8).reshape(h, img.bytesPerLine() // 4, 4)[:, :w, :].copy()

            # Alpha-composite на тёмный фон перед переводом в палитру:
            # полупрозрачные пиксели антиалиасинга иначе маппируются в
            # случайные цвета палитры (розовый/пурпурный «хромакей»-артефакт).
            rgba = Image.fromarray(arr, "RGBA")
            bg_color = (14, 16, 24)  # совпадает с типичным фоном плеера
            bg = Image.new("RGBA", rgba.size, bg_color + (255,))
            flat = Image.alpha_composite(bg, rgba).convert("RGB")
            images.append(flat.convert("P", palette=Image.ADAPTIVE, dither=Image.Dither.NONE))
    finally:
        vinyl_widget.angle = saved_angle
        vinyl_widget.playing = saved_playing
        vinyl_widget.update()

    if not images:
        return False
    images[0].save(
        str(out_path), save_all=True, append_images=images[1:],
        duration=int(1000 / fps), loop=0,
    )
    return True
