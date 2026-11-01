"""Regression for DWM writing beyond the overlay's timing buffer on Windows."""
import ctypes
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from overlay_live import _DWM_TIMING, dwm_frames


class OverlayMemory(unittest.TestCase):
    def test_sdk_size_and_frame_offset(self):
        self.assertEqual(ctypes.sizeof(_DWM_TIMING), 292)
        self.assertEqual(_DWM_TIMING.cFrame.offset, 56)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows DWM API')
    def test_windows_writes_only_inside_allocated_structure(self):
        class Guarded(ctypes.Structure):
            _pack_ = 1
            _fields_ = [('before', ctypes.c_ubyte * 32), ('timing', _DWM_TIMING),
                        ('after', ctypes.c_ubyte * 32)]
        guarded = Guarded()
        guarded.before[:] = [0xA5] * 32
        guarded.after[:] = [0x5A] * 32
        guarded.timing.cbSize = ctypes.sizeof(guarded.timing)
        native = ctypes.windll.dwmapi.DwmGetCompositionTimingInfo
        from ctypes import wintypes
        native.argtypes = [wintypes.HWND, ctypes.POINTER(_DWM_TIMING)]
        native.restype = wintypes.LONG
        for _ in range(100):
            result = native(None, ctypes.byref(guarded.timing))
            self.assertEqual(bytes(guarded.before), b'\xa5' * 32)
            self.assertEqual(bytes(guarded.after), b'\x5a' * 32)
            if result:
                self.skipTest('Windows compositor unavailable')
        self.assertIsInstance(dwm_frames(), int)


if __name__ == '__main__':
    unittest.main()
