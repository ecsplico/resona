# Stabilize the PyTorch dependency — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the unpinned PyTorch *nightly* (`torch>=2.11.0.dev`, `cu128` index) with a *stable* PyTorch release on the `cu130` index for `engine-voxtral` and `engine-whisper`, and remove `torch`/`torchaudio` from the `[live]` CLI extra entirely by switching its mic-audio resampling to `soxr`.

**Architecture:** Three packages currently pin a PyTorch nightly via the workspace's `pytorch-nightly` index (`download.pytorch.org/whl/nightly/cu128`). Nightly builds are non-reproducible and `cu128` is being removed from PyTorch's stable build matrix (deprecated as of PyTorch 2.12). The host GPU is an RTX 5070 Ti (Blackwell, compute capability `sm_120`); the host driver is 595.71.05 (CUDA 13.2-capable). Blackwell needs `cu128`-or-newer kernels, so `cu126` is not an option — the stable target must be **`cu130`**, which the driver already supports. `engine-voxtral` (HuggingFace `transformers`) and `engine-whisper` (`openai-whisper`) genuinely need PyTorch as their compute backend, so they move to stable `cu130`. The `[live]` extra only used `torchaudio` for a 44.1 kHz→16 kHz resample; that is replaced with `soxr` (a tiny, dependency-free resampler), making the live TUI completely torch-free.

**Tech Stack:** Python 3.12, uv workspace, PyTorch stable (`cu130`), `transformers`, `openai-whisper`, `soxr`, Docker (`nvidia/cuda` base images), pytest.

