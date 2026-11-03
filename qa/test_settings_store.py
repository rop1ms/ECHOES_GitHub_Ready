"""settings.json без балласта: тяжёлые разделы — в state/, пишутся только при изменении; старый формат
и откат на прошлую версию не теряют данных.

python qa/test_settings_store.py
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import atomic_json
import settings_store as ss

fails = []
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                                          # noqa: BLE001
    pass


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        fails.append(msg)


tmp = Path(tempfile.mkdtemp())
sf = tmp / "settings.json"
big = {"t%d" % i: {"plays": i, "title": "x" * 40} for i in range(3000)}
s = {"theme": "Echoes Music", "volume": 80, "play_history": big, "listening_diary": {"2026-11-03": ["a"] * 50}}
ss.save(sf, s)
atomic_json.flush(5)
slim = json.loads(sf.read_text("utf-8"))
check("play_history" not in slim and slim["volume"] == 80, "в settings.json — только лёгкое")
check((tmp / "state" / "play_history.json").exists(), "тяжёлый раздел — отдельным файлом")
check(sf.stat().st_size < 2000, f"settings.json маленький: {sf.stat().st_size} байт")

m0 = (tmp / "state" / "play_history.json").stat().st_mtime_ns
s["volume"] = 70
ss.save(sf, s)
atomic_json.flush(5)
check((tmp / "state" / "play_history.json").stat().st_mtime_ns == m0, "неизменённый раздел не перезаписывается")
s["play_history"]["t1"]["plays"] = 99
ss.save(sf, s)
atomic_json.flush(5)
check(json.loads((tmp / "state" / "play_history.json").read_text("utf-8"))["t1"]["plays"] == 99,
      "изменённый раздел записан")

ss._LAST.clear()
loaded = atomic_json.load_json(sf, {})
ss.load_into(loaded, sf)
check(loaded["play_history"]["t1"]["plays"] == 99 and loaded["listening_diary"], "при запуске всё собирается обратно")

old = dict(loaded, play_history={"old": {"plays": 1}})     # старый формат: раздел прямо в settings.json
ss._LAST.clear()
ss.load_into(old, sf)
check(old["play_history"] == {"old": {"plays": 1}}, "раздел внутри settings.json (старый формат) главнее")
empty = dict(loaded, play_history={})                       # прошлая версия после отката сохранила «пусто»
ss.load_into(empty, sf)
check(empty["play_history"].get("t1", {}).get("plays") == 99, "пустой раздел от прошлой версии не затирает данные")

real = Path.home() / "Echoes" / "settings.json"
if real.exists():
    d = json.loads(real.read_text("utf-8"))
    before = len(json.dumps(d, ensure_ascii=False, separators=(",", ":")).encode())
    after = len(json.dumps({k: v for k, v in d.items() if k not in ss.HEAVY}, ensure_ascii=False,
                           separators=(",", ":")).encode())
    print(f"     ваш settings.json: {before // 1024} КБ → {after // 1024} КБ на каждое сохранение")
shutil.rmtree(tmp, ignore_errors=True)
print("FAILED" if fails else "ALL OK", len(fails), flush=True)
sys.exit(1 if fails else 0)
