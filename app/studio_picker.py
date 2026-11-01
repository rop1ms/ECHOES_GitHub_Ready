"""Searchable instrument/effect picker and local user preset banks."""
from copy import deepcopy
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLineEdit,QListWidget,QListWidgetItem,QPushButton,QLabel,QCheckBox,QInputDialog
import daw_inst as I
import daw_fx as FX
import search_util as SU

class Picker(QDialog):
    def __init__(self,shell):
        super().__init__(shell);self.sh=shell;self.resize(720,560);self.setWindowTitle('Инструменты и эффекты · F8')
        self.entries=[];self.favorites=shell.S().setdefault('favorite_plugins',[])
        for kind,cls in I.GENERATORS.items():
            self.entries.append((f'Инструмент · {cls.NAME}', 'gen',kind,{},cls.NAME))
            for name,params in cls.PRESETS.items():
                if name!='Init':self.entries.append((f'{cls.NAME} · {name}','gen',kind,params,name))
        for kind,cls in FX.FX_TYPES.items():self.entries.append((f'Эффект · {cls.NAME}','fx',kind,{},cls.NAME))
        layout=QVBoxLayout(self);self.search=QLineEdit();self.search.setPlaceholderText('Название инструмента, звук, категория…');layout.addWidget(self.search)
        self.only=QCheckBox('Только избранное');layout.addWidget(self.only)
        self.list=QListWidget();layout.addWidget(self.list)
        self.info=QLabel('Инструмент добавляется новым каналом, эффект — на выбранную дорожку микшера.');self.info.setWordWrap(True);layout.addWidget(self.info)
        row=QHBoxLayout()
        for name,fn in [('Послушать',self.preview),('★ Избранное',self.favorite),('Добавить',self.add),('Закрыть',self.reject)]:
            button=QPushButton(name);button.clicked.connect(fn);row.addWidget(button)
        layout.addLayout(row);self.search.textChanged.connect(self.refresh);self.only.toggled.connect(self.refresh)
        self.list.itemDoubleClicked.connect(self.add);self.search.returnPressed.connect(self.add);self.refresh();self.search.setFocus()
    def refresh(self):
        self.list.clear();words=[SU.variants(w) for w in self.search.text().lower().split()]
        for i,entry in enumerate(self.entries):
            title=entry[0];favorite=title in self.favorites
            if self.only.isChecked() and not favorite:continue
            if not all(any(v in title.lower() for v in values) for values in words):continue
            item=QListWidgetItem(('★ ' if favorite else '')+title);item.setData(Qt.ItemDataRole.UserRole,i);self.list.addItem(item)
        if self.list.count():self.list.setCurrentRow(0)
    def selected(self):
        item=self.list.currentItem();return self.entries[item.data(Qt.ItemDataRole.UserRole)] if item else None
    def favorite(self):
        entry=self.selected()
        if not entry:return
        if entry[0] in self.favorites:self.favorites.remove(entry[0])
        else:self.favorites.append(entry[0])
        self.sh.save_settings();self.refresh()
    def preview(self):
        entry=self.selected()
        if entry and entry[1]=='gen':self.sh.preview_gen(entry[2],dict(entry[3]))
        elif entry:self.info.setText('Эффект можно послушать после добавления на дорожку с играющим звуком.')
    def add(self,*_):
        entry=self.selected()
        if not entry:return
        if entry[1]=='gen':self.sh.add_channel(entry[2],dict(entry[3]),entry[4])
        else:self.sh.add_fx_to_selected(entry[2])
        self.accept()

def bank(panel):
    cls=panel.cls();key=panel.kind+':'+str(getattr(cls,'KIND',None) or getattr(cls,'TYPE',''))
    data=panel.sh.S().setdefault('preset_banks',{}).setdefault(key,{})
    dialog=QDialog(panel);dialog.setWindowTitle('Мои пресеты · '+cls.NAME);dialog.resize(440,400)
    layout=QVBoxLayout(dialog);search=QLineEdit();search.setPlaceholderText('Поиск пресета');layout.addWidget(search)
    items=QListWidget();layout.addWidget(items)
    def refresh():items.clear();items.addItems(sorted(name for name in data if search.text().casefold() in name.casefold()))
    def save():
        name,ok=QInputDialog.getText(dialog,'Сохранить пресет','Название:')
        if ok and name.strip():
            name=name.strip()[:80]
            if len(data)>=200 and name not in data:return
            data[name]=deepcopy(panel.params());panel.sh.save_settings();refresh()
    def apply():
        item=items.currentItem()
        if item:panel._preset((item.text(),deepcopy(data[item.text()])))
    def delete():
        item=items.currentItem()
        if item:data.pop(item.text(),None);panel.sh.save_settings();refresh()
    for title,fn in [('Сохранить текущий звук',save),('Применить выбранный',apply),('Удалить выбранный',delete)]:
        button=QPushButton(title);button.clicked.connect(fn);layout.addWidget(button)
    search.textChanged.connect(refresh);refresh();dialog.exec();dialog.deleteLater()
