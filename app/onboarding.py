"""Первый запуск для обычного человека: три коротких шага прямо в окне (не отдельное окно).

  1. Где ваша музыка — папка, файлы или «пропустить» (можно слушать онлайн без скачивания);
  2. Перенести плейлисты — ссылка со Spotify, Яндекс Музыки, VK, YouTube Music, SoundCloud, Deezer, Apple Music
     (открывает тот самый раздел Настроек);
  3. Как будет выглядеть — пара цветовых тем одним щелчком.

Показывается один раз и только новичку: коллекция пустая и мастер ещё не проходили. У тех, у кого музыка уже
есть, он просто отмечается пройденным.
"""
from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

THEMES = ["Echoes Classic", "Midnight", "Sakura", "Зелёный (как Spotify)", "Неон", "Океан", "Закат", "Мята"]


def needed(win) -> bool:
    s = win.settings
    if s.get("onboarded"):
        return False
    if getattr(win, "library", None):                     # музыка уже есть — мастер не нужен
        s["onboarded"] = True
        return False
    return True


def maybe_show(win):
    if needed(win):
        Onboarding(win).show()


class Onboarding(QWidget):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.step = 0
        self.theme = None
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setGeometry(win.rect())
        win.installEventFilter(self)
        self.card = QFrame(self)
        self.card.setObjectName("OnbCard")
        self.card.setStyleSheet(
            "QFrame#OnbCard{background:#141418;border:1px solid rgba(255,255,255,0.10);border-radius:22px;}"
            "QLabel{background:transparent;color:#f2f2f2;} QLabel#H{font-size:26px;font-weight:900;}"
            "QLabel#S{font-size:13px;color:#a6a6ad;} QLabel#Step{font-size:11px;color:#ffdb1a;font-weight:900;"
            "letter-spacing:2px;}"
            "QPushButton{background:rgba(255,255,255,0.08);color:#fff;border:none;border-radius:12px;"
            "padding:11px 16px;font-weight:800;font-size:13px;} QPushButton:hover{background:rgba(255,255,255,0.15);}"
            "QPushButton#Go{background:#ffdb1a;color:#000;} QPushButton#Link{background:transparent;color:#9a9aa2;"
            "font-weight:700;} QPushButton:checked{border:2px solid #ffdb1a;}")
        self.card.setFixedWidth(560)
        self.v = QVBoxLayout(self.card)
        self.v.setContentsMargins(30, 26, 30, 24)
        self.v.setSpacing(12)
        self._render()

    # ── шаги ──
    def _clear(self):
        while self.v.count():
            it = self.v.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()                                   # сразу с экрана (удалится чуть позже)
                w.setProperty("onb_old", True)
                w.deleteLater()
            elif it.layout() is not None:
                self._drop(it.layout())

    def _drop(self, lay):
        while lay.count():
            it = lay.takeAt(0)
            if it.widget() is not None:
                it.widget().hide()
                it.widget().setProperty("onb_old", True)
                it.widget().deleteLater()
            elif it.layout() is not None:
                self._drop(it.layout())

    def _head(self, step, title, sub):
        s = QLabel(f"ШАГ {step} ИЗ 3")
        s.setObjectName("Step")
        h = QLabel(title)
        h.setObjectName("H")
        h.setWordWrap(True)
        d = QLabel(sub)
        d.setObjectName("S")
        d.setWordWrap(True)
        for w in (s, h, d):
            self.v.addWidget(w)
        self.v.addSpacing(6)

    def _button(self, text, fn, go=False):
        b = QPushButton(text)
        if go:
            b.setObjectName("Go")
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)          # случайный Enter/пробел при запуске не «нажимает»
        b.setAutoDefault(False)
        b.clicked.connect(lambda _=False: fn())
        return b

    def _footer(self, next_text="Дальше", back=True):
        row = QHBoxLayout()
        if back and self.step > 0:
            b = self._button("Назад", lambda: self._go(self.step - 1))
            b.setObjectName("Link")
            row.addWidget(b)
        row.addStretch(1)
        skip = self._button("Пропустить всё", lambda: self.finish(user=True))
        skip.setObjectName("Link")
        row.addWidget(skip)
        row.addWidget(self._button(next_text, lambda: self._go(self.step + 1), go=True))
        self.v.addSpacing(8)
        self.v.addLayout(row)

    def _render(self):
        self._clear()
        w = self.win
        if self.step == 0:
            self._head(1, "Добро пожаловать в ECHOES 👋",
                       "Где лежит ваша музыка? ECHOES найдёт треки, обложки и тексты сам. Нет файлов — не беда: "
                       "можно слушать онлайн без скачивания.")
            row = QHBoxLayout()
            row.addWidget(self._button("Выбрать папку с музыкой", lambda: self._add(w._add_folder), go=True))
            row.addWidget(self._button("Добавить файлы", lambda: self._add(w._add_files)))
            self.v.addLayout(row)
            self.count = QLabel("")
            self.count.setObjectName("S")
            self.v.addWidget(self.count)
            self._footer()
        elif self.step == 1:
            self._head(2, "Перенесите свои плейлисты",
                       "Вставьте ссылку на плейлист из Spotify, Яндекс Музыки, VK, YouTube Music, SoundCloud, "
                       "Deezer или Apple Music — ECHOES соберёт его у себя.")
            self.v.addWidget(self._button("Перенести плейлист по ссылке", self._transfer, go=True))
            self._footer()
        else:
            self._head(3, "Как будет выглядеть ECHOES",
                       "Выберите цвет — всё остальное можно настроить потом в Настройки → Оформление.")
            g = QGridLayout()
            g.setSpacing(8)
            import ya_skin
            self.theme_btns = []
            for i, name in enumerate(THEMES):
                acc = (ya_skin.PRESETS.get(name) or {}).get("accent") or "#ffdb1a"
                b = QPushButton(name)
                b.setCheckable(True)
                b.setStyleSheet(f"QPushButton{{border-left:6px solid {acc};}}")
                b.clicked.connect(lambda _=False, n=name: self._pick_theme(n))
                g.addWidget(b, i // 2, i % 2)
                self.theme_btns.append(b)
            self.v.addLayout(g)
            self._footer("Готово")
        for w in self.card.findChildren(QWidget):          # новые элементы шага — сразу видны (старые спрятаны)
            if not w.property("onb_old"):
                w.show()
        self.card.adjustSize()
        self._place()
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, lambda: (self.card.adjustSize(), self._place()) if not self.isHidden() else None)

    def _go(self, step):
        if step >= 3:
            self.finish(user=True)
            return
        self.step = max(0, step)
        self._render()

    def _add(self, fn):
        try:
            fn()
        except Exception as e:                             # noqa: BLE001
            print("[onboarding]", e)
        n = len(getattr(self.win, "library", []) or [])
        try:
            self.count.setText(f"В коллекции треков: {n}" if n else "Пока пусто — можно добавить позже")
        except RuntimeError:
            pass

    def _transfer(self):
        """Открыть «Перенести плейлист с площадки» в Настройках (мастер закрывается)."""
        self.finish(user=True)
        sh = getattr(self.win, "_ya", None)
        if sh is None:
            return
        try:
            sh.show_page("settings")
            hub = sh.settings_hub
            hub.select("library")
            import settings_inline as si
            for s in hub.findChildren(si.Section):
                t = s.findChild(QLabel, "InlineTitle")
                if t is not None and t.text().startswith("Перенести плейлист"):
                    s.set_open(True)
                    break
        except Exception as e:                             # noqa: BLE001
            print("[onboarding] transfer:", e)

    def _pick_theme(self, name):
        self.theme = name
        for b in self.theme_btns:
            b.setChecked(b.text() == name)

    def finish(self, user=False):
        """user — человек сам прошёл/пропустил мастер; иначе (закрытие окна и т. п.) он покажется снова."""
        w = self.win
        if user:
            w.settings["onboarded"] = True
        theme = self.theme
        try:
            w.removeEventFilter(self)
        except RuntimeError:
            pass
        self.hide()
        self.deleteLater()
        if theme:
            import ya_skin
            sk = dict(ya_skin.normalize(w.settings.get("ya_skin")))
            sk.update(ya_skin.PRESETS.get(theme) or {})
            w.settings["ya_skin"] = sk
            try:
                ya_skin.reskin(w)
            except Exception as e:                         # noqa: BLE001
                print("[onboarding] theme:", e)
        try:
            w.save_settings()
        except Exception:                                  # noqa: BLE001
            pass

    # ── вид ──
    def _place(self):
        self.setGeometry(self.win.rect())
        self.card.move((self.width() - self.card.width()) // 2, max(20, (self.height() - self.card.height()) // 2))
        self.raise_()

    def eventFilter(self, obj, e):
        if obj is self.win and e.type() == QEvent.Type.Resize:
            self._place()
        return False

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 170))
        p.end()

    def mousePressEvent(self, e):
        e.accept()                                         # клики мимо карточки — не в окно под слоем
