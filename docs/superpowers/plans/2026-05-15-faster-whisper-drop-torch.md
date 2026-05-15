# Drop PyTorch nightly from the faster-whisper backend — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the `torch>=2.11.0.dev` (PyTorch nightly) dependency from `resona-engine-faster-whisper`, replacing it with the explicit `nvidia-cublas-cu12` / `nvidia-cudnn-cu12` libraries that CTranslate2 actually needs.

**Architecture:** faster-whisper has no upstream torch dependency — its inference engine is CTranslate2, which links against `libcublas.so.12` + `libcudnn.so.9`. Today PyTorch nightly is dragged in only to (a) side-load those CUDA libraries into the process and (b) provide `torch.cuda.is_available()` for device detection. We replace (a) with a small `ctypes`-based preloader that loads the CUDA `.so` files from the `nvidia-*` pip wheels (the same trick PyTorch itself uses internally), and (b) with a `ctranslate2.get_cuda_device_count()` fallback in the registry. Net result: a faster-whisper-only install drops from ~4.7 GB to ~1.3 GB and no longer needs the PyTorch nightly index.

**Tech Stack:** Python 3.12, uv workspace, faster-whisper 1.1.1, CTranslate2 4.6, `nvidia-cublas-cu12` / `nvidia-cudnn-cu12` pip wheels, pytest.

**Non-goals (explicitly out of scope):**
- `resona-engine-whisper` and `resona-engine-voxtral` keep their `torch` nightly dependency — PyTorch is their genuine runtime engine. The workspace `pytorch-nightly` index and `[tool.uv.sources]` torch entries stay.
- The `[live]` extra of `resona-cli` keeps `torchaudio` — out of scope.
- No `[gpu]` optional-extra split for faster-whisper; the `nvidia-*` deps are unconditional (matching how `torch` was unconditional before). A future refinement, not this plan.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `packages/engine-faster-whisper/src/resona_engine_faster_whisper/_cuda_libs.py` | `preload_cuda_libs()` — ctypes-preload cuBLAS/cuDNN from `nvidia-*` wheels | Create |
| `packages/engine-faster-whisper/tests/test_cuda_libs.py` | Unit tests for the preloader | Create |
| `packages/engine-faster-whisper/src/resona_engine_faster_whisper/transcriber.py` | Call `preload_cuda_libs()` before constructing a CUDA `WhisperModel` | Modify |
| `packages/engine-faster-whisper/tests/test_transcriber.py` | Add test that CUDA device triggers the preloader | Modify |
| `packages/engine-faster-whisper/pyproject.toml` | Swap `torch` → `nvidia-cublas-cu12` + `nvidia-cudnn-cu12` | Modify |
| `packages/asr-core/src/resona_asr_core/registry.py` | Add CTranslate2 fallback to `_detect_device()` | Modify |
| `packages/asr-core/tests/test_registry.py` | Test the CTranslate2 device-detection fallback | Modify |
| `uv.lock` | Regenerated lockfile | Modify (via `uv lock`) |
| `justfile` | Drop `--index` from `install-cli-faster-whisper` | Modify |
| `CLAUDE.md` | Update install-personas table + the torch-nightly warning | Modify |

Note: the faster-whisper `Dockerfile` needs **no change** — it only runs `uv sync --package resona-engine-faster-whisper --frozen`, which picks up the new dependency set from the regenerated lockfile automatically. Task 6 verifies this.

⚠️ The working tree already has uncommitted changes to `justfile` (and a deleted `.geminiignore`). Before starting, confirm with the user whether those should be committed/stashed first, so Task 5's `justfile` edit doesn't get tangled with unrelated changes.

---

## Task 1: CUDA library preloader module

**Files:**
- Create: `packages/engine-faster-whisper/src/resona_engine_faster_whisper/_cuda_libs.py`
- Test: `packages/engine-faster-whisper/tests/test_cuda_libs.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/engine-faster-whisper/tests/test_cuda_libs.py`:

