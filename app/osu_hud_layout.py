# osu_hud_layout.py
"""
Расположение интерфейса игры esu! — как редактор скина в osu!lazer.

  * Каждый элемент HUD (здоровье, счёт, точность, комбо, клавиши, шкала ошибок, моды, таблица рекордов)
    можно перетащить мышью, изменить колесом размер, спрятать правой кнопкой. Открывается из меню паузы
    кнопкой «Расположение интерфейса» (или F6 на паузе).
  * Раскладка своя у каждого скина: settings["osu"]["hud_layouts"][имя скина] = {элемент: {x, y, s, hide}},
    x / y — сдвиг центра в долях окна, s — масштаб. "" — встроенный вид esu!.
  * Скины osu!lazer приносят свою раскладку (skininfo.json / *.json со списками компонентов: ScoreCounter,
    ComboCounter, AccuracyCounter, HealthDisplay, HitErrorMeter, KeyCounterDisplay, Leaderboard) — она
    ставится сама, пока вы не поменяли раскладку для этого скина вручную.
  * Клавиши справа — из скина osu! (inputoverlay-background / inputoverlay-key), если они в нём есть.

Подключается из main.py (install()) после osu_skin_plus и обёртывает методы osu_game.OsuGame снаружи.
"""
from __future__ import annotations

import copy
import json
import time
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen

ELEMENTS = [("hp", "Здоровье"), ("score", "Счёт"), ("acc", "Точность и прогресс"), ("combo", "Комбо"),
            ("keys", "Клавиши"), ("hiterror", "Шкала ошибок"), ("mods", "Моды"), ("board", "Таблица рекордов")]
NAMES = dict(ELEMENTS)
SNAP = 10.0
_O: dict = {}
GM = None
SK = None


def _tr(s: str) -> str:
    try:
        import i18n
        return i18n.tr(s)
    except Exception:                                      # noqa: BLE001
        return s


def _font(px, weight=QFont.Weight.Normal):
    f = QFont("Segoe UI")
    f.setPixelSize(max(6, int(px)))
    f.setWeight(weight)
    return f


# ------------------------------------------------------------------ #
#  Какая раскладка сейчас                                             #
# ------------------------------------------------------------------ #

def skin_key(game) -> str:
    try:
        return str(game.S.get("skin") or "")
    except Exception:                                      # noqa: BLE001
        return ""


def current(game) -> dict:
    """Раскладка для кадра: своя (если меняли для этого скина) или из скина osu!lazer."""
    S = game.S
    lays = S.get("hud_layouts") or {}
    k = skin_key(game)
    if k in lays:
        return lays[k] or {}
    return skin_layout(game)


# ── раскладка из скина osu!lazer ── #

_COMP_CACHE: dict = {}
_TYPES = (("ScoreCounter", "score"), ("ComboCounter", "combo"), ("AccuracyCounter", "acc"),
          ("HealthDisplay", "hp"), ("HitErrorMeter", "hiterror"), ("KeyCounterDisplay", "keys"),
          ("Leaderboard", "board"), ("ModDisplay", "mods"))
_ANCHOR_NAMES = {"topleft": (0, 0), "topcentre": (.5, 0), "topcenter": (.5, 0), "topright": (1, 0),
                 "centreleft": (0, .5), "centerleft": (0, .5), "centre": (.5, .5), "center": (.5, .5),
                 "centreright": (1, .5), "centerright": (1, .5), "bottomleft": (0, 1), "bottomcentre": (.5, 1),
                 "bottomcenter": (.5, 1), "bottomright": (1, 1)}


def _anchor(v):
    """Anchor osu!framework: флаги (y0=1, y1=2, y2=4, x0=8, x1=16, x2=32) или имя «TopRight»."""
    if isinstance(v, str):
        k = v.replace(" ", "").replace("_", "").lower()
        if k in _ANCHOR_NAMES:
            return _ANCHOR_NAMES[k]
        try:
            v = int(k)
        except ValueError:
            return (0, 0)
    try:
        v = int(v)
    except (TypeError, ValueError):
        return (0, 0)
    ay = 0 if v & 1 else .5 if v & 2 else 1 if v & 4 else 0
    ax = 0 if v & 8 else .5 if v & 16 else 1 if v & 32 else 0
    return ax, ay


