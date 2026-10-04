"""Local checks of native readback, cancellation, ink coordinates and shared policy."""
import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "src"), str(Path(__file__).parent)]
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QPoint, QPointF, QRect, Qt, Signal
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication, QCheckBox
from test_interactions import FakeDesktop

from companion.preferences import InteractionPreferences, PreferenceStore
from presentation.doodles import Doodle, StrokeTrace, drawing_strokes
from presentation.interactions import InteractionDirector
from presentation.pet import ASSET_ROOT, DesktopPet
from presentation.settings_panel import SettingsWindow
from presentation.system_actions import geometry_goal


class MovingDesktop(FakeDesktop):
    geometry_finished = Signal(str, bool)

    def __init__(self):
        super().__init__()
        self.moves = []
        self.geometry_busy = False

    def set_geometry(self, window, geometry):
        if not self.authorize_geometry(window.identity):
            return False
        self.moves.append(QRect(geometry))
        self.geometry_busy = True
        return True

    def acknowledge(self):
        self.active_window = replace(self.active_window, geometry=self.moves[-1])
        self.windows[self.active_window.identity] = self.active_window
        self.geometry_busy = False
        self.changed.emit(self.active_window)
        self.geometry_finished.emit(self.active_window.identity, True)


class InkGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_window_marks_scale_and_screen_drawings_stay_fixed(self):
        trace = StrokeTrace(drawing_strokes("arrow"))
        free = Doodle(trace, QPointF(210, 260), progress=trace.length)
        bound = Doodle(trace, QPointF(210, 260), "window", QRect(200, 240, 400, 300),
                       QRect(200, 240, 400, 300), progress=trace.length)
        before, fixed = bound.tip, free.tip
        bound.geometry = QRect(300, 340, 800, 600)
        self.assertEqual(bound.tip, QPointF(300 + (before.x() - 200) * 2, 340 + (before.y() - 240) * 2))
        self.assertEqual(free.tip, fixed)
        bound.completed = 10
        self.assertEqual(bound.opacity(16), 1)
        self.assertAlmostEqual(bound.opacity(16.5), .5)
        self.assertEqual(bound.opacity(17), 0)

    def test_trace_does_not_connect_separate_strokes_and_nib_matches_line(self):
        trace = StrokeTrace([[QPointF(0, 0), QPointF(10, 0)], [QPointF(20, 0), QPointF(20, 10)]])
        path, tip = trace.partial(25)
        self.assertEqual(tip, QPointF(20, 5))
        self.assertEqual(path.elementCount(), 4)
        self.assertTrue(path.elementAt(2).isMoveTo())
        self.assertTrue(StrokeTrace(drawing_strokes("text", "你好 Mori")).edges)

    def test_geometry_destinations_respect_work_area(self):
        bounds = QRect(-1600, 0, 1600, 1000)
        rect = QRect(-500, 300, 450, 400)
        self.assertTrue(bounds.contains(geometry_goal("move-window", rect, bounds)))
        self.assertEqual(geometry_goal("snap-left", rect, bounds), QRect(-1600, 0, 800, 1000))


