"""Native, GIL-independent VLC/PortAudio callbacks; Python only sends controls."""
from __future__ import annotations
import ctypes as C
import math
import threading
import time
from pathlib import Path
import numpy as np
import vlc

SR = 48000
_LIB = None


def library():
    global _LIB
    if _LIB is not None:
        return _LIB
    lib = C.CDLL(str(Path(__file__).with_name('echoes_audio.dll')))
    p, i, u, f, d = C.c_void_p, C.c_int, C.c_uint, C.c_float, C.c_double
    signatures = {
        'na_create': (p, [p]), 'na_close': (None, [p]), 'na_destroy': (None, [p]),
        'na_deck': (p, [p]), 'na_reset': (None, [p]), 'na_anchor': (None, [p,d,i]),
        'na_gain': (None, [p,f,i]), 'na_vari': (None, [p,d,d]), 'na_vari_busy': (i,[p]),
        'na_chain': (None,[p,p]), 'na_crossfade': (None,[p,p,p,d]), 'na_fade_done': (i,[p]),
        'na_render': (None,[p,p,u,d,C.c_ulong]), 'na_info': (None,[p,p]),
        'na_stats': (None,[p,p]), 'na_matrix': (None,[p,p]), 'na_visual': (u,[p,p,u,u]),
        'na_play': (None,[p,p,u,C.c_int64]), 'na_pause': (None,[p,C.c_int64]),
        'na_resume': (None,[p,C.c_int64]), 'na_flush': (None,[p,C.c_int64]),
        'na_drain': (None,[p]), 'na_volume': (None,[p,f,C.c_bool]),
    }
    for name,(result,args) in signatures.items():
        fn=getattr(lib,name);fn.restype=result;fn.argtypes=args
    _LIB=lib
    return lib


class NativeDeck:
    def __init__(self, mixer, name):
        self.m, self.name, self.lib = mixer, name, mixer.lib
        self.handle=self.lib.na_deck(mixer.handle)
        if not self.handle:
            raise RuntimeError('Cannot allocate native audio deck')
        self._amp, self._mute, self._cbs = 1., False, None

    def attach(self, player):
        callbacks=[]
        for name,decorator in [('play','AudioPlayCb'),('pause','AudioPauseCb'),
                               ('resume','AudioResumeCb'),('flush','AudioFlushCb'),
                               ('drain','AudioDrainCb'),('volume','AudioSetVolumeCb')]:
            # Casting an existing C address creates no Python callback thunk.
            callbacks.append(C.cast(getattr(self.lib,'na_'+name),getattr(vlc.CallbackDecorators,decorator)))
        self._cbs=tuple(callbacks)
        player.audio_set_callbacks(*callbacks[:5], self.handle)
        player.audio_set_volume_callback(callbacks[5])
        # S16: в VLC сигнал не должен доходить до 0 дБ — запас под эквалайзер держит движок (_eq_headroom_db),
        # громкое сверх того мягко прижимает лимитер в echoes_audio.dll
        player.audio_set_format('S16N',SR,2)

    @property
    def amp(self):
        return self._amp
    @amp.setter
    def amp(self, value):
        self._amp=float(value)
        self.lib.na_gain(self.handle,self._amp,self._mute)
    @property
    def mute(self):
        return self._mute
    @mute.setter
    def mute(self, value):
        self._mute=bool(value)
        self.lib.na_gain(self.handle,self._amp,self._mute)

    def reset(self):
        self.lib.na_reset(self.handle)
    def set_anchor(self, ms, expect_flush=False):
        self.lib.na_anchor(self.handle,float(ms),bool(expect_flush))
    def set_vari(self, target, seconds):
        self.lib.na_vari(self.handle,float(target),float(seconds))
    def vari_busy(self):
        return bool(self.lib.na_vari_busy(self.handle))
    def info(self):
        values=(C.c_double*10)()
        self.lib.na_info(self.handle,values)
        return tuple(values)
    def finished(self):
        return self.info()[6]==4
    def lag_seconds(self):
        return self.info()[7]
    def heard_seconds(self):
        anchor,played,rendered,sounding,latency,n,*_=self.info()
        if math.isnan(anchor):
            return None
        value=played
        if sounding:
            value=max(0.,min(played,played-SR*latency-n+SR*(time.perf_counter()-rendered)))
        return anchor/1000., value/SR


class NativeMatrix:
    def __init__(self,mixer):
        self.m=mixer
        self.configure({})
    def configure(self,options):
        from listening_dsp import matrix
        self.target=matrix(options)
        self.m.lib.na_matrix(self.m.handle,self.target.ctypes.data)


