# resona-tts-local

Local / offline **text-to-speech** engines for Resona — the generation
counterpart to the local ASR engines. Ported from
[Voicebox](https://github.com/jamiepine/voicebox)'s TTS backends into Resona's
plugin style.

Unlike `resona-cloud-tts` (OpenAI / ElevenLabs / Deepgram), these run entirely
on your machine.

## Engines

| Name | Library | Cloning | Presets | Instruct | Languages |
|------|---------|---------|---------|----------|-----------|
| `kokoro` | `kokoro` | – | ✅ | – | 8 |
| `chatterbox` | `chatterbox-tts` | ✅ zero-shot | – | – | 23 |
| `chatterbox-turbo` | `chatterbox-tts` | ✅ zero-shot | – | – (paralinguistic tags) | en |
| `qwen` | `mlx-audio` (mac) / `qwen-tts` | ✅ zero-shot | – | ✅ | 10 |
| `qwen-custom-voice` | `qwen-tts` | – | ✅ (9 speakers) | ✅ | 10 |

## Install

The engine **code** ships with the package; each engine lazy-imports its native
model library on first use. From a clone, the `just` recipes are the easiest way
to add an engine's libs to the workspace venv:

```bash
just tts-kokoro       # Kokoro-82M — tiny, CPU-realtime (start here)
just tts-qwen         # Qwen3-TTS on Apple Silicon (mlx-audio)
just tts-chatterbox   # Chatterbox ML + Turbo (--no-deps recipe, one step)
```

### pip equivalents

Two engines are **lockable extras** (they co-resolve with Resona's modern
`numpy>=2.1` / `torch>=2.10` stack):

```bash
pip install 'resona-tts-local[kokoro]'    # tiny, CPU-realtime — start here
pip install 'resona-tts-local[qwen]'      # Qwen3-TTS on Apple Silicon (mlx-audio, MLX-native)
```

### Chatterbox (and Turbo) — `--no-deps`

`chatterbox-tts` declares *conservative* pins (`numpy<2.0`, `torch==2.6.0`,
exact `transformers`) that block co-locking — the same clash Voicebox sidesteps
with `--no-deps`. The pins are **not** real ABI limits: chatterbox imports and
runs on `torch 2.12 / transformers 5.8 / numpy 2.2` (verified). `just
tts-chatterbox` runs this for you, or manually:

```bash
uv pip install --no-deps chatterbox-tts
uv pip install librosa s3tokenizer diffusers resemble-perth conformer \
               omegaconf pykakasi pyloudnorm spacy-pkuseg einops
```

### Qwen3-TTS on PyTorch (Linux/CUDA) — caveat

`qwen-tts` (PyTorch) currently **breaks on `transformers>=5.8`**
(`check_model_inputs` signature change) and pins `transformers==4.57.3`, so it
is not reliable on Resona's stack. On Apple Silicon use the `qwen` extra
(mlx-audio) instead; on Linux, either pin a compatible transformers in an
isolated env or track upstream
[QwenLM/Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS).

Selecting an engine whose library is absent raises `EngineUnavailableError`
with the exact install command.

## Use

```python
from resona_tts_local import get_engine

result = get_engine("kokoro").synthesize("Hallo Welt", language="de")
open("out.wav", "wb").write(result["audio"])          # audio/wav

# zero-shot voice cloning
get_engine("chatterbox").synthesize(
    "Cloned voice", language="en", ref_audio="sample.wav",
)

# delivery instruction (Qwen)
get_engine("qwen").synthesize("Hi there", instruct="speak slowly and softly")
```

`get_engine` returns a memoized, lazily-loaded engine; the model loads on the
first `synthesize`. Selecting an engine whose extra is not installed raises
`EngineUnavailableError` with an install hint.

## Model cache

All engines call `resona_asr_core.model_cache.configure_model_cache()`, so they
share the HuggingFace hub cache with Resona's ASR engines and with Voicebox. Set
`RESONA_MODELS_DIR` (or reuse Voicebox's `VOICEBOX_MODELS_DIR`) to relocate it.
