# net_showcase.py
"""
Оформление профиля Echoes.NET (как витрина в профиле Dota 2).

  * фон шапки профиля — своя картинка (затемнение регулируется) или аура из цветов любимых треков;
  * «Витрина» под шапкой — холст-конструктор: фон (градиент / картинка) и свободно расставленные блоки:
    текст, картинка, трек (клик — слушать), статистика, ачивка, наклейка, фигура. Блоки двигаются мышкой,
    тянутся за уголок, поворачиваются, у каждого — прозрачность и слой. Есть готовые шаблоны.

Всё лежит в settings["ya_profile_deco"] = {"frame", "hdr", "sc", "aura"} и уходит в профиль ("deco"):
друзьям — с карточкой, остальным — публичным событием. Картинки сжимаются, всё оформление ≤ 60 КБ
(у публичных релеев лимит на размер события — туда уходит копия с ужатыми картинками, for_public).
"""
from __future__ import annotations

import base64
import copy
import json
import math

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (QColor, QFont, QFontMetrics, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
                         QRadialGradient, QTransform)
from PyQt6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                             QMenu, QPushButton, QScrollArea, QSlider, QSpinBox, QVBoxLayout, QWidget)

W0, H0 = 1000, 420                   # логический размер холста витрины
LIMIT = 400000                       # всё оформление (JSON) — друзьям (через Tor до 4 МБ на сообщение)
PUBLIC_LIMIT = 60000                 # в публичную сеть (у релеев лимит на событие) — копия с ужатыми картинками
TYPES = ("text", "img", "track", "stat", "ach", "sticker", "shape", "now")
ITEM_ANIMS = {"": "Нет", "float": "Парит", "pulse": "Пульс", "spin": "Вращается", "blink": "Мерцает",
              "beat": "Под музыку"}
_ANIM = {"on": False, "t": 0.0, "lvl": 0.0}              # кадр анимации (ставит ShowcaseView)


def animated(sc) -> bool:
    return bool((sc or {}).get("bg", {}).get("beat")) or any(it.get("anim") or it.get("t") == "now"
                                                              for it in (sc or {}).get("items") or [])
STATS = {"listened": "Прослушано", "tracks": "Треков в коллекции", "friends": "Друзей", "posts": "Постов",
         "followers": "Подписчиков"}
STICKERS = {"sun": "Солнце", "heart": "Сердце", "star": "Звезда", "note": "Нота", "fire": "Огонь", "bolt": "Молния",
            "crown": "Корона", "vinyl": "Пластинка", "sparkle": "Блеск", "moon": "Луна"}
FONTS = {"": "Обычный", "rounded": "Округлый", "mono": "Терминал", "hand": "Рукописный"}


# ══════════════════════════ данные ══════════════════════════ #

def _hex(v, d="#ffffff") -> str:
    q = QColor(str(v or ""))
    return q.name() if q.isValid() else d


def _f(v, lo, hi, d) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return d
    if x != x:
        return d
    return max(lo, min(hi, x))


def _img_ok(b64, limit) -> str:
    if not isinstance(b64, str) or not b64 or len(b64) > limit:
        return ""
    try:
        raw = base64.b64decode(b64)
    except ValueError:
        return ""
    return b64 if raw[:3] == b"\xff\xd8\xff" or raw[:8] == b"\x89PNG\r\n\x1a\n" else ""


def clean_sc(sc) -> dict:
    """Проверка чужой витрины: только известные поля и размеры."""
    if not isinstance(sc, dict):
        return {}
    bg = sc.get("bg") if isinstance(sc.get("bg"), dict) else {}
    out = {"bg": {"c1": _hex(bg.get("c1"), "#1a1a22"), "c2": _hex(bg.get("c2"), "#08080a"),
                  "ang": _f(bg.get("ang"), 0, 360, 135), "img": _img_ok(bg.get("img"), 160000),
                  "dim": _f(bg.get("dim"), 0, 90, 30)}, "items": []}
    if bg.get("beat"):
        out["bg"]["beat"] = True
    from net_core import clean_track, clip
    for it in (sc.get("items") or [])[:30]:
        if not isinstance(it, dict) or it.get("t") not in TYPES:
            continue
        o = {"t": it["t"], "x": _f(it.get("x"), -0.5, 1.5, 0.1), "y": _f(it.get("y"), -0.5, 1.5, 0.1),
             "w": _f(it.get("w"), 0.01, 2, 0.2), "h": _f(it.get("h"), 0.01, 2, 0.2),
             "rot": _f(it.get("rot"), -180, 180, 0), "op": _f(it.get("op"), 0.05, 1, 1)}
        if it.get("anim") in ITEM_ANIMS and it.get("anim"):
            o["anim"] = it["anim"]
        t = it["t"]
        if t == "text":
            o.update(text=clip(it.get("text"), 300), size=_f(it.get("size"), 8, 160, 36), color=_hex(it.get("color")),
                     bold=bool(it.get("bold", True)), align=it.get("align") if it.get("align") in ("l", "c", "r") else "l",
                     shadow=bool(it.get("shadow", True)), font=it.get("font") if it.get("font") in FONTS else "")
        elif t == "img":
            o.update(img=_img_ok(it.get("img"), 140000), radius=_f(it.get("radius"), 0, 50, 8),
                     fit=it.get("fit") if it.get("fit") in ("cover", "contain", "stretch") else "cover")
            if not o["img"]:
                continue
        elif t == "track":
            tr = clean_track(it.get("track"))
            if not tr:
                continue
            o.update(track=tr, img=_img_ok(it.get("img"), 16000))
        elif t == "stat":
            o.update(k=it.get("k") if it.get("k") in STATS else "listened", color=_hex(it.get("color"), "#ffdb1a"))
        elif t == "ach":
            o.update(k=clip(it.get("k"), 40))
        elif t == "sticker":
            o.update(k=it.get("k") if it.get("k") in STICKERS else "star", color=_hex(it.get("color"), "#ffdb1a"))
        elif t == "now":
            o.update(color=_hex(it.get("color"), "#ffdb1a"))
        elif t == "shape":
            o.update(k="circle" if it.get("k") == "circle" else "rect", color=_hex(it.get("color"), "#ffffff"),
                     alpha=_f(it.get("alpha"), 0, 255, 60), radius=_f(it.get("radius"), 0, 50, 12))
        out["items"].append(o)
    return out


def clean_deco(d) -> dict | None:
    """Рамка + фон шапки + витрина + аура (из профиля друга или публичной сети)."""
    if not isinstance(d, dict):
        return None
    import net_frames
    out = {}
    fr = net_frames.clean_spec(d.get("frame"))
    if fr:
        out["frame"] = fr
    h = d.get("hdr") if isinstance(d.get("hdr"), dict) else {}
    img = _img_ok(h.get("img"), 160000)
    hd = {"dim": _f(h.get("dim"), 0, 90, 45), "pos": _f(h.get("pos"), 0, 1, 0.5)}
    if img:
        hd["img"] = img
    if h.get("c1"):                                            # свой цвет / градиент
        hd.update(c1=_hex(h.get("c1"), "#202028"), c2=_hex(h.get("c2") or h.get("c1"), "#202028"),
                  ang=_f(h.get("ang"), 0, 360, 135))
    draw = _img_ok(h.get("draw"), 160000)
    if draw:
        hd["draw"] = draw                                      # рисунок поверх (PNG с прозрачностью)
    if img or hd.get("c1") or draw:
        out["hdr"] = hd
    sc = clean_sc(d.get("sc"))
    if sc and sc.get("items"):
        out["sc"] = sc
    aura = [_hex(c, "") for c in (d.get("aura") or [])[:4] if isinstance(c, str)]
    aura = [c for c in aura if c]
    if aura:
        out["aura"] = aura
    s = json.dumps(out, ensure_ascii=False)
    while len(s) > LIMIT * 1.1 and out.get("sc", {}).get("items"):  # великоватое — без картинок по одной
        its = out["sc"]["items"]
        big = max(range(len(its)), key=lambda i: len(its[i].get("img") or ""))
        if not its[big].get("img"):
            break
        its.pop(big)
        s = json.dumps(out, ensure_ascii=False)
    if len(s) > LIMIT * 1.1:
        out.get("sc", {}).get("bg", {}).pop("img", None)
        out.pop("hdr", None)
    return out or None


def for_public(deco, limit=None) -> dict:
    """Копия для публичной сети: картинки пережимаются, пока всё не влезет в PUBLIC_LIMIT."""
    PUBLIC_LIMIT = limit or globals()["PUBLIC_LIMIT"]
    d = copy.deepcopy(deco or {})

    def slots():
        out = []
        for it in (d.get("sc") or {}).get("items") or []:
            if it.get("img"):
                out.append((it, "img"))
        if (d.get("sc") or {}).get("bg", {}).get("img"):
            out.append((d["sc"]["bg"], "img"))
        if (d.get("hdr") or {}).get("img"):
            out.append((d["hdr"], "img"))
        return out
    for _ in range(8):
        if size_of(d) <= PUBLIC_LIMIT:
            return d
        for holder, k in slots():
            b = holder[k]
            if len(b) < 3000:
                continue
            img = img_of(b)
            side = max(img.width(), img.height())
            nb = jpeg_b64(img, max(64, int(side * 0.7)), int(len(b) * 0.55)) if not img.isNull() else ""
            holder[k] = nb
    for holder, k in sorted(slots(), key=lambda x: -len(x[0][x[1]])):   # всё ещё много — без картинок
        if size_of(d) <= PUBLIC_LIMIT:
            break
        holder[k] = ""
    if size_of(d) > PUBLIC_LIMIT and (d.get("hdr") or {}).get("draw"):
        d["hdr"].pop("draw")
    if d.get("sc"):
        d["sc"]["items"] = [it for it in d["sc"]["items"] if it.get("t") != "img" or it.get("img")]
    if d.get("hdr") and not (d["hdr"].get("img") or d["hdr"].get("c1") or d["hdr"].get("draw")):
        d.pop("hdr")
    return d


def size_of(deco) -> int:
    return len(json.dumps(deco or {}, ensure_ascii=False))


