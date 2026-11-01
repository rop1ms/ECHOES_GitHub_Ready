"""Offline regressions for the playback/download audit. Does not open audio devices."""
import copy
import os
from pathlib import Path
import random
import sys
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from PyQt6.QtWidgets import QApplication, QWidget
from music_duel import Duel
from online_dl import OnlineDownloadQueue
from tier_store import TierStore
from listening_tools import ListeningTools, parse_time


class Downloads(unittest.TestCase):
    def test_name_collision_preserves_both_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); (root / "in").mkdir(); (root / "out").mkdir()
            source = root / "in/song.mp3"; source.write_bytes(b"new audio")
            (root / "out/song.mp3").write_bytes(b"original audio")
            (root / "out/song (2).mp3").write_bytes(b"another audio")
            host = NS(_out_dir_fn=lambda: root / "out")
            final = Path(OnlineDownloadQueue._move_up(host, str(source)))
            self.assertEqual(final.name, "song (3).mp3")
            self.assertEqual(final.read_bytes(), b"new audio")
            self.assertEqual((root / "out/song.mp3").read_bytes(), b"original audio")
            self.assertEqual((root / "out/song (2).mp3").read_bytes(), b"another audio")
            self.assertFalse(source.exists())

    def test_failed_copy_preserves_source_and_removes_partial(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); (root / "in").mkdir()
            source = root / "in/song.mp3"; source.write_bytes(b"new audio")
            host = NS(_out_dir_fn=lambda: root / "out")
            def fail_copy(source, target):
                target.write(b"partial"); raise OSError("disk full")
            with patch("online_dl.shutil.copyfileobj", fail_copy):
                with self.assertRaises(OSError): OnlineDownloadQueue._move_up(host, str(source))
            self.assertEqual(source.read_bytes(), b"new audio")
            self.assertEqual(list((root / "out").iterdir()), [])

    def test_move_failure_is_error_and_keeps_download(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); root.mkdir(exist_ok=True)
            source = root / "song.mp3"; source.write_bytes(b"new audio")
            queue = OnlineDownloadQueue(lambda: root / "blocked")
            queue._next = lambda: None
            queue._is_preview = lambda p: False
            queue._wrong_track = lambda p: False
            queue._move_up = lambda p: (_ for _ in ()).throw(OSError("access denied"))
            queue.current = {"url": "test:track", "track": {}, "tmp": root}
            queue._on_item(str(source))
            self.assertFalse(queue._got_file)
            self.assertIn("move_error", queue.current)
            queue._on_finished(True, "done")
            self.assertEqual(queue.state("test:track")[0], "error")
            self.assertTrue(source.exists())


class Duels(unittest.TestCase):
    def game(self, mode="pairs"):
        game = Duel(); game.start([{"path": f"track-{i}", "title": str(i)} for i in range(5)], mode, random.Random(4))
        return game

    def test_pair_history_is_validated(self):
        game = self.game(); game.vote(0); snapshot = game.dump()
        for entry in ({"pair": [0, 1]}, {"pair": [0, 1], "winner": 3},
                      {"pair": [0, 0], "winner": 0}, {"pair": [0, 1], "winner": True}):
            bad = copy.deepcopy(snapshot); bad["history"] = [entry]
            restored = self.game(); before = restored.dump()
            self.assertFalse(restored.restore(bad)); self.assertEqual(restored.dump(), before)

    def test_snapshot_and_restore_do_not_alias_saved_settings(self):
        game = self.game(); game.vote(1); saved = game.dump()
        restored = Duel(); self.assertTrue(restored.restore(saved)); restored.undo()
        self.assertEqual(len(saved["history"]), 1)
        saved["roster"][0]["title"] = "changed"
        self.assertNotEqual(restored.roster[0]["title"], "changed")

    def test_king_and_pairs_roundtrip_and_undo(self):
        for mode in ("king", "pairs"):
            game = self.game(mode)
            count = 4 if mode == "king" else 40
            for i in range(count): game.vote(i % 2)
            snapshot = game.dump(); restored = Duel()
            self.assertTrue(restored.restore(snapshot)); self.assertEqual(restored.wins(), game.wins())
            for _ in range(count): restored.undo()
            self.assertEqual(restored.history, [])
            self.assertEqual(len(restored.current()), 2)


