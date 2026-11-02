"""AI lyrics sync output (lyrics_sync.aligned_times + build_lrc) and how the lyrics view reads it.

- every word of an aligned line gets its own <mm:ss.xx> tag, and lyrics_view.parse_lrc turns it back into
  per-word timings for the liquid fill (same count as the words of the line, in order, after the line start);
- line starts get the Whisper offset (WORD_BIAS), are strictly increasing, and a line with no aligned
  words is placed between its neighbours and written without word tags;
- one-word lines and lines with unaligned words stay plain lines."""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from PyQt6.QtWidgets import QApplication

APP = QApplication.instance() or QApplication([])
import lyrics_sync as LS  # noqa: E402
from lyrics_view import parse_lrc  # noqa: E402


def aligned(lines, starts):
    """starts[i] — list of word starts for line i (None — word not aligned)."""
    out = []
    for li, line in enumerate(lines):
        for k, w in enumerate(line.split()):
            s = starts[li][k] if starts[li] is not None else None
            out.append((li, w, None if s is None else (s, s + 0.3)))
    return out


class AlignedLrc(unittest.TestCase):
    lines = ['Get it out my head', 'Is it me or you instead', 'Hey', 'What am I to you', 'Last line here']

    def build(self, starts):
        times, words = LS.aligned_times(self.lines, aligned(self.lines, starts))
        return times, words, LS.build_lrc(self.lines, times, words)

    def test_word_tags_round_trip(self):
        starts = [[10.0, 10.3, 10.6, 10.8, 11.1], [12.4, 12.7, 12.9, 13.1, 13.4, 13.8], [15.0],
                  [19.8, 20.1, 20.3, 20.6, 20.9], [24.0, 24.3, 24.9]]
        times, words, lrc = self.build(starts)
        b = LS.WORD_BIAS
        self.assertAlmostEqual(times[0], 10.0 + b, places=3)
        res = parse_lrc(lrc)
        parsed = [ln for ln in (res[0] if isinstance(res, tuple) else res) if ln['ms'] >= 0]
        self.assertEqual([ln['text'] for ln in parsed], self.lines)
        for ln, src, line in zip(parsed, starts, self.lines):
            n = len(line.split())
            if n == 1:
                self.assertIsNone(ln['words'], 'one-word line stays plain')
                continue
            self.assertEqual([w for _, w in ln['words']], line.split())
            got = [ms / 1000 for ms, _ in ln['words']]
            for g, s in zip(got, src):
                self.assertAlmostEqual(g, s + b, delta=0.011)
            self.assertEqual(got, sorted(got))
            self.assertGreaterEqual(got[0], ln['ms'] / 1000 - 0.011)

    def test_unaligned_line_between_neighbours(self):
        starts = [[10.0, 10.3, 10.6, 10.8, 11.1], None, [15.0], [19.8, None, 20.3, 20.6, 20.9], [24.0, 24.3, 24.9]]
        times, words, lrc = self.build(starts)
        self.assertTrue(times[0] < times[1] < times[2] < times[3] < times[4])
        self.assertIsNone(words[1], 'no aligned words — no word tags')
        self.assertIsNone(words[3], 'a word missing — no partial word tags')
        self.assertNotIn('<', lrc.splitlines()[1])
        self.assertNotIn('<', lrc.splitlines()[3])

    def test_strictly_increasing(self):
        starts = [[10.0] * 5, [10.0] * 6, [10.0], [10.0] * 5, [10.0] * 3]
        times, _w, _l = self.build(starts)
        for a, b in zip(times, times[1:]):
            self.assertGreaterEqual(b - a, 0.2 - 1e-9)


if __name__ == '__main__':
    unittest.main()
