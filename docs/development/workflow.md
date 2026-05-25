# Dev Workflow

This page covers the day-to-day development loop: how to install the workspace,
run services, add dependencies, and know when you need to reinstall the tool.

---

## The editable loop

### 1. Install all packages once

```bash
uv sync --all-packages
```

This installs every workspace package into `.venv` in **editable mode**. All
Python source under `src/` is live — changes to any package take effect the
next time the process starts. The workspace root supplies the missing build
dependencies for `openai-whisper` via `[tool.uv.extra-build-dependencies]`,
so no `--no-build-isolation-package` flag is needed.

### 2. Run everything through `uv run`

```bash
uv run resona transcribe ./audio/          # CLI
uv run resona-engine-faster-whisper        # engine server on :7001
uv run resona-api                          # API server on :7000
uv run resona rec                          # recorder TUI
uv run resona live                         # live transcription TUI
uv run resona ui                           # record-and-transcribe TUI
```

`uv run` resolves the workspace `.venv`. Because all packages are editable,
your edits are visible immediately — no reinstall step needed.

---

## `uv run` vs `uv tool install` — comparison

| | `uv run resona` (recommended) | `uv tool install resona-cli` |
|---|---|---|
| Install mode | Editable (live source) | Copied into isolated tool env |
| Workspace deps editable | Yes — all packages | No — only resona-cli itself when `--editable` is passed |
| Pick up code changes | Immediately (on next process start) | Requires reinstall |
| Use case | Development and testing | Verifying install personas |
| Reinstall command | n/a | `uv tool install --reinstall --from ./apps/resona-cli resona-cli` |

!!! warning "Tool install is not editable for workspace deps"
    Even if you pass `--editable` to `uv tool install`, only `resona-cli` itself
    becomes editable. Packages such as `resona-postprocess` and `resona-asr-core`
    are still **copied**, not linked. Use `uv run resona` from the repo root for
    all development work.

---

## Common dev tasks

### Running individual services

```bash
# Terminal 1 — engine (requires GPU or falls back to CPU)
uv run resona-engine-faster-whisper

# Terminal 2 — API server (requires engine on :7001)
uv run resona-api

# Terminal 3 — CLI (talks to API on :7000)
uv run resona transcribe ./audio/
```

### Adding a dependency to a package

```bash
uv add --package resona-api httpx
uv add --package resona-cli typer
```

The `--package` flag scopes the addition to the named workspace member. After
running this command, `uv.lock` is updated and the package is immediately
available in the workspace venv — no extra sync step needed.

### Running the docs server

```bash
uv run mkdocs serve          # live-reload dev server at http://localhost:8000
uv run mkdocs build          # write static site to site/
```

### Checking what is installed

```bash
uv pip list | grep resona
```

All `resona-*` packages should show their source path (ending in `/src`) when
installed editable.

---

## When you DO need to reinstall the tool

The installed tool (`uv tool install`) is only needed when you are verifying
one of the install personas described in [Installation](../getting-started/installation.md).
In that case, reinstall after every change:

```bash
# Default persona (CTranslate2 only)
uv tool install --reinstall --from ./apps/resona-cli resona-cli

# With PyTorch Whisper engine
uv tool install --reinstall --from ./apps/resona-cli 'resona-cli[whisper]'

# With Voxtral engine
uv tool install --reinstall --from ./apps/resona-cli 'resona-cli[voxtral]'
```

!!! note
    The `[whisper]` and `[voxtral]` extras pull a PyTorch build from the
    `cu130` index. `uv tool install` does not inherit the workspace index
    configuration, so these may fail to resolve `torch` outside the workspace.
    If that happens, stay in the workspace and use `uv run resona` instead.
