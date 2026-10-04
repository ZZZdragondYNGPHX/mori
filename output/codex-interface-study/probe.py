import json
import os
import base64
import queue
import socket
import struct
import pty
import fcntl
import termios
import select
import signal
import re
import sys
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent
CODEX = str(ROOT / 'codex')
clients = []
posts = []
release_first = threading.Event()
native_question = threading.Event()
async_fixture = threading.Event()
native_output = bytearray()
relay_messages = {'client': [], 'server': []}

class Model(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"data": []}')

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        names = [t.get('name', '') for t in body.get('tools', [])]
        (ROOT/('tool_defs_'+str(len(posts)+1)+'.json')).write_text(json.dumps(
            [t for t in body.get('tools',[]) if 'input' in t.get('name','') or 'message' in t.get('name','')],indent=2))
        if not names:
            (ROOT/'extra_request.json').write_text(json.dumps(body,indent=2))
        question = next((name for name in names if name.endswith('request_user_input')), None)
        async_tool=next((name for name in names if name.endswith('send_message_to_user_async')),None)
        posts.append({'tools': names, 'inputTypes': [x.get('type') for x in body.get('input', [])]})
        print('LOCAL_MODEL_REQUEST', len(posts), 'tools=', names, flush=True)
        if len(posts)==1:
            release_first.wait(10)
        # A synthetic tool call only on the second turn; all inference is local.
        if async_fixture.is_set() and async_tool:
            async_fixture.clear()
            item={'type':'function_call','id':'fc_async_local','call_id':'call_async_local',
                  'name':async_tool,'arguments':json.dumps({'message':'Local asynchronous attention fixture.'})}
        elif native_question.is_set() and question:
            native_question.clear()
            args = {'questions': [{'header': 'Local probe', 'id': 'local_choice',
                                  'question': 'Choose for the isolated probe.',
                                  'options': [{'label': 'A', 'description': 'First'},
                                              {'label': 'B', 'description': 'Second'}]}]}
            item = {'type': 'function_call', 'id': 'fc_local', 'call_id': 'call_local',
                    'name': question, 'arguments': json.dumps(args)}
        else:
            item = {'type': 'message', 'id': 'msg_local_' + str(len(posts)),
                    'role': 'assistant', 'phase': 'final_answer', 'status': 'completed',
                    'content': [{'type': 'output_text', 'text': 'Mori local probe text.', 'annotations': []}]}
        response = {'id': 'resp_local_' + str(len(posts)), 'object': 'response',
                    'status': 'completed', 'output': [item],
                    'usage': {'input_tokens': 1, 'output_tokens': 1, 'total_tokens': 2}}
        events = [ {'type': 'response.created', 'response': dict(response, status='in_progress', output=[])},
                   {'type': 'response.output_item.added', 'output_index': 0,
                    'item': dict(item, arguments='') if item['type']=='function_call' else dict(item, content=[]) } ]
        if item['type'] == 'function_call':
            events += [{'type': 'response.function_call_arguments.delta', 'item_id': item['id'],
                        'output_index': 0, 'delta': item['arguments']},
                       {'type': 'response.function_call_arguments.done', 'item_id': item['id'],
                        'output_index': 0, 'arguments': item['arguments']}]
        else:
            events += [{'type': 'response.content_part.added', 'item_id': item['id'],
                        'output_index': 0, 'content_index': 0,
                        'part': {'type': 'output_text', 'text': '', 'annotations': []}},
                       {'type': 'response.output_text.delta', 'item_id': item['id'],
                        'output_index': 0, 'content_index': 0, 'delta': item['content'][0]['text']}]
        events += [{'type': 'response.output_item.done', 'output_index': 0, 'item': item},
                   {'type': 'response.completed', 'response': response}]
        stream = ''.join('event: '+e['type']+'\ndata: '+json.dumps(e)+'\n\n' for e in events).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Content-Length', str(len(stream)))
        self.end_headers()
        self.wfile.write(stream)

