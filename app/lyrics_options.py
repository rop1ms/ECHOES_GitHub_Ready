"""Lyrics display preferences and asynchronous translation, shared by every theme."""
import json
import threading
import urllib.parse
import urllib.request
from PyQt6.QtCore import QObject, pyqtSignal, Qt
from PyQt6.QtWidgets import (QDialog, QFormLayout, QComboBox, QDoubleSpinBox,
    QCheckBox, QPushButton, QLabel, QLineEdit, QMessageBox, QVBoxLayout, QWidget)
from lyrics_view import parse_lrc, split_plain


LANGUAGES = [('ru', 'Русский'), ('en', 'Английский'), ('uk', 'Украинский'), ('be', 'Белорусский'),
             ('kk', 'Казахский'), ('uz', 'Узбекский'), ('hy', 'Армянский'), ('ka', 'Грузинский'),
             ('az', 'Азербайджанский'), ('de', 'Немецкий'), ('fr', 'Французский'), ('es', 'Испанский'),
             ('it', 'Итальянский'), ('pt', 'Португальский'), ('pl', 'Польский'), ('cs', 'Чешский'),
             ('tr', 'Турецкий'), ('ja', 'Японский'), ('ko', 'Корейский'), ('zh-CN', 'Китайский'),
             ('ar', 'Арабский'), ('hi', 'Хинди'), ('id', 'Индонезийский'), ('vi', 'Вьетнамский'),
             ('th', 'Тайский'), ('nl', 'Нидерландский'), ('sv', 'Шведский'), ('fi', 'Финский'),
             ('el', 'Греческий'), ('he', 'Иврит')]


def translate_lines(lines, target, cancelled=None):
    result, cache = [], {}
    # Keep the original line boundaries and timestamps, including instrumental gaps.
    for line in lines:
        if cancelled and cancelled():
            return None
        if line in cache:
            result.append(cache[line])
            continue
        if not line.strip():
            result.append('')
            continue
        query = urllib.parse.urlencode({'client': 'gtx', 'sl': 'auto', 'tl': target,
                                        'dt': 't', 'q': line})
        request = urllib.request.Request('https://translate.googleapis.com/translate_a/single?' + query,
                                         headers={'User-Agent': 'ECHOES/1.0'})
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.load(response)
        translated = ''.join(part[0] for part in data[0] if part[0])
        cache[line] = translated
        result.append(translated)
    return result


