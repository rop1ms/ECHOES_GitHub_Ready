# osu_pause.py
"""
Панели по бокам меню паузы osu!: слева эквалайзер плеера, справа звук, фон и интерфейс игры.

Эквалайзер — не копия, а пульт к эквалайзеру плеера: ползунки и пресеты те же, что в
настройках (двигаете здесь — меняется там и сразу звучит). Остальное — настройки темы
osu! (win.settings["osu"]); фон, затемнение, смещение и громкости применяются прямо
на паузе, без выхода из карты.
"""
from __future__ import annotations

from PyQt6.QtCore import QObject, QRect, Qt, QTimer
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSlider,
                             QVBoxLayout, QWidget)

QSS = """
QFrame#OsuPausePanel { background: rgba(22, 15, 30, 222); border: 1px solid rgba(255, 102, 170, 120);
    border-radius: 18px; }
QFrame#OsuPausePanel QWidget#Body { background: transparent; }
QFrame#OsuPausePanel QScrollArea { background: transparent; border: none; }
QFrame#OsuPausePanel QLabel { color: #ffffff; background: transparent; font-family: 'Segoe UI'; font-size: 13px; }
QFrame#OsuPausePanel QLabel#H2 { color: #ff66aa; font-size: 12px; font-weight: 800; letter-spacing: 2px;
    padding-top: 6px; }
QFrame#OsuPausePanel QLabel#Val { color: #ffb3d4; font-weight: 700; }
QFrame#OsuPausePanel QLabel#Tiny { color: #b9adc4; font-size: 10px; }
QFrame#OsuPausePanel QCheckBox { color: #ffffff; font-size: 13px; spacing: 8px; background: transparent; }
QFrame#OsuPausePanel QCheckBox::indicator { width: 30px; height: 16px; border-radius: 8px; background: #4a3d52;
    border: 1px solid #6b5a75; }
QFrame#OsuPausePanel QCheckBox::indicator:checked { background: #ff66aa; border: 1px solid #ffb3d4; }
QFrame#OsuPausePanel QSlider::groove:horizontal { height: 4px; background: #4a3d52; border-radius: 2px; }
QFrame#OsuPausePanel QSlider::sub-page:horizontal { background: #ff66aa; border-radius: 2px; }
QFrame#OsuPausePanel QSlider::handle:horizontal { width: 14px; height: 14px; margin: -6px 0; border-radius: 7px;
    background: #ffffff; border: 2px solid #ff66aa; }
QFrame#OsuPausePanel QSlider::groove:vertical { width: 4px; background: #4a3d52; border-radius: 2px; }
QFrame#OsuPausePanel QSlider::sub-page:vertical { background: #4a3d52; border-radius: 2px; }
QFrame#OsuPausePanel QSlider::add-page:vertical { background: #ff66aa; border-radius: 2px; }
QFrame#OsuPausePanel QSlider::handle:vertical { width: 14px; height: 14px; margin: 0 -6px; border-radius: 7px;
    background: #ffffff; border: 2px solid #ff66aa; }
QFrame#OsuPausePanel QPushButton { background: rgba(255, 255, 255, 0.10); color: #ffffff; border: none;
    border-radius: 13px; padding: 6px 14px; font-weight: 700; font-size: 12px; }
QFrame#OsuPausePanel QPushButton:hover { background: #ff66aa; }
QFrame#OsuPausePanel QComboBox { background: #2c2233; color: #ffffff; border: 1px solid #4a3d52; border-radius: 8px;
    padding: 4px 10px; font-size: 13px; }
QFrame#OsuPausePanel QComboBox QAbstractItemView { background: #2c2233; color: #ffffff;
    selection-background-color: #ff66aa; }
"""

EQ_FREQS = ["60", "170", "310", "600", "1K", "3K", "6K", "12K", "14K", "16K"]
BG_MODES = [("cover", "Обложка"), ("clip", "Клип песни"), ("none", "Без фона")]


def _h2(text) -> QLabel:
    lb = QLabel(text.upper())
    lb.setObjectName("H2")
    return lb


