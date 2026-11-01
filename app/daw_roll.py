# daw_roll.py
"""
Пианоролл студии: ноты канала в текущем паттерне.

Мышь: щелчок по пустому — нота (длина как у последней), тянуть ноту — двигать, тянуть правый
край — длина, правая кнопка — удалить (тянуть — стирать), Ctrl+тянуть — выделить рамкой,
Shift+тянуть ноту — копия, Alt — двигать без сетки, средняя кнопка — двигать вид,
Alt+Shift+колесо над нотой — сила нажатия. Клавиатура слева — послушать ноту. Внизу — сила нажатия.
Окно показывает паттерн, в котором лежат ноты выбранного канала (переключатель «Паттерн»).
Клавиши: Delete, Ctrl+A, Ctrl+C / Ctrl+V, Ctrl+D (повторить выделенное), стрелки вверх/вниз —
транспонировать (Shift — на октаву), влево/вправо — сдвиг по сетке, Q — квантизация.
Сверху: канал, инструменты, сетка, аккорд-штамп, подсветка лада, генераторы (аккорды, бас,
арпеджио, мелодия).
"""
from __future__ import annotations

import math
import random

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QComboBox, QMenu, QScrollBar, QWidget

import daw_dsp as D
import daw_fx as FX
import daw_project as PR
import daw_ui as U

KEYW = 58
RULER = 20
VEL = 70
SB = 11
SNAPS = [("1/1 такта", 4.0), ("1/2", 2.0), ("доля", 1.0), ("1/2 доли", 0.5), ("1/4 доли (шаг)", 0.25),
         ("1/8 доли", 0.125), ("триоли", 1 / 3), ("без сетки", 0.0)]
CHORDS = [("одна нота", []), ("мажор", [0, 4, 7]), ("минор", [0, 3, 7]), ("септаккорд 7", [0, 4, 7, 10]),
          ("мажорный 7", [0, 4, 7, 11]), ("минорный 7", [0, 3, 7, 10]), ("sus2", [0, 2, 7]), ("sus4", [0, 5, 7]),
          ("уменьшённый", [0, 3, 6]), ("увеличенный", [0, 4, 8]), ("квинта (power)", [0, 7, 12]),
          ("октава", [0, 12])]
BLACK = {1, 3, 6, 8, 10}


