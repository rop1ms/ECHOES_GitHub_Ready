"""Local Russian/English voice control, strict routing and editable scenarios.

The PortAudio callback only queues bounded audio blocks. A single background
worker warms the model, gates speech, and transcribes; all player/Qt actions run
on the GUI thread. No model download or network transcription is performed.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import queue
import threading
import time

import numpy as np
from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from voice_audio import SR, SpeechBuffer, confident_text
from voice_commands import (INTENTS, WAKE, normalize as _norm, parse, route, strip_wake,
                            validate_commands)

_MODEL = {}
_MODEL_LOCK = threading.Lock()
_DISCOVERY = (0.0, None, None)
_NAME_CONTEXT = {
    "ru": "Голосовое обращение к приложению: «Эхос».",
    "en": "The music application's name is ECHOES.",
    "auto": "Название приложения: Эхос (ECHOES).",
}


def english(win=None):
    try:
        import i18n
        return (getattr(win, "settings", {}).get("language", i18n.LANG) if win is not None else i18n.LANG) == "en"
    except ImportError:
        return False


def tr(win, ru, en):
    from ui_language import UIText
    return UIText(ru, en, 'en' if english(win) else 'ru')


def _discover():
    """Locate a cached multilingual model without importing CTranslate2 in Qt.

    Snapshot paths also avoid Hub metadata/network lookups during model loading.
    Discovery is cached briefly so opening settings repeatedly doesn't walk disk.
    """
    global _DISCOVERY
    now = time.monotonic()
    if now - _DISCOVERY[0] < 8:
        return _DISCOVERY[1:]
    try:
        installed = importlib.util.find_spec("faster_whisper") is not None
    except (ImportError, ValueError):
        installed = False
    found = (None, None)
    if installed:
        hub = Path(os.environ.get("HF_HUB_CACHE") or Path(os.environ.get("HF_HOME") or
                   Path.home() / ".cache" / "huggingface") / "hub")
        for size in ("base", "small", "tiny", "medium"):
            snapshots = hub / f"models--Systran--faster-whisper-{size}" / "snapshots"
            try:
                bins = sorted(snapshots.glob("*/model.bin"), key=lambda p: p.stat().st_mtime, reverse=True)
                if bins:
                    found = (size, str(bins[0].parent))
                    break
            except OSError:
                continue
    _DISCOVERY = (now, *found)
    return found


def model_name() -> str | None:
    return _discover()[0]


def load_model():
    _size, path = _discover()
    if path is None:
        return None
    with _MODEL_LOCK:
        if path not in _MODEL:
            from faster_whisper import WhisperModel
            cpu = os.cpu_count() or 4
            _MODEL[path] = WhisperModel(path, device="cpu", compute_type="int8",
                                        cpu_threads=max(1, min(4, cpu - 1)), local_files_only=True)
        return _MODEL[path]


def transcribe(audio: np.ndarray, language="ru", strict=False) -> str:
    model = load_model()
    if model is None:
        return ""
    # A scalar zero temperature prevents Whisper's repeated sampling fallbacks.
    # A little name context makes the uncommon Russian «Эхос» recognisable.
    # It contains no action phrases which noise could reproduce verbatim.
    segments, _info = model.transcribe(
        np.asarray(audio, dtype=np.float32), language=language if language in ("ru", "en") else None,
        beam_size=1, best_of=1, temperature=0.0, condition_on_previous_text=False,
        vad_filter=True, vad_parameters={"min_silence_duration_ms": 200, "speech_pad_ms": 100},
        without_timestamps=True, no_speech_threshold=.5, log_prob_threshold=-.95, max_new_tokens=48,
        initial_prompt=_NAME_CONTEXT.get(language, _NAME_CONTEXT["auto"]),
    )
    return confident_text(segments, strict=strict)


def commands(win):
    raw = win.settings.get("voice_commands") or []
    return [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []


def execute(win, act, arg) -> str:
    """Execute an explicit, already validated action on the GUI thread."""
    import ya_quick as Q
    sh = getattr(win, "_ya", None)
    text = lambda ru, en: tr(win, ru, en)
    if act == "next":
        win.next_track()
        return text("Следующий трек", "Next track")
    if act == "prev":
        win.prev_track()
        return text("Предыдущий трек", "Previous track")
    if act in ("pause", "play"):
        if bool(win.engine.is_playing()) != (act == "play"):
            win.toggle_play()
        return text("Пауза", "Paused") if act == "pause" else text("Играю", "Playing")
    if act in ("vol_up", "vol_down"):
        Q.volume(win, 15 if act == "vol_up" else -15)
        return text("Громче", "Louder") if act == "vol_up" else text("Тише", "Quieter")
    if act == "volume":
        win.vol.setValue(max(0, min(100, int(arg))))
        return text(f"Громкость {win.vol.value()} %", f"Volume {win.vol.value()}%")
    if act == "mute":
        if win.vol.value() > 0:
            win._muted_at = win.vol.value()
            win.vol.setValue(0)
        return text("Без звука", "Muted")
    if act == "unmute":
        if win.vol.value() == 0:
            win.vol.setValue(int(getattr(win, "_muted_at", 0) or 60))
        return text("Звук включён", "Unmuted")
    if act == "like":
        Q.like(win)
        return text("Мне нравится", "Like")
    if act == "mini":
        Q.toggle_mini(win)
        return text("Мини-плеер", "Mini player")
    if act == "wave":
        Q.wave(win)
        return text("Моя волна", "My wave")
    if act == "shuffle":
        win.btn_shuffle.click()
        return text("Перемешивание", "Shuffle")
    if act == "playlist" and sh is not None:
        import search_util
        names = sh.user_playlists()
        matches = [n for n in names if _norm(n) == _norm(arg)]
        if not matches:
            matches = [n for n in names if search_util.matches(arg, n)]
        if len(matches) == 1:
            name = matches[0]
            sh.open_playlist(name)
            sh._pp_play(False)
            return text(f"Плейлист «{name}»", f"Playlist “{name}”")
        if len(matches) > 1:
            return text("Несколько плейлистов — уточните название", "Several playlists match; say the full name")
        return text(f"Не нашёл плейлист «{arg}»", f"Playlist “{arg}” not found")
    if act == "find" and arg:
        import search_util
        for i, track in enumerate(win.library):
            if search_util.track_matches(arg, track):
                win._play_index(i, "library")
                label = f"{track.get('artist', '')} — {track.get('title', '')}"
                return text(f"Включаю: {label}", f"Playing: {label}")
        return text(f"Не нашёл: «{arg}»", f"No match: “{arg}”")
    return ""


class ScenarioRunner(QObject):
    """Ordered automation steps; waits use Qt timers and never block the UI."""
    def __init__(self, listener):
        super().__init__(listener)
        self.listener = listener
        self.steps = []
        self.name = ""
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._next)

    def cancel(self):
        self.timer.stop()
        self.steps = []

    def start(self, rule):
        clean = validate_commands([rule])[0]
        self.cancel()
        self.steps = list(clean["steps"])
        self.name = clean["name"]
        self.timer.start(0)
        return tr(self.listener.win, f"Сценарий «{self.name}»", f"Scenario “{self.name}”")

    def _next(self):
        win = self.listener.win
        if not self.steps:
            self.listener.state.emit(tr(win, f"Сценарий «{self.name}» завершён", f"Scenario “{self.name}” finished"))
            return
        step = self.steps.pop(0)
        action = step["a"]
        if action == "wait":
            self.timer.start(step["v"] * 1000)
            return
        try:
            from voice_commands import AUTOMATION_ACTIONS
            if action == "quieter":
                # Voice scenarios can run outside a game. A relative reduction
                # here must not arm automation's game-specific restoration.
                win.vol.setValue(round(win.vol.value() * (100 - step["v"]) / 100))
            elif action in AUTOMATION_ACTIONS:
                if action == "playlist":
                    sh = getattr(win, "_ya", None)
                    if sh is None or step["name"] not in sh.user_playlists():
                        raise ValueError(tr(win, "Плейлист больше не существует", "Playlist no longer exists"))
                import ya_auto
                if not ya_auto.get(win).run({"do": step, "when": {"t": "voice"}}):
                    raise RuntimeError(tr(win, "Не удалось выполнить шаг", "Could not complete the step"))
            else:
                execute(win, action, step.get("text", ""))
        except Exception as error:                         # noqa: BLE001
            self.cancel()
            self.listener.state.emit(tr(win, f"Сценарий остановлен: {error}", f"Scenario stopped: {error}"))
            return
        self.timer.start(0)


class Listener(QObject):
    heard = pyqtSignal(str, str)
    state = pyqtSignal(str)
    _prepared = pyqtSignal(int, bool, str)
    _recognized = pyqtSignal(int, str, str, str, bool)

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.stream = None
        self.always = False
        self.ptt_until = 0.0
        self._busy_generation = None
        self._ready = False
        self._requested_ptt = False
        self._generation = 0
        self._jobs = queue.Queue(maxsize=64)
        self._worker = None
        self._closed = threading.Event()
        self._last = ("", "", 0.0)
        self.scenario = ScenarioRunner(self)
        self._prepared.connect(self._on_prepared)
        self._recognized.connect(self._done)
        from PyQt6.QtWidgets import QApplication
        if QApplication.instance() is not None:
            QApplication.instance().aboutToQuit.connect(self.close)

    def _say(self, ru, en):
        return tr(self.win, ru, en)

    @property
    def busy(self):
        # A late worker can own only its original generation. Even if restart
        # interleaves between the generation check and this token's assignment,
        # an old decode can never mark the replacement session busy.
        return self._busy_generation == self._generation

    def _start(self, ptt):
        self._generation += 1
        self._ready = False
        self._busy_generation = None
        self._requested_ptt = ptt
        self.ptt_until = 0
        self._close_stream()
        self._language = self.win.settings.get("voice_cmd_language", "en" if english(self.win) else "ru")
        self._strict = self.win.settings.get("voice_cmd_sensitivity", "normal") == "strict"
        while True:
            try:
                self._jobs.get_nowait()
            except queue.Empty:
                break
        if self._worker is None or not self._worker.is_alive():
            self._closed.clear()
            self._worker = threading.Thread(target=self._work, name="echoes-voice", daemon=True)
            self._worker.start()
        self.state.emit(self._say("Готовлю локальное распознавание…", "Preparing local recognition…"))
        self._jobs.put_nowait(("prepare", self._generation, None, ptt))

    def _ensure_stream(self):
        if self.stream is not None:
            return True
        try:
            import sounddevice as sd
            self.stream = sd.InputStream(samplerate=SR, channels=1, dtype="float32", blocksize=0,
                                         latency="low", callback=self._cb)
            self.stream.start()
            return True
        except Exception as error:                         # noqa: BLE001
            self.state.emit(self._say(f"Микрофон недоступен: {error}", f"Microphone unavailable: {error}"))
            self._close_stream()
            return False

    def _close_stream(self):
        stream, self.stream = self.stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:                             # noqa: BLE001
                pass

    def set_always(self, on):
        self.always = bool(on)
        if on:
            self._start(False)
        elif time.monotonic() >= self.ptt_until:
            self._generation += 1
            self._ready = False
            self._busy_generation = None
            self._requested_ptt = False
            self._close_stream()
            self.state.emit(self._say("Выключено", "Off"))

    def restart(self):
        if self.always or self.ptt_until > time.monotonic() or self._requested_ptt:
            self._start(self._requested_ptt or self.ptt_until > time.monotonic())

    def push_to_talk(self):
        if self.busy:
            self.state.emit(self._say("Заканчиваю предыдущую команду…", "Finishing the previous command…"))
            return
        self._start(True)

    def _on_prepared(self, generation, ready, error):
        if generation != self._generation or self._closed.is_set():
            return
        if not ready:
            self._requested_ptt = False
            self.state.emit(error or self._say("Нет локальной модели распознавания", "No local recognition model"))
            return
        if not self._ensure_stream():
            self._requested_ptt = False
            return
        self._ready = True
        if self._requested_ptt:
            self.ptt_until = time.monotonic() + 5
            QTimer.singleShot(5050, lambda g=generation: self._ptt_end(g))
            message = self._say("Говорите команду…", "Say a command…")
            import ya_quick
            ya_quick._say(self.win, message)
            self.state.emit(message)
        else:
            self.state.emit(self._say("Слушаю: «ECHOES, …»", "Listening: “ECHOES, …”"))

    def _ptt_end(self, generation):
        if generation != self._generation or not self._requested_ptt:
            return
        if not self.busy:
            try:
                self._jobs.put_nowait(("flush", generation, None, True))
            except queue.Full:
                pass
        if not self.always:
            self._close_stream()

    def _cb(self, indata, frames, timestamp, status):
        # No model, segmentation, concatenation, threads or Qt work in PortAudio.
        ptt = time.monotonic() < self.ptt_until
        if not self._ready or self.busy or not (self.always or ptt):
            return
        try:
            self._jobs.put_nowait(("audio", self._generation, indata[:, 0].copy(), ptt))
        except queue.Full:
            self._ready = False
            self.state.emit(self._say("Аудиопоток перегружен — включите микрофон заново",
                                      "Audio overloaded; restart the microphone"))

    def _work(self):
        gate = SpeechBuffer()
        language, strict = "ru", False
        while not self._closed.is_set():
            try:
                kind, generation, audio, ptt = self._jobs.get(timeout=.2)
            except queue.Empty:
                continue
            if generation != self._generation:
                continue
            if kind == "prepare":
                gate = SpeechBuffer(self._strict)
                language, strict = self._language, self._strict
                try:
                    ready = load_model() is not None
                    self._prepared.emit(generation, ready, "")
                except Exception as error:                 # noqa: BLE001
                    self._prepared.emit(generation, False,
                                        self._say(f"Не удалось загрузить модель: {error}", f"Model load failed: {error}"))
                continue
            if self.busy:
                gate.reset()
                continue
            utterance = gate.finish() if kind == "flush" else gate.feed(audio)
            if generation != self._generation:
                gate.reset()
                continue
            if utterance is None and kind != "flush":
                continue
            self._busy_generation = generation
            self.state.emit(self._say("Распознаю…", "Recognising…"))
            text, action, argument = "", "", ""
            try:
                if utterance is not None:
                    text = transcribe(utterance, language, strict)
                    action, argument = route(text, commands(self.win), require_wake=not ptt)
            except Exception as error:                     # noqa: BLE001
                print("[voice]", error)
            if generation == self._generation:
                self._recognized.emit(generation, text, action, argument, ptt)
            gate.reset()

    def _done(self, generation, text, action, argument, ptt):
        if generation != self._generation:
            return
        result = ""
        try:
            last_action, last_arg, last_time = self._last
            duplicate = not ptt and (action, argument) == (last_action, last_arg) and time.monotonic() - last_time < 1.2
            if action and not duplicate:
                if action == "scenario":
                    rule = next((r for r in commands(self.win) if str(r.get("id")) == argument and r.get("on", True)), None)
                    result = self.scenario.start(rule) if rule else ""
                else:
                    result = execute(self.win, action, argument)
                self._last = (action, argument, time.monotonic())
            if result or ptt:
                message = result or self._say("Не расслышал команду", "Command not recognised")
                import ya_quick
                ya_quick._say(self.win, message)
                self.state.emit(f"«{text}» → {message}" if text else message)
                self.heard.emit(text, result)
            elif not action:
                self.state.emit(self._say("Слушаю: «ECHOES, …»", "Listening: “ECHOES, …”"))
        except Exception as error:                         # noqa: BLE001
            self.state.emit(self._say(f"Ошибка команды: {error}", f"Command failed: {error}"))
        finally:
            if self._busy_generation == generation:
                self._busy_generation = None
            if ptt:
                self._requested_ptt = False
                self.ptt_until = 0
            if not self.always and self.ptt_until <= time.monotonic():
                self._ready = False
                self._close_stream()

    def close(self):
        self._generation += 1
        self._closed.set()
        self._ready = False
        self._close_stream()
        self.scenario.cancel()


def get(win) -> Listener:
    listener = getattr(win, "_voice_cmd", None)
    if listener is None:
        listener = win._voice_cmd = Listener(win)
    return listener


def install(win):
    if win.settings.get("voice_cmd_always"):
        QTimer.singleShot(1200, lambda: get(win).set_always(True))


def settings_card(shell):
    from PyQt6.QtWidgets import QCheckBox, QComboBox, QLabel, QPushButton, QVBoxLayout, QHBoxLayout
    from echo_glass import GlassFrame, glass_controls_qss
    win = shell.win
    t = lambda ru, en: tr(win, ru, en)
    card = GlassFrame(radius=16)
    card.setMaximumWidth(800)
    card.setStyleSheet("QLabel,QCheckBox{color:#edf3fb;background:transparent;}"
                      "QLabel#YaSub{color:#a8b5c6;} QPushButton{border-radius:10px;padding:7px 12px;}"
                      "QComboBox{border-radius:8px;padding:6px 10px;}" + glass_controls_qss())
    v = QVBoxLayout(card)
    v.setContentsMargins(18, 16, 18, 16)
    title = QLabel(t("Голосовые команды", "Voice commands"))
    title.setStyleSheet("font-size:15px;font-weight:700;background:transparent;")
    v.addWidget(title)
    available = model_name() is not None
    description = QLabel(t(
        "«Эхос, следующий», «Эхос, пауза», «включи плейлист Дорога». Ctrl+Alt+V — одна команда без обращения. "
        "В фоне обязательно начинайте с «Эхос» или «ECHOES». Непонятная речь ничего не запускает. Всё обрабатывается на ПК.",
        "“ECHOES, next track”, “ECHOES, pause”, “play playlist Road”. Ctrl+Alt+V — one command without a wake word. "
        "Background commands must start with “ECHOES”. Unrecognised speech does nothing. Audio stays on this PC."))
    description.setObjectName("YaSub")
    description.setWordWrap(True)
    v.addWidget(description)
    if not available:
        missing = QLabel(t("Нужны faster-whisper и локальная модель: установите их через синхронизацию текста песни.",
                           "faster-whisper and a local model are required: install them through lyrics synchronisation."))
        missing.setWordWrap(True)
        missing.setObjectName("YaSub")
        v.addWidget(missing)
    settings = QHBoxLayout()
    settings.addWidget(QLabel(t("Язык речи:", "Speech language:")))
    language = QComboBox()
    for label, key in (("Русский", "ru"), ("English", "en"), (t("Авто (медленнее)", "Auto (slower)"), "auto")):
        language.addItem(label, key)
    language.setCurrentIndex(max(0, language.findData(win.settings.get("voice_cmd_language", "en" if english(win) else "ru"))))
    settings.addWidget(language)
    settings.addWidget(QLabel(t("Фильтр:", "Filter:")))
    sensitivity = QComboBox()
    sensitivity.addItem(t("Обычный", "Normal"), "normal")
    sensitivity.addItem(t("Строгий (для шума)", "Strict (noisy room)"), "strict")
    sensitivity.setCurrentIndex(max(0, sensitivity.findData(win.settings.get("voice_cmd_sensitivity", "normal"))))
    settings.addWidget(sensitivity)
    settings.addStretch(1)
    v.addLayout(settings)

    def save_option(key, value):
        win.settings[key] = value
        win.save_settings()
        get(win).restart()
    language.currentIndexChanged.connect(lambda: save_option("voice_cmd_language", language.currentData()))
    sensitivity.currentIndexChanged.connect(lambda: save_option("voice_cmd_sensitivity", sensitivity.currentData()))
    row = QHBoxLayout()
    always = QCheckBox(t("Всегда слушать «ECHOES, …»", "Always listen for “ECHOES, …”"))
    always.setChecked(bool(win.settings.get("voice_cmd_always")))
    always.setEnabled(available)

    def toggle(on):
        win.settings["voice_cmd_always"] = bool(on)
        win.save_settings()
        get(win).set_always(on)
    always.toggled.connect(toggle)
    row.addWidget(always)
    listen = QPushButton(t("Сказать команду", "Say a command"))
    listen.setObjectName("AccentBtn")
    listen.setEnabled(available)
    listen.clicked.connect(lambda: get(win).push_to_talk())
    row.addWidget(listen)
    row.addStretch(1)
    v.addLayout(row)
    custom = QPushButton(t("Свои команды и сценарии", "Custom commands and scenarios"))
    custom.clicked.connect(lambda: __import__("voice_editor").CommandsDialog(win).exec())
    v.addWidget(custom)
    status = QLabel("")
    status.setObjectName("YaSub")
    status.setWordWrap(True)
    v.addWidget(status)
    get(win).state.connect(status.setText)
    return card
