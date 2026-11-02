"""Embedded song sections must track layout moves before any scrolling occurs."""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from PyQt6.QtWidgets import QApplication,QWidget,QScrollArea,QListView,QVBoxLayout,QAbstractItemView
from PyQt6.QtGui import QStandardItemModel,QStandardItem
from track_section import TrackSection

APP=QApplication.instance() or QApplication([])


class TrackSectionCold(unittest.TestCase):
    def setUp(self):
        self.outer=QScrollArea();self.outer.setWidgetResizable(True);self.outer.resize(800,600)
        body=QWidget();self.layout=QVBoxLayout(body);self.layout.setContentsMargins(0,0,0,0)
        self.header=QWidget();self.header.setFixedHeight(900);self.layout.addWidget(self.header)
        self.view=QListView();self.view.model_=QStandardItemModel(self.view)
        for i in range(5):self.view.model_.appendRow(QStandardItem(str(i)))
        self.view.setModel(self.view.model_);self.view.setUniformItemSizes(True)
        self.view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.section=TrackSection(self.view,60);self.layout.addWidget(self.section)
        footer=QWidget();footer.setFixedHeight(1500);self.layout.addWidget(footer)
        self.outer.setWidget(body);self.outer.show()
        for _ in range(4):APP.processEvents()

    def tearDown(self):
        self.outer.close();self.outer.deleteLater();APP.processEvents()

    def test_header_collapsing_reveals_previously_offscreen_song_view(self):
        bar=self.outer.verticalScrollBar();self.assertEqual(bar.value(),0)
        self.assertFalse(self.view.isVisible());height=self.section.height()
        self.header.setFixedHeight(10);self.layout.activate();APP.processEvents()
        self.assertEqual(self.section.height(),height)
        self.assertEqual(bar.value(),0)
        self.assertTrue(self.view.isVisible())
        self.assertLess(self.section.y(),self.outer.viewport().height())

    def test_header_expanding_hides_song_view_without_scroll_or_resize(self):
        self.header.setFixedHeight(10);self.layout.activate();APP.processEvents()
        self.assertTrue(self.view.isVisible());height=self.section.height()
        self.header.setFixedHeight(900);self.layout.activate();APP.processEvents()
        self.assertEqual(self.section.height(),height)
        self.assertEqual(self.outer.verticalScrollBar().value(),0)
        self.assertFalse(self.view.isVisible())


if __name__=='__main__':unittest.main(verbosity=2)