**Non-goals (explicitly out of scope):**
- `engine-faster-whisper` is already torch-free (prior plan) and its CTranslate2 CUDA libraries are `cu12` wheels — its Dockerfile and deps are **not** touched here.
- No change to how backends are selected or to the `Transcriber` protocol.
- The 3 pre-existing `apps/resona-cli/tests/test_backends.py` failures (they read the user's real `~/.resona/config.json`) are a separate, unrelated test-isolation bug — not addressed here.

---

## Background facts the implementer needs

- **Stable PyTorch index URL for cu130:** `https://download.pytorch.org/whl/cu130` (note: no `/nightly/` segment).
- **Why cu130 and not cu126:** the RTX 5070 Ti is Blackwell (`sm_120`). PyTorch `cu126` wheels contain no `sm_120` kernels and fail at runtime with "no kernel image is available for execution on the device". `cu128` works but is deprecated/removed from the stable matrix. `cu130` is the current stable CUDA target and the host driver (595.71.05) supports CUDA 13.2.
- **`soxr`:** PyPI package `soxr`, import name `soxr`. `soxr.resample(array, in_rate, out_rate)` returns a resampled numpy array, preserving `float32` dtype. Stateless per call — behaviour-equivalent to torchaudio's `Resample` transform, which was also applied per-chunk.
- The workspace dev venv has **both** `torch` (via whisper/voxtral) and `ctranslate2` installed; `import faster_whisper` etc. all work there. A "no regression" test run means the same 261 collected tests, with only the 3 known `test_backends.py` failures.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `pyproject.toml` (workspace root) | uv index + sources for torch | Modify — swap `pytorch-nightly`/`cu128` → `pytorch-cu130`/`cu130`; drop `torchaudio`/`triton` sources + `override-dependencies` |
| `packages/engine-voxtral/pyproject.toml` | voxtral backend deps | Modify — `torch>=2.11.0.dev` → `torch>=2.10` |
| `packages/engine-whisper/pyproject.toml` | whisper backend deps | Modify — `torch>=2.11.0.dev` → `torch>=2.10` |
| `apps/resona-cli/pyproject.toml` | `[live]` extra | Modify — `torchaudio>=2.11.0.dev` → `soxr>=0.5` |
| `apps/resona-cli/src/resona_cli/live_ui.py` | live mic resampling | Modify — replace torchaudio resampler with a `soxr`-based `_resample_to_asr()` helper |
| `apps/resona-cli/src/resona_cli/main.py:50` | `[live]` extra import guard | Modify — `"torchaudio"` → `"soxr"` |
| `apps/resona-cli/tests/test_live_resample.py` | unit test for `_resample_to_asr()` | Create |
| `packages/engine-voxtral/Dockerfile` | voxtral image base | Modify — CUDA 12.8 base → CUDA 13.0 base |
| `packages/engine-whisper/Dockerfile` | whisper image base | Modify — CUDA 12.8 base → CUDA 13.0 base |
| `justfile` | install recipes | Modify — `cu128` nightly index → `cu130` stable; `install-cli-full` becomes index-free |
| `CLAUDE.md` | dependency docs | Modify — replace nightly/`cu128` mentions with stable/`cu130` |
| `uv.lock` | lockfile | Modify (via `uv lock`) |

---

## Task 1: Move engine-voxtral and engine-whisper to stable PyTorch (cu130)

**Files:**
- Modify: `pyproject.toml` (workspace root)
- Modify: `packages/engine-voxtral/pyproject.toml`
- Modify: `packages/engine-whisper/pyproject.toml`
- Modify: `uv.lock` (regenerated)

- [ ] **Step 1: Repoint the workspace torch index to stable cu130**

In the workspace-root `pyproject.toml`, the current torch-related blocks are:

```toml
[[tool.uv.index]]
name = "pytorch-nightly"
url = "https://download.pytorch.org/whl/nightly/cu128"
explicit = true

[tool.uv.sources]
torch = [{ index = "pytorch-nightly" }]
torchaudio = [{ index = "pytorch-nightly" }]
triton = [{ index = "pytorch-nightly" }]

[tool.uv]
override-dependencies = ["triton>=3.0.0"]
```

Replace **all four** of those blocks with:

```toml
[[tool.uv.index]]
name = "pytorch-cu130"
url = "https://download.pytorch.org/whl/cu130"
explicit = true

[tool.uv.sources]
torch = [{ index = "pytorch-cu130" }]
```

Rationale for the removals: `torchaudio` is dropped from the workspace entirely in Task 2, so it needs no source. `triton` is a normal PyPI dependency of *stable* torch (the nightly-specific `triton` source pin and the `override-dependencies` triton hack were only needed for nightly builds). Leave every other `[tool.uv.*]` table (`workspace`, `extra-build-dependencies`, `pytest.ini_options`, `dependency-groups`) untouched. If removing `override-dependencies` leaves an empty `[tool.uv]` table header with nothing under it, remove that bare `[tool.uv]` header line too.

- [ ] **Step 2: Update the engine torch constraints**

In `packages/engine-voxtral/pyproject.toml`, the `dependencies` array currently is:

```toml
dependencies = [
    "resona-asr-core",
    "resona-engine-server",
    "transformers>=4.45.0",
    "torch>=2.11.0.dev",
    "accelerate>=0.34.0",
]
```

Change the `torch` line to `"torch>=2.10",` (drop the `.dev` pre-release floor — a stable floor):

```toml
dependencies = [
    "resona-asr-core",
    "resona-engine-server",
    "transformers>=4.45.0",
    "torch>=2.10",
    "accelerate>=0.34.0",
]
```

In `packages/engine-whisper/pyproject.toml`, the `dependencies` array currently is:

```toml
dependencies = [
    "resona-asr-core",
    "resona-engine-server",
    "openai-whisper>=20240930",
    "torch>=2.11.0.dev",
]
```

Change the `torch` line the same way:

```toml
dependencies = [
    "resona-asr-core",
    "resona-engine-server",
    "openai-whisper>=20240930",
    "torch>=2.10",
]
```

- [ ] **Step 3: Regenerate the lockfile**

Run: `uv lock`

Expected: completes without error and resolves `torch` to a **stable** version (no `.dev` suffix) from the cu130 index.

**If `uv lock` fails:**
- If the error mentions `triton` or `pytorch-triton`: stable torch declares its own `triton` (or `pytorch-triton`) dependency. Do **not** re-add the old override. Read the exact name/version the error reports and, only if uv genuinely cannot resolve it from PyPI, add a minimal `[tool.uv.sources]` entry for that exact package name pointing at `pytorch-cu130`. Re-run `uv lock`. Report precisely what you added.
- For any other resolution error you cannot resolve by adjusting the `torch` floor: STOP and report BLOCKED with the full error text.

- [ ] **Step 4: Sync and verify torch is stable + Blackwell-capable**

Run: `uv sync --all-packages --no-build-isolation-package openai-whisper`

Then verify the resolved torch:

```bash
uv run python -c "import torch; print('torch', torch.__version__); assert '.dev' not in torch.__version__, 'still a nightly build'; print('cuda available:', torch.cuda.is_available()); print('device:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NONE'); x = torch.randn(64, 64, device='cuda'); print('cuda matmul ok, sum=', float((x @ x).sum()))"
```

Expected output: a non-`.dev` torch version, `cuda available: True`, `device: NVIDIA GeForce RTX 5070 Ti`, and a `cuda matmul ok` line. The matmul line is the critical Blackwell check — it proves the stable `cu130` wheel ships `sm_120` kernels. If it errors with "no kernel image is available", the resolved torch version is too old for Blackwell — raise the `torch` floor in Step 2 (e.g. `>=2.11`) and re-lock.

- [ ] **Step 5: Run the touched packages' tests**

Run: `uv run pytest packages/engine-voxtral/tests/ packages/engine-whisper/tests/ packages/asr-core/tests/ -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml packages/engine-voxtral/pyproject.toml packages/engine-whisper/pyproject.toml uv.lock
git commit -m "feat: move engine-voxtral/engine-whisper to stable PyTorch (cu130)

PyTorch nightly is non-reproducible and cu128 is deprecated from the
stable build matrix. The RTX 5070 Ti is Blackwell (sm_120), which rules
out cu126; cu130 is the stable target and the host driver supports it.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Replace torchaudio with soxr in the [live] extra

**Files:**
- Modify: `apps/resona-cli/pyproject.toml`
- Modify: `apps/resona-cli/src/resona_cli/live_ui.py`
- Modify: `apps/resona-cli/src/resona_cli/main.py:50`
- Modify: `pyproject.toml` (workspace root — dev dependency group)
- Create: `apps/resona-cli/tests/test_live_resample.py`
- Modify: `uv.lock` (regenerated)

Follow TDD: write the failing test first, then implement.

- [ ] **Step 1: Swap the [live] extra dependency**

In `apps/resona-cli/pyproject.toml`, the `live` optional-dependency group currently is:

```toml
live = [
    "textual>=3.2.0",
    "sounddevice>=0.5.1",
    "soundfile>=0.13.1",
    "numpy>=2.1.3",
    "torchaudio>=2.11.0.dev",
    "resona-asr-core",
]
```

Replace the `torchaudio` line with `soxr`:

```toml
live = [
    "textual>=3.2.0",
    "sounddevice>=0.5.1",
    "soundfile>=0.13.1",
    "numpy>=2.1.3",
    "soxr>=0.5",
    "resona-asr-core",
]
```

- [ ] **Step 2: Add soxr to the workspace dev dependency group**

In the workspace-root `pyproject.toml`, the `[dependency-groups]` `dev` list currently ends with these TUI test deps:

```toml
    # TUI test deps — required so test_micrec runs in the workspace dev env
    # (these are optional in resona-cli, but workspace devs want full coverage)
    "textual>=3.2.0",
    "sounddevice>=0.5.1",
    "soundfile>=0.13.1",
    "numpy>=2.1.3",
]
```

Add `soxr` so `live_ui.py` (and its new test) can be imported in the workspace dev venv:

```toml
    # TUI test deps — required so test_micrec runs in the workspace dev env
    # (these are optional in resona-cli, but workspace devs want full coverage)
    "textual>=3.2.0",
    "sounddevice>=0.5.1",
    "soundfile>=0.13.1",
    "numpy>=2.1.3",
    "soxr>=0.5",
]
```

- [ ] **Step 3: Write the failing test**

Create `apps/resona-cli/tests/test_live_resample.py`:

```python
import numpy as np

