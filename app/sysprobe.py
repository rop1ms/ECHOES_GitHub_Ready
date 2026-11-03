"""Какой это компьютер — и как ECHOES должен на нём рисовать, чтобы было плавно.

probe() при запуске (до окна, ~0,1 с): видеокарта — настоящий OpenGL или программный (llvmpipe, «Базовый
видеоадаптер Microsoft», GDI Generic: тогда любой GL-слой переводит ВСЁ окно на рисование процессором и
интерфейс падает до 8–10 к/с), ядра, память, размер экрана. Из этого — уровень: low / mid / high.

apply_startup(settings) — до создания окна:
  • программный OpenGL → GPU-слои выключены целиком (туманность, переходы, прокрутка на GPU, огонь дуэли);
  • первый запуск или сменилось железо → рекомендованный режим графики («лёгкий» на слабых ПК), если человек
    сам не выбирал свои настройки производительности.

FpsGuard — сторож плавности: если окно на экране, а поток интерфейса подряд ~16 с выдаёт меньше ~15 к/с
(не во время загрузки и не в режиме геймера), включает «лёгкий режим» и говорит об этом. Один раз:
выключили вручную — больше не трогает.
"""
from __future__ import annotations

import os
import platform
import statistics
import sys
import time

SOFT_GL = ("llvmpipe", "softpipe", "swiftshader", "gdi generic", "basic render", "microsoft basic",
           "software", "mesa offscreen")
INFO: dict = {}


def _ram_gb() -> float:
    try:
        if sys.platform == "win32":
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("len", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                            ("avail", ctypes.c_ulonglong), ("tpf", ctypes.c_ulonglong), ("apf", ctypes.c_ulonglong),
                            ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong), ("ave", ctypes.c_ulonglong)]
            m = MS()
            m.len = ctypes.sizeof(MS)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
                return m.total / 2 ** 30
        elif hasattr(os, "sysconf"):
            return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2 ** 30
    except Exception:                                      # noqa: BLE001
        pass
    return 8.0


def probe_gpu() -> dict:
    """{ok, renderer, vendor, version}: ok — настоящая видеокарта с OpenGL ≥ 2."""
    out = {"ok": False, "renderer": "", "vendor": "", "version": ""}
    if os.environ.get("QT_QPA_PLATFORM", "") == "offscreen":
        return out
    try:
        from PyQt6.QtGui import QOffscreenSurface, QOpenGLContext
        from PyQt6.QtOpenGL import QOpenGLVersionFunctionsFactory, QOpenGLVersionProfile
        surf = QOffscreenSurface()
        surf.create()
        ctx = QOpenGLContext()
        if not ctx.create() or not ctx.makeCurrent(surf):
            return out
        vp = QOpenGLVersionProfile()
        vp.setVersion(2, 0)
        f = QOpenGLVersionFunctionsFactory.get(vp, ctx)
        if f is not None:
            out["vendor"] = str(f.glGetString(0x1F00) or "")
            out["renderer"] = str(f.glGetString(0x1F01) or "")
            out["version"] = str(f.glGetString(0x1F02) or "")
        major = ctx.format().majorVersion()
        ctx.doneCurrent()
        name = (out["renderer"] + " " + out["vendor"]).lower()
        out["ok"] = major >= 2 and bool(out["renderer"]) and not any(k in name for k in SOFT_GL)
    except Exception as e:                                 # noqa: BLE001
        out["error"] = str(e)[:200]
    return out


def _integrated(renderer: str) -> bool:
    r = renderer.lower()
    if "intel" in r and not any(k in r for k in ("arc a", "arc(tm) a")):
        return True
    return any(k in r for k in ("radeon(tm) graphics", "radeon graphics", "vega 3", "vega 6", "vega 8",
                                "mali", "adreno", "apple m", "uhd", "hd graphics", "iris"))


def probe(cached: dict | None = None) -> dict:
    """cached — прошлый результат проверки видеокарты: OpenGL-контекст при запуске стоит до 0,6 с, поэтому
    настоящую видеокарту перепроверяем раз в 3 дня (программный OpenGL — при каждом запуске: вдруг поставили драйвер)."""
    global INFO
    from PyQt6.QtGui import QGuiApplication
    import time as _t
    c = (cached or {}).get("gpu") if isinstance(cached, dict) else None
    if c and c.get("ok") and _t.time() - float((cached or {}).get("ts") or 0) < 3 * 86400:
        gpu = dict(c)
    else:
        gpu = probe_gpu()
    px = 0
    try:
        scr = QGuiApplication.primaryScreen()
        if scr is not None:
            g = scr.geometry()
            px = int(g.width() * g.height() * scr.devicePixelRatio() ** 2)
    except Exception:                                      # noqa: BLE001
        pass
    ts = (cached or {}).get("ts") if gpu is not None and c and gpu == c else _t.time()
    INFO = {"gpu": gpu, "cpu": os.cpu_count() or 2, "ram_gb": round(_ram_gb(), 1), "screen_px": px,
            "os": f"{platform.system()} {platform.release()}", "integrated": _integrated(gpu.get("renderer", "")),
            "ts": ts}
    INFO["tier"] = tier(INFO)
    return INFO


def tier(info: dict) -> str:
    gpu, cpu, ram, px = info["gpu"], info["cpu"], info["ram_gb"], info["screen_px"]
    if not gpu.get("ok") or cpu <= 2 or ram < 4.5:
        return "low"
    big = px > 2560 * 1440
    if (info.get("integrated") and (big or cpu <= 4 or ram < 7.5)) or cpu <= 4 or ram < 7.5:
        return "mid"
    return "high"


