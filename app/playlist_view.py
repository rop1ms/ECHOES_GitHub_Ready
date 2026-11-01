# playlist_view.py
"""
Spotify-style страница плейлиста.

Виджет ничего не знает о движке воспроизведения / файловой системе —
только рисует то, что ему передали через render(), и сигналит наружу
о действиях пользователя. Всю логику (плей, сохранение, диалоги)
обрабатывает MainWindow.
"""
from img_load import load_pixmap
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget,
    QListWidgetItem, QAbstractItemView, QMenu, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtGui import QColor, QPainter, QLinearGradient, QPainterPath, QShortcut, QKeySequence, QIcon, QPixmap


def _fmt_mmss(seconds) -> str:
    try:
        seconds = max(0, int(float(seconds or 0)))
    except (TypeError, ValueError):
        seconds = 0
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _fmt_total(seconds_total) -> str:
    seconds_total = max(0, int(seconds_total or 0))
    h, rem = divmod(seconds_total, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h} ч {m} мин"
    if m > 0:
        return f"{m} мин"
    return f"{s} сек"


def _ru_plural_tracks(n: int) -> str:
    n_abs = abs(int(n)) % 100
    n1 = n_abs % 10
    if 11 <= n_abs <= 14:
        return "треков"
    if n1 == 1:
        return "трек"
    if 2 <= n1 <= 4:
        return "трека"
    return "треков"


