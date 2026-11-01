# clip_sync.py
"""Плавная синхронизация клипа с песней (режим клипа).

Раньше клип сверялся с песней раз в 0,3 с по сырому get_time() VLC, а он обновляется ступеньками
по ~0,3 с — расхождение «скакало» на ±0,2 с, и клип то и дело перематывался (каждая перемотка VLC —
подвисание картинки). Теперь:
  • время и песни, и клипа берётся плавное (между ступеньками досчитывается по часам);
  • небольшое расхождение убирается мягко — клип чуть ускоряется/замедляется (до ±10 %);
  • перемотка — только при большом разрыве (пользователь перемотал, сменился трек), с упреждением,
    которое подстраивается само (VLC приземляется после перемотки с задержкой).

Подключается при запуске (main.py → install()) и заменяет ClipMode._sync снаружи, не меняя clip_mode.py.
"""
from __future__ import annotations

import time

_O = {}

SYNC_DT = 0.08      # как часто сверяться, с
HARD = 0.75         # расхождение больше — перемотка
DEAD = 0.03         # меньше — ничего не трогаем
GAIN = 0.8          # поправка скорости на 1 с расхождения
MAXK = 0.10         # не больше ±10 % к скорости
COOL = 0.9          # после перемотки дать VLC приземлиться, с


class Clock:
    """Плавное время проигрывателя VLC: get_time() меняется ступеньками — между ними досчитываем."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.raw = None
        self.t0 = 0.0
        self.base = 0.0

    def get(self, ms, now, rate, playing=True) -> float:
        if ms is None or ms < 0:
            self.reset()
            return 0.0
        if not playing:
            self.raw, self.t0, self.base = ms, now, float(ms)
            return ms / 1000.0
        if self.raw is None or ms != self.raw:
            start = float(ms)
            if self.raw is not None:
                est = self.base + (now - self.t0) * 1000.0 * rate
                if 0.0 < est - ms < 300.0:                 # свежая ступенька чуть позади — назад не прыгаем
                    start = est
            self.raw, self.t0, self.base = ms, now, start
            return start / 1000.0
        return (self.base + min(1.0, now - self.t0) * 1000.0 * rate) / 1000.0


def _clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def state(cm) -> dict:
    st = cm.__dict__.get("_cs")
    if st is None or st.get("src") is not getattr(cm, "src", None):
        st = cm._cs = dict(src=getattr(cm, "src", None), clock=Clock(), err=None, cool=0.0, lead=0.12,
                           measure=False, rate=None, vt=None, play=False)
    return st


def _set_rate(src, st, r):
    if st["rate"] is None or abs(r - st["rate"]) > 0.003:
        src.set_rate(r)
        st["rate"] = r


def _seek(cm, st, p, tgt, base, now):
    try:
        p.set_time(int(max(0.0, tgt + st["lead"] * base) * 1000))
    except Exception:                                      # noqa: BLE001
        return
    st["cool"] = now + COOL
    st["clock"].reset()
    st["err"] = None
    st["measure"] = True
    _set_rate(cm.src, st, base)


def _sync(self):
    """Клип — вровень с песней: пауза, перемотка, скорость, сдвиг."""
    src = self.src
    if src is None or src.p is None:
        return
    st = state(self)
    eng = self.win.engine
    try:
        smooth = getattr(eng, "get_position_smooth", None)
        pos = float((smooth() if smooth else eng.get_position()) or 0)
        playing = bool(eng.is_playing())
    except Exception:                                      # noqa: BLE001
        return
    want = pos + self._offset()
    p = src.p
    try:
        length = (p.get_length() or 0) / 1000.0
        ms = p.get_time()
    except Exception:                                      # noqa: BLE001
        return
    now = time.monotonic()
    try:
        base = float(self.win.speed.value()) / 100.0
    except Exception:                                      # noqa: BLE001
        base = 1.0
    self._rate = base
    play = playing and want >= 0 and not (length > 0 and want >= length - 0.15)
    tgt = min(max(0.0, want), max(0.0, length - 0.2)) if length > 0 else max(0.0, want)

    if play != (self._vpaused is False):
        src.set_paused(not play)
        self._vpaused = not play
        st["clock"].reset()
        st["err"] = None
        if play and length > 0 and ms is not None and abs(ms / 1000.0 - tgt) > 0.25:
            _seek(self, st, p, tgt, base, now)             # продолжить сразу с нужного места
    st["play"] = play

    if not play:
        _set_rate(src, st, base)
        st["vt"] = (ms or 0) / 1000.0
        if length > 0 and ms is not None and abs(ms / 1000.0 - tgt) > 0.6 and now >= st["cool"]:
            try:
                p.set_time(int(tgt * 1000))                # на паузе кадр стоит там, где песня
            except Exception:                              # noqa: BLE001
                pass
            st["cool"] = now + 0.5
        return

    vt = st["clock"].get(ms, now, st["rate"] or base)
    st["vt"] = vt
    if now < st["cool"]:
        return
    err = vt - tgt
    if st["measure"]:                                      # куда приземлилась перемотка → поправить упреждение
        st["measure"] = False
        if abs(err) < HARD:
            st["lead"] = _clamp(st["lead"] - 0.6 * err, 0.0, 0.6)
    if length > 0 and abs(err) > HARD:
        _seek(self, st, p, tgt, base, now)
        return
    e = err if st["err"] is None else st["err"] * 0.75 + err * 0.25
    st["err"] = e
    k = 0.0 if abs(e) < DEAD else _clamp(-GAIN * e, -MAXK, MAXK)
    _set_rate(src, st, base * (1.0 + k))


def video_time(cm):
    """Плавное время картинки клипа (для звука клипа) и её текущая скорость."""
    st = cm.__dict__.get("_cs")
    if not st or st.get("vt") is None:
        return None, float(getattr(cm, "_rate", 1.0) or 1.0)
    return st["vt"], float(st["rate"] or getattr(cm, "_rate", 1.0) or 1.0)


def _w_tick(self, *a, **k):
    try:
        now = time.perf_counter()
        if self.src is not None and not self.win.isMinimized() and now - self._last_sync > SYNC_DT:
            self._last_sync = now                          # исходный _tick (раз в 0,3 с) тогда пропустит
            self._sync()
    except Exception:                                      # noqa: BLE001
        pass
    return _O["tick"](self, *a, **k)


def install() -> bool:
    try:
        import clip_mode as CM
    except Exception as e:                                 # noqa: BLE001
        print("[clip sync]", e)
        return False
    C = CM.ClipMode
    if getattr(C, "_sync_smooth", False):
        return False
    _O["tick"] = C._tick
    C._tick = _w_tick
    if getattr(C, "_mixer_wrapped", False):                # микшер уже обернул _sync — подменить внутри
        try:
            import clip_mixer
            clip_mixer._O["sync"] = _sync
        except Exception:                                  # noqa: BLE001
            C._sync = _sync
    else:
        C._sync = _sync
    C._sync_smooth = True
    return True