def gpu_ok() -> bool:
    return bool((INFO.get("gpu") or {}).get("ok", True)) if INFO else True


def summary(info: dict | None = None) -> str:
    i = info or INFO
    if not i:
        return ""
    g = i.get("gpu") or {}
    name = g.get("renderer") or "нет данных"
    if not g.get("ok"):
        name += " — без ускорения (программный OpenGL)"
    return f"Видеокарта: {name} · {i.get('cpu')} потоков · {i.get('ram_gb'):g} ГБ ОЗУ"


LITE = {                     # лёгкий режим: всё, что заметно ест кадры на слабом железе
    "motion_profile": "eco", "ui_animations": False, "fps_limit": 60,
}
LITE_PERF = {"animated_images": False, "aura_quality": "low", "warm_pages": False, "background_fps": 30,
             "duel_particles": False, "gif_cache": False}
MID = {"motion_profile": "balanced"}


def set_lite(settings: dict, on: bool):
    """Включить/выключить лёгкий режим (прежние значения запоминаются и возвращаются)."""
    if on and not settings.get("lite_mode"):
        settings["lite_prev"] = {k: settings.get(k) for k in LITE}
        settings["lite_prev_perf"] = {k: (settings.get("performance") or {}).get(k) for k in LITE_PERF}
        settings.update(LITE)
        perf = dict(settings.get("performance") or {})
        perf.update(LITE_PERF)
        settings["performance"] = perf
    elif not on and settings.get("lite_mode"):
        for k, v in (settings.pop("lite_prev", None) or {}).items():
            if v is None:
                settings.pop(k, None)
            else:
                settings[k] = v
        perf = dict(settings.get("performance") or {})
        for k, v in (settings.pop("lite_prev_perf", None) or {}).items():
            if v is None:
                perf.pop(k, None)
            else:
                perf[k] = v
        settings["performance"] = perf
    settings["lite_mode"] = bool(on)


def apply_startup(settings: dict) -> dict:
    """До создания окна: проверить железо, выключить GPU-слои без видеокарты, предложить режим графики."""
    info = probe(settings.get("sys_probe"))
    if not info["gpu"].get("ok"):
        for k in ("ECHOES_NO_GPU_NEBULA", "ECHOES_NO_GPU_SCROLL", "ECHOES_NO_GPU_DUEL"):
            os.environ.setdefault(k, "1")
        os.environ.setdefault("ECHOES_SOFT_GL", "1")
    sig = f"{info['gpu'].get('renderer')}|{info['cpu']}|{round(info['ram_gb'])}"
    changed = settings.get("sys_probe_sig") != sig
    settings["sys_probe_sig"] = sig
    settings["sys_probe"] = {k: v for k, v in info.items()}
    if changed and not settings.get("perf_user_set"):
        if info["tier"] == "low":
            set_lite(settings, True)
            settings["lite_reason"] = "auto"
        elif info["tier"] == "mid" and not settings.get("lite_mode"):
            settings.update(MID)
    print("[sys]", summary(info), "· уровень:", info["tier"],
          "· лёгкий режим" if settings.get("lite_mode") else "")
    return info


# ── сторож плавности ──

class FpsGuard:
    WINDOW_S = 8.0
    BAD_WINDOWS = 2
    BAD_FRAME_MS = 66.0                                    # медиана интервала > 66 мс ≈ меньше 15 к/с

    def __init__(self, win):
        from PyQt6.QtCore import QTimer
        self.win = win
        self.samples: list = []
        self.bad = 0
        self.t0 = time.perf_counter()
        self.last = time.perf_counter()
        self.timer = QTimer(win)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    def _eligible(self) -> bool:
        w = self.win
        try:
            from PyQt6.QtWidgets import QApplication
            from PyQt6.QtCore import Qt
            if not w.isVisible() or w.isMinimized():
                return False
            if QApplication.applicationState() != Qt.ApplicationState.ApplicationActive:
                return False
            import game_mode
            if game_mode.active(w):
                return False
        except Exception:                                  # noqa: BLE001
            return False
        return time.perf_counter() - self.t0 > 25.0       # первые секунды — загрузка, не считаем

    def _tick(self):
        now = time.perf_counter()
        dt = (now - self.last) * 1000.0
        self.last = now
        if not self._eligible():
            self.samples.clear()
            return
        self.samples.append(dt)
        if sum(self.samples) < self.WINDOW_S * 1000:
            return
        med = statistics.median(self.samples)
        self.samples.clear()
        self.bad = self.bad + 1 if med > self.BAD_FRAME_MS else 0
        if self.bad >= self.BAD_WINDOWS:
            self.timer.stop()
            self._lite()

    def _lite(self):
        w = self.win
        s = w.settings
        if s.get("lite_mode") or s.get("lite_guard_off"):
            return
        set_lite(s, True)
        s["lite_reason"] = "guard"
        try:
            import performance
            performance.apply(w)
            import frameclock
            frameclock.configure(s, w)
        except Exception as e:                             # noqa: BLE001
            print("[sys] lite:", e)
        try:
            w._toast("Интерфейс тормозил — включил лёгкий режим. Вернуть: Настройки → Производительность")
        except Exception:                                  # noqa: BLE001
            pass
        print("[sys] FPS guard: лёгкий режим включён")


def install_guard(win):
    if os.environ.get("QT_QPA_PLATFORM", "") == "offscreen":
        return None
    g = win.__dict__.get("_fps_guard")
    if g is None:
        g = win._fps_guard = FpsGuard(win)
    return g
