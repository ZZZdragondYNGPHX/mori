"""Transparent local Codex WebSocket tee; never answers or changes an RPC.

Only the native terminal client owns the conversation. Capturing runs on a
separate queue, so a missing/slow desktop cannot delay the terminal transport.
"""
import json
import os
import queue
import socket
import threading
import time
import uuid
from collections import OrderedDict

from companion.client import send_event
from companion.notices import TaskNotice


class Frames:
    """Incremental RFC6455 decoder for a passive HTTP Upgrade stream."""
    LIMIT = 16 * 1024 * 1024

    def __init__(self, callback):
        self.callback = callback
        self.buffer = bytearray()
        self.upgraded = False
        self.fragment = bytearray()
        self.disabled = False

    def feed(self, chunk):
        if self.disabled:
            return
        self.buffer.extend(chunk)
        if not self.upgraded:
            end = self.buffer.find(b'\r\n\r\n')
            if end < 0:
                if len(self.buffer) > 65536:
                    self.disabled = True
                return
            del self.buffer[:end + 4]
            self.upgraded = True
        while len(self.buffer) >= 2:
            first, second = self.buffer[:2]
            size, offset = second & 127, 2
            if size in (126, 127):
                width = 2 if size == 126 else 8
                if len(self.buffer) < 2 + width:
                    return
                size = int.from_bytes(self.buffer[2:2 + width], 'big')
                offset += width
            masked = bool(second & 128)
            if size > self.LIMIT:
                self.disabled = True
                self.buffer.clear()
                return
            total = offset + (4 if masked else 0) + size
            if len(self.buffer) < total:
                return
            mask = self.buffer[offset:offset + 4] if masked else None
            offset += 4 if masked else 0
            payload = bytes(self.buffer[offset:total])
            del self.buffer[:total]
            if mask:
                payload = bytes(value ^ mask[i % 4] for i, value in enumerate(payload))
            opcode = first & 15
            if opcode in (1, 0):
                if opcode == 1:
                    self.fragment.clear()
                self.fragment.extend(payload)
                if len(self.fragment) > self.LIMIT:
                    self.fragment.clear()
                    self.disabled = True
                    return
                if first & 128:
                    try:
                        data = json.loads(self.fragment)
                        if isinstance(data, dict):
                            self.callback(data)
                    except (ValueError, UnicodeError):
                        pass
                    self.fragment.clear()


def terminal_binding(environment=None):
    env = os.environ if environment is None else environment
    return {key: env.get(name, '') for key, name in (
        ('service', 'KONSOLE_DBUS_SERVICE'), ('session', 'KONSOLE_DBUS_SESSION'),
        ('window', 'KONSOLE_DBUS_WINDOW'), ('native_window', 'WINDOWID')) if env.get(name)}


def question_text(params):
    questions = params.get('questions') or []
    if questions:
        lines = []
        for question in questions:
            lines.append(question.get('question') or question.get('title') or '')
            for option in question.get('options') or []:
                if isinstance(option, str):
                    lines.append('• ' + option)
                else:
                    lines.append('• ' + option.get('label', '')
                                 + ('：' + option['description'] if option.get('description') else ''))
        return '\n'.join(lines)
    # These are user-facing approval fields, not command execution output.
    text = params.get('message') or params.get('reason') or 'Codex 需要你的确认。'
    command = params.get('command')
    if command:
        text += '\n' + (' '.join(command) if isinstance(command, list) else str(command))
    return text


