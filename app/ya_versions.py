# ya_versions.py
"""
Версии трека — сравнить на лету (оригинал / slowed / sped up / remix …).

Окно (правый клик по обложке в плеере → «Версии трека…»):
  * «На лету» — та же запись быстрее/медленнее (темп и тон вместе, как настоящие slowed/sped up): ×0.85, ×1, ×1.25, ×1.3;
  * «В коллекции» — другие файлы этой песни: название без «(slowed + reverb)», «sped up», «remix»… совпадает;
  * «В сети» — поиск версий (YouTube Music / SoundCloud);
  * переключение сохраняет место в песне (пропорционально длине версии), «A/B» — мгновенно назад и обратно.
"""
from __future__ import annotations

import difflib
import re
import threading
import time

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

KINDS = (("ultra slowed", "Ultra slowed"), ("slowed", "Slowed"), ("reverb", "Slowed"), ("sped up", "Sped up"),
         ("speed up", "Sped up"), ("spedup", "Sped up"), ("nightcore", "Nightcore"), ("remix", "Remix"),
         ("rmx", "Remix"), ("bootleg", "Remix"), ("flip", "Remix"), ("instrumental", "Instrumental"),
         ("acoustic", "Acoustic"), ("live", "Live"), ("cover", "Cover"), ("extended", "Extended"),
         ("edit", "Edit"), ("bass boosted", "Bass boosted"), ("phonk", "Remix"), ("8d", "8D"))
_TAG = re.compile(r"[\(\[\{]([^\)\]\}]*)[\)\]\}]")
SPEEDS = ((85, "Slowed ×0.85"), (100, "Обычная"), (125, "Sped up ×1.25"), (130, "Nightcore ×1.3"))


def kind_of(title: str) -> str:
    t = (title or "").lower()
    for key, name in KINDS:
        if re.search(r"(?<![a-zа-я])" + re.escape(key) + r"(?![a-zа-я])", t):
            return name
    return "Оригинал"


def base_title(title: str) -> str:
    """Название без пометок версии: «song (slowed + reverb)» → «song»."""
    t = (title or "").lower()
    t = _TAG.sub(lambda m: "" if kind_of(m.group(1)) != "Оригинал" or "feat" in m.group(1) or "prod" in m.group(1)
                 else m.group(0), t)
    parts = re.split(r"\s[-–—|/]\s", t)
    if len(parts) > 1 and kind_of(parts[-1]) != "Оригинал":
        t = " - ".join(parts[:-1])
    t = re.sub(r"\b(slowed|reverb|sped up|speed up|nightcore|ultra|remix|edit|version|ver\.?)\b", " ", t)
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _artists(a: str) -> set:
    return {x.strip().lower() for x in re.split(r",|&| x | feat\.?| ft\.?|;", a or "") if x.strip()}


def same_song(a: dict, b: dict) -> bool:
    ba, bb = base_title(a.get("title", "")), base_title(b.get("title", ""))
    if not ba or not bb:
        return False
    if ba != bb and difflib.SequenceMatcher(None, ba, bb).ratio() < 0.9:
        return False
    ar, br = _artists(a.get("artist", "")), _artists(b.get("artist", ""))
    if ar & br:
        return True
    title_b = (b.get("title") or "").lower()                   # ремикс другого автора: оригинальный в названии
    return any(x in title_b for x in ar) or kind_of(b.get("title", "")) != "Оригинал"


def local_versions(win, t) -> list:
    out = []
    for x in win.library:
        if str(x.get("path")) != str(t.get("path")) and same_song(t, x):
            out.append(x)
    return out[:20]


class _Bus(QObject):
    found = pyqtSignal(object)


