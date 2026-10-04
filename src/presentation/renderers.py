"""Qt raster backend. Animation SDKs can implement the same character contract."""
import logging
from importlib.metadata import entry_points
from pathlib import Path

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QMovie, QPixmap

from character.contracts import Emotion
from character.timing import gif_speed_scale


class RasterRenderer(QObject):
    """One streaming GIF decoder, one displayed frame; no global frame cache."""
    def __init__(self, assets, display, placeholder, parent=None):
        super().__init__(parent)
        self.assets = assets
        self.display = display
        self.placeholder = placeholder
        self.movie = None
        self.state = None
        self.emotion = Emotion()
        self._paused = True
        self._exhausted = False
        self._remaining_ms = 0
        self._limit = QTimer(self)
        self._limit.setSingleShot(True)
        self._limit.timeout.connect(self._finish)

    def set_emotion(self, emotion):
        self.emotion = emotion
        if self.movie is not None:
            self.movie.setSpeed(self._playback_speed())

    def _playback_speed(self):
        # Calm states move more slowly; intensity never exceeds authored FPS.
        scale = gif_speed_scale(self.state.key) if self.assets.definition.id == "whale-girl" and self.state else 1
        return round((80 + 20 * max(0, min(1, self.emotion.intensity))) * scale)

    def set_animation(self, state):
        if state == self.state:
            return
        self._release_movie()
        self.state = state
        self._exhausted = False
        self._remaining_ms = state.duration_ms
        try:
            path = self.assets.resolve(state.key)
        except (ValueError, KeyError):
            self.display(self.placeholder(state.key))
            return
        if Path(path).suffix.lower() == ".gif":
            movie = QMovie(str(path), parent=self)
            movie.setCacheMode(QMovie.CacheNone)
            movie.setSpeed(self._playback_speed())
            if movie.isValid():
                self.movie = movie
                movie.frameChanged.connect(self._display_frame)
                movie.error.connect(self._on_error)
                movie.jumpToFrame(0)
                if not self._paused:
                    self.resume()
                return
            movie.deleteLater()
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            fallback = self.assets.definition.fallbacks.get(state.key)
            if fallback:
                pixmap = QPixmap(str(self.assets.resolve_path(fallback)))
        self.display(pixmap if not pixmap.isNull() else self.placeholder(state.key))

    def _display_frame(self, _index):
        if self.movie is not None:
            pixmap = self.movie.currentPixmap()
            if not pixmap.isNull():
                self.display(pixmap)
            if (not self.state.loop and self.movie.frameCount() > 1 and
                    _index == self.movie.frameCount() - 1):
                self._finish()

    def _on_error(self, _error):
        logging.warning("Unable to decode current character animation")
        self._finish()

    def _finish(self):
        self._exhausted = True
        self._limit.stop()
        if self.movie is not None:
            self.movie.setPaused(True)

    def pause(self):
        self._paused = True
        if self._limit.isActive():
            self._remaining_ms = max(1, self._limit.remainingTime())
            self._limit.stop()
        if self.movie is not None:
            self.movie.setPaused(True)

    def resume(self):
        self._paused = False
        if self.movie is None or self._exhausted:
            return
        if self.movie.state() == QMovie.NotRunning:
            self.movie.start()
        else:
            self.movie.setPaused(False)
        if not self.state.loop and not self._limit.isActive():
            self._limit.start(self._remaining_ms)

    def _release_movie(self):
        self._limit.stop()
        if self.movie is not None:
            movie, self.movie = self.movie, None
            movie.stop()
            movie.frameChanged.disconnect(self._display_frame)
            movie.error.disconnect(self._on_error)
            movie.deleteLater()

    def dispose(self):
        self.pause()
        self._release_movie()
        self.state = None


def create_renderer(kind, assets, display, placeholder, parent=None):
    """Explicit skin backend selection. No unrequested plugin SDK is loaded."""
    if kind == "raster":
        return RasterRenderer(assets, display, placeholder, parent)
    for plugin in entry_points(group="mori_companion.renderers"):
        if plugin.name == kind:
            return plugin.load()(assets=assets, display=display, placeholder=placeholder, parent=parent)
    raise ValueError(f"Renderer plugin is not installed: {kind}")
