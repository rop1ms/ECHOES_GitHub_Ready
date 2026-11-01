#include <QtWidgets>
#include "i18n.h"
// The UI walkthrough (--ui-test) drives real widgets through QTest.
#define QT_WIDGETS_LIB
#define QT_GUI_LIB
#include <QtTest/QTest>
#include <cmath>
#include <functional>
#include <cstdio>
#include <cstdarg>
#ifdef Q_OS_WIN
#include <windows.h>
#endif

// The complete editor runs in this C++ process. Python only hosts its native window.
static constexpr double kPi=3.141592653589793;
static QString num(double v) { return QString::number(v, 'f', 6); }
// DaVinci-style timecode HH:MM:SS:FF.
static QString timecode(double t,int fps) {
    qint64 frames=qMax<qint64>(0,qint64(std::floor(t*fps+.0001)));int f=int(frames%fps);qint64 s=frames/fps;
    return QString("%1:%2:%3:%4").arg(s/3600,2,10,QChar('0')).arg(s/60%60,2,10,QChar('0')).arg(s%60,2,10,QChar('0')).arg(f,2,10,QChar('0'));
}
#include "catalog.h"

struct Clip {
    QString id=QUuid::createUuid().toString(), path, name, text, kind="video", effect="none",group,easing="linear";
    QString fit="fit",blend="normal",look,inAnim,outAnim,trans,label;
    QJsonArray keys; QJsonObject grade,sound,style,mask;
    double start=0, in=0, duration=5, source=5, speed=1, gain=1, opacity=1, fadeIn=0, fadeOut=0, stabilize=0;
    int track=0, x=0, y=0, endx=0, endy=0, size=64, srcW=0, srcH=0;
    double scale=1, rotation=0,animationOffset=0,motionSpan=0,sourceFps=30;
    double cropL=0,cropT=0,cropR=0,cropB=0,lookAmount=1,inDur=.5,outDur=.5,transDur=.5;
    bool audio=true, motion=false, reverse=false;
    // Render-only state, never saved: still-frame time, animated body length, transition tail,
    // audio crossfade-in from the previous clip.
    double shift=0,span=0,tail=0,xfadeIn=0;QString tailAnim;
    QJsonObject json() const {
        return {{"id",id},{"path",path},{"name",name},{"text",text},{"kind",kind},{"effect",effect},{"group",group},{"keys",keys},{"easing",easing},
            {"start",start},{"in",in},{"duration",duration},{"source",source},{"speed",speed},
            {"gain",gain},{"opacity",opacity},{"fade",qMax(fadeIn,fadeOut)},{"fadeIn",fadeIn},{"fadeOut",fadeOut},{"track",track},{"x",x},{"y",y},{"endx",endx},{"endy",endy},{"scale",scale},{"rotation",rotation},{"motion",motion},{"animationOffset",animationOffset},{"motionSpan",motionSpan},{"sourceFps",sourceFps},{"size",size},{"audio",audio},
            {"fit",fit},{"blend",blend},{"look",look},{"lookAmount",lookAmount},{"inAnim",inAnim},{"inDur",inDur},{"outAnim",outAnim},{"outDur",outDur},{"trans",trans},{"transDur",transDur},{"label",label},
            {"grade",grade},{"sound",sound},{"style",style},{"cropL",cropL},{"cropT",cropT},{"cropR",cropR},{"cropB",cropB},{"reverse",reverse},{"srcW",srcW},{"srcH",srcH},{"mask",mask},{"stabilize",stabilize}};
    }
    static Clip read(QJsonObject o) {
        Clip c; c.id=o["id"].toString(c.id); c.path=o["path"].toString(); c.name=o["name"].toString();
        c.text=o["text"].toString(); c.kind=o["kind"].toString("video"); if(!QStringList{"video","audio","image","text","color","adjust"}.contains(c.kind))c.kind="video";
        c.effect=o["effect"].toString("none");c.group=o["group"].toString();
        c.easing=o["easing"].toString("linear");QList<QJsonObject> keys;
        for(auto k:o["keys"].toArray()){auto key=k.toObject();double t=key["time"].toDouble();if(!std::isfinite(t)||t<0)continue;QJsonObject v{{"time",t},{"x",qBound(-7680,qRound(key["x"].toDouble()),7680)},{"y",qBound(-7680,qRound(key["y"].toDouble()),7680)}};
            if(key.contains("scale"))v["scale"]=qBound(.05,key["scale"].toDouble(1),10.);if(key.contains("rotation"))v["rotation"]=qBound(-3600.,key["rotation"].toDouble(),3600.);
            if(key.contains("opacity"))v["opacity"]=qBound(0.,key["opacity"].toDouble(1),1.);if(key.contains("volume"))v["volume"]=qBound(0.,key["volume"].toDouble(1),4.);
            keys.append(v);if(keys.size()==256)break;}
        std::stable_sort(keys.begin(),keys.end(),[](auto a,auto b){return a["time"].toDouble()<b["time"].toDouble();});for(auto k:keys){if(!c.keys.isEmpty()&&std::abs(c.keys.last().toObject()["time"].toDouble()-k["time"].toDouble())<.001)c.keys.removeLast();c.keys.append(k);}
        c.start=qMax(0.,o["start"].toDouble()); c.in=qMax(0.,o["in"].toDouble());
        c.source=qBound(.04,o["source"].toDouble(5),86400.);
        c.speed=qBound(.1,o["speed"].toDouble(1),10.);
        c.duration=qBound(.04,o["duration"].toDouble(5),86400.);
        if(c.kind=="video"||c.kind=="audio") c.duration=qMax(.04,qMin(c.duration,(c.source-c.in)/c.speed));
        c.gain=qBound(0.,o["gain"].toDouble(1),4.); c.opacity=qBound(0.,o["opacity"].toDouble(1),1.);
        double legacy=o["fade"].toDouble();c.fadeIn=qBound(0.,o.contains("fadeIn")?o["fadeIn"].toDouble():legacy,c.duration/2);c.fadeOut=qBound(0.,o.contains("fadeOut")?o["fadeOut"].toDouble():legacy,c.duration/2);
        c.track=qBound(0,o["track"].toInt(),63);
        c.x=qBound(-7680,o["x"].toInt(),7680); c.y=qBound(-7680,o["y"].toInt(),7680);
        c.endx=qBound(-7680,o["endx"].toInt(),7680);c.endy=qBound(-7680,o["endy"].toInt(),7680);
        c.scale=qBound(.05,o["scale"].toDouble(1),10.);c.rotation=qBound(-360.,o["rotation"].toDouble(),360.);c.motion=o["motion"].toBool();
        c.animationOffset=qBound(-86400.,o["animationOffset"].toDouble(),86400.);c.motionSpan=qBound(0.,o["motionSpan"].toDouble(),86400.);
        c.sourceFps=qBound(1.,o["sourceFps"].toDouble(30),240.);
        c.size=qBound(8,o["size"].toInt(64),400); c.audio=o["audio"].toBool(true);
        c.fit=o["fit"].toString("fit");if(!QStringList{"fit","fill","stretch","blur"}.contains(c.fit))c.fit="fit";
        c.blend=o["blend"].toString("normal");bool knownBlend=false;for(auto &b:blendCatalog())knownBlend|=b.id==c.blend;if(!knownBlend)c.blend="normal";
        c.look=o["look"].toString();c.lookAmount=qBound(0.,o["lookAmount"].toDouble(1),1.);
        c.inAnim=o["inAnim"].toString();c.outAnim=o["outAnim"].toString();c.trans=o["trans"].toString();
        c.inDur=qBound(.05,o["inDur"].toDouble(.5),30.);c.outDur=qBound(.05,o["outDur"].toDouble(.5),30.);c.transDur=qBound(.05,o["transDur"].toDouble(.5),10.);
        c.label=o["label"].toString();if(!c.label.isEmpty()&&!QColor(c.label).isValid())c.label.clear();
        for(auto key:{"grade","sound","style","mask"}){const QJsonObject in=o[key].toObject();QJsonObject out;for(auto it=in.constBegin();it!=in.constEnd();++it){QJsonValue v=it.value();if(v.isDouble())out[it.key()]=qBound(-10000.,v.toDouble(),10000.);else if(v.isBool()||v.isString())out[it.key()]=v;}(key==QString("grade")?c.grade:key==QString("sound")?c.sound:key==QString("style")?c.style:c.mask)=out;}
        if(!QStringList{"ellipse","rect"}.contains(c.mask["shape"].toString()))c.mask={};
        c.stabilize=c.kind=="video"?qBound(0.,o["stabilize"].toDouble(),100.):0;
        c.cropL=qBound(0.,o["cropL"].toDouble(),.45);c.cropT=qBound(0.,o["cropT"].toDouble(),.45);c.cropR=qBound(0.,o["cropR"].toDouble(),.45);c.cropB=qBound(0.,o["cropB"].toDouble(),.45);
        c.reverse=o["reverse"].toBool()&&(c.kind=="video"||c.kind=="audio");c.srcW=qBound(0,o["srcW"].toInt(),16384);c.srcH=qBound(0,o["srcH"].toInt(),16384);
        return c;
    }
};
static double body(const Clip &c){return c.span>0?c.span:c.duration;}
static bool visual(const Clip &c){return c.kind!="audio";}