class VersionsDialog(QDialog):
    def __init__(self, shell):
        super().__init__(shell.win)
        self.shell, self.win = shell, shell.win
        self.t = shell.current_track() or {}
        self._prev = None                                      # для A/B: (что играло, доля)
        self.setWindowTitle("Версии трека")
        self.resize(560, 620)
        import net_frames
        self.setStyleSheet(net_frames._QSS)
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(8)
        h = QLabel(f"<b style='font-size:20px'>{self.t.get('title', 'Ничего не играет')}</b><br>"
                   f"<span style='color:#9a9a9a'>{self.t.get('artist', '')} · {kind_of(self.t.get('title', ''))}</span>")
        v.addWidget(h)
        tip = QLabel("Переключение — с того же места песни. «A/B» — мгновенно туда и обратно.")
        tip.setObjectName("Sub")
        v.addWidget(tip)
        v.addWidget(net_frames._sec("НА ЛЕТУ — ЭТА ЖЕ ЗАПИСЬ"))
        sr = QHBoxLayout()
        cur = int(round(float(getattr(self.win.engine, "speed", 1.0) or 1.0) * 100))
        self.speed_btns = []
        for val, name in SPEEDS:
            b = QPushButton(name)
            b.setCheckable(True)
            b.setChecked(val == cur)
            b.clicked.connect(lambda _=False, val=val: self.set_speed(val))
            self.speed_btns.append((val, b))
            sr.addWidget(b)
        v.addLayout(sr)
        v.addWidget(net_frames._sec("В КОЛЛЕКЦИИ"))
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        inner = QWidget()
        inner.setStyleSheet("background:transparent;")
        self.list = QVBoxLayout(inner)
        self.list.setSpacing(4)
        locs = local_versions(self.win, self.t) if self.t else []
        self.list.addWidget(self._row(self.t, True)) if self.t else None
        for x in locs:
            self.list.addWidget(self._row(x))
        if not locs:
            e = QLabel("Других версий в коллекции нет.")
            e.setObjectName("Sub")
            self.list.addWidget(e)
        self.net_sec = net_frames._sec("В СЕТИ")
        self.list.addWidget(self.net_sec)
        self.b_net = QPushButton("Найти версии в сети")
        self.b_net.clicked.connect(self.search_net)
        self.list.addWidget(self.b_net)
        self.list.addStretch(1)
        sc.setWidget(inner)
        v.addWidget(sc, 1)
        bot = QHBoxLayout()
        self.b_ab = QPushButton("A/B — назад")
        self.b_ab.setEnabled(False)
        self.b_ab.clicked.connect(self.ab)
        bot.addWidget(self.b_ab)
        bot.addStretch(1)
        c = QPushButton("Готово")
        c.setObjectName("Accent")
        c.clicked.connect(self.accept)
        bot.addWidget(c)
        v.addLayout(bot)
        self._bus = _Bus()
        self._bus.found.connect(self._net_found)

    # ── строки ── #

    def _row(self, x, current=False):
        from yamusic_theme import cover_pixmap
        f = QFrame()
        f.setObjectName("VRow")
        f.setStyleSheet("QFrame#VRow{background:rgba(255,255,255,%s);border-radius:12px;}"
                        "QFrame#VRow:hover{background:rgba(255,255,255,0.12);} QLabel{background:transparent;}"
                        % ("0.1" if current else "0.04"))
        f.setCursor(Qt.CursorShape.PointingHandCursor)
        h = QHBoxLayout(f)
        h.setContentsMargins(8, 6, 12, 6)
        pic = QLabel()
        pic.setFixedSize(40, 40)
        pic.setPixmap(cover_pixmap(x.get("cover", ""), 40, 6, seed=x.get("title", "")))
        h.addWidget(pic)
        import html as _h                                # названия с YouTube/SoundCloud — текст, не разметка
        t = QLabel(f"<b>{_h.escape(str(x.get('title', '')))}</b><br>"
                   f"<span style='color:#9a9a9a'>{_h.escape(str(x.get('artist', '')))}</span>")
        h.addWidget(t, 1)
        k = QLabel(("▶ играет · " if current else "") + kind_of(x.get("title", "")))
        k.setStyleSheet("color:#ffdb1a;font-weight:800;")
        h.addWidget(k)
        if not current:
            f.mouseReleaseEvent = lambda _e, x=x: self.switch(x)
        return f

    # ── переключение ── #

    def _frac(self) -> float:
        try:
            dur = float(self.win.engine.duration or 0)
            return float(self.win.engine.get_position() or 0) / dur if dur > 0 else 0.0
        except Exception:                                      # noqa: BLE001
            return 0.0

    def _seek_when_ready(self, frac, key, tries=60):
        """После загрузки новой версии — на то же место (доля длины)."""
        eng = self.win.engine
        cur = self.shell.current_track() or {}
        if str(cur.get("path")) == key and float(eng.duration or 0) > 1:
            eng.seek(frac * float(eng.duration))
            return
        if tries > 0:
            QTimer.singleShot(100, lambda: self._seek_when_ready(frac, key, tries - 1))

    def switch(self, x):
        frac = self._frac()
        self._prev = (self.shell.current_track(), frac)
        self.b_ab.setEnabled(True)
        if x.get("stream") or str(x.get("path", "")).startswith("stream:"):
            self.win.stream_tracks([x], playlist="⁣versions")
        else:
            self.shell.play_path(str(x.get("path")))
        QTimer.singleShot(150, lambda: self._seek_when_ready(frac, str(x.get("path"))))

    def ab(self):
        if not self._prev or not self._prev[0]:
            return
        prev, _frac = self._prev
        self.switch(prev)

    def set_speed(self, val):
        for v, b in self.speed_btns:
            b.setChecked(v == val)
        sl = getattr(self.win, "speed", None)
        if sl is not None and hasattr(sl, "setValue"):
            sl.setValue(int(val))
        else:
            self.win.engine.set_speed(val / 100)

    # ── сеть ── #

    def search_net(self):
        self.b_net.setEnabled(False)
        self.b_net.setText("Ищу…")
        t = dict(self.t)
        bus = self._bus

        def work():
            found, seen = [], set()
            try:
                import online_search
                base = base_title(t.get("title", ""))
                art = (t.get("artist") or "").split(",")[0]
                for q in (f"{art} {base}", f"{base} slowed", f"{base} sped up", f"{base} remix"):
                    for x in (online_search.search(q).get("tracks") or [])[:12]:
                        k = (x.get("artist", "").lower(), x.get("title", "").lower())
                        if k in seen or not same_song(t, x):
                            continue
                        seen.add(k)
                        found.append(x)
            except Exception as e:                             # noqa: BLE001
                print("[versions]", e)
            bus.found.emit(found[:16])
        threading.Thread(target=work, daemon=True).start()

    def _net_found(self, found):
        try:
            import stream_core
        except Exception:                                      # noqa: BLE001
            return
        self.b_net.hide()
        if not found:
            e = QLabel("В сети других версий не нашлось.")
            e.setObjectName("Sub")
            self.list.insertWidget(self.list.count() - 1, e)
            return
        for x in found:
            self.list.insertWidget(self.list.count() - 1, self._row(stream_core.make_track(x)))


def open_versions(shell):
    VersionsDialog(shell).exec()
