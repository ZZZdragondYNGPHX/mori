import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from character.motion import TouchProgression, movement_direction
from companion.events import EventType
from companion.preferences import InteractionPreferences
from presentation.keyposes import PoseLibrary
from presentation.pet import ASSET_ROOT, DesktopPet


class MotionPolicyTests(unittest.TestCase):
    def test_four_directions_and_stable_diagonal_boundary(self):
        for delta, direction in (((-80, 5), "left"), ((80, 5), "right"),
                                 ((5, -80), "up"), ((5, 80), "down")):
            self.assertEqual(movement_direction(*delta), direction)
        direction = "right"
        for dy in (99, 101, 98, 104):
            direction = movement_direction(100, dy, direction)
            self.assertEqual(direction, "right")
        self.assertEqual(movement_direction(100, 140, direction), "down")
        self.assertEqual(movement_direction(0, 0, "up"), "up")

    def test_repeated_touch_progresses_independently_then_recovers(self):
        progression = TouchProgression()
        self.assertTrue(progression.next("head", 0).startswith("head-1-"))
        self.assertTrue(progression.next("head", 1).startswith("head-2-"))
        self.assertTrue(progression.next("tail", 2).startswith("tail-1-"))
        shy = progression.next("head", 3)
        self.assertTrue(shy.startswith("head-3-"))
        next_shy = progression.next("head", 4)
        self.assertTrue(next_shy.startswith("head-3-"))
        self.assertNotEqual(shy, next_shy)
        self.assertTrue(progression.next("head", 17).startswith("head-1-"))


class KeyPoseTests(unittest.TestCase):
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
        self.pet.move(20, 20)  # Canonical right-facing hit coordinates for these fixtures.
        self.now = [100.0]
        self.pet._touch_poses.clock = lambda: self.now[0]
        self.app.processEvents()

    def tearDown(self):
        self.pet._quitting = True
        self.pet._cleanup()
        self.pet.close()
        self.pet.deleteLater()
        self.app.processEvents()
        self.environment.stop()
        self.data.cleanup()

    def test_bundled_clips_are_transparent_and_direction_drawings_are_distinct(self):
        root = Path(ASSET_ROOT) / "motion"
        manifest = json.loads((root / "clips.json").read_text())
        images = set()
        for clip in manifest["clips"].values():
            self.assertEqual(len(clip["frames"]), len(clip["durations"]))
            self.assertTrue(all(ms >= 80 for ms in clip["durations"]))
            images.update(clip["frames"])
        self.assertEqual(len(images), 132)
        for filename in images:
            image = QImage(str(root / filename))
            self.assertEqual((image.width(), image.height()), (280, 230))
            self.assertTrue(image.hasAlphaChannel())
            self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
        views = [QImage(str(root / f"walk-{direction}-0.png"))
                 for direction in ("left", "right", "up", "down")]
        for index, image in enumerate(views):
            for other in views[index + 1:]:
                self.assertNotEqual(image, other)

    def test_actual_head_click_plays_in_focus_and_latest_work_state_returns(self):
        self.assertFalse(self.pet.preferences.entertaining)
        QTest.mouseClick(self.pet, Qt.LeftButton, pos=QPoint(190, 65))
        self.pet._click_timer.stop()
        self.pet._do_touch()
        self.assertTrue(self.pet._touch_poses.active)
        self.assertEqual(self.pet._touch_region, "head")
        self.pet.apply_tool_reaction(self.pet.character.react(EventType.WAITING_INPUT), [], [])
        self.assertEqual(self.pet._state, "touch")
        self.assertTrue(self.pet._touch_poses.active)
        self.pet._on_touch_timeout()
        self.assertFalse(self.pet._touch_poses.active)
        self.assertEqual(self.pet._cur_key, "attention")
        self.assertFalse(self.pet._renderer._paused)
        self.assertFalse(self.pet.interactions._ambient.isActive())

    def test_tail_click_is_different_and_drag_interrupts_reaction(self):
        QTest.mouseClick(self.pet, Qt.LeftButton, pos=QPoint(65, 120))
        self.pet._click_timer.stop()
        self.pet._do_touch()
        self.assertEqual(self.pet._touch_region, "tail")
        self.assertTrue(self.pet._touch_poses.library._name.startswith("tail-1-"))
        self.pet._start_pickup()
        self.assertFalse(self.pet._touch_poses.active)
        self.assertFalse(self.pet._touch_poses._timer.isActive())
        self.assertEqual(self.pet._state, "pickup")
        self.assertTrue(self.pet._drag_poses.active)
        self.assertTrue(self.pet._renderer._paused)

    def test_hide_pauses_touch_time_and_does_not_finish_in_background(self):
        self.pet._touch_region = "head"
        self.pet._do_touch()
        self.now[0] += .2
        self.pet._touch_poses.advance()
        frame = self.pet._touch_poses.frame.toImage()
        remaining = self.pet._touch_timer.remainingTime()
        self.pet.hide()
        self.assertFalse(self.pet._touch_timer.isActive())
        self.assertFalse(self.pet._touch_poses._timer.isActive())
        self.now[0] += 60
        self.pet.show()
        self.assertEqual(self.pet._touch_poses.frame.toImage(), frame)
        self.assertLessEqual(abs(self.pet._touch_timer.remainingTime() - remaining), 20)

    def test_touch_animation_holds_recovery_and_stops_high_rate_timer(self):
        self.pet._touch_region = "head"
        self.pet._do_touch()
        self.now[0] += 10
        self.pet._touch_poses.advance()
        self.assertFalse(self.pet._touch_poses._timer.isActive())
        self.assertEqual(self.pet._touch_poses.frame.toImage(),
                         self.pet._touch_poses.library.frames[-1].toImage())

    def test_greeting_clears_paused_touch_overlay(self):
        self.pet._touch_region = "head"
        self.pet._do_touch()
        self.pet.hide()
        self.pet.show_whale(greet=True)
        self.assertEqual(self.pet._state, "cheerup_show")
        self.assertFalse(self.pet._touch_poses.active)
        self.assertFalse(self.pet._touch_timer.isActive())
        self.assertEqual(self.pet._cur_key, "cheerup")

    def test_walking_uses_up_view_and_stops_when_focus_returns(self):
        director = self.pet.interactions
        director.clock = lambda: self.now[0]
        director.apply(InteractionPreferences(mode="entertainment"), persist=False)
        origin = self.pet.pos()
        with patch.object(director, "_random_destination", return_value=origin + QPoint(0, -100)):
            self.assertTrue(director.start("wander"))
        self.assertEqual(director.direction, "up")
        self.now[0] += .2
        director.advance()
        canvas = QImage(280, 230, QImage.Format_ARGB32)
        canvas.fill(Qt.transparent)
        painter = QPainter(canvas)
        self.assertTrue(director.draw(painter))
        painter.end()
        self.assertEqual(director._walk_poses._name, "walk-up")
        director.apply(InteractionPreferences(mode="focus"), persist=False)
        self.assertFalse(director.active)
        self.assertFalse(director._frame.isActive())
        self.assertFalse(director._ambient.isActive())
        self.assertEqual(self.pet._state, "idle")

    def test_missing_keyposes_fall_back_to_existing_touch(self):
        self.pet._touch_poses.library = PoseLibrary(Path(self.data.name))
        self.pet._touch_region = "head"
        self.pet._do_touch()
        self.assertFalse(self.pet._touch_poses.active)
        self.assertEqual(self.pet._cur_key, "touch")
        self.assertFalse(self.pet._renderer._paused)


if __name__ == "__main__":
    unittest.main()
