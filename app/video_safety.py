# video_safety.py
"""Защита видеофона тем из конструктора (theme_layers.VideoSource) от падения при смене размера окна.

VideoSource рисует кадры прямо из буферов, в которые пишет VLC (без копий). При разворачивании окна
VLC перестраивает вывод несколько раз подряд (на 2560×1440 — три раза за ~100 мс), каждый раз с новыми
буферами, а старые наборы VideoSource хранит ограниченно. Показанный кадр ещё ссылается на освобождённый
набор → «access violation» в drawImage и вылет приложения.

Модуль оборачивает методы класса снаружи (не правит theme_layers.py): каждый старый набор буферов
держится здесь, пока новый набор не покажет несколько кадров — к этому времени все, кто рисует видео,
уже взяли кадры из нового. Вызывать install() до создания первого видеофона.
"""
from __future__ import annotations

KEEP_FRAMES = 4                                            # кадров из нового набора — и старые можно отпустить
MAX_SETS = 24                                              # предохранитель: перестройки без единого кадра


def install() -> bool:
    try:
        import theme_layers as TL
    except Exception as e:                                 # noqa: BLE001
        print("[video_safety]", e)
        return False
    cls = getattr(TL, "VideoSource", None)
    if cls is None or getattr(cls, "_vs_wrapped", False):
        return False
    orig_setup = getattr(cls, "_on_setup", None)
    orig_frame = getattr(cls, "_on_frame", None)
    if orig_setup is None or orig_frame is None:
        return False

    def _on_setup(self, *args):
        # поток VLC: запомнить текущий набор до того, как оригинал его заменит/обрежет
        old = getattr(self, "_bufs", None)
        keep = self.__dict__.setdefault("_vs_keep", [])
        if old:
            keep.append(old)
            if len(keep) > MAX_SETS:
                del keep[:-MAX_SETS]
        self._vs_frames = 0
        return orig_setup(self, *args)

    def _on_frame(self, *args):
        res = orig_frame(self, *args)
        n = getattr(self, "_vs_frames", 0) + 1
        self._vs_frames = n
        if n >= KEEP_FRAMES and self.__dict__.get("_vs_keep"):
            self._vs_keep = []                             # новый набор уже на экране у всех — старые не нужны
        return res

    _on_setup.__name__, _on_frame.__name__ = "_on_setup", "_on_frame"
    cls._on_setup = _on_setup
    cls._on_frame = _on_frame
    cls._vs_wrapped = True
    return True
