"""Build ~10-minute benchmark audio with an accurate reference transcript.

By default the samples are assembled from Google FLEURS — a multilingual read-
speech corpus with verified transcriptions — concatenating clips until the
target duration is reached. This gives symmetric German and English samples with
ground-truth text. Results are cached on disk so repeat runs don't re-download.

You can also supply your own audio + reference text via ``custom_sample()``.
"""

import logging
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000

# FLEURS clips are unrelated sentences. Concatenated gaplessly they form an
# adversarial signal: VAD-based engines (faster-whisper batched) under-segment
# and drop speech, while context-conditioned decoders (whisper.cpp) fall into
# repetition loops. A short silence between clips lets VAD segment cleanly and
# lets decoders reset between sentences, so every engine is scored fairly.
SILENCE_SECONDS = 0.4

# FLEURS language configs.
FLEURS_CONFIGS = {
    "en": "en_us",
    "de": "de_de",
}


def _resample(array: np.ndarray, sr: int, target: int = SAMPLE_RATE) -> np.ndarray:
    if sr == target:
        return array.astype(np.float32)
    # Linear resample — adequate for benchmark audio assembly.
    n = int(round(len(array) * target / sr))
    x_old = np.linspace(0.0, 1.0, num=len(array), endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=n, endpoint=False)
    return np.interp(x_new, x_old, array).astype(np.float32)


def load_sample(
    language: str,
    target_seconds: float,
    cache_dir: Path,
) -> tuple[np.ndarray, str, float]:
    """Return (audio_float32_16k, reference_text, duration_seconds) for a language."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    tag = f"fleurs_{language}_{int(target_seconds)}s_g{int(SILENCE_SECONDS * 1000)}"
    npy = cache_dir / f"{tag}.npy"
    txt = cache_dir / f"{tag}.txt"

    if npy.exists() and txt.exists():
        log.info("Using cached sample: %s", npy)
        audio = np.load(npy)
        reference = txt.read_text(encoding="utf-8")
        return audio, reference, len(audio) / SAMPLE_RATE

    if language not in FLEURS_CONFIGS:
        raise ValueError(
            f"No FLEURS config for language {language!r}. "
            f"Known: {sorted(FLEURS_CONFIGS)}. Use --audio/--reference for custom data."
        )

    import io

    import soundfile as sf
    from datasets import Audio, load_dataset  # heavy/optional; imported on demand

    log.info("Streaming FLEURS (%s) to assemble ~%.0fs of audio…", language, target_seconds)
    ds = load_dataset(
        "google/fleurs",
        FLEURS_CONFIGS[language],
        split="test",
        streaming=True,
    )
    # Newer `datasets` decodes audio via torchcodec by default; disable decoding
    # and decode the raw bytes with soundfile instead (no torchcodec/ffmpeg dep).
    ds = ds.cast_column("audio", Audio(decode=False))

    silence = np.zeros(int(SILENCE_SECONDS * SAMPLE_RATE), dtype=np.float32)
    chunks: list[np.ndarray] = []
    refs: list[str] = []
    total = 0.0
    for ex in ds:
        audio = ex["audio"]
        raw = audio.get("bytes")
        if raw is None and audio.get("path"):
            raw = Path(audio["path"]).read_bytes()
        data, sr = sf.read(io.BytesIO(raw), dtype="float32", always_2d=False)
        if data.ndim > 1:  # downmix to mono
            data = data.mean(axis=1)
        arr = _resample(np.asarray(data, dtype=np.float32), sr)
        if chunks:
            chunks.append(silence)  # gap between clips (see SILENCE_SECONDS)
        chunks.append(arr)
        refs.append(ex.get("raw_transcription") or ex.get("transcription") or "")
        total += len(arr) / SAMPLE_RATE
        if total >= target_seconds:
            break

    full = np.concatenate(chunks).astype(np.float32)
    reference = " ".join(r.strip() for r in refs if r.strip())
    np.save(npy, full)
    txt.write_text(reference, encoding="utf-8")
    log.info("Assembled %.1fs (%d clips), cached to %s", total, len(chunks), npy)
    return full, reference, len(full) / SAMPLE_RATE


def custom_sample(audio_path: str, reference_path: str) -> tuple[np.ndarray, str, float]:
    """Load a user-supplied audio file + reference transcript."""
    from resona_asr_core.audio import load_audio_path

    audio = load_audio_path(audio_path)
    reference = Path(reference_path).read_text(encoding="utf-8")
    return audio, reference, len(audio) / SAMPLE_RATE
