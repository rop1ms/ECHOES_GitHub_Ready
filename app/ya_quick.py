# ya_quick.py
"""
Быстрое управление ECHOES:
  * мини-плеер — маленькое стеклянное окошко поверх всех окон (обложка, трек, ◀ ⏯ ▶, ♥, полоска, тянется мышью);
  * палитра команд Ctrl+K — «включи волну», «открой профиль кента», «плейлист Дорога», «громче», название трека…;
  * действия для горячих клавиш (player_prefs): громкость, без звука, лайк, «в плейлист…», мини-плеер, палитра.
"""
from __future__ import annotations

import time

from PyQt6.QtCore import QEvent, QPoint, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QCursor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                             QMenu, QPushButton, QVBoxLayout, QWidget)


def _shell(win):
    return getattr(win, "_ya", None)


def _cur(win):
    sh = _shell(win)
    try:
        return sh.current_track() if sh is not None else None
    except Exception:                                          # noqa: BLE001
        return None


def _say(win, text):
    """Короткая подсказка: в окне — тост, при мини-плеере — в нём."""
    mp = getattr(win, "_mini_player", None)
    if mp is not None and mp.isVisible():
        mp.flash(text)
    try:
        win._toast(text)
    except Exception:                                          # noqa: BLE001
        pass


# ══════════════════════════ действия ══════════════════════════ #

def volume(win, d):
    sl = getattr(win, "vol", None)
    if sl is None:
        return
    if d == 0:
        cur = sl.value()
        if cur > 0:
            win._muted_at = cur
            sl.setValue(0)
            _say(win, "Без звука")
        else:
            sl.setValue(int(getattr(win, "_muted_at", 0) or 60))
            _say(win, f"Громкость {sl.value()} %")
        return
    sl.setValue(max(0, min(100, sl.value() + d)))
    _say(win, f"Громкость {sl.value()} %")


def like(win):
    sh, t = _shell(win), _cur(win)
    if sh is None or t is None:
        return
    sh.toggle_like(t)
    _say(win, ("♥ В «Мне нравится»: " if sh.is_liked(t) else "Убрано из «Мне нравится»: ") + str(t.get("title", "")))


def add_to_playlist_menu(win):
    """«В плейлист…» — меню у курсора со своими плейлистами."""
    sh, t = _shell(win), _cur(win)
    if sh is None or t is None:
        _say(win, "Ничего не играет")
        return
    m = QMenu()
    m.setStyleSheet("QMenu{background:#1d1d20;color:#f0f0f0;border:1px solid #2c2c30;border-radius:10px;padding:6px;}"
                    "QMenu::item{padding:6px 18px;border-radius:6px;} QMenu::item:selected{background:#2e2e32;}")
    head = m.addAction(f"«{t.get('title', '')}» — в плейлист:")
    head.setEnabled(False)
    for n in sh.user_playlists()[:30]:
        m.addAction(n, lambda n=n: (sh.add_to_playlist(t, n), _say(win, f"Добавлено в «{n}»")))
    m.exec(QCursor.pos())


def wave(win):
    sh = _shell(win)
    if sh is None:
        return
    from yamusic_theme import WAVE_PL
    if win.current_index >= 0 and getattr(win, "queue_context", None) == WAVE_PL:
        if not sh.is_playing():
            win.toggle_play()
        return
    sh._wave_play_clicked()


def show_main(win):
    if win.isMinimized():
        win.showNormal()
    win.show()
    win.raise_()
    win.activateWindow()


def do(win, key) -> bool:
    """Действия горячих клавиш сверх базовых (player_prefs._do). True — выполнено."""
    fn = {"vol_up": lambda: volume(win, 5), "vol_down": lambda: volume(win, -5), "mute": lambda: volume(win, 0),
          "like": lambda: like(win), "add_pl": lambda: add_to_playlist_menu(win),
          "mini": lambda: toggle_mini(win), "palette": lambda: open_palette(win),
          "voice": lambda: __import__("ya_voice").get(win).push_to_talk(),
          "show": lambda: (show_main(win) if not win.isVisible() or win.isMinimized() or not win.isActiveWindow()
                           else win.showMinimized())}.get(key)
    if fn is None:
        return False
    fn()
    return True


# ══════════════════════════ мини-плеер ══════════════════════════ #

