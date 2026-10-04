import os
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_data = tempfile.TemporaryDirectory()
os.environ['MORI_COMPANION_HOME'] = _data.name

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QContextMenuEvent, QMouseEvent, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from character.dialogue import AMBIENT_THOUGHTS, IDLE_ANIMATION_SPEECH
from companion.client import send_event
from companion.events import CompanionEvent, EventType
from companion.preferences import InteractionPreferences
from companion.runtime import CompanionController
from presentation.pet import ASSET_ROOT, IMAGE_RELS, DesktopPet, SpeechBubble


class PresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.pet = DesktopPet()
        self.pet.show()
        self.pet._idle_start()
        self.app.processEvents()

    def tearDown(self):
        self.pet._quitting = True
        self.pet._cleanup()
        self.pet.close()
        self.pet.deleteLater()
        self.app.processEvents()

    def test_original_assets_flags_and_idle_rotation(self):
        self.assertTrue(self.pet.windowFlags() & Qt.WindowStaysOnTopHint)
        self.assertTrue(self.pet.testAttribute(Qt.WA_TranslucentBackground))
        for relative in IMAGE_RELS.values():
            self.assertFalse(QPixmap(str(Path(ASSET_ROOT)/relative)).isNull())
        self.assertEqual(self.pet._cur_key, 'nothing')
        self.pet._on_stage_timeout()
        self.assertEqual(self.pet._cur_key, 'eat')
        self.pet._on_stage_timeout()
        self.assertEqual(self.pet._cur_key, 'think')
        self.pet._on_stage_timeout()
        self.assertEqual(self.pet._cur_key, 'nothing')

    def test_click_and_drag_still_work(self):
        QTest.mouseClick(self.pet, Qt.LeftButton)
        QTest.qWait(self.app.doubleClickInterval()+50)
        self.assertEqual(self.pet._state, 'touch')
        self.assertEqual(self.pet._cur_key, 'touch')
        self.pet._on_touch_timeout()
        QTest.mousePress(self.pet, Qt.LeftButton)
        point = QPointF(40, 40)
        moved = QMouseEvent(QEvent.MouseMove, point, QPointF(self.pet.mapToGlobal(point.toPoint())),
                            Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
        self.app.sendEvent(self.pet, moved)
        self.assertEqual(self.pet._state, 'pickup')
        QTest.mouseRelease(self.pet, Qt.LeftButton)
        self.assertEqual(self.pet._state, 'landing')
        self.assertFalse(self.pet._stage_timer.isActive())
        self.pet._on_touch_timeout()
        self.assertEqual(self.pet._state, 'idle')
        self.assertTrue(self.pet._stage_timer.isActive())

    def test_right_click_shows_one_reusable_menu(self):
        QTest.mouseClick(self.pet, Qt.RightButton)
        self.app.processEvents()
        menu = self.pet._context_menu
        self.assertIsNotNone(menu)
        self.assertTrue(menu.isVisible())
        self.assertIn('工具状态', [action.text() for action in menu.actions()])
        # Some backends also deliver a contextMenuEvent after the same click.
        event = QContextMenuEvent(QContextMenuEvent.Mouse, QPoint(25, 25),
                                  self.pet.mapToGlobal(QPoint(25, 25)))
        self.app.sendEvent(self.pet, event)
        self.assertIs(self.pet._context_menu, menu)
        menu.hide()
        QTest.mouseClick(self.pet, Qt.RightButton)
        self.assertIs(self.pet._context_menu, menu)
        self.assertTrue(menu.isVisible())

    def test_double_click_does_not_trigger_single_click(self):
        QTest.mouseClick(self.pet, Qt.LeftButton)
        QTest.mouseDClick(self.pet, Qt.LeftButton)
        QTest.qWait(self.app.doubleClickInterval() + 50)
        self.assertEqual(self.pet._state, 'idle')
        self.assertFalse(self.pet._click_timer.isActive())

    def test_speech_is_a_reusable_non_focusing_tooltip(self):
        self.pet._show_speech('第一条消息')
        self.app.processEvents()
        bubble = self.pet._bubble
        native_id = bubble.winId()
        self.assertEqual(bubble.windowType(), Qt.ToolTip)
        self.assertIs(bubble.parentWidget(), self.pet)
        self.assertTrue(bubble.windowFlags() & Qt.WindowDoesNotAcceptFocus)
        self.assertTrue(bubble.windowFlags() & Qt.WindowTransparentForInput)
        self.assertTrue(bubble.testAttribute(Qt.WA_ShowWithoutActivating))
        self.assertFalse(bubble.testAttribute(Qt.WA_DeleteOnClose))
        self.assertEqual(bubble.focusPolicy(), Qt.NoFocus)
        self.assertFalse(bubble.hasFocus())
        for index in range(20):
            self.pet._show_speech(f'消息 {index}')
        self.assertIs(self.pet._bubble, bubble)
        self.assertEqual(bubble._text, '消息 19')
        self.assertEqual(bubble.winId(), native_id)
        self.assertEqual(len(self.pet.findChildren(SpeechBubble)), 1)
        self.assertTrue(self.pet._bubble_timer.isActive())
        self.pet._kill_bubble()
        self.assertIsNone(self.pet._bubble)
        self.assertFalse(bubble.isVisible())
        self.pet._show_speech('隐藏后恢复消息')
        self.assertIs(self.pet._bubble, bubble)
        self.assertEqual(bubble.winId(), native_id)

    def test_speech_follows_pet_and_hidden_pet_does_not_show_or_allocate_speech(self):
        self.pet.hide()
        self.pet._show_speech('不可见消息')
        self.assertIsNone(self.pet._speech_bubble_cache)
        self.pet.show_whale(greet=False)
        self.pet.move(200, 250)
        self.pet._show_speech('跟随消息')
        bubble = self.pet._bubble
        before = bubble.pos()
        self.pet.move(self.pet.pos() + QPoint(30, 25))
        self.assertEqual(bubble.pos() - before, QPoint(30, 25))
        self.pet.hide()
        self.assertIsNone(self.pet._bubble)
        self.assertFalse(bubble.isVisible())
        self.assertFalse(self.pet._bubble_timer.isActive())
        self.pet._show_speech('隐藏消息')
        self.assertFalse(bubble.isVisible())

    def test_tool_event_does_not_interrupt_touch_and_resumes_afterwards(self):
        self.pet._do_touch()
        touch_speech = self.pet._bubble._text
        reaction = self.pet.character.react(EventType.WAITING_INPUT)
        self.pet.apply_tool_reaction(reaction, ['ci · waiting'], [])
        self.assertEqual(self.pet._cur_key, 'touch')
        self.pet._on_touch_timeout()
        self.assertEqual(self.pet._state, 'idle')
        self.assertEqual(self.pet._cur_key, 'attention')
        self.assertEqual(self.pet._bubble._text, touch_speech)
        self.assertNotEqual(self.pet._bubble._text, reaction.speech)

    def test_work_daydream_shows_food_speech_without_restarting_animation(self):
        self.pet.interactions.apply(InteractionPreferences().with_module('expression', True), persist=False)
        for activity in ('task_started', 'thinking', 'generating'):
            with self.subTest(activity=activity):
                self.pet.apply_tool_reaction(self.pet.character.react(activity), [], [])
                self.pet._kill_bubble()
                animation = self.pet._renderer.state
                with patch.object(self.pet._ambient_dialogue.rng, 'choice', return_value=AMBIENT_THOUGHTS[0]), \
                        patch.object(self.pet._renderer, 'set_animation') as animate:
                    self.pet._pop_ambient_line()
                    animate.assert_not_called()
                self.assertEqual(self.pet._bubble._text, AMBIENT_THOUGHTS[0])
                self.assertEqual(self.pet._renderer.state, animation)
                self.assertEqual(self.pet._tool_reaction.status, activity)
                self.assertTrue(self.pet._dialogue_timer.isActive())

    def test_bound_idle_cues_keep_dialogue_clock_running_in_every_stage(self):
        self.pet.interactions.apply(InteractionPreferences().with_module('expression', True), persist=False)
        with patch.object(self.pet._dialogue_timer, 'start') as start:
            for stage in ('eat', 'think', 'nothing'):
                self.pet._on_stage_timeout()
                self.assertEqual(self.pet._cur_key, stage)
                if stage in IDLE_ANIMATION_SPEECH:
                    self.assertEqual(self.pet._bubble._text, IDLE_ANIMATION_SPEECH[stage])
                self.pet._kill_bubble()
                self.pet._pop_ambient_line()
                self.assertIsNotNone(self.pet._bubble)
                self.assertTrue(self.pet._dialogue_timer.isActive())
            start.assert_not_called()
        self.pet._pause_idle()
        self.pet._stage_idx = 1
        self.pet._back_to_idle()
        self.assertTrue(self.pet._dialogue_timer.isActive())

    def test_ambient_thoughts_yield_to_bound_speech_and_important_states(self):
        self.pet.interactions.apply(InteractionPreferences().with_module('expression', True), persist=False)
        self.pet._show_speech('动作的专属台词')
        self.pet._pop_ambient_line()
        self.assertEqual(self.pet._bubble._text, '动作的专属台词')
        for activity in ('waiting_input', 'task_failed', 'task_completed'):
            self.pet.apply_tool_reaction(self.pet.character.react(activity), [], [])
            self.assertEqual(self.pet._bubble._text, self.pet._tool_reaction.speech)
            self.pet._kill_bubble()
            self.pet._pop_ambient_line()
            self.assertIsNone(self.pet._bubble)

    def test_animation_change_alone_does_not_repeat_event_speech(self):
        self.pet.interactions.apply(InteractionPreferences().with_module('expression', True), persist=False)
        reaction = self.pet.character.react('thinking')
        self.pet.apply_tool_reaction(reaction, [], [])
        self.pet._kill_bubble()
        self.pet.apply_tool_reaction(replace(reaction, image='nothing', animation=None), [], [])
        self.assertEqual(self.pet._cur_key, 'nothing')
        self.assertIsNone(self.pet._bubble)

    def test_ambient_timer_stops_when_expression_disabled_or_pet_hidden(self):
        enabled = InteractionPreferences().with_module('expression', True)
        self.pet.interactions.apply(enabled, persist=False)
        self.assertTrue(self.pet._dialogue_timer.isActive())
        self.pet._pop_ambient_line()
        self.assertIsNotNone(self.pet._bubble)
        self.pet.interactions.apply(enabled.with_module('expression', False), persist=False)
        self.assertFalse(self.pet._dialogue_timer.isActive())
        self.pet._pop_ambient_line()
        self.assertIsNone(self.pet._bubble)
        self.pet.interactions.apply(enabled, persist=False)
        self.pet.hide()
        self.assertFalse(self.pet._dialogue_timer.isActive())
        self.pet._pop_ambient_line()
        self.assertIsNone(self.pet._bubble)
        self.pet.show_whale(greet=False)
        self.assertTrue(self.pet._dialogue_timer.isActive())

    def test_hidden_pet_stops_ambient_timers_and_retains_latest_tool_state(self):
        self.pet.hide()
        self.assertFalse(self.pet._stage_timer.isActive())
        self.assertFalse(self.pet._dialogue_timer.isActive())
        self.pet.apply_tool_reaction(self.pet.character.react(EventType.GENERATING), ['ci'], [])
        self.assertIsNone(self.pet._bubble)
        self.pet.show_whale(greet=False)
        self.assertEqual(self.pet._cur_key, 'eat')
        self.assertTrue(self.pet._stage_timer.isActive())

    def test_entrance_finishes_and_returns_to_current_tool_state(self):
        self.pet.play_entrance()
        self.assertEqual(self.pet._state, 'cheerup_show')
        self.pet.apply_tool_reaction(self.pet.character.react(EventType.THINKING), ['aider'], [])
        self.pet._do_touch()
        self.assertEqual(self.pet._state, 'cheerup_show')
        deadline = time.monotonic() + self.pet._cheerup_timer.interval() / 1000 + 1
        while self.pet._state == 'cheerup_show' and time.monotonic() < deadline:
            QTest.qWait(20)
        self.assertEqual(self.pet._state, 'idle')
        self.assertEqual(self.pet._cur_key, 'think')

    def test_socket_event_reaches_qt_presentation(self):
        descriptor = Path(_data.name)/'test-bridge.json'
        controller = CompanionController(self.pet, descriptor_path=descriptor)
        try:
            send_event(CompanionEvent(source='future-agent', session_id='s', type='generating').to_dict(),
                       descriptor_path=descriptor)
            QTest.qWait(100)
            self.assertEqual(self.pet._cur_key, 'eat')
            self.assertIn('future-agent', self.pet.tool_summary_text)
            self.pet.open_status_window()
            self.assertIn('generating', self.pet._status_window._events.toPlainText())
        finally:
            controller.stop()
            controller.deleteLater()

    def test_file_import_preserves_original_and_chat_search_is_local(self):
        path = Path(_data.name)/'notes.txt'
        path.write_text('Mori Companion local knowledge')
        self.assertTrue(self.pet._import_document(str(path)))
        self.assertTrue(path.exists())
        self.pet.open_chat_window()
        chat = self.pet._chat_window
        chat._edit.setText('搜索 knowledge')
        chat._on_send()
        self.assertIn('Mori Companion local knowledge', chat._view.toPlainText())
        chat.close()
        self.assertFalse(chat.isVisible())
        self.assertTrue(self.pet.isVisible())

    def test_corrupt_skin_falls_back_without_crashing(self):
        with patch.object(self.pet.character.assets, 'resolve', return_value=Path(_data.name)/'missing.png'):
            self.pet._set_image('think')
            self.assertFalse(self.pet._pixmap.isNull())
            self.assertFalse(self.pet.play_entrance())
