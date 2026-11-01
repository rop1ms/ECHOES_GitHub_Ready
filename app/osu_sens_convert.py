# osu_sens_convert.py
"""
Перенос чувствительности из игр в esu!.

В шутерах чувствительность — градусы поворота камеры на один отсчёт мыши; в esu! (как в osu!) —
пиксели экрана на отсчёт (100 % = 1 px). Перенос: движение мыши, которым в игре поворачиваешь
камеру на всё поле зрения (горизонтальный FOV), в esu! проводит курсор через всю ширину экрана —
так рука делает то же движение, что при наводке на цель у края экрана.

Таблица «градусов на отсчёт при чувствительности 1» — общеизвестные значения движков;
для Roblox — базовая скорость стандартной камеры (0.002π рад на пиксель указателя) × настройка
«Чувствительность камеры»; Rivals умножает её на свою. Если игры нет или ощущается иначе — режим
«см на 360°» (измерить линейкой в игре) точен для любой игры.
"""
from __future__ import annotations

import math

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel,
                             QPushButton, QSpinBox, QVBoxLayout)

ROBLOX_YAW = 0.36                     # 0.002·π рад на пиксель = 0.36° при чувствительности 1


def _hfov(vfov_deg, aspect=16 / 9):
    return math.degrees(2 * math.atan(math.tan(math.radians(vfov_deg) / 2) * aspect))


def _minecraft(pct):
    f = 0.6 * (pct / 200.0) + 0.2
    return f ** 3 * 1.2


# ключ: (название, подпись поля, чувств. по умолч., шаг, макс, FOV по умолч., deg/отсчёт(s, extra), extra-подпись)
GAMES = [
    ("roblox", "Roblox (обычная камера)", "Чувствительность камеры", 1.0, 0.01, 10, round(_hfov(70), 1),
     lambda s, e: ROBLOX_YAW * s, None),
    ("rivals", "Roblox RIVALS", "Чувствительность в RIVALS", 1.0, 0.01, 20, round(_hfov(70), 1),
     lambda s, e: ROBLOX_YAW * s * e, "Чувствительность Roblox"),
    ("fortnite", "Fortnite", "Чувствительность X, %", 6.0, 0.1, 100, 103.0, lambda s, e: 0.005555 * s, None),
    ("roblox_games", "Roblox: Arsenal, BedWars, Da Hood и др. (камера Roblox)", "Чувствительность камеры Roblox",
     1.0, 0.01, 10, round(_hfov(70), 1), lambda s, e: ROBLOX_YAW * s, None),
    ("cs2", "Counter-Strike 2 / CS:GO / CS 1.6", "sensitivity", 2.0, 0.01, 20, 106.3, lambda s, e: 0.022 * s, None),
    ("source", "Source: TF2, Garry's Mod, L4D2, Portal, Titanfall 2", "sensitivity", 2.0, 0.01, 20, 106.3,
     lambda s, e: 0.022 * s, None),
    ("quake", "Quake Champions / Quake Live / старые CoD", "sensitivity", 2.0, 0.01, 20, 106.3,
     lambda s, e: 0.022 * s, None),
    ("deadlock", "Deadlock", "sensitivity", 1.0, 0.01, 20, 106.3, lambda s, e: 0.044 * s, None),
    ("marvel", "Marvel Rivals", "Чувствительность", 2.5, 0.01, 100, 103.0, lambda s, e: 0.0175 * s, None),
    ("tarkov", "Escape from Tarkov", "Чувствительность мыши", 0.3, 0.01, 5, round(_hfov(75), 1),
     lambda s, e: 0.125 * s, None),
    ("rust", "Rust", "input.sensitivity", 0.5, 0.01, 10, round(_hfov(90), 1), lambda s, e: 0.1125 * s, None),
    ("valorant", "Valorant", "Чувствительность", 0.4, 0.001, 10, 103.0, lambda s, e: 0.07 * s, None),
    ("apex", "Apex Legends", "Чувствительность мыши", 2.0, 0.01, 20, 106.3, lambda s, e: 0.022 * s, None),
    ("ow2", "Overwatch 2", "Чувствительность", 7.0, 0.01, 100, 103.0, lambda s, e: 0.0066 * s, None),
    ("cod", "Call of Duty (MW / Warzone / BO)", "Чувствительность мыши", 6.0, 0.01, 100, 103.0,
     lambda s, e: 0.0066 * s, None),
    ("r6", "Rainbow Six Siege", "Чувствительность (множитель 0.02)", 10.0, 1, 100, 91.5,
     lambda s, e: 0.00572957795 * s, None),
    ("minecraft", "Minecraft", "Чувствительность, % (0–200)", 100.0, 1, 200, round(_hfov(70), 1),
     lambda s, e: _minecraft(s), None),
    ("osu", "osu! (stable / lazer)", "Sensitivity", 1.0, 0.01, 10, 0.0, None, None),
    ("cm360", "Любая игра: см на 360°", "Сантиметров на полный оборот", 30.0, 0.1, 500, 103.0, None, None),
    ("yaw", "Любая игра: градусов на отсчёт", "Градусов на отсчёт (yaw × sens)", 0.044, 0.0001, 10, 103.0,
     lambda s, e: s, None),
]


