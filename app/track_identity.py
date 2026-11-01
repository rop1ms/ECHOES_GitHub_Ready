# track_identity.py
"""
Кто исполнитель и как называется трек — по «грязным» тегам и имени файла.

Типичные случаи из реальной библиотеки, которые надо понимать:
  • «Blessed - zxcursed - SoundLoadMate.com»   тег артиста пуст, порядок
                                               «Название - Исполнитель» + мусор сайта
  • «Hideki Naganuma - DA PEOPLE» / тег «DJ TGS» настоящий артист в названии,
                                               в теге — загрузивший
  • «In My Room- Julia Wolf (lyric video)»     дефис без пробела, приписка
  • «07 Linkin Park - Faint», «12 - Versus»    номера дорожек
  • «C SECTION PROD HEAVENGAZER»               «prod» без скобок
  • «The Vampire _ ヴァンパイア - …»            «/» превращён в «_» при скачивании
  • «key vs. locket, Laura Almeida»            несколько артистов
  • «KiloWatts_-_Topic», «… [cbVmnzr7AOU]»     следы YouTube / yt-dlp

parse_candidates() возвращает упорядоченный список гипотез (artist, title):
самые вероятные — первыми. Дальше поиск пробует их по очереди и ПРОВЕРЯЕТ
найденное через match_score(): название/артист/длительность.
"""

from __future__ import annotations

import functools
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

# ── мусор ─────────────────────────────────────────────────────────────── #

_SITE_JUNK = re.compile(
    r"\s*[-–—|]?\s*\b(?:soundloadmate\.com|soundloadmate|zippyshare\.com|mp3skull\.com|"
    r"hulkshare\.com|mp3juices?\.\w+|mp3clan\.com|youtubetomp3\.\w+|ytmp3\.\w+|"
    r"downloadmp3\.\w+|y2mate\.\w+|savefrom\.\w+|ssyoutube\.\w+|musify\.\w+|"
    r"muzofond\.\w+|zaycev\.net|hitmotop\.\w+)\b",
    re.IGNORECASE)
_YT_ID = re.compile(r"\s*\[[A-Za-z0-9_-]{11}\]\s*$")          # yt-dlp: «… [cbVmnzr7AOU]»
_NUM_ID = re.compile(r"[-_\s]\d{9,}$")                          # «ТЦК-1769966469»
_COPY_TAIL = re.compile(r"(?:\s*(?:\(\d{1,2}\)|[-–—]\s*(?:копия|copy)(?:\s*\(\d+\))?|\.(?:mp3|m4a|flac|wav|ogg|opus)))+\s*$", re.I)
_DL_TAIL = re.compile(r"[\s_]*(?:klickaud|forhub[\s_]*soundcloud[\s_]*to[\s_]*mp3|"
                      r"soundcloud[\s_]*to[\s_]*mp3|sclouddownloader|scdownloader|"
                      r"\(?(?:youtube|soundcloud)\)?)\s*$", re.I)
_HASHY = re.compile(r"^[A-Za-z0-9+/=_-]{16,}$")
_LEAD_JUNK_ID = re.compile(r"^[a-z0-9]{10,12}-(?=[a-z])", re.I)  # «rl089hxopkw-across-the-sea»
_TRACK_NO = re.compile(r"^\s*(?:\d{1,3}\s*[.)\-–—]\s+|0\d\s+(?=\S)|\d{2}\s+(?=\S+\s+-\s))")
_TOPIC = re.compile(r"[\s_]*-[\s_]*topic$", re.I)
_VIDEO_NOISE = re.compile(
    r"[\(\[]\s*(?:official\s*(?:music\s*)?(?:video|audio|visualizer|lyric\s*video|mv)?|"
    r"lyric(?:s)?(?:\s*video)?|audio|video|visualizer|music\s*video|mv|hd|hq|4k|"
    r"full(?:\s*(?:song|version|ver\.?))?|explicit|clean|remaster(?:ed)?(?:\s*\d{4})?|"
    r"\d{4}\s*remaster(?:ed)?|free\s*(?:download|dl)|out\s*now|premiere|"
    r"slowed(?:\s*\+?\s*reverb)?|sped\s*up|speed\s*up|nightcore|8d(?:\s*audio)?|"
    r"bass\s*boost(?:ed)?)[^\)\]]*[\)\]]",
    re.I)
_INCL = re.compile(r"[\(\[]\s*incl\.?[^\)\]]*[\)\]]", re.I)
_FEAT = re.compile(r"\s*[\(\[]?\s*(?:\b(?:ft|feat|featuring|при\s*уч\.?)\b\.?|\bw/)\s*[^\)\]]+[\)\]]?", re.I)
_PROD = re.compile(r"\s*[\(\[]?\s*\b(?:prod(?:uced)?\.?\s*(?:by)?)\b\.?\s+[^\)\]]*[\)\]]?\s*$", re.I)
_BRACKETS = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")
_DECOR = re.compile(r"[▶►•★☆♪♫™®©#]+")
_ARTIST_SPLIT = re.compile(r"\s*(?:,|&|\+|\band\b|\bx\b|×|\bfeat\.?\b|\bft\.?\b|\bvs\.?\b(?!\s*\.)|/|;)\s*", re.I)
_SEPARATORS = [" - ", " – ", " — ", " | ", " _ ", " // ", " /// ", " ~ "]

