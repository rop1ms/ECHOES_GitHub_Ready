"""Cross-theme listening workbench. Data stays local and collections are bounded."""
from __future__ import annotations
import ctypes
import json
import math
import statistics
import time
from collections import deque
from pathlib import Path
from PyQt6.QtCore import Qt, QTimer, QObject, pyqtSignal, pyqtSlot, QEvent
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QTabWidget,
    QWidget, QLabel, QPushButton, QLineEdit, QListWidget, QListWidgetItem, QDoubleSpinBox,
    QSpinBox, QCheckBox, QComboBox, QFileDialog, QInputDialog, QMessageBox)


def parse_time(value):
    parts=str(value).strip().replace(',', '.').split(':')
    if not 1<=len(parts)<=3:raise ValueError('Формат: секунды, мин:сек или ч:мин:сек')
    values=[float(p) for p in parts]
    if any(not math.isfinite(v) or v<0 for v in values):raise ValueError('Время должно быть положительным')
    if len(values)>1 and (values[-1]>=60 or any(v>=60 for v in values[1:-1])):
        raise ValueError('Минуты и секунды после двоеточия должны быть меньше 60')
    result=0.
    for number in values:result=result*60+number
    return result


def fmt(seconds):
    seconds=max(0.,float(seconds))
    return f'{int(seconds//60)}:{seconds%60:05.2f}'


def memory_mb():
    class Counters(ctypes.Structure):
        _fields_=[('cb',ctypes.c_ulong),('faults',ctypes.c_ulong),
                  ('peak',ctypes.c_size_t),('working',ctypes.c_size_t),
                  ('paged_peak',ctypes.c_size_t),('paged',ctypes.c_size_t),
                  ('nonpaged_peak',ctypes.c_size_t),('nonpaged',ctypes.c_size_t),
                  ('pagefile',ctypes.c_size_t),('pagefile_peak',ctypes.c_size_t),('private',ctypes.c_size_t)]
    try:
        data=Counters();data.cb=ctypes.sizeof(data)
        kernel=ctypes.windll.kernel32;kernel.GetCurrentProcess.restype=ctypes.c_void_p
        fn=ctypes.windll.psapi.GetProcessMemoryInfo
        fn.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_ulong]
        if not fn(kernel.GetCurrentProcess(),ctypes.byref(data),data.cb):return None
        return {'working':data.working/2**20,'private':data.private/2**20}
    except (AttributeError,OSError):return None


