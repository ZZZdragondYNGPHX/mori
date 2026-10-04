"""Bubble shape, deliberately delayed input, and persisted visual controls."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from companion.preferences import InteractionPreferences, PreferenceStore
from presentation.speech import SpeechBubble


class SpeechTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = QWidget()
        self.owner.setGeometry(400, 500, 200, 100)
        self.owner.preferences = InteractionPreferences()
        self.owner.show()
        self.bubble = SpeechBubble('一个任务\n完整原文开头', self.owner, interactive=True)
        self.bubble.place_above(self.owner)
        self.bubble.show()
        self.app.processEvents()

    def tearDown(self):
        self.bubble.close()
        self.owner.close()
        self.app.processEvents()

    def test_full_alpha_surface_and_click_only_shape_exclude_rectangle_corners(self):
        self.assertTrue(self.bubble.mask().isEmpty())
        pixels = self.bubble.grab().toImage()
        self.assertEqual(pixels.pixelColor(0, 0).alpha(), 0)
        self.bubble._set_input_enabled(True)
        mask = self.bubble.mask()
        self.assertFalse(mask.isEmpty())
        self.assertFalse(mask.contains(QPoint(0, 0)))
        body = self.bubble._body_rect()
        self.assertFalse(mask.contains(body.topLeft().toPoint()))
        self.assertTrue(mask.contains(body.center().toPoint()))
        for point, _ in self.bubble._dots():
            self.assertTrue(mask.contains(point.toPoint()))
        native_id = self.bubble.winId()
        self.bubble.set_text('拖拽时换一条更长的台词，尺寸也会发生变化。')
        self.bubble.place_above(self.owner)
        self.app.processEvents()
        self.assertFalse(self.bubble._input_enabled)
        self.assertTrue(self.bubble.mask().isEmpty())
        self.assertEqual(self.bubble.winId(), native_id)
        self.assertEqual(self.bubble.grab().toImage().pixelColor(0, 0).alpha(), 0)

    def test_continuous_hover_unlocks_after_three_seconds_and_resets_on_exit(self):
        bubble = self.bubble
        native_id = bubble.winId()
        geometry = bubble.geometry()
        clicks = []
        bubble.clicked.connect(lambda: clicks.append(True))
        center = bubble._body_rect().center().toPoint()
        position = [bubble.mapToGlobal(center)]
        with patch('presentation.speech.QCursor.pos', side_effect=lambda: position[0]):
            bubble._poll_hover(10)
            QTest.mouseClick(bubble, Qt.LeftButton, pos=center)
            self.assertFalse(clicks)
            bubble._poll_hover(12.99)
            self.assertTrue(bubble.windowHandle().flags() & Qt.WindowTransparentForInput)
            # The rectangular corner is outside the circle and cancels the dwell.
            position[0] = bubble.mapToGlobal(bubble._body_rect().topLeft().toPoint())
            bubble._poll_hover(13)
            position[0] = bubble.mapToGlobal(center)
            bubble._poll_hover(14)
            bubble._poll_hover(16.99)
            self.assertFalse(bubble._input_enabled)
            bubble._poll_hover(17)
            self.assertTrue(bubble._input_enabled)
            self.assertFalse(bubble.windowHandle().flags() & Qt.WindowTransparentForInput)
            QTest.mouseClick(bubble, Qt.LeftButton, pos=center)
            self.assertEqual(len(clicks), 1)
            position[0] = bubble.mapToGlobal(QPoint(0, 0))
            bubble._poll_hover(18)
            self.assertFalse(bubble._input_enabled)
            self.assertTrue(bubble.windowHandle().flags() & Qt.WindowTransparentForInput)
        self.assertEqual(bubble.winId(), native_id)
        self.assertEqual(bubble.geometry(), geometry)
        bubble.hide()
        self.assertFalse(bubble._hover.isActive())
        bubble.show()
        self.assertFalse(bubble._input_enabled)

    def test_daily_speech_stays_passthrough_and_opacity_is_persisted(self):
        daily = SpeechBubble('日常台词', self.owner)
        try:
            daily.place_above(self.owner)
            daily.show()
            center = daily.mapToGlobal(daily._body_rect().center().toPoint())
            with patch('presentation.speech.QCursor.pos', return_value=center):
                daily._poll_hover(0)
                daily._poll_hover(20)
            self.assertFalse(daily._input_enabled)
            self.assertTrue(daily.mask().isEmpty())
            self.assertFalse(daily._hover.isActive())
            daily.set_opacity(40)
            self.assertEqual(daily._opacity, .4)
            # Test persistence and alpha of the actual rendered circle.
            with tempfile.TemporaryDirectory() as directory:
                store = PreferenceStore(Path(directory) / 'settings.json')
                store.save(InteractionPreferences(bubble_opacity=40))
                self.assertEqual(store.load().bubble_opacity, 40)
            point = daily._body_rect().center().toPoint() + QPoint(0, 25)
            self.assertLess(daily.grab().toImage().pixelColor(point).alpha(), 120)
            self.assertEqual(InteractionPreferences.from_dict({'bubble_opacity': 300}).bubble_opacity, 100)
            self.assertEqual(InteractionPreferences.from_dict({'bubble_opacity': False}).bubble_opacity, 95)
        finally:
            daily.close()


if __name__ == '__main__':
    unittest.main()
