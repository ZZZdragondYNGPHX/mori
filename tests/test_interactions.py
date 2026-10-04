import json
import os
import random
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PySide6.QtCore import QObject, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from character.window_motion import clip_index
from companion.preferences import InteractionPreferences, PreferenceStore
from presentation.desktop_interaction import DesktopWindow, KWinDesktop
from presentation.interactions import InteractionDirector
from presentation.pet import ASSET_ROOT, DesktopPet


class FakeDesktop(QObject):
    changed = Signal(object)
    operation_finished = Signal(str, bool)
    availability_changed = Signal()

    def __init__(self):
        super().__init__()
        self.active_window = DesktopWindow('ordinary', 'org.kde.konsole', QRect(200, 240, 400, 480),
                                           True, True, True, False, False, os.getpid() + 100)
        self.windows = {'ordinary': self.active_window}
        self.available = True
        self.reason = 'Test desktop'
        self.pending = None
        self.minimized = []
        self.restored = []

    def start(self):
        pass

    def stop(self):
        self.cancel_pending()

    def cancel_pending(self):
        self.pending = None

    def minimize(self, window):
        self.pending = window.identity
        return True

    def commit(self):
        identity, self.pending = self.pending, None
        if identity is not None and self.authorize(identity):
            self.minimized.append(identity)
            self.operation_finished.emit(identity, True)

    def restore(self, identity):
        self.restored.append(identity)
        self.operation_finished.emit(identity, True)
        return True


class InteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.data = tempfile.TemporaryDirectory()
        self.now = [100.0]
        self.pet = DesktopPet()
        self.pet.show()
        self.pet._idle_start()
        self.pet.interactions.stop()
        self.desktop = FakeDesktop()
        self.director = InteractionDirector(self.pet, ASSET_ROOT, self.pet.preferences,
                                           self.desktop, clock=lambda: self.now[0])
        self.director.store = PreferenceStore(Path(self.data.name) / 'settings.json')
        self.pet.interactions = self.director
        self.app.processEvents()

    def tearDown(self):
        self.pet._quitting = True
        self.pet._cleanup()
        self.pet.close()
        self.pet.deleteLater()
        self.app.processEvents()
        self.data.cleanup()

    def entertain(self, **kwargs):
        self.director.apply(InteractionPreferences(mode='entertainment', **kwargs))

    def advance(self, seconds):
        self.now[0] += seconds
        self.director.advance()

    def reach_commit(self):
        self.entertain(minimize_pranks=True)
        self.assertTrue(self.director.start('minimize'))
        self.advance(self.director.steps[self.director.index].seconds + .1)
        self.advance(4.1)
        self.advance(2.1)
        self.assertEqual(self.desktop.pending, 'ordinary')

    def test_focus_keeps_state_animation_but_suppresses_proactive_speech(self):
        reaction = self.pet.character.react('thinking')
        self.pet.apply_tool_reaction(reaction, [], [])
        self.assertEqual(self.pet._cur_key, 'think')
        self.assertIsNone(self.pet._bubble)
        self.assertFalse(self.pet._dialogue_timer.isActive())
        self.assertFalse(self.director._ambient.isActive())
        self.pet._show_speech('用户主动触摸的反馈')
        self.assertIsNotNone(self.pet._bubble)

    def test_mode_switch_cancels_motion_and_does_not_replay_speech(self):
        self.entertain()
        origin = self.pet.pos()
        self.assertTrue(self.director.start('wander'))
        self.advance(1)
        self.assertTrue(self.director._frame.isActive())
        self.director.apply(replace(self.pet.preferences, mode='focus'))
        self.assertFalse(self.director.active)
        self.assertEqual(self.pet.pos(), origin)
        self.assertEqual(self.pet._state, 'idle')
        self.assertFalse(self.director._frame.isActive())
        self.assertFalse(self.director._ambient.isActive())
        self.assertIsNone(self.pet._bubble)

    def test_wander_chooses_varied_positions_and_stays_at_each_stop(self):
        self.entertain(dialogue=False)
        self.director.rng = random.Random(31)
        stops, walks, pauses = [], [], []
        bounds = QRect(0, 0, 1280, 720)
        self.pet.move(500, 250)
        with patch.object(self.director, '_bounds', return_value=bounds):
            for _ in range(12):
                origin = self.pet.pos()
                self.assertTrue(self.director.start('wander'))
                walk = self.director.steps[0]
                walks.append(walk.seconds)
                self.advance(walk.seconds + .01)
                stop = self.pet.pos()
                self.assertNotEqual(stop, origin)
                self.assertTrue(bounds.contains(self.pet.geometry()))
                pause = self.director.steps[1]
                pauses.append(pause.seconds)
                self.advance(pause.seconds + .01)
                self.assertFalse(self.director.active)
                self.assertEqual(self.pet.pos(), stop)
                self.assertFalse(self.director._frame.isActive())
                self.assertTrue(self.director._ambient.isActive())
                stops.append(stop)
        self.assertGreater(len({p.x() for p in stops}), 6)
        self.assertGreater(len({p.y() for p in stops}), 6)
        self.assertGreater(len(set(walks)), 6)
        self.assertGreater(len(set(pauses)), 6)

    def test_cursor_play_finishes_where_it_arrived(self):
        self.entertain(dialogue=False)
        self.pet.move(20, 20)
        with patch('presentation.interactions.QCursor.pos', return_value=QPoint(350, 250)):
            self.assertTrue(self.director.start('cursor'))
            while self.director.active and self.director.steps[self.director.index].pose != 'mouse-settle':
                self.advance(.08)
            destination = QPoint(self.pet.pos())
            self.advance(self.director.steps[self.director.index].seconds + .01)
            self.assertFalse(self.director.active)
            self.assertEqual(self.pet.pos(), destination)

    def test_sit_rejects_missing_headroom_and_keeps_support_when_it_fits(self):
        self.entertain(dialogue=False)
        bounds = QRect(-1280, 40, 1280, 720)
        with patch.object(self.director, '_bounds', return_value=bounds):
            for top in (40, 65, 350):
                with self.subTest(top=top):
                    target = replace(self.desktop.active_window, geometry=QRect(-1100, top, 700, 400))
                    self.desktop.windows[target.identity] = target
                    if top < 350:
                        self.assertFalse(self.director.start('sit', target=target))
                        self.assertFalse(self.director.active)
                        continue
                    self.assertTrue(self.director.start('sit', target=target))
                    self.advance(self.director.steps[self.director.index].seconds + .1)
                    support = self.director._sit_anchor()
                    self.assertEqual((self.pet.pos() + support).y(), top)
                    self.assertEqual((self.pet.pos() + support).x(), target.geometry.center().x())
                    self.advance(.2)
                    self.assertEqual((self.pet.pos() + support).y(), top)
                    self.director.cancel()

    def test_sit_sways_about_support_and_tracks_window_without_teleporting_on_exit(self):
        self.entertain(dialogue=False)
        target = replace(self.desktop.active_window, geometry=QRect(100, 220, 500, 400))
        self.desktop.windows[target.identity] = target
        self.assertTrue(self.director.start('sit', target=target))
        self.advance(self.director.steps[self.director.index].seconds + .1)
        target = replace(target, geometry=QRect(120, 260, 500, 400))
        self.desktop.windows[target.identity] = target
        self.desktop.changed.emit(target)
        self.director._follow_target()
        self.assertEqual(self.pet.pos() + self.director._sit_anchor(), QPoint(target.geometry.center().x(), 260))

        class SupportPainter(QPainter):
            def drawPixmap(self, point, pixmap):
                self.support = self.transform().map(point + anchor)
                super().drawPixmap(point, pixmap)

        frame = QImage(280, 230, QImage.Format_ARGB32)
        frame.fill(Qt.transparent)
        for elapsed in (.1, .35, .7):
            self.advance(elapsed)
            clip = self.director._window_poses.load(self.director.steps[self.director.index].pose)
            index = clip_index(clip, (self.now[0] - self.director._started) * 1000)
            anchor = QPoint(*clip['anchors'][index])
            painter = SupportPainter(frame)
            self.director.draw(painter)
            painter.end()
            self.assertEqual(painter.support + self.pet.pos(), QPoint(target.geometry.center().x(), 260))
        self.advance(6.41)
        self.advance(1.13)
        perched = self.pet.pos()
        self.advance(.01)
        self.assertLess((self.pet.pos() - perched).manhattanLength(), 5)
        destination = QPoint(self.director.steps[-1].destination)
        for _ in range(4):
            if not self.director.active:
                break
            self.advance(self.director.steps[self.director.index].seconds + .01)
        self.assertFalse(self.director.active)
        self.assertEqual(self.pet.pos(), destination)

    def test_disabling_pranks_revokes_an_already_pending_operation(self):
        self.reach_commit()
        self.director.apply(self.pet.preferences.with_module("system", False))
        self.desktop.commit()
        self.assertEqual(self.desktop.minimized, [])
        self.assertFalse(self.director.active)

    def test_foreground_change_during_countdown_cancels_target(self):
        self.entertain(minimize_pranks=True)
        self.director.start('minimize')
        self.advance(self.director.steps[self.director.index].seconds + .1)
        self.desktop.active_window = replace(self.desktop.active_window, identity='another')
        self.desktop.changed.emit(self.desktop.active_window)
        self.advance(20)
        self.assertFalse(self.director.active)
        self.assertEqual(self.desktop.minimized, [])

    def test_exclusion_and_nonordinary_windows_are_never_targets(self):
        self.entertain(minimize_pranks=True, excluded_apps=['KONSOLE'])
        self.assertFalse(self.director.start('minimize'))
        self.entertain(minimize_pranks=True)
        self.desktop.active_window = replace(self.desktop.active_window, normal=False)
        self.assertFalse(self.director.start('minimize'))
        self.assertEqual(self.desktop.minimized, [])

    def test_successful_minimize_restores_same_identity_after_foreground_changes(self):
        self.reach_commit()
        self.desktop.commit()
        self.assertEqual(self.desktop.minimized, ['ordinary'])
        self.assertEqual(self.director.restore_identity, 'ordinary')
        self.director.cancel()
        self.desktop.active_window = replace(self.desktop.active_window, identity='another')
        self.director.restore_window()
        self.assertEqual(self.desktop.restored, ['ordinary'])
        self.assertEqual(self.director.restore_identity, '')
        self.assertFalse(self.director._minimize_ready())

    def test_hidden_pet_cancels_story_and_all_story_timers(self):
        self.entertain()
        self.director.start('wander')
        self.pet.hide()
        self.assertFalse(self.director.active)
        self.assertFalse(self.director._frame.isActive())
        self.assertFalse(self.director._ambient.isActive())
        self.assertIsNone(self.pet._bubble)

    def test_stories_render_visible_pixels_through_the_real_paint_path(self):
        self.entertain()
        for scene in ('wander', 'cursor', 'peek', 'sit', 'dance'):
            with self.subTest(scene=scene):
                self.assertTrue(self.director.start(scene))
                self.advance(self.director.steps[self.director.index].seconds + .1)
                frame = self.pet.grab().toImage()
                self.assertFalse(frame.isNull())
                self.assertTrue(any(bytes(frame.constBits())))
                self.director.cancel()

    def test_manual_drag_interrupts_peek_and_removes_paint_clip(self):
        self.entertain()
        self.director.start('peek')
        self.advance(self.director.steps[self.director.index].seconds + .1)
        self.assertIsNotNone(self.director._clip_rect)
        self.pet._start_pickup()
        self.assertFalse(self.director.active)
        self.assertEqual(self.pet._state, 'pickup')
        self.assertIsNone(self.director._clip_rect)
        self.assertTrue(self.pet.mask().isEmpty())

    def test_peek_to_walk_replaces_old_pixels_and_moves_on_the_next_frame(self):
        self.entertain(dialogue=False)
        self.director.rng = random.Random(17)
        self.assertTrue(self.director.start('peek'))
        self.advance(self.director.steps[self.director.index].seconds + .1)
        self.assertIsNotNone(self.director._clip_rect)

        # Reuse one surface across poses, with no automatic background erase.
        # This models stale backing-store pixels when a clipped pose is exposed.
        frame = QImage(280, 230, QImage.Format_ARGB32)
        frame.fill(Qt.red)
        self.pet._transitions.clock = lambda: self.now[0]

        def render_into(image):
            class SurfacePainter(QPainter):
                def __init__(self, _widget):
                    super().__init__(_widget if isinstance(_widget, QImage) else image)

            with patch('presentation.pet.QPainter', SurfacePainter):
                self.pet.paintEvent(None)

        render_into(frame)
        edge = self.desktop.active_window.geometry.right() - self.pet.x()
        self.assertTrue(all(frame.pixelColor(x, y).alpha() == 0
                            for x in range(edge) for y in range(frame.height())))
        self.assertTrue(any(frame.pixelColor(x, y).alpha() > 0
                            for x in range(edge, frame.width()) for y in range(frame.height())))
        peek_pixels = bytes(frame.constBits())
        self.advance(7.1)
        self.assertEqual(self.director.steps[self.director.index].pose, 'walk')
        self.assertIsNone(self.director._clip_rect)
        self.assertTrue(self.pet.mask().isEmpty())
        self.assertTrue(self.director._frame.isActive())
        render_into(frame)
        self.now[0] += .13  # The authored short transition must fully release its source.
        render_into(frame)
        self.assertTrue(bytes(frame.constBits()) != peek_pixels)

        clean = QImage(280, 230, QImage.Format_ARGB32)
        clean.fill(Qt.transparent)
        render_into(clean)
        self.assertEqual(frame, clean, 'The walk frame retained pixels from the old peek pose')
        walk_pixels = bytes(frame.constBits())
        start = self.pet.pos()
        self.advance(.16)
        render_into(frame)
        self.assertNotEqual(self.pet.pos(), start)
        self.now[0] += .08
        render_into(frame)
        self.assertTrue(bytes(frame.constBits()) != walk_pixels)

    def test_preferences_preserve_adapter_settings_and_normalize_bad_input(self):
        store = self.director.store
        store.path.write_text(json.dumps({'enabled_adapters': ['codex', 'custom'], 'other': 3}))
        preferences = InteractionPreferences.from_dict({'mode': 'bad', 'minimize_pranks': 'yes',
            'interval_seconds': 1, 'minimize_interval_seconds': -10, 'excluded_apps': ['Firefox', None]})
        store.save(preferences)
        saved = json.loads(store.path.read_text())
        self.assertEqual(saved['enabled_adapters'], ['codex', 'custom'])
        self.assertEqual(saved['other'], 3)
        self.assertEqual(store.load().mode, 'focus')
        self.assertFalse(store.load().minimize_pranks)
        self.assertEqual(store.load().interval_seconds, 5)
        self.assertEqual(store.load().minimize_interval_seconds, 60)
        self.assertEqual(store.load().excluded_apps, ['Firefox'])

    def test_missing_qtdbus_keeps_the_desktop_companion_usable(self):
        with patch('presentation.desktop_interaction.QDBusConnection', None):
            desktop = KWinDesktop()
            desktop.start()
            self.assertFalse(desktop.available)
            desktop.stop()

    def test_tier_weights_are_independent_of_window_count(self):
        self.entertain()
        base = self.desktop.active_window
        self.desktop.windows.update({f'visible-{i}': replace(base, identity=f'visible-{i}', active=False)
                                     for i in range(20)})
        self.desktop.windows.update({f'background-{i}': replace(base, identity=f'background-{i}',
                                                              active=False, minimized=True)
                                     for i in range(80)})
        self.director.rng = random.Random(19)
        counts = [0, 0, 0]
        for _ in range(2400):
            target = self.director.select_target()
            counts[2 if target.minimized else 0 if target.identity == 'ordinary' else 1] += 1
        self.assertTrue(1500 < counts[0] < 1700, counts)
        self.assertTrue(500 < counts[1] < 700, counts)
        self.assertTrue(140 < counts[2] < 260, counts)

    def test_explicitly_pinned_window_and_zero_weights(self):
        self.entertain(top_weight=8, foreground_weight=0, background_weight=0)
        pinned = replace(self.desktop.active_window, identity='pinned', active=False, keep_above=True)
        self.desktop.windows['pinned'] = pinned
        for _ in range(20):
            self.assertEqual(self.director.select_target().identity, 'pinned')
        self.entertain(top_weight=0, foreground_weight=0, background_weight=0)
        self.assertIsNone(self.director.select_target())
        self.assertFalse(self.director.start('peek'))

    def test_top_is_highest_stacked_eligible_visible_window(self):
        self.entertain()
        base = self.desktop.active_window
        highest = replace(base, identity='highest', active=False, stacking_order=10)
        self.desktop.windows['highest'] = highest
        self.desktop.windows['elsewhere'] = replace(highest, identity='elsewhere',
                                                   stacking_order=100, current_desktop=False)
        self.desktop.windows['minimized'] = replace(highest, identity='minimized',
                                                   stacking_order=200, minimized=True)
        top, foreground, background = self.director.target_groups()
        self.assertEqual([w.identity for w in top], ['highest'])
        self.assertEqual([w.identity for w in foreground], ['ordinary'])
        self.assertEqual([w.identity for w in background], ['minimized'])

    def test_geometry_and_activation_storm_does_not_restart_or_retarget_scene(self):
        bounds = patch.object(self.director, '_bounds', return_value=QRect(0, 0, 1600, 1000))
        bounds.start()
        self.addCleanup(bounds.stop)
        self.entertain()
        self.assertTrue(self.director.start('peek'))
        self.advance(self.director.steps[self.director.index].seconds + .1)
        started = self.director._started
        with patch.object(self.pet, '_show_speech') as speech:
            for i in range(100):
                target = replace(self.desktop.windows['ordinary'], geometry=QRect(60 + i, 300, 600, 500))
                self.desktop.windows['ordinary'] = target
                self.desktop.active_window = replace(target, identity='another', active=True)
                self.desktop.changed.emit(self.desktop.active_window)
                self.director._follow_target()
            self.assertEqual(self.director.target.identity, 'ordinary')
            self.assertEqual(self.director.index, 1)
            self.assertEqual(self.director._started, started)
            speech.assert_not_called()
        self.advance(7.1)
        self.assertEqual(self.director.index, 2)
        self.advance(self.director.steps[self.director.index].seconds + .1)
        self.assertFalse(self.director.active)
        self.assertFalse(self.director._follow.isActive())

    def test_background_greeting_does_not_restore_or_minimize_program(self):
        self.entertain(top_weight=0, foreground_weight=0, background_weight=1)
        background = replace(self.desktop.active_window, identity='background', active=False, minimized=True)
        self.desktop.windows['background'] = background
        self.assertTrue(self.director.start('background'))
        self.assertEqual(self.director.target.identity, 'background')
        self.advance(self.director.steps[self.director.index].seconds + .01)
        self.advance(self.director.steps[self.director.index].seconds + .01)
        self.assertFalse(self.director.active)
        self.assertEqual(self.desktop.minimized, [])
        self.assertEqual(self.desktop.restored, [])

    def test_observer_initial_snapshot_and_duplicate_reports(self):
        desktop = KWinDesktop()
        reports = []
        desktop.changed.connect(reports.append)
        def item(identity, **extra):
            return dict(id=identity, app='test', x=0, y=0, width=600, height=400,
                        active=False, normal=True, minimizable=True, minimized=False,
                        fullscreen=False, pid=os.getpid() + 1, **extra)
        payload = json.dumps({'windows': [item('one'), item('two', keepAbove=True, stackingOrder=9)],
                              'active': 'one'})
        desktop.Report(payload)
        desktop.Report(payload)
        self.assertEqual(len(reports), 1)
        self.assertEqual(set(desktop.windows), {'one', 'two'})
        self.assertTrue(desktop.windows['two'].keep_above)
        self.assertEqual(desktop.windows['two'].stacking_order, 9)
        self.assertTrue(desktop.active_window.active)
        desktop.stop()

    def test_weights_persist_and_invalid_values_are_clamped(self):
        preferences = InteractionPreferences.from_dict({'top_weight': 500, 'foreground_weight': -3,
                                                        'background_weight': True})
        self.director.store.save(preferences)
        saved = self.director.store.load()
        self.assertEqual((saved.top_weight, saved.foreground_weight, saved.background_weight), (100, 0, 1))