def _walk(obj, out):
    if isinstance(obj, dict):
        if "Type" in obj and ("Anchor" in obj or "Position" in obj):
            out.append(obj)
            return
        for v in obj.values():
            _walk(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk(v, out)


def lazer_components(sf) -> dict:
    """{элемент esu!: компонент lazer} из json-файлов скина (кэш по папке)."""
    if sf is None:
        return {}
    d = Path(getattr(sf, "dir", ""))
    key = str(d)
    if key in _COMP_CACHE:
        return _COMP_CACHE[key]
    comps = []
    try:
        for f in sorted(d.glob("*.json")):
            try:
                _walk(json.loads(f.read_text("utf-8", errors="ignore")), comps)
            except (OSError, ValueError):
                continue
    except OSError:
        pass
    found = {}
    for c in comps:
        t = str(c.get("Type") or "")
        for needle, el in _TYPES:
            if needle in t and el not in found:
                found[el] = c
    _COMP_CACHE[key] = found
    return found


def skin_layout(game) -> dict:
    sf = getattr(SK, "ACTIVE", None) if SK is not None else None
    comps = lazer_components(sf)
    if not comps:
        return {}
    boxes = game.__dict__.get("_hud_boxes") or {}
    W, H = max(1, game.width()), max(1, game.height())
    k = H / 768.0                                          # lazer: интерфейс в «точках» экрана высотой 768
    out = {}
    for el, c in comps.items():
        box = boxes.get(el)
        if box is None:
            continue
        ax, ay = _anchor(c.get("Anchor", 9))
        ox, oy = _anchor(c.get("Origin", c.get("Anchor", 9)))
        pos = c.get("Position") or {}
        sc = c.get("Scale") or {}
        try:
            px, py = float(pos.get("X", 0)), float(pos.get("Y", 0))
            s = float(sc.get("X", 1) if isinstance(sc, dict) else sc)
        except (TypeError, ValueError, AttributeError):
            continue
        s = max(0.3, min(3.0, s or 1.0))
        o = QPointF(ax * W + px * k, ay * H + py * k)
        bw, bh = box.width() * s, box.height() * s
        cx = o.x() + (0.5 - ox) * bw
        cy = o.y() + (0.5 - oy) * bh
        out[el] = {"x": (cx - box.center().x()) / W, "y": (cy - box.center().y()) / H, "s": s}
    return out


# ------------------------------------------------------------------ #
#  Клавиши из скина osu! (inputoverlay-*)                             #
# ------------------------------------------------------------------ #

KEY_COL = {"K": QColor(255, 222, 0), "M": QColor(248, 0, 158)}


def draw_skin_keys(game, p, f, x0, y0, kw) -> bool:
    import osu_skin_import as SI
    dpr = game.devicePixelRatioF()
    cache = game._hud_cache
    H = kw * 4 + 18
    key = ("ibg", int(kw), round(dpr, 2), id(f))
    bg = cache.get(key)
    if bg is None:
        img, k1 = f.image("inputoverlay-background")
        bg = False
        if img is not None and not img.isNull():
            from PyQt6.QtGui import QTransform
            rot = img.transformed(QTransform().rotate(90))   # в osu! фон лежит горизонтально — справа он стоит
            s = H * 1.12 / max(1, rot.height())
            bg = SI.to_pm(rot, rot.width() * s, rot.height() * s, dpr)
        cache[key] = bg
    if bg:
        d = bg.devicePixelRatio()
        p.setOpacity(1.0)
        p.drawPixmap(QPointF(x0 + kw - bg.width() / d + kw * 0.12, y0 - H * 0.06), bg)
    txt_col = QColor(0, 0, 0)
    try:
        from osu_skin_import import _rgb
        c = _rgb((f.ini.get("colours") or {}).get("inputoverlaytext", ""))
        if c is not None:
            txt_col = c
    except Exception:                                      # noqa: BLE001
        pass
    for i, k in enumerate(("K1", "K2", "M1", "M2")):
        on = game.keys[k]
        fl = max(0.0, min(1.0, game.key_flash.get(k, 0.0)))
        ck = ("ikey", k, bool(on), int(kw), round(dpr, 2), id(f))
        pm = cache.get(ck)
        if pm is None:
            img, _k1 = f.image("inputoverlay-key")
            if img is None or img.isNull():
                return False
            if on:
                img = SI.tint(img, KEY_COL[k[0]])
            pm = SI.to_pm(img, kw, kw * img.height() / max(1, img.width()), dpr)
            cache[ck] = pm
        sc = 0.86 if on else 1.0 - 0.08 * fl
        cy = y0 + i * (kw + 6) + kw / 2
        GM.blit(p, pm, x0 + kw / 2, cy, sc, 1.0)
        n = game.key_counts[k]
        label = str(n) if n else k
        GM.blit(p, game._text_pix(label, kw * (0.3 if len(label) < 4 else 0.24), txt_col), x0 + kw / 2, cy, 1.0, 1.0)
    return True


# ------------------------------------------------------------------ #
#  Редактор                                                           #
# ------------------------------------------------------------------ #

def _lay(game) -> dict:
    lays = game.S.setdefault("hud_layouts", {})
    return lays.setdefault(skin_key(game), {})


def final_rect(game, el) -> QRectF | None:
    box = (game.__dict__.get("_hud_boxes") or {}).get(el)
    if box is None:
        return None
    lay = (game.__dict__.get("_hud_lay_frame") or {}).get(el) or {}
    W, H = game.width(), game.height()
    s = float(lay.get("s", 1.0) or 1.0)
    c = box.center()
    cx, cy = c.x() + float(lay.get("x", 0)) * W, c.y() + float(lay.get("y", 0)) * H
    return QRectF(cx - box.width() * s / 2, cy - box.height() * s / 2, box.width() * s, box.height() * s)


def _pause_btn_rect(game) -> QRectF:
    W = game.width()
    return QRectF(W - 330, 18, 310, 42)


def _panel_rect(game) -> QRectF:
    """Панель редактора — посередине экрана: элементы интерфейса по умолчанию стоят по краям
    (сверху справа счёт, сверху слева здоровье), и панель не должна их закрывать."""
    W, H = game.width(), game.height()
    w, h = min(W - 40, 640.0), 112.0
    return QRectF((W - w) / 2, H * 0.5 - h / 2, w, h)


def _tool_rects(game) -> dict:
    pr = _panel_rect(game)
    y = pr.bottom() - 52
    keys = ["skin"] if skin_layout(game) else []
    keys += ["reset", "done"]
    bw = 180.0
    x = pr.center().x() - (len(keys) * bw + (len(keys) - 1) * 10) / 2
    out = {}
    for k in keys:
        out[k] = QRectF(x, y, bw, 40)
        x += bw + 10
    return out


def enter(game):
    if game.state != "paused":
        return
    lays = game.S.setdefault("hud_layouts", {})
    k = skin_key(game)
    if k not in lays:
        lays[k] = copy.deepcopy(current(game))            # начинаем с того, что видно (в т. ч. раскладки скина)
    game._lay_ed = {"drag": None, "hover": None, "tool": None}
    try:
        game._panels(False)
    except Exception:                                      # noqa: BLE001
        pass
    game.state = "layout"
    game.setCursor(Qt.CursorShape.ArrowCursor)
    game.update()


def leave(game):
    if game.state != "layout":
        return
    game.state = "paused"
    game._lay_ed = None
    game.unsetCursor()
    try:
        game.host.save_settings()
    except Exception:                                      # noqa: BLE001
        pass
    try:
        game._panels(True)
    except Exception:                                      # noqa: BLE001
        pass
    game.update()


def _hit(game, pos):
    order = [el for el, _ in ELEMENTS][::-1]               # верхние (нарисованные позже) — первыми
    for el in order:
        r = final_rect(game, el)
        if r is not None and r.adjusted(-6, -6, 6, 6).contains(pos):
            return el
    return None


def _snap(game, r: QRectF) -> QRectF:
    W, H = game.width(), game.height()
    dx = dy = 0.0
    for edge, target in ((r.left(), 0.0), (r.right(), W), (r.center().x(), W / 2)):
        if abs(edge - target) < SNAP:
            dx = target - edge
            break
    for edge, target in ((r.top(), 0.0), (r.bottom(), H), (r.center().y(), H / 2)):
        if abs(edge - target) < SNAP:
            dy = target - edge
            break
    return r.translated(dx, dy)


def _press(game, e):
    pos = e.position()
    ed = game._lay_ed
    for k, r in _tool_rects(game).items():
        if r.contains(pos):
            try:
                game.sfx("menuhit", 0.7)
            except Exception:                              # noqa: BLE001
                pass
            if k == "done":
                leave(game)
            elif k == "reset":
                _lay(game).clear()
                game.S["hud_layouts"][skin_key(game)] = {}
            elif k == "skin":
                game.S["hud_layouts"].pop(skin_key(game), None)
                game.S.setdefault("hud_layouts", {})[skin_key(game)] = copy.deepcopy(skin_layout(game))
            game.update()
            return
    el = _hit(game, pos)
    if el is None:
        return
    lay = _lay(game)
    item = lay.setdefault(el, {})
    if e.button() == Qt.MouseButton.RightButton:
        item["hide"] = not item.get("hide", False)
        game.update()
        return
    if e.button() == Qt.MouseButton.LeftButton:
        ed["drag"] = (el, QPointF(pos), float(item.get("x", 0)), float(item.get("y", 0)))


def _move(game, e):
    pos = e.position()
    ed = game._lay_ed
    if ed.get("drag"):
        el, p0, x0, y0 = ed["drag"]
        W, H = max(1, game.width()), max(1, game.height())
        item = _lay(game).setdefault(el, {})
        item["x"], item["y"] = x0 + (pos.x() - p0.x()) / W, y0 + (pos.y() - p0.y()) / H
        game._hud_lay_frame = current(game)
        r = final_rect(game, el)
        if r is not None:
            sr = _snap(game, r)
            item["x"] += (sr.left() - r.left()) / W
            item["y"] += (sr.top() - r.top()) / H
        game.update()
        return
    old = ed.get("hover")
    ed["hover"] = _hit(game, pos)
    ed["tool"] = next((k for k, r in _tool_rects(game).items() if r.contains(pos)), None)
    if old != ed["hover"]:
        game.update()


def _release(game, e):
    ed = game._lay_ed
    if ed:
        ed["drag"] = None


def _wheel(game, e):
    pos = e.position()
    el = _hit(game, pos)
    if el is None:
        return
    item = _lay(game).setdefault(el, {})
    step = 0.05 if e.angleDelta().y() > 0 else -0.05
    item["s"] = round(max(0.4, min(3.0, float(item.get("s", 1.0)) + step)), 2)
    game.update()


def _double(game, e):
    el = _hit(game, e.position())
    if el is not None:
        _lay(game).pop(el, None)
        game.update()


def paint_overlay(game, p):
    W, H = game.width(), game.height()
    ed = game._lay_ed or {}
    p.setOpacity(1.0)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pr = _panel_rect(game)
    p.setPen(QPen(QColor(255, 255, 255, 60), 1))
    p.setBrush(QColor(0, 0, 0, 200))
    p.drawRoundedRect(pr, 12, 12)
    p.setPen(QColor(255, 255, 255))
    p.setFont(_font(18, QFont.Weight.DemiBold))
    sk = skin_key(game) or _tr("встроенный вид esu!")
    p.drawText(QRectF(pr.left() + 16, pr.top() + 6, pr.width() - 32, 26), Qt.AlignmentFlag.AlignCenter,
               _tr("Расположение интерфейса") + f"  ·  {_tr('скин')}: {sk}")
    p.setFont(_font(12))
    p.setPen(QColor(255, 255, 255, 190))
    p.drawText(QRectF(pr.left() + 10, pr.top() + 32, pr.width() - 20, 20), Qt.AlignmentFlag.AlignCenter,
               _tr("Перетаскивайте мышью · колесо — размер · правая кнопка — спрятать · двойной клик — на место"))
    labels = {"done": _tr("Готово"), "reset": _tr("Сбросить всё"), "skin": _tr("Как в скине")}
    for k, r in _tool_rects(game).items():
        hov = ed.get("tool") == k
        col = QColor(255, 102, 170) if k == "done" else QColor(70, 74, 92)
        if hov:
            col = col.lighter(125)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(r, 8, 8)
        p.setPen(QColor(255, 255, 255))
        p.setFont(_font(15, QFont.Weight.DemiBold))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, labels[k])
    lay = game.__dict__.get("_hud_lay_frame") or {}
    for el, _name in ELEMENTS:
        r = final_rect(game, el)
        if r is None:
            continue
        hidden = bool((lay.get(el) or {}).get("hide"))
        hov = ed.get("hover") == el or (ed.get("drag") or (None,))[0] == el
        col = QColor(255, 80, 90) if hidden else QColor(255, 102, 170) if hov else QColor(120, 200, 255)
        p.setBrush(QColor(col.red(), col.green(), col.blue(), 70 if hidden else 40 if hov else 18))
        pen = QPen(col, 2 if hov else 1.4, Qt.PenStyle.SolidLine if hov else Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawRect(r.adjusted(-3, -3, 3, 3))
        if hidden:
            p.drawLine(r.topLeft(), r.bottomRight())
            p.drawLine(r.topRight(), r.bottomLeft())
        s = float((lay.get(el) or {}).get("s", 1.0) or 1.0)
        tag = _tr(NAMES[el]) + (f"  ×{s:.2f}" if abs(s - 1) > 0.001 else "") + (
            "  (" + _tr("скрыто") + ")" if hidden else "")
        p.setFont(_font(12, QFont.Weight.DemiBold))
        tw = p.fontMetrics().horizontalAdvance(tag) + 12
        ty = r.top() - 24 if r.top() > 26 else r.bottom() + 6
        tx = max(4.0, min(W - tw - 4, r.left()))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 190))
        p.drawRoundedRect(QRectF(tx, ty, tw, 20), 4, 4)
        p.setPen(QColor(255, 255, 255))
        p.drawText(QRectF(tx, ty, tw, 20), Qt.AlignmentFlag.AlignCenter, tag)