class PlaylistHeader(QWidget):
    """Верхний блок: вертикальный градиент из цвета обложки в фон панели —
    тот самый Spotify-style «fade», подстроенный под цвет обложки."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._top = QColor("#2a2a35")
        self._bottom = QColor("#0e1018")
        self.setMinimumHeight(210)

    def set_colors(self, top: QColor, bottom: QColor):
        self._top = QColor(top)
        self._bottom = QColor(bottom)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width(), self.height(), 22, 22)
        p.setClipPath(path)
        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0.0, self._top)
        # Нижний цвет — прозрачный вариант акцентного цвета, чтобы градиент
        # плавно растворялся в фоне интерфейса, а не обрывался тёмным прямоугольником
        fade = QColor(self._top)
        fade.setAlpha(0)
        grad.setColorAt(0.72, fade)
        grad.setColorAt(1.0, fade)
        p.fillRect(self.rect(), grad)
        p.end()


class PlaylistDetailView(QWidget):
    """Полноэкранная (в пределах центральной колонки) страница плейлиста."""

    back_requested = pyqtSignal()
    play_requested = pyqtSignal(str)
    shuffle_play_requested = pyqtSignal(str)
    track_activated = pyqtSignal(str, int)
    cover_change_requested = pyqtSignal(str)
    rename_requested = pyqtSignal(str)
    description_edit_requested = pyqtSignal(str)
    delete_requested = pyqtSignal(str)
    tracks_reordered = pyqtSignal(str)
    track_remove_requested = pyqtSignal(str, list)
    add_tracks_requested = pyqtSignal(str)             # открыть выбор треков из коллекции
    move_requested = pyqtSignal(str, list, int)        # (плейлист, строки, сдвиг -1/+1)
    tags_requested = pyqtSignal(str, int)              # редактор тегов для строки
    reveal_requested = pyqtSignal(str, int)       # «Показать в проводнике»: (плейлист, строка)
    delete_everywhere_requested = pyqtSignal(str, list)  # удалить из плеера целиком

    def __init__(self, parent=None):
        super().__init__(parent)
        self._name = None
        self._accent_color = QColor("#a78bfa")
        self._icon_cache: dict = {}
        self._placeholder_cover_fn = None
        self._round_pixmap_fn = None
        self.setObjectName("PlaylistDetail")
        self.setStyleSheet("#PlaylistDetail, .QWidget{background:transparent;}")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        # ── Header с градиентом ──
        self.header = PlaylistHeader()
        hv = QVBoxLayout(self.header)
        hv.setContentsMargins(22, 14, 22, 18)
        hv.setSpacing(10)

        back_row = QHBoxLayout()
        self.btn_back = QPushButton("Назад к плееру")
        self.btn_back.setObjectName("GhostBtn")
        self.btn_back.setToolTip("Вернуться к пластинке")
        self.btn_back.clicked.connect(self.back_requested.emit)
        back_row.addWidget(self.btn_back)
        back_row.addStretch()
        hv.addLayout(back_row)

        top_row = QHBoxLayout()
        top_row.setSpacing(18)

        self.cover_lbl = QLabel()
        self.cover_lbl.setFixedSize(120, 120)
        self.cover_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cover_lbl.setToolTip("Нажми, чтобы изменить обложку плейлиста")
        self.cover_lbl.mousePressEvent = lambda e: self.cover_change_requested.emit(self._name)
        top_row.addWidget(self.cover_lbl)

        info_col = QVBoxLayout()
        info_col.setSpacing(4)
        kind_lbl = QLabel("ПЛЕЙЛИСТ")
        kind_lbl.setObjectName("Section")
        info_col.addWidget(kind_lbl)

        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        self.title_lbl = QLabel("—")
        self.title_lbl.setStyleSheet("font-size:28px; font-weight:800; letter-spacing:-0.5px;")
        self.title_lbl.setWordWrap(True)
        title_row.addWidget(self.title_lbl, 1)
        self.btn_rename = QPushButton("Изм.")
        self.btn_rename.setObjectName("IconBtn")
        self.btn_rename.setFixedWidth(52)
        self.btn_rename.setToolTip("Переименовать плейлист")
        self.btn_rename.clicked.connect(lambda: self.rename_requested.emit(self._name))
        title_row.addWidget(self.btn_rename)
        info_col.addLayout(title_row)

        self.desc_lbl = QLabel("")
        self.desc_lbl.setObjectName("Sub")
        self.desc_lbl.setWordWrap(True)
        self.desc_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        self.desc_lbl.setToolTip("Нажми, чтобы изменить описание")
        self.desc_lbl.mousePressEvent = lambda e: self.description_edit_requested.emit(self._name)
        info_col.addWidget(self.desc_lbl)

        self.meta_lbl = QLabel("")
        self.meta_lbl.setObjectName("Sub")
        info_col.addWidget(self.meta_lbl)

        top_row.addLayout(info_col, 1)
        hv.addLayout(top_row)

        actions_row = QHBoxLayout()
        actions_row.setSpacing(8)
        self.btn_play = QPushButton("Играть")
        self.btn_play.setObjectName("AccentBtn")
        self.btn_play.clicked.connect(lambda: self.play_requested.emit(self._name))
        actions_row.addWidget(self.btn_play)
        self.btn_shuffle_play = QPushButton("Вперемешку")
        self.btn_shuffle_play.setObjectName("GhostBtn")
        self.btn_shuffle_play.clicked.connect(lambda: self.shuffle_play_requested.emit(self._name))
        actions_row.addWidget(self.btn_shuffle_play)
        actions_row.addStretch()
        self.btn_cover = QPushButton("Обложка")
        self.btn_cover.setObjectName("GhostBtn")
        self.btn_cover.setToolTip("Загрузить обложку плейлиста")
        self.btn_cover.clicked.connect(lambda: self.cover_change_requested.emit(self._name))
        actions_row.addWidget(self.btn_cover)
        self.btn_menu = QPushButton("Ещё ▾")
        self.btn_menu.setObjectName("IconBtn")
        self.btn_menu.setFixedWidth(72)
        self.btn_menu.setToolTip("Переименовать, описание, обложка, удалить плейлист")
        self.btn_menu.clicked.connect(self._show_menu)
        actions_row.addWidget(self.btn_menu)
        hv.addLayout(actions_row)

        root.addWidget(self.header)

        # ── понятная панель действий над треками ──
        tools = QHBoxLayout()
        tools.setContentsMargins(6, 0, 6, 0)
        tools.setSpacing(6)

        def tbtn(text, tip, fn, accent=False):
            b = QPushButton(text)
            b.setObjectName("AccentBtn" if accent else "GhostBtn")
            b.setToolTip(tip)
            b.clicked.connect(fn)
            tools.addWidget(b)
            return b
        self.btn_add_tracks = tbtn("＋ Добавить треки", "Выбрать треки из коллекции и добавить в этот плейлист",
                                   lambda: self.add_tracks_requested.emit(self._name), accent=True)
        tools.addSpacing(10)
        self.btn_up = tbtn("Выше", "Переместить выделенные треки выше (или перетащите мышкой)",
                           lambda: self._move(-1))
        self.btn_down = tbtn("Ниже", "Переместить выделенные треки ниже", lambda: self._move(1))
        self.btn_remove = tbtn("Убрать", "Убрать выделенные треки из ЭТОГО плейлиста (Delete).\n"
                                          "Из коллекции и с диска они не удаляются.", self._remove_selected)
        self.btn_tags = tbtn("Теги", "Изменить название, исполнителя, альбом, обложку и текст файла",
                             self._tags_selected)
        tools.addStretch(1)
        self.btn_del_all = tbtn("Удалить из плеера…", "Удалить выделенные треки из коллекции и всех плейлистов "
                                                       "(можно и с диска)", self._delete_everywhere)
        root.addLayout(tools)
        self.sel_hint = QLabel("Выделите трек кликом (Ctrl/Shift + клик — несколько), чтобы двигать, убрать или удалить")
        self.sel_hint.setObjectName("Sub")
        self.sel_hint.setContentsMargins(10, 0, 0, 0)
        root.addWidget(self.sel_hint)

        self.empty_lbl = QLabel("Плейлист пока пуст.\nНажмите «＋ Добавить треки» или перетащите треки\n"
                                "из библиотеки слева на название плейлиста.")
        self.empty_lbl.setObjectName("Sub")
        self.empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_lbl.hide()
        root.addWidget(self.empty_lbl)

        # ── Список треков ──
        self.track_list = QListWidget()
        self.track_list.setObjectName("PlaylistTrackList")
        self.track_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.track_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.track_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.track_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.track_list.setIconSize(QSize(40, 40))
        self.track_list.setStyleSheet(
            "QListWidget#PlaylistTrackList {"
            "  background: transparent;"
            "  border: none;"
            "}"
            "QListWidget#PlaylistTrackList::item {"
            "  padding: 4px 6px;"
            "  border-radius: 6px;"
            "}"
            "QListWidget#PlaylistTrackList::item:hover {"
            "  background: rgba(128,128,128,0.16);"
            "}"
            "QListWidget#PlaylistTrackList::item:selected {"
            "  background: rgba(128,128,128,0.30);"
            "}"
        )
        self.track_list.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.track_list.model().rowsMoved.connect(lambda *a: self.tracks_reordered.emit(self._name))
        self.track_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.track_list.customContextMenuRequested.connect(self._context_menu)
        self.track_list.itemSelectionChanged.connect(self._update_tools)
        root.addWidget(self.track_list, 1)

        self._delete_sc = QShortcut(QKeySequence(Qt.Key.Key_Delete), self.track_list)
        self._delete_sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._delete_sc.activated.connect(self._remove_selected)

        hint = QLabel("Двойной клик — играть  ·  перетащите трек мышкой, чтобы поменять порядок  ·  "
                      "правая кнопка — все действия")
        hint.setObjectName("Sub")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(hint)

    # ------------------------------------------------------------------ #
    def _apply_accent_buttons(self, color: QColor):
        """Перекрашивает кнопку «Играть» и «Вперемешку» под акцентный цвет обложки."""
        # Вычисляем более тёмный вариант для hover/pressed
        h, s, v, _ = color.getHsvF()
        hover = QColor.fromHsvF(h, min(s * 1.05, 1.0), min(v * 1.12, 1.0))
        pressed = QColor.fromHsvF(h, min(s * 1.1, 1.0), max(v * 0.82, 0.0))

        # Яркость акцентного цвета определяет цвет текста (тёмный на светлом фоне)
        luma = 0.299 * color.redF() + 0.587 * color.greenF() + 0.114 * color.blueF()
        text_col = "#0d0d12" if luma > 0.55 else "#ffffff"

        self.btn_play.setStyleSheet(f"""
            QPushButton {{
                background: {color.name()};
                color: {text_col};
                border: none;
                border-radius: 20px;
                padding: 8px 28px;
                font-size: 14px;
                font-weight: 700;
                letter-spacing: 0.3px;
            }}
            QPushButton:hover {{
                background: {hover.name()};
            }}
            QPushButton:pressed {{
                background: {pressed.name()};
            }}
        """)

        # «Вперемешку» — ghost с обводкой в акцентный цвет
        self.btn_shuffle_play.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {color.name()};
                border: 1.5px solid {color.name()};
                border-radius: 20px;
                padding: 8px 20px;
                font-size: 13px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: rgba({color.red()},{color.green()},{color.blue()},40);
            }}
            QPushButton:pressed {{
                background: rgba({color.red()},{color.green()},{color.blue()},70);
            }}
        """)

    def render(self, name, tracks, cover_pixmap, description, author,
               top_color, bottom_color, current_path=None, icon_cache=None,
               placeholder_cover_fn=None, round_pixmap_fn=None):
        self._name = name
        self._accent_color = QColor(top_color)
        self._icon_cache = icon_cache if icon_cache is not None else {}
        self._placeholder_cover_fn = placeholder_cover_fn
        self._round_pixmap_fn = round_pixmap_fn
        self.header.set_colors(top_color, bottom_color)
        self.cover_lbl.setPixmap(cover_pixmap)
        self._apply_accent_buttons(QColor(top_color))
        self.title_lbl.setText(name)
        desc = (description or "").strip()
        self.desc_lbl.setText(desc if desc else "Нажми, чтобы добавить описание")
        total = sum(float(t.get("duration", 0) or 0) for t in tracks)
        count = len(tracks)
        self.meta_lbl.setText(
            f"Автор: {author}   ·   {count} {_ru_plural_tracks(count)}   ·   {_fmt_total(total)}"
        )
        self._populate_tracks(tracks, current_path)

    def _make_track_icon(self, cover_path: str) -> QIcon:
        """Возвращает иконку обложки трека (40×40, скруглённая).
        Использует кеш из MainWindow если передан через render()."""
        cache_key = f"pl40_{cover_path}"
        if cache_key in self._icon_cache:
            return self._icon_cache[cache_key]
        pm = None
        if cover_path and __import__("pathlib").Path(cover_path).exists():
            raw = load_pixmap(cover_path, 160)
            if not raw.isNull():
                if self._round_pixmap_fn:
                    pm = self._round_pixmap_fn(raw, 40)
                else:
                    pm = raw.scaled(40, 40, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                    Qt.TransformationMode.SmoothTransformation)
        if pm is None or pm.isNull():
            if self._placeholder_cover_fn:
                pm = self._placeholder_cover_fn(40)
            else:
                pm = QPixmap(40, 40)
                pm.fill(QColor(30, 32, 44))
        icon = QIcon(pm)
        self._icon_cache[cache_key] = icon
        return icon

    def _populate_tracks(self, tracks, current_path=None):
        self.track_list.blockSignals(True)
        self.track_list.clear()
        for i, t in enumerate(tracks):
            title = t.get("title") or "Untitled"
            artist = t.get("artist") or "Unknown artist"
            album = t.get("album") or ""
            dur = _fmt_mmss(t.get("duration", 0))
            middle = f"{title}  ·  {artist}" + (f"  ·  {album}" if album else "")
            path = str(t.get("path", ""))
            is_current = bool(current_path) and path == current_path
            prefix = f"{i + 1:02d}.  "
            item = QListWidgetItem(f"{prefix}{middle}   {dur}")
            item.setData(Qt.ItemDataRole.UserRole, path)
            # Обложка трека
            cover_path = str(t.get("cover", "") or "")
            item.setIcon(self._make_track_icon(cover_path))
            if is_current:
                item.setForeground(self._accent_color)
            self.track_list.addItem(item)
        self.track_list.blockSignals(False)
        self.empty_lbl.setVisible(not tracks)
        self.track_list.setVisible(bool(tracks))
        self._update_tools()

    def _sel_rows(self):
        return sorted({idx.row() for idx in self.track_list.selectedIndexes()})

    def _update_tools(self):
        rows = self._sel_rows()
        on = bool(rows)
        for b in (self.btn_up, self.btn_down, self.btn_remove, self.btn_tags, self.btn_del_all):
            b.setEnabled(on)
        if on:
            self.sel_hint.setText(f"Выделено: {len(rows)}  ·  Ctrl/Shift + клик — выделить несколько, "
                                  f"Ctrl+A — все  ·  кнопки выше работают со всеми выделенными")
        else:
            self.sel_hint.setText("Выделите трек кликом (Ctrl/Shift + клик — несколько), "
                                  "чтобы двигать, убрать или удалить")

    def select_rows(self, rows):
        self.track_list.clearSelection()
        for r in rows:
            it = self.track_list.item(r)
            if it is not None:
                it.setSelected(True)
        if rows:
            self.track_list.scrollToItem(self.track_list.item(rows[0]))

    def _move(self, delta):
        rows = self._sel_rows()
        if rows:
            self.move_requested.emit(self._name, rows, delta)

    def _tags_selected(self):
        rows = self._sel_rows()
        if rows:
            self.tags_requested.emit(self._name, rows[0])

    def _delete_everywhere(self):
        rows = self._sel_rows()
        if rows:
            self.delete_everywhere_requested.emit(self._name, rows)

    def current_track_paths(self):
        """Порядок путей треков как они сейчас показаны в списке (после drag&drop)."""
        return [self.track_list.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self.track_list.count())]

    # ------------------------------------------------------------------ #
    def _on_item_double_clicked(self, item):
        self.track_activated.emit(self._name, self.track_list.row(item))

    def _context_menu(self, pos):
        if not self.track_list.selectedItems():
            return
        menu = QMenu(self)
        act_play = menu.addAction("Играть")
        act_tags = menu.addAction("Теги, обложка и текст…")
        act_reveal = menu.addAction("Показать в проводнике")
        menu.addSeparator()
        act_up = menu.addAction("Выше")
        act_down = menu.addAction("Ниже")
        menu.addSeparator()
        act_remove = menu.addAction("Убрать из этого плейлиста")
        act_del = menu.addAction("    Удалить из плеера…")
        chosen = menu.exec(self.track_list.mapToGlobal(pos))
        if chosen == act_play:
            self.track_activated.emit(self._name, self.track_list.currentRow())
        elif chosen == act_tags:
            self._tags_selected()
        elif chosen == act_reveal:
            rows = self._sel_rows()
            if rows:
                self.reveal_requested.emit(self._name, rows[0])
        elif chosen == act_up:
            self._move(-1)
        elif chosen == act_down:
            self._move(1)
        elif chosen == act_remove:
            self._remove_selected()
        elif chosen == act_del:
            self._delete_everywhere()

    def _remove_selected(self):
        rows = sorted({idx.row() for idx in self.track_list.selectedIndexes()}, reverse=True)
        if not rows:
            return
        self.track_remove_requested.emit(self._name, rows)

    def _show_menu(self):
        menu = QMenu(self)
        act_rename = menu.addAction("Переименовать")
        act_desc = menu.addAction("Изменить описание")
        act_cover = menu.addAction("Изменить обложку")
        act_add = menu.addAction("Добавить треки из коллекции…")
        menu.addSeparator()
        act_delete = menu.addAction("Удалить плейлист")
        chosen = menu.exec(self.btn_menu.mapToGlobal(self.btn_menu.rect().bottomLeft()))
        if chosen == act_rename:
            self.rename_requested.emit(self._name)
        elif chosen == act_desc:
            self.description_edit_requested.emit(self._name)
        elif chosen == act_cover:
            self.cover_change_requested.emit(self._name)
        elif chosen == act_add:
            self.add_tracks_requested.emit(self._name)
        elif chosen == act_delete:
            self.delete_requested.emit(self._name)
