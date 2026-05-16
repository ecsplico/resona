# Unified STT/TTS API + Engine Discovery — Design

**Date:** 2026-05-16

**Goal:** Turn `resona-api` into a gateway that exposes an OpenAI-compatible
speech API for both speech-to-text and text-to-speech, discovers and routes to
multiple transcription engines (several local `engine-server` instances plus
cloud providers), activates cloud engines purely from environment variables,
and serves an engine-discovery route. Add cloud text-to-speech as a new package.

This is **subsystem A** of a larger request. See "Scope and decomposition"
below for the other subsystems and why they are deferred.

---

## 1. Scope and decomposition

The original request spanned five independent subsystems. They are split into
separate spec → plan → implementation cycles:

| # | Subsystem | Status |
|---|-----------|--------|
| **A** | Unified STT/TTS API + engine discovery + cloud activation + Docker | **this spec** |
| B | Local TTS synthesis engines | not planned (cloud TTS only) |
| C | Compliance layer — transcription logging, encrypted audio retention for private jobs, corrected-transcript upload for training | future spec |
| D | Keycloak / OIDC user authentication | future spec |
| E | Multi-engine Docker | folded into A |

**This spec covers A and E only.** It deliberately does not implement
transcription logging, encrypted retention, corrected-transcript upload, or
Keycloak. Where A makes a decision that C or D will build on (the `private`
classification, the engine catalogue), that is called out.

## 2. Background — current state

- `resona-engine-server` (`:7001`) is a **stateless, single-engine** FastAPI
  service. One engine per process, selected by `RESONA_ENGINE`. Contract:
  `POST /transcribe` → `{text, language, segments}`. No DB, no postprocessing.
- `resona-api` (`:7000`) is an **async job queue**: `POST /jobs` stores a file
  and registers a `PENDING` job; a background `TranscribeTask` thread dequeues
  it, calls one engine, applies postprocessing, writes the result to the DB.
- Engine selection in `resona-api` today is binary and ad-hoc: if
  `RESONA_CLOUD_ENGINE` is set, `tasks_transcribe._cloud_transcribe()` calls a
  `resona-cloud-stt` provider; otherwise `EngineClient` calls the one
  `engine-server` at `RESONA_ENGINE_URL`.
- `resona-cloud-stt` already provides Deepgram, ElevenLabs, and OpenAI STT as a
  pure `httpx` package: each provider exposes an identical
  `transcribe(audio_path, *, api_key, model, language, options)` function.
- There is **no engine-discovery endpoint, no TTS anywhere, and no
  OpenAI-compatible API**.

## 3. Design decisions (resolved)

These were settled during brainstorming:

1. **`resona-api` becomes the gateway.** It hosts the unified API, the
   discovery route, and cloud activation. `engine-server` stays a stateless
   local-engine worker. (Chosen over expanding `engine-server` or adding a new
   gateway package — `resona-api` already has a DB and cloud routing, and
   subsystems C/D need a stateful host.)
2. **API surface mirrors OpenAI `/v1/audio/*`** — the de-facto standard, the
   only one of the three named providers that is symmetric for STT and TTS,
   and the one most existing clients already speak.
3. **`/v1/audio/transcriptions` is synchronous.** The async `/jobs` queue is
   kept for the existing CLI/web batch flow.
4. **`private=true` refuses cloud routing** — a private request must be handled
   by a self-hosted engine; the server hard-refuses before any audio leaves the
   machine if only cloud engines can serve it.
5. **Engine selected by an explicit `engine` field**, with an optional `model`
   field selecting the model within that engine. OpenAI clients that send only
   `model` get the server's default engine.
6. **TTS is cloud-only** — a new `resona-cloud-tts` package with OpenAI,
   ElevenLabs, and Deepgram (Aura) providers. No local synthesis.
7. **A gateway can have multiple local backends** — several `engine-server`
   instances, each running a different engine, all discovered and routable.
8. **Docker uses Compose profiles**, one per engine; several can be activated
   together.
9. **`RESONA_CLOUD_ENGINE` / `RESONA_CLOUD_MODEL` / `RESONA_CLOUD_OPTIONS` are
   removed** — superseded by the engine registry and `RESONA_DEFAULT_ENGINE`.
   The project is pre-1.0 and these vars were added recently; a clean
   replacement is preferred over a back-compat alias.
