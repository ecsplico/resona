# CLAUDE.md — Resona development guide

## Project overview

Resona is a modular transcription platform with pluggable ASR engines and a composable postprocessing pipeline. Designed for German medical dictation but usable for any language.

## Project structure

```
resona/
├── pyproject.toml          ← workspace root
├── uv.lock
├── docker-compose.resona.yml
├── benchmarks/             ← standalone backend speed+accuracy benchmark (not a workspace member)
├── apps/
│   ├── resona-cli/         ← resona: typer CLI (watch, transcribe, profiles, rec/live/ui TUIs)
│   └── web/                ← browser UI (PWA dictaphone, live page) — plain HTML/JS
└── packages/
    ├── asr-core/           ← resona-asr-core: lean ASR contracts (protocol, registry, audio, live_transcriber). No FastAPI.
    ├── engine-server/      ← resona-engine-server: FastAPI HTTP/WS app, :7001. Depends on asr-core.
    ├── engine-faster-whisper/ ← resona-engine-faster-whisper: CTranslate2 engine (default, CPU)
    ├── engine-whisper/     ← resona-engine-whisper: OpenAI Whisper (PyTorch) engine
    ├── engine-voxtral/     ← resona-engine-voxtral: HuggingFace Transformers engine
    ├── engine-mlx-whisper/ ← resona-engine-mlx-whisper: Apple MLX engine (GPU, macOS arm64)
    ├── engine-whispercpp/  ← resona-engine-whispercpp: whisper.cpp engine (Metal, via pywhispercpp)
    ├── engine-lightning-mlx/ ← resona-engine-lightning-mlx: batched MLX engine (GPU, macOS arm64)
    ├── engine-parakeet/    ← resona-engine-parakeet: NVIDIA Parakeet (NeMo FastConformer) engine (Linux CUDA/CPU, multilingual)
    ├── cloud-stt/          ← resona-cloud-stt: cloud STT providers (Deepgram, ElevenLabs, OpenAI)
    ├── cloud-tts/          ← resona-cloud-tts: cloud TTS providers (OpenAI, ElevenLabs, Deepgram)
    ├── tts-local/          ← resona-tts-local: local/offline TTS engines (Kokoro, Chatterbox, Qwen3-TTS) — ported from Voicebox
    ├── postprocess/        ← resona-postprocess: profile-based postprocessing pipeline (replacements + LLM + extract)
    ├── api/                ← resona-api: job queue + DB + postprocessing + unified STT/TTS API, :7000
    └── client/             ← resona-client: httpx client library
```

- `apps/` contains end-user applications (CLI tool, web front-end).
- `packages/` contains the services and libraries they depend on.
- Each Python package follows src-layout: `<root>/src/<module>/`.

Directus and the transcribe worker live in the separate `resona-pwa` repo; resona exposes only resona-api + engines.

## The stateless engine contract

**resona-engine-server has no database and no persistent state.** Every request must be self-contained.

- `POST /transcribe` accepts `audio_file`, `language`, `task`, `initial_prompt`, `vad_filter`, `word_timestamps`
- The engine returns `{text, language, segments}` — raw transcript only
- **No replacements or postprocessing in the engine** — that is the caller's responsibility
- The engine never reads from or writes to a database
- The engine never deletes audio files

When adding functionality to engine-server, ask: "can this be done with only what's in the HTTP request?" If it requires a DB lookup or postprocessing, it belongs in resona-api or resona-postprocess.

## Engine discovery via entry points

Engines register themselves in their `pyproject.toml`:

```toml
[project.entry-points."resona.engines"]
faster-whisper = "resona_engine_faster_whisper.transcriber:FastWhisperTranscriber"
```

The registry in `resona_asr_core/registry.py` discovers engines at runtime:
- Engine selection priority: explicit arg to `get_transcriber()` > `RESONA_ENGINE` env var > **environment-aware default** (`recommended_engine()`)
- `recommended_engine(installed=None)` picks the best installed engine for the host: on **Apple Silicon** it prefers the GPU-native engines (`mlx-whisper` → `lightning-mlx` → `whisper-cpp` → `faster-whisper`), informed by `benchmarks/`; elsewhere (CUDA/CPU Linux) it prefers `faster-whisper` (which uses the GPU when present). `installed_engines()` lists discoverable engines. `_is_apple_silicon()` gates the platform branch.
- `get_transcriber()` returns a thread-safe singleton
- Each engine's `[project.scripts]` points to `resona_engine_server.run:main` — same FastAPI app, different engine

Available engines: `faster-whisper`, `whisper`, `voxtral`, `mlx-whisper`, `whisper-cpp`, `lightning-mlx`, `parakeet` (NVIDIA NeMo, Linux CUDA/CPU). The default is environment-aware (`recommended_engine()`): `mlx-whisper` on Apple Silicon when installed, else `faster-whisper`.

On Apple Silicon, `faster-whisper` runs CPU-only (CTranslate2 has no Metal backend). For GPU acceleration on a Mac at the same model size, use `mlx-whisper` / `lightning-mlx` (MLX) or `whisper-cpp` (Metal). See `benchmarks/` for a speed+accuracy comparison harness.

