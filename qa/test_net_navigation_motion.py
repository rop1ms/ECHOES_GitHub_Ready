"""Only Echoes.NET tab selection, cold layout and shared transition lifecycle."""
import os
import sys
import unittest
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from PyQt6 import sip
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QApplication,QWidget,QStackedWidget,QScrollArea,QComboBox,
    QVBoxLayout,QHBoxLayout,QPushButton,QButtonGroup,QLabel,QLineEdit)
import ui_anim
from net_page import NetPage,_VoiceDevices

APP=QApplication.instance() or QApplication([])


class Tabs(QWidget):
    TABS=NetPage.TABS
    show_tab=NetPage.show_tab
    def __init__(self):
        super().__init__()
        self.setStyleSheet('QWidget#Glass {background:#293846;} QLabel {color:white;}')
        self.setObjectName('Glass')
        self.resize(1100,720)
        self._tab=None;self.node=object();self._data_ver=0
        self._chat_peer=None;self._profile_onion=None;self._profile_npub=None
        self._filled_ver={};self.builds={};self.tab_btns={};self.tabs={}
        root=QVBoxLayout(self);row=QHBoxLayout();root.addLayout(row)
        group=QButtonGroup(self)
        self.stack=QStackedWidget();root.addWidget(self.stack)
        for key in ('feed','chats','room','friends','net','profile','mods','threads','admin','requests'):
            page=QScrollArea();page.setWidgetResizable(True)
            self.tabs[key]=page;self.stack.addWidget(page)
            if key!='profile':
                button=QPushButton(key);button.setCheckable(True);group.addButton(button)
                self.tab_btns[key]=button;row.addWidget(button)
        self.threads=SimpleNamespace(fill=lambda:self.fill('threads'))
    _refresh_tab=NetPage._refresh_tab
    _refresh_soon=NetPage._refresh_soon
    _EVENT_TABS=NetPage._EVENT_TABS
    def fill(self,key):
        self.builds[key]=self.builds.get(key,0)+1
        body=QWidget();layout=QVBoxLayout(body);layout.addWidget(QLabel(key+' title'))
        editor=QLineEdit();editor.setObjectName('Draft');layout.addWidget(editor)
        for index in range(32):layout.addWidget(QPushButton(f'{key} row {index}'))
        self.tabs[key].setWidget(body)
    def _fill_feed(self):self.fill('feed')
    def _fill_chat_list(self):pass
    def _fill_chat(self):self.fill('chats')
    def _fill_room(self):self.fill('room')
    def _fill_friends(self):self.fill('friends')
    def _fill_net(self):self.fill('net')
    def _fill_profile(self):self.fill('profile')
    def _fill_mods(self):self.fill('mods')
    def _fill_admin(self):self.fill('admin')
    def _fill_requests(self):self.fill('requests')


