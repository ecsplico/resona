# Backend → Engine Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the "backend" concept to "engine" across the entire Resona repo as a pure, behavior-preserving refactor, keeping the `uv run pytest` suite (263 passing) green after every task.

**Architecture:** Resona is a `uv` workspace monorepo (Python 3.12, src-layout packages). "Backend" is currently overloaded: ASR plugins discovered via the `resona.backends` entry-point group, the `RESONA_BACKEND` env var, and the `BackendConfig`/`BackendEntry`/`resolve_backend` config model in `resona-client`. This plan unifies all of that under the word "engine" — entry-point group, env var, config classes/keys, the `resona backends` CLI subcommand, the `--backend` flag, and the `InProcessEngine(backend=...)` keyword — with backward-compatible config loading so existing `~/.resona/config.json` files keep working.

**Tech Stack:** Python 3.12, `uv` workspace, `pytest`, `typer` CLI, `httpx`, `python-decouple` config, Python entry points (`importlib.metadata`).

---

## Scope notes (read before starting)

This is Plan 1 of the design `docs/superpowers/specs/2026-05-15-cloud-engines-private-design.md`. Plan 2 (cloud engines + `private`) is **out of scope** — do not add new fields, providers, or `--private` here.

**Do NOT rename these — they are unrelated uses of the word "backend":**
- `build-backend = "hatchling.build"` in every `pyproject.toml` — this is PEP 517 build metadata.
- `anyio_backend` fixture in `packages/engine-server/tests/conftest.py` — this is a pytest-anyio fixture name.

**Already correctly named "engine" — leave alone:** `RESONA_ENGINE_URL`, `RESONA_ENGINE_KEY`, `EngineClient`, `RemoteEngine`, `InProcessEngine`, `LocalEngine`, `engine.py`, `local_engine.py`. Only `RESONA_BACKEND` (the ASR-plugin selector) and the `BackendConfig` family change.

**`.env.example` note:** `.env.example` is stale and uses `ASR_MODE`, not `RESONA_BACKEND` — there is nothing to rename there. Do not edit `.env.example`.

