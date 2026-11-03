"""Настройки без отдельных окон: каждая строка раскрывается на месте и ничего не открывает отдельным окном;
конструктор стримера работает встроенным (добавить слой, сдвиг стрелкой, шаблон, сохранение в настройки).

python qa/test_settings_inline.py [папка-для-картинок]
"""
import sys
from pathlib import Path

source = Path(__file__).with_name('ui_smoke.py').read_text('utf-8')
exec(compile(source.split('win = None')[0], str(Path(__file__).with_name('ui_smoke.py')), 'exec'))
import main, ui_anim
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import QApplication, QDialog, QLabel, QScrollArea

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else None
main.WindowsMediaKeyFilter.register = lambda self: None
fails = []


def check(cond, msg):
    print(('ok   ' if cond else 'FAIL ') + msg, flush=True)
    if not cond:
        fails.append(msg)


win = main.MainWindow()
win.resize(1500, 950)
win.show(); settle(.6)
shell = win._ya
shell.show_page('settings'); ui_anim._finish(shell.stack); settle(.5)
hub = shell.settings_hub
import settings_inline as si
import stream_designer
secs = hub.findChildren(si.Section)
rows = hub.findChildren(si.Row)
check(len(secs) >= 12, f'строк-разделов: {len(secs)}')
check(len(rows) >= 6, f'строк-действий: {len(rows)}')
titles = [s.findChild(QLabel, 'InlineTitle').text() for s in secs]
before = {id(w) for w in QApplication.topLevelWidgets() if w.isVisible()}
for s, t in zip(secs, titles):
    hub.select(next(b.cat for b in hub.blocks if s in b.widgets))
    settle(.05)
    s.set_open(True)
    settle(.25)
    bad = isinstance(s.content, QLabel) and s.content.text().startswith('Не открылось')
    check(s.content is not None and not bad and s.is_open(),
          f'«{t}» раскрылся на месте' + (f': {s.content.text()}' if bad else ''))
    new = [w for w in QApplication.topLevelWidgets() if w.isVisible() and id(w) not in before
           and isinstance(w, QDialog)]
    check(not new, f'«{t}» не открыл отдельного окна')
    if OUT and t in ('Конструктор окна стримера', 'Очистка медиатеки и кэша', 'Масштаб и окно', 'Импорт из Spotify',
                     'Темы оформления', 'Найти пропавшие треки'):
        OUT.mkdir(parents=True, exist_ok=True)
        page = shell.pages['settings']
        page.ensureWidgetVisible(s.header, 0, 0) if isinstance(page, QScrollArea) else None
        settle(.2)
        s.grab().save(str(OUT / f'inline_{len(t)}_{t[:12]}.png'))
    if t != 'Конструктор окна стримера':
        s.set_open(False)
        settle(.05)

st = next((s.content for s in secs if isinstance(s.content, stream_designer.StreamStudio)), None)
check(st is not None, 'конструктор стримера встроен')
if st is not None:
    n = len(st.design['items'])
    st.add('text')
    settle(.5)
    check(len(st.design['items']) == n + 1, 'слой добавлен')
    it = st.design['items'][-1]
    x0 = it['x']
    st.canvas.setFocus()
    QApplication.sendEvent(st.canvas, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier))
    settle(.5)
    check(abs(st.design['items'][-1]['x'] - (x0 + .05)) < 1e-6, 'стрелка сдвигает слой')
    check(len((win.settings.get('stream_design') or {}).get('items', [])) == n + 1, 'дизайн сохранился сам')
    st.use_template('Карточка')
    settle(.5)
    check(win.settings['stream_design']['width'] == 420, 'шаблон применился и сохранился')
if OUT:
    def shot(cat, sec_title, name):
        hub.select(cat); settle(.2)
        for s, t in zip(secs, titles):
            if t == sec_title:
                s.set_open(True); settle(.3)
                shell.pages['settings'].ensureWidgetVisible(s.header, 0, 0); settle(.3)
        win.grab().save(str(OUT / name))
    for s in secs:
        s.set_open(False)
    shot('tools', 'Конструктор окна стримера', 'win_stream.png')
    shot('library', 'Очистка медиатеки и кэша', 'win_cleanup.png')
    shot('look', 'Масштаб и окно', 'win_prefs.png')
    shot('library', 'Найти пропавшие треки', 'win_lost.png')
    hub.select('tools'); settle(.2)
    for s, t in zip(secs, titles):
        if t == 'Конструктор окна стримера':
            s.set_open(True); settle(.3)
            shell.pages['settings'].ensureWidgetVisible(s.content, 0, 0); settle(.4)
            win.resize(1500, 1500); settle(.5)
            shell.pages['settings'].ensureWidgetVisible(s.content, 0, 0); settle(.4)
            win.grab().save(str(OUT / 'stream_section.png'))
print('FAILED' if fails else 'ALL OK', len(fails))
win.close()
sys.exit(1 if fails else 0)
