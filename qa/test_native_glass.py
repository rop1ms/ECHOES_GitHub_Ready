"""Window glass and transition snapshots from echoes_ui.dll must match the Python versions exactly.

- echo_glass._liquid_surface: C++ port vs the original Python drawing (grain included), several
  sizes/radii, compared pixel for pixel;
- ui_anim outgoing snapshot: copy from the window buffer vs re-rendering the page (_grab_area)."""
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from PyQt6.QtCore import QEvent, QSize, Qt
from PyQt6.QtGui import QColor, QImage, QLinearGradient, QPainter
from PyQt6.QtWidgets import (QApplication, QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
                             QListWidget)

APP = QApplication.instance() or QApplication([])
import echo_glass  # noqa: E402
import native_ui  # noqa: E402
import ui_anim  # noqa: E402


def pump(sec=0.05):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        APP.processEvents()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
        time.sleep(0.002)


def same(a, b):
    a = a.toImage().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    b = b.toImage().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    if a.size() != b.size():
        return f'size {a.size()} != {b.size()}'
    bad = sum(1 for y in range(a.height()) for x in range(a.width()) if a.pixel(x, y) != b.pixel(x, y))
    return bad


class Backdrop(QWidget):
    """Custom-painted ancestor (like AmbientFrame): the snapshot must include it under the page."""
    def paintEvent(self, _):
        p = QPainter(self)
        g = QLinearGradient(0, 0, self.width(), self.height())
        g.setColorAt(0, QColor('#1b2a44')); g.setColorAt(1, QColor('#3a0f22'))
        p.fillRect(self.rect(), g)
        p.end()


class NativeGlass(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not native_ui.available():
            raise unittest.SkipTest('echoes_ui.dll unavailable: ' + native_ui.error())

    def test_liquid_surface_matches_python(self):
        for w, h, radius in ((1920, 1040, 9), (1600, 900, 22), (250, 900, 9), (1400, 96, 9), (37, 23, 4),
                             (3, 2, 9), (2560, 1400, 16)):
            with self.subTest(size=(w, h), radius=radius):
                native = echo_glass._liquid_surface(QSize(w, h), radius)
                with patch.object(native_ui, 'available', return_value=False):
                    python = echo_glass._liquid_surface(QSize(w, h), radius)
                self.assertEqual(same(python, native), 0)

    def test_background_surface_matches_and_repaints_waiters(self):
        size, radius = QSize(1333, 777), 9
        echo_glass._SURF_CACHE.pop((1333, 777, 9), None)

        class Waiter(QWidget):
            updates = 0
            def update(self):
                self.updates += 1
        w = Waiter()
        self.assertFalse(echo_glass.surface_ready(size, radius, w))
        self.assertFalse(echo_glass.surface_ready(size, radius, w))   # повторный запрос — тот же заказ
        deadline = time.perf_counter() + 5
        while (1333, 777, 9) not in echo_glass._SURF_CACHE and time.perf_counter() < deadline:
            pump(0.01)
        self.assertTrue(echo_glass.surface_ready(size, radius, w))
        self.assertEqual(w.updates, 1)
        with patch.object(native_ui, 'available', return_value=False):
            python = echo_glass._liquid_surface(size, radius)
        self.assertEqual(same(python, echo_glass._SURF_CACHE[(1333, 777, 9)]), 0)

    def page(self):
        root = Backdrop(); root.resize(900, 600)
        lay = QVBoxLayout(root); lay.setContentsMargins(30, 40, 20, 10)
        stack = QStackedWidget(); lay.addWidget(stack)
        a = QWidget(); al = QVBoxLayout(a)
        al.addWidget(QLabel('Коллекция — длинная подпись, текст и кнопки'))
        b = QPushButton('Кнопка'); b.setStyleSheet('QPushButton{background:#ffd60a;border-radius:12px;padding:8px}')
        al.addWidget(b)
        lst = QListWidget(); lst.addItems([f'Трек {i}' for i in range(40)]); al.addWidget(lst)
        stack.addWidget(a); stack.addWidget(QWidget())
        root.show(); pump(0.2)
        return root, stack

    def test_window_grab_matches_render(self):
        # The window copy is exactly what is on screen. Re-rendering the page
        # region (_grab_area) differs from the screen itself by gradient
        # rounding only (<=2 levels where the ancestor gradient is drawn
        # through a clipped region), never by content.
        root, stack = self.page()
        native = native_ui.grab_from_window(stack)
        self.assertIsNotNone(native)
        a = ui_anim._grab_area(stack).toImage().convertToFormat(QImage.Format.Format_RGB32)
        b = native.toImage().convertToFormat(QImage.Format.Format_RGB32)
        self.assertEqual(a.size(), b.size())
        worst = max(max(abs(((a.pixel(x, y) >> s) & 255) - ((b.pixel(x, y) >> s) & 255)) for s in (0, 8, 16))
                    for y in range(0, a.height()) for x in range(0, a.width()))
        self.assertLessEqual(worst, 2)
        root.close(); root.deleteLater(); pump()

    def test_switch_uses_window_copy_and_finishes(self):
        root, stack = self.page()
        with patch.object(ui_anim, '_grab_area', wraps=ui_anim._grab_area) as render:
            ui_anim.switch(stack, stack.widget(1), 1)
            ov = ui_anim._OVERLAYS.get(id(stack))
            self.assertIsNotNone(ov)
            self.assertEqual(render.call_count, 0)         # уходящая страница — копия, без отрисовки
            pump(0.6)
        self.assertNotIn(id(stack), ui_anim._OVERLAYS)
        self.assertIs(stack.currentWidget(), stack.widget(1))
        root.close(); root.deleteLater(); pump()

    def test_hidden_widget_falls_back(self):
        w = QWidget(); w.resize(100, 100)
        self.assertIsNone(native_ui.grab_from_window(w))


if __name__ == '__main__':
    unittest.main(verbosity=2)
