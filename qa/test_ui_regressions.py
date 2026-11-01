"""Offline regressions for the button cache and failed recommendations refresh."""
import os
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from PyQt6.QtCore import qInstallMessageHandler, QSize
from PyQt6.QtGui import QIcon, QPixmap, QColor
from PyQt6.QtWidgets import QApplication, QPushButton
import paint_cache

app = QApplication.instance() or QApplication([])
app.setStyle("Fusion")


class UiRegressions(unittest.TestCase):
    def test_icon_height_invalidates_cache(self):
        button = QPushButton("")
        button.resize(64, 44)
        icon = QPixmap(24, 24)
        icon.fill(QColor("#ffdb1a"))
        button.setIcon(QIcon(icon))
        button.setIconSize(QSize(24, 24))
        filt = paint_cache._ButtonCache()
        app.installEventFilter(filt)
        try:
            first = button.grab().toImage()
            button.setIconSize(QSize(24, 12))
            self.assertNotEqual(first, button.grab().toImage())
        finally:
            app.removeEventFilter(filt)

    def test_button_cache_draws_text_without_recursive_paint(self):
        messages = []
        old_handler = qInstallMessageHandler(lambda kind, context, msg: messages.append(msg))
        filt = paint_cache._ButtonCache()
        app.installEventFilter(filt)
        button = QPushButton("Audit text")
        button.resize(180, 44)
        button.setStyleSheet("QPushButton {background:#101318;color:white;border:1px solid #888;border-radius:12px;}")
        try:
            first = button.grab().toImage()
            self.assertIn("_echoes_pc", button.__dict__)
            self.assertEqual(first, button.grab().toImage())
            button.setText("Updated")
            self.assertNotEqual(first, button.grab().toImage())
            button.setCheckable(True)
            button.setChecked(True)
            button.grab()
            self.assertTrue(any("Updated" == key for key in button._echoes_pc[0]))
            self.assertFalse(any("Recursive repaint" in m or "Painter not active" in m for m in messages), messages)
        finally:
            app.removeEventFilter(filt)
            qInstallMessageHandler(old_handler)

    def test_button_cache_matches_native_style(self):
        button = QPushButton("Native text")
        button.resize(180, 44)
        button.setStyleSheet("QPushButton {background:#101318;color:white;border:1px solid #888;border-radius:12px;}")
        # Keep hover/focus identical; only interception differs.
        expected = button.grab().toImage()
        filt = paint_cache._ButtonCache()
        app.installEventFilter(filt)
        try:
            actual = button.grab().toImage()
            self.assertEqual(expected.size(), actual.size())
            # A transparent intermediate pixmap adds one rounding step at the
            # antialiased border. Text, fills and geometry must otherwise match.
            largest = 0
            for y in range(expected.height()):
                for x in range(expected.width()):
                    a, b = expected.pixelColor(x, y).getRgb(), actual.pixelColor(x, y).getRgb()
                    largest = max(largest, max(abs(u-v) for u, v in zip(a, b)))
            self.assertLessEqual(largest, 2)
        finally:
            app.removeEventFilter(filt)

    def test_failed_refresh_cannot_restart_on_rebuild(self):
        from yamusic_theme import YaShell, Page
        import ya_foryou as F

        page = Page()
        shell = NS(win=NS(_ui_bridge=NS(call=NS(emit=lambda fn: fn()))), _cur_page="foryou", _dirty=set())
        taste = {"now": [], "top": [], "genres": [], "owned": set(), "owned_artists": set()}
        shell._rebuild = lambda key: YaShell._foryou_net(shell, page)

        class InlineThread:
            def __init__(self, target, **kwargs):
                self.target = target
            def start(self):
                self.target()

        with patch.object(F, "taste", return_value=taste), patch.object(F, "load_cache", return_value={}), \
             patch.object(F, "stale", return_value=(True, True)), patch.object(F, "refresh") as refresh, \
             patch("threading.Thread", InlineThread), patch("yamusic_theme.time.monotonic", return_value=100):
            YaShell._foryou_net(shell, page)
            YaShell._foryou_net(shell, page)
            self.assertEqual(refresh.call_count, 1)
            self.assertFalse(shell._fy_busy)
            with patch("yamusic_theme.time.monotonic", return_value=161):
                YaShell._foryou_net(shell, page)
            self.assertEqual(refresh.call_count, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
