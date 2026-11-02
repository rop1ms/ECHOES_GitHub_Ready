"""Only profile JSON corruption, atomic write failure and recovery scenarios."""
import ast
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
import atomic_json


class ProfileJsonRecovery(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='echoes-json-probe-')
        self.root=Path(self.temporary.name);self.path=self.root/'library.json'
        self.tracks=[{'path':'synthetic-track.flac','title':'Трек','artist':'Artist'}]
    def tearDown(self):self.temporary.cleanup()

    def test_utf8_bom_library_loads_without_changing_file(self):
        payload=json.dumps(self.tracks,ensure_ascii=False).encode('utf-8-sig')
        self.path.write_bytes(payload)
        self.assertEqual(atomic_json.load_json(self.path,[]),self.tracks)
        self.assertEqual(self.path.read_bytes(),payload)
        self.assertFalse(atomic_json.backup_path(self.path).exists())

    def test_malformed_empty_and_truncated_files_recover_without_touching_evidence(self):
        atomic_json.save_json(self.path,self.tracks)
        for payload in (b'',b'[',b'[{"path":"cut',b'\xff\xfe\0'):
            self.path.write_bytes(payload)
            self.assertEqual(atomic_json.load_json(self.path,[]),self.tracks)
            self.assertEqual(self.path.read_bytes(),payload)

    def test_wrong_container_shape_recovers_last_good_library(self):
        atomic_json.save_json(self.path,self.tracks)
        self.path.write_text('{}','utf-8')
        self.assertEqual(atomic_json.load_json(self.path,[]),self.tracks)

    def test_valid_intentionally_empty_collection_wins_over_nonempty_backup(self):
        atomic_json.save_json(self.path,self.tracks)
        self.path.write_text('[]','utf-8')
        self.assertEqual(atomic_json.load_json(self.path,[]),[])

    def test_invalid_object_does_not_truncate_existing_library_or_backup(self):
        atomic_json.save_json(self.path,self.tracks);before=self.path.read_bytes()
        with self.assertRaises(TypeError):atomic_json.save_json(self.path,{'bad':object()})
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(atomic_json.backup_path(self.path).read_bytes(),before)

    def test_interrupted_temp_write_does_not_touch_target_or_backup(self):
        atomic_json.save_json(self.path,self.tracks);before=self.path.read_bytes()
        real_fdopen=atomic_json.os.fdopen
        class Interrupted:
            def __init__(self,fd):self.stream=real_fdopen(fd,'wb')
            def __enter__(self):return self
            def write(self,payload):self.stream.write(payload[:5]);raise OSError('synthetic interrupted write')
            def __exit__(self,*args):self.stream.close()
        with patch.object(atomic_json.os,'fdopen',side_effect=lambda fd,mode:Interrupted(fd)):
            with self.assertRaises(OSError):atomic_json.save_json(self.path,[])
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(atomic_json.backup_path(self.path).read_bytes(),before)
        self.assertEqual(list(self.root.glob('*.tmp')),[])

    def test_failed_commit_after_corrupt_restart_never_destroys_good_backup(self):
        atomic_json.save_json(self.path,self.tracks);self.path.write_bytes(b'[interrupted')
        recovered=atomic_json.load_json(self.path,[]);recovered.append({'path':'new-download.ogg'})
        with patch.object(atomic_json.os,'replace',side_effect=OSError('synthetic disk failure')):
            with self.assertRaises(OSError):atomic_json.save_json(self.path,recovered)
        self.assertEqual(atomic_json.load_json(self.path,[]),self.tracks)
        self.assertEqual(self.path.read_bytes(),b'[interrupted')
        self.assertEqual(list(self.root.glob('*.tmp')),[])

    def test_new_download_after_recovery_commits_known_valid_backup(self):
        atomic_json.save_json(self.path,self.tracks);self.path.write_bytes(b'')
        recovered=atomic_json.load_json(self.path,[])+[{'path':'new-download.ogg'}]
        atomic_json.save_json(self.path,recovered)
        self.assertEqual(json.loads(self.path.read_text('utf-8')),recovered)
        self.assertEqual(json.loads(atomic_json.backup_path(self.path).read_text('utf-8')),recovered)

    def test_failed_backup_refresh_leaves_committed_library_readable(self):
        atomic_json.save_json(self.path,self.tracks)
        real_replace=atomic_json.os.replace
        def replace(source,target):
            if target==atomic_json.backup_path(self.path):raise OSError('synthetic backup denied')
            return real_replace(source,target)
        with patch.object(atomic_json.os,'replace',side_effect=replace):atomic_json.save_json(self.path,[])
        self.assertEqual(atomic_json.load_json(self.path,[]),[])
        self.assertEqual(json.loads(atomic_json.backup_path(self.path).read_text('utf-8')),self.tracks)

    def test_concurrent_readers_never_observe_partial_json(self):
        atomic_json.save_json(self.path,self.tracks);errors=[];finished=threading.Event()
        def writer():
            try:
                for i in range(35):atomic_json.save_json(self.path,[dict(self.tracks[0],revision=i)])
            except Exception as error:errors.append(error)
            finally:finished.set()
        thread=threading.Thread(target=writer);thread.start()
        while not finished.is_set():
            value=atomic_json.load_json(self.path,[])
            if not value:errors.append('reader observed missing/partial collection')
        thread.join(2);self.assertEqual(errors,[])

    def test_main_wrappers_use_atomic_helpers_without_importing_or_launching_application(self):
        tree=ast.parse((ROOT/'app/main.py').read_text('utf-8'))
        definitions=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name in ('load_json','save_json')]
        namespace={};exec(compile(ast.Module(body=definitions,type_ignores=[]),'main-json-wrappers','exec'),namespace)
        namespace['save_json'](self.path,self.tracks)
        self.path.write_bytes(b'')
        self.assertEqual(namespace['load_json'](self.path,[]),self.tracks)

    def test_defaults_and_compact_unicode_serialization_remain_compatible(self):
        default={};self.assertIs(atomic_json.load_json(self.path,default),default)
        atomic_json.save_json(self.path,self.tracks)
        self.assertEqual(self.path.read_text('utf-8'),json.dumps(self.tracks,ensure_ascii=False,separators=(',',':')))
        self.assertEqual(len(list(self.root.iterdir())),2)


if __name__=='__main__':unittest.main(verbosity=2)
