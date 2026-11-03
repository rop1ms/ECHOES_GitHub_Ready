"""Host bridge for the C++ spoken-word catalogue; UI, network and parsing live in echoes_ui.dll."""
import ctypes
import json
import time
import weakref

from PyQt6 import sip
import native_ui

_PANELS = weakref.WeakSet()
_PLAY = ctypes.CFUNCTYPE(None, ctypes.c_char_p, ctypes.c_int)


def create(win):
    if not native_ui.available():
        return None
    lib = native_ui._lib
    lib.eui_spoken_new.argtypes = [ctypes.c_wchar_p, _PLAY]
    lib.eui_spoken_new.restype = ctypes.c_void_p
    lib.eui_spoken_language.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    lib.eui_spoken_language.restype = None

    def play(raw, index):
        try:
            import stream_core
            entries = json.loads(raw.decode('utf-8'))
            tracks = [stream_core.make_track(dict(item, artist=item.get('author', ''),
                       thumb_url=item.get('cover', ''))) for item in entries]
            # Native RSS parsing already supplies direct audio URLs. Register them with
            # the existing player so a podcast is never searched for as a music track.
            with win.streams.lock:
                for track in tracks:
                    win.streams.cache[track['path']] = {'url': track['url'], 'page': track['url'],
                        'exp': time.time() + 86400, 'duration': track['duration']}
            if entries[index].get('kind') == 'audiobooks' and getattr(win, 'shuffle', False):
                win.btn_shuffle.setChecked(False)
                win._toggle_shuffle(False)
            win.stream_tracks(tracks, start=index, playlist='\u2063spoken')
        except Exception:
            import sys
            sys.excepthook(*sys.exc_info())

    callback = _PLAY(play)
    panel = native_ui._wrap(lib.eui_spoken_new(win.settings.get('language', 'en'), callback))
    panel._spoken_callback = callback
    _PANELS.add(panel)
    return panel


def relabel(language):
    for panel in list(_PANELS):
        if not sip.isdeleted(panel):
            native_ui._lib.eui_spoken_language(sip.unwrapinstance(panel), language)
