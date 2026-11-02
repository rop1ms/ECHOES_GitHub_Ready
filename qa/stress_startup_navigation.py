"""Broad startup/navigation UI audit, isolated data, no audio or network use."""
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
import traceback
import wave
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
os.environ['QT_QPA_PLATFORM']='offscreen'
runtime=Path(os.environ['LOCALAPPDATA'])/'Programs/ECHOES'
os.environ['PYTHON_VLC_LIB_PATH']=str(runtime/'vlc/libvlc.dll')
os.environ['ECHOES_AUDIO_BACKEND']='vlc'
os.environ['OPENBLAS_NUM_THREADS']='1';os.environ['OMP_NUM_THREADS']='1'
temporary=tempfile.TemporaryDirectory(prefix='echoes-startup-stress-',ignore_cleanup_errors=True)
home=Path(temporary.name);Path.home=classmethod(lambda cls:home)
data=home/'.neon_player';data.mkdir()
settings=dict(theme='Echoes Music',language='en',ya_page='collection',own_audio_output=False,
    ui_animations=True,ya_decor={},ya_skin={},discord_rpc=False,media_session=False,
    close_to_tray=False,intro_enabled=False,voice_cmd_always=False,auto_tags=False)
(data/'settings.json').write_text(json.dumps(settings),'utf-8')
tracks=[]
for index in range(400):
    path=home/f'synthetic-{index}.wav'
    with wave.open(str(path),'wb') as output:
        output.setparams((1,2,8000,0,'NONE','not compressed'));output.writeframes(b'\0\0'*80)
    tracks.append(dict(path=str(path),title=f'Song {index}',artist=f'Artist {index%20}',
                       album=f'Album {index%10}',duration=180))
(data/'library.json').write_text(json.dumps(tracks),'utf-8')
(data/'playlists.json').write_text(json.dumps({'Мне нравится':{'tracks':tracks[::10],'desc':'Synthetic likes','cover':''}}),'utf-8')

def offline(*args,**kwargs):raise OSError('offline startup navigation audit')
socket.socket.connect=offline;socket.create_connection=offline
socket.getaddrinfo=offline;socket.gethostbyname=offline
from PyQt6 import sip
from PyQt6.QtCore import Qt,qInstallMessageHandler
from PyQt6.QtWidgets import QApplication,QWidget,QPushButton
import i18n
i18n.install('en')
app=QApplication([]);app.setStyle('Fusion');i18n.start(app)
import main,ui_anim,ya_decor,frameclock
main.WindowsMediaKeyFilter.register=lambda self:None
ya_decor.Decor._taskbar_init=lambda self:None
errors=[];qt_messages=[];checks=[];failures=[];samples=[]
def exception(kind,value,tb):
    trace=''.join(traceback.format_exception(kind,value,tb))[-3000:]
    trace=trace.replace(str(ROOT),'<SOURCE>').replace(str(home),'<TEMP_PROFILE>')
    errors.append(dict(type=kind.__name__,message=str(value),trace=trace))
sys.excepthook=exception
def qt_message(kind,context,message):
    message=message.replace(str(runtime).replace('\\','/'),'<ECHOES_RUNTIME>')
    if len(qt_messages)<120:qt_messages.append(dict(type=kind.value,message=message))
qInstallMessageHandler(qt_message)
win=main.MainWindow();win.resize(1440,950);win.show();shell=win._ya
started=time.perf_counter();switches=0

def pump(seconds=.025):
    deadline=time.perf_counter()+seconds
    while time.perf_counter()<deadline:
        app.processEvents();time.sleep(.001)

def check(name,condition,**detail):
    entry=dict(name=name,passed=bool(condition),**detail);checks.append(entry)
    if not condition:failures.append(entry);print('FAIL '+json.dumps(entry),flush=True)

def model_count():return shell.c_list.model_.rowCount()

def collection_checkpoint(name,expected=400):
    shell.show_page('collection');pump(.04)
    check(name,model_count()==expected and len(win.library)==400,
          rows=model_count(),library=len(win.library),filter=list(shell._coll_filter),search=shell.c_search.text(),
          list_visible=shell.c_list.isVisible(),viewport=[shell.c_list.viewport().width(),shell.c_list.viewport().height()])

def switch(key,turn=True):
    global switches
    start=time.perf_counter();shell.show_page(key)
    if turn:pump(.008)
    samples.append((time.perf_counter()-start)*1000);switches+=1
    check('selected_'+key,shell.stack.currentWidget() is shell.pages[key],roundtrip=switches)

def select_chip(text):
    for index in range(shell.c_chips.count()):
        widget=shell.c_chips.itemAt(index).widget()
        if isinstance(widget,QPushButton) and widget.text()==text:
            widget.click();return True
    return False

