# i18n.py
"""
Перевод интерфейса ECHOES (сейчас: русский → английский) — во ВСЕХ темах сразу.

Исходники плеера написаны по-русски, и вместо того чтобы обвешивать сотни строк
вызовами tr(), перевод встроен прямо в Qt: install() подменяет методы, через
которые текст попадает на экран (setText, setToolTip, setWindowTitle,
addItem/addAction, QMessageBox/QInputDialog/QFileDialog, QPainter.drawText,
QFontMetrics…), а тексты из конструкторов виджетов переводятся «обходом» при
показе окна. Словарь — i18n_en.py: точные строки + шаблоны f-строк
(«{0} треков» → «{0} tracks»).

Код плеера при этом продолжает видеть РУССКИЙ текст: text()/currentText()/
itemText() возвращают исходную строку для переведённых виджетов, поэтому
проверки вида label.text().startswith("Анализ звучания") не ломаются.
Пользовательские данные (названия треков, плейлистов) не трогаются: переводится
только то, что есть в словаре.

Первый запуск следует языку системы (RU/EN); смена языка применяется сразу.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

DEFAULT_LANGUAGE = "en"
LANG = DEFAULT_LANGUAGE
LANGUAGES = (("ru", "Русский"), ("en", "English"))

_CYR = re.compile("[А-Яа-яЁё]")
_EXACT: dict = {}
_CASEFOLD: dict = {}
_TEMPLATES: list = []          # [(regex, english_template)]
_TEMPLATE_BUCKETS: dict = {}
_TEMPLATE_ANY: list = []
_CACHE: dict = {}
_CACHE_MAX = 6000
_SRC = "_i18n_src"             # свойство виджета: исходный (русский) текст
_DST = "_i18n_dst"             # … и то, что показали вместо него
_SEEN = "_i18n_seen"
_ITEM_ROLE = 0x0100 + 777      # Qt.UserRole + 777: исходный текст пункта QComboBox
_TAG = re.compile(r"(<[^>]+>)")


def read_language(settings_file) -> str:
    try:
        lang = json.loads(Path(settings_file).read_text("utf-8-sig")).get("language")
        return lang if lang in dict(LANGUAGES) else system_language()
    except Exception:                                    # noqa: BLE001
        return system_language()

def system_language():
    """Use the system UI language for a fresh profile; unsupported languages use English."""
    import sys
    if sys.platform=='win32':
        try:
            import ctypes
            return 'ru' if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3ff == 0x19 else 'en'
        except (AttributeError,OSError):pass
    from PyQt6.QtCore import QLocale
    language=QLocale.system().uiLanguages()
    return 'ru' if language and language[0].split('-')[0].lower()=='ru' else 'en'

def change_language(code,window=None):
    from ui_language import apply
    apply(code,window)


def _load(lang):
    global LANG
    LANG = lang
    _EXACT.clear(); _CASEFOLD.clear(); _TEMPLATES.clear(); _CACHE.clear()
    _TEMPLATE_BUCKETS.clear(); _TEMPLATE_ANY.clear()
    import i18n_en
    _EXACT.update(i18n_en.EXACT)
    templates = dict(i18n_en.TEMPLATES)
    try:                                                 # дополнение словаря (osu!, студия, конструктор…)
        import i18n_en_extra
        for k, v in i18n_en_extra.EXACT.items():
            _EXACT.setdefault(k, v)
        for k, v in i18n_en_extra.TEMPLATES.items():
            templates.setdefault(k, v)
    except Exception as e:                               # noqa: BLE001
        print("[i18n] extra:", e)
    complete = Path(__file__).with_name('assets') / 'i18n_en_complete.json'
    if complete.exists():
        catalog = json.loads(complete.read_text('utf-8'))
        for key, value in catalog.get('exact', {}).items():
            if key not in _EXACT or _CYR.search(_EXACT[key]):
                _EXACT[key] = value
        for key, value in catalog.get('templates', {}).items():
            if key not in templates or _CYR.search(templates[key]):
                templates[key] = value
    _CASEFOLD.update((key.casefold(), value) for key, value in _EXACT.items())
    for ru, en in templates.items():
        parts = re.split(r"(\{\d+\})", ru)
        rx = "".join("(.*?)" if re.fullmatch(r"\{\d+\}", p)
                     else re.escape(p.replace("{{", "{").replace("}}", "}")) for p in parts)
        order = [int(p[1:-1]) for p in parts if re.fullmatch(r"\{\d+\}", p)]
        lit = len(re.sub(r"\{\d+\}", "", ru))
        item = (lit, re.compile(rx, re.S | re.I), en, order)
        _TEMPLATES.append(item)
        if re.match(r'^\{\d+\}', ru):
            _TEMPLATE_ANY.append(item)
        elif ru:
            _TEMPLATE_BUCKETS.setdefault(ru[0].casefold(), []).append(item)
    _TEMPLATES.sort(key=lambda t: -t[0])                 # самые конкретные — первыми
    _TEMPLATE_ANY.sort(key=lambda t: -t[0])
    for key, values in _TEMPLATE_BUCKETS.items():
        _TEMPLATE_BUCKETS[key] = sorted(values + _TEMPLATE_ANY, key=lambda t: -t[0])


def _template(s):
    for _, rx, en, order in _TEMPLATE_BUCKETS.get(s[:1].casefold(), _TEMPLATE_ANY):
        m = rx.fullmatch(s)
        if m:
            vals = [""] * (max(order) + 1 if order else 0)
            for k, g in zip(order, m.groups()):
                vals[k] = tr(g) if _CYR.search(g) else g
            try:
                result = en.format(*vals)
                return result.upper() if s.isupper() else result
            except Exception:                            # noqa: BLE001
                return None
    return None


def _all_or_none(pieces):
    """Перевести каждый кусок; если хоть один русский кусок не перевёлся — None."""
    out = []
    for p in pieces:
        if _CYR.search(p):
            t = _lookup(p)
            if t is None:
                return None
            out.append(t)
        else:
            out.append(p)
    return out


def _lookup(s):
    r = _EXACT.get(s)
    if r is not None:
        return r
    core = s.strip()
    if core != s:
        translated = _lookup(core)
        if translated is not None:
            i = s.index(core)
            return s[:i] + translated + s[i + len(core):]
    if s.isupper():
        translated = _CASEFOLD.get(s.casefold())
        if translated is not None:
            return translated.upper()
    r = _template(s)
    if r is not None:
        return r
    if "\n" in s:
        p = _all_or_none(s.split("\n"))
        if p is not None:
            return "\n".join(p)
    if "<" in s and ">" in s:
        pieces = _TAG.split(s)
        if len([x for x in pieces if x]) > 1:              # «<Название>» целиком не дробим — иначе бесконечная рекурсия
            p = _all_or_none(pieces)
            if p is not None:
                return "".join(p)
    for sep in ("   ", "  ·  ", " · ", " — ", ": "):
        if sep in s:
            p = _all_or_none(s.split(sep))
            if p is not None:
                return sep.join(p)
    numbered = re.match(r'^(\s*\d+[.)]\s*)(.+)$', s, re.S)
    if numbered:
        translated = _lookup(numbered[2])
        if translated is not None:
            return numbered[1] + translated
    icon = re.match(r'^([^\wА-Яа-яЁё]+)([А-Яа-яЁё].*)$', s, re.S)
    if icon:
        translated = _lookup(icon[2])
        if translated is not None:
            return icon[1] + translated
    return None


def tr(s):
    """Перевод строки интерфейса (без перевода — сама строка)."""
    pair=getattr(s,'_echoes_ui_pair',None)
    if pair is not None:return pair[1] if LANG=='en' else pair[0]
    if LANG == "ru" or not isinstance(s, str) or not s or not _CYR.search(s):
        return s
    r = _CACHE.get(s)
    if r is None:
        # UI drawings use isolated codepoints, while the existing catalogue
        # keeps its original icon spellings. Never rewrite arbitrary emoji.
        import ui_symbols
        lookup = ui_symbols.source_text(s)
        r = _lookup(lookup)
        if r is None:
            r = s
        elif lookup != s:
            r = ui_symbols.translated_symbols(s, r)
        if len(_CACHE) > _CACHE_MAX:
            _CACHE.clear()
        _CACHE[s] = r
    return r


# ------------------------------------------------------------------ #
#  Подмена методов Qt                                                 #
# ------------------------------------------------------------------ #

def _remember(obj, src, dst):
    if dst is not src and dst != src:
        try:
            obj.setProperty(_SRC, src)
            obj.setProperty(_DST, dst)
        except Exception:                                # noqa: BLE001
            pass


def _amp(src, t):
    """У кнопок/пунктов меню «&» — мнемоника: «wow & flutter» показался бы как «wow _flutter»."""
    return t.replace("&", "&&") if ("&" in t and "&" not in src) else t


def _wrap_setter(cls, name, mnemonic=False):
    """setText(text) и подобные: перевести, запомнить исходник для геттера."""
    orig = getattr(cls, name)
    from ui_language import NATIVE
    NATIVE[(cls,name)]=orig

    def setter(self, text, *a):
        if isinstance(text, str):
            t = tr(text)
            if mnemonic and t is not text:
                t = _amp(text, t)
            if t is not text:
                _remember(self, text, t)
            from ui_language import record
            record(self,name,text,orig,a,t)
            return orig(self, t, *a)
        return orig(self, text, *a)
    setattr(cls, name, setter)


def _wrap_getter(cls, name):
    orig = getattr(cls, name)

    def getter(self, *a):
        t = orig(self, *a)
        from ui_language import source_text
        source=source_text(self,'set'+name[0].upper()+name[1:],t)
        if source is not None:return source
        try:
            if t and self.property(_DST) == t:
                src = self.property(_SRC)
                if src is not None:
                    return src
        except Exception:                                # noqa: BLE001
            pass
        return t
    setattr(cls, name, getter)


def _wrap_strargs(cls, name, static=False):
    """Перевести все строковые аргументы (диалоги, рисование текста, метрики)."""
    orig = getattr(cls, name)

    def f(*a, **kw):
        a = tuple(tr(x) if isinstance(x, str) else x for x in a)
        kw = {k: (tr(v) if isinstance(v, str) else v) for k, v in kw.items()}
        return orig(*a, **kw)
    setattr(cls, name, staticmethod(f) if static else f)


def _wrap_args_at(cls, name, idx, kws=()):
    """Статический метод: перевести только аргументы с номерами idx (заголовок, подпись…)."""
    orig = getattr(cls, name)

    def f(*a, **kw):
        a = tuple(tr(x) if (i in idx and isinstance(x, str)) else x for i, x in enumerate(a))
        kw = {k: (tr(v) if (k in kws and isinstance(v, str)) else v) for k, v in kw.items()}
        return orig(*a, **kw)
    setattr(cls, name, staticmethod(f))


_RAW = {}


def display_text(widget):
    """Text actually shown by Qt; public text() retains the source for logic.

    Custom style/pixmap renderers must use this to avoid painting Russian over
    an English control whose source-preserving getter was intentionally wrapped.
    """
    from PyQt6.QtWidgets import QAbstractButton, QLabel
    key = "button" if isinstance(widget, QAbstractButton) else "label" if isinstance(widget, QLabel) else None
    native = _RAW.get(key)
    return native(widget) if native is not None else widget.text()


def _translate_widget(w):
    from PyQt6.QtWidgets import (QAbstractButton, QComboBox, QGroupBox, QLabel, QLineEdit,
                                 QPlainTextEdit, QTabWidget, QTextEdit)
    try:
        if w.property(_SEEN):
            return
        w.setProperty(_SEEN, True)
        if isinstance(w, (QLabel, QAbstractButton)):
            raw = _RAW["label" if isinstance(w, QLabel) else "button"](w)
            if raw and _CYR.search(raw) and 'setText' not in getattr(w,'_echoes_lang_fields',{}):
                w.setText(raw.replace("&&", "&") if isinstance(w, QAbstractButton) else raw)
        if isinstance(w, (QLineEdit, QTextEdit, QPlainTextEdit)):
            p = w.placeholderText()
            if p and _CYR.search(p) and 'setPlaceholderText' not in getattr(w,'_echoes_lang_fields',{}):
                w.setPlaceholderText(p)
        if isinstance(w, QGroupBox) and _CYR.search(w.title() or "") and 'setTitle' not in getattr(w,'_echoes_lang_fields',{}):
            w.setTitle(w.title())
        if isinstance(w, QTabWidget):
            for i in range(w.count()):
                if w.widget(i) not in getattr(w,'_echoes_tab_sources',{}) and _CYR.search(w.tabText(i) or ""):
                    w.setTabText(i, w.tabText(i))
        if isinstance(w, QComboBox):
            for i in range(w.count()):
                if w.itemData(i, _ITEM_ROLE) is None:
                    t = w.itemText(i)
                    if t and _CYR.search(t) and tr(t) != t:
                        w.setItemData(i, t, _ITEM_ROLE)
                        _orig_setItemText(w, i, tr(t))
        tip = w.toolTip()
        if tip and _CYR.search(tip) and 'setToolTip' not in getattr(w,'_echoes_lang_fields',{}):
            w.setToolTip(tip)
        title = w.windowTitle()
        if title and _CYR.search(title) and 'setWindowTitle' not in getattr(w,'_echoes_lang_fields',{}):
            w.setWindowTitle(title)
    except RuntimeError:                                 # виджет уже удалён
        pass


def sweep(root=None):
    """Перевести тексты, заданные в конструкторах (обход дерева виджетов)."""
    from PyQt6.QtWidgets import QApplication, QWidget
    try:
        widgets = ([root] + root.findChildren(QWidget)) if root is not None else QApplication.allWidgets()
    except RuntimeError:
        return
    for w in widgets:
        _translate_widget(w)


_orig_setItemText = None
_INSTALLED = False


def install(lang: str):
    """Load the catalogue and install source-preserving UI hooks once."""
    global _orig_setItemText, _orig_action_setText, _INSTALLED
    _load(lang)
    if _INSTALLED:
        return
    _INSTALLED = True
    from PyQt6 import QtWidgets as W, QtGui as G
    from PyQt6.QtCore import QTimer

    _RAW["label"], _RAW["button"] = W.QLabel.text, W.QAbstractButton.text
    _wrap_setter(W.QLabel, "setText")
    _wrap_getter(W.QLabel, "text")
    _wrap_setter(W.QAbstractButton, "setText", mnemonic=True)
    _wrap_getter(W.QAbstractButton, "text")
    _orig_action_setText = G.QAction.setText
    _wrap_setter(G.QAction, "setText", mnemonic=True)
    _wrap_getter(G.QAction, "text")
    _wrap_setter(G.QAction, "setToolTip")
    _wrap_setter(W.QWidget, "setToolTip")
    _wrap_setter(W.QWidget, "setWindowTitle")
    _wrap_setter(W.QWidget, "setStatusTip")
    for cls in (W.QLineEdit, W.QTextEdit, W.QPlainTextEdit):
        _wrap_setter(cls, "setPlaceholderText")
    _wrap_setter(W.QTextEdit, "setHtml")
    _wrap_setter(W.QGroupBox, "setTitle")
    for cls in (W.QSpinBox, W.QDoubleSpinBox):
        _wrap_setter(cls, "setSuffix")
        _wrap_setter(cls, "setPrefix")
    _wrap_setter(W.QAbstractSpinBox, "setSpecialValueText")

    # элементы списков: переводим только «служебные» (без UserRole) — это заглушки
    # вроде «Плейлистов пока нет»; настоящие плейлисты/треки несут данные и не трогаются
    from PyQt6.QtCore import Qt as _Qt
    L = W.QListWidget
    o_l_add, o_l_ins = L.addItem, L.insertItem

    def _fix_item(it):
        if isinstance(it, W.QListWidgetItem) and it.data(_Qt.ItemDataRole.UserRole) is None:
            t = it.text()
            if t in _EXACT:
                it._i18n_list_src=t;it.setText(tr(t))
        return it
    L.addItem = lambda self, it: o_l_add(self, _fix_item(it))
    L.insertItem = lambda self, row, it: o_l_ins(self, row, _fix_item(it))

    # элементы деревьев и таблиц (браузер студии, «Попытки по картам»…) и заголовки колонок:
    # класс подменяется до импорта модулей интерфейса; text() возвращает исходный русский текст
    class _TreeItem(W.QTreeWidgetItem):
        def __init__(self, *a):
            a = list(a)
            src = None
            for i, v in enumerate(a):
                if isinstance(v, (list, tuple)) and v and all(isinstance(x, str) for x in v):
                    src = list(v)
                    a[i] = [tr(x) for x in v]
            super().__init__(*a)
            if src is not None:
                self._i18n = {c: s for c, s in enumerate(src) if _CYR.search(s) or hasattr(s,'_echoes_ui_pair')}

        def setText(self, col, s):
            t = tr(s)
            if t != s or _CYR.search(s) or hasattr(s,'_echoes_ui_pair'):
                self.__dict__.setdefault("_i18n", {})[col] = s
            elif "_i18n" in self.__dict__:
                self._i18n.pop(col, None)
            super().setText(col, t)

        def text(self, col):
            d = self.__dict__.get("_i18n")
            return d[col] if d and col in d else super().text(col)

    class _TableItem(W.QTableWidgetItem):
        def __init__(self, *a):
            a = list(a)
            src = None
            for i, v in enumerate(a):
                if isinstance(v, str):
                    src = v
                    a[i] = tr(v)
                    break
            super().__init__(*a)
            if src is not None and (tr(src) != src or _CYR.search(src) or hasattr(src,'_echoes_ui_pair')):
                self._i18n = src

        def setText(self, s):
            t = tr(s)
            self.__dict__["_i18n"] = s if t != s or _CYR.search(s) or hasattr(s,'_echoes_ui_pair') else None
            super().setText(t)

        def text(self):
            s = self.__dict__.get("_i18n")
            return s if s else super().text()

    W.QTreeWidgetItem = _TreeItem
    W.QTableWidgetItem = _TableItem
    for cls, n in ((W.QTreeWidget, "setHeaderLabels"), (W.QTableWidget, "setHorizontalHeaderLabels"),
                   (W.QTableWidget, "setVerticalHeaderLabels")):
        setattr(cls, n, (lambda o: lambda self, labels: o(self, [tr(x) for x in labels]))(getattr(cls, n)))
    W.QTreeWidget.setHeaderLabel = (lambda o: lambda self, s: o(self, tr(s)))(W.QTreeWidget.setHeaderLabel)
    _wrap_setter(W.QMenu, "setTitle")
    for cls in (W.QMessageBox,):
        for n in ("setText", "setInformativeText", "setDetailedText", "setWindowTitle"):
            _wrap_setter(cls, n)
        for n in ("information", "warning", "question", "critical", "about"):
            _wrap_strargs(cls, n, static=True)
    for n in ("getText", "getInt", "getDouble", "getMultiLineText", "getItem"):
        _wrap_args_at(W.QInputDialog, n, (1, 2), ("title", "label"))
    for n in ("setLabelText", "setOkButtonText", "setCancelButtonText"):
        _wrap_setter(W.QInputDialog, n)
    for n in ("getOpenFileName", "getOpenFileNames", "getSaveFileName", "getExistingDirectory"):
        _wrap_args_at(W.QFileDialog, n, (1, 3), ("caption", "filter"))
    _wrap_strargs(G.QPainter, "drawText")
    for n in ("elidedText", "horizontalAdvance", "boundingRect", "size", "tightBoundingRect"):
        if hasattr(G.QFontMetrics, n):
            _wrap_strargs(G.QFontMetrics, n)
        if hasattr(G.QFontMetricsF, n):
            _wrap_strargs(G.QFontMetricsF, n)

    # вкладки
    orig_addTab, orig_insertTab, orig_setTabText = W.QTabWidget.addTab, W.QTabWidget.insertTab, W.QTabWidget.setTabText
    W.QTabWidget.addTab = lambda self, w, *a: orig_addTab(self, w, *[tr(x) if isinstance(x, str) else x for x in a])
    W.QTabWidget.insertTab = lambda self, i, w, *a: orig_insertTab(self, i, w, *[tr(x) if isinstance(x, str) else x for x in a])
    W.QTabWidget.setTabText = lambda self, i, t: orig_setTabText(self, i, tr(t))

    # QComboBox: на экране — перевод, коду — исходник (роль _ITEM_ROLE)
    C = W.QComboBox
    o_add, o_addItems, o_insert, o_setItemText = C.addItem, C.addItems, C.insertItem, C.setItemText
    o_itemText, o_curText, o_findText, o_setCur = C.itemText, C.currentText, C.findText, C.setCurrentText
    _orig_setItemText = o_setItemText

    def _split_text(a):
        """Аргументы addItem/insertItem: (text, data) или (icon, text, data) → индекс текста."""
        return 0 if a and isinstance(a[0], str) else (1 if len(a) > 1 and isinstance(a[1], str) else None)

    def addItem(self, *a):
        i = _split_text(a)
        if i is None:
            return o_add(self, *a)
        src = a[i]
        a = list(a); a[i] = tr(src)
        o_add(self, *a)
        if a[i] != src:
            self.setItemData(self.count() - 1, src, _ITEM_ROLE)

    def addItems(self, items):
        for t in items:
            addItem(self, t)

    def insertItem(self, index, *a):
        i = _split_text(a)
        if i is None:
            return o_insert(self, index, *a)
        src = a[i]
        a = list(a); a[i] = tr(src)
        o_insert(self, index, *a)
        if a[i] != src:
            idx = index if 0 <= index <= self.count() - 1 else self.count() - 1
            self.setItemData(idx, src, _ITEM_ROLE)

    def setItemText(self, index, text):
        t = tr(text)
        o_setItemText(self, index, t)
        self.setItemData(index, text if t != text else None, _ITEM_ROLE)

    def itemText(self, index):
        src = self.itemData(index, _ITEM_ROLE)
        return src if isinstance(src, str) else o_itemText(self, index)

    def currentText(self):
        i = self.currentIndex()
        if i >= 0:
            src = self.itemData(i, _ITEM_ROLE)
            if isinstance(src, str) and o_curText(self) == o_itemText(self, i):
                return src
        return o_curText(self)

    def findText(self, text, *a):
        r = o_findText(self, text, *a)
        return r if r >= 0 or not isinstance(text, str) else o_findText(self, tr(text), *a)

    def setCurrentText(self, text):
        i = findText(self, text)
        if i >= 0:
            self.setCurrentIndex(i)
        else:
            o_setCur(self, tr(text))

    C.addItem, C.addItems, C.insertItem, C.setItemText = addItem, addItems, insertItem, setItemText
    C.itemText, C.currentText, C.findText, C.setCurrentText = itemText, currentText, findText, setCurrentText

    # QMenu: пункты, разделы, подменю; перед показом — перевести всё, что пришло из конструкторов
    M = W.QMenu
    o_addAction, o_addSection, o_addMenu = M.addAction, M.addSection, M.addMenu

    def addAction(self, *a):
        a = list(a)
        src = None
        for i, x in enumerate(a[:2]):
            if isinstance(x, str):
                src = x
                a[i] = tr(x)
                break
        act = o_addAction(self, *a)
        if src is not None and act is not None:
            t = _amp(src, tr(src))
            if t != act.text():
                _orig_action_setText(act, t)
            _remember(act, src, t)
        return act

    M.addAction = addAction
    M.addSection = lambda self, *a: o_addSection(self, *[tr(x) if isinstance(x, str) else x for x in a])
    M.addMenu = lambda self, *a: o_addMenu(self, *[tr(x) if isinstance(x, str) else x for x in a])

    def _menu_prepare(menu):
        for act in menu.actions():
            t = act.text()
            if t and _CYR.search(t):
                act.setText(t)
            sub = act.menu()
            if sub is not None:
                _menu_prepare(sub)

    for n in ("exec", "popup"):
        o = getattr(M, n)

        def shown(self, *a, _o=o):
            _menu_prepare(self)
            return _o(self, *a)
        setattr(M, n, shown)

    # show(): перевести тексты из конструкторов у всего поддерева
    o_show = W.QWidget.show

    def show(self):
        try:
            if not self.property(_SEEN):
                sweep(self)
        except RuntimeError:
            pass
        return o_show(self)
    W.QWidget.show = show

    # виджет попал в раскладку/контейнер — перевести сразу (Qt покажет его сам, без show())
    def _hook_add(cls, name, widx):
        o = getattr(cls, name)

        def f(self, *a, _o=o):
            w = a[widx] if len(a) > widx else None
            if isinstance(w, W.QWidget):
                try:
                    if not w.property(_SEEN):
                        sweep(w)
                except RuntimeError:
                    pass
            return _o(self, *a)
        setattr(cls, name, f)

    for cls, name, widx in ((W.QBoxLayout, "addWidget", 0), (W.QBoxLayout, "insertWidget", 1),
                            (W.QGridLayout, "addWidget", 0), (W.QLayout, "addWidget", 0),
                            (W.QStackedWidget, "addWidget", 0), (W.QStackedWidget, "insertWidget", 1),
                            (W.QStackedLayout, "addWidget", 0), (W.QScrollArea, "setWidget", 0),
                            (W.QSplitter, "addWidget", 0), (W.QSplitter, "insertWidget", 1)):
        _hook_add(cls, name, widx)

    for cls in (W.QDialog,):
        o_exec = cls.exec

        def exec_(self, *a, _o=o_exec):
            sweep(self)
            return _o(self, *a)
        cls.exec = exec_

    # Translate native-created children when Qt polishes/shows them. Polling
    # every widget every second made large network pages stutter while scrolling.
    def _late():
        app = W.QApplication.instance()
        if app is None:
            return
        from PyQt6.QtCore import QEvent, QObject

        class _NewWidgetTranslator(QObject):
            def eventFilter(self, obj, event):
                if event.type() in (QEvent.Type.Polish, QEvent.Type.Show) and isinstance(obj, W.QWidget):
                    _translate_widget(obj)
                return False

        guard = _NewWidgetTranslator(app)
        app.installEventFilter(guard)
        app._i18n_guard = guard
        QTimer.singleShot(0, lambda: sweep())
    _late_holder.append(_late)
    import ui_language
    ui_language.install()


_late_holder: list = []
_orig_action_setText = None


def start(app=None):
    """Register UI drawings and translate new widgets after QApplication exists."""
    import ui_symbols
    ui_symbols.install(app)
    for f in _late_holder:
        f()
    _late_holder.clear()
