# osu_ranking.py
"""
Экран результата как в osu! (stable) — со всеми картинками скина, которые его меняют:

  ranking-panel (панель со счётом и попаданиями), ranking-title (надпись «Ranking» справа сверху),
  ranking-X / XH / S / SH / A / B / C / D (большая буква оценки), ranking-maxcombo, ranking-accuracy,
  ranking-graph (подложка графика здоровья), ranking-perfect (полное комбо), ranking-retry, ranking-replay,
  menu-back (кнопка «назад», в т. ч. анимированная menu-back-0…N), hit300 / 300g / 100 / 100k / 50 / 0,
  цифры счёта и комбо — шрифтом скина (ScorePrefix / ComboPrefix).

Раскладка — по экрану osu! 1024×768 (масштаб — от высоты окна). Чего в скине нет — рисуется в духе
стандартного скина osu!. Включается в стиле «классика» (SK.LOOK == "osu") и всегда, когда выбран скин osu!;
в стиле Y2K без скина — прежний экран esu!.
"""
from __future__ import annotations

import time

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF

_O: dict = {}
T = None            # osu_theme
SK = None           # osu_skin


def _tr(s: str) -> str:
    try:
        import i18n
        return i18n.tr(s)
    except Exception:                                      # noqa: BLE001
        return s


def _on(self) -> bool:
    return SK is not None and (getattr(SK, "LOOK", "") == "osu" or getattr(SK, "ACTIVE", None) is not None)


def _files():
    return getattr(SK, "ACTIVE", None)


def _sprite(self, names, k, key=None):
    """Спрайт скина в масштабе экрана osu! (k = H / 768) — QPixmap или None."""
    sf = _files()
    if sf is None:
        return None
    cache = self.__dict__.setdefault("_rk_cache", {})
    ck = (tuple(names), round(k, 3), id(sf), key)
    if ck in cache:
        return cache[ck]
    import osu_skin_import as SI
    img, k1 = sf.image(*names)
    pm = None
    if img is not None and not img.isNull():
        pm = SI.to_pm(img, img.width() * k1 * k, img.height() * k1 * k, self.devicePixelRatioF())
    if len(cache) > 80:
        cache.clear()
    cache[ck] = pm
    return pm


def _frames(self, base, k):
    sf = _files()
    if sf is None:
        return []
    out = []
    for i in range(40):
        if not sf.has(f"{base}-{i}"):
            break
        pm = _sprite(self, [f"{base}-{i}"], k)
        if pm is not None:
            out.append(pm)
    if not out:
        pm = _sprite(self, [base], k)
        out = [pm] if pm is not None else []
    return out


def _wh(pm):
    d = pm.devicePixelRatio()
    return pm.width() / d, pm.height() / d


def _text(p, r, s, px, col=QColor(255, 255, 255), weight=QFont.Weight.Normal,
          align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter):
    T.text(p, r, s, px, col, weight, align)


def _btn_rects(self) -> dict:
    W, H = self.width(), self.height()
    k = H / 768.0
    out = {}
    back = _frames(self, "menu-back", k)
    if back:
        w, h = _wh(back[0])
        out["back"] = QRectF(0, H - h, w, h)
    else:
        out["back"] = QRectF(0, H - 64 * k, 170 * k, 64 * k)
    for key, names, y in (("retry", ["ranking-retry"], 576), ("replay", ["ranking-replay"], 672)):
        pm = _sprite(self, names, k)
        if pm is not None:
            w, h = _wh(pm)
        else:
            w, h = 380 * k, 80 * k
        out[key] = QRectF(W - w, y * k - h / 2, w, h)
    out["offset"] = QRectF(W * 0.5 - 140 * k, H - 54 * k, 280 * k, 40 * k)
    return out


def _buttons(self):
    if not _on(self):
        return _O["buttons"](self)
    r = self.res or {}
    out = _btn_rects(self)
    if not r.get("replay"):
        out.pop("replay", None)
    if r.get("watched") or abs(float(r.get("mean_err", 0) or 0)) < 3:
        out.pop("offset", None)
    return out


