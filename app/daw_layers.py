"""Four-part hybrid instrument; independent original engine, not a VST emulator."""
import numpy as np
import daw_inst as I
from daw_fx import P

class Layers(I.Gen):
    KIND='layers';NAME='Echoes Layers · 4 слоя'
    INFO='Четыре независимых слоя: аналог, FM, клавиши, щипок или PCM. Свои тон, панорама, фильтр и огибающая на слой.'
    PARAMS=[]
    for i in range(1,5):
        PARAMS += [P(f'{i}type',f'{i}: источник',0,4,0,'','choice',['Аналог','FM','Клавиши','Щипок','PCM-файл']),
            P(f'{i}level',f'{i}: уровень',0,1,.7 if i==1 else 0,'%'),
            P(f'{i}tune',f'{i}: тон',-24,24,0,'st','int'),P(f'{i}pan',f'{i}: панорама',-1,1,0,''),
            P(f'{i}cut',f'{i}: фильтр',40,20000,12000,'Hz','log'),
            P(f'{i}att',f'{i}: атака',.001,2,.01,'с','log'),P(f'{i}rel',f'{i}: релиз',.01,3,.3,'с','log')]
    PARAMS += [P('vol','Общий уровень',0,1,.7,'%')]
    PRESETS={'Init':{},'Воздушные клавиши':{'1type':2,'2type':0,'2level':.3,'2tune':12,'2att':.4,'2pan':-.4,'3type':1,'3level':.2,'3tune':12,'3pan':.4},
             'Тёплый стек':{'1cut':1800,'1att':.4,'1rel':1.4,'2level':.5,'2tune':12,'2pan':-.5,'2att':.5,'3level':.35,'3tune':-12,'3pan':.5,'3att':.3},
             'Стеклянный щипок':{'1type':3,'2type':1,'2level':.4,'2tune':12,'2pan':.4,'3type':2,'3level':.2,'3pan':-.4}}
    def tail(self):return max(float(self.g(f'{i}rel')) for i in range(1,5))
    def key(self):return super().key()+'|'+str(self.p.get('sample',''))
    def render(self,pitch,vel,dur):
        n=max(1,int((dur+self.tail())*I.SR)+128);out=np.zeros((n,2),np.float32)
        for i in range(1,5):
            level=float(self.g(f'{i}level'))
            if level<=0:continue
            typ=int(self.g(f'{i}type'));att=float(self.g(f'{i}att'));rel=float(self.g(f'{i}rel'))
            cls=(I.Synth3x,I.FM,I.Keys,I.Pluck,I.Sampler)[max(0,min(4,typ))]
            if typ==4 and not self.p.get('sample'):continue
            params={'att':att,'rel':rel,'cut':float(self.g(f'{i}cut')),'vol':.7}
            if typ==4:params.update(sample=self.p['sample'],hold=0)
            sound=cls(params).render(pitch+int(self.g(f'{i}tune')),vel,dur)
            envelope=I.env_adsr(max(1,int(dur*I.SR)),len(sound),att,.1,1.,rel)
            sound*=envelope[:,None]
            # Every source (including PCM and keys) gets its own low-pass.
            sound=I.filt(sound,0,float(self.g(f'{i}cut')))
            pan=float(self.g(f'{i}pan'));sound[:,0]*=1-max(0,pan);sound[:,1]*=1+min(0,pan)
            take=min(n,len(sound));out[:take]+=sound[:take]*level
        return np.tanh(out*float(self.g('vol'))).astype(np.float32)

I.GENERATORS[Layers.KIND]=Layers
