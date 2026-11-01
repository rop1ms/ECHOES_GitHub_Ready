"""Application-wide logical and text scale, loaded before constructing widgets."""
import json
import os
import re
from pathlib import Path


def settings(path):
    try:
        return json.loads(Path(path).read_text('utf-8'))
    except (OSError, ValueError):
        return {}


def factor(value):
    try:
        return max(.75, min(2.5, float(value)))
    except (TypeError, ValueError):
        return 1.0


def configure_environment(path):
    os.environ['QT_SCALE_FACTOR'] = str(factor(settings(path).get('ui_scale', 1)))


def install_font_scale(app, path):
    if getattr(app, '_echoes_font_scale_installed', False):
        return
    app._echoes_font_scale_installed = True
    scale = factor(settings(path).get('font_scale', 1))
    if scale == 1:
        return
    from PyQt6.QtWidgets import QWidget, QApplication
    from PyQt6.QtGui import QFont
    original_style = QWidget.setStyleSheet
    original_app_style = QApplication.setStyleSheet
    original_pixel = QFont.setPixelSize
    original_point = QFont.setPointSize
    original_point_f = QFont.setPointSizeF
    original_init = QFont.__init__
    def init_font(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        # Explicit point sizes in painted themes bypass setPointSize in Qt.
        explicit = args[1] if len(args) > 1 and isinstance(args[0], str) else kwargs.get('pointSize', -1)
        if isinstance(explicit, (int, float)) and explicit > 0:
            original_point_f(self, self.pointSizeF()*scale)
    QFont.__init__ = init_font
    def qss(text):
        return re.sub(r'(font-size\s*:\s*)(\d+(?:\.\d+)?)(px|pt)(?!/\*echoes-font-scale\*/)',
                      lambda m: m[1] + str(round(float(m[2])*scale, 2)) + m[3] + '/*echoes-font-scale*/', text)
    QWidget.setStyleSheet = lambda self, text: original_style(self, qss(text))
    QApplication.setStyleSheet = lambda self, text: original_app_style(self, qss(text))
    QFont.setPixelSize = lambda self, value: original_pixel(self, max(1, round(value*scale)))
    QFont.setPointSize = lambda self, value: original_point(self, max(1, round(value*scale)))
    QFont.setPointSizeF = lambda self, value: original_point_f(self, max(1., value*scale))
    font = app.font()
    original_point_f(font, max(1., font.pointSizeF()*scale))
    app.setFont(font)