def _fallback_btn(p, r, label, c0, c1, hov):
    """Кнопка в духе osu!: вытянутая плашка от правого края с надписью."""
    path = QPainterPath()
    h = r.height()
    path.moveTo(r.left() + h * 0.5, r.top())
    path.lineTo(r.right() + 2, r.top())
    path.lineTo(r.right() + 2, r.bottom())
    path.lineTo(r.left() + h * 0.5, r.bottom())
    path.quadTo(r.left(), r.bottom(), r.left(), r.center().y())
    path.quadTo(r.left(), r.top(), r.left() + h * 0.5, r.top())
    g = QLinearGradient(r.topLeft(), r.bottomLeft())
    g.setColorAt(0, c0.lighter(118) if hov else c0)
    g.setColorAt(1, c1.lighter(118) if hov else c1)
    p.setOpacity(1.0)
    p.fillPath(path, QBrush(g))
    p.strokePath(path, QPen(QColor(255, 255, 255, 120 if hov else 60), 2))
    _text(p, r.adjusted(h * 0.6, 0, 0, 0), label, h * 0.4, QColor(255, 255, 255), QFont.Weight.DemiBold)


def _paint(self, e):
    if not _on(self):
        return _O["paint"](self, e)
    p = T.QPainter(self)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    W, H = self.width(), self.height()
    T.draw_bg(p, self.bg.get(self.shell.bg_path(self.track), W, H, self.devicePixelRatioF(), blur=False), W, H, 0.45,
              cache=self.bg)
    if not self.res:
        p.end()
        return
    r, m, t = self.res, self.m, self.track
    now = time.perf_counter()
    e_t = now - self.t_enter
    dpr = self.devicePixelRatioF()
    k = H / 768.0
    sf = _files()
    # цифры: шрифт скина (ScorePrefix), без него — цифры esu!
    if self.__dict__.get("_rk_dsig") != (round(k, 3), id(sf), dpr):
        self._rk_dsig = (round(k, 3), id(sf), dpr)
        self._rk_score = T.Digits(46 * k, dpr=dpr)
        self._rk_num = T.Digits(30 * k, dpr=dpr)
        if sf is not None:
            try:
                import osu_skin_import as SI
                SI.patch_digits(self._rk_score, "score")
                SI.patch_digits(self._rk_num, "score")
            except Exception as ex:                        # noqa: BLE001
                print("[ranking] digits:", ex)
    # шапка: тёмная полоса, название, «Beatmap by», «Played by»
    p.setOpacity(1.0)
    p.fillRect(QRectF(0, 0, W, 96 * k), QColor(0, 0, 0, 205))
    _text(p, QRectF(6 * k, 4 * k, W * 0.66, 32 * k), f"{t.get('artist') or '—'} - {T.track_title(t)} [{m['name']}]",
          26 * k, QColor(255, 255, 255), QFont.Weight.Normal)
    by = m.get("creator") or ("ECHOES AI" if not m.get("osu") else "—")
    _text(p, QRectF(6 * k, 36 * k, W * 0.66, 24 * k), f"{_tr('Beatmap by')} {by}", 18 * k, QColor(255, 255, 255, 230))
    who = "Auto" if r.get("auto") else self.shell.player_name()
    when = time.strftime('%d.%m.%Y %H:%M:%S', time.localtime(r["time"]))
    _text(p, QRectF(6 * k, 60 * k, W * 0.66, 24 * k), f"{_tr('Played by')} {who} {_tr('on')} {when}", 18 * k,
          QColor(255, 255, 255, 230))
    title = _sprite(self, ["ranking-title"], k)
    if title is not None:
        w, h = _wh(title)
        p.drawPixmap(QPointF(W - w - 32 * k, 0), title)
    else:
        _text(p, QRectF(W - 420 * k, 0, 390 * k, 96 * k), "Ranking", 60 * k, QColor(255, 255, 255),
              QFont.Weight.Light, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    # панель
    panel = _sprite(self, ["ranking-panel"], k)
    py = 102 * k
    if panel is not None:
        p.drawPixmap(QPointF(0, py), panel)
    else:
        pr = QRectF(-20 * k, py, 640 * k, 470 * k)
        T.rrect(p, pr, 18 * k, QColor(0, 0, 0, 150), QPen(QColor(255, 255, 255, 60), 2))
        p.setPen(QPen(QColor(255, 255, 255, 50), 1.5))
        p.drawLine(QPointF(20 * k, py + 118 * k), QPointF(600 * k, py + 118 * k))
    kk = min(1.0, e_t / 1.0)
    sc = int(r["score"] * (1 - (1 - kk) ** 3))
    self._rk_score.draw(p, f"{sc:08d}", 220 * k, py + 62 * k, "l")
    if not sf or not sf.has("ranking-panel"):
        _text(p, QRectF(30 * k, py + 40 * k, 180 * k, 44 * k), _tr("Счёт"), 24 * k, QColor(255, 255, 255, 200))
    sk = self.shell.skin_for(36 * k)
    rows = (((300, r["c300"]), ("300g", r["geki"])), ((100, r["c100"]), ("100k", r["katu"])),
            ((50, r["c50"]), (0, r["c0"])))
    for ri, row in enumerate(rows):
        y = (264 + ri * 84) * k - 102 * k + py
        for ci, (kind, val) in enumerate(row):
            ix = (64 if ci == 0 else 352) * k
            try:
                T.blit(p, sk.judgement(kind), ix, y, 1.0, 1.0)
            except Exception:                              # noqa: BLE001
                pass
            self._rk_num.draw(p, f"{val}x", ix + 64 * k, y, "l")
    # подписи и значения: макс. комбо и точность
    ly = 480 * k - 102 * k + py
    for names, x, label in ((["ranking-maxcombo"], 8, "Max Combo"), (["ranking-accuracy"], 291, "Accuracy")):
        pm = _sprite(self, names, k)
        if pm is not None:
            p.drawPixmap(QPointF(x * k, ly), pm)
        else:
            _text(p, QRectF(x * k + 16 * k, ly, 260 * k, 30 * k), label, 22 * k, QColor(255, 255, 255, 220))
    self._rk_num.draw(p, f"{r['combo']}x", 24 * k, ly + 62 * k, "l")
    self._rk_num.draw(p, f"{r['acc'] * 100:.2f}%", 312 * k, ly + 62 * k, "l")
    # график здоровья
    gy = 600 * k
    graph = _sprite(self, ["ranking-graph"], k)
    if graph is not None:
        gw, gh = _wh(graph)
        p.drawPixmap(QPointF(8 * k, gy), graph)
        gr = QRectF(8 * k + gw * 0.03, gy + gh * 0.12, gw * 0.94, gh * 0.76)
    else:
        gr = QRectF(16 * k, gy, 300 * k, 120 * k)
        T.rrect(p, gr, 6 * k, QColor(0, 0, 0, 130), QPen(QColor(255, 255, 255, 70), 1.2))
        gr = gr.adjusted(6 * k, 8 * k, -6 * k, -8 * k)
    hh = r.get("hp_hist") or []
    if len(hh) > 1:
        t0, t1 = hh[0][0], hh[-1][0]
        good, bad = QPainterPath(), QPainterPath()
        prev = None
        for tt, hpv in hh:
            x = gr.left() + (tt - t0) / max(1.0, t1 - t0) * gr.width()
            yv = gr.bottom() - hpv * gr.height()
            path = good if hpv >= 0.5 else bad
            if prev is None:
                path.moveTo(x, yv)
            else:
                path.moveTo(*prev)
                path.lineTo(x, yv)
            prev = (x, yv)
        p.setOpacity(min(1.0, e_t / 0.6))
        p.strokePath(good, QPen(QColor(120, 230, 70), 2.5 * k + 0.5))
        p.strokePath(bad, QPen(QColor(230, 50, 50), 2.5 * k + 0.5))
        p.setOpacity(1.0)
    # «Perfect» — полное комбо
    full = r.get("c0", 0) == 0 and int(r.get("combo", 0)) >= int(r.get("max_combo", 0) or 0) > 0
    if full and not r.get("failed"):
        pm = _sprite(self, ["ranking-perfect"], k)
        if pm is not None:
            T.blit(p, pm, 416 * k + _wh(pm)[0] / 2, 688 * k, 1.0, min(1.0, max(0.0, e_t - 0.8) * 3))
        else:
            _text(p, QRectF(330 * k, 640 * k, 300 * k, 60 * k), "PERFECT", 40 * k, QColor(255, 230, 120),
                  QFont.Weight.Black)
    # pp / UR — мелко под панелью, как подсказка
    _text(p, QRectF(330 * k, 720 * k - 22 * k, W - 760 * k, 24 * k),
          f"{r['pp']:.0f}pp  ·  UR {r['ur']:.1f}  ·  {abs(r['mean_err']):.1f} {_tr('мс')} "
          f"{_tr('позже') if r['mean_err'] > 0 else _tr('раньше')}", 15 * k, QColor(255, 220, 120), QFont.Weight.DemiBold)
    # оценка: большая буква справа
    gk = min(1.0, max(0.0, (e_t - 0.35) / 0.5))
    if gk > 0:
        gp = self.shell.grade_pix(r["grade"], 300 * k, dpr) if sf is None or not (
            sf.has(f"ranking-{_grade_file(r['grade'])}")) else _sprite(self, [f"ranking-{_grade_file(r['grade'])}"], k)
        if gp is not None:
            T.blit(p, gp, W - 192 * k, 320 * k, 1.6 - 0.6 * (1 - (1 - gk) ** 3), gk)
        if gk < 1:
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            p.fillRect(self.rect(), QColor(255, 255, 255, int(40 * (1 - gk))))
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
    if r.get("failed"):
        _text(p, QRectF(W - 520 * k, 110 * k, 480 * k, 50 * k), _tr("ПРОВАЛ"), 36 * k, QColor(255, 90, 90),
              QFont.Weight.Black, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    elif self.rank == 1 and e_t > 1.0:
        _text(p, QRectF(W - 520 * k, 110 * k, 480 * k, 50 * k), _tr("НОВЫЙ РЕКОРД!"), 30 * k, QColor(255, 220, 120),
              QFont.Weight.Black, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    # моды — под буквой
    x = W - 192 * k - len(r["mods"]) * 26 * k
    for mm in r["mods"]:
        pm = self.shell.mod_pix(mm, 44 * k, dpr)
        p.setOpacity(1.0)
        p.drawPixmap(QPointF(x, 466 * k), pm)
        x += 52 * k
    # кнопки
    rects = self._buttons()
    for key in ("retry", "replay"):
        rr = rects.get(key)
        if rr is None:
            continue
        hov = self.hover == key
        pm = _sprite(self, [f"ranking-{key}"], k)
        if pm is not None:
            p.setOpacity(1.0)
            p.drawPixmap(QPointF(rr.left() - (12 * k if hov else 0), rr.top()), pm)
            if hov:
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
                p.setOpacity(0.25)
                p.drawPixmap(QPointF(rr.left() - 12 * k, rr.top()), pm)
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
                p.setOpacity(1.0)
        else:
            c0, c1 = ((QColor(255, 172, 10), QColor(232, 120, 0)) if key == "retry"
                      else (QColor(60, 160, 230), QColor(30, 110, 200)))
            _fallback_btn(p, rr.translated(-12 * k if hov else 0, 0), _tr("Заново") if key == "retry" else
                          _tr("Смотреть повтор"), c0, c1, hov)
    back = _frames(self, "menu-back", k)
    rb = rects["back"]
    if back:
        fr = back[int(now * 60) % len(back)] if len(back) > 1 else back[0]
        p.setOpacity(1.0)
        p.drawPixmap(QPointF(rb.left(), rb.top()), fr)
        if self.hover == "back":
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            p.setOpacity(0.2)
            p.drawPixmap(QPointF(rb.left(), rb.top()), fr)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            p.setOpacity(1.0)
    else:
        hov = self.hover == "back"
        g = QLinearGradient(rb.topLeft(), rb.topRight())
        g.setColorAt(0, QColor(238, 51, 153) if not hov else QColor(255, 90, 180))
        g.setColorAt(1, QColor(187, 17, 119) if not hov else QColor(220, 40, 150))
        poly = QPolygonF([rb.topLeft(), QPointF(rb.right() - rb.height() * 0.3, rb.top()),
                          QPointF(rb.right(), rb.bottom()), rb.bottomLeft()])
        p.setOpacity(1.0)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawPolygon(poly)
        _text(p, rb.adjusted(rb.height() * 0.5, 0, 0, 0), "back", rb.height() * 0.42, QColor(255, 255, 255),
              QFont.Weight.Normal)
        p.setPen(QPen(QColor(255, 255, 255), max(2.0, rb.height() * 0.06), Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        cx, cy, a = rb.left() + rb.height() * 0.3, rb.center().y(), rb.height() * 0.14
        p.drawPolyline([QPointF(cx + a, cy - a), QPointF(cx, cy), QPointF(cx + a, cy + a)])
    ro = rects.get("offset")
    if ro is not None:
        hov = self.hover == "offset"
        T.rrect(p, ro, ro.height() / 2, QColor(120, 90, 220) if not hov else QColor(150, 120, 240))
        _text(p, ro, _tr("Подогнать смещение"), ro.height() * 0.42, QColor(255, 255, 255), QFont.Weight.DemiBold,
              Qt.AlignmentFlag.AlignCenter)
    p.end()


def _grade_file(g: str) -> str:
    return {"SS": "x", "SSH": "xh", "S": "s", "SH": "sh"}.get(g, g.lower())


def install() -> bool:
    global T, SK
    try:
        import osu_skin
        import osu_theme
    except Exception as e:                                 # noqa: BLE001
        print("[ranking]", e)
        return False
    T, SK = osu_theme, osu_skin
    R = osu_theme.ResultScreen
    if getattr(R, "_osu_ranking", False):
        return False
    _O.update(paint=R.paintEvent, buttons=R._buttons)
    R.paintEvent = _paint
    R._buttons = _buttons
    R._osu_ranking = True
    return True
