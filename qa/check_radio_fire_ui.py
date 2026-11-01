"""Focused offline UI checks for radio branding/queue and native card fire only."""
import os
os.environ['ECHOES_QA_LANGUAGE']=os.environ.get('ECHOES_QA_LANGUAGE','en')
from pathlib import Path
source=Path(__file__).with_name('ui_smoke.py').read_text('utf-8')
exec(compile(source.split('win = None')[0],str(Path(__file__).with_name('ui_smoke.py')),'exec'))
import base64,io,statistics,wave
from unittest.mock import patch
from PIL import Image
from PyQt6.QtCore import QRectF,Qt
from PyQt6.QtGui import QPixmap,QPainter,QColor,QLinearGradient
from PyQt6.QtWidgets import QLabel,QListWidget,QPushButton
from types import SimpleNamespace as NS
import main,ui_anim
from radio_station import Station
from radio_ui import StationDialog,avatar_pixmap

main.WindowsMediaKeyFilter.register=lambda self:None
win=main.MainWindow();win.resize(1600,1050);win.show();settle(.35)
out=ROOT/'docs/screenshots';out.mkdir(parents=True,exist_ok=True)
tracks=[]
for i in range(8):
    audio=audit_home/f'demo-{i}.wav'
    with wave.open(str(audio),'wb') as file:
        file.setnchannels(1);file.setsampwidth(2);file.setframerate(8000);file.writeframes(b'\0\0'*8000)
    cover=audit_home/f'cover-{i}.png';pm=QPixmap(500,500);p=QPainter(pm)
    g=QLinearGradient(0,0,500,500);g.setColorAt(0,QColor(['#103f35','#182e64','#46295c'][i%3]));g.setColorAt(1,QColor(['#46d8a6','#508bde','#a56ce9'][i%3]));p.fillRect(pm.rect(),g)
    p.setPen(QColor('#e6fff8'));p.setFont(QFont('Segoe UI',26,QFont.Weight.Bold));p.drawText(QRectF(0,0,500,500),Qt.AlignmentFlag.AlignCenter,['AFTERGLOW','BLUE HOUR','PARALLAX'][i%3]);p.end();pm.save(str(cover))
    tracks.append({'path':str(audit_home/f'demo-{i}.wav'),'title':['Afterglow','Blue Hour','Parallax'][i%3]+f' / {i+1}',
                   'artist':'ECHOES Sessions','album':'Signals','duration':180+i*10,'cover':str(cover)})
win.library=tracks;win.playlists['Signals']={'tracks':tracks};win.settings['duel_options']={'chorus':False}
shell=win._ya;shell.show_page('duel');ui_anim._finish(shell.stack);settle(.2)
duel=shell.pages['duel'];duel.begin();settle(.2)
for _ in range(5):duel.vote(0);duel.arena.fast_forward()
settle(.9)
times=[]
for _ in range(35):
    before=time.perf_counter();duel.arena.grab();times.append((time.perf_counter()-before)*1000)
assert duel.game.streak()==5 and duel.arena.fire_cache.bytes>0
fire_bytes=duel.arena.fire_cache.bytes
if settings['language']=='en':win.grab().save(str(out/'duel-fire.png'))
net=shell.pages['net'];net.node.radio_allowed=lambda *args:True;shell.show_page('net');ui_anim._finish(shell.stack)
buf=io.BytesIO();Image.new('RGB',(144,144),'#3478b4').save(buf,format='JPEG')
cfg={'name':'Night Signals','avatar':base64.b64encode(buf.getvalue()).decode(),'genre':'Electronic','repeat':True,
     'queue':[{'id':str(i),'track':track} for i,track in enumerate(tracks[:4])]}
dialog=StationDialog(net,cfg);dialog.show();settle(.2)
assert dialog.queue.count()==4
dialog.source.setCurrentIndex(dialog.source.findData('Signals'));assert dialog.library.count()==8
dialog.library.setCurrentRow(4);dialog.add_selected();assert dialog.queue.count()==5
dialog.queue.setCurrentRow(4);dialog.move(-1);assert dialog.queue.item(3).data(Qt.ItemDataRole.UserRole)['track']['title']==tracks[4]['title']
if settings['language']=='en':dialog.grab().save(str(out/'radio-editor.png'))
dialog.save();assert dialog.result_config['name']=='Night Signals'
station=Station(dialog.result_config)
net.node.settings['radio_station']=station.config()
net.node.room_host={'title':'Night Signals','station':station,'state':station.state(),'public':True,'share':True}
net.node.room_members=set();net.show_tab('room');net._fill_room();settle(.25)
queue=next(w for w in net.room_page.findChildren(QListWidget) if w.count()==5)
assert 'Afterglow' in queue.item(0).text()
assert net.room_page.findChildren(QLabel)
if settings['language']=='en':win.grab().save(str(out/'radio-live.png'))
before=queue
net._on_event_impl('room',net.node.room_view());assert not before.isHidden(),'Position-only update must retain queue widget'
guest_state={**station.state(),'title':'Night Signals','members':['Host','Listener'],'share':True}
net.node.room_host=None;net.node.room_guest='demo-host';net.node.room_state=guest_state
net._fill_room();settle(.2)
assert any(w.count()==5 for w in net.room_page.findChildren(QListWidget))
assert not any(b.isVisible() and b.text() in ('Station and queue','Станция и очередь') for b in net.room_page.findChildren(QPushButton))
net.node.room_guest=None;net.node.room_state=None
net.node.nostr=NS(up_count=0)
net.node.public['rooms']={('demo','station'):{'onion':'demo-host','npub':'demo','title':'Night Signals','radio':cfg,
    'genre':'Electronic','now':tracks[0],'n':12,'exp':time.time()+300,'ts':time.time()}}
net._fill_room();settle(.15)
assert any(label.text()=='Night Signals' for label in net.room_page.findChildren(QLabel))
assert not errors,errors
result={'language':settings['language'],'focused_ui_checks':['editor add/reorder/save','host on-air queue','position update retains widgets',
    'guest sees station and queue','public station branding','native fire render'],
    'arena_mean_ms':round(statistics.mean(times),2),'arena_p95_ms':round(sorted(times)[33],2),
    'fire_recipe_bytes':fire_bytes,'hidden_fire_recipe_bytes':duel.arena.fire_cache.bytes,'python_errors':errors}
(ROOT/f"qa/results/radio_fire_ui_{settings['language']}.json").write_text(json.dumps(result,indent=2),'utf-8')
print(json.dumps(result),flush=True)
net.node.nostr=None;win._really_quit=True;win.close();settle(.1);os._exit(0)
