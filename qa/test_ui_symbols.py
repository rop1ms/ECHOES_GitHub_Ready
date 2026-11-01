"""Offline coverage for drawn UI glyphs, translation and preserved reactions."""
from pathlib import Path
import os
import sys
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter, QTextLayout
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget
import i18n
import ui_symbols as S


class SymbolsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        # Windows' offscreen platform does not enumerate system fonts itself.
        for font in ("arial.ttf", "segoeui.ttf", "seguisym.ttf", "consola.ttf", "times.ttf"):
            QFontDatabase.addApplicationFont(str(Path("C:/Windows/Fonts") / font))
        cls.app.setFont(QFont("Segoe UI", 10))
        i18n.install("en")
        i18n.start(cls.app)
        cls.app.processEvents()

    def test_every_drawing_has_font_glyph_in_normal_text_families(self):
        for family in ("YS Text", "Inter", "Segoe UI Variable Display", "Segoe UI", "Helvetica Neue",
                       "Arial", "Consolas", "Times New Roman", "Unbounded"):
            for name in S.NAMES:
                with self.subTest(family=family, name=name):
                    layout = QTextLayout(S.symbol(name) + " ECHOES", QFont(family, 18))
                    layout.beginLayout()
                    line = layout.createLine()
                    line.setLineWidth(1000)
                    layout.endLayout()
                    runs = layout.glyphRuns()
                    self.assertTrue(any(r.rawFont().familyName() == S.FAMILY for r in runs))
                    self.assertTrue(all(all(r.glyphIndexes()) for r in runs))

    def test_translation_preserves_drawings_and_user_emoji(self):
        self.assertEqual(i18n.tr(S.symbol("game") + " Играет в Echoes 🎁"),
                         S.symbol("game") + " Playing Echoes 🎁")
        self.assertEqual(i18n.tr("hello 🔥 🎧 🎁"), "hello 🔥 🎧 🎁")
        self.assertEqual(i18n.tr("🎧"), "🎧")
        self.assertEqual(i18n.tr("❤️"), "❤️")
        self.assertEqual(i18n.tr(S.symbol("gift") + "  Подарок другу"),
                         S.symbol("gift") + "  Gift to a friend")

    def test_content_labels_and_reaction_values_are_unchanged(self):
        label = QLabel("hello 🔥 🎧 🎁")
        label.show()
        self.app.processEvents()
        self.assertEqual(i18n._RAW["label"](label), "hello 🔥 🎧 🎁")
        import net_core
        self.assertEqual(net_core.EMOJI, ("🔥", "🎧", "😭", "💀", "🤯", "❤️"))
        label.close()

    def test_new_constructor_labels_translate_without_periodic_sweep(self):
        label = QLabel("Настройки")
        label.show()
        self.app.processEvents()
        self.assertEqual(i18n._RAW["label"](label), "Settings")
        self.assertEqual(label.text(), "Настройки")
        self.assertFalse(hasattr(self.app, "_i18n_timer"))
        self.assertTrue(hasattr(self.app, "_i18n_guard"))
        label.close()

    def test_button_cache_uses_english_display_text(self):
        import paint_cache
        button = QPushButton()
        button.setText("Моя волна")
        self.assertEqual(button.text(), "Моя волна")
        self.assertEqual(i18n.display_text(button), "My Wave")
        self.assertEqual(paint_cache._key(button)[3], "My Wave")
        # Exercise the actual style draw, recording exactly what is painted.
        paint_cache.install()
        button.resize(180, 40)
        button.show()
        self.app.processEvents()
        cached = button.__dict__.get("_echoes_pc")
        self.assertIsNotNone(cached)
        self.assertEqual(cached[0][3], "My Wave")
        button.close()

    def test_real_chat_bubble_preserves_native_content_and_visible_pixels(self):
        from net_page import NetPage
        samples = ("Message 4: emoji content remains 🔥 🎧", "English message without emoji",
                   "Пользовательский текст 🔥 🎧")
        for direction in ("in", "out"):
            for message in samples:
                with self.subTest(direction=direction, message=message):
                    host = QWidget()
                    host.setObjectName("ChatHarness")
                    host.setStyleSheet("QWidget#ChatHarness{background:#151515;}QLabel{background:transparent;}")
                    layout = QVBoxLayout(host)
                    row = NetPage._bubble(object(), {"dir": direction, "peer": "friend", "text": message,
                                                    "ts": time.time(), "delivered": True})
                    layout.addWidget(row)
                    host.resize(520, 160)
                    host.show()
                    self.app.processEvents()
                    labels = [w for w in row.findChildren(QLabel) if i18n.display_text(w) == message]
                    self.assertEqual(len(labels), 1)
                    content = labels[0]
                    self.assertEqual(content.text(), message)
                    # Exercise the retained-bubble path: existing content is moved
                    # back into a refreshed layout, rather than reconstructed.
                    layout.takeAt(0)
                    layout.addWidget(row)
                    self.app.processEvents()
                    self.assertEqual(i18n.display_text(content), message)
                    content.setFixedSize(content.size())
                    painted = content.grab().toImage()
                    content.clear()
                    self.app.processEvents()
                    blank = content.grab().toImage()
                    # Labels have transparent backgrounds: alpha is essential
                    # when detecting dark outgoing text before compositing.
                    changed = sum(
                        sum(abs(a - b) for a, b in zip(painted.pixelColor(x, y).getRgb(),
                                                       blank.pixelColor(x, y).getRgb())) > 60
                        for y in range(painted.height()) for x in range(painted.width()))
                    self.assertGreater(changed, 50, "Message text exists but contributes no visible pixels")
                    host.close()

    def test_icon_artifact_and_sheet(self):
        rows = (len(S.NAMES) + 7) // 8
        sheet = QImage(560, rows * 80, QImage.Format.Format_ARGB32_Premultiplied)
        sheet.fill(QColor("#171a22"))
        p = QPainter(sheet)
        for i, name in enumerate(S.NAMES):
            icon = S.icon(name, 34, "#e5e9f4")
            self.assertFalse(icon.isNull())
            icon.paint(p, (i % 8) * 70 + 18, (i // 8) * 80 + 10, 34, 34)
            p.setPen(QColor("#b7c1d6"))
            p.drawText((i % 8) * 70 + 4, (i // 8) * 80 + 62, name)
        p.end()
        out = ROOT / "qa" / "results" / "ui_symbols.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        self.assertTrue(sheet.save(str(out)))
        # Inspect actual font fallback too, rather than only the QIcon paths.
        sheet.fill(QColor("#171a22"))
        p = QPainter(sheet)
        p.setPen(QColor("#e5e9f4"))
        font = QFont("Segoe UI")
        font.setPixelSize(34)
        p.setFont(font)
        for i, name in enumerate(S.NAMES):
            p.drawText((i % 8) * 70 + 18, (i // 8) * 80 + 42, S.symbol(name))
        p.end()
        self.assertTrue(sheet.save(str(out.with_name("ui_symbols_font.png"))))


if __name__ == "__main__":
    unittest.main()