10. **`engine-server` `/health` is extended** to report `{status, engine,
    models}`. This adds no persistent state — it only reports the process's
    static configuration — so it does not violate the stateless contract. It
    is required so the gateway can tell multiple backends apart.

---

## 4. Component layout

```
packages/
  cloud-tts/                         ← NEW: resona-cloud-tts
    pyproject.toml                   # deps: httpx. Workspace member.
    src/resona_cloud_tts/
      __init__.py
      types.py        # SpeechResult TypedDict: {audio: bytes, content_type: str}
      errors.py       # CloudTTSError, MissingAPIKeyError, ProviderHTTPError
      registry.py     # PROVIDERS, PROVIDER_ENV_KEYS, DEFAULT_MODELS,
                      #   DEFAULT_VOICES, get_provider()
      providers/
        __init__.py
        openai.py
        elevenlabs.py
        deepgram.py
    tests/
      fixtures/                      # small audio response samples
      test_openai.py
      test_elevenlabs.py
      test_deepgram.py
      test_registry.py

  api/src/resona_api/
    engine_registry.py               ← NEW: engine catalogue + resolution
    audio_routes.py                  ← NEW: /v1/audio/* + /v1/engines routers
    engine_client.py                 ← unchanged HTTP client (now pooled)
    app.py                           ← register the new router
    tasks_transcribe.py              ← routes through engine_registry

  engine-server/src/resona_engine_server/
    app.py                           ← /health reports {engine, models}
```

`resona-api` adds `resona-cloud-tts` as a workspace dependency (it already
depends on `resona-cloud-stt`).

## 5. `resona-cloud-tts` package

A lean, `httpx`-only package, modelled exactly on `resona-cloud-stt`.

`registry.py` constants:

```python
PROVIDER_ENV_KEYS = {
    "openai":     "OPENAI_API_KEY",
    "elevenlabs": "ELEVENLABS_API_KEY",
    "deepgram":   "DEEPGRAM_API_KEY",
}
DEFAULT_MODELS = {
    "openai":     "tts-1",
    "elevenlabs": "eleven_multilingual_v2",
    "deepgram":   "aura-2-thalia-en",
}
DEFAULT_VOICES = {
    "openai":     "alloy",
    "elevenlabs": "Rachel",        # ElevenLabs default voice id/name
    "deepgram":   None,            # Deepgram voice is encoded in the model
}
PROVIDERS = {"openai", "elevenlabs", "deepgram"}
```

Every provider module exposes the **same** function:

```python
def synthesize(
    text: str,
    *,
    api_key: str,
    model: str | None = None,
    voice: str | None = None,
    response_format: str = "mp3",
    options: dict | None = None,
) -> SpeechResult: ...
```

- The package **never reads env vars** — the caller resolves the key and passes
  it in. Pure and trivially testable with `respx`.
- `options` is free-form; each provider whitelists the keys it understands and
  **drops unknown keys with a `logging.warning`**.
- `SpeechResult` = `{"audio": bytes, "content_type": str}`.
- `errors.py` mirrors `resona-cloud-stt`: `CloudTTSError` (base),
  `MissingAPIKeyError`, `ProviderHTTPError` (HTTP status + provider body).
  Small duplication with `resona-cloud-stt`'s errors is accepted to keep each
  package self-contained.

### 5.1 Provider REST details

**OpenAI** — `POST https://api.openai.com/v1/audio/speech`
- Header `Authorization: Bearer <key>`; JSON body `{model, input, voice,
  response_format, speed}`.
- Whitelisted `options`: `speed`. Response body is the raw audio; `content_type`
  derived from `response_format` (`mp3`→`audio/mpeg`, `wav`→`audio/wav`,
  `opus`→`audio/opus`, `aac`→`audio/aac`, `flac`→`audio/flac`).

**ElevenLabs** — `POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}`
- Header `xi-api-key: <key>`; JSON body `{text, model_id, voice_settings?}`.
- `voice` is the ElevenLabs voice id (path param). `response_format` maps to the
  `output_format` query param (`mp3`→`mp3_44100_128`, `wav`→`pcm_44100` is not
  a true wav — restrict supported formats to `mp3` and `opus` for ElevenLabs;
  unsupported format → `CloudTTSError`).
