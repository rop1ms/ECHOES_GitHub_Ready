"""Bounded station schedule, independent of the host's personal audio player."""
from __future__ import annotations
import base64
from functools import lru_cache
import io
import secrets
import time

MAX_QUEUE = 100

def clean_avatar(value):
    if not isinstance(value, str) or len(value) > 24576:
        return ''
    return _avatar_checked(value)

@lru_cache(maxsize=32)
def _avatar_checked(value):
    try:
        from PIL import Image
        raw = base64.b64decode(value, validate=True)
        if len(raw) > 18432 or not raw.startswith(b'\xff\xd8\xff'):
            return ''
        with Image.open(io.BytesIO(raw)) as image:
            if image.format != 'JPEG' or not (1 <= image.width <= 192 and 1 <= image.height <= 192):
                return ''
            image.verify()
        return value
    except Exception:
        return ''

def profile(raw):
    raw = raw if isinstance(raw, dict) else {}
    avatar = raw.get('avatar', '')
    return {'name': str(raw.get('name') or 'Echoes Radio')[:80].strip() or 'Echoes Radio',
            'avatar': clean_avatar(avatar) if isinstance(avatar, str) else ''}

def clean_wire(raw, previous=None):
    """Validate full packets; retain branding/queue for revision-matched deltas."""
    if not isinstance(raw, dict):
        return None
    from net_core import clean_track
    previous = previous if isinstance(previous, dict) else {}
    revision = str(raw.get('revision') or '')[:32]
    base = previous if previous.get('revision') == revision else {}
    out = profile({**base, **raw})
    out.update(revision=revision, current=str(raw.get('current') or '')[:32], repeat=bool(raw.get('repeat', True)))
    queue = raw.get('queue', base.get('queue', []))
    out['queue'] = []
    for item in queue[:MAX_QUEUE] if isinstance(queue, list) else []:
        if isinstance(item, dict) and (track := clean_track(item.get('track'))):
            out['queue'].append({'id': str(item.get('id') or '')[:32], 'track': track})
    return out

def summary(raw):
    if not isinstance(raw, dict): return None
    return profile(raw)

class Station:
    def __init__(self, config, clock=time.monotonic):
        self.clock = clock
        self.playing = True
        self.offset = 0.
        self.started = clock()
        self.current = ''
        self.configure(config)

    def configure(self, config):
        from net_core import clean_track
        queue = []
        seen = set()
        raw_queue = config.get('queue')
        for item in raw_queue[:MAX_QUEUE] if isinstance(raw_queue, list) else []:
            if not isinstance(item, dict): continue
            track = clean_track(item.get('track'))
            if not track or track.get('duration', 0) <= 0: continue
            qid = str(item.get('id') or secrets.token_hex(8))[:32]
            if qid in seen: qid = secrets.token_hex(8)
            seen.add(qid)
            queue.append({'id': qid, 'track': track})
        if not queue: raise ValueError('Choose tracks with a known duration for the station queue.')
        position = self.position() if hasattr(self, 'queue') else 0.
        old_index = next((i for i, q in enumerate(getattr(self, 'queue', [])) if q['id'] == self.current), 0)
        self.queue = queue
        self.brand = profile(config)
        self.genre = str(config.get('genre') or '')[:40]
        self.repeat = bool(config.get('repeat', True))
        if self.current not in seen:
            self.current = queue[min(old_index, len(queue)-1)]['id']; position = 0.
        self.offset = position; self.started = self.clock()
        self.revision = secrets.token_hex(8)
        self.advance()

    def position(self):
        return max(0., self.offset + (self.clock()-self.started if self.playing else 0.))

    def advance(self):
        elapsed = self.position()
        index = next(i for i, q in enumerate(self.queue) if q['id'] == self.current)
        if self.repeat:
            elapsed %= sum(q['track']['duration'] for q in self.queue)
        while elapsed >= self.queue[index]['track']['duration']:
            elapsed -= self.queue[index]['track']['duration']
            if index+1 == len(self.queue) and not self.repeat:
                elapsed = self.queue[index]['track']['duration']; self.playing = False; break
            index = (index+1) % len(self.queue)
        self.current = self.queue[index]['id']
        self.offset = elapsed; self.started = self.clock()

    def control(self, action, qid=None):
        self.advance()
        if action == 'pause': self.playing = not self.playing
        elif action == 'next':
            index = next(i for i,q in enumerate(self.queue) if q['id'] == self.current)
            if index+1 == len(self.queue) and not self.repeat:
                self.playing = False; self.offset = self.queue[index]['track']['duration']
            else:
                self.current = self.queue[(index+1) % len(self.queue)]['id']; self.offset = 0.
        elif action == 'play' and any(q['id'] == qid for q in self.queue):
            self.current = qid; self.offset = 0.; self.playing = True
        self.started = self.clock()

    def state(self):
        self.advance()
        return {'track': next(q['track'] for q in self.queue if q['id'] == self.current),
                'pos': self.position(), 'playing': self.playing, 'ts': time.time(),
                'radio': {**self.brand, 'revision': self.revision, 'current': self.current,
                          'repeat': self.repeat, 'queue': self.queue}}

    def config(self):
        return {**self.brand, 'genre': self.genre, 'repeat': self.repeat, 'queue': self.queue}
