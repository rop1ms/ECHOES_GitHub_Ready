"""Focused checks: scrolling never decodes or stats a new cover on the GUI thread."""
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from PyQt6.QtGui import QColor,QImage
from PyQt6.QtWidgets import QApplication,QWidget
import yamusic_theme as theme

APP=QApplication.instance() or QApplication([])


class RepaintTarget(QWidget):
    updates=0
    def update(self,*args): self.updates+=1;super().update(*args)


class CoverScrollLoading(unittest.TestCase):
    def setUp(self):
        theme._COVER_CACHE.clear();theme._COVER_BYTES[0]=0;theme._NO_COVER.clear()
        self.widget=RepaintTarget();self.gui_thread=threading.get_ident()
        self.release=threading.Event()

    def tearDown(self):
        self.release.set();self.widget.close()

    def wait_for(self,condition):
        until=time.monotonic()+2
        while not condition() and time.monotonic()<until:
            APP.processEvents();time.sleep(.001)
        self.assertTrue(condition())

    @staticmethod
    def image():
        image=QImage(80,80,QImage.Format.Format_ARGB32)
        image.fill(QColor('#123456'));return image

    def test_new_cover_and_file_access_stay_off_the_gui_thread(self):
        path='slow-cover-first-request.png';key=(path,40,6,False,'');threads=[]
        def decode(*args):
            threads.append(threading.get_ident())
            self.release.wait(2)
            return self.image()
        original_exists=Path.exists
        def exists(path):
            self.assertNotEqual(threading.get_ident(),self.gui_thread)
            return original_exists(path)
        with patch.object(theme,'_cover_image',decode),patch.object(Path,'exists',exists):
            placeholder=theme.cover_pixmap(path,40,lazy=self.widget)
            self.assertFalse(placeholder.isNull())
            self.wait_for(lambda:bool(threads))
            self.assertNotEqual(threads[0],self.gui_thread)
            self.assertNotIn(key,theme._COVER_CACHE)
            self.release.set();self.wait_for(lambda:key in theme._COVER_CACHE)

    def test_pending_repaints_register_each_viewport_only_once(self):
        path='pending-cover.png';key=(path,40,6,False,'');entered=threading.Event()
        def decode(*args):
            entered.set();self.release.wait(2);return self.image()
        with patch.object(theme,'_cover_image',decode):
            theme.cover_pixmap(path,40,lazy=self.widget)
            self.wait_for(entered.is_set)
            for _ in range(100):theme.cover_pixmap(path,40,lazy=self.widget)
            self.assertEqual(len(theme._LOADER.waiting[key]),1)
            self.release.set();self.wait_for(lambda:key not in theme._LOADER.waiting)
            self.assertEqual(self.widget.updates,1)

    def test_ready_image_replaces_placeholder_and_next_paint_uses_cache(self):
        path='ready-cover.png';key=(path,40,6,False,'');calls=[]
        def decode(*args):calls.append(1);return self.image()
        with patch.object(theme,'_cover_image',decode):
            placeholder=theme.cover_pixmap(path,40,lazy=self.widget)
            self.wait_for(lambda:key in theme._COVER_CACHE)
            ready=theme.cover_pixmap(path,40,lazy=self.widget)
            self.assertNotEqual(placeholder.cacheKey(),ready.cacheKey())
            self.assertEqual(ready.toImage().pixelColor(40,40).name(),'#123456')
            for _ in range(20):self.assertEqual(theme.cover_pixmap(path,40,lazy=self.widget).cacheKey(),ready.cacheKey())
            self.assertEqual(len(calls),1)

    def test_failed_cover_is_remembered_without_retrying_every_scroll_frame(self):
        path='missing-cover.png';key=(path,40,6,False,'');calls=[]
        def decode(*args):calls.append(1);return QImage()
        with patch.object(theme,'_cover_image',decode):
            theme.cover_pixmap(path,40,lazy=self.widget)
            self.wait_for(lambda:key in theme._NO_COVER)
            for _ in range(100):self.assertFalse(theme.cover_pixmap(path,40,lazy=self.widget).isNull())
            self.assertEqual(len(calls),1)


if __name__=='__main__':unittest.main(verbosity=2)
