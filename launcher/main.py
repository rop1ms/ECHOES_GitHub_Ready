"""ECHOES Launcher — установка с GitHub, запуск и обновление. Сборка: tools/build_launcher.ps1 (один .exe)."""
import math
import os
import sys
import time
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSettings, QTimer, QUrl, QRectF, QPointF, QLockFile, QStandardPaths
from PyQt6.QtGui import (QIcon, QDesktopServices, QPainter, QColor, QLinearGradient, QRadialGradient, QFont,
                         QPainterPath, QPen)
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFileDialog,
                             QProgressBar, QPlainTextEdit, QFrame, QMessageBox, QStackedWidget, QCheckBox, QLineEdit,
                             QGraphicsOpacityEffect)
import core

ROOT = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
ACC = QColor(core.accent())


def _hex(c: QColor) -> str:
    return c.name()


# ── живая туманность слева ── #

class Nebula(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumWidth(330)
        self.t0 = time.perf_counter()
        self.ver = ""
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.timer.start(33)

    def paintEvent(self, _):
        t = time.perf_counter() - self.t0
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, w, h), 22, 22)
        p.setClipPath(path)
        p.fillRect(self.rect(), QColor("#07070a"))
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        blobs = ((ACC, 0.32, 0.30, 0.55, 0.0), (QColor("#ff4f9a"), 0.70, 0.55, 0.50, 2.1),
                 (QColor("#7a5cff"), 0.40, 0.78, 0.60, 4.0), (ACC.lighter(130), 0.62, 0.20, 0.35, 5.5))
        for col, cx, cy, r, ph in blobs:
            x = w * (cx + 0.08 * math.sin(t * 0.35 + ph))
            y = h * (cy + 0.06 * math.cos(t * 0.29 + ph * 1.3))
            rad = max(w, h) * r * (1 + 0.08 * math.sin(t * 0.5 + ph))
            g = QRadialGradient(QPointF(x, y), rad)
            c0 = QColor(col); c0.setAlpha(150)
            c1 = QColor(col); c1.setAlpha(40)
            c2 = QColor(col); c2.setAlpha(0)
            g.setColorAt(0, c0); g.setColorAt(0.45, c1); g.setColorAt(1, c2)
            p.fillRect(self.rect(), g)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        shade = QLinearGradient(0, h * 0.45, 0, h)
        shade.setColorAt(0, QColor(0, 0, 0, 0)); shade.setColorAt(1, QColor(0, 0, 0, 200))
        p.fillRect(self.rect(), shade)
        # звезда-логотип
        cx, cy, R = 54, 64, 20
        star = QPainterPath()
        for i in range(16):
            a = math.pi * i / 8 + t * 0.15
            rr = R if i % 2 == 0 else R * 0.38
            pt = QPointF(cx + rr * math.cos(a), cy + rr * math.sin(a))
            star.moveTo(pt) if i == 0 else star.lineTo(pt)
        star.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 235))
        p.drawPath(star)
        f = QFont("Segoe UI", 22, QFont.Weight.Black)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 6)
        p.setFont(f)
        p.setPen(QColor("#ffffff"))
        p.drawText(QRectF(88, 44, w - 100, 40), Qt.AlignmentFlag.AlignVCenter, "ECHOES")
        p.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
        p.drawText(QRectF(28, h - 132, w - 56, 60), Qt.TextFlag.TextWordWrap, "Ваша музыка.\nВаше пространство.")
        p.setFont(QFont("Segoe UI", 10))
        p.setPen(QColor(255, 255, 255, 150))
        p.drawText(QRectF(28, h - 56, w - 56, 24), Qt.AlignmentFlag.AlignVCenter,
                   f"Лаунчер  ·  {self.ver}" if self.ver else "Лаунчер")
        p.end()


# ── фоновые задачи ── #

class RemoteCheck(QThread):
    done = pyqtSignal(str)

    def run(self):
        try:
            self.done.emit(core.remote_version())
        except Exception:                                  # noqa: BLE001
            self.done.emit("")


INSTALL_STEPS = ("Скачиваю ECHOES с GitHub", "Файлы плеера", "Python — свой, отдельный", "Менеджер пакетов pip",
                 "Библиотеки (самый долгий шаг)", "VLC — движок звука", "ffmpeg — загрузка и эффекты",
                 "Ярлыки и регистрация в Windows")