## Package responsibilities

### resona-asr-core
- `protocol.py` — `Transcriber` Protocol + `TranscriptionResult` TypedDict; optional `StreamingTranscriber`/`StreamSession` protocols (`stream_session()` → `feed()`/`flush()` returning `StreamUpdate`) for engines that decode incrementally
- `registry.py` — entry-point discovery, singleton, device detection (`_detect_device`), and environment-aware default engine selection (`recommended_engine`, `installed_engines`, `_is_apple_silicon`). Calls `configure_model_cache()` before loading any engine
- `model_cache.py` — shared HuggingFace model cache. `configure_model_cache()` honors `RESONA_MODELS_DIR` / Voicebox's `VOICEBOX_MODELS_DIR` (→ `HF_HUB_CACHE`); default is the HF hub cache (`~/.cache/huggingface/hub`), so STT engines, local TTS engines, and Voicebox **share weights** on macOS. Also `model_cache_dir()`, `is_model_cached(repo_id)`
- `audio.py` — `load_audio()`, `SAMPLE_RATE`
- `live_transcriber.py` — VAD-based live transcription engine (numpy only). Delegates to a native `StreamSession` when the engine implements `StreamingTranscriber`, otherwise uses the windowed local-agreement fallback

### resona-engine-server
- `app.py` — FastAPI app: `/health`, `POST /transcribe`, `WS /ws/transcribe`, `WS /ws/live`
  - `/health` returns `{status: "ok", engine: str, models: [str]}`
- `auth.py` — optional `RESONA_ENGINE_KEY` auth
- `ws_transcribe.py`, `ws_live.py` — WebSocket endpoint handlers
- `run.py` — uvicorn entry point

### resona-engine-faster-whisper
- `transcriber.py` — `FastWhisperTranscriber`: CTranslate2 engine (default, recommended). CPU-only on Apple Silicon (CTranslate2 has no Metal backend); an `mps` device request is mapped to `cpu`.
- Configured via `DEFAULT_FASTWHISPER_MODEL` env var
- Throughput knobs (env): `FASTWHISPER_BATCHED` (default true — uses `BatchedInferencePipeline`), `FASTWHISPER_BATCH_SIZE` (8), `FASTWHISPER_BEAM_SIZE` (5; set 1 for greedy), `FASTWHISPER_CPU_THREADS` (0=auto; set to perf-core count), `FASTWHISPER_COMPUTE_TYPE`. `word_timestamps` falls back to the sequential (non-batched) path.
- VAD-drop safety net: the batched pipeline's Silero VAD can silently discard large spans of speech on audio it misjudges. After a batched run, if the returned segments cover less than `FASTWHISPER_MIN_COVERAGE` (default 0.5) of the audio — checked only for clips ≥ `FASTWHISPER_MIN_COVERAGE_AUDIO_S` seconds (default 20) — the engine automatically re-runs on the sequential (non-VAD) path, which is slower but does not drop speech. Set `FASTWHISPER_MIN_COVERAGE=0` to disable.

### resona-engine-whisper
- `transcriber.py` — `WhisperTranscriber`: original OpenAI Whisper (PyTorch)
- Configured via `DEFAULT_WHISPER_MODEL` env var

### resona-engine-voxtral
- `transcriber.py` — `VoxtralTranscriber`: HuggingFace Transformers pipeline (supports Voxtral, Whisper, etc.)
- Configured via `DEFAULT_VOXTRAL_MODEL` env var (default: `openai/whisper-large-v3`)

### resona-engine-mlx-whisper (Apple Silicon, GPU)
- `transcriber.py` — `MLXWhisperTranscriber`: Whisper on the Apple GPU via the MLX framework. Lazy-imports `mlx_whisper`. Maps short model names (`large-v3`) to `mlx-community/*` repos.
- Configured via `DEFAULT_MLX_WHISPER_MODEL` (default `mlx-community/whisper-large-v3-mlx`). macOS arm64 only (dependency gated by platform marker).

### resona-engine-whispercpp (Metal)
- `transcriber.py` — `WhisperCppTranscriber`: whisper.cpp via `pywhispercpp` (Metal on Mac, Accelerate elsewhere). Lazy-imports the model. Segment timestamps are 10ms units → seconds.
- Configured via `DEFAULT_WHISPERCPP_MODEL` (default `large-v3`), `WHISPERCPP_N_THREADS` (0=auto).

### resona-engine-lightning-mlx (Apple Silicon, GPU, batched)
- `transcriber.py` — `LightningMLXTranscriber`: batched MLX inference via `lightning-whisper-mlx`. Transcribes from a path, so the waveform is written to a temp WAV; does **not** support `initial_prompt` (ignored with a warning).
- Configured via `DEFAULT_LIGHTNING_MLX_MODEL` (default `large-v3`), `LIGHTNING_MLX_BATCH_SIZE` (12), `LIGHTNING_MLX_QUANT` (`none`/`4bit`/`8bit`). macOS arm64 only.
- Note: `lightning-whisper-mlx` pins `tiktoken==0.3.3`; the workspace root relaxes this via `[tool.uv] override-dependencies = ["tiktoken>=0.7"]` to co-resolve with litellm.

