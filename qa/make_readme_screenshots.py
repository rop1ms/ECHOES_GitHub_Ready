"""Real ECHOES widgets, original cover artwork and an isolated offline demo profile."""
import os
os.environ['ECHOES_QA_LANGUAGE']='en'
from pathlib import Path
source=Path(__file__).with_name('ui_smoke.py').read_text('utf-8')
exec(compile(source.split('win = None')[0],str(Path(__file__).with_name('ui_smoke.py')),'exec'))
from PyQt6.QtCore import QRectF, Qt, QPointF
from PyQt6.QtGui import QColor, QPainter, QPen, QLinearGradient, QPixmap, QPainterPath
from PyQt6.QtWidgets import QLabel, QAbstractButton
import main, ui_anim

main.WindowsMediaKeyFilter.register=lambda self:None
win=main.MainWindow()
win.resize(1600,1000)
win.settings['nickname']='ECHOES'
win.settings['net_name']='ECHOES'
win.show();settle(.5)
shell=win._ya
out=ROOT/'docs/screenshots';out.mkdir(parents=True,exist_ok=True)
art=audit_home/'art';art.mkdir()
titles=['AFTERGLOW','ORBITAL','BLUE HOUR','DAYBREAK','PARALLAX','CHROMA','SLOW MOTION','NIGHT DRIVE','SOLSTICE','DRIFT','AURORA','IN BLOOM']
palettes=[('#fa6d4c','#592e89'),('#31d9d1','#123e88'),('#718cff','#27234d'),('#ffcf79','#ac3667'),('#9d80fa','#262e73'),('#ff5caf','#6029bd')]
tracks=[]
for index,title in enumerate(titles):
    pix=QPixmap(640,640);pix.fill(QColor('#10121d'))
    p=QPainter(pix);p.setRenderHint(QPainter.RenderHint.Antialiasing)
    a,b=palettes[index%len(palettes)]
    gradient=QLinearGradient(0,0,640,640);gradient.setColorAt(0,QColor(b));gradient.setColorAt(.55,QColor(a));gradient.setColorAt(1,QColor('#10121c'))
    p.fillRect(pix.rect(),gradient)
    p.setBrush(Qt.BrushStyle.NoBrush)
    for k in range(16):
        color=QColor('#ffffff');color.setAlpha(15+k*5)
        p.setPen(QPen(color,1.5))
        if index%3==0:p.drawEllipse(QRectF(65+k*7,45+k*7,515-k*16,515-k*16))
        elif index%3==1:
            p.save();p.translate(320,260);p.rotate(k*6+20);p.drawRoundedRect(QRectF(-190+k*4,-190+k*4,380-k*8,380-k*8),22,22);p.restore()
        else:
            path=QPainterPath();path.moveTo(-40,150+k*17);path.cubicTo(160,25+k*23,410,470-k*13,700,200+k*13);p.drawPath(path)
    p.setPen(QColor('#ffffff'));p.setFont(QFont('Segoe UI',12,QFont.Weight.DemiBold));p.drawText(40,55,'E C H O E S   /   S E S S I O N S')
    p.setFont(QFont('Segoe UI',31,QFont.Weight.Bold));p.drawText(QRectF(36,475,570,80),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,title)
    p.setFont(QFont('Segoe UI',12));p.drawText(40,594,f'VOL. {index+1:02d}      •      ORIGINAL DEMO ARTWORK')
    p.end();cover=art/f'{index}.png';pix.save(str(cover))
    tracks.append({'path':str(audit_home/f'{index}.wav'),'title':title.title(),'artist':['ECHOES Sessions','Neon Atlas','Lunar Fields'][index%3],
                   'album':'Signals / Vol. '+str(index//4+1),'duration':180+index*11,'cover':str(cover),'genre':['Electronic','Ambient','Synthwave'][index%3]})
win.library=tracks
win.playlists={name:{'tracks':[t['path'] for t in part],'cover':part[0]['cover']} for name,part in [('Signals',tracks[:6]),('Night Drive',tracks[6:]),('Liked songs',tracks[::2])]}
shell._dirty.update(shell.pages)
shell.p_title.setText('Afterglow');shell.p_artist.set_artists('ECHOES Sessions') if hasattr(shell.p_artist,'set_artists') else None
shell.p_artist.set_track(tracks[0])
shell.p_cover.set_cover(tracks[0]['cover']) if hasattr(shell.p_cover,'set_cover') else None
import ya_foryou as F
F.load_cache=lambda:{'trends':{'ts':time.time(),'data':{'world':[dict(t,cover_url=t['cover']) for t in tracks]}}}
F.stale=lambda *args:(False,False)
shell._fy_cover=lambda card,url:setattr(card,'cover',url)

missing=set()
def screenshot(name,widget=win):
    settle(.65)
    for child in widget.findChildren(QLabel)+widget.findChildren(QAbstractButton):
        text=child.text()
        if child.isVisible() and i18n._CYR.search(text) and i18n._CYR.search(i18n.tr(text)): missing.add(text)
    widget.grab().save(str(out/(name+'.png')))
    print('Saved',name,flush=True)

for key,name in [('collection','music'),('foryou','discover')]:
    shell._dirty.add(key);shell.show_page(key);ui_anim._finish(shell.stack);screenshot(name)
shell.show_page('duel');ui_anim._finish(shell.stack)
duel=shell.pages['duel'];duel.game.start(tracks);duel.arena.set_pair(duel.game.current(),'none');duel.render();screenshot('duel')
duel.arena.timer.stop()
shell.show_page('net');ui_anim._finish(shell.stack)
net=shell.pages['net'];store=net.node.store
for index,(title,text) in enumerate([
    ('ECHOES Sessions','A new place for your music. Share a discovery, build a listening room, and let your friends hear what moves you.'),
    ('Neon Atlas','Tonight’s session: ambient textures, analog synths, and a little Afterglow. What would you add to the queue?'),
    ('Lunar Fields','Twelve tracks. One champion. Our next music duel starts here — bring your favorite discovery.')]):
    store.put_post({'id':f'demo-post-{index}','author':net.node.me.onion if index==0 else f'demo-peer-{index}',
                    'name':title,'ts':time.time()-index*1800,'text':text,'kind':'post','track':json.dumps(tracks[index])})
    if index:store.upsert_contact(f'demo-peer-{index}',name=title,status='friend')
net._data_ver=net.__dict__.get('_data_ver',0)+1
net.show_tab('feed');screenshot('network')

import ya_skin
editor=ya_skin.SkinEditor(win,win);editor.resize(1220,850);editor.show();screenshot('appearance',editor);editor.close()
from voice_editor import CommandsDialog
win.settings['voice_commands']=[{'id':'demo-evening','name':'Evening mode','on':True,'phrases':['evening mode','my evening'],
                                 'steps':[{'a':'volume','v':35},{'a':'playlist','name':'Night Drive'},{'a':'notify','text':'Your evening soundtrack is ready.'}]}]
dialog=CommandsDialog(win);dialog.resize(1200,800);dialog.show();screenshot('voice',dialog);dialog.reject()
try:
    import theme_studio
    designer=theme_studio.ThemeStudio(win);designer.resize(1000,900);designer.show();screenshot('designer',designer);designer._app_closing=True;designer.close()
except Exception as exc:print('Designer:',repr(exc),flush=True)
try:
    win._apply_theme('Echoes Studio');settle(.8)
    studio=win._daw
    import daw_project as PR
    pattern=studio.p['patterns'][0];pattern['name']='Afterglow / drums'
    for index,channel in enumerate(studio.p['channels']):
        pattern['notes'][channel['id']]=[[step*.25,.2,60,.75] for step in range(index%3,16,4 if index<2 else 2)]
    studio.p['name']='Afterglow Sessions'
    for index in range(6):
        studio.p['clips'].append({'id':PR.uid(),'kind':'pat','ref':pattern['id'],'lane':index%3,'start':index*4.,'len':4.,'off':0.,'vol':1.})
    studio.rack.rebuild();studio.playlist.refresh();studio.roll.refresh();studio._update_title()
    studio.workspace_preset('arrange')
    screenshot('studio')
except Exception as exc:print('Studio:',repr(exc),flush=True)

(ROOT/'qa/results/screenshot_translation_gaps.json').write_text(json.dumps(sorted(missing),ensure_ascii=False,indent=2),'utf-8')
print('Translation gaps',len(missing),flush=True)
if getattr(win,'_daw',None):win._daw.modified=False
win._really_quit=True;win.close();settle(.2)
os._exit(0)
