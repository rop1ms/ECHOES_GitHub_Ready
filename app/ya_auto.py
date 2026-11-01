# ya_auto.py
"""
Автоматика: правила «когда → что сделать» (Настройки → Инструменты → «Автоматика», по умолчанию правил нет).

Когда: в определённое время (и дни недели) · запустилась игра · игра закрылась · друг появился в сети
(любой или конкретный) · ECHOES запущен.
Что: включить «Мою волну» / плейлист · громкость N % · тише на N % (вернётся сама, когда игра закроется) ·
пауза · уведомление с текстом.
Готовые примеры: «в 23:00 — спокойное», «в игре — тише», «друг в сети — уведомить».
"""
from __future__ import annotations

import secrets
import time

from PyQt6.QtCore import QObject, QTime, QTimer
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                             QScrollArea, QSpinBox, QTimeEdit, QVBoxLayout, QWidget)

WHEN = {"time": "В определённое время", "game_on": "Запустилась игра", "game_off": "Игра закрылась",
        "friend_on": "Друг появился в сети", "start": "ECHOES запущен"}
DO = {"wave": "Включить «Мою волну»", "playlist": "Включить плейлист", "volume": "Громкость, %",
      "quieter": "Тише на, % (вернётся после игры)", "pause": "Пауза", "notify": "Уведомление"}
DAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


def rules(win) -> list:
    return [r for r in (win.settings.get("auto_rules") or []) if isinstance(r, dict)]


def save_rules(win, lst):
    win.settings["auto_rules"] = lst
    try:
        win.save_settings()
    except Exception:                                          # noqa: BLE001
        pass


def presets(win) -> list:
    calm = next((n for n in getattr(getattr(win, "_ya", None), "user_playlists", lambda: [])()
                 if any(w in n.lower() for w in ("спок", "chill", "relax", "сон", "lofi", "night", "ноч"))), "")
    return [
        ("В 23:00 — спокойное", {"when": {"t": "time", "at": "23:00", "days": list(range(7))},
                                 "do": {"a": "playlist", "name": calm} if calm else {"a": "volume", "v": 35}}),
        ("В игре — тише", {"when": {"t": "game_on"}, "do": {"a": "quieter", "v": 50}}),
        ("Друг в сети — уведомить", {"when": {"t": "friend_on", "onion": ""}, "do": {"a": "notify", "text": ""}}),
    ]


def describe(r, win=None) -> str:
    w, d = r.get("when") or {}, r.get("do") or {}
    t = w.get("t")
    if t == "time":
        days = w.get("days") or []
        ds = "каждый день" if len(days) == 7 else ", ".join(DAYS[i] for i in days if 0 <= i < 7) or "никогда"
        when = f"в {w.get('at', '00:00')} ({ds})"
    elif t == "friend_on":
        nm = w.get("name") or ("любой друг" if not w.get("onion") else w["onion"][:8])
        when = f"когда {nm} в сети" if w.get("onion") else "когда друг появился в сети"
    else:
        when = WHEN.get(t, "?").lower()
    a = d.get("a")
    if a == "playlist":
        what = f"включить «{d.get('name') or '?'}»"
    elif a == "volume":
        what = f"громкость {d.get('v', 50)} %"
    elif a == "quieter":
        what = f"тише на {d.get('v', 50)} %"
    elif a == "notify":
        what = "уведомить" + (f": {d['text']}" if d.get("text") else "")
    else:
        what = DO.get(a, "?").lower()
    return f"{when.capitalize()} → {what}"


