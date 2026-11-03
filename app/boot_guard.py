"""Откат обновления, которое не запускается.

update.ps1 перед заменой файлов сохраняет прошлую версию программы в <папка ECHOES>/app.prev.
Здесь, в самом начале main.py: каждый запуск отмечается «в процессе»; через ~15 с после показа окна (и при
обычном выходе) — «запустился нормально». Если новая версия ДВАЖДЫ подряд не дошла до «нормально» (вылет,
ошибка при старте), ECHOES возвращает файлы из app.prev, запоминает версию как плохую (обновлятор её больше
не предлагает) и перезапускается уже прошлой версией. Настройки, библиотека и плейлисты не трогаются —
они лежат в другой папке.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

APP = Path(__file__).resolve().parent
PREV = APP.parent / "app.prev"
STATE = APP.parent / "boot_state.json"
FAILS_TO_ROLLBACK = 2


def _version(folder: Path) -> str:
    try:
        return (folder / "version.txt").read_text("utf-8").strip()
    except OSError:
        return ""


def state() -> dict:
    try:
        d = json.loads(STATE.read_text("utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d: dict):
    try:
        STATE.write_text(json.dumps(d, ensure_ascii=False), "utf-8")
    except OSError:
        pass


def _alive(pid) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0 or pid == os.getpid():
        return False
    if sys.platform == "win32":
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return code.value == 259                                          # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def enter() -> None:
    """Самое начало запуска. Может откатить версию и перезапустить ECHOES (тогда процесс завершается)."""
    if os.environ.get("ECHOES_NO_BOOT_GUARD") == "1" or not (APP.parent / "python").is_dir():
        return                                                            # запуск из исходников / тесты
    ver = _version(APP)
    d = state()
    if d.get("version") == ver and _alive(d.get("pid")):
        return                       # ECHOES уже открыт — это второй запуск, он сейчас просто покажет окно
    if d.get("version") != ver:
        d = {"version": ver, "fails": 0, "pending": False, "bad": d.get("bad") or []}
    elif d.get("pending") and not _alive(d.get("pid")):                   # прошлый запуск так и не «встал»
        d["fails"] = int(d.get("fails") or 0) + 1
    prev = _version(PREV)
    if d["fails"] >= FAILS_TO_ROLLBACK and prev and prev != ver and (PREV / "main.py").exists():
        if _rollback(ver, prev, d):
            return
    d.update(pending=True, pid=os.getpid(), started=time.time())
    _save(d)


def _rollback(bad: str, prev: str, d: dict) -> bool:
    try:
        for item in PREV.iterdir():
            dst = APP / item.name
            if item.is_dir():
                shutil.copytree(item, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dst)
        shutil.rmtree(APP / "__pycache__", ignore_errors=True)
    except OSError as e:
        print("[boot] откат не удался:", e)
        return False
    bad_list = list(dict.fromkeys((d.get("bad") or []) + [bad]))[-10:]
    _save({"version": prev, "fails": 0, "pending": False, "bad": bad_list, "rolled_back_from": bad,
           "rolled_back_at": time.time()})
    try:
        import data_home
        log = data_home.root() / "crash.log"
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"\n[boot] версия {bad} дважды не запустилась — вернул {prev}\n")
    except OSError:
        pass
    exe = sys.executable
    if exe.lower().endswith("python.exe") and (Path(exe).parent / "pythonw.exe").exists():
        exe = str(Path(exe).parent / "pythonw.exe")
    subprocess.Popen([exe] + sys.argv, cwd=str(APP), close_fds=True)
    os._exit(0)
    return True


def mark_ok() -> None:
    """Запуск удался (окно показано и живёт / обычный выход)."""
    d = state()
    if d.get("pending") or d.get("fails"):
        d.update(pending=False, fails=0, ok_at=time.time())
        _save(d)


def rolled_back_notice() -> str:
    """Один раз после отката — что сказать человеку."""
    d = state()
    bad = d.pop("rolled_back_from", None)
    if not bad:
        return ""
    _save(d)
    return (f"Версия {bad} не запустилась на этом компьютере — ECHOES вернул прошлую ({d.get('version')}). "
            "Автор получит исправление, а эта версия больше не будет предлагаться.")
