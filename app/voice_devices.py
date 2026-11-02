"""Lightweight device discovery, independent of the voice DSP/codec imports."""
import atexit
import json
from pathlib import Path
import subprocess
import sys
import threading
import time

_PROBES=set()
_LOCK=threading.Lock()


def _stop_probes():
    with _LOCK:active=list(_PROBES)
    for process in active:
        try:
            if process.poll() is None:process.kill()
        except OSError:pass


atexit.register(_stop_probes)


def devices():
    """Keep PortAudio indices, ordering and name dedup identical to VoiceEngine."""
    try:
        import sounddevice as sd
        ins,outs,seen_i,seen_o=[],[],set(),set()
        for index,device in enumerate(sd.query_devices()):
            name=str(device.get('name',''))[:60]
            if device.get('max_input_channels',0)>0 and name not in seen_i:
                seen_i.add(name);ins.append((index,name))
            if device.get('max_output_channels',0)>0 and name not in seen_o:
                seen_o.add(name);outs.append((index,name))
        return ins,outs
    except Exception:
        return [],[]


def isolated_devices(cancel=None,timeout=8.):
    """Keep native library bootstrap/import locks out of the UI interpreter."""
    process=None
    try:
        executable=Path(sys.executable)
        # The desktop launcher may use pythonw (no stdout). Use the sibling
        # console interpreter with CREATE_NO_WINDOW for the JSON pipe.
        if executable.stem.lower()=='pythonw' and executable.with_name('python.exe').exists():
            executable=executable.with_name('python.exe')
        process=subprocess.Popen([str(executable),'-B',str(Path(__file__).resolve()),'--probe'],
            stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        with _LOCK:_PROBES.add(process)
        deadline=time.monotonic()+timeout
        while True:
            if (cancel is not None and cancel.is_set()) or time.monotonic()>=deadline:
                process.kill();process.communicate(timeout=1);return [],[]
            try:
                output,_=process.communicate(timeout=min(.15,max(.001,deadline-time.monotonic())))
                break
            except subprocess.TimeoutExpired:continue
        if process.returncode!=0 or len(output)>256*1024:return [],[]
        result=json.loads(output.decode('utf-8'))
        if not isinstance(result,list) or len(result)!=2:return [],[]
        return tuple([(index,name[:60]) for index,name in group
                      if isinstance(index,int) and isinstance(name,str)] for group in result)
    except (OSError,ValueError,TypeError,subprocess.TimeoutExpired):
        return [],[]
    finally:
        if process is not None:
            try:
                if process.poll() is None:process.kill();process.communicate(timeout=1)
            except (OSError,subprocess.TimeoutExpired):pass
            with _LOCK:_PROBES.discard(process)


if __name__=='__main__' and '--probe' in sys.argv:
    print(json.dumps(devices(),ensure_ascii=True))
