import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from companion.adapters.codex import CodexAdapter
from companion.adapters.codex_cli import launch_command
from companion.adapters.registry import AdapterRegistry
from companion.bridge import EventBridge
from companion.cli import hooks_config, install_codex_hooks
from companion.client import send_event
from companion.events import CompanionEvent, EventType
from companion.transport import BridgeServer


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.descriptor = Path(self.temporary.name)/'bridge.json'
        self.bridge = EventBridge()
        self.registry = AdapterRegistry()
        self.registry.register(CodexAdapter())
        self.server = BridgeServer(self.bridge, self.registry, self.descriptor)
        self.server.start()
        self.opener = build_opener(ProxyHandler({}))

    def tearDown(self):
        self.server.stop()
        self.assertFalse(self.descriptor.exists())
        self.temporary.cleanup()

    def raw_request(self, path, body, **headers):
        defaults = {'Authorization': 'Bearer '+self.server.token, 'Content-Type': 'application/json'}
        defaults.update(headers)
        return self.opener.open(Request(self.server.url+path, body, defaults), timeout=2)

    def test_generic_plugins_and_codex_use_the_same_bridge(self):
        event = CompanionEvent(source='git', session_id='repo', type='task_completed')
        self.assertTrue(send_event(event.to_dict(), descriptor_path=self.descriptor)['accepted'])
        send_event({'hook_event_name':'PermissionRequest','session_id':'codex-session',
                    'turn_id':'turn','prompt':'secret','tool_input':{'command':'secret'}},
                   adapter='codex', descriptor_path=self.descriptor)
        events = self.bridge.drain()
        self.assertEqual([e.type for e in events], [EventType.TASK_COMPLETED, EventType.WAITING_INPUT])
        self.assertNotIn('secret', json.dumps(events[1].to_dict()))
        self.assertEqual(events[1].task_id, 'turn')
        if os.name == 'posix':
            self.assertEqual(self.descriptor.stat().st_mode & 0o777, 0o600)

    def test_http_rejects_unauthorized_bad_and_oversized_events(self):
        cases = [
            ('/v1/events', b'{}', {'Authorization':'wrong'}, 401),
            ('/v1/events', b'{}', {'Origin':'http://localhost'}, 401),
            ('/v1/events', b'bad-json', {}, 400),
            ('/v1/events', b'x'*65537, {}, 413),
            ('/v1/adapters/missing', b'{}', {}, 404),
            ('/v1/unknown', b'{}', {}, 404),
            ('/v1/events', b'{}', {'Content-Type':'text/plain'}, 415),
        ]
        for path, body, headers, expected in cases:
            with self.subTest(path=path, expected=expected), self.assertRaises(HTTPError) as raised:
                self.raw_request(path, body, **headers)
            self.assertEqual(raised.exception.code, expected)
            raised.exception.close()
        self.assertEqual(self.bridge.drain(), [])

    def test_queue_backpressure(self):
        self.bridge._capacity = 1
        event = CompanionEvent(source='ci', session_id='build', type='thinking').to_dict()
        send_event(event, descriptor_path=self.descriptor)
        with self.assertRaises(HTTPError) as raised:
            send_event(event, descriptor_path=self.descriptor)
        self.assertEqual(raised.exception.code, 429)
        raised.exception.close()

    def test_bad_adapter_is_isolated(self):
        class Broken:
            id = 'broken'
            def normalize(self, payload):
                raise RuntimeError('private details')
        self.registry.register(Broken())
        with self.assertRaises(HTTPError) as raised:
            send_event({}, adapter='broken', descriptor_path=self.descriptor)
        self.assertEqual(raised.exception.code, 500)
        self.assertNotIn(b'private details', raised.exception.read())
        raised.exception.close()
        event = CompanionEvent(source='ci', session_id='b', type='idle')
        self.assertTrue(send_event(event.to_dict(), descriptor_path=self.descriptor)['accepted'])

    def test_codex_hook_subprocess_end_to_end_and_unavailable_desktop(self):
        script = Path(__file__).resolve().parents[1]/'src'/'companionctl.py'
        command = [sys.executable, str(script), 'codex-hook', '--descriptor', str(self.descriptor)]
        payload = {'hook_event_name':'Stop', 'session_id':'s', 'turn_id':'t',
                   'last_assistant_message':'secret result'}
        environment = dict(os.environ, MORI_CODEX_CLI='1')
        result = subprocess.run(command, input=json.dumps(payload), text=True, capture_output=True, timeout=2, env=environment)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '{}\n')
        self.assertEqual(result.stderr, '')
        self.assertEqual(self.bridge.drain()[0].type, EventType.TASK_COMPLETED)
        self.server.stop()
        result = subprocess.run(command, input=json.dumps(payload), text=True, capture_output=True, timeout=2, env=environment)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '{}\n', ''))

    def test_gui_or_unmarked_hooks_are_not_observed(self):
        script = Path(__file__).resolve().parents[1]/'src'/'companionctl.py'
        environment = dict(os.environ)
        environment.pop('MORI_CODEX_CLI', None)
        result = subprocess.run([sys.executable, str(script), 'codex-hook', '--descriptor', str(self.descriptor)],
                                input=json.dumps({'hook_event_name':'Stop','session_id':'gui'}),
                                text=True, capture_output=True, env=environment, timeout=2)
        self.assertEqual((result.returncode, result.stdout), (0, '{}\n'))
        self.assertEqual(self.bridge.drain(), [])

    def test_cli_launcher_preserves_arguments_and_generates_valid_toml(self):
        args = ['exec', '--json', 'hello world']
        command = launch_command(args, executable='/bin/codex')
        self.assertEqual(command[:2], ['/bin/codex','--no-daemon'])
        self.assertEqual(command[-3:], args)
        for i in range(2, len(command)-3, 2):
            self.assertEqual(command[i], '-c')
            config = tomllib.loads(command[i+1])
            self.assertIn('hooks', config)
        with self.assertRaises(ValueError):
            launch_command(['--remote=ws://localhost'], executable='/bin/codex')

    def test_hooks_install_preserves_existing_handlers_and_is_idempotent(self):
        path = Path(self.temporary.name)/'hooks.json'
        existing = {'description':'keep', 'hooks':{
            'Stop':[{'matcher':'*', 'hooks':[{'type':'command','command':'echo mine'}]}],
            'Other':[{'hooks':[{'type':'command','command':'existing-command'}]}]}}
        path.write_text(json.dumps(existing))
        backup = install_codex_hooks(path)
        self.assertEqual(json.loads(backup.read_text()), existing)
        merged = json.loads(path.read_text())
        self.assertEqual(merged['description'], 'keep')
        self.assertEqual(merged['hooks']['Other'], existing['hooks']['Other'])
        self.assertEqual(merged['hooks']['Stop'][0], existing['hooks']['Stop'][0])
        self.assertIsNone(install_codex_hooks(path))
        self.assertEqual(json.loads(path.read_text()), merged)
        self.assertTrue(all(group[0]['hooks'][0]['timeout'] == 1
                            for group in hooks_config()['hooks'].values()))

    def test_codex_only_interprets_its_own_lifecycle(self):
        adapter = CodexAdapter()
        self.assertIsNone(adapter.normalize({'hook_event_name':'FutureEvent'}))
        self.assertIsNone(adapter.normalize({'hook_event_name':'Stop','agent_id':'child'}))
        event = adapter.normalize({'hook_event_name':'SessionStart','source':'compact','session_id':'s'})
        self.assertEqual(event.type, EventType.THINKING)
        event = adapter.normalize({'hook_event_name':'PostToolUse','session_id':'s',
                                   'tool_response':{'exit_code':1}})
        self.assertEqual(event.type, EventType.THINKING)
        self.assertIn('错误', event.summary)
