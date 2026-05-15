# Cloud Transcription Engines + Private Engines — Design

**Date:** 2026-05-15

**Goal:** Add three cloud speech-to-text engines (Deepgram, ElevenLabs, OpenAI)
usable from both the `resona` CLI and `resona-api`; introduce a `private`
classification with a `--private` flag so sensitive audio stays on
user-controlled infrastructure; unify transcription-target selection under a
single `--engine` argument; and rename the "backend" concept to "engine"
repo-wide.

---

## 1. Background — current state

resona has two unrelated things both called "backend":

- **Engine backends** (`faster-whisper`, `whisper`, `voxtral`) — ASR plugins
  discovered via the `resona.backends` entry-point group, selected by the
  `--backend` CLI flag or the `RESONA_BACKEND` env var. Run locally
  (`InProcessEngine`/`LocalEngine`) or inside `resona-engine-server`.
- **Config backends** (`~/.resona/config.json`) — remote `resona-api` servers
  the CLI submits jobs to, modelled by `BackendEntry`/`BackendConfig` in
  `resona_client/config.py` and resolved by `resolve_backend()`.

This design adds a third kind of transcription target — third-party cloud STT
APIs — and unifies all three under one vocabulary: **engine**.

## 2. Terminology after this design

An **engine** is any transcription target. There are three kinds:

| Kind     | Examples                            | Source                                  | Private |
|----------|-------------------------------------|-----------------------------------------|---------|
| `local`  | `faster-whisper`, `whisper`, `voxtral` | `resona.engines` entry points (always present) | always  |
| `cloud`  | a config entry, `type: cloud`       | `config.json` engines list              | never   |
| `server` | a config entry, `type: resona-api`  | `config.json` engines list              | when marked `private: true` |

## 3. Implementation split

This is delivered as **one design, two implementation plans**:

- **Plan 1 — the rename.** Mechanical, behavior-preserving `backend` → `engine`
  refactor across the repo. The test suite stays green throughout. Lands first
  as a clean foundation.
- **Plan 2 — cloud engines + `private`.** New functionality, built on the
  renamed foundation.

Each plan gets its own file under `docs/superpowers/plans/`.

---

## Plan 1 — `backend` → `engine` rename

Pure refactor. No behavior change. Every change below is a rename; the test
suite must pass identically before and after.

### 1.1 Entry-point group

- `resona.backends` → `resona.engines` in every engine package's
  `pyproject.toml`: `engine-faster-whisper`, `engine-whisper`, `engine-voxtral`.
- `resona_asr_core/registry.py` — discover entry points from the
  `resona.engines` group.

### 1.2 Environment variable

- `RESONA_BACKEND` → `RESONA_ENGINE` (engine selection in `resona-engine-server`
  / `resona-asr-core/registry.py`).
- Update `.env.example`, `docker-compose.resona.yml`, and any Dockerfile/compose
  env references.

### 1.3 resona-client config module

- `BackendEntry` → `EngineEntry`; `BackendConfig` → `EngineConfig`.
- `resolve_backend()` → `resolve_engine()`.
- `config.json` top-level key `backends` → `engines`; `default_backend` →
  `default_engine`. **Backward compatible:** `EngineConfig.load()` reads the new
  `engines` key, and falls back to a legacy `backends` key if present (so
  existing `~/.resona/config.json` files keep working). On `save()`, only the
  new keys are written.

### 1.4 resona-cli

- `apps/resona-cli/src/resona_cli/backends.py` → `engines.py`.
- `resona backends` subcommand → `resona engines` (`list`/`add`/`remove`/`test`).
- The `--backend` flag on `transcribe` → `--engine` (see Plan 2 for its expanded
  meaning; in Plan 1 it keeps today's "local engine plugin" meaning, just
  renamed).
- `InProcessEngine(backend=...)` keyword → `engine=...`.

### 1.5 Tests

- `apps/resona-cli/tests/test_backends.py` → `test_engines.py`; update the
  `isolated_config` fixture's patched symbols to the renamed paths.