# ------------------------------------------------------------------ #
#  Обёртки методов игры                                               #
# ------------------------------------------------------------------ #

_SAMPLE_BOARD = [{"player": "peppy", "score": 9_850_000, "combo": 1240},
                 {"player": "WhiteCat", "score": 7_420_000, "combo": 980},
                 {"player": "mrekk", "score": 5_120_000, "combo": 640}]


def _paint_frame(self, p):
    if self.state != "layout":
        return _O["paint"](self, p)
    board, mods = self._board, self.mods
    if not board:
        self._board = _SAMPLE_BOARD
    if not mods:
        self.mods = {"HD", "HR"}
    try:
        _O["paint"](self, p)
    finally:
        self._board, self.mods = board, mods
    paint_overlay(self, p)


def _draw_menu(self, p, W, H, title):
    _O["menu"](self, p, W, H, title)
    if title != "Провал" and self.state == "paused":
        r = _pause_btn_rect(self)
        hov = getattr(self, "_lay_btn_hov", False)
        p.setOpacity(1.0)
        p.setPen(QPen(QColor(255, 255, 255, 200 if hov else 110), 1.5))
        p.setBrush(QColor(255, 102, 170, 200) if hov else QColor(0, 0, 0, 150))
        p.drawRoundedRect(r, 10, 10)
        p.setPen(QColor(255, 255, 255))
        p.setFont(_font(15, QFont.Weight.DemiBold))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, _tr("Расположение интерфейса") + "  (F6)")


def _mouse_press(self, e):
    if self.state == "layout":
        return _press(self, e)
    if self.state == "paused" and _pause_btn_rect(self).contains(e.position()):
        enter(self)
        return
    return _O["press"](self, e)


def _mouse_move(self, e):
    if self.state == "layout":
        return _move(self, e)
    if self.state == "paused":
        hov = _pause_btn_rect(self).contains(e.position())
        if hov != getattr(self, "_lay_btn_hov", False):
            self._lay_btn_hov = hov
            self.update()
    return _O["move"](self, e)


def _mouse_release(self, e):
    if self.state == "layout":
        return _release(self, e)
    return _O["release"](self, e)


def _wheel_event(self, e):
    if self.state == "layout":
        e.accept()
        return _wheel(self, e)
    return _O["wheel"](self, e)


def _double_click(self, e):
    if self.state == "layout":
        return _double(self, e)
    return _O["dbl"](self, e)


def _key_press(self, e):
    if self.state == "layout":
        if e.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_F6):
            leave(self)
        return
    if self.state == "paused" and e.key() == Qt.Key.Key_F6 and not e.isAutoRepeat():
        enter(self)
        return
    return _O["key"](self, e)


def install() -> bool:
    global GM, SK
    try:
        import osu_game
        import osu_skin
    except Exception as e:                                 # noqa: BLE001
        print("[hud layout]", e)
        return False
    GM, SK = osu_game, osu_skin
    G = osu_game.OsuGame
    if getattr(G, "_hud_layout", False):
        return False
    _O.update(paint=G._paint_frame, menu=G._draw_menu, press=G.mousePressEvent, move=G.mouseMoveEvent,
              release=G.mouseReleaseEvent, wheel=G.wheelEvent, dbl=G.mouseDoubleClickEvent, key=G.keyPressEvent)
    G._paint_frame = _paint_frame
    G._draw_menu = _draw_menu
    G.mousePressEvent = _mouse_press
    G.mouseMoveEvent = _mouse_move
    G.mouseReleaseEvent = _mouse_release
    G.wheelEvent = _wheel_event
    G.mouseDoubleClickEvent = _double_click
    G.keyPressEvent = _key_press
    G._hud_layout = True
    return True