from resona_cli import live_ui


def test_resample_converts_to_asr_rate():
    """A 1-second mic chunk resamples to ~1 second at the ASR rate."""
    assert live_ui.MIC_SAMPLE_RATE != live_ui.ASR_SAMPLE_RATE, (
        "test assumes differing rates (the default 44100 vs 16000)"
    )
    one_second = np.zeros(live_ui.MIC_SAMPLE_RATE, dtype=np.float32)
    out = live_ui._resample_to_asr(one_second)
    assert abs(len(out) - live_ui.ASR_SAMPLE_RATE) < 100
    assert out.dtype == np.float32


def test_resample_is_identity_when_rates_match(monkeypatch):
    """When the rates already match, the chunk is returned unchanged."""
    monkeypatch.setattr(live_ui, "_NEEDS_RESAMPLE", False)
    audio = np.arange(1000, dtype=np.float32)
    out = live_ui._resample_to_asr(audio)
    assert out is audio
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `uv run pytest apps/resona-cli/tests/test_live_resample.py -v`
Expected: FAIL — `AttributeError: module 'resona_cli.live_ui' has no attribute '_resample_to_asr'` (and the module may also still fail to import if torchaudio is gone — that is fixed in Step 5).

- [ ] **Step 5: Rewrite the resampling in live_ui.py**