class TierPersistence(unittest.TestCase):
    def test_rename_and_remap_keep_ratings(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); store = TierStore(root / "tiers.json")
            tier = store.tiers[0]; store.assign(root / "old.mp3", tier["id"])
            rows = store.tiers; rows[0]["name"] = "Best"; store.update_tiers(rows)
            store.remap_path(root / "old.mp3", root / "new.mp3")
            reloaded = TierStore(store.path)
            self.assertEqual(reloaded.tiers[0]["name"], "Best")
            self.assertEqual(reloaded.tier_for(root / "new.mp3"), tier["id"])

    def test_failed_atomic_save_does_not_change_state(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); store = TierStore(root / "tiers.json")
            store.assign(root / "old.mp3", store.tiers[0]["id"])
            before = store.snapshot(); disk = store.path.read_bytes()
            with patch("tier_store.os.replace", side_effect=OSError("write denied")):
                with self.assertRaises(OSError): store.assign(root / "new.mp3", store.tiers[1]["id"])
            self.assertEqual(store.snapshot(), before); self.assertEqual(store.path.read_bytes(), disk)


class VlcDiscovery(unittest.TestCase):
    def test_missing_dll_never_walks_drives(self):
        from vlc_location import find_libvlc
        with patch.dict(sys.modules, {"winreg": None}), patch("vlc_location.os.walk", side_effect=AssertionError("drive crawl")), patch("vlc_location.os.path.isfile", return_value=False):
            self.assertIsNone(find_libvlc(app_dir="C:/test/app", environ={}))

    def test_bundle_standard_path_and_explicit_locations(self):
        from vlc_location import find_libvlc
        with tempfile.TemporaryDirectory() as td, patch.dict(sys.modules, {"winreg": None}):
            root = Path(td); app_dir = root / "app"; app_dir.mkdir()
            paths = [root / "vlc", root / "programfiles/VideoLAN/VLC", root / "custom", root / "explicit"]
            for directory in paths: directory.mkdir(parents=True)
            for directory in paths: (directory / "libvlc.dll").write_bytes(b"fake DLL, not loaded")
            env = {"ProgramFiles": str(root / "programfiles"), "PATH": '"' + str(paths[2]) + '"'}
            self.assertEqual(Path(find_libvlc(app_dir=app_dir, environ=env)), paths[0])
            (paths[0] / "libvlc.dll").unlink()
            self.assertEqual(Path(find_libvlc(app_dir=app_dir, environ=env)), paths[1])
            (paths[1] / "libvlc.dll").unlink()
            self.assertEqual(Path(find_libvlc(app_dir=app_dir, environ=env)), paths[2])
            env["PYTHON_VLC_LIB_PATH"] = str(paths[3] / "libvlc.dll")
            self.assertEqual(Path(find_libvlc(app_dir=app_dir, environ=env)), paths[3])
            env.pop("PYTHON_VLC_LIB_PATH"); env["ECHOES_VLC_DIR"] = str(paths[3])
            self.assertEqual(Path(find_libvlc(app_dir=app_dir, environ=env)), paths[3])

    def test_loader_passes_selected_dll_to_python_vlc(self):
        # Execute only the loader block with a mock VLC, without importing/opening hardware.
        import ast
        tree = ast.parse((Path(__file__).resolve().parents[1] / "app/audio_engine.py").read_text(encoding="utf-8"))
        block = next(n for n in tree.body if isinstance(n, ast.If) and "platform.system" in ast.unparse(n.test))
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); (root / "libvlc.dll").write_bytes(b"fake DLL")
            import types
            mock_vlc = types.ModuleType("vlc")
            namespace = {"os": os, "sys": sys, "platform": NS(system=lambda: "Windows")}
            before = os.getcwd()
            with patch.dict(os.environ, {"PYTHON_VLC_LIB_PATH": str(root / "libvlc.dll"), "PYTHON_VLC_MODULE_PATH": str(root)}), patch.dict(sys.modules, {"vlc": mock_vlc}), patch("vlc_location.find_libvlc", return_value=str(root)), patch.object(os, "add_dll_directory", return_value=NS(close=lambda: None)):
                exec(compile(ast.Module(body=[block], type_ignores=[]), "audio_engine_loader", "exec"), namespace)
                self.assertEqual(namespace["vlc"], mock_vlc)
                self.assertEqual(os.environ["PYTHON_VLC_LIB_PATH"], str(root / "libvlc.dll"))
                self.assertEqual(os.getcwd(), before)


    def test_loader_replaces_stale_env_and_keeps_valid_custom_dll(self):
        import ast, types
        tree = ast.parse((Path(__file__).resolve().parents[1] / "app/audio_engine.py").read_text(encoding="utf-8"))
        block = next(n for n in tree.body if isinstance(n, ast.If) and "platform.system" in ast.unparse(n.test))
        compiled = compile(ast.Module(body=[block], type_ignores=[]), "audio_engine_loader", "exec")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); (root / "libvlc.dll").write_bytes(b"fake bundle")
            custom = root / "renamed-custom.dll"; custom.write_bytes(b"fake custom")
            mock_vlc = types.ModuleType("vlc")
            for override, expected in ((root / "missing.dll", root / "libvlc.dll"), (custom, custom)):
                namespace = {"os": os, "sys": sys, "platform": NS(system=lambda: "Windows")}
                with patch.dict(os.environ, {"PYTHON_VLC_LIB_PATH": str(override), "PYTHON_VLC_MODULE_PATH": str(root / "missing-plugins")}), patch.dict(sys.modules, {"vlc": mock_vlc}), patch("vlc_location.find_libvlc", return_value=str(root)), patch.object(os, "add_dll_directory", return_value=NS(close=lambda: None)):
                    exec(compiled, namespace)
                    self.assertEqual(os.environ["PYTHON_VLC_LIB_PATH"], str(expected))
                    self.assertEqual(os.environ["PYTHON_VLC_MODULE_PATH"], str(root))

    def test_loader_restores_cwd_when_dll_directory_setup_fails(self):
        import ast
        tree = ast.parse((Path(__file__).resolve().parents[1] / "app/audio_engine.py").read_text(encoding="utf-8"))
        block = next(n for n in tree.body if isinstance(n, ast.If) and "platform.system" in ast.unparse(n.test))
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); (root / "libvlc.dll").write_bytes(b"fake bundle")
            namespace = {"os": os, "sys": sys, "platform": NS(system=lambda: "Windows")}
            before = os.getcwd()
            with patch.dict(os.environ, {"PYTHON_VLC_LIB_PATH": str(root / "libvlc.dll"), "PYTHON_VLC_MODULE_PATH": str(root)}), patch("vlc_location.find_libvlc", return_value=str(root)), patch.object(os, "add_dll_directory", side_effect=OSError("setup failed")):
                with self.assertRaises(OSError):
                    exec(compile(ast.Module(body=[block], type_ignores=[]), "audio_engine_loader", "exec"), namespace)
                self.assertEqual(os.getcwd(), before)


