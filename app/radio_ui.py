"""Liquid Glass station branding and independent on-air queue editor."""
from collections import OrderedDict
import base64
import copy
import hashlib
from PyQt6.QtCore import Qt, QByteArray, QBuffer, QIODevice, QSize
from PyQt6.QtGui import QPixmap, QPainter, QPainterPath
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QPushButton, QCheckBox, QComboBox, QListWidget, QListWidgetItem, QAbstractItemView,
    QFileDialog, QMessageBox)
from echo_glass import GlassFrame, glass_controls_qss
from duel_settings import tr
from radio_station import MAX_QUEUE, clean_avatar

_AVATARS = OrderedDict()

def avatar_pixmap(value, size=64):
    key = (hashlib.sha256(value.encode()).digest(), size)
    if key in _AVATARS:
        _AVATARS.move_to_end(key); return _AVATARS[key]
    from yamusic_theme import ya_icon
    photo = QPixmap()
    if value and clean_avatar(value): photo.loadFromData(base64.b64decode(value))
    if photo.isNull(): return ya_icon('broadcast_tower', size).pixmap(size, size)
    out = QPixmap(size*2, size*2); out.setDevicePixelRatio(2); out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath(); path.addEllipse(0, 0, size, size); painter.setClipPath(path)
    painter.drawPixmap(0, 0, photo.scaled(size,size,Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                        Qt.TransformationMode.SmoothTransformation)); painter.end()
    _AVATARS[key] = out
    while len(_AVATARS)>24: _AVATARS.popitem(last=False)
    return out

