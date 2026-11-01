"""Bounded Windows VLC discovery; never crawl a user's drives."""
from __future__ import annotations
import os


def find_libvlc(app_dir=None, environ=None):
    env = os.environ if environ is None else environ
    here = os.path.dirname(os.path.abspath(__file__)) if app_dir is None else os.fspath(app_dir)
    explicit = env.get("PYTHON_VLC_LIB_PATH", "")
    if explicit and os.path.isfile(explicit):
        return os.path.dirname(os.path.abspath(explicit))
    candidates = [env.get("ECHOES_VLC_DIR", ""), env.get("PYTHON_VLC_MODULE_PATH", ""),
                  os.path.join(os.path.dirname(here), "vlc"), os.path.join(here, "vlc")]
    # Registry entries are a small fixed set, including per-user installations.
    try:
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for sub in (r"SOFTWARE\VideoLAN\VLC", r"SOFTWARE\WOW6432Node\VideoLAN\VLC"):
                try:
                    with winreg.OpenKey(hive, sub) as key:
                        directory, _ = winreg.QueryValueEx(key, "InstallDir")
                        if isinstance(directory, str):
                            candidates.append(directory)
                except OSError:
                    pass
    except ImportError:
        pass
    candidates.extend(os.path.join(base, "VideoLAN", "VLC") for base in
                      (env.get("ProgramFiles", r"C:\Program Files"),
                       env.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                       os.path.join(env.get("LOCALAPPDATA", os.path.expanduser(r"~\AppData\Local")), "Programs")))
    candidates.append(os.path.expanduser(r"~\scoop\apps\vlc\current"))
    candidates.extend(part.strip().strip('"') for part in env.get("PATH", "").split(os.pathsep))
    seen = set()
    for directory in candidates:
        if not directory:
            continue
        directory = os.path.abspath(os.path.expandvars(directory))
        key = os.path.normcase(directory)
        if key in seen:
            continue
        seen.add(key)
        if os.path.isfile(os.path.join(directory, "libvlc.dll")):
            return directory
    return None
