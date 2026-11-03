"""Умный 8D: темп находится, звук кружит между ушами с периодом круга, басы в центре, громкость как была;
кнопка 8D в панели «Звук» связана с переключателем плеера.

python qa/test_8d.py [папка-для-картинок]
"""
import sys
import time
from pathlib import Path

source = Path(__file__).with_name('ui_smoke.py').read_text('utf-8')
exec(compile(source.split('win = None')[0], str(Path(__file__).with_name('ui_smoke.py')), 'exec'))
import numpy as np
import spatial8d

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else None
fails = []


def check(cond, msg):
    print(('ok   ' if cond else 'FAIL ') + msg, flush=True)
    if not cond:
        fails.append(msg)


# синтетический трек: бочка 120 BPM + бас 55 Гц + аккорд, 40 с
sr = 44100
n = sr * 40
t = np.arange(n) / sr
beat = 60 / 120
kick = np.zeros(n, np.float32)
for k in np.arange(0, 40, beat):
    i = int(k * sr)
    m = min(n - i, int(0.12 * sr))
    kick[i:i + m] += (np.sin(2 * np.pi * 60 * t[:m]) * np.exp(-t[:m] * 30)).astype(np.float32)
bass = 0.3 * np.sin(2 * np.pi * 55 * t)
chord = 0.15 * (np.sin(2 * np.pi * 440 * t) + np.sin(2 * np.pi * 554 * t) + np.sin(2 * np.pi * 1320 * t))
mono = (kick + bass + chord).astype(np.float32)
audio = np.stack([mono, mono], axis=1) * 0.5
t0 = time.time()
out, info = spatial8d.render(audio, sr)
dt = time.time() - t0
check(abs(info['bpm'] - 120) < 3 or abs(info['bpm'] - 60) < 2 or abs(info['bpm'] - 240) < 4,
      f'темп найден: {info["bpm"]:.1f} BPM')
check(dt < 6, f'40 с музыки — за {dt:.2f} с')
check(out.shape == audio.shape and not np.isnan(out).any(), 'форма и числа в порядке')
rms_in = float(np.sqrt((audio ** 2).mean()))
rms_out = float(np.sqrt((out ** 2).mean()))
check(abs(rms_out / rms_in - 1) < 0.05, f'громкость как у оригинала ({rms_out / rms_in:.3f})')
check(float(np.abs(out).max()) <= 0.981, 'без перегруза')
# верха кружат: разница громкости L/R меняет знак с периодом круга
from scipy import signal as sg
sos = sg.butter(4, 1000 / (sr / 2), 'high', output='sos')
hi = sg.sosfilt(sos, out, axis=0)
win = int(sr * 0.25)
el = np.sqrt(np.convolve(hi[:, 0] ** 2, np.ones(win) / win, 'valid'))[::win]
er = np.sqrt(np.convolve(hi[:, 1] ** 2, np.ones(win) / win, 'valid'))[::win]
bal = (er - el) / (er + el + 1e-9)
check(bal.max() > 0.35 and bal.min() < -0.35, f'звук уходит то вправо, то влево ({bal.min():.2f} … {bal.max():.2f})')
flips = int(np.sum(np.diff(np.sign(bal)) != 0))
check(2 <= flips <= 14, f'кругов за 40 с — разумно (смен сторон: {flips})')
lo = sg.sosfilt(sg.butter(4, 90 / (sr / 2), 'low', output='sos'), out, axis=0)
lb = np.sqrt((lo[:, 0] ** 2).mean()) / (np.sqrt((lo[:, 1] ** 2).mean()) + 1e-9)
check(abs(lb - 1) < 0.08, f'басы в центре (L/R = {lb:.3f})')
check('BPM' in spatial8d.describe(info), 'описание: ' + spatial8d.describe(info))

# кнопка в панели «Звук» ↔ переключатель плеера
import main
main.WindowsMediaKeyFilter.register = lambda self: None
w = main.MainWindow()
w.show(); settle(.5)
check(hasattr(w, '_btn_8d'), 'у плеера есть переключатель 8D')
from ya_sound_panel import SoundPanel, Orbit8D
panel = SoundPanel(w._ya, embedded=True)
panel.resize(360, 640); panel.show(); settle(.2)
b = panel.findChild(Orbit8D)
check(b is not None, 'в панели «Звук» есть кнопка 8D')
if b is not None:
    w._btn_8d.blockSignals(True); w._btn_8d.setChecked(True); w._btn_8d.blockSignals(False)
    panel.sync(); settle(.3)
    check(b.isChecked(), 'кнопка отражает включённый 8D')
    if OUT:
        OUT.mkdir(parents=True, exist_ok=True)
        panel.setStyleSheet(panel.styleSheet() + 'QFrame#SndPanel{background:#0d0d10;}')
        panel.grab().save(str(OUT / 'sound_panel_8d.png'))
    w._btn_8d.blockSignals(True); w._btn_8d.setChecked(False); w._btn_8d.blockSignals(False)
print('FAILED' if fails else 'ALL OK', len(fails))
w.close()
sys.exit(1 if fails else 0)
