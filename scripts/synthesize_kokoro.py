"""Synthesize a short Chinese utterance with the downloaded CPU Kokoro model."""
import argparse
import json
import resource
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def synthesize(model_dir, text, output, speaker_id=3, threads=4):
    import sherpa_onnx
    import soundfile as sf

    config = sherpa_onnx.OfflineTtsConfig(
        model=sherpa_onnx.OfflineTtsModelConfig(
            kokoro=sherpa_onnx.OfflineTtsKokoroModelConfig(
                model=str(model_dir / "model.int8.onnx"),
                voices=str(model_dir / "voices.bin"),
                tokens=str(model_dir / "tokens.txt"),
                data_dir=str(model_dir / "espeak-ng-data"),
                lexicon=",".join(str(model_dir / name) for name in
                                 ("lexicon-us-en.txt", "lexicon-zh.txt")),
            ),
            num_threads=threads, provider="cpu", debug=False,
        ),
        rule_fsts=",".join(str(model_dir / name) for name in
                           ("date-zh.fst", "number-zh.fst", "phone-zh.fst")),
        max_num_sentences=1,
    )
    if not config.validate():
        raise ValueError("Invalid local Kokoro model configuration")
    started = time.perf_counter()
    tts = sherpa_onnx.OfflineTts(config)
    loading_seconds = time.perf_counter() - started
    started = time.perf_counter()
    audio = tts.generate(text, sid=speaker_id, speed=1.0)
    generation_seconds = time.perf_counter() - started
    if not len(audio.samples):
        raise ValueError("Kokoro returned no audio")
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output, audio.samples, audio.sample_rate, subtype="PCM_16")
    audio_seconds = len(audio.samples) / audio.sample_rate
    report = {
        "model": "Kokoro-82M-v1.1-zh INT8", "model_directory": str(model_dir),
        "provider": "cpu", "threads": threads, "speaker_id": speaker_id,
        "speaker_name": "zf_001" if speaker_id == 3 else None,
        "text": text, "sample_rate": audio.sample_rate,
        "loading_seconds": round(loading_seconds, 3),
        "generation_seconds": round(generation_seconds, 3),
        "audio_seconds": round(audio_seconds, 3),
        "real_time_factor": round(generation_seconds / audio_seconds, 3),
        "process_peak_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
        "measurement_note": "One short CPU utterance; excludes model loading from generation time",
    }
    output.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, default=Path.home() /
                        ".cache/mori-tts/kokoro-zh-int8/kokoro-int8-multi-lang-v1_1")
    parser.add_argument("--text", default="说吧，我听着")
    parser.add_argument("--output", type=Path, default=ROOT / "output/kokoro-local-study/voice-sample.wav")
    parser.add_argument("--speaker-id", type=int, default=3)
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    synthesize(args.model_dir.resolve(), args.text, args.output.resolve(), args.speaker_id, args.threads)
