# track_tools.py
"""
Общие диалоги управления треками для ВСЕХ тем (Vinyl glass, Fluid, Winamp,
Echoes Music):

  • confirm_delete()  — «Удалить из плеера?» + галочка «и файл с диска (в Корзину)»;
  • TrackPicker       — выбрать треки из коллекции (поиск + галочки), чтобы
                        добавить их в плейлист;
  • move_to_trash()   — удалить файл в Корзину Windows (а не насовсем);
  • show_help()       — короткая понятная справка «как тут что делать».
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
                             QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QDialogButtonBox,
                             QAbstractItemView)


def _name(t: dict) -> str:
    title = t.get("title") or Path(str(t.get("path", ""))).stem or "Без названия"
    artist = t.get("artist") or ""
    return f"{artist} — {title}" if artist else title


def confirm_delete(parent, tracks: list) -> tuple[bool, bool]:
    """(удалять?, удалять ли файлы с диска)."""
    n = len(tracks)
    dlg = QDialog(parent)
    dlg.setWindowTitle("Удалить из плеера")
    dlg.setMinimumWidth(440)
    v = QVBoxLayout(dlg)
    v.setSpacing(10)
    head = QLabel(f"<b>Удалить {n} {'трек' if n == 1 else ('трека' if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else 'треков')} "
                  f"из плеера?</b>")
    v.addWidget(head)
    names = "\n".join("•  " + _name(t) for t in tracks[:6]) + (f"\n…и ещё {n - 6}" if n > 6 else "")
    lst = QLabel(names)
    lst.setWordWrap(True)
    v.addWidget(lst)
    info = QLabel("Треки пропадут из коллекции и из всех плейлистов.")
    info.setObjectName("Sub")
    info.setWordWrap(True)
    v.addWidget(info)
    cb = QCheckBox("Удалить и сами файлы с диска (в Корзину)")
    cb.setToolTip("Без галочки файлы останутся в папке — их можно будет добавить снова")
    v.addWidget(cb)
    bb = QDialogButtonBox()
    ok = bb.addButton("Удалить", QDialogButtonBox.ButtonRole.AcceptRole)
    ok.setObjectName("AccentBtn")
    bb.addButton("Отмена", QDialogButtonBox.ButtonRole.RejectRole)
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    v.addWidget(bb)
    res = dlg.exec() == QDialog.DialogCode.Accepted
    return res, bool(cb.isChecked())


def move_to_trash(path: str) -> bool:
    """Файл → Корзина. Если Корзина недоступна — False (файл не трогаем)."""
    try:
        from PyQt6.QtCore import QFile
        r = QFile.moveToTrash(str(path))
        ok = r[0] if isinstance(r, tuple) else bool(r)
        if ok:
            return True
    except Exception:                                # noqa: BLE001
        pass
    try:
        from send2trash import send2trash            # если вдруг установлен
        send2trash(str(path))
        return True
    except Exception:                                # noqa: BLE001
        return False


class TrackPicker(QDialog):
    """Выбор треков из коллекции: поиск + галочки."""

    def __init__(self, parent, library: list, title="Добавить треки", exclude_paths=()):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(560, 620)
        self._lib = [t for t in library if str(t.get("path", "")) not in set(exclude_paths)]
        self._checked: set = set()
        v = QVBoxLayout(self)
        v.setSpacing(8)
        tip = QLabel("Отметьте галочками треки и нажмите «Добавить». Поиск — по названию, исполнителю, альбому.")
        tip.setObjectName("Sub")
        tip.setWordWrap(True)
        v.addWidget(tip)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск в коллекции…")
        self.search.textChanged.connect(self._fill)
        v.addWidget(self.search)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.list.itemChanged.connect(self._on_item)
        self.list.itemClicked.connect(self._toggle)
        v.addWidget(self.list, 1)
        row = QHBoxLayout()
        b_all = QPushButton("Отметить все найденные")
        b_all.clicked.connect(lambda: self._set_all(True))
        b_none = QPushButton("Снять все")
        b_none.clicked.connect(lambda: self._set_all(False))
        row.addWidget(b_all)
        row.addWidget(b_none)
        row.addStretch(1)
        self.count = QLabel("")
        self.count.setObjectName("Sub")
        row.addWidget(self.count)
        v.addLayout(row)
        bb = QHBoxLayout()
        bb.addStretch(1)
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        self.ok = QPushButton("Добавить")
        self.ok.setObjectName("AccentBtn")
        self.ok.clicked.connect(self.accept)
        bb.addWidget(cancel)
        bb.addWidget(self.ok)
        v.addLayout(bb)
        self._fill()

    def _fill(self, *_):
        q = self.search.text().strip().lower()
        self.list.blockSignals(True)
        self.list.clear()
        shown = 0
        for i, t in enumerate(self._lib):
            text = _name(t)
            if q and q not in text.lower() and q not in str(t.get("album") or "").lower():
                continue
            it = QListWidgetItem(text)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if i in self._checked else Qt.CheckState.Unchecked)
            it.setData(Qt.ItemDataRole.UserRole, i)
            self.list.addItem(it)
            shown += 1
            if shown >= 3000:
                break
        self.list.blockSignals(False)
        self._update()

    def _toggle(self, it):
        it.setCheckState(Qt.CheckState.Unchecked if it.checkState() == Qt.CheckState.Checked
                         else Qt.CheckState.Checked)

    def _on_item(self, it):
        i = it.data(Qt.ItemDataRole.UserRole)
        if it.checkState() == Qt.CheckState.Checked:
            self._checked.add(i)
        else:
            self._checked.discard(i)
        self._update()

    def _set_all(self, on):
        self.list.blockSignals(True)
        for k in range(self.list.count()):
            it = self.list.item(k)
            it.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
            i = it.data(Qt.ItemDataRole.UserRole)
            (self._checked.add if on else self._checked.discard)(i)
        self.list.blockSignals(False)
        self._update()

    def _update(self):
        n = len(self._checked)
        self.count.setText(f"отмечено: {n}")
        self.ok.setText(f"Добавить ({n})" if n else "Добавить")
        self.ok.setEnabled(n > 0)

    def selected(self) -> list:
        return [self._lib[i] for i in sorted(self._checked)]


HELP_TEXT = """
<h3>Как пользоваться</h3>
<p><b>Коллекция (библиотека)</b><br>
• Клик по треку — играть.<br>
• Ctrl / Shift + клик — выделить несколько.<br>
• Правая кнопка мыши — теги и обложка, добавить в плейлист, удалить.<br>
• Треки можно <b>перетащить мышкой на плейлист</b> в списке плейлистов.</p>
<p><b>Плейлисты</b><br>
• «＋ Новый плейлист» — создать.<br>
• Клик по плейлисту — открыть его страницу, двойной клик — сразу играть.<br>
• На странице плейлиста: «＋ Добавить треки», «Выше» / «Ниже» — порядок
  (или перетаскивайте треки мышкой), «Убрать» — из этого плейлиста,
  «Теги» — изменить название/исполнителя/альбом/обложку файла.<br>
• Удалить или переименовать плейлист — кнопка «Ещё» на его странице
  или правая кнопка мыши по плейлисту в списке.</p>
<p><b>Музыка из интернета</b><br>
• Кнопка «Музыка» (Ctrl+M) — поиск и скачивание с YouTube Music / SoundCloud,
  исполнители, альбомы, «Моя волна».</p>
<p><b>Горячие клавиши</b>: Пробел — пауза, ← → — пред./след. трек,
Delete — удалить выделенное, F11 — полный экран.</p>
"""


def show_help(parent):
    QMessageBox.information(parent, "Как пользоваться", HELP_TEXT)
