// Native launcher surface. Python is only the download/install backend bridge.
#include <QtWidgets/QApplication>
#include <QtWidgets/QWidget>
#include <QtWidgets/QLabel>
#include <QtWidgets/QPushButton>
#include <QtWidgets/QComboBox>
#include <QtWidgets/QCheckBox>
#include <QtWidgets/QLineEdit>
#include <QtWidgets/QProgressBar>
#include <QtWidgets/QPlainTextEdit>
#include <QtWidgets/QStackedWidget>
#include <QtWidgets/QVBoxLayout>
#include <QtWidgets/QHBoxLayout>
#include <QtWidgets/QFileDialog>
#include <QtWidgets/QMessageBox>
#include <QtWidgets/QMenu>
#include <QtGui/QPainter>
#include <QtGui/QPainterPath>
#include <QtGui/QCloseEvent>
#include <QtGui/QDesktopServices>
#include <QtGui/QIcon>
#include <QtGui/QFontDatabase>
#include <QtCore/QJsonDocument>
#include <QtCore/QJsonObject>
#include <QtCore/QSettings>
#include <QtCore/QFile>
#include <QtCore/QFileInfo>
#include <QtCore/QDir>
#include <QtCore/QUrl>
#include <QtCore/QRegularExpression>
#include <QtCore/QStandardPaths>
#include <QtCore/QStorageInfo>
#include <QtCore/QTimer>
#include <cmath>
#include <vector>

#define API extern "C" __declspec(dllexport)
using Callback = void (*)(const char *);
static Callback callback = nullptr;

