"""Стиль ника, мини-профиль и значки ачивок: чистка данных из сети, кириллица в каждом шрифте, отрисовка.

python qa/test_nick_miniprof.py [папка-для-картинок]
"""
import base64
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFontMetricsF, QImage, QPainter, QPixmap, QRawFont
from PyQt6.QtWidgets import QApplication

app = QApplication(sys.argv)
import ach_icons
import net_miniprof
import net_nick

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else None
fails = []


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails.append(msg)


# 1. данные из сети: только известные ключи и цвета
bad = {"font": "<script>", "fx": "evil", "c1": "red;background:url(x)", "c2": "#12345"}
check(net_nick.clean(bad) == {}, "чужой мусор в стиле ника отброшен")
good = {"font": "pixel", "fx": "neon", "c1": "#ff00aa", "c2": "#00ffaa", "x": 1}
check(net_nick.clean(good) == {"font": "pixel", "fx": "neon", "c1": "#ff00aa", "c2": "#00ffaa"}, "свой стиль сохранён")
mp = net_miniprof.clean({"pron": "  он / его  " * 9, "about": "x" * 500, "banner": "AAAA" * 100, "ach": ["a"] * 9,
                         "bc1": "#ff0000"})
check(len(mp.get("pron", "")) <= 40 and len(mp.get("about", "")) <= 190 and "banner" not in mp
      and len(mp["ach"]) == 6, "мини-профиль из сети обрезан и без поддельного баннера")

# 2. у каждого шрифта есть буквы для кириллицы (сам или парный)
net_nick._load_fonts()
fd = net_nick.FONT_DIR
for k, title, lat, cyr in net_nick.FONTS[1:]:
    files = {f.stem.split("[")[0].split("-")[0].lower(): f for f in fd.glob("*.ttf")}
    pair = next((f for f in fd.glob("*.ttf") if QRawFont(str(f), 20).familyName() == cyr), None)
    latin = next((f for f in fd.glob("*.ttf") if QRawFont(str(f), 20).familyName() == lat), None)
    check(latin is not None, f"шрифт «{title}» ({lat}) на месте")
    check(pair is not None and all(QRawFont(str(pair), 20).supportsCharacter(ord(c)) for c in "ЖжЯяЁёЩщ"),
          f"«{title}»: кириллица из {cyr}")

# 3. ник рисуется (каждый эффект что-то закрашивает)
for fx, title in net_nick.EFFECTS:
    img = QImage(300, 60, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    net_nick.paint_nick(p, QRectF(0, 0, 300, 60), "Эхо Echo", {"font": "bangers", "fx": fx, "c1": "#ff5c8a",
                                                                "c2": "#38c6ff"}, 28, 1.0)
    p.end()
    painted = sum(1 for x in range(0, 300, 3) for y in range(0, 60, 3) if img.pixelColor(x, y).alpha() > 40)
    check(painted > 40, f"эффект «{title}» рисует ник ({painted} точек)")

# 4. значки ачивок — все разные
seen = set()
for k in ach_icons.DRAW:
    img = ach_icons.pixmap(k, 32).toImage()
    sig = bytes(img.constBits().asstring(img.sizeInBytes()))
    check(sig not in seen and any(img.pixelColor(x, y).alpha() for x in range(0, 64, 2) for y in range(0, 64, 2)),
          f"значок ачивки {k} свой")
    seen.add(sig)

# 5. картинки для глаз
if OUT:
    OUT.mkdir(parents=True, exist_ok=True)
    dlg = net_nick.NickStyleDialog("Эхо Echoes", {"font": "chicle", "fx": "gradient", "c1": "#ff5c8a", "c2": "#7c5cff"})
    dlg.resize(780, 640)
    dlg.show()
    app.processEvents()
    dlg.grab().save(str(OUT / "nick_dialog.png"))
    names = {k: (k, "") for k in ach_icons.DRAW}
    pm = QPixmap(76, 76)
    pm.fill(QColor("#7c5cff"))
    raw = QImage(640, 220, QImage.Format.Format_RGB32)
    raw.fill(QColor("#ff8a3d"))
    from net_showcase import jpeg_b64
    info = {"name": "Ропим", "deco": {"nick": {"font": "pixel", "fx": "neon", "c1": "#38c6ff", "c2": "#7c5cff"}},
            "mp": {"pron": "он/его", "about": "слушаю всё подряд, особенно ночью", "bc1": "#5b3cff", "bc2": "#ff5fa2",
                   "ach": ["night_owl", "dj", "collector"]},
            "ach": ["night_owl", "dj", "collector", "streak7"], "status": "слушает Artist — Song",
            "avatar": pm, "ach_names": names}
    card = net_miniprof.MiniCard(info, popup=False)
    card.show()
    app.processEvents()
    card.grab().save(str(OUT / "mini_card.png"))
    grid = QPixmap(6 * 56, 3 * 56)
    grid.fill(QColor("#151518"))
    p = QPainter(grid)
    for i, k in enumerate(ach_icons.DRAW):
        ach_icons.paint_ach(p, k, QRectF(8 + (i % 6) * 56, 8 + (i // 6) * 56, 40, 40), "#ffdb1a")
    p.end()
    grid.save(str(OUT / "ach_icons.png"))
    print("картинки:", OUT)

print("FAILED" if fails else "ALL OK", len(fails))
sys.exit(1 if fails else 0)
