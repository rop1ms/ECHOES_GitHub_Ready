"""Only queue reuse, invalidation and bounded upcoming-list navigation."""
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from PyQt6.QtWidgets import QApplication, QLabel
from ya_queue import QueuePage, UPCOMING_MAX

APP = QApplication.instance() or QApplication([])


class QueueNavigation(unittest.TestCase):
    def setUp(self):
        tracks = [{'path':f'probe-{i}', 'title':f'Song {i}', 'artist':'Artist', 'duration':120} for i in range(160)]
        self.win = NS(library=tracks, playlists={}, current_index=0, queue_context='library',
                      user_queue=tracks[10:18], _shuffle_pos=0, _queued_now=False,
                      _shuffle_queue=[], _saved_ctx=None, shuffle=False)
        self.shell = NS(win=self.win, current_track=lambda:self.win.library[self.win.current_index],
                        _lib_rows=lambda rows:[{'t':t,'ctx':'library','i':self.win.library.index(t)} for t in rows],
                        is_current=lambda t:False, is_liked=lambda t:False, is_playing=lambda:False)
        self.page = QueuePage(self.shell); self.page.resize(900,600)

    def tearDown(self):
        self.page.close(); self.page.deleteLater(); APP.processEvents()

    def test_revisiting_unchanged_queue_reuses_widgets_and_scroll(self):
        self.assertTrue(self.page.refresh())
        self.page.show(); APP.processEvents()
        bar = self.page.verticalScrollBar(); bar.setValue(min(400,bar.maximum()))
        position = bar.value(); first = self.page.v.itemAt(0).widget()
        for _ in range(15): self.assertFalse(self.page.refresh())
        self.assertIs(self.page.v.itemAt(0).widget(),first)
        self.assertEqual(bar.value(),position)

    def test_same_length_reorder_and_replacement_invalidate(self):
        self.page.refresh(); first = self.page.sig
        self.win.user_queue[0],self.win.user_queue[1] = self.win.user_queue[1],self.win.user_queue[0]
        self.assertNotEqual(self.page.signature(),first); self.assertTrue(self.page.refresh())
        first = self.page.sig; self.win.user_queue[0] = self.win.library[-1]
        self.assertNotEqual(self.page.signature(),first); self.assertTrue(self.page.refresh())

    def test_visible_track_metadata_and_current_track_invalidate(self):
        self.page.refresh(); first = self.page.sig
        self.win.library[2]['title'] = 'Renamed upcoming song'
        self.assertNotEqual(self.page.signature(),first); self.assertTrue(self.page.refresh())
        self.win.current_index = 1
        self.assertTrue(self.page.refresh())
        self.assertTrue(any(label.text()=='Song 1' for label in self.page.findChildren(QLabel)))

    def test_saved_context_and_shuffle_order_are_respected(self):
        self.win.shuffle = True; self.win._queued_now = True
        order = list(reversed(range(len(self.win.library))))
        self.win._saved_ctx = ('library',42,order,8)
        ctx,cur,sq,pos = self.page._context_view()
        self.assertEqual((ctx,cur,pos),('library',42,8))
        self.assertEqual(self.page._upcoming(self.win.library,cur,sq,pos),order[9:9+UPCOMING_MAX])
        old = self.page.signature(); order[9],order[10] = order[10],order[9]
        self.assertNotEqual(self.page.signature(),old)

    def test_huge_context_only_reads_displayed_upcoming_rows(self):
        class LargeSequence:
            reads = 0
            def __len__(self):return 1_000_000
            def __getitem__(self,index):self.reads+=1;return index
            def __iter__(self):raise AssertionError('whole shuffle list scanned')
        tracks = LargeSequence(); sq = LargeSequence()
        self.win.shuffle = True
        result = self.page._upcoming(tracks,900_000,sq,900_000)
        self.assertEqual(result,list(range(900_001,900_101)))
        self.assertEqual(sq.reads,UPCOMING_MAX)
        self.assertEqual(tracks.reads,0)

    def test_explicit_force_refresh_still_rebuilds(self):
        self.page.refresh(); first = self.page.v.itemAt(0).widget()
        self.assertTrue(self.page.refresh(force=True))
        self.assertIsNot(self.page.v.itemAt(0).widget(),first)


if __name__=='__main__':unittest.main(verbosity=2)
