"""Страница исполнителя, блок «Об исполнителе» (как в Spotify): большое круглое фото, слушатели за месяц,
начало описания; клик — окно со всей информацией (подписчики, слушатели, поклонники, прослушивания,
полное описание, теги, ссылки на YouTube Music / Deezer / SoundCloud / Википедию, источники)."""
from __future__ import annotations

import threading
import webbrowser

from PyQt6.QtCore import Qt, QRectF, QObject, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPixmap, QFont, QFontMetrics
from PyQt6.QtWidgets import (QWidget, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
                             QScrollArea)


def _font(px, bold=False):
    try:
        from yamusic_theme import ya_font
        return ya_font(px, QFont.Weight.Bold if bold else QFont.Weight.Normal)
    except Exception:                                          # noqa: BLE001
        f = QFont()
        f.setPixelSize(px)
        f.setBold(bold)
        return f


def _human(n):
    try:
        from artist_net import human
        return human(n)
    except Exception:                                          # noqa: BLE001
        return f"{int(n):,}".replace(",", " ")


def _full(n):
    return f"{int(n):,}".replace(",", " ")


class _Pics(QObject):
    done = pyqtSignal(object, str)


def load_pictures(owner, items):
    """[(Card, url)] → скачать фото в фоне и подставить в карточки."""
    bus = _Pics(owner)

    def apply(card, path):
        try:
            card.cover = path
            card.update()
        except RuntimeError:
            pass
    bus.done.connect(apply)

    def work():
        import artist_net
        for card, url in items:
            try:
                p = artist_net.image(url)
                if p:
                    bus.done.emit(card, p)
            except Exception:                                  # noqa: BLE001
                pass
    threading.Thread(target=work, name="echoes-related-pics", daemon=True).start()


