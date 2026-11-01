# ya_queue.py
"""
Страница «Очередь» внутри окна плеера (тема Яндекс Музыки): что играет сейчас,
ваша очередь («Играть следующим» / «В очередь») и что будет дальше из текущего
плейлиста/библиотеки — с учётом режима «вперемешку».
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton

from lyrics import sanitize_title
from yamusic_theme import (Page, TrackListView, cover_pixmap, ya_icon, icon_button, YELLOW)

UPCOMING_MAX = 100


def _section(text, button_text=None, on_click=None):
    w = QWidget()
    w.setObjectName("YaPageInner")
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 22, 0, 8)
    lbl = QLabel(text)
    lbl.setObjectName("YaH2")
    lay.addWidget(lbl)
    lay.addStretch(1)
    if button_text:
        b = QPushButton(button_text)
        b.setObjectName("YaChip")
        b.setFixedHeight(36)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(on_click)
        lay.addWidget(b)
    return w


class QueueRow(QWidget):
    """Строка очереди: обложка, название/исполнитель, справа — убрать (если можно)."""

    def __init__(self, t, current=False, on_click=None, on_remove=None, parent=None):
        super().__init__(parent)
        self.on_click = on_click
        self.setFixedHeight(58)
        if on_click:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("QueueRow:hover{background:rgba(255,255,255,0.06);border-radius:10px;}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(12)
        pic = QLabel()
        pic.setFixedSize(44, 44)
        pic.setPixmap(cover_pixmap(t.get("cover", ""), 44, 6, seed=str(t.get("path", ""))))
        pic.setStyleSheet("background:transparent;")
        col = QVBoxLayout()
        col.setSpacing(0)
        title = QLabel(sanitize_title(t.get("title", "") or "Без названия"))
        title.setStyleSheet(f"font-weight:700;font-size:13px;background:transparent;"
                            f"color:{YELLOW if current else '#ffffff'};")
        sub = QLabel(t.get("artist", "") or "Неизвестный исполнитель")
        sub.setObjectName("YaSub")
        sub.setStyleSheet("background:transparent;")
        col.addWidget(title)
        col.addWidget(sub)
        lay.addWidget(pic)
        lay.addLayout(col, 1)
        if on_remove:
            b = icon_button("close", "Убрать из очереди", 32, 16, "#bdbdbd")
            b.clicked.connect(on_remove)
            lay.addWidget(b)

    def mouseReleaseEvent(self, e):
        if self.on_click and e.button() == Qt.MouseButton.LeftButton:
            self.on_click()


class QueuePage(Page):
    def __init__(self, shell, parent=None):
        super().__init__(parent, margins=(32, 22, 32, 24))
        self.shell = shell
        self.sig = None

    # что менялось с прошлой перерисовки (shell._tick сравнивает и зовёт refresh)
    def signature(self):
        w = self.shell.win
        return (w.current_index, w.queue_context, len(w.user_queue), w._shuffle_pos, w._queued_now,
                bool(w.shuffle), len(w.library))

    def _context_view(self):
        """(контекст, индекс, shuffle-очередь, позиция в ней), от которых считать «дальше»."""
        w = self.shell.win
        if w._queued_now and w._saved_ctx:
            return w._saved_ctx
        return (w.queue_context, w.current_index, list(w._shuffle_queue), w._shuffle_pos)

    def refresh(self):
        sh = self.shell
        w = sh.win
        self.sig = self.signature()
        vbar = self.verticalScrollBar().value()
        self.clear()

        h = QLabel("Очередь")
        h.setObjectName("YaH1")
        self.v.addWidget(h)

        cur = sh.current_track()
        if cur is not None:
            self.v.addWidget(_section("Сейчас играет"))
            self.v.addWidget(QueueRow(cur, current=True))

        if w.user_queue:
            def clear_q():
                w.user_queue.clear()
                self.refresh()
            self.v.addWidget(_section(f"Дальше — ваша очередь · {len(w.user_queue)}", "Очистить", clear_q))
            for i, t in enumerate(list(w.user_queue)):
                def play(i=i):
                    if 0 <= i < len(w.user_queue):
                        w.user_queue.insert(0, w.user_queue.pop(i))
                        w._play_from_queue()

                def remove(i=i):
                    if 0 <= i < len(w.user_queue):
                        w.user_queue.pop(i)
                        self.refresh()
                self.v.addWidget(QueueRow(t, on_click=play, on_remove=remove))

        ctx, cur_i, sq, sp = self._context_view()
        tracks = w.library if ctx == "library" else w.playlists.get(ctx, {}).get("tracks", [])
        if w.shuffle and sq and len(sq) == len(tracks):
            order = [sq[k] for k in range(sp + 1, len(sq))]
        else:
            order = list(range(cur_i + 1, len(tracks))) if cur_i >= 0 else []
        order = [i for i in order if 0 <= i < len(tracks)][:UPCOMING_MAX]
        name = w.ctx_title(ctx) if hasattr(w, "ctx_title") else ("Библиотека" if ctx == "library" else ctx)
        mode = " · вперемешку" if w.shuffle else ""
        if order:
            self.v.addWidget(_section(f"Далее: {name}{mode}"))
            lst = TrackListView(sh)
            lst.set_auto_height(True)
            lst.set_rows([{"t": tracks[i], "ctx": ctx, "i": i} for i in order])
            self.v.addWidget(lst)
        elif cur is None and not w.user_queue:
            lbl = QLabel("Очередь пуста. Включите трек или добавьте его через меню «Играть следующим» / «Добавить в очередь».")
            lbl.setObjectName("YaSub")
            lbl.setWordWrap(True)
            self.v.addSpacing(16)
            self.v.addWidget(lbl)
        elif not w.user_queue:
            lbl = QLabel("Дальше ничего нет — это последний трек.")
            lbl.setObjectName("YaSub")
            self.v.addSpacing(16)
            self.v.addWidget(lbl)
        self.v.addStretch(1)
        self.verticalScrollBar().setValue(vbar)