static double eased(double t,QString kind){t=qBound(0.,t,1.);return kind=="smooth"?t*t*(3-2*t):kind=="in"?t*t:kind=="out"?1-(1-t)*(1-t):t;}
static QString easeExpr(QString f,QString kind){return kind=="smooth"?"("+f+")*("+f+")*(3-2*("+f+"))":kind=="in"?"pow("+f+",2)":kind=="out"?"1-pow(1-("+f+"),2)":f;}

// Keyframe tracks. Each key snapshots the transform; a missing property uses the clip value.
using Points=QList<QPair<double,double>>;
static double staticValue(const Clip &c,QString prop){return prop=="x"?c.x:prop=="y"?c.y:prop=="scale"?c.scale:prop=="rotation"?c.rotation:prop=="opacity"?c.opacity:prop=="volume"?c.gain:0;}
static Points track(const Clip &c,QString prop){
    Points p;double fallback=staticValue(c,prop);
    if(c.keys.isEmpty()){if(c.motion&&(prop=="x"||prop=="y")){double span=c.motionSpan>0?c.motionSpan:c.duration;p<<qMakePair(-c.animationOffset,prop=="x"?double(c.x):double(c.y))<<qMakePair(span-c.animationOffset,prop=="x"?double(c.endx):double(c.endy));}return p;}
    for(auto v:c.keys){auto k=v.toObject();p<<qMakePair(k["time"].toDouble()-c.animationOffset,k.contains(prop)?k[prop].toDouble():fallback);}
    return p;
}
static bool varies(const Points &p){for(auto &v:p)if(std::abs(v.second-p.first().second)>1e-6)return true;return false;}
static double valueAt(const Clip &c,QString prop,double local){
    Points p=track(c,prop);if(p.isEmpty())return staticValue(c,prop);if(local<=p.first().first)return p.first().second;
    for(int i=1;i<p.size();i++)if(local<=p[i].first){double f=eased((local-p[i-1].first)/qMax(.001,p[i].first-p[i-1].first),c.easing);return p[i-1].second+(p[i].second-p[i-1].second)*f;}
    return p.last().second;
}
static QString valueExpr(const Clip &c,QString prop,QString L,double factor){
    Points p=track(c,prop);if(p.isEmpty()||!varies(p))return num((p.isEmpty()?staticValue(c,prop):p.first().second)*factor);
    QString e=num(p.last().second*factor);
    for(int i=p.size()-2;i>=0;i--){auto a=p[i],b=p[i+1];QString f=easeExpr("clip(("+L+"-"+num(a.first)+")/"+num(qMax(.001,b.first-a.first))+",0,1)",c.easing);e="if(lt("+L+","+num(b.first)+"),"+num(a.second*factor)+"+("+num((b.second-a.second)*factor)+")*("+f+"),"+e+")";}
    return e;
}
static bool animatedProp(const Clip &c,QString prop){return varies(track(c,prop));}
struct Pose { double x,y,scale,rotation,opacity,volume; };
static Pose poseAt(const Clip &c,double local){return {valueAt(c,"x",local),valueAt(c,"y",local),valueAt(c,"scale",local),valueAt(c,"rotation",local),valueAt(c,"opacity",local),valueAt(c,"volume",local)};}
// Kept for callers that only need the position path.
static QPointF positionAt(const Clip &c,double t){return {valueAt(c,"x",t),valueAt(c,"y",t)};}

// In/out animations and transitions as FFmpeg expressions of local time L.
struct Ramp { bool in; double st,d; };
struct Motion { QStringList x,y,scale,rotate; QList<Ramp> ramps; };
static Motion motionTerms(const Clip &c,QString L){
    Motion m;double D=body(c);
    auto apply=[&](QString type,QString e,QString f,bool in,double d){
        if(type=="fade"||type=="dissolve"||type=="dip"){m.ramps<<Ramp{in,in?0:D-d,d};return;}
        if(type=="slide_l"||type=="push_r")m.x<<"-W*(1-("+e+"))";else if(type=="slide_r"||type=="push_l")m.x<<"W*(1-("+e+"))";
        else if(type=="slide_u")m.y<<"H*(1-("+e+"))";else if(type=="slide_d")m.y<<"-H*(1-("+e+"))";
        else if(type=="zoom_in"){m.scale<<"(0.3+0.7*("+e+"))";m.ramps<<Ramp{in,in?0:D-d,d*.6};}
        else if(type=="zoom_out"){m.scale<<"(1.8-0.8*("+e+"))";m.ramps<<Ramp{in,in?0:D-d,d*.6};}
        else if(type=="spin"){m.scale<<"max(0.05,"+e+")";m.rotate<<"(-270*(1-("+e+")))";m.ramps<<Ramp{in,in?0:D-d,d*.5};}
        else if(type=="pop"){m.scale<<"if(lt("+f+",0.6),max(0.05,"+f+"/0.6*1.15),1.15-("+f+"-0.6)/0.4*0.15)";m.ramps<<Ramp{in,0,d*.3};}
    };
    auto easedIn=[&](double d){QString f="clip(("+L+")/"+num(d)+",0,1)";return qMakePair("(1-pow(1-"+f+",2))",f);};
    if(!c.trans.isEmpty()){double d=qMin(c.transDur,D);auto e=easedIn(d);apply(c.trans,e.first,e.second,true,d);}
    if(!c.inAnim.isEmpty()){double d=qMin(c.inDur,D);auto e=easedIn(d);apply(c.inAnim,e.first,e.second,true,d);}
    if(!c.outAnim.isEmpty()){double d=qMin(c.outDur,D);QString f="clip(("+num(D)+"-"+L+")/"+num(d)+",0,1)";apply(c.outAnim,"(1-pow(1-"+f+",2))",f,false,d);}
    if(c.tail>0){QString ft="clip(("+L+"-"+num(D)+")/"+num(c.tail)+",0,1)";
        if(c.tailAnim=="dip")m.ramps<<Ramp{false,D,c.tail};else if(c.tailAnim=="push_l")m.x<<"-W*("+ft+")";else if(c.tailAnim=="push_r")m.x<<"W*("+ft+")";}
    double fi=qMin(c.fadeIn,D/2),fo=qMin(c.fadeOut,D/2);if(fi>0)m.ramps<<Ramp{true,0,fi};if(fo>0)m.ramps<<Ramp{false,D-fo,fo};
    return m;
}
static double rampsAt(const QList<Ramp> &ramps,double t){double a=1;for(auto &r:ramps)a*=r.in?qBound(0.,(t-r.st)/qMax(.001,r.d),1.):qBound(0.,(r.st+r.d-t)/qMax(.001,r.d),1.);return a;}
static QString rampsExpr(const QList<Ramp> &ramps,QString L){QStringList f;for(auto &r:ramps)f<<(r.in?"clip(("+L+"-"+num(r.st)+")/"+num(qMax(.001,r.d))+",0,1)":"clip(("+num(r.st+r.d)+"-"+L+")/"+num(qMax(.001,r.d))+",0,1)");return f.isEmpty()?"1":f.join("*");}