- Whitelisted `options`: `stability`, `similarity_boost`, `style` (folded into
  `voice_settings`).

**Deepgram** — `POST https://api.deepgram.com/v1/speak`
- Header `Authorization: Token <key>`; query params `model`, `encoding`/
  `container` derived from `response_format`; JSON body `{text}`.
- The Deepgram voice is part of the model name (`aura-2-thalia-en`); a separate
  `voice` argument, if given, overrides the model. Whitelisted `options`:
  `sample_rate`.

When a provider cannot honour a requested `response_format`, it raises
`CloudTTSError` with a clear message rather than silently substituting.

## 6. The engine registry

`engine_registry.py` builds and serves the catalogue of engines this
deployment exposes, and resolves a request to a concrete handler.

### 6.1 Engine catalogue

An engine is described by:

```python
@dataclass
class EngineInfo:
    name: str                 # "faster-whisper", "deepgram", ...
    kind: str                 # "local" | "cloud"
    capabilities: list[str]   # subset of ["stt", "tts"]
    private: bool             # local → True; cloud → False
    available: bool           # local → /health reachable; cloud → key set
    models: list[str]         # advertised models
    url: str | None           # local: engine-server URL; cloud: None
    provider: str | None      # cloud: provider name; local: None
```

**Local engines** — one per URL in `RESONA_ENGINE_URLS` (comma-separated).
At resolution time the gateway probes each URL's `/health`:
- reachable → `available=True`, `name`/`models` taken from the `/health` body,
  `kind="local"`, `capabilities=["stt"]`, `private=True`.
- unreachable → still listed with `available=False` so `/v1/engines` shows it;
  `name` falls back to the URL.
- **Name collision** (two URLs report the same `engine`): keep the first, log a
  warning naming both URLs, skip the duplicate.

**Cloud engines** — `deepgram`, `openai`, `elevenlabs`. An engine is
`available=True` purely because its API-key env var is set. `capabilities` is
the union of STT (always, via `resona-cloud-stt`) and TTS (via
`resona-cloud-tts`) — since one key enables both, an available cloud engine has
`capabilities=["stt","tts"]`. `kind="cloud"`, `private=False`.

`/health` probe results are cached briefly (a few seconds TTL) so a burst of
requests does not hammer every backend; `/v1/engines` always probes fresh.

### 6.2 Resolution

```python
def resolve(engine: str | None, capability: str, private: bool) -> EngineInfo
```

1. **`private=true`** → drop every engine with `private=False` (all cloud)
   from the candidate set first.
2. **`engine` given** →
   - unknown name → `EngineNotFoundError` → HTTP `400`.
   - found but `available=False` → `EngineUnavailableError` → HTTP `503`.
   - found but does not support `capability` → `400`
     (e.g. a local engine asked for `tts`).
   - found but `private=true` filtered it out (cloud engine under `private`) →
     `PrivacyViolationError` → HTTP `400`, message
     `"engine 'X' is not private — refused under private=true"`. Raised
     **before** the upload is forwarded.
   - `engine == "local"` is an alias → resolves to the default local engine.
3. **`engine` omitted** → `RESONA_DEFAULT_ENGINE`, if set and it satisfies
   `capability` + `private`; otherwise the first `available` candidate
   supporting `capability` (local engines preferred over cloud). None → `409`
   (`"no engine available"`; under `private`, `"no private engine available"`).

`private=true` keeps **all** local engines as candidates — multiplicity does
not weaken the guarantee.

## 7. Endpoints

All new routes live on `resona-api` in `audio_routes.py`. Existing `/jobs`,
`/replacements`, `/prompts` routes are unchanged. Auth is the existing optional
`RESONA_API_KEY` dependency (Keycloak is subsystem D).

### 7.1 `POST /v1/audio/transcriptions` — synchronous STT

`multipart/form-data`:

| Field | Type | Notes |
|-------|------|-------|
| `file` | file | required, audio |
| `model` | str | optional; model within the engine |
| `language` | str | optional; default `de` |
| `prompt` | str | optional; initial prompt |
| `temperature` | float | optional |
| `response_format` | str | `json` (default), `text`, `verbose_json` |
| `engine` | str | Resona extension; engine name or `local` |
| `private` | bool | Resona extension; default `false` |

