# preview.py
"""
Прослушивание 30 секунд трека из интернета ДО скачивания.

Ссылку на аудиопоток достаёт yt-dlp (без загрузки файла), играет отдельный
экземпляр VLC — основной плеер не трогается: если он играл, ставится на паузу
и после превью продолжает. В списке одновременно играет только одно превью.
"""
from __future__ import annotations

import threading
import time

from PyQt6.QtCore import QObject, QTimer

PREVIEW_SEC = 30


class PreviewPlayer(QObject):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self._inst = None
        self._player = None
        self._token = 0
        self._owner = None            # cb(state, left_sec): loading | playing | idle | error
        self._elapsed = 0.0           # сколько секунд превью реально прозвучало (без пауз)
        self._last = 0.0
        self._started = 0.0
        self._paused = False
        self._left = float(PREVIEW_SEC)
        self._paused_main = False
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._tick)

    def active_for(self, owner) -> bool:
        return self._owner == owner

    # ── управление ── #

    def toggle(self, url, duration, owner):
        """Кнопка в ряду: тот же ряд — пауза/продолжить, другой — запуск."""
        if self._owner is not None and self._owner == owner:
            self.toggle_pause()
        else:
            self.play(url, duration, owner)

    def is_active(self) -> bool:
        return self._owner is not None

    def toggle_pause(self):
        """Пауза/продолжить (кнопка в ряду, пробел, медиаклавиши, Play/Pause плеера)."""
        if self._owner is None or self._player is None or self._last == 0.0:
            return                                           # ещё грузится — паузить нечего
        self._paused = not self._paused
        try:
            self._player.set_pause(1 if self._paused else 0)
        except Exception as e:                               # noqa: BLE001
            print("[preview] pause:", e)
        self._last = time.monotonic()
        self._owner("paused" if self._paused else "playing", self._left)

    def play(self, url, duration, owner):
        self.stop()
        self._token += 1
        token = self._token
        self._owner = owner
        owner("loading", 0)
        import os
        if url and os.path.isfile(url):                      # свой файл — сразу, без поиска в интернете
            QTimer.singleShot(0, lambda: self._start(token, url, int(duration or 0)))
            return
        threading.Thread(target=self._resolve, args=(token, url, int(duration or 0)), daemon=True).start()

    def stop(self, resume=True):
        self._token += 1
        self._timer.stop()
        if self._player is not None:
            try:
                self._player.stop()
            except Exception:                        # noqa: BLE001
                pass
        owner, self._owner = self._owner, None
        self._paused = False
        if owner is not None:
            try:
                owner("idle", 0)
            except RuntimeError:                     # виджет уже удалён
                pass
        if self._paused_main and resume:
            try:
                self.win.engine.play()
            except Exception as e:                   # noqa: BLE001
                print("[preview] resume:", e)
        self._paused_main = False

    # ── внутреннее ── #

    def _resolve(self, token, url, duration):
        try:
            import yt_dlp
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True,
                                   "format": "bestaudio/best", "skip_download": True}) as ydl:
                info = ydl.extract_info(url, download=False)
            if info and info.get("entries"):
                info = next((e for e in info["entries"] if e), None)
            stream = (info or {}).get("url")
            if not stream:
                raise RuntimeError("нет ссылки на поток")
            duration = duration or int((info or {}).get("duration") or 0)
        except Exception as e:                       # noqa: BLE001
            print("[preview]", e)
            self.win._ui_bridge.call.emit(lambda: self._fail(token))
            return
        self.win._ui_bridge.call.emit(lambda: self._start(token, stream, duration))

    def _fail(self, token):
        if token != self._token:
            return
        owner = self._owner
        self.stop()
        if owner is not None:
            owner("error", 0)

    def _start(self, token, stream, duration):
        if token != self._token or self._owner is None:
            return
        try:
            import vlc
            if self._inst is None:
                self._inst = vlc.Instance("--quiet --network-caching=1500 --no-video")
                self._player = self._inst.media_player_new()
            off = 0 if duration < 75 else min(int(duration * 0.3), 90)     # не интро, а кусок из середины
            m = self._inst.media_new(stream)
            if off:
                m.add_option(f":start-time={off}")
            self._player.set_media(m)
            self._vol = int(self.win.settings.get("volume", 80))
            self._player.audio_set_volume(self._vol)
            try:
                if self.win.engine.is_playing():
                    self.win.engine.pause()
                    self._paused_main = True
            except Exception:                        # noqa: BLE001
                pass
            self._player.play()
        except Exception as e:                       # noqa: BLE001
            print("[preview] vlc:", e)
            self._fail(token)
            return
        self._elapsed, self._last, self._paused = 0.0, 0.0, False
        self._left = float(PREVIEW_SEC)
        self._started = time.monotonic()
        self._timer.start()

    def _tick(self):
        if self._owner is None or self._player is None:
            self._timer.stop()
            return
        st = str(self._player.get_state())
        if "Error" in st:
            self._fail(self._token)
            return
        if "Ended" in st:
            self.stop()
            return
        if self._paused:
            return
        if "Playing" not in st:
            if self._last == 0.0 and time.monotonic() - self._started > 25:   # поток так и не пошёл
                self._fail(self._token)
            return
        now = time.monotonic()
        if self._last:
            self._elapsed += now - self._last                # отсчёт 30 с — только пока реально звучит
        self._last = now
        left = self._left = PREVIEW_SEC - self._elapsed
        if left <= 0:
            self.stop()
            return
        if left < 1.5:                                       # плавное затухание
            self._player.audio_set_volume(max(0, int(self._vol * left / 1.5)))
        self._owner("playing", left)


def get_preview(win) -> PreviewPlayer:
    p = getattr(win, "_preview", None)
    if p is None:
        p = win._preview = PreviewPlayer(win)
    return p