class MiniPlayer(QWidget):
    W, H = 360, 104

    def __init__(self, win):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint)
        self.win = win
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("ECHOES — мини-плеер")
        self.setFixedSize(self.W, self.H)
        self.setMouseTracking(True)
        self._drag = None
        self._hover = False
        self._msg, self._msg_t = "", 0.0
        self._cover_key, self._cover = None, None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(104, 0, 12, 0)
        lay.addStretch(1)
        self.btns = {}
        for k, txt, tip in (("prev", "◀◀", "Предыдущий"), ("play", "▶", "Пауза / играть"), ("next", "▶▶", "Следующий"),
                            ("like", "♡", "Мне нравится"), ("back", "⤢", "Открыть ECHOES"), ("close", "✕", "Закрыть")):
            b = QPushButton(txt)
            b.setToolTip(tip)
            b.setFixedSize(26, 26)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet("QPushButton{background:rgba(255,255,255,0.08);color:#fff;border:none;border-radius:13px;"
                            "font-size:13px;padding:0;} QPushButton:hover{background:rgba(255,255,255,0.22);}")
            if k in ("prev", "next"):
                b.setStyleSheet(b.styleSheet().replace("font-size:13px", "font-size:8px;letter-spacing:-2px"))
            self.btns[k] = b
        self.btns["prev"].clicked.connect(win.prev_track)
        self.btns["play"].clicked.connect(win.toggle_play)
        self.btns["next"].clicked.connect(win.next_track)
        self.btns["like"].clicked.connect(lambda: like(win))
        self.btns["back"].clicked.connect(lambda: (show_main(win), self.close()))
        self.btns["close"].clicked.connect(self.close)
        row = QHBoxLayout()
        row.setSpacing(4)
        for k in ("prev", "play", "next", "like"):
            row.addWidget(self.btns[k])
        row.addStretch(1)
        row.addWidget(self.btns["back"])
        row.addWidget(self.btns["close"])
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 12)
        col.addStretch(1)
        col.addLayout(row)
        lay.insertLayout(0, col, 1)
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.update)
        pos = win.settings.get("mini_player_pos")
        scr = QApplication.primaryScreen().availableGeometry()
        if isinstance(pos, list) and len(pos) == 2:
            self.move(int(pos[0]), int(pos[1]))
        else:
            self.move(scr.right() - self.W - 24, scr.bottom() - self.H - 24)

    def showEvent(self, e):
        self.timer.start()
        super().showEvent(e)

    def closeEvent(self, e):
        self.timer.stop()
        self.win.settings["mini_player_pos"] = [self.x(), self.y()]
        super().closeEvent(e)

    def flash(self, text):
        self._msg, self._msg_t = text, time.monotonic()
        self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.pos()
            self._press_pos = e.position()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None
        try:                                                   # клик по полоске — перемотка
            p = e.position()
            if p.y() > self.H - 10 and 104 <= p.x() <= self.W - 12:
                eng = self.win.engine
                dur = float(eng.duration or 0)
                if dur > 0:
                    eng.seek(dur * (p.x() - 104) / (self.W - 116))
        except Exception:                                      # noqa: BLE001
            pass
        self.win.settings["mini_player_pos"] = [self.x(), self.y()]

    def mouseDoubleClickEvent(self, e):
        show_main(self.win)

    def paintEvent(self, _):
        from yamusic_theme import cover_pixmap
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(1, 1, self.W - 2, self.H - 2)
        path = QPainterPath()
        path.addRoundedRect(r, 20, 20)
        p.fillPath(path, QColor(18, 18, 22, 228 if self._hover else 205))
        p.setPen(QPen(QColor(255, 255, 255, 34), 1))
        p.drawPath(path)
        t = _cur(self.win) or {}
        key = str(t.get("cover") or "") + "|" + str(t.get("title") or "")
        if key != self._cover_key:
            self._cover_key = key
            self._cover = cover_pixmap(str(t.get("cover") or ""), 80, 14, seed=str(t.get("title") or "♪"))
        p.drawPixmap(12, 12, self._cover)
        x0, w = 104, self.W - 116
        f = QFont()
        f.setFamilies(["YS Text", "Inter", "Segoe UI"])
        f.setPixelSize(14)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(255, 255, 255))
        title = t.get("title") or "Ничего не играет"
        if self._msg and time.monotonic() - self._msg_t < 2.2:
            title = self._msg
        p.drawText(QRectF(x0, 12, w, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(f).elidedText(title, Qt.TextElideMode.ElideRight, w))
        f.setPixelSize(12)
        f.setBold(False)
        p.setFont(f)
        p.setPen(QColor(255, 255, 255, 160))
        p.drawText(QRectF(x0, 32, w, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(f).elidedText(str(t.get("artist") or ""), Qt.TextElideMode.ElideRight, w))
        frac = 0.0
        try:
            eng = self.win.engine
            dur = float(eng.duration or 0)
            frac = max(0.0, min(1.0, float(eng.get_position() or 0) / dur)) if dur > 0 else 0.0
            playing = bool(eng.is_playing())
        except Exception:                                      # noqa: BLE001
            playing = False
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 40))
        p.drawRoundedRect(QRectF(x0, self.H - 9, w, 3), 1.5, 1.5)
        p.setBrush(QColor("#ffdb1a"))
        p.drawRoundedRect(QRectF(x0, self.H - 9, w * frac, 3), 1.5, 1.5)
        p.end()
        sh = _shell(self.win)
        self.btns["play"].setText("❚❚" if playing else "▶")
        try:
            self.btns["like"].setText("♥" if t and sh is not None and sh.is_liked(t) else "♡")
        except Exception:                                      # noqa: BLE001
            pass


