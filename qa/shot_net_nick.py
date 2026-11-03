"""Лента Echoes.NET со стильным ником и мини-профилем — в настоящем окне на изолированном профиле."""
import json
import sys
import time
from pathlib import Path
source = Path(__file__).with_name('ui_smoke.py').read_text('utf-8')
exec(compile(source.split('win = None')[0], str(Path(__file__).with_name('ui_smoke.py')), 'exec'))
import main, ui_anim

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else '.')
main.WindowsMediaKeyFilter.register = lambda self: None
win = main.MainWindow()
win.resize(1500, 920)
win.settings['ya_profile_deco'] = {'nick': {'font': 'chicle', 'fx': 'gradient', 'c1': '#ff5c8a', 'c2': '#7c5cff'},
                                   'mp': {'pron': 'он/его', 'about': 'ночные плейлисты и синтвейв',
                                          'bc1': '#5b3cff', 'bc2': '#ff5fa2'}}
win.show(); settle(.6)
shell = win._ya
shell.show_page('net'); ui_anim._finish(shell.stack); settle(.5)
net = shell.pages['net']
store = net.node.store
for i, text in enumerate(['Новый плейлист на вечер — заходите послушать', 'Кто что слушает сегодня?']):
    store.put_post({'id': f'demo-{i}', 'author': net.node.me.onion, 'name': 'Ропим', 'ts': time.time() - i * 900,
                    'text': text, 'kind': 'post'})
net._data_ver = net.__dict__.get('_data_ver', 0) + 1
net.show_tab('feed'); settle(.8)
try:
    net._fill_feed()
except Exception as e:
    print('fill', e)
settle(.6)
win.grab().save(str(OUT / 'net_feed_nick.png'))
import net_miniprof
from PyQt6.QtWidgets import QLabel
avs = [w for w in net.findChildren(QLabel) if w.width() == 34 and w.isVisible()]
print('avatars', len(avs))
if avs:
    net_miniprof._Popup.show_for(net, net.node.me.onion, avs[0]); settle(.4)
    c = net_miniprof._Popup.card
    c.grab().save(str(OUT / 'net_mini_popup.png'))
print('done')
import net_nick
nls = [w for w in net.findChildren(net_nick.NickLabel)]
print('nicks', len(nls))
if nls:
    card = nls[0]
    for _ in range(6):
        card = card.parentWidget()
        if card.objectName() == 'NetCard':
            break
    card.grab().save(str(OUT / 'net_post_nick.png'))
