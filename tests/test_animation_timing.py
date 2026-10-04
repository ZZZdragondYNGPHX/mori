"""Minimal local verification of semantic timing and quieter cadence."""
import json
import unittest
from pathlib import Path

import test_animation_modules as fixtures
from PySide6.QtCore import QPoint
from PySide6.QtGui import QTransform
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from character.cursor import CursorInterest
from character.timing import POSE_MS, pose_durations, quiet_interval
from companion.preferences import InteractionPreferences
from presentation.pet import ASSET_ROOT, IDLE_TALK_MS


class TimingPolicyTests(unittest.TestCase):
    def test_pose_retiming_preserves_complete_sequences_and_left_right_symmetry(self):
        clips = json.loads((Path(ASSET_ROOT) / "motion/clips.json").read_text())["clips"]
        for name, clip in clips.items():
            result = pose_durations(name, clip["durations"])
            self.assertEqual(len(result), len(clip["frames"]))
            self.assertTrue(all(ms >= 80 for ms in result), name)
            if name in POSE_MS:
                self.assertEqual(sum(result), POSE_MS[name])
        self.assertEqual(pose_durations("mouse-shy", clips["mouse-shy"]["durations"]),
                         pose_durations("mouse-shy-left", clips["mouse-shy-left"]["durations"]))
        self.assertGreater(sum(pose_durations("head-3-0", clips["head-3-0"]["durations"])), 1840)
        self.assertEqual(pose_durations("walk-left", [180] * 4), [180] * 4)

    def test_configured_frequency_is_preserved_with_moderate_extra_quiet_time(self):
        self.assertEqual(InteractionPreferences().interval_seconds, 20)
        self.assertEqual(InteractionPreferences.from_dict({"interval_seconds": 15}).interval_seconds, 15)
        for variation, expected in ((0, 23), (.5, 25), (1, 27)):
            self.assertAlmostEqual(quiet_interval(20, variation), expected)
        self.assertEqual(IDLE_TALK_MS, 45000)

    def test_pointer_looks_are_short_with_a_longer_quiet_gap(self):
        interest = CursorInterest()
        self.assertIsNone(interest.look(180, 0, 0))
        self.assertEqual(interest.look(180, 0, .3), "right")
        for now in (2.7, 3, 4.5, 6.3):
            self.assertIsNone(interest.look(180, 0, now))
        self.assertEqual(interest.look(180, 0, 6.7), "right")


class TimingPresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    setUp = fixtures.ModulePresentationTests.setUp
    tearDown = fixtures.ModulePresentationTests.tearDown
    advance = fixtures.ModulePresentationTests.advance
    render = fixtures.ModulePresentationTests.render

    def test_window_sequences_finish_on_the_complete_recovery_pose(self):
        self.director.apply(InteractionPreferences(mode="entertainment"), persist=False)
        self.assertTrue(self.director.start("peek-right", self.desktop.active_window))
        self.advance(self.director.steps[0].seconds + .01)
        self.assertAlmostEqual(self.director.steps[self.director.index].seconds, 5.2)
        self.advance(5.19)
        self.render()
        self.assertEqual(self.director.displayed_frame.toImage(), self.director._window_poses.frames[-1].toImage())
        self.advance(.02)
        self.assertEqual(self.director.steps[self.director.index].pose, "walk")

    def test_window_rest_is_longer_and_short_actions_match_pose_timing(self):
        self.director.apply(InteractionPreferences(mode="entertainment"), persist=False)
        for scene, expected in (("sit", {"window-seat": 1.45, "window-swing": 12, "window-rise": 1.25}),
                                ("press", {"window-consider": 1.2, "window-press": 1.05, "window-press-recover": .9}),
                                ("stretch", {"daily-stretch": 2.8})):
            self.assertTrue(self.director.start(scene, self.desktop.active_window))
            for step in self.director.steps:
                if step.pose in expected:
                    self.assertAlmostEqual(step.seconds, expected[step.pose])
            self.director.cancel(restore_position=False)

    def test_touch_and_landing_timers_match_retimed_players_and_work_returns(self):
        self.pet._touch_region = "head"
        self.pet._do_touch()
        total = sum(self.pet._touch_poses.library.clip["durations"])
        self.assertEqual(self.pet._touch_timer.interval(), total)
        self.pet.apply_tool_reaction(self.pet.character.react("waiting_input"), [], [])
        self.pet._on_touch_timeout()
        self.assertEqual(self.pet._cur_key, "attention")
        self.pet._start_pickup()
        self.pet._end_pickup()
        self.assertEqual(sum(self.pet._touch_poses.library.clip["durations"]), 1220)
        self.assertEqual(self.pet._touch_timer.interval(), 1220)

    def test_failure_gif_finishes_all_poses_before_its_feedback_deadline(self):
        self.pet._enter_file_import(False)
        renderer = self.pet._renderer
        self.assertEqual(renderer.movie.speed(), 74)
        self.assertEqual(self.pet._file_import_timer.interval(), 4400)
        QTest.qWait(2800)
        self.assertEqual(renderer.movie.currentFrameNumber(), 3)
        self.assertTrue(renderer._exhausted)
        self.assertTrue(self.pet._file_import_timer.isActive())
        self.assertEqual(renderer.state.key, "wrong")

    def test_manual_gaze_and_work_interrupts_remain_immediate(self):
        self.director.apply(InteractionPreferences(mode="entertainment"), persist=False)
        interval = self.director._ambient.remainingTime()
        self.assertTrue(51600 <= interval <= 60900)
        self.assertTrue(self.director.start("nap"))
        position = QPoint(self.pet.pos())
        self.pet.apply_tool_reaction(self.pet.character.react("thinking"), [], [])
        self.assertFalse(self.director.active)
        self.assertEqual(self.pet.pos(), position)
        self.assertEqual(self.pet._cur_key, "think")
        self.pet.move(1200, 300)
        self.pet.grab()
        self.assertEqual(self.pet._visible_base_frame.toImage(),
                         self.pet._pixmap.toImage().transformed(QTransform().scale(-1, 1)))
