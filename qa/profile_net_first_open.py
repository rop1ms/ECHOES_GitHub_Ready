"""Four cold Echoes.NET tabs in a disposable offline profile; no audio or contacts."""
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
os.environ['QT_QPA_PLATFORM']='offscreen'
runtime=Path(os.environ['LOCALAPPDATA'])/'Programs/ECHOES'
os.environ['PYTHON_VLC_LIB_PATH']=str(runtime/'vlc/libvlc.dll')
os.environ['ECHOES_AUDIO_BACKEND']='vlc'
os.environ['OPENBLAS_NUM_THREADS']='1';os.environ['OMP_NUM_THREADS']='1'
temporary=tempfile.TemporaryDirectory(prefix='echoes-net-first-',ignore_cleanup_errors=True)
home=Path(temporary.name);Path.home=classmethod(lambda cls:home)
data=home/'.neon_player';data.mkdir()
(data/'settings.json').write_text(json.dumps(dict(theme='Echoes Music',language='en',ya_page='collection',
    own_audio_output=False,ui_animations=False,ya_decor={},ya_skin={},discord_rpc=False,media_session=False,
    close_to_tray=False,intro_enabled=False,voice_cmd_always=False,auto_tags=False)),'utf-8')
def offline(*args,**kwargs):raise OSError('offline cold net navigation probe')
socket.socket.connect=offline;socket.create_connection=offline
from PyQt6.QtWidgets import QApplication,QWidget
from PyQt6 import sip
import i18n
i18n.install('en')
app=QApplication([]);app.setStyle('Fusion');i18n.start(app)
import main,ui_anim,ya_decor,frameclock
main.WindowsMediaKeyFilter.register=lambda self:None
ya_decor.Decor._taskbar_init=lambda self:None
win=main.MainWindow();win.resize(1440,950);win.show()
for _ in range(12):app.processEvents()
shell=win._ya;shell._timer.stop()
win.settings['ui_animations']=True
frameclock.clock().set_paused(True)  # prepare exact captured frame explicitly
shell.show_page('net')
outer=ui_anim._OVERLAYS.get(id(shell.stack))
if outer is not None:outer._prepare();outer.finish()
net=shell.pages['net']
net._tick_t.stop();net._pres_tick.stop()
results=[]
profiling='--profile-settings' in sys.argv
if '--no-device-bootstrap' in sys.argv:
    import net_page
    def no_native_bootstrap(self,combo,key,selected):
        combo.addItem('Windows default',-1)
    net_page._VoiceDevices.bind=no_native_bootstrap

def geometry(page):
    return {id(w):(w,w.geometry().getRect()) for w in [page]+page.findChildren(QWidget) if w.isVisible()}
try:
    for key in (('net',) if profiling else ('friends','net','room','threads')):
        if profiling:
            import cProfile
            profile=cProfile.Profile();profile.enable()
        started=time.perf_counter();net.show_tab(key);built=time.perf_counter()
        overlay=ui_anim._OVERLAYS.get(id(net.stack))
        if overlay is None:raise AssertionError('missing network transition for '+key)
        overlay._prepare()
        prepared=time.perf_counter()
        if profiling:
            profile.disable()
            import pstats
            stats=pstats.Stats(profile)
            top=[]
            for (filename,line,name),(cc,nc,tt,ct,callers) in sorted(stats.stats.items(),key=lambda item:item[1][3],reverse=True)[:12]:
                top.append(dict(file=Path(filename).name,line=line,function=name,calls=nc,total_ms=round(tt*1000,3),cumulative_ms=round(ct*1000,3)))
        page=net.tabs[key];before=geometry(page)
        for _ in range(5):app.processEvents()
        changed=[]
        for _,(w,rect) in before.items():
            if not sip.isdeleted(w) and w.isVisible() and rect!=w.geometry().getRect():
                changed.append(dict(type=type(w).__name__,before=list(rect),after=list(w.geometry().getRect())))
        results.append(dict(tab=key,visible_widgets=len(before),geometry_changes=changed,
                            overlay_extent=[overlay.width(),overlay.height()],
                            preparation_ms=round((time.perf_counter()-started)*1000,3)))
        if profiling:
            results[-1].update(build_ms=round((built-started)*1000,3),capture_prepare_ms=round((prepared-built)*1000,3),process_events_ms=round((time.perf_counter()-prepared)*1000,3),profile_top=top)
        overlay.finish()
    print('NET_FIRST_OPEN '+json.dumps(results),flush=True)
finally:
    node=net.node;store=getattr(node,'store',None)
    win._really_quit=True;win.close()
    for _ in range(5):app.processEvents()
    if store is not None:store.db.close()
    temporary.cleanup()
if any(item['geometry_changes'] for item in results):sys.exit(1)