class NativeMixer:
    native=True
    LATENCY=.25
    def __init__(self, open_stream=True):
        self.lib=library()
        clock=C.cast(vlc.dll.libvlc_clock,C.c_void_p).value
        self.handle=self.lib.na_create(clock)
        if not self.handle:
            raise RuntimeError('Cannot allocate native audio mixer')
        self.closed=False
        self.decks=[]
        self.vis_cb=None
        self.latency_s=self.LATENCY
        self.listening=NativeMatrix(self)
        self.stream=None
        self.on_device=None          # callable(ok: bool) — устройство пропало / вернулось (из потока сторожа)
        self._lost=False
        self._opened_at=0.
        self._stop=threading.Event()
        self._stream_lock=threading.Lock()
        self._watchdog=None
        try:
            if open_stream:
                self._open()
                self._watchdog=threading.Thread(target=self._watch,daemon=True,name='native-audio-device')
                self._watchdog.start()
        except BaseException:
            self.close()
            raise

    def _open(self):
        import sounddevice as sd
        address=C.cast(self.lib.na_pa_callback,C.c_void_p).value
        callback=sd._ffi.cast('PaStreamCallback*',address)
        userdata=sd._ffi.cast('void*',self.handle)
        # _StreamBase is sounddevice's documented library-author interface for
        # native CFFI callback pointers. The array/buffer wrappers enter Python.
        stream=sd._StreamBase(kind='output',samplerate=SR,channels=2,dtype='float32',
                              blocksize=480,latency=self.LATENCY,callback=callback,
                              userdata=userdata,wrap_callback=None)
        try:
            stream.start()
        except BaseException:
            stream.close()
            raise
        self.stream=stream
        self.latency_s=float(stream.latency)
        self._opened_at=time.perf_counter()

    def _device(self,ok):
        """Сообщить движку: пока устройства нет, VLC должен стоять на паузе. Иначе он копит звук в полную
        очередь деки, а после переподключения выбрасывает «опоздавший» — трек перескакивал вперёд."""
        if ok==(not self._lost):
            return
        self._lost=not ok
        cb=self.on_device
        if cb is not None:
            try:cb(ok)
            except Exception as exc:print('[native-audio] device callback:',exc)

    def _watch(self):
        delay=.5
        while not self._stop.wait(delay):
            with self._stream_lock:
                if self.closed:
                    return
                try:
                    now=time.perf_counter()
                    stuck=(self.stream is not None and self.stream.active and now-self._opened_at>2.
                           and now-self.stats()['last_render']>1.)    # поток «активен», но звуковая карта молчит
                    if self.stream is not None and self.stream.active and not stuck:
                        self._device(True)
                        delay=.5
                        continue
                    self._device(False)
                    if stuck:print('[native-audio] output stalled - reopening')
                    if self.stream is not None:
                        try:self.stream.abort()
                        except Exception:pass
                        self.stream.close()
                        self.stream=None
                    self._open()
                    self._device(True)
                    delay=.5
                except Exception as exc:
                    print('[native-audio] reconnect:',exc)
                    delay=min(3.,delay*1.5)

    def new_deck(self,name):
        deck=NativeDeck(self,name)
        self.decks.append(deck)
        return deck
    def chain(self,prev,nxt):
        self.lib.na_chain(prev.handle,nxt.handle)
    def crossfade(self,prev,nxt,seconds):
        self.lib.na_crossfade(self.handle,prev.handle,nxt.handle,float(seconds))
    def crossfade_done(self):
        return bool(self.lib.na_fade_done(self.handle))
    def stats(self):
        values=(C.c_double*3)()
        self.lib.na_stats(self.handle,values)
        return dict(last_render=values[0],callbacks=int(values[1]),device_underflows=int(values[2]))
    def alive(self):
        return not self.closed and time.perf_counter()-self.stats()['last_render']<1.5
    def visual_samples(self,n):
        n=max(1,min(32768,int(n)))
        out=np.empty(n,dtype=np.float32)
        got=self.lib.na_visual(self.handle,out.ctypes.data,n,int(self.latency_s*SR))
        return out if got==n else None
    def close(self):
        if self.closed:
            return
        self.closed=True
        self._stop.set()
        self.lib.na_close(self.handle)
        with self._stream_lock:
            if self.stream is not None:
                try:
                    self.stream.stop()
                finally:
                    self.stream.close()
                    self.stream=None
        if self._watchdog is not None and self._watchdog is not threading.current_thread():
            self._watchdog.join(1)
        self.lib.na_destroy(self.handle)
        self.handle=None
