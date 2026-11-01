# daw_rack.py
"""
Стойка каналов студии: у каждого канала — включатель, панорама, громкость, дорожка микшера,
имя (щелчок — окно инструмента, правая кнопка — меню) и шаги паттерна по 1/16. Если у канала
в паттерне есть мелодия (не только шаги) — вместо шагов мини-превью нот, щелчок — пианоролл.
Сверху — выбор паттерна, число шагов и свинг.
"""
from __future__ import annotations

import copy

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (QColorDialog, QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QMenu,
                             QScrollArea, QVBoxLayout, QWidget)

import daw_fx as FX
import daw_inst as I
import daw_project as PR
import daw_ui as U

ROW_H = 30
STEP_W = 20


def _vol_spec():
    return FX.P("vol", "Громкость канала", 0, 1.25, 0.78, "%")


def _pan_spec():
    return FX.P("pan", "Панорама канала", -1, 1, 0.0, "")


def is_steps_only(pat, cid, root) -> bool:
    for n in PR.notes_of(pat, cid):
        q = n[0] * 4
        if abs(q - round(q)) > 1e-6 or abs(n[1] - 0.25) > 1e-6 or int(n[2]) != int(root):
            return False
    return True


class StepStrip(QWidget):
    """Шаги одного канала (или мини-превью нот)."""

    def __init__(self, rack, cid, parent=None):
        super().__init__(parent)
        self.rack, self.cid = rack, cid
        self.setFixedHeight(ROW_H - 6)
        self._paint_state = None
        self._last_step = None
        self.setMouseTracking(True)

    @property
    def sh(self):
        return self.rack.sh

    def steps(self):
        return self.rack.n_steps()

    def _step_at(self, x):
        s = int(x // STEP_W)
        return s if 0 <= s < self.steps() else None

    def mode_roll(self):
        c = PR.chan(self.sh.p, self.cid)
        pat = PR.cur_pattern(self.sh.p)
        return c is not None and pat is not None and not is_steps_only(pat, self.cid, c.get("root", 60))

    def enterEvent(self, e):
        U.hint_to(self, "Шаги: щелчок — включить, тянуть — рисовать, правая кнопка — стереть" if not self.mode_roll()
                  else "В паттерне мелодия — щелчок откроет пианоролл")

    def mousePressEvent(self, e):
        c = PR.chan(self.sh.p, self.cid)
        pat = PR.cur_pattern(self.sh.p)
        if c is None or pat is None:
            return
        if c.get("gen") == "audio":
            U.hint_to(self, "Аудиоканал играет клипы из плейлиста — шагов у него нет")
            return
        if self.mode_roll():
            self.sh.select_channel(self.cid)
            self.sh.open_roll(self.cid)
            return
        st = self._step_at(e.position().x())
        if st is None:
            return
        self.sh.commit("Шаги")
        if e.button() == Qt.MouseButton.RightButton:
            self._paint_state = False
        else:
            on = st in PR.steps_of(pat, self.cid)
            self._paint_state = not on
        self._apply(st)

    def _apply(self, st):
        c = PR.chan(self.sh.p, self.cid)
        pat = PR.cur_pattern(self.sh.p)
        if c is None or pat is None or st is None:
            return
        need_len = (st // 16 + 1) * 4.0
        if pat.get("len", 4.0) < need_len:
            pat["len"] = need_len
        was = st in PR.steps_of(pat, self.cid)
        if was != self._paint_state:
            PR.toggle_step(pat, self.cid, st, c.get("root", 60), force=self._paint_state)
            if self._paint_state:
                self.sh.preview_channel(c)
            self.sh.touch(views=("rack", "roll", "playlist"))
        self._last_step = st
        self.update()

    def mouseMoveEvent(self, e):
        if self._paint_state is None:
            return
        st = self._step_at(e.position().x())
        if st is not None and st != self._last_step:
            self._apply(st)

    def mouseReleaseEvent(self, e):
        self._paint_state = None
        self._last_step = None

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        sh = self.sh
        c = PR.chan(sh.p, self.cid)
        pat = PR.cur_pattern(sh.p)
        if c is None or pat is None:
            return
        n = self.steps()
        h = self.height()
        col = QColor(c.get("color") or "#6c9ad8")
        if c.get("gen") == "audio":
            p.setPen(U.DIM)
            p.setFont(U.font(11))
            p.drawText(QRectF(4, 0, n * STEP_W, h), Qt.AlignmentFlag.AlignVCenter,
                       "аудиоканал — клипы в плейлисте")
            return
        if self.mode_roll():
            r = QRectF(1, 1, n * STEP_W - 2, h - 2)
            p.setBrush(QColor("#22282c"))
            p.setPen(QPen(QColor("#3a4248"), 1))
            p.drawRoundedRect(r, 3, 3)
            ns = PR.notes_of(pat, self.cid)
            if ns:
                lo = min(x[2] for x in ns)
                hi = max(x[2] for x in ns)
                span = max(6, hi - lo + 1)
                beats = n / 4.0
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(col.lighter(120))
                for t, ln, pitch, _v in ns:
                    if t >= beats:
                        continue
                    x = 2 + t / beats * (r.width() - 2)
                    w = max(2.0, ln / beats * (r.width() - 2))
                    y = r.bottom() - 3 - (pitch - lo + 0.5) / span * (r.height() - 6)
                    p.drawRect(QRectF(x, y - 1.5, w, 3))
            self._playhead(p, n, h)
            return
        on = PR.steps_of(pat, self.cid)
        cur = sh.cur_step()
        for s in range(n):
            x = s * STEP_W
            r = QRectF(x + 1.5, 1.5, STEP_W - 3, h - 3)
            grp = (s // 4) % 2
            if s in on:
                p.setBrush(QColor("#eef1f3") if not c.get("mute") else QColor("#8f979e"))
                p.setPen(QPen(col, 1.5))
            else:
                p.setBrush(U.STEP_A if grp == 0 else U.STEP_B)
                p.setPen(QPen(QColor("#2a2f33"), 1))
            p.drawRoundedRect(r, 2.5, 2.5)
            if cur is not None and s == cur % n:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(U.ACC, 2))
                p.drawRoundedRect(r.adjusted(-0.5, -0.5, 0.5, 0.5), 3, 3)
        p.end()

    def _playhead(self, p, n, h):
        cur = self.sh.cur_step()
        if cur is None:
            return
        x = (cur % n) * STEP_W + STEP_W / 2
        p.setPen(QPen(U.ACC, 1.5))
        p.drawLine(QPointF(x, 1), QPointF(x, h - 1))


class NameButton(QWidget):
    """Кнопка с именем канала: щелчок — окно инструмента, правая — меню, цвет — полоска слева."""

    def __init__(self, rack, cid, parent=None):
        super().__init__(parent)
        self.rack, self.cid = rack, cid
        self.setFixedSize(128, ROW_H - 6)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.hover = False

    def enterEvent(self, e):
        self.hover = True
        self.update()
        c = PR.chan(self.rack.sh.p, self.cid)
        if c:
            g = I.GENERATORS.get(c.get("gen"))
            U.hint_to(self, f"{c['name']} — {g.NAME if g else 'аудиоканал'}: щелчок — открыть, правая кнопка — меню")

    def leaveEvent(self, e):
        self.hover = False
        self.update()

    def mousePressEvent(self, e):
        sh = self.rack.sh
        if e.button() == Qt.MouseButton.LeftButton:
            sh.select_channel(self.cid)
            sh.open_channel_plugin(self.cid, toggle=True)
        elif e.button() == Qt.MouseButton.RightButton:
            sh.select_channel(self.cid)
            self.rack.channel_menu(self.cid, e.globalPosition().toPoint())

    def mouseDoubleClickEvent(self, e):
        self.rack.rename(self.cid)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = PR.chan(self.rack.sh.p, self.cid)
        if c is None:
            return
        sel = self.rack.sh.p.get("sel_channel") == self.cid
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        base = QColor("#4d565e") if self.hover else QColor("#424a51")
        if sel:
            base = QColor("#59636b")
        p.setBrush(base)
        p.setPen(QPen(QColor("#22272b"), 1))
        p.drawRoundedRect(r, 3, 3)
        col = QColor(c.get("color") or "#6c9ad8")
        p.setBrush(col)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(2, 2, 5, r.height() - 3), 2, 2)
        p.setPen(U.TEXT if not c.get("mute") else U.DIM)
        p.setFont(U.font(12, sel))
        name = p.fontMetrics().elidedText(c["name"], Qt.TextElideMode.ElideRight, int(r.width() - 16))
        p.drawText(QRectF(11, 0, r.width() - 12, r.height()), Qt.AlignmentFlag.AlignVCenter, name)
        p.end()


class InsertBox(QWidget):
    """Номер дорожки микшера: щелчок — меню, колесо — соседняя."""

    def __init__(self, rack, cid, parent=None):
        super().__init__(parent)
        self.rack, self.cid = rack, cid
        self.setFixedSize(26, 18)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def enterEvent(self, e):
        c = PR.chan(self.rack.sh.p, self.cid)
        if c:
            i = int(c.get("insert", 0))
            U.hint_to(self, f"Дорожка микшера: {'мастер' if i == 0 else i} ({self.rack.sh.p['mixer'][i]['name']})")

    def wheelEvent(self, e):
        c = PR.chan(self.rack.sh.p, self.cid)
        if c is None:
            return
        d = 1 if e.angleDelta().y() > 0 else -1
        self.rack.set_insert(self.cid, max(0, min(PR.N_INSERTS, int(c.get("insert", 0)) + d)))

    def mousePressEvent(self, e):
        self.rack.insert_menu(self.cid, e.globalPosition().toPoint())

    def paintEvent(self, e):
        p = QPainter(self)
        c = PR.chan(self.rack.sh.p, self.cid)
        if c is None:
            return
        i = int(c.get("insert", 0))
        p.fillRect(self.rect(), QColor("#16191b"))
        p.setPen(QColor("#3e464c"))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        p.setPen(U.ACC if i else U.DIM)
        p.setFont(U.font(11, True, mono=True))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "М" if i == 0 else str(i))
        p.end()


