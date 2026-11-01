# win_taskbar.py
"""
Значок ECHOES в панели задач Windows (ITaskbarList3):
  * прогресс играющего трека прямо на значке (зелёный — играет, жёлтый — пауза);
  * кнопки ◀  ⏯  ▶ в превью окна (наведите на значок) — как у Spotify.

Нажатие кнопки превью Проводник присылает окну через SendMessage (WM_COMMAND, THBN_CLICKED) — мимо фильтров
событий Qt, поэтому оконная процедура главного окна подменяется (и возвращается при закрытии). Объект один на
окно и живёт, пока живо окно (get) — иначе Windows вызвала бы уже освобождённую процедуру. После перезапуска
Проводника кнопки добавляются заново (сообщение TaskbarButtonCreated).
"""
from __future__ import annotations

import ctypes
import struct
import sys
from ctypes import wintypes
from pathlib import Path

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, Qt, QTimer
from PyQt6.QtGui import QColor, QImage, QPainter, QPainterPath
from PyQt6.QtWidgets import QApplication

from taskbar_identity import _GUID

CLSID_TaskbarList = "56FDF344-FD6D-11d0-958A-006097C9A090"
IID_ITaskbarList3 = "EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF"
TBPF_NOPROGRESS, TBPF_NORMAL, TBPF_PAUSED = 0, 2, 8
THB_ICON, THB_TOOLTIP, THB_FLAGS = 2, 4, 8
WM_COMMAND, THBN_CLICKED = 0x0111, 0x1800
ID_PREV, ID_PLAY, ID_NEXT = 41, 42, 43


class THUMBBUTTON(ctypes.Structure):
    _fields_ = [("dwMask", wintypes.DWORD), ("iId", wintypes.UINT), ("iBitmap", wintypes.UINT),
                ("hIcon", wintypes.HICON), ("szTip", wintypes.WCHAR * 260), ("dwFlags", wintypes.DWORD)]


def _icon_png(kind: str, size=32) -> bytes:
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 255, 255))
    s = size / 32.0
    path = QPainterPath()
    if kind == "play":
        path.moveTo(10 * s, 7 * s); path.lineTo(25 * s, 16 * s); path.lineTo(10 * s, 25 * s)
    elif kind == "pause":
        path.addRoundedRect(9 * s, 7 * s, 5 * s, 18 * s, 1.5 * s, 1.5 * s)
        path.addRoundedRect(18 * s, 7 * s, 5 * s, 18 * s, 1.5 * s, 1.5 * s)
    elif kind == "prev":
        path.addRect(7 * s, 8 * s, 3 * s, 16 * s)
        path.moveTo(25 * s, 8 * s); path.lineTo(11 * s, 16 * s); path.lineTo(25 * s, 24 * s)
    else:
        path.addRect(22 * s, 8 * s, 3 * s, 16 * s)
        path.moveTo(7 * s, 8 * s); path.lineTo(21 * s, 16 * s); path.lineTo(7 * s, 24 * s)
    path.closeSubpath()
    p.drawPath(path)
    p.end()
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


def _hicon(kind: str) -> int:
    """PNG внутри .ico (Vista+) → HICON через LoadImage."""
    d = Path.home() / ".neon_player" / "ui_cache"
    f = d / f"tb_{kind}.ico"
    if not f.exists():
        png = _icon_png(kind)
        d.mkdir(parents=True, exist_ok=True)
        f.write_bytes(struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", 32, 32, 0, 0, 1, 32, len(png), 22) + png)
    user32 = ctypes.windll.user32
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                  wintypes.UINT]
    return int(user32.LoadImageW(None, str(f), 1, 0, 0, 0x10) or 0)


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


def get(win) -> "Taskbar":
    """Один объект на окно (живёт вместе с окном)."""
    tb = win.__dict__.get("_echoes_taskbar")
    if tb is None or tb.hwnd != int(win.winId()):
        tb = Taskbar(win)
        win._echoes_taskbar = tb
    return tb


