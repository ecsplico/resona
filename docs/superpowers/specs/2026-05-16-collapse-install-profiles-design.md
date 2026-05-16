# Collapse resona-cli install profiles into a torch-free default

**Date:** 2026-05-16
**Status:** Approved

## Problem

`resona-cli` ships five install extras — `record`, `live`, `faster-whisper`,
`whisper`, `voxtral`. The base install (`uv tool install --from ./apps/resona-cli
resona-cli`) is a lean HTTP client with no audio capture and no local engine, so
the common "record and transcribe locally" workflow requires remembering the
right extra combination (`[live,faster-whisper]`). All of `record`, `live`, and
`faster-whisper` are torch-free, so there is no dependency-weight reason to keep
them opt-in.

## Goal

Make the default install a fully capable, torch-free tool: record TUIs, live
transcription, and in-process local `faster-whisper` transcription all work out
of the box. Keep only the two torch-pulling engines (`whisper`, `voxtral`) as
opt-in extras.

## Design

### pyproject.toml change

`apps/resona-cli/pyproject.toml` — move the union of the `record`, `live`, and
`faster-whisper` extra dependencies into the base `dependencies`:

```toml
dependencies = [
    "httpx>=0.28.1",
    "typer>=0.15.3",
    "python-dotenv>=1.1.0",
    "python-decouple>=3.8",
    "resona-client",
    "resona-cloud-stt",
    "resona-postprocess",
    "textual>=3.2.0",
    "sounddevice>=0.5.1",
    "soundfile>=0.13.1",
    "numpy>=2.1.3",
    "soxr>=0.5",
    "resona-asr-core",
    "resona-engine-faster-whisper",
]

[project.optional-dependencies]
whisper = ["resona-engine-whisper"]
voxtral = ["resona-engine-voxtral"]
```

- `record`, `live`, `faster-whisper` extras are **removed entirely**. Installing
  `resona-cli[faster-whisper]` (etc.) will fail with "unknown extra" — this is
  intentional; all docs referencing those names are updated in the same change.
- `resona-asr-core` is dropped from the `whisper`/`voxtral` extras: it is now a
  base dependency, and `resona-engine-whisper`/`-voxtral` depend on it
  transitively regardless.
- `[tool.uv.sources]` is unchanged — all seven workspace entries are still
  referenced (`resona-engine-whisper`/`-voxtral` from the extras, the rest from
  the base).

### Engine resolution — no code change

`transcribe.py:_resolve_target`, when no `--engine` is passed and `config.json`
has no usable engine, already falls back to `("local", cfg.default_engine)`
with `default_engine` defaulting to `"faster-whisper"`. With `faster-whisper`
now always installed, `InProcessEngine` always imports successfully, so this
fallback runs in-process instead of spawning a subprocess. No change to
resolution logic, `config.py`, or `engine.py` is required. The
subprocess-based `LocalEngine` fallback remains as defensive dead-ish code and
is left untouched.

## Documentation updates

Part of the same change:

- **CLAUDE.md** — collapse the install personas table to the default install
  plus the `whisper`/`voxtral` note; update the `uv tool install` caveat
  paragraph; update the "Cross-package imports" note (`live_transcriber` and the
  faster-whisper engine are no longer gated behind extras).
- **README.md** — persona table (lines 285–292) and the local-only-mode
  paragraph (line 96, "when an engine extra is installed").
- **docs/architecture.md** — lines 200–201 ("gated behind [live] extra" /
  "gated behind engine extra") no longer apply to faster-whisper.

## Out of scope

- No seeded/default `config.json` file — the implicit hardcoded fallback is
  sufficient.
- No new `local` engine entry type.
- No backward-compatible empty extra aliases.

## Verification

- `uv sync --all-packages` resolves cleanly.
- `uv pip install --dry-run` (or inspecting metadata) shows base `resona-cli`
  pulls `textual`, `sounddevice`, `soundfile`, `numpy`, `soxr`,
  `resona-asr-core`, `resona-engine-faster-whisper`.
- `uv tool install --from ./apps/resona-cli resona-cli` succeeds and
  `resona rec`, `resona live`, and `resona transcribe <file>` (no server) all
  work.
- `resona-cli[whisper]` and `resona-cli[voxtral]` still resolve their engines.
- `resona-cli[faster-whisper]` now errors with "unknown extra".
- `uv run pytest apps/resona-cli/tests/` passes.
