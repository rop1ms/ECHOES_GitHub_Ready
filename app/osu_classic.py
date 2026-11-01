# osu_classic.py
"""
esu! в стиле «классика» — ещё ближе к osu! (stable), по образцу записей игры:

  * выбор песни: карусель цветных панелей (наборы — розовые / оранжевые, если карта уже готова; сложности — синие,
    выбранная — белая), миниатюра, «исполнитель // автор карты», ряд из 10 звёзд, маленькая буква лучшей оценки;
    панели въезжают справа лесенкой, наведённая выдвигается влево; «Случайно» прокручивает карусель, как в osu!;
  * шапка с синей кромкой и изгибом, «Группировать» / «Сортировать» с вкладками, строка «Поиск:»;
  * «Локальный топ» и «Рекордов пока нет!» слева; низ — «назад», Тема / Моды / Случайно / Карта (F1–F3),
    карточка игрока и пульсирующая печенька esu! в углу;
  * моды — на весь экран: «Упрощение / Усложнение / Особые», множитель очков, «1. Сбросить все моды», «2. Закрыть»;
  * меню «Карта…», сортировка и группировка — диалог osu!: вопрос сверху, цветные кнопки «1. … 2. …»;
  * курсор osu! со шлейфом и в меню (из скина, если он есть; при нажатии курсор увеличивается — CursorExpand),
    снег из скина (menu-snow) в главном меню, надпись welcome_text из скина на заставке.

Подключается при запуске (main.py → install(), после osu_y2k) и подменяет методы экранов снаружи — osu_theme.py
не меняется. В стиле Y2K (и если в настройках выключено «Классика как в osu!») работает прежняя отрисовка.
"""
from __future__ import annotations

import math
import random
import time

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import (QBrush, QColor, QCursor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
                         QPolygonF)
from PyQt6.QtWidgets import QApplication, QMenu, QWidget

_O: dict = {}
T = None            # osu_theme
SK = None           # osu_skin

PANEL_PINK = QColor(232, 74, 152)
PANEL_ORANGE = QColor(240, 136, 30)
PANEL_BLUE = QColor(26, 158, 230)
EDGE = QColor(80, 165, 255)
DARK_TXT = QColor(38, 38, 50)


def _tr(s: str) -> str:
    """Перевод строки до склейки с номером/префиксом (словарь ищет строку целиком)."""
    try:
        import i18n
        return i18n.tr(s)
    except Exception:                                      # noqa: BLE001
        return s


