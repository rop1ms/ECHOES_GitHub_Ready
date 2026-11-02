"""C++ card rows (echoes_ui.dll) must paint exactly like HRow + Card, pixel for pixel.

Same cards go into the Python HRow and into NativeCardRow; both are grabbed after the
appearance cascade and cover loading settle, normal and hovered, at DPR 1 and 2."""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QColor, QEnterEvent, QImage, QMouseEvent
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout

APP = QApplication.instance() or QApplication([])
import native_ui  # noqa: E402
import ya_decor  # noqa: E402
import yamusic_theme as Y  # noqa: E402


def pump(sec=0.05):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        APP.processEvents()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
        time.sleep(0.002)


def cover_files(tmp):
    """Real-looking covers: gradients with detail, so scaling/clipping differences would show."""
    out = []
    for i, (a, b) in enumerate((('#ff3b6b', '#2a0a5e'), ('#00d4ff', '#0b3d1a'), ('#ffd60a', '#3d0b0b'))):
        img = QImage(600, 600, QImage.Format.Format_RGB32)
        from PyQt6.QtGui import QLinearGradient, QPainter
        p = QPainter(img)
        g = QLinearGradient(0, 0, 600, 600); g.setColorAt(0, QColor(a)); g.setColorAt(1, QColor(b))
        p.fillRect(img.rect(), g)
        p.setPen(QColor('white'))
        for k in range(0, 600, 37):
            p.drawLine(k, 0, 600 - k, 600)
        p.end()
        path = os.path.join(tmp, f'cover{i}.jpg')
        img.save(path, quality=92)
        out.append(path)
    return out


SPECS = []


def specs(covers):
    long = 'Очень длинное название трека, которое точно не поместится в карточку'
    return [
        dict(title='Моя волна', subtitle='Всё, что вы любите', size=150,
             mix=(QColor('#ffd60a'), QColor('#e0218a')), mix_covers=covers),
        dict(title='Настроение дня', subtitle='Время суток и погода', size=150,
             mix=(QColor('#ff8a00'), QColor('#7a2cff'))),
        dict(title='Энергия', subtitle='Под настроение', size=150,
             mix=(QColor.fromHsvF(0.02, 0.85, 1.0), QColor.fromHsvF(0.14, 0.9, 0.7))),
        dict(title=long, subtitle='Исполнитель · похож на кого-то очень длинного', cover=covers[0], size=150, seed='a'),
        dict(title='Без обложки', subtitle='Артист', cover='', size=150, seed='Артист|Без обложки'),
        dict(title='Исполнитель', subtitle='42 трека', cover=covers[1], size=130, circle=True),
        dict(title='Круг без фото', subtitle='похож на X и Y', cover='', size=130, circle=True),
        dict(title='Плейлист', subtitle='', cover=covers[2], size=150, seed='pl'),
    ]


