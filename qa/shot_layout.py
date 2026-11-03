"""Скриншоты новой раскладки (шапка, нижняя панель, плитки плейлистов, коллекция) на изолированном профиле."""
import os
import sys
from pathlib import Path
source = Path(__file__).with_name('ui_smoke.py').read_text('utf-8')
exec(compile(source.split('win = None')[0], str(Path(__file__).with_name('ui_smoke.py')), 'exec'))
import main, ui_anim

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else '.')
main.WindowsMediaKeyFilter.register = lambda self: None
win = main.MainWindow()
win.resize(1500, 920)
win.library = [{'path': str(audit_home / f'{i}.wav'), 'title': f'Трек {i}', 'artist': 'Artist', 'duration': 180}
               for i in range(30)]
win.playlists = {'Вечер': {'tracks': win.library[:5]}, 'Мне нравится': {'tracks': win.library[::3]},
                 'Дорога': {'tracks': win.library[5:9]}}
win.settings['pl_usage'] = {'Вечер': 40, 'Дорога': 20}
win.show(); settle(.6)
shell = win._ya
shell._dirty.update(shell.pages)
try:
    shell.pl_strip.refresh()
except Exception as e:
    print('strip', e)
for key in ('collection',):
    shell._dirty.add(key); shell.show_page(key); ui_anim._finish(shell.stack); settle(.6)
    win.grab().save(str(OUT / f'layout_{key}.png'))
print('order', shell.playlists_by_usage())
shell.net_badge.setText('3'); shell.net_badge.show(); settle(.3)
win.grab().save(str(OUT / 'layout_badge.png'))
print('done')
