# lyrics_sync.py
"""
ИИ-синхронизация текста с треком (локально, аудио никуда не отправляется).

1. Вокал отделяется от бита нейросетью (daw_sep, Kim Vocal 2 — та же, что у мэшап-бота; результат кэшируется).
   Распознавание по чистому голосу намного точнее, чем по миксу с битом.
2. Whisper (faster-whisper, small, int8) слушает только участки, где есть голос (VAD) — проигрыши и тишина
   пропускаются, поэтому трек считается за десятки секунд, а не минуты.
3. Слова текста выравниваются с услышанными (Нидлман — Вунш, нечёткое сравнение + транслит: Whisper иногда
   пишет русский рэп латиницей). Начало строки — по нескольким совпавшим словам с учётом темпа речи.
4. Начало строки «прилипает» к настоящему началу голоса (вступление по огибающей вокала), строки без
   совпадений раскладываются по местам, где голос реально звучит, а не равномерно по времени.

Модель скачивается один раз при первом запуске (small ≈ 460 МБ); сеть вокала (~67 МБ) — тоже.
"""
from __future__ import annotations

import difflib
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

_TS = re.compile(r"\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]")
_SECTION = re.compile(r"^\s*[\[(][^\])]{1,40}[\])]\s*$")       # [Припев], (Chorus)
_WORD = re.compile(r"[^\W_]+", re.UNICODE)

MODEL_DEFAULT = "small"
MATCH_MIN = 0.7                 # похожесть слов, с которой считаем их «тем же словом»
SR = 16000
HOP = 0.02                      # шаг огибающей вокала, с


class SyncCancelled(Exception):
    pass


# ── установка / загрузка ── #

def whisper_installed() -> bool:
    try:
        import faster_whisper                        # noqa: F401
        return True
    except Exception:                                # noqa: BLE001
        return False


def install_whisper(log=None) -> bool:
    """pip install faster-whisper в тот же Python, на котором работает плеер."""
    exe = Path(sys.executable)
    py = exe.with_name("python.exe") if exe.with_name("python.exe").exists() else exe
    flags = 0x08000000 if os.name == "nt" else 0       # CREATE_NO_WINDOW
    try:
        p = subprocess.Popen([str(py), "-m", "pip", "install", "--prefer-binary", "--no-warn-script-location",
                              "--disable-pip-version-check", "faster-whisper"],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             encoding="utf-8", errors="replace", creationflags=flags)
        for line in p.stdout:                        # type: ignore[union-attr]
            line = line.strip()
            if log and line.startswith(("Collecting", "Downloading", "Installing", "Successfully")):
                log(line[:90])
        p.wait()
        if p.returncode != 0:
            return False
    except Exception as e:                           # noqa: BLE001
        print("[sync] pip:", e)
        return False
    import importlib
    importlib.invalidate_caches()
    return whisper_installed()


# ── подготовка текста ── #

def plain_lines(text: str) -> list[str]:
    """Строки текста без таймкодов, заголовков секций и пустых строк."""
    out = []
    for raw in (text or "").splitlines():
        s = _TS.sub("", raw).strip()
        if not s or _SECTION.match(s):
            continue
        if re.match(r"^\[(ar|ti|al|by|offset|length|re|ve):", s, re.I):
            continue
        out.append(s)
    return out


_TR = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
       "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
       "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "", "ы": "y", "ь": "",
       "э": "e", "ю": "yu", "я": "ya"}


def _translit(w: str) -> str:
    """Кириллица → латиница и упрощение латиницы (ph→f, ck→k…): «братик» ≈ «bratik»."""
    s = "".join(_TR.get(c, c) for c in w)
    for a, b in (("ph", "f"), ("ck", "k"), ("qu", "kv"), ("w", "v"), ("x", "ks"), ("j", "i"), ("y", "i"),
                 ("kh", "h"), ("ts", "c")):
        s = s.replace(a, b)
    return re.sub(r"(.)\1+", r"\1", s)


def _words(s: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(s)]


