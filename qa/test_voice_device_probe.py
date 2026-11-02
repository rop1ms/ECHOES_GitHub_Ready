"""Only native-discovery isolation, cleanup and the lightweight settings card."""
import builtins
import json
import os
from pathlib import Path
import sys
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from PyQt6.QtWidgets import QApplication,QWidget
from net_page import NetPage,_VoiceDevices
import voice_devices

APP=QApplication.instance() or QApplication([])


class DeviceProbe(unittest.TestCase):
    def process(self,payload):
        process=Mock();process.communicate.return_value=(json.dumps(payload).encode(),b'')
        process.returncode=0;process.poll.return_value=0
        return process

    def test_direct_query_preserves_indices_dedup_and_full_duplex_devices(self):
        hardware=[dict(name='Mic',max_input_channels=1),dict(name='Mic',max_input_channels=2),
                  dict(name='Headset',max_input_channels=1,max_output_channels=2)]
        with patch.dict(sys.modules,sounddevice=NS(query_devices=lambda:hardware)):
            self.assertEqual(voice_devices.devices(),([(0,'Mic'),(2,'Headset')],[(2,'Headset')]))

    def test_isolated_result_keeps_saved_portaudio_ids(self):
        process=self.process([[[93,'Mic']],[[48,'Speakers']]])
        with patch.object(voice_devices.subprocess,'Popen',return_value=process) as launch:
            self.assertEqual(voice_devices.isolated_devices(),([(93,'Mic')],[(48,'Speakers')]))
        command=launch.call_args.args[0]
        self.assertEqual(command[0],sys.executable);self.assertIn('--probe',command)
        self.assertEqual(voice_devices._PROBES,set())

    def test_cancel_and_timeout_terminate_child_and_release_registry(self):
        for cancel,timeout in ((threading.Event(),0),(threading.Event(),8)):
            if timeout:cancel.set()
            process=self.process([[],[]])
            with patch.object(voice_devices.subprocess,'Popen',return_value=process):
                self.assertEqual(voice_devices.isolated_devices(cancel,timeout),([],[]))
            process.kill.assert_called_once();self.assertEqual(voice_devices._PROBES,set())

    def test_desktop_pythonw_launcher_uses_hidden_console_interpreter_for_json(self):
        process=self.process([[],[]])
        with patch.object(voice_devices.sys,'executable',str(Path(sys.executable).with_name('pythonw.exe'))),\
             patch.object(Path,'exists',return_value=True),\
             patch.object(voice_devices.subprocess,'Popen',return_value=process) as launch:
            voice_devices.isolated_devices()
        self.assertEqual(Path(launch.call_args.args[0][0]).name,'python.exe')
        self.assertIn('creationflags',launch.call_args.kwargs)

    def test_malformed_or_failed_child_does_not_break_settings(self):
        for payload,returncode in (({'bad':True},0),([[],[]],1)):
            process=self.process(payload);process.returncode=returncode
            with patch.object(voice_devices.subprocess,'Popen',return_value=process):
                self.assertEqual(voice_devices.isolated_devices(),([],[]))

    def test_exit_cleanup_terminates_outstanding_probes(self):
        process=self.process([[],[]]);process.poll.return_value=None
        voice_devices._PROBES.add(process)
        try:voice_devices._stop_probes();process.kill.assert_called_once()
        finally:voice_devices._PROBES.discard(process)

    def test_building_voice_card_never_imports_voice_dsp_or_codecs(self):
        owner=QWidget();owner.win=NS(settings={'language':'en'})
        owner.node=NS(settings={},save_settings=Mock());owner._voice=None
        original=builtins.__import__
        def reject(name,*args,**kwargs):
            if name in ('net_voice','av'):raise AssertionError('codec import during page construction')
            return original(name,*args,**kwargs)
        try:
            with patch.object(builtins,'__import__',side_effect=reject),patch.object(_VoiceDevices,'bind'):
                card=NetPage._voice_card(owner)
                self.assertGreater(card.layout().count(),4)
                owner.node.save_settings.assert_not_called()
                card.deleteLater()
        finally:owner.deleteLater();APP.processEvents()


if __name__=='__main__':unittest.main(verbosity=2)
