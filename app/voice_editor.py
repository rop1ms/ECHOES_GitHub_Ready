"""Liquid-glass custom voice command editor, with the automation action schema."""
from __future__ import annotations

from copy import deepcopy
import secrets

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel,
                             QLineEdit, QListWidget, QPlainTextEdit, QPushButton, QSpinBox,
                             QSplitter, QTableWidget, QVBoxLayout, QWidget)

from echo_glass import GlassFrame, glass_controls_qss
from voice_commands import MAX_COMMANDS, MAX_STEPS, route, validate_commands
import ya_voice

_ACTIONS = (
    ("wave", "Моя волна", "My wave"),
    ("playlist", "Включить плейлист", "Play a playlist"),
    ("find", "Включить трек / исполнителя", "Play a track / artist"),
    ("volume", "Громкость, %", "Set volume, %"),
    ("quieter", "Тише на, %", "Reduce volume by, %"),
    ("pause", "Пауза", "Pause"),
    ("play", "Продолжить музыку", "Resume music"),
    ("next", "Следующий трек", "Next track"),
    ("prev", "Предыдущий трек", "Previous track"),
    ("vol_up", "Громче на 15 %", "Volume up 15%"),
    ("vol_down", "Тише на 15 %", "Volume down 15%"),
    ("mute", "Выключить звук", "Mute"),
    ("unmute", "Включить звук", "Unmute"),
    ("like", "Переключить «Мне нравится»", "Toggle like"),
    ("shuffle", "Переключить перемешивание", "Toggle shuffle"),
    ("mini", "Переключить мини-плеер", "Toggle mini player"),
    ("notify", "Показать уведомление", "Show a notification"),
    ("wait", "Подождать, секунд", "Wait, seconds"),
)

_ERRORS = {
    "commands_limit": ("Можно добавить до 100 команд.", "You can add up to 100 commands."),
    "invalid_command": ("Некорректная команда. Удалите её и создайте заново.", "Invalid command. Remove it and create it again."),
    "phrases_required": ("Для каждой команды нужны 1–12 фраз длиной до 180 символов.", "Each command needs 1–12 phrases, up to 180 characters each."),
    "omit_wake": ("Впишите фразу без обращения «Эхос» / «ECHOES».", "Enter a phrase without the ECHOES wake word."),
    "duplicate_phrase": ("Фразы разных команд должны различаться, включая выключенные команды.", "Command phrases must be unique, including disabled commands."),
    "builtin_phrase": ("Эта фраза занята встроенной командой. Выберите собственную фразу.", "This phrase is a built-in command. Choose your own phrase."),
    "steps_required": ("В каждом сценарии должно быть от 1 до 40 шагов.", "Each scenario needs 1–40 steps."),
    "invalid_step": ("В сценарии есть неизвестное действие.", "The scenario contains an unknown action."),
    "invalid_number": ("Громкость: 0–100 %. Ожидание: 0–60 секунд.", "Volume: 0–100%. Wait: 0–60 seconds."),
    "argument_required": ("Укажите название плейлиста, трека или текст уведомления (до 500 символов).", "Enter a playlist name, track, or notification text (up to 500 characters)."),
}


class CommandsDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.items = deepcopy(ya_voice.commands(win))
        self._current = -1
        self._loading = False
        self.setObjectName("VoiceCommandsDialog")
        self.setWindowTitle(self.t("Голосовые команды и сценарии", "Voice commands and scenarios"))
        self.resize(970, 690)
        self.setMinimumSize(750, 540)
        self.setSizeGripEnabled(True)
        self.setStyleSheet(
            "QDialog#VoiceCommandsDialog{background:#080b11;}"
            "QLabel,QCheckBox{color:#edf3fb;background:transparent;}"
            "QLabel#VoiceSub{color:#a6b3c7;}"
            "QPushButton{color:#edf3fb;border-radius:10px;padding:7px 11px;}"
            "QLineEdit,QComboBox,QSpinBox{color:#edf3fb;border-radius:8px;padding:6px 8px;}"
            "QPlainTextEdit,QListWidget,QTableWidget{color:#edf3fb;background:rgba(5,9,16,160);"
            "border:1px solid rgba(216,231,250,35);border-top-color:rgba(235,244,255,70);border-radius:10px;}"
            "QListWidget::item{padding:10px 8px;border-radius:7px;}"
            "QListWidget::item:selected{background:rgba(160,190,235,40);}"
            "QHeaderView::section{background:#171e29;color:#b9c6da;border:none;padding:7px;}"
            "QTableWidget{gridline-color:#28303c;}QSplitter::handle{background:transparent;}"
            + glass_controls_qss())
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        surface = GlassFrame(radius=20)
        outer.addWidget(surface)
        body = QVBoxLayout(surface)
        body.setContentsMargins(20, 18, 20, 18)
        title = QLabel(self.t("Своя фраза — свой сценарий", "Your phrase, your scenario"))
        title.setStyleSheet("font-size:23px;font-weight:800;background:transparent;")
        body.addWidget(title)
        help_ = self.sub(self.t(
            "Шаги выполняются по порядку. Доступны действия автоматики и управление плеером. "
            "В фоне: «Эхос, мой вечер». По Ctrl+Alt+V: «мой вечер».",
            "Steps run in order using automation and player actions. In the background: “ECHOES, evening mode”. "
            "With Ctrl+Alt+V: “evening mode”."))
        body.addWidget(help_)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        body.addWidget(splitter, 1)
        left = QWidget()
        left.setStyleSheet("background:transparent;")
        column = QVBoxLayout(left)
        column.setContentsMargins(0, 0, 8, 0)
        self.list = QListWidget()
        self.list.setMinimumWidth(170)
        column.addWidget(self.list, 1)
        buttons = QHBoxLayout()
        add = QPushButton(self.t("Добавить", "Add"))
        add.clicked.connect(self.add_command)
        remove = QPushButton(self.t("Удалить", "Remove"))
        remove.clicked.connect(self.remove_command)
        buttons.addWidget(add)
        buttons.addWidget(remove)
        column.addLayout(buttons)
        splitter.addWidget(left)
        self.editor = QWidget()
        self.editor.setStyleSheet("background:transparent;")
        edit = QVBoxLayout(self.editor)
        edit.setContentsMargins(8, 0, 0, 0)
        self.enabled = QCheckBox(self.t("Команда включена", "Command enabled"))
        edit.addWidget(self.enabled)
        self.name = QLineEdit()
        self.name.setMaxLength(100)
        self.name.setPlaceholderText(self.t("Название сценария", "Scenario name"))
        edit.addWidget(self.name)
        edit.addWidget(QLabel(self.t("Фразы — каждая с новой строки, без «Эхос»", "Phrases — one per line, without “ECHOES”")))
        self.phrases = QPlainTextEdit()
        self.phrases.setPlaceholderText(self.t("мой вечер\nвечерний режим", "evening mode\nmy evening"))
        self.phrases.setMaximumHeight(82)
        self.phrases.setTabChangesFocus(True)
        edit.addWidget(self.phrases)
        edit.addWidget(QLabel(self.t("Сценарий", "Scenario")))
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels((self.t("Действие", "Action"), self.t("Параметр", "Argument")))
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setDefaultSectionSize(42)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        edit.addWidget(self.table, 1)
        step_buttons = QHBoxLayout()
        for label, callback in (
            (self.t("Добавить шаг", "Add step"), self.add_step),
            (self.t("Убрать", "Remove"), self.remove_step),
            (self.t("Выше", "Move up"), lambda: self.move_step(-1)),
            (self.t("Ниже", "Move down"), lambda: self.move_step(1)),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            step_buttons.addWidget(button)
        edit.addLayout(step_buttons)
        splitter.addWidget(self.editor)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([230, 640])
        test = QHBoxLayout()
        self.test_phrase = QLineEdit()
        self.test_phrase.setPlaceholderText(self.t("Проверить фразу текстом: «Эхос, мой вечер»", "Check a phrase: “ECHOES, evening mode”"))
        self.test_phrase.setMaxLength(250)
        self.test_phrase.returnPressed.connect(self.check_phrase)
        test.addWidget(self.test_phrase, 1)
        check = QPushButton(self.t("Проверить фразу", "Check phrase"))
        check.clicked.connect(self.check_phrase)
        test.addWidget(check)
        body.addLayout(test)
        self.status = self.sub("")
        self.status.setMinimumHeight(34)
        body.addWidget(self.status)
        footer = QHBoxLayout()
        stop = QPushButton(self.t("Остановить сценарий", "Stop scenario"))
        stop.clicked.connect(lambda: ya_voice.get(win).scenario.cancel())
        footer.addWidget(stop)
        footer.addStretch(1)
        cancel = QPushButton(self.t("Отмена", "Cancel"))
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        save = QPushButton(self.t("Сохранить", "Save"))
        save.setObjectName("AccentBtn")
        save.clicked.connect(self.save)
        footer.addWidget(save)
        body.addLayout(footer)
        self.list.currentRowChanged.connect(self.select_command)
        self._rebuild_list(0 if self.items else -1)

    def t(self, ru, en):
        return ya_voice.tr(self.win, ru, en)

    @staticmethod
    def sub(text):
        label = QLabel(text)
        label.setObjectName("VoiceSub")
        label.setWordWrap(True)
        return label

    def _error(self, error):
        ru, en = _ERRORS.get(str(error), (str(error), str(error)))
        self.status.setText(self.t(ru, en))

    def _rebuild_list(self, selection):
        self._loading = True
        self.list.clear()
        for item in self.items:
            self.list.addItem(item.get("name") or self.t("Новая команда", "New command"))
        self._loading = False
        self._current = -1
        self.list.setCurrentRow(selection)
        if selection < 0:
            self.editor.setEnabled(False)
            self.name.clear()
            self.phrases.clear()
            self.table.setRowCount(0)

    def _commit(self):
        if 0 <= self._current < len(self.items):
            item = self.items[self._current]
            item.update(name=self.name.text().strip(), on=self.enabled.isChecked(),
                        phrases=[s.strip() for s in self.phrases.toPlainText().splitlines() if s.strip()],
                        steps=self._read_steps())
            self.list.item(self._current).setText(item["name"] or self.t("Новая команда", "New command"))

    def select_command(self, row):
        if self._loading:
            return
        self._commit()
        self._current = row
        self.editor.setEnabled(0 <= row < len(self.items))
        if not 0 <= row < len(self.items):
            return
        item = self.items[row]
        self.name.setText(item.get("name", ""))
        self.enabled.setChecked(item.get("on", True))
        self.phrases.setPlainText("\n".join(item.get("phrases") or []))
        self._fill_steps(item.get("steps") or [])

    def add_command(self):
        self._commit()
        if len(self.items) >= MAX_COMMANDS:
            self._error("commands_limit")
            return
        self.items.append({"id": secrets.token_hex(8), "on": True,
                           "name": self.t("Новая команда", "New command"), "phrases": [],
                           "steps": [{"a": "wave"}]})
        self._rebuild_list(len(self.items) - 1)
        self.name.setFocus()
        self.name.selectAll()

    def remove_command(self):
        row = self.list.currentRow()
        if 0 <= row < len(self.items):
            self.items.pop(row)
            self._rebuild_list(min(row, len(self.items) - 1))

    def _fill_steps(self, steps):
        self.table.setRowCount(0)
        for step in steps:
            row = self.table.rowCount()
            self.table.insertRow(row)
            action = QComboBox()
            for key, ru, en in _ACTIONS:
                action.addItem(self.t(ru, en), key)
            index = action.findData(step.get("a"))
            if index < 0:
                action.addItem(self.t("Неизвестное действие", "Unknown action"), step.get("a"))
                index = action.count() - 1
            action.setCurrentIndex(index)
            self.table.setCellWidget(row, 0, action)
            self._parameter(row, step)
            action.currentIndexChanged.connect(lambda _=0, r=row: self._parameter(r, {}))

    def _parameter(self, row, step):
        action = self.table.cellWidget(row, 0).currentData()
        if action in ("volume", "quieter", "wait"):
            widget = QSpinBox()
            widget.setRange(0, 60 if action == "wait" else 100)
            widget.setSuffix(self.t(" с", " s") if action == "wait" else " %")
            widget.setValue(int(step.get("v", 1 if action == "wait" else 50)))
        elif action == "playlist":
            widget = QComboBox()
            widget.setEditable(True)
            try:
                names = self.win._ya.user_playlists()
            except (AttributeError, TypeError):
                names = list(getattr(self.win, "playlists", {}))
            widget.addItems(names)
            widget.setCurrentText(step.get("name", ""))
        else:
            widget = QLineEdit(step.get("text", ""))
            widget.setMaxLength(500)
            widget.setEnabled(action in ("find", "notify"))
            widget.setPlaceholderText(self.t("Текст уведомления", "Notification text") if action == "notify" else
                                      self.t("Трек или исполнитель", "Track or artist") if action == "find" else "—")
        self.table.setCellWidget(row, 1, widget)

    def _read_steps(self):
        result = []
        for row in range(self.table.rowCount()):
            action = self.table.cellWidget(row, 0).currentData()
            parameter = self.table.cellWidget(row, 1)
            step = {"a": action}
            if action in ("volume", "quieter", "wait"):
                step["v"] = parameter.value()
            elif action == "playlist":
                step["name"] = parameter.currentText()
            elif action in ("find", "notify"):
                step["text"] = parameter.text()
            result.append(step)
        return result

    def add_step(self):
        steps = self._read_steps()
        if len(steps) >= MAX_STEPS:
            self._error("steps_required")
            return
        steps.append({"a": "wave"})
        self._fill_steps(steps)
        self.table.selectRow(len(steps) - 1)

    def remove_step(self):
        row = self.table.currentRow()
        if row >= 0:
            steps = self._read_steps()
            steps.pop(row)
            self._fill_steps(steps)
            self.table.selectRow(min(row, len(steps) - 1))

    def move_step(self, offset):
        row = self.table.currentRow()
        steps = self._read_steps()
        target = row + offset
        if 0 <= row < len(steps) and 0 <= target < len(steps):
            steps[row], steps[target] = steps[target], steps[row]
            self._fill_steps(steps)
            self.table.selectRow(target)

    def check_phrase(self):
        self._commit()
        try:
            clean = validate_commands(self.items)
        except ValueError as error:
            self._error(error)
            return
        action, argument = route(self.test_phrase.text(), clean)
        if action == "scenario":
            name = next(item["name"] for item in clean if item["id"] == argument)
            self.status.setText(self.t(f"Фраза запускает сценарий «{name}». Проверка ничего не выполняет.",
                                      f"This phrase runs “{name}”. The check does not execute any actions."))
        elif action:
            label = next((self.t(ru, en) for key, ru, en in _ACTIONS if key == action), action)
            self.status.setText(self.t("Встроенная команда: ", "Built-in command: ") + label + (f" — {argument}" if argument else ""))
        else:
            self.status.setText(self.t("Совпадений нет. Действий не будет.", "No match. No action will run."))

    def save(self):
        self._commit()
        try:
            clean = validate_commands(self.items)
        except ValueError as error:
            self._error(error)
            return
        previous = self.win.settings.get("voice_commands")
        self.win.settings["voice_commands"] = clean
        try:
            self.win.save_settings()
        except Exception as error:                         # noqa: BLE001
            if previous is None:
                self.win.settings.pop("voice_commands", None)
            else:
                self.win.settings["voice_commands"] = previous
            self.status.setText(self.t(f"Не удалось сохранить: {error}", f"Could not save: {error}"))
            return
        self.accept()
