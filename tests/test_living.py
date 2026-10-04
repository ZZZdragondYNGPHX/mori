import json
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import test_presentation as fixtures
from PySide6.QtCore import QEvent
from PySide6.QtGui import QImageReader, QTransform
from PySide6.QtWidgets import QApplication

from character.life import LIFE_SCENES, THEMES
from companion.preferences import InteractionPreferences, PreferenceStore
from presentation.pet import ASSET_ROOT
from presentation.settings_panel import SettingsWindow


class LivingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        fixtures.PresentationTests.setUp(self)
        self.pet.interactions.apply(InteractionPreferences(dialogue=False).with_module(
            'expression', True).with_module('rest', True), persist=False)
        self.life = self.pet.living
        self.now = [0.0]
        self.life.clock = lambda: self.now[0]
        self.life.memory.clock = self.life.clock

    def tearDown(self):
        fixtures.PresentationTests.tearDown(self)
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)

    def finish(self):
        self.now[0] += self.life.scene.seconds + .01
        self.life.advance()

    def test_work_micro_action_keeps_task_and_returns_to_latest_animation(self):
        self.pet.apply_tool_reaction(self.pet.character.react('generating'), [], [])
        position = self.pet.pos()
        self.assertFalse(self.life.start('life-snack'))
        self.assertTrue(self.life.start('life-hungry'))
        self.assertEqual(self.pet._tool_reaction.status, 'generating')
        self.assertFalse(self.pet._stage_timer.isActive())
        self.pet.apply_tool_reaction(self.pet.character.react('thinking'), [], [])
        self.assertTrue(self.life.active)
        self.finish()
        self.assertFalse(self.life.active)
        self.assertEqual(self.pet._state, 'idle')
        self.assertEqual(self.pet._cur_key, 'think')
        self.assertEqual(self.pet.pos(), position)
        self.assertTrue(self.pet._stage_timer.isActive())
        self.assertFalse(self.life._frame.isActive())

    def test_eating_only_records_after_completion_and_changes_future_choices(self):
        self.assertTrue(self.life.start('life-snack'))
        self.now[0] += 2
        self.life.advance()
        self.assertFalse(self.life.memory.recent('ate'))
        self.pet._do_touch()
        self.assertFalse(self.life.active)
        self.assertFalse(self.life.memory.recent('ate'))
        self.assertTrue(self.life.memory.recent('touched'))
        self.pet._on_touch_timeout()
        self.pet._kill_bubble()
        self.assertTrue(self.life.start('life-snack'))
        self.finish()
        self.assertTrue(self.life.memory.recent('ate'))
        self.assertFalse(self.life.start('life-snack'))
        self.assertTrue(self.life.start('life-crumbs'))

    def test_work_preempts_full_idle_story_without_completing_memory(self):
        self.assertTrue(self.life.start('life-doze'))
        self.now[0] += 5
        self.life.advance()
        self.pet.apply_tool_reaction(self.pet.character.react('thinking'), [], [])
        self.assertFalse(self.life.active)
        self.assertFalse(self.life.memory.recent('rested'))
        self.assertEqual(self.pet._cur_key, 'think')
        self.assertFalse(self.life.start('life-doze'))

    def test_failure_waiting_and_touch_immediately_preempt_micro_actions(self):
        for status in ('waiting_input', 'task_failed', 'task_completed'):
            self.pet.apply_tool_reaction(self.pet.character.react('thinking'), [], [])
            self.pet._kill_bubble()
            self.assertTrue(self.life.start('life-cloud'))
            self.pet.apply_tool_reaction(self.pet.character.react(status), [], [])
            self.assertFalse(self.life.active)
            self.assertEqual(self.pet._state, 'idle')
            self.assertEqual(self.pet._cur_key, self.pet._tool_reaction.image)
            self.assertFalse(self.life.memory.recent('dreamed'))
            self.assertTrue(self.life.memory.recent('interrupted'))

    def test_module_and_visibility_stop_timers_and_restart_full_quiet_interval(self):
        self.assertTrue(self.life.start('life-daydream'))
        self.pet.hide()
        self.assertFalse(self.life.active)
        self.assertFalse(self.life._frame.isActive())
        self.assertFalse(self.life._ambient.isActive())
        self.assertFalse(self.life.memory.recent('dreamed'))
        self.pet.show_whale(greet=False)
        self.assertTrue(29900 <= self.life._ambient.remainingTime() <= 90100)
        prefs = self.pet.preferences.with_module('expression', False).with_module('rest', False)
        self.pet.interactions.apply(prefs, persist=False)
        self.assertFalse(self.life._ambient.isActive())
        self.assertFalse(self.life.start('life-wave'))

    def test_recent_rest_and_mirrored_pose_have_consistent_recovery(self):
        self.assertTrue(self.life.start('life-rest'))
        self.finish()
        self.assertTrue(self.life.memory.recent('rested'))
        self.assertFalse(self.life.start('life-nod'))
        self.assertTrue(self.life.start('life-awake'))
        self.pet.move(520, 300)
        self.pet.grab()
        expected = self.life.frame.toImage().transformed(QTransform().scale(-1, 1))
        self.assertEqual(self.pet._visible_base_frame.toImage(), expected)
        from presentation.speech import head_anchor

        self.assertEqual(self.pet.speech_anchor(), head_anchor(
            self.pet._visible_base_frame, self.pet.rect().topLeft(), self.pet.rect()))

    def test_scheduling_matches_relaxed_defaults_and_settings_persist(self):
        with patch.object(self.life.rng, 'uniform', return_value=.5):
            self.life.schedule()
            self.assertEqual(self.life._ambient.interval(), 30000)
            self.pet.apply_tool_reaction(self.pet.character.react('generating'), [], [])
            self.assertEqual(self.life._ambient.interval(), 60000)
        window = SettingsWindow(self.pet)
        try:
            with patch.object(PreferenceStore, 'save') as save:
                window.life_work.setValue(160)
                window.life_idle.setValue(80)
                saved = save.call_args.args[0]
                self.assertEqual(saved.life_work_seconds, 160)
                self.assertEqual(saved.life_idle_seconds, 80)
                self.assertEqual(InteractionPreferences.from_dict({'life_work_seconds': 1}).life_work_seconds, 60)
        finally:
            window.deleteLater()

    def test_generated_assets_and_all_clips_are_complete_transparent_and_bounded(self):
        root = Path(ASSET_ROOT) / 'motion'
        manifest = json.loads((root / 'clips.json').read_text())['clips']
        names = Counter()
        for name, scene in LIFE_SCENES.items():
            clip = manifest[name]
            self.assertEqual(len(clip['frames']), len(clip['durations']))
            self.assertEqual(sum(clip['durations']), round(scene.seconds * 1000))
            for filename in clip['frames']:
                names[filename] += 1
        self.assertEqual(len(names), 36)
        for theme in THEMES:
            for index in range(6):
                frame = QImageReader(str(root / f'life-{theme}-{index}.png')).read()
                self.assertEqual((frame.width(), frame.height()), (280, 230))
                self.assertTrue(frame.hasAlphaChannel())
                self.assertEqual(frame.pixelColor(0, 0).alpha(), 0)