**Do NOT edit historical records:** files under `docs/superpowers/specs/` and `docs/superpowers/plans/` are dated records and must not be changed (including this file's own directory).

---

## File Structure

Files **renamed** (use `git mv` to preserve history):

| Old path | New path | Role |
|----------|----------|------|
| `apps/resona-cli/src/resona_cli/backends.py` | `apps/resona-cli/src/resona_cli/engines.py` | CLI subcommand module for managing config engine entries |
| `apps/resona-cli/tests/test_backends.py` | `apps/resona-cli/tests/test_engines.py` | Tests for the CLI engines subcommand |
| `docs/configuration/backends.md` | `docs/configuration/engines.md` | Docs page on configured engine servers + SSH |

Files **modified**:

| Path | Role / change |
|------|---------------|
| `packages/engine-faster-whisper/pyproject.toml` | Entry-point group `resona.backends` → `resona.engines` |
| `packages/engine-whisper/pyproject.toml` | Entry-point group `resona.backends` → `resona.engines` |
| `packages/engine-voxtral/pyproject.toml` | Entry-point group `resona.backends` → `resona.engines` |
| `packages/asr-core/src/resona_asr_core/registry.py` | `ENTRY_POINT_GROUP` constant + `RESONA_BACKEND` env var + local var names |
| `packages/asr-core/tests/test_registry.py` | Test function names + `_load_from_entrypoint` kwarg |
| `docker-compose.resona.yml` | `RESONA_BACKEND` env key → `RESONA_ENGINE` |
| `packages/client/src/resona_client/config.py` | `BackendEntry`→`EngineEntry`, `BackendConfig`→`EngineConfig`, `resolve_backend`→`resolve_engine`, `default_backend`→`default_engine`, `engines`/legacy `backends` key handling |
| `packages/client/src/resona_client/client.py` | Import + call of `resolve_engine`, error-message text |
| `packages/client/tests/test_config.py` | All renamed symbols + new legacy-key test |
| `apps/resona-cli/src/resona_cli/engines.py` (renamed) | Typer app `engines_app`, command funcs, `EngineConfig`/`EngineEntry` import |
| `apps/resona-cli/src/resona_cli/main.py` | Import + `add_typer` name `backends` → `engines` |
| `apps/resona-cli/src/resona_cli/engine.py` | `InProcessEngine.__init__` `backend` kwarg → `engine` |
| `apps/resona-cli/src/resona_cli/local_engine.py` | `LocalEngine.__init__` `backend` kwarg → `engine` |
| `apps/resona-cli/src/resona_cli/transcribe.py` | `--backend` flag → `--engine`, `EngineConfig` import, local var names |
| `apps/resona-cli/src/resona_cli/watch.py` | `--backend` flag → `--engine`, `EngineConfig` import, local var names |
| `apps/resona-cli/tests/test_engines.py` (renamed) | Renamed symbols + module-patch path `resona_cli.backends` → `resona_cli.engines` |
| `apps/resona-cli/tests/test_engine.py` | `InProcessEngine(backend=...)` → `engine=...` |
| `apps/resona-cli/tests/test_extras.py` | `InProcessEngine(backend=...)` → `engine=...` |
| `apps/resona-cli/tests/test_local_engine.py` | `LocalEngine(backend=...)` → `engine=...` |
| `apps/resona-cli/tests/test_transcribe.py` | `--backend` flag → `--engine`, `default_backend`/`backend` kwarg refs |
| `CLAUDE.md`, `README.md`, `mkdocs.yml`, `docs/*.md`, `docs/configuration/*.md`, `docs/reference/*.md` | Prose: "backend" → "engine" where it means the renamed concept |

---

## Task 1 — Entry-point group rename + registry discovery

Renames the `resona.backends` entry-point group to `resona.engines` in all three engine packages and updates `registry.py` to discover from the new group. After editing entry-point metadata, the installed entry points must be refreshed via `uv sync` or the registry finds nothing.

**Files:**
- Modify: `packages/engine-faster-whisper/pyproject.toml` (line 25)
- Modify: `packages/engine-whisper/pyproject.toml` (line 21)
- Modify: `packages/engine-voxtral/pyproject.toml` (line 22)
- Modify: `packages/asr-core/src/resona_asr_core/registry.py` (line 16)
- Test: `packages/asr-core/tests/test_registry.py`, `packages/engine-faster-whisper/tests/test_integration.py`

**Steps:**

- [ ] In `packages/engine-faster-whisper/pyproject.toml`, change the section header `[project.entry-points."resona.backends"]` to `[project.entry-points."resona.engines"]`. Leave the line below it (`faster-whisper = "..."`) unchanged.
- [ ] In `packages/engine-whisper/pyproject.toml`, change `[project.entry-points."resona.backends"]` to `[project.entry-points."resona.engines"]`. Leave the `whisper = "..."` line unchanged.
- [ ] In `packages/engine-voxtral/pyproject.toml`, change `[project.entry-points."resona.backends"]` to `[project.entry-points."resona.engines"]`. Leave the `voxtral = "..."` line unchanged.
- [ ] In `packages/asr-core/src/resona_asr_core/registry.py` line 16, change `ENTRY_POINT_GROUP = "resona.backends"` to `ENTRY_POINT_GROUP = "resona.engines"`.
- [ ] **Refresh installed entry points.** Run `uv sync --all-packages --no-build-isolation-package openai-whisper`. This rewrites the editable installs' `entry_points.txt` so `importlib.metadata.entry_points(group="resona.engines")` resolves. Without this, the registry's `entry_points()` lookup returns an empty list and every transcriber test fails.
- [ ] Run the registry + engine integration tests: `uv run pytest packages/asr-core/tests/ packages/engine-faster-whisper/tests/ -q`. Expected: all pass (the registry suite uses mocked entry points, but the integration test exercises real discovery). If the integration test errors with "Backend ... not found", the `uv sync` step did not take — re-run it.
- [ ] Run the full suite once to confirm nothing else broke: `uv run pytest -q`. Expected: `263 passed`.
- [ ] Commit:
  ```
  git commit -am "refactor: rename resona.backends entry-point group to resona.engines"
  ```

---

## Task 2 — `RESONA_BACKEND` env var → `RESONA_ENGINE`

Renames the ASR-plugin selector env var. Only one real code reference and one docker-compose reference exist.

**Files:**
- Modify: `packages/asr-core/src/resona_asr_core/registry.py` (line 40)
- Modify: `docker-compose.resona.yml` (line 11)
- Test: `packages/asr-core/tests/test_registry.py`, `packages/engine-server/tests/`

**Steps:**

- [ ] In `packages/asr-core/src/resona_asr_core/registry.py` line 40, change `config("RESONA_BACKEND", default="faster-whisper")` to `config("RESONA_ENGINE", default="faster-whisper")`. The surrounding line stays `name = backend or config(...)` for now — the local var `backend` is renamed in Task 3.
- [ ] In `docker-compose.resona.yml` line 11, change the env key `RESONA_BACKEND: faster-whisper` to `RESONA_ENGINE: faster-whisper`. Keep the value and indentation.
- [ ] Search for any other live reference: `grep -rn "RESONA_BACKEND" --include="*.py" --include="*.yml" --include="*.toml" .` — expected output is empty (no remaining hits outside `docs/superpowers/`, which are historical and untouched).
- [ ] Run the affected suites: `uv run pytest packages/asr-core/tests/ packages/engine-server/tests/ -q`. Expected: all pass. (`test_registry.py` mocks `config` via `_load_from_entrypoint`'s `backend=` argument, so the env-var name change is transparent to it.)
- [ ] Run the full suite: `uv run pytest -q`. Expected: `263 passed`.
- [ ] Commit:
  ```
  git commit -am "refactor: rename RESONA_BACKEND env var to RESONA_ENGINE"
  ```

---

## Task 3 — `registry.py` internal `backend` identifiers → `engine`

Renames the local variable and parameter names in `registry.py` and their test references, so the public registry API speaks "engine".

**Files:**
- Modify: `packages/asr-core/src/resona_asr_core/registry.py` (lines 1, 22–23, 38–71)
- Modify: `packages/asr-core/tests/test_registry.py` (lines 36, 65–75, 99–106)

**Steps:**

- [ ] In `packages/asr-core/src/resona_asr_core/registry.py`, apply this old → new mapping:
  - `_load_from_entrypoint(backend: str | None = None)` → `_load_from_entrypoint(engine: str | None = None)`; inside it `name = backend or config(...)` → `name = engine or config(...)`.
  - `get_transcriber(backend: str | None = None)` → `get_transcriber(engine: str | None = None)`; the call `_transcriber = _load_from_entrypoint(backend)` → `_load_from_entrypoint(engine)`.
  - Docstrings/comments mentioning "backend" as the ASR plugin → "engine" (module docstring line 1 "Backend discovery..." → "Engine discovery..."; the `get_transcriber` docstring lines about "The `backend` argument" → "The `engine` argument", "swap backends" → "swap engines"; comment "whisper / voxtral backends ship it" → "whisper / voxtral engines ship it", "faster-whisper backend ships it" → "faster-whisper engine ships it").
  - Log/error strings: `f"Loading backend '{name}'..."` → `f"Loading engine '{name}'..."`; `f"Backend '{name}' does not satisfy..."` → `f"Engine '{name}' does not satisfy..."`; `f"Backend '{name}' ready."` → `f"Engine '{name}' ready."`; `f"Backend '{name}' not found. Installed backends: {installed}"` → `f"Engine '{name}' not found. Installed engines: {installed}"`.
- [ ] In `packages/asr-core/tests/test_registry.py`, apply this old → new mapping:
  - Test function `test_load_from_entrypoint_finds_backend` → `test_load_from_entrypoint_finds_engine`.
  - Test function `test_explicit_backend_name` → `test_explicit_engine_name`; inside it the call `_load_from_entrypoint(backend="specific")` → `_load_from_entrypoint(engine="specific")`.
  - Test function `test_registry_selects_correct_backend_from_multiple` → `test_registry_selects_correct_engine_from_multiple`; its docstring "multiple backends are registered" → "multiple engines are registered".
  - Comment/class in the protocol-violation test: docstring "Backend that doesn't satisfy..." → "Engine that doesn't satisfy..."; class `BadBackend` → `BadEngine` and update the `_make_entry_point("bad", BadBackend)` reference to `BadEngine`.
- [ ] Run: `uv run pytest packages/asr-core/tests/test_registry.py -q`. Expected: all registry tests pass (same count of collected tests as before, just renamed).
- [ ] Run the full suite: `uv run pytest -q`. Expected: `263 passed`.
- [ ] Commit:
  ```
  git commit -am "refactor: rename backend identifiers to engine in asr-core registry"
  ```

---

## Task 4 — `resona-client` config: `BackendEntry`/`BackendConfig`/`resolve_backend` → engine, with legacy-key fallback

Renames the config model classes, the `resolve_backend` function, and the `default_backend` field. `EngineConfig.load()` reads the new top-level `engines` key but falls back to a legacy `backends` key, and reads legacy `default_backend` when `default_engine` is absent; `save()` writes only the new keys. Includes a new test asserting a legacy-format config still loads.

**Files:**
- Modify: `packages/client/src/resona_client/config.py` (whole file — symbols throughout)
- Modify: `packages/client/src/resona_client/client.py` (lines 48–69, 169)
- Modify: `packages/client/tests/test_config.py` (whole file)
- Test: `packages/client/tests/`

**Steps:**

- [ ] In `packages/client/src/resona_client/config.py`, apply this old → new mapping (the module-level path constants `CONFIG_DIR`, `CONFIG_FILE`, `_LEGACY_CONFIG_DIR`, `_LEGACY_CONFIG_FILE` do **not** change):
  - Class `BackendEntry` → `EngineEntry` (definition at line 60; rename every constructor call and type annotation: in `is_reachable(entry: BackendEntry, ...)`, `_start_ssh_tunnel(entry: BackendEntry)`, `_wait_for_backend(entry: BackendEntry, ...)`, and the `Optional[BackendEntry]` returns of `EngineConfig.get` and `resolve_engine`).
  - Class `BackendConfig` → `EngineConfig` (definition at line 93; rename inside `resolve_backend`'s body `cfg = BackendConfig.load()`).
  - Function `resolve_backend` → `resolve_engine` (definition at line 239).
  - Helper `_wait_for_backend` → `_wait_for_engine` (definition at line 230; update its call site inside `resolve_engine`).
  - `EngineConfig` field `backends: list[EngineEntry]` → `engines: list[EngineEntry]`; field `default_backend: str = "faster-whisper"` → `default_engine: str = "faster-whisper"`. Update every internal reference: `self.backends` in `get`, `add`, `remove`, `resolve_engine`'s `cfg.backends`.
  - Module docstring (lines 1–23) and class/method docstrings: replace "backend"/"Backend"/"Backends" with "engine"/"Engine"/"Engines" where it refers to a configured server entry. Update the `resona backends list` / `resona backends add` references in docstrings to `resona engines list` / `resona engines add`. Error strings: `f"Backend '{entry.name}' already exists"` → `f"Engine '{entry.name}' already exists"`; `f"Backend '{name}' not found"` → `f"Engine '{name}' not found"`. Log line `f"Migrated backend config..."` → `f"Migrated engine config..."`.
- [ ] In `packages/client/src/resona_client/config.py`, make `EngineConfig.load()` backward compatible. Replace the body's parsing block so that, after `data = json.loads(...)`:
  ```python
  raw_engines = data.get("engines", data.get("backends", []))
  engines = [EngineEntry(**e) for e in raw_engines]
  default_engine = data.get("default_engine", data.get("default_backend", "faster-whisper"))
  return cls(engines=engines, default_engine=default_engine)
  ```
  This reads the new `engines` key first and falls back to a legacy `backends` key; likewise `default_engine` falls back to legacy `default_backend`.
- [ ] In `packages/client/src/resona_client/config.py`, confirm `EngineConfig.save()` writes **only** the new keys:
  ```python
  data = {
      "engines": [asdict(e) for e in self.engines],
      "default_engine": self.default_engine,
  }
  ```
- [ ] In `packages/client/src/resona_client/client.py`, apply this old → new mapping:
  - Line 62 import `from .config import resolve_backend` → `from .config import resolve_engine`.
  - Line 63 call `entry = resolve_backend(auto_start=auto_start)` → `entry = resolve_engine(auto_start=auto_start)`.
  - Docstring lines 48–56: "resolving the backend to use" → "resolving the engine to use", "First reachable backend" → "First reachable engine", "a backend has compose_dir" → "an engine has compose_dir", "no backend could be resolved" → "no engine could be resolved".
  - Error message lines 68–69: `"No reachable resona backend found.\n"` → `"No reachable resona engine found.\n"`; `"Add one with:  resona backends add <name> <url>"` → `"Add one with:  resona engines add <name> <url>"`.
  - Line 169 docstring "passed to the transcription backend" → "passed to the transcription engine".
- [ ] In `packages/client/tests/test_config.py`, apply this old → new mapping throughout:
  - Module docstring: `BackendConfig, BackendEntry, is_reachable, resolve_backend` → `EngineConfig, EngineEntry, is_reachable, resolve_engine`.
  - Import line 9 `from resona_client.config import BackendConfig, BackendEntry, is_reachable, resolve_backend` → `from resona_client.config import EngineConfig, EngineEntry, is_reachable, resolve_engine`.
  - Every `BackendEntry(...)` → `EngineEntry(...)`; every `BackendConfig(...)`/`BackendConfig.load()` → `EngineConfig(...)`/`EngineConfig.load()`.
  - Every `cfg.backends` / `loaded.backends` → `cfg.engines` / `loaded.engines`; every `BackendConfig(backends=[...])` → `EngineConfig(engines=[...])`.
  - Every `default_backend=` kwarg and `.default_backend` attribute → `default_engine=` / `.default_engine`.
  - Every `resolve_backend(...)` call → `resolve_engine(...)`; the `monkeypatch.setattr("resona_client.config.BackendConfig.load", ...)` strings → `"resona_client.config.EngineConfig.load"`.
  - Test names referencing the symbol: `test_load_default_backend_from_config` → `test_load_default_engine_from_config`; `test_load_default_backend_falls_back_to_faster_whisper` → `test_load_default_engine_falls_back_to_faster_whisper`; `test_save_persists_default_backend` → `test_save_persists_default_engine`; `test_resolve_backend_no_backends` → `test_resolve_engine_no_engines`; `test_resolve_backend_returns_first_reachable` → `test_resolve_engine_returns_first_reachable`; `test_resolve_backend_returns_none_when_unreachable_no_autostart` → `test_resolve_engine_returns_none_when_unreachable_no_autostart`; `test_resolve_backend_skips_unreachable_tries_next` → `test_resolve_engine_skips_unreachable_tries_next`.
  - JSON written by tests: the `default_backend` key inside JSON payloads being **written as new config** (e.g. the `test_load_default_engine_from_config` payload, the `save` round-trip assertions like `data["default_engine"]` and `data["engines"][0]["name"]`) must use the **new** keys. The `engines`/`default_engine` keys are what `save()` produces and what `load()` prefers.
- [ ] In `packages/client/tests/test_config.py`, add a new test asserting a **legacy-format** config still loads. Place it in the `EngineConfig.load` section:
  ```python
  def test_load_reads_legacy_backends_key(tmp_path, monkeypatch):
      """A config.json using the old `backends`/`default_backend` keys still loads."""
      config_file = tmp_path / "config.json"
      legacy = tmp_path / "legacy-never-created"
      config_file.write_text(json.dumps({
          "backends": [{"name": "old", "api_url": "http://old:7000", "api_key": "", "compose_dir": None}],
          "default_backend": "voxtral",
      }))
      monkeypatch.setattr("resona_client.config.CONFIG_FILE", config_file)
      monkeypatch.setattr("resona_client.config.CONFIG_DIR", tmp_path)
      monkeypatch.setattr("resona_client.config._LEGACY_CONFIG_DIR", legacy)
      monkeypatch.setattr("resona_client.config._LEGACY_CONFIG_FILE", legacy / "config.json")
      cfg = EngineConfig.load()
      assert len(cfg.engines) == 1
      assert cfg.engines[0].name == "old"
      assert cfg.default_engine == "voxtral"
  ```
  (Match the exact `monkeypatch`/path-isolation idiom already used by the neighbouring tests in this file.)
- [ ] Run the client suite: `uv run pytest packages/client/tests/ -q`. Expected: all pass, with **one more** test than before (the new legacy-key test).
- [ ] Run the full suite: `uv run pytest -q`. Expected: `264 passed` (263 baseline + 1 new test).
- [ ] Commit:
  ```
  git commit -am "refactor: rename Backend{Config,Entry}/resolve_backend to Engine* with legacy-key fallback"
  ```

---

## Task 5 — `InProcessEngine` and `LocalEngine` `backend` keyword → `engine`

Renames the `backend` constructor keyword to `engine` on both engine wrapper classes and updates every caller and test.

**Files:**
- Modify: `apps/resona-cli/src/resona_cli/engine.py` (lines 50, 80–90)
- Modify: `apps/resona-cli/src/resona_cli/local_engine.py` (lines 1, 38–43)
- Modify: `apps/resona-cli/src/resona_cli/transcribe.py` (lines 130–179 — caller side)
- Modify: `apps/resona-cli/src/resona_cli/watch.py` (lines 67–85 — caller side)
- Modify: `apps/resona-cli/tests/test_engine.py` (lines 39, 54, 70)
- Modify: `apps/resona-cli/tests/test_extras.py` (lines 22, 32)
- Modify: `apps/resona-cli/tests/test_local_engine.py` (lines 89, 99)
- Test: `apps/resona-cli/tests/`

**Steps:**

- [ ] In `apps/resona-cli/src/resona_cli/engine.py`, apply this old → new mapping:
  - `InProcessEngine.__init__(self, backend: str = "faster-whisper")` → `__init__(self, engine: str = "faster-whisper")`.
  - Body `self._backend = backend` → `self._engine = engine`; `self._transcriber = get_transcriber(backend)` → `get_transcriber(engine)`.
  - Line 50 hint string "In-process transcription requires a backend extra." → "In-process transcription requires an engine extra."
  - Line 80 docstring "Loads an ASR backend in-process" → "Loads an ASR engine in-process".
  - If `self._backend` is read anywhere else in the file, rename those reads to `self._engine`.
- [ ] In `apps/resona-cli/src/resona_cli/local_engine.py`, apply this old → new mapping:
  - `LocalEngine.__init__(self, ..., backend: str = "faster-whisper", ...)` → `engine: str = "faster-whisper"`.
  - Body `self.backend = backend` → `self.engine = engine`; `self._package = f"resona-engine-{backend}"` → `f"resona-engine-{engine}"`.
  - Line 1 module docstring "fallback transcription backend" → "fallback transcription engine".
  - If `self.backend` is read anywhere else, rename those reads to `self.engine`.
- [ ] In `apps/resona-cli/src/resona_cli/transcribe.py`, update the caller side: `InProcessEngine(backend=backend)` → `InProcessEngine(engine=backend)` (line ~168) and `LocalEngine(model=model, timeout=engine_timeout, backend=backend)` → `LocalEngine(model=model, timeout=engine_timeout, engine=backend)` (line ~179). Leave the surrounding local var named `backend` for now — it is renamed in Task 6.
- [ ] In `apps/resona-cli/src/resona_cli/watch.py`, update the caller side: `LocalEngine(model=model, timeout=engine_timeout, backend=backend)` → `LocalEngine(model=model, timeout=engine_timeout, engine=backend)` (line ~85). Leave the local var named `backend` for now.
- [ ] In `apps/resona-cli/tests/test_engine.py`, change `InProcessEngine(backend="faster-whisper")` → `InProcessEngine(engine="faster-whisper")` at lines 54 and 70. Update the docstring "loads a backend via get_transcriber" → "loads an engine via get_transcriber".
- [ ] In `apps/resona-cli/tests/test_extras.py`, change `InProcessEngine(backend="faster-whisper")` → `InProcessEngine(engine="faster-whisper")` at line 32, and the test name `test_in_process_engine_without_backend_extra_shows_hint` → `test_in_process_engine_without_engine_extra_shows_hint`.
- [ ] In `apps/resona-cli/tests/test_local_engine.py`, change `LocalEngine(backend="whisper")` → `LocalEngine(engine="whisper")` at line 99, and the test name `test_enter_spawns_custom_backend` → `test_enter_spawns_custom_engine`.
- [ ] Run the cli suite: `uv run pytest apps/resona-cli/tests/ -q`. Expected: all pass (same cli test count).
- [ ] Run the full suite: `uv run pytest -q`. Expected: `264 passed`.
- [ ] Commit:
  ```
  git commit -am "refactor: rename InProcessEngine/LocalEngine 'backend' kwarg to 'engine'"
  ```

---

## Task 6 — CLI: `--backend` flag → `--engine`; `resona backends` subcommand → `resona engines`; rename module + tests

Renames the `backends.py` module to `engines.py` (via `git mv`), the `resona backends` subcommand to `resona engines` (keeping `list`/`add`/`remove`/`test`), and the `--backend` flag on `transcribe`/`watch` to `--engine`. The CLI test module is renamed `test_backends.py` → `test_engines.py` and its `resona_cli.backends.*` patch paths updated. The `isolated_config` fixture patches `resona_client.config.CONFIG_DIR/CONFIG_FILE/_LEGACY_CONFIG_DIR/_LEGACY_CONFIG_FILE` — those module-level names did not move (Task 4), so the fixture body stays the same.

**Files:**
- Rename: `apps/resona-cli/src/resona_cli/backends.py` → `apps/resona-cli/src/resona_cli/engines.py`
- Rename: `apps/resona-cli/tests/test_backends.py` → `apps/resona-cli/tests/test_engines.py`
- Modify: `apps/resona-cli/src/resona_cli/engines.py` (renamed file — symbols throughout)
- Modify: `apps/resona-cli/src/resona_cli/main.py` (lines 5, 13)
- Modify: `apps/resona-cli/src/resona_cli/transcribe.py` (lines 64–76, local vars)
- Modify: `apps/resona-cli/src/resona_cli/watch.py` (lines 20–32, local vars)
- Modify: `apps/resona-cli/tests/test_engines.py` (renamed file — symbols + patch paths)
- Modify: `apps/resona-cli/tests/test_transcribe.py` (lines 312–368)
- Test: `apps/resona-cli/tests/`

**Steps:**

- [ ] `git mv apps/resona-cli/src/resona_cli/backends.py apps/resona-cli/src/resona_cli/engines.py`
- [ ] `git mv apps/resona-cli/tests/test_backends.py apps/resona-cli/tests/test_engines.py`
- [ ] In `apps/resona-cli/src/resona_cli/engines.py`, apply this old → new mapping:
  - Module docstring line 1 "managing resona backends" → "managing resona engines".
  - Import `from resona_client.config import BackendConfig, BackendEntry, is_reachable` → `from resona_client.config import EngineConfig, EngineEntry, is_reachable`.
  - Typer app `backends_app = typer.Typer(no_args_is_help=True, help="Manage backend server addresses.")` → `engines_app = typer.Typer(no_args_is_help=True, help="Manage engine server addresses.")`.
  - Every decorator `@backends_app.command(...)` → `@engines_app.command(...)`.
  - Command functions: `list_backends` → `list_engines`, `add_backend` → `add_engine`, `remove_backend` → `remove_engine`, `test_backends` → `test_engines`.
  - Every `BackendConfig.load()` → `EngineConfig.load()`; every `BackendEntry(...)` → `EngineEntry(...)`; every `cfg.backends` → `cfg.engines`.
  - User-facing strings: "No backends configured." → "No engines configured."; "Add one with:  resona backends add <name> <url>" → "Add one with:  resona engines add <name> <url>"; argument help "Unique name for this backend" → "Unique name for this engine"; "Name of the backend to remove" → "Name of the engine to remove"; "Backend name to test (tests all if omitted)" → "Engine name to test (tests all if omitted)"; `f"Backend '{name}' not found."` → `f"Engine '{name}' not found."`; docstrings/usage examples `resona backends add ...` → `resona engines add ...`; `--compose-dir` help "this backend can be auto-started" → "this engine can be auto-started"; `--ssh` help "when the backend is not directly reachable" → "when the engine is not directly reachable"; "Test reachability of one or all backends." → "Test reachability of one or all engines."; "List all configured backends" → "List all configured engines".
- [ ] In `apps/resona-cli/src/resona_cli/main.py`:
  - Line 5 `from .backends import backends_app` → `from .engines import engines_app`.
  - Line 13 `app.add_typer(backends_app, name="backends", help="Manage backend server addresses.")` → `app.add_typer(engines_app, name="engines", help="Manage engine server addresses.")`.
- [ ] In `apps/resona-cli/src/resona_cli/transcribe.py`, apply this old → new mapping:
  - Line 64 the typer option `backend: Optional[str] = typer.Option(None, "--backend", help="Backend for local engine ... Falls back to default_backend in ~/.resona/config.json.")` → `engine: Optional[str] = typer.Option(None, "--engine", help="Engine for local transcription (e.g. faster-whisper, whisper, voxtral). Falls back to default_engine in ~/.resona/config.json.")`.
  - Line 68 import `from resona_client.config import BackendConfig` → `from resona_client.config import EngineConfig`.
  - Line 70 `resolved_backend = backend or BackendConfig.load().default_backend` → `resolved_engine = engine or EngineConfig.load().default_engine`.
  - Line 76 `_transcribe_local_fallback(files, output_dir, model, language, engine_timeout, resolved_backend)` → pass `resolved_engine`.
  - In `_transcribe_local_fallback` and `_resolve_local_engine`, rename the parameter `backend` → `engine` and every internal reference (`InProcessEngine(engine=backend)` becomes `InProcessEngine(engine=engine)`; `LocalEngine(..., engine=backend)` becomes `LocalEngine(..., engine=engine)`; the log strings `f"... running backend '{backend}' in-process."` → `f"... running engine '{engine}' in-process."` and `f"... starting local engine subprocess (backend={backend})."` → `f"... starting local engine subprocess (engine={engine})."`).
  - Docstring of `_resolve_local_engine` "a backend extra" → "an engine extra", "spawns ``resona-engine-<backend>``" → "spawns ``resona-engine-<engine>``".
- [ ] In `apps/resona-cli/src/resona_cli/watch.py`, apply this old → new mapping:
  - Line 20 typer option `backend: Optional[str] = typer.Option(None, "--backend", help="Backend for local engine ...")` → `engine: Optional[str] = typer.Option(None, "--engine", help="Engine for local transcription (e.g. faster-whisper, whisper, voxtral). Falls back to default_engine in ~/.resona/config.json.")`.
  - Line 24 import `from resona_client.config import BackendConfig` → `from resona_client.config import EngineConfig`.
  - Line 26 `resolved_backend = backend or BackendConfig.load().default_backend` → `resolved_engine = engine or EngineConfig.load().default_engine`.
  - Line 32 pass `resolved_engine` instead of `resolved_backend`.
  - In the local-fallback helper, rename parameter `backend` → `engine`, the log string `f"... starting local engine (backend={backend})."` → `f"... starting local engine (engine={engine})."`, and `LocalEngine(..., engine=backend)` → `LocalEngine(..., engine=engine)`.
- [ ] In `apps/resona-cli/tests/test_engines.py` (the renamed test file), apply this old → new mapping:
  - Module docstring line 1 "Tests for resona_cli.backends CLI commands." → "Tests for resona_cli.engines CLI commands."
  - The `isolated_config` fixture body is unchanged — it patches `resona_client.config.CONFIG_DIR`/`CONFIG_FILE`/`_LEGACY_CONFIG_DIR`/`_LEGACY_CONFIG_FILE`, and those module-level names were **not** renamed in Task 4. Only update the fixture's docstring wording "all backend-config I/O" → "all engine-config I/O" and "``BackendConfig.load()`` migrates" → "``EngineConfig.load()`` migrates".
  - Helper `_write_backends(config_file, *entries)` → `_write_engines(...)`; its body `config_file.write_text(json.dumps({"backends": list(entries)}))` → `json.dumps({"engines": list(entries)})`; docstring "the given backend dicts" → "the given engine dicts".
  - Helper `_backend(name, ...)` → `_engine(name, ...)`; update every call site.
  - Every `patch("resona_cli.backends.is_reachable", ...)` → `patch("resona_cli.engines.is_reachable", ...)`.
  - Every `runner.invoke(app, ["backends", ...])` → `runner.invoke(app, ["engines", ...])`.
  - Every JSON assertion `data["backends"]` → `data["engines"]`.
  - Test function names: `test_list_no_backends` → `test_list_no_engines`; `test_list_shows_backends` → `test_list_shows_engines`; `test_add_backend` → `test_add_engine`; `test_add_backend_with_key` → `test_add_engine_with_key`; `test_add_backend_duplicate_fails` → `test_add_engine_duplicate_fails`; `test_remove_backend` → `test_remove_engine`; `test_remove_nonexistent_backend_fails` → `test_remove_nonexistent_engine_fails`; `test_test_backends_no_backends` → `test_test_engines_no_engines`; `test_test_backends_reachable_exits_0` → `test_test_engines_reachable_exits_0`; `test_test_backends_unreachable_exits_1` → `test_test_engines_unreachable_exits_1`; `test_test_specific_backend_not_found` → `test_test_specific_engine_not_found`.
  - Output assertions: `"No backends configured"` → `"No engines configured"`.
- [ ] In `apps/resona-cli/tests/test_transcribe.py`, apply this old → new mapping:
  - Test `test_transcribe_fallback_passes_backend_to_local_engine` → `test_transcribe_fallback_passes_engine_to_local_engine`; docstring "--backend is forwarded" → "--engine is forwarded"; the invoke `["transcribe", str(tmp_path), "--backend", "whisper"]` → `["transcribe", str(tmp_path), "--engine", "whisper"]`; the assertion `call_kwargs.get("backend") == "whisper"` → `call_kwargs.get("engine") == "whisper"` (the kwarg name changed in Task 5).
  - Test `test_transcribe_fallback_uses_default_backend_from_config` → `test_transcribe_fallback_uses_default_engine_from_config`; docstring "reads default_backend from config" → "reads default_engine from config"; `mock_config.default_backend = "voxtral"` → `mock_config.default_engine = "voxtral"`; `patch("resona_client.config.BackendConfig.load", ...)` → `patch("resona_client.config.EngineConfig.load", ...)`; `call_kwargs.get("backend") == "voxtral"` → `call_kwargs.get("engine") == "voxtral"`.
  - Line 440 docstring "a backend is installed" → "an engine is installed".
- [ ] Run the cli suite: `uv run pytest apps/resona-cli/tests/ -q`. Expected: all pass (same cli test count, with `test_engines.py` now collected in place of `test_backends.py`).
- [ ] Run the full suite: `uv run pytest -q`. Expected: `264 passed`.
- [ ] Commit:
  ```
  git commit -am "refactor: rename 'backends' CLI subcommand and --backend flag to 'engines'/--engine"
  ```

---

## Task 7 — Documentation: replace "backend" with "engine" for the renamed concept

Updates `CLAUDE.md`, `README.md`, `mkdocs.yml`, and the docs under `docs/` (excluding the untouchable `docs/superpowers/` records). Renames the `docs/configuration/backends.md` page to `engines.md` and updates the mkdocs nav link.

**Files:**
- Rename: `docs/configuration/backends.md` → `docs/configuration/engines.md`
- Modify: `CLAUDE.md`
- Modify: `README.md`
- Modify: `mkdocs.yml` (lines 2, 61)
- Modify: `docs/architecture.md`, `docs/cli.md`, `docs/getting-started.md`, `docs/index.md`, `docs/onboarding.md`, `docs/configuration/environment.md`, `docs/configuration/engines.md` (renamed), `docs/reference/client.md`, `docs/reference/engine.md`

**Steps:**

- [ ] `git mv docs/configuration/backends.md docs/configuration/engines.md`
- [ ] In `mkdocs.yml`: line 2 `site_description: Modular audio transcription platform with pluggable ASR backends` → `... pluggable ASR engines`; line 61 nav entry `- Backends & SSH: configuration/backends.md` → `- Engines & SSH: configuration/engines.md`.
- [ ] In `docs/configuration/engines.md` (renamed page): heading `# Backends & SSH` → `# Engines & SSH`; replace "backend"/"Backends" with "engine"/"Engines" throughout where it refers to a configured server; update every `resona backends ...` command example to `resona engines ...`; update the `backends`/`default_backend` config-key names shown in JSON examples to `engines`/`default_engine` (note: if the page documents config-file compatibility, add a sentence that legacy `backends`/`default_backend` keys are still read for backward compatibility).
- [ ] In `docs/cli.md`: command-table row `| \`backends\` | Manage backend server addresses |` → `| \`engines\` | Manage engine server addresses |`; option row `| \`--backend\` | ... | Backend for local engine ... |` → `| \`--engine\` | ... | Engine for local transcription ... |`; the `## \`resona backends\`` section heading and all `### backends list/add/remove/test` sub-headings → `engines`; every `resona backends ...` command example → `resona engines ...`; the `# Local fallback with a specific backend` comment and `resona transcribe ./audio/ --backend whisper` example → `--engine whisper`; "Backend resolution" section heading and prose → "Engine resolution"; the `[Backends & SSH](configuration/backends.md)` cross-links → `[Engines & SSH](configuration/engines.md)`.
- [ ] In `docs/architecture.md`: `RESONA_BACKEND` → `RESONA_ENGINE` (lines 45, 106, 108); prose "selects which backend to load" → "selects which engine to load"; the `RESONA_BACKEND | Package | Class | ...` table header/cells referring to the renamed concept → "engine"; keep `RESONA_ENGINE_KEY` (line 159) unchanged.
- [ ] In `docs/configuration/environment.md`: line 9 table row `| \`RESONA_BACKEND\` | \`faster-whisper\` | Backend to load: ... |` → `| \`RESONA_ENGINE\` | \`faster-whisper\` | Engine to load: faster-whisper, whisper, or any installed entry-point name |`. Leave `RESONA_ENGINE_KEY` / `RESONA_ENGINE_URL` rows unchanged.
- [ ] In `docs/getting-started.md`: line 23 comment `# Edit .env — set RESONA_BACKEND, model names, ...` → `# Edit .env — set RESONA_ENGINE, model names, ...`.
- [ ] In `docs/reference/engine.md`: line 15 "Backends are discovered via Python entry points ... The `RESONA_BACKEND` environment variable selects which backend to load." → "Engines are discovered via Python entry points ... The `RESONA_ENGINE` environment variable selects which engine to load."; update remaining "backend" prose referring to ASR plugins → "engine".
- [ ] In `docs/index.md`, `docs/onboarding.md`, `docs/reference/client.md`: replace "backend"/"Backends" with "engine"/"Engines" where it refers to the renamed concept (ASR plugin, entry-point group, config server entry, the `resona backends` command, `RESONA_BACKEND`). Do not touch `build-backend` or any unrelated usage.
- [ ] In `README.md`: `RESONA_BACKEND=whisper uv run resona-engine-whisper` (line 209) → `RESONA_ENGINE=whisper ...`; env-var table row `| \`RESONA_BACKEND\` | \`faster-whisper\` | Backend to load |` (line 235) → `| \`RESONA_ENGINE\` | \`faster-whisper\` | Engine to load |`; the `[project.entry-points."resona.backends"]` example → `resona.engines`; the "How to add a new transcription backend" heading and steps → "engine"; `resona backends` command examples → `resona engines`; install-persona text and `--backend` flag mentions → `--engine`. Keep `RESONA_ENGINE_URL`/`RESONA_ENGINE_KEY` rows unchanged.
- [ ] In `CLAUDE.md`: the `[project.entry-points."resona.backends"]` example → `resona.engines`; line 54 "`RESONA_BACKEND` env var selects which backend to load" → "`RESONA_ENGINE` env var selects which engine to load"; line 307 env-var table row `| \`RESONA_BACKEND\` | engine-server | Backend selection | \`faster-whisper\` |` → `| \`RESONA_ENGINE\` | engine-server | Engine selection | \`faster-whisper\` |`; the "How to add a new transcription backend" section heading → "engine"; "Backend discovery via entry points" heading → "Engine discovery via entry points"; the `resona backends` references → `resona engines`; the `resona transcribe ... --backend` examples → `--engine`; `BackendConfig`/`default_backend` mentions in the `resona-client` and backend-resolution sections → `EngineConfig`/`default_engine`. Keep `RESONA_ENGINE_URL`/`RESONA_ENGINE_KEY` and `build-backend` unchanged.
- [ ] Verify no stale references remain in live (non-historical) files: `grep -rn -i "resona.backends\|RESONA_BACKEND\|BackendConfig\|BackendEntry\|resolve_backend\|default_backend\|resona backends\|--backend" --include="*.py" --include="*.toml" --include="*.yml" --include="*.md" . | grep -v "docs/superpowers/"`. Expected output: empty. (`build-backend` and `anyio_backend` will not match these patterns; if they do appear, they were not touched and that is correct.)
- [ ] Build the docs to confirm the nav link and renamed page resolve: `uv run mkdocs build --strict`. Expected: build succeeds with no warnings about a missing `configuration/backends.md`.
- [ ] Commit:
  ```
  git commit -am "docs: rename 'backend' concept to 'engine' across docs and guides"
  ```

---

## Task 8 — Full-suite verification

Confirms the entire rename is behavior-preserving and the suite is green.

**Files:** none (verification only)

**Steps:**

- [ ] Run the full suite: `uv run pytest -q`. Expected final line: `264 passed` (263 baseline + the one new legacy-key test from Task 4). No failures, no errors, no new warnings beyond the pre-existing `datetime.utcnow()` `DeprecationWarning`.
- [ ] Run the live-reference grep once more across all file types: `grep -rn -i "resona\.backends\|RESONA_BACKEND\|BackendConfig\|BackendEntry\|resolve_backend\|default_backend\|\"backends\"\|'backends'" --include="*.py" --include="*.toml" --include="*.yml" --include="*.yaml" --include="*.json" --include="*.md" . | grep -v "docs/superpowers/"`. Expected: empty (the only legacy `backends`/`default_backend` strings remaining are the backward-compat reads inside `EngineConfig.load()` and the Task 4 legacy-key test, which are intentional — confirm any hits are exactly those).
- [ ] Confirm the three `git mv` renames are tracked as renames: `git log --oneline --follow -- apps/resona-cli/src/resona_cli/engines.py | head -1` returns a commit, and `git status` shows a clean tree.
- [ ] If any task left the suite red, stop and fix before claiming completion — every task's commit must individually leave `uv run pytest` green.