struct Text { const char *key; const char *en; const char *ru; };
static const Text texts[] = {
    {"brand.tagline", "My music.\nMy rules.", "Моя музыка.\nМои правила."},
    {"brand.caption", "YOUR NEXT CHAPTER", "ТВОЯ НОВАЯ ГЛАВА"},
    {"brand.detail", "Music, discoveries and a world of your own.", "Музыка, открытия и мир, который принадлежит тебе."},
    {"header.launcher", "Launcher", "Лаунчер"},
    {"language.label", "Interface language", "Язык интерфейса"},
    {"language.manage", "Translation files", "Файлы перевода"},
    {"language.import", "Load a translation…", "Загрузить перевод…"},
    {"language.export", "Save English template…", "Сохранить английский шаблон…"},
    {"language.invalid", "This translation file is invalid. Use the English template and keep its placeholders unchanged.", "Неверный файл перевода. Используйте английский шаблон и сохраните его подстановки без изменений."},
    {"language.loaded", "Translation loaded. Missing entries use English.", "Перевод загружен. Пропущенные строки отображаются на английском."},
    {"language.save_error", "The translation file could not be saved.", "Не удалось сохранить файл перевода."},
    {"home.ready", "Your music is waiting.", "Музыка уже ждёт."},
    {"home.subtitle", "A new session, a familiar feeling. Pick up where you left off.", "Новый сеанс, знакомое чувство. Продолжай с того места, где остановился."},
    {"home.installed", "Installed · {version}", "Установлена · {version}"},
    {"home.new", "Available · {version}", "Доступна · {version}"},
    {"home.latest", "You're up to date.", "Установлена последняя версия."},
    {"home.checking", "Checking GitHub for updates…", "Проверяем обновления на GitHub…"},
    {"home.offline", "Could not check GitHub. Your installed player is ready to use.", "Не удалось проверить GitHub. Установленным плеером можно пользоваться."},
    {"home.launching", "Opening ECHOES…", "Открываем ECHOES…"},
    {"button.launch", "Open ECHOES", "Запустить ECHOES"},
    {"button.update", "Update to {version}", "Обновить до {version}"},
    {"button.app_folder", "App folder", "Папка программы"},
    {"button.data_folder", "My data", "Мои данные"},
    {"button.reinstall", "Reinstall…", "Переустановить…"},
    {"button.uninstall", "Uninstall", "Удалить"},
    {"button.back", "Back", "Назад"},
    {"button.change", "Change…", "Изменить…"},
    {"button.install", "Download & install", "Скачать и установить"},
    {"button.details", "Installation details", "Подробности установки"},
    {"button.home", "Back to launcher", "К лаунчеру"},
    {"setup.title", "Make room for your music.", "Место для твоей музыки."},
    {"setup.subtitle", "Install ECHOES with its own Python, audio engine and tools. No administrator rights needed.", "Установим ECHOES вместе с отдельным Python, звуковым движком и инструментами. Права администратора не нужны."},
    {"setup.location", "INSTALL LOCATION", "ПАПКА УСТАНОВКИ"},
    {"setup.choose", "Choose an installation folder", "Выберите папку установки"},
    {"setup.space", "{space} GB free · around 1 GB needed", "Свободно {space} ГБ · потребуется около 1 ГБ"},
    {"setup.space_unknown", "Around 1 GB of free space needed", "Потребуется около 1 ГБ свободного места"},
    {"setup.options", "MAKE IT YOURS", "НАСТРОИТЬ ПОД СЕБЯ"},
    {"option.desktop", "Desktop shortcut", "Ярлык на рабочем столе"},
    {"option.menu", "Start menu shortcuts", "Ярлыки в меню «Пуск»"},
    {"option.spotify", "Spotify download helper (spotdl)", "Загрузчик из Spotify (spotdl)"},
    {"option.autostart", "Open ECHOES when Windows starts", "Запускать ECHOES вместе с Windows"},
    {"option.launch", "Open ECHOES after installation", "Открыть ECHOES после установки"},
    {"progress.install", "Setting the stage.", "Готовим всё к прослушиванию."},
    {"progress.update", "Something new is on the way.", "Новое уже близко."},
    {"progress.note", "Your library and settings stay with you.", "Медиатека и настройки остаются с тобой."},
    {"progress.download", "Downloading the latest ECHOES from GitHub…", "Скачиваем последнюю версию ECHOES с GitHub…"},
    {"progress.unpack", "Preparing the installation files…", "Подготавливаем файлы установки…"},
    {"progress.installing", "Installing ECHOES and its components…", "Устанавливаем ECHOES и его компоненты…"},
    {"progress.updating", "Updating ECHOES and its components…", "Обновляем ECHOES и его компоненты…"},
    {"done.install", "You're all set.", "Всё готово."},
    {"done.update", "Fresh sound. Same home.", "Новое звучание. Всё на месте."},
    {"done.subtitle", "ECHOES is ready. Your next favourite is out there.", "ECHOES готов. Твоя следующая любимая песня уже ждёт."},
    {"error.title", "Something went wrong", "Что-то пошло не так"},
    {"error.backend", "The operation could not be completed. Check your connection and the installation details.", "Не удалось завершить действие. Проверьте интернет и подробности установки."},
    {"error.running", "ECHOES is already open. Look for its window or the tray icon.", "ECHOES уже открыт. Найдите его окно или значок в трее."},
    {"error.missing", "ECHOES was not found in this folder.", "ECHOES не найден в этой папке."},
    {"error.busy", "Please let the installation finish before closing the launcher.", "Дождитесь завершения установки перед закрытием лаунчера."},
    {"uninstall.question", "Open the ECHOES uninstaller? You can choose to keep your music and settings there.", "Открыть удаление ECHOES? Там можно оставить музыку и настройки."},
    {"dialog.yes", "Continue", "Продолжить"},
    {"dialog.no", "Cancel", "Отмена"},
};

