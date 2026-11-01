# dsp_export.py
"""Экспорт треков с эффектами на диск: Slowed, Speed Up, Reverb, Bass Boost (в любых сочетаниях).

Скорость меняется ресэмплингом (темп и высота вместе — как в классических slowed/nightcore-версиях),
реверб — тот же, что у пресета плеера, низы — полочный фильтр ffmpeg. Кодирование в MP3 320 / FLAC / WAV
с тегами исходника, обложкой и пометкой эффекта в названии.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import threading

from PyQt6.QtCore import Qt, QObject, pyqtSignal,QTimer
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QCheckBox, QSlider, QLabel,
                             QComboBox, QPushButton, QProgressBar, QFileDialog, QLineEdit)

SR = 44100
NOWIN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

def player_fx(win):
    """One snapshot of the same state used by Settings and the bottom sound button."""
    engine=win.engine;mode=getattr(engine,'preset_mode',None)
    factor=getattr(engine,'preset_factor',1.) or 1.
    if mode in ('slowed','speedup') and factor==1.:factor=.8 if mode=='slowed' else 1.25
    factor*=float(getattr(engine,'speed',1.) or 1.)
    eq=[max(-20.,min(20.,float(v))) for v in win.eq_effective()]
    return {'linked':True,'slowed':factor<.999,'speedup':factor>1.001,
            'slowed_rate':factor if factor<1 else .8,
            'speed_rate':factor if factor>1 else 1.25,
            'reverb':mode=='slowed','reverb_wet':getattr(engine,'_REVERB_WET',.45),
            'bass':win.bass_boost()>0,'bass_db':12*win.bass_boost()/100,
            'eq_gains':eq,'gain':max(1.,min(100.,win._boost_percent()/100))}

def audio_filters(fx):
    filters=[];eq=fx.get('eq_gains')
    if eq is not None:
        for hz,gain in zip((60,170,310,600,1000,3000,6000,12000,14000,16000),eq):
            if abs(gain)>.01:filters.append(f'equalizer=f={hz}:t=o:w=1:g={gain:.3f}')
    elif fx.get('bass'):
        gain=max(1.,min(18.,float(fx.get('bass_db',6.))));filters.append(f'bass=g={gain:.1f}:f=95:w=0.7')
    gain=float(fx.get('gain',1.))
    if gain!=1.:filters.append(f'volume={gain:.4f}')
    if filters:filters.append('alimiter=limit=0.97:level=false')
    return filters


def effect_label(fx: dict) -> str:
    parts = []
    if fx.get("slowed"):
        parts.append("slowed")
    if fx.get("speedup"):
        parts.append("sped up")
    if fx.get("reverb"):
        parts.append("reverb")
    if fx.get("bass"):
        parts.append("bass boosted")
    if fx.get('eq_gains') and any(abs(g)>.01 for g in fx['eq_gains']):parts.append('EQ')
    if fx.get('gain',1)>1:parts.append('amplified')
    return " + ".join(parts) or ('player settings' if fx.get('linked') else '')


def speed_factor(fx: dict) -> float:
    if fx.get("slowed"):
        return float(fx.get("slowed_rate", 0.8))
    if fx.get("speedup"):
        return float(fx.get("speed_rate", 1.25))
    return 1.0


def render(engine, src: str, dst: str, fx: dict, fmt: str = "mp3", meta: dict | None = None):
    """Обработать src → dst. Вызывать в фоновом потоке. Бросает RuntimeError с понятным текстом."""
    if engine is None:
        raise RuntimeError("аудиодвижок недоступен")
    audio, sr, ch = engine._decode_to_numpy(src, SR, speed_factor(fx))
    if fx.get("reverb"):
        audio = engine._apply_reverb_to_audio(audio, sr,wet_amount=fx.get('reverb_wet',.45))
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", prefix="echoes_fx_", delete=False)
    tmp.close()
    try:
        engine._numpy_to_wav(audio, sr, ch, tmp.name)
        del audio
        filters = audio_filters(fx)
        codec = {"mp3": ["-c:a", "libmp3lame", "-b:a", "320k", "-id3v2_version", "3"],
                 "flac": ["-c:a", "flac", "-sample_fmt", "s32"],
                 "wav": ["-c:a", "pcm_s24le"]}[fmt]
        md = []
        for k, v in (meta or {}).items():
            if v:
                md += ["-metadata", f"{k}={v}"]
        base = ["ffmpeg", "-y", "-loglevel", "error", "-i", tmp.name, "-i", src]
        af = ["-af", ",".join(filters)] if filters else []
        tries = []
        if fmt in ("mp3", "flac"):                    # с обложкой из исходника
            tries.append(base + ["-map", "0:a", "-map", "1:v?", "-c:v", "copy",
                                 "-disposition:v", "attached_pic", "-map_metadata", "1"] + af + codec + md + [dst])
        tries.append(base + ["-map", "0:a", "-map_metadata", "1"] + af + codec + md + [dst])
        err = ""
        for cmd in tries:
            proc = subprocess.run(cmd, capture_output=True, creationflags=NOWIN)
            if proc.returncode == 0 and os.path.exists(dst):
                return dst
            err = proc.stderr.decode(errors="replace").strip()[-300:]
        raise RuntimeError(f"ffmpeg: {err or 'не удалось закодировать'}")
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def _safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip().rstrip(".") or "track"


class _Sig(QObject):
    progress = pyqtSignal(int, int, str)
    done = pyqtSignal(list, list)


class ExportDialog(QDialog):
    def __init__(self, win, tracks: list[dict]):
        super().__init__(win)
        self.win = win
        self.tracks = [t for t in tracks if t and t.get("path")]
        self.setWindowTitle("Экспорт с эффектами")
        self.setMinimumWidth(460)
        st = win.settings.get("fx_export", {}) if hasattr(win, "settings") else {}
        self._busy = False
        from echo_glass import GlassFrame,glass_controls_qss
        from duel_settings import tr
        self.t=lambda ru,en:tr(win,ru,en)
        self.setStyleSheet('QDialog{background:#090b10;}QLabel,QCheckBox{color:#eef2fa;background:transparent;}'+glass_controls_qss())
        outer=QVBoxLayout(self);glass=GlassFrame(radius=22);outer.addWidget(glass);v=QVBoxLayout(glass);v.setContentsMargins(22,20,22,20)
        v.setSpacing(10)
        self.follow_player=QCheckBox(self.t('Брать эффекты из настроек плеера','Use effects from player settings'))
        self.follow_player.setChecked(win.settings.get('export_follow_player',True));v.addWidget(self.follow_player)
        self.link_note=QLabel(self.t('Скорость, реверб, эквалайзер, бас и усиление — как в настройках и нижней панели звука.',
            'Speed, reverb, EQ, bass and gain follow Settings and the bottom sound panel.'));self.link_note.setWordWrap(True);v.addWidget(self.link_note)
        n = len(self.tracks)
        if n == 1:
            t = self.tracks[0]
            head = f"<b>{t.get('title') or os.path.basename(t['path'])}</b><br>" \
                   f"<span style='color:gray'>{t.get('artist') or ''}</span>"
        else:
            head = f"<b>Треков: {n}</b>"
        lab = QLabel(head)
        lab.setWordWrap(True)
        v.addWidget(lab)

        presets = QGridLayout()
        self._preset_buttons=[]
        presets.setSpacing(6)
        for n, (title, cfg) in enumerate((("Slowed + Reverb", {"slowed": True, "reverb": True}),
                                          ("Speed Up", {"speedup": True}),
                                          ("Bass Boost", {"bass": True}),
                                          ("Slowed + Reverb + Bass", {"slowed": True, "reverb": True, "bass": True}),
                                          ("Speed Up + Bass", {"speedup": True, "bass": True}),
                                          ("Сбросить", {}))):
            b = QPushButton(title)
            self._preset_buttons.append(b)
            b.setToolTip("Эффекты можно сочетать как угодно — отметьте галочки ниже")
            b.clicked.connect(lambda _=False, c=cfg: self._preset(c))
            presets.addWidget(b, n // 3, n % 3)
        v.addLayout(presets)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        self.rows = {}

        def row(r, key, text, lo, hi, val, fmt):
            cb = QCheckBox(text)
            cb.setChecked(bool(st.get(key, False)))
            sl = QSlider(Qt.Orientation.Horizontal)
            sl.setRange(lo, hi)
            sl.setValue(int(st.get(key + "_v", val)))
            lb = QLabel()
            lb.setMinimumWidth(64)
            upd = (lambda _=0, s=sl, l=lb, f=fmt: l.setText(f(s.value())))
            sl.valueChanged.connect(upd)
            upd()
            grid.addWidget(cb, r, 0)
            grid.addWidget(sl, r, 1)
            grid.addWidget(lb, r, 2)
            self.rows[key] = (cb, sl)
            return cb

        a = row(0, "slowed", "Slowed", 50, 100, 80, lambda x: f"×{x / 100:.2f}")
        b = row(1, "speedup", "Speed Up", 100, 200, 125, lambda x: f"×{x / 100:.2f}")
        row(2, "reverb", "Reverb", 10, 90, 45, lambda x: f"{x}%")
        row(3, "bass", "Bass Boost", 2, 15, 6, lambda x: f"+{x} дБ")
        a.toggled.connect(lambda on: on and b.setChecked(False))       # медленнее и быстрее сразу нельзя
        b.toggled.connect(lambda on: on and a.setChecked(False))
        v.addLayout(grid)

        fr = QHBoxLayout()
        fr.addWidget(QLabel("Формат:"))
        self.fmt = QComboBox()
        self.fmt.addItems(["mp3", "flac", "wav"])
        self.fmt.setCurrentText(st.get("fmt", "mp3"))
        fr.addWidget(self.fmt)
        fr.addStretch(1)
        v.addLayout(fr)

        dr = QHBoxLayout()
        self.dir = QLineEdit(st.get("dir") or os.path.join(os.path.expanduser("~"), "Music", "ECHOES FX"))
        pick = QPushButton("…")
        pick.setFixedWidth(34)
        pick.setStyleSheet('padding:0;')
        pick.clicked.connect(self._pick_dir)
        dr.addWidget(QLabel("Папка:"))
        dr.addWidget(self.dir, 1)
        dr.addWidget(pick)
        v.addLayout(dr)

        self.bar = QProgressBar()
        self.bar.setRange(0, max(1, n))
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        v.addWidget(self.bar)
        v.addWidget(self.status)

        br = QHBoxLayout()
        br.addStretch(1)
        self.b_open = QPushButton("Открыть папку")
        self.b_open.clicked.connect(lambda: os.startfile(self.dir.text()) if os.path.isdir(self.dir.text()) else None)
        self.b_go = QPushButton("Экспортировать")
        self.b_go.setDefault(True)
        self.b_go.clicked.connect(self._go)
        br.addWidget(self.b_open)
        br.addWidget(self.b_go)
        v.addLayout(br)

        self.sig = _Sig()
        self.sig.progress.connect(self._on_progress)
        self.sig.done.connect(self._on_done)
        if not self.follow_player.isChecked() and not any(cb.isChecked() for cb, _ in self.rows.values()):
            self._preset({"slowed": True, "reverb": True})
        self.follow_player.toggled.connect(self._link_changed)
        self._sync_link()
        self._link_timer=QTimer(self);self._link_timer.setInterval(250);self._link_timer.timeout.connect(self._sync_link)

    def _link_changed(self,on):
        self.win.settings['export_follow_player']=bool(on);self.win.save_settings();self._sync_link()

    def _sync_link(self):
        desired=self.win.settings.get('export_follow_player',True)
        if self.follow_player.isChecked()!=desired:
            self.follow_player.blockSignals(True);self.follow_player.setChecked(desired);self.follow_player.blockSignals(False)
        linked=self.follow_player.isChecked()
        for button in self._preset_buttons:button.setEnabled(not linked and not self._busy)
        for key,(check,slider) in self.rows.items():
            check.setEnabled(not linked and not self._busy);slider.setEnabled(not linked and not self._busy)
        if not linked:return
        fx=player_fx(self.win)
        values={'slowed':round(fx['slowed_rate']*100),'speedup':round(fx['speed_rate']*100),
                'reverb':round(fx['reverb_wet']*100),'bass':round(fx['bass_db'])}
        for key,(check,slider) in self.rows.items():
            check.blockSignals(True);check.setChecked(fx[key]);check.blockSignals(False)
            slider.setValue(values[key])

    def showEvent(self,event):
        super().showEvent(event);self._sync_link();self._link_timer.start()

    def hideEvent(self,event):
        self._link_timer.stop();super().hideEvent(event)

    def _preset(self, cfg):
        for key, (cb, _sl) in self.rows.items():
            cb.setChecked(bool(cfg.get(key)))

    def _pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Папка для экспорта", self.dir.text())
        if d:
            self.dir.setText(d)

    def _fx(self):
        if self.follow_player.isChecked():return player_fx(self.win)
        cb = {k: c.isChecked() for k, (c, _s) in self.rows.items()}
        sv = {k: s.value() for k, (_c, s) in self.rows.items()}
        return {"slowed": cb["slowed"], "slowed_rate": sv["slowed"] / 100.0,
                "speedup": cb["speedup"], "speed_rate": sv["speedup"] / 100.0,
                "reverb": cb["reverb"], "reverb_wet": sv["reverb"] / 100.0,
                "bass": cb["bass"], "bass_db": float(sv["bass"])}

    def _go(self):
        if self._busy:
            return
        fx = self._fx()
        label = effect_label(fx)
        if not label:
            self.status.setText("Выберите хотя бы один эффект.")
            return
        out_dir = self.dir.text().strip()
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as e:
            self.status.setText(f"Не удалось создать папку: {e}")
            return
        fmt = self.fmt.currentText()
        if hasattr(self.win, "settings"):
            self.win.settings["fx_export"] = {"dir": out_dir, "fmt": fmt,
                                              **{k: c.isChecked() for k, (c, _s) in self.rows.items()},
                                              **{k + "_v": s.value() for k, (_c, s) in self.rows.items()}}
            self.win.save_settings()
        self._busy = True
        self.b_go.setEnabled(False)
        self.bar.setValue(0)
        engine = getattr(self.win, "engine", None)
        jobs = list(self.tracks)

        def work():
            ok, bad = [], []
            for i, t in enumerate(jobs):
                title = t.get("title") or os.path.splitext(os.path.basename(t["path"]))[0]
                artist = t.get("artist") or ""
                self.sig.progress.emit(i, len(jobs), title)
                new_title = f"{title} ({label})"
                name = _safe(f"{artist} - {new_title}" if artist else new_title)
                dst = os.path.join(out_dir, f"{name}.{fmt}")
                try:
                    render(engine, t["path"], dst, fx, fmt, {"title": new_title})
                    ok.append(dst)
                except Exception as e:                       # noqa: BLE001
                    bad.append(f"{title}: {e}")
            self.sig.done.emit(ok, bad)

        threading.Thread(target=work, daemon=True, name="fx-export").start()

    def _on_progress(self, i, n, title):
        self.bar.setValue(i)
        self.status.setText(f"Обработка {i + 1} из {n}: {title}…")

    def _on_done(self, ok, bad):
        self._busy = False
        self.b_go.setEnabled(True)
        self.bar.setValue(self.bar.maximum())
        msg = f"Готово: {len(ok)}" + (f" · ошибок: {len(bad)}" if bad else "")
        if ok:
            msg += f"<br><span style='color:gray'>{os.path.basename(ok[-1])}</span>"
        if bad:
            msg += "<br><span style='color:#ff6b7f'>" + "<br>".join(bad[:3]) + "</span>"
        self.status.setText(msg)


def open_dialog(win, tracks):
    tracks = [t for t in (tracks or []) if t]
    if not tracks:
        try:
            from dsl_runtime import PlayerBridge
            cur = PlayerBridge(win).current()
        except Exception:                                    # noqa: BLE001
            cur = None
        tracks = [cur] if cur else []
    if not tracks:
        return
    dlg = ExportDialog(win, tracks)
    try:
        import inapp
        inapp.present(win, dlg, "Экспорт с эффектами")
    except Exception:                                        # noqa: BLE001
        dlg.show()
