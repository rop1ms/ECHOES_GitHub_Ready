"""Настройки без отдельных окон: каждый инструмент — строка в своём разделе Настроек, которая раскрывается на месте.

Section — заголовок (значок, название, пояснение, стрелка); по щелчку под ним появляется сам инструмент: окно, которое
раньше открывалось отдельно (импорт из Spotify, очистка, теги, масштаб…), встраивается прямо сюда (embed). Если
встроенное окно само закрывается («Готово», «Сохранить»), строка просто сворачивается, а при следующем раскрытии
инструмент создаётся заново, с чистого листа.
Row — строка-действие с кнопкой справа (то, что по природе своё окно или режим: конструктор тем, режим обложки).
"""
from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, QPointF, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (QAbstractScrollArea, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                             QSizePolicy, QVBoxLayout, QWidget)

QSS = """
QFrame#InlineSection { background: rgba(255,255,255,0.035); border: 1px solid rgba(255,255,255,0.07);
                       border-radius: 16px; }
QFrame#InlineSection[open="true"] { background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.12); }
QLabel#InlineTitle { font-size: 15px; font-weight: 800; background: transparent; }
QLabel#InlineSub { font-size: 12px; color: #8f8f96; background: transparent; }
QWidget#InlineBody { background: transparent; }
QPushButton#InlineAct { background: rgba(255,255,255,0.08); border: none; border-radius: 10px; padding: 7px 14px;
                        font-weight: 700; }
QPushButton#InlineAct:hover { background: rgba(255,255,255,0.15); }
"""
CLOSE_TEXTS = {"Готово", "Закрыть", "Close", "Done", "OK", "Ок"}


class _Chevron(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(18, 18)
        self.open = False

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor("#bdbdc4"), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                      Qt.PenJoinStyle.RoundJoin))
        c = QPointF(9, 9)
        if self.open:
            p.drawPolyline([QPointF(c.x() - 4.5, c.y() - 2), c + QPointF(0, 2.5), QPointF(c.x() + 4.5, c.y() - 2)])
        else:
            p.drawPolyline([QPointF(c.x() - 2, c.y() - 4.5), c + QPointF(2.5, 0), QPointF(c.x() - 2, c.y() + 4.5)])
        p.end()


class _IconTile(QLabel):
    def __init__(self, icon, accent):
        super().__init__()
        self.setFixedSize(38, 38)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        c = QColor(accent)
        self.setStyleSheet(f"background:rgba({c.red()},{c.green()},{c.blue()},0.14);border-radius:11px;")
        if icon is not None:
            self.setPixmap(icon.pixmap(QSize(20, 20)))


class _Header(QFrame):
    def __init__(self, section):
        super().__init__()
        self.section = section
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("background:transparent;")

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.section.toggle()