In `apps/resona-cli/src/resona_cli/live_ui.py`:

(a) Add `import soxr` to the import block. The current top-of-file imports are:

```python
import os
import threading
import time
import queue
import numpy as np
```

Change to:

```python
import os
import threading
import time
import queue
import numpy as np
import soxr
```

(b) Replace the module-level resampler block. It currently reads (lines 29-36):

```python
# Pre-build resampler once if sample rates differ
_resampler = None
if MIC_SAMPLE_RATE != ASR_SAMPLE_RATE:
    try:
        import torchaudio
        _resampler = torchaudio.transforms.Resample(MIC_SAMPLE_RATE, ASR_SAMPLE_RATE)
    except ImportError:
        pass  # Will fall back to per-chunk import in _feed_audio_to_transcriber
```

Replace that entire block with:

```python
# Resample mic audio to the ASR sample rate when the two differ.
_NEEDS_RESAMPLE = MIC_SAMPLE_RATE != ASR_SAMPLE_RATE


def _resample_to_asr(audio_float: np.ndarray) -> np.ndarray:
    """Resample a mono float32 chunk from the mic rate to the ASR rate.

    Returns the input unchanged when the rates already match.
    """
    if not _NEEDS_RESAMPLE:
        return audio_float
    return soxr.resample(audio_float, MIC_SAMPLE_RATE, ASR_SAMPLE_RATE)
```

(c) Replace the per-chunk resampling logic. It currently reads (lines 195-213):

```python
                if not self.is_paused:
                    if chunk.shape[1] > 1:
                        chunk = chunk.mean(axis=1, keepdims=True)

                    audio_float = chunk.flatten().astype(np.float32)

                    if _resampler is not None:
                        import torch
                        audio_resampled = _resampler(torch.from_numpy(audio_float)).numpy()
                    elif MIC_SAMPLE_RATE != ASR_SAMPLE_RATE:
                        import torch
                        import torchaudio.functional as F
                        audio_resampled = F.resample(
                            torch.from_numpy(audio_float), MIC_SAMPLE_RATE, ASR_SAMPLE_RATE,
                        ).numpy()
                    else:
                        audio_resampled = audio_float

                    self._live_transcriber.add_audio(audio_resampled)
```

Replace that entire block with:

```python
                if not self.is_paused:
                    if chunk.shape[1] > 1:
                        chunk = chunk.mean(axis=1, keepdims=True)

                    audio_float = chunk.flatten().astype(np.float32)
                    audio_resampled = _resample_to_asr(audio_float)

                    self._live_transcriber.add_audio(audio_resampled)
```

After this edit, `live_ui.py` must contain no reference to `torch`, `torchaudio`, or `_resampler`. Verify with: `grep -nE "torch|_resampler" apps/resona-cli/src/resona_cli/live_ui.py` — expect no output.

- [ ] **Step 6: Update the [live] import guard in main.py**

