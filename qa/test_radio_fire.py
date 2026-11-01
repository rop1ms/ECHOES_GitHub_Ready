"""Only station branding/scheduling/protocol and native card flame regressions."""
import asyncio
import base64
import copy
import io
import os
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter
from PIL import Image
from radio_station import Station, clean_wire, clean_avatar, MAX_QUEUE
from net_core import NetNode
from duel_fire import FireCache
from music_duel import Duel

APP=QApplication.instance() or QApplication([])

def config():
    return {'name':'Night Signals','avatar':'','genre':'Electronic','repeat':True,
        'queue':[{'id':'a','track':{'title':'First','artist':'Sessions','duration':10,'path':'private.wav'}},
                 {'id':'b','track':{'title':'Second','artist':'Sessions','duration':20}}]}

class Schedule(unittest.TestCase):
    def setUp(self):
        self.now=0.; self.station=Station(config(),lambda:self.now)

    def test_clock_advances_and_wraps_without_a_player(self):
        self.now=13; st=self.station.state()
        self.assertEqual(st['radio']['current'],'b');self.assertAlmostEqual(st['pos'],3)
        self.now=95; st=self.station.state()
        self.assertEqual(st['radio']['current'],'a');self.assertAlmostEqual(st['pos'],5)

    def test_pause_resume_and_next_are_independent(self):
        self.now=4;self.station.control('pause');self.now=90
        self.assertEqual(self.station.state()['pos'],4)
        self.station.control('pause');self.now=92
        self.assertEqual(self.station.state()['pos'],6)
        self.station.control('next');self.assertEqual(self.station.state()['track']['title'],'Second')
        self.station.control('play','a');self.assertEqual(self.station.state()['pos'],0)

    def test_nonrepeat_stops_at_end(self):
        cfg=config();cfg['repeat']=False;self.station.configure(cfg)
        self.now=200;st=self.station.state()
        self.assertFalse(st['playing']);self.assertEqual(st['pos'],20)
        self.now=400;self.assertEqual(self.station.state()['pos'],20)

    def test_edit_retains_active_track_and_elapsed_position(self):
        self.now=14;self.station.state();cfg=self.station.config();cfg['queue'].reverse();cfg['name']='Renamed'
        self.station.configure(cfg);st=self.station.state()
        self.assertEqual(st['radio']['current'],'b');self.assertEqual(st['pos'],4)
        self.assertEqual(st['radio']['name'],'Renamed')
        cfg['queue']=[cfg['queue'][1]];self.station.configure(cfg)
        self.assertEqual(self.station.state()['radio']['current'],'a');self.assertEqual(self.station.position(),0)

    def test_empty_invalid_queue_does_not_replace_live_station(self):
        before=copy.deepcopy(self.station.config())
        for queue in [[],[{'track':{'title':'Unknown','duration':0}}],{},None]:
            with self.assertRaises(ValueError):self.station.configure({'queue':queue})
            self.assertEqual(self.station.config(),before)

    def test_bounded_queue_unique_ids_and_no_paths_on_wire(self):
        cfg=config();cfg['queue']=[cfg['queue'][0]]*150
        station=Station(cfg);queue=station.state()['radio']['queue']
        self.assertEqual(len(queue),MAX_QUEUE);self.assertEqual(len({q['id'] for q in queue}),MAX_QUEUE)
        self.assertNotIn('private.wav',str(station.state()))

    def test_full_and_delta_profile_queue_merge(self):
        full=clean_wire(self.station.state()['radio'])
        delta={k:v for k,v in full.items() if k not in ('avatar','queue')};delta['current']='b'
        self.assertEqual(clean_wire(delta,full)['queue'],full['queue'])
        delta['revision']='different'
        self.assertEqual(clean_wire(delta,full)['queue'],[])

    def test_avatar_validation_and_size_limit(self):
        buf=io.BytesIO();Image.new('RGB',(144,144),'blue').save(buf,format='JPEG')
        avatar=base64.b64encode(buf.getvalue()).decode()
        self.assertEqual(clean_avatar(avatar),avatar)
        for raw in ['not-base64','a'*25000,base64.b64encode(b'\xff\xd8\xffgarbage').decode()]:
            self.assertEqual(clean_avatar(raw),'')
        buf=io.BytesIO();Image.new('RGB',(300,300)).save(buf,format='JPEG')
        self.assertEqual(clean_avatar(base64.b64encode(buf.getvalue()).decode()),'')

