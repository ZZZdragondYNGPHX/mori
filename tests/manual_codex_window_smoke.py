"""Verify real Konsole window/tab routing using only an owned temporary terminal."""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from presentation.platform import configure_desktop_platform
from presentation.task_windows import TaskWindows, dbus_call, inspect_terminal


def wait(test, seconds=6):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if test():
            return True
        QTest.qWait(40)
    return False


def main():
    configure_desktop_platform()
    app = QApplication([])
    with tempfile.TemporaryDirectory(prefix='mori-terminal-check-') as directory:
        root = Path(directory)
        script = root / 'terminal.py'
        report = root / 'binding.json'
        script.write_text("import os,json,time\nfrom pathlib import Path\n"
                          f"Path({str(report)!r}).write_text(json.dumps({{k:os.getenv(v,'') for k,v in "
                          "(('service','KONSOLE_DBUS_SERVICE'),('session','KONSOLE_DBUS_SESSION'),"
                          "('window','KONSOLE_DBUS_WINDOW'),('native_window','WINDOWID'))}))\n"
                          "time.sleep(120)\n")
        environment = dict(os.environ)
        environment.pop('QT_QPA_PLATFORM', None)
        terminal = subprocess.Popen(['konsole', '--separate', '--workdir', directory, '-e', sys.executable, str(script)],
                                    env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        terminal2 = None
        tracker = TaskWindows()
        try:
            assert wait(report.exists), 'Owned terminal did not start'
            binding = json.loads(report.read_text())
            tracker.bind('first', binding)
            if not wait(lambda: tracker.can_return('first')):
                print('DEBUG', binding, tracker.snapshots, tracker.errors, tracker.desktop.available, tracker.desktop.reason,
                      [(w.identity,w.application,w.pid,w.normal,w.active) for w in tracker.desktop.windows.values()])
                raise AssertionError('No KWin association')
            first = inspect_terminal(binding)
            assert first.selected
            identity = tracker.identities['first']
            results = []
            tracker.returned.connect(lambda key, ok, reason: results.append((key, ok, reason)))
            # Native startup activation may be suppressed; exercise the explicit
            # return action before treating this owned window as foreground.
            tracker.return_to('first')
            assert wait(lambda: results, 10) and results[-1][1], results
            second_id = int(dbus_call(first.service, first.window, 'org.kde.konsole.Window.newSession'))
            dbus_call(first.service, first.window, 'org.kde.konsole.Window.setCurrentSession', second_id)
            second_binding = dict(binding, session=f'/Sessions/{second_id}')
            tracker.bind('second', second_binding)
            assert wait(lambda: tracker.snapshots.get('second') and tracker.snapshots['second'].selected)
            assert tracker.strength('first') == 'background', 'Other tab incorrectly considered visible'
            assert wait(lambda: tracker.can_return('second'))
            results.clear()
            tracker.return_to('first')
            if not wait(lambda: results, 10):
                print('RETURN_DEBUG', tracker.return_future, tracker.future, tracker.errors, tracker.desktop._scripts, flush=True)
                raise AssertionError('Return did not finish')
            assert results[-1][1], results[-1]
            assert wait(lambda: tracker.snapshots.get('first') and tracker.snapshots['first'].selected)
            assert tracker.identities['first'] == identity
            assert wait(lambda: tracker.strength('first') in ('visible', 'covered')), ('Foreground classification wrong', tracker.snapshots.get('first'))
            assert tracker.desktop.active_window and tracker.desktop.active_window.identity == identity
            second_report = root / 'other-binding.json'
            other_script = root / 'other-terminal.py'
            other_script.write_text(script.read_text().replace(str(report), str(second_report)))
            terminal2 = subprocess.Popen(['konsole', '--separate', '--workdir', directory,
                '-e', sys.executable, str(other_script)], env=environment,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            assert wait(second_report.exists)
            other_binding = json.loads(second_report.read_text())
            assert other_binding['service'] != binding['service']
            tracker.bind('independent', other_binding)
            assert wait(lambda: tracker.can_return('independent')), ('Independent binding', tracker.snapshots, tracker.errors, tracker.identities, tracker.returning, [(w.application, w.pid) for w in tracker.desktop.windows.values()])
            assert tracker.identities['independent'] != identity
            before = len(results)
            tracker.return_to('second')
            assert wait(lambda: len(results) > before and results[-1][1], 12), (results, tracker.return_future, tracker.returning, tracker.errors, tracker.snapshots, tracker.desktop.active_window)
            assert tracker.desktop.active_window.identity == identity
            assert inspect_terminal(second_binding).selected
            before = len(results)
            tracker.return_to('independent')
            assert wait(lambda: len(results) > before and results[-1][1], 12), (results, tracker.return_future, tracker.returning, tracker.errors, tracker.snapshots, tracker.desktop.active_window)
            assert tracker.desktop.active_window.identity == tracker.identities['independent']
            print('PASS: two independent native Konsole windows; two tabs; exact tab/window return; KWin activation verified')
        finally:
            tracker.close()
            for owned in (terminal, terminal2):
                if owned:
                    owned.terminate()
                    try:
                        owned.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        owned.kill()
                        owned.wait()
            app.processEvents()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
