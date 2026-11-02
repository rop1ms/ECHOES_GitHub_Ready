"""Cold page geometry only, in a disposable offline profile. No playback or accounts."""
import json
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
# Reuse only the isolated MainWindow setup and cleanup from the navigation probe.
fixture=ROOT/'qa/profile_queue_navigation.py'
exec(compile(fixture.read_text('utf-8').split("if '--cleanup-only'")[0],str(fixture),'exec'))
shell._timer.stop()
win.settings['ui_animations']=True
win.library=[dict(path=f'cold-{i}',title=f'Song {i}',artist='Probe',duration=180) for i in range(400)]
win.current_index=0;win.queue_context='library';win.user_queue=win.library[20:40]
from PyQt6 import sip
from PyQt6.QtWidgets import QWidget


def geometry(page):
    # Retain wrappers so Python id reuse cannot look like a layout change.
    return {id(widget):(widget,type(widget).__name__,[widget.x(),widget.y(),widget.width(),widget.height()],widget.isVisible())
            for widget in [page]+page.findChildren(QWidget)}


results=[]
original_capture=ui_anim._grab_area
try:
    for key in ('queue','albums','profile','tierlist'):
        page=shell.pages[key]
        captured=[]
        def capture(target,*args,**kwargs):
            result=original_capture(target,*args,**kwargs)
            if target is shell.stack and shell.stack.currentWidget() is page:
                captured.append(geometry(page))
            return result
        ui_anim._grab_area=capture
        shell.show_page(key)
        overlay=ui_anim._OVERLAYS.get(id(shell.stack))
        if overlay is not None:overlay._prepare()
        deadline=time.monotonic()+2
        while not captured and time.monotonic()<deadline:
            app.processEvents();time.sleep(.001)
        before=captured[-1] if captured else geometry(page)
        app.processEvents()
        after=geometry(page)
        changes=[]
        for identity,entry in before.items():
            widget,kind,rect,visible=entry
            if sip.isdeleted(widget) or identity not in after:continue
            _,_,new_rect,new_visible=after[identity]
            if (visible or new_visible) and (rect,visible)!=(new_rect,new_visible):
                changes.append(dict(widget=kind,before=rect,after=new_rect,visible_before=visible,visible_after=new_visible))
        result=dict(page=key,captured=bool(captured),visible_geometry_changes=changes)
        results.append(result)
        print('COLD_PAGE '+json.dumps(result),flush=True)
        ui_anim._grab_area=original_capture
        ui_anim._finish(shell.stack)
        shell.show_page('collection');ui_anim._finish(shell.stack);app.processEvents()
finally:
    ui_anim._grab_area=original_capture
    cleanup()

report={'scope':'First incoming snapshot geometry versus the following GUI event turn, only queue/albums/profile/tierlist',
        'fixture':{'platform':'Windows Qt offscreen','window_size':[1440,950],'synthetic_tracks':400,
                   'user_queue_tracks':20,'profile':'temporary','network':'blocked','audio':'not played'},
        'pages':results,
        'passed':all(item['captured'] and not item['visible_geometry_changes'] for item in results),
        'limitations':'Geometry/polish regression probe, not a full player scenario or live display FPS test.'}
path=ROOT/'qa/results/first-page-geometry-2026.11.02-4.json'
path.parent.mkdir(parents=True,exist_ok=True)
path.write_text(json.dumps(report,indent=2)+'\n','utf-8')
print('FIRST_PAGE_RESULT '+json.dumps(report),flush=True)
if not report['passed']:raise SystemExit(1)
