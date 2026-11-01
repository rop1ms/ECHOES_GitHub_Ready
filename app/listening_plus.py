"""Local listening scenes, track rules and session tools; all opt-in."""
from __future__ import annotations
import json
import math
import time
from pathlib import Path
from PyQt6.QtWidgets import (QWidget,QFormLayout,QVBoxLayout,QHBoxLayout,QLabel,
    QPushButton,QDoubleSpinBox,QSpinBox,QComboBox,QCheckBox,QLineEdit,QListWidget,
    QFileDialog,QInputDialog,QMessageBox)

class ListeningPlus:
    def __init__(self,tools):
        self.tools=tools;self.win=tools.win
        self.options=self.win.settings.setdefault('listening_scene',{'width':1.,'balance':0.,'mode':'stereo','swap':False})
        self.deadline=0.;self.fade=30.;self.key='';self.pending=False
        self.last_save=time.monotonic();self.gain=1.;self.last_matrix=None
        self.apply()

    def apply(self):
        mixer=getattr(self.win.engine,'mixer',None)
        values=dict(self.options,gain=self.gain)
        signature=(id(mixer),tuple(sorted(values.items())))
        if mixer is not None and hasattr(mixer,'listening') and signature!=self.last_matrix:
            mixer.listening.configure(values);self.last_matrix=signature

    def rule(self,key=None):
        rules=self.win.settings.setdefault('listening_track_rules',{})
        key=self.tools.key() if key is None else key
        if not key:return {}
        if key not in rules:
            if len(rules)>=2000:rules.pop(next(iter(rules)))
            rules[key]={'start':0.,'outro':0.,'resume':False,'position':0.,'rating':0}
        return rules[key]

    def tick(self):
        now=time.monotonic();engine=self.win.engine;key=self.tools.key()
        if key!=self.key:
            self.key=key;self.pending=True;self.last_matrix=None
            if key:
                track=self.tools.current()
                history=self.win.settings.setdefault('listening_diary',[])
                history.append({'path':key,'title':track.get('title',''),'artist':track.get('artist',''),'time':time.time()})
                del history[:-500]
        duration=float(engine.duration or 0);pos=float(engine.get_position() or 0)
        playing=engine.is_playing()
        if key and duration>0 and playing:
            rule=self.rule()
            if self.pending:
                self.pending=False
                target=float(rule.get('start',0))
                if rule.get('resume'):target=max(target,float(rule.get('position',0)))
                if target>=duration-2:target=float(rule.get('start',0))
                if .1<target<duration-1:
                    engine.seek(target);pos=target
            outro=float(rule.get('outro',0))
            if outro>0 and pos>=duration-outro and not self.tools.loop_enabled:
                rule['position']=0.
                if not self.tools.consume_stop():self.win.next_track()
                return
            rule['position']=pos if pos<duration-2 else 0.
        if self.deadline:
            left=self.deadline-now
            self.gain=max(0.,min(1.,left/max(.1,self.fade)))
            if left<=0:
                engine.pause_hard();self.win._set_playing_ui(False)
                self.deadline=0.;self.gain=1.
        self.apply()
        if now-self.last_save>=15:
            self.last_save=now;self.tools.save()

    def blocks_transition(self):
        return float(self.rule().get('outro',0))>0

    def sleep(self,minutes,fade):
        if minutes and getattr(self.win.engine,'mixer',None) is None:
            raise ValueError('Для плавного таймера включите «Свой аудиовывод» в настройках звука.')
        self.fade=max(0.1,float(fade));self.deadline=time.monotonic()+float(minutes)*60 if minutes else 0.
        self.gain=1.;self.apply()

    def quick_loop(self,seconds):
        self.tools.sync_track();engine=self.win.engine
        if not self.tools.key() or not engine.duration:raise ValueError('Сначала откройте трек')
        self.tools.a=max(0.,float(engine.get_position() or 0))
        self.tools.b=min(float(engine.duration),self.tools.a+seconds)
        self.tools.enable_loop(True)

    def import_bookmarks(self,filename):
        if Path(filename).stat().st_size>2_000_000:raise ValueError('Файл больше 2 МБ')
        data=json.loads(Path(filename).read_text('utf-8'))
        if not isinstance(data,list):raise ValueError('Ожидается список закладок')
        valid=[]
        for item in data[:500]:
            if not isinstance(item,dict):continue
            try:pos=float(item.get('pos',-1))
            except (ValueError,TypeError):continue
            if not math.isfinite(pos) or pos<0 or not isinstance(item.get('path'),str):continue
            valid.append({k:str(item.get(k,''))[:500] for k in ('path','title','artist','note')}|{'pos':pos})
        merged=self.tools.bookmarks()+valid;seen=set();result=[]
        for item in merged:
            sig=(item['path'],item['pos'],item.get('note',''))
            if sig not in seen:seen.add(sig);result.append(item)
        self.win.settings['listening_bookmarks']=result[-500:];self.tools.save();self.tools.changed.emit()

    def add_tabs(self,tabs,dialog):
        scene=QWidget();f=QFormLayout(scene)
        desc=QLabel('Настройте пространство под наушники или изучите детали микса. Изменения плавные; исходные файлы не меняются.')
        desc.setWordWrap(True);f.addRow(desc)
        controls=[]
        def change(key,value):self.options[key]=value;self.apply();self.tools.save()
        for key,title,lo,hi,step in [('width','Ширина стерео (1 = оригинал)',0,2,.05),('balance','Баланс (−1 слева, +1 справа)',-1,1,.05)]:
            spin=QDoubleSpinBox();spin.setRange(lo,hi);spin.setSingleStep(step);spin.setValue(self.options.get(key,1 if key=='width' else 0))
            spin.valueChanged.connect(lambda value,key=key:change(key,value));f.addRow(title,spin);controls.append((key,spin))
        mode=QComboBox()
        for title,key in [('Оригинальное стерео','stereo'),('Моно — совместимость','mono'),('Только левый в оба уха','left'),('Только правый в оба уха','right'),('Центр микса (Mid)','mid'),('Боковые детали (Side)','side')]:mode.addItem(title,key)
        mode.setCurrentIndex(max(0,mode.findData(self.options.get('mode','stereo'))))
        mode.currentIndexChanged.connect(lambda:change('mode',mode.currentData()));f.addRow('Режим',mode)
        swap=QCheckBox('Поменять левый и правый каналы');swap.setChecked(self.options.get('swap',False));swap.toggled.connect(lambda on:change('swap',on));f.addRow(swap)
        def sync():
            for key,spin in controls:spin.blockSignals(True);spin.setValue(self.options.get(key,1 if key=='width' else 0));spin.blockSignals(False)
            mode.blockSignals(True);mode.setCurrentIndex(max(0,mode.findData(self.options.get('mode','stereo'))));mode.blockSignals(False)
            swap.blockSignals(True);swap.setChecked(self.options.get('swap',False));swap.blockSignals(False)
            self.apply();self.tools.save()
        scenes=self.win.settings.setdefault('listening_scenes',{})
        presets=QComboBox();presets.addItems(list(scenes))
        row=QHBoxLayout();row.addWidget(presets)
        def save_scene():
            name,ok=QInputDialog.getText(dialog,'Звуковая сцена','Название (до 30 сохранённых сцен):')
            if ok and name.strip():
                name=name.strip()[:80]
                if len(scenes)>=30 and name not in scenes:raise ValueError('Удалите сцену перед сохранением новой')
                scenes[name]=dict(self.options);presets.clear();presets.addItems(list(scenes));presets.setCurrentText(name);self.tools.save()
        def load_scene():
            if presets.currentText() in scenes:self.options.update(scenes[presets.currentText()]);sync()
        def delete_scene():
            scenes.pop(presets.currentText(),None);presets.clear();presets.addItems(list(scenes));self.tools.save()
        for label,fn in [('Сохранить',save_scene),('Применить',load_scene),('Удалить',delete_scene),('Оригинал',lambda:(self.options.update(width=1.,balance=0.,mode='stereo',swap=False),sync()))]:
            b=QPushButton(label);b.clicked.connect(lambda _,fn=fn:dialog.run(fn));row.addWidget(b)
        f.addRow('Мои сцены',row)
        available=getattr(self.win.engine,'mixer',None) is not None
        scene.setEnabled(available)
        tabs.addTab(scene,'Звуковая сцена')
        routine=QWidget();f=QFormLayout(routine)
        status=QLabel('Плавный таймер требует «Свой аудиовывод».' if not available else 'Таймер сна выключен' if not self.deadline else 'Таймер активен: '+str(max(0,round((self.deadline-time.monotonic())/60)))+' мин')
        status.setWordWrap(True);f.addRow(status)
        minutes=QSpinBox();minutes.setRange(1,240);minutes.setValue(30);fade=QSpinBox();fade.setRange(1,300);fade.setValue(round(self.fade))
        f.addRow('Сон через, минут',minutes);f.addRow('Плавное затихание, секунд',fade)
        row=QHBoxLayout()
        for label,fn in [('Запустить',lambda:(self.sleep(minutes.value(),fade.value()),status.setText('Таймер сна активен'))),('Отменить',lambda:(self.sleep(0,30),status.setText('Таймер сна выключен')))]:
            b=QPushButton(label);b.clicked.connect(lambda _,fn=fn:dialog.run(fn));row.addWidget(b)
        f.addRow(row)
        track=self.tools.current();key=self.tools.key()
        label=QLabel('Настройки трека: '+str(track.get('title','Сначала откройте трек')));label.setWordWrap(True);f.addRow(label)
        rule=dict(self.rule());start=QDoubleSpinBox();outro=QDoubleSpinBox()
        for control,field,title in [(start,'start','Начинать с, секунд'),(outro,'outro','Пропускать последние, секунд')]:
            control.setRange(0,3600);control.setValue(rule.get(field,0));f.addRow(title,control)
        resume=QCheckBox('Запоминать и восстанавливать позицию этого трека');resume.setChecked(rule.get('resume',False));f.addRow(resume)
        rating=QSpinBox();rating.setRange(0,5);rating.setSpecialValueText('Без оценки');rating.setValue(rule.get('rating',0));f.addRow('Моя оценка',rating)
        def save_rule():
            if not key or key!=self.tools.key():raise ValueError('Трек сменился. Откройте инструменты заново.')
            if start.value()+outro.value()>=float(self.win.engine.duration or 0)-1:raise ValueError('Между началом и концом должна остаться хотя бы секунда')
            self.rule().update(start=start.value(),outro=outro.value(),resume=resume.isChecked(),rating=rating.value())
            if outro.value() and getattr(self.win.engine,'_gapless_watch',False):self.win.engine._cancel_transition()
            self.tools.save();status.setText('Настройки трека сохранены')
        b=QPushButton('Сохранить настройки этого трека');b.clicked.connect(lambda:dialog.run(save_rule));f.addRow(b)
        row=QHBoxLayout()
        for seconds in (10,30,60):
            b=QPushButton(f'Петля {seconds} с от текущего места');b.clicked.connect(lambda _,seconds=seconds:dialog.run(lambda:self.quick_loop(seconds)));row.addWidget(b)
        f.addRow(row);tabs.addTab(routine,'Сон / трек')
        diary=QWidget();lay=QVBoxLayout(diary);items=QListWidget()
        for item in reversed(self.win.settings.get('listening_diary',[])):
            rule=self.win.settings.get('listening_track_rules',{}).get(item['path'],{})
            items.addItem(time.strftime('%d.%m %H:%M',time.localtime(item['time']))+'  '+item.get('artist','')+' — '+item.get('title','')+'  '+'★'*int(rule.get('rating',0)))
        lay.addWidget(QLabel('Последние 500 открытых треков · данные хранятся локально'));lay.addWidget(items)
        row=QHBoxLayout()
        def export():
            name,_=QFileDialog.getSaveFileName(dialog,'Экспорт дневника','','JSON (*.json)')
            if name:Path(name).write_text(json.dumps(self.win.settings.get('listening_diary',[]),ensure_ascii=False,indent=2),'utf-8')
        def clear():self.win.settings['listening_diary']=[];items.clear();self.tools.save()
        def import_marks():
            name,_=QFileDialog.getOpenFileName(dialog,'Импорт закладок','','JSON (*.json)')
            if name:self.import_bookmarks(name)
        for label,fn in [('Экспорт дневника',export),('Очистить дневник',clear),('Импорт закладок',import_marks)]:
            b=QPushButton(label);b.clicked.connect(lambda _,fn=fn:dialog.run(fn));row.addWidget(b)
        lay.addLayout(row);tabs.addTab(diary,'Дневник')
