"""Local checks for the second-batch touch and drag transitions."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from character.motion import DragDynamics
from companion.events import EventType
from presentation.keyposes import PoseLibrary, drag_transform
from presentation.pet import DesktopPet


class DragPolicyTests(unittest.TestCase):
    def test_reversal_is_continuous_and_stopped_motion_settles(self):
        motion = DragDynamics()
        motion.push(60, 0, .04)
        motion.advance(.04)
        self.assertGreater(motion.angle, 0)
        angle = motion.angle
        motion.push(-60, 0, .04)
        self.assertEqual(motion.angle, angle)
        self.assertEqual(motion.pose, 2)
        for _ in range(100):
            motion.advance(.04)
        self.assertTrue(motion.settled)

    def test_extreme_velocity_and_elapsed_time_remain_bounded(self):
        motion = DragDynamics()
        for delta in (1e6, -1e6):
            motion.push(delta, delta, 0)
            motion.advance(30)
            self.assertLessEqual(abs(motion.angle), 9)
            self.assertTrue(.965 <= motion.stretch <= 1.035)


class GestureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.data = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {"MORI_COMPANION_HOME": self.data.name})
        self.environment.start()
        self.pet = DesktopPet()
        self.pet.show()
        self.pet._idle_start()
        self.now = [100.0]
        self.pet._drag_poses.clock = self.pet._touch_poses.clock = lambda: self.now[0]
        self.app.processEvents()

    def tearDown(self):
        self.pet._quitting = True
        self.pet._cleanup()
        self.pet.close()
        self.pet.deleteLater()
        self.app.processEvents()
        self.environment.stop()
        self.data.cleanup()

    def click(self, point):
        QTest.mouseClick(self.pet, Qt.LeftButton, pos=point)
        self.pet._click_timer.stop()
        self.pet._do_touch()

    def test_cheeks_use_independent_variants_in_focus(self):
        self.assertFalse(self.pet.preferences.entertaining)
        self.click(QPoint(170, 120))
        self.assertEqual(self.pet._touch_region, "cheek-left")
        self.assertEqual(self.pet._touch_poses.library._name, "cheek-left-1-0")
        self.pet._on_touch_timeout()
        self.click(QPoint(218, 120))
        self.assertEqual(self.pet._touch_poses.library._name, "cheek-right-1-0")
        self.pet._on_touch_timeout()
        self.click(QPoint(170, 120))
        self.assertEqual(self.pet._touch_poses.library._name, "cheek-left-2-1")
        self.assertFalse(self.pet.interactions._ambient.isActive())

    def test_direction_pose_and_timer_sleep_after_stopping(self):
        self.pet._start_pickup()
        player = self.pet._drag_poses
        self.now[0] += .2
        player.sample(QPoint(80, 0))
        self.assertEqual(player.pose_index, 1)
        self.now[0] += .04
        player.sample(QPoint(0, 0))
        self.assertEqual(player.pose_index, 2)
        self.now[0] += .04
        player.sample(QPoint(0, -100))
        self.assertEqual(player.pose_index, 3)
        for _ in range(100):
            self.now[0] += .04
            player.advance()
        self.assertEqual(player.pose_index, 0)
        self.assertFalse(player._timer.isActive())
        self.assertTrue(player.active)

    def test_release_lands_in_place_and_restores_latest_work_state(self):
        self.pet._start_pickup()
        self.pet.move(QPoint(210, 190))
        destination = self.pet.pos()
        self.pet._end_pickup()
        self.assertEqual(self.pet._state, "landing")
        self.assertEqual(self.pet._touch_poses.library._name, "landing")
        self.assertFalse(self.pet._stage_timer.isActive())
        self.pet.apply_tool_reaction(self.pet.character.react(EventType.WAITING_INPUT), [], [])
        self.assertEqual(self.pet._state, "landing")
        self.now[0] += .32
        self.pet._touch_poses.advance()
        self.assertEqual(self.pet._touch_poses.frame.toImage(),
                         self.pet._touch_poses.library.frames[2].toImage())
        self.pet._on_touch_timeout()
        self.assertEqual(self.pet.pos(), destination)
        self.assertEqual(self.pet._cur_key, "attention")
        self.assertFalse(self.pet._renderer._paused)

    def test_regrabbing_interrupts_landing_and_hiding_ends_drag(self):
        self.pet._start_pickup()
        self.pet._end_pickup()
        self.pet._start_pickup()
        self.assertFalse(self.pet._touch_timer.isActive())
        self.assertFalse(self.pet._touch_poses.active)
        self.assertTrue(self.pet._drag_poses.active)
        self.pet.hide()
        self.assertEqual(self.pet._state, "idle")
        self.assertFalse(self.pet._drag_poses.active)
        self.assertFalse(self.pet._drag_poses._timer.isActive())
        self.pet.show()
        self.assertFalse(self.pet._renderer._paused)

    def test_foot_and_tail_grabs_keep_all_pose_bounds_in_canvas(self):
        self.pet._start_pickup()
        player = self.pet._drag_poses
        canvas = QRectF(1, 1, 278, 228)
        for pivot in (QPointF(65, 100), QPointF(220, 210), QPointF(190, 65)):
            for bounds in player._bounds:
                for angle, stretch in ((9, 1.035), (-9, .965)):
                    transform = drag_transform(pivot, angle, stretch, bounds)
                    self.assertTrue(canvas.contains(transform.mapRect(bounds)))
                    self.assertAlmostEqual(transform.map(pivot).x(), pivot.x())
                    self.assertAlmostEqual(transform.map(pivot).y(), pivot.y())
        image = QImage(280, 230, QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        self.assertTrue(player.draw(painter))
        painter.end()

    def test_missing_drag_assets_use_existing_pickup_and_release(self):
        self.pet._drag_poses.library = PoseLibrary(Path(self.data.name))
        self.pet._start_pickup()
        self.assertEqual(self.pet._cur_key, "pickup")
        self.assertFalse(self.pet._drag_poses.active)
        self.assertFalse(self.pet._renderer._paused)
        self.pet._end_pickup()
        self.assertEqual(self.pet._state, "idle")


if __name__ == "__main__":
    unittest.main()
