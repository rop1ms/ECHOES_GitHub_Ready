"""Gamer mode (game_mode.py): game recognition and what the mode switches.

- is_game: fullscreen Direct3D, game folders and known games are games; launchers, browsers,
  video players and ECHOES itself are not; a borderless fullscreen app counts unless it is a system one;
- the foreground probe in echoes_ui.dll answers;
- turning the mode on pauses background Python work, lowers the process priority, pauses the frame
  clock only while the window is not focused, and everything is restored when it turns off;
- auto mode needs the game on screen for two polls and turns off when the game process is gone;
  turning it off by hand during a game keeps it off until that game exits."""
import ctypes
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QWidget

APP = QApplication.instance() or QApplication([])
import bgproc  # noqa: E402
import frameclock  # noqa: E402
import game_mode  # noqa: E402
import native_ui  # noqa: E402


class Win(QWidget):
    def __init__(self):
        super().__init__()
        self.settings = {}

    def save_settings(self):
        pass


def priority():
    k32 = ctypes.windll.kernel32
    k32.GetCurrentProcess.restype = ctypes.c_void_p
    k32.GetPriorityClass.argtypes = [ctypes.c_void_p]
    return k32.GetPriorityClass(k32.GetCurrentProcess())


class Recognition(unittest.TestCase):
    def test_games(self):
        g = game_mode.is_game
        self.assertTrue(g(r'D:\Games\Fortnite\FortniteGame\Binaries\Win64\FortniteClient-Win64-Shipping.exe', 4242, False, 1))
        self.assertTrue(g(r'C:\Program Files (x86)\Steam\steamapps\common\Terraria\Terraria.exe', 4242, False, 1))
        self.assertTrue(g(r'C:\Riot Games\VALORANT\live\ShooterGame\Binaries\Win64\VALORANT-Win64-Shipping.exe', 4242, False, 1))
        self.assertTrue(g(r'C:\Something\Unknown.exe', 4242, False, 3), 'Windows reports fullscreen Direct3D')
        self.assertTrue(g(r'C:\Indie\cool_game.exe', 4242, True, 1), 'borderless fullscreen app')
        self.assertTrue(g(r'C:\Users\u\AppData\Local\Roblox\Versions\v1\RobloxPlayerBeta.exe', 4242, False, 1))

    def test_not_games(self):
        g = game_mode.is_game
        self.assertFalse(g(r'C:\Program Files (x86)\Steam\steam.exe', 4242, True, 1))
        self.assertFalse(g(r'C:\Program Files\Epic Games\Launcher\Portal\Binaries\Win64\EpicGamesLauncher.exe', 4242, False, 1))
        self.assertFalse(g(r'C:\Program Files\Google\Chrome\Application\chrome.exe', 4242, True, 3), 'fullscreen video')
        self.assertFalse(g(r'C:\Program Files\VideoLAN\VLC\vlc.exe', 4242, True, 1))
        self.assertFalse(g(r'C:\Windows\explorer.exe', 4242, True, 1))
        self.assertFalse(g(r'C:\Windows\System32\notepad.exe', 4242, True, 1), 'system program')
        self.assertFalse(g(r'C:\Tools\editor.exe', 4242, False, 1), 'ordinary window')
        self.assertFalse(g(r'C:\Indie\cool_game.exe', os.getpid(), True, 3), 'ECHOES itself')
        self.assertFalse(g(r'C:\Program Files\Java\bin\javaw.exe', 4242, False, 1), 'java app in a window')

    def test_probe(self):
        if not native_ui.available():
            self.skipTest('echoes_ui.dll unavailable')
        info = game_mode.foreground()
        self.assertIsNotNone(info)
        exe, pid, full, quns = info
        self.assertIsInstance(exe, str)
        self.assertIn(quns, range(0, 8))
        self.assertTrue(game_mode.process_alive(os.getpid()))
        self.assertFalse(game_mode.process_alive(0))


class Switching(unittest.TestCase):
    def setUp(self):
        self.win = Win()
        self.gm = game_mode.GameMode(self.win)
        self.gm.timer.stop()
        self.addCleanup(lambda: self.gm.set_active(False))
        p = patch.object(game_mode.GameMode, '_release_memory', lambda self: None)
        p.start()
        self.addCleanup(p.stop)

    def test_effects_and_restore(self):
        before = priority()
        self.gm.set_active(True, 'manual')
        self.assertTrue(bgproc.fg_busy(), 'background Python work waits')
        self.assertEqual(priority(), 0x4000, 'below normal while gaming')
        self.gm._app_state(Qt.ApplicationState.ApplicationInactive)
        self.assertTrue(frameclock.clock().paused, 'animations stop while ECHOES is not focused')
        self.gm._app_state(Qt.ApplicationState.ApplicationActive)
        self.assertFalse(frameclock.clock().paused)
        self.gm.set_active(False, 'manual')
        self.assertFalse(bgproc.fg_busy())
        self.assertEqual(priority(), before)
        self.gm._app_state(Qt.ApplicationState.ApplicationInactive)
        self.assertFalse(frameclock.clock().paused, 'without the mode the clock is not paused')

    def test_auto_detection_lifecycle(self):
        alive = {'pid': True}
        fg = (r'C:\Steam\steamapps\common\Game\game.exe', 777, True, 1)
        with patch.object(game_mode, 'foreground', lambda: fg), \
                patch.object(game_mode, 'process_alive', lambda pid: pid == 777 and alive['pid']):
            self.gm.poll()
            self.assertFalse(self.gm.active, 'one poll is not enough')
            self.gm.poll()
            self.assertTrue(self.gm.active)
            self.assertEqual(self.gm.game, ('game.exe', 777))
            self.gm.toggle()                                   # turned off by hand mid-game
            self.assertFalse(self.gm.active)
            self.gm.poll(); self.gm.poll(); self.gm.poll()
            self.assertFalse(self.gm.active, 'stays off until this game exits')
            alive['pid'] = False
            self.gm.poll()
            alive['pid'] = True
            self.gm.poll(); self.gm.poll()
            self.assertTrue(self.gm.active, 'a new run of the game turns it on again')
            alive['pid'] = False
            self.gm.poll()
            self.assertFalse(self.gm.active, 'game closed')

    def test_auto_off_setting(self):
        self.win.settings['gamer_mode_auto'] = False
        with patch.object(game_mode, 'foreground', lambda: (r'C:\Steam\steamapps\common\G\g.exe', 5, True, 3)):
            for _ in range(4):
                self.gm.poll()
        self.assertFalse(self.gm.active)


if __name__ == '__main__':
    unittest.main()
