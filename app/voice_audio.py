"""Small streaming speech gate, independent of Qt/PortAudio/model packages."""
from __future__ import annotations

from collections import deque
import math

import numpy as np

SR = 16000


class SpeechBuffer:
    """Adaptive level gate with pre-roll, hysteresis and bounded utterances.

    Silero VAD and model confidence are a second gate at transcription time.
    Keeping 180 ms before the onset prevents clipping the wake word/consonants.
    All durations count samples, so arbitrary hardware callback sizes work.
    """

    def __init__(self, strict=False):
        self.strict = strict
        self.noise = 0.002
        self.pre = deque()
        self.pre_size = 0
        self.reset()

    def reset(self):
        self.parts = []
        self.samples = self.voiced = self.silent = 0
        self.started = False
        self.pre.clear()
        self.pre_size = 0

    def feed(self, audio):
        x = np.asarray(audio, dtype=np.float32).reshape(-1)
        if not x.size or not np.isfinite(x).all():
            return None
        level = float(np.sqrt(np.mean(x * x)))
        threshold = max(0.009 if self.strict else 0.006,
                        min(0.055, self.noise * (3.8 if self.strict else 2.8)))
        loud = level >= (threshold * 0.65 if self.started else threshold)
        if not self.started:
            if not loud:
                # Slowly adapt only to the background, not the user's voice.
                alpha = min(0.08, x.size / SR * 0.45)
                self.noise += alpha * (min(level, 0.02) - self.noise)
                self.pre.append(x)
                self.pre_size += x.size
                while self.pre and self.pre_size > int(SR * .18):
                    self.pre_size -= self.pre.popleft().size
                return None
            self.started = True
            self.parts.extend(self.pre)
            self.samples = self.pre_size
            self.pre.clear()
            self.pre_size = 0
        self.parts.append(x)
        self.samples += x.size
        if loud:
            self.voiced += x.size
            self.silent = 0
        else:
            self.silent += x.size
        # 320 ms endpoint instead of 600 ms, with enough context for short gaps.
        if self.silent >= int(SR * .32) or self.samples >= SR * 7:
            return self.finish()
        return None

    def finish(self):
        if not self.parts or self.voiced < int(SR * .18):
            self.reset()
            return None
        # Leave 100 ms trailing silence for VAD; don't decode the whole tail.
        audio = np.concatenate(self.parts)
        trim = max(0, self.silent - int(SR * .10))
        if trim:
            audio = audio[:-trim]
        self.reset()
        return audio


def confident_text(segments, *, strict=False):
    """Reject entire utterances with doubtful segments, not just their suffix.

    Dropping one uncertain segment could leave a confident command suffix and
    accidentally transform ordinary speech into a different command.
    """
    parts = []
    for segment in segments:
        text = str(segment.text or "").strip()
        if not text:
            continue
        silence = float(segment.no_speech_prob)
        logprob = float(segment.avg_logprob)
        compression = float(segment.compression_ratio)
        if (not all(math.isfinite(v) for v in (silence, logprob, compression))
                or silence > (.35 if strict else .50)
                or logprob < (-.70 if strict else -.95)
                or compression > 2.2):
            return ""
        parts.append(text)
    return " ".join(parts).strip()