class NetNavigationMotion(unittest.TestCase):
    def setUp(self):
        self.policy=patch.object(ui_anim,'_WIN',SimpleNamespace(settings={'motion_profile':'balanced'}))
        self.policy.start();self.view=Tabs()
    def tearDown(self):
        for overlay in list(ui_anim._OVERLAYS.values()):
            if not sip.isdeleted(overlay):overlay.finish()
        self.view.close();self.view.deleteLater();APP.processEvents();self.policy.stop()
    def show(self):
        self.view.show_tab('feed');self.view.show();APP.processEvents()
    def prepare(self):
        overlay=ui_anim._OVERLAYS.get(id(self.view.stack))
        if overlay is not None:
            overlay._prepare();overlay.set_t(1.)
        return overlay
    def test_hidden_startup_does_not_capture_or_run_animation(self):
        with patch.object(ui_anim,'_grab_area',side_effect=AssertionError('hidden startup capture')):
            self.view.show_tab('feed');self.view.show_tab('friends')
        self.assertIs(self.view.stack.currentWidget(),self.view.tabs['friends'])
        self.assertNotIn(id(self.view.stack),ui_anim._OVERLAYS)
    def test_cold_net_tabs_have_complete_geometry_and_animate(self):
        self.show()
        for key in ('friends','chats','room','net','mods','threads','requests','admin','profile'):
            self.view.show_tab(key);overlay=self.prepare()
            self.assertIsNotNone(overlay,key)
            page=self.view.tabs[key]
            self.assertIs(self.view.stack.currentWidget(),page)
            self.assertGreater(page.widget().width(),900,key)
            self.assertGreater(page.widget().findChild(QLineEdit).width(),850,key)
            self.assertEqual(overlay.size(),self.view.stack.size())
            self.assertFalse(overlay.new.isNull())
            overlay.finish()
    def test_rapid_net_navigation_keeps_one_overlay_and_final_selection(self):
        self.show()
        keys=('friends','chats','room','feed')
        with patch.object(ui_anim,'_grab_area',wraps=ui_anim._grab_area) as capture:
            for index in range(28):self.view.show_tab(keys[index%4])
            self.assertEqual(len(ui_anim._OVERLAYS),1)
            # Incoming snapshots are not generated for superseded requests.
            self.assertLessEqual(capture.call_count,2)
            overlay=self.prepare()
            self.assertLessEqual(capture.call_count,3)
            self.assertFalse(overlay.new.isNull())
        self.assertEqual(self.view._tab,'feed')
        self.assertEqual(self.view.builds,{'feed':1,'friends':1,'chats':1,'room':1})
    def test_tabs_keep_drafts_scroll_and_widgets_on_return(self):
        self.show();self.view.show_tab('chats');self.prepare().finish();APP.processEvents()
        page=self.view.tabs['chats'];body=page.widget();editor=body.findChild(QLineEdit)
        editor.setText('Unsent station suggestion');page.verticalScrollBar().setValue(350)
        saved=page.verticalScrollBar().value()
        self.view.show_tab('friends');self.prepare().finish()
        self.view.show_tab('chats');self.prepare().finish();APP.processEvents()
        self.assertIs(page.widget(),body)
        self.assertEqual(editor.text(),'Unsent station suggestion')
        self.assertEqual(page.verticalScrollBar().value(),saved)
        self.assertEqual(self.view.builds['chats'],1)
    def test_same_tab_does_not_start_another_transition_or_rebuild(self):
        self.show()
        with patch.object(ui_anim,'_grab_area',side_effect=AssertionError('same tab capture')):
            for _ in range(10):self.view.show_tab('feed')
        self.assertEqual(self.view.builds,{'feed':1})
        self.assertNotIn(id(self.view.stack),ui_anim._OVERLAYS)
    def test_stale_tab_shows_at_once_and_refreshes_after_transition(self):
        """A network event marks a filled tab stale: the click shows it as is, the rebuild comes after the
        slide; events only touch the tabs that show their data."""
        self.show();self.view.show_tab('friends');self.view.show_tab('feed')
        self.assertEqual(self.view.builds,{'feed':1,'friends':1})
        self.view._tab_ver={'friends':1}                       # e.g. a presence event
        self.view.show_tab('friends')
        self.assertEqual(self.view.builds['friends'],1,'no rebuild before the tab is shown')
        end=time.perf_counter()+0.6
        while time.perf_counter()<end:APP.processEvents();time.sleep(0.005)
        self.assertEqual(self.view.builds['friends'],2,'refreshed after the transition')
        self.view.show_tab('feed')
        end=time.perf_counter()+0.6
        while time.perf_counter()<end:APP.processEvents();time.sleep(0.005)
        self.assertEqual(self.view.builds['feed'],1,'feed was not touched by the event')
        self.assertEqual(NetPage._EVENT_TABS['presence'],('friends','chats','net','room'))
    def test_profile_selection_and_tab_direction_follow_navigation(self):
        self.show();self.view.show_tab('friends');overlay=self.prepare()
        self.assertEqual(overlay.dir,1);overlay.finish()
        self.view.show_tab('room');overlay=self.prepare()
        self.assertEqual(overlay.dir,1);overlay.finish()
        self.view.show_tab('feed');overlay=self.prepare()
        self.assertEqual(overlay.dir,-1);overlay.finish()
        self.view.show_tab('profile');self.prepare().finish()
        self.assertFalse(any(button.isChecked() for button in self.view.tab_btns.values()))


