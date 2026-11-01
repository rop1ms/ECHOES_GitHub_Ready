"""Offline voice regressions: routing, speech boundaries, Qt lifecycle and editor.

No microphone, network, real settings or model download is used.
"""
import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import numpy as np
from PyQt6.QtCore import QThread
from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtWidgets import QApplication, QComboBox, QSlider, QWidget

from voice_audio import SR, SpeechBuffer, confident_text
from voice_commands import normalize, parse, route, strip_wake, validate_commands
import ya_voice
from voice_editor import CommandsDialog

app = QApplication.instance() or QApplication([])
if os.name == "nt" and not QFontDatabase.families():
    # Windows' offscreen Qt plugin doesn't enumerate installed system fonts.
    for filename in ("segoeui.ttf", "segoeuib.ttf"):
        font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / filename
        if font.exists():
            QFontDatabase.addApplicationFont(str(font))
    app.setFont(QFont("Segoe UI", 10))


def rule(phrase="evening mode", **changes):
    return {"id": "evening", "name": "Evening", "on": True, "phrases": [phrase],
            "steps": [{"a": "volume", "v": 35}, {"a": "wait", "v": 1}, {"a": "wave"}], **changes}


class RoutingTests(unittest.TestCase):
    def test_unknown_speech_is_inert(self):
        for text in ("", "Korn", "спасибо за просмотр", "we should go now", "это эхо", "волноваться не стоит"):
            with self.subTest(text=text):
                self.assertEqual(parse(text), ("", ""))

    def test_wake_requires_a_complete_first_token(self):
        for text in ("эхо следующий", "эко пауза", "echo next", "echoesxyz next", "ну эхос следующий", "the echoes pause"):
            with self.subTest(text=text):
                self.assertEqual(route(text, require_wake=True), ("", ""))
        for text in ("Эхос, следующий!", "ECHOES, next track.", "Hey ECHOES next", "эй эхос следующий", "Эхо с следующей."):
            self.assertEqual(route(text, require_wake=True), ("next", ""))

    def test_full_command_match(self):
        for text in ("next song is a great song", "следующий раз поговорим", "пауза в разговоре", "louder than before", "впереди нас ждут"):
            self.assertEqual(route("ECHOES " + text, require_wake=True), ("", ""))

    def test_russian_english_equivalents(self):
        examples = {
            "next": ("следующий трек", "next track"), "prev": ("предыдущая песня", "previous song"),
            "pause": ("выключи музыку", "pause music"), "play": ("продолжи воспроизведение", "resume playback"),
            "vol_up": ("сделай громче", "turn it up"), "vol_down": ("убавь громкость", "volume down"),
            "mute": ("выключи звук", "mute"), "unmute": ("верни звук", "unmute"),
            "like": ("мне нравится", "like this track"), "mini": ("открой мини-плеер", "open mini player"),
            "wave": ("включи мою волну", "play my wave"), "shuffle": ("перемешай музыку", "shuffle music"),
        }
        for action, texts in examples.items():
            for text in texts:
                with self.subTest(action=action, text=text):
                    self.assertEqual(parse(text), (action, ""))

    def test_explicit_search_and_volume(self):
        self.assertEqual(parse("поставь Korn"), ("find", "korn"))
        self.assertEqual(parse("play playlist Road"), ("playlist", "road"))
        self.assertEqual(parse("включи плейлист Дорога"), ("playlist", "дорога"))
        self.assertEqual(parse("set volume to 30 percent"), ("volume", "30"))
        self.assertEqual(parse("громкость 100 процентов"), ("volume", "100"))
        self.assertEqual(parse("volume 101"), ("", ""))

    def test_custom_aliases_are_exact_and_wake_protected(self):
        rules = [rule(phrases=["evening mode", "Мой вечер"])]
        self.assertEqual(route("Эхос, МОЙ вечер!", rules, require_wake=True), ("scenario", "evening"))
        self.assertEqual(route("evening mode", rules), ("scenario", "evening"))
        self.assertEqual(route("evening mode", rules, require_wake=True), ("", ""))
        self.assertEqual(route("evening mode please", rules), ("", ""))
        rules[0]["on"] = False
        self.assertEqual(route("evening mode", rules), ("", ""))

    def test_scenario_validation_copies_and_rejects_collisions(self):
        original = rule()
        clean = validate_commands([original])
        clean[0]["steps"][0]["v"] = 5
        self.assertEqual(original["steps"][0]["v"], 35)
        for items, key in (([rule(), rule(id="second", phrase="EVENING, mode!")], "duplicate_phrase"),
                           ([rule("pause")], "builtin_phrase"), ([rule("Эхос, мой вечер")], "omit_wake"),
                           ([rule(steps=[])], "steps_required"), ([rule(steps=[{"a": "shell"}])], "invalid_step"),
                           ([rule(steps=[{"a": "wait", "v": 61}])], "invalid_number"),
                           ([rule(steps=[{"a": "playlist", "name": ""}])], "argument_required")):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, key):
                validate_commands(items)