static Clip sliced(const Clip &source,double offset,double duration){Clip c=source;c.start+=offset;c.in+=offset*c.speed;c.duration=duration;c.animationOffset+=offset;if(c.motion&&c.motionSpan==0)c.motionSpan=source.duration;
    if(offset>0)c.trans.clear(),c.inAnim.clear();if(offset+duration<source.duration-.001)c.outAnim.clear();
    if(c.reverse)c.in=source.in+(source.duration-offset-duration)*c.speed;return c;
}
struct Marker { double time=0;QString name,color="#e8a33d"; };
struct Project {
    struct Track {QString kind="video",name;bool muted=false,locked=false;QJsonObject json()const{return {{"kind",kind},{"name",name},{"muted",muted},{"locked",locked}};}};
    QList<Clip> clips; int width=1920,height=1080,fps=30,tracks=1;QList<Track> lanes{Track{}};
    QString background="#000000";QList<Marker> markers;double inPoint=-1,outPoint=-1;
    static QString type(const Clip &c){return c.kind=="audio"?"audio":c.kind=="text"?"text":"video";}
    void sync(){tracks=lanes.size();}
    QString trackName(int i)const{if(i<0||i>=lanes.size())return {};auto &t=lanes[i];if(!t.name.isEmpty())return t.name;int n=0;for(int j=0;j<=i;j++)if(lanes[j].kind==t.kind)n++;return QString(t.kind=="audio"?EchoesText("Звук %1"):t.kind=="text"?EchoesText("Титры %1"):EchoesText("Видео %1")).arg(n);}
    QString trackCode(int i)const{if(i<0||i>=lanes.size())return {};int n=0;for(int j=0;j<=i;j++)if(lanes[j].kind==lanes[i].kind)n++;return QString(lanes[i].kind=="audio"?"A%1":lanes[i].kind=="text"?"T%1":"V%1").arg(n);}
    bool muted(int i)const{return i>=0&&i<lanes.size()&&lanes[i].muted;}
    bool locked(int i)const{return i>=0&&i<lanes.size()&&lanes[i].locked;}
    int insertTrack(int at,QString kind){at=qBound(0,at,int(lanes.size()));for(auto &c:clips)if(c.track>=at)c.track++;lanes.insert(at,Track{kind,{},false,false});sync();return at;}
    int ensureTrack(QString kind){for(int i=0;i<lanes.size();i++)if(lanes[i].kind==kind&&!lanes[i].locked)return i;return insertTrack(kind=="text"?0:lanes.size(),kind);}
    void compact(){
        bool keepEmptyVideo=false;for(int i=lanes.size()-1;i>=0;i--){bool used=false;for(auto &c:clips)used|=c.track==i;if(!used&&lanes.size()>1){bool anyVideo=false;for(auto &c:clips)anyVideo|=type(c)=="video";if(lanes[i].kind=="video"&&!anyVideo&&!keepEmptyVideo){keepEmptyVideo=true;continue;}lanes.removeAt(i);for(auto &c:clips)if(c.track>i)c.track--;}}
        if(lanes.isEmpty())lanes.append(Track{});sync();
    }
    double end() const { double t=0; for(const auto &c:clips)t=qMax(t,c.start+c.duration);return t; }
    QJsonObject json() const { QJsonArray a,l,m;for(auto &c:clips)a.append(c.json());for(auto &t:lanes)l.append(t.json());for(auto &k:markers)m.append(QJsonObject{{"time",k.time},{"name",k.name},{"color",k.color}});
        return {{"format","echoes-montage"},{"version",2},{"width",width},{"height",height},{"fps",fps},{"tracks",tracks},{"lanes",l},{"clips",a},{"background",background},{"markers",m},{"in",inPoint},{"out",outPoint}}; }
    bool load(QJsonObject o) {
        if(o["format"]!="echoes-montage" || o["version"].toInt()<1 || o["version"].toInt()>2 || !o["clips"].isArray())return false;
        width=qBound(240,o["width"].toInt(1920),3840);height=qBound(240,o["height"].toInt(1080),3840);
        width=width/2*2;height=height/2*2;fps=qBound(12,o["fps"].toInt(30),60);
        background=QColor(o["background"].toString()).isValid()?QColor(o["background"].toString()).name():"#000000";
        markers.clear();for(auto v:o["markers"].toArray()){auto k=v.toObject();double t=k["time"].toDouble(-1);if(!std::isfinite(t)||t<0)continue;markers.append({t,k["name"].toString().left(120),QColor(k["color"].toString()).isValid()?k["color"].toString():"#e8a33d"});if(markers.size()==2000)break;}
        std::sort(markers.begin(),markers.end(),[](auto &a,auto &b){return a.time<b.time;});
        inPoint=o["in"].toDouble(-1);outPoint=o["out"].toDouble(-1);if(!std::isfinite(inPoint)||inPoint<0)inPoint=-1;if(!std::isfinite(outPoint)||outPoint<=0)outPoint=-1;
        clips.clear();lanes.clear();QSet<QString> ids;
        for(auto v:o["clips"].toArray()){Clip c=Clip::read(v.toObject());if(ids.contains(c.id))c.id=QUuid::createUuid().toString();ids.insert(c.id);clips.append(c);}
        for(auto v:o["lanes"].toArray()){auto l=v.toObject();QString k=l["kind"].toString("video");if(!QStringList{"video","audio","text"}.contains(k))k="video";lanes.append({k,l["name"].toString(),l["muted"].toBool(),l["locked"].toBool()});if(lanes.size()==64)break;}
        if(lanes.isEmpty()){
            // Migrate v1 mixed layers without changing visual stacking order.
            QMap<QString,int> map;QList<Clip*> order;for(auto &c:clips)order<<&c;std::stable_sort(order.begin(),order.end(),[](auto a,auto b){return a->track<b->track;});
            for(auto c:order){QString key=QString::number(c->track)+"/"+type(*c);if(!map.contains(key)){map[key]=lanes.size();lanes.append({type(*c),{},false,false});}c->track=map[key];}
        }else for(auto &c:clips){if(c.track>=lanes.size()){while(lanes.size()<=c.track)lanes.append(Track{});}if(lanes[c.track].kind!=type(c))c.track=ensureTrack(type(c));}
        if(lanes.isEmpty())lanes.append(Track{});sync();return true;
    }
};

// A transition at clip B extends the clip A that ends where B starts on the same track:
// A keeps playing (or holds its last frame) underneath while B animates in.
static Project prepared(const Project &source){
    Project p=source;
    for(auto &b:p.clips){if(b.trans.isEmpty()||!visual(b)||b.kind=="adjust")continue;
        for(auto &a:p.clips)if(a.id!=b.id&&a.track==b.track&&visual(a)&&a.kind!="adjust"&&a.tail==0&&std::abs(a.start+a.duration-b.start)<.02){double d=qMin(b.transDur,b.duration);a.span=a.duration;a.tail=d;a.tailAnim=b.trans;a.duration+=d;
            if(b.audio&&b.kind=="video")b.xfadeIn=d;break;}}
    // Sound crossfades: an audio clip with its own transition, or the sound linked to a video
    // transition, overlaps the previous audio clip on its track.
    QHash<QString,double> linkedTrans;for(auto &b:p.clips)if(visual(b)&&!b.trans.isEmpty()&&!b.group.isEmpty())linkedTrans[b.group]=b.transDur;
    for(auto &b:p.clips){if(b.kind!="audio")continue;double d=!b.trans.isEmpty()?b.transDur:linkedTrans.value(b.group,0);if(d<=0)continue;
        for(auto &a:p.clips)if(a.id!=b.id&&a.track==b.track&&a.kind=="audio"&&a.tail==0&&std::abs(a.start+a.duration-b.start)<.02){d=qMin(d,b.duration);a.span=a.duration;a.tail=d;a.tailAnim="crossfade";a.duration+=d;b.xfadeIn=d;break;}}
    return p;
}
// Only the part of the timeline between from and to, shifted to start at zero.
static Project rangeProject(const Project &source,double from,double to){
    Project p=source;p.clips.clear();p.markers.clear();p.inPoint=p.outPoint=-1;
    for(auto c:source.clips){double end=c.start+c.duration;if(end<=from+.001||c.start>=to-.001)continue;
        if(c.start<from){double cut=from-c.start;c=sliced(c,cut,c.duration-cut);}if(c.start+c.duration>to)c=sliced(c,0,to-c.start);c.start-=from;p.clips<<c;}
    return p;
}
static QString stabilizeFile(const Clip &c){
    QString key=c.path+"|"+QString::number(QFileInfo(c.path).lastModified().toMSecsSinceEpoch())+"|"+num(c.in)+"|"+num(body(c)*c.speed);
    return QStandardPaths::writableLocation(QStandardPaths::AppLocalDataLocation)+"/montage-stab/"+QCryptographicHash::hash(key.toUtf8(),QCryptographicHash::Md5).toHex()+".trf";
}
// FFmpeg filter arguments need "C\:/path" inside single quotes.
static QString filterPath(QString path){return "'"+QDir::fromNativeSeparators(path).replace(":","\\:")+"'";}
static QStringList stabilizeArgs(const Clip &c){
    QDir().mkpath(QFileInfo(stabilizeFile(c)).absolutePath());
    return {"-v","error","-y","-ss",num(c.in),"-t",num(body(c)*c.speed),"-i",c.path,"-vf","vidstabdetect=shakiness=6:accuracy=12:result="+filterPath(stabilizeFile(c)),"-f","null","-"};
}
static bool paintMask(const Clip &c,int w,int h,QString path){
    auto m=c.mask;bool invert=m["invert"].toBool();QImage img(w,h,QImage::Format_Grayscale8);img.fill(invert?Qt::white:Qt::black);QPainter p(&img);p.setRenderHint(QPainter::Antialiasing);
    p.translate(w/2.+m["x"].toDouble()/100*w,h/2.+m["y"].toDouble()/100*h);p.rotate(m["angle"].toDouble());double mw=qMax(1.,m["w"].toDouble(60)/100*w),mh=qMax(1.,m["h"].toDouble(60)/100*h);
    p.setPen(Qt::NoPen);p.setBrush(invert?Qt::black:Qt::white);QRectF r(-mw/2,-mh/2,mw,mh);
    if(m["shape"].toString()=="ellipse")p.drawEllipse(r);else{double rad=qMin(mw,mh)/2*qBound(0.,m["round"].toDouble(),100.)/100;p.drawRoundedRect(r,rad,rad);}
    p.end();return img.save(path);
}

