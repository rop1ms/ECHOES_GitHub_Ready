# audio_engine.py
import data_home
import os
import sys
import math
import time
import platform
import threading
import ctypes
import subprocess
import tempfile
import wave
import struct
from ctypes import c_void_p, c_ubyte, c_uint, c_int64, c_size_t, POINTER, CFUNCTYPE
from collections import OrderedDict

import numpy as np


class _LazyScipySignal:
    """scipy.signal нужен только для реверба пресетов — грузим его при первом обращении:
    scipy при старте плеера — это ~70 МБ памяти и полсекунды запуска."""
    _mod = None

    def __getattr__(self, name):
        if _LazyScipySignal._mod is None:
            from scipy import signal
            _LazyScipySignal._mod = signal
        return getattr(_LazyScipySignal._mod, name)


scipy_signal = _LazyScipySignal()

if platform.system() == "Windows":
    from vlc_location import find_libvlc as _find_libvlc

    def _ensure_plugin_cache(d):
        """plugins\\plugins.dat — кэш плагинов VLC. Без него (или устаревшего — VLC обновили) libvlc при каждом
        запуске загружает ВСЕ ~365 плагинов (138 МБ DLL) и держит их в памяти; с ним — около 10 (3 МБ).
        На «холодном» диске это десятки секунд запуска. vlc-cache-gen создаёт кэш за полсекунды, один раз."""
        try:
            gen = os.path.join(d, "vlc-cache-gen.exe")
            plugins = os.path.join(d, "plugins")
            dat = os.path.join(plugins, "plugins.dat")
            if not (os.path.isfile(gen) and os.path.isdir(plugins)):
                return
            if os.path.isfile(dat):
                newest = max(os.path.getmtime(p) for p in (os.path.join(d, "libvlccore.dll"), os.path.join(d, "libvlc.dll"))
                             if os.path.isfile(p))
                if os.path.getmtime(dat) >= newest:
                    return
            probe = os.path.join(plugins, ".echoes_write_test")
            try:                                           # Program Files без прав администратора — не трогаем
                open(probe, "wb").close()                  # (os.access на Windows не видит запрет ACL)
                os.remove(probe)
            except OSError:
                return
            import subprocess
            subprocess.run([gen, plugins], cwd=d, timeout=60, capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:                             # noqa: BLE001
            print("[audio_engine] plugins.dat:", e)

    _vlc_dir = _find_libvlc()
    if _vlc_dir:
        _ensure_plugin_cache(_vlc_dir)
        _cwd = os.getcwd()
        # Resolve env paths before changing cwd; stale overrides must not hide the bundle.
        _vlc_lib_path = os.environ.get("PYTHON_VLC_LIB_PATH", "")
        _vlc_module_path = os.environ.get("PYTHON_VLC_MODULE_PATH", "")
        os.environ["PYTHON_VLC_LIB_PATH"] = (os.path.abspath(_vlc_lib_path)
            if _vlc_lib_path and os.path.isfile(_vlc_lib_path) else os.path.join(_vlc_dir, "libvlc.dll"))
        os.environ["PYTHON_VLC_MODULE_PATH"] = (os.path.abspath(_vlc_module_path)
            if _vlc_module_path and os.path.isdir(_vlc_module_path) else _vlc_dir)
        try:
            os.chdir(_vlc_dir)
            _vlc_dll_directory = os.add_dll_directory(_vlc_dir)
            os.environ["PATH"] = _vlc_dir + os.pathsep + os.environ.get("PATH", "")
            # The selected exact DLL bypasses python-vlc's second library search.
            import vlc
        finally:
            os.chdir(_cwd)
    else:
        print("[audio_engine] ОШИБКА: libvlc.dll не найден. Установите VLC: https://www.videolan.org/")
        sys.exit(1)
else:
    import vlc

from PyQt6.QtCore import QObject, pyqtSignal, QTimer, Qt


# ---------------------------------------------------------------------- #
#  Захват реального аудиопотока плеера (smem) — для визуализатора.        #
#  Теневой плеер (_vis_player) декодирует тот же файл ТОЛЬКО в smem —    #
#  без dst=display, поэтому он никогда не звучит из колонок.              #
#  Основной self.player — чистое нативное воспроизведение без sout.       #
# ---------------------------------------------------------------------- #
SMEM_RATE = 44100
SMEM_CHANNELS = 1
SMEM_SCRATCH_SIZE = 1 << 20  # с запасом под один рендер-блок

_PRERENDER_CB = CFUNCTYPE(None, c_void_p, POINTER(POINTER(c_ubyte)), c_size_t)
_POSTRENDER_CB = CFUNCTYPE(
    None, c_void_p, POINTER(c_ubyte), c_uint, c_uint, c_uint, c_uint, c_size_t, c_int64
)


class _NullPlayer:
    """Заглушка теневого плеера визуализатора (при своём аудиовыводе он не нужен)."""

    def __getattr__(self, _name):
        return lambda *a, **k: 0


class AudioEngine(QObject):
    finished          = pyqtSignal()
    duration_changed  = pyqtSignal(float)
    position_changed  = pyqtSignal(float)
    state_changed     = pyqtSignal(bool)
    error_changed     = pyqtSignal(str)
    crossfade_started = pyqtSignal(str)  # next_path — трек уже реально играет, пора обновить UI
    _gl_wake          = pyqtSignal()     # фоновый поток стыка → GUI: новая дека запущена

    # Полосы — те, что реально двигает VLC (libvlc 3 всегда берёт стандартные ISO-частоты). Раньше подписи были
    # 60/170/310/600/1K/3K/6K/12K/14K/16K: ползунок «3K» на деле крутил 1 кГц, «14K» — 8 кГц.
    EQ_FREQUENCIES = [31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]

    def __init__(self, parent=None):
        super().__init__(parent)

        _sys = platform.system()
        _args = "--no-xlib " if _sys == "Linux" else ""
        # file-caching=0 — VLC не буферизует локальные файлы, старт мгновенный
        # Без scaletempo: set_rate() меняет и темп, и питч вместе —
        # именно так звучат настоящие slowed reverb и speed up треки.
        # Качество воспроизведения:
        #  * --no-audio-time-stretch — VLC по умолчанию при set_rate() ≠ 1
        #    включает растяжение времени (scaletempo): питч сохраняется, а звук
        #    «режется» на куски с металлическими артефактами. Без него rate
        #    меняет темп и питч вместе — как у настоящей пластинки;
        #  * --audio-resampler=soxr,any — высококачественный ресэмплер (SoX);
        #    если модуля нет в сборке VLC — любой доступный;
        #  * небольшой буфер чтения (250 мс) вместо 0 — без щелчков/заиканий,
        #    когда интерфейс нагружен (старт при этом остаётся мгновенным).
        # Буфер чтения локальных файлов оставлен нулевым (как было): любой
        # буфер удлиняет старт деки и ломает точный gapless-стык.
        # --quiet: без служебного шума VLC в консоли (например «buffer deadlock
        # prevented» от скрытого плеера визуализатора — на звук не влияет)
        _args += ("--file-caching=0 --network-caching=1000 --live-caching=0 --disc-caching=0 "
                  "--no-audio-time-stretch --audio-resampler=soxr,any --quiet")
        self.instance = vlc.Instance(_args)

        self.player = self.instance.media_player_new()

        # Предзагруженные медиа: path -> media, LRU-кэш на несколько треков
        # (следующий + предыдущий + запас), чтобы переключение в обе стороны
        # было мгновенным, а не только "вперёд".
        self._preload_cache = OrderedDict()
        self._preload_urls = {}                     # путь «stream:…» → ссылка, с которой готовили media
        self.url_resolver = None                    # callable("stream:…") -> прямая ссылка на поток (stream_core)
        self._preload_cache_limit = 4

        self.vlc_eq = vlc.AudioEqualizer()
        self.vlc_eq.set_preamp(0.0)
        self.player.set_equalizer(None)     # плоский EQ — в обход (см. _eq_for_player)
        self._eq_makeup_db = 0.0   # компенсация широкополосной просадки от полос EQ

        self.current_path = ""
        self._duration    = 0.0
        self.volume       = 0.8
        self.gain         = 1.0     # коэффициент выравнивания громкости текущего трека
        self._gain_next   = 1.0     # то же для трека, который въезжает при кроссфейде/гэплессе
        self.gain_provider = None   # callable(path) -> линейный коэффициент (ставит MainWindow)
        self.speed        = 1.0
        self._boost       = 1.0     # экстремальный множитель громкости (×1..×100, из настроек)

        # ── Пресеты: Slowed+Reverb / Speed Up ────────────────────────
        # VLC set_rate() без scaletempo меняет темп И питч вместе —
        # именно так звучат настоящие slowed reverb и speed up:
        #   slowed: голос "грузит" вниз, темп замедляется
        #   speedup: голос "чирикает" вверх, темп ускоряется
        # Реверб для slowed добавляется через предобработку аудио:
        # ffmpeg декодирует → scipy свёртывает с IR → temp wav → VLC играет.
        self.preset_mode  = None   # None | "slowed" | "speedup"
        self._preset_temp_path = None  # путь к временному обработанному файлу

        self.crossfade_enabled = False
        self.crossfade_ms      = 1200
        self.gapless_enabled   = False

        self._apply_loudness()

        # ── вторая звучащая дека — для кроссфейда ─────────────────────
        # Пока self.player играет текущий трек, _player_alt тихо
        # запускает следующий и постепенно "въезжает" по громкости;
        # по завершении фейда декам меняют роли местами.
        self._player_alt = self.instance.media_player_new()
        self._player_alt.audio_set_volume(0)

        # ── собственный аудиовывод (audio_out): сэмпл-точный gapless ──────
        # Деки VLC отдают PCM в микшер, а он играет один непрерывный поток.
        # Нет sounddevice/устройства — mixer = None, VLC выводит звук сам.
        self.mixer = None
        self._decks = {}
        try:
            import audio_out
            self.mixer = audio_out.create_mixer()
        except Exception as e:                       # noqa: BLE001
            print("[audio] own output unavailable:", e)
            self.mixer = None
        if self.mixer is not None:
            for _p, _n in ((self.player, "A"), (self._player_alt, "B")):
                _d = self.mixer.new_deck(_n)
                _d.attach(_p)
                self._decks[id(_p)] = _d
            self.mixer.vis_cb = self._mixer_vis
            self._dev_paused = False
            if hasattr(self.mixer, "on_device"):
                self.mixer.on_device = self._device_state
            try:
                from PyQt6.QtWidgets import QApplication
                app = QApplication.instance()
                if app is not None:
                    app.aboutToQuit.connect(self.shutdown)
            except Exception:                        # noqa: BLE001
                pass

        self._fade_timer = QTimer(self)
        self._fade_timer.setInterval(30)
        self._fade_timer.timeout.connect(self._fade_step)
        self._fade_active     = False
        self._fade_from       = None
        self._fade_to         = None
        self._fade_elapsed_ms = 0
        self._fade_duration_ms = 0
        self._fade_next_path  = ""
        # Гэплесс-переключение отслеживает РЕАЛЬНЫЙ конец старой деки
        # (vlc.State.Ended), а не только заранее рассчитанное время —
        # оценка "сколько осталось" по duration/position у VLC может быть
        # неточной (особенно на VBR-файлах), и если положиться только на
        # таймер, переключение иногда происходит позже, чем трек реально
        # замолкает — получается пауза/тишина вместо гэплесса.
        self._gapless_watch = False
        # Точный таймер стыка (5 мс) — работает только последние ~1.5 с трека
        self._gl_timer = QTimer(self)
        self._gl_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._gl_timer.setInterval(20)
        self._gl_timer.timeout.connect(self._gl_step)
        # Момент старта следующей деки ловит отдельный поток (не GUI): в тяжёлых
        # темах (Echoes Music: свечение, страницы) GUI-таймер опаздывал на
        # десятки мс — на стыке появлялась слышимая пауза.
        self._gl_token = 0
        self._gl_wake.connect(self._gl_timer.start)
        self._smooth = (None, 0.0, 0.0)   # (сырое время VLC, момент, оценка) — плавная позиция
        self._gl_phase = None
        self._gl_start_est_ms = 60.0      # задержка старта деки (по журналу: ~25–60 мс; подстраивается)
        self._gl_clock = (None, 0.0)       # (последнее время VLC, момент его смены) — для экстраполяции
        self._gl_t0 = 0.0
        self._gl_measured = False
        self._gl_seam_t0 = 0.0

        self._was_playing = False
        self._want_playing = False
        self._last_state  = None

        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self._poll_state)
        self.timer.start()

        # ── захват PCM для визуализатора ──────────────────────────────
        self._audio_lock = threading.Lock()
        self._ring_size = SMEM_RATE * 3  # 3 секунды про запас
        self._ring = np.zeros(self._ring_size, dtype=np.float32)
        self._ring_pos = 0
        self._ring_filled = 0
        self._smem_scratch = ctypes.create_string_buffer(SMEM_SCRATCH_SIZE)

        # ссылки на callback-объекты нужно держать в self, иначе их соберёт GC
        self._prerender_cb = _PRERENDER_CB(self._smem_prerender)
        self._postrender_cb = _POSTRENDER_CB(self._smem_postrender)
        self._sout_option = self._build_smem_sout()

        # Отдельный "теневой" плеер только для визуализации.
        # sout направлен исключительно в smem — dst=display убран,
        # поэтому звук из колонок идёт ТОЛЬКО через основной self.player.
        # Со своим аудиовыводом визуализатор получает ровно то, что звучит, —
        # второе декодирование тем же VLC не нужно.
        self._vis_player = _NullPlayer() if self.mixer is not None else self.instance.media_player_new()

        # ── Vinyl Warp (wow & flutter) ─────────────────────────────────
        # Имитация детонации старого/погнутого винила: медленная "wow"
        # синусоида (~0.6 Гц) + быстрая "flutter" (~6 Гц) чуть модулируют
        # playback rate. Применяется только к основному плееру — vis_player
        # намеренно не подтягиваем за питчем, чтобы не искажать частотный
        # анализ визуализатора мелкими сдвигами скорости.
        self.warp_enabled = False
        self._warp_t = 0.0
        self._vari_pause_timer = QTimer(self)                  # пауза VLC в конце торможения пластинки
        self._vari_pause_timer.setSingleShot(True)
        self._vari_pause_timer.timeout.connect(self._vari_pause_now)
        self._warp_timer = QTimer(self)
        self._warp_timer.setInterval(40)
        self._warp_timer.timeout.connect(self._warp_step)
        self._warp_timer.start()

        # ── Scratch Pause: эффект остановки/раскрутки пластинки ─────────
        # Вместо мгновенного obрыва звука на паузе — короткое "торможение"
        # (плавное падение rate+volume), и симметричный быстрый "разгон"
        # при возобновлении. И то, и другое двигает те же самые
        # player.set_rate()/audio_set_volume(), которые уже используются
        # для скорости и громкости — никаких доп. звуковых файлов не нужно.
        self.vinyl_pause_fx = True
        self._spindown_active = False
        self._spindown_t = 0.0
        self._spindown_start_rate = 1.0
        self._spindown_timer = QTimer(self)
        self._spindown_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._spindown_timer.setInterval(10)
        self._spindown_timer.timeout.connect(self._spindown_step)

        self._spinup_active = False
        self._spinup_t = 0.0
        self._spinup_timer = QTimer(self)
        self._spinup_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._spinup_timer.setInterval(10)
        self._spinup_timer.timeout.connect(self._spinup_step)

        # ── Anti-click fade-in при обычном переключении треков ──────────
        # Не только кроссфейд/спин-эффекты должны быть плавными: если файл
        # начинается не с полной тишины, резкий скачок громкости с 0 на
        # 100% в момент load()+play() слышен как щелчок. Короткий (120 мс)
        # линейный разгон громкости с нуля устраняет его без заметной
        # задержки старта.
        self._startfade_active = False
        self._startfade_t = 0.0
        self._startfade_timer = QTimer(self)
        self._startfade_timer.setInterval(15)
        self._startfade_timer.timeout.connect(self._startfade_step)

    def _build_smem_sout(self) -> str:
        pre_addr = ctypes.cast(self._prerender_cb, c_void_p).value
        post_addr = ctypes.cast(self._postrender_cb, c_void_p).value
        # Только transcode → smem, без duplicate{dst=display}.
        # Теневой плеер декодирует аудио в кольцевой буфер и никогда
        # не выводит его на звуковую карту — дублирования звука нет.
        return (
            ":sout=#"
            f"transcode{{acodec=s16l,channels={SMEM_CHANNELS},samplerate={SMEM_RATE}}}:"
            f"smem{{audio-prerender-callback={pre_addr},audio-postrender-callback={post_addr},"
            "audio-data=0}"
        )

    def _smem_prerender(self, p_audio_data, pp_pcm_buffer, size):
        # VLC просит буфер под очередной блок — отдаём заранее выделенную область.
        try:
            if size <= SMEM_SCRATCH_SIZE:
                pp_pcm_buffer[0] = ctypes.cast(self._smem_scratch, POINTER(c_ubyte))
        except Exception:
            pass

    def _smem_postrender(self, p_audio_data, p_pcm_buffer, channels, rate, nb_samples, bits_per_sample, size, pts):
        # Вызывается на внутреннем потоке VLC сразу после рендера блока.
        try:
            if size <= 0:
                return
            raw = ctypes.string_at(p_pcm_buffer, size)
            arr = np.frombuffer(raw, dtype=np.int16)
            if channels and channels > 1:
                arr = arr.reshape(-1, channels).mean(axis=1)
            samples = arr.astype(np.float32) * (1.0 / 32768.0)
            with self._audio_lock:
                self._ring_write(samples)
        except Exception:
            pass

    def _ring_write(self, samples: "np.ndarray"):
        n = len(samples)
        if n <= 0:
            return
        if n >= self._ring_size:
            self._ring[:] = samples[-self._ring_size:]
            self._ring_pos = 0
        else:
            end = self._ring_pos + n
            if end <= self._ring_size:
                self._ring[self._ring_pos:end] = samples
            else:
                first = self._ring_size - self._ring_pos
                self._ring[self._ring_pos:] = samples[:first]
                self._ring[:end - self._ring_size] = samples[first:]
            self._ring_pos = end % self._ring_size
        self._ring_filled = min(self._ring_size, self._ring_filled + n)

    # ── свой аудиовывод: деки, позиция, визуализатор ── #

    def _deck(self, player):
        if getattr(self, "mixer", None) is None:        # ещё в конструкторе / свой вывод выключен
            return None
        return self._decks.get(id(player))

    def _stop_player(self, player):
        """stop() деки VLC. Сначала очищаем деку микшера — её drain мог ждать
        доигрывания, и stop() VLC иначе ждал бы его."""
        d = self._deck(player)
        if d is not None:
            d.reset()
        player.stop()

    def _silence(self, player):
        """audio_set_volume(0) + ноль в деке своего вывода."""
        player.audio_set_volume(0)
        d = self._deck(player)
        if d is not None:
            d.amp = 0.0

    def _set_mute(self, player, mute: bool):
        player.audio_set_mute(bool(mute))
        d = self._deck(player)
        if d is not None:
            d.mute = bool(mute)

    def _pos_of(self, player):
        """Точная позиция по своему выводу: якорь + реально прозвучавшие сэмплы.
        None — если якоря нет (тогда считаем по часам VLC)."""
        d = self._deck(player)
        if d is None:
            return None
        h = d.heard_seconds()
        if h is None:
            return None
        base, sec = h
        return base + sec * max(0.25, float(self.speed or 1.0))

    def _lag(self, player) -> float:
        d = self._deck(player)
        return d.lag_seconds() * max(0.25, float(self.speed or 1.0)) if d is not None else 0.0

    def _device_state(self, ok: bool):
        """Звуковое устройство пропало / вернулось (поток сторожа микшера; libvlc потокобезопасен).
        Пока устройства нет, VLC стоит на паузе: иначе он копил звук, а потом выбрасывал «опоздавший» —
        после запуска игры или смены наушников трек перескакивал вперёд на секунды."""
        decks = [p for p in (self.player, self._fade_to if self._fade_active else None) if p is not None]
        if not ok:
            if self._want_playing and any(p.get_state() == vlc.State.Playing for p in decks):
                self._dev_paused = True
                for p in decks:
                    p.set_pause(1)
        elif self._dev_paused:
            self._dev_paused = False
            if self._want_playing:
                for p in decks:
                    if p.get_state() == vlc.State.Paused:
                        p.set_pause(0)

    def _mixer_vis(self, mono):
        with self._audio_lock:
            self._ring_write(mono)

    def shutdown(self):
        """Выход из приложения. Сначала глушим деки VLC (их потоки вызывают наши
        колбэки), и только потом закрываем вывод — иначе при выходе VLC мог
        позвать колбэк уже разрушенного Python-объекта (редкий вылет)."""
        m = self.mixer
        if m is None or m.closed:
            return
        for p in (self.player, self._player_alt):
            try:
                self._stop_player(p)
            except Exception:                        # noqa: BLE001
                pass
        m.close()

    @property
    def own_output(self) -> bool:
        return self.mixer is not None

    def get_visual_samples(self, n: int = 2048):
        """Последние n семплов реально звучащего аудио, моно, float32 в [-1, 1].
        None, если данных ещё недостаточно (например, только начали трек)."""
        if getattr(self.mixer, 'native', False):
            return self.mixer.visual_samples(n)
        with self._audio_lock:
            # свой вывод пишет в кольцо то, что уйдёт в колонки через latency —
            # отступаем на неё назад, чтобы картинка совпадала со звуком
            lat = int(self.mixer.latency_s * self.visual_sample_rate) if self.mixer is not None else 0
            lat = min(lat, max(0, self._ring_filled - n))
            if self._ring_filled < n:
                return None
            start = (self._ring_pos - n - lat) % self._ring_size
            if start + n <= self._ring_size:
                return self._ring[start:start + n].copy()
            return np.concatenate([self._ring[start:], self._ring[:start + n - self._ring_size]])

    @property
    def visual_sample_rate(self) -> int:
        if self.mixer is not None:
            import audio_out
            return audio_out.SR
        return SMEM_RATE

    def get_level(self) -> float:
        """RMS уровня последних семплов реально играющего аудио (~0..1) —
        используется для реактивной подсветки диска/иглы в VinylWidget.
        Переиспользует тот же кольцевой буфер, что и визуализатор, поэтому
        не требует librosa/sounddevice и отдельного аудио-захвата."""
        samples = self.get_visual_samples(1024)
        if samples is None:
            return 0.0
        return float(np.sqrt(np.mean(np.square(samples))))

    @property
    def duration(self):
        return float(self._duration)

    # ------------------------------------------------------------------ #
    #  EQ                                                                  #
    # ------------------------------------------------------------------ #

    # Эквалайзер VLC при preamp = 0 дБ ПРИГЛУШАЕТ весь сигнал на 12 дБ:
    # единичное усиление у него — preamp +12 дБ (так и в пресете «Flat» самого
    # VLC). Раньше EQ цеплялся с preamp ≈ 0, поэтому стоило подвинуть бас —
    # и весь трек проседал на 12 дБ (буст +6 дБ возвращал только 2–5 дБ).
    # Замер (pink noise / 60 Гц): preamp N дБ → N − 12 дБ относительно обхода.
    EQ_UNITY_DB = 12.0
    EQ_PREAMP_MAX = 20.0

    EQ_HEADROOM_PAD = 1.5               # соседние полосы складываются — чуть больше запаса

    def _eq_headroom_db(self) -> float:
        """На сколько опустить preamp, чтобы поднятые полосы (басс буст, пресет, автопресет по жанру)
        не упирались в 0 дБ внутри VLC. Звук отдаётся в 16 битах, и всё выше 0 дБ там срезалось —
        на басу это звучало «жужжанием», хотя сам файл чистый. Опущенное возвращается громкостью
        в нашем выводе (с плавающей точкой), а громкие пики мягко прижимает лимитер."""
        if self._eq_for_player() is None:
            return 0.0
        top = max(self.vlc_eq.get_amp_at_index(i) for i in range(10)) + self._eq_makeup_db
        return min(30.0, top + self.EQ_HEADROOM_PAD) if top > 0.05 else 0.0

    def _eq_for_player(self):
        """Плоский EQ без компенсации = без эквалайзера: сигнал идёт мимо
        фильтров VLC бит-в-бит. Как только есть хоть одна полоса или makeup —
        EQ прицепляется (с preamp от единичного уровня, см. EQ_UNITY_DB)."""
        flat = all(abs(self.vlc_eq.get_amp_at_index(i)) < 0.05 for i in range(10))
        return None if (flat and self._eq_makeup_db < 0.05) else self.vlc_eq

    def _apply_eq(self, player=None):
        (player or self.player).set_equalizer(self._eq_for_player())

    def _update_makeup(self):
        """Широкополосная оценка суммарного действия 10 полос (взвешенное
        среднее их линейных коэффициентов). Если пресет в среднем ПРИГЛУШАЕТ
        сигнал (V-образные кривые, срез середины), компенсируем просадку
        preamp'ом — EQ никогда не делает звук тише ползунка.
        Положительный net не трогаем: громкость и так вырастет."""
        gains = np.array([self.vlc_eq.get_amp_at_index(i) for i in range(10)], dtype=np.float64)
        lin = np.power(10.0, gains / 20.0)
        w = 1.0 / (1.0 + np.power(np.asarray(self.EQ_FREQUENCIES, dtype=np.float64) / 700.0, 1.5))
        net_db = 10.0 * math.log10(float((w * lin).sum() / w.sum()) + 1e-9)
        self._eq_makeup_db = min(self.EQ_PREAMP_MAX - self.EQ_UNITY_DB, max(0.0, -net_db))

    def set_eq_band(self, band_index: int, gain_db: float):
        if 0 <= band_index < 10:
            self.vlc_eq.set_amp_at_index(max(-20.0, min(20.0, float(gain_db))), band_index)
            self._update_makeup()
            self._apply_loudness()

    def get_eq_band(self, band_index: int) -> float:
        return self.vlc_eq.get_amp_at_index(band_index) if 0 <= band_index < 10 else 0.0

    def reset_eq(self):
        for i in range(10):
            self.vlc_eq.set_amp_at_index(0.0, i)
        self._update_makeup()
        self._apply_loudness()

    # ------------------------------------------------------------------ #
    #  Предзагрузка медиа (без воспроизведения)                           #
    # ------------------------------------------------------------------ #

    def preload(self, path: str):
        """Парсит медиа заранее и кладёт в кэш — без запуска воспроизведения.
        Когда load() придёт с тем же путём, media уже готов и play() стартует
        мгновенно. Держим сразу несколько кэшированных треков (не один слот),
        чтобы работало и вперёд, и назад."""
        path = str(path)
        if path.startswith("stream:") and (self.url_resolver is None or not self.url_resolver(path)):
            return                                  # ссылки на поток ещё нет — нечего готовить
        if not path or path in self._preload_cache:
            self._preload_cache.move_to_end(path) if path in self._preload_cache else None
            return
        media = self._media(path)
        if not path.startswith("stream:"):
            media.parse_with_options(vlc.MediaParseFlag.local, 0)
        self._preload_cache[path] = media
        self._preload_urls[path] = self.url_resolver(path) if path.startswith("stream:") and self.url_resolver else ""
        while len(self._preload_cache) > self._preload_cache_limit:
            old, _m = self._preload_cache.popitem(last=False)
            self._preload_urls.pop(old, None)

    def _take_preloaded(self, path: str):
        """Заготовленный media (или None). Онлайн-трек — только если ссылка та же: после «ссылка умерла →
        взяли новую» заготовка держала бы мёртвую, и трек снова не включился бы."""
        media = self._preload_cache.pop(path, None)
        url = self._preload_urls.pop(path, "")
        if (media is not None and path.startswith("stream:") and self.url_resolver is not None
                and (self.url_resolver(path) or "") != url):
            return None
        return media

    # ------------------------------------------------------------------ #
    #  Воспроизведение                                                     #
    # ------------------------------------------------------------------ #

    def load(self, path, autoplay=True, start_sec=0.0):
        self._want_playing = bool(autoplay)
        self._vari_pause_timer.stop()
        path = str(path)
        self.gain = self._gain_for(path)
        d = self._deck(self.player)
        if d is not None:
            d.reset()                    # set_media ниже остановит деку — drain не должен её держать
            d.set_anchor(float(start_sec) * 1000.0 if start_sec and start_sec > 0.05 else 0.0)

        if self._fade_active:
            # Ручное переключение трека прерывает кроссфейд — глушим обе деки.
            self._fade_timer.stop()
            self._gl_timer.stop()
            self._gl_token += 1
            self._gl_phase = None
            self._fade_active = False
            self._gapless_watch = False
            # Сначала восстанавливаем громкость основного плеера (он мог быть
            # в процессе fade-out и иметь громкость < volume), только потом stop().
            self._apply_loudness()
            self._stop_player(self._player_alt)
            self._silence(self._player_alt)
            self._fade_from = None
            self._fade_to = None

        # Ручная загрузка трека прерывает любой активный spin-down/up —
        # иначе застрявший таймер эффекта продолжит дёргать rate/volume
        # уже нового трека.
        self._spindown_timer.stop()
        self._spindown_active = False
        self._spinup_timer.stop()
        self._spinup_active = False
        self._startfade_timer.stop()
        self._startfade_active = False

        self.current_path = path
        self._last_state  = None
        self._resume_at = None

        media = self._take_preloaded(path)
        if media is None:
            media = self._media(path)

        with self._audio_lock:
            self._ring.fill(0.0)
            self._ring_pos = 0
            self._ring_filled = 0

        # Старт с позиции — опцией медиа, а не seek() сразу после play():
        # такой seek VLC часто теряет (плеер ещё не открыл поток), а опция
        # start-time применяется точно и без щелчка перемотки.
        if start_sec and start_sec > 0.05:
            media.add_option(f":start-time={float(start_sec):.3f}")

        self.player.set_media(media)
        self._set_mute(self.player, False)
        self.player.set_rate(self.speed)
        self._apply_eq()

        # Теневой плеер получает медиа только со sout→smem (без dst=display),
        # поэтому он никогда не воспроизводит звук через колонки.
        vis_media = self._media(path, vis=True)
        vis_media.add_option(self._sout_option)
        if start_sec and start_sec > 0.05:
            vis_media.add_option(f":start-time={float(start_sec):.3f}")
        self._vis_player.stop()
        self._vis_player.set_media(vis_media)
        self._vis_player.set_rate(self.speed)

        if autoplay:
            # Стартуем с нулевой громкости и линейно разгоняем её —
            # убирает щелчок в момент старта воспроизведения (см. комментарий
            # у _startfade_timer в __init__).
            self._silence(self.player)
            self.player.play()
            self._vis_player.play()
            if getattr(self.mixer, 'native', False):
                # Native per-sample gain ramp also works while GUI timers stall.
                self._apply_loudness()
            else:
                self._startfade_t = 0.0
                self._startfade_active = True
                self._startfade_timer.start()
        else:
            self._apply_loudness()
        return True

    def play(self):
        self._want_playing = True
        self._vari_pause_timer.stop()
        if not self.current_path:
            return False
        if self.player.get_state() == vlc.State.Ended:
            # play() у доигравшего трека VLC молча игнорирует (оставалась тишина при повторе трека) —
            # открываем его заново с запомненной позиции
            at, self._resume_at = getattr(self, "_resume_at", None), None
            return self.load(self.current_path, autoplay=True, start_sec=at or 0.0)
        # Запоминаем состояние ДО вызова play() — только реальная пауза
        # (State.Paused) требует nudge для сброса буфера аудиодрайвера.
        # State.Stopped / State.NothingSpecial (новая дека после gapless/crossfade
        # до старта) не нуждаются в nudge — там аудиоустройство ещё не
        # инициализировано, и set_time() на нём просто теряется, что и вызывало
        # тишину при ручном переключении трека (лечилось только pause+play).
        was_paused = self.player.get_state() == vlc.State.Paused
        ok = self.player.play() == 0
        if ok and self.mixer is not None:
            # свой вывод: «залипания» звука после паузы нет — nudge не нужен;
            # прицепленная (gapless) дека тоже продолжает
            if self._fade_active and self._gapless_watch and self._fade_to is not None \
                    and self._fade_to.get_state() == vlc.State.Paused:
                self._fade_to.set_pause(0)
            return ok
        if ok:
            if was_paused:
                # Известный баг libvlc: после pause()->play() позиция идёт,
                # а звук "залипает" (особенно на WASAPI/DirectSound). Лёгкий
                # seek на текущую позицию форсирует сброс буфера аудиовывода
                # и возвращает звук — ровно то, что вручную чинит перемотка.
                # Небольшая задержка нужна, чтобы play() успел перевести
                # плеер из Paused в Playing до самого seek.
                player, path = self.player, self.current_path
                QTimer.singleShot(60, lambda: self._nudge_resume() if self.player is player and self.current_path == path and self._want_playing else None)
            self._vis_player.play()
        return ok

    def _nudge_resume(self):
        t = self.player.get_time()
        if t is not None and t >= 0:
            self.player.set_time(t)

    def _startfade_step(self):
        if not self._startfade_active:return
        if not self._want_playing:
            self._startfade_timer.stop();self._startfade_active=False;self._apply_loudness();return
        self._startfade_t += self._startfade_timer.interval() / 1000.0
        dur = 0.12
        t = min(1.0, self._startfade_t / dur)
        self._apply_loudness(scale=t)
        if t >= 1.0:
            self._startfade_timer.stop()
            self._startfade_active = False

    def pause(self):
        self._want_playing = False
        if self.mixer is not None and self._fade_active and self._gapless_watch and self._fade_to is not None:
            # пауза перед стыком: обе деки на паузу, стык останется точным
            self.player.set_pause(1)
            if self._fade_to.get_state() == vlc.State.Playing:
                self._fade_to.set_pause(1)
            return
        if self._fade_active and self._gapless_watch and self._fade_to is not None:
            # пауза прямо на стыке: завершаем переход, чтобы не играли две деки
            self._gl_timer.stop()
            self._gl_phase = None
            self._gapless_watch = False
            if self._fade_to.get_state() not in (vlc.State.Playing, vlc.State.Paused):
                self._fade_to.play()
            self._finish_crossfade()
        self.player.pause()
        self._vis_player.pause()

    def pause_hard(self):
        """Пауза, которая ничего не переключает. pause() у VLC — переключатель: на уже стоящем на паузе
        треке он звук ВКЛЮЧАЕТ (кнопка «Пауза» в дуэли и в инструментах прослушивания запускала музыку)."""
        self._want_playing = False
        st = self.player.get_state()
        if st in (vlc.State.Playing, vlc.State.Opening, vlc.State.Buffering) or self.is_playing():
            if st == vlc.State.Playing:
                self.pause()
            else:
                self.player.set_pause(1)
                self._vis_player.set_pause(1)
        if self._fade_active and self._fade_to is not None                 and self._fade_to.get_state() == vlc.State.Playing:
            self._fade_to.set_pause(1)

    # ------------------------------------------------------------------ #
    #  Vinyl Warp (wow & flutter)                                          #
    # ------------------------------------------------------------------ #

    def set_warp_enabled(self, enabled: bool):
        self.warp_enabled = bool(enabled)
        if not self.warp_enabled:
            # Возвращаем чистую скорость — без этого rate может застрять
            # на последнем модулированном значении.
            self.player.set_rate(self.speed)

    def _warp_step(self):
        if not self.warp_enabled or not self.current_path or self._fade_active \
                or self._spindown_active or self._spinup_active or self._startfade_active:
            return
        self._warp_t += self._warp_timer.interval() / 1000.0
        wow = math.sin(self._warp_t * 2 * math.pi * 0.6) * 0.005
        flutter = math.sin(self._warp_t * 2 * math.pi * 6.0) * 0.0015
        rate = self.speed * (1.0 + wow + flutter)
        self.player.set_rate(max(0.5, min(2.0, rate)))

    # ------------------------------------------------------------------ #
    #  Scratch Pause: остановка / раскрутка пластинки                     #
    # ------------------------------------------------------------------ #

    SPIN_DOWN_S = 0.55                       # торможение пластинки (свой вывод)
    SPIN_UP_S = 0.55                         # разгон; равен торможению — буфер не «уплывает»

    def pause_with_effect(self):
        """Пауза с эффектом "остановки пластинки": плавно тормозим темп и
        громкость перед реальной паузой вместо резкого обрыва звука.
        Если vinyl_pause_fx выключен (или трек не играет) — обычная пауза."""
        self._want_playing = False
        if not self.vinyl_pause_fx or not self.is_playing():
            self.pause()
            return
        d = self._deck(self.player)
        if d is not None and not self._fade_active:
            # свой вывод: торможение — по сэмплам из готового буфера, VLC сразу на паузу
            # VLC ставим на паузу, только когда пластинка почти встала: пока она тормозит, VLC
            # подаёт звук дальше. Раньше пауза была сразу — готового буфера хватало на десятую
            # долю секунды, торможение обрывалось, а разгон потом начинался рывком.
            self._spinup_timer.stop()
            self._spinup_active = False
            d.set_vari(0.0, self.SPIN_DOWN_S)
            self._vari_pause_timer.start(int(self.SPIN_DOWN_S * 850))
            return
        self._spinup_timer.stop()
        self._spinup_active = False
        self._spindown_t = 0.0
        self._spindown_start_rate = self.player.get_rate() or self.speed
        self._spindown_active = True
        self._spin_last = time.perf_counter()
        self._spindown_timer.start()

    def _vari_pause_now(self):
        if self._want_playing:return
        self.player.set_pause(1)
        self._vis_player.pause()

    def _spindown_step(self):
        if not self._spindown_active:return
        # по реальному времени: тик таймера на загруженном интерфейсе опаздывает
        now = time.perf_counter()
        self._spindown_t += min(0.05, now - getattr(self, "_spin_last", now))
        self._spin_last = now
        dur = 0.45
        t = min(1.0, self._spindown_t / dur)
        ease = t * t  # ускоряющееся "торможение" — характерно для spin-down пластинки
        rate = max(0.1, self._spindown_start_rate * (1.0 - ease * 0.94))
        self.player.set_rate(rate)
        self._apply_loudness(scale=1.0 - ease)
        if t >= 1.0:
            self._spindown_timer.stop()
            self._spindown_active = False
            self.player.pause()
            self._vis_player.pause()
            # Восстанавливаем нормальную скорость/громкость для следующего play().
            self.player.set_rate(self.speed)
            self._apply_loudness()

    def play_with_effect(self):
        """Возобновление с эффектом "раскрутки пластинки" — плавный разгон
        скорости и громкости вместо резкого рывка звука. Возвращает True,
        если play() у libvlc успешно стартовал."""
        self._want_playing = True
        if not self.vinyl_pause_fx:
            return self.play()
        if not self.current_path:
            return False
        d = self._deck(self.player)
        if d is not None and not self._fade_active:
            self._spindown_timer.stop()
            self._spindown_active = False
            pending = self._vari_pause_timer.isActive()        # нажали «играть» во время торможения
            self._vari_pause_timer.stop()
            if pending or self.player.get_state() == vlc.State.Paused or d.vari_busy():
                # разгон — тоже на выходе, по сэмплам; длительность = торможению, чтобы буфер
                # VLC после пары «пауза/play» оставался того же размера
                d.set_vari(1.0, self.SPIN_UP_S)
            return self.play()
        self._spindown_timer.stop()
        self._spindown_active = False
        ok = self.player.play() == 0
        if ok:
            self._spinup_t = 0.0
            self._apply_loudness(scale=0.0)
            self.player.set_rate(0.3)
            self._spinup_active = True
            self._spin_last = time.perf_counter()
            self._spinup_timer.start()
            self._vis_player.play()
        return ok

    def _spinup_step(self):
        if not self._spinup_active:return
        if not self._want_playing:
            self._spinup_timer.stop();self._spinup_active=False;return
        now = time.perf_counter()
        self._spinup_t += min(0.05, now - getattr(self, "_spin_last", now))
        self._spin_last = now
        dur = 0.35
        t = min(1.0, self._spinup_t / dur)
        ease = 1 - (1 - t) * (1 - t)  # быстрый старт, плавный подход к норме
        rate = 0.3 + (self.speed - 0.3) * ease
        self.player.set_rate(rate)
        self._apply_loudness(scale=ease)
        if t >= 1.0:
            self._spinup_timer.stop()
            self._spinup_active = False
            self.player.set_rate(self.speed)
            self._apply_loudness()

    def stop(self):
        self._want_playing = False
        self._vari_pause_timer.stop()
        self._spindown_timer.stop()
        self._spindown_active = False
        self._spinup_timer.stop()
        self._spinup_active = False
        self._startfade_timer.stop()
        self._startfade_active = False
        if self.mixer is not None and self._fade_active and self._fade_to is not None:
            self._cancel_transition()
        self._stop_player(self.player)
        self._vis_player.stop()
        self._duration = 0.0

    def is_playing(self):
        if self._vari_pause_timer.isActive():               # пластинка тормозит перед паузой — уже «пауза»
            return False
        return self.player.is_playing() == 1

    def is_paused(self):
        return self.player.get_state() == vlc.State.Paused

    def has_finished(self):
        return self.player.get_state() == vlc.State.Ended

    def get_position(self):
        p = self._pos_of(self.player)
        if p is not None:
            return p
        ms = self.player.get_time()
        if ms is None or ms < 0:
            return 0.0
        # свой вывод: звучащее отстаёт от часов VLC на lag деки
        return max(0.0, ms / 1000.0 - self._lag(self.player))

    def get_position_smooth(self):
        """Позиция без «ступенек»: VLC обновляет get_time() порциями по
        ~100–300 мс, из-за этого текст песни отставал. Между обновлениями
        время досчитывается по системным часам; назад не прыгает."""
        p = self._pos_of(self.player)
        if p is not None:
            return p                     # уже точная и плавная — по сэмплам
        ms = self.player.get_time()
        now = time.monotonic()
        if ms is None or ms < 0:
            return 0.0
        raw, t0, base = self._smooth
        playing = self.player.get_state() == vlc.State.Playing and not self._fade_active
        if not playing:
            self._smooth = (ms, now, float(ms))
            return max(0.0, ms / 1000.0 - self._lag(self.player))
        rate = max(0.25, float(self.speed or 1.0))
        if raw is None or ms != raw:
            est_prev = base + (now - t0) * 1000.0 * rate if raw is not None else ms
            # свежее показание чуть отстаёт от досчитанного — не откатываемся назад
            start = est_prev if 0.0 < est_prev - ms < 250.0 else float(ms)
            self._smooth = (ms, now, start)
            return max(0.0, start / 1000.0 - self._lag(self.player))
        est = base + min(1.0, now - t0) * 1000.0 * rate
        return max(0.0, est / 1000.0 - self._lag(self.player))

    def seek(self, seconds):
        if self.mixer is not None and self._fade_active and self._gapless_watch:
            self._cancel_transition()            # стык перепланируется у нового конца трека
        if self.current_path:
            ms = int(max(0.0, float(seconds)) * 1000)
            if self.player.get_state() == vlc.State.Ended:
                self._resume_at = ms / 1000.0    # доигравший трек VLC не перематывает — начнём отсюда в play()
            d = self._deck(self.player)
            if d is not None:
                d.set_anchor(ms, expect_flush=True)
            self.player.set_time(ms)
            self._vis_player.set_time(ms)

    def _media(self, path: str, vis: bool = False):
        """vlc.Media для пути трека. «stream:…» — онлайн-трек: ссылку на аудиопоток даёт url_resolver
        (stream_core.StreamResolver, ссылки готовятся заранее), сам путь для плеера остаётся прежним."""
        path = str(path)
        if path.startswith("stream:") and self.url_resolver is not None:
            url = self.url_resolver(path) or ""
            m = self.instance.media_new(url or path)
            m.add_option(":network-caching=350")          # поток стартует быстро (ссылки yt-dlp — на CDN, без заиканий)
            m.add_option(":http-reconnect")
            return m
        return self.instance.media_new(path)

    def _gain_for(self, path) -> float:
        try:
            return max(0.05, min(4.0, float(self.gain_provider(path)))) if self.gain_provider else 1.0
        except Exception:                            # noqa: BLE001
            return 1.0

    # Громкость VLC (audio_set_volume) — НЕ линейная: и WASAPI/DirectSound
    # на Windows, и программная громкость считают амплитуду как (v/100)³.
    # Верхнего предела у libvlc нет (v = 1000 → ×1000, замерено), поэтому
    # весь запас громкости делаем через неё, а не через preamp эквалайзера.
    VLC_VOL_MAX = 2200                  # ≈ ×10 000 по амплитуде — с запасом
    BOOST_MAX = 100.0                   # буст из настроек: ×100 = 10 000 %

    def _vol_split(self, slider_scaled: float, linear: float = 1.0) -> int:
        """Ползунок (0..1, уже умноженный на масштаб фейда) × линейные
        множители (выравнивание громкости, буст) → значение для VLC.
        Ползунок сохраняет прежний кубический ход (на 50% — как раньше),
        а линейные множители больше не возводятся в куб."""
        amp = max(0.0, float(slider_scaled)) ** 3 * max(0.0, float(linear))
        return int(round(100.0 * amp ** (1.0 / 3.0)))

    def _apply_loudness(self, player=None, scale=1.0, gain=None):
        """Единая точка установки громкости: ползунок × масштаб эффекта
        (фейд/спин) × выравнивание × буст → VLC volume; preamp EQ держит
        единичный уровень + makeup от полос. Все изменения громкости — здесь."""
        g = self.gain if gain is None else gain
        p = player or self.player
        d = self._deck(p)
        hr = self._eq_headroom_db()
        comp = 10.0 ** (hr / 20.0)                       # что убрали из VLC — вернуть после него
        vlc_vol = max(0, min(self.VLC_VOL_MAX, self._vol_split(self.volume * scale,
                                                               g * self._boost * (1.0 if d is not None else comp))))
        p.audio_set_volume(vlc_vol)
        if d is not None:
            # свой вывод: громкость — прямо в деку (колбэк VLC может прийти позже
            # первых сэмплов, и новый трек начинался бы со щелчка на полной громкости)
            d.amp = (vlc_vol / 100.0) ** 3 * comp
        self.vlc_eq.set_preamp(max(-20.0, min(self.EQ_PREAMP_MAX, self.EQ_UNITY_DB + self._eq_makeup_db - hr)))
        self._apply_eq(p)

    @property
    def boost(self) -> float:
        return self._boost

    def set_boost(self, factor: float):
        """Экстремальный множитель громкости (×1..×100 = 100..10 000 %) из настроек."""
        self._boost = max(1.0, min(self.BOOST_MAX, float(factor)))
        self._apply_loudness()

    def set_gain(self, gain: float):
        """Новый коэффициент выравнивания для играющего трека (например, когда закончилось измерение)."""
        self.gain = float(gain)
        if not (self._fade_active or self._startfade_active or self._spindown_active or self._spinup_active):
            self._apply_loudness()

    def set_volume(self, value):
        self.volume = max(0.0, min(1.0, float(value)))
        self._apply_loudness()

    def set_speed(self, speed):
        """Устанавливает скорость воспроизведения.
        VLC set_rate() без scaletempo меняет темп И питч вместе —
        именно так звучат slowed reverb и speed up."""
        self.speed = max(0.5, min(2.0, float(speed)))
        self.player.set_rate(self.speed)
        self._vis_player.set_rate(self.speed)

    # ------------------------------------------------------------------ #
    #  Пресеты: Slowed+Reverb и Speed Up                                  #
    # ------------------------------------------------------------------ #

    # Параметры пресетов. Оба делаются ОФЛАЙН (заранее, в фоне) честным
    # ресэмплингом: темп и питч меняются вместе, как у пластинки на другой
    # скорости — именно так звучат настоящие slowed/nightcore-версии.
    # Раньше использовался VLC set_rate(): при включённом по умолчанию
    # time-stretch VLC сохранял питч и «резал» звук на куски (металлический
    # призвук, «плывущие» ударные).
    _SLOWED_RATE  = 0.80   # темп × 0.80 → питч ≈ −3.9 полутона
    _SPEEDUP_RATE = 1.25   # темп × 1.25 → питч ≈ +3.9 полутона
    PRESET_FACTORS = {"slowed": _SLOWED_RATE, "speedup": _SPEEDUP_RATE}

    # Реверб для slowed: зал с мягким «тёмным» хвостом
    _REVERB_IR_DURATION = 2.6
    _REVERB_WET = 0.34
    _REVERB_PREDELAY = 0.022

    preset_factor = 1.0      # во сколько раз ускорен файл, который сейчас играет

    def _build_reverb_ir(self, sr: int, seed: int = 42) -> np.ndarray:
        """IR «зала»: экспоненциально затухающий шум, у которого верха гаснут
        быстрее низов (как в реальном помещении — хвост «темнеет»), плюс
        ранние отражения и пре-дилей. Разные seed для L/R дают широкую
        стереобазу вместо «моно-гула»."""
        n = int(sr * self._REVERB_IR_DURATION)
        t = np.arange(n, dtype=np.float32) / sr
        rng = np.random.default_rng(seed)
        noise = rng.standard_normal(n).astype(np.float32)
        # две полосы: яркая быстро гаснет, тёмная тянется дольше
        if scipy_signal is not None:
            b, a = scipy_signal.butter(2, 3500.0 / (sr / 2.0), btype="low")
            dark = scipy_signal.lfilter(b, a, noise).astype(np.float32)
        else:
            dark = noise
        bright = noise - dark
        ir = dark * np.exp(-2.6 * t) + bright * np.exp(-6.5 * t) * 0.55
        # мягкое нарастание первых 30 мс — без «щелчка» в начале хвоста
        ramp = np.minimum(1.0, t / 0.03)
        ir *= ramp
        for delay, gain in ((0.011, 0.55), (0.019, 0.42), (0.029, 0.33),
                            (0.043, 0.26), (0.061, 0.19), (0.083, 0.14)):
            k = int(sr * delay * (1.0 + (seed % 7) * 0.013))
            if k < n:
                ir[k] += gain * (1 if (k + seed) % 2 else -1)
        pre = np.zeros(int(sr * self._REVERB_PREDELAY), dtype=np.float32)
        ir = np.concatenate([pre, ir])
        ir /= max(1e-9, float(np.sqrt(np.sum(ir * ir))))      # единичная энергия
        return ir

    def _apply_reverb_to_audio(self, audio: np.ndarray, sr: int, wet_amount=None) -> np.ndarray:
        """Свёртка с IR (отдельный IR на каждый канал). Мокрый сигнал
        выравнивается по громкости к сухому по RMS — реверб не «утаскивает»
        громкость и не перегружает. Без tanh-лимитера (он добавлял грязь)."""
        if scipy_signal is None:
            return audio
        mix_amount=self._REVERB_WET if wet_amount is None else max(0.,min(.95,float(wet_amount)))
        stereo = audio.ndim == 2
        chans = [audio[:, c] for c in range(audio.shape[1])] if stereo else [audio]
        out = []
        for ci, ch in enumerate(chans):
            ir = self._build_reverb_ir(sr, seed=42 + ci * 17)
            wet = scipy_signal.fftconvolve(ch, ir, mode="full")[:len(ch)].astype(np.float32)
            dry_rms = float(np.sqrt(np.mean(ch * ch))) + 1e-9
            wet_rms = float(np.sqrt(np.mean(wet * wet))) + 1e-9
            wet *= dry_rms / wet_rms
            out.append(ch * (1.0 - mix_amount * 0.6) + wet * mix_amount)
        res = np.stack(out, axis=1) if stereo else out[0]
        return res.astype(np.float32)

    def _decode_to_numpy(self, path: str, sr: int = 44100, factor: float = 1.0):
        """Декодирует файл через ffmpeg в float32 (samples, 2). factor ≠ 1 —
        смена скорости РЕСЭМПЛИНГОМ (темп и питч вместе): звук
        «переинтерпретируется» на частоте sr·factor и затем качественно
        (SoX, 28 бит точности) пересчитывается обратно в sr."""
        chains = []
        if abs(factor - 1.0) > 1e-4:
            sr2 = int(round(sr * factor))
            chains.append(f"aresample={sr},asetrate={sr2},aresample={sr}:resampler=soxr:precision=28")
            # запасной вариант для сборок ffmpeg без libsoxr: длинный sinc-фильтр swr
            chains.append(f"aresample={sr},asetrate={sr2},"
                          f"aresample={sr}:filter_size=64:phase_shift=10:cutoff=0.97")
        else:
            chains.append(f"aresample={sr}:resampler=soxr:precision=28")
            chains.append(f"aresample={sr}")
        last_err = ""
        for af in chains:
            cmd = ["ffmpeg", "-y", "-i", path, "-vn", "-af", af,
                   "-f", "f32le", "-acodec", "pcm_f32le", "-ac", "2",
                   "-loglevel", "error", "-"]
            proc = subprocess.run(
                cmd, capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            if proc.returncode == 0 and proc.stdout:
                audio = np.frombuffer(proc.stdout, dtype=np.float32).reshape(-1, 2)
                return audio.copy(), sr, 2
            last_err = proc.stderr.decode(errors="replace")[:200]
        raise RuntimeError(f"ffmpeg decode failed: {last_err}")

    def _numpy_to_wav(self, audio: np.ndarray, sr: int, channels: int, path: str):
        """WAV 24 бит. Громкость НЕ подтягивается вверх (раньше тихие треки
        разгонялись до пика) — только защита от перегруза, если пик > −0.3 dBFS."""
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if peak > 0.966:
            audio = audio * (0.966 / peak)
        pcm = np.clip(np.round(audio * 8388607.0), -8388608, 8388607).astype(np.int32)
        b = pcm.astype("<i4").view(np.uint8).reshape(-1, 4)[:, :3]   # младшие 3 байта
        with wave.open(path, "w") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(3)
            wf.setframerate(sr)
            wf.writeframes(np.ascontiguousarray(b).tobytes())

    def render_preset(self, src_path: str, mode: str) -> str:
        """Готовит файл пресета (вызывать в ФОНОВОМ потоке): ресэмплинг под
        скорость пресета (+ реверб для slowed) → временный WAV 24 бит."""
        factor = self.PRESET_FACTORS[mode]
        SR = 44100
        audio, sr, ch = self._decode_to_numpy(src_path, SR, factor)
        if mode == "slowed":
            audio = self._apply_reverb_to_audio(audio, sr)
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", prefix=f"neon_{mode}_", delete=False)
        tmp.close()
        self._numpy_to_wav(audio, sr, ch, tmp.name)
        return tmp.name

    def _cleanup_preset_temp(self, keep: str | None = None):
        """Удаляет временный файл пресета (кроме keep)."""
        if self._preset_temp_path and self._preset_temp_path != keep:
            try:
                os.unlink(self._preset_temp_path)
            except OSError:
                pass
            self._preset_temp_path = None

    # совместимость со старыми вызовами
    def activate_slowed_reverb(self, src_path: str, progress_cb=None) -> str:
        self.preset_mode = "slowed"
        return self.render_preset(src_path, "slowed")

    def deactivate_preset(self):
        self.preset_mode = None
        self.preset_factor = 1.0
        return 1.0

    # ------------------------------------------------------------------ #
    #  Кроссфейд                                                           #
    # ------------------------------------------------------------------ #

    @property
    def is_crossfading(self) -> bool:
        return self._fade_active

    def start_crossfade(self, next_path: str, duration_ms: int = None, start_sec: float = 0.0) -> bool:
        """Запускает next_path на второй деке поверх ещё играющего текущего
        трека и плавно (equal-power) сводит громкости. current_path и все
        сигналы переключатся только в момент фактической смены звука —
        через crossfade_started, а не сразу. start_sec — с какой секунды пустить следующий
        (умный кроссфейд dj_mix: с первой доли после тихого вступления)."""
        next_path = str(next_path)
        if not next_path or not self.current_path or self._fade_active:
            return False

        duration_ms = int(duration_ms if duration_ms is not None else self.crossfade_ms)
        duration_ms = max(100, duration_ms)

        media = self._take_preloaded(next_path)
        if media is None:
            media = self._media(next_path)

        self._gain_next = self._gain_for(next_path)
        start_sec = max(0.0, float(start_sec or 0.0))
        if start_sec > 0.05:
            media.add_option(f":start-time={start_sec:.3f}")
        d = self._deck(self._player_alt)
        if d is not None:
            d.reset()
            d.set_anchor(start_sec * 1000.0)
        self._player_alt.set_media(media)
        self._player_alt.set_rate(self.speed)
        self._apply_eq(self._player_alt)
        # audio_set_mute применяется на уровне аудиодрайвера атомарно —
        # в отличие от audio_set_volume(0), который обрабатывается после
        # первого рендер-блока и может пропустить импульс инициализации.
        self._silence(self._player_alt)
        self._set_mute(self._player_alt, True)
        self._player_alt.play()

        self._fade_from        = self.player
        self._fade_to          = self._player_alt
        self._fade_elapsed_ms  = 0
        self._fade_duration_ms = duration_ms
        self._fade_next_path   = next_path
        self._fade_active      = True
        if getattr(self.mixer, 'native', False):
            self._set_mute(self._player_alt, False)
            self._apply_loudness(player=self._player_alt, gain=self._gain_next)
            self.mixer.crossfade(self._deck(self.player), self._deck(self._player_alt), duration_ms / 1000.)
            self._fade_timer.start()
            return True
        # Пауза перед стартом фейда: VLC успевает открыть аудиоустройство
        # тихо, без характерного щелчка инициализации.
        incoming = self._fade_to
        token = self._gl_token
        QTimer.singleShot(80, lambda: self._fade_timer.start() if self._fade_active and not self._gapless_watch and self._fade_to is incoming and self._gl_token == token else None)
        return True

    def start_gapless(self, next_path: str, remain_ms: float) -> bool:
        """Гэплесс-переход (вызывается за ~1.5 с до конца трека).

        Громкость на стыке НЕ трогается вовсе. Причина (по журналу реальных
        переходов): позиция трека в VLC идёт с небольшим опережением
        звучащего, а громкость применяется «сейчас» — любое приглушение по
        позиции срезало хвост старого трека, а заглушённый заранее новый
        терял начало.

        Как теперь:
          1. медиа следующего трека открыто заранее (без буфера чтения);
          2. новая дека стартует СРАЗУ НА ПОЛНОЙ ГРОМКОСТИ ровно за
             «задержку старта» до конца старой (время старой — плавное,
             с экстраполяцией между «ступеньками» VLC). У обеих дек одна и та
             же задержка аудиоустройства, поэтому стык получается впритык;
          3. старая дека доигрывает до конца сама и останавливается только
             после того, как полностью отзвучит;
          4. задержка старта замеряется на каждом переходе и подстраивается.
        """
        next_path = str(next_path)
        if not next_path or not self.current_path or self._fade_active:
            return False

        media = self._take_preloaded(next_path)
        if media is None:
            media = self._media(next_path)
            if not next_path.startswith("stream:"):
                media.parse_with_options(vlc.MediaParseFlag.local, 0)
        media.add_option(":file-caching=0")

        self._gain_next = self._gain_for(next_path)
        alt = self._player_alt
        new_deck = self._deck(alt)
        if new_deck is not None:
            new_deck.reset()
            new_deck.set_anchor(0.0)
        alt.set_media(media)
        alt.set_rate(self.speed)
        self._apply_eq(alt)
        self._set_mute(alt, False)
        self._apply_loudness(player=alt, gain=self._gain_next)

        if new_deck is not None:
            # Свой вывод: новая дека стартует СРАЗУ и копит звук, а микшер
            # включит её ровно с того сэмпла, на котором кончится текущая.
            # Ни угадывания задержек, ни зависимости от таймеров/FPS.
            self.mixer.chain(self._deck(self.player), new_deck)
            alt.play()
            self._fade_from      = self.player
            self._fade_to        = alt
            self._fade_next_path = next_path
            self._fade_active    = True
            self._gapless_watch  = True
            self._gl_phase       = "chained"
            self._gl_token += 1
            self._gl_timer.start()
            self._gl_log(f"chained next (own output), remain={round(remain_ms)}ms")
            QTimer.singleShot(max(4000, int(remain_ms) + 15000), lambda token=self._gl_token: self._finish_gapless_switch(token))
            return True

        self._fade_from      = self.player
        self._fade_to        = alt
        self._fade_next_path = next_path
        self._fade_active    = True
        self._gapless_watch  = True
        self._gl_phase       = "wait"
        self._gl_clock       = (None, 0.0)
        self._gl_new_t0      = 0.0
        self._gl_measured    = False
        self._gl_old_end_t   = None
        self._gl_token += 1
        threading.Thread(target=self._gl_wait_thread, args=(self._gl_token,), daemon=True).start()
        QTimer.singleShot(max(400, int(remain_ms) + 3000), lambda token=self._gl_token: self._finish_gapless_switch(token))
        return True

    def _gl_log(self, msg: str):
        """Короткий журнал стыков (~/Echoes/gapless.log) — чтобы по
        реальным переходам подогнать тайминги под конкретный компьютер."""
        try:
            from pathlib import Path
            p = data_home.root() / "gapless.log"
            if p.exists() and p.stat().st_size > 200_000:
                p.write_text("", encoding="utf-8")
            with open(p, "a", encoding="utf-8") as f:
                f.write(time.strftime("%H:%M:%S ") + msg + "\n")
        except Exception:
            pass

    def _gl_old_remaining_ms(self, now: float):
        """Остаток старого трека. VLC обновляет get_time() порциями, поэтому
        между обновлениями время экстраполируется по системным часам."""
        old = self._fade_from
        if old is None:
            return None
        length, t = old.get_length(), old.get_time()
        if not length or length <= 0 or t is None or t < 0:
            return None
        last_t, last_mono = self._gl_clock
        if last_t is None or t != last_t:
            self._gl_clock = (t, now)
            est = float(t)
        else:
            est = t + (now - last_mono) * 1000.0 * max(0.25, float(self.speed or 1.0))
        return float(length) - est

    def _gl_wait_thread(self, token):
        """Фоновый поток: ждёт точный момент и запускает следующую деку,
        затем замеряет её задержку старта. Не зависит от загрузки GUI."""
        ended = (vlc.State.Ended, vlc.State.Stopped, vlc.State.Error)
        try:
            while self._gl_token == token and self._fade_active and self._gl_phase == "wait":
                old, new = self._fade_from, self._fade_to
                now = time.monotonic()
                old_done = old.get_state() in ended
                remain = self._gl_old_remaining_ms(now)
                if old_done or (remain is not None and remain <= self._gl_start_est_ms):
                    new.play()                   # сразу на полной громкости
                    self._gl_new_t0 = now
                    self._gl_phase = "joined"
                    self._gl_log(f"start next: remain={None if remain is None else round(remain)} "
                                 f"est={round(self._gl_start_est_ms)} old_done={old_done}")
                    break
                if remain is None or remain > 400:
                    time.sleep(0.02)
                else:
                    time.sleep(0.001)
            # замер задержки старта новой деки
            t_end = time.monotonic() + 1.5
            while self._gl_token == token and self._gl_phase == "joined" and not self._gl_measured \
                    and time.monotonic() < t_end:
                nt = self._fade_to.get_time()
                if nt is not None and nt > 0:
                    lat = (time.monotonic() - self._gl_new_t0) * 1000.0 - float(nt)
                    if 3.0 <= lat <= 250.0:      # выбросы (подвис компьютер) не учитываем
                        self._gl_start_est_ms = max(10.0, min(150.0, self._gl_start_est_ms * 0.7 + lat * 0.3))
                    self._gl_measured = True
                    self._gl_log(f"next sounding: lat={round(lat)}ms -> est={round(self._gl_start_est_ms)}")
                    break
                time.sleep(0.001)
        except Exception as e:                   # noqa: BLE001
            self._gl_log(f"thread error: {e}")
        if self._gl_token == token:
            try:
                self._gl_wake.emit()             # дальше — спокойный GUI-таймер
            except RuntimeError:
                pass

    def _gl_step(self):
        if not self._fade_active or not self._gapless_watch:
            self._gl_timer.stop()
            return
        if self.mixer is not None and self._gl_phase == "chained":
            # звук уже склеен микшером; здесь только меняем деки ролями
            old = self._deck(self._fade_from)
            if old is not None and old.finished():
                self._gl_log("seam done (sample-accurate)")
                self._gl_timer.stop()
                self._gl_phase = None
                self._gapless_watch = False
                self._finish_crossfade()
            return
        old, new = self._fade_from, self._fade_to
        now = time.monotonic()
        old_done = old.get_state() in (vlc.State.Ended, vlc.State.Stopped, vlc.State.Error)
        remain = self._gl_old_remaining_ms(now)

        if self._gl_phase == "wait":
            return                               # момент старта ловит _gl_wait_thread

        # Замер задержки старта: VLC сообщает время порциями, поэтому при
        # первом ненулевом показании «прошло − показание» = задержка старта.
        if not self._gl_measured:
            nt = new.get_time()
            if nt is not None and nt > 0:
                lat = (now - self._gl_new_t0) * 1000.0 - float(nt)
                if 3.0 <= lat <= 250.0:
                    self._gl_start_est_ms = max(10.0, min(150.0, self._gl_start_est_ms * 0.7 + lat * 0.3))
                self._gl_measured = True
                self._gl_log(f"next sounding: lat={round(lat)}ms -> est={round(self._gl_start_est_ms)}")

        if self._gl_phase == "joined":
            # ждём, пока старая дека полностью отзвучит, и только потом её гасим
            if self._gl_old_end_t is None and (old_done or (remain is not None and remain < -600.0)):
                self._gl_old_end_t = now
            if self._gl_old_end_t is not None and (now - self._gl_old_end_t) >= 0.25 \
                    and (self._gl_measured or (now - self._gl_new_t0) > 1.5):
                self._gl_log("done")
                self._gl_timer.stop()
                self._gl_phase = None
                self._gapless_watch = False
                self._finish_crossfade()

    def _finish_gapless_switch(self, token=None):
        """Watchdog belongs to exactly one transition, including pause retries."""
        if token is not None and token != self._gl_token:
            return
        if not self._fade_active or not self._gapless_watch:
            return
        if self.mixer is not None and self._gl_phase == "chained":
            if self.player.get_state() == vlc.State.Paused:
                QTimer.singleShot(3000, lambda token=self._gl_token: self._finish_gapless_switch(token))   # на паузе — ждём дальше
                return
            old=self._deck(self._fade_from)
            if old is not None and not old.finished() and self.mixer.alive():
                # Wall time is not audio time: stalls/rate changes must not cut a tail.
                QTimer.singleShot(1000, lambda token=self._gl_token: self._finish_gapless_switch(token))
                return
        self._gl_log(f"FALLBACK timer fired, phase={self._gl_phase}")
        self._gl_timer.stop()
        self._gl_phase = None
        self._gapless_watch = False
        new = self._fade_to
        if new.get_state() not in (vlc.State.Playing, vlc.State.Paused):
            new.play()
        self._set_mute(new, False)
        self._apply_loudness(player=new, gain=self._gain_next)
        QTimer.singleShot(40, lambda token=self._gl_token: self._finish_crossfade() if token == self._gl_token and self._fade_active else None)

    def _cancel_transition(self):
        """Отменить начатый стык/кроссфейд (перемотка, стоп): вторая дека молчит."""
        self._gl_timer.stop()
        self._fade_timer.stop()
        self._gl_token += 1
        self._gl_phase = None
        alt = self._fade_to
        self._fade_active = False
        self._gapless_watch = False
        self._fade_from = None
        self._fade_to = None
        if alt is not None:
            self._stop_player(alt)
            self._silence(alt)
        self._apply_loudness()

    def _fade_step(self):
        if not self._fade_active or self._fade_to is None:return
        if getattr(self.mixer, 'native', False):
            if self.mixer.crossfade_done():
                self._finish_crossfade()
            return
        self._fade_elapsed_ms += self._fade_timer.interval()
        t = min(1.0, self._fade_elapsed_ms / self._fade_duration_ms)
        # Первый тик — снимаем mute с входящей деки непосредственно
        # перед тем, как начнём повышать её громкость. К этому моменту
        # VLC уже инициализировал аудиоустройство за время задержки 80 мс,
        # поэтому первый рендер-блок уйдёт тихо.
        if self._fade_elapsed_ms <= self._fade_timer.interval():
            self._set_mute(self._fade_to, False)
        # Equal-power кривая — суммарная громкость на слух не проседает в середине фейда.
        vol_out = math.cos(t * math.pi / 2.0)
        vol_in  = math.sin(t * math.pi / 2.0)
        self._apply_loudness(player=self._fade_from, gain=self.gain, scale=vol_out)
        self._apply_loudness(player=self._fade_to, gain=self._gain_next, scale=vol_in)
        if t >= 1.0:
            self._finish_crossfade()

    def _finish_crossfade(self):
        self._fade_timer.stop()
        old_player = self._fade_from

        # Меняем деки ролями: та, что "въехала", становится основной.
        self.gain = self._gain_next
        self.player, self._player_alt = self._fade_to, old_player
        self._set_mute(self.player, False)
        if self._want_playing and self.player.get_state() == vlc.State.Paused:
            self.player.set_pause(0)
        self._apply_loudness()

        # Сбрасываем PCM-кольцо ДО перезапуска vis_player — чтобы
        # старые семплы уходящего трека не попали в следующий кадр FFT.
        with self._audio_lock:
            self._ring.fill(0.0)
            self._ring_pos = 0
            self._ring_filled = 0

        # Останавливаем старую деку только после смены ролей,
        # чтобы исключить момент тишины между stop() и play() новой деки.
        self._stop_player(old_player)
        self._silence(old_player)

        self.current_path = self._fade_next_path
        self._duration = 0.0
        self._last_state = None

        # Перецепляем теневой visualizer-плеер на новый трек и синхронизируем позицию.
        vis_media = self._media(self.current_path, vis=True)
        vis_media.add_option(self._sout_option)
        self._vis_player.stop()
        self._vis_player.set_media(vis_media)
        self._vis_player.set_rate(self.speed)
        self._vis_player.play()
        pos_ms = self.player.get_time()
        if pos_ms and pos_ms > 0:
            self._vis_player.set_time(pos_ms)

        self._fade_active = False
        self._gapless_watch = False
        self._fade_from = None
        self._fade_to = None
        self.crossfade_started.emit(self.current_path)

    # ------------------------------------------------------------------ #
    #  Poll                                                                #
    # ------------------------------------------------------------------ #

    def _poll_state(self):
        if self._fade_active:
            # Во время кроссфейда self.player ещё указывает на уходящую деку —
            # её естественный Ended не должен триггерить обычный finished/advance.
            if self._gapless_watch and self._fade_from is not None:
                # Стык ведёт точный _gl_step; здесь только не даём «замёрзнуть»
                # полосе прогресса в последние полторы секунды трека.
                p = self._pos_of(self._fade_from)
                t = self._fade_from.get_time()
                if p is not None:
                    self.position_changed.emit(p)
                elif t is not None and t >= 0:
                    self.position_changed.emit(max(0.0, t / 1000.0 - self._lag(self._fade_from)))
            return
        state = self.player.get_state()

        if state != self._last_state:
            self._last_state = state
            playing = (state == vlc.State.Playing)
            if playing != self._was_playing:
                self._was_playing = playing
                self.state_changed.emit(playing)
            if state == vlc.State.Ended:
                self.finished.emit()
            elif state == vlc.State.Error:
                self.error_changed.emit("Ошибка воспроизведения VLC")

        if state in (vlc.State.Playing, vlc.State.Paused):
            dur_ms = self.player.get_length()
            if dur_ms > 0:
                dur_sec = dur_ms / 1000.0
                if abs(dur_sec - self._duration) > 0.1:
                    self._duration = dur_sec
                    self.duration_changed.emit(self._duration)
            self.position_changed.emit(self.get_position())