class SensConvertDialog(QDialog):
    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self.shell = shell
        self.setWindowTitle("Перенос чувствительности из игры")
        self.setMinimumWidth(560)
        self.setStyleSheet(
            "QDialog{background:#1b1424;} QLabel{color:#fff;background:transparent;}"
            "QLabel#Sub{color:rgba(255,255,255,0.6);font-size:12px;}"
            "QLabel#Big{color:#ff9ccb;font-size:30px;font-weight:900;}"
            "QLabel#Info{color:rgba(255,255,255,0.75);font-size:12px;}"
            "QComboBox,QDoubleSpinBox,QSpinBox{background:#2a2036;color:#fff;border:1px solid #4a3a5c;"
            "border-radius:8px;padding:5px 8px;min-height:22px;}"
            "QCheckBox{color:#fff;}"
            "QPushButton{background:rgba(255,255,255,0.10);color:#fff;border:none;border-radius:14px;"
            "padding:8px 16px;font-weight:600;} QPushButton:hover{background:rgba(255,255,255,0.18);}"
            "QPushButton#Main{background:#ff66aa;font-weight:800;} QPushButton#Main:hover{background:#ff85bd;}")
        S = shell.S()
        mem = dict(S.get("sens_conv") or {})
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 18, 22, 18)
        v.setSpacing(10)
        h = QLabel("Перенос чувствительности")
        h.setStyleSheet("font-size:18px;font-weight:800;")
        v.addWidget(h)
        sub = QLabel("Выберите игру и впишите свою чувствительность оттуда — esu! подберёт такую, чтобы рука "
                     "делала то же движение: поворот камеры на всё поле зрения = курсор через весь экран.")
        sub.setObjectName("Sub")
        sub.setWordWrap(True)
        v.addWidget(sub)
        g = QGridLayout()
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(8)
        self.game = QComboBox()
        for key, name, *_ in GAMES:
            self.game.addItem(name, key)
        self.val_lbl = QLabel()
        self.val = QDoubleSpinBox()
        self.val.setDecimals(4)
        self.extra_lbl = QLabel()
        self.extra = QDoubleSpinBox()
        self.extra.setDecimals(3)
        self.extra.setRange(0.01, 10)
        self.extra.setSingleStep(0.05)
        self.extra.setValue(float(mem.get("extra", 1.0)))
        self.fov = QDoubleSpinBox()
        self.fov.setRange(30, 170)
        self.fov.setDecimals(1)
        self.fov.setSuffix("°")
        self.dpi = QSpinBox()
        self.dpi.setRange(100, 32000)
        self.dpi.setSingleStep(100)
        self.dpi.setValue(int(mem.get("dpi", 800)))
        self.same = QCheckBox("Та же мышь и тот же DPI, что сейчас")
        self.same.setChecked(bool(mem.get("same", True)))
        self.dpi_now = QSpinBox()
        self.dpi_now.setRange(100, 32000)
        self.dpi_now.setSingleStep(100)
        self.dpi_now.setValue(int(mem.get("dpi_now", 800)))
        rows = [("Игра", self.game), (self.val_lbl, self.val), (self.extra_lbl, self.extra),
                ("Поле зрения (по горизонтали)", self.fov), ("DPI мыши в игре", self.dpi), (None, self.same),
                ("DPI мыши сейчас", self.dpi_now)]
        self._dpi_now_lbl = None
        for i, (lab, w) in enumerate(rows):
            if lab is not None:
                lw = lab if isinstance(lab, QLabel) else QLabel(lab)
                g.addWidget(lw, i, 0)
                if w is self.dpi_now:
                    self._dpi_now_lbl = lw
            g.addWidget(w, i, 1)
        v.addLayout(g)
        self.big = QLabel("")
        self.big.setObjectName("Big")
        self.big.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.big)
        self.info = QLabel("")
        self.info.setObjectName("Info")
        self.info.setWordWrap(True)
        self.info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.info)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        self.ok = QPushButton("Применить")
        self.ok.setObjectName("Main")
        self.ok.clicked.connect(self._apply)
        row.addWidget(cancel)
        row.addWidget(self.ok)
        v.addLayout(row)
        self._vals = dict(mem.get("vals") or {})
        self._fovs = dict(mem.get("fovs") or {})
        self._cur = None
        i = self.game.findData(mem.get("game", "rivals"))
        self.game.setCurrentIndex(max(0, i))
        self.game.currentIndexChanged.connect(self._game_changed)
        for w in (self.val, self.extra, self.fov, self.dpi, self.dpi_now):
            w.valueChanged.connect(self._calc)
        self.same.toggled.connect(self._calc)
        self._game_changed()

    def _spec(self):
        key = self.game.currentData()
        return next(gm for gm in GAMES if gm[0] == key)

    def _game_changed(self, *_):
        if self._cur is not None:                     # запомнить значения прежней игры
            self._vals[self._cur] = self.val.value()
            self._fovs[self._cur] = self.fov.value()
        key, _name, label, dflt, step, mx, fov, _fn, extra = self._spec()
        self._cur = key
        self.val_lbl.setText(label)
        self.val.blockSignals(True)
        self.val.setRange(step, mx)
        self.val.setSingleStep(step if step >= 0.01 else 0.001)
        self.val.setValue(float(self._vals.get(key, dflt)))
        self.val.blockSignals(False)
        self.extra_lbl.setVisible(extra is not None)
        self.extra.setVisible(extra is not None)
        if extra:
            self.extra_lbl.setText(extra)
        is_osu = key == "osu"
        self.fov.setVisible(not is_osu)
        self.fov.blockSignals(True)
        self.fov.setValue(float(self._fovs.get(key, fov or 103.0)))
        self.fov.blockSignals(False)
        self._calc()

    def _screen_px(self) -> float:
        try:
            scr = self.shell.window().screen()
            return scr.geometry().width() * scr.devicePixelRatio()
        except Exception:                              # noqa: BLE001
            return 1920.0

    def _result(self):
        key, _n, _l, _d, _s, _m, _f, fn, _e = self._spec()
        s = self.val.value()
        dpi_g = float(self.dpi.value())
        dpi_n = dpi_g if self.same.isChecked() else float(self.dpi_now.value())
        if key == "osu":                             # те же единицы: пиксели на отсчёт
            return s * dpi_g / dpi_n, None
        if key == "cm360":
            deg = 360.0 / (s / 2.54 * dpi_g)
        else:
            deg = fn(s, self.extra.value())
        if deg <= 0:
            return None, None
        cm_fov = self.fov.value() / deg / dpi_g * 2.54          # сколько см рукой — поворот на поле зрения
        counts_now = cm_fov / 2.54 * dpi_n
        return self._screen_px() / max(1e-6, counts_now), 360.0 / deg / dpi_g * 2.54

    def _calc(self, *_):
        self._dpi_now_lbl.setVisible(not self.same.isChecked())
        self.dpi_now.setVisible(not self.same.isChecked())
        sens, cm360 = self._result()
        if sens is None:
            self.big.setText("—")
            self.ok.setEnabled(False)
            return
        pct = sens * 100
        clamped = max(1.0, min(500.0, pct))
        self.big.setText(f"{clamped:.0f}%")
        info = []
        if cm360:
            info.append(f"В игре: {cm360:.1f} см на 360°")
        cm_screen = self._screen_px() / max(1e-6, sens) / float(
            self.dpi.value() if self.same.isChecked() else self.dpi_now.value()) * 2.54
        info.append(f"в esu!: {cm_screen:.1f} см рукой через весь экран")
        if pct < 1 or pct > 500:
            info.append(f"(точно было бы {pct:.1f}% — ограничено 1–500%)")
        if self.game.currentData() in ("roblox", "rivals", "roblox_games"):
            info.append("Для Roblox: скорость указателя Windows 6/11 и без «повышенной точности».")
        self.info.setText("  ·  ".join(info[:2]) + ("\n" + "\n".join(info[2:]) if len(info) > 2 else ""))
        self.ok.setEnabled(True)
        self._sens = clamped / 100.0

    def _apply(self):
        S = self.shell.S()
        self._vals[self._cur] = self.val.value()
        self._fovs[self._cur] = self.fov.value()
        S["sens_conv"] = {"game": self._cur, "vals": self._vals, "fovs": self._fovs, "extra": self.extra.value(),
                          "dpi": self.dpi.value(), "dpi_now": self.dpi_now.value(), "same": self.same.isChecked()}
        S["sens"] = round(self._sens, 4)
        self.shell.save_settings()
        self.accept()
