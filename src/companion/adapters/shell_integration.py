"""Opt-in shell integration: ordinary interactive `codex` uses the CLI adapter.

The original executable and global Codex hooks/config are untouched. GUI and
non-interactive callers retain their normal PATH executable.
"""
import os
import shlex
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

MARKER = '# Mori Companion: interactive Codex CLI monitoring'


def login_shell():
    try:
        import pwd
        return Path(pwd.getpwuid(os.getuid()).pw_shell).name
    except (ImportError, KeyError, AttributeError):
        return Path(os.environ.get('SHELL', 'bash')).name


def fish_quote(value):
    return "'" + str(value).replace('\\', '\\\\').replace("'", "\\'") + "'"


def shell_script(shell, python=None, launcher=None):
    python = python or sys.executable
    launcher = launcher or str(Path(__file__).resolve().parents[2] / 'mori_codex.py')
    if shell == 'fish':
        command = ' '.join(fish_quote(value) for value in (python, launcher))
        return (MARKER + '\nif status is-interactive\n'
                '    function codex --wraps=codex --description "Codex CLI with Mori Companion"\n'
                f'        {command} $argv\n'
                '    end\nend\n')
    if shell in ('bash', 'zsh'):
        command = shlex.join([python, launcher])
        return (MARKER + '\ncase $- in\n    *i*)\n'
                '        codex() {\n'
                f'            {command} "$@"\n'
                '        }\n        ;;\nesac\n')
    raise ValueError('Supported shells: fish, bash, zsh')


def _write_owned(path, contents):
    if path.exists():
        existing = path.read_text(encoding='utf-8')
        if existing == contents:
            return
        if not existing.startswith(MARKER + '\n'):
            raise FileExistsError(f'Refusing to overwrite an unrelated file: {path}')
        suffix = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        shutil.copy2(path, path.with_name(path.name + '.backup-' + suffix))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding='utf-8')


def install_shell(shell=None, home=None):
    shell = shell or login_shell()
    home = Path(home) if home is not None else Path.home()
    config_root = home / '.config'
    if home == Path.home():
        config_root = Path(os.environ.get('XDG_CONFIG_HOME', str(config_root)))
    contents = shell_script(shell)
    if shell == 'fish':
        function = config_root / 'fish/functions/codex.fish'
        if function.exists() and MARKER not in function.read_text(encoding='utf-8'):
            raise FileExistsError(f'Existing custom codex function needs manual integration: {function}')
        path = config_root / 'fish/conf.d/mori-companion.fish'
        _write_owned(path, contents)
        return path
    path = config_root / 'mori-companion' / ('codex.' + shell)
    startup = home / ('.bashrc' if shell == 'bash' else '.zshrc')
    if shell == 'zsh' and home == Path.home():
        startup = Path(os.environ.get('ZDOTDIR', str(home))) / '.zshrc'
    _write_owned(path, contents)
    existing = startup.read_text(encoding='utf-8') if startup.exists() else ''
    line = f'[ -r {shlex.quote(str(path))} ] && . {shlex.quote(str(path))}'
    if line not in existing.splitlines():
        if startup.exists():
            suffix = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            shutil.copy2(startup, startup.with_name(startup.name + '.backup-' + suffix))
        startup.parent.mkdir(parents=True, exist_ok=True)
        with startup.open('a', encoding='utf-8') as stream:
            stream.write('\n' + MARKER + '\n' + line + '\n')
    return path
