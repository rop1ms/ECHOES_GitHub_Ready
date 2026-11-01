"""Deterministic voice routing and scenario validation; no microphone or Qt required.

Unrecognised speech is deliberately inert. Searching music requires an explicit
verb, and background listening requires a complete, unambiguous wake word.
"""
from __future__ import annotations

import re


WAKE = frozenset(("эхос", "ехос", "экос", "echoes"))
MAX_COMMANDS = 100
MAX_STEPS = 40
MAX_ALIASES = 12
AUTOMATION_ACTIONS = frozenset(("wave", "playlist", "volume", "quieter", "pause", "notify"))
SCENARIO_ACTIONS = AUTOMATION_ACTIONS | frozenset((
    "play", "next", "prev", "vol_up", "vol_down", "mute", "unmute", "like", "mini", "shuffle", "find", "wait",
))


def normalize(text) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", str(text or "").casefold().replace("ё", "е")).split())


def strip_wake(text) -> tuple[bool, str]:
    words = normalize(text).split()
    # A greeting is allowed only before the wake word, never an arbitrary word.
    if words and words[0] in ("эй", "hey", "ok", "okay", "хей"):
        words = words[1:]
    # Whisper may split the consonant at the end of the unfamiliar proper name
    # into a separate Russian preposition. Accept that exact two-token spelling,
    # while the common standalone noun «эхо» remains deliberately insufficient.
    if words[:2] == ["эхо", "с"]:
        return True, " ".join(words[2:])
    if words and words[0] in WAKE:
        return True, " ".join(words[1:])
    return False, normalize(text)


_PATTERNS = (
    ("next", r"(?:следующий(?: трек| трэк)?|следующая(?: песня)?|следующее|следующей|дальше|пропусти(?: трек)?|скип|некст|вперед|next(?: track| song)?|skip(?: track| song)?)"),
    ("prev", r"(?:предыдущий(?: трек| трэк)?|предыдущая(?: песня)?|предыдущее|предыдущей|назад|previous(?: track| song)?|go back|back)"),
    ("pause", r"(?:пауза|стоп|поставь (?:на )?паузу|останови(?: музыку| воспроизведение)?|выключи музыку|pause(?: music| playback)?|stop(?: music| playback)?)"),
    ("play", r"(?:продолжи(?: музыку| воспроизведение)?|продолжить|включи (?:музыку|воспроизведение)|играй|играть|плей|возобнови(?: музыку| воспроизведение)?|play(?: music)?|resume(?: music| playback)?|continue(?: music| playback)?)"),
    ("vol_up", r"(?:громче|погромче|прибавь(?: громкость)?|сделай громче|louder|volume up|turn (?:it |the volume )?up)"),
    ("vol_down", r"(?:тише|потише|убавь(?: громкость)?|сделай тише|quieter|softer|volume down|turn (?:it |the volume )?down)"),
    ("mute", r"(?:без звука|выключи звук|мьют|mute(?: audio| sound)?)"),
    ("unmute", r"(?:включи звук|верни звук|unmute(?: audio| sound)?)"),
    ("like", r"(?:лайк|нравится|мне нравится|в избранное|добавь в избранное|сердечко|like(?: this(?: song| track)?)?|favorite(?: this(?: song| track)?)?)"),
    ("mini", r"(?:мини плеер|миниплеер|маленький плеер|(?:открой|включи) мини плеер|(?:open )?mini player)"),
    ("wave", r"(?:(?:включи|поставь|запусти) (?:мою )?волну|моя волна|волна|(?:play|start|open) (?:my )?wave|my wave)"),
    ("shuffle", r"(?:перемешай(?: музыку| треки)?|шафл|вперемешку|shuffle(?: music| tracks)?)"),
)
INTENTS = tuple((re.compile(pattern), action) for action, pattern in _PATTERNS)


def parse(text: str) -> tuple[str, str]:
    c = normalize(text)
    # Full matches prevent spoken sentences/song lyrics starting with a command
    # from executing it. Built-ins precede the explicit music-search verbs.
    for pattern, action in INTENTS:
        if pattern.fullmatch(c):
            return action, ""
    m = re.fullmatch(r"(?:громкость|установи громкость|сделай громкость|set (?:the )?volume(?: to)?|volume) (\d{1,3})(?: процентов| percent)?", c)
    if m and 0 <= int(m.group(1)) <= 100:
        return "volume", str(int(m.group(1)))
    m = re.fullmatch(r"(?:включи|поставь|запусти|играй|сыграй|play|start) (?:плейлист|playlist) (.+)", c)
    if m:
        return "playlist", m.group(1)
    m = re.fullmatch(r"(?:включи|поставь|запусти|играй|сыграй|найди|play|find|search for) (.+)", c)
    if m:
        return "find", m.group(1)
    return "", ""


def route(text, commands=(), *, require_wake=False) -> tuple[str, str]:
    woke, command = strip_wake(text)
    if require_wake and not woke:
        return "", ""
    if not command:
        return "", ""
    for item in commands:
        if not isinstance(item, dict) or not item.get("on", True):
            continue
        aliases = item.get("phrases") or []
        if isinstance(aliases, list) and command in (normalize(p) for p in aliases):
            return "scenario", str(item.get("id", ""))
    return parse(command)


def validate_commands(commands) -> list[dict]:
    """Return a clean, serialisable copy, or raise a user-facing validation key.

    Reject collisions rather than silently changing which scenario a phrase runs.
    The editor translates the keys into the selected interface language.
    """
    if not isinstance(commands, list) or len(commands) > MAX_COMMANDS:
        raise ValueError("commands_limit")
    result, aliases, identifiers = [], set(), set()
    for item in commands:
        if not isinstance(item, dict):
            raise ValueError("invalid_command")
        ident = str(item.get("id") or "")
        if not ident or ident in identifiers:
            raise ValueError("invalid_command")
        identifiers.add(ident)
        raw = item.get("phrases")
        if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_ALIASES:
            raise ValueError("phrases_required")
        phrases = []
        for phrase in raw:
            p = normalize(phrase)
            if not p or len(p) > 180:
                raise ValueError("phrases_required")
            woke, _ = strip_wake(p)
            if woke:
                raise ValueError("omit_wake")
            if p in aliases:
                raise ValueError("duplicate_phrase")
            if parse(p)[0]:
                raise ValueError("builtin_phrase")
            aliases.add(p)
            phrases.append(str(phrase).strip())
        raw_steps = item.get("steps")
        if not isinstance(raw_steps, list) or not 1 <= len(raw_steps) <= MAX_STEPS:
            raise ValueError("steps_required")
        steps = []
        for step in raw_steps:
            if not isinstance(step, dict) or step.get("a") not in SCENARIO_ACTIONS:
                raise ValueError("invalid_step")
            action = step["a"]
            clean = {"a": action}
            if action in ("volume", "quieter", "wait"):
                try:
                    value = int(step.get("v", 1 if action == "wait" else 50))
                except (TypeError, ValueError, OverflowError):
                    raise ValueError("invalid_number") from None
                if not 0 <= value <= (60 if action == "wait" else 100):
                    raise ValueError("invalid_number")
                clean["v"] = value
            elif action in ("playlist", "notify", "find"):
                key = "name" if action == "playlist" else "text"
                value = str(step.get(key) or "").strip()
                if not value or len(value) > 500:
                    raise ValueError("argument_required")
                clean[key] = value
            steps.append(clean)
        result.append({"id": ident, "on": bool(item.get("on", True)),
                       "name": str(item.get("name") or phrases[0]).strip()[:100],
                       "phrases": phrases, "steps": steps})
    return result
