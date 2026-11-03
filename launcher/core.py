"""ECHOES Launcher core: find the installation, talk to GitHub, run the bundled installer / updater.
Never touches the user's music or the data folder (~/Echoes)."""
from pathlib import Path
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.request
import zipfile

import psutil

REPO = "rop1ms/Echoes"
BRANCH = "main"
VERSION_URL = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/app/version.txt"
ZIP_URL = f"https://codeload.github.com/{REPO}/zip/refs/heads/{BRANCH}"
DEFAULT_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Programs" / "ECHOES"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def data_dir() -> Path:
    new, old = Path.home() / "Echoes", Path.home() / ".neon_player"
    return new if new.exists() or not old.exists() else old


def accent() -> str:
    """Акцент темы пользователя (если ECHOES уже стоит) — лаунчер в тех же цветах."""
    try:
        s = json.loads((data_dir() / "settings.json").read_text("utf-8-sig"))
        a = str((s.get("ya_skin") or {}).get("accent") or "")
        if re.fullmatch(r"#[0-9a-fA-F]{6}", a):
            return a
    except (OSError, ValueError):
        pass
    return "#ffdb1a"


def installation(path):
    try:
        p = Path(path).resolve()
    except (OSError, TypeError, ValueError):
        return False
    return (p / "app/main.py").is_file() and (p / "python/pythonw.exe").is_file()


def discover():
    candidates = []
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall\ECHOES") as k:
            candidates.append(Path(winreg.QueryValueEx(k, "InstallLocation")[0]))
    except OSError:
        pass
    candidates.append(DEFAULT_DIR)
    return next((p for p in candidates if installation(p)), None)


def version(folder):
    try:
        return (Path(folder) / "app/version.txt").read_text("utf-8-sig").strip()[:80]
    except (OSError, TypeError):
        return ""


def parse_version(s) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", str(s or ""))[:6])


def remote_version(timeout=8) -> str:
    req = urllib.request.Request(VERSION_URL, headers={"User-Agent": "ECHOES-Launcher"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace").strip()


def running(root):
    root = Path(root).resolve()
    for p in psutil.process_iter(["exe", "cmdline"]):
        try:
            exe = p.info["exe"]
            if exe and Path(exe).resolve().is_relative_to(root / "python"):
                return True
            if any(Path(a).resolve() == root / "app/main.py" for a in (p.info["cmdline"] or []) if a.endswith("main.py")):
                return True
        except (OSError, ValueError, psutil.Error):
            continue
    return False


def launch(root):
    root = Path(root).resolve()
    if not installation(root):
        raise ValueError("ECHOES не найден в этой папке.")
    if running(root):
        raise ValueError("ECHOES уже запущен — его окно или значок в трее.")
    return subprocess.Popen([str(root / "python/pythonw.exe"), str(root / "app/main.py")], cwd=root / "app")


def download_source(progress=lambda done, total: None, cancel=lambda: False) -> Path:
    """Свежая версия с GitHub → распакованная папка (с app/ и setup/)."""
    base = Path(tempfile.gettempdir()) / "echoes_launcher"
    base.mkdir(parents=True, exist_ok=True)
    zpath = base / "echoes.zip"
    req = urllib.request.Request(ZIP_URL, headers={"User-Agent": "ECHOES-Launcher"})
    with urllib.request.urlopen(req, timeout=30) as r, open(zpath, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            if cancel():
                raise RuntimeError("отменено")
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            progress(done, total)
    out = base / f"src-{int(time.time())}"
    with zipfile.ZipFile(zpath) as z:
        for n in z.namelist():
            if Path(n).is_absolute() or ".." in Path(n).parts:
                raise RuntimeError("подозрительный путь в архиве")
        root = z.namelist()[0].split("/")[0]
        z.extractall(out)
    zpath.unlink(missing_ok=True)
    src = out / root
    if not (src / "app/main.py").is_file() or not (src / "setup/install.ps1").is_file():
        raise RuntimeError("в архиве нет программы")
    return src


STEP_RE = re.compile(r"\[(\d+)/(\d+)\]\s*(.*)")


def run_script(script: Path, args, on_line, cancel=lambda: False) -> int:
    """PowerShell-скрипт установки/обновления без окна консоли; каждая строка вывода → on_line(text)."""
    cmd = ["powershell.exe", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)] + list(args)
    p = subprocess.Popen(cmd, cwd=str(script.parent.parent), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    for raw in iter(p.stdout.readline, b""):
        if cancel():
            p.kill()
            break
        line = raw.decode("utf-8", "replace").rstrip()
        line = re.sub(r"\x1b\[[0-9;]*m", "", line)
        if line.strip():
            on_line(line)
    return p.wait()


def uninstall(root):
    script = Path(root) / "uninstall.ps1"
    if not script.is_file():
        raise ValueError("Удаление не найдено в папке ECHOES.")
    subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                     creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
