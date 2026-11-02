# lyrics_sync.py
"""
ИИ-синхронизация текста с треком (локально, аудио никуда не отправляется).

Текст уже известен — поэтому Whisper его не распознаёт, а ВЫРАВНИВАЕТ (force_align): по карте внимания
модели (CTranslate2 align) находит, где в звуке спето каждое слово текста. Раньше Whisper распознавал пение
(рэп — с ошибками), а потом услышанное сопоставлялось с текстом: долго (2–11 мин на трек, отделение голоса
на процессоре + распознавание) и криво (медиана ошибки начала строки 0.33 с, худшие 10 % — до минуты,
часть треков падала по лимиту времени). Сейчас (замер на 6 треках с ручной разметкой LRCLIB): медиана 0.23 с,
61 % строк точнее 0.3 с, худшие 10 % — до 2.3 с, без сбоев; само выравнивание — 15–30 с на процессоре.

1. Звук: чистый вокал (daw_sep, Kim Vocal 2), если он уже в кэше или есть видеокарта для разделения
   (onnxruntime с DirectML); иначе — сам трек: на миксе чуть менее точно (медиана 0.25 с), зато без
   минут разделения на процессоре.
2. Окна по 30 с: в окно подаётся весь оставшийся текст (с запасом), берутся слова, которые уверенно
   легли внутрь окна (не ближе TAIL к краю), следующее окно — с последнего взятого слова.
3. Начало слова у Whisper в среднем на ~0.15 с раньше голоса — сдвиг WORD_BIAS.
4. Результат — LRC с метками каждого слова (<мм:сс.сс>): «жидкая» заливка текста идёт по настоящим словам.

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
WORD_BIAS = 0.12                # Whisper ставит начало слова раньше голоса
TAIL = 2.5                      # слова ближе к краю окна — в следующее окно
MAX_TOKENS = 200                # текста в одно окно (запас: лишнее ляжет к краю и не будет взято)
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


def _pip(args, log=None) -> bool:
    """pip в тот же Python, на котором работает плеер."""
    exe = Path(sys.executable)
    py = exe.with_name("python.exe") if exe.with_name("python.exe").exists() else exe
    flags = 0x08000000 if os.name == "nt" else 0       # CREATE_NO_WINDOW
    try:
        p = subprocess.Popen([str(py), "-m", "pip", *args, "--disable-pip-version-check"],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             encoding="utf-8", errors="replace", creationflags=flags)
        for line in p.stdout:                        # type: ignore[union-attr]
            line = line.strip()
            if log and line.startswith(("Collecting", "Downloading", "Installing", "Successfully")):
                log(line[:90])
        p.wait()
        return p.returncode == 0
    except Exception as e:                           # noqa: BLE001
        print("[sync] pip:", e)
        return False


def install_gpu_runtime(log=None) -> bool:
    """Windows: onnxruntime с DirectML вместо обычного — отделение голоса на любой видеокарте с DirectX 12
    (секунды вместо минут). Оба пакета ставят один и тот же модуль onnxruntime, поэтому обычный сначала
    убираем; не вышло поставить DirectML — возвращаем обычный (VAD faster-whisper без него не работает)."""
    if os.name != "nt":
        return False
    try:
        import daw_sep
        if daw_sep.gpu_available():
            return True
    except Exception:                                # noqa: BLE001
        pass
    _pip(["uninstall", "-y", "onnxruntime"], log)
    ok = _pip(["install", "--prefer-binary", "--no-warn-script-location", "onnxruntime-directml"], log)
    if not ok:
        _pip(["install", "--prefer-binary", "--no-warn-script-location", "onnxruntime"], log)
    import importlib
    importlib.invalidate_caches()
    return ok


def install_whisper(log=None) -> bool:
    """pip install faster-whisper (и на Windows — onnxruntime с DirectML, см. install_gpu_runtime)."""
    if not _pip(["install", "--prefer-binary", "--no-warn-script-location", "faster-whisper"], log):
        return False
    import importlib
    importlib.invalidate_caches()
    install_gpu_runtime(log)
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


def _stamp(t: float) -> str:
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m):02d}:{s:05.2f}"


def build_lrc(lines: list[str], times: list[float], words: list | None = None) -> str:
    """words[i] — начала слов строки i (по split()) или None: тогда строка без пословных меток."""
    rows = []
    for i, (line, t) in enumerate(zip(lines, times)):
        ws = words[i] if words is not None and i < len(words) else None
        parts = line.split()
        if ws and len(ws) == len(parts) and len(parts) > 1:
            body = parts[0] + "".join(f" <{_stamp(max(t, w))}>{p}" for w, p in zip(ws[1:], parts[1:]))
            rows.append(f"[{_stamp(t)}]{body}")
        else:
            rows.append(f"[{_stamp(t)}] {line}")
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


def release():
    """Модели (~0.5 ГБ) — из памяти (режим геймера, после синхронизации)."""
    _MODEL.clear()


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


def _vocals_cheap(path: str) -> bool:
    """Чистый вокал без минут на процессоре: уже в кэше или разделение на видеокарте."""
    try:
        import daw_sep
        return daw_sep.have_runtime() and (daw_sep.cached(path) or (daw_sep.model_ready() and daw_sep.gpu_available()))
    except Exception:                                # noqa: BLE001
        return False


def force_align(mdl, audio, lines: list[str], language: str, cancel=None, progress=None):
    """Слова текста → [(строка, слово, (начало, конец) с | None)]. Окна по 30 с, текст подаётся с запасом,
    берутся слова, уверенно легшие внутрь окна; следующее окно — с последнего взятого слова."""
    import numpy as np
    from faster_whisper.audio import pad_or_trim
    from faster_whisper.tokenizer import Tokenizer
    progress = progress or (lambda s: None)
    tok = Tokenizer(mdl.hf_tokenizer, mdl.model.is_multilingual, task="transcribe", language=language or "en")
    words = []
    for li, line in enumerate(lines):
        for w in line.split():
            t = tok.encode(" " + w)
            words.append((li, w, t))
    feats = mdl.feature_extractor(audio)
    nf = feats.shape[-1]
    fps = 100.0
    env = envelope(audio)
    pos = 0
    if len(env):
        thr = max(float(np.percentile(env, 35)) * 2.0, float(env.max()) * 0.06)
        loud = np.nonzero(env > thr)[0]
        if len(loud):
            pos = max(0, int((loud[0] * HOP - 0.3) * fps))      # с начала голоса, не со вступления
    res: list = [None] * len(words)
    i, guard = 0, 0
    while i < len(words) and pos < nf - 30 and guard < 400:
        guard += 1
        if cancel is not None and cancel.is_set():
            raise SyncCancelled()
        if not words[i][2]:
            i += 1
            continue
        seg = feats[:, pos:pos + 3000]
        nfr = seg.shape[-1]
        enc = mdl.encode(pad_or_trim(seg))
        toks, j = [], i
        while j < len(words) and len(toks) + len(words[j][2]) <= MAX_TOKENS:
            toks += words[j][2]
            j += 1
        al = mdl.find_alignment(tok, [toks], enc, nfr)[0] if toks else []
        starts, cum = {}, 0
        for a in al:
            starts.setdefault(cum, (float(a["start"]), float(a["end"])))
            cum += len(a["tokens"])
        last_window = pos + nfr >= nf
        wend = nfr / fps
        took, acc = 0, 0
        for k in range(i, j):
            if not words[k][2]:
                took += 1
                continue
            hit = starts.get(acc)
            acc += len(words[k][2])
            if hit is None or (not last_window and hit[1] > wend - TAIL):
                break
            res[k] = (pos / fps + hit[0], pos / fps + hit[1])
            took += 1
        progress(f"Выравниваю текст по голосу… {min(99, int((pos + nfr) / nf * 100))}%")
        if took == 0:
            pos += int((wend - TAIL) * fps) if not last_window else nf
            continue
        i += took
        last = next((res[k] for k in range(i - 1, -1, -1) if res[k] is not None), None)
        pos = max(pos + 50, int(last[1] * fps) - 20) if last else pos + 1500
    return [(li, w, r) for (li, w, _t), r in zip(words, res)]


def aligned_times(lines: list[str], aligned):
    """Начало каждой строки и начала её слов (с учётом WORD_BIAS); строки без слов — между соседями."""
    first: dict = {}
    per_line: dict = {}
    for li, _w, r in aligned:
        per_line.setdefault(li, []).append(None if r is None else r[0] + WORD_BIAS)
        if r is not None and li not in first:
            first[li] = r[0] + WORD_BIAS
    known = sorted(first)
    times = []
    for li in range(len(lines)):
        if li in first:
            times.append(first[li])
            continue
        prev = [first[k] for k in known if k < li]
        nxt = [first[k] for k in known if k > li]
        times.append(prev[-1] + 2.0 if prev else (max(0.0, nxt[0] - 2.0) if nxt else 0.0))
    out, prev = [], -1.0
    for t in times:                                   # строго по возрастанию
        t = max(float(t), prev + 0.2)
        out.append(t)
        prev = t
    words = []
    for li in range(len(lines)):
        ws = per_line.get(li)
        if not ws or any(w is None for w in ws):
            words.append(None)
            continue
        fixed, p = [], out[li]
        for w in ws:
            p = max(p, w)
            fixed.append(p)
        words.append(fixed)
    return out, words


def sync_lyrics(audio_path: str, text: str, progress=None, cancel: threading.Event | None = None,
                model: str = MODEL_DEFAULT, use_vocals: bool = True) -> str:
    """Возвращает LRC-текст (с метками слов). progress(str) — статус для интерфейса."""
    progress = progress or (lambda s: None)
    lines = plain_lines(text)
    if not lines:
        raise ValueError("Сначала впишите или найдите текст песни")
    if not Path(audio_path).exists():
        raise FileNotFoundError("Файл трека не найден")
    voc = vocals_16k(audio_path, progress, cancel) if use_vocals and _vocals_cheap(audio_path) else None
    if cancel is not None and cancel.is_set():
        raise SyncCancelled()
    progress("Читаю трек…")
    audio = voc if voc is not None else _decode(audio_path)
    import numpy as np
    if not isinstance(audio, np.ndarray) or not len(audio):
        raise RuntimeError("Не удалось прочитать звук трека")
    mdl = _load_model(model, progress)
    aligned = force_align(mdl, audio, lines, _guess_language(text) or "en", cancel, progress)
    if not any(r is not None for _li, _w, r in aligned):
        raise RuntimeError("Не удалось найти в треке голос с этим текстом (инструментал или другой текст?)")
    times, words = aligned_times(lines, aligned)
    return build_lrc(lines, times, words)
