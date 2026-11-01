#pragma once

static QRectF textBounds(const Clip &c,QSizeF canvas){
    auto st=c.style;QFont f(st["font"].toString("Segoe UI"));f.setPixelSize(qMax(6,c.size));f.setBold(st["bold"].toBool(true));f.setItalic(st["italic"].toBool());
    if(st["spacing"].toDouble())f.setLetterSpacing(QFont::PercentageSpacing,100+st["spacing"].toDouble());
    QFontMetricsF fm(f);QString text=st["upper"].toBool()?c.text.toUpper():c.text;double maxw=canvas.width()-60,width=0;int lines=0;
    for(auto para:text.split('\n')){QString line;for(auto word:para.split(' ')){QString next=line.isEmpty()?word:line+" "+word;if(!line.isEmpty()&&fm.horizontalAdvance(next)>maxw){width=qMax(width,fm.horizontalAdvance(line));lines++;line=word;}else line=next;}width=qMax(width,fm.horizontalAdvance(line));lines++;}
    double h=fm.lineSpacing()*lines,pad=st["box"].toBool()?18:6;QString align=st["align"].toString("center");double x=align=="left"?40:align=="right"?canvas.width()-40-width:(canvas.width()-width)/2;
    return QRectF(x-pad,(canvas.height()-h)/2-pad,width+pad*2,h+pad*2);
}
static void applyTextStyle(Clip &c,const TextStyle &t){c.style=t.style;c.size=t.size;if(c.keys.isEmpty()){c.y=t.y;c.x=0;}}

class Editor: public QMainWindow {
    Project project;Timeline *timeline=nullptr;MediaCache *media=nullptr;MediaList *library,*bin;
    QLabel *status=nullptr,*timeLabel=nullptr,*viewerTime=nullptr,*selectionLabel=nullptr,*projectLabel=nullptr,*exportSummary=nullptr,*previewHint=nullptr;QProgressBar *progress;
    QWidget *screen;PreviewFrame *frameView;QStackedWidget *previewStack,*leftStack,*inspectorStack;Scopes *scopes;
    QComboBox *format,*fpsBox,*exportPreset,*exportRes,*exportFps,*exportRange,*exportSpeed,*track,*easing;
    QSlider *exportQuality,*lookAmountSlider;QCheckBox *exportLoud;QPushButton *playButton=nullptr,*cancelButton=nullptr,*exportButton=nullptr,*keyButton=nullptr;QToolButton *bladeButton=nullptr,*snapButton=nullptr,*linkButton=nullptr,*modeSimple=nullptr,*modePro=nullptr;
    QListWidget *effectList,*lookList,*transList,*textList,*layerList;QTableWidget *keyTable;QPlainTextEdit *textEdit;QDoubleSpinBox *transLength;
    QList<QStackedWidget*> clipPages;QButtonGroup *pages,*rails;QAction *proAction=nullptr,*gridAction=nullptr,*safeAction=nullptr;
    struct Rule{QWidget *w;QFormLayout *form;QStringList kinds;bool pro;};QList<Rule> rules;QList<std::function<void(const Clip&)>> fillers;
    QProcess *render=nullptr;QPointer<QProcess> frameProc;QSet<QProcess*> frameJobs;QList<QProcess*> probes;
    QTemporaryDir temp;QScopedPointer<QTemporaryDir> renderDir;QString ffmpeg,ffprobe,projectPath,proxyPath,partialPath,finalPath,pendingExport;ExportOptions pendingOptions;
    Vlc vlc;QList<QJsonObject> undoStack,redoStack;QJsonObject renderSnapshot;QList<Clip> clipboard;
    QTimer timer,autosave,previewTimer,frameTimer,inspectTimer,textTimer;
    struct Import {QString path;double at;int row;};QQueue<Import> importQueue;
    bool filling=false,dirty=false,proxyValid=false,paused=true,wantPlaying=false,startingVlc=false,renderProxy=false,pro=false,sliding=false,dragPending=false,persistMode=true;
    bool closing=false,closePending=false,cancelledPreview=false,probing=false,frameAgain=false;quint64 frameGeneration=0,playGeneration=0;double lastFrameTime=-1,renderDuration=1;
    QByteArray renderLog,progressBuffer;QString autosavePath,dragId;int completedPreviews=0,completedFrames=0,discardedPreviews=0;Pose dragPose{0,0,1,0,1,1};
    QProcess *quick=nullptr,*recorder=nullptr,*job=nullptr;QString chunkFile,nextFile,recordPath,curveChannel="curveM";double chunkFrom=0,chunkLen=0,nextFrom=-1,nextLen=0,recordAt=0;
    bool chunkMode=false,chunkWaiting=false,testing=false;quint64 chunkGeneration=0;QList<qint64> chunkTimes;QSet<QString> stabilizing,stabilizeFailed;QPushButton *recordButton=nullptr;CurveEditor *curves=nullptr;
    const QStringList visualKinds{"video","image","text","color"},mediaKinds{"video","image"},timedKinds{"video","audio"};
    static QList<QPair<QString,QSize>> formats(){return {{"16:9 • 1920×1080 • YouTube",{1920,1080}},{"9:16 • 1080×1920 • TikTok / Reels / Shorts",{1080,1920}},{"1:1 • 1080×1080 • Instagram",{1080,1080}},{EchoesText("4:5 • 1080×1350 • Instagram пост"),{1080,1350}},{"16:9 • 3840×2160 • 4K",{3840,2160}},{"16:9 • 2560×1440 • 2K",{2560,1440}},{EchoesText("21:9 • 2560×1080 • Кино"),{2560,1080}},{"16:9 • 1280×720 • HD",{1280,720}},{EchoesText("4:3 • 1440×1080 • Ретро"),{1440,1080}}};}
public:
    QString python,transcriber,autoPath;
    Editor(QString ff,QString probe,QString vlcDir,QString lib,bool testMode=false):ffmpeg(ff),ffprobe(probe){
        setWindowTitle("ECHOES Montage");resize(1600,960);setMinimumSize(1180,720);setStyleSheet(editorStyle());
        testing=testMode;persistMode=!testMode;pro=persistMode&&QSettings().value("pro",false).toBool();
        media=new MediaCache(ffmpeg,this);timeline=new Timeline(&project,media);
        buildMenus();
        auto root=new QWidget;setCentralWidget(root);auto layout=new QVBoxLayout(root);layout->setContentsMargins(0,0,0,0);layout->setSpacing(0);
        layout->addWidget(buildTop());
        auto vertical=new QSplitter(Qt::Vertical);auto upper=new QSplitter;upper->addWidget(buildLeft());upper->addWidget(buildViewer());upper->addWidget(buildInspector());upper->setStretchFactor(1,1);upper->setSizes({360,820,370});
        vertical->addWidget(upper);vertical->addWidget(buildTimeline());vertical->setStretchFactor(0,3);vertical->setStretchFactor(1,2);vertical->setSizes({560,330});layout->addWidget(vertical,1);
        layout->addWidget(buildBottom());
        media->changed=[this]{timeline->update();refreshBinIcons();};
        timeline->before=[this]{checkpoint();};timeline->changed=[this]{edited();inspect();};timeline->liveChanged=[this]{edited();};timeline->selection=[this]{inspect();};timeline->seek=[this](double t){seekTo(t);};
        timeline->dropped=[this](QString p,double t,int row){enqueue(p,t,row);};timeline->context=[this](QString){contextMenu();};
        timeline->bladed=[this](QString id,double t){bladeAt(id,t);};timeline->emptyMenu=[this](double t,int row){emptyMenu(t,row);};timeline->rulerMenu=[this](double t){rulerMenu(t);};timeline->trackMenu=[this](int row){trackMenu(row);};
        timeline->zoomAt=[this](double k,double x){zoomBy(k,x);};
        setAcceptDrops(true);loadLibrary(lib);vlc.open(vlcDir,screen->winId());
        previewTimer.setSingleShot(true);connect(&previewTimer,&QTimer::timeout,this,[this]{autoPreview();});frameTimer.setSingleShot(true);connect(&frameTimer,&QTimer::timeout,this,[this]{renderFrame();});
        inspectTimer.setSingleShot(true);connect(&inspectTimer,&QTimer::timeout,this,[this]{inspect();});
        connect(&timer,&QTimer::timeout,this,[this]{if(chunkMode&&!paused&&!startingVlc){chunkTick();return;}if(proxyValid&&!paused&&!startingVlc){if(vlc.state()==6){wantPlaying=false;paused=true;timeline->playhead=project.end();updatePlayButton();}else if(vlc.time()>=0)timeline->playhead=vlc.time();timeline->update();updateTime();}});timer.start(40);
        autosavePath=(testMode?temp.path():QStandardPaths::writableLocation(QStandardPaths::AppLocalDataLocation))+"/montage-autosave.json";QDir().mkpath(QFileInfo(autosavePath).absolutePath());connect(&autosave,&QTimer::timeout,this,[this]{if(dirty)write(autosavePath,project.json());});autosave.start(30000);
        applyMode();showPage(0);inspect();updateExportSummary();
        if(!testMode)QTimer::singleShot(200,this,[this]{QFile f(autosavePath);if(f.open(QIODevice::ReadOnly)){auto o=QJsonDocument::fromJson(f.readAll()).object();if(!o["clips"].toArray().isEmpty()&&QMessageBox::question(this,EchoesText("Восстановление"),EchoesText("Восстановить последний автосохранённый монтаж?"))==QMessageBox::Yes){project.load(o);edited();inspect();syncFormat();rebuildBin();}}});
    }
    ~Editor(){closing=true;previewTimer.stop();frameTimer.stop();media->shutdown();for(auto p:frameJobs){p->disconnect(this);p->kill();p->waitForFinished(1000);}if(render){render->disconnect(this);render->kill();render->waitForFinished(2000);}for(auto p:probes){p->disconnect(this);p->kill();p->waitForFinished(1000);}}

