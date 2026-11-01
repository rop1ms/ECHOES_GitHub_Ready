"""Editable normalized layout for the native streaming overlay."""
import copy
import json
import math
from pathlib import Path
from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPixmap, QMovie
from PyQt6.QtWidgets import (QWidget, QDialog, QHBoxLayout, QVBoxLayout, QFormLayout,
    QListWidget, QPushButton, QComboBox, QDoubleSpinBox, QLineEdit, QCheckBox,
    QFileDialog, QMessageBox, QScrollArea)


def default_design():
    return {'background': '#0c0c12', 'width': 640, 'height': 180, 'items': [
        {'kind': 'cover', 'x': .02, 'y': .08, 'w': .23, 'h': .84},
        {'kind': 'title', 'x': .28, 'y': .07, 'w': .69, 'h': .21, 'size': 23},
        {'kind': 'artist', 'x': .28, 'y': .3, 'w': .69, 'h': .16, 'size': 17},
        {'kind': 'spectrum', 'x': .28, 'y': .51, 'w': .69, 'h': .32},
        {'kind': 'progress', 'x': .28, 'y': .9, 'w': .69, 'h': .025}]}


def normalize(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get('items'), list):
        raise ValueError('Ожидается объект с массивом items')
    def color(value, default):
        c = QColor(str(value))
        return c.name(QColor.NameFormat.HexArgb) if c.isValid() else default
    def number(value, default, low, high):
        try:
            value = float(value)
            return max(low, min(high, value)) if math.isfinite(value) else default
        except (TypeError, ValueError):
            return default
    design = {'background': color(raw.get('background', '#0c0c12'), '#0c0c12'),
              'width': int(number(raw.get('width'), 640, 320, 3840)),
              'height': int(number(raw.get('height'), 180, 100, 2160)),
              'frameless': bool(raw.get('frameless', False)), 'items': []}
    for item in raw['items'][:64]:
        if not isinstance(item, dict) or item.get('kind') not in KINDS:
            continue
        out = dict(item)
        for key, default in [('x', 0), ('y', 0), ('w', .3), ('h', .2)]:
            out[key] = number(item.get(key), default, .01 if key in ('w', 'h') else 0,
                              1 if key in ('w', 'h') else .99)
        out['w'] = min(out['w'], 1 - out['x'])
        out['h'] = min(out['h'], 1 - out['y'])
        out['size'] = number(item.get('size'), 20, 6, 160)
        out['color'] = color(item.get('color', '#ffffff'), '#ffffff')
        out['opacity'] = number(item.get('opacity'), 1, 0, 1)
        out['radius'] = number(item.get('radius'), 8, 0, 100)
        out['hidden'] = bool(item.get('hidden', False))
        out['text'] = str(item.get('text', ''))[:2000]
        out['file'] = str(item.get('file', ''))[:1024]
        out['family'] = str(item.get('family', 'Segoe UI'))[:80]
        design['items'].append(out)
    return design


KINDS = {'cover': 'Обложка', 'title': 'Название', 'artist': 'Исполнитель',
         'spectrum': 'Визуализатор', 'progress': 'Прогресс', 'time': 'Время',
         'text': 'Свой текст', 'image': 'Изображение / GIF', 'panel': 'Плашка'}