class ListeningTools(QObject):
    changed=pyqtSignal()
    def __init__(self,win):
        super().__init__(win)
        self.win=win
        self.a=self.b=None
        self.loop_enabled=False
        self.loop_limit=0;self.loop_completed=0
        self.stop_after=False
        self.track_key=''
        self._last_seek=0.
        self.taps=deque(maxlen=12)
        self.eq_a=None;self.eq_b=None
        self.session_seconds=0.
        self._last_tick=time.monotonic()
        self.dialog=None
        from listening_plus import ListeningPlus
        self.plus=ListeningPlus(self)
        self.timer=QTimer(self);self.timer.setInterval(100)
        self.timer.timeout.connect(self.tick);self.timer.start()
        self.shortcuts=[]
        for key,fn in [('Ctrl+J',self.open),('Ctrl+Shift+P',self.palette),('Ctrl+Shift+B',self.add_bookmark)]:
            shortcut=QShortcut(QKeySequence(key),win);shortcut.activated.connect(fn);self.shortcuts.append(shortcut)

    def current(self):
        return self.win._current_track() or {}

    def key(self):return str(self.current().get('path',''))

    def save(self):self.win.save_settings()

    def sync_track(self):
        key=self.key()
        if key!=self.track_key:
            self.track_key=key
            self.a=self.b=None
            self.loop_enabled=False;self.loop_completed=0
            self.stop_after=False
            self.changed.emit()

    def tick(self):
        now=time.monotonic();dt=max(0.,min(1.,now-self._last_tick));self._last_tick=now
        self.sync_track()
        self.plus.tick()
        if self.win.engine.is_playing():
            self.session_seconds+=dt
        if self.loop_enabled and self.a is not None and self.b is not None and self.win.engine.is_playing():
            pos=float(self.win.engine.get_position() or 0)
            if pos>=self.b and now-self._last_seek>.3:
                self.loop_completed+=1
                if self.loop_limit and self.loop_completed>=self.loop_limit:
                    self.loop_enabled=False
                else:
                    self.win.engine.seek(self.a)
                    self._last_seek=now
                self.changed.emit()

    def set_point(self,which):
        self.sync_track()
        if not self.key():raise ValueError('Сначала выберите трек')
        value=max(0.,float(self.win.engine.get_position() or 0))
        if which=='a':self.a=value
        else:self.b=value
        self.loop_enabled=False;self.loop_completed=0;self.changed.emit()

    def enable_loop(self,on):
        self.sync_track()
        if on and (self.a is None or self.b is None or self.b-self.a<.25):
            raise ValueError('Установите A и B: B должен быть позже A минимум на 0,25 секунды')
        self.loop_enabled=bool(on);self.loop_completed=0
        if on:
            self.stop_after=False
            self.win.engine.seek(self.a)
        self.changed.emit()

    def toggle_stop_after(self,on):
        self.sync_track()
        if on and not self.key():raise ValueError('Сначала выберите трек')
        self.stop_after=bool(on)
        if on:
            self.loop_enabled=False
            if getattr(self.win.engine,'_gapless_watch',False):
                self.win.engine._cancel_transition()
        self.changed.emit()

    def consume_stop(self):
        if not self.stop_after:return False
        self.stop_after=False;self.loop_enabled=False
        self.win.engine.stop();self.win._set_playing_ui(False)
        self.changed.emit()
        return True

    def blocks_transition(self):
        preview=getattr(self.win,'_duel_preview',None)
        return self.stop_after or self.loop_enabled or self.plus.blocks_transition() or bool(preview and preview==self.key())

    def bookmarks(self):
        return self.win.settings.setdefault('listening_bookmarks',[])

    def add_bookmark(self,note=None):
        if not self.key():return
        if note is None:
            note,ok=QInputDialog.getText(self.win,'Закладка','Заметка к этому моменту:')
            if not ok:return
        track=self.current()
        self.bookmarks().append({'path':self.key(),'title':str(track.get('title',''))[:300],
            'artist':str(track.get('artist',''))[:200], 'pos':max(0.,float(self.win.engine.get_position() or 0)),
            'note':str(note)[:500]})
        self.win.settings['listening_bookmarks']=self.bookmarks()[-500:]
        self.save();self.changed.emit()

    def jump(self,position):
        value=parse_time(position) if isinstance(position,str) else float(position)
        if not math.isfinite(value) or value<0:raise ValueError('Некорректное время')
        duration=float(self.win.engine.duration or 0)
        if not self.key() or duration<=0:raise ValueError('Дождитесь загрузки трека')
        self.win.engine.seek(min(duration,value))

    def tap(self,now=None):
        now=time.monotonic() if now is None else float(now)
        if self.taps and (now-self.taps[-1]>3 or now<=self.taps[-1]):self.taps.clear()
        self.taps.append(now)
        if len(self.taps)<2:return None
        differences=[b-a for a,b in zip(self.taps,list(self.taps)[1:]) if .15<=b-a<=3]
        return round(60/statistics.median(differences),1) if differences else None

    def capture_eq(self,slot):
        values=[self.win.engine.get_eq_band(i) for i in range(10)]
        setattr(self,'eq_'+slot,values)
        self.changed.emit()

    def apply_eq(self,slot):
        values=getattr(self,'eq_'+slot)
        if values is None:raise ValueError('Сначала сохраните настройки в слот '+slot.upper())
        for i,value in enumerate(values):self.win.engine.set_eq_band(i,value)
        # Existing sliders stay synchronized with the engine.
        sliders=getattr(self.win,'_eq_sliders',[])
        labels=getattr(self.win,'_eq_labels',[])
        for i,(slider,value) in enumerate(zip(sliders,values)):
            slider.blockSignals(True);slider.setValue(round(value));slider.blockSignals(False)
            if i<len(labels):labels[i].setText(f'{value:g}')
        self.changed.emit()

    def copy_timestamp(self):
        track=self.current()
        text=f"{track.get('artist','')} — {track.get('title','')} · {fmt(self.win.engine.get_position() or 0)}"
        QApplication.clipboard().setText(text)

    def export_bookmarks(self):
        filename,_=QFileDialog.getSaveFileName(self.win,'Экспорт закладок','','JSON (*.json)')
        if filename:
            try:Path(filename).write_text(json.dumps(self.bookmarks(),ensure_ascii=False,indent=2),'utf-8')
            except OSError as exc:QMessageBox.warning(self.win,'Экспорт закладок',str(exc))

    def open(self):
        for shell in (getattr(self.win, '_ya', None), getattr(getattr(self.win, '_hub', None), 'shell', None)):
            if shell is not None and shell.isVisible():
                shell.show_page('listening')
                return
        self.win._open_hub('listening')

    def palette(self):
        dialog=CommandPalette(self);dialog.finished.connect(dialog.deleteLater);dialog.exec()


