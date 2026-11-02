"""Regression coverage for packaging and isolated studio/designer data flows."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ECHOES_NO_NATIVE", "1")


class ComponentAudit(unittest.TestCase):
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
