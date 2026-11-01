"""Bounded live radio voice rack and short file soundboard decoder (worker threads only)."""
from pathlib import Path
import warnings
import numpy as np

DEFAULTS={'enabled':False,'pitch':0,'formant':0,'autotune':0,'key':0,'scale':2,'robot':0,
          'echo':0,'reverb':0,'chorus':0,'distortion':0,'tremolo':0,'telephone':False,'bitcrush':0,'compress':False}
PRESETS=[('Чистый голос','Clean',{}),('Ведущий','Announcer',{'compress':True}),
    ('Мягкий автотюн','Soft autotune',{'autotune':65,'compress':True}),('Жёсткий автотюн','Hard autotune',{'autotune':100}),
    ('Глубокий голос','Deep voice',{'pitch':-5,'formant':-2}),('Бурундук','Chipmunk',{'pitch':7,'formant':3}),
    ('Робот','Robot',{'robot':100}),('Телефон','Telephone',{'telephone':True,'compress':True}),
    ('Мегафон','Megaphone',{'telephone':True,'distortion':45}),('Космос','Space',{'reverb':45,'echo':30,'chorus':25}),
    ('Кассета','Lo-fi',{'bitcrush':45,'chorus':15}),('Дрожащий голос','Tremolo',{'tremolo':55})]

def options(raw):
    raw=raw if isinstance(raw,dict) else {};out=dict(DEFAULTS)
    for key in out:
        if isinstance(out[key],bool):out[key]=raw.get(key,out[key]) is True;continue
        lo,hi=(-12,12) if key=='pitch' else (-6,6) if key=='formant' else (0,11) if key=='key' else (0,2) if key=='scale' else (0,100)
        try:out[key]=max(lo,min(hi,int(raw.get(key,out[key]))))
        except (ValueError,TypeError,OverflowError):pass
    return out

class Room:
    TYPE="room"
    def __init__(self,wet):
        self.wet=wet;self.rings=[np.zeros((n,2),np.float32) for n in (1327,1789,1987,2381)];self.pos=[0]*4
    def process(self,x):
        wet=np.zeros_like(x);n=len(x)
        for i,ring in enumerate(self.rings):
            ids=(np.arange(n)+self.pos[i])%len(ring);delayed=ring[ids].copy()
            ring[ids]=x*.3+delayed*.57;wet+=delayed;self.pos[i]=(self.pos[i]+n)%len(ring)
        return x*(1-self.wet*.4)+wet*self.wet*.55

class VoiceRack:
    def __init__(self,config):
        self.chain=[];self.down=None;self.up=None;self.pending=np.zeros(0,np.float32)
        self.update(config)

    @staticmethod
    def slots(c):
        slots=[]
        def add(kind,params):slots.append((kind,params))
        if c['compress']:add('comp',{'thr':-18,'ratio':3,'makeup':1})
        if c['pitch'] or c['formant']:add('pitch',{'semi':c['pitch'],'fshift':c['formant'],'formant':1})
        if c['autotune']:add('autotune',{'amount':c['autotune']/100,'speed':0 if c['autotune']>=95 else 35,'key':c['key'],'scale':c['scale']})
        if c['robot']:add('robot',{'mix':c['robot']/100})
        if c['telephone']:add('filter',{'type':2,'cut':1550,'res':.7})
        if c['distortion']:add('dist',{'drive':8+c['distortion']*.15,'out':-8,'mix':c['distortion']/120})
        if c['bitcrush']:add('crush',{'bits':max(4,16-c['bitcrush']//8),'down':1+c['bitcrush']//15,'mix':.8})
        if c['chorus']:add('chorus',{'voices':2,'mix':c['chorus']/130})
        if c['tremolo']:add('trem',{'sync':0,'rate':5,'depth':c['tremolo']/100})
        if c['echo']:add('delay',{'time':8,'ms':240,'fb':.28,'pp':0,'mix':c['echo']/140})
        if c['reverb']:add('room',{'wet':c['reverb']/100})
        return slots

    def update(self,config):
        from daw_fx import FX_TYPES
        c=options(config);slots=self.slots(c)
        if [fx.TYPE for fx in self.chain]==[kind for kind,_ in slots]:
            for fx,(kind,params) in zip(self.chain,slots):
                if kind=='room':fx.wet=params['wet']
                else:
                    if kind=='autotune' and any(fx.p.get(key)!=params.get(key) for key in ('key','scale')):fx.tgt_note=None
                    fx.p.update(params)
        else:
            self.chain=[Room(params['wet']) if kind=='room' else FX_TYPES[kind](params) for kind,params in slots]
        self.config=c

    def process(self,x):
        if not self.config['enabled'] or not self.chain:return np.asarray(x,dtype=np.float32)
        # Stateful resampling keeps the studio DSP's 44.1 kHz tuning correct on the 48 kHz voice channel.
        with warnings.catch_warnings():
            warnings.simplefilter('ignore',DeprecationWarning);import audioop
        pcm=np.clip(x*32767,-32768,32767).astype('<i2').tobytes()
        raw,self.down=audioop.ratecv(pcm,2,1,48000,44100,self.down)
        mono=np.frombuffer(raw,dtype='<i2').astype(np.float32)/32768
        y=np.repeat(mono[:,None],2,axis=1)
        for effect in self.chain:y=effect.process(y)
        y=np.nan_to_num(y.mean(axis=1),nan=0.,posinf=0.,neginf=0.)
        raw,self.up=audioop.ratecv(np.clip(y*32767,-32768,32767).astype('<i2').tobytes(),2,1,44100,48000,self.up)
        audio=np.frombuffer(raw,dtype='<i2').astype(np.float32)/32768
        # Keep a short FIFO: never drop a fractional resampler sample at block boundaries.
        self.pending=np.concatenate((self.pending,audio))
        n=len(x);out=self.pending[:n];self.pending=self.pending[n:]
        if len(out)<n:out=np.pad(out,(n-len(out),0))
        return np.clip(out,-.98,.98)

def decode_clip(path):
    """Decode at most 30 seconds/10 MiB; no whole-song allocation or UI-thread decode."""
    from net_files import AUDIO
    p=Path(path)
    if p.suffix.lower() not in AUDIO or not p.is_file() or p.stat().st_size>10*2**20:raise ValueError('Choose an audio file up to 10 MiB and 30 seconds.')
    import av
    chunks=[];count=0
    with av.open(str(p)) as container:
        resampler=av.AudioResampler(format='fltp',layout='mono',rate=48000)
        for frame in container.decode(audio=0):
            for converted in resampler.resample(frame):
                chunk=converted.to_ndarray().reshape(-1).astype(np.float32)
                count+=len(chunk)
                if count>48000*30:raise ValueError('Soundboard clips must be 30 seconds or shorter.')
                chunks.append(chunk)
        for converted in resampler.resample(None):
            chunk=converted.to_ndarray().reshape(-1).astype(np.float32);count+=len(chunk)
            if count>48000*30:raise ValueError('Soundboard clips must be 30 seconds or shorter.')
            chunks.append(chunk)
    if not chunks:raise ValueError('This audio file is empty.')
    return np.clip(np.nan_to_num(np.concatenate(chunks)), -1.,1.)
