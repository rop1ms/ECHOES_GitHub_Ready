"""Конструктор окна стримера: окно «Сейчас играет» для OBS / TikTok Studio, собранное из слоёв.

Дизайн — нормализованные слои (обложка, название, исполнитель, визуализатор, прогресс, время, свой текст,
картинка/GIF, плашка) в settings["stream_design"]; окно стримера (stream_widget) рисует его каждый кадр.

StreamStudio — редактор в стиле ECHOES: сверху шаблоны и действия, по центру холст (тянуть слои за тело и за
углы, стрелки — сдвиг, Delete — удалить, прилипание к краям и к другим слоям), снизу слева — слои (глаз,
порядок), справа — свойства выбранного слоя и окна. Правки сразу видны в окне стримера (сохраняются сами).
Живёт прямо в Настройках (settings_inline) или в окне StreamEditor (правый щелчок по окну стримера).
"""
import copy
import json
import math
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import QBrush, QColor, QFont, QIcon, QMovie, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFontComboBox,
                             QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu,
                             QMessageBox, QPushButton, QSlider, QVBoxLayout, QWidget)


def default_design():
    return {'background': '#0c0c12', 'width': 640, 'height': 180, 'items': [
        {'kind': 'cover', 'x': .02, 'y': .08, 'w': .23, 'h': .84},
        {'kind': 'title', 'x': .28, 'y': .07, 'w': .69, 'h': .21, 'size': 23},
        {'kind': 'artist', 'x': .28, 'y': .3, 'w': .69, 'h': .16, 'size': 17},
        {'kind': 'spectrum', 'x': .28, 'y': .51, 'w': .69, 'h': .32},
        {'kind': 'progress', 'x': .28, 'y': .9, 'w': .69, 'h': .025}]}


# Готовые шаблоны — отправная точка (дальше всё правится)
TEMPLATES = {
    'Компакт': default_design,
    'Широкая полоса': lambda: {'background': '#e6101016', 'width': 1280, 'height': 160, 'frameless': True, 'items': [
        {'kind': 'panel', 'x': 0, 'y': 0, 'w': 1, 'h': 1, 'color': '#cc14141c', 'radius': 26},
        {'kind': 'cover', 'x': .012, 'y': .1, 'w': .1, 'h': .8, 'radius': 16},
        {'kind': 'title', 'x': .125, 'y': .14, 'w': .5, 'h': .32, 'size': 34},
        {'kind': 'artist', 'x': .125, 'y': .5, 'w': .5, 'h': .24, 'size': 24, 'color': '#b8b8c0'},
        {'kind': 'spectrum', 'x': .64, 'y': .14, 'w': .34, 'h': .6, 'color': '#ffdb1a'},
        {'kind': 'progress', 'x': .125, 'y': .84, 'w': .855, 'h': .035, 'color': '#ffdb1a'}]},
    'Карточка': lambda: {'background': '#0c0c12', 'width': 420, 'height': 520, 'items': [
        {'kind': 'cover', 'x': .07, 'y': .05, 'w': .86, 'h': .66, 'radius': 22},
        {'kind': 'title', 'x': .07, 'y': .73, 'w': .86, 'h': .08, 'size': 26, 'align': 'center'},
        {'kind': 'artist', 'x': .07, 'y': .81, 'w': .86, 'h': .06, 'size': 19, 'color': '#a9a9b2', 'align': 'center'},
        {'kind': 'progress', 'x': .07, 'y': .9, 'w': .86, 'h': .012, 'color': '#ffdb1a'},
        {'kind': 'time', 'x': .07, 'y': .92, 'w': .86, 'h': .05, 'size': 14, 'color': '#8a8a92', 'align': 'center'}]},
    'Минимал': lambda: {'background': '#00000000', 'width': 640, 'height': 90, 'frameless': True, 'items': [
        {'kind': 'title', 'x': .02, 'y': .05, 'w': .96, 'h': .5, 'size': 30},
        {'kind': 'artist', 'x': .02, 'y': .55, 'w': .96, 'h': .4, 'size': 20, 'color': '#cfcfd6'}]},
}


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
              'height': int(number(raw.get('height'), 180, 80, 2160)),
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
        out['align'] = item.get('align') if item.get('align') in ('left', 'center', 'right') else 'left'
        design['items'].append(out)
    return design


_FONTS_READY = []


def _fonts():
    """Свои шрифты из файлов (custom_fonts) — зарегистрировать до первой отрисовки окна стримера."""
    if not _FONTS_READY:
        _FONTS_READY.append(True)
        try:
            import custom_fonts
            custom_fonts.load_all()
        except Exception as exc:                          # noqa: BLE001
            print('[stream] fonts:', exc)


KINDS = {'cover': 'Обложка', 'title': 'Название', 'artist': 'Исполнитель',
         'spectrum': 'Визуализатор', 'progress': 'Прогресс', 'time': 'Время',
         'text': 'Свой текст', 'image': 'Изображение / GIF', 'panel': 'Плашка'}