try:
    pump(.5)
    collection_checkpoint('startup_preloaded_collection')
    music=[key for key in ('wave','search','foryou','collection','albums','follows','tierlist','duel','net','profile','queue','lyrics','settings') if key in shell.pages]
    for key in music:
        switch(key);overlay=ui_anim._OVERLAYS.get(id(shell.stack))
        if overlay is not None:overlay._prepare()
        page=shell.pages[key]
        check('cold_geometry_'+key,page.width()>100 and page.height()>100,size=[page.width(),page.height()])
    collection_checkpoint('collection_after_first_visits')

    # Repeated navigation with actual event turns, followed by coalesced bursts.
    for cycle in range(8):
        for key in music:switch(key)
        collection_checkpoint('collection_roundtrip_'+str(cycle))
    for cycle in range(20):
        for key in ('queue','collection','profile','duel','albums'):switch(key,False)
        pump(.01)
    collection_checkpoint('collection_after_rapid_bursts')
    pump(.5)
    check('no_frozen_overlay_after_idle',len(ui_anim._OVERLAYS)==0,overlays=len(ui_anim._OVERLAYS))

    shell.show_page('collection');pump(.02)
    check('liked_chip_exists',select_chip('Мне нравится'))
    pump(.01);check('liked_rows',model_count()==40,rows=model_count())
    check('all_chip_exists',select_chip('Всё'))
    shell.c_search.setText('definitely-no-synthetic-result');pump(.01)
    check('empty_search_is_intentional',model_count()==0,rows=model_count())
    shell.c_search.clear();pump(.01)
    collection_checkpoint('clear_search_restores_collection')
    shell.c_search.setText('Song 12');pump(.01)
    count=model_count();check('search_has_matches',0<count<400,rows=count)
    switch('profile');switch('collection')
    check('search_state_retained',shell.c_search.text()=='Song 12' and model_count()==count,rows=model_count())
    shell.c_search.clear();pump(.01)

    # Changes that used to invalidate/replace a prepared page snapshot.
    for width,height in ((1080,720),(1600,1050),(1280,850)):
        switch('queue',False);win.resize(width,height);pump(.05)
        collection_checkpoint('collection_after_resize_'+str(width))
    for code in ('ru','en'):
        index=win.lang_cb.findData(code);win.lang_cb.setCurrentIndex(index);pump(.03)
        collection_checkpoint('collection_language_'+code)
        check('language_'+code,i18n.LANG==code,actual=i18n.LANG)
    win.showMinimized();pump(.03);win.showNormal();win.activateWindow();pump(.3)
    collection_checkpoint('collection_after_restore')

    # Actual cached network page navigation, without contacting anyone.
    switch('net');net=shell.pages['net']
    net_keys=[key for key in ('feed','chats','friends','room','threads','net','profile') if key in net.tabs]
    for cycle in range(5):
        for key in net_keys:
            net.show_tab(key);pump(.01)
            check('net_selected_'+key,net.stack.currentWidget() is net.tabs[key],cycle=cycle)
    pump(.5)
    check('net_no_frozen_overlay_after_idle',len(ui_anim._OVERLAYS)==0,overlays=len(ui_anim._OVERLAYS))
    collection_checkpoint('collection_after_net_stress')

    # Async metadata/analysis and importer completion must not leave a stale
    # snapshot or turn an otherwise healthy collection into an empty view.
    shell.show_page('collection');pump(.02)
    replacements=[dict(track,title='Retagged '+str(index)) for index,track in enumerate(win.library)]
    win.library=replacements;win._save_library();shell.on_tracks_changed();pump(.025)
    rows=shell.c_list.model_.rows
    check('same_size_replacement_refreshes_track_identity',len(rows)==400 and
          all(row['t']['title'].startswith('Retagged ') for row in rows),rows=len(rows))
    win.library=[dict(track,title='Song '+str(index)) for index,track in enumerate(win.library)]
    win._save_library();shell.on_tracks_changed();pump(.025)

    shell.show_page('profile');pump(.01);shell.show_page('collection')
    overlay=ui_anim._OVERLAYS.get(id(shell.stack))
    if overlay is not None:overlay._prepare()
    check('late_import_fixture_has_active_overlay',overlay is not None and not overlay._finished)
    extra=dict(win.library[0],path=str(home/'late-import.wav'),title='Late imported song')
    Path(extra['path']).write_bytes(Path(win.library[0]['path']).read_bytes())
    win.library.append(extra);win._save_library();shell.on_tracks_changed();pump(.02)
    check('late_import_updates_visible_model',model_count()==401,rows=model_count())
    check('late_import_clears_stale_snapshot',overlay is None or overlay._finished,
          active_overlays=len(ui_anim._OVERLAYS))
    check('late_import_persisted',len(json.loads(main.LIB_FILE.read_text('utf-8')))==401)
    win.library.pop();win._save_library();shell.on_tracks_changed();pump(.02)

    select_chip('Мне нравится');pump(.01)
    shell._on_profiles({track['path']:{'families':['rock'],'energy':.5,'valence':.5,'tempo':120}
                        for track in win.library})
    pump(.02)
    check('late_analysis_keeps_liked_filter',shell._coll_filter==('liked',None) and model_count()==40,
          rows=model_count(),filter=list(shell._coll_filter))
    select_chip('Всё');shell.c_search.setText('Song 12');pump(.01)
    search_count=model_count();shell._on_profiles({});pump(.02)
    check('late_analysis_keeps_search_results',model_count()==search_count and search_count>0,
          before=search_count,after=model_count(),search=shell.c_search.text())
    shell.c_search.clear();pump(.01)

    # Simulate the initially empty view before its first successful rebuild.
    shell.c_list.set_rows([])
    shell._profiles=None
    with patch.object(win,'_ya_profiles',side_effect=RuntimeError('synthetic optional analysis unavailable')):
        shell._dirty.add('collection');shell.show_page('collection');pump(.02)
        check('optional_analysis_failure_preserves_collection',model_count()==400,rows=model_count())
    fail=RuntimeError('synthetic transient page build failure')
    with patch.object(shell,'_fill_collection',side_effect=fail):
        try:shell._rebuild('collection')
        except RuntimeError:pass
        check('failed_build_remains_retryable','collection' in shell._dirty)
    collection_checkpoint('collection_recovers_after_transient_build_failure')

    # Button-group selection changes before the clicked handler runs: failed
    # navigation must restore the previously visible page and button together.
    ui_anim._finish(shell.stack)
    previous_page=shell.stack.currentWidget();previous_key=shell._cur_page
    previous_history=list(shell.__dict__.get('_page_hist',[]));previous_setting=win.settings.get('ya_page')
    expected_failures=[]
    shell._dirty.add('albums')
    with patch.object(shell,'_fill_albums',side_effect=RuntimeError('synthetic failed album navigation')),\
         patch.object(sys,'excepthook',lambda kind,value,tb:expected_failures.append(str(value))):
        shell._nav_btns['albums'].click();pump(.01)
    check('failed_navigation_retains_visible_page',shell.stack.currentWidget() is previous_page and shell._cur_page==previous_key,
          selected=shell._cur_page)
    check('failed_navigation_restores_checked_button',shell.nav.checkedButton() is shell._nav_btns.get(previous_key))
    check('failed_navigation_does_not_commit_history_or_saved_page',
          list(shell.__dict__.get('_page_hist',[]))==previous_history and win.settings.get('ya_page')==previous_setting,
          history_before=previous_history,history_after=list(shell.__dict__.get('_page_hist',[])),
          saved_page_before=previous_setting,saved_page_after=win.settings.get('ya_page'))
    shell._nav_btns['albums'].click();pump(.02)
    check('failed_navigation_retry_succeeds',shell.stack.currentWidget() is shell.pages['albums'] and shell._cur_page=='albums')
    collection_checkpoint('collection_after_failed_navigation_retry')
    check('library_persistence_intact',len(json.loads(main.LIB_FILE.read_text('utf-8')))==400)
    check('no_uncaught_ui_exceptions',not errors,count=len(errors))
