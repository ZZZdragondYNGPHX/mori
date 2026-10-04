"""Build audition files; this does not enable sound in the companion.

Requires edge-tts in a separate build environment, ffmpeg, and KDE Ocean sounds.
Only the fixed audition text is sent to the online speech service.
"""
import argparse
import asyncio
import json
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def convert(source, destination, volume=0.85, cue=False):
    filters = "silenceremove=start_periods=1:start_duration=0.02:start_threshold=-48dB"
    if cue:
        filters += ",areverse,silenceremove=start_periods=1:start_duration=0.02:start_threshold=-48dB,areverse"
        filters += ",afade=t=out:st=0.66:d=0.06"
    filters += f",volume={volume}"
    command = ["ffmpeg", "-v", "error", "-y", "-i", str(source)]
    if cue:
        command += ["-t", "0.72"]
    command += ["-af", filters, "-ar", "24000", "-ac", "1",
                "-c:a", "pcm_s16le", str(destination)]
    subprocess.run(command, check=True)


def join(parts, destination):
    with wave.open(str(destination), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24000)
        for source, pause_ms in parts:
            with wave.open(str(source), "rb") as audio:
                assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, 24000)
                output.writeframes(audio.readframes(audio.getnframes()))
            output.writeframes(b"\x00\x00" * (24000 * pause_ms // 1000))


async def build(manifest):
    import edge_tts

    specification = json.loads(manifest.read_text(encoding="utf-8"))
    folder = manifest.parent
    cue_folder = Path(specification["cue_source_directory"])
    for child in ("cues", "voices", "previews"):
        (folder / child).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mori-voice-build-") as temporary:
        staging = Path(temporary)
        for clip in specification["clips"]:
            name = clip["id"]
            cue = folder / "cues" / f"{name}.wav"
            cue_source = cue_folder / clip["cue_source"]
            convert(cue_source, cue, volume=0.65, cue=True)
            attribution = cue_source.with_suffix(cue_source.suffix + ".license")
            shutil.copyfile(attribution, cue.with_suffix(".wav.license"))
            parts = []
            for number, segment in enumerate(clip["segments"]):
                mp3 = staging / f"{name}-{number}.mp3"
                pcm = mp3.with_suffix(".wav")
                speaker = edge_tts.Communicate(
                    segment["text"], specification["voice"],
                    rate=segment["rate"], pitch=segment["pitch"],
                    connect_timeout=10, receive_timeout=20,
                )
                await asyncio.wait_for(speaker.save(str(mp3)), timeout=40)
                convert(mp3, pcm)
                parts.append((pcm, segment["pause_after_ms"]))
            voice = folder / "voices" / f"{name}.wav"
            preview = folder / "previews" / f"{name}.wav"
            join(parts, voice)
            join([(cue, 180), (voice, 0)], preview)
            print(f"Built {preview.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path,
                        default=ROOT / "output/edge-dialogue-study/build-feedback.json")
    args = parser.parse_args()
    asyncio.run(build(args.manifest.resolve()))
