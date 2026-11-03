"""Collection protection: a second ECHOES must not erase tracks; lost tracks can be found again.

Reported: a friend lost ~300 tracks from the collection. The only code path that drops tracks without asking
is an overwrite by another instance holding an older collection in memory."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import library_guard as LG  # noqa: E402
from atomic_json import load_json, save_json  # noqa: E402


def load(path):
    return [t for t in load_json(path, []) if isinstance(t, dict)]


class Guard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = Path(self.tmp.name)
        self.file = self.dir / 'library.json'
        self.music = self.dir / 'music'
        self.music.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def tracks(self, start, n):
        out = []
        for i in range(start, start + n):
            p = self.music / f'song{i}.mp3'
            p.write_bytes(b'ID3')
            out.append({'path': str(p.resolve()), 'title': f'song{i}'})
        return out

    def test_second_instance_cannot_erase_tracks(self):
        save_json(self.file, self.tracks(0, 10))
        a_lib = load(self.file)
        a = LG.Guard(self.dir, self.file, load, save_json); a.loaded(a_lib)
        b_lib = load(self.file)
        b = LG.Guard(self.dir, self.file, load, save_json); b.loaded(b_lib)
        b_lib.extend(self.tracks(10, 300)); b.save(b_lib)            # second ECHOES downloads 300 tracks
        added = a.save(a_lib)                                       # first one saves its old collection
        self.assertEqual(added, 300)
        self.assertEqual(len(load(self.file)), 310)

    def test_explicit_removal_is_not_brought_back(self):
        save_json(self.file, self.tracks(0, 10))
        a_lib = load(self.file); a = LG.Guard(self.dir, self.file, load, save_json); a.loaded(a_lib)
        b_lib = load(self.file); b = LG.Guard(self.dir, self.file, load, save_json); b.loaded(b_lib)
        b_lib.append(self.tracks(50, 1)[0]); b.save(b_lib)
        gone = a_lib.pop(0); a.forget([gone['path']])
        a.save(a_lib)
        paths = {t['path'] for t in load(self.file)}
        self.assertNotIn(gone['path'], paths)
        self.assertEqual(len(paths), 10)

    def test_shrink_without_removal_leaves_snapshot_and_daily_backup(self):
        lib = self.tracks(0, 400)
        save_json(self.file, lib)
        g = LG.Guard(self.dir, self.file, load, save_json); g.loaded(lib)
        g.save(lib[:100])
        self.assertTrue(list(self.dir.glob('library.shrink-*.json')))
        self.assertTrue(list((self.dir / 'backups').glob('library-*.json')))
        self.assertIn('без явного удаления', (self.dir / 'library_changes.log').read_text('utf-8'))

    def test_find_lost_recovers_tracks_remembered_elsewhere(self):
        all_tracks = self.tracks(0, 310)
        lib = all_tracks[:10]
        lost = all_tracks[10:]
        (self.dir / 'playlists.json').write_text(json.dumps({'P': {'tracks': lost[:200]}}), 'utf-8')
        (self.dir / 'lyrics_cache.json').write_text(json.dumps({t['path']: {'text': ''} for t in lost[200:]}), 'utf-8')
        found = LG.find_lost(self.dir, lib)
        self.assertEqual(len(found), 300)
        os.remove(lost[0]['path'])                                  # файла нет — не предлагать
        self.assertEqual(len(LG.find_lost(self.dir, lib)), 299)


@unittest.skipUnless(os.name == 'nt', 'Windows mutex')
class SingleInstance(unittest.TestCase):
    def test_mutex_detects_second_start(self):
        import single_instance as S
        name = 'ECHOES-test-' + os.urandom(4).hex()
        self.assertTrue(S._first_by_mutex(name))
        self.assertFalse(S._first_by_mutex(name))


if __name__ == '__main__':
    unittest.main()
