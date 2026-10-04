"""Pointer-axis input only: no pointer motion, typing, clipboard or screenshots.

Wayland uses optional PyGObject/GIO for exact Portal variant types. Its callbacks
are dispatched by a small Qt timer; no nested main loop or input-hook thread.
"""
import ctypes
import os
import sys
import uuid

from PySide6.QtCore import QObject, QTimer, Signal

from character.dialogue import fixed_line, template_line

SERVICE = 'org.freedesktop.portal.Desktop'
ROOT = '/org/freedesktop/portal/desktop'
INTERFACE = 'org.freedesktop.portal.RemoteDesktop'
REQUEST = 'org.freedesktop.portal.Request'
SESSION = 'org.freedesktop.portal.Session'


class DesktopScroll(QObject):
    ready = Signal(bool)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.bus = None
        self.Gio = self.GLib = None
        self.session = ''
        self.requests = {}
        self.preparing = False
        self.available = False
        self.reason = ''
        self.generation = 0
        self._session_subscription = 0
        self._display = None
        self._x11 = self._xtst = None
        self._axis_remainder = 0.
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.setInterval(45000)
        self._timeout.timeout.connect(lambda: self.fail(fixed_line("notice.scroll_timeout")))
        self._pump = QTimer(self)
        self._pump.setInterval(20)
        self._pump.timeout.connect(self._dispatch)

    def _dispatch(self):
        context = self.GLib.MainContext.default()
        for _ in range(16):
            if not context.pending():
                break
            context.iteration(False)

    def prepare(self):
        if self.available:
            self.ready.emit(True)
            return
        if self.preparing:
            return
        if sys.platform == 'win32':
            self.available = True
            self.ready.emit(True)
            return
        if not (os.environ.get('XDG_SESSION_TYPE') == 'wayland' or os.environ.get('WAYLAND_DISPLAY')):
            try:
                self._x11 = ctypes.CDLL('libX11.so.6')
                self._xtst = ctypes.CDLL('libXtst.so.6')
                self._x11.XOpenDisplay.restype = ctypes.c_void_p
                self._x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
                self._x11.XFlush.argtypes = [ctypes.c_void_p]
                self._x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
                self._xtst.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
                self._display = self._x11.XOpenDisplay(None)
                if not self._display:
                    raise OSError('No X display')
                self.available = True
                self.ready.emit(True)
            except (OSError, AttributeError):
                self.fail(fixed_line("notice.scroll_unavailable"))
            return
        try:
            from gi.repository import Gio, GLib
            self.Gio, self.GLib = Gio, GLib
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except (ImportError, ValueError, OSError) as error:
            self.fail(template_line("scroll.dependencies").format(error=error))
            return
        except Exception as error:  # GLib.Error is only available after importing GI.
            self.fail(template_line("scroll.connect_failed").format(error=error))
            return
        self.preparing = True
        self._pump.start()
        self._timeout.start()
        options = {'session_handle_token': self.GLib.Variant('s', 'mori_' + uuid.uuid4().hex)}
        self._request('CreateSession', '(a{sv})', (), options, self._created)

    def _call(self, path, interface, method, parameters, callback=None):
        generation = self.generation

        def finished(connection, result, _data):
            try:
                reply = connection.call_finish(result)
                if generation == self.generation and callback:
                    callback(reply)
            except self.GLib.Error as error:
                if generation == self.generation:
                    self.fail(template_line("scroll.api_failed").format(error=error.message))

        self.bus.call(SERVICE, path, interface, method, parameters, None,
                      self.Gio.DBusCallFlags.NONE, 1500, None, finished, None)

    def _request(self, method, signature, arguments, options, callback):
        token = 'mori_' + uuid.uuid4().hex
        sender = self.bus.get_unique_name().lstrip(':').replace('.', '_')
        path = f'{ROOT}/request/{sender}/{token}'
        generation = self.generation

        def response(_connection, _sender, signal_path, _interface, _name, parameters, _data):
            if generation != self.generation or signal_path not in self.requests:
                return
            self.bus.signal_unsubscribe(self.requests.pop(signal_path))
            code, results = parameters.unpack()
            if code:
                self.fail(fixed_line("notice.scroll_unauthorized"))
            else:
                callback(results)

        self.requests[path] = self.bus.signal_subscribe(SERVICE, REQUEST, 'Response', path, None,
                                                       self.Gio.DBusSignalFlags.NONE, response, None)
        options = {**options, 'handle_token': self.GLib.Variant('s', token)}
        self._call(ROOT, INTERFACE, method, self.GLib.Variant(signature, (*arguments, options)))

    def _created(self, results):
        self.session = str(results.get('session_handle', ''))
        if not self.session.startswith(ROOT + '/session/'):
            self.fail(fixed_line("notice.scroll_session_invalid"))
            return
        self._session_subscription = self.bus.signal_subscribe(
            SERVICE, SESSION, 'Closed', self.session, None, self.Gio.DBusSignalFlags.NONE,
            lambda *_args: self.fail(fixed_line("notice.scroll_session_closed")), None)
        self._request('SelectDevices', '(oa{sv})', (self.session,),
                      {'types': self.GLib.Variant('u', 2)}, self._selected)

    def _selected(self, _results):
        self._request('Start', '(osa{sv})', (self.session, ''), {}, self._started)

    def _started(self, results):
        if not int(results.get('devices', 0)) & 2:
            self.fail(fixed_line("notice.scroll_permission"))
            return
        self._timeout.stop()
        self.preparing = False
        self.available = True
        self.ready.emit(True)

    def scroll(self, dy, finish=False):
        if not self.available:
            return False
        if self.session:
            self._call(ROOT, INTERFACE, 'NotifyPointerAxis', self.GLib.Variant(
                '(oa{sv}dd)', (self.session, {'finish': self.GLib.Variant('b', finish)}, 0., float(dy))))
        elif sys.platform == 'win32':
            self._axis_remainder += dy * 3
            amount = int(self._axis_remainder)
            self._axis_remainder -= amount
            if amount:
                ctypes.windll.user32.mouse_event(0x0800, 0, 0, ctypes.c_uint32(-amount), 0)
        elif self._display:
            self._axis_remainder += dy
            while abs(self._axis_remainder) >= 30:
                button = 5 if self._axis_remainder > 0 else 4
                self._xtst.XTestFakeButtonEvent(self._display, button, 1, 0)
                self._xtst.XTestFakeButtonEvent(self._display, button, 0, 0)
                self._axis_remainder += -30 if self._axis_remainder > 0 else 30
            self._x11.XFlush(self._display)
        if finish:
            self._axis_remainder = 0
        return True

    def fail(self, reason):
        self.reason = reason
        self.close()
        self.failed.emit(reason)
        self.ready.emit(False)

    def close(self):
        self.generation += 1
        self._timeout.stop()
        self._pump.stop()
        self.preparing = self.available = False
        if self.bus:
            for path, subscription in self.requests.items():
                self.bus.signal_unsubscribe(subscription)
                self.bus.call(SERVICE, path, REQUEST, 'Close', None, None,
                              self.Gio.DBusCallFlags.NONE, 1000, None, None, None)
            self.requests.clear()
            if self.session:
                path, self.session = self.session, ''
                self.bus.signal_unsubscribe(self._session_subscription)
                self._session_subscription = 0
                self.bus.call(SERVICE, path, SESSION, 'Close', None, None,
                              self.Gio.DBusCallFlags.NONE, 1000, None, None, None)
        if self._display:
            self._x11.XCloseDisplay(self._display)
            self._display = None
        self._axis_remainder = 0
