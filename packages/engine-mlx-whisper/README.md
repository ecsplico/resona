# resona-engine-mlx-whisper

Apple Silicon (MLX/Metal) Whisper backend for Resona — fast **local GPU**
transcription on macOS without CUDA or PyTorch.

MLX runs Whisper on the Apple Silicon GPU, so this is the recommended local
engine on M-series Macs. It speaks the standard Resona `Transcriber` contract,
so it works for both `POST /transcribe` and the live `/ws/live` path.

## Install

macOS (Apple Silicon) only:

```bash
uv tool install --from ./apps/resona-cli 'resona-cli[mlx]'
# or in the workspace dev env
uv sync --package resona-engine-mlx-whisper
```

There is no Docker image: MLX requires a Metal GPU and cannot run in a Linux
container. Use `resona-engine-faster-whisper` for containerized/Linux deployments.

## Configure

| Variable | Default | Description |
|----------|---------|-------------|
| `RESONA_ENGINE` | `faster-whisper` | Set to `mlx-whisper` to select this engine |
| `DEFAULT_MLXWHISPER_MODEL` | `mlx-community/whisper-large-v3-turbo` | MLX-community Whisper repo (German+English) |

```bash
RESONA_ENGINE=mlx-whisper uv run resona-engine-mlx-whisper   # :7001
# or via the CLI's in-process path:
uv run resona transcribe ./audio/ --engine mlx-whisper
uv run resona live --engine mlx-whisper --language de
```

The `device` constructor argument is accepted for protocol compatibility but
ignored — MLX always targets Metal.
