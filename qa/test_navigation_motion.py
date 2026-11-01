"""Focused pixel, lifecycle and wheel regressions; no player scenario or profile."""
import os
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from PyQt6 import sip
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QColor, QPainter, QWheelEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QStackedWidget, QWidget, QScrollArea, QVBoxLayout, QLabel, QPushButton
import ui_anim
import wheel_guard

APP = QApplication.instance() or QApplication([])


def color_at(image,x,y):
    ratio=image.devicePixelRatio()
    return image.pixelColor(round(x*ratio),round(y*ratio)).name()


class Page(QWidget):
    color = '#31587e'
    dpr = 1.
    def devicePixelRatioF(self): return self.dpr
    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(self.color))
        p.fillRect(self.width()-90, self.height()-90, 90, 90, QColor('#f23867'))
        p.end()


class NavigationMotion(unittest.TestCase):
    def setUp(self):
        self.widgets = []
        self.policy = patch.object(ui_anim, '_WIN', SimpleNamespace(settings={'motion_profile':'balanced'}))
        self.policy.start()

    def tearDown(self):
        for overlay in list(ui_anim._OVERLAYS.values()):
            if not sip.isdeleted(overlay): overlay.finish()
        for widget in self.widgets:
            if not sip.isdeleted(widget): widget.close(); widget.deleteLater()
        APP.processEvents()
        self.policy.stop()

    def page(self, width=1800, height=1000):
        page = Page(); page.resize(width, height); page.show()
        self.widgets.append(page); APP.processEvents()
        return page

    def stack(self):
        stack = QStackedWidget(); stack.resize(1000, 650)
        a, b = Page(), Page(); b.color = '#84512a'
        stack.addWidget(a); stack.addWidget(b); stack.show()
        self.widgets.append(stack); APP.processEvents()
        return stack, a, b

    def test_bounded_capture_fills_full_page_for_all_dpi_and_profiles(self):
        page = self.page(1801, 1003)
        for dpr in (1., 1.5, 2.):
            page.dpr = dpr
            for profile, budget in (('eco',900_000),('balanced',2_100_000)):
                with self.subTest(dpr=dpr, profile=profile):
                    ui_anim._WIN.settings['motion_profile'] = profile
                    pm = ui_anim._grab_area(page)
                    self.assertLessEqual(pm.width()*pm.height(), budget)
                    if profile=='balanced':self.assertEqual(pm.size(),page.size())
                    overlay = ui_anim._Transition(page, pm, pm, 'slide', 1)
                    overlay.set_t(1.)
                    image = overlay.grab().toImage()
                    self.assertEqual(color_at(image,1751,953), '#f23867')
                    self.assertEqual(color_at(image,1700,50), page.color)
                    overlay.finish(); APP.processEvents()

    def test_slide_keeps_scale_and_brightness_throughout(self):
        page = self.page(); pm = ui_anim._grab_area(page)
        for direction in (-1,0,1):
            overlay = ui_anim._Transition(page, pm, pm, 'slide', direction)
            for progress in (0., .1, .25, .5, .8, 1.):
                overlay.set_t(progress); image = overlay.grab().toImage()
                for x,y,color in ((1,1,page.color),(1790,10,page.color),(1750,950,'#f23867')):
                    self.assertEqual(color_at(image,x,y), color, (direction,progress,x,y))
            overlay.finish(); APP.processEvents()

    def test_sheet_endpoints_also_fill_full_extent(self):
        page = self.page(); old = ui_anim._grab_area(page)
        page.color = '#84512a'; new = ui_anim._grab_area(page)
        for kind in ('up','down'):
            overlay = ui_anim._Transition(page,old,new,kind,1)
            overlay.set_t(1.); image = overlay.grab().toImage()
            self.assertEqual(color_at(image,1750,950),'#f23867')
            self.assertEqual(color_at(image,1700,50),page.color)
            overlay.finish(); APP.processEvents()

    def test_resize_and_hide_release_snapshot_and_clock(self):
        for gesture in ('resize','hide'):
            stack,a,b = self.stack(); ui_anim.switch(stack,b)
            overlay = ui_anim._OVERLAYS[id(stack)]; timer = overlay.frame_timer
            if gesture=='resize': stack.resize(1100,700)
            else: stack.hide()
            self.assertNotIn(id(stack),ui_anim._OVERLAYS)
            self.assertFalse(timer.isActive())
            self.assertTrue(overlay.new.isNull())

    def test_input_interrupts_transition_without_consuming_click(self):
        stack,a,b = self.stack(); ui_anim.switch(stack,b)
        overlay = ui_anim._OVERLAYS[id(stack)]
        QTest.mousePress(b,Qt.MouseButton.LeftButton)
        self.assertTrue(overlay._finished)
        self.assertFalse(overlay.frame_timer.isActive())
        self.assertIs(stack.currentWidget(),b)
        QTest.mouseRelease(b,Qt.MouseButton.LeftButton)

    def test_shared_frame_clock_finishes_after_elapsed_duration(self):
        stack,a,b = self.stack(); ui_anim.switch(stack,b)
        overlay = ui_anim._OVERLAYS[id(stack)]
        overlay._prepare()
        timer = overlay.frame_timer
        start = overlay._started; duration = overlay._duration
        with patch.object(ui_anim.time,'perf_counter',return_value=start+duration*.4):
            overlay._frame(); self.assertAlmostEqual(overlay.t,.4,places=4)
        with patch.object(ui_anim.time,'perf_counter',return_value=start+duration*2): overlay._frame()
        self.assertNotIn(id(stack),ui_anim._OVERLAYS)
        self.assertFalse(timer.isActive())
        overlay.finish()  # repeated cancellation is harmless

    def test_rapid_navigation_releases_previous_frames_and_bounds_overlays(self):
        stack,a,b = self.stack(); previous = None
        for index in range(12):
            target = b if index%2==0 else a
            ui_anim.switch(stack,target,direction=1)
            if previous is not None:
                self.assertTrue(previous._finished)
                self.assertFalse(previous.frame_timer.isActive())
                self.assertTrue(previous.old.isNull())
            previous = ui_anim._OVERLAYS[id(stack)]
            self.assertIs(stack.currentWidget(),target)
        for _ in range(3):
            other,x,y = self.stack(); ui_anim.switch(other,y)
            self.assertLessEqual(len(ui_anim._OVERLAYS),2)

    def test_morph_uses_same_clock_geometry_and_cleanup(self):
        page = self.page()
        ui_anim.morph(page,lambda:setattr(page,'color','#84512a'))
        overlay = ui_anim._OVERLAYS[id(page)]
        overlay._prepare()
        overlay.set_t(1.); image = overlay.grab().toImage()
        self.assertEqual(color_at(image,1700,50),page.color)
        self.assertEqual(color_at(image,1750,950),'#f23867')
        self.assertTrue(overlay.frame_timer.isActive())
        overlay.finish(); self.assertFalse(overlay.frame_timer.isActive())

    def test_first_show_capture_has_final_nested_layout_geometry(self):
        stack,a,b = self.stack(); target = QScrollArea(); body = QWidget()
        layout = QVBoxLayout(body); label = QLabel('Collection'); layout.addWidget(label)
        label.setMinimumHeight(45); layout.addStretch(1)
        target.setWidgetResizable(True); target.setWidget(body); stack.addWidget(target)
        ui_anim.switch(stack,target)
        ui_anim._OVERLAYS[id(stack)]._prepare()
        self.assertGreater(label.width(),800)
        overlay = ui_anim._OVERLAYS[id(stack)]
        self.assertEqual(overlay.size(),stack.size())

    def test_transparent_pages_capture_ancestor_glass_without_siblings(self):
        root = self.page(1000,700)
        parent = QWidget(root); parent.setGeometry(50,40,900,600)
        stack = QStackedWidget(parent); stack.setGeometry(60,60,700,450)
        stack.addWidget(QWidget()); stack.addWidget(QWidget())
        class Sibling(Page):
            paints=0
            def paintEvent(self,event):
                self.paints+=1;super().paintEvent(event)
        sibling=Sibling(parent);sibling.setGeometry(780,0,100,100)
        parent.show();sibling.show();stack.show();APP.processEvents()
        sibling.paints=0
        pm=ui_anim._grab_area(stack)
        self.assertEqual(pm.toImage().pixelColor(10,10).name(),root.color)
        self.assertEqual(sibling.paints,0)
        ui_anim.switch(stack,stack.widget(1));overlay=ui_anim._OVERLAYS[id(stack)]
        self.assertEqual(color_at(overlay.grab().toImage(),10,10),root.color)
        overlay._prepare();overlay.set_t(.5)
        self.assertEqual(color_at(overlay.grab().toImage(),10,10),root.color)

    def test_fast_event_burst_captures_only_final_incoming_page(self):
        stack,a,b=self.stack()
        with patch.object(ui_anim,'_grab_area',wraps=ui_anim._grab_area) as capture:
            for index in range(30):ui_anim.switch(stack,b if index%2==0 else a)
            self.assertEqual(capture.call_count,1)
            overlay=ui_anim._OVERLAYS[id(stack)]
            self.assertIsNone(overlay.new)
            overlay._prepare()
            self.assertEqual(capture.call_count,2)
            self.assertEqual(overlay.new.toImage().pixelColor(500,200).name(),a.color)

    def test_external_navigation_click_preserves_visible_transition_until_switch(self):
        stack,a,b=self.stack();ui_anim.switch(stack,b)
        overlay=ui_anim._OVERLAYS[id(stack)];overlay._prepare();overlay.set_t(.4)
        before=overlay.grab().toImage()
        button=QPushButton('Next');self.widgets.append(button);button.show()
        QTest.mousePress(button,Qt.MouseButton.LeftButton)
        self.assertFalse(overlay._finished)
        ui_anim.switch(stack,a)
        current=ui_anim._OVERLAYS[id(stack)]
        self.assertEqual(current.old.toImage().pixelColor(500,200).name(),color_at(before,500,200))
        self.assertTrue(overlay._finished)
        QTest.mouseRelease(button,Qt.MouseButton.LeftButton)

    def scroll_area(self):
        area = QScrollArea(); body = QWidget(); body.resize(1800,6000)
        area.setWidget(body); area.resize(600,450); area.show()
        self.widgets.append(area); APP.processEvents()
        return area

    def test_reversing_wheel_turns_immediately_and_settles(self):
        area = self.scroll_area(); bar = area.verticalScrollBar(); bar.setValue(1000)
        smooth = wheel_guard._Smooth()
        try:
            smooth.add(bar,1000,area)
            smooth.add(bar,-100,area)
            with patch.object(wheel_guard.time,'perf_counter',return_value=smooth.t+.016): smooth._tick()
            self.assertLess(bar.value(),1000)
            for _ in range(60):
                with patch.object(wheel_guard.time,'perf_counter',return_value=smooth.t+.016): smooth._tick()
            self.assertEqual(bar.value(),900)
            self.assertFalse(smooth.timer.isActive())
        finally: smooth.timer.stop()

    def test_drag_hidden_page_and_changed_range_cancel_or_clamp(self):
        area = self.scroll_area(); bar = area.verticalScrollBar(); smooth = wheel_guard._Smooth()
        try:
            smooth.add(bar,300,area); bar.setSliderDown(True); smooth._tick()
            self.assertFalse(smooth.timer.isActive()); bar.setSliderDown(False)
            smooth.add(bar,1000,area); bar.setMaximum(20)
            for _ in range(60):
                with patch.object(wheel_guard.time,'perf_counter',return_value=smooth.t+.016): smooth._tick()
            self.assertEqual(bar.value(),20); self.assertFalse(smooth.timer.isActive())
            smooth.add(bar,-10,area); area.hide(); smooth._tick()
            self.assertFalse(smooth.timer.isActive())
        finally: smooth.timer.stop()

    def test_precise_touchpad_stays_native_and_shift_uses_horizontal_bar(self):
        area = self.scroll_area()
        precise = QWheelEvent(QPointF(10,10),QPointF(10,10),QPoint(0,-6),QPoint(0,-120),
                             Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.ScrollUpdate,False)
        self.assertFalse(wheel_guard.smooth_scroll(area,precise))
        shift = QWheelEvent(QPointF(10,10),QPointF(10,10),QPoint(),QPoint(0,-120),
                           Qt.MouseButton.NoButton,Qt.KeyboardModifier.ShiftModifier,Qt.ScrollPhase.NoScrollPhase,False)
        self.assertTrue(wheel_guard.smooth_scroll(area,shift))
        smooth = wheel_guard._SMOOTH
        self.assertIn(id(area.horizontalScrollBar()),smooth.items)
        smooth.items.clear(); smooth.timer.stop()


if __name__=='__main__': unittest.main(verbosity=2)
