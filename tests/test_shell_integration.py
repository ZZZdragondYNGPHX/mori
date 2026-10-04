import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from companion.adapters.shell_integration import (
    MARKER,
    fish_quote,
    install_shell,
    shell_script,
)


class ShellIntegrationTests(unittest.TestCase):
    def test_ordinary_codex_preserves_arguments_stdio_exit_and_injects_hooks(self):
        for shell in ('bash', 'fish', 'zsh'):
            executable = shutil.which(shell)
            if executable is None:
                continue
            with self.subTest(shell=shell), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                binary = root / 'bin'
                binary.mkdir()
                fake = binary / 'codex'
                fake.write_text('#!' + sys.executable + '\n'
                    'import json, os, sys\n'
                    'print(json.dumps({"arguments": sys.argv[1:], '
                    '"marked": os.environ.get("MORI_CODEX_CLI"), "stdin": sys.stdin.read()}))\n'
                    'raise SystemExit(17)\n')
                fake.chmod(0o700)
                script = root / "config with ' quote" / ('codex.' + shell)
                script.parent.mkdir()
                script.write_text(shell_script(shell))
                quote = fish_quote if shell == 'fish' else shlex.quote
                prompt = '字串 $HOME `literal` "quote" \'single\''
                command = 'source ' + quote(str(script)) + '; codex exec --json ' + quote(prompt)
                if shell == 'fish':
                    arguments = [executable, '--no-config', '-i', '-c', command]
                elif shell == 'bash':
                    arguments = [executable, '--noprofile', '--norc', '-i', '-c', command]
                else:
                    arguments = [executable, '-f', '-i', '-c', command]
                environment = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ['PATH'])
                environment.pop('MORI_CODEX_CLI', None)
                result = subprocess.run(arguments, input='terminal-input', text=True,
                                        capture_output=True, timeout=5, env=environment)
                self.assertEqual(result.returncode, 17, result.stderr)
                data = json.loads(result.stdout)
                self.assertEqual(data['arguments'][-3:], ['exec', '--json', prompt])
                self.assertEqual(data['arguments'][0], '--no-daemon')
                self.assertEqual(data['marked'], '1')
                self.assertEqual(data['stdin'], 'terminal-input')
                self.assertTrue(any('companionctl.py' in arg and 'codex-hook' in arg
                                    for arg in data['arguments']))

    def test_noninteractive_shell_does_not_intercept_original_codex(self):
        for shell in ('bash', 'fish', 'zsh'):
            executable = shutil.which(shell)
            if executable is None:
                continue
            with self.subTest(shell=shell), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                binary = root / 'bin'
                binary.mkdir()
                fake = binary / 'codex'
                fake.write_text('#!/bin/sh\nprintf "original:%s" "$1"\n')
                fake.chmod(0o700)
                script = root / ('codex.' + shell)
                script.write_text(shell_script(shell))
                quote = fish_quote if shell == 'fish' else shlex.quote
                command = 'source ' + quote(str(script)) + '; codex app-server'
                flags = ['--no-config'] if shell == 'fish' else (['--noprofile', '--norc'] if shell == 'bash' else ['-f'])
                environment = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ['PATH'])
                result = subprocess.run([executable, *flags, '-c', command], capture_output=True,
                                        text=True, timeout=3, env=environment)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, 'original:app-server')

    def test_install_preserves_startup_and_is_idempotent(self):
        for shell in ('fish', 'bash', 'zsh'):
            with self.subTest(shell=shell), tempfile.TemporaryDirectory() as temporary:
                home = Path(temporary)
                startup = home / ('.zshrc' if shell == 'zsh' else '.bashrc')
                startup.write_text('# Existing settings\nexport MY_SETTING=hello\n')
                original = startup.read_text()
                path = install_shell(shell, home)
                self.assertIn(MARKER, path.read_text())
                after = startup.read_text()
                self.assertTrue(after.startswith(original))
                self.assertEqual(install_shell(shell, home), path)
                self.assertEqual(startup.read_text(), after)
                if shell == 'fish':
                    self.assertEqual(after, original)
                    self.assertEqual(path.parent.name, 'conf.d')
                else:
                    self.assertEqual(len(list(home.glob(startup.name + '.backup-*'))), 1)

    def test_unrelated_custom_function_or_config_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            function = home / '.config/fish/functions/codex.fish'
            function.parent.mkdir(parents=True)
            function.write_text('function codex; echo my-custom-function; end\n')
            with self.assertRaises(FileExistsError):
                install_shell('fish', home)
            self.assertIn('my-custom-function', function.read_text())
            function.unlink()
            config = home / '.config/fish/conf.d/mori-companion.fish'
            config.parent.mkdir(parents=True)
            config.write_text('# Unrelated user settings\n')
            with self.assertRaises(FileExistsError):
                install_shell('fish', home)
            self.assertEqual(config.read_text(), '# Unrelated user settings\n')
