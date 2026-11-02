# game_mode.py
"""
Режим геймера: пока идёт игра, ECHOES только играет музыку и не мешает игре.

Включается кнопкой с геймпадом в верхней панели или сам, когда запущена игра (Настройки → Режим
геймера → «Включать автоматически в играх», по умолчанию включено). Что делает:
  * анимации интерфейса (Моя волна, визуализатор, текст, дуэль…) стоят, пока окно ECHOES не активно —
    ни процессора, ни видеокарты на кадры, которых за игрой не видно;
  * фоновая работа на Python (анализ треков, жанры, тексты, прогрев страниц) ждёт — bgproc.set_fg_busy;
  * приоритет процесса — «ниже среднего»: игра первая; поток звука при этом «критичный по времени»
    (audio_out), поэтому музыка не заикается;
  * кэши картинок сброшены, модели ИИ выгружены, память отдана системе (mem_trim).
Окно снова в фокусе — анимации идут (не чаще 60 кадров/с), выходите из игры — всё как было.

Распознавание игры (раз в 2.5 с, echoes_ui.dll: eui_foreground_info) — по окну на переднем плане:
  * Windows сообщает о полноэкранном Direct3D-приложении (SHQueryUserNotificationState = 3);
  * .exe лежит в папке игр (Steam, Epic, Riot, Xbox, GOG, EA, Ubisoft, Rockstar, Roblox…) или это
    известная игра (Fortnite, Valorant, CS2, Dota 2, osu!…);
  * окно закрывает весь монитор, и это не браузер, не видеоплеер, не проводник и не сам ECHOES.
Лаунчеры (Steam, Epic, Battle.net…) — не игры. Чтобы режим не мигал, игра должна продержаться на переднем
плане два опроса подряд, а выключается режим, только когда процесс игры завершился (свернули игру —
режим остаётся). Выключили режим кнопкой посреди игры — до конца этой игры он сам не включится.
"""
from __future__ import annotations

import ctypes
import os

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication

POLL_MS = 2500

GAME_DIRS = ("\\steamapps\\common\\", "\\epic games\\", "\\riot games\\", "\\xboxgames\\", "\\gog games\\",
             "\\gog galaxy\\games\\", "\\ea games\\", "\\ubisoft game launcher\\games\\", "\\rockstar games\\",
             "\\roblox\\versions\\", "\\wargaming.net\\", "\\hoyoplay\\games\\", "\\battlestate games\\",
             "\\minecraft launcher\\", "\\games\\")
GAME_EXES = {
    "fortniteclient-win64-shipping.exe", "valorant-win64-shipping.exe", "cs2.exe", "csgo.exe", "dota2.exe",
    "robloxplayerbeta.exe", "osu!.exe", "gta5.exe", "gta5_enhanced.exe", "r5apex.exe", "r5apex_dx12.exe",
    "overwatch.exe", "league of legends.exe", "eldenring.exe", "cyberpunk2077.exe", "rainbowsix.exe",
    "destiny2.exe", "tslgame.exe", "witcher3.exe", "rocketleague.exe", "genshinimpact.exe", "starrail.exe",
    "zenlesszonezero.exe", "warframe.x64.exe", "deadbydaylight-win64-shipping.exe", "cod.exe", "bf2042.exe",
    "discovery.exe", "marvel-win64-shipping.exe", "helldivers2.exe", "terraria.exe", "stardew valley.exe",
    "hollow_knight.exe", "geometrydash.exe", "minecraft.windows.exe", "javaw.exe", "pathofexile_x64.exe",
    "pathofexile.exe", "wow.exe", "hearthstone.exe", "dbd.exe", "rust.exe", "eft.exe",
    "escapefromtarkov.exe", "fallguys_client_game.exe", "brawlhalla.exe", "paladins.exe", "smite.exe",
}
LAUNCHERS = {
    "steam.exe", "steamwebhelper.exe", "epicgameslauncher.exe", "epicwebhelper.exe", "riotclientservices.exe",
    "riotclientux.exe", "riotclientuxrender.exe", "battle.net.exe", "galaxyclient.exe", "eadesktop.exe",
    "origin.exe", "upc.exe", "ubisoftconnect.exe", "robloxplayerlauncher.exe", "minecraftlauncher.exe",
    "crashreportclient.exe", "unitycrashhandler64.exe", "hoyoplay.exe", "launcher.exe",
}
NOT_GAMES = {
    "explorer.exe", "applicationframehost.exe", "searchhost.exe", "startmenuexperiencehost.exe",
    "shellexperiencehost.exe", "lockapp.exe", "textinputhost.exe", "chrome.exe", "msedge.exe", "firefox.exe",
    "opera.exe", "opera_gx.exe", "brave.exe", "browser.exe", "vivaldi.exe", "arc.exe", "yandex.exe",
    "vlc.exe", "mpc-hc64.exe", "mpc-hc.exe", "mpc-be64.exe", "potplayermini64.exe", "potplayer64.exe",
    "mpv.exe", "wmplayer.exe", "video.ui.exe", "microsoft.media.player.exe", "powerpnt.exe", "obs64.exe",
    "discord.exe", "telegram.exe", "code.exe", "devenv.exe", "windowsterminal.exe", "photos.exe",
    "python.exe", "pythonw.exe", "claude.exe", "taskmgr.exe", "spotify.exe", "yandexmusic.exe",
}
_QUNS_D3D_FULLSCREEN = 3


