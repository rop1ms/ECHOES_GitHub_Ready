"""Actual cache budgets, hidden-resource release and background frame pacing."""
from __future__ import annotations
import ctypes
import sys
import weakref
from PyQt6.QtCore import QObject,QEvent,QTimer,Qt
from PyQt6.QtGui import QPixmapCache,QImageReader,QMovie
from PyQt6.QtWidgets import QApplication,QAbstractItemView,QWidget

_WIN=None
DEFAULTS={'cover_mb':24,'rows_mb':12,'buttons_mb':8,'qt_mb':16,'aura_mb':4,
          'aura_quality':'balanced','preview_side':1920,'release_hidden':True,
          'background_limit':True,'background_fps':30,'warm_pages':False,
          'animated_images':True,'gif_cache':False,'chorus_prefetch':True,'chorus_seconds':360,'duel_particles':False}


def config(settings=None):
    settings=settings if settings is not None else getattr(_WIN,'settings',{})
    raw=settings.get('performance',{})
    raw=raw if isinstance(raw,dict) else {};out=dict(DEFAULTS)
    if settings.get('motion_profile') in ('eco','reduced'):out.update(cover_mb=12,qt_mb=8)
    for key in ('cover_mb','rows_mb','buttons_mb','qt_mb','aura_mb'):
        try:out[key]=max(1,min(128,int(raw.get(key,out[key]))))
        except (TypeError,ValueError,OverflowError):pass
    for key in ('release_hidden','background_limit','warm_pages','animated_images','gif_cache','chorus_prefetch','duel_particles'):
        if type(raw.get(key)) is bool:out[key]=raw[key]
    for key,allowed in [('aura_quality',('low','balanced','high')),('preview_side',(1280,1920,2560,3840)),('background_fps',(30,60)),('chorus_seconds',(120,240,360,600))]:
        if raw.get(key) in allowed:out[key]=raw[key]
    return out


def memory_usage():
    """Working set and private committed bytes, not an artificial RAM percentage."""
    if sys.platform=='win32':
        class Counters(ctypes.Structure):
            _fields_=[('cb',ctypes.c_uint32),('faults',ctypes.c_uint32)]+[(name,ctypes.c_size_t) for name in
                       ('peak','working','peak_paged','paged','peak_nonpaged','nonpaged','pagefile','peak_pagefile','private')]
        data=Counters();data.cb=ctypes.sizeof(data)
        kernel=ctypes.windll.kernel32;kernel.GetCurrentProcess.restype=ctypes.c_void_p
        fn=ctypes.windll.psapi.GetProcessMemoryInfo;fn.argtypes=[ctypes.c_void_p,ctypes.POINTER(Counters),ctypes.c_uint32];fn.restype=ctypes.c_int
        if fn(kernel.GetCurrentProcess(),ctypes.byref(data),data.cb):return data.working,data.private
    return (0,0)


def configure_caches(win):
    values=config(win.settings)
    QPixmapCache.setCacheLimit(values['qt_mb']*1024)
    QImageReader.setAllocationLimit(96)
    from raster_budget import ROWS
    ROWS.limit=values['rows_mb']*2**20;ROWS.prune()
    import yamusic_theme as Y
    Y._COVER_BUDGET=values['cover_mb']*2**20;Y.prune_cover_cache()
    import paint_cache
    paint_cache.set_budget(values['buttons_mb']*2**20)


def movie_for(widget):
    for name in ('_av_movie','_movie','movie'):
        item=getattr(widget,name,None)
        if isinstance(item,QMovie):return item
        if name=='movie' and callable(item):
            try:
                item=item()
                if isinstance(item,QMovie):return item
            except TypeError:pass
    return None


def configure_movie(movie,widget=None):
    values=config();movie.setCacheMode(QMovie.CacheMode.CacheAll if values['gif_cache'] else QMovie.CacheMode.CacheNone)
    paused=not values['animated_images'] or (values['release_hidden'] and widget is not None and not widget.isVisible())
    if movie.state()==QMovie.MovieState.NotRunning:movie.start()
    movie.setPaused(paused)


def release_widget(widget):
    from raster_budget import ROWS
    if type(widget).__name__ in ('GlassFrame','AmbientFrame'):
        widget.__dict__.pop('_deco_pm',None)
        if '_surface' in widget.__dict__:widget._surface=None
    if isinstance(widget,QAbstractItemView):ROWS.clear(id(widget.itemDelegate()))
    if type(widget).__name__=='Arena':
        widget.fire_cache.clear();widget._halo_cache.clear()
        for sprite in widget.sprites:sprite.card=None;sprite.card_key=None
    # Destroying previews should release full-resolution source pixmaps promptly.
    if type(widget).__name__=='_Canvas':
        widget._display_cache=None


def clear_caches(win):
    from raster_budget import ROWS
    ROWS.clear();QPixmapCache.clear()
    import yamusic_theme as Y,paint_cache
    Y.clear_cover_cache();paint_cache.clear_all()
    for widget in win.findChildren(QWidget):
        if type(widget).__name__=='Arena':widget.fire_cache.clear();widget._halo_cache.clear()
        if not widget.isVisible():release_widget(widget)
    win._icon_cache.clear()
    import mem_trim
    mem_trim.trim_later(300)
    win.update()


class Governor(QObject):
    def __init__(self,win):
        super().__init__(win);self.win=win
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.timeout.connect(self.frame_policy)
        QApplication.instance().installEventFilter(self)
    def frame_policy(self):
        import frameclock
        settings=dict(self.win.settings);values=config(settings)
        if values['background_limit'] and (self.win.isMinimized() or QApplication.applicationState()!=Qt.ApplicationState.ApplicationActive):
            settings['fps_limit']=values['background_fps']
        frameclock.configure(settings,self.win)
    def eventFilter(self,obj,event):
        kind=event.type()
        if kind==QEvent.Type.ApplicationStateChange or (obj is self.win and kind==QEvent.Type.WindowStateChange):self.timer.start(120)
        elif kind in (QEvent.Type.Hide,QEvent.Type.Show) and isinstance(obj,QWidget):
            values=config()
            if values['release_hidden'] and kind==QEvent.Type.Hide:
                try:release_widget(obj)
                except (RuntimeError,AttributeError):pass
            movie=movie_for(obj)
            if movie is not None:
                # Apply after the widget's own showEvent, which may resume it.
                ref=weakref.ref(obj)
                def update():
                    widget=ref()
                    if widget is not None:
                        try:
                            mv=movie_for(widget)
                            if mv is not None:configure_movie(mv,widget)
                        except RuntimeError:pass
                QTimer.singleShot(0,update)
        return False


def install(win):
    global _WIN
    _WIN=win
    if not hasattr(win,'_performance_governor'):win._performance_governor=Governor(win)
    configure_caches(win)


def apply(win):
    install(win);win._performance_governor.frame_policy()
    for widget in win.findChildren(QWidget):
        movie=movie_for(widget)
        if movie is not None:configure_movie(movie,widget)
        if type(widget).__name__=='Arena':widget.fire_cache.clear();widget.update()
    if config(win.settings)['warm_pages']:
        import warmup
        warmup.start(win)
    win.save_settings()
