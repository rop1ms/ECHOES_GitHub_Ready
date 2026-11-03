"""Снимок «Моей волны» с GPU-туманностью (на экране, временный профиль)."""
import sys
from pathlib import Path
src = Path(__file__).with_name('ui_smoke.py').read_text('utf-8').replace('os.environ["QT_QPA_PLATFORM"] = "offscreen"', 'pass')
exec(compile(src.split('win = None')[0], str(Path(__file__).with_name('ui_smoke.py')), 'exec'))
import main, ui_anim
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else '.')
main.WindowsMediaKeyFilter.register = lambda self: None
win = main.MainWindow(); win.resize(1500, 900); win.show(); settle(.8)
shell = win._ya
shell.show_page('wave'); ui_anim._finish(shell.stack); settle(3.0)
print('gl', shell.wave._gl_state)
shell.wave.grab().save(str(OUT / 'wave_page.png'))
win.close()
