import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_module_data = tempfile.TemporaryDirectory()
os.environ['MORI_COMPANION_HOME'] = _module_data.name

from PySide6.QtCore import QEvent, QSize
from PySide6.QtGui import QImageReader, QMovie
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from character.animation import ANIMATION_KEYS
from character.contracts import AnimationState
from character.defaults import GIF_ANIMATIONS, PNG_ANIMATIONS, default_character
from character.emotions import ActivityEmotionSystem
from character.skins import SkinRepository, load_definition
from companion.client import send_event
from companion.events import CompanionEvent, EventType
from companion.runtime import CompanionController
from presentation.pet import ASSET_ROOT, DesktopPet
from presentation.skin_panel import validate_image


class EmotionTests(unittest.TestCase):
    def test_polling_does_not_retrigger_emotion_and_idle_recovers(self):
        now = [0]
        emotions = ActivityEmotionSystem(clock=lambda: now[0])
        happy = emotions.resolve('task_completed')
        now[0] = 100
        self.assertEqual(emotions.resolve('task_completed'), happy)
        self.assertEqual(emotions.resolve('idle'), happy)
        now[0] = 103
        self.assertEqual(emotions.resolve('idle').intensity, 0.5)
        now[0] = 106
        self.assertEqual(emotions.resolve('idle').name, 'neutral')
        self.assertEqual(emotions.resolve('waiting_input').name, 'expectant')


class GifTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.data = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {'MORI_COMPANION_HOME': self.data.name})
        self.environment.start()
        self.pet = DesktopPet()
        self.pet.show()
        self.pet._idle_start()
        self.app.processEvents()

    def tearDown(self):
        self.pet._quitting = True
        self.pet._cleanup()
        self.pet.close()
        self.pet.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()
        self.environment.stop()
        self.data.cleanup()

    def test_nine_gifs_have_real_frames_transparency_and_bounded_timing(self):
        self.assertEqual(set(GIF_ANIMATIONS), set(ANIMATION_KEYS))
        for relative in GIF_ANIMATIONS.values():
            path = Path(ASSET_ROOT) / relative
            with self.subTest(path=path):
                validate_image(path)
                reader = QImageReader(str(path))
                self.assertEqual(reader.imageCount(), 4)
                png = QImageReader(str(Path(ASSET_ROOT) / PNG_ANIMATIONS[path.stem])).read()
                self.assertEqual(png.size(), QSize(280, 230))
                digests = set()
                while reader.canRead():
                    frame = reader.read()
                    self.assertFalse(frame.isNull())
                    self.assertEqual(frame.size(), png.size())
                    self.assertEqual(frame.pixelColor(0, 0).alpha(), 0)
                    self.assertGreaterEqual(reader.nextImageDelay(), 80)
                    self.assertLessEqual(reader.nextImageDelay(), 2200)
                    digests.add(hashlib.sha256(frame.constBits()).digest())
                self.assertEqual(len(digests), 4)

    def test_default_pose_switches_and_png_fallback_keep_window_geometry(self):
        point, size = self.pet.pos(), self.pet.size()
        for key in ANIMATION_KEYS:
            self.pet._set_image(key)
            self.assertEqual(self.pet.size(), size)
            self.assertEqual(self.pet.pos(), point)
        with patch.object(self.pet.character.assets, 'resolve',
                          return_value=Path(ASSET_ROOT) / PNG_ANIMATIONS['nothing']):
            self.pet._set_image('nothing')
            self.assertEqual(self.pet.size(), size)
            self.assertEqual(self.pet.pos(), point)

    def test_playback_advances_without_moving_window_and_hidden_pauses(self):
        movie = self.pet._renderer.movie
        self.assertEqual(movie.cacheMode(), QMovie.CacheNone)
        point = self.pet.pos()
        size = self.pet.size()
        movie.jumpToFrame(1)
        QTest.qWait(400)
        self.assertGreater(movie.currentFrameNumber(), 0)
        self.assertEqual(self.pet.pos(), point)
        self.assertEqual(self.pet.size(), size)
        self.pet.hide()
        frame = movie.currentFrameNumber()
        self.assertEqual(movie.state(), QMovie.Paused)
        QTest.qWait(250)
        self.assertEqual(movie.currentFrameNumber(), frame)
        self.pet.show_whale(greet=False)
        self.assertEqual(movie.state(), QMovie.Running)

    def test_all_generic_events_select_expected_gif_over_actual_bridge(self):
        path = Path(self.data.name) / 'events.json'
        controller = CompanionController(self.pet, descriptor_path=path)
        try:
            expected = ['think', 'think', 'eat', 'attention', 'cheerup', 'wrong', 'nothing']
            for event_type, key in zip(EventType, expected):
                event = CompanionEvent(source='codex', session_id='cli-session', type=event_type)
                send_event(event.to_dict(), descriptor_path=path)
                QTest.qWait(30)
                self.assertEqual(self.pet._cur_key, key)
                movie = self.pet._renderer.movie
                self.assertIsNotNone(movie)
                self.assertTrue(movie.fileName().endswith(key + '.gif'))
                self.assertEqual(self.pet._renderer.emotion, self.pet._tool_reaction.emotion)
            # The same activity from another adapter has identical presentation.
            send_event(CompanionEvent(source='future-plugin', session_id='other',
                       type='waiting_input').to_dict(), descriptor_path=path)
            QTest.qWait(30)
            self.assertEqual(self.pet._cur_key, 'attention')
        finally:
            controller.stop()
            controller.deleteLater()

    def test_repeated_event_does_not_reset_movie_and_decoder_is_released(self):
        reaction = self.pet.character.react(EventType.THINKING)
        self.pet.apply_tool_reaction(reaction, [], [])
        movie = self.pet._renderer.movie
        QTest.qWait(300)
        frame = movie.currentFrameNumber()
        self.pet.apply_tool_reaction(reaction, ['updated session'], [])
        self.assertIs(self.pet._renderer.movie, movie)
        self.assertEqual(movie.currentFrameNumber(), frame)
        for key in ANIMATION_KEYS:
            self.pet._set_image(key)
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertEqual(len(self.pet._renderer.findChildren(QMovie)), 1)

    def test_emotion_intensity_changes_speed_without_replacing_decoder(self):
        from character.contracts import Emotion

        renderer = self.pet._renderer
        movie = renderer.movie
        renderer.set_emotion(Emotion('focused', 0.8))
        self.assertEqual(movie.speed(), 96)
        renderer.set_emotion(Emotion('neutral', 0))
        self.assertEqual(movie.speed(), 80)
        self.assertIs(renderer.movie, movie)

    def test_one_shot_animation_stops_and_preserves_remaining_time_when_hidden(self):
        renderer = self.pet._renderer
        renderer.set_animation(AnimationState('cheerup', loop=False, duration_ms=220))
        QTest.qWait(100)
        self.pet.hide()
        remaining = renderer._remaining_ms
        self.assertGreater(remaining, 0)
        QTest.qWait(250)
        self.assertFalse(renderer._exhausted)
        # Resume directly so ambient idle logic does not replace the test clip.
        renderer.resume()
        QTest.qWait(remaining + 60)
        self.assertTrue(renderer._exhausted)
        self.assertEqual(renderer.movie.state(), QMovie.Paused)

    def test_imported_skin_persists_and_swapping_keeps_interaction(self):
        repository = SkinRepository(self.data.name)
        files = {key: self.pet.character.assets.resolve(key) for key in ANIMATION_KEYS}
        skin = repository.install('测试角色', files, validate_image)
        self.assertEqual(len(repository.list()), 1)
        self.pet._start_pickup()
        self.pet.set_character(skin)
        self.assertEqual(self.pet._state, 'pickup')
        self.assertEqual(self.pet._cur_key, 'pickup')
        self.assertEqual(self.pet._renderer.movie.state(), QMovie.Running)
        repository.select(skin.definition.id)
        selected = repository.selected(default_character(ASSET_ROOT))
        self.assertEqual(selected.definition.id, skin.definition.id)
        self.pet._end_pickup()
        self.pet.set_character(default_character(ASSET_ROOT))
        repository.remove(skin.definition.id)
        self.assertEqual(repository.list(), [])

    def test_invalid_import_is_atomic_and_manifest_cannot_escape(self):
        repository = SkinRepository(self.data.name)
        bad = Path(self.data.name) / 'bad.gif'
        bad.write_bytes(b'GIF89a invalid')
        files = {key: self.pet.character.assets.resolve(key) for key in ANIMATION_KEYS}
        files['think'] = bad
        with self.assertRaises(ValueError):
            repository.install('损坏角色', files, validate_image)
        self.assertEqual(list(repository.root.iterdir()), [])
        root = Path(self.data.name) / 'escaped'
        root.mkdir()
        (root / 'character.json').write_text(json.dumps({
            'schema_version': 1, 'id': 'escaped', 'name': 'bad',
            'animations': {key: '../bad.gif' for key in ANIMATION_KEYS}}))
        with self.assertRaises(ValueError):
            load_definition(root)

    def test_missing_gif_uses_original_png(self):
        default = default_character(ASSET_ROOT)
        default.definition.animations['nothing'] = 'missing.gif'
        self.assertTrue(str(default.assets.resolve('nothing')).endswith('nothing.png'))
        self.pet.set_character(default)
        self.assertIsNone(self.pet._renderer.movie)
        self.assertFalse(self.pet._pixmap.isNull())