```python
import sys
import types
from unittest.mock import MagicMock, patch

from resona_engine_faster_whisper._cuda_libs import preload_cuda_libs


def test_preload_is_noop_when_packages_absent():
    """No nvidia.* packages installed -> preload silently does nothing."""
    with patch(
        "resona_engine_faster_whisper._cuda_libs.importlib.util.find_spec",
        return_value=None,
    ):
        with patch("resona_engine_faster_whisper._cuda_libs.ctypes.CDLL") as cdll:
            preload_cuda_libs()
            cdll.assert_not_called()


def test_preload_loads_existing_libs(tmp_path):
    """When an nvidia lib dir + .so exist, the .so is CDLL-loaded RTLD_GLOBAL."""
    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    (lib_dir / "libcublas.so.12").touch()
    (lib_dir / "libcudnn.so.9").touch()

    fake_spec = types.SimpleNamespace(submodule_search_locations=[str(lib_dir)])

    with patch(
        "resona_engine_faster_whisper._cuda_libs.importlib.util.find_spec",
        return_value=fake_spec,
    ):
        with patch("resona_engine_faster_whisper._cuda_libs.ctypes.CDLL") as cdll:
            preload_cuda_libs()
            loaded = {call.args[0] for call in cdll.call_args_list}
            assert str(lib_dir / "libcublas.so.12") in loaded
            assert str(lib_dir / "libcudnn.so.9") in loaded


def test_preload_swallows_oserror(tmp_path):
    """A failing CDLL load is logged, not raised."""
    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    (lib_dir / "libcublas.so.12").touch()
    (lib_dir / "libcudnn.so.9").touch()

    fake_spec = types.SimpleNamespace(submodule_search_locations=[str(lib_dir)])

    with patch(
        "resona_engine_faster_whisper._cuda_libs.importlib.util.find_spec",
        return_value=fake_spec,
    ):
        with patch(
            "resona_engine_faster_whisper._cuda_libs.ctypes.CDLL",
            side_effect=OSError("boom"),
        ):
            preload_cuda_libs()  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/engine-faster-whisper/tests/test_cuda_libs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'resona_engine_faster_whisper._cuda_libs'`

- [ ] **Step 3: Write the implementation**

Create `packages/engine-faster-whisper/src/resona_engine_faster_whisper/_cuda_libs.py`:

```python
"""Preload pip-installed NVIDIA CUDA libraries so CTranslate2 can find them.

faster-whisper's CTranslate2 backend links against ``libcublas.so.12`` and
``libcudnn.so.9``. When these come from the ``nvidia-cublas-cu12`` /
``nvidia-cudnn-cu12`` pip wheels (rather than a system-wide CUDA install), the
dynamic loader will not find them unless ``LD_LIBRARY_PATH`` was set *before*
the process started. Loading the shared objects with ``RTLD_GLOBAL`` makes them
resolvable for CTranslate2's later ``dlopen`` — this is the same mechanism
PyTorch uses internally for its bundled CUDA libraries, and is why a plain
``import torch`` used to make the GPU "just work" here.
"""

import ctypes
import importlib.util
import logging
from pathlib import Path

log = logging.getLogger(__name__)

# (pip package providing the lib dir, SONAME to load). cuBLAS first: cuDNN's
# kernels depend on cuBLAS symbols, so cuBLAS must be in the process first.
_CUDA_LIBS: list[tuple[str, str]] = [
    ("nvidia.cublas.lib", "libcublas.so.12"),
    ("nvidia.cudnn.lib", "libcudnn.so.9"),
]


def preload_cuda_libs() -> None:
    """Best-effort preload of CUDA libraries from ``nvidia-*`` pip wheels.

    Safe no-op when the packages are absent (CPU-only install) or when the
    libraries are already provided by the system. Never raises.
    """
    for module_name, soname in _CUDA_LIBS:
        spec = importlib.util.find_spec(module_name)
        if spec is None or not spec.submodule_search_locations:
            continue
        lib_dir = Path(next(iter(spec.submodule_search_locations)))
        so_path = lib_dir / soname
        if not so_path.exists():
            continue
        try:
            ctypes.CDLL(str(so_path), mode=ctypes.RTLD_GLOBAL)
            log.debug("Preloaded CUDA library %s", so_path)
        except OSError as exc:
            log.warning("Could not preload %s: %s", so_path, exc)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/engine-faster-whisper/tests/test_cuda_libs.py -v`
Expected: PASS — 3 passed

- [ ] **Step 5: Commit**

```bash
git add packages/engine-faster-whisper/src/resona_engine_faster_whisper/_cuda_libs.py \
        packages/engine-faster-whisper/tests/test_cuda_libs.py
git commit -m "feat(faster-whisper): add ctypes CUDA library preloader"
```

---

## Task 2: Wire the preloader into the transcriber

**Files:**
- Modify: `packages/engine-faster-whisper/src/resona_engine_faster_whisper/transcriber.py`
- Test: `packages/engine-faster-whisper/tests/test_transcriber.py`

- [ ] **Step 1: Write the failing tests**

Add these two tests to the end of `packages/engine-faster-whisper/tests/test_transcriber.py`:

```python
@patch("resona_engine_faster_whisper.transcriber.preload_cuda_libs")
@patch("resona_engine_faster_whisper.transcriber.WhisperModel")
def test_cuda_device_preloads_libs(mock_model_cls, mock_preload):
    FastWhisperTranscriber(device="cuda", modelname="tiny")
    mock_preload.assert_called_once()


@patch("resona_engine_faster_whisper.transcriber.preload_cuda_libs")
@patch("resona_engine_faster_whisper.transcriber.WhisperModel")
def test_cpu_device_does_not_preload_libs(mock_model_cls, mock_preload):
    FastWhisperTranscriber(device="cpu", modelname="tiny")
    mock_preload.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/engine-faster-whisper/tests/test_transcriber.py -v -k preload`
Expected: FAIL — `AttributeError: <module> does not have the attribute 'preload_cuda_libs'`

- [ ] **Step 3: Modify the transcriber**

In `packages/engine-faster-whisper/src/resona_engine_faster_whisper/transcriber.py`:

Add the import after the existing `from faster_whisper import WhisperModel` line:

```python
from faster_whisper import WhisperModel

from ._cuda_libs import preload_cuda_libs
```

Then, inside `FastWhisperTranscriber.__init__`, add the preload call **before** the `WhisperModel(...)` line. The method becomes:

```python
    def __init__(self, device: str = "cpu", modelname: str | None = None):
        model_name = modelname or DEFAULT_MODEL
        compute_type = "int8_float16" if device == "cuda" else "int8"
        if device == "cuda":
            preload_cuda_libs()
        log.info(f"Loading FastWhisper model: {model_name} on {device} ({compute_type})")
        self.model = WhisperModel(model_name, device=device, compute_type=compute_type)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/engine-faster-whisper/tests/test_transcriber.py -v`
Expected: PASS — all tests pass (the 3 pre-existing + the 2 new)

- [ ] **Step 5: Commit**

```bash
git add packages/engine-faster-whisper/src/resona_engine_faster_whisper/transcriber.py \
        packages/engine-faster-whisper/tests/test_transcriber.py
git commit -m "feat(faster-whisper): preload CUDA libs before loading the CUDA model"
```

---

## Task 3: CTranslate2 fallback for device detection

**Files:**
- Modify: `packages/asr-core/src/resona_asr_core/registry.py:19-25`
- Test: `packages/asr-core/tests/test_registry.py`

Context: `_detect_device()` currently imports `torch` and returns `"cpu"` on `ImportError`. With `torch` removed from a faster-whisper-only install, that path would always pick `"cpu"` — a GPU regression. Add a `ctranslate2.get_cuda_device_count()` fallback. CTranslate2 is present whenever faster-whisper is installed; the import stays soft so `asr-core` gains no hard dependency. When `torch` *is* present (whisper/voxtral installs, and the dev workspace) the first branch still wins, so existing behavior and tests are unchanged.

- [ ] **Step 1: Write the failing test**

Add to `packages/asr-core/tests/test_registry.py`. First add `import sys` and `import types` to the existing imports at the top of the file, then add this test:

```python
def test_detect_device_uses_ctranslate2_when_torch_absent(monkeypatch):
    """With torch unavailable, _detect_device falls back to CTranslate2."""
    import resona_asr_core.registry as reg

    # Make `import torch` raise ImportError.
    monkeypatch.setitem(sys.modules, "torch", None)

    fake_ct2 = types.ModuleType("ctranslate2")
    fake_ct2.get_cuda_device_count = lambda: 0
    monkeypatch.setitem(sys.modules, "ctranslate2", fake_ct2)
    assert reg._detect_device() == "cpu"

    fake_ct2.get_cuda_device_count = lambda: 1
    assert reg._detect_device() == "cuda"


def test_detect_device_cpu_when_nothing_available(monkeypatch):
    """No torch and no ctranslate2 -> cpu."""
    import resona_asr_core.registry as reg

    monkeypatch.setitem(sys.modules, "torch", None)
    monkeypatch.setitem(sys.modules, "ctranslate2", None)
    assert reg._detect_device() == "cpu"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/asr-core/tests/test_registry.py -v -k detect_device`
Expected: FAIL — `test_detect_device_uses_ctranslate2_when_torch_absent` fails because the current `except ImportError` branch returns `"cpu"` unconditionally (the second assertion expecting `"cuda"` fails).

- [ ] **Step 3: Modify `_detect_device()`**

In `packages/asr-core/src/resona_asr_core/registry.py`, replace the whole `_detect_device` function (lines 19-25):

