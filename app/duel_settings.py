"""Duel preferences and library filtering; no analysis or network on UI paths."""
from __future__ import annotations
import math
import re

DEFAULTS = {'chorus': True, 'fire': True, 'genre': '', 'artist': '', 'album': '',
            'playlist': '', 'liked_only': False, 'min_seconds': 0, 'max_seconds': 0}


def tr(win, ru, en):
    return en if win.settings.get('language', 'ru') == 'en' else ru


def options(settings):
    raw = settings.get('duel_options')
    raw = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULTS)
    for key in ('chorus', 'fire', 'liked_only'):
        if type(raw.get(key)) is bool: out[key] = raw[key]
    for key in ('genre', 'artist', 'album', 'playlist'):
        out[key] = str(raw.get(key) or '')[:200].strip()
    for key in ('min_seconds', 'max_seconds'):
        try: out[key] = max(0, min(7200, int(raw.get(key) or 0)))
        except (ValueError, TypeError, OverflowError): pass
    return out


def duration(track):
    try:
        value = float(track.get('duration') or 0)
        return value if math.isfinite(value) and value > 0 else 0.
    except (TypeError, ValueError): return 0.


def liked_paths(win):
    rows = win.playlists.get('Мне нравится', {}).get('tracks', [])
    return {str(track.get('path') or '') if isinstance(track,dict) else str(track) for track in rows}


def genres(track, profiles=None):
    values = track.get('genres') or track.get('genre') or []
    if isinstance(values, str): values = re.split(r'[,;/]+', values)
    if isinstance(values, dict): values = list(values)
    if not isinstance(values, (list, tuple, set)): values = []
    profile = (profiles or {}).get(str(track.get('path') or ''), {})
    inferred = profile.get('genres') or [] if isinstance(profile, dict) else []
    if isinstance(inferred, dict): inferred = list(inferred)
    if isinstance(inferred, str): inferred = [inferred]
    if not isinstance(inferred,(list,tuple,set)): inferred=[]
    result = []
    for item in list(values) + list(inferred):
        if isinstance(item, (list, tuple)): item = item[0] if item else ''
        if isinstance(item, str) and item.strip(): result.append(item.strip())
    return list(dict.fromkeys(result))


def filter_tracks(tracks, opts, *, playlists=None, liked=(), profiles=None):
    """All active filters intersect. Multi-valued genre/artist tags match exactly."""
    selected = None
    if opts['playlist']:
        pl = (playlists or {}).get(opts['playlist'], {})
        rows = pl.get('tracks', []) if isinstance(pl, dict) else []
        selected = {str(t.get('path') or t.get('url') or '') if isinstance(t, dict) else str(t) for t in rows}
    favorites = set(liked or ())
    out = []
    for track in tracks:
        key = str(track.get('path') or track.get('url') or '')
        if selected is not None and key not in selected: continue
        if opts['liked_only'] and key not in favorites: continue
        if opts['genre'] and opts['genre'].casefold() not in {g.casefold() for g in genres(track, profiles)}: continue
        artists = track.get('artists') or [track.get('artist') or '']
        if isinstance(artists, str): artists = [artists]
        artists = [a.get('name', '') if isinstance(a, dict) else str(a) for a in artists]
        if opts['artist'] and opts['artist'].casefold() not in {a.casefold() for a in artists + [str(track.get('artist') or '')]}: continue
        if opts['album'] and opts['album'].casefold() != str(track.get('album') or '').casefold(): continue
        seconds = duration(track)
        if opts['min_seconds'] and seconds < opts['min_seconds']: continue
        if opts['max_seconds'] and (not seconds or seconds > opts['max_seconds']): continue
        out.append(track)
    return out


FIRE_LEVELS=(5,10,20,35,50,100)

def fire_tier(wins):
    return sum(wins >= threshold for threshold in FIRE_LEVELS)


