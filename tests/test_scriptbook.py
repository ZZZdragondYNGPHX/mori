"""Offline export contracts for persistent recordings and clean TTS input."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from export_dialogue import export

from character.scriptbook import build_scriptbook


class ScriptbookTests(unittest.TestCase):
    def test_recording_ids_survive_reordering_and_added_lines(self):
        before = build_scriptbook({"friend.idle": ("喂，来了？", "干嘛？")})
        after = build_scriptbook({"friend.idle": ("新句子。", "干嘛？", "喂，来了？")})
        old_ids = {line["text"]: line["id"] for line in before["lines"]}
        new_ids = {line["text"]: line["id"] for line in after["lines"]}
        self.assertTrue(old_ids.items() <= new_ids.items())
        edited = build_scriptbook({"friend.idle": ("喂，来啦？",)})
        self.assertNotIn(edited["lines"][0]["id"], old_ids.values())

    def test_repeated_speech_reuses_one_recording_and_preserves_uses(self):
        book = build_scriptbook({"friend.idle": ("干嘛？",), "touch.body": ("干嘛？",)})
        self.assertEqual(len(book["lines"]), 1)
        self.assertEqual(book["lines"][0]["groups"], ["friend.idle", "touch.body"])

    def test_dynamic_placeholders_are_separate_from_tts_input(self):
        book = build_scriptbook({"touch.body": ("干嘛？",), "template.count": ("共{count}份",)})
        self.assertEqual([line["tts_text"] for line in book["lines"]], ["干嘛？"])
        self.assertEqual(book["templates"][0]["fields"], ["count"])
        with self.assertRaises(ValueError):
            build_scriptbook({"touch.body": ("共{count}份",)})

    def test_exported_formats_share_audio_mapping_and_have_clean_text(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            export(folder)
            book = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
            batch = [json.loads(line) for line in (folder / "lines.jsonl").read_text(encoding="utf-8").splitlines()]
            sentences = (folder / "sentences.txt").read_text(encoding="utf-8").splitlines()
        self.assertEqual(book["lines"], batch)
        self.assertEqual(sentences, [line["tts_text"] for line in batch])
        self.assertEqual(len({line["audio_file"] for line in batch}), len(batch))
        self.assertEqual({template["group"] for template in book["templates"]}, {
            "template.files.count", "template.scroll.dependencies",
            "template.scroll.connect_failed", "template.scroll.api_failed",
        })
        for line in batch:
            self.assertEqual(line["text"], line["tts_text"])
            self.assertTrue(line["instruct"])
            self.assertTrue(line["audio_file"].endswith(line["id"] + ".wav"))
            for marker in ("本小姐", "主人", "～", "{", "}", "（", "）", "[", "]"):
                self.assertNotIn(marker, line["tts_text"])
