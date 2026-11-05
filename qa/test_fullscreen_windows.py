"""F11 regression: live painting, DWM popups, combo selection and restored geometry.

ECHOES_QA_NATIVE=1 exercises real Windows OpenGL composition as well.
"""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

NATIVE = os.environ.get('ECHOES_QA_NATIVE') == '1' and sys.platform == 'win32'
if not NATIVE:
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QPainter, QColor
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget, QComboBox, QFrame
import ui_anim
import window_snap

APP = QApplication.instance() or QApplication([])
APP.setStyle("Fusion")


class Panel(QFrame):
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#ee2244'))
        painter.end()


class Fullscreen(unittest.TestCase):
    def test_maximized_state_and_minimized_restore(self):
        win = QMainWindow()
        win.settings = {'ui_animations': True}
        win.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        if NATIVE:
            window_snap.install(win)
        win.resize(850, 560)
        win.showMaximized()
        QTest.qWait(50)
        anim = ui_anim._WinAnim(win)
        try:
            ui_anim.animate_state(win, 'full')
            QTest.qWait(50)
            anim.minimize()
            QTest.qWait(150)
            ui_anim.bring_front(win)
            QTest.qWait(300)
            self.assertTrue(win.isFullScreen())
            self.assertTrue(win.updatesEnabled())
            self.assertEqual(win.windowOpacity(), 1.0)
            ui_anim.animate_state(win, 'max')
            QTest.qWait(50)
            self.assertTrue(win.isMaximized())
        finally:
            win.close()

    def test_fullscreen_popup_and_restore(self):
        win = QMainWindow()
        win.settings = {'ui_animations': True}
        win.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        win.setGeometry(70, 90, 850, 560)
        body = QWidget()
        body.setStyleSheet('background:#182438;')
        win.setCentralWidget(body)
        if NATIVE:
            from PyQt6.QtOpenGLWidgets import QOpenGLWidget
            anchor = QOpenGLWidget(body)
            anchor.setGeometry(1, 1, 1, 1)
            anchor.show()
            window_snap.install(win)
        win.show()
        QTest.qWait(80)
        normal = win.geometry()
        before = window_snap._api()[2].GetWindowLongPtrW(int(win.winId()), window_snap.GWL_STYLE) if NATIVE else 0
        panel = Panel(win, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        panel.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        panel.setStyleSheet('background:#ee2244;')
        panel.resize(300, 180)
        combo = QComboBox(body)
        combo.setGeometry(300, 250, 180, 35)
        combo.setStyleSheet("QComboBox {background:#222;color:white;} QComboBox QAbstractItemView {background:#333;color:white;}")
        combo.addItems(['30 FPS', '60 FPS', '120 FPS'])
        combo.show()
        try:
            for _ in range(3):
                with patch.object(win, 'grab', side_effect=AssertionError('F11 must not snapshot the desktop')):
                    ui_anim.animate_state(win, 'full')
                QTest.qWait(80)
                self.assertTrue(win.isFullScreen())
                self.assertTrue(win.updatesEnabled())
                self.assertIsNone(getattr(win, '_state_anim', None))
                if NATIVE:
                    u = window_snap._api()[2]
                    self.assertTrue(u.GetWindowLongPtrW(int(win.winId()), window_snap.GWL_STYLE) & window_snap.WS_BORDER)
                panel.move(win.mapToGlobal(QPoint(100, 100)))
                panel.show()
                panel.raise_()
                QTest.qWait(100)
                self.assertTrue(panel.isVisible())
                if NATIVE:
                    pos = panel.mapToGlobal(QPoint(80, 80))
                    img = win.screen().grabWindow(0).toImage()
                    origin = win.screen().geometry().topLeft()
                    dpr = img.devicePixelRatio()
                    color = img.pixelColor(round((pos.x()-origin.x())*dpr), round((pos.y()-origin.y())*dpr))
                    self.assertGreater(color.red(), 150, 'Sound-style popup must be visible above GL fullscreen')
                panel.hide()
                win.raise_()
                win.activateWindow()
                QTest.qWait(100)
                combo.showPopup()
                QTest.qWait(80)
                view = combo.view()
                self.assertTrue(view.isVisible())
                rect = view.visualRect(combo.model().index(1, 0))
                QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
                self.assertEqual(combo.currentIndex(), 1)
                combo.setCurrentIndex(0)
                ui_anim.animate_state(win, 'normal')
                QTest.qWait(80)
                self.assertFalse(win.isFullScreen())
                self.assertEqual(win.geometry(), normal)
                if NATIVE:
                    style = u.GetWindowLongPtrW(int(win.winId()), window_snap.GWL_STYLE)
                    self.assertEqual(style & window_snap.WS_BORDER, before & window_snap.WS_BORDER)
        finally:
            panel.close()
            win.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