class LyricsOptions(QObject):
    ready = pyqtSignal(int, object, str)
    status_changed = pyqtSignal(str)

    def __init__(self, win):
        super().__init__(win)
        self.win, self.view = win, win.lyrics_view
        self.source, self.translated, self.generation = '', None, 0
        self.cache = {}
        self.display_lines = []
        self.revision = 0
        self.data_revision = 0
        self.status_text = ""
        self.ready.connect(self._ready)
        self.original_set = self.view.set_lyrics
        self.view.set_lyrics = self.set_lyrics
        self.original_custom = self.view.set_custom
        def custom(*args, **kwargs):
            self.original_custom(*args, **kwargs)
            self.apply_style()
        self.view.set_custom = custom
        self.view._text_panel.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view._text_panel.customContextMenuRequested.connect(lambda _: self.open())
        self.view._text_panel.setToolTip('Правый клик — перевод, оформление и прокрутка текста')

    def set_status(self, message):
        self.status_text = str(message)
        self.status_changed.emit(self.status_text)

    def set_lyrics(self, text):
        self.data_revision += 1
        self.generation += 1
        self.source, self.translated = text, None
        self.set_status('')
        self.original_set(text)
        self.display_lines = self.view._lines
        self.apply_style()

    def apply_style(self):
        panel = self.view._text_panel
        style = dict(panel._st)
        style.update(self.win.settings.get('lyrics_display', {}))
        panel.set_style(style, panel._st_family)
        self.revision += 1
        ya = getattr(self.win, '_ya', None)
        if ya is not None and hasattr(ya, 'l_panel'):
            ya.l_panel.set_style(style, panel._st_family)
            if hasattr(ya, '_lyr_bg'):
                ya._lyr_bg = None
                ya.pages['lyrics'].update()
                ya.l_panel.update()

    def render(self):
        self.data_revision += 1
        panel = self.view._text_panel
        position = panel._pos
        self.original_set(self.source)
        self.display_lines = self.view._lines
        mode = self.win.settings.get('lyrics_translation_mode', 'below')
        panel._translation_below = mode == 'below' and self.translated is not None
        if self.translated is not None and mode != 'off':
            lines = [dict(line) for line in self.view._lines]
            for line, translated in zip(lines, self.translated):
                line['text'] = translated if mode == 'replace' else line['text'] + ('\n' + translated if translated else '')
                line['words'] = None
            panel.load(lines, self.view._has_timings)
            self.display_lines = lines
        panel.set_pos_ms(position[0])
        self.apply_style()

    def _ready(self, generation, translated, error):
        if generation != self.generation:
            return
        if error:
            self.set_status('Ошибка перевода')
            QMessageBox.warning(self.win, 'Перевод текста', 'Не удалось получить перевод.\n' + error)
            return
        self.translated = translated
        self.set_status('Перевод готов')
        self.cache[(self.source, self.win.settings.get('lyrics_translation_language', 'ru'))] = translated
        while len(self.cache) > 64:
            self.cache.pop(next(iter(self.cache)))
        self.render()

    def translate(self):
        self.generation += 1
        generation = self.generation
        self.set_status('Перевод… Можно продолжать слушать музыку.')
        source = self.source
        target = self.win.settings.get('lyrics_translation_language', 'ru')
        cached = self.cache.get((source, target))
        if cached is not None:
            self._ready(generation, cached, '')
            return
        lines, timed = parse_lrc(source)
        if not timed:
            lines = split_plain(source)
        texts = [line['text'] for line in lines]
        def run():
            try:
                result, error = translate_lines(texts, target, lambda: generation != self.generation), ''
            except Exception as exc:
                result, error = None, str(exc)
            try:
                self.ready.emit(generation, result, error)
            except RuntimeError:
                pass
        threading.Thread(target=run, daemon=True, name='lyrics-translation').start()

    def open(self):
        dlg = QDialog(self.win)
        dlg.finished.connect(dlg.deleteLater)
        dlg.setWindowTitle('Текст: перевод и оформление')
        QVBoxLayout(dlg).addWidget(self.build(dlg))
        dlg.exec()

    def build(self, dlg=None) -> QWidget:
        """Форма «перевод и оформление текста»: в окне (open) или прямо в Настройках (dlg=None — без «Готово»)."""
        box = QWidget()
        form = QFormLayout(box)
        mode = QComboBox()
        for title, value in [('Под оригиналом', 'below'), ('Вместо оригинала', 'replace'), ('Оригинал', 'off')]:
            mode.addItem(title, value)
        mode.setCurrentIndex(max(0, mode.findData(self.win.settings.get('lyrics_translation_mode', 'below'))))
        target = QComboBox()                                    # язык перевода — списком, без кодов
        current = self.win.settings.get('lyrics_translation_language', 'ru')
        for code, title in LANGUAGES:
            target.addItem(title, code)
        if target.findData(current) < 0:
            target.addItem(current, current)
        target.setCurrentIndex(target.findData(current))
        form.addRow('Перевод', mode)
        form.addRow('Язык перевода', target)
        note = QLabel('«Перевести» отправляет текст песни в Google Translate. Требуется интернет.')
        note.setWordWrap(True)
        form.addRow(note)
        status = QLabel(self.status_text)
        status.setWordWrap(True)
        self.status_changed.connect(status.setText)
        form.addRow(status)
        style = self.win.settings.setdefault('lyrics_display', {})
        controls = {}
        for key, title, lo, hi, default in [('size', 'Шрифт, px (0 — авто)', 0, 120, 0),
                ('line_gap', 'Между строками', .2, 4, 1), ('scroll_speed', 'Скорость прокрутки', 1, 20, 7),
                ('translation_scale', 'Размер перевода относительно оригинала', .4, 1.5, .75)]:
            spin = QDoubleSpinBox()
            spin.setRange(lo, hi)
            spin.setValue(style.get(key, default))
            controls[key] = spin
            form.addRow(title, spin)
        manual = QCheckBox('Только ручная прокрутка')
        manual.setChecked(style.get('manual_scroll', False))
        form.addRow(manual)
        style.setdefault('liquid_glass', True)
        glass = QCheckBox('Liquid Glass — размытая обложка и стекло на всём фоне')
        glass.setChecked(style.get('liquid_glass', True))
        form.addRow(glass)
        liquid = QCheckBox('Жидкий текст — строка заливается белым по буквам, буквы подрастают волной')
        liquid.setChecked(self.win.settings.get('lyrics_liquid', True))
        form.addRow(liquid)
        fast = QCheckBox('Быстрая ИИ-синхронизация — base, без отделения вокала')
        fast.setChecked(self.win.settings.get('lyrics_sync_fast', False))
        fast.setToolTip('Быстрее и легче для процессора; на сложном вокале обычный режим small может быть точнее.')
        form.addRow(fast)
        def apply():
            style.update({key: spin.value() for key, spin in controls.items()})
            style['manual_scroll'] = manual.isChecked()
            style['liquid_glass'] = glass.isChecked()
            self.win.settings['lyrics_liquid'] = liquid.isChecked()
            ya = getattr(self.win, '_ya', None)
            for sh in (ya, getattr(getattr(self.win, '_hub', None), 'shell', None)):
                panel = getattr(sh, 'l_panel', None)
                if panel is not None:
                    panel.liquid = liquid.isChecked()
                    panel._layout_w = -1
                    panel.update()
            self.win.settings['lyrics_sync_fast'] = fast.isChecked()
            self.win.settings['lyrics_translation_mode'] = mode.currentData()
            language = target.currentData() or 'ru'
            if language != self.win.settings.get('lyrics_translation_language', 'ru'):
                self.generation += 1
                self.translated = None
            self.win.settings['lyrics_translation_language'] = language
            from player_prefs import _save
            _save(self.win)
            self.render()
        save = QPushButton('Применить')
        save.clicked.connect(apply)
        form.addRow(save)
        translate = QPushButton('Перевести текущий текст')
        translate.clicked.connect(lambda: (apply(), self.translate()))
        form.addRow(translate)
        if dlg is not None:
            close = QPushButton('Готово')
            close.clicked.connect(lambda: (apply(), dlg.accept()))
            form.addRow(close)
        return box
