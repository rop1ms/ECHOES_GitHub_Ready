"""Wheel scrolling on the GPU (gpu_scroll + wheel_guard) must show exactly what the raster page shows.

The GL layer is replaced by a software one that composes the same way as scroll_gl.cpp (backdrop
copied as is, tiles over it, offset rounded to device pixels). For a page (QScrollArea with mixed
widgets) and the track list (TrackListView rows), the composed frame at a scroll position is compared
with the window grabbed after the session puts the real page at that position. Also checked: the page
does not move during the session, ends at the target, and its updates are re-enabled."""
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')
os.environ.pop('ECHOES_NO_GPU_SCROLL', None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPixmap, QWheelEvent
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget

APP = QApplication.instance() or QApplication([])
import gpu_scroll  # noqa: E402
import native_ui  # noqa: E402
import wheel_guard  # noqa: E402
import yamusic_theme as yt  # noqa: E402


def pump(sec=0.05):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        APP.processEvents()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
        time.sleep(0.002)


class FakeLayer:
    """scroll_gl.cpp in software: backdrop, tiles, offset."""
    def __init__(self):
        self.widget = QWidget()
        self.back, self.tiles, self.off, self.tile_h = None, {}, 0.0, 256

    def ok(self):
        return True

    def backdrop(self, img):
        self.back, self.tiles = (QImage(img) if img is not None else None), {}

    def tile(self, i, img):
        self.tiles[i] = QImage(img)

    def drop(self, i):
        self.tiles.pop(i, None)

    def release(self):
        self.back, self.tiles = None, {}

    def frame(self, off, tile_h):
        self.off, self.tile_h = off, tile_h

    def compose(self):
        img = QImage(self.back)
        img.setDevicePixelRatio(1.0)
        p = QPainter(img)
        dpr = self.back.devicePixelRatio()
        off = round(self.off * dpr)
        for i, t in self.tiles.items():
            p.drawImage(QPoint(0, int(i * self.tile_h * dpr - off)), t)
        p.end()
        return img


class Anchor:
    def ok(self):
        return True


class AmbientFrame(QWidget):
    """Glass panel contract used by gpu_scroll: rounded clip + its cached surface (fixed here, the real
    one is built asynchronously)."""
    def resizeEvent(self, e):
        pm = QPixmap(self.size())
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        g = QLinearGradient(0, 0, self.width(), self.height())
        g.setColorAt(0, QColor('#141820')); g.setColorAt(0.5, QColor('#2b1630')); g.setColorAt(1, QColor('#0c1a14'))
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 18, 18)
        p.fillPath(path, g)
        p.end()
        self._surface = pm

    def paintEvent(self, e):
        p = QPainter(self)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 18, 18)
        p.setClipPath(path)
        p.drawPixmap(0, 0, self._surface)
        p.end()


class Shell:
    win = None

    def is_current(self, t):
        return False

    def is_playing(self):
        return False

    def is_liked(self, t):
        return t.get('liked', False)


def diff(a, b, mask):
    """Pixels that differ, counted only where the backdrop is opaque (rounded panel corners show the window)."""
    a = a.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    b = b.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    if a.size() != b.size():
        return f'size {a.size()} != {b.size()}'
    bad = 0
    for y in range(0, a.height()):
        for x in range(0, a.width(), 2):
            if mask.pixelColor(x, y).alpha() == 255 and a.pixel(x, y) != b.pixel(x, y):
                bad += 1
    return bad


