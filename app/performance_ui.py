"""Performance controls with explicit memory/CPU tradeoffs and live RAM counters."""
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,QComboBox,QSpinBox,QCheckBox,QPushButton,QTabWidget
from echo_glass import GlassFrame,glass_controls_qss
from duel_settings import tr
import performance as P


class PerformanceCard(GlassFrame):
    def __init__(self,win):
        super().__init__(radius=22);self.win=win;self.fields={};self.globals={}
        self.setStyleSheet('QLabel,QCheckBox{color:#e9eef9;background:transparent;}QComboBox,QSpinBox{color:#e9eef9;padding:6px;min-height:20px;}'+glass_controls_qss())
        body=QVBoxLayout(self);body.setContentsMargins(24,22,24,24);body.setSpacing(12)
        title=QLabel(self.t('Производительность','Performance'));title.setStyleSheet('font-size:25px;font-weight:800;');body.addWidget(title)
        info=QLabel(self.t('Настрой расход памяти и детализацию. Звук продолжает работать при снижении FPS интерфейса.',
                          'Balance memory use and visual detail. Audio keeps playing when interface FPS is reduced.'))
        info.setWordWrap(True);body.addWidget(info)
        self.memory=QLabel();self.memory.setStyleSheet('color:#a9dfce;');body.addWidget(self.memory)
        self.timer=QTimer(self);self.timer.setInterval(2000);self.timer.timeout.connect(self.refresh_memory)
        preset=QComboBox()
        for key,ru,en in [('custom','Свои настройки','Custom'),('balanced','Баланс','Balanced'),('memory','Меньше ОЗУ','Low memory'),('smooth','Плавность','Smooth')]:preset.addItem(self.t(ru,en),key)
        preset.currentIndexChanged.connect(lambda:self.preset(preset.currentData()));body.addWidget(preset)
        self.tabs=QTabWidget();body.addWidget(self.tabs)
        self.heading('Кадры и движение','Frames and motion')
        self.combo('fps_limit','Предел FPS','FPS limit',[(0,'По монитору','Match display')]+[(v,str(v),str(v)) for v in (30,60,90,120,144,165,240,360)],global_=True)
        self.check('fps_adaptive','Снижать FPS при нагрузке','Adapt FPS under load',global_=True,default=True)
        self.check('background_limit','Снижать FPS в фоне','Reduce FPS in the background')
        self.combo('background_fps','FPS в фоне','Background FPS',[(30,'30','30'),(60,'60','60')])
        self.combo('motion_profile','Профиль движения','Motion profile',[(k,ru,en) for k,ru,en in [('vivid','Живой','Vivid'),('balanced','Сбалансированный','Balanced'),('eco','Экономный','Economy'),('reduced','Минимум движения','Reduced motion')]],global_=True)
        self.check('ui_animations','Переходы интерфейса','Interface transitions',global_=True,default=True)
        self.heading('Бюджеты памяти','Memory budgets')
        for key,ru,en in [('cover_mb','Обложки','Cover artwork'),('rows_mb','Строки треков — общий кэш','Track rows — shared cache'),('buttons_mb','Кнопки','Button rasters'),('qt_mb','Кэш изображений Qt','Qt pixmap cache'),('aura_mb','Кэш огненной ауры','Fire aura cache')]:
            spin=QSpinBox();spin.setRange(1,128);spin.setSuffix(' MiB');self.fields[key]=spin;self.form.addRow(self.t(ru,en),spin)
        self.check('release_hidden','Освобождать кэши скрытых страниц','Release hidden-page caches')
        self.combo('preview_side','Предел стороны фото/видео-GIF','Photo / animated image size limit',[(v,str(v)+' px',str(v)+' px') for v in (1280,1920,2560,3840)])
        self.heading('Эффекты и фоновые задачи','Effects and background work')
        self.combo('aura_quality','Детализация ауры','Aura detail',[(k,ru,en) for k,ru,en in [('low','Экономная','Low'),('balanced','Сбалансированная','Balanced'),('high','Высокая','High')]])
        self.check('duel_particles','Дополнительное конфетти в дуэлях','Extra duel confetti')
        self.check('animated_images','Анимация аватарок и GIF','Animate avatars and GIFs')
        self.check('gif_cache','Хранить все кадры GIF (больше ОЗУ)','Cache every GIF frame (more RAM)')
        self.check('warm_pages','Прогревать скрытые страницы (больше ОЗУ)','Prewarm hidden pages (more RAM)')
        self.check('chorus_prefetch','Заранее анализировать пару в дуэли','Pre-analyze the current duel pair')
        self.combo('chorus_seconds','Бюджет анализа припева','Chorus analysis budget',[(v,str(v)+' '+self.t('сек','sec'),str(v)+' sec') for v in (120,240,360,600)])
        self.check('video_hw','Аппаратное декодирование видео','Hardware video decoding',global_=True,default=True)
        self.check('native_fx','Нативные эффекты C++ (нужен перезапуск)','Native C++ effects (restart required)',global_=True,default=True)
        note=QLabel(self.t('Маленький кэш экономит ОЗУ, но чаще пересоздаёт картинки. Высокая детализация ауры и кэш всех кадров GIF требуют больше памяти.',
                          'Smaller caches save RAM but rebuild images more often. High aura detail and caching every GIF frame use more memory.'))
        note.setWordWrap(True);note.setStyleSheet('color:#9faac0;font-size:11px;');body.addWidget(note)
        buttons=QHBoxLayout();clear=QPushButton(self.t('Освободить кэши сейчас','Release caches now'));clear.clicked.connect(lambda:(P.clear_caches(win),self.refresh_memory()));buttons.addWidget(clear)
        buttons.addStretch();save=QPushButton(self.t('Применить','Apply'));save.setObjectName('AccentBtn');save.clicked.connect(self.apply);buttons.addWidget(save);body.addLayout(buttons)
        self.feedback=QLabel();self.feedback.setWordWrap(True);body.addWidget(self.feedback)
        self.load(P.config(win.settings),win.settings)

    def t(self,ru,en):return tr(self.win,ru,en)
    def heading(self,ru,en):
        page=QWidget();self.form=QFormLayout(page);self.form.setContentsMargins(10,16,10,16);self.form.setSpacing(10)
        self.tabs.addTab(page,self.t(ru,en))
    def combo(self,key,ru,en,choices,global_=False):
        item=QComboBox()
        for value,a,b in choices:item.addItem(self.t(a,b),value)
        (self.globals if global_ else self.fields)[key]=item;self.form.addRow(self.t(ru,en),item)
    def check(self,key,ru,en,global_=False,default=False):
        item=QCheckBox(self.t(ru,en));(self.globals if global_ else self.fields)[key]=item
        item.setProperty('default',default);self.form.addRow(item)
    def load(self,values,globals_):
        for fields,data in [(self.fields,values),(self.globals,globals_)]:
            for key,widget in fields.items():
                value=data.get(key,widget.property('default'))
                if isinstance(widget,QCheckBox):widget.setChecked(bool(value))
                elif isinstance(widget,QSpinBox):widget.setValue(int(value))
                else:
                    if key=='motion_profile' and value is None:value='vivid'
                    if key=='fps_limit' and value is None:value=0
                    index=widget.findData(value)
                    if index<0:widget.addItem(str(value),value);index=widget.count()-1
                    widget.setCurrentIndex(index)
    def preset(self,key):
        if key=='custom':return
        values=dict(P.DEFAULTS);general=dict(self.win.settings,fps_adaptive=True,native_fx=True,video_hw=True)
        if key=='memory':
            values.update(cover_mb=8,rows_mb=4,buttons_mb=3,qt_mb=8,aura_mb=2,aura_quality='low',preview_side=1280,chorus_prefetch=False,chorus_seconds=120)
            general.update(fps_limit=60,motion_profile='eco',ui_animations=True)
        elif key=='smooth':
            values.update(cover_mb=48,rows_mb=24,buttons_mb=12,qt_mb=24,aura_mb=8,aura_quality='high',warm_pages=True)
            general.update(fps_limit=0,motion_profile='vivid',ui_animations=True)
        else:general.update(fps_limit=0,motion_profile='balanced',ui_animations=True)
        self.load(values,general)
    def apply(self):
        def read(widget):
            return widget.isChecked() if isinstance(widget,QCheckBox) else widget.value() if isinstance(widget,QSpinBox) else widget.currentData()
        old=dict(self.win.settings);self.win.settings['performance']={key:read(widget) for key,widget in self.fields.items()}
        self.win.settings.update({key:read(widget) for key,widget in self.globals.items()})
        for key,attr in [('fps_limit','fps_cb'),('fps_adaptive','fps_adapt_cb'),('video_hw','video_hw_cb'),('native_fx','native_fx_cb'),('ui_animations','ui_anim_cb')]:
            widget=getattr(self.win,attr,None)
            if widget is None:continue
            widget.blockSignals(True)
            if isinstance(widget,QComboBox):widget.setCurrentIndex(max(0,widget.findData(self.win.settings[key])))
            else:widget.setChecked(self.win.settings[key])
            widget.blockSignals(False)
        for key in ('video_hw','native_fx'):
            if self.win.settings[key]!=old.get(key,True):self.win._on_perf_toggled(key,self.win.settings[key])
        import motion
        motion.configure(self.win);P.apply(self.win);self.refresh_memory()
        self.feedback.setText(self.t('Применено. Нативные эффекты полностью изменятся после перезапуска.','Applied. Native effect changes take full effect after restart.'))
    def refresh_memory(self):
        working,private=P.memory_usage()
        from raster_budget import ROWS
        import yamusic_theme as Y,paint_cache
        self.memory.setText(self.t('ОЗУ: ','RAM: ')+f'{working/2**20:.0f} MiB · '+self.t('приватная память: ','private: ')+f'{private/2**20:.0f} MiB\n'+
                            self.t('Кэши обложек / строк / кнопок: ','Cover / row / button caches: ')+f'{Y._COVER_BYTES[0]/2**20:.1f} / {ROWS.bytes/2**20:.1f} / {paint_cache._BYTES/2**20:.1f} MiB')
    def showEvent(self,event):super().showEvent(event);self.refresh_memory();self.timer.start()
    def hideEvent(self,event):self.timer.stop();super().hideEvent(event)


def settings_card(win):return PerformanceCard(win)


def open_dialog(win):
    from PyQt6.QtWidgets import QDialog
    dialog=getattr(win,'_performance_dialog',None)
    if dialog is None:
        dialog=QDialog(win);dialog.setWindowTitle(tr(win,'Производительность','Performance'));dialog.resize(860,790)
        dialog.setMinimumSize(640,640);dialog.setSizeGripEnabled(True);dialog.setStyleSheet('QDialog{background:#080b10;}')
        layout=QVBoxLayout(dialog);layout.addWidget(PerformanceCard(win));win._performance_dialog=dialog
    dialog.show();dialog.raise_();dialog.activateWindow()
    return dialog