_BAD_ARTISTS = {
    "", "unknown", "unknown artist", "неизвестный исполнитель", "various artists",
    "va", "various", "artist", "<unknown>", "music", "soundcloud", "youtube",
}


def _nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "")


def basic_clean(s: str) -> str:
    """Чистит строку от сайтов, id, номеров, видео-приписок; сохраняет feat/скобки
    с осмысленным содержимым (их уберут отдельные варианты)."""
    s = _nfkc(s)
    s = _YT_ID.sub("", s.strip())
    s = re.sub(r"\s*\[\d{6,}\]", "", s)                            # «[998446144]»
    s = re.sub(r"\s*[\(\[](?:spotisaver|spotidownloader|spotdl|soundloadmate)[\)\]]", "", s, flags=re.I)
    s = s.replace("_", " ")
    s = re.sub(r"\s+-\s+topic\s+-\s+", " - ", s, flags=re.I)        # «ARTIST - Topic - Title»
    s = re.sub(r"\s+", " ", s).strip()
    for _ in range(3):
        s0 = s
        s = _COPY_TAIL.sub("", s).strip()
        s = _YT_ID.sub("", s)
        s = _SITE_JUNK.sub("", s).strip()
        s = _DL_TAIL.sub("", s).strip()
        if s == s0:
            break
    s = _NUM_ID.sub("", s)
    s = _LEAD_JUNK_ID.sub("", s)
    s = _VIDEO_NOISE.sub("", s)
    s = _INCL.sub("", s)
    s = _DECOR.sub(" ", s)
    s = re.sub(r"\(\s*\)|\[\s*\]", "", s)
    s = re.sub(r"\s+", " ", s).strip(" -–—|_.,")
    return s


def strip_extras(title: str) -> str:
    """Название без feat./prod./любых скобок — «голое» имя трека."""
    t = _FEAT.sub("", title)
    t = _PROD.sub("", t)
    t = _BRACKETS.sub("", t)
    t = re.sub(r"\s+", " ", t).strip(" -–—|_.,")
    return t or title


def primary_artist(artist: str) -> str:
    a = _TOPIC.sub("", basic_clean(artist))
    parts = [p for p in _ARTIST_SPLIT.split(a) if p and p.strip()]
    return parts[0].strip() if parts else a


def is_bad_artist(artist: str) -> bool:
    a = _TOPIC.sub("", (artist or "")).strip().lower()
    return a in _BAD_ARTISTS or bool(_HASHY.match(a) and re.search(r"\d", a) and re.search(r"[a-z]", a) and re.search(r"[A-Z]", artist or ""))


def is_junk_title(title: str) -> bool:
    """«65wXsoL3sTv0IrIU2axI+GaGOwFAlwkU» — хэш вместо названия."""
    t = (title or "").strip()
    if not t:
        return True
    if _HASHY.match(t) and re.search(r"\d", t) and re.search(r"[a-z]", t) and re.search(r"[A-Z]", t):
        return True
    return False


def _stem(path: str) -> str:
    name = re.split(r"[\\/]", path or "")[-1]
    return re.sub(r"\.[A-Za-z0-9]{2,5}$", "", name)


def _split_pair(s: str):
    """Все разбиения строки по разделителю «A - B» (включая «A- B»)."""
    out = []
    for sep in _SEPARATORS:
        if sep in s:
            parts = [p.strip() for p in s.split(sep)]
            left, right = s.split(sep, 1)
            out.append((left.strip(), right.strip()))
            if len(parts) >= 3:
                # «Title - Artist - Uploader», «Artist - Title - Channel»
                for i in range(len(parts) - 1):
                    out.append((parts[i], parts[i + 1]))
                out.append((sep.join(parts[:-1]), parts[-1]))
    m = re.match(r"^(.+?\S)-\s+(\S.+)$", s)                       # «In My Room- Julia Wolf»
    if m:
        out.append((m.group(1).strip(), m.group(2).strip()))
    return [(a, b) for a, b in out if a and b]


@functools.lru_cache(maxsize=8192)
def norm(s: str) -> str:
    """Для сравнения: регистр, диакритика, пунктуация, пробелы. С кэшем: одни и те же названия
    сравниваются сотни раз (подбор текстов, жанров) — раньше каждый раз заново."""
    s = _nfkc(s).casefold()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s]", " ", s)
    s = s.replace("_", " ")
    return re.sub(r"\s+", " ", s).strip()


def _squash(s: str) -> str:
    return norm(s).replace(" ", "")


