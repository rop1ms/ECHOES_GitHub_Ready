# ya_sound_panel.py
"""
Панель «Звук» — кнопка со столбиками эквалайзера в нижнем плеере Echoes Music.

  • Басс буст — прибавка к низким полосам эквалайзера (MainWindow.set_bass_boost): ваши ползунки EQ и пресеты
    не меняются, звучит «ползунок + буст»; мини-эквалайзер сверху показывает, что реально играет;
  • Супер громкость — тот же множитель, что «Экстремальная громкость» в настройках (100 %…10 000 %);
  • Найткор / Словед — те же пресеты, что в настройках (Speed Up 1.25× и Slowed + Reverb 0.80×):
    темп и тон меняются вместе, позиция в треке сохраняется;
  • 8D — звук летает вокруг головы (spatial8d): ИИ находит темп и сильные доли, круг идёт в такт; на кнопке
    точка облетает голову, пока 8D включён. Остаётся включённым и на следующих треках.

Все значения общие с настройками — меняете здесь, меняется и там.
"""
from __future__ import annotations

import math
import time

from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget,QCheckBox

YELLOW = "#ffdb1a"

QSS = """
QFrame#SndPanel { background:transparent; border:none; border-radius:18px; }
QLabel { background:transparent; color:#ffffff; }
QLabel#SndH { font-size:16px; font-weight:800; }
QLabel#SndSec { font-size:13px; font-weight:700; }
QLabel#SndVal { font-size:12px; font-weight:700; color:#ffdb1a; }
QLabel#SndSub { font-size:11px; color:#8a8a8a; }
QPushButton#SndChip { background:rgba(255,255,255,0.07); color:#e6e6e6; border:none; border-radius:13px; padding:5px 10px;
                      font-size:12px; font-weight:700; }
QPushButton#SndChip:hover { background:rgba(255,255,255,0.13); }
QPushButton#SndChip:checked { background:#ffdb1a; color:#111111; }
QPushButton#SndBig { background:rgba(255,255,255,0.07); color:#ffffff; border:none; border-radius:14px; padding:10px 8px;
                     font-size:13px; font-weight:800; text-align:center; }
QPushButton#SndBig:hover { background:rgba(255,255,255,0.13); }
QPushButton#SndBig:checked { background:#ffdb1a; color:#111111; }
QPushButton#SndBig:disabled { color:#777777; }
QPushButton#SndLink { background:transparent; color:#8a8a8a; border:none; font-size:12px; font-weight:600;
                      text-align:left; padding:2px 0; }
QPushButton#SndLink:hover { color:#ffffff; }
QSlider::groove:horizontal { height:4px; background:#3a3a3a; border-radius:2px; }
QSlider::sub-page:horizontal { background:#ffdb1a; border-radius:2px; }
QSlider::handle:horizontal { background:#ffffff; width:14px; height:14px; margin:-5px 0; border-radius:7px; }
"""

BASS_STEPS = ((0, "Выкл"), (35, "Лёгкий"), (70, "Сильный"), (100, "Максимум"))