Flow: validate file → `registry.resolve(engine, "stt", private)` → save upload
to a temp path → dispatch:
- **local** → `EngineClient` for that engine's URL → `POST /transcribe`.
- **cloud** → `resona_cloud_stt.get_provider(name).transcribe(...)` with the key
  resolved from the provider's env var.
→ run the existing postprocessing pipeline (DB replacements) on the text →
shape the response.

Responses:
- `json` → `{"text": "..."}`
- `text` → `text/plain` body
- `verbose_json` → `{"text", "language", "duration", "segments": [{start,end,
  text}]}`

### 7.2 `POST /v1/audio/speech` — synchronous TTS

`application/json`:

| Field | Type | Notes |
|-------|------|-------|
| `model` | str | optional; provider model |
| `input` | str | required; text to synthesize |
| `voice` | str | optional; provider voice |
| `response_format` | str | `mp3` (default), `opus`, `wav`, `aac`, `flac` (provider-dependent) |
| `speed` | float | optional |
| `engine` | str | Resona extension; cloud engine name |
| `private` | bool | Resona extension; default `false` |

Flow: `registry.resolve(engine, "tts", private)` → `resona_cloud_tts` provider
→ return a `StreamingResponse` of the audio bytes with the `SpeechResult`
content type.

Because there is no local TTS engine, `private=true` on this route always
resolves to "no private engine available" → **`409`**. This falls out of the
registry rules with no special-casing.

### 7.3 `GET /v1/engines` — discovery

No auth-sensitive payload; still behind `RESONA_API_KEY` if configured. Probes
all backends fresh and returns the catalogue:

```json
{
  "engines": [
    {"name": "faster-whisper", "kind": "local", "capabilities": ["stt"],
     "private": true,  "available": true,  "models": ["large-v3"]},
    {"name": "whisper", "kind": "local", "capabilities": ["stt"],
     "private": true,  "available": false, "models": []},
    {"name": "deepgram", "kind": "cloud", "capabilities": ["stt","tts"],
     "private": false, "available": true,  "models": ["nova-3"]}
  ],
  "default": "faster-whisper"
}
```

`url` and `provider` are omitted from the public payload.

## 8. `engine-server` change

`GET /health` changes from `{"status": "ok"}` to:

```json
{"status": "ok", "engine": "faster-whisper", "models": ["large-v3"]}
```

`engine` is the value of `RESONA_ENGINE`. `models` is the engine's configured
model (a one-element list, from `DEFAULT_FASTWHISPER_MODEL` etc.). No persistent
state is introduced. The Docker healthcheck still passes (it only checks HTTP
200). This is the only change to `engine-server` in this spec.

## 9. Async job flow

`tasks_transcribe.py` is rewired to route through the engine registry instead
of its current `RESONA_CLOUD_ENGINE` branch:

- The background `TranscribeTask` resolves its engine via
  `registry.resolve(engine=<job engine or None>, capability="stt",
  private=False)` and dispatches the same way as `/v1/audio/transcriptions`.
- The `Job` model gains an optional `engine: str | None` column so a queued job
  can pin an engine; `POST /jobs` gains an optional `engine` form field.
  Existing rows default to `None` (→ `RESONA_DEFAULT_ENGINE`).
- `_cloud_transcribe()` and the `RESONA_CLOUD_*` env reads are deleted.

Postprocessing after transcription is unchanged.

## 10. Configuration