static void paintText(QPainter &painter,QRectF area,const Clip &c,double factor){
    auto st=c.style;QFont f(st["font"].toString("Segoe UI"));f.setPixelSize(qMax(6,int(c.size*factor)));f.setBold(st["bold"].toBool(true));f.setItalic(st["italic"].toBool());
    if(st["spacing"].toDouble())f.setLetterSpacing(QFont::PercentageSpacing,100+st["spacing"].toDouble());
    QFontMetricsF fm(f);QString text=st["upper"].toBool()?c.text.toUpper():c.text;double maxw=area.width()-60*factor;QStringList lines;
    for(auto para:text.split('\n')){QString line;for(auto word:para.split(' ')){QString next=line.isEmpty()?word:line+" "+word;if(!line.isEmpty()&&fm.horizontalAdvance(next)>maxw){lines<<line;line=word;}else line=next;}lines<<line;}
    double lh=fm.lineSpacing(),total=lh*lines.size(),top=area.center().y()-total/2;QString align=st["align"].toString("center");QPainterPath path;double minx=1e9,maxx=-1e9;
    for(int i=0;i<lines.size();i++){double lw=fm.horizontalAdvance(lines[i]);double x=align=="left"?area.left()+40*factor:align=="right"?area.right()-40*factor-lw:area.center().x()-lw/2;path.addText(x,top+i*lh+fm.ascent(),f,lines[i]);minx=qMin(minx,x);maxx=qMax(maxx,x+lw);}
    painter.setRenderHint(QPainter::Antialiasing);
    if(st["box"].toBool()){QColor b(st["boxColor"].toString("#000000"));b.setAlphaF(qBound(0.,st["boxOpacity"].toDouble(60)/100,1.));double pad=18*factor;painter.setPen(Qt::NoPen);painter.setBrush(b);painter.drawRoundedRect(QRectF(minx-pad,top-pad*.6,maxx-minx+pad*2,total+pad*1.2),10*factor,10*factor);}
    QColor color(st["color"].toString("#ffffff"));if(!color.isValid())color=Qt::white;
    if(st["shadow"].toBool()){painter.save();painter.translate(5*factor,6*factor);painter.fillPath(path,QColor(0,0,0,150));painter.restore();}
    if(st["glow"].toBool())for(double wv:{22.,13.,6.}){QColor g=color;g.setAlpha(55);painter.strokePath(path,QPen(g,wv*factor,Qt::SolidLine,Qt::RoundCap,Qt::RoundJoin));}
    double stroke=st["stroke"].toDouble();if(stroke>0){QColor s(st["strokeColor"].toString("#000000"));painter.strokePath(path,QPen(s.isValid()?s:QColor(Qt::black),stroke*2*factor,Qt::SolidLine,Qt::RoundCap,Qt::RoundJoin));}
    painter.fillPath(path,color);
}
// Titles and color layers are painted once per render into canvas-sized PNG inputs.
static bool paintCard(const Clip &c,int w,int h,double factor,QString path){
    QImage img(w,h,QImage::Format_ARGB32_Premultiplied);img.fill(Qt::transparent);QPainter painter(&img);
    if(c.kind=="color"){QColor a(c.style["color"].toString("#1f6feb")),b(c.style["color2"].toString());if(!a.isValid())a=QColor("#1f6feb");
        if(b.isValid()){double ang=c.style["angle"].toDouble(90)*kPi/180;QPointF mid(w/2.,h/2.),dir(std::cos(ang)*w/2,std::sin(ang)*h/2);QLinearGradient g(mid-dir,mid+dir);g.setColorAt(0,a);g.setColorAt(1,b);painter.fillRect(img.rect(),g);}else painter.fillRect(img.rect(),a);}
    else paintText(painter,QRectF(img.rect()),c,factor);
    painter.end();return img.save(path);
}

static QStringList gradeFilters(const QJsonObject &g){
    QStringList f;auto v=[&](QString k){return g[k].toDouble();};
    if(v("exposure"))f<<"exposure=exposure="+num(v("exposure")/50);
    QStringList eq;if(v("brightness"))eq<<"brightness="+num(v("brightness")/250);if(v("contrast"))eq<<"contrast="+num(qMax(0.,1+v("contrast")/100));
    if(v("saturation"))eq<<"saturation="+num(qMax(0.,1+v("saturation")/100));if(v("gamma"))eq<<"gamma="+num(std::pow(2.,v("gamma")/100));if(!eq.isEmpty())f<<"eq="+eq.join(":");
    if(v("temperature"))f<<"colortemperature=temperature="+num(qBound(1000.,6500-v("temperature")*40,40000.));
    if(v("tint")){QString t=num(-v("tint")/300);f<<"colorbalance=gs="+t+":gm="+t+":gh="+t;}
    if(v("hue"))f<<"hue=h="+num(v("hue")*1.8);if(v("vibrance"))f<<"vibrance=intensity="+num(v("vibrance")/100);
    if(v("shadows")||v("highlights")||v("fade"))f<<QString("curves=all='0/%1 0.25/%2 0.75/%3 1/1'").arg(num(qBound(0.,v("fade")/250,.4)),num(qBound(.02,.25+v("shadows")/400,.48)),num(qBound(.52,.75+v("highlights")/400,.98)));
    // DaVinci-style curves: "x/y x/y …" per channel, sanitised before reaching FFmpeg.
    QStringList curves;for(auto ch:QList<QPair<QString,QString>>{{"curveM","master"},{"curveR","red"},{"curveG","green"},{"curveB","blue"}}){QString pts=g[ch.first].toString();pts.remove(QRegularExpression("[^0-9./ ]"));if(pts.split(' ',Qt::SkipEmptyParts).size()>=2&&pts.simplified()!="0/0 1/1")curves<<ch.second+"='"+pts.simplified()+"'";}
    if(!curves.isEmpty())f<<"curves="+curves.join(":");
    if(v("sharpen")>0)f<<"unsharp=5:5:"+num(v("sharpen")/40);if(v("blur")>0)f<<"gblur=sigma="+num(v("blur")/5);
    if(v("vignette")>0)f<<"vignette=angle="+num(v("vignette")/100*1.3);if(v("grain")>0)f<<"noise=alls="+num(v("grain")/3)+":allf=t";
    return f;
}
static QStringList soundFilters(const Clip &c){
    QStringList f;auto s=c.sound;
    if(s["pitch"].toDouble())f<<"rubberband=pitch="+num(std::pow(2.,qBound(-24.,s["pitch"].toDouble(),24.)/12));
    QString voice=presetFilter(voiceCatalog(),s["voice"].toString());if(!voice.isEmpty())f<<voice;
    if(s["denoise"].toBool())f<<"afftdn=nf=-25";if(s["clarity"].toBool())f<<"highpass=f=80,acompressor=threshold=0.1:ratio=3:attack=5:release=80,equalizer=f=3000:t=q:w=1:g=3";
    if(s["bass"].toBool())f<<"bass=g=8";if(s["low"].toDouble())f<<"bass=g="+num(s["low"].toDouble());if(s["mid"].toDouble())f<<"equalizer=f=1000:t=q:w=1:g="+num(s["mid"].toDouble());if(s["high"].toDouble())f<<"treble=g="+num(s["high"].toDouble());
    if(s["normalize"].toBool())f<<"dynaudnorm=f=200";
    return f;
}