except Exception as exc:
    exception(type(exc),exc,exc.__traceback__)
    check('scenario_completed',False,error=str(exc))
finally:
    node=shell.pages['net'].node;store=getattr(node,'store',None)
    # Allow a just-requested analysis job to commit before deleting the test
    # profile. Real profiles persist after closing; deleting one too early
    # would manufacture a cleanup-only FileNotFoundError in the worker.
    deadline=time.perf_counter()+.75
    while getattr(win,'_intel_prof_busy',False) and time.perf_counter()<deadline:pump(.01)
    intel_worker=getattr(win,'_intel_worker',None)
    win._really_quit=True;win.close();pump(.05)
    if intel_worker is not None and intel_worker.is_alive():intel_worker.join(timeout=2)
    if store is not None:
        try:store.db.close()
        except Exception:pass
    temporary.cleanup()

report=dict(scope='Isolated real-app startup, repeated music/net tabs, filters/language/resize/restore; no radio/editor/full playback scenario',
    app_version_at_start=(ROOT/'app/version.txt').read_text('utf-8').strip(),
    fixture=dict(tracks=400,liked=40,network='blocked',audio='not played',profile='temporary',
                 real_user_data='not used'),music_switches=switches,checks=len(checks),passed=sum(item['passed'] for item in checks),
    failures=failures,uncaught=errors,qt_messages=qt_messages,
    selected_switch_elapsed_ms=dict(median=round(sorted(samples)[len(samples)//2],3),maximum=round(max(samples),3)),
    elapsed_seconds=round(time.perf_counter()-started,3),
    limitations='Offscreen lifecycle/invariant stress, not measured on-screen FPS; real network, audio playback, radio and native editor excluded.')
out=ROOT/'qa/results/startup-navigation-stress-2026.11.02-5.json';out.parent.mkdir(parents=True,exist_ok=True)
out.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n','utf-8')
print('STRESS_RESULT '+json.dumps({k:v for k,v in report.items() if k not in ('qt_messages','uncaught')}),flush=True)
if failures or errors:sys.exit(1)