In `apps/resona-cli/src/resona_cli/main.py`, line 50 currently is:

```python
    _require_extra("live", "textual", "sounddevice", "soundfile", "torchaudio", "resona_asr_core")
```

Change `"torchaudio"` to `"soxr"`:

```python
    _require_extra("live", "textual", "sounddevice", "soundfile", "soxr", "resona_asr_core")
```

- [ ] **Step 7: Regenerate the lockfile and sync**

Run: `uv lock`
Then: `uv sync --all-packages --no-build-isolation-package openai-whisper`
Expected: both succeed; `soxr` is now in the venv.

- [ ] **Step 8: Run the test to verify it passes**

Run: `uv run pytest apps/resona-cli/tests/test_live_resample.py -v`
Expected: PASS — 2 passed.

Also run the existing CLI tests to confirm no regression: `uv run pytest apps/resona-cli/tests/test_micrec.py -v` — expected PASS.

- [ ] **Step 9: Commit**

```bash
git add apps/resona-cli/pyproject.toml apps/resona-cli/src/resona_cli/live_ui.py apps/resona-cli/src/resona_cli/main.py pyproject.toml apps/resona-cli/tests/test_live_resample.py uv.lock
git commit -m "feat(cli): resample live mic audio with soxr instead of torchaudio

The [live] extra pulled torchaudio (and a full torch nightly) solely to
resample mic audio to the ASR sample rate. soxr does this in one call
with no torch dependency, making the live TUI completely torch-free.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Bump the engine Docker base images to CUDA 13

**Files:**
- Modify: `packages/engine-voxtral/Dockerfile`
- Modify: `packages/engine-whisper/Dockerfile`

Context: both Dockerfiles use `nvidia/cuda:12.8.0-…-ubuntu24.04` base images (a builder stage on `-devel`, a runtime stage on `-runtime`). The packages now install `cu130` torch wheels, so the base images should match CUDA 13. Do **not** touch `packages/engine-faster-whisper/Dockerfile` — that backend uses `cu12` CTranslate2 wheels and stays on CUDA 12.

- [ ] **Step 1: Bump the voxtral Dockerfile**

Read `packages/engine-voxtral/Dockerfile`. Replace **every** occurrence of `nvidia/cuda:12.8.0-` with `nvidia/cuda:13.0.1-` (this covers both the `-devel-ubuntu24.04` builder base and the `-runtime-ubuntu24.04` runtime base). Change nothing else.

- [ ] **Step 2: Bump the whisper Dockerfile**

Read `packages/engine-whisper/Dockerfile`. Replace **every** occurrence of `nvidia/cuda:12.8.0-` with `nvidia/cuda:13.0.1-`. Change nothing else.

- [ ] **Step 3: Verify the chosen image tags exist**

Run: `docker pull nvidia/cuda:13.0.1-runtime-ubuntu24.04 && docker pull nvidia/cuda:13.0.1-devel-ubuntu24.04`

Expected: both pull successfully. **If the `13.0.1` patch tag does not exist**, list available tags (`docker run --rm quay.io/skopeo/stable list-tags docker://nvidia/cuda 2>/dev/null | grep '13\.0.*ubuntu24.04'`, or check hub.docker.com/r/nvidia/cuda/tags) and use the latest available `13.0.x-…-ubuntu24.04` pair instead — apply the same tag in both Dockerfiles. Report which tag you used.

- [ ] **Step 4: Commit**

```bash
git add packages/engine-voxtral/Dockerfile packages/engine-whisper/Dockerfile
git commit -m "build: bump voxtral/whisper engine images to CUDA 13 base

Matches the cu130 stable PyTorch wheels these images now install.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Update justfile and CLAUDE.md

**Files:**
- Modify: `justfile`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update the justfile install recipes**

In `justfile`:

(a) `install-cli-whisper` and `install-cli-voxtral` each pass `--index https://download.pytorch.org/whl/nightly/cu128`. Change that URL in **both** recipes to the stable cu130 index: `--index https://download.pytorch.org/whl/cu130`.

(b) `install-cli-full` currently is:

```make
# Live TUI + local faster-whisper engine (live-from-mic, no server)
install-cli-full:
    uv tool install --force \
        --index https://download.pytorch.org/whl/nightly/cu128 \
        --from ./apps/resona-cli 'resona-cli[record,live,faster-whisper]'
```

Its three extras (`record`, `live`, `faster-whisper`) are now **all torch-free** (`record` always was; `live` is after Task 2; `faster-whisper` after the prior plan). Drop the `--index` line:

```make
# Live TUI + local faster-whisper engine (live-from-mic, no server)
install-cli-full:
    uv tool install --force --from ./apps/resona-cli 'resona-cli[record,live,faster-whisper]'
```

(c) The comment block above the `install-cli` recipes currently reads:

```
# The *-whisper / *-voxtral / *-full recipes pass --index for the torch
# nightly wheel since `uv tool install` does NOT inherit the workspace's
# pytorch-nightly index. If it still fails, use `just install` + `uv run resona`.
```

Replace it with:

```
# The *-whisper / *-voxtral recipes pass --index for the stable cu130 torch
# wheel since `uv tool install` does NOT inherit the workspace's pytorch
# index. If it still fails, use `just install` + `uv run resona`.
```

- [ ] **Step 2: Update CLAUDE.md**

In `CLAUDE.md`, search for every mention of `nightly`, `cu128`, and `pytorch-nightly` and update them to reflect the stable cu130 reality. Specifically:

(a) The install-personas warning block currently reads:

```
⚠️ The `[whisper]`/`[voxtral]` extras pull a torch nightly. `uv tool install` does NOT inherit the workspace's pytorch-nightly index, so these may fail to resolve torch. Workarounds:
- Stay inside the workspace and use `uv run resona <command>`.
- Or: `uv pip install --extra-index-url https://download.pytorch.org/whl/nightly/cu128 'resona-cli[whisper]'` into a managed venv.

The `[faster-whisper]` extra is torch-free — it uses CTranslate2 plus the `nvidia-cublas-cu12` / `nvidia-cudnn-cu12` wheels from PyPI — so `uv tool install ... 'resona-cli[faster-whisper]'` works without the nightly index.
```

Replace it with:

```
⚠️ The `[whisper]`/`[voxtral]` extras pull a stable PyTorch build from the cu130 index. `uv tool install` does NOT inherit the workspace's pytorch index, so these may fail to resolve torch. Workarounds:
- Stay inside the workspace and use `uv run resona <command>`.
- Or: `uv pip install --extra-index-url https://download.pytorch.org/whl/cu130 'resona-cli[whisper]'` into a managed venv.

The `[faster-whisper]` and `[live]` extras are torch-free — `[faster-whisper]` uses CTranslate2 plus the `nvidia-cublas-cu12` / `nvidia-cudnn-cu12` wheels, and `[live]` uses `soxr` for resampling — so `uv tool install` works for them without any extra index.
```

(b) The "Running in development" section's install command (`uv sync --all-packages --no-build-isolation-package openai-whisper`) is unchanged — verify it still appears and needs no edit.

(c) If `CLAUDE.md` mentions a `pytorch-nightly` index name or the `cu128` URL anywhere else (e.g. an environment/configuration note), update the name to `pytorch-cu130` and the URL to `https://download.pytorch.org/whl/cu130`. If there are no other mentions, no further edit is needed.

- [ ] **Step 3: Commit**