def toggle_mini(win):
    mp = getattr(win, "_mini_player", None)
    if mp is None:
        mp = win._mini_player = MiniPlayer(win)
    if mp.isVisible():
        mp.close()
    else:
        mp.show()
        mp.raise_()


# ══════════════════════════ палитра команд ══════════════════════════ #

def _commands(win) -> list:
    """[(название, подпись, функция, доп. слова для поиска)]."""
    sh = _shell(win)
    out = []

    def add(title, sub, fn, words=""):
        out.append((title, sub, fn, words))
    add("Включить «Мою волну»", "музыка", lambda: wave(win), "включи волну моя волна радио")
    add("Пауза / играть", "музыка", win.toggle_play, "пауза стоп играть продолжить play pause")
    add("Следующий трек", "музыка", win.next_track, "дальше пропустить скип next")
    add("Предыдущий трек", "музыка", win.prev_track, "назад prev")
    add("Случайный трек из коллекции", "музыка",
        lambda: win._play_index(__import__("random").randrange(len(win.library)), "library") if win.library else None,
        "рандом случайный")
    add("Мне нравится — играющий трек", "музыка", lambda: like(win), "лайк сердечко нравится like")
    add("Добавить играющий в плейлист…", "музыка", lambda: add_to_playlist_menu(win), "в плейлист добавить")
    add("Громче", "звук", lambda: volume(win, 10), "громкость прибавь")
    add("Тише", "звук", lambda: volume(win, -10), "громкость убавь")
    add("Без звука / вернуть", "звук", lambda: volume(win, 0), "мьют mute выключи звук")
    add("Мини-плеер", "окно", lambda: toggle_mini(win), "маленькое окно поверх mini")
    add("Сказать голосовую команду", "голос", lambda: __import__("ya_voice").get(win).push_to_talk(),
        "микрофон голос")
    add("Автоматика…", "настройки", lambda: __import__("ya_auto").RulesDialog(win).exec(), "правила расписание")
    add("Клип песни", "окно", getattr(win, "_toggle_clip_mode", lambda: None), "видео клип")
    add("Текст песни", "окно", sh.toggle_lyrics if sh is not None else (lambda: None), "слова lyrics текст")
    if sh is not None:
        for key, title, words in (("search", "Поиск", "найти"), ("collection", "Коллекция", "библиотека мои треки"),
                                  ("profile", "Мой профиль", "профиль статистика"), ("queue", "Очередь", "далее"),
                                  ("settings", "Настройки", "параметры"), ("net", "Echoes.NET", "друзья сеть лента")):
            add(f"Открыть: {title}", "страница", lambda k=key: (show_main(win), sh.show_page(k)), words)
        net = sh.pages.get("net")
        if net is not None and net.node is not None:
            for tab, title in (("friends", "Друзья"), ("feed", "Лента"), ("chats", "Чаты"), ("mods", "Моды")):
                add(f"Echoes.NET: {title}", "страница",
                    lambda t=tab: (show_main(win), sh.show_page("net"), net.show_tab(t)), "сеть")
            try:
                for c in net.node.store.contacts("friend")[:60]:
                    nm = c.get("name") or c["onion"][:8]
                    add(f"Профиль: {nm}", "друг", lambda o=c["onion"]: (show_main(win), sh.show_page("net"),
                                                                       net.open_profile(o)), "открой профиль друга")
                    add(f"Написать: {nm}", "друг", lambda o=c["onion"]: (show_main(win), sh.show_page("net"),
                                                                        net.open_chat(o)), "чат сообщение")
            except Exception:                                  # noqa: BLE001
                pass
        for n in sh.user_playlists()[:80]:
            add(f"Плейлист: {n}", "открыть", lambda n=n: (show_main(win), sh.open_playlist(n)), "плейлист открой")
            add(f"Включить плейлист: {n}", "играть",
                lambda n=n: (sh.open_playlist(n), sh._pp_play(False)), "включи плейлист")
        seen = set()
        for t in win.library:
            a = str(t.get("artist") or "").strip()
            if a and a.lower() not in seen:
                seen.add(a.lower())
                add(f"Исполнитель: {a}", "страница", lambda a=a: (show_main(win), sh.open_artist(a)), "артист")
            if len(seen) >= 400:
                break
    return out


