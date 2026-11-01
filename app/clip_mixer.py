# clip_mixer.py
"""Микшер громкости в режиме клипа: «Музыка» (громкость песни) и «Звук клипа» (своя звуковая дорожка видео —
вступления, сценки, голоса). Видео клипа играет без звука, поэтому звук клипа — отдельный проигрыватель VLC
только со звуком, который держится вровень с картинкой (пауза, перемотка, скорость, сдвиг).

Подключается при запуске (main.py → install()) и обёртывает методы clip_mode.ClipMode снаружи, не меняя файл.
"""
from __future__ import annotations

import time

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSlider, QVBoxLayout, QPushButton, QSizePolicy

_O = {}


def _settings(cm):
    return getattr(cm.win, "settings", {}) or {}


# ── звук клипа ── #

def _audio_start(cm):
    path = getattr(cm, "_src_path", None)
    vol = int(_settings(cm).get("clip_audio_vol", 0))
    if not path or vol <= 0 or cm.__dict__.get("_ca_path") == path:
        return
    _audio_stop(cm)
    try:
        import vlc
        inst = cm.__dict__.get("_ca_inst")
        if inst is None:
            inst = vlc.Instance("--quiet --no-video --no-video-title-show")
            cm._ca_inst = inst
        mp = inst.media_player_new()
        m = inst.media_new(path)
        m.add_option(":no-video")
        vt = _video_time(cm)[0]
        if vt is None and getattr(cm, "src", None) is not None and cm.src.p is not None:
            vt = (cm.src.p.get_time() or 0) / 1000.0
        if vt and vt > 0:
            m.add_option(f":start-time={vt + 0.08:.3f}")    # сразу с места картинки, а не с 0:00
        mp.set_media(m)
        mp.audio_set_volume(vol)
        mp.play()
        cm._ca_mp, cm._ca_path = mp, path
        cm._ca_paused = False
        cm._ca_st = dict(clock=None, err=None, cool=time.monotonic() + 0.8, rate=None)
    except Exception as e:                                 # noqa: BLE001
        print("[clip mixer]", e)
        cm._ca_mp = cm._ca_path = None


def _audio_stop(cm):
    mp = cm.__dict__.get("_ca_mp")
    if mp is not None:
        try:
            mp.stop()
            mp.release()
        except Exception:                                  # noqa: BLE001
            pass
    cm._ca_mp = cm._ca_path = None


def _video_time(cm):
    try:
        import clip_sync
        return clip_sync.video_time(cm)
    except Exception:                                      # noqa: BLE001
        return None, float(getattr(cm, "_rate", 1.0) or 1.0)


def _audio_sync(cm):
    """Звук клипа — туда же, где картинка: пауза/игра как у видео; мелкое расхождение — чуть быстрее/медленнее,
    перемотка — только при большом разрыве (частые перемотки рвали звук)."""
    mp = cm.__dict__.get("_ca_mp")
    src = getattr(cm, "src", None)
    if mp is None or src is None or getattr(src, "p", None) is None:
        return
    try:
        paused = bool(getattr(cm, "_vpaused", False))
        st = cm.__dict__.get("_ca_st") or {}
        cm._ca_st = st
        if paused != cm.__dict__.get("_ca_paused"):
            mp.set_pause(1 if paused else 0)
            cm._ca_paused = paused
            st["clock"] = None
            st["err"] = None
        vt, vrate = _video_time(cm)
        if vt is None:
            vt = (src.p.get_time() or 0) / 1000.0
        now = time.monotonic()

        def rate_to(r):
            if st.get("rate") is None or abs(r - st["rate"]) > 0.008:
                mp.set_rate(r)
                st["rate"] = r
        if paused:
            rate_to(vrate)
            return
        if now < st.get("cool", 0.0):
            return
        import clip_sync
        if st.get("clock") is None:
            st["clock"] = clip_sync.Clock()
        at = st["clock"].get(mp.get_time(), now, st.get("rate") or vrate)
        err = at - vt
        if vt > 0 and abs(err) > 0.5:
            mp.set_time(int((vt + 0.1 * vrate) * 1000))
            st.update(cool=now + 0.8, err=None)
            st["clock"].reset()
            rate_to(vrate)
            return
        e = err if st.get("err") is None else st["err"] * 0.75 + err * 0.25
        st["err"] = e
        k = 0.0 if abs(e) < 0.04 else max(-0.06, min(0.06, -0.8 * e))
        rate_to(vrate * (1.0 + k))
    except Exception:                                      # noqa: BLE001
        pass


