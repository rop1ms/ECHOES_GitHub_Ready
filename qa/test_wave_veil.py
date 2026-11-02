"""My Wave veil (wave_veil.py): the void swallows the panels around My Wave, only symbols stay.

- the veil turns a glass panel black but never the My Wave content panel;
- buttons in the panels lose their backgrounds and get white icons; leaving restores them exactly;
- decorations (section heading, artist card) fade and hide, and come back when leaving;
- leaving returns the panels exactly to their normal look."""
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from PyQt6.QtCore import QPoint
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

APP = QApplication.instance() or QApplication([])
import wave_veil  # noqa: E402
import yamusic_theme as yt  # noqa: E402


def pump(sec=0.05):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        APP.processEvents()
        time.sleep(0.003)


def brightness(img: QImage) -> float:
    img = img.convertToFormat(QImage.Format.Format_ARGB32)
    tot = n = 0
    for y in range(4, img.height() - 4, 6):
        for x in range(4, img.width() - 4, 6):
            c = img.pixelColor(x, y)
            tot += c.red() + c.green() + c.blue()
            n += 1
    return tot / max(1, n)


class Shell(QWidget):
    def __init__(self):
        super().__init__()
        self.resize(900, 500)
        lay = QHBoxLayout(self)
        self.side = yt.AmbientFrame()
        self.side.setObjectName('YaSideNav')
        sv = QVBoxLayout(self.side)
        self._side_head = QLabel('РАЗДЕЛЫ')
        sv.addWidget(self._side_head)
        self.btn = QPushButton('Моя волна')
        self.btn.setStyleSheet('QPushButton{background:#8a5cff;color:#101010;}')
        from PyQt6.QtGui import QIcon, QPixmap
        pm = QPixmap(20, 20)
        pm.fill(QColor(20, 20, 20))                             # dark icon — would vanish on black
        self.btn.setIcon(QIcon(pm))
        sv.addWidget(self.btn)
        self.artist_card = QLabel('artist card')
        sv.addWidget(self.artist_card)
        self.content = yt.AmbientFrame()
        self.content.setObjectName('YaContent')
        lay.addWidget(self.content, 3)
        lay.addWidget(self.side, 1)


class WaveVeil(unittest.TestCase):
    def setUp(self):
        p = patch.object(wave_veil, 'IN_MS', 120)
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(wave_veil, 'OUT_MS', 120)
        p.start()
        self.addCleanup(p.stop)
        self.shell = Shell()
        self.shell.show()
        pump(0.3)
        self.veil = wave_veil.install(self.shell)
        self.addCleanup(lambda: setattr(wave_veil, '_VEIL', None))

    def grab(self, w):
        return w.grab().toImage()

    def test_dark_in_and_back(self):
        side0, content0 = brightness(self.grab(self.shell.side)), brightness(self.grab(self.shell.content))
        self.veil.set_on(True)
        pump(0.5)
        self.assertAlmostEqual(wave_veil.level(), 1.0, places=2)
        self.assertLess(brightness(self.grab(self.shell.side)), side0 * 0.6, 'panel swallowed by black')
        self.assertTrue(wave_veil.void())
        self.assertIn('transparent', self.shell.btn.styleSheet())
        ic = self.shell.btn.icon().pixmap(20, 20).toImage()
        self.assertEqual(ic.pixelColor(10, 10).lightness(), 255, 'dark symbol turns white')
        self.assertAlmostEqual(brightness(self.grab(self.shell.content)), content0, delta=1.0)
        self.assertTrue(self.shell.btn.isVisible(), 'buttons stay')
        self.assertFalse(self.shell._side_head.isVisible())
        self.assertFalse(self.shell.artist_card.isVisible())
        self.veil.set_on(False)
        self.assertFalse(wave_veil.void())
        self.assertEqual(self.shell.btn.styleSheet(), 'QPushButton{background:#8a5cff;color:#101010;}')
        self.assertLess(self.shell.btn.icon().pixmap(20, 20).toImage().pixelColor(10, 10).lightness(), 40)
        pump(0.5)
        self.assertEqual(wave_veil.level(), 0.0)
        self.assertTrue(self.shell._side_head.isVisible())
        self.assertTrue(self.shell.artist_card.isVisible())
        self.assertIsNone(self.shell.artist_card.graphicsEffect())
        self.assertAlmostEqual(brightness(self.grab(self.shell.side)), side0, delta=1.0)


if __name__ == '__main__':
    unittest.main()
