"""Shared byte-bounded LRU for row rasterization (not a per-view item count)."""
from collections import OrderedDict


class ByteLRU:
    def __init__(self, limit=12*2**20):self.limit=limit;self.bytes=0;self.items=OrderedDict()
    def get(self,key):
        entry=self.items.pop(key,None)
        if entry is None:return None
        self.items[key]=entry;return entry[0]
    def put(self,key,value,size):
        old=self.items.pop(key,None)
        if old:self.bytes-=old[1]
        if size<=self.limit:self.items[key]=(value,size);self.bytes+=size
        self.prune()
    def prune(self):
        while self.bytes>self.limit and self.items:
            _,(_,size)=self.items.popitem(last=False);self.bytes-=size
    def clear(self,owner=None):
        if owner is None:self.items.clear();self.bytes=0;return
        for key in [key for key in self.items if key[0]==owner]:
            _,size=self.items.pop(key);self.bytes-=size


ROWS=ByteLRU()