UPDATE_STEPS = ("Скачиваю новую версию с GitHub", "Нахожу установленный ECHOES", "Закрываю ECHOES",
                "Обновляю файлы программы", "Обновляю загрузчик yt-dlp")


class Job(QThread):
    step = pyqtSignal(int)              # активный шаг
    pct = pyqtSignal(int)
    line = pyqtSignal(str)
    finished_ok = pyqtSignal(bool, str)

    def __init__(self, mode, target, opts=None):
        super().__init__()
        self.mode, self.target, self.opts = mode, Path(target), opts or {}
        self._stop = False

    def run(self):
        try:
            self.step.emit(0)
            share = 25 if self.mode == "install" else 40

            def dl(done, total):
                if total:
                    self.pct.emit(int(share * done / total))
                    self.line.emit(f"Скачано {done // 2**20} из {total // 2**20} МБ")
            src = core.download_source(dl, lambda: self._stop)
            self.line.emit(f"Версия {core.version(src)} скачана")
            if self.mode == "install":
                args = ["-Auto", "-InstallDir", str(self.target), "-NoLaunch"]
                for key, flag in (("desktop", "-NoDesktop"), ("menu", "-NoStartMenu"), ("spotify", "-NoSpotify")):
                    if not self.opts.get(key, True):
                        args.append(flag)
                if self.opts.get("autostart"):
                    args.append("-Autostart")
                script = src / "setup" / "install.ps1"

                def on_line(text):
                    m = core.STEP_RE.search(text)
                    if m:
                        n, total = int(m.group(1)), int(m.group(2))
                        self.step.emit(n)
                        self.pct.emit(share + int((100 - share) * (n - 1) / total))
                    self.line.emit(text.strip())
            else:
                args = ["-Auto"]
                script = src / "setup" / "update.ps1"
                marks = ("Ищу установленный", "Закрываю ECHOES", "Обновляю файлы", "Обновляю загрузчик")

                def on_line(text):
                    for i, mk in enumerate(marks, 1):
                        if mk in text:
                            self.step.emit(i)
                            self.pct.emit(share + int((100 - share) * (i - 1) / len(marks)))
                    self.line.emit(text.strip())
            code = core.run_script(script, args, on_line, lambda: self._stop)
            if code != 0:
                raise RuntimeError(f"установщик завершился с ошибкой (код {code}) — подробности ниже")
            self.pct.emit(100)
            self.finished_ok.emit(True, "")
        except Exception as e:                             # noqa: BLE001
            self.finished_ok.emit(False, str(e) or e.__class__.__name__)


# ── окно ── #

class StepRow(QWidget):
    def __init__(self, text):
        super().__init__()
        self.state = "wait"
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 2, 0, 2)
        self.dot = QLabel()
        self.dot.setFixedWidth(22)
        self.lbl = QLabel(text)
        h.addWidget(self.dot)
        h.addWidget(self.lbl, 1)
        self.set("wait")

    def set(self, state):
        self.state = state
        sym, col = {"wait": ("○", "#55555e"), "run": ("●", _hex(ACC)), "ok": ("✓", "#3ddc84"),
                    "bad": ("×", "#ff5b5b")}[state]
        self.dot.setText(sym)
        self.dot.setStyleSheet(f"color:{col};font-size:15px;font-weight:900;")
        self.lbl.setStyleSheet(f"color:{'#ffffff' if state == 'run' else '#c9c9d1' if state == 'ok' else '#7d7d88'};"
                               f"font-weight:{'800' if state == 'run' else '600'};")


