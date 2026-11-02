"""Real collection startup with BOM/recovered JSON, isolated profile and blocked network."""
import argparse
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
import wave

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
parser=argparse.ArgumentParser();parser.add_argument('--case',choices=('bom','backup'),required=True)
case=parser.parse_args().case
os.environ['QT_QPA_PLATFORM']='offscreen'
runtime=Path(os.environ['LOCALAPPDATA'])/'Programs/ECHOES'
os.environ['PYTHON_VLC_LIB_PATH']=str(runtime/'vlc/libvlc.dll')
os.environ['ECHOES_AUDIO_BACKEND']='vlc'
os.environ['OPENBLAS_NUM_THREADS']='1';os.environ['OMP_NUM_THREADS']='1'
temporary=tempfile.TemporaryDirectory(prefix='echoes-library-startup-',ignore_cleanup_errors=True)
home=Path(temporary.name);Path.home=classmethod(lambda cls:home)
data=home/'.neon_player';data.mkdir()
(data/'settings.json').write_text(json.dumps(dict(theme='Echoes Music',language='en',ya_page='collection',
    own_audio_output=False,ui_animations=True,ya_decor={},ya_skin={},discord_rpc=False,media_session=False,
    close_to_tray=False,intro_enabled=False,voice_cmd_always=False,auto_tags=False)),'utf-8')
tracks=[]
for index in range(24):
    path=home/f'synthetic-{index}.wav'
    with wave.open(str(path),'wb') as output:
        output.setparams((1,2,8000,0,'NONE','not compressed'));output.writeframes(b'\0\0'*80)
    tracks.append(dict(path=str(path),title=f'Synthetic {index}',artist='Synthetic Artist',duration=120))
library=data/'library.json'
if case=='bom':library.write_text(json.dumps(tracks),'utf-8-sig')
else:
    library.write_bytes(b'[{"path":"synthetic interrupted')
    library.with_name(library.name+'.bak').write_text(json.dumps(tracks),'utf-8')
original=library.read_bytes()

def offline(*args,**kwargs):raise OSError('offline library startup probe')
socket.socket.connect=offline;socket.create_connection=offline
socket.getaddrinfo=offline;socket.gethostbyname=offline
from PyQt6.QtWidgets import QApplication
import i18n
i18n.install('en')
app=QApplication([]);app.setStyle('Fusion');i18n.start(app)
import main,ya_decor,ui_anim
main.WindowsMediaKeyFilter.register=lambda self:None
ya_decor.Decor._taskbar_init=lambda self:None
errors=[]
sys.excepthook=lambda kind,value,tb:errors.append(dict(type=kind.__name__,message=str(value)))
win=None
try:
    win=main.MainWindow();win.resize(1440,950);win.show()
    deadline=time.perf_counter()+.5
    while time.perf_counter()<deadline:app.processEvents();time.sleep(.001)
    shell=win._ya
    report=dict(case=case,scope='Only real startup and initial collection; no navigation matrix',
        expected_tracks=len(tracks),library_tracks=len(win.library),collection_rows=shell.c_list.model_.rowCount(),
        selected_page=shell._cur_page,filter=list(shell._coll_filter),search=shell.c_search.text(),
        source_unchanged=library.read_bytes()==original,python_errors=errors,
        network='blocked',audio='not played',profile='isolated temporary profile')
    report['passed']=(report['library_tracks']==24 and report['collection_rows']==24
        and report['selected_page']=='collection' and report['source_unchanged'] and not errors)
finally:
    if win is not None:
        node=win._ya.pages['net'].node;store=getattr(node,'store',None)
        win._really_quit=True;win.close()
        for _ in range(5):app.processEvents()
        if store is not None:store.db.close()
    temporary.cleanup()
target=ROOT/'qa/results'/('library-startup-'+case+'.json')
target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(report,indent=2)+'\n','utf-8')
print('LIBRARY_STARTUP_RESULT '+json.dumps(report))
if not report['passed']:sys.exit(1)
