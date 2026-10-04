"""Local pointer policy and Qt integration checks for batch three."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from character.cursor import CursorFollower, CursorInterest
from companion.events import EventType
from companion.preferences import InteractionPreferences
from presentation.keyposes import PoseLibrary
from presentation.pet import DesktopPet


class CursorPolicyTests(unittest.TestCase):
    def test_glances_have_quiet_gaps_and_stable_direction_boundaries(self):
        interest = CursorInterest()
        self.assertIsNone(interest.look(200, 0, 0))
        self.assertEqual(interest.look(200, 0, .25), "right")
        self.assertEqual(interest.look(100, 104, .3), "right")
        self.assertEqual(interest.look(0, -200, .4), "right")
        self.assertEqual(interest.look(0, -200, .6), "up")
        self.assertIsNone(interest.look(0, -200, 3))
        self.assertIsNone(interest.look(0, -300, 4.5))
        self.assertIsNone(interest.look(0, -350, 4.6))

    def test_only_close_or_recent_fast_approaches_make_her_shy(self):
        interest = CursorInterest()
        interest.sample(0, 0, 0)
        self.assertFalse(interest.shy(120, 0))
        interest.sample(80, 0, .04)
        self.assertTrue(interest.shy(120, .04))
        self.assertFalse(interest.shy(400, .04))
        self.assertFalse(interest.shy(120, 1))
        self.assertTrue(interest.shy(50, 1))

    def test_follow_has_personal_space_slow_reversal_and_finite_travel(self):
        follower = CursorFollower()
        self.assertEqual(follower.advance(80, 0, .04), (0, 0))
        x, _ = follower.advance(1000, 0, .04)
        self.assertTrue(0 < x < 4)
        x, _ = follower.advance(-1000, 0, .04)
        self.assertLess(abs(x), 4)
        for _ in range(200):
            x, y = follower.advance(1000, 0, 10)
            self.assertLessEqual(abs(x), 8)
            self.assertEqual(y, 0)
        self.assertAlmostEqual(follower.travelled, 260)
        self.assertEqual(follower.advance(1000, 0, .04), (0, 0))


class CursorPresentationTests(unittest.TestCase):
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
        self.pet.move(320, 240)
        self.director = self.pet.interactions
        self.now = [100.0]
        self.director.clock = lambda: self.now[0]
        self.bounds = patch.object(self.director, "_bounds", return_value=QRect(0, 0, 1600, 1000))
        self.bounds.start()
        self.cursor = [QPoint(900, 330)]
        self.cursor_patch = patch("presentation.interactions.QCursor.pos", side_effect=lambda: self.cursor[0])
        self.cursor_patch.start()
        self.director.apply(InteractionPreferences(mode="entertainment", dialogue=False), persist=False)

    def tearDown(self):
        self.pet._quitting = True
        self.pet._cleanup()
        self.pet.close()
        self.pet.deleteLater()
        self.app.processEvents()
        self.cursor_patch.stop()
        self.bounds.stop()
        self.environment.stop()
        self.data.cleanup()

    def advance(self, seconds):
        self.now[0] += seconds
        self.director.advance()

    def render(self):
        image = QImage(280, 230, QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        drawn = self.director.draw(painter)
        painter.end()
        self.assertTrue(drawn)
        self.assertTrue(any(bytes(image.constBits())))
        return image

    def test_nearby_gaze_preserves_idle_position_and_returns_to_blinking(self):
        attention = self.director.attention
        origin = self.pet.pos()
        self.cursor[0] = origin + QPoint(385, 90)
        attention.advance()
        self.now[0] += .3
        attention.advance()
        self.assertIsNotNone(attention.frame)
        self.render()
        self.assertEqual(self.pet.pos(), origin)
        self.assertEqual(self.pet._state, "idle")
        self.assertTrue(self.pet._stage_timer.isActive())
        self.now[0] += 2.8
        attention.advance()
        self.assertIsNone(attention.frame)
        self.assertTrue(attention._timer.isActive())

    def test_cursor_switch_and_walking_switch_have_independent_effects(self):
        self.director.apply(InteractionPreferences(mode="entertainment", cursor_play=False), persist=False)
        self.assertFalse(self.director.attention._timer.isActive())
        self.assertFalse(self.director.start("cursor"))
        self.director.apply(InteractionPreferences(mode="entertainment", walking=False), persist=False)
        self.assertTrue(self.director.attention._timer.isActive())
        self.assertFalse(self.director.start("cursor"))

    def test_chase_tracks_live_pointer_without_teleporting_and_finishes(self):
        self.assertTrue(self.director.start("cursor"))
        self.assertFalse(self.director.attention._timer.isActive())
        self.advance(.73)
        self.advance(self.director.steps[self.director.index].seconds + .01)
        self.assertEqual(self.director.steps[self.director.index].pose, "cursor-follow")
        origin = self.pet.pos()
        for _ in range(25):
            before = self.pet.pos()
            self.advance(.04)
            self.assertLessEqual((self.pet.pos() - before).manhattanLength(), 9)
        self.assertGreater(self.pet.x(), origin.x())
        self.render()
        self.assertTrue(self.director._walk_poses._name.startswith("walk-"))
        self.cursor[0] = QPoint(60, 330)
        for _ in range(25):
            self.advance(.04)
        self.assertEqual(self.director.direction, "left")
        self.render()
        for _ in range(100):
            if not self.director.active:
                break
            self.advance(.08)
        self.assertFalse(self.director.active)
        self.assertLessEqual(self.director._cursor_distance, 270)
        self.assertFalse(self.director._frame.isActive())
        self.assertEqual(self.pet._state, "idle")

    def test_close_left_pointer_uses_left_shyness_then_warm_recovery(self):
        self.cursor[0] = self.pet.pos() + QPoint(135, 90)
        origin = self.pet.pos()
        self.assertTrue(self.director.start("cursor"))
        self.advance(.73)
        self.render()
        self.assertEqual(self.director._cursor_poses._name, "mouse-curious-left")
        self.advance(self.director.steps[self.director.index].seconds + .01)
        self.assertEqual(self.director.steps[self.director.index].pose, "walk")
        self.advance(self.director.steps[self.director.index].seconds + .01)
        self.assertEqual(self.director.steps[self.director.index].pose, "mouse-shy")
        self.render()
        self.assertEqual(self.director._cursor_poses._name, "mouse-shy-left")
        self.advance(self.director.steps[self.director.index].seconds + .01)
        self.assertGreater(self.pet.x(), origin.x())
        self.assertLessEqual((self.pet.pos() - origin).manhattanLength(), 54)
        self.advance(self.director.steps[self.director.index].seconds + .01)
        self.assertFalse(self.director.active)

    def test_focus_hide_and_manual_touch_cancel_attention_and_stories(self):
        self.director.start("cursor")
        self.pet._touch_region = "head"
        self.pet._do_touch()
        self.assertFalse(self.director.active)
        self.assertFalse(self.director.attention._timer.isActive())
        self.assertTrue(self.pet._touch_poses.active)
        self.pet._on_touch_timeout()
        self.assertTrue(self.director.attention._timer.isActive())
        self.pet.hide()
        self.assertFalse(self.director._ambient.isActive())
        self.assertFalse(self.director.attention._timer.isActive())
        self.pet.show()
        self.director.apply(InteractionPreferences(mode="focus"), persist=False)
        self.assertFalse(self.director.attention._timer.isActive())
        self.assertFalse(self.director._frame.isActive())

    def test_new_work_event_yields_cursor_story_at_current_position(self):
        self.director.start("cursor")
        self.advance(.73)
        self.advance(self.director.steps[self.director.index].seconds + .01)
        for _ in range(10):
            self.advance(.04)
        origin = self.pet.pos()
        self.pet.apply_tool_reaction(self.pet.character.react(EventType.GENERATING), [], [])
        self.assertFalse(self.director.active)
        self.assertEqual(self.pet.pos(), origin)
        self.assertFalse(self.director.attention._timer.isActive())
        self.assertEqual(self.pet._cur_key, self.pet._tool_reaction.image)
        self.assertFalse(self.pet._renderer._paused)
        self.assertFalse(self.director.start("cursor"))

    def test_missing_gaze_and_story_artwork_fall_back_to_existing_assets(self):
        self.director.attention.library = PoseLibrary(Path(self.data.name))
        self.cursor[0] = self.pet.pos() + QPoint(385, 90)
        self.director.attention.advance()
        self.now[0] += .3
        self.director.attention.advance()
        self.assertIsNone(self.director.attention.frame)
        self.director._cursor_poses = PoseLibrary(Path(self.data.name))
        self.assertTrue(self.director.start("cursor"))
        self.advance(.1)
        self.render()


if __name__ == "__main__":
    unittest.main()
