"""Duel selection/chorus races and private staff report delivery, offline only."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import numpy as np
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from duel_settings import options, filter_tracks, fire_tier, liked_paths
from duel_chorus import detect_samples, explicit_start, stamp
from duel_fire import FireCache
from music_duel import Duel, DuelPage
from net_store import Store
import net_bugs

APP=QApplication.instance() or QApplication([])


class DuelOptions(unittest.TestCase):
    def setUp(self):
        self.tracks=[{'path':'a','title':'A','genre':'Rock','artist':'One','album':'First','duration':180},
                     {'path':'b','title':'B','genres':['Rock','Pop'],'artist':'Two','album':'Second','duration':220},
                     {'path':'c','title':'C','genre':'Jazz','artist':'One','album':'First','duration':300}]

    def filtered(self,**kwargs):
        return filter_tracks(self.tracks,options({'duel_options':kwargs}),playlists={'Mix':{'tracks':['a',self.tracks[2]]}},liked={'a','b'})

    def test_defaults_and_intersection(self):
        self.assertTrue(options({})['chorus']);self.assertTrue(options({})['fire'])
        self.assertEqual([t['path'] for t in self.filtered(genre='rock')],['a','b'])
        self.assertEqual([t['path'] for t in self.filtered(genre='Rock',artist='One',album='First',playlist='Mix',liked_only=True,min_seconds=150,max_seconds=190)],['a'])
        self.assertEqual(self.filtered(genre='Jazz',liked_only=True),[])

    def test_missing_playlist_and_unknown_duration_do_not_leak(self):
        self.assertEqual(self.filtered(playlist='Removed'),[])
        self.tracks[0]['duration']='unknown'
        self.assertNotIn(self.tracks[0],self.filtered(max_seconds=200))

    def test_multi_artist_and_cached_genres(self):
        self.tracks[0]['artists']=[{'name':'Guest'},'One']
        result=filter_tracks(self.tracks,options({'duel_options':{'artist':'Guest','genre':'ambient'}}),profiles={'a':{'genres':['ambient']}})
        self.assertEqual(result,[self.tracks[0]])

    def test_favorites_store_contains_tracks(self):
        self.assertEqual(liked_paths(NS(playlists={'Мне нравится':{'tracks':['a',{'path':'b'}]}})),{'a','b'})

    def test_wins_undo_restore_and_fire(self):
        game=Duel();game.start(self.tracks)
        champion=game.current()[0]['path'];game.vote(0);game.vote(0)
        wins={game.roster[i]['path']:n for i,n in game.wins().items()};self.assertEqual(wins[champion],2)
        restored=Duel();self.assertTrue(restored.restore(game.dump()));restored.undo()
        self.assertEqual(sum(restored.wins().values()),1)
        self.assertEqual([fire_tier(n) for n in [0,1,2,3,5,10,20,35]],[0,1,1,2,3,4,5,6])

    def test_fire_cache_is_bounded_and_colors_change(self):
        cache=FireCache();first=cache.frame(300,412,1,1.)
        self.assertIs(first,cache.frame(300,412,1,1.))
        self.assertNotEqual(first,cache.frame(300,412,20,1.))
        for n in range(100):cache.frame(300,412,1+n%40,n/12)
        self.assertLessEqual(len(cache.frames),36)

    def test_explicit_and_invalid_chorus_times(self):
        self.assertEqual(explicit_start({'chorus_start':'1:12','duration':200}),72.)
        for value in [True,float('nan'),float('inf'),-2,199]:
            self.assertIsNone(explicit_start({'chorus_start':value,'duration':200}))

    def test_silence_short_and_nonfinite_samples(self):
        for samples in [np.zeros(240000),np.ones(100),np.full(240000,np.nan)]:
            self.assertEqual(detect_samples(samples),(0.,'start'))

    def test_repeated_loud_section_is_used_instead_of_fixed_offset(self):
        rate=8000;t=np.arange(rate*8)/rate
        quiet=(.03*np.sin(t*2*np.pi*164)).astype('float32')
        chorus=(.5*np.sin(t*2*np.pi*261.6)+.4*np.sin(t*2*np.pi*329.6)+.3*np.sin(t*2*np.pi*392)).astype('float32')
        song=np.concatenate([quiet,quiet,chorus,quiet,quiet,chorus,quiet])
        start,kind=detect_samples(song)
        self.assertEqual(kind,'chorus');self.assertGreaterEqual(start,14);self.assertLess(start,24)

    def test_analysis_result_cannot_start_old_or_hidden_track(self):
        track={'path':'a'};fake=NS(_preview_request=(0,track,4),game=NS(current=lambda:[track]),
                                  win=NS(_stream_token=5),isVisible=lambda:True,_start_preview=Mock())
        DuelPage._chorus_ready(fake,stamp(track),(42.,'chorus'))
        fake._start_preview.assert_not_called()
        fake._preview_request=(0,track,5);fake.isVisible=lambda:False
        DuelPage._chorus_ready(fake,stamp(track),(42.,'chorus'));fake._start_preview.assert_not_called()
        fake._preview_request=(0,track,5);fake.isVisible=lambda:True
        DuelPage._chorus_ready(fake,stamp(track),(42.,'chorus'));fake._start_preview.assert_called_once_with(track,42.)


class StaffReports(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.stores=[]
        self.sender=self.node('user','');self.admin=self.node('admin','admin');self.other=self.node('outsider','')

    def node(self,name,role):
        store=Store(Path(self.temporary.name)/(name+'.db'));self.stores.append(store)
        return NS(me=NS(onion=name),admin_onion='admin',mods=lambda:{'mod':{}},role=lambda peer: 'admin' if peer=='admin' else 'mod' if peer=='mod' else '',
                  my_role=lambda:role,store=store,peers={},emit=Mock(),poke=Mock(),call=Mock(),_name=lambda:name)

    def tearDown(self):
        for store in self.stores:store.db.close()
        self.temporary.cleanup()

    def queued(self):
        ident=net_bugs.queue(self.sender,'admin','Playback issue','Playback stops when I open the queue.','Open queue','2026-test')
        return ident,json.loads(self.sender.store.q('SELECT payload FROM bug_reports WHERE id=?',(ident,))[0]['payload'])

    def connection(self,peer):
        messages=[]
        async def send(message):messages.append(message)
        return NS(onion=peer,send=send,messages=messages)

    async def test_only_verified_staff_can_be_selected(self):
        self.assertEqual(net_bugs.recipients(self.sender),['admin','mod'])
        for recipient in ['outsider','user','fake-admin']:
            with self.assertRaises(ValueError):net_bugs.queue(self.sender,recipient,'Test','A valid detailed bug report.')

    async def test_private_roundtrip_deduplicates_and_requires_ack(self):
        ident,payload=self.queued();to_staff=self.connection('admin')
        await net_bugs.flush(self.sender,to_staff)
        self.assertEqual(len(to_staff.messages),1)
        self.assertEqual(self.sender.store.q('SELECT state FROM bug_reports')[0]['state'],'queued')
        from_user=self.connection('user')
        await net_bugs.receive(self.admin,from_user,to_staff.messages[0]);await net_bugs.receive(self.admin,from_user,to_staff.messages[0])
        self.assertEqual(len(self.admin.store.q('SELECT id FROM bug_reports')),1)
        await net_bugs.receive(self.sender,self.connection('outsider'),from_user.messages[0])
        self.assertEqual(self.sender.store.q('SELECT state FROM bug_reports')[0]['state'],'queued')
        await net_bugs.receive(self.sender,self.connection('admin'),from_user.messages[0])
        self.assertEqual(self.sender.store.q('SELECT state FROM bug_reports')[0]['state'],'received')

    async def test_nonstaff_does_not_receive_or_ack_report(self):
        ident,payload=self.queued();peer=self.connection('user')
        await net_bugs.receive(self.other,peer,{'t':'bug_report','report':payload})
        self.assertEqual(self.other.store.q('SELECT * FROM bug_reports'),[]);self.assertEqual(peer.messages,[])

    async def test_moderator_cannot_ack_a_report_addressed_to_admin(self):
        ident,_=self.queued()
        await net_bugs.receive(self.sender,self.connection('mod'),{'t':'bug_ack','id':ident})
        self.assertEqual(self.sender.store.q('SELECT state FROM bug_reports')[0]['state'],'queued')

    async def test_pending_report_survives_store_reopen(self):
        ident,_=self.queued();self.sender.store.db.close();self.stores.remove(self.sender.store)
        self.sender.store=Store(Path(self.temporary.name)/'user.db');self.stores.append(self.sender.store)
        self.assertEqual(net_bugs.pending_peers(self.sender),['admin'])
        connection=self.connection('admin');await net_bugs.flush(self.sender,connection)
        self.assertEqual(connection.messages[0]['report']['id'],ident)

    async def test_stranger_report_uses_dedicated_protocol_before_friend_gate(self):
        from net_core import NetNode
        _,payload=self.queued();connection=self.connection('user')
        await NetNode._handle(self.admin,connection,{'t':'bug_report','report':payload})
        self.assertEqual(len(self.admin.store.q("SELECT id FROM bug_reports WHERE direction='in'")),1)

    async def test_cancelled_queue_is_not_retried(self):
        ident,_=self.queued()
        self.sender.store.x("UPDATE bug_reports SET state='cancelled' WHERE id=?",(ident,))
        connection=self.connection('admin');await net_bugs.flush(self.sender,connection)
        self.assertEqual(connection.messages,[]);self.assertEqual(net_bugs.pending_peers(self.sender),[])

    async def test_receiver_rate_limit_and_malformed_payload(self):
        _,base=self.queued();peer=self.connection('user')
        for i in range(8):
            report=dict(base,id=f'{i:024x}');await net_bugs.receive(self.admin,peer,{'t':'bug_report','report':report})
        self.assertEqual(len(self.admin.store.q('SELECT id FROM bug_reports')),5)
        for broken in [dict(base,title=''),dict(base,details='x'*3001),dict(base,ts=float('inf')),dict(base,id='bad')]:
            self.assertIsNone(net_bugs.clean(broken))


if __name__=='__main__':unittest.main()
