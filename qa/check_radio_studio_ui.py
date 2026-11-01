"""Focused offline UI/export walkthrough for radio studio, approvals and shared player controls."""
import os
os.environ['ECHOES_QA_LANGUAGE']=os.environ.get('ECHOES_QA_LANGUAGE','en')
from pathlib import Path
source=Path(__file__).with_name('ui_smoke.py').read_text('utf-8')
exec(compile(source.split('win = None')[0],str(Path(__file__).with_name('ui_smoke.py')),'exec'))
import wave,json,copy
import numpy as np
from unittest.mock import PropertyMock,patch,AsyncMock,Mock
from types import SimpleNamespace as NS
from PyQt6.QtCore import QRectF,Qt
from PyQt6.QtGui import QPixmap,QPainter,QColor,QLinearGradient
from PyQt6.QtWidgets import QPushButton
import main,ui_anim
from net_core import Identity,NetNode
from radio_station import Station
from radio_console import RadioConsole
from radio_audio import options
import net_radio_auth as auth
import net_bugs,dsp_export

def inline(coro):
    try:coro.send(None)
    except StopIteration as done:return done.value
    raise AssertionError('Offline fixture unexpectedly awaited real I/O')

main.WindowsMediaKeyFilter.register=lambda self:None
win=main.MainWindow();win.resize(1700,1120);win.show();settle(.35);win.vol.setValue(0)
out=ROOT/'docs/screenshots';out.mkdir(parents=True,exist_ok=True)
tracks=[]
for i in range(10):
    audio=audit_home/f'demo-{i}.wav'
    samples=(np.sin(np.arange(48000*8)*2*np.pi*(220+i*20)/48000)*5000).astype('<i2')
    with wave.open(str(audio),'wb') as file:
        file.setnchannels(1);file.setsampwidth(2);file.setframerate(48000);file.writeframes(samples.tobytes())
    cover=audit_home/f'cover-{i}.png';pm=QPixmap(500,500);p=QPainter(pm)
    g=QLinearGradient(0,0,500,500);g.setColorAt(0,QColor(['#103f35','#182e64','#46295c'][i%3]));g.setColorAt(1,QColor(['#46d8a6','#508bde','#a56ce9'][i%3]));p.fillRect(pm.rect(),g)
    p.setPen(QColor('#e6fff8'));p.setFont(QFont('Segoe UI',26,QFont.Weight.Bold));p.drawText(QRectF(0,0,500,500),Qt.AlignmentFlag.AlignCenter,['AFTERGLOW','BLUE HOUR','PARALLAX'][i%3]);p.end();pm.save(str(cover))
    tracks.append({'path':str(audio),'title':['Afterglow','Blue Hour','Parallax'][i%3]+f' / {i+1}',
        'artist':'ECHOES Sessions','album':'Signals','duration':180+i*10,'cover':str(cover)})
win.library=tracks;win.playlists['Signals']={'tracks':tracks};win.current_index=0;win.queue_context='library'
win.settings['duel_options']={'chorus':False};shell=win._ya;net=shell.pages['net']
root=Identity(b'a'*32);authority=patch.object(NetNode,'admin_onion',new_callable=PropertyMock,return_value=root.onion);authority.start()
issuer=NS(base=audit_home/'staff',me=root,_radio_permits={},role=lambda peer:'admin' if peer==root.onion else '',
    my_role=lambda:'admin',emit=Mock(),_staff_load=lambda:{'mods':None},_staff_publish=AsyncMock(),_broadcast_friends=AsyncMock(),peers={})
issuer.base.mkdir()
assert not net.node.radio_allowed()
panel=net.open_bug_report();panel.category.setCurrentIndex(panel.category.findData('radio'))
panel.details.setPlainText('I would like to host an evening show about electronic music.');panel.submit();settle(.15)
assert net.node.store.q('SELECT state FROM bug_reports')[0]['state']=='queued'
decision=inline(auth.issue(issuer,net.node.me.onion,True));assert auth.accept(net.node,net.node.me.onion,auth.proof(issuer,net.node.me.onion))
assert net.node.radio_allowed()
panel.tabs.setCurrentIndex(1);settle(.1)
if settings['language']=='en':win.grab().save(str(out/'requests-radio.png'))
with patch.object(NetNode,'admin_onion',new_callable=PropertyMock,return_value=net.node.me.onion):
    incoming=net_bugs.clean({'id':'f'*24,'title':'Evening radio show','details':'I would like to host an evening music show for the community.',
        'steps':'','name':'ECHOES Demo','version':'2026.11.01-4','ts':time.time(),'category':'radio'})
    inline(net_bugs.receive(net.node,NS(onion=root.onion,send=AsyncMock()),{'t':'bug_report','report':incoming}))
    panel.refresh();panel.tabs.setCurrentIndex(2);settle(.15)
    assert any(b.text() in ('Approve','Одобрить') for b in panel.findChildren(QPushButton))
    assert any(b.text() in ('Decline','Отклонить') for b in panel.findChildren(QPushButton))
    if settings['language']=='en':win.grab().save(str(out/'staff-requests.png'))
