"""Native-resolution edge flames. No enlarged bitmap or rectangular aura surface."""
from collections import OrderedDict
from dataclasses import dataclass
import math
from PyQt6.QtCore import QPointF,QRectF,Qt
from PyQt6.QtGui import QColor,QPainter,QPainterPath,QPen,QLinearGradient,QRadialGradient,QBrush
from duel_settings import fire_tier

COLORS=('#28ee9a','#429cff','#a876ff','#ff55a7','#ff9747','#c4faff')

@dataclass(frozen=True)
class Emitter:
    x:float;y:float;nx:float;ny:float;tx:float;ty:float;seed:float;breadth:float;reach:float

class FireCache:
    def __init__(self):self.frames=OrderedDict();self.bytes=0
    def clear(self):self.frames.clear();self.bytes=0
    def frame(self,width,height,wins,now=0):
        tier=fire_tier(wins)
        if not tier:return ()
        from performance import config
        opts=config();count={'low':36,'balanced':64,'high':88}[opts['aura_quality']]
        key=(width,height,tier,count)
        if key in self.frames:
            self.frames.move_to_end(key);return self.frames[key]
        outline=QPainterPath();outline.addRoundedRect(QRectF(-width/2,-height/2,width,height),24,24)
        length=outline.length();emitters=[]
        for i in range(count):
            seed=i*2.3999632297+tier*.37
            percent=outline.percentAtLength((i+.3+.15*math.sin(seed))*length/count)
            origin=outline.pointAtPercent(percent)
            a=outline.pointAtPercent(max(0,percent-.0008));b=outline.pointAtPercent(min(1,percent+.0008))
            tx,ty=b.x()-a.x(),b.y()-a.y();norm=max(.001,math.hypot(tx,ty));tx/=norm;ty/=norm
            nx,ny=-ty,tx
            if nx*origin.x()+ny*origin.y()<0:nx,ny=-nx,-ny
            emitters.append(Emitter(origin.x(),origin.y(),nx,ny,tx,ty,seed,5+(i%4)*2,24+tier*3+(i%5)*7))
        packet=tuple(emitters);self.frames[key]=packet;self.bytes+=len(packet)*1024
        budget=opts['aura_mb']*2**20
        while self.bytes>budget and self.frames:
            _,old=self.frames.popitem(last=False);self.bytes-=len(old)*1024
        return packet
    def draw(self,painter,rect,width,height,wins,now):
        packet=self.frame(width,height,wins,now)
        if not packet:return
        color=QColor(COLORS[fire_tier(wins)-1]);painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for emitter in packet:
            e=emitter;phase=now*2.8+e.seed
            flicker=.78+.2*math.sin(phase)+.09*math.sin(phase*1.73)
            reach=e.reach*flicker
            # The lower edge curls out and upward rather than hitting a flat floor.
            if e.ny>.65:reach*=.46
            curl=math.sin(phase*.87)*15
            ox,oy=e.x,e.y
            lift=28+19*(.5+.5*math.sin(phase*.61))
            tip=QPointF(ox+e.nx*reach*.8+e.tx*curl,oy+e.ny*reach*.7+e.ty*curl-lift)
            root=QPointF(ox,oy)
            edges=[[],[]]
            for j in range(9):
                u=j/8;wave=math.sin(u*8.4-phase)*13*math.sin(math.pi*u)
                x=ox+e.nx*reach*.8*u+e.tx*(curl*u+wave)
                y=oy+e.ny*reach*.7*u+e.ty*(curl*u+wave)-lift*u*u
                half=e.breadth*(1-u)**.85*(.85+.15*math.sin(u*13+phase))
                edges[0].append(QPointF(x-e.tx*half,y-e.ty*half))
                edges[1].append(QPointF(x+e.tx*half,y+e.ty*half))
            points=edges[0]+list(reversed(edges[1]))
            path=QPainterPath(points[0])
            for j in range(1,len(points)-1):
                path.quadTo(points[j],(points[j]+points[j+1])*.5)
            path.lineTo(points[-1])
            path.closeSubpath()
            # Translucent ribbons overlap softly. Outlined tongues look like petals.
            painter.setPen(Qt.PenStyle.NoPen)
            gradient=QLinearGradient(root,tip)
            pulse=.65+.35*math.sin(phase*.81)**2
            hot=QColor(color).lighter(135);hot.setAlpha(int(170*pulse));gradient.setColorAt(0,hot)
            body=QColor(color);body.setAlpha(int(92*pulse));gradient.setColorAt(.32,body)
            body.setAlpha(27);gradient.setColorAt(.68,body);body.setAlpha(0);gradient.setColorAt(1,body)
            if int(e.seed*10)%2==0:
                bloom=QRadialGradient(root,23)
                ink=QColor(color);ink.setAlpha(42);bloom.setColorAt(0,ink)
                ink.setAlpha(0);bloom.setColorAt(1,ink)
                painter.setBrush(bloom);painter.drawEllipse(root,23,23)
                painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.fillPath(path,gradient)
            if int(e.seed*10)%3==0:
                smoke=QPainterPath(root);smoke.cubicTo(QPointF(ox+e.nx*reach*.6+curl,oy+e.ny*reach*.5),
                                                    QPointF(tip.x()+curl*.5,tip.y()-10),QPointF(tip.x()-curl*.3,tip.y()-22))
                vapor=QLinearGradient(root,QPointF(tip.x(),tip.y()-22))
                ink=QColor(color);ink.setAlpha(55);vapor.setColorAt(0,ink);ink.setAlpha(0);vapor.setColorAt(1,ink)
                painter.setPen(QPen(QBrush(vapor),1.25));painter.drawPath(smoke)
        # Sparse embers have independent trajectories and no rectangular clipping.
        painter.setPen(Qt.PenStyle.NoPen)
        for i in range(10):
            e=packet[(i*7)%len(packet)];life=(now*.27+i*.173)%1
            x=e.x+e.nx*(8+life*44)+math.sin(i*3.7+now)*5
            y=e.y+e.ny*life*20-life*66
            ink=QColor(color).lighter(185);ink.setAlpha(int(math.sin(life*math.pi)*150));painter.setBrush(ink)
            radius=.6+(i%3)*.28;painter.drawEllipse(QPointF(x,y),radius,radius)
        painter.restore()
