"""Native C++ editor host. No timeline, compositing or video processing in Python."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile

from PyQt6.QtCore import QProcess, QProcessEnvironment, QTimer
from PyQt6.QtGui import QWindow
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton

THEME_NAME = "ECHOES Montage"


def montage_theme_dict():
    # Matches the native editor's DaVinci-style graphite palette.
    return dict(bg="#1c1c1f", panel="#232326", panel2="#2a2a2e", text="#d7d7da",
                muted="#8b8b92", accent="#e8822c", accent2="#e5332a",
                glass="rgba(255,255,255,0.04)", glass2="rgba(255,255,255,0.08)",
                border="#0d0d0f", border2="#36363c", danger="#ff5c5c", montage=True)


def _tool(name):
    app = Path(__file__).resolve().parent
    for path in (app.parent / "ffmpeg" / f"{name}.exe", app / "ffmpeg" / f"{name}.exe",
                 app.parent / "ffmpeg" / "bin" / f"{name}.exe", app / "ffmpeg" / "bin" / f"{name}.exe"):
        if path.is_file():
            return str(path)
    installed = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "Programs/ECHOES/ffmpeg" / f"{name}.exe"
    if installed.is_file():
        return str(installed)
    return shutil.which(name) or ""


class MontageShell(QWidget):
    def __init__(self, player, parent=None):
        super().__init__(parent)
        self.player = player
        self.process = QProcess(self)
        try:
            import noflash
            noflash.hide_qprocess(self.process)
        except Exception:                                      # noqa: BLE001
            pass
        self.native_window = None
        self.container = None
        self._buffer = b""
        self._closing = False
        self._after_close = None
        self._library = tempfile.NamedTemporaryFile(suffix=".json", prefix="echoes-montage-", delete=False)
        self._library.write(json.dumps(player.library, ensure_ascii=False).encode("utf-8"))
        self._library.close()
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.message = QLabel("Запускается ECHOES Montage • C++ / Qt…")
        self.message.setWordWrap(True)
        self.layout_.addWidget(self.message)
        self.back = QPushButton("Вернуться в Echoes Music")
        self.back.clicked.connect(lambda: player._apply_theme("Echoes Music"))
        self.layout_.addWidget(self.back)
        exe = Path(__file__).parent / "montage_native" / "echoes_montage.exe"
        ffmpeg, ffprobe = _tool("ffmpeg"), _tool("ffprobe")
        if not exe.is_file() or not ffmpeg or not ffprobe:
            self.message.setText("Не найден C++ редактор или FFmpeg/FFprobe. "
                                 "Сборка: tools/montage/build.ps1. FFmpeg и FFprobe должны быть рядом с ECHOES или в PATH.")
            return
        vlc = ""
        for folder in (Path(__file__).parent.parent / "vlc", Path(__file__).parent / "vlc",
                       Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "Programs/ECHOES/vlc",
                       Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "VideoLAN" / "VLC"):
            if (folder / "libvlc.dll").is_file():
                vlc = str(folder)
                break
        env = QProcessEnvironment.systemEnvironment()
        # PyQt's plugin directory belongs to another Qt build; do not inherit it.
        for key in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QT_QPA_PLATFORM"):
            env.remove(key)
        env.insert("ECHOES_LANGUAGE", player.settings.get("language", "ru"))
        self.process.setProcessEnvironment(env)
        self.process.setWorkingDirectory(str(exe.parent))
        self.process.readyReadStandardOutput.connect(self._output)
        self.process.errorOccurred.connect(lambda _: self.message.setText("Ошибка запуска C++ редактора: " + self.process.errorString()))
        self.process.finished.connect(self._finished)
        self.process.start(str(exe), ["--embedded", "--ffmpeg", ffmpeg, "--ffprobe", ffprobe,
                                      "--vlc", vlc, "--library", self._library.name])
        QTimer.singleShot(15000, self._check_start)

    def _check_start(self):
        if not self._closing and self.container is None:
            self.message.setText("Редактор не открыл окно. " + bytes(self.process.readAllStandardError()).decode("utf-8", "replace")[-2000:])

    def _output(self):
        self._buffer += bytes(self.process.readAllStandardOutput())
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            if line.strip() == b"ECHOES_CLOSE_CANCELLED":
                self._after_close = None
            if line.startswith(b"ECHOES_HWND=") and self.container is None:
                self.native_window = QWindow.fromWinId(int(line.split(b"=", 1)[1].strip()))
                self.container = QWidget.createWindowContainer(self.native_window, self)
                self.layout_.insertWidget(0, self.container, 1)
                self.message.hide()
                self.back.hide()

    def _finished(self, *_):
        if not self._closing:
            self.message.setText("Монтажный редактор закрыт. Проекты можно открыть при следующем входе в тему.")
            self.message.show()
            self.back.show()
            if self.container:
                self.container.hide()
            callback = self._after_close or (lambda: self.player._apply_theme("Echoes Music"))
            self._after_close = None
            QTimer.singleShot(0, callback)

    def shutdown(self):
        self._closing = True
        # Emergency termination preserves the last autosave. The host asks the editor
        # to save/close normally before switching themes (request_close).
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
            self.process.waitForFinished(3000)
        Path(self._library.name).unlink(missing_ok=True)

    def request_close(self, callback=None):
        if self.process.state() == QProcess.ProcessState.NotRunning:
            return True
        if self.native_window is None:
            self.process.kill()
            self.process.waitForFinished(2000)
            return True
        # WM_CLOSE lets the C++ editor handle unsaved projects and active renders.
        import ctypes
        from ctypes import wintypes
        self._after_close = callback
        if self.native_window:
            post = ctypes.windll.user32.PostMessageW
            post.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            post.restype = wintypes.BOOL
            post(int(self.native_window.winId()), 0x0010, 0, 0)
        return False