def _lib():
    try:
        import native_ui
        if native_ui.available() and hasattr(native_ui._lib, "eui_foreground_info"):
            return native_ui._lib
    except Exception:                                          # noqa: BLE001
        pass
    return None


def foreground():
    """(exe_path, pid, fullscreen, quns) окна на переднем плане или None."""
    lib = _lib()
    if lib is None:
        return None
    buf = ctypes.create_unicode_buffer(1024)
    pid, full = ctypes.c_int(0), ctypes.c_int(0)
    quns = lib.eui_foreground_info(buf, 1024, ctypes.byref(pid), ctypes.byref(full))
    return buf.value, pid.value, bool(full.value), int(quns)


def is_game(exe: str, pid: int, fullscreen: bool, quns: int) -> bool:
    if not exe or pid <= 0 or pid == os.getpid():
        return False
    low = exe.lower().replace("/", "\\")
    name = low.rsplit("\\", 1)[-1]
    if name in LAUNCHERS or name in NOT_GAMES and name != "javaw.exe":
        return False
    if quns == _QUNS_D3D_FULLSCREEN:
        return True
    if name in GAME_EXES:
        return name != "javaw.exe" or fullscreen or ".minecraft" in low or "minecraft" in low
    if any(d in low for d in GAME_DIRS):
        return True
    # «оконная без рамки» игра: окно закрывает весь монитор и это не системная программа
    return fullscreen and "\\windows\\" not in low and "\\windowsapps\\microsoft." not in low


def process_alive(pid: int) -> bool:
    lib = _lib()
    return bool(lib is not None and pid > 0 and lib.eui_process_alive(int(pid)))