class _Panel(QFrame):
    """Тёмная карточка со своей прокруткой (на низком окне содержимое не обрезается)."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("OsuPausePanel")
        self.setStyleSheet(QSS)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 6, 4, 6)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("Body")
        self.v = QVBoxLayout(body)
        self.v.setContentsMargins(14, 8, 14, 10)
        self.v.setSpacing(6)
        sc.setWidget(body)
        outer.addWidget(sc)
        self.hide()


class PausePanels(QObject):
    def __init__(self, game):
        super().__init__(game)
        self.game = game
        self.host = game.host
        self.win = game.win
        self._sync = False
        self._save = QTimer(self)
        self._save.setSingleShot(True)
        self._save.timeout.connect(self.host.save_settings)
        self.left = _Panel(game)
        self.right = _Panel(game)
        self._build_eq(self.left.v)
        self._build_right(self.right.v)

    @property
    def S(self) -> dict:
        return self.host.osu_settings()

    def _saved(self):
        self._save.start(400)

    # ── эквалайзер плеера ── #
    def _main_eq(self):
        w = self.win
        return getattr(w, "_eq_sliders", None), getattr(w, "eq_preset_cb", None)

    def _build_eq(self, v):
        v.addWidget(_h2("Эквалайзер"))
        self.eq_cb = QComboBox()
        self.eq_cb.setToolTip("Те же пресеты, что в настройках плеера")
        self.eq_cb.activated.connect(self._eq_preset)
        v.addWidget(self.eq_cb)
        row = QHBoxLayout()
        row.setSpacing(2)
        self.eq_sl, self.eq_val = [], []
        for i, f in enumerate(EQ_FREQS):
            col = QVBoxLayout()
            col.setSpacing(2)
            val = QLabel("0")
            val.setObjectName("Tiny")
            val.setAlignment(Qt.AlignmentFlag.AlignCenter)
            sl = QSlider(Qt.Orientation.Vertical)
            sl.setRange(-20, 20)
            sl.setMinimumHeight(120)
            sl.setToolTip(f"{f} Гц")
            sl.valueChanged.connect(lambda x, k=i: self._eq_band(k, x))
            fl = QLabel(f)
            fl.setObjectName("Tiny")
            fl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            col.addWidget(val)
            col.addWidget(sl, 1, Qt.AlignmentFlag.AlignHCenter)
            col.addWidget(fl)
            row.addLayout(col)
            self.eq_sl.append(sl)
            self.eq_val.append(val)
        v.addLayout(row, 1)
        self.eq_auto = QCheckBox("Автопресет по жанру трека")
        self.eq_auto.toggled.connect(self._eq_auto)
        v.addWidget(self.eq_auto)
        b = QPushButton("Сбросить эквалайзер")
        b.clicked.connect(self._eq_reset)
        v.addWidget(b)
        note = QLabel("Это эквалайзер плеера: изменения сразу слышны и сохраняются в его настройках.")
        note.setObjectName("Tiny")
        note.setWordWrap(True)
        v.addWidget(note)

    def _eq_pull(self):
        sls, cb = self._main_eq()
        self._sync = True
        try:
            if cb is not None:
                self.eq_cb.clear()
                for i in range(cb.count()):
                    self.eq_cb.addItem(cb.itemText(i), cb.itemData(i))
                self.eq_cb.setCurrentIndex(cb.currentIndex())
            if sls:
                for mine, lab, theirs in zip(self.eq_sl, self.eq_val, sls):
                    mine.setValue(theirs.value())
                    lab.setText(f"{theirs.value():+d}" if theirs.value() else "0")
            ac = getattr(self.win, "eq_auto_cb", None)
            if ac is not None:
                self.eq_auto.setChecked(ac.isChecked())
        finally:
            self._sync = False

    def _eq_band(self, k, val):
        self.eq_val[k].setText(f"{val:+d}" if val else "0")
        if self._sync:
            return
        sls, cb = self._main_eq()
        if sls and k < len(sls):
            sls[k].setValue(val)                       # обработчик плеера: звук, сохранение, «Вручную»
        if cb is not None:
            self._sync = True
            self.eq_cb.setCurrentIndex(cb.currentIndex())
            self._sync = False

    def _eq_preset(self, idx):
        if self._sync:
            return
        _sls, cb = self._main_eq()
        if cb is not None and 0 <= idx < cb.count():
            cb.setCurrentIndex(idx)
        self._eq_pull()

    def _eq_auto(self, on):
        if self._sync:
            return
        ac = getattr(self.win, "eq_auto_cb", None)
        if ac is not None:
            ac.setChecked(bool(on))
        self._eq_pull()

    def _eq_reset(self):
        fn = getattr(self.win, "_reset_eq", None)
        if fn is not None:
            fn()
        self._eq_pull()

    # ── звук, фон, интерфейс ── #
    def _slider_row(self, v, label, lo, hi, fmt, on_change):
        top = QHBoxLayout()
        top.addWidget(QLabel(label))
        top.addStretch(1)
        val = QLabel()
        val.setObjectName("Val")
        top.addWidget(val)
        v.addLayout(top)
        sl = QSlider(Qt.Orientation.Horizontal)
        sl.setRange(lo, hi)

        def ch(x):
            val.setText(fmt(x))
            if not self._sync:
                on_change(x)
        sl.valueChanged.connect(ch)
        sl._val = val
        sl._fmt = fmt
        v.addWidget(sl)
        return sl

    def _check(self, v, label, key, on_change=None):
        c = QCheckBox(label)

        def ch(on):
            if self._sync:
                return
            self.S[key] = bool(on)
            self._saved()
            if on_change:
                on_change(bool(on))
            self.game.update()
        c.toggled.connect(ch)
        c._key = key
        v.addWidget(c)
        return c

    def _build_right(self, v):
        g = self.game
        v.addWidget(_h2("Звук"))
        self.s_music = self._slider_row(v, "Громкость музыки", 0, 100, lambda x: f"{x}%", self._set_music)
        self.s_sfx = self._slider_row(v, "Громкость хитсаундов", 0, 100, lambda x: f"{x}%", self._set_sfx)
        self.s_off = self._slider_row(v, "Смещение звука (+ — ноты позже)", -150, 150, lambda x: f"{x:+d} мс",
                                      self._set_offset)
        v.addWidget(_h2("Фон"))
        self.s_dim = self._slider_row(v, "Затемнение фона", 0, 100, lambda x: f"{x}%", self._set_dim)
        row = QHBoxLayout()
        row.addWidget(QLabel("Фон карты"))
        row.addStretch(1)
        self.c_bg = QComboBox()
        for k, name in BG_MODES:
            self.c_bg.addItem(name, k)
        self.c_bg.currentIndexChanged.connect(self._set_bg)
        row.addWidget(self.c_bg)
        v.addLayout(row)
        self.k_blur = self._check(v, "Размыть фон", "blur", lambda _on: setattr(g, "bg_scaled", None))
        self.k_pulse = self._check(v, "Фон пульсирует в припевах", "bg_pulse")
        v.addWidget(_h2("Интерфейс игры"))
        self.k_hud = self._check(v, "Счёт, здоровье и комбо", "hud")
        self.k_err = self._check(v, "Шкала ошибок попадания", "hit_error")
        self.k_keys = self._check(v, "Счётчик нажатий клавиш", "key_overlay")
        self.k_trail = self._check(v, "След курсора", "cursor_trail")
        self.s_cur = self._slider_row(v, "Размер курсора", 50, 200, lambda x: f"{x / 100:.2f}×", self._set_cursor)
        v.addWidget(_h2("Ввод"))
        self.s_sens = self._slider_row(v, "Чувствительность", 1, 500, lambda x: f"{x}%", self._set_sens)
        cv = QPushButton("Перенести из игры…")
        cv.setToolTip("Чувствительность из Roblox, RIVALS, CS2, Valorant, Fortnite и других")
        cv.clicked.connect(self._convert)
        v.addWidget(cv)
        rb = QPushButton("Сбросить настройки")
        rb.setToolTip("Хитсаунды, смещение, фон, интерфейс игры, курсор и чувствительность — как по умолчанию "
                      "(громкость музыки не меняется)")
        rb.clicked.connect(self._reset)
        v.addSpacing(6)
        v.addWidget(rb)
        v.addStretch(1)

    def _set_sens(self, x):
        self.S["sens"] = x / 100.0
        self._saved()

    def _convert(self):
        from osu_sens_convert import SensConvertDialog
        if SensConvertDialog(self.host, self.game).exec():
            self._pull_right()

    def _reset(self):
        from osu_game import OSU_DEFAULTS
        S = self.S
        old_off = float(S.get("offset", 0))
        for k in ("sfx_vol", "offset", "dim", "bg", "blur", "bg_pulse", "hud", "hit_error", "key_overlay",
                  "cursor_trail", "cursor_size", "sens"):
            S[k] = OSU_DEFAULTS[k]
        g = self.game
        g.offset += float(S["offset"]) - old_off
        try:
            self.host.sfx.volume = float(S["sfx_vol"])
        except Exception:                              # noqa: BLE001
            pass
        self._saved()
        self._pull_right()
        try:
            g._load_background()
            g.bg_scaled = None
        except Exception as e:                         # noqa: BLE001
            print("[osu pause] reset bg:", e)
        g.update()

    def _set_music(self, x):
        vol = getattr(self.win, "vol", None)
        if vol is not None:
            vol.setValue(int(x))                       # ползунок громкости плеера сам меняет звук и сохраняет
        else:
            try:
                self.win.engine.set_volume(x / 100.0)
            except Exception:                          # noqa: BLE001
                pass

    def _set_sfx(self, x):
        self.S["sfx_vol"] = x / 100.0
        try:
            self.host.sfx.volume = x / 100.0
            self.host.sfx.play("hover", 0.8)           # сразу слышно, как громко
        except Exception:                              # noqa: BLE001
            pass
        self._saved()

    def _set_offset(self, x):
        old = float(self.S.get("offset", 0))
        self.S["offset"] = int(x)
        self.game.offset += x - old                    # локальная подгонка карты сохраняется
        self._saved()

    def _set_dim(self, x):
        self.S["dim"] = x / 100.0
        self._saved()
        self.game.update()

    def _set_bg(self, _i):
        if self._sync:
            return
        self.S["bg"] = self.c_bg.currentData()
        self._saved()
        g = self.game
        try:
            g._load_background()
            if g.video is not None:
                g._vpaused = None
            g.bg_scaled = None
        except Exception as e:                         # noqa: BLE001
            print("[osu pause] bg:", e)
        g.update()

    def _set_cursor(self, x):
        self.S["cursor_size"] = x / 100.0
        self._saved()
        self.game.update()

    def _pull_right(self):
        S = self.S
        self._sync = True
        try:
            vol = getattr(self.win, "vol", None)
            for sl, val in ((self.s_music, vol.value() if vol is not None else 50),
                            (self.s_sfx, int(round(float(S.get("sfx_vol", 0.6)) * 100))),
                            (self.s_off, int(round(float(S.get("offset", 0))))),
                            (self.s_dim, int(round(float(S.get("dim", 0.7)) * 100))),
                            (self.s_cur, int(round(float(S.get("cursor_size", 1.0)) * 100))),
                            (self.s_sens, int(round(float(S.get("sens", 1.0)) * 100)))):
                sl.setValue(int(val))
                sl._val.setText(sl._fmt(int(val)))
            i = next((n for n, (k, _) in enumerate(BG_MODES) if k == S.get("bg", "cover")), 0)
            self.c_bg.setCurrentIndex(i)
            for c in (self.k_blur, self.k_pulse, self.k_hud, self.k_err, self.k_keys, self.k_trail):
                c.setChecked(bool(S.get(c._key, True if c._key != "blur" else False)))
        finally:
            self._sync = False

    # ── показ ── #
    def place(self):
        g = self.game
        W, H = g.width(), g.height()
        menu_l = W / 2 - W * 0.22                       # левый край кнопок меню паузы
        side = int(min(380, menu_l - 56))
        if side < 220 or H < 420:                      # окно слишком узкое — панели не влезут
            self.left.hide()
            self.right.hide()
            return False
        top, h = int(H * 0.16), int(H * 0.72)
        self.left.setGeometry(QRect(28, top, side, h))
        self.right.setGeometry(QRect(W - 28 - side, top, side, h))
        return True

    def show(self):
        self._eq_pull()
        self._pull_right()
        if self.place():
            for p in (self.left, self.right):
                p.show()
                p.raise_()

    def hide(self):
        for p in (self.left, self.right):
            if p.isVisible():
                p.hide()

    def visible(self) -> bool:
        return self.left.isVisible()