class SystemChoreographyTests(unittest.TestCase):
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
        self.desktop = MovingDesktop()
        self.now = [100.]
        self.d = InteractionDirector(self.pet, ASSET_ROOT, InteractionPreferences().with_module("system", True),
                                     self.desktop, clock=lambda: self.now[0])
        self.d.store = PreferenceStore(Path(self.data.name) / "settings.json")
        self.pet.interactions = self.d
        self.pet.preferences = self.d.preferences
        self.pet.move(300, 300)
        self.app.processEvents()

    def tearDown(self):
        self.pet._quitting = True
        self.pet._cleanup()
        self.pet.close()
        self.pet.deleteLater()
        self.app.processEvents()
        self.env.stop()
        self.data.cleanup()

    def advance(self, seconds):
        self.now[0] += seconds
        self.d.advance()

    def test_grip_follows_confirmed_window_geometry_and_waits_before_release(self):
        self.assertTrue(self.d.start("move-window"))
        self.advance(self.d.steps[0].seconds + .01)
        self.advance(.41)
        before = self.d.system.contact
        self.advance(.5)
        self.assertEqual(self.d.system.contact, before)
        self.assertTrue(self.desktop.moves)
        self.desktop.acknowledge()
        self.assertNotEqual(self.d.system.contact, before)
        image = QImage(280, 230, QImage.Format_ARGB32_Premultiplied)
        image.fill(0)
        painter = QPainter(image)
        self.assertTrue(self.d.draw(painter))
        painter.end()
        clip = self.d.system.library.clip
        anchor = QPoint(*clip["anchors"][1 + int((self.d.target.geometry.topLeft() - self.d.system.origin_geometry.topLeft()).manhattanLength() / 28) % 2])
        self.assertEqual(self.pet.pos() + self.d.displayed_origin + anchor, self.d.system.contact.toPoint())
        self.advance(3)
        self.assertEqual(self.d.steps[self.d.index].pose, "system-drag")
        self.desktop.acknowledge()
        self.advance(.01)
        self.assertEqual(self.d.steps[self.d.index].pose, "system-release")

    def test_user_interactive_move_interrupts_mori_in_place(self):
        self.assertTrue(self.d.start("move-window"))
        self.advance(self.d.steps[0].seconds + .01)
        position = self.pet.pos()
        window = replace(self.desktop.active_window, interactive=True)
        self.desktop.windows[window.identity] = window
        self.desktop.changed.emit(window)
        self.assertFalse(self.d.active)
        self.assertEqual(self.pet.pos(), position)
        self.assertFalse(self.d.system.authorize_geometry(window.identity))

    def test_autonomous_move_uses_active_window_when_weighted_target_is_background(self):
        self.d.apply(replace(self.d.preferences, mode="entertainment").with_module("system", True), persist=False)
        background = replace(self.desktop.active_window, identity="background", active=False, minimized=True)
        with patch.object(self.d, "select_target", return_value=background), \
                patch.object(self.d.rng, "choice", return_value="move-window"), \
                patch.object(self.d, "start", return_value=True) as start:
            self.d._choose()
        self.assertEqual(start.call_args.kwargs["target"].identity, self.desktop.active_window.identity)

    def test_ink_expires_and_disabling_module_clears_windows_and_timers(self):
        self.assertTrue(self.d.system.start("doodle-screen", point=QPoint(400, 360), kind="star"))
        self.assertTrue(self.d.system.ink.windows)
        for window in self.d.system.ink.windows:
            self.assertTrue(window.windowFlags() & Qt.WindowTransparentForInput)
        self.advance(self.d.steps[0].seconds + .01)
        self.advance(.46)
        self.advance(self.d.steps[self.d.index].seconds + .01)
        self.assertIsNotNone(self.d.system.doodle.completed)
        completed = self.d.system.doodle.completed
        self.advance(.56)
        self.assertFalse(self.d.active)
        self.now[0] = completed + 6.5
        self.d.system.ink.advance()
        self.assertTrue(self.d.system.ink.drawings)
        self.now[0] = completed + 7.1
        self.d.system.ink.advance()
        self.assertFalse(self.d.system.ink.drawings)
        self.assertFalse(self.d.system.ink._timer.isActive())
        self.assertTrue(self.d.system.start("doodle-screen", point=QPoint(400, 360)))
        self.d.apply(self.d.preferences.with_module("system", False), persist=False)
        self.assertFalse(self.d.active)
        self.assertFalse(self.d.system.ink.windows)
        self.assertFalse(self.d.system.ink.drawings)

    def test_only_shared_switch_is_displayed_and_legacy_gate_is_retired_on_save(self):
        window = SettingsWindow(self.pet)
        self.pet._settings_window = window
        self.assertNotIn("minimize_pranks", window.checks)
        self.assertFalse(any("恶作剧" in check.text() for check in window.findChildren(QCheckBox)))
        self.assertTrue(window.system_execute.isEnabled())
        self.d.store.save(self.d.preferences)
        data = json.loads(self.d.store.path.read_text())["interactions"]
        self.assertNotIn("minimize_pranks", data)
        self.assertTrue(self.d.store.load().module_enabled("system"))

    def test_position_picker_survives_window_observer_updates_and_closes_when_disabled(self):
        self.d.system.ink.pick(lambda point: None)
        self.desktop.changed.emit(self.desktop.active_window)
        self.assertTrue(self.d.system.ink.pickers)
        self.assertFalse(self.d._can_start())
        self.d.apply(self.d.preferences.with_module("system", False), persist=False)
        self.assertFalse(self.d.system.ink.pickers)

    def test_scroll_uses_selected_region_and_yields_if_pointer_moves(self):
        self.d.system.input.available = True
        point = QPoint(500, 450)
        with patch("presentation.system_actions.QCursor.pos", return_value=point), \
                patch.object(self.d.system.input, "scroll", return_value=True) as scroll:
            self.assertTrue(self.d.system.start("scroll-down", point=point))
            self.advance(self.d.steps[0].seconds + .01)
            self.advance(.41)
            self.advance(.65)
            self.assertAlmostEqual(scroll.call_args.args[0], 60)
            self.assertFalse(scroll.call_args.kwargs["finish"])
            with patch("presentation.system_actions.QCursor.pos", return_value=point + QPoint(1, 0)):
                self.advance(.01)
            self.assertFalse(self.d.active)
            self.assertEqual(scroll.call_args.args[0], 0)
            self.assertTrue(scroll.call_args.kwargs["finish"])

    def test_top_edge_buttons_keep_full_character_and_correct_finger_contact(self):
        target = replace(self.desktop.active_window, geometry=QRect(0, 0, 800, 760), maximized=True)
        self.desktop.active_window = target
        self.desktop.windows[target.identity] = target
        self.assertTrue(self.d.start("maximize"))
        self.advance(self.d.steps[0].seconds + .01)
        self.advance(self.d.steps[self.d.index].seconds + .01)
        self.advance(.85)
        image = QImage(280, 230, QImage.Format_ARGB32_Premultiplied)
        image.fill(0)
        painter = QPainter(image)
        self.assertTrue(self.d.draw(painter))
        painter.end()
        self.assertEqual(self.d.system.library._name, "system-reach")
        anchor = QPoint(*self.d.system.library.clip["anchors"][2])
        if self.d._window_facing == "left":
            anchor.setX(280 - anchor.x())
        from presentation.desktop_interaction import KWinDesktop
        self.assertEqual(self.pet.pos() + anchor, KWinDesktop.button_point(target, "maximize"))
        self.assertIsNone(self.desktop.pending)


if __name__ == "__main__":
    unittest.main()
