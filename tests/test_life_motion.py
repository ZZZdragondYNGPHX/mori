"""Focused local checks for available ledges, real poses and interruptions."""
import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from test_interactions import FakeDesktop

from character.window_motion import fits_box
from companion.preferences import InteractionPreferences
from presentation.interactions import InteractionDirector
from presentation.pet import ASSET_ROOT, DesktopPet
from presentation.transitions import FrameTransition


class LedgePolicyTests(unittest.TestCase):
    def test_entire_outline_and_margin_are_required(self):
        self.assertTrue(fits_box((20, 20, 70, 90), (0, 0, 100, 100)))
        self.assertFalse(fits_box((20, 20, 99, 90), (0, 0, 100, 100)))
        self.assertTrue(fits_box((-180, 60, -40, 170), (-200, 40, 0, 200)))
        self.assertFalse(fits_box((10, -1, 80, 90), (0, 0, 100, 100)))


class TransitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.widget = QWidget()
        self.widget.show()
        self.now = [0.0]
        self.blend = FrameTransition(self.widget, clock=lambda: self.now[0])

    def tearDown(self):
        self.blend.clear()
        self.widget.close()
        self.widget.deleteLater()
        self.app.processEvents()

    def frame(self, color):
        image = QImage(8, 8, QImage.Format_ARGB32_Premultiplied)
        image.fill(QColor(color))
        return image

    def test_midpoint_stays_opaque_and_finishes_without_old_pixels(self):
        red, blue = self.frame("red"), self.frame("blue")
        self.blend.present(red, "a")
        self.assertEqual(self.blend.present(blue, "b"), red)
        self.now[0] = .06
        color = self.blend.present(blue, "b").pixelColor(3, 3)
        self.assertGreaterEqual(color.alpha(), 254)
        self.assertLess(abs(color.red() - color.blue()), 3)
        self.now[0] = .13
        self.assertEqual(self.blend.present(blue, "b"), blue)
        self.assertFalse(self.blend._timer.isActive())

    def test_interrupt_begins_at_the_visible_mixture(self):
        self.blend.present(self.frame("red"), "a")
        self.blend.present(self.frame("blue"), "b")
        self.now[0] = .04
        visible = self.blend.present(self.frame("blue"), "b")
        self.assertEqual(self.blend.present(self.frame("green"), "c"), visible)
        self.now[0] = .2
        self.assertEqual(self.blend.present(self.frame("green"), "c"), self.frame("green"))

    def test_same_pose_does_not_restart_and_hidden_time_is_paused(self):
        self.blend.present(self.frame("red"), "a")
        self.blend.present(self.frame("blue"), "b")
        self.now[0] = .04
        self.blend.present(self.frame("blue"), "b")
        self.assertEqual(self.blend._started, 0)
        self.blend.pause()
        self.assertFalse(self.blend._timer.isActive())
        self.now[0] = 100
        self.blend.resume()
        self.assertAlmostEqual(self.now[0] - self.blend._started, .04)


