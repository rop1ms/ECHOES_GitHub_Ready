"""Offline regression checks for navigation, incremental chats and feed ranking."""
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout, QStackedWidget
from net_store import Store
from net_page import NetPage, avatar_pm, _AVATARS
from yamusic_theme import Page
from net_perf import batch_updates
import ui_anim

APP = QApplication.instance() or QApplication([])


class ChatHarness(QWidget):
    _fill_chat = NetPage._fill_chat

    def __init__(self, store):
        super().__init__()
        self.node = NS(store=store)
        self._chat_peer = "friend"
        self.chat_view = Page(self)
        self.chat_head = QWidget(self)
        self.built = []

    def _chat_header(self):
        pass

    def _update_badge(self):
        pass

    def _bubble(self, m):
        self.built.append(m["id"])
        row = QWidget()
        QVBoxLayout(row).addWidget(QLabel(m["text"]))
        return row


class NetUiPerformance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "net.db")

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def message(self, mid, **kwargs):
        self.store.add_message(mid, "friend", "in", time.time(), mid, **kwargs)

    def test_chat_appends_only_new_bubble_and_keeps_existing_widgets(self):
        for n in range(150):
            self.message(str(n))
        view = ChatHarness(self.store)
        view._fill_chat()
        first = view._chat_rows["0"][1]
        view.built.clear()
        for _ in range(5):
            view._fill_chat()
        self.assertEqual(view.built, [])
        self.message("new")
        view._fill_chat()
        self.assertEqual(view.built, ["new"])
        self.assertIs(view._chat_rows["0"][1], first)
        self.assertEqual(self.store.unread(), {})
        view.deleteLater()

    def test_delivery_status_rebuilds_only_changed_bubble(self):
        self.message("a")
        self.store.add_message("b", "friend", "out", time.time(), "b")
        view = ChatHarness(self.store)
        view._fill_chat()
        view.built.clear()
        self.store.mark_delivered("b")
        view._fill_chat()
        self.assertEqual(view.built, ["b"])
        view.deleteLater()

    def test_capsule_reveals_without_rebuilding_other_messages(self):
        self.message("a")
        unlock = time.time() + 60
        self.message("capsule", extra={"kind": "capsule", "unlock": unlock})
        view = ChatHarness(self.store)
        view._fill_chat()
        self.assertEqual(view._capsule_until, unlock)
        view.built.clear()
        with patch("net_page.time.time", return_value=unlock + 1):
            view._fill_chat()
        self.assertEqual(view.built, ["capsule"])
        self.assertEqual(view._capsule_until, 0)
        view.deleteLater()

    def test_hot_ranking_matches_old_algorithm_in_two_queries(self):
        for n in range(30):
            pid = str(n)
            self.store.put_post({"id": pid, "author": "friend", "ts": time.time() - n * 30, "text": pid})
            for voter in range(4):
                self.store.put_vote({"post": pid, "voter": str(voter), "val": -1 if voter == 1 else 1, "ts": 1})
            self.store.put_comment({"id": "c" + pid, "post": pid, "author": "friend", "ts": 1, "text": "hi"})
        ids = [str(n) for n in range(30)]
        expected = {pid: [self.store.score(pid)[0] - self.store.score(pid)[1], len(self.store.comments(pid))] for pid in ids}
        with patch.object(self.store, "q", wraps=self.store.q) as query:
            self.assertEqual(self.store.post_activity(ids), expected)
        self.assertEqual(query.call_count, 2)

    def test_outgoing_capsule_header_updates_at_unlock(self):
        unlock = time.time() + 60
        self.store.add_message("capsule", "friend", "out", time.time(), "secret",
                               extra={"kind": "capsule", "unlock": unlock})
        view = ChatHarness(self.store)
        view._fill_chat()
        view.built.clear()
        self.assertEqual(view._capsule_until, unlock)
        with patch("net_page.time.time", return_value=unlock + 1):
            view._fill_chat()
        self.assertEqual(view.built, ["capsule"])
        self.assertEqual(view._capsule_until, 0)
        view.deleteLater()

    def test_navigation_reuses_view_until_event_changes_version(self):
        calls = []
        view = NS(node=object(), _data_ver=1, _fill_feed=lambda: calls.append("feed"))
        NetPage._refresh_tab(view, "feed", force=False)
        for _ in range(100):
            NetPage._refresh_tab(view, "feed", force=False)
        self.assertEqual(calls, ["feed"])
        view._data_ver += 1
        NetPage._refresh_tab(view, "feed", force=False)
        self.assertEqual(calls, ["feed", "feed"])

    def test_feed_reuses_cards_and_rebuilds_only_changed_post(self):
        self.store.upsert_contact("friend", name="Friend", status="friend")
        posts = [{"id": str(n), "author": "me", "ts": time.time(), "text": str(n)} for n in range(5)]
        class Feed(QWidget):
            _fill_feed = NetPage._fill_feed
            _mod_post = NetPage._mod_post
            _discard_feed_cards = NetPage._discard_feed_cards
            # виртуальная лента: места постов и карточки рядом с экраном
            FEED_FIRST = NetPage.FEED_FIRST
            _feed_estimate = NetPage._feed_estimate
            _feed_signature = NetPage._feed_signature
            _feed_put = NetPage._feed_put
            _feed_drop = NetPage._feed_drop
            _feed_window_soon = NetPage._feed_window_soon
            _feed_window = NetPage._feed_window
            eventFilter = NetPage.eventFilter
            def __init__(self, store):
                super().__init__()
                self.node = NS(store=store, me=NS(onion="me"))
                self.shell = NS(current_track=lambda: getattr(self, "track", None))
                self.feed_page = Page(self)
                self._feed_sort = "new"
                self._open_threads, self._revealed, self._reply_to = set(), set(), {}
                self.built = []
            def _composer(self): return QWidget()
            def _type_row(self, page): pass
            def _sorted_posts(self): return posts
            def _type_ok(self, p): return True
            def _fill_feed_side(self): pass
            def _post_card(self, p):
                self.built.append(p["id"])
                return QLabel(p["text"])
        view = Feed(self.store)
        # Bypass the unrelated weekly-challenge module in this focused fixture.
        view._feed_type = "post"
        view._fill_feed()
        first = view._feed_cards["0"][1]
        view.built.clear()
        view._fill_feed()
        self.assertEqual(view.built, [])
        self.store.put_react({"post": "1", "who": "friend", "kind": "emo", "val": "🔥", "ts": 1})
        view._fill_feed()
        self.assertEqual(view.built, ["1"])
        self.assertIs(view._feed_cards["0"][1], first)
        view.built.clear()
        view._open_threads.add("2")
        view._fill_feed()
        self.assertEqual(view.built, ["2"])
        view.built.clear()
        view.track = {"artist": "Artist", "title": "Song"}
        view._fill_feed()
        self.assertEqual(view.built, ["2"])
        view.built.clear()
        view._data_ver = 1
        view._fill_feed()
        self.assertEqual(view.built, ["2"])
        posts[3]["orig"] = {"id": "nested-quiz", "kind": "quiz"}
        view._fill_feed()
        view.built.clear()
        view._revealed.add("nested-quiz")
        view._fill_feed()
        self.assertEqual(view.built, ["3"])
        view.built.clear()
        view._mod_shown = {"nested-post"}
        view._fill_feed()
        self.assertEqual(view.built, ["3"])
        with patch.object(view, "_composer", side_effect=ValueError("fixture")):
            with self.assertRaises(ValueError):
                view._fill_feed()
        self.assertNotIn("_feed_detached", view.__dict__)
        self.assertNotIn("_feed_cards", view.__dict__)
        self.assertTrue(view.feed_page.updatesEnabled())
        view.built.clear()
        view._fill_feed()
        self.assertEqual(view.built, ["0", "1", "2", "3", "4"])
        view.deleteLater()

    def test_network_transition_has_no_screenshots_or_overlay(self):
        stack = QStackedWidget()
        a, b = QWidget(), QWidget()
        b.setProperty("echoes_instant_navigation", True)
        stack.addWidget(a); stack.addWidget(b)
        stack.resize(800, 600); stack.show()
        with patch.object(ui_anim, "_grab_area", side_effect=AssertionError("snapshot blocks input")):
            ui_anim.switch(stack, b)
            self.assertIs(stack.currentWidget(), b)
            ui_anim.switch(stack, a)
        self.assertNotIn(id(stack), ui_anim._OVERLAYS)
        stack.close()

    def test_navigation_cache_is_specific_to_chat_and_profile(self):
        rendered = []
        view = NS(node=object(), _data_ver=1, _chat_peer="alice", _profile_onion="alice",
                  _fill_chat_list=lambda: None, _fill_chat=lambda: rendered.append(view._chat_peer),
                  _fill_profile=lambda: rendered.append(view._profile_onion or view._profile_npub))
        NetPage._refresh_tab(view, "chats", force=False)
        view._chat_peer = "bob"
        NetPage._refresh_tab(view, "chats", force=False)
        NetPage._refresh_tab(view, "profile", force=False)
        view._profile_onion = "bob"
        NetPage._refresh_tab(view, "profile", force=False)
        view._profile_onion, view._profile_npub = None, "public-key"
        NetPage._refresh_tab(view, "profile", force=False)
        self.assertEqual(rendered, ["alice", "bob", "alice", "bob", "public-key"])

    def test_batch_restores_updates_after_error(self):
        widget = QWidget()
        class Handler:
            view = widget
            @batch_updates("view")
            def fill(self):
                self.was_disabled = not self.view.updatesEnabled()
                raise ValueError("fixture")
        handler = Handler()
        with self.assertRaises(ValueError):
            handler.fill()
        self.assertTrue(handler.was_disabled)
        self.assertTrue(widget.updatesEnabled())

    def test_avatar_cache_invalidates_photo_and_keeps_returned_pixmap_safe(self):
        from PyQt6.QtGui import QImage, QColor
        import net_frames
        photo = Path(self.temp.name) / "avatar.png"
        img = QImage(160, 160, QImage.Format.Format_RGB32)
        img.fill(QColor("red")); img.save(str(photo))
        _AVATARS.clear()
        with patch.object(net_frames, "RESOLVER", None):
            a = avatar_pm("friend", "Name", 40, photo=str(photo))
            b = avatar_pm("friend", "Name", 40, photo=str(photo))
            self.assertEqual(a.cacheKey(), b.cacheKey())
            b.fill(Qt.GlobalColor.transparent)
            self.assertFalse(a.toImage() == b.toImage())
            img.fill(QColor("blue")); img.save(str(photo))
            os.utime(photo, (time.time() + 2, time.time() + 2))
            c = avatar_pm("friend", "Name", 40, photo=str(photo))
            self.assertNotEqual(a.toImage(), c.toImage())


if __name__ == "__main__":
    unittest.main()
