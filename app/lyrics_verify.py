# lyrics_verify.py
"""
Проверка найденных текстов: «тот ли это текст для этого трека» — чтобы реже попадалась чужая песня.

Источники текста сравнивают только название/исполнителя/длительность (lyrics.match_score). Этого мало, когда
у файла нет исполнителя («Faint» → нашёлся Lonewolf – Faint) или у песни есть тёзки. Здесь каждый найденный
вариант получает поправки к оценке:

  • согласие источников — тексты кластеризуются по похожести слов; один и тот же текст от LRCLIB, Musixmatch
    и NetEase почти наверняка правильный, одиночка — подозрителен;
  • правдоподобие таймингов — последняя строка не позже конца трека и не на середине;
  • язык — название кириллицей, а текст целиком латиницей (или наоборот иероглифы) — штраф;
  • название в тексте — у большинства песен слова названия есть в припеве;
  • мусор — «Lyrics not available», ссылки, «You might also like», слишком короткий текст;
  • (если варианты спорные и есть Whisper) — СЛУХ: нейросеть слушает 30 секунд середины трека и считает,
    какой текст совпадает с услышанными словами. Это самый сильный признак; занимает несколько секунд и
    запускается только при сомнениях.

choose(hits, cands, duration, path) → лучший Hit или None (тогда lyrics._pick решает по-старому).
"""
from __future__ import annotations

import re
import threading
from pathlib import Path

_WORD = re.compile(r"[^\W\d_]{2,}", re.UNICODE)
_TS = re.compile(r"\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]|<\d{1,3}:\d{2}\.\d{2,3}>")
_JUNK = re.compile(r"lyrics (?:not|aren.t) available|we (?:do not|don.t) have the lyrics|you might also like|"
                   r"\bembed\b|https?://|www\.|paroles de la chanson|instrumental only|текст песни не найден|"
                   r"see .{0,40} live|get tickets", re.I)
_TITLE_STOP = {"feat", "prod", "remix", "edit", "version", "remastered", "live", "official", "audio", "video",
               "the", "and", "with", "from", "ft"}

_HEARD: dict = {}                     # путь → услышанные слова (кэш на сессию)
_HEARD_LOCK = threading.Lock()


def _words(text):
    return [w.lower() for w in _WORD.findall(_TS.sub(" ", text or ""))]


def _script(text):
    letters = [c for c in (text or "") if c.isalpha()]
    if not letters:
        return "", 0.0
    cyr = sum(1 for c in letters if "Ѐ" <= c <= "ӿ") / len(letters)
    cjk = sum(1 for c in letters if "぀" <= c <= "ヿ" or "一" <= c <= "鿿"
              or "가" <= c <= "힯") / len(letters)
    if cyr > 0.5:
        return "cyr", cyr
    if cjk > 0.3:
        return "cjk", cjk
    return "lat", 1.0 - cyr - cjk


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _last_ts(text):
    last = 0.0
    for m in re.finditer(r"\[(\d{1,3}):(\d{2}(?:[.:]\d{1,3})?)\]", text or ""):
        last = max(last, int(m.group(1)) * 60 + float(m.group(2).replace(":", ".")))
    return last


def features(h, cands, duration):
    """Поправки к оценке одного варианта без прослушивания: {имя: поправка}."""
    f = {}
    text = h.text or ""
    words = _words(text)
    lines = [l for l in _TS.sub("", text).splitlines() if l.strip()]
    if _JUNK.search(text):
        f["мусор"] = -0.35
    if not h.instrumental and len(lines) < 4:
        f["короткий"] = -0.15
    if h.synced and duration and duration > 30:
        last = _last_ts(text)
        if last > duration + 8:
            f["длиннее трека"] = -0.3
        elif last and last < duration * 0.4:
            f["кончается рано"] = -0.12
    q_title = " ".join(t for _a, t in cands[:2])
    q_s, _ = _script(q_title)
    t_s, share = _script(text)
    if q_s == "cyr" and t_s == "lat" and share > 0.9:
        f["язык"] = -0.2
    elif q_s in ("cyr", "lat") and t_s == "cjk":
        f["язык"] = -0.1
    tw = {w for _a, t in cands[:2] for w in _words(t) if len(w) >= 3 and w not in _TITLE_STOP}
    if tw and words:
        ws = set(words)
        frac = sum(1 for w in tw if w in ws) / len(tw)
        f["название в тексте"] = round(0.06 * frac, 3)
    return f


def clusters(hits):
    """Группы вариантов с одним и тем же текстом (похожесть слов ≥ 0.45)."""
    sets = [set(_words(h.text)) for h in hits]
    group = list(range(len(hits)))
    for i in range(len(hits)):
        for j in range(i + 1, len(hits)):
            if group[j] != j:
                continue
            if _jaccard(sets[i], sets[j]) >= 0.45:
                group[j] = group[i]
    return group, sets


