# media_session.py
"""
Системная медиасессия Windows (SMTC — System Media Transport Controls).

ECHOES отдаёт Windows то, что играет: название, исполнитель, альбом, обложка, позиция и длительность.
Благодаря этому трек виден в плашке громкости / медиа-виджете Windows 10–11, на экране блокировки,
в Game Bar, в виджетах и программах, читающих медиасессии; работают медиаклавиши клавиатуры и
кнопки ⏮ ⏯ ⏭ в этих плашках, перемотка ползунком в системном виджете.

Через пакеты winrt-* (ставятся установщиком). Нет пакетов или не Windows — тихо выключено.
Состояние плеера опрашивается раз в секунду: изменения (трек, пауза, перемотка) уходят в Windows,
команды из Windows приходят сигналом в поток интерфейса.
"""
from __future__ import annotations

import asyncio
import sys
import threading
from datetime import timedelta
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal


def available() -> bool:
    if not sys.platform.startswith("win"):
        return False
    try:
        import winrt.windows.media.playback  # noqa: F401
        return True
    except Exception:                                      # noqa: BLE001
        return False


class MediaSession(QObject):
    command = pyqtSignal(str, float)                       # play / pause / toggle / next / prev / stop / seek

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.ok = False
        self._last = None                                  # (путь, название, исполнитель, альбом, обложка)
        self._last_status = None
        self._last_pos = -10.0
        self._last_dur = -1.0
        self._busy = False
        self._last_status_playing = False
        self.command.connect(self._on_command)
        try:
            from winrt.windows.media import MediaPlaybackType  # noqa: F401
            from winrt.windows.media.playback import MediaPlayer
            self.mp = MediaPlayer()
            self.mp.command_manager.is_enabled = False     # кнопками управляем сами, без звука MediaPlayer
            s = self.smtc = self.mp.system_media_transport_controls
            s.is_enabled = True
            s.is_play_enabled = s.is_pause_enabled = True
            s.is_next_enabled = s.is_previous_enabled = True
            s.is_stop_enabled = True
            self._tok_btn = s.add_button_pressed(self._on_button)
            self._tok_seek = s.add_playback_position_change_requested(self._on_seek)
            self.ok = True
        except Exception as e:                             # noqa: BLE001
            print("[media session] недоступна:", e)
            return
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._poll)
        self.timer.start()
        QTimer.singleShot(300, self._poll)

    # ── из Windows ──
    def _on_button(self, _sender, args):
        try:
            from winrt.windows.media import SystemMediaTransportControlsButton as B
            b = args.button
            name = {B.PLAY: "play", B.PAUSE: "pause", B.NEXT: "next", B.PREVIOUS: "prev", B.STOP: "stop"}.get(b)
            if name:
                self.command.emit(name, 0.0)
        except Exception as e:                             # noqa: BLE001
            print("[media session] кнопка:", e)

    def _on_seek(self, _sender, args):
        try:
            pos = args.requested_playback_position
            sec = pos.total_seconds() if hasattr(pos, "total_seconds") else float(pos) / 1e7
            self.command.emit("seek", float(sec))
        except Exception as e:                             # noqa: BLE001
            print("[media session] перемотка:", e)

    def _on_command(self, name, value):
        w = self.win
        try:
            playing = bool(w.engine.is_playing())
            if name == "play" and not playing:
                w.toggle_play()
            elif name == "pause" and playing:
                w.toggle_play()
            elif name == "next":
                w.next_track()
            elif name == "prev":
                w.prev_track()
            elif name == "stop" and playing:
                w.toggle_play()
            elif name == "seek":
                w.engine.seek(max(0.0, value))
        except Exception as e:                             # noqa: BLE001
            print("[media session] команда:", e)
        QTimer.singleShot(150, self._poll)

    # ── в Windows ──
    def _poll(self):
        if not self.ok:
            return
        w = self.win
        try:
            t = w._current_track()
        except Exception:                                  # noqa: BLE001
            t = None
        try:
            playing = bool(w.engine.is_playing())
            pos = float(w.engine.get_position() or 0.0)
            dur = float(getattr(w.engine, "duration", 0) or (t or {}).get("duration") or 0)
        except Exception:                                  # noqa: BLE001
            playing, pos, dur = False, 0.0, 0.0
        from winrt.windows.media import MediaPlaybackStatus as S
        status = S.PLAYING if playing else (S.PAUSED if t else S.CLOSED)
        if status != self._last_status:
            self._last_status = status
            try:
                self.smtc.playback_status = status
            except Exception as e:                         # noqa: BLE001
                print("[media session] статус:", e)
        key = None
        if t:
            try:
                from lyrics import sanitize_title
            except Exception:                              # noqa: BLE001
                def sanitize_title(s):
                    return s
            key = (str(t.get("path", "")), sanitize_title(t.get("title") or Path(str(t.get("path", ""))).stem),
                   str(t.get("artist") or ""), str(t.get("album") or ""), str(t.get("cover") or ""))
        if key != self._last and not self._busy:
            self._last = key
            self._send_meta(key)
            self._last_pos = -10.0
        # позицию Windows досчитывает сама — шлём при смене трека/длительности, перемотке (позиция ушла от
        # ожидаемой) и раз в 10 с на всякий случай
        import time as _t
        now = _t.monotonic()
        sent_t = getattr(self, "_sent_t", 0.0)
        expect = self._last_pos + ((now - sent_t) if (playing and self._last_status_playing) else 0.0)
        self._last_status_playing = playing
        if dur > 0 and (abs(pos - expect) > 2.0 or abs(dur - self._last_dur) > 0.5 or now - sent_t > 10):
            self._last_pos, self._last_dur, self._sent_t = pos, dur, now
            try:
                from winrt.windows.media import SystemMediaTransportControlsTimelineProperties as TL
                tl = TL()
                tl.start_time = timedelta(0)
                tl.min_seek_time = timedelta(0)
                tl.end_time = timedelta(seconds=dur)
                tl.max_seek_time = timedelta(seconds=dur)
                tl.position = timedelta(seconds=max(0.0, min(pos, dur)))
                self.smtc.update_timeline_properties(tl)
            except Exception as e:                         # noqa: BLE001
                print("[media session] позиция:", e)

    def _send_meta(self, key):
        """Название, исполнитель, альбом и обложка. Обложка читается асинхронно (WinRT) — в своём потоке."""
        s = self.smtc
        try:
            from winrt.windows.media import MediaPlaybackType
            du = s.display_updater
            du.clear_all()
            if key is None:
                du.update()
                return
            du.type = MediaPlaybackType.MUSIC
            mp = du.music_properties
            mp.title = key[1] or "ECHOES"
            mp.artist = key[2]
            mp.album_title = key[3]
            mp.album_artist = key[2]
            du.update()
        except Exception as e:                             # noqa: BLE001
            print("[media session] подпись:", e)
            return
        cover = key[4]
        if not cover or not Path(cover).is_file():
            return
        self._busy = True

        def work():
            async def go():
                from winrt.windows.storage import StorageFile
                from winrt.windows.storage.streams import RandomAccessStreamReference
                f = await StorageFile.get_file_from_path_async(str(Path(cover).resolve()))
                return RandomAccessStreamReference.create_from_file(f)
            ref = None
            try:
                ref = asyncio.run(go())
            except Exception as e:                         # noqa: BLE001
                print("[media session] обложка:", e)
            try:
                if ref is not None and self._last == key:
                    du = s.display_updater
                    du.thumbnail = ref
                    du.update()
            except Exception as e:                         # noqa: BLE001
                print("[media session] обложка:", e)
            finally:
                self._busy = False
        threading.Thread(target=work, daemon=True, name="smtc-cover").start()

    def close(self):
        if not self.ok:
            return
        self.ok = False
        try:
            self.timer.stop()
            self.smtc.playback_status = __import__("winrt.windows.media", fromlist=["MediaPlaybackStatus"]) \
                .MediaPlaybackStatus.CLOSED
            self.smtc.display_updater.clear_all()
            self.smtc.display_updater.update()
            self.smtc.is_enabled = False
        except Exception:                                  # noqa: BLE001
            pass


def register_app_name(aumid: str = "echoes.player"):
    """Имя и значок для медиа-плашки Windows: без записи в реестре она подписала бы плеер «Python».
    HKCU\\Software\\Classes\\AppUserModelId\\<id> — так Windows узнаёт обычные (не Store) приложения."""
    try:
        import winreg
        icon = Path(__file__).resolve().parent / "icon.png"
        ico = Path(__file__).resolve().parent / "icon.ico"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\AppUserModelId\{aumid}") as k:
            winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, "ECHOES")
            if icon.exists() or ico.exists():
                winreg.SetValueEx(k, "IconUri", 0, winreg.REG_SZ, str(icon if icon.exists() else ico))
            winreg.SetValueEx(k, "IconBackgroundColor", 0, winreg.REG_SZ, "FF0B0B10")
    except Exception as e:                                 # noqa: BLE001
        print("[media session] имя приложения:", e)


def start(win) -> MediaSession | None:
    if not available():
        return None
    register_app_name()
    ms = MediaSession(win)
    if not ms.ok:
        return None
    try:
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(ms.close)
    except Exception:                                      # noqa: BLE001
        pass
    return ms
