// Native spoken-word discovery. Only the final playback request crosses into the host.
#include "spoken_catalog.h"
#include "spoken_widgets.h"
#include <QtWidgets>
#include <QtNetwork>
#include <functional>

#define EUI_API extern "C" __declspec(dllexport)
using PlaySpoken = void (*)(const char *, int);

namespace {
QString text(bool ru, const char *en, const char *rus) {
    return QString::fromUtf8(ru ? rus : en);
}
QString plain(QString value) {
    QTextDocument document;
    document.setHtml(value);
    return document.toPlainText().simplified();
}
QString duration(int seconds) {
    if (seconds <= 0) return {};
    return seconds >= 3600 ? QString("%1:%2:%3").arg(seconds/3600).arg(seconds/60%60,2,10,QChar('0')).arg(seconds%60,2,10,QChar('0'))
                           : QString("%1:%2").arg(seconds/60).arg(seconds%60,2,10,QChar('0'));
}

class CoverCard final : public NativeSpokenButton {
public:
    QJsonObject data;
    QPixmap cover;
    bool book;
    CoverCard(QJsonObject item, bool isBook, QWidget *parent) : NativeSpokenButton(parent), data(item), book(isBook) {
        setFixedSize(174, 246);
        setCursor(Qt::PointingHandCursor);
        setFocusPolicy(Qt::StrongFocus);
        setToolTip(data["title"].toString() + "\n" + data["author"].toString());
        setAccessibleName(data["title"].toString());
        setStyleSheet("QPushButton{background:transparent;border:none;padding:0;}");
    }
    void paintEvent(QPaintEvent *) override {
        QPainter p(this);
        p.setRenderHint(QPainter::Antialiasing);
        p.setRenderHint(QPainter::SmoothPixmapTransform);
        QRectF art(1, 1, 172, 174);
        const auto hash=qHash(data["title"].toString());
        QColor a=book ? QColor::fromHsv(22+hash%28,140,185) : QColor::fromHsv(218+hash%70,155,190);
        QColor b=book ? QColor(47,27,36) : QColor(24,25,63);
        QLinearGradient gradient(art.topLeft(),art.bottomRight());
        gradient.setColorAt(0,a);gradient.setColorAt(1,b);
        QPainterPath shape;shape.addRoundedRect(art,12,12);
        p.save();p.setClipPath(shape);p.fillRect(art,gradient);
        if (!cover.isNull()) {
            auto scaled=cover.scaled(art.size().toSize()*devicePixelRatioF(),Qt::KeepAspectRatioByExpanding,Qt::SmoothTransformation);
            scaled.setDevicePixelRatio(devicePixelRatioF());
            QSizeF size=scaled.deviceIndependentSize();
            p.drawPixmap(QPointF(art.center().x()-size.width()/2,art.center().y()-size.height()/2),scaled);
        } else if (book) {
            p.setPen(QPen(QColor(255,235,203,110),1));
            p.drawRoundedRect(QRectF(27,20,116,136),3,3);p.drawLine(37,20,37,156);
            p.setPen(QColor(255,245,226));QFont f=font();f.setPixelSize(19);f.setBold(true);p.setFont(f);
            p.drawText(QRectF(48,42,82,94),Qt::TextWordWrap|Qt::AlignCenter,data["title"].toString().left(65));
        } else {
            p.setPen(QPen(QColor(215,203,255,170),2));
            for (int i=0;i<3;++i) p.drawEllipse(QPointF(86,87),30+i*19,30+i*19);
            p.setBrush(QColor(238,230,255));p.setPen(Qt::NoPen);
            p.drawRoundedRect(QRectF(78,65,16,33),8,8);p.drawRect(QRectF(84,98,4,14));
        }
        if (underMouse() || hasFocus()) {
            p.fillRect(art,QColor(0,0,0,35));p.setPen(Qt::NoPen);p.setBrush(QColor("#ffdb63"));p.drawEllipse(QPointF(140,144),19,19);
            p.setBrush(QColor("#171414"));QPainterPath triangle;triangle.moveTo(136,137);triangle.lineTo(147,144);triangle.lineTo(136,151);triangle.closeSubpath();p.drawPath(triangle);
        }
        p.restore();
        QFont f=font();f.setPixelSize(14);f.setBold(true);p.setFont(f);p.setPen(QColor("#f5f1ec"));
        QFontMetrics fm(f);QString title=data["title"].toString();
        // Two explicit lines, elided at word boundaries by Qt's text layout.
        QTextLayout layout(title,f);layout.beginLayout();
        for(int i=0;i<2;++i) { auto line=layout.createLine();if(!line.isValid())break;line.setLineWidth(168);line.setPosition(QPointF(2,185+i*19));
            if(i==1 && line.textStart()+line.textLength()<title.size()) {
                p.drawText(QRect(2,204,168,19),Qt::AlignLeft|Qt::AlignVCenter,fm.elidedText(title.mid(line.textStart()),Qt::ElideRight,168));break;
            }
            line.draw(&p,QPointF());
        }
        layout.endLayout();
        f.setPixelSize(11);f.setBold(false);p.setFont(f);p.setPen(QColor("#aaa5b3"));
        p.drawText(QRect(2,226,168,18),Qt::AlignVCenter,QFontMetrics(f).elidedText(data["author"].toString(),Qt::ElideRight,168));
        if(hasFocus()) {p.setPen(QPen(QColor("#ffdb63"),2));p.setBrush(Qt::NoBrush);p.drawRoundedRect(art,12,12);}
    }
    void enterEvent(QEnterEvent *e) override {QPushButton::enterEvent(e);update();}
    void leaveEvent(QEvent *e) override {QPushButton::leaveEvent(e);update();}
};

class SpokenSection final : public QFrame {
public:
    bool ru, book, started=false;
    int generation=0;
    QString kind;
    QString mode="intro";
    PlaySpoken play;
    SpokenCatalog catalog;
    QNetworkAccessManager pictures;
    QLabel *heading,*subtitle,*status,*source;
    QLineEdit *search;
    QPushButton *find,*retry;
    QComboBox *contentLanguage;
    QScrollArea *scroll;
    QHBoxLayout *cards;
    QJsonArray entries;
    QPointer<QDialog> details;
    SpokenSection(QString type, bool russian, PlaySpoken cb, QWidget *parent)
        : QFrame(parent),ru(russian),book(type=="audiobooks"),kind(type),play(cb),catalog(this),pictures(this) {
        setObjectName(book?"AudiobooksSection":"PodcastsSection");
        setStyleSheet("QFrame#AudiobooksSection,QFrame#PodcastsSection{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #1c1922,stop:1 #101116);border:1px solid #34303c;border-radius:20px;}"
            "QLabel{background:transparent;color:#f5f1ec;border:0;}"
            "QLineEdit,QComboBox{background:#121219;color:#eeeaf5;border:1px solid #46404f;border-radius:10px;padding:8px 12px;min-height:18px;}"
            "QPushButton{color:#eeeaf5;background:#292430;border:1px solid #4b4055;border-radius:10px;padding:8px 14px;}"
            "QPushButton:hover{background:#40334d;}QPushButton:focus{border-color:#ffdb63;}"
            "QScrollArea{background:transparent;border:0;}QScrollBar:horizontal{height:7px;background:#17131d;}QScrollBar::handle:horizontal{background:#5a4b67;border-radius:3px;min-width:40px;}");
        auto outer=new QVBoxLayout(this);outer->setContentsMargins(22,20,22,16);outer->setSpacing(12);
        auto titleRow=new QHBoxLayout;heading=new QLabel(this);heading->setStyleSheet("font-size:23px;font-weight:800;");titleRow->addWidget(heading,1);
        source=new QLabel(book?"LIBRIVOX / INTERNET ARCHIVE":"PODCASTS / RSS",this);source->setStyleSheet("font-size:10px;color:#b4a7c7;letter-spacing:1px;");titleRow->addWidget(source);outer->addLayout(titleRow);
        subtitle=new QLabel(this);subtitle->setWordWrap(true);subtitle->setStyleSheet("font-size:12px;color:#b1aabf;");outer->addWidget(subtitle);
        auto controls=new QHBoxLayout;search=new QLineEdit(this);search->setClearButtonEnabled(true);search->setMinimumWidth(120);controls->addWidget(search,1);
        contentLanguage=new QComboBox(this);contentLanguage->addItem("English","en");contentLanguage->addItem(QString::fromUtf8("Русский"),"ru");contentLanguage->setCurrentIndex(ru?1:0);controls->addWidget(contentLanguage);
        find=new QPushButton(this);controls->addWidget(find);outer->addLayout(controls);
        status=new QLabel(this);status->setWordWrap(true);status->setStyleSheet("font-size:12px;color:#bbb1c9;");outer->addWidget(status);
        retry=new QPushButton(this);retry->setMaximumWidth(220);outer->addWidget(retry,0,Qt::AlignLeft);retry->hide();
        scroll=new QScrollArea(this);scroll->setWidgetResizable(true);scroll->setVerticalScrollBarPolicy(Qt::ScrollBarAlwaysOff);scroll->setHorizontalScrollBarPolicy(Qt::ScrollBarAsNeeded);scroll->setFixedHeight(260);
        auto inner=new QWidget;inner->setStyleSheet("background:transparent;");cards=new QHBoxLayout(inner);cards->setContentsMargins(0,0,0,6);cards->setSpacing(16);cards->addStretch();scroll->setWidget(inner);outer->addWidget(scroll);
        QObject::connect(find,&QPushButton::clicked,this,[this]{load();});
        QObject::connect(search,&QLineEdit::returnPressed,this,[this]{load();});
        QObject::connect(retry,&QPushButton::clicked,this,[this]{load();});
        QObject::connect(contentLanguage,&QComboBox::currentIndexChanged,this,[this]{if(started)load();});
        translate(ru);
    }
    void translate(bool russian) {
        ru=russian;
        heading->setText(book?text(ru,"Audiobooks","Аудио Книги"):text(ru,"Podcasts","Подкасты"));
        subtitle->setText(book?text(ru,"A little more story. Discover unabridged classics, read by real people.","Чуть больше истории. Открывайте полные книги, озвученные живыми голосами.")
                             :text(ru,"A new perspective. Conversations, discoveries and stories worth your time.","Новый взгляд. Разговоры, открытия и истории, которым хочется уделить время."));
        search->setPlaceholderText(book?text(ru,"Title or author","Название или автор"):text(ru,"Podcast or topic","Подкаст или тема"));
        search->setAccessibleName(search->placeholderText());contentLanguage->setAccessibleName(book?text(ru,"Content language","Язык записей"):text(ru,"Catalogue region","Регион каталога"));
        contentLanguage->setToolTip(book?text(ru,"Language of the audiobooks","Язык аудиокниг"):text(ru,"Choose the RU or EN directory. Publishers determine the language of each episode.","Выберите RU- или EN-каталог. Язык каждого выпуска определяется издателем."));
        find->setText(text(ru,"Search","Найти"));retry->setText(text(ru,"Try again","Попробовать снова"));
        showCount();
        if(details)details->close();
    }
    void showCount() {
        if(mode=="intro"){status->setText(text(ru,"Open a card to choose a chapter or episode.","Откройте карточку, чтобы выбрать главу или выпуск."));return;}
        if(mode=="loading"){status->setText(text(ru,"Finding something worth listening to…","Ищем то, что стоит послушать…"));return;}
        if(mode=="offline"){status->setText(text(ru,"Connect to the internet to discover books and podcasts.","Подключитесь к интернету, чтобы открыть книги и подкасты."));return;}
        if(mode=="error"){status->setText(text(ru,"The catalogue is unavailable. Your music is still here. Please try again.","Каталог пока недоступен. Ваша музыка по-прежнему здесь. Попробуйте снова."));return;}
        status->setText(entries.isEmpty()?text(ru,"Nothing found. Try another title or language.","Ничего не нашлось. Попробуйте другое название или язык.")
            :text(ru,"%1 to explore · open a card to listen","%1 для знакомства · откройте карточку и слушайте").arg(entries.size()));
    }
    void setEntries(QJsonArray items) {
        entries=items;mode="ready";
        while(cards->count()>1){auto item=cards->takeAt(0);if(item->widget())item->widget()->deleteLater();delete item;}
        for(const auto &value:entries) {
            auto item=value.toObject();auto card=new CoverCard(item,book,scroll->widget());cards->insertWidget(cards->count()-1,card);
            QObject::connect(card,&QPushButton::clicked,this,[this,item]{open(item);});
            auto cover=QUrl(item["cover"].toString());if(cover.scheme()!="https"&&cover.scheme()!="http")continue;
            QNetworkRequest request(cover);request.setTransferTimeout(12000);request.setAttribute(QNetworkRequest::RedirectPolicyAttribute,QNetworkRequest::NoLessSafeRedirectPolicy);
            auto reply=pictures.get(request);QPointer<CoverCard> guard(card);
            QObject::connect(reply,&QNetworkReply::readyRead,reply,[reply]{if(reply->bytesAvailable()>4*1024*1024)reply->abort();});
            QObject::connect(reply,&QNetworkReply::finished,this,[reply,guard]{
                if(guard&&reply->error()==QNetworkReply::NoError){auto bytes=reply->readAll();QImage image;image.loadFromData(bytes);if(!image.isNull()){guard->cover=QPixmap::fromImage(image.scaled(400,400,Qt::KeepAspectRatio,Qt::SmoothTransformation));guard->update();}}
                reply->deleteLater();
            });
        }
        showCount();
    }
    void load() {
        started=true;int token=++generation;retry->hide();find->setEnabled(false);mode="loading";showCount();
        if(qEnvironmentVariableIsSet("ECHOES_SPOKEN_OFFLINE")){find->setEnabled(true);mode="offline";showCount();return;}
        catalog.browse(kind,contentLanguage->currentData().toString(),search->text().trimmed(),[this,token](QJsonArray found,QString error){
            if(token!=generation)return;find->setEnabled(true);
            if(!error.isEmpty()){mode="error";showCount();retry->show();return;}
            setEntries(found);
        });
    }
    void open(QJsonObject item) {
        if(details){details->raise();return;}
        auto dialog=new QDialog(this);details=dialog;dialog->setAttribute(Qt::WA_DeleteOnClose);dialog->setWindowTitle(item["title"].toString());dialog->resize(760,640);dialog->setMinimumSize(540,440);
        dialog->setStyleSheet("QDialog{background:#121117;color:#f3edf9;}QLabel{color:#f3edf9;background:transparent;}QListWidget{background:#19171f;border:1px solid #39323f;border-radius:12px;color:#eee7f6;padding:8px;}QListWidget::item{padding:12px;border-bottom:1px solid #302936;}QListWidget::item:selected{background:#393047;border-radius:8px;}QPushButton{padding:10px 18px;border-radius:12px;background:#ffdb63;color:#161218;font-weight:bold;}");
        auto layout=new QVBoxLayout(dialog);layout->setContentsMargins(24,24,24,24);layout->setSpacing(12);
        auto title=new QLabel(item["title"].toString(),dialog);title->setWordWrap(true);title->setTextFormat(Qt::PlainText);title->setStyleSheet("font-size:24px;font-weight:800;");layout->addWidget(title);
        auto author=new QLabel(item["author"].toString(),dialog);author->setWordWrap(true);author->setTextFormat(Qt::PlainText);author->setStyleSheet("color:#b5a7c4;");layout->addWidget(author);
        auto description=new QLabel(plain(item["description"].toString()).left(480),dialog);description->setWordWrap(true);description->setTextFormat(Qt::PlainText);description->setMaximumHeight(110);layout->addWidget(description);
        auto note=new QLabel(text(ru,"Loading chapters and episodes…","Загружаем главы и выпуски…"),dialog);note->setWordWrap(true);layout->addWidget(note);
        auto list=new QListWidget(dialog);list->setWordWrap(true);layout->addWidget(list,1);
        auto controls=new QHBoxLayout;auto go=new QPushButton(text(ru,"Play selected","Слушать выбранное"),dialog);go->setEnabled(false);controls->addWidget(go);controls->addStretch();auto close=new QPushButton(text(ru,"Close","Закрыть"),dialog);controls->addWidget(close);layout->addLayout(controls);
        QObject::connect(close,&QPushButton::clicked,dialog,&QDialog::close);
        QPointer<QDialog> guard(dialog);
        catalog.episodes(item,[this,guard,list,note,go,item](QJsonArray tracks,QString error){
            if(!guard)return;
            if(!error.isEmpty()||tracks.isEmpty()){note->setText(text(ru,"No playable episodes were returned. Please try another card.","Не удалось получить записи для прослушивания. Попробуйте другую карточку."));return;}
            note->setText(text(ru,"%1 chapters / episodes · double-click to listen","%1 глав / выпусков · двойной щелчок для прослушивания").arg(tracks.size()));
            for(const auto &v:tracks){auto t=v.toObject();QString d=duration(t["duration"].toInt());auto row=new QListWidgetItem(t["title"].toString()+(d.isEmpty()?"":"  ·  "+d),list);row->setData(Qt::UserRole,t);}
            list->setCurrentRow(0);go->setEnabled(true);
            auto start=[this,guard,list,tracks]{int index=list->currentRow();if(index<0||!play)return;auto bytes=QJsonDocument(tracks).toJson(QJsonDocument::Compact);play(bytes.constData(),index);if(guard)guard->close();};
            QObject::connect(go,&QPushButton::clicked,guard,start);QObject::connect(list,&QListWidget::itemDoubleClicked,guard,[start](QListWidgetItem*){start();});
        });
        dialog->show();
    }
    void showEvent(QShowEvent *event) override {QFrame::showEvent(event);if(!started)QTimer::singleShot(0,this,[this]{load();});}
};

class SpokenPanel final : public QWidget {
public:
    SpokenSection *books,*podcasts;
    SpokenPanel(QString language,PlaySpoken play) {
        setObjectName("SpokenDiscovery");auto layout=new QVBoxLayout(this);layout->setContentsMargins(0,8,0,8);layout->setSpacing(22);
        books=new SpokenSection("audiobooks",language=="ru",play,this);podcasts=new SpokenSection("podcasts",language=="ru",play,this);layout->addWidget(books);layout->addWidget(podcasts);
    }
    void language(QString language){books->translate(language=="ru");podcasts->translate(language=="ru");}
};
}

EUI_API void *eui_spoken_new(const wchar_t *language, PlaySpoken play) {return new SpokenPanel(QString::fromWCharArray(language),play);}
EUI_API void eui_spoken_language(void *widget,const wchar_t *language){static_cast<SpokenPanel*>(widget)->language(QString::fromWCharArray(language));}
EUI_API void eui_spoken_fixture(void *widget, int podcasts, const char *json){
    auto section=podcasts?static_cast<SpokenPanel*>(widget)->podcasts:static_cast<SpokenPanel*>(widget)->books;
    section->setEntries(QJsonDocument::fromJson(json).array());
}
