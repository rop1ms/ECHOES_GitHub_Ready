# dsl_studio.py
"""Студия EchoScript: код с подсветкой и горячей перезагрузкой, блоки и свойства, расстановка мышью,
импорт из тем, экспорт/импорт файлов, журнал и справка."""
from __future__ import annotations

import data_home
import os
import re

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QRegularExpression, QRectF, QSize, QMimeData
from PyQt6.QtGui import (QColor, QFont, QSyntaxHighlighter, QTextCharFormat, QTextCursor, QKeySequence,
                         QShortcut, QTextFormat, QIcon, QPixmap, QImage, QPainter, QPen, QDrag)
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QComboBox, QPushButton,
                             QPlainTextEdit, QTabWidget, QTextBrowser, QLabel, QInputDialog, QMessageBox, QSplitter,
                             QTextEdit, QToolButton, QMenu, QScrollArea, QLineEdit, QColorDialog, QFileDialog,
                             QListWidget, QListWidgetItem, QFrame, QDialog, QCheckBox, QDialogButtonBox,
                             QDoubleSpinBox, QAbstractItemView)

import dsl_docs
import dsl_layout as DL
from dsl_lang import KEYWORDS, SOFT_KEYWORDS

APP_DIR = os.path.dirname(os.path.abspath(__file__))
BUILTIN_DIR = os.path.join(APP_DIR, "dsl_themes")
USER_DIR = os.path.join(str(data_home.root()), "echoscript")

TEMPLATE = """// Новая тема EchoScript. Справка — вкладка «Справка» справа сверху.
theme { name: "%s", bg: "#0a0a12", accent: "#7c5cff", accent2: "#35e0c2", text: "#f2f2f7", muted: "#8b8ba3" }

widget Pulse {
  state r = 0
  on frame(dt) { r = approach(r, audio.bass, 12, dt) }
  on click { toggle() }
  on draw {
    blend("add")
    glow(w / 2, h / 2, min(w, h) * (0.25 + r * 0.25), theme.accent)
    blend("normal")
    fill(theme.text)
    text(track.loaded ? track.title : "ECHOES", w / 2, h / 2, 22, align: "center", weight: 700)
  }
}

scene {
  add Pulse { x: 0.2, y: 0.2, w: 0.6, h: 0.6 }
}
"""


# ── список скриптов ──
def list_scripts() -> list[dict]:
    """[{name, path, builtin, overridden}] — пользовательские перекрывают встроенные с тем же именем."""
    out = {}
    for d, builtin in ((BUILTIN_DIR, True), (USER_DIR, False)):
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for fn in names:
            if fn.lower().endswith(".echo"):
                stem = fn[:-5]
                key = stem.lower()
                prev = out.get(key)
                out[key] = {"name": stem, "path": os.path.join(d, fn), "builtin": builtin,
                            "overridden": bool(prev and prev["builtin"] and not builtin),
                            "builtin_path": prev["path"] if prev and prev["builtin"] else
                            (os.path.join(d, fn) if builtin else None)}
    return sorted(out.values(), key=lambda s: (not s["builtin"] and not s["overridden"], s["name"].lower()))


def find_script(name: str) -> dict | None:
    for s in list_scripts():
        if s["name"].lower() == (name or "").lower():
            return s
    return None


def read_script(name: str) -> str | None:
    s = find_script(name)
    if not s:
        return None
    try:
        with open(s["path"], encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def user_path(name: str) -> str:
    os.makedirs(USER_DIR, exist_ok=True)
    safe = re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "script"
    return os.path.join(USER_DIR, safe + ".echo")


# ── подсветка ──
class EchoHighlighter(QSyntaxHighlighter):
    def __init__(self, doc):
        super().__init__(doc)

        def fmt(color, bold=False, italic=False):
            f = QTextCharFormat()
            f.setForeground(QColor(color))
            if bold:
                f.setFontWeight(QFont.Weight.Bold)
            f.setFontItalic(italic)
            return f
        self.rules = []
        kw = "|".join(sorted((KEYWORDS | SOFT_KEYWORDS) - {"true", "false", "nil", "self"}))
        self.rules.append((QRegularExpression(r"\b(" + kw + r")\b"), fmt("#ff7aa8", True)))
        self.rules.append((QRegularExpression(r"\b(true|false|nil|self|PI|TAU|E)\b"), fmt("#ffb36b")))
        self.rules.append((QRegularExpression(r"\b(audio|track|player|playlist|theme|time|dt|frame|W|H|mouse|"
                                              r"x|y|w|h|hover|pressed)\b"), fmt("#7fd8ff")))
        self.rules.append((QRegularExpression(r"\b[A-Za-z_]\w*(?=\s*\()"), fmt("#c9a6ff")))
        self.rules.append((QRegularExpression(r"(?<=\bwidget\s)\s*\w+|(?<=\badd\s)\s*\w+"), fmt("#ffe08a", True)))
        self.rules.append((QRegularExpression(r"(?<=\bon\s)\s*\w+"), fmt("#8cf0b4", True)))
        self.rules.append((QRegularExpression(r"\b\d+(\.\d+)?([eE][+-]?\d+)?\b"), fmt("#ffb36b")))
        self.rules.append((QRegularExpression(r"#[0-9a-fA-F]{3,8}\b"), fmt("#ffd28a")))
        self.str_fmt = fmt("#a8e57f")
        self.com_fmt = fmt("#6b6889", italic=True)
        self.str_re = QRegularExpression(r'"([^"\\]|\\.)*"?|\'([^\'\\]|\\.)*\'?|`([^`\\]|\\.)*`?')

    def highlightBlock(self, text):
        for rx, f in self.rules:
            it = rx.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(), m.capturedLength(), f)
        it = self.str_re.globalMatch(text)
        strings = []
        while it.hasNext():
            m = it.next()
            strings.append((m.capturedStart(), m.capturedEnd()))
            self.setFormat(m.capturedStart(), m.capturedLength(), self.str_fmt)

        def in_str(i):
            return any(a <= i < b for a, b in strings)
        # комментарии: // и /* */ (многострочные — через состояние блока)
        self.setCurrentBlockState(0)
        start = 0
        if self.previousBlockState() == 1:
            end = text.find("*/")
            if end < 0:
                self.setFormat(0, len(text), self.com_fmt)
                self.setCurrentBlockState(1)
                return
            self.setFormat(0, end + 2, self.com_fmt)
            start = end + 2
        i = start
        while i < len(text):
            if text.startswith("//", i) and not in_str(i):
                self.setFormat(i, len(text) - i, self.com_fmt)
                return
            if text.startswith("/*", i) and not in_str(i):
                end = text.find("*/", i + 2)
                if end < 0:
                    self.setFormat(i, len(text) - i, self.com_fmt)
                    self.setCurrentBlockState(1)
                    return
                self.setFormat(i, end + 2 - i, self.com_fmt)
                i = end + 2
                continue
            i += 1


class CodeEdit(QPlainTextEdit):
    """Редактор: моноширинный шрифт, Tab = 2 пробела, автоотступ после Enter."""

    def __init__(self, parent=None):
        super().__init__(parent)
        f = QFont("Cascadia Mono")
        if not f.exactMatch():
            f = QFont("Consolas")
        f.setPointSizeF(10.5)
        f.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(f)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 2)
        self.error_line = 0

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Tab and not e.modifiers():
            self.insertPlainText("  ")
            return
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not e.modifiers():
            line = self.textCursor().block().text()
            indent = len(line) - len(line.lstrip(" "))
            before = line[:self.textCursor().positionInBlock()].rstrip()
            if before.endswith("{"):
                indent += 2
            super().keyPressEvent(e)
            self.insertPlainText(" " * indent)
            return
        if e.key() == Qt.Key.Key_BraceRight:
            c = self.textCursor()
            line = c.block().text()
            if line.strip() == "" and len(line) >= 2 and c.positionInBlock() == len(line):
                c.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.KeepAnchor, 2)
                c.removeSelectedText()
        super().keyPressEvent(e)

    def mark_error(self, line: int):
        self.error_line = line
        sels = []
        if line > 0:
            blk = self.document().findBlockByNumber(line - 1)
            if blk.isValid():
                s = QTextEdit.ExtraSelection()
                s.format.setBackground(QColor(200, 40, 60, 70))
                s.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
                s.cursor = QTextCursor(blk)
                sels.append(s)
        self.setExtraSelections(sels)


