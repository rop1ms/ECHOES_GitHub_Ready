"""Focused radio voice/soundboard, signed approvals, support replies and player/export tests."""
import asyncio,copy,json,os,secrets,sys,tempfile,time,unittest,wave
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,Mock,patch
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from net_core import NetNode,Identity
from net_store import Store
import net_radio_auth as auth
import net_bugs
from radio_audio import VoiceRack,PRESETS,options,decode_clip
from duel_settings import fire_tier,FIRE_LEVELS
from dsp_export import player_fx,audio_filters,speed_factor

class TestNode(NetNode):
    @property
    def admin_onion(self):return self._test_admin

class Permissions(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.nodes=[]
        self.admin_id=Identity(b'a'*32);self.mod_id=Identity(b'm'*32);self.user_id=Identity(b'u'*32)
        self.admin=self.node(self.admin_id);self.mod=self.node(self.mod_id);self.user=self.node(self.user_id)
        ml=self.admin.me.sign_obj({'t':'echoes-mods','pub':self.admin.me.onion,'ts':time.time(),
            'mods':{self.mod.me.onion:{'name':'Moderator','ts':time.time()}}})
        for n in self.nodes:n._take_modlist(ml)

    def node(self,identity):
        node=object.__new__(TestNode);node._test_admin=self.admin_id.onion;node.me=identity
        node.base=Path(self.temp.name)/identity.onion;node.base.mkdir()
        node.store=Store(node.base/'net.db');node.settings={};node.peers={};node.nostr=None;node.loop=None
        node.room_host=None;node.room_guest=None;node.room_state=None;node.room_members=set()
        node.emit=Mock();node.poke=Mock();node.call=Mock();node.save_settings=Mock();node.log=Mock()
        node._name=lambda:'Demo';node._broadcast_friends=AsyncMock();node._staff_publish=AsyncMock(return_value=0)
        node.staff_hidden=lambda *args:'';node.is_friend=lambda peer:False
        self.nodes.append(node);return node

    def tearDown(self):
        for node in self.nodes:node.store.db.close()
        self.temp.cleanup()

    async def grant(self,issuer=None):
        issuer=issuer or self.mod
        decision=await auth.issue(issuer,self.user.me.onion,True)
        self.assertTrue(auth.accept(self.user,self.user.me.onion,auth.proof(issuer,self.user.me.onion)))
        return decision

    async def test_default_denied_and_staff_allowed(self):
        self.assertFalse(auth.allowed(self.user));self.assertTrue(auth.allowed(self.admin));self.assertTrue(auth.allowed(self.mod))
        await self.user.radio_open({'queue':[]})
        self.assertIsNone(self.user.room_host)
        await self.user.room_open('Bypass attempt',radio=NS(state=Mock()))
        self.assertIsNone(self.user.room_host)

    async def test_admin_and_mod_can_issue_valid_signed_permits(self):
        await self.grant();self.assertTrue(auth.allowed(self.user))
        await self.grant(self.admin);self.assertTrue(auth.allowed(self.user))

    async def test_unsigned_forged_and_nonstaff_permits_rejected(self):
        decision=await self.grant();forged={**decision,'target':self.admin.me.onion}
        self.assertFalse(auth.take(self.user,forged))
        self.assertFalse(auth.valid(self.user,{k:v for k,v in decision.items() if k!='sig'}))
        fake=self.user.me.sign_obj({**decision,'by':self.user.me.onion})
        self.assertFalse(auth.valid(self.user,fake))
        self.assertIsNone(await auth.issue(self.user,self.user.me.onion,True))

    async def test_revocation_prevents_replay_and_survives_restart(self):
        old=await self.grant();decision=await auth.issue(self.mod,self.user.me.onion,False)
        self.assertFalse(auth.accept(self.user,self.user.me.onion,auth.proof(self.mod,self.user.me.onion)))
        self.assertFalse(auth.take(self.user,old));self.assertFalse(auth.allowed(self.user))
        self.user.__dict__.pop('_radio_permits');self.assertFalse(auth.allowed(self.user))

    async def test_removed_moderator_cannot_authorize_radio(self):
        await self.grant()
        ml=self.admin.me.sign_obj({'t':'echoes-mods','pub':self.admin.me.onion,'ts':time.time()+1,'mods':{}})
        self.user._take_modlist(ml);self.assertFalse(auth.allowed(self.user))

    async def test_private_request_approval_reply_and_ack_roundtrip(self):
        staff=self.mod;user=self.user
        ident=net_bugs.queue(user,staff.me.onion,'Radio request','I would like to host a music show.',category='radio')
        sent=[]
        async def send(packet):sent.append(copy.deepcopy(packet))
        to_staff=NS(onion=staff.me.onion,send=send);to_user=NS(onion=user.me.onion,send=send)
        await net_bugs.flush(user,to_staff)
        await staff._handle(to_user,sent.pop(0));ack=sent.pop(0);await user._handle(to_staff,ack)
        staff.peers[user.me.onion]=to_user
        self.assertTrue(await net_bugs.answer(staff,ident,'Approved. Welcome on air!','approved'))
        packets=list(sent);sent.clear()
        for packet in packets:await user._handle(to_staff,packet)
        row=user.store.q('SELECT state,payload FROM bug_reports WHERE id=?',(ident,))[0]
        self.assertEqual(row['state'],'approved');self.assertTrue(auth.allowed(user))
        self.assertEqual(json.loads(row['payload'])['answer']['text'],'Approved. Welcome on air!')
        for packet in list(sent):await staff._handle(to_user,packet)
        self.assertEqual(staff.store.q('SELECT state FROM bug_reports')[0]['state'],'answered')
        await user._handle(to_staff,ack)
        self.assertEqual(user.store.q('SELECT state FROM bug_reports')[0]['state'],'approved')

    async def test_nonstaff_cannot_reply_or_approve(self):
        self.assertFalse(await net_bugs.answer(self.user,'a'*24,'Approved','approved'))
        ident=net_bugs.queue(self.user,self.mod.me.onion,'Radio request','A detailed application for a station.',category='radio')
        await net_bugs.receive(self.user,NS(onion=self.user.me.onion,send=AsyncMock()),
            {'t':'staff_reply','id':ident,'answer':{'state':'approved','text':'Forged'}})
        self.assertEqual(self.user.store.q('SELECT state FROM bug_reports')[0]['state'],'queued')

    async def test_approval_without_valid_radio_permission_is_ignored(self):
        ident=net_bugs.queue(self.user,self.mod.me.onion,'Radio request','A detailed application for a station.',category='radio')
        await net_bugs.receive(self.user,NS(onion=self.mod.me.onion,send=AsyncMock()),
            {'t':'staff_reply','id':ident,'answer':{'state':'approved','text':'No certificate'}})
        self.assertFalse(auth.allowed(self.user));self.assertEqual(self.user.store.q('SELECT state FROM bug_reports')[0]['state'],'queued')

    async def test_radio_sound_file_requires_current_station_membership(self):
        path=Path(self.temp.name)/'clip.wav';path.write_bytes(b'audio')
        meta={'fid':'a'*24,'name':'clip.wav','size':5,'sha':'a'*64,'kind':'audio','pv':{}}
        self.user.store.put_file(meta['fid'],self.user.me.onion,meta,'have',str(path),['radio'])
        self.user.room_host={'sounds':[meta]};self.user.room_members={self.mod.me.onion};self.user._streams={}
        self.user._stream_file=AsyncMock()
        stranger=NS(onion=self.admin.me.onion,via='direct',send=AsyncMock())
        await self.user._file_get(stranger,{'fid':meta['fid']});self.assertEqual(stranger.send.call_args.args[0]['t'],'file_err')
        member=NS(onion=self.mod.me.onion,via='direct',send=AsyncMock())
        await self.user._file_get(member,{'fid':meta['fid']});await asyncio.sleep(0)
        self.user._stream_file.assert_awaited_once()

class RadioAudio(unittest.TestCase):
    def tone(self,freq=220,seconds=1):return (.2*np.sin(np.arange(round(48000*seconds))*2*np.pi*freq/48000)).astype(np.float32)
    def process(self,rack,audio):return np.concatenate([rack.process(audio[i:i+960]) for i in range(0,len(audio),960)])
    def peak(self,audio):
        audio=audio[len(audio)//2:];return np.argmax(abs(np.fft.rfft(audio*np.hanning(len(audio)))))*48000/len(audio)

    def test_bypass_is_exact_and_options_are_bounded(self):
        x=self.tone();np.testing.assert_array_equal(VoiceRack({}).process(x),x)
        c=options({'pitch':999,'formant':-999,'autotune':float('nan'),'key':999})
        self.assertEqual((c['pitch'],c['formant'],c['key']),(12,-6,11))

    def test_all_voice_presets_produce_finite_fixed_size_output(self):
        x=self.tone(seconds=.4)
        for _,name,preset in PRESETS:
            with self.subTest(name=name):
                y=self.process(VoiceRack({**preset,'enabled':True}),x)
                self.assertEqual(len(y),len(x));self.assertTrue(np.isfinite(y).all());self.assertLessEqual(np.max(abs(y)),.981)

    def test_pitch_moves_frequency_without_changing_duration(self):
        x=self.tone(seconds=1.5);rack=VoiceRack({'enabled':True,'pitch':7});y=self.process(rack,x)
        self.assertEqual(len(x),len(y));self.assertAlmostEqual(self.peak(y),220*2**(7/12),delta=8)
        rack.update({'enabled':True,'pitch':5})
        changed=rack.process(x[:960]);self.assertGreater(float(np.sqrt(np.mean(changed**2))),.01,'Adjusting pitch must not restart the silent STFT warmup')

    def test_autotune_snaps_a_detuned_tone_to_scale(self):
        y=self.process(VoiceRack({'enabled':True,'autotune':100,'scale':2}),self.tone(430,2))
        self.assertAlmostEqual(self.peak(y),440,delta=4)

    def test_echo_and_reverb_have_real_tails(self):
        x=np.concatenate([self.tone(seconds=.12),np.zeros(48000,dtype=np.float32)])
        for effect in ['echo','reverb']:
            y=self.process(VoiceRack({'enabled':True,effect:70}),x)
            self.assertGreater(np.max(abs(y[15000:24000])),.0001)

    def test_clip_decoder_limits_and_mono_sample_rate(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'sound.wav'
            def write(seconds):
                with wave.open(str(path),'wb') as f:
                    f.setnchannels(1);f.setsampwidth(2);f.setframerate(48000);f.writeframes((self.tone(seconds=seconds)*32767).astype('<i2').tobytes())
            write(.2);self.assertEqual(len(decode_clip(path)),9600)
            write(31)
            with self.assertRaises(ValueError):decode_clip(path)
            with self.assertRaises(ValueError):decode_clip(Path(directory)/'script.exe')

    def test_soundboard_sends_audio_even_when_microphone_gate_is_closed(self):
        from net_voice import VoiceEngine,FRAME
        voice=VoiceEngine({'voice_ns':0});voice.want={'adpcm'};voice.sink=Mock();voice._play_local=Mock()
        voice._process(np.zeros(FRAME),self.tone(seconds=.02))
        self.assertTrue(voice.talking);voice.sink.assert_called_once();self.assertFalse(voice.mic_active)
        voice._start_worker=Mock();voice.play_clip(np.ones(20,dtype=np.float32)*.1)
        self.assertEqual(len(voice._clip),20+FRAME*3)
        np.testing.assert_array_equal(voice._clip[-FRAME*3:],0)
        voice._mic=NS(stop=Mock(),close=Mock());voice.radio_fx_active=True;voice.mic_off()
        self.assertEqual(voice._tail_frames,10)
        voice.sink.reset_mock();voice._process(np.zeros(FRAME),force=True);voice.sink.assert_called_once()
        voice.close()

class PlayerAndLevels(unittest.TestCase):
    def win(self):return NS(engine=NS(preset_mode='slowed',preset_factor=.8,_REVERB_WET=.45),
        eq_effective=lambda:[6.,3.,0.,0.,0.,0.,0.,0.,0.,0.],bass_boost=lambda:50,_boost_percent=lambda:200)
    def test_export_uses_active_player_speed_eq_bass_and_gain(self):
        fx=player_fx(self.win());self.assertEqual(speed_factor(fx),.8);self.assertEqual(fx['gain'],2)
        filters=audio_filters(fx);self.assertTrue(any('equalizer=f=60' in f for f in filters))
        self.assertFalse(any(f.startswith('bass=') for f in filters),'Bass is already included in the shared effective EQ')
        self.assertIn('volume=2.0000',filters)
    def test_normal_player_does_not_force_slowed_export(self):
        win=self.win();win.engine.preset_mode=None;win.engine.preset_factor=1.;win._boost_percent=lambda:100
        fx=player_fx(win);self.assertEqual(speed_factor(fx),1);self.assertFalse(fx['reverb'])
        win.engine.speed=1.5;self.assertEqual(speed_factor(player_fx(win)),1.5)
        win.engine.speed=.9;self.assertEqual(speed_factor(player_fx(win)),.9);self.assertFalse(player_fx(win)['reverb'])
    def test_fire_starts_at_level_one_and_advances_only_at_thresholds(self):
        self.assertTrue(all(fire_tier(wins)==0 for wins in range(5)))
        for i,threshold in enumerate(FIRE_LEVELS):
            self.assertEqual(fire_tier(threshold),i+1);self.assertEqual(fire_tier(threshold-1),i)

if __name__=='__main__':unittest.main()
