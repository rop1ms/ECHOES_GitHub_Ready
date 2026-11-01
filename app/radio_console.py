"""Small Liquid Glass voice-effects/soundboard cloud anchored to radio controls."""
from pathlib import Path
import shutil
import threading
from PyQt6.QtCore import Qt,QPoint,QTimer
from PyQt6.QtWidgets import (QFrame,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QTabWidget,QWidget,
    QComboBox,QCheckBox,QSlider,QScrollArea,QFileDialog)
from echo_glass import GlassFrame,glass_controls_qss
from duel_settings import tr
from radio_audio import options,PRESETS,decode_clip

def engine(page):
    voice=getattr(page,'_radio_voice',None)
    if voice is None:
        from net_voice import VoiceEngine
        voice=page._radio_voice=VoiceEngine(page.node.settings)
    voice.sink=page.node.radio_voice_out;voice.want={page.node.room_codec()};voice.radio_fx_active=True
    return voice

def play_clip(page,fid):
    rh=page.node.room_host
    if not rh or not rh.get('station') or not page.node.radio_allowed():return
    if not any(s.get('fid')==fid for s in rh.get('sounds',[])):return
    if page.__dict__.get('_radio_sound_loading'):
        page._radio_pending_fid=fid;return
    page._radio_sound_loading=True;page._radio_pending_fid=None
    record=page.node.store.file(fid) or {};path=record.get('path','')
    token=page.__dict__.get('_radio_sound_token',0)+1;page._radio_sound_token=token
    def ready(samples,error):
        page._radio_sound_loading=False
        pending=page.__dict__.pop('_radio_pending_fid',None)
        if page.node.room_host is not rh or page._radio_sound_token!=token:return
        if pending and page.node.room_host is rh:
            play_clip(page,pending);return
        if error:page.toast(error);return
        try:engine(page).play_clip(samples)
        except Exception as error:page.toast(str(error))
    def work():
        try:samples=decode_clip(path);error=''
        except Exception as exc:samples=None;error=str(exc)
        page.win._ui_bridge.call.emit(lambda:ready(samples,error))
    threading.Thread(target=work,daemon=True,name='radio-sound-load').start()

def stop_clip(page):
    page._radio_pending_fid=None
    page._radio_sound_token=page.__dict__.get('_radio_sound_token',0)+1
    voice=getattr(page,'_radio_voice',None)
    if voice is not None:voice.stop_clip()

