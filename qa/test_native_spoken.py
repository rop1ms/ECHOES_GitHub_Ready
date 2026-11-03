"""Native spoken discovery: real QWidget integration, language changes and direct-audio playback bridge."""
import ctypes
from pathlib import Path

source = Path(__file__).with_name('ui_smoke.py').read_text('utf-8')
exec(compile(source.split('win = None')[0], str(Path(__file__).with_name('ui_smoke.py')), 'exec'))
import main
import native_ui
import native_spoken
from PyQt6 import sip

assert native_ui.available(), native_ui.error()
main.WindowsMediaKeyFilter.register = lambda self: None
win = main.MainWindow()
win._ya.show_page('foryou')
win.show(); settle(.3)
panel = win._ya._spoken_panel
assert panel is not None
assert panel.metaObject().className() == 'QWidget'  # Implemented by the native DLL, no Python paint handler.
native_ui._lib.eui_spoken_fixture.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_char_p]
native_ui._lib.eui_spoken_fixture.restype = None
books = [
    {'title':'The Secret Garden','author':'Frances Hodgson Burnett'},
    {'title':'A Journey to the Centre of the Earth','author':'Jules Verne'},
    {'title':'Pride and Prejudice','author':'Jane Austen'},
    {'title':'The Adventures of Sherlock Holmes','author':'Arthur Conan Doyle'},
    {'title':'The Time Machine','author':'H. G. Wells'},
]
podcasts = [
    {'title':'Conversations that stay with you','author':'Culture & ideas'},
    {'title':'A curious mind','author':'Science & discovery'},
    {'title':'Beyond the horizon','author':'Travel & stories'},
    {'title':'A moment to think','author':'People & perspectives'},
    {'title':'The creative process','author':'Art & design'},
]
for index, entries in enumerate((books, podcasts)):
    native_ui._lib.eui_spoken_fixture(sip.unwrapinstance(panel),index,json.dumps(entries).encode())

for language in ('en', 'ru', 'en'):
    i18n.change_language(language,win)
    settle(.2)
    labels=[i18n.display_text(label) for label in panel.findChildren(QLabel)]
    assert ('Аудио Книги' if language=='ru' else 'Audiobooks') in labels, labels
    assert ('Подкасты' if language=='ru' else 'Podcasts') in labels, labels
    if language == 'en':
        assert not any(i18n._CYR.search(label) for label in labels), labels

# Rebuilding For You preserves the native panel and its search state.
from PyQt6.QtWidgets import QLineEdit
search = panel.findChild(QLineEdit)
search.setText('remember my search')
win._ya._fill_foryou();settle(.1)
assert win._ya._spoken_panel is panel
assert search.text() == 'remember my search'
search.clear()

# Verify the existing player receives direct RSS media instead of a music search.
requests = []
win.stream_tracks = lambda tracks, **kwargs: requests.append((tracks, kwargs))
episodes=[{'title':'Chapter one','author':'Reader','url':'https://example.com/chapter.mp3',
           'duration':123,'album':'Example audiobook','kind':'audiobooks'}]
win.shuffle=True
win.btn_shuffle.setChecked(True)
panel._spoken_callback(json.dumps(episodes).encode(), 0)
assert len(requests)==1
tracks, options=requests[0]
assert tracks[0]['artist']=='Reader' and tracks[0]['duration']==123
assert win.streams.cached(tracks[0]['path'])=='https://example.com/chapter.mp3'
assert options['playlist']=='\u2063spoken'
assert not win.shuffle and not win.btn_shuffle.isChecked()

# Capture just the native panel, without unrelated scrolling or test-profile data.
panel.setParent(None)
for language in ('ru','en'):
    native_spoken.relabel(language)
    panel.resize(1020,820);panel.show();settle(.15)
    from PyQt6.QtWidgets import QPushButton
    assert all(b.isVisible() for b in panel.findChildren(QPushButton) if b.accessibleName())
    panel.grab().save(str(OUT / f'spoken_native_{language}.png'))
panel.close();win.close()
print('PASS native panels, RU/EN, preserved search, direct-audio playback', flush=True)
sys.stdout.flush()
os._exit(1 if errors else 0)
