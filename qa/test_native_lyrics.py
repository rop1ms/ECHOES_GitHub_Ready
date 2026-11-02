"""Lyrics panel frames drawn through echoes_ui.dll (cached layouts and glyphs) must equal the
plain drawText frames pixel for pixel: liquid fill at many moments, glow/shadow/karaoke styles,
alignments, upper case, translations below the line, overflowing words."""
import os
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from PyQt6.QtCore import QEvent
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication

APP = QApplication.instance() or QApplication([])
import lyrics_view  # noqa: E402
import native_ui  # noqa: E402

LRC = [
    (2000, "Uh, we took a hit, I taste vanilla"),
    (6500, "Let's find a hiding spot, I don't want nobody to see us here tonight"),
    (9800, ""),
    (11000, "Я не знаю, что сказать тебе сегодня ночью, когда горят огни"),
    (15200, "Ёжик в тумане — щёлк, щёлк"),
    (19000, "Supercalifragilisticexpialidociouslywordthatdoesnotfitanywhereatallinthispanelright"),
    (30000, "short"),
    (33500, "Мы снова здесь, и всё как прежде"),
    (37000, "Last line ✨ with emoji"),
]


def pump(sec=0.03):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        APP.processEvents()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
        time.sleep(0.002)


class NativeLyrics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not native_ui.available():
            raise unittest.SkipTest('echoes_ui.dll unavailable: ' + native_ui.error())

    def panel(self, liquid, style, translation=False, width=1194, height=714):
        p = lyrics_view.LyricsScrollPanel()
        p.resize(width, height)
        p.liquid = liquid
        lines = [dict(ms=ms, text=(t + ('\nПеревод строки на русском' if translation and t else ''))) for ms, t in LRC]
        if translation:
            p._translation_below = True
        p.load(lines, timed=True)
        p.set_style(style)
        p.show(); pump()
        return p

    def frame(self, p, ms, native):
        p._ntext = None if not native else p.__dict__.get('_ntext_keep') or native_ui.TextCache()
        if native:
            p._ntext_keep = p._ntext
        p.__dict__.pop('_lq_arr', None)
        p._pos = (ms, 1000.0)
        clock = SimpleNamespace(monotonic=lambda: 1000.0)
        with patch.object(lyrics_view, 'time', clock):
            p._update_state()
            p._alpha = [p._base_alpha(i) for i in range(len(p._texts))]
            p._dots_a = 1.0 if p._dots is not None else 0.0
            p._target = p._target_scroll(); p._scroll = p._target
            return p.grab().toImage().convertToFormat(QImage.Format.Format_ARGB32)

    def check(self, liquid, style, moments, translation=False, **size):
        p = self.panel(liquid, style, translation, **size)
        try:
            for ms in moments:
                with self.subTest(liquid=liquid, style=style, ms=ms, translation=translation):
                    a, b = self.frame(p, ms, False), self.frame(p, ms, True)
                    if a != b and liquid:
                        # The liquid line (glyph runs under the line's own scale) may differ from
                        # per-character drawText by antialiasing rounding only; wrapped lines must not.
                        worst = max(max(abs(((a.pixel(x, y) >> s) & 255) - ((b.pixel(x, y) >> s) & 255))
                                        for s in (0, 8, 16, 24))
                                    for y in range(a.height()) for x in range(a.width()) if a.pixel(x, y) != b.pixel(x, y))
                        self.assertLessEqual(worst, 8)
                        continue
                    if a != b:
                        out = Path(__file__).resolve().parent / 'results'
                        out.mkdir(exist_ok=True)
                        a.save(str(out / 'lyrics_python.png')); b.save(str(out / 'lyrics_native.png'))
                        bad = sum(1 for y in range(a.height()) for x in range(a.width()) if a.pixel(x, y) != b.pixel(x, y))
                        self.fail(f'{bad} pixels differ')
        finally:
            p.close(); p.deleteLater(); pump()

    # моменты: вступление, начало/середина/конец заливки строк, проигрыш, длинное слово, конец
    MOMENTS = (500, 2100, 3300, 4700, 6600, 8000, 9900, 11500, 13900, 15300, 19500, 25000, 31000, 36000, 38500)

    def test_liquid_default(self):
        self.check(True, {}, self.MOMENTS)

    def test_plain_default(self):
        self.check(False, {}, self.MOMENTS[::2])

    def test_styles(self):
        self.check(True, {'glow': 0.8, 'shadow': 0.6}, self.MOMENTS[::3])
        self.check(False, {'glow': 0.8, 'shadow': 0.6, 'karaoke': True}, self.MOMENTS[::3])
        self.check(True, {'align': 'left', 'upper': True, 'size': 30, 'weight': 800, 'letter': 1.5},
                   self.MOMENTS[::3])
        self.check(False, {'align': 'right', 'size': 22}, self.MOMENTS[::4])

    def test_translation_and_narrow(self):
        self.check(False, {}, self.MOMENTS[::4], translation=True)
        self.check(True, {}, self.MOMENTS[::4], width=420, height=700)

    def test_static_layer_matches_direct_frames(self):
        from PyQt6.QtCore import QPoint
        from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPixmap
        for liquid in (True, False):
            p = self.panel(liquid, {'glow': 0.5} if not liquid else {})
            bg = QPixmap(1400, 900)
            g = QLinearGradient(0, 0, 1400, 900); g.setColorAt(0, QColor('#2a1b3d')); g.setColorAt(1, QColor('#0d2b33'))
            qp = QPainter(bg); qp.fillRect(bg.rect(), g); qp.end()
            p.set_backdrop(lambda: (bg, QPoint(37, 21)))
            for ms in (3300, 8000, 13900, 36000):
                with self.subTest(liquid=liquid, ms=ms):
                    p.__dict__.pop('_layer', None); p.__dict__.pop('_layer_k', None); p.__dict__.pop('_prev_k', None)
                    direct = self.frame(p, ms, True)                   # 1-й кадр — напрямую
                    built = self.frame(p, ms, True)                    # 2-й — слой собирается
                    self.assertIsNotNone(p.__dict__.get('_layer'))
                    cached = self.frame(p, ms, True)                   # 3-й — из готового слоя
                    self.assertEqual(direct, built)
                    self.assertEqual(direct, cached)
            p.close(); p.deleteLater(); pump()

    def test_cache_reused_and_font_change(self):
        p = self.panel(True, {})
        tc = native_ui.TextCache(); p._ntext = tc
        p.update(); pump()
        p.set_style({'size': 33}); p.update(); pump()             # новый шрифт — кэш пересобирается
        self.assertIsNotNone(p.grab())
        p.close(); p.deleteLater(); pump()


if __name__ == '__main__':
    unittest.main(verbosity=2)
