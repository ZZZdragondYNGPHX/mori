import os
import sys
import tempfile
import unittest
from concurrent.futures import Future
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_data = tempfile.TemporaryDirectory()
os.environ['MORI_COMPANION_HOME'] = _data.name

from PySide6.QtCore import QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from companion.client import send_event
from companion.events import CompanionEvent
from companion.notices import TaskNotice
from companion.preferences import InteractionPreferences
from companion.runtime import CompanionController
from presentation.desktop_interaction import DesktopWindow
from presentation.pet import DesktopPet
from presentation.settings_panel import SettingsWindow
from presentation.task_windows import TaskWindows, TerminalSnapshot, visibility


class TaskPresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.pet = DesktopPet()
        self.pet.show()
        self.pet._idle_start()
        self.descriptor = Path(_data.name) / 'notice-bridge.json'
        self.controller = CompanionController(self.pet, descriptor_path=self.descriptor)
        self.ui = self.controller.notices
        self.ui.windows.desktop.start = lambda: None
        self.app.processEvents()

    def tearDown(self):
        self.controller.stop()
        self.pet._quitting = True
        self.pet._cleanup()
        self.pet.close()
        self.app.processEvents()

    def notice(self, kind, **fields):
        send_event(TaskNotice('codex', 'terminal:thread', kind, title='修气泡', **fields).to_dict(),
                   descriptor_path=self.descriptor)
        QTest.qWait(40)

    def test_long_original_click_expand_and_pin_while_new_messages_arrive(self):
        self.notice('bound')
        self.ui.state.set_visibility('terminal:thread', 'background')
        original = 'Codex 原文\n' + '一段完整描述\n' * 14000
        self.notice('message', item_id='m1', text=original)
        self.assertTrue(self.ui.bubble.isVisible())
        self.assertTrue(self.ui.bubble.windowHandle().flags() & Qt.WindowTransparentForInput)
        self.assertTrue(self.ui.bubble.windowFlags() & Qt.WindowDoesNotAcceptFocus)
        native_id = self.ui.bubble.winId()
        center = self.ui.bubble._body_rect().center().toPoint()
        with patch('presentation.speech.QCursor.pos', return_value=self.ui.bubble.mapToGlobal(center)):
            self.ui.bubble._poll_hover(0)
            self.ui.bubble._poll_hover(3.01)
            QTest.mouseClick(self.ui.bubble, Qt.LeftButton, pos=center)
        self.assertTrue(self.ui.panel.isVisible())
        self.assertEqual(self.ui.panel.original.toPlainText(), original)
        self.notice('message', item_id='m2', text='新原文：暂不覆盖正在阅读的文字。')
        self.assertEqual(self.ui.panel.original.toPlainText(), original)
        self.assertIn('1 条新消息', self.ui.panel.status.text())
        self.assertEqual(self.ui.bubble.winId(), native_id)
        self.ui.panel.fresh.click()
        self.assertEqual(self.ui.panel.original.toPlainText(), '新原文：暂不覆盖正在阅读的文字。')
        self.assertEqual(self.ui.state.tasks['terminal:thread'].unread, 0)

    def test_visible_blocking_choice_knocks_view_pauses_and_resolution_clears(self):
        self.notice('bound')
        self.ui.state.set_visibility('terminal:thread', 'visible')
        self.notice('input', request_id='r', text='选择 A 或 B', blocking=True)
        task = self.ui.state.tasks['terminal:thread']
        self.assertEqual(task.pending['r'].knocks, 1)
        self.assertEqual(self.ui.feedback.kind, 'knock')
        self.ui.open_bubble()
        self.assertTrue(task.pending['r'].viewed)
        self.assertIn('待选择', self.ui.bubble._text)
        self.assertIsNone(self.ui.feedback.kind)
        self.notice('completed', task_id='turn')
        self.assertIn('r', task.pending)
        self.notice('resolved', request_id='r')
        self.assertFalse(task.pending)
        self.assertNotIn('待选择', self.ui.bubble._text)

    def test_opacity_setting_updates_both_live_bubbles_and_persists(self):
        self.pet._show_speech('日常台词')
        window = SettingsWindow(self.pet)
        try:
            window.bubble_opacity.setValue(65)
            self.assertEqual(self.pet.preferences.bubble_opacity, 65)
            self.assertEqual(self.pet._speech_bubble_cache._opacity, .65)
            self.assertEqual(self.ui.bubble._opacity, .65)
            self.assertEqual(self.pet.interactions.store.load().bubble_opacity, 65)
        finally:
            window.deleteLater()

    def test_visible_ordinary_text_and_thinking_stay_quiet(self):
        self.notice('bound')
        self.ui.state.set_visibility('terminal:thread', 'visible')
        self.notice('started', task_id='turn')
        self.notice('message', item_id='m', text='普通进度文字')
        self.assertIsNone(self.ui.feedback.kind)
        self.assertFalse(self.ui.bubble.isVisible())
        self.pet._stage_idx = 2
        self.pet._apply_idle_stage()
        self.assertEqual(self.pet._cur_key, 'nothing')

    def test_codex_work_allows_thoughts_but_task_feedback_has_priority(self):
        self.pet.interactions.apply(InteractionPreferences().with_module('expression', True), persist=False)
        self.notice('bound')
        self.ui.state.set_visibility('terminal:thread', 'visible')
        self.notice('started', task_id='turn')
        self.assertTrue(self.pet._codex_working)
        self.pet._pop_ambient_line()
        self.assertIsNotNone(self.pet._bubble)
        self.assertEqual(self.pet._cur_key, 'nothing')
        self.notice('input', request_id='r', text='选择 A 或 B', blocking=True)
        self.assertIsNone(self.pet._bubble)
        self.pet._pop_ambient_line()
        self.assertIsNone(self.pet._bubble)
        self.assertIn('选择 A 或 B', self.ui.bubble._text)
        self.notice('resolved', request_id='r')
        self.ui.bubble.hide()
        self.ui.feedback.request('terminal:thread', 'text', 'background')
        self.pet._pop_ambient_line()
        self.assertIsNone(self.pet._bubble)

    def test_codex_choice_interrupts_life_and_does_not_record_unfinished_meal(self):
        self.pet.interactions.apply(InteractionPreferences(dialogue=False).with_module('expression', True), persist=False)
        self.assertTrue(self.pet.living.start('life-snack'))
        self.notice('bound')
        self.ui.state.set_visibility('terminal:thread', 'visible')
        self.notice('input', request_id='r', text='请确认这一项', blocking=True)
        self.assertFalse(self.pet.living.active)
        self.assertFalse(self.pet._ambient_dialogue.memory.recent('ate'))
        self.assertEqual(self.ui.feedback.kind, 'knock')
        self.assertFalse(self.pet.living.start('life-wave'))

    def test_legacy_codex_thinking_is_quiet_and_other_tools_still_animate(self):
        send_event(CompanionEvent('codex', 'legacy', 'thinking').to_dict(), descriptor_path=self.descriptor)
        QTest.qWait(40)
        self.pet._stage_idx = 2
        self.pet._apply_idle_stage()
        self.assertEqual(self.pet._cur_key, 'nothing')
        self.assertIsNone(self.pet._bubble)
        send_event(CompanionEvent('future-agent', 'other', 'generating').to_dict(), descriptor_path=self.descriptor)
        QTest.qWait(40)
        self.assertEqual(self.pet._cur_key, 'eat')

    def test_window_and_tab_visibility_ignores_moris_own_surfaces(self):
        snapshot = TerminalSnapshot(':1.9', 2, '/Windows/1', '/konsole/MainWindow_1', 99, True, True)
        target = DesktopWindow('target', 'konsole', QRect(100, 100, 600, 500), pid=99, stacking_order=2)
        pet = DesktopWindow('pet', 'mori', QRect(500, 200, 200, 200), pid=22, stacking_order=6)
        self.assertEqual(visibility(snapshot, target, [target, pet], 22), 'visible')
        overlay = DesktopWindow('other', 'editor', QRect(500, 200, 200, 200), pid=33, stacking_order=5)
        self.assertEqual(visibility(snapshot, target, [target, overlay], 22), 'covered')
        other_tab = TerminalSnapshot(':1.9', 2, '/Windows/1', '/konsole/MainWindow_1', 99, False, True)
        self.assertEqual(visibility(other_tab, target, [target], 22), 'background')

    def test_return_is_explicit_switches_exact_tab_before_window_activation(self):
        windows = TaskWindows()
        snapshot = TerminalSnapshot(':1.9', 2, '/Windows/1', '/konsole/MainWindow_1', 99, False, False)
        windows.bindings['a'] = {'service': ':1.9', 'session': '/Sessions/2'}
        windows.snapshots['a'] = snapshot
        windows.identities['a'] = 'uuid-a'
        calls = []
        windows.desktop.windows = {'uuid-a': object()}
        def activate(identity, pid, done):
            calls.append(('activate', (identity, pid)))
            done(True)
        windows.desktop.activate = activate
        self.assertFalse(calls)
        def dbus(service, path, method, *args):
            calls.append((method, args))
            return 'true' if method.endswith('isActiveWindow') else '2'
        try:
            with patch('presentation.task_windows.inspect_terminal', side_effect=[snapshot, replace(snapshot, active=True)]), \
                 patch('presentation.task_windows.dbus_call', side_effect=dbus):
                windows.return_to('a')
                windows.return_future.result(timeout=1)
                # Poll only the explicit return result, without starting a fresh read worker.
                windows.future = Future()
                windows.poll()
                windows.return_future.result(timeout=1)
                windows.poll()
            self.assertEqual(calls[0], ('org.kde.konsole.Window.setCurrentSession', (2,)))
            self.assertEqual(calls[1], ('activate', ('uuid-a', 99)))
            self.assertIn(('org.kde.konsole.Window.setCurrentSession', (2,)), calls[2:])
        finally:
            windows.close()


if __name__ == '__main__':
    unittest.main()
