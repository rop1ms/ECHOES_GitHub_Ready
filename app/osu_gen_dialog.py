# osu_gen_dialog.py
"""
Окно «Сгенерировать карту» темы esu!: весь трек или только отрывок.

Волна трека во всю ширину, выделенный отрывок — светлый, ручки по краям тянутся мышью,
за середину выделение двигается целиком; щелчок вне выделения — послушать с этого места.
«Слушать отрывок» играет ровно выделенное (в конце — пауза), бегущая линия показывает,
где звук. Быстрые длины: весь трек, 30 с, 60 с, 90 с, 2 мин (от начала выделения).
"""
from __future__ import annotations

import threading

import numpy as np
from PyQt6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

PINK = QColor(255, 102, 170)
MIN_LEN = 15_000                     # мс — короче карта не имеет смысла


def _fmt(ms) -> str:
    s = max(0, int(round(ms / 1000)))
    return f"{s // 60}:{s % 60:02d}"


class _Bridge(QObject):
    peaks = pyqtSignal(object, float)


class SegmentBar(QWidget):
    changed = pyqtSignal()
    seek = pyqtSignal(float)          # мс — послушать отсюда

    def __init__(self, dur_ms, parent=None):
        super().__init__(parent)
        self.dur = max(1.0, float(dur_ms))
        self.a, self.b = 0.0, self.dur
        self.peaks = None
        self.play_ms = None
        self._drag = None             # ("a"|"b"|"move", смещение)
        self.setMinimumHeight(130)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_range(self, a, b):
        a = max(0.0, min(float(a), self.dur - MIN_LEN))
        b = min(self.dur, max(float(b), a + MIN_LEN))
        self.a, self.b = a, b
        self.update()
        self.changed.emit()

    def _x(self, ms):
        return 8 + (self.width() - 16) * ms / self.dur

    def _ms(self, x):
        return max(0.0, min(self.dur, (x - 8) / max(1.0, self.width() - 16) * self.dur))

    def mousePressEvent(self, e):
        x = e.position().x()
        xa, xb = self._x(self.a), self._x(self.b)
        if abs(x - xa) <= 9:
            self._drag = ("a", 0.0)
        elif abs(x - xb) <= 9:
            self._drag = ("b", 0.0)
        elif xa < x < xb:
            self._drag = ("move", self._ms(x) - self.a, x)
        else:
            self.seek.emit(self._ms(x))

    def mouseMoveEvent(self, e):
        x = e.position().x()
        if self._drag is None:
            near = min(abs(x - self._x(self.a)), abs(x - self._x(self.b))) <= 9
            self.setCursor(Qt.CursorShape.SizeHorCursor if near else Qt.CursorShape.PointingHandCursor)
            return
        kind = self._drag[0]
        ms = self._ms(x)
        if kind == "a":
            self.set_range(min(ms, self.b - MIN_LEN), self.b)
        elif kind == "b":
            self.set_range(self.a, max(ms, self.a + MIN_LEN))
        else:
            ln = self.b - self.a
            a = max(0.0, min(self.dur - ln, ms - self._drag[1]))
            self.set_range(a, a + ln)

    def mouseReleaseEvent(self, e):
        d = self._drag
        self._drag = None
        if d and d[0] == "move" and abs(e.position().x() - d[2]) < 3:
            self.seek.emit(self._ms(e.position().x()))   # щелчок внутри без сдвига — слушать отсюда

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        r = QRectF(0, 0, W, H)
        path = QPainterPath()
        path.addRoundedRect(r, 12, 12)
        p.fillPath(path, QColor(16, 12, 22))
        xa, xb = self._x(self.a), self._x(self.b)
        p.fillRect(QRectF(xa, 0, xb - xa, H), QColor(255, 102, 170, 46))
        mid = H / 2
        if self.peaks is not None and len(self.peaks):
            n = len(self.peaks)
            bw = (W - 16) / n
            p.setPen(Qt.PenStyle.NoPen)
            for i, v in enumerate(self.peaks):
                x = 8 + i * bw
                h = max(1.5, float(v) * (H * 0.42))
                inside = xa <= x + bw / 2 <= xb
                p.setBrush(QColor(255, 170, 210) if inside else QColor(255, 255, 255, 70))
                p.drawRect(QRectF(x, mid - h, max(1.0, bw - 0.6), h * 2))
        else:
            p.setPen(QColor(255, 255, 255, 120))
            p.setFont(QFont("Segoe UI", 10))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, "Читаю трек…")
        for x in (xa, xb):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(PINK)
            p.drawRoundedRect(QRectF(x - 3, 4, 6, H - 8), 3, 3)
            p.drawEllipse(QPointF(x, mid), 7, 7)
        if self.play_ms is not None:
            x = self._x(self.play_ms)
            p.setPen(QPen(QColor(255, 255, 255), 2))
            p.drawLine(QPointF(x, 2), QPointF(x, H - 2))
        p.end()


