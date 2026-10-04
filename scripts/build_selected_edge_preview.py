"""Rebuild the selected audition from cached Edge speech, without networking."""
import json
import subprocess
import tempfile
import wave
from pathlib import Path

from build_voice_previews import ROOT, join


def build():
    folder = ROOT / "output/edge-dialogue-study"
    source = folder / "source.wav"
    portions = [
        {"id": "opening", "start": 0.0, "end": 0.5, "speed": 0.8, "pitch": 1.05},
        {"id": "ending", "start": 1.1, "end": None, "speed": 1.18, "pitch": 1.10},
    ]
    with tempfile.TemporaryDirectory(prefix="mori-selected-edge-") as directory:
        staging = Path(directory)
        for portion in portions:
            filters = f"atrim=start={portion['start']}"
            if portion["end"] is not None:
                filters += f":end={portion['end']}"
            filters += (
                f",asetpts=PTS-STARTPTS,asetrate={24000 * portion['pitch']}"
                f",aresample=24000,atempo={portion['speed'] / portion['pitch']}"
            )
            subprocess.run([
                "ffmpeg", "-v", "error", "-y", "-i", str(source), "-af", filters,
                "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le",
                str(staging / f"{portion['id']}.wav"),
            ], check=True)
            with wave.open(str(staging / f"{portion['id']}.wav"), "rb") as audio:
                portion["output_frames"] = audio.getnframes()
        lead = staging / "lead.wav"
        with wave.open(str(lead), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(24000)
            audio.writeframes(b"\x00\x00" * 6000)
        output = folder / "selected.wav"
        join([(lead, 0), (staging / "opening.wav", 100),
              (staging / "ending.wav", 0)], output)
    record = {
        "text": "搞定，还得是我", "source": "output/edge-dialogue-study/source.wav",
        "source_provider": "Edge TTS zh-CN-XiaoyiNeural",
        "processing": "local cached audio edit, not a new API response",
        "portions": portions, "lead_in_ms": 250, "extra_clause_gap_ms": 100,
        "selection": "用户认可清楚的开头，后半句选择更清亮、更快版",
        "online_latency_verified": False,
        "scope": "这句试听的分段节奏；不代表整段对话必须加快18%",
    }
    (folder / "selected.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(f"Built {output.relative_to(ROOT)}")


if __name__ == "__main__":
    build()
