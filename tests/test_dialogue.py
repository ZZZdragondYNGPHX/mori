import random
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from character.dialogue import AmbientDialogue, EventDialogue, dialogue_groups
from character.life import LIFE_SCENES, THEMES, LifeMemory


class DialogueTests(unittest.TestCase):
    def test_work_can_daydream_about_food_without_idle_only_lines(self):
        dialogue = AmbientDialogue(random.Random(42))
        for activity in ('task_started', 'thinking', 'generating'):
            with patch.object(dialogue.rng, 'choices', return_value=['food']):
                lines = {dialogue.choose(activity, ('好无聊啊',)) for _ in range(40)}
            self.assertTrue(set(dialogue_groups()['food.work']).issubset(lines))
            self.assertNotIn('好无聊啊', lines)

    def test_idle_retains_existing_lines_and_avoids_immediate_repeats(self):
        dialogue = AmbientDialogue(random.Random(42))
        lines = [dialogue.choose('idle', ('原有台词',)) for _ in range(200)]
        self.assertIn('原有台词', lines)
        self.assertTrue(all(a != b for a, b in zip(lines, lines[1:])))

    def test_important_feedback_is_not_a_time_for_ambient_thoughts(self):
        dialogue = AmbientDialogue()
        for activity in ('waiting_input', 'task_completed', 'task_failed'):
            self.assertEqual(dialogue.choose(activity), '')

    def test_all_six_themes_are_equally_full_and_lines_are_unique(self):
        groups = dialogue_groups()
        for theme in THEMES:
            self.assertEqual(sum(len(groups[theme + '.' + key]) for key in ('common', 'work', 'idle', 'memory')), 64)
        lines = [line for pool in groups.values() for line in pool]
        self.assertEqual(len(lines), 636)
        self.assertEqual(len(set(lines)), len(lines))

    def test_recent_meal_and_sleep_choose_matching_memory_then_expire(self):
        now = [0.0]
        memory = LifeMemory(clock=lambda: now[0])
        dialogue = AmbientDialogue(random.Random(42), memory)
        for theme, event, ttl in (('food', 'ate', 900), ('sleep', 'rested', 420)):
            now[0] = 0
            memory.record(event)
            with patch.object(dialogue.rng, 'choices', return_value=[theme]):
                self.assertIn(dialogue.choose('generating'), dialogue_groups()[theme + '.memory'])
                now[0] = ttl
                self.assertNotIn(dialogue.choose('generating'), dialogue_groups()[theme + '.memory'])
            self.assertFalse(memory.recent(event))

    def test_events_do_not_change_speech_during_polling(self):
        dialogue = EventDialogue()
        first = dialogue.resolve('thinking')
        self.assertTrue(first)
        for _ in range(100):
            self.assertEqual(dialogue.resolve('thinking'), first)
        self.assertEqual(dialogue.resolve('idle'), '')
        self.assertNotEqual(dialogue.resolve('thinking'), first)

    def test_scene_memory_prevents_immediate_meals_and_has_work_safe_choices(self):
        memory = LifeMemory(clock=lambda: 0)
        memory.record('ate')
        self.assertFalse(LIFE_SCENES['life-snack'].eligible(memory))
        self.assertTrue(LIFE_SCENES['life-crumbs'].eligible(memory))
        self.assertEqual(len(LIFE_SCENES), 36)
        for theme in THEMES:
            scenes = [s for s in LIFE_SCENES.values() if s.theme == theme]
            self.assertEqual(len(scenes), 6)
            self.assertTrue(any(s.working for s in scenes))
            self.assertTrue(any(not s.working for s in scenes))
        self.assertTrue(all(s.seconds <= 4 for s in LIFE_SCENES.values() if s.working))
