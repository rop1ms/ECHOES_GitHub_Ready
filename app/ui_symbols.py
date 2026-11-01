"""Small, original monochrome UI drawings. Reactions keep using emoji_pack.

The bundled outline font lets the same drawings work in native controls, rich
text and custom-painted overlays without rasterising a new icon every frame.
Only explicit UI literals use these private-use characters: messages, names,
reaction values and radio protocol markers are never rewritten at runtime.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (QColor, QFontDatabase, QIcon, QPainter, QPainterPath,
                         QPainterPathStroker, QPixmap)

FAMILY = "ECHOES Symbols"
# Stable indices are part of the source/font contract; append new entries.
NAMES = ("trophy", "computer", "lock", "game", "gift", "shield", "radio",
         "microphone", "pin", "mute", "headphones", "phone", "globe", "people", "sun", "envelope",
         "next", "previous", "play_pause", "pause", "down")
ORIGINALS = ("🏆", "💻", "🔒", "🎮", "🎁", "🛡", "📻", "🎙", "📌", "🔇", "🎧", "📞", "🌐", "👥", "☀", "✉",
             "⏭", "⏮", "⏯", "⏸", "⬇")
GLYPHS = {name: chr(0xF1000 + i) for i, name in enumerate(NAMES)}
TO_ORIGINAL = {GLYPHS[n]: e for n, e in zip(NAMES, ORIGINALS)}
_font_id = None


def symbol(name: str) -> str:
    return GLYPHS[name]


def source_text(text: str) -> str:
    """Restore icon spellings for the existing translation catalogue only."""
    if not isinstance(text, str) or not any(0xF1000 <= ord(c) < 0xF1000 + len(NAMES) for c in text):
        return text
    return "".join(TO_ORIGINAL.get(c, c) for c in text)


def translated_symbols(source: str, translated: str) -> str:
    """Retain explicit UI drawings after translation; leave user emoji alone."""
    for glyph, original in TO_ORIGINAL.items():
        count = source.count(glyph)
        if count:
            translated = translated.replace(original, glyph, count)
    return translated


def install(app=None):
    """Register once, after QApplication construction and before UI creation."""
    global _font_id
    if _font_id is not None:
        return
    font_path = Path(__file__).with_name("assets") / "EchoesSymbols.ttf"
    font_id = QFontDatabase.addApplicationFont(str(font_path))
    if font_id < 0:
        raise RuntimeError(f"Cannot load ECHOES UI drawings: {font_path}")
    _font_id = font_id
    # Qt 6.8+: prefer our deliberately isolated codepoints for every text font.
    # Earlier Qt versions discover the registered font through normal fallback.
    if hasattr(QFontDatabase, "addApplicationFallbackFontFamily"):
        from PyQt6.QtCore import QChar
        QFontDatabase.addApplicationFallbackFontFamily(QChar.Script.Script_Common, FAMILY)


def outline(name: str) -> QPainterPath:
    """Original 24-unit line drawings, also used to rebuild the bundled font."""
    path = QPainterPath()

    def line(*xy):
        path.moveTo(xy[0], xy[1])
        for i in range(2, len(xy), 2):
            path.lineTo(xy[i], xy[i + 1])

    def rect(x, y, w, h, radius=0):
        path.addRoundedRect(QRectF(x, y, w, h), radius, radius)

    def circle(x, y, r):
        path.addEllipse(QPointF(x, y), r, r)

    if name == "trophy":
        line(7, 4, 17, 4, 16, 11, 14, 14, 10, 14, 8, 11, 7, 4)
        path.moveTo(7, 6); path.cubicTo(0, 4, 3, 13, 9, 11)
        path.moveTo(17, 6); path.cubicTo(24, 4, 21, 13, 15, 11)
        line(12, 14, 12, 19); line(8, 20, 16, 20)
    elif name == "computer":
        rect(5, 4, 14, 11, 1.4)
        line(5, 15, 2.5, 19, 21.5, 19, 19, 15)
        line(10, 17, 14, 17)
    elif name == "lock":
        rect(5, 10, 14, 11, 2)
        path.moveTo(8, 10); path.lineTo(8, 6); path.cubicTo(8, 1, 16, 1, 16, 6); path.lineTo(16, 10)
        line(12, 14, 12, 17)
    elif name == "game":
        path.moveTo(8, 7); path.lineTo(16, 7)
        path.cubicTo(20, 6, 21, 10, 22, 16)
        path.cubicTo(22.5, 21, 18, 20, 16, 16)
        path.lineTo(8, 16); path.cubicTo(6, 20, 1.5, 21, 2, 16)
        path.cubicTo(3, 10, 4, 6, 8, 7)
        line(6, 12, 10, 12); line(8, 10, 8, 14)
        circle(16.5, 11, 0.25); circle(18.5, 13, 0.25)
    elif name == "gift":
        rect(3, 8, 18, 4, 0.5); rect(5, 12, 14, 9, 0.5)
        line(12, 8, 12, 21)
        path.moveTo(12, 8); path.cubicTo(0, 9, 6, -2, 12, 8)
        path.cubicTo(18, -2, 24, 9, 12, 8)
    elif name == "shield":
        path.moveTo(12, 2); path.lineTo(21, 6); path.lineTo(20, 13)
        path.cubicTo(19, 17, 15, 20, 12, 22)
        path.cubicTo(9, 20, 5, 17, 4, 13); path.lineTo(3, 6); path.closeSubpath()
        line(8, 11, 11, 14, 16, 9)
    elif name == "radio":
        rect(3, 8, 18, 13, 2); line(5, 8, 17, 2)
        circle(8, 15, 3); line(14, 12, 18, 12); line(14, 16, 18, 16)
    elif name == "microphone":
        rect(9, 2, 6, 12, 3)
        path.moveTo(5, 10); path.cubicTo(5, 21, 19, 21, 19, 10)
        line(12, 18, 12, 22); line(8, 22, 16, 22)
    elif name == "pin":
        line(8, 3, 16, 3); line(9, 3, 9, 9, 5, 14, 19, 14, 15, 9, 15, 3)
        line(12, 14, 12, 22)
    elif name == "mute":
        line(10, 8, 6, 11, 3, 11, 3, 16, 6, 16, 11, 20, 11, 12)
        path.moveTo(15, 7); path.cubicTo(18, 9, 19, 13, 18, 16)
        line(3, 3, 21, 21)
    elif name == "headphones":
        path.moveTo(4, 15); path.lineTo(4, 11); path.cubicTo(4, 0, 20, 0, 20, 11); path.lineTo(20, 15)
        rect(3, 12, 5, 9, 2); rect(16, 12, 5, 9, 2)
    elif name == "phone":
        path.moveTo(4, 3); path.lineTo(8, 2); path.lineTo(11, 8); path.lineTo(8, 10)
        path.cubicTo(10, 13, 11, 14, 14, 16)
        path.lineTo(16, 13); path.lineTo(22, 16); path.lineTo(21, 20)
        path.cubicTo(15, 26, -2, 9, 4, 3)
    elif name == "globe":
        circle(12, 12, 9)
        path.addEllipse(QRectF(8, 3, 8, 18))
        line(3, 12, 21, 12); line(5, 7, 19, 7); line(5, 17, 19, 17)
    elif name == "people":
        circle(9, 7, 3); circle(17, 8, 2.5)
        path.moveTo(2, 20); path.cubicTo(2, 10, 16, 10, 16, 20)
        path.moveTo(17, 13); path.cubicTo(21, 13, 22, 17, 22, 20)
        line(2, 20, 16, 20)
    elif name == "sun":
        import math
        circle(12, 12, 4)
        for i in range(8):
            a = i * math.pi / 4
            line(12 + 7 * math.cos(a), 12 + 7 * math.sin(a),
                 12 + 10 * math.cos(a), 12 + 10 * math.sin(a))
    elif name == "envelope":
        rect(2, 5, 20, 14, 2)
        line(3, 6, 12, 13, 21, 6)
    elif name in ("next", "previous"):
        if name == "next":
            line(5, 4, 16, 12, 5, 20, 5, 4); line(19, 4, 19, 20)
        else:
            line(19, 4, 8, 12, 19, 20, 19, 4); line(5, 4, 5, 20)
    elif name == "play_pause":
        line(3, 4, 13, 12, 3, 20, 3, 4)
        line(17, 5, 17, 19); line(21, 5, 21, 19)
    elif name == "pause":
        line(8, 4, 8, 20); line(16, 4, 16, 20)
    elif name == "down":
        line(12, 3, 12, 21); line(5, 14, 12, 21, 19, 14)
    else:
        raise KeyError(name)
    stroker = QPainterPathStroker()
    stroker.setWidth(1.65)
    stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
    stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return stroker.createStroke(path).simplified()


@lru_cache(maxsize=256)
def icon(name: str, size: int = 20, color: str = "#dedee7") -> QIcon:
    """Cached transparent QIcon for new controls; no emoji/font dependency."""
    size = max(8, min(256, int(size)))
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.GlobalColor.transparent)
    pm.setDevicePixelRatio(2)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 26, size / 26)
    painter.translate(1, 1)
    painter.fillPath(outline(name), QColor(color))
    painter.end()
    return QIcon(pm)
