# overlay_live.py
"""
Настоящий игровой оверлей ECHOES (Windows). Виджеты и вид — overlay_demo.OverlayCanvas.

  * прозрачное окно поверх всех окон на мониторе, где сейчас игра;
  * закрытый оверлей — только закреплённые виджеты и только поверх игры на весь экран/«без рамки»
    (настройка «only_games»); клики проходят в игру (окно прозрачно для мыши), фокус у игры не отнимается;
  * открытый (бинд, по умолчанию Shift+`) — затемнение, виджеты таскаются и закрепляются, кнопки ⏮ ⏯ ⏭
    нажимаются; Esc или тот же бинд — закрыть;
  * бинды — системные (RegisterHotKey): работают, когда ECHOES в фоне; занятое другой программой сочетание
    не регистрируется (об этом — в журнале);
  * эксклюзивный полный экран (не «без рамки») оверлей не перекрывает — для этого надо внедряться в игру,
    этого не делаем.

apply(win) — включить/выключить/перечитать настройки (вызывается при запуске темы и из настроек).
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes

from PyQt6.QtCore import QAbstractNativeEventFilter, QObject, QRect, Qt, QTimer
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import QApplication

import overlay_demo as OD

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY = 0x0312
_VK = {Qt.Key.Key_Space: 0x20, Qt.Key.Key_Left: 0x25, Qt.Key.Key_Up: 0x26, Qt.Key.Key_Right: 0x27,
       Qt.Key.Key_Down: 0x28, Qt.Key.Key_QuoteLeft: 0xC0, Qt.Key.Key_AsciiTilde: 0xC0, Qt.Key.Key_Minus: 0xBD,
       Qt.Key.Key_Equal: 0xBB, Qt.Key.Key_Plus: 0xBB, Qt.Key.Key_Tab: 0x09, Qt.Key.Key_Return: 0x0D,
       Qt.Key.Key_Enter: 0x0D, Qt.Key.Key_Insert: 0x2D, Qt.Key.Key_Delete: 0x2E, Qt.Key.Key_Home: 0x24,
       Qt.Key.Key_End: 0x23, Qt.Key.Key_PageUp: 0x21, Qt.Key.Key_PageDown: 0x22, Qt.Key.Key_Comma: 0xBC,
       Qt.Key.Key_Period: 0xBE, Qt.Key.Key_Slash: 0xBF, Qt.Key.Key_Semicolon: 0xBA, Qt.Key.Key_Apostrophe: 0xDE,
       Qt.Key.Key_BracketLeft: 0xDB, Qt.Key.Key_BracketRight: 0xDD, Qt.Key.Key_Backslash: 0xDC,
       Qt.Key.Key_Pause: 0x13, Qt.Key.Key_ScrollLock: 0x91, Qt.Key.Key_Escape: 0x1B, Qt.Key.Key_Backspace: 0x08}


def parse(seq: str):
    """«Ctrl+Shift+M» → (модификаторы Windows, виртуальная клавиша) или None."""
    if not seq:
        return None
    ks = QKeySequence.fromString(seq, QKeySequence.SequenceFormat.PortableText)
    if ks.isEmpty():
        return None
    comb = ks[0]
    key, mods = comb.key(), comb.keyboardModifiers()
    m = 0
    if mods & Qt.KeyboardModifier.ControlModifier:
        m |= MOD_CONTROL
    if mods & Qt.KeyboardModifier.ShiftModifier:
        m |= MOD_SHIFT
    if mods & Qt.KeyboardModifier.AltModifier:
        m |= MOD_ALT
    if mods & Qt.KeyboardModifier.MetaModifier:
        m |= MOD_WIN
    kv = key.value if hasattr(key, "value") else int(key)
    if 0x41 <= kv <= 0x5A or 0x30 <= kv <= 0x39:              # буквы и цифры совпадают с VK
        return m, kv
    if Qt.Key.Key_F1.value <= kv <= Qt.Key.Key_F24.value:
        return m, 0x70 + kv - Qt.Key.Key_F1.value
    vk = _VK.get(Qt.Key(kv)) if kv in [k.value for k in _VK] else None
    return (m, vk) if vk else None


class _RECT(ctypes.Structure):
    _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long), ("r", ctypes.c_long), ("b", ctypes.c_long)]


class _MONINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", _RECT), ("rcWork", _RECT), ("dwFlags", wintypes.DWORD)]


def game_monitor():
    """Игра на весь экран / «без рамки» на переднем плане (не ECHOES, не рабочий стол) → (x, y, w, h) монитора."""
    u = ctypes.windll.user32
    hwnd = u.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == os.getpid():
        return None
    cls = ctypes.create_unicode_buffer(64)
    u.GetClassNameW(hwnd, cls, 64)
    if cls.value in ("Progman", "WorkerW", "Shell_TrayWnd", "Windows.UI.Core.CoreWindow"):
        return None
    r = _RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    mon = u.MonitorFromWindow(hwnd, 2)
    mi = _MONINFO()
    mi.cbSize = ctypes.sizeof(_MONINFO)
    if not u.GetMonitorInfoW(mon, ctypes.byref(mi)):
        return None
    m = mi.rcMonitor
    if r.l <= m.l and r.t <= m.t and r.r >= m.r and r.b >= m.b:
        return m.l, m.t, m.r - m.l, m.b - m.t
    return None


class OverlayWindow(OD.OverlayCanvas):
    def __init__(self, ov):
        super().__init__(ov.cfg, {}, live=True)
        self.ov = ov
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint |
                            Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput |
                            Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setWindowTitle("ECHOES overlay")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.action.connect(ov.widget_action)
        self.changed.connect(ov.save_layout)
        self.reply.connect(ov.send_reply)

    def set_interactive(self, on):
        """Открытый — ловит мышь и клавиатуру; закрытый — прозрачен для мыши, фокус у игры."""
        geo = self.geometry()
        vis = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, not on)
        self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, not on)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, not on)
        self.setGeometry(geo)
        if vis or on:
            self.show()
        if on:
            self.raise_()
            self.activateWindow()
            self.setFocus()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape and self.open:
            self.ov.toggle()
            return
        super().keyPressEvent(e)


class _HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, ov):
        super().__init__()
        self.ov = ov

    def nativeEventFilter(self, etype, message):
        try:
            if bytes(etype) == b"windows_generic_MSG":
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == WM_HOTKEY and int(msg.wParam) in self.ov.ids:
                    act = self.ov.ids[int(msg.wParam)]
                    QTimer.singleShot(0, lambda: self.ov.run(act))
                    return True, 0
        except Exception:                                      # noqa: BLE001
            pass
        return False, 0


class Overlay(QObject):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.cfg = OD.load(win.settings)
        self.ids = {}                                          # id хоткея → действие
        self.failed = []                                       # бинды, которые заняты другой программой
        self.wnd = None
        self.in_game = None
        self._filter = _HotkeyFilter(self)
        QApplication.instance().installNativeEventFilter(self._filter)
        self.slow = QTimer(self)                               # раз в секунду: игра ли на экране
        self.slow.setInterval(1000)
        self.slow.timeout.connect(self._check_game)
        self.fast = QTimer(self)                               # данные и анимация, пока окно видно
        self.fast.setInterval(250)
        self.fast.timeout.connect(self._frame)
        QApplication.instance().aboutToQuit.connect(self.stop)
        self._note_hooked = False

    # ── включение ── #

    def apply(self):
        self.cfg = OD.load(self.win.settings)
        self._unregister()
        if not self.cfg.get("enabled"):
            self.slow.stop()
            self.fast.stop()
            if self.wnd is not None:
                self.wnd.hide()
            return
        if self.wnd is None:
            self.wnd = OverlayWindow(self)
        self.wnd.cfg = self.cfg
        self._register()
        self._hook_notes()
        self.slow.start()
        self._check_game()

    def stop(self):
        self._unregister()
        self.slow.stop()
        self.fast.stop()
        if self.wnd is not None:
            self.wnd.hide()

    def _register(self):
        u = ctypes.windll.user32
        self.failed = []
        for i, (act, _n, _d) in enumerate(OD.ACTIONS):
            pr = parse(self.cfg["binds"].get(act, ""))
            if not pr:
                continue
            hid = 0xEC00 + i
            if u.RegisterHotKey(None, hid, pr[0] | MOD_NOREPEAT, pr[1]):
                self.ids[hid] = act
            else:
                self.failed.append(act)
                print(f"[overlay] бинд «{self.cfg['binds'].get(act)}» занят другой программой")

    def _unregister(self):
        u = ctypes.windll.user32
        for hid in list(self.ids):
            u.UnregisterHotKey(None, hid)
        self.ids = {}

    # ── уведомления Echoes.NET → в оверлей ── #

    def _hook_notes(self):
        if self._note_hooked:
            return
        self._note_hooked = True
        try:
            import net_notify
            orig = net_notify.Notifier.show
            ov = self

            def show(self_, kind, title, text="", *a, **k):
                try:
                    key = str(k.get("key") or "")
                    ov.note(kind, title, text, key.split(":", 1)[1] if kind == "message" and ":" in key else "")
                except Exception:                              # noqa: BLE001
                    pass
                return orig(self_, kind, title, text, *a, **k)
            net_notify.Notifier.show = show
        except Exception as e:                                 # noqa: BLE001
            print("[overlay] notes:", e)

    def note(self, kind, title, text, onion=""):
        if self.wnd is None or not self.cfg.get("enabled"):
            return
        sh = self.cfg["show"]
        if kind in ("message", "comment", "mention", "group") and not sh.get("msg"):
            return
        if kind in ("call", "ring") and not sh.get("call"):
            return
        if kind in ("online", "room", "now") and not sh.get("friends_now"):
            return
        self.wnd.note(title or "Echoes.NET", text or "", onion if onion and not onion.startswith("g:") else "")
        self._check_game()

    # ── состояние ── #

    def _visible_wanted(self) -> bool:
        w = self.wnd
        if w.open:
            return True
        if w.toasts or (w.notes and self.in_game is not None):
            return True
        if not any(x.get("pin") for x in self.cfg["w"].values()):
            return False
        return self.in_game is not None or not self.cfg.get("only_games", True)

    def _place(self, mon):
        if mon is None:
            scr = QApplication.primaryScreen()
            g = scr.geometry()
        else:
            dpr = 1.0
            scr = QApplication.screenAt(QRect(*mon).center()) or QApplication.primaryScreen()
            dpr = scr.devicePixelRatio()
            g = QRect(int(mon[0] / dpr), int(mon[1] / dpr), int(mon[2] / dpr), int(mon[3] / dpr))
        if self.wnd.geometry() != g:
            self.wnd.setGeometry(g)

    def _check_game(self):
        if self.wnd is None:
            return
        try:
            self.in_game = game_monitor() if sys.platform == "win32" else None
        except Exception:                                      # noqa: BLE001
            self.in_game = None
        self._game_clock()
        want = self._visible_wanted()
        if want:
            if not self.wnd.open:
                self._place(self.in_game)
            self._refresh()
            if not self.wnd.isVisible():
                self.wnd.show()
            if not self.fast.isActive():
                self.fast.start()
        elif self.wnd.isVisible():
            self.wnd.hide()
            self.fast.stop()

    def _frame(self):
        w = self.wnd
        if w is None or not w.isVisible():
            self.fast.stop()
            return
        self._refresh()
        w._tick()
        self.fast.setInterval(16 if (w.open or w.animating() or w._drag) else 250)
        if not w.open and not self._visible_wanted():
            w.hide()
            self.fast.stop()

    def _refresh(self):
        """Живые данные: трек, прогресс, голосовой чат, строка текста."""
        w, d = self.wnd, self.wnd.data
        win = self.win
        sh = getattr(win, "_ya", None)
        t = sh.current_track() if sh is not None else None
        if t:
            from yamusic_theme import cover_pixmap, sanitize_title
            key = str(t.get("path", ""))
            if d.get("_key") != key:
                d.update(_key=key, title=sanitize_title(t.get("title", "")) or "Без названия",
                         artist=t.get("artist", ""), cover_pm=cover_pixmap(t.get("cover", ""), 128, 10, seed=key))
        else:
            d.update(_key=None, title="Ничего не играет", artist="ECHOES", cover_pm=None)
        try:
            eng = win.engine
            dur = float(eng.duration or 0)
            d["frac"] = float(eng.get_position() or 0) / dur if dur > 0 else 0.0
            d["playing"] = bool(sh.is_playing()) if sh is not None else False
            f = float(getattr(eng, "preset_factor", 1.0) or 1.0)
            ms = float(eng.get_position() or 0) * 1000 * f + 150
        except Exception:                                      # noqa: BLE001
            ms = 0
        lv = getattr(win, "lyrics_view", None)
        line = ""
        if lv is not None and getattr(lv, "_has_timings", False):
            for ln in getattr(lv, "_lines", None) or []:
                m = ln.get("ms", -1) if isinstance(ln, dict) else -1
                if m is None or m < 0:
                    continue
                if m <= ms:
                    line = ln.get("text") or ""
                else:
                    break
        d["lyric"] = ("♪ " + line) if line else ""
        voice, title = [], ""
        try:
            net = sh.pages.get("net") if sh is not None else None
            calls = getattr(net, "calls", None)
            view = getattr(calls, "view", None)
            if view and view.get("parts"):
                nd = net.node
                v = calls.engine
                talk = set(v.speaking()) if v else set()
                title = view.get("title", "")
                for pinfo in view["parts"]:
                    o = pinfo["onion"]
                    me = o == nd.me.onion
                    name = "Вы" if me else (nd._contact_name(o) if hasattr(nd, "_contact_name") else o[:8])
                    from net_page import hue_of
                    speaking = bool(v and v.talking and not calls.muted) if me else (o in talk and not calls.deaf)
                    voice.append((name, hue_of(o), speaking, calls.muted if me else bool(pinfo.get("mute")),
                                  "" if me else o))
                d["muted"], d["deaf"] = calls.muted, calls.deaf
                d["vols"] = dict(nd.settings.get("voice_vols") or {})
        except Exception:                                      # noqa: BLE001
            pass
        d["voice"], d["call_title"] = voice, title
        w.update()

    # ── действия ── #

    def run(self, act):
        if self.wnd is None:
            return
        w = self.wnd
        if act == "toggle":
            self.toggle()
            return
        if act == "mute":
            net = getattr(getattr(self.win, "_ya", None), "pages", {}).get("net")
            calls = getattr(net, "calls", None)
            if calls is not None and getattr(calls, "view", None):
                calls.toggle_mute()
                w.toast("\U000f1009 Микрофон выключен" if calls.muted else "\U000f1007 Микрофон включён")
            else:
                w.toast("Вы не в голосовом чате")
        elif act == "play":
            self.win.toggle_play()
            QTimer.singleShot(120, lambda: w.toast("▶ Играет" if self._playing() else "\U000f1013 Пауза"))
        elif act == "next":
            self.win.next_track()
            w.toast("\U000f1010 Следующий трек")
        elif act == "pin":
            on = not all(x.get("pin") for x in self.cfg["w"].values())
            for x in self.cfg["w"].values():
                x["pin"] = on
            self.save_layout()
            w.toast("\U000f1008 Виджеты закреплены" if on else "Виджеты откреплены")
        self._check_game()

    def _playing(self) -> bool:
        sh = getattr(self.win, "_ya", None)
        return bool(sh.is_playing()) if sh is not None else False

    # ── игра: время и FPS ── #

    def _game_clock(self):
        """Раз в секунду: какая игра, сколько в ней (сессия и за сегодня), FPS по кадрам экрана Windows."""
        d = self.wnd.data
        now = time.monotonic()
        if self.in_game is not None:
            if not d.get("game"):
                try:
                    import net_game
                    d["game"] = net_game.detect() or "Игра"
                except Exception:                              # noqa: BLE001
                    d["game"] = "Игра"
                self._g_t0 = now
            d["game_secs"] = now - getattr(self, "_g_t0", now)
            day = time.strftime("%Y-%m-%d")
            gt = self.win.settings.setdefault("game_time", {})
            if day not in gt:
                gt.clear()
            gt.setdefault(day, {})
            gt[day][d["game"]] = int(gt[day].get(d["game"], 0)) + 1
        else:
            d["game"], d["game_secs"] = "", 0
        f = dwm_frames()
        last = getattr(self, "_fps_last", None)
        if f is not None and last is not None and now > last[1]:
            d["fps"] = int(round((f - last[0]) / (now - last[1])))
        self._fps_last = (f, now) if f is not None else None

    # ── голосовой чат из оверлея ── #

    def _calls(self):
        net = getattr(getattr(self.win, "_ya", None), "pages", {}).get("net")
        return net, getattr(net, "calls", None)

    def voice_action(self, act):
        net, calls = self._calls()
        w = self.wnd
        if net is None or calls is None:
            return
        nd = net.node
        if act == "vmute":
            if getattr(calls, "view", None):
                calls.toggle_mute()
                w.toast("\U000f1009 Микрофон выключен" if calls.muted else "\U000f1007 Микрофон включён")
        elif act == "vdeaf":
            if getattr(calls, "view", None):
                calls.toggle_deaf()
                w.toast("\U000f100a Звук собеседников выключен" if calls.deaf else "\U000f100a Звук включён")
        elif act == "vleave":
            calls.hang_up()
            w.toast("Вы вышли из голосового")
        elif act == "vinvite":
            from PyQt6.QtGui import QCursor
            from PyQt6.QtWidgets import QMenu
            m = QMenu(w)
            m.setStyleSheet("QMenu{background:rgba(24,24,28,245);color:#fff;border:1px solid rgba(255,255,255,0.2);"
                            "border-radius:10px;padding:6px;} QMenu::item{padding:6px 16px;border-radius:6px;}"
                            "QMenu::item:selected{background:rgba(255,255,255,0.15);}")
            n = 0
            for c in nd.store.contacts("friend"):
                if net._online(c["onion"]):
                    nm = c.get("name") or c["onion"][:8]
                    m.addAction(nm, lambda o=c["onion"], nm=nm: (nd.call(nd.call_invite, o), w.toast(f"\U000f100b Зовём {nm}…")))
                    n += 1
            if not n:
                m.addAction("Никого из друзей нет в сети").setEnabled(False)
            m.exec(QCursor.pos())
        elif act.startswith(("vol+:", "vol-:")):
            o = act[5:]
            vols = dict(nd.settings.get("voice_vols") or {})
            v = max(0.0, min(2.0, float(vols.get(o, 1.0)) + (0.1 if act.startswith("vol+") else -0.1)))
            vols[o] = round(v, 2)
            nd.settings["voice_vols"] = vols
            nd.save_settings()
            eng = calls.engine
            if eng is not None:
                with eng.lock:
                    if o in eng.srcs:
                        eng.srcs[o].vol = v
            w.toast(f"Громкость {nd._contact_name(o) if hasattr(nd, '_contact_name') else o[:8]}: {int(v * 100)} %")
        self._refresh()

    def send_reply(self, onion, text):
        net, _calls = self._calls()
        if net is None or not onion or net.node is None:
            return
        nd = net.node
        if net.running():
            nd.call(nd.send_message, onion, text, None, None)
        else:
            import asyncio
            asyncio.run(nd.send_message(onion, text, None, None))

    def widget_action(self, act):
        if act.startswith("v") and act not in ("prev",):
            self.voice_action(act)
            return
        if act == "prev":
            self.win.prev_track()
        elif act == "next":
            self.win.next_track()
        elif act == "play":
            self.win.toggle_play()
        QTimer.singleShot(80, self._refresh)

    def toggle(self):
        w = self.wnd
        if w is None:
            return
        opening = not w.open
        if opening:
            self._place(self.in_game or game_monitor())
            self._refresh()
        w.set_open(opening)
        w.set_interactive(opening)
        if not self.fast.isActive():
            self.fast.start()
        self.fast.setInterval(16)
        self._check_game()

    def save_layout(self):
        c = OD.load(self.win.settings)
        c["w"] = self.cfg["w"]
        self.win.settings["overlay"] = c
        try:
            self.win.save_settings()
        except Exception:                                      # noqa: BLE001
            pass


class _RATIO(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("n", ctypes.c_uint32), ("d", ctypes.c_uint32)]


class _DWM_TIMING(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("cbSize", ctypes.c_uint32), ("rateRefresh", _RATIO), ("qpcRefreshPeriod", ctypes.c_uint64),
                ("rateCompose", _RATIO), ("qpcVBlank", ctypes.c_uint64), ("cRefresh", ctypes.c_uint64),
                ("cDXRefresh", ctypes.c_uint32), ("qpcCompose", ctypes.c_uint64), ("cFrame", ctypes.c_uint64),
                # Remaining fields of the SDK's packed DWM_TIMING_INFO.
                # The previous 220-byte tail allocated 284 bytes but advertised
                # 292 to Windows, overwriting the Python allocator's metadata.
                ("rest", ctypes.c_byte * 228)]


def dwm_frames():
    """Сколько кадров собрал рабочий стол Windows (для «FPS ≈» в оверлее) или None."""
    if sys.platform != "win32":
        return None
    try:
        t = _DWM_TIMING()
        t.cbSize = ctypes.sizeof(t)
        get_timing = ctypes.windll.dwmapi.DwmGetCompositionTimingInfo
        get_timing.argtypes = [wintypes.HWND, ctypes.POINTER(_DWM_TIMING)]
        get_timing.restype = wintypes.LONG
        if get_timing(None, ctypes.byref(t)) != 0:
            return None
        return int(t.cFrame)
    except Exception:                                          # noqa: BLE001
        return None


def get(win) -> Overlay | None:
    ov = win.__dict__.get("_echoes_overlay")
    if ov is None and sys.platform == "win32":
        ov = win._echoes_overlay = Overlay(win)
    return ov


def apply(win):
    """Включить/выключить/перечитать настройки оверлея."""
    if sys.platform != "win32":
        return
    ov = win.__dict__.get("_echoes_overlay")
    if ov is None and not OD.load(win.settings).get("enabled"):
        return                                                 # выключен и не создавался — ничего не делать
    get(win).apply()
