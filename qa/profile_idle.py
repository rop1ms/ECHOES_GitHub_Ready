"""Что делает поток интерфейса, когда ECHOES просто открыт (временный профиль, на экране).

python qa/profile_idle.py [страница] [секунды] — cProfile главного потока + доля занятого времени.
"""
import cProfile
import pstats
import sys
import time
from pathlib import Path

src = Path(__file__).with_name('ui_smoke.py').read_text('utf-8')
if '--offscreen' not in sys.argv:
    src = src.replace('os.environ["QT_QPA_PLATFORM"] = "offscreen"', 'pass')
exec(compile(src.split('win = None')[0], str(Path(__file__).with_name('ui_smoke.py')), 'exec'))
import main, ui_anim
from PyQt6.QtWidgets import QApplication

args = [a for a in sys.argv[1:] if not a.startswith('--')]
page = args[0] if args else 'foryou'
secs = float(args[1]) if len(args) > 1 else 10.0
main.WindowsMediaKeyFilter.register = lambda self: None
win = main.MainWindow()
win.resize(1600, 950)
win.show(); settle(1.0)
shell = win._ya
shell.show_page(page); ui_anim._finish(shell.stack); settle(4.0)
pr = cProfile.Profile()
busy = 0.0
t_end = time.perf_counter() + secs
pr.enable()
while time.perf_counter() < t_end:
    t = time.perf_counter()
    QApplication.processEvents()
    busy += time.perf_counter() - t
    time.sleep(0.002)
pr.disable()
print(f'страница {page}: поток интерфейса занят {busy / secs * 100:.1f}% времени')
st = pstats.Stats(pr)
st.sort_stats('tottime').print_stats(18)
win.close()
