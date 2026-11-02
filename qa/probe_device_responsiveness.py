"""GUI heartbeat during isolated device enumeration; no recording or playback."""
import json
import os
from pathlib import Path
import sys
import threading
import time

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication
import voice_devices

app=QApplication([]);samples=[];result=[];done=threading.Event();cancel=threading.Event()
start=time.perf_counter()
def query():
    try:result.append(voice_devices.isolated_devices(cancel=cancel))
    finally:done.set()
worker=threading.Thread(target=query,daemon=True);worker.start()
timer=QTimer();timer.setInterval(10)
def tick():
    samples.append(time.perf_counter())
    if done.is_set() or time.perf_counter()-start>10:app.quit()
timer.timeout.connect(tick);timer.start();app.exec()
cancel.set();worker.join(2)
intervals=sorted((b-a)*1000 for a,b in zip(samples,samples[1:]))
report=dict(scope='Qt GUI heartbeat during only native device discovery, no audio capture/playback',
    isolated_query_completed=done.is_set(),heartbeat_ticks=len(samples),
    elapsed_ms=round((time.perf_counter()-start)*1000,3),
    heartbeat_p95_ms=round(intervals[min(len(intervals)-1,int(len(intervals)*.95))],3) if intervals else None,
    heartbeat_max_ms=round(max(intervals),3) if intervals else None,
    device_counts=[len(group) for group in result[0]] if result else [],
    remaining_helpers=len(voice_devices._PROBES),
    limitations='Event-loop heartbeat only, not page rendering FPS or a controlled old/new application comparison.')
path=Path(__file__).resolve().parent/'results/device-discovery-2026.11.02-4.json'
path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(report,indent=2)+'\n','utf-8')
print(json.dumps(report))
if not done.is_set() or len(samples)<3 or voice_devices._PROBES:sys.exit(1)
