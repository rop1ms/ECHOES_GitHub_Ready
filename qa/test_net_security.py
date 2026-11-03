"""Данные от чужих людей в Echoes.NET безопасны: «картинки-бомбы» не декодируются, присланные файлы помечены
«из интернета», опасные типы требуют подтверждения, ники и тексты не превращаются в HTML-разметку.

python qa/test_net_security.py
"""
import os
import struct
import sys
import tempfile
import time
import zlib
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from PyQt6.QtCore import QBuffer, QIODevice, Qt
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication

app = QApplication(sys.argv)
fails = []
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                                          # noqa: BLE001
    pass


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


def png_bomb(w, h) -> bytes:
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"\x00" * 64)) + chunk(b"IEND", b"")


import img_guard
t0 = time.time()
img = img_guard.load(png_bomb(30000, 30000))
check(img.isNull() and time.time() - t0 < 2, "картинка-бомба 30000×30000 не декодируется")
check(img_guard.safe_size(png_bomb(30000, 30000)) is None, "заявленный размер бомбы отклонён заранее")
big = QImage(4000, 1000, QImage.Format.Format_RGB32)
big.fill(QColor("#336699"))
buf = QBuffer()
buf.open(QIODevice.OpenModeFlag.WriteOnly)
big.save(buf, "PNG")
small = img_guard.load(bytes(buf.data()))
check(not small.isNull() and max(small.width(), small.height()) <= 2048, f"большое фото уменьшено при чтении: "
      f"{small.width()}×{small.height()}")

import net_files
for ext in (".exe", ".iso", ".url", ".msix", ".py", ".xlsm", ".lnk", ".chm"):
    check(ext in net_files.DANGER, f"{ext} — спросить перед открытием")
p = Path(tempfile.mkdtemp()) / "track.mp3"
p.write_bytes(b"x")
net_files.mark_internet(p)
if os.name == "nt":
    zone = Path(str(p) + ":Zone.Identifier")
    check("ZoneId=3" in open(zone, encoding="ascii").read(), "присланный файл помечен «из интернета»")
check(net_files.safe_name("..\\..\\Windows\\evil.exe") == "evil.exe", "путь в имени файла отбрасывается")

import net_page
w = net_page.lbl("<img src='file:///C:/x.png'><b>Ник</b>")
check(w.textFormat() == Qt.TextFormat.PlainText, "ник с HTML показывается как текст, не разметка")
src_q = (ROOT / "app" / "net_queue.py").read_text("utf-8")
src_c = (ROOT / "app" / "net_challenge.py").read_text("utf-8")
check("_h.escape(str(who))" in src_q and "_h.escape(str(p.get('theme')" in src_c,
      "чужие имя/трек/тема в HTML-подписях экранируются")
print("FAILED" if fails else "ALL OK", len(fails), flush=True)
sys.exit(1 if fails else 0)
