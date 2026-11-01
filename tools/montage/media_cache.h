#pragma once
#include <cstring>
#include "style.h"

// Bounded, demand-driven decoding. No media decoding happens inside paintEvent.
class MediaCache: public QObject {
    struct Job { QString key,path; int second; bool wave; };
    QString ffmpeg; QCache<QString,QPixmap> images{320};QCache<QString,QVector<float>> waves{64};
    QQueue<Job> queue;QSet<QString> pending,failed;QSet<QProcess*> active;
    mutable QHash<QString,QString> signatures;
    QString key(QString path,int second,bool wave)const {if(!signatures.contains(path)){QFileInfo f(path);if(signatures.size()>4096)signatures.clear();signatures[path]=path+"|"+QString::number(f.lastModified().toMSecsSinceEpoch())+"|"+QString::number(f.size());}return signatures[path]+"|"+QString::number(second)+(wave?"w":"v");}
    void request(QString path,int second,bool wave){
        QString k=key(path,second,wave);if(pending.contains(k)||failed.contains(k)||queue.size()>96)return;
        pending.insert(k);queue.enqueue({k,path,second,wave});pump();
    }
    void pump(){
        while(active.size()<2&&!queue.isEmpty()){
            Job j=queue.dequeue();auto p=new QProcess(this);active.insert(p);
            auto done=[this,p,j](bool ok){
                if(!active.remove(p))return;
                QByteArray data=p->readAllStandardOutput();
                if(ok&&j.wave){auto peaks=new QVector<float>;int count=data.size()/int(sizeof(float));peaks->reserve((count+79)/80);
                    for(int i=0;i<count;i+=80){float peak=0;for(int s=i;s<qMin(i+80,count);s++){float sample;std::memcpy(&sample,data.constData()+s*4,4);if(std::isfinite(sample))peak=qMax(peak,std::abs(sample));}peaks->append(qMin(1.f,peak));}
                    waves.insert(j.key,peaks);
                }else if(ok){auto pix=new QPixmap;if(pix->loadFromData(data))images.insert(j.key,pix);else{delete pix;failed.insert(j.key);}}
                else failed.insert(j.key);
                pending.remove(j.key);p->deleteLater();if(changed)changed();pump();
            };
            connect(p,qOverload<int,QProcess::ExitStatus>(&QProcess::finished),this,[done](int code,QProcess::ExitStatus st){done(code==0&&st==QProcess::NormalExit);});
            connect(p,&QProcess::errorOccurred,this,[done](QProcess::ProcessError e){if(e==QProcess::FailedToStart)done(false);});
            QStringList args{"-v","error","-threads","1","-ss",j.wave?QString::number(j.second):QString::number(j.second/1000.,'f',3),"-i",j.path,"-filter_threads","1"};
            if(j.wave)args<<"-t"<<"30"<<"-vn"<<"-ac"<<"1"<<"-ar"<<"8000"<<"-f"<<"f32le"<<"pipe:1";
            else args<<"-frames:v"<<"1"<<"-vf"<<"scale=160:90:force_original_aspect_ratio=decrease,pad=160:90:(ow-iw)/2:(oh-ih)/2"<<"-c:v"<<"mjpeg"<<"-f"<<"image2pipe"<<"pipe:1";
            p->start(ffmpeg,args);
            QTimer::singleShot(15000,p,[p]{if(p->state()!=QProcess::NotRunning)p->kill();});
        }
    }
public:
    std::function<void()> changed;
    explicit MediaCache(QString executable,QObject *parent):QObject(parent),ffmpeg(executable){}
    const QPixmap *thumbnail(QString path,double time){int stamp=qMax(0,qRound(time*1000));QString k=key(path,stamp,false);auto found=images.object(k);if(!found)request(path,stamp,false);return found;}
    float peak(QString path,double t){int second=qMax(0,int(t)/30*30);QString k=key(path,second,true);auto data=waves.object(k);if(!data){request(path,second,true);return -1;}int i=int((t-second)*100);return i>=0&&i<data->size()?data->at(i):0;}
    void invalidate(QString path){signatures.remove(path);}
    int imageCount()const{return images.size();}int waveCount()const{return waves.size();}
    void shutdown(){changed=nullptr;queue.clear();for(auto p:active){p->disconnect(this);p->kill();p->waitForFinished(1000);}active.clear();}
    ~MediaCache(){shutdown();}
};

// Viewer: shows the composited frame and lets the user move/scale the selected layer directly.
class PreviewFrame: public QWidget {
    int drag=0;QPointF pressAt;double pressDistance=1;
public:
    QImage image;QString hint=EchoesText("Перетащите видео сюда или нажмите «Импорт медиа»");QSize canvas{1920,1080};
    bool hasBox=false,guides=false,safe=false,snapX=false,snapY=false,picking=false;QPointF boxCenter;QSizeF boxSize;double boxRotation=0;QPainterPath maskPath;
    std::function<void()> begin,end,doubleClicked;std::function<void(QPointF)> moved,pick;std::function<void(double)> scaled;std::function<void(QColor)> colorPicked;
    explicit PreviewFrame(QWidget *parent=nullptr):QWidget(parent){setMinimumSize(320,180);setMouseTracking(true);}
    QRectF area()const{QSizeF s=canvas;s.scale(size(),Qt::KeepAspectRatio);return QRectF(QPointF((width()-s.width())/2,(height()-s.height())/2),s);}
    double ratio()const{return area().width()/qMax(1,canvas.width());}
    QPointF toProject(QPointF w)const{QRectF a=area();return (w-a.topLeft())/ratio();}
    QPolygonF box()const{QRectF a=area();double k=ratio();QTransform t;t.translate(a.left()+boxCenter.x()*k,a.top()+boxCenter.y()*k);t.rotate(boxRotation);return t.map(QPolygonF(QRectF(-boxSize.width()*k/2,-boxSize.height()*k/2,boxSize.width()*k,boxSize.height()*k)));}
    void paintEvent(QPaintEvent*)override{
        QPainter p(this);p.fillRect(rect(),QColor(pal::deep));QRectF a=area();p.fillRect(a,Qt::black);
        if(image.isNull()){p.setPen(QColor("#7d7d84"));p.drawText(rect().adjusted(32,20,-32,-20),Qt::AlignCenter|Qt::TextWordWrap,hint);}else p.drawImage(a,image);
        p.setRenderHint(QPainter::Antialiasing);
        if(guides){p.setPen(QPen(QColor(255,255,255,70),1));for(int i=1;i<3;i++){p.drawLine(QPointF(a.left()+a.width()*i/3,a.top()),QPointF(a.left()+a.width()*i/3,a.bottom()));p.drawLine(QPointF(a.left(),a.top()+a.height()*i/3),QPointF(a.right(),a.top()+a.height()*i/3));}}
        if(safe){p.setPen(QPen(QColor(255,255,255,80),1,Qt::DashLine));p.drawRect(a.adjusted(a.width()*.05,a.height()*.05,-a.width()*.05,-a.height()*.05));p.drawRect(a.adjusted(a.width()*.1,a.height()*.1,-a.width()*.1,-a.height()*.1));}
        if(snapX){p.setPen(QPen(QColor(pal::accent),1));p.drawLine(QPointF(a.center().x(),a.top()),QPointF(a.center().x(),a.bottom()));}
        if(snapY){p.setPen(QPen(QColor(pal::accent),1));p.drawLine(QPointF(a.left(),a.center().y()),QPointF(a.right(),a.center().y()));}
        if(hasBox){auto poly=box();p.setPen(QPen(QColor(pal::accent),1.5,Qt::DashLine));p.setBrush(Qt::NoBrush);p.drawPolygon(poly);p.setPen(QPen(QColor(pal::accent),1.5));p.setBrush(QColor("#1c1c1f"));for(int i=0;i<4;i++)p.drawRect(QRectF(poly[i]-QPointF(5,5),QSizeF(10,10)));}
        if(!maskPath.isEmpty()){QTransform t;t.translate(a.left(),a.top());t.scale(ratio(),ratio());p.setBrush(Qt::NoBrush);p.setPen(QPen(QColor(255,255,255,190),1.2,Qt::DashDotLine));p.drawPath(t.map(maskPath));}
        if(picking){p.setPen(QColor(pal::accentHi));p.drawText(rect().adjusted(10,8,-10,-8),Qt::AlignTop|Qt::AlignHCenter,EchoesText("Нажмите на цвет фона, который нужно убрать"));}
    }
    void mousePressEvent(QMouseEvent *e)override{
        if(e->button()!=Qt::LeftButton)return;QPointF at=e->position();
        if(picking){picking=false;setCursor(Qt::ArrowCursor);QRectF a=area();if(!image.isNull()&&a.contains(at)){QPoint px(int((at.x()-a.left())/a.width()*image.width()),int((at.y()-a.top())/a.height()*image.height()));if(colorPicked)colorPicked(image.pixelColor(px.x()%image.width(),px.y()%image.height()));}update();return;}
        if(hasBox){auto poly=box();for(int i=0;i<4;i++)if(QLineF(poly[i],at).length()<10){drag=2;pressAt=at;QPointF c=area().topLeft()+boxCenter*ratio();pressDistance=qMax(4.,QLineF(c,at).length());if(begin)begin();return;}}
        if(!(hasBox&&box().containsPoint(at,Qt::OddEvenFill))&&pick)pick(toProject(at));
        if(hasBox&&box().containsPoint(at,Qt::OddEvenFill)){drag=1;pressAt=at;if(begin)begin();}
    }
    void mouseMoveEvent(QMouseEvent *e)override{
        QPointF at=e->position();
        if(!drag){bool corner=false;if(hasBox)for(auto v:box())corner|=QLineF(v,at).length()<10;setCursor(picking?Qt::CrossCursor:corner?Qt::SizeFDiagCursor:hasBox&&box().containsPoint(at,Qt::OddEvenFill)?Qt::SizeAllCursor:Qt::ArrowCursor);return;}
        if(drag==1&&moved)moved((at-pressAt)/ratio());
        if(drag==2&&scaled){QPointF c=area().topLeft()+boxCenter*ratio();scaled(QLineF(c,at).length()/pressDistance);}
    }
    void mouseReleaseEvent(QMouseEvent*)override{if(drag){drag=0;snapX=snapY=false;if(end)end();update();}}
    void mouseDoubleClickEvent(QMouseEvent*)override{if(doubleClicked)doubleClicked();}
    void wheelEvent(QWheelEvent *e)override{if(!hasBox||!scaled){e->ignore();return;}double steps=e->angleDelta().y()/120.;if(begin)begin();scaled(std::pow(1.06,steps));if(end)end();e->accept();}
};