class StationProtocol(unittest.IsolatedAsyncioTestCase):
    def node(self):
        node=object.__new__(NetNode)
        node.room_host={'title':'Night Signals','state':Station(config()).state(),'station':Station(config()),
                        'share':True,'public':True,'genre':'Electronic','id':'station'}
        node.room_members={'guest'};node.peers={'guest':NS(send=AsyncMock())};node.settings={}
        node.room_guest=None;node.room_state=None;node.room_chat=[];node.me=NS(onion='host')
        node.emit=Mock();node.log=Mock();node._broadcast_friends=AsyncMock();node._hi=Mock(return_value={})
        node._name=lambda:'Host';node._contact_name=lambda peer:peer;node.room_codec=lambda:'adpcm'
        node.nostr=None;node._staff={'mods':None,'acts':{}};node._radio_permits={};node.radio_allowed=lambda *args:True
        return node

    async def test_personal_player_updates_cannot_overwrite_air(self):
        node=self.node();before=copy.deepcopy(node.room_host['state'])
        await node.room_update({'track':{'title':'Personal song'},'pos':900,'playing':False})
        self.assertEqual(node.room_host['state'],before)

    async def test_normal_listen_together_still_follows_player(self):
        node=self.node();node.room_host.pop('station')
        await node.room_update({'track':{'title':'Personal song'},'pos':9,'playing':False})
        self.assertEqual(node.room_host['state']['track']['title'],'Personal song')

    async def test_listener_full_then_small_delta_then_profile_edit(self):
        node=self.node();peer=node.peers['guest']
        await node._room_broadcast();full=peer.send.call_args.args[0]['state']
        self.assertIn('queue',full['radio'])
        await node._room_broadcast();delta=peer.send.call_args.args[0]['state']
        self.assertNotIn('queue',delta['radio']);self.assertNotIn('avatar',delta['radio'])
        self.assertEqual(clean_wire(delta['radio'],full['radio'])['queue'],full['radio']['queue'])
        node.room_host['state']['radio']['revision']='edited'
        await node._room_broadcast();self.assertIn('queue',peer.send.call_args.args[0]['state']['radio'])

    async def test_station_close_cancels_schedule(self):
        node=self.node();node.room_host['public']=False
        node._radio_task=asyncio.create_task(asyncio.sleep(3600));task=node._radio_task
        await node.room_close()
        self.assertTrue(task.cancelled());self.assertIsNone(node.room_host)
        self.assertIsNone(node.peers['guest'].send.call_args.args[0]['state'])

    async def test_signed_public_announcement_contains_station_profile(self):
        node=self.node();node.nostr=NS(publish=AsyncMock());node._nostr_sign=AsyncMock(return_value={'signed':True})
        node._link_tag=lambda:['echoes-link','demo']
        await node._nostr_room_announce()
        import json
        tags=dict((t[0],t[1]) for t in node._nostr_sign.call_args.args[2])
        info=json.loads(tags['echoes-room'])
        self.assertEqual(info['radio']['name'],'Night Signals');self.assertEqual(info['now']['title'],'First')
        self.assertNotIn('queue',info['radio']);node.nostr.publish.assert_awaited_once()
        self.assertEqual(node._clean_room_summary({'radio':info['radio']})['radio'],info['radio'])

    @patch('net_radio_auth.accept',return_value=True)
    async def test_guest_receives_branding_and_preserves_queue_between_updates(self,_authority):
        host=self.node();guest=self.node();guest.room_host=None;guest.room_guest='host';guest.is_friend=lambda peer:False
        buf=io.BytesIO();Image.new('RGB',(32,32),'green').save(buf,format='JPEG')
        host.room_host['state']['radio']['avatar']=base64.b64encode(buf.getvalue()).decode()
        await host._room_broadcast();message=copy.deepcopy(host.peers['guest'].send.call_args.args[0])
        await guest._handle(NS(onion='host'),message)
        self.assertEqual(guest.room_state['radio']['name'],'Night Signals')
        self.assertTrue(guest.room_state['radio']['avatar'])
        await host._room_broadcast();message=copy.deepcopy(host.peers['guest'].send.call_args.args[0])
        await guest._handle(NS(onion='host'),message)
        self.assertEqual(len(guest.room_state['radio']['queue']),2)
        self.assertTrue(guest.room_state['radio']['avatar'])

    async def test_background_loop_advances_air_without_gui_or_personal_playback(self):
        node=self.node();clock=[0.];station=Station(config(),lambda:clock[0]);node.room_host['station']=station
        seen=[]
        async def broadcast():seen.append(copy.deepcopy(node.room_host['state']))
        async def tick(_):
            if clock[0]:raise asyncio.CancelledError
            clock[0]=13.
        node._room_broadcast=broadcast;node._nostr_room_announce=AsyncMock()
        with patch('net_core.asyncio.sleep',side_effect=tick):
            with self.assertRaises(asyncio.CancelledError):await node._radio_loop()
        self.assertEqual([st['track']['title'] for st in seen],['First','Second'])

class NativeFire(unittest.TestCase):
    def render(self,now=1,wins=10,scale=1):
        image=QImage(600*scale,720*scale,QImage.Format.Format_ARGB32_Premultiplied);image.fill(Qt.GlobalColor.transparent)
        p=QPainter(image);p.scale(scale,scale);p.translate(300,360)
        FireCache().draw(p,QRectF(-194,-250,388,500),300,412,wins,now);p.end();return image

    def test_emitters_start_on_actual_card_not_outer_rectangle(self):
        cache=FireCache();points=cache.frame(300,412,10)
        self.assertIs(points,cache.frame(300,412,10,50))
        for e in points:
            self.assertLessEqual(abs(e.x),150.01);self.assertLessEqual(abs(e.y),206.01)
            self.assertTrue(abs(e.x)>125 or abs(e.y)>181)
            self.assertGreater(e.x*e.nx+e.y*e.ny,0)

    def test_native_render_animates_and_changes_tier(self):
        self.assertNotEqual(self.render(1),self.render(1.5))
        self.assertNotEqual(self.render(1,5),self.render(1,50))
        self.assertFalse(self.render(scale=2).isNull())
        self.assertTrue(all(self.render(wins=0).pixelColor(x,y).alpha()==0 for x,y in [(100,100),(300,300)]))

    def test_recipes_remain_under_memory_budget(self):
        cache=FireCache()
        with patch('performance.config',return_value={'aura_quality':'high','aura_mb':1}):
            for width in range(150,300):cache.frame(width,412,3)
        self.assertLessEqual(cache.bytes,2**20)
        cache.clear();self.assertEqual(cache.bytes,0)

    def test_champion_streak_counts_actual_wins(self):
        game=Duel();game.start([{'path':str(i)} for i in range(5)])
        game.vote(0);game.vote(0);self.assertEqual(game.streak(),2)
        game.vote(1);self.assertEqual(game.streak(),1)
        game.undo();self.assertEqual(game.streak(),2)

if __name__=='__main__': unittest.main()
