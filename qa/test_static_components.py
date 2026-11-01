"""Regression coverage for packaging and isolated studio/map/designer data flows."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ECHOES_NO_NATIVE", "1")


class ComponentAudit(unittest.TestCase):
    def test_osz_import_stays_inside_song_directory(self):
        import osu_beatmap as maps
        with tempfile.TemporaryDirectory(prefix="echoes-osz-audit-") as tmp:
            folder = Path(tmp)
            old_songs = maps.SONGS
            maps.SONGS = folder / "songs"
            try:
                archive = folder / "test.osz"
                escaped = folder / "escaped.osu"
                with zipfile.ZipFile(archive, "w") as z:
                    z.writestr("map.osu", "osu file format v14")
                    z.writestr("../escaped.osu", "invalid")
                    # A valid absolute Windows member name used to bypass '..'.
                    z.writestr(escaped.as_posix(), "invalid")
                result = Path(maps.extract_osz(str(archive)))
                self.assertTrue((result / "map.osu").is_file())
                self.assertFalse(escaped.exists())
                self.assertEqual(result, Path(maps.extract_osz(str(archive))))
            finally:
                maps.SONGS = old_songs

    def test_osz_same_name_archives_do_not_merge(self):
        import osu_beatmap as maps
        with tempfile.TemporaryDirectory(prefix="echoes-osz-names-") as tmp:
            folder = Path(tmp)
            old_songs = maps.SONGS
            maps.SONGS = folder / "songs"
            try:
                results = []
                for i in range(2):
                    source = folder / str(i)
                    source.mkdir()
                    archive = source / "same.osz"
                    with zipfile.ZipFile(archive, "w") as z:
                        z.writestr("map.osu", str(i))
                    results.append(Path(maps.extract_osz(str(archive))))
                self.assertNotEqual(*results)
                self.assertEqual((results[0] / "map.osu").read_text(), "0")
                self.assertEqual((results[1] / "map.osu").read_text(), "1")
            finally:
                maps.SONGS = old_songs

    def test_studio_project_and_pattern_roundtrip(self):
        import daw_project as projects
        with tempfile.TemporaryDirectory(prefix="echoes-studio-audit-") as tmp:
            old_projects = projects.PROJECTS
            projects.PROJECTS = Path(tmp)
            try:
                project = projects.new_project("Аудит")
                pattern = project["patterns"][0]
                channel_id = project["channels"][0]["id"]
                self.assertTrue(projects.toggle_step(pattern, channel_id, 4))
                path = projects.save(project)
                loaded = projects.load(path)
                self.assertEqual(projects.steps_of(loaded["patterns"][0], channel_id), {4})
                self.assertFalse(projects.toggle_step(loaded["patterns"][0], channel_id, 4))
                self.assertEqual(projects.notes_of(loaded["patterns"][0], channel_id), [])
            finally:
                projects.PROJECTS = old_projects

    def test_script_compiler_executes_bounded_program(self):
        import dsl_lang as dsl
        program = dsl.compile_program("let total = 0\nfor i in 0..10 { total += i * i }")
        dsl.BUDGET[0] = dsl.DEFAULT_BUDGET
        frame = dsl.Frame({}, None, None, {"__user__": set()})
        program.top(frame)
        self.assertEqual(frame.vars["total"], 285)

    def test_stream_design_validates_bounds(self):
        from stream_designer import normalize, default_design
        design = normalize(default_design())
        self.assertEqual(normalize(json.loads(json.dumps(design))), design)
        raw = {"width": float("nan"), "height": -1, "items": [
            {"kind": "text", "x": .99, "w": 5, "y": .99, "h": 5}, None,
            {"kind": "unknown"}]}
        normalized = normalize(raw)
        self.assertEqual(len(normalized["items"]), 1)
        item = normalized["items"][0]
        self.assertLessEqual(item["x"] + item["w"], 1)
        self.assertLessEqual(item["y"] + item["h"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
