"""Bounded, event-driven micro-interactions shared by all ECHOES themes.
No screenshots, blur buffers or per-button graphics effects are retained.
"""
from __future__ import annotations
from PyQt6.QtGui import QColor, QPixmapCache, QImageReader

PROFILES = {
    'vivid': ('Живой', 1.0, .85, 144, 24),
    'balanced': ('Сбалансированный', .65, 1.0, 90, 16),
    'eco': ('Экономный', .35, .8, 60, 8),
    'reduced': ('Минимум движения', 0.0, .35, 60, 8),
}
_WIN = None


def profile(win=None):
    win = win or _WIN
    settings = getattr(win, 'settings', {})
    key = settings.get('motion_profile', 'vivid')
    return PROFILES.get(key, PROFILES['vivid'])


def strength(win=None):
    win = win or _WIN
    settings = getattr(win, 'settings', {})
    if not settings.get('ui_animations', True):
        return 0.0
    return profile(win)[1] * max(0., min(1.5, float(settings.get('motion_intensity', 1.0))))


def duration(milliseconds, win=None):
    win = win or _WIN
    speed = max(.5, min(2., float(getattr(win, 'settings', {}).get('motion_speed', 1.0))))
    return max(45, round(milliseconds * profile(win)[2] / speed))


def accent(win):
    if getattr(win, '_wa_on', False):
        return QColor('#7dff59')
    if getattr(win, '_osu_on', False):
        return QColor('#ff78b9')
    if getattr(win, '_daw_on', False):
        return QColor('#ffad4b')
    if getattr(win, '_ya_on', False):
        return QColor('#ffdb4d')
    if getattr(win, '_fluid_layout_on', False):
        return QColor('#aa7355')
    return QColor(getattr(getattr(win, 'vinyl', None), 'accent', '#a78bfa'))


def configure(win):
    import frameclock
    frameclock.configure(win.settings, win)
    QPixmapCache.setCacheLimit(profile(win)[4] * 1024)
    # Bound individual image decoding, independently of the image caches.
    if hasattr(QImageReader, 'setAllocationLimit'):
        QImageReader.setAllocationLimit(128)
    try:
        import yamusic_theme
        yamusic_theme._COVER_BUDGET = (12 if win.settings.get('motion_profile') in ('eco','reduced') else 24) * 2**20
        yamusic_theme.prune_cover_cache()
    except ImportError:
        pass
    import ui_anim
    ui_anim.install_effects(win)


def install_animation_policy():
    """Retime Python-started Qt animations, including children of animation groups.
    Each animation stores its own unscaled duration: restarting cannot compound it.
    Theme-specific easing curves and audio/timer clocks remain untouched.
    """
    from PyQt6.QtCore import QAbstractAnimation, QVariantAnimation, QAnimationGroup
    if getattr(QAbstractAnimation.start, '_echoes_motion', False):
        return
    original = QAbstractAnimation.start
    def retime(animation):
        if isinstance(animation, QAnimationGroup):
            for index in range(animation.animationCount()):
                retime(animation.animationAt(index))
        elif isinstance(animation, QVariantAnimation):
            current = animation.duration()
            if current <= 0:
                return
            if current != getattr(animation, '_echoes_applied_duration', None):
                animation._echoes_base_duration = current
            base = getattr(animation, '_echoes_base_duration', current)
            value = duration(base) if strength() > 0 else 1
            animation.setDuration(value)
            animation._echoes_applied_duration = value
    def start(animation, *args, **kwargs):
        retime(animation)
        return original(animation, *args, **kwargs)
    start._echoes_motion = True
    QAbstractAnimation.start = start


def install(win):
    """Подсветки/вспышки поверх кнопок и строк при наведении и клике убраны по просьбе пользователя
    (жёлтые рамки, «залипавшие» поверх интерфейса); остаются только длительности анимаций тем."""
    global _WIN
    _WIN=win
    install_animation_policy()
    configure(win)
