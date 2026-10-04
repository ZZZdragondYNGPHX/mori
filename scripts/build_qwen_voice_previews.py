"""Generate Mori audition clips with an already downloaded Qwen model.

Run with the separate qwen-tts environment. Inference is local and offline;
the companion does not load the model or start a speech server.
"""
import argparse
import json
import shutil
import tempfile
import time
from pathlib import Path

from build_voice_previews import ROOT, convert, join


def build(manifest, model_dir):
    import numpy as np
    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel

    specification = json.loads(manifest.read_text(encoding="utf-8"))
    folder = manifest.parent
    if not torch.cuda.is_available():
        raise RuntimeError("This trial requires the NVIDIA CUDA GPU")
    for child in ("cues", "voices", "previews"):
        (folder / child).mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(4)
    torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    print("Loading Qwen3-TTS 1.7B locally (BF16, SDPA)...", flush=True)
    model = Qwen3TTSModel.from_pretrained(
        str(model_dir), device_map="cuda:0", dtype=torch.bfloat16,
        attn_implementation="sdpa", local_files_only=True,
    )
    torch.cuda.synchronize()
    loading_seconds = time.perf_counter() - started
    print(f"Model loaded in {loading_seconds:.1f}s", flush=True)
    measurements = []
    with tempfile.TemporaryDirectory(prefix="mori-qwen-audio-") as temporary:
        staging = Path(temporary)
        for clip in specification["clips"]:
            name = clip["id"]
            source = (folder / specification["cue_source_directory"] / clip["cue_source"]).resolve()
            cue = folder / "cues" / f"{name}.wav"
            shutil.copyfile(source, cue)
            shutil.copyfile(source.with_suffix(".wav.license"), cue.with_suffix(".wav.license"))
            torch.manual_seed(clip["seed"])
            print(f"Generating {name}: {clip['text']}", flush=True)
            started = time.perf_counter()
            with torch.inference_mode():
                arguments = dict(text=clip["text"], language="Chinese",
                                 instruct=clip["instruct"], max_new_tokens=128)
                if specification.get("generation_mode") == "voice_design":
                    waves, sample_rate = model.generate_voice_design(**arguments)
                else:
                    waves, sample_rate = model.generate_custom_voice(
                        speaker=specification["voice"], **arguments,
                    )
            torch.cuda.synchronize()
            generation_seconds = time.perf_counter() - started
            samples = np.asarray(waves[0], dtype=np.float32)
            if not samples.size or not np.isfinite(samples).all():
                raise ValueError(f"Invalid audio for {name}")
            peak = float(np.max(np.abs(samples)))
            if peak < 0.0001:
                raise ValueError(f"Silent audio for {name}")
            # Preserve relative dynamics while leaving headroom in the WAV.
            samples *= min(3.0, 0.8 / peak)
            raw = staging / f"{name}.wav"
            sf.write(raw, samples, sample_rate, subtype="PCM_16")
            voice = folder / "voices" / f"{name}.wav"
            preview = folder / "previews" / f"{name}.wav"
            convert(raw, voice)
            join([(cue, 180), (voice, 0)], preview)
            measurements.append({
                "id": name, "generation_seconds": round(generation_seconds, 3),
                "native_audio_seconds": round(len(samples) / sample_rate, 3),
                "native_sample_rate": sample_rate,
                "torch_peak_allocated_mib": round(torch.cuda.max_memory_allocated() / 2**20, 1),
                "torch_peak_reserved_mib": round(torch.cuda.max_memory_reserved() / 2**20, 1),
            })
            print(f"Built {preview.relative_to(ROOT)} in {generation_seconds:.1f}s", flush=True)
    report = {
        "model": specification["model"], "revision": specification["model_revision"],
        "generation_mode": specification.get("generation_mode", "custom_voice"),
        "speaker": specification["voice"], "device": torch.cuda.get_device_name(),
        "torch_version": torch.__version__, "cuda_version": torch.version.cuda,
        "dtype": "bfloat16", "attention": "sdpa", "inference_network": "offline",
        "loading_seconds": round(loading_seconds, 3), "clips": measurements,
        "memory_note": "PyTorch allocator peaks; excludes CUDA context and other desktop processes",
    }
    (folder / "benchmark.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    specification["status"] = "qwen_local_trial_generated"
    manifest.write_text(json.dumps(specification, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path,
                        default=ROOT / "output/voice-selected-qwen/pending-generation.json")
    parser.add_argument("--model-dir", type=Path,
                        default=Path.home() / ".cache/mori-tts/qwen3-voicedesign-1.7b/model")
    args = parser.parse_args()
    build(args.manifest.resolve(), args.model_dir.resolve())