class AudioTests(unittest.TestCase):
    def test_silence_and_brief_click_never_form_a_command(self):
        gate = SpeechBuffer()
        for _ in range(100):
            self.assertIsNone(gate.feed(np.zeros(320, np.float32)))
        gate.feed(np.ones(640, np.float32) * .8)  # 40 ms impulse
        for _ in range(20):
            self.assertIsNone(gate.feed(np.zeros(320, np.float32)))
        self.assertIsNone(gate.finish())

    def test_endpoint_is_short_and_preserves_preroll(self):
        gate = SpeechBuffer()
        for _ in range(8):
            gate.feed(np.zeros(320, np.float32))
        for _ in range(15):
            self.assertIsNone(gate.feed(np.ones(320, np.float32) * .04))
        result = None
        silence = 0
        while result is None and silence < 30:
            result = gate.feed(np.zeros(320, np.float32))
            silence += 1
        self.assertLessEqual(silence * .02, .34)
        self.assertIsNotNone(result)
        self.assertTrue(np.all(result[:320] == 0))
        self.assertLessEqual(result.size, int(SR * .6))

    def test_variable_hardware_block_sizes_and_bounded_recording(self):
        gate = SpeechBuffer()
        result = None
        for i in range(1000):
            result = gate.feed(np.ones((111, 277, 512)[i % 3], np.float32) * .04)
            if result is not None:
                break
        self.assertIsNotNone(result)
        self.assertLess(result.size, int(SR * 7.04))

    def test_low_level_noise_and_nonfinite_samples_are_ignored(self):
        gate = SpeechBuffer()
        rng = np.random.default_rng(2)
        for _ in range(100):
            self.assertIsNone(gate.feed(rng.normal(0, .001, 320).astype(np.float32)))
        self.assertIsNone(gate.feed(np.full(320, np.nan, np.float32)))
        self.assertIsNone(gate.finish())

    def test_doubtful_segment_cannot_leave_an_executable_suffix(self):
        good = NS(text="ECHOES next", no_speech_prob=.05, avg_logprob=-.3, compression_ratio=1.0)
        bad = NS(text="ordinary conversation", no_speech_prob=.8, avg_logprob=-1.2, compression_ratio=1.0)
        self.assertEqual(confident_text([good]), "ECHOES next")
        self.assertEqual(confident_text([bad, good]), "")
        nan = NS(text="ECHOES next", no_speech_prob=float("nan"), avg_logprob=-.3, compression_ratio=1.0)
        self.assertEqual(confident_text([nan]), "")
        borderline = NS(text="ECHOES next", no_speech_prob=.4, avg_logprob=-.8, compression_ratio=1.0)
        self.assertNotEqual(confident_text([borderline]), "")
        self.assertEqual(confident_text([borderline], strict=True), "")

    def test_transcription_uses_requested_language_and_no_sampling_retries(self):
        segment = NS(text="ECHOES next", no_speech_prob=.1, avg_logprob=-.2, compression_ratio=1)
        calls = []
        model = NS(transcribe=lambda audio, **kwargs: (calls.append(kwargs) or iter([segment]), None))
        with patch.object(ya_voice, "load_model", return_value=model):
            self.assertEqual(ya_voice.transcribe(np.zeros(16000, np.float32), "en"), "ECHOES next")
        self.assertEqual(calls[0]["language"], "en")
        self.assertEqual(calls[0]["temperature"], 0)
        self.assertTrue(calls[0]["vad_filter"])
        self.assertNotIn("next", calls[0]["initial_prompt"].lower())
        self.assertNotIn("pause", calls[0]["initial_prompt"].lower())
        self.assertEqual(calls[0]["max_new_tokens"], 48)


class FakeWindow(QWidget):
    def __init__(self, language="en"):
        super().__init__()
        self.settings = {"language": language}
        self.calls = []
        self.saved = 0
        self.vol = QSlider(self)
        self.vol.setRange(0, 100)
        self.vol.setValue(60)
        self.playlists = {"Road": []}
        self._ya = NS(user_playlists=lambda: ["Road"])
        self.engine = NS(is_playing=lambda: True)
        self.next_track = lambda: self.calls.append(("next", QThread.currentThread() == app.thread()))
        self._toast = lambda text: None

    def save_settings(self):
        self.saved += 1


