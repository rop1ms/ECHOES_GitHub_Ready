"""Collection rendering must survive optional analysis and failed first builds."""
import os
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import QApplication,QWidget,QVBoxLayout,QLineEdit,QLabel,QListView,QStackedWidget,QButtonGroup
from PyQt6 import sip
from yamusic_theme import YaShell,TrackModel,LIKED

APP=QApplication.instance() or QApplication([])


class CollectionProbe(QWidget):
    profiles=YaShell.profiles
    _fill_collection=YaShell._fill_collection
    _coll_apply=YaShell._coll_apply
    _set_coll_filter=YaShell._set_coll_filter
    _rebuild=YaShell._rebuild

    def __init__(self):
        super().__init__()
        tracks=[dict(path=f'track-{n}',title=f'Song {n}',artist='Artist') for n in range(24)]
        self.win=NS(library=tracks,playlists={LIKED:{'tracks':tracks[:5]}},_ya_profiles=Mock(return_value={}))
        self._profiles=None;self._coll_filter=('all',None);self._dirty={'collection'}
        self.c_chips_w=QWidget(self);self.c_chips=QVBoxLayout(self.c_chips_w)
        self.c_search=QLineEdit(self);self.c_count=QLabel(self)
        self.c_list=QListView(self);self.c_list.model_=TrackModel(self.c_list)
        self.c_list.setModel(self.c_list.model_);self.c_list.set_rows=self.c_list.model_.set_rows
        self.stack=QStackedWidget(self);self.pages={'collection':QWidget()};self.stack.addWidget(self.pages['collection'])

    def _on_profiles(self,profiles):self._profiles=profiles


class CollectionRecovery(unittest.TestCase):
    def setUp(self):self.shell=CollectionProbe()
    def tearDown(self):self.shell.deleteLater();APP.sendPostedEvents(None,QEvent.Type.DeferredDelete.value)

    def test_optional_analysis_failure_does_not_hide_full_collection(self):
        self.shell.win._ya_profiles.side_effect=OSError('temporary analysis cache unavailable')
        self.shell._rebuild('collection')
        self.assertEqual(self.shell.c_list.model_.rowCount(),24)
        self.assertEqual(len(self.shell.win.library),24)
        self.assertEqual(self.shell._profiles,{})
        self.assertNotIn('collection',self.shell._dirty)

    def test_failed_first_build_remains_retryable_on_return(self):
        real=self.shell._fill_collection
        with patch.object(self.shell,'_fill_collection',side_effect=RuntimeError('transient view failure')):
            with self.assertRaises(RuntimeError):self.shell._rebuild('collection')
        self.assertIn('collection',self.shell._dirty)
        self.shell._rebuild('collection')
        self.assertEqual(self.shell.c_list.model_.rowCount(),24)
        self.assertNotIn('collection',self.shell._dirty)

    def test_filter_empty_result_recovers_to_all_without_touching_library(self):
        self.shell._fill_collection();self.shell._set_coll_filter('liked',None)
        self.assertEqual(self.shell.c_list.model_.rowCount(),5)
        self.shell.c_search.setText('no matching track');self.shell._coll_apply()
        self.assertEqual(self.shell.c_list.model_.rowCount(),0)
        self.shell.c_search.clear();self.shell._set_coll_filter('all',None)
        self.assertEqual(self.shell.c_list.model_.rowCount(),24)
        self.assertEqual(len(self.shell.win.library),24)

    def test_repeated_rebuilds_release_obsolete_filter_groups(self):
        self.shell._fill_collection();old=self.shell._collection_group
        for _ in range(10):self.shell._fill_collection()
        APP.sendPostedEvents(None,QEvent.Type.DeferredDelete.value)
        self.assertTrue(sip.isdeleted(old))
        self.assertEqual(len(self.shell.c_chips_w.findChildren(QButtonGroup)),1)
        self.assertEqual(self.shell.c_list.model_.rowCount(),24)


if __name__=='__main__':unittest.main(verbosity=2)
