# net_voicemsg.py
"""
Голосовые сообщения в чатах Echoes.NET.

  * запись: зажмите 🎙 у поля ввода — идёт запись (до 3 минут), отпустили — сообщение уходит
    (короче 0,7 с — не отправляется: случайное нажатие); есть ffmpeg — сжимается в Opus (≈ 3 КБ/с);
  * в чате — плашка: ▶/⏸, волна (пройденное — цветом, клик по волне — перемотка), длительность, ×1 / ×1.5 / ×2;
  * играет отдельным плеером — музыка ставится на паузу и продолжается после.
Файл уходит обычным путём файлов чата (через Tor напрямую), имя начинается с «Голосовое».
"""
from __future__ import annotations

import subprocess
import threading
import time
import wave
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

SR = 16000
MAX_SEC = 180
PREFIX = "Голосовое"


def is_voice(meta) -> bool:
    return str((meta or {}).get("name", "")).startswith(PREFIX)


class Recorder:
    """Запись с микрофона по умолчанию (sounddevice), в память."""

    def __init__(self):
        self.chunks = []
        self.stream = None
        self.t0 = 0.0
        self.level = 0.0

    def start(self):
        import sounddevice as sd
        self.chunks = []
        self.t0 = time.monotonic()

        def cb(indata, frames, t, status):
            x = indata[:, 0].copy()
            self.chunks.append(x)
            self.level = float(np.sqrt(np.mean(x * x)))
        self.stream = sd.InputStream(samplerate=SR, channels=1, dtype="float32", blocksize=800, callback=cb)
        self.stream.start()

    def stop(self) -> np.ndarray:
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:                                  # noqa: BLE001
                pass
            self.stream = None
        return np.concatenate(self.chunks) if self.chunks else np.zeros(0, np.float32)

    def seconds(self) -> float:
        return time.monotonic() - self.t0 if self.stream is not None else 0.0


def save(x: np.ndarray, folder: Path) -> Path | None:
    """Запись → файл: Opus (если есть ffmpeg), иначе WAV 16 кГц моно."""
    if x.size < SR * 0.7:
        return None
    x = x[: SR * MAX_SEC]
    pk = float(np.abs(x).max()) or 1.0
    if pk < 0.5:                                               # тихий микрофон — поднять, но без перегруза
        x = x * min(4.0, 0.5 / pk)
    folder.mkdir(parents=True, exist_ok=True)
    stem = folder / time.strftime(f"{PREFIX} %Y-%m-%d %H-%M-%S")
    wav = stem.with_suffix(".wav")
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    try:
        from loudness import _ffmpeg
        ff = _ffmpeg()
    except Exception:                                          # noqa: BLE001
        ff = None
    if ff:
        ogg = stem.with_suffix(".ogg")
        try:
            r = subprocess.run([ff, "-v", "error", "-y", "-i", str(wav), "-c:a", "libopus", "-b:a", "24k", str(ogg)],
                               capture_output=True, timeout=30, creationflags=0x08000000)
            if r.returncode == 0 and ogg.exists() and ogg.stat().st_size > 200:
                wav.unlink(missing_ok=True)
                return ogg
        except Exception:                                      # noqa: BLE001
            pass
    return wav


class RecButton(QPushButton):
    """🎙 у поля ввода: зажать — запись, отпустить — отправить."""
    done = pyqtSignal(str)

    def __init__(self, folder: Path, parent=None):
        super().__init__("\U000f1007", parent)
        self.folder = folder
        self.rec = None
        self.setFixedSize(40, 40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Голосовое сообщение: зажмите и говорите, отпустите — отправится")
        self._base = "QPushButton{border-radius:20px;font-size:16px;}"
        self.setStyleSheet(self._base)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._tick)

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        try:
            self.rec = Recorder()
            self.rec.start()
        except Exception as ex:                                # noqa: BLE001
            self.rec = None
            self.setToolTip(f"Микрофон недоступен: {ex}")
            return
        self.setStyleSheet(self._base + "QPushButton{background:#ff4d4d;color:#fff;}")
        self.timer.start()

    def _tick(self):
        if self.rec is None:
            return
        s = self.rec.seconds()
        self.setText(f"{int(s)}")
        if s >= MAX_SEC:
            self.mouseReleaseEvent(None)

    def mouseReleaseEvent(self, e):
        if self.rec is None:
            return
        self.timer.stop()
        x = self.rec.stop()
        self.rec = None
        self.setText("\U000f1007")
        self.setStyleSheet(self._base)
        p = save(x, self.folder)
        if p is not None:
            self.done.emit(str(p))


def peaks_of(path: str, n=60) -> list:
    try:
        from daw_dsp import decode
        x = decode(path, sr=8000, channels=1, timeout=20)[:, 0]
    except Exception:                                          # noqa: BLE001
        return []
    if x.size < n:
        return []
    m = x.size // n
    v = np.sqrt(np.mean(np.square(x[: m * n].reshape(n, m)), axis=1))
    v = v / max(1e-6, float(v.max()))
    return [round(float(a), 3) for a in v]


class VoiceWave(QWidget):
    seek = pyqtSignal(float)

    def __init__(self, peaks, parent=None):
        super().__init__(parent)
        self.peaks = peaks or [0.3] * 40
        self.frac = 0.0
        self.setFixedHeight(28)
        self.setMinimumWidth(150)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, e):
        self.seek.emit(max(0.0, min(1.0, e.position().x() / max(1, self.width()))))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        n = len(self.peaks)
        W, H = self.width(), self.height()
        bw = W / n
        p.setPen(Qt.PenStyle.NoPen)
        for i, v in enumerate(self.peaks):
            h = 3 + v * (H - 4)
            p.setBrush(QColor("#ffdb1a") if (i + 0.5) / n <= self.frac else QColor(255, 255, 255, 90))
            p.drawRoundedRect(QRectF(i * bw + 0.5, (H - h) / 2, max(1.5, bw - 1.5), h), 1, 1)
        p.end()