class GameMode(QObject):
    changed = pyqtSignal(bool, str)                            # включён ли, причина/игра

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.active = False
        self.game = ("", 0)                                    # (имя .exe, pid) игры, из-за которой включён
        self._seen = ("", 0, 0)                                # кандидат: имя, pid, сколько опросов подряд
        self._muted_pid = 0                                    # выключили кнопкой — эту игру не трогаем
        self._prio_saved = None
        self.timer = QTimer(self)
        self.timer.setInterval(POLL_MS)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        QApplication.instance().applicationStateChanged.connect(self._app_state)
        if win.settings.get("gamer_mode_manual"):
            QTimer.singleShot(0, lambda: self.set_active(True, "manual"))

    # ── настройки ──
    def auto(self) -> bool:
        return bool(self.win.settings.get("gamer_mode_auto", True))

    def manual(self) -> bool:
        return bool(self.win.settings.get("gamer_mode_manual", False))

    # ── кнопка ──
    def toggle(self):
        if self.active:
            if self.game[1]:
                self._muted_pid = self.game[1]                 # до конца этой игры — сам не включать
            self.win.settings["gamer_mode_manual"] = False
            self.set_active(False, "manual")
        else:
            self.win.settings["gamer_mode_manual"] = True
            self.set_active(True, "manual")
        try:
            self.win.save_settings()
        except Exception:                                      # noqa: BLE001
            pass

    # ── распознавание ──
    def poll(self):
        if self.active and self.game[1]:
            if not process_alive(self.game[1]):                # игра закрыта
                self.game = ("", 0)
                if not self.manual():
                    self.set_active(False, "game_closed")
            return
        if self._muted_pid and not process_alive(self._muted_pid):
            self._muted_pid = 0
        if not self.auto() or self.manual():
            return
        info = foreground()
        if info is None:
            return
        exe, pid, full, quns = info
        if not is_game(exe, pid, full, quns) or pid == self._muted_pid:
            self._seen = ("", 0, 0)
            return
        name = exe.replace("/", "\\").rsplit("\\", 1)[-1]
        n = self._seen[2] + 1 if self._seen[1] == pid else 1
        self._seen = (name, pid, n)
        if n >= 2:
            self.game = (name, pid)
            print(f"[gamer] игра: {exe} (полный экран: {full}, состояние Windows: {quns})")
            self.set_active(True, name)

    # ── применение ──
    def set_active(self, on: bool, why: str = ""):
        on = bool(on)
        if on == self.active:
            return
        self.active = on
        try:
            import bgproc
            bgproc.set_fg_busy("gamer_mode", on)               # фоновые расчёты на Python ждут
        except Exception:                                      # noqa: BLE001
            pass
        self._priority(on)
        if on:
            self._release_memory()
        self._app_state(QApplication.applicationState())
        try:
            self.win._performance_governor.frame_policy()
        except Exception:                                      # noqa: BLE001
            pass
        self.changed.emit(on, why)

    def _app_state(self, state):
        """Пока режим включён и окно не в фокусе — общие часы кадров стоят (анимации не рисуются)."""
        import frameclock
        frameclock.set_paused(self.active and state != Qt.ApplicationState.ApplicationActive)

    def _priority(self, on: bool):
        if os.name != "nt":
            return
        try:
            k32 = ctypes.windll.kernel32
            k32.GetCurrentProcess.restype = ctypes.c_void_p
            k32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            k32.GetPriorityClass.argtypes = [ctypes.c_void_p]
            proc = k32.GetCurrentProcess()
            if on:
                self._prio_saved = k32.GetPriorityClass(proc) or 0x20
                k32.SetPriorityClass(proc, 0x4000)              # BELOW_NORMAL_PRIORITY_CLASS
            elif self._prio_saved:
                k32.SetPriorityClass(proc, self._prio_saved)
                self._prio_saved = None
        except Exception as e:                                 # noqa: BLE001
            print("[gamer] priority:", e)

    def _release_memory(self):
        """Кэши картинок, модели ИИ, неиспользуемая память — системе (игре нужнее)."""
        try:
            import performance
            performance.clear_caches(self.win)
        except Exception as e:                                 # noqa: BLE001
            print("[gamer] caches:", e)
        import sys
        for mod, fn in (("daw_sep", "release"), ("lyrics_sync", "release")):
            m = sys.modules.get(mod)                           # только загруженные: не импортировать ради выгрузки
            if m is not None and hasattr(m, fn):
                try:
                    getattr(m, fn)()
                except Exception:                              # noqa: BLE001
                    pass

        def trim():
            try:
                import mem_trim
                mem_trim.trim(working_set=True)
            except Exception:                                  # noqa: BLE001
                pass
        QTimer.singleShot(1500, trim)


def install(win):
    gm = win.__dict__.get("_game_mode")
    if gm is None:
        gm = win._game_mode = GameMode(win)
    return gm


def active(win=None) -> bool:
    gm = getattr(win, "_game_mode", None) if win is not None else None
    return bool(gm is not None and gm.active)
