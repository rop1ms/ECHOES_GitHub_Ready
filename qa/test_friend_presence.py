"""Synthetic presence/relay replay and online-alert regressions; no real contacts or relays."""
import asyncio
import json
import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'app'))
from net_core import NetNode, NostrPeer, NOSTR_PEER_IDLE, PRESENCE_SKEW
from net_page import NetPage


class Store:
    def __init__(self):self.people={'friend':{'onion':'friend','status':'friend','npk':'a'*64,'last_seen':0,'now':'','room':''}}
    def contact(self,onion):return self.people.get(onion)
    def contacts(self,status=None):return [c for c in self.people.values() if status is None or c['status']==status]
    def upsert_contact(self,onion,**fields):self.people[onion].update(fields)


def node():
    n=NetNode.__new__(NetNode)
    n.me=NS(onion='me');n.store=Store();n.peers={};n._incoming={}
    n.room_host=None;n.room_guest=None;n.room_members=set();n.now_playing=None
    n._ev_ok=Mock(return_value=True);n.linked_onion=Mock(return_value='friend')
    n._greet=AsyncMock();n._udp_offer=AsyncMock();n.emit=Mock();n.log=Mock()
    n._stop=False;n.settings={};n._name=lambda:'ECHOES';n._link_tag=lambda:[]
    return n


class Presence(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)  # fake monotonic jumps are intentional, not slow tasks
        self.n=node();self.wall=100_000.;self.mono=1_000.
        self.wall_patch=patch('net_core.time.time',side_effect=lambda:self.wall)
        self.mono_patch=patch('net_core.time.monotonic',side_effect=lambda:self.mono)
        self.wall_patch.start();self.mono_patch.start()

    async def asyncTearDown(self):
        await asyncio.sleep(0)
        self.wall_patch.stop();self.mono_patch.stop()

    def event(self,ts=None,on=True,**extra):
        return {'created_at':self.wall if ts is None else ts,'pubkey':'a'*64,
                'content':json.dumps(dict(on=on,now=None,room=None,**extra))}

    async def test_outgoing_mailbox_does_not_mean_friend_online(self):
        p=self.n._nostr_attach('friend','a'*64)
        self.assertFalse(self.n.is_online('friend'))
        original=p._last_rx_mono;self.wall+=60;self.mono+=60
        self.n._nostr_attach('friend','a'*64)
        self.assertEqual(p._last_rx_mono,original)
        self.assertFalse(self.n.is_online('friend'))

    async def test_fresh_heartbeat_expires_from_event_time_not_delivery(self):
        self.n._nostr_presence(self.event(ts=self.wall-150))
        self.assertTrue(self.n.is_online('friend'))
        self.assertEqual(self.n.store.contact('friend')['last_seen'],self.wall-150)
        self.wall+=61;self.mono+=61
        self.assertFalse(self.n.is_online('friend'))  # UI expires even before maintenance removes the object

    async def test_duplicates_and_reordered_history_cannot_renew_or_resurrect(self):
        initial=self.event();self.n._nostr_presence(initial)
        p=self.n.peers['friend'];last=p._last_rx_mono
        self.wall+=NOSTR_PEER_IDLE+1;self.mono+=NOSTR_PEER_IDLE+1
        self.n._nostr_presence(initial)
        self.n._nostr_presence(self.event(ts=self.wall-1800))
        self.n._nostr_presence(self.event(ts=self.wall-1500))
        self.assertEqual(p._last_rx_mono,last)
        self.assertFalse(self.n.is_online('friend'))

    async def test_offline_wins_same_second_and_old_online_cannot_undo_logout(self):
        on=self.event();self.n._nostr_presence(on)
        self.n._nostr_presence(self.event(on=False))
        self.assertNotIn('friend',self.n.peers)
        self.n._nostr_presence(on)
        self.assertFalse(self.n.is_online('friend'))
        self.wall+=1;self.mono+=1;self.n._nostr_presence(self.event())
        self.assertTrue(self.n.is_online('friend'))

    async def test_invalid_future_and_malformed_presence_do_not_poison_watermark(self):
        for ev in (self.event(ts=self.wall+5000),self.event(ts=float('nan')),
                   dict(self.event(),content='[]'),dict(self.event(),content='{}'),
                   dict(self.event(),content='{"on":"false"}')):
            self.n._nostr_presence(ev)
        self.assertFalse(self.n.is_online('friend'))
        self.n._nostr_presence(self.event())
        self.assertTrue(self.n.is_online('friend'))

    async def test_old_offline_cannot_close_newer_inbound_or_direct_connection(self):
        p=self.n._nostr_attach('friend','a'*64,received=True)
        self.n._nostr_presence(self.event(ts=self.wall-30,on=False))
        self.assertTrue(self.n.is_online('friend'))
        p.via='direct'
        self.wall+=1;self.mono+=1;self.n._nostr_presence(self.event(on=False))
        self.assertTrue(self.n.is_online('friend'))

    async def test_wall_clock_rollback_does_not_reject_live_udp_or_extend_ttl(self):
        p=self.n._nostr_attach('friend','a'*64,received=True)
        self.wall-=3600;self.mono+=100
        self.n._received(p)  # direct/UDP activity, independent of sender/event clocks
        self.assertTrue(self.n.is_online('friend'))
        self.mono+=NOSTR_PEER_IDLE+1
        self.assertFalse(self.n.is_online('friend'))

    async def test_offline_publishes_once_urgently_and_blocks_late_regular_heartbeat(self):
        published=[];sign_started=asyncio.Event();allow_online=asyncio.Event()
        async def sign(kind,content,tags):
            event=json.loads(content)
            if event['on']:
                sign_started.set();await allow_online.wait()
            return event
        async def publish(event,urgent=False):published.append((event['on'],urgent))
        self.n.nostr=NS(up_count=1,closed=False,publish=publish)
        self.n._nostr_sign=sign
        regular=asyncio.create_task(self.n._presence_send())
        await sign_started.wait()
        await asyncio.gather(self.n._presence_send(leaving=True),self.n._presence_send(leaving=True))
        allow_online.set();await regular
        await self.n._presence_send()
        self.assertEqual(published,[(False,True)])