class Palette(QDialog):
    def __init__(self, win):
        super().__init__(win, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.win = win
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(640, 460)
        self.cmds = _commands(win)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        box = QWidget()
        box.setObjectName("PalBox")
        box.setStyleSheet("QWidget#PalBox{background:rgba(22,22,26,245);border:1px solid rgba(255,255,255,0.12);"
                          "border-radius:18px;} QLineEdit{background:transparent;border:none;color:#fff;"
                          "font-size:18px;padding:14px 16px;} QListWidget{background:transparent;border:none;"
                          "color:#e8e8e8;font-size:14px;outline:none;} QListWidget::item{padding:7px 14px;"
                          "border-radius:10px;} QListWidget::item:selected{background:rgba(255,219,26,0.18);"
                          "color:#fff;} QLabel{color:#8a8a8a;font-size:11px;padding:0 16px 10px 16px;}")
        bv = QVBoxLayout(box)
        bv.setContentsMargins(6, 4, 6, 4)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Команда, плейлист, друг, исполнитель или трек…  (Esc — закрыть)")
        bv.addWidget(self.search)
        self.list = QListWidget()
        bv.addWidget(self.list, 1)
        hint = QLabel("↑↓ — выбрать · Enter — выполнить · Ctrl+K — открыть снова")
        bv.addWidget(hint)
        v.addWidget(box)
        self.search.textChanged.connect(self.fill)
        self.search.returnPressed.connect(self.run)
        self.list.itemActivated.connect(lambda _i: self.run())
        self.search.installEventFilter(self)
        self.fill()
        g = win.geometry()
        self.move(g.center().x() - self.width() // 2, g.y() + max(60, g.height() // 6))
        self.search.setFocus()

    def eventFilter(self, obj, e):
        if obj is self.search and e.type() == QEvent.Type.KeyPress:
            if e.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up) and self.list.count():
                d = 1 if e.key() == Qt.Key.Key_Down else -1
                self.list.setCurrentRow((self.list.currentRow() + d) % self.list.count())
                return True
        return super().eventFilter(obj, e)

    def matches(self, q) -> list:
        import search_util
        q = q.strip()
        res = [(i, c) for i, c in enumerate(self.cmds) if search_util.matches(q, c[0], c[3])] if q else \
            list(enumerate(self.cmds[:14]))
        rows = [(c[0], c[1], ("cmd", i)) for i, c in res[:40]]
        if len(q) >= 2:                                        # треки коллекции по названию
            n = 0
            for i, t in enumerate(self.win.library):
                if search_util.track_matches(q, t):
                    rows.append((f"Включить: {t.get('artist', '')} — {t.get('title', '')}", "трек", ("track", i)))
                    n += 1
                    if n >= 25:
                        break
        return rows

    def fill(self):
        self.list.clear()
        for title, sub, ref in self.matches(self.search.text()):
            it = QListWidgetItem(f"{title}    ·  {sub}")
            it.setData(Qt.ItemDataRole.UserRole, ref)
            self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(0)

    def run(self):
        it = self.list.currentItem()
        if it is None:
            return
        kind, i = it.data(Qt.ItemDataRole.UserRole)
        self.accept()
        if kind == "track":
            QTimer.singleShot(0, lambda: self.win._play_index(i, "library"))
        else:
            QTimer.singleShot(0, self.cmds[i][2])


def open_palette(win):
    show_main(win) if not win.isVisible() else None
    d = Palette(win)
    d.finished.connect(d.deleteLater)
    d.exec()