static QJsonObject english() {
    QJsonObject o;
    for (const auto &t : texts) o[t.key] = QString::fromUtf8(t.en);
    return o;
}
static bool isNewer(const QString &remote,const QString &installed) {
    auto parts=[](const QString &value){std::vector<int> result;auto m=QRegularExpression("\\d+").globalMatch(value);while(m.hasNext())result.push_back(m.next().captured().toInt());return result;};
    return !remote.isEmpty()&&parts(remote)>parts(installed);
}
static QStringList tokens(const QString &s) {
    QStringList out;
    auto matches = QRegularExpression("\\{[a-z]+\\}").globalMatch(s);
    while (matches.hasNext()) out << matches.next().captured();
    out.sort(); return out;
}
static bool parsePack(const QByteArray &bytes, QJsonObject &out, QString &name) {
    if (bytes.isEmpty() || bytes.size() > 256 * 1024) return false;
    QJsonParseError error;
    const auto doc = QJsonDocument::fromJson(bytes, &error);
    if (error.error != QJsonParseError::NoError || !doc.isObject()) return false;
    auto obj = doc.object();
    if (obj["format"].toInt() != 1 || !obj["name"].isString() || !obj["translations"].isObject()) return false;
    name = obj["name"].toString().trimmed();
    if (name.isEmpty() || name.size() > 60) return false;
    auto input = obj["translations"].toObject();
    auto base = english();
    for (auto it = input.begin(); it != input.end(); ++it) {
        if (!base.contains(it.key())) continue;
        if (!it->isString()) return false;
        const QString value = it->toString();
        if (value.trimmed().isEmpty() || value.size() > 4000 || tokens(value) != tokens(base[it.key()].toString())) return false;
        out[it.key()] = value;
    }
    return !out.isEmpty();
}

class Launcher;
class Brand final : public QWidget {
public:
    QString caption, tagline, detail;
    QColor accent;
    explicit Brand(QColor color, QWidget *parent = nullptr): QWidget(parent), accent(color) {
        setMinimumWidth(310); setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Expanding);
    }
protected:
    void paintEvent(QPaintEvent *) override {
        QPainter p(this); p.setRenderHint(QPainter::Antialiasing);
        QPainterPath clip; clip.addRoundedRect(QRectF(rect()), 24, 24);p.setClipPath(clip);
        p.fillRect(rect(), QColor("#0c1018"));
        for (int i=0; i<3; ++i) {
            QColor c=i==0 ? accent : i==1 ? QColor("#7959f7") : QColor("#34c5b4");
            QRadialGradient g(QPointF(width() * (i==0?.05:i==1?.95:.5), height() * (i==0?.2:i==1?.65:.9)), height()*.75);
            c.setAlpha(115);g.setColorAt(0,c);c.setAlpha(0);g.setColorAt(1,c);p.fillRect(rect(),g);
        }
        p.setPen(QPen(QColor(255,255,255,24), 1));
        for (int i=0;i<5;++i) p.drawEllipse(QPointF(width()*.75, height()*.31), 68+i*28, 68+i*28);
        p.setPen(Qt::NoPen);p.setBrush(QColor(255,255,255,235));
        QPainterPath star;
        const QPointF center(44,49);
        for(int i=0;i<16;++i) {
            const double a=i*3.141592653589793/8., r=i%2 ? 7.5:18.;
            QPointF v=center+QPointF(std::cos(a)*r,std::sin(a)*r);
            i?star.lineTo(v):star.moveTo(v);
        }
        star.closeSubpath();p.drawPath(star);
        QFont font("Segoe UI");font.setPixelSize(20);font.setWeight(QFont::Black);font.setLetterSpacing(QFont::AbsoluteSpacing,4);
        p.setFont(font);p.setPen(Qt::white);p.drawText(QRectF(76,29,width()-96,40),Qt::AlignVCenter,"ECHOES");
        font.setPixelSize(11);font.setWeight(QFont::DemiBold);font.setLetterSpacing(QFont::AbsoluteSpacing,2);p.setFont(font);
        p.setPen(QColor(255,255,255,140));p.drawText(QRectF(28,height()-265,width()-56,28),Qt::AlignVCenter,caption);
        font.setPixelSize(36);font.setWeight(QFont::Bold);font.setLetterSpacing(QFont::AbsoluteSpacing,0);p.setFont(font);p.setPen(Qt::white);
        p.drawText(QRectF(28,height()-220,width()-56,128),Qt::AlignTop|Qt::TextWordWrap,tagline);
        font.setPixelSize(13);font.setWeight(QFont::Normal);p.setFont(font);p.setPen(QColor(255,255,255,165));
        p.drawText(QRectF(28,height()-77,width()-56,56),Qt::AlignTop|Qt::TextWordWrap,detail);
    }
};

