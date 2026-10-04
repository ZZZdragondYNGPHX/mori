"""Register this checkout in the user's Linux application menu."""
import os
import shlex
import shutil
import subprocess
from pathlib import Path


def desktop_string(value):
    return str(value).replace('\\', '\\\\').replace('\n', '\\n').replace('\r', '\\r')


def desktop_argument(value):
    value = str(value).replace('%', '%%')
    for character in ('\\', '"', '`', '$'):
        value = value.replace(character, '\\' + character)
    return desktop_string('"' + value + '"')


def main():
    root = Path(__file__).resolve().parents[1]
    executable = root / '.venv/bin/mori-companion'
    if not executable.is_file():
        raise SystemExit('Install Mori in this checkout’s .venv before registering the application.')
    data = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share'))
    applications = data / 'applications'
    applications.mkdir(parents=True, exist_ok=True)
    entry = applications / 'mori-companion.desktop'
    entry.write_text('\n'.join([
        '[Desktop Entry]', 'Type=Application', 'Version=1.0', 'Name=Mori Companion',
        'GenericName=Desktop Companion', 'GenericName[zh_CN]=桌面陪伴',
        'Comment=Animated desktop companion with focus and entertainment modes',
        'Comment[zh_CN]=支持专注与娱乐模式的鲸鱼娘桌宠',
        'Exec=' + desktop_argument(executable), 'TryExec=' + desktop_string(executable),
        'Path=' + desktop_string(root),
        'Icon=' + desktop_string(root / 'src/asset_library/interaction/peek.png'),
        'Terminal=false', 'Categories=Utility;Amusement;',
        'StartupWMClass=Mori Companion', 'Keywords=Mori;Companion;鲸鱼娘;桌宠;', ''
    ]), encoding='utf-8')
    binaries = Path.home() / '.local/bin'
    binaries.mkdir(parents=True, exist_ok=True)
    for name in ('mori-companion', 'companionctl', 'mori-codex'):
        launcher = binaries / name
        launcher.write_text('#!/bin/sh\nexec ' + shlex.quote(str(root / '.venv/bin' / name)) + ' "$@"\n',
                            encoding='utf-8')
        launcher.chmod(0o755)
    for command in (['update-desktop-database', str(applications)], ['kbuildsycoca6', '--noincremental']):
        if shutil.which(command[0]):
            subprocess.run(command, check=True)
    print(entry)


if __name__ == '__main__':
    main()