def _ratio(a: str, b: str) -> float:
    if difflib.SequenceMatcher(None, a, b).quick_ratio() < MATCH_MIN:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _sim(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    r = _ratio(a, b)
    if r < MATCH_MIN:
        ta, tb = _translit(a), _translit(b)
        r = 1.0 if ta == tb else max(r, _ratio(ta, tb) * 0.95)
    return r if r >= MATCH_MIN else 0.0


def _guess_language(text: str):
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return None
    cyr = sum(1 for c in letters if "Ѐ" <= c <= "ӿ")
    if cyr / len(letters) > 0.5:
        return "ru"
    jp = sum(1 for c in letters if "぀" <= c <= "ヿ" or "一" <= c <= "鿿")
    if jp / len(letters) > 0.3:
        return "ja"
    return "en" if all(ord(c) < 0x250 for c in letters) else None


# ── выравнивание ── #

def _nw(L, T):
    """Нидлман — Вунш: слова текста L [(слово, строка, позиция)] против услышанных T [(слово, начало, конец)].
    → пары (индекс в L, индекс в T) совпавших слов."""
    n, m = len(L), len(T)
    if not n or not m:
        return []
    GAP = -0.5
    move = [bytearray(m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        move[i][0] = 2
    for j in range(1, m + 1):
        move[0][j] = 3
    previous=[j*GAP for j in range(m+1)]
    simc: dict = {}
    for i in range(1, n + 1):
        lw = L[i - 1][0]
        row, prow, mrow = [i*GAP]+[0.0]*m, previous, move[i]
        for j in range(1, m + 1):
            key = (lw, T[j - 1][0])
            sm = simc.get(key)
            if sm is None:
                if len(simc)>=32768:simc.clear()
                sm = simc[key] = _sim(*key)
            diag = prow[j - 1] + (3.0 * sm - 1.0 if sm else -1.0)
            up, left = prow[j] + GAP, row[j - 1] + GAP
            if diag >= up and diag >= left:
                row[j], mrow[j] = diag, 1
            elif up >= left:
                row[j], mrow[j] = up, 2
            else:
                row[j], mrow[j] = left, 3
        previous=row
    pairs = []
    i, j = n, m
    while i > 0 or j > 0:
        mv = move[i][j]
        if mv == 1:
            if _sim(L[i - 1][0], T[j - 1][0]):
                pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif mv == 2:
            i -= 1
        else:
            j -= 1
    pairs.reverse()
    return pairs


def _onsets(env, thr):
    """Вступления голоса по огибающей: время (с), где громкость поднялась выше порога после паузы ≥ 0.12 с."""
    out, quiet = [], 0
    for i, v in enumerate(env):
        if v < thr:
            quiet += 1
        else:
            if quiet * HOP >= 0.12:
                out.append(i * HOP)
            quiet = 0
    return out


def align_lines(lines: list[str], heard: list[tuple[str, float, float]], env=None, require_confidence=False) -> list[float]:
    """lines — строки текста; heard — услышанные слова (слово, начало, конец); env — огибающая вокала
    (кадры по HOP с) для привязки к вступлениям. → время начала каждой строки (с), по возрастанию."""
    import bisect
    L = []
    for li, line in enumerate(lines):
        for k, w in enumerate(_words(line)):
            L.append((w, li, k))
    T = [(w, s, e) for w, s, e in heard if w]
    if len(L)*len(T)>4_000_000:
        raise ValueError('Слишком длинный текст для быстрого выравнивания. Разделите запись на треки.')
    pairs = _nw(L, T)
    if require_confidence and (len(pairs)<min(3,len(L)) or len(pairs)/max(1,len(L))<.15):
        raise ValueError('Слишком мало совпадений голоса с текстом. Проверьте текст: надёжные тайминги не получены.')
    durs = sorted(max(0.08, min(0.8, e - s)) for _w, s, e in T) or [0.3]
    wd = max(0.15, min(0.45, durs[len(durs) // 2]))        # типичная длина слова в этом треке
    per_line: dict = {}
    for i, j in pairs:
        _w, li, k = L[i]
        per_line.setdefault(li, []).append((k, T[j][1]))
    nl = len(lines)
    times: list = [None] * nl
    for li, ms in per_line.items():
        # оценка начала по каждому совпавшему слову; берём медиану первых трёх (устойчиво к одной ошибке)
        est = sorted(t - k * wd for k, t in ms[:3])
        times[li] = max(0.0, est[len(est) // 2] if len(ms) >= 3 else min(est))
    # одиночные совпадения, нарушающие порядок, — выбросы
    known = [i for i in range(nl) if times[i] is not None]
    for a in known:
        prev = [times[b] for b in known if b < a][-1:] or [None]
        nxt = [times[b] for b in known if b > a][:1] or [None]
        if prev[0] is not None and nxt[0] is not None and len(per_line[a]) == 1 and \
                not (prev[0] <= times[a] <= nxt[0]):
            times[a] = None
    ons = []
    if env is not None and len(env):
        import numpy as np
        e = np.asarray(env, dtype=float)
        thr = max(float(np.percentile(e, 35)) * 2.0, float(e.max()) * 0.06)
        ons = _onsets(e, thr)
    known = [i for i in range(nl) if times[i] is not None]
    wc = [max(1, len(_words(x))) for x in lines]
    if not known:
        end = T[-1][2] if T else 3.0 * nl
        start = T[0][1] if T else 0.0
        tot, acc = sum(wc), 0
        for i in range(nl):
            times[i] = start + (end - start) * acc / tot
            acc += wc[i]
    else:
        first, last = known[0], known[-1]
        for i in range(first - 1, -1, -1):
            times[i] = max(0.0, times[i + 1] - max(1.2, wc[i] * wd * 1.4))
        for i in range(last + 1, nl):
            times[i] = times[i - 1] + max(1.2, wc[i - 1] * wd * 1.4)
        for a, b in zip(known, known[1:]):
            if b - a <= 1:
                continue
            gap = [o for o in ons if times[a] + 0.4 < o < times[b] - 0.4]
            need = b - a - 1
            if len(gap) >= need:                           # голос реально вступает — туда и ставим строки
                tot = sum(wc[a:b])
                acc = wc[a]
                for i in range(a + 1, b):
                    want = times[a] + (times[b] - times[a]) * acc / tot
                    times[i] = min(gap, key=lambda o: abs(o - want))
                    acc += wc[i]
            else:
                tot = sum(wc[a:b])
                acc = wc[a]
                for i in range(a + 1, b):
                    times[i] = times[a] + (times[b] - times[a]) * acc / tot
                    acc += wc[i]
    # привязка к вступлению голоса: ближайшее в окне [-0.6; +0.3] с
    if ons:
        for i in range(nl):
            t = times[i]
            k = bisect.bisect_left(ons, t - 0.6)
            best = None
            while k < len(ons) and ons[k] <= t + 0.3:
                if best is None or abs(ons[k] - t) < abs(best - t):
                    best = ons[k]
                k += 1
            if best is not None:
                times[i] = best
    out, prev = [], -1.0
    for t in times:                                   # строго по возрастанию
        t = max(float(t or 0.0), prev + 0.2)
        out.append(t)
        prev = t
    return out


def build_lrc(lines: list[str], times: list[float]) -> str:
    rows = []
    for line, t in zip(lines, times):
        m, s = divmod(t, 60)
        rows.append(f"[{int(m):02d}:{s:05.2f}] {line}")
    return "\n".join(rows)


# ── аудио ── #

_MODEL = {}


def _ffmpeg() -> str | None:
    p = shutil.which("ffmpeg")
    if p:
        return p
    local = Path(__file__).resolve().parent.parent / "ffmpeg" / "ffmpeg.exe"
    return str(local) if local.exists() else None


def _decode(path: str, start: float = 0.0, length: float = 0.0):
    """Трек -> моно 16 кГц float32 через ffmpeg (минуя PyAV: у него бывают конфликты версий)."""
    import numpy as np
    exe = _ffmpeg()
    if not exe:
        return path                                  # пусть faster-whisper декодирует сам
    flags = 0x08000000 if os.name == "nt" else 0
    cmd = [exe, "-nostdin", "-loglevel", "error"]
    if start > 0:
        cmd += ["-ss", f"{start:.2f}"]
    cmd += ["-i", path]
    if length > 0:
        cmd += ["-t", f"{length:.2f}"]
    cmd += ["-vn", "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"]
    r = subprocess.run(cmd, capture_output=True, creationflags=flags, timeout=60)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError("не удалось прочитать аудио: " + r.stderr.decode("utf-8", "replace")[:150])
    return np.frombuffer(r.stdout, dtype=np.float32)


def vocals_16k(path: str, progress=None, cancel=None):
    """Отделённый вокал (моно, 16 кГц) или None, если нейросети нет. Кэш daw_sep — второй раз мгновенно."""
    try:
        import daw_sep
        if not (daw_sep.have_runtime() and (daw_sep.model_ready() or daw_sep.cached(path))):
            return None
        res = daw_sep.separate(path, progress=(lambda f, s: progress and progress(f"Отделяю голос от музыки… {int(f * 100)}%")),
                               cancel=cancel, allow_download=False, quality="fast")
        if res.get("method") == "fallback":
            return None
        import numpy as np
        from scipy.signal import resample_poly
        v = res["vocals"].mean(axis=1).astype(np.float32)
        return resample_poly(v, 160, 441).astype(np.float32)
    except Exception as e:                           # noqa: BLE001
        if "отменено" in str(e):
            raise SyncCancelled()
        print("[sync] vocals:", e)
        return None


def envelope(audio):
    import numpy as np
    a = np.asarray(audio, dtype=np.float32)
    hop = int(SR * HOP)
    n = len(a) // hop
    if n <= 0:
        return np.zeros(0)
    fr = a[:n * hop].reshape(n, hop)
    rms = np.sqrt((fr * fr).mean(axis=1) + 1e-12)
    k = 3
    return np.convolve(rms, np.ones(k) / k, mode="same")


def _load_model(size: str, progress, local_only=False):
    if size in _MODEL:
        return _MODEL[size]
    from faster_whisper import WhisperModel
    progress("Загружаю модель распознавания (первый раз — скачивание, до ~460 МБ)…")
    cpu = os.cpu_count() or 4
    mdl = WhisperModel(size, device="cpu", compute_type="int8", cpu_threads=max(1, min(4, cpu - 1)), local_files_only=local_only)
    _MODEL[size] = mdl
    return mdl


def transcribe_words(mdl, audio, language=None, prompt="", cancel=None, progress=None, dur=0.0, vad=True, max_seconds=240):
    progress = progress or (lambda s: None)
    deadline=time.monotonic()+max_seconds
    segs, info = mdl.transcribe(audio, language=language, word_timestamps=True,
                                vad_filter=vad, vad_parameters={"min_silence_duration_ms": 400, "speech_pad_ms": 200},
                                beam_size=1, condition_on_previous_text=False, initial_prompt=prompt or None,
                                no_speech_threshold=0.75)
    dur = dur or float(getattr(info, "duration", 0) or 0)
    heard = []
    for sg in segs:
        if time.monotonic()>deadline:
            raise TimeoutError('Распознавание превысило лимит времени. Попробуйте более короткую запись.')
        if cancel is not None and cancel.is_set():
            raise SyncCancelled()
        for w in (sg.words or []):
            for sub in _words(w.word):
                heard.append((sub, float(w.start), float(w.end)))
        if dur:
            progress(f"Слушаю трек… {min(99, int(sg.end / dur * 100))}%")
    return heard


def sync_lyrics(audio_path: str, text: str, progress=None, cancel: threading.Event | None = None,
                model: str = MODEL_DEFAULT, use_vocals: bool = True) -> str:
    """Возвращает LRC-текст. progress(str) — статус для интерфейса."""
    progress = progress or (lambda s: None)
    lines = plain_lines(text)
    if not lines:
        raise ValueError("Сначала впишите или найдите текст песни")
    if not Path(audio_path).exists():
        raise FileNotFoundError("Файл трека не найден")
    voc = vocals_16k(audio_path, progress, cancel) if use_vocals else None
    if cancel is not None and cancel.is_set():
        raise SyncCancelled()
    audio = voc if voc is not None else _decode(audio_path)
    mdl = _load_model(model, progress)
    progress("Слушаю трек… 0%")
    import numpy as np
    dur = len(audio) / SR if isinstance(audio, np.ndarray) else 0.0
    heard = transcribe_words(mdl, audio, _guess_language(text), " ".join(lines[:2])[:200], cancel, progress, dur)
    if not [h for h in heard if h[0]]:
        raise RuntimeError("В треке не удалось расслышать слова (инструментал или очень тихий вокал)")
    progress("Сопоставляю с текстом…")
    env = envelope(voc) if voc is not None else None
    times = align_lines(lines, heard, env, require_confidence=True)
    return build_lrc(lines, times)
