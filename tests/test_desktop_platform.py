import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from presentation.platform import configure_desktop_platform


class DesktopPlatformTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.plugins = Path(self.temporary.name)
        (self.plugins / 'platforms').mkdir()
        (self.plugins / 'platforms' / 'libqxcb.so').touch()

    def tearDown(self):
        self.temporary.cleanup()

    def test_wayland_with_xwayland_uses_xcb(self):
        environ = {'XDG_SESSION_TYPE': 'wayland', 'WAYLAND_DISPLAY': 'wayland-0', 'DISPLAY': ':0'}
        self.assertEqual(configure_desktop_platform(environ, 'linux', self.plugins), 'xcb')
        self.assertEqual(environ['QT_QPA_PLATFORM'], 'xcb')

    def test_explicit_platform_choice_is_preserved(self):
        for choice in ('offscreen', 'wayland', 'xcb'):
            environ = {'XDG_SESSION_TYPE': 'wayland', 'DISPLAY': ':0', 'QT_QPA_PLATFORM': choice}
            self.assertEqual(configure_desktop_platform(environ, 'linux', self.plugins), choice)

    def test_unavailable_xwayland_or_plugin_does_not_force_xcb(self):
        for environ in ({'XDG_SESSION_TYPE': 'wayland'}, {'DISPLAY': ':0', 'XDG_SESSION_TYPE': 'x11'}):
            self.assertIsNone(configure_desktop_platform(environ, 'linux', self.plugins))
            self.assertNotIn('QT_QPA_PLATFORM', environ)
        (self.plugins / 'platforms' / 'libqxcb.so').unlink()
        environ = {'XDG_SESSION_TYPE': 'wayland', 'DISPLAY': ':0'}
        self.assertIsNone(configure_desktop_platform(environ, 'linux', self.plugins))

    def test_other_operating_systems_are_unchanged(self):
        for platform in ('win32', 'darwin'):
            environ = {'XDG_SESSION_TYPE': 'wayland', 'DISPLAY': ':0'}
            self.assertIsNone(configure_desktop_platform(environ, platform, self.plugins))