class RollCanvas(QWidget):
    def __init__(self, roll, parent=None):
        super().__init__(parent)
        self.roll = roll
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.ppb = 96.0                       # пикселей на долю
        self.kh = 14                          # высота клавиши
        self.sx = 0.0
        self.sy = (127 - 84) * self.kh        # сверху — до C6
        self.drag = None
        self._len_checked = False
        self._wheel_vel = False
        self.sel: list = []
        self.hover_note = None
        self.hover_key = None
        self.hover_cell = None                # (доля, высота) — где встанет новая нота
        self.box = None
        self.vbar = QScrollBar(Qt.Orientation.Vertical, self)
        self.hbar = QScrollBar(Qt.Orientation.Horizontal, self)
        self.vbar.valueChanged.connect(self._vs)
        self.hbar.valueChanged.connect(self._hs)
        self._sync_bars()
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self._dirty = True                    # сетка и ноты — готовой картинкой, поверх — только курсор
        self._layer = None

    def update(self, *a):
        self._dirty = True
        super().update(*a)

    def update_dynamic(self):
        super().update()

    @property
    def sh(self):
        return self.roll.sh

    # ── геометрия ── #
    def grid_rect(self):
        return QRectF(KEYW, RULER, max(10, self.width() - KEYW - SB), max(10, self.height() - RULER - VEL - SB))

    def bx(self, beat):
        return KEYW + beat * self.ppb - self.sx

    def xb(self, x):
        return (x - KEYW + self.sx) / self.ppb

    def py(self, pitch):
        return RULER + (127 - pitch) * self.kh - self.sy

    def yp(self, y):
        return 127 - int((y - RULER + self.sy) // self.kh)

    def snap(self):
        return self.roll.snap_val()

    def q(self, beat, mode="floor"):
        s = self.snap()
        if s <= 0:
            return max(0.0, beat)
        if mode == "round":
            return max(0.0, round(beat / s) * s)
        return max(0.0, math.floor(beat / s + 1e-9) * s)

    def notes(self):
        pat = PR.cur_pattern(self.sh.p)
        cid = self.roll.cid
        if pat is None or cid is None:
            return None, []
        return pat, pat.setdefault("notes", {}).setdefault(cid, [])

    def pat_len(self):
        pat = PR.cur_pattern(self.sh.p)
        return PR.pattern_used_len(pat) if pat else 4.0

    def _sync_bars(self):
        g = self.grid_rect()
        total_y = 128 * self.kh
        self.vbar.blockSignals(True)
        self.vbar.setRange(0, max(0, int(total_y - g.height())))
        self.vbar.setPageStep(int(g.height()))
        self.vbar.setValue(int(self.sy))
        self.vbar.blockSignals(False)
        beats = max(self.pat_len() + 8, 16)
        self.hbar.blockSignals(True)
        self.hbar.setRange(0, max(0, int(beats * self.ppb - g.width())))
        self.hbar.setPageStep(int(g.width()))
        self.hbar.setValue(int(self.sx))
        self.hbar.blockSignals(False)

    def _vs(self, v):
        self.sy = float(v)
        self.update()

    def _hs(self, v):
        self.sx = float(v)
        self.update()

    def resizeEvent(self, e):
        self.vbar.setGeometry(self.width() - SB, RULER, SB, self.height() - RULER - SB)
        self.hbar.setGeometry(KEYW, self.height() - SB, self.width() - KEYW - SB, SB)
        self._sync_bars()

    def center_on_notes(self):
        _pat, ns = self.notes()
        g = self.grid_rect()
        if ns:
            mid = (min(n[2] for n in ns) + max(n[2] for n in ns)) / 2
        else:
            c = PR.chan(self.sh.p, self.roll.cid)
            mid = (c or {}).get("root", 60)
        self.sy = max(0.0, min(128 * self.kh - g.height(), (127 - mid) * self.kh - g.height() / 2))
        self.sx = 0.0
        self._sync_bars()
        self.update()

    # ── попадание ── #
    def note_at(self, pos):
        _pat, ns = self.notes()
        x, y = pos.x(), pos.y()
        for n in reversed(ns):
            r = QRectF(self.bx(n[0]), self.py(n[2]), max(4.0, n[1] * self.ppb), self.kh)
            if r.contains(QPointF(x, y)):
                edge = x > r.right() - max(4.0, min(10.0, r.width() * 0.4))
                return n, edge
        return None, False

    # ── мышь ── #
    def mousePressEvent(self, e):
        self.setFocus()
        pos = e.position()
        g = self.grid_rect()
        pat, ns = self.notes()
        c = PR.chan(self.sh.p, self.roll.cid)
        if pat is None or c is None:
            return
        if e.button() == Qt.MouseButton.MiddleButton:
            self.drag = ("pan", pos, self.sx, self.sy)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        self.hover_cell = None
        if pos.x() < KEYW and RULER <= pos.y() < g.bottom():
            pitch = self.yp(pos.y())
            self.sh.preview_channel(c, pitch, 0.8, 1.0)
            self.hover_key = pitch
            self.drag = ("key", pitch)
            self.update()
            return
        if pos.y() >= g.bottom() and pos.y() < self.height() - SB and pos.x() >= KEYW:
            self.sh.commit("Сила нажатия")
            self.drag = ("vel",)
            self._vel_at(pos)
            return
        if pos.y() < RULER:
            if self.sh.p.get("mode") == "pat":
                self.sh.set_pos_beats(self.q(self.xb(pos.x()), "floor"))
            return
        if not g.contains(pos):
            return
        tool = self.roll.tool
        n, edge = self.note_at(pos)
        if e.button() == Qt.MouseButton.RightButton or tool == "erase":
            self.sh.commit("Удалить ноты")
            self.drag = ("erase",)
            if n is not None:
                self._remove(n)
            return
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier or tool == "select":
            if n is not None and not (e.modifiers() & Qt.KeyboardModifier.ControlModifier):
                if n not in self.sel:
                    self.sel = [n]
                self._start_move(pos, n)
                return
            self.drag = ("box", pos)
            self.box = QRectF(pos, pos)
            if not (e.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                self.sel = []
            return
        if n is not None:
            if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:          # копия
                self.sh.commit("Копия нот")
                group = self.sel if n in self.sel else [n]
                copies = [list(x) for x in group]
                ns.extend(copies)
                self.sel = copies
                n = copies[group.index(n)]
                self._start_move(pos, n, committed=True)
                return
            if edge:
                self.sh.commit("Длина ноты")
                group = self.sel if n in self.sel else [n]
                self.drag = ("resize", n, pos.x(), [(x, x[1]) for x in group])
                self._length_matters(c)
                return
            if n not in self.sel:
                self.sel = [n]
            self._start_move(pos, n)
            self.sh.preview_channel(c, n[2], n[3], min(1.0, n[1]))
            return
        # новая нота
        self.sh.commit("Нота")
        t = self.q(self.xb(pos.x()))
        pitch = self.yp(pos.y())
        ln = self.roll.last_len
        new = []
        chord = CHORDS[self.roll.chord_cb.currentIndex()][1] or [0]
        for iv in chord:
            if 0 <= pitch + iv <= 127:
                nn = [round(t, 5), ln, int(pitch + iv), self.roll.last_vel]
                ns.append(nn)
                new.append(nn)
        self.sel = new
        self.sh.preview_channel(c, pitch, self.roll.last_vel, min(1.0, ln))
        self._start_move(pos, new[0] if new else None, committed=True)
        self.drag = ("new", pos.x(), new) if new else None
        self._changed(rebuild=True)

    def _start_move(self, pos, n, committed=False):
        if n is None:
            return
        if not committed:
            self.sh.commit("Двигать ноты")
        group = self.sel if n in self.sel else [n]
        self.drag = ("move", pos, n, [(x, x[0], x[2]) for x in group], n[0])

    def mouseMoveEvent(self, e):
        pos = e.position()
        d = self.drag
        if d is None:
            n, edge = self.note_at(pos)
            g = self.grid_rect()
            cell = None
            if n is not None and g.contains(pos):
                self.setCursor(Qt.CursorShape.SizeHorCursor if edge else Qt.CursorShape.SizeAllCursor)
                U.hint_to(self, f"{D.note_name(int(n[2]))}: начало {n[0]:.2f}, длина {n[1]:.2f} доли, "
                                f"сила {int(n[3] * 100)} %")
            else:
                self.unsetCursor()
                if g.contains(pos) and self.roll.tool == "draw":
                    cell = (self.q(self.xb(pos.x())), self.yp(pos.y()))
            if cell != self.hover_cell:
                self.hover_cell = cell
                self.update()
            hk = self.yp(pos.y()) if pos.x() < KEYW else None
            if hk != self.hover_key:
                self.hover_key = hk
                self.update(0, RULER, KEYW, self.height())
            return
        kind = d[0]
        if kind == "pan":
            _k, p0, sx0, sy0 = d
            g = self.grid_rect()
            self.sx = max(0.0, sx0 - (pos.x() - p0.x()))
            self.sy = max(0.0, min(128 * self.kh - g.height(), sy0 - (pos.y() - p0.y())))
            self._sync_bars()
            self.update()
            return
        free = bool(e.modifiers() & Qt.KeyboardModifier.AltModifier)    # Alt — без сетки
        if kind == "key":
            pitch = self.yp(pos.y())
            if pitch != d[1]:
                c = PR.chan(self.sh.p, self.roll.cid)
                self.sh.preview_channel(c, pitch, 0.8, 1.0)
                self.drag = ("key", pitch)
                self.hover_key = pitch
                self.update()
        elif kind == "vel":
            self._vel_at(pos)
        elif kind == "erase":
            n, _ = self.note_at(pos)
            if n is not None:
                self._remove(n)
        elif kind == "box":
            self.box = QRectF(d[1], pos).normalized()
            self.update()
        elif kind in ("move",):
            _k, p0, n0, group, t0 = d
            dt = self.xb(pos.x()) - self.xb(p0.x())
            dp = self.yp(pos.y()) - self.yp(p0.y())
            nt0 = self.q(t0 + dt, "round") if self.snap() > 0 and not free else max(0.0, t0 + dt)
            shift = nt0 - t0
            minstart = min(g[1] for g in group)
            shift = max(shift, -minstart)
            old_pitch = n0[2]
            for x, ot, op in group:
                x[0] = round(ot + shift, 5)
                x[2] = int(max(0, min(127, op + dp)))
            if n0[2] != old_pitch:
                c = PR.chan(self.sh.p, self.roll.cid)
                self.sh.preview_channel(c, n0[2], n0[3], 0.4)
            self._changed(rebuild=False)
        elif kind == "resize":
            _k, n0, x0, group = d
            dl = (pos.x() - x0) / self.ppb
            s = 0.0 if free else self.snap()
            for x, ol in group:
                nl = ol + dl
                if s > 0:
                    nl = max(s, round(nl / s) * s)
                x[1] = round(max(0.03, nl), 5)
            self.roll.last_len = n0[1]
            self._changed(rebuild=False)
        elif kind == "new":
            _k, x0, new = d
            dl = (pos.x() - x0) / self.ppb
            s = self.snap() or 0.0625
            if dl > s * 0.5:
                if self.snap() > 0 and not free:
                    ln = max(s, round((self.roll.last_len + dl) / s) * s)
                else:
                    ln = max(0.03, self.roll.last_len + dl)
                if not self._len_checked:
                    self._len_checked = True
                    self._length_matters(PR.chan(self.sh.p, self.roll.cid))
                for x in new:
                    x[1] = round(ln, 5)
                self._changed(rebuild=False)

    def mouseReleaseEvent(self, e):
        d = self.drag
        self.drag = None
        self._len_checked = False
        if d is None:
            return
        if d[0] == "pan":
            self.unsetCursor()
            return
        if d[0] == "box" and self.box is not None:
            _pat, ns = self.notes()
            r = self.box
            for n in ns:
                nr = QRectF(self.bx(n[0]), self.py(n[2]), max(4.0, n[1] * self.ppb), self.kh)
                if r.intersects(nr) and n not in self.sel:
                    self.sel.append(n)
            self.box = None
            self.update()
        elif d[0] == "key":
            self.hover_key = None
            self.update()
        elif d[0] == "new":
            if d[2]:
                self.roll.last_len = d[2][0][1]
            self._changed(rebuild=True)
        elif d[0] in ("move", "resize", "vel", "erase"):
            pat, ns = self.notes()
            ns.sort(key=lambda n: (n[0], n[2]))
            self._changed(rebuild=True)

    def mouseDoubleClickEvent(self, e):
        n, _ = self.note_at(e.position())
        if n is not None:
            self.sel = [n]
            self.update()

    def wheelEvent(self, e):
        dy = e.angleDelta().y() or e.angleDelta().x()
        mods = e.modifiers()
        both = Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ShiftModifier
        if (mods & both) == both:
            n, _ = self.note_at(e.position())
            group = (self.sel if n in self.sel else [n]) if n is not None else list(self.sel)
            if group:
                if not self._wheel_vel:
                    self.sh.commit("Сила нажатия")
                    self._wheel_vel = True
                for x in group:
                    x[3] = round(max(0.02, min(1.0, x[3] + (0.05 if dy > 0 else -0.05))), 3)
                self.roll.last_vel = group[0][3]
                U.hint_to(self, f"Сила нажатия: {int(group[0][3] * 100)} %")
                self._changed(rebuild=False)
            return
        self._wheel_vel = False
        if mods & Qt.KeyboardModifier.ControlModifier:
            b = self.xb(e.position().x())
            f = 1.15 if dy > 0 else 1 / 1.15
            self.ppb = max(12.0, min(600.0, self.ppb * f))
            self.sx = max(0.0, b * self.ppb - (e.position().x() - KEYW))
        elif mods & Qt.KeyboardModifier.AltModifier:
            self.kh = max(7, min(30, self.kh + (1 if dy > 0 else -1)))
        elif mods & Qt.KeyboardModifier.ShiftModifier:
            self.sx = max(0.0, self.sx - dy)
        else:
            self.sy = max(0.0, min(128 * self.kh - self.grid_rect().height(), self.sy - dy))
        self._sync_bars()
        self.update()

    def _vel_at(self, pos):
        _pat, ns = self.notes()
        if not ns:
            return
        g = self.grid_rect()
        top, bot = g.bottom() + 6, self.height() - SB - 4
        v = max(0.02, min(1.0, (bot - pos.y()) / max(1, bot - top)))
        x = pos.x()
        targets = [n for n in self.sel] if self.sel else []
        if not targets:
            best = None
            for n in ns:
                dx = abs(self.bx(n[0]) - x)
                if dx < 8 and (best is None or dx < best[0]):
                    best = (dx, n)
            if best:
                targets = [best[1]]
        else:
            targets = [n for n in targets if abs(self.bx(n[0]) - x) < 8] or targets
        for n in targets:
            n[3] = round(v, 3)
        if targets:
            self.roll.last_vel = targets[0][3]
            self._changed(rebuild=False)
            U.hint_to(self, f"Сила нажатия: {int(v * 100)} %")

    def leaveEvent(self, e):
        if self.hover_cell is not None or self.hover_key is not None:
            self.hover_cell = None
            self.hover_key = None
            self.update()

    def _length_matters(self, c):
        """Сэмплер с «Играть всю длину» не слушает длину ноты — раз её меняют, выключаем это."""
        if c is None or c.get("gen") != "sampler":
            return
        prm = c.setdefault("params", {})
        if prm.get("hold", 1):
            prm["hold"] = 0
            prm["rel"] = min(float(prm.get("rel", 0.05) or 0.05), 0.08)
            U.hint_to(self, "Теперь длина ноты обрезает звук сэмплера (в его настройках выключено "
                            "«Играть всю длину»)")
            self.sh.touch(rebuild=False, views=("plugins",))

    def _remove(self, n):
        _pat, ns = self.notes()
        if n in ns:
            ns.remove(n)
        if n in self.sel:
            self.sel.remove(n)
        self._changed(rebuild=True)

    def _changed(self, rebuild=True):
        self.update()
        if rebuild:
            self.sh.touch(views=("rack", "playlist"))
        else:
            self.sh.touch(rebuild=True, views=())

    # ── клавиши ── #
    def keyPressEvent(self, e):
        k = e.key()
        mods = e.modifiers()
        pat, ns = self.notes()
        if pat is None:
            return e.ignore()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        if k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.sel:
            self.sh.commit("Удалить ноты")
            for n in list(self.sel):
                if n in ns:
                    ns.remove(n)
            self.sel = []
            self._changed()
        elif ctrl and k == Qt.Key.Key_A:
            self.sel = list(ns)
            self.update()
        elif ctrl and k == Qt.Key.Key_C and self.sel:
            t0 = min(n[0] for n in self.sel)
            self.roll.clip = [[n[0] - t0, n[1], n[2], n[3]] for n in self.sel]
            U.hint_to(self, f"Скопировано нот: {len(self.sel)}")
        elif ctrl and k == Qt.Key.Key_V and self.roll.clip:
            self.sh.commit("Вставить ноты")
            at = max((n[0] + n[1] for n in self.sel), default=None)
            if at is None:
                at = self.q(self.sh.play_beat_in_pattern() or 0.0)
            at = self.q(at, "round")
            new = [[round(at + x[0], 5), x[1], x[2], x[3]] for x in self.roll.clip]
            ns.extend(new)
            self.sel = new
            self._changed()
        elif ctrl and k == Qt.Key.Key_D and self.sel:
            self.sh.commit("Повторить ноты")
            t0 = min(n[0] for n in self.sel)
            t1 = max(n[0] + n[1] for n in self.sel)
            span = max(self.snap() or 0.25, self.q(t1 - t0 + 1e-6, "round") if self.snap() else t1 - t0)
            new = [[round(n[0] + span, 5), n[1], n[2], n[3]] for n in self.sel]
            ns.extend(new)
            self.sel = new
            self._changed()
        elif k in (Qt.Key.Key_Up, Qt.Key.Key_Down) and self.sel:
            self.sh.commit("Транспонировать")
            d = (12 if mods & Qt.KeyboardModifier.ShiftModifier else 1) * (1 if k == Qt.Key.Key_Up else -1)
            for n in self.sel:
                n[2] = int(max(0, min(127, n[2] + d)))
            self._changed()
        elif k in (Qt.Key.Key_Left, Qt.Key.Key_Right) and self.sel:
            self.sh.commit("Сдвиг нот")
            s = self.snap() or 0.25
            d = s * (1 if k == Qt.Key.Key_Right else -1)
            if min(n[0] for n in self.sel) + d >= 0:
                for n in self.sel:
                    n[0] = round(n[0] + d, 5)
            self._changed()
        elif k == Qt.Key.Key_Q and not ctrl:
            self.roll.quantize()
        else:
            e.ignore()

    # ── рисование ── #
    def paintEvent(self, e):
        from PyQt6.QtGui import QPixmap
        dpr = self.devicePixelRatioF()
        size = (self.width(), self.height(), dpr)
        if self._dirty or self._layer is None or self._layer[0] != size:
            pm = QPixmap(max(1, int(self.width() * dpr)), max(1, int(self.height() * dpr)))
            pm.setDevicePixelRatio(dpr)
            q = QPainter(pm)
            self._paint_static(q)
            if q.isActive():
                q.end()
            self._layer = (size, pm)
            self._dirty = False
        p = QPainter(self)
        p.drawPixmap(0, 0, self._layer[1])
        pb = self.sh.play_beat_in_pattern() if self.roll.cid else None
        if pb is not None:
            g = self.grid_rect()
            p.setClipRect(g)
            x = self.bx(pb)
            p.setPen(QPen(U.ACC, 1.5))
            p.drawLine(QPointF(x, g.top()), QPointF(x, g.bottom()))
        p.end()

    def _paint_static(self, p):
        w, h = self.width(), self.height()
        g = self.grid_rect()
        pat, ns = self.notes()
        c = PR.chan(self.sh.p, self.roll.cid) if self.roll.cid else None
        p.fillRect(QRectF(0, 0, w, h), QColor("#22282c"))
        if pat is None or c is None:
            p.setPen(U.DIM)
            p.setFont(U.font(13))
            p.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, "Выберите канал с инструментом")
            p.end()
            return
        plen = self.pat_len()
        scale = self.roll.scale_set()
        # ряды клавиш
        p.setClipRect(g)
        top_p = self.yp(g.top())
        bot_p = self.yp(g.bottom())
        for pitch in range(max(0, bot_p - 1), min(127, top_p + 1) + 1):
            y = self.py(pitch)
            black = pitch % 12 in BLACK
            col = QColor("#262c30") if black else QColor("#2d3439")
            if scale is not None and (pitch % 12) in scale:
                col = col.lighter(118)
            p.fillRect(QRectF(g.left(), y, g.width(), self.kh), col)
            if pitch % 12 == 0:
                p.setPen(QColor("#1b1f22"))
                p.drawLine(QPointF(g.left(), y + self.kh), QPointF(g.right(), y + self.kh))
        # вертикальные линии
        b0 = max(0, int(self.xb(g.left())) - 1)
        b1 = int(self.xb(g.right())) + 2
        s = self.snap()
        if s and s * self.ppb >= 6:
            p.setPen(QColor(255, 255, 255, 12))
            k = math.floor(b0 / s)
            while k * s < b1:
                x = self.bx(k * s)
                p.drawLine(QPointF(x, g.top()), QPointF(x, g.bottom()))
                k += 1
        for b in range(b0, b1):
            x = self.bx(b)
            p.setPen(QColor(0, 0, 0, 120) if b % 4 == 0 else QColor(255, 255, 255, 22))
            p.drawLine(QPointF(x, g.top()), QPointF(x, g.bottom()))
        # за концом паттерна — темнее
        xe = self.bx(plen)
        if xe < g.right():
            p.fillRect(QRectF(max(g.left(), xe), g.top(), g.right() - max(g.left(), xe), g.height()), QColor(0, 0, 0, 70))
        # ноты других каналов (призраки)
        if self.roll.ghost:
            for cid, others in pat.get("notes", {}).items():
                if cid == self.roll.cid:
                    continue
                oc = PR.chan(self.sh.p, cid)
                if oc is None:
                    continue
                col = QColor(oc.get("color") or "#888")
                col.setAlpha(55)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(col)
                for n in others:
                    p.drawRect(QRectF(self.bx(n[0]), self.py(n[2]) + 1, max(3.0, n[1] * self.ppb), self.kh - 2))
        # ноты
        col = QColor(c.get("color") or "#6c9ad8")
        base = U.mix_col(col, QColor("#9bd15c"), 0.55)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(U.font(10))
        for n in ns:
            r = QRectF(self.bx(n[0]), self.py(n[2]) + 0.5, max(4.0, n[1] * self.ppb), self.kh - 1)
            if r.right() < g.left() or r.left() > g.right() or r.bottom() < g.top() or r.top() > g.bottom():
                continue
            sel = n in self.sel
            fill = QColor("#ff9b8a") if sel else U.mix_col(base.darker(140), base.lighter(115), n[3])
            p.setBrush(fill)
            p.setPen(QPen(QColor("#15181a"), 1))
            p.drawRoundedRect(r, 2, 2)
            if r.width() > 26 and self.kh >= 11:
                p.setPen(QColor("#15181a"))
                p.drawText(r.adjusted(3, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, D.note_name(int(n[2])))
        if self.hover_cell is not None and self.drag is None:
            ht, hp = self.hover_cell
            hr = QRectF(self.bx(ht), self.py(hp) + 0.5, max(4.0, self.roll.last_len * self.ppb), self.kh - 1)
            p.fillRect(QRectF(g.left(), self.py(hp), g.width(), self.kh), QColor(255, 255, 255, 10))
            p.setBrush(QColor(255, 255, 255, 26))
            p.setPen(QPen(QColor(255, 255, 255, 70), 1, Qt.PenStyle.DashLine))
            p.drawRoundedRect(hr, 2, 2)
        if self.box is not None:
            p.setBrush(QColor(240, 163, 58, 40))
            p.setPen(QPen(U.ACC, 1, Qt.PenStyle.DashLine))
            p.drawRect(self.box)
        p.setClipping(False)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        # линейка
        rr = QRectF(KEYW, 0, w - KEYW, RULER)
        p.fillRect(rr, QColor("#353c42"))
        p.setPen(U.DIM)
        p.setFont(U.font(10))
        p.setClipRect(rr)
        for b in range(b0, b1):
            x = self.bx(b)
            if b % 4 == 0:
                p.setPen(U.TEXT)
                p.drawText(QPointF(x + 3, 14), str(b // 4 + 1))
                p.setPen(QColor("#5b646c"))
                p.drawLine(QPointF(x, 10), QPointF(x, RULER))
            elif self.ppb > 28:
                p.setPen(QColor("#4b545b"))
                p.drawLine(QPointF(x, 15), QPointF(x, RULER))
        xe = self.bx(plen)
        p.setPen(QPen(U.ACC, 2))
        p.drawLine(QPointF(xe, 0), QPointF(xe, RULER))
        p.setClipping(False)
        # клавиатура
        kr = QRectF(0, RULER, KEYW, g.height())
        p.setClipRect(kr)
        p.fillRect(kr, QColor("#e9ecee"))
        for pitch in range(max(0, bot_p - 1), min(127, top_p + 1) + 1):
            y = self.py(pitch)
            black = pitch % 12 in BLACK
            if black:
                p.fillRect(QRectF(0, y, KEYW * 0.62, self.kh), QColor("#20252a"))
            else:
                p.setPen(QColor("#b9c0c5"))
                p.drawLine(QPointF(0, y + self.kh), QPointF(KEYW, y + self.kh))
            if pitch == self.hover_key or (self.hover_cell is not None and pitch == self.hover_cell[1]):
                p.fillRect(QRectF(0, y, KEYW, self.kh), QColor(240, 163, 58, 150))
            if pitch % 12 == 0:
                p.setPen(QColor("#30363b"))
                p.setFont(U.font(9, True))
                p.drawText(QRectF(KEYW * 0.6, y, KEYW * 0.38, self.kh), Qt.AlignmentFlag.AlignVCenter |
                           Qt.AlignmentFlag.AlignRight, D.note_name(pitch))
        p.setClipping(False)
        p.fillRect(QRectF(0, 0, KEYW, RULER), QColor("#2b3136"))
        # сила нажатия
        vr = QRectF(KEYW, g.bottom(), w - KEYW - SB, h - g.bottom() - SB)
        p.fillRect(vr, QColor("#1d2225"))
        p.setPen(QColor("#3a4248"))
        p.drawLine(QPointF(KEYW, g.bottom()), QPointF(w, g.bottom()))
        p.fillRect(QRectF(0, g.bottom(), KEYW, vr.height()), QColor("#2b3136"))
        p.setPen(U.DIM)
        p.setFont(U.font(10))
        p.drawText(QRectF(4, g.bottom(), KEYW - 6, vr.height()), Qt.AlignmentFlag.AlignVCenter, "Сила")
        p.setClipRect(vr)
        top, bot = g.bottom() + 6, h - SB - 4
        for n in ns:
            x = self.bx(n[0])
            if x < KEYW - 4 or x > w:
                continue
            y = bot - n[3] * (bot - top)
            colv = QColor("#ff9b8a") if n in self.sel else base
            p.setPen(QPen(colv, 2))
            p.drawLine(QPointF(x + 1, bot), QPointF(x + 1, y))
            p.setBrush(colv)
            p.drawEllipse(QPointF(x + 1, y), 3, 3)
        p.setClipping(False)
        p.end()


class PianoRoll(QWidget):
    """Содержимое окна «Пианоролл» + панель инструментов."""

    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self.sh = shell
        self.cid = None
        self.tool = "draw"
        self.last_len = 0.25
        self.last_vel = 0.78
        self.ghost = True
        self.clip = []
        from PyQt6.QtWidgets import QVBoxLayout
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.canvas = RollCanvas(self)
        v.addWidget(self.canvas, 1)
        self.toolbar = self._make_toolbar()

    def _make_toolbar(self):
        tb = U.Toolbar()
        self.chan_cb = QComboBox()
        self.chan_cb.setMinimumWidth(130)
        U.tip(self.chan_cb, "Канал, ноты которого редактируются")
        self.chan_cb.activated.connect(lambda i: self.sh.select_channel(self.chan_cb.itemData(i), open_roll=True))
        tb.add(self.chan_cb)
        tb.add(U.label("Паттерн"))
        self.pat_cb = QComboBox()
        self.pat_cb.setMinimumWidth(110)
        self.pat_cb.activated.connect(lambda i: self.sh.select_pattern(self.pat_cb.itemData(i)))
        tb.add(self.pat_cb)
        tb.space(6)
        self.tool_btns = {}
        for t, ic, tip, txt in (("draw", "pencil", "Карандаш: ставить и двигать ноты", "Рисовать"),
                                ("select", "select", "Выделение рамкой", "Выделить"),
                                ("erase", "erase", "Ластик: удалять ноты", "Стирать")):
            b = U.IconButton(ic, tip, 24, checkable=True, text=txt)
            b.clicked.connect(lambda _=False, t=t: self.set_tool(t))
            self.tool_btns[t] = b
            tb.add(b)
        self.tool_btns["draw"].setChecked(True)
        tb.space(6)
        tb.add(U.label("Сетка"))
        self.snap_cb = QComboBox()
        for nm, _v in SNAPS:
            self.snap_cb.addItem(nm)
        self.snap_cb.setCurrentIndex(4)
        self.snap_cb.currentIndexChanged.connect(lambda _i: self.canvas.update())
        tb.add(self.snap_cb)
        tb.add(U.label("Аккорд"))
        self.chord_cb = QComboBox()
        for nm, _iv in CHORDS:
            self.chord_cb.addItem(nm)
        U.tip(self.chord_cb, "Штамп: одним щелчком ставится весь аккорд")
        tb.add(self.chord_cb)
        tb.add(U.label("Лад"))
        self.key_cb = QComboBox()
        self.key_cb.addItem("нет", None)
        for k in range(12):
            for sc, nm in ((1, "минор"), (0, "мажор")):
                self.key_cb.addItem(f"{D.KEYS[k]} {nm}", (k, sc))
        U.tip(self.key_cb, "Подсветить ноты лада и использовать его в генераторах")
        self.key_cb.currentIndexChanged.connect(lambda _i: self.canvas.update())
        tb.add(self.key_cb)
        b = U.IconButton("magnet", "Квантизация: подтянуть начала нот к сетке (Q)", 24, text="Ровно")
        b.clicked.connect(self.quantize)
        tb.add(b)
        b = U.IconButton("star", "Генераторы: аккорды, бас, арпеджио, мелодия", 24, text="Идеи")
        b.clicked.connect(lambda: self.gen_menu(b.mapToGlobal(b.rect().bottomLeft())))
        tb.add(b)
        self.ghost_btn = U.IconButton("roll", "Показывать ноты других каналов", 24, checkable=True)
        self.ghost_btn.setChecked(True)
        self.ghost_btn.toggled.connect(self._ghost)
        tb.add(self.ghost_btn)
        tb.space(8)
        self.status = U.label("", 11)
        tb.add(self.status)
        self.place_btn = U.IconButton("plus", "Поставить этот паттерн в плейлист", 24, text="В песню")
        self.place_btn.clicked.connect(self.place_in_song)
        tb.add(self.place_btn)
        tb.stretch()
        return tb

    def _ghost(self, on):
        self.ghost = on
        self.canvas.update()

    def set_tool(self, t):
        self.tool = t
        for k, b in self.tool_btns.items():
            b.setChecked(k == t)

    def snap_val(self):
        return SNAPS[self.snap_cb.currentIndex()][1]

    def scale_set(self):
        d = self.key_cb.currentData()
        if not d:
            return None
        k, sc = d
        iv = FX.SCALES[sc][1]
        return {(k + x) % 12 for x in iv}

    def key_scale(self):
        d = self.key_cb.currentData()
        if d:
            return d
        return (9, 1)                                     # ля минор по умолчанию

    def set_channel(self, cid):
        changed = cid != self.cid
        self.cid = cid
        self.canvas.sel = []
        if cid is not None:
            self._follow_pattern()
        self.refresh()
        if changed:
            self.canvas.center_on_notes()

    def _follow_pattern(self):
        """Ноты канала лежат в другом паттерне — показать его, а не пустой текущий."""
        p = self.sh.p
        pat = PR.cur_pattern(p)
        if pat is not None and PR.notes_of(pat, self.cid):
            return
        for pt in p["patterns"]:
            if PR.notes_of(pt, self.cid):
                self.sh.select_pattern(pt["id"], quiet=True)
                return

    def on_pattern(self):
        """Выбран другой паттерн: у канала в нём нет нот, а у другого есть — перейти на тот канал."""
        pat = PR.cur_pattern(self.sh.p)
        if pat is not None and not PR.notes_of(pat, self.cid):
            for c in self.sh.p["channels"]:
                if c.get("gen") != "audio" and PR.notes_of(pat, c["id"]):
                    self.cid = c["id"]
                    self.canvas.sel = []
                    self.refresh()
                    self.canvas.center_on_notes()
                    return
        self.canvas.sel = []
        self.refresh()

    def _uses(self, pid):
        return sum(1 for k in self.sh.p["clips"] if k.get("kind") == "pat" and k.get("ref") == pid)

    def place_in_song(self):
        p = self.sh.p
        pat = PR.cur_pattern(p)
        if pat is None:
            return
        self.sh.commit("Паттерн в песню")
        pb = self.sh.song_beat() if p.get("mode") == "song" else 0.0
        if pb and pb > 0:
            start = math.floor(pb / 4) * 4                # с такта под курсором песни
        else:
            start = PR.song_beats(p) if p["clips"] else 0.0   # иначе — в конец песни
        ln = PR.pattern_used_len(pat)
        lane = PR.free_lane(p, start, ln)
        p["clips"].append({"id": PR.uid(), "kind": "pat", "ref": pat["id"], "lane": lane, "start": round(start, 4),
                           "len": ln})
        self.sh.touch(views=("playlist", "roll"))
        U.hint_to(self, f"Паттерн «{pat.get('name', '')}» стоит в плейлисте: такт {int(start // 4) + 1}, "
                        f"дорожка {lane + 1}")

    def _sync_status(self):
        pat = PR.cur_pattern(self.sh.p)
        if pat is None or self.cid is None:
            self.status.setText("")
            self.place_btn.hide()
            return
        uses = self._uses(pat["id"])
        if uses == 0:
            song = self.sh.p.get("mode") == "song"
            self.status.setText("паттерна нет в плейлисте" + (" — в песне его не слышно" if song else ""))
            self.status.setStyleSheet("color: #f0a33a; background: transparent;")
            self.place_btn.show()
        else:
            self.status.setText(f"в песне: {uses} раз(а)")
            self.status.setStyleSheet(f"color: {U.DIM.name()}; background: transparent;")
            self.place_btn.hide()

    def refresh(self):
        self.chan_cb.blockSignals(True)
        self.chan_cb.clear()
        for c in self.sh.p["channels"]:
            if c.get("gen") != "audio":
                self.chan_cb.addItem(c["name"], c["id"])
        i = self.chan_cb.findData(self.cid)
        if i < 0 and self.chan_cb.count():
            i = 0
            self.cid = self.chan_cb.itemData(0)
        self.chan_cb.setCurrentIndex(max(0, i))
        self.chan_cb.blockSignals(False)
        self.pat_cb.blockSignals(True)
        self.pat_cb.clear()
        cur = PR.cur_pattern(self.sh.p)
        for pt in self.sh.p["patterns"]:
            mark = "  *" if self.cid and PR.notes_of(pt, self.cid) else ""
            self.pat_cb.addItem(pt.get("name", "Паттерн") + mark, pt["id"])
        if cur is not None:
            self.pat_cb.setCurrentIndex(max(0, self.pat_cb.findData(cur["id"])))
        self.pat_cb.blockSignals(False)
        self._sync_status()
        self.canvas._sync_bars()
        self.canvas.update()

    def tick(self):
        if self.isVisible() and self.sh.play_beat_in_pattern() is not None:
            self.canvas.update_dynamic()

    # ── действия ── #
    def quantize(self):
        _pat, ns = self.canvas.notes()
        s = self.snap_val() or 0.25
        targets = self.canvas.sel or ns
        if not targets:
            return
        self.sh.commit("Квантизация")
        for n in targets:
            n[0] = round(round(n[0] / s) * s, 5)
        self.canvas._changed()
        U.hint_to(self, f"Квантизовано нот: {len(targets)}")

    def gen_menu(self, gp):
        m = QMenu(self)
        m.addAction("Аккорды: грустная прогрессия (i–VI–III–VII)", lambda: self.gen_chords([0, 5, 2, 6]))
        m.addAction("Аккорды: поп (I–V–vi–IV)", lambda: self.gen_chords([0, 4, 5, 3], major=True))
        m.addAction("Аккорды: трэп (i–iv–VI–v)", lambda: self.gen_chords([0, 3, 5, 4]))
        m.addAction("Аккорды: джаз (ii–V–I–I)", lambda: self.gen_chords([1, 4, 0, 0], major=True, sevenths=True))
        m.addSeparator()
        m.addAction("Бас по аккордам паттерна", self.gen_bass)
        m.addAction("Арпеджио из выделенных / всех нот", self.gen_arp)
        m.addAction("Мелодия в ладу (каждый раз новая)", self.gen_melody)
        m.addAction("808 по бочке (ноты на ударах бочки)", self.gen_808)
        m.addSeparator()
        m.addAction("Очистить ноты канала", self.clear_notes)
        m.exec(gp)

    def _scale_notes(self, major=None):
        k, sc = self.key_scale()
        if major is not None:
            sc = 0 if major else 1
        return k, FX.SCALES[sc][1]

    def _put(self, new, label):
        pat, ns = self.canvas.notes()
        if pat is None:
            return
        self.sh.commit(label)
        ns.extend(new)
        ns.sort(key=lambda n: (n[0], n[2]))
        end = max(n[0] + n[1] for n in ns) if ns else 4
        pat["len"] = max(pat.get("len", 4.0), math.ceil(end / 4 - 1e-6) * 4)
        self.canvas.sel = list(new)
        self.canvas._changed()
        self.canvas.center_on_notes()

    def gen_chords(self, degrees, major=False, sevenths=False):
        k, sc = self._scale_notes(major)
        base = 48 + k
        new = []
        for bar, dg in enumerate(degrees):
            for j in range(4 if sevenths else 3):
                st = dg + 2 * j
                octv, idx = divmod(st, len(sc))
                pitch = base + sc[idx] + 12 * octv
                new.append([bar * 4.0, 4.0, int(pitch), 0.72])
        self._put(new, "Аккорды")

    def gen_bass(self):
        pat, ns = self.canvas.notes()
        if pat is None:
            return
        # корни аккордов — самые низкие ноты всех каналов в каждом такте
        allnotes = [n for cid, lst in pat.get("notes", {}).items() for n in lst
                    if PR.chan(self.sh.p, cid) and PR.chan(self.sh.p, cid).get("gen") != "sampler"]
        bars = int(PR.pattern_used_len(pat) // 4)
        new = []
        k, _sc = self._scale_notes()
        for b in range(bars):
            inbar = [n for n in allnotes if b * 4 <= n[0] < b * 4 + 4]
            root = (min(n[2] for n in inbar) % 12) if inbar else k
            pitch = 36 + root
            for q in (0, 1.5, 2.5, 3):
                new.append([b * 4 + q, 0.5 if q < 3 else 0.75, pitch, 0.85 if q == 0 else 0.7])
        self._put(new, "Бас")

    def gen_arp(self):
        pat, ns = self.canvas.notes()
        src = self.canvas.sel or list(ns)
        if not src:
            U.hint_to(self, "Нет нот для арпеджио — сначала поставьте аккорды")
            return
        groups = {}
        for n in src:
            groups.setdefault(round(n[0], 3), []).append(n)
        new = []
        for t0, g in sorted(groups.items()):
            pitches = sorted({x[2] for x in g})
            dur = max(x[1] for x in g)
            steps = int(dur / 0.25)
            seq = pitches + pitches[-2:0:-1] if len(pitches) > 2 else pitches
            for s in range(steps):
                new.append([round(t0 + s * 0.25, 5), 0.25, seq[s % len(seq)] + 12, 0.7 if s % 4 else 0.85])
        self.sh.commit("Арпеджио")
        for n in src:
            if n in ns:
                ns.remove(n)
        ns.extend(new)
        ns.sort(key=lambda n: (n[0], n[2]))
        self.canvas.sel = new
        self.canvas._changed()

    def gen_melody(self):
        pat, ns = self.canvas.notes()
        if pat is None:
            return
        k, sc = self._scale_notes()
        rng = random.Random()
        bars = max(2, int(PR.pattern_used_len(pat) // 4))
        rhythms = [[1, 0.5, 0.5, 1, 1], [0.5, 0.5, 0.5, 0.5, 1, 1], [1.5, 0.5, 1, 1], [0.75, 0.75, 0.5, 1, 1],
                   [0.5, 1, 0.5, 2]]
        deg = rng.choice([0, 2, 4])
        new = []
        motif = rng.choice(rhythms)
        for b in range(bars):
            t = b * 4.0
            rh = motif if b % 2 == 0 else rng.choice(rhythms)
            for d in rh:
                if rng.random() < 0.12:
                    t += d
                    continue
                deg = max(-3, min(10, deg + rng.choice([-2, -1, -1, 0, 1, 1, 2, 3, -3])))
                octv, idx = divmod(deg, len(sc))
                pitch = 60 + k + sc[idx] + 12 * octv
                new.append([round(t, 5), round(d * 0.9, 5), int(pitch), round(rng.uniform(0.65, 0.9), 2)])
                t += d
        self._put(new, "Мелодия")

    def gen_808(self):
        pat, ns = self.canvas.notes()
        if pat is None:
            return
        kicks = []
        for cid, lst in pat.get("notes", {}).items():
            c = PR.chan(self.sh.p, cid)
            if c and c.get("gen") == "sampler" and "Боч" in str(c.get("params", {}).get("sample", "")) + c["name"]:
                kicks += [n[0] for n in lst]
        if not kicks:
            U.hint_to(self, "В паттерне нет бочки — поставьте шаги бочке, и 808 ляжет на них")
            return
        k, _sc = self._scale_notes()
        kicks = sorted(set(kicks))
        new = []
        for i, t in enumerate(kicks):
            nxt = kicks[i + 1] if i + 1 < len(kicks) else PR.pattern_used_len(pat)
            new.append([t, round(max(0.25, nxt - t), 5), 36 + k, 0.85])
        self._put(new, "808 по бочке")

    def clear_notes(self):
        pat, ns = self.canvas.notes()
        if ns:
            self.sh.commit("Очистить ноты")
            ns.clear()
            self.canvas.sel = []
            self.canvas._changed()