# ── слух: Whisper по 30 секундам середины трека ── #

def _model_ready():
    try:
        import lyrics_sync as LS
        if "small" in LS._MODEL:
            return True
        if not LS.whisper_installed():
            return False
        hub = Path.home() / ".cache" / "huggingface" / "hub" / "models--Systran--faster-whisper-small"
        return hub.exists()                              # без скачивания 460 МБ посреди поиска текста
    except Exception:                                    # noqa: BLE001
        return False


def heard_words(path, duration, language=None):
    """Слова, услышанные в отрывке трека (множество), или None."""
    if not path or str(path).startswith("stream:") or not Path(path).exists() or not _model_ready():
        return None
    with _HEARD_LOCK:
        stat=Path(path).stat()
        cache_key=(str(path),stat.st_size,stat.st_mtime_ns,language)
        if cache_key in _HEARD:
            return _HEARD[cache_key]
        try:
            import lyrics_sync as LS
            dur = float(duration or 0)
            start = max(0.0, dur * 0.3)
            audio = LS._decode(path, start, 32.0)
            mdl = LS._load_model("small", lambda s: None, local_only=True)
            heard = LS.transcribe_words(mdl, audio, language, vad=True, max_seconds=30)
            res = {w for w, _s, _e in heard if len(w) >= 2}
        except Exception as e:                           # noqa: BLE001
            print("[lyrics verify] слух:", e)
            res = None
        if len(_HEARD)>=64:_HEARD.pop(next(iter(_HEARD)))
        _HEARD[cache_key] = res
        return res


def _overlap(heard, words):
    """Доля услышанных слов, которые есть в тексте (с нечётким сравнением и транслитом)."""
    if not heard:
        return 0.0
    import lyrics_sync as LS
    ws = set(words)
    tw = {LS._translit(w) for w in ws}
    hit = 0
    for h in heard:
        if h in ws or LS._translit(h) in tw:
            hit += 1
        elif len(h) >= 4 and any(abs(len(h) - len(w)) <= 2 and LS._sim(h, w) >= 0.8 for w in ws):
            hit += 1
    return hit / len(heard)


def choose(hits, cands, duration=0.0, path="", accept=0.72, log=print):
    """Лучший вариант с учётом проверки. None — если ни один не проходит порог."""
    ok = [h for h in hits if h.text and h.text.strip() and h.score >= accept - 0.1]
    if not ok:
        return None
    group, sets = clusters(ok)
    sources = {}
    for h, g in zip(ok, group):
        sources.setdefault(g, set()).add(h.source)
    final = []
    for h, g in zip(ok, group):
        f = features(h, cands, duration)
        agree = len(sources[g]) - 1
        if agree:
            f["согласие источников"] = round(min(0.12, 0.04 * agree), 3)
        final.append([h.score + sum(f.values()), h, g, f])
    # спорно: лучшие разные тексты близки по оценке, или у трека не было исполнителя и победитель — одиночка
    by_group = {}
    for s, h, g, f in final:
        if s > by_group.get(g, (-9, None))[0]:
            by_group[g] = (s, h)
    tops = sorted((v[0] for v in by_group.values()), reverse=True)
    no_artist = not any(a for a, _t in cands)
    lead = max(final, key=lambda x: x[0])
    disputed = len(tops) >= 2 and (tops[0] - tops[1] < 0.2 or (no_artist and len(sources[lead[2]]) == 1))
    if disputed:
        lang = None
        try:
            import lyrics_sync as LS
            lang = LS._guess_language(lead[1].text)
        except Exception:                                # noqa: BLE001
            pass
        heard = heard_words(path, duration, lang)
        if heard and len(heard) >= 6:
            for row in final:
                ov = _overlap(heard, sets[ok.index(row[1])])
                adj = round(max(-0.3, min(0.45, (ov - 0.2) * 1.2)), 3)
                row[3]["слух"] = adj
                row[0] += adj
    for s, h, g, f in sorted(final, key=lambda x: -x[0])[:4]:
        log(f"[lyrics verify] {s:.2f} {h.source} {h.artist!r} - {h.title!r} synced={h.synced} {f}")
    passing = [r for r in final if r[0] >= accept]
    if not passing:
        return None
    syn = [r for r in passing if r[1].synced]
    plain = [r for r in passing if not r[1].synced and not r[1].instrumental]
    inst = [r for r in passing if r[1].instrumental and r[0] >= accept + 0.15]
    best_syn = max(syn, key=lambda r: r[0]) if syn else None
    best_plain = max(plain, key=lambda r: r[0]) if plain else None
    if best_syn and (not best_plain or best_syn[0] >= best_plain[0] - 0.05):
        return best_syn[1]                               # с таймингами — если не явно хуже обычного
    if best_plain:
        return best_plain[1]
    if inst:
        return max(inst, key=lambda r: r[0])[1]
    return best_syn[1] if best_syn else None
