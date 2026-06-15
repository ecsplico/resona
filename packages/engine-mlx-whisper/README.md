# resona-engine-mlx-whisper

Apple MLX Whisper backend for [Resona](https://github.com/ecsplico/resona).

GPU-accelerated transcription on Apple Silicon. Runs the same Whisper model
sizes as `resona-engine-faster-whisper`, but offloads to the Mac GPU via the
[MLX](https://github.com/ml-explore/mlx) framework instead of running on the
CPU — typically a large speedup on M-series chips.

**Apple Silicon (arm64 macOS) only.** Models are HuggingFace repos of
MLX-converted weights (e.g. `mlx-community/whisper-large-v3-mlx`); configure with
`DEFAULT_MLX_WHISPER_MODEL`.

See the [main repository](https://github.com/ecsplico/resona) for documentation.
