"""Короткая техническая сводка о ПК и ECHOES — уходит вместе с баг-репортом, чтобы было видно, что у человека
(видеокарта и уровень ПК, путь звука, лёгкий режим, FPS, последние ошибки). Без личных данных: ни путей
с именем пользователя, ни треков."""
from __future__ import annotations

import os
import platform
import re
import sys
from pathlib import Path


def _scrub(text: str) -> str:
    home = str(Path.home())
    text = text.replace(home, "~").replace(home.replace("\\", "/"), "~")
    return re.sub(r"[A-Za-z]:\\\\Users\\\\[^\\\\]+", "~", text)


def report(win=None, max_len=3500) -> str:
    lines = []
    try:
        ver = (Path(__file__).with_name("version.txt")).read_text("utf-8").strip()
    except OSError:
        ver = "?"
    lines.append(f"ECHOES {ver} · {platform.system()} {platform.release()} ({platform.version()}) · Python "
                 f"{sys.version.split()[0]}")
    s = getattr(win, "settings", None) or {}
    try:
        import sysprobe
        info = sysprobe.INFO or s.get("sys_probe") or {}
        if info:
            g = info.get("gpu") or {}
            lines.append(f"GPU: {g.get('renderer') or '?'} | {g.get('vendor') or ''} | GL {g.get('version') or '?'} | "
                         f"ускорение: {'да' if g.get('ok') else 'НЕТ'}")
            lines.append(f"CPU потоков: {info.get('cpu')} · ОЗУ: {info.get('ram_gb')} ГБ · экран: "
                         f"{info.get('screen_px', 0) / 1e6:.1f} Мп · уровень: {info.get('tier')}")
    except Exception as e:                                 # noqa: BLE001
        lines.append(f"sysprobe: {e}")
    lines.append(f"Лёгкий режим: {bool(s.get('lite_mode'))} ({s.get('lite_reason', '')}) · движение: "
                 f"{s.get('motion_profile', 'vivid')} · переходы: {s.get('ui_animations', True)} · FPS-лимит: "
                 f"{s.get('fps_limit', 0)}")
    try:
        from PyQt6.QtCore import QT_VERSION_STR, PYQT_VERSION_STR
        lines.append(f"Qt {QT_VERSION_STR} · PyQt {PYQT_VERSION_STR} · GPU-слои: "
                     f"{'выкл' if os.environ.get('ECHOES_SOFT_GL') else 'вкл'} · "
                     f"якорь GL: {getattr(win, '_gpu_anchor', None) is not None}")
    except Exception:                                      # noqa: BLE001
        pass
    try:                                                   # C++-модули: без них всё медленнее (молча)
        import native_ui
        ui = "да" if native_ui.available() else f"НЕТ ({native_ui._err})"
        try:
            import native_fx
            fx = "да" if native_fx.available() else "НЕТ"
        except Exception as e:                             # noqa: BLE001
            fx = f"НЕТ ({e})"
        from PyQt6.QtCore import qVersion
        lines.append(f"C++ интерфейс: {ui} · C++ эффекты: {fx} · Qt runtime {qVersion()}")
    except Exception as e:                                 # noqa: BLE001
        lines.append(f"native: {e}")
    try:
        eng = getattr(win, "engine", None)
        m = getattr(eng, "mixer", None)
        path = "VLC (без своего вывода)" if m is None else ("echoes_audio.dll" if getattr(m, "native", False)
                                                           else "Python (sounddevice)")
        lat = getattr(m, "latency_s", None)
        drive = hasattr(getattr(m, "lib", None), "na_drive") if m is not None and getattr(m, "native", False) else None
        lines.append(f"Звук: {path}" + (f" · задержка {lat * 1000:.0f} мс" if lat else "") +
                     f" · буст ×{getattr(eng, '_boost', 1):g} · басс {s.get('bass_boost', 0)}%" +
                     ("" if drive is None else f" · перегруз/бас в DLL: {'да' if drive else 'НЕТ (старая DLL)'}"))
        try:
            import vlc
            lines.append("VLC " + (vlc.libvlc_get_version() or b"").decode("utf-8", "replace"))
        except Exception:                                  # noqa: BLE001
            pass
        if m is not None and hasattr(m, "stats"):
            st = m.stats()
            lines.append(f"Вывод: колбэков {st.get('callbacks')}, провалов {st.get('underruns')}")
    except Exception as e:                                 # noqa: BLE001
        lines.append(f"звук: {e}")
    try:
        g = getattr(win, "_fps_guard", None)
        if g is not None and g.samples:
            import statistics
            lines.append(f"Интерфейс сейчас: ~{1000 / max(1.0, statistics.median(g.samples)):.0f} к/с")
    except Exception:                                      # noqa: BLE001
        pass
    try:                                                   # последние ошибки из журнала
        import data_home
        log = data_home.root() / "crash.log"
        tail = log.read_text("utf-8", "replace")[-20000:]
        errs = re.findall(r"\n(\w+(?:Error|Exception)[^\n]{0,160})", tail)
        if errs:
            lines.append("Последние ошибки: " + " | ".join(dict.fromkeys(errs[-6:])))
    except Exception:                                      # noqa: BLE001
        pass
    return _scrub("\n".join(lines))[:max_len]
