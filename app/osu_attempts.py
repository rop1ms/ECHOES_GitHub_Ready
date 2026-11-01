# osu_attempts.py
"""
«Попытки по картам» темы osu!: сколько раз начинали каждую карту (трек + сложность), сколько
раз прошли и провалили, с какой попытки прошли впервые, лучшая точность и счёт. Колонки
сортируются щелчком, поиск — по треку; двойной щелчок — эта карта в выборе песни.
"""
from __future__ import annotations

import time

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
                             QTableWidget, QTableWidgetItem, QVBoxLayout)

import osu_mapgen as G

QSS = """
QDialog { background: #1d1621; }
QLabel { color: #ffffff; background: transparent; font-family: 'Segoe UI'; font-size: 13px; }
QLabel#Title { font-size: 22px; font-weight: 900; }
QLabel#Sub { color: #b9adc4; font-size: 12px; }
QLabel#Sum { color: #ffb3d4; font-size: 14px; font-weight: 700; }
QLineEdit { background: #2c2233; color: #ffffff; border: 1px solid #4a3d52; border-radius: 10px; padding: 6px 10px;
    font-size: 13px; }
QPushButton { background: #ff66aa; color: #ffffff; border: none; border-radius: 14px; padding: 7px 18px;
    font-weight: 700; font-size: 13px; }
QPushButton:hover { background: #ff85bb; }
QTableWidget { background: #241b29; color: #ffffff; border: 1px solid #3a2f42; border-radius: 10px;
    gridline-color: transparent; font-size: 13px; selection-background-color: rgba(255,102,170,0.35);
    alternate-background-color: #2a2030; }
QHeaderView::section { background: #2c2233; color: #ff9fc8; border: none; padding: 7px 6px; font-weight: 800;
    font-size: 12px; }
QTableCornerButton::section { background: #2c2233; border: none; }
"""

COLS = ["Трек", "Сложность", "★", "Попыток", "Пройдено", "Провалов", "Выходов", "Первое прохождение",
        "Лучшая точность", "Последняя игра"]


class _Num(QTableWidgetItem):
    """Ячейка, которая сортируется по числу, а показывает текст."""

    def __init__(self, text, value):
        super().__init__(text)
        self.value = value
        self.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

    def __lt__(self, other):
        try:
            return self.value < other.value
        except AttributeError:
            return super().__lt__(other)


class AttemptsDialog(QDialog):
    def __init__(self, shell):
        super().__init__(shell)
        self.shell = shell
        self.setWindowTitle("Попытки по картам")
        self.setStyleSheet(QSS)
        self.resize(1040, 620)
        from osu_theme import load_attempts
        self.data = load_attempts()
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 18, 22, 18)
        v.setSpacing(10)
        t = QLabel("Попытки по картам")
        t.setObjectName("Title")
        v.addWidget(t)
        plays = sum(int(e.get("plays", 0)) for e in self.data.values())
        passed = sum(1 for e in self.data.values() if int(e.get("passes", 0)) > 0)
        fails = sum(int(e.get("fails", 0)) for e in self.data.values())
        firsts = [int(e["first_pass"]) for e in self.data.values() if e.get("first_pass")]
        s = f"Всего попыток: {plays}  ·  карт: {len(self.data)}  ·  пройдено карт: {passed}  ·  провалов: {fails}"
        if firsts:
            s += f"  ·  в среднем карта проходится с {sum(firsts) / len(firsts):.1f}-й попытки"
        sm = QLabel(s)
        sm.setObjectName("Sum")
        sm.setWordWrap(True)
        v.addWidget(sm)
        row = QHBoxLayout()
        self.q = QLineEdit()
        self.q.setPlaceholderText("Найти трек или исполнителя…")
        self.q.setClearButtonEnabled(True)
        self.q.textChanged.connect(self._filter)
        row.addWidget(self.q, 1)
        v.addLayout(row)
        tb = self.tb = QTableWidget(0, len(COLS))
        tb.setHorizontalHeaderLabels(COLS)
        tb.verticalHeader().setVisible(False)
        tb.setAlternatingRowColors(True)
        tb.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        tb.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        tb.setShowGrid(False)
        hh = tb.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, len(COLS)):
            hh.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        tb.cellDoubleClicked.connect(self._open)
        v.addWidget(tb, 1)
        self._fill()
        note = QLabel("Попытка — каждый запуск карты (без Auto, просмотра повторов и проверки из редактора). "
                      "Выход и быстрый рестарт тоже считаются попыткой. Двойной щелчок — открыть карту. "
                      + ("Для карт, сыгранных до появления счётчика, известны только прохождения из рекордов."
                         if any(e.get("seeded") for e in self.data.values()) else ""))
        note.setObjectName("Sub")
        note.setWordWrap(True)
        v.addWidget(note)
        bb = QHBoxLayout()
        bb.addStretch(1)
        b = QPushButton("Закрыть")
        b.clicked.connect(self.accept)
        bb.addWidget(b)
        v.addLayout(bb)

    def _fill(self):
        tb = self.tb
        tb.setSortingEnabled(False)
        items = sorted(self.data.items(), key=lambda kv: -float(kv[1].get("last", 0)))
        tb.setRowCount(len(items))
        for r, (key, e) in enumerate(items):
            title = e.get("title") or ""
            artist = e.get("artist") or ""
            name = f"{artist} — {title}" if artist and title else (title or key.split("|")[0])
            it = QTableWidgetItem(name)
            it.setData(Qt.ItemDataRole.UserRole, (e.get("path", ""), e.get("diff", "")))
            it.setToolTip(name)
            tb.setItem(r, 0, it)
            dk = e.get("diff", "")
            dname = e.get("diff_name") or G.diff_info(dk)["name"] if dk else e.get("diff_name", "")
            tb.setItem(r, 1, _Num(dname, G.DIFF_KEYS.index(dk) if dk in G.DIFF_KEYS else 9))
            st = float(e.get("stars", 0))
            tb.setItem(r, 2, _Num(f"{st:.2f}" if st else "—", st))
            n = int(e.get("plays", 0))
            tb.setItem(r, 3, _Num(str(n), n))
            for c, k in ((4, "passes"), (5, "fails"), (6, "quits")):
                x = int(e.get(k, 0))
                tb.setItem(r, c, _Num(str(x), x))
            fp = int(e.get("first_pass") or 0)
            if fp:
                fp_t = "с 1-й" if fp == 1 else f"с {fp}-й"
            else:
                fp_t = "до учёта" if e.get("seeded") and e.get("passes") else "ещё нет"
            tb.setItem(r, 7, _Num(fp_t, fp or 10 ** 6))
            acc = float(e.get("best_acc", 0))
            tb.setItem(r, 8, _Num(f"{acc * 100:.2f}%" if acc else "—", acc))
            last = float(e.get("last", 0))
            tb.setItem(r, 9, _Num(time.strftime("%d.%m.%Y %H:%M", time.localtime(last)) if last else "—", last))
        tb.setSortingEnabled(True)

    def _filter(self, text):
        from search_util import matches
        for r in range(self.tb.rowCount()):
            it = self.tb.item(r, 0)
            self.tb.setRowHidden(r, not matches(text, it.text() if it else ""))

    def _open(self, row, _col):
        it = self.tb.item(row, 0)
        path, dk = it.data(Qt.ItemDataRole.UserRole) if it else ("", "")
        if path:
            self.accept()
            self.shell.goto_map(path, dk)