KIND_ICON = {'cover': 'cover', 'title': 'note', 'artist': 'user', 'spectrum': 'eq', 'progress': 'play',
             'time': 'clock', 'text': 'edit', 'image': 'attach', 'panel': 'album'}
TEXT_KINDS = ('title', 'artist', 'time', 'text')
_ALIGN = {'left': Qt.AlignmentFlag.AlignLeft, 'center': Qt.AlignmentFlag.AlignHCenter,
          'right': Qt.AlignmentFlag.AlignRight}


def paint_scene(widget, p, design, st, bands, cover, size=None):
    _fonts()
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
                path = QPainterPath()
                path.addRoundedRect(r, item.get('radius', 8), item.get('radius', 8))
                p.setClipPath(path)
                scaled = pix.scaled(r.size().toSize(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                    Qt.TransformationMode.SmoothTransformation)
                p.drawPixmap(r, scaled, QRectF((scaled.width()-r.width())/2,
                                               (scaled.height()-r.height())/2, r.width(), r.height()))
            elif kind == 'cover':                         # нет обложки — мягкая заглушка, чтобы место было видно
                p.setBrush(QColor(255, 255, 255, 22))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(r, item.get('radius', 8), item.get('radius', 8))
        elif kind == 'panel':
            p.setBrush(color)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(r, item.get('radius', 8), item.get('radius', 8))
        elif kind == 'progress':
            rad = min(r.height() / 2, item.get('radius', 8))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(100, 100, 100, 80))
            p.drawRoundedRect(r, rad, rad)
            fraction = max(0, min(1, st.get('pos', 0) / max(.001, st.get('dur', 0))))
            p.setBrush(color)
            p.drawRoundedRect(QRectF(r.x(), r.y(), r.width()*fraction, r.height()), rad, rad)
        elif kind == 'spectrum':
            count = max(1, len(bands))
            bw = max(1, r.width()/count-2)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            rad = min(bw / 2, item.get('radius', 8) / 4)
            for index, value in enumerate(bands):
                height = max(1, min(1, float(value)) * r.height())
                p.drawRoundedRect(QRectF(r.x()+index*r.width()/count, r.bottom()-height, bw, height), rad, rad)
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
            p.drawText(r, _ALIGN.get(item.get('align'), Qt.AlignmentFlag.AlignLeft) | Qt.AlignmentFlag.AlignVCenter,
                       p.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, int(r.width())))
        p.restore()


# ══════════════════════════════════════════════════════════════════════════ #
#  Редактор
# ══════════════════════════════════════════════════════════════════════════ #

ACCENT = '#ffdb1a'
QSS = f"""
QWidget#StreamStudio {{ background: transparent; }}
QFrame#SSPanel {{ background: rgba(255,255,255,0.035); border: 1px solid rgba(255,255,255,0.07); border-radius: 14px; }}
QLabel#SSHead {{ color: #8f8f96; font-size: 10px; font-weight: 900; letter-spacing: 1px; background: transparent; }}
QLabel {{ background: transparent; }}
QLabel#SSHint {{ color: #8a8a92; font-size: 11px; }}
QPushButton#SSChip {{ background: rgba(255,255,255,0.07); border: 1px solid transparent; border-radius: 13px;
                      padding: 5px 12px; font-weight: 700; }}
QPushButton#SSChip:hover {{ background: rgba(255,255,255,0.13); }}
QPushButton#SSChip:checked {{ border: 1px solid {ACCENT}; color: {ACCENT}; }}
QPushButton#SSAccent {{ background: {ACCENT}; color: #000; border: none; border-radius: 13px; padding: 6px 14px;
                        font-weight: 800; }}
QPushButton#SSRound {{ background: rgba(255,255,255,0.07); border: none; border-radius: 15px; }}
QPushButton#SSRound:hover {{ background: rgba(255,255,255,0.15); }}
QListWidget#SSLayers {{ background: transparent; border: none; outline: none; }}
QListWidget#SSLayers::item {{ border-radius: 9px; padding: 6px 6px; margin: 1px 0; }}
QListWidget#SSLayers::item:hover {{ background: rgba(255,255,255,0.06); }}
QListWidget#SSLayers::item:selected {{ background: rgba(255,219,26,0.16); color: {ACCENT}; }}
QDoubleSpinBox, QLineEdit, QComboBox, QFontComboBox {{ background: rgba(255,255,255,0.06);
    border: 1px solid rgba(255,255,255,0.09); border-radius: 8px; padding: 4px 7px; min-height: 22px; }}
QSlider::groove:horizontal {{ height: 4px; background: rgba(255,255,255,0.12); border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: #fff; width: 14px; margin: -6px 0; border-radius: 7px; }}
"""
SIZES = [('640 × 180', 640, 180), ('800 × 200', 800, 200), ('1280 × 160', 1280, 160), ('1280 × 360', 1280, 360),
         ('420 × 520', 420, 520), ('1080 × 1080', 1080, 1080), ('1920 × 1080', 1920, 1080)]


def _icon(name, size=16, color='#d6d6dc'):
    try:
        from yamusic_theme import ya_icon
        return ya_icon(name, size, color)
    except Exception:                                     # noqa: BLE001
        return QIcon()


def _round(name, tip, fn):
    b = QPushButton()
    b.setObjectName('SSRound')
    b.setIcon(_icon(name, 15))
    b.setIconSize(QSize(15, 15))
    b.setFixedSize(30, 30)
    b.setToolTip(tip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.clicked.connect(fn)
    return b


class _Swatch(QPushButton):
    """Кнопка-цвет (с прозрачностью): щелчок — палитра с альфой."""
    def __init__(self, on_pick):
        super().__init__()
        self.setFixedSize(46, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.color = QColor('#ffffff')
        self.on_pick = on_pick
        self.clicked.connect(self._pick)

    def set_color(self, c):
        self.color = QColor(c)
        self.update()

    def _pick(self):
        c = QColorDialog.getColor(self.color, self, 'Цвет', QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if c.isValid():
            self.set_color(c)
            self.on_pick(c.name(QColor.NameFormat.HexArgb))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(r, 8, 8)
        p.setClipPath(path)
        _checker(p, r, 6)
        p.fillRect(r, self.color)
        p.setClipping(False)
        p.setPen(QPen(QColor(255, 255, 255, 60), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, 8, 8)
        p.end()


def _checker(p, r, cell=10):
    p.fillRect(r, QColor('#2a2a31'))
    c2 = QColor('#36363e')
    y, row = r.top(), 0
    while y < r.bottom():
        x = r.left() + (cell if row % 2 else 0)
        while x < r.right():
            p.fillRect(QRectF(x, y, cell, cell).intersected(r), c2)
            x += cell * 2
        y += cell
        row += 1


class Canvas(QWidget):
    """Холст: слои тянутся за тело и за любой угол, прилипают к краям/центру и к другим слоям."""
    SNAP = 0.012

    def __init__(self, editor):
        super().__init__()
        self.editor, self.selected, self.drag, self.hover = editor, -1, None, -1
        self.guides = []
        self.setMinimumSize(320, 200)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

    def viewport(self):
        design = self.editor.design
        pad = 14
        scale = min((self.width() - pad * 2) / design['width'], (self.height() - pad * 2) / design['height'])
        return ((self.width()-design['width']*scale)/2, (self.height()-design['height']*scale)/2, scale)

    def scene_point(self, point):
        x, y, scale = self.viewport()
        return QPointF((point.x()-x)/scale, (point.y()-y)/scale)

    def _rect(self, item):
        d = self.editor.design
        return QRectF(item['x']*d['width'], item['y']*d['height'], item['w']*d['width'], item['h']*d['height'])

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = QPainterPath()
        bg.addRoundedRect(QRectF(self.rect()), 14, 14)
        p.fillPath(bg, QColor('#141418'))
        x, y, scale = self.viewport()
        d = self.editor.design
        width, height = d['width'], d['height']
        area = QRectF(x, y, width * scale, height * scale)
        p.save()
        p.setClipRect(area)
        _checker(p, area, 9)                              # шахматка — видно прозрачный фон окна
        p.restore()
        p.save()
        p.translate(x, y)
        p.scale(scale, scale)
        stream = getattr(self.editor.win, '_stream_win', None)
        st = getattr(stream, '_st', {}) or {'title': 'Название трека', 'artist': 'Исполнитель', 'pos': 70, 'dur': 210}
        bands = getattr(stream, '_bands', None)
        if bands is None:
            bands = [0.25 + 0.55 * abs(math.sin(i * 0.55)) for i in range(48)]
        paint_scene(self, p, d, st, bands, getattr(stream, '_pix', None), (width, height))
        p.restore()
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(area)
        items = d['items']
        if 0 <= self.hover < len(items) and self.hover != self.selected:
            r = self._screen(self._rect(items[self.hover]))
            p.setPen(QPen(QColor(255, 255, 255, 90), 1, Qt.PenStyle.DashLine))
            p.drawRect(r)
        p.setPen(QPen(QColor(ACCENT), 1, Qt.PenStyle.DashLine))
        for kind, v in self.guides:                       # линии прилипания
            if kind == 'x':
                gx = x + v * width * scale
                p.drawLine(QPointF(gx, area.top()), QPointF(gx, area.bottom()))
            else:
                gy = y + v * height * scale
                p.drawLine(QPointF(area.left(), gy), QPointF(area.right(), gy))
        if 0 <= self.selected < len(items):
            r = self._screen(self._rect(items[self.selected]))
            p.setPen(QPen(QColor(ACCENT), 1.6))
            p.drawRect(r)
            p.setBrush(QColor('#ffffff'))
            p.setPen(QPen(QColor(ACCENT), 1.4))
            for c in (r.topLeft(), r.topRight(), r.bottomLeft(), r.bottomRight()):
                p.drawEllipse(c, 4.5, 4.5)
        p.end()

    def _screen(self, r):
        x, y, s = self.viewport()
        return QRectF(x + r.x() * s, y + r.y() * s, r.width() * s, r.height() * s)

    def _hit(self, pos):
        items = self.editor.design['items']
        if 0 <= self.selected < len(items):                # сперва — углы выделенного
            r = self._screen(self._rect(items[self.selected]))
            for name, c in (('tl', r.topLeft()), ('tr', r.topRight()), ('bl', r.bottomLeft()), ('br', r.bottomRight())):
                if abs(pos.x() - c.x()) < 8 and abs(pos.y() - c.y()) < 8:
                    return self.selected, name
        for i in reversed(range(len(items))):
            if not items[i].get('hidden') and self._screen(self._rect(items[i])).contains(pos):
                return i, 'move'
        return -1, None

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        i, mode = self._hit(event.position())
        self.editor.select_layer(i)
        if i >= 0:
            self.drag = (self.scene_point(event.position()), dict(self.editor.design['items'][i]), mode)
        self.update()

    def mouseMoveEvent(self, event):
        if not self.drag:
            i, mode = self._hit(event.position())
            self.hover = i
            cur = {'tl': Qt.CursorShape.SizeFDiagCursor, 'br': Qt.CursorShape.SizeFDiagCursor,
                   'tr': Qt.CursorShape.SizeBDiagCursor, 'bl': Qt.CursorShape.SizeBDiagCursor,
                   'move': Qt.CursorShape.SizeAllCursor}.get(mode, Qt.CursorShape.ArrowCursor)
            self.setCursor(cur)
            self.update()
            return
        start, o, mode = self.drag
        d = self.editor.design
        item = d['items'][self.selected]
        pt = self.scene_point(event.position())
        dx, dy = (pt.x()-start.x())/d['width'], (pt.y()-start.y())/d['height']
        free = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        self.guides = []
        if mode == 'move':
            nx = max(0, min(1-o['w'], o['x']+dx))
            ny = max(0, min(1-o['h'], o['y']+dy))
            if not free:
                nx = self._snap('x', nx, o['w'])
                ny = self._snap('y', ny, o['h'])
            item['x'], item['y'] = nx, ny
        else:
            l, t, r_, b = o['x'], o['y'], o['x'] + o['w'], o['y'] + o['h']
            if 'l' in mode:
                l = min(r_ - .02, max(0, l + dx))
            if 'r' in mode:
                r_ = max(l + .02, min(1, r_ + dx))
            if 't' in mode:
                t = min(b - .02, max(0, t + dy))
            if 'b' in mode:
                b = max(t + .02, min(1, b + dy))
            if not free:
                l, r_ = self._snap_edge('x', l) if 'l' in mode else l, self._snap_edge('x', r_) if 'r' in mode else r_
                t, b = self._snap_edge('y', t) if 't' in mode else t, self._snap_edge('y', b) if 'b' in mode else b
            item['x'], item['y'], item['w'], item['h'] = l, t, max(.02, r_ - l), max(.02, b - t)
        self.editor.layer_moved()
        self.update()

    def _targets(self, axis):
        out = [0.0, 0.5, 1.0]
        for j, it in enumerate(self.editor.design['items']):
            if j != self.selected and not it.get('hidden'):
                a, size = (it['x'], it['w']) if axis == 'x' else (it['y'], it['h'])
                out += [a, a + size, a + size / 2]
        return out

    def _snap(self, axis, pos, size):
        best = None
        for t in self._targets(axis):
            for off in (0.0, size, size / 2):
                dlt = t - (pos + off)
                if abs(dlt) < self.SNAP and (best is None or abs(dlt) < abs(best[0])):
                    best = (dlt, t)
        if best is None:
            return pos
        self.guides.append((axis, best[1]))
        return max(0.0, min(1 - size, pos + best[0]))

    def _snap_edge(self, axis, v):
        for t in self._targets(axis):
            if abs(t - v) < self.SNAP:
                self.guides.append((axis, t))
                return t
        return v

    def mouseReleaseEvent(self, event):
        if self.drag:
            self.drag = None
            self.guides = []
            self.editor.changed(commit=True)
            self.update()

    def leaveEvent(self, e):
        self.hover = -1
        self.update()

    def keyPressEvent(self, e):
        items = self.editor.design['items']
        if not 0 <= self.selected < len(items):
            return super().keyPressEvent(e)
        k = e.key()
        step = 0.05 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 0.005
        it = items[self.selected]
        if k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.editor.remove()
            return
        moves = {Qt.Key.Key_Left: ('x', -step), Qt.Key.Key_Right: ('x', step),
                 Qt.Key.Key_Up: ('y', -step), Qt.Key.Key_Down: ('y', step)}
        if k in moves:
            key, dv = moves[k]
            size = it['w'] if key == 'x' else it['h']
            it[key] = max(0, min(1 - size, it[key] + dv))
            self.editor.layer_moved()
            self.editor.changed(commit=True)
            self.update()
            return
        super().keyPressEvent(e)


class StreamStudio(QWidget):
    """Конструктор окна стримера в стиле ECHOES (см. описание модуля)."""

    def __init__(self, win, parent=None):
        super().__init__(parent)
        _fonts()
        self.setObjectName('StreamStudio')
        self.setStyleSheet(QSS)
        self.win = win
        try:
            self.design = normalize(copy.deepcopy(win.settings.get('stream_design') or default_design()))
        except ValueError:
            self.design = normalize(default_design())
        self._win_sig = self._window_sig()
        self._save_t = QTimer(self)
        self._save_t.setSingleShot(True)
        self._save_t.setInterval(350)
        self._save_t.timeout.connect(self.save)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)
        # ── шаблоны и действия ──
        top = QHBoxLayout()
        top.setSpacing(6)
        lab = QLabel('ШАБЛОНЫ')
        lab.setObjectName('SSHead')
        top.addWidget(lab)
        for name in TEMPLATES:
            b = QPushButton(name)
            b.setObjectName('SSChip')
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, n=name: self.use_template(n))
            top.addWidget(b)
        top.addStretch(1)
        more = QPushButton()
        more.setObjectName('SSRound')
        more.setIcon(_icon('dots', 15))
        more.setFixedSize(30, 30)
        more.setToolTip('Импорт, экспорт, сброс')
        more.setCursor(Qt.CursorShape.PointingHandCursor)
        more.clicked.connect(lambda: self._more_menu(more))
        top.addWidget(more)
        self.show_btn = QPushButton('  Окно стримера')
        self.show_btn.setObjectName('SSAccent')
        self.show_btn.setIcon(_icon('broadcast_tower', 15, '#000000'))
        self.show_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.show_btn.setToolTip('Открыть окно «Сейчас играет» — его захватывает OBS / TikTok Studio')
        self.show_btn.clicked.connect(self.open_stream)
        top.addWidget(self.show_btn)
        v.addLayout(top)
        # ── холст ──
        self.canvas = Canvas(self)
        self.canvas.setMinimumHeight(250)
        v.addWidget(self.canvas, 3)
        hint = QLabel('Тяните слой или его угол · стрелки — сдвиг (Shift — крупнее) · Delete — удалить · '
                      'Alt — без прилипания · правки сразу в окне стримера')
        hint.setObjectName('SSHint')
        hint.setWordWrap(True)
        v.addWidget(hint)
        # ── слои | свойства ──
        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        v.addLayout(bottom, 2)
        lp = QFrame()
        lp.setObjectName('SSPanel')
        lp.setFixedWidth(230)
        lv = QVBoxLayout(lp)
        lv.setContentsMargins(10, 10, 10, 10)
        lv.setSpacing(6)
        lh = QHBoxLayout()
        t = QLabel('СЛОИ')
        t.setObjectName('SSHead')
        lh.addWidget(t)
        lh.addStretch(1)
        self.add_btn = _round('plus', 'Добавить слой', lambda: self._add_menu(self.add_btn))
        lh.addWidget(self.add_btn)
        lv.addLayout(lh)
        self.list = QListWidget()
        self.list.setObjectName('SSLayers')
        self.list.setIconSize(QSize(16, 16))
        self.list.currentRowChanged.connect(lambda i: self.select_layer(self._row_to_index(i), from_list=True))
        self.list.itemChanged.connect(self._item_checked)
        lv.addWidget(self.list, 1)
        ops = QHBoxLayout()
        ops.addWidget(_round('up', 'Выше (поверх других)', lambda: self.reorder(1)))
        ops.addWidget(_round('down', 'Ниже', lambda: self.reorder(-1)))
        ops.addWidget(_round('copy', 'Дублировать', self.duplicate))
        ops.addStretch(1)
        ops.addWidget(_round('close', 'Удалить слой', self.remove))
        lv.addLayout(ops)
        bottom.addWidget(lp)
        bottom.addWidget(self._props(), 1)
        self.refresh()

    # ── свойства ──
    def _props(self):
        pf = QFrame()
        pf.setObjectName('SSPanel')
        pv = QVBoxLayout(pf)
        pv.setContentsMargins(12, 10, 12, 12)
        pv.setSpacing(8)
        self.sel_title = QLabel('Слой')
        self.sel_title.setStyleSheet('font-size:15px;font-weight:800;')
        pv.addWidget(self.sel_title)
        g = QGridLayout()
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(6)
        self.fields = {}
        for i, (key, title) in enumerate((('x', 'X'), ('y', 'Y'), ('w', 'Ширина'), ('h', 'Высота'))):
            f = QDoubleSpinBox()
            f.setRange(0, 100)
            f.setDecimals(1)
            f.setSuffix(' %')
            f.valueChanged.connect(lambda val, k=key: self.change(k, val / 100))
            lab = QLabel(title)
            lab.setObjectName('SSHint')
            g.addWidget(lab, i // 2 * 2, i % 2)
            g.addWidget(f, i // 2 * 2 + 1, i % 2)
            self.fields[key] = f
        pv.addLayout(g)
        look = QGridLayout()
        look.setHorizontalSpacing(8)
        look.setVerticalSpacing(6)
        self.swatch = _Swatch(lambda c: self.change('color', c))
        lab = QLabel('Цвет')
        lab.setObjectName('SSHint')
        look.addWidget(lab, 0, 0)
        look.addWidget(self.swatch, 0, 1)
        self.sliders = {}
        for r, (key, title, hi) in enumerate((('opacity', 'Прозрачность', 100), ('radius', 'Скругление', 100)), 1):
            s = QSlider(Qt.Orientation.Horizontal)
            s.setRange(0, hi)
            s.valueChanged.connect(lambda val, k=key: self.change(k, val / 100 if k == 'opacity' else val))
            lab = QLabel(title)
            lab.setObjectName('SSHint')
            look.addWidget(lab, r, 0)
            look.addWidget(s, r, 1)
            self.sliders[key] = s
        pv.addLayout(look)
        # текст
        self.text_box = QWidget()
        tg = QGridLayout(self.text_box)
        tg.setContentsMargins(0, 0, 0, 0)
        tg.setHorizontalSpacing(8)
        tg.setVerticalSpacing(6)
        self.family = QFontComboBox()
        self.family.setEditable(True)
        self.family.currentTextChanged.connect(lambda text: self.change('family', text.strip()[:80] or 'Segoe UI'))
        ff = QPushButton('Из файла…')
        ff.setObjectName('SSChip')
        ff.setToolTip('TTF, OTF, TTC, WOFF — шрифт сохранится в ECHOES')
        ff.clicked.connect(self.pick_font)
        lab = QLabel('Шрифт')
        lab.setObjectName('SSHint')
        tg.addWidget(lab, 0, 0)
        fr = QHBoxLayout()
        fr.addWidget(self.family, 1)
        fr.addWidget(ff)
        tg.addLayout(fr, 0, 1)
        self.size = QDoubleSpinBox()
        self.size.setRange(6, 160)
        self.size.setSuffix(' px')
        self.size.valueChanged.connect(lambda val: self.change('size', val))
        lab = QLabel('Размер')
        lab.setObjectName('SSHint')
        tg.addWidget(lab, 1, 0)
        ar = QHBoxLayout()
        ar.addWidget(self.size)
        self.align_btns = {}
        for key, title in (('left', 'Слева'), ('center', 'По центру'), ('right', 'Справа')):
            b = QPushButton(title)
            b.setObjectName('SSChip')
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, k=key: self.change('align', k, refresh=True))
            ar.addWidget(b)
            self.align_btns[key] = b
        tg.addLayout(ar, 1, 1)
        self.text_lab = QLabel('Текст')
        self.text_lab.setObjectName('SSHint')
        self.text = QLineEdit()
        self.text.textEdited.connect(lambda text: self.change('text', text))
        tg.addWidget(self.text_lab, 2, 0)
        tg.addWidget(self.text, 2, 1)
        pv.addWidget(self.text_box)
        # картинка
        self.img_box = QWidget()
        ib = QHBoxLayout(self.img_box)
        ib.setContentsMargins(0, 0, 0, 0)
        self.file_lab = QLabel('Файл не выбран')
        self.file_lab.setObjectName('SSHint')
        pick = QPushButton('Выбрать картинку / GIF…')
        pick.setObjectName('SSChip')
        pick.clicked.connect(self.pick_image)
        ib.addWidget(pick)
        ib.addWidget(self.file_lab, 1)
        pv.addWidget(self.img_box)
        # окно
        sep = QLabel('ОКНО')
        sep.setObjectName('SSHead')
        pv.addSpacing(4)
        pv.addWidget(sep)
        wg = QGridLayout()
        wg.setHorizontalSpacing(8)
        wg.setVerticalSpacing(6)
        self.bg = _Swatch(lambda c: (self.design.__setitem__('background', c), self.changed()))
        lab = QLabel('Фон')
        lab.setObjectName('SSHint')
        wg.addWidget(lab, 0, 0)
        bgr = QHBoxLayout()
        bgr.addWidget(self.bg)
        self.frameless = QCheckBox('Без рамки (для прозрачного фона)')
        self.frameless.toggled.connect(lambda on: (self.design.__setitem__('frameless', on), self.changed()))
        bgr.addWidget(self.frameless)
        bgr.addStretch(1)
        wg.addLayout(bgr, 0, 1)
        lab = QLabel('Размер')
        lab.setObjectName('SSHint')
        wg.addWidget(lab, 1, 0)
        sr = QHBoxLayout()
        self.preset = QComboBox()
        for title, _w, _h in SIZES:
            self.preset.addItem(title)
        self.preset.addItem('Свой')
        self.preset.activated.connect(self._preset)
        sr.addWidget(self.preset)
        self.win_w, self.win_h = QDoubleSpinBox(), QDoubleSpinBox()
        for f, key, lo, hi in ((self.win_w, 'width', 320, 3840), (self.win_h, 'height', 80, 2160)):
            f.setRange(lo, hi)
            f.setDecimals(0)
            f.valueChanged.connect(lambda val, k=key: self._win_size(k, val))
            sr.addWidget(f)
        wg.addLayout(sr, 1, 1)
        pv.addLayout(wg)
        pv.addStretch(1)
        return pf

    # ── данные ──
    def _row_to_index(self, row):
        n = len(self.design['items'])
        return n - 1 - row if 0 <= row < n else -1        # в списке верхний слой — сверху

    def _index_to_row(self, i):
        n = len(self.design['items'])
        return n - 1 - i if 0 <= i < n else -1

    def refresh(self, keep=None):
        items = self.design['items']
        self.list.blockSignals(True)
        self.list.clear()
        for it in reversed(items):
            name = KINDS[it['kind']] + (f' · {it["text"][:16]}' if it['kind'] == 'text' and it.get('text') else '')
            li = QListWidgetItem(_icon(KIND_ICON.get(it['kind'], 'edit'), 16,
                                       '#6a6a72' if it.get('hidden') else '#e0e0e6'), name)
            li.setFlags(li.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            li.setCheckState(Qt.CheckState.Unchecked if it.get('hidden') else Qt.CheckState.Checked)
            li.setToolTip('Галочка — показывать слой')
            self.list.addItem(li)
        self.list.blockSignals(False)
        self.bg.set_color(self.design['background'])
        self.frameless.blockSignals(True)
        self.frameless.setChecked(bool(self.design.get('frameless')))
        self.frameless.blockSignals(False)
        for f, key in ((self.win_w, 'width'), (self.win_h, 'height')):
            f.blockSignals(True)
            f.setValue(self.design[key])
            f.blockSignals(False)
        idx = next((k for k, (_t, w, h) in enumerate(SIZES)
                    if (w, h) == (self.design['width'], self.design['height'])), len(SIZES))
        self.preset.setCurrentIndex(idx)
        i = keep if keep is not None else (self.canvas.selected if 0 <= self.canvas.selected < len(items)
                                           else len(items) - 1)
        self.select_layer(i)

    def select_layer(self, i, from_list=False):
        items = self.design['items']
        i = i if 0 <= i < len(items) else -1
        self.canvas.selected = i
        if not from_list:
            self.list.blockSignals(True)
            self.list.setCurrentRow(self._index_to_row(i))
            self.list.blockSignals(False)
        has = i >= 0
        for w in list(self.fields.values()) + list(self.sliders.values()) + [self.swatch]:
            w.setEnabled(has)
        if not has:
            self.sel_title.setText('Выберите слой на холсте или в списке')
            self.text_box.hide()
            self.img_box.hide()
            self.canvas.update()
            return
        it = items[i]
        self.sel_title.setText(KINDS[it['kind']])
        self.layer_moved()
        self.swatch.set_color(it.get('color', '#ffffff'))
        for key, s in self.sliders.items():
            s.blockSignals(True)
            s.setValue(int(round(it.get(key, 1 if key == 'opacity' else 8) * (100 if key == 'opacity' else 1))))
            s.blockSignals(False)
        is_text = it['kind'] in TEXT_KINDS
        self.text_box.setVisible(is_text)
        if is_text:
            self.family.blockSignals(True)
            self.family.setCurrentText(str(it.get('family') or 'Segoe UI'))
            self.family.blockSignals(False)
            self.size.blockSignals(True)
            self.size.setValue(it.get('size', 20))
            self.size.blockSignals(False)
            for k, b in self.align_btns.items():
                b.setChecked(k == it.get('align', 'left'))
            own = it['kind'] == 'text'
            self.text_lab.setVisible(own)
            self.text.setVisible(own)
            self.text.setText(it.get('text', ''))
        self.img_box.setVisible(it['kind'] == 'image')
        if it['kind'] == 'image':
            self.file_lab.setText(Path(it.get('file', '')).name or 'Файл не выбран')
        self.canvas.update()

    def layer_moved(self):
        i = self.canvas.selected
        if not 0 <= i < len(self.design['items']):
            return
        it = self.design['items'][i]
        for key, f in self.fields.items():
            f.blockSignals(True)
            f.setValue(it[key] * 100)
            f.blockSignals(False)

    def change(self, key, value, refresh=False):
        i = self.canvas.selected
        if 0 <= i < len(self.design['items']):
            it = self.design['items'][i]
            it[key] = value
            if key in ('x', 'y', 'w', 'h'):
                it['w'] = min(max(.01, it['w']), 1 - it['x'])
                it['h'] = min(max(.01, it['h']), 1 - it['y'])
            if refresh:
                self.select_layer(i)
            self.changed()

    def changed(self, commit=False):
        self.canvas.update()
        self._save_t.start(0 if commit else 350)

    def _item_checked(self, li):
        i = self._row_to_index(self.list.row(li))
        if 0 <= i < len(self.design['items']):
            self.design['items'][i]['hidden'] = li.checkState() != Qt.CheckState.Checked
            li.setIcon(_icon(KIND_ICON.get(self.design['items'][i]['kind'], 'edit'), 16,
                             '#6a6a72' if self.design['items'][i]['hidden'] else '#e0e0e6'))
            self.changed()

    def _window_sig(self):
        d = self.design
        return (d['width'], d['height'], bool(d.get('frameless')))

    def save(self):
        """Сохранить и показать в окне стримера (окно переоткрывается, только если поменялись его рамка/размер)."""
        try:
            self.design = normalize(self.design)
        except ValueError:
            return
        self.win.settings['stream_design'] = copy.deepcopy(self.design)
        try:
            from player_prefs import _save
            _save(self.win)
        except Exception as exc:                          # noqa: BLE001
            print('[stream] save:', exc)
        stream = getattr(self.win, '_stream_win', None)
        if stream is not None:
            if self._window_sig() != self._win_sig:
                stream.apply_design()
            else:
                stream.update()
        self._win_sig = self._window_sig()

    # ── действия ──
    def _add_menu(self, anchor):
        m = QMenu(self)
        for key, title in KINDS.items():
            m.addAction(_icon(KIND_ICON.get(key, 'edit'), 16), title, lambda k=key: self.add(k))
        m.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _more_menu(self, anchor):
        m = QMenu(self)
        m.addAction('Импорт дизайна (JSON)…', self.load)
        m.addAction('Экспорт дизайна (JSON)…', self.export)
        m.addSeparator()
        m.addAction('Сбросить к стандартному', self.reset)
        m.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def add(self, kind):
        item = {'kind': kind, 'x': .1, 'y': .1, 'w': .4, 'h': .25, 'size': 20}
        if kind == 'text':
            item['text'] = 'Мой текст'
        if kind == 'panel':
            item.update(x=0, y=0, w=1, h=1, color='#99000000', radius=18)
        self.design['items'].append(item)
        self.design = normalize(self.design)
        self.refresh(keep=len(self.design['items']) - 1)
        if kind == 'image':
            self.pick_image()
        self.changed(commit=True)

    def duplicate(self):
        i = self.canvas.selected
        if 0 <= i < len(self.design['items']):
            it = dict(self.design['items'][i])
            it['x'] = min(1 - it['w'], it['x'] + .03)
            it['y'] = min(1 - it['h'], it['y'] + .03)
            self.design['items'].insert(i + 1, it)
            self.refresh(keep=i + 1)
            self.changed(commit=True)

    def remove(self):
        i = self.canvas.selected
        if 0 <= i < len(self.design['items']):
            self.design['items'].pop(i)
            self.refresh(keep=min(i, len(self.design['items']) - 1))
            self.changed(commit=True)

    def reorder(self, direction):
        i = self.canvas.selected
        j = i + direction
        items = self.design['items']
        if 0 <= i < len(items) and 0 <= j < len(items):
            items[i], items[j] = items[j], items[i]
            self.refresh(keep=j)
            self.changed(commit=True)

    def _preset(self, k):
        if k < len(SIZES):
            _t, w, h = SIZES[k]
            self.design['width'], self.design['height'] = w, h
            self.refresh(keep=self.canvas.selected)
            self.changed(commit=True)

    def _win_size(self, key, val):
        self.design[key] = int(val)
        idx = next((k for k, (_t, w, h) in enumerate(SIZES)
                    if (w, h) == (self.design['width'], self.design['height'])), len(SIZES))
        self.preset.setCurrentIndex(idx)
        self.changed()

    def use_template(self, name):
        self.design = normalize(TEMPLATES[name]())
        self.refresh(keep=-1)
        self.changed(commit=True)

    def open_stream(self):
        self.save()
        fn = getattr(self.win, '_open_stream_widget', None)
        if fn is not None:
            fn()

    def pick_image(self):
        filename, _ = QFileDialog.getOpenFileName(self, 'Изображение', '', 'Изображения (*.png *.jpg *.jpeg *.webp *.gif)')
        if filename:
            self.change('file', filename, refresh=True)

    def pick_font(self):
        import custom_fonts
        filename, _ = QFileDialog.getOpenFileName(self, 'Шрифт', '', custom_fonts.FILTER)
        if not filename:
            return
        try:
            family = custom_fonts.import_font(filename)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, 'Шрифт', f'Не удалось подключить шрифт: {exc}')
            return
        self.family.setCurrentText(family)
        self.change('family', family)

    def load(self):
        filename, _ = QFileDialog.getOpenFileName(self, 'Дизайн', '', 'JSON (*.json)')
        if filename:
            try:
                self.design = normalize(json.loads(Path(filename).read_text('utf-8')))
                self.refresh(keep=-1)
                self.changed(commit=True)
            except Exception as exc:                      # noqa: BLE001
                QMessageBox.warning(self, 'Не удалось открыть дизайн', str(exc))

    def export(self):
        filename, _ = QFileDialog.getSaveFileName(self, 'Дизайн', 'echoes-stream.json', 'JSON (*.json)')
        if filename:
            try:
                Path(filename).write_text(json.dumps(normalize(self.design), ensure_ascii=False, indent=2), 'utf-8')
            except Exception as exc:                      # noqa: BLE001
                QMessageBox.warning(self, 'Не удалось сохранить дизайн', str(exc))

    def reset(self):
        self.use_template('Компакт')


class StreamEditor(QDialog):
    """Тот же конструктор отдельным окном (правый щелчок по окну стримера)."""

    def __init__(self, win):
        super().__init__(win)
        self.finished.connect(self.deleteLater)
        self.win = win
        self.setWindowTitle('Конструктор окна стримера')
        self.setStyleSheet('QDialog{background:#101013;}')
        self.resize(1100, 760)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 16)
        self.studio = StreamStudio(win)
        v.addWidget(self.studio)

    def done(self, r):
        self.studio.save()
        super().done(r)
