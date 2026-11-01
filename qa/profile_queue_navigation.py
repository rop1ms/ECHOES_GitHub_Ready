"""Offline disposable-profile navigation probe; never plays audio or opens accounts."""
import json
import os
from pathlib import Path
import socket
import statistics
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
os.environ['QT_QPA_PLATFORM']='offscreen'
runtime=Path(os.environ['LOCALAPPDATA'])/'Programs/ECHOES'
os.environ['PYTHON_VLC_LIB_PATH']=str(runtime/'vlc/libvlc.dll')
os.environ['ECHOES_AUDIO_BACKEND']='vlc'
os.environ['OPENBLAS_NUM_THREADS']='1'
os.environ['OMP_NUM_THREADS']='1'
temporary=tempfile.TemporaryDirectory(prefix='echoes-navigation-',ignore_cleanup_errors=True)
home=Path(temporary.name); Path.home=classmethod(lambda cls:home)
data=home/'.neon_player';data.mkdir()
(data/'settings.json').write_text(json.dumps(dict(theme='Echoes Music',language='en',ya_page='collection',
    own_audio_output=False,ui_animations=False,ya_decor={},ya_skin={},discord_rpc=False,media_session=False,
    close_to_tray=False,intro_enabled=False,voice_cmd_always=False,auto_tags=False)), 'utf-8')
def offline(*args,**kwargs):raise OSError('offline navigation probe')
socket.socket.connect=offline;socket.create_connection=offline
from PyQt6.QtWidgets import QApplication
import i18n
i18n.install('en')
app=QApplication([]);app.setStyle('Fusion');i18n.start(app)
import main
import ui_anim
import ya_decor
main.WindowsMediaKeyFilter.register=lambda self:None
ya_decor.Decor._taskbar_init=lambda self:None
win=main.MainWindow();win.resize(1440,950);win.show()
for _ in range(15):app.processEvents()
shell=win._ya
def cleanup():
    node=shell.pages['net'].node
    store=getattr(node,'store',None)
    win.engine.stop();win._really_quit=True;win.close()
    for _ in range(5):app.processEvents()
    if store is not None:store.db.close()
    temporary.cleanup()
if '--cleanup-only' in sys.argv:
    cleanup();print('CLEANUP_OK',flush=True);sys.exit(0)
shell._timer.stop()  # isolate navigation from unrelated polling
win.library=[dict(path=f'probe-{i}',title=f'Song {i}',artist=f'Artist {i%30}',duration=180) for i in range(2500)]
win.current_index=0;win.queue_context='library';win.user_queue=win.library[20:40]
shell._dirty.update({'collection'})
shell.show_page('collection');shell.show_page('queue');app.processEvents()
queue=shell.pages['queue'];refresh=queue.refresh
results={}
for mode in ('forced_rebuild','reuse'):
    queue.refresh=(lambda:refresh(force=True)) if mode=='forced_rebuild' else refresh
    samples=[]
    for _ in range(12):
        start=time.perf_counter()
        shell.show_page('collection');shell.show_page('queue')
        app.processEvents()
        samples.append((time.perf_counter()-start)*1000)
    results[mode]={'median_ms':round(statistics.median(samples),3),'max_ms':round(max(samples),3)}
queue.refresh=refresh

# A real decorative background previously disappeared from stack-only captures.
win.settings['ui_animations']=True
ya_decor._CFG['aura']=True;ya_decor._CFG['glass']='clear'
shell.decor.pal=['#ff3399','#2299ff'];shell.decor._aura_pm=None
shell.show_page('collection');ui_anim._finish(shell.stack);app.processEvents()
stack=shell.stack
origin=stack.mapTo(win,stack.rect().topLeft())
native=win.grab().copy(origin.x(),origin.y(),stack.width(),stack.height()).toImage()
capture=ui_anim._grab_area(stack).toImage()
points=[]
for x,y in ((stack.width()-32,stack.height()-32),(stack.width()//2,stack.height()-40)):
    a=native.pixelColor(round(x*native.devicePixelRatio()),round(y*native.devicePixelRatio()))
    b=capture.pixelColor(int(x*capture.width()/stack.width()),int(y*capture.height()/stack.height()))
    points.append({'point':[x,y],'native':a.name(),'capture':b.name(),'max_channel_delta':max(abs(a.red()-b.red()),abs(a.green()-b.green()),abs(a.blue()-b.blue()))})
results['background']=points

start=time.perf_counter()
for _ in range(8):
    shell.show_page('queue');shell.show_page('collection')
rapid_ms=(time.perf_counter()-start)*1000
overlay=ui_anim._OVERLAYS.get(id(stack))
if overlay is not None:
    overlay._prepare()
    image=overlay.new.toImage()
    results['rapid']={'switches':16,'input_burst_ms':round(rapid_ms,3),'active_overlays':len(ui_anim._OVERLAYS),
                      'prepared_extent':[image.width(),image.height()]}
ui_anim._finish(stack)
print('NAVIGATION_RESULT '+json.dumps(results),flush=True)
cleanup()
