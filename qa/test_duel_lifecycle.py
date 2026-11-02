"""Duel lifecycle, delayed search, persistence failure and restart scenarios only."""
import os
from pathlib import Path
import random
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock,patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from PyQt6 import sip
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication,QWidget,QPushButton
import frameclock
from music_duel import Duel,DuelPage,Arena,identity

APP=QApplication.instance() or QApplication([])


class GameState(unittest.TestCase):
    def tracks(self,n=6):return [dict(path=f'{i}.mp3',title=f'Track {i}') for i in range(n)]

    def test_complete_undo_restart_restore_every_round_in_both_modes(self):
        for mode in ('king','pairs'):
            game=Duel();game.start(self.tracks(),mode,random.Random(17))
            for turn in range(80 if mode=='pairs' else 5):
                before=game.dump();game.vote(turn%2);after=game.dump()
                resumed=Duel();self.assertTrue(resumed.restore(after));self.assertEqual(resumed.dump(),after)
                game.undo();self.assertEqual(game.dump(),before);self.assertEqual(len(game.current()),2)
                game.restore(after)
            game.start(self.tracks(),mode,random.Random(19))
            self.assertEqual(len(game.current()),2);self.assertFalse(game.history)

    def test_impossible_or_duplicate_saved_session_is_rejected_transactionally(self):
        game=Duel();game.start(self.tracks(),rng=random.Random(3));original=game.dump()
        broken=game.dump();broken['champion']=broken['order'][1]
        self.assertFalse(game.restore(broken));self.assertEqual(game.dump(),original)
        broken=game.dump();broken['roster'][1]['path']=broken['roster'][0]['path']
        self.assertFalse(game.restore(broken));self.assertEqual(game.dump(),original)
        game.vote(0);original=game.dump();broken=game.dump();broken['history'][0]=broken['order'][-1]
        self.assertFalse(game.restore(broken));self.assertEqual(game.dump(),original)

    def test_empty_duplicate_or_invalid_new_pool_preserves_current_tournament(self):
        game=Duel();game.start(self.tracks());game.vote(1);saved=game.dump()
        for tracks in ([],self.tracks(1),[self.tracks(1)[0]]*3,[None,'bad',{},dict(path=123)]):
            with self.assertRaises(ValueError):game.start(tracks)
            self.assertEqual(game.dump(),saved)


