# net_market.py
"""
Магазин рамок и витрин от пользователей — во вкладке «Моды» рядом с темами.

Рамка или витрина — это «мод» без файла: всё оформление лежит прямо в описании мода
({"type": "frame", "frame": {...}} или {"type": "showcase", "sc": {...}}), поэтому ставится мгновенно,
даже если автор не в сети. Публикация, удаление, жалобы и скрытие — общие с темами (net_core: моды).
Витрина в магазине ужимается (картинки пережимаются), чтобы влезть в событие Nostr.
"""
from __future__ import annotations

import json
import secrets

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                             QPlainTextEdit, QPushButton, QVBoxLayout)

TYPES = ("frame", "showcase")
TYPE_NAMES = {"theme": "Тема ECHOES", "frame": "Рамка профиля", "showcase": "Витрина профиля"}
SC_LIMIT = 45000                     # витрина в магазине (событие Nostr целиком — до ~64 КБ)


def kind_of(mod) -> str:
    t = (mod or {}).get("type")
    return t if t in TYPES else "theme"


def clean_deco_mod(m) -> dict | None:
    from net_core import clip
    out = {"type": m.get("type"), "mid": clip(m.get("mid"), 20), "version": clip(m.get("version"), 20),
           "theme": "", "shots": []}
    if m.get("type") == "frame":
        import net_frames
        fr = net_frames.clean_spec(m.get("frame"))
        if fr.get("id") != "custom":
            return None
        out["frame"] = fr
    else:
        import net_showcase
        sc = net_showcase.clean_sc(m.get("sc"))
        if not sc.get("items") or len(json.dumps(sc, ensure_ascii=False)) > SC_LIMIT * 1.15:
            return None
        out["sc"] = sc
    return out


def pack_showcase(sc: dict) -> dict:
    import net_showcase
    return (net_showcase.for_public({"sc": sc}, limit=SC_LIMIT).get("sc")) or {}


async def publish(nd, typ, payload, title, desc, public=True):
    """Выложить рамку/витрину: пост друзьям и (если public) — в общий магазин Nostr."""
    from net_core import clip
    why = nd.mod_check(title, desc)
    if why:
        nd.emit("toast", f"Не опубликовано: похоже на запрещённое ({why})")
        return
    mid = secrets.token_hex(8)
    mod = {"mid": mid, "type": typ, "version": ""}
    mod["frame" if typ == "frame" else "sc"] = payload
    mod = clean_deco_mod(mod)
    if mod is None:
        nd.emit("toast", "Не опубликовано: рамка или витрина слишком большая")
        return
    title, desc = clip(title, 80), clip(desc, 3000)
    await nd.publish(desc, None, "mod", title=title, mod=mod)
    nos = getattr(nd, "nostr", None)
    if public and nos is not None and nos.up_count:
        content = json.dumps({"title": title, "desc": desc, "mod": mod}, ensure_ascii=False)
        ev = await nd._nostr_sign(30078, content, [["d", f"echoes-mod:{mid}"], ["t", "echoes-mod"],
                                                   nd._link_tag()], 10)
        nd._nostr_mod(ev)
        n = await nos.publish(ev)
        nd.emit("toast", f"«{title}» в магазине для всех · релеев: {n}")
    else:
        nd.emit("toast", f"«{title}» выложено для друзей")


def preview_pm(net, it, w, h) -> QPixmap:
    """Картинка карточки: рамка — на вашей аватарке, витрина — уменьшенная копия."""
    m = it.get("mod") or {}
    pm = QPixmap(w * 2, h * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, w, h), min(w, h) * 0.12, min(w, h) * 0.12)
    p.setClipPath(path)
    if kind_of(m) == "frame":
        g = QLinearGradient(0, 0, w, h)
        g.setColorAt(0, QColor("#24203a"))
        g.setColorAt(1, QColor("#0d0d12"))
        p.fillRect(QRectF(0, 0, w, h), g)
        import net_frames
        s = int(min(w, h) * 0.86)
        av = net._my_avatar_pm(s) if hasattr(net, "_my_avatar_pm") else QPixmap()
        p.drawPixmap(int((w - s) / 2), int((h - s) / 2), net_frames.framed(av, m["frame"], s))
    else:
        import net_showcase
        R = QRectF(0, 0, w, h)
        sw = min(w, h * net_showcase.W0 / net_showcase.H0)
        r = QRectF((w - sw) / 2, (h - sw * net_showcase.H0 / net_showcase.W0) / 2, sw, sw * net_showcase.H0 / net_showcase.W0)
        p.fillRect(R, QColor("#0d0d12"))
        net_showcase.paint_showcase(p, r, m["sc"], net_showcase.Ctx(name=it.get("name", "")), radius=8)
    p.end()
    return pm


def is_worn(net, it) -> bool:
    m = it.get("mod") or {}
    d = net.win.settings.get("ya_profile_deco") or {}
    if kind_of(m) == "frame":
        return (d.get("frame") or {}).get("png") == m["frame"].get("png")
    return json.dumps(d.get("sc") or {}, sort_keys=True) == json.dumps(m.get("sc") or {}, sort_keys=True)