class LifeMotionTests(unittest.TestCase):
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
        self.pet.interactions.stop()
        self.now = [100.0]
        self.desktop = FakeDesktop()
        self.window = replace(self.desktop.active_window, geometry=QRect(360, 240, 680, 480))
        self.desktop.active_window = self.window
        self.desktop.windows[self.window.identity] = self.window
        self.director = InteractionDirector(self.pet, ASSET_ROOT, self.pet.preferences, self.desktop,
                                           clock=lambda: self.now[0])
        self.pet.interactions = self.director
        self.bounds = patch.object(self.director, "_bounds", return_value=QRect(0, 0, 1600, 1000))
        self.bounds.start()
        self.director.apply(InteractionPreferences(mode="entertainment", dialogue=False), persist=False)

    def tearDown(self):
        self.pet._quitting = True
        self.pet._cleanup()
        self.pet.close()
        self.pet.deleteLater()
        self.app.processEvents()
        self.bounds.stop()
        self.environment.stop()
        self.data.cleanup()

    def advance(self, seconds):
        self.now[0] += seconds
        self.director.advance()

    def render(self):
        image = QImage(280, 230, QImage.Format_ARGB32_Premultiplied)
        image.fill(0)
        painter = QPainter(image)
        self.assertTrue(self.director.draw(painter))
        painter.end()
        return image

    def target(self, geometry):
        self.window = replace(self.window, geometry=geometry)
        self.desktop.windows[self.window.identity] = self.window
        self.desktop.active_window = self.window

    def test_right_sliver_is_rejected_without_moving_or_pausing(self):
        self.target(QRect(900, 240, 680, 480))
        origin = self.pet.pos()
        self.assertFalse(self.director.start("peek-right", self.window))
        self.assertEqual(self.pet.pos(), origin)
        self.assertEqual(self.pet._state, "idle")
        self.assertFalse(self.director._frame.isActive())
        self.assertTrue(self.director.start("peek-left", self.window))

    def test_narrow_low_or_high_windows_do_not_start_attached_scenes(self):
        for geometry, scene in ((QRect(500, 240, 160, 480), "sit"),
                                (QRect(500, 40, 680, 480), "sit"),
                                (QRect(500, 40, 680, 480), "peek-top"),
                                (QRect(500, 850, 680, 100), "peek-right"),
                                (QRect(500, 240, 180, 400), "press")):
            with self.subTest(geometry=geometry, scene=scene):
                self.target(geometry)
                self.assertFalse(self.director.start(scene, self.window))

    def test_top_peek_support_and_whole_outline_follow_the_window(self):
        self.assertTrue(self.director.start("peek-top", self.window))
        self.advance(self.director.steps[self.director.index].seconds + .01)
        frames = []
        for seconds in (0, .5, .7, 1.3):
            self.advance(seconds)
            frames.append(self.render())
            anchor = self.director._window_poses.anchor_at("window-peek-top", 0)
            self.assertEqual(self.pet.pos() + anchor,
                             QPoint(self.window.geometry.center().x(), self.window.geometry.top()))
        self.assertGreater(len({bytes(frame.constBits()) for frame in frames}), 2)
        self.assertEqual(self.pet._hit_touch_region(QPoint(141, 130) + self.director.displayed_origin), "head")
        started = self.director._started
        self.target(self.window.geometry.translated(80, 30))
        self.director._follow_target()
        self.assertEqual(self.director._started, started)
        self.assertTrue(self.director._window_fits("peek-top", self.window))

    def test_moving_to_edge_or_screen_shrink_ends_the_current_scene(self):
        self.director.start("peek-right", self.window)
        self.advance(self.director.steps[self.director.index].seconds + .01)
        self.target(QRect(900, 240, 680, 480))
        self.director._follow_target()
        self.assertFalse(self.director.active)
        self.assertFalse(self.director._frame.isActive())
        self.target(QRect(360, 240, 680, 480))
        self.director.start("sit", self.window)
        self.advance(self.director.steps[self.director.index].seconds + .01)
        with patch.object(self.director, "_bounds", return_value=QRect(0, 210, 1600, 790)):
            self.advance(.04)
        self.assertFalse(self.director.active)

    def test_real_daily_poses_require_entertainment_and_yield_to_work(self):
        for scene in ("stretch", "nap", "dance"):
            self.assertTrue(self.director.start(scene))
            self.render()
            self.assertTrue(self.director._scene_poses._name.startswith(("daily-", "base-")))
            self.director.cancel(restore_position=False)
        self.director.start("nap")
        self.pet.apply_tool_reaction(self.pet.character.react("thinking"), [], [])
        self.assertFalse(self.director.active)
        self.assertEqual(self.pet._cur_key, "think")
        self.assertFalse(self.director.start("stretch"))
        self.director.apply(replace(self.pet.preferences, mode="focus"), persist=False)
        self.assertFalse(self.director.start("nap"))

    def test_seating_uses_a_short_position_transition_for_large_support_changes(self):
        self.director.start("sit", self.window)
        self.advance(self.director.steps[self.director.index].seconds + .01)
        self.advance(sum(self.director._window_poses.definition("window-seat")["durations"][:2]) / 1000 - .01)
        before = self.pet.pos()
        self.advance(.04)
        midway = self.pet.pos()
        self.assertGreater(midway.y(), before.y())
        self.assertLess(midway.y() - before.y(), 40)
        self.advance(.12)
        self.assertEqual(self.pet.pos() + self.director._sit_anchor(),
                         QPoint(self.window.geometry.center().x(), self.window.geometry.top()))

    def test_legacy_story_poses_use_authored_frames(self):
        self.director.start("wander")
        self.advance(self.director.steps[0].seconds + .01)
        self.render()
        self.assertEqual(self.director._scene_poses._name, "base-think")
        self.assertEqual(len(self.director._scene_poses.frames), 4)

    def test_all_nine_base_clips_have_four_distinct_authored_sources(self):
        manifest = json.loads((Path(ASSET_ROOT) / "motion/clips.json").read_text())
        self.assertEqual(len(manifest["base_animations"]), 9)
        for name in manifest["base_animations"].values():
            clip = manifest["clips"][name]
            self.assertEqual(len(set(clip["frames"])), 4)
            self.assertEqual(len(clip["durations"]), 4)

    def test_once_work_reaction_holds_its_authored_recovery(self):
        self.pet._set_image("cheerup")
        renderer = self.pet._renderer
        # CacheNone streams forward; exercise real playback rather than seeking
        # over undecoded frames, which Qt's GIF reader may reject.
        QTest.qWait(2600)
        self.assertTrue(renderer._exhausted)
        renderer.resume()
        self.assertEqual(renderer.movie.currentFrameNumber(), 3)
        self.assertFalse(renderer._limit.isActive())

    def test_actual_widget_transition_stops_when_hidden_and_clears_on_cleanup(self):
        self.app.processEvents()
        self.pet._set_image("think")
        self.app.processEvents()
        self.assertIsNotNone(self.pet._transitions.target)
        self.pet.hide()
        self.assertFalse(self.pet._transitions._timer.isActive())
        self.pet._transitions.clear()
        self.assertIsNone(self.pet._transitions.source)


if __name__ == "__main__":
    unittest.main()
