"""Offline regressions for resizable media viewers and embedded dialog cards."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from PyQt6.QtCore import Qt, QSize, QRect, QPoint, QObject, pyqtSignal, QByteArray
from PyQt6.QtGui import QImage, QColor, QPdfWriter, QPainter, QFontDatabase, QFont
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget, QDialog, QVBoxLayout, QLabel
from PyQt6.QtTest import QTest
import inapp
from net_files_ui import MediaViewer, MediaThumb

app = QApplication.instance() or QApplication([])
app.setStyle("Fusion")
if not QFontDatabase.families() and Path("C:/Windows/Fonts/segoeui.ttf").exists():
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
    app.setFont(QFont("Segoe UI", 10))


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.path = self.folder / "image.png"
        image = QImage(240, 180, QImage.Format.Format_RGB32)
        image.fill(QColor("#478cbe"))
        image.save(str(self.path))
        self.win = QMainWindow()
        self.win.setCentralWidget(QWidget())
        self.win.resize(1280, 900)
        self.win.show()
        self.windows = []
        app.processEvents()

    def tearDown(self):
        for window in self.windows:
            window.close()
        self.win.close()
        app.processEvents()
        self.temp.cleanup()

    def viewer(self, kind="image", path=None):
        path = path or self.path
        view = MediaViewer(self.win, str(path), {"name": path.name, "kind": kind, "size": path.stat().st_size})
        self.windows.append(view)
        view.resize(1200, 800)
        view.show()
        app.processEvents()
        return view

    def test_image_fills_available_viewport_and_refits_after_resize(self):
        view = self.viewer()
        self.assertGreater(view.canvas.scale, 1)  # small attachments can also use the screen
        self.assertGreater(view.canvas.width(), view.width() * .95)
        before = view.canvas.scale
        view.resize(700, 500)
        app.processEvents()
        self.assertLess(view.canvas.scale, before)
        self.assertGreaterEqual(view.bar.x(), 0)
        self.assertLessEqual(view.bar.geometry().right(), view.width())

    def test_cards_downsample_large_photos_and_suspend_hidden_spinners(self):
        path = self.folder / "large.png"
        image = QImage(4000, 3000, QImage.Format.Format_RGB32)
        image.fill(QColor("#446688"))
        image.save(str(path))
        thumb = MediaThumb({"kind": "image", "pv": {"w": 4000, "h": 3000}})
        self.windows.append(thumb)
        thumb.set_local(str(path))
        self.assertLessEqual(thumb.img.width(), 1024)
        self.assertLessEqual(thumb.img.height(), 840)
        thumb.set_progress(.5)
        self.assertFalse(thumb._t.isActive())
        thumb.show()
        app.processEvents()
        self.assertTrue(thumb._t.isActive())
        thumb.hide()
        self.assertFalse(thumb._t.isActive())

    def test_gif_attachments_open_the_resizable_media_viewer(self):
        import net_gif
        import base64
        path = self.folder / "cached.gif"
        path.write_bytes(base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"))

        class Loader(QObject):
            done = pyqtSignal(str, QByteArray)
            def get(self, url):
                pass

        cached_loader = Loader()
        with patch.object(net_gif, "loader", return_value=cached_loader), \
                patch.object(net_gif, "cache_file", return_value=path), \
                patch("net_files_ui.MediaViewer") as viewer:
            gif = net_gif.GifView({"url": "https://media.tenor.com/test.gif", "w": 1, "h": 1})
            self.windows.append(gif)
            gif._open_full()
            viewer.return_value.exec.assert_called_once()
            self.assertEqual(viewer.call_args.args[2]["kind"], "image")
            self.assertTrue(viewer.call_args.args[2]["pv"]["gif"])

    def test_text_panel_uses_almost_the_whole_window(self):
        path = self.folder / "notes.txt"
        path.write_text("A resizable text attachment\n" * 100, encoding="utf-8")
        view = self.viewer("text", path)
        self.assertGreater(view.panel.width(), view.width() * .95)
        view.resize(700, 500)
        app.processEvents()
        self.assertGreater(view.panel.width(), view.width() * .95)
        self.assertLessEqual(view.panel.geometry().bottom(), view.height())

    def test_native_preview_exec_bypasses_compact_inapp_adapter(self):
        view = self.viewer()
        marker = []
        with patch.dict(inapp._orig, {"exec": lambda d: marker.append(d) or 7}):
            self.assertEqual(view.exec(), 7)
        self.assertEqual(marker, [view])

    def test_pdf_viewport_follows_resizing(self):
        from PyQt6.QtPdfWidgets import QPdfView
        path = self.folder / "sample.pdf"
        writer = QPdfWriter(str(path))
        painter = QPainter(writer)
        painter.drawText(100, 100, "Resizable PDF attachment")
        painter.end()
        del writer
        view = self.viewer("pdf", path)
        pdf = view.panel.findChild(QPdfView)
        self.assertIsNotNone(pdf)
        first = pdf.width()
        view.resize(700, 500)
        app.processEvents()
        self.assertLess(pdf.width(), first)
        self.assertGreater(pdf.width(), view.width() * .85)

    def test_video_viewport_uses_direct_qt_output(self):
        from PyQt6.QtMultimediaWidgets import QVideoWidget
        path = self.folder / "video.mp4"
        # Layout and output wiring do not require a real decoder or audio device.
        path.write_bytes(b"")
        view = self.viewer("video", path)
        output = view.panel.findChild(QVideoWidget)
        self.assertIs(view.player.videoOutput(), output)
        self.assertGreater(output.width(), view.width() * .85)
        view.resize(700, 500)
        app.processEvents()
        self.assertGreater(output.width(), view.width() * .85)
        view.close()
        self.assertIsNone(view.player)

    def test_fullscreen_restores_previous_window_state(self):
        view = self.viewer()
        view.showMaximized()
        app.processEvents()
        view.toggle_fullscreen()
        self.assertTrue(view.isFullScreen())
        view.toggle_fullscreen()
        self.assertTrue(view.isMaximized())

    def test_embedded_cards_resize_maximize_restore_and_stay_inside_parent(self):
        content = QDialog(self.win)
        content.resize(500, 350)
        layout = QVBoxLayout(content)
        layout.addWidget(QLabel("Attachment / dialog content"))
        layer = inapp.Layer(self.win, content, "Preview")
        self.windows.append(layer)
        app.processEvents()
        initial = layer.card.size()
        QTest.mousePress(layer.grip, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
        QTest.mouseMove(layer.grip, QPoint(100, 80))
        QTest.mouseRelease(layer.grip, Qt.MouseButton.LeftButton, pos=QPoint(100, 80))
        self.assertGreater(layer.card.width(), initial.width())
        self.assertGreater(layer.card.height(), initial.height())
        before_move = layer.card.pos()
        QTest.mousePress(layer.title, Qt.MouseButton.LeftButton, pos=QPoint(20, 10))
        QTest.mouseMove(layer.title, QPoint(50, 40))
        QTest.mouseRelease(layer.title, Qt.MouseButton.LeftButton, pos=QPoint(50, 40))
        self.assertNotEqual(layer.card.pos(), before_move)
        layer._set_card_geometry(QRect(100, 90, 1050, 700))
        size = layer.card.size()
        self.assertEqual(size, QSize(1050, 700))
        layer.toggle_maximized()
        app.processEvents()
        self.assertEqual(layer.card.geometry(), layer.rect().adjusted(6, 6, -6, -6))
        layer.toggle_maximized()
        self.assertEqual(layer.card.size(), size)
        self.win.resize(480, 350)
        app.processEvents()
        self.assertTrue(layer.rect().contains(layer.card.geometry()))
        self.assertTrue(layer.card.rect().contains(layer.grip.geometry()))
        layer.request_close()
        self.windows.remove(layer)


if __name__ == "__main__":
    unittest.main()
