"""Try full-utterance online speech through Edge TTS without an API key.

Requires edge-tts and ffmpeg. This writes audition files and measures the
first audio chunk; it does not enable speech playback in Mori.
"""
import argparse
import asyncio
import json
import tempfile
import time
import wave
from pathlib import Path

from build_voice_previews import ROOT, convert


async def synthesize(text, output, voice, rate, pitch):
    import edge_tts

    if not text.strip():
        raise ValueError("Text must not be empty")
    if output.suffix.lower() != ".wav":
        raise ValueError("Output must be a WAV file")
    output.parent.mkdir(parents=True, exist_ok=True)
    speaker = edge_tts.Communicate(
        text, voice, rate=rate, pitch=pitch,
        connect_timeout=10, receive_timeout=20,
    )
    started = time.perf_counter()
    first_audio_seconds = None
    audio_chunks = 0
    with tempfile.TemporaryDirectory(prefix="mori-edge-audio-") as temporary:
        mp3 = Path(temporary) / "speech.mp3"
        async with asyncio.timeout(40):
            with mp3.open("wb") as stream:
                async for chunk in speaker.stream():
                    if chunk["type"] == "audio" and chunk["data"]:
                        if first_audio_seconds is None:
                            first_audio_seconds = time.perf_counter() - started
                        stream.write(chunk["data"])
                        audio_chunks += 1
        if first_audio_seconds is None:
            raise ValueError("Edge returned no audio")
        request_seconds = time.perf_counter() - started
        # Publish only after synthesis and decoding have both succeeded.
        with tempfile.NamedTemporaryFile(
            prefix=".edge-", suffix=".wav", dir=output.parent, delete=False,
        ) as staging:
            pcm = Path(staging.name)
        try:
            convert(mp3, pcm)
            with wave.open(str(pcm), "rb") as audio:
                if audio.getnframes() == 0:
                    raise ValueError("Decoded audio is empty")
                audio_seconds = audio.getnframes() / audio.getframerate()
            pcm.replace(output)
        finally:
            pcm.unlink(missing_ok=True)
    report = {
        "provider": "Microsoft Edge online TTS via edge-tts",
        "voice": voice, "rate": rate, "pitch": pitch, "text": text,
        "first_audio_seconds": round(first_audio_seconds, 3),
        "request_seconds": round(request_seconds, 3),
        "audio_seconds": round(audio_seconds, 3), "audio_chunks": audio_chunks,
        "text_submission": "full utterance, not individual characters",
        "measurement_note": "Network receipt of first chunk, not playback latency",
        "listening_status": "awaiting_user_review",
    }
    output.with_suffix(".json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", default="说吧，我听着。刚才那个问题，我们接着聊。")
    parser.add_argument("--voice", default="zh-CN-XiaoyiNeural")
    parser.add_argument("--rate", default="+0%")
    parser.add_argument("--pitch", default="+10Hz")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "output/edge-dialogue-study/xiaoyi.wav")
    args = parser.parse_args()
    try:
        asyncio.run(synthesize(args.text, args.output.resolve(), args.voice,
                               args.rate, args.pitch))
    except Exception as error:
        # Service errors can contain signed URLs; do not echo those in logs.
        raise SystemExit(f"Edge synthesis failed ({type(error).__name__}).") from None