class Transcript:
    def __init__(self, emit, binding=None, instance=None, clock=time.monotonic):
        self.emit = emit
        self.binding = binding or {}
        self.instance = instance or uuid.uuid4().hex
        self.clock = clock
        self.threads = {}
        self.calls = {}
        self.messages = {}
        self.flushed = {}
        self.last_text = {}
        self.dirty = set()
        self.resolved = set()

    def notice(self, thread, kind, **fields):
        if kind in ('input', 'resolved'):
            visited = set()
            while self.threads.get(thread, {}).get('parent') and thread not in visited:
                visited.add(thread)
                thread = self.threads[thread]['parent']
        if not thread or thread not in self.threads or self.threads[thread].get('child'):
            return
        self.emit(TaskNotice('codex', self.instance + ':' + thread, kind,
                             binding=self.binding, **fields))

    def describe(self, thread):
        if not isinstance(thread, dict) or not thread.get('id'):
            return
        key = thread['id']
        existing = self.threads.get(key, {})
        source = thread.get('source')
        child = bool(thread.get('parentThreadId') or
                     thread.get('threadSource') not in (None, 'user') or
                     (isinstance(source, dict) and 'subAgent' in source))
        title = thread.get('name') or existing.get('native_title') or ''
        preview = thread.get('preview') or existing.get('preview') or ''
        self.threads[key] = dict(child=child, native_title=title, preview=preview, parent=thread.get('parentThreadId'))
        fallback = ' '.join(preview.split())[:32]
        self.notice(key, 'bound', title=(title or fallback or '未命名任务')[:240])

    def feed(self, direction, message):
        method, params = message.get('method', ''), message.get('params') or {}
        if direction == 'client':
            if 'id' in message and method:
                self.calls[message['id']] = (method, params)
            return
        if not method:
            request = self.calls.pop(message.get('id'), None)
            result = message.get('result') or {}
            if (isinstance(result, dict) and 'thread' in result and request
                    and (request[0] in ('thread/start', 'thread/resume', 'thread/fork')
                         or result['thread']['id'] in self.threads)):
                self.describe(result['thread'])
            if request and request[0] == 'turn/start' and isinstance(result, dict) and result.get('turn'):
                self.notice(request[1].get('threadId'), 'started', task_id=result['turn']['id'])
            return
        thread = params.get('threadId', '')
        turn = params.get('turnId', '')
        if method == 'thread/started':
            self.describe(params.get('thread'))
        elif method == 'thread/name/updated':
            native = params.get('threadName') or ''
            if thread in self.threads:
                self.threads[thread]['native_title'] = native
                fallback = self.threads[thread].get('preview', '')
                self.notice(thread, 'title', title=(native or ' '.join(fallback.split())[:32] or '未命名任务')[:240])
        elif method in ('turn/started', 'turn/completed'):
            data = params.get('turn') or {}
            status = data.get('status')
            kind = 'started' if method == 'turn/started' else {
                'completed': 'completed', 'failed': 'failed', 'interrupted': 'interrupted'}.get(status)
            if kind:
                error = data.get('error') or {}
                self.notice(thread, kind, task_id=data.get('id', turn), text=error.get('message', ''))
        elif method in ('item/started', 'item/completed'):
            item = params.get('item') or {}
            if item.get('type') == 'agentMessage':
                key = (thread, turn, item['id'])
                self.messages[key] = item.get('text') or self.messages.get(key, '')
                self.flush(key, complete=method == 'item/completed')
            elif item.get('type') == 'userMessage' and not self.threads.get(thread, {}).get('native_title'):
                parts = item.get('content') or []
                prompt = ' '.join(p.get('text', '') for p in parts if p.get('type') == 'text')
                if prompt and not self.threads.get(thread, {}).get('preview'):
                    self.threads.setdefault(thread, {})['preview'] = prompt
                    self.notice(thread, 'title', title=' '.join(prompt.split())[:32])
        elif method == 'item/agentMessage/delta':
            key = (thread, turn, params['itemId'])
            self.messages[key] = self.messages.get(key, '') + params.get('delta', '')
            self.dirty.add(key)
            if self.clock() - self.flushed.get(key, -100) >= .8:
                self.flush(key, complete=False)
        elif 'id' in message and method in (
                'item/tool/requestUserInput', 'item/commandExecution/requestApproval',
                'item/fileChange/requestApproval', 'item/permissions/requestApproval',
                'mcpServer/elicitation/request'):
            request_id = str(message['id'])
            if (thread, request_id) not in self.resolved:
                self.notice(thread, 'input', task_id=turn, request_id=request_id,
                            blocking=params.get('isBlocking', True), text=question_text(params))
        elif method == 'serverRequest/resolved':
            request_id = str(params['requestId'])
            self.resolved.add((thread, request_id))
            self.notice(thread, 'resolved', request_id=request_id)

    def flush(self, key, complete):
        thread, turn, item = key
        text = self.messages.get(key, '')
        self.dirty.discard(key)
        if text and (self.last_text.get(key) != text or complete):
            self.last_text[key] = text
            self.notice(thread, 'message', task_id=turn, item_id=turn + ':' + item,
                        text=text, complete=complete)
            self.flushed[key] = self.clock()

    def tick(self):
        for key in list(self.dirty):
            if self.clock() - self.flushed.get(key, -100) >= .8:
                # Only changed snapshots need IPC; the sender also coalesces snapshots.
                self.flush(key, complete=False)

    def close(self, kind='closed'):
        for thread in self.threads:
            self.notice(thread, kind)