class NativeCardStrip(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not native_ui.available():
            raise unittest.SkipTest('echoes_ui.dll unavailable: ' + native_ui.error())
        cls.tmp = tempfile.TemporaryDirectory()
        cls.covers = cover_files(cls.tmp.name)

    def build(self, native, cfg):
        ya_decor._CFG.update(cfg)
        host = QWidget(); host.resize(1500, 260)
        lay = QVBoxLayout(host); lay.setContentsMargins(0, 0, 0, 0)
        row = Y.NativeCardRow() if native else Y.HRow()
        for spec in specs(self.covers):
            spec = dict(spec)
            row.card(spec.pop('title'), **spec)
        lay.addWidget(row.widget_); lay.addStretch(1)
        host.setStyleSheet(Y.ya_theme_qss() if hasattr(Y, 'ya_theme_qss') else '')
        host.show()
        deadline = time.perf_counter() + 3
        while time.perf_counter() < deadline and Y._LOADER.waiting:
            pump(0.02)
        pump(0.6)                                          # каскад (≤0.3 с задержки + 0.22 с)
        return host, row

    def cards_of(self, row, native):
        if native:
            return [(row.strip.card_pos(i), it.size_) for i, it in enumerate(row.items)]
        return [((c.x(), c.y()), c.size_) for c in row.inner.findChildren(Y.Card)]

    def hover(self, row, native, i):
        if native:
            canvas = row.widget_.widget()
            (x, y), s = self.cards_of(row, True)[i]
            pos = QPointF(x + s / 2, y + s / 2)
            QApplication.sendEvent(canvas, QMouseEvent(QEvent.Type.MouseMove, pos, canvas.mapToGlobal(pos),
                                                       Qt.MouseButton.NoButton, Qt.MouseButton.NoButton,
                                                       Qt.KeyboardModifier.NoModifier))
        else:
            cards = row.inner.findChildren(Y.Card)
            for card in cards:                             # как настоящая мышь: с прежней карточки — уход
                if card._hover:
                    QApplication.sendEvent(card, QEvent(QEvent.Type.Leave))
            QApplication.sendEvent(cards[i], QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
        pump(0.05)

    def compare(self, cfg, hover_index=None):
        images = []
        for native in (False, True):
            host, row = self.build(native, cfg)
            if hover_index is not None:
                self.hover(row, native, hover_index)
            images.append(row.widget_.grab().toImage().convertToFormat(QImage.Format.Format_ARGB32))
            self.assertEqual(len(self.cards_of(row, native)), len(specs(self.covers)))
            host.close(); host.deleteLater(); pump(0.05)
        py, cc = images
        self.assertEqual(py.size(), cc.size())
        diff = [(x, y) for y in range(py.height()) for x in range(py.width()) if py.pixel(x, y) != cc.pixel(x, y)]
        if diff:
            out = Path(__file__).resolve().parent / 'results'
            out.mkdir(exist_ok=True)
            py.save(str(out / 'card_strip_python.png')); cc.save(str(out / 'card_strip_native.png'))
        self.assertEqual(diff[:5], [], f'{len(diff)} pixels differ (cfg={cfg}, hover={hover_index})')

    def test_plain(self):
        self.compare(dict(card_glow=False, mix_covers=False, cascade=False))

    def test_mix_covers_and_cascade(self):
        self.compare(dict(card_glow=False, mix_covers=True, cascade=True))

    def test_hover_every_kind(self):
        for i in (0, 3, 4, 5, 7):
            with self.subTest(card=i):
                self.compare(dict(card_glow=False, mix_covers=True, cascade=False), hover_index=i)

    def test_hover_glow(self):
        for i in (0, 3, 5):
            with self.subTest(card=i):
                self.compare(dict(card_glow=True, mix_covers=True, cascade=False), hover_index=i)

    def test_click_and_flight_source(self):
        ya_decor._CFG.update(dict(cascade=False))
        host, row = self.build(True, {})
        hits = []
        row.items[3].clicked.connect(lambda: hits.append(3))
        canvas = row.widget_.widget()
        (x, y), s = self.cards_of(row, True)[3]
        pos = QPointF(x + 10, y + 10)
        for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
            QApplication.sendEvent(canvas, QMouseEvent(kind, pos, canvas.mapToGlobal(pos), Qt.MouseButton.LeftButton,
                                                       Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        self.assertEqual(hits, [3])
        self.assertIs(ya_decor._LAST_CARD['ref'](), row.items[3])
        self.assertFalse(row.items[3]._cbuf.isNull())
        host.close(); host.deleteLater(); pump(0.05)

    def test_deleted_row_ignores_late_cover(self):
        host, row = self.build(True, {})
        item = row.items[3]
        host.deleteLater(); pump(0.05)
        self.assertFalse(row.strip.alive)
        item.update()                                      # поздний ответ _CoverLoader — без падения


if __name__ == '__main__':
    unittest.main(verbosity=2)
