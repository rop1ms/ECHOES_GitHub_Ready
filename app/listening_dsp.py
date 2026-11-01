"""Small, smoothed stereo matrix. No audio copies in the native path."""
import ctypes
import numpy as np
IDENTITY=np.array([1,0,0,1],np.float32)
SMOOTHING=float(1-np.exp(-1/(48000*.01)))

def matrix(options):
    width=float(np.clip(options.get('width',1),0,2))
    mode=options.get('mode','stereo')
    if mode=='mono': width=0
    m=np.array([[(1+width)/2,(1-width)/2],[(1-width)/2,(1+width)/2]],np.float32)
    m/=max(1.,width)
    if mode=='left':m[:]=[[1,0],[1,0]]
    elif mode=='right':m[:]=[[0,1],[0,1]]
    elif mode=='mid':m[:]=.5
    elif mode=='side':m[:]=[[.5,-.5],[-.5,.5]]
    if options.get('swap'):m=m[::-1].copy()
    balance=float(np.clip(options.get('balance',0),-1,1))
    m[0]*=1-max(0,balance);m[1]*=1+min(0,balance)
    m*=float(np.clip(options.get('gain',1),0,1))
    return m.ravel().copy()

class StereoProcessor:
    def __init__(self):
        self.target=matrix({});self.current=self.target.copy();self.native=None
        try:
            import native_fx
            if native_fx.available():
                fn=native_fx._lib.fx_listener_matrix
                fn.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_float]
                fn.restype=None;self.native=fn
        except (AttributeError,OSError):pass
    def configure(self,options):self.target=matrix(options)
    def process(self,a):
        if not len(a):return a
        target=self.target
        if np.max(np.abs(self.current-target))<1e-6 and np.array_equal(target,IDENTITY):
            return a
        # 10 ms one-pole smoothing, independent of callback block size.
        k=SMOOTHING
        if self.native:
            self.native(a.ctypes.data,len(a),self.current.ctypes.data,target.ctypes.data,k)
        else:
            weights=(1-k)**np.arange(1,len(a)+1,dtype=np.float64)
            coeff=target[None,:]+weights[:,None]*(self.current-target)[None,:]
            left=a[:,0].copy();right=a[:,1].copy()
            a[:,0]=left*coeff[:,0]+right*coeff[:,1]
            a[:,1]=left*coeff[:,2]+right*coeff[:,3]
            self.current[:]=coeff[-1]
        return a
