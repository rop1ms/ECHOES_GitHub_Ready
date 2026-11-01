"""Rebuild original UI drawings as a compact outline font (development only)."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from PyQt6.QtGui import QTransform
import ui_symbols as S


def build():
    names = [".notdef", *S.NAMES]
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder(names)
    fb.setupCharacterMap({ord(S.symbol(n)): n for n in S.NAMES})
    glyphs = {}
    for name in names:
        pen = TTGlyphPen(None)
        if name != ".notdef":
            # Flatten once at a high resolution; runtime draws normal glyphs.
            transform = QTransform().scale(1000 / 26, -1000 / 26)
            transform.translate(1, -21)
            for poly in S.outline(name).toSubpathPolygons(transform):
                pts = [(round(p.x()), round(p.y())) for p in poly]
                if len(pts) < 3:
                    continue
                pen.moveTo(pts[0])
                for pt in pts[1:]:
                    pen.lineTo(pt)
                pen.closePath()
        glyphs[name] = pen.glyph()
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics({n: (1000, 40) for n in names})
    fb.setupHorizontalHeader(ascent=850, descent=-150)
    fb.setupNameTable({"familyName": S.FAMILY, "styleName": "Regular",
                       "uniqueFontIdentifier": "ECHOES Symbols 1.0",
                       "fullName": S.FAMILY, "psName": "EchoesSymbols-Regular",
                       "version": "Version 1.0", "copyright": "Original ECHOES UI drawings, 2026"})
    fb.setupOS2(sTypoAscender=850, sTypoDescender=-150, usWinAscent=950, usWinDescent=200)
    fb.setupPost()
    target = ROOT / "app" / "assets" / "EchoesSymbols.ttf"
    target.parent.mkdir(parents=True, exist_ok=True)
    fb.save(target)
    print(f"Built {target.name}: {target.stat().st_size} bytes, {len(S.NAMES)} drawings")


if __name__ == "__main__":
    build()