class Client:
    def __init__(self, name):
        self.name, self.counter = name, 0
        self.q, self.messages = queue.Queue(), []
        self.socket = socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        self.socket.connect(str(ROOT/'server.sock'))
        key=base64.b64encode(os.urandom(16)).decode()
        self.socket.sendall(('GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n'
                            'Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\n'
                            'Sec-WebSocket-Key: '+key+'\r\n\r\n').encode())
        handshake=b''
        while not handshake.endswith(b'\r\n\r\n'):
            handshake+=self.socket.recv(1)
        assert b'101 Switching Protocols' in handshake,handshake
        threading.Thread(target=self.read, daemon=True).start()
        clients.append(self)

    def read(self):
        try:
            while True:
                first,second=self.exact(2)
                length=second&127
                if length==126: length=struct.unpack('!H',self.exact(2))[0]
                elif length==127: length=struct.unpack('!Q',self.exact(8))[0]
                mask=self.exact(4) if second&128 else None
                payload=self.exact(length)
                if mask: payload=bytes(b^mask[i%4] for i,b in enumerate(payload))
                if first&15==8: return
                if first&15==9: self.frame(payload,10); continue
                if first&15!=1: continue
                try: msg=json.loads(payload)
                except ValueError: continue
                self.messages.append(msg)
                self.q.put(msg)
        except (EOFError,OSError): pass

    def exact(self,n):
        data=b''
        while len(data)<n:
            chunk=self.socket.recv(n-len(data))
            if not chunk: raise EOFError()
            data+=chunk
        return data

    def frame(self,payload,opcode=1):
        mask=os.urandom(4)
        size=len(payload)
        header=bytes([128|opcode])
        header+=(bytes([128|size]) if size<126 else
                 bytes([128|126])+struct.pack('!H',size) if size<65536 else
                 bytes([128|127])+struct.pack('!Q',size))
        self.socket.sendall(header+mask+bytes(b^mask[i%4] for i,b in enumerate(payload)))

    def send(self, message):
        self.frame(json.dumps(message).encode())

    def rpc(self, method, params, timeout=15):
        self.counter += 1
        request_id=self.counter
        self.send({'id':request_id,'method':method,'params':params})
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            try: msg=self.q.get(timeout=max(.01,deadline-time.monotonic()))
            except queue.Empty: break
            if msg.get('id') == request_id and 'method' not in msg:
                if 'error' in msg: raise RuntimeError(method+': '+json.dumps(msg['error']))
                return msg['result']
        raise TimeoutError(method)

    def init(self):
        self.rpc('initialize', {'clientInfo':{'name':self.name,'version':'0.1'},
                               'capabilities': {'experimentalApi':True}})
        self.send({'method':'initialized','params':{}})

    def methods(self):
        return [m['method'] for m in self.messages if 'method' in m]

def wait_for(test, seconds=15):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        if test(): return True
        time.sleep(.05)
    return False

def relay():
    listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
    (ROOT/'relay.sock').unlink(missing_ok=True)
    listener.bind(str(ROOT/'relay.sock'))
    listener.listen()
    def accept():
        downstream,_=listener.accept()
        upstream=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        upstream.connect(str(ROOT/'server.sock'))
        def pump(source,target,direction):
            buffer=b''; upgraded=False
            try:
                while True:
                    chunk=source.recv(65536)
                    if not chunk: break
                    target.sendall(chunk)
                    buffer+=chunk
                    if not upgraded:
                        if b'\r\n\r\n' not in buffer: continue
                        _,buffer=buffer.split(b'\r\n\r\n',1); upgraded=True
                    while len(buffer)>=2:
                        opcode,flag=buffer[:2]; length=flag&127; offset=2
                        if length==126:
                            if len(buffer)<4: break
                            length=struct.unpack('!H',buffer[2:4])[0]; offset=4
                        elif length==127:
                            if len(buffer)<10: break
                            length=struct.unpack('!Q',buffer[2:10])[0]; offset=10
                        masked=bool(flag&128)
                        if len(buffer)<offset+(4 if masked else 0)+length: break
                        mask=buffer[offset:offset+4] if masked else None
                        offset+=4 if masked else 0
                        payload=buffer[offset:offset+length]; buffer=buffer[offset+length:]
                        if mask: payload=bytes(b^mask[i%4] for i,b in enumerate(payload))
                        if opcode&15!=1: continue
                        try: message=json.loads(payload)
                        except ValueError: continue
                        relay_messages[direction].append(message)
            except OSError: pass
        threading.Thread(target=pump,args=(downstream,upstream,'client'),daemon=True).start()
        threading.Thread(target=pump,args=(upstream,downstream,'server'),daemon=True).start()
    threading.Thread(target=accept,daemon=True).start()
    return listener