def save_script(name: str, text: str) -> str:
    """Сохранить скрипт: встроенный после правки хранится как пользовательская копия с тем же именем."""
    s = find_script(name)
    path = s["path"] if s is not None and not s["builtin"] else user_path(name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def user_scripts() -> list[dict]:
    """Скрипты пользователя (без изменённых встроенных) — показываются в списке тем."""
    return [s for s in list_scripts() if not s["builtin"] and not s["overridden"]]


STUDIO_QSS = """
    #EchoStudio { background: #12111a; }
    #EchoStudio QLabel { color: #c9c6d8; }
    #EchoStudio QComboBox, #EchoStudio QPushButton, #EchoStudio QToolButton {
        background: #1d1b29; color: #ece9f6; border: 1px solid #2c2940; border-radius: 8px;
        padding: 4px 10px; min-height: 22px; }
    #EchoStudio QPushButton:hover, #EchoStudio QToolButton:hover { background: #2a2740; }
    #EchoStudio QPushButton:checked { background: #ffb347; color: #1a1408; border: none; font-weight: 600; }
    #EchoStudio QToolButton::menu-indicator { image: none; width: 0; }
    #EchoStudio QLineEdit { background: #0d0c14; color: #e9e6f5; border: 1px solid #2c2940; border-radius: 6px;
        padding: 3px 6px; }
    #EchoStudio QPlainTextEdit { background: #0d0c14; color: #e9e6f5; border: 1px solid #23212f;
        border-radius: 8px; selection-background-color: #3b3360; }
    #EchoStudio QTextBrowser { background: #0f0e17; border: 1px solid #23212f; border-radius: 8px; }
    #EchoStudio QScrollArea { background: transparent; border: none; }
    #EchoStudio QTabWidget::pane { border: none; }
    #EchoStudio QTabBar::tab { background: transparent; color: #8d89a6; padding: 6px 14px; border: none; }
    #EchoStudio QTabBar::tab:selected { color: #ffb347; border-bottom: 2px solid #ffb347; }
    #EchoStudio #BlockBtn { text-align: left; padding: 7px 10px; }
    #EchoStudio #Group { color: #8d89a6; font-size: 11px; font-weight: 600; letter-spacing: 1px; padding-top: 6px; }
"""

BLOCK_MIME = "application/x-echo-block"

PROP_LABELS = {
    "color": "Цвет", "color2": "Второй цвет", "radius": "Скругление", "border": "Контур, толщина",
    "border_color": "Цвет контура", "pulse": "Пульс от баса", "spin": "Вращение, °/с", "kind": "Форма",
    "sides": "Стороны / лучи", "label": "Текст", "bind": "Подставлять", "size": "Размер", "align": "Выравнивание",
    "weight": "Жирность", "font": "Шрифт", "src": "Файл", "opacity": "Непрозрачность", "dim": "Затемнение",
    "react": "Реакция на звук", "bars": "Столбиков", "gap": "Промежуток", "mirror": "От центра",
    "thick": "Толщина линии", "glow_on": "Свечение", "rays": "Лучей", "hue": "Оттенок, °", "amount": "Количество",
    "action": "Действие", "times": "Показывать время", "row": "Высота строки", "shadow": "Свечение вокруг",
    "light": "Свет 1", "light2": "Свет 2", "points": "Точек", "wobble": "Колыхание", "speed": "Скорость",
    "d": "Контур (SVG)", "z": "Слой", "group": "Группа", "visible": "Показывать", "clip": "Обрезать по рамке",
    "cache": "Запоминать картинку", "resolution": "Разрешение", "warps": "Нитей основы", "rows": "Рядов утка",
}
OPTION_LABELS = {
    "rect": "прямоугольник", "circle": "круг", "ring": "кольцо", "star": "звезда", "hex": "шестиугольник",
    "triangle": "треугольник", "polygon": "многоугольник", "left": "влево", "center": "по центру",
    "right": "вправо", "play": "пауза / воспроизведение", "prev": "предыдущий", "next": "следующий",
    "shuffle": "перемешать", "repeat": "повтор", "title": "название трека", "artist": "исполнитель",
    "album": "альбом", "time": "время",
}
THEME_LABELS = {"accent": "Акцент", "accent2": "Акцент 2", "accent3": "Акцент 3", "text": "Текст",
                "muted": "Приглушённый", "bg": "Фон", "panel": "Панель"}
UNIT_PROPS = {"opacity", "dim", "react", "pulse", "wobble", "amount"}


def _swatch(col) -> QIcon:
    pm = QPixmap(18, 18)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if col is None:
        p.setPen(QPen(QColor("#8d89a6"), 1.5))
        p.drawEllipse(2, 2, 14, 14)
        p.drawLine(4, 14, 14, 4)
    else:
        p.setPen(QPen(QColor(255, 255, 255, 90), 1))
        p.setBrush(col)
        p.drawEllipse(1, 1, 16, 16)
    p.end()
    return QIcon(pm)


class _BlockList(QListWidget):
    """Список блоков: щелчок — добавить, перетаскивание — поставить на сцену в нужное место."""

    def __init__(self):
        super().__init__()
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def startDrag(self, actions):
        it = self.currentItem()
        key = it.data(Qt.ItemDataRole.UserRole) if it is not None else None
        if not key:
            return
        md = QMimeData()
        md.setData(BLOCK_MIME, str(key).encode("utf-8"))
        d = QDrag(self)
        d.setMimeData(md)
        if not it.icon().isNull():
            d.setPixmap(it.icon().pixmap(72, 44))
        d.exec(Qt.DropAction.CopyAction)


COLOR_RE = re.compile(r'^"#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?"$')


class ScriptStudio(QWidget):
    """Панель студии. canvas — ScriptCanvas.
    script_changed(name) — выбран другой скрипт; scripts_changed — список скриптов изменился;
    detach_requested — вынести в отдельное окно / вернуть; close_requested — закрыть панель."""
    script_changed = pyqtSignal(str)
    scripts_changed = pyqtSignal()
    close_requested = pyqtSignal()
    detach_requested = pyqtSignal()
    edit_mode_changed = pyqtSignal(bool)

    def __init__(self, canvas, current: str = "Loom", win=None, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self.win = win
        self.current = current
        self._loading = False
        self._dirty = False
        self.setObjectName("EchoStudio")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumWidth(400)
        self.setStyleSheet(STUDIO_QSS)
        self.setWindowTitle("Конструктор темы")

        # ── верхняя строка ──
        top = QHBoxLayout()
        top.setSpacing(6)
        self.combo = QComboBox()
        self.combo.setMinimumWidth(140)
        self.combo.currentIndexChanged.connect(self._on_pick)
        self.b_file = QToolButton()
        self.b_file.setText("Файл ▾")
        self.b_file.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.b_file.setMenu(self._file_menu())
        self.b_layout = QPushButton("Двигать")
        self.b_layout.setCheckable(True)
        self.b_layout.setToolTip("Включено — виджеты на сцене выбираются и двигаются мышью (код обновляется сам).\n"
                                 "Выключите, чтобы нажимать кнопки сцены, как в плеере.\n"
                                 "Стрелки — сдвиг, Shift — крупнее и с пропорциями, Alt — без прилипания, Delete — удалить")
        self.b_layout.toggled.connect(self.set_edit_mode)
        self.b_detach = QPushButton("⧉")
        self.b_detach.setToolTip("Отдельное окно / вернуть в плеер")
        self.b_detach.setFixedWidth(34)
        self.b_detach.clicked.connect(self.detach_requested.emit)
        b_close = QPushButton("Готово")
        b_close.setToolTip("Закрыть конструктор (всё уже сохранено)")
        b_close.clicked.connect(self.close_requested.emit)
        b_undo = QPushButton("↶")
        b_undo.setToolTip("Отменить (Ctrl+Z)")
        b_undo.setFixedWidth(34)
        b_undo.clicked.connect(lambda: self.undo(False))
        b_redo = QPushButton("↷")
        b_redo.setToolTip("Повторить (Ctrl+Y)")
        b_redo.setFixedWidth(34)
        b_redo.clicked.connect(lambda: self.undo(True))
        top.addWidget(self.combo, 1)
        for b in (self.b_file, b_undo, b_redo, self.b_layout, self.b_detach, b_close):
            top.addWidget(b)

        # ── код ──
        self.edit = CodeEdit()
        self.hl = EchoHighlighter(self.edit.document())
        self.edit.textChanged.connect(self._on_text)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(400)
        self.log.setPlaceholderText("Журнал: log(...) и print(...) из скрипта, ошибки")
        code_tab = QWidget()
        self.code_tab = code_tab
        cl = QVBoxLayout(code_tab)
        cl.setContentsMargins(0, 6, 0, 0)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.edit)
        split.addWidget(self.log)
        split.setSizes([600, 110])
        cl.addWidget(split, 1)
        hint = QLabel("Изменения применяются сами через полсекунды · Ctrl+S — сохранить · Ctrl+R — перезапустить сцену")
        hint.setStyleSheet("color:#6f6b88; font-size:11px;")
        hint.setWordWrap(True)
        cl.addWidget(self.status)
        cl.addWidget(hint)

        # ── блоки + свойства ──
        self.blocks_tab = self._build_blocks()

        self.docs = QTextBrowser()
        self.docs.setHtml(dsl_docs.html())
        self.tabs = QTabWidget()
        self.tabs.addTab(self.blocks_tab, "Конструктор")
        self.tabs.addTab(code_tab, "Код")
        self.tabs.addTab(self.docs, "Справка")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)
        lay.addLayout(top)
        lay.addWidget(self.tabs, 1)
        self.perf = QLabel("")
        self.perf.setStyleSheet("color:#6f6b88; font-size:11px;")
        self.perf.setToolTip("Скорость сцены: кадров в секунду и время кадра (логика скрипта + рисование)")
        lay.addWidget(self.perf)
        self._perf_timer = QTimer(self)
        self._perf_timer.timeout.connect(self._update_perf)
        self._perf_timer.start(500)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._apply)
        QShortcut(QKeySequence("Ctrl+S"), self, activated=self._save)
        QShortcut(QKeySequence("Ctrl+R"), self, activated=lambda: self._apply(keep_state=False))
        canvas.errors_changed.connect(self._on_errors)
        canvas.log_line.connect(self._on_log)
        canvas.geometry_committed.connect(self._on_geom)
        canvas.undo_requested.connect(self.undo)
        canvas.delete_requested.connect(self._on_delete_inst)
        canvas.selection_changed.connect(lambda *_: self._refresh_inspector())
        canvas.block_dropped.connect(lambda key, x, y: self.insert_block(key, at=(x, y)))
        canvas.context_requested.connect(self._context_menu)
        self.refresh(select=current)

    # ══ меню «Файл» ══
    def _file_menu(self):
        m = QMenu(self)
        new = m.addMenu("Новый")
        new.addAction("Пустой холст", lambda: self._new("blank"))
        new.addAction("Готовый набор (обложка, кнопки, спектр, список)", lambda: self._new("starter"))
        self._from_theme_menu = new.addMenu("Из темы плеера")
        self._from_theme_menu.aboutToShow.connect(lambda: self._fill_theme_menu(self._from_theme_menu, "new"))
        m.addAction("Копия…", self._dup)
        m.addAction("Переименовать…", self._rename)
        m.addSeparator()
        self._colors_menu = m.addMenu("Взять цвета из темы")
        self._colors_menu.aboutToShow.connect(lambda: self._fill_theme_menu(self._colors_menu, "colors"))
        self._imp_w_menu = m.addMenu("Импорт виджетов из скрипта")
        self._imp_w_menu.aboutToShow.connect(self._fill_import_scripts)
        self._imp_l_menu = m.addMenu("Импорт слоёв и фона из темы конструктора")
        self._imp_l_menu.aboutToShow.connect(self._fill_import_layers)
        m.addSeparator()
        m.addAction("Экспорт в файл .echo…", self._export)
        m.addAction("Импорт файла .echo…", self._import)
        m.addAction("Открыть папку скриптов", lambda: (os.makedirs(USER_DIR, exist_ok=True), os.startfile(USER_DIR)))
        m.addSeparator()
        self.act_gpu = m.addAction("Рисовать видеокартой (GPU)")
        self.act_gpu.setCheckable(True)
        self.act_gpu.setChecked(self.canvas.gpu)
        self.act_gpu.setToolTip("Сглаживание и свечение считает видеокарта — в 2–3 раза быстрее")
        self.act_gpu.triggered.connect(self._toggle_gpu)
        self.act_window = m.addAction("Студия в отдельном окне")
        self.act_window.setCheckable(True)
        self.act_window.setChecked(self.isWindow())
        self.act_window.triggered.connect(lambda _=False: self.detach_requested.emit())
        m.aboutToShow.connect(lambda: self.act_window.setChecked(self.isWindow()))
        m.addSeparator()
        self._act_del = m.addAction("Удалить", self._delete)
        return m

    # ══ импорт ══
    def _fill_import_scripts(self):
        menu = self._imp_w_menu
        menu.clear()
        for sc in list_scripts():
            if sc["name"] == self.current:
                continue
            menu.addAction(sc["name"], lambda n=sc["name"]: self._import_from_script(n))
        if menu.isEmpty():
            a = menu.addAction("других скриптов нет")
            a.setEnabled(False)

    def _import_from_script(self, name):
        source = read_script(name) or ""
        names = DL.widget_names(source)
        if not names:
            QMessageBox.information(self, "Импорт", f"В скрипте «{name}» нет виджетов.")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Виджеты из «{name}»")
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel("Какие виджеты перенести (вместе с нужными им функциями и цветами темы):"))
        boxes = []
        for n in names:
            cb = QCheckBox(n)
            cb.setChecked(True)
            v.addWidget(cb)
            boxes.append(cb)
        place = QCheckBox("С их расстановкой на сцене (иначе — только код, добавляйте сами)")
        place.setChecked(True)
        v.addWidget(place)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        chosen = [b.text() for b in boxes if b.isChecked()]
        if not chosen:
            return
        new, added = DL.import_widgets(self.edit.toPlainText(), source, chosen, place.isChecked())
        self.apply_source(new, keep_state=True)
        self.status.setText(f"<span style='color:#8cf0b4'>Импортировано: {', '.join(added)}</span>")

    def _fill_import_layers(self):
        menu = self._imp_l_menu
        menu.clear()
        try:
            from themes import THEMES
        except Exception:                                  # noqa: BLE001
            return
        names = [n for n, t in THEMES.items() if t.get("custom")]
        for n in names:
            menu.addAction(n, lambda nm=n: self._import_layers(nm))
        if not names:
            a = menu.addAction("тем из конструктора нет")
            a.setEnabled(False)

    def _import_layers(self, theme_name):
        from themes import THEMES
        td = THEMES.get(theme_name) or {}
        store = getattr(self.win, "theme_store", None)
        W, H = max(1, self.canvas.width()), max(1, self.canvas.height())
        new, n = DL.import_theme_layers(self.edit.toPlainText(), td, store, W, H)
        self.apply_source(new, keep_state=True)
        self.status.setText(f"<span style='color:#8cf0b4'>Из «{theme_name}»: цвета, фон и слоёв — {n}</span>")

    def _fill_theme_menu(self, menu, mode):
        menu.clear()
        try:
            from themes import THEMES, theme_groups
        except Exception:                                  # noqa: BLE001
            return
        for title, _hint, names in theme_groups():
            names = [n for n in names if not THEMES.get(n, {}).get("dsl_user")]
            if not names:
                continue
            menu.addSection(title)
            for n in names:
                menu.addAction(n, lambda nm=n, md=mode: self._use_theme(nm, md))

    def _use_theme(self, theme_name, mode):
        from themes import THEMES
        td = THEMES.get(theme_name) or {}
        store = getattr(self.win, "theme_store", None)
        if mode == "colors":
            pal = DL.palette_of(td)
            if pal:
                self.apply_source(DL.theme_set(self.edit.toPlainText(), pal))
                self.status.setText(f"<span style='color:#8cf0b4'>Цвета взяты из темы «{theme_name}»</span>")
            return
        name = self._ask_name("Новый скрипт из темы", theme_name.lstrip("✦◈ ").strip() + " (скрипт)")
        if name:
            self._create(name, DL.script_from_theme(name, td, store))

    # ══ список ══
    def refresh(self, select: str | None = None, reload_editor=True):
        self._loading = True
        self.combo.clear()
        self.scripts = list_scripts()
        for s in self.scripts:
            label = s["name"] + ("  · встроенный" if s["builtin"] else "  · изменён" if s["overridden"] else "")
            self.combo.addItem(label, s["name"])
        idx = max(0, self.combo.findData(select or self.current))
        self.combo.setCurrentIndex(idx)
        self._loading = False
        if reload_editor:
            self._load_editor(self.combo.currentData())
        else:
            self._update_del()

    def _info(self, name):
        return next((s for s in self.scripts if s["name"] == name), None)

    def _update_del(self):
        info = self._info(self.current)
        self._act_del.setText("Сбросить к встроенной версии" if info and info["overridden"] else "Удалить")
        self._act_del.setEnabled(bool(info and not info["builtin"]))

    def _load_editor(self, name):
        if not name:
            return
        self.current = name
        src = read_script(name) or ""
        self._loading = True
        self.edit.setPlainText(src)
        self._loading = False
        self._dirty = False
        self._update_del()
        self.edit.mark_error(0)
        self._refresh_inspector()

    def _on_pick(self, _i):
        if self._loading:
            return
        if self._dirty:
            self._save(quiet=True)
        name = self.combo.currentData()
        self._load_editor(name)
        self.script_changed.emit(name)

    # ══ правка и применение ══
    def _on_text(self):
        if self._loading:
            return
        self._dirty = True
        self._timer.start()

    def _apply(self, keep_state=True):
        self._timer.stop()
        err = self.canvas.load_source(self.edit.toPlainText(), keep_state=keep_state)
        if err is None:
            self._save(quiet=True)
        self._on_errors([err] if err else list(self.canvas.scene.errors if self.canvas.scene else []))
        self._refresh_inspector()

    def apply_source(self, src: str, keep_state=True):
        """Новый текст скрипта из визуальных инструментов: в редактор (курсор и прокрутка на месте),
        сразу применить и сохранить."""
        if src is None or src == self.edit.toPlainText():
            return
        cur = self.edit.textCursor().position()
        vs = self.edit.verticalScrollBar().value()
        self._loading = True
        c = self.edit.textCursor()
        c.beginEditBlock()                                  # одним шагом — Ctrl+Z отменяет целиком
        c.select(QTextCursor.SelectionType.Document)
        c.insertText(src)
        c.endEditBlock()
        self._loading = False
        c = self.edit.textCursor()
        c.setPosition(min(cur, len(src)))
        self.edit.setTextCursor(c)
        self.edit.verticalScrollBar().setValue(vs)
        self._dirty = True
        self._apply(keep_state=keep_state)

    def _save(self, quiet=False):
        if not self._dirty and quiet:
            return
        name = self.current
        info = self._info(name)
        try:
            save_script(name, self.edit.toPlainText())
        except OSError as e:
            self.status.setText(f"<span style='color:#ff6b7f'>Не удалось сохранить: {e}</span>")
            return
        self._dirty = False
        if info is not None and info["builtin"]:
            self.refresh(select=name, reload_editor=False)
        if not quiet:
            self.status.setText("<span style='color:#8cf0b4'>Сохранено</span>")

    def _on_errors(self, errs):
        errs = [e for e in errs if e]
        if not errs:
            self.status.setText("<span style='color:#8cf0b4'>работает</span>")
            self.edit.mark_error(0)
            return
        msg = errs[-1]
        self.status.setText(f"<span style='color:#ff6b7f'>Ошибка: {msg}</span>")
        m = re.search(r"строка (\d+)", msg)
        self.edit.mark_error(int(m.group(1)) if m else 0)

    def _update_perf(self):
        if not self.isVisible():
            return
        st = self.canvas.stats()
        mode = "видеокарта" if st["gpu"] else "процессор"
        col = "#8cf0b4" if st["fps"] >= 55 else ("#ffd28a" if st["fps"] >= 40 else "#ff6b7f")
        self.perf.setText(f"<span style='color:{col}'>{st['fps']:.0f} к/с</span> · кадр {st['frame_ms']:.1f} мс "
                          f"(рисование {st['paint_ms']:.1f}) · {mode}")

    def _toggle_gpu(self, on):
        real = self.canvas.set_gpu(on)
        self.act_gpu.setChecked(real)
        if self.win is not None:
            self.win.settings["dsl_gpu"] = bool(on)
        if on and not real:
            QMessageBox.information(self, "EchoScript", "Видеокарта недоступна для рисования (нет OpenGL 2.0 "
                                                        "или только программный драйвер) — сцена рисуется процессором.")

    def _on_log(self, line):
        self.log.appendPlainText(str(line))

    # ══ расстановка ══
    def set_edit_mode(self, on):
        if self.b_layout.isChecked() != bool(on):
            self.b_layout.setChecked(bool(on))              # повторно придёт сюда через toggled
            return
        self.canvas.set_edit_mode(bool(on))
        if on:
            self.tabs.setCurrentWidget(self.blocks_tab)
        self.edit_mode_changed.emit(bool(on))
        self._refresh_inspector()

    def _on_geom(self, items):
        """[(строка, класс, geom)] — правки идут снизу вверх, чтобы номера строк выше не сдвигались."""
        src = self.edit.toPlainText()
        for line, cls, geom in sorted(items, key=lambda t: -t[0]):
            new = DL.set_geom(src, line, cls, geom)
            if new is None:
                self.status.setText(f"<span style='color:#ff6b7f'>Не нашёл «add {cls}» в строке {line}</span>")
                continue
            src = new
        self.apply_source(src)

    def _on_delete_inst(self, items):
        src = self.edit.toPlainText()
        for line, cls in sorted(items, key=lambda t: -t[0]):
            new = DL.remove_add(src, line, cls)
            if new is not None:
                src = new
        self.canvas.select(None)
        self.apply_source(src)

    def undo(self, redo=False):
        self._timer.stop()
        self._loading = True
        (self.edit.redo if redo else self.edit.undo)()
        self._loading = False
        self._dirty = True
        self._apply()

    def _goto_line(self, line):
        blk = self.edit.document().findBlockByNumber(max(0, line - 1))
        if blk.isValid():
            c = QTextCursor(blk)
            self.edit.setTextCursor(c)
            self.edit.centerCursor()
        self.tabs.setCurrentWidget(self.code_tab)
        self.edit.setFocus()

    # ══ вкладка «Конструктор» ══
    def _build_blocks(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 6, 0, 0)
        v.setSpacing(6)

        # подсказка для первого раза
        self.tour = QFrame()
        self.tour.setObjectName("Tour")
        self.tour.setStyleSheet("#Tour { background: #1d1a2c; border: 1px solid #3a3358; border-radius: 10px; }"
                                "#Tour QLabel { color: #ece9f6; }")
        tl = QVBoxLayout(self.tour)
        tl.setContentsMargins(12, 10, 12, 10)
        tl.setSpacing(4)
        for n, text in (("1", "<b>Перетащите блок</b> из списка ниже на сцену (или просто щёлкните по нему)."),
                        ("2", "<b>Двигайте</b> виджет мышью, тяните за углы, чтобы изменить размер."),
                        ("3", "<b>Настройте</b> его ниже: цвета, текст, файлы, что делать по клику.")):
            lab = QLabel(f"<span style='color:#ffb347; font-weight:800'>{n}</span>&nbsp;&nbsp;{text}")
            lab.setWordWrap(True)
            tl.addWidget(lab)
        ok = QPushButton("Понятно")
        ok.setFixedWidth(110)
        ok.clicked.connect(self._tour_done)
        tl.addWidget(ok, 0, Qt.AlignmentFlag.AlignRight)
        v.addWidget(self.tour)
        if self.win is not None and self.win.settings.get("dsl_tour_done"):
            self.tour.hide()

        split = QSplitter(Qt.Orientation.Vertical)
        self.palette = _BlockList()
        self.palette.setObjectName("Palette")
        self.palette.setStyleSheet(
            "#Palette { background: #0d0c14; border: 1px solid #23212f; border-radius: 8px; color: #ece9f6;"
            "  padding: 4px; outline: none; }"
            "#Palette::item { padding: 4px 6px; border-radius: 6px; }"
            "#Palette::item:hover { background: #2a2740; }"
            "#Palette::item:selected { background: #3b3360; color: #ffffff; }")
        self.palette.setIconSize(QSize(72, 44))
        groups = {}
        for blk in DL.BLOCKS.values():
            groups.setdefault(blk["group"], []).append(blk)
        bold = QFont(self.palette.font())
        bold.setBold(True)
        self._pal_items = {}
        for g, items in groups.items():
            head = QListWidgetItem(g.upper())
            head.setFlags(Qt.ItemFlag.NoItemFlags)
            head.setFont(bold)
            head.setForeground(QColor("#8d89a6"))
            self.palette.addItem(head)
            for blk in items:
                it = QListWidgetItem(blk["title"])
                it.setToolTip(blk["hint"] + "\nПеретащите на сцену или щёлкните, чтобы добавить")
                it.setData(Qt.ItemDataRole.UserRole, blk["key"])
                it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled)
                self.palette.addItem(it)
                self._pal_items[blk["key"]] = it
        self.palette.itemClicked.connect(
            lambda it: it.data(Qt.ItemDataRole.UserRole) and self.insert_block(it.data(Qt.ItemDataRole.UserRole)))
        split.addWidget(self.palette)
        QTimer.singleShot(0, self._make_previews)

        self.insp = QWidget()
        self.insp.setObjectName("Insp")
        self.insp.setStyleSheet("#Insp { background: #12111a; }")
        self.insp_l = QVBoxLayout(self.insp)
        self.insp_l.setContentsMargins(2, 2, 6, 2)
        self.insp_l.setSpacing(5)
        sa2 = QScrollArea()
        sa2.setWidgetResizable(True)
        sa2.setFrameShape(QFrame.Shape.NoFrame)
        sa2.viewport().setStyleSheet("background: #12111a;")
        sa2.setWidget(self.insp)
        split.addWidget(sa2)
        split.setSizes([260, 460])
        v.addWidget(split, 1)
        self.insp_note = QLabel("")
        return w

    def _tour_done(self):
        self.tour.hide()
        if self.win is not None:
            self.win.settings["dsl_tour_done"] = True

    def _make_previews(self):
        """Живые превью блоков: каждый блок рисуется по-настоящему (с текущим треком и цветами темы)."""
        try:
            from dsl_runtime import Scene
            theme = DL.theme_get(self.edit.toPlainText())
            for key, it in self._pal_items.items():
                src = DL.BLANK % "превью"
                src = DL.theme_set(src, {k: v for k, v in theme.items() if isinstance(v, str) and k != "name"})
                src = DL.add_block(src, key, {"x": 0, "y": 0, "w": 1, "h": 1})
                sc = Scene(src, self.canvas.bridge, self.canvas.analyzer, log_fn=lambda m: None)
                W, H = 144, 88
                sc.resize(W, H)
                sc.start()
                for _ in range(3):
                    sc.frame(1 / 60)
                img = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
                img.fill(QColor(theme.get("bg") or "#0a0a12") if isinstance(theme.get("bg"), str) else QColor("#0a0a12"))
                p = QPainter(img)
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                sc.paint(p)
                p.setPen(QPen(QColor(255, 255, 255, 40), 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(QRectF(1, 1, W - 2, H - 2), 8, 8)
                p.end()
                it.setIcon(QIcon(QPixmap.fromImage(img)))
        except Exception as e:                             # noqa: BLE001
            print("[studio] превью блоков:", e)

    def insert_block(self, key, at=None):
        """Добавить блок; at=(x, y) в пикселях сцены — поставить его центром туда (перетаскивание)."""
        b = DL.BLOCKS[key]
        gx, gy, gw, gh = b["geom"]
        W, H = max(1, self.canvas.width()), max(1, self.canvas.height())
        if at is not None:
            if gw >= 1 and gh >= 1:
                gx, gy = 0, 0                              # фон — всегда на весь экран
            else:
                gx = min(max(at[0] / W - gw / 2, 0.0), 1 - gw)
                gy = min(max(at[1] / H - gh / 2, 0.0), 1 - gh)
        else:
            n = sum(1 for i in (self.canvas.scene.instances if self.canvas.scene else []) if i.cls.name == b["cls"])
            if gw < 1 or gh < 1:                            # новые копии — чуть со сдвигом, чтобы не слипались
                gx = min(0.9, gx + 0.03 * n)
                gy = min(0.9, gy + 0.03 * n)
        src = DL.add_block(self.edit.toPlainText(), key, {"x": gx, "y": gy, "w": gw, "h": gh})
        self.apply_source(src, keep_state=True)
        sc = self.canvas.scene
        if sc is not None and not self.canvas.load_error:
            last = [i for i in sc.instances if re.sub(r"\d+$", "", i.cls.name) == b["cls"]]
            if last:
                if not self.b_layout.isChecked():
                    self.set_edit_mode(True)
                self.canvas.select(last[-1])
        self.status.setText(f"<span style='color:#8cf0b4'>Добавлено: {b['title']}</span>")

    def _context_menu(self, gpos):
        inst = self.canvas.selected()
        if inst is None:
            return
        sel = self.canvas.selected_all()
        m = QMenu(self)
        if len(sel) > 1:
            m.addAction(f"Сгруппировать ({len(sel)})", self._group)
            m.addAction("Разгруппировать", self._ungroup)
            m.addSeparator()
            m.addAction(f"Удалить ({len(sel)})",
                        lambda: self._on_delete_inst([(i.line, i.cls.name) for i in sel if self.canvas.editable(i)]))
        else:
            ed = self.canvas.editable(inst)
            a = m.addAction("Копия", lambda: self._clone(inst)); a.setEnabled(ed)
            a = m.addAction("На передний план", lambda: self._z(inst, +1)); a.setEnabled(ed)
            a = m.addAction("На задний план", lambda: self._z(inst, -1)); a.setEnabled(ed)
            m.addSeparator()
            m.addAction("Показать код", lambda: self._goto_line(inst.line))
            m.addSeparator()
            a = m.addAction("Удалить", lambda: self._on_delete_inst([(inst.line, inst.cls.name)])); a.setEnabled(ed)
        m.exec(gpos)

    def _clear_inspector(self):
        while self.insp_l.count():
            it = self.insp_l.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
            elif it.layout() is not None:
                lay = it.layout()
                while lay.count():
                    x = lay.takeAt(0).widget()
                    if x is not None:
                        x.hide()
                        x.deleteLater()

    def _section(self, title):
        lab = QLabel(title)
        lab.setStyleSheet("color:#8d89a6; font-weight:700; letter-spacing:1px; padding-top:8px;")
        self.insp_l.addWidget(lab)

    def _refresh_inspector(self):
        if not hasattr(self, "insp_l"):
            return
        sa = self.insp.parentWidget().parentWidget() if self.insp.parentWidget() else None
        cur = self.canvas.selected()
        key = (cur.cls.name, self.canvas._ordinal(cur)) if cur is not None else None
        if sa is not None and key is not None and key == getattr(self, "_insp_key", None):
            keep = sa.verticalScrollBar().value()
            QTimer.singleShot(0, lambda: sa.verticalScrollBar().setValue(keep))
        self._insp_key = key
        self._clear_inspector()
        sel = self.canvas.selected_all() if self.canvas.edit_mode else []
        if not sel:
            lab = QLabel("СВОЙСТВА\n\nЩёлкните по виджету на сцене,\nчтобы настроить его.\n\n"
                         "Ctrl+клик или рамка мышью —\nвыбрать несколько."
                         if self.canvas.edit_mode else
                         "СВОЙСТВА\n\nВключите «Двигать» вверху,\nчтобы выбирать виджеты на сцене.")
            lab.setStyleSheet("color:#8d89a6;")
            self.insp_l.addWidget(lab)
            self.insp_l.addStretch(1)
            return
        if len(sel) > 1:
            self._inspector_many(sel)
            return
        inst = sel[0]
        cls = inst.cls.name
        line = inst.line
        editable = self.canvas.editable(inst)
        blk = DL.block_of(cls)
        title = blk["title"] if blk else cls
        head = QLabel(f"<span style='font-size:15px; font-weight:800; color:#ffb347'>{title}</span>"
                      f"<span style='color:#6f6b88'>  {cls}</span>")
        self.insp_l.addWidget(head)
        row = QHBoxLayout()
        for text, tip, fn in (("Копия", "Сделать копию рядом", lambda: self._clone(inst)),
                              ("▲", "На передний план", lambda: self._z(inst, +1)),
                              ("▼", "На задний план", lambda: self._z(inst, -1)),
                              ("</>", "Показать код этого виджета", lambda: self._goto_line(line)),
                              ("Удалить", "Удалить со сцены (Delete)", lambda: self._on_delete_inst([(line, cls)]))):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            b.setEnabled(editable or text == "</>")
            row.addWidget(b)
        self.insp_l.addLayout(row)
        if not editable:
            lab = QLabel("Этот виджет создаётся в цикле —\nего свойства меняются в коде.")
            lab.setStyleSheet("color:#8d89a6;")
            self.insp_l.addWidget(lab)
            self.insp_l.addStretch(1)
            return
        src = self.edit.toPlainText()
        current = DL.get_props(src, line, cls)
        meta = DL.prop_meta(src, cls)

        # положение и размер — в процентах экрана
        self._section("ПОЛОЖЕНИЕ И РАЗМЕР, %")
        g = self.canvas._frac(inst)
        geo = QGridLayout()
        geo.setHorizontalSpacing(6)
        boxes = {}
        for i, (k, lab) in enumerate((("x", "X"), ("y", "Y"), ("w", "Ширина"), ("h", "Высота"))):
            sb = QDoubleSpinBox()
            sb.setRange(0.0, 100.0)
            sb.setDecimals(1)
            sb.setSingleStep(0.5)
            sb.setValue(g[k] * 100)
            boxes[k] = sb
            geo.addWidget(QLabel(lab), i // 2, (i % 2) * 2)
            geo.addWidget(sb, i // 2, (i % 2) * 2 + 1)
        def commit_geo(_=None, ln=line, c=cls):
            vals = {k: max(0.0, min(1.0, b.value() / 100)) for k, b in boxes.items()}
            vals["w"] = max(vals["w"], 0.005)
            vals["h"] = max(vals["h"], 0.005)
            self._on_geom([(ln, c, vals)])
        for b in boxes.values():
            b.editingFinished.connect(commit_geo)
        self.insp_l.addLayout(geo)

        # свойства блока — понятными контролами
        names = [pn for pn, _pe, _ln in inst.cls.props]
        if names:
            self._section("НАСТРОЙКИ")
            grid = QGridLayout()
            grid.setHorizontalSpacing(6)
            grid.setVerticalSpacing(5)
            for r, pn in enumerate(names):
                self._prop_row(grid, r, inst, pn, current.get(pn), meta.get(pn, {}))
            self.insp_l.addLayout(grid)

        # события — без кода
        self._section("ЧТО ДЕЛАТЬ")
        eg = QGridLayout()
        eg.setHorizontalSpacing(6)
        eg.setVerticalSpacing(4)
        for r, (ev, etitle) in enumerate(DL.EVENTS):
            key = "on_" + ev
            cur = current.get(key, "").strip()
            eg.addWidget(QLabel(etitle), r, 0)
            cb = QComboBox()
            cb.addItem("— ничего —", "")
            for ptitle, code in DL.EVENT_PRESETS:
                cb.addItem(ptitle, code)
            cb.addItem("Свой код…", "__custom__")
            idx = cb.findData(cur) if cur else 0
            if cur and idx < 0:
                cb.addItem("свой код", cur)
                idx = cb.count() - 1
            cb.setCurrentIndex(max(0, idx))
            if ev in inst.cls.handlers:
                cb.setToolTip("У виджета уже есть «on " + ev + "» в коде — это действие выполнится вдобавок")
            cb.activated.connect(lambda _i, c=cb, k=key, ln=line, cl=cls, prev=cur: self._set_event(c, k, ln, cl, prev))
            eg.addWidget(cb, r, 1)
        self.insp_l.addLayout(eg)

        # редко нужное — свёрнуто
        adv_btn = QToolButton()
        adv_btn.setText("Дополнительно ▸")
        adv_btn.setCheckable(True)
        adv_btn.setStyleSheet("QToolButton { border: none; color: #8d89a6; padding-top: 8px; }")
        self.insp_l.addWidget(adv_btn)
        adv = QWidget()
        ag = QGridLayout(adv)
        ag.setContentsMargins(0, 0, 0, 0)
        ag.setHorizontalSpacing(6)
        adv_meta = {"z": {"comment": "слой: больше — выше"}, "group": {"comment": "общая группа выделяется вместе"},
                    "visible": {}, "clip": {"comment": "обрезать рисование по рамке виджета"},
                    "cache": {"comment": "рисовать один раз и запоминать картинку (для статичного)"},
                    "resolution": {"comment": "0.1…1 — рисовать в меньшем разрешении (только процессором)",
                                   "range": (0.1, 1.0)}}
        for r, pn in enumerate(adv_meta):
            self._prop_row(ag, r, inst, pn, current.get(pn), adv_meta[pn])
        adv.setVisible(False)
        adv_btn.toggled.connect(lambda on, a=adv, b=adv_btn: (a.setVisible(on),
                                                               b.setText("Дополнительно ▾" if on else "Дополнительно ▸")))
        self.insp_l.addWidget(adv)
        self.insp_l.addStretch(1)

    # ── одна строка свойства: контрол по типу значения ──
    def _prop_row(self, grid, r, inst, pn, cur_text, meta):
        line, cls = inst.line, inst.cls.name
        dv = inst.fields.get(pn)
        label = QLabel(PROP_LABELS.get(pn, pn))
        tip = (meta.get("comment") or "").strip()
        label.setToolTip((tip + "\n" if tip else "") + f"свойство «{pn}»")
        grid.addWidget(label, r, 0)

        def commit(text, k=pn):
            self._set_prop(line, cls, k, text)
        opts = meta.get("options")
        rng = meta.get("range")
        is_color = type(dv).__name__ == "QColor" or (isinstance(dv, str) and dv.startswith("#")) or \
            pn.endswith("color") or pn.endswith("color2") or pn in ("light", "light2")
        w = None
        if pn == "src":
            w = QWidget()
            hl = QHBoxLayout(w)
            hl.setContentsMargins(0, 0, 0, 0)
            ed = QLineEdit(str(dv or ""))
            ed.setPlaceholderText("файл не выбран")
            ed.editingFinished.connect(lambda e=ed: commit(DL._lit(e.text().strip())) if e.text().strip() else None)
            pick = QPushButton("Выбрать…")
            pick.clicked.connect(lambda _=False, e=ed: self._pick_file(e, pn, line, cls))
            hl.addWidget(ed, 1)
            hl.addWidget(pick)
        elif opts:
            w = QComboBox()
            for o in opts:
                w.addItem(OPTION_LABELS.get(o, o), o)
            i = w.findData(str(dv)) if dv is not None else -1
            w.setCurrentIndex(max(0, i))
            w.activated.connect(lambda _i, c=w: commit(DL._lit(c.currentData())))
        elif isinstance(dv, bool):
            w = QCheckBox()
            w.setChecked(dv)
            w.toggled.connect(lambda on: commit("true" if on else "false"))
        elif is_color:
            w = self._color_button(dv, cur_text or meta.get("default"), lambda text: commit(text),
                                   allow_none=(dv is None or (meta.get("default") or "") == "nil"))
        elif isinstance(dv, (int, float)):
            lo, hi = rng if rng else ((0.0, 1.0) if pn in UNIT_PROPS else (min(0.0, float(dv)) * 4 - 0, 4000.0))
            if not rng and float(dv) < 0:
                lo = -4000.0
            w = QDoubleSpinBox()
            w.setRange(lo, hi)
            whole = float(dv).is_integer() and not (rng and hi - lo <= 1) and pn not in UNIT_PROPS
            w.setDecimals(0 if whole else 2)
            w.setSingleStep(1 if whole else max(0.01, round((hi - lo) / 50, 2)))
            w.setValue(float(dv))
            w.setKeyboardTracking(False)                    # значение уходит по Enter / стрелкам, не по каждой цифре
            t = QTimer(w)
            t.setSingleShot(True)
            t.setInterval(250)
            t.timeout.connect(lambda sb=w, wh=whole: commit(str(int(sb.value())) if wh else DL.fmt_num(sb.value())))
            w.valueChanged.connect(lambda _v, tm=t: tm.start())
        elif isinstance(dv, str) or (dv is None and pn in ("font", "label", "d")):
            w = QLineEdit("" if dv is None else str(dv))
            w.setPlaceholderText("по умолчанию")
            w.editingFinished.connect(lambda e=w: commit(DL._lit(e.text())) if e.text() != (dv or "") else None)
        else:
            w = QLineEdit(cur_text or "")
            w.setPlaceholderText("выражение: " + (meta.get("default") or "nil"))
            w.setToolTip("Значение — выражение EchoScript")
            w.editingFinished.connect(lambda e=w: commit(e.text()) if e.text().strip() else None)
            label.setText(label.text() + "  ƒ")
        if tip:
            w.setToolTip(tip)
        grid.addWidget(w, r, 1)
        if cur_text is not None:                            # значение задано у этого виджета — можно сбросить
            rb = QToolButton()
            rb.setText("↺")
            rb.setToolTip("Вернуть значение по умолчанию")
            rb.setStyleSheet("QToolButton { border: none; color: #8d89a6; }")
            rb.clicked.connect(lambda _=False, k=pn: self._reset_prop(line, cls, k))
            grid.addWidget(rb, r, 2)

    def _color_button(self, dv, cur_text, commit, allow_none=False):
        """Образец цвета; по щелчку — цвета темы, свой цвет или «нет»."""
        try:
            from dsl_runtime import to_qcolor
            col = to_qcolor(dv) if dv is not None else None
        except Exception:                                  # noqa: BLE001
            col = None
        b = QPushButton()
        name = ""
        if cur_text and cur_text.strip().startswith("theme."):
            name = "тема: " + THEME_LABELS.get(cur_text.strip()[6:], cur_text.strip()[6:])
        elif col is not None:
            name = col.name()
        else:
            name = "нет"
        b.setText("  " + name)
        b.setIcon(_swatch(col))
        b.setStyleSheet("text-align: left;")
        theme = self.canvas.theme() or {}
        m = QMenu(b)
        m.addSection("Цвета темы")
        for k in ("accent", "accent2", "accent3", "text", "muted", "bg", "panel"):
            v = theme.get(k)
            if v is None:
                continue
            try:
                from dsl_runtime import to_qcolor
                qc = to_qcolor(v)
            except Exception:                              # noqa: BLE001
                continue
            m.addAction(_swatch(qc), THEME_LABELS.get(k, k), lambda kk=k: commit(f"theme.{kk}"))
        m.addSeparator()
        m.addAction("Выбрать свой цвет…", lambda: self._pick_any_color(col, commit))
        if allow_none:
            m.addAction("Нет (без второго цвета)", lambda: commit("nil"))
        b.setMenu(m)
        return b

    def _pick_any_color(self, start, commit):
        c = QColorDialog.getColor(start or QColor("#ffffff"), self, "Цвет",
                                  QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if c.isValid():
            hx = c.name(QColor.NameFormat.HexRgb) if c.alpha() == 255 else \
                "#%02x%02x%02x%02x" % (c.red(), c.green(), c.blue(), c.alpha())
            commit(f'"{hx}"')

    def _reset_prop(self, line, cls, key):
        src = self.edit.toPlainText()
        new = DL.remove_prop(src, line, cls, key)
        if new is not None and new != src:
            self.apply_source(new)

    def _set_event(self, combo, key, line, cls, prev):
        code = combo.currentData()
        if code == "__custom__":
            start = prev or "fn() {\n  \n}"
            text, ok = QInputDialog.getMultiLineText(self, "Свой код события",
                                                     "Функция EchoScript (параметры как у события: "
                                                     "click(x, y), wheel(d), beat(power)…):", start)
            if not ok or not text.strip():
                self._refresh_inspector()
                return
            code = " ".join(l.strip() for l in text.strip().splitlines() if l.strip())
        src = self.edit.toPlainText()
        new = DL.remove_prop(src, line, cls, key) if not code else DL.set_props(src, line, cls, {key: code})
        if new is not None and new != src:
            self.apply_source(new)

    def _inspector_many(self, sel):
        n = len(sel)
        head = QLabel(f"<b style='color:#ffb347'>Выбрано: {n}</b>")
        self.insp_l.addWidget(head)
        eds = [i for i in sel if self.canvas.editable(i)]
        groups = {i.fields.get("group") for i in sel if i.fields.get("group")}

        def row(btns):
            r = QHBoxLayout()
            for text, tip, fn in btns:
                b = QPushButton(text)
                b.setToolTip(tip)
                b.clicked.connect(fn)
                b.setEnabled(len(eds) >= 2 or text in ("Удалить", "Разгруппировать"))
                r.addWidget(b)
            self.insp_l.addLayout(r)
        lab = QLabel("ВЫРОВНЯТЬ")
        lab.setStyleSheet("color:#8d89a6; font-weight:600;")
        self.insp_l.addWidget(lab)
        row([("⇤", "По левому краю", lambda: self._align("left")),
             ("⇹", "По центру по горизонтали", lambda: self._align("hcenter")),
             ("⇥", "По правому краю", lambda: self._align("right")),
             ("⤒", "По верху", lambda: self._align("top")),
             ("⇕", "По центру по вертикали", lambda: self._align("vcenter")),
             ("⤓", "По низу", lambda: self._align("bottom"))])
        row([("↔ поровну", "Распределить по горизонтали с равными промежутками", lambda: self._align("hdist")),
             ("↕ поровну", "Распределить по вертикали с равными промежутками", lambda: self._align("vdist"))])
        row([("= ширина", "Ширина как у последнего выбранного", lambda: self._align("samew")),
             ("= высота", "Высота как у последнего выбранного", lambda: self._align("sameh"))])
        lab = QLabel("ГРУППА")
        lab.setStyleSheet("color:#8d89a6; font-weight:600; padding-top:6px;")
        self.insp_l.addWidget(lab)
        row([("Сгруппировать", "Выбираются и двигаются вместе (свойство group)", self._group),
             ("Разгруппировать", "Убрать свойство group", self._ungroup),
             ("Удалить", "Удалить выбранные", lambda: self._on_delete_inst([(i.line, i.cls.name) for i in eds]))])
        if groups:
            g = QLabel("группы: " + ", ".join(sorted(groups)))
            g.setStyleSheet("color:#8d89a6;")
            self.insp_l.addWidget(g)
        self.insp_l.addStretch(1)

    def _align(self, how):
        c = self.canvas
        sel = [i for i in c.selected_all() if c.editable(i)]
        if len(sel) < 2:
            return
        rs = {i: c._rect(i) for i in sel}
        ref = rs[sel[-1]]
        out = []
        if how in ("hdist", "vdist"):
            horiz = how == "hdist"
            order = sorted(sel, key=lambda i: rs[i].left() if horiz else rs[i].top())
            first, last = rs[order[0]], rs[order[-1]]
            total = sum(rs[i].width() if horiz else rs[i].height() for i in order)
            span = (last.right() - first.left()) if horiz else (last.bottom() - first.top())
            gap = (span - total) / (len(order) - 1)
            pos = first.left() if horiz else first.top()
            for i in order:
                r = QRectF(rs[i])
                if horiz:
                    r.moveLeft(pos)
                    pos += r.width() + gap
                else:
                    r.moveTop(pos)
                    pos += r.height() + gap
                out.append((i, r))
        else:
            box = rs[sel[0]]
            for i in sel[1:]:
                box = box.united(rs[i])
            for i in sel:
                r = QRectF(rs[i])
                if how == "left":
                    r.moveLeft(box.left())
                elif how == "right":
                    r.moveRight(box.right())
                elif how == "hcenter":
                    r.moveLeft(box.center().x() - r.width() / 2)
                elif how == "top":
                    r.moveTop(box.top())
                elif how == "bottom":
                    r.moveBottom(box.bottom())
                elif how == "vcenter":
                    r.moveTop(box.center().y() - r.height() / 2)
                elif how == "samew":
                    r.setWidth(ref.width())
                elif how == "sameh":
                    r.setHeight(ref.height())
                out.append((i, r))
        c.set_rects(out)

    def _group(self):
        c = self.canvas
        sel = [i for i in c.selected_all() if c.editable(i)]
        if len(sel) < 2:
            return
        used = {i.fields.get("group") for i in c.scene.instances}
        k = 1
        while f"g{k}" in used:
            k += 1
        src = self.edit.toPlainText()
        for i in sorted(sel, key=lambda i: -i.line):
            new = DL.set_props(src, i.line, i.cls.name, {"group": f'"g{k}"'})
            src = new if new is not None else src
        self.apply_source(src)
        self.status.setText(f"<span style='color:#8cf0b4'>Группа g{k}: {len(sel)} виджета(ов)</span>")

    def _ungroup(self):
        c = self.canvas
        sel = [i for i in c.selected_all() if c.editable(i)]
        src = self.edit.toPlainText()
        for i in sorted(sel, key=lambda i: -i.line):
            new = DL.remove_prop(src, i.line, i.cls.name, "group")
            src = new if new is not None else src
        self.apply_source(src)

    def _set_prop(self, line, cls, key, text):
        text = text.strip()
        src = self.edit.toPlainText()
        cur = DL.get_props(src, line, cls)
        if text == cur.get(key, "") or not text:
            return
        new = DL.set_props(src, line, cls, {key: text})
        if new is not None:
            self.apply_source(new)

    def _pick_color(self, ed, key, line, cls, default):
        try:
            from dsl_runtime import to_qcolor
            start = to_qcolor(ed.text().strip().strip('"') or default or "#ffffff")
        except Exception:                                  # noqa: BLE001
            start = QColor("#ffffff")
        c = QColorDialog.getColor(start, self, "Цвет", QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if c.isValid():
            hx = c.name(QColor.NameFormat.HexRgb) if c.alpha() == 255 else \
                "#%02x%02x%02x%02x" % (c.red(), c.green(), c.blue(), c.alpha())
            ed.setText(f'"{hx}"')
            self._set_prop(line, cls, key, ed.text())

    def _pick_file(self, ed, key, line, cls):
        path, _ = QFileDialog.getOpenFileName(self, "Файл", os.path.expanduser("~"),
                                              "Картинки и видео (*.png *.jpg *.jpeg *.gif *.webp *.bmp *.mp4 *.webm "
                                              "*.mov *.mkv *.avi *.m4v);;Все файлы (*)")
        if path:
            ed.setText('"' + path.replace("\\", "/") + '"')
            self._set_prop(line, cls, key, ed.text())

    def _z(self, inst, d):
        z = int(inst.fields.get("z", 0) or 0) + d
        new = DL.move_z(self.edit.toPlainText(), inst.line, inst.cls.name, z)
        if new is not None:
            self.apply_source(new)

    def _clone(self, inst):
        src = self.edit.toPlainText()
        props = DL.get_props(src, inst.line, inst.cls.name)
        g = self.canvas._frac(inst)
        props.update({"x": DL.fmt_num(min(0.95, g["x"] + 0.03)), "y": DL.fmt_num(min(0.95, g["y"] + 0.03))})
        new = DL.add_instance(src, inst.cls.name, ", ".join(f"{k}: {v}" for k, v in props.items()))
        self.apply_source(new)
        sc = self.canvas.scene
        if sc is not None:
            same = [i for i in sc.instances if i.cls.name == inst.cls.name]
            if same:
                self.canvas.select(same[-1])

    # ══ файлы ══
    def _ask_name(self, title, default):
        name, ok = QInputDialog.getText(self, title, "Название:", text=default)
        name = (name or "").strip()
        if not ok or not name:
            return None
        if find_script(name):
            QMessageBox.information(self, title, "Скрипт с таким названием уже есть.")
            return None
        return name

    def _create(self, name, text):
        if self._dirty:
            self._save(quiet=True)
        with open(user_path(name), "w", encoding="utf-8") as f:
            f.write(text)
        self.refresh(select=name)
        self.scripts_changed.emit()
        self.script_changed.emit(name)

    def _new(self, kind="blank"):
        name = self._ask_name("Новый скрипт", "Моя тема")
        if not name:
            return
        q = name.replace('"', "'")
        self._create(name, DL.starter(name) if kind == "starter" else DL.BLANK % q)

    def _dup(self):
        name = self._ask_name("Копия скрипта", self.current + " 2")
        if name:
            self._create(name, DL.theme_set(self.edit.toPlainText(), {"name": name}))

    def _rename(self):
        info = self._info(self.current)
        if not info or info["builtin"] or info["overridden"]:
            QMessageBox.information(self, "EchoScript", "Встроенный скрипт переименовать нельзя — сделайте копию.")
            return
        name = self._ask_name("Переименовать", info["name"])
        if not name:
            return
        self._save(quiet=True)
        new_path = user_path(name)
        try:
            os.replace(info["path"], new_path)
        except OSError as e:
            QMessageBox.warning(self, "EchoScript", f"Не удалось переименовать: {e}")
            return
        self.current = name
        self.refresh(select=name)
        self.apply_source(DL.theme_set(self.edit.toPlainText(), {"name": name}))
        self.scripts_changed.emit()
        self.script_changed.emit(name)

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт скрипта", os.path.join(os.path.expanduser("~"),
                                              self.current + ".echo"), "EchoScript (*.echo)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.edit.toPlainText())
            self.status.setText(f"<span style='color:#8cf0b4'>Сохранено: {os.path.basename(path)}</span>")

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(self, "Импорт скрипта", os.path.expanduser("~"), "EchoScript (*.echo)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except OSError as e:
            QMessageBox.warning(self, "EchoScript", f"Не удалось прочитать: {e}")
            return
        base = os.path.splitext(os.path.basename(path))[0]
        name = base
        n = 2
        while find_script(name):
            name = f"{base} {n}"
            n += 1
        self._create(name, text)

    def _delete(self):
        info = self._info(self.current)
        if not info or info["builtin"]:
            return
        what = "Вернуть встроенную версию? Ваши правки удалятся." if info["overridden"] else \
            f"Удалить скрипт «{info['name']}»?"
        if QMessageBox.question(self, "EchoScript", what) != QMessageBox.StandardButton.Yes:
            return
        try:
            os.remove(info["path"])
        except OSError:
            pass
        keep = info["name"] if info["overridden"] else None
        self._dirty = False
        self.refresh(select=keep)
        self.scripts_changed.emit()
        self.script_changed.emit(self.combo.currentData())

    def closeEvent(self, e):
        # отдельное окно закрыли крестиком — вернуть панель в плеер, а не уничтожать
        if self.isWindow():
            e.ignore()
            self.detach_requested.emit()
            return
        super().closeEvent(e)


def _show(v) -> str:
    if type(v).__name__ == "QColor":
        return v.name()
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return DL.fmt_num(v)
    if v is None:
        return "nil"
    if isinstance(v, str):
        return f'"{v}"'
    s = str(v)
    return s if len(s) < 40 else s[:37] + "…"