class AboutCard(QWidget):
    """Карточка «Об исполнителе»: тёмная плашка, круглое фото, слушатели (жирным), строки описания."""

    def __init__(self, shell, data, name):
        super().__init__()
        self.shell, self.d, self.name = shell, data or {}, name
        self.setMinimumHeight(440)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Подробнее об исполнителе")
        p = self.d.get("picture") or ""
        self.pic = QPixmap(p) if p else QPixmap()
        if self.pic.isNull():
            self.pic = None

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            AboutDialog(self.shell, self.d, self.name, self.window()).exec()

    def paintEvent(self, _):
        from artist_net import audience
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        W, H = self.width(), self.height()
        card = QPainterPath()
        card.addRoundedRect(QRectF(0, 0, W, H), 12, 12)
        p.fillPath(card, QColor(255, 255, 255, 18 if not self.underMouse() else 26))
        D = 120 if W < 650 else 200
        cx, cy = (20, 24) if W < 650 else (40, 40)
        circ = QPainterPath()
        circ.addEllipse(QRectF(cx, cy, D, D))
        p.save()
        p.setClipPath(circ)
        if self.pic is not None:
            s = min(self.pic.width(), self.pic.height())
            p.drawPixmap(QRectF(cx, cy, D, D), self.pic,
                         QRectF((self.pic.width() - s) / 2, (self.pic.height() - s) / 2, s, s))
        else:
            p.fillRect(QRectF(cx, cy, D, D), QColor(60, 60, 64))
        p.restore()
        n, what = audience(self.d)
        y = cy + D + 30
        p.setPen(QColor("#ffffff"))
        if n:
            p.setFont(_font(15, True))
            p.drawText(QRectF(cx, y - 18, D + 120, 24), Qt.AlignmentFlag.AlignVCenter, f"{_full(n)} {what}")
        # справа от фото — имя, цифры по площадкам, жанры, описание (раньше там было пусто)
        x = cx + D + (20 if W < 650 else 44)
        rw = W - x - 32
        y = cy + 4
        d = self.d
        p.setFont(_font(26, True))
        p.setPen(QColor("#ffffff"))
        fm = QFontMetrics(_font(26, True))
        p.drawText(QRectF(x, y, rw, 36), Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(self.name or d.get("name") or "", Qt.TextElideMode.ElideRight, int(rw)))
        y += 48
        from artist_net import facts
        from i18n import tr
        personal = [(label, value) for label, value in facts(d)
                    if label in ('Дата рождения', 'Дата смерти', 'Дата распада', 'Основана', 'Откуда', 'Место рождения', 'Страна / регион')]
        p.setFont(_font(13))
        p.setPen(QColor(255, 255, 255, 215))
        for label, value in personal:
            text = tr(label) + ': ' + value
            p.drawText(QRectF(x, y, rw, 22), Qt.AlignmentFlag.AlignVCenter,
                       QFontMetrics(_font(13)).elidedText(text, Qt.TextElideMode.ElideRight, max(1, int(rw))))
            y += 24
        stats = [(d.get(k), lab) for k, lab in (("ytm_subs", "подписчиков YouTube"), ("fans", "поклонников Deezer"),
                                                ("listeners", "слушателей Last.fm"), ("playcount", "прослушиваний"),
                                                ("ytm_views", "просмотров YouTube")) if d.get(k)]
        columns = max(1, min(3, int(rw / 150)))
        col_w = rw / columns
        shown_stats = stats[:3] if columns < 3 else stats[:6]
        for i, (v, lab) in enumerate(shown_stats):
            sx = x + (i % columns) * col_w
            sy = y + (i // columns) * 52
            p.setFont(_font(20, True))
            p.setPen(QColor("#ffffff"))
            p.drawText(QRectF(sx, sy, col_w - 10, 26), Qt.AlignmentFlag.AlignVCenter, _human(v))
            p.setFont(_font(12))
            p.setPen(QColor(255, 255, 255, 150))
            p.drawText(QRectF(sx, sy + 26, col_w - 10, 18), Qt.AlignmentFlag.AlignVCenter,
                       QFontMetrics(_font(12)).elidedText(tr(lab), Qt.TextElideMode.ElideRight, max(1, int(col_w - 10))))
        if stats:
            y += 52 * ((len(shown_stats) + columns - 1) // columns) + 10
        tags = [str(t) for t in (d.get("tags") or [])][:8]
        if tags:
            p.setFont(_font(13, True))
            from ya_artist_card import _accent
            p.setPen(_accent())
            p.drawText(QRectF(x, y, rw, 20), Qt.AlignmentFlag.AlignVCenter,
                       QFontMetrics(_font(13, True)).elidedText("  ·  ".join(tags), Qt.TextElideMode.ElideRight, int(rw)))
            y += 32
        bio = " ".join(str(d.get("bio") or "").split())
        f = _font(14)
        fm = QFontMetrics(f)
        lines = int((H - y - 40) / fm.lineSpacing())
        p.setFont(f)
        if bio and lines > 0:
            p.setPen(QColor(255, 255, 255, 215))
            txt = fm.elidedText(bio, Qt.TextElideMode.ElideRight, int(rw * lines * 0.93))
            p.drawText(QRectF(x, y, rw, lines * fm.lineSpacing() + 2),
                       Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignTop, txt)
        p.setFont(_font(12, True))
        p.setPen(QColor(255, 255, 255, 120 if not self.underMouse() else 220))
        p.drawText(QRectF(x, H - 36, rw, 20), Qt.AlignmentFlag.AlignVCenter, "Подробнее  ›")
        p.end()

    def enterEvent(self, e):
        self.update()

    def leaveEvent(self, e):
        self.update()


class AboutDialog(QDialog):
    """Всё об исполнителе: слева — цифры, справа — описание, теги, ссылки, источники."""

    def __init__(self, shell, d, name, parent=None):
        super().__init__(parent)
        self.setWindowTitle(d.get("name") or name)
        self.setMinimumSize(720, 560)
        h = QHBoxLayout(self)
        h.setSpacing(28)
        left = QVBoxLayout()
        left.setSpacing(18)
        stats = []
        if d.get("ytm_subs"):
            stats.append((d["ytm_subs"], "Подписчики YouTube"))
        if d.get("ytm_monthly"):
            stats.append((d["ytm_monthly"], "Слушатели за месяц"))
        if d.get("listeners"):
            stats.append((d["listeners"], "Слушатели Last.fm"))
        if d.get("fans"):
            stats.append((d["fans"], "Поклонники Deezer"))
        if d.get("playcount"):
            stats.append((d["playcount"], "Прослушивания" + (" SoundCloud" if d.get("sc_profile") else "")))
        if d.get("ytm_views"):
            stats.append((d["ytm_views"], "Просмотры YouTube"))
        for n, label in stats[:6]:
            v = QVBoxLayout()
            v.setSpacing(0)
            big = QLabel(_full(n))
            big.setStyleSheet("font-size:28px;font-weight:900;background:transparent;")
            v.addWidget(big)
            sub = QLabel(label)
            sub.setObjectName("YaSub")
            v.addWidget(sub)
            left.addLayout(v)
        if not stats:
            e = QLabel("Цифр пока нет — серверы не ответили")
            e.setObjectName("YaSub")
            left.addWidget(e)
        left.addStretch(1)
        links = [("YouTube Music", f"https://music.youtube.com/channel/{d['ytm_id']}" if d.get("ytm_id") else ""),
                 ("Deezer", f"https://www.deezer.com/artist/{d['deezer_id']}" if d.get("deezer_id") else ""),
                 ("SoundCloud", d.get("sc_profile") or ""), ("Википедия", d.get("wiki_url") or ""),
                 ("MusicBrainz", f"https://musicbrainz.org/artist/{d['mb_id']}" if d.get('mb_id') else '')]
        for title, url in links:
            if not url:
                continue
            b = QPushButton(f"  {title}  ↗")
            b.setObjectName("YaChip")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet("QPushButton{border-radius:14px;padding:5px 12px;text-align:left;}")
            b.clicked.connect(lambda _=False, u=url: webbrowser.open(u))
            left.addWidget(b)
        lw = QWidget()
        lw.setLayout(left)
        lw.setFixedWidth(230)
        h.addWidget(lw)
        right = QVBoxLayout()
        right.setSpacing(12)
        nm = QLabel(d.get("name") or name)
        nm.setStyleSheet("font-size:24px;font-weight:900;background:transparent;")
        right.addWidget(nm)
        if d.get("tags"):
            tg = QLabel("  ·  ".join(d["tags"][:8]))
            tg.setObjectName("YaSub")
            tg.setWordWrap(True)
            right.addWidget(tg)
        bio = str(d.get("bio") or "").strip()
        box = QFrame()
        box.setStyleSheet("QFrame{background:rgba(255,255,255,0.06);border-radius:12px;}"
                          "QLabel{background:transparent;}")
        bl = QVBoxLayout(box)
        bl.setContentsMargins(16, 14, 16, 14)
        bl.setAlignment(Qt.AlignmentFlag.AlignTop)
        from artist_net import facts
        from i18n import tr
        for label, value in facts(d):
            fact = QLabel(tr(label) + ': ' + value)
            fact.setTextFormat(Qt.TextFormat.PlainText)
            fact.setWordWrap(True)
            fact.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            bl.addWidget(fact)
        if d.get('mb_id'):
            source = QLabel(tr('Источник: MusicBrainz'))
            source.setObjectName('YaSub')
            bl.addWidget(source)
        lb = QLabel(bio or "Описания пока нет.")
        lb.setWordWrap(True)
        lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lb.setStyleSheet("font-size:14px;color:#e2e2e2;")
        bl.addWidget(lb)
        if d.get("bio_src"):
            s = QLabel(f"Источник: {d['bio_src']}")
            s.setObjectName("YaSub")
            bl.addWidget(s)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.Shape.NoFrame)
        sc.setStyleSheet("QScrollArea{background:transparent;}")
        sc.setWidget(box)
        right.addWidget(sc, 1)
        srcs = d.get("sources") or []
        if srcs:
            s2 = QLabel("Данные: " + ", ".join(srcs))
            s2.setObjectName("YaSub")
            right.addWidget(s2)
        rw = QWidget()
        rw.setLayout(right)
        h.addWidget(rw, 1)