class ChannelRow(QWidget):
    def __init__(self, rack, cid, parent=None):
        super().__init__(parent)
        self.rack, self.cid = rack, cid
        sh = rack.sh
        self.setFixedHeight(ROW_H)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(4)
        self.led = U.Led("Канал включён (выкл — заглушён)")
        self.led.setChecked(not (PR.chan(sh.p, cid) or {}).get("mute"))
        self.led.toggled.connect(self._mute)
        lay.addWidget(self.led)
        self.pan = U.Knob(_pan_spec(), lambda: (PR.chan(sh.p, self.cid) or {}).get("pan", 0.0),
                          lambda v: self._set("pan", v), 22, U.BLUE)
        self.vol = U.Knob(_vol_spec(), lambda: (PR.chan(sh.p, self.cid) or {}).get("vol", 0.78),
                          lambda v: self._set("vol", v), 22, U.ACC)
        for k in (self.pan, self.vol):
            k.pressed_.connect(lambda: sh.commit("Канал"))
            lay.addWidget(k)
        self.ins = InsertBox(rack, cid)
        lay.addWidget(self.ins)
        self.name = NameButton(rack, cid)
        lay.addWidget(self.name)
        self.strip = StepStrip(rack, cid)
        lay.addWidget(self.strip, 1)
        self.sel = QWidget()
        self.sel.setFixedSize(6, ROW_H - 10)
        lay.addWidget(self.sel)

    def _set(self, k, v):
        c = PR.chan(self.rack.sh.p, self.cid)
        if c is not None:
            c[k] = float(v)

    def _mute(self, on):
        c = PR.chan(self.rack.sh.p, self.cid)
        if c is not None and c.get("mute") == on:
            self.rack.sh.commit("Заглушить канал")
            c["mute"] = not on
            self.name.update()
            self.strip.update()

    def refresh(self):
        c = PR.chan(self.rack.sh.p, self.cid)
        if c is None:
            return
        self.led.blockSignals(True)
        self.led.setChecked(not c.get("mute"))
        self.led.blockSignals(False)
        sel = self.rack.sh.p.get("sel_channel") == self.cid
        self.sel.setStyleSheet(f"background: {'#9bd15c' if sel else '#2a3035'}; border-radius: 2px;")
        n = self.rack.n_steps()
        self.strip.setFixedWidth(n * STEP_W)
        for w in (self.pan, self.vol, self.ins, self.name, self.strip):
            w.update()


