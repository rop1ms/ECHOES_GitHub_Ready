"""Optional live test of native catalogue results and covers, with no personal profile."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
os.environ.pop('ECHOES_SPOKEN_OFFLINE',None)
import sys
import time
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from PyQt6.QtWidgets import QApplication, QPushButton, QLabel, QListWidget, QDialog
from PyQt6.QtTest import QTest
from PyQt6.QtGui import QFont, QFontDatabase
import native_ui
import native_spoken
app=QApplication([])
for name in ('segoeui.ttf','segoeuib.ttf','arial.ttf','arialbd.ttf'):
    QFontDatabase.addApplicationFont(str(Path('C:/Windows/Fonts')/name))
app.setFont(QFont('Segoe UI',10))
assert native_ui.available(), native_ui.error()
panel=native_spoken.create(SimpleNamespace(settings={'language':'ru'}))
panel.resize(1020,920);panel.show()
deadline=time.monotonic()+65
while time.monotonic()<deadline:
    app.processEvents();QTest.qWait(100)
    cards=[w for w in panel.findChildren(QPushButton) if w.accessibleName()]
    if len(cards)>=60:break
print('Live cards:',len(cards),flush=True)
assert len(cards)>0,[w.text() for w in panel.findChildren(QLabel)]
QTest.qWait(3000)
out=Path(__file__).parent/'results';out.mkdir(exist_ok=True)
panel.grab().save(str(out/'spoken_live_ru.png'))
# Open the first actual book and verify a native list of playable chapters appears.
cards[0].click()
deadline=time.monotonic()+60
chapters=None
while time.monotonic()<deadline:
    app.processEvents();QTest.qWait(100)
    chapters=panel.findChild(QListWidget)
    if chapters and chapters.count():break
assert chapters is not None and chapters.count()>0
print('Live chapters:',chapters.count(),flush=True)
dialog=panel.findChild(QDialog)
dialog.grab().save(str(out/'spoken_live_chapters_ru.png'))
panel.close()
print('PASS live catalogue and chapters',flush=True)