#include "timeline.h"

// Resolve VLC dynamically: no Python frame processing and no dependency on VLC headers.
class Vlc {
    QLibrary lib; void *instance=nullptr,*player=nullptr;
    template<class F> F fn(const char *n){return reinterpret_cast<F>(lib.resolve(n));}
public:
    static void log(void*,int level,const void*,const char *format,va_list args){if(level<3)return;std::vfprintf(stderr,format,args);std::fputc('\n',stderr);}
    bool open(const QString &dir, WId hwnd){
#ifdef Q_OS_WIN
        SetDllDirectoryW(reinterpret_cast<LPCWSTR>(dir.utf16()));
#endif
        lib.setFileName(dir+"/libvlc.dll");if(!lib.load())return false;
        const char *required[]={"libvlc_new","libvlc_media_player_new","libvlc_media_player_set_hwnd","libvlc_media_new_path","libvlc_media_player_set_media","libvlc_media_release","libvlc_media_player_play","libvlc_media_player_set_pause","libvlc_media_player_stop","libvlc_media_player_get_time","libvlc_media_player_set_time","libvlc_media_player_release","libvlc_release"};
        for(auto s:required)if(!lib.resolve(s))return false;
        QByteArray plugin=(dir+"/plugins").toUtf8();qputenv("VLC_PLUGIN_PATH",plugin);
#ifdef Q_OS_WIN
        // VLC's MinGW build uses MSVCRT, Qt uses UCRT. Each CRT has its own getenv cache.
        if(auto crt=GetModuleHandleW(L"msvcrt.dll")){auto put=reinterpret_cast<int(*)(const char*)>(reinterpret_cast<void*>(GetProcAddress(crt,"_putenv")));if(put)put((QByteArray("VLC_PLUGIN_PATH=")+plugin).constData());}
#endif
        const char *args[]={"--no-video-title-show","--no-snapshot-preview","--no-video-deco","--avcodec-hw=none","--vout=wingdi","--ignore-config","--no-avcodec-dr"};
        instance=fn<void*(*)(int,const char*const*)>("libvlc_new")(7,args);if(!instance)return false;
        if(auto setLog=fn<void(*)(void*,void(*)(void*,int,const void*,const char*,va_list),void*)>("libvlc_log_set"))setLog(instance,&Vlc::log,nullptr);
        player=fn<void*(*)(void*)>("libvlc_media_player_new")(instance);if(!player)return false;
        fn<void(*)(void*,void*)>("libvlc_media_player_set_hwnd")(player,reinterpret_cast<void*>(hwnd));return true;
    }
    bool ready()const{return player;}
    void play(const QString &path){if(!player)return;QByteArray nativePath=QDir::toNativeSeparators(path).toUtf8();auto m=fn<void*(*)(void*,const char*)>("libvlc_media_new_path")(instance,nativePath.constData());if(!m){std::fprintf(stderr,"VLC could not create media: %s\n",nativePath.constData());return;}fn<void(*)(void*,void*)>("libvlc_media_player_set_media")(player,m);fn<void(*)(void*)>("libvlc_media_release")(m);int result=fn<int(*)(void*)>("libvlc_media_player_play")(player);if(result<0)std::fprintf(stderr,"VLC playback failed: %s\n",path.toUtf8().constData());}
    void pause(bool b){if(player)fn<void(*)(void*,int)>("libvlc_media_player_set_pause")(player,b);}
    void stop(){if(player)fn<void(*)(void*)>("libvlc_media_player_stop")(player);}
    double time(){return player?fn<qint64(*)(void*)>("libvlc_media_player_get_time")(player)/1000.:0;}
    int state(){auto f=fn<int(*)(void*)>("libvlc_media_player_get_state");return player&&f?f(player):0;}
    bool snapshot(QString path){auto f=fn<int(*)(void*,unsigned,const char*,unsigned,unsigned)>("libvlc_video_take_snapshot");QByteArray file=QDir::toNativeSeparators(path).toUtf8();return player&&f&&f(player,0,file.constData(),0,0)==0;}
    void mute(bool on){auto f=fn<void(*)(void*,int)>("libvlc_audio_set_mute");if(player&&f)f(player,on);}
    void seek(double t){if(player)fn<void(*)(void*,qint64)>("libvlc_media_player_set_time")(player,qint64(t*1000));}
    ~Vlc(){if(player){stop();fn<void(*)(void*)>("libvlc_media_player_release")(player);}if(instance)fn<void(*)(void*)>("libvlc_release")(instance);}
};