New / changed environment variables (read with `python-decouple`'s `config()`):

| Variable | Package | Purpose | Default |
|----------|---------|---------|---------|
| `RESONA_ENGINE_URLS` | api | Comma-separated local `engine-server` URLs | `http://localhost:7001` |
| `RESONA_DEFAULT_ENGINE` | api | Default engine name when a request omits `engine` | (first available) |
| `OPENAI_API_KEY` | cloud-stt, cloud-tts | Enables OpenAI STT **and** TTS | (none) |
| `ELEVENLABS_API_KEY` | cloud-stt, cloud-tts | Enables ElevenLabs STT **and** TTS | (none) |
| `DEEPGRAM_API_KEY` | cloud-stt, cloud-tts | Enables Deepgram STT **and** TTS | (none) |

**Removed:** `RESONA_ENGINE_URL` (→ `RESONA_ENGINE_URLS`), `RESONA_CLOUD_ENGINE`,
`RESONA_CLOUD_MODEL`, `RESONA_CLOUD_OPTIONS`.

## 11. Docker

`docker-compose.resona.yml` is restructured:

- Three engine services — `engine-faster-whisper`, `engine-whisper`,
  `engine-voxtral` — each built from its package Dockerfile, each with
  `profiles: ["<engine-name>"]` and its own container hostname on `:7001`.
- `resona-api` has no profile (always runs); its `RESONA_ENGINE_URLS` lists all
  three internal hostnames. Engines not started simply show
  `available: false` in `/v1/engines`.
- `depends_on` is relaxed — the gateway no longer hard-depends on one engine
  being healthy; it tolerates absent backends.

Usage:

```bash
docker compose -f docker-compose.resona.yml --profile faster-whisper up
docker compose -f docker-compose.resona.yml \
  --profile faster-whisper --profile whisper up   # two local backends
```

Cloud engines need no container — only their API keys in `.env`.

## 12. Error handling

| Condition | HTTP | Error type |
|-----------|------|-----------|
| Unknown `engine` name | `400` | `EngineNotFoundError` |
| `engine` known but supports neither the requested capability | `400` | `CapabilityError` |
| Cloud `engine` named under `private=true` | `400` | `PrivacyViolationError` (before upload forwarded) |
| `engine` resolved but `available=False` | `503` | `EngineUnavailableError` |
| No engine available at all | `409` | `NoEngineError` |
| Missing cloud API key for a resolved cloud engine | `503` | `MissingAPIKeyError` |
| Provider non-2xx | `502` | `ProviderHTTPError` (status + provider body) |
| Unsupported `response_format` for a provider | `400` | `CloudTTSError` |
| Unsupported audio file type | `415` | (existing `validate_audio_file`) |

A FastAPI exception handler maps the typed errors to these responses with a
JSON `{"detail": "..."}` body.

## 13. Testing

- **`resona-cloud-tts`** — per-provider `respx` tests: assert request shape
  (auth header, body, query params, `options` whitelisting) and response →
  `SpeechResult`; cover `401` and `400`. `test_registry.py` covers
  `get_provider()`, env-key lookup, default models/voices.
- **`engine_registry`** — catalogue construction from `RESONA_ENGINE_URLS`
  (mock `/health`), cloud availability by key presence, name-collision handling,
  unreachable backend listed as `available=False`; `resolve()` for: explicit
  engine, default, `private` filtering, the privacy-violation refusal, capability
  mismatch, no-engine-available.
- **Endpoints** — FastAPI `TestClient` tests for `/v1/audio/transcriptions`,
  `/v1/audio/speech`, `/v1/engines` with `engine-server` (`respx`) and the cloud
  packages mocked: response-format variants, `engine`/`private` routing,
  the `409` on private TTS, error mapping.
- **`engine-server`** — `/health` returns `engine` + `models`.
- **`tasks_transcribe`** — async job routes through the registry; `Job.engine`
  pinning honoured.
- Full existing suite stays green.

## 14. Docs

- `CLAUDE.md` — gateway role of `resona-api`; the `/v1/audio/*` + `/v1/engines`
  routes; the `resona-cloud-tts` package and its table entry; the engine
  registry; the env-var table changes (`RESONA_ENGINE_URLS`,
  `RESONA_DEFAULT_ENGINE`, removed `RESONA_CLOUD_*`); the new `/health` shape.
- `README.md` — running multiple local backends; the OpenAI-compatible API;
  cloud STT/TTS via env keys; the Compose profile usage.
- `docs/**` — architecture page updated for the gateway + multi-backend model.

## 15. Out of scope

- Transcription logging, encrypted audio retention, corrected-transcript upload
  (subsystem C).
- Keycloak / OIDC authentication (subsystem D).
- Local TTS synthesis engines.
- Streaming `/v1/audio/*` (the existing `WS /ws/live` stays local-engine only).
- Cloud engines as `resona.engines` entry points or inside `engine-server`
  (`engine-server` stays local/GPU only).
- Load-balancing several `engine-server` instances of the *same* engine.
- Storing API keys anywhere but environment variables.