def test_native():
    listener=relay()
    native_question.set()
    pid,master=pty.fork()
    if pid==0:
        os.chdir(ROOT/'workspace')
        environment=dict(os.environ,TERM='xterm-256color')
        os.execvpe(CODEX,[CODEX,'--remote','unix://'+str(ROOT/'relay.sock'),
                         '--no-alt-screen','--model','mori-probe','--sandbox','read-only',
                         '--ask-for-approval','never','Local fixture choice.'],environment)
    fcntl.ioctl(master,termios.TIOCSWINSZ,struct.pack('HHHH',45,140,0,0))
    def read_terminal():
        try:
            while True:
                chunk=os.read(master,65536)
                if not chunk: break
                native_output.extend(chunk)
                if b'\x1b[6n' in chunk: os.write(master,b'\x1b[1;1R')
        except OSError: pass
    threading.Thread(target=read_terminal,daemon=True).start()
    try:
        methods=lambda:[m.get('method') for m in relay_messages['server']]
        deadline=time.monotonic()+20
        sent_trust=False
        while time.monotonic()<deadline and 'item/tool/requestUserInput' not in methods():
            if not sent_trust and b'trust this' in native_output.lower():
                os.write(master,b'\r'); sent_trust=True
            time.sleep(.05)
        prompts=[m for m in relay_messages['server'] if m.get('method')=='item/tool/requestUserInput']
        print('NATIVE_QUESTION',bool(prompts),flush=True)
        if not prompts:
            (ROOT/'native.screen').write_bytes(native_output)
            print('NATIVE_METHODS',sorted(set(m.get('method','response') for m in relay_messages['server'])),flush=True)
            return
        request=prompts[0]
        print('NATIVE_IS_BLOCKING',request['params'].get('isBlocking'),flush=True)
        time.sleep(.4)
        os.write(master,b'\r')
        answered=wait_for(lambda:any('method' not in m and m.get('id')==request['id'] and 'result' in m
                                    for m in relay_messages['client']),5)
        if not answered:
            os.write(master,b'\r')
            answered=wait_for(lambda:any('method' not in m and m.get('id')==request['id'] and 'result' in m
                                        for m in relay_messages['client']),5)
        print('NATIVE_ANSWER',answered,flush=True)
        print('NATIVE_RESOLVED',wait_for(lambda:'serverRequest/resolved' in methods(),5),flush=True)
        print('NATIVE_ORIGINAL_TEXT',wait_for(lambda:b'Mori local probe text.' in native_output,5),flush=True)
        print('NATIVE_ANSWER_PAYLOAD',[m['result'] for m in relay_messages['client']
                                    if 'method' not in m and m.get('id')==request['id'] and 'result' in m],flush=True)
        tid=request['params']['threadId']
        reader=Client('mori_native_reader'); reader.init()
        print('NATIVE_TASK_NAME',reader.rpc('thread/read',{'threadId':tid})['thread'].get('name'),flush=True)
        # Native asynchronous attention is a separate function and event path.
        wait_for(lambda:any(m.get('method')=='turn/completed' for m in relay_messages['server']),5)
        time.sleep(.4)
        previous_count=len(relay_messages['server'])
        async_fixture.set()
        os.write(master,b'Local async fixture.')
        time.sleep(.3)
        os.write(master,b'\r')
        async_items=lambda:[m['params']['item'] for m in relay_messages['server'][previous_count:]
                           if m.get('method') in ('item/started','item/completed')
                           and m.get('params',{}).get('item',{}).get('delivery')=='async']
        found=wait_for(lambda:bool(async_items()),10)
        print('NATIVE_ASYNC_DELIVERY',found,flush=True)
        print('NATIVE_ASYNC_ITEMS',async_items(),flush=True)
        print('NATIVE_ASYNC_TEXT',b'Local asynchronous attention fixture.' in native_output,flush=True)
        (ROOT/'native.events.json').write_text(json.dumps(relay_messages,indent=2))
        (ROOT/'native.screen').write_bytes(native_output)
    finally:
        try: os.kill(pid,signal.SIGTERM)
        except ProcessLookupError: pass
        os.waitpid(pid,0); os.close(master); listener.close()