from PyQt6.QtWidgets import QApplication
class ToolsDialog(QDialog):
    def __init__(self,tools,parent=None,embedded=False):
        super().__init__(parent if embedded else tools.win, Qt.WindowType.Widget if embedded else Qt.WindowType.Dialog)
        if embedded:
            self.setWindowFlags(Qt.WindowType.Widget)
        self.setObjectName("YaPageInner")
        self.tools=tools
        self._bookmarks_signature=None
        self.setWindowTitle('ECHOES — инструменты слушателя')
        self.resize(740,600)
        layout=QVBoxLayout(self);tabs=QTabWidget();layout.addWidget(tabs)
        player=QWidget();form=QFormLayout(player)
        self.info=QLabel();self.info.setWordWrap(True);form.addRow(self.info)
        row=QHBoxLayout()
        for title,which in [('A — начало','a'),('B — конец','b')]:
            button=QPushButton(title);button.clicked.connect(lambda _,which=which:self.run(lambda:tools.set_point(which)));row.addWidget(button)
        form.addRow(row)
        self.loop=QCheckBox('Повторять фрагмент A–B')
        self.loop.toggled.connect(lambda on:self.run(lambda:tools.enable_loop(on)))
        form.addRow(self.loop)
        count=QSpinBox();count.setRange(0,999);count.setSpecialValueText('Бесконечно')
        count.setValue(tools.loop_limit);count.valueChanged.connect(lambda value:setattr(tools,'loop_limit',value))
        form.addRow('Число проходов',count)
        self.stop=QCheckBox('Остановиться после текущего трека')
        self.stop.toggled.connect(lambda on:self.run(lambda:tools.toggle_stop_after(on)));form.addRow(self.stop)
        seek=QHBoxLayout();self.time=QLineEdit();self.time.setPlaceholderText('1:23.50 или секунды')
        seek.addWidget(self.time);go=QPushButton('Перейти');go.clicked.connect(lambda:self.run(lambda:tools.jump(self.time.text())));seek.addWidget(go)
        form.addRow('Точное время',seek)
        speed=QDoubleSpinBox();speed.setRange(.5,2);speed.setSingleStep(.05);speed.setValue(tools.win.engine.speed);speed.setSuffix(' ×')
        speed.valueChanged.connect(lambda value: tools.win.speed.setValue(round(value*100))
                                   if hasattr(tools.win,'speed') else tools.win.engine.set_speed(value))
        form.addRow('Скорость практики',speed)
        copy=QPushButton('Скопировать название и таймкод');copy.clicked.connect(tools.copy_timestamp);form.addRow(copy)
        tabs.addTab(player,'Фрагмент')
        bookmarks=QWidget();bm=QVBoxLayout(bookmarks);self.markers=QListWidget();bm.addWidget(self.markers)
        buttons=QHBoxLayout()
        for title,fn in [('Добавить здесь',tools.add_bookmark),('Перейти',self.jump_marker),
                         ('Заметка',self.edit_marker),('Удалить',self.remove_marker),('Экспорт JSON',tools.export_bookmarks)]:
            button=QPushButton(title);button.clicked.connect(lambda _,fn=fn:self.run(fn));buttons.addWidget(button)
        bm.addLayout(buttons);self.markers.itemDoubleClicked.connect(lambda _:self.run(self.jump_marker))
        tabs.addTab(bookmarks,'Закладки')
        compare=QWidget();eq=QVBoxLayout(compare)
        eq.addWidget(QLabel('Сохраните два состояния эквалайзера и сравнивайте их во время воспроизведения.'))
        for slot in ('a','b'):
            row=QHBoxLayout()
            for title,fn in [('Запомнить '+slot.upper(),lambda slot=slot:tools.capture_eq(slot)),('Слушать '+slot.upper(),lambda slot=slot:tools.apply_eq(slot))]:
                button=QPushButton(title);button.clicked.connect(lambda _,fn=fn:self.run(fn));row.addWidget(button)
            eq.addLayout(row)
        self.bpm=QLabel('Нажимайте кнопку в ритм музыки')
        tap=QPushButton('TAP — определить BPM');tap.clicked.connect(self.tap)
        eq.addWidget(tap);eq.addWidget(self.bpm);eq.addStretch();tabs.addTab(compare,'EQ / BPM')
        performance=QWidget();perf=QVBoxLayout(performance)
        self.metrics=QLabel();self.metrics.setWordWrap(True);perf.addWidget(self.metrics)
        profile=QComboBox();import motion
        for key,data in motion.PROFILES.items():profile.addItem(data[0],key)
        profile.setCurrentIndex(max(0,profile.findData(tools.win.settings.get('motion_profile','vivid'))))
        profile.currentIndexChanged.connect(lambda _:self.configure('motion_profile',profile.currentData()))
        perf.addWidget(QLabel('Профиль анимаций'));perf.addWidget(profile)
        for key,title,lo,hi in [('motion_intensity','Выразительность',0,1.5),('motion_speed','Скорость переходов',.5,2)]:
            line=QHBoxLayout();line.addWidget(QLabel(title));spin=QDoubleSpinBox();spin.setRange(lo,hi);spin.setSingleStep(.1)
            spin.setValue(tools.win.settings.get(key,1));spin.valueChanged.connect(lambda value,key=key:self.configure(key,value));line.addWidget(spin);perf.addLayout(line)
        release=QPushButton('Освободить кэш изображений');release.clicked.connect(self.release_cache);perf.addWidget(release)
        perf.addWidget(QLabel('Память: реальные показатели процесса Windows. Освобождение кэша не удаляет медиафайлы.'))
        perf.addStretch();tabs.addTab(performance,'Анимации')
        tools.plus.add_tabs(tabs,self)
        tabs.setUsesScrollButtons(True)
        self.note=QLabel('Ctrl+J — инструменты · Ctrl+Shift+P — команды · Ctrl+Shift+B — закладка');self.note.setWordWrap(True);layout.addWidget(self.note)
        if not embedded:
            close=QPushButton('Готово');close.clicked.connect(self.accept);layout.addWidget(close)
        tools.changed.connect(self.refresh)
        self.metric_timer=QTimer(self);self.metric_timer.setInterval(1000);self.metric_timer.timeout.connect(self.update_metrics)
        self.finished.connect(self.detach)
        self.refresh();self.update_metrics()

    def configure(self,key,value):
        self.tools.win.settings[key]=value;self.tools.save()
        import motion;motion.configure(self.tools.win)

    def run(self,fn):
        try:fn()
        except (ValueError,OSError) as exc:self.note.setText(str(exc))
        self.refresh()

    @pyqtSlot()
    def refresh(self):
        t=self.tools
        self.info.setText(f"A: {fmt(t.a) if t.a is not None else '—'}   B: {fmt(t.b) if t.b is not None else '—'}   Проходов: {t.loop_completed}")
        for box,value in [(self.loop,t.loop_enabled),(self.stop,t.stop_after)]:
            box.blockSignals(True);box.setChecked(value);box.blockSignals(False)
        bookmarks=t.bookmarks()
        signature=(id(bookmarks),len(bookmarks))
        if signature!=self._bookmarks_signature:
            self._bookmarks_signature=signature
            selected=self.markers.currentRow();self.markers.clear()
            for index,item in enumerate(bookmarks):
                entry=QListWidgetItem(f"{fmt(item['pos'])}  {item.get('title','')} · {item.get('note','')}")
                entry.setData(Qt.ItemDataRole.UserRole,index);self.markers.addItem(entry)
            if self.markers.count():self.markers.setCurrentRow(max(0,min(selected,self.markers.count()-1)))

    def detach(self, *_):
        self.metric_timer.stop()
        try:
            self.tools.changed.disconnect(self.refresh)
        except (TypeError, RuntimeError):
            pass

    def selected(self):
        item=self.markers.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def jump_marker(self):
        index=self.selected()
        if index is None:return
        item=self.tools.bookmarks()[index]
        if item['path']!=self.tools.key():raise ValueError('Сначала откройте трек этой закладки: '+item.get('title',''))
        self.tools.jump(item['pos'])

    def remove_marker(self):
        index=self.selected()
        if index is not None:
            self.tools.bookmarks().pop(index);self.tools.save();self.tools.changed.emit()

    def edit_marker(self):
        index=self.selected()
        if index is None:return
        item=self.tools.bookmarks()[index]
        note,ok=QInputDialog.getText(self,'Заметка','Текст заметки:',text=item.get('note',''))
        if ok:
            item['note']=note[:500]
            self._bookmarks_signature=None
            self.tools.save();self.tools.changed.emit()

    def tap(self):
        bpm=self.tools.tap();self.bpm.setText(f'{bpm:.1f} BPM' if bpm else 'Ещё один удар…')

    def release_cache(self):
        from PyQt6.QtGui import QPixmapCache
        import yamusic_theme,mem_trim
        yamusic_theme.clear_cover_cache();QPixmapCache.clear();mem_trim.trim(working_set=False)
        self.update_metrics()

    def update_metrics(self):
        import frameclock
        data=memory_mb();clock=frameclock.clock()
        memory=(f"Рабочая память: {data['working']:.1f} МБ · Private: {data['private']:.1f} МБ" if data else 'Показатели памяти недоступны')
        self.metrics.setText(f"{memory}\nЧасы кадров: {clock.measured:.1f} FPS · цель {clock.target:.0f}\nАктивных анимационных таймеров: {clock._users}\nСлушали в этом запуске: {fmt(self.tools.session_seconds)}")

    def showEvent(self,event):
        super().showEvent(event);self.metric_timer.start()

    def hideEvent(self,event):
        self.metric_timer.stop();super().hideEvent(event)


