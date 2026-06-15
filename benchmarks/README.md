# Resona transcription benchmarks

`transcription_benchmark.py` runs every available ASR backend over the same
~10-minute German **and** English audio and writes a log capturing:

- **Hardware / environment** (chip, cores, memory, Python, tuning env vars)
- **Model** used per backend (large-v3 family — same size, fair comparison)
- **Speed** — transcription time, real-time factor (RTF) and × realtime
- **Accuracy** — word error rate (WER) and character error rate (CER) vs a
  ground-truth reference

Backends not installed are skipped (with a logged reason), so it works after a
full `uv sync --all-packages` or with any subset of engine extras.

## Audio + reference

By default the samples are assembled from [Google FLEURS](https://huggingface.co/datasets/google/fleurs)
(`de_de` and `en_us`) — read speech with verified transcriptions — concatenating
clips up to the target duration. The assembled audio + reference are cached under
`benchmarks/cache/` so repeat runs don't re-download.

Bring your own instead with `--audio file.wav --reference file.txt` (single
`--languages` value).

## Running

The benchmark needs `jiwer` (WER/CER), and `datasets` + `soundfile` (only when
downloading FLEURS). Install the engines you want to compare first:

```bash
uv sync --all-packages          # installs all engines (incl. Apple MLX ones on a Mac)

uv run --with jiwer --with datasets --with soundfile \
    python benchmarks/transcription_benchmark.py
```

Useful flags:

```bash
# subset of backends, shorter sample
--backends faster-whisper,mlx-whisper,whisper-cpp --target-seconds 300

# German only, custom file
--languages de --audio dictation.wav --reference dictation.txt

# include model-load/compile time in the measurement
--no-warmup
```

Backend keys: `faster-whisper`, `mlx-whisper`, `whisper-cpp`, `lightning-mlx`,
`whisper`, `voxtral`.

## Output

Logs are written to `benchmarks/results/benchmark_<UTC-timestamp>.{md,json}`.
The markdown has a per-language results table sorted fastest-first; the JSON has
the full structured data.

> The MLX (`mlx-whisper`, `lightning-mlx`) engines and Metal-accelerated
> `whisper-cpp` use the Apple GPU; `faster-whisper` runs on the CPU. Expect the
> GPU engines to win on speed at the same model size on Apple Silicon.
