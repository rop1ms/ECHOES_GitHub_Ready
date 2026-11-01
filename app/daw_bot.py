# daw_bot.py
"""
Окно мэшап-бота: выбор треков «Вокал» и «Бит» (поиск по библиотеке), переключатели
(slowed / sped up, короче, без переходов, только припев, схема), чат — можно писать
по-русски («смешай Numb и In the End», «ещё вариант», «вокал выше на 1»…). Тяжёлая работа
(анализ, нейросеть, растяжение) — в фоне, с ходом работы в чате; готовый мэшап открывается
проектом студии, всё можно править руками.
"""
from __future__ import annotations

import threading
import time

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QComboBox, QCompleter, QFrame, QHBoxLayout, QLabel, QLineEdit, QProgressBar,
                             QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

import daw_mashup as M
import daw_project as PR
import daw_ui as U


class _Bridge(QObject):
    progress = pyqtSignal(float, str)
    done = pyqtSignal(object)
    failed = pyqtSignal(str)


class Bubble(QFrame):
    def __init__(self, text, mine=False, parent=None):
        super().__init__(parent)
        self.setObjectName("Bubble")
        bg = "#f0a33a" if mine else "#353c42"
        fg = "#15181a" if mine else "#dfe5e9"
        self.setStyleSheet(f"QFrame#Bubble {{ background: {bg}; border-radius: 8px; }}"
                           f"QLabel {{ color: {fg}; background: transparent; font-size: 12px; }}")
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 7, 10, 7)
        self.lab = QLabel(text)
        self.lab.setWordWrap(True)
        self.lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(self.lab)
        self.bar = None

    def add_progress(self):
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        self.layout().addWidget(self.bar)