class GenerateDialog(QDialog):
    def __init__(self, shell, track, parent=None):
        super().__init__(parent)
        self.shell, self.t = shell, track
        self.win = shell.win
        self.setWindowTitle("Сгенерировать карту")
        self.setMinimumWidth(760)
        self.setStyleSheet(
            "QDialog{background:#1b1424;} QLabel{color:#ffffff;background:transparent;}"
            "QLabel#Sub{color:rgba(255,255,255,0.6);font-size:12px;}"
            "QLabel#Val{color:#ff9ccb;font-size:13px;font-weight:700;}"
            "QPushButton{background:rgba(255,255,255,0.10);color:#fff;border:none;border-radius:14px;"
            "padding:7px 14px;font-weight:600;}"
            "QPushButton:hover{background:rgba(255,255,255,0.18);}"
            "QPushButton#Main{background:#ff66aa;font-weight:800;padding:9px 22px;}"
            "QPushButton#Main:hover{background:#ff85bd;}")
        dur = float(track.get("duration") or 0) * 1000
        if dur <= 0:
            try:
                dur = float(self.win.engine.duration or 0) * 1000 if shell._is_current(track) else 0
            except Exception:                                  # noqa: BLE001
                dur = 0
        self.dur = max(dur, MIN_LEN + 1000)
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 18, 22, 18)
        v.setSpacing(10)
        title = QLabel(f"{track.get('artist') or ''} — {track.get('title') or ''}".strip(" —"))
        title.setStyleSheet("font-size:17px;font-weight:800;")
        v.addWidget(title)
        sub = QLabel("Карта строится только по выделенному отрывку. Тяните розовые края, двигайте выделение "
                     "за середину; щелчок по волне — послушать с этого места.")
        sub.setObjectName("Sub")
        sub.setWordWrap(True)
        v.addWidget(sub)
        self.bar = SegmentBar(self.dur)
        v.addWidget(self.bar)
        row = QHBoxLayout()
        self.lbl = QLabel("")
        self.lbl.setObjectName("Val")
        row.addWidget(self.lbl)
        row.addStretch(1)
        for name, ln in (("Весь трек", 0), ("30 с", 30), ("60 с", 60), ("90 с", 90), ("2 мин", 120)):
            b = QPushButton(name)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, ln=ln: self._preset(ln))
            row.addWidget(b)
        v.addLayout(row)
        row2 = QHBoxLayout()
        self.play_btn = QPushButton("Слушать отрывок")
        self.play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_btn.clicked.connect(self._toggle_listen)
        row2.addWidget(self.play_btn)
        row2.addStretch(1)
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Сгенерировать")
        ok.setObjectName("Main")
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.clicked.connect(self.accept)
        row2.addWidget(cancel)
        row2.addWidget(ok)
        v.addLayout(row2)
        self.bar.changed.connect(self._label)
        self.bar.seek.connect(lambda ms: self._listen(ms, to_end=False))
        self._listening = False
        self._stop_at = None
        self._init_range()
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self._br = _Bridge()
        self._br.peaks.connect(self._got_peaks)
        threading.Thread(target=self._load_peaks, daemon=True, name="esu-wave").start()

    # ── выделение ── #
    def _init_range(self):
        seg = self.shell.segment_of(self.t)
        if seg:
            self.bar.set_range(*seg)
        elif self.dur > 150_000:
            start = self.dur * 0.3
            try:
                import osu_mapgen as G
                d = G.load_cached(str(self.t.get("path")), self.shell.S().get("mapgen"))
                if d and d.get("an"):
                    start = max(0.0, float(G.preview_time(d["an"])) - 15_000)
            except Exception:                                  # noqa: BLE001
                pass
            self.bar.set_range(start, start + 90_000)
        else:
            self.bar.set_range(0, self.dur)
        self._label()

    def _preset(self, sec):
        if not sec:
            self.bar.set_range(0, self.dur)
            return
        a = self.bar.a
        if a + sec * 1000 > self.dur:
            a = max(0.0, self.dur - sec * 1000)
        self.bar.set_range(a, a + sec * 1000)

    def _label(self):
        a, b = self.bar.a, self.bar.b
        whole = a <= 500 and b >= self.dur - 500
        self.lbl.setText(f"Весь трек · {_fmt(self.dur)}" if whole else
                         f"{_fmt(a)} – {_fmt(b)}   ·   длина {_fmt(b - a)}")

    def result_segment(self):
        a, b = self.bar.a, self.bar.b
        if a <= 500 and b >= self.dur - 500:
            return None
        return [int(a), int(b)]

    # ── волна ── #
    def _load_peaks(self):
        try:
            import osu_mapgen as G
            x = G.decode(str(self.t.get("path")))
            x = np.abs(np.asarray(x, np.float32).reshape(-1))
            dur = len(x) / float(G.SR) * 1000
            n = 300
            k = max(1, len(x) // n)
            pk = x[:k * n].reshape(n, k).max(1) if len(x) >= n else np.zeros(n)
            pk = pk / (np.percentile(pk, 98) + 1e-9)
            self._br.peaks.emit(np.clip(pk, 0, 1), dur)
        except Exception as e:                                 # noqa: BLE001
            print("[esu gen] wave:", e)

    def _got_peaks(self, pk, dur):
        try:
            self.bar.peaks = pk
            if dur > MIN_LEN and abs(dur - self.dur) > 1500:  # длина из тегов неточная — по звуку
                a, b = self.bar.a, self.bar.b
                self.dur = self.bar.dur = dur
                self.bar.set_range(min(a, dur - MIN_LEN), min(b, dur))
            self.bar.update()
        except RuntimeError:
            pass

    # ── прослушивание ── #
    def _listen(self, ms, to_end=True):
        try:
            self.shell._light_load(self.t, ms / 1000.0, autoplay=True)
        except Exception as e:                                 # noqa: BLE001
            print("[esu gen] listen:", e)
            return
        self._listening = True
        self._stop_at = self.bar.b if (to_end or self.bar.a <= ms <= self.bar.b) else None
        self.play_btn.setText("Стоп")

    def _toggle_listen(self):
        if self._listening:
            self._stop()
        else:
            self._listen(self.bar.a)

    def _stop(self):
        self._listening = False
        self._stop_at = None
        self.play_btn.setText("Слушать отрывок")
        try:
            self.win.engine.pause()
        except Exception:                                      # noqa: BLE001
            pass

    def _tick(self):
        try:
            eng = self.win.engine
            mine = self.shell._is_current(self.t)
            pos = float(eng.get_position() or 0) * 1000 if mine else None
        except Exception:                                      # noqa: BLE001
            pos = None
        self.bar.play_ms = pos
        self.bar.update()
        if self._listening and pos is not None and self._stop_at is not None and pos >= self._stop_at:
            self._stop()

    def done(self, r):
        self._timer.stop()
        if self._listening:
            self._stop()
        super().done(r)
