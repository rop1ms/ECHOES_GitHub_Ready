"""Memory budgets, hidden-resource release and bounded image decoding."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from PyQt6.QtWidgets import QApplication,QPushButton,QWidget,QVBoxLayout
from PyQt6.QtGui import QImage,QPixmap,QMovie,QColor
from PyQt6.QtCore import QEvent
from raster_budget import ByteLRU,ROWS
import performance,paint_cache
from duel_fire import FireCache
from net_files_ui import _Canvas
APP=QApplication.instance() or QApplication([])


class Performance(unittest.TestCase):
    def test_lru_counts_bytes_replaces_and_evicts_oldest(self):
        cache=ByteLRU(100);cache.put((1,1),'first',40);cache.put((2,2),'second',40)
        cache.get((1,1));cache.put((3,3),'third',40)
        self.assertIsNone(cache.get((2,2)));self.assertEqual(cache.bytes,80)
        cache.put((1,1),'changed',70);self.assertEqual(cache.bytes,70)
        cache.put((9,9),'oversized',200);self.assertEqual(cache.bytes,70)
        cache.clear(1);self.assertEqual(cache.bytes,0)

    def test_shared_budget_does_not_multiply_by_view_count(self):
        cache=ByteLRU(12000)
        for view in range(20):
            for row in range(50):cache.put((view,row),(view,row),400)
        self.assertLessEqual(cache.bytes,12000);self.assertEqual(len(cache.items),30)

    def test_button_budget_and_hidden_release(self):
        paint_cache.clear_all();paint_cache.set_budget(16000)
        parent=QWidget();layout=QVBoxLayout(parent);buttons=[]
        for n in range(20):
            button=QPushButton(str(n));layout.addWidget(button);buttons.append(button)
            pixmap=QPixmap(100,20);pixmap.fill(QColor('blue'));button._echoes_pc=(n,pixmap);paint_cache._record(button,pixmap)
        self.assertLessEqual(paint_cache._BYTES,16000)
        self.assertNotIn('_echoes_pc',buttons[0].__dict__)
        handler=paint_cache._ButtonCache()
        handler.eventFilter(buttons[-1],QEvent(QEvent.Type.Hide))
        self.assertNotIn('_echoes_pc',buttons[-1].__dict__)
        parent.close();paint_cache.clear_all();paint_cache.set_budget(8*2**20)

    def test_smoke_aura_budget_even_when_sizes_and_colors_change(self):
        cache=FireCache()
        with patch.object(performance,'config',return_value=dict(performance.DEFAULTS,aura_mb=1,aura_quality='high')):
            for n in range(60):cache.frame(280+n%2,392+n%2,n%40+1,n*.13)
            self.assertLessEqual(cache.bytes,2**20)
            cache.clear();self.assertEqual(cache.bytes,0)

    def test_configuration_rejects_corrupt_settings(self):
        values=performance.config({'performance':{'cover_mb':'broken','gif_cache':'yes','rows_mb':float('inf'),'preview_side':-1}})
        self.assertEqual(values['cover_mb'],24);self.assertFalse(values['gif_cache']);self.assertEqual(values['rows_mb'],12);self.assertEqual(values['preview_side'],1920)

    def test_photo_is_decoded_at_selected_size(self):
        with tempfile.TemporaryDirectory() as folder:
            file=Path(folder)/'photo.png';image=QImage(2500,1600,QImage.Format.Format_RGB32);image.fill(QColor('blue'));image.save(str(file))
            canvas=_Canvas()
            with patch.object(performance,'config',return_value=dict(performance.DEFAULTS,preview_side=1280)):canvas.load(str(file))
            self.assertEqual(canvas.pm.width(),1280);self.assertLessEqual(canvas.pm.height(),1280);canvas.close()

    def test_memory_counter_is_real_and_struct_is_sized(self):
        if sys.platform=='win32':
            working,private=performance.memory_usage();self.assertGreater(working,2**20);self.assertGreater(private,2**20)

    def test_background_fps_does_not_overwrite_user_limit(self):
        from PyQt6.QtCore import Qt
        win=NS(settings={'fps_limit':240},isMinimized=lambda:True)
        with patch('frameclock.configure') as configure:
            performance.Governor.frame_policy(NS(win=win))
            self.assertEqual(configure.call_args.args[0]['fps_limit'],30)
            self.assertEqual(win.settings['fps_limit'],240)


if __name__=='__main__':unittest.main()
