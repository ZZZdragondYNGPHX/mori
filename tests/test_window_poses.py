"""Local window-attachment checks; no real desktop windows are modified."""
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication
from test_interactions import FakeDesktop

from character.window_motion import choose_peek_side, clip_index
from companion.preferences import InteractionPreferences
from presentation.desktop_interaction import KWinDesktop
from presentation.interactions import InteractionDirector
from presentation.pet import ASSET_ROOT, DesktopPet
from presentation.window_poses import WindowPoseLibrary


class WindowPolicyTests(unittest.TestCase):
    def test_available_edges_and_alternation(self):
        self.assertEqual(choose_peek_side(300, 300, "left"), "right")
        self.assertEqual(choose_peek_side(300, 300, "right"), "left")
        self.assertEqual(choose_peek_side(20, 300), "right")
        self.assertEqual(choose_peek_side(300, 20), "left")

    def test_nonloop_recovery_holds_and_leg_cycle_wraps(self):
        clip = {"durations": [200, 300, 400], "loop": False}
        self.assertEqual(clip_index(clip, 9999), 2)
        clip["loop"] = True
        self.assertEqual(clip_index(clip, 910), 0)


class WindowPoseTests(unittest.TestCase):
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
        image = QImage(280, 230, QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        drawn = self.director.draw(painter)
        painter.end()
        self.assertTrue(drawn)
        self.assertTrue(any(bytes(image.constBits())))
        return image

    def move_window(self, dx, dy):
        self.window = replace(self.window, geometry=self.window.geometry.translated(dx, dy))
        self.desktop.windows[self.window.identity] = self.window
        self.desktop.active_window = self.window
        self.desktop.changed.emit(self.window)
        self.director._follow_target()

    def assert_support(self):
        director = self.director
        name = director.steps[director.index].pose
        clip = director._window_poses.load(name)
        index = clip_index(clip, (self.now[0] - director._started) * 1000)
        anchor = QPoint(*clip["anchors"][index])

        class SupportPainter(QPainter):
            def drawPixmap(self, point, pixmap):
                self.support = self.transform().map(point + anchor)
                super().drawPixmap(point, pixmap)

        image = QImage(280, 230, QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        painter = SupportPainter(image)
        self.assertTrue(director.draw(painter))
        actual = painter.support + self.pet.pos()
        painter.end()
        self.assertEqual(actual, QPoint(self.window.geometry.center().x(), self.window.geometry.top()))

    def test_both_peeks_clip_only_window_interior_and_clear_on_exit(self):
        for previous, side in (("left", "right"), ("right", "left")):
            self.director._last_peek_side = previous
            self.assertTrue(self.director.start("peek", self.window))
            self.advance(self.director.steps[self.director.index].seconds + .01)
            image = self.render()
            self.assertEqual(self.director._peek_side, side)
            clip = self.director._clip_rect
            hidden_x = clip.left() - 1 if side == "right" else clip.right() + 1
            self.assertTrue(all(image.pixelColor(hidden_x, y).alpha() == 0 for y in range(230)))
            self.assertEqual(self.pet._hit_touch_region(QPoint(hidden_x, 70)), "body")
            self.advance(7.01)
            self.assertEqual(self.director.steps[self.director.index].pose, "walk")
            self.assertIsNone(self.director._clip_rect)
            self.assertTrue(self.pet.mask().isEmpty())
            self.render()
            self.director.cancel(restore_position=False)
        self.director._last_peek_side = "left"
        self.director.start("peek", self.window)
        self.advance(self.director.steps[self.director.index].seconds + .01)
        self.move_window(1599 - self.window.geometry.right(), 0)
        self.assertFalse(self.director.active)
        self.assertFalse(self.director._window_poses.frames)

    def test_peek_speech_avoids_software_and_pet_and_holds_while_pose_animates(self):
        screen = SimpleNamespace(availableGeometry=lambda: QRect(0, 0, 1600, 1000))
        with patch.object(self.pet, "screen", return_value=screen):
            for side in ("left", "right"):
                self.assertTrue(self.director.start("peek-" + side, self.window))
                self.advance(self.director.steps[self.director.index].seconds + .01)
                self.render()
                self.pet._show_speech("任务有新的进度。")
                bubble = self.pet._bubble
                geometry = bubble.geometry()
                self.assertFalse(geometry.intersects(self.window.geometry))
                self.assertFalse(geometry.intersects(self.pet.geometry()))
                self.assertTrue(screen.availableGeometry().contains(geometry))
                head = self.pet.mapToGlobal(self.pet.speech_anchor())
                self.assertLess(abs(bubble.x() + bubble._body_rect().center().x() - head.x()),
                                bubble._diameter)
                with patch.object(bubble, "setGeometry", wraps=bubble.setGeometry) as configure:
                    for _ in range(10):
                        self.advance(.10)
                        self.render()
                        bubble.follow(self.pet)
                        self.app.processEvents()
                    self.assertEqual(bubble.geometry(), geometry)
                    configure.assert_not_called()
                self.director.cancel(restore_position=False)
                self.pet.grab()
                self.pet._show_speech("回到桌面继续陪你。")
                self.assertEqual(self.pet._speech_anchor_key[0], "base")

    def test_seating_leg_swing_and_rising_keep_the_same_support(self):
        self.director.start("sit", self.window)
        self.advance(self.director.steps[self.director.index].seconds + .01)
        self.assert_support()
        self.render()
        self.director.direction = "left"
        self.assertEqual(self.pet._hit_touch_region(QPoint(175, 65)), "head")
        self.advance(.3)
        self.assert_support()
        self.advance(self.director.steps[self.director.index].seconds - .3 + .01)
        self.assertEqual(self.director.steps[self.director.index].pose, "window-swing")
        for seconds in (.12, .4, .4):
            self.advance(seconds)
            self.assert_support()
        self.advance(self.director.steps[self.director.index].seconds - .92 + .01)
        self.assertEqual(self.director.steps[self.director.index].pose, "window-rise")
        self.assert_support()
        self.advance(.4)
        self.assert_support()
        self.advance(.7)
        self.assert_support()
        self.assertEqual(self.pet.pos() + QPoint(175, 222),
                         QPoint(self.window.geometry.center().x(), self.window.geometry.top()))

    def test_window_move_during_rise_keeps_phase_and_departs_smoothly(self):
        self.director.start("sit", self.window)
        self.advance(self.director.steps[self.director.index].seconds + .01)
        self.advance(self.director.steps[self.director.index].seconds + .01)
        self.advance(self.director.steps[self.director.index].seconds + .01)
        started = self.director._started
        self.move_window(90, -20)
        self.assertEqual(self.director._started, started)
        self.assert_support()
        self.advance(self.director.steps[self.director.index].seconds + .01)
        self.assertEqual(self.director.steps[self.director.index].pose, "walk")
        origin = self.pet.pos()
        self.advance(.04)
        self.assertLess((self.pet.pos() - origin).manhattanLength(), 5)

    def test_plain_button_scene_reaches_contact_and_never_minimizes(self):
        self.assertFalse(self.pet.preferences.minimize_pranks)
        self.assertTrue(self.director.start("press", self.window))
        self.advance(self.director.steps[self.director.index].seconds + .01)
        self.advance(self.director.steps[self.director.index].seconds + .01)
        self.advance(self.director.steps[self.director.index].seconds - .1)
        self.render()
        self.assertEqual(self.director._window_poses._name, "window-press")
        contact = self.pet.pos() + self.director._window_poses.contact
        if self.director._window_facing == "left":
            contact.setX(contact.x() + 280 - 2 * self.director._window_poses.contact.x())
        self.assertEqual(contact, KWinDesktop.button_point(self.window))
        self.advance(.21)
        self.assertEqual(self.director.steps[self.director.index].pose, "window-press-recover")
        self.assertIsNone(self.desktop.pending)
        self.assertEqual(self.desktop.minimized, [])

    def test_button_motion_tracks_window_without_restarting_contact_phase(self):
        self.director.start("press", self.window)
        self.advance(self.director.steps[self.director.index].seconds + .01)
        self.advance(self.director.steps[self.director.index].seconds + .01)
        started = self.director._started
        self.move_window(60, 25)
        self.assertEqual(self.director._started, started)
        contact = self.pet.pos() + self.director._window_poses.contact
        if self.director._window_facing == "left":
            contact.setX(contact.x() + 280 - 2 * self.director._window_poses.contact.x())
        self.assertEqual(contact, KWinDesktop.button_point(self.window))

    def test_real_minimize_still_requires_preferences_and_cancels_on_grab(self):
        self.assertFalse(self.director.start("minimize"))
        self.director.apply(replace(self.pet.preferences, minimize_pranks=True), persist=False)
        self.assertTrue(self.director.start("minimize"))
        self.advance(self.director.steps[self.director.index].seconds + .01)
        self.advance(4.01)
        self.advance(2.01)
        self.assertEqual(self.desktop.pending, self.window.identity)
        self.render()
        self.pet._start_pickup()
        self.desktop.commit()
        self.assertEqual(self.desktop.minimized, [])
        self.assertFalse(self.director.active)
        self.assertFalse(self.director._window_poses.frames)

    def test_focus_and_hidden_window_stop_attached_animation_timers(self):
        self.director.start("sit", self.window)
        self.advance(self.director.steps[self.director.index].seconds + .01)
        self.render()
        self.pet.hide()
        self.assertFalse(self.director.active)
        self.assertFalse(self.director._frame.isActive())
        self.assertFalse(self.director._follow.isActive())
        self.assertFalse(self.director._window_poses.frames)
        self.pet.show()
        self.director.apply(replace(self.pet.preferences, mode="focus"), persist=False)
        self.assertFalse(self.director.start("press", self.window))
        self.assertFalse(self.director._ambient.isActive())

    def test_missing_window_artwork_uses_legacy_sitting(self):
        self.director._window_poses = WindowPoseLibrary(Path(self.data.name))
        self.director.start("sit", self.window)
        self.advance(self.director.steps[self.director.index].seconds + .01)
        self.render()
        self.assertFalse(self.director._window_poses.frames)

    def test_edge_without_visible_room_reschedules_instead_of_stalling(self):
        self.window = replace(self.window, geometry=QRect(0, 0, 1600, 1000))
        self.desktop.windows[self.window.identity] = self.window
        self.desktop.active_window = self.window
        self.director._ambient.stop()
        with (patch.object(self.director, "select_target", return_value=self.window),
              patch.object(self.director.rng, "choice", return_value="peek")):
            self.director._choose()
        self.assertFalse(self.director.active)
        self.assertTrue(self.director._ambient.isActive())


if __name__ == "__main__":
    unittest.main()