class AudioMixer(unittest.TestCase):
    def test_sample_exact_gapless_with_memory_backend(self):
        import numpy as np
        import audio_out
        backend = lambda render: NS(latency_s=.01, close=lambda: None)
        mixer = audio_out.Mixer(backend)
        first = mixer.new_deck("first"); second = mixer.new_deck("second")
        for deck, count, value in ((first, 3, .1), (second, 4, .2)):
            deck.chunks.append([0., np.full((count, 2), value, dtype=np.float32), 0])
            deck.frames = count; deck.state = "play"; deck.eos = True; deck.g = 1.
        mixer.chain(first, second)
        with patch("audio_out.vlc.libvlc_clock", return_value=0):
            rendered = mixer._render(8)
        np.testing.assert_allclose(rendered[:, 0], [.1, .1, .1, .2, .2, .2, .2, 0])
        self.assertTrue(first.finished()); self.assertTrue(second.finished())
        self.assertEqual(first.played, 3); self.assertEqual(second.played, 4)
        mixer.close()

    def test_limiter_and_paused_deck(self):
        import numpy as np
        import audio_out
        values = np.array([-10., -1., -.2, 0, .2, 1., 10.], dtype=np.float32)
        limited = audio_out._soft_limit(values)
        self.assertTrue(np.all(np.isfinite(limited)))
        self.assertTrue(np.all(np.abs(limited) <= 1.))
        np.testing.assert_allclose(limited[2:5], [-.2, 0, .2])
        mixer = audio_out.Mixer(lambda render: NS(latency_s=.01, close=lambda: None))
        deck = mixer.new_deck("paused"); deck.state = "play"; deck.paused = True
        deck.chunks.append([0., np.ones((5, 2), dtype=np.float32), 0]); deck.frames = 5
        with patch("audio_out.vlc.libvlc_clock", return_value=0):
            self.assertFalse(mixer._render(5).any())
        self.assertEqual(deck.frames, 5); mixer.close()


