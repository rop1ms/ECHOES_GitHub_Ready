# daw_playlist.py
"""
Плейлист студии: дорожки с клипами паттернов и аудиоклипами (дорожки не привязаны к каналам —
как в студийных программах, клип можно положить куда угодно).

Инструменты: кисть (ставить и двигать клипы; щелчок по клипу выбирает его для следующих),
рисование серией, ластик, нож, выделение рамкой, заглушка. Тянуть клип — двигать (Shift/Ctrl —
копия), края — длина (левый край аудиоклипа сдвигает начало звука). Правая кнопка — удалить.
Двойной щелчок: паттерн — открыть, аудиоклип — свойства (громкость, затухания, тон, темп).
Линейка: щелчок — позиция. Колесо — прокрутка, Ctrl — масштаб, Alt — высота дорожек.
Сюда можно перетащить трек или звук из браузера.
"""
from __future__ import annotations

import math
from collections import OrderedDict

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import QComboBox, QInputDialog, QMenu, QScrollBar, QVBoxLayout, QWidget

import daw_dsp as D
import daw_project as PR
import daw_ui as U

HEADW = 140
RULER = 22
SB = 11
SNAPS = [("такт", 4.0), ("доля", 1.0), ("1/2 доли", 0.5), ("1/4 доли", 0.25), ("без сетки", 0.0)]


def _wave_pixmap(pk, s0, s1, W, H):
    """Волна клипа картинкой: минимум/максимум пиков на столбец — numpy, без линии на каждый пиксель."""
    from PyQt6.QtGui import QImage
    n = len(pk)
    idx = np.linspace(s0, s1, W + 1)
    lo = np.clip(idx[:-1].astype(np.int64), 0, max(0, n - 1))
    ok = idx[:-1] < n
    if n == 0 or not ok.any():
        px = QPixmap(W, H)
        px.fill(Qt.GlobalColor.transparent)
        return px
    mn = np.minimum.reduceat(pk[:, 0], lo)
    mx = np.maximum.reduceat(pk[:, 1], lo)
    mid = H / 2
    top = np.floor(mid - mx * mid * 0.95).astype(np.int32)
    bot = np.ceil(mid - mn * mid * 0.95).astype(np.int32)
    bot = np.maximum(bot, top + 1)
    ys = np.arange(H, dtype=np.int32)[:, None]
    m = (ys >= top[None, :]) & (ys < bot[None, :]) & ok[None, :]
    img = np.zeros((H, W), np.uint32)
    a = 200
    img[m] = (a << 24) | (int(235 * a / 255) << 16) | (int(240 * a / 255) << 8) | int(243 * a / 255)
    qi = QImage(img.data, W, H, W * 4, QImage.Format.Format_ARGB32_Premultiplied).copy()
    return QPixmap.fromImage(qi)