class CommandPalette(QDialog):
    def __init__(self,tools):
        super().__init__(tools.win)
        self.setWindowTitle('ECHOES — команды')
        self.resize(620,480);self.tools=tools
        layout=QVBoxLayout(self);self.search=QLineEdit();self.search.setPlaceholderText('Команда, тема или действие…');layout.addWidget(self.search)
        self.list=QListWidget();layout.addWidget(self.list)
        self.actions=[('Инструменты слушателя · A/B, BPM, закладки',tools.open),
            ('Добавить закладку здесь',tools.add_bookmark),('Скопировать текущий таймкод',tools.copy_timestamp),
            ('Перевод и оформление текста',tools.win._lyrics_options.open),('Масштаб, клавиши и окно',tools.win.open_player_prefs),
            ('Показать текст песни',tools.win._toggle_lyrics_mode),('Окно стримера',tools.win._open_stream_widget),
            ('Конструктор тем',tools.win._open_theme_studio),('Режим обложки',tools.win._toggle_cover_mode)]
        import themes
        self.actions.extend(('Тема: '+name,lambda name=name:tools.win._apply_theme(name)) for name in themes.THEMES)
        self.search.textChanged.connect(self.fill);self.search.returnPressed.connect(self.execute);self.list.itemActivated.connect(lambda _:self.execute())
        self.search.installEventFilter(self)
        self.fill();self.search.setFocus()

    def eventFilter(self,obj,event):
        if obj is self.search and event.type()==QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Down,Qt.Key.Key_Up) and self.list.count():
                delta=1 if event.key()==Qt.Key.Key_Down else -1
                self.list.setCurrentRow((self.list.currentRow()+delta)%self.list.count())
                return True
        return super().eventFilter(obj,event)

    def fill(self):
        import search_util
        self.list.clear()
        for index,(title,fn) in enumerate(self.actions):
            if search_util.matches(self.search.text(),title):
                item=QListWidgetItem(title);item.setData(Qt.ItemDataRole.UserRole,index);self.list.addItem(item)
        if self.list.count():self.list.setCurrentRow(0)

    def execute(self):
        item=self.list.currentItem()
        if item:
            fn=self.actions[item.data(Qt.ItemDataRole.UserRole)][1]
            self.accept();QTimer.singleShot(0,fn)


def install(win):
    if not getattr(win,'_listening_tools',None):win._listening_tools=ListeningTools(win)
