"""«Моя волна»: сколько стоит прокрутка списка настроений/жанров (на экране, с GPU-туманностью, профиль — временный).

Меряет: интервалы между кадрами списка, время его paintEvent, задержку главного цикла (таймер 5 мс)
в покое и во время прокрутки колесом. python qa/profile_wave_wheel.py [--offscreen]
"""
import statistics
import sys
import time
from pathlib import Path

src = Path(__file__).with_name('ui_smoke.py').read_text('utf-8')
if '--offscreen' not in sys.argv:
    src = src.replace('os.environ["QT_QPA_PLATFORM"] = "offscreen"', 'pass')
exec(compile(src.split('win = None')[0], str(Path(__file__).with_name('ui_smoke.py')), 'exec'))
import main, ui_anim, ya_wave
from PyQt6.QtCore import QPoint, QPointF, Qt, QTimer
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import QApplication

main.WindowsMediaKeyFilter.register = lambda self: None
win = main.MainWindow()
win.resize(1500, 900)
win.show(); settle(.8)
shell = win._ya
shell.show_page('wave'); ui_anim._finish(shell.stack); settle(2.0)
wave = shell.wave
wheel = wave.wheel
print('gl state:', getattr(wave, '_gl_state', None), 'items:', len(wheel.items))

paints, costs = [], []
orig = ya_wave.WaveWheel.paintEvent


def timed(self, e):
    t = time.perf_counter()
    orig(self, e)
    costs.append(time.perf_counter() - t)
    paints.append(t)


ya_wave.WaveWheel.paintEvent = timed
pcost=[]
porig=ya_wave.WavePage.paintEvent
def ptimed(self,e):
    t=time.perf_counter(); porig(self,e); pcost.append(time.perf_counter()-t)
ya_wave.WavePage.paintEvent=ptimed
tcost=[]
torig=ya_wave.WavePage._tick
def ttimed(self):
    t=time.perf_counter(); torig(self); tcost.append(time.perf_counter()-t)
ya_wave.WavePage._tick=ttimed
lag = []
last = [time.perf_counter()]


def beat():
    now = time.perf_counter()
    lag.append(now - last[0])
    last[0] = now


tm = QTimer(); tm.setInterval(5); tm.timeout.connect(beat); tm.start()


def phase(name, sec, scroll):
    pcost.clear(); tcost.clear()
    paints.clear(); costs.clear(); lag.clear(); last[0] = time.perf_counter()
    end = time.perf_counter() + sec
    k = 0
    while time.perf_counter() < end:
        if scroll and k % 6 == 0:
            pos = QPointF(wheel.width() * 0.3, wheel.height() / 2)
            ev = QWheelEvent(pos, wheel.mapToGlobal(pos), QPoint(0, 0), QPoint(0, -120 if (k // 60) % 2 == 0 else 120),
                             Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase,
                             False)
            QApplication.sendEvent(wheel, ev)
        QApplication.processEvents()
        time.sleep(0.005)
        k += 1
    gaps = [b - a for a, b in zip(paints, paints[1:])]
    print(f'{name:10s} кадров списка {len(paints):4d}  '
          f'paint ср {statistics.mean(costs) * 1000 if costs else 0:6.2f} мс  макс {max(costs or [0]) * 1000:6.1f}  '
          f'интервал ср {statistics.mean(gaps) * 1000 if gaps else 0:6.1f} мс  '
          f'стр.paint {len(pcost)}x{statistics.mean(pcost or [0])*1000:.2f}мс  tick {len(tcost)}x{statistics.mean(tcost or [0])*1000:.2f}мс  '
          f'задержка цикла p95 {sorted(lag)[int(len(lag) * .95)] * 1000 if lag else 0:6.1f} мс  макс {max(lag or [0]) * 1000:6.1f}')


phase('покой', 2.0, False)
import cProfile, pstats
if '--cprof' in sys.argv:
    win.resize(1900, 1040); settle(1.0)
    pr = cProfile.Profile(); pr.enable()
phase('прокрутка', 4.0, True)
if '--cprof' in sys.argv:
    pr.disable(); pstats.Stats(pr).sort_stats('tottime').print_stats(14)
phase('покой', 2.0, False)
win.close()