class Launcher(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ECHOES")
        self.resize(1000, 640)
        self.setMinimumSize(900, 600)
        self.store = QSettings("ECHOES", "Launcher")
        self.job = None
        self.remote = ""
        found = core.discover()
        saved = str(self.store.value("installation", "") or "")
        self.target = str(found or (saved if core.installation(saved) else "") or "")
        icon = Path(getattr(sys, "_MEIPASS", ROOT)) / "icon.ico"
        if not icon.exists():
            icon = ROOT / "app/icon.ico"
        self.setWindowIcon(QIcon(str(icon)))
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(16)
        self.neb = Nebula()
        root.addWidget(self.neb, 4)
        self.pages = QStackedWidget()
        root.addWidget(self.pages, 6)
        self.home = self._home_page()
        self.setup = self._install_page()
        self.prog = self._progress_page()
        self.done = self._done_page()
        for w in (self.home, self.setup, self.prog, self.done):
            self.pages.addWidget(w)
        self._style()
        self.refresh()
        self.check = RemoteCheck()
        self.check.done.connect(self._remote)
        self.check.start()

    # ── страницы ──
    def _card(self):
        f = QFrame()
        f.setObjectName("Card")
        v = QVBoxLayout(f)
        v.setContentsMargins(26, 24, 26, 24)
        v.setSpacing(12)
        return f, v

    def _home_page(self):
        f, v = self._card()
        self.h_title = QLabel("Готов к запуску")
        self.h_title.setObjectName("H1")
        v.addWidget(self.h_title)
        self.h_sub = QLabel("")
        self.h_sub.setObjectName("Sub")
        self.h_sub.setWordWrap(True)
        v.addWidget(self.h_sub)
        chips = QHBoxLayout()
        self.chip_ver = QLabel("")
        self.chip_ver.setObjectName("Chip")
        self.chip_new = QLabel("")
        self.chip_new.setObjectName("ChipNew")
        self.chip_new.hide()
        chips.addWidget(self.chip_ver)
        chips.addWidget(self.chip_new)
        chips.addStretch(1)
        v.addLayout(chips)
        v.addStretch(1)
        self.b_play = QPushButton("▶   Запустить ECHOES")
        self.b_play.setObjectName("Primary")
        self.b_play.setMinimumHeight(58)
        self.b_play.clicked.connect(self.launch)
        v.addWidget(self.b_play)
        self.b_update = QPushButton("Обновить")
        self.b_update.setObjectName("Accent2")
        self.b_update.setMinimumHeight(46)
        self.b_update.clicked.connect(self.start_update)
        self.b_update.hide()
        v.addWidget(self.b_update)
        row = QHBoxLayout()
        for text, fn in (("Папка программы", lambda: self.open_folder(self.target)),
                         ("Папка данных", lambda: self.open_folder(str(core.data_dir()))),
                         ("Переустановить…", lambda: self.pages.setCurrentWidget(self.setup)),
                         ("Удалить", self.uninstall)):
            b = QPushButton(text)
            b.setObjectName("Ghost")
            b.clicked.connect(fn)
            row.addWidget(b)
        v.addLayout(row)
        self.h_status = QLabel("")
        self.h_status.setObjectName("Sub")
        self.h_status.setWordWrap(True)
        v.addWidget(self.h_status)
        return f

    def _install_page(self):
        f, v = self._card()
        t = QLabel("Установка ECHOES")
        t.setObjectName("H1")
        v.addWidget(t)
        s = QLabel("Скачаю свежую версию с GitHub и поставлю всё, что нужно: свой Python, библиотеки, движок звука "
                   "VLC и ffmpeg. Права администратора не нужны, другие программы не трогаются.")
        s.setObjectName("Sub")
        s.setWordWrap(True)
        v.addWidget(s)
        v.addSpacing(6)
        lab = QLabel("КУДА УСТАНОВИТЬ")
        lab.setObjectName("Sec")
        v.addWidget(lab)
        row = QHBoxLayout()
        self.e_dir = QLineEdit(str(Path(self.target) if self.target else core.DEFAULT_DIR))
        self.e_dir.setReadOnly(True)
        row.addWidget(self.e_dir, 1)
        b = QPushButton("Изменить…")
        b.setObjectName("Ghost")
        b.clicked.connect(self.choose_dir)
        row.addWidget(b)
        v.addLayout(row)
        self.free_lbl = QLabel("")
        self.free_lbl.setObjectName("Sub")
        v.addWidget(self.free_lbl)
        lab = QLabel("ДОПОЛНИТЕЛЬНО")
        lab.setObjectName("Sec")
        v.addWidget(lab)
        self.opts = {}
        for key, text, on in (("desktop", "Ярлык на рабочем столе", True), ("menu", "Ярлыки в меню «Пуск»", True),
                              ("spotify", "Загрузчик из Spotify (spotdl, +40 МБ)", True),
                              ("autostart", "Запускать ECHOES вместе с Windows", False),
                              ("launch", "Открыть ECHOES после установки", True)):
            c = QCheckBox(text)
            c.setChecked(bool(self.store.value(f"opt_{key}", on, type=bool)))
            v.addWidget(c)
            self.opts[key] = c
        v.addStretch(1)
        row = QHBoxLayout()
        self.b_back = QPushButton("Назад")
        self.b_back.setObjectName("Ghost")
        self.b_back.clicked.connect(lambda: self.pages.setCurrentWidget(self.home))
        row.addWidget(self.b_back)
        go = QPushButton("Скачать и установить")
        go.setObjectName("Primary")
        go.setMinimumHeight(52)
        go.clicked.connect(self.start_install)
        row.addWidget(go, 1)
        v.addLayout(row)
        self._free()
        return f

    def _progress_page(self):
        f, v = self._card()
        self.p_title = QLabel("Установка")
        self.p_title.setObjectName("H1")
        v.addWidget(self.p_title)
        self.steps_box = QVBoxLayout()
        self.steps_box.setSpacing(2)
        v.addLayout(self.steps_box)
        self.rows = []
        v.addSpacing(6)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        v.addWidget(self.bar)
        self.p_line = QLabel("")
        self.p_line.setObjectName("Sub")
        self.p_line.setWordWrap(True)
        v.addWidget(self.p_line)
        self.b_log = QPushButton("Подробности ▾")
        self.b_log.setObjectName("Link")
        self.b_log.clicked.connect(lambda: (self.log.setVisible(not self.log.isVisible()),
                                            self.b_log.setText("Подробности ▴" if self.log.isVisible() else "Подробности ▾")))
        v.addWidget(self.b_log, 0, Qt.AlignmentFlag.AlignLeft)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.document().setMaximumBlockCount(800)
        self.log.hide()
        v.addWidget(self.log, 1)
        v.addStretch(1)
        return f

    def _done_page(self):
        f, v = self._card()
        v.addStretch(1)
        self.d_icon = QLabel("✓")
        self.d_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.d_icon.setStyleSheet(f"font-size:64px;font-weight:900;color:{_hex(ACC)};")
        v.addWidget(self.d_icon)
        self.d_title = QLabel("ECHOES установлен")
        self.d_title.setObjectName("H1")
        self.d_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.d_title)
        self.d_sub = QLabel("")
        self.d_sub.setObjectName("Sub")
        self.d_sub.setWordWrap(True)
        self.d_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.d_sub)
        v.addStretch(1)
        row = QHBoxLayout()
        self.d_back = QPushButton("К лаунчеру")
        self.d_back.setObjectName("Ghost")
        self.d_back.clicked.connect(lambda: (self.refresh(), self.pages.setCurrentWidget(self.home)))
        row.addWidget(self.d_back)
        self.d_play = QPushButton("▶   Запустить ECHOES")
        self.d_play.setObjectName("Primary")
        self.d_play.setMinimumHeight(52)
        self.d_play.clicked.connect(self.launch)
        row.addWidget(self.d_play, 1)
        v.addLayout(row)
        return f

    # ── состояние ──
    def refresh(self):
        ok = bool(self.target and core.installation(self.target))
        self.neb.ver = core.version(self.target) if ok else ""
        if not ok:
            self.pages.setCurrentWidget(self.setup)
            self.b_back.setVisible(False)
            return
        self.b_back.setVisible(True)
        ver = core.version(self.target)
        self.chip_ver.setText(f"Установлена {ver}")
        self.h_sub.setText(f"{self.target}")
        newer = self.remote and core.parse_version(self.remote) > core.parse_version(ver)
        self.chip_new.setVisible(bool(newer))
        self.chip_new.setText(f"Новая версия {self.remote}" if newer else "")
        self.b_update.setVisible(bool(newer))
        self.b_update.setText(f"Обновить до {self.remote}" if newer else "")
        if self.remote and not newer:
            self.h_status.setText("У вас последняя версия.")
        self.neb.update()

    def _remote(self, ver):
        self.remote = ver
        if not ver:
            self.h_status.setText("GitHub не ответил — проверю обновления в следующий раз.")
        self.refresh()

    def _free(self):
        try:
            import shutil
            d = Path(self.e_dir.text())
            while not d.exists() and d.parent != d:
                d = d.parent
            gb = shutil.disk_usage(d).free / 2**30
            self.free_lbl.setText(f"Нужно около 1 ГБ · свободно на диске {gb:.1f} ГБ")
        except OSError:
            self.free_lbl.setText("Нужно около 1 ГБ")

    def choose_dir(self):
        p = QFileDialog.getExistingDirectory(self, "Куда установить ECHOES", str(Path(self.e_dir.text()).parent))
        if not p:
            return
        d = Path(p)
        if d.name.lower() != "echoes":
            d = d / "ECHOES"
        self.e_dir.setText(str(d))
        self._free()

    # ── действия ──
    def _start(self, mode):
        self.rows = []
        while self.steps_box.count():
            it = self.steps_box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for text in (INSTALL_STEPS if mode == "install" else UPDATE_STEPS):
            r = StepRow(text)
            self.steps_box.addWidget(r)
            self.rows.append(r)
        self.p_title.setText("Установка ECHOES" if mode == "install" else f"Обновление до {self.remote}")
        self.bar.setValue(0)
        self.log.clear()
        self.p_line.setText("")
        self.pages.setCurrentWidget(self.prog)
        target = self.e_dir.text() if mode == "install" else self.target
        opts = {k: c.isChecked() for k, c in self.opts.items()}
        for k, v in opts.items():
            self.store.setValue(f"opt_{k}", v)
        self.job = Job(mode, target, opts)
        self.job.step.connect(self._step)
        self.job.pct.connect(self.bar.setValue)
        self.job.line.connect(self._line)
        self.job.finished_ok.connect(lambda ok, msg, m=mode, t=target, o=opts: self._finished(ok, msg, m, t, o))
        self.job.start()

    def start_install(self):
        self._start("install")

    def start_update(self):
        self._start("update")

    def _step(self, n):
        for i, r in enumerate(self.rows):
            r.set("ok" if i < n else "run" if i == n else "wait")

    def _line(self, text):
        self.log.appendPlainText(text)
        if not text.startswith(("  ", "PS ")):
            self.p_line.setText(text[:140])

    def _finished(self, ok, msg, mode, target, opts):
        self.job = None
        if not ok:
            for r in self.rows:
                if r.state == "run":
                    r.set("bad")
            self.p_line.setText("Не получилось: " + msg)
            self.log.show()
            self.b_log.setText("Подробности ▴")
            back = QPushButton("Назад")
            back.setObjectName("Ghost")
            back.clicked.connect(lambda: (back.deleteLater(), self.refresh(),
                                          self.pages.setCurrentWidget(self.home if self.target else self.setup)))
            self.steps_box.addWidget(back)
            return
        for r in self.rows:
            r.set("ok")
        if mode == "install":
            self.target = target
            self.store.setValue("installation", target)
            self.d_title.setText("ECHOES установлен")
            self.d_sub.setText(f"{target}\nЯрлыки созданы. Обновления дальше — с GitHub, кнопкой «Обновить» в самом плеере.")
            if opts.get("launch"):
                QTimer.singleShot(400, self.launch)
        else:
            self.d_title.setText("ECHOES обновлён")
            self.d_sub.setText(f"Версия {core.version(self.target)} — плеер уже запускается.")
        self.remote = self.remote or ""
        self.pages.setCurrentWidget(self.done)

    def launch(self):
        try:
            core.launch(self.target)
            self.h_status.setText("ECHOES запускается…")
            QTimer.singleShot(1200, self.close)
        except Exception as e:                             # noqa: BLE001
            self.h_status.setText(str(e))
            self.d_sub.setText(str(e))

    def uninstall(self):
        r = QMessageBox.question(self, "ECHOES", "Удалить ECHOES с этого компьютера?\n"
                                 "Откроется окно удаления — там можно оставить свою музыку и настройки.")
        if r == QMessageBox.StandardButton.Yes:
            try:
                core.uninstall(self.target)
            except Exception as e:                         # noqa: BLE001
                self.h_status.setText(str(e))

    def open_folder(self, path):
        if path and Path(path).is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        else:
            self.h_status.setText("Папка пока не создана.")

    def closeEvent(self, event):
        if self.job is not None:
            QMessageBox.information(self, "ECHOES", "Дождитесь окончания — сейчас ставятся файлы.")
            event.ignore()
        else:
            event.accept()

    def showEvent(self, e):
        super().showEvent(e)
        try:                                               # тёмная рамка окна Windows 10/11
            import ctypes
            hwnd = int(self.winId())
            val = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(val), ctypes.sizeof(val))
        except Exception:                                  # noqa: BLE001
            pass

    def _style(self):
        a, a2 = _hex(ACC), _hex(ACC.lighter(125))
        on = "#0b0b0d" if ACC.lightness() > 140 else "#ffffff"
        self.setStyleSheet(f"""
        QWidget {{ background:#0b0b0e; color:#ececf1; font-family:"Segoe UI"; font-size:13px; }}
        QFrame#Card {{ background:qlineargradient(x1:0,y1:0,x2:0.8,y2:1,stop:0 #17171c,stop:1 #0f0f13);
                       border:1px solid rgba(255,255,255,0.07); border-radius:22px; }}
        QFrame#Card QLabel, QFrame#Card QCheckBox {{ background:transparent; }}
        QLabel#H1 {{ font-size:28px; font-weight:900; }}
        QLabel#Sub {{ color:#9b9ba8; font-size:13px; }}
        QLabel#Sec {{ color:#7d7d88; font-size:11px; font-weight:800; letter-spacing:1px; padding-top:6px; }}
        QFrame#Card QLabel#Chip {{ background:rgba(255,255,255,0.07); border-radius:11px; padding:5px 12px; font-weight:700; }}
        QFrame#Card QLabel#ChipNew {{ background:{a}; color:{on}; border-radius:11px; padding:5px 12px; font-weight:800; }}
        QPushButton {{ background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.09); border-radius:14px;
                      padding:11px 16px; font-weight:700; }}
        QPushButton:hover {{ background:rgba(255,255,255,0.11); border-color:rgba(255,255,255,0.18); }}
        QPushButton#Primary {{ background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {a2},stop:1 {a}); color:{on};
                              border:none; font-size:17px; font-weight:900; }}
        QPushButton#Primary:hover {{ background:{a2}; }}
        QPushButton#Primary:disabled {{ background:#2a2a30; color:#77777f; }}
        QPushButton#Accent2 {{ background:transparent; border:2px solid {a}; color:{a2}; font-size:15px; font-weight:800; }}
        QPushButton#Ghost {{ background:rgba(255,255,255,0.05); font-weight:600; }}
        QPushButton#Link {{ background:transparent; border:none; color:#9b9ba8; padding:2px 0; text-align:left; }}
        QPushButton#Link:hover {{ color:#ffffff; }}
        QLineEdit {{ background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.1); border-radius:12px;
                    padding:10px 12px; color:#ececf1; }}
        QCheckBox {{ spacing:10px; padding:3px 0; font-size:14px; }}
        QCheckBox::indicator {{ width:36px; height:20px; border-radius:10px; background:#2a2a31; }}
        QCheckBox::indicator:checked {{ background:{a}; }}
        QProgressBar {{ background:rgba(255,255,255,0.07); border:none; border-radius:5px; height:10px; }}
        QProgressBar::chunk {{ background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 {a},stop:1 #ff4f9a); border-radius:5px; }}
        QPlainTextEdit {{ background:#0d0d10; border:1px solid rgba(255,255,255,0.07); border-radius:12px; padding:8px;
                         color:#a9a9b4; font-family:Consolas; font-size:11px; }}
        """)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    lock = QLockFile(str(Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.TempLocation))
                         / "echoes-launcher.lock"))
    if not lock.tryLock(0):
        QMessageBox.information(None, "ECHOES", "Лаунчер уже открыт.")
        sys.exit(0)
    w = Launcher()
    w.show()
    if "--smoke-test" in sys.argv:
        def capture():
            out = Path(os.environ.get("ECHOES_LAUNCHER_SHOT") or (ROOT / "launcher-preview.png"))
            w.grab().save(str(out))
            if "--install-page" in sys.argv:
                w.pages.setCurrentWidget(w.setup)
                app.processEvents()
                w.grab().save(str(out.with_name(out.stem + "-install.png")))
            if "--progress-page" in sys.argv:
                w._start_preview = True
                for t in INSTALL_STEPS:
                    pass
                w.rows = []
                for t in INSTALL_STEPS:
                    r = StepRow(t); w.steps_box.addWidget(r); w.rows.append(r)
                w._step(4); w.bar.setValue(55); w.p_line.setText("Библиотеки Python… PyQt6")
                w.pages.setCurrentWidget(w.prog)
                app.processEvents()
                w.grab().save(str(out.with_name(out.stem + "-progress.png")))
            app.quit()
        QTimer.singleShot(900, capture)
    sys.exit(app.exec())
