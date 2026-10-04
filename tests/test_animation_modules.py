"""Local checks for mode profiles, meaningful timing and whole-body facing."""
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
from PySide6.QtGui import QImage, QPainter, QTransform
from PySide6.QtWidgets import QApplication
from test_interactions import FakeDesktop

from character.motion import GAITS, choose_gait, inward_facing, travel_seconds
from companion.preferences import (
    OPTIONAL_MODULES,
    InteractionPreferences,
    PreferenceStore,
)
from presentation.interactions import SCENE_MODULES, SCENES, InteractionDirector
from presentation.pet import ASSET_ROOT, DesktopPet
from presentation.settings_panel import SettingsWindow


class ModulePolicyTests(unittest.TestCase):
    def test_mode_profiles_are_independent_and_basic_cannot_be_disabled(self):
        prefs = InteractionPreferences()
        self.assertFalse(prefs.active_modules)
        self.assertTrue(prefs.module_enabled("basic"))
        custom = prefs.with_module("cursor", True).with_module("window", False, "entertainment")
        self.assertTrue(custom.module_enabled("cursor"))
        self.assertFalse(prefs.module_enabled("cursor"))
        self.assertFalse(custom.module_enabled("window", "entertainment"))
        self.assertTrue(custom.with_module("basic", False).module_enabled("basic"))
        self.assertEqual(set(SCENES), set(SCENE_MODULES))

    def test_legacy_choices_migrate_and_explicit_overrides_win(self):
        prefs = InteractionPreferences.from_dict({"mode": "entertainment", "walking": False,
                                                 "cursor_play": True, "minimize_pranks": True})
        self.assertFalse(prefs.module_enabled("movement"))
        self.assertTrue(prefs.module_enabled("system"))
        self.assertFalse(prefs.module_enabled("system", "focus"))
        self.assertTrue(prefs.with_module("movement", True).module_enabled("movement"))
        normalized = InteractionPreferences.from_dict({"module_profiles": {"focus": {
            "basic": False, "rest": "yes", "cursor": True, "unknown": True}}, "rest_seconds": 999})
        self.assertEqual(normalized.module_profiles["focus"], {"cursor": True})
        self.assertEqual(normalized.rest_seconds, 300)

    def test_profiles_persist_without_losing_adapter_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text(json.dumps({"adapter": {"provider": "local"}}))
            store = PreferenceStore(path)
            prefs = InteractionPreferences().with_module("rest", True).with_module("movement", False, "entertainment")
            store.save(replace(prefs, movement_pace="trot", rest_seconds=90))
            loaded = store.load()
            self.assertTrue(loaded.module_enabled("rest"))
            self.assertFalse(loaded.module_enabled("movement", "entertainment"))
            self.assertEqual((loaded.movement_pace, loaded.rest_seconds), ("trot", 90))
            self.assertEqual(json.loads(path.read_text())["adapter"], {"provider": "local"})

    def test_gaits_change_cadence_and_duration_without_an_arrival_hold(self):
        self.assertEqual([choose_gait(d) for d in (60, 220, 500)], ["walk", "trot", "run"])
        self.assertEqual(choose_gait(500, "walk"), "walk")
        self.assertGreater(GAITS["run"].speed / GAITS["run"].stride,
                           GAITS["trot"].speed / GAITS["trot"].stride)
        self.assertGreater(travel_seconds(220, "trot"), travel_seconds(220, "run"))
        self.assertLess(travel_seconds(2, "run"), .1)
        self.assertEqual(inward_facing(1200, 0, 1600), "left")
        self.assertEqual(inward_facing(-1100, -1600, 0), "right")


class ModulePresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.data = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"MORI_COMPANION_HOME": self.data.name})
        self.env.start()
        self.pet = DesktopPet()
        self.pet.show()
        self.pet._idle_start()
        self.pet.interactions.stop()
        self.desktop = FakeDesktop()
        self.now = [100.0]
        self.director = InteractionDirector(self.pet, ASSET_ROOT, self.pet.preferences, self.desktop,
                                           clock=lambda: self.now[0])
        self.director.store = PreferenceStore(Path(self.data.name) / "settings.json")
        self.pet.interactions = self.director
        self.bounds = patch.object(self.director, "_bounds", return_value=QRect(0, 0, 1600, 1000))
        self.bounds.start()
        self.pet.move(300, 300)
        self.app.processEvents()

    def tearDown(self):
        if self.pet._settings_window:
            self.pet._settings_window.deleteLater()
        self.pet._quitting = True
        self.pet._cleanup()
        self.pet.close()
        self.pet.deleteLater()
        self.app.processEvents()
        self.bounds.stop()
        self.env.stop()
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

    def test_custom_focus_allows_one_module_and_keeps_basic_feedback(self):
        prefs = InteractionPreferences().with_module("rest", True)
        self.director.apply(prefs, persist=False)
        self.assertTrue(self.director.start("nap"))
        self.assertFalse(self.director.start("wander"))
        self.pet._touch_region = "head"
        self.pet._do_touch()
        self.assertTrue(self.pet._touch_poses.active)
        self.pet.apply_tool_reaction(self.pet.character.react("waiting_input"), [], [])
        self.pet._on_touch_timeout()
        self.assertEqual(self.pet._cur_key, "attention")
        self.assertFalse(self.pet._dialogue_timer.isActive())

    def test_settings_save_both_profiles_and_restore_only_current_mode(self):
        window = SettingsWindow(self.pet)
        self.pet._settings_window = window
        window.module_checks["focus", "cursor"].setChecked(True)
        window.module_checks["entertainment", "movement"].setChecked(False)
        window.pace.setCurrentIndex(window.pace.findData("run"))
        window.rest_time.setValue(65)
        loaded = self.director.store.load()
        self.assertTrue(loaded.module_enabled("cursor", "focus"))
        self.assertFalse(loaded.module_enabled("movement", "entertainment"))
        self.assertEqual((loaded.rest_seconds, loaded.movement_pace), (65, "run"))
        window.mode.setCurrentIndex(window.mode.findData("entertainment"))
        self.assertFalse(self.pet.preferences.module_enabled("movement"))
        window.reset_modules()
        self.assertTrue(self.pet.preferences.module_enabled("movement"))
        self.assertTrue(self.pet.preferences.module_enabled("cursor", "focus"))
        self.assertFalse(window.module_checks["focus", "basic"].isEnabled())
        self.assertIn("已恢复", window.saved.text())

    def test_all_optional_modules_off_stop_story_and_attention_timers(self):
        prefs = InteractionPreferences(mode="entertainment")
        for name in OPTIONAL_MODULES:
            prefs = prefs.with_module(name, False)
        self.director.apply(prefs, persist=False)
        self.assertFalse(self.director._ambient.isActive())
        self.assertFalse(self.director.attention._timer.isActive())
        for scene in SCENES:
            self.assertFalse(self.director.start(scene))
        self.pet._touch_region = "tail"
        self.pet._do_touch()
        self.assertTrue(self.pet._touch_poses.active)

    def test_system_module_is_independent_and_revoking_it_cancels_pending_operation(self):
        prefs = InteractionPreferences(minimize_pranks=True).with_module("system", True)
        self.director.apply(prefs, persist=False)
        self.assertFalse(prefs.module_enabled("window"))
        self.assertTrue(self.director.start("minimize"))
        for _ in range(3):
            self.advance(self.director.steps[self.director.index].seconds + .01)
        self.assertEqual(self.desktop.pending, "ordinary")
        self.director.apply(prefs.with_module("system", False), persist=False)
        self.desktop.commit()
        self.assertFalse(self.desktop.minimized)
        self.assertFalse(self.director.active)

    def test_system_module_is_the_only_operation_switch(self):
        self.director.apply(InteractionPreferences().with_module("system", True), persist=False)
        self.assertTrue(self.director.start("minimize"))
        self.assertFalse(self.desktop.pending)

    def test_restore_remains_available_after_system_module_is_off(self):
        self.director.restore_identity = "ordinary"
        with patch.object(self.desktop, "stop", wraps=self.desktop.stop) as stop:
            self.director.restore_window()
        self.assertEqual(self.desktop.restored, ["ordinary"])
        self.assertFalse(self.director.restore_identity)
        stop.assert_called_once()

    def test_run_stops_gait_at_destination_and_uses_actual_leftward_displacement(self):
        self.director.apply(InteractionPreferences(mode="entertainment"), persist=False)
        self.pet.move(1000, 300)
        target = QPoint(350, 300)
        with patch.object(self.director, "_random_destination", return_value=target):
            self.assertTrue(self.director.start("run"))
        seconds = self.director.steps[0].seconds
        self.assertAlmostEqual(seconds, 650 / GAITS["run"].speed)
        self.advance(seconds / 2)
        self.render()
        self.assertEqual(self.director.direction, "left")
        self.assertEqual(self.director._walk_poses._name, "walk-left")
        self.advance(seconds / 2 + .01)
        self.assertEqual(self.pet.pos(), target)
        self.assertNotEqual(self.director.steps[self.director.index].pose, "walk")
        self.advance(.2)
        self.assertEqual(self.pet.pos(), target)

    def test_nap_has_long_sleep_and_wakes_before_returning_to_idle(self):
        self.director.apply(InteractionPreferences(mode="entertainment", rest_seconds=60), persist=False)
        self.assertTrue(self.director.start("nap"))
        self.advance(.53)
        self.assertEqual(self.director.steps[self.director.index].pose, "nap-sleep")
        self.assertEqual(self.director._frame.interval(), 200)
        self.advance(20)
        self.render()
        self.assertTrue(self.director.active)
        self.assertEqual(self.director.steps[self.director.index].seconds, 60)
        self.advance(40.01)
        self.assertEqual(self.director.steps[self.director.index].pose, "nap-wake")
        self.assertEqual(self.director._frame.interval(), 40)
        self.advance(.79)
        self.assertFalse(self.director.active)
        self.assertFalse(self.director._frame.isActive())

    def test_work_interrupts_long_rest_in_place(self):
        self.director.apply(InteractionPreferences().with_module("rest", True), persist=False)
        self.assertTrue(self.director.start("nap"))
        self.advance(.6)
        position = self.pet.pos()
        self.pet.apply_tool_reaction(self.pet.character.react("thinking"), [], [])
        self.assertFalse(self.director.active)
        self.assertEqual(self.pet.pos(), position)
        self.assertEqual(self.pet._cur_key, "think")

    def test_idle_work_and_touch_mirror_together_with_head_and_tail_hits(self):
        self.pet.move(1200, 300)
        self.pet._set_image("think")
        self.pet.grab()
        original = self.pet._pixmap.toImage()
        expected = original.transformed(QTransform().scale(-1, 1))
        self.assertEqual(self.pet._visible_base_frame.toImage(), expected)
        self.assertEqual(self.pet._hit_touch_region(QPoint(90, 60)), "head")
        self.assertEqual(self.pet._hit_touch_region(QPoint(220, 130)), "tail")
        self.pet._touch_region = "head"
        self.pet._do_touch()
        self.pet.grab()
        self.assertTrue(self.pet._visible_base_reversed)
        self.assertEqual(self.pet._visible_base_frame.toImage(),
                         self.pet._touch_poses.frame.toImage().transformed(QTransform().scale(-1, 1)))

    def test_mirrored_window_anchors_and_footprints_keep_contact(self):
        self.director.apply(InteractionPreferences(mode="entertainment"), persist=False)
        target = replace(self.desktop.active_window, geometry=QRect(920, 300, 580, 500))
        self.desktop.windows[target.identity] = target
        self.assertTrue(self.director.start("peek-top", target))
        self.advance(self.director.steps[0].seconds + .01)
        self.render()
        self.assertTrue(self.director.displayed_reversed)
        anchor = self.director._window_poses.anchor_at("window-peek-top", 0, "left")
        self.assertEqual(self.pet.pos() + anchor, QPoint(target.geometry.center().x(), target.geometry.top()))
        self.assertFalse(self.director._window_fits("peek-top", replace(target, geometry=QRect(920, 40, 580, 500))))

    def test_focus_gaze_on_right_side_keeps_body_inward_and_eyes_toward_cursor(self):
        self.pet.move(1200, 300)
        self.director.apply(InteractionPreferences().with_module("cursor", True), persist=False)
        attention = self.director.attention
        with patch("presentation.cursor_attention.QCursor.pos", return_value=QPoint(1110, 390)):
            attention.advance()
            self.now[0] += .3
            attention.advance()
        self.assertEqual(attention.interest.direction, "left")
        self.render()
        self.assertTrue(self.director.displayed_reversed)
        self.assertEqual(attention.frame.toImage(),
                         attention.library.frames[1].toImage().transformed(QTransform().scale(-1, 1)))
        self.assertFalse(self.pet.preferences.module_enabled("movement"))

    def test_mirrored_drag_keeps_grab_point_and_sway_in_screen_direction(self):
        self.pet.move(1200, 300)
        self.pet._drag_offset = QPoint(90, 60)
        self.pet._press_global = QPoint(1290, 360)
        player = self.pet._drag_poses
        player.clock = lambda: self.now[0]
        self.pet._start_pickup()
        self.assertTrue(player.mirrored)
        self.assertEqual(player.pivot.toPoint(), QPoint(190, 60))
        self.now[0] += .2
        player.sample(QPoint(1315, 360))
        self.assertLess(player.dynamics.vx, 0)
        self.assertEqual(player.pose_index, 2)
        self.pet.grab()
        self.assertTrue(self.pet._visible_base_reversed)

    def test_work_preempts_running_and_keeps_its_actual_position(self):
        self.director.apply(InteractionPreferences(mode="entertainment"), persist=False)
        self.assertTrue(self.director.start("run"))
        self.advance(.4)
        position = self.pet.pos()
        self.pet.apply_tool_reaction(self.pet.character.react("generating"), [], [])
        self.assertFalse(self.director.active)
        self.assertEqual(self.pet.pos(), position)
        self.assertEqual(self.pet._cur_key, "eat")
        self.assertFalse(self.director.start("dance"))

    def test_retargeted_window_walk_turns_body_with_real_movement(self):
        self.director.apply(InteractionPreferences(mode="entertainment"), persist=False)
        target = replace(self.desktop.active_window, geometry=QRect(700, 300, 600, 500))
        self.desktop.windows[target.identity] = target
        self.assertTrue(self.director.start("peek-right", target))
        self.advance(self.director.steps[0].seconds * .7)
        self.assertEqual(self.director.direction, "right")
        target = replace(target, geometry=QRect(60, 300, 600, 500))
        self.desktop.windows[target.identity] = target
        self.director._follow_target()
        previous = self.pet.pos()
        self.advance(.16)
        self.assertLess(self.pet.x(), previous.x())
        self.assertLess((self.pet.pos() - previous).manhattanLength(), 30)
        self.render()
        self.assertEqual(self.director.direction, "left")
        self.assertEqual(self.director._walk_poses._name, "walk-left")