class NotificationAndExit(unittest.TestCase):
    def test_online_alert_requires_observed_offline_and_honors_cooldown(self):
        n=node();clock=[100_000.];online=[False]
        h=NS(node=n,_online=lambda onion:online[0],_name=lambda onion:'Friend',alert=Mock(),
             _tab='none',shell=NS(_dirty=set()),win=NS(_toast=Mock()),_net_up_t=clock[0]-100)
        with patch('net_page.time.time',side_effect=lambda:clock[0]):
            NetPage._presence_refresh(h)
            clock[0]+=50;online[0]=True;NetPage._presence_refresh(h)
            h.alert.assert_not_called()
            online[0]=False;NetPage._presence_refresh(h)
            clock[0]+=601;online[0]=True;NetPage._presence_refresh(h)
            self.assertEqual(h.alert.call_count,1)
            online[0]=False;NetPage._presence_refresh(h)
            clock[0]+=601;online[0]=True;NetPage._presence_refresh(h)
            self.assertEqual(h.alert.call_count,1)
            h._online_seen=set();h._off_since.clear();clock[0]+=2000
            NetPage._presence_refresh(h)
            self.assertEqual(h.alert.call_count,1)  # an unknown gap never implies a long absence

    def test_full_exit_attempts_offline_before_stopping_thread_and_respects_deadline(self):
        for stalled in (False,True):
            n=node();ready=threading.Event();received=[]
            loop=asyncio.new_event_loop()
            def run():
                asyncio.set_event_loop(loop);ready.set();loop.run_forever()
                pending=asyncio.all_tasks(loop)
                for task in pending:task.cancel()
                loop.run_until_complete(asyncio.gather(*pending,return_exceptions=True));loop.close()
            n.loop=loop;n.thread=threading.Thread(target=run,daemon=True);n.thread.start();ready.wait(1)
            async def leave(leaving=False):
                received.append((leaving,n._stop))
                if stalled:await asyncio.sleep(60)
                else:loop.call_soon(loop.stop)
            n._presence_send=leave
            try:
                start=time.monotonic();n.shutdown(timeout=.08);elapsed=time.monotonic()-start
                self.assertLess(elapsed,.3)
                self.assertEqual(received,[(True,True)])
            finally:
                if n.thread.is_alive():loop.call_soon_threadsafe(loop.stop)
                n.thread.join(1)


if __name__=='__main__':unittest.main(verbosity=2)
