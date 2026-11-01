# loudness.py
"""
Выравнивание громкости треков (как ReplayGain / «Нормализация громкости» в
стримингах). Каждый трек один раз измеряется через ffmpeg (EBU R128: интегральная
громкость в LUFS и пик), результат кэшируется в ~/.neon_player/loudness.json.
При воспроизведении движок умножает громкость на коэффициент трека, так что
громкие и тихие записи звучат на одном уровне. Сам файл не изменяется.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

TARGET_LUFS = -14.0        # куда подтягиваем (стандарт стримингов)
MAX_BOOST_DB = 12.0        # сильнее не усиливаем — вылезает шум
MAX_CUT_DB = 20.0
PEAK_CEIL_DB = -1.0        # после усиления пик не выше −1 dBFS (без клиппинга)

_I = re.compile(r"^\s*I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", re.M)
_PEAK = re.compile(r"Peak:\s*(-?\d+(?:\.\d+)?|-inf)\s*dBFS", re.M)


def _ffmpeg() -> str | None:
    p = shutil.which("ffmpeg")
    if p:
        return p
    local = Path(__file__).resolve().parent.parent / "ffmpeg" / "ffmpeg.exe"
    return str(local) if local.exists() else None


def measure(path: str):
    """(LUFS, пик dBFS) трека или None, если не вышло."""
    exe = _ffmpeg()
    if not exe or not Path(path).exists():
        return None
    flags = 0x08000000 if os.name == "nt" else 0
    try:
        r = subprocess.run([exe, "-nostdin", "-hide_banner", "-nostats", "-i", path, "-vn",
                            "-af", "ebur128=peak=true", "-f", "null", "-"],
                           capture_output=True, timeout=180, creationflags=flags)
    except Exception as e:                           # noqa: BLE001
        print("[loudness]", e)
        return None
    out = r.stderr.decode("utf-8", "replace")
    # итог — последний блок «Summary» (в нём I: и Peak:)
    tail = out[out.rfind("Summary:"):] if "Summary:" in out else out
    mi = _I.findall(tail)
    if not mi:
        return None
    mp = _PEAK.findall(tail)
    peak = -99.0 if not mp or mp[-1] == "-inf" else float(mp[-1])
    return float(mi[-1]), peak


def gain_db(lufs: float, peak: float, target: float = TARGET_LUFS) -> float:
    g = target - lufs
    if g > 0:                                         # усиление ограничено запасом до клиппинга
        g = min(g, PEAK_CEIL_DB - peak, MAX_BOOST_DB)
        g = max(g, 0.0)
    else:
        g = max(g, -MAX_CUT_DB)
    return g


class LoudnessStore:
    def __init__(self, path: Path, on_ready=None):
        self.path = Path(path)
        self.on_ready = on_ready                       # cb(track_path) — вызывается из рабочего потока
        self.data: dict = {}
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:                            # noqa: BLE001
            self.data = {}
        self._lock = threading.Lock()
        self._todo: list[str] = []
        self._worker = None
        self._dirty = 0

    def _key(self, path):
        try:
            st = os.stat(path)
            return str(path), int(st.st_mtime), st.st_size
        except OSError:
            return str(path), 0, 0

    def get(self, path):
        """(LUFS, пик) из кэша или None (кэш устарел, если файл менялся)."""
        e = self.data.get(str(path))
        if not e:
            return None
        _, mt, sz = self._key(path)
        if e.get("mt") != mt or e.get("sz") != sz:
            return None
        return e["lufs"], e["peak"]

    def linear_gain(self, path, target: float = TARGET_LUFS) -> float:
        m = self.get(path)
        if m is None:
            return 1.0
        return 10 ** (gain_db(m[0], m[1], target) / 20.0)

    def request(self, path, urgent=False):
        """Поставить трек в очередь на измерение (срочный — в начало)."""
        path = str(path)
        if not path or self.get(path) is not None:
            return
        with self._lock:
            if path in self._todo:
                if urgent:
                    self._todo.remove(path)
                    self._todo.insert(0, path)
            else:
                self._todo.insert(0, path) if urgent else self._todo.append(path)
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._run, daemon=True)
                self._worker.start()

    def request_many(self, paths):
        for p in paths:
            self.request(p)

    def _run(self):
        while True:
            with self._lock:
                if not self._todo:
                    break
                path = self._todo.pop(0)
            if self.get(path) is not None:
                continue
            try:
                import bgproc
                bgproc.idle_wait()                   # идёт игра esu! — замер громкости подождёт
            except Exception:                        # noqa: BLE001
                pass
            m = measure(path)
            _, mt, sz = self._key(path)
            self.data[path] = {"lufs": m[0], "peak": m[1], "mt": mt, "sz": sz} if m else \
                {"lufs": TARGET_LUFS, "peak": -99.0, "mt": mt, "sz": sz}      # не измерился — не трогаем
            self._dirty += 1
            if self._dirty >= 10:
                self.save()
            if self.on_ready:
                try:
                    self.on_ready(path)
                except Exception as e:               # noqa: BLE001
                    print("[loudness] cb:", e)
            time.sleep(0.05)
        self.save()

    def save(self):
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data), encoding="utf-8")
            tmp.replace(self.path)
            self._dirty = 0
        except Exception as e:                       # noqa: BLE001
            print("[loudness] save:", e)

    def pending(self) -> int:
        with self._lock:
            return len(self._todo)