class TrackPicker(QComboBox):
    """Выбор трека из библиотеки с поиском по мере набора."""

    def __init__(self, shell, placeholder, parent=None):
        super().__init__(parent)
        self.sh = shell
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.lineEdit().setPlaceholderText(placeholder)
        self.setMinimumWidth(200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.fill()

    def fill(self):
        cur = self.track()
        self.blockSignals(True)
        self.clear()
        lib = sorted(self.sh.library(), key=lambda t: M.track_title(t).lower())
        for t in lib:
            if t.get("path"):
                self.addItem(M.track_title(t), t.get("path"))
        comp = QCompleter([self.itemText(i) for i in range(self.count())], self)
        comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        comp.setFilterMode(Qt.MatchFlag.MatchContains)
        self.setCompleter(comp)
        self.setCurrentIndex(-1)
        if cur:
            self.set_track(cur)
        self.blockSignals(False)

    def track(self):
        txt = self.currentText().strip()
        if not txt:
            return None
        i = self.findText(txt)
        if i < 0:
            ts = M.find_tracks(txt, self.sh.library(), 1)
            return ts[0] if ts else None
        path = self.itemData(i)
        return next((t for t in self.sh.library() if t.get("path") == path), None)

    def set_track(self, t):
        if t is None:
            self.setCurrentIndex(-1)
            return
        i = self.findData(t.get("path"))
        if i >= 0:
            self.setCurrentIndex(i)
        else:
            self.setEditText(M.track_title(t))


class BotView(QWidget):
    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self.sh = shell
        self.cache = {}
        self.opt = M.Options()
        self.last = None                      # (вокал, бит)
        self.busy = False
        self.cancel = threading.Event()
        self.prog_bubble = None
        self.br = _Bridge()
        self.br.progress.connect(self._on_progress)
        self.br.done.connect(self._on_done)
        self.br.failed.connect(self._on_failed)
        self.setStyleSheet("background: #2a3035;")
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)
        # выбор треков
        row = QHBoxLayout()
        row.addWidget(U.label("Вокал", 12, True, U.TEXT))
        self.voc = TrackPicker(shell, "трек, чей голос взять…")
        row.addWidget(self.voc, 1)
        sw = U.IconButton("loop", "Поменять местами", 26)
        sw.clicked.connect(self.swap_pickers)
        row.addWidget(sw)
        row.addWidget(U.label("Бит", 12, True, U.TEXT))
        self.beat = TrackPicker(shell, "трек, чью музыку взять…")
        row.addWidget(self.beat, 1)
        v.addLayout(row)
        # переключатели
        row2 = QHBoxLayout()
        row2.setSpacing(4)
        self.chips = {}
        for key, txt, tip in (("slow", "Slowed", "Замедлить (как slowed + reverb)"),
                              ("fast", "Sped up", "Ускорить (как sped up)"),
                              ("short", "Короче", "Около двух минут"),
                              ("notr", "Без переходов", "Без райзеров и ударов перед припевами"),
                              ("chorus", "Только припевы", "Оставить только припевы")):
            b = U.IconButton("note", tip, 24, checkable=True, text=txt)
            b.toggled.connect(lambda on, key=key: self._chip(key, on))
            self.chips[key] = b
            row2.addWidget(b)
        row2.addSpacing(6)
        self.scheme = QComboBox()
        self.scheme.addItem("Оба целиком", "both")
        self.scheme.addItem("По песне вокала", "voc")
        self.scheme.addItem("По структуре бита", "beat")
        self.scheme.addItem("Схема: сам решит", "auto")
        self.scheme.setMinimumWidth(150)
        U.tip(self.scheme, "По песне вокала — голос идёт как в оригинале, под ним меняются части бита.\n"
                               "По структуре бита — части бита по порядку, на них вокал той же роли.\n"
                               "Оба целиком — бит и вокал как в оригиналах, без нарезки: бот выбирает только,\n"
                               "где вступить голосу, чтобы припев лёг на припев.")
        row2.addWidget(self.scheme)
        self.entry = QComboBox()
        self.entry.addItem("Вход: как в бите", "beat")
        self.entry.addItem("Вход: как в вокале", "voc")
        self.entry.addItem("Вход: по припевам", "roles")
        U.tip(self.entry, "Где вступает голос в схеме «Оба целиком»: там, где пели в оригинале бита; "
                              "через столько же тактов, сколько голос молчал в своём оригинале; или так, "
                              "чтобы припев лёг на припев")
        self.scheme.currentIndexChanged.connect(lambda _i: self.entry.setVisible(self.scheme.currentData() == "both"))
        row2.addWidget(self.entry)
        self.quality = QComboBox()
        self.quality.addItem("Отделение: лучшее", "best")
        self.quality.addItem("Отделение: быстрое", "fast")
        U.tip(self.quality, "Лучшее — две нейросети, голос отделяется вместе с эффектами (ревер, дисторшн, "
                                "автотюн), но в два раза дольше. Быстрое — одна нейросеть.")
        q = str(self.sh.S().get("sep_quality", "best"))
        self.quality.setCurrentIndex(max(0, self.quality.findData(q)))
        self.quality.currentIndexChanged.connect(self._quality)
        self._quality()
        row2.addWidget(self.quality)
        row2.addStretch(1)
        v.addLayout(row2)
        # кнопки
        row3 = QHBoxLayout()
        row3.setSpacing(4)
        self.btn_make = U.IconButton("bot", "Собрать мэшап из выбранных треков", 30, text="Сделать мэшап", color=U.ACC)
        self.btn_make.clicked.connect(self.make_from_pickers)
        row3.addWidget(self.btn_make)
        for ic, txt, fn, tip in (("star", "Подобрать пару", self.suggest_from_picker, "Что из библиотеки подойдёт к треку"),
                                 ("loop", "Ещё вариант", self.again, "Другая раскладка тех же треков"),
                                 ("drum", "Разобрать бит", self.split_from_picker,
                                  "Достать из трека в поле «Бит» бочку, 808, малый, хэты и мелодию — для ремейка"),
                                 ("export", "В библиотеку", self.export, "Сохранить мэшап в MP3 и добавить в плеер")):
            b = U.IconButton(ic, tip, 30, text=txt)
            b.clicked.connect(fn)
            row3.addWidget(b)
        row3.addStretch(1)
        self.btn_stop = U.IconButton("stop", "Остановить работу бота", 30, text="Стоп")
        self.btn_stop.clicked.connect(self.stop)
        self.btn_stop.setEnabled(False)
        row3.addWidget(self.btn_stop)
        v.addLayout(row3)
        # чат
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.chat = QWidget()
        self.chat.setStyleSheet("background: #22272b;")
        self.cl = QVBoxLayout(self.chat)
        self.cl.setContentsMargins(8, 8, 8, 8)
        self.cl.setSpacing(8)
        self.cl.addStretch(1)
        self.scroll.setWidget(self.chat)
        self._stick = True
        self.scroll.verticalScrollBar().rangeChanged.connect(self._to_bottom)
        v.addWidget(self.scroll, 1)
        row4 = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Напиши боту: «смешай Numb и In the End», «ещё вариант», «помощь»…")
        self.input.returnPressed.connect(self.send)
        row4.addWidget(self.input, 1)
        b = U.IconButton("send", "Отправить", 28)
        b.clicked.connect(self.send)
        row4.addWidget(b)
        v.addLayout(row4)
        self.toolbar = None
        self.say("Привет! Я делаю мэшапы из твоих треков: беру голос одного и музыку другого, подгоняю темп по "
                 "долям и тональность, раскладываю по частям песни и свожу. Выбери треки сверху или напиши, что "
                 "смешать. «помощь» — что я умею.")

    # ── чат ── #
    def say(self, text, mine=False, progress=False):
        b = Bubble(text, mine)
        if progress:
            b.add_progress()
        row = QHBoxLayout()
        if mine:
            row.addStretch(1)
        row.addWidget(b, 0 if mine else 1)
        if not mine:
            row.addSpacing(40)
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        w.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        self.cl.insertWidget(self.cl.count() - 1, w)
        self._stick = True                                 # новое сообщение — прокрутить к нему
        return b

    def _to_bottom(self, _lo, hi):
        if self._stick:
            self.scroll.verticalScrollBar().setValue(hi)
            QTimer.singleShot(200, lambda: setattr(self, "_stick", False))

    def refresh(self):
        self.voc.fill()
        self.beat.fill()

    def _quality(self, _i=None):
        q = self.quality.currentData() or "best"
        try:
            import daw_sep
            daw_sep.QUALITY = q
        except Exception:                                # noqa: BLE001
            pass
        if self.sh.S().get("sep_quality") != q:
            self.sh.S()["sep_quality"] = q
            self.sh.save_settings()
            self.cache.clear()                           # разборы с другим отделением — заново

    def _chip(self, key, on):
        if key == "slow" and on:
            self.chips["fast"].setChecked(False)
        if key == "fast" and on:
            self.chips["slow"].setChecked(False)

    def _opts_from_ui(self):
        o = M.Options(seed=self.opt.seed, extra_semis=self.opt.extra_semis, vocal_gain_db=self.opt.vocal_gain_db)
        o.speed = 0.85 if self.chips["slow"].isChecked() else (1.2 if self.chips["fast"].isChecked() else 1.0)
        o.max_len = 125.0 if self.chips["short"].isChecked() else 0.0
        o.transitions = not self.chips["notr"].isChecked()
        o.chorus_only = self.chips["chorus"].isChecked()
        o.backbone = self.scheme.currentData() or "both"
        o.entry = self.entry.currentData() or "beat"
        return o

    def swap_pickers(self):
        a, b = self.voc.track(), self.beat.track()
        self.voc.set_track(b)
        self.beat.set_track(a)

    # ── команды ── #
    def send(self):
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        self.say(text, mine=True)
        r = M.parse(text, self.sh.library())
        o = r["opts"]
        if "speed" in o:
            self.chips["slow"].setChecked(o["speed"] < 0.99)
            self.chips["fast"].setChecked(o["speed"] > 1.01)
        if "max_len" in o:
            self.chips["short"].setChecked(o["max_len"] > 0)
        if "transitions" in o:
            self.chips["notr"].setChecked(not o["transitions"])
        if "chorus_only" in o:
            self.chips["chorus"].setChecked(bool(o["chorus_only"]))
        if "backbone" in o:
            self.scheme.setCurrentIndex(max(0, self.scheme.findData(o["backbone"])))
        if "entry" in o:
            self.scheme.setCurrentIndex(max(0, self.scheme.findData("both")))
            self.entry.setCurrentIndex(max(0, self.entry.findData(o["entry"])))
            self.opt.seed = 0
        if "extra_semis_delta" in o:
            self.opt.extra_semis += o["extra_semis_delta"]
        if "vocal_gain_delta" in o:
            self.opt.vocal_gain_db += o["vocal_gain_delta"]
        it = r["intent"]
        if "entry" in o and it == "again":               # новое место вступления — с первого варианта
            it = None
        if it == "help":
            self.say(M.HELP)
        elif it == "suggest":
            t = r["tracks"][0] if r["tracks"] else self.voc.track() or self.beat.track()
            if t is None:
                self.say("К какому треку подобрать пару? Напиши название или выбери его в поле «Вокал».")
            else:
                self.suggest(t)
        elif it == "swap":
            if self.last:
                self.last = (self.last[1], self.last[0])
                self.voc.set_track(self.last[0])
                self.beat.set_track(self.last[1])
                self.opt.seed = 0
                self.start(self.last[0], self.last[1])
            else:
                self.swap_pickers()
                self.say("Поменял местами поля «Вокал» и «Бит».")
        elif it == "again":
            self.again()
        elif it == "export":
            self.export()
        elif it == "split":
            t = r["tracks"][0] if r["tracks"] else self.beat.track() or self.voc.track()
            if t is None:
                self.say("Какой бит разобрать? Напиши название или выбери его в поле «Бит».")
            else:
                self.split(t)
        elif it == "analyze" and r["tracks"]:
            self.analyze(r["tracks"][0])
        elif it == "mashup":
            ts = r["tracks"]
            if len(ts) >= 2:
                self.opt.seed = 0
                self.voc.set_track(ts[0])
                self.beat.set_track(ts[1])
                self.start(ts[0], ts[1], auto_roles=(r.get("order") != "voc_first"))
            elif len(ts) == 1:
                self.say(f"Нашёл «{M.track_title(ts[0])}». С каким треком смешать? Напиши второй или выбери в поле.")
                self.voc.set_track(ts[0])
            else:
                self.say("Не нашёл таких треков в библиотеке. Выбери их в полях сверху — там поиск по мере набора.")
        else:
            if self.last and r["opts"]:
                self.start(*self.last)
            else:
                self.say("Не понял. Напиши, например: «смешай Numb и Faint» или «помощь».")

    def make_from_pickers(self):
        tv, ti = self.voc.track(), self.beat.track()
        if tv is None or ti is None:
            self.say("Выбери оба трека: чей голос взять (Вокал) и чью музыку (Бит).")
            return
        if tv.get("path") == ti.get("path"):
            self.say("Это один и тот же трек — выбери два разных.")
            return
        self.opt.seed = 0
        self.say(f"Вокал «{M.track_title(tv)}» на бит «{M.track_title(ti)}».", mine=True)
        self.start(tv, ti)

    def again(self):
        if not self.last:
            self.say("Сначала сделаем мэшап — потом смогу предложить другой вариант.")
            return
        self.opt.seed += 1
        self.start(*self.last)

    def split_from_picker(self):
        t = self.beat.track() or self.voc.track()
        if t is None:
            self.say("Выбери трек в поле «Бит» — разберу его на звуки (бочка, 808, малый, хэты, мелодия).")
            return
        self.split(t)

    def split(self, t):
        if self.busy:
            self.say("Подожди, я ещё занят предыдущим (или нажми «Стоп»).")
            return
        import daw_split
        self.say(f"Разбираю «{M.track_title(t)}» на звуки…")
        self.prog_bubble = self.say("Начинаю…", progress=True)

        def job(prog, cancel):
            r = daw_split.split_beat(t, prog, cancel)
            return ("split", r)
        self._run(job)

    def suggest_from_picker(self):
        t = self.voc.track() or self.beat.track()
        if t is None:
            self.say("Выбери трек в поле «Вокал» — подберу к нему музыку.")
            return
        self.suggest(t)

    def suggest(self, t):
        store = None
        try:
            store = self.sh.win._intel_store()
        except Exception:                                # noqa: BLE001
            store = None
        res = M.suggest_pairs(t, self.sh.library(), store, 8)
        if not res:
            self.say("Для подбора нужен анализ библиотеки (темп и тональность треков) — плеер делает его сам в "
                     "фоне, когда включены умные рекомендации. Пока могу смешать любые два трека, которые ты выберешь.")
            return
        lines = [f"К «{M.track_title(t)}» по темпу и тональности лучше всего подходят:"]
        for i, (sc, tr, why) in enumerate(res, 1):
            lines.append(f"{i}. {M.track_title(tr)} — {int(sc * 100)} % ({why})")
        lines.append("Выбери любой в поле «Бит» и нажми «Сделать мэшап».")
        self.say("\n".join(lines))
        self.beat.set_track(res[0][1])
        if self.voc.track() is None:
            self.voc.set_track(t)

    def analyze(self, t):
        if self.busy:
            self.say("Подожди, я ещё занят предыдущим.")
            return
        self.say(f"Слушаю «{M.track_title(t)}»…")
        bub = self.say("Анализ…", progress=True)
        self.prog_bubble = bub
        self._run(lambda prog, cancel: ("an", t, M.analyze(t["path"], prog)))

    def start(self, tv, ti, auto_roles=False):
        if self.busy:
            self.say("Подожди, я ещё собираю предыдущий мэшап (или нажми «Стоп»).")
            return
        self.last = (tv, ti)
        opt = self._opts_from_ui()
        self.prog_bubble = self.say("Начинаю…", progress=True)
        cache = self.cache

        def job(prog, cancel):
            a, b = tv, ti
            if auto_roles:
                prog(0.0, "Решаю, чей голос брать…")
                a, b = M.choose_roles(tv, ti, cache, lambda f, s: prog(f * 0.3, s), cancel)
            p, text, pl = M.make_mashup(a, b, opt, lambda f, s: prog(f * 0.8, s), cancel, cache)
            # подготовить звук заранее — чтобы сразу играло (в два потока: БПФ numpy отпускает GIL)
            from concurrent.futures import ThreadPoolExecutor
            srcs = sorted(p["sources"], key=lambda s: 0 if s.get("warp") else 1)
            done = [0]

            def load(src):
                if cancel.is_set():
                    raise RuntimeError("отменено")
                self.sh.sources.load(src, cancel=cancel)
                done[0] += 1
                prog(0.8 + 0.19 * done[0] / max(1, len(srcs)), f"Готов звук: {src['name']}")
            prog(0.8, "Растягиваю бит и вокал по долям…")
            with ThreadPoolExecutor(2) as ex:
                list(ex.map(load, srcs))
            return ("mashup", p, text, a, b)
        self._run(job)

    def _run(self, job):
        self.busy = True
        self.cancel = threading.Event()
        self.btn_stop.setEnabled(True)
        self.btn_make.setEnabled(False)
        br, cancel = self.br, self.cancel
        last_emit = [0.0]

        def prog(f, s):
            now = time.perf_counter()
            if now - last_emit[0] > 0.15 or f >= 0.999:
                last_emit[0] = now
                br.progress.emit(float(f), str(s))

        def run():
            try:
                res = job(prog, cancel)
                br.done.emit(res)
            except Exception as e:                       # noqa: BLE001
                br.failed.emit(str(e) or e.__class__.__name__)
        threading.Thread(target=run, daemon=True, name="studio-bot").start()

    def stop(self):
        self.cancel.set()

    def _finish(self):
        self.busy = False
        self.btn_stop.setEnabled(False)
        self.btn_make.setEnabled(True)
        try:
            import daw_sep
            daw_sep.release()                            # нейросеть (~300 МБ) больше не нужна
        except Exception:                                # noqa: BLE001
            pass

    def _on_progress(self, f, s):
        b = self.prog_bubble
        if b is not None:
            try:
                b.lab.setText(s)
                if b.bar is not None:
                    b.bar.setValue(int(f * 1000))
            except RuntimeError:
                pass
        self.sh.hint(f"Мэшап-бот: {s}")

    def _on_failed(self, msg):
        self._finish()
        b = self.prog_bubble
        if b is not None and b.bar is not None:
            b.bar.hide()
        if "отменено" in msg:
            self.say("Остановил.")
        else:
            self.say(f"Не получилось: {msg}")

    def _on_done(self, res):
        self._finish()
        b = self.prog_bubble
        if b is not None and b.bar is not None:
            b.bar.setValue(1000)
            b.lab.setText("Готово")
        if res[0] == "split":
            r = res[1]
            self.sh.load_project(r["project"], mashup=True)
            try:
                self.sh.browser.refresh_samples(rescan=True)
            except Exception:                            # noqa: BLE001
                pass
            self.say(r["text"] + "\nПробел — слушать.")
            return
        if res[0] == "an":
            _k, t, an = res
            k = an.get("key", {})
            self.say(f"«{M.track_title(t)}»: {an['bpm']:.1f} BPM ({'ровный' if an.get('steady') else 'живой'} темп), "
                     f"тональность {k.get('name', '?')} ({k.get('camelot', '')}), длина {int(an['dur'] // 60)}:"
                     f"{int(an['dur'] % 60):02d}, частей: {len(an.get('sections', []))}.")
            return
        _k, p, text, a, b2 = res
        self.last = (a, b2)
        self.voc.set_track(a)
        self.beat.set_track(b2)
        self.sh.load_project(p, mashup=True)
        self.say(text + "\nПробел — слушать. Нравится — «В библиотеку».")

    def export(self):
        if not self.sh.p.get("mashup"):
            self.say("Экспортирую текущий проект.")
        self.sh.export_dialog(to_library=True)
