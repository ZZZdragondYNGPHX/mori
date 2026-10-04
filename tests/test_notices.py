import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from companion.adapters.codex_stream import Frames, Transcript
from companion.notices import NoticeState, TaskNotice


class NoticeTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.state = NoticeState(lambda: self.now)

    def notice(self, kind, **fields):
        self.state.ingest(TaskNotice('codex', 'window:thread', kind, **fields))

    def test_blocking_choice_stays_until_explicit_resolution(self):
        self.notice('bound')
        self.state.set_visibility('window:thread', 'visible')
        self.notice('input', request_id='r1', text='选择 A / B', blocking=True)
        self.state.tick()
        self.assertEqual(self.state.take_feedback(), [('window:thread', 'knock', 'visible')])
        self.state.viewed('window:thread')
        self.now = 200
        self.state.tick()
        self.notice('message', item_id='m', text='普通文字')
        self.notice('completed', task_id='turn')
        self.assertIn('r1', self.state.tasks['window:thread'].pending)
        self.assertFalse(any(f[1] == 'knock' for f in self.state.take_feedback()))
        self.notice('resolved', request_id='r1')
        self.assertFalse(self.state.tasks['window:thread'].pending)

    def test_delayed_choice_and_visibility_strength(self):
        self.notice('input', request_id='r', text='边做边选', blocking=False)
        self.state.set_visibility('window:thread', 'background')
        self.state.tick()
        self.assertFalse(self.state.take_feedback())
        self.now = 20
        self.state.tick()
        self.assertEqual(self.state.take_feedback()[0][1:], ('knock', 'background'))
        self.state.set_visibility('window:thread', 'covered')
        self.now = 55
        self.state.tick()
        self.assertEqual(self.state.take_feedback()[0][2], 'covered')
        self.state.set_visibility('window:thread', 'visible')
        self.now = 300
        self.state.tick()
        self.assertFalse(self.state.take_feedback())
        self.assertTrue(self.state.tasks['window:thread'].pending)

    def test_messages_keep_original_and_coalesce_feedback(self):
        self.notice('bound')
        self.state.set_visibility('window:thread', 'background')
        text = '原文\n' * 10000
        self.notice('message', item_id='m1', text=text, complete=False)
        self.notice('message', item_id='m1', text=text + '完', complete=True)
        self.notice('message', item_id='m2', text='第二条')
        task = self.state.tasks['window:thread']
        self.assertEqual(list(task.messages.values()), [text + '完', '第二条'])
        self.assertEqual(task.unread, 2)
        self.assertEqual(len(self.state.take_feedback()), 1)
        self.now = 11
        self.notice('message', item_id='m3', text='第三条')
        self.assertEqual(len(self.state.take_feedback()), 1)

    def test_duplicate_completions_and_interruption(self):
        self.notice('completed', task_id='t')
        self.notice('completed', task_id='t')
        self.notice('interrupted', task_id='t2')
        self.assertEqual(len(self.state.take_feedback()), 1)

    def test_two_windows_resolving_one_does_not_clear_other(self):
        for key in ('window1:thread', 'window2:thread'):
            self.state.ingest(TaskNotice('codex', key, 'input', request_id='0', text=key))
        self.state.ingest(TaskNotice('codex', 'window1:thread', 'resolved', request_id='0'))
        self.assertFalse(self.state.tasks['window1:thread'].pending)
        self.assertTrue(self.state.tasks['window2:thread'].pending)

    def test_transcript_ignores_reasoning_and_tool_failures(self):
        notices = []
        stream = Transcript(notices.append, instance='terminal')
        def server(method, params, **extra):
            stream.feed('server', dict(method=method, params=params, **extra))
        server('thread/started', {'thread': {'id': 't', 'name': '修气泡', 'source': 'cli'}})
        server('item/completed', {'threadId': 't', 'turnId': 'turn',
                                 'item': {'type': 'reasoning', 'id': 'r', 'text': 'secret'}})
        server('item/completed', {'threadId': 't', 'turnId': 'turn',
                                 'item': {'type': 'commandExecution', 'id': 'c', 'status': 'failed'}})
        server('item/agentMessage/delta', {'threadId': 't', 'turnId': 'turn', 'itemId': 'm', 'delta': '原文'})
        server('item/completed', {'threadId': 't', 'turnId': 'turn',
                                 'item': {'type': 'agentMessage', 'id': 'm', 'text': '原文', 'delivery': 'async',
                                          'phase': 'final_answer'}})
        server('item/tool/requestUserInput', {'threadId': 't', 'turnId': 'turn', 'isBlocking': False,
                                             'questions': [{'question': '选项', 'options': [{'label': 'A'}]}]}, id=5)
        server('serverRequest/resolved', {'threadId': 't', 'requestId': 5})
        self.assertFalse(any(n.kind in ('failed', 'completed') for n in notices))
        self.assertEqual([n.text for n in notices if n.kind == 'message'], ['原文', '原文'])
        question = next(n for n in notices if n.kind == 'input')
        self.assertFalse(question.blocking)
        self.assertIn('A', question.text)
        self.assertEqual(notices[-1].request_id, '5')

    def test_history_and_internal_title_threads_do_not_become_tasks(self):
        notices = []
        stream = Transcript(notices.append, instance='terminal')
        stream.feed('client', {'id': 'read', 'method': 'thread/read', 'params': {'threadId': 'old'}})
        stream.feed('server', {'id': 'read', 'result': {'thread': {'id': 'old', 'name': '旧任务'}}})
        stream.feed('client', {'id': 'start', 'method': 'thread/start', 'params': {}})
        stream.feed('server', {'id': 'start', 'result': {'thread': {'id': 'root', 'threadSource': 'user'}}})
        stream.feed('server', {'method': 'thread/started', 'params': {'thread': {
            'id': 'helper', 'threadSource': 'thread_title', 'ephemeral': True}}})
        stream.feed('server', {'method': 'item/completed', 'params': {
            'threadId': 'helper', 'turnId': 'h', 'item': {'type': 'agentMessage', 'id': 'm', 'text': '临时标题'}}})
        stream.feed('server', {'method': 'turn/completed', 'params': {
            'threadId': 'helper', 'turn': {'id': 'h', 'status': 'completed'}}})
        stream.feed('server', {'method': 'thread/name/updated', 'params': {
            'threadId': 'root', 'threadName': '原生自动任务名'}})
        self.assertEqual({n.session_id for n in notices}, {'terminal:root'})
        self.assertEqual(notices[-1].title, '原生自动任务名')
        self.assertFalse(any(n.kind in ('message', 'completed') for n in notices))

    def test_disconnect_and_session_exit_do_not_forge_input_resolution(self):
        self.notice('input', request_id='r', text='尚未作答')
        self.notice('disconnected')
        self.now = 300
        self.state.tick()
        self.assertFalse(self.state.take_feedback())
        self.notice('closed')
        self.assertIn('r', self.state.tasks['window:thread'].pending)
        self.notice('resolved', request_id='r')
        self.assertFalse(self.state.tasks['window:thread'].pending)

    def test_frame_chunks_mask_and_fragmentation(self):
        import json
        import struct
        messages = []
        frames = Frames(messages.append)
        frames.feed(b'HTTP/1.1 101 Switching Protocols\r\n\r\n')
        payload = json.dumps({'method': 'hello', 'params': {'text': 'a' * 200}}).encode()
        for number, part in enumerate((payload[:100], payload[100:])):
            mask = b'abcd'
            header = bytes([1 if number == 0 else 128, 254]) + struct.pack('!H', len(part)) + mask
            data = header + bytes(b ^ mask[i % 4] for i, b in enumerate(part))
            for b in data:
                frames.feed(bytes([b]))
        self.assertEqual(messages, [json.loads(payload)])


if __name__ == '__main__':
    unittest.main()
