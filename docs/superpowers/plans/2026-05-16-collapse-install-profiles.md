# Collapse resona-cli Install Profiles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the default `resona-cli` install a fully capable, torch-free tool (record, live, in-process faster-whisper) and keep only `whisper`/`voxtral` as opt-in extras.

**Architecture:** Move the `record`, `live`, and `faster-whisper` extra dependencies into the base `dependencies` of `apps/resona-cli/pyproject.toml`; delete those three extras; keep `whisper` and `voxtral`. No application-code change — engine resolution already falls back to local `faster-whisper`. Update the three docs that reference the old extras.

**Tech Stack:** Python packaging (PEP 621 `pyproject.toml`), uv workspace, hatchling.

**Spec:** `docs/superpowers/specs/2026-05-16-collapse-install-profiles-design.md`

---

### Task 1: Refactor `apps/resona-cli/pyproject.toml`

**Files:**
- Modify: `apps/resona-cli/pyproject.toml`

- [ ] **Step 1: Replace the `dependencies` and `[project.optional-dependencies]` sections**

In `apps/resona-cli/pyproject.toml`, the `dependencies` array currently ends after `"resona-postprocess",`. Replace the `dependencies` array and the entire `[project.optional-dependencies]` block so they read exactly:

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

Leave `[build-system]`, `[project]` metadata, `[tool.uv.sources]`, `[project.scripts]`, and `[tool.hatch.build.targets.wheel]` unchanged. `[tool.uv.sources]` keeps all seven workspace entries.

- [ ] **Step 2: Verify the workspace still resolves**

Run: `uv sync --all-packages --no-build-isolation-package openai-whisper`
Expected: completes without error; no "no extra named" or resolution failure.

- [ ] **Step 3: Verify the base package now pulls the audio/engine deps**

Run: `uv pip show resona-cli` then inspect `Requires:`, or run `uv tree --package resona-cli`.
Expected: `textual`, `sounddevice`, `soundfile`, `numpy`, `soxr`, `resona-asr-core`, and `resona-engine-faster-whisper` all appear as base requirements of `resona-cli`.

- [ ] **Step 4: Verify the removed extras are gone and kept extras work**

Run: `uv pip install --dry-run --no-deps 'resona-cli[faster-whisper]' 2>&1 | head -5` (from a context where `resona-cli` is resolvable) — or simply confirm by inspecting the file that no `record`/`live`/`faster-whisper` keys remain under `[project.optional-dependencies]`.
Expected: only `whisper` and `voxtral` extras are defined.

- [ ] **Step 5: Run the CLI test suite**

Run: `uv run pytest apps/resona-cli/tests/`
Expected: PASS (no tests should depend on extras being opt-in).

- [ ] **Step 6: Commit**

```bash
git add apps/resona-cli/pyproject.toml uv.lock
git commit -m "build(resona-cli): make record/live/faster-whisper default, keep only whisper/voxtral extras"
```

---

### Task 2: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Replace the "Install personas" table and caveat**

Find the `### Install personas` section. Replace the table and the two paragraphs that follow it (the `⚠️` paragraph and the `[faster-whisper]`/`[live]` torch-free paragraph) with:

```markdown
### Install personas

| Persona | Command |
|---------|---------|
| Default (record, live, local faster-whisper) | `uv tool install --from ./apps/resona-cli resona-cli` |
| Default + Whisper (PyTorch) engine | `uv tool install --from ./apps/resona-cli 'resona-cli[whisper]'` |
| Default + Voxtral (PyTorch) engine | `uv tool install --from ./apps/resona-cli 'resona-cli[voxtral]'` |

The default install is torch-free: it bundles the record/live TUIs and the
CTranslate2-based `faster-whisper` engine (via the `nvidia-cublas-cu12` /
`nvidia-cudnn-cu12` wheels and `soxr` for resampling), so `uv tool install`
works without any extra index.

⚠️ The `[whisper]`/`[voxtral]` extras pull a stable PyTorch build from the cu130 index. `uv tool install` does NOT inherit the workspace's pytorch index, so these may fail to resolve torch. Workarounds:
- Stay inside the workspace and use `uv run resona <command>`.
- Or: `uv pip install --extra-index-url https://download.pytorch.org/whl/cu130 'resona-cli[whisper]'` into a managed venv.
```

- [ ] **Step 2: Update the "Cross-package imports" note**

Find the line under `## Import conventions` that begins `Cross-package imports: resona-cli imports`. Replace that sentence with:

```markdown
Cross-package imports: resona-cli imports `resona_asr_core.live_transcriber` (for the `live` command) and `resona_asr_core.registry` (for `InProcessEngine`). Both `resona-asr-core` and `resona-engine-faster-whisper` are base dependencies of resona-cli, so these imports always resolve. All other cross-package communication is over HTTP.
```

- [ ] **Step 3: Verify no stale extra references remain**

Run: `grep -n 'resona-cli\[record\|resona-cli\[live\|resona-cli\[faster-whisper\|\[live\] extra\|\[faster-whisper\] and \[live\]' CLAUDE.md`
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(CLAUDE): update install personas for collapsed profiles"
```

---

### Task 3: Update README.md

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Replace the persona table and its caveat**

Find the persona table (header row `| Persona | Command |`) and the paragraph immediately after it that starts `The \`[faster-whisper]\`, \`[live]\`, and \`[record]\` extras are torch-free`. Replace both with:

```markdown
| Persona | Command |
|---------|---------|
| Default (record, live, local faster-whisper) | `uv tool install --from ./apps/resona-cli resona-cli` |
| Default + Whisper (PyTorch) engine | `uv tool install --from ./apps/resona-cli 'resona-cli[whisper]'` |
| Default + Voxtral (PyTorch) engine | `uv tool install --from ./apps/resona-cli 'resona-cli[voxtral]'` |

The default install is torch-free and needs no extra index — it includes the record/live TUIs and the `faster-whisper` engine. The `[whisper]`/`[voxtral]` extras pull a stable PyTorch build from the cu130 index; if `uv tool install` does not inherit the workspace's pytorch index, use `uv run resona` from inside the workspace, or `uv pip install --extra-index-url https://download.pytorch.org/whl/cu130 'resona-cli[whisper]'`.
```

- [ ] **Step 2: Update the local-only-mode paragraph**

Find the paragraph under `### Local-only mode (no server)` that mentions `when an engine extra is installed (e.g. \`resona-cli[faster-whisper]\`)`. Replace the whole paragraph with:

```markdown
If no server is reachable, the CLI automatically transcribes locally. The default install bundles the `faster-whisper` engine, so the CLI runs it in-process — no subprocess spawn, no extra to install.
```

- [ ] **Step 3: Verify no stale extra references remain**

Run: `grep -n 'resona-cli\[record\|resona-cli\[live\|resona-cli\[faster-whisper' README.md`
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs(README): update install personas for collapsed profiles"
```

---

### Task 4: Update docs/architecture.md

**Files:**
- Modify: `docs/architecture.md`

- [ ] **Step 1: Update the cross-package import diagram lines**

Find these two lines (around lines 200-201):

```
resona-cli  ──imports──▶  resona_asr_core.live_transcriber  (live command, gated behind [live] extra)
resona-cli  ──imports──▶  resona_asr_core.registry           (InProcessEngine, gated behind engine extra)
```

Replace them with:

```
resona-cli  ──imports──▶  resona_asr_core.live_transcriber  (live command — base dependency)
resona-cli  ──imports──▶  resona_asr_core.registry           (InProcessEngine — base dependency)
```

- [ ] **Step 2: Verify no stale extra references remain**

Run: `grep -n '\[live\] extra\|engine extra\|resona-cli\[' docs/architecture.md`
Expected: no output (or only matches unrelated to the removed extras — confirm by reading any hits).

- [ ] **Step 3: Commit**

```bash
git add docs/architecture.md
git commit -m "docs(architecture): drop extra-gating note for faster-whisper/live"
```

---

## Final verification

- [ ] `uv sync --all-packages --no-build-isolation-package openai-whisper` succeeds.
- [ ] `uv run pytest apps/resona-cli/tests/` passes.
- [ ] `grep -rn 'resona-cli\[record\|resona-cli\[live\|resona-cli\[faster-whisper' CLAUDE.md README.md docs/architecture.md` returns nothing.
- [ ] `apps/resona-cli/pyproject.toml` `[project.optional-dependencies]` contains only `whisper` and `voxtral`.
