"""Offline integration: chorus start, saved filters, staff reports and performance UI."""
import os
os.environ['ECHOES_QA_LANGUAGE']='en'
from pathlib import Path
source=Path(__file__).with_name('ui_smoke.py').read_text('utf-8')
exec(compile(source.split('win = None')[0],str(Path(__file__).with_name('ui_smoke.py')),'exec'))
import asyncio,json,statistics,wave
from unittest.mock import patch,PropertyMock,AsyncMock
from types import SimpleNamespace as NS
import numpy as np
from PyQt6.QtCore import QRectF,Qt
from PyQt6.QtGui import QPixmap,QPainter,QColor,QLinearGradient
from PyQt6.QtWidgets import QPushButton
import main,ui_anim,performance
from duel_chorus import analyze
from duel_settings import options
from raster_budget import ROWS

main.WindowsMediaKeyFilter.register=lambda self:None
win=main.MainWindow();win.resize(1600,1000);win.show();settle(.5);win.vol.setValue(0)
out=ROOT/'docs/screenshots';out.mkdir(parents=True,exist_ok=True)
rate=8000;t=np.arange(rate*8)/rate
quiet=.03*np.sin(t*2*np.pi*164)
chorus=.5*np.sin(t*2*np.pi*261.6)+.4*np.sin(t*2*np.pi*329.6)+.3*np.sin(t*2*np.pi*392)
samples=(np.clip(np.concatenate([quiet,quiet,chorus,quiet,quiet,chorus,quiet]),-1,1)*16000).astype('<i2')
tracks=[]
for index in range(8):
    file=audit_home/f'demo-{index}.wav'
    with wave.open(str(file),'wb') as f:f.setnchannels(1);f.setsampwidth(2);f.setframerate(rate);f.writeframes(samples.tobytes())
    cover=audit_home/f'cover-{index}.png';pm=QPixmap(500,500);p=QPainter(pm)
    gradient=QLinearGradient(0,0,500,500);gradient.setColorAt(0,QColor(['#103f35','#182e64','#46295c'][index%3]));gradient.setColorAt(1,QColor(['#46d8a6','#508bde','#a56ce9'][index%3]));p.fillRect(pm.rect(),gradient)
    p.setPen(QColor('#d8fff4'));p.setFont(QFont('Segoe UI',26,QFont.Weight.Bold));p.drawText(QRectF(0,0,500,500),Qt.AlignmentFlag.AlignCenter,['AFTERGLOW','BLUE HOUR','PARALLAX'][index%3]);p.end();pm.save(str(cover))
    tracks.append({'path':str(file),'title':['Afterglow','Blue Hour','Parallax'][index%3]+f' / {index+1}', 'artist':'ECHOES Sessions',
                   'album':'Signals','genre':'Electronic' if index<6 else 'Ambient','duration':56,'cover':str(cover),'chorus_start':16})
win.library=tracks;win.playlists['Signals']={'tracks':tracks};win.playlists['Мне нравится']={'tracks':tracks[:4]}
detected=analyze({k:v for k,v in tracks[0].items() if k!='chorus_start'})
assert 12<detected[0]<24,detected
shell=win._ya;shell.show_page('duel');ui_anim._finish(shell.stack);settle(.3)
duel=shell.pages['duel'];duel.begin();settle(.7)
loads=[];original=win.engine.load
def load(path,*args,**kwargs):loads.append(kwargs.get('start_sec',0));return original(path,*args,**kwargs)
win.engine.load=load;duel.preview(0);settle(.9)
assert loads[-1]==16,loads
assert win.engine.get_position()>=14,win.engine.get_position()
win.engine.pause_hard();win._set_playing_ui(False)
for _ in range(3):duel.vote(0);duel.arena.fast_forward()
settle(.5);win.grab().save(str(out/'duel-fire.png'))
times=[]
for index in range(80):
    before=time.perf_counter();duel.arena.grab();times.append((time.perf_counter()-before)*1000)
active_aura_bytes=duel.arena.fire_cache.bytes
dialog=duel.open_settings();dialog=duel._settings_dialog;dialog.resize(920,800);settle(.2)
dialog.grab().save(str(out/'duel-settings.png'))
save=next(b for b in dialog.findChildren(QPushButton) if b.text()=='Save');save.click();settle(.2)
assert win.settings['duel_options']['chorus'] is True

shell.show_page('settings');ui_anim._finish(shell.stack);shell.settings_hub.select('performance');settle(.6)
from performance_ui import PerformanceCard
card=shell.settings_hub.findChild(PerformanceCard)
card.preset('memory');card.apply();assert ROWS.limit==4*2**20
card.preset('balanced');card.apply();assert ROWS.limit==12*2**20
card.tabs.setCurrentIndex(1);settle(.3);win.grab().save(str(out/'performance-memory.png'))
card.tabs.setCurrentIndex(2);settle(.2);win.grab().save(str(out/'performance-effects.png'))
net=shell.pages['net'];report=net.open_bug_report();settle(.2)
assert net.node.loop is None,'Offline integration must never send on a real network'
report.subject.setText('Playback stops when opening the queue')
report.details.setPlainText('The current track pauses when I open the queue. I expected playback to continue without interruption.')
report.steps.setPlainText('1. Start a local track.\n2. Open the queue.\n3. Watch the playback button.')
report.grab().save(str(out/'bug-report.png'));report.submit()
assert net.node.store.q("SELECT state FROM bug_reports WHERE direction='out'")[0]['state']=='queued'
from net_bugs import receive
with patch.object(type(net.node),'admin_onion',new_callable=PropertyMock,return_value=net.node.me.onion):
    incoming=receive(net.node,NS(onion='demo-user',send=AsyncMock()),{'t':'bug_report','report':{
        'id':'a'*24,'title':'Preview window stops refreshing','details':'The picture preview stopped refreshing after I resized the window.',
        'steps':'Open a picture and resize its preview window.','version':'2026.11.01-2','name':'ECHOES Demo','ts':time.time()}})
    try:incoming.send(None)
    except StopIteration:pass
    report.refresh();report.tabs.setCurrentIndex(2);settle(.2);report.grab().save(str(out/'bug-inbox.png'))
report.close()
ui_anim.animate_state(win,'full');settle(.04);win.animated_minimize();settle(.05);ui_anim.bring_front(win);settle(.6)
assert win.updatesEnabled() and not win.isMinimized() and win.isFullScreen()
assert not errors,errors
result={'chorus_seconds':round(detected[0],2),'chorus_kind':detected[1],'duel_start_seconds':loads[-1],
        'arena_mean_ms':round(statistics.mean(times),2),'arena_p95_ms':round(sorted(times)[75],2),
        'active_aura_cache_mib':round(active_aura_bytes/2**20,2),'hidden_aura_cache_mib':round(duel.arena.fire_cache.bytes/2**20,2),'row_budget_mib':ROWS.limit/2**20,
        'bug_report':'queued locally; no network','python_errors':errors}
(ROOT/'qa/results/duel_performance_ui.json').write_text(json.dumps(result,indent=2),'utf-8');print(json.dumps(result),flush=True)
win._really_quit=True;win.close();settle(.1);os._exit(0)
