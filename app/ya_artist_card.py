"""Карточка исполнителя играющего трека (как в Spotify): фото во всю ширину с подписью «Об исполнителе»,
имя с галочкой, сколько слушателей, «Подписаться» и пара строк о нём. Клик — страница исполнителя.

Данные — artist_net (Deezer / Last.fm / Википедия / SoundCloud): сначала из кэша на диске, иначе в фоне.
Подписки на исполнителей — win.settings["followed_artists"].
"""
from __future__ import annotations

import threading

from PyQt6.QtCore import Qt, QRectF, QPointF, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPixmap, QLinearGradient, QFont, QFontMetrics, QPen
from PyQt6.QtWidgets import QWidget, QAbstractButton

PHOTO_H = 150


class PillButton(QAbstractButton):
    """«Подписаться» как в Spotify: прозрачная пилюля с белой обводкой (рисуется сама, без чужих стилей)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.on = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(30)

    def setText(self, t):
        super().setText(t)
        fm = QFontMetrics(_font(12, True))
        self.setFixedWidth(fm.horizontalAdvance(t) + 30)
        self.update()

    def enterEvent(self, e):
        self.update()

    def leaveEvent(self, e):
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        hov = self.underMouse()
        if self.on:
            p.setBrush(QColor(255, 255, 255, 40 if hov else 26))
            p.setPen(QPen(QColor(255, 255, 255, 90), 1))
        else:
            p.setBrush(QColor(255, 255, 255, 18 if hov else 0))
            p.setPen(QPen(QColor(255, 255, 255, 255 if hov else 160), 1.2))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setPen(QColor("#ffffff"))
        p.setFont(_font(12, True))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.text())
        p.end()


def _accent() -> QColor:
    try:
        import yamusic_theme
        return QColor(yamusic_theme.YELLOW)
    except Exception:                                          # noqa: BLE001
        return QColor("#ffdb1a")


def _font(px, bold=False) -> QFont:
    try:
        from yamusic_theme import ya_font
        return ya_font(px, QFont.Weight.Bold if bold else QFont.Weight.Normal)
    except Exception:                                          # noqa: BLE001
        f = QFont()
        f.setPixelSize(px)
        f.setBold(bold)
        return f


class ArtistCard(QWidget):
    got = pyqtSignal(str, object)                              # имя, данные — из фонового потока

    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self.shell = shell
        self.name = ""
        self.data = None
        self.pic = None
        self._busy = set()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Открыть страницу исполнителя")
        self.setMinimumHeight(PHOTO_H + 168)
        self.follow = PillButton(self)
        self.follow.setText("Подписаться")
        self.follow.clicked.connect(self._toggle_follow)
        self.got.connect(self._on_got)
        import artist_follow
        artist_follow.bus().changed.connect(self._style_follow)   # подписались/отписались в другом месте
        self.hide()

    # ── данные ── #

    def set_artist(self, name: str):
        name = (name or "").strip()
        if not name or name.lower() in ("неизвестный исполнитель", "unknown artist"):
            self.name, self.data, self.pic = "", None, None
            self.hide()
            return
        if name == self.name and self.data is not None:
            return
        self.name = name
        self.data, self.pic = None, None
        try:
            import artist_net
            c = artist_net.cached(name)
        except Exception:                                      # noqa: BLE001
            c = None
        if c and not c.get("error"):
            self._apply(c)
            if (not c.get("ytm_checked") or "ytm_related" not in c or "mb_checked" not in c
                    or c.get('schema') != artist_net.ARTIST_SCHEMA or c.get('bio_language') != artist_net._language()):
                self._fetch(name)                             # дополнить «слушателями за месяц»
        else:
            self._fetch(name)
        self._style_follow()
        self.show()
        self.update()

    def _fetch(self, name):
        if name in self._busy:
            return
        self._busy.add(name)

        def work():
            try:
                import artist_net
                key = str((self.shell.win.settings or {}).get("lastfm_key", "") or "")
                d = artist_net.fetch_artist(name, key, images=False)
            except Exception as e:                             # noqa: BLE001
                d = {"error": str(e)}
            try:
                self.got.emit(name, d)
            except RuntimeError:
                pass
        threading.Thread(target=work, name="echoes-artist-card", daemon=True).start()

    def _on_got(self, name, d):
        self._busy.discard(name)
        if name == self.name and isinstance(d, dict) and not d.get("error"):
            self._apply(d)
            self.update()

    def _apply(self, d):
        self.data = d
        path = d.get("picture") or ""
        if not path:
            try:
                import artist_net
                path = artist_net.cached_image(d.get("picture_url") or d.get("wiki_img") or "")
            except Exception:                                  # noqa: BLE001
                path = ""
        pm = QPixmap(path) if path else QPixmap()
        self.pic = pm if not pm.isNull() else None
        if self.pic is None and (d.get("picture_url") or d.get("wiki_img")):
            def dl(url=d.get("picture_url") or d.get("wiki_img"), name=self.name):
                try:
                    import artist_net
                    p = artist_net.image(url)
                except Exception:                              # noqa: BLE001
                    p = ""
                if p:
                    dd = dict(d, picture=p)
                    try:
                        self.got.emit(name, dd)
                    except RuntimeError:
                        pass
            threading.Thread(target=dl, daemon=True).start()

    # ── подписка ── #

    def _followed(self) -> list:
        import artist_follow
        return artist_follow.followed(self.shell.win)

    def _toggle_follow(self):
        import artist_follow
        artist_follow.set_follow(self.shell.win, self.name, not artist_follow.is_followed(self.shell.win, self.name))
        self._style_follow()

    def _style_follow(self):
        import artist_follow
        on = artist_follow.is_followed(self.shell.win, self.name)
        self.follow.on = on
        self.follow.setText("Вы подписаны" if on else "Подписаться")
        self._place()

    # ── раскладка и рисование ── #

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place()

    def _place(self):
        self.follow.move(12, PHOTO_H + 66)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.name:
            self.shell.open_artist(self.name)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        W, H = self.width(), self.height()
        card = QPainterPath()
        card.addRoundedRect(QRectF(0, 0, W, H), 14, 14)
        p.setClipPath(card)
        p.fillRect(self.rect(), QColor(255, 255, 255, 14))
        photo = QRectF(0, 0, W, PHOTO_H)
        if self.pic is not None:
            src = self.pic
            k = max(W / max(1, src.width()), PHOTO_H / max(1, src.height()))
            sw, sh = W / k, PHOTO_H / k
            p.drawPixmap(photo, src, QRectF((src.width() - sw) / 2, max(0.0, (src.height() - sh) * 0.25), sw, sh))
        else:
            g = QLinearGradient(0, 0, W, PHOTO_H)
            acc = _accent()
            c0 = QColor(acc); c0.setAlpha(90)
            g.setColorAt(0, c0)
            g.setColorAt(1, QColor(20, 20, 22))
            p.fillRect(photo, g)
        top = QLinearGradient(0, 0, 0, 46)                     # подпись поверх фото читается
        top.setColorAt(0, QColor(0, 0, 0, 150))
        top.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(QRectF(0, 0, W, 46), top)
        p.setFont(_font(12, True))
        p.setPen(QColor("#ffffff"))
        p.drawText(QRectF(12, 8, W - 24, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   "Об исполнителе")
        d = self.data or {}
        name = d.get("name") or self.name
        f = _font(15, True)
        p.setFont(f)
        fm = QFontMetrics(f)
        nm = fm.elidedText(name, Qt.TextElideMode.ElideRight, W - 48)
        y = PHOTO_H + 12
        p.drawText(QRectF(12, y, W - 24, 22), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, nm)
        try:
            from artist_net import audience
            fans, what = audience(d)
        except Exception:                                      # noqa: BLE001
            fans, what = int(d.get("fans") or 0), "поклонников"
        if fans >= 10000:                                      # известный исполнитель — галочка
            x = 12 + fm.horizontalAdvance(nm) + 8
            cy = y + 11
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_accent())
            p.drawEllipse(QPointF(x + 7, cy), 7, 7)
            p.setPen(QPen(QColor("#000000"), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(x + 3.8, cy), QPointF(x + 6.2, cy + 2.4))
            p.drawLine(QPointF(x + 6.2, cy + 2.4), QPointF(x + 10.4, cy - 2.6))
        p.setFont(_font(12))
        p.setPen(QColor(255, 255, 255, 190))
        if fans:
            try:
                from artist_net import human
                n = human(fans)
            except Exception:                                  # noqa: BLE001
                n = str(fans)
            txt = f"{n} {what}"
        elif self.name in self._busy:
            txt = "загружаю…"
        else:
            txt = ""
        p.drawText(QRectF(12, PHOTO_H + 36, W - 24, 24), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(_font(12)).elidedText(txt, Qt.TextElideMode.ElideRight, int(W - 24)))
        # ниже кнопки — всё, что влезает: цифры по площадкам, жанры, начало описания
        y = PHOTO_H + 108
        stats = []
        for key, label in (("ytm_monthly", "слушателей за месяц"), ("ytm_subs", "подписчиков YouTube"),
                           ("fans", "поклонников Deezer"), ("listeners", "слушателей Last.fm"),
                           ("playcount", "прослушиваний")):
            v = int(d.get(key) or 0)
            if v and not (v == fans and label.startswith(what.split()[0])):
                stats.append((v, label))
        if stats and H - y > 60:
            try:
                from artist_net import human as _h
            except Exception:                                  # noqa: BLE001
                _h = str
            for v, label in stats[:4]:
                if y + 20 > H - 12:
                    break
                p.setFont(_font(13, True))
                p.setPen(QColor("#ffffff"))
                num = _h(v)
                p.drawText(QRectF(12, y, W - 24, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, num)
                nx = 12 + QFontMetrics(_font(13, True)).horizontalAdvance(num) + 6
                p.setFont(_font(11))
                p.setPen(QColor(255, 255, 255, 150))
                p.drawText(QRectF(nx, y, W - nx - 12, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                           QFontMetrics(_font(11)).elidedText(label, Qt.TextElideMode.ElideRight, int(W - nx - 12)))
                y += 22
            y += 8
        tags = [str(t) for t in (d.get("tags") or [])][:6]
        if tags and H - y > 40:
            p.setFont(_font(11))
            p.setPen(_accent())
            p.drawText(QRectF(12, y, W - 24, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       QFontMetrics(_font(11)).elidedText(" · ".join(tags), Qt.TextElideMode.ElideRight, int(W - 24)))
            y += 26
        bio = " ".join(str(d.get("bio") or "").split())
        if bio and H - y > 30:
            f11 = _font(11)
            fm11 = QFontMetrics(f11)
            lines = max(1, int((H - y - 12) / fm11.lineSpacing()))
            p.setFont(f11)
            p.setPen(QColor(255, 255, 255, 140))
            p.drawText(QRectF(12, y, W - 24, lines * fm11.lineSpacing() + 2),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                       fm11.elidedText(bio, Qt.TextElideMode.ElideRight, int((W - 30) * lines * 0.92)))
        p.end()