    // ---------- Widget factories ----------
    QFrame *panel(){auto f=new QFrame;f->setObjectName("panel");return f;}
    QFrame *strip(QHBoxLayout *&row,QString title={}){auto f=new QFrame;f->setObjectName("strip");f->setFixedHeight(34);row=new QHBoxLayout(f);row->setContentsMargins(10,0,8,0);row->setSpacing(4);if(!title.isEmpty()){auto l=new QLabel(title);l->setObjectName("paneltitle");row->addWidget(l);}return f;}
    QPushButton *button(QBoxLayout*l,QString text,std::function<void()> f,bool primary=false){auto b=new QPushButton(text);if(primary)b->setObjectName("primary");if(l)l->addWidget(b);connect(b,&QPushButton::clicked,this,f);return b;}
    QToolButton *tool(QBoxLayout*l,QIcon icon,QString text,QString tip,std::function<void()> f,bool checkable=false){auto b=new QToolButton;b->setIcon(icon);b->setText(text);b->setToolTip(tip);b->setToolButtonStyle(text.isEmpty()?Qt::ToolButtonIconOnly:Qt::ToolButtonTextBesideIcon);b->setIconSize(QSize(18,18));b->setCheckable(checkable);b->setFocusPolicy(Qt::NoFocus);if(l)l->addWidget(b);connect(b,&QToolButton::clicked,this,f);return b;}
    void rule(QWidget*w,QStringList kinds,bool proOnly=false,QFormLayout*form=nullptr){rules<<Rule{w,form,kinds,proOnly};}
    QFormLayout *group(QVBoxLayout *l,QString title,QStringList kinds={},bool proOnly=false){auto g=new QGroupBox(title);auto f=new QFormLayout(g);f->setLabelAlignment(Qt::AlignLeft|Qt::AlignVCenter);f->setFieldGrowthPolicy(QFormLayout::AllNonFixedFieldsGrow);f->setHorizontalSpacing(10);f->setVerticalSpacing(7);f->setContentsMargins(0,4,0,4);l->addWidget(g);rule(g,kinds,proOnly);return f;}
    QPair<QWidget*,QVBoxLayout*> scrollPage(){auto content=new QWidget;auto l=new QVBoxLayout(content);l->setContentsMargins(12,4,12,12);l->setSpacing(4);auto scroll=new QScrollArea;scroll->setWidgetResizable(true);scroll->setWidget(content);scroll->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);return {scroll,l};}
    QWidget *slider(QFormLayout *f,QString label,double lo,double hi,double step,int decimals,std::function<double(const Clip&)> get,std::function<void(Clip&,double)> set,bool all=false,QString suffix={}){
        auto row=new QWidget;row->setObjectName("slider:"+label);auto h=new QHBoxLayout(row);h->setContentsMargins(0,0,0,0);h->setSpacing(6);
        auto s=new QSlider(Qt::Horizontal);s->setRange(qRound(lo/step),qRound(hi/step));s->setFocusPolicy(Qt::NoFocus);
        auto v=new QDoubleSpinBox;v->setRange(lo,hi);v->setDecimals(decimals);v->setSingleStep(step);v->setKeyboardTracking(false);v->setFixedWidth(78);v->setSuffix(suffix);v->setButtonSymbols(QAbstractSpinBox::NoButtons);v->setAlignment(Qt::AlignRight);
        h->addWidget(s,1);h->addWidget(v);f->addRow(label,row);
        connect(s,&QSlider::sliderPressed,this,[this]{if(editable())checkpoint();sliding=true;});
        connect(s,&QSlider::sliderReleased,this,[this]{sliding=false;inspect();});
        connect(s,&QSlider::valueChanged,this,[=,this](int i){if(filling)return;double val=i*step;{QSignalBlocker b(v);v->setValue(val);}mutate([=](Clip&c){set(c,val);},all,!sliding);});
        connect(v,&QDoubleSpinBox::valueChanged,this,[=,this](double val){if(filling)return;{QSignalBlocker b(s);s->setValue(qRound(val/step));}mutate([=](Clip&c){set(c,val);},all,true);});
        fillers<<[=](const Clip&c){double val=get(c);QSignalBlocker b1(s),b2(v);s->setValue(qRound(val/step));v->setValue(val);};
        return row;
    }
    QCheckBox *check(QFormLayout *f,QString text,std::function<bool(const Clip&)> get,std::function<void(Clip&,bool)> set,bool all=false){auto b=new QCheckBox(text);f->addRow(b);connect(b,&QCheckBox::clicked,this,[=,this](bool on){mutate([=](Clip&c){set(c,on);},all);});fillers<<[=](const Clip&c){QSignalBlocker k(b);b->setChecked(get(c));};return b;}
    QComboBox *choice(QFormLayout *f,QString label,const QList<Preset>&items,std::function<QString(const Clip&)> get,std::function<void(Clip&,QString)> set,bool all=false){
        auto b=new QComboBox;for(auto &i:items)b->addItem(i.glyph.isEmpty()||i.glyph.startsWith('#')?i.name:i.glyph+"  "+i.name,i.id);f->addRow(label,b);
        connect(b,&QComboBox::activated,this,[=,this](int){QString id=b->currentData().toString();mutate([=](Clip&c){set(c,id);},all);});fillers<<[=](const Clip&c){QSignalBlocker k(b);b->setCurrentIndex(qMax(0,b->findData(get(c))));};return b;}
    QPushButton *colorButton(QFormLayout *f,QString label,std::function<QString(const Clip&)> get,std::function<void(Clip&,QString)> set){
        auto b=new QPushButton;b->setFixedHeight(26);f->addRow(label,b);
        connect(b,&QPushButton::clicked,this,[=,this]{auto c=editable();if(!c)return;QColor start(get(*c));QColor picked=QColorDialog::getColor(start.isValid()?start:QColor(Qt::white),this,label);if(picked.isValid())mutate([=](Clip&x){set(x,picked.name());});});
        fillers<<[=](const Clip&c){QColor q(get(c));b->setText(q.isValid()?q.name().toUpper():"—");b->setStyleSheet(q.isValid()?QString("background:%1;color:%2;border:1px solid #0d0d0f;").arg(q.name(),q.lightness()>140?"#111":"#fff"):"");};return b;}
    QDoubleSpinBox *number(QFormLayout *f,QString label,double lo,double hi,double step,std::function<double(const Clip&)> get,std::function<void(Clip&,double)> set){
        auto b=new QDoubleSpinBox;b->setRange(lo,hi);b->setDecimals(3);b->setSingleStep(step);b->setKeyboardTracking(false);f->addRow(label,b);
        connect(b,&QDoubleSpinBox::valueChanged,this,[=,this](double v){if(!filling)mutate([=](Clip&c){set(c,v);});});fillers<<[=](const Clip&c){QSignalBlocker k(b);b->setValue(get(c));};return b;}
    QHBoxLayout *buttonRow(QFormLayout *f){auto w=new QWidget;auto h=new QHBoxLayout(w);h->setContentsMargins(0,0,0,0);h->setSpacing(4);f->addRow(w);return h;}

    // ---------- Layout ----------
    void buildMenus(){
        auto bar=new QMenuBar;setMenuBar(bar);
        auto act=[this](QMenu*m,QString text,QKeySequence key,std::function<void()> f){auto a=m->addAction(text);if(!key.isEmpty())a->setShortcut(key);a->setShortcutContext(Qt::WindowShortcut);connect(a,&QAction::triggered,this,f);return a;};
        auto file=bar->addMenu(EchoesText("Файл"));act(file,EchoesText("Новый проект"),QKeySequence::New,[this]{newProject();});act(file,EchoesText("Открыть…"),QKeySequence::Open,[this]{openProject();});act(file,EchoesText("Сохранить"),QKeySequence::Save,[this]{saveProject();});act(file,EchoesText("Сохранить как…"),QKeySequence("Ctrl+Shift+S"),[this]{QString keep=projectPath;projectPath.clear();if(!saveProject())projectPath=keep;});
        file->addSeparator();act(file,EchoesText("Импорт медиа…"),QKeySequence("Ctrl+I"),[this]{importFiles();});act(file,EchoesText("Импорт субтитров .srt…"),{},[this]{importSrt();});act(file,EchoesText("Экспорт субтитров .srt…"),{},[this]{exportSrt();});
        act(file,EchoesText("Авто-субтитры из речи…"),QKeySequence("Ctrl+Shift+T"),[this]{autoSubtitles();});act(file,EchoesText("Записать голос за кадром"),QKeySequence("Ctrl+R"),[this]{recordVoice();});
        file->addSeparator();act(file,EchoesText("Экспорт видео…"),QKeySequence("Ctrl+E"),[this]{showPage(4);});act(file,EchoesText("Сохранить кадр как PNG…"),QKeySequence("Ctrl+Shift+E"),[this]{saveFrame();});file->addSeparator();act(file,EchoesText("Вернуться в ECHOES Music"),{},[this]{close();});
        auto edit=bar->addMenu(EchoesText("Правка"));act(edit,EchoesText("Отменить"),QKeySequence::Undo,[this]{undo();});act(edit,EchoesText("Повторить"),QKeySequence("Ctrl+Y"),[this]{redo();})->setShortcuts({QKeySequence("Ctrl+Y"),QKeySequence("Ctrl+Shift+Z")});edit->addSeparator();
        act(edit,EchoesText("Вырезать"),QKeySequence::Cut,[this]{copy();remove();});act(edit,EchoesText("Копировать"),QKeySequence::Copy,[this]{copy();});act(edit,EchoesText("Вставить"),QKeySequence::Paste,[this]{paste(timeline->playhead);});act(edit,EchoesText("Дублировать"),QKeySequence("Ctrl+D"),[this]{duplicate();});
        act(edit,EchoesText("Удалить"),QKeySequence(Qt::Key_Delete),[this]{remove();})->setShortcuts({QKeySequence(Qt::Key_Delete),QKeySequence(Qt::Key_Backspace)});act(edit,EchoesText("Удалить со сдвигом"),QKeySequence("Shift+Delete"),[this]{rippleDelete();});
        edit->addSeparator();act(edit,EchoesText("Выделить всё"),QKeySequence::SelectAll,[this]{selectAll();});act(edit,EchoesText("Снять выделение"),QKeySequence(Qt::Key_Escape),[this]{timeline->selectOnly({});inspect();timeline->update();});
        auto clip=bar->addMenu(EchoesText("Клип"));act(clip,EchoesText("Разрезать под курсором"),QKeySequence(Qt::Key_S),[this]{split();})->setShortcuts({QKeySequence(Qt::Key_S),QKeySequence("Ctrl+B")});
        act(clip,EchoesText("Обрезать начало до курсора"),QKeySequence(Qt::Key_Q),[this]{trimToPlayhead(true);});act(clip,EchoesText("Обрезать конец до курсора"),QKeySequence(Qt::Key_W),[this]{trimToPlayhead(false);});clip->addSeparator();
        act(clip,EchoesText("Стоп-кадр"),QKeySequence(Qt::Key_F),[this]{freezeFrame();});act(clip,EchoesText("Воспроизвести задом наперёд"),QKeySequence(Qt::Key_R),[this]{toggleReverse();});
        auto speeds=clip->addMenu(EchoesText("Скорость"));for(double v:{.25,.5,.75,1.,1.5,2.,3.,4.})speeds->addAction(QString("%1×").arg(v),this,[this,v]{setSpeed(v);});clip->addSeparator();
        act(clip,EchoesText("Ключевой кадр"),QKeySequence(Qt::Key_K),[this]{toggleKey();});act(clip,EchoesText("Отделить исходный звук"),{},[this]{extractAudio();});act(clip,EchoesText("Разъединить видео и звук"),{},[this]{unlink();});
        auto tl=bar->addMenu(EchoesText("Таймлайн"));act(tl,EchoesText("Воспроизвести / пауза"),QKeySequence(Qt::Key_Space),[this]{play();});act(tl,EchoesText("Инструмент «Выбор»"),QKeySequence(Qt::Key_A),[this]{setBlade(false);});act(tl,EchoesText("Инструмент «Лезвие»"),QKeySequence(Qt::Key_B),[this]{setBlade(true);});
        act(tl,EchoesText("Привязка вкл/выкл"),QKeySequence(Qt::Key_N),[this]{snapButton->click();});act(tl,EchoesText("Связанное выделение вкл/выкл"),QKeySequence("Ctrl+Shift+L"),[this]{linkButton->click();});tl->addSeparator();
        act(tl,EchoesText("Добавить текст"),QKeySequence(Qt::Key_T),[this]{addText("plain");});act(tl,EchoesText("Добавить корректирующий слой"),{},[this]{addAdjustment();});act(tl,EchoesText("Удалить пустоты на дорожке клипа"),{},[this]{if(auto c=timeline->current())closeGaps(c->track);});tl->addSeparator();
        act(tl,EchoesText("Приблизить"),QKeySequence("Ctrl+="),[this]{zoomBy(1.25,-1);});act(tl,EchoesText("Отдалить"),QKeySequence("Ctrl+-"),[this]{zoomBy(.8,-1);});act(tl,EchoesText("Уместить весь проект"),QKeySequence("Shift+Z"),[this]{fitTimeline();});
        auto marks=bar->addMenu(EchoesText("Метки"));act(marks,EchoesText("Поставить / убрать метку"),QKeySequence(Qt::Key_M),[this]{toggleMarker(timeline->playhead);});act(marks,EchoesText("Следующая метка"),QKeySequence("Shift+Down"),[this]{jumpMarker(1);});act(marks,EchoesText("Предыдущая метка"),QKeySequence("Shift+Up"),[this]{jumpMarker(-1);});
        act(marks,EchoesText("Удалить все метки"),{},[this]{if(project.markers.isEmpty())return;checkpoint();project.markers.clear();edited();});marks->addSeparator();
        act(marks,EchoesText("Точка входа"),QKeySequence(Qt::Key_I),[this]{setRange(true);});act(marks,EchoesText("Точка выхода"),QKeySequence(Qt::Key_O),[this]{setRange(false);});act(marks,EchoesText("Сбросить вход и выход"),QKeySequence("Alt+X"),[this]{checkpoint();project.inPoint=project.outPoint=-1;edited();updateExportSummary();});marks->addSeparator();
        act(marks,EchoesText("Найти биты в музыке"),{},[this]{detectBeats();});act(marks,EchoesText("Разрезать клип по меткам"),{},[this]{cutAtMarkers();});
        auto view=bar->addMenu(EchoesText("Вид"));proAction=act(view,EchoesText("Режим «Профи»"),QKeySequence("Ctrl+Shift+P"),[this]{pro=proAction->isChecked();applyMode();});proAction->setCheckable(true);
        gridAction=act(view,EchoesText("Сетка третей"),QKeySequence("Ctrl+'"),[this]{frameView->guides=gridAction->isChecked();frameView->update();});gridAction->setCheckable(true);
        safeAction=act(view,EchoesText("Безопасная зона"),{},[this]{frameView->safe=safeAction->isChecked();frameView->update();});safeAction->setCheckable(true);view->addSeparator();
        QStringList names{EchoesText("Монтаж"),EchoesText("Анимация"),EchoesText("Цвет"),EchoesText("Звук"),EchoesText("Экспорт")};for(int i=0;i<5;i++)act(view,EchoesText("Страница «")+names[i]+"»",QKeySequence(QString("Shift+%1").arg(i+1)),[this,i]{showPage(i);});
        auto help=bar->addMenu(EchoesText("Справка"));act(help,EchoesText("Горячие клавиши"),QKeySequence(Qt::Key_F1),[this]{hotkeys();});act(help,EchoesText("Как начать"),{},[this]{QMessageBox::information(this,"ECHOES Montage",EchoesText("1. Нажмите «Импорт медиа» или перетащите файлы в окно.\n2. Перетаскивайте клипы по таймлайну, края — обрезка.\n3. Слева — переходы, фильтры, эффекты, текст: выберите клип и нажмите на нужный.\n4. Внизу — страницы: Монтаж, Анимация, Цвет, Звук, Экспорт.\n5. «Экспорт» сохранит готовое видео.\n\nВ режиме «Профи» (Вид → Режим «Профи») открываются кадрирование, режимы наложения, таблица ключей, скопы и эквалайзер."));});
    }
    QWidget *buildTop(){
        QHBoxLayout *row;auto top=strip(row);top->setFixedHeight(42);
        auto brand=new QLabel("ECHOES");brand->setObjectName("brand");row->addWidget(brand);auto sub=new QLabel("MONTAGE");sub->setObjectName("section");row->addWidget(sub);row->addSpacing(14);
        tool(row,glyphIcon("←"),EchoesText("Музыка"),EchoesText("Вернуться в ECHOES Music"),[this]{close();});tool(row,glyphIcon("▤"),EchoesText("Открыть"),EchoesText("Открыть проект • Ctrl+O"),[this]{openProject();});tool(row,glyphIcon("▾"),EchoesText("Сохранить"),EchoesText("Сохранить проект • Ctrl+S"),[this]{saveProject();});
        row->addSpacing(12);projectLabel=new QLabel(EchoesText("Без названия"));projectLabel->setObjectName("muted");row->addWidget(projectLabel);row->addStretch();
        auto seg=new QButtonGroup(this);modeSimple=tool(row,glyphIcon("◯"),EchoesText("Простой"),EchoesText("Понятный интерфейс: только главное"),[this]{pro=false;applyMode();},true);modePro=tool(row,glyphIcon("◆"),EchoesText("Профи"),EchoesText("Все инструменты: кадрирование, наложение, ключи, скопы, эквалайзер"),[this]{pro=true;applyMode();},true);seg->addButton(modeSimple);seg->addButton(modePro);seg->setExclusive(true);
        row->addSpacing(14);format=new QComboBox;for(auto &f:formats())format->addItem(f.first,f.second);format->setToolTip(EchoesText("Формат кадра проекта"));row->addWidget(format);
        connect(format,&QComboBox::activated,this,[this](int){QSize s=format->currentData().toSize();if(s.isEmpty())return;checkpoint();project.width=s.width();project.height=s.height();edited();updateExportSummary();});
        fpsBox=new QComboBox;for(int f:{24,25,30,50,60})fpsBox->addItem(QString(EchoesText("%1 к/с")).arg(f),f);fpsBox->setCurrentIndex(2);fpsBox->setToolTip(EchoesText("Частота кадров проекта"));row->addWidget(fpsBox);
        connect(fpsBox,&QComboBox::activated,this,[this](int){checkpoint();project.fps=fpsBox->currentData().toInt();edited();updateExportSummary();});
        row->addSpacing(8);button(row,EchoesText("Экспорт"),[this]{showPage(4);},true)->setToolTip(EchoesText("Открыть страницу экспорта • Ctrl+E"));
        return top;
    }
    QWidget *buildLeft(){
        auto outer=new QWidget;auto h=new QHBoxLayout(outer);h->setContentsMargins(0,0,0,0);h->setSpacing(0);
        auto railFrame=new QFrame;railFrame->setObjectName("pagebar");railFrame->setFixedWidth(68);auto rail=new QVBoxLayout(railFrame);rail->setContentsMargins(5,8,5,8);rail->setSpacing(3);
        rails=new QButtonGroup(this);leftStack=new QStackedWidget;
        QList<QPair<QString,QString>> items{{"▣",EchoesText("Медиа")},{"♫",EchoesText("Музыка")},{"T",EchoesText("Текст")},{"⇄",EchoesText("Переходы")},{"◐",EchoesText("Фильтры")},{"✦",EchoesText("Эффекты")},{"▤",EchoesText("Слои")}};
        for(int i=0;i<items.size();i++){auto b=new QToolButton;b->setObjectName("rail");b->setText(items[i].second);b->setIcon(glyphIcon(items[i].first,QColor("#c8c8cc"),22));b->setIconSize(QSize(22,22));b->setToolButtonStyle(Qt::ToolButtonTextUnderIcon);b->setCheckable(true);b->setFocusPolicy(Qt::NoFocus);b->setFixedSize(58,54);rail->addWidget(b);rails->addButton(b,i);}
        rail->addStretch();connect(rails,&QButtonGroup::idClicked,leftStack,&QStackedWidget::setCurrentIndex);rails->button(0)->setChecked(true);
        auto body=panel();auto bl=new QVBoxLayout(body);bl->setContentsMargins(0,0,0,0);bl->setSpacing(0);QHBoxLayout *sr;auto head=strip(sr,EchoesText("Медиатека и эффекты"));bl->addWidget(head);bl->addWidget(leftStack,1);
        auto page=[](){auto w=new QWidget;auto v=new QVBoxLayout(w);v->setContentsMargins(10,10,10,10);v->setSpacing(8);return qMakePair(w,v);};
        auto hint=[](QVBoxLayout*v,QString text){auto l=new QLabel(text);l->setObjectName("hint");l->setWordWrap(true);v->addWidget(l);return l;};
        auto search=new QLineEdit;search->setPlaceholderText(EchoesText("Поиск медиа и музыки"));search->setClearButtonEnabled(true);
        connect(search,&QLineEdit::textChanged,this,[this](QString s){for(auto list:{library,bin})for(int i=0;i<list->count();i++)list->item(i)->setHidden(!list->item(i)->text().contains(s,Qt::CaseInsensitive));});
        {auto [w,v]=page();button(v,EchoesText("⤓   Импорт медиа"),[this]{importFiles();},true);v->addWidget(search);bin=new MediaList;bin->setViewMode(QListView::IconMode);bin->setIconSize(QSize(112,63));bin->setGridSize(QSize(124,96));bin->setResizeMode(QListView::Adjust);bin->setMovement(QListView::Static);bin->setWordWrap(true);bin->setDragDropMode(QAbstractItemView::DragOnly);v->addWidget(bin,1);
            hint(v,EchoesText("Перетащите файл на таймлайн или дважды щёлкните — он встанет под курсор."));leftStack->addWidget(w);}
        {auto [w,v]=page();auto s2=new QLineEdit;s2->setPlaceholderText(EchoesText("Поиск в коллекции ECHOES"));s2->setClearButtonEnabled(true);v->addWidget(s2);library=new MediaList;v->addWidget(library,1);hint(v,EchoesText("Музыка из вашей коллекции ECHOES. Перетащите трек на таймлайн."));
            recordButton=button(v,EchoesText("🎙  Записать голос"),[this]{recordVoice();});recordButton->setToolTip(EchoesText("Голос за кадром с микрофона: запись встанет под курсор на новую дорожку • Ctrl+R"));leftStack->addWidget(w);
            connect(s2,&QLineEdit::textChanged,this,[this](QString s){for(int i=0;i<library->count();i++)library->item(i)->setHidden(!library->item(i)->text().contains(s,Qt::CaseInsensitive));});}
        for(auto list:{library,bin})connect(list,&QListWidget::itemDoubleClicked,this,[this](QListWidgetItem *i){enqueue(i->data(Qt::UserRole).toString());});
        {auto [w,v]=page();button(v,EchoesText("＋  Добавить текст"),[this]{addText("plain");},true);textList=new QListWidget;textList->setViewMode(QListView::IconMode);textList->setIconSize(QSize(132,74));textList->setGridSize(QSize(144,100));textList->setResizeMode(QListView::Adjust);textList->setMovement(QListView::Static);
            for(auto &t:textCatalog()){QPixmap pix(264,148);pix.fill(QColor("#101012"));QPainter pp(&pix);Clip c;c.kind="text";c.text=EchoesText("Привет");c.style=t.style;c.size=t.size;paintText(pp,QRectF(pix.rect()),c,264./1080*1.6);pp.end();auto it=new QListWidgetItem(QIcon(pix),t.name,textList);it->setData(Qt::UserRole,t.id);}
            v->addWidget(textList,1);connect(textList,&QListWidget::itemClicked,this,[this](QListWidgetItem*i){auto c=timeline->current();if(c&&c->kind=="text"&&!project.locked(c->track)){for(auto &t:textCatalog())if(t.id==i->data(Qt::UserRole).toString())mutate([t](Clip&x){applyTextStyle(x,t);});}else addText(i->data(Qt::UserRole).toString());});
            button(v,EchoesText("✦  Авто-субтитры из речи"),[this]{autoSubtitles();},true)->setToolTip(EchoesText("Распознать речь в проекте и расставить субтитры (офлайн, модель ECHOES) • Ctrl+Shift+T"));button(v,EchoesText("Импорт субтитров .srt…"),[this]{importSrt();});hint(v,EchoesText("Нажмите на стиль: добавится новый текст, а если выбран текстовый клип — он получит этот стиль."));leftStack->addWidget(w);}
        {auto [w,v]=page();auto f=new QFormLayout;transLength=new QDoubleSpinBox;transLength->setRange(.1,5);transLength->setSingleStep(.1);transLength->setValue(.6);transLength->setSuffix(EchoesText(" с"));f->addRow(EchoesText("Длительность"),transLength);v->addLayout(f);
            transList=new QListWidget;for(auto &t:transitionCatalog()){auto it=new QListWidgetItem(glyphIcon(t.glyph,QColor(pal::accentHi)),t.name,transList);it->setData(Qt::UserRole,t.id);}v->addWidget(transList,1);
            connect(transList,&QListWidget::itemClicked,this,[this](QListWidgetItem*i){if(filling)return;auto c=editable();if(!c||!visual(*c)||c->kind=="adjust"){status->setText(EchoesText("Выберите на таймлайне клип, к началу которого добавить переход."));return;}QString id=i->data(Qt::UserRole).toString();double d=transLength->value();mutate([id,d](Clip&x){x.trans=id;x.transDur=d;});});
            button(v,EchoesText("Применить ко всем стыкам"),[this]{transitionEverywhere();});hint(v,EchoesText("Переход ставится в начало выбранного клипа. Предыдущий клип на той же дорожке плавно сменится выбранным."));leftStack->addWidget(w);}
        {auto [w,v]=page();lookList=new QListWidget;lookList->setIconSize(QSize(40,24));for(auto &l:lookCatalog()){QPixmap pix(80,48);QStringList c=l.glyph.split(',');if(c.size()==2){QPainter pp(&pix);QLinearGradient g(0,0,80,48);g.setColorAt(0,QColor(c[0]));g.setColorAt(1,QColor(c[1]));pp.fillRect(pix.rect(),g);}else pix.fill(QColor("#3a3a40"));auto it=new QListWidgetItem(QIcon(pix),l.name,lookList);it->setData(Qt::UserRole,l.id);}v->addWidget(lookList,1);
            connect(lookList,&QListWidget::itemClicked,this,[this](QListWidgetItem*i){if(filling)return;QString id=i->data(Qt::UserRole).toString();if(!editable()){status->setText(EchoesText("Выберите клип, чтобы применить фильтр."));return;}mutate([id](Clip&c){if(visual(c))c.look=id;},true);});
            auto f=new QFormLayout;lookAmountSlider=new QSlider(Qt::Horizontal);lookAmountSlider->setRange(0,100);lookAmountSlider->setValue(100);f->addRow(EchoesText("Сила фильтра"),lookAmountSlider);v->addLayout(f);
            connect(lookAmountSlider,&QSlider::sliderPressed,this,[this]{if(editable())checkpoint();sliding=true;});connect(lookAmountSlider,&QSlider::sliderReleased,this,[this]{sliding=false;inspect();});
            connect(lookAmountSlider,&QSlider::valueChanged,this,[this](int v){if(!filling)mutate([v](Clip&c){c.lookAmount=v/100.;},true,!sliding);});
            hint(v,EchoesText("Фильтр меняет настроение всего клипа. Точная настройка — на странице «Цвет»."));leftStack->addWidget(w);}
        {auto [w,v]=page();effectList=new QListWidget;for(auto &fx:effectCatalog()){auto it=new QListWidgetItem(glyphIcon(fx.glyph,QColor(pal::accentHi)),fx.name,effectList);it->setData(Qt::UserRole,fx.id);it->setFlags(it->flags()|Qt::ItemIsUserCheckable);it->setCheckState(Qt::Unchecked);}v->addWidget(effectList,1);
            connect(effectList,&QListWidget::itemChanged,this,[this]{if(filling)return;QStringList fx;for(int i=0;i<effectList->count();i++)if(effectList->item(i)->checkState()==Qt::Checked)fx<<effectList->item(i)->data(Qt::UserRole).toString();QString value=fx.isEmpty()?"none":fx.join(',');mutate([value](Clip&c){if(visual(c))c.effect=value;},true);});
            button(v,EchoesText("Сбросить эффекты"),[this]{mutate([](Clip&c){c.effect="none";},true);});hint(v,EchoesText("Отметьте эффекты — они применятся к выбранным клипам сверху вниз."));leftStack->addWidget(w);}
        {auto [w,v]=page();button(v,EchoesText("◧  Корректирующий слой"),[this]{addAdjustment();},true)->setToolTip(EchoesText("Слой, который применяет цвет и эффекты ко всему, что под ним"));
            layerList=new QListWidget;layerList->setViewMode(QListView::IconMode);layerList->setIconSize(QSize(56,56));layerList->setGridSize(QSize(66,70));layerList->setResizeMode(QListView::Adjust);layerList->setMovement(QListView::Static);
            QList<QPair<QString,QString>> swatches{{"#000000",""},{"#ffffff",""},{"#e8822c",""},{"#e5332a",""},{"#ffd400",""},{"#2db86b",""},{"#1f6feb",""},{"#7b3fe4",""},{"#ff4fa3",""},{"#ff8a3d","#b83b5e"},{"#2d6f86","#e0913f"},{"#0f2027","#2c5364"},{"#ff2bd6","#2bffd5"},{"#f6c6d8","#bfe0e8"},{"#141e30","#243b55"}};
            for(auto &s:swatches){QPixmap pix(56,56);QPainter pp(&pix);if(s.second.isEmpty())pp.fillRect(pix.rect(),QColor(s.first));else{QLinearGradient g(0,0,56,56);g.setColorAt(0,QColor(s.first));g.setColorAt(1,QColor(s.second));pp.fillRect(pix.rect(),g);}pp.end();auto it=new QListWidgetItem(QIcon(pix),"",layerList);it->setData(Qt::UserRole,s.first);it->setData(Qt::UserRole+1,s.second);it->setToolTip(s.second.isEmpty()?EchoesText("Цветной фон"):EchoesText("Градиентный фон"));}
            v->addWidget(new QLabel(EchoesText("Цветной фон и градиенты")));v->addWidget(layerList,1);connect(layerList,&QListWidget::itemClicked,this,[this](QListWidgetItem*i){addColorLayer(i->data(Qt::UserRole).toString(),i->data(Qt::UserRole+1).toString());});
            button(v,EchoesText("Цвет фона проекта…"),[this]{QColor c=QColorDialog::getColor(QColor(project.background),this,EchoesText("Фон проекта"));if(!c.isValid())return;checkpoint();project.background=c.name();edited();});
            hint(v,EchoesText("Цветной слой ложится под видео — удобно для вертикальных роликов и заставок."));leftStack->addWidget(w);}
        h->addWidget(railFrame);h->addWidget(body,1);return outer;
    }
    QWidget *buildViewer(){
        auto center=panel();auto cl=new QVBoxLayout(center);cl->setContentsMargins(0,0,0,0);cl->setSpacing(0);
        QHBoxLayout *row;auto head=strip(row,EchoesText("Просмотр"));row->addStretch();previewHint=new QLabel(EchoesText("● авто-обновление"));previewHint->setObjectName("hint");row->addWidget(previewHint);row->addSpacing(10);viewerTime=new QLabel("00:00:00:00");viewerTime->setObjectName("timecode");row->addWidget(viewerTime);cl->addWidget(head);
        previewStack=new QStackedWidget;screen=new QWidget;screen->setAttribute(Qt::WA_NativeWindow);screen->setStyleSheet("background:black;");screen->setMinimumSize(320,180);frameView=new PreviewFrame;frameView->setFocusPolicy(Qt::ClickFocus);
        previewStack->addWidget(frameView);previewStack->addWidget(screen);cl->addWidget(previewStack,1);
        frameView->begin=[this]{auto c=editable();if(!c)return;dragId=c->id;dragPose=poseAt(*c,localAt(*c));dragPending=true;};
        frameView->moved=[this](QPointF d){auto c=editable();if(!c||c->id!=dragId)return;if(dragPending){checkpoint();dragPending=false;}double nx=dragPose.x+d.x(),ny=dragPose.y+d.y(),snap=8/qMax(.01,frameView->ratio());frameView->snapX=std::abs(nx)<snap;frameView->snapY=std::abs(ny)<snap;if(frameView->snapX)nx=0;if(frameView->snapY)ny=0;setKeyed(*c,"x",nx);setKeyed(*c,"y",ny);*c=Clip::read(c->json());edited();timeline->update();updateBox();};
        frameView->scaled=[this](double k){auto c=editable();if(!c||c->id!=dragId)return;if(dragPending){checkpoint();dragPending=false;}setKeyed(*c,"scale",qBound(.05,dragPose.scale*k,10.));*c=Clip::read(c->json());edited();updateBox();};
        frameView->end=[this]{dragId.clear();dragPending=false;inspect();};
        frameView->pick=[this](QPointF at){pickAt(at);};
        frameView->doubleClicked=[this]{auto c=timeline->current();if(c&&c->kind=="text"){showPage(0);textEdit->setFocus();textEdit->selectAll();}};
        frameView->colorPicked=[this](QColor col){mutate([col](Clip&c){c.grade["key"]=col.name();if(!c.grade.contains("keySim"))c.grade["keySim"]=30;});};
        QHBoxLayout *tr;auto transport=strip(tr);transport->setStyleSheet("QFrame#strip{border-top:1px solid #0d0d0f;border-bottom:0;}");transport->setFixedHeight(46);
        tool(tr,glyphIcon("⏮"),"",EchoesText("В начало • Home"),[this]{seekTo(0);});tool(tr,glyphIcon("⇤"),"",EchoesText("Предыдущая склейка • ↑"),[this]{jumpEdit(-1);});tool(tr,glyphIcon("◂"),"",EchoesText("Кадр назад • ←"),[this]{step(-1);});
        playButton=button(tr,"▶",[this]{play();},true);playButton->setFixedSize(64,32);playButton->setStyleSheet("font-size:17px;padding:0;");playButton->setToolTip(EchoesText("Воспроизвести / пауза • Пробел"));
        tool(tr,glyphIcon("▸"),"",EchoesText("Кадр вперёд • →"),[this]{step(1);});tool(tr,glyphIcon("⇥"),"",EchoesText("Следующая склейка • ↓"),[this]{jumpEdit(1);});tool(tr,glyphIcon("⏭"),"",EchoesText("В конец • End"),[this]{seekTo(project.end());});
        tr->addSpacing(10);timeLabel=new QLabel;timeLabel->setObjectName("muted");tr->addWidget(timeLabel);tr->addStretch();
        tool(tr,glyphIcon("#"),"",EchoesText("Сетка третей"),[this]{gridAction->trigger();},true);tool(tr,glyphIcon("▢"),"",EchoesText("Безопасная зона"),[this]{safeAction->trigger();},true);tool(tr,glyphIcon("◉"),EchoesText("Кадр"),EchoesText("Сохранить текущий кадр как PNG"),[this]{saveFrame();});
        cl->addWidget(transport);
        for(auto key:{Qt::Key_Left,Qt::Key_Right,Qt::Key_Up,Qt::Key_Down,Qt::Key_Home,Qt::Key_End})for(QWidget *w:{static_cast<QWidget*>(timeline),static_cast<QWidget*>(frameView)})for(bool shift:{false,true}){
            if(shift&&(key==Qt::Key_Up||key==Qt::Key_Down||key==Qt::Key_Home||key==Qt::Key_End))continue;auto s=new QShortcut(QKeySequence(int(key)|(shift?int(Qt::ShiftModifier):0)),w);s->setContext(Qt::WidgetShortcut);
            connect(s,&QShortcut::activated,this,[this,key,shift]{if(key==Qt::Key_Left)step(shift?-project.fps:-1);else if(key==Qt::Key_Right)step(shift?project.fps:1);else if(key==Qt::Key_Up)jumpEdit(-1);else if(key==Qt::Key_Down)jumpEdit(1);else if(key==Qt::Key_Home)seekTo(0);else seekTo(project.end());});}
        return center;
    }
    QWidget *buildInspector(){
        auto right=panel();right->setMinimumWidth(330);auto rl=new QVBoxLayout(right);rl->setContentsMargins(0,0,0,0);rl->setSpacing(0);QHBoxLayout *row;rl->addWidget(strip(row,EchoesText("Инспектор")));
        selectionLabel=new QLabel;selectionLabel->setWordWrap(true);selectionLabel->setContentsMargins(12,8,12,4);selectionLabel->setStyleSheet("color:#e6e6e8;font-weight:600;");rl->addWidget(selectionLabel);
        inspectorStack=new QStackedWidget;rl->addWidget(inspectorStack,1);
        auto wrap=[this](QWidget *content){auto s=new QStackedWidget;auto empty=new QLabel(EchoesText("Выберите клип на таймлайне,\nчтобы настроить его здесь.\n\nНичего не выбрано — значит,\nможно добавить медиа слева."));empty->setAlignment(Qt::AlignCenter);empty->setObjectName("muted");s->addWidget(empty);s->addWidget(content);clipPages<<s;return s;};
        inspectorStack->addWidget(wrap(pageClip()));inspectorStack->addWidget(wrap(pageAnimation()));inspectorStack->addWidget(wrap(pageColor()));inspectorStack->addWidget(wrap(pageAudio()));inspectorStack->addWidget(pageExport());
        return right;
    }
    QWidget *pageClip(){
        auto [page,l]=scrollPage();
        auto tf=group(l,EchoesText("ТЕКСТ"),{"text"});textEdit=new QPlainTextEdit;textEdit->setFixedHeight(72);textEdit->setPlaceholderText(EchoesText("Введите текст"));tf->addRow(textEdit);textTimer.setSingleShot(true);
        connect(textEdit,&QPlainTextEdit::textChanged,this,[this]{if(!filling)textTimer.start(450);});
        connect(&textTimer,&QTimer::timeout,this,[this]{QString t=textEdit->toPlainText();if(t.trimmed().isEmpty())return;auto c=editable();if(!c||c->text==t)return;mutate([t](Clip&x){if(x.kind=="text"){x.text=t;x.name=QString(t.left(45)).replace('\n',' ');}});});
        fillers<<[this](const Clip&c){if(textEdit->toPlainText()!=c.text&&!textTimer.isActive()){QSignalBlocker b(textEdit);textEdit->setPlainText(c.text);}};
        auto styles=new QComboBox;styles->addItem(EchoesText("Выберите стиль…"),"");for(auto &t:textCatalog())styles->addItem(t.name,t.id);tf->addRow(EchoesText("Стиль"),styles);
        connect(styles,&QComboBox::activated,this,[this,styles]{QString id=styles->currentData().toString();for(auto &t:textCatalog())if(t.id==id)mutate([t](Clip&c){applyTextStyle(c,t);});QSignalBlocker b(styles);styles->setCurrentIndex(0);});
        auto font=new QFontComboBox;tf->addRow(EchoesText("Шрифт"),font);connect(font,&QFontComboBox::currentFontChanged,this,[this](QFont f){if(!filling)mutate([f](Clip&c){c.style["font"]=f.family();});});fillers<<[font](const Clip&c){QSignalBlocker b(font);font->setCurrentFont(QFont(c.style["font"].toString("Segoe UI")));};
        slider(tf,EchoesText("Размер"),8,400,1,0,[](auto&c){return double(c.size);},[](auto&c,double v){c.size=int(v);});
        colorButton(tf,EchoesText("Цвет"),[](auto&c){return c.style["color"].toString("#ffffff");},[](auto&c,QString v){c.style["color"]=v;});
        auto align=new QComboBox;align->addItem(EchoesText("По центру"),"center");align->addItem(EchoesText("Слева"),"left");align->addItem(EchoesText("Справа"),"right");tf->addRow(EchoesText("Выравнивание"),align);connect(align,&QComboBox::activated,this,[this,align]{QString a=align->currentData().toString();mutate([a](Clip&c){c.style["align"]=a;});});fillers<<[align](const Clip&c){QSignalBlocker b(align);align->setCurrentIndex(qMax(0,align->findData(c.style["align"].toString("center"))));};
        check(tf,EchoesText("Жирный"),[](auto&c){return c.style["bold"].toBool(true);},[](auto&c,bool v){c.style["bold"]=v;});check(tf,EchoesText("Курсив"),[](auto&c){return c.style["italic"].toBool();},[](auto&c,bool v){c.style["italic"]=v;});check(tf,EchoesText("ЗАГЛАВНЫЕ БУКВЫ"),[](auto&c){return c.style["upper"].toBool();},[](auto&c,bool v){c.style["upper"]=v;});
        slider(tf,EchoesText("Обводка"),0,30,1,0,[](auto&c){return c.style["stroke"].toDouble();},[](auto&c,double v){c.style["stroke"]=v;});colorButton(tf,EchoesText("Цвет обводки"),[](auto&c){return c.style["strokeColor"].toString("#000000");},[](auto&c,QString v){c.style["strokeColor"]=v;});
        check(tf,EchoesText("Тень"),[](auto&c){return c.style["shadow"].toBool();},[](auto&c,bool v){c.style["shadow"]=v;});check(tf,EchoesText("Неоновое свечение"),[](auto&c){return c.style["glow"].toBool();},[](auto&c,bool v){c.style["glow"]=v;});
        check(tf,EchoesText("Плашка под текстом"),[](auto&c){return c.style["box"].toBool();},[](auto&c,bool v){c.style["box"]=v;});colorButton(tf,EchoesText("Цвет плашки"),[](auto&c){return c.style["boxColor"].toString("#000000");},[](auto&c,QString v){c.style["boxColor"]=v;});
        slider(tf,EchoesText("Плашка"),0,100,1,0,[](auto&c){return c.style["boxOpacity"].toDouble(60);},[](auto&c,double v){c.style["boxOpacity"]=v;},false," %");
        rule(slider(tf,EchoesText("Межбуквенный"),-20,100,1,0,[](auto&c){return c.style["spacing"].toDouble();},[](auto&c,double v){c.style["spacing"]=v;},false," %"),{},true,tf);
        auto cf=group(l,EchoesText("ЦВЕТНОЙ СЛОЙ"),{"color"});colorButton(cf,EchoesText("Цвет"),[](auto&c){return c.style["color"].toString("#1f6feb");},[](auto&c,QString v){c.style["color"]=v;});
        check(cf,EchoesText("Градиент"),[](auto&c){return QColor(c.style["color2"].toString()).isValid();},[](auto&c,bool v){if(v)c.style["color2"]="#000000";else c.style.remove("color2");});
        colorButton(cf,EchoesText("Второй цвет"),[](auto&c){return c.style["color2"].toString();},[](auto&c,QString v){c.style["color2"]=v;});slider(cf,EchoesText("Угол"),0,360,1,0,[](auto&c){return c.style["angle"].toDouble(90);},[](auto&c,double v){c.style["angle"]=v;},false,"°");
        auto tr=group(l,EchoesText("ПРЕОБРАЗОВАНИЕ"),visualKinds);
        {auto fitRow=buttonRow(tr);QList<QPair<QString,QString>> fits{{"fit",EchoesText("Вписать")},{"fill",EchoesText("Заполнить")},{"stretch",EchoesText("Растянуть")},{"blur",EchoesText("Размытый фон")}};auto grp=new QButtonGroup(this);
            for(auto &f:fits){auto b=new QPushButton(f.second);b->setCheckable(true);b->setToolTip(f.first=="blur"?EchoesText("Видео по центру, по краям — его размытая копия (как в CapCut)"):f.first=="fill"?EchoesText("Закрыть весь кадр, обрезав края"):"");grp->addButton(b);fitRow->addWidget(b);QString id=f.first;connect(b,&QPushButton::clicked,this,[this,id]{mutate([id](Clip&c){if(c.kind=="video"||c.kind=="image")c.fit=id;});});fillers<<[b,id](const Clip&c){QSignalBlocker k(b);b->setChecked(c.fit==id);};}
            rule(fitRow->parentWidget(),mediaKinds,false,tr);}
        slider(tr,EchoesText("Масштаб"),5,1000,1,0,[this](auto&c){return poseAt(c,localAt(c)).scale*100;},[this](auto&c,double v){setKeyed(c,"scale",v/100);},false," %");
        slider(tr,EchoesText("Позиция X"),-3840,3840,1,0,[this](auto&c){return poseAt(c,localAt(c)).x;},[this](auto&c,double v){setKeyed(c,"x",v);});
        slider(tr,EchoesText("Позиция Y"),-3840,3840,1,0,[this](auto&c){return poseAt(c,localAt(c)).y;},[this](auto&c,double v){setKeyed(c,"y",v);});
        slider(tr,EchoesText("Поворот"),-360,360,1,0,[this](auto&c){return poseAt(c,localAt(c)).rotation;},[this](auto&c,double v){setKeyed(c,"rotation",v);},false,"°");
        slider(tr,EchoesText("Непрозрачность"),0,100,1,0,[this](auto&c){return poseAt(c,localAt(c)).opacity*100;},[this](auto&c,double v){setKeyed(c,"opacity",v/100);},false," %");
        {auto r=buttonRow(tr);auto flip=[this](QString fx){mutate([fx](Clip&c){QStringList list=c.effect=="none"?QStringList():c.effect.split(',');if(list.contains(fx))list.removeAll(fx);else list<<fx;c.effect=list.isEmpty()?"none":list.join(',');});};
            button(r,EchoesText("⇋ Отразить"),[flip]{flip("mirror");});button(r,EchoesText("⇵ Перевернуть"),[flip]{flip("flip");});button(r,EchoesText("Сброс"),[this]{mutate([](Clip&c){c.x=c.y=0;c.scale=1;c.rotation=0;c.opacity=1;c.keys={};c.motion=false;});});}
        auto mk=group(l,EchoesText("МАСКА"),visualKinds);QList<Preset> shapes{{"",EchoesText("Нет"),"",""},{"ellipse",EchoesText("Круг / овал"),"",""},{"rect",EchoesText("Прямоугольник"),"",""}};
        choice(mk,EchoesText("Форма"),shapes,[](auto&c){return c.mask["shape"].toString();},[](auto&c,QString v){if(v.isEmpty()){c.mask={};return;}if(c.mask.isEmpty())c.mask=QJsonObject{{"w",60},{"h",60},{"feather",15}};c.mask["shape"]=v;});
        for(auto m:QList<std::tuple<QString,QString,double,double,double>>{{"x",EchoesText("Центр X"),-50,50,0},{"y",EchoesText("Центр Y"),-50,50,0},{"w",EchoesText("Ширина"),1,200,60},{"h",EchoesText("Высота"),1,200,60},{"feather",EchoesText("Растушёвка"),0,100,15},{"round",EchoesText("Скругление"),0,100,0},{"angle",EchoesText("Поворот маски"),-180,180,0}}){
            QString key=std::get<0>(m);double def=std::get<4>(m);slider(mk,std::get<1>(m),std::get<2>(m),std::get<3>(m),1,0,[key,def](auto&c){return c.mask[key].toDouble(def);},[key](auto&c,double v){c.mask[key]=v;},false,key=="angle"?"°":" %");}
        check(mk,EchoesText("Инвертировать маску"),[](auto&c){return c.mask["invert"].toBool();},[](auto&c,bool v){c.mask["invert"]=v;});
        auto crop=group(l,EchoesText("КАДРИРОВАНИЕ"),mediaKinds,true);
        slider(crop,EchoesText("Слева"),0,45,1,0,[](auto&c){return c.cropL*100;},[](auto&c,double v){c.cropL=v/100;},false," %");slider(crop,EchoesText("Справа"),0,45,1,0,[](auto&c){return c.cropR*100;},[](auto&c,double v){c.cropR=v/100;},false," %");
        slider(crop,EchoesText("Сверху"),0,45,1,0,[](auto&c){return c.cropT*100;},[](auto&c,double v){c.cropT=v/100;},false," %");slider(crop,EchoesText("Снизу"),0,45,1,0,[](auto&c){return c.cropB*100;},[](auto&c,double v){c.cropB=v/100;},false," %");
        auto comp=group(l,EchoesText("НАЛОЖЕНИЕ"),visualKinds,true);choice(comp,EchoesText("Режим"),blendCatalog(),[](auto&c){return c.blend;},[](auto&c,QString v){c.blend=v;});
        auto sp=group(l,EchoesText("СКОРОСТЬ"),timedKinds);
        {auto r=buttonRow(sp);for(double v:{.25,.5,1.,1.5,2.,4.})button(r,QString("%1×").arg(v),[this,v]{setSpeed(v);});}
        {auto ramp=new QComboBox;ramp->addItem(EchoesText("Кривая скорости…"));for(auto &r:ramps())ramp->addItem(r.first);ramp->setToolTip(EchoesText("Смена скорости внутри клипа: замедление в середине, рывки и т. п."));sp->addRow(EchoesText("Кривая"),ramp);
            connect(ramp,&QComboBox::activated,this,[this,ramp](int i){if(i>0){auto r=ramps()[i-1];speedRamp(r.first,r.second);}QSignalBlocker b(ramp);ramp->setCurrentIndex(0);});}
        slider(sp,EchoesText("Скорость"),.1,10,.05,2,[](auto&c){return c.speed;},[](auto&c,double v){double content=c.duration*c.speed;c.speed=v;c.duration=content/v;},false,"×");
        {auto rev=new QCheckBox(EchoesText("Задом наперёд (реверс)"));sp->addRow(rev);connect(rev,&QCheckBox::clicked,this,[this,rev]{toggleReverse();QSignalBlocker b(rev);auto c=timeline->current();rev->setChecked(c&&c->reverse);});fillers<<[rev](const Clip&c){QSignalBlocker b(rev);rev->setChecked(c.reverse);};}
        auto st=group(l,EchoesText("СТАБИЛИЗАЦИЯ"),{"video"});slider(st,EchoesText("Сила"),0,100,1,0,[](auto&c){return c.stabilize;},[](auto&c,double v){c.stabilize=v;},false," %");
        {auto h=new QLabel(EchoesText("Убирает дрожание камеры. В первый раз видео анализируется в фоне."));h->setObjectName("hint");h->setWordWrap(true);st->addRow(h);}
        auto fd=group(l,EchoesText("ПЛАВНОСТЬ"),{});
        slider(fd,EchoesText("Плавное начало"),0,10,.1,1,[](auto&c){return c.fadeIn;},[](auto&c,double v){c.fadeIn=v;},true,EchoesText(" с"));slider(fd,EchoesText("Плавный конец"),0,10,.1,1,[](auto&c){return c.fadeOut;},[](auto&c,double v){c.fadeOut=v;},true,EchoesText(" с"));
        auto tm=group(l,EchoesText("ВРЕМЯ И ДОРОЖКА"),{},true);track=new QComboBox;tm->addRow(EchoesText("Дорожка"),track);connect(track,&QComboBox::activated,this,[this]{moveToTrack(track->currentData().toInt());});
        number(tm,EchoesText("Начало, с"),0,86400,.05,[](auto&c){return c.start;},[](auto&c,double v){c.start=v;});number(tm,EchoesText("Вход в исходник, с"),0,86400,.05,[](auto&c){return c.in;},[](auto&c,double v){c.in=v;});number(tm,EchoesText("Длина, с"),.04,86400,.05,[](auto&c){return c.duration;},[](auto&c,double v){c.duration=v;});
        auto ac=group(l,EchoesText("ДЕЙСТВИЯ"),{});
        {auto r=buttonRow(ac);rule(button(r,EchoesText("❄ Стоп-кадр"),[this]{freezeFrame();}),{"video"});rule(button(r,EchoesText("✂ Разрезать"),[this]{split();}),{});}
        {auto r=buttonRow(ac);rule(button(r,EchoesText("Отделить звук"),[this]{extractAudio();}),{"video"});rule(button(r,EchoesText("Разъединить"),[this]{unlink();}),{"video","audio"});}
        l->addStretch();return page;
    }
    QWidget *pageAnimation(){
        auto [page,l]=scrollPage();QStringList anim{"video","image","text","color","adjust"};
        auto ia=group(l,EchoesText("ПОЯВЛЕНИЕ"),anim);choice(ia,EchoesText("Анимация"),inCatalog(),[](auto&c){return c.inAnim;},[](auto&c,QString v){c.inAnim=v;},true);slider(ia,EchoesText("Длительность"),.1,5,.05,2,[](auto&c){return c.inDur;},[](auto&c,double v){c.inDur=v;},true,EchoesText(" с"));
        auto oa=group(l,EchoesText("ИСЧЕЗНОВЕНИЕ"),anim);choice(oa,EchoesText("Анимация"),outCatalog(),[](auto&c){return c.outAnim;},[](auto&c,QString v){c.outAnim=v;},true);slider(oa,EchoesText("Длительность"),.1,5,.05,2,[](auto&c){return c.outDur;},[](auto&c,double v){c.outDur=v;},true,EchoesText(" с"));
        auto ta=group(l,EchoesText("ПЕРЕХОД В НАЧАЛЕ КЛИПА"),visualKinds);choice(ta,EchoesText("Переход"),transitionCatalog(),[](auto&c){return c.trans;},[](auto&c,QString v){c.trans=v;});slider(ta,EchoesText("Длительность"),.1,5,.05,2,[](auto&c){return c.transDur;},[](auto&c,double v){c.transDur=v;},false,EchoesText(" с"));
        {auto r=buttonRow(ta);button(r,EchoesText("Применить ко всем стыкам"),[this]{transitionEverywhere();});}
        auto kb=group(l,EchoesText("ПЛАВНЫЙ ЗУМ И ПАНОРАМА"),visualKinds);
        {auto r=buttonRow(kb);button(r,EchoesText("⊕ Приблизить"),[this]{kenBurns("in");});button(r,EchoesText("⊖ Отдалить"),[this]{kenBurns("out");});}
        {auto r=buttonRow(kb);button(r,"←",[this]{kenBurns("left");})->setToolTip(EchoesText("Панорама влево"));button(r,"→",[this]{kenBurns("right");})->setToolTip(EchoesText("Панорама вправо"));button(r,"↑",[this]{kenBurns("up");})->setToolTip(EchoesText("Панорама вверх"));button(r,"↓",[this]{kenBurns("down");})->setToolTip(EchoesText("Панорама вниз"));}
        auto kf=group(l,EchoesText("КЛЮЧЕВЫЕ КАДРЫ"),{});auto help=new QLabel(EchoesText("Поставьте курсор, нажмите ◆ и меняйте масштаб, позицию, поворот, прозрачность или громкость — между ключами всё плавно анимируется."));help->setObjectName("hint");help->setWordWrap(true);kf->addRow(help);
        {auto r=buttonRow(kf);keyButton=button(r,EchoesText("◆ Ключ"),[this]{toggleKey();},true);keyButton->setToolTip(EchoesText("Поставить / убрать ключевой кадр под курсором • K"));button(r,"◀",[this]{jumpKey(-1);})->setToolTip(EchoesText("Предыдущий ключ"));button(r,"▶",[this]{jumpKey(1);})->setToolTip(EchoesText("Следующий ключ"));button(r,EchoesText("Удалить все"),[this]{mutate([](Clip&c){c.keys={};});});}
        easing=new QComboBox;for(auto e:QList<QPair<QString,QString>>{{EchoesText("Равномерно"),"linear"},{EchoesText("Плавно"),"smooth"},{EchoesText("Ускорение"),"in"},{EchoesText("Замедление"),"out"}})easing->addItem(e.first,e.second);kf->addRow(EchoesText("Интерполяция"),easing);
        connect(easing,&QComboBox::activated,this,[this]{QString e=easing->currentData().toString();mutate([e](Clip&c){c.easing=e;});});fillers<<[this](const Clip&c){QSignalBlocker b(easing);easing->setCurrentIndex(qMax(0,easing->findData(c.easing)));};
        keyTable=new QTableWidget(0,6);keyTable->setHorizontalHeaderLabels({EchoesText("Сек"),"X","Y",EchoesText("Масш.%"),EchoesText("Пов.°"),EchoesText("Прозр.%")});keyTable->horizontalHeader()->setSectionResizeMode(QHeaderView::Stretch);keyTable->verticalHeader()->hide();keyTable->setSelectionBehavior(QAbstractItemView::SelectRows);keyTable->setMinimumHeight(150);kf->addRow(keyTable);rule(keyTable,{},true,kf);
        connect(keyTable,&QTableWidget::cellChanged,this,[this]{editKeys();});
        connect(keyTable,&QTableWidget::cellClicked,this,[this](int row,int){auto c=timeline->current();if(c&&keyTable->item(row,0)){int index=keyTable->item(row,0)->data(Qt::UserRole).toInt();if(index<c->keys.size())seekTo(c->start+c->keys[index].toObject()["time"].toDouble()-c->animationOffset);}});
        auto mv=group(l,EchoesText("ПРОСТОЕ ДВИЖЕНИЕ"),visualKinds,true);check(mv,EchoesText("Движение от начала к концу"),[](auto&c){return c.motion;},[](auto&c,bool v){if(v&&!c.motion){c.keys={};c.animationOffset=0;c.motionSpan=0;}c.motion=v;});
        slider(mv,EchoesText("Конечный X"),-3840,3840,1,0,[](auto&c){return double(c.endx);},[](auto&c,double v){c.endx=int(v);});slider(mv,EchoesText("Конечный Y"),-3840,3840,1,0,[](auto&c){return double(c.endy);},[](auto&c,double v){c.endy=int(v);});
        l->addStretch();return page;
    }
    QWidget *pageColor(){
        auto [page,l]=scrollPage();QStringList kinds{"video","image","text","color","adjust"};
        auto lk=group(l,EchoesText("ФИЛЬТР"),kinds);choice(lk,EchoesText("Фильтр"),lookCatalog(),[](auto&c){return c.look;},[](auto&c,QString v){c.look=v;},true);slider(lk,EchoesText("Сила"),0,100,1,0,[](auto&c){return c.lookAmount*100;},[](auto&c,double v){c.lookAmount=v/100;},true," %");
        auto cc=group(l,EchoesText("КОРРЕКЦИЯ"),kinds);
        struct G{QString key,label;double lo,hi;bool pro;};
        for(auto g:QList<G>{{"exposure",EchoesText("Экспозиция"),-100,100,false},{"brightness",EchoesText("Яркость"),-100,100,false},{"contrast",EchoesText("Контраст"),-100,100,false},{"saturation",EchoesText("Насыщенность"),-100,100,false},{"vibrance",EchoesText("Сочность"),-100,100,false},{"temperature",EchoesText("Температура"),-100,100,false},{"tint",EchoesText("Оттенок"),-100,100,false},{"hue",EchoesText("Цветовой тон"),-100,100,false},{"shadows",EchoesText("Тени"),-100,100,false},{"highlights",EchoesText("Света"),-100,100,false},{"gamma",EchoesText("Гамма"),-100,100,true},{"fade",EchoesText("Выцветание"),0,100,true},{"sharpen",EchoesText("Резкость"),0,100,false},{"blur",EchoesText("Размытие"),0,100,true},{"vignette",EchoesText("Виньетка"),0,100,false},{"grain",EchoesText("Зерно"),0,100,true}}){
            QString key=g.key;auto row=slider(cc,g.label,g.lo,g.hi,1,0,[key](auto&c){return c.grade[key].toDouble();},[key](auto&c,double v){if(v==0)c.grade.remove(key);else c.grade[key]=v;},true);if(g.pro)rule(row,{},true,cc);}
        {auto r=buttonRow(cc);button(r,EchoesText("✦ Авто"),[this]{mutate([](Clip&c){c.grade["contrast"]=10;c.grade["saturation"]=12;c.grade["vibrance"]=18;c.grade["sharpen"]=15;c.grade["shadows"]=8;},true);})->setToolTip(EchoesText("Быстрое улучшение: контраст, сочность и резкость"));
            button(r,EchoesText("Сбросить цвет"),[this]{mutate([](Clip&c){QString key=c.grade["key"].toString();double sim=c.grade["keySim"].toDouble(),blend=c.grade["keyBlend"].toDouble();c.grade={};if(!key.isEmpty()){c.grade["key"]=key;c.grade["keySim"]=sim;c.grade["keyBlend"]=blend;}c.look.clear();c.lookAmount=1;},true);});}
        auto cv=group(l,EchoesText("КРИВЫЕ"),kinds);
        {auto r=buttonRow(cv);auto grp=new QButtonGroup(this);for(auto ch:QList<QPair<QString,QString>>{{"curveM",EchoesText("Общая")},{"curveR","R"},{"curveG","G"},{"curveB","B"}}){auto b=new QPushButton(ch.second);b->setCheckable(true);b->setChecked(ch.first=="curveM");grp->addButton(b);r->addWidget(b);QString k=ch.first;
            connect(b,&QPushButton::clicked,this,[this,k]{curveChannel=k;curves->color=QColor(k=="curveR"?"#ff6b6b":k=="curveG"?"#5be37d":k=="curveB"?"#5b9dff":"#e8e8ea");if(auto c=timeline->current())curves->setText(c->grade.value(k).toString());});}
            button(r,EchoesText("Сброс"),[this]{QString k=curveChannel;mutate([k](Clip&c){c.grade.remove(k);});});}
        curves=new CurveEditor;curves->setToolTip(EchoesText("Тяните точки. Щелчок по линии — новая точка, правая кнопка — удалить точку."));cv->addRow(curves);
        curves->begin=[this]{if(editable())checkpoint();sliding=true;};curves->changed=[this]{QString k=curveChannel,v=curves->text();mutate([k,v](Clip&c){c.grade[k]=v;},false,false);};curves->end=[this]{sliding=false;inspect();};
        fillers<<[this](const Clip&c){if(!curves->dragging())curves->setText(c.grade[curveChannel].toString());};
        auto key=group(l,EchoesText("ХРОМАКЕЙ • УДАЛЕНИЕ ФОНА"),mediaKinds);
        {auto r=buttonRow(key);button(r,EchoesText("⌖ Пипетка"),[this]{if(!editable())return;frameView->picking=true;frameView->setCursor(Qt::CrossCursor);frameView->update();status->setText(EchoesText("Нажмите в окне просмотра на цвет фона, который нужно убрать."));});
            button(r,EchoesText("Зелёный"),[this]{mutate([](Clip&c){c.grade["key"]="#00ff00";if(!c.grade.contains("keySim"))c.grade["keySim"]=30;});});button(r,EchoesText("Синий"),[this]{mutate([](Clip&c){c.grade["key"]="#0047bb";if(!c.grade.contains("keySim"))c.grade["keySim"]=30;});});button(r,EchoesText("Убрать"),[this]{mutate([](Clip&c){c.grade.remove("key");});});}
        colorButton(key,EchoesText("Цвет фона"),[](auto&c){return c.grade["key"].toString();},[](auto&c,QString v){c.grade["key"]=v;});
        slider(key,EchoesText("Допуск"),1,100,1,0,[](auto&c){return c.grade["keySim"].toDouble(30);},[](auto&c,double v){c.grade["keySim"]=v;});slider(key,EchoesText("Мягкость края"),0,100,1,0,[](auto&c){return c.grade["keyBlend"].toDouble(10);},[](auto&c,double v){c.grade["keyBlend"]=v;});
        auto sc=group(l,EchoesText("СКОПЫ"),{},true);auto mode=new QComboBox;mode->addItems({EchoesText("Гистограмма RGB"),EchoesText("Осциллограф яркости"),EchoesText("Вектороскоп")});sc->addRow(mode);scopes=new Scopes;sc->addRow(scopes);connect(mode,&QComboBox::activated,this,[this](int i){scopes->mode=i;scopes->update();});
        l->addStretch();return page;
    }
    QWidget *pageAudio(){
        auto [page,l]=scrollPage();
        auto vol=group(l,EchoesText("ГРОМКОСТЬ"),timedKinds);rule(check(vol,EchoesText("Звук клипа включён"),[](auto&c){return c.audio;},[](auto&c,bool v){c.audio=v;}),{"video"},false,vol);
        slider(vol,EchoesText("Громкость"),0,400,1,0,[this](auto&c){return poseAt(c,localAt(c)).volume*100;},[this](auto&c,double v){setKeyed(c,"volume",v/100);},false," %");
        slider(vol,EchoesText("Плавное начало"),0,10,.1,1,[](auto&c){return c.fadeIn;},[](auto&c,double v){c.fadeIn=v;},true,EchoesText(" с"));slider(vol,EchoesText("Плавный конец"),0,10,.1,1,[](auto&c){return c.fadeOut;},[](auto&c,double v){c.fadeOut=v;},true,EchoesText(" с"));
        rule(slider(vol,EchoesText("Кроссфейд"),0,5,.1,1,[](auto&c){return c.trans=="crossfade"?c.transDur:0.;},[](auto&c,double v){if(v<=0)c.trans.clear();else{c.trans="crossfade";c.transDur=v;}},false,EchoesText(" с")),{"audio"},false,vol);
        auto vc=group(l,EchoesText("ГОЛОС И ТОН"),timedKinds);choice(vc,EchoesText("Эффект голоса"),voiceCatalog(),[](auto&c){return c.sound["voice"].toString();},[](auto&c,QString v){if(v.isEmpty())c.sound.remove("voice");else c.sound["voice"]=v;},true);
        slider(vc,EchoesText("Тон"),-12,12,1,0,[](auto&c){return c.sound["pitch"].toDouble();},[](auto&c,double v){if(v==0)c.sound.remove("pitch");else c.sound["pitch"]=v;},true,EchoesText(" пт"));
        auto en=group(l,EchoesText("УЛУЧШЕНИЕ"),timedKinds);
        for(auto e:QList<QPair<QString,QString>>{{"denoise",EchoesText("Шумоподавление")},{"normalize",EchoesText("Выровнять громкость")},{"clarity",EchoesText("Чёткость голоса")},{"bass",EchoesText("Усилить бас")}}){QString k=e.first;check(en,e.second,[k](auto&c){return c.sound[k].toBool();},[k](auto&c,bool v){if(v)c.sound[k]=true;else c.sound.remove(k);},true);}
        auto eq=group(l,EchoesText("ЭКВАЛАЙЗЕР"),timedKinds,true);
        for(auto e:QList<QPair<QString,QString>>{{"low",EchoesText("Низкие")},{"mid",EchoesText("Средние")},{"high",EchoesText("Высокие")}}){QString k=e.first;slider(eq,e.second,-12,12,1,0,[k](auto&c){return c.sound[k].toDouble();},[k](auto&c,double v){if(v==0)c.sound.remove(k);else c.sound[k]=v;},true,EchoesText(" дБ"));}
        auto bt=group(l,EchoesText("РИТМ"),timedKinds);auto bh=new QLabel(EchoesText("Найдите биты в музыке — на таймлайне появятся голубые метки. Затем разрежьте видео по ним одним нажатием."));bh->setObjectName("hint");bh->setWordWrap(true);bt->addRow(bh);
        {auto r=buttonRow(bt);button(r,EchoesText("♫ Найти биты"),[this]{detectBeats();},true);button(r,EchoesText("✂ По меткам"),[this]{cutAtMarkers();});}
        {auto r=buttonRow(bt);button(r,EchoesText("Убрать метки битов"),[this]{checkpoint();project.markers.erase(std::remove_if(project.markers.begin(),project.markers.end(),[](auto &m){return m.name==EchoesText("бит");}),project.markers.end());edited();});}
        l->addStretch();return page;
    }
    QWidget *pageExport(){
        auto [page,l]=scrollPage();
        auto pf=group(l,EchoesText("ПРЕСЕТ"),{});exportPreset=new QComboBox;for(auto &e:exportCatalog())exportPreset->addItem(e.name,e.id);pf->addRow(exportPreset);
        auto sf=group(l,EchoesText("НАСТРОЙКИ"),{});
        exportQuality=new QSlider(Qt::Horizontal);exportQuality->setRange(0,100);sf->addRow(EchoesText("Качество"),exportQuality);auto ql=new QLabel(EchoesText("← меньше файл        лучше качество →"));ql->setObjectName("hint");sf->addRow("",ql);
        exportRes=new QComboBox;exportRes->addItem(EchoesText("Как в проекте"),0);for(int h:{2160,1440,1080,720,480})exportRes->addItem(QString("%1p").arg(h),h);sf->addRow(EchoesText("Разрешение"),exportRes);
        exportFps=new QComboBox;exportFps->addItem(EchoesText("Как в проекте"),0);for(int f:{15,24,25,30,50,60})exportFps->addItem(QString(EchoesText("%1 к/с")).arg(f),f);sf->addRow(EchoesText("Частота кадров"),exportFps);
        exportRange=new QComboBox;exportRange->addItem(EchoesText("Весь проект"),0);exportRange->addItem(EchoesText("От точки входа до выхода (I / O)"),1);sf->addRow(EchoesText("Диапазон"),exportRange);
        exportSpeed=new QComboBox;exportSpeed->addItem(EchoesText("Быстрее"),"veryfast");exportSpeed->addItem(EchoesText("Сбалансированно"),"medium");exportSpeed->addItem(EchoesText("Медленнее, файл меньше"),"slow");exportSpeed->setCurrentIndex(1);sf->addRow(EchoesText("Кодирование"),exportSpeed);rule(exportSpeed,{},true,sf);
        exportLoud=new QCheckBox(EchoesText("Выровнять громкость под соцсети (−14 LUFS)"));sf->addRow(exportLoud);
        exportSummary=new QLabel;exportSummary->setWordWrap(true);exportSummary->setStyleSheet("color:#e6e6e8;background:#1a1a1d;border-radius:4px;padding:10px;");l->addWidget(exportSummary);
        exportButton=new QPushButton(EchoesText("⇪   Экспортировать…"));exportButton->setObjectName("primary");exportButton->setMinimumHeight(40);l->addWidget(exportButton);connect(exportButton,&QPushButton::clicked,this,[this]{exportProject();});
        auto more=group(l,EchoesText("ЕЩЁ"),{});{auto r=buttonRow(more);button(r,EchoesText("Кадр в PNG"),[this]{saveFrame();});button(r,EchoesText("Субтитры .srt"),[this]{exportSrt();});}
        auto presetChanged=[this]{auto e=exportCatalog().value(exportPreset->currentIndex());QSignalBlocker b1(exportQuality),b2(exportRes),b3(exportFps),b4(exportLoud);
            int lo=e.codec=="vp9"?15:14,hi=e.codec=="vp9"?45:32;exportQuality->setValue(e.crf>0?qRound(double(hi-e.crf)/(hi-lo)*100):80);exportRes->setCurrentIndex(qMax(0,exportRes->findData(e.height)));exportFps->setCurrentIndex(qMax(0,exportFps->findData(e.fps)));exportLoud->setChecked(e.loud);
            bool audioOnly=e.container=="mp3"||e.container=="wav";exportQuality->setEnabled(e.crf>0);exportRes->setEnabled(!audioOnly);exportFps->setEnabled(!audioOnly);exportLoud->setEnabled(e.container!="gif");updateExportSummary();};
        connect(exportPreset,&QComboBox::activated,this,presetChanged);for(auto box:{exportRes,exportFps,exportRange,exportSpeed})connect(box,&QComboBox::activated,this,[this]{updateExportSummary();});connect(exportQuality,&QSlider::valueChanged,this,[this]{updateExportSummary();});
        presetChanged();l->addStretch();return page;
    }
    QWidget *buildTimeline(){
        auto card=panel();auto tl=new QVBoxLayout(card);tl->setContentsMargins(0,0,0,0);tl->setSpacing(0);QHBoxLayout *t;auto tools=strip(t);tools->setFixedHeight(40);
        tool(t,glyphIcon("↶"),"",EchoesText("Отменить • Ctrl+Z"),[this]{undo();});tool(t,glyphIcon("↷"),"",EchoesText("Повторить • Ctrl+Y"),[this]{redo();});t->addSpacing(6);
        auto pointer=tool(t,glyphIcon("➤"),"",EchoesText("Выбор • A"),[this]{setBlade(false);},true);bladeButton=tool(t,glyphIcon("✂"),"",EchoesText("Лезвие: щёлкните по клипу, чтобы разрезать • B"),[this]{setBlade(bladeButton->isChecked());},true);pointer->setChecked(true);
        auto toolGroup=new QButtonGroup(this);toolGroup->addButton(pointer);toolGroup->addButton(bladeButton);rule(pointer,{},true);rule(bladeButton,{},true);t->addSpacing(6);
        tool(t,glyphIcon("⫽"),EchoesText("Разрезать"),EchoesText("Разрезать под курсором • S"),[this]{split();});tool(t,glyphIcon("✕"),EchoesText("Удалить"),EchoesText("Удалить • Delete (Shift+Delete — со сдвигом)"),[this]{remove();});
        rule(tool(t,glyphIcon("⇤"),"",EchoesText("Обрезать начало до курсора • Q"),[this]{trimToPlayhead(true);}),{},true);rule(tool(t,glyphIcon("⇥"),"",EchoesText("Обрезать конец до курсора • W"),[this]{trimToPlayhead(false);}),{},true);
        tool(t,glyphIcon("❄"),EchoesText("Стоп-кадр"),EchoesText("Заморозить кадр под курсором на 2 секунды • F"),[this]{freezeFrame();});tool(t,glyphIcon("⟲"),EchoesText("Реверс"),EchoesText("Воспроизвести клип задом наперёд • R"),[this]{toggleReverse();});
        auto speedButton=tool(t,glyphIcon("⏱"),EchoesText("Скорость"),EchoesText("Скорость клипа"),[]{});speedButton->setPopupMode(QToolButton::InstantPopup);auto sm=new QMenu(speedButton);for(double v:{.25,.5,.75,1.,1.5,2.,3.,4.})sm->addAction(QString("%1×").arg(v),this,[this,v]{setSpeed(v);});speedButton->setMenu(sm);
        tool(t,glyphIcon("T"),EchoesText("Текст"),EchoesText("Добавить текст • T"),[this]{addText("plain");});tool(t,glyphIcon("◆"),EchoesText("Ключ"),EchoesText("Ключевой кадр под курсором • K"),[this]{toggleKey();});
        tool(t,glyphIcon("⚑"),EchoesText("Метка"),EchoesText("Поставить / убрать метку • M"),[this]{toggleMarker(timeline->playhead);});tool(t,glyphIcon("♫"),EchoesText("Биты"),EchoesText("Найти биты в выбранной музыке"),[this]{detectBeats();});
        t->addStretch();
        snapButton=tool(t,glyphIcon("⊓"),EchoesText("Привязка"),EchoesText("Прилипание к краям, курсору и меткам • N"),[this]{timeline->snap=snapButton->isChecked();},true);snapButton->setChecked(true);
        linkButton=tool(t,glyphIcon("⛓"),EchoesText("Связь"),EchoesText("Видео и его звук выбираются вместе • Ctrl+Shift+L"),[this]{timeline->linkedSelection=linkButton->isChecked();timeline->update();},true);linkButton->setChecked(true);rule(linkButton,{},true);
        t->addSpacing(8);tool(t,glyphIcon("−"),"",EchoesText("Отдалить • Ctrl+-"),[this]{zoomBy(.8,-1);});auto zoom=new QSlider(Qt::Horizontal);zoom->setObjectName("zoom");zoom->setRange(2,400);zoom->setValue(80);zoom->setFixedWidth(130);zoom->setFocusPolicy(Qt::NoFocus);t->addWidget(zoom);tool(t,glyphIcon("+"),"",EchoesText("Приблизить • Ctrl+="),[this]{zoomBy(1.25,-1);});
        tool(t,glyphIcon("⤢"),"",EchoesText("Уместить весь проект • Shift+Z"),[this]{fitTimeline();});
        connect(zoom,&QSlider::valueChanged,this,[this](int z){if(filling)return;timeline->zoom=z;timeline->resizeCanvas();});
        tl->addWidget(tools);auto scroll=new TimelineScroll(timeline);scroll->setMinimumHeight(180);tl->addWidget(scroll,1);return card;
    }
    QWidget *buildBottom(){
        auto bar=new QFrame;bar->setObjectName("pagebar");bar->setFixedHeight(46);auto row=new QHBoxLayout(bar);row->setContentsMargins(12,2,12,2);
        status=new QLabel(EchoesText("Готово. Добавьте медиа — кадры и звук появятся автоматически."));status->setObjectName("muted");status->setMinimumWidth(200);row->addWidget(status,1);
        pages=new QButtonGroup(this);QList<QPair<QString,QString>> items{{"✂",EchoesText("Монтаж")},{"◆",EchoesText("Анимация")},{"◐",EchoesText("Цвет")},{"♪",EchoesText("Звук")},{"⇪",EchoesText("Экспорт")}};
        for(int i=0;i<items.size();i++){auto b=new QToolButton;b->setObjectName("page");b->setText(items[i].second);b->setIcon(glyphIcon(items[i].first,QColor("#c8c8cc"),18));b->setIconSize(QSize(18,18));b->setToolButtonStyle(Qt::ToolButtonTextUnderIcon);b->setCheckable(true);b->setFocusPolicy(Qt::NoFocus);b->setToolTip(QString("Shift+%1").arg(i+1));row->addWidget(b);pages->addButton(b,i);}
        connect(pages,&QButtonGroup::idClicked,this,[this](int i){showPage(i);});
        auto right=new QWidget;auto rr=new QHBoxLayout(right);rr->setContentsMargins(0,0,0,0);progress=new QProgressBar;progress->setRange(0,100);progress->setFixedWidth(160);progress->hide();rr->addStretch();rr->addWidget(progress);
        cancelButton=button(rr,EchoesText("Отменить экспорт"),[this]{if(render&&!renderProxy)render->kill();});cancelButton->hide();row->addWidget(right,1);
        return bar;
    }

    // ---------- Mode, pages, inspector ----------
    void applyMode(){
        if(persistMode)QSettings().setValue("pro",pro);if(modePro){modePro->setChecked(pro);modeSimple->setChecked(!pro);}if(proAction){QSignalBlocker b(proAction);proAction->setChecked(pro);}
        if(!pro&&bladeButton&&bladeButton->isChecked())setBlade(false);applyRules(timeline->current());
    }
    void applyRules(const Clip *c){for(auto &r:rules){bool vis=(r.kinds.isEmpty()||(c&&r.kinds.contains(c->kind)))&&(pro||!r.pro);if(r.form)r.form->setRowVisible(r.w,vis);else r.w->setVisible(vis);}}
    void showPage(int i){inspectorStack->setCurrentIndex(i);if(auto b=pages->button(i))b->setChecked(true);if(i==2)scopes->setImage(frameView->image);if(i==4)updateExportSummary();}
    double localAt(const Clip &c){return qBound(0.,timeline->playhead-c.start,c.duration);}
    void setKeyed(Clip &c,QString prop,double v){
        if(c.keys.isEmpty()){if(prop=="x")c.x=qRound(v);else if(prop=="y")c.y=qRound(v);else if(prop=="scale")c.scale=v;else if(prop=="rotation")c.rotation=v;else if(prop=="opacity")c.opacity=v;else if(prop=="volume")c.gain=v;return;}
        upsertKey(c,prop,v);
    }
    void upsertKey(Clip &c,QString prop,double v){
        double local=localAt(c);Pose ps=poseAt(c,local);QJsonObject k{{"time",local+c.animationOffset},{"x",ps.x},{"y",ps.y},{"scale",ps.scale},{"rotation",ps.rotation},{"opacity",ps.opacity},{"volume",ps.volume}};if(!prop.isEmpty())k[prop]=v;
        QJsonArray keys;double half=.5/project.fps;for(auto e:c.keys)if(std::abs(e.toObject()["time"].toDouble()-k["time"].toDouble())>half)keys.append(e);keys.append(k);c.keys=keys;c.motion=false;
    }
    int keyAtPlayhead(const Clip &c){double kt=localAt(c)+c.animationOffset,half=.5/project.fps;for(int i=0;i<c.keys.size();i++)if(std::abs(c.keys[i].toObject()["time"].toDouble()-kt)<=half)return i;return -1;}
    void toggleKey(){auto c=editable();if(!c)return;if(timeline->playhead<c->start||timeline->playhead>c->start+c->duration){status->setText(EchoesText("Поставьте курсор над клипом, чтобы добавить ключевой кадр."));return;}int at=keyAtPlayhead(*c);
        mutate([this,at](Clip&x){if(at>=0)x.keys.removeAt(at);else upsertKey(x,{},0);});status->setText(at>=0?EchoesText("Ключевой кадр удалён."):EchoesText("Ключевой кадр добавлен. Измените масштаб, позицию или прозрачность — появится анимация."));}
    void jumpKey(int dir){auto c=timeline->current();if(!c)return;double now=localAt(*c),best=dir>0?1e9:-1e9;bool found=false;for(auto v:c->keys){double t=v.toObject()["time"].toDouble()-c->animationOffset;if(t<-.001||t>c->duration+.001)continue;if(dir>0&&t>now+.001&&t<best){best=t;found=true;}if(dir<0&&t<now-.001&&t>best){best=t;found=true;}}if(found)seekTo(c->start+best);}
    void editKeys(){if(filling)return;auto c=editable();if(!c)return;QJsonArray keys=c->keys;
        for(int i=0;i<keyTable->rowCount();i++){QList<double> v;for(int col=0;col<6;col++){bool ok=false;double x=keyTable->item(i,col)?keyTable->item(i,col)->text().toDouble(&ok):0;if(!ok){inspect();return;}v<<x;}if(v[0]<0||v[0]>c->duration){inspect();return;}
            int index=keyTable->item(i,0)->data(Qt::UserRole).toInt();if(index>=keys.size())continue;auto k=keys[index].toObject();k["time"]=v[0]+c->animationOffset;k["x"]=v[1];k["y"]=v[2];k["scale"]=v[3]/100;k["rotation"]=v[4];k["opacity"]=v[5]/100;keys[index]=k;}
        checkpoint();c->keys=keys;*c=Clip::read(c->json());edited();inspect();}
    Clip *editable(){auto c=timeline->current();return c&&!project.locked(c->track)?c:nullptr;}
    QList<Clip*> chosenClips(){QList<Clip*> out;auto ids=timeline->chosen();for(auto &c:project.clips)if(ids.contains(c.id)&&!project.locked(c.track))out<<&c;return out;}
    // Applies a change to the selected clip (or all selected), keeping linked video/audio in sync.
    void mutate(std::function<void(Clip&)> f,bool all=false,bool record=true){
        auto c=editable();if(!c)return;QList<Clip*> targets{c};if(all)for(auto v:chosenClips())if(v!=c&&visual(*v)==visual(*c))targets<<v;
        if(record)checkpoint();
        for(auto t:targets){Clip old=*t;f(*t);*t=Clip::read(t->json());
            bool timing=t->start!=old.start||t->in!=old.in||t->duration!=old.duration||t->speed!=old.speed||t->reverse!=old.reverse;
            if(timing&&!old.group.isEmpty())for(auto &v:project.clips)if(v.id!=t->id&&v.group==old.group&&!project.locked(v.track)){v.start=t->start;v.in=t->in;v.duration=t->duration;v.speed=t->speed;v.reverse=t->reverse;}
            bool fades=t->fadeIn!=old.fadeIn||t->fadeOut!=old.fadeOut;
            if(fades&&!old.group.isEmpty())for(auto &v:project.clips)if(v.id!=t->id&&v.group==old.group&&!project.locked(v.track)){v.fadeIn=t->fadeIn;v.fadeOut=t->fadeOut;}}
        edited();if(record&&!dragId.size())inspect();else{timeline->update();updateBox();}
    }
    void inspect(){
        filling=true;auto c=timeline->current();bool allowed=c&&!project.locked(c->track);int count=timeline->chosen().size();
        QString kind=!c?"":c->kind=="video"?EchoesText("Видео"):c->kind=="audio"?EchoesText("Звук"):c->kind=="image"?EchoesText("Изображение"):c->kind=="text"?EchoesText("Текст"):c->kind=="color"?EchoesText("Цветной слой"):EchoesText("Корректирующий слой");
        selectionLabel->setText(c?(kind+" • "+c->name+(count>1?QString(EchoesText(" • выбрано: %1")).arg(count):"")+(project.locked(c->track)?EchoesText(" • дорожка заблокирована"):"")):EchoesText("Ничего не выбрано"));
        for(auto s:clipPages){s->setCurrentIndex(c?1:0);s->widget(1)->setEnabled(allowed);}
        if(c){
            track->clear();for(int i=0;i<project.tracks;i++)if(project.lanes[i].kind==Project::type(*c))track->addItem(project.trackName(i),i);track->addItem(EchoesText("+ Новая дорожка"),-1);track->setCurrentIndex(track->findData(c->track));
            for(auto &f:fillers)f(*c);
            for(int i=0;i<effectList->count();i++){auto it=effectList->item(i);it->setCheckState(c->effect.split(',').contains(it->data(Qt::UserRole).toString())?Qt::Checked:Qt::Unchecked);}
            for(int i=0;i<lookList->count();i++)if(lookList->item(i)->data(Qt::UserRole).toString()==c->look)lookList->setCurrentRow(i);lookAmountSlider->setValue(qRound(c->lookAmount*100));
            for(int i=0;i<transList->count();i++)if(transList->item(i)->data(Qt::UserRole).toString()==c->trans)transList->setCurrentRow(i);
            keyTable->setRowCount(0);for(int index=0;index<c->keys.size();index++){auto k=c->keys[index].toObject();double at=k["time"].toDouble()-c->animationOffset;if(at<-.001||at>c->duration+.001)continue;int row=keyTable->rowCount();keyTable->insertRow(row);
                QList<double> vals{qMax(0.,at),k["x"].toDouble(),k["y"].toDouble(),k.contains("scale")?k["scale"].toDouble()*100:c->scale*100,k.contains("rotation")?k["rotation"].toDouble():c->rotation,k.contains("opacity")?k["opacity"].toDouble()*100:c->opacity*100};
                for(int col=0;col<6;col++){auto item=new QTableWidgetItem(QString::number(vals[col],'f',col==0?3:1));item->setData(Qt::UserRole,index);keyTable->setItem(row,col,item);}}
            keyButton->setText(keyAtPlayhead(*c)>=0?EchoesText("◆ Убрать ключ"):EchoesText("◆ Ключ"));
        }else{keyTable->setRowCount(0);for(int i=0;i<effectList->count();i++)effectList->item(i)->setCheckState(Qt::Unchecked);}
        effectList->setEnabled(allowed&&visual(*c));lookList->setEnabled(allowed&&visual(*c));transList->setEnabled(allowed&&visual(*c));
        applyRules(c);updateBox();filling=false;
    }
    struct Box { QPointF center;QSizeF size;double rotation; };
    Box layerBox(const Clip &c,double local){
        Pose ps=poseAt(c,local);double W=project.width,H=project.height;QPointF C(W/2,H/2),offset;QSizeF content(W,H);
        if(c.kind=="text"){QRectF r=textBounds(c,QSizeF(W,H));content=r.size();offset=r.center()-C;}
        else if((c.kind=="video"||c.kind=="image")&&c.srcW>0&&c.srcH>0&&c.fit!="fill"&&c.fit!="stretch"){QSizeF src(c.srcW*(1-c.cropL-c.cropR),c.srcH*(1-c.cropT-c.cropB));src.scale(W,H,Qt::KeepAspectRatio);content=src;}
        return {C+offset*ps.scale+QPointF(ps.x,ps.y),content*ps.scale,ps.rotation};
    }
    void updateBox(){
        frameView->canvas=QSize(project.width,project.height);auto c=timeline->current();double t=timeline->playhead;
        if(!c||!visual(*c)||c->kind=="adjust"||t<c->start||t>=c->start+c->duration||project.locked(c->track)){frameView->hasBox=false;frameView->maskPath=QPainterPath();frameView->update();return;}
        auto b=layerBox(*c,t-c->start);frameView->hasBox=true;frameView->boxCenter=b.center;frameView->boxSize=b.size;frameView->boxRotation=b.rotation;
        frameView->maskPath=QPainterPath();if(!c->mask.value("shape").toString().isEmpty()){Pose ps=poseAt(*c,t-c->start);double W=project.width,H=project.height,k=ps.scale;auto m=c->mask;QTransform x;x.translate(W/2+ps.x,H/2+ps.y);x.rotate(ps.rotation);x.translate(m["x"].toDouble()/100*W*k,m["y"].toDouble()/100*H*k);x.rotate(m["angle"].toDouble());
            double mw=m["w"].toDouble(60)/100*W*k,mh=m["h"].toDouble(60)/100*H*k;QRectF r(-mw/2,-mh/2,mw,mh);QPainterPath path;if(m["shape"].toString()=="ellipse")path.addEllipse(r);else{double rad=qMin(mw,mh)/2*m["round"].toDouble()/100;path.addRoundedRect(r,rad,rad);}frameView->maskPath=x.map(path);}
        frameView->update();
    }
    void pickAt(QPointF at){
        QList<Clip*> hits;double t=timeline->playhead;for(auto &c:project.clips)if(visual(c)&&c.kind!="adjust"&&!project.muted(c.track)&&t>=c.start&&t<c.start+c.duration)hits<<&c;
        std::stable_sort(hits.begin(),hits.end(),[](Clip*a,Clip*b){return a->track!=b->track?a->track<b->track:a->start>b->start;});
        for(auto c:hits){auto b=layerBox(*c,t-c->start);QTransform m;m.translate(b.center.x(),b.center.y());m.rotate(b.rotation);if(m.map(QPolygonF(QRectF(-b.size.width()/2,-b.size.height()/2,b.size.width(),b.size.height()))).containsPoint(at,Qt::OddEvenFill)){timeline->selectOnly(c->id);inspect();timeline->update();return;}}
    }

    // ---------- Project state ----------
    bool write(QString path,QJsonObject o){QSaveFile f(path);if(!f.open(QIODevice::WriteOnly))return false;f.write(QJsonDocument(o).toJson());return f.commit();}
    bool groupLocked(const Clip &c){for(auto &v:project.clips)if((v.id==c.id||(!c.group.isEmpty()&&v.group==c.group))&&project.locked(v.track))return true;return false;}
    QList<QString> groupIds(const Clip &c){QList<QString> ids;for(auto &v:project.clips)if(v.id==c.id||(!c.group.isEmpty()&&c.group==v.group))ids<<v.id;return ids;}
    void checkpoint(){undoStack<<project.json();if(undoStack.size()>100)undoStack.removeFirst();redoStack.clear();}
    void updateTime(){timeLabel->setText(timecode(timeline->playhead,project.fps)+"  /  "+timecode(project.end(),project.fps));viewerTime->setText(timecode(timeline->playhead,project.fps));}
    void updatePlayButton(){playButton->setText(wantPlaying?"❚❚":"▶");}
    void edited(){
        if(chunkMode||quick||(wantPlaying&&!proxyValid)){wantPlaying=false;vlc.stop();stopChunks();}
        dirty=true;proxyValid=false;paused=true;startingVlc=false;++playGeneration;++frameGeneration;vlc.pause(true);previewStack->setCurrentWidget(frameView);frameView->hint=project.clips.isEmpty()?EchoesText("Перетащите видео сюда или нажмите «Импорт медиа»"):EchoesText("Обновляем кадр…");
        timeline->playhead=qBound(0.,timeline->playhead,qMax(0.,project.end()-.001));timeline->resizeCanvas();updateTime();updatePlayButton();setWindowTitle("ECHOES Montage • *");projectLabel->setText((projectPath.isEmpty()?EchoesText("Без названия"):QFileInfo(projectPath).completeBaseName())+" •");
        frameTimer.start(60);previewTimer.start(420);previewHint->setText(EchoesText("● применяем изменения…"));ensureStabilization();
    }
    void seekTo(double t){timeline->playhead=qBound(0.,t,qMax(0.,project.end()-.001));timeline->update();updateTime();if(proxyValid&&wantPlaying&&!startingVlc)vlc.seek(timeline->playhead);
        else if(wantPlaying&&chunkMode){double local=timeline->playhead-chunkFrom;if(local>=0&&local<chunkLen-.1)vlc.seek(local);else{stopChunks();startChunk(timeline->playhead,true);}}else{previewStack->setCurrentWidget(frameView);if(!frameTimer.isActive())frameTimer.start(45);}updateBox();if(!sliding&&dragId.isEmpty())inspectTimer.start(70);}
    void step(int frames){if(wantPlaying)play();seekTo(timeline->playhead+double(frames)/project.fps);}
    void autoPreview(){
        if(closing||closePending||project.clips.isEmpty()||(wantPlaying&&!proxyValid))return;if(probing){previewTimer.start(150);return;}
        if(render){if(renderProxy&&renderSnapshot!=project.json()){cancelledPreview=true;render->kill();}return;}
        if(!proxyValid&&vlc.ready())startRender(true,temp.path()+"/preview_"+QUuid::createUuid().toString(QUuid::Id128)+".mp4");
    }
    void renderFrame(){
        if(frameProc){frameAgain=true;return;}frameAgain=false;quint64 generation=frameGeneration;double at=timeline->playhead;
        if(wantPlaying)return;Project frame=frameProject(project,timeline->playhead);
        if(frame.clips.isEmpty()){frameView->image=QImage();frameView->hint=project.clips.isEmpty()?EchoesText("Перетащите видео сюда или нажмите «Импорт медиа»"):EchoesText("В этой позиции нет изображения");frameView->update();previewStack->setCurrentWidget(frameView);return;}
        auto dir=QSharedPointer<QTemporaryDir>::create();auto r=plan(frame,dir->path(),{},true,true);if(!r.error.isEmpty()){status->setText(r.error);return;}
        auto proc=new QProcess(this);frameProc=proc;frameJobs.insert(proc);
        auto done=[this,proc,dir,generation,at](bool ok){if(!frameJobs.remove(proc))return;QByteArray data=proc->readAllStandardOutput();if(generation==frameGeneration&&ok){QImage image=QImage::fromData(data);if(!image.isNull()){frameView->image=image;frameView->update();completedFrames++;lastFrameTime=at;if(scopes->isVisible())scopes->setImage(image);if(!wantPlaying)previewStack->setCurrentWidget(frameView);}}if(frameProc==proc)frameProc=nullptr;proc->deleteLater();if(frameAgain&&!closing)frameTimer.start(0);};
        connect(proc,qOverload<int,QProcess::ExitStatus>(&QProcess::finished),this,[done](int code,QProcess::ExitStatus s){done(code==0&&s==QProcess::NormalExit);});connect(proc,&QProcess::errorOccurred,this,[done](QProcess::ProcessError e){if(e==QProcess::FailedToStart)done(false);});proc->start(ffmpeg,r.args);
    }
    void launchPlayback(){
        if(!proxyValid||!wantPlaying)return;double target=timeline->playhead;quint64 generation=++playGeneration;startingVlc=true;paused=false;vlc.play(proxyPath);previewStack->setCurrentWidget(screen);
        auto wait=new QTimer(this);auto attempts=new int(0);connect(wait,&QTimer::timeout,this,[this,wait,attempts,generation,target]{
            if(generation!=playGeneration||++*attempts>100){wait->stop();wait->deleteLater();delete attempts;return;}
            if(vlc.state()==3){if(testing)vlc.mute(true);vlc.seek(target);startingVlc=false;wait->stop();wait->deleteLater();delete attempts;}
        });wait->start(20);updatePlayButton();
    }
    void play(){if(project.clips.isEmpty()){status->setText(EchoesText("Сначала добавьте видео или музыку."));return;}wantPlaying=!wantPlaying;
        if(!wantPlaying){stopPlayback();return;}
        if(proxyValid){if(vlc.state()==4&&!chunkMode){paused=false;if(std::abs(vlc.time()-timeline->playhead)>.08)vlc.seek(timeline->playhead);vlc.pause(false);previewStack->setCurrentWidget(screen);}else launchPlayback();}
        else{
            // No full preview yet: play short chunks rendered from the cursor instead of waiting.
            if(render&&renderProxy){cancelledPreview=true;render->kill();}stopChunks();startChunk(timeline->playhead,true);status->setText(EchoesText("Готовим воспроизведение…"));previewHint->setText(EchoesText("● готовим воспроизведение…"));}
        updatePlayButton();
    }
    void undo(){if(undoStack.isEmpty())return;redoStack<<project.json();project.load(undoStack.takeLast());edited();inspect();syncFormat();rebuildBin();}
    void redo(){if(redoStack.isEmpty())return;undoStack<<project.json();project.load(redoStack.takeLast());edited();inspect();syncFormat();rebuildBin();}
    void syncFormat(){filling=true;QSize s(project.width,project.height);int i=format->findData(s);if(i<0){format->addItem(QString(EchoesText("%1×%2 • свой")).arg(s.width()).arg(s.height()),s);i=format->count()-1;}format->setCurrentIndex(i);fpsBox->setCurrentIndex(qMax(0,fpsBox->findData(project.fps)));filling=false;updateExportSummary();}

    // ---------- Editing commands ----------
    QString splitAt(QStringList ids,double t){
        QHash<QString,QString> groups;QString rightSelection;QList<Clip> added;
        for(auto &v:project.clips)if(ids.contains(v.id)){double at=t-v.start;if(at<.04||at>v.duration-.04)continue;Clip original=v;Clip right=sliced(original,at,original.duration-at);right.id=QUuid::createUuid().toString();right.fadeIn=0;v=sliced(original,0,at);v.fadeOut=0;
            if(!original.group.isEmpty()){if(!groups.contains(original.group))groups[original.group]=QUuid::createUuid().toString();right.group=groups[original.group];}
            if(original.id==timeline->selected)rightSelection=right.id;added<<right;}
        project.clips.append(added);return rightSelection;
    }
    void split(){auto c=editable();if(!c||groupLocked(*c))return;double t=timeline->playhead;QStringList ids;for(auto v:chosenClips())if(t>v->start+.04&&t<v->start+v->duration-.04)ids<<v->id;
        if(ids.isEmpty()){status->setText(EchoesText("Поставьте курсор внутри фрагмента и нажмите «Разрезать»."));return;}checkpoint();QString sel=splitAt(ids,t);if(!sel.isEmpty())timeline->selectOnly(sel);edited();inspect();}
    void bladeAt(QString id,double t){Clip *c=nullptr;for(auto &v:project.clips)if(v.id==id)c=&v;if(!c||groupLocked(*c))return;QStringList ids=timeline->linkedSelection?groupIds(*c):QStringList{id};
        if(t<c->start+.04||t>c->start+c->duration-.04)return;double fr=1./project.fps;t=std::round(t/fr)*fr;checkpoint();timeline->selectOnly(id);splitAt(ids,t);edited();inspect();}
    void duplicate(){auto c=editable();if(!c||groupLocked(*c))return;auto ids=timeline->chosen();double offset=c->duration;QHash<QString,QString> groups;QString selection;checkpoint();QList<Clip> added;
        for(auto v:project.clips)if(ids.contains(v.id)&&!project.locked(v.track)){QString old=v.id;v.id=QUuid::createUuid().toString();v.start+=offset;if(!v.group.isEmpty()){if(!groups.contains(v.group))groups[v.group]=QUuid::createUuid().toString();v.group=groups[v.group];}if(old==timeline->selected)selection=v.id;added<<v;}
        project.clips.append(added);timeline->selectOnly(selection);edited();inspect();}
    void remove(){auto c=editable();if(!c||groupLocked(*c))return;QStringList ids;for(auto v:chosenClips())ids<<v->id;if(ids.isEmpty())return;checkpoint();project.clips.removeIf([&](const Clip&v){return ids.contains(v.id);});timeline->selectOnly({});project.compact();edited();inspect();}
    void rippleDelete(){auto list=chosenClips();if(list.isEmpty())return;QList<Clip> gone;for(auto c:list)gone<<*c;checkpoint();QStringList ids;for(auto &g:gone)ids<<g.id;project.clips.removeIf([&](const Clip&v){return ids.contains(v.id);});
        std::sort(gone.begin(),gone.end(),[](auto &a,auto &b){return a.start>b.start;});for(auto &g:gone)for(auto &v:project.clips)if(v.track==g.track&&v.start>=g.start+g.duration-.001&&!project.locked(v.track))v.start=qMax(0.,v.start-g.duration);
        timeline->selectOnly({});project.compact();edited();inspect();status->setText(EchoesText("Удалено со сдвигом: пустота закрыта."));}
    void copy(){clipboard.clear();for(auto c:chosenClips())clipboard<<*c;if(!clipboard.isEmpty())status->setText(QString(EchoesText("Скопировано клипов: %1. Ctrl+V — вставить под курсор.")).arg(clipboard.size()));}
    void paste(double at){if(clipboard.isEmpty())return;double first=1e18;for(auto &c:clipboard)first=qMin(first,c.start);checkpoint();QHash<QString,QString> groups;QString selection;
        for(auto c:clipboard){c.id=QUuid::createUuid().toString();c.start=at+(c.start-first);if(!c.group.isEmpty()){if(!groups.contains(c.group))groups[c.group]=QUuid::createUuid().toString();c.group=groups[c.group];}
            if(c.track>=project.tracks||project.lanes[c.track].kind!=Project::type(c)||project.locked(c.track))c.track=project.ensureTrack(Project::type(c));project.clips<<c;if(selection.isEmpty())selection=c.id;}
        timeline->selectOnly(selection);edited();inspect();}
    void selectAll(){timeline->extra.clear();timeline->selected.clear();for(auto &c:project.clips){if(timeline->selected.isEmpty())timeline->selected=c.id;else timeline->extra.insert(c.id);}inspect();timeline->update();}
    void trimToPlayhead(bool head){auto list=chosenClips();double t=timeline->playhead;bool any=false;for(auto c:list)any|=t>c->start+.04&&t<c->start+c->duration-.04;if(!any){status->setText(EchoesText("Курсор должен стоять внутри выбранного клипа."));return;}checkpoint();
        for(auto c:list){double at=t-c->start;if(at<.04||at>c->duration-.04)continue;*c=head?sliced(*c,at,c->duration-at):sliced(*c,0,at);}edited();inspect();}
    void setSpeed(double v){auto c=editable();if(!c||(c->kind!="video"&&c->kind!="audio")){status->setText(EchoesText("Скорость меняется у видео и звука."));return;}mutate([v](Clip&x){double content=x.duration*x.speed;x.speed=v;x.duration=content/v;});status->setText(QString(EchoesText("Скорость: %1×")).arg(v));}
    void toggleReverse(){auto c=editable();if(!c||(c->kind!="video"&&c->kind!="audio")){status->setText(EchoesText("Реверс доступен для видео и звука."));return;}if(!c->reverse&&c->duration*c->speed>180){QMessageBox::information(this,EchoesText("Реверс"),EchoesText("Реверс работает для фрагментов до 3 минут исходника: обрежьте клип и попробуйте снова."));return;}
        bool on=!c->reverse;mutate([on](Clip&x){x.reverse=on;});status->setText(on?EchoesText("Клип воспроизводится задом наперёд."):EchoesText("Реверс выключен."));}
    void moveToTrack(int dest){auto c=editable();if(!c)return;if(dest>=0&&(project.locked(dest)||dest==c->track))return;checkpoint();if(dest<0){dest=project.insertTrack(c->track,Project::type(*c));c=timeline->current();}c->track=dest;project.compact();edited();inspect();}
    void unlink(){auto c=editable();if(!c||c->group.isEmpty()||groupLocked(*c))return;QString group=c->group;checkpoint();for(auto &v:project.clips)if(v.group==group)v.group.clear();edited();inspect();}
    void extractAudio(){auto c=editable();if(!c||c->kind!="video"||!c->audio)return;checkpoint();Clip audio=*c;c->audio=false;c->group=QUuid::createUuid().toString();audio.group=c->group;audio.id=QUuid::createUuid().toString();audio.kind="audio";audio.effect="none";audio.look.clear();audio.grade={};audio.name+=EchoesText(" • звук");audio.track=project.ensureTrack("audio");project.clips<<audio;edited();inspect();}
    void freezeFrame(){
        auto c=editable();if(!c||c->kind!="video"){status->setText(EchoesText("Выберите видеоклип и поставьте курсор на нужный кадр."));return;}double t=timeline->playhead,local=t-c->start;if(local<0||local>c->duration){status->setText(EchoesText("Курсор должен стоять над видеоклипом."));return;}
        double src=c->reverse?c->in+(c->duration-local)*c->speed:c->in+local*c->speed;QString dir=QStandardPaths::writableLocation(QStandardPaths::AppLocalDataLocation)+"/montage-frames";QDir().mkpath(dir);QString out=dir+"/"+QUuid::createUuid().toString(QUuid::Id128)+".png";
        auto proc=new QProcess(this);QString id=c->id;status->setText(EchoesText("Создаём стоп-кадр…"));
        connect(proc,qOverload<int,QProcess::ExitStatus>(&QProcess::finished),this,[this,proc,out,id,t](int code,QProcess::ExitStatus){proc->deleteLater();Clip *c=nullptr;for(auto &v:project.clips)if(v.id==id)c=&v;if(code!=0||!c||!QFileInfo::exists(out)){status->setText(EchoesText("Не удалось создать стоп-кадр."));return;}
            checkpoint();QStringList ids=groupIds(*c);QSet<int> tracks;for(auto &v:project.clips)if(ids.contains(v.id))tracks.insert(v.track);Clip still=*c;
            if(t>c->start+.04&&t<c->start+c->duration-.04)splitAt(ids,t);const double hold=2;for(auto &v:project.clips)if(tracks.contains(v.track)&&v.start>=t-.001)v.start+=hold;
            still.id=QUuid::createUuid().toString();still.kind="image";still.path=out;still.name=EchoesText("Стоп-кадр");still.start=t;still.duration=hold;still.in=0;still.source=hold;still.speed=1;still.reverse=false;still.audio=false;still.group.clear();still.keys={};still.trans.clear();still.inAnim.clear();still.outAnim.clear();still.fadeIn=still.fadeOut=0;
            project.clips<<still;timeline->selectOnly(still.id);addToBin(out);edited();inspect();status->setText(EchoesText("Стоп-кадр на 2 секунды добавлен."));});
        proc->start(ffmpeg,{"-v","error","-y","-ss",num(src),"-i",c->path,"-frames:v","1",out});
    }
    void transitionEverywhere(){QString id;for(int i=0;i<transList->count();i++)if(transList->item(i)->isSelected())id=transList->item(i)->data(Qt::UserRole).toString();if(id.isEmpty())id="dissolve";double d=transLength->value();checkpoint();int n=0;
        for(auto &b:project.clips){if(!visual(b)||b.kind=="adjust"||project.locked(b.track))continue;for(auto &a:project.clips)if(a.id!=b.id&&a.track==b.track&&visual(a)&&std::abs(a.start+a.duration-b.start)<.02){b.trans=id;b.transDur=d;n++;break;}}
        edited();inspect();status->setText(n?QString(EchoesText("Переход «%1» добавлен на стыков: %2")).arg(presetName(transitionCatalog(),id)).arg(n):EchoesText("Стыков не найдено: поставьте клипы вплотную друг к другу на одной дорожке."));}
    void closeGaps(int row){checkpoint();QList<Clip*> list;for(auto &c:project.clips)if(c.track==row)list<<&c;std::sort(list.begin(),list.end(),[](auto a,auto b){return a->start<b->start;});double cursor=0;
        for(auto c:list){if(c->start>cursor+.001){double shift=c->start-cursor;QString g=c->group;for(auto &v:project.clips)if(v.id==c->id||(!g.isEmpty()&&v.group==g))v.start-=shift;}cursor=qMax(cursor,c->start+c->duration);}edited();inspect();}
    void addText(QString style){checkpoint();Clip c;c.kind="text";c.text=EchoesText("Ваш текст");c.name=EchoesText("Ваш текст");c.start=timeline->playhead;c.duration=4;for(auto &t:textCatalog())if(t.id==style)applyTextStyle(c,t);c.track=project.ensureTrack("text");project.clips<<c;timeline->selectOnly(c.id);edited();inspect();showPage(0);textEdit->setFocus();textEdit->selectAll();}
    void addAdjustment(){checkpoint();Clip c;c.kind="adjust";c.name=EchoesText("Корректирующий слой");c.start=timeline->playhead;c.duration=qMax(3.,project.end()-c.start);c.audio=false;int at=0;while(at<project.tracks&&project.lanes[at].kind=="text")at++;c.track=project.insertTrack(at,"video");project.clips<<c;timeline->selectOnly(c.id);edited();inspect();showPage(2);status->setText(EchoesText("Корректирующий слой: цвет и эффекты применятся ко всем слоям под ним."));}
    void addColorLayer(QString a,QString b){checkpoint();Clip c;c.kind="color";c.name=b.isEmpty()?EchoesText("Цветной фон"):EchoesText("Градиент");c.style["color"]=a;if(!b.isEmpty())c.style["color2"]=b;c.start=timeline->playhead;c.duration=5;c.audio=false;int at=0;for(int i=0;i<project.tracks;i++)if(project.lanes[i].kind!="audio")at=i+1;c.track=project.insertTrack(at,"video");project.clips<<c;timeline->selectOnly(c.id);edited();inspect();}
    void toggleMarker(double t){checkpoint();double fr=1./project.fps;for(int i=0;i<project.markers.size();i++)if(std::abs(project.markers[i].time-t)<fr){project.markers.removeAt(i);edited();return;}project.markers<<Marker{t,"",pal::accent};std::sort(project.markers.begin(),project.markers.end(),[](auto &a,auto &b){return a.time<b.time;});edited();}
    void jumpMarker(int dir){double now=timeline->playhead;if(dir>0){for(auto &m:project.markers)if(m.time>now+.001){seekTo(m.time);return;}}else for(int i=project.markers.size()-1;i>=0;i--)if(project.markers[i].time<now-.001){seekTo(project.markers[i].time);return;}}
    void jumpEdit(int dir){QList<double> points{0,project.end()};for(auto &c:project.clips)points<<c.start<<c.start+c.duration;std::sort(points.begin(),points.end());double now=timeline->playhead;if(dir>0){for(double p:points)if(p>now+.001){seekTo(p);return;}}else for(int i=points.size()-1;i>=0;i--)if(points[i]<now-.001){seekTo(points[i]);return;}}
    void setRange(bool in){checkpoint();if(in)project.inPoint=timeline->playhead;else project.outPoint=timeline->playhead;if(project.inPoint>=0&&project.outPoint>0&&project.outPoint<=project.inPoint)std::swap(project.inPoint,project.outPoint);edited();updateExportSummary();if(project.inPoint>=0&&project.outPoint>0)exportRange->setCurrentIndex(1);}
    void zoomBy(double k,double x){auto scroll=qobject_cast<QScrollArea*>(timeline->parentWidget()->parentWidget());double vx=x<0?(scroll?scroll->viewport()->width()/2.:0):x-(scroll?scroll->horizontalScrollBar()->value():0);double at=(vx+(scroll?scroll->horizontalScrollBar()->value():0)-Timeline::Header)/timeline->zoom;
        timeline->zoom=qBound(2.,timeline->zoom*k,400.);timeline->resizeCanvas();if(auto z=findChild<QSlider*>("zoom")){filling=true;z->setValue(int(timeline->zoom));filling=false;}if(scroll)scroll->horizontalScrollBar()->setValue(int(Timeline::Header+at*timeline->zoom-vx));}
    void fitTimeline(){auto scroll=qobject_cast<QScrollArea*>(timeline->parentWidget()->parentWidget());double w=(scroll?scroll->viewport()->width():1000)-Timeline::Header-30;timeline->zoom=qBound(2.,w/qMax(1.,project.end()),400.);timeline->resizeCanvas();if(auto z=findChild<QSlider*>("zoom")){filling=true;z->setValue(int(timeline->zoom));filling=false;}if(scroll)scroll->horizontalScrollBar()->setValue(0);}
    void setBlade(bool on){timeline->blade=on;if(bladeButton)bladeButton->setChecked(on);status->setText(on?EchoesText("Лезвие: щёлкните по клипу в месте разреза. A — вернуться к выбору."):EchoesText("Инструмент «Выбор»."));timeline->update();}

    // ---------- Beats, subtitles, frames ----------
    void detectBeats(){
        auto c=timeline->current();if(!c||(c->kind!="audio"&&!(c->kind=="video"&&c->audio))||c->path.isEmpty()){status->setText(EchoesText("Выберите на таймлайне музыку (или видео со звуком), чтобы найти биты."));return;}
        Clip clip=*c;double length=qMin(clip.duration*clip.speed,900.);auto proc=new QProcess(this);status->setText(EchoesText("Слушаем музыку и ищем биты…"));
        connect(proc,qOverload<int,QProcess::ExitStatus>(&QProcess::finished),this,[this,proc,clip](int code,QProcess::ExitStatus){QByteArray data=proc->readAllStandardOutput();proc->deleteLater();if(code!=0){status->setText(EchoesText("Не удалось прочитать звук для поиска битов."));return;}
            const int rate=11025,hop=512;int count=data.size()/4,frames=count/hop;if(frames<16){status->setText(EchoesText("Фрагмент слишком короткий."));return;}QVector<double> energy(frames),flux(frames,0.);
            for(int f=0;f<frames;f++){double e=0;for(int s=0;s<hop;s++){float v;std::memcpy(&v,data.constData()+(f*hop+s)*4,4);if(std::isfinite(v))e+=v*v;}energy[f]=std::log(e+1e-6);}
            for(int f=8;f<frames;f++){double avg=0;for(int k=f-8;k<f;k++)avg+=energy[k];flux[f]=qMax(0.,energy[f]-avg/8);}
            double mean=0,var=0;for(double v:flux)mean+=v;mean/=frames;for(double v:flux)var+=(v-mean)*(v-mean);double threshold=mean+std::sqrt(var/frames)*1.1;
            QList<double> beats;double last=-1;for(int f=1;f<frames-1;f++)if(flux[f]>threshold&&flux[f]>=flux[f-1]&&flux[f]>=flux[f+1]){double src=double(f*hop)/rate;if(last<0||src-last>=.28){beats<<src;last=src;}if(beats.size()>=600)break;}
            if(beats.isEmpty()){status->setText(EchoesText("Явных битов не найдено."));return;}checkpoint();double from=clip.start,to=clip.start+clip.duration;
            project.markers.erase(std::remove_if(project.markers.begin(),project.markers.end(),[&](auto &m){return m.name==EchoesText("бит")&&m.time>=from-.001&&m.time<=to+.001;}),project.markers.end());
            for(double b:beats){double t=clip.reverse?clip.start+clip.duration-b/clip.speed:clip.start+b/clip.speed;if(t>=from&&t<=to)project.markers<<Marker{t,EchoesText("бит"),"#3fb6ff"};}
            std::sort(project.markers.begin(),project.markers.end(),[](auto &a,auto &b){return a.time<b.time;});edited();status->setText(QString(EchoesText("Найдено битов: %1. Выберите видео и нажмите «Разрезать по меткам».")).arg(beats.size()));});
        proc->start(ffmpeg,{"-v","error","-ss",num(clip.in),"-t",num(length),"-i",clip.path,"-vn","-ac","1","-ar","11025","-f","f32le","pipe:1"});
    }
    void cutAtMarkers(){auto c=editable();if(!c||groupLocked(*c))return;QList<double> cuts;for(auto &m:project.markers)if(m.time>c->start+.08&&m.time<c->start+c->duration-.08)cuts<<m.time;if(cuts.isEmpty()){status->setText(EchoesText("Внутри выбранного клипа нет меток."));return;}
        checkpoint();QStringList ids=timeline->linkedSelection?groupIds(*c):QStringList{c->id};QString first=c->id;double s=c->start,e=c->start+c->duration;QSet<int> tracks;for(auto &v:project.clips)if(ids.contains(v.id))tracks.insert(v.track);std::sort(cuts.begin(),cuts.end());
        // Every piece stays inside the original span, so later cuts find the right-hand pieces by position.
        for(double t:cuts){QStringList now;for(auto &v:project.clips)if(tracks.contains(v.track)&&v.start>=s-.001&&v.start+v.duration<=e+.001&&v.start<t-.04&&v.start+v.duration>t+.04)now<<v.id;splitAt(now,t);}
        timeline->selectOnly(first);edited();inspect();status->setText(QString(EchoesText("Клип разрезан на %1 частей.")).arg(cuts.size()+1));}
    void importSrt(){QString path=QFileDialog::getOpenFileName(this,EchoesText("Импорт субтитров"),{},EchoesText("Субтитры SRT (*.srt)"));if(path.isEmpty())return;QFile f(path);if(!f.open(QIODevice::ReadOnly|QIODevice::Text))return;QString text=QString::fromUtf8(f.readAll()).replace("\r","");
        QRegularExpression time(R"((\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+))");auto seconds=[](QRegularExpressionMatch m,int o){return m.captured(o).toDouble()*3600+m.captured(o+1).toDouble()*60+m.captured(o+2).toDouble()+m.captured(o+3).toDouble()/1000;};
        QList<Clip> cues;for(auto block:text.split(QRegularExpression("\n\\s*\n"))){auto lines=block.split('\n');for(int i=0;i<lines.size();i++){auto m=time.match(lines[i]);if(!m.hasMatch())continue;QString body=lines.mid(i+1).join('\n').trimmed();body.remove(QRegularExpression("<[^>]+>"));if(body.isEmpty())break;
            Clip c;c.kind="text";c.text=body;c.name=QString(body.left(45)).replace('\n',' ');c.start=seconds(m,1);c.duration=qMax(.2,seconds(m,5)-c.start);for(auto &t:textCatalog())if(t.id=="subtitle")applyTextStyle(c,t);c.y=int(project.height*.38);cues<<c;break;}}
        if(cues.isEmpty()){QMessageBox::warning(this,EchoesText("Субтитры"),EchoesText("В файле не найдено субтитров."));return;}checkpoint();int row=project.insertTrack(0,"text");project.lanes[row].name=EchoesText("Субтитры");for(auto &c:cues){c.track=row;project.clips<<c;}edited();inspect();status->setText(QString(EchoesText("Импортировано субтитров: %1")).arg(cues.size()));}
    void exportSrt(){QList<Clip> list;for(auto &c:project.clips)if(c.kind=="text")list<<c;if(list.isEmpty()){status->setText(EchoesText("В проекте нет текстовых клипов."));return;}std::sort(list.begin(),list.end(),[](auto &a,auto &b){return a.start<b.start;});
        QString path=QFileDialog::getSaveFileName(this,EchoesText("Экспорт субтитров"),{},EchoesText("Субтитры SRT (*.srt)"));if(path.isEmpty())return;if(!path.endsWith(".srt",Qt::CaseInsensitive))path+=".srt";
        auto stamp=[](double t){int ms=qRound(t*1000);return QString("%1:%2:%3,%4").arg(ms/3600000,2,10,QChar('0')).arg(ms/60000%60,2,10,QChar('0')).arg(ms/1000%60,2,10,QChar('0')).arg(ms%1000,3,10,QChar('0'));};
        QString out;for(int i=0;i<list.size();i++)out+=QString("%1\n%2 --> %3\n%4\n\n").arg(i+1).arg(stamp(list[i].start),stamp(list[i].start+list[i].duration),list[i].text);QSaveFile f(path);if(f.open(QIODevice::WriteOnly)){f.write(out.toUtf8());f.commit();status->setText(EchoesText("Субтитры сохранены: ")+path);}}
    void saveFrame(){if(project.clips.isEmpty())return;QString path=QFileDialog::getSaveFileName(this,EchoesText("Сохранить кадр"),{},EchoesText("Изображение PNG (*.png)"));if(path.isEmpty())return;if(!path.endsWith(".png",Qt::CaseInsensitive))path+=".png";
        auto dir=QSharedPointer<QTemporaryDir>::create();ExportOptions opt;opt.still=path;auto r=plan(frameProject(project,timeline->playhead),dir->path(),{},false,true,opt);if(!r.error.isEmpty()){status->setText(r.error);return;}
        auto proc=new QProcess(this);connect(proc,qOverload<int,QProcess::ExitStatus>(&QProcess::finished),this,[this,proc,dir,path](int code,QProcess::ExitStatus){proc->deleteLater();status->setText(code==0?EchoesText("Кадр сохранён: ")+path:EchoesText("Не удалось сохранить кадр."));});proc->start(ffmpeg,r.args);status->setText(EchoesText("Сохраняем кадр…"));}

    // ---------- Menus ----------
    void contextMenu(){QMenu menu(this);auto c=timeline->current();
        menu.addAction(EchoesText("Разрезать под курсором\tS"),this,[this]{split();});menu.addAction(EchoesText("Обрезать начало до курсора\tQ"),this,[this]{trimToPlayhead(true);});menu.addAction(EchoesText("Обрезать конец до курсора\tW"),this,[this]{trimToPlayhead(false);});menu.addSeparator();
        menu.addAction(EchoesText("Копировать\tCtrl+C"),this,[this]{copy();});menu.addAction(EchoesText("Вырезать\tCtrl+X"),this,[this]{copy();remove();});menu.addAction(EchoesText("Вставить\tCtrl+V"),this,[this]{paste(timeline->playhead);})->setEnabled(!clipboard.isEmpty());menu.addAction(EchoesText("Дублировать\tCtrl+D"),this,[this]{duplicate();});
        menu.addAction(EchoesText("Удалить\tDelete"),this,[this]{remove();});menu.addAction(EchoesText("Удалить со сдвигом\tShift+Delete"),this,[this]{rippleDelete();});menu.addSeparator();
        if(c&&(c->kind=="video"||c->kind=="audio")){auto sm=menu.addMenu(EchoesText("Скорость"));for(double v:{.25,.5,.75,1.,1.5,2.,3.,4.})sm->addAction(QString("%1×").arg(v),this,[this,v]{setSpeed(v);});menu.addAction(c->reverse?EchoesText("Выключить реверс\tR"):EchoesText("Задом наперёд\tR"),this,[this]{toggleReverse();});}
        if(c&&c->kind=="video")menu.addAction(EchoesText("Стоп-кадр\tF"),this,[this]{freezeFrame();});
        if(c&&(c->kind=="video"||c->kind=="image")){auto fm=menu.addMenu(EchoesText("Кадр"));for(auto f:QList<QPair<QString,QString>>{{"fit",EchoesText("Вписать")},{"fill",EchoesText("Заполнить")},{"stretch",EchoesText("Растянуть")},{"blur",EchoesText("Размытый фон")}})fm->addAction(f.second,this,[this,f]{mutate([f](Clip&x){x.fit=f.first;},true);});}
        menu.addAction(EchoesText("Ключевой кадр\tK"),this,[this]{toggleKey();});menu.addSeparator();
        if(c&&c->kind=="video")menu.addAction(EchoesText("Отделить исходный звук"),this,[this]{extractAudio();});if(c&&!c->group.isEmpty())menu.addAction(EchoesText("Разъединить видео и звук"),this,[this]{unlink();});
        if(c&&(c->kind=="audio"||c->kind=="video"))menu.addAction(EchoesText("Найти биты"),this,[this]{detectBeats();});
        auto labels=menu.addMenu(EchoesText("Цвет клипа"));for(auto l:QList<QPair<QString,QString>>{{"",EchoesText("Без цвета")},{"#e8822c",EchoesText("Оранжевый")},{"#e5332a",EchoesText("Красный")},{"#e8c32c",EchoesText("Жёлтый")},{"#3fae5a",EchoesText("Зелёный")},{"#2fb8c9",EchoesText("Бирюзовый")},{"#3d6fe0",EchoesText("Синий")},{"#8a55d6",EchoesText("Фиолетовый")},{"#e055a5",EchoesText("Розовый")}}){auto a=labels->addAction(l.second,this,[this,l]{mutate([l](Clip&x){x.label=l.first;},true);});if(!l.first.isEmpty())a->setIcon(drawnIcon([l](QPainter&p,QRectF r){p.setBrush(QColor(l.first));p.setPen(Qt::NoPen);p.drawEllipse(r);},QColor(l.first),14));}
        menu.exec(QCursor::pos());}
    void emptyMenu(double t,int row){QMenu menu(this);menu.addAction(EchoesText("Вставить сюда"),this,[this,t]{paste(t);})->setEnabled(!clipboard.isEmpty());menu.addAction(EchoesText("Поставить метку здесь"),this,[this,t]{toggleMarker(t);});menu.addAction(EchoesText("Добавить текст здесь"),this,[this,t]{seekTo(t);addText("plain");});
        if(row>=0&&row<project.tracks)menu.addAction(EchoesText("Удалить пустоты на этой дорожке"),this,[this,row]{closeGaps(row);});menu.addAction(EchoesText("Импорт медиа…"),this,[this]{importFiles();});menu.exec(QCursor::pos());}
    void rulerMenu(double t){QMenu menu(this);int nearest=-1;double best=8/timeline->zoom;for(int i=0;i<project.markers.size();i++)if(std::abs(project.markers[i].time-t)<best){best=std::abs(project.markers[i].time-t);nearest=i;}
        menu.addAction(EchoesText("Поставить метку здесь"),this,[this,t]{toggleMarker(t);});
        if(nearest>=0){menu.addAction(EchoesText("Переименовать метку…"),this,[this,nearest]{bool ok;QString n=QInputDialog::getText(this,EchoesText("Метка"),EchoesText("Название метки"),QLineEdit::Normal,project.markers[nearest].name,&ok);if(ok){checkpoint();project.markers[nearest].name=n;edited();}});menu.addAction(EchoesText("Удалить метку"),this,[this,nearest]{checkpoint();project.markers.removeAt(nearest);edited();});
            auto colors=menu.addMenu(EchoesText("Цвет метки"));for(auto col:{"#e8822c","#e5332a","#e8c32c","#3fae5a","#3fb6ff","#8a55d6","#e055a5"})colors->addAction(drawnIcon([col](QPainter&p,QRectF r){p.setBrush(QColor(col));p.setPen(Qt::NoPen);p.drawEllipse(r);},QColor(col),14),"",this,[this,nearest,col]{checkpoint();project.markers[nearest].color=col;edited();});}
        menu.addSeparator();menu.addAction(EchoesText("Точка входа здесь"),this,[this,t]{seekTo(t);setRange(true);});menu.addAction(EchoesText("Точка выхода здесь"),this,[this,t]{seekTo(t);setRange(false);});menu.addAction(EchoesText("Сбросить вход и выход"),this,[this]{checkpoint();project.inPoint=project.outPoint=-1;edited();updateExportSummary();});menu.exec(QCursor::pos());}
    void trackMenu(int row){QMenu menu(this);
        menu.addAction(EchoesText("Переименовать дорожку…"),this,[this,row]{bool ok;QString n=QInputDialog::getText(this,EchoesText("Дорожка"),EchoesText("Название"),QLineEdit::Normal,project.trackName(row),&ok);if(ok){checkpoint();project.lanes[row].name=n.trimmed();edited();}});
        menu.addAction(project.muted(row)?EchoesText("Включить дорожку"):EchoesText("Выключить дорожку"),this,[this,row]{checkpoint();project.lanes[row].muted=!project.lanes[row].muted;edited();});menu.addAction(project.locked(row)?EchoesText("Разблокировать"):EchoesText("Заблокировать"),this,[this,row]{checkpoint();project.lanes[row].locked=!project.lanes[row].locked;edited();inspect();});
        menu.addAction(EchoesText("Удалить пустоты на дорожке"),this,[this,row]{closeGaps(row);});menu.addSeparator();
        for(auto k:QList<QPair<QString,QString>>{{"video",EchoesText("Добавить дорожку видео")},{"audio",EchoesText("Добавить дорожку звука")},{"text",EchoesText("Добавить дорожку титров")}})menu.addAction(k.second,this,[this,row,k]{checkpoint();project.insertTrack(row,k.first);edited();});
        bool empty=true;for(auto &c:project.clips)empty&=c.track!=row;auto del=menu.addAction(EchoesText("Удалить пустую дорожку"),this,[this,row]{checkpoint();project.lanes.removeAt(row);for(auto &c:project.clips)if(c.track>row)c.track--;if(project.lanes.isEmpty())project.lanes<<Project::Track{};project.sync();edited();});del->setEnabled(empty&&project.tracks>1);
        menu.exec(QCursor::pos());}
    void hotkeys(){QMessageBox box(this);box.setWindowTitle(EchoesText("Горячие клавиши"));box.setTextFormat(Qt::RichText);
        box.setText(EchoesText("<table cellspacing=6><tr><td><b>Пробел</b></td><td>воспроизвести / пауза</td><td><b>S, Ctrl+B</b></td><td>разрезать</td></tr><tr><td><b>← →</b></td><td>кадр назад / вперёд (Shift — секунда)</td><td><b>↑ ↓</b></td><td>предыдущая / следующая склейка</td></tr>") +
            EchoesText("<tr><td><b>Q / W</b></td><td>обрезать начало / конец до курсора</td><td><b>Delete</b></td><td>удалить (Shift — со сдвигом)</td></tr><tr><td><b>Ctrl+C / X / V</b></td><td>копировать / вырезать / вставить</td><td><b>Ctrl+D</b></td><td>дублировать</td></tr>") +
            EchoesText("<tr><td><b>A / B</b></td><td>выбор / лезвие</td><td><b>N</b></td><td>привязка</td></tr><tr><td><b>K</b></td><td>ключевой кадр</td><td><b>M</b></td><td>метка (Shift+↑↓ — переход)</td></tr><tr><td><b>I / O</b></td><td>точки входа / выхода</td><td><b>Alt+X</b></td><td>сбросить вход/выход</td></tr>") +
            EchoesText("<tr><td><b>F</b></td><td>стоп-кадр</td><td><b>R</b></td><td>реверс</td></tr><tr><td><b>T</b></td><td>добавить текст</td><td><b>Ctrl+колесо</b></td><td>масштаб таймлайна</td></tr><tr><td><b>Shift+Z</b></td><td>уместить проект</td><td><b>Shift+1…5</b></td><td>страницы инспектора</td></tr>") +
            EchoesText("<tr><td><b>Ctrl+Z / Y</b></td><td>отменить / повторить</td><td><b>Ctrl+S / O / N</b></td><td>сохранить / открыть / новый</td></tr><tr><td><b>Ctrl+E</b></td><td>экспорт</td><td><b>Ctrl+Shift+P</b></td><td>режим «Профи»</td></tr></table>") +
            EchoesText("<p>В окне просмотра выбранный слой можно двигать мышью, тянуть за угол для масштаба и крутить колесо.</p>"));box.exec();}

    // ---------- Media ----------
    void loadLibrary(QString path){QFile file(path);if(!file.open(QIODevice::ReadOnly))return;for(auto v:QJsonDocument::fromJson(file.readAll()).array()){auto o=v.toObject();QString path=o["path"].toString();if(!QFileInfo::exists(path))continue;auto i=new QListWidgetItem(glyphIcon("♫",QColor(pal::wave)),o["artist"].toString()+" — "+o["title"].toString(),library);i->setData(Qt::UserRole,path);i->setToolTip(path);}}
    void addToBin(QString path){for(int i=0;i<bin->count();i++)if(bin->item(i)->data(Qt::UserRole).toString()==path)return;auto i=new QListWidgetItem(QFileInfo(path).fileName(),bin);i->setData(Qt::UserRole,path);i->setToolTip(path);refreshBinIcons();}
    void refreshBinIcons(){
        static const QStringList images{"png","jpg","jpeg","webp","bmp"},audio{"mp3","wav","flac","m4a","ogg","aac","opus"};
        for(int i=0;i<bin->count();i++){auto it=bin->item(i);if(it->data(Qt::UserRole+1).toBool())continue;QString path=it->data(Qt::UserRole).toString(),ext=QFileInfo(path).suffix().toLower();
            if(audio.contains(ext)){it->setIcon(drawnIcon([](QPainter&p,QRectF r){p.fillRect(r.adjusted(-4,-4,4,4),QColor(pal::audio));p.setPen(QPen(QColor(pal::wave),2));for(int k=0;k<12;k++){double h=r.height()*(.15+.35*std::abs(std::sin(k*1.7)));double x=r.left()+r.width()*(k+.5)/12;p.drawLine(QPointF(x,r.center().y()-h),QPointF(x,r.center().y()+h));}},QColor(pal::wave),56));it->setData(Qt::UserRole+1,true);}
            else if(images.contains(ext)){QImageReader reader(path);reader.setScaledSize(reader.size().scaled(224,126,Qt::KeepAspectRatio));QImage img=reader.read();if(!img.isNull())it->setIcon(QIcon(QPixmap::fromImage(img)));it->setData(Qt::UserRole+1,true);}
            else if(auto pix=media->thumbnail(path,1.0)){it->setIcon(QIcon(*pix));it->setData(Qt::UserRole+1,true);}}
    }
    void rebuildBin(){bin->clear();for(auto &c:project.clips)if(!c.path.isEmpty())addToBin(c.path);}
    void importFiles(){for(auto p:QFileDialog::getOpenFileNames(this,EchoesText("Добавить видео, музыку или изображения"),{},EchoesText("Медиа (*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.mp3 *.wav *.flac *.m4a *.ogg *.aac *.opus *.png *.jpg *.jpeg *.webp *.bmp *.gif);;Все файлы (*)")))enqueue(p);}
    void enqueue(QString path,double at=-1,int row=-1){if(!QFileInfo(path).isFile()){status->setText(EchoesText("Нет локального файла: ")+path);return;}importQueue.enqueue({path,at<0?timeline->playhead:at,row});probeNext();}
    void probeNext(){
        if(probing||importQueue.isEmpty())return;probing=true;Import job=importQueue.dequeue();auto proc=new QProcess(this);probes<<proc;
        auto done=[this,proc,job](bool ok){if(!probes.removeOne(proc))return;auto o=QJsonDocument::fromJson(proc->readAllStandardOutput()).object();bool video=false,audio=false;double sourceFps=30;int sw=0,sh=0;
            for(auto v:o["streams"].toArray()){auto stream=v.toObject();auto type=stream["codec_type"].toString();bool realVideo=type=="video"&&!stream["disposition"].toObject()["attached_pic"].toInt();video|=realVideo;audio|=type=="audio";if(realVideo){sw=stream["width"].toInt();sh=stream["height"].toInt();auto rate=stream["avg_frame_rate"].toString().split('/');if(rate.size()==2&&rate[1].toDouble()>0)sourceFps=qBound(1.,rate[0].toDouble()/rate[1].toDouble(),240.);}}
            QString ext=QFileInfo(job.path).suffix().toLower();bool image=QStringList{"png","jpg","jpeg","webp","bmp"}.contains(ext);
            if(ok&&(video||audio)){checkpoint();media->invalidate(job.path);Clip c;c.path=job.path;c.name=QFileInfo(job.path).completeBaseName();c.kind=image?"image":video?"video":"audio";c.source=o["format"].toObject()["duration"].toString().toDouble();if(c.source<=0)c.source=5;c.duration=c.kind=="image"?5:c.source;c.sourceFps=sourceFps;c.audio=audio;c.start=job.at;c.srcW=sw;c.srcH=sh;
                QString kind=Project::type(c);c.track=job.row>=0&&job.row<project.tracks&&project.lanes[job.row].kind==kind&&!project.locked(job.row)?job.row:project.ensureTrack(kind);
                if(job.row<0)for(auto &v:project.clips)if(v.track==c.track&&v.start<=c.start+.01)c.start=qMax(c.start,v.start+v.duration);
                QList<Clip> imported;if(c.kind=="video"&&audio){c.audio=false;c.group=QUuid::createUuid().toString();Clip sound=c;sound.kind="audio";sound.audio=true;sound.id=QUuid::createUuid().toString();sound.name+=EchoesText(" • звук");sound.track=project.ensureTrack("audio");imported<<sound;}imported.prepend(c);project.clips.append(imported);timeline->selectOnly(c.id);addToBin(job.path);edited();inspect();status->setText(EchoesText("Добавлен: ")+c.name+EchoesText(" • кадры и звук загружаются в фоне"));
            }else status->setText(EchoesText("Не удалось прочитать медиа: ")+job.path+" • "+QString::fromUtf8(proc->readAllStandardError()).right(200));
            proc->deleteLater();probing=false;probeNext();if(!probing)previewTimer.start(150);
        };
        connect(proc,qOverload<int,QProcess::ExitStatus>(&QProcess::finished),this,[done](int code,QProcess::ExitStatus st){done(code==0&&st==QProcess::NormalExit);});connect(proc,&QProcess::errorOccurred,this,[done](QProcess::ProcessError e){if(e==QProcess::FailedToStart)done(false);});proc->start(ffprobe,{"-v","error","-show_entries","format=duration:stream=codec_type,avg_frame_rate,width,height:stream_disposition=attached_pic","-of","json",job.path});
    }

    // ---------- Files and export ----------
    bool confirmDiscard(){if(!dirty)return true;auto b=QMessageBox::question(this,EchoesText("Несохранённый монтаж"),EchoesText("Сохранить изменения проекта?"),QMessageBox::Save|QMessageBox::Discard|QMessageBox::Cancel);return b==QMessageBox::Save?saveProject():b==QMessageBox::Discard;}
    void newProject(){if(probing||(render&&!renderProxy)){status->setText(EchoesText("Дождитесь импорта или экспорта."));return;}if(!confirmDiscard())return;checkpoint();project=Project();projectPath.clear();timeline->selectOnly({});timeline->playhead=0;wantPlaying=false;bin->clear();frameView->image=QImage();edited();inspect();syncFormat();}
    bool saveProject(){QString path=projectPath;if(path.isEmpty())path=QFileDialog::getSaveFileName(this,EchoesText("Сохранить монтаж"),{},"ECHOES Montage (*.echoedit)");if(path.isEmpty())return false;if(!path.endsWith(".echoedit"))path+=".echoedit";if(!write(path,project.json())){QMessageBox::warning(this,EchoesText("Ошибка"),EchoesText("Не удалось сохранить проект: ")+path);return false;}projectPath=path;dirty=false;setWindowTitle("ECHOES Montage • "+QFileInfo(path).fileName());projectLabel->setText(QFileInfo(path).completeBaseName());status->setText(EchoesText("Сохранено: ")+path);write(autosavePath,project.json());return true;}
    void openProject(){if(probing||(render&&!renderProxy)){status->setText(EchoesText("Дождитесь импорта или экспорта."));return;}if(!confirmDiscard())return;QString path=QFileDialog::getOpenFileName(this,EchoesText("Открыть монтаж"),{},"ECHOES Montage (*.echoedit)");if(path.isEmpty())return;QFile f(path);if(!f.open(QIODevice::ReadOnly))return;Project candidate;if(!candidate.load(QJsonDocument::fromJson(f.readAll()).object())){QMessageBox::warning(this,EchoesText("Ошибка"),EchoesText("Неподдерживаемый или повреждённый проект."));return;}checkpoint();project=candidate;projectPath=path;timeline->selectOnly({});wantPlaying=false;edited();dirty=false;projectLabel->setText(QFileInfo(path).completeBaseName());inspect();syncFormat();rebuildBin();QStringList missing;for(auto &c:project.clips)if(c.kind!="text"&&c.kind!="color"&&c.kind!="adjust"&&!QFileInfo::exists(c.path))missing<<c.path;if(!missing.isEmpty())QMessageBox::warning(this,EchoesText("Недостающие медиа"),missing.join("\n"));}
    ExportOptions exportOptions(){auto e=exportCatalog().value(exportPreset->currentIndex());ExportOptions o;o.container=e.container;o.codec=e.codec;o.audio=e.audio;int lo=e.codec=="vp9"?15:14,hi=e.codec=="vp9"?45:32;o.crf=e.crf>0?qRound(hi-(hi-lo)*exportQuality->value()/100.):e.crf;
        o.height=exportRes->currentData().toInt();o.fps=exportFps->currentData().toInt();o.loud=exportLoud->isChecked()&&e.container!="gif";o.speed=exportSpeed->currentData().toString();
        if(exportRange->currentIndex()==1){o.from=qMax(0.,project.inPoint);o.to=project.outPoint>0?project.outPoint:project.end();}return o;}
    void updateExportSummary(){if(!exportSummary)return;auto o=exportOptions();auto e=exportCatalog().value(exportPreset->currentIndex());int h=o.height>0?o.height:project.height,w=o.height>0?int(double(h)*project.width/project.height)/2*2:project.width;double from=o.from>=0?o.from:0,to=o.to>0?o.to:project.end();
        bool audioOnly=e.container=="mp3"||e.container=="wav";exportSummary->setText(QString(EchoesText("<b>%1</b><br>%2%3 • длительность %4<br>Файл: .%5%6")).arg(e.name,audioOnly?QString(EchoesText("только звук")):QString(EchoesText("%1×%2 • %3 к/с")).arg(w).arg(h).arg(o.fps>0?o.fps:project.fps),e.crf>0?QString(EchoesText(" • качество %1")).arg(exportQuality->value()):QString(),timecode(qMax(0.,to-from),project.fps),e.container,o.loud?EchoesText(" • громкость −14 LUFS"):""));}
    void exportProject(){if(render&&!renderProxy)return;if(project.clips.isEmpty()){status->setText(EchoesText("Проект пуст — добавьте медиа."));return;}auto o=exportOptions();QString ext=o.container,filter=QString("%1 (*.%2)").arg(ext.toUpper(),ext);
        QString path=askSave(EchoesText("Экспорт"),QFileInfo(projectPath).completeBaseName(),filter);if(path.isEmpty())return;if(!path.endsWith("."+ext,Qt::CaseInsensitive))path+="."+ext;if(render){pendingExport=path;pendingOptions=o;cancelledPreview=true;render->kill();}else startRender(false,path,o);}
    void startRender(bool proxy,QString output,ExportOptions opt={}){
        if(render||probing||project.clips.isEmpty())return;for(auto &c:project.clips)if(QFileInfo(c.path).absoluteFilePath()==QFileInfo(output).absoluteFilePath()){if(!proxy)QMessageBox::warning(this,EchoesText("Экспорт"),EchoesText("Нельзя перезаписать исходный медиафайл."));return;}
        renderDir.reset(new QTemporaryDir);finalPath=output;partialPath=QFileInfo(output).absolutePath()+"/.echoes-render-"+QUuid::createUuid().toString(QUuid::Id128)+"."+QFileInfo(output).suffix();auto r=plan(project,renderDir->path(),partialPath,proxy,false,opt);
        if(!r.error.isEmpty()){status->setText(r.error);if(!proxy)QMessageBox::warning(this,EchoesText("Подготовка монтажа"),r.error);renderDir.reset();return;}
        renderSnapshot=project.json();renderProxy=proxy;renderDuration=r.duration;cancelledPreview=false;renderLog.clear();progressBuffer.clear();render=new QProcess(this);cancelButton->setVisible(!proxy);progress->setVisible(!proxy);progress->setValue(0);if(!proxy){exportButton->setEnabled(false);exportButton->setText(EchoesText("Идёт экспорт…"));}
        status->setText(proxy?EchoesText("Предпросмотр обновляется в фоне…"):EchoesText("Экспорт…"));
        connect(render,&QProcess::readyReadStandardError,this,[this]{renderLog+=render->readAllStandardError();if(renderLog.size()>16000)renderLog=renderLog.right(16000);});
        connect(render,&QProcess::readyReadStandardOutput,this,[this]{progressBuffer+=render->readAllStandardOutput();int at;while((at=progressBuffer.indexOf('\n'))>=0){auto line=progressBuffer.left(at);progressBuffer.remove(0,at+1);if(line.startsWith("out_time_us=")){double t=line.mid(12).trimmed().toDouble()/1000000;int pc=qBound(0,int(t/qMax(.01,renderDuration)*100),100);status->setText(QString("%1: %2%").arg(renderProxy?EchoesText("Обновляем предпросмотр"):EchoesText("Экспорт")).arg(pc));if(!renderProxy)progress->setValue(pc);}}});
        connect(render,qOverload<int,QProcess::ExitStatus>(&QProcess::finished),this,[this](int code,QProcess::ExitStatus s){finishRender(code==0&&s==QProcess::NormalExit);});connect(render,&QProcess::errorOccurred,this,[this](QProcess::ProcessError e){if(e==QProcess::FailedToStart){renderLog+=render->errorString().toUtf8();finishRender(false);}});render->start(ffmpeg,r.args);
    }
    void finishRender(bool ok){
        if(!render)return;bool proxy=renderProxy,cancelled=cancelledPreview,stale=proxy&&renderSnapshot!=project.json();renderLog+=render->readAllStandardError();render->deleteLater();render=nullptr;cancelButton->hide();progress->hide();exportButton->setEnabled(true);exportButton->setText(EchoesText("⇪   Экспортировать…"));
        if(proxy&&(cancelled||stale)){ok=false;discardedPreviews++;}
        if(ok){QString backup=finalPath+".echoes-backup-"+QUuid::createUuid().toString(QUuid::Id128);bool existed=QFileInfo::exists(finalPath);if(existed&&!QFile::rename(finalPath,backup))ok=false;if(ok&&!QFile::rename(partialPath,finalPath)){ok=false;if(existed)QFile::rename(backup,finalPath);}else if(ok&&existed)QFile::remove(backup);}
        if(ok&&proxy){vlc.stop();if(!proxyPath.isEmpty())QFile::remove(proxyPath);proxyPath=finalPath;proxyValid=true;completedPreviews++;previewHint->setText(EchoesText("● всё актуально"));status->setText(EchoesText("Готово • кадры, эффекты и звук актуальны"));if(wantPlaying){stopChunks();launchPlayback();}}
        else if(ok){status->setText(EchoesText("Экспорт завершён: ")+finalPath);if(!closing&&!testing){QMessageBox box(QMessageBox::Information,EchoesText("Экспорт завершён"),finalPath,QMessageBox::Ok,this);auto open=box.addButton(EchoesText("Показать в папке"),QMessageBox::ActionRole);box.exec();if(box.clickedButton()==open)QProcess::startDetached("explorer.exe",{"/select,",QDir::toNativeSeparators(finalPath)});}}
        else{QFile::remove(partialPath);if(!cancelled&&!stale){status->setText(proxy?EchoesText("Не удалось обновить предпросмотр. Проверьте медиафайлы."):EchoesText("Экспорт остановлен или завершился ошибкой."));if(!proxy&&!closing&&!testing)QMessageBox::warning(this,EchoesText("Экспорт не завершён"),QString::fromUtf8(renderLog).right(2400));}}
        renderDir.reset();
        if(closePending){QTimer::singleShot(0,this,[this]{close();});return;}
        if(!pendingExport.isEmpty()){QString output=pendingExport;ExportOptions o=pendingOptions;pendingExport.clear();QTimer::singleShot(0,this,[this,output,o]{startRender(false,output,o);});}
        else if((proxy&&(stale||cancelled))||!proxy)previewTimer.start(0);
    }

    // ---------- Fast playback: short proxy chunks rendered from the cursor ----------
    void startChunk(double from,bool playNow){
        if(quick){quick->disconnect(this);quick->kill();quick->waitForFinished(500);quick->deleteLater();quick=nullptr;}
        // The first chunk is short so playback starts quickly; later ones are prepared ahead.
        double to=qMin(project.end(),from+(playNow?2.:8.));if(to-from<.05){stopPlayback();return;}
        QString out=temp.path()+"/chunk_"+QUuid::createUuid().toString(QUuid::Id128)+".mp4";auto dir=QSharedPointer<QTemporaryDir>::create();
        ExportOptions small;small.proxyWidth=480;auto r=plan(rangeProject(project,from,to),dir->path(),out,true,false,small);if(!r.error.isEmpty()){status->setText(r.error);stopPlayback();return;}
        quint64 generation=chunkGeneration;auto proc=new QProcess(this);quick=proc;auto clock=QSharedPointer<QElapsedTimer>::create();clock->start();
        connect(proc,qOverload<int,QProcess::ExitStatus>(&QProcess::finished),this,[this,proc,dir,out,from,to,generation,playNow,clock](int code,QProcess::ExitStatus s){
            if(quick==proc)quick=nullptr;proc->deleteLater();chunkTimes<<clock->elapsed();
            if(generation!=chunkGeneration||code!=0||s!=QProcess::NormalExit){QFile::remove(out);return;}
            if(playNow||chunkWaiting)playChunk(out,from,to-from);else{if(!nextFile.isEmpty())QFile::remove(nextFile);nextFile=out;nextFrom=from;nextLen=to-from;}});
        proc->start(ffmpeg,r.args);
    }
    void playChunk(QString file,double from,double len){
        if(!wantPlaying){QFile::remove(file);return;}
        chunkWaiting=false;if(!chunkFile.isEmpty()&&chunkFile!=file)QFile::remove(chunkFile);chunkFile=file;chunkFrom=from;chunkLen=len;chunkMode=true;paused=false;startingVlc=true;
        double target=timeline->playhead-from;quint64 generation=++playGeneration;vlc.play(file);previewStack->setCurrentWidget(screen);previewHint->setText(EchoesText("● быстрый просмотр"));status->setText(EchoesText("Воспроизведение"));
        auto wait=new QTimer(this);auto attempts=QSharedPointer<int>::create(0);
        connect(wait,&QTimer::timeout,this,[this,wait,attempts,generation,target]{if(generation!=playGeneration||++*attempts>200){wait->deleteLater();return;}if(vlc.state()==3){if(testing)vlc.mute(true);if(target>.08)vlc.seek(target);startingVlc=false;wait->deleteLater();}});wait->start(15);
        if(from+len<project.end()-.05&&nextFile.isEmpty())startChunk(from+len,false);
    }
    void chunkTick(){
        double t=vlc.time();int state=vlc.state();if(t>=0&&state==3)timeline->playhead=qMin(project.end(),chunkFrom+t);
        if(state==6||(state==3&&t>=chunkLen-.04)){double next=chunkFrom+chunkLen;
            if(next>=project.end()-.05){stopPlayback();timeline->playhead=qMax(0.,project.end()-.001);}
            else if(!nextFile.isEmpty()&&std::abs(nextFrom-next)<.01){QString f=nextFile;nextFile.clear();timeline->playhead=next;playChunk(f,nextFrom,nextLen);}
            else if(!chunkWaiting){chunkWaiting=true;vlc.pause(true);timeline->playhead=next;previewHint->setText(EchoesText("● буферизация…"));if(!quick)startChunk(next,true);}}
        timeline->update();updateTime();
    }
    void stopChunks(){chunkMode=false;chunkWaiting=false;++chunkGeneration;if(quick){quick->disconnect(this);quick->kill();quick->waitForFinished(500);quick->deleteLater();quick=nullptr;}for(auto f:{chunkFile,nextFile})if(!f.isEmpty())QFile::remove(f);chunkFile.clear();nextFile.clear();}
    void stopPlayback(){wantPlaying=false;paused=true;++playGeneration;startingVlc=false;vlc.pause(true);if(chunkMode||quick){vlc.stop();stopChunks();}updatePlayButton();frameTimer.start(0);previewStack->setCurrentWidget(frameView);previewTimer.start(400);}

    // ---------- Stabilisation, speed ramps, Ken Burns ----------
    void ensureStabilization(){
        for(auto &c:project.clips){if(c.stabilize<=0||c.path.isEmpty())continue;QString file=stabilizeFile(c);if(QFileInfo::exists(file)||stabilizing.contains(file)||stabilizeFailed.contains(file))continue;
            stabilizing.insert(file);auto proc=new QProcess(this);status->setText(EchoesText("Стабилизация: анализируем движение камеры…"));
            connect(proc,qOverload<int,QProcess::ExitStatus>(&QProcess::finished),this,[this,proc,file](int code,QProcess::ExitStatus){proc->deleteLater();stabilizing.remove(file);
                if(code!=0||!QFileInfo::exists(file)){QFile::remove(file);stabilizeFailed.insert(file);status->setText(EchoesText("Не удалось проанализировать видео для стабилизации."));return;}status->setText(EchoesText("Стабилизация готова."));edited();});
            proc->start(ffmpeg,stabilizeArgs(c));}
    }
    static QList<QPair<QString,QList<QPair<double,double>>>> ramps(){return {{EchoesText("Пуля"),{{.4,1},{.2,.25},{.4,1}}},{EchoesText("Герой"),{{.3,1.6},{.4,.35},{.3,1.6}}},{EchoesText("Монтаж"),{{.25,2},{.25,.5},{.25,2},{.25,.5}}},{EchoesText("Рывок"),{{.35,.5},{.3,3},{.35,.5}}},{EchoesText("Плавное замедление"),{{.25,1},{.25,.7},{.25,.45},{.25,.3}}},{EchoesText("Плавное ускорение"),{{.25,.3},{.25,.45},{.25,.7},{.25,1}}}};}
    // Splits the clip (and its linked sound) into consecutive parts with their own speeds.
    void speedRamp(QString name,QList<QPair<double,double>> segments){
        auto c=editable();if(!c||(c->kind!="video"&&c->kind!="audio")){status->setText(EchoesText("Кривая скорости работает для видео и звука."));return;}if(c->reverse){status->setText(EchoesText("Кривая скорости недоступна для реверса."));return;}if(groupLocked(*c))return;
        QString first=c->id;QStringList ids=timeline->linkedSelection?groupIds(*c):QStringList{c->id};QList<Clip> originals;for(auto &v:project.clips)if(ids.contains(v.id))originals<<v;
        double total=0,length=0;for(auto &s:segments)total+=s.first;for(auto &s:segments)length+=c->duration*c->speed*s.first/total/s.second;double oldEnd=c->start+c->duration,delta=c->start+length-oldEnd;
        checkpoint();QSet<int> tracks;for(auto &o:originals)tracks.insert(o.track);for(auto &v:project.clips)if(tracks.contains(v.track)&&!ids.contains(v.id)&&v.start>=oldEnd-.001)v.start+=delta;
        project.clips.removeIf([&](const Clip&v){return ids.contains(v.id);});QStringList groups;for(int i=0;i<segments.size();i++)groups<<QUuid::createUuid().toString();
        for(auto o:originals){double content=o.duration*o.speed,in=o.in,start=o.start;for(int i=0;i<segments.size();i++){Clip s=o;if(i)s.id=QUuid::createUuid().toString();double part=content*segments[i].first/total;
            s.in=in;s.speed=segments[i].second;s.duration=part/s.speed;s.start=start;s.keys={};s.motion=false;s.animationOffset=0;if(i>0){s.trans.clear();s.inAnim.clear();s.fadeIn=0;}if(i<segments.size()-1){s.outAnim.clear();s.fadeOut=0;}
            if(!o.group.isEmpty())s.group=groups[i];in+=part;start+=s.duration;project.clips<<Clip::read(s.json());}}
        timeline->selectOnly(first);edited();inspect();status->setText(EchoesText("Кривая скорости «")+name+EchoesText("» применена."));
    }
    void kenBurns(QString kind){
        mutate([this,kind](Clip&c){double W=project.width,H=project.height,s0=1,s1=1,x0=0,x1=0,y0=0,y1=0;
            if(kind=="in")s1=1.25;else if(kind=="out")s0=1.25;else{s0=s1=1.2;double dx=W*.07,dy=H*.07;if(kind=="left"){x0=dx;x1=-dx;}else if(kind=="right"){x0=-dx;x1=dx;}else if(kind=="up"){y0=dy;y1=-dy;}else{y0=-dy;y1=dy;}}
            auto key=[&](double t,double s,double x,double y){return QJsonObject{{"time",t+c.animationOffset},{"x",c.x+x},{"y",c.y+y},{"scale",s*c.scale},{"rotation",c.rotation},{"opacity",c.opacity},{"volume",c.gain}};};
            c.keys={key(0,s0,x0,y0),key(c.duration,s1,x1,y1)};c.motion=false;c.easing="smooth";});
        status->setText(EchoesText("Плавное движение камеры добавлено ключевыми кадрами."));
    }

    // ---------- Auto subtitles and voice-over ----------
    QString askSave(QString title,QString name,QString filter){if(!autoPath.isEmpty()){QString p=autoPath;autoPath.clear();return p;}return QFileDialog::getSaveFileName(this,title,name,filter);}
    void autoSubtitles(QString language={}){
        if(project.clips.isEmpty()){status->setText(EchoesText("Сначала добавьте видео или звук с речью."));return;}if(job){status->setText(EchoesText("Подождите: уже идёт обработка."));return;}
        if(python.isEmpty()||!QFileInfo::exists(python)||!QFileInfo::exists(transcriber)){QMessageBox::warning(this,EchoesText("Авто-субтитры"),EchoesText("Не найден Python ECHOES или скрипт распознавания. Переустановите ECHOES."));return;}
        if(language.isEmpty()){QStringList names{EchoesText("Определить автоматически"),EchoesText("Русский"),"English",EchoesText("Українська"),"Deutsch","Español","Français"},codes{"auto","ru","en","uk","de","es","fr"};bool ok;
            QString pick=QInputDialog::getItem(this,EchoesText("Авто-субтитры"),EchoesText("Язык речи в ролике:"),names,0,false,&ok);if(!ok)return;language=codes.value(names.indexOf(pick),"auto");}
        auto dir=QSharedPointer<QTemporaryDir>::create();QString wav=dir->path()+"/speech.wav";ExportOptions opt;opt.container="wav";opt.codec="wav";
        auto r=plan(project,dir->path(),wav,false,false,opt);if(!r.error.isEmpty()){status->setText(r.error);return;}
        status->setText(EchoesText("Авто-субтитры: готовим звук…"));progress->show();progress->setValue(0);auto mix=new QProcess(this);job=mix;
        connect(mix,qOverload<int,QProcess::ExitStatus>(&QProcess::finished),this,[this,mix,wav,language,dir](int code,QProcess::ExitStatus){mix->deleteLater();job=nullptr;
            if(code!=0||!QFileInfo::exists(wav)){progress->hide();status->setText(EchoesText("Авто-субтитры: не удалось подготовить звук."));return;}transcribe(wav,language,dir);});
        mix->start(ffmpeg,r.args);
    }
    void transcribe(QString wav,QString language,QSharedPointer<QTemporaryDir> dir){
        status->setText(EchoesText("Авто-субтитры: распознаём речь…"));auto proc=new QProcess(this);job=proc;auto buffer=QSharedPointer<QByteArray>::create();auto cues=QSharedPointer<QList<QJsonObject>>::create();auto error=QSharedPointer<QString>::create();
        auto env=QProcessEnvironment::systemEnvironment();env.insert("PYTHONIOENCODING","utf-8");env.insert("HF_HUB_OFFLINE","1");proc->setProcessEnvironment(env);
        connect(proc,&QProcess::readyReadStandardOutput,this,[this,proc,buffer,cues,error]{*buffer+=proc->readAllStandardOutput();int at;while((at=buffer->indexOf('\n'))>=0){auto o=QJsonDocument::fromJson(buffer->left(at)).object();buffer->remove(0,at+1);
            if(o.contains("text"))*cues<<o;else if(o.contains("progress")){int pc=qRound(o["progress"].toDouble()*100);progress->setValue(pc);status->setText(QString(EchoesText("Авто-субтитры: %1%")).arg(pc));}else if(o.contains("error"))*error=o["error"].toString();}});
        connect(proc,qOverload<int,QProcess::ExitStatus>(&QProcess::finished),this,[this,proc,cues,error,dir](int code,QProcess::ExitStatus){proc->deleteLater();job=nullptr;progress->hide();Q_UNUSED(dir);
            if(*error=="model"){QMessageBox::information(this,EchoesText("Авто-субтитры"),EchoesText("Модель распознавания ещё не скачана. Один раз включите в ECHOES синхронизацию текста песни — модель скачается, и субтитры будут работать офлайн."));status->setText(EchoesText("Авто-субтитры: нет модели распознавания."));return;}
            if(cues->isEmpty()){status->setText(!error->isEmpty()?EchoesText("Авто-субтитры: ")+*error:code==0?EchoesText("Речь в проекте не найдена."):EchoesText("Не удалось распознать речь."));return;}
            checkpoint();int row=project.insertTrack(0,"text");project.lanes[row].name=EchoesText("Субтитры");
            for(auto &o:*cues){Clip c;c.kind="text";c.text=o["text"].toString();c.name=c.text.left(45);c.start=qMax(0.,o["start"].toDouble());c.duration=qMax(.3,o["end"].toDouble()-c.start);for(auto &t:textCatalog())if(t.id=="subtitle")applyTextStyle(c,t);c.y=int(project.height*.38);c.track=row;project.clips<<c;}
            edited();inspect();status->setText(QString(EchoesText("Авто-субтитры: фраз — %1. Текст и стиль можно поправить в инспекторе.")).arg(cues->size()));});
        proc->start(python,{transcriber,wav,language});
    }
    void recordVoice(){
        if(recorder){recorder->write("q");recorder->closeWriteChannel();if(recordButton)recordButton->setText(EchoesText("Сохраняем запись…"));return;}
        QProcess list;list.start(ffmpeg,{"-hide_banner","-list_devices","true","-f","dshow","-i","dummy"});list.waitForFinished(8000);QStringList devices;
        auto it=QRegularExpression("\"([^\"]+)\" \\(audio\\)").globalMatch(QString::fromUtf8(list.readAllStandardError()));while(it.hasNext())devices<<it.next().captured(1);
        if(devices.isEmpty()){QMessageBox::warning(this,EchoesText("Запись голоса"),EchoesText("Микрофон не найден."));return;}
        QString device=devices.first();if(devices.size()>1){bool ok;device=QInputDialog::getItem(this,EchoesText("Запись голоса"),EchoesText("Микрофон:"),devices,0,false,&ok);if(!ok)return;}
        QString folder=QStandardPaths::writableLocation(QStandardPaths::AppLocalDataLocation)+"/montage-voice";QDir().mkpath(folder);recordPath=folder+EchoesText("/Голос ")+QDateTime::currentDateTime().toString("yyyy-MM-dd HH-mm-ss")+".wav";recordAt=timeline->playhead;
        recorder=new QProcess(this);connect(recorder,qOverload<int,QProcess::ExitStatus>(&QProcess::finished),this,[this](int,QProcess::ExitStatus){recorder->deleteLater();recorder=nullptr;if(recordButton)recordButton->setText(EchoesText("🎙  Записать голос"));
            if(QFileInfo(recordPath).size()<2000){status->setText(EchoesText("Запись не удалась: проверьте микрофон."));return;}checkpoint();int row=project.insertTrack(project.tracks,"audio");project.lanes[row].name=EchoesText("Голос");enqueue(recordPath,recordAt,row);});
        recorder->start(ffmpeg,{"-hide_banner","-y","-f","dshow","-i","audio="+device,"-ac","1","-ar","48000",recordPath});
        if(recordButton)recordButton->setText(EchoesText("■  Остановить запись"));status->setText(EchoesText("Идёт запись голоса с «")+device+EchoesText("». Нажмите «Остановить запись» (Ctrl+R), когда закончите."));
    }

    // ---------- Live UI walkthrough: real widgets, synthetic mouse and keyboard ----------
    void uiTest(QString dir){QTimer::singleShot(500,this,[this,dir]{int code=runUiTest(dir);dirty=false;QApplication::exit(code);});}
    int runUiTest(QString dir){
        QJsonObject result;QStringList failed;int shot=0;
        auto snap=[&](QString name){grab().save(QString("%1/%2_%3.png").arg(dir).arg(++shot,2,10,QChar('0')).arg(name));};
        auto expect=[&](bool ok,QString what){result[what]=ok;if(!ok)failed<<what;std::printf("%s %s\n",ok?"ok  ":"FAIL",what.toUtf8().constData());std::fflush(stdout);};
        auto waitFor=[&](std::function<bool()> f,int ms){QElapsedTimer t;t.start();while(!f()&&t.elapsed()<ms)QTest::qWait(50);return f();};
        auto count=[&](QString kind){int n=0;for(auto &c:project.clips)n+=c.kind==kind;return n;};
        auto at=[&](QString kind,double t)->Clip*{for(auto &c:project.clips)if(c.kind==kind&&t>=c.start&&t<c.start+c.duration)return &c;return nullptr;};
        auto clickClip=[&](Clip *c){if(c)QTest::mouseClick(timeline,Qt::LeftButton,{},timeline->rect(*c).center().toPoint());};
        // Qt delivers drops only inside a real OS drag session, so call the timeline's drop handler.
        auto drop=[&](QString file,double t,int row){timeline->dropped(file,t,row);};
        Clip fallback;auto cur=[&]()->Clip&{auto c=timeline->current();return c?*c:fallback;};
        auto rowOf=[&](QString label){return findChild<QWidget*>("slider:"+label);};
        auto groupBox=[&](QString title)->QGroupBox*{for(auto g:findChildren<QGroupBox*>())if(g->title()==title)return g;return nullptr;};
        auto clickItem=[&](QListWidget *list,int i){QTest::mouseClick(list->viewport(),Qt::LeftButton,{},list->visualItemRect(list->item(i)).center());};
        // Import by dropping files onto the timeline.
        drop(dir+"/source.mp4",0,0);waitFor([&]{return !probing&&project.clips.size()==2;},15000);expect(count("video")==1&&count("audio")==1,"drop_video_makes_linked_audio");
        int audioRow=-1;for(int i=0;i<project.tracks;i++)if(project.lanes[i].kind=="audio")audioRow=i;drop(dir+"/speech.wav",3.2,audioRow);waitFor([&]{return !probing&&project.clips.size()==3;},15000);expect(count("audio")==2,"drop_audio_onto_audio_track");
        fitTimeline();QTest::qWait(1500);snap("imported");
        // Select, move the cursor on the ruler, split with S.
        clickClip(at("video",.5));expect(timeline->current()&&timeline->current()->kind=="video","click_selects_clip");
        QTest::mouseClick(timeline,Qt::LeftButton,{},QPoint(int(Timeline::Header+1.0*timeline->zoom),20));expect(std::abs(timeline->playhead-1)<.06,"ruler_click_moves_cursor");
        QTest::keyClick(timeline,Qt::Key_S);expect(count("video")==2&&count("audio")==3,"key_S_splits_video_and_sound");
        // Effects library panels.
        clickClip(at("video",2));QTest::mouseClick(rails->button(3),Qt::LeftButton);clickItem(transList,1);expect(timeline->current()&&timeline->current()->trans=="dissolve","transition_panel_click");
        QTest::mouseClick(rails->button(4),Qt::LeftButton);clickItem(lookList,1);expect(timeline->current()&&timeline->current()->look=="cinema","filter_panel_click");
        QTest::mouseClick(rails->button(5),Qt::LeftButton);effectList->item(0)->setCheckState(Qt::Checked);expect(timeline->current()&&timeline->current()->effect.contains("mono"),"effect_checkbox");
        QTest::qWait(800);snap("panels");
        // Colour page: drag a slider handle.
        QTest::mouseClick(pages->button(2),Qt::LeftButton);QTest::qWait(200);
        if(auto row=rowOf(EchoesText("Контраст"))){auto s=row->findChild<QSlider*>();QPoint c=s->rect().center();QTest::mousePress(s,Qt::LeftButton,{},c);QTest::mouseMove(s,c+QPoint(20,0));QTest::mouseMove(s,c+QPoint(45,0));QTest::mouseRelease(s,Qt::LeftButton,{},c+QPoint(45,0));}
        expect(timeline->current()&&timeline->current()->grade.value("contrast").toDouble()>5,"color_slider_drag");
        {QPoint c(curves->width()/2,curves->height()/2);QTest::mousePress(curves,Qt::LeftButton,{},c);QTest::mouseMove(curves,c-QPoint(0,20));QTest::mouseMove(curves,c-QPoint(0,35));QTest::mouseRelease(curves,Qt::LeftButton,{},c-QPoint(0,35));}
        expect(timeline->current()&&timeline->current()->grade.value("curveM").toString().split(' ').size()==3,"curve_point_drag");
        QTest::qWait(1200);snap("color_page");
        // Viewer: drag the layer and scale with the wheel.
        QTest::mouseClick(pages->button(0),Qt::LeftButton);seekTo(2.2);QTest::qWait(300);double x0=timeline->current()?timeline->current()->x:0;
        if(frameView->hasBox){QPoint c=(frameView->area().topLeft()+frameView->boxCenter*frameView->ratio()).toPoint();QTest::mousePress(frameView,Qt::LeftButton,{},c);QTest::mouseMove(frameView,c+QPoint(40,0));QTest::mouseMove(frameView,c+QPoint(70,8));QTest::mouseRelease(frameView,Qt::LeftButton,{},c+QPoint(70,8));
            QWheelEvent wheel(QPointF(c+QPoint(70,8)),QPointF(frameView->mapToGlobal(c)),QPoint(),QPoint(0,240),Qt::NoButton,Qt::NoModifier,Qt::NoScrollPhase,false);QApplication::sendEvent(frameView,&wheel);}
        expect(timeline->current()&&timeline->current()->x>x0+40,"viewer_drag_moves_layer");expect(timeline->current()&&timeline->current()->scale>1.05,"viewer_wheel_scales");
        // Keyframes: K, move, change scale in the inspector.
        seekTo(cur().start+.1);QTest::keyClick(timeline,Qt::Key_K);expect(cur().keys.size()==1,"key_K_adds_keyframe");
        seekTo(cur().start+1.5);if(auto row=rowOf(EchoesText("Масштаб")))if(auto spin=row->findChild<QDoubleSpinBox*>())spin->setValue(160);expect(cur().keys.size()==2,"inspector_change_adds_second_key");
        // Clipboard, undo, marker.
        int before=project.clips.size();QTest::keyClick(timeline,Qt::Key_C,Qt::ControlModifier);seekTo(project.end());QTest::keyClick(timeline,Qt::Key_V,Qt::ControlModifier);expect(project.clips.size()>before,"ctrl_c_ctrl_v");
        QTest::keyClick(timeline,Qt::Key_Z,Qt::ControlModifier);expect(int(project.clips.size())==before,"ctrl_z_undo");
        QTest::keyClick(timeline,Qt::Key_M);expect(!project.markers.isEmpty(),"key_M_marker");
        // Text from the panel, then typing.
        clickClip(at("video",.5));QTest::mouseClick(rails->button(2),Qt::LeftButton);clickItem(textList,5);QTest::keyClicks(textEdit,"Hello ECHOES UI");waitFor([&]{auto c=timeline->current();return c&&c->kind=="text"&&c->text=="Hello ECHOES UI";},3000);
        expect(timeline->current()&&timeline->current()->text=="Hello ECHOES UI"&&timeline->current()->style.value("glow").toBool(),"text_tile_and_typing");
        // Simple/Pro switch changes what the inspector shows.
        clickClip(at("video",.5));QTest::mouseClick(modePro,Qt::LeftButton);bool shown=groupBox(EchoesText("КАДРИРОВАНИЕ"))&&groupBox(EchoesText("КАДРИРОВАНИЕ"))->isVisibleTo(this);QTest::mouseClick(modeSimple,Qt::LeftButton);bool hidden=groupBox(EchoesText("КАДРИРОВАНИЕ"))&&!groupBox(EchoesText("КАДРИРОВАНИЕ"))->isVisibleTo(this);expect(shown&&hidden,"simple_pro_switch");
        // Mask via its combo box.
        if(auto g=groupBox(EchoesText("МАСКА")))if(auto combo=g->findChild<QComboBox*>()){combo->setCurrentIndex(1);emit combo->activated(1);}expect(timeline->current()&&timeline->current()->mask.value("shape").toString()=="ellipse","mask_shape");
        QTest::qWait(1200);snap("mask");
        // Speed ramp, freeze frame.
        int videos=count("video");for(auto combo:findChildren<QComboBox*>())if(combo->count()&&combo->itemText(0)==EchoesText("Кривая скорости…")){combo->setCurrentIndex(1);emit combo->activated(1);}expect(count("video")==videos+2,"speed_ramp_preset");
        seekTo(.3);clickClip(at("video",.3));seekTo(.3);QTest::keyClick(timeline,Qt::Key_F);expect(waitFor([&]{return count("image")>=1;},15000),"key_F_freeze_frame");
        // Fast playback from the cursor.
        seekTo(.2);QElapsedTimer latency;latency.start();QTest::mouseClick(playButton,Qt::LeftButton);bool started=waitFor([&]{return chunkMode&&!startingVlc&&vlc.state()==3;},20000);qint64 ms=latency.elapsed();double t0=timeline->playhead;
        QJsonArray trace;for(int i=0;i<12;i++){QTest::qWait(250);trace.append(QString("%1:%2/%3/%4").arg(i*250).arg(timeline->playhead,0,'f',2).arg(vlc.time(),0,'f',2).arg(vlc.state()));}
        QJsonArray renders;for(auto v:chunkTimes)renders.append(v);result["playback_trace"]=trace;result["chunk_render_ms"]=renders;
        result["playback_start_ms"]=ms;expect(started&&timeline->playhead>t0+1,"fast_chunk_playback");snap("playing");QTest::mouseClick(playButton,Qt::LeftButton);expect(!wantPlaying&&!chunkMode,"pause_stops_chunks");
        // Stabilisation analysis in the background.
        clickClip(at("video",.2));mutate([](Clip&c){c.stabilize=40;});Clip *sv=timeline->current();QString trf=sv?stabilizeFile(*sv):QString();expect(!trf.isEmpty()&&waitFor([&]{return QFileInfo::exists(trf)&&stabilizing.isEmpty();},120000),"stabilization_analysis");
        // Auto subtitles from the speech clip.
        if(QFileInfo::exists(python)){autoSubtitles("ru");bool done=waitFor([&]{return !job&&std::any_of(project.lanes.begin(),project.lanes.end(),[](auto &l){return l.name==EchoesText("Субтитры");});},240000);int subs=0;for(auto &c:project.clips)subs+=c.kind=="text"&&project.lanes[c.track].name==EchoesText("Субтитры");result["subtitles"]=subs;expect(done&&subs>=2,"auto_subtitles");}
        QTest::qWait(1500);snap("subtitles");
        // Export page: GIF preset and the real Export button.
        QTest::mouseClick(pages->button(4),Qt::LeftButton);int gif=exportPreset->findData("gif");exportPreset->setCurrentIndex(gif);emit exportPreset->activated(gif);exportRange->setCurrentIndex(0);
        QString out=dir+"/ui-export.gif";QFile::remove(out);autoPath=out;QTest::mouseClick(exportButton,Qt::LeftButton);expect(waitFor([&]{return !render&&QFileInfo(out).size()>1000;},180000),"export_page_gif");
        snap("export");
        write(dir+"/ui-results.json",result);std::printf("%s\n",QJsonDocument(result).toJson(QJsonDocument::Compact).constData());return failed.isEmpty()?0:30;
    }


    // ---------- Tests ----------
    bool interactionTest(QString dir){
        Project saved=project;project=Project();if(project.tracks!=1)return false;
        Clip video;video.kind="video";video.source=20;video.duration=10;video.audio=false;video.group="linked-test";video.name="Video";project.clips<<video;
        Clip audio=video;audio.id=QUuid::createUuid().toString();audio.kind="audio";audio.audio=true;audio.track=project.ensureTrack("audio");project.clips<<audio;
        Clip other=video;other.id=QUuid::createUuid().toString();other.start=10;other.duration=5;other.group.clear();project.clips<<other;timeline->resizeCanvas();timeline->snap=false;
        auto event=[this](QEvent::Type type,QPointF at,Qt::MouseButton button,Qt::MouseButtons buttons){QMouseEvent e(type,at,QPointF(timeline->mapToGlobal(at.toPoint())),button,buttons,Qt::NoModifier);QApplication::sendEvent(timeline,&e);};
        QPointF from=timeline->rect(project.clips[0]).center(),to=from+QPointF(timeline->zoom,Timeline::Row*2+35);
        event(QEvent::MouseButtonPress,from,Qt::LeftButton,Qt::LeftButton);event(QEvent::MouseMove,to,Qt::NoButton,Qt::LeftButton);event(QEvent::MouseButtonRelease,to,Qt::LeftButton,Qt::NoButton);
        auto selected=timeline->current();if(!selected||project.tracks!=3||std::abs(selected->start-1)>.01||project.lanes[selected->track].kind!="video")return false;
        for(auto &c:project.clips)if(c.id==audio.id&&std::abs(c.start-1)>.01)return false;
        project.lanes[selected->track].locked=true;QPointF lockedFrom=timeline->rect(*selected).center();event(QEvent::MouseButtonPress,lockedFrom,Qt::LeftButton,Qt::LeftButton);event(QEvent::MouseMove,lockedFrom+QPointF(100,0),Qt::NoButton,Qt::LeftButton);event(QEvent::MouseButtonRelease,lockedFrom,Qt::LeftButton,Qt::NoButton);if(std::abs(timeline->current()->start-1)>.01)return false;project.lanes[timeline->current()->track].locked=false;
        timeline->playhead=5;split();if(project.clips.size()!=5)return false;undo();if(project.clips.size()!=3)return false;
        timeline->selectOnly(video.id);timeline->playhead=3;copy();paste(15);if(project.clips.size()!=5)return false;undo();if(project.clips.size()!=3)return false;
        timeline->selectOnly(video.id);rippleDelete();if(project.clips.size()!=1)return false;undo();if(project.clips.size()!=3)return false;
        timeline->selectOnly(video.id);toggleMarker(2);if(project.markers.size()!=1)return false;undo();
        timeline->selectOnly(video.id);unlink();for(auto &c:project.clips)if(c.id==audio.id&&!c.group.isEmpty())return false;
        write(dir+"/interaction-results.json",{{"single_initial_track",true},{"mouse_drag_creates_typed_track",true},{"linked_audio_moves",true},{"track_lock_blocks_drag",true},{"linked_split_and_undo",true},{"copy_paste",true},{"ripple_delete",true},{"markers",true},{"unlink",true}});
        project=saved;timeline->selectOnly({});timeline->playhead=0;timeline->snap=true;undoStack.clear();redoStack.clear();return true;
    }
    void view(bool proMode,int page){pro=proMode;applyMode();showPage(page);inspect();}
    bool smokeTest(QString dir){
        QDir().mkpath(dir);if(!interactionTest(dir))return false;
        QDir().mkpath(dir);Clip c;c.kind="text";c.text=EchoesText("ECHOES • твоя история");c.name=EchoesText("Заголовок");c.duration=6;c.track=project.ensureTrack("text");project.clips<<c;timeline->selectOnly(c.id);timeline->playhead=2;split();if(project.clips.size()!=2)return false;undo();if(project.clips.size()!=1||project.clips[0].duration!=6)return false;redo();if(project.clips.size()!=2)return false;duplicate();if(project.clips.size()!=3)return false;remove();if(project.clips.size()!=2)return false;
        timeline->selectOnly(project.clips[0].id);auto id=timeline->selected;timeline->testMoveToNewTrack(id,0);if(project.lanes[timeline->current()->track].kind!="text")return false;
        timeline->playhead=1;toggleKey();if(timeline->current()->keys.size()!=1)return false;mutate([this](Clip&x){setKeyed(x,"scale",1.5);});if(std::abs(timeline->current()->keys[0].toObject()["scale"].toDouble()-1.5)>.001)return false;
        Clip bg;bg.kind="color";bg.style["color"]="#2d6f86";bg.style["color2"]="#e0913f";bg.duration=6;bg.track=project.ensureTrack("video");project.clips<<bg;project.markers<<Marker{3,"","#e8822c"};project.inPoint=1;project.outPoint=4;
        if(!write(dir+"/smoke.echoedit",project.json()))return false;Project other;if(!other.load(project.json()))return false;
        if(other.json()!=project.json()){write(dir+"/roundtrip-a.json",project.json());write(dir+"/roundtrip-b.json",other.json());return false;}
        for(int i=0;i<6;i++){auto item=new QListWidgetItem(glyphIcon("♫",QColor(pal::wave)),QString(EchoesText("Моя коллекция / Трек %1")).arg(i+1),library);item->setToolTip("Test fixture");}
        timeline->selectOnly(project.clips[0].id);timeline->playhead=1.5;showPage(pro?2:0);inspect();edited();dirty=false;return true;
    }
    void integrationTest(QString dir){
        QFile::remove(dir+"/vlc-frame.png");
        enqueue(dir+"/source.mp4");auto poll=new QTimer(this);auto elapsed=QSharedPointer<QElapsedTimer>::create();elapsed->start();auto state=QSharedPointer<int>::create(0);auto oldCount=QSharedPointer<int>::create(0);
        connect(poll,&QTimer::timeout,this,[this,poll,elapsed,state,oldCount,dir]{
            if(elapsed->elapsed()>30000){grab().save(dir+"/failure.png");std::fprintf(stderr,"Integration timeout: state=%d frames=%d proxies=%d status=%s\n",*state,completedFrames,completedPreviews,status->text().toUtf8().constData());poll->stop();dirty=false;QApplication::exit(11);return;}
            if(*state==0&&!probing&&!project.clips.isEmpty()){
                if(project.clips.size()!=2||project.tracks!=2||project.clips[0].audio||project.clips[1].kind!="audio"||project.clips[0].group!=project.clips[1].group){QApplication::exit(12);return;}
                for(auto &c:project.clips)c.duration=2;inspect();edited();seekTo(.5);*state=1;
            }else if(*state==1&&proxyValid&&completedFrames>0&&media->imageCount()>0&&media->waveCount()>0){
                *oldCount=completedPreviews;auto c=timeline->current();c->effect="mono";edited();previewTimer.start(0);QTimer::singleShot(20,this,[this]{if(auto c=timeline->current()){c->effect="mono,vignette";edited();}});*state=2;
            }else if(*state==2&&proxyValid&&completedPreviews>*oldCount&&discardedPreviews>0){
                *oldCount=completedPreviews;auto c=timeline->current();QString id=c->id;checkpoint();timeline->testMoveToNewTrack(id,0);edited();inspect();*state=3;
            }else if(*state==3&&proxyValid&&completedPreviews>*oldCount){
                if(project.tracks!=2){QApplication::exit(13);return;}play();*state=4;
            }else if(*state==4&&!startingVlc&&vlc.time()>.7){
                if(!vlc.snapshot(dir+"/vlc-frame.png")){std::fputs("VLC produced no decoded video frame\n",stderr);QApplication::exit(15);return;}play();seekTo(.8);*state=5;
            }else if(*state==5&&std::abs(lastFrameTime-.8)<.02){
                grab().save(dir+"/playback.png");if(QFileInfo::exists(dir+"/covered.mp3")){enqueue(dir+"/covered.mp3");*state=6;}else *state=7;
            }else if(*state==6&&!probing){
                if(project.clips.size()!=3||project.clips.last().kind!="audio"){QApplication::exit(14);return;}*state=7;
            }else if(*state==7){
                if(QImage(dir+"/vlc-frame.png").isNull()){QApplication::exit(16);return;}
                QJsonObject result{{"automatic_previews",completedPreviews},{"stale_previews_discarded",discardedPreviews},{"cursor_frames",completedFrames},{"thumbnails",media->imageCount()},{"wave_chunks",media->waveCount()},{"dynamic_tracks",project.tracks},{"separate_linked_audio",true},{"native_playback",true},{"cover_art_stays_audio",project.clips.size()==3}};write(dir+"/automatic-results.json",result);std::puts(QJsonDocument(result).toJson(QJsonDocument::Compact).constData());poll->stop();dirty=false;close();
            }
        });poll->start(100);
    }
protected:
    void dragEnterEvent(QDragEnterEvent *e)override{if(e->mimeData()->hasUrls())e->acceptProposedAction();}
    void dropEvent(QDropEvent *e)override{for(auto u:e->mimeData()->urls())if(u.isLocalFile())enqueue(u.toLocalFile());e->acceptProposedAction();}
    void closeEvent(QCloseEvent *e)override{
        if(render&&!renderProxy){QMessageBox::information(this,EchoesText("Идёт экспорт"),EchoesText("Завершите или отмените экспорт перед выходом."));std::puts("ECHOES_CLOSE_CANCELLED");std::fflush(stdout);e->ignore();return;}
        if(render){closePending=true;previewTimer.stop();cancelledPreview=true;render->kill();e->ignore();return;}
        if(!confirmDiscard()){closePending=false;previewTimer.start(0);std::puts("ECHOES_CLOSE_CANCELLED");std::fflush(stdout);e->ignore();return;}
        closing=true;previewTimer.stop();frameTimer.stop();timer.stop();media->shutdown();for(auto p:probes){p->disconnect(this);p->kill();p->waitForFinished(1000);}probes.clear();vlc.stop();QFile::remove(autosavePath);e->accept();
    }
};