class DuelLifecycle(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.tracks=[]
        for index in range(7):
            path=self.root/f'{index}.mp3';path.write_bytes(b'not decoded in these tests')
            self.tracks.append(dict(path=str(path),title=f'Song {index}',artist='Artist',duration=100,genre='Rock'))
        self.win=QWidget();self.win.library=list(self.tracks);self.win.playlists={}
        self.win.settings={'language':'en','duel_options':{'chorus':False,'fire':False}}
        self.win.save_settings=Mock();self.win._toast=Mock();self.win._stream_token=0;self.win._duel_preview=None
        self.win.engine=NS(pause_hard=Mock(),is_playing=lambda:False)
        self.win._set_playing_ui=Mock();self.win.stream_tracks=Mock()
        self.shell=NS(win=self.win,_profiles={})
        self.cover=patch.object(Arena,'_load_cover',lambda *_:None);self.cover.start()
        frameclock.clock().set_paused(True)
        self.page=DuelPage(self.shell);self.page.resize(1100,850);self.page.show();APP.processEvents()

    def tearDown(self):
        if not sip.isdeleted(self.page):
            self.page.arena.timer.stop();self.page.close();sip.delete(self.page)
        self.win.close();sip.delete(self.win);APP.processEvents();self.cover.stop();self.temp.cleanup()

    def pending(self):
        self.page._set_mode('pairs');self.page.internet.setChecked(True)
        self.page.busy=True;self.page.request+=1;self.page._arm_start(self.page.request)
        return self.page.request

    @staticmethod
    def response():return {'tracks':[dict(url=f'https://example.invalid/{i}',title=f'Online {i}') for i in range(3)]}

    def test_first_empty_library_then_add_files_both_start_buttons_work(self):
        self.win.library=[];self.page.start.click();self.assertFalse(self.page.game.roster)
        self.assertTrue(self.page.start.isEnabled())
        self.win.library=list(self.tracks)
        QTest.mouseClick(self.page.arena,Qt.MouseButton.LeftButton,pos=self.page.arena.start_rect().center().toPoint())
        self.assertEqual(len(self.page.game.current()),2)
        self.page.vote(1);self.page.start.click()
        self.assertFalse(self.page.game.history);self.assertEqual(len(self.page.game.roster),7)

    def test_fast_votes_undo_tab_switch_complete_and_restart_stay_consistent(self):
        self.page.begin()
        for turn in range(6):
            self.page.vote(turn%2);self.page.arena.fast_forward()
            current={identity(t) for t in self.page.game.current()}
            sprites={s.ident for s in self.page.arena.sprites if s.side is not None and not s.leaving}
            self.assertEqual(current,sprites)
            self.page.hide();self.page.show();APP.processEvents()
        self.assertEqual(len(self.page.game.current()),1)
        self.page.undo();self.assertEqual(len(self.page.game.current()),2)
        self.page.begin();self.assertEqual(len(self.page.game.current()),2);self.assertFalse(self.page.game.history)

    def test_filters_missing_files_and_library_removed_preserve_old_progress_and_recover(self):
        self.page.begin();self.page.vote(0);saved=self.page.game.dump()
        self.win.settings['duel_options']['genre']='No matching genre'
        self.page.begin();self.assertEqual(self.page.game.dump(),saved)
        self.win.settings['duel_options']['genre']=''
        self.win.library=[dict(path='missing.mp3'),None,dict(path=123),dict(path='bad\0path')]
        self.page.begin();self.assertEqual(self.page.game.dump(),saved)
        self.win.library=list(self.tracks);self.page.begin()
        self.assertEqual(len(self.page.game.current()),2);self.assertFalse(self.page.game.history)

    def test_late_online_search_cannot_reset_new_local_tournament(self):
        request=self.pending();self.page._set_mode('king');self.page.begin();self.page.vote(0)
        saved=self.page.game.dump();self.page.received(request,self.response(),True)
        self.assertEqual(self.page.game.dump(),saved);self.assertFalse(self.page.busy)
        self.assertTrue(self.page.start.isEnabled())

    def test_mode_filters_query_hide_and_progress_cancel_old_autostart(self):
        for cancel in ('mode','filter','query','hide','vote'):
            self.page.show();self.page._set_mode('king');self.page.begin();saved=self.page.game.dump()
            request=self.pending()
            if cancel=='mode':self.page._set_mode('king')
            elif cancel=='filter':self.page.options_changed()
            elif cancel=='query':self.page.query.setText('another query')
            elif cancel=='hide':self.page.hide()
            else:self.page.vote(0);saved=self.page.game.dump()
            self.page.received(request,self.response(),True)
            self.assertEqual(self.page.game.dump(),saved,cancel)
            self.page.online=[];self.page.query.clear()

    def test_repeated_start_while_search_busy_keeps_latest_explicit_start_intent(self):
        request=self.pending();self.page.online=[]
        self.page.begin();self.page.begin();self.page.received(request,self.response(),False)
        self.assertEqual(self.page.game.mode,'pairs');self.assertGreater(len(self.page.game.roster),7)
        self.assertFalse(self.page.busy);self.assertFalse(self.page._pending_start)

    def test_stale_malformed_or_failed_search_recovers_controls_without_game_loss(self):
        self.page.begin();self.page.vote(0);saved=self.page.game.dump()
        for result in (None,[],{'tracks':None,'errors':None},{'tracks':[None,{},dict(url=55)],'errors':[123]}):
            request=self.pending();self.page.received(request,result,True)
            self.assertFalse(self.page.busy);self.assertTrue(self.page.load.isEnabled())
            self.assertEqual(self.page.game.dump(),saved)
        request=self.pending();self.page.received(request-1,self.response(),True)
        self.assertTrue(self.page.busy)
        self.page.received(request,{'tracks':[]},True);self.assertFalse(self.page.busy)

    def test_save_failure_does_not_disable_restart_or_desynchronize_cards(self):
        self.win.save_settings.side_effect=OSError('read only fixture')
        self.page.begin();self.page.vote(0);self.page.undo();self.page.begin()
        self.assertEqual(len(self.page.game.current()),2)
        self.assertFalse(self.page.game.history);self.assertTrue(self.page.start.isEnabled())
        self.assertIn('save',self.page.status.text().lower())
        self.win.save_settings.side_effect=None;self.page.vote(1)
        self.assertFalse(self.page._save_error)

    def test_unserializable_save_does_not_interrupt_start_or_vote(self):
        for error in (TypeError('non-JSON metadata'),ValueError('invalid saved value')):
            with self.subTest(error=type(error).__name__):
                self.win.save_settings.side_effect=error
                self.page.begin();self.page.vote(0)
                self.assertEqual(self.page.game.cursor,2)
                self.assertTrue(self.page._save_error)
                self.assertTrue(self.page.start.isEnabled())

    def test_partial_track_metadata_cannot_break_champion_screen_or_restart(self):
        self.win.library=[dict(self.tracks[0],title=None),dict(self.tracks[1],title=123)]
        self.page.begin();self.page.vote(0)
        self.assertEqual(len(self.page.game.current()),1)
        self.page.start.click();self.assertEqual(len(self.page.game.current()),2)

    def test_pairs_skip_vote_undo_pause_and_restart_keep_active_cards_in_sync(self):
        self.page._set_mode('pairs');self.page.begin()
        for turn in range(30):
            if turn%3==0:self.page.skip.click()
            else:self.page.vote(turn%2)
            if turn%5==0:self.page.undo()
            self.page.arena.fast_forward()
            self.assertEqual({identity(t) for t in self.page.game.current()},
                             {s.ident for s in self.page.arena.sprites if s.side is not None and not s.leaving})
        saved=self.page.game.dump()
        pause=next(button for button in self.page.findChildren(QPushButton) if button.text()=='Пауза')
        pause.click();self.win.engine.pause_hard.assert_called_once()
        self.assertEqual(self.page.game.dump(),saved)
        self.page.start.click();self.assertFalse(self.page.game.history)
        self.assertTrue(self.page.skip.isEnabled());self.assertEqual(len(self.page.game.current()),2)

    def test_invalid_initial_session_keeps_start_usable(self):
        for invalid in ('bad',{'roster':None},{},None):
            self.win.settings['duel_session']=invalid
            other=DuelPage(self.shell)
            try:
                other.start.click();self.assertTrue(other.start.isEnabled())
                self.assertEqual(len(other.game.current()),2)
            finally:other.arena.timer.stop();sip.delete(other)

    def test_restore_survives_empty_initial_library_and_missing_current_file(self):
        self.page.begin();self.page.vote(0);saved=self.page.game.dump()
        self.win.settings['duel_session']=saved;self.win.library=[]
        other=DuelPage(self.shell)
        try:
            self.assertEqual(other.game.dump(),saved)
            other.begin();self.assertEqual(other.game.dump(),saved)
            self.win.library=list(self.tracks);other.begin()
            self.assertEqual(len(other.game.current()),2);self.assertFalse(other.game.history)
        finally:other.arena.timer.stop();sip.delete(other)

    def test_result_after_page_deletion_is_ignored(self):
        request=self.pending();page=self.page;sip.delete(page)
        page.received(request,self.response(),True)

    def test_empty_arena_shortcuts_do_not_cancel_an_explicit_pending_start(self):
        request=self.pending();self.page.vote(0);self.page.undo();self.page.skip_pair()
        self.page.received(request,self.response(),True)
        self.assertEqual(self.page.game.mode,'pairs');self.assertEqual(len(self.page.game.current()),2)


if __name__=='__main__':unittest.main(verbosity=2)