### resona-engine-parakeet
- `transcriber.py` — `ParakeetTranscriber`: NVIDIA Parakeet / FastConformer via NeMo (`ASRModel.from_pretrained`). Default `nvidia/parakeet-tdt-0.6b-v3` (multilingual, DE+EN). `nemo_toolkit[asr]` dep gated to `sys_platform == 'linux'` (Linux/CUDA-first; CPU works). NeMo imported lazily so the entry point registers on macOS but selecting it errors there. `transcribe()` accepts `language`/`vad_*`/`initial_prompt` for protocol compatibility and ignores them (NeMo auto-detects language); returns `segments=[]`, so the live fallback uses the text-split path.
- Native cache-aware streaming is **opt-in** via `RESONA_PARAKEET_STREAMING` (default off) and only activates when the loaded model is cache-aware-capable; `stream_session` is bound only then, so otherwise the engine stays a plain `Transcriber` and `resona live` uses the windowed local-agreement fallback. `ParakeetStreamSession` drives NeMo `conformer_stream_step` over a `CacheAwareStreamingAudioBuffer`; experimental, needs GPU validation. `_text_delta`/`_hypothesis_text` are pure helpers.
- Configured via `DEFAULT_PARAKEET_MODEL` env var (default: `nvidia/parakeet-tdt-0.6b-v3`)

### resona-cloud-stt
- `types.py` — `TranscriptionResult` TypedDict: `{text, language, segments}`
- `errors.py` — `CloudSTTError` (base), `MissingAPIKeyError` (env var not set), `ProviderHTTPError` (non-2xx response)
- `registry.py` — `PROVIDERS` (set), `PROVIDER_ENV_KEYS` (name → env var), `DEFAULT_MODELS` (name → model), `get_provider(name)` (returns provider module)
- `streaming.py` — live (WebSocket) STT. `StreamTranscript{text, is_final}`, `STREAMING_PROVIDERS = {deepgram, elevenlabs}`, `supports_streaming(name)`, and `async open_stream(provider, *, api_key, model, language, sample_rate, interim_results, options)` → a provider session with `await send_audio(pcm)`, `await finish()`, `await close()`, and `async for t in session` yielding `StreamTranscript`. OpenAI has no realtime path wired
- `providers/deepgram.py` — batch POSTs raw audio to Deepgram `/v1/listen`; plus `open_stream` → `_DeepgramStream` over `wss://api.deepgram.com/v1/listen` (binary PCM up, `Results` down, `CloseStream` to finish). Default model `nova-3`; key env var `DEEPGRAM_API_KEY`
- `providers/elevenlabs.py` — batch POSTs audio to ElevenLabs Speech-to-Text; plus `open_stream` → `_ElevenLabsStream` over `wss://api.elevenlabs.io/v1/speech-to-text/realtime` (`input_audio_chunk` base64 frames, `commit_strategy=vad`, `partial_transcript`/`committed_transcript` down). Default model `scribe_v1`; key env var `ELEVENLABS_API_KEY`
- `providers/openai.py` — POSTs audio to OpenAI Whisper API; default model `whisper-1`; key env var `OPENAI_API_KEY`. Batch only (no streaming)

### resona-cloud-tts
- `types.py` — `SpeechResult` TypedDict: `{audio: bytes, content_type: str}`
- `errors.py` — `CloudTTSError` (base), `MissingAPIKeyError(env_var)` (env var not set), `ProviderHTTPError(provider, status_code, body)` (non-2xx response)
- `registry.py` — `PROVIDERS` (set), `PROVIDER_ENV_KEYS` (name → env var), `DEFAULT_MODELS` (name → model), `DEFAULT_VOICES` (name → voice), `CONTENT_TYPES` (name → MIME type), `get_provider(name)` (returns provider module)
- `providers/openai.py` — `synthesize(text, *, api_key, model, voice, response_format, options)` → POSTs to `https://api.openai.com/v1/audio/speech`; Bearer auth; default model `tts-1`; key env var `OPENAI_API_KEY`
- `providers/elevenlabs.py` — POSTs to `https://api.elevenlabs.io/v1/text-to-speech/{voice_id}`; `xi-api-key` header; key env var `ELEVENLABS_API_KEY`
- `providers/deepgram.py` — POSTs to `https://api.deepgram.com/v1/speak`; Token auth; voice overrides model; key env var `DEEPGRAM_API_KEY`

