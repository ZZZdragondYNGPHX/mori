"""Export Mori's authored lines locally; never load or call a TTS engine."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from character.scriptbook import build_scriptbook  # noqa: E402


def export(output_dir):
    book = build_scriptbook()
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "manifest.json").write_text(
        json.dumps(book, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    (output_dir / "lines.jsonl").write_text(
        "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in book["lines"]),
        encoding="utf-8",
    )
    (output_dir / "sentences.txt").write_text(
        "".join(line["tts_text"] + "\n" for line in book["lines"]), encoding="utf-8",
    )
    print(f"Exported {len(book['lines'])} spoken lines and {len(book['templates'])} templates to {output_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output/tts-scriptbook")
    args = parser.parse_args()
    export(args.output_dir.resolve())