class Engine(QObject):
    """Следит за временем (раз в 20 с), игрой (net_game) и друзьями (присутствие Echoes.NET)."""

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.fired = {}                                        # id правила → ключ минуты (не срабатывать дважды)
        self.game = ""
        self.quiet_from = None                                 # громкость до «тише в игре»
        self.online = set()
        self.t = QTimer(self)
        self.t.setInterval(20000)
        self.t.timeout.connect(self.tick)
        self.t.start()
        QTimer.singleShot(5000, lambda: self.fire("start"))

    def _active(self, kind):
        return [r for r in rules(self.win) if r.get("on", True) and (r.get("when") or {}).get("t") == kind]

    def tick(self, now=None):
        lt = time.localtime(now or time.time())
        hm = f"{lt.tm_hour:02d}:{lt.tm_min:02d}"
        for r in self._active("time"):
            w = r["when"]
            key = time.strftime("%Y%m%d%H%M", lt)
            if w.get("at") == hm and lt.tm_wday in (w.get("days") or list(range(7))) and self.fired.get(r.get("id")) != key:
                self.fired[r.get("id")] = key
                self.run(r)
        if self._active("game_on") or self._active("game_off"):
            try:
                import net_game
                g = net_game.detect()
            except Exception:                                  # noqa: BLE001
                g = ""
            self.set_game(g)

    def set_game(self, g):
        if g == self.game:
            return
        was, self.game = self.game, g
        if g and not was:
            self.fire("game_on", game=g)
        elif was and not g:
            if self.quiet_from is not None:                    # «тише в игре» — вернуть громкость
                sl = getattr(self.win, "vol", None)
                if sl is not None:
                    sl.setValue(self.quiet_from)
                self.quiet_from = None
            self.fire("game_off", game=was)

    def friend_presence(self, onion, online, name=""):
        if online and onion not in self.online:
            self.online.add(onion)
            for r in self._active("friend_on"):
                if not r["when"].get("onion") or r["when"]["onion"] == onion:
                    self.run(r, friend=name or onion[:8])
        elif not online:
            self.online.discard(onion)

    def fire(self, kind, **ctx):
        for r in self._active(kind):
            self.run(r, **ctx)

    def run(self, r, **ctx):
        """Execute one shared automation action; return whether it succeeded."""
        d = r.get("do") or {}
        a = d.get("a")
        win = self.win
        sh = getattr(win, "_ya", None)
        try:
            if a == "wave":
                import ya_quick
                ya_quick.wave(win)
            elif a == "playlist" and sh is not None and d.get("name") in win.playlists:
                sh.open_playlist(d["name"])
                sh._pp_play(False)
            elif a == "volume":
                win.vol.setValue(max(0, min(100, int(d.get("v", 50)))))
            elif a == "quieter":
                if self.quiet_from is None:
                    self.quiet_from = win.vol.value()
                    win.vol.setValue(int(self.quiet_from * (100 - max(0, min(100, int(d.get("v", 50))))) / 100))
            elif a == "pause":
                if win.engine.is_playing():
                    win.toggle_play()
            elif a == "notify":
                text = d.get("text") or (f"{ctx['friend']} в сети" if ctx.get("friend") else describe(r))
                net = getattr(sh, "pages", {}).get("net") if sh is not None else None
                if net is not None:
                    net.alert("info", "Автоматика", text, force=True)
                else:
                    win._toast(text)
            else:
                return False
            r_log = win.settings.setdefault("auto_log", [])
            r_log.append([time.time(), describe(r)])
            del r_log[:-30]
            return True
        except Exception as e:                                 # noqa: BLE001
            print("[auto]", e)
            return False


def get(win) -> Engine:
    e = getattr(win, "_auto_engine", None)
    if e is None:
        e = win._auto_engine = Engine(win)
    return e


# ══════════════════════════ окно правил ══════════════════════════ #

