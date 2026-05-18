# Design: MkDocs Full Rewrite + GitHub Pages Deployment

**Date:** 2026-05-17  
**Status:** Approved  
**Scope:** Full rewrite of all documentation, mkdocs.yml overhaul, GitHub Actions deploy workflow, README.md slim-down, CLAUDE.md cleanup.

---

## 1. Goals

- Replace all stale docs (ws-* references, wrong commands, missing features) with accurate content sourced from CLAUDE.md and the codebase.
- Add documentation for features currently undocumented: cloud TTS, unified `/v1/engines` API, dev workflow, privacy flag, postprocessing pipeline.
- Set up GitHub Actions to deploy to GitHub Pages (`gh-pages` branch) on every push to `main` that touches docs.
- Slim README.md to ~60 lines; redirect detail to the docs site.
- Clean up CLAUDE.md: remove tables duplicated in the public docs site; keep only dev-workflow-specific content.

---

## 2. Navigation Structure (27 pages)

```
Home                              docs/index.md
Getting Started
  Installation                    docs/getting-started/installation.md
  Quick Start                     docs/getting-started/quick-start.md
  Local-Only Mode                 docs/getting-started/local-only.md
User Guide
  CLI Reference                   docs/guide/cli.md
  Engine Selection                docs/guide/engines.md
  Text Replacements               docs/guide/replacements.md
  Postprocessing                  docs/guide/postprocessing.md
  Speech Synthesis (TTS)          docs/guide/tts.md
  Privacy                         docs/guide/privacy.md
Server Setup
  Docker Deployment               docs/server/docker.md
  Full-Stack (uv)                 docs/server/full-stack.md
Configuration
  Environment Variables           docs/configuration/environment.md
  Config Files                    docs/configuration/config-files.md
Architecture
  Overview                        docs/architecture/overview.md
  Stateless Engine Contract       docs/architecture/engine-contract.md
  Job Lifecycle                   docs/architecture/job-lifecycle.md
  Postprocessing Pipeline         docs/architecture/postprocessing.md
API Reference
  resona-client                   docs/reference/client.md
  resona-api                      docs/reference/api.md
  resona-engine-server            docs/reference/engine-server.md
  resona-asr-core                 docs/reference/asr-core.md
  resona-cloud-stt                docs/reference/cloud-stt.md
  resona-cloud-tts                docs/reference/cloud-tts.md
  resona-postprocess              docs/reference/postprocess.md
Development
  Dev Workflow                    docs/development/workflow.md
  Adding an Engine                docs/development/adding-engine.md
  Testing                         docs/development/testing.md
```

**Files removed** (stale): `docs/onboarding.md`, `docs/reference/ws-api.md`, `docs/reference/engine.md`, `docs/configuration/engines.md`.

---

## 3. mkdocs.yml Changes

### Theme additions
```yaml
theme:
  name: material
  features:
    - navigation.tabs
    - navigation.sections
    - navigation.indexes     # section folder pages (e.g. getting-started/index)
    - navigation.instant
    - navigation.top
    - navigation.footer
    - content.code.copy
    - content.code.annotate
    - toc.follow
  repo_url: https://github.com/ecsplico/resona
  repo_name: ecsplico/resona
  edit_uri: edit/main/docs/
```

### mkdocstrings paths — remove stale entries
Remove: `packages/ws-client/src`, `packages/ws-api/src`, `packages/ws-engine/src`, `apps/cli/src`.  
Keep: all 10 live package `src/` paths.

### Remove broken-link suppression
Remove `validation.links.not_found: ignore` — links will be correct.

---

## 4. GitHub Actions Workflow

File: `.github/workflows/deploy-docs.yml`

```yaml
name: Deploy docs to GitHub Pages
on:
  push:
    branches: [main]
    paths:
      - "docs/**"
      - "mkdocs.yml"
      - ".github/workflows/deploy-docs.yml"
  workflow_dispatch:

permissions:
  contents: write

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0       # needed for git-revision-date if added later
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install uv
      - run: uv sync --all-packages --no-build-isolation-package openai-whisper
      - run: uv run mkdocs gh-deploy --force
```

The `gh-pages` branch is auto-created on first deploy. GitHub Pages must be configured in repo Settings → Pages → Source: Deploy from branch `gh-pages`.

---

## 5. Content Approach Per Section

### Home (`index.md`)
- Elevator pitch (2 sentences)
- ASCII architecture diagram (updated from CLAUDE.md)
- Package table
- Three quick-start paths (local-only, Docker, cloud)
- Links to Getting Started

### Getting Started
- **installation.md**: prerequisites (Python 3.12+, uv, ffmpeg, CUDA GPU), `uv sync --all-packages`, install personas table, note on PyTorch extras
- **quick-start.md**: transcribe a file in under 2 minutes using local-only mode; annotated terminal output
- **local-only.md**: in-process engine fallback, how spawning works, output files, `--engine` flag, `--output-dir`