class Launcher final : public QWidget {
public:
    explicit Launcher(const QJsonObject &state): QWidget(nullptr),
        store(state.contains("settings_path") ? state["settings_path"].toString() : QString("HKEY_CURRENT_USER\\Software\\ECHOES\\Launcher"),
              state.contains("settings_path") ? QSettings::IniFormat : QSettings::NativeFormat) {
        const QString fontRoot = qEnvironmentVariable("WINDIR", "C:/Windows") + "/Fonts/";
        for(const auto &file : {"segoeui.ttf", "segoeuib.ttf", "seguisym.ttf"})QFontDatabase::addApplicationFont(fontRoot+file);
        QApplication::setFont(QFont("Segoe UI",10));
        setObjectName("LauncherRoot");setWindowTitle("ECHOES Launcher");resize(1060,690);setMinimumSize(980,650);
        target=state["target"].toString();version=state["version"].toString();dataPath=state["data"].toString();
        if(target.isEmpty())target=store.value("installation",state["default"].toString()).toString();
        accent=QColor(state["accent"].toString("#ffdb1a"));
        if(!accent.isValid())accent=QColor("#ffdb1a");
        setWindowIcon(QIcon(state["icon"].toString()));
        language=store.value("language","en").toString();
        QJsonObject loaded;QString packName;
        if(parsePack(store.value("translation_pack").toByteArray(),loaded,packName)){custom=loaded;customName=packName;}
        if(language!="ru"&&language!="custom")language="en";
        if(language=="custom"&&custom.isEmpty())language="en";
        auto root=new QHBoxLayout(this);root->setContentsMargins(16,16,16,16);root->setSpacing(14);
        brand=new Brand(accent,this);root->addWidget(brand,4);
        auto right=new QWidget(this);auto column=new QVBoxLayout(right);column->setContentsMargins(10,4,8,8);column->setSpacing(20);root->addWidget(right,6);
        auto header=new QHBoxLayout;launcherLabel=new QLabel;launcherLabel->setObjectName("Eyebrow");header->addWidget(launcherLabel);header->addStretch();
        languages=new QComboBox;languages->setObjectName("Language");languages->setMinimumWidth(128);languages->addItem("English","en");languages->addItem(QString::fromUtf8("Русский"),"ru");
        if(!custom.isEmpty())languages->addItem(customName,"custom");
        languages->setCurrentIndex(languages->findData(language));header->addWidget(languages);
        languageFiles=new QPushButton("+");languageFiles->setObjectName("IconButton");languageFiles->setFixedSize(36,36);header->addWidget(languageFiles);column->addLayout(header);
        pages=new QStackedWidget;column->addWidget(pages,1);
        buildHome();buildSetup();buildProgress();buildDone();
        for(auto l:findChildren<QLabel*>())l->setTextFormat(Qt::PlainText);
        connect(languages,&QComboBox::currentIndexChanged,this,[this](int){language=languages->currentData().toString();store.setValue("language",language);applyText();});
        connect(languageFiles,&QPushButton::clicked,this,[this]{QMenu menu;auto load=menu.addAction(tr("language.import"));auto save=menu.addAction(tr("language.export"));auto action=menu.exec(languageFiles->mapToGlobal(QPoint(0,languageFiles->height())));if(action==load)importPack();else if(action==save)exportPack();});
        setStyleSheet(QString(R"(
            QWidget#LauncherRoot { background:#090b10; color:#eef1f7; }
            QLabel { color:#e9edf5; background:transparent; }
            QLabel#Title { font-size:29px; font-weight:750; }
            QLabel#Sub { color:#929eaf; font-size:13px; }
            QLabel#Eyebrow { color:#8391a6; font-size:11px; font-weight:700; }
            QLabel#Badge { border:1px solid #303a4a; border-radius:12px; padding:5px 10px; font-size:11px; color:#c7d2df; }
            QPushButton { background:#161c26; color:#edf2fa; border:1px solid #2a3443; border-radius:13px; padding:10px 16px; font-weight:600; }
            QPushButton:hover { background:#212c3a; border-color:#46566d; }
            QPushButton:disabled { background:#11151c; color:#596577; border-color:#202632; }
            QPushButton#Primary { background:%1; color:#090b10; border:none; font-size:15px; font-weight:750; }
            QPushButton#Primary:hover { background:%2; }
            QPushButton#Ghost { background:transparent; padding:7px 9px; font-size:11px; }
            QPushButton#IconButton { padding:0; border-radius:10px; font-size:22px; }
            QComboBox { color:#dce6f3; background:#141b25; border:1px solid #2a3443; border-radius:10px; padding:8px 12px; }
            QComboBox QAbstractItemView { background:#151c26; color:#eef2f8; selection-background-color:#334056; }
            QLineEdit { color:#ccd7e6; background:#101720; border:1px solid #2a3546; border-radius:10px; padding:10px; }
            QCheckBox { color:#c8d1df; spacing:10px; font-size:13px; }
            QCheckBox::indicator { width:16px; height:16px; border:1px solid #48566b; border-radius:5px; background:#121924; }
            QCheckBox::indicator:checked { background:%1; border-color:%1; }
            QProgressBar { border:none; border-radius:4px; background:#192331; height:8px; }
            QProgressBar::chunk { background:%1; border-radius:4px; }
            QPlainTextEdit { color:#c1cddd; background:#0b1119; border:1px solid #273345; border-radius:10px; font-size:11px; }
            QMenu { background:#151c26; color:#eef2f8; border:1px solid #354156; padding:6px; }
            QMenu::item { padding:8px 14px; } QMenu::item:selected { background:#334056; }
        )").arg(accent.name(),accent.lighter(115).name()));
        applyText();pages->setCurrentWidget(version.isEmpty()?setup:home);
    }

    QString tr(const QString &key) const {
        if(language=="custom"&&custom.contains(key))return custom[key].toString();
        for(const auto &t:texts)if(key==QLatin1String(t.key))return QString::fromUtf8(language=="ru"?t.ru:t.en);
        return key;
    }
    QString formatted(const QString &key) const {return tr(key).replace("{version}",version).replace("{path}",target);}
    QLabel *label(const char *key, const char *name="Sub") {auto l=new QLabel;l->setObjectName(name);l->setWordWrap(true);labels.append({l,key});return l;}
    QPushButton *button(const char *key,const char *name="",QWidget *parent=nullptr){auto b=new QPushButton(parent);b->setObjectName(name);buttons.append({b,key});return b;}
    QVBoxLayout *page(QWidget *&w){w=new QWidget;pages->addWidget(w);auto v=new QVBoxLayout(w);v->setContentsMargins(2,4,2,0);v->setSpacing(15);return v;}
    void buildHome(){
        auto v=page(home);v->addSpacing(12);v->addWidget(label("home.ready","Title"));v->addWidget(label("home.subtitle"));
        auto row=new QHBoxLayout;installed=new QLabel;installed->setObjectName("Badge");newVersion=new QLabel;newVersion->setObjectName("Badge");row->addWidget(installed);row->addWidget(newVersion);row->addStretch();v->addLayout(row);
        location=new QLabel(target);location->setWordWrap(true);location->setObjectName("Sub");location->setTextInteractionFlags(Qt::TextSelectableByMouse);v->addWidget(location);v->addStretch();
        auto play=button("button.launch","Primary");play->setMinimumHeight(58);v->addWidget(play);connect(play,&QPushButton::clicked,this,[this]{send("launch");});
        updateButton=new QPushButton;updateButton->setMinimumHeight(46);v->addWidget(updateButton);connect(updateButton,&QPushButton::clicked,this,[this]{start("update");});
        auto folders=new QHBoxLayout;auto app=button("button.app_folder","Ghost"),data=button("button.data_folder","Ghost");folders->addWidget(app);folders->addWidget(data);v->addLayout(folders);
        connect(app,&QPushButton::clicked,this,[this]{QDesktopServices::openUrl(QUrl::fromLocalFile(target));});connect(data,&QPushButton::clicked,this,[this]{QDesktopServices::openUrl(QUrl::fromLocalFile(dataPath));});
        auto manage=new QHBoxLayout;auto reinstall=button("button.reinstall","Ghost"),uninstall=button("button.uninstall","Ghost");manage->addWidget(reinstall);manage->addWidget(uninstall);v->addLayout(manage);
        connect(reinstall,&QPushButton::clicked,this,[this]{pages->setCurrentWidget(setup);});
        connect(uninstall,&QPushButton::clicked,this,[this]{QMessageBox box(QMessageBox::Question,"ECHOES",tr("uninstall.question"),QMessageBox::NoButton,this);auto yes=box.addButton(tr("dialog.yes"),QMessageBox::AcceptRole);box.addButton(tr("dialog.no"),QMessageBox::RejectRole);box.exec();if(box.clickedButton()==yes)send("uninstall");});
        status=new QLabel;status->setWordWrap(true);status->setObjectName("Sub");v->addWidget(status);
    }
    void buildSetup(){
        auto v=page(setup);v->setSpacing(11);v->addWidget(label("setup.title","Title"));v->addWidget(label("setup.subtitle"));v->addSpacing(4);v->addWidget(label("setup.location","Eyebrow"));
        auto row=new QHBoxLayout;directory=new QLineEdit(target);directory->setReadOnly(true);row->addWidget(directory,1);auto choose=button("button.change");row->addWidget(choose);v->addLayout(row);
        connect(choose,&QPushButton::clicked,this,[this]{auto path=QFileDialog::getExistingDirectory(this,tr("setup.choose"),directory->text());if(!path.isEmpty()){directory->setText(QDir(path).filePath("ECHOES"));applyText();}});
        space=new QLabel;space->setObjectName("Sub");v->addWidget(space);v->addWidget(label("setup.options","Eyebrow"));
        const char *keys[]={"desktop","menu","spotify","autostart","launch"};
        for(auto key:keys){auto check=new QCheckBox;const QString id=key;check->setChecked(store.value("opt_"+id,id!="autostart").toBool());options[id]=check;v->addWidget(check);}
        v->addStretch();auto bottom=new QHBoxLayout;back=button("button.back","Ghost");bottom->addWidget(back);auto install=button("button.install","Primary");install->setMinimumHeight(52);bottom->addWidget(install,1);v->addLayout(bottom);
        connect(back,&QPushButton::clicked,this,[this]{pages->setCurrentWidget(home);});connect(install,&QPushButton::clicked,this,[this]{start("install");});
    }
    void buildProgress(){
        auto v=page(progressPage);v->addSpacing(24);progressTitle=new QLabel;progressTitle->setObjectName("Title");progressTitle->setWordWrap(true);v->addWidget(progressTitle);v->addWidget(label("progress.note"));v->addStretch();
        progressLabel=new QLabel;progressLabel->setWordWrap(true);v->addWidget(progressLabel);bar=new QProgressBar;bar->setTextVisible(false);v->addWidget(bar);
        details=button("button.details","Ghost");v->addWidget(details);log=new QPlainTextEdit;log->setReadOnly(true);log->setMaximumBlockCount(1500);log->setMinimumHeight(180);log->hide();v->addWidget(log);v->addStretch();
        connect(details,&QPushButton::clicked,this,[this]{log->setVisible(!log->isVisible());});
        retry=button("button.back");retry->hide();v->addWidget(retry);connect(retry,&QPushButton::clicked,this,[this]{pages->setCurrentWidget(version.isEmpty()?setup:home);});
    }
    void buildDone(){
        auto v=page(donePage);v->addStretch();auto icon=new QLabel("✓");icon->setAlignment(Qt::AlignCenter);icon->setStyleSheet("font-size:58px;color:"+accent.name());v->addWidget(icon);
        doneTitle=new QLabel;doneTitle->setObjectName("Title");doneTitle->setWordWrap(true);doneTitle->setAlignment(Qt::AlignCenter);v->addWidget(doneTitle);auto sub=label("done.subtitle");sub->setAlignment(Qt::AlignCenter);v->addWidget(sub);v->addStretch();
        auto row=new QHBoxLayout;auto homeButton=button("button.home");row->addWidget(homeButton);auto play=button("button.launch","Primary");play->setMinimumHeight(52);row->addWidget(play,1);v->addLayout(row);
        connect(homeButton,&QPushButton::clicked,this,[this]{send("refresh");pages->setCurrentWidget(home);});connect(play,&QPushButton::clicked,this,[this]{send("launch");});
    }
    void applyText(){
        for(const auto &pair:labels)pair.first->setText(tr(pair.second));
        for(const auto &pair:buttons)pair.first->setText(tr(pair.second));
        for(auto it=options.begin();it!=options.end();++it)it.value()->setText(tr("option."+it.key()));
        brand->caption=tr("brand.caption");brand->tagline=tr("brand.tagline");brand->detail=tr("brand.detail");brand->update();
        launcherLabel->setText(tr("header.launcher"));languages->setAccessibleName(tr("language.label"));languageFiles->setToolTip(tr("language.manage"));languageFiles->setAccessibleName(tr("language.manage"));
        installed->setText(formatted("home.installed"));newVersion->setText(tr("home.new").replace("{version}",remote));newVersion->setVisible(newer);
        updateButton->setText(tr("button.update").replace("{version}",remote));updateButton->setVisible(newer);location->setText(target);back->setVisible(!version.isEmpty());
        status->setText(tr(statusKey));progressTitle->setText(tr(mode=="update"?"progress.update":"progress.install"));progressLabel->setText(tr(stage));doneTitle->setText(tr(mode=="update"?"done.update":"done.install"));
        QString disk=directory->text();
        while(!QFileInfo::exists(disk)){
            const auto parent=QFileInfo(disk).dir().absolutePath();
            if(parent==disk)break;
            disk=parent;
        }
        QStorageInfo storage(disk);
        space->setText(storage.isValid() ? tr("setup.space").replace("{space}",QString::number(qMax<qint64>(0,storage.bytesAvailable())/double(1LL<<30),'f',1)) : tr("setup.space_unknown"));
    }
    void start(const QString &op){
        if(busy)return;mode=op;busy=true;stage="progress.download";bar->setRange(0,100);bar->setValue(0);log->clear();log->hide();retry->hide();pages->setCurrentWidget(progressPage);applyText();
        if(op=="install")target=directory->text();
        QJsonObject o;for(auto it=options.begin();it!=options.end();++it){o[it.key()]=it.value()->isChecked();store.setValue("opt_"+it.key(),it.value()->isChecked());}
        send(op,o);
    }
    void send(const QString &action,QJsonObject extra={}){
        extra["action"]=action;extra["target"]=target;extra["language"]=language=="ru"?"ru":"en";
        if(callback){auto data=QJsonDocument(extra).toJson(QJsonDocument::Compact);callback(data.constData());}
    }
    void setState(const QJsonObject &s){
        if(s.contains("target")){target=s["target"].toString();store.setValue("installation",target);directory->setText(target);}
        if(s.contains("version"))version=s["version"].toString();
        if(s.contains("remote")){remote=s["remote"].toString();statusKey=remote.isEmpty()?"home.offline":"home.latest";}
        newer=isNewer(remote,version);
        if(s.contains("status"))statusKey=s["status"].toString();
        applyText();
        if(s.contains("status")&&statusKey.startsWith("error."))QMessageBox::warning(this,tr("error.title"),tr(statusKey));
    }
    void progress(int pct,const QJsonObject &event){
        if(pct>=0){bar->setRange(0,100);bar->setValue(qBound(0,pct,100));}else bar->setRange(0,0);
        if(event.contains("stage"))stage=event["stage"].toString();
        if(event.contains("line"))log->appendPlainText(event["line"].toString());
        progressLabel->setText(tr(stage));
    }
    void done(bool success,const QString &error){
        busy=false;bar->setRange(0,100);bar->setValue(success?100:0);
        if(success){pages->setCurrentWidget(donePage);applyText();}else{stage=error.isEmpty()?"error.backend":error;progressLabel->setText(tr(stage));log->show();retry->show();}
    }
    bool loadPack(const QByteArray &bytes,bool persist){
        QJsonObject parsed;QString name;if(!parsePack(bytes,parsed,name))return false;
        custom=parsed;customName=name;language="custom";
        if(persist){store.setValue("translation_pack",bytes);store.setValue("language",language);}
        int index=languages->findData("custom");languages->blockSignals(true);
        if(index<0){languages->addItem(name,"custom");index=languages->count()-1;}else languages->setItemText(index,name);
        languages->setCurrentIndex(index);languages->blockSignals(false);applyText();return true;
    }
    void importPack(){
        auto path=QFileDialog::getOpenFileName(this,tr("language.import"),QString(),"JSON (*.json)");if(path.isEmpty())return;
        QFile f(path);if(!f.open(QIODevice::ReadOnly)||!loadPack(f.read(256*1024+1),true)){QMessageBox::warning(this,"ECHOES",tr("language.invalid"));return;}
        QMessageBox::information(this,"ECHOES",tr("language.loaded"));
    }
    void exportPack(){
        auto path=QFileDialog::getSaveFileName(this,tr("language.export"),"echoes-launcher.en.json","JSON (*.json)");if(path.isEmpty())return;
        QFile f(path);QJsonObject obj{{"format",1},{"name","My language"},{"translations",english()}};
        if(!f.open(QIODevice::WriteOnly)||f.write(QJsonDocument(obj).toJson())<0)QMessageBox::warning(this,"ECHOES",tr("language.save_error"));
    }
protected:
    void closeEvent(QCloseEvent *e) override {if(busy){QMessageBox::information(this,"ECHOES",tr("error.busy"));e->ignore();}else QWidget::closeEvent(e);}
private:
    QSettings store;
    QString language,customName,target,version,remote,dataPath,mode="install",stage="progress.download",statusKey="home.checking";
    QJsonObject custom;QColor accent;bool busy=false,newer=false;
    Brand *brand;QStackedWidget *pages;QWidget *home,*setup,*progressPage,*donePage;
    QComboBox *languages;QPushButton *languageFiles,*updateButton,*back,*details,*retry;
    QLabel *launcherLabel,*installed,*newVersion,*location,*status,*space,*progressTitle,*progressLabel,*doneTitle;
    QLineEdit *directory;QProgressBar *bar;QPlainTextEdit *log;
    QMap<QString,QCheckBox*> options;
    QVector<QPair<QLabel*,QString>> labels;QVector<QPair<QPushButton*,QString>> buttons;
};

API const char *ela_qt_version(){return QT_VERSION_STR;}
API void ela_callback(Callback cb){callback=cb;}
API void *ela_new(const char *json){return new Launcher(QJsonDocument::fromJson(json).object());}
API void ela_state(void *p,const char *json){static_cast<Launcher*>(p)->setState(QJsonDocument::fromJson(json).object());}
API void ela_progress(void *p,int pct,const char *json){static_cast<Launcher*>(p)->progress(pct,QJsonDocument::fromJson(json).object());}
API void ela_done(void *p,int ok,const char *error){static_cast<Launcher*>(p)->done(ok!=0,QString::fromUtf8(error));}
API int ela_validate_pack(const char *json){QJsonObject result;QString name;return parsePack(QByteArray(json),result,name)?1:0;}
API int ela_load_pack(void *p,const char *json,int persist){return static_cast<Launcher*>(p)->loadPack(json,persist!=0)?1:0;}
API const char *ela_english_template(){static QByteArray bytes=QJsonDocument(QJsonObject{{"format",1},{"name","My language"},{"translations",english()}}).toJson();return bytes.constData();}
