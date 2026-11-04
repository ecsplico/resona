# CLAUDE.md — Resona development guide

## Project overview

Resona is a modular transcription platform with pluggable ASR engines and a composable postprocessing pipeline. Designed for German medical dictation but usable for any language.

## Project structure

```
resona/
├── pyproject.toml          ← workspace root
├── uv.lock
├── docker-compose.resona.yml
├── docker-compose.coolify.yml ← public hub on Coolify (hub + hosted MCP, Caddy labels, /data volume); docs/server/coolify.md
├── benchmarks/             ← standalone backend speed+accuracy benchmark (not a workspace member; scores via resona-eval)
├── eval/                   ← evaluation corpus (eval/corpus, synthetic, CC0) + promoted baselines (eval/baselines.json)
├── apps/
│   ├── resona-cli/         ← resona: typer CLI (watch, transcribe, profiles, rec/live/ui TUIs)
│   ├── resona-worker/      ← resona-worker: outbound-only pull worker (claims hub jobs under leases, runs one execution subprocess per job)
│   └── resona-mcp/         ← resona-mcp: MCP server (stdio + hosted Streamable HTTP /mcp) over the hub job API
│                             (no bundled web UI; browser use goes through the Resona PWA standalone mode)
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
    ├── job-core/           ← resona-job-core: shared job/result contracts, execution policy, PolicyDenied. No ML deps.
    ├── engine-docling/     ← resona-engine-docling: in-process Docling PDF/image → Markdown (OCR: ocrmac/rapidocr/tesseract). Not an audio engine; not in `resona.engines`.
    ├── cloud-stt/          ← resona-cloud-stt: cloud STT providers (Deepgram, ElevenLabs, OpenAI)
    ├── cloud-tts/          ← resona-cloud-tts: cloud TTS providers (OpenAI, ElevenLabs, Deepgram)
    ├── tts-local/          ← resona-tts-local: local/offline TTS engines (Kokoro, Chatterbox, Qwen3-TTS) — ported from Voicebox
    ├── postprocess/        ← resona-postprocess: profile-based postprocessing pipeline (replacements + LLM + extract)
    ├── api/                ← resona-api: job queue + DB + postprocessing + unified STT/TTS API, :7000
    ├── eval/               ← resona-eval: corpus loader, WER/CER + document scoring, quality gate, baselines. No ML deps.
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
- `vad.py` — `decode_speech(audio, decode, *, language, vad_filter, vad_parameters)` for Whisper engines without their own VAD (mlx-whisper, openai-whisper): with `vad_filter` runs faster-whisper's Silero VAD (ONNX weights bundled in the `faster-whisper` wheel, no download; lazy import, so engines using it declare `faster-whisper`), decodes only the concatenated speech spans and maps segment/word times back; no speech → empty transcript without decoding. `vad_filter=False` decodes the full waveform.
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
- `transcriber.py` — `WhisperTranscriber`: original OpenAI Whisper (PyTorch); the `linux-cuda` audio candidate (`large-v3` on CUDA). `vad_filter` is handled by `resona_asr_core.vad.decode_speech` (openai-whisper has no VAD and emits "Vielen Dank." on noise); depends on `faster-whisper` only for the Silero model.
- Configured via `DEFAULT_WHISPER_MODEL` env var

### resona-engine-voxtral
- `transcriber.py` — `VoxtralTranscriber`: HuggingFace Transformers pipeline (supports Voxtral, Whisper, etc.)
- Configured via `DEFAULT_VOXTRAL_MODEL` env var (default: `openai/whisper-large-v3`)

### resona-engine-mlx-whisper (Apple Silicon, GPU)
- `transcriber.py` — `MLXWhisperTranscriber`: Whisper on the Apple GPU via the MLX framework. Lazy-imports `mlx_whisper`. Maps short model names (`large-v3`) to `mlx-community/*` repos. `vad_filter` (the hub job default) is handled by `resona_asr_core.vad.decode_speech` (see asr-core).
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

### resona-engine-docling
- `convert.py` — `convert_document(path, *, options: DocumentOptions, accelerator) -> JobResult`: runs Docling `DocumentConverter` in-process (not the CLI, not docling-serve). Translates `DocumentOptions` (languages de/en/fr/it/es/ko, `ocr=auto|force|off`, `ocr_engine=ocrmac|rapidocr|tesseract`, inclusive page range, `image_scale` 1.0–4.0 OCR raster multiplier, `ocr_min_confidence` rapidocr-only) into the pinned Docling 2.129 option classes. Option/engine conflicts come from job-core's shared `document_options_conflict(options, ocr_engine)` (also applied by the hub at admission, so they are 422 before any worker claims) and raise `ConflictingDocumentOptions`; host-specific gaps (ocrmac off macOS, missing tesseract pack) raise `UnsupportedDocumentOptions`; non-PDF/image input raises `UnsupportedMediaType`. Converters are cached per option set (page range excluded).
- `options.py` — engine-side `DocumentOptions` (job-core subclass). The per-backend language identifiers live in `convert.py` (`_OCRMAC_LANG` `de-DE`/`en-US`/`fr-FR`/`it-IT`/`es-ES`/`ko-KR`, `_TESSERACT_LANG` `deu`/`eng`/`fra`/`ita`/`spa`/`kor`); rapidocr runs one bundled PP-OCRv6 multilingual model (de/en/fr/it/es, no ko).
- Result contract lives in `resona-job-core` (`resona_job_core.results.JobResult`). Docling is pinned exactly (`docling==2.129.0`); exact pin changes require re-running `packages/engine-docling/tests`.

### resona-worker
- `hub.py` — `HubClient`: worker-side REST client for `/v1/workers/*`, bearer auth, error mapping (`stale_lease`, `policy_denied`, `protocol_incompatible`) and bounded retries. Wire models `HeartbeatReply`, `ClaimedJob`, `JobLease` live here.
- `supervisor.py` — `Supervisor`: at start probes each non-CPU accelerator of the manifest through the executor and withdraws the tuples of unusable ones (a CUDA container without GPU heartbeats with no tuples). Claim → download input (SHA-256 checked; document inputs renamed `input.pdf|.png|.jpg|.tif` by magic bytes) → run execution subprocess → postprocess stages, each gated by a hub policy check → upload result or bounded failure. `LeaseKeeper` renews the lease for the whole job; lease loss or cancellation kills the subprocess group and uploads nothing. Heartbeat reports and claims offer only tested tuples (manifest) ∩ hub-approved tuples (heartbeat reply) minus withdrawn ones. OOM (exit code 5, killed by SIGKILL, or an OOM marker such as `[metal::malloc]`/"out of memory" in the engine's stderr tail) fails the attempt retryably and withdraws that tuple until the worker restarts; the hub only records the withdrawal in the job error and never changes `approved_capabilities`.
- `execute.py` — subprocess entry point (`python -m resona_worker.execute --snapshot --input --out [--metrics]`): ASR via `resona.engines` entry points (per-job process, no registry singleton) or Docling documents. A candidate whose accelerator is unusable (`accelerator_available`: cuda = `torch.cuda.is_available()`) exits `UNSUPPORTED_CAPABILITY` before loading anything; `--probe-accelerator <cpu|cuda|metal>` answers the same check. HF-repo models load only from the local cache snapshot (offline); provenance `model_revision` = snapshot commit hash. CUDA/Metal stages carry `peak_accelerator_bytes`. Exit codes map to failure codes. `subprocess_env(private=True)` is an allowlist (system/locale basics, model-cache paths, engine tuning prefixes; credential-shaped names always dropped) plus forced `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE`/`HF_HUB_DISABLE_TELEMETRY`/`DO_NOT_TRACK`; non-private jobs inherit the env minus `RESONA_API_KEY`.
- `weights.py` — `release_model_pins()`: `model -> revision` from the packaged `resona_worker/models.json` (the same file `release/build_manifest.py` reads; HF repos pinned to 40-hex commits, incl. Docling's `docling-project/docling-layout-heron` and `docling-project/docling-models`). `local_snapshot(repo, revision)`: offline HF cache lookup (cache dir read from `HF_HUB_CACHE` at call time). `ensure_pinned_snapshot(repo, ref, pin, *, offline)`: takes the pinned commit from the cache or downloads it (unless `offline`, then `WeightsError` naming `repo@pin`), then points the branch/tag the engine loads (`refs/main`, `refs/v2.3.0`) at the pin; never accepts another commit. openai-whisper aliases (`large-v3`) are single checkpoints under `XDG_CACHE_HOME/whisper`; their revision is the SHA-256 in openai-whisper's download URL (`whisper_revision`, verified by `sha256_file`). Downloads happen only in `prepare`.
- `prepare.py` — `prepare(settings, profile, *, model_pins=None, offline=False) -> CapabilitiesManifest`: pins come from a release manifest or, when `None`, `release_model_pins()`; an unpinned model is an error. Before any run, `pin_weights` pins every candidate's weights (ASR model + Docling `stage_weights()`), downloading missing pinned revisions unless `offline`. Then it runs the real executor on packaged fixtures (`fixtures/de_short.wav`, `fixtures/scan_de.pdf`) in the private env, checks expected German words, that the primary stage (`asr`, or `layout` for documents) ran on the candidate's accelerator and that every HF model recorded in provenance is the pinned revision; records seconds, peak RSS, peak accelerator bytes (MLX / `torch.cuda.max_memory_allocated`) and per-stage devices (Docling tableformer runs on CPU by design under Metal). Any failure aborts and leaves the previous manifest untouched.
- `config.py` — token file (0600 regular file, no symlinks), hub URL (HTTPS, or loopback HTTP), capabilities manifest (`CapabilitiesManifest`, `schema_version` 1; a bare JSON list is rejected — run `prepare`).
- `launchd.py` — macOS user agent: `install` writes `~/Library/LaunchAgents/<label>.plist` (token *path* only, allowlisted env, umask 077, logs `~/Library/Logs/resona-worker/`) and `launchctl bootstrap gui/<uid>`; `uninstall` boots out and deletes it.
- `cli.py` — `resona-worker prepare --profile <name> --out caps.json [--settings-file --release-manifest --offline]`, `capabilities caps.json` (approve list for `resona-admin worker approve`), `run --hub --token-file --capabilities-file --work-dir [--once]`, `install --profile --hub --token-file --capabilities-file [--label]`, `uninstall [--label]`. The same typer app is mounted as `resona worker …` in resona-cli (`worker_cli.py`).
- `update.py` — `resona-worker update --version --manifest [--wheels] --profile --hub --token-file --capabilities-file --work-dir [--releases-dir --extras --settings-file --label --worker-stopped --drain-timeout]`: manifest → hub release check (`GET /v1/workers/releases/{id}`, same digest; warns when the release has no approved tuples, bundled or not) → SHA-256-verified wheels + `worker-constraints.txt` (uv.lock export) → drain → venv with only the `resona-worker` wheel (other wheels as URL constraints) → `prepare` with manifest model pins → swap `current`, then the capabilities file, then restart (launchd) or supersede; a failure in any of these restores `current` and the capabilities file (and restarts the previous release's launchd service). Any failure keeps the previous release. No hub-triggered path.
- `drain.py` — work-dir handshake files `drain`/`drained`/`superseded`/`supervisor.pid`; `wait_drained` also returns when the recorded supervisor is dead. Docs: `docs/server/releases.md`.
- The worker holds no ML dependencies in the base install; extras `audio-cpu`, `apple`, `cuda`, `documents` add the engine packages. It opens no listening socket. Mac setup: `docs/server/mac-worker.md`. Linux NVIDIA: `apps/resona-worker/Dockerfile` (CUDA build args; `torch 2.12.0+cu130` covers Blackwell `sm_120`) + root `docker-compose.worker.yml` (GPU reservation, `models`/`state` volumes, tmpfs `/work`, read-only 0600 token bind, no ports/socket), docs `docs/server/linux-worker.md`.
- Postprocess in `supervisor.py`: each llm/extract step is guarded by a fresh hub policy check; `PostprocessStepFailed` → job failure `postprocess_failed` (retryable); chunk warnings go into `JobResult.warnings`. After the job's last step on an endpoint with `unload_url` (job-core `Endpoint`, `local_llm` + loopback only, validated at settings save), and on any failure exit after a call, `_unload` POSTs to it (best effort, 10 s, logged by endpoint id only). `docker-compose.worker.llm.yml` overrides the worker with `network_mode: host` so a host-loopback model server (llama-swap on gpu-box `127.0.0.1:9292`) passes the loopback policy.

### resona-mcp (lives in `apps/resona-mcp/`)
- `server.py` — `build_server(client_for, InputPolicy) -> MCPServer` (SDK `mcp==2.3.0`, `mcp.server.mcpserver`). Tools: `create_upload_url` (single-use 10-min hub upload link + `curl` command; file bytes bypass the model), `upload_content` (8 MiB decoded cap and required `sha256` of the original, both checked before any hub call), `transcribe_audio`/`convert_document` (exactly one of `upload_id`/`path`; return `{job_id, status, private}` immediately; `private=True` default), `get_job`, `get_result(job_id, offset, limit≤32768)` (one hub range read via `ResonaClient.get_result_markdown`, never the whole result; chunk + `next_offset`/`total_chars`; chunks concatenate to REST `markdown`), `cancel_job`, `list_engines`, `get_settings`. No settings/profile mutation, credential or enrollment tools — `TOOL_NAMES` is the full surface. Hub errors become tool errors carrying the hub code.
- `paths.py` — `resolve_under_root(raw, root)`: stdio-only `path` input; rejects URLs, `..`/symlink escapes and non-regular files.
- `stdio.py` — `create_stdio_server()`: `RESONA_API_URL` + `RESONA_API_KEY` required; `RESONA_MCP_ROOT` enables path input (no default).
- `http.py` — `create_app()`: Streamable HTTP at `/mcp`. `BearerAuth` requires `Authorization: Bearer` on every request, verifies it against the hub (`HubTokenVerifier`, `GET /v1/settings`, 30 s positive cache) and binds each MCP session id to the token hash (other token → 404). Tool calls build a per-request `ResonaClient` with the caller's token; the server holds no hub key. Remote input policy rejects `path`. Host/Origin allowlists via SDK `TransportSecuritySettings` (`RESONA_MCP_ALLOWED_HOSTS`/`RESONA_MCP_ALLOWED_ORIGINS`).
- `cli.py` — `resona-mcp stdio` | `resona-mcp http --host --port`; logs to stderr only.
- Uses `ResonaClient.upload_bytes` / `submit_upload` for in-memory uploads. Docs: `docs/guide/mcp.md`.

### resona-eval (lives in `packages/eval/`)
- `metrics.py` — versioned normalizations (`text-v1`: lowercase, ’→', non-word chars → space, whitespace collapsed; `markdown-v1`: HTML comments removed, then `text-v1`), `wer`/`cer`/`score` (also used by `benchmarks/`), `inserted_words`, `missing_table_cells` (cells of Markdown pipe tables), `reading_order_failures` (ordered anchors)
- `corpus.py` — `load_corpus(dir)`: `eval/corpus/MANIFEST.json` (per fixture: kind, file, SHA-256, source, license, exact reference, checks, table cells, reading-order anchors, optional document job `options`); refuses files that do not match their hash and document `options` that are not valid `DocumentOptions` (`Fixture.document_options()` = fixture language merged with the options; audio fixtures take none). Corpus hash = SHA-256 of MANIFEST.json
- `scoring.py` — `score_fixture(fixture, JobResult)`: audio on `raw_text` (WER/CER, `silence_insertions`), documents on `markdown` (CER, `missing_cells`, `order_failures`; polish rewrites `markdown`, so postprocess runs score the polished text). Numbers only
- `gate.py` — `evaluate_gate(candidate, baseline)`: pure; fails on missing cells, order failures, silence insertions, WER/CER > baseline + 0.02 absolute, fixtures/metrics absent from the baseline (`baseline_required:<id>`) or baseline fixtures not evaluated (`missing_fixture:<id>`). No comparable baseline (none, or another corpus hash) → outcome `baseline_required`; `Gate.record()` then reports only the absolute checks so the hub can admit a first promotion
- `evaluation.py` — `build_report(...)` (report.json: metrics + provenance, platform from the ASR/layout stage accelerator, model revisions, sorted `runtime_versions`, `postprocess_profile`) and `evaluation_record(...)` (`POST /v1/evaluations` body). A report with a `postprocess_profile` is measurement-only: no baseline allowed, outcome `measurement_only` unless absolute checks fail, not recordable and `promoted_entry` refuses it
- `baselines.py` — `eval/baselines.json` keyed `<execution_profile>/<platform>`: corpus hash, release, runtime versions, normalization ids, model revisions, per-fixture metrics; `upsert_baseline` writes atomically
- Corpus generator: `eval/corpus/make_corpus.py --only base|hard|all` (base: macOS `say -v Anna` + Arial; hard document fixtures: reportlab-bundled Bitstream Vera, byte-reproducible with pinned reportlab/pillow/numpy). Changing the manifest changes the corpus hash, so every baseline must be re-promoted. Docs: `docs/guide/evaluation.md`

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
- `registry.py` — `ENGINES`, `ENGINE_INFO` (languages/cloning/presets/instruct per engine), `installed_engines()`, `recommended_engine()` (→ `kokoro`), `recommended_offline_engine()` (→ `piper`, the torch-free default used by the CLI `speech` fallback), `get_engine(name)` (memoized, lazily instantiated singleton), `reset()`. Plain dict + importlib (like cloud-tts), not entry points
- `audio.py` — `to_wav_bytes`, `to_numpy`, `wav_result` (engines produce float32 + sr, encoded to WAV/PCM_16)
- `engines/` — one module per engine; each lazy-imports its native lib (clear `EngineUnavailableError` with install command if absent) and calls `configure_model_cache()` in `__init__`:
  - `piper.py` (`PiperEngine`) — Piper via `piper-tts` (ONNX/onnxruntime), **torch-free**, preset voices, cross-platform, CPU-realtime. Does **not** use `_base.py` (no torch). Voices downloaded on first use to `PIPER_VOICES_DIR` (default `<model_cache_dir()>/piper`) via `piper.download_voices.download_voice`; `speed` maps to `length_scale=1/speed`. Default voice German (`de_DE-thorsten-medium`). Ships as a **base dep of `resona-cli`** (the only torch-free local TTS) so `resona speech` works offline. Lockable extra: `resona-tts-local[piper]`
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
- `llm.py` — `llm_transform(text, *, prompt, endpoint, policy, ...)` and `llm_extract(text, *, prompt, endpoint, policy, ...)` via litellm. Both require an `Endpoint` and an `ExecutionPolicy`; `check_policy` runs before every provider call. No implicit model, base URL or env fallback inside these functions; `local_llm` endpoints get a fixed placeholder `api_key` (never an ambient `OPENAI_API_KEY`), and litellm is imported with `LITELLM_LOCAL_MODEL_COST_MAP=True`. `llm_transform_chunked(...)` (llm steps with `chunk_chars`): per-chunk calls with `max_attempts`, a chunk that keeps failing raises `PostprocessStepFailed` (hard failure, never skipped); a reply failing `max_length_deviation`/`preserve_markup` keeps the raw chunk + warning `polish_chunk_rejected:<n>:<length|markup>`. `legacy_step_endpoint(step)` derives an endpoint for explicit local CLI mode only (`RESONA_LLM_MODEL` / `RESONA_LLM_API_BASE`, loopback = `local_llm`, otherwise `cloud_llm`)
- `chunking.py` — `split_markdown(text, chunk_chars)` (block boundaries; never cuts a pipe table or fenced code block; chunks concatenate to the input), `markup_signature` (HTML comments, heading levels, table row cell counts), `length_deviation`
- `pipeline.py` — `PostprocessPipeline`: composable step chain; `PostprocessResult{text, data, warnings}` (text steps may return `StepOutput(text, warnings)`); `build_pipeline(profile, *, policy)` validates every LLM stage against the policy before returning (`PolicyDenied` on denial); `local_policy(profile, *, private)` builds the policy for local CLI runs only; `run()` re-raises `PolicyDenied` and `PostprocessStepFailed`
- `profile.py` — `Profile` dataclass (name, description, initial_prompt, steps); `resolve_profile(ref, profiles_dir)` accepts a name, inline JSON string, file path, or `Profile`/dict; `list_profiles(dir)`, `bundled_default()`; validates the chunking keys (`chunk_chars` ≥ 1000, `max_attempts` 1–10, `max_length_deviation`, `preserve_markup`, `timeout`)
- `profiles/default.json` — bundled default profile with German dictation replacements (Komma, Punkt, Absatz, medical headings, name corrections)
- `profiles/document-polish-de.json` — OCR-correction-only chunked llm step on endpoint `local-llm` (an org admin registers a loopback `local_llm` endpoint under that id on the approved worker; 12000 chars, 3 attempts, 30 % length guard, markup guard); uploaded to a hub with `resona profiles push`, see `docs/server/linux-worker.md#llm-polish-on-the-worker-host`. Polished results keep the engine's Markdown in `JobResult.raw_markdown`
- `default_replacements.json` — bundled replacement rules used by the default profile

### resona-api
- `app.py` — FastAPI lifespan: validates env, takes the single-hub lock (`hublock.acquire()`, released on shutdown), runs `migrate` (`db/engine.create_db_and_tables`), removes legacy `MD_PATH` result exports once, starts `maintenance.maintenance_loop()` as an asyncio task (cancelled on shutdown). Mounts health, jobs, worker, audio, profiles, settings and streaming routers; `errors.install_error_handlers` renders `{error, message}` bodies at the top level. The hub runs no inference for queued jobs
- `health_routes.py` — unauthenticated `GET /health` (liveness, `{status:"ok"}`) and `GET /ready` (200 only when schema is at `TARGET_VERSION`, `DATA_PATH`/`DB_PATH` writable and `maintenance.last_tick` within 90 s, else 503 `not_ready`; `capacity` `available|degraded` from approved workers seen recently — offline workers never fail readiness)
- `hublock.py` — exclusive `flock` on `<DB_PATH>/hub.lock`; the hub holds it for its lifetime, offline `resona-admin restore` refuses (`HubRunning`) while it is held
- `restore.py` — `restore(backup)` for `resona-admin restore`: hub stopped, SQLite backup API copy + migrate, revokes every restored credential and worker, invalidates leases (requeue or fail), flags missing payloads `content_unavailable` (pending → failed; completed → result route 410 `content_unavailable`), unconsumed uploads without file dropped, then one maintenance pass. Counts only
- `run.py` — `resona-api` entry point; `log_config()` adds a filter that strips query strings from uvicorn access lines
- `jobs_routes.py` — client job API (roles client|org_admin; worker tokens 403): `POST /v1/uploads` (multipart `file`, streamed to staging with SHA-256, cap `RESONA_MAX_UPLOAD_BYTES` default 500 MiB → 413 `too_large`, magic-byte sniffing → `audio|document` else 415, empty 422; expires after 1 h unattached), `POST /v1/upload-links` (mints a single-use `UploadLink`, hash-only, 10 min, URL from `RESONA_PUBLIC_URL`) + `POST /v1/upload-links/upload` (`X-Resona-Upload-Token`, spent by the first attempt, 401 `invalid_upload_link` when used/expired/minting credential revoked), `POST /v1/jobs` (`Idempotency-Key` required; same key+request → same job, changed request → 409 `idempotency_conflict`; consumes the upload atomically, 404 `upload_not_found`; resolves the org's current settings revision + execution profile into an immutable `execution_snapshot` (candidates, language, typed options, postprocess profile revision, policy); `check_policy` on every candidate stage and `build_pipeline` on the postprocess profile before the job exists → 403 `policy_denied`), `GET /v1/jobs` (newest first, opaque cursor, limit 1–100), `GET /v1/jobs/{id}` (JobView with `queue_reason`: `no_capable_worker` | `release_not_approved` | `waiting_capacity`), `GET /v1/jobs/{id}/result` (409 `not_ready`, 410 `result_expired`, 410 `content_unavailable` when the result file did not survive a restore), `GET /v1/jobs/{id}/result/markdown?offset=&limit=` (one Markdown character range, `limit` ≤ `MAX_MARKDOWN_CHUNK_CHARS` = 32768 → `{text, offset, next_offset|null, total_chars, kind, language, page_count, warnings, provenance}`, same 404/409/410), `POST /v1/jobs/{id}/cancel` (pending → cancelled + input removed; processing → `cancel_requested` fences the lease), `DELETE /v1/jobs/{id}` (any state: pending/processing jobs are cancelled and their lease fenced in the same transaction; row, idempotency keys and files go at once, later reads 404; a deleted processing job stays as an invisible `cancelled`/`deleted` tombstone until its lease would have expired, then `broker.sweep_leases` purges it). Cross-org IDs are 404. Pydantic errors become safe field-path messages via `errors.validation_message`
- `worker_routes.py` / `broker.py` — pull-worker protocol (`/v1/workers/heartbeat|claim`, lease-fenced `/v1/workers/jobs/{id}/input|heartbeat|policy|result|fail`); broker holds the DB/filesystem logic in short `BEGIN IMMEDIATE` transactions; lease-bound calls take a frozen `LeaseRef(worker_id, organization_id, job_id, lease_token)`. `try_claim` takes a job only if its privacy class equals the worker's approval (private ↔ `approved_private`, `private=false` ↔ non-private worker), a snapshot candidate is offered and hub-approved, `releases.may_run` approves the worker release, and every snapshot endpoint carrying `worker_id` (postprocess LLM + cloud ASR, `endpoints_assigned`) is assigned to this worker; ineligible jobs are skipped, never written. `check_stage` denies an endpoint assigned to another worker. Non-private jobs of an org that disabled cloud (`cloud_denied`) are failed `policy_denied` by `sweep_leases`, not by claim polls. `fail_job` never mutates `approved_capabilities`
- `maintenance.py` — `run_once()` / `maintenance_loop()`: lease expiry, upload/result/metadata retention, expired upload-link deletion, staging and orphan sweeps; each successful pass sets `last_tick` (read by `/ready`)
- `storage.py` — generated-name layout under `<DATA_PATH>/organizations/<storage_id>/` with staged atomic writes
- `deps.py` — `get_db_session`
- `profiles_routes.py` — org-scoped profile revisions: `GET /profiles`, `GET /profiles/{name}` (client+admin), `PUT /profiles/{name}` and `DELETE /profiles/{name}` (org_admin). Stored bodies go through `Profile.from_dict`; the bundled `default` stays readable. Hosted jobs accept profile names only
- `settings_routes.py` — `GET /v1/settings` returns `{organization_id, revision, settings}` with `ETag`; `PUT /v1/settings` (org_admin) requires `If-Match` (428 missing, 409 stale) and creates a new revision
- `audio_routes.py` — OpenAI-compatible audio routes: `GET /v1/engines`, `POST /v1/audio/transcriptions`, `POST /v1/audio/speech`. `/v1/engines` is org-scoped and probes nothing: candidate tuples of the org's execution profiles that ≥1 approved, unrevoked org worker has both approved and reported (tested), grouped per (engine, job kind) with `candidates[{candidate, execution_profiles}]`; cloud ASR only with `cloud_enabled` + an approved `cloud-asr:<provider>` endpoint and only via non-private workers; TTS engines from `engine_registry.speech_catalogue()` (cloud ones only with `cloud_enabled`); `available` = such a worker is online; `default` = first listed audio engine of the default execution profile or null. `/v1/audio/transcriptions` is an adapter onto the job path: it streams the multipart `file` into an upload and admits a normal audio job through `jobs_routes._admit` (same policy, execution profile and stored-profile rules; optional `Idempotency-Key`, else a generated `sync-<uuid>` key; `model=whisper-1` means the profile default, any other `model`/`engine` must be an approved candidate → 422 `unapproved_override`), then waits up to `RESONA_SYNC_WAIT_SECONDS` (default 60) for a worker and answers `json`/`text`/`verbose_json` from the `JobResult`. On timeout: 504 `{error:'timeout', message, job_id}` with `Location: /v1/jobs/<id>` while the job continues; a failed job maps its error code (403 `policy_denied`, 502 otherwise). The hub never runs STT itself. `/v1/audio/speech` dispatches to **local** TTS engines (`resona-tts-local`: kokoro/chatterbox/qwen…) or **cloud** providers — pick via `engine`; `voice`/`language` forwarded
- `streaming_routes.py` — Deepgram-compatible live streaming: `WS /v1/listen`, plus `GET /v1/listen/engines` (client credential): the engines the org can stream right now — available local engine-servers, and cloud providers with a realtime API when `cloud_enabled` and their `cloud-asr:<provider>` endpoint is approved — each `{name, kind, private, models}`; `default` is `RESONA_DEFAULT_ENGINE` if listed and private, else the first local engine, else null. Auth via org credential: `Authorization: Token <token>` (also Bearer/X-API-Key/`?token=`/`?api_key=`), resolved to a `Principal`; tenant headers are never read. Query params `model`/`language`/`encoding`(linear16)/`sample_rate`/`interim_results` + Resona `engine`/`profile`. Cloud bridges are denied for private sessions and for orgs without `cloud_enabled`. Resolves the engine, then dispatches: a **local** engine-server → `_run_local_bridge`/`_bridge` (proxy to its `/ws/live`); a **cloud** provider with a realtime API (Deepgram, ElevenLabs) → `_run_cloud_bridge`/`_bridge_cloud` (opens `resona_cloud_stt.streaming.open_stream`, bridges binary PCM ↔ provider session). Both emit Deepgram `Results`/`Metadata`. Cloud providers without streaming (OpenAI) are rejected. `_CLOUD_FINAL_DRAIN_SECONDS` bounds the post-stop drain for providers that don't close on commit (ElevenLabs)
- `engine_registry.py` — multi-backend catalogue: probes local engine `/health` endpoints, lists local TTS engines (`_local_tts_engines`, `kind="local-tts"`, private, available when `resona-tts-local` is installed), checks cloud provider API keys, `EngineInfo` dataclass, `resolve(name)`, `speech_catalogue()` (TTS engines for `/v1/engines`, no engine-server probe), `cloud_asr_endpoint_id(engine)` (`cloud-asr:<provider>` or None), `run_tts(engine, text, ...)` (dispatches `local-tts` → `resona_tts_local.get_engine().synthesize`, else cloud provider), `cloud_api_key(provider)` (public key lookup for the streaming bridge), error hierarchy
- `privacy.py` — `admit(...)` resolves and policy-checks the synchronous audio chain; `cloud_endpoint_allowed(settings, engine)` tells whether the org's cloud policy approves an engine's cloud ASR endpoint (always true for local engines; shared by `/v1/engines` and `/v1/listen/engines`); `load_org_profile`, `is_profile_name`
- `settings_store.py` — `current_settings(session, org) -> (revision, OrganizationSettings)`
- `db/models.py` — `Job` SQLModel table (non-null `organization_id`, `kind`, `private`, `settings_revision`) plus `Organization`, `Credential` (SHA-256 hash only), `Worker`, `SettingsRevision`, `ProfileRevision`
- `db/engine.py` — SQLite engine (WAL, `foreign_keys=ON`, `busy_timeout=5000`); `db/schema.py` runs ordered numbered migrations under `BEGIN EXCLUSIVE` with a pre-upgrade backup (`<db>.pre-v<N>.bak`); `migrate(engine)` is called at startup. Legacy job rows require `RESONA_BOOTSTRAP_ORGANIZATION_ID`. Migrations: 1 identity/settings/jobs, 2 releases/evaluations, 3 upload links, 4 `job.id` AUTOINCREMENT (rebuilds `job`, seeds `sqlite_sequence` from every job-id reference) so a deleted job's id is never reissued
- `paths.py` — `DATA_PATH`, `FILE_PATH`, `DB_PATH` resolved from env
- `auth.py` — `Principal(credential_id, organization_id, role, worker_id)` resolved from stored, unrevoked credentials (`Authorization: Bearer` or `X-API-Key`; conflicting values → 401). `require_roles(...)` enforces roles (403 on mismatch); `websocket_principal` resolves WS tokens the same way. No auth-disabled mode
- `admin.py` — hub-local `resona-admin`: `organization create`, `credential issue|revoke`, `worker enroll|approve|revoke`, `release stage --manifest <file>|list`, `restore <backup>`. Tokens are shown once; only their hash is stored. `credential issue`/`worker enroll` accept `--token-sha256 <hex>` to register a token generated elsewhere without it ever reaching the hub
- `releases.py` — release identity (`hub_release_id()` = `RESONA_RELEASE_ID` else installed `resona-api` version), `stage_release` (validated `resona_job_core.release.ReleaseManifest`, immutable per id; the bundled placeholder may be filled by its real manifest), `approved_tuples`, `may_run`. The bundled hub release (recorded by migration 2 and at startup) may run evaluation jobs without staging; production jobs of every release, bundled included, need an `ApprovedReleaseTuple` from a promoted evaluation for (execution profile, platform, release). Otherwise pending jobs show `queue_reason: release_not_approved`
- `evaluations_routes.py` — `GET /v1/version` (no auth), `POST/GET /v1/evaluations`, `POST /v1/evaluations/{id}/promote`, `GET /v1/baselines` (org_admin; metrics only). Recorded evaluations need `fixture_checks` (`{fixture_id: {kind, checks}}`) and exactly the metrics those checks produce, else 422 `incomplete_metrics`. Gate re-checked on promote: 422 `gate_failed`, 409 `baseline_revision_mismatch`; a current baseline on another `corpus_sha256` → 409 `baseline_corpus_mismatch` unless the body has `rebaseline: true` (then absolute checks only). Worker routes return 426 `protocol_incompatible` unless `X-Resona-Worker-Protocol` major is 1. Docs: `docs/server/releases.md`

### resona-client
- `client.py` — `ResonaClient`: the hub's canonical v1 job API. Reads `RESONA_API_URL` / `RESONA_API_KEY` and sends `Authorization: Bearer <key>` (the hub derives the organization from it). `upload(path) -> Upload`; `submit_job(path, *, kind='audio'|'document', execution_profile, language, engine, model, profile, options: AudioOptions|DocumentOptions, private=True, idempotency_key, provenance) -> JobRef` uploads then `POST /v1/jobs` with an `Idempotency-Key` (uuid4 unless given), reusing the same key on bounded retries after transport errors / 502–504. `get_job`/`cancel_job -> JobView`, `list_jobs(cursor, limit) -> JobPage`, `wait_for_job(id, timeout, poll)` (raises `JobTimeout`; never falls back locally), `get_result -> resona_job_core.JobResult` (409 → `JobNotReady`, 410 → `ResultExpired`), `get_result_markdown(id, offset=0, limit=32768) -> MarkdownChunk` (`GET /v1/jobs/{id}/result/markdown`, same errors), `promote_evaluation(id, expected_baseline_revision=, rebaseline=False)` (409 → `RevisionConflict`), `delete_job`, `get_settings`/`put_settings(settings, if_match=etag) -> SettingsDoc` (409 → `RevisionConflict`). Error bodies `{error, message}` → `ResonaAPIError(status, code, message)`; 403 `policy_denied` → `PolicyDeniedError`. Also `create_transcription(audio_path, *, model, language, response_format, engine, execution_profile, private=True, profile)` (`/v1/audio/transcriptions`; 504 → `SyncWaitTimeout` carrying `job_id`, the job keeps running), `create_speech`, `list_engines` and profile methods on `/profiles/{name}` (`list_profiles`, `get_profile`, `put_profile`, `delete_profile`). Depends on `resona-job-core` (pydantic only), no ML stack.
- `config.py` — `EngineConfig`: `~/.resona/config.json`, auto-start (SSH tunnel, docker compose), `default_engine`, `default_profile`; `EngineEntry`: one Resona hub (resona-api) — `name`, `api_url`, `api_key`, `compose_dir`, `ssh_host`, `ssh_remote_port`; legacy `type: "cloud"` entries are dropped on load with a warning and obsolete keys (`private`, `provider`, …) ignored; `resolve_engine()` — probes priority-ordered hub entries, auto-starting unreachable ones

### resona-cli (lives in `apps/resona-cli/`)
- `main.py` — typer app root, `resona` command
- `hub.py` — hub selection and errors shared by hub commands: `connect_hub()` and `hub_identity()` resolve the same hub (`RESONA_API_URL`/`RESONA_API_KEY`, then `WS_API_URL`/`WS_API_KEY`, else the first `resona-api` entry with a URL in `~/.resona/config.json`); an unreachable chosen hub → exit 1, never another entry and never local. `fail()`, exit codes (`EXIT_ERROR=1`, `EXIT_POLICY_DENIED=2`), `check_privacy_flags(private, allow_cloud)` (`--private`/`--allow-cloud` mutually exclusive; default private), `policy_summary(job)` (one stderr line: job, private, execution profile, candidate, status)
- `media.py` — shared engine of `transcribe`/`doc`: `MediaRequest`, `run_media(req)`. Hub mode uploads each file as an audio/document job (`submit_job`), prints the policy summary to stderr, waits (`--timeout`; a timeout leaves the job running and exits 1) or with `--no-wait` prints job IDs; `--json` prints one parseable JSON report on stdout. Policy denial exits 2, hub 422 `unapproved_override` is reported as such; nothing falls back to local or cloud processing
- `transcribe.py` / `doc.py` — `resona transcribe <files|globs|dirs>` (audio; `--translate`, `--word-timestamps`) and `resona doc` (PDF/PNG/JPEG/TIFF; `DocumentOptions` flags `--ocr auto|force|off`, `--ocr-engine`, `--languages de,en`, `--pages a-b`). Shared flags: `--execution-profile` (approved candidates) separate from postprocess `--profile` (stored profile name; file/inline JSON only with `--local`), `--engine/--model` overrides (hub accepts approved candidates only), `--language`, `--output-dir`, `--force`, `--json`, `--no-wait`, `--timeout`, `--private`/`--allow-cloud`, `--local`
- `outputs.py` — `output_paths(source, output_dir)` → `<stem>.md` + `<stem>.result.json`; `write_result(...)` writes both via temp file + link/rename, refusing to replace existing files without `force` (`OutputExists`)
- `jobs_cli.py` — `resona jobs get|wait|result|cancel|delete <id>` and `jobs list [--cursor --limit --json]`; `wait` exits 0/1/2 by outcome, `result` prints Markdown (`--json` the JobResult) or writes files with `--output-dir [--stem] [--force]`
- `eval_cli.py` — `resona eval --profile <execution profile> --output-dir <dir> [--remote] [--platform] [--release-id] [--corpus] [--baselines]`: local mode runs each fixture through `local_run` (organization polish disabled) and compares with `eval/baselines.json`; `--remote` submits `purpose=evaluation` jobs (org-admin key, staged release, default the hub's `/v1/version` release), records `POST /v1/evaluations` and compares with `GET /v1/baselines`. Exit 0 pass, 3 gate failed, 4 `baseline_required`. `--remote` records send `fixture_checks` from the corpus manifest. `resona eval promote <id> --report <dir>/report.json [--rebaseline]` promotes on the hub (expected revision = the hub baseline seen by that run) and pins the entry in `eval/baselines.json`
- `settings_cli.py` / `settings_cache.py` — `resona settings get|pull|set`: revisioned non-secret settings cached per `(hub URL, organization_id)` in `~/.resona/settings-cache/` (`credential` = SHA-256 prefix of the token, never the token; `stale` flag). `set --file <json>` or `key=value` validates against `OrganizationSettings`, sends `If-Match` from the cached (else freshly fetched) ETag; 409 → exit 1 with a pull hint; changes apply to new jobs only
- `worker_cli.py` — `resona worker run|prepare|capabilities|install|uninstall|update`: resona-worker's typer app (`resona_worker.cli.app`) mounted under `resona`, so both entry points share one implementation
- `local_run.py` — explicit `--local`: loads cached settings for the configured hub+credential (marks them `stale`) or built-in private defaults, resolves the execution profile's approved candidates, runs `check_policy` (+ `build_pipeline` with `local_policy(profile, base=org policy)`) before any processing, then runs the candidate in the worker's execution subprocess (`python -m resona_worker.execute`, private env: offline caches, no credentials). Cloud only when allowed by the cached org settings
- `watch.py` — `watch` subcommand: polls a directory and runs each new audio file as a private hub job (or `--local`), writing `<stem>.md` + `.result.json`; existing outputs are skipped, policy denial stops with exit 2
- `speech.py` — `speech` subcommand: synthesise TTS. Tries the gateway (`client.create_speech`); on `HTTPStatusError` (reachable gateway) surfaces the error, but on no-server / `ConnectError` / `TimeoutException` falls back to a local in-process engine via `resona_tts_local.get_engine` — engine = `--engine` if it's a registered local-tts engine, else `recommended_offline_engine()` (Piper, torch-free). `--voice` defaults to `None` (engine default); `--language/-l` (default `de`) drives local voice selection. Offline output is always WAV. `_emit` handles play/stdout/file
- `engine.py` — `Engine` Protocol + `InProcessEngine` (direct asr-core call) for the `live` TUI
- `local_engine.py` — `probe_local_engine(port=DEFAULT_LOCAL_ENGINE_PORT)` checks `RESONA_LOCAL_ENGINE_PORT` (default `7720`) for an already-running resona-engine-server via `/health`. `LocalEngine`: HTTP transcription client for a resona-engine-server — on `__enter__`, attaches to an already-running one if `probe_local_engine()` finds it, else spawns `uv run resona-engine-<name>` for the context's lifetime. Used by `live_ui.py` to avoid loading an in-process model when a local engine is already running.
- `engines.py` — `resona engines` subcommands for Resona hub entries (`list`, `add <name> <api_url>`, `remove`, `test`, `benchmark-select`, `status` = hub's live engine catalogue)
- `profiles.py` — `resona profiles` subcommand: `list`, `show`, `push`, `pull`, `delete` for server-side profile management
- `micrec.py` — `RecordingSession` + `MicRecApp` Textual TUI base; `rec` subcommand
- `markdown_output.py` — `render_markdown(text, meta)` / `write_markdown(path, body, meta)` / `read_markdown(path) -> (body, meta)` / `strip_frontmatter(text) -> (body, meta)`: hand-rolled YAML-frontmatter round-trip (booleans/numbers plain, strings quoted) for job outputs and `live_ui.py`'s autosaved documents
- `dictate_anchors.py` — pure, Textual-independent anchor-tracking: `Location`, `adjust_location()`, `AnchorTracker` (`register`/`apply_edit`/`get`/`pop`). Keeps a dictation segment's insertion point valid as the surrounding document is edited (by the user, or by another segment resolving) while its transcription is in flight — `start` is inclusive/right-gravity, `end` is exclusive/left-gravity so a span never absorbs adjacent typed text or another segment's marker
- `dictate.py` — shared push-to-talk/live-dictation primitives used by `live_ui.py`: `Segment` (`kind: "ptt"|"live"`), `DictationTextArea` (a `TextArea` that reports every edit — keystrokes and programmatic — to an `AnchorTracker`, and via `internal_edit()`/`watch_selection()` distinguishes genuine user activity from our own bookkeeping edits, so starting/resolving one segment never wrongly disturbs another), `resolve_anchor()` (place text at a span or fall back to the live cursor; optionally leaves a fresh marker behind for a live segment's next delta), `TranscriptionWorker` (single background thread serializing all access to the shared `get_transcriber()` model across every open document and both dictation modes), `run_serialized()` (blocking helper for local live-mode's per-tick model calls)
- `live_ui.py` — `LiveApp`: the primary TUI, `live` subcommand. Multiple open documents as tabs (`DocumentPane`, created via `ctrl+n` or the startup filename prompt when launched with no file argument — `NewFileModal`). Each document defaults to push-to-talk (`F8` records/stops a segment via `dictate.py`'s anchor mechanics); a per-document **Live switch** (`ctrl+l`, backed by a `Switch` widget) flips it to continuous streaming transcription instead, splicing confirmed deltas into the same anchor-tracked buffer (leaving a fresh marker behind after each delta, removed for good on stop). `--language/-l`, `--engine/-e` (in-process engine: flag → `RESONA_ENGINE` → `platform_preferred_engine()`; validated against installed engines, exported as `RESONA_ENGINE`), `--remote/-r URL` (streams instead of running locally — forces every document into Live mode, since push-to-talk over an explicit `--remote` gateway isn't implemented). Without `--remote`, `_load_engine` first calls `local_engine.probe_local_engine()` — if a resona-engine-server is already running (default port `7720`), attaches to it via HTTP for push-to-talk *and* points Live mode's `RemoteLiveTranscriber` at it (`LiveApp._detected_engine_url`), loading no in-process model at all; otherwise falls back to `InProcessEngine`. With `--remote` **and** `--engine`, hits a resona-api `/v1/listen` gateway (`--engine` picks the backend: `deepgram`/`elevenlabs`/local engine name); with `--remote` alone, an engine-server `/ws/live`. `DocumentPane` duck-types the `app_ref` interface `RecordingSession` expects, so each document's recording state is independent; only one document app-wide may record at a time (one physical mic). Autosaves as markdown after every insertion, on `ctrl+s`, and on quit
- `remote_live.py` — remote `live` backends over a `websockets` sync connection (sender + receiver threads), all exposing the `LiveTranscriber` pull surface (`add_audio`/`has_enough_audio`/`process_sync`/`flush_sync`/`get_full_transcript`/`_audio_event_sync`). `_BaseRemoteLive` holds the shared threading; subclasses define URL, finish frame, and message classifier: `RemoteLiveTranscriber` → engine-server `/ws/live` (`partial`/`final` JSON, `stop` to finish), `GatewayLiveTranscriber` → resona-api `/v1/listen` (Deepgram `Results`/`Metadata`, `CloseStream` to finish, `?engine=` selects backend, `RESONA_API_KEY` → `Authorization: Token`). Audio is the shared `{type:audio, data:base64 int16}` frame (accepted by both upstreams); `_pcm_b64` encodes 16 kHz float32 → base64 int16; `_build_ws_url` normalizes URLs + merges query params
- `ui.py` — `WSUIApp`: record-and-transcribe TUI

## Import conventions

Within a package, use relative imports:
```python
from .db.models import Job
from .storage import job_dir
```

Cross-package imports: resona-cli imports `resona_asr_core.live_transcriber` (for the `live` command), `resona_asr_core.registry` (for `InProcessEngine`) and `resona_worker.execute` (`subprocess_env` for explicit `--local`; it runs `python -m resona_worker.execute`). `resona-asr-core`, `resona-engine-faster-whisper` and `resona-worker` are base dependencies of resona-cli, so these imports always resolve. All other cross-package communication is over HTTP.

## How to add a new transcription engine

1. Create `packages/engine-<name>/` with src-layout
2. Implement a class with `transcribe(audio: np.ndarray, **kwargs) -> TranscriptionResult`
3. Constructor: `__init__(self, device: str, modelname: str | None = None)`
4. Register in pyproject.toml: `[project.entry-points."resona.engines"]`
5. Add `[tool.uv.sources]` with `resona-asr-core = { workspace = true }` and `resona-engine-server = { workspace = true }`
6. Set `[project.scripts]` to `resona_engine_server.run:main`
7. The engine must not touch the database

## How to add a new endpoint to resona-api

1. Add the route to the matching router module (`jobs_routes.py` for client job routes); raise errors with `errors.api_error(status, code, message)`
2. Add any new DB models to `db/models.py`
3. Add a corresponding method to `ResonaClient` in `packages/client/src/resona_client/client.py`
4. Add a CLI subcommand if appropriate

## Job flow

### Server path

```
Client → POST /v1/uploads → hub streams the file into org storage, returns upload_id
Client → POST /v1/jobs (Idempotency-Key) → hub snapshots settings/profile/candidates, checks policy, creates PENDING job
Worker → POST /v1/workers/claim → fenced lease + execution_snapshot →
  GET input → runs engine + postprocess (fresh /policy check before any external stage) →
  POST result (JobResult JSON) → hub promotes result.json, job COMPLETED, input removed
Client → GET /v1/jobs/{id} → status; GET /v1/jobs/{id}/result → JobResult (24 h retention)
```

Hosted jobs accept a stored profile name only (or none: the org's `postprocess_profile` when `polish` is on).

### Explicit local path (`--local`)

```
resona transcribe memo.m4a --local --execution-profile cpu-private
  settings: cached `resona settings pull` revision for this hub+credential (marked stale) → else built-in private defaults
  resolve execution profile → approved candidates (engine/model overrides must match one) →
  check_policy on every stage (+ build_pipeline for --profile) before any processing →
  python -m resona_worker.execute (private env, offline caches) → JobResult →
  writes <stem>.md + <stem>.result.json
```

Never automatic: a configured hub that times out, denies or is unreachable is an error, not a reason to run locally.

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
| `RESONA_LLM_MODEL` | _(unset)_ | Explicit local CLI mode only (`local_policy`): model for legacy `llm`/`extract` steps without `model`. No default model; the hub never reads it |
| `RESONA_LLM_API_BASE` | (unset) | Explicit local CLI mode only: API base for legacy steps. Auto-filled for `lm_studio/`→`:1234/v1` and `mlx/`→`:10240/v1` when unset |
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
uv run resona live                     # push-to-talk dictation TUI (prompts for a filename)
uv run resona live notes.md            # open (or create) a document directly
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
uv run pytest apps/resona-mcp/tests/               # mcp server
uv run pytest packages/postprocess/tests/          # postprocess
uv run pytest packages/tts-local/tests/            # local TTS engines
uv run pytest -k test_transcribe                   # one test
```

Mocking strategy:
- resona-engine-server: mock the transcriber at `resona_engine_server.app.get_transcriber`
- resona-api: `TestClient` against the app with org/credential fixtures (`packages/api/tests/conftest.py`); drive queued jobs through the worker routes (claim → result) instead of mocking inference
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
uv run --with datasets --with soundfile \
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

GPU engine packages use a `nvidia/cuda:*-runtime-ubuntu24.04` base (faster-whisper on 12.8.0; whisper/voxtral on 13.0.1; parakeet on 12.8.0 to match PyPI torch's bundled CUDA 12.x, with `libsndfile1` added for NeMo/soundfile). The API uses `python:3.12-slim` and also carries `resona-mcp` (the Coolify compose runs `resona-mcp http` from the same image). Do not add GPU deps to the API Dockerfile. `engine-mlx-whisper` has **no** Dockerfile — Metal cannot run in a Linux container; run it natively on macOS.

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

### Execution resolution (`resona transcribe` / `resona doc`)

1. Hub: `RESONA_API_URL`/`RESONA_API_KEY`, else the first reachable `resona-api` entry of `~/.resona/config.json`; none → error unless `--local`
2. `--execution-profile` (else the organization's `default_execution_profile`) selects approved candidates; `--engine/--model` may only narrow to an approved candidate (hub 422 `unapproved_override`)
3. `--private` (default) / `--allow-cloud`: cloud stages additionally require the organization's `cloud_enabled`; provider keys in the environment never enable cloud

## What NOT to do

- Do not add database access to engine-server or any engine package
- Do not add postprocessing (replacements, LLM) to the engine — it belongs in resona-api or resona-postprocess
- Do not delete audio files after transcription
- Do not add `ScanInboxTask` back — inbox scanning is done by `resona watch`
- Do not add a synchronous `/asr` endpoint to resona-api — the engine owns direct transcription
- Do not use `os.environ[]` — use `config()` from python-decouple
