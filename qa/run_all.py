"""Все тесты ECHOES одной командой (локально и в CI на GitHub): каждый qa/test_*.py и qa/ui_smoke.py —
в своём процессе, без окна (offscreen), с ограничением времени. Код выхода 1, если что-то упало.

python qa/run_all.py [--out папка-логов] [--only имя,имя] [--timeout сек]
В CI (ECHOES_CI=1) тесты из NEED_DEVICE пропускаются: им нужна звуковая карта, видеокарта или микрофон.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

QA = Path(__file__).resolve().parent
NEED_DEVICE = {                     # на сервере GitHub нет звука/GPU/микрофона — там их не гоняем
    "test_voice_device_probe",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    ap.add_argument("--only", default="")
    ap.add_argument("--timeout", type=int, default=600)
    a = ap.parse_args()
    out = Path(a.out) if a.out else None
    if out:
        out.mkdir(parents=True, exist_ok=True)
    tests = sorted(QA.glob("test_*.py")) + [QA / "ui_smoke.py"]
    if a.only:
        want = {x.strip() for x in a.only.split(",") if x.strip()}
        tests = [t for t in tests if t.stem in want]
    ci = os.environ.get("ECHOES_CI") == "1"
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
    failed, results = [], []
    for t in tests:
        if ci and t.stem in NEED_DEVICE:
            results.append((t.stem, "skip", 0.0))
            print(f"skip  {t.stem}  (нужно устройство)", flush=True)
            continue
        t0 = time.time()
        try:
            p = subprocess.run([sys.executable, str(t)], cwd=str(QA.parent), env=env, capture_output=True,
                               timeout=a.timeout)
            code, log = p.returncode, p.stdout + p.stderr
        except subprocess.TimeoutExpired as e:
            code, log = -9, (e.stdout or b"") + (e.stderr or b"") + b"\n[TIMEOUT]"
        dt = time.time() - t0
        ok = code == 0
        results.append((t.stem, "ok" if ok else f"FAIL({code})", dt))
        print(f"{'ok  ' if ok else 'FAIL'}  {t.stem}  {dt:.0f} c", flush=True)
        if out:
            (out / f"{t.stem}.log").write_bytes(log)
        if not ok:
            failed.append(t.stem)
            tail = log.decode("utf-8", "replace").strip().splitlines()[-25:]
            print("      " + "\n      ".join(tail), flush=True)
    print(f"\nИтог: {sum(1 for r in results if r[1] == 'ok')} прошло, {len(failed)} упало, "
          f"{sum(1 for r in results if r[1] == 'skip')} пропущено", flush=True)
    if failed:
        print("Упали: " + ", ".join(failed), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