class StationDialog(QDialog):
    def __init__(self, page, config=None):
        super().__init__(page.win)
        self.page = page; self.win = page.win; self.t = lambda ru,en: tr(self.win,ru,en)
        t = self.t; config = copy.deepcopy(config or {})
        self.setWindowTitle(t('Радиостанция · эфирная очередь','Radio station · on-air queue'))
        self.resize(980,760); self.setMinimumSize(760,600); self.setSizeGripEnabled(True)
        self.setStyleSheet('QDialog{background:#090b10;}QLabel,QCheckBox{color:#edf1fa;background:transparent;}'
            'QListWidget{background:rgba(12,17,25,180);color:#edf1fa;border:1px solid #303948;border-radius:12px;}'
            'QListWidget::item{padding:9px;}QListWidget::item:selected{background:#303b50;border-radius:8px;}'
            'QLineEdit,QComboBox{color:#edf1fa;min-height:28px;padding:5px;}' + glass_controls_qss())
        layout=QVBoxLayout(self); glass=GlassFrame(radius=24); layout.addWidget(glass)
        body=QVBoxLayout(glass); body.setContentsMargins(24,22,24,22); body.setSpacing(12)
        title=QLabel(t('Твоя станция. Твой эфир.','Your station. Your broadcast.'))
        title.setStyleSheet('font-size:25px;font-weight:800;'); body.addWidget(title)
        hint=QLabel(t('Слушатели видят название, аватар и очередь станции. Эфир работает отдельно от вашего плеера.',
                     'Listeners see the station name, avatar and queue. The broadcast runs independently of your player.'))
        hint.setWordWrap(True); hint.setStyleSheet('color:#a6b5cb;'); body.addWidget(hint)
        branding=QHBoxLayout(); self.avatar=config.get('avatar',''); self.photo=QLabel(); self.photo.setFixedSize(72,72)
        self._photo(); branding.addWidget(self.photo)
        form=QFormLayout(); self.name=QLineEdit(config.get('name') or 'Echoes Radio'); self.name.setMaxLength(80)
        self.genre=QLineEdit(config.get('genre','')); self.genre.setMaxLength(40)
        form.addRow(t('Название станции','Station name'),self.name); form.addRow(t('Жанр','Genre'),self.genre)
        branding.addLayout(form,1); photos=QVBoxLayout()
        self.button(photos,t('Выбрать аватар','Choose avatar'),self.choose_avatar)
        self.button(photos,t('Убрать аватар','Remove avatar'),lambda:(setattr(self,'avatar',''),self._photo()))
        branding.addLayout(photos); body.addLayout(branding)
        filters=QHBoxLayout(); self.source=QComboBox(); self.source.addItem(t('Вся коллекция','Entire library'),None)
        for name in page.shell.user_playlists(): self.source.addItem(name,name)
        self.search=QLineEdit(); self.search.setPlaceholderText(t('Найти трек или исполнителя','Find a track or artist'))
        filters.addWidget(self.source,1); filters.addWidget(self.search,2); body.addLayout(filters)
        lists=QHBoxLayout(); a=QVBoxLayout(); b=QVBoxLayout()
        a.addWidget(QLabel(t('Коллекция · выберите несколько треков','Library · select multiple tracks')))
        self.library=QListWidget(); self.library.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        a.addWidget(self.library,1)
        row=QHBoxLayout(); self.button(row,t('Добавить выбранные','Add selected'),self.add_selected)
        self.button(row,t('Добавить список','Add this list'),lambda:self.add_tracks(self.available))
        a.addLayout(row); b.addWidget(QLabel(t('Эфирная очередь · перетащите для сортировки','On-air queue · drag to reorder')))
        self.queue=QListWidget(); self.queue.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.queue.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection); b.addWidget(self.queue,1)
        for item in config.get('queue',[]): self.add_item(item)
        row=QHBoxLayout(); self.button(row,t('Выше','Move up'),lambda:self.move(-1))
        self.button(row,t('Ниже','Move down'),lambda:self.move(1)); self.button(row,t('Убрать','Remove'),self.remove)
        b.addLayout(row); lists.addLayout(a,1); lists.addLayout(b,1); body.addLayout(lists,1)
        self.count=QLabel(); body.addWidget(self.count); self.update_count()
        self.repeat=QCheckBox(t('Повторять эфирную очередь','Repeat on-air queue')); self.repeat.setChecked(config.get('repeat',True))
        body.addWidget(self.repeat)
        note=QLabel(t('До 100 треков с известной длительностью. Слушатели используют свою коллекцию, поток или файл ведущего при доступном прямом соединении.',
                     'Up to 100 tracks with a known duration. Listeners use their library, a stream, or the host’s file when a direct connection is available.'))
        note.setWordWrap(True); note.setStyleSheet('color:#97a8c0;font-size:11px;'); body.addWidget(note)
        actions=QHBoxLayout(); actions.addStretch(); self.button(actions,t('Отмена','Cancel'),self.reject)
        self.button(actions,t('Сохранить эфир','Save broadcast') if page.node.room_host else t('Начать эфир','Go live'),self.save)
        body.addLayout(actions)
        self.search.textChanged.connect(self.filter); self.source.currentIndexChanged.connect(self.filter)
        self.library.itemDoubleClicked.connect(lambda item:self.add_tracks([item.data(Qt.ItemDataRole.UserRole)]))
        self.filter()

    def button(self, layout, text, callback):
        b=QPushButton(text); b.setMinimumHeight(34); b.clicked.connect(callback); layout.addWidget(b); return b

    def _photo(self): self.photo.setPixmap(avatar_pixmap(self.avatar,72))

    def choose_avatar(self):
        path,_=QFileDialog.getOpenFileName(self,self.t('Аватар станции','Station avatar'),'','Images (*.png *.jpg *.jpeg *.webp)')
        if not path:return
        from img_load import read_image
        image=read_image(path,144)
        if image is None or image.isNull():return
        side=min(image.width(),image.height())
        image=image.copy((image.width()-side)//2,(image.height()-side)//2,side,side).scaled(144,144,
            Qt.AspectRatioMode.IgnoreAspectRatio,Qt.TransformationMode.SmoothTransformation)
        data=QByteArray(); buffer=QBuffer(data); buffer.open(QIODevice.OpenModeFlag.WriteOnly); image.save(buffer,'JPEG',72)
        self.avatar=clean_avatar(base64.b64encode(bytes(data)).decode()); self._photo()

    def filter(self):
        source=self.source.currentData()
        tracks=self.win.library
        # Playlist entries are usually local paths, resolve without creating streams.
        if source:
            bypath={str(t.get('path')):t for t in self.win.library}
            tracks=[bypath.get(str(item),item) for item in self.win.playlists.get(source,{}).get('tracks',[])]
        query=self.search.text().casefold().strip(); self.available=[]; self.library.clear()
        for track in tracks:
            if not isinstance(track,dict) or not track.get('duration') or query not in f"{track.get('artist','')} {track.get('title','')}".casefold():continue
            self.available.append(track)
            item=QListWidgetItem(self.label(track)); item.setData(Qt.ItemDataRole.UserRole,track); self.library.addItem(item)
            if len(self.available)>=1000:break

    @staticmethod
    def label(track):return f"{track.get('artist','')} — {track.get('title','')}"

    def add_item(self,entry):
        item=QListWidgetItem(self.label(entry.get('track',{}))); item.setData(Qt.ItemDataRole.UserRole,entry); self.queue.addItem(item)

    def add_tracks(self,tracks):
        from net_core import clean_track
        import secrets
        for track in tracks[:MAX_QUEUE-self.queue.count()]:
            meta=clean_track(track)
            if meta and meta['duration']>0:self.add_item({'id':secrets.token_hex(8),'track':meta})
        self.update_count()

    def add_selected(self):self.add_tracks([item.data(Qt.ItemDataRole.UserRole) for item in self.library.selectedItems()])
    def remove(self):
        for item in self.queue.selectedItems():self.queue.takeItem(self.queue.row(item))
        self.update_count()
    def move(self,delta):
        index=self.queue.currentRow(); target=index+delta
        if 0<=index and 0<=target<self.queue.count():
            item=self.queue.takeItem(index);self.queue.insertItem(target,item);self.queue.setCurrentItem(item)
    def update_count(self):self.count.setText(self.t('Треков в эфире','Tracks in broadcast')+f': {self.queue.count()} / {MAX_QUEUE}')
    def save(self):
        if not self.queue.count():
            QMessageBox.information(self,self.windowTitle(),self.t('Добавьте хотя бы один трек.','Add at least one track.'));return
        self.result_config={'name':self.name.text(),'avatar':self.avatar,'genre':self.genre.text(),'repeat':self.repeat.isChecked(),
                            'queue':[self.queue.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.queue.count())]}
        self.accept()
