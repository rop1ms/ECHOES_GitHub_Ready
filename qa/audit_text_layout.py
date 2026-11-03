"""Offline RU/EN tour: capture text clipping and untranslated UI in major pages and settings."""
import sys
from pathlib import Path

source = Path(__file__).with_name('ui_smoke.py').read_text('utf-8')
exec(compile(source.split('win = None')[0], str(Path(__file__).with_name('ui_smoke.py')), 'exec'))
from PyQt6.QtWidgets import QPushButton, QStyle, QStyleOptionButton
import main
main.WindowsMediaKeyFilter.register = lambda self: None
win = main.MainWindow()
win.show()
report = []


def inspect(page):
    settle(.2)
    for b in app.allWidgets():
        if not isinstance(b, (QPushButton, QLabel)) or not b.isVisible():
            continue
        text = i18n.display_text(b)
        if len(text.strip()) < 3 or '<' in text or '\n' in text:
            continue
        if isinstance(b, QLabel):
            if b.wordWrap():
                continue
            rect = b.contentsRect()
        else:
            option = QStyleOptionButton()
            b.initStyleOption(option)
            rect = b.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, b)
        needed = b.fontMetrics().horizontalAdvance(text.replace('&&', '\0').replace('&', '').replace('\0', '&'))
        available = rect.width() - (b.iconSize().width() + 4 if isinstance(b, QPushButton) and not b.icon().isNull() else 0)
        if needed > available + 3:
            report.append({'page': page, 'text': text, 'width': b.width(), 'needed': needed,
                           'available': available, 'class': type(b).__name__, 'name': b.objectName()})


for width in (1024, 1440):
    win.resize(width, 900)
    for page in ('collection', 'search', 'wave', 'foryou', 'albums', 'follows', 'tierlist',
                 'duel', 'net', 'profile', 'queue', 'lyrics', 'settings', 'listening'):
        win._ya.show_page(page)
        inspect(f'{width}:{page}')
    for category in win._ya.settings_hub.nav_btns:
        win._ya.settings_hub.select(category)
        inspect(f'{width}:settings:{category}')

from spotify_import import SpotifyImportDialog
dialog = SpotifyImportDialog(win, provider='auto')
dialog.resize(720, 650)
dialog.show()
inspect('import')
chips = dialog.findChildren(QPushButton)
services = [b for b in chips if b.objectName() == 'YaChip']
assert len(services) == 8
for b in services:
    assert b.width() >= b.sizeHint().width(), (b.text(), b.width(), b.sizeHint().width())
assert len({b.y() for b in services}) > 1, 'Services should wrap at this width'
dialog.grab().save(str(OUT / f'import_fixed_{settings["language"]}.png'))
dialog.close()

from ya_artist_about import AboutCard, AboutDialog
data = {'name': 'Artist example', 'mb_type': 'Person', 'mb_begin': '1946-09-05',
        'mb_end': '1991-11-24', 'mb_ended': True, 'mb_birthplace': 'Zanzibar',
        'mb_country': 'United Kingdom', 'mb_area': 'London', 'bio': 'Artist biography.',
        'mb_id': 'b95ce3ff-3d05-4e87-9e01-c97b66af13d4', 'ytm_subs': 1234567}
card = AboutCard(win._ya, data, data['name'])
card.resize(760, 440)
card.show(); settle(.1)
card.grab().save(str(OUT / f'artist_facts_{settings["language"]}.png'))
detail = AboutDialog(win._ya, data, data['name'])
detail.show(); settle(.1)
detail.grab().save(str(OUT / f'artist_details_{settings["language"]}.png'))
detail.close();card.close()
path = OUT / f'text_layout_{settings["language"]}.json'
path.write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
print('Layout findings:', len(report), str(path), flush=True)
win.close()
sys.stdout.flush()
os._exit(1 if errors else 0)