def apply(net, it, ask=True) -> bool:
    """«Надеть рамку» / «Поставить витрину»."""
    win = net.win
    m = it.get("mod") or {}
    d = dict(win.settings.get("ya_profile_deco") or {})
    if kind_of(m) == "frame":
        import net_gifts
        keep_from = f"{it.get('name') or 'магазина'}"
        net_gifts.keep_frame(win, m["frame"], keep_from)
        d["frame"] = dict(m["frame"])
        msg = f"Рамка «{it.get('title', '')}» надета"
    else:
        if ask and (d.get("sc") or {}).get("items") and QMessageBox.question(
                net, "Витрина", "Заменить вашу витрину на «%s»?" % it.get("title", "")) != QMessageBox.StandardButton.Yes:
            return False
        d["sc"] = json.loads(json.dumps(m["sc"]))
        msg = f"Витрина «{it.get('title', '')}» в профиле — её можно переделать"
    win.settings["ya_profile_deco"] = d
    try:
        win.save_settings()
    except Exception:                                          # noqa: BLE001
        pass
    try:
        net._deco_changed()
        net.toast(msg)
    except Exception:                                          # noqa: BLE001
        pass
    return True


class PublishDialog(QDialog):
    """Выложить свою рамку или витрину в магазин."""

    def __init__(self, net, typ="frame"):
        super().__init__(net)
        import net_frames
        self.net = net
        self.typ = typ
        deco = dict(net.win.settings.get("ya_profile_deco") or {})
        fr = deco.get("frame") or {}
        self.frame = fr if fr.get("id") == "custom" else None
        self.sc = deco.get("sc") if (deco.get("sc") or {}).get("items") else None
        self.setWindowTitle("Выложить в магазин")
        self.setStyleSheet(net_frames._QSS + "QLineEdit, QPlainTextEdit{background:#1d1d20;color:#f0f0f0;"
                           "border:1px solid #2c2c30;border-radius:10px;padding:7px 10px;}")
        self.resize(520, 520)
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(8)
        t = QLabel("Выложить в магазин")
        t.setStyleSheet("font-size:22px;font-weight:900;")
        v.addWidget(t)
        row = QHBoxLayout()
        self.tg = QButtonGroup(self)
        for k, n in (("frame", "Рамку"), ("showcase", "Витрину")):
            b = QPushButton(n)
            b.setCheckable(True)
            b.setChecked(k == typ)
            b.clicked.connect(lambda _=False, k=k: self._set(k))
            self.tg.addButton(b)
            row.addWidget(b)
        self.draw_b = QPushButton("Нарисовать другую…")
        self.draw_b.clicked.connect(self._draw)
        row.addWidget(self.draw_b)
        row.addStretch(1)
        v.addLayout(row)
        self.pv = QLabel()
        self.pv.setFixedHeight(190)
        self.pv.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.pv)
        self.title = QLineEdit()
        self.title.setMaxLength(80)
        self.title.setPlaceholderText("Название")
        v.addWidget(self.title)
        self.desc = QPlainTextEdit()
        self.desc.setPlaceholderText("Описание (необязательно)")
        self.desc.setFixedHeight(80)
        v.addWidget(self.desc)
        self.pub = QCheckBox("Для всех в Echoes.NET (иначе — только друзьям)")
        self.pub.setChecked(True)
        v.addWidget(self.pub)
        self.err = QLabel()
        self.err.setStyleSheet("color:#ff6b6b;")
        v.addWidget(self.err)
        v.addStretch(1)
        bot = QHBoxLayout()
        bot.addStretch(1)
        c = QPushButton("Отмена")
        c.clicked.connect(self.reject)
        ok = QPushButton("Выложить")
        ok.setObjectName("Accent")
        ok.clicked.connect(self._ok)
        bot.addWidget(c)
        bot.addWidget(ok)
        v.addLayout(bot)
        self._set(typ)

    def payload(self):
        if self.typ == "frame":
            return self.frame
        return pack_showcase(self.sc) if self.sc else None

    def _set(self, k):
        self.typ = k
        self.draw_b.setVisible(k == "frame")
        pl = self.payload()
        if pl:
            it = {"mod": {"type": k, "frame" if k == "frame" else "sc": pl}, "name": ""}
            self.pv.setPixmap(preview_pm(self.net, it, 300 if k == "showcase" else 180, 180))
            self.err.setText("")
        else:
            self.pv.setPixmap(QPixmap())
            self.err.setText("Своей рамки пока нет — нажмите «Нарисовать другую…»" if k == "frame" else
                             "Витрины пока нет — соберите её в профиле («Оформить профиль → Витрина»)")

    def _draw(self):
        import net_frames
        d = net_frames.FrameDrawer(self.net._my_avatar_pm(160), None, self)
        if d.exec() and getattr(d, "result_png", ""):
            self.frame = {"id": "custom", "png": d.result_png}
            self._set("frame")

    def _ok(self):
        if not self.payload():
            return
        if not self.title.text().strip():
            self.err.setText("Дайте название")
            return
        self.accept()


def publish_dialog(net, typ="frame"):
    if not net.running():
        net.toast("Включите сеть, чтобы выложить")
        return
    d = PublishDialog(net, typ)
    if d.exec():
        net.node.call(publish, net.node, d.typ, d.payload(), d.title.text().strip(),
                      d.desc.toPlainText().strip(), d.pub.isChecked())