def test_concurrent():
    release_first.set()
    a,b,watcher=Client('mori_task_a'),Client('mori_task_b'),Client('mori_task_watcher')
    for c in (a,b,watcher): c.init()
    pending=[]
    for c in (a,b):
        thread=c.rpc('thread/start',{'cwd':str(ROOT/'workspace'),'approvalPolicy':'never','sandbox':'read-only'})['thread']
        native_question.set()
        c.rpc('turn/start',{'threadId':thread['id'],'input':[{'type':'text','text':'Concurrent local fixture.'}],
                           'collaborationMode':{'mode':'plan','settings':{'model':'mori-probe'}}})
        assert wait_for(lambda:any(m.get('method')=='item/tool/requestUserInput' for m in c.messages)), 'Concurrent question missing'
        request=next(m for m in c.messages if m.get('method')=='item/tool/requestUserInput')
        response=watcher.rpc('thread/resume',{'threadId':thread['id']})
        pending.append((c,thread['id'],request))
        print('LATE_JOIN_RESPONSE_FIELDS',list(response),flush=True)
    time.sleep(.3)
    replay=[m for m in watcher.messages if m.get('method')=='item/tool/requestUserInput']
    print('LATE_JOIN_REPLAYS',[{'thread':m['params']['threadId'],'id':m['id']} for m in replay],flush=True)
    c,tid,request=pending[0]
    c.send({'id':request['id'],'result':{'answers':{'local_choice':{'answers':['A']}}}})
    assert wait_for(lambda:any(m.get('method')=='serverRequest/resolved' and m['params']['threadId']==tid for m in watcher.messages))
    time.sleep(.2)
    second_tid=pending[1][1]
    untouched=not any(m.get('method')=='serverRequest/resolved' and m['params']['threadId']==second_tid for m in watcher.messages)
    print('ANSWER_A_KEEPS_B_PENDING',untouched,flush=True)
    c,tid,request=pending[1]
    c.send({'id':request['id'],'result':{'answers':{'local_choice':{'answers':['B']}}}})
    print('ANSWER_B_RESOLVES_B',wait_for(lambda:any(m.get('method')=='serverRequest/resolved' and m['params']['threadId']==tid for m in watcher.messages)),flush=True)
    (ROOT/'concurrent.events.json').write_text(json.dumps({c.name:c.messages for c in (a,b,watcher)},indent=2))

http = ThreadingHTTPServer(('127.0.0.1', 0), Model)
threading.Thread(target=http.serve_forever,daemon=True).start()
home=ROOT/'home'
(home/'config.toml').write_text('model = "mori-probe"\nmodel_provider = "mori_probe"\n'
    '[model_providers.mori_probe]\nname="Local Mori probe"\nwire_api="responses"\n'
    'base_url="http://127.0.0.1:'+str(http.server_port)+'/v1"\nrequires_openai_auth=false\n'
    '[features]\ndefault_mode_request_user_input=true\nsend_message_to_user_async=true\n')
server_err=(ROOT/'server.stderr').open('w')
server=subprocess.Popen([CODEX,'app-server','--listen','unix://'+str(ROOT/'server.sock')],
                        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=server_err,
                        cwd=ROOT/'workspace')