def open_settings(page):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
                                QCheckBox, QComboBox, QSpinBox, QPushButton)
    from echo_glass import GlassFrame, glass_controls_qss
    win = page.win
    t = lambda ru, en: tr(win, ru, en)
    dialog = QDialog(win); dialog.setWindowTitle(t('Настройки дуэли', 'Duel settings'))
    dialog.resize(660, 690); dialog.setMinimumSize(560, 580); dialog.setSizeGripEnabled(True)
    dialog.setStyleSheet('QDialog{background:#090b10;}QLabel,QCheckBox{color:#edf1fa;background:transparent;}'
                        'QComboBox,QSpinBox{color:#edf1fa;padding:7px;min-height:22px;}' + glass_controls_qss())
    layout = QVBoxLayout(dialog); glass = GlassFrame(radius=22); layout.addWidget(glass)
    body = QVBoxLayout(glass); body.setContentsMargins(24, 22, 24, 22); body.setSpacing(12)
    title = QLabel(t('Твоя дуэль, твои правила', 'Your duel, your rules')); title.setStyleSheet('font-size:23px;font-weight:800;'); body.addWidget(title)
    hint = QLabel(t('Фильтры применяются вместе к следующей дуэли. Текущий турнир останется до нового старта.',
                    'Filters apply together to the next duel. Your current tournament stays until you start again.'))
    hint.setWordWrap(True); body.addWidget(hint)
    opts = options(win.settings)
    chorus = QCheckBox(t('Начинать прослушивание с припева', 'Start previews at the chorus')); chorus.setChecked(opts['chorus']); body.addWidget(chorus)
    note = QLabel(t('Анализ в фоне, результат сохраняется. Если припев не найден — яркий фрагмент; без анализа — начало трека.',
                    'Background analysis is cached. If no chorus is found, use a highlight; without analysis, use the track beginning.'))
    note.setWordWrap(True); note.setStyleSheet('color:#9da9bc;font-size:11px;'); body.addWidget(note)
    fire = QCheckBox(t('Огненная аура за победы', 'Fire aura for wins')); fire.setChecked(opts['fire']); body.addWidget(fire)
    legend = QLabel(t('Уровни: 5 — изумрудный · 10 — синий · 20 — фиолетовый · 35 — розовый · 50 — огненный · 100 — ледяной. До 5 побед огня нет.',
                      'Levels: 5 — emerald · 10 — blue · 20 — violet · 35 — pink · 50 — flame · 100 — ice. No fire before 5 wins.'))
    legend.setWordWrap(True); legend.setStyleSheet('color:#9da9bc;font-size:11px;'); body.addWidget(legend)
    form = QFormLayout(); form.setSpacing(10); combos = {}
    library = win.library; profiles = getattr(page.shell, '_profiles', None) or {}
    choices = {'genre': sorted({g for track in library for g in genres(track, profiles)}, key=str.casefold),
               'artist': sorted({str(track.get('artist') or '') for track in library} - {''}, key=str.casefold),
               'album': sorted({str(track.get('album') or '') for track in library} - {''}, key=str.casefold),
               'playlist': sorted(n for n in win.playlists if not n.startswith('\u2063'))}
    for key, ru, en in [('genre','Жанр','Genre'),('artist','Исполнитель','Artist'),('album','Альбом','Album'),('playlist','Плейлист','Playlist')]:
        combo = QComboBox(); combo.addItem(t('Все', 'All'), '')
        for value in choices[key]: combo.addItem(value, value)
        if opts[key] and combo.findData(opts[key]) < 0: combo.addItem(opts[key], opts[key])
        combo.setCurrentIndex(max(0, combo.findData(opts[key]))); combos[key] = combo; form.addRow(t(ru,en), combo)
    low, high = QSpinBox(), QSpinBox()
    for spin, key in [(low,'min_seconds'),(high,'max_seconds')]:
        spin.setRange(0,7200); spin.setSpecialValueText(t('Без ограничения','No limit')); spin.setSuffix(t(' сек',' sec')); spin.setValue(opts[key])
    form.addRow(t('Длительность от','Duration from'),low); form.addRow(t('Длительность до','Duration to'),high); body.addLayout(form)
    liked = QCheckBox(t('Только избранные треки','Liked tracks only')); liked.setChecked(opts['liked_only']); body.addWidget(liked)
    count = QLabel(); body.addWidget(count)
    def values():
        return dict(chorus=chorus.isChecked(),fire=fire.isChecked(),liked_only=liked.isChecked(),
                    min_seconds=low.value(),max_seconds=high.value(),**{k:c.currentData() for k,c in combos.items()})
    def update_count():
        n = len(filter_tracks(library,values(),playlists=win.playlists,liked=liked_paths(win),profiles=profiles))
        count.setText(t('Подходящих треков: ', 'Matching tracks: ') + str(n))
    for c in combos.values(): c.currentIndexChanged.connect(update_count)
    low.valueChanged.connect(update_count); high.valueChanged.connect(update_count); liked.toggled.connect(update_count); update_count()
    row = QHBoxLayout(); reset = QPushButton(t('Сбросить','Reset')); row.addWidget(reset); row.addStretch()
    cancel = QPushButton(t('Отмена','Cancel')); cancel.clicked.connect(dialog.reject); row.addWidget(cancel)
    save = QPushButton(t('Сохранить','Save')); save.setObjectName('AccentBtn'); row.addWidget(save); body.addLayout(row)
    def defaults():
        chorus.setChecked(True);fire.setChecked(True);liked.setChecked(False);low.setValue(0);high.setValue(0)
        for combo in combos.values():combo.setCurrentIndex(0)
    reset.clicked.connect(defaults)
    def apply():
        if high.value() and low.value()>high.value():
            count.setText(t('Минимальная длительность больше максимальной.','Minimum duration exceeds maximum.'));return
        win.settings['duel_options']=values();win.save_settings();page.options_changed();dialog.accept()
    save.clicked.connect(apply)
    dialog.open(); page._settings_dialog=dialog
    return dialog
