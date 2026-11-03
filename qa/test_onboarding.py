"""Первый запуск: мастер показывается новичку с пустой коллекцией (и не показывается остальным), шаги
переключаются, «перенести плейлист» открывает нужный раздел Настроек, выбранная тема применяется.

python qa/test_onboarding.py [папка-для-картинок]
"""
import sys
from pathlib import Path

source = Path(__file__).with_name('ui_smoke.py').read_text('utf-8')
exec(compile(source.split('win = None')[0], str(Path(__file__).with_name('ui_smoke.py')), 'exec'))
import main
from PyQt6.QtWidgets import QLabel

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else None
main.WindowsMediaKeyFilter.register = lambda self: None
fails = []


def check(cond, msg):
    print(('ok   ' if cond else 'FAIL ') + msg, flush=True)
    if not cond:
        fails.append(msg)


win = main.MainWindow()
win.resize(1400, 900)
win.show(); settle(.5)
import onboarding
win.library = [{"path": "x.mp3", "title": "x"}]
win.settings.pop("onboarded", None)
check(not onboarding.needed(win) and win.settings.get("onboarded"), "с музыкой мастер не нужен")
win.library = []
win.settings.pop("onboarded", None)
check(onboarding.needed(win), "новичку с пустой коллекцией — нужен")
ob = onboarding.Onboarding(win)
ob.show(); settle(.3)


def title():
    return next((l.text() for l in ob.card.findChildren(QLabel) if l.objectName() == "H" and l.isVisible()), "")


check("Добро пожаловать" in title(), "шаг 1: " + title())
if OUT:
    OUT.mkdir(parents=True, exist_ok=True)
    win.grab().save(str(OUT / "onb_1.png"))
ob._go(1); settle(.2)
check("плейлист" in title().lower(), "шаг 2: " + title())
if OUT:
    win.grab().save(str(OUT / "onb_2.png"))
ob._go(2); settle(.2)
check("выглядеть" in title(), "шаг 3: " + title())
ob._pick_theme("Океан"); settle(.1)
if OUT:
    win.grab().save(str(OUT / "onb_3.png"))
ob.finish(user=True); settle(1.0)
check(win.settings.get("onboarded") and (win.settings.get("ya_skin") or {}).get("accent") == "#2fc6ff",
      "готово: мастер пройден, тема «Океан» применена")
ob2 = onboarding.Onboarding(win); ob2.show(); settle(.2)
ob2._go(1); settle(.1)
ob2._transfer(); settle(.6)
import settings_inline as si
shell = win._ya
opened = [s for s in shell.settings_hub.findChildren(si.Section)
          if s.findChild(QLabel, "InlineTitle").text().startswith("Перенести плейлист") and s.is_open()]
check(bool(opened) and shell._cur_page == "settings", "«Перенести плейлист» открыл нужный раздел Настроек")
print("FAILED" if fails else "ALL OK", len(fails), flush=True)
win.close()
sys.exit(1 if fails else 0)
