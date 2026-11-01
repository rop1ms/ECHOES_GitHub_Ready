# daw_plugins.py
"""
Окна плагинов студии: инструменты каналов и эффекты микшера. Ручки строятся по описанию
параметров (выбор из списка — выпадающий список, вкл/выкл — флажок). Сверху — пресеты и
наглядная часть: график эквалайзера с точками полос (тянуть мышью, колесо — ширина),
индикатор подавления (компрессор, лимитер, де-эссер, гейт, качка), ноты автотюна,
осциллограф и спектр анализатора, волна сэмплера; у инструментов — клавиатура для пробы.
"""
from __future__ import annotations

import math
import os

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel,
                             QScrollArea, QVBoxLayout, QWidget)

import daw_dsp as D
import daw_fx as FX
import daw_inst as I
import daw_project as PR
import daw_ui as U


# ------------------------------------------------------------------ #
#  Наглядные части                                                    #
# ------------------------------------------------------------------ #

class EqGraph(QWidget):
    """АЧХ эквалайзера с точками полос."""
    FMIN, FMAX, DBR = 20.0, 20000.0, 18.0

    def __init__(self, panel, parent=None):
        super().__init__(parent)
        self.panel = panel
        self.setMinimumHeight(170)
        self.setMouseTracking(True)
        self.drag = None
        self.freqs = np.geomspace(self.FMIN, self.FMAX, 220)

    def p(self):
        return self.panel.params()

    def fx(self):
        return FX.EQ(self.p())

    def fx2x(self, f):
        return 10 + (math.log10(f) - math.log10(self.FMIN)) / (math.log10(self.FMAX) - math.log10(self.FMIN)) * (self.width() - 20)

    def x2f(self, x):
        t = (x - 10) / max(1, self.width() - 20)
        return 10 ** (math.log10(self.FMIN) + t * (math.log10(self.FMAX) - math.log10(self.FMIN)))

    def db2y(self, d):
        return self.height() / 2 - d / self.DBR * (self.height() / 2 - 10)

    def y2db(self, y):
        return (self.height() / 2 - y) / (self.height() / 2 - 10) * self.DBR

    def points(self):
        P = self.p()
        out = []
        for i, (kind, _f) in enumerate(FX.EQ_BANDS, 1):
            f = float(P.get(f"f{i}", _f))
            g = 0.0 if kind in ("lowcut", "highcut") else float(P.get(f"g{i}", 0))
            out.append((i, kind, QPointF(self.fx2x(f), self.db2y(g))))
        return out

    def _hit(self, pos):
        for i, kind, pt in self.points():
            if (pt - pos).manhattanLength() < 12:
                return i, kind
        return None

    def mousePressEvent(self, e):
        h = self._hit(e.position())
        if h:
            self.panel.commit()
            self.drag = h

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self.drag is None:
            h = self._hit(pos)
            self.setCursor(Qt.CursorShape.SizeAllCursor if h else Qt.CursorShape.ArrowCursor)
            if h:
                i, kind = h
                P = self.p()
                U.hint_to(self, f"{FX.EQ_NAMES[i - 1]}: {FX.EQ.spec(f'f{i}').fmt(P[f'f{i}'])}" +
                          ("" if kind in ("lowcut", "highcut") else f", {P[f'g{i}']:+.1f} дБ, Q {P[f'q{i}']:.2f}") +
                          " — тянуть, колесо — ширина")
            return
        i, kind = self.drag
        P = self.p()
        f = max(20.0, min(20000.0, self.x2f(pos.x())))
        P[f"f{i}"] = round(f, 1)
        if kind not in ("lowcut", "highcut"):
            P[f"g{i}"] = round(max(-18.0, min(18.0, self.y2db(pos.y()))), 2)
        self.update()
        self.panel.param_changed()

    def mouseReleaseEvent(self, e):
        self.drag = None
        self.panel.sync_knobs()

    def wheelEvent(self, e):
        h = self._hit(e.position())
        if not h:
            return
        i, kind = h
        if kind in ("lowcut", "highcut"):
            return
        P = self.p()
        self.panel.commit()
        q = float(P.get(f"q{i}", 0.9)) * (1.12 if e.angleDelta().y() > 0 else 1 / 1.12)
        P[f"q{i}"] = round(max(0.2, min(8.0, q)), 3)
        self.update()
        self.panel.param_changed()
        self.panel.sync_knobs()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor("#16191b"))
        p.setPen(QColor("#2b3135"))
        for f in (50, 100, 200, 500, 1000, 2000, 5000, 10000):
            x = self.fx2x(f)
            p.drawLine(QPointF(x, 0), QPointF(x, h))
        for d in (-12, -6, 0, 6, 12):
            y = self.db2y(d)
            p.setPen(QColor("#3a4146") if d == 0 else QColor("#252a2e"))
            p.drawLine(QPointF(0, y), QPointF(w, y))
        p.setPen(U.DIM)
        p.setFont(U.font(9))
        for f, t in ((100, "100"), (1000, "1к"), (10000, "10к")):
            p.drawText(QPointF(self.fx2x(f) + 2, h - 3), t)
        try:
            resp = self.fx().response(self.freqs)
        except Exception:                                # noqa: BLE001
            resp = np.zeros(len(self.freqs))
        path = QPainterPath()
        for k, (f, d) in enumerate(zip(self.freqs, resp)):
            pt = QPointF(self.fx2x(f), self.db2y(max(-self.DBR * 1.2, min(self.DBR * 1.2, d))))
            if k == 0:
                path.moveTo(pt)
            else:
                path.lineTo(pt)
        fill = QPainterPath(path)
        fill.lineTo(QPointF(self.fx2x(self.freqs[-1]), self.db2y(0)))
        fill.lineTo(QPointF(self.fx2x(self.freqs[0]), self.db2y(0)))
        fill.closeSubpath()
        p.fillPath(fill, QColor(240, 163, 58, 40))
        p.setPen(QPen(U.ACC, 2))
        p.drawPath(path)
        cols = ["#e8574f", "#f0a33a", "#d8d36c", "#9bd15c", "#6cd8c4", "#5ea8e8", "#b38fd8"]
        for i, kind, pt in self.points():
            p.setBrush(QColor(cols[i - 1]))
            p.setPen(QPen(QColor("#111"), 1))
            p.drawEllipse(pt, 6, 6)
            p.setPen(QColor("#111"))
            p.setFont(U.font(8, True))
            p.drawText(QRectF(pt.x() - 6, pt.y() - 6, 12, 12), Qt.AlignmentFlag.AlignCenter, str(i))
        p.end()


class GrMeter(QWidget):
    """Подавление (дБ) / поправка автотюна."""

    def __init__(self, panel, parent=None, kind="gr"):
        super().__init__(parent)
        self.panel = panel
        self.kind = kind
        self.setMinimumHeight(46 if kind != "scope" else 140)
        self.val = 0.0

    def tick(self):
        fx = self.panel.fx_obj()
        if fx is None:
            return
        self.val = float(getattr(fx, "meter", 0.0) or 0.0)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor("#16191b"))
        fx = self.panel.fx_obj()
        if self.kind == "gr":
            gr = max(-24.0, min(0.0, self.val))
            p.setPen(U.DIM)
            p.setFont(U.font(10))
            p.drawText(QRectF(8, 4, w, 14), Qt.AlignmentFlag.AlignLeft, "Подавление")
            bar = QRectF(8, 22, w - 16, 14)
            p.fillRect(bar, QColor("#24292d"))
            ww = (-gr / 24.0) * bar.width()
            p.fillRect(QRectF(bar.right() - ww, bar.top(), ww, bar.height()), U.ACC)
            p.setPen(U.TEXT)
            p.drawText(QRectF(8, 4, w - 16, 14), Qt.AlignmentFlag.AlignRight, f"{gr:.1f} дБ")
        elif self.kind == "tune":
            det = getattr(fx, "detected", 0.0) if fx else 0.0
            tgt = getattr(fx, "target", 0.0) if fx else 0.0
            p.setFont(U.font(16, True, mono=True))
            if det > 0:
                p.setPen(U.TEXT)
                p.drawText(QRectF(10, 0, w / 2, h), Qt.AlignmentFlag.AlignVCenter,
                           "слышу " + D.note_name(int(round(D.hz_to_midi(det)))))
                p.setPen(U.ACC)
                p.drawText(QRectF(w / 2, 0, w / 2 - 10, h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                           "→ " + (D.note_name(int(round(D.hz_to_midi(tgt)))) if tgt > 0 else "—") +
                           f"  {self.val:+.2f} пт")
            else:
                p.setPen(U.DIM)
                p.setFont(U.font(12))
                p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Голоса нет — включите воспроизведение")
        elif self.kind == "scope" and fx is not None:
            buf = getattr(fx, "buf", None)
            if buf is not None and len(buf):
                half = h / 2
                path = QPainterPath()
                n = len(buf)
                for i in range(0, n, max(1, n // w)):
                    x = i / n * w
                    y = half * 0.5 - float(buf[i]) * half * 0.45
                    if i == 0:
                        path.moveTo(x, y)
                    else:
                        path.lineTo(x, y)
                p.setPen(QPen(U.GRN, 1.2))
                p.drawPath(path)
                spec = np.abs(np.fft.rfft(buf * np.hanning(n)))
                spec = 20 * np.log10(spec / (n / 4) + 1e-6)
                freqs = np.fft.rfftfreq(n, 1 / D.SR)
                path = QPainterPath()
                first = True
                for f, s in zip(freqs[1:], spec[1:]):
                    if f < 20:
                        continue
                    x = (math.log10(f) - 1.3) / (math.log10(20000) - 1.3) * w
                    y = h - max(0.0, min(1.0, (s + 80) / 80)) * half * 0.95
                    if first:
                        path.moveTo(x, y)
                        first = False
                    else:
                        path.lineTo(x, y)
                p.setPen(QPen(U.ACC, 1.2))
                p.drawPath(path)
        p.end()


class SampleView(QWidget):
    """Волна звука сэмплера."""

    def __init__(self, panel, parent=None):
        super().__init__(parent)
        self.panel = panel
        self.setMinimumHeight(70)
        self._key = None
        self._pk = None

    def paintEvent(self, e):
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor("#16191b"))
        ref = str(self.panel.params().get("sample") or "")
        if ref != self._key:
            self._key = ref
            a = I.sample_data(ref)
            m = a.mean(axis=1)
            n = max(1, len(m) // max(1, w))
            k = len(m) // n
            r = m[:k * n].reshape(k, n) if k else np.zeros((1, 1))
            self._pk = (r.min(axis=1), r.max(axis=1), len(m) / D.SR)
        mn, mx, dur = self._pk
        p.setPen(QPen(U.GRN, 1))
        for x in range(min(w, len(mn))):
            p.drawLine(QPointF(x, h / 2 - mx[x] * h * 0.45), QPointF(x, h / 2 - mn[x] * h * 0.45))
        st = float(self.panel.params().get("start", 0))
        if st > 0:
            p.fillRect(QRectF(0, 0, st * w, h), QColor(0, 0, 0, 120))
        p.setPen(U.DIM)
        p.setFont(U.font(10))
        name = ref[4:] if ref.startswith("kit:") else os.path.basename(ref)
        p.drawText(QRectF(6, 2, w - 12, 14), Qt.AlignmentFlag.AlignLeft, f"{name} — {dur:.2f} с")
        p.end()


class MiniKeys(QWidget):
    """Клавиатура для пробы инструмента (две октавы от C3; щелчок — нота)."""

    def __init__(self, panel, parent=None):
        super().__init__(parent)
        self.panel = panel
        self.setFixedHeight(46)
        self.base = 48
        self.down = None

    def _pitch(self, x, y):
        wk = [0, 2, 4, 5, 7, 9, 11]
        n_white = 14
        ww = self.width() / n_white
        if y < self.height() * 0.6:
            for o in range(2):
                for i, bk in enumerate((1, 3, 6, 8, 10)):
                    pos = [1, 2, 4, 5, 6][i] + o * 7
                    bx = pos * ww - ww * 0.32
                    if bx <= x <= bx + ww * 0.64:
                        return self.base + o * 12 + bk
        wi = int(x // ww)
        o, k = divmod(wi, 7)
        return self.base + o * 12 + wk[min(6, k)]

    def mousePressEvent(self, e):
        pt = self._pitch(e.position().x(), e.position().y())
        self.down = pt
        self.panel.audition(pt)
        self.update()

    def mouseReleaseEvent(self, e):
        self.down = None
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        w, h = self.width(), self.height()
        ww = w / 14
        for i in range(14):
            o, k = divmod(i, 7)
            pitch = self.base + o * 12 + [0, 2, 4, 5, 7, 9, 11][k]
            r = QRectF(i * ww, 0, ww - 1, h)
            p.fillRect(r, QColor("#f0a33a") if pitch == self.down else QColor("#e9ecee"))
            if k == 0:
                p.setPen(QColor("#555"))
                p.setFont(U.font(9))
                p.drawText(r.adjusted(0, 0, 0, -2), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
                           D.note_name(pitch))
        for o in range(2):
            for i, bk in enumerate((1, 3, 6, 8, 10)):
                pos = [1, 2, 4, 5, 6][i] + o * 7
                pitch = self.base + o * 12 + bk
                r = QRectF(pos * ww - ww * 0.32, 0, ww * 0.64, h * 0.6)
                p.fillRect(r, QColor("#f0a33a") if pitch == self.down else QColor("#22272b"))
        p.end()


# ------------------------------------------------------------------ #
#  Панель плагина                                                     #
# ------------------------------------------------------------------ #

class PluginPanel(QWidget):
    """kind='fx' (insert, slot) или 'gen' (id канала)."""

    def __init__(self, shell, kind, ref, parent=None):
        super().__init__(parent)
        self.sh = shell
        self.kind = kind
        self.ref = ref
        self.widgets = []
        self.knobs = []
        self.extras = []
        self.setStyleSheet("background: #2a3035;")
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(8, 6, 8, 8)
        self.v.setSpacing(6)
        self.build()

    # ── данные ── #
    def slot(self):
        if self.kind != "fx":
            return None
        i, s = self.ref
        try:
            return self.sh.p["mixer"][i]["fx"][s]
        except (IndexError, KeyError):
            return None

    def chan(self):
        return PR.chan(self.sh.p, self.ref) if self.kind == "gen" else None

    def params(self):
        if self.kind == "fx":
            sl = self.slot()
            return sl["params"] if sl else {}
        c = self.chan()
        return c.setdefault("params", {}) if c else {}

    def cls(self):
        if self.kind == "fx":
            return FX.FX_TYPES.get((self.slot() or {}).get("type"))
        return I.GENERATORS.get((self.chan() or {}).get("gen"))

    def fx_obj(self):
        if self.kind != "fx":
            return None
        return self.sh.eng.mixer.fx_of(*self.ref)

    def title(self):
        cls = self.cls()
        if self.kind == "fx":
            i, s = self.ref
            return f"{cls.NAME if cls else 'Эффект'} — {'мастер' if i == 0 else 'дорожка ' + str(i)}, ячейка {s + 1}"
        c = self.chan()
        return f"{(c or {}).get('name', '?')} — {cls.NAME if cls else ''}"

    def valid(self):
        return self.cls() is not None

    # ── события ── #
    def commit(self):
        self.sh.commit("Параметр плагина")

    def param_changed(self):
        if self.kind == "gen":
            self.sh.touch(rebuild=True, views=())          # ноты пересчитаются (с задержкой)
        for x in self.extras:
            if isinstance(x, (EqGraph, SampleView)):
                x.update()

    def audition(self, pitch):
        c = self.chan()
        if c is not None:
            self.sh.preview_channel(c, pitch, 0.85, 1.0)

    def sync_knobs(self):
        for w in self.knobs:
            if isinstance(w, U.Knob):
                w.update()
            elif isinstance(w, QComboBox):
                key = w.property("pkey")
                w.blockSignals(True)
                w.setCurrentIndex(int(self.params().get(key, 0)))
                w.blockSignals(False)
            elif isinstance(w, QCheckBox):
                key = w.property("pkey")
                w.blockSignals(True)
                w.setChecked(bool(self.params().get(key, 0)))
                w.blockSignals(False)
        for x in self.extras:
            x.update()

    def tick(self):
        for x in self.extras:
            if isinstance(x, GrMeter):
                x.tick()

    # ── сборка ── #
    def build(self):
        cls = self.cls()
        if cls is None:
            return
        head = QHBoxLayout()
        head.setSpacing(6)
        info = QLabel(cls.INFO)
        info.setWordWrap(True)
        info.setStyleSheet("color: #9aa4ab; font-size: 11px;")
        head.addWidget(info, 1)
        presets = FX.FX_PRESETS.get(cls.TYPE) if self.kind == "fx" else cls.PRESETS
        if presets:
            cb = QComboBox()
            cb.addItem("Пресет…")
            for nm in presets:
                cb.addItem(nm)
            cb.activated.connect(lambda i, presets=presets: self._preset(list(presets.items())[i - 1]) if i > 0 else None)
            head.addWidget(cb)
        if self.kind == "fx":
            led = U.Led("Эффект включён")
            led.setChecked(bool(self.slot().get("on", True)))
            led.toggled.connect(self._bypass)
            head.addWidget(led)
        self.v.addLayout(head)
        bank_button=U.IconButton('save','Сохранить и загрузить собственные настройки этого инструмента или эффекта',24,text='Мои пресеты…')
        bank_button.clicked.connect(lambda:__import__('studio_picker').bank(self))
        self.v.addWidget(bank_button)
        t = getattr(cls, "TYPE", None) or getattr(cls, "KIND", "")
        if self.kind == "fx" and t == "eq":
            g = EqGraph(self)
            self.extras.append(g)
            self.v.addWidget(g)
        elif self.kind == "fx" and t in ("comp", "limiter", "deesser", "pump"):
            g = GrMeter(self, kind="gr")
            self.extras.append(g)
            self.v.addWidget(g)
        elif self.kind == "fx" and t == "autotune":
            g = GrMeter(self, kind="tune")
            self.extras.append(g)
            self.v.addWidget(g)
        elif self.kind == "fx" and t == "analyzer":
            g = GrMeter(self, kind="scope")
            self.extras.append(g)
            self.v.addWidget(g, 1)
        if self.kind == "gen" and t in ("sampler", "layers"):
            sv = SampleView(self)
            self.extras.append(sv)
            self.v.addWidget(sv)
            row = QHBoxLayout()
            kit = QComboBox()
            kit.addItem("Звук кита…")
            names = []
            for cat, items in I.KIT:
                for nm, _f in items:
                    kit.addItem(f"{cat}: {nm}")
                    names.append(nm)
            kit.activated.connect(lambda i, names=names: self._sample("kit:" + names[i - 1], names[i - 1]) if i > 0 else None)
            row.addWidget(kit, 1)
            b = U.IconButton("open", "Взять звук из файла", 24, text="Файл…")
            b.clicked.connect(self._sample_file)
            row.addWidget(b)
            self.v.addLayout(row)
        # ручки
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.Shape.NoFrame)
        inner = QWidget()
        grid = QGridLayout(inner)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        cols = 5
        k = 0
        P = self.params()
        for spec in cls.PARAMS:
            cell = QVBoxLayout()
            cell.setSpacing(2)
            lab = QLabel(spec.name)
            lab.setStyleSheet("color: #aab3b9; font-size: 10px;")
            lab.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            lab.setWordWrap(True)
            lab.setFixedWidth(96)
            if spec.kind == "choice":
                w = QComboBox()
                w.setFixedWidth(100)
                for ch in spec.choices:
                    w.addItem(str(ch))
                w.setCurrentIndex(int(P.get(spec.key, spec.default)))
                w.setProperty("pkey", spec.key)
                w.activated.connect(lambda i, key=spec.key: self._set_choice(key, i))
            elif spec.kind == "bool":
                w = QCheckBox("вкл")
                w.setChecked(bool(P.get(spec.key, spec.default)))
                w.setProperty("pkey", spec.key)
                w.toggled.connect(lambda on, key=spec.key: self._set_choice(key, 1 if on else 0))
            else:
                w = U.Knob(spec, lambda key=spec.key, d=spec.default: self.params().get(key, d),
                           lambda v, key=spec.key: self._set(key, v), 36, U.ACC)
                w.pressed_.connect(self.commit)
                w.released_.connect(self._released)
            self.knobs.append(w)
            cell.addWidget(lab)
            cell.addWidget(w, 0, Qt.AlignmentFlag.AlignHCenter)
            box = QWidget()
            box.setLayout(cell)
            grid.addWidget(box, k // cols, k % cols, Qt.AlignmentFlag.AlignTop)
            k += 1
        grid.setRowStretch(k // cols + 1, 1)
        area.setWidget(inner)
        self.v.addWidget(area, 1)
        if self.kind == "gen":
            mk = MiniKeys(self)
            self.v.addWidget(mk)

    def _set(self, key, v):
        self.params()[key] = round(float(v), 5)
        self.param_changed()

    def _released(self):
        for x in self.extras:
            x.update()

    def _set_choice(self, key, i):
        self.commit()
        self.params()[key] = int(i)
        self.param_changed()
        for x in self.extras:
            x.update()

    def _preset(self, item):
        nm, prm = item
        self.commit()
        P = self.params()
        cls = self.cls()
        keep = {k: v for k, v in P.items() if k == "sample"}
        P.clear()
        P.update({s.key: s.default for s in cls.PARAMS})
        P.update(keep)
        P.update(prm)
        self.sync_knobs()
        self.param_changed()
        if self.kind == "gen":
            c = self.chan()
            if c is not None and (c["name"] in cls.PRESETS or c["name"] == cls.NAME):
                c["name"] = nm
                self.sh.touch(rebuild=True, views=("rack",))
        U.hint_to(self, f"Пресет: {nm}")

    def _bypass(self, on):
        sl = self.slot()
        if sl is not None and bool(sl.get("on", True)) != on:
            self.commit()
            sl["on"] = on
            self.sh.touch(rebuild=False, fx=True, views=("mixer",))

    def _sample(self, ref, name):
        c = self.chan()
        if c is None:
            return
        if c.get('gen') == 'layers':
            self.commit();self.params()['sample']=ref
            self.param_changed()
        else:
            self.sh.set_sample(c["id"], ref, name)
        for x in self.extras:
            x.update()

    def _sample_file(self):
        f, _ = QFileDialog.getOpenFileName(self, "Звук для сэмплера", "", "Звук (*.wav *.mp3 *.flac *.ogg *.m4a *.aiff)")
        if f:
            self._sample(f, os.path.splitext(os.path.basename(f))[0][:30])