class MiniEq(QWidget):
    """10 столбиков: что реально звучит (эквалайзер + басс буст); низкие полосы буста подсвечены."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.vals = [0.0] * 10
        self.setFixedHeight(58)

    def refresh(self):
        try:
            v = list(self.win.eq_effective())
        except Exception:                                  # noqa: BLE001
            v = [0.0] * 10
        if v != self.vals:
            self.vals = v
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height() - 14
        mid = h / 2
        p.setPen(QColor(255, 255, 255, 30))
        p.drawLine(0, int(mid), w, int(mid))
        n = len(self.vals)
        bw = w / n
        boost = self.win.bass_boost() if hasattr(self.win, "bass_boost") else 0
        p.setPen(Qt.PenStyle.NoPen)
        for i, g in enumerate(self.vals):
            x = i * bw + bw * 0.22
            hh = (g / 20.0) * (mid - 2)
            r = QRectF(x, mid - hh if hh > 0 else mid, bw * 0.56, max(2.0, abs(hh)))
            col = QColor(YELLOW) if (i < 4 and boost > 0) else QColor(255, 255, 255, 150)
            p.setBrush(col)
            p.drawRoundedRect(r, 2, 2)
        p.setPen(QColor(138, 138, 138))
        f = p.font()
        f.setPixelSize(9)
        p.setFont(f)
        for i, lbl in enumerate(("31", "62", "125", "250", "500", "1K", "2K", "4K", "8K", "16K")):
            p.drawText(QRectF(i * bw, h + 2, bw, 12), Qt.AlignmentFlag.AlignCenter, lbl)
        p.end()


class Orbit8D(QPushButton):
    """Кнопка 8D: голова, вокруг которой летает звук; пока включено — точка кружит (и тень, когда она сзади)."""

    def __init__(self):
        super().__init__()
        self.setObjectName("SndBig")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMinimumHeight(58)
        self.setToolTip("Умный 8D: ИИ находит темп и сильные доли трека, и звук облетает голову в такт; "
                        "басы остаются в центре. Лучше всего в наушниках")
        self.busy = False
        self._t = QTimer(self)
        self._t.setInterval(33)
        self._t.timeout.connect(self.update)
        self.toggled.connect(self._anim)

    def _anim(self, *_):
        if self.isChecked() and self.isVisible():
            self._t.start()
        else:
            self._t.stop()
            self.update()

    def showEvent(self, e):
        super().showEvent(e)
        self._anim()

    def hideEvent(self, e):
        self._t.stop()
        super().hideEvent(e)

    def paintEvent(self, e):
        super().paintEvent(e)                                  # фон и рамка — из стиля SndBig
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        on = self.isChecked()
        ink = QColor("#111111") if on else QColor("#ffffff")
        cx, cy = 34.0, self.height() / 2
        rx, ry = 20.0, 9.0
        a = (time.monotonic() * (2 * math.pi / 2.4)) if on else 0.9
        dot = QPointF(cx + math.sin(a) * rx, cy - math.cos(a) * ry)
        behind = math.cos(a) > 0                           # верх эллипса — «сзади» головы
        p.setPen(QPen(QColor(ink.red(), ink.green(), ink.blue(), 70), 1.4, Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(cx, cy), rx, ry)              # орбита
        if behind:                                          # сзади — точка за головой
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(ink.red(), ink.green(), ink.blue(), 120))
            p.drawEllipse(dot, 3.2, 3.2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ink)
        p.drawEllipse(QPointF(cx, cy - 2), 7.5, 8.5)        # голова
        p.drawRoundedRect(QRectF(cx - 10, cy + 6, 20, 7), 3.5, 3.5)   # плечи
        if not behind:
            p.setBrush(QColor("#ffdb1a") if not on else QColor("#111111"))
            p.drawEllipse(dot, 4.0, 4.0)
            if on:                                          # след за точкой
                for k in range(1, 4):
                    b = a - k * 0.22
                    if math.cos(b) <= 0:
                        c = QColor(17, 17, 17, 110 - k * 30)
                        p.setBrush(c)
                        p.drawEllipse(QPointF(cx + math.sin(b) * rx, cy - math.cos(b) * ry), 3.2 - k * .6, 3.2 - k * .6)
        p.setPen(ink)
        f = QFont(self.font())
        f.setPixelSize(15)
        f.setWeight(QFont.Weight.Black)
        p.setFont(f)
        r = QRectF(66, 8, self.width() - 74, self.height() / 2 - 6)
        p.drawText(r, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom), "8D-звук")
        f.setPixelSize(12)
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        sub = ("ИИ строит полёт…" if self.busy else "летает вокруг головы, в такт") if on else             "звук летает вокруг головы · ИИ"
        p.drawText(QRectF(66, self.height() / 2 + 1, self.width() - 74, self.height() / 2 - 8),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop), sub)
        p.end()

    def text(self):
        return "8D"


class SoundPanel(QFrame):
    def __init__(self, shell,embedded=False):
        super().__init__(shell, Qt.WindowType.Widget if embedded else Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.embedded=embedded
        self.shell, self.win = shell, shell.win
        from duel_settings import tr
        self.t=lambda ru,en:tr(self.win,ru,en)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        try:                                                   # акцент темы, а не всегда жёлтый
            import ya_skin
            acc, on_acc = ya_skin.CURRENT.get("accent", YELLOW), ya_skin.CURRENT.get("on_accent", "#111111")
        except Exception:                                      # noqa: BLE001
            acc, on_acc = YELLOW, "#111111"
        self.setStyleSheet(QSS.replace("#ffdb1a", acc).replace("color:#111111", f"color:{on_acc}"))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        from echo_glass import GlassFrame
        body = GlassFrame(self, 18)                            # «жидкое стекло», как плеер
        body.setObjectName("SndPanel")
        outer.addWidget(body)
        v = QVBoxLayout(body)
        v.setContentsMargins(18, 14, 18, 14)
        v.setSpacing(8)
        h = QLabel("Звук")
        h.setObjectName("SndH")
        v.addWidget(h)
        self.eq = MiniEq(self.win)
        v.addWidget(self.eq)

        # басс буст
        v.addLayout(self._sec_row("Басс буст", "bass_val"))
        self.bass = QSlider(Qt.Orientation.Horizontal)
        self.bass.setRange(0, 100)
        self.bass.setCursor(Qt.CursorShape.PointingHandCursor)
        self.bass.valueChanged.connect(self._bass_changed)
        v.addWidget(self.bass)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.bass_btns = {}
        for pct, name in BASS_STEPS:
            b = self._chip(name)
            b.clicked.connect(lambda _=False, p=pct: self.bass.setValue(p))
            row.addWidget(b)
            self.bass_btns[pct] = b
        v.addLayout(row)

        # наушники — поправка эквалайзера под модель (hp_eq)
        v.addSpacing(4)
        v.addLayout(self._sec_row("Наушники", "hp_val"))
        from PyQt6.QtWidgets import QComboBox, QCompleter
        import hp_eq
        self.hp = QComboBox()
        self.hp.setEditable(True)
        self.hp.addItem("Без поправки", "")
        for name in hp_eq.models():
            self.hp.addItem(name, name)
        self.hp.lineEdit().setPlaceholderText("Начните вводить модель…")
        comp = self.hp.completer()
        if comp is not None:
            comp.setFilterMode(Qt.MatchFlag.MatchContains)
            comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        cur = self.win.settings.get("hp_profile", "")
        i = self.hp.findData(cur)
        self.hp.setCurrentIndex(max(0, i))
        self.hp.activated.connect(lambda i: (self.win.set_hp_profile(self.hp.itemData(i) or ""), self.sync()))
        v.addWidget(self.hp)

        # супер громкость
        v.addSpacing(4)
        v.addLayout(self._sec_row("Супер громкость", "loud_val"))
        grid = QGridLayout()
        grid.setSpacing(6)
        self.loud_btns = {}
        for i, pct in enumerate(getattr(self.win, "BOOST_PRESETS", (100, 200, 500, 1000, 2000, 5000, 10000))):
            b = self._chip("Обычная" if pct == 100 else f"×{pct // 100}")
            b.clicked.connect(lambda _=False, p=pct: self._set_loud(p))
            grid.addWidget(b, i // 4, i % 4)
            self.loud_btns[pct] = b
        v.addLayout(grid)
        self.loud_hint = QLabel("")
        self.loud_hint.setObjectName("SndSub")
        v.addWidget(self.loud_hint)

        # найткор / словед
        v.addSpacing(4)
        sec = QLabel("Скорость и тон")
        sec.setObjectName("SndSec")
        v.addWidget(sec)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.b_night = self._big("Найткор\n1.25× — быстрее и выше")
        self.b_slow = self._big("Словед\n0.80× + реверб")
        self.b_night.clicked.connect(lambda: self._toggle_preset("_btn_speedup", self.b_night))
        self.b_slow.clicked.connect(lambda: self._toggle_preset("_btn_slowed", self.b_slow))
        row.addWidget(self.b_night)
        row.addWidget(self.b_slow)
        v.addLayout(row)
        self.b_8d = Orbit8D()
        self.b_8d.clicked.connect(lambda: self._toggle_preset("_btn_8d", self.b_8d))
        v.addWidget(self.b_8d)
        self.preset_status = QLabel("")
        self.preset_status.setObjectName("SndSub")
        v.addWidget(self.preset_status)

        more = QPushButton("Весь эквалайзер и настройки звука")
        more.setObjectName("SndLink")
        more.setCursor(Qt.CursorShape.PointingHandCursor)
        more.clicked.connect(self._open_settings)
        v.addWidget(more)
        from duel_settings import tr
        self.follow_export=QCheckBox(tr(self.win,'Экспортировать с текущими настройками плеера','Use current player settings for export'))
        self.follow_export.setChecked(self.win.settings.get('export_follow_player',True))
        self.follow_export.toggled.connect(self._follow_export_changed);v.addWidget(self.follow_export)
        export=QPushButton(tr(self.win,'Экспорт текущего трека с эффектами','Export current track with effects'))
        export.clicked.connect(lambda:self.win.export_effects([]));v.addWidget(export)

        if not embedded:self.setFixedWidth(360)
        else:
            # в настройках: ширина как у остальных карточек; множитель громкости там — отдельной карточкой
            # «Экстремальная громкость», а ссылка «все настройки звука» ведёт на эту же страницу
            self.setMaximumWidth(760)
            for w in [self.loud_val, self.loud_hint, more] + list(self.loud_btns.values()):
                w.hide()
            for i in range(v.count()):
                it = v.itemAt(i)
                lay = it.layout() if it is not None else None
                if lay is not None and any(lay.itemAt(j).widget() is self.loud_val for j in range(lay.count())):
                    for j in range(lay.count()):
                        w = lay.itemAt(j).widget()
                        if w is not None:
                            w.hide()
        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.sync)

    # ── части ── #

    def _sec_row(self, title, attr):
        row = QHBoxLayout()
        lbl = QLabel(title)
        lbl.setObjectName("SndSec")
        val = QLabel("")
        val.setObjectName("SndVal")
        setattr(self, attr, val)
        row.addWidget(lbl)
        row.addStretch(1)
        row.addWidget(val)
        return row

    @staticmethod
    def _chip(text):
        b = QPushButton(text)
        b.setObjectName("SndChip")
        b.setCheckable(True)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return b

    @staticmethod
    def _big(text):
        b = QPushButton(text)
        b.setObjectName("SndBig")
        b.setCheckable(True)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setMinimumHeight(56)
        return b

    # ── действия ── #

    def _bass_changed(self, v):
        self.win.set_bass_boost(v)
        self.sync()

    def _set_loud(self, pct):
        self.win._set_boost_percent(pct)
        self.sync()

    def _toggle_preset(self, attr, mine):
        src = getattr(self.win, attr, None)
        if src is None:
            return
        if not src.isEnabled():                            # пресет ещё готовится
            if src.isChecked():                            # повторный клик по нему — отменить подготовку
                src.setChecked(False)
            self.sync()
            return
        src.setChecked(not src.isChecked())                # тот же переключатель, что в настройках
        self.sync()

    def _open_settings(self):
        if not self.embedded:self.hide()
        try:
            self.shell.show_page("settings")
            self.shell.settings_hub.select('sound')
        except Exception:                                  # noqa: BLE001
            pass

    # ── состояние ── #

    def sync(self):
        w = self.win
        self.follow_export.blockSignals(True);self.follow_export.setChecked(w.settings.get('export_follow_player',True));self.follow_export.blockSignals(False)
        bass = w.bass_boost()
        if self.bass.value() != bass and not self.bass.isSliderDown():
            self.bass.blockSignals(True)
            self.bass.setValue(bass)
            self.bass.blockSignals(False)
        self.bass_val.setText(self.t("выкл","off") if bass == 0 else self.t(
            f"+{bass} %  ·  до +{round(12 * bass / 100)} дБ на 60 Гц",f"+{bass}% · up to +{round(12 * bass / 100)} dB at 60 Hz"))
        hp = w.settings.get("hp_profile", "")
        self.hp_val.setText("выровнено" if hp else "выкл")
        for p, b in self.bass_btns.items():
            b.setChecked(p == bass)
        pct = w._boost_percent()
        self.loud_val.setText(f"{pct} %")
        for p, b in self.loud_btns.items():
            b.setChecked(p == pct)
        self.loud_hint.setText("Обычная громкость" if pct <= 100 else
                               "Аномальная громкость — перегруз гарантирован" if pct >= 5000 else
                               "Очень громко: возможен перегруз" if pct >= 1000 else
                               f"Громче обычного в {pct / 100:g} раз")
        for mine, attr in ((self.b_night, "_btn_speedup"), (self.b_slow, "_btn_slowed"), (self.b_8d, "_btn_8d")):
            src = getattr(w, attr, None)
            on = bool(src is not None and src.isChecked())
            if mine.isChecked() != on:
                mine.setChecked(on)
        st = getattr(w, "_preset_status_lbl", None)
        busy = bool(st is not None and not st.isHidden() and st.text())
        self.preset_status.setText(st.text() if busy else ("Играет как обычно" if not (self.b_night.isChecked()
                                                          or self.b_slow.isChecked() or self.b_8d.isChecked()) else
                                                          ("8D: " + getattr(w.engine, "last_8d", "")
                                                           if self.b_8d.isChecked() and getattr(w.engine, "last_8d", "")
                                                           else "")))
        self.b_8d.busy = busy and self.b_8d.isChecked()
        self.eq.refresh()

    def popup(self, anchor: QWidget):
        self.sync()
        self.adjustSize()
        g = anchor.mapToGlobal(QPoint(anchor.width() // 2 - self.width() // 2, -self.height() - 10))
        scr = (anchor.screen() or QGuiApplication.primaryScreen()).availableGeometry()
        x = max(scr.left() + 8, min(g.x(), scr.right() - self.width() - 8))
        y = max(scr.top() + 8, g.y())
        self.move(x, y)
        self.show()
        self.raise_()
        self.timer.start()

    def hideEvent(self, e):
        self.timer.stop()
        import time
        if not self.embedded:self.shell._sound_closed_at = time.monotonic()
        super().hideEvent(e)

    def showEvent(self,event):
        super().showEvent(event);self.sync();self.timer.start()

    def _follow_export_changed(self,on):
        self.win.settings['export_follow_player']=bool(on);self.win.save_settings()