# ── панель микшера ── #

def _row(title, val, fn):
    w = QFrame()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(10)
    lb = QLabel(title)
    lb.setMinimumWidth(96)
    s = QSlider(Qt.Orientation.Horizontal)
    s.setRange(0, 100)
    s.setValue(int(val))
    v = QLabel(f"{int(val)}%")
    v.setObjectName("ClipSub")
    v.setMinimumWidth(40)
    v.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def ch(x):
        v.setText(f"{x}%")
        fn(x)
    s.valueChanged.connect(ch)
    h.addWidget(lb)
    h.addWidget(s, 1)
    h.addWidget(v)
    return w, s


def _build_mixer(cm):
    p = QFrame(cm)
    p.setObjectName("ClipFx")                              # тот же вид, что у панели эффектов
    v = QVBoxLayout(p)
    v.setContentsMargins(14, 12, 14, 12)
    v.setSpacing(10)
    h = QLabel("МИКШЕР")
    h.setObjectName("ClipHead")
    v.addWidget(h)

    def music(x):
        try:
            cm.win.vol.setValue(int(x))                    # общая громкость плеера (как ползунок внизу)
        except Exception:                                  # noqa: BLE001
            pass

    def clip(x):
        cm.win.settings["clip_audio_vol"] = int(x)
        mp = cm.__dict__.get("_ca_mp")
        if x <= 0:
            _audio_stop(cm)
        elif mp is None:
            _audio_start(cm)
            _audio_sync(cm)
        else:
            mp.audio_set_volume(int(x))
    try:
        mv = cm.win.vol.value()
    except Exception:                                      # noqa: BLE001
        mv = 80
    r1, cm._mx_music = _row("Музыка", mv, music)
    r2, cm._mx_clip = _row("Звук клипа", _settings(cm).get("clip_audio_vol", 0), clip)
    v.addWidget(r1)
    v.addWidget(r2)
    note = QLabel("Звук клипа — дорожка самого видео (вступления, сценки).\nВместе с музыкой песня может звучать дважды.")
    note.setObjectName("ClipSub")
    v.addWidget(note)
    p.setFixedWidth(380)
    p.adjustSize()
    p.hide()
    try:
        cm.win.vol.valueChanged.connect(lambda x: (cm._mx_music.blockSignals(True), cm._mx_music.setValue(x),
                                                   cm._mx_music.blockSignals(False)))
    except Exception:                                      # noqa: BLE001
        pass
    return p


def _toggle(cm):
    p = cm._mixp
    p.setVisible(not p.isVisible())
    cm._b_mix.setChecked(p.isVisible())
    _place(cm)


def _place(cm):
    p = cm.__dict__.get("_mixp")
    if p is None or not p.isVisible():
        return
    b = cm.bottom.geometry()
    p.adjustSize()
    p.move(max(14, b.right() - p.width() - 8), b.top() - p.height() - 10)
    p.raise_()


# ── обёртки методов ClipMode ── #

def _w_build(self, *a, **k):
    r = _O["build"](self, *a, **k)
    try:
        b = self._btn("Микшер", "Микшер: громкость музыки и звука клипа", lambda: _toggle(self), checkable=True)
        b.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        lay = self.bottom.layout()
        lay.insertWidget(max(0, lay.count() - 1), b)        # перед последним отступом — справа
        self._b_mix = b
        self._mixp = _build_mixer(self)
    except Exception as e:                                 # noqa: BLE001
        print("[clip mixer] build:", e)
    return r


def _w_start(self, path, *a, **k):
    r = _O["start"](self, path, *a, **k)
    _audio_start(self)
    return r


def _w_stop(self, *a, **k):
    _audio_stop(self)
    return _O["stop"](self, *a, **k)


def _w_sync(self, *a, **k):
    r = _O["sync"](self, *a, **k)
    _audio_sync(self)
    return r


def _w_layout(self, *a, **k):
    r = _O["layout"](self, *a, **k)
    _place(self)
    return r


def install() -> bool:
    try:
        import clip_mode as CM
    except Exception as e:                                 # noqa: BLE001
        print("[clip mixer]", e)
        return False
    C = CM.ClipMode
    if getattr(C, "_mixer_wrapped", False):
        return False
    _O.update(build=C._build, start=C._start_video, stop=C._stop_video, sync=C._sync, layout=C._layout_children)
    C._build = _w_build
    C._start_video = _w_start
    C._stop_video = _w_stop
    C._sync = _w_sync
    C._layout_children = _w_layout
    C._mixer_wrapped = True
    return True