### User Guide
- **cli.md**: every subcommand (`transcribe`, `watch`, `rec`, `live`, `ui`, `speech`, `engines`, `replacements`, `prompts`) with all flags; generated from CLAUDE.md authoritative list
- **engines.md**: built-in local engines table, cloud providers table, `--engine` resolution order, `config.json` entries, auto-activation of cloud engines via API key
- **replacements.md**: default German dictation rules, `~/.resona/replacements.json` override, CRUD via CLI
- **postprocessing.md**: pipeline steps (replacements → LLM), `postprocess.json` format, litellm model strings, `RESONA_LLM_MODEL` / `RESONA_LLM_API_BASE`
- **tts.md**: `resona speech` command, cloud TTS providers table (voices, formats, env vars), `POST /v1/audio/speech`
- **privacy.md**: `--private` / `--no-private`, `default_private` in config.json, which engines are private, cloud engines are never private

### Server Setup
- **docker.md**: `docker compose -f docker-compose.resona.yml up -d`, GPU requirements, health checks, env var wiring
- **full-stack.md**: `uv run resona-engine-faster-whisper` + `uv run resona-api`, `RESONA_ENGINE_URLS` for multi-engine, `RESONA_DEFAULT_ENGINE`

### Configuration
- **environment.md**: complete table of all env vars (from CLAUDE.md), grouped by service
- **config-files.md**: `~/.resona/config.json` schema, `replacements.json`, `postprocess.json`; annotated examples

### Architecture
- **overview.md**: updated ASCII diagram + package table with roles and GPU requirements
- **engine-contract.md**: what is/isn't allowed in engine packages; the "can this be done with only what's in the HTTP request?" test; entry-point discovery
- **job-lifecycle.md**: full annotated flow diagram (client → api → engine → postprocess → client)
- **postprocessing.md**: `PostprocessPipeline` internals, step types, `build_pipeline_from_config()`, default replacements

### API Reference
Each page: handwritten overview → mkdocstrings `:::` block for key public symbols.
- **client.md**: `ResonaClient` methods, `EngineConfig`, `EngineEntry`, `resolve_engine()`
- **api.md**: all REST endpoints with request/response shapes; job lifecycle states; OpenAI-compatible audio routes
- **engine-server.md**: `POST /transcribe`, `WS /ws/transcribe`, `WS /ws/live` protocols; `/health` response; auth
- **asr-core.md**: `Transcriber` protocol, `TranscriptionResult`, `load_audio()`, `get_transcriber()`, `SAMPLE_RATE`
- **cloud-stt.md**: `PROVIDERS`, `PROVIDER_ENV_KEYS`, `DEFAULT_MODELS`, `get_provider()`, error hierarchy
- **cloud-tts.md**: `PROVIDERS`, `PROVIDER_ENV_KEYS`, `DEFAULT_VOICES`, `CONTENT_TYPES`, `get_provider()`, error hierarchy
- **postprocess.md**: `PostprocessPipeline`, `apply_replacements()`, `llm_postprocess()`, `build_pipeline_from_config()`

### Development
- **workflow.md**: `uv sync --all-packages` → editable installs → `uv run resona`; why `uv tool install` is for end-users; reinstall cadence
- **adding-engine.md**: 7-step guide from CLAUDE.md (create package, implement class, register entry point, etc.)
- **testing.md**: pytest commands, mocking strategy per package, audio fixtures, `CliRunner` for CLI tests

---

## 6. README.md Slim-Down

Target: ~60 lines. Content:
- One-paragraph description
- Architecture diagram (keep — useful for GitHub landing page)
- Package table (keep — quick orientation)
- Three install paths (local-only one-liner, Docker one-liner, link to full docs)
- Link to docs site: `https://ecsplico.github.io/resona/`
- License line

Remove: full CLI usage section, text replacements section, engine selection deep-dive, configuration tables, API reference, development section — all now live in the docs site.

---

## 7. CLAUDE.md Cleanup

Remove or abbreviate:
- Large env var table (lives in `configuration/environment.md`)
- Per-package responsibility summaries (lives in `architecture/` + `reference/`)
- Install personas table (lives in `getting-started/installation.md`)

Keep:
- Project structure diagram (dev-specific, not public docs)
- Import conventions
- Mocking strategy (dev-specific)
- Docker section (build context, Dockerfile rules)
- What NOT to do list
- Running in development section (editable vs copied — just added)

---

## 8. Files Changed

| Action | Path |
|---|---|
| Rewrite | `mkdocs.yml` |
| Rewrite | `docs/index.md` |
| Delete | `docs/onboarding.md` |
| Rewrite | `docs/getting-started.md` → split into `getting-started/` |
| Delete | `docs/cli.md` → replaced by `guide/cli.md` |
| Delete | `docs/architecture.md` → replaced by `architecture/` |
| Delete | `docs/configuration/engines.md` |
| Rewrite | `docs/configuration/environment.md` |
| Add | `docs/configuration/config-files.md` |
| Delete | `docs/reference/ws-api.md` |
| Delete | `docs/reference/engine.md` |
| Rewrite | `docs/reference/client.md` |
| Add | `docs/reference/api.md`, `engine-server.md`, `asr-core.md`, `cloud-stt.md`, `cloud-tts.md`, `postprocess.md` |
| Add | `docs/guide/` (6 files) |
| Add | `docs/server/` (2 files) |
| Add | `docs/architecture/` (4 files) |
| Add | `docs/development/` (3 files) |
| Add | `.github/workflows/deploy-docs.yml` |
| Rewrite | `README.md` |
| Update | `CLAUDE.md` |

Total: ~35 file operations.
