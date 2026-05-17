# Postprocessing Profiles — Design

**Date:** 2026-05-17
**Status:** Approved (design); pending implementation plan

## Problem

Resona's `resona-postprocess` package has a working composable pipeline
(`PostprocessPipeline`, `apply_replacements`, `llm_postprocess`,
`build_pipeline_from_config`), but LLM postprocessing is effectively
unreachable in normal operation:

| Path | What it runs | LLM steps? |
|------|--------------|------------|
| `tasks_transcribe.py` (async `/jobs` queue) | inline pipeline, DB replacements only | no |
| `/v1/audio/transcriptions` (sync route) | `apply_replacements()` only | no |
| CLI `transcribe` → gateway | server-side (replacements only) | no |
| CLI `transcribe` → local fallback | `build_pipeline_from_config()` | yes — only here |

`llm_postprocess()` only runs when no server is reachable *and* the user has
hand-written `~/.resona/postprocess.json`. There is no DB model, REST endpoint,
or CLI surface for LLM postprocessing. Configuration is fragmented: regex
replacements and Whisper initial prompts each live in their own DB table
(`Replacement`, `InitialPrompt`), with no notion of a use case (discharge
letter vs. SOAP note vs. raw cleanup) that would group the right prompt and the
right postprocessing together.

## Goal

Introduce **profiles**: named, use-case-specific configuration bundles that
carry the Whisper `initial_prompt` *and* an ordered chain of postprocessing
steps. Profiles are flat JSON files in a unified format, reachable from the
async `/jobs` API and both CLI paths, and submittable inline to the API.

## Scope

In scope:

- A unified profile file format and the `Profile` abstraction in
  `resona-postprocess`.
- Profile-driven postprocessing in the async `/jobs` worker and both CLI paths
  (gateway and local fallback), unified on one code path.
- Full profile support on the sync `/v1/audio/transcriptions` route.
- Flat-file profile storage with thin file-CRUD REST endpoints.
- Removal of the `Replacement` and `InitialPrompt` DB tables and their
  REST/CLI surfaces.
- Hardening of `llm.py`.

Out of scope:

- The `apps/web` browser UI. It is not updated by this work.
- A profile-editing GUI. Profiles are edited as files or via `PUT /profiles`.
- Alembic / formal schema migrations — the project creates tables directly via
  `create_db_and_tables()`.

## Decisions

These were settled during brainstorming and are fixed inputs to the design:

1. **CLI scope:** both CLI paths (gateway and local fallback) are covered and
   unified on one builder.
2. **Step types:** `replacements`, `llm` (text→text), and `extract`
   (prompt-defined free-form JSON extraction).
3. **Replacements are per-profile** — each profile owns its own rules; there is
   no global replacement set.
4. **Structured extraction** is prompt-defined free-form JSON (no schema),
   stored as a JSON column on the `Job` and written as a `.json` sidecar file
   on the file-producing paths.
5. **Storage:** flat files only (Approach A). No config DB table; the API DB
   holds only `Job` rows. Per-`Job` profile snapshotting provides
   reproducibility.
6. **Initial prompts** move into profiles; the `InitialPrompt` table is removed.
7. **Sync route:** `/v1/audio/transcriptions` gets full profile support
   (resolved through the same code path); latency is the caller's opt-in.

## Architecture overview

```
profile JSON file ──┐
inline profile JSON ─┼─> resolve_profile() ──> Profile ──> build_pipeline()
named profile ───────┘                                          │
                                                                 v
                              raw transcript ──> PostprocessPipeline.run()
                                                                 │
                                              PostprocessResult{text, data}
```

`resona-postprocess` owns the profile concept. `resona-api` and `resona-cli`
both consume it; neither reimplements pipeline logic.

## Profile file format

A profile is a single JSON file, `<name>.json`, in a profiles directory. One
format, used identically by the API server, the CLI gateway path, and the CLI
local fallback.

```json
{
  "name": "arztbrief",
  "description": "Discharge-letter formatting with field extraction",
  "initial_prompt": ["Befund", "Diagnose", "Therapie", "Procedere"],
  "steps": [
    {
      "type": "replacements",
      "name": "dictation-commands",
      "rules": [
        {"pattern": "\\bKomma\\b", "replacement": ","},
        {"pattern": "\\bAbsatz\\b", "replacement": "\n\n"}
      ]
    },
    {
      "type": "replacements",
      "name": "house-defaults",
      "source": "default_replacements.json"
    },
    {
      "type": "llm",
      "name": "format",
      "prompt": "Formatiere diesen Befund als strukturierten Arztbrief.",
      "model": "gpt-4o-mini",
      "temperature": 0.2,
      "max_tokens": 4000,
      "api_base": null
    },
    {
      "type": "extract",
      "name": "fields",
      "prompt": "Extrahiere Diagnose, Medikation und Procedere als JSON-Objekt.",
      "model": "gpt-4o-mini"
    }
  ]
}
```

Field semantics:

- **`name`** — profile identifier; should match the filename stem.
- **`description`** — human-readable summary, shown by `GET /profiles` and
  `resona profiles list`.
- **`initial_prompt`** — list of vocabulary-biasing phrases. Joined with `", "`
  and passed to the engine as `initial_prompt`. Replaces the `InitialPrompt`
  table. May be empty or omitted.
- **`steps`** — ordered list. Three step types:
  - **`replacements`** — regex rules applied case-insensitively, in order.
    Rules come from inline `rules` (list of `{pattern, replacement}`) **or** a
    `source` file. Relative `source` paths resolve against the profile file's
    directory; the bundled `default_replacements.json` is referenceable by
    name. Per-profile.
  - **`llm`** — text→text transform. Required `prompt`; optional `model`,
    `temperature`, `max_tokens`, `api_base`, each falling back to the
    `RESONA_LLM_*` env vars when absent.
  - **`extract`** — prompt-defined free-form JSON extraction. Runs on the
    current text and contributes parsed JSON to the result's `data` under the
    step's `name`. Does **not** modify the text chain.
- Every step has an optional `name` (defaults to the step type); `extract`
  steps must have a unique `name` because it keys their output in `data`.

**Field-name compatibility:** the replacements loader accepts both the new
`pattern` key and the legacy `name` key (the current bundled
`default_replacements.json` uses `name` for the regex). Existing
`default_replacements.json` and user `~/.resona/replacements.json` files keep
working untouched.

**Bundled `default` profile:** `resona-postprocess` ships a `default` profile —
the current `default_replacements.json` as a single `replacements` step, no LLM.
Any job or request with no `profile` resolves to it, so behavior is unchanged
for callers that never adopt profiles.

## `resona-postprocess` changes

This package becomes the single home of the profile concept.

### New `profile.py`

- `Profile` dataclass — `name`, `description`, `initial_prompt: list[str]`,
  `steps: list[dict]`.
- `Profile.from_dict(data)` / `Profile.from_file(path)` — parse and validate.
  Validation fails fast at load time, not mid-job: unknown step types, missing
  required fields, uncompilable regex patterns, and duplicate `extract`-step
  `name`s all raise a clear error.
- `resolve_profile(ref, profiles_dir)` — `ref` is a profile name (resolved to
  `profiles_dir/<name>.json`), a filesystem path, or an already-parsed dict
  (inline submission). Returns a `Profile`. A name found as a file in
  `profiles_dir` shadows the bundled profile of the same name; `bundled_default()`
  is used only when no file matches.
- `list_profiles(profiles_dir)` — enumerate available profiles (name +
  description).
- `bundled_default()` — return the shipped `default` profile.

`sources.py`'s file-reading role folds into `profile.py`;
`build_pipeline_from_config` is removed.

### `pipeline.py`

The chain becomes result-carrying. `PostprocessPipeline.run(text)` returns a
`PostprocessResult` instead of a bare `str`:

```python
@dataclass
class PostprocessResult:
    text: str
    data: dict   # keyed by extract-step name
```

- `replacements` and `llm` steps update `result.text`.
- `extract` steps run on the current `result.text` and merge their parsed JSON
  into `result.data[step_name]`.
- `build_pipeline(profile: Profile) -> PostprocessPipeline` builds the pipeline
  directly from a `Profile`.

### `llm.py` hardening

LLM steps now run server-side, so robustness matters:

- Clean, explicit error when `litellm` is missing (not an `AttributeError`).
- Wire through `temperature`, `max_tokens`, `timeout`, and `api_base` (the
  current `sources.py` step builder drops `api_base`).
- `extract` calls use litellm JSON mode; output is parsed best-effort. On parse
  failure the raw string is preserved under `data[step_name]["_raw"]` so a job
  never hard-fails on a malformed LLM response.
- One retry on transient errors; log model and token usage.

### Failure policy

A failing `llm` or `extract` step is logged and skipped — the job still
completes with the best text available, and `data` records the skipped step.
Replacements already skip individually-bad patterns. A job fails only on
non-postprocessing errors (missing audio, engine failure).

## `resona-api` changes

### DB model (`db/models.py`)

- Delete the `Replacement` and `InitialPrompt` tables.
- `Job` gains three fields:
  - `profile: Optional[str]` — the profile name/ref requested (`"default"` when
    none given).
  - `profile_config: Optional[str]` — JSON snapshot of the **resolved** profile
    the job actually ran with (reproducibility / audit).
  - `structured: Optional[str]` — JSON string of the merged `extract` output.
- `db/utils.py` loses `get_active_replacements()` and
  `get_active_initial_prompts_string()`.
- `db/presets.py` loses replacement/prompt seeding.

### Profiles storage and CRUD

The server reads `RESONA_PROFILES_DIR` (default `~/.resona/profiles/`, resolved
via `config()`). A new thin file-CRUD router:

- `GET /profiles` — list profile names + descriptions.
- `GET /profiles/{name}` — return the profile file JSON.
- `PUT /profiles/{name}` — write/replace a profile file. The body is validated
  via `Profile.from_dict` before writing; 400 on an invalid profile.