def paint_scene(widget, p, design, st, bands, cover, size=None):
    width, height = size or (widget.width(), widget.height())
    p.fillRect(QRectF(0, 0, width, height), QColor(design.get('background', '#0c0c12')))
    for item in design['items']:
        if item.get('hidden'):
            continue
        r = QRectF(item['x'] * width, item['y'] * height,
                   item['w'] * width, item['h'] * height)
        p.save()
        p.setClipRect(r)
        p.setOpacity(item.get('opacity', 1))
        color = QColor(item.get('color', '#ffffff'))
        p.setPen(color)
        kind = item['kind']
        if kind in ('cover', 'image'):
            pix = cover
            if kind == 'image':
                filename = item.get('file', '')
                cache = getattr(widget, '_asset_cache', None)
                if cache is None:
                    widget._asset_cache = cache = {}
                if filename not in cache:
                    if filename.lower().endswith('.gif'):
                        movie = QMovie(filename, parent=widget)
                        movie.frameChanged.connect(widget.update)
                        movie.start()
                        cache[filename] = movie
                    else:
                        cache[filename] = QPixmap(filename)
                asset = cache[filename]
                pix = asset.currentPixmap() if isinstance(asset, QMovie) else asset
            if pix is not None and not pix.isNull():
                from PyQt6.QtGui import QPainterPath
                path = QPainterPath()
                path.addRoundedRect(r, item.get('radius', 8), item.get('radius', 8))
                p.setClipPath(path)
                scaled = pix.scaled(r.size().toSize(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                    Qt.TransformationMode.SmoothTransformation)
                p.drawPixmap(r, scaled, QRectF((scaled.width()-r.width())/2,
                                               (scaled.height()-r.height())/2, r.width(), r.height()))
        elif kind == 'panel':
            p.setBrush(color)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(r, item.get('radius', 8), item.get('radius', 8))
        elif kind == 'progress':
            p.fillRect(r, QColor(100, 100, 100, 80))
            fraction = max(0, min(1, st.get('pos', 0) / max(.001, st.get('dur', 0))))
            p.fillRect(QRectF(r.x(), r.y(), r.width()*fraction, r.height()), color)
        elif kind == 'spectrum':
            count = max(1, len(bands))
            for index, value in enumerate(bands):
                height = max(1, min(1, float(value)) * r.height())
                p.fillRect(QRectF(r.x()+index*r.width()/count, r.bottom()-height,
                                  max(1, r.width()/count-2), height), color)
        else:
            text = item.get('text', '') if kind == 'text' else str(st.get(kind, '') or ('Нет трека' if kind == 'title' else ''))
            if kind == 'time':
                def fmt(seconds):
                    seconds = max(0, int(seconds))
                    return f'{seconds//60}:{seconds%60:02d}'
                text = fmt(st.get('pos', 0)) + ' / ' + fmt(st.get('dur', 0))
            font = QFont(item.get('family', 'Segoe UI'))
            font.setPixelSize(max(6, round(item.get('size', 20)*width/design.get('width', 640))))
            font.setBold(kind == 'title')
            p.setFont(font)
            p.drawText(r, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       p.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, int(r.width())))
        p.restore()


class Canvas(QWidget):
    def __init__(self, editor):
        super().__init__()
        self.editor, self.selected, self.drag = editor, -1, None
        self.setMinimumSize(400, 220)
        self.setMouseTracking(True)

    def viewport(self):
        design = self.editor.design
        scale = min(self.width()/design['width'], self.height()/design['height'])
        return ((self.width()-design['width']*scale)/2,
                (self.height()-design['height']*scale)/2, scale)

    def scene_point(self, point):
        x, y, scale = self.viewport()
        return QPointF((point.x()-x)/scale, (point.y()-y)/scale)

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor('#202027'))
        x, y, scale = self.viewport()
        p.translate(x,y)
        p.scale(scale,scale)
        width, height = self.editor.design['width'], self.editor.design['height']
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        stream = getattr(self.editor.win, '_stream_win', None)
        st = getattr(stream, '_st', {}) or {'title': 'Название трека', 'artist': 'Исполнитель', 'pos': 70, 'dur': 210}
        paint_scene(self, p, self.editor.design, st, getattr(stream, '_bands', [.3]*48), getattr(stream, '_pix', None), (width, height))
        if 0 <= self.selected < len(self.editor.design['items']):
            item = self.editor.design['items'][self.selected]
            r = QRectF(item['x']*self.editor.design['width'], item['y']*self.editor.design['height'], item['w']*self.editor.design['width'], item['h']*self.editor.design['height'])
            p.setPen(QPen(QColor('#a78bfa'), 2))
            p.drawRect(r)
            p.fillRect(QRectF(r.right()-8, r.bottom()-8, 8, 8), QColor('#a78bfa'))
        p.end()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        point = self.scene_point(event.position())
        for i in reversed(range(len(self.editor.design['items']))):
            item = self.editor.design['items'][i]
            r = QRectF(item['x']*self.editor.design['width'], item['y']*self.editor.design['height'], item['w']*self.editor.design['width'], item['h']*self.editor.design['height'])
            if r.contains(point) and not item.get('hidden'):
                self.editor.list.setCurrentRow(i)
                self.drag = (point, dict(item), abs(point.x()-r.right()) < 12 and abs(point.y()-r.bottom()) < 12)
                break

    def mouseMoveEvent(self, event):
        if not self.drag:
            return
        start, original, resize = self.drag
        item = self.editor.design['items'][self.selected]
        point = self.scene_point(event.position())
        dx, dy = (point.x()-start.x())/self.editor.design['width'], (point.y()-start.y())/self.editor.design['height']
        if resize:
            item['w'] = max(.02, min(1-item['x'], original['w']+dx))
            item['h'] = max(.02, min(1-item['y'], original['h']+dy))
        else:
            item['x'] = max(0, min(1-item['w'], original['x']+dx))
            item['y'] = max(0, min(1-item['h'], original['y']+dy))
        self.editor.select(self.selected)
        self.update()

    def mouseReleaseEvent(self, event):
        self.drag = None


