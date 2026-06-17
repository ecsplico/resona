#!/usr/bin/env python
"""Dump actual transcripts for suspect backends to investigate WER outliers.

Reuses the cached FLEURS samples written by transcription_benchmark.py. Runs the
named backends, saves each hypothesis next to the reference, and prints WER plus
a head-to-head preview so we can see *why* a backend scored badly.
"""

import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from _metrics import normalize, score  # noqa: E402

CACHE = HERE / "cache"
OUT = HERE / "diagnose"
OUT.mkdir(exist_ok=True)

BACKENDS = {
    "faster-whisper": lambda: __import__(
        "resona_engine_faster_whisper.transcriber", fromlist=["FastWhisperTranscriber"]
    ).FastWhisperTranscriber(device="cpu", modelname="large-v3"),
    "whisper-cpp": lambda: __import__(
        "resona_engine_whispercpp.transcriber", fromlist=["WhisperCppTranscriber"]
    ).WhisperCppTranscriber(modelname="large-v3"),
    "lightning-mlx": lambda: __import__(
        "resona_engine_lightning_mlx.transcriber", fromlist=["LightningMLXTranscriber"]
    ).LightningMLXTranscriber(modelname="large-v3"),
}


def load(lang):
    # Match the gapped cache tag written by _datasets.py (fleurs_<lang>_600s_g400).
    base = next(CACHE.glob(f"fleurs_{lang}_600s*.npy"))
    audio = np.load(base)
    ref = base.with_suffix(".txt").read_text(encoding="utf-8")
    return audio, ref


def main():
    which = sys.argv[1:] or ["faster-whisper", "whisper-cpp"]
    langs = ["de", "en"]
    for key in which:
        print(f"\n{'='*70}\n{key}\n{'='*70}")
        engine = BACKENDS[key]()
        for lang in langs:
            audio, ref = load(lang)
            result = engine.transcribe(audio, language=lang)
            hyp = result.get("text", "")
            (OUT / f"{key}_{lang}.txt").write_text(hyp, encoding="utf-8")
            m = score(ref, hyp)
            nh, nr = normalize(hyp), normalize(ref)
            print(f"\n--- {key} [{lang}] WER={m['wer']:.3f} CER={m['cer']:.3f} "
                  f"ref_words={m['ref_words']} hyp_words={m['hyp_words']} "
                  f"n_segments={len(result.get('segments', []))}")
            print(f"  REF[:300]: {nr[:300]}")
            print(f"  HYP[:300]: {nh[:300]}")
            # tail, where hallucinated repetition usually shows up
            print(f"  HYP[-300:]: {nh[-300:]}")


if __name__ == "__main__":
    main()