- Update every other test referencing renamed symbols, the entry-point group,
  or the env var.

### 1.6 Docs

- `CLAUDE.md`, `README.md`, `docs/**` — replace "backend" with "engine" where it
  refers to the renamed concept; update env-var tables, the entry-point example,
  the "How to add a new transcription backend" section, and install personas.

### 1.7 Verification

`uv run pytest` — all tests green, same count as before the rename.

---

## Plan 2 — Cloud engines + `private`

### 2.1 New package: `resona-cloud-stt`

Lean, `httpx`-only package (no torch, no GPU). src-layout under
`packages/cloud-stt/`, module `resona_cloud_stt`.

```
packages/cloud-stt/
  pyproject.toml                 # deps: httpx. Workspace member.
  src/resona_cloud_stt/
    __init__.py
    types.py        # TranscriptionResult TypedDict: {text, language, segments}
    errors.py       # CloudSTTError, MissingAPIKeyError, ProviderHTTPError
    registry.py     # PROVIDERS, PROVIDER_ENV_KEYS, DEFAULT_MODELS, get_provider()
    providers/
      __init__.py
      deepgram.py
      elevenlabs.py
      openai.py
  tests/
    fixtures/        # small response JSON samples
    test_deepgram.py
    test_elevenlabs.py
    test_openai.py
    test_registry.py
```

`registry.py` constants:

```python
PROVIDER_ENV_KEYS = {
    "deepgram":   "DEEPGRAM_API_KEY",
    "elevenlabs": "ELEVENLABS_API_KEY",
    "openai":     "OPENAI_API_KEY",
}
DEFAULT_MODELS = {
    "deepgram":   "nova-3",
    "elevenlabs": "scribe_v1",
    "openai":     "whisper-1",
}
PROVIDERS = {"deepgram", "elevenlabs", "openai"}
```

Every provider module exposes the **same** function:

```python
def transcribe(
    audio_path: Path,
    *,
    api_key: str,
    model: str | None = None,
    language: str | None = None,
    options: dict | None = None,
) -> TranscriptionResult: ...
```

- The package **never reads env vars** — the caller resolves the API key and
  passes it in. This keeps the package pure and trivially testable with `respx`.
- `options` is a free-form dict; each provider whitelists the keys it
  understands and **drops unknown keys with a `logging.warning`** rather than
  forwarding them (avoids provider `400`s).
- Output is normalized to `TranscriptionResult` = `{text, language, segments}`,
  `segments` being a list of `{start, end, text}`.

### 2.2 Provider REST details

**Deepgram** — `POST https://api.deepgram.com/v1/listen`
- Header `Authorization: Token <key>`; body = raw audio bytes with the file's
  `Content-Type`.
- Query params: `model`, `language` (omitted when `None`), plus whitelisted
  `options`: `smart_format`, `diarize`, `punctuate`, `numerals`.
- Response: `text` from `results.channels[0].alternatives[0].transcript`.
  `segments` = one segment spanning the transcript, `start`/`end` taken from the
  first/last entry of the `words` array (empty list → `0.0`). `language` echoes
  the request (or `""` if not given).

**ElevenLabs** — `POST https://api.elevenlabs.io/v1/speech-to-text`
- Header `xi-api-key: <key>`; `multipart/form-data` with `file`, `model_id`
  (the resolved model, default `scribe_v1`), `language_code` (when `language`
  set), plus whitelisted `options`: `diarize`, `num_speakers`,
  `tag_audio_events`.
- Response: `text` from `text`; `language` from `language_code`; `segments` =
  one segment spanning the text, `start`/`end` from first/last `words` entry.

**OpenAI** — `POST https://api.openai.com/v1/audio/transcriptions`
- Header `Authorization: Bearer <key>`; `multipart/form-data` with `file`,
  `model` (default `whisper-1`), `response_format=verbose_json`, `language`
  (when set), plus whitelisted `options`: `prompt`, `temperature`.