struct ExportOptions { QString container="mp4",codec="h264";int crf=18;QString speed="medium";int height=0,fps=0,audio=192;double from=-1,to=-1;bool loud=false;QString still;int proxyWidth=640; };
struct RenderPlan { QStringList args; QString graph,error; double duration=0; };
// A linear FFmpeg chain that can splice in multi-pad graphs ("{in}"/"{out}"/"{n}").
struct Chain {
    QStringList *graph;QString label;int *seq;QStringList pending;
    void add(QString f){if(!f.isEmpty())pending<<f;}
    QString flush(){if(pending.isEmpty())return label;QString out="c"+QString::number((*seq)++);*graph<<"["+label+"]"+pending.join(",")+"["+out+"]";pending.clear();label=out;return label;}
    void filter(QString f){if(f.contains("{in}")){flush();QString n="x"+QString::number((*seq)++),out=n+"o";*graph<<QString(f).replace("{in}",label).replace("{out}",out).replace("{n}",n);label=out;}else add(f);}
};
static RenderPlan plan(const Project &source,const QString &work,const QString &output,bool proxy,bool stillFrame=false,const ExportOptions &opt={}){
    RenderPlan r;Project p=prepared(source);if(p.clips.isEmpty()||p.end()<=0){r.error=EchoesText("Проект пуст");return r;}
    bool audioOnly=opt.container=="mp3"||opt.container=="wav",gif=opt.container=="gif";
    int w=p.width,h=p.height;if(proxy){int pw=qMax(160,qMin(opt.proxyWidth,qMax(p.width,p.height)))/2*2;if(p.height>p.width){h=pw;w=qMax(2,int(double(pw)*p.width/p.height)/2*2);}else{w=pw;h=qMax(2,int(double(pw)*p.height/p.width)/2*2);}}else if(opt.height>0&&!stillFrame){h=opt.height/2*2;w=qMax(2,int(double(h)*p.width/p.height)/2*2);}
    int fps=opt.fps>0?opt.fps:p.fps;double factor=double(w)/p.width,end=p.end();
    double from=opt.from>=0&&opt.to>opt.from?qMin(opt.from,end):0,to=opt.from>=0&&opt.to>opt.from?qMin(opt.to,end):end;r.duration=qMax(.04,to-from);
    QStringList graph;int input=0,seq=0;
    if(!audioOnly)graph<<QString("color=c=0x%1:s=%2x%3:r=%4:d=%5,format=rgba[base]").arg(QColor(p.background).name().mid(1)).arg(w).arg(h).arg(fps).arg(num(end));
    QString video="base";QStringList audios;
    r.args={"-hide_banner","-y","-filter_complex_threads","2"};
    auto mixLabel=[&]{return "mix"+QString::number(seq++);};
    auto window=[&](const Clip &c){return QString("between(t,%1,%2)").arg(num(c.start),num(c.start+c.duration-.0001));};
    auto look=[&](Chain &ch,const Clip &c){
        for(auto f:gradeFilters(c.grade))ch.add(f);
        QString lf=presetFilter(lookCatalog(),c.look);
        if(!lf.isEmpty()){if(c.lookAmount>=.999)ch.add(lf);else ch.filter("[{in}]split[{n}a][{n}b];[{n}b]"+lf+"[{n}c];[{n}a][{n}c]blend=all_mode=normal:all_opacity="+num(c.lookAmount)+"[{out}]");}
        for(auto e:c.effect.split(','))ch.filter(presetFilter(effectCatalog(),e));
    };
    // Last layer in the list is composited last; track 0 is the top layer, later clips cover earlier ones.
    QList<Clip> ordered=p.clips;std::stable_sort(ordered.begin(),ordered.end(),[](const Clip&a,const Clip&b){return a.track!=b.track?a.track>b.track:a.start<b.start;});
    for(auto c:ordered){
        if(p.muted(c.track))continue;
        if(c.kind=="adjust"){
            if(audioOnly)continue;QString n=QString::number(seq++);graph<<"["+video+"]split[ak"+n+"][am"+n+"]";
            Chain ch{&graph,"am"+n,&seq,{}};ch.add("format=rgba");look(ch,c);ch.add("format=rgba");
            Motion m=motionTerms(c,"(t-"+num(c.start)+"+"+num(c.shift)+")");double op=stillFrame?valueAt(c,"opacity",c.shift)*rampsAt(m.ramps,c.shift):valueAt(c,"opacity",0);
            if(stillFrame||m.ramps.isEmpty()){if(op<.999)ch.add("colorchannelmixer=aa="+num(op));}
            else ch.add("geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='alpha(X,Y)*"+num(op)+"*"+rampsExpr(m.ramps,"(T-"+num(c.start)+")")+"'");
            QString next=mixLabel();graph<<"[ak"+n+"]["+ch.flush()+"]overlay=0:0:enable='"+window(c)+"'["+next+"]";video=next;continue;
        }
        bool wantsVideo=visual(c)&&!audioOnly,wantsAudio=!stillFrame&&!gif&&(c.kind=="audio"||c.kind=="video")&&c.audio;
        if(!wantsVideo&&!wantsAudio)continue;
        QString path=c.path;
        if(c.kind=="text"||c.kind=="color"){path=work+"/card_"+QString::number(input)+".png";if(!paintCard(c,w,h,factor,path)){r.error=EchoesText("Не удалось подготовить титры");return r;}}
        if(!QFileInfo::exists(path)){r.error=EchoesText("Файл не найден: ")+path;return r;}
        bool still=c.kind=="image"||c.kind=="text"||c.kind=="color";double D=body(c);
        if(still)r.args<<"-loop"<<"1"<<"-framerate"<<QString::number(fps);
        r.args<<"-i"<<path;
        QString id=QString::number(input++);
        if(wantsVideo){
            QString L="(t+"+num(c.shift)+")";Motion m=motionTerms(c,L);
            bool scaleAnim=animatedProp(c,"scale")||!m.scale.isEmpty(),rotAnim=animatedProp(c,"rotation")||!m.rotate.isEmpty(),opAnim=animatedProp(c,"opacity");
            Pose pose=poseAt(c,c.shift);double s0=scaleAnim?1:pose.scale;int sw=qMax(2,int(w*s0)/2*2),sh=qMax(2,int(h*s0)/2*2);
            Chain ch{&graph,id+":v",&seq,{}};
            if(still)ch.add("trim=duration="+num(c.duration)),ch.add("setpts=PTS-STARTPTS");
            else{bool stab=c.stabilize>0&&!stillFrame&&QFileInfo::exists(stabilizeFile(c));
                // vidstab transforms are indexed by frame, so the stream must match the analysed segment exactly.
                ch.add("trim=start="+num(c.in)+":duration="+num((c.reverse||stab?D:c.duration)*c.speed));
                if(stab)ch.add("setpts=PTS-STARTPTS"),ch.add("vidstabtransform=input="+filterPath(stabilizeFile(c))+":smoothing="+QString::number(qRound(5+c.stabilize/2))+":optzoom=1:zoomspeed=0.2:interpol=bilinear");
                if(c.reverse)ch.add("reverse");ch.add("setpts=(PTS-STARTPTS)/"+num(c.speed));
                if(c.tail>0&&!stillFrame)ch.add("tpad=stop_mode=clone:stop_duration="+num(c.tail+.2)),ch.add("trim=duration="+num(c.duration));}
            ch.add("fps="+QString::number(fps));
            if(c.cropL+c.cropR+c.cropT+c.cropB>0)ch.add(QString("crop=w='trunc(iw*%1/2)*2':h='trunc(ih*%2/2)*2':x='iw*%3':y='ih*%4'").arg(num(1-c.cropL-c.cropR),num(1-c.cropT-c.cropB),num(c.cropL),num(c.cropT)));
            QString fit=c.kind=="text"||c.kind=="color"?"stretch":c.fit;
            if(fit=="blur"){
                // Blurred, cover-scaled copy of the same frames fills the canvas behind the clip.
                QString n=QString::number(seq++);graph<<"["+ch.flush()+"]split[bf"+n+"][bb"+n+"]";ch.label="bf"+n;
                QString bg=QString("[bb%1]scale=%2:%3:force_original_aspect_ratio=increase,crop=%2:%3,gblur=sigma=%4,format=rgba").arg(n).arg(w).arg(h).arg(num(qMax(4.,30*factor)));
                double op=stillFrame?rampsAt(m.ramps,c.shift):1;if(op<.999)bg+=",colorchannelmixer=aa="+num(op);
                if(!stillFrame)for(auto &rp:m.ramps)bg+=QString(",fade=t=%1:st=%2:d=%3:alpha=1").arg(rp.in?"in":"out",num(rp.st),num(rp.d));
                graph<<bg+",setpts=PTS+"+num(c.start)+"/TB[bg"+n+"]";QString next=mixLabel();graph<<QString("[%1][bg%2]overlay=0:0:eof_action=pass:enable='%3'[%4]").arg(video,n,window(c),next);video=next;fit="fit";
            }
            if(fit=="fill")ch.add(QString("scale=%1:%2:force_original_aspect_ratio=increase,crop=%1:%2").arg(sw).arg(sh));
            else if(fit=="stretch")ch.add(QString("scale=%1:%2").arg(sw).arg(sh));
            else ch.add(QString("scale=%1:%2:force_original_aspect_ratio=decrease").arg(sw).arg(sh));
            ch.add("format=rgba");
            QString key=c.grade["key"].toString();if(QColor(key).isValid())ch.add(QString("chromakey=0x%1:%2:%3,format=rgba").arg(QColor(key).name().mid(1),num(qBound(.005,c.grade["keySim"].toDouble(30)/250,.5)),num(qBound(0.,c.grade["keyBlend"].toDouble(10)/500,.5))));
            look(ch,c);
            ch.add("format=rgba");if(fit=="fit")ch.add(QString("pad=%1:%2:(ow-iw)/2:(oh-ih)/2:color=black@0").arg(sw).arg(sh));ch.add("setsar=1");
            if(!c.mask["shape"].toString().isEmpty()){
                // A painted greyscale mask multiplies the layer's own alpha, keeping padding transparent.
                QString mpath=work+"/mask_"+id+".png";if(!paintMask(c,sw,sh,mpath)){r.error=EchoesText("Не удалось подготовить маску");return r;}
                r.args<<"-loop"<<"1"<<"-framerate"<<QString::number(fps)<<"-i"<<mpath;QString mid=QString::number(input++);double feather=c.mask["feather"].toDouble()/100*qMin(sw,sh)/6;
                graph<<"["+mid+":v]format=gray,"+(feather>.3?"gblur=sigma="+num(feather)+",":QString())+"trim=duration="+num(c.duration)+",setpts=PTS-STARTPTS[mk"+mid+"]";
                ch.filter("[{in}]split[{n}a][{n}b];[{n}b]alphaextract[{n}c];[{n}c][mk"+mid+"]blend=all_mode=multiply[{n}d];[{n}a][{n}d]alphamerge[{out}]");
            }
            if(rotAnim){QStringList rot{valueExpr(c,"rotation",L,1)};rot<<m.rotate;ch.add("rotate=a='(("+rot.join(")+(")+"))*PI/180':ow='hypot(iw,ih)':oh='hypot(iw,ih)':c=none");}
            else if(pose.rotation!=0){QString angle=num(pose.rotation*kPi/180);ch.add("rotate="+angle+":ow=rotw("+angle+"):oh=roth("+angle+"):c=none");}
            if(stillFrame){double op=pose.opacity*rampsAt(m.ramps,c.shift);if(op<.999)ch.add("colorchannelmixer=aa="+num(op));}
            else if(opAnim)ch.add("geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='alpha(X,Y)*("+valueExpr(c,"opacity","T",1)+")*"+rampsExpr(m.ramps,"T")+"'");
            else{if(pose.opacity<.999)ch.add("colorchannelmixer=aa="+num(pose.opacity));for(auto &rp:m.ramps)ch.add(QString("fade=t=%1:st=%2:d=%3:alpha=1").arg(rp.in?"in":"out",num(rp.st),num(rp.d)));}
            if(scaleAnim){QStringList sc{valueExpr(c,"scale",L,1)};sc<<m.scale;QString S="("+sc.join(")*(")+")";ch.add("scale=w='max(2,trunc(iw*"+S+"/2)*2)':h='max(2,trunc(ih*"+S+"/2)*2)':eval=frame");}
            ch.add("setpts=PTS+"+num(c.start)+"/TB");QString layer=ch.flush();
            QString G="(t-"+num(c.start)+"+"+num(c.shift)+")";Motion om=motionTerms(c,G);
            QStringList mx{valueExpr(c,"x",G,factor)},my{valueExpr(c,"y",G,factor)};mx<<om.x;my<<om.y;
            // FFmpeg's parser rejects "+-W", so every term is parenthesised.
            auto sum=[](QStringList terms){for(auto &t:terms)t="("+t+")";return terms.join("+");};
            QString place=QString("x='(W-w)/2+%1':y='(H-h)/2+%2'").arg(sum(mx),sum(my));
            QString neutral;for(auto &b:blendCatalog())if(b.id==c.blend)neutral=b.glyph;
            QString next=mixLabel();
            if(neutral.isEmpty())graph<<QString("[%1][%2]overlay=%3:eof_action=pass:enable='%4'[%5]").arg(video,layer,place,window(c),next);
            else{QString n=QString::number(seq++);graph<<QString("color=c=0x%1:s=%2x%3:r=%4:d=%5,format=rgba[nb%6]").arg(neutral.mid(1)).arg(w).arg(h).arg(fps).arg(num(end)).arg(n);
                graph<<QString("[nb%1][%2]overlay=%3:eof_action=pass[nl%1]").arg(n,layer,place);
                graph<<QString("[%1][nl%2]blend=all_mode=%3:c3_mode=normal:enable='%4',format=rgba[%5]").arg(video,n,c.blend,window(c),next);}
            video=next;
        }
        if(wantsAudio){
            // A crossfade tail keeps the sound playing under the next clip while it fades out.
            double AD=c.reverse?D:D+c.tail;QString a="a"+id;QString chain=QString("[%1:a]atrim=start=%2:duration=%3,asetpts=PTS-STARTPTS").arg(id,num(c.in),num(AD*c.speed));
            if(c.reverse)chain+=",areverse";
            double speed=c.speed;while(speed>2){chain+=",atempo=2";speed/=2;}while(speed<.5){chain+=",atempo=0.5";speed/=.5;}chain+=",atempo="+num(speed);
            for(auto f:soundFilters(c))chain+=","+f;
            chain+=animatedProp(c,"volume")?",volume='"+valueExpr(c,"volume","t",1)+"':eval=frame":",volume="+num(c.gain);
            double fi=qMin(c.fadeIn,D/2),fo=qMin(c.fadeOut,D/2);if(fi>0)chain+=",afade=t=in:d="+num(fi);if(fo>0)chain+=",afade=t=out:st="+num(D-fo)+":d="+num(fo);
            if(c.xfadeIn>0)chain+=",afade=t=in:d="+num(c.xfadeIn);if(AD>D)chain+=",afade=t=out:st="+num(D)+":d="+num(AD-D);
            chain+=",adelay="+QString::number(qRound64(c.start*1000))+":all=1["+a+"]";graph<<chain;audios<<"["+a+"]";
        }
    }
    bool ranged=from>0||to<end-.001;
    if(!audioOnly){
        QString tail=ranged?QString("trim=start=%1:end=%2,setpts=PTS-STARTPTS,").arg(num(from),num(to)):"";
        if(gif)graph<<"["+video+"]"+tail+"split[g1][g2];[g1]palettegen=stats_mode=diff[gp];[g2][gp]paletteuse=dither=bayer:bayer_scale=4[vout]";
        else graph<<"["+video+"]"+tail+"format="+(opt.codec=="prores"?"yuv422p10le":"yuv420p")+"[vout]";
    }
    if(!audios.isEmpty()&&!gif){QString chain=audios.join("")+QString("amix=inputs=%1:duration=longest:normalize=0,alimiter=limit=0.95:level=0,apad,atrim=duration=%2").arg(audios.size()).arg(num(end));
        if(opt.loud)chain+=",loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000";if(ranged)chain+=QString(",atrim=start=%1:end=%2,asetpts=PTS-STARTPTS").arg(num(from),num(to));graph<<chain+"[aout]";}
    if(audioOnly&&audios.isEmpty()){r.error=EchoesText("В проекте нет звука для экспорта");return r;}
    r.graph=graph.join(";\n");QString script=work+"/filters.txt";QFile file(script);if(!file.open(QIODevice::WriteOnly)){r.error=file.errorString();return r;}file.write(r.graph.toUtf8());file.close();
    r.args<<"-/filter_complex"<<script;
    if(!audioOnly)r.args<<"-map"<<"[vout]";
    if(stillFrame){r.args<<"-frames:v"<<"1"<<"-c:v"<<"png"<<"-threads"<<"1";if(opt.still.isEmpty())r.args<<"-f"<<"image2pipe"<<"pipe:1";else r.args<<"-update"<<"1"<<opt.still;return r;}
    if(!audios.isEmpty()&&!gif){r.args<<"-map"<<"[aout]";
        if(opt.container=="mp3")r.args<<"-c:a"<<"libmp3lame"<<"-b:a"<<QString::number(qMax(64,opt.audio))+"k";else if(opt.container=="wav"||opt.codec=="prores")r.args<<"-c:a"<<"pcm_s16le";
        else if(opt.container=="webm")r.args<<"-c:a"<<"libopus"<<"-b:a"<<QString::number(qMax(64,opt.audio))+"k";else r.args<<"-c:a"<<"aac"<<"-b:a"<<QString::number(qMax(64,opt.audio))+"k";}
    if(proxy)r.args<<"-c:v"<<"libx264"<<"-preset"<<"ultrafast"<<"-crf"<<"25"<<"-threads"<<"4";
    else if(gif)r.args<<"-loop"<<"0";
    else if(!audioOnly){QString crf=QString::number(qBound(0,opt.crf,51));
        if(opt.codec=="h265")r.args<<"-c:v"<<"libx265"<<"-preset"<<opt.speed<<"-crf"<<crf<<"-tag:v"<<"hvc1";
        else if(opt.codec=="prores")r.args<<"-c:v"<<"prores_ks"<<"-profile:v"<<"3";
        else if(opt.codec=="vp9")r.args<<"-c:v"<<"libvpx-vp9"<<"-crf"<<crf<<"-b:v"<<"0"<<"-row-mt"<<"1"<<"-deadline"<<"good"<<"-cpu-used"<<"4";
        else r.args<<"-c:v"<<"libx264"<<"-preset"<<opt.speed<<"-crf"<<crf<<"-threads"<<"4";}
    r.args<<"-t"<<num(r.duration);if(opt.container=="mp4"||opt.container=="mov")r.args<<"-movflags"<<"+faststart";
    r.args<<"-progress"<<"pipe:1"<<output;
    return r;
}

