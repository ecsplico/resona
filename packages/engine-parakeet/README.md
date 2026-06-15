# resona-engine-parakeet

NVIDIA **Parakeet** (NeMo FastConformer) backend for Resona — fast, multilingual
transcription on CUDA GPUs (and CPU). The default `parakeet-tdt-0.6b-v3` covers
25 European languages including **German and English**.

It speaks the standard Resona `Transcriber` contract, so it works for both
`POST /transcribe` and the live `/ws/live` path. In `resona live` it is driven
incrementally by the framework's windowed local-agreement path — no extra setup.

## Install

Linux + NVIDIA CUDA is the recommended target (NeMo is Linux/CUDA-first):

```bash
uv sync --package resona-engine-parakeet
```

The `nemo_toolkit[asr]` dependency is gated to `sys_platform == 'linux'`, so a
macOS `uv sync --all-packages` stays green; the entry point still registers on
macOS but selecting the engine raises on the missing NeMo import. On Apple
Silicon use `resona-engine-mlx-whisper` instead.

## Configure

| Variable | Default | Description |
|----------|---------|-------------|
| `RESONA_ENGINE` | `faster-whisper` | Set to `parakeet` to select this engine |
| `DEFAULT_PARAKEET_MODEL` | `nvidia/parakeet-tdt-0.6b-v3` | NeMo model name (multilingual DE+EN) |
| `RESONA_PARAKEET_STREAMING` | `false` | Opt in to the native cache-aware streaming session (see below) |

```bash
RESONA_ENGINE=parakeet uv run resona-engine-parakeet      # :7001
# or via the CLI's in-process path:
uv run resona transcribe ./audio/ --engine parakeet
uv run resona live --engine parakeet --language de
```

## Native cache-aware streaming (experimental, opt-in)

By default the engine is a batch `Transcriber`; live transcription works through
the framework's windowed local-agreement fallback. For lower latency, NeMo
**cache-aware streaming** is available behind `RESONA_PARAKEET_STREAMING=1` — but
only when the loaded model is a cache-aware streaming model (e.g. a
`*_streaming_*` FastConformer). With any other model the flag is ignored and the
engine stays batch-only.

When enabled and supported, the engine exposes a native `StreamSession` that
`LiveTranscriber` uses instead of re-transcribing overlapping windows. This path
must be validated on GPU hardware with a streaming-capable checkpoint; treat it
as experimental.
```bash
RESONA_PARAKEET_STREAMING=1 RESONA_ENGINE=parakeet \
  DEFAULT_PARAKEET_MODEL=nvidia/stt_en_fastconformer_hybrid_large_streaming_multi \
  uv run resona live --engine parakeet --language en
```

## Docker

The engine builds on `nvidia/cuda:12.8.0-runtime-ubuntu24.04` like the other
GPU engines (see the project Docker notes). NeMo model weights download on first
use and are cached.
