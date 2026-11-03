"""Echoes.NET virtual feed and painted reaction bar.

* the feed keeps a cheap place for every post and builds real cards only near the screen;
* scrolling builds the cards that come into view and drops the far ones, keeping their height
  (nothing jumps); unchanged cards are reused across refills;
* the reaction bar is one painted widget: a click on a chip reacts / un-reacts, «+» opens the picker."""
import os
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from PyQt6.QtCore import QPointF, Qt  # noqa: E402
from PyQt6.QtGui import QMouseEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel, QWidget  # noqa: E402

APP = QApplication.instance() or QApplication([])
from yamusic_theme import Page  # noqa: E402
from net_page import NetPage, _FeedSlot  # noqa: E402


class Store:
    def __init__(self):
        self.reacts_ = {}

    def votes(self, pid): return []
    def reacts(self, pid): return self.reacts_.get(pid, [])
    def comments(self, pid): return []
    def contacts(self, kind): return [{"onion": "f"}]


def pump(sec=0.05):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        APP.processEvents()
        time.sleep(0.002)


class Feed(QWidget):
    _fill_feed = NetPage._fill_feed
    _mod_post = NetPage._mod_post
    _discard_feed_cards = NetPage._discard_feed_cards
    FEED_FIRST = NetPage.FEED_FIRST
    _feed_estimate = NetPage._feed_estimate
    _feed_signature = NetPage._feed_signature
    _feed_put = NetPage._feed_put
    _feed_drop = NetPage._feed_drop
    _feed_window_soon = NetPage._feed_window_soon
    _feed_window = NetPage._feed_window
    _feed_places = NetPage._feed_places
    eventFilter = NetPage.eventFilter

    def __init__(self, posts):
        super().__init__()
        self.posts = posts
        self.node = NS(store=Store(), me=NS(onion="me"), mod_hidden=lambda *a: "", mod_check=lambda *a: "")
        self.shell = NS(current_track=lambda: None)
        self.feed_page = Page(self)
        self.feed_page.resize(800, 700)
        from PyQt6.QtWidgets import QVBoxLayout
        QVBoxLayout(self).addWidget(self.feed_page)
        self._feed_sort = "new"
        self._feed_type = "post"
        self._open_threads, self._revealed, self._reply_to = set(), set(), {}
        self.built = []

    def _composer(self): return QWidget()
    def _type_row(self, page): pass
    def _sorted_posts(self): return self.posts
    def _type_ok(self, p): return True
    def _fill_feed_side(self): pass

    def _post_card(self, p):
        self.built.append(p["id"])
        lbl = QLabel(p["text"])
        lbl.setFixedHeight(200 + 40 * (int(p["id"]) % 3))
        return lbl


class VirtualFeed(unittest.TestCase):
    def setUp(self):
        self.posts = [{"id": str(n), "author": "f", "ts": time.time() - n, "text": f"post {n}"} for n in range(80)]
        self.view = Feed(self.posts)
        self.view.resize(820, 720)
        self.view.show()
        self.view._fill_feed()
        pump(0.3)

    def tearDown(self):
        self.view.deleteLater()
        pump(0.05)

    def test_only_cards_near_screen_are_built(self):
        v = self.view
        self.assertEqual(len(v._feed_slots), 80)
        self.assertLess(len(v._feed_cards), 25, "the whole feed must not be built")
        self.assertIn("0", v._feed_cards)
        self.assertNotIn("79", v._feed_cards)

    def test_scrolling_builds_new_and_drops_far_cards_without_jumps(self):
        v = self.view
        sb = v.feed_page.verticalScrollBar()
        for _ in range(60):
            sb.setValue(sb.value() + 300)
            pump(0.03)
        pump(0.4)
        self.assertIn("79", v._feed_cards)
        self.assertNotIn("0", v._feed_cards, "far cards are dropped")
        slot = v._feed_slots["0"]
        self.assertGreaterEqual(slot.minimumHeight(), 200, "a dropped card keeps its place height")
        on_screen = [pid for pid, _s, y, h in v._feed_places()
                     if y + h >= sb.value() and y <= sb.value() + v.feed_page.viewport().height()]
        self.assertTrue(on_screen and all(pid in v._feed_cards for pid in on_screen), "everything on screen is built")

    def test_refill_reuses_unchanged_cards(self):
        v = self.view
        first = v._feed_cards["0"][1]
        v.built.clear()
        v._fill_feed()
        pump(0.2)
        self.assertEqual(v.built, [])
        self.assertIs(v._feed_cards["0"][1], first)


class ReactBarPaint(unittest.TestCase):
    def test_click_reacts_and_unreacts(self):
        from net_core import EMOJI
        from net_music import ReactBar
        got = []
        bar = ReactBar({EMOJI[0]: 2}, EMOJI[1], got.append)
        bar.show()
        pump(0.05)

        def click(i):
            r = bar._rects[i]
            pos = QPointF(r.center())
            for kind in (QMouseEvent.Type.MouseButtonPress, QMouseEvent.Type.MouseButtonRelease):
                APP.sendEvent(bar, QMouseEvent(kind, pos, bar.mapToGlobal(pos), Qt.MouseButton.LeftButton,
                                               Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        click(0)
        click(1)                                   # своя реакция — снять
        self.assertEqual(got, [EMOJI[0], ""])
        opened = []
        bar._picker = lambda pos: opened.append(pos)
        click(len(bar._chips))
        self.assertEqual(len(opened), 1)
        self.assertGreaterEqual(bar.sizeHint().width(), 7 * 28)
        bar.grab()                                 # отрисовка без ошибок
        bar.deleteLater()


if __name__ == "__main__":
    unittest.main()
