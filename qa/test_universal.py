"""ECHOES на любом ПК: уровень железа и лёгкий режим, программный OpenGL без GPU-слоёв, запись настроек в фоне,
диагностика для баг-репортов, слой окна не остаётся «стеной», громкость и бас реально работают.

python qa/test_universal.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from PyQt6.QtWidgets import QApplication, QDialog, QMainWindow, QWidget

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


import sysprobe

base = {"cpu": 8, "ram_gb": 16.0, "screen_px": 1920 * 1080, "integrated": False}
check(sysprobe.tier(dict(base, gpu={"ok": False})) == "low", "без ускорения видеокартой — уровень low")
check(sysprobe.tier(dict(base, cpu=4, integrated=True, gpu={"ok": True})) == "mid", "встройка и 4 потока — mid")
check(sysprobe.tier(dict(base, gpu={"ok": True})) == "high", "дискретная, 8 потоков, 16 ГБ — high")
check(sysprobe._integrated("Intel(R) UHD Graphics 620") and not sysprobe._integrated("NVIDIA GeForce RTX 3060"),
      "встроенная видеокарта узнаётся")

s = {"motion_profile": "vivid", "ui_animations": True, "performance": {"animated_images": True}}
sysprobe.set_lite(s, True)
check(s["lite_mode"] and s["ui_animations"] is False and s["performance"]["animated_images"] is False,
      "лёгкий режим выключает тяжёлое")
sysprobe.set_lite(s, False)
check(s["motion_profile"] == "vivid" and s["ui_animations"] is True and s["performance"]["animated_images"] is True,
      "выключение возвращает как было")

orig = sysprobe.probe_gpu
sysprobe.probe_gpu = lambda: {"ok": False, "renderer": "llvmpipe (LLVM 15.0.7, 256 bits)", "vendor": "Mesa", "version": "4.5"}
for k in ("ECHOES_SOFT_GL", "ECHOES_NO_GPU_NEBULA"):
    os.environ.pop(k, None)
st = {}
sysprobe.apply_startup(st)
check(os.environ.get("ECHOES_SOFT_GL") == "1" and os.environ.get("ECHOES_NO_GPU_NEBULA") == "1",
      "программный OpenGL — GPU-слои выключены")
check(st.get("lite_mode") is True and st.get("lite_reason") == "auto", "слабый ПК — лёгкий режим сам")
import ya_wave
check(not ya_wave.gpu_enabled(), "туманность не лезет в OpenGL без видеокарты")
st2 = {"perf_user_set": True, "motion_profile": "vivid"}
sysprobe.apply_startup(st2)
check(not st2.get("lite_mode") and st2["motion_profile"] == "vivid", "свои настройки человека не трогаются")
sysprobe.probe_gpu = orig
os.environ.pop("ECHOES_SOFT_GL", None)
os.environ.pop("ECHOES_NO_GPU_NEBULA", None)

import atomic_json
tmp = Path(tempfile.mkdtemp()) / "settings.json"
atomic_json.save_json(tmp, {"a": 1})
atomic_json.save_json_async(tmp, {"a": 2})
check(atomic_json.load_json(tmp, {})["a"] == 2, "сразу после сохранения читается новое")
for i in range(50):
    atomic_json.save_json_async(tmp, {"a": 100 + i})
atomic_json.flush(5)
check(json.loads(tmp.read_text("utf-8"))["a"] == 149, "в файле — последнее сохранение (склейка)")
check(json.loads(atomic_json.backup_path(tmp).read_text("utf-8"))["a"] == 149, "и копия обновлена")

import diagnostics
rep = diagnostics.report(None)
check(rep.startswith("ECHOES") and str(Path.home()) not in rep, "диагностика без личных путей")
import net_bugs
p = net_bugs.clean({"id": "a" * 24, "category": "bug", "title": "Тест", "details": "описание проблемы тут",
                    "steps": "", "version": "1", "name": "x", "ts": time.time(), "diag": rep})
check(p is not None and p.get("diag") == rep.strip()[:4000], "диагностика доходит в репорте")

import inapp
win = QMainWindow()
win.resize(900, 700)
win.setCentralWidget(QWidget())
win.show()
inapp.install(win) if hasattr(inapp, "install") else None
dlg = QDialog(win)
L = inapp.Layer(win, dlg, "тест")
app.processEvents()
from PyQt6 import sip
sip.delete(dlg)                                           # окно удалилось мимо обычного закрытия
t0 = time.time()
while time.time() - t0 < 2.5:
    app.processEvents()
    time.sleep(0.02)
check(L._closing, "слой без окна сам убирается (не перехватывает клики)")

# громкость и бас: настоящий движок, вывод без звуковой карты
out = Path(tempfile.mkdtemp()) / "loud.json"
py = sys.executable
r = subprocess.run([py, str(ROOT / "qa" / "probe_loudness.py"), "--json", str(out)], capture_output=True,
                   timeout=240)
if out.exists():
    d = json.loads(out.read_text("utf-8"))
    b0, b10 = d["boost x1 bass 0%"], d["boost x10 bass 0%"]
    bb = d["boost x1 bass 100%"]
    check(b10["rms_db"] - b0["rms_db"] >= 3.0, f"«Экстремальная громкость» ×10 громче на {b10['rms_db'] - b0['rms_db']:.1f} дБ")
    check(bb["bass_vs_mid"] - b0["bass_vs_mid"] >= 3.0,
          f"басс буст 100% — бас к середине +{bb['bass_vs_mid'] - b0['bass_vs_mid']:.1f} дБ")
    check(max(v["peak"] for v in d.values()) <= 0.99, "без цифрового клиппинга")
else:
    check(False, "замер громкости не выполнился: " + r.stdout.decode("utf-8", "replace")[-300:])
print("FAILED" if fails else "ALL OK", len(fails), flush=True)
os._exit(1 if fails else 0)