panel.refresh()
station=Station({'name':'Night Signals','genre':'Electronic','repeat':True,'queue':[{'id':str(i),'track':t} for i,t in enumerate(tracks[:4])]})
clip={'fid':'a'*24,'name':'Station ident.wav','size':Path(tracks[0]['path']).stat().st_size,'sha':'b'*64,'kind':'audio','pv':{'dur':2}}
net.node.store.put_file(clip['fid'],net.node.me.onion,clip,'have',tracks[0]['path'],['radio'])
net.node.room_host={'title':'Night Signals','station':station,'state':station.state(),'public':True,'share':True,'sounds':[clip]}
net.node.settings['radio_station']=station.config();net.show_tab('room');net._fill_room();settle(.2)
anchor=next(b for b in net.room_page.findChildren(QPushButton) if b.isVisible() and b.text() in ('Broadcast voice & sounds','Голос и звуки эфира'))
console=RadioConsole(net,anchor,True);console.show();settle(.15)
console.preset(2);console.controls['pitch'].setValue(3);console.controls['reverb'].setValue(35);settle(.15)
assert options(net.node.settings['radio_voice_fx'])['pitch']==3
if settings['language']=='en':console.grab().save(str(out/'radio-voice-fx.png'))
console.tabs.setCurrentIndex(1);settle(.1)
if settings['language']=='en':console.grab().save(str(out/'radio-soundboard.png'))
assert any(b.text() in ('Play on air','В эфир') for b in console.findChildren(QPushButton));console.close()
shell.show_page('settings');ui_anim._finish(shell.stack);shell.settings_hub.select('sound');settle(.3)
from ya_sound_panel import SoundPanel
embedded=next(p for p in shell.settings_hub.findChildren(SoundPanel) if p.embedded)
embedded.bass.setValue(60);embedded._set_loud(200);embedded._toggle_preset('_btn_speedup',embedded.b_night)
until=time.monotonic()+5
while win.engine.preset_factor!=1.25 and time.monotonic()<until:settle(.05)
win.engine.pause_hard();win._set_playing_ui(False)
assert win.bass_boost()==60 and win._boost_percent()==200
assert win.engine.preset_mode=='speedup' and win.engine.preset_factor==1.25,(win.engine.preset_mode,win.engine.preset_factor,win._preset_status_lbl.text())
shell.toggle_sound_panel();settle(.1);popup=shell._sound_panel
assert popup.bass.value()==60 and popup.b_night.isChecked();popup.bass.setValue(35);embedded.sync();assert embedded.bass.value()==35
popup.hide()
if settings['language']=='en':win.grab().save(str(out/'player-effects-settings.png'))
export=dsp_export.ExportDialog(win,[tracks[0]]);export.show();settle(.15)
export.dir.setText(str(Path('Music')/'ECHOES FX'))
assert export.follow_player.isChecked() and export._fx()['speedup']
assert not export.rows['slowed'][0].isEnabled()
embedded.follow_export.setChecked(False);export._sync_link();assert not export.follow_player.isChecked()
embedded.follow_export.setChecked(True);export._sync_link();assert export.follow_player.isChecked()
if settings['language']=='en':export.grab().save(str(out/'export-player-settings.png'))
output=audit_home/'rendered.wav';dsp_export.render(win.engine,tracks[0]['path'],str(output),export._fx(),'wav')
import av
with av.open(str(output)) as rendered:
    stream=rendered.streams.audio[0];render_seconds=float(stream.duration*stream.time_base)
assert abs(render_seconds-6.4)<.06,render_seconds
export.close();win.engine.pause_hard();win._set_playing_ui(False)
shell.show_page('duel');ui_anim._finish(shell.stack);duel=shell.pages['duel'];duel.begin();settle(.2)
duel.vote(0);duel.arena.fast_forward();settle(.15);assert duel.arena.fire_cache.bytes==0
for _ in range(4):duel.vote(0);duel.arena.fast_forward()
settle(.9);assert duel.arena.fire_cache.bytes>0
if settings['language']=='en':win.grab().save(str(out/'duel-fire.png'))
assert not errors,errors
result={'language':settings['language'],'focused_ui_checks':['request category and queued application','signed hosting permission','voice cloud preset and sliders',
    'staff inbox approval and decline controls','file soundboard controls','settings and bottom popup share bass/gain/speed','export follows shared player state','actual WAV export duration',
    'no fire at one win, level one at five wins'],'rendered_seconds':render_seconds,'python_errors':errors}
(ROOT/f"qa/results/radio_studio_ui_{settings['language']}.json").write_text(json.dumps(result,indent=2),'utf-8');print(json.dumps(result),flush=True)
authority.stop();net.node.room_host=None;win._really_quit=True;win.close();settle(.1);os._exit(0)