```python
def _detect_device() -> str:
    """Return 'cuda' if a GPU is available, else 'cpu'.

    Prefers torch when present (whisper / voxtral backends ship it); otherwise
    falls back to CTranslate2 (the faster-whisper backend ships it). With
    neither installed, assumes CPU.
    """
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        pass
    try:
        import ctranslate2
        return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    except ImportError:
        return "cpu"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/asr-core/tests/test_registry.py -v`
Expected: PASS — all tests pass (pre-existing registry tests + the 2 new detection tests)

- [ ] **Step 5: Commit**

```bash
git add packages/asr-core/src/resona_asr_core/registry.py \
        packages/asr-core/tests/test_registry.py
git commit -m "feat(asr-core): detect CUDA via CTranslate2 when torch is absent"
```

---

## Task 4: Swap the dependency in pyproject.toml and relock

**Files:**
- Modify: `packages/engine-faster-whisper/pyproject.toml`
- Modify: `uv.lock` (regenerated)

- [ ] **Step 1: Edit the dependency list**

In `packages/engine-faster-whisper/pyproject.toml`, replace the `dependencies` array:

```toml
dependencies = [
    "resona-asr-core",
    "resona-engine-server",
    "faster-whisper>=1.1.1",
    "nvidia-cublas-cu12>=12.3",
    "nvidia-cudnn-cu12>=9.0,<10",
]
```

(Removed: `"torch>=2.11.0.dev"`. Added the two `nvidia-*` libraries CTranslate2 links against. `nvidia-cublas-cu12>=12.3` because cuDNN 9 requires CUDA ≥ 12.3; `nvidia-cudnn-cu12>=9.0,<10` pins the cuDNN 9 series CTranslate2 4.6 needs.)

- [ ] **Step 2: Regenerate the lockfile**

Run: `uv lock`
Expected: completes without error. If uv reports a version conflict on `nvidia-cudnn-cu12` or `nvidia-cublas-cu12` (because the `torch` nightly used by `engine-whisper`/`engine-voxtral` pins specific `nvidia-*` versions), widen the constraint to match the version torch requires — e.g. read the conflicting version from the error and set `nvidia-cudnn-cu12` to that exact `9.x` value. Do **not** add a `[tool.uv.sources]` entry — these wheels come from the default PyPI index.

- [ ] **Step 3: Sync and verify the faster-whisper tests still pass**

Run: `uv sync --all-packages --no-build-isolation-package openai-whisper`
Then: `uv run pytest packages/engine-faster-whisper/tests/ packages/asr-core/tests/ -v`
Expected: PASS — all tests pass. (`torch` remains in the venv because `engine-whisper`/`engine-voxtral` still depend on it; only the *declared* dependency of `engine-faster-whisper` changed.)

- [ ] **Step 4: Confirm faster-whisper no longer declares torch**

Run: `uv tree --package resona-engine-faster-whisper --depth 1`
Expected: the printed dependency list shows `faster-whisper`, `nvidia-cublas-cu12`, `nvidia-cudnn-cu12`, `resona-asr-core`, `resona-engine-server` — and **no** `torch`.

- [ ] **Step 5: Commit**

```bash
git add packages/engine-faster-whisper/pyproject.toml uv.lock
git commit -m "feat(faster-whisper): replace torch nightly with explicit nvidia cuBLAS/cuDNN deps"
```

---

## Task 5: Update justfile and CLAUDE.md

**Files:**
- Modify: `justfile`
- Modify: `CLAUDE.md`

⚠️ `justfile` has pre-existing uncommitted edits — see the warning in the File Structure section. Resolve that with the user before this task.

- [ ] **Step 1: Drop the `--index` from the faster-whisper install recipe**

In `justfile`, replace the `install-cli-faster-whisper` recipe:

```make
# Fully local: bundles faster-whisper backend (no torch — CTranslate2 + nvidia wheels)
install-cli-faster-whisper:
    uv tool install --force --from ./apps/resona-cli 'resona-cli[faster-whisper]'
```

(The `--index https://download.pytorch.org/whl/nightly/cu128` lines are removed — the faster-whisper extra no longer pulls a torch nightly. Leave `install-cli-whisper`, `install-cli-voxtral`, and `install-cli-full` unchanged: they still need the nightly index for `torch` / `torchaudio`.)

- [ ] **Step 2: Update the comment block above the install recipes**

In `justfile`, the comment block above `install-cli` currently reads:

```
# The *-whisper / *-voxtral / *-full recipes pass --index for the torch
# nightly wheel since `uv tool install` does NOT inherit the workspace's
# pytorch-nightly index. If it still fails, use `just install` + `uv run resona`.
```

It is already correct (it lists `*-whisper / *-voxtral / *-full`, not `faster-whisper`). Leave it as-is — verify no edit is needed.