class RuleRow(QFrame):
    def __init__(self, dlg, r):
        super().__init__()
        self.dlg, self.r = dlg, r
        self.setObjectName("Rule")
        self.setStyleSheet("QFrame#Rule{background:#1b1b1f;border-radius:12px;}")
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 10)
        top = QHBoxLayout()
        self.on = QCheckBox()
        self.on.setChecked(r.get("on", True))
        self.on.toggled.connect(lambda x: (r.__setitem__("on", bool(x)), dlg.changed()))
        top.addWidget(self.on)
        top.addWidget(QLabel("Когда:"))
        self.wt = QComboBox()
        for k, n in WHEN.items():
            self.wt.addItem(n, k)
        self.wt.setCurrentIndex(list(WHEN).index((r.get("when") or {}).get("t", "time")))
        self.wt.currentIndexChanged.connect(self._when_type)
        top.addWidget(self.wt)
        self.wbox = QHBoxLayout()
        top.addLayout(self.wbox)
        top.addStretch(1)
        rm = QPushButton("Удалить")
        rm.clicked.connect(lambda: dlg.remove(r))
        top.addWidget(rm)
        v.addLayout(top)
        bot = QHBoxLayout()
        bot.addSpacing(22)
        bot.addWidget(QLabel("Что сделать:"))
        self.dt = QComboBox()
        for k, n in DO.items():
            self.dt.addItem(n, k)
        self.dt.setCurrentIndex(list(DO).index((r.get("do") or {}).get("a", "wave")))
        self.dt.currentIndexChanged.connect(self._do_type)
        bot.addWidget(self.dt)
        self.dbox = QHBoxLayout()
        bot.addLayout(self.dbox)
        bot.addStretch(1)
        v.addLayout(bot)
        self._fill_when()
        self._fill_do()

    @staticmethod
    def _clear(lay):
        while lay.count():
            it = lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    def _when_type(self):
        self.r["when"] = {"t": self.wt.currentData()}
        if self.r["when"]["t"] == "time":
            self.r["when"].update(at="23:00", days=list(range(7)))
        self._fill_when()
        self.dlg.changed()

    def _do_type(self):
        self.r["do"] = {"a": self.dt.currentData()}
        self._fill_do()
        self.dlg.changed()

    def _fill_when(self):
        self._clear(self.wbox)
        w = self.r.setdefault("when", {"t": "time"})
        if w["t"] == "time":
            te = QTimeEdit(QTime.fromString(w.get("at", "23:00"), "HH:mm"))
            te.setDisplayFormat("HH:mm")
            te.timeChanged.connect(lambda t: (w.__setitem__("at", t.toString("HH:mm")), self.dlg.changed()))
            self.wbox.addWidget(te)
            days = set(w.setdefault("days", list(range(7))))
            for i, n in enumerate(DAYS):
                b = QPushButton(n)
                b.setCheckable(True)
                b.setChecked(i in days)
                b.setFixedWidth(34)

                def tog(on, i=i):
                    ds = set(w.get("days") or [])
                    (ds.add if on else ds.discard)(i)
                    w["days"] = sorted(ds)
                    self.dlg.changed()
                b.toggled.connect(tog)
                self.wbox.addWidget(b)
        elif w["t"] == "friend_on":
            cb = QComboBox()
            cb.addItem("любой друг", "")
            for o, nm in self.dlg.friends():
                cb.addItem(nm, o)
            i = cb.findData(w.get("onion", ""))
            cb.setCurrentIndex(max(0, i))
            cb.currentIndexChanged.connect(lambda _i: (w.update(onion=cb.currentData(), name=cb.currentText()
                                                                if cb.currentData() else ""), self.dlg.changed()))
            self.wbox.addWidget(cb)

    def _fill_do(self):
        self._clear(self.dbox)
        d = self.r.setdefault("do", {"a": "wave"})
        if d["a"] == "playlist":
            cb = QComboBox()
            for n in self.dlg.playlists():
                cb.addItem(n)
            if d.get("name"):
                cb.setCurrentText(d["name"])
            d["name"] = cb.currentText()
            cb.currentTextChanged.connect(lambda s: (d.__setitem__("name", s), self.dlg.changed()))
            self.dbox.addWidget(cb)
        elif d["a"] in ("volume", "quieter"):
            sp = QSpinBox()
            sp.setRange(0, 100)
            sp.setSuffix(" %")
            sp.setValue(int(d.setdefault("v", 50)))
            sp.valueChanged.connect(lambda x: (d.__setitem__("v", int(x)), self.dlg.changed()))
            self.dbox.addWidget(sp)
        elif d["a"] == "notify":
            le = QLineEdit(d.get("text", ""))
            le.setPlaceholderText("текст (пусто — само)")
            le.textChanged.connect(lambda s: (d.__setitem__("text", s), self.dlg.changed()))
            self.dbox.addWidget(le)


class RulesDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.lst = [dict(r) for r in rules(win)]
        self.setWindowTitle("Автоматика")
        self.resize(820, 560)
        self.setStyleSheet("QDialog{background:#111113;} QLabel{color:#f0f0f0;background:transparent;}"
                           "QLabel#Sub{color:#8a8a8a;font-size:11px;} QCheckBox{color:#f0f0f0;}"
                           "QPushButton{background:#232326;color:#f0f0f0;border:none;border-radius:10px;padding:6px 12px;"
                           "font-weight:700;} QPushButton:hover{background:#2e2e32;} QPushButton:checked{background:#f0f0f0;"
                           "color:#000;} QComboBox, QLineEdit, QSpinBox, QTimeEdit{background:#1d1d20;color:#f0f0f0;"
                           "border:1px solid #2c2c30;border-radius:8px;padding:4px 8px;}"
                           "QScrollArea{background:transparent;border:none;}")
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 16, 20, 16)
        t = QLabel("Автоматика")
        t.setStyleSheet("font-size:22px;font-weight:900;")
        v.addWidget(t)
        s = QLabel("Правила «когда → что сделать». Срабатывают, пока ECHOES открыт (можно свернуть в трей).")
        s.setObjectName("Sub")
        v.addWidget(s)
        pr = QHBoxLayout()
        pr.addWidget(QLabel("Готовые:"))
        for name, rule in presets(win):
            b = QPushButton("+ " + name)
            b.clicked.connect(lambda _=False, rule=rule: self.add(rule))
            pr.addWidget(b)
        pr.addStretch(1)
        v.addLayout(pr)
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.inner = QWidget()
        self.inner.setStyleSheet("background:transparent;")
        self.box = QVBoxLayout(self.inner)
        self.box.setContentsMargins(0, 0, 6, 0)
        self.area.setWidget(self.inner)
        v.addWidget(self.area, 1)
        bot = QHBoxLayout()
        nb = QPushButton("+ Своё правило")
        nb.clicked.connect(lambda: self.add({"when": {"t": "time", "at": "08:00", "days": list(range(5))},
                                             "do": {"a": "wave"}}))
        bot.addWidget(nb)
        bot.addStretch(1)
        ok = QPushButton("Готово")
        ok.setStyleSheet("QPushButton{background:#ffdb1a;color:#000;}")
        ok.clicked.connect(self.accept)
        bot.addWidget(ok)
        v.addLayout(bot)
        self.fill()

    def friends(self):
        try:
            net = self.win._ya.pages["net"]
            return [(c["onion"], c.get("name") or c["onion"][:8]) for c in net.node.store.contacts("friend")]
        except Exception:                                      # noqa: BLE001
            return []

    def playlists(self):
        try:
            return self.win._ya.user_playlists()
        except Exception:                                      # noqa: BLE001
            return list(self.win.playlists)

    def fill(self):
        RuleRow._clear(self.box)
        if not self.lst:
            e = QLabel("Правил пока нет — добавьте готовое или своё.")
            e.setObjectName("Sub")
            self.box.addWidget(e)
        for r in self.lst:
            self.box.addWidget(RuleRow(self, r))
        self.box.addStretch(1)

    def add(self, rule):
        r = {"id": secrets.token_hex(4), "on": True, **{k: dict(v) for k, v in rule.items()}}
        self.lst.append(r)
        self.changed()
        self.fill()

    def remove(self, r):
        self.lst = [x for x in self.lst if x is not r]
        self.changed()
        QTimer.singleShot(0, self.fill)

    def changed(self):
        save_rules(self.win, self.lst)


def settings_card(shell):
    win = shell.win
    card = QFrame()
    card.setObjectName("YaWaveCard")
    card.setStyleSheet("QFrame#YaWaveCard{background:#171717;border-radius:16px;}")
    card.setMaximumWidth(760)
    lay = QHBoxLayout(card)
    lay.setContentsMargins(18, 14, 18, 14)
    col = QVBoxLayout()
    t = QLabel("Автоматика")
    t.setStyleSheet("font-size:14px;font-weight:700;background:transparent;")
    col.addWidget(t)
    n = len(rules(win))
    d = QLabel("«В 23:00 — спокойное», «в игре — тише», «друг в сети — уведомить» и свои правила. "
               + (f"Правил: {n}." if n else "Правил пока нет."))
    d.setObjectName("YaSub")
    d.setWordWrap(True)
    col.addWidget(d)
    lay.addLayout(col, 1)
    b = QPushButton("Настроить")
    b.setObjectName("AccentBtn")

    def open_():
        RulesDialog(win).exec()
        m = len(rules(win))
        d.setText(d.text().rsplit("Прав", 1)[0] + (f"Правил: {m}." if m else "Правил пока нет."))
    b.clicked.connect(open_)
    lay.addWidget(b)
    return card
