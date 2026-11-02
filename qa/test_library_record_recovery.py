"""Mixed malformed library rows must not erase healthy tracks or crash startup."""
import ast
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
from artist_identity import index_for


def functions():
    tree=ast.parse((ROOT/'app/main.py').read_text('utf-8'))
    selected=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name in ('load_json','load_library')]
    namespace={};exec(compile(ast.Module(body=selected,type_ignores=[]),'main-library-loader','exec'),namespace)
    return namespace


class LibraryRecordRecovery(unittest.TestCase):
    def test_healthy_rows_keep_identity_order_and_unknown_metadata(self):
        namespace=functions()
        a={'path':'synthetic-a.flac','artist':'First','custom':{'keep':True}}
        b={'path':'stream:synthetic-b','artist':'Second'}
        records=[None,a,'bad-row',b,42,[],{}]
        namespace['load_json']=Mock(return_value=records)
        loaded=namespace['load_library'](Path('unused-test-library.json'))
        self.assertEqual(loaded,[a,b,{}])
        self.assertIs(loaded[0],a);self.assertIs(loaded[1],b)
        self.assertEqual(records,[None,a,'bad-row',b,42,[],{}])

    def test_loading_mixed_rows_does_not_rewrite_or_create_profile_files(self):
        namespace=functions()
        with tempfile.TemporaryDirectory(prefix='echoes-library-row-probe-') as folder:
            path=Path(folder)/'library.json'
            payload=json.dumps([None,{'path':'synthetic-a.flac','title':'Healthy'}]).encode('utf-8-sig')
            path.write_bytes(payload)
            self.assertEqual(namespace['load_library'](path),[{'path':'synthetic-a.flac','title':'Healthy'}])
            self.assertEqual(path.read_bytes(),payload)
            self.assertEqual([p.name for p in Path(folder).iterdir()],['library.json'])

    def test_real_artist_index_accepts_recovered_collection(self):
        namespace=functions()
        with tempfile.TemporaryDirectory(prefix='echoes-library-index-probe-') as folder:
            path=Path(folder)/'library.json'
            path.write_text(json.dumps([None,{'path':'synthetic-a.flac','title':'Healthy','artist':'Synthetic Artist'}]),'utf-8')
            self.assertEqual(len(namespace['load_json'](path,[])),2)
            tracks=namespace['load_library'](path)
            self.assertEqual(len(tracks),1)
            self.assertIsNotNone(index_for(tracks))

    def test_all_invalid_current_records_recover_healthy_backup_without_rewriting(self):
        namespace=functions()
        with tempfile.TemporaryDirectory(prefix='echoes-library-fallback-probe-') as folder:
            path=Path(folder)/'library.json';backup=path.with_name(path.name+'.bak')
            payload=b'[null,42,"broken"]';path.write_bytes(payload)
            tracks=[{'path':'synthetic-good.flac','title':'Recovered','artist':'Synthetic'}]
            backup.write_text(json.dumps(tracks),'utf-8')
            self.assertEqual(namespace['load_library'](path),tracks)
            self.assertEqual(path.read_bytes(),payload)

    def test_intentionally_empty_current_collection_is_not_replaced_by_backup(self):
        namespace=functions()
        with tempfile.TemporaryDirectory(prefix='echoes-library-empty-probe-') as folder:
            path=Path(folder)/'library.json';path.write_bytes(b'[]')
            path.with_name(path.name+'.bak').write_text('[{"path":"old-deleted.flac"}]','utf-8')
            self.assertEqual(namespace['load_library'](path),[])
            self.assertEqual(path.read_bytes(),b'[]')

    def test_invalid_current_and_invalid_backup_return_empty_default(self):
        namespace=functions()
        with tempfile.TemporaryDirectory(prefix='echoes-library-unusable-probe-') as folder:
            path=Path(folder)/'library.json';backup=path.with_name(path.name+'.bak')
            path.write_bytes(b'[null]');backup.write_bytes(b'[42,"invalid"]')
            self.assertEqual(namespace['load_library'](path),[])
            self.assertEqual(path.read_bytes(),b'[null]')
            self.assertEqual(backup.read_bytes(),b'[42,"invalid"]')


if __name__=='__main__':unittest.main(verbosity=2)
