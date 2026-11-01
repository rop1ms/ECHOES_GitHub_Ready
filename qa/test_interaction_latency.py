"""Input must apply immediately without serializing settings or repainting covered pages."""
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
runtime = Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs/ECHOES'
os.environ.setdefault('PYTHON_VLC_LIB_PATH', str(runtime / 'vlc/libvlc.dll'))
from PyQt6.QtCore import Qt, QRect, QPointF
from PyQt6.QtWidgets import QApplication, QWidget, QMainWindow, QDialog, QStackedWidget
from PyQt6.QtGui import QPixmap, QColor
from PyQt6.QtTest import QTest
import main
import inapp
import ui_anim
from net_files_ui import _Canvas

APP = QApplication.instance() or QApplication([])


class InputLatency(unittest.TestCase):
    def test_volume_applies_every_step_but_persists_once(self):
        class Host(QWidget):
            _volume_changed = main.MainWindow._volume_changed
            _schedule_settings_save = main.MainWindow._schedule_settings_save
            save_settings = main.MainWindow.save_settings
        host = Host()
        applied = []
        host.engine = NS(set_volume=applied.append)
        host.settings = {}
        with patch.object(main, 'save_json') as save:
            for i in range(100): host._volume_changed(i)
            self.assertEqual(len(applied), 100)
            self.assertEqual(applied[-1], .99)
            save.assert_not_called()
            QTest.qWait(420)
            self.assertEqual(save.call_count, 1)
            host._volume_changed(37)
            host.save_settings()  # close/explicit save flushes immediately
            self.assertEqual(save.call_count, 2)
            QTest.qWait(420)
            self.assertEqual(save.call_count, 2)
        host.deleteLater()

    def test_opaque_dialog_does_not_repaint_page_during_drag(self):
        class Page(QWidget):
            paints = 0
            def paintEvent(self, event): self.paints += 1
        win = QMainWindow(); page = Page(); win.setCentralWidget(page)
        win.resize(1000, 720); win.show(); APP.processEvents()
        content = QDialog(win); content.resize(550, 400)
        layer = inapp.Layer(win, content, 'Preview')
        APP.processEvents()
        page.paints = 0
        for i in range(20):
            layer._set_card_geometry(QRect(30+i, 30+i, 600, 430))
            APP.processEvents()
        self.assertEqual(page.paints, 0)
        self.assertTrue(layer.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent))
        layer.request_close(); win.close(); APP.processEvents()

    def test_static_photo_pan_reuses_display_raster(self):
        canvas = _Canvas(); canvas.resize(1000, 700)
        canvas.pm = QPixmap(2600, 1800); canvas.pm.fill(QColor('#56789a'))
        canvas.reset(); canvas.show(); APP.processEvents()
        first = canvas._display_cache[1].cacheKey()
        for i in range(10):
            canvas.off = QPointF(i, i); canvas.repaint()
        self.assertEqual(canvas._display_cache[1].cacheKey(), first)
        canvas.zoom_to(canvas.scale * 1.1); canvas.repaint()
        self.assertNotEqual(canvas._display_cache[1].cacheKey(), first)
        canvas.close()

    def test_new_input_finishes_page_snapshot(self):
        stack = QStackedWidget(); a, b = QWidget(), QWidget()
        stack.addWidget(a); stack.addWidget(b)
        stack.resize(700, 450); stack.show(); APP.processEvents()
        with patch.object(ui_anim, 'enabled', return_value=True):
            ui_anim.switch(stack, b)
            self.assertIn(id(stack), ui_anim._OVERLAYS)
            QTest.mousePress(b, Qt.MouseButton.LeftButton)
            self.assertNotIn(id(stack), ui_anim._OVERLAYS)
        stack.close(); APP.processEvents()


if __name__ == '__main__': unittest.main()
