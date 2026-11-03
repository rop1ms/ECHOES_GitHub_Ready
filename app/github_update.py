# github_update.py
"""
Обновление ECHOES с GitHub: кнопка «Обновить» в шапке окна, когда на GitHub вышла новая версия.

  * раз в несколько часов (и вскоре после запуска) сравниваем свой app/version.txt с тем, что лежит в ветке
    main репозитория ECHOES на GitHub (raw-файл, без API и без лимитов);
  * новее — в шапке загорается та же кнопка «Обновить», что и у обновлений через Echoes.NET;
  * по нажатию: что нового (последний коммит), затем ECHOES скачивает архив ветки с GitHub (прогресс — на
    кнопке), проверяет, что внутри именно эта версия, и запускает из него обычный установщик
    setup/update.ps1 в автоматическом режиме: он закрывает плеер, заменяет файлы программы (настройки,
    плейлисты и библиотека не трогаются), обновляет yt-dlp и снова открывает ECHOES.

Сеть — только два адреса GitHub; всё в фоне, интерфейс не ждёт.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
import time
import zipfile
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

REPO = "rop1ms/ECHOES_GitHub_Ready"
BRANCH = "main"
VERSION_URL = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/app/version.txt"
COMMIT_URL = f"https://api.github.com/repos/{REPO}/commits/{BRANCH}"
ZIP_URL = f"https://codeload.github.com/{REPO}/zip/refs/heads/{BRANCH}"
APP_DIR = Path(__file__).resolve().parent
CHECK_EVERY_S = 3 * 3600
FIRST_CHECK_MS = 25_000


def parse_version(s: str) -> tuple:
    """«2026.11.02-27» → (2026, 11, 2, 27); непонятное — пустой кортеж (меньше любой версии)."""
    nums = re.findall(r"\d+", str(s or ""))
    return tuple(int(x) for x in nums[:6])


def local_version(app_dir: Path = APP_DIR) -> str:
    try:
        return (app_dir / "version.txt").read_text("utf-8").strip()
    except OSError:
        return ""


def _get(url: str, timeout: float = 10.0) -> bytes:
    import requests
    r = requests.get(url, timeout=timeout, headers={"User-Agent": "ECHOES-updater",
                                                    "Accept": "application/vnd.github+json"})
    r.raise_for_status()
    return r.content


def remote_version() -> str:
    return _get(VERSION_URL).decode("utf-8", "replace").strip()


def changes_note() -> str:
    """Сообщение последнего коммита (что нового) — или пусто."""
    try:
        d = json.loads(_get(COMMIT_URL).decode("utf-8", "replace"))
        msg = str(((d.get("commit") or {}).get("message")) or "")
        lines = [l for l in msg.splitlines() if l.strip() and not l.startswith("Co-Authored-By")]
        return "\n".join(lines[:14])
    except Exception:                                    # noqa: BLE001
        return ""


def download(ver: str, progress=None, cancel=None) -> Path:
    """Архив ветки → распакованная папка с app/ и setup/; проверяет, что внутри версия ver."""
    import requests
    base = Path(tempfile.gettempdir()) / "echoes_github_update"
    base.mkdir(parents=True, exist_ok=True)
    zpath = base / f"echoes-{re.sub(r'[^0-9.-]', '_', ver)}.zip"
    with requests.get(ZIP_URL, stream=True, timeout=20, headers={"User-Agent": "ECHOES-updater"}) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        with open(zpath, "wb") as f:
            for chunk in r.iter_content(256 * 1024):
                if cancel is not None and cancel.is_set():
                    raise RuntimeError("отменено")
                f.write(chunk)
                got += len(chunk)
                if progress:
                    progress(got, total)
    out = base / f"src-{int(time.time())}"
    with zipfile.ZipFile(zpath) as z:
        names = z.namelist()
        root = names[0].split("/")[0] if names else ""
        for n in names:                                  # только внутрь out — без «../»
            p = Path(n)
            if p.is_absolute() or ".." in p.parts:
                raise RuntimeError("подозрительный путь в архиве")
        z.extractall(out)
    src = out / root
    got_ver = (src / "app" / "version.txt").read_text("utf-8").strip() if (src / "app" / "version.txt").exists() else ""
    if not (src / "app" / "main.py").exists() or not (src / "setup" / "update.ps1").exists():
        raise RuntimeError("в архиве нет программы")
    if parse_version(got_ver) < parse_version(ver):
        raise RuntimeError(f"в архиве версия {got_ver or '?'}, ждали {ver}")
    try:
        zpath.unlink()
    except OSError:
        pass
    return src


def launch_installer(src: Path) -> None:
    """Обычный установщик из скачанной версии, в своём окне: закроет ECHOES, обновит файлы и откроет снова."""
    script = src / "setup" / "update.ps1"
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    subprocess.Popen(["powershell.exe", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                      "-Auto"], cwd=str(src), creationflags=flags)


class GitHubUpdater(QObject):
    """Проверка в фоне и кнопка «Обновить» в шапке окна."""

    found = pyqtSignal(str, str)          # версия, что нового
    latest = pyqtSignal(str)              # ручная проверка: новее нет (версия на GitHub)
    message = pyqtSignal(str)             # ручная проверка: что сказать человеку
    progress = pyqtSignal(int)            # 0..100
    failed = pyqtSignal(str)
    ready = pyqtSignal(str)               # папка скачанной версии

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.remote = ""
        self.note = ""
        self.busy = False
        self._cancel = threading.Event()
        self.found.connect(self._on_found)
        self.progress.connect(self._on_progress)
        self.failed.connect(self._on_failed)
        self.ready.connect(self._on_ready)
        self.latest.connect(self._on_latest)
        self.message.connect(lambda m: getattr(self.win, "_toast", print)(m))
        self.timer = QTimer(self)
        self.timer.setInterval(CHECK_EVERY_S * 1000)
        self.timer.timeout.connect(self.check)
        self.timer.start()
        QTimer.singleShot(FIRST_CHECK_MS, self.check)

    # ── проверка ──
    def check(self, manual=False):
        if self.busy or (os.environ.get("ECHOES_NO_GH_UPDATE") and not manual):
            return

        def work():
            try:
                rv = remote_version()
                if parse_version(rv) > parse_version(local_version()):
                    if manual:
                        self._manual = True
                    self.found.emit(rv, changes_note())
                elif manual:
                    self.latest.emit(rv)
            except Exception as e:                       # noqa: BLE001
                print("[github update] check:", e)
                if manual:
                    self.message.emit(f"Не удалось проверить обновления: GitHub не ответил ({e.__class__.__name__})")
        threading.Thread(target=work, daemon=True, name="gh-update-check").start()

    def pending(self) -> bool:
        return bool(self.remote) and parse_version(self.remote) > parse_version(local_version())

    def _button(self, text="Обновить", on=True):
        tb = getattr(self.win, "titlebar", None)
        if tb is not None and hasattr(tb, "set_update"):
            tb.set_update(on, self.ask, text)

    def _on_found(self, ver, note):
        manual = self.__dict__.pop("_manual", False)
        skipped = (self.win.settings.get("gh_update_skip") or "") == ver and not manual
        self.remote, self.note = ver, note
        if not skipped:
            self._button()
        if manual:
            self.ask()

    def _on_latest(self, ver):
        try:
            self.win._toast(f"У вас последняя версия ECHOES ({local_version() or ver})")
        except Exception:                                # noqa: BLE001
            pass

    # ── установка ──
    def ask(self):
        if self.busy:
            return
        from PyQt6.QtWidgets import QMessageBox
        text = (f"Вышла новая версия ECHOES: {self.remote}\nУ вас: {local_version() or 'неизвестно'}\n\n"
                + (f"Что нового:\n{self.note}\n\n" if self.note else "")
                + "ECHOES скачает её с GitHub, закроется на минуту, обновит файлы и откроется снова. "
                  "Настройки, плейлисты и библиотека не трогаются.")
        box = QMessageBox(self.win)
        box.setWindowTitle("Обновление ECHOES")
        box.setText(text)
        upd = box.addButton("Обновить сейчас", QMessageBox.ButtonRole.AcceptRole)
        later = box.addButton("Позже", QMessageBox.ButtonRole.RejectRole)
        skip = box.addButton("Пропустить эту версию", QMessageBox.ButtonRole.DestructiveRole)
        box.exec()
        if box.clickedButton() is skip:
            self.win.settings["gh_update_skip"] = self.remote
            try:
                self.win.save_settings()
            except Exception:                            # noqa: BLE001
                pass
            self._button(on=False)
            return
        if box.clickedButton() is not upd:
            return
        self.start()

    def start(self):
        self.busy = True
        self._cancel.clear()
        self._button("0%")
        ver = self.remote

        def work():
            try:
                src = download(ver, lambda got, total: self.progress.emit(int(100 * got / total) if total else -1),
                               self._cancel)
                self.ready.emit(str(src))
            except Exception as e:                       # noqa: BLE001
                self.failed.emit(str(e) or e.__class__.__name__)
        threading.Thread(target=work, daemon=True, name="gh-update-download").start()

    def _on_progress(self, pct):
        self._button(f"{pct}%" if pct >= 0 else "Скачиваю…")

    def _on_failed(self, why):
        self.busy = False
        self._button("Обновить")
        try:
            self.win._toast(f"Не удалось скачать обновление: {why}")
        except Exception:                                # noqa: BLE001
            print("[github update]", why)

    def _on_ready(self, src):
        self._button("Устанавливаю…")
        try:
            launch_installer(Path(src))
        except Exception as e:                           # noqa: BLE001
            self._on_failed(f"не запустился установщик: {e}")
            return
        # установщик сам закроет ECHOES; закрываемся аккуратно сами, чтобы сохранить всё
        try:
            from PyQt6.QtWidgets import QApplication
            self.win._really_quit = True
            self.win.close()
            QApplication.instance().quit()
        except Exception:                                # noqa: BLE001
            pass


def install(win):
    if getattr(win, "_gh_updater", None) is None:
        win._gh_updater = GitHubUpdater(win)
    return win._gh_updater