def pump_until(predicate, timeout=2):
    end = time.monotonic() + timeout
    while not predicate() and time.monotonic() < end:
        app.processEvents()
        time.sleep(.005)
    app.processEvents()
    return bool(predicate())


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.win = FakeWindow()
        self.listener = ya_voice.get(self.win)

    def tearDown(self):
        self.listener.close()
        if self.listener._worker is not None:
            self.listener._worker.join(timeout=2)
        self.win.close()

    def _feed_command(self):
        for _ in range(15):
            self.listener._jobs.put(("audio", self.listener._generation, np.ones(320, np.float32) * .04, False))
        for _ in range(17):
            self.listener._jobs.put(("audio", self.listener._generation, np.zeros(320, np.float32), False))

    def test_background_recognition_executes_only_on_gui_thread(self):
        with patch.object(ya_voice, "load_model", return_value=object()), \
             patch.object(self.listener, "_ensure_stream", return_value=True), \
             patch.object(ya_voice, "transcribe", return_value="ECHOES next"):
            self.listener.set_always(True)
            self.assertTrue(pump_until(lambda: self.listener._ready))
            self._feed_command()
            self.assertTrue(pump_until(lambda: bool(self.win.calls)))
        self.assertEqual(self.win.calls, [("next", True)])

    def test_disabling_during_decode_discards_late_command(self):
        decoding, release = threading.Event(), threading.Event()

        def delayed(*args):
            decoding.set()
            release.wait(2)
            return "ECHOES next"

        with patch.object(ya_voice, "load_model", return_value=object()), \
             patch.object(self.listener, "_ensure_stream", return_value=True), \
             patch.object(ya_voice, "transcribe", side_effect=delayed):
            self.listener.set_always(True)
            self.assertTrue(pump_until(lambda: self.listener._ready))
            self._feed_command()
            self.assertTrue(decoding.wait(2))
            self.listener.set_always(False)
            release.set()
            self.assertTrue(pump_until(lambda: not self.listener.busy))
        self.assertEqual(self.win.calls, [])
        self.assertFalse(self.listener._ready)

    def test_ptt_deadline_starts_after_model_is_ready(self):
        release = threading.Event()
        with patch.object(ya_voice, "load_model", side_effect=lambda: (release.wait(2), object())[1]), \
             patch.object(self.listener, "_ensure_stream", return_value=True):
            self.listener.push_to_talk()
            self.assertEqual(self.listener.ptt_until, 0)
            release.set()
            self.assertTrue(pump_until(lambda: self.listener._ready))
            self.assertGreater(self.listener.ptt_until, time.monotonic() + 4)
            self.listener._ptt_end(self.listener._generation)
            self.assertTrue(pump_until(lambda: not self.listener._requested_ptt))
        self.assertEqual(self.win.calls, [])
        self.assertFalse(self.listener._ready)

    def test_restart_during_segmentation_cannot_stick_the_new_session_busy(self):
        entered, release = threading.Event(), threading.Event()

        class BlockingGate:
            def __init__(self, *args):
                pass

            def feed(self, audio):
                entered.set()
                release.wait(2)
                return np.ones(6400, np.float32) * .04

            def reset(self):
                pass

        with patch.object(ya_voice, "SpeechBuffer", BlockingGate), \
             patch.object(ya_voice, "load_model", return_value=object()), \
             patch.object(self.listener, "_ensure_stream", return_value=True), \
             patch.object(ya_voice, "transcribe", return_value="ECHOES next"):
            self.listener.set_always(True)
            self.assertTrue(pump_until(lambda: self.listener._ready))
            self.listener._jobs.put(("audio", self.listener._generation, np.ones(320, np.float32), False))
            self.assertTrue(entered.wait(2))
            self.listener.restart()  # the old gate.feed is still blocked
            release.set()
            self.assertTrue(pump_until(lambda: self.listener._ready))
            self.assertFalse(self.listener.busy)
            self.assertFalse(self.win.calls)
            self.listener._jobs.put(("audio", self.listener._generation, np.ones(320, np.float32), False))
            self.assertTrue(pump_until(lambda: bool(self.win.calls)))
        self.assertEqual(self.win.calls, [("next", True)])

    def test_busy_ownership_from_old_session_is_inert(self):
        old = self.listener._generation
        self.listener._generation += 1
        self.listener._busy_generation = old
        self.assertFalse(self.listener.busy)

    def test_old_generation_result_never_executes(self):
        self.listener._generation = 5
        self.listener._done(4, "ECHOES next", "next", "", False)
        self.assertFalse(self.win.calls)

    def test_mute_is_idempotent_and_unmute_restores_volume(self):
        ya_voice.execute(self.win, "mute", "")
        ya_voice.execute(self.win, "mute", "")
        self.assertEqual(self.win.vol.value(), 0)
        ya_voice.execute(self.win, "unmute", "")
        self.assertEqual(self.win.vol.value(), 60)

    def test_scenario_runs_shared_actions_in_order_without_blocking(self):
        calls = []
        engine = NS(run=lambda step: calls.append(step["do"]["a"]) or True)
        import ya_auto
        with patch.object(ya_auto, "get", return_value=engine):
            self.listener.scenario.start(rule(steps=[{"a": "pause"}, {"a": "wait", "v": 0}, {"a": "volume", "v": 20}]))
            self.assertEqual(calls, [])
            self.assertTrue(pump_until(lambda: len(calls) == 2))
        self.assertEqual(calls, ["pause", "volume"])

    def test_failed_scenario_step_stops_following_actions(self):
        calls = []
        import ya_auto
        with patch.object(ya_auto, "get", return_value=NS(run=lambda step: calls.append(step) or False)):
            self.listener.scenario.start(rule(steps=[{"a": "pause"}, {"a": "volume", "v": 20}]))
            self.assertTrue(pump_until(lambda: bool(calls)))
            app.processEvents()
        self.assertEqual(len(calls), 1)
        self.assertFalse(self.listener.scenario.steps)

    def test_cancel_wait_prevents_later_actions(self):
        self.listener.scenario.start(rule(steps=[{"a": "wait", "v": 60}, {"a": "next"}]))
        app.processEvents()
        self.assertTrue(self.listener.scenario.timer.isActive())
        self.listener.scenario.cancel()
        app.processEvents()
        self.assertFalse(self.listener.scenario.timer.isActive())
        self.assertFalse(self.win.calls)


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.win = FakeWindow()
        self.dialog = CommandsDialog(self.win)

    def tearDown(self):
        self.dialog.close()
        self.win.close()

    def test_add_edit_order_save_and_reload(self):
        d = self.dialog
        d.add_command()
        d.name.setText("Evening")
        d.phrases.setPlainText("evening mode\nмой вечер")
        d.add_step()
        combo = d.table.cellWidget(1, 0)
        combo.setCurrentIndex(combo.findData("volume"))
        d.table.cellWidget(1, 1).setValue(35)
        d.table.selectRow(1)
        d.move_step(-1)
        d.save()
        saved = self.win.settings["voice_commands"][0]
        self.assertEqual(saved["steps"], [{"a": "volume", "v": 35}, {"a": "wave"}])
        self.assertEqual(saved["phrases"], ["evening mode", "мой вечер"])
        self.assertEqual(self.win.saved, 1)
        other = CommandsDialog(self.win)
        try:
            self.assertEqual(other.name.text(), "Evening")
            self.assertEqual(other.table.rowCount(), 2)
        finally:
            other.close()

    def test_duplicate_phrases_do_not_overwrite_saved_settings(self):
        d = self.dialog
        d.add_command()
        d.phrases.setPlainText("evening mode")
        d.add_command()
        d.phrases.setPlainText("EVENING, mode")
        d.save()
        self.assertNotIn("voice_commands", self.win.settings)
        self.assertIn("unique", d.status.text())

    def test_switching_and_deleting_commands_preserves_current_edits(self):
        d = self.dialog
        d.add_command()
        d.phrases.setPlainText("first mode")
        d.name.setText("First")
        d.add_command()
        d.name.setText("Second")
        d.phrases.setPlainText("second mode")
        d.list.setCurrentRow(0)
        self.assertEqual(d.phrases.toPlainText(), "first mode")
        d.remove_command()
        self.assertEqual(d.name.text(), "Second")
        d.save()
        self.assertEqual(self.win.settings["voice_commands"][0]["name"], "Second")

    def test_dialog_resizes_without_hidden_footer_and_checks_without_execution(self):
        d = self.dialog
        d.add_command()
        d.phrases.setPlainText("evening mode")
        d.test_phrase.setText("ECHOES evening mode")
        d.check_phrase()
        self.assertIn("does not execute", d.status.text())
        self.assertEqual(self.win.calls, [])
        d.resize(750, 540)
        d.show()
        app.processEvents()
        for button in d.findChildren(QComboBox):
            self.assertGreater(button.width(), 60)
        self.assertLessEqual(d.status.geometry().bottom(), d.status.parentWidget().height())
        self.assertEqual(d.width(), 750)

    def test_english_and_russian_cards_render_without_model(self):
        with patch.object(ya_voice, "model_name", return_value=None):
            for language in ("ru", "en"):
                self.win.settings["language"] = language
                card = ya_voice.settings_card(NS(win=self.win))
                card.resize(760, 300)
                self.assertFalse(card.grab().isNull())
                card.close()


if __name__ == "__main__":
    unittest.main()