class DesktopDelivery:
    """Retry local IPC; retain state during a desktop restart, never block the CLI."""
    def __init__(self):
        self.pending = OrderedDict()
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True, name='mori-desktop-delivery')
        self.thread.start()

    def emit(self, notice):
        # Keep lifecycle order, replace only undelivered snapshots of the same item.
        key = (notice.session_id, notice.kind, notice.item_id) if notice.kind == 'message' else notice.event_id
        with self.lock:
            self.pending[key] = notice

    def run(self):
        while not self.stop.is_set():
            with self.lock:
                pair = next(iter(self.pending.items()), None)
            if pair is None:
                self.stop.wait(.05)
                continue
            key, notice = pair
            try:
                send_event(notice.to_dict(), timeout=.35)
            except (OSError, ValueError, KeyError, TypeError):
                self.stop.wait(.5)
            else:
                with self.lock:
                    if self.pending.get(key) is notice:
                        self.pending.pop(key, None)

    def close(self):
        # Give the final closed notice a short chance to arrive.
        deadline = time.monotonic() + .6
        while self.pending and time.monotonic() < deadline:
            time.sleep(.02)
        self.stop.set()
        self.thread.join(timeout=.5)


class StreamRelay:
    def __init__(self, path, upstream, binding=None, emit=None):
        self.path, self.upstream = str(path), str(upstream)
        self.delivery = DesktopDelivery() if emit is None else None
        self.transcript = Transcript(emit or self.delivery.emit, binding)
        self.queue = queue.Queue()
        self.stop = threading.Event()
        self.connections = []
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(self.path)
        os.chmod(self.path, 0o600)
        self.listener.listen(2)
        self.listener.settimeout(.2)
        self.worker = threading.Thread(target=self.consume, daemon=True, name='mori-codex-capture')
        self.worker.start()
        threading.Thread(target=self.accept, daemon=True, name='mori-codex-relay').start()

    def consume(self):
        while not self.stop.is_set() or not self.queue.empty():
            try:
                direction, message = self.queue.get(timeout=.2)
            except queue.Empty:
                self.transcript.tick()
                continue
            try:
                self.transcript.feed(direction, message)
            except (KeyError, TypeError, ValueError, AttributeError):
                # An experimental schema variation must not affect the native CLI.
                continue

    def accept(self):
        while not self.stop.is_set():
            try:
                downstream, _ = self.listener.accept()
                upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                upstream.connect(self.upstream)
            except socket.timeout:
                continue
            except OSError:
                break
            self.connections.extend((downstream, upstream))
            for source, target, direction in ((downstream, upstream, 'client'),
                                               (upstream, downstream, 'server')):
                threading.Thread(target=self.pump, args=(source, target, direction), daemon=True).start()

    def pump(self, source, target, direction):
        parser = Frames(lambda message: self.queue.put((direction, message)))
        try:
            while not self.stop.is_set():
                chunk = source.recv(65536)
                if not chunk:
                    break
                # Capture an outgoing request before the upstream can reply to it.
                if direction == "client":
                    parser.feed(chunk)
                    target.sendall(chunk)
                else:
                    target.sendall(chunk)
                    parser.feed(chunk)
        except OSError:
            pass
        finally:
            try:
                target.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    def close(self):
        self.stop.set()
        self.listener.close()
        for connection in self.connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        # Drain queued messages before signalling the terminal's actual exit.
        deadline = time.monotonic() + .3
        while not self.queue.empty() and time.monotonic() < deadline:
            time.sleep(.01)
        self.worker.join(timeout=1)
        self.transcript.close()
        if self.delivery:
            self.delivery.close()
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass
