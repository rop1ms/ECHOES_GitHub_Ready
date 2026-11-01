#pragma once
#include "media_cache.h"

// mode: 1 move, 2 trim start, 3 trim end, 4 rubber band, 5 fade in, 6 fade out.
class Timeline: public QWidget {
    int mode=0,targetRow=-1,targetSlot=-1;bool started=false,scrubbing=false;QPointF anchor,hover{-1,-1};QRectF band;Clip original;QList<Clip> originals;
    void target(double y,QString kind){
        targetRow=-1;targetSlot=-1;
        if(y<Top){targetSlot=0;return;}if(y>=Top+p->tracks*Row){targetSlot=p->tracks;return;}
        int row=int((y-Top)/Row);double offset=y-Top-row*Row;
        if(offset<12)targetSlot=row;else if(offset>Row-12)targetSlot=row+1;
        else if(p->lanes[row].kind==kind&&!p->locked(row))targetRow=row;
        else targetSlot=row+(offset>Row/2?1:0);
    }
    void commitDrop(){
        if(mode!=1||!started)return;QString kind=Project::type(original);
        if(targetSlot>=0&&p->tracks<64){int at=p->insertTrack(targetSlot,kind);auto c=current();if(c)c->track=at;}
        else if(targetRow>=0){auto c=current();if(c)c->track=targetRow;}
        p->compact();
    }
    double timeAt(double x)const{return qMax(0.,(x-Header)/zoom);}
    QRectF eyeRect(int t,int hx)const{return QRectF(hx+14,Top+t*Row+Row-30,20,16);}
    QRectF lockRect(int t,int hx)const{return QRectF(hx+44,Top+t*Row+Row-30,18,16);}
    QPointF fadeHandle(const Clip &c,bool in)const{QRectF r=rect(c);return in?QPointF(r.left()+qMin(c.fadeIn*zoom,r.width()/2)+6,r.top()+6):QPointF(r.right()-qMin(c.fadeOut*zoom,r.width()/2)-6,r.top()+6);}
public:
    static constexpr int Header=170,Top=58,Row=80;
    Project *p;MediaCache *cache;QString selected;QSet<QString> extra;double playhead=0,zoom=80;bool snap=true,linkedSelection=true,blade=false;
    QSize viewportSize{1000,200};
    std::function<void()> changed,before,selection,liveChanged;
    std::function<void(double)> seek;
    std::function<void(QString,double,int)> dropped;
    std::function<void(QString)> context;
    std::function<void(QString,double)> bladed;
    std::function<void(double,int)> emptyMenu;
    std::function<void(double)> rulerMenu;
    std::function<void(int)> trackMenu;
    std::function<void(double,double)> zoomAt;
    explicit Timeline(Project *pr,MediaCache *media):p(pr),cache(media){setMouseTracking(true);setFocusPolicy(Qt::StrongFocus);setAcceptDrops(true);resizeCanvas();}
    void resizeCanvas(){QSize s(qMax(viewportSize.width(),qBound(1000,int((p->end()+8)*zoom)+Header,2000000)),qMax(viewportSize.height(),Top+p->tracks*Row+64));setMinimumSize(s);resize(s);update();}
    Clip *current(){for(auto &c:p->clips)if(c.id==selected)return &c;return nullptr;}
    bool linked(const Clip &a,const Clip &b)const{return a.id==b.id||(linkedSelection&&!a.group.isEmpty()&&a.group==b.group);}
    bool isChosen(const Clip &c)const{if(c.id==selected||extra.contains(c.id))return true;if(!linkedSelection||c.group.isEmpty())return false;for(auto &v:p->clips)if((v.id==selected||extra.contains(v.id))&&v.group==c.group)return true;return false;}
    QStringList chosen()const{QStringList ids;for(auto &c:p->clips)if(isChosen(c))ids<<c.id;return ids;}
    void selectOnly(QString id){selected=id;extra.clear();}
    QRectF rect(const Clip &c)const{return QRectF(Header+c.start*zoom,Top+c.track*Row+8,qMax(5.,c.duration*zoom),Row-18);}
    static QColor bodyColor(const Clip &c){if(c.kind=="color"){QColor v(c.style["color"].toString("#1f6feb"));return v.isValid()?v:QColor("#1f6feb");}return QColor(c.kind=="audio"?pal::audio:c.kind=="text"?pal::title:c.kind=="adjust"?pal::adjust:pal::video);}
    void paintEvent(QPaintEvent *event)override{
        QPainter a(this);a.setRenderHint(QPainter::Antialiasing);a.fillRect(event->rect(),QColor("#18181b"));a.setFont(QFont("Segoe UI",9));
        QRect visible=visibleRegion().boundingRect();int hx=visible.left();double left=timeAt(visible.left()),right=(visible.right()-Header)/zoom;
        // Ruler with frame-accurate timecode.
        a.fillRect(QRect(hx,0,visible.width(),30),QColor("#202024"));a.fillRect(QRect(hx,30,visible.width(),Top-30),QColor("#1a1a1d"));
        double step=zoom<8?30:zoom<20?10:zoom<45?5:zoom<120?1:.5;
        for(double s=std::floor(left/step)*step;s<=right+step;s+=step){int x=Header+s*zoom;if(x<hx+Header)continue;a.setPen(QColor("#2a2a2f"));a.drawLine(x,30,x,height());a.setPen(QColor("#5a5a62"));a.drawLine(x,18,x,30);a.setPen(QColor("#9a9aa2"));a.drawText(x+4,15,timecode(s,p->fps));
            for(int k=1;k<5;k++){int sx=Header+(s+step*k/5)*zoom;a.setPen(QColor("#3d3d44"));a.drawLine(sx,24,sx,30);}}
        if(p->inPoint>=0||p->outPoint>0){double i=qMax(0.,p->inPoint),o=p->outPoint>0?p->outPoint:p->end();a.fillRect(QRectF(Header+i*zoom,0,qMax(1.,(o-i)*zoom),30),QColor(232,130,44,45));a.setPen(QColor(pal::accent));
            if(p->inPoint>=0)a.drawText(QPointF(Header+i*zoom+2,27),"[");if(p->outPoint>0)a.drawText(QPointF(Header+o*zoom-7,27),"]");}
        if(started&&mode==1){a.setPen(QColor("#77777e"));a.drawText(QRect(hx+Header+10,30,visible.width()-Header-20,Top-30),Qt::AlignVCenter,EchoesText("↑ Отпустите здесь — появится новая дорожка сверху"));}
        for(int t=0;t<p->tracks;t++){
            QRect row(hx,Top+t*Row,Header-2,Row);a.fillRect(row,QColor(t%2?"#222226":"#25252a"));a.fillRect(QRect(hx+Header,Top+t*Row,visible.width()-Header,Row),QColor(t%2?"#1b1b1e":"#1d1d20"));
            QString kind=p->lanes[t].kind;QColor code(kind=="audio"?pal::audio:kind=="text"?pal::title:pal::video);
            a.setPen(Qt::NoPen);a.setBrush(code);a.drawRoundedRect(QRectF(hx+12,Top+t*Row+10,30,18),3,3);a.setPen(Qt::white);a.drawText(QRectF(hx+12,Top+t*Row+10,30,18),Qt::AlignCenter,p->trackCode(t));
            a.setPen(QColor(p->muted(t)?"#6c6c72":"#e2e2e6"));a.drawText(QRect(hx+50,Top+t*Row+10,Header-60,18),Qt::AlignVCenter,a.fontMetrics().elidedText(p->trackName(t),Qt::ElideRight,Header-60));
            a.setBrush(Qt::NoBrush);a.setPen(QPen(QColor(p->muted(t)?pal::accent:"#9a9aa2"),1.4));if(kind=="audio")drawSpeaker(a,eyeRect(t,hx).adjusted(3,1,-3,-1),p->muted(t));else drawEye(a,eyeRect(t,hx).adjusted(2,3,-2,-3),p->muted(t));
            a.setPen(QPen(QColor(p->locked(t)?pal::accent:"#9a9aa2"),1.4));drawLock(a,lockRect(t,hx).adjusted(3,0,-3,0),p->locked(t));
            a.setPen(QColor("#121214"));a.drawLine(hx,Top+(t+1)*Row,visible.right(),Top+(t+1)*Row);
        }
        a.setPen(QColor("#0d0d0f"));a.drawLine(hx+Header-1,0,hx+Header-1,height());
        a.save();a.setClipRect(QRect(hx+Header,30,qMax(0,visible.width()-Header),height()));
        for(auto &m:p->markers){int x=Header+m.time*zoom;QColor c(m.color);c.setAlpha(70);a.setPen(c);a.drawLine(x,30,x,Top+p->tracks*Row);}
        for(auto &c:p->clips){QRectF r=rect(c);if(!r.intersects(visible))continue;bool chosen=isChosen(c);QColor bg=bodyColor(c);a.setOpacity(p->muted(c.track)?.4:1);a.setPen(Qt::NoPen);a.setBrush(bg.darker(c.kind=="audio"?100:115));a.drawRoundedRect(r,4,4);
            a.save();QPainterPath clip;clip.addRoundedRect(r.adjusted(1,1,-1,-1),3,3);a.setClipPath(clip,Qt::IntersectClip);
            if(c.kind=="video"||c.kind=="image"){
                double tile=qMax(52.,zoom);int first=qMax(0,int((visible.left()-r.left())/tile));int last=qMin(int(std::ceil(r.width()/tile)),int((visible.right()-r.left())/tile)+1);
                for(int i=first;i<last;i++){double t=i*tile/zoom;double sec=c.kind=="image"?0:qMax(0.,qMin(c.source-.04,c.reverse?c.in+(c.duration-t)*c.speed:c.in+t*c.speed));sec=std::floor(sec*c.sourceFps)/c.sourceFps;auto pix=cache->thumbnail(c.path,sec);QRectF cell(r.left()+i*tile,r.top()+18,tile-1,r.height()-18);if(pix)a.drawPixmap(cell,*pix,pix->rect());else{a.fillRect(cell,bg.darker(140));a.setPen(QColor("#5a5a62"));a.drawText(cell,Qt::AlignCenter,QString::number(sec,'f',1)+EchoesText(" с"));}}
            }else if(c.kind=="audio"){
                a.setPen(QPen(QColor(pal::wave),1.2));int from=qMax(int(r.left()),visible.left()+Header),to=qMin(int(r.right()),visible.right());
                for(int x=from;x<to;x+=2){double local=(x-r.left())/zoom;double source=c.reverse?c.in+(c.duration-local)*c.speed:c.in+local*c.speed;float v=cache->peak(c.path,source);double h=v<0?1:qMax(1.,std::sqrt(v)*(r.height()-22)/2*valueAt(c,"volume",local)/qMax(.01,c.gain)*qMin(1.,c.gain));a.drawLine(QPointF(x,r.center().y()-h+8),QPointF(x,r.center().y()+h+8));}
            }else if(c.kind=="text"){a.setPen(QColor("#f1e6ff"));a.drawText(r.adjusted(10,20,-8,-4),Qt::AlignVCenter,c.text.left(80).replace('\n',' '));}
            else if(c.kind=="adjust"){a.setPen(QColor("#ffe7b0"));a.drawText(r.adjusted(10,20,-8,-4),Qt::AlignVCenter,EchoesText("Корректирующий слой"));}
            QColor head=c.label.isEmpty()?bg.darker(160):QColor(c.label);head.setAlpha(225);a.fillRect(QRectF(r.left(),r.top(),r.width(),18),head);a.setPen(Qt::white);
            QString tags=(c.group.isEmpty()?"":" ↔")+QString(c.reverse?" ⟲":"")+(std::abs(c.speed-1)>.001?QString(" %1×").arg(c.speed):"")+(c.effect!="none"||!c.look.isEmpty()||!c.grade.isEmpty()?" fx":"");
            a.drawText(r.adjusted(7,0,-5,-r.height()+18),Qt::AlignVCenter,a.fontMetrics().elidedText(c.name+tags,Qt::ElideRight,int(r.width())-12));
            // Fade ramps, transitions and keyframes drawn over the clip body.
            a.setPen(QPen(QColor(255,255,255,170),1.2));if(c.fadeIn>0)a.drawLine(QPointF(r.left(),r.bottom()),QPointF(r.left()+qMin(c.fadeIn*zoom,r.width()/2),r.top()+18));if(c.fadeOut>0)a.drawLine(QPointF(r.right(),r.bottom()),QPointF(r.right()-qMin(c.fadeOut*zoom,r.width()/2),r.top()+18));
            if(!c.trans.isEmpty()){QRectF tr(r.left(),r.top()+18,qMin(c.transDur*zoom,r.width()),r.height()-18);a.fillRect(tr,QColor(232,130,44,70));a.setPen(QColor(pal::accentHi));a.drawText(tr,Qt::AlignCenter,"⇄");}
            a.setPen(Qt::NoPen);a.setBrush(QColor("#ffd27a"));for(auto v:c.keys){double kt=v.toObject()["time"].toDouble()-c.animationOffset;if(kt<-.001||kt>c.duration+.001)continue;QPointF k(r.left()+kt*zoom,r.bottom()-7);a.drawPolygon(QPolygonF{k+QPointF(0,-5),k+QPointF(5,0),k+QPointF(0,5),k+QPointF(-5,0)});}
            a.restore();a.setBrush(Qt::NoBrush);a.setPen(QPen(chosen?QColor(pal::accent):bg.lighter(130),chosen?2:1));a.drawRoundedRect(r,4,4);
            if(chosen&&c.id==selected&&r.width()>40){a.setPen(QPen(QColor(pal::accent),1.4));a.setBrush(QColor("#1c1c1f"));a.drawEllipse(fadeHandle(c,true),4,4);a.drawEllipse(fadeHandle(c,false),4,4);}
            a.setOpacity(1);
        }
        if(started&&mode==1){a.setPen(QColor("#77777e"));a.drawText(QRect(hx+Header+12,Top+p->tracks*Row+6,visible.width()-Header-24,40),Qt::AlignVCenter,EchoesText("↓ Отпустите здесь — появится новая дорожка снизу"));
            a.setPen(QPen(QColor(pal::accent),3));if(targetSlot>=0){int y=Top+targetSlot*Row;a.drawLine(hx+Header,y,visible.right(),y);}else if(targetRow>=0)a.drawRect(QRect(hx+Header,Top+targetRow*Row,visible.width()-Header-2,Row));}
        if(mode==4&&started){a.setPen(QPen(QColor(pal::accent),1,Qt::DashLine));a.setBrush(QColor(232,130,44,30));a.drawRect(band.normalized());}
        if(blade&&hover.x()>Header+hx&&hover.y()>Top){a.setPen(QPen(QColor("#ff6b5e"),1,Qt::DashLine));a.drawLine(QPointF(hover.x(),Top),QPointF(hover.x(),Top+p->tracks*Row));}
        for(auto &m:p->markers){double x=Header+m.time*zoom;a.setPen(Qt::NoPen);a.setBrush(QColor(m.color));a.drawPolygon(QPolygonF{QPointF(x-5,18),QPointF(x+5,18),QPointF(x+5,25),QPointF(x,30),QPointF(x-5,25)});}
        int px=Header+playhead*zoom;a.setPen(QPen(QColor(pal::playhead),1.6));a.drawLine(px,18,px,height());a.setBrush(QColor(pal::playhead));a.setPen(Qt::NoPen);a.drawPolygon(QPolygonF{QPointF(px-7,16),QPointF(px+7,16),QPointF(px+7,24),QPointF(px,31),QPointF(px-7,24)});a.restore();
        a.fillRect(QRect(hx,0,Header-1,30),QColor("#202024"));a.setPen(QColor(pal::accentHi));QFont tc("Consolas",11);a.setFont(tc);a.drawText(QRect(hx+10,0,Header-14,30),Qt::AlignVCenter,timecode(playhead,p->fps));
    }
    void mousePressEvent(QMouseEvent *e)override{
        int hx=visibleRegion().boundingRect().left();QPointF at=e->position();
        if(e->button()==Qt::RightButton){
            if(at.x()<hx+Header&&at.y()>=Top){int t=int((at.y()-Top)/Row);if(t<p->tracks&&trackMenu)trackMenu(t);return;}
            if(at.y()<30){if(rulerMenu)rulerMenu(timeAt(at.x()));return;}
            for(auto it=p->clips.rbegin();it!=p->clips.rend();++it)if(rect(*it).contains(at)){if(!isChosen(*it))selectOnly(it->id);if(selection)selection();update();if(context)context(it->id);return;}
            if(emptyMenu)emptyMenu(timeAt(at.x()),at.y()>=Top?int((at.y()-Top)/Row):-1);return;}
        if(e->button()!=Qt::LeftButton)return;setFocus();
        if(at.x()<hx+Header&&at.y()>=Top){int t=int((at.y()-Top)/Row);if(t<p->tracks){bool eye=eyeRect(t,hx).adjusted(-4,-4,4,4).contains(at),lock=lockRect(t,hx).adjusted(-4,-4,4,4).contains(at);if(!eye&&!lock)return;if(before)before();if(eye)p->lanes[t].muted=!p->lanes[t].muted;else p->lanes[t].locked=!p->lanes[t].locked;if(changed)changed();if(selection)selection();}return;}
        if(at.y()<Top){for(auto &m:p->markers)if(std::abs(Header+m.time*zoom-at.x())<6&&at.y()<30){playhead=m.time;if(seek)seek(playhead);update();return;}scrubbing=true;playhead=qBound(0.,timeAt(at.x()),qMax(0.,p->end()));if(seek)seek(playhead);update();return;}
        mode=0;started=false;targetRow=-1;targetSlot=-1;
        if(auto c=current();c&&!p->locked(c->track)&&!blade)for(bool in:{true,false})if(QLineF(fadeHandle(*c,in),at).length()<8){original=*c;originals.clear();for(auto &v:p->clips)if(linked(v,original))originals<<v;anchor=at;mode=in?5:6;return;}
        for(auto it=p->clips.rbegin();it!=p->clips.rend();++it)if(rect(*it).contains(at)){
            if(blade){if(!p->locked(it->track)&&bladed)bladed(it->id,timeAt(at.x()));return;}
            if(e->modifiers()&Qt::ControlModifier){if(it->id==selected){selected=extra.isEmpty()?QString():*extra.begin();extra.remove(selected);}else if(extra.contains(it->id))extra.remove(it->id);else{if(!selected.isEmpty())extra.insert(selected);selected=it->id;}if(selection)selection();update();return;}
            if(!isChosen(*it))selectOnly(it->id);else selected=it->id;
            if(p->locked(it->track)){if(selection)selection();update();return;}
            original=*it;originals.clear();anchor=at;mode=anchor.x()<rect(*it).left()+8?2:anchor.x()>rect(*it).right()-8?3:1;targetRow=it->track;
            for(auto &c:p->clips)if(mode==1?isChosen(c):linked(c,original))originals<<c;break;
        }
        if(!mode&&!blade){if(!(e->modifiers()&Qt::ControlModifier)){selected.clear();extra.clear();}mode=4;band=QRectF(at,at);anchor=at;}
        if(mode!=4||!(e->modifiers()&Qt::ControlModifier)){playhead=qBound(0.,timeAt(at.x()),qMax(0.,p->end()));if(seek)seek(playhead);}
        if(selection)selection();update();
    }
    void mouseMoveEvent(QMouseEvent *e)override{
        hover=e->position();int hx=visibleRegion().boundingRect().left();
        if(!(e->buttons()&Qt::LeftButton)){Qt::CursorShape shape=blade&&hover.y()>Top&&hover.x()>hx+Header?Qt::SplitHCursor:Qt::ArrowCursor;
            if(!blade)for(auto &c:p->clips){QRectF r=rect(c);if(r.contains(hover)){if(hover.x()<r.left()+8||hover.x()>r.right()-8)shape=Qt::SizeHorCursor;break;}}setCursor(shape);if(blade)update();return;}
        if(scrubbing){playhead=qBound(0.,timeAt(e->position().x()),qMax(0.,p->end()));if(seek)seek(playhead);update();return;}
        if(!mode)return;if(!started&&(e->position()-anchor).manhattanLength()<3)return;
        if(mode==4){started=true;band=QRectF(anchor,e->position());QRectF b=band.normalized();for(auto &c:p->clips)if(rect(c).intersects(b)){if(selected.isEmpty())selected=c.id;else if(c.id!=selected)extra.insert(c.id);}update();return;}
        if(!started){for(auto &v:originals)if(p->locked(v.track))return;if(before)before();started=true;}
        auto c=current();if(!c)return;double d=(e->position().x()-anchor.x())/zoom;
        if(mode==1){double next=qMax(0.,original.start+d);if(originals.size()<=2)target(e->position().y(),Project::type(original));
            if(snap){QList<double> targets{0,playhead};for(auto &m:p->markers)targets<<m.time;if(p->inPoint>=0)targets<<p->inPoint;if(p->outPoint>0)targets<<p->outPoint;for(auto &v:p->clips)if(!isChosen(v))targets<<v.start<<v.start+v.duration;for(double t:targets){if(std::abs(next-t)*zoom<8){next=t;break;}if(std::abs(next+original.duration-t)*zoom<8){next=qMax(0.,t-original.duration);break;}}}d=next-original.start;
            for(auto &o:originals)d=qMax(d,-o.start);for(auto &v:p->clips)for(auto &o:originals)if(v.id==o.id)v.start=o.start+d;
        }else if(mode==2){for(auto &o:originals)d=qBound(qMax(-o.start,o.kind=="video"||o.kind=="audio"?(o.reverse?-(o.source-o.in-o.duration*o.speed)/o.speed:-o.in/o.speed):-86400.),d,o.duration-.04);for(auto &v:p->clips)for(auto &o:originals)if(v.id==o.id)v=sliced(o,d,o.duration-d);}
        else if(mode==3){double delta=d;for(auto &o:originals){double maxDur=o.kind=="video"||o.kind=="audio"?(o.reverse?(o.in+o.duration*o.speed)/o.speed:(o.source-o.in)/o.speed):86400;delta=qBound(.04-o.duration,delta,qMax(.04,maxDur)-o.duration);}for(auto &v:p->clips)for(auto &o:originals)if(v.id==o.id){v=sliced(o,0,o.duration+delta);if(v.reverse)v.in=qMax(0.,o.in-delta*o.speed);}}
        else{double half=original.duration/2;for(auto &v:p->clips)for(auto &o:originals)if(v.id==o.id){if(mode==5)v.fadeIn=qBound(0.,o.fadeIn+d,half);else v.fadeOut=qBound(0.,o.fadeOut-d,half);}}
        if(liveChanged)liveChanged();update();
    }
    void mouseReleaseEvent(QMouseEvent*)override{scrubbing=false;if(mode==4){mode=0;started=false;if(selection)selection();update();return;}if(mode){commitDrop();mode=0;if(started&&changed)changed();started=false;targetRow=targetSlot=-1;resizeCanvas();}}
    void leaveEvent(QEvent*)override{hover={-1,-1};if(blade)update();}
    void wheelEvent(QWheelEvent *e)override{if(e->modifiers()&Qt::ControlModifier){if(zoomAt)zoomAt(std::pow(1.15,e->angleDelta().y()/120.),e->position().x());e->accept();}else e->ignore();}
    void dragEnterEvent(QDragEnterEvent *e)override{if(e->mimeData()->hasUrls())e->acceptProposedAction();}
    void dragMoveEvent(QDragMoveEvent *e)override{e->acceptProposedAction();}
    void dropEvent(QDropEvent *e)override{double at=timeAt(e->position().x());int row=qBound(0,int((e->position().y()-Top)/Row),p->tracks-1);for(auto u:e->mimeData()->urls())if(u.isLocalFile()&&dropped)dropped(u.toLocalFile(),at,row);e->acceptProposedAction();}
    // Used by interaction tests without involving the desktop or user's files.
    void testMoveToNewTrack(QString id,int slot){selectOnly(id);auto c=current();if(!c)return;original=*c;mode=1;started=true;targetSlot=slot;commitDrop();mode=0;started=false;}
};

class TimelineScroll:public QScrollArea {
    Timeline *timeline;
public:
    explicit TimelineScroll(Timeline *t):timeline(t){setWidget(t);setWidgetResizable(false);}
protected:
    void resizeEvent(QResizeEvent *e)override{QScrollArea::resizeEvent(e);timeline->viewportSize=viewport()->size();timeline->resizeCanvas();}
};
