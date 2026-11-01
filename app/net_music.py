# net_music.py
"""
Echoes.NET: всё музыкальное в постах и чатах (виджеты и помощники для net_page).

Посты:
  • Момент       — трек с конкретной секунды: «слушай с 1:23» (кнопка включает трек и перематывает);
  • Баттл        — два трека, друзья выбирают; полосы процентов, превью каждого вслепую;
  • Плейлист     — до 50 треков одним постом: «Слушать» и «Сохранить себе»;
  • Угадай трек  — название скрыто, 30 секунд вслепую (отдельный плеер, основной не выдаёт ответ),
                   ответ — комментарием, проверяется сам; угадавшие отмечаются у автора;
  • Цитата       — строчка песни (берётся из синхронного текста на текущей секунде) крупно + трек;
  • Репост       — чужой пост с подписью автора: друзья друзей видят его, подделать нельзя.
У каждого поста с треком — «аура» в цвет трека, реакции-эмодзи, таймлайн комментариев:
комментарий можно привязать к секунде трека, и когда трек играет из поста, комментарии
всплывают в нужный момент. Ответ треком превращает тред в «эстафету» — её можно слушать подряд.

Чаты: момент трека, капсула времени (откроется в выбранный час), приглашение в комнату, строчка песни,
плейлист чата (все треки, которыми обменялись), совместимость вкусов и Blend — общий микс двоих.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import random
import time
from pathlib import Path

from PyQt6.QtCore import Qt, QRectF, QPointF, QSize, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QLinearGradient, QFontMetrics, QIcon
from PyQt6.QtWidgets import QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton

from yamusic_theme import YELLOW, MUTED, ya_font, ya_icon, icon_button, cover_pixmap, sanitize_title

STATS_FILE = Path.home() / ".neon_player" / "stats.json"


# ══════════════════════════════════════════════════════════════════════════ #
#  Помощники
# ══════════════════════════════════════════════════════════════════════════ #

def fmt_sec(s) -> str:
    s = int(max(0, float(s or 0)))
    return f"{s // 60}:{s % 60:02d}"


def safe_track(t):
    """Трек из чужого поста → только безопасные поля (как net_core.clean_track)."""
    try:
        from net_core import clean_track
        return clean_track(t)
    except Exception:                                          # noqa: BLE001
        return None


def norm(s) -> str:
    try:
        from track_identity import norm as _n
        return _n(s or "")
    except Exception:                                          # noqa: BLE001
        return (s or "").casefold().strip()


def tkey(t) -> tuple:
    if not t:
        return ("", "")
    return norm(t.get("artist") or ""), norm(sanitize_title(t.get("title") or ""))


def track_hue(meta) -> float:
    k = "|".join(tkey(meta)) or "?"
    return int(hashlib.md5(k.encode("utf-8")).hexdigest()[:6], 16) / 0xFFFFFF


def aura_qss(meta, obj="NetCard") -> str:
    """Аура поста: мягкий градиент в «цвет» трека (у каждого трека свой)."""
    c = QColor.fromHsvF(track_hue(meta), 0.75, 1.0)
    r, g, b = c.red(), c.green(), c.blue()
    return (f"QFrame#{obj}{{background:qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 rgba({r},{g},{b},0.20),"
            f" stop:0.45 rgba(255,255,255,0.045), stop:1 rgba(255,255,255,0.03));"
            f" border:1px solid rgba({r},{g},{b},0.22); border-radius:16px;}}")


def preview_url(meta) -> str:
    """Что дать yt-dlp для 30-секундного превью: ссылку YouTube/SoundCloud из трека или поиск."""
    t = safe_track(meta) or {}
    if t.get("url"):
        return t["url"]
    return f"ytsearch1:{t.get('artist', '')} {t.get('title', '')}".strip()


def guess_ok(guess: str, meta) -> bool:
    """«Угадай трек»: название (без мусора) совпадает с ответом, опечатки прощаются."""
    g = norm(guess)
    title = norm(sanitize_title((meta or {}).get("title") or ""))
    if not g or not title:
        return False
    if g == title or (len(title) >= 4 and title in g):
        return True
    return difflib.SequenceMatcher(None, g, title).ratio() >= 0.8


def library_match(win, meta):
    key = tkey(meta)
    for i, t in enumerate(win.library):
        tk = tkey(t)
        if tk == key or (not key[0] and tk[1] == key[1]):
            return i, t
    return None, None


def items_for(win, metas) -> list:
    """Треки из сети → элементы очереди: свой файл, если есть, иначе поток."""
    import stream_core
    out = []
    for m in metas:
        m = safe_track(m)
        if not m:
            continue
        _i, t = library_match(win, m)
        out.append(dict(t) if t is not None else stream_core.make_track(m))
    return out


def _genre_key(g) -> str:
    """Жанр из тега → ключ сравнения: первый из «Hip-Hop/Rap; Trap», без регистра и дефисов."""
    g = str(g or "").strip().lower()
    if not g or g in ("unknown", "other", "неизвестно", "genre", "misc"):
        return ""
    g = re.split(r"[/;,|&]", g)[0].strip()
    return re.sub(r"[\s\-_.]+", "", g)[:30]


def _genres(win, st) -> dict:
    """Веса жанров по времени прослушивания (жанр трека; нет тега — жанр других треков исполнителя)."""
    if win is None:
        return {}
    by_path, by_artist = {}, {}
    for t in getattr(win, "library", []) or []:
        g = _genre_key(t.get("genre"))
        if g:
            by_path[str(t.get("path", ""))] = g
            a = norm(t.get("artist") or "")
            if a:
                by_artist.setdefault(a, {})
                by_artist[a][g] = by_artist[a].get(g, 0) + 1
    out = {}
    for path, v in (st.get("tracks") or {}).items():
        if not isinstance(v, dict):
            continue
        g = by_path.get(path)
        if not g:
            cand = by_artist.get(norm(v.get("artist") or "")) or {}
            g = max(cand, key=cand.get) if cand else ""
        if g:
            out[g] = out.get(g, 0.0) + float(v.get("sec") or 0)
    top = max(out.values(), default=0) or 1.0
    best = sorted(out.items(), key=lambda kv: -kv[1])[:24]
    return {g: round((sec / top) ** 0.5, 3) for g, sec in best if sec > 0}


def taste_from_stats(win=None) -> dict | None:
    """Свой «вкус» для друзей: веса жанров и топ-40 исполнителей (по времени), 25 самых заслушанных треков
    и сколько треков в коллекции (совсем маленькой или огромной коллекции совместимость всегда небольшая)."""
    try:
        st = json.loads(STATS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    arts = sorted(((k, float(v.get("sec") or 0)) for k, v in (st.get("artists") or {}).items() if k),
                  key=lambda x: -x[1])[:40]
    if not arts:
        return None
    top = arts[0][1] or 1.0
    artists = {norm(k): round((s / top) ** 0.5, 3) for k, s in arts if norm(k)}
    tr = sorted((v for v in (st.get("tracks") or {}).values() if isinstance(v, dict)),
                key=lambda v: -float(v.get("plays") or 0))[:25]
    tracks = [{"artist": v.get("artist") or "", "title": sanitize_title(v.get("title") or "")} for v in tr]
    out = {"artists": artists, "tracks": tracks, "genres": _genres(win, st)}
    if win is not None:
        out["n"] = len(getattr(win, "library", []) or [])
    return out


def my_top_tracks(win, n=25) -> list:
    """Свои самые заслушанные треки из коллекции (для Blend)."""
    try:
        st = json.loads(STATS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    by_path = {str(t.get("path", "")): t for t in win.library}
    tr = sorted(((p, v) for p, v in (st.get("tracks") or {}).items() if p in by_path),
                key=lambda x: -float(x[1].get("plays") or 0))[:n]
    return [dict(by_path[p]) for p, _v in tr]


def current_lyric(win) -> str:
    """Строчка синхронного текста на текущей секунде (или пусто, если текста с таймингами нет)."""
    try:
        lv = win.lyrics_view
        if not getattr(lv, "_has_timings", False):
            return ""
        pos = float(win.engine.get_position() or 0) * 1000
        best = ""
        for ln in getattr(lv, "_lines", None) or []:
            if 0 <= ln.get("ms", -1) <= pos and (ln.get("text") or "").strip():
                best = ln["text"].strip()
        return best
    except Exception:                                          # noqa: BLE001
        return ""


def ago_in(ts) -> str:
    d = max(0, float(ts) - time.time())
    if d < 60:
        return "меньше минуты"
    if d < 3600:
        return f"{int(d // 60)} мин"
    if d < 86400:
        return f"{int(d // 3600)} ч {int(d % 3600 // 60)} мин"
    return f"{int(d // 86400)} дн"


def capsule_times() -> list[tuple[str, float]]:
    now = time.time()
    lt = time.localtime(now)

    def at(h, days=0):
        base = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, 0, 0, 0, 0, -1)) + days * 86400
        return base if base > now + 60 else base + 86400
    return [("Через 1 час", now + 3600), ("Сегодня в 21:00", at(21)), ("Завтра в 9:00", at(9, 1 if lt.tm_hour >= 9 else 0)),
            ("Через неделю", now + 7 * 86400)]


# ══════════════════════════════════════════════════════════════════════════ #
#  Виджеты
# ══════════════════════════════════════════════════════════════════════════ #

def _small_btn(text, accent=False) -> QPushButton:
    b = QPushButton(text)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if accent:
        b.setObjectName("AccentBtn")
    b.setStyleSheet("QPushButton{padding:6px 12px;border-radius:11px;font-size:12px;}")
    return b


class TrackChip(QFrame):
    """Трек в посте / сообщении / комнате: обложка, название, ▶, «с 1:23» и превью 30 сек вслепую."""

    def __init__(self, meta: dict, on_play, compact=False, cover="", at=None, page=None, hidden=False, owner=None,
                 parent=None):
        super().__init__(parent)
        meta = safe_track(meta) or {"title": "?", "artist": ""}
        self.meta = meta
        self._on_play = on_play
        self.setObjectName("NetTrack")
        self.setStyleSheet("QFrame#NetTrack{background:rgba(0,0,0,0.42);border-radius:12px;}")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 8, 10, 8)
        lay.setSpacing(10)
        sz = 36 if compact else 44
        cov = QLabel()
        cov.setFixedSize(sz, sz)
        path = cover
        if not path and not hidden:
            try:
                import artist_net
                path = artist_net.cached_image(meta.get("thumb_url") or "") or ""
            except Exception:                                  # noqa: BLE001
                pass
        cov.setPixmap(cover_pixmap(path, sz, 8, seed="?" if hidden else f"{meta.get('artist')}{meta.get('title')}"))
        lay.addWidget(cov)
        if not path and not hidden:                            # чужой трек: обложку подгрузить (thumb_url / Deezer)
            import cover_lookup
            cover_lookup.want(meta, lambda p, cov=cov, sz=sz: cov.setPixmap(cover_pixmap(p, sz, 8)))
        col = QVBoxLayout(); col.setSpacing(0)
        t = QLabel("? ? ?" if hidden else (meta.get("title") or "Без названия"))
        t.setStyleSheet("color:#ffffff;font-weight:800;font-size:13px;")
        a = QLabel("исполнитель скрыт" if hidden else (meta.get("artist") or ""))
        a.setStyleSheet("color:rgba(255,255,255,0.72);font-size:12px;")
        col.addWidget(t); col.addWidget(a)
        lay.addLayout(col, 1)
        if page is not None and owner and not hidden and page.node is not None and owner != page.node.me.onion                 and library_match(page.win, meta)[1] is None and page.node.store.contact(owner):
            gb = icon_button("download", "Нет в вашей коллекции — взять файл у друга (через Echoes.NET)", 30, 16,
                             "#e6e6e6")
            gb.clicked.connect(lambda: page.get_track_from(owner, meta))
            lay.addWidget(gb)
        if page is not None:
            pv = icon_button("wave", "Превью 30 секунд (основной плеер встанет на паузу)", 30, 16, "#e6e6e6")
            pv.clicked.connect(lambda: page.preview(meta, pv))
            lay.addWidget(pv)
        if at is not None and not hidden:
            mb = _small_btn(f"▶ с {fmt_sec(at)}", accent=True)
            mb.setToolTip("Включить трек с этой секунды")
            mb.clicked.connect(lambda: on_play(meta, at))
            lay.addWidget(mb)
        if not hidden:
            pb = icon_button("play", "Слушать", 32, 16, "#000000")
            pb.setStyleSheet(f"QPushButton{{background:{YELLOW};border-radius:16px;}}"
                             "QPushButton:hover{background:#ffe55c;}")
            pb.clicked.connect(lambda: on_play(meta))
            lay.addWidget(pb)
        self._hidden = hidden

    def mouseDoubleClickEvent(self, e):
        if not self._hidden:
            self._on_play(self.meta)


class TimelineMarks(QWidget):
    """Таймлайн трека поста: точки — комментарии «на секунде»; клик по точке — включить с неё."""

    def __init__(self, duration, marks, on_seek, parent=None):
        super().__init__(parent)
        self.dur = max(1.0, float(duration or 0) or max([m[0] for m in marks] + [60]) * 1.1)
        self.marks = marks                       # [(sec, имя, текст)]
        self.on_seek = on_seek
        self.setFixedHeight(22)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        tip = "\n".join(f"{fmt_sec(s)} · {n}: {t[:60]}" for s, n, t in marks[:12])
        self.setToolTip("Комментарии на таймлайне трека:\n" + tip)

    def _x(self, s):
        return 6 + (self.width() - 12) * min(1.0, s / self.dur)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        y = self.height() / 2
        p.setPen(QPen(QColor(255, 255, 255, 40), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(6, y), QPointF(self.width() - 6, y))
        p.setPen(Qt.PenStyle.NoPen)
        for s, _n, _t in self.marks:
            p.setBrush(QColor(YELLOW))
            p.drawEllipse(QPointF(self._x(s), y), 4.5, 4.5)
        p.end()

    def mouseReleaseEvent(self, e):
        x = e.position().x()
        best = min(self.marks, key=lambda m: abs(self._x(m[0]) - x), default=None)
        if best and abs(self._x(best[0]) - x) < 14:
            self.on_seek(max(0.0, best[0] - 2))


class ReactBar(QWidget):
    """Реакции под постом: эмодзи Noto (не смайлики Windows) со счётчиками и свои реакции-картинки.
    Своя — подсвечена; клик — поставить / снять. «+» — сетка ваших реакций и «Добавить свою…»."""

    LABELS = ("Круто", "Слушаю", "Трогает", "Убило", "Впечатляет", "Нравится")

    def __init__(self, counts: dict, mine: str, on_react, parent=None, on_manage=None):
        super().__init__(parent)
        from net_core import EMOJI
        import emoji_pack
        import net_reactions as NR
        from echo_glass import glass_controls_qss
        self.on_react, self.on_manage, self.mine = on_react, on_manage, mine
        qss = glass_controls_qss() + "QPushButton{border-radius:13px;padding:2px 9px;font-size:12px;font-weight:800;}"
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)
        self._emoji_btns = {}

        def chip(val, ic, tip):
            n = counts.get(val, 0)
            b = QPushButton(f" {n}" if n else "")
            b.setIcon(ic)
            b.setIconSize(QSize(18, 18))
            b.setFixedHeight(28)
            b.setMinimumWidth(36)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setCheckable(True)
            b.setChecked(val == mine)
            b.setToolTip(tip)
            b.setStyleSheet(qss)
            b.clicked.connect(lambda _=False, v=val: on_react("" if v == mine else v))
            h.addWidget(b)
            return b
        for e, label in zip(EMOJI, self.LABELS):
            self._emoji_btns[e] = chip(e, emoji_pack.icon(e, 18), label)
        for val, n in sorted(counts.items(), key=lambda kv: -kv[1]):       # свои реакции, которые уже стоят
            if NR.is_custom(val) and n:
                pm = NR.pixmap(val, 18)
                if not pm.isNull():
                    chip(val, QIcon(pm), NR.name_of(val))
        plus = QPushButton("+")
        plus.setFixedSize(28, 28)
        plus.setCursor(Qt.CursorShape.PointingHandCursor)
        plus.setToolTip("Свои реакции")
        plus.setStyleSheet(qss + "QPushButton{padding:0;font-size:15px;}")
        plus.clicked.connect(lambda: self._picker(plus))
        h.addWidget(plus)
        h.addStretch(1)
        emoji_pack.bus.ready.connect(self._emoji_ready)

    def _emoji_ready(self, ch):
        import emoji_pack
        b = self._emoji_btns.get(ch)
        if b is not None:
            try:
                b.setIcon(emoji_pack.icon(ch, 18))
            except RuntimeError:
                pass

    def _picker(self, anchor):
        from PyQt6.QtWidgets import QGridLayout, QMenu, QWidgetAction
        import net_reactions as NR
        m = QMenu(self)
        m.setStyleSheet("QMenu{background:#1d1d20;border:1px solid #333;border-radius:12px;padding:6px;}"
                        "QMenu::item{color:#f2f2f2;padding:7px 16px;border-radius:8px;}"
                        "QMenu::item:selected{background:#2e2e33;}")
        items = NR.mine()
        if items:
            box = QWidget()
            g = QGridLayout(box)
            g.setContentsMargins(4, 4, 4, 4)
            g.setSpacing(4)
            for i, it in enumerate(items):
                b = QPushButton()
                b.setIcon(QIcon(NR.pixmap(it["id"], 30)))
                b.setIconSize(QSize(30, 30))
                b.setFixedSize(40, 40)
                b.setToolTip(it["name"])
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.setStyleSheet("QPushButton{background:transparent;border:none;border-radius:10px;}"
                                "QPushButton:hover{background:rgba(255,255,255,0.12);}"
                                + ("QPushButton{background:rgba(255,219,26,0.25);}" if it["id"] == self.mine else ""))
                b.clicked.connect(lambda _=False, v=it["id"]: (m.close(), self.on_react("" if v == self.mine else v)))
                g.addWidget(b, i // 6, i % 6)
            wa = QWidgetAction(m)
            wa.setDefaultWidget(box)
            m.addAction(wa)
            m.addSeparator()
        else:
            a = m.addAction("Своих реакций пока нет")
            a.setEnabled(False)
        if self.on_manage:
            m.addAction("Добавить свою реакцию…", self.on_manage)
        m.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))


class BattleBar(QWidget):
    """Полоса процентов баттла за один трек."""

    def __init__(self, share: float, mine: bool, parent=None):
        super().__init__(parent)
        self.share, self.mine = share, mine
        self.setFixedHeight(8)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 30))
        p.drawRoundedRect(r, 4, 4)
        g = QLinearGradient(0, 0, r.width(), 0)
        g.setColorAt(0, QColor(YELLOW) if self.mine else QColor("#9a9a9a"))
        g.setColorAt(1, QColor("#ff4fa3") if self.mine else QColor("#5a5a5a"))
        p.setBrush(g)
        p.drawRoundedRect(QRectF(0, 0, max(8.0, r.width() * self.share), r.height()), 4, 4)
        p.end()


class QuoteBlock(QLabel):
    """Строчка песни крупно, в кавычках."""

    def __init__(self, text, meta, parent=None):
        super().__init__(parent)
        c = QColor.fromHsvF(track_hue(meta), 0.55, 1.0)
        self.setText(f"«{text}»")
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setStyleSheet(f"font-size:22px;font-weight:900;font-style:italic;color:{c.name()};padding:4px 2px;")
