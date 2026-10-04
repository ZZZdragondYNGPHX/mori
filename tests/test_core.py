import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from character import default_character
from companion.bridge import BridgeBusy, EventBridge
from companion.events import CompanionEvent, EventType
from companion.state import CompanionState


class CoreTests(unittest.TestCase):
    def event(self, kind, **kwargs):
        return CompanionEvent(source='arbitrary-agent', session_id='one', type=kind, **kwargs)

    def test_only_the_seven_generic_events_exist(self):
        self.assertEqual({e.value for e in EventType}, {
            'task_started', 'thinking', 'generating', 'waiting_input',
            'task_completed', 'task_failed', 'idle'})
        for source in ('claude-code', 'aider', 'vscode', 'git', 'ci', 'future-tool'):
            state = CompanionState()
            state.ingest(CompanionEvent(source=source, session_id='same', type='generating'))
            self.assertEqual(state.dominant_status(), EventType.GENERATING)

    def test_schema_validation(self):
        invalid = [
            {'source': '', 'session_id': 'a', 'type': 'idle'},
            {'source': 'a', 'session_id': 'a', 'type': 'tool.started'},
            {'source': 'a', 'session_id': 'a', 'type': 'idle', 'prompt': 'secret'},
            {'source': 'a', 'session_id': 'a', 'type': 'idle', 'timestamp': float('nan')},
            {'source': 'a', 'session_id': 'a', 'type': 'idle', 'schema_version': True},
            {'source': 'a\n', 'session_id': 'a', 'type': 'idle'},
        ]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                CompanionEvent.from_dict(payload)
        event = self.event('thinking')
        self.assertEqual(event, CompanionEvent.from_dict(event.to_dict()))

    def test_bridge_coalesces_wakeups_and_bounds_memory(self):
        wakeups = []
        bridge = EventBridge(lambda: wakeups.append(1), capacity=2)
        bridge.publish(self.event('thinking'))
        bridge.publish(self.event('generating'))
        with self.assertRaises(BridgeBusy):
            bridge.publish(self.event('idle'))
        self.assertEqual(len(wakeups), 1)
        self.assertEqual(len(bridge.drain()), 2)
        bridge.publish(self.event('idle'))
        self.assertEqual(len(wakeups), 2)

    def test_sessions_attention_priority_and_terminal_decay(self):
        now = [0]
        state = CompanionState(clock=lambda: now[0])
        state.ingest(self.event('waiting_input'))
        state.ingest(CompanionEvent(source='ci', session_id='build', type='generating'))
        self.assertEqual(state.dominant_status(), EventType.WAITING_INPUT)
        state.ingest(self.event('task_completed'))
        self.assertEqual(state.dominant_status(), EventType.GENERATING)
        state.ingest(CompanionEvent(source='ci', session_id='build', type='task_failed'))
        self.assertEqual(state.dominant_status(), EventType.TASK_FAILED)
        now[0] = 9
        self.assertEqual(state.dominant_status(), EventType.IDLE)
        now[0] = 1801
        self.assertIn('状态已过期', state.session_lines()[0])

    def test_duplicate_stale_and_retired_task_events_are_ignored(self):
        state = CompanionState()
        event = self.event('task_started', task_id='first')
        self.assertTrue(state.ingest(event))
        self.assertFalse(state.ingest(event))
        self.assertFalse(state.ingest(self.event('generating', timestamp=time.time()-10)))
        state.ingest(self.event('task_completed', task_id='first'))
        self.assertFalse(state.ingest(self.event('thinking', task_id='first')))
        state.ingest(self.event('task_started', task_id='second'))
        self.assertFalse(state.ingest(self.event('task_completed', task_id='first')))
        self.assertEqual(state.dominant_status(), EventType.TASK_STARTED)

    def test_session_and_history_caps(self):
        state = CompanionState(session_limit=3)
        for i in range(1100):
            state.ingest(CompanionEvent(source='ci', session_id=str(i), type='idle'))
        self.assertEqual(len(state.sessions), 3)
        self.assertEqual(len(state.history), 200)
        self.assertEqual(len(state._seen), 1024)

    def test_character_contract_resolves_original_assets(self):
        root = Path(__file__).resolve().parents[1] / 'src' / 'asset_library'
        character = default_character(root)
        for kind in EventType:
            reaction = character.react(kind)
            self.assertEqual(reaction.status, kind.value)
            self.assertTrue(character.assets.resolve(reaction.image).is_file())
        self.assertEqual(character.react(EventType.WAITING_INPUT).emotion.name, 'expectant')
        character.definition.animations['bad'] = '../escape.png'
        with self.assertRaises(ValueError):
            character.assets.resolve('bad')

    def test_core_and_character_do_not_import_qt_or_tool_implementations(self):
        import ast
        root = Path(__file__).resolve().parents[1] / 'src'
        files = [root/'companion'/name for name in ('events.py', 'state.py', 'bridge.py')]
        files += list((root/'character').glob('*.py'))
        for path in files:
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotIn('codex', node.module or '')
                    self.assertNotIn('PySide6', node.module or '')
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn('PySide6', alias.name)
