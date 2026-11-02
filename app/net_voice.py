# net_voice.py
"""
Голос Echoes.NET: комнаты «Слушать вместе», звонки в личке и созвоны в группах.

Микрофон 48 кГц моно, кадры по 20 мс → обработка → кодек → сеть (net_core: voice_send / call_voice).

Обработка (в своём потоке, не в звуковом колбэке — без треска):
  • срез гула ниже ~85 Гц (биквад Баттерворта);
  • шумодав — спектральный фильтр Винера с «решением по предыдущему кадру» (decision-directed) и
    оценкой шума по минимумам: вентилятор, клавиатура, гул, шипение уходят, голос остаётся;
  • автоусиление (AGC) — тихий микрофон подтягивается, громкий не перегружает; мягкий лимитер;
  • шумовой порог (VAD) с удержанием 300 мс — в тишине ничего не отправляется.

Кодеки: «opus» (48 кГц, ~48 кбит/с, как у Discord; нужен PyAV) и запасной «adpcm» (24 кГц, IMA ADPCM —
есть везде, где есть Python 3.12). Отправитель выбирает общий для всех слушателей (net_core).

Воспроизведение: у каждого говорящего свой буфер джиттера по номерам кадров (старт после ~60 мс,
потолок ~200 мс), потерянный кадр — затухающий повтор предыдущего; голоса складываются с мягким
ограничением. Есть громкость входа/выхода, выбор устройств, «слышать себя» для проверки микрофона.
"""
from __future__ import annotations

import base64
import collections
import threading
import time
import warnings

import numpy as np

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    import audioop                                             # IMA ADPCM: есть в Python 3.12

RATE = 48000
FRAME = 960                                                    # 20 мс
START_FRAMES = 3                                               # буфер джиттера: начать после 60 мс
MAX_FRAMES = 10                                                # больше 200 мс отставания — догнать
HANGOVER = 15                                                  # шумовой порог держит 300 мс после речи
NS_FLOOR = {0: 1.0, 1: 0.12, 2: 0.045}                         # шумодав: выкл / обычный / сильный


def _have_av() -> bool:
    try:
        import av                                              # noqa: F401
        av.codec.Codec("libopus", "w")
        return True
    except Exception:                                          # noqa: BLE001
        return False


HAVE_OPUS = _have_av()


def codecs() -> list:
    """Что умеет этот ECHOES — сообщается собеседникам (лучший первым)."""
    return (["opus"] if HAVE_OPUS else []) + ["adpcm"]


def pick_codec(caps_list) -> str:
    """Общий кодек для всех слушателей: opus, если он есть у каждого, иначе adpcm."""
    if HAVE_OPUS and caps_list and all("opus" in (c or ()) for c in caps_list):
        return "opus"
    return "adpcm"


# ══════════════════════════ обработка микрофона ══════════════════════════ #

class _HighPass:
    """Биквад-фильтр верхних частот (RBJ, Баттерворт), ~85 Гц — гул, удары по столу, ветер."""

    def __init__(self, fc=85.0):
        import math
        w0 = 2 * math.pi * fc / RATE
        alpha = math.sin(w0) / (2 * 0.7071)
        c = math.cos(w0)
        a0 = 1 + alpha
        self.b = np.array([(1 + c) / 2, -(1 + c), (1 + c) / 2]) / a0
        self.a = np.array([1.0, -2 * c / a0, (1 - alpha) / a0])
        try:
            from scipy.signal import lfilter, lfilter_zi
            self._lf = lfilter
            self.zi = lfilter_zi(self.b, self.a) * 0.0
        except Exception:                                      # noqa: BLE001
            self._lf = None
            self.s = [0.0, 0.0, 0.0, 0.0]

    def __call__(self, x):
        if self._lf is not None:
            y, self.zi = self._lf(self.b, self.a, x, zi=self.zi)
            return y
        b0, b1, b2 = self.b
        _, a1, a2 = self.a
        x1, x2, y1, y2 = self.s
        out = np.empty_like(x)
        for i, v in enumerate(x):
            y = b0 * v + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            x2, x1, y2, y1 = x1, v, y1, y
            out[i] = y
        self.s = [x1, x2, y1, y2]
        return out


class Denoiser:
    """Шумодав: окно 40 мс с перекрытием 50% (sqrt-Ханн), фильтр Винера по каждой полосе."""

    def __init__(self, strength=1):
        n = FRAME * 2
        self.win = np.sqrt(np.hanning(n + 1)[:n]).astype(np.float64)
        self.prev = np.zeros(FRAME)
        self.ola = np.zeros(FRAME)
        self.noise = None
        self.g_prev = None
        self.post_prev = None
        self.frames = 0
        self.strength = strength
        self.snr = 1.0                                         # средний сигнал/шум в полосе голоса (для VAD)
        f = np.fft.rfftfreq(FRAME * 2, 1.0 / RATE)
        self.band = (f > 250) & (f < 3800)

    def __call__(self, x):
        buf = np.concatenate([self.prev, x])
        self.prev = x
        X = np.fft.rfft(buf * self.win)
        P = X.real * X.real + X.imag * X.imag + 1e-12
        self.frames += 1
        if self.noise is None:
            self.noise = P.copy()
        elif self.frames < 20:                                 # первые 0.4 с — учим фон
            self.noise = 0.75 * self.noise + 0.25 * np.minimum(P, self.noise * 4)
        else:                                                  # минимумы: быстро вниз, медленно вверх
            self.noise = np.where(P < self.noise, 0.7 * self.noise + 0.3 * P, self.noise * 1.008)
        avg = getattr(self, "avg", None)                       # средний шум (для VAD; минимумы занижают)
        if avg is None or self.frames < 20:
            self.avg = avg = P.copy() if avg is None else 0.8 * avg + 0.2 * P
        self.snr = float(np.mean(P[self.band] / avg[self.band]))
        if self.snr < 2.0:
            self.avg = 0.92 * avg + 0.08 * P
        if self.strength <= 0:
            G = np.ones_like(P)
        else:
            post = P / self.noise
            ml = np.maximum(post - 1.0, 0.0)
            xi = ml if self.g_prev is None else 0.96 * (self.g_prev ** 2) * self.post_prev + 0.04 * ml
            G = xi / (1.0 + xi)
            G = np.maximum(G, NS_FLOOR.get(self.strength, 0.12))
            G = np.convolve(G, (0.25, 0.5, 0.25), mode="same")  # сгладить по частоте — без «бульканья»
            self.g_prev, self.post_prev = G, post
        y = np.fft.irfft(X * G, n=FRAME * 2) * self.win
        out = self.ola + y[:FRAME]
        self.ola = y[FRAME:].copy()
        return out


class _Agc:
    def __init__(self):
        self.gain = 1.0
        self.env = 0.0

    def __call__(self, x, voiced):
        rms = float(np.sqrt(np.mean(x * x)) + 1e-9)
        if voiced:
            self.env = 0.9 * self.env + 0.1 * rms if self.env else rms
            want = min(6.0, max(0.5, 0.085 / max(self.env, 1e-4)))
            k = 0.25 if want < self.gain else 0.04             # громче — плавно, тише — быстро
            self.gain += (want - self.gain) * k
        y = x * self.gain
        peak = np.abs(y).max()
        if peak > 0.92:                                        # мягкий лимитер
            y = np.tanh(y * 1.1) / np.tanh(1.1)
        return y


# ══════════════════════════ кодеки ══════════════════════════ #

_HALF = np.array([-0.0106, 0.0, 0.0493, 0.0, -0.1386, 0.0, 0.6000, 1.0, 0.6000, 0.0, -0.1386, 0.0, 0.0493, 0.0,
                  -0.0106]) / 2.0                              # полуполосный ФНЧ для 48↔24 кГц


class _Enc:
    def __init__(self, codec):
        self.codec = codec
        self.pts = 0
        if codec == "opus":
            import av
            e = av.CodecContext.create("libopus", "w")
            e.sample_rate = RATE
            e.layout = "mono"
            e.format = "s16"
            e.bit_rate = 48000
            e.options = {"application": "voip", "frame_duration": "20", "packet_loss": "10", "fec": "1",
                         "vbr": "on"}
            self.e = e
        else:
            self.tail = np.zeros(len(_HALF) - 1)
            self.state = None

    def encode(self, pcm16: np.ndarray) -> list:
        if self.codec == "opus":
            import av
            fr = av.AudioFrame.from_ndarray(pcm16.reshape(1, -1), format="s16", layout="mono")
            fr.sample_rate = RATE
            fr.pts = self.pts
            self.pts += FRAME
            return [bytes(p) for p in self.e.encode(fr)]
        x = np.concatenate([self.tail, pcm16.astype(np.float64)])
        self.tail = x[-(len(_HALF) - 1):]
        y = np.convolve(x, _HALF * 2.0, mode="valid")[::2]    # 48 → 24 кГц
        s16 = np.clip(y, -32768, 32767).astype(np.int16).tobytes()
        st = self.state or (0, 0)
        data, self.state = audioop.lin2adpcm(s16, 2, self.state)
        head = int(st[0]).to_bytes(2, "little", signed=True) + bytes([int(st[1]) & 0xFF])
        return [head + data]                                   # с состоянием — потеря кадра не ломает следующий


class _Dec:
    def __init__(self, codec):
        self.codec = codec
        if codec == "opus":
            import av
            d = av.CodecContext.create("libopus", "r")
            d.sample_rate = RATE
            d.layout = "mono"
            self.d = d
        else:
            self.tail = np.zeros(len(_HALF) - 1)

    def decode(self, data: bytes):
        if self.codec == "opus":
            import av
            out = []
            for f in self.d.decode(av.Packet(data)):
                a = f.to_ndarray()
                a = a.astype(np.float32) / (32768.0 if a.dtype == np.int16 else 1.0)
                out.append(a.reshape(-1))
            return np.concatenate(out) if out else None
        if len(data) < 4:
            return None
        st = (int.from_bytes(data[:2], "little", signed=True), data[2])
        s16, _ = audioop.adpcm2lin(data[3:], 2, st)
        y = np.frombuffer(s16, dtype=np.int16).astype(np.float64)
        up = np.zeros(len(y) * 2)
        up[::2] = y                                            # 24 → 48 кГц
        x = np.concatenate([self.tail, up])
        self.tail = x[-(len(_HALF) - 1):]
        return (np.convolve(x, _HALF * 2.0, mode="valid") / 32768.0).astype(np.float32)


# ══════════════════════════ движок ══════════════════════════ #

class _Src:
    """Один говорящий: декодеры, буфер джиттера по номерам кадров, уровень для «говорит»."""

    def __init__(self):
        self.decs = {}
        self.frames: dict[int, np.ndarray] = {}
        self.next = None
        self.started = False
        self.last = None
        self.miss = 0
        self.level = 0.0
        self.heard = 0.0
        self.vol = 1.0


class VoiceEngine:
    def __init__(self, settings=None):
        self.settings = settings if isinstance(settings, dict) else {}
        self._mic = None
        self._out = None
        self._inq = collections.deque(maxlen=50)
        self._work = None
        self._run = False
        self.lock = threading.Lock()
        self.srcs: dict[str, _Src] = {}
        self.sink = None                                       # (seq, {codec: [bytes]}) → в сеть
        self.want = {"adpcm"}                                  # какие кодеки нужны слушателям
        self.encs: dict[str, _Enc] = {}
        self.seq = 0
        self.level = 0.0                                       # уровень своего микрофона (после обработки)
        self.talking = False
        self.muted = False
        self.deaf = False
        self.loopback = False                                  # «слышать себя» (проверка микрофона)
        self._hold = 0
        self._hp = _HighPass()
        self._ns = Denoiser(self.ns_level)
        self._agc = _Agc()
        self.last_heard: dict[str, float] = {}
        self.radio_fx_active=False
        self._radio_rack=None;self._radio_config=None
        self._clip=None;self._clip_pos=0
        self._tail_frames=0
        self._wake_audio=threading.Event()

    # ── настройки ── #

    @property
    def ns_level(self) -> int:
        return int(self.settings.get("voice_ns", 1))

    def set_ns(self, level: int):
        self.settings["voice_ns"] = int(level)
        self._ns.strength = int(level)

    def _dev(self, key):
        v = self.settings.get(key)
        return v if v not in ("", None, -1) else None

    # ── микрофон ── #

    def mic_on(self):
        import sounddevice as sd
        if self._mic is not None:
            return
        self._tail_frames=0

        def cb(indata, frames, t, status):
            self._inq.append(indata[:, 0].copy())
            self._wake_audio.set()
        self._mic = sd.InputStream(samplerate=RATE, channels=1, dtype="float32", blocksize=FRAME,
                                   device=self._dev("voice_in"), latency="low", callback=cb)
        self._mic.start()
        self._start_worker()

    def _start_worker(self):
        self._run = True
        self._wake_audio.set()
        if self._work is None or not self._work.is_alive():
            self._work = threading.Thread(target=self._worker, name="echoes-voice", daemon=True)
            self._work.start()

    def mic_off(self):
        was_open=self._mic is not None
        if self._mic is not None:
            try:
                self._mic.stop()
                self._mic.close()
            except Exception:                                  # noqa: BLE001
                pass
        self._mic = None
        self._inq.clear()
        self.level = 0.0
        self.talking = False
        if was_open and self.radio_fx_active:
            self._tail_frames=10;self._wake_audio.set()

    @property
    def mic_active(self) -> bool:
        return self._mic is not None

    def _worker(self):
        deadline=time.monotonic()
        while self._run:
            if not self._inq and self._clip is None and not self._tail_frames:
                self._wake_audio.wait(.1);self._wake_audio.clear()
                continue
            if (not self._inq or self._mic is None) and time.monotonic()<deadline:
                time.sleep(min(.004,deadline-time.monotonic()));continue
            x = self._inq.popleft().astype(np.float64) if self._inq else np.zeros(FRAME)
            x *= float(self.settings.get("voice_in_vol", 1.0))
            deadline=time.monotonic()+FRAME/RATE
            clip=None
            tail=self._tail_frames>0 and self._mic is None
            if tail:self._tail_frames-=1
            with self.lock:
                if self._clip is not None:
                    clip=self._clip[self._clip_pos:self._clip_pos+FRAME].copy();self._clip_pos+=FRAME
                    if self._clip_pos>=len(self._clip):self._clip=None;self._clip_pos=0
            try:
                self._process(x,clip,tail)
            except Exception as e:                             # noqa: BLE001
                print("[voice]", e)

    def play_clip(self,samples):
        samples=np.asarray(samples,dtype=np.float32).reshape(-1)
        if not 0<len(samples)<=RATE*30:raise ValueError('Invalid radio clip length')
        with self.lock:
            self._clip=np.concatenate((samples,np.zeros(FRAME*3,np.float32)));self._clip_pos=0
        self._start_worker()

    def stop_clip(self):
        with self.lock:
            self._clip=None;self._clip_pos=0;self.srcs.pop('__radio_clip__',None)
            getattr(self,'_pend',{}).pop('__radio_clip__',None)

    def _process(self, x, clip=None, force=False):
        y = self._hp(x)
        y = self._ns(y)
        rms = float(np.sqrt(np.mean(y * y)) + 1e-9)
        if self._ns.frames < 25:                               # первые полсекунды — учим фон, не отправляем
            self._nf = rms if self._ns.frames == 1 else 0.8 * getattr(self, "_nf", rms) + 0.2 * rms
            voiced = False
        else:
            nf = self._nf                                      # уровень фона после шумодава
            self._nf = nf = min(rms, nf * 1.03) if rms > nf else 0.85 * nf + 0.15 * rms
            sens = float(self.settings.get("voice_gate", 0.0) or 0.0)
            if sens > 0:                                       # своя чувствительность (как в Discord)
                voiced = rms > sens
            else:                                              # авто: речь заметно выше шума в полосе голоса
                voiced = self._ns.snr > 3.0 and rms > max(0.003, nf * 1.6)
        self._hold = HANGOVER if voiced else max(0, self._hold - 1)
        y = self._agc(y, voiced) if self.settings.get("voice_agc", True) else y
        if self.radio_fx_active:
            from radio_audio import options,VoiceRack
            config=options(self.settings.get('radio_voice_fx'))
            if config!=self._radio_config:
                if not config['enabled']:self._radio_rack=None
                elif self._radio_rack is not None:self._radio_rack.update(config)
                else:self._radio_rack=VoiceRack(config)
                self._radio_config=config
            if self._radio_rack is not None:y=self._radio_rack.process(y)
        if clip is not None:
            y=np.clip(y+np.pad(clip,(0,max(0,len(y)-len(clip))))[:len(y)]*float(self.settings.get('radio_sound_gain',.7)),-.98,.98)
            if not self.loopback:
                try:self._play_local('__radio_clip__',np.pad(clip,(0,max(0,len(y)-len(clip))))[:len(y)].astype(np.float32)*float(self.settings.get('radio_sound_gain',.7)))
                except Exception:pass
        self.level = min(1.0, float(np.sqrt(np.mean(y * y))) * 6)
        self.talking = (self._hold > 0 or clip is not None or force) and not self.muted
        if self.loopback:
            self._play_local("__me__", y.astype(np.float32))
        if not self.talking:
            return
        pcm = np.clip(y * 32767.0, -32768, 32767).astype(np.int16)
        self.seq += 1
        out = {}
        for c in sorted(self.want or {"adpcm"}):
            enc = self.encs.get(c)
            if enc is None:
                try:
                    enc = self.encs[c] = _Enc(c)
                except Exception as e:                         # noqa: BLE001
                    print("[voice] кодек", c, e)
                    continue
            out[c] = enc.encode(pcm)
        sink = self.sink
        if sink and out:
            try:
                sink(self.seq, out)
            except Exception as e:                             # noqa: BLE001
                print("[voice] sink:", e)

    # ── воспроизведение ── #

    def _ensure_out(self):
        import sounddevice as sd
        if self._out is not None:
            return

        def cb(outdata, frames, t, status):
            outdata[:, 0] = self._mix(frames)
        self._out = sd.OutputStream(samplerate=RATE, channels=1, dtype="float32", blocksize=480,
                                    device=self._dev("voice_out"), latency="low", callback=cb)
        self._out.start()
        self._pending = collections.deque()

    def _mix(self, n):
        mix = np.zeros(n, dtype=np.float32)
        pend = getattr(self, "_pend", None)
        if pend is None:
            pend = self._pend = {}
        with self.lock:
            for key, s in list(self.srcs.items()):
                buf = pend.get(key)
                while buf is None or len(buf) < n:
                    fr = self._next_frame(s)
                    if fr is None:
                        break
                    buf = fr if buf is None else np.concatenate([buf, fr])
                if buf is None:
                    continue
                k = min(n, len(buf))
                if not self.deaf:
                    mix[:k] += buf[:k] * s.vol
                pend[key] = buf[k:] if len(buf) > k else None
        vol = float(self.settings.get("voice_out_vol", 1.0))
        mix *= vol
        peak = float(np.abs(mix).max()) if n else 0.0
        if peak > 0.95:                                        # несколько голосов сразу — мягко ограничить
            mix = np.tanh(mix)
        return mix

    def _next_frame(self, s: _Src):
        if not s.started:
            if len(s.frames) < START_FRAMES:
                return None
            s.started = True
            s.next = min(s.frames)
        if len(s.frames) > MAX_FRAMES:                         # отстали — догнать (выкинуть старое)
            for k in sorted(s.frames)[:len(s.frames) - START_FRAMES]:
                s.frames.pop(k, None)
            s.next = min(s.frames)
        fr = s.frames.pop(s.next, None) if s.next is not None else None
        if fr is None:
            later = [k for k in s.frames if k > (s.next or 0)]
            if not later:
                s.miss += 1
                if s.miss > 8:                                 # пауза в речи — накопить заново
                    s.started = False
                    s.last = None
                    return None
                if s.last is not None and s.miss <= 3:         # потеря: затухающий повтор
                    s.last = s.last * 0.55
                    s.next = (s.next or 0) + 1
                    return s.last
                s.next = (s.next or 0) + 1
                return np.zeros(FRAME, dtype=np.float32)
            s.next = min(later)
            fr = s.frames.pop(s.next)
        s.miss = 0
        s.next += 1
        s.last = fr
        return fr

    def _play_local(self, key, pcm):
        self._ensure_out()
        with self.lock:
            s = self.srcs.setdefault(key, _Src())
            seq = (max(s.frames) + 1) if s.frames else (s.next or 0)
            s.frames[seq] = pcm

    def play(self, src: str, codec: str, seq: int, frames):
        """Кадры от говорящего: frames — список bytes (или base64-строк), seq — номер первого."""
        self._ensure_out()
        with self.lock:
            s = self.srcs.setdefault(src, _Src())
            s.vol = float((self.settings.get("voice_vols") or {}).get(src, 1.0))
        for i, fr in enumerate(frames or []):
            try:
                data = base64.b64decode(fr) if isinstance(fr, str) else bytes(fr)
                dec = s.decs.get(codec)
                if dec is None:
                    dec = s.decs[codec] = _Dec(codec)
                pcm = dec.decode(data)
            except Exception:                                  # noqa: BLE001
                continue
            if pcm is None or not len(pcm):
                continue
            pcm = pcm[:FRAME] if len(pcm) >= FRAME else np.pad(pcm, (0, FRAME - len(pcm)))
            with self.lock:
                k = int(seq) + i
                if s.started and s.next is not None and k < s.next - 50:
                    s.started, s.next = False, None            # отправитель начал заново
                    s.frames.clear()
                if s.next is None or k >= s.next:
                    s.frames[k] = pcm.astype(np.float32)
                s.level = 0.8 * s.level + 0.2 * min(1.0, float(np.sqrt(np.mean(pcm * pcm))) * 6)
        s.heard = time.time()
        self.last_heard[src] = s.heard

    def speaking(self, within=0.45) -> list[str]:
        now = time.time()
        return [k for k, t in self.last_heard.items() if now - t < within]

    def drop(self, src):
        with self.lock:
            self.srcs.pop(src, None)
            getattr(self, "_pend", {}).pop(src, None)
        self.last_heard.pop(src, None)

    def close(self):
        self._run=False;self._wake_audio.set()
        self.stop_clip()
        self.mic_off()
        self._tail_frames=0
        self._radio_rack=None;self._radio_config=None
        if self._out is not None:
            try:
                self._out.stop()
                self._out.close()
            except Exception:                                  # noqa: BLE001
                pass
        self._out = None
        with self.lock:
            self.srcs.clear()
        self.last_heard.clear()

    # ── устройства ── #

    @staticmethod
    def devices():
        """[(индекс, имя)] для входа и выхода (только WASAPI/MME без дублей по имени)."""
        from voice_devices import devices
        return devices()


Voice = VoiceEngine                                            # старое имя (комнаты)
