"""Spectral smoke, glowing filaments and embers on a byte-bounded loop cache."""
from collections import OrderedDict
import math
from PyQt6.QtCore import QPointF,QRectF,Qt
from PyQt6.QtGui import QColor,QImage,QPainter,QPainterPath,QPen,QPixmap
from duel_settings import fire_tier

COLORS=('#28ee9a','#429cff','#a876ff','#ff55a7','#ff9747','#c4faff')

class FireCache:
    def __init__(self):self.frames=OrderedDict();self.bytes=0;self._geometry=None
    def clear(self):self.frames.clear();self.bytes=0;self._geometry=None
    def policy(self,width,height):
        from performance import config
        opts=config();quality=opts['aura_quality'];side,frames={'low':(112,10),'balanced':(160,16),'high':(224,20)}[quality]
        h=max(1,round(side*(height+88)/(width+88)));size=side*h*4;budget=opts['aura_mb']*2**20
        frames=min(frames,max(4,budget//max(1,size*2)))
        return side,h,frames,budget,quality
    def frame(self,width,height,wins,now):
        tier=fire_tier(wins)
        if not tier:return None
        w,h,count,budget,quality=self.policy(width,height)
        geometry=(width,height,w,h,count,budget)
        if geometry!=self._geometry:self.clear();self._geometry=geometry
        phase_index=int((now%1.6)/1.6*count)%count
        key=(tier,phase_index)
        if key in self.frames:
            self.frames.move_to_end(key);return self.frames[key]
        import numpy as np
        phase=phase_index*math.tau/count
        x=np.linspace(-44,width+44,w,dtype=np.float32)[None,:]
        y=np.linspace(-44,height+44,h,dtype=np.float32)[:,None]
        qx=np.abs(x-width/2)-(width/2-24);qy=np.abs(y-height/2)-(height/2-24)
        distance=np.sqrt(np.maximum(qx,0)**2+np.maximum(qy,0)**2)+np.minimum(np.maximum(qx,qy),0)-24
        u=x/width;v=y/height
        warp=np.sin(u*13+v*7+phase)*.06+np.sin(v*17-phase)*.035
        turbulence=(np.sin((u+warp)*31+v*9+phase*2)*.24+
                    np.sin(u*13-(v+warp)*33+phase)*.23+
                    np.sin(u*71+v*41+np.sin(v*17+phase))*.12+
                    np.sin(u*121-v*83-phase*2)*.065+.5)
        turbulence=np.clip(turbulence,0,1)
        plume=np.exp(-np.maximum(distance,0)/(9+turbulence*27))
        hollow=np.clip((distance+12)/12,0,1)
        density=np.clip((turbulence-.24)/.68,0,1)**1.6
        alpha=np.clip(plume*hollow*density*(.73+tier*.025),0,.9)
        core=np.exp(-((distance-2)/1.7)**2)*(.5+.5*turbulence)
        alpha=np.maximum(alpha,core*.88)
        boundary=np.minimum(np.minimum(x+44,width+44-x),np.minimum(y+44,height+44-y))
        alpha*=np.clip(boundary/17,0,1)**1.5
        base=QColor(COLORS[tier-1]);color=np.array([base.red(),base.green(),base.blue()],dtype=np.float32)
        brightness=.55+turbulence[...,None]*.9
        rgb=np.minimum(255,color[None,None,:]*brightness+core[...,None]*120)
        pixels=np.empty((h,w,4),dtype=np.uint8);pixels[:,:,:3]=rgb.astype(np.uint8);pixels[:,:,3]=(alpha*255).astype(np.uint8)
        image=QImage(pixels.tobytes(),w,h,w*4,QImage.Format.Format_RGBA8888).copy()
        painter=QPainter(image);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        sx=w/(width+88);sy=h/(height+88);painter.scale(sx,sy);painter.translate(44,44)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for i in range(8):
            side=-1 if i%2==0 else 1;edge=0 if side<0 else width
            yy=height*(i+.4)/8;drift=math.sin(phase+i*2.3)*8
            path=QPainterPath(QPointF(edge,yy+22));path.cubicTo(edge+side*(15+drift),yy+2,edge-side*4,yy-18,edge+side*(9-drift),yy-35)
            ink=QColor(base).lighter(145);ink.setAlpha(90);painter.setPen(QPen(ink,1.15));painter.drawPath(path)
        painter.setPen(Qt.PenStyle.NoPen)
        for i in range(12):
            drift=(phase/math.tau+i*.173)%1.;xx=(-12 if i%2==0 else width+12)+math.sin(i*8+phase)*16
            yy=height*(1-drift);ink=QColor(base).lighter(170);ink.setAlpha(int(math.sin(drift*math.pi)*190));painter.setBrush(ink)
            radius=1.1+(i%3)*.45;painter.drawEllipse(QPointF(xx,yy),radius,radius)
        painter.end();pixmap=QPixmap.fromImage(image);self.frames[key]=pixmap;self.bytes+=w*h*4
        while self.bytes>budget and self.frames:
            _,old=self.frames.popitem(last=False);self.bytes-=old.width()*old.height()*4
        return pixmap
    def draw(self,painter,rect,width,height,wins,now):
        current=self.frame(width,height,wins,now)
        if current is None:return
        w,h,count,budget,quality=self.policy(width,height)
        if quality=='low':painter.drawPixmap(rect,current,QRectF(current.rect()));return
        step=1.6/count;mix=(now%1.6)/step%1
        following=self.frame(width,height,wins,now+step)
        opacity=painter.opacity();painter.setOpacity(opacity*(1-mix));painter.drawPixmap(rect,current,QRectF(current.rect()))
        painter.setOpacity(opacity*mix);painter.drawPixmap(rect,following,QRectF(following.rect()));painter.setOpacity(opacity)
