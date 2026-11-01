"""Measure the former count-only row cache against the shared byte budget."""
import os,sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
os.environ['QT_QPA_PLATFORM']='offscreen'
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPixmap,QColor
from raster_budget import ByteLRU
from performance import memory_usage
app=QApplication([])
before=memory_usage();mode=sys.argv[1];cache={} if mode=='before' else ByteLRU(12*2**20)
for n in range(400):
    pixmap=QPixmap(1440,56);pixmap.fill(QColor('#12151b'))
    if mode=='before':cache[n]=pixmap
    else:cache.put((1,n),pixmap,pixmap.width()*pixmap.height()*4)
after=memory_usage()
print(json.dumps({'mode':mode,'rows':400,'working_delta_mib':round((after[0]-before[0])/2**20,2),
                  'private_delta_mib':round((after[1]-before[1])/2**20,2),'cached_mib':round((400*1440*56*4 if mode=='before' else cache.bytes)/2**20,2)}))