class Section(QFrame):
    """Раскрывающаяся строка настроек; factory() → виджет (или QDialog — встраивается) создаётся при раскрытии."""

    def __init__(self, title, sub, icon, factory, accent="#ffdb1a", height=None, on_closed=None):
        super().__init__()
        self.setObjectName("InlineSection")
        self.setStyleSheet(QSS)
        self.setMaximumWidth(760)
        self.factory = factory
        self.fixed_h = height
        self.on_closed = on_closed
        self.content = None
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.header = _Header(self)
        h = QHBoxLayout(self.header)
        h.setContentsMargins(14, 12, 16, 12)
        h.setSpacing(12)
        h.addWidget(_IconTile(icon, accent))
        col = QVBoxLayout()
        col.setSpacing(1)
        t = QLabel(title)
        t.setObjectName("InlineTitle")
        col.addWidget(t)
        if sub:
            s = QLabel(sub)
            s.setObjectName("InlineSub")
            s.setWordWrap(True)
            col.addWidget(s)
        h.addLayout(col, 1)
        self.chev = _Chevron()
        h.addWidget(self.chev)
        v.addWidget(self.header)
        self.body = QWidget()
        self.body.setObjectName("InlineBody")
        self.bl = QVBoxLayout(self.body)
        self.bl.setContentsMargins(14, 0, 14, 14)
        self.body.hide()
        v.addWidget(self.body)

    def is_open(self):
        return self.body.isVisibleTo(self)

    def toggle(self):
        self.set_open(not self.is_open())

    def set_open(self, on):
        if on and self.content is None:
            try:
                w = self.factory()
            except Exception as e:                                 # noqa: BLE001
                w = QLabel(f"Не открылось: {e}")
                w.setWordWrap(True)
            if w is None:
                return
            if isinstance(w, QDialog):
                w = embed(w, self._embedded_closed)
            if self.fixed_h:
                w.setMinimumHeight(self.fixed_h)
            self.content = w
            self.bl.addWidget(w)
        self.body.setVisible(on)
        self.chev.open = on
        self.chev.update()
        self.setProperty("open", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        if on:
            QTimer.singleShot(60, self._reveal)

    def _reveal(self):
        w = self.parentWidget()
        while w is not None and not isinstance(w, QScrollArea):
            w = w.parentWidget()
        if isinstance(w, QScrollArea):
            w.ensureWidgetVisible(self.header, 0, 40)

    def _embedded_closed(self, result):
        """Встроенное окно закрылось само — свернуть строку; в следующий раз — заново."""
        c, self.content = self.content, None
        self.body.hide()
        self.chev.open = False
        self.chev.update()
        self.setProperty("open", "false")
        self.style().unpolish(self)
        self.style().polish(self)
        if c is not None:
            try:
                c.hide()
                c.deleteLater()
            except RuntimeError:                                   # окно уже удалило себя само
                pass
        if self.on_closed is not None:
            try:
                self.on_closed(result)
            except Exception as e:                                 # noqa: BLE001
                print("[settings] after close:", e)


class _CloseWatch(QObject):
    def __init__(self, dlg, cb):
        super().__init__(dlg)
        self.cb = cb
        self.fired = False
        dlg.finished.connect(self._done)

    def _done(self, result):
        if not self.fired:
            self.fired = True
            QTimer.singleShot(0, lambda: self.cb(result))


def embed(dlg: QDialog, on_close=None) -> QWidget:
    """Окно → часть страницы Настроек: без рамки, без своих размеров; вложенная прокрутка раскрывается целиком."""
    dlg.setWindowFlags(Qt.WindowType.Widget)
    dlg.setModal(False)
    dlg.setMinimumSize(0, 0)
    dlg.setMaximumSize(16777215, 16777215)
    dlg.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
    dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    dlg.setProperty("embedded", True)
    dlg.setAutoFillBackground(False)                               # фон — у строки настроек, не своё чёрное окно
    dlg.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
    dlg.setStyleSheet((dlg.styleSheet() or "") + "\nQDialog { background: transparent; }")
    for sc in dlg.findChildren(QScrollArea):
        if sc.parentWidget() is dlg and sc.widgetResizable() and sc.widget() is not None:
            sc.setStyleSheet("QScrollArea{background:transparent;border:none;}")
            sc.viewport().setAutoFillBackground(False)
            sc.widget().setAutoFillBackground(False)
            sc.setFrameShape(QFrame.Shape.NoFrame)
            sc.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            sc.setMinimumHeight(min(1400, sc.widget().sizeHint().height() + 4))
    for b in dlg.findChildren(QPushButton):                        # «Готово/Закрыть» здесь не нужны
        if b.text().strip() in CLOSE_TEXTS:
            b.hide()
    if on_close is not None:
        _CloseWatch(dlg, on_close)
    dlg.show()
    return dlg


class Row(QFrame):
    """Строка-действие: значок, название, пояснение и кнопки справа."""

    def __init__(self, title, sub, icon, actions, accent="#ffdb1a"):
        super().__init__()
        self.setObjectName("InlineSection")
        self.setStyleSheet(QSS)
        self.setMaximumWidth(760)
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 12, 14, 12)
        h.setSpacing(12)
        h.addWidget(_IconTile(icon, accent))
        col = QVBoxLayout()
        col.setSpacing(1)
        t = QLabel(title)
        t.setObjectName("InlineTitle")
        col.addWidget(t)
        if sub:
            s = QLabel(sub)
            s.setObjectName("InlineSub")
            s.setWordWrap(True)
            col.addWidget(s)
        h.addLayout(col, 1)
        self.buttons = []
        for text, fn in actions:
            b = QPushButton(text)
            b.setObjectName("InlineAct")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(fn)
            h.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)
            self.buttons.append(b)


def group_title(text) -> QLabel:
    lab = QLabel(text.upper())
    lab.setObjectName("Section")
    return lab


class LostTracks(QWidget):
    """«Найти пропавшие треки» прямо в Настройках: проверка в фоне, список найденного и «Вернуть» — без окон."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.found = []
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        self.info = QLabel("ECHOES проверит всё, что помнит о треках: плейлисты, историю, кэши, резервные копии и "
                           "папку загрузок. Файлы, которые есть на диске, но пропали из коллекции, можно вернуть.")
        self.info.setWordWrap(True)
        self.info.setObjectName("InlineSub")
        v.addWidget(self.info)
        self.list = QLabel("")
        self.list.setWordWrap(True)
        self.list.setStyleSheet("color:#d8d8d8;font-size:12px;background:rgba(255,255,255,0.04);"
                                "border-radius:10px;padding:8px 10px;")
        self.list.hide()
        v.addWidget(self.list)
        row = QHBoxLayout()
        self.go = QPushButton("Проверить")
        self.go.setObjectName("InlineAct")
        self.go.setCursor(Qt.CursorShape.PointingHandCursor)
        self.go.clicked.connect(self.scan)
        row.addWidget(self.go)
        self.back = QPushButton("")
        self.back.setObjectName("InlineAct")
        self.back.setStyleSheet("QPushButton{background:#ffdb1a;color:#000;}")
        self.back.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back.clicked.connect(self.restore)
        self.back.hide()
        row.addWidget(self.back)
        row.addStretch(1)
        v.addLayout(row)

    def scan(self):
        import threading
        import data_home
        import library_guard
        from pathlib import Path
        self.go.setEnabled(False)
        self.go.setText("Ищу…")
        self.back.hide()
        w = self.win
        lib = list(w.library)
        removed = set(w.settings.get("lib_removed") or [])
        dl = str(w.settings.get("download_dir") or "")

        def work():
            try:
                found = [p for p in library_guard.find_lost(data_home.root(), lib, dl) if p not in removed]
            except Exception as e:                                 # noqa: BLE001
                print("[library] find lost:", e)
                found = []
            w._ui_bridge.call.emit(lambda: self._done(found))
        threading.Thread(target=work, daemon=True, name="find-lost-inline").start()

    def _done(self, found):
        from pathlib import Path
        try:
            self.go.setEnabled(True)
        except RuntimeError:
            return
        self.go.setText("Проверить ещё раз")
        self.found = found
        if not found:
            self.list.setText("Пропавших треков нет: всё, что ECHOES помнит и что есть на диске, уже в коллекции.")
            self.list.show()
            return
        names = ", ".join(Path(p).stem for p in found[:15]) + (" …" if len(found) > 15 else "")
        self.list.setText(f"Нашлось {len(found)}: {names}")
        self.list.show()
        self.back.setText(f"Вернуть в коллекцию ({len(found)})")
        self.back.show()

    def restore(self):
        w = self.win
        for p in self.found:
            w._register_track(p)
        w._save_library()
        w._refresh_library_view()
        w._toast(f"Вернул в коллекцию: {len(self.found)}")
        self.list.setText(f"Возвращено: {len(self.found)}")
        self.found = []
        self.back.hide()
