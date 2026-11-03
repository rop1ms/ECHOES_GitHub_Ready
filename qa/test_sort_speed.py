"""Сортировка в коллекции и плейлистах (и что дальше играет то, что ниже в списке) + «Экстремальная скорость».

python qa/test_sort_speed.py [папка-для-картинок]
"""
import sys
from pathlib import Path

source = Path(__file__).with_name('ui_smoke.py').read_text('utf-8')
exec(compile(source.split('win = None')[0], str(Path(__file__).with_name('ui_smoke.py')), 'exec'))
import main, ui_anim

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else None
main.WindowsMediaKeyFilter.register = lambda self: None
fails = []


def check(cond, msg):
    print(('ok   ' if cond else 'FAIL ') + msg, flush=True)
    if not cond:
        fails.append(msg)


win = main.MainWindow()
win.resize(1500, 950)
win.show(); settle(.5)
shell = win._ya
names = [("Zeta", "Bravo", 300), ("Alpha", "Charlie", 120), ("Mike", "Alpha", 200), ("Трек 10", "Delta", 90),
         ("Трек 2", "Delta", 240)]
win.library = [{"path": str(audit_home / f"{i}.wav"), "title": t, "artist": a, "duration": d, "album": a + " LP"}
               for i, (t, a, d) in enumerate(names)]
win.playlists = {"Мой": {"tracks": [dict(x) for x in win.library]}}
shell._dirty.add("collection"); shell.show_page("collection"); ui_anim._finish(shell.stack); settle(.3)


def coll_titles():
    return [r["t"]["title"] for r in shell.c_list.model_.rows]


check(coll_titles()[0] == "Трек 2", "по умолчанию — новые сверху")
shell._set_coll_sort("title"); settle(.1)
check(coll_titles() == ["Alpha", "Mike", "Zeta", "Трек 2", "Трек 10"], "по названию (числа по-человечески): " + ", ".join(coll_titles()))
shell._set_coll_sort("artist"); settle(.1)
check([r["t"]["artist"] for r in shell.c_list.model_.rows][:2] == ["Alpha", "Bravo"], "по исполнителю")
shell._set_coll_sort("duration"); settle(.1)
check(coll_titles()[0] == "Трек 10" and coll_titles()[-1] == "Zeta", "по длительности")
shell._set_coll_sort("reverse"); settle(.1)
check(coll_titles()[0] == "Zeta", "сначала старые")
check("Сначала старые" in shell.c_sort.text(), "кнопка показывает выбранное: " + shell.c_sort.text().strip())

shell._set_coll_sort("title"); settle(.1)
rows = shell.c_list.model_.rows
shell._remember_order(rows[0])
order = win._view_order.get("library")
check(order == [r["i"] for r in rows], "порядок на экране запомнен для воспроизведения")
win.queue_context, win.current_index = "library", rows[0]["i"]
check(win._step_index("library", rows[0]["i"], 1, len(win.library)) == rows[1]["i"], "следующий — тот, что ниже в списке")
up, idx = win._upcoming_tracks(3)
check(idx == [r["i"] for r in rows[1:4]], "очередь «дальше» — по списку")
check(win._step_index("library", rows[-1]["i"], 1, len(win.library)) == -1 or win.repeat_mode == 1,
      "после последнего — конец (без повтора)")

shell.open_playlist("Мой"); settle(.2)
check(shell.pp_sort.isVisible(), "у плейлиста есть сортировка")
shell._set_pl_sort("duration"); settle(.2)
check([r["t"]["title"] for r in shell.pp_list.model_.rows][0] == "Трек 10", "плейлист отсортирован по длительности")
check((win.settings.get("sort_pl") or {}).get("Мой") == "duration", "сортировка плейлиста запомнена")

for rate in (4.0, 0.25, 1.0):
    win.set_play_speed(rate)
    check(abs(win.engine.speed - rate) < 1e-6 and win.settings["speed"] == int(rate * 100),
          f"экстремальная скорость {rate}×")
from ya_sound_panel import SoundPanel
panel = SoundPanel(shell, embedded=True)
panel.resize(360, 760); panel.show()
win.set_play_speed(4.0); panel.sync(); settle(.2)
check(panel.speed.value() == 100 and panel.speed_btns[4.0].isChecked(), "ползунок и кнопка 4× в панели")
panel.speed.setValue(-100); settle(.1)
check(abs(win.engine.speed - 0.25) < 1e-6, "ползунок влево до упора — 0.25×")
if OUT:
    OUT.mkdir(parents=True, exist_ok=True)
    panel.setStyleSheet(panel.styleSheet() + "QFrame#SndPanel{background:#0d0d10;}")
    panel.grab().save(str(OUT / "speed_panel.png"))
    shell.show_page("collection"); ui_anim._finish(shell.stack); settle(.3)
    win.grab().save(str(OUT / "collection_sort.png"))
win.set_play_speed(1.0)
print("FAILED" if fails else "ALL OK", len(fails), flush=True)
win.close()
sys.exit(1 if fails else 0)
