"""Select a desktop backend that supports the pet's window positioning."""
import os
import sys
from pathlib import Path

from PySide6.QtCore import QLibraryInfo


def configure_desktop_platform(environ=None, platform=None, plugins_path=None):
    """Use XWayland on Linux Wayland desktops with an available X display.

    Native Wayland does not support QWidget.move() or portable always-on-top
    requests. Do this before QApplication is constructed, and preserve explicit
    user/CI platform choices (including offscreen).
    """
    environ = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    if platform != "linux" or environ.get("QT_QPA_PLATFORM"):
        return environ.get("QT_QPA_PLATFORM")
    wayland = environ.get("XDG_SESSION_TYPE") == "wayland" or environ.get("WAYLAND_DISPLAY")
    if not wayland or not environ.get("DISPLAY"):
        return None
    plugins_path = plugins_path or QLibraryInfo.path(QLibraryInfo.PluginsPath)
    if not (Path(plugins_path) / "platforms" / "libqxcb.so").is_file():
        return None
    environ["QT_QPA_PLATFORM"] = "xcb"
    return "xcb"
