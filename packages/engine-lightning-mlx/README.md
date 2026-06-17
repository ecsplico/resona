# resona-engine-lightning-mlx

[Lightning Whisper MLX](https://github.com/mustafaaljadery/lightning-whisper-mlx)
backend for [Resona](https://github.com/ecsplico/resona).

Batched MLX inference on the Apple Silicon GPU — the fastest option for
long-form audio on a Mac, at the same model sizes as the other Whisper engines.

**Apple Silicon (arm64 macOS) only.** Configure with
`DEFAULT_LIGHTNING_MLX_MODEL` (`large-v3`, `distil-large-v3`, …),
`LIGHTNING_MLX_BATCH_SIZE`, and `LIGHTNING_MLX_QUANT` (`none`/`4bit`/`8bit`).

Limitations: transcribes from a file path (Resona writes a temp WAV) and does
not support `initial_prompt` (ignored).

See the [main repository](https://github.com/ecsplico/resona) for documentation.