class StreamEditor(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.finished.connect(self.deleteLater)
        self.win = win
        self.design = normalize(copy.deepcopy(win.settings.get('stream_design') or default_design()))
        self.setWindowTitle('Конструктор окна стримера — перетаскивайте элементы и нижний правый угол')
        self.resize(1120, 720)
        row = QHBoxLayout(self)
        left = QVBoxLayout()
        row.addLayout(left)
        self.list = QListWidget()
        left.addWidget(self.list)
        self.kind = QComboBox()
        for key, title in KINDS.items():
            self.kind.addItem(title, key)
        left.addWidget(self.kind)
        for text, callback in [('Добавить', self.add), ('Удалить', self.remove),
                ('На слой выше', lambda: self.reorder(1)), ('На слой ниже', lambda: self.reorder(-1)),
                ('Импорт JSON', self.load), ('Экспорт JSON', self.export), ('Сбросить', self.reset)]:
            button = QPushButton(text)
            button.clicked.connect(callback)
            left.addWidget(button)
        center = QVBoxLayout()
        row.addLayout(center, 1)
        self.canvas = Canvas(self)
        center.addWidget(self.canvas, 1)
        form_widget = QWidget()
        form = QFormLayout(form_widget)
        self.fields = {}
        for key, title, lo, hi in [('x', 'X, %', 0, 100), ('y', 'Y, %', 0, 100),
                ('w', 'Ширина, %', 1, 100), ('h', 'Высота, %', 1, 100),
                ('size', 'Шрифт, px', 6, 160), ('opacity', 'Прозрачность, %', 0, 100), ('radius', 'Скругление', 0, 100)]:
            field = QDoubleSpinBox()
            field.setRange(lo, hi)
            field.valueChanged.connect(lambda value, key=key: self.change(key, value/100 if key in ('x','y','w','h','opacity') else value))
            form.addRow(title, field)
            self.fields[key] = field
        for key, title in [('color', 'Цвет (#RRGGBB / #AARRGGBB)'), ('family', 'Шрифт'), ('text', 'Текст'), ('file', 'Путь изображения / GIF')]:
            field = QLineEdit()
            field.textEdited.connect(lambda text, key=key: self.change(key, text))
            form.addRow(title, field)
            self.fields[key] = field
        pick = QPushButton('Выбрать изображение / GIF')
        pick.clicked.connect(self.pick_image)
        form.addRow(pick)
        self.hidden = QCheckBox('Скрыть элемент')
        self.hidden.toggled.connect(lambda value: self.change('hidden', value))
        form.addRow(self.hidden)
        background = QLineEdit(self.design['background'])
        background.textEdited.connect(lambda text: (self.design.__setitem__('background', text), self.canvas.update()))
        self.background = background
        form.addRow('Фон окна (#AARRGGBB — с прозрачностью)', background)
        frameless = QCheckBox('Без рамки окна (нужно для прозрачного фона)')
        self.frameless = frameless
        frameless.setChecked(self.design.get('frameless',False))
        frameless.toggled.connect(lambda on: self.design.__setitem__('frameless',on))
        form.addRow(frameless)
        self.window_fields = {}
        for key, title, lo, hi in [('width','Ширина окна',320,3840), ('height','Высота окна',100,2160)]:
            field = QDoubleSpinBox()
            field.setRange(lo,hi)
            field.setValue(self.design[key])
            field.valueChanged.connect(lambda value, key=key: (self.design.__setitem__(key,int(value)), self.canvas.update()))
            self.window_fields[key] = field
            form.addRow(title,field)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form_widget)
        scroll.setMinimumWidth(310)
        row.addWidget(scroll)
        apply = QPushButton('Сохранить и применить')
        apply.clicked.connect(self.apply)
        center.addWidget(apply)
        self.list.currentRowChanged.connect(self.select)
        self.refresh()

    def refresh(self):
        for key, field in self.window_fields.items():
            field.blockSignals(True)
            field.setValue(self.design[key])
            field.blockSignals(False)
        self.frameless.blockSignals(True)
        self.frameless.setChecked(self.design.get('frameless', False))
        self.frameless.blockSignals(False)
        self.list.clear()
        self.list.addItems([KINDS[item['kind']] for item in self.design['items']])
        self.list.setCurrentRow(0 if self.design['items'] else -1)
        self.canvas.update()

    def select(self, index):
        self.canvas.selected = index
        if not 0 <= index < len(self.design['items']):
            return
        item = self.design['items'][index]
        for key, field in self.fields.items():
            field.blockSignals(True)
            if isinstance(field, QLineEdit):
                field.setText(str(item.get(key, '')))
            else:
                value = item.get(key, 1 if key == 'opacity' else 20 if key == 'size' else 8 if key == 'radius' else 0)
                field.setValue(value*100 if key in ('x','y','w','h','opacity') else value)
            field.blockSignals(False)
        self.hidden.blockSignals(True)
        self.hidden.setChecked(item.get('hidden', False))
        self.hidden.blockSignals(False)
        self.canvas.update()

    def change(self, key, value):
        index = self.canvas.selected
        if 0 <= index < len(self.design['items']):
            self.design['items'][index][key] = value
            self.canvas.update()

    def add(self):
        self.design['items'].append({'kind':self.kind.currentData(),'x':.1,'y':.1,'w':.5,'h':.25,'size':20,'text':'Текст'})
        self.refresh()
        self.list.setCurrentRow(len(self.design['items'])-1)

    def remove(self):
        index = self.canvas.selected
        if 0 <= index < len(self.design['items']):
            self.design['items'].pop(index)
            self.refresh()

    def reorder(self, direction):
        i = self.canvas.selected
        j = i + direction
        if 0 <= i < len(self.design['items']) and 0 <= j < len(self.design['items']):
            self.design['items'][i], self.design['items'][j] = self.design['items'][j], self.design['items'][i]
            self.refresh()
            self.list.setCurrentRow(j)

    def pick_image(self):
        filename, _ = QFileDialog.getOpenFileName(self, 'Изображение', '', 'Изображения (*.png *.jpg *.jpeg *.webp *.gif)')
        if filename:
            self.change('file',filename)
            self.select(self.canvas.selected)

    def load(self):
        filename, _ = QFileDialog.getOpenFileName(self,'Дизайн','','JSON (*.json)')
        if filename:
            try:
                self.design = normalize(json.loads(Path(filename).read_text('utf-8')))
                self.background.setText(self.design['background'])
                self.refresh()
            except Exception as exc:
                QMessageBox.warning(self,'Не удалось открыть дизайн',str(exc))

    def export(self):
        filename, _ = QFileDialog.getSaveFileName(self,'Дизайн','','JSON (*.json)')
        if filename:
            try:
                Path(filename).write_text(json.dumps(normalize(self.design),ensure_ascii=False,indent=2),'utf-8')
            except Exception as exc:
                QMessageBox.warning(self,'Не удалось сохранить дизайн',str(exc))

    def reset(self):
        self.design = normalize(default_design())
        self.background.setText(self.design['background'])
        self.refresh()

    def apply(self):
        self.design = normalize(self.design)
        self.win.settings['stream_design'] = copy.deepcopy(self.design)
        from player_prefs import _save
        _save(self.win)
        stream = getattr(self.win,'_stream_win',None)
        if stream:
            stream.apply_design()
        self.accept()