- `DELETE /profiles/{name}`.

The old `/replacements/*` and `/prompts/*` routes are removed.

### `POST /jobs`

A new optional `profile` form field. Its value is either:

- a profile **name** — resolved server-side against `RESONA_PROFILES_DIR`; or
- an **inline profile JSON string** — submitted directly (the "same format to
  the API" requirement).

Stored on the job. Absent → bundled `default`.

### `tasks_transcribe.py` (job worker)

- Resolve the job's profile via `resolve_profile`; snapshot the resolved
  profile to `job.profile_config`.
- Pass `profile.initial_prompt` (joined) to the engine — replaces the DB prompt
  lookup.
- Build the pipeline with `build_pipeline(profile)`; run it. `result.text` →
  `job.md`; `result.data` → `job.structured`.
- Write the `.md` file as today; additionally write a `<stem>.json` sidecar
  when `structured` is non-empty.
- The inline replacements snippet (current lines 75–82) is deleted — all
  postprocessing flows through the shared builder.

### `audio_routes.py` — sync route

`/v1/audio/transcriptions` gains an optional `profile` form field (name or
inline JSON), resolved through the same `resolve_profile` + `build_pipeline`
path. It runs the full pipeline including `llm` and `extract` steps. Structured
output is returned in the JSON response body (a `structured` key) rather than
written as a sidecar file. The `get_active_replacements()` call is removed.

## `resona-cli` changes

### `transcribe` command

New `--profile NAME|PATH` option:

- A bare **name** → forwarded to the API as the `profile` field (gateway path),
  or resolved against `~/.resona/profiles/` (local fallback).
- A **path** to a `.json` file → contents submitted **inline** to the API, or
  loaded directly in the local fallback.
- Both paths write the `.txt`/`.md` output and, when the profile produced
  structured data, a `<stem>.json` sidecar.
- The local fallback builds the pipeline with `build_pipeline(profile)` — the
  same code the server runs.

### New `resona profiles` subcommand

Mirrors the `resona replacements` pattern; talks to the API:

- `list` — list server profiles.
- `show NAME` — print a profile.
- `push NAME PATH` — upload a local file (`PUT /profiles/{name}`).
- `pull NAME [PATH]` — download a server profile to a local file.
- `delete NAME`.

### Removed surface

- `resona replacements` and `resona prompts` CLI subcommands are removed —
  their configuration now lives in profile files.
- `resona-client` loses the replacement/prompt CRUD methods and gains
  `list_profiles`, `get_profile`, `put_profile`, `delete_profile`, plus a
  `profile` argument on `submit_job` and `create_transcription`.

### Config

`~/.resona/config.json` (`EngineConfig`) gains an optional `default_profile`.
The local fallback and `transcribe` use it when `--profile` is absent.

## Migration & backward compatibility

- **Existing DB data:** a one-shot startup helper, run on the first launch
  after upgrade, reads the old `Replacement` / `InitialPrompt` tables if they
  exist and exports them to `~/.resona/profiles/default.json` (active
  replacements as a `replacements` step, active initial prompt as
  `initial_prompt`). It then drops the two tables via raw SQL (the SQLModel
  classes no longer exist to call `.__table__.drop()`). If the tables do not
  exist, it is a no-op. The written `default.json` shadows the bundled
  `default` profile, so a migrated install keeps its prior config and a fresh
  install uses the bundled one.
- **Bundled default:** any job/request with no `profile` resolves to the
  shipped `default` profile, so behavior is unchanged for non-adopters.
- **Field-name compatibility:** the replacements loader accepts the legacy
  `name` key as well as `pattern`.
- **Removed REST/CLI surface:** `/replacements/*`, `/prompts/*`, `resona
  replacements`, and `resona prompts` are gone — a breaking change for anyone
  scripting against them. Documented in the changelog and
  `docs/configuration/`, plus CLAUDE.md.

## Testing

Following the existing per-package `tests/` and mocking conventions:

- **resona-postprocess** — `Profile` parse/validate (bad step type, missing
  field, uncompilable regex, legacy `name` key); `build_pipeline` step
  ordering; `PostprocessResult` text vs. `data`; `llm`/`extract` steps with
  `litellm.completion` mocked (success, malformed JSON, transient-error retry,
  missing-litellm).
- **resona-api** — `/profiles` CRUD including invalid-profile 400;
  `POST /jobs` with a named profile and with inline JSON; `tasks_transcribe`
  end-to-end with the engine and LLM mocked, asserting `job.md`,
  `job.structured`, the `profile_config` snapshot, and the `.json` sidecar;
  `/v1/audio/transcriptions` with a `profile`.
- **resona-cli** — `CliRunner` for `resona profiles` subcommands and
  `transcribe --profile` (name vs. path) against a mocked client; local-fallback
  path with a profile file.
- **resona-client** — `respx` for the new profile methods.

## Open questions

None. The design is approved pending the implementation plan.