class RadioConsole(QFrame):
    def __init__(self,page,anchor,host=True):
        super().__init__(page,Qt.WindowType.Popup|Qt.WindowType.FramelessWindowHint)
        self.page=page;self.host=host;self.t=lambda ru,en:tr(page.win,ru,en);t=self.t
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground);self.resize(470,600)
        self.setStyleSheet('QLabel,QCheckBox{color:#eef2fa;background:transparent;}QTabWidget::pane{border:0;}'
            'QTabBar::tab{color:#a7b4c9;background:rgba(255,255,255,12);padding:7px 15px;margin-right:4px;border-radius:9px;}'
            'QTabBar::tab:selected{color:#fff;background:rgba(255,255,255,30);}'
            'QComboBox{color:#eef2fa;background:#171d28;padding:5px;border-radius:9px;}'+glass_controls_qss())
        outer=QVBoxLayout(self);outer.setContentsMargins(0,0,0,0);glass=GlassFrame(radius=22);outer.addWidget(glass)
        body=QVBoxLayout(glass);body.setContentsMargins(18,16,18,16)
        title=QLabel(t('Голос и звуки эфира','Broadcast voice & sounds') if host else t('Звуки станции','Station sounds'));title.setStyleSheet('font-size:20px;font-weight:800;');body.addWidget(title)
        self.tabs=QTabWidget();body.addWidget(self.tabs,1);self.controls={}
        self.save_timer=QTimer(self);self.save_timer.setSingleShot(True);self.save_timer.setInterval(350);self.save_timer.timeout.connect(page.node.save_settings)
        if host:self.build_voice()
        self.build_sounds()
        self.adjustSize();self.resize(470,600)
        point=anchor.mapToGlobal(QPoint(0,-self.height()-8));screen=anchor.screen().availableGeometry()
        self.move(max(screen.left()+8,min(point.x(),screen.right()-self.width()-8)),max(screen.top()+8,point.y()))

    def build_voice(self):
        t=self.t;c=options(self.page.node.settings.get('radio_voice_fx'));root=QWidget();v=QVBoxLayout(root)
        self.enabled=QCheckBox(t('Эффекты голоса в эфире','Voice effects on air'));self.enabled.setChecked(c['enabled']);v.addWidget(self.enabled)
        presets=self.presets=QComboBox()
        for ru,en,_ in PRESETS:presets.addItem(t(ru,en))
        presets.addItem(t('Свои настройки','Custom'))
        presets.setCurrentIndex(self.preset_index(c))
        presets.activated.connect(self.preset);v.addWidget(presets)
        tuning=QHBoxLayout();self.key=QComboBox();self.key.addItems(['C','C#','D','D#','E','F','F#','G','G#','A','A#','B']);self.key.setCurrentIndex(c['key'])
        self.scale=QComboBox();self.scale.addItems([t('Мажор','Major'),t('Минор','Minor'),t('Хроматика','Chromatic')]);self.scale.setCurrentIndex(c['scale'])
        tuning.addWidget(self.key);tuning.addWidget(self.scale);v.addLayout(tuning)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setStyleSheet('QScrollArea{border:0;background:transparent;}');scroll.viewport().setStyleSheet('background:transparent;');content=QWidget();rows=QVBoxLayout(content)
        for key,ru,en,lo,hi in [('autotune','Автотюн','Autotune',0,100),('pitch','Питч · полутоны','Pitch · semitones',-12,12),('formant','Тембр · полутоны','Formant · semitones',-6,6),
            ('robot','Робот / вокодер','Robot / vocoder',0,100),('echo','Эхо','Echo',0,100),('reverb','Реверберация','Reverb',0,100),('chorus','Хорус / двойной голос','Chorus / double voice',0,100),
            ('distortion','Насыщение / перегруз','Saturation / distortion',0,100),('tremolo','Тремоло','Tremolo',0,100),('bitcrush','Биткрашер','Bitcrusher',0,100)]:
            line=QHBoxLayout();label=QLabel(t(ru,en));value=QLabel(str(c[key]));value.setMinimumWidth(44);value.setAlignment(Qt.AlignmentFlag.AlignRight)
            line.addWidget(label,1);line.addWidget(value);rows.addLayout(line)
            slider=QSlider(Qt.Orientation.Horizontal);slider.setRange(lo,hi);slider.setValue(c[key]);self.controls[key]=slider;rows.addWidget(slider)
            slider.valueChanged.connect(lambda number,lab=value:lab.setText(str(number)));slider.valueChanged.connect(self.changed)
        scroll.setWidget(content);v.addWidget(scroll,1)
        self.telephone=QCheckBox(t('Телефонный фильтр','Telephone filter'));self.telephone.setChecked(c['telephone'])
        self.compress=QCheckBox(t('Компрессор ведущего','Broadcast compressor'));self.compress.setChecked(c['compress'])
        v.addWidget(self.telephone);v.addWidget(self.compress)
        note=QLabel(t('Обрабатывается только микрофон этой радиостанции. Автотюн и питч добавляют небольшую задержку обработки.',
            'Processes this station’s microphone only. Autotune and pitch add a short processing delay.'));note.setWordWrap(True);note.setStyleSheet('color:#9baac0;font-size:11px;');v.addWidget(note)
        for widget in [self.enabled,self.telephone,self.compress]:widget.toggled.connect(self.changed)
        self.key.currentIndexChanged.connect(self.changed);self.scale.currentIndexChanged.connect(self.changed)
        self.tabs.addTab(root,t('Голос','Voice'))

    def changed(self):
        c={'enabled':self.enabled.isChecked(),'telephone':self.telephone.isChecked(),'compress':self.compress.isChecked(),
           'key':self.key.currentIndex(),'scale':self.scale.currentIndex(),**{key:slider.value() for key,slider in self.controls.items()}}
        self.page.node.settings['radio_voice_fx']=options(c);self.save_timer.start()
        self.presets.setCurrentIndex(self.preset_index(options(c)))

    @staticmethod
    def preset_index(config):
        return next((i for i,(_,_,preset) in enumerate(PRESETS) if options({**preset,'enabled':i!=0})==config),len(PRESETS))

    def preset(self,index):
        if index>=len(PRESETS):return
        c=options({**PRESETS[index][2],'enabled':index!=0})
        for key,slider in self.controls.items():slider.setValue(c[key])
        self.enabled.setChecked(c['enabled']);self.telephone.setChecked(c['telephone']);self.compress.setChecked(c['compress'])
        self.key.setCurrentIndex(c['key']);self.scale.setCurrentIndex(c['scale']);self.changed()

    def build_sounds(self):
        t=self.t;root=QWidget();v=QVBoxLayout(root)
        note=QLabel(t('До 16 файлов · до 30 с и 10 МиБ каждый. Звук идёт по голосовому каналу эфира; слушатели могут скачать оригинал.',
                     'Up to 16 files · 30 s and 10 MiB each. Clips play through the broadcast voice channel; listeners can download originals.'));note.setWordWrap(True);v.addWidget(note)
        rv=self.page.node.room_view() or {};state=rv.get('state') or {}
        sounds=(self.page.node.room_host or {}).get('sounds',[]) if self.host else (state.get('radio') or {}).get('sounds',[])
        scroll=QScrollArea();scroll.setWidgetResizable(True);content=QWidget();rows=QVBoxLayout(content)
        for meta in sounds:
            line=QHBoxLayout();name=QLabel(meta.get('name',''));name.setWordWrap(True);line.addWidget(name,1)
            button=QPushButton(t('В эфир','Play on air') if self.host else t('Скачать','Download'))
            button.clicked.connect(lambda _=False,m=meta:self.sound_action(m));line.addWidget(button)
            if self.host:
                remove=QPushButton('×');remove.setFixedWidth(30);remove.setStyleSheet('padding:0;');remove.setToolTip(t('Убрать из панели','Remove from soundboard'))
                remove.clicked.connect(lambda _=False,fid=meta['fid']:(self.page.node.call(self.page.node.radio_remove_sound,fid),self.close()));line.addWidget(remove)
            rows.addLayout(line)
        rows.addStretch();scroll.setWidget(content);v.addWidget(scroll,1)
        if self.host:
            gain=QSlider(Qt.Orientation.Horizontal);gain.setRange(0,100);gain.setValue(round(float(self.page.node.settings.get('radio_sound_gain',.7))*100))
            v.addWidget(QLabel(t('Громкость звуков','Soundboard volume')));v.addWidget(gain)
            gain.valueChanged.connect(lambda value:(self.page.node.settings.__setitem__('radio_sound_gain',value/100),self.save_timer.start()))
            row=QHBoxLayout();add=QPushButton(t('Добавить файл','Add file'));add.clicked.connect(self.add_file);row.addWidget(add)
            stop=QPushButton(t('Остановить звук','Stop clip'));stop.clicked.connect(lambda:stop_clip(self.page));row.addWidget(stop);v.addLayout(row)
        self.tabs.addTab(root,t('Звуки','Sounds'))

    def add_file(self):
        path,_=QFileDialog.getOpenFileName(self,self.t('Звук для эфира','Broadcast sound'),'','Audio (*.wav *.mp3 *.flac *.ogg *.opus *.m4a *.aac)')
        if path:self.page.node.call(self.page.node.radio_add_sound,path);self.close()

    def sound_action(self,meta):
        if self.host:play_clip(self.page,meta['fid']);return
        state=self.page.node.file_state(meta['fid']);path=state.get('path','')
        if state.get('state') in ('have','done') and Path(path).is_file():
            destination,_=QFileDialog.getSaveFileName(self,self.t('Сохранить звук','Save sound'),meta['name'])
            if destination:
                try:shutil.copyfile(path,destination)
                except OSError as error:self.page.toast(str(error))
        else:self.page.node.call(self.page.node.fetch,meta,self.page.node.room_guest);self.page.toast(self.t('Звук скачивается. Нажмите ещё раз, чтобы сохранить.','Downloading sound. Click again to save it.'))

    def hideEvent(self,event):
        if self.save_timer.isActive():self.save_timer.stop();self.page.node.save_settings()
        super().hideEvent(event)