class GpuScroll(unittest.TestCase):
    def setUp(self):
        import ya_decor
        p = patch.object(ya_decor, 'CURRENT', None)
        p.start()
        self.addCleanup(p.stop)
        p = patch('ya_skin.interface_radius', lambda: 18)
        p.start()
        self.addCleanup(p.stop)
        self.layer = FakeLayer()
        p = patch.object(native_ui, 'ScrollGL', lambda: self.layer)
        p.start()
        self.addCleanup(p.stop)
        self.win = QWidget()
        self.win.resize(900, 640)
        self.win._gpu_anchor = Anchor()
        self.win.setStyleSheet('QListView{background:transparent;border:none;outline:none;}'   # как в теме
                               'QScrollArea{background:transparent;border:none;}')
        lay = QVBoxLayout(self.win)
        lay.setContentsMargins(12, 12, 12, 12)
        self.frame = AmbientFrame()
        self.frame.setObjectName('YaContent')
        lay.addWidget(self.frame)
        self.inner = QVBoxLayout(self.frame)
        self.inner.setContentsMargins(0, 0, 0, 0)

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        pump(0.02)

    def run_session(self, area, positions):
        bar = area.verticalScrollBar()
        sess = gpu_scroll.begin(area, bar)
        self.assertIsNotNone(sess, 'GPU session should start')
        self.assertFalse(sess.live, 'first frames stay raster while visible tiles are prepared')
        self.assertFalse(self.layer.widget.isVisible())
        for _ in range(50):                                    # visible tiles, a few ms per frame
            if sess.live:
                break
            sess.frame(float(bar.value()))
        self.assertTrue(sess.live, 'GPU takes over once visible tiles are ready')
        before = area.widget().pos() if hasattr(area, 'widget') and area.widget() is not None else None
        for pos in positions:
            bar.blockSignals(True)
            bar.setValue(int(round(pos)))
            bar.blockSignals(False)
            sess.frame(pos)
        if before is not None:
            self.assertEqual(area.widget().pos(), before, 'page must stay put under the layer')
        composed = self.layer.compose()
        mask = QImage(self.layer.back)
        sess.end()
        self.assertTrue(area.viewport().updatesEnabled())
        pump(0.15)
        vp = area.viewport()
        real = self.win.grab(QRect(vp.mapTo(self.win, QPoint(0, 0)), vp.size())).toImage()
        return composed, real, mask

    def test_page_matches_raster(self):
        page = yt.Page()
        colors = ['#ff3355', '#33aaff', '#ffd21a', '#3ddc84', '#b06cff']
        for k in range(28):
            row = QWidget()
            row.setFixedHeight(64)
            row.setStyleSheet(f'background:{colors[k % 5]}; border-radius:10px;')
            h = QVBoxLayout(row)
            h.addWidget(QLabel(f'Строка {k} — текст для проверки'))
            page.v.addWidget(row)
            if k % 7 == 3:
                page.v.addWidget(QPushButton(f'Кнопка {k}'))
        self.inner.addWidget(page)
        self.win.show()
        pump(0.3)
        bar = page.verticalScrollBar()
        bar.setValue(150)
        pump(0.1)
        composed, real, mask = self.run_session(page, [180.4, 260.0, 371.6, 512.0, 733.0])
        self.assertEqual(bar.value(), 733)
        self.assertEqual(page.widget().y(), -733)
        self.assertEqual(diff(composed, real, mask), 0)

    def test_track_list_matches_raster(self):
        lv = yt.TrackListView(Shell())
        rows = [{'t': {'title': f'Трек {k}', 'artist': f'Исполнитель {k % 9}', 'duration': 60 + k * 7,
                       'path': f'C:/m/{k}.mp3', 'cover': '', 'liked': k % 4 == 0}, 'ctx': 'library', 'i': k}
                for k in range(120)]
        lv.set_rows(rows)
        self.inner.addWidget(lv)
        self.win.show()
        pump(0.3)
        lv.verticalScrollBar().setValue(300)
        pump(0.1)
        composed, real, mask = self.run_session(lv, [330.0, 420.5, 700.0, 1111.0, 1500.0])
        self.assertEqual(lv.verticalScrollBar().value(), 1500)
        self.assertEqual(diff(composed, real, mask), 0)

    def test_page_with_track_section_matches_raster(self):
        """Queue / playlist pages: a page-height track list keeps rows only near the visible part —
        tiles further down must still show its rows."""
        page = yt.Page()
        page.v.addWidget(QLabel('Очередь'))
        lv = yt.TrackListView(Shell())
        lv.set_auto_height(True)
        lv.set_rows([{'t': {'title': f'Трек {k}', 'artist': 'Кто-то', 'duration': 100 + k, 'path': f'C:/q/{k}.mp3',
                            'cover': ''}, 'ctx': 'library', 'i': k} for k in range(90)])
        page.v.addWidget(lv.section_widget())
        self.inner.addWidget(page)
        self.win.show()
        pump(0.3)
        page.verticalScrollBar().setValue(100)
        pump(0.1)
        composed, real, mask = self.run_session(page, [200.0, 600.0, 1300.0, 2100.0])
        self.assertEqual(page.verticalScrollBar().value(), 2100)
        self.assertEqual(diff(composed, real, mask), 0)

    def test_wheel_drives_session_to_target(self):
        page = yt.Page()
        for k in range(40):
            page.v.addWidget(QLabel(f'Метка {k}'))
        self.inner.addWidget(page)
        self.win.show()
        pump(0.3)
        started = []
        orig = gpu_scroll.begin

        def spy(area, bar):
            s = orig(area, bar)
            started.append(s)
            return s
        with patch.object(gpu_scroll, 'begin', spy):
            vp = page.viewport()
            pos = QPointF(vp.width() / 2, vp.height() / 2)
            for _ in range(3):
                ev = QWheelEvent(pos, vp.mapToGlobal(pos), QPoint(0, 0), QPoint(0, -120), Qt.MouseButton.NoButton,
                                 Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
                self.assertTrue(wheel_guard.smooth_scroll(page, ev))
            pump(1.2)
        self.assertTrue(started and started[0] is not None)
        target = min(300, page.verticalScrollBar().maximum())
        self.assertEqual(page.verticalScrollBar().value(), target)
        self.assertEqual(page.widget().y(), -target, 'page must land where the scrollbar is')
        self.assertTrue(vp.updatesEnabled())
        self.assertFalse(self.layer.widget.isVisible())

    def test_transition_mid_glide_puts_page_in_place(self):
        """A page switch snapshots the window mid-glide: the page must already be where the scrollbar is."""
        page = yt.Page()
        for k in range(60):
            page.v.addWidget(QLabel(f'Метка {k}'))
        self.inner.addWidget(page)
        self.win.show()
        pump(0.3)
        vp = page.viewport()
        pos = QPointF(vp.width() / 2, vp.height() / 2)
        for _ in range(8):
            ev = QWheelEvent(pos, vp.mapToGlobal(pos), QPoint(0, 0), QPoint(0, -120), Qt.MouseButton.NoButton,
                             Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
            wheel_guard.smooth_scroll(page, ev)
        end = time.perf_counter() + 0.5
        while not self.layer.widget.isVisible() and time.perf_counter() < end:
            pump(0.005)
        pump(0.03)
        bar = page.verticalScrollBar()
        self.assertTrue(self.layer.widget.isVisible(), 'glide should be on the GPU')
        self.assertNotEqual(page.widget().y(), -bar.value())
        wheel_guard.finish_gpu()
        self.assertEqual(page.widget().y(), -bar.value())
        self.assertFalse(self.layer.widget.isVisible())
        pump(1.2)                                              # the rest of the glide — raster
        target = min(800, bar.maximum())
        self.assertEqual(bar.value(), target)
        self.assertEqual(page.widget().y(), -target)

    def test_page_with_msaa_canvas_falls_back_to_raster(self):
        """Duel page: the arena's GL canvas (4x MSAA) snapshots as garbage and animates — no GPU scroll."""
        from PyQt6.QtOpenGLWidgets import QOpenGLWidget
        page = yt.Page()
        for k in range(30):
            page.v.addWidget(QLabel(f'Метка {k}'))
        gl = QOpenGLWidget()
        gl.setProperty('echoes_live', True)                     # as music_duel.ArenaCanvas
        gl.setFixedHeight(200)
        page.v.addWidget(gl)
        self.inner.addWidget(page)
        self.win.show()
        pump(0.2)
        self.assertIsNone(gpu_scroll.begin(page, page.verticalScrollBar()))
        gl.hide()
        pump(0.05)
        self.assertIsNotNone(gpu_scroll.begin(page, page.verticalScrollBar()), 'hidden canvas does not matter')

    def test_covered_area_falls_back_to_raster(self):
        page = yt.Page()
        for k in range(40):
            page.v.addWidget(QLabel(f'Метка {k}'))
        self.inner.addWidget(page)
        self.win.show()
        pump(0.2)
        over = QLabel('поверх', self.frame)
        over.setGeometry(40, 40, 200, 40)
        over.show()
        over.raise_()
        pump(0.05)
        self.assertIsNone(gpu_scroll.begin(page, page.verticalScrollBar()))


if __name__ == '__main__':
    unittest.main()
