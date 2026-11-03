# ya_decor_ui.py
"""Настройки «Живого оформления» Echoes Music: карточка в «Настройках» и окно со всеми переключателями.
Всё применяется сразу, без перезапуска."""
from __future__ import annotations

import data_home
import shutil
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                             QPushButton, QScrollArea, QSlider, QVBoxLayout, QWidget)

import ya_decor

GROUPS = (
    ("ЦВЕТ И ФОН", (("cover_accent", "Акцент из обложки", "кнопка Play, прогресс, логотип — в цвет трека"),
                    ("aura", "Аура окна", "пятна цветов обложки плавают за панелями"),
                    ("pulse", "Пульс под бит", "аура светлеет на сильных долях баса"),
                    ("daytime", "Время суток", "утром тёплый свет, ночью — тёмно-синий"),
                    ("weather", "Погода из обложки", "холодная обложка — капли на стекле, тёплая — лучи"),
                    ("parallax", "Параллакс", "пятна чуть смещаются за курсором"))),
    ("СТЕКЛО", (("grain", "Зерно", "лёгкий шум 2–3 % — стекло не «пластиковое»"),
                ("rim", "Светлый кант", "тонкая линия по верху панелей, ярче под курсором"))),
    ("ПЛЕЕР", (("wave_bar", "Волна вместо полоски", "силуэт громкости трека, пройденное — цветом"),
               ("vinyl", "Пластинка у обложки", "крутится, пока играет; на паузе уезжает"),
               ("mini_lyrics", "Строка текста в плеере", "текущая строка под исполнителем"),
               ("play_glow", "Свечение кнопки Play", "кольцо-эквалайзер вокруг кнопки"))),
    ("СТРАНИЦЫ", (("headers", "Шапки в цвет обложки", "альбомы, плейлисты, фото исполнителя"),
                  ("sticky", "Липкий заголовок", "имя исполнителя прилипает сверху при прокрутке"),
                  ("card_glow", "Свечение карточек", "цветной свет под карточкой при наведении"),
                  ("mix_covers", "Миксы из обложек", "обложки «Моей волны» и жанров — из треков внутри"),
                  ("empty_art", "Живые пустые страницы", "солнце ECHOES вместо «пока пусто»"))),
    ("АНИМАЦИИ", (("shared", "Перелёт обложки", "с карточки на страницу альбома"),
                  ("cascade", "Каскад появления", "карточки возникают по очереди"),
                  ("spring", "Пружинящие кнопки", "лёгкое сжатие при нажатии и отскок"),
                  ("sun_loader", "Солнце вместо загрузки", "лучи загораются по кругу"))),
    ("ECHOES.NET", (("net_aura", "Аура профиля", "шапка профиля — в цвета любимых треков"),
                    ("now_eq", "Живое «Сейчас слушает»", "мини-эквалайзер у трека друга"))),
    ("WINDOWS И МЕЛОЧИ", (("taskbar", "Прогресс на значке", "полоска трека на значке в панели задач"),
                          ("thumb_buttons", "Кнопки в превью окна", "◀ \U000f1012 ▶ при наведении на значок"),
                          ("ui_sounds", "Звуки интерфейса", "тихий щелчок при смене вкладок"),
                          ("egg", "Пасхалка", "10 кликов по солнцу в логотипе…"))),
)


def _save(shell, key, val):
    win = shell.win
    d = dict(win.settings.get("ya_decor") or {})
    d[key] = val
    win.settings["ya_decor"] = d
    try:
        win.save_settings()
    except Exception:                                          # noqa: BLE001
        pass
    dec = getattr(shell, "decor", None)
    if dec is not None:
        dec.apply_settings()
    else:
        ya_decor.load_cfg(win.settings)


def settings_card(shell) -> QWidget:
    card = QFrame()
    card.setObjectName("YaWaveCard")
    card.setStyleSheet("QFrame#YaWaveCard{background:#171717;border-radius:16px;}")
    card.setMaximumWidth(760)
    lay = QHBoxLayout(card)
    lay.setContentsMargins(18, 14, 18, 14)
    lay.setSpacing(14)
    col = QVBoxLayout()
    col.setSpacing(2)
    t = QLabel("Живое оформление")
    t.setStyleSheet("font-size:14px;font-weight:700;background:transparent;")
    d = QLabel("Акцент из обложки, аура и пульс окна, режимы стекла (прозрачное, OLED, цветное), своя картинка фона, "
               "волна в плеере, пластинка, текст под треком, анимации, прогресс на значке в панели задач.")
    d.setObjectName("YaSub")
    d.setWordWrap(True)
    col.addWidget(t)
    col.addWidget(d)
    lay.addLayout(col, 1)
    b = QPushButton("Настроить")
    b.setObjectName("AccentBtn")
    b.clicked.connect(lambda: DecorDialog(shell).exec())
    lay.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)
    return card


