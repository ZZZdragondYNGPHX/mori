from pathlib import Path
fixture=(Path(__file__).parent/'probe.py').read_text().split('http = ThreadingHTTPServer')[0]
fixture=fixture.replace("'text': 'Mori local probe text.'",
    "'text': (json.dumps({'title':'修复原生接口'}) if body.get('text',{}).get('format',{}).get('type')=='json_schema' else 'Mori local probe text.')")
exec(fixture)
sys.path.insert(0, '/mnt/repo/src')
from companion.adapters.codex_stream import Transcript
raw=[]
original_feed=Transcript.feed
def recording_feed(self,direction,message):
    raw.append({'direction':direction,'message':message})
    return original_feed(self,direction,message)
Transcript.feed=recording_feed
from companion.bridge import EventBridge
from companion.transport import BridgeServer
from companion.adapters.registry import AdapterRegistry
from companion.adapters.catalog import BUILTIN_ADAPTERS, DEFAULT_ADAPTERS
bridge=EventBridge(capacity=2048)
os.environ['MORI_COMPANION_HOME']=str(ROOT/'mori-state')
os.environ['PATH']=str(ROOT)+os.pathsep+os.environ['PATH']
server=BridgeServer(bridge, AdapterRegistry.load(DEFAULT_ADAPTERS, BUILTIN_ADAPTERS))
server.start()
http=ThreadingHTTPServer(('127.0.0.1',0),Model)
threading.Thread(target=http.serve_forever,daemon=True).start()
(ROOT/'home/config.toml').write_text('model="mori-probe"\nmodel_provider="mori_probe"\n'
    '[model_providers.mori_probe]\nname="Local Mori probe"\nwire_api="responses"\n'
    'base_url="http://127.0.0.1:'+str(http.server_port)+'/v1"\nrequires_openai_auth=false\n')
release_first.set();native_question.set()
notices=[]
stop_capture=threading.Event()
def capture():
    while not stop_capture.is_set():
        notices.extend(bridge.drain());time.sleep(.03)
threading.Thread(target=capture,daemon=True).start()
pid,master=pty.fork()
if pid==0:
    os.chdir(ROOT/'workspace')
    os.execvpe('/usr/bin/python',['/usr/bin/python','/mnt/recording_launcher.py','--no-alt-screen',
        '--model','mori-probe','--sandbox','read-only','--ask-for-approval','never','Local fixture choice.'],
        dict(os.environ, TERM='xterm-256color'))
fcntl.ioctl(master,termios.TIOCSWINSZ,struct.pack('HHHH',45,140,0,0))
def read_terminal():
    try:
        while True:
            chunk=os.read(master,65536)
            if not chunk:break
            native_output.extend(chunk)
            if b'\x1b[6n' in chunk:os.write(master,b'\x1b[1;1R')
    except OSError:pass
threading.Thread(target=read_terminal,daemon=True).start()
try:
    deadline=time.monotonic()+15;trusted=False
    while time.monotonic()<deadline and not any(n.kind=='input' for n in notices):
        if not trusted and b'trust this' in native_output.lower():
            os.write(master,b'\r');trusted=True
        time.sleep(.05)
    if not any(n.kind=='input' for n in notices):
        (ROOT/'production.screen').write_bytes(native_output)
        raise AssertionError('Native input did not reach Mori')
    question=next(n for n in notices if n.kind=='input')
    print('INPUT_CAPTURED',question.blocking,question.text,flush=True)
    time.sleep(.3);os.write(master,b'\r')
    assert wait_for(lambda:any(n.kind=='resolved' for n in notices),5),'Resolution absent'
    assert wait_for(lambda:any(n.kind=='message' and n.text=='Mori local probe text.' for n in notices),5),'Original absent'
    assert wait_for(lambda:any(n.kind=='completed' for n in notices),5),'Completion absent'
    print('NATIVE_CHOICE_RESOLUTION_ORIGINAL_COMPLETE_PASS',flush=True)
    async_fixture.set();time.sleep(.3)
    os.write(master,b'Local async fixture.');time.sleep(.3);os.write(master,b'\r')
    assert wait_for(lambda:any(n.kind=='message' and n.text=='Local asynchronous attention fixture.' for n in notices),10),'Async original absent'
    print('NATIVE_ASYNC_ORIGINAL_PASS',flush=True)
    assert wait_for(lambda:any(n.title=='修复原生接口' for n in notices),5),'Native generated title absent'
    assert len({n.session_id for n in notices})==1,'Historical/helper thread leaked'
    print('NATIVE_GENERATED_TASK_TITLE_AND_ROOT_FILTER_PASS',flush=True)
    (ROOT/'production.notices.json').write_text(json.dumps([n.to_dict() for n in notices],indent=2))
finally:
    os.kill(pid,signal.SIGTERM)
    os.waitpid(pid,0);os.close(master)
    time.sleep(.5)
    stop_capture.set();server.stop();http.shutdown()
    (ROOT/'production.screen').write_bytes(native_output)