def jpeg_b64(img: QImage, side: int, limit: int, png=False) -> str:
    """Картинку — в base64 не больше limit: уменьшаем и сжимаем, пока не влезет."""
    if img.isNull():
        return ""
    s = side
    while s >= 48:
        im = img.scaled(s, s, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        for q in ((None,) if png else (86, 74, 62, 50, 40)):
            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            if png:
                im.save(buf, "PNG", 9)
            else:
                if im.hasAlphaChannel():
                    flat = QImage(im.size(), QImage.Format.Format_RGB32)
                    flat.fill(QColor(0, 0, 0))
                    pp = QPainter(flat)
                    pp.drawImage(0, 0, im)
                    pp.end()
                    im = flat
                im.save(buf, "JPG", q)
            b = base64.b64encode(bytes(ba)).decode()
            if len(b) <= limit:
                return b
        s = int(s * 0.8)
    return ""


_IMG: dict = {}


def img_of(b64: str) -> QImage:
    if not b64:
        return QImage()
    k = (len(b64), b64[:48], b64[-48:])
    hit = _IMG.get(k)
    if hit is None:
        hit = QImage()
        try:
            hit.loadFromData(base64.b64decode(b64))
        except ValueError:
            pass
        if len(_IMG) > 120:
            _IMG.clear()
        _IMG[k] = hit
    return hit


# ══════════════════════════ рисование ══════════════════════════ #

def _fonts(key):
    try:
        import ya_skin
        return list(ya_skin.TITLE_FAMILY.get(key, []))
    except Exception:                                          # noqa: BLE001
        return []


def paint_sticker(p: QPainter, k: str, r: QRectF, col: QColor):
    c = r.center()
    R = min(r.width(), r.height()) / 2
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(col)
    path = QPainterPath()
    if k in ("sun", "star", "sparkle"):
        n = {"sun": 14, "star": 5, "sparkle": 4}[k]
        inner = {"sun": 0.62, "star": 0.42, "sparkle": 0.22}[k]
        for i in range(n * 2 + 1):
            a = i * math.pi / n - math.pi / 2
            rr = R if i % 2 == 0 else R * inner
            pt = QPointF(c.x() + math.cos(a) * rr, c.y() + math.sin(a) * rr)
            path.moveTo(pt) if i == 0 else path.lineTo(pt)
        p.drawPath(path)
        if k == "sun":
            g = QRadialGradient(c, R * 0.6)
            g.setColorAt(0, QColor("#fff6b0"))
            g.setColorAt(1, col)
            p.setBrush(g)
            p.drawEllipse(c, R * 0.55, R * 0.55)
    elif k == "heart":
        s = R
        path.moveTo(c.x(), c.y() + s * 0.8)
        path.cubicTo(c.x() - s * 1.6, c.y() - s * 0.2, c.x() - s * 0.6, c.y() - s * 1.3, c.x(), c.y() - s * 0.45)
        path.cubicTo(c.x() + s * 0.6, c.y() - s * 1.3, c.x() + s * 1.6, c.y() - s * 0.2, c.x(), c.y() + s * 0.8)
        p.drawPath(path)
    elif k == "note":
        p.drawEllipse(QPointF(c.x() - R * 0.25, c.y() + R * 0.55), R * 0.4, R * 0.3)
        p.drawRect(QRectF(c.x() + R * 0.05, c.y() - R * 0.9, R * 0.14, R * 1.45))
        path.moveTo(c.x() + R * 0.19, c.y() - R * 0.9)
        path.quadTo(c.x() + R * 0.8, c.y() - R * 0.6, c.x() + R * 0.6, c.y() - R * 0.05)
        path.quadTo(c.x() + R * 0.55, c.y() - R * 0.45, c.x() + R * 0.19, c.y() - R * 0.5)
        p.drawPath(path)
    elif k == "fire":
        path.moveTo(c.x(), c.y() - R)
        path.cubicTo(c.x() + R * 0.9, c.y() - R * 0.2, c.x() + R * 0.8, c.y() + R, c.x(), c.y() + R)
        path.cubicTo(c.x() - R * 0.8, c.y() + R, c.x() - R * 0.9, c.y() - R * 0.1, c.x() - R * 0.2, c.y() - R * 0.4)
        path.cubicTo(c.x() - R * 0.1, c.y() - R * 0.1, c.x() + R * 0.1, c.y() - R * 0.6, c.x(), c.y() - R)
        g = QLinearGradient(c.x(), c.y() - R, c.x(), c.y() + R)
        g.setColorAt(0, QColor("#ffe066"))
        g.setColorAt(1, col)
        p.setBrush(g)
        p.drawPath(path)
    elif k == "bolt":
        for x, y in ((0.15, -1), (-0.55, 0.12), (-0.02, 0.12), (-0.2, 1), (0.6, -0.15), (0.05, -0.15), (0.15, -1)):
            pt = QPointF(c.x() + x * R, c.y() + y * R)
            path.moveTo(pt) if path.elementCount() == 0 else path.lineTo(pt)
        p.drawPath(path)
    elif k == "crown":
        pts = ((-1, 0.6), (-1, -0.5), (-0.5, 0.0), (0, -0.8), (0.5, 0.0), (1, -0.5), (1, 0.6))
        for i, (x, y) in enumerate(pts):
            pt = QPointF(c.x() + x * R, c.y() + y * R)
            path.moveTo(pt) if i == 0 else path.lineTo(pt)
        path.closeSubpath()
        p.drawPath(path)
    elif k == "vinyl":
        p.setBrush(QColor(12, 12, 14))
        p.drawEllipse(c, R, R)
        p.setPen(QPen(QColor(255, 255, 255, 30), max(0.6, R * 0.02)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for k2 in range(4):
            p.drawEllipse(c, R * (0.5 + k2 * 0.12), R * (0.5 + k2 * 0.12))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawEllipse(c, R * 0.32, R * 0.32)
        p.setBrush(QColor(0, 0, 0))
        p.drawEllipse(c, R * 0.05, R * 0.05)
    elif k == "moon":
        a = QPainterPath()
        a.addEllipse(c, R, R)
        b = QPainterPath()
        b.addEllipse(QPointF(c.x() + R * 0.45, c.y() - R * 0.3), R * 0.85, R * 0.85)
        p.drawPath(a.subtracted(b))


class Ctx:
    """Что витрина знает о хозяине: имя, статистика, ачивки, обложки треков, кто смотрит."""

    def __init__(self, name="", stats=None, ach_names=None, ach=(), followers=0, play=None, cover_of=None, now=None):
        self.now = now                                         # () → играющий сейчас трек хозяина или None
        self.name = name
        self.stats = dict(stats or {})
        self.stats.setdefault("followers", followers)
        self.ach_names = ach_names or {}
        self.ach = set(ach or ())
        self.play = play
        self.cover_of = cover_of


def _fmt_stat(k, v) -> str:
    try:
        v = int(v or 0)
    except (TypeError, ValueError):
        v = 0
    if k == "listened":
        h = v // 3600
        return f"{h} ч" if h >= 1 else f"{v // 60} мин"
    if v >= 10000:
        return f"{v / 1000:.1f}k".replace(".0k", "k")
    return str(v)


def item_rect(it, R: QRectF) -> QRectF:
    sx = R.width()
    return QRectF(R.x() + it["x"] * sx, R.y() + it["y"] * R.height(), it["w"] * sx, it["h"] * R.height())


def paint_item(p: QPainter, it: dict, R: QRectF, ctx: Ctx):
    k = R.width() / W0                                        # масштаб шрифтов от ширины
    r = item_rect(it, R)
    p.save()
    p.setOpacity(it.get("op", 1))
    an = it.get("anim") if _ANIM["on"] else ""
    if an:
        t = _ANIM["t"]
        ph = (it["x"] * 7.3 + it["y"] * 3.1) % 6.28            # у каждого блока своя фаза
        c = r.center()
        if an == "float":
            p.translate(0, math.sin(t * 2.1 + ph) * R.height() * 0.018)
        elif an in ("pulse", "beat", "spin"):
            p.translate(c)
            if an == "spin":
                p.rotate((t * 30) % 360)
            else:
                s = 1 + (0.05 * math.sin(t * 3.9 + ph) if an == "pulse" else 0.14 * _ANIM["lvl"])
                p.scale(s, s)
            p.translate(-c)
        elif an == "blink":
            p.setOpacity(it.get("op", 1) * (0.55 + 0.45 * math.sin(t * 3.0 + ph)))
    if it.get("rot"):
        c = r.center()
        p.translate(c)
        p.rotate(it["rot"])
        p.translate(-c)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    t = it["t"]
    if t == "text":
        f = QFont()
        fams = _fonts(it.get("font", ""))
        if fams:
            f.setFamilies(fams)
        f.setPixelSize(max(6, int(it.get("size", 36) * k)))
        f.setWeight(QFont.Weight.Black if it.get("bold", True) else QFont.Weight.Normal)
        p.setFont(f)
        al = {"l": Qt.AlignmentFlag.AlignLeft, "c": Qt.AlignmentFlag.AlignHCenter,
              "r": Qt.AlignmentFlag.AlignRight}[it.get("align", "l")] | Qt.AlignmentFlag.AlignVCenter
        flags = al | Qt.TextFlag.TextWordWrap
        if it.get("shadow", True):
            p.setPen(QColor(0, 0, 0, 150))
            p.drawText(r.translated(0, max(1.0, 2 * k)), flags, it.get("text", ""))
        p.setPen(QColor(it.get("color", "#ffffff")))
        p.drawText(r, flags, it.get("text", ""))
    elif t == "img":
        img = img_of(it.get("img", ""))
        if not img.isNull():
            path = QPainterPath()
            rad = min(r.width(), r.height()) * it.get("radius", 8) / 100
            path.addRoundedRect(r, rad, rad)
            p.setClipPath(path, Qt.ClipOperation.IntersectClip)
            fit = it.get("fit", "cover")
            if fit == "stretch":                               # растянуть ровно по рамке
                p.drawImage(r, img, QRectF(img.rect()))
            elif fit == "contain":                             # вписать целиком
                s = min(r.width() / img.width(), r.height() / img.height())
                dw, dh = img.width() * s, img.height() * s
                p.drawImage(QRectF(r.center().x() - dw / 2, r.center().y() - dh / 2, dw, dh), img, QRectF(img.rect()))
            else:                                              # заполнить (обрезать лишнее)
                s = max(r.width() / img.width(), r.height() / img.height())
                sw, sh = r.width() / s, r.height() / s
                p.drawImage(r, img, QRectF((img.width() - sw) / 2, (img.height() - sh) / 2, sw, sh))
    elif t == "track":
        path = QPainterPath()
        rad = min(r.height() * 0.22, 18 * k + 4)
        path.addRoundedRect(r, rad, rad)
        p.fillPath(path, QColor(0, 0, 0, 120))
        p.setPen(QPen(QColor(255, 255, 255, 30), 1))
        p.drawPath(path)
        pad = r.height() * 0.12
        cs = r.height() - 2 * pad
        cr = QRectF(r.x() + pad, r.y() + pad, cs, cs)
        img = img_of(it.get("img", ""))
        if img.isNull() and ctx.cover_of is not None:
            try:
                pth = ctx.cover_of(it["track"])
                if pth:
                    img = QImage(pth)
            except Exception:                                  # noqa: BLE001
                pass
        cp = QPainterPath()
        cp.addRoundedRect(cr, cs * 0.12, cs * 0.12)
        if not img.isNull():
            p.save()
            p.setClipPath(cp, Qt.ClipOperation.IntersectClip)
            s = max(cs / img.width(), cs / img.height())
            p.drawImage(cr, img, QRectF((img.width() - cs / s) / 2, (img.height() - cs / s) / 2, cs / s, cs / s))
            p.restore()
        else:
            g = QLinearGradient(cr.topLeft(), cr.bottomRight())
            g.setColorAt(0, QColor("#ffdb1a"))
            g.setColorAt(1, QColor("#ff4fa3"))
            p.fillPath(cp, g)
        tx = cr.right() + pad
        tw = r.right() - tx - pad
        f = QFont()
        f.setPixelSize(max(7, int(cs * 0.26)))
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(255, 255, 255))
        tr = it["track"]
        fm = QFontMetrics(f)
        p.drawText(QRectF(tx, r.y() + pad, tw, cs * 0.5), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
                   fm.elidedText(tr.get("title", ""), Qt.TextElideMode.ElideRight, int(max(10, tw))))
        f2 = QFont()
        f2.setPixelSize(max(6, int(cs * 0.2)))
        p.setFont(f2)
        p.setPen(QColor(255, 255, 255, 170))
        p.drawText(QRectF(tx, r.y() + pad + cs * 0.52, tw, cs * 0.4), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                   QFontMetrics(f2).elidedText("▶  " + tr.get("artist", ""), Qt.TextElideMode.ElideRight, int(max(10, tw))))
    elif t == "now":
        paint_now(p, it, r, ctx, k)
    elif t == "stat":
        path = QPainterPath()
        path.addRoundedRect(r, r.height() * 0.18, r.height() * 0.18)
        p.fillPath(path, QColor(0, 0, 0, 105))
        col = QColor(it.get("color", "#ffdb1a"))
        key = it.get("k", "listened")
        val = _fmt_stat(key, ctx.stats.get(key))
        label = STATS.get(key, key).upper()
        f = QFont()
        f.setFamilies(["YS Text", "Inter", "Segoe UI Variable Display", "Segoe UI"])
        f.setPixelSize(max(8, int(r.height() * 0.42)))
        f.setWeight(QFont.Weight.Black)
        while f.pixelSize() > 8 and QFontMetrics(f).horizontalAdvance(val) > r.width() * 0.9:   # влезть по ширине
            f.setPixelSize(f.pixelSize() - 1)
        p.setFont(f)
        p.setPen(col)
        p.drawText(QRectF(r.x(), r.y() + r.height() * 0.08, r.width(), r.height() * 0.56), Qt.AlignmentFlag.AlignCenter,
                   val)
        f2 = QFont(f)
        f2.setPixelSize(max(6, int(r.height() * 0.16)))
        f2.setWeight(QFont.Weight.Bold)
        while f2.pixelSize() > 6 and QFontMetrics(f2).horizontalAdvance(label) > r.width() * 0.9:
            f2.setPixelSize(f2.pixelSize() - 1)
        p.setFont(f2)
        p.setPen(QColor(255, 255, 255, 190))
        p.drawText(QRectF(r.x(), r.y() + r.height() * 0.62, r.width(), r.height() * 0.3), Qt.AlignmentFlag.AlignCenter,
                   label)
    elif t == "ach":
        name = ctx.ach_names.get(it.get("k", ""), (it.get("k", ""), ""))[0]
        have = it.get("k") in ctx.ach
        path = QPainterPath()
        path.addRoundedRect(r, r.height() / 2, r.height() / 2)
        p.fillPath(path, QColor(255, 219, 26, 46) if have else QColor(255, 255, 255, 18))
        p.setPen(QPen(QColor(255, 219, 26, 160) if have else QColor(255, 255, 255, 40), 1.2))
        p.drawPath(path)
        s = r.height() * 0.62
        paint_sticker(p, "star", QRectF(r.x() + r.height() * 0.2, r.center().y() - s / 2, s, s),
                      QColor("#ffdb1a") if have else QColor("#555"))
        f = QFont()
        f.setPixelSize(max(7, int(r.height() * 0.34)))
        f.setWeight(QFont.Weight.Black)
        p.setFont(f)
        p.setPen(QColor("#ffdb1a") if have else QColor("#888"))
        x = r.x() + r.height() * 0.95
        p.drawText(QRectF(x, r.y(), r.right() - x - r.height() * 0.3, r.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   QFontMetrics(f).elidedText(name, Qt.TextElideMode.ElideRight, int(max(10, r.right() - x))))
    elif t == "sticker":
        paint_sticker(p, it.get("k", "star"), r, QColor(it.get("color", "#ffdb1a")))
    elif t == "shape":
        col = QColor(it.get("color", "#ffffff"))
        col.setAlpha(int(it.get("alpha", 60)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        if it.get("k") == "circle":
            p.drawEllipse(r)
        else:
            rad = min(r.width(), r.height()) * it.get("radius", 12) / 100
            p.drawRoundedRect(r, rad, rad)
    p.restore()


def now_track(ctx):
    try:
        return ctx.now() if ctx is not None and ctx.now is not None else None
    except Exception:                                          # noqa: BLE001
        return None


def paint_now(p: QPainter, it: dict, r: QRectF, ctx: Ctx, k: float):
    """Блок «Сейчас играет»: обложка, название и живой эквалайзер; ничего не играет — «Сейчас тишина»."""
    tr = now_track(ctx)
    col = QColor(it.get("color", "#ffdb1a"))
    path = QPainterPath()
    rad = min(r.height() * 0.22, 18 * k + 4)
    path.addRoundedRect(r, rad, rad)
    p.fillPath(path, QColor(0, 0, 0, 130))
    p.setPen(QPen(QColor(col.red(), col.green(), col.blue(), 90), 1.2))
    p.drawPath(path)
    pad = r.height() * 0.12
    cs = r.height() - 2 * pad
    cr = QRectF(r.x() + pad, r.y() + pad, cs, cs)
    cp = QPainterPath()
    cp.addRoundedRect(cr, cs * 0.12, cs * 0.12)
    img = QImage()
    if tr and ctx.cover_of is not None:
        try:
            pth = ctx.cover_of(tr)
            if pth:
                img = QImage(pth)
        except Exception:                                      # noqa: BLE001
            pass
    if not img.isNull():
        p.save()
        p.setClipPath(cp, Qt.ClipOperation.IntersectClip)
        s = max(cs / img.width(), cs / img.height())
        p.drawImage(cr, img, QRectF((img.width() - cs / s) / 2, (img.height() - cs / s) / 2, cs / s, cs / s))
        p.restore()
    else:
        g = QLinearGradient(cr.topLeft(), cr.bottomRight())
        g.setColorAt(0, col)
        g.setColorAt(1, QColor("#7c4dff"))
        p.fillPath(cp, g)
    tx = cr.right() + pad
    tw = max(10, r.right() - tx - pad)
    f = QFont()
    f.setFamilies(["YS Text", "Inter", "Segoe UI Variable Display", "Segoe UI"])
    f.setPixelSize(max(6, int(cs * 0.17)))
    f.setBold(True)
    f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1)
    p.setFont(f)
    p.setPen(col)
    head = "СЕЙЧАС ИГРАЕТ" if tr else "СЕЙЧАС ТИШИНА"
    p.drawText(QRectF(tx, r.y() + pad, tw, cs * 0.26), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, head)
    if tr:                                                     # мини-эквалайзер
        bw = cs * 0.05
        ex = tx + QFontMetrics(f).horizontalAdvance(head) + bw * 2
        tt = _ANIM["t"] if _ANIM["on"] else 0.0
        for j in range(4):
            h = cs * 0.22 * (0.35 + 0.65 * abs(math.sin(tt * (3.1 + j * 1.3) + j)))
            p.fillRect(QRectF(ex + j * bw * 1.7, r.y() + pad + cs * 0.24 - h, bw, h), col)
    f2 = QFont()
    f2.setFamilies(["YS Text", "Inter", "Segoe UI Variable Display", "Segoe UI"])
    f2.setPixelSize(max(7, int(cs * 0.24)))
    f2.setBold(True)
    p.setFont(f2)
    p.setPen(QColor(255, 255, 255))
    p.drawText(QRectF(tx, r.y() + pad + cs * 0.3, tw, cs * 0.34), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
               QFontMetrics(f2).elidedText(tr.get("title", "") if tr else "ничего не играет",
                                           Qt.TextElideMode.ElideRight, int(tw)))
    f3 = QFont()
    f3.setFamilies(["YS Text", "Inter", "Segoe UI Variable Display", "Segoe UI"])
    f3.setPixelSize(max(6, int(cs * 0.19)))
    p.setFont(f3)
    p.setPen(QColor(255, 255, 255, 170))
    p.drawText(QRectF(tx, r.y() + pad + cs * 0.66, tw, cs * 0.3), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
               QFontMetrics(f3).elidedText(("▶  " + tr.get("artist", "")) if tr else "обновляется само",
                                           Qt.TextElideMode.ElideRight, int(tw)))


def paint_showcase(p: QPainter, R: QRectF, sc: dict, ctx: Ctx, radius=18.0):
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    clip = QPainterPath()
    clip.addRoundedRect(R, radius, radius)
    p.setClipPath(clip)
    bg = sc.get("bg") or {}
    a = math.radians(bg.get("ang", 135))
    cx, cy = R.center().x(), R.center().y()
    dx, dy = math.cos(a) * R.width() / 2, math.sin(a) * R.height() / 2
    g = QLinearGradient(cx - dx, cy - dy, cx + dx, cy + dy)
    g.setColorAt(0, QColor(bg.get("c1", "#1a1a22")))
    g.setColorAt(1, QColor(bg.get("c2", "#08080a")))
    p.fillRect(R, g)
    img = img_of(bg.get("img", ""))
    if not img.isNull():
        s = max(R.width() / img.width(), R.height() / img.height())
        sw, sh = R.width() / s, R.height() / s
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(R, img, QRectF((img.width() - sw) / 2, (img.height() - sh) / 2, sw, sh))
        p.fillRect(R, QColor(0, 0, 0, int(255 * bg.get("dim", 30) / 100)))
    if bg.get("beat") and _ANIM["on"] and _ANIM["lvl"] > 0.02:   # фон дышит под музыку
        rg = QRadialGradient(R.center(), max(R.width(), R.height()) * 0.7)
        col = QColor(bg.get("c1", "#1a1a22")).lighter(170)
        col.setAlpha(int(150 * _ANIM["lvl"]))
        rg.setColorAt(0, col)
        col2 = QColor(col)
        col2.setAlpha(0)
        rg.setColorAt(1, col2)
        p.fillRect(R, rg)
    for it in sc.get("items") or []:
        paint_item(p, it, R, ctx)
    p.restore()


def hit_item(sc, R: QRectF, pos: QPointF):
    """Верхний блок под точкой (с учётом поворота) — индекс или None."""
    items = sc.get("items") or []
    for i in range(len(items) - 1, -1, -1):
        it = items[i]
        r = item_rect(it, R)
        pt = pos
        if it.get("rot"):
            tr = QTransform()
            c = r.center()
            tr.translate(c.x(), c.y())
            tr.rotate(-it["rot"])
            tr.translate(-c.x(), -c.y())
            pt = tr.map(pos)
        if r.contains(pt):
            return i
    return None


# ══════════════════════════ просмотр ══════════════════════════ #

class ShowcaseView(QWidget):
    """Витрина в профиле: тянется по ширине (1000:420), клик по треку — слушать."""

    def __init__(self, sc: dict, ctx: Ctx, parent=None, max_w=100000, min_w=900):
        super().__init__(parent)
        self.sc, self.ctx = sc, ctx
        self.max_w = max_w
        self.setMouseTracking(True)
        self.setMinimumWidth(min_w)                            # уже — у страницы появится прокрутка вбок
        self.setMinimumHeight(int(min_w * H0 / W0))
        self._buf = None
        self._buf_key = None

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return int(min(w, self.max_w) * H0 / W0)

    def sizeHint(self):
        return QSize(900, int(900 * H0 / W0))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        h = self.heightForWidth(self.width())
        if self.minimumHeight() != h:
            self.setMinimumHeight(h)
            self.setMaximumHeight(h)

    def _rect(self) -> QRectF:
        w = min(self.width(), self.max_w)
        return QRectF(0, 0, w, w * H0 / W0)

    def mouseMoveEvent(self, e):
        i = hit_item(self.sc, self._rect(), e.position())
        on = i is not None and self.sc["items"][i]["t"] in ("track", "now") and self.ctx.play is not None
        self.setCursor(Qt.CursorShape.PointingHandCursor if on else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, e):
        i = hit_item(self.sc, self._rect(), e.position())
        if i is not None and self.sc["items"][i]["t"] == "track" and self.ctx.play is not None:
            self.ctx.play(self.sc["items"][i]["track"])
        elif i is not None and self.sc["items"][i]["t"] == "now" and self.ctx.play is not None and now_track(self.ctx):
            self.ctx.play(now_track(self.ctx))

    def _anim_tick(self):
        try:
            if self.isVisible() and self.window().isVisible() and not self.visibleRegion().isEmpty():
                self.update()
        except RuntimeError:
            pass

    def paintEvent(self, _):
        if animated(self.sc):                                  # живая витрина — рисуется каждый кадр
            import time
            if getattr(self, "_atimer", None) is None:
                from PyQt6.QtCore import QTimer
                self._atimer = QTimer(self)
                self._atimer.setInterval(40)
                self._atimer.timeout.connect(self._anim_tick)
                self._atimer.start()
                self._t0 = time.monotonic()
            import net_frames
            _ANIM.update(on=True, t=time.monotonic() - self._t0, lvl=net_frames.music_level())
            p = QPainter(self)
            try:
                paint_showcase(p, self._rect(), self.sc, self.ctx)
            finally:
                _ANIM["on"] = False
                p.end()
            return
        dpr = self.devicePixelRatioF()
        key = (self.width(), self.height(), dpr, id(self.sc))
        if key != self._buf_key:                               # витрина рисуется один раз — прокрутка без затрат
            R = self._rect()
            buf = QPixmap(max(1, int(R.width() * dpr)), max(1, int(R.height() * dpr)))
            buf.setDevicePixelRatio(dpr)
            buf.fill(Qt.GlobalColor.transparent)
            bp = QPainter(buf)
            paint_showcase(bp, QRectF(0, 0, R.width(), R.height()), self.sc, self.ctx)
            bp.end()
            self._buf, self._buf_key = buf, key
        p = QPainter(self)
        p.drawPixmap(0, 0, self._buf)
        p.end()


# ══════════════════════════ шапка профиля ══════════════════════════ #

def header_paint(hdr, p: QPainter, rect: QRectF):
    """Поверх стекла шапки: своя картинка (с затемнением) или аура любимых треков."""
    deco = getattr(hdr, "deco", None) or {}
    h = deco.get("hdr") or {}
    img = img_of(h.get("img", ""))
    clip = QPainterPath()
    clip.addRoundedRect(rect, 22, 22)
    p.save()
    p.setClipPath(clip)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if h.get("c1"):                                            # свой цвет / градиент
        a = math.radians(h.get("ang", 135))
        cx, cy = rect.center().x(), rect.center().y()
        dx, dy = math.cos(a) * rect.width() / 2, math.sin(a) * rect.height() / 2
        g = QLinearGradient(cx - dx, cy - dy, cx + dx, cy + dy)
        g.setColorAt(0, QColor(h["c1"]))
        g.setColorAt(1, QColor(h.get("c2") or h["c1"]))
        p.fillRect(rect, g)
    if not img.isNull():
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        s = max(rect.width() / img.width(), rect.height() / img.height())
        sw, sh = rect.width() / s, rect.height() / s
        y = (img.height() - sh) * h.get("pos", 0.5)
        p.drawImage(rect, img, QRectF((img.width() - sw) / 2, y, sw, sh))
        p.fillRect(rect, QColor(0, 0, 0, int(255 * h.get("dim", 45) / 100)))
        g = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        g.setColorAt(0.4, QColor(0, 0, 0, 0))
        g.setColorAt(1, QColor(0, 0, 0, 150))
        p.fillRect(rect, g)
    elif deco.get("aura") and not h.get("c1"):
        import ya_decor
        if ya_decor.on("net_aura"):
            cols = [QColor(c) for c in deco["aura"]]
            for i, (cx, cy, rr) in enumerate(((0.12, 0.3, 0.5), (0.7, 0.1, 0.45), (0.95, 0.9, 0.5), (0.4, 1.1, 0.4))):
                col = QColor(cols[i % len(cols)])
                col.setAlpha(90)
                g = QRadialGradient(QPointF(rect.x() + rect.width() * cx, rect.y() + rect.height() * cy),
                                    rect.width() * rr)
                g.setColorAt(0, col)
                col.setAlpha(0)
                g.setColorAt(1, col)
                p.fillRect(rect, g)
    draw = img_of(h.get("draw", ""))
    if not draw.isNull():                                      # рисунок — поверх всего, по всей шапке
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(rect, draw)
    p.restore()


HDR_W, HDR_H = 1200, 230                                       # холст рисунка шапки (тянется на всю шапку)


class HeaderDrawCanvas(QWidget):
    """Рисование поверх шапки: кисть, ластик, линия, заливка всего; под ним — текущий фон шапки."""
    def __init__(self, hdr: dict, aura=None, parent=None):
        super().__init__(parent)
        self.hdr = hdr
        self.aura = aura
        self.img = QImage(HDR_W, HDR_H, QImage.Format.Format_ARGB32_Premultiplied)
        self.img.fill(0)
        old = img_of(hdr.get("draw", ""))
        if not old.isNull():
            p = QPainter(self.img)
            p.drawImage(QRectF(0, 0, HDR_W, HDR_H), old)
            p.end()
        self.undo = []
        self.color = QColor("#ffdb1a")
        self.width_ = 8
        self.eraser = False
        self.glow = False
        self._last = None
        self.setMinimumSize(720, int(720 * HDR_H / HDR_W))
        self.setCursor(Qt.CursorShape.CrossCursor)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return int(w * HDR_H / HDR_W)

    def _R(self) -> QRectF:
        w = self.width()
        h = w * HDR_H / HDR_W
        if h > self.height():
            h = self.height()
            w = h * HDR_W / HDR_H
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def _to_img(self, pos) -> QPointF:
        R = self._R()
        return QPointF((pos.x() - R.x()) * HDR_W / R.width(), (pos.y() - R.y()) * HDR_H / R.height())

    def _push(self):
        self.undo.append(self.img.copy())
        del self.undo[:-30]

    def do_undo(self):
        if self.undo:
            self.img = self.undo.pop()
            self.update()

    def clear(self):
        self._push()
        self.img.fill(0)
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._push()
            self._last = self._to_img(e.position())
            self._stroke(self._last, self._last)

    def mouseMoveEvent(self, e):
        if self._last is not None:
            pt = self._to_img(e.position())
            self._stroke(self._last, pt)
            self._last = pt

    def mouseReleaseEvent(self, e):
        self._last = None

    def _stroke(self, a, b):
        p = QPainter(self.img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.eraser:
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        if self.glow and not self.eraser:
            gc = QColor(self.color)
            gc.setAlpha(55)
            p.setPen(QPen(gc, self.width_ * 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.drawLine(a, b)
        p.setPen(QPen(self.color if not self.eraser else QColor(0, 0, 0), self.width_, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawLine(a, b)
        p.end()
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(14, 14, 16))
        R = self._R()

        class _H:
            pass
        h = _H()
        h.deco = {"hdr": {k: v for k, v in self.hdr.items() if k != "draw"}, "aura": self.aura or []}
        p.fillRect(R, QColor(12, 12, 14))
        header_paint(h, p, R)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(R, self.img)
        p.setPen(QPen(QColor(255, 255, 255, 40), 1, Qt.PenStyle.DashLine))   # где будут аватарка и имя
        p.setBrush(Qt.BrushStyle.NoBrush)
        k = R.width() / HDR_W
        p.drawEllipse(QRectF(R.x() + 28 * k, R.y() + 18 * k, 170 * k * HDR_W / 1200, 170 * k * HDR_W / 1200))
        p.end()

    def result_png(self) -> str:
        if all(self.img.pixelColor(x, y).alpha() == 0 for x in range(0, HDR_W, 24) for y in range(0, HDR_H, 12)):
            return ""                                          # пусто
        for w in (HDR_W, 960, 800, 640):
            im = self.img.scaled(w, int(w * HDR_H / HDR_W), Qt.AspectRatioMode.IgnoreAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            im.save(buf, "PNG", 9)
            b = base64.b64encode(bytes(ba)).decode()
            if len(b) <= 150000:
                return b
        return ""


class HeaderDrawer(QDialog):
    def __init__(self, hdr: dict, aura=None, parent=None):
        super().__init__(parent)
        import net_frames
        from PyQt6.QtWidgets import QButtonGroup, QGridLayout
        self.setWindowTitle("Рисовать на шапке профиля")
        self.setStyleSheet(net_frames._QSS)
        self.resize(1180, 520)
        self.result = None
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        t = QLabel("Рисунок на шапке профиля")
        t.setStyleSheet("font-size:20px;font-weight:900;")
        root.addWidget(t)
        tip = QLabel("Рисуйте мышкой поверх фона шапки. Пунктирный круг — где будет аватарка.")
        tip.setObjectName("Sub")
        root.addWidget(tip)
        self.canvas = HeaderDrawCanvas(hdr, aura)
        root.addWidget(self.canvas, 1)
        bar = QHBoxLayout()
        pal = QGridLayout()
        pal.setSpacing(4)
        cols = ("#ffdb1a", "#ff9d00", "#ff3b3b", "#ff5fa8", "#c77dff", "#7c8cff", "#2fc6ff", "#5ef2c2", "#7ddc6a",
                "#ffffff", "#9a9aa2", "#000000")
        for i, cc in enumerate(cols):
            b = QPushButton()
            b.setFixedSize(28, 24)
            b.setStyleSheet(f"QPushButton{{background:{cc};border:2px solid rgba(255,255,255,0.18);border-radius:7px;"
                            "min-height:24px;max-height:24px;padding:0;} QPushButton:hover{border-color:#fff;}")
            b.clicked.connect(lambda _=False, cc=cc: self._color(QColor(cc)))
            pal.addWidget(b, i // 6, i % 6)
        bar.addLayout(pal)
        bar.addWidget(_btn("Другой цвет…", lambda: self._color(QColorDialog.getColor(self.canvas.color, self, "Цвет"))))
        self.w_lbl = QLabel()
        bar.addWidget(self.w_lbl)
        ws = QSlider(Qt.Orientation.Horizontal)
        ws.setRange(1, 60)
        ws.setFixedWidth(140)
        ws.setValue(self.canvas.width_)
        ws.valueChanged.connect(self._width)
        bar.addWidget(ws)
        self._width(ws.value())
        grp = QButtonGroup(self)
        for name, er in (("Кисть", False), ("Ластик", True)):
            b = QPushButton(name)
            b.setCheckable(True)
            b.setChecked(not er)
            b.clicked.connect(lambda _=False, er=er: setattr(self.canvas, "eraser", er))
            grp.addButton(b)
            bar.addWidget(b)
        g = QCheckBox("Свечение")
        g.toggled.connect(lambda on: setattr(self.canvas, "glow", on))
        bar.addWidget(g)
        bar.addWidget(_btn("Отменить", self.canvas.do_undo))
        bar.addWidget(_btn("Очистить", self.canvas.clear))
        bar.addStretch(1)
        bar.addWidget(_btn("Отмена", self.reject))
        bar.addWidget(_btn("Готово", self._done, True))
        root.addLayout(bar)

    def _color(self, c):
        if c is not None and c.isValid():
            self.canvas.color = c
            self.canvas.eraser = False

    def _width(self, v):
        self.canvas.width_ = v
        self.w_lbl.setText(f"Толщина: {v}")

    def _done(self):
        self.result = self.canvas.result_png()
        self.accept()


# ══════════════════════════ редактор ══════════════════════════ #

class EditCanvas(QWidget):
    """Холст редактора: выбор, перетаскивание, уголок — размер. Del — удалить, стрелки — сдвиг."""
    selected = pyqtSignal(object)
    changed = pyqtSignal()
    context = pyqtSignal(object, object)                       # ПКМ: (индекс блока | None, глобальная точка)
    edit_item = pyqtSignal(int)                                # двойной клик по блоку

    def __init__(self, sc, ctx, parent=None, min_w=560):
        super().__init__(parent)
        self.sc, self.ctx = sc, ctx
        self.sel = None
        self._drag = None
        self.tools = None                    # окошко инструментов — видно, пока виден холст
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(min_w, int(min_w * H0 / W0))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return int(w * H0 / W0)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        h = self.heightForWidth(self.width())                  # витрина тянется по ширине, высота — пропорцией
        if self.minimumHeight() != h:
            self.setMinimumHeight(h)
            self.setMaximumHeight(h)

    def showEvent(self, e):
        super().showEvent(e)
        if self.tools is not None:
            self.tools.show_near()

    def hideEvent(self, e):
        super().hideEvent(e)
        if self.tools is not None:
            try:
                self.tools.hide()
            except RuntimeError:
                pass

    def _R(self) -> QRectF:
        w = self.width()
        h = w * H0 / W0
        if h > self.height():
            h = self.height()
            w = h * W0 / H0
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    CORNERS = ("tl", "tr", "bl", "br")

    def _handles(self, i) -> dict:
        r = item_rect(self.sc["items"][i], self._R())
        pts = {"tl": r.topLeft(), "tr": r.topRight(), "bl": r.bottomLeft(), "br": r.bottomRight()}
        return {k: QRectF(p.x() - 7, p.y() - 7, 14, 14) for k, p in pts.items()}

    def _handle(self, i) -> QRectF:
        return self._handles(i)["br"]

    def _corner_at(self, pos):
        if self.sel is None or self.sel >= len(self.sc["items"]):
            return None
        if self.sc["items"][self.sel].get("rot"):              # повёрнутый — тянется только за нижний правый
            return "br" if self._handle(self.sel).contains(pos) else None
        return next((k for k, r in self._handles(self.sel).items() if r.contains(pos)), None)

    def _keep_ratio(self, it, e) -> bool:
        """Картинки и наклейки тянутся пропорционально (Shift — свободно); остальное — наоборот."""
        shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        return (not shift) if it["t"] in ("img", "sticker", "track", "stat", "ach") else shift

    def mousePressEvent(self, e):
        self.setFocus()
        pos = e.position()
        if e.button() == Qt.MouseButton.RightButton:            # ПКМ — меню блока (или холста)
            i = hit_item(self.sc, self._R(), pos)
            self.sel = i
            self.selected.emit(i)
            self.update()
            self.context.emit(i, e.globalPosition().toPoint())
            return
        corner = self._corner_at(pos)
        if corner is not None:
            self._drag = ("size:" + corner, pos, dict(self.sc["items"][self.sel]))
            return
        i = hit_item(self.sc, self._R(), pos)
        self.sel = i
        self.selected.emit(i)
        if i is not None:
            self._drag = ("move", pos, dict(self.sc["items"][i]))
        self.update()

    def mouseDoubleClickEvent(self, e):
        i = hit_item(self.sc, self._R(), e.position())
        if i is not None:
            self.edit_item.emit(i)

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self._drag is None:
            c = self._corner_at(pos)
            self.setCursor(Qt.CursorShape.SizeFDiagCursor if c in ("tl", "br") else
                           Qt.CursorShape.SizeBDiagCursor if c in ("tr", "bl") else
                           Qt.CursorShape.SizeAllCursor if hit_item(self.sc, self._R(), pos) is not None
                           else Qt.CursorShape.ArrowCursor)
            return
        kind, p0, orig = self._drag
        R = self._R()
        dx, dy = (pos.x() - p0.x()) / R.width(), (pos.y() - p0.y()) / R.height()
        it = self.sc["items"][self.sel]
        if kind == "move":
            it["x"] = round(max(-0.9, min(1.4, orig["x"] + dx)), 4)
            it["y"] = round(max(-0.9, min(1.4, orig["y"] + dy)), 4)
            for key, size in (("x", "w"), ("y", "h")):              # прилипание к краям и центру
                for target in (0.0, 0.5 - it[size] / 2, 1.0 - it[size]):
                    if abs(it[key] - target) < 0.012:
                        it[key] = round(target, 4)
        else:
            c = kind.split(":")[1]
            sx = 1 if c in ("tr", "br") else -1                # тянем правый или левый край
            sy = 1 if c in ("bl", "br") else -1
            w = max(0.02, min(3.0, orig["w"] + sx * dx))
            h = max(0.02, min(3.0, orig["h"] + sy * dy))
            if self._keep_ratio(it, e):                         # по масштабу — без искажения
                k = max(w / max(0.001, orig["w"]), h / max(0.001, orig["h"]))
                w, h = orig["w"] * k, orig["h"] * k
            it["w"], it["h"] = round(w, 4), round(h, 4)
            it["x"] = round(orig["x"] + (orig["w"] - w if sx < 0 else 0), 4)
            it["y"] = round(orig["y"] + (orig["h"] - h if sy < 0 else 0), 4)
        self.update()

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            self._drag = None
            self.changed.emit()

    def keyPressEvent(self, e):
        if self.sel is None or self.sel >= len(self.sc["items"]):
            return super().keyPressEvent(e)
        it = self.sc["items"][self.sel]
        step = 0.01 if not e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 0.05
        k = e.key()
        if k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.sc["items"].pop(self.sel)
            self.sel = None
            self.selected.emit(None)
        elif k == Qt.Key.Key_Left:
            it["x"] -= step
        elif k == Qt.Key.Key_Right:
            it["x"] += step
        elif k == Qt.Key.Key_Up:
            it["y"] -= step
        elif k == Qt.Key.Key_Down:
            it["y"] += step
        elif k in (Qt.Key.Key_Plus, Qt.Key.Key_Equal, Qt.Key.Key_Minus):   # +/- — масштаб от центра
            f = 1.08 if k != Qt.Key.Key_Minus else 1 / 1.08
            cx, cy = it["x"] + it["w"] / 2, it["y"] + it["h"] / 2
            it["w"], it["h"] = max(0.02, it["w"] * f), max(0.02, it["h"] * f)
            it["x"], it["y"] = cx - it["w"] / 2, cy - it["h"] / 2
        else:
            return super().keyPressEvent(e)
        self.update()
        self.changed.emit()

    def wheelEvent(self, e):
        """Ctrl + колесо над выбранным блоком — масштаб по центру (пропорционально)."""
        if not e.modifiers() & Qt.KeyboardModifier.ControlModifier or self.sel is None or \
                self.sel >= len(self.sc["items"]):
            return super().wheelEvent(e)
        it = self.sc["items"][self.sel]
        f = 1.06 if e.angleDelta().y() > 0 else 1 / 1.06
        cx, cy = it["x"] + it["w"] / 2, it["y"] + it["h"] / 2
        it["w"], it["h"] = max(0.02, min(3.0, it["w"] * f)), max(0.02, min(3.0, it["h"] * f))
        it["x"], it["y"] = round(cx - it["w"] / 2, 4), round(cy - it["h"] / 2, 4)
        self.update()
        self.changed.emit()
        e.accept()

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(14, 14, 16))
        R = self._R()
        paint_showcase(p, R, self.sc, self.ctx, 14)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(255, 255, 255, 14), 1, Qt.PenStyle.DashLine))  # центр и поля
        p.drawLine(QPointF(R.center().x(), R.top()), QPointF(R.center().x(), R.bottom()))
        if self.sel is not None and self.sel < len(self.sc["items"]):
            it = self.sc["items"][self.sel]
            r = item_rect(it, R)
            p.save()
            if it.get("rot"):
                c = r.center()
                p.translate(c)
                p.rotate(it["rot"])
                p.translate(-c)
            p.setPen(QPen(QColor("#ffdb1a"), 1.5, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(r)
            p.restore()
            p.setPen(QPen(QColor(0, 0, 0, 160), 1))
            p.setBrush(QColor("#ffdb1a"))
            hs = self._handles(self.sel) if not it.get("rot") else {"br": self._handle(self.sel)}
            for hr in hs.values():
                p.drawRoundedRect(hr, 3, 3)
        p.end()


def _btn(text, fn, accent=False):
    b = QPushButton(text)
    if accent:
        b.setObjectName("Accent")
    b.clicked.connect(fn)
    return b


TEMPLATES = {
    "Меломан": {"bg": {"c1": "#ffb000", "c2": "#3a0d4a", "ang": 120, "img": "", "dim": 30},
                "items": [{"t": "text", "x": 0.05, "y": 0.08, "w": 0.6, "h": 0.22, "text": "Музыка — это всё",
                           "size": 56, "color": "#ffffff", "bold": True, "align": "l", "shadow": True, "font": ""},
                          {"t": "stat", "x": 0.05, "y": 0.42, "w": 0.2, "h": 0.36, "k": "listened", "color": "#ffdb1a"},
                          {"t": "stat", "x": 0.28, "y": 0.42, "w": 0.2, "h": 0.36, "k": "tracks", "color": "#ffffff"},
                          {"t": "sticker", "x": 0.8, "y": 0.08, "w": 0.15, "h": 0.36, "k": "vinyl", "color": "#ffdb1a",
                           "rot": 0},
                          {"t": "sticker", "x": 0.7, "y": 0.6, "w": 0.07, "h": 0.17, "k": "note", "color": "#ffffff",
                           "rot": -12}]},
    "Минимал": {"bg": {"c1": "#15161a", "c2": "#050506", "ang": 90, "img": "", "dim": 30},
                "items": [{"t": "text", "x": 0.06, "y": 0.3, "w": 0.88, "h": 0.4, "text": "слушаю тишину между треками",
                           "size": 44, "color": "#e8e8e8", "bold": False, "align": "c", "shadow": False, "font": "mono"},
                          {"t": "shape", "x": 0.42, "y": 0.72, "w": 0.16, "h": 0.012, "k": "rect", "color": "#ffdb1a",
                           "alpha": 230, "radius": 50}]},
    "Неон": {"bg": {"c1": "#ff3cf0", "c2": "#1b0a5a", "ang": 160, "img": "", "dim": 30},
             "items": [{"t": "shape", "x": 0.62, "y": -0.2, "w": 0.5, "h": 1.2, "k": "circle", "color": "#2fc6ff",
                        "alpha": 70, "radius": 50},
                       {"t": "text", "x": 0.05, "y": 0.12, "w": 0.6, "h": 0.3, "text": "NIGHT DRIVE", "size": 72,
                        "color": "#ffffff", "bold": True, "align": "l", "shadow": True, "font": "rounded"},
                       {"t": "sticker", "x": 0.08, "y": 0.6, "w": 0.06, "h": 0.14, "k": "sparkle", "color": "#ffffff"},
                       {"t": "sticker", "x": 0.16, "y": 0.66, "w": 0.04, "h": 0.1, "k": "star", "color": "#ffe066"},
                       {"t": "sticker", "x": 0.8, "y": 0.55, "w": 0.12, "h": 0.28, "k": "moon", "color": "#ffe066",
                        "rot": 18}]},
}


class ShowcaseTools(QFrame):
    """Плавающее окошко инструментов витрины — поверх профиля (сама витрина правится прямо в профиле).
    Таскается за заголовок. «Сохранить» — saved(deco), «Отмена» — cancelled; changed — живой предпросмотр шапки."""
    saved = pyqtSignal(dict)
    cancelled = pyqtSignal()
    changed = pyqtSignal()
    delete_all = pyqtSignal()                                 # «Удалить витрину из профиля»
    W = 340

    def __init__(self, deco: dict, ctx: Ctx, tracks=(), host=None):
        super().__init__(host)
        import net_frames
        self.setObjectName("ScTools")
        self.setStyleSheet(net_frames._QSS.replace("QDialog{background:#111113;}", "") +
                           "QFrame#ScTools{background:rgba(20,20,23,0.97);border:1px solid rgba(255,255,255,0.10);"
                           "border-radius:16px;} QWidget#ScInner{background:transparent;}"
                           "QComboBox, QLineEdit, QSpinBox{background:#1d1d20;color:#f0f0f0;"
                           "border:1px solid #2c2c30;border-radius:9px;padding:4px 8px;}")
        self.deco = copy.deepcopy(deco or {})
        sc = self.deco.get("sc") or copy.deepcopy(TEMPLATES["Меломан"])
        sc.setdefault("bg", {"c1": "#1a1a22", "c2": "#08080a", "ang": 135, "img": "", "dim": 30})
        sc.setdefault("items", [])
        self.sc = sc
        self.ctx = ctx
        self.tracks = [t for t in tracks if t]
        self.canvas = None
        self._drag = None
        self._placed = False
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 12)
        root.setSpacing(6)
        head = QHBoxLayout()
        self.grip = QLabel("⠿  Инструменты витрины")
        self.grip.setStyleSheet("font-size:14px;font-weight:900;")
        self.grip.setCursor(Qt.CursorShape.SizeAllCursor)
        self.grip.setToolTip("Перетащите окошко за заголовок")
        self.grip.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        head.addWidget(self.grip, 1)
        x = QPushButton("✕")
        x.setFixedSize(28, 28)
        x.setToolTip("Закрыть без сохранения")
        x.clicked.connect(self.cancelled.emit)
        head.addWidget(x)
        root.addLayout(head)
        tip = QLabel("Тяните блоки на витрине, уголки — размер, ПКМ — действия с блоком, Del — удалить.")
        tip.setObjectName("Sub")
        tip.setWordWrap(True)
        root.addWidget(tip)
        scr = QScrollArea()
        scr.setWidgetResizable(True)
        inner = QWidget()
        inner.setObjectName("ScInner")
        self.side = QVBoxLayout(inner)
        self.side.setContentsMargins(0, 0, 6, 0)
        self.side.setSpacing(6)
        self.side.addWidget(net_frames._sec("ДОБАВИТЬ"))
        from PyQt6.QtWidgets import QGridLayout
        g = QGridLayout()
        g.setSpacing(5)
        for i, (text, fn) in enumerate((("+ Текст", self._add_text), ("+ Картинка", self._add_img),
                                        ("+ Трек", self._add_track), ("+ Статистика", self._add_stat),
                                        ("+ Ачивка", self._add_ach), ("+ Наклейка", self._add_sticker),
                                        ("+ Фигура", self._add_shape), ("+ Сейчас играет", self._add_now))):
            g.addWidget(_btn(text, fn), i // 2, i % 2)
        self.side.addLayout(g)
        self.side.addWidget(net_frames._sec("ШАБЛОНЫ"))
        tpl = QHBoxLayout()
        for name in TEMPLATES:
            tpl.addWidget(_btn(name, lambda _=False, n=name: self._template(n)))
        self.side.addLayout(tpl)
        self.side.addWidget(_btn("Пустой холст", lambda: self._template(None)))
        self.props_box = QVBoxLayout()
        self.side.addLayout(self.props_box)
        self.side.addWidget(net_frames._sec("ФОН ВИТРИНЫ"))
        bgr = QHBoxLayout()
        bgr.addWidget(self._swatch(self.sc["bg"]["c1"], lambda c: self._bg("c1", c)))
        bgr.addWidget(self._swatch(self.sc["bg"]["c2"], lambda c: self._bg("c2", c)))
        bgr.addWidget(_btn("Картинка…", self._bg_img))
        bgr.addWidget(_btn("✕", lambda: self._bg("img", "")))
        self.side.addLayout(bgr)
        self.side.addWidget(QLabel("Угол градиента"))
        self.side.addWidget(self._slider(0, 360, int(self.sc["bg"].get("ang", 135)), lambda v: self._bg("ang", v)))
        self.side.addWidget(QLabel("Затемнение картинки"))
        self.side.addWidget(self._slider(0, 90, int(self.sc["bg"].get("dim", 30)), lambda v: self._bg("dim", v)))
        bt = QCheckBox("Фон пульсирует под музыку")
        bt.setChecked(bool(self.sc["bg"].get("beat")))
        bt.setToolTip("У того, кто смотрит профиль, фон витрины вспыхивает в такт его музыке")
        bt.toggled.connect(lambda on: (self.sc["bg"].pop("beat", None), self._bg("beat", True) if on else self._changed()))
        self.side.addWidget(bt)
        self.side.addWidget(net_frames._sec("ФОН ШАПКИ ПРОФИЛЯ"))
        hd = self.deco.get("hdr") or {}
        self.hdr_lbl = QLabel()
        self.hdr_lbl.setObjectName("Sub")
        self.hdr_lbl.setWordWrap(True)
        self.side.addWidget(self.hdr_lbl)
        hc = QHBoxLayout()
        hc.addWidget(QLabel("Цвет"))
        self.hdr_c1 = self._swatch(hd.get("c1") or "#2a1f4a", lambda c: self._hdr_color("c1", c))
        self.hdr_c2 = self._swatch(hd.get("c2") or hd.get("c1") or "#0c0c12", lambda c: self._hdr_color("c2", c))
        hc.addWidget(self.hdr_c1)
        hc.addWidget(QLabel("→"))
        hc.addWidget(self.hdr_c2)
        hc.addWidget(_btn("Один цвет", lambda: self._hdr_color("c2", (self.deco.get("hdr") or {}).get("c1") or "#2a1f4a")))
        hc.addStretch(1)
        self.side.addLayout(hc)
        self.side.addWidget(QLabel("Угол градиента шапки"))
        self.side.addWidget(self._slider(0, 360, int(hd.get("ang", 135)), lambda v: self._hdr_set("ang", v)))
        hr = QHBoxLayout()
        hr.addWidget(_btn("Картинка…", self._hdr_img))
        hr.addWidget(_btn("Рисовать…", self._hdr_draw))
        hr.addWidget(_btn("Сбросить", self._hdr_clear))
        self.side.addLayout(hr)
        self._hdr_label()
        self.side.addWidget(QLabel("Затемнение"))
        self.hdr_dim = self._slider(0, 90, int(hd.get("dim", 45)), lambda v: self._hdr("dim", v))
        self.side.addWidget(self.hdr_dim)
        self.side.addWidget(QLabel("Что видно: верх ↔ низ картинки"))
        self.side.addWidget(self._slider(0, 100, int(100 * hd.get("pos", 0.5)), lambda v: self._hdr("pos", v / 100)))
        self.side.addStretch(1)
        scr.setWidget(inner)
        root.addWidget(scr, 1)
        self.budget = QLabel()
        self.budget.setObjectName("Sub")
        root.addWidget(self.budget)
        bot = QHBoxLayout()
        bot.addWidget(_btn("Удалить витрину", self.delete_all.emit))
        bot.addStretch(1)
        bot.addWidget(_btn("Отмена", self.cancelled.emit))
        self.b_ok = _btn("Сохранить", lambda: self.saved.emit(self.result()), True)
        bot.addWidget(self.b_ok)
        root.addLayout(bot)
        self.setFixedWidth(self.W)
        self.hide()
        self._select(None)
        self._update_budget()

    # ── холст в профиле ── #

    def make_canvas(self) -> "EditCanvas":
        """Новый холст (профиль пересобирается — старый удаляется вместе со страницей)."""
        c = EditCanvas(self.sc, self.ctx, min_w=900)
        c.tools = self
        c.selected.connect(self._select)
        c.changed.connect(self._changed)
        c.context.connect(self._context)
        c.edit_item.connect(self._edit_item)
        self.canvas = c
        return c

    def _canvas_ok(self) -> bool:
        try:
            return self.canvas is not None and self.canvas.isVisible() is not None
        except RuntimeError:
            self.canvas = None
            return False

    def show_near(self):
        host = self.parentWidget()
        if host is None:
            return
        self.setFixedHeight(max(360, min(680, host.height() - 120)))
        if not self._placed:                                   # первый раз — справа, дальше там, куда утащили
            self._placed = True
            self.move(host.width() - self.W - 40, 90)
        self._clamp()
        self.show()
        self.raise_()

    def _clamp(self):
        host = self.parentWidget()
        if host is None:
            return
        self.move(max(0, min(self.x(), host.width() - self.width())),
                  max(0, min(self.y(), host.height() - self.height())))

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and e.position().y() < 40:
            self._drag = e.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)
            self._clamp()

    def mouseReleaseEvent(self, e):
        self._drag = None

    # ── помощники ── #

    def _swatch(self, color, on_pick):
        b = QPushButton()
        b.setFixedSize(40, 28)
        b.setStyleSheet(f"QPushButton{{background:{color};border:2px solid rgba(255,255,255,0.2);border-radius:8px;}}")

        def pick():
            c = QColorDialog.getColor(QColor(color), self, "Цвет")
            if c.isValid():
                b.setStyleSheet(f"QPushButton{{background:{c.name()};border:2px solid rgba(255,255,255,0.2);"
                                "border-radius:8px;}")
                on_pick(c.name())
        b.clicked.connect(pick)
        return b

    def _slider(self, lo, hi, val, fn):
        s = QSlider(Qt.Orientation.Horizontal)
        s.setRange(lo, hi)
        s.setValue(val)
        s.valueChanged.connect(fn)
        return s

    def _update_budget(self):
        n = size_of(self.result())
        self.budget.setText(f"Размер оформления: {n / 1000:.0f} из {LIMIT // 1000} КБ" +
                            ("  — слишком много, уменьшите картинки" if n > LIMIT else
                             "  · незнакомым уйдёт ужатая копия" if n > PUBLIC_LIMIT else ""))
        self.b_ok.setEnabled(n <= LIMIT)

    def _changed(self):
        if self._canvas_ok():
            self.canvas.update()
        self._update_budget()
        self.changed.emit()

    def _pick_image(self, side, limit, png=False) -> str:
        fn, _ = QFileDialog.getOpenFileName(self, "Картинка", "", "Картинки (*.png *.jpg *.jpeg *.webp *.bmp *.gif)")
        if not fn:
            return ""
        img = QImage(fn)
        return jpeg_b64(img, side, limit, png=png and img.hasAlphaChannel())

    def _bg(self, k, v):
        self.sc["bg"][k] = v
        self._changed()

    def _bg_img(self):
        b = self._pick_image(1600, 120000)
        if b:
            self._bg("img", b)

    def _hdr(self, k, v):
        if self.deco.get("hdr"):
            self.deco["hdr"][k] = v
            self._changed()


    def _hdr_label(self):
        h = self.deco.get("hdr") or {}
        bits = (["цвет" if h.get("c1") == h.get("c2") else "градиент"] if h.get("c1") else []) + \
            (["картинка"] if h.get("img") else []) + (["рисунок"] if h.get("draw") else [])
        self.hdr_lbl.setText("Сейчас: " + " + ".join(bits) if bits else "Сейчас: аура из любимых треков")

    def _hdr_obj(self) -> dict:
        return self.deco.setdefault("hdr", {"dim": self.hdr_dim.value() if hasattr(self, "hdr_dim") else 45,
                                             "pos": 0.5})

    def _hdr_set(self, k, v):
        h = self._hdr_obj()
        h[k] = v
        if k == "ang" and not h.get("c1"):
            return
        self._hdr_label()
        self._changed()

    def _hdr_color(self, k, c):
        h = self._hdr_obj()
        if not h.get("c1"):
            h["c1"] = c
            h["c2"] = c if k == "c1" else h.get("c2", c)
        h[k] = c
        h.setdefault("ang", 135)
        for key, sw in (("c1", self.hdr_c1), ("c2", self.hdr_c2)):
            sw.setStyleSheet(f"QPushButton{{background:{h[key]};border:2px solid rgba(255,255,255,0.2);"
                             "border-radius:8px;}")
        self._hdr_label()
        self._changed()

    def _hdr_img(self):
        b = self._pick_image(1800, 120000)
        if b:
            self._hdr_obj()["img"] = b
            self._hdr_label()
            self._changed()

    def _hdr_draw(self):
        d = HeaderDrawer(self._hdr_obj(), self.deco.get("aura"), self)
        if d.exec() and d.result is not None:
            h = self._hdr_obj()
            if d.result:
                h["draw"] = d.result
            else:
                h.pop("draw", None)
            self._hdr_label()
            self._changed()

    def _hdr_clear(self):
        self.deco.pop("hdr", None)
        self._hdr_label()
        self._changed()

    def _set_sel(self, i):
        if self._canvas_ok():
            self.canvas.sel = i
        self._select(i)

    def _template(self, name):
        if name is None:
            self.sc["items"] = []
        else:
            t = copy.deepcopy(TEMPLATES[name])
            self.sc["bg"] = t["bg"]
            self.sc["items"] = t["items"]
        self._set_sel(None)
        self._changed()

    def _remove(self):
        self.sc["items"] = []
        self._set_sel(None)
        self._changed()

    def _add(self, it):
        it.setdefault("rot", 0)
        it.setdefault("op", 1)
        self.sc["items"].append(it)
        self._set_sel(len(self.sc["items"]) - 1)
        self._changed()

    def _menu(self, items):
        m = QMenu(self)
        for text, fn in items:
            a = m.addAction(text)
            if fn is None:
                a.setEnabled(False)
            else:
                a.triggered.connect(fn)
        m.exec(self.cursor().pos())

    def _add_text(self):
        self._add({"t": "text", "x": 0.1, "y": 0.1, "w": 0.5, "h": 0.2, "text": "Новый текст", "size": 40,
                   "color": "#ffffff", "bold": True, "align": "l", "shadow": True, "font": ""})

    def _img_item_size(self, b, w=0.3):
        """Размер блока под пропорции картинки (на холсте 1000×420)."""
        img = img_of(b)
        if img.isNull():
            return w, 0.6
        h = w * W0 / H0 * img.height() / max(1, img.width())
        if h > 0.9:
            w, h = w * 0.9 / h, 0.9
        return round(w, 4), round(h, 4)

    def _add_img(self):
        b = self._pick_image(1000, 100000, png=True)
        if b:
            w, h = self._img_item_size(b)
            self._add({"t": "img", "x": 0.5 - w / 2, "y": 0.5 - h / 2, "w": w, "h": h, "img": b, "radius": 6,
                       "fit": "cover"})

    # ── ПКМ по блоку / холсту ── #

    def _context(self, i, gpos):
        m = QMenu(self.canvas)
        if i is None or i >= len(self.sc["items"]):
            add = m.addMenu("Добавить")
            for text, fn in (("Текст", self._add_text), ("Картинку…", self._add_img), ("Трек", self._add_track),
                             ("Статистику", self._add_stat), ("Ачивку", self._add_ach), ("Наклейку", self._add_sticker),
                             ("Фигуру", self._add_shape), ("«Сейчас играет»", self._add_now)):
                add.addAction(text, fn)
            m.addAction("Фон витрины: картинка…", self._bg_img)
            if self.sc["bg"].get("img"):
                m.addAction("Фон витрины: убрать картинку", lambda: self._bg("img", ""))
            m.addSeparator()
            m.addAction("Очистить холст", lambda: self._template(None))
            m.addAction("Удалить витрину из профиля", self.delete_all.emit)
            m.exec(gpos)
            return
        it = self.sc["items"][i]
        t = it["t"]
        if t == "img":
            m.addAction("Заменить картинку…", lambda: self._replace_img(i))
            m.addAction("Сохранить картинку как…", lambda: self._save_img(i))
            fit = m.addMenu("Как показывать")
            for k, name in (("cover", "Заполнить (обрезать края)"), ("contain", "Вписать целиком"),
                            ("stretch", "Растянуть по рамке")):
                a = fit.addAction(("● " if it.get("fit", "cover") == k else "   ") + name)
                a.triggered.connect(lambda _=False, k=k: self._set(i, "fit", k))
            m.addAction("Исходные пропорции", lambda: self._natural(i))
            m.addAction("Растянуть на всю витрину", lambda: self._fill(i))
            m.addSeparator()
        elif t == "text":
            m.addAction("Изменить текст…", lambda: self._edit_item(i))
            m.addSeparator()
        elif t == "track":
            if self.ctx.play is not None:
                m.addAction("Слушать", lambda: self.ctx.play(it["track"]))
            m.addSeparator()
        sz = m.addMenu("Размер")
        for name, f in (("Больше  (+)", 1.15), ("Меньше  (−)", 1 / 1.15), ("Вдвое больше", 2.0), ("Вдвое меньше", 0.5)):
            sz.addAction(name, lambda f=f: self._scale(i, f))
        m.addAction("По центру", lambda: self._center(i))
        if it.get("rot"):
            m.addAction("Сбросить поворот", lambda: self._set(i, "rot", 0))
        m.addSeparator()
        m.addAction("На передний план", lambda: self._to_end(i, True))
        m.addAction("На задний план", lambda: self._to_end(i, False))
        m.addAction("Копия", lambda: self._dup(i))
        m.addSeparator()
        m.addAction("Удалить блок", lambda: self._del(i))
        m.addAction("Удалить витрину из профиля", self.delete_all.emit)
        m.exec(gpos)

    def _set(self, i, k, v):
        self.sc["items"][i][k] = v
        self._set_sel(i)
        self._changed()

    def _scale(self, i, f):
        it = self.sc["items"][i]
        cx, cy = it["x"] + it["w"] / 2, it["y"] + it["h"] / 2
        it["w"], it["h"] = max(0.02, min(3.0, it["w"] * f)), max(0.02, min(3.0, it["h"] * f))
        it["x"], it["y"] = round(cx - it["w"] / 2, 4), round(cy - it["h"] / 2, 4)
        self._changed()

    def _center(self, i):
        it = self.sc["items"][i]
        it["x"], it["y"] = round(0.5 - it["w"] / 2, 4), round(0.5 - it["h"] / 2, 4)
        self._changed()

    def _natural(self, i):
        it = self.sc["items"][i]
        w, h = self._img_item_size(it["img"], it["w"])
        cx, cy = it["x"] + it["w"] / 2, it["y"] + it["h"] / 2
        it["w"], it["h"], it["x"], it["y"] = w, h, round(cx - w / 2, 4), round(cy - h / 2, 4)
        self._changed()

    def _fill(self, i):
        it = self.sc["items"][i]
        it.update(x=0.0, y=0.0, w=1.0, h=1.0, rot=0, fit="cover", radius=0)
        self._set_sel(i)
        self._changed()

    def _to_end(self, i, front):
        its = self.sc["items"]
        it = its.pop(i)
        if front:
            its.append(it)
            self._set_sel(len(its) - 1)
        else:
            its.insert(0, it)
            self._set_sel(0)
        self._changed()

    def _replace_img(self, i):
        b = self._pick_image(1000, 100000, png=True)
        if b:
            self.sc["items"][i]["img"] = b
            self._changed()

    def _save_img(self, i):
        img = img_of(self.sc["items"][i].get("img", ""))
        if img.isNull():
            return
        fn, _ = QFileDialog.getSaveFileName(self, "Сохранить картинку", "картинка.png", "PNG (*.png);;JPEG (*.jpg)")
        if fn:
            img.save(fn)

    def _edit_item(self, i):
        it = self.sc["items"][i]
        if it["t"] == "text":
            from PyQt6.QtWidgets import QInputDialog
            t, ok = QInputDialog.getMultiLineText(self, "Текст", "Текст блока:", it.get("text", ""))
            if ok:
                self._set(i, "text", t[:300])
        else:
            self._set_sel(i)

    def _add_track(self):
        self._menu([(f"{tr.get('artist', '')} — {tr.get('title', '')}",
                     lambda _=False, tr=tr: self._add_track_item(tr)) for tr in self.tracks[:20]]
                   or [("Нет треков: включите трек или добавьте любимые", None)])

    def _add_track_item(self, tr):
        import net_page
        meta = net_page.track_meta(tr) or {}
        cv = str(tr.get("cover") or "")
        img = jpeg_b64(QImage(cv), 128, 8000) if cv else ""
        self._add({"t": "track", "x": 0.55, "y": 0.62, "w": 0.4, "h": 0.26, "track": meta, "img": img})

    def _add_stat(self):
        self._menu([(name, lambda _=False, k=k: self._add({"t": "stat", "x": 0.7, "y": 0.1, "w": 0.2, "h": 0.36,
                                                           "k": k, "color": "#ffdb1a"}))
                    for k, name in STATS.items()])

    def _add_ach(self):
        self._menu([(self.ctx.ach_names.get(k, (k,))[0],
                     lambda _=False, k=k: self._add({"t": "ach", "x": 0.05, "y": 0.75, "w": 0.3, "h": 0.14, "k": k}))
                    for k in sorted(self.ctx.ach)] or [("Пока нет ачивок", None)])

    def _add_sticker(self):
        self._menu([(name, lambda _=False, k=k: self._add({"t": "sticker", "x": 0.45, "y": 0.35, "w": 0.1,
                                                           "h": 0.24, "k": k, "color": "#ffdb1a"}))
                    for k, name in STICKERS.items()])

    def _add_now(self):
        self._add({"t": "now", "x": 0.05, "y": 0.66, "w": 0.42, "h": 0.26, "color": "#ffdb1a"})

    def _add_shape(self):
        self._menu([("Прямоугольник", lambda: self._add({"t": "shape", "x": 0.05, "y": 0.05, "w": 0.4, "h": 0.4,
                                                          "k": "rect", "color": "#ffffff", "alpha": 40,
                                                          "radius": 12})),
                    ("Круг", lambda: self._add({"t": "shape", "x": 0.6, "y": 0.1, "w": 0.3, "h": 0.7, "k": "circle",
                                                "color": "#ffffff", "alpha": 40, "radius": 50}))])

    # ── свойства выбранного блока ── #

    def _clear_props(self):
        def wipe(lay):
            while lay.count():
                it = lay.takeAt(0)
                if it.widget() is not None:
                    it.widget().deleteLater()
                elif it.layout() is not None:
                    wipe(it.layout())
        wipe(self.props_box)

    def _select(self, i):
        import net_frames
        self._clear_props()
        box = self.props_box
        if i is None or i >= len(self.sc["items"]):
            box.addWidget(net_frames._sec("БЛОК"))
            l = QLabel("Кликните по блоку на витрине, чтобы изменить его, или добавьте новый кнопками выше.")
            l.setObjectName("Sub")
            l.setWordWrap(True)
            box.addWidget(l)
            return
        it = self.sc["items"][i]
        names = {"text": "ТЕКСТ", "img": "КАРТИНКА", "track": "ТРЕК", "stat": "СТАТИСТИКА", "ach": "АЧИВКА",
                 "sticker": "НАКЛЕЙКА", "shape": "ФИГУРА", "now": "СЕЙЧАС ИГРАЕТ"}
        box.addWidget(net_frames._sec("ВЫБРАН БЛОК: " + names[it["t"]]))
        if it["t"] == "now":
            l = QLabel("Показывает, что вы слушаете прямо сейчас — у друзей обновляется само. Клик — включить.")
            l.setObjectName("Sub")
            l.setWordWrap(True)
            box.addWidget(l)
            box.addWidget(self._swatch(it.get("color", "#ffdb1a"), lambda c: setv("color", c)))

        def setv(k, v):
            it[k] = v
            self._changed()
        if it["t"] == "text":
            e = QLineEdit(it.get("text", ""))
            e.textChanged.connect(lambda s: setv("text", s))
            box.addWidget(e)
            r = QHBoxLayout()
            sp = QSpinBox()
            sp.setRange(8, 160)
            sp.setValue(int(it.get("size", 40)))
            sp.valueChanged.connect(lambda v: setv("size", v))
            r.addWidget(QLabel("Размер"))
            r.addWidget(sp)
            r.addWidget(self._swatch(it.get("color", "#ffffff"), lambda c: setv("color", c)))
            box.addLayout(r)
            r2 = QHBoxLayout()
            cb = QComboBox()
            for k, n in FONTS.items():
                cb.addItem(n, k)
            cb.setCurrentIndex(list(FONTS).index(it.get("font", "")))
            cb.currentIndexChanged.connect(lambda _i: setv("font", cb.currentData()))
            r2.addWidget(cb)
            al = QComboBox()
            for k, n in (("l", "Слева"), ("c", "По центру"), ("r", "Справа")):
                al.addItem(n, k)
            al.setCurrentIndex(["l", "c", "r"].index(it.get("align", "l")))
            al.currentIndexChanged.connect(lambda _i: setv("align", al.currentData()))
            r2.addWidget(al)
            box.addLayout(r2)
            r3 = QHBoxLayout()
            b1 = QCheckBox("Жирный")
            b1.setChecked(it.get("bold", True))
            b1.toggled.connect(lambda on: setv("bold", on))
            b2 = QCheckBox("Тень")
            b2.setChecked(it.get("shadow", True))
            b2.toggled.connect(lambda on: setv("shadow", on))
            r3.addWidget(b1)
            r3.addWidget(b2)
            box.addLayout(r3)
        elif it["t"] in ("stat", "sticker", "shape"):
            r = QHBoxLayout()
            r.addWidget(QLabel("Цвет"))
            r.addWidget(self._swatch(it.get("color", "#ffdb1a"), lambda c: setv("color", c)))
            r.addStretch(1)
            box.addLayout(r)
            if it["t"] == "shape":
                box.addWidget(QLabel("Плотность цвета"))
                box.addWidget(self._slider(0, 255, int(it.get("alpha", 60)), lambda v: setv("alpha", v)))
                if it.get("k") == "rect":
                    box.addWidget(QLabel("Скругление"))
                    box.addWidget(self._slider(0, 50, int(it.get("radius", 12)), lambda v: setv("radius", v)))
        elif it["t"] == "img":
            cbf = QComboBox()
            for k, n in (("cover", "Заполнить (обрезать)"), ("contain", "Вписать целиком"), ("stretch", "Растянуть")):
                cbf.addItem(n, k)
            cbf.setCurrentIndex(["cover", "contain", "stretch"].index(it.get("fit", "cover")))
            cbf.currentIndexChanged.connect(lambda _i: setv("fit", cbf.currentData()))
            box.addWidget(cbf)
            rr = QHBoxLayout()
            rr.addWidget(_btn("Исходные пропорции", lambda: self._natural(i)))
            rr.addWidget(_btn("На всю витрину", lambda: self._fill(i)))
            box.addLayout(rr)
            hint = QLabel("Уголки — масштаб без искажения (Shift — свободно), Ctrl+колесо или +/− — масштаб, "
                          "ПКМ — ещё действия.")
            hint.setObjectName("Sub")
            hint.setWordWrap(True)
            box.addWidget(hint)
            box.addWidget(QLabel("Скругление углов"))
            box.addWidget(self._slider(0, 50, int(it.get("radius", 8)), lambda v: setv("radius", v)))
        elif it["t"] == "track":
            l = QLabel(f"{it['track'].get('artist', '')} — {it['track'].get('title', '')}\nКлик в профиле — слушать.")
            l.setObjectName("Sub")
            l.setWordWrap(True)
            box.addWidget(l)
        box.addWidget(QLabel("Анимация (видна в профиле)"))
        acb = QComboBox()
        for k, n in ITEM_ANIMS.items():
            acb.addItem(n, k)
        acb.setCurrentIndex(list(ITEM_ANIMS).index(it.get("anim", "")) if it.get("anim", "") in ITEM_ANIMS else 0)
        acb.currentIndexChanged.connect(lambda _i: (it.pop("anim", None), setv("anim", acb.currentData())
                                                    if acb.currentData() else self._changed()))
        box.addWidget(acb)
        box.addWidget(QLabel("Поворот"))
        box.addWidget(self._slider(-180, 180, int(it.get("rot", 0)), lambda v: setv("rot", v)))
        box.addWidget(QLabel("Прозрачность"))
        box.addWidget(self._slider(5, 100, int(100 * it.get("op", 1)), lambda v: setv("op", v / 100)))
        r = QHBoxLayout()
        r.addWidget(_btn("Выше", lambda: self._layer(i, 1)))
        r.addWidget(_btn("Ниже", lambda: self._layer(i, -1)))
        r.addWidget(_btn("Копия", lambda: self._dup(i)))
        r.addWidget(_btn("Удалить", lambda: self._del(i)))
        box.addLayout(r)

    def _layer(self, i, d):
        j = max(0, min(len(self.sc["items"]) - 1, i + d))
        if j != i:
            its = self.sc["items"]
            its[i], its[j] = its[j], its[i]
            self._set_sel(j)
            self._changed()

    def _dup(self, i):
        it = copy.deepcopy(self.sc["items"][i])
        it["x"] += 0.03
        it["y"] += 0.04
        self._add(it)

    def _del(self, i):
        self.sc["items"].pop(i)
        self._set_sel(None)
        self._changed()

    def result(self) -> dict:
        d = {k: v for k, v in self.deco.items() if k != "sc"}
        if self.sc.get("items"):
            d["sc"] = self.sc
        return d