### resona-tts-local
- Local/offline TTS engines, ported from [Voicebox](https://github.com/jamiepine/voicebox)'s backends into Resona's plugin style. In-process (no engine-server). Counterpart to `resona-cloud-tts`.
- `types.py` — `SpeechResult` TypedDict `{audio: bytes, content_type: str, sample_rate: int}` (shape-compatible with cloud-tts + `sample_rate`)
- `protocol.py` — `LocalTTSEngine` Protocol: `synthesize(text, *, language, voice, ref_audio, ref_text, instruct, speed, seed) -> SpeechResult` (+ optional `synthesize_array() -> (np.ndarray, sr)`)
- `registry.py` — `ENGINES`, `ENGINE_INFO` (languages/cloning/presets/instruct per engine), `installed_engines()`, `recommended_engine()` (→ `kokoro`), `get_engine(name)` (memoized, lazily instantiated singleton), `reset()`. Plain dict + importlib (like cloud-tts), not entry points
- `audio.py` — `to_wav_bytes`, `to_numpy`, `wav_result` (engines produce float32 + sr, encoded to WAV/PCM_16)
- `engines/` — one module per engine; each lazy-imports its native lib (clear `EngineUnavailableError` with install command if absent) and calls `configure_model_cache()` in `__init__`:
  - `kokoro.py` (`KokoroEngine`) — Kokoro-82M via `kokoro` (KModel/KPipeline), preset voices, 8 langs, cross-platform. Lockable extra: `resona-tts-local[kokoro]`
  - `chatterbox.py` / `chatterbox_turbo.py` — `chatterbox-tts` (ML: 23 langs + zero-shot cloning; Turbo: EN + paralinguistic tags). CPU-forced on mac
  - `qwen.py` (`QwenTTSEngine`) — Qwen3-TTS Base, zero-shot cloning + `instruct`; MLX (`mlx-audio`) on Apple Silicon, `qwen-tts` (PyTorch) elsewhere. Size via `DEFAULT_QWEN_TTS_MODEL` (`1.7b`/`0.6b`)
  - `qwen_custom_voice.py` — Qwen CustomVoice, 9 preset speakers + `instruct`, `qwen-tts` (PyTorch) all platforms
- **Dependency note** (the native libs declare conservative pins, not real ABI limits — verified June 2026 against torch 2.12 / transformers 5.8 / numpy 2.2). Install via the `just` recipes: `just tts-kokoro` / `just tts-qwen` / `just tts-chatterbox`.
  - **Lockable extras**: `[kokoro]` (pure) and `[qwen]` = `mlx-audio` on Apple Silicon (MLX-native, co-resolves cleanly). `pip install 'resona-tts-local[kokoro,qwen]'` (or `just tts-kokoro` / `just tts-qwen`)
  - **chatterbox / turbo**: `chatterbox-tts` pins `numpy<2.0`/`torch==2.6.0`/exact-`transformers` so it can't co-lock, but **imports & runs on the modern stack** when installed `--no-deps` + its pure deps (`librosa s3tokenizer diffusers resemble-perth conformer omegaconf pykakasi pyloudnorm spacy-pkuseg einops`). Same trick as Voicebox's `--no-deps`. Use `just tts-chatterbox`.
  - **qwen-tts (PyTorch)**: currently **breaks on `transformers>=5.8`** (`check_model_inputs` signature) and pins `transformers==4.57.3` — not reliable on Resona's stack; prefer the `[qwen]` mlx-audio path on mac, or track upstream `QwenLM/Qwen3-TTS`.
  - Engine code always ships; a missing lib raises `EngineUnavailableError` with the install command.

### resona-postprocess
- `replacements.py` — `apply_replacements(text, list[dict])` — regex-based, case-insensitive
- `llm.py` — `llm_transform(text, prompt, ...)` and `llm_extract(text, prompt, ...)` via litellm; `llm_postprocess` is a deprecated alias for `llm_transform`. `_resolve_api_base(model, api_base)` resolves the endpoint (explicit > `RESONA_LLM_API_BASE` > localhost default by model prefix), so local LLMs work offline: set `RESONA_LLM_MODEL=ollama/llama3` / `lm_studio/<m>` (→ `:1234/v1`) / `mlx/<m>` (→ `:10240/v1`)
- `pipeline.py` — `PostprocessPipeline`: composable step chain; `PostprocessResult{text, data}` carries both formatted text and structured extraction data; `build_pipeline(profile)` constructs a runnable pipeline from a `Profile`
- `profile.py` — `Profile` dataclass (name, description, initial_prompt, steps); `resolve_profile(ref, profiles_dir)` accepts a name, inline JSON string, file path, or `Profile`/dict; `list_profiles(dir)`, `bundled_default()`
- `profiles/default.json` — bundled default profile with German dictation replacements (Komma, Punkt, Absatz, medical headings, name corrections)
- `default_replacements.json` — bundled replacement rules used by the default profile

### resona-api
- `app.py` — FastAPI lifespan: creates DB, starts `TranscribeTask`, instantiates `EngineClient`; runs `migration.py` on startup to export any legacy DB tables to `default.json`
- `endpoints.py` — REST routes: jobs
- `profiles_routes.py` — CRUD routes for profile files: `GET /profiles/`, `GET /profiles/{name}`, `PUT /profiles/{name}`, `DELETE /profiles/{name}`
- `audio_routes.py` — OpenAI-compatible audio routes: `GET /v1/engines`, `POST /v1/audio/transcriptions`, `POST /v1/audio/speech`; accepts optional `profile` field. `/v1/audio/speech` dispatches to **local** TTS engines (`resona-tts-local`: kokoro/chatterbox/qwen…) or **cloud** providers — pick via `engine`; `voice`/`language` forwarded
- `streaming_routes.py` — Deepgram-compatible live streaming: `WS /v1/listen`. Auth via `Authorization: Token <RESONA_API_KEY>` (also Bearer/X-API-Key/`?token=`). Query params `model`/`language`/`encoding`(linear16)/`sample_rate`/`interim_results` + Resona `engine`/`profile`. Resolves the engine, then dispatches: a **local** engine-server → `_run_local_bridge`/`_bridge` (proxy to its `/ws/live`); a **cloud** provider with a realtime API (Deepgram, ElevenLabs) → `_run_cloud_bridge`/`_bridge_cloud` (opens `resona_cloud_stt.streaming.open_stream`, bridges binary PCM ↔ provider session). Both emit Deepgram `Results`/`Metadata`. Cloud providers without streaming (OpenAI) are rejected. `_CLOUD_FINAL_DRAIN_SECONDS` bounds the post-stop drain for providers that don't close on commit (ElevenLabs)
- `engine_registry.py` — multi-backend catalogue: probes local engine `/health` endpoints, lists local TTS engines (`_local_tts_engines`, `kind="local-tts"`, private, available when `resona-tts-local` is installed), checks cloud provider API keys, `EngineInfo` dataclass, `resolve(name)`, `run_stt(engine, audio, ...)`, `run_tts(engine, text, ...)` (dispatches `local-tts` → `resona_tts_local.get_engine().synthesize`, else cloud provider), `cloud_api_key(provider)` (public key lookup for the streaming bridge), error hierarchy
- `tasks_transcribe.py` — background thread: dequeues PENDING jobs, resolves the job's profile via `resolve_profile`, calls engine with `profile.initial_prompt_string()`, **applies profile pipeline** after engine returns
- `engine_client.py` — `EngineClient.transcribe()`: POSTs to engine (no replacements sent)
- `migration.py` — on startup, exports legacy `Replacement`/`InitialPrompt` DB rows to a `default.json` profile file in `RESONA_PROFILES_DIR`; idempotent
- `db/models.py` — `Job` SQLModel table; `Job` has `engine`, `profile`, `profile_config`, `structured` fields
- `db/engine.py` — SQLite engine + `create_db_and_tables()`
- `db/utils.py` — `register_job()`
- `formatting.py` — writes markdown output files
- `paths.py` — `DATA_PATH`, `FILE_PATH`, `DB_PATH`, `PROFILES_PATH` resolved from env
- `auth.py` — optional `RESONA_API_KEY` auth

### resona-client
- `client.py` — `ResonaClient`: all resona-api HTTP operations. Reads `RESONA_API_URL` / `RESONA_API_KEY`. Includes profile CRUD methods (`list_profiles`, `get_profile`, `push_profile`, `pull_profile`, `delete_profile`) and a `profile` argument on `submit_job` and `create_transcription`.
- `config.py` — `EngineConfig`: `~/.resona/config.json`, auto-start (SSH tunnel, docker compose), `default_engine`, `default_profile`, `default_private`; `EngineEntry`: per-entry `type` (`resona-api` or `cloud`), `provider`, `model`, `options`, `private`; `resolve_engine(private_only=False)` — walks priority-ordered entries, optionally skipping non-private ones

### resona-cli (lives in `apps/resona-cli/`)
- `main.py` — typer app root, `resona` command
- `watch.py` — `watch` subcommand: polls directory, calls `client.submit_job()`; accepts `--profile`
- `transcribe.py` — `transcribe` subcommand: accepts files, glob patterns, or directories; `--engine NAME` unified selector (built-in local engine, config.json server entry, or cloud entry); `--private`/`--no-private` to require a private engine; `--profile NAME` or inline JSON profile; submits to resona-api, calls cloud provider, or falls back to a local engine
- `engine.py` — `Engine` Protocol + `RemoteEngine` (HTTP) + `InProcessEngine` (direct asr-core call) + `CloudEngine` (wraps an `EngineEntry` of type `cloud`; calls `resona_cloud_stt` provider directly); used by transcribe.
- `local_engine.py` — `LocalEngine`: subprocess-based fallback for transcribe when InProcessEngine extras aren't installed.
- `engines.py` — `resona engines` CRUD subcommands; `engines add --type cloud --provider <name>` registers cloud entries
- `profiles.py` — `resona profiles` subcommand: `list`, `show`, `push`, `pull`, `delete` for server-side profile management
- `micrec.py` — `RecordingSession` + `MicRecApp` Textual TUI base; `rec` subcommand
- `live_ui.py` — `WSLiveApp`: live transcription TUI. `live` subcommand accepts `--language/-l`, `--engine/-e` (in-process engine: flag → `RESONA_ENGINE` → `platform_preferred_engine()`; validated against installed engines, exported as `RESONA_ENGINE` so the `LiveTranscriber` singleton loads it), and `--remote/-r URL` (stream to a remote server). With `--remote` **and** `--engine`, the TUI hits a resona-api `/v1/listen` gateway and `--engine` picks the backend (`deepgram`/`elevenlabs`/local engine name) → cloud streaming from the TUI; with `--remote` alone it hits an engine-server `/ws/live`. `WSLiveApp(remote=, remote_engine=)` swaps in `GatewayLiveTranscriber` or `RemoteLiveTranscriber`; the worker/feed loops are unchanged because all backends share the same pull surface
- `remote_live.py` — remote `live` backends over a `websockets` sync connection (sender + receiver threads), all exposing the `LiveTranscriber` pull surface (`add_audio`/`has_enough_audio`/`process_sync`/`flush_sync`/`get_full_transcript`/`_audio_event_sync`). `_BaseRemoteLive` holds the shared threading; subclasses define URL, finish frame, and message classifier: `RemoteLiveTranscriber` → engine-server `/ws/live` (`partial`/`final` JSON, `stop` to finish), `GatewayLiveTranscriber` → resona-api `/v1/listen` (Deepgram `Results`/`Metadata`, `CloseStream` to finish, `?engine=` selects backend, `RESONA_API_KEY` → `Authorization: Token`). Audio is the shared `{type:audio, data:base64 int16}` frame (accepted by both upstreams); `_pcm_b64` encodes 16 kHz float32 → base64 int16; `_build_ws_url` normalizes URLs + merges query params
- `ui.py` — `WSUIApp`: record-and-transcribe TUI

## Import conventions

Within a package, use relative imports:
```python
from .db.models import Job
from .engine_client import EngineClient
```

Cross-package imports: resona-cli imports `resona_asr_core.live_transcriber` (for the `live` command) and `resona_asr_core.registry` (for `InProcessEngine`). Both `resona-asr-core` and `resona-engine-faster-whisper` are base dependencies of resona-cli, so these imports always resolve. All other cross-package communication is over HTTP.

## How to add a new transcription engine

1. Create `packages/engine-<name>/` with src-layout
2. Implement a class with `transcribe(audio: np.ndarray, **kwargs) -> TranscriptionResult`
3. Constructor: `__init__(self, device: str, modelname: str | None = None)`
4. Register in pyproject.toml: `[project.entry-points."resona.engines"]`
5. Add `[tool.uv.sources]` with `resona-asr-core = { workspace = true }` and `resona-engine-server = { workspace = true }`
6. Set `[project.scripts]` to `resona_engine_server.run:main`
7. The engine must not touch the database

## How to add a new endpoint to resona-api

1. Add the route to `packages/api/src/resona_api/endpoints.py`
2. Add any new DB models to `db/models.py`
3. Add a corresponding method to `ResonaClient` in `packages/client/src/resona_client/client.py`
4. Add a CLI subcommand if appropriate

## Job flow

### Server path

```
Client → POST /jobs (profile=<name|inline JSON>) → resona-api saves file, registers PENDING job
resona-api TranscribeTask polls PENDING jobs →
  resolves profile via resolve_profile(job.profile or "default", PROFILES_PATH) →
  calls EngineClient.transcribe(filepath, language, profile.initial_prompt_string()) →
    POSTs multipart to engine POST /transcribe (no replacements) →
  engine returns {text, language, segments} →
  result = build_pipeline(profile).run(text) →
  stores result.text as job.md, result.data as job.structured →
  writes transcript + md to Job row, sets status COMPLETED
Client → GET /job/{id} → sees COMPLETED job with transcript + md
```

Profile may be: a profile name (resolved from RESONA_PROFILES_DIR), an inline JSON string, or omitted (uses the bundled `default` profile).

### Local fallback path

```
resona transcribe ./audio/ --engine voxtral --profile my-profile
  no server reachable →
  resolves engine: --engine flag → config.json default_engine → "faster-whisper"
  resolves profile: --profile flag → config.json default_profile → bundled "default"
  spawns: uv run resona-engine-voxtral on a free port
  waits for /health →
  POSTs each audio file to local engine →
  engine returns {text, language, segments} →
  result = build_pipeline(profile).run(text) →
  writes transcript to output file
```

## Postprocessing

Postprocessing is driven by **profiles**. A profile is a JSON file (or inline JSON string) that bundles an `initial_prompt` list and an ordered list of pipeline steps. Profiles replace the old DB-backed `Replacement`/`InitialPrompt` tables.

### Profile file format

```json
{
  "name": "my-profile",
  "description": "German medical dictation with LLM formatting",
  "initial_prompt": ["Befund", "Diagnose", "Medikation"],
  "steps": [
    {
      "type": "replacements",
      "rules": [
        {"pattern": "\\bKomma\\b", "replacement": ","},
        {"pattern": "\\bPunkt\\b",  "replacement": "."}
      ]
    },
    {
      "type": "llm",
      "name": "format",
      "prompt": "Format this medical dictation as a structured clinical note.",
      "model": "ollama/llama3"
    }
  ]
}
```

Step types:
- `replacements` — regex rules applied case-insensitively. Supply `rules` (inline array) or `source` (path to a JSON rules file).
- `llm` — sends text to a language model via litellm; returns transformed text.
- `extract` — sends text to a language model and returns structured JSON data stored in `job.structured`.

### Bundled default profile

`profiles/default.json` is bundled with `resona-postprocess`. It includes German dictation commands:

| Spoken | Written |
|--------|---------|
| Komma | , |
| Punkt | . |
| Absatz | (newline) |
| Kapitel | # (heading) |
| Klammer auf/zu | ( ) |

Plus medical section headings (Verlauf, Medikation, Psychopathologischer Befund, Procedere) and name corrections.

### Using profiles

Server-side: pass `profile=<name>` or `profile=<inline JSON>` when submitting a job via `POST /jobs` or `POST /v1/audio/transcriptions`. Named profiles are loaded from `RESONA_PROFILES_DIR` (default `<DATA_PATH>/profiles/`); the bundled `default` profile is used when none is specified.

CLI: `resona transcribe ./audio/ --profile my-profile` or `resona transcribe ./audio/ --profile '{"name":"x","steps":[...]}'`

Manage server-side profiles with `resona profiles list|show|push|pull|delete`.

### Env vars for LLM steps

| Variable | Default | Description |
|----------|---------|-------------|
| `RESONA_LLM_MODEL` | `gpt-4o-mini` | Default LLM model for `llm`/`extract` steps that do not specify `model`. Use `ollama/<m>`, `lm_studio/<m>`, or `mlx/<m>` for local LLMs |
| `RESONA_LLM_API_BASE` | (unset) | Custom API base URL. Auto-filled for `lm_studio/`→`:1234/v1` and `mlx/`→`:10240/v1` when unset |
| `RESONA_MODELS_DIR` / `VOICEBOX_MODELS_DIR` | HF default (`~/.cache/huggingface/hub`) | Shared HuggingFace model cache (→ `HF_HUB_CACHE`). Set to share downloaded weights with Voicebox; honored by all STT + local TTS engines |
| `DEFAULT_QWEN_TTS_MODEL` | `1.7b` | Qwen3-TTS / CustomVoice size (`1.7b` or `0.6b`) for `resona-tts-local` |
| `RESONA_PROFILES_DIR` | `<DATA_PATH>/profiles` (server) / `~/.resona/profiles/` (CLI) | Directory where named profile files are stored |

## Running in development

```bash
# Install all packages
uv sync --all-packages

# Run individual services
uv run resona-engine-faster-whisper   # :7001, needs GPU
uv run resona-api                      # :7000, needs engine running

# CLI tools
uv run resona rec                      # recorder TUI
uv run resona live                     # live transcription TUI
uv run resona live --engine mlx-whisper --language en  # pick engine + language
uv run resona live --remote ws://gpu-box:7001 --language de  # stream to a remote engine /ws/live
uv run resona live --remote http://api-host:7000 --engine deepgram    # cloud streaming via resona-api /v1/listen
uv run resona live --remote http://api-host:7000 --engine elevenlabs --language de
uv run resona ui                       # record + transcribe
uv run resona transcribe ./audio/      # transcribe a directory
uv run resona transcribe one.mp3       # transcribe a single file
uv run resona transcribe "audio/*.mp3" # transcribe a quoted glob
uv run resona watch ./inbox/           # watch directory

# Local-only (no server needed — spawns engine automatically)
uv run resona transcribe ./audio/ --output-dir ./out/
uv run resona transcribe ./audio/ --engine whisper --language en
```

### Editable vs. copied installs

`uv sync --all-packages` installs every workspace package into the workspace
`.venv` editable — `uv run resona <cmd>` from the repo root picks up source
edits to any package immediately. **This is the dev loop.**

`uv tool install` (Install personas below) **copies** the packages into an
isolated tool env; that copy is not editable. After editing code, an installed
tool must be refreshed with
`uv tool install --reinstall --from ./apps/resona-cli resona-cli`. Note that
`--editable` would only make `resona-cli` itself editable, not its workspace
dependencies (`resona-postprocess`, `resona-asr-core`, …) — so `uv run` from the
workspace is the only fully-editable path. Reserve `uv tool install` for testing
the end-user personas.

### Install personas

| Persona | Command |
|---------|---------|
| Default (record, live, local faster-whisper) | `uv tool install --from ./apps/resona-cli resona-cli` |
| Default + Whisper (PyTorch) engine | `uv tool install --from ./apps/resona-cli 'resona-cli[whisper]'` |
| Default + Voxtral (PyTorch) engine | `uv tool install --from ./apps/resona-cli 'resona-cli[voxtral]'` |
| Default + Apple GPU engines (mac) | `uv tool install --from ./apps/resona-cli 'resona-cli[apple]'` |
| Default + Parakeet (NeMo) engine (Linux CUDA/CPU) | `uv tool install --from ./apps/resona-cli 'resona-cli[parakeet]'` |

Apple-Silicon extras: `[mlx]` (mlx-whisper), `[whisper-cpp]` (whisper.cpp), `[lightning-mlx]` (batched MLX), or `[apple]` for all three.

See [docs/getting-started/installation.md](docs/getting-started/installation.md) for details including PyTorch extras.

```bash
# Documentation
uv run mkdocs serve                    # dev server at :8000
uv run mkdocs build                    # static docs to site/
```

## Testing

Tests live in `<pkg>/tests/`. Run with:

```bash
uv run pytest                                      # all tests
uv run pytest packages/engine-server/tests/        # engine server
uv run pytest packages/asr-core/tests/             # asr core
uv run pytest packages/api/tests/                  # api
uv run pytest packages/client/tests/               # client
uv run pytest apps/resona-cli/tests/               # cli
uv run pytest packages/postprocess/tests/          # postprocess
uv run pytest packages/tts-local/tests/            # local TTS engines
uv run pytest -k test_transcribe                   # one test
```

Mocking strategy:
- resona-engine-server: mock the transcriber at `resona_engine_server.app.get_transcriber`
- resona-api: mock `EngineClient.transcribe` with `respx` (httpx mock)
- resona-client: use `respx.mock` to intercept httpx calls
- resona-cli: use typer's `CliRunner` for command tests

Audio fixtures: keep small WAV files (1-2 seconds, 16kHz mono) in `<pkg>/tests/fixtures/`.

### Benchmarking backends

`benchmarks/transcription_benchmark.py` compares every installed engine over the
same ~10-min German + English audio (assembled from Google FLEURS, cached), and
writes `benchmarks/results/benchmark_<ts>.{md,json}` with hardware, model, speed
(RTF / × realtime) and accuracy (WER / CER). Engine unit tests mock the native
libs; the benchmark runs real inference and is not part of `pytest`.

```bash
uv sync --all-packages
uv run --with jiwer --with datasets --with soundfile \
    python benchmarks/transcription_benchmark.py
```

## Docker

Each engine builds from the workspace root as context:

```dockerfile
COPY pyproject.toml uv.lock* ./
COPY packages/engine-server/ ./packages/engine-server/
COPY packages/asr-core/ ./packages/asr-core/
COPY packages/engine-faster-whisper/ ./packages/engine-faster-whisper/
RUN uv sync --package resona-engine-faster-whisper --frozen --no-dev
```

GPU engine packages use a `nvidia/cuda:*-runtime-ubuntu24.04` base (faster-whisper on 12.8.0; whisper/voxtral on 13.0.1; parakeet on 12.8.0 to match PyPI torch's bundled CUDA 12.x, with `libsndfile1` added for NeMo/soundfile). The API uses `python:3.12-slim`. Do not add GPU deps to the API Dockerfile. `engine-mlx-whisper` has **no** Dockerfile — Metal cannot run in a Linux container; run it natively on macOS.

Each engine is a compose service behind a `profiles: [<name>]` flag and is reachable on its own host port: faster-whisper `:7001`, whisper `:7002`, voxtral `:7003`, parakeet `:7004` (all map to container `:7001`); the API is always-on at `:7000`. `docker-compose.cpu.yml` is a CPU override for `engine-faster-whisper` only (the PyTorch/NeMo engines are impractical on CPU).

```bash
# default profile (api only — bring an engine up explicitly):
docker compose -f docker-compose.resona.yml --profile faster-whisper up
docker compose -f docker-compose.resona.yml --profile parakeet up
# CPU-only faster-whisper:
docker compose -f docker-compose.resona.yml -f docker-compose.cpu.yml --profile faster-whisper up
```

## Environment and configuration

All config is read with `python-decouple`'s `config()`. This reads from env vars first, then `.env` file. Never use `os.environ[]` directly — use `config("VAR_NAME", default=...)`.

Exception: `resona-client` uses `os.getenv()` for `RESONA_API_URL` / `RESONA_API_KEY` (it has no decouple dependency).

### Key environment variables

See [docs/configuration/environment.md](docs/configuration/environment.md) for the full env var reference.

### Engine resolution order (`resona transcribe`)

1. `--engine NAME` CLI flag: resolves a built-in local engine name (`faster-whisper`, `whisper`, `voxtral`), a `config.json` server entry, or a `config.json` cloud entry (highest priority)
2. `--private` / `--no-private`: when private is required (via flag or `default_private`), non-private engines are skipped or refused; cloud engines are never private
3. `default_engine` in `~/.resona/config.json` (a concrete name pins it)
4. `default_engine` is `"auto"` (the default): the CLI calls `recommended_engine()` — `mlx-whisper` on Apple Silicon when installed, else `faster-whisper`. Resolved by `_resolve_local_engine_name()` in `transcribe.py` (shared by `watch`).

## What NOT to do

- Do not add database access to engine-server or any engine package
- Do not add postprocessing (replacements, LLM) to the engine — it belongs in resona-api or resona-postprocess
- Do not delete audio files after transcription
- Do not add `ScanInboxTask` back — inbox scanning is done by `resona watch`
- Do not add a synchronous `/asr` endpoint to resona-api — the engine owns direct transcription
- Do not use `os.environ[]` — use `config()` from python-decouple
