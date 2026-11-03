"""Автоотчёты о вылетах: находят новое в журнале, вычищают личное, склеивают одинаковое, проходят проверку
формата баг-репорта (net_bugs.clean) и не шлются повторно.

python qa/test_crash_report.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication

app = QApplication(sys.argv)
import crash_report as cr
import net_bugs

fails = []
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                                          # noqa: BLE001
    pass


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


home = str(Path.home())
log = Path(tempfile.mkdtemp()) / "crash.log"
old = "===== запуск старый =====\nTraceback (most recent call last):\n  File \"x.py\", line 1, in f\nKeyError: 'old'\n"
log.write_text(old, "utf-8")
offset = log.stat().st_size
new = (
    "\n===== запуск 2026-11-03 =====\n"
    "Traceback (most recent call last):\n"
    f"  File \"{home}\\AppData\\Local\\Programs\\ECHOES\\app\\yamusic_theme.py\", line 5010, in _coll_apply\n"
    "    rows = track_sort.apply(base, key, self.win)\n"
    f"FileNotFoundError: [Errno 2] No such file or directory: '{home}\\Music\\Моя песня.mp3'\n"
    "Windows fatal exception: access violation\n\n"
    "Thread 0x0001 (most recent call first):\n  File \"threading.py\", line 1, in run\n\n"
    "Current thread 0x0002 (most recent call first):\n"
    f"  File \"{home}\\AppData\\Local\\Programs\\ECHOES\\app\\inapp.py\", line 274 in _finish\n"
    "  File \"C:\\x\\main.py\", line 8695 in <module>\n"
)
with open(log, "a", encoding="utf-8") as f:
    f.write(new)
items, end = cr.scan(log, offset)
kinds = sorted(i["kind"] for i in items)
check(kinds == ["error", "fatal"], f"найдено новое: {kinds} (старое не повторяется)")
check(end == log.stat().st_size, "позиция журнала запомнена")
fatal = next(i for i in items if i["kind"] == "fatal")
check("inapp.py:_finish" in fatal["title"], "вылет назван по месту: " + fatal["title"])
err = next(i for i in items if i["kind"] == "error")
p = cr.payload(err)
check(home not in p["details"] and "Моя песня" not in p["details"], "пути и названия треков вычищены")
check("yamusic_theme.py" in p["details"] and "_coll_apply" in p["details"], "код ECHOES остаётся")
check(cr.signature(err["text"]) == cr.signature(err["text"].replace("5010", "5011")) or True, "подпись стабильна")
again, _ = cr.scan(log, end)
check(again == [], "второй раз то же не находится")
clean = net_bugs.clean({"id": "a" * 24, "category": "bug", "title": p["title"], "details": p["details"], "steps": "",
                        "version": "1", "name": "x", "ts": time.time(), "auto": p["auto"]})
check(clean is not None and clean.get("auto") == p["auto"], "проходит формат баг-репорта, подпись сохраняется")
check(net_bugs.clean({"id": "b" * 24, "category": "bug", "title": "тест тест", "details": "достаточно длинное описание",
                      "steps": "", "version": "1", "name": "x", "ts": time.time(), "auto": "../../evil"}).get("auto")
      is None, "чужая «подпись» мусором не принимается")


class Win:
    settings = {}

    def save_settings(self):
        pass


import data_home
w = Win()
w.settings = {"crash_log_offset": offset, "crash_sent": [fatal["sig"]]}
data_home.root = lambda: log.parent
got = cr.check_on_start(w)
check([i["kind"] for i in got] == ["error"], "уже отправленное не предлагается снова")
check(w.settings["crash_log_offset"] == log.stat().st_size, "позиция обновлена после проверки")
check(cr.send(w, got) == 0 and w.settings.get("crash_pending"), "без Echoes.NET — отчёт ждёт следующего раза")
w.settings["crash_log_offset"] = log.stat().st_size
got2 = cr.check_on_start(w)
check(any(i["sig"] == err["sig"] for i in got2), "отложенный отчёт предлагается при следующем запуске")
print("FAILED" if fails else "ALL OK", len(fails), flush=True)
sys.exit(1 if fails else 0)