class PlaylistCanvas(QWidget):
    def __init__(self, pl, parent=None):
        super().__init__(parent)
        self.pl = pl
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)
        self.ppb = 16.0
        self.lh = 46
        self.sx = 0.0
        self.sy = 0.0
        self.drag = None
        self.sel: list = []
        self.box = None
        self.cache: OrderedDict = OrderedDict()
        self.vbar = QScrollBar(Qt.Orientation.Vertical, self)
        self.hbar = QScrollBar(Qt.Orientation.Horizontal, self)
        self.vbar.valueChanged.connect(lambda v: self._scroll(y=v))
        self.hbar.valueChanged.connect(lambda v: self._scroll(x=v))
        self.drop_preview = None
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self._dirty = True                    # неподвижная часть (дорожки, клипы) — готовой картинкой
        self._layer = None

    def update(self, *a):
        """Любая правка из кода — перерисовать всё; движение курсора — update_dynamic()."""
        self._dirty = True
        super().update(*a)

    def update_dynamic(self):
        super().update()

    @property
    def sh(self):
        return self.pl.sh

    @property
    def p(self):
        return self.sh.p

    # ── геометрия ── #
    def grid(self):
        return QRectF(HEADW, RULER, max(10, self.width() - HEADW - SB), max(10, self.height() - RULER - SB))

    def bx(self, b):
        return HEADW + b * self.ppb - self.sx

    def xb(self, x):
        return (x - HEADW + self.sx) / self.ppb

    def ly(self, lane):
        return RULER + lane * self.lh - self.sy

    def yl(self, y):
        return int((y - RULER + self.sy) // self.lh)

    def snap(self):
        return self.pl.snap_val()

    def q(self, b, mode="round"):
        s = self.snap()
        if s <= 0:
            return max(0.0, b)
        f = round if mode == "round" else math.floor
        return max(0.0, f(b / s + (1e-9 if mode != "round" else 0)) * s)

    def song_end(self):
        return PR.song_beats(self.p)

    def _scroll(self, x=None, y=None):
        if x is not None:
            self.sx = float(x)
        if y is not None:
            self.sy = float(y)
        self.update()

    def sync_bars(self):
        g = self.grid()
        self.vbar.blockSignals(True)
        self.vbar.setRange(0, max(0, int(PR.N_LANES * self.lh - g.height())))
        self.vbar.setPageStep(int(g.height()))
        self.vbar.setValue(int(self.sy))
        self.vbar.blockSignals(False)
        beats = self.song_end() + 64
        self.hbar.blockSignals(True)
        self.hbar.setRange(0, max(0, int(beats * self.ppb - g.width())))
        self.hbar.setPageStep(int(g.width()))
        self.hbar.setValue(int(self.sx))
        self.hbar.blockSignals(False)

    def resizeEvent(self, e):
        self.vbar.setGeometry(self.width() - SB, RULER, SB, self.height() - RULER - SB)
        self.hbar.setGeometry(HEADW, self.height() - SB, self.width() - HEADW - SB, SB)
        self.sync_bars()

    def clip_rect(self, k):
        return QRectF(self.bx(k["start"]), self.ly(k["lane"]) + 1, max(3.0, k["len"] * self.ppb), self.lh - 2)

    def clip_at(self, pos):
        for k in reversed(self.p["clips"]):
            r = self.clip_rect(k)
            if r.contains(pos):
                edge = None
                if pos.x() > r.right() - min(7.0, r.width() * 0.3):
                    edge = "r"
                elif pos.x() < r.left() + min(7.0, r.width() * 0.3) and k["kind"] == "audio":
                    edge = "l"
                return k, edge
        return None, None

    # ── мышь ── #
    def mousePressEvent(self, e):
        self.setFocus()
        pos = e.position()
        g = self.grid()
        sh = self.sh
        if pos.y() < RULER and pos.x() >= HEADW:
            sh.set_mode("song")
            sh.set_pos_beats(self.q(self.xb(pos.x()), "floor") if self.snap() else self.xb(pos.x()))
            self.drag = ("ruler",)
            return
        if pos.x() < HEADW and pos.y() >= RULER:
            lane = self.yl(pos.y())
            if 0 <= lane < PR.N_LANES:
                ly = self.ly(lane)
                if pos.x() < 24 and ly + 6 <= pos.y() <= ly + 22:
                    sh.commit("Заглушить дорожку")
                    ln = self.p["lanes"][lane]
                    ln["mute"] = not ln.get("mute")
                    sh.touch(views=("playlist",))
                elif e.button() == Qt.MouseButton.RightButton:
                    self.lane_menu(lane, e.globalPosition().toPoint())
            return
        if not g.contains(pos):
            return
        tool = self.pl.tool
        k, edge = self.clip_at(pos)
        mods = e.modifiers()
        if e.button() == Qt.MouseButton.RightButton or tool == "erase":
            sh.commit("Удалить клипы")
            self.drag = ("erase",)
            if k is not None:
                self._remove(k)
            return
        if tool == "mute" and k is not None:
            sh.commit("Заглушить клип")
            k["mute"] = not k.get("mute")
            sh.touch(views=("playlist",))
            return
        if tool == "slice" and k is not None:
            self._slice(k, self.q(self.xb(pos.x())))
            return
        if tool == "select" or (mods & Qt.KeyboardModifier.ControlModifier and k is None):
            self.drag = ("box", pos)
            self.box = QRectF(pos, pos)
            if not (mods & Qt.KeyboardModifier.ShiftModifier):
                self.sel = []
            return
        if k is not None:
            self.pl.set_place(k)
            if edge == "r":
                sh.commit("Длина клипа")
                group = self.sel if k in self.sel else [k]
                self.drag = ("rsz", pos.x(), [(c, c["len"]) for c in group])
                return
            if edge == "l":
                sh.commit("Начало клипа")
                self.drag = ("lsz", pos.x(), k, k["start"], k["len"], float(k.get("off") or 0))
                return
            if mods & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier):
                sh.commit("Копия клипов")
                group = self.sel if k in self.sel else [k]
                copies = [dict(c, id=PR.uid()) for c in group]
                self.p["clips"].extend(copies)
                k = copies[group.index(k)]
                self.sel = copies
                self.drag = ("move", pos, k, [(c, c["start"], c["lane"]) for c in copies])
                return
            if k not in self.sel:
                self.sel = [k]
            sh.commit("Двигать клипы")
            self.drag = ("move", pos, k, [(c, c["start"], c["lane"]) for c in self.sel])
            self.update()
            return
        # пусто: поставить выбранное
        lane = self.yl(pos.y())
        if not 0 <= lane < PR.N_LANES:
            return
        new = self.pl.make_clip(self.q(self.xb(pos.x()), "floor"), lane)
        if new is None:
            return
        sh.commit("Поставить клип")
        self.p["clips"].append(new)
        self.sel = [new]
        sh.set_mode("song", quiet=True)
        if tool == "paint":
            self.drag = ("paint", new["len"], lane, {round(new["start"], 4)})
        else:
            self.drag = ("move", pos, new, [(new, new["start"], new["lane"])])
        sh.touch(views=("playlist",))

    def mouseMoveEvent(self, e):
        pos = e.position()
        d = self.drag
        if d is None:
            k, edge = self.clip_at(pos)
            if k is not None and self.grid().contains(pos):
                self.setCursor(Qt.CursorShape.SizeHorCursor if edge else
                               (Qt.CursorShape.SplitHCursor if self.pl.tool == "slice" else Qt.CursorShape.SizeAllCursor))
                U.hint_to(self, self.pl.clip_name(k) + f" — с такта {k['start'] / 4 + 1:.2f}, {k['len'] / 4:g} т. "
                                "Двойной щелчок — открыть, правая кнопка — удалить")
            else:
                self.unsetCursor()
            return
        kind = d[0]
        if kind == "ruler":
            self.sh.set_pos_beats(max(0.0, self.q(self.xb(pos.x()), "floor") if self.snap() else self.xb(pos.x())))
        elif kind == "erase":
            k, _ = self.clip_at(pos)
            if k is not None:
                self._remove(k)
        elif kind == "box":
            self.box = QRectF(d[1], pos).normalized()
            self.update()
        elif kind == "move":
            _k, p0, k0, group = d
            db = self.xb(pos.x()) - self.xb(p0.x())
            dl = self.yl(pos.y()) - self.yl(p0.y())
            s0 = dict((id(c), (st, ln)) for c, st, ln in group)
            st0, _ = s0[id(k0)]
            ns = self.q(st0 + db) if self.snap() else max(0.0, st0 + db)
            shift = ns - st0
            shift = max(shift, -min(st for _c, st, _l in group))
            dl = max(dl, -min(l for _c, _s, l in group))
            dl = min(dl, PR.N_LANES - 1 - max(l for _c, _s, l in group))
            for c, st, ln in group:
                c["start"] = round(st + shift, 4)
                c["lane"] = int(ln + dl)
            self.sh.touch(views=("playlist",))
        elif kind == "rsz":
            _k, x0, group = d
            dbeats = (pos.x() - x0) / self.ppb
            s = self.snap()
            for c, ol in group:
                nl = ol + dbeats
                if s > 0:
                    nl = max(s, round(nl / s) * s)
                nl = max(0.25, nl)
                mx = self.pl.max_len(c)
                if mx is not None:
                    nl = min(nl, mx)
                c["len"] = round(nl, 4)
            self.sh.touch(views=("playlist",))
        elif kind == "lsz":
            _k, x0, k, st0, ln0, off0 = d
            db = (pos.x() - x0) / self.ppb
            ns = self.q(st0 + db) if self.snap() else st0 + db
            ns = max(0.0, min(st0 + ln0 - 0.25, ns))
            spb = 60.0 / float(self.p["bpm"])
            off = off0 + (ns - st0) * spb
            if off < 0:
                ns = st0 - off0 / spb
                off = 0.0
            k["start"] = round(ns, 4)
            k["len"] = round(st0 + ln0 - ns, 4)
            k["off"] = round(off, 5)
            self.sh.touch(views=("playlist",))
        elif kind == "paint":
            _k, ln, lane, done = d
            b = self.q(self.xb(pos.x()), "floor")
            st = math.floor(b / ln) * ln
            if round(st, 4) not in done and st >= 0:
                new = self.pl.make_clip(st, lane)
                if new is not None and not any(c["lane"] == lane and c["start"] < st + ln and st < c["start"] + c["len"]
                                               for c in self.p["clips"]):
                    self.p["clips"].append(new)
                    done.add(round(st, 4))
                    self.sh.touch(views=("playlist",))

    def mouseReleaseEvent(self, e):
        d = self.drag
        self.drag = None
        if d is None:
            return
        if d[0] == "box" and self.box is not None:
            for k in self.p["clips"]:
                if self.box.intersects(self.clip_rect(k)) and k not in self.sel:
                    self.sel.append(k)
            self.box = None
            self.update()
        elif d[0] in ("move", "rsz", "lsz", "paint", "erase"):
            self.sh.touch(views=("playlist",))
            self.sync_bars()

    def mouseDoubleClickEvent(self, e):
        pos = e.position()
        if pos.x() < HEADW and pos.y() >= RULER:
            lane = self.yl(pos.y())
            if 0 <= lane < PR.N_LANES:
                self.rename_lane(lane)
            return
        k, _ = self.clip_at(pos)
        if k is None:
            return
        if k["kind"] == "pat":
            self.sh.select_pattern(k["ref"])
            self.sh.show_window("rack")
        else:
            self.sh.clip_props(k)

    def wheelEvent(self, e):
        dy = e.angleDelta().y()
        mods = e.modifiers()
        if mods & Qt.KeyboardModifier.ControlModifier:
            b = self.xb(e.position().x())
            f = 1.15 if dy > 0 else 1 / 1.15
            self.ppb = max(2.0, min(200.0, self.ppb * f))
            self.sx = max(0.0, b * self.ppb - (e.position().x() - HEADW))
            self.cache.clear()
        elif mods & Qt.KeyboardModifier.AltModifier:
            self.lh = max(24, min(140, self.lh + (4 if dy > 0 else -4)))
            self.cache.clear()
        elif mods & Qt.KeyboardModifier.ShiftModifier:
            self.sx = max(0.0, self.sx - dy)
        else:
            self.sy = max(0.0, min(PR.N_LANES * self.lh - self.grid().height(), self.sy - dy))
        self.sync_bars()
        self.update()

    def keyPressEvent(self, e):
        k = e.key()
        ctrl = bool(e.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.sel:
            self.sh.commit("Удалить клипы")
            for c in list(self.sel):
                if c in self.p["clips"]:
                    self.p["clips"].remove(c)
            self.sel = []
            self.sh.touch(views=("playlist",))
        elif ctrl and k == Qt.Key.Key_A:
            self.sel = list(self.p["clips"])
            self.update()
        elif ctrl and k == Qt.Key.Key_C and self.sel:
            t0 = min(c["start"] for c in self.sel)
            self.pl.clipboard = [dict(c, start=c["start"] - t0) for c in self.sel]
            U.hint_to(self, f"Скопировано клипов: {len(self.sel)}")
        elif ctrl and k == Qt.Key.Key_V and self.pl.clipboard:
            self.sh.commit("Вставить клипы")
            at = self.q(self.sh.song_beat())
            new = [dict(c, id=PR.uid(), start=round(at + c["start"], 4)) for c in self.pl.clipboard]
            self.p["clips"].extend(new)
            self.sel = new
            self.sh.touch(views=("playlist",))
        elif ctrl and k == Qt.Key.Key_D and self.sel:
            self.sh.commit("Повторить клипы")
            t0 = min(c["start"] for c in self.sel)
            t1 = max(c["start"] + c["len"] for c in self.sel)
            span = t1 - t0
            new = [dict(c, id=PR.uid(), start=round(c["start"] + span, 4)) for c in self.sel]
            self.p["clips"].extend(new)
            self.sel = new
            self.sh.touch(views=("playlist",))
        else:
            e.ignore()

    def _remove(self, k):
        if k in self.p["clips"]:
            self.p["clips"].remove(k)
        if k in self.sel:
            self.sel.remove(k)
        self.sh.touch(views=("playlist",))

    def _slice(self, k, at):
        if not (k["start"] + 0.01 < at < k["start"] + k["len"] - 0.01):
            return
        self.sh.commit("Разрезать клип")
        left = at - k["start"]
        right = dict(k, id=PR.uid(), start=round(at, 4), len=round(k["len"] - left, 4))
        if k["kind"] == "audio":
            spb = 60.0 / float(self.p["bpm"])
            right["off"] = round(float(k.get("off") or 0) + left * spb, 5)
            right["fin"] = 0.0
            k["fout"] = 0.0
        k["len"] = round(left, 4)
        self.p["clips"].append(right)
        self.sh.touch(views=("playlist",))

    # ── дорожки ── #
    def rename_lane(self, lane):
        ln = self.p["lanes"][lane]
        t, ok = QInputDialog.getText(self, "Дорожка", "Название дорожки:", text=ln["name"])
        if ok and t.strip():
            self.sh.commit("Название дорожки")
            ln["name"] = t.strip()[:30]
            self.update()

    def lane_menu(self, lane, gp):
        m = QMenu(self)
        m.addAction("Переименовать…", lambda: self.rename_lane(lane))
        m.addAction("Заглушить / включить", lambda: self._lane_mute(lane))
        m.addAction("Выделить все клипы дорожки", lambda: self._lane_select(lane))
        m.addAction("Очистить дорожку", lambda: self._lane_clear(lane))
        m.exec(gp)

    def _lane_mute(self, lane):
        self.sh.commit("Заглушить дорожку")
        ln = self.p["lanes"][lane]
        ln["mute"] = not ln.get("mute")
        self.sh.touch(views=("playlist",))

    def _lane_select(self, lane):
        self.sel = [c for c in self.p["clips"] if c["lane"] == lane]
        self.update()

    def _lane_clear(self, lane):
        if any(c["lane"] == lane for c in self.p["clips"]):
            self.sh.commit("Очистить дорожку")
            self.p["clips"] = [c for c in self.p["clips"] if c["lane"] != lane]
            self.sel = []
            self.sh.touch(views=("playlist",))

    # ── перетаскивание из браузера ── #
    def dragEnterEvent(self, e):
        md = e.mimeData()
        if (md.hasText() and md.text().startswith("echoes:")) or md.hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        pos = e.position()
        self.drop_preview = (self.q(self.xb(pos.x()), "floor"), self.yl(pos.y()))
        self.update()
        e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self.drop_preview = None
        self.update()

    def dropEvent(self, e):
        self.drop_preview = None
        pos = e.position()
        beat = self.q(self.xb(pos.x()), "floor") if pos.x() >= HEADW else 0.0
        lane = max(0, min(PR.N_LANES - 1, self.yl(pos.y())))
        md = e.mimeData()
        if md.hasUrls() and not (md.hasText() and md.text().startswith("echoes:")):
            # файлы из проводника: аудио — клипами друг под другом, папка — в «Мои сэмплы»
            import os
            import daw_samples as SM
            for i, u in enumerate(md.urls()[:16]):
                f = u.toLocalFile()
                if os.path.isdir(f):
                    if SM.add_folder(self.sh.S(), f):
                        self.sh.save_settings()
                    self.sh.browser.refresh_samples(rescan=True)
                    self.sh.hint(f"Папка «{os.path.basename(f)}» добавлена в «Мои сэмплы»")
                elif os.path.splitext(f)[1].lower() in SM.EXTS:
                    self.sh.drop_item(f"echoes:track:{f}", beat, min(PR.N_LANES - 1, lane + i))
            e.acceptProposedAction()
            return
        self.sh.drop_item(md.text(), beat, lane)
        e.acceptProposedAction()

    # ── рисование ── #
    def _pat_pix(self, k, w, h):
        pat = PR.pattern(self.p, k["ref"])
        if pat is None:
            return None
        key = ("p", k["ref"], round(k["len"], 3), int(w), int(h), str(pat.get("notes"))[:4000].__hash__(),
               PR.pattern_used_len(pat))
        px = self.cache.get(key)
        if px is not None:
            return px
        px = QPixmap(max(1, int(w)), max(1, int(h)))
        px.fill(Qt.GlobalColor.transparent)
        p = QPainter(px)
        plen = PR.pattern_used_len(pat)
        allnotes = [(cid, n) for cid, lst in pat.get("notes", {}).items() for n in lst]
        if allnotes:
            lo = min(n[2] for _c, n in allnotes)
            hi = max(n[2] for _c, n in allnotes)
            span = max(8, hi - lo + 1)
            reps = int(math.ceil(k["len"] / plen))
            for r in range(reps):
                for cid, n in allnotes:
                    t = r * plen + n[0]
                    if t >= k["len"]:
                        continue
                    c = PR.chan(self.p, cid)
                    col = QColor((c or {}).get("color") or "#cfe5b8").lighter(130)
                    x = t / k["len"] * w
                    ww = max(1.5, min(n[1], k["len"] - t) / k["len"] * w)
                    y = h - 2 - (n[2] - lo + 0.5) / span * (h - 4)
                    p.fillRect(QRectF(x, y - 1, ww, 2), col)
        p.end()
        self.cache[key] = px
        while len(self.cache) > 300:
            self.cache.popitem(last=False)
        return px

    def _audio_pix(self, k, w, h):
        src = PR.source(self.p, k["ref"])
        if src is None:
            return None
        ratio = PR.clip_ratio(self.p, k, src)
        a = self.sh.sources.get_loaded(src, float(k.get("semis") or 0), ratio)
        if a is None:
            return None
        rk = PR.recipe_key(src, float(k.get("semis") or 0), ratio)
        key = ("a", rk, round(float(k.get("off") or 0), 4), round(k["len"], 3), int(w), int(h),
               round(float(self.p["bpm"]), 3))
        px = self.cache.get(key)
        if px is not None:
            return px
        pk = PR.peaks(rk, a)
        hop = 512
        spb = 60.0 / float(self.p["bpm"])
        s0 = float(k.get("off") or 0) * ratio * D.SR / hop
        s1 = s0 + k["len"] * spb * D.SR / hop
        W = max(1, int(w))
        px = _wave_pixmap(pk, s0, s1, W, max(1, int(h)))
        self.cache[key] = px
        while len(self.cache) > 300:
            self.cache.popitem(last=False)
        return px

    def paintEvent(self, e):
        dpr = self.devicePixelRatioF()
        size = (self.width(), self.height(), dpr)
        if self._dirty or self._layer is None or self._layer[0] != size:
            pm = QPixmap(max(1, int(self.width() * dpr)), max(1, int(self.height() * dpr)))
            pm.setDevicePixelRatio(dpr)
            q = QPainter(pm)
            self._paint_static(q)
            self._layer = (size, pm)
            self._dirty = False
        p = QPainter(self)
        p.drawPixmap(0, 0, self._layer[1])
        self._paint_dynamic(p)
        p.end()

    def _paint_dynamic(self, p):
        """Только то, что движется при воспроизведении: курсор песни и треугольник на линейке."""
        g = self.grid()
        P = self.p
        p.setClipRect(g)
        sb = self.sh.song_beat_playing()
        if sb is not None:
            x = self.bx(sb)
            p.setPen(QPen(U.ACC, 1.5))
            p.drawLine(QPointF(x, g.top()), QPointF(x, g.bottom()))
        rr = QRectF(HEADW, 0, self.width() - HEADW, RULER)
        p.setClipRect(rr)
        x = self.bx(self.sh.song_beat())
        path = QPainterPath(QPointF(x - 6, 2))
        path.lineTo(QPointF(x + 6, 2))
        path.lineTo(QPointF(x, 12))
        path.closeSubpath()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillPath(path, U.ACC if P.get("mode") == "song" else U.DIM)
        p.setClipping(False)

    def _paint_static(self, p):
        w, h = self.width(), self.height()
        g = self.grid()
        P = self.p
        p.fillRect(QRectF(0, 0, w, h), QColor("#262c31"))
        # дорожки (фон)
        p.setClipRect(g)
        l0 = max(0, self.yl(g.top()))
        l1 = min(PR.N_LANES - 1, self.yl(g.bottom()))
        for lane in range(l0, l1 + 1):
            y = self.ly(lane)
            col = QColor("#2f373c") if lane % 2 == 0 else QColor("#2b3237")
            if P["lanes"][lane].get("mute"):
                col = col.darker(130)
            p.fillRect(QRectF(g.left(), y, g.width(), self.lh), col)
            p.setPen(QColor("#20262a"))
            p.drawLine(QPointF(g.left(), y + self.lh - 0.5), QPointF(g.right(), y + self.lh - 0.5))
        b0 = max(0, int(self.xb(g.left())) - 1)
        b1 = int(self.xb(g.right())) + 2
        step = 1 if self.ppb >= 8 else (4 if self.ppb >= 2.5 else 16)
        for b in range(b0 - b0 % step, b1, step):
            x = self.bx(b)
            p.setPen(QColor(0, 0, 0, 110) if b % 4 == 0 else QColor(255, 255, 255, 14))
            p.drawLine(QPointF(x, g.top()), QPointF(x, g.bottom()))
        # клипы
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        hdr = 14
        for k in P["clips"]:
            r = self.clip_rect(k)
            if r.right() < g.left() or r.left() > g.right() or r.bottom() < g.top() or r.top() > g.bottom():
                continue
            sel = k in self.sel
            if k["kind"] == "pat":
                pat = PR.pattern(P, k["ref"])
                col = QColor((pat or {}).get("color") or "#6f8f4f")
            else:
                c = PR.chan(P, k.get("chan"))
                col = QColor((c or {}).get("color") or "#5e86b8").darker(115)
            if k.get("mute"):
                col = QColor("#4a5054")
            body = QColor(col)
            body.setAlpha(200)
            p.setPen(QPen(QColor("#ffffff") if sel else col.darker(160), 1.5 if sel else 1))
            p.setBrush(body.darker(125))
            p.drawRoundedRect(r, 3, 3)
            hr = QRectF(r.left(), r.top(), r.width(), min(hdr, r.height()))
            path = QPainterPath()
            path.addRoundedRect(hr, 3, 3)
            p.fillPath(path, col.lighter(115) if not sel else col.lighter(150))
            inner = QRectF(r.left() + 1, r.top() + hdr, r.width() - 2, r.height() - hdr - 1)
            if inner.height() > 4 and inner.width() > 2:
                vis_l = max(inner.left(), g.left())
                vis_r = min(inner.right(), g.right())
                pix = self._pat_pix(k, inner.width(), inner.height()) if k["kind"] == "pat" else \
                    self._audio_pix(k, inner.width(), inner.height())
                if pix is not None and vis_r > vis_l:
                    sx = vis_l - inner.left()
                    p.drawPixmap(QRectF(vis_l, inner.top(), vis_r - vis_l, inner.height()), pix,
                                 QRectF(sx, 0, vis_r - vis_l, inner.height()))
                elif pix is None and k["kind"] == "audio":
                    p.setPen(U.DIM)
                    p.setFont(U.font(10))
                    p.drawText(inner.adjusted(4, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, "готовлю звук…")
                if k["kind"] == "audio" and (k.get("fin") or k.get("fout")):
                    p.setPen(QPen(QColor(255, 255, 255, 120), 1))
                    if k.get("fin"):
                        fx = r.left() + float(k["fin"]) * self.ppb
                        p.drawLine(QPointF(r.left(), inner.bottom()), QPointF(fx, inner.top()))
                    if k.get("fout"):
                        fx = r.right() - float(k["fout"]) * self.ppb
                        p.drawLine(QPointF(fx, inner.top()), QPointF(r.right(), inner.bottom()))
            p.setPen(QColor("#121416"))
            p.setFont(U.font(10, True))
            p.drawText(hr.adjusted(4, 0, -2, 0), Qt.AlignmentFlag.AlignVCenter,
                       p.fontMetrics().elidedText(self.pl.clip_name(k), Qt.TextElideMode.ElideRight,
                                                  int(max(0, hr.width() - 6))))
        if self.box is not None:
            p.setBrush(QColor(240, 163, 58, 40))
            p.setPen(QPen(U.ACC, 1, Qt.PenStyle.DashLine))
            p.drawRect(self.box)
        if self.drop_preview is not None:
            b, lane = self.drop_preview
            p.setBrush(QColor(240, 163, 58, 60))
            p.setPen(QPen(U.ACC, 1))
            p.drawRect(QRectF(self.bx(b), self.ly(lane), 4 * self.ppb, self.lh))
        # конец песни и указатель
        xe = self.bx(self.song_end())
        p.setPen(QPen(QColor(255, 255, 255, 40), 1, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(xe, g.top()), QPointF(xe, g.bottom()))
        p.setClipping(False)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        # линейка
        rr = QRectF(HEADW, 0, w - HEADW, RULER)
        p.fillRect(rr, QColor("#383f45"))
        p.setClipRect(rr)
        p.setFont(U.font(10))
        bar_step = 1 if self.ppb * 4 >= 28 else (4 if self.ppb * 4 >= 8 else 16)
        for bar in range(b0 // 4, b1 // 4 + 1):
            x = self.bx(bar * 4)
            if bar % bar_step == 0:
                p.setPen(U.TEXT)
                p.drawText(QPointF(x + 3, 15), str(bar + 1))
                p.setPen(QColor("#6a737b"))
                p.drawLine(QPointF(x, 12), QPointF(x, RULER))
        p.setClipping(False)
        # заголовки дорожек
        p.fillRect(QRectF(0, 0, HEADW, RULER), QColor("#2b3136"))
        p.setPen(U.DIM)
        p.setFont(U.font(10, True))
        p.drawText(QRectF(8, 0, HEADW, RULER), Qt.AlignmentFlag.AlignVCenter,
                   "ПЕСНЯ" if P.get("mode") == "song" else "ПАТТЕРН (L — песня)")
        hr_ = QRectF(0, RULER, HEADW, g.height())
        p.setClipRect(hr_)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for lane in range(l0, l1 + 1):
            y = self.ly(lane)
            ln = P["lanes"][lane]
            p.fillRect(QRectF(0, y, HEADW, self.lh), QColor("#363d43") if lane % 2 == 0 else QColor("#323940"))
            p.setPen(QColor("#22272b"))
            p.drawLine(QPointF(0, y + self.lh - 0.5), QPointF(HEADW, y + self.lh - 0.5))
            on = not ln.get("mute")
            p.setBrush(U.GRN if on else QColor("#3a4146"))
            p.setPen(QPen(QColor("#15181a"), 1))
            p.drawEllipse(QPointF(13, y + 14), 5, 5)
            p.setPen(U.TEXT if on else U.DIM)
            p.setFont(U.font(11))
            p.drawText(QRectF(24, y + 4, HEADW - 28, 20), Qt.AlignmentFlag.AlignVCenter,
                       p.fontMetrics().elidedText(ln["name"], Qt.TextElideMode.ElideRight, HEADW - 30))
        p.setClipping(False)
        p.setPen(QColor("#1d2124"))
        p.drawLine(QPointF(HEADW - 0.5, 0), QPointF(HEADW - 0.5, h))
        p.end()


class Playlist(QWidget):
    """Содержимое окна «Плейлист» + панель инструментов."""

    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self.sh = shell
        self.tool = "draw"
        self.place = None                     # что ставить: ("pat", pid) или ("audio", клип-образец)
        self.clipboard = []
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.canvas = PlaylistCanvas(self)
        v.addWidget(self.canvas, 1)
        self.toolbar = self._make_toolbar()

    def _make_toolbar(self):
        tb = U.Toolbar()
        self.tool_btns = {}
        for t, ic, tip, txt in (("draw", "pencil", "Кисть: ставить и двигать клипы (щелчок по клипу — выбрать его образцом)", "Кисть"),
                                ("paint", "brush", "Рисовать серией: тянуть — клипы подряд", "Серия"),
                                ("erase", "erase", "Ластик: удалять клипы", "Ластик"),
                                ("slice", "slice", "Нож: разрезать клип", "Нож"),
                                ("select", "select", "Выделение рамкой", "Рамка"),
                                ("mute", "mute", "Заглушить клип", "Тишина")):
            b = U.IconButton(ic, tip, 24, checkable=True, text=txt)
            b.clicked.connect(lambda _=False, t=t: self.set_tool(t))
            self.tool_btns[t] = b
            tb.add(b)
        self.tool_btns["draw"].setChecked(True)
        tb.space(8)
        tb.add(U.label("Сетка"))
        self.snap_cb = QComboBox()
        for nm, _v in SNAPS:
            self.snap_cb.addItem(nm)
        self.snap_cb.setCurrentIndex(0)
        tb.add(self.snap_cb)
        tb.space(8)
        tb.add(U.label("Ставить"))
        self.place_cb = QComboBox()
        self.place_cb.setMinimumWidth(170)
        U.tip(self.place_cb, "Что ставит кисть: паттерн или аудиоклип")
        self.place_cb.activated.connect(self._pick_place)
        tb.add(self.place_cb)
        tb.stretch()
        return tb

    def set_tool(self, t):
        self.tool = t
        for k, b in self.tool_btns.items():
            b.setChecked(k == t)

    def snap_val(self):
        return SNAPS[self.snap_cb.currentIndex()][1]

    def _pick_place(self, i):
        d = self.place_cb.itemData(i)
        if d and d[0] == "pat":
            self.place = ("pat", d[1])
            self.sh.select_pattern(d[1], quiet=True)
        elif d and d[0] == "src":
            k = next((c for c in self.sh.p["clips"] if c["kind"] == "audio" and c["ref"] == d[1]), None)
            if k is not None:
                self.place = ("audio", dict(k))

    def set_place(self, k):
        if k["kind"] == "pat":
            self.place = ("pat", k["ref"])
        else:
            self.place = ("audio", dict(k))
        self.refresh_place()

    def refresh_place(self):
        cb = self.place_cb
        cb.blockSignals(True)
        cb.clear()
        for pt in self.sh.p["patterns"]:
            cb.addItem("Паттерн: " + pt["name"], ("pat", pt["id"]))
        seen = set()
        for k in self.sh.p["clips"]:
            if k["kind"] == "audio" and k["ref"] not in seen:
                seen.add(k["ref"])
                src = PR.source(self.sh.p, k["ref"])
                if src:
                    cb.addItem("Аудио: " + src["name"], ("src", k["ref"]))
        cur = None
        if self.place and self.place[0] == "pat":
            cur = ("pat", self.place[1])
        elif self.place and self.place[0] == "audio":
            cur = ("src", self.place[1]["ref"])
        else:
            pt = PR.cur_pattern(self.sh.p)
            cur = ("pat", pt["id"]) if pt else None
        for i in range(cb.count()):
            if cb.itemData(i) == cur:
                cb.setCurrentIndex(i)
                break
        cb.blockSignals(False)

    def make_clip(self, start, lane):
        P = self.sh.p
        if self.place and self.place[0] == "audio":
            k = dict(self.place[1], id=PR.uid(), start=round(start, 4), lane=int(lane))
            if PR.source(P, k["ref"]) is None:
                return None
            return k
        pid = self.place[1] if self.place and self.place[0] == "pat" else (PR.cur_pattern(P) or {}).get("id")
        pat = PR.pattern(P, pid)
        if pat is None:
            return None
        return {"id": PR.uid(), "kind": "pat", "ref": pid, "lane": int(lane), "start": round(start, 4),
                "len": PR.pattern_used_len(pat)}

    def max_len(self, k):
        if k["kind"] != "audio":
            return None
        src = PR.source(self.sh.p, k["ref"])
        if src is None:
            return None
        ratio = PR.clip_ratio(self.sh.p, k, src)
        a = self.sh.sources.get_loaded(src, float(k.get("semis") or 0), ratio)
        if a is None:
            return None
        spb = 60.0 / float(self.sh.p["bpm"])
        return max(0.25, (len(a) / D.SR - float(k.get("off") or 0) * ratio) / spb)

    def clip_name(self, k):
        if k["kind"] == "pat":
            pat = PR.pattern(self.sh.p, k["ref"])
            return (pat or {}).get("name", "?")
        src = PR.source(self.sh.p, k["ref"])
        return (src or {}).get("name", "аудио")

    def refresh(self):
        self.canvas.sel = [c for c in self.canvas.sel if c in self.sh.p["clips"]]
        self.refresh_place()
        self.canvas.sync_bars()
        self.canvas.update()

    def tick(self):
        if self.isVisible():
            self.canvas.update_dynamic()

    def scroll_to_beat(self, b):
        g = self.canvas.grid()
        x = self.canvas.bx(b)
        if x < g.left() or x > g.right() - 40:
            self.canvas.sx = max(0.0, b * self.canvas.ppb - g.width() * 0.2)
            self.canvas.sync_bars()
            self.canvas.update()
