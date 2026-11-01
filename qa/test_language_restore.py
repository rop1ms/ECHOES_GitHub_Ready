"""Fresh-install language and interrupted full-screen transition regressions."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMainWindow, QLabel
import i18n
import ui_anim
import frameclock

APP = QApplication.instance() or QApplication([])


class LanguageRestore(unittest.TestCase):
    def test_default_and_existing_language(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            self.assertEqual(i18n.read_language(path), 'en')
            for data, expected in (({}, 'ru'), ({'language':'ru'}, 'ru'), ({'language':'en'}, 'en')):
                path.write_text(json.dumps(data), 'utf-8')
                self.assertEqual(i18n.read_language(path), expected)

    def test_catalog_templates_and_reload(self):
        i18n.install('en')
        count = len(i18n._TEMPLATES)
        getter = QLabel.text
        i18n.install('en')
        self.assertEqual(count, len(i18n._TEMPLATES))
        self.assertIs(getter, QLabel.text)
        self.assertEqual(i18n.tr('Голосовые команды и сценарии'), 'Voice commands and scenarios')
        self.assertEqual(i18n.tr('РАУНД 1 ИЗ 11'), 'ROUND 1 OF 11')
        self.assertEqual(i18n.tr('←  ВЫБРАТЬ'), '←  SELECT')
        self.assertNotRegex(i18n.tr('1. — пусто —'), i18n._CYR)
        catalog = json.loads((Path(i18n.__file__).parent / 'assets/i18n_en_complete.json').read_text('utf-8'))
        import re
        for ru, en in catalog['templates'].items():
            self.assertEqual(sorted(re.findall(r'\{\d+\}', ru)), sorted(re.findall(r'\{\d+\}', en)))
            arguments = ['test'] * (1 + max([int(x) for x in re.findall(r'\{(\d+)\}', en)] or [0]))
            en.format(*arguments)

    def test_minimize_before_queued_transition_starts(self):
        self._interrupted_restore(0)

    def test_minimize_during_fullscreen_transition(self):
        self._interrupted_restore(35)

    def _interrupted_restore(self, delay):
        win = QMainWindow()
        win.setCentralWidget(QLabel('ECHOES restore regression'))
        win.resize(600, 400)
        win.show()
        APP.processEvents()
        controller = ui_anim._WinAnim(win)
        frameclock.clock().set_paused(False)
        try:
            with patch.object(ui_anim, 'enabled', return_value=True):
                ui_anim.animate_state(win, 'full')
                if delay: QTest.qWait(delay)
                controller.minimize()
                QTest.qWait(20)
                self.assertIsNone(getattr(win, '_state_anim', None))
                self.assertTrue(win.updatesEnabled())
                ui_anim.bring_front(win)
                QTest.qWait(550)
                self.assertTrue(win.isFullScreen())
                self.assertFalse(win.isMinimized())
                self.assertTrue(win.updatesEnabled())
                self.assertEqual(win.windowOpacity(), 1.0)
                self.assertFalse(win.grab().isNull())
        finally:
            win.close()
            APP.processEvents()


if __name__ == '__main__': unittest.main()