// A frame at the cursor decodes only the active sources, seeking directly to their
// source positions. It never waits for the full playback proxy to be encoded.
static Project frameProject(const Project &source,double time){
    Project full=prepared(source),p=full;p.clips.clear();
    for(auto c:full.clips){if(c.kind=="audio"||source.muted(c.track)||time<c.start||time>=c.start+c.duration)continue;
        double local=time-c.start;c.span=body(c);c.shift=local;
        if(c.kind=="video"){double src=c.reverse?c.in+(c.span-qMin(local,c.span))*c.speed:c.in+qMin(local,c.span)*c.speed;c.in=std::floor(qBound(0.,src,c.source-.04)*c.sourceFps+.000001)/c.sourceFps;c.reverse=false;}
        c.start=0;c.duration=.1;c.audio=false;p.clips<<c;
    }
    return p;
}

#include "editor.h"

int main(int argc,char **argv){
    QApplication app(argc,argv);QCoreApplication::setOrganizationName("ECHOES");QCoreApplication::setApplicationName("Montage");app.setStyle("Fusion");
    QCommandLineParser parser;parser.addHelpOption();parser.addOptions({{"ffmpeg","FFmpeg executable","path"},{"ffprobe","FFprobe executable","path"},{"vlc","VLC folder","path"},{"library","ECHOES library JSON","path"},{"embedded","Print HWND for host"},{"self-test","Run render-plan and model checks","directory"},{"render-plan","Generate FFmpeg args for project","file"},{"work-dir","Directory for render assets","directory"},{"output","MP4 destination","file"},{"proxy","Generate proxy render plan"},{"export","Export preset id for --render-plan","id"},{"range","Export range from:to seconds","range"},{"frame","Still frame time for --render-plan","seconds"},{"smoke-test","Exercise editor and save a screenshot","directory"},{"pro","Smoke test in pro mode"},{"page","Smoke test inspector page","index"},{"integration-test","Verify import, render and native playback","directory"},{"ui-test","Drive the real window with synthetic input","directory"},{"stabilize","Analyse stabilisation for a project","file"},{"python","Python with faster-whisper for auto subtitles","path"}});parser.process(app);
    if(parser.isSet("render-plan")){
        QFile file(parser.value("render-plan"));if(!file.open(QIODevice::ReadOnly))return 4;Project p;if(!p.load(QJsonDocument::fromJson(file.readAll()).object()))return 5;
        QString work=parser.value("work-dir");if(work.isEmpty()||parser.value("output").isEmpty())return 6;QDir().mkpath(work);
        ExportOptions opt;if(parser.isSet("export"))for(auto &e:exportCatalog())if(e.id==parser.value("export")){opt.container=e.container;opt.codec=e.codec;opt.crf=e.crf;opt.height=e.height;opt.fps=e.fps;opt.audio=e.audio;opt.loud=e.loud;opt.speed="veryfast";}
        if(parser.isSet("range")){auto parts=parser.value("range").split(':');if(parts.size()==2){opt.from=parts[0].toDouble();opt.to=parts[1].toDouble();}}
        RenderPlan r;if(parser.isSet("frame")){opt.still=parser.value("output");r=plan(frameProject(p,parser.value("frame").toDouble()),work,{},false,true,opt);}else r=plan(p,work,parser.value("output"),parser.isSet("proxy"),false,opt);
        if(!r.error.isEmpty()){std::fprintf(stderr,"%s\n",r.error.toUtf8().constData());return 7;}QFile args(work+"/args.json");if(!args.open(QIODevice::WriteOnly))return 8;args.write(QJsonDocument(QJsonArray::fromStringList(r.args)).toJson());return 0;
    }
    if(parser.isSet("self-test")){
        Clip animated;animated.duration=10;animated.source=20;animated.motion=true;animated.endx=100;animated.easing="smooth";auto cut=sliced(animated,3,4);
        if(QLineF(positionAt(cut,1.2),positionAt(animated,4.2)).length()>.0001)return 20;
        animated.motion=false;animated.keys={QJsonObject{{"time",0},{"x",-100},{"y",0},{"scale",.5}},QJsonObject{{"time",5},{"x",100},{"y",20},{"scale",2}},QJsonObject{{"time",10},{"x",0},{"y",0}}};cut=sliced(animated,3,4);
        if(QLineF(positionAt(cut,1.2),positionAt(animated,4.2)).length()>.0001)return 21;
        if(QLineF(positionAt(Clip::read(cut.json()),1.2),positionAt(cut,1.2)).length()>.0001)return 22;
        if(std::abs(valueAt(cut,"scale",1.2)-valueAt(animated,"scale",4.2))>.0001||std::abs(valueAt(animated,"scale",10)-1)>.0001)return 25;
        Project legacy;if(!legacy.load({{"format","echoes-montage"},{"version",1},{"tracks",5},{"clips",QJsonArray{QJsonObject{{"kind","video"},{"track",1},{"fade",.5}},QJsonObject{{"kind","audio"},{"track",3}}}}})||legacy.tracks!=2||legacy.lanes[0].kind!="video"||legacy.lanes[1].kind!="audio")return 23;
        if(legacy.clips[0].fadeIn!=.5||legacy.clips[0].fadeOut!=.5)return 26;
        legacy.clips.clear();legacy.compact();if(legacy.tracks!=1)return 24;
        Project tr;Clip a;a.kind="image";a.path="a.png";a.duration=2;Clip b=a;b.id=QUuid::createUuid().toString();b.start=2;b.trans="dissolve";b.transDur=.5;tr.clips<<a<<b;auto prep=prepared(tr);if(std::abs(prep.clips[0].duration-2.5)>.0001||prep.clips[0].tail!=.5||std::abs(prep.end()-4)>.0001)return 27;
        QDir().mkpath(parser.value("self-test"));Project p;Clip c;c.kind="text";c.text=EchoesText("ECHOES • Привет");c.name="Test";c.duration=2;c.track=p.ensureTrack("text");p.clips<<c;p.markers<<Marker{1,EchoesText("М"),"#e8a33d"};Project copy;if(!copy.load(p.json())||copy.json()!=p.json())return 2;
        auto r=plan(p,parser.value("self-test"),parser.value("self-test")+"/test.mp4",true);if(!r.error.isEmpty())return 3;QFile f(parser.value("self-test")+"/args.json");if(!f.open(QIODevice::WriteOnly))return 4;f.write(QJsonDocument(QJsonArray::fromStringList(r.args)).toJson());
        std::puts("Model round-trip and render-plan OK");return 0;
    }
    QString local=qEnvironmentVariable("LOCALAPPDATA")+"/Programs/ECHOES";
    auto findTool=[&](QString name){if(parser.isSet(name))return parser.value(name);for(QString dir:QStringList{QCoreApplication::applicationDirPath()+"/../../ffmpeg",QCoreApplication::applicationDirPath()+"/../ffmpeg",local+"/ffmpeg"}){QString file=QDir(dir).absoluteFilePath(name+".exe");if(QFileInfo::exists(file))return file;}return QStandardPaths::findExecutable(name);};
    QString vlcDir=parser.value("vlc");if(vlcDir.isEmpty()){for(QString dir:QStringList{QCoreApplication::applicationDirPath()+"/../../vlc",local+"/vlc",qEnvironmentVariable("ProgramFiles")+"/VideoLAN/VLC"})if(QFileInfo::exists(dir+"/libvlc.dll")){vlcDir=QDir::cleanPath(dir);break;}}
    if(parser.isSet("stabilize")){
        QFile file(parser.value("stabilize"));if(!file.open(QIODevice::ReadOnly))return 4;Project p;if(!p.load(QJsonDocument::fromJson(file.readAll()).object()))return 5;
        for(auto &c:p.clips)if(c.stabilize>0&&!QFileInfo::exists(stabilizeFile(c))){QProcess proc;proc.start(findTool("ffmpeg"),stabilizeArgs(c));if(!proc.waitForFinished(600000)||proc.exitCode()!=0){std::fputs(proc.readAllStandardError().constData(),stderr);return 10;}}
        return 0;
    }
    bool testMode=parser.isSet("smoke-test")||parser.isSet("integration-test")||parser.isSet("ui-test");
    QString library=parser.value("library");if(library.isEmpty()&&!testMode)library=QDir::homePath()+"/.neon_player/library.json";
    Editor editor(findTool("ffmpeg"),findTool("ffprobe"),vlcDir,library,testMode);
    // Auto subtitles reuse ECHOES' embedded Python with faster-whisper (the lyrics-sync model).
    editor.python=parser.value("python");for(QString py:QStringList{QCoreApplication::applicationDirPath()+"/../../python/python.exe",local+"/python/python.exe"})if(editor.python.isEmpty()&&QFileInfo::exists(py))editor.python=QDir::cleanPath(py);
    editor.transcriber=QDir::cleanPath(QCoreApplication::applicationDirPath()+"/../montage_transcribe.py");
    if(parser.isSet("embedded"))editor.setWindowFlags(Qt::FramelessWindowHint);
    editor.show();if(parser.isSet("embedded")){std::printf("ECHOES_HWND=%llu\n",static_cast<unsigned long long>(editor.winId()));std::fflush(stdout);}
    if(parser.isSet("smoke-test")){QString dir=parser.value("smoke-test");if(!editor.smokeTest(dir))return 9;if(parser.isSet("pro")||parser.isSet("page"))editor.view(parser.isSet("pro"),parser.value("page").toInt());QTimer::singleShot(900,&editor,[&editor,dir]{editor.grab().save(dir+"/editor.png");editor.close();});}
    if(parser.isSet("integration-test"))editor.integrationTest(parser.value("integration-test"));
    if(parser.isSet("ui-test"))editor.uiTest(parser.value("ui-test"));
    return app.exec();
}