class VoicePlayer:
    """Один плеер голосовых на приложение: ставит музыку на паузу и продолжает её после."""
    _inst = None

    def __init__(self, win):
        import vlc
        self.win = win
        self.vlc = vlc.Instance("--quiet --no-video")
        self.p = self.vlc.media_player_new()
        self.row = None
        self._paused_main = False
        self.timer = QTimer()
        self.timer.setInterval(80)
        self.timer.timeout.connect(self._tick)

    @classmethod
    def get(cls, win):
        if cls._inst is None:
            cls._inst = VoicePlayer(win)
        return cls._inst

    def play(self, row, path, rate):
        if self.row is not None and self.row is not row:
            self.stop()
        if self.row is row and self.p.is_playing():
            self.p.pause()
            row.set_playing(False)
            return
        if self.row is not row:
            self.p.set_media(self.vlc.media_new(path))
            self.row = row
            try:
                if self.win.engine.is_playing():
                    self.win.engine.pause()
                    self._paused_main = True
            except Exception:                                  # noqa: BLE001
                pass
        self.p.play()
        self.p.set_rate(rate)
        row.set_playing(True)
        self.timer.start()

    def set_rate(self, rate):
        self.p.set_rate(rate)

    def seek(self, frac):
        try:
            self.p.set_position(float(frac))
        except Exception:                                      # noqa: BLE001
            pass

    def stop(self):
        self.timer.stop()
        try:
            self.p.stop()
        except Exception:                                      # noqa: BLE001
            pass
        if self.row is not None:
            try:
                self.row.set_playing(False)
                self.row.wave.frac = 0
                self.row.wave.update()
            except RuntimeError:
                pass
        self.row = None
        if self._paused_main:
            self._paused_main = False
            try:
                self.win.engine.play()
            except Exception:                                  # noqa: BLE001
                pass

    def _tick(self):
        if self.row is None:
            self.timer.stop()
            return
        st = str(self.p.get_state())
        if "Ended" in st or "Error" in st:
            self.stop()
            return
        try:
            self.row.wave.frac = max(0.0, float(self.p.get_position()))
            self.row.wave.update()
            self.row.time_lbl.setText(_fmt(self.p.get_time() / 1000.0) + " / " + _fmt(self.row.dur))
        except RuntimeError:
            self.stop()


def _fmt(s) -> str:
    s = max(0, int(s or 0))
    return f"{s // 60}:{s % 60:02d}"


class VoiceRow(QFrame):
    """Плашка голосового в чате (для FileCard)."""
    RATES = (1.0, 1.5, 2.0)

    def __init__(self, card, pv):
        super().__init__()
        self.card = card
        self.dur = float(pv.get("dur") or 0)
        self.rate = 1.0
        self.setObjectName("VoiceRow")
        self.setStyleSheet("QFrame#VoiceRow{background:rgba(0,0,0,0.35);border-radius:18px;} QLabel{background:transparent;}"
                           "QPushButton{background:rgba(255,255,255,0.1);border:none;border-radius:15px;color:#fff;"
                           "font-weight:800;padding:0;} QPushButton:hover{background:rgba(255,255,255,0.2);}")
        h = QHBoxLayout(self)
        h.setContentsMargins(8, 6, 10, 6)
        h.setSpacing(8)
        self.play_btn = QPushButton("▶")
        self.play_btn.setFixedSize(34, 34)
        self.play_btn.setStyleSheet("QPushButton{background:#ffdb1a;color:#000;border-radius:17px;font-size:14px;}")
        self.play_btn.clicked.connect(self.toggle)
        h.addWidget(self.play_btn)
        self.wave = VoiceWave(pv.get("vwave") or pv.get("wave"))
        self.wave.seek.connect(self._seek)
        h.addWidget(self.wave, 1)
        self.time_lbl = QLabel(_fmt(self.dur))
        self.time_lbl.setStyleSheet("font-size:11px;color:#cfcfcf;")
        h.addWidget(self.time_lbl)
        self.rate_btn = QPushButton("×1")
        self.rate_btn.setFixedSize(40, 30)
        self.rate_btn.setToolTip("Скорость: ×1 / ×1.5 / ×2")
        self.rate_btn.clicked.connect(self.next_rate)
        h.addWidget(self.rate_btn)
        self.setFixedWidth(330)

    def set_playing(self, on):
        self.play_btn.setText("\U000f1013" if on else "▶")

    def toggle(self):
        path = self.card.path
        if not path or not Path(path).exists():
            self.card.activate()                                # ещё не скачано — скачать
            return
        if not self.wave.peaks or self.wave.peaks == [0.3] * 40:
            self.wave.peaks = peaks_of(path) or self.wave.peaks
        VoicePlayer.get(self.card.page.win).play(self, path, self.rate)

    def next_rate(self):
        i = (self.RATES.index(self.rate) + 1) % len(self.RATES)
        self.rate = self.RATES[i]
        self.rate_btn.setText(f"×{self.rate:g}")
        vp = VoicePlayer._inst
        if vp is not None and vp.row is self:
            vp.set_rate(self.rate)

    def _seek(self, frac):
        vp = VoicePlayer._inst
        if vp is not None and vp.row is self:
            vp.seek(frac)
        else:
            self.toggle()
            QTimer.singleShot(200, lambda: VoicePlayer.get(self.card.page.win).seek(frac))
