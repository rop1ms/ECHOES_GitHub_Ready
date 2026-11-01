"""Explicit genre corrections. Kept separate from the audio analysis worker."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QListWidget, QListWidgetItem,
                            QDialogButtonBox, QMessageBox, QLineEdit)


class GenreCorrectionDialog(QDialog):
    def __init__(self, names, selected=(), parent=None):
        super().__init__(parent)
        self.setWindowTitle("Исправить жанр")
        self.resize(460, 530)
        layout = QVBoxLayout(self)
        hint = QLabel("Отметьте один или несколько жанров. Ваша оценка имеет приоритет над автоматическим определением.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        search = QLineEdit()
        search.setPlaceholderText("Найти жанр")
        layout.addWidget(search)
        self.list = QListWidget()
        for family, name in sorted(names.items(), key=lambda pair: pair[1].casefold()):
            item = QListWidgetItem(str(name).capitalize(), self.list)
            item.setData(Qt.ItemDataRole.UserRole, family)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if family in selected else Qt.CheckState.Unchecked)
        search.textChanged.connect(lambda value: [self.list.item(i).setHidden(value.casefold() not in self.list.item(i).text().casefold()) for i in range(self.list.count())])
        layout.addWidget(self.list, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.families = []

    def accept(self):
        self.families = [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())
                         if self.list.item(i).checkState() == Qt.CheckState.Checked]
        if not self.families:
            QMessageBox.information(self, "Выберите жанр", "Отметьте хотя бы один жанр. Убрать ручное исправление можно в окне результата.")
            return
        super().accept()