// DaVinci-style scopes computed from the current viewer frame.
class Scopes: public QWidget {
public:
    QImage source;int mode=0;
    explicit Scopes(QWidget *parent=nullptr):QWidget(parent){setMinimumHeight(150);}
    void setImage(const QImage &image){source=image.isNull()?QImage():image.scaledToWidth(qMin(256,qMax(1,image.width()))).convertToFormat(QImage::Format_RGB32);update();}
    void paintEvent(QPaintEvent*)override{
        QPainter p(this);p.fillRect(rect(),QColor("#101012"));QRectF r=rect().adjusted(6,6,-6,-6);p.setPen(QColor("#26262b"));for(int i=1;i<4;i++)p.drawLine(QPointF(r.left(),r.top()+r.height()*i/4),QPointF(r.right(),r.top()+r.height()*i/4));
        if(source.isNull()){p.setPen(QColor(pal::muted));p.drawText(rect(),Qt::AlignCenter,EchoesText("Нет кадра"));return;}
        int w=source.width(),h=source.height();
        if(mode==0){QVector<int> hist[3];for(auto &v:hist)v.fill(0,256);for(int y=0;y<h;y++){auto line=reinterpret_cast<const QRgb*>(source.constScanLine(y));for(int x=0;x<w;x++){hist[0][qRed(line[x])]++;hist[1][qGreen(line[x])]++;hist[2][qBlue(line[x])]++;}}
            int top=1;for(auto &v:hist)for(int i=2;i<254;i++)top=qMax(top,v[i]);QColor colors[3]={QColor(255,70,70,150),QColor(70,255,110,150),QColor(80,140,255,150)};
            p.setRenderHint(QPainter::Antialiasing);for(int c=0;c<3;c++){QPainterPath path;path.moveTo(r.bottomLeft());for(int i=0;i<256;i++)path.lineTo(r.left()+r.width()*i/255,r.bottom()-r.height()*qMin(1.,double(hist[c][i])/top));path.lineTo(r.bottomRight());p.fillPath(path,colors[c]);}}
        else if(mode==1){QImage plot(w,128,QImage::Format_ARGB32);plot.fill(Qt::transparent);for(int y=0;y<h;y++){auto line=reinterpret_cast<const QRgb*>(source.constScanLine(y));for(int x=0;x<w;x++){int l=qGray(line[x]);int py=127-l/2;QRgb old=plot.pixel(x,py);int a=qMin(255,qAlpha(old)+40);plot.setPixel(x,py,qRgba(120,255,160,a));}}p.drawImage(r,plot);}
        else{double s=qMin(r.width(),r.height());QRectF sq(r.center().x()-s/2,r.center().y()-s/2,s,s);p.setPen(QColor("#2e2e34"));p.drawEllipse(sq);p.drawLine(QPointF(sq.center().x(),sq.top()),QPointF(sq.center().x(),sq.bottom()));p.drawLine(QPointF(sq.left(),sq.center().y()),QPointF(sq.right(),sq.center().y()));
            p.setPen(QColor(150,255,190,60));for(int y=0;y<h;y+=2){auto line=reinterpret_cast<const QRgb*>(source.constScanLine(y));for(int x=0;x<w;x+=2){double R=qRed(line[x]),G=qGreen(line[x]),B=qBlue(line[x]);double cb=-.1687*R-.3313*G+.5*B,cr=.5*R-.4187*G-.0813*B;p.drawPoint(QPointF(sq.center().x()+cb/255*s,sq.center().y()-cr/255*s));}}}
    }
};