- [ ] **Step 3: Update the CLAUDE.md install-personas warning**

In `CLAUDE.md`, find the warning block under the "Install personas" table:

```
⚠️ The `[faster-whisper]`/`[whisper]`/`[voxtral]` extras pull a torch nightly. `uv tool install` does NOT inherit the workspace's pytorch-nightly index, so these may fail to resolve torch. Workarounds:
- Stay inside the workspace and use `uv run resona <command>`.
- Or: `uv pip install --extra-index-url https://download.pytorch.org/whl/nightly/cu128 'resona-cli[faster-whisper]'` into a managed venv.
```

Replace it with:

```
⚠️ The `[whisper]`/`[voxtral]` extras pull a torch nightly. `uv tool install` does NOT inherit the workspace's pytorch-nightly index, so these may fail to resolve torch. Workarounds:
- Stay inside the workspace and use `uv run resona <command>`.
- Or: `uv pip install --extra-index-url https://download.pytorch.org/whl/nightly/cu128 'resona-cli[whisper]'` into a managed venv.

The `[faster-whisper]` extra is torch-free — it uses CTranslate2 plus the `nvidia-cublas-cu12` / `nvidia-cudnn-cu12` wheels from PyPI — so `uv tool install ... 'resona-cli[faster-whisper]'` works without the nightly index.
```

- [ ] **Step 4: Update the install-personas table row in CLAUDE.md**

In `CLAUDE.md`, the personas table has this row:

```
| Fully local (no server) | `uv tool install --from ./apps/resona-cli 'resona-cli[faster-whisper]'` ⚠️ see note |
```

Replace it with:

```
| Fully local (no server) | `uv tool install --from ./apps/resona-cli 'resona-cli[faster-whisper]'` |
```

(The `⚠️ see note` is dropped — the faster-whisper extra no longer has the torch caveat.)

- [ ] **Step 5: Commit**

```bash
git add justfile CLAUDE.md
git commit -m "docs: faster-whisper extra is torch-free, drop nightly-index caveat"
```

---

## Task 6: Verification

These checks cannot be unit-tested — the executor must run them and report results.

- [ ] **Step 1: Full test suite**

Run: `uv run pytest`
Expected: PASS — all packages green, no regressions.

- [ ] **Step 2: Docker build resolves with the new deps**

Run: `docker build -f packages/engine-faster-whisper/Dockerfile -t resona-fw-test .`
Expected: build succeeds. The `uv sync --package resona-engine-faster-whisper --frozen` step must resolve against the regenerated `uv.lock` without needing the PyTorch nightly index. If the build fails on a frozen-lock mismatch, re-run `uv lock` (Task 4) and rebuild.

- [ ] **Step 3: GPU smoke test (requires a CUDA machine)**

This is the critical real-world check — the `ctypes` preloader cannot be verified without an actual GPU. On a CUDA host:

```bash
RESONA_BACKEND=faster-whisper uv run resona-engine-faster-whisper
```

Then in another shell, POST a short WAV fixture to `http://localhost:7001/transcribe` and confirm a transcript comes back. Watch the engine log for `Loading FastWhisper model: ... on cuda` (proves `_detect_device()` picked CUDA via CTranslate2) and the absence of any `Could not preload` warning or `libcublas`/`libcudnn` "cannot open shared object file" error.

If you see a `cannot open shared object file: libcudnn.so.9` error, the preloader's SONAME or package path is wrong for the installed wheel version — inspect `.venv/lib/python3.12/site-packages/nvidia/cudnn/lib/` and adjust `_CUDA_LIBS` in `_cuda_libs.py`.

- [ ] **Step 4: Report**

Summarize: test suite result, Docker build result, and GPU smoke-test result (or note explicitly that no GPU host was available and the smoke test is outstanding).

---

## Self-Review

**Spec coverage:** Option A as scoped = (1) drop torch from faster-whisper [Task 4], (2) add explicit nvidia deps [Task 4], (3) LD_LIBRARY_PATH / preload helper [Tasks 1-2], (4) torch-free device detection [Task 3], (5) Docker [Task 6 Step 2 — verified, no edit needed], (6) workspace/docs cleanup [Task 5]. All covered.

**Placeholder scan:** Every code step contains complete code. The only conditional instruction is Task 4 Step 2's version-conflict handling, which gives a concrete resolution procedure rather than a placeholder.

**Type consistency:** `preload_cuda_libs()` — defined in Task 1, imported in Task 2, mocked by the same name in both tasks' tests. `_detect_device()` — signature unchanged. `_CUDA_LIBS` referenced consistently in `_cuda_libs.py` and Task 6's troubleshooting note.