def _ease(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


def _on(obj) -> bool:
    sh = getattr(obj, "shell", obj)
    try:
        return bool(sh.osu_look()) and bool(sh.S().get("classic_plus", True))
    except Exception:                                      # noqa: BLE001
        return False


def _st(obj) -> dict:
    d = obj.__dict__.get("_oc")
    if d is None:
        d = obj._oc = {}
    return d


def _txt(p, r, s, px, color=QColor(255, 255, 255), weight=QFont.Weight.Normal,
         align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elide=True):
    T.text(p, r, s, px, color, weight, align, elide=elide)


def _shadow_txt(p, r, s, px, color=QColor(255, 255, 255), weight=QFont.Weight.Normal,
                align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter):
    _txt(p, r.translated(1.2, 1.4), s, px, QColor(0, 0, 0, 150), weight, align)
    _txt(p, r, s, px, color, weight, align)


# ------------------------------------------------------------------ #
#  Выбор песни                                                        #
# ------------------------------------------------------------------ #

def _sizes(self):
    if not _on(self):
        return _O["sizes"](self)
    H = self.height()
    ph = max(64.0, H * 0.104)
    return ph, ph * 0.94, max(4.0, H * 0.006)


def _band(H):
    return H * 0.105, H * 0.19          # высота шапки справа / слева (под «Локальный топ»)


def _bot(H):
    return H * 0.115


def _scores_cache(self):
    st = _st(self)
    now = time.perf_counter()
    if now - st.get("sc_t", 0.0) > 3.0:
        st["sc_t"] = now
        try:
            st["sc"] = T.load_scores()
        except Exception:                                  # noqa: BLE001
            st["sc"] = {}
    return st.get("sc") or {}


def _best_grade(self, m, ck=None) -> str | None:
    """Лучшая оценка карты; кэш по (трек, сложность) — map_key трогает диск, а зовётся на каждую панель."""
    st = _st(self)
    sc = _scores_cache(self)
    if st.get("gr_src") is not sc:
        st["gr_src"] = sc
        st["gr"] = {}
    cache = st["gr"]
    if ck is not None and ck in cache:
        return cache[ck]
    g = None
    if m:
        try:
            lst = sc.get(T.map_key(m)) or []
            if lst:
                g = max(lst, key=lambda s: s.get("score", 0)).get("grade")
        except Exception:                                  # noqa: BLE001
            g = None
    if ck is not None and m:
        cache[ck] = g
    return g


def _diff_map(self, t, key):
    """Карта сложности, если уже загружена (для буквы оценки)."""
    if T.is_osu_track(t):
        return {"osu_md5": key}
    st = self.shell.sets.get(str(t.get("path")))
    if st and st.get("data"):
        return st["data"]["maps"].get(key)
    return None


def _panel_bg(q, r, col, border_a=220, border_w=2.0):
    """Панель карусели osu!: заливка цветом, тонкая белая рамка, скруглённый левый край, мягкая тень."""
    rad = r.height() * 0.07
    path = QPainterPath()
    path.addRoundedRect(r.adjusted(1, 1, rad * 2, -1), rad, rad)
    q.setPen(Qt.PenStyle.NoPen)
    q.setBrush(QColor(0, 0, 0, 70))
    q.drawPath(path.translated(2, 3))
    g = QLinearGradient(r.topLeft(), r.bottomLeft())
    g.setColorAt(0, SK.lighter(col, 0.10))
    g.setColorAt(1, col)
    q.setBrush(QBrush(g))
    q.drawPath(path)
    q.setBrush(Qt.BrushStyle.NoBrush)
    q.setPen(QPen(QColor(255, 255, 255, border_a), border_w))
    q.drawPath(path)
    return path


def _thumb_into(q, r, th, col, clip_path):
    """Миниатюра слева, обрезанная по панели, с переходом в её цвет."""
    tw = r.height() * 1.45
    tr = QRectF(r.left() + 2, r.top() + 2, tw, r.height() - 4)
    q.save()
    q.setClipPath(clip_path)
    if th is not None and not th.isNull():
        sw, sh_ = th.width(), th.height()
        k = max(tr.width() / sw, tr.height() / sh_)
        cw, ch = tr.width() / k, tr.height() / k
        q.drawPixmap(tr, th, QRectF((sw - cw) / 2, (sh_ - ch) / 2, cw, ch))
    else:
        q.fillRect(tr, SK.darker(col, 0.25))
    g = QLinearGradient(tr.topLeft(), tr.topRight())
    g.setColorAt(0.55, QColor(col.red(), col.green(), col.blue(), 0))
    g.setColorAt(1.0, QColor(col.red(), col.green(), col.blue(), 235))
    q.fillRect(tr, QBrush(g))
    q.restore()
    return tr.right()


def _stars_row(q, x, cy, h, s, fg):
    """Десять мест под звёзды: целые — полные, дробная — меньше, остальные — точки (как в osu!)."""
    step = h * 0.33
    for i in range(10):
        f = max(0.0, min(1.0, s - i))
        cx = x + i * step + step / 2
        q.setPen(Qt.PenStyle.NoPen)
        if f > 0.05:
            rad = h * 0.12 * (0.55 + 0.45 * f)
            q.setBrush(QColor(fg.red(), fg.green(), fg.blue(), 245))
            q.drawPolygon(SK.star_shape(cx, cy, rad, rad * 0.45, 5, -math.pi / 2))
        else:
            q.setBrush(QColor(fg.red(), fg.green(), fg.blue(), 70))
            q.drawEllipse(QPointF(cx, cy), h * 0.022, h * 0.022)


def _paint_track(self, q, r, t, sel, ready, dots, th):
    col = PANEL_ORANGE if ready else PANEL_PINK
    if sel:
        col = SK.lighter(col, 0.12)
    col = QColor(col.red(), col.green(), col.blue(), 225)
    path = _panel_bg(q, r, col, 255 if sel else 200, 2.4 if sel else 1.6)
    x = _thumb_into(q, r, th, col, path) + r.height() * 0.14
    h = r.height()
    w = r.width() * 0.62
    _shadow_txt(q, QRectF(x, r.top() + h * 0.08, w, h * 0.36), T.track_title(t), h * 0.25)
    by = (t.get("creator") or "osu!") if T.is_osu_track(t) else "ECHOES AI"
    _shadow_txt(q, QRectF(x, r.top() + h * 0.42, w, h * 0.24), f"{t.get('artist') or '—'} // {by}", h * 0.165)
    xx = x
    for s in dots:
        q.setPen(QPen(QColor(255, 255, 255, 200), 1))
        q.setBrush(T.star_color(s))
        q.drawEllipse(QPointF(xx + h * 0.07, r.top() + h * 0.8), h * 0.06, h * 0.06)
        xx += h * 0.17
    if not dots and not ready:
        _txt(q, QRectF(x, r.top() + h * 0.66, w, h * 0.26), "карты нет — F4", h * 0.15, QColor(255, 255, 255, 200))


def _paint_diff(self, q, r, t, meta, sel, state, th, grade):
    s = meta.get("stars")
    col = QColor(255, 255, 255, 238) if sel else QColor(PANEL_BLUE.red(), PANEL_BLUE.green(), PANEL_BLUE.blue(), 220)
    path = _panel_bg(q, r, col, 255, 2.0 if sel else 1.6)
    x = _thumb_into(q, r, th, QColor(col.red(), col.green(), col.blue()), path) + r.height() * 0.12
    h = r.height()
    w = r.width() * 0.6
    fg = DARK_TXT if sel else QColor(255, 255, 255)
    if grade:
        try:
            gp = self.shell.grade_pix(grade, h * 0.4, self.devicePixelRatioF())
            T.blit(q, gp, x + h * 0.2, r.top() + h * 0.42, 1.0, 1.0)
            x += h * 0.44
        except Exception:                                  # noqa: BLE001
            pass
    fade = QColor(fg.red(), fg.green(), fg.blue(), 255 if sel else 120)
    _txt(q, QRectF(x, r.top() + h * 0.04, w, h * 0.3), T.track_title(t), h * 0.21, fade)
    by = meta.get("creator") or ((t.get("creator") or "osu!") if T.is_osu_track(t) else "ECHOES AI")
    _txt(q, QRectF(x, r.top() + h * 0.3, w, h * 0.22), f"{t.get('artist') or '—'} // {by}", h * 0.15, fade)
    _txt(q, QRectF(x, r.top() + h * 0.5, w, h * 0.24), meta.get("name") or "?", h * 0.18, fg, QFont.Weight.Bold)
    if s is not None:
        _stars_row(q, x - h * 0.03, r.top() + h * 0.84, h, s, fg)
        _txt(q, QRectF(x + h * 3.45, r.top() + h * 0.72, h * 1.4, h * 0.24), f"★ {s:.2f}", h * 0.14,
             QColor(fg.red(), fg.green(), fg.blue(), 200), QFont.Weight.DemiBold)
    elif state:
        _txt(q, QRectF(x, r.top() + h * 0.72, w, h * 0.24), state, h * 0.14,
             QColor(255, 120, 120) if state.startswith("не удалось") else fg)


def _draw_carousel(self, p, W, H):
    if not _on(self):
        return _O["carousel"](self, p, W, H)
    st = _st(self)
    now = time.perf_counter()
    dt = min(0.05, now - st.get("ct", now))
    st["ct"] = now
    enter = st.get("enter", 0.0)
    offs = st.setdefault("offs", {})
    cur = self.cur()
    s_cur = self._set()
    th_cur = self._thumb(cur) if cur is not None else None
    pw = int(W * 0.62 + 60)
    seen = set()
    for i, (kind, key, r) in enumerate(self._entries()):
        ident = (kind, key)
        hov = self.hover == ident
        tgt = -W * 0.024 if hov else 0.0
        o = offs.get(ident, tgt)
        o += (tgt - o) * min(1.0, dt * 14)
        offs[ident] = o
        seen.add(ident)
        k = (now - enter - i * 0.03) / 0.4                  # вход: лесенкой справа
        slide = 0.0 if k >= 1 else (1 - _ease(k)) * W * 0.55
        x, y = round(r.left() + o + slide), round(r.top())
        if kind == "track":
            t = self.tracks[key]
            sel = key == self.sel
            ready = T.is_osu_track(t)
            dots = ()
            if T.is_osu_track(t):
                e = self._osu_entry(t)
                dots = tuple(round(d["stars"], 1) for d in (e or {}).get("diffs", [])[:12])
            else:
                s2 = self.shell.sets.get(str(t.get("path")))
                if s2 and s2.get("data"):
                    ready = True
                    dots = tuple(round(s2["data"]["maps"][k2]["stars"], 1) for k2 in T.DIFF_KEYS
                                 if s2["data"]["maps"].get(k2))
            th = self._thumb(t)
            pk = ("oc_t", self.shell.cover_of(t), str(t.get("path")), sel, ready, dots, th is not None)
            pm = self._panel(pk, pw, r.height(), lambda q, rr, t=t, sel=sel, ready=ready, dots=dots, th=th:
                             _paint_track(self, q, rr, t, sel, ready, dots, th))
        else:
            t = cur
            sel = key == self._cur_diff()
            meta = self._diff_meta(t, key)
            state = ""
            if meta.get("stars") is None and s_cur:
                if s_cur.get("state") == "busy":
                    f, s = s_cur.get("prog", (0.0, ""))
                    state = f"{s} {int(f * 100)}%"
                elif s_cur.get("state") == "error":
                    state = "не удалось: " + s_cur.get("err", "")
                elif s_cur.get("state") == "idle":
                    state = "карты нет — «Сгенерировать карту» (F4)"
            grade = _best_grade(self, _diff_map(self, t, key), (str(t.get("path")), key))
            pk = ("oc_d", str(t.get("path")), key, sel, meta.get("name"), meta.get("stars"), state, grade,
                  th_cur is not None)
            pm = self._panel(pk, pw, r.height(), lambda q, rr, t=t, meta=meta, sel=sel, state=state, g=grade:
                             _paint_diff(self, q, rr, t, meta, sel, state, th_cur, g))
        p.drawPixmap(QPointF(x, y), pm)
    for k in [k for k in offs if k not in seen]:
        del offs[k]


def _header_path(W, H):
    hb, hl = _band(H)
    x1, x2 = W * 0.255, W * 0.3
    path = QPainterPath()
    path.moveTo(0, hl)
    path.lineTo(x1, hl)
    path.cubicTo(x1 + (x2 - x1) * 0.5, hl, x1 + (x2 - x1) * 0.5, hb, x2, hb)
    path.lineTo(W, hb)
    return path


def _dropdown(p, r, label, hov):
    p.setPen(QPen(QColor(255, 255, 255, 230 if hov else 170), 1.2))
    p.setBrush(QColor(0, 0, 0, 200 if hov else 150))
    p.drawRect(r)
    _txt(p, r.adjusted(6, 0, -r.height(), 0), label, r.height() * 0.62)
    c = QPointF(r.right() - r.height() * 0.55, r.center().y())
    a = r.height() * 0.2
    p.setPen(QPen(QColor(255, 255, 255), max(1.5, r.height() * 0.09), Qt.PenStyle.SolidLine,
                  Qt.PenCapStyle.RoundCap))
    p.drawPolyline(QPolygonF([QPointF(c.x() - a, c.y() - a * 0.5), QPointF(c.x(), c.y() + a * 0.5),
                              QPointF(c.x() + a, c.y() - a * 0.5)]))


def _draw_info(self, p, W, H, top):
    """Шапка — картинкой: перерисовывается, только когда меняется то, что в ней показано."""
    if not _on(self):
        return _O["info"](self, p, W, H, top)
    t = self.cur()
    s = self._set() or {}
    prog = s.get("prog", (0.0, ""))
    hv = self.hover if self.hover and self.hover[0] == "btn" else None
    key = (int(W), int(H), round(self.devicePixelRatioF(), 2), str((t or {}).get("path")), self._cur_diff() if t else None,
           tuple(sorted(self.mods)), self.coll, self.sort, self.search,
           (int(time.perf_counter() * 2) % 2) if self.search else 0, hv, s.get("state"),
           (round(prog[0], 2), prog[1]) if isinstance(prog, (tuple, list)) else None, len(self.tracks),
           self._map() is not None, self.edit_mode)
    st = _st(self)
    c = st.get("hdr")
    if c is None or c[0] != key:
        pm = SK._pm(W, H * 0.25, self.devicePixelRatioF())
        q = SK._painter(pm)
        _draw_info_raw(self, q, W, H)
        q.end()
        st["hdr"] = c = (key, pm, dict(self._btn_rects))
    self._btn_rects = dict(c[2])
    p.drawPixmap(QPointF(0, 0), c[1])


def _draw_info_raw(self, p, W, H):
    self._btn_rects = {}
    hb, hl = _band(H)
    edge = _header_path(W, H)
    fill = QPainterPath(edge)
    fill.lineTo(W, 0)
    fill.lineTo(0, 0)
    fill.closeSubpath()
    p.fillPath(fill, QColor(0, 0, 0, 205))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(EDGE.red(), EDGE.green(), EDGE.blue(), 70), 6))
    p.drawPath(edge)
    p.setPen(QPen(EDGE, 2.2))
    p.drawPath(edge)
    t = self.cur()
    lw = W * 0.4
    if t is None:
        _shadow_txt(p, QRectF(12, 4, lw, H * 0.05), self.empty_text(), H * 0.022)
    else:
        m = self._map()
        meta = self._diff_meta(t, self._cur_diff())
        _shadow_txt(p, QRectF(10, 0, lw, H * 0.04), f"{t.get('artist') or '—'} - {T.track_title(t)} [{meta['name']}]",
                    H * 0.027)
        if T.is_osu_track(t):
            sub = f"Карта: {meta.get('creator') or t.get('creator') or '—'}"
        elif self.edit_mode:
            sub = "Режим редактора: Enter или щелчок по сложности откроет редактор карты"
        elif m is None and (self._set() or {}).get("state") != "busy":
            sub = "Карты ещё нет — нажмите «Сгенерировать карту» (F4)"
        else:
            sub = "Карта: ECHOES AI"
        _shadow_txt(p, QRectF(10, H * 0.037, lw, H * 0.022), sub, H * 0.016)
        if m:
            rate = 1.5 if (self.mods & {"DT", "NC"}) else 0.75 if "HT" in self.mods else 1.0
            cs, ar, od, hp = T.G.apply_mods(m["cs"], m["ar"], m["od"], m["hp"], self.mods)
            ar2, od2 = T.G.effective_ar_od(ar, od, rate)
            n = m["n_circles"] + m["n_sliders"] + m["n_spinners"]
            _shadow_txt(p, QRectF(10, H * 0.058, lw, H * 0.022),
                        f"Длина: {T.fmt_time(m['length'] / rate)}   BPM: {m['bpm'] * rate:.0f}   Объекты: {n}",
                        H * 0.017, weight=QFont.Weight.Bold)
            _shadow_txt(p, QRectF(10, H * 0.079, lw, H * 0.02),
                        f"Круги: {m['n_circles']}   Слайдеры: {m['n_sliders']}   Спиннеры: {m['n_spinners']}",
                        H * 0.015)
            _shadow_txt(p, QRectF(10, H * 0.098, lw, H * 0.02),
                        f"CS:{cs:.1f}  AR:{ar2:.1f}  OD:{od2:.1f}  HP:{hp:.1f}  Звёзды: {m['stars']:.2f}",
                        H * 0.0145, QColor(255, 255, 255, 215))
        else:
            st = self._set()
            if st and st.get("state") == "busy":
                f, s = st.get("prog", (0.0, ""))
                label = "Открываю карту" if T.is_osu_track(t) else "Модель слушает трек"
                _shadow_txt(p, QRectF(10, H * 0.06, lw, H * 0.022), f"{label}: {s}", H * 0.016)
                T.rrect(p, QRectF(10, H * 0.09, lw * 0.7, 5), 2.5, QColor(255, 255, 255, 40))
                T.rrect(p, QRectF(10, H * 0.09, lw * 0.7 * f, 5), 2.5, T.PINK)
    # «Локальный топ» — под сведениями, на левой части шапки
    rl = QRectF(8, H * 0.152, W * 0.22, H * 0.03)
    _dropdown(p, rl, "Попытки и рекорды", self.hover == ("btn", "attempts"))
    self._btn_rects["attempts"] = rl
    # справа: Группировать / Сортировать
    fs = H * 0.034
    _txt(p, QRectF(W * 0.415, H * 0.008, W * 0.15, H * 0.05), "Группировать", fs, QColor(130, 200, 255),
         align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    rc = QRectF(W * 0.57, H * 0.02, W * 0.135, H * 0.03)
    _dropdown(p, rc, self._coll_label(), self.hover == ("btn", "coll"))
    _txt(p, QRectF(W * 0.705, H * 0.008, W * 0.14, H * 0.05), "Сортировать", fs, QColor(150, 235, 120),
         align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    rs = QRectF(W * 0.85, H * 0.02, W * 0.14, H * 0.03)
    sort_names = {"title": "По названию", "artist": "По исполнителю", "length": "По длине", "added": "По добавлению"}
    _dropdown(p, rs, sort_names.get(self.sort, self.sort), self.hover == ("btn", "sort"))
    self._btn_rects["coll"], self._btn_rects["sort"] = rc, rs
    tabs = (("tab_colls", "Коллекции", False), ("tab_mine", "Моя музыка", self.coll == self.COLL_MINE),
            ("tab_osu", "Карты osu!", self.coll == self.COLL_OSU),
            ("tab_all", "Всё вместе", self.coll in (None, self.COLL_ALL)))
    tx, tw, ty, th = W * 0.555, W * 0.108, H * 0.067, H * 0.028
    for i, (k, label, on) in enumerate(tabs):
        r = QRectF(tx + i * (tw + 2), ty, tw, th)
        hov = self.hover == ("btn", k)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255) if on else (QColor(250, 70, 110) if hov else QColor(220, 30, 80)))
        p.drawRect(r)
        _txt(p, r, label, th * 0.56, DARK_TXT if on else QColor(255, 255, 255), QFont.Weight.Bold,
             Qt.AlignmentFlag.AlignCenter)
        self._btn_rects[k] = r
    # строка поиска
    r = QRectF(W * 0.735, hb + 2, W * 0.265, H * 0.034)
    p.fillRect(r, QColor(60, 60, 64, 175))
    on = int(time.perf_counter() * 2) % 2
    pf = r.height() * 0.58
    _txt(p, QRectF(r.left() + 12, r.top(), W * 0.06, r.height()), "Поиск:", pf, QColor(150, 235, 90),
         QFont.Weight.Bold)
    _txt(p, QRectF(r.left() + 12 + W * 0.055, r.top(), r.width() - W * 0.12, r.height()),
         (self.search + ("▏" if on else "")) if self.search else "введите название", pf, QColor(255, 255, 255),
         QFont.Weight.Bold)
    _txt(p, QRectF(r.left(), r.bottom() + 1, r.width() - 8, H * 0.022), T.n_tracks(len(self.tracks)), H * 0.014,
         QColor(255, 255, 255, 170), align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)