class Listener(unittest.TestCase):
    def setUp(self):
        self.win = QWidget(); self.win.settings = {}; self.win.save_settings = lambda: None
        self.position = 1.; self.playing = True; self.seeks = []; self.pauses = []
        self.win._current_track = lambda: {"path": "test.mp3", "title": "Test"}
        self.win._set_playing_ui = lambda playing: None
        self.win.engine = NS(mixer=None, duration=100., speed=1.,
            get_position=lambda: self.position, is_playing=lambda: self.playing,
            seek=self.seeks.append, stop=lambda: self.pauses.append("stop"),
            pause_hard=lambda: self.pauses.append("pause"))
        self.tools = ListeningTools(self.win); self.tools.timer.stop(); self.tools.sync_track()
    def tearDown(self): self.win.close()

    def test_time_validation_and_jump_clamp(self):
        self.assertEqual(parse_time("1:23.5"), 83.5)
        for value in ("nan", "inf", "-1", "1:99"):
            with self.assertRaises(ValueError): parse_time(value)
        self.tools.jump("3:00"); self.assertEqual(self.seeks[-1], 100.)

    def test_loop_and_stop_are_mutually_exclusive(self):
        self.tools.a = 1.; self.tools.b = 5.; self.tools.enable_loop(True)
        self.assertEqual(self.seeks[-1], 1.)
        self.tools.toggle_stop_after(True)
        self.assertFalse(self.tools.loop_enabled); self.assertTrue(self.tools.consume_stop())
        self.assertEqual(self.pauses, ["stop"])
        self.assertFalse(self.tools.consume_stop())

    def test_bpm_and_bookmark_import(self):
        self.assertIsNone(self.tools.tap(10.))
        self.assertEqual(self.tools.tap(10.5), 120.)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "marks.json"
            p.write_text('[{"path":"song.mp3","pos":12},{"path":"song.mp3","pos":12},{"path":"x","pos":-3}]', encoding="utf-8")
            self.tools.plus.import_bookmarks(p)
            self.assertEqual(len(self.tools.bookmarks()), 1)
            self.assertEqual(self.tools.bookmarks()[0]["pos"], 12.)


if __name__ == "__main__":
    app = QApplication.instance() or QApplication([])
    unittest.main()
