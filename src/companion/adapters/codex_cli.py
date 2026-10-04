"""Launch native Codex with a private streaming relay and scoped hook fallback.

No GUI state observation, no global hooks/config writes, no API key handling.
The dedicated CLI process prevents hooks from leaking into a shared GUI daemon.
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def toml_value(value):
    if isinstance(value, dict):
        return "{ " + ", ".join(json.dumps(k) + " = " + toml_value(v) for k, v in value.items()) + " }"
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(v) for v in value) + "]"
    return json.dumps(value, ensure_ascii=True)


def launch_command(arguments, executable=None):
    from companion.cli import hooks_config
    executable = executable or shutil.which("codex")
    if not executable:
        raise FileNotFoundError("Codex CLI is not installed or not on PATH")
    # A remote app-server does not inherit this process's hooks/environment.
    if any(arg == "--remote" or arg.startswith("--remote=") for arg in arguments):
        raise ValueError("Mori monitors local Codex CLI sessions; --remote is not supported")
    command = [executable, "--no-daemon"]
    for name, groups in hooks_config()["hooks"].items():
        command += ["-c", "hooks." + name + "=" + toml_value(groups)]
    return command + list(arguments)


# Maintenance and batch commands retain their existing behavior.
COMMANDS = {'agents', 'exec', 'e', 'review', 'login', 'logout', 'mcp', 'plugin',
            'app-server', 'remote-control', 'completion', 'update', 'doctor', 'sandbox',
            'debug', 'apply', 'a', 'resume', 'queue', 'archive', 'delete', 'migrate-rollouts',
            'unarchive', 'fork', 'cloud', 'exec-server', 'features', 'help'}
VALUE_FLAGS = {'-c', '--config', '--enable', '--disable', '-i', '--image', '-m', '--model',
               '-p', '--profile', '-s', '--sandbox', '-a', '--ask-for-approval', '-C', '--cd',
               '--add-dir', '--local-provider', '--remote', '--remote-auth-token-env'}


def interactive(arguments):
    if any(a in ('-h', '--help', '-V', '--version') for a in arguments):
        return False
    skip = False
    for arg in arguments:
        if skip:
            skip = False
            continue
        if arg in VALUE_FLAGS:
            skip = True
        elif arg == '--':
            return True
        elif not arg.startswith('-'):
            return arg not in COMMANDS or arg in ('resume', 'fork')
    return True


def server_options(arguments):
    result = []
    for index, arg in enumerate(arguments):
        if arg in ('-c', '--config', '--enable', '--disable'):
            if index + 1 >= len(arguments):
                raise ValueError('Missing Codex option value')
            result.extend((arg, arguments[index + 1]))
        elif arg.startswith(('--config=', '--enable=', '--disable=')):
            result.append(arg)
    return result


def working_directory(arguments):
    directory = None
    for index, arg in enumerate(arguments):
        if arg == '--':
            break
        if arg in ('-C', '--cd') and index + 1 < len(arguments):
            directory = arguments[index + 1]
        elif arg.startswith('--cd='):
            directory = arg.split('=', 1)[1]
        elif arg.startswith('-C') and len(arg) > 2:
            directory = arg[2:]
    return str(Path(directory).resolve()) if directory else None


def stream_launch(arguments, executable):
    from .codex_stream import StreamRelay, terminal_binding

    if any(arg == '--remote' or arg.startswith('--remote=') for arg in arguments):
        raise ValueError('Mori monitors local Codex CLI sessions; --remote is not supported')
    environment = dict(os.environ, MORI_CODEX_CLI='1', MORI_CODEX_STREAM='1')
    with tempfile.TemporaryDirectory(prefix='mori-cli-') as directory:
        upstream = Path(directory) / 'server.sock'
        downstream = Path(directory) / 'terminal.sock'
        options = ['-c', 'features.default_mode_request_user_input=true',
                   '-c', 'features.send_message_to_user_async=true'] + server_options(arguments)
        # A profile's layered configuration cannot be reproduced with remote TUI overrides.
        # Keep that launch on the established native hook path rather than changing providers.
        if any(a in ('-p', '--profile', '--oss', '--local-provider')
               or a.startswith(('--profile=', '--local-provider=')) for a in arguments):
            print('Mori: 此启动使用配置档案/本地模型，保留原生启动和基础状态提醒。', file=sys.stderr)
            return subprocess.call(launch_command(arguments, executable),
                                   env=dict(os.environ, MORI_CODEX_CLI='1'))
        with (Path(directory) / 'server.log').open('w+b') as log:
            server = subprocess.Popen([executable, *options, 'app-server', '--listen', 'unix://' + str(upstream)],
                                      env=environment, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                      start_new_session=True, cwd=working_directory(arguments))
            relay = None
            client = None
            old_handlers = {}
            try:
                deadline = time.monotonic() + 8
                while not upstream.exists():
                    if server.poll() is not None or time.monotonic() >= deadline:
                        print('Mori: 实时接口启动失败，使用基础状态提醒。', file=sys.stderr)
                        return subprocess.call(launch_command(arguments, executable),
                                               env=dict(os.environ, MORI_CODEX_CLI='1'))
                    time.sleep(.03)
                relay = StreamRelay(downstream, upstream, terminal_binding())
                command = [executable, '--remote', 'unix://' + str(downstream)]
                command.extend(a for a in arguments if a != '--no-daemon')
                client = subprocess.Popen(command, env=environment)
                # Ctrl-C is delivered directly to the terminal CLI; the wrapper stays alive
                # until that CLI exits. Termination is forwarded to the owned processes.
                old_handlers[signal.SIGINT] = signal.signal(signal.SIGINT, lambda *_: None)
                old_handlers[signal.SIGTERM] = signal.signal(signal.SIGTERM, lambda *_: client.terminate())
                code = client.wait()
                return code if code >= 0 else 128 - code
            finally:
                if client and client.poll() is None:
                    client.terminate()
                    try:
                        client.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        client.kill()
                        client.wait()
                if relay:
                    relay.close()
                server.terminate()
                try:
                    server.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()
                for sig, handler in old_handlers.items():
                    signal.signal(sig, handler)


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    try:
        executable = shutil.which('codex')
        if not executable:
            raise FileNotFoundError('Codex CLI is not installed or not on PATH')
        if os.name == 'posix' and interactive(arguments):
            return stream_launch(arguments, executable)
        command = launch_command(arguments, executable)
        environment = dict(os.environ, MORI_CODEX_CLI='1')
        if os.name == 'posix':
            os.execvpe(command[0], command, environment)
        return subprocess.call(command, env=environment)
    except (OSError, ValueError) as error:
        print(f'Mori: {error}', file=sys.stderr)
        return 1