def similarity(a: str, b: str) -> float:
    """0..1: учитывает «lucybedroque» ≈ «Lucy Bedroque», порядок слов, вхождение."""
    try:
        import bgproc
        bgproc.throttle()                    # в фоновом потоке — не отнимать GIL у интерфейса
    except Exception:                        # noqa: BLE001
        pass
    na, nb = norm(a or ""), norm(b or "")
    if not na or not nb:
        return 0.0
    if na == nb or _squash(a) == _squash(b):
        return 1.0
    r1 = SequenceMatcher(None, na, nb).ratio()
    r2 = SequenceMatcher(None, _squash(a), _squash(b)).ratio()
    ta, tb = set(na.split()), set(nb.split())
    jac = len(ta & tb) / max(1, len(ta | tb))
    contain = 0.0
    sa, sb = _squash(a), _squash(b)
    if len(sa) >= 3 and len(sb) >= 3 and (sa in sb or sb in sa):
        contain = 0.85 * min(len(sa), len(sb)) / max(len(sa), len(sb)) + 0.1
    return max(r1, r2, jac, contain)


def parse_candidates(title: str, artist: str = "", path: str = "", album: str = "") -> list[tuple[str, str]]:
    """Гипотезы (artist, title) от самой вероятной к менее вероятной."""
    if str(path or "").startswith("stream:"):
        path = ""                                     # онлайн-трек (stream_core): имени файла нет
    cands: list[tuple[str, str, float]] = []

    def add(a, t, w):
        a = primary_artist(a) if a else ""
        t = basic_clean(t)
        if not t or is_junk_title(t):
            return
        if is_bad_artist(a):
            a = ""
        cands.append((a, t, w))
        bare = strip_extras(t)
        if bare and bare != t:
            cands.append((a, bare, w - 0.05))
        # «only human / anywhere at all», «The Vampire / ヴァンパイア» — части названия
        for sep in (" / ", " _ ", " | "):
            if sep in bare:
                for part in bare.split(sep):
                    part = part.strip()
                    if len(part) >= 2:
                        cands.append((a, part, w - 0.12))

    tag_artist = basic_clean(artist)
    tag_artist_ok = bool(tag_artist) and not is_bad_artist(tag_artist)
    t0 = basic_clean(title)
    t0 = _TRACK_NO.sub("", t0)

    stem = _stem(path)
    s0 = basic_clean(stem)
    s0 = _TRACK_NO.sub("", s0)

    for src, base_w in ((t0, 1.0), (s0, 0.8)):
        if not src:
            continue
        pairs = _split_pair(src)
        if pairs:
            for left, right in pairs:
                left_a = similarity(left, tag_artist) if tag_artist_ok else 0.0
                right_a = similarity(right, tag_artist) if tag_artist_ok else 0.0
                if tag_artist_ok and right_a >= 0.8:          # «Title - Artist» (SoundLoadMate)
                    add(right, left, base_w + 0.1)
                elif tag_artist_ok and left_a >= 0.8:          # «Artist - Title»
                    add(left, right, base_w + 0.1)
                else:
                    # артист в названии не совпал с тегом: тег — часто загрузивший
                    # («DJ TGS»). Обе гипотезы, чаще встречается «Artist - Title».
                    add(left, right, base_w - 0.05)
                    if tag_artist_ok:
                        add(tag_artist, src, base_w - 0.08)
                    add(right, left, base_w - 0.15)
        else:
            if tag_artist_ok:
                add(tag_artist, src, base_w)
            else:
                add("", src, base_w - 0.3)
    if tag_artist_ok and t0:
        add(tag_artist, t0, 0.9)

    # дедупликация с сохранением лучшего веса
    best: dict[tuple[str, str], float] = {}
    order: list[tuple[str, str]] = []
    for a, t, w in cands:
        key = (norm(a), norm(t))
        if not key[1]:
            continue
        if key not in best:
            order.append((a, t))
            best[key] = w
        else:
            best[key] = max(best[key], w)
    order.sort(key=lambda p: -best[(norm(p[0]), norm(p[1]))])
    return order[:8]


def match_score(cands: list[tuple[str, str]], found_artist: str, found_title: str,
                found_duration: float = 0.0, duration: float = 0.0) -> float:
    """Насколько найденный трек (artist/title/duration) похож на наш. 0..~1.3"""
    if not found_title:
        return 0.0
    ft = strip_extras(basic_clean(found_title))
    fa = found_artist or ""
    best = 0.0
    for a, t in cands[:6]:
        ts = max(similarity(t, ft), similarity(strip_extras(t), ft))
        if a:
            fas = [fa] + [p for p in _ARTIST_SPLIT.split(fa) if p]
            as_ = max(similarity(a, x) for x in fas) if fa else 0.3
            s = 0.62 * ts + 0.38 * as_
            if ts >= 0.92 and as_ < 0.35:
                s = min(s, 0.55)                        # то же название, другой артист
        else:
            s = 0.85 * ts
        best = max(best, s)
    if duration > 0 and found_duration > 0:
        d = abs(duration - found_duration)
        if d <= 2.5:
            best += 0.22
        elif d <= 6:
            best += 0.08
        elif d > 20:
            best -= 0.35
    return best
