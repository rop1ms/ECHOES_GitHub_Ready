"""Theme corners, distinct navigation icons, adaptive window frames and duel rendering."""
import os
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM']='offscreen'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from PyQt6.QtCore import Qt, QRect, QRectF, QPointF
from PyQt6.QtGui import QPixmap, QColor
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget, QPushButton
from PyQt6.QtTest import QTest
from frameclock import FrameTimer
import ya_skin, ui_anim
from echo_glass import GlassFrame
from yamusic_theme import ya_icon
from net_page import NetPage
from music_duel import Arena, Sprite

APP=QApplication.instance() or QApplication([])

class ThemeWindowDuel(unittest.TestCase):
    def test_corners_are_independent_and_bounded(self):
        skin=ya_skin.normalize({'radius':0,'panel_radius':32})
        self.assertEqual(skin['radius'],0)
        self.assertEqual(skin['panel_radius'],32)
        self.assertEqual(ya_skin.normalize({'panel_radius':999})['panel_radius'],36)
        self.assertEqual(ya_skin.normalize({})['panel_radius'],22)
        self.assertIn('border-radius:32px',ya_skin.overrides_qss(skin))
        self.assertIn('border-radius:32px',ya_skin.preview_qss(skin))

    def test_glass_surface_uses_interface_corners(self):
        frame=GlassFrame(); frame.resize(200,120)
        original=dict(ya_skin.CURRENT)
        try:
            ya_skin.CURRENT['panel_radius']=0
            frame.grab()
            self.assertEqual(frame._surface_radius,0)
            self.assertEqual(frame._surface.toImage().pixelColor(2,2).alpha(),255)
            ya_skin.CURRENT['panel_radius']=36
            frame.grab()
            self.assertGreater(frame._surface_radius,0)
            self.assertEqual(frame._surface.toImage().pixelColor(2,2).alpha(),0)
        finally:
            ya_skin.CURRENT.clear();ya_skin.CURRENT.update(original)
            frame.close()

    def test_navigation_drawings_differ(self):
        names=('tastemap','broadcast_tower','users','net','wave','headphones','note')
        pictures=[ya_icon(name,40).pixmap(40,40).toImage() for name in names]
        for i in range(len(pictures)):
            for j in range(i): self.assertNotEqual(pictures[i],pictures[j],(names[i],names[j]))

    def test_friend_play_action_fits_its_own_row(self):
        fake=NS(_av=lambda *a: QPixmap(32,32),_clickable=lambda widget,*a:widget,
                play_meta=lambda *a:None,open_chat=lambda *a:None,quiz_live_start=lambda *a:None)
        contact={'onion':'peer','name':'Friend','now':'{"artist":"Artist","title":"Song"}','room':'','game':''}
        widget=NetPage._friend_now_card(fake,contact)
        widget.resize(280,158);widget.show();APP.processEvents()
        button=next(b for b in widget.findChildren(QPushButton) if b.text()=='Включить трек')
        self.assertGreaterEqual(button.width(),button.sizeHint().width())
        self.assertTrue(widget.rect().contains(button.geometry()))
        widget.close()

    def test_manual_resize_reuses_snapshot_and_releases_it(self):
        win=QMainWindow();central=QWidget();win.setCentralWidget(central)
        win.resize(500,350);win.show();APP.processEvents()
        preview=ui_anim._ResizePreview(win)
        with patch.object(ui_anim,'enabled',return_value=True): preview.begin()
        key=preview.surface.snapshot.cacheKey()
        win.resize(700,500);APP.processEvents()
        self.assertEqual(preview.surface.size(),central.size())
        self.assertEqual(preview.surface.snapshot.cacheKey(),key)
        preview.end();self.assertIsNone(preview.surface)
        win.close()

    def test_state_animation_restores_updates(self):
        win=QMainWindow();win.setCentralWidget(QWidget());win.resize(500,350)
        win.show();win.showMaximized();APP.processEvents()
        import frameclock
        frameclock.clock().set_paused(False)
        with patch.object(ui_anim,'enabled',return_value=True):
            ui_anim.animate_state(win,'normal');QTest.qWait(30)
            ghost=win._state_anim
            self.assertIsInstance(ghost.frame_timer,FrameTimer)
            self.assertIsInstance(ghost._rect(),QRectF)
            QTest.qWait(450)
        self.assertIsNone(win._state_anim)
        self.assertTrue(win.updatesEnabled())
        win.close()

    def test_duel_raster_caches_and_frame_clock(self):
        host=QWidget();host.resize(1000,700)
        page=NS(widget=lambda:host,preview_side=lambda:None)
        arena=Arena(page);arena.setParent(host);arena.resize(1000,700)
        sprite=Sprite({'path':'a','title':'A'},-1)
        first=arena._halo(sprite,True)
        self.assertEqual(first.cacheKey(),arena._halo(sprite,True).cacheKey())
        self.assertIsInstance(arena.timer,FrameTimer)
        self.assertTrue(arena.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent))
        host.close()

if __name__=='__main__':unittest.main()
