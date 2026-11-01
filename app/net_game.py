# net_game.py
"""
«Сейчас в игре»: если на переднем плане игра на весь экран, друзья видят значок 🎮 с её названием
(в карточке друга, в профиле и в шапке чата). Определяется раз в 20 с по окну на переднем плане
(то же правило, что у игрового оверлея); браузеры, плееры и прочие «не игры» не считаются.
Отключается в настройках Echoes.NET («Показывать, во что играю»).
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes

from PyQt6.QtCore import QObject, QTimer

# процессы, которые бывают на весь экран, но это не игры
NOT_GAMES = {
    "chrome", "msedge", "firefox", "opera", "opera_gx", "brave", "yandex", "browser", "vivaldi", "arc",
    "vlc", "mpc-hc", "mpc-hc64", "mpc-be64", "potplayermini64", "potplayer", "wmplayer", "video.ui", "mpv",
    "explorer", "applicationframehost", "powerpnt", "winword", "excel", "acrord32", "acrobat", "obs64",
    "discord", "telegram", "spotify", "zoom", "teams", "ms-teams", "steamwebhelper", "textinputhost",
    "lockapp", "searchhost", "startmenuexperiencehost", "shellexperiencehost", "code", "devenv", "python",
    "pythonw", "echoes", "photos", "microsoft.photos", "screenclippinghost", "snippingtool", "mspaint",
}

# красивые названия известных игр (по имени файла без .exe)
KNOWN = {
    "cs2": "Counter-Strike 2", "csgo": "CS:GO", "dota2": "Dota 2", "valorant-win64-shipping": "VALORANT",
    "fortniteclient-win64-shipping": "Fortnite", "r5apex": "Apex Legends", "r5apex_dx12": "Apex Legends",
    "gta5": "GTA V", "gta5_enhanced": "GTA V", "rdr2": "Red Dead Redemption 2", "minecraft": "Minecraft",
    "javaw": "Minecraft", "robloxplayerbeta": "Roblox", "leagueoflegends": "League of Legends",
    "league of legends": "League of Legends", "overwatch": "Overwatch 2", "eldenring": "ELDEN RING",
    "cyberpunk2077": "Cyberpunk 2077", "witcher3": "The Witcher 3", "rustclient": "Rust", "pubg": "PUBG",
    "tslgame": "PUBG", "rainbowsix": "Rainbow Six Siege", "rocketleague": "Rocket League",
    "deadbydaylight-win64-shipping": "Dead by Daylight", "terraria": "Terraria", "hollow_knight": "Hollow Knight",
    "genshinimpact": "Genshin Impact", "starrail": "Honkai: Star Rail", "zenlesszonezero": "Zenless Zone Zero",
    "wow": "World of Warcraft", "hearthstone": "Hearthstone", "diablo iv": "Diablo IV", "fifa23": "FIFA 23",
    "fc24": "EA FC 24", "fc25": "EA FC 25", "fc26": "EA FC 26", "warthunder": "War Thunder", "aces": "War Thunder",
    "worldoftanks": "World of Tanks", "tarkov": "Escape from Tarkov", "escapefromtarkov": "Escape from Tarkov",
    "bg3": "Baldur's Gate 3", "bg3_dx11": "Baldur's Gate 3", "helldivers2": "HELLDIVERS 2", "palworld-win64-shipping":
    "Palworld", "osu!": "osu!", "stardew valley": "Stardew Valley", "lethal company": "Lethal Company",
    "deadlock": "Deadlock", "marvel-win64-shipping": "Marvel Rivals", "thefinals": "THE FINALS",
}


def _exe_of(hwnd) -> str:
    k = ctypes.windll.kernel32
    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = k.OpenProcess(0x1000, False, pid.value)                # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(520)
        n = wintypes.DWORD(520)
        if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return buf.value
    finally:
        k.CloseHandle(h)
    return ""


def name_for(exe: str, title: str) -> str:
    """Имя игры: известное → по файлу; иначе — заголовок окна (или имя файла)."""
    stem = os.path.splitext(os.path.basename(exe or ""))[0].lower()
    if not stem or stem in NOT_GAMES:
        return ""
    if stem in KNOWN:
        return KNOWN[stem]
    t = (title or "").strip()
    for cut in (" - ", " — ", " | "):                           # «Игра - DirectX 12» → «Игра»
        if cut in t:
            t = t.split(cut)[0].strip()
    return (t or os.path.splitext(os.path.basename(exe))[0])[:60]


def detect() -> str:
    """Название игры на весь экран на переднем плане или ""."""
    if sys.platform != "win32":
        return ""
    try:
        from overlay_live import game_monitor
        if game_monitor() is None:
            return ""
        u = ctypes.windll.user32
        hwnd = u.GetForegroundWindow()
        buf = ctypes.create_unicode_buffer(256)
        u.GetWindowTextW(hwnd, buf, 256)
        return name_for(_exe_of(hwnd), buf.value)
    except Exception:                                          # noqa: BLE001
        return ""


def badge(name: str) -> str:
    return f"\U000f1003 Играет в {name}" if name else ""


class Watcher(QObject):
    """Раз в 20 с: сменилась игра — разослать друзьям (только если включено «Показывать, во что играю»)."""

    def __init__(self, net, interval=20000):
        super().__init__(net)
        self.net = net
        self.cur = ""
        self.t = QTimer(self)
        self.t.setInterval(interval)
        self.t.timeout.connect(self.check)
        self.t.start()

    def check(self, game=None):
        nd = self.net.node
        if nd is None or not self.net.running():
            return
        g = detect() if game is None else game
        if not nd.settings.get("share_game", True):
            g = ""
        if g != self.cur:
            self.cur = g
            nd.call(nd.set_game, g)