class ChannelRack(QWidget):
    """Содержимое окна «Стойка каналов»."""

    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self.sh = shell
        self.rows: dict = {}
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.inner = QWidget()
        self.inner.setStyleSheet("background: #2a3035;")
        self.vl = QVBoxLayout(self.inner)
        self.vl.setContentsMargins(2, 4, 2, 4)
        self.vl.setSpacing(0)
        self.scroll.setWidget(self.inner)
        v.addWidget(self.scroll, 1)
        # нижняя кнопка «+»
        self.add_btn = U.IconButton("plus", "Добавить канал: звук кита, инструмент, аудиоканал или файл", 24,
                                    text="Канал")
        self.add_btn.clicked.connect(lambda: self.add_menu(self.add_btn.mapToGlobal(self.add_btn.rect().bottomLeft())))
        self.toolbar = self._make_toolbar()

    # ── панель окна ── #
    def _make_toolbar(self):
        tb = U.Toolbar()
        self.pat_cb = QComboBox()
        self.pat_cb.setMinimumWidth(150)
        U.tip(self.pat_cb, "Паттерн, который редактируется и играет в режиме «Паттерн»")
        self.pat_cb.activated.connect(self._pick_pattern)
        tb.add(self.pat_cb)
        b = U.IconButton("plus", "Новый паттерн", 22)
        b.clicked.connect(self.sh.new_pattern)
        tb.add(b)
        b = U.IconButton("new", "Копия паттерна", 22)
        b.clicked.connect(self.sh.clone_pattern)
        tb.add(b)
        tb.space(8)
        tb.add(U.label("Шагов"))
        self.steps_cb = QComboBox()
        for n in (16, 32, 48, 64):
            self.steps_cb.addItem(str(n), n)
        self.steps_cb.activated.connect(self._pick_steps)
        tb.add(self.steps_cb)
        tb.space(8)
        self.swing = U.Knob(FX.P("swing", "Свинг", 0, 1, 0, "%"), lambda: self.sh.p.get("swing", 0.0),
                            self._set_swing, 22, U.GRN)
        self.swing.pressed_.connect(lambda: self.sh.commit("Свинг"))
        tb.add(U.label("Свинг"))
        tb.add(self.swing)
        tb.stretch()
        tb.add(self.add_btn)
        return tb

    def _set_swing(self, v):
        self.sh.p["swing"] = float(v)
        self.sh.touch(views=())

    def _pick_pattern(self, i):
        pid = self.pat_cb.itemData(i)
        if pid:
            self.sh.select_pattern(pid)

    def _pick_steps(self, i):
        n = self.steps_cb.itemData(i)
        pat = PR.cur_pattern(self.sh.p)
        if pat is None:
            return
        self.sh.commit("Длина паттерна")
        pat["len"] = n / 4.0
        # ноты дальше новой длины — убрать
        for cid, ns in list(pat.get("notes", {}).items()):
            keep = [x for x in ns if x[0] < pat["len"] - 1e-9]
            if keep:
                pat["notes"][cid] = keep
            else:
                pat["notes"].pop(cid, None)
        self.sh.touch(views=("rack", "roll", "playlist"))

    def n_steps(self):
        pat = PR.cur_pattern(self.sh.p)
        if pat is None:
            return 16
        return int(max(16, PR.pattern_used_len(pat) * 4))

    # ── строки ── #
    def rebuild(self):
        while self.vl.count():
            it = self.vl.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        self.rows = {}
        for c in self.sh.p["channels"]:
            r = ChannelRow(self, c["id"])
            self.rows[c["id"]] = r
            self.vl.addWidget(r)
        self.vl.addStretch(1)
        self.refresh()

    def refresh(self):
        ids = [c["id"] for c in self.sh.p["channels"]]
        if ids != list(self.rows.keys()):
            self.rebuild()
            return
        for r in self.rows.values():
            r.refresh()
        self.pat_cb.blockSignals(True)
        self.pat_cb.clear()
        for pt in self.sh.p["patterns"]:
            self.pat_cb.addItem(pt["name"], pt["id"])
        cur = PR.cur_pattern(self.sh.p)
        if cur:
            self.pat_cb.setCurrentIndex(max(0, self.pat_cb.findData(cur["id"])))
        self.pat_cb.blockSignals(False)
        n = self.n_steps()
        self.steps_cb.blockSignals(True)
        i = self.steps_cb.findData(n)
        if i < 0:
            self.steps_cb.addItem(str(n), n)
            i = self.steps_cb.findData(n)
        self.steps_cb.setCurrentIndex(i)
        self.steps_cb.blockSignals(False)
        self.swing.update()

    def tick(self):
        for r in self.rows.values():
            r.strip.update()

    # ── действия ── #
    def set_insert(self, cid, i):
        c = PR.chan(self.sh.p, cid)
        if c is None:
            return
        self.sh.commit("Дорожка микшера")
        c["insert"] = int(i)
        self.sh.touch(views=("rack", "mixer"))

    def insert_menu(self, cid, gp):
        c = PR.chan(self.sh.p, cid)
        if c is None:
            return
        m = QMenu(self)
        a = m.addAction("Свободная дорожка")
        a.triggered.connect(lambda: self.set_insert(cid, PR.free_insert(self.sh.p)))
        m.addSeparator()
        for i in range(PR.N_INSERTS + 1):
            nm = "Мастер" if i == 0 else f"{i}. {self.sh.p['mixer'][i]['name']}"
            a = m.addAction(("•  " if int(c.get("insert", 0)) == i else "    ") + nm)
            a.triggered.connect(lambda _=False, i=i: self.set_insert(cid, i))
        m.exec(gp)

    def rename(self, cid):
        c = PR.chan(self.sh.p, cid)
        if c is None:
            return
        t, ok = QInputDialog.getText(self, "Канал", "Название канала:", text=c["name"])
        if ok and t.strip():
            self.sh.commit("Переименовать канал")
            c["name"] = t.strip()[:40]
            self.sh.touch(rebuild=False, views=("rack", "mixer", "roll"))

    def channel_menu(self, cid, gp):
        sh = self.sh
        c = PR.chan(sh.p, cid)
        if c is None:
            return
        m = QMenu(self)
        if c.get("gen") != "audio":
            m.addAction("Открыть инструмент", lambda: sh.open_channel_plugin(cid))
            m.addAction("Пианоролл", lambda: sh.open_roll(cid))
        m.addAction("Переименовать…", lambda: self.rename(cid))
        cm = m.addMenu("Цвет")
        for col in I.DEFAULT_COLORS:
            a = cm.addAction("    " + col)
            a.triggered.connect(lambda _=False, col=col: self._color(cid, col))
        cm.addAction("Другой…", lambda: self._color_dialog(cid))
        im = m.addMenu("Дорожка микшера")
        im.addAction("Свободная дорожка", lambda: self.set_insert(cid, PR.free_insert(sh.p)))
        for i in range(PR.N_INSERTS + 1):
            nm = "Мастер" if i == 0 else f"{i}. {sh.p['mixer'][i]['name']}"
            a = im.addAction(("•  " if int(c.get("insert", 0)) == i else "    ") + nm)
            a.triggered.connect(lambda _=False, i=i: self.set_insert(cid, i))
        if c.get("gen") != "audio":
            g = I.GENERATORS.get(c.get("gen"))
            if g is not None and g.PRESETS:
                pm = m.addMenu("Пресет")
                for nm, prm in g.PRESETS.items():
                    pm.addAction(nm, lambda prm=prm, nm=nm: sh.apply_gen_preset(cid, prm, nm))
            if c.get("gen") == "sampler":
                km = m.addMenu("Звук кита")
                for cat, items in I.KIT:
                    sm = km.addMenu(cat)
                    for nm, _f in items:
                        sm.addAction(nm, lambda nm=nm: sh.set_sample(cid, "kit:" + nm, nm))
                m.addAction("Звук из файла…", lambda: self._sample_file(cid))
            rm = m.addMenu("Заменить инструмент")
            for kind, cls in I.GENERATORS.items():
                rm.addAction(cls.NAME, lambda kind=kind: sh.replace_gen(cid, kind))
            fm = m.addMenu("Заполнить шаги")
            for every in (1, 2, 4, 8):
                fm.addAction(f"Каждый {every}-й шаг", lambda every=every: self._fill(cid, every))
            m.addAction("Очистить ноты в паттерне", lambda: self._clear(cid))
        m.addSeparator()
        m.addAction("Клонировать", lambda: sh.clone_channel(cid))
        m.addAction("Выше", lambda: self._move(cid, -1))
        m.addAction("Ниже", lambda: self._move(cid, 1))
        m.addSeparator()
        m.addAction("Удалить канал", lambda: sh.delete_channel(cid))
        m.exec(gp)

    def _color(self, cid, col):
        c = PR.chan(self.sh.p, cid)
        if c:
            self.sh.commit("Цвет канала")
            c["color"] = col
            self.sh.touch(rebuild=False, views=("rack", "playlist", "roll"))

    def _color_dialog(self, cid):
        c = PR.chan(self.sh.p, cid)
        if not c:
            return
        col = QColorDialog.getColor(QColor(c.get("color") or "#6c9ad8"), self, "Цвет канала")
        if col.isValid():
            self._color(cid, col.name())

    def _sample_file(self, cid):
        f, _ = QFileDialog.getOpenFileName(self, "Звук для сэмплера", "", "Звук (*.wav *.mp3 *.flac *.ogg *.m4a *.aiff)")
        if f:
            import os
            self.sh.set_sample(cid, f, os.path.splitext(os.path.basename(f))[0])

    def _fill(self, cid, every):
        c = PR.chan(self.sh.p, cid)
        pat = PR.cur_pattern(self.sh.p)
        if not c or not pat:
            return
        self.sh.commit("Заполнить шаги")
        for s in range(0, self.n_steps(), every):
            PR.toggle_step(pat, cid, s, c.get("root", 60), force=True)
        self.sh.touch(views=("rack", "roll", "playlist"))

    def _clear(self, cid):
        pat = PR.cur_pattern(self.sh.p)
        if pat and pat.get("notes", {}).get(cid):
            self.sh.commit("Очистить ноты")
            pat["notes"].pop(cid, None)
            self.sh.touch(views=("rack", "roll", "playlist"))

    def _move(self, cid, d):
        chs = self.sh.p["channels"]
        i = next((k for k, c in enumerate(chs) if c["id"] == cid), -1)
        j = i + d
        if i < 0 or not 0 <= j < len(chs):
            return
        self.sh.commit("Порядок каналов")
        chs[i], chs[j] = chs[j], chs[i]
        self.sh.touch(rebuild=False, views=("rack",))

    def add_menu(self, gp):
        sh = self.sh
        m = QMenu(self)
        km = m.addMenu("Звук кита (сэмплер)")
        for cat, items in I.KIT:
            sm = km.addMenu(cat)
            for nm, _f in items:
                sm.addAction(nm, lambda nm=nm: sh.add_channel("sampler", {"sample": "kit:" + nm}, nm))
        im = m.addMenu("Инструмент")
        for kind, cls in I.GENERATORS.items():
            if kind == "sampler":
                continue
            sm = im.addMenu(cls.NAME)
            sm.addAction("Чистый", lambda kind=kind, cls=cls: sh.add_channel(kind, {}, cls.NAME))
            for nm, prm in cls.PRESETS.items():
                if nm == "Init":
                    continue
                sm.addAction(nm, lambda kind=kind, prm=prm, nm=nm: sh.add_channel(kind, dict(prm), nm))
        m.addAction("Сэмплер из файла…", self._add_file_sampler)
        m.addAction("Аудиоканал (для клипов и записи)", lambda: sh.add_channel("audio", {}, "Аудио"))
        m.exec(gp)

    def _add_file_sampler(self):
        f, _ = QFileDialog.getOpenFileName(self, "Звук для сэмплера", "", "Звук (*.wav *.mp3 *.flac *.ogg *.m4a *.aiff)")
        if f:
            import os
            self.sh.add_channel("sampler", {"sample": f}, os.path.splitext(os.path.basename(f))[0][:30])