- Response (`verbose_json`): `text`, `language`, and `segments` mapped directly
  from the response `segments` array (`start`, `end`, `text`).

### 2.3 `EngineEntry` schema (cloud fields)

`EngineEntry` (renamed from `BackendEntry` in Plan 1) gains five optional
fields. Existing config files keep working — missing keys fall back to
dataclass defaults.

```python
type:     str           = "resona-api"   # "resona-api" | "cloud"
provider: str | None    = None           # cloud: "deepgram"|"elevenlabs"|"openai"
model:    str | None    = None           # provider model override
private:  bool          = False          # resona-api entries: user-asserted
options:  dict          = field(default_factory=dict)  # forwarded to provider
```

- `api_url` becomes optional (`= ""`) — `cloud` entries have none.
- Methods:
  - `is_private()` → `False` for any `cloud` entry (the `private` flag is
    ignored there); `self.private` for `resona-api` entries.
  - `is_usable()` → `resona-api`: `/health` reachable (today's behavior);
    `cloud`: the provider's API-key env var is set.
- Validation (on `engines add` and on `EngineConfig.load()`): a `cloud` entry
  must name a `provider` in `PROVIDERS`. Invalid entries are rejected on `add`
  and warned-and-skipped on `load`. An API key is never stored for `cloud`
  entries.

### 2.4 `resolve_engine()` changes

`resolve_engine()` (renamed in Plan 1) gains:

- `name: str | None = None` — pin a specific config entry by name.
- `private_only: bool = False` — skip entries where `is_private()` is `False`.
- `cloud` entries are "usable" when their API-key env var is set (no `/health`
  probe, no auto-start).
- **Hardening:** when an entry has a `compose_dir` that does not exist, log a
  clear warning and skip that entry instead of letting `subprocess.Popen` raise
  a raw `FileNotFoundError` (fixes a pre-existing bug).

### 2.5 CLI — unified `--engine`

`--engine NAME` accepts **any** engine name uniformly:

- a built-in local engine (`faster-whisper`/`whisper`/`voxtral`),
- a `config.json` `cloud` entry,
- a `config.json` `resona-api` entry.

Resolution in `transcribe`:

1. `--engine NAME` given → resolve that name: config entry first, else a
   built-in local engine name. Unknown name → error.
2. Not given → try `config.json` entries in priority order via
   `resolve_engine(private_only=<--private>)`; if none usable, fall back to
   `default_engine` (a local engine, default `faster-whisper`).

Routing by resolved kind:

- `cloud` → new `CloudEngine` (see 2.6).
- `resona-api` → `ResonaClient` (today's path).
- `local` / no entry → local fallback (`InProcessEngine`/`LocalEngine`),
  always private.

`resona engines list` shows the merged view — the three local engines plus
configured entries — each with `type`, privacy, and status:

```
NAME            TYPE    PRIVATE  STATUS
faster-whisper  local   yes      built-in
whisper         local   yes      built-in
voxtral         local   yes      built-in
my-gpu-box      server  yes      reachable
deepgram        cloud   no       key set
```

`resona engines add NAME [API_URL]` gains options: `--type {resona-api,cloud}`,
`--provider`, `--model`, `--private`, and `--option KEY=VALUE` (repeatable,
fills `options`). Adding an entry whose name shadows a built-in local engine is
rejected.

### 2.6 `CloudEngine`

A new class in `resona_cli/engine.py` implementing the existing `Engine`
protocol (`transcribe(audio: Path, **kwargs) -> TranscriptionResult`). Given a
resolved `cloud` `EngineEntry`, it:

1. Resolves the API key from the provider's env var
   (`PROVIDER_ENV_KEYS[provider]`); missing → `MissingAPIKeyError`.
2. Calls `resona_cloud_stt.<provider>.transcribe(...)` with the entry's `model`,
   `language`, and `options`. The `transcribe` command's existing `--model` and
   `--language` flags, when provided, override the entry's values for that run.
3. Returns the `TranscriptionResult`.

### 2.7 `--private` semantics

- **Private:** local engines (always); `resona-api` entries with
  `private: true`.
- **Non-private:** all `cloud` entries; `resona-api` entries left
  `private: false`.
- With `--private`:
  - priority resolution skips non-private entries;
  - an explicit `--engine NAME` naming a non-private entry is a **hard error
    before any audio leaves the machine** —
    `"Engine 'X' is not private — refused under --private"`;
  - falling through to a local engine is allowed.
- `default_private: bool = false` in `config.json` (on `EngineConfig`) makes
  `--private` the implicit default, so a user handling sensitive audio never
  has to type the flag. An explicit `--no-private` overrides it for one run.

### 2.8 resona-api routing

`resona-api`'s `tasks_transcribe.py` today always calls `EngineClient` →
`resona-engine-server`. New env vars (read with `python-decouple`'s `config()`)
let it route to a cloud engine instead:

- `RESONA_CLOUD_ENGINE` — provider name. When set, jobs go to
  `resona-cloud-stt`; when unset, behavior is exactly as today.
- `RESONA_CLOUD_MODEL` — optional model override.
- `RESONA_CLOUD_OPTIONS` — optional JSON object of provider options.
- API key via `config("DEEPGRAM_API_KEY")` / etc.

Postprocessing (replacements + LLM pipeline) still runs locally afterward —
unchanged. `resona-api` adds `resona-cloud-stt` as a workspace dependency. The
`--private` flag is a CLI guard only; `resona-api` simply honors its env config.

### 2.9 Error handling

| Condition | Behavior |
|-----------|----------|
| Missing API key | `MissingAPIKeyError` naming the env var; CLI prints a clean message, exits non-zero |
| Provider non-2xx | `ProviderHTTPError` with HTTP status + provider message body |
| Unknown provider in config | rejected on `engines add`; warned-and-skipped on `load` |
| Unknown `options` key | warned and dropped before the request |
| `--private` violation | hard error before any upload |
| Engine-name collision on `add` | rejected with a clear message |
| Missing `compose_dir` | warn and skip the entry (no raw `FileNotFoundError`) |

### 2.10 Testing

- **`resona-cloud-stt`** — per-provider tests using `respx` to mock the provider
  endpoints: assert request shape (auth header, body/multipart, query params,
  `options` mapping) and response parsing → `TranscriptionResult`; cover `401`
  (bad key) and `400` (bad request). `test_registry.py` covers
  `get_provider()`, env-key lookup, default models.
- **`EngineConfig`** — new fields, `is_private()`, `is_usable()`,
  `resolve_engine(name=…, private_only=…)`, name pinning, legacy `backends`-key
  read, missing-`compose_dir` skip.
- **CLI** — `CliRunner` tests: `--engine` pinning a local/cloud/server engine;
  `--private` filtering and the refusal error; `default_private` behavior and
  `--no-private` override; `engines list` merged view; `engines add --type
  cloud` and `--option`; engine-name collision rejection.
- **resona-api** — `tasks_transcribe` routes to `resona-cloud-stt` when
  `RESONA_CLOUD_ENGINE` is set (mock the package); default engine-server path
  unchanged.
- Full existing suite green after the Plan 1 rename and after Plan 2.

### 2.11 Docs

- `README.md` / `CLAUDE.md` — document the three cloud engines, the env-var API
  keys, the `--engine`/`--private` flags, `default_private`, the new
  `resona-cloud-stt` package, and the `RESONA_CLOUD_*` resona-api env vars.
- Add `resona-cloud-stt` to the package table and the workspace layout.

---

## Out of scope

- Streaming/live transcription via cloud providers (`WS /ws/live` stays
  local-engine only).
- Cloud engines as `resona.engines` entry points / inside
  `resona-engine-server` (engine-server stays local/GPU only).
- Storing API keys in `config.json` (keys are always env-only).
- A secrets manager / keyring integration.