def _draw_scores(self, p, W, H, top, bot):
    if not _on(self):
        return _O["scores"](self, p, W, H, top, bot)
    hb, hl = _band(H)
    bot = _bot(H)
    m = self._map()
    t = self.cur()
    x, y, w = 14, hl + H * 0.02, W * 0.3
    S = self.shell.S()
    if t is not None and not T.is_osu_track(t):
        busy = (self._set() or {}).get("state") == "busy"
        rg = QRectF(x, y, w, H * 0.05 if m is None else H * 0.034)
        hov = self.hover == ("btn", "gen")
        def paint_gen(q, busy=busy, hov=hov, has=m is not None, rg0=QRectF(0, 0, rg.width(), rg.height())):
            if not has:
                q.fillRect(rg0, QColor(255, 102, 170, 120 if busy else (255 if hov else 220)))
                _txt(q, rg0, "Карта строится…" if busy else "Сгенерировать карту  F4", rg0.height() * 0.42,
                     QColor(255, 255, 255), QFont.Weight.Black, Qt.AlignmentFlag.AlignCenter)
            else:
                q.fillRect(rg0, QColor(255, 102, 170, 110 if hov else 60))
                _txt(q, rg0, "Сгенерировать заново / выбрать отрывок  F4", rg0.height() * 0.4,
                     QColor(255, 255, 255, 235), QFont.Weight.DemiBold, Qt.AlignmentFlag.AlignCenter)
        p.drawPixmap(rg.topLeft(), _layer(self, ("gen", busy, hov, m is not None), rg.width(), rg.height(), paint_gen))
        if not busy:
            self._btn_rects["gen"] = rg
        y += rg.height() + H * 0.01
    if t is not None:
        if T.is_osu_track(t):
            mm = m or {}
            msg = ("У карты есть видео — " + ("оно будет фоном" if S.get("map_video", True) else "выключено")
                   ) if mm.get("video") else "Фон карты — из её папки osu!"
            key = "mapvideo"
        else:
            ent = self.shell.clip_entry(t)
            if ent and ent.get("path"):
                msg = "Клип скачан — " + ("он будет фоном карты" if S.get("bg") == "clip" else "фон: обложка")
                key = "bgmode"
            else:
                msg = self.shell.clip_state(t) or "Скачать клип песни для фона карты"
                key = "clipdl"
        r = QRectF(x, y, w, H * 0.03)
        hb_ = self.hover == ("btn", key)

        def paint_msg(q, msg=msg, hb_=hb_, r0=QRectF(0, 0, r.width(), r.height())):
            q.fillRect(r0, QColor(0, 0, 0, 150 if hb_ else 100))
            _txt(q, r0.adjusted(10, 0, -6, 0), msg, r0.height() * 0.45, QColor(255, 255, 255, 225))
        p.drawPixmap(r.topLeft(), _layer(self, ("msg", msg, hb_), r.width(), r.height(), paint_msg))
        self._btn_rects[key] = r
        y += r.height() + H * 0.02
    key = T.map_key(m) if m else None
    if key != self._scores_key:
        self._scores_key = key
        self._scores = T.load_scores().get(key, []) if key else []
    self._score_rects = []
    if m and not self._scores:
        r = QRectF(x + W * 0.005, y + H * 0.11, W * 0.245, H * 0.05)

        def paint_none(q, w0=r.width(), h0=r.height()):
            r0 = QRectF(2, 2, w0, h0)
            q.setPen(QPen(QColor(255, 255, 255), 2))
            q.setBrush(QColor(0, 0, 0, 120))
            q.drawRect(r0)
            _trophy(q, QRectF(r0.left() + h0 * 0.25, r0.top() + h0 * 0.2, h0 * 0.6, h0 * 0.6))
            _txt(q, r0.adjusted(h0, 0, 0, 0), "Рекордов пока нет!", h0 * 0.5, QColor(70, 190, 255),
                 QFont.Weight.Light, Qt.AlignmentFlag.AlignCenter)
        p.drawPixmap(QPointF(r.left() - 2, r.top() - 2), _layer(self, ("noscores",), r.width() + 4, r.height() + 4,
                                                                 paint_none))
        return
    rh = H * 0.062
    rows = max(1, int((H - bot - y - 10) // (rh + 4)))
    for i, s in enumerate(self._scores[:min(8, rows)]):
        k = (time.perf_counter() - _st(self).get("enter", 0.0) - i * 0.05) / 0.35
        sx = 0.0 if k >= 1 else -(1 - _ease(k)) * w
        rr = QRectF(x + sx, y + i * (rh + 4), w, rh)
        hov = self.hover == ("score", i)
        skey = ("score", s.get("player"), s.get("score"), s.get("combo"), s.get("acc"), s.get("grade"),
                tuple(s.get("mods") or ()), hov, id(SK.ACTIVE))

        def paint_row(q, s=s, hov=hov):
            r0 = QRectF(0, 0, w, rh)
            q.fillRect(r0, QColor(0, 0, 0, 190 if hov else 140))
            q.fillRect(QRectF(0, 0, 3, rh), QColor(EDGE))
            gp = self.shell.grade_pix(s["grade"], rh * 0.62, self.devicePixelRatioF())
            T.blit(q, gp, rh * 0.5, rh / 2, 1.0, 1.0)
            _shadow_txt(q, QRectF(rh, rh * 0.04, w * 0.5, rh * 0.45), s.get("player") or "игрок", rh * 0.3,
                        weight=QFont.Weight.Bold)
            _txt(q, QRectF(rh, rh * 0.5, w * 0.62, rh * 0.42),
                 f"Очки: {s['score']:,} ({s['combo']}x)".replace(",", " ") + ("  " + "".join(s["mods"]) if s["mods"]
                                                                             else ""),
                 rh * 0.24, QColor(255, 255, 255, 200))
            _txt(q, QRectF(w - w * 0.35, 0, w * 0.33, rh), f"{s['acc'] * 100:.2f}%", rh * 0.27, QColor(255, 220, 120),
                 QFont.Weight.Bold, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        p.drawPixmap(QPointF(rr.left(), rr.top()), _layer(self, skey, w, rh, paint_row))
        self._score_rects.append(rr)


def _trophy(p, r):
    """Кубок (значок «Рекордов пока нет!»)."""
    w, h = r.width(), r.height()
    x, y = r.left(), r.top()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 255, 255))
    cup = QPainterPath()
    cup.moveTo(x + w * 0.22, y + h * 0.05)
    cup.lineTo(x + w * 0.78, y + h * 0.05)
    cup.cubicTo(x + w * 0.78, y + h * 0.5, x + w * 0.62, y + h * 0.62, x + w * 0.5, y + h * 0.62)
    cup.cubicTo(x + w * 0.38, y + h * 0.62, x + w * 0.22, y + h * 0.5, x + w * 0.22, y + h * 0.05)
    p.drawPath(cup)
    p.drawRect(QRectF(x + w * 0.45, y + h * 0.6, w * 0.1, h * 0.2))
    p.drawRect(QRectF(x + w * 0.3, y + h * 0.8, w * 0.4, h * 0.12))
    p.setPen(QPen(QColor(255, 255, 255), max(1.5, w * 0.08)))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(QRectF(x + w * 0.02, y + h * 0.1, w * 0.3, h * 0.32), 90 * 16, 180 * 16)
    p.drawArc(QRectF(x + w * 0.68, y + h * 0.1, w * 0.3, h * 0.32), -90 * 16, 180 * 16)


def _bottom_buttons(self):
    if not _on(self):
        return _O["bottom_buttons"](self)
    W, H = self.width(), self.height()
    bot = _bot(H)
    y0 = H - bot
    bw = max(64.0, W * 0.064)
    x0 = W * 0.16
    R = bot * 1.12
    cx, cy = W - bot * 0.66, H - bot * 0.3
    return {"back": QRectF(0, H - bot * 0.6, max(100.0, W * 0.068), bot * 0.4),
            "themes": QRectF(x0, y0 + 2, bw, bot - 2), "mods": QRectF(x0 + bw + 2, y0 + 2, bw, bot - 2),
            "random": QRectF(x0 + (bw + 2) * 2, y0 + 2, bw, bot - 2),
            "options": QRectF(x0 + (bw + 2) * 3, y0 + 2, bw, bot - 2),
            "play": QRectF(cx - R, cy - R, 2 * R, 2 * R)}


def _layer(self, key, w, h, fn):
    """Неподвижная часть экрана — картинкой: fn(q) рисует её один раз, пока не сменится key.
    Текст, градиенты и сглаженные фигуры на каждом кадре стоили миллисекунды; картинка — доли."""
    c = self.__dict__.setdefault("_layers", {})
    dpr = self.devicePixelRatioF()
    k = (key, int(w), int(h), round(dpr, 2))
    pm = c.get(k)
    if pm is None:
        if len(c) > 96:
            c.clear()
        pm = SK._pm(w, h, dpr)
        q = SK._painter(pm)
        fn(q)
        q.end()
        c[k] = pm
    return pm


def _draw_bottom(self, p, W, H, bot):
    if not _on(self):
        return _O["bottom"](self, p, W, H, bot)
    st = _st(self)
    now = time.perf_counter()
    dt = min(0.05, now - st.get("bt", now))
    st["bt"] = now
    hv = st.setdefault("hv", {})
    bot = _bot(H)
    y0 = H - bot
    b = self._bottom_buttons()
    for key in b:
        tgt = 1.0 if self.hover == ("bottom", key) else 0.0
        v = hv.get(key, 0.0)
        hv[key] = v + (tgt - v) * min(1.0, dt * 12)
    qkey = ("bottom", W, H) + tuple(round(hv.get(k, 0.0) * 24) for k in ("back", "themes", "mods", "random",
                                                                           "options"))
    top = min(r.top() for k, r in b.items() if k != "play") - 4
    pm = _layer(self, qkey, W, H - top, lambda q: (q.translate(0, -top), _draw_bottom_bar(self, q, W, H, b, hv, y0, bot)))
    p.drawPixmap(QPointF(0, top), pm)
    _draw_bottom_live(self, p, W, H, b, hv, y0, bot)


def _draw_bottom_bar(self, p, W, H, b, hv, y0, bot):
    """Полоса снизу: фон, «назад», Тема / Моды / Случайно / Карта (в кэш-картинку)."""
    hv = {k: round(v * 24) / 24 for k, v in hv.items()}
    p.fillRect(QRectF(0, y0, W, bot), QColor(0, 0, 0, 225))
    p.fillRect(QRectF(0, y0, W, 2), EDGE)
    # назад
    r = b["back"]
    a = hv.get("back", 0.0)
    rr = r.translated(a * 8, 0)
    path = QPainterPath()
    path.addPolygon(QPolygonF([QPointF(rr.left() - 10, rr.top()), QPointF(rr.right(), rr.top()),
                               QPointF(rr.right() - rr.height() * 0.25, rr.bottom()),
                               QPointF(rr.left() - 10, rr.bottom())]))
    p.fillPath(path, SK.lighter(QColor(238, 50, 130), 0.15 * a) if a > 0.01 else QColor(238, 50, 130))
    ic = QPointF(rr.left() + rr.height() * 0.45, rr.center().y())
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 255, 255))
    p.drawEllipse(ic, rr.height() * 0.3, rr.height() * 0.3)
    p.setPen(QPen(QColor(238, 50, 130), rr.height() * 0.08, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    s = rr.height() * 0.11
    p.drawPolyline(QPolygonF([QPointF(ic.x() + s * 0.4, ic.y() - s), QPointF(ic.x() - s * 0.6, ic.y()),
                              QPointF(ic.x() + s * 0.4, ic.y() + s)]))
    _txt(p, QRectF(rr.left() + rr.height() * 0.85, rr.top(), rr.width(), rr.height()), "назад", rr.height() * 0.5)
    # Тема / Моды / Случайно / Карта
    for key, label, fk, col in (("themes", "Тема", "", QColor(160, 110, 255)), ("mods", "Моды", "F1",
                                QColor(255, 90, 170)), ("random", "Случайно", "F2", QColor(150, 220, 60)),
                                ("options", "Карта", "F3", QColor(70, 160, 255))):
        r = b[key]
        a = hv.get(key, 0.0)
        g = QLinearGradient(r.topLeft(), r.bottomLeft())
        g.setColorAt(0, QColor(42, 42, 50, 235))
        g.setColorAt(1, QColor(20, 20, 26, 235))
        p.fillRect(r, QBrush(g))
        if a > 0.01:
            gg = QLinearGradient(r.bottomLeft(), r.topLeft())
            gg.setColorAt(0, QColor(col.red(), col.green(), col.blue(), int(150 * a)))
            gg.setColorAt(1, QColor(col.red(), col.green(), col.blue(), 0))
            p.fillRect(QRectF(r.left(), r.bottom() - r.height() * a, r.width(), r.height() * a), QBrush(gg))
        p.fillRect(QRectF(r.left(), r.top(), r.width(), 3 + 3 * a), col)
        if key == "themes":
            c = QPointF(r.center().x(), r.top() + r.height() * 0.36)
            p.setPen(QPen(QColor(255, 255, 255), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, r.height() * 0.15, r.height() * 0.15)
            _txt(p, QRectF(c.x() - 20, c.y() - 20, 40, 40), "e", r.height() * 0.16, QColor(255, 255, 255),
                 QFont.Weight.Bold, Qt.AlignmentFlag.AlignCenter)
            _txt(p, QRectF(r.left(), r.top() + r.height() * 0.58, r.width(), r.height() * 0.3), label,
                 r.height() * 0.2, align=Qt.AlignmentFlag.AlignCenter)
        else:
            _txt(p, QRectF(r.left(), r.top() + r.height() * 0.2, r.width(), r.height() * 0.4), label,
                 r.height() * 0.2, align=Qt.AlignmentFlag.AlignCenter)
            _txt(p, QRectF(r.left(), r.bottom() - r.height() * 0.26, r.width() - 5, r.height() * 0.24), fk,
                 r.height() * 0.13, QColor(255, 255, 255, 200), align=Qt.AlignmentFlag.AlignRight |
                 Qt.AlignmentFlag.AlignVCenter)


def _draw_bottom_live(self, p, W, H, b, hv, y0, bot):
    """То, что меняется: моды, карточка игрока (сама картинкой), пульсирующая печенька."""
    # выбранные моды — над «Моды»
    x = b["mods"].left()
    sz = bot * 0.4
    for m in sorted(self.mods):
        pm = self.shell.mod_pix(m, sz, self.devicePixelRatioF())
        p.drawPixmap(QPointF(x, y0 - sz * 1.05), pm)
        x += pm.width() / pm.devicePixelRatio() * 0.72
    # карточка игрока
    try:
        mn = self.shell.menu
        tb = mn._toolbar()
        p.save()
        p.translate(b["options"].right() + W * 0.05, y0 + (bot - tb) / 2)
        mn._draw_user(p, W, H)
        p.restore()
    except Exception:                                      # noqa: BLE001
        pass
    # печенька esu! в углу
    r = b["play"]
    a = hv.get("play", 0.0)
    R = r.width() / 2 * (1 + 0.06 * a) * (1 + 0.03 * self.shell.pulse.beat)
    try:
        self.shell.menu._draw_cookie(p, r.center().x(), r.center().y(), R)
    except Exception:                                      # noqa: BLE001
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(T.PINK)
        p.drawEllipse(r.center(), R, R)


# ── моды на весь экран ── #

def _mod_rects(self):
    if not _on(self):
        return _O["mod_rects"](self)
    W, H = self.width(), self.height()
    size = min(H * 0.083, W * 0.048)
    y0 = H * 0.245
    out = []
    for ri, (_title, mods) in enumerate(T.MOD_ROWS):
        y = y0 + ri * H * 0.125
        for mi, m in enumerate(mods):
            x = W * 0.258 + mi * W * 0.076 - (1 - self.mk) * W * 0.05 * (mi + 1)
            out.append((m, QRectF(x, y, size, size)))
    return out, y0, size


def _draw_mods(self, p, W, H):
    if not _on(self):
        return _O["mods"](self, p, W, H)
    rects, y0, size = self._mod_rects()
    mk = self.mk
    p.setOpacity(mk)
    p.fillRect(QRectF(0, 0, W, H), QColor(0, 0, 0, 215))
    for i, line in enumerate(("Моды влияют на процесс игры. Некоторые из них изменяют количество получаемых",
                              "очков, а некоторые придуманы просто так, для развлечения.")):
        _shadow_txt(p, QRectF(W * 0.006, H * (0.005 + i * 0.045), W * 0.99, H * 0.045), line, H * 0.03)
    mult = T.mods_mult(self.mods) if hasattr(T, "mods_mult") else 1.0
    _txt(p, QRectF(0, H * 0.13, W, H * 0.06), f"Множитель очков: {mult:.2f}x".replace(".", ","), H * 0.04,
         QColor(255, 255, 255), QFont.Weight.Light, Qt.AlignmentFlag.AlignCenter)
    cols = (QColor(90, 225, 80), QColor(255, 100, 40), QColor(255, 255, 255))
    titles = ("Упрощение игры", "Усложнение игры", "Особые")
    for ri in range(len(T.MOD_ROWS)):
        y = y0 + ri * H * 0.125
        _txt(p, QRectF(W * 0.026 - (1 - mk) * W * 0.1, y, W * 0.22, size), titles[ri] if ri < 3 else T.MOD_ROWS[ri][0],
             H * 0.034, cols[min(ri, 2)])
    dpr = self.devicePixelRatioF()
    for m, r in rects:
        on = m in self.mods
        hov = self.hover == ("mod", m)
        pm = self.shell.mod_pix(m, size, dpr, on)
        rot = (-7.0 if on else 0.0)                         # включённый мод чуть наклонён, как в osu!
        T.blit(p, pm, r.center().x(), r.center().y() - (size * 0.06 if hov else 0), 1.1 if (hov or on) else 1.0,
               mk if on or hov else mk * 0.85, rot)
        _txt(p, QRectF(r.left() - 30, r.bottom() + 1, r.width() + 60, H * 0.02), T.MOD_NAMES.get(m, m), H * 0.014,
             QColor(255, 255, 255, 230 if on else 140), align=Qt.AlignmentFlag.AlignCenter)
    rb = QRectF(W * 0.244 - (1 - mk) * W * 0.2, H * 0.668, W * 0.53, H * 0.072)
    rc = QRectF(W * 0.225 + (1 - mk) * W * 0.2, H * 0.772, W * 0.53, H * 0.072)
    self._mods_btns = {"reset": rb, "close": rc}
    for r, label, col, k in ((rb, "1. Сбросить все моды", QColor(222, 62, 22), "reset"),
                             (rc, "2. Закрыть", QColor(118, 118, 118), "close")):
        hov = self.hover == ("modbtn", k)
        p.fillRect(r, SK.lighter(col, 0.15) if hov else col)
        _txt(p, r, label, r.height() * 0.55, QColor(255, 255, 255), QFont.Weight.Normal, Qt.AlignmentFlag.AlignCenter)
    p.setOpacity(1.0)


def _toggle_mod(self, m):
    was = m in self.mods
    _O["toggle_mod"](self, m)
    if _on(self):
        self.sfx("check_off" if was else "check_on", 0.7)


# ── диалог osu! вместо выпадающих меню ── #

class OsuDialog(QWidget):
    """Как в osu!: затемнение, вопрос сверху, столбик цветных кнопок «1. …», Esc — отмена."""
    COLORS = [QColor(120, 190, 40), QColor(226, 64, 30), QColor(170, 92, 205), QColor(150, 80, 200),
              QColor(214, 80, 20), QColor(40, 150, 210), QColor(200, 60, 140), QColor(70, 170, 120)]

    def __init__(self, shell, title, items):
        super().__init__(shell)
        self.shell = shell
        self.title = title
        self.items = list(items) + [("Отмена", None)]
        self.k = 0.0
        self.closing = False
        self.hover = -1
        self.t0 = time.perf_counter()
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setGeometry(shell.rect())
        self.timer = QTimer(self)
        self.timer.setInterval(15)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        if _on(shell) and shell.S().get("osu_cursor_menus", True):
            self.setCursor(Qt.CursorShape.BlankCursor)
        self.show()
        self.raise_()
        self.setFocus()
        try:
            shell.sfx.play("menuclick", 0.6)
        except Exception:                                  # noqa: BLE001
            pass

    def _tick(self):
        tgt = 0.0 if self.closing else 1.0
        self.k += (tgt - self.k) * 0.25
        if self.closing and self.k < 0.03:
            self.timer.stop()
            self.hide()
            self.deleteLater()
            return
        _cursor_step(self)
        self.update()

    def _rects(self):
        W, H = self.width(), self.height()
        n = len(self.items)
        bh, gap = H * 0.052, H * 0.012
        y0 = max(H * 0.2, H * 0.5 - n * (bh + gap) / 2)
        out = []
        for i in range(n):
            k = _ease((time.perf_counter() - self.t0 - i * 0.04) / 0.3)
            dx = (1 - k) * W * 0.1 * (1 if i % 2 else -1) + (W * 0.006 if i % 2 else -W * 0.004)
            out.append(QRectF(W * 0.33 + dx, y0 + i * (bh + gap), W * 0.34, bh))
        return out

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        p.setOpacity(max(0.0, min(1.0, self.k)))
        p.fillRect(QRectF(0, 0, W, H), QColor(0, 0, 0, 215))
        y = H * 0.008
        for i, line in enumerate(self.title):
            _shadow_txt(p, QRectF(W * 0.008, y, W * 0.98, H * 0.04), line, H * (0.03 if i == 0 else 0.026))
            y += H * 0.038
        for i, (r, (label, _fn)) in enumerate(zip(self._rects(), self.items)):
            last = i == len(self.items) - 1
            col = QColor(110, 110, 110) if last else self.COLORS[i % len(self.COLORS)]
            hov = i == self.hover
            rr = r.adjusted(-6, -2, 6, 2) if hov else r
            p.fillRect(rr, SK.lighter(col, 0.18) if hov else col)
            if hov:
                p.setPen(QPen(QColor(255, 255, 255, 200), 1.5))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(rr)
            _txt(p, rr, f"{i + 1}. {_tr(label)}", r.height() * 0.5, QColor(255, 255, 255), QFont.Weight.Normal,
                 Qt.AlignmentFlag.AlignCenter)
        p.setOpacity(1.0)
        _cursor_paint(self, p)
        p.end()

    def _pick(self, i):
        if self.closing:
            return
        self.closing = True
        fn = self.items[i][1] if 0 <= i < len(self.items) else None
        try:
            self.shell.sfx.play("menuhit" if fn else "menuback", 0.7)
        except Exception:                                  # noqa: BLE001
            pass
        if fn is not None:
            QTimer.singleShot(0, fn)

    def mouseMoveEvent(self, e):
        old = self.hover
        self.hover = next((i for i, r in enumerate(self._rects()) if r.contains(e.position())), -1)
        if self.hover != old and self.hover >= 0:
            try:
                self.shell.sfx.play("hover", 0.4)
            except Exception:                              # noqa: BLE001
                pass

    def mousePressEvent(self, e):
        i = next((i for i, r in enumerate(self._rects()) if r.contains(e.position())), -1)
        self._pick(i if i >= 0 else len(self.items) - 1)

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key.Key_Escape:
            self._pick(len(self.items) - 1)
        elif Qt.Key.Key_1 <= k <= Qt.Key.Key_9:
            i = k - Qt.Key.Key_1
            if i < len(self.items):
                self._pick(i)


_DLG: dict = {}


def _flatten(menu, prefix=""):
    out = []
    for a in menu.actions():
        if a.isSeparator() or not a.isEnabled() or not a.isVisible():
            continue
        sub = None
        try:
            sub = a.menu()
        except Exception:                                  # noqa: BLE001
            sub = None
        if sub is not None:
            out += _flatten(sub, a.text().replace("&", "") + ": ")
            continue
        label = _tr(prefix.rstrip(": ")) + ": " + _tr(a.text().replace("&", "")) if prefix else \
            _tr(a.text().replace("&", ""))
        if a.isCheckable() and a.isChecked():
            label += "  ✓"
        out.append((label, a.trigger))
    return out


class _CaptureMenu(QMenu):
    """QMenu, которое вместо выпадающего списка показывает диалог osu! с теми же пунктами."""

    def exec(self, *a, **k):                               # noqa: A003
        shell = _DLG.get("shell")
        items = _flatten(self)
        if shell is None or not items:
            return super().exec(*a, **k)
        self._keep = OsuDialog(shell, _DLG.get("title") or [""], items)
        return None


def _dialog_wrap(name, title_fn):
    def w(self, *a, **k):
        if not _on(self):
            return _O[name](self, *a, **k)
        _DLG["shell"] = self.shell
        _DLG["title"] = title_fn(self)
        old = T.QMenu
        T.QMenu = _CaptureMenu
        try:
            return _O[name](self, *a, **k)
        finally:
            T.QMenu = old
    return w


def _title_map(self):
    t = self.cur()
    head = f"{t.get('artist') or '—'} - {T.track_title(t)}" if t else "esu!"
    return [head, "Что вы хотите сделать с этой картой?"]


# ── «Случайно» — карусель прокручивается ── #

def _random(self):
    if not _on(self) or len(self.tracks) < 3:
        return _O["random"](self)
    b = random.randrange(len(self.tracks))
    self._random_previous=(self.cur() or {}).get('path')
    if b == self.sel:
        b = (b + len(self.tracks) // 2) % len(self.tracks)
    _st(self)["spin"] = {"t0": time.perf_counter(), "a": self.sel, "b": b, "dur": 0.85, "last": self.sel, "snd": 0.0}
    self.sfx("whoosh", 0.5)


def _step(self, dt, now):
    st = _st(self)
    sp = st.get("spin")
    if sp is not None:
        f = (now - sp["t0"]) / sp["dur"]
        if f >= 1.0:
            st["spin"] = None
            self._select(sp["b"])
        else:
            i = int(round(sp["a"] + (sp["b"] - sp["a"]) * _ease(f)))
            if i != sp["last"]:
                sp["last"] = i
                self._select(i, sound=False, preview=False)
                if now - sp["snd"] > 0.045:
                    sp["snd"] = now
                    self.sfx("menuclick", 0.25)
    r = _O["sel_step"](self, dt, now)
    _cursor_step(self)
    return r


def _sel_enter(self):
    r = _O["sel_enter"](self)
    _st(self)["enter"] = time.perf_counter()
    return r


def _click(self, h):
    if _on(self) and h and h[0] == "btn" and str(h[1]).startswith("tab_"):
        k = h[1]
        self.sfx("menuclick")
        if k == "tab_colls":
            return self._coll_menu()
        self._set_coll({"tab_mine": self.COLL_MINE, "tab_osu": self.COLL_OSU, "tab_all": self.COLL_ALL}[k])
        return None
    if _on(self) and h and h == ("bottom", "random"):
        self.sfx("menuclick")
        return self._random()
    return _O["click"](self, h)


def _sel_key(self, e):
    if _on(self) and self.mods_open and e.key() in (Qt.Key.Key_1, Qt.Key.Key_2):
        if e.key() == Qt.Key.Key_1:
            self.mods.clear()
            self.sfx("menuback")
            self._save_prefs()
        else:
            self.mods_open = False
        return None
    return _O["sel_key"](self, e)


def _sel_paint(self, e):
    _O["sel_paint"](self, e)
    if _on(self):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        _cursor_paint(self, p)
        p.end()


# ------------------------------------------------------------------ #
#  Курсор osu! со шлейфом — и в меню                                  #
# ------------------------------------------------------------------ #

def _cursor_on(w) -> bool:
    sh = getattr(w, "shell", None)
    return sh is not None and _on(sh) and sh.S().get("osu_cursor_menus", True)


def _cursor_step(w):
    """Позиции шлейфа и «нажатость» (курсор увеличивается при нажатии, как CursorExpand в osu!)."""
    st = _st(w)
    on = _cursor_on(w)
    want = Qt.CursorShape.BlankCursor if on else Qt.CursorShape.ArrowCursor
    if st.get("curshape") != want:
        st["curshape"] = want
        if on:
            w.setCursor(want)
        else:
            w.unsetCursor()
    if not on:
        st["trail"] = []
        return
    now = time.perf_counter()
    gp = QCursor.pos()
    pos = QPointF(w.mapFromGlobal(gp))
    under = QApplication.widgetAt(gp)
    st["visible"] = under is w
    tr = st.setdefault("trail", [])
    if not tr or (tr[-1][0] - pos).manhattanLength() > 1.5:
        # между кадрами — промежуточные точки, чтобы шлейф был сплошным
        if tr:
            last = tr[-1][0]
            d = math.hypot(pos.x() - last.x(), pos.y() - last.y())
            n = min(12, int(d / 9))
            for j in range(1, n):
                f = j / n
                tr.append((QPointF(last.x() + (pos.x() - last.x()) * f, last.y() + (pos.y() - last.y()) * f), now))
        tr.append((pos, now))
    st["trail"] = [x for x in tr if now - x[1] < 0.22][-90:]
    st["pos"] = pos
    pressed = bool(QApplication.mouseButtons() & (Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton))
    e = st.get("expand", 0.0)
    st["expand"] = e + ((1.0 if pressed else 0.0) - e) * 0.35


def _cursor_skin(w):
    sh = w.shell
    try:
        return sh.skin_for(48)
    except Exception:                                      # noqa: BLE001
        return None


def _cursor_paint(w, p):
    if not _cursor_on(w):
        return
    st = _st(w)
    if not st.get("visible") or st.get("pos") is None:
        return
    sk = _cursor_skin(w)
    if sk is None:
        return
    H = w.height()
    try:
        cs = float(w.shell.S().get("cursor_size", 1.0) or 1.0)
    except Exception:                                      # noqa: BLE001
        cs = 1.0
    sc = H / 900.0 * cs * 0.9
    now = time.perf_counter()
    try:
        tp = sk.trail(1.0)
        for pt, t0 in st.get("trail", [])[:-1]:
            a = max(0.0, 1 - (now - t0) / 0.22)
            T.blit(p, tp, pt.x(), pt.y(), sc, a * 0.85)
    except Exception:                                      # noqa: BLE001
        pass
    expand = 1.0 + 0.3 * st.get("expand", 0.0)
    rot = 0.0
    try:
        ini = (getattr(SK, "ACTIVE", None).ini or {}).get("general", {}) if getattr(SK, "ACTIVE", None) else {}
        if ini.get("cursorexpand", "1").strip() == "0":
            expand = 1.0
        if ini.get("cursorrotate", "1").strip() == "1" and getattr(SK, "ACTIVE", None) is not None:
            rot = (now * 50.0) % 360.0                     # градусы, как у blit
    except Exception:                                      # noqa: BLE001
        pass
    pos = st["pos"]
    T.blit(p, sk.cursor(1.0), pos.x(), pos.y(), sc * expand, 1.0, rot)


# ------------------------------------------------------------------ #
#  Главное меню: курсор, снег из скина, welcome_text                  #
# ------------------------------------------------------------------ #

def _menu_paint(self, e):
    _O["menu_paint"](self, e)
    if not _on(self):
        return
    p = QPainter(self)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    _snow(self, p)
    _cursor_paint(self, p)
    p.end()


def _snow(self, p):
    sf = getattr(SK, "ACTIVE", None)
    if sf is None or not self.shell.S().get("skin_snow", True):
        return
    st = _st(self)
    pm = st.get("snow")
    if pm is None and not st.get("snow_none"):
        img, k = sf.image("menu-snow")
        if img is None:
            st["snow_none"] = True
            return
        s = max(8.0, img.width() * k * self.height() / 768.0)
        pm = st["snow"] = QPixmap.fromImage(img).scaled(int(s), int(s), Qt.AspectRatioMode.KeepAspectRatio,
                                                       Qt.TransformationMode.SmoothTransformation)
    if pm is None:
        return
    W, H = self.width(), self.height()
    tt = time.perf_counter()
    rnd = random.Random(11)
    for _ in range(36):
        sp = rnd.uniform(25, 70)
        x0 = rnd.uniform(0, W)
        ph = rnd.uniform(0, H + 60)
        sc = rnd.uniform(0.35, 1.0)
        y = (ph + tt * sp) % (H + 60) - 30
        x = x0 + math.sin(tt * rnd.uniform(0.3, 0.9) + ph) * 25
        T.blit(p, pm, x, y, sc, 0.75 * sc, tt * rnd.uniform(-35, 35))


def _welcome_pm(self, H):
    sf = getattr(SK, "ACTIVE", None)
    if sf is not None and _on(self):
        st = _st(self)
        key = (int(H), id(sf))
        if st.get("wl_key") != key:
            st["wl_key"] = key
            st["wl"] = None
            img, k = sf.image("welcome_text")
            if img is not None:
                pm = QPixmap.fromImage(img)
                s = H / 768.0 * k
                st["wl"] = pm.scaled(int(pm.width() * s), int(pm.height() * s), Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
        if st.get("wl") is not None:
            return st["wl"]
    return _O["welcome_pm"](self, H)


def _menu_step(self, dt, now):
    r = _O["menu_step"](self, dt, now)
    _cursor_step(self)
    return r


def _menu_choose(self, i, gpos=None):
    """Звуки кнопок меню из скина (menu-play-click и др.), если они есть."""
    if _on(self):
        try:
            names = (["menu_play", "menu_edit", "menu_options", "menu_exit"] if self.level == 0
                     else ["menu_freeplay", "", "", "menuback"])
            if i < len(names) and names[i] and self.shell.sfx.has(names[i]):
                self.shell.sfx.play(names[i], 0.8)
        except Exception:                                  # noqa: BLE001
            pass
    return _O["menu_choose"](self, i, gpos)


# ------------------------------------------------------------------ #
#  Настройки: музыка при входе в esu!, классика, курсор                #
# ------------------------------------------------------------------ #

def pick_track(shell, parent=None):
    """Окно выбора трека из библиотеки (своя музыка + карты osu!) с поиском. → трек или None."""
    from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout
    d = QDialog(parent)
    d.setWindowTitle("Трек при входе в esu!")
    d.resize(560, 620)
    v = QVBoxLayout(d)
    ed = QLineEdit()
    ed.setPlaceholderText("Поиск: исполнитель или название…")
    lst = QListWidget()
    v.addWidget(ed)
    v.addWidget(lst, 1)
    bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    v.addWidget(bb)
    bb.accepted.connect(d.accept)
    bb.rejected.connect(d.reject)
    try:
        import osu_beatmap as OB
        pool = list(shell.win.library) + OB.library_tracks()
    except Exception:                                      # noqa: BLE001
        pool = list(shell.win.library)
    seen, tracks = set(), []
    for t in pool:
        p = str(t.get("path") or "")
        if p and p not in seen:
            seen.add(p)
            tracks.append(t)
    tracks.sort(key=lambda t: (str(t.get("artist") or "").lower(), T.track_title(t).lower()))
    cur = str(shell.S().get("menu_track") or "")

    def fill():
        q = ed.text().strip().lower()
        lst.clear()
        for t in tracks:
            name = f"{t.get('artist') or '—'} — {T.track_title(t)}" + ("   [osu!]" if T.is_osu_track(t) else "")
            if q and not all(w in name.lower() for w in q.split()):
                continue
            it = QListWidgetItem(name)
            it.setData(Qt.ItemDataRole.UserRole, t)
            lst.addItem(it)
            if str(t.get("path")) == cur:
                lst.setCurrentItem(it)
    ed.textChanged.connect(lambda _s: fill())
    lst.itemDoubleClicked.connect(lambda _i: d.accept())
    fill()
    if d.exec() and lst.currentItem() is not None:
        return lst.currentItem().data(Qt.ItemDataRole.UserRole)
    return None


def _settings_extra(panel):
    from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout
    sh = panel.shell
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    real = panel.v
    panel.v = lay
    try:
        panel._h2("Музыка при входе в esu!")
        panel._note("Как «circles!» в osu!: что играет, когда открываете тему esu!. Можно поставить любой трек "
                    "из библиотеки или карту osu!.")
        panel._combo("menu_music", "Играть", [("keep", "что играло"), ("random", "случайный трек"),
                                               ("track", "выбранный трек")])
        row = QHBoxLayout()
        lb = QLabel()
        lb.setWordWrap(True)

        def show():
            t = sh.menu_music_track()
            lb.setText(f"{t.get('artist') or '—'} — {T.track_title(t)}" if t else "трек не выбран")
        btn = QPushButton("Выбрать трек…")
        btn.setObjectName("Btn")

        def choose():
            t = pick_track(sh, panel)
            if t is not None:
                sh.S()["menu_track"] = str(t.get("path"))
                sh.S()["menu_music"] = "track"
                sh.save_settings()
                panel.resync()
                show()
                sh.toast(f"При входе в esu!: {T.track_title(t)}")
        btn.clicked.connect(choose)
        play = QPushButton("▶")
        play.setObjectName("Btn")
        play.setFixedWidth(40)
        play.setToolTip("Послушать сейчас")
        play.clicked.connect(lambda: sh._menu_music() if sh.menu_music_track() else None)
        row.addWidget(lb, 1)
        row.addWidget(btn)
        row.addWidget(play)
        w = QWidget()
        w.setLayout(row)
        row.setContentsMargins(0, 2, 0, 2)
        lay.addWidget(w)
        show()
        panel._check("menu_track_chorus", "С припева (как превью карты)")
        panel._h2("Как в osu!")
        panel._check("classic_plus", "Классика как в osu!: карусель, моды и диалоги",
                     sub="Только для стиля «классика». Выключите — будет прежний вид выбора песни.")
        panel._check("osu_cursor_menus", "Курсор osu! со следом и в меню")
        panel._check("skin_snow", "Снег из скина в меню (menu-snow)")
    finally:
        panel.v = real
    idx = 0
    for i in range(real.count()):                          # после первого заголовка и пояснения
        w = real.itemAt(i).widget()
        if w is not None and w.objectName() == "H2":
            idx = i
            break
    real.insertWidget(idx, box)


RAIL_W = 52
RAIL_ICONS = ["settings", "music", "themes", "play", "edit", "solo", "import", "home", "back", "exit"]


def _icon_pm(kind, size=22, col=QColor(255, 255, 255)):
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.GlobalColor.transparent)
    q = QPainter(pm)
    q.setRenderHint(QPainter.RenderHint.Antialiasing)
    SK.draw_icon(q, kind, QRectF(2, 2, size - 4, size - 4), col)
    q.end()
    return pm


def _add_rail(panel):
    """Слева — столбик значков разделов, как в настройках osu!: щелчок прокручивает к разделу,
    текущий раздел отмечен розовой полоской."""
    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import QHBoxLayout, QLabel, QScrollArea, QToolButton, QVBoxLayout
    sc = panel.findChild(QScrollArea)
    lay = panel.layout()
    if sc is None or lay is None:
        return
    body = sc.widget()
    heads = [w for w in body.findChildren(QLabel) if w.objectName() == "H2"]
    heads.sort(key=lambda w: w.mapTo(body, QPointF(0, 0).toPoint()).y())
    if not heads:
        return
    lay.removeWidget(sc)
    row = QWidget()
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(0)
    rail = QWidget()
    rail.setObjectName("OsuRail")
    rail.setFixedWidth(RAIL_W)
    rail.setStyleSheet("QWidget#OsuRail { background: #120e14; }"
                       "QToolButton { background: transparent; border: none; border-left: 3px solid transparent;"
                       " padding: 6px; } QToolButton:hover { background: rgba(255,255,255,0.08); }"
                       "QToolButton:checked { border-left: 3px solid #ff66aa; background: rgba(255,102,170,0.12); }")
    v = QVBoxLayout(rail)
    v.setContentsMargins(0, 70, 0, 10)
    v.setSpacing(4)
    btns = []
    for i, lb in enumerate(heads[:14]):
        b = QToolButton()
        b.setCheckable(True)
        b.setAutoExclusive(True)
        b.setIcon(QIcon(_icon_pm(RAIL_ICONS[i % len(RAIL_ICONS)])))
        b.setFixedSize(RAIL_W, 40)
        b.setToolTip(lb.text().title())
        b.clicked.connect(lambda _=False, lb=lb: sc.verticalScrollBar().setValue(
            lb.mapTo(body, QPointF(0, 0).toPoint()).y() - 8))
        v.addWidget(b)
        btns.append((b, lb))
    v.addStretch(1)

    def mark(_v=0):
        y = sc.verticalScrollBar().value() + 20
        cur = btns[0][0]
        for b, lb in btns:
            if lb.mapTo(body, QPointF(0, 0).toPoint()).y() <= y:
                cur = b
        if not cur.isChecked():
            cur.setChecked(True)
    sc.verticalScrollBar().valueChanged.connect(mark)
    btns[0][0].setChecked(True)
    # длинные кнопки/подписи не должны раздвигать панель шире экрана панели (раньше правый край обрезался)
    from PyQt6.QtWidgets import QAbstractButton, QComboBox, QSizePolicy
    for w in body.findChildren(QWidget):
        if isinstance(w, (QAbstractButton, QComboBox)) and w.sizeHint().width() > 300:
            w.setMinimumWidth(1)                           # 0 в Qt — «не задано» (берётся подсказка размера)
            w.setSizePolicy(QSizePolicy.Policy.Ignored, w.sizePolicy().verticalPolicy())
        elif isinstance(w, QComboBox):
            w.setMinimumContentsLength(8)
            w.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    h.addWidget(rail)
    h.addWidget(sc, 1)
    lay.addWidget(row)
    T.SettingsPanel.W = 510 + RAIL_W                      # блок из плеера внутри — 440 px + поля + полоса прокрутки


def _settings_build(self):
    _O["settings_build"](self)
    try:
        for k, v in (("classic_plus", True), ("osu_cursor_menus", True), ("skin_snow", True)):
            self.shell.S().setdefault(k, v)
        _settings_extra(self)
    except Exception as e:                                 # noqa: BLE001
        print("[osu classic] settings:", e)
    try:
        QTimer.singleShot(0, lambda: _add_rail(self))
    except Exception as e:                                 # noqa: BLE001
        print("[osu classic] rail:", e)


def _toggle_settings(self, on=None):
    """Панель настроек выезжает слева, как в osu!."""
    p = self.settings_panel
    was = p.isVisible()
    r = _O["toggle_settings"](self, on)
    if p.isVisible() and not was:
        from PyQt6.QtCore import QEasingCurve, QPoint, QPropertyAnimation
        a = QPropertyAnimation(p, b"pos", p)
        a.setDuration(240)
        a.setStartValue(QPoint(-p.width(), 0))
        a.setEndValue(QPoint(0, 0))
        a.setEasingCurve(QEasingCurve.Type.OutCubic)
        p.move(-p.width(), 0)
        a.start()
        p._slide = a
    return r


# ------------------------------------------------------------------ #
#  Плавность: пока открыт esu!                                         #
# ------------------------------------------------------------------ #

def _smooth_on(on: bool):
    """Пока открыт esu!: фоновые расчёты плеера на Python берут меньше времени (bgproc «мягко занят»),
    а уже созданные объекты убраны из обхода сборщика мусора (gc.freeze) — его проходы короче и не
    дают рывков по 40–60 мс в меню и выборе песни."""
    import gc
    try:
        import bgproc
        bgproc.set_soft_busy("esu", on)
    except Exception:                                      # noqa: BLE001
        pass
    try:
        if on:
            gc.collect()
            gc.freeze()
        else:
            gc.unfreeze()
    except Exception:                                      # noqa: BLE001
        pass


def _shell_init(self, *a, **k):
    _O["shell_init"](self, *a, **k)
    _smooth_on(True)


def _shell_shutdown(self, *a, **k):
    try:
        return _O["shell_shutdown"](self, *a, **k)
    finally:
        _smooth_on(False)


# ------------------------------------------------------------------ #
#  Подключение                                                        #
# ------------------------------------------------------------------ #

def install() -> bool:
    global T, SK
    try:
        import osu_theme
        import osu_skin
    except Exception as e:                                 # noqa: BLE001
        print("[osu classic]", e)
        return False
    T, SK = osu_theme, osu_skin
    if getattr(T, "_classic_installed", False):
        return False
    S_, M_ = T.SelectScreen, T.MenuScreen
    _O.update(sizes=S_._sizes, carousel=S_._draw_carousel, info=S_._draw_info, scores=S_._draw_scores,
              bottom_buttons=S_._bottom_buttons, bottom=S_._draw_bottom, mod_rects=S_._mod_rects, mods=S_._draw_mods,
              toggle_mod=S_._toggle_mod, random=S_._random, sel_step=S_.step, sel_enter=S_.enter, click=S_._click,
              sel_key=S_.keyPressEvent, sel_paint=S_.paintEvent, options_menu=S_._options_menu,
              sort_menu=S_._sort_menu, coll_menu=S_._coll_menu, menu_paint=M_.paintEvent, welcome_pm=M_._welcome_pm,
              menu_step=M_.step, menu_choose=M_._choose, settings_build=T.SettingsPanel._build)
    T.SettingsPanel._build = _settings_build
    _O["shell_init"], _O["shell_shutdown"] = T.OsuShell.__init__, T.OsuShell.shutdown
    T.OsuShell.__init__ = _shell_init
    T.OsuShell.shutdown = _shell_shutdown
    _O["toggle_settings"] = T.OsuShell.toggle_settings
    T.OsuShell.toggle_settings = _toggle_settings
    S_._sizes = _sizes
    S_._draw_carousel = _draw_carousel
    S_._draw_info = _draw_info
    S_._draw_scores = _draw_scores
    S_._bottom_buttons = _bottom_buttons
    S_._draw_bottom = _draw_bottom
    S_._mod_rects = _mod_rects
    S_._draw_mods = _draw_mods
    S_._toggle_mod = _toggle_mod
    S_._random = _random
    S_.step = _step
    S_.enter = _sel_enter
    S_._click = _click
    S_.keyPressEvent = _sel_key
    S_.paintEvent = _sel_paint
    S_._options_menu = _dialog_wrap("options_menu", _title_map)
    S_._sort_menu = _dialog_wrap("sort_menu", lambda s: ["Сортировка", "Как упорядочить песни?"])
    S_._coll_menu = _dialog_wrap("coll_menu", lambda s: ["Группировка", "Какие песни показывать?"])
    M_.paintEvent = _menu_paint
    M_._welcome_pm = _welcome_pm
    M_.step = _menu_step
    M_._choose = _menu_choose
    T._classic_installed = True
    return True