class VoiceDeviceDiscovery(unittest.TestCase):
    def setUp(self):
        self.owner=QWidget();self.owner.win=SimpleNamespace(settings={'language':'en'})
        self.discovery=_VoiceDevices(self.owner);self.release=threading.Event();self.entered=threading.Event()
        self.calls=[];self.workers=[]
    def tearDown(self):
        self.release.set()
        for worker in self.workers:worker.join(1)
        if not sip.isdeleted(self.owner):sip.delete(self.owner)
        APP.processEvents()
    def devices(self,**kwargs):
        self.calls.append(threading.get_ident());self.workers.append(threading.current_thread())
        self.entered.set();self.release.wait(1)
        return ([(93,'A very long microphone name '*8)],[(48,'Output')])
    def wait(self,condition):
        until=time.monotonic()+2
        while not condition() and time.monotonic()<until:APP.processEvents();time.sleep(.001)
        self.assertTrue(condition())
    def test_query_is_off_gui_deduplicated_and_never_saves_selection_on_population(self):
        import voice_devices
        a,b=QComboBox(self.owner),QComboBox(self.owner);events=[]
        a.currentIndexChanged.connect(events.append);b.currentIndexChanged.connect(events.append)
        with patch.object(voice_devices,'isolated_devices',self.devices):
            self.discovery.bind(a,'voice_in',93);self.discovery.bind(b,'voice_out',48)
            self.assertEqual(a.currentData(),93);self.assertEqual(b.currentData(),48)
            self.assertFalse(a.isEnabled());self.assertTrue(self.entered.wait(1))
            self.assertNotEqual(self.calls[0],threading.get_ident())
            before=a.minimumSizeHint().width();self.release.set()
            self.wait(lambda:self.discovery.cache is not None)
            self.assertEqual(len(self.calls),1);self.assertEqual(events,[])
            self.assertEqual(a.currentData(),93);self.assertEqual(b.currentData(),48)
            self.assertTrue(a.isEnabled());self.assertEqual(a.minimumSizeHint().width(),before)
            replacement=QComboBox(self.owner);self.discovery.bind(replacement,'voice_in',93)
            self.assertEqual(replacement.currentData(),93);self.assertEqual(len(self.calls),1)
    def test_dead_combo_is_skipped_and_unavailable_saved_id_is_preserved(self):
        import voice_devices
        a,b=QComboBox(self.owner),QComboBox(self.owner)
        with patch.object(voice_devices,'isolated_devices',self.devices):
            self.discovery.bind(a,'voice_in',93);self.discovery.bind(b,'voice_out',999)
            self.assertTrue(self.entered.wait(1));sip.delete(a);self.release.set()
            self.wait(lambda:self.discovery.cache is not None)
            self.assertEqual(b.currentData(),999);self.assertTrue(b.isEnabled())
            self.assertEqual(self.discovery.pending,[])
    def test_late_worker_result_after_page_deletion_does_not_touch_qt(self):
        import voice_devices
        errors=[]
        with patch.object(voice_devices,'isolated_devices',self.devices),patch.object(threading,'excepthook',errors.append):
            self.discovery.bind(QComboBox(self.owner),'voice_in',None)
            self.assertTrue(self.entered.wait(1));sip.delete(self.owner);self.release.set()
            for worker in self.workers:worker.join(1)
            APP.processEvents();self.assertEqual(errors,[])


if __name__=='__main__':unittest.main(verbosity=2)