try:
    assert wait_for(lambda:(ROOT/'server.sock').exists() and server.poll() is None,5),'Server did not start'
    if '--native-only' in sys.argv:
        release_first.set()
        test_native()
        print('NATIVE_PROBE_DONE',flush=True)
        raise SystemExit(0)
    if '--concurrent-only' in sys.argv:
        test_concurrent()
        print('CONCURRENT_PROBE_DONE',flush=True)
        raise SystemExit(0)
    owner,observer=Client('mori_probe_owner'),Client('mori_probe_observer')
    owner.init(); observer.init()
    thread=owner.rpc('thread/start',{'cwd':str(ROOT/'workspace'),'ephemeral':False,
                                    'approvalPolicy':'never','sandbox':'read-only'})['thread']
    tid=thread['id']
    owner.rpc('turn/start',{'threadId':tid,'input':[{'type':'text','text':'Local text fixture.'}]})
    assert wait_for(lambda:bool(posts)), 'The local model fixture was not called'
    print('LOADED', owner.rpc('thread/loaded/list',{}), observer.rpc('thread/loaded/list',{}),flush=True)
    observer.rpc('thread/resume',{'threadId':tid})
    owner.rpc('thread/name/set',{'threadId':tid,'name':'Mori isolated probe'})
    release_first.set()
    assert wait_for(lambda: 'turn/completed' in owner.methods()),'Text turn did not finish'
    time.sleep(.2)
    print('TEXT_STREAM', {c.name:sum(m.get('method')=='item/agentMessage/delta' for m in c.messages) for c in clients},flush=True)
    print('TITLE_STREAM', {c.name:sum(m.get('method')=='thread/name/updated' for m in c.messages) for c in clients},flush=True)
    owner.messages.clear(); observer.messages.clear()
    native_question.set()
    owner.rpc('turn/start',{'threadId':tid,'input':[{'type':'text','text':'Local question fixture.'}],
               'collaborationMode':{'mode':'plan','settings':{'model':'mori-probe','reasoning_effort':None,'developer_instructions':None}}})
    wait_for(lambda:any(m.get('method')=='item/tool/requestUserInput' for m in owner.messages+observer.messages)
                    or 'turn/completed' in owner.methods(),15)
    requests={c.name:[m for m in c.messages if m.get('method')=='item/tool/requestUserInput'] for c in clients}
    print('QUESTION_REQUESTS', {name:[{'id':r['id'],'isBlocking':r['params'].get('isBlocking')} for r in rs]
                                for name,rs in requests.items()},flush=True)
    if any(requests.values()):
        origin=next(c for c in clients if requests[c.name])
        request=requests[origin.name][0]
        origin.send({'id':request['id'],'result':{'answers':{'local_choice':{'answers':['A']}}}})
        assert wait_for(lambda:'serverRequest/resolved' in owner.methods()),'Question did not resolve'
        time.sleep(.2)
        print('QUESTION_RESOLVED',{c.name:sum(m.get('method')=='serverRequest/resolved' for m in c.messages) for c in clients},flush=True)
    print('ALL_METHODS',{c.name:sorted(set(c.methods())) for c in clients},flush=True)
    assert wait_for(lambda:'turn/completed' in owner.methods()), 'Question turn did not finish'
    owner.messages.clear(); observer.messages.clear()
    native_question.set()
    owner.rpc('turn/start',{'threadId':tid,'input':[{'type':'text','text':'Observer disconnect fixture.'}]})
    assert wait_for(lambda:any(m.get('method')=='item/tool/requestUserInput' for m in owner.messages)), 'Disconnect question absent'
    pending=next(m for m in owner.messages if m.get('method')=='item/tool/requestUserInput')
    observer.socket.shutdown(socket.SHUT_RDWR); observer.socket.close()
    time.sleep(.3)
    print('DISCONNECT_KEEPS_PENDING','serverRequest/resolved' not in owner.methods(),flush=True)
    owner.send({'id':pending['id'],'result':{'answers':{'local_choice':{'answers':['A']}}}})
    print('DISCONNECT_OWNER_CAN_ANSWER',wait_for(lambda:'serverRequest/resolved' in owner.methods()),flush=True)
    assert wait_for(lambda:'turn/completed' in owner.methods()), 'Disconnect turn did not finish'
    test_native()
    print('PROBE_DONE',flush=True)
finally:
    release_first.set()
    for client in clients:
        client.socket.close()
    server.terminate()
    try: server.wait(timeout=3)
    except subprocess.TimeoutExpired: server.kill(); server.wait()
    server_err.close(); http.shutdown()
