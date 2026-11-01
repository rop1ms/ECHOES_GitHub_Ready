# daw_mixer.py
"""
Микшер студии: мастер и 20 дорожек — индикатор, фейдер, панорама, включатель, соло; справа —
цепочка из 10 эффектов выбранной дорожки (включатель, эффект, микс), пресеты голоса.
Щелчок по эффекту — окно плагина, правая кнопка — заменить / пресет / порядок / удалить.
"""
from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (QHBoxLayout, QInputDialog, QMenu, QScrollArea, QVBoxLayout, QWidget)

import daw_fx as FX
import daw_project as PR
import daw_ui as U

STRIP_W = 66


class Strip(QWidget):
    def __init__(self, mx, i, parent=None):
        super().__init__(parent)
        self.mx, self.i = mx, i
        sh = mx.sh
        self.setFixedWidth(STRIP_W if i else STRIP_W + 14)
        v = QVBoxLayout(self)
        v.setContentsMargins(4, 22, 4, 4)
        v.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(3)
        self.meter = U.Meter(None, 10, 150)
        self.fader = U.Fader(lambda: self.ins().get("vol", 1.0), lambda g: self._set("vol", g), None, 22, 150)
        self.fader.pressed_.connect(lambda: sh.commit("Громкость дорожки"))
        row.addStretch(1)
        row.addWidget(self.meter)
        row.addWidget(self.fader)
        row.addStretch(1)
        v.addLayout(row)
        self.pan = U.Knob(FX.P("pan", "Панорама дорожки", -1, 1, 0, ""), lambda: self.ins().get("pan", 0.0),
                          lambda x: self._set("pan", x), 26, U.BLUE)
        self.pan.pressed_.connect(lambda: sh.commit("Панорама дорожки"))
        h = QHBoxLayout()
        h.addStretch(1)
        h.addWidget(self.pan)
        h.addStretch(1)
        v.addLayout(h)
        h2 = QHBoxLayout()
        h2.setSpacing(2)
        self.led = U.Led("Дорожка включена")
        self.led.toggled.connect(self._mute)
        h2.addWidget(self.led)
        if i:
            self.solo = U.IconButton("headphones", "Соло: слышна только эта дорожка", 18, checkable=True, color=U.ACC)
            self.solo.toggled.connect(self._solo)
            h2.addWidget(self.solo)
        else:
            self.solo = None
        h2.addStretch(1)
        v.addLayout(h2)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def ins(self):
        return self.mx.sh.p["mixer"][self.i]

    def _set(self, k, v):
        self.ins()[k] = float(v)

    def _mute(self, on):
        if self.ins().get("mute") == on:
            self.mx.sh.commit("Заглушить дорожку")
            self.ins()["mute"] = not on
            self.update()

    def _solo(self, on):
        if bool(self.ins().get("solo")) != on:
            self.mx.sh.commit("Соло")
            self.ins()["solo"] = on

    def refresh(self):
        ins = self.ins()
        self.led.blockSignals(True)
        self.led.setChecked(not ins.get("mute"))
        self.led.blockSignals(False)
        if self.solo is not None:
            self.solo.blockSignals(True)
            self.solo.setChecked(bool(ins.get("solo")))
            self.solo.blockSignals(False)
        self.fader.update()
        self.pan.update()
        self.update()

    def mousePressEvent(self, e):
        self.mx.select(self.i)
        if e.button() == Qt.MouseButton.RightButton:
            self.mx.strip_menu(self.i, e.globalPosition().toPoint())

    def mouseDoubleClickEvent(self, e):
        if e.position().y() < 22:
            self.mx.rename(self.i)

    def enterEvent(self, e):
        n = sum(1 for s in self.ins()["fx"] if s)
        chans = [c["name"] for c in self.mx.sh.p["channels"] if int(c.get("insert", 0)) == self.i]
        U.hint_to(self, f"{'Мастер' if self.i == 0 else 'Дорожка ' + str(self.i)}: {self.ins()['name']}"
                        + (f" — каналы: {', '.join(chans)}" if chans else "") + (f", эффектов: {n}" if n else ""))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        sel = self.mx.cur == self.i
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setBrush(QColor("#3b434a") if sel else (QColor("#30373c") if self.i else QColor("#383f45")))
        p.setPen(QPen(U.ACC if sel else QColor("#22272b"), 1))
        p.drawRoundedRect(r, 3, 3)
        ins = self.ins()
        p.setPen(U.ACC if self.i == 0 else U.DIM)
        p.setFont(U.font(10, True))
        p.drawText(QRectF(4, 2, r.width() - 6, 10), Qt.AlignmentFlag.AlignLeft, "М" if self.i == 0 else str(self.i))
        n = sum(1 for s in ins["fx"] if s and s.get("on", True))
        if n:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(U.GRN)
            for k in range(min(n, 6)):
                p.drawEllipse(QRectF(r.right() - 6 - k * 5, 4, 3.5, 3.5))
        p.setPen(U.TEXT if not ins.get("mute") else U.DIM)
        p.setFont(U.font(10))
        p.drawText(QRectF(3, 10, r.width() - 4, 12), Qt.AlignmentFlag.AlignHCenter,
                   p.fontMetrics().elidedText(ins["name"], Qt.TextElideMode.ElideRight, int(r.width() - 6)))
        p.end()


class SlotRow(QWidget):
    def __init__(self, mx, s, parent=None):
        super().__init__(parent)
        self.mx, self.s = mx, s
        self.setFixedHeight(28)
        h = QHBoxLayout(self)
        h.setContentsMargins(2, 1, 2, 1)
        h.setSpacing(4)
        self.led = U.Led("Эффект включён")
        self.led.toggled.connect(self._on)
        h.addWidget(self.led)
        self.btn = SlotButton(self)
        h.addWidget(self.btn, 1)
        self.mix = U.Knob(FX.P("mix", "Микс эффекта", 0, 1, 1, "%"), self._get_mix, self._set_mix, 22, U.GRN)
        self.mix.pressed_.connect(lambda: mx.sh.commit("Микс эффекта"))
        h.addWidget(self.mix)

    def slot(self):
        return self.mx.sh.p["mixer"][self.mx.cur]["fx"][self.s]

    def _get_mix(self):
        sl = self.slot()
        return sl.get("mix", 1.0) if sl else 1.0

    def _set_mix(self, v):
        sl = self.slot()
        if sl:
            sl["mix"] = float(v)

    def _on(self, on):
        sl = self.slot()
        if sl and bool(sl.get("on", True)) != on:
            self.mx.sh.commit("Вкл/выкл эффект")
            sl["on"] = on
            self.mx.sh.touch(rebuild=False, fx=True, views=("mixer",))

    def refresh(self):
        sl = self.slot()
        self.led.blockSignals(True)
        self.led.setChecked(bool(sl and sl.get("on", True)))
        self.led.setEnabled(sl is not None)
        self.led.blockSignals(False)
        self.mix.setEnabled(sl is not None)
        self.mix.update()
        self.btn.update()


class SlotButton(QWidget):
    def __init__(self, row, parent=None):
        super().__init__(parent)
        self.row = row
        self.setFixedHeight(22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.hover = False

    def enterEvent(self, e):
        self.hover = True
        self.update()
        sl = self.row.slot()
        cls = FX.FX_TYPES.get((sl or {}).get("type"))
        U.hint_to(self, (cls.NAME + " — " + cls.INFO + " Правая кнопка — меню") if cls else
                  "Пустая ячейка: щелчок — выбрать эффект")

    def leaveEvent(self, e):
        self.hover = False
        self.update()

    def mousePressEvent(self, e):
        mx = self.row.mx
        sl = self.row.slot()
        gp = e.globalPosition().toPoint()
        if e.button() == Qt.MouseButton.RightButton or sl is None:
            mx.slot_menu(self.row.s, gp)
        else:
            mx.sh.open_fx_plugin(mx.cur, self.row.s, toggle=True)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        sl = self.row.slot()
        p.setBrush(QColor("#454e55") if self.hover else QColor("#3a4248") if sl else QColor("#2c3237"))
        p.setPen(QPen(QColor("#22272b"), 1))
        p.drawRoundedRect(r, 3, 3)
        cls = FX.FX_TYPES.get((sl or {}).get("type"))
        p.setFont(U.font(11, bool(cls)))
        p.setPen(U.TEXT if cls and sl.get("on", True) else U.DIM)
        txt = f"{self.row.s + 1}. " + (cls.NAME if cls else "— пусто —")
        p.drawText(r.adjusted(6, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(txt, Qt.TextElideMode.ElideRight, int(r.width() - 10)))
        p.end()


class MixerView(QWidget):
    """Содержимое окна «Микшер»."""

    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self.sh = shell
        self.cur = 1
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.scroll = QScrollArea()
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        inner = QWidget()
        inner.setStyleSheet("background: #262c30;")
        self.sl = QHBoxLayout(inner)
        self.sl.setContentsMargins(4, 4, 4, 4)
        self.sl.setSpacing(3)
        self.strips = []
        for i in range(PR.N_INSERTS + 1):
            s = Strip(self, i)
            self.strips.append(s)
            self.sl.addWidget(s)
            if i == 0:
                sep = QWidget()
                sep.setFixedWidth(6)
                self.sl.addWidget(sep)
        self.sl.addStretch(1)
        self.scroll.setWidget(inner)
        h.addWidget(self.scroll, 1)
        side = QWidget()
        side.setFixedWidth(250)
        side.setStyleSheet("background: #2c3237;")
        sv = QVBoxLayout(side)
        sv.setContentsMargins(6, 6, 6, 6)
        sv.setSpacing(3)
        self.title = U.label("", 12, True, U.TEXT)
        sv.addWidget(self.title)
        self.rows = []
        for s in range(10):
            r = SlotRow(self, s)
            self.rows.append(r)
            sv.addWidget(r)
        sv.addSpacing(6)
        b = U.IconButton("mic", "Пресет голоса: готовая цепочка эффектов для вокала", 26, text="Пресет голоса")
        b.clicked.connect(lambda: self.voice_menu(b.mapToGlobal(b.rect().bottomLeft())))
        sv.addWidget(b)
        b2 = U.IconButton("erase", "Убрать все эффекты с дорожки", 26, text="Очистить цепочку")
        b2.clicked.connect(self.clear_chain)
        sv.addWidget(b2)
        sv.addStretch(1)
        self.info = U.label("", 10)
        self.info.setWordWrap(True)
        sv.addWidget(self.info)
        h.addWidget(side)
        self.toolbar = None
        self.select(1)

    def select(self, i):
        self.cur = int(i)
        for s in self.strips:
            s.update()
        self.refresh_side()

    def refresh_side(self):
        ins = self.sh.p["mixer"][self.cur]
        self.title.setText(("Мастер" if self.cur == 0 else f"Дорожка {self.cur}") + f": {ins['name']}")
        for r in self.rows:
            r.refresh()
        chans = [c["name"] for c in self.sh.p["channels"] if int(c.get("insert", 0)) == self.cur]
        lat = self.sh.eng.mixer.lat[self.cur] if self.cur < len(self.sh.eng.mixer.lat) else 0
        self.info.setText(("Каналы: " + ", ".join(chans) if chans else "Каналов на дорожке нет") +
                          (f"\nЗадержка эффектов {lat / 44.1:.0f} мс — компенсируется" if lat else ""))

    def refresh(self):
        for s in self.strips:
            s.refresh()
        self.refresh_side()

    def tick(self):
        if not self.isVisible():
            return
        pk = self.sh.eng.mixer.peaks
        for s in self.strips:
            if s.i < len(pk):
                s.meter.set(pk[s.i][0], pk[s.i][1])

    # ── меню ── #
    def rename(self, i):
        ins = self.sh.p["mixer"][i]
        t, ok = QInputDialog.getText(self, "Дорожка микшера", "Название:", text=ins["name"])
        if ok and t.strip():
            self.sh.commit("Название дорожки")
            ins["name"] = t.strip()[:24]
            self.sh.touch(rebuild=False, views=("mixer", "rack"))

    def strip_menu(self, i, gp):
        m = QMenu(self)
        m.addAction("Переименовать…", lambda: self.rename(i))
        if i:
            m.addAction("Соло", lambda: self._solo_only(i))
        m.addAction("Сбросить громкость и панораму", lambda: self._reset(i))
        m.addAction("Очистить эффекты", lambda: (self.select(i), self.clear_chain()))
        m.exec(gp)

    def _solo_only(self, i):
        self.sh.commit("Соло")
        for k, ins in enumerate(self.sh.p["mixer"]):
            ins["solo"] = (k == i) and not ins.get("solo")
        self.refresh()

    def _reset(self, i):
        self.sh.commit("Сброс дорожки")
        ins = self.sh.p["mixer"][i]
        ins["vol"], ins["pan"] = 1.0, 0.0
        self.refresh()

    def slot_menu(self, s, gp):
        sh = self.sh
        ins = sh.p["mixer"][self.cur]
        sl = ins["fx"][s]
        m = QMenu(self)
        add = m if sl is None else m.addMenu("Заменить эффект")
        for grp in FX.FX_GROUPS:
            gm = add.addMenu(grp)
            for cls in FX.FX_CLASSES:
                if cls.GROUP == grp:
                    gm.addAction(cls.NAME, lambda t=cls.TYPE: self.set_fx(s, t))
        if sl is not None:
            pr = FX.FX_PRESETS.get(sl["type"])
            if pr:
                pm = m.addMenu("Пресет")
                for nm, prm in pr.items():
                    pm.addAction(nm, lambda prm=prm: self.preset(s, prm))
            m.addAction("Открыть окно", lambda: sh.open_fx_plugin(self.cur, s))
            m.addSeparator()
            m.addAction("Выше", lambda: self.move(s, -1))
            m.addAction("Ниже", lambda: self.move(s, 1))
            m.addAction("Удалить", lambda: self.set_fx(s, None))
        m.exec(gp)

    def set_fx(self, s, t):
        sh = self.sh
        sh.commit("Эффект")
        ins = sh.p["mixer"][self.cur]
        ins["fx"][s] = FX.new_slot(t) if t else None
        sh.close_fx_plugin(self.cur, s)
        sh.touch(rebuild=False, fx=True, views=("mixer",))
        if t:
            sh.open_fx_plugin(self.cur, s)

    def preset(self, s, prm):
        sl = self.sh.p["mixer"][self.cur]["fx"][s]
        if not sl:
            return
        self.sh.commit("Пресет эффекта")
        cls = FX.FX_TYPES[sl["type"]]
        sl["params"].clear()
        sl["params"].update({p.key: p.default for p in cls.PARAMS})
        sl["params"].update(prm)
        self.sh.touch(rebuild=False, fx=True, views=("mixer", "plugins"))

    def move(self, s, d):
        fx = self.sh.p["mixer"][self.cur]["fx"]
        j = s + d
        if not 0 <= j < len(fx):
            return
        self.sh.commit("Порядок эффектов")
        fx[s], fx[j] = fx[j], fx[s]
        self.sh.close_fx_plugin(self.cur, s)
        self.sh.close_fx_plugin(self.cur, j)
        self.sh.touch(rebuild=False, fx=True, views=("mixer",))

    def voice_menu(self, gp):
        m = QMenu(self)
        for nm, _chain in FX.VOICE_PRESETS:
            m.addAction(nm, lambda nm=nm: self.sh.apply_voice_preset(self.cur, nm))
        m.exec(gp)

    def clear_chain(self):
        ins = self.sh.p["mixer"][self.cur]
        if any(ins["fx"]):
            self.sh.commit("Очистить цепочку")
            for s in range(10):
                self.sh.close_fx_plugin(self.cur, s)
            ins["fx"] = [None] * 10
            self.sh.touch(rebuild=False, fx=True, views=("mixer",))