class Taskbar:
    def __init__(self, win):
        if sys.platform != "win32":
            raise OSError("только Windows")
        self.win = win
        self.hwnd = int(win.winId())
        self.progress_on = True
        self._buttons_on = False
        self._added = False
        self._state = None
        self._val = None
        self._playing = None
        self.ptr = ctypes.c_void_p()
        ole = ctypes.windll.ole32
        ole.CoInitialize(None)
        hr = ole.CoCreateInstance(ctypes.byref(_GUID.parse(CLSID_TaskbarList)), None, 1,
                                  ctypes.byref(_GUID.parse(IID_ITaskbarList3)), ctypes.byref(self.ptr))
        if hr != 0 or not self.ptr.value:
            raise OSError(f"ITaskbarList3: {hr:#x}")
        vt = ctypes.cast(ctypes.cast(self.ptr, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
        F = ctypes.WINFUNCTYPE
        self._HrInit = F(ctypes.HRESULT, ctypes.c_void_p)(vt[3])
        self._SetValue = F(ctypes.HRESULT, ctypes.c_void_p, wintypes.HWND, ctypes.c_ulonglong,
                           ctypes.c_ulonglong)(vt[9])
        self._SetState = F(ctypes.HRESULT, ctypes.c_void_p, wintypes.HWND, ctypes.c_int)(vt[10])
        self._AddButtons = F(ctypes.HRESULT, ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                             ctypes.POINTER(THUMBBUTTON))(vt[15])
        self._UpdButtons = F(ctypes.HRESULT, ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                             ctypes.POINTER(THUMBBUTTON))(vt[16])
        self._Release = F(ctypes.c_ulong, ctypes.c_void_p)(vt[2])
        self.hr_init = self._HrInit(self.ptr)
        self.icons = {k: _hicon(k) for k in ("prev", "play", "pause", "next")}
        u = ctypes.windll.user32
        u.SetWindowLongPtrW.restype = ctypes.c_void_p
        u.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        u.CallWindowProcW.restype = ctypes.c_ssize_t
        u.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self._u = u
        self.msg_created = u.RegisterWindowMessageW("TaskbarButtonCreated")
        self._proc = WNDPROC(self._wndproc)
        self._old = u.SetWindowLongPtrW(self.hwnd, -4, ctypes.cast(self._proc, ctypes.c_void_p))  # GWLP_WNDPROC
        QApplication.instance().aboutToQuit.connect(self.close)   # до уничтожения окна — вернуть процедуру

    def _wndproc(self, hwnd, msg, wp, lp):
        try:
            if msg == WM_COMMAND and (wp >> 16) & 0xFFFF == THBN_CLICKED:
                bid = wp & 0xFFFF
                QTimer.singleShot(0, lambda: self.clicked(bid))
                return 0
            if msg == self.msg_created:
                QTimer.singleShot(0, self.reset)
        except Exception:                                      # noqa: BLE001
            pass
        return self._u.CallWindowProcW(self._old, hwnd, msg, wp, lp)

    # ── прогресс ── #

    def update(self, frac, playing):
        if not self.progress_on or frac is None:
            self._set_state(TBPF_NOPROGRESS)
        else:
            self._set_state(TBPF_NORMAL if playing else TBPF_PAUSED)
            v = int(max(0.0, min(1.0, frac)) * 1000)
            if v != self._val:
                self._val = v
                self._SetValue(self.ptr, self.hwnd, v, 1000)
        if self._buttons_on and self._added and bool(playing) != self._playing:
            self._playing = bool(playing)
            self._push(update=True)

    def _set_state(self, st):
        if st != self._state:
            self._state = st
            self._val = None
            self._SetState(self.ptr, self.hwnd, st)

    def clear(self):
        try:
            self._set_state(TBPF_NOPROGRESS)
        except Exception:                                      # noqa: BLE001
            pass

    # ── кнопки превью ── #

    def _arr(self, enabled=True):
        arr = (THUMBBUTTON * 3)()
        tips = {ID_PREV: "Предыдущий", ID_PLAY: "Пауза" if self._playing else "Играть", ID_NEXT: "Следующий"}
        for b, (i, ic) in zip(arr, ((ID_PREV, "prev"), (ID_PLAY, "pause" if self._playing else "play"),
                                    (ID_NEXT, "next"))):
            b.dwMask = THB_ICON | THB_TOOLTIP | THB_FLAGS
            b.iId = i
            b.hIcon = self.icons[ic]
            b.szTip = tips[i]
            b.dwFlags = 0 if enabled else 0x8 | 0x1           # THBF_HIDDEN | THBF_DISABLED
        return arr

    def _push(self, update=False):
        arr = self._arr(self._buttons_on)
        if update or self._added:
            return self._UpdButtons(self.ptr, self.hwnd, 3, arr)
        hr = self._AddButtons(self.ptr, self.hwnd, 3, arr)
        self._added = hr == 0
        return hr

    def set_buttons(self, on):
        self._buttons_on = bool(on)
        if on or self._added:
            self._push(update=self._added)

    def reset(self):
        """Проводник пересоздал кнопку окна — всё заново."""
        self._added = False
        self._state = None
        self._val = None
        self.hr_init = self._HrInit(self.ptr)
        if self._buttons_on:
            self._push()

    def clicked(self, bid):
        w = self.win
        fn = {ID_PREV: getattr(w, "prev_track", None), ID_PLAY: getattr(w, "toggle_play", None),
              ID_NEXT: getattr(w, "next_track", None)}.get(bid)
        if fn is not None:
            fn()

    def close(self):
        """Вернуть окну его процедуру (до уничтожения окна)."""
        try:
            if self._old:
                self._u.SetWindowLongPtrW(self.hwnd, -4, self._old)
                self._old = None
        except Exception:                                      # noqa: BLE001
            pass
        try:
            self._Release(self.ptr)
        except Exception:                                      # noqa: BLE001
            pass