```bash
git add justfile CLAUDE.md
git commit -m "docs: stable cu130 torch index; live/full extras are torch-free

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Verification

These checks cannot be unit-tested — the executor must run them and report results.

- [ ] **Step 1: Full test suite**

Run: `uv run pytest`
Expected: same collection as before, all passing **except** the 3 known pre-existing `apps/resona-cli/tests/test_backends.py` failures (`test_list_no_backends`, `test_add_backend`, `test_add_backend_with_key`). Any *other* failure is a regression — investigate before proceeding.

- [ ] **Step 2: Confirm no nightly torch remains**

Run: `uv run python -c "import torch; print(torch.__version__)"`
Expected: a stable version with no `.dev` suffix.

Run: `grep -rn "nightly\|2.11.0.dev\|cu128" pyproject.toml packages/*/pyproject.toml apps/resona-cli/pyproject.toml justfile`
Expected: no output (no nightly/cu128 references remain in any pyproject or the justfile).

- [ ] **Step 3: GPU smoke test — voxtral and whisper backends (the critical Blackwell check)**

On the CUDA host, verify each torch-backed engine actually transcribes on the RTX 5070 Ti with stable cu130 torch. Use tiny models to keep it fast:

```bash
uv run python -c "
from resona_asr_core.audio import load_audio
from resona_engine_whisper.transcriber import WhisperTranscriber
with open('data/files/b42222f5357169762a7e.wav','rb') as f:
    audio = load_audio(f)
t = WhisperTranscriber(device='cuda', modelname='tiny')
print('whisper transcript:', repr(t.transcribe(audio, language='de')['text'][:120]))
print('WHISPER CUDA OK')
"
```

```bash
DEFAULT_VOXTRAL_MODEL=openai/whisper-tiny uv run python -c "
from resona_asr_core.audio import load_audio
from resona_engine_voxtral.transcriber import VoxtralTranscriber
with open('data/files/b42222f5357169762a7e.wav','rb') as f:
    audio = load_audio(f)
t = VoxtralTranscriber(device='cuda', modelname='openai/whisper-tiny')
print('voxtral transcript:', repr(t.transcribe(audio, language='de')['text'][:120]))
print('VOXTRAL CUDA OK')
"
```

Expected: both print a transcript and an `… CUDA OK` line, with no "no kernel image is available for execution on the device" error. This proves stable `cu130` torch runs on the Blackwell GPU. If a kernel-image error appears, the resolved torch is too old — raise the `torch>=2.10` floor (Task 1 Step 2) and re-lock.

- [ ] **Step 4: Docker builds**

Run both engine image builds:

```bash
docker build -f packages/engine-voxtral/Dockerfile -t resona-voxtral-test .
docker build -f packages/engine-whisper/Dockerfile -t resona-whisper-test .
```

Expected: both succeed. Then confirm each image got a stable, non-nightly torch:

```bash
docker run --rm --entrypoint python3.12 resona-voxtral-test -c "import torch; print('voxtral image torch', torch.__version__); assert '.dev' not in torch.__version__"
docker run --rm --entrypoint python3.12 resona-whisper-test -c "import torch; print('whisper image torch', torch.__version__); assert '.dev' not in torch.__version__"
```

Expected: both print a non-`.dev` torch version. Afterwards remove the test images: `docker rmi resona-voxtral-test resona-whisper-test`.

- [ ] **Step 5: Report**

Summarize: test-suite result (and confirmation only the 3 known failures remain), resolved stable torch version, both GPU smoke-test results, and both Docker build results. If no GPU host was available for Step 3, state that explicitly and mark the Blackwell smoke test as outstanding.

---

## Self-Review

**Spec coverage:** The request was "stabilize the pytorch dependency in `live`, `engine-voxtral`, and `engine-whisper`" — (1) engine-voxtral + engine-whisper move to stable cu130 [Task 1]; (2) `[live]` becomes torch-free via soxr [Task 2]; supporting changes: Docker base images [Task 3], install recipes + docs [Task 4], end-to-end verification including the Blackwell GPU check [Task 5]. All covered.

**Placeholder scan:** Every code/config step shows the exact before/after text. The only conditional branches (Task 1 Step 3 triton handling, Task 1 Step 4 / Task 5 Step 3 kernel-image fallback, Task 3 Step 3 image-tag fallback) each give a concrete resolution procedure rather than a vague placeholder.

**Type/name consistency:** `_resample_to_asr` and `_NEEDS_RESAMPLE` are defined in Task 2 Step 5, imported/used in the callback in the same step, and referenced by name in the Task 2 Step 3 test and Task 2 Step 5(c). The index name `pytorch-cu130` and URL `https://download.pytorch.org/whl/cu130` are used consistently across Task 1, Task 4, and Task 5. The `torch>=2.10` floor is identical in both engine pyprojects.
