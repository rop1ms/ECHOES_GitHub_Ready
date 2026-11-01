# dsl_mode.py
"""Темы на EchoScript внутри главного окна.

Тема с флагом "dsl" (встроенная «Loom» и все скрипты пользователя — «◈ Название») включает слой поверх
центральной области окна: живая сцена скрипта + студия (код, блоки, свойства, справка). Студия бывает
панелью справа или отдельным окном. Обычный интерфейс на это время прячется и возвращается как был
при выборе любой другой темы.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QObject, QEvent, QPoint
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QSplitter, QPushButton

import dsl_studio
import dsl_layout as DL
from dsl_runtime import ScriptCanvas, MEDIA

DEFAULT_SCRIPT = "Loom"
USER_PREFIX = "◈ "

BTN_QSS = """
QPushButton { background: rgba(20,18,32,0.72); color: #ece9f6; border: 1px solid rgba(255,255,255,0.10);
              border-radius: 13px; padding: 4px 12px; font: 600 12px 'Segoe UI'; }
QPushButton:hover { background: rgba(60,52,92,0.85); }
QPushButton:checked { background: #ffb347; color: #1a1408; border: none; }
"""


# ── скрипты пользователя в списке тем ──
def theme_name_for(script: str) -> str:
    s = dsl_studio.find_script(script)
    if s is None or s["builtin"] or s["overridden"]:
        return script if script in _themes() else DEFAULT_SCRIPT
    return USER_PREFIX + s["name"]


def _themes():
    from themes import THEMES
    return THEMES


def register_scripts(win=None):
    """THEMES ← «◈ Имя» для каждого скрипта пользователя (цвета берутся из его блока theme { })."""
    THEMES = _themes()
    for k in [k for k, t in THEMES.items() if t.get("dsl_user")]:
        del THEMES[k]
    base = {k: v for k, v in THEMES.get("Loom", {}).items() if k not in ("dsl", "dsl_script")}
    for s in dsl_studio.user_scripts():
        d = dict(base)
        try:
            with open(s["path"], encoding="utf-8") as f:
                th = DL.theme_get(f.read())
        except OSError:
            th = {}
        for k in ("bg", "accent", "accent2", "text", "muted"):
            v = th.get(k)
            if isinstance(v, str) and v.startswith("#") and len(v) in (7, 9):
                d[k] = v[:7]
        d.update({"dsl": True, "dsl_script": s["name"], "dsl_user": True})
        THEMES[USER_PREFIX + s["name"]] = d
    if win is not None and hasattr(win, "_fill_theme_combo") and hasattr(win, "theme_cb"):
        cur = win.settings.get("theme", "")
        win._fill_theme_combo()
        win.theme_cb.blockSignals(True)
        win.theme_cb.setCurrentText(cur)
        win.theme_cb.blockSignals(False)


class _Host(QWidget):
    """Слой режима EchoScript: сцена | студия, и плавающие кнопки."""

    def __init__(self, win, parent, script):
        super().__init__(parent)
        self.win = win
        self.setObjectName("EchoHost")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("#EchoHost { background: #07070b; }")
        self.canvas = ScriptCanvas(win, self)
        self.studio = None
        self.detached = False
        self.split = QSplitter(Qt.Orientation.Horizontal, self)
        self.split.setHandleWidth(4)
        self.split.setStyleSheet("QSplitter::handle { background: #1c1a28; }")
        self.split.addWidget(self.canvas)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.split)

        self.bar = QWidget(self.canvas)
        bl = QHBoxLayout(self.bar)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(6)
        self.b_themes = QPushButton("Темы ▾")
        self.b_themes.setToolTip("Выбрать тему")
        self.b_themes.clicked.connect(self._themes_menu)
        self.b_studio = QPushButton("Изменить тему")
        self.b_studio.setToolTip("Конструктор: добавляйте блоки, двигайте и настраивайте их мышью")
        self.b_studio.clicked.connect(self.toggle_studio)
        for b in (self.b_themes, self.b_studio):
            b.setStyleSheet(BTN_QSS)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            bl.addWidget(b)
        self.bar.adjustSize()
        self.canvas.installEventFilter(self)

        self.script = None
        self.load_script(script, keep_state=False)

    def eventFilter(self, obj, ev):
        if obj is self.canvas and ev.type() == QEvent.Type.Resize:
            self._place_bar()
        return False

    def _place_bar(self):
        self.bar.adjustSize()
        self.bar.move(self.canvas.width() - self.bar.width() - 12, 10)
        self.bar.raise_()

    def load_script(self, name, keep_state=False):
        if dsl_studio.find_script(name) is None:
            name = DEFAULT_SCRIPT
        if name == self.script and self.canvas.scene is not None:
            return
        src = dsl_studio.read_script(name) or ""
        self.script = name
        self.win.settings["dsl_script"] = name
        self.canvas.load_source(src, keep_state=keep_state)
        if self.studio is not None and self.studio.current != name:
            self.studio.refresh(select=name)

    # ── студия ──
    def _ensure_studio(self):
        if self.studio is not None:
            return self.studio
        st = dsl_studio.ScriptStudio(self.canvas, current=self.script, win=self.win)
        st.script_changed.connect(self._studio_picked)
        st.scripts_changed.connect(lambda: register_scripts(self.win))
        st.close_requested.connect(self.hide_studio)
        st.detach_requested.connect(self.toggle_detach)
        st.hide()
        self.studio = st
        return st

    def _studio_picked(self, name):
        if not name:
            return
        self.script = None                                   # загрузить заново, даже если имя то же
        tn = theme_name_for(name)
        if tn in _themes() and self.win.settings.get("theme") != tn:
            self.win._apply_theme(tn)                         # тема в списке и настройки — тоже на этот скрипт
        else:
            self.load_script(name)

    def _dock(self):
        st = self.studio
        self.split.addWidget(st)
        w = max(self.width(), 900)
        self.split.setSizes([int(w * 0.58), int(w * 0.42)])

    def toggle_studio(self):
        first = self.studio is None
        st = self._ensure_studio()
        if st.isVisible():
            self.hide_studio()
            return
        if first and self.win.settings.get("dsl_studio_detached") and not self.detached:
            self.toggle_detach()                              # запомненный режим: отдельное окно
            st.set_edit_mode(True)
            st.tabs.setCurrentWidget(st.blocks_tab)
            return
        if self.detached:
            st.show()
            st.raise_()
            st.activateWindow()
        else:
            self._dock()
            st.show()
        st.set_edit_mode(True)                                # открыли конструктор — сразу можно двигать
        st.tabs.setCurrentWidget(st.blocks_tab)
        self.b_studio.setText("Готово")

    def hide_studio(self):
        st = self.studio
        if st is None:
            return
        st._save(quiet=True)
        st.set_edit_mode(False)                               # закрыли — сцена снова работает как плеер
        st.hide()
        self.b_studio.setText("Изменить тему")

    def toggle_detach(self):
        st = self._ensure_studio()
        if not self.detached:
            geo = st.geometry()
            st.setParent(None)
            st.setWindowFlags(Qt.WindowType.Window)
            st.resize(max(560, geo.width()), max(640, self.height()))
            pos = self.mapToGlobal(QPoint(self.width() - st.width() - 30, 40))
            st.move(pos)
            self.detached = True
            st.b_detach.setToolTip("Вернуть студию в плеер")
            st.show()
            st.raise_()
            st.activateWindow()
        else:
            st.hide()
            st.setWindowFlags(Qt.WindowType.Widget)
            self.detached = False
            st.b_detach.setToolTip("Отдельное окно")
            self._dock()
            st.show()
        self.b_studio.setText("Готово")
        self.win.settings["dsl_studio_detached"] = self.detached
        try:
            import sys
            mod = sys.modules.get(type(self.win).__module__)    # main.py запущен как __main__ — не импортировать заново
            mod.save_json(mod.SETTINGS_FILE, self.win.settings)
        except Exception:                                  # noqa: BLE001
            pass

    def _themes_menu(self):
        win = self.win
        pos = self.b_themes.mapToGlobal(QPoint(0, self.b_themes.height() + 4))
        if hasattr(win, "_winamp_theme_menu"):
            win._winamp_theme_menu(pos)

    def shutdown(self):
        try:
            if self.studio is not None:
                self.studio._save(quiet=True)
                if self.detached:
                    self.studio.hide()
                self.studio.deleteLater()
        except Exception:                                  # noqa: BLE001
            pass
        try:
            self.canvas.timer.stop()
            self.canvas.set_gpu(False)                     # освободить буфер видеокарты
        except Exception:                                  # noqa: BLE001
            pass
        MEDIA.clear()


class _Resizer(QObject):
    def __init__(self, host, parent):
        super().__init__(parent)
        self.host = host

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.Resize:
            self.host.setGeometry(obj.rect())
        return False


def _enter(win, script):
    st = win.__dict__.get("_dsl")
    if st is not None:
        st["host"].load_script(script)
        return
    root = win.centralWidget()
    hidden = []
    # всё, что лежит в центральной области (панели, фоновые слои), прячем — сцена непрозрачная,
    # а спрятанные виджеты не тратят время на перерисовку
    for w in root.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
        if w.isVisible() and not w.isWindow():
            w.hide()
            hidden.append(w)
    viz_timer = None
    try:
        if win.viz.timer.isActive():
            win.viz.timer.stop()
            viz_timer = win.viz.timer
    except Exception:                                      # noqa: BLE001
        pass
    host = _Host(win, root, script)
    host.setGeometry(root.rect())
    rs = _Resizer(host, root)
    root.installEventFilter(rs)
    host.show()
    host.raise_()
    win._dsl = {"host": host, "hidden": hidden, "resizer": rs, "viz_timer": viz_timer}


def _leave(win):
    st = win.__dict__.get("_dsl")
    if st is None:
        return
    win._dsl = None
    host = st["host"]
    host.shutdown()
    try:
        win.centralWidget().removeEventFilter(st["resizer"])
    except RuntimeError:
        pass
    host.hide()
    host.deleteLater()
    for w in st["hidden"]:
        try:
            w.show()
        except RuntimeError:
            pass
    try:
        if st["viz_timer"] is not None:
            st["viz_timer"].start()
    except Exception:                                      # noqa: BLE001
        pass


def install(win):
    """Перехватывает win._apply_theme_data: тема с "dsl" → слой EchoScript, любая другая → обычный вид."""
    if getattr(win, "_dsl_installed", False):
        return
    win._dsl_installed = True
    win._dsl = None
    try:
        register_scripts(win)
    except Exception as e:                                 # noqa: BLE001
        print("[dsl] скрипты:", e)
    orig = win._apply_theme_data

    def apply_theme_data(theme_data, selector_name=None):
        if theme_data.get("dsl"):
            script = theme_data.get("dsl_script") or (selector_name if selector_name and
                                                      dsl_studio.find_script(selector_name) else DEFAULT_SCRIPT)
            base = {k: v for k, v in theme_data.items() if not k.startswith("dsl")}
            orig(base, selector_name)                 # стили окна, заголовок, обычная раскладка под слоем
            _enter(win, script)
            return
        _leave(win)
        orig(theme_data, selector_name)

    win._apply_theme_data = apply_theme_data
    win.export_effects = getattr(win, "export_effects", None) or (lambda tracks: _export(win, tracks))


def _export(win, tracks):
    import dsp_export
    dsp_export.open_dialog(win, tracks)