// DaVinci-style curve: endpoints slide vertically, click adds a point, right click removes it.
class CurveEditor: public QWidget {
    int drag=-1;
    QRectF area()const{return QRectF(rect()).adjusted(8,8,-8,-8);}
    QPointF toWidget(QPointF v)const{QRectF a=area();return {a.left()+v.x()*a.width(),a.bottom()-v.y()*a.height()};}
    QPointF toCurve(QPointF w)const{QRectF a=area();return {qBound(0.,(w.x()-a.left())/a.width(),1.),qBound(0.,(a.bottom()-w.y())/a.height(),1.)};}
public:
    QList<QPointF> points{{0,0},{1,1}};QColor color{"#e8e8ea"};std::function<void()> begin,changed,end;
    explicit CurveEditor(QWidget *parent=nullptr):QWidget(parent){setMinimumHeight(180);setMouseTracking(true);}
    bool dragging()const{return drag>=0;}
    void setText(QString s){QList<QPointF> out;for(auto t:s.split(' ',Qt::SkipEmptyParts)){auto xy=t.split('/');if(xy.size()==2)out<<QPointF(qBound(0.,xy[0].toDouble(),1.),qBound(0.,xy[1].toDouble(),1.));}
        std::sort(out.begin(),out.end(),[](auto a,auto b){return a.x()<b.x();});if(out.size()<2)out={{0,0},{1,1}};points=out;update();}
    QString text()const{QStringList t;for(auto v:points)t<<QString::number(v.x(),'f',3)+"/"+QString::number(v.y(),'f',3);return t.join(' ');}
    void paintEvent(QPaintEvent*)override{
        QPainter p(this);p.setRenderHint(QPainter::Antialiasing);p.fillRect(rect(),QColor("#101012"));QRectF a=area();p.setPen(QColor("#26262b"));
        for(int i=1;i<4;i++){p.drawLine(QPointF(a.left()+a.width()*i/4,a.top()),QPointF(a.left()+a.width()*i/4,a.bottom()));p.drawLine(QPointF(a.left(),a.top()+a.height()*i/4),QPointF(a.right(),a.top()+a.height()*i/4));}
        p.setPen(QPen(QColor("#3a3a40"),1,Qt::DashLine));p.drawLine(a.bottomLeft(),a.topRight());
        // Catmull-Rom through the points approximates FFmpeg's spline closely enough for editing.
        QPainterPath path;path.moveTo(toWidget(points.first()));for(int i=0;i+1<points.size();i++){QPointF p0=points[qMax(0,i-1)],p1=points[i],p2=points[i+1],p3=points[qMin(int(points.size())-1,i+2)];
            QPointF c1=p1+(p2-p0)/6,c2=p2-(p3-p1)/6;c1.setY(qBound(0.,c1.y(),1.));c2.setY(qBound(0.,c2.y(),1.));path.cubicTo(toWidget(c1),toWidget(c2),toWidget(p2));}
        p.setPen(QPen(color,2));p.setBrush(Qt::NoBrush);p.drawPath(path);p.setBrush(QColor("#1c1c1f"));for(auto v:points)p.drawEllipse(toWidget(v),4.5,4.5);
    }
    void mousePressEvent(QMouseEvent *e)override{
        QPointF at=e->position();int hit=-1;for(int i=0;i<points.size();i++)if(QLineF(toWidget(points[i]),at).length()<9)hit=i;
        if(e->button()==Qt::RightButton){if(hit>0&&hit<points.size()-1){if(begin)begin();points.removeAt(hit);update();if(changed)changed();if(end)end();}return;}
        if(e->button()!=Qt::LeftButton)return;if(begin)begin();
        if(hit<0){QPointF v=toCurve(at);int i=0;while(i<points.size()&&points[i].x()<v.x())i++;if(i==0||i==points.size()||points[i].x()-v.x()<.02||v.x()-points[i-1].x()<.02){drag=-1;if(end)end();return;}points.insert(i,v);hit=i;if(changed)changed();}
        drag=hit;update();
    }
    void mouseMoveEvent(QMouseEvent *e)override{
        if(drag<0)return;QPointF v=toCurve(e->position());if(drag==0)v.setX(0);else if(drag==points.size()-1)v.setX(1);else v.setX(qBound(points[drag-1].x()+.02,v.x(),points[drag+1].x()-.02));
        points[drag]=v;update();if(changed)changed();
    }
    void mouseReleaseEvent(QMouseEvent*)override{if(drag>=0){drag=-1;if(end)end();}}
};

class MediaList: public QListWidget {
public:
    explicit MediaList(QWidget *p=nullptr):QListWidget(p){setDragEnabled(true);setSelectionMode(QAbstractItemView::ExtendedSelection);}
protected:
    QMimeData *mimeData(const QList<QListWidgetItem*> &items)const override{auto data=new QMimeData;QList<QUrl> urls;for(auto i:items)urls<<QUrl::fromLocalFile(i->data(Qt::UserRole).toString());data->setUrls(urls);return data;}
};