class DecorDialog(QDialog):
    def __init__(self, shell, embedded=False):
        super().__init__(shell.win)
        self.shell = shell
        ya_decor.load_cfg(shell.win.settings)
        c = ya_decor.cfg()
        self.setWindowTitle("Живое оформление Echoes Music")
        self.resize(820, 720)
        self.setStyleSheet("QDialog{background:#111113;} QLabel{color:#f0f0f0;background:transparent;}"
                           "QLabel#Sec{color:#8a8a8a;font-size:11px;font-weight:800;letter-spacing:1px;padding-top:12px;}"
                           "QLabel#Sub{color:#8a8a8a;font-size:11px;}"
                           "QPushButton{background:#232326;color:#f0f0f0;border:none;border-radius:12px;"
                           "padding:7px 14px;font-weight:700;} QPushButton:hover{background:#2e2e32;}"
                           "QPushButton:checked{background:#f0f0f0;color:#000;}"
                           "QScrollArea{background:transparent;border:none;}")
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        head = QLabel("Живое оформление")
        head.setStyleSheet("font-size:22px;font-weight:900;")
        root.addWidget(head)
        sub = QLabel("Каждую вещь можно выключить. Всё применяется сразу — смотрите на окно за этим.")
        sub.setObjectName("Sub")
        root.addWidget(sub)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        inner = QWidget()
        inner.setStyleSheet("background:transparent;")
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 0, 10, 0)
        v.setSpacing(4)
        # стекло
        v.addWidget(self._sec("РЕЖИМ СТЕКЛА"))
        gr = QHBoxLayout()
        grp = QButtonGroup(self)
        self.glass_btn = {}
        for k, name in ya_decor.GLASS_MODES:
            b = QPushButton(name)
            b.setStyleSheet('QPushButton{color:#e6ebf2;background:rgba(255,255,255,0.06);'
                             'border:1px solid rgba(255,255,255,0.14);border-radius:10px;padding:7px 14px;}'
                             'QPushButton:checked{color:#101114;background:#ffdb1a;border-color:#ffdb1a;}')
            b.setCheckable(True)
            b.setChecked(c["glass"] == k)
            b.clicked.connect(lambda _=False, k=k: _save(self.shell, "glass", k))
            grp.addButton(b)
            self.glass_btn[k] = b
            gr.addWidget(b)
        gr.addStretch(1)
        v.addLayout(gr)
        # фон окна
        v.addWidget(self._sec("СВОЯ КАРТИНКА ФОНА ОКНА"))
        br = QHBoxLayout()
        self.bg_lbl = QLabel(Path(c["bg_image"]).name if c["bg_image"] else "не выбрана — цвет темы и аура")
        self.bg_lbl.setObjectName("Sub")
        br.addWidget(self.bg_lbl, 1)
        pick = QPushButton("Выбрать…")
        pick.clicked.connect(self._pick_bg)
        br.addWidget(pick)
        clr = QPushButton("Убрать")
        clr.clicked.connect(lambda: (_save(self.shell, "bg_image", ""), self.bg_lbl.setText("не выбрана")))
        br.addWidget(clr)
        v.addLayout(br)
        self.dim_lbl = QLabel()
        v.addWidget(self.dim_lbl)
        self.dim = QSlider(Qt.Orientation.Horizontal)
        self.dim.setRange(0, 90)
        self.dim.setValue(int(c["bg_dim"]))
        self.dim.valueChanged.connect(self._dim)
        self.dim.sliderReleased.connect(lambda: _save(self.shell, "bg_dim", self.dim.value()))
        v.addWidget(self.dim)
        self._dim(self.dim.value())
        hint = QLabel("Совет: с картинкой фона красивее всего стекло «Прозрачное».")
        hint.setObjectName("Sub")
        v.addWidget(hint)
        # переключатели
        self.checks = {}
        for title, items in GROUPS:
            v.addWidget(self._sec(title))
            g = QGridLayout()
            g.setHorizontalSpacing(18)
            g.setVerticalSpacing(6)
            for i, (key, name, how) in enumerate(items):
                box = QVBoxLayout()
                box.setSpacing(0)
                cb = QCheckBox(name)
                cb.setStyleSheet("color:#f0f0f0;font-weight:700;")
                cb.setChecked(bool(c.get(key)))
                cb.toggled.connect(lambda on, key=key: _save(self.shell, key, bool(on)))
                self.checks[key] = cb
                box.addWidget(cb)
                h = QLabel(how)
                h.setObjectName("Sub")
                h.setWordWrap(True)
                h.setContentsMargins(58, 0, 0, 4)
                box.addWidget(h)
                g.addLayout(box, i // 2, i % 2)
            v.addLayout(g)
        # погода за окном
        from PyQt6.QtWidgets import QLineEdit
        import ya_weather
        v.addWidget(self._sec("ПОГОДА ЗА ОКНОМ"))
        wcb = QCheckBox("Тема по реальной погоде")
        wcb.setStyleSheet("color:#f0f0f0;font-weight:700;")
        wcb.setChecked(bool(c.get("real_weather")))
        v.addWidget(wcb)
        wh = QLabel("Снег — падают хлопья, дождь — струи, гроза — вспышки, солнце — тёплый свет, ночь — звёзды. "
                    "Погода с Open-Meteo (бесплатно, без регистрации) по городу, раз в 30 минут.")
        wh.setObjectName("Sub")
        wh.setWordWrap(True)
        v.addWidget(wh)
        wr = QHBoxLayout()
        self.city = QLineEdit(str(c.get("weather_city") or ""))
        self.city.setPlaceholderText("Город, например: Москва")
        self.city.setStyleSheet("QLineEdit{background:#1d1d20;color:#f0f0f0;border:1px solid #2c2c30;"
                                "border-radius:10px;padding:7px 10px;}")
        wr.addWidget(self.city, 1)
        chk = QPushButton("Применить")
        wr.addWidget(chk)
        v.addLayout(wr)
        self.w_status = QLabel(ya_weather.describe(getattr(getattr(shell.win, "_weather_ctl", None), "last", None))
                               if c.get("real_weather") else "")
        self.w_status.setObjectName("Sub")
        v.addWidget(self.w_status)

        def apply_city():
            _save(self.shell, "weather_city", self.city.text().strip())
            ctl = ya_weather.install(self.shell.win)
            ctl.refresh()
            self.w_status.setText("загружаю погоду…")
            try:
                ctl.got.disconnect(self._w_done)
            except TypeError:
                pass
            ctl.got.connect(self._w_done)
        chk.clicked.connect(apply_city)
        self.city.returnPressed.connect(apply_city)
        wcb.toggled.connect(lambda on: (_save(self.shell, "real_weather", bool(on)), apply_city() if on else
                                        self.w_status.setText("")))
        v.addStretch(1)
        sc.setWidget(inner)
        root.addWidget(sc, 1)
        bot = QHBoxLayout()
        reset = QPushButton("Всё по умолчанию")
        reset.clicked.connect(self._reset)
        bot.addWidget(reset)
        bot.addStretch(1)
        ok = QPushButton("Готово")
        ok.setStyleSheet("QPushButton{background:#ffdb1a;color:#000;}")
        ok.clicked.connect(self.accept)
        bot.addWidget(ok)
        ok.setVisible(not embedded)
        root.addLayout(bot)

    def _w_done(self, w):
        try:
            import ya_weather
            self.w_status.setText(ya_weather.describe(w))
        except RuntimeError:
            pass

    def _sec(self, text):
        l = QLabel(text)
        l.setObjectName("Sec")
        return l

    def _dim(self, v):
        self.dim_lbl.setText(f"Затемнение картинки: {v} %")

    def _pick_bg(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Картинка фона окна", "",
                                            "Картинки (*.png *.jpg *.jpeg *.webp *.bmp)")
        if not fn:
            return
        try:                                                   # копия рядом с настройками — не потеряется
            dst = data_home.root() / ("window_bg" + Path(fn).suffix.lower())
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(fn, dst)
            fn = str(dst)
        except OSError:
            pass
        _save(self.shell, "bg_image", fn)
        self.bg_lbl.setText(Path(fn).name)

    def _reset(self):
        self.shell.win.settings["ya_decor"] = {}
        _save(self.shell, "glass", "matte")
        c = ya_decor.cfg()
        for k, cb in self.checks.items():
            cb.blockSignals(True)
            cb.setChecked(bool(c.get(k)))
            cb.blockSignals(False)
        for k, b in self.glass_btn.items():
            b.setChecked(k == c["glass"])
        self.dim.setValue(int(c["bg_dim"]))
        self.bg_lbl.setText("не выбрана — цвет темы и аура")
