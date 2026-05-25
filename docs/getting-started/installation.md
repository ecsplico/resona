# Installation

## Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.12+ | Required by all packages |
| [uv](https://docs.astral.sh/uv/) | latest | Package manager and workspace runner |
| ffmpeg | any recent | Must be on `PATH`; used for audio decoding |
| NVIDIA GPU + CUDA | 12.8+ | Required for local engine only; not needed for cloud-only or API-only use |

## Workspace development install

Clone the repo and sync all packages in one step:

```bash
git clone https://github.com/ecsplico/resona.git
cd resona
uv sync --all-packages
```

The legacy `openai-whisper` package does not declare its build-time dependencies; the workspace root's `[tool.uv.extra-build-dependencies]` block supplies `setuptools` and `wheel` so `uv sync` succeeds without a manual `--no-build-isolation-package` flag.

Run commands through `uv run` while inside the workspace:

```bash
uv run resona transcribe recording.mp3
uv run resona-engine-faster-whisper   # starts engine server on :7001
uv run resona-api                     # starts API server on :7000
```

## Install personas (uv tool)

If you want an installed `resona` command outside the workspace, pick a persona:

| Persona | Command |
|---------|---------|
| Default (record, live, local faster-whisper) | `uv tool install --from ./apps/resona-cli resona-cli` |
| Default + Whisper (PyTorch) engine | `uv tool install --from ./apps/resona-cli 'resona-cli[whisper]'` |
| Default + Voxtral (PyTorch) engine | `uv tool install --from ./apps/resona-cli 'resona-cli[voxtral]'` |

The **default install is torch-free**: it bundles faster-whisper via CTranslate2, so `uv tool install` completes without a GPU index.

!!! warning "PyTorch extras and the cu130 index"
    The `[whisper]` and `[voxtral]` extras depend on PyTorch from the cu130 index. `uv tool install` does **not** inherit the workspace's index configuration, so resolution may fail.

    Workarounds:

    - Stay inside the workspace and use `uv run resona <command>` — the workspace index is always active.
    - Or install into a managed venv directly:

      ```bash
      uv pip install \
        --extra-index-url https://download.pytorch.org/whl/cu130 \
        'resona-cli[whisper]'
      ```

## macOS

The default persona (`faster-whisper`) is CPU-only and torch-free — it works out of the box on Apple Silicon and Intel Macs:

```bash
uv tool install --from ./apps/resona-cli resona-cli
resona transcribe recording.mp3
```

The `[whisper]` and `[voxtral]` extras pull PyTorch from a CUDA-Linux wheel index (`pytorch-cu130`) that has no macOS wheels — installing them straight will fail. Install PyTorch from PyPI first, then add the extra without the CUDA index:

```bash
uv tool install --from ./apps/resona-cli --with torch 'resona-cli[whisper]'
# or, for an existing tool environment
uv tool install --reinstall --from ./apps/resona-cli --with torch 'resona-cli[whisper]'
```

This installs the default PyPI torch wheel, which on Apple Silicon ships with **MPS** (Metal Performance Shaders) support. Resona's `_detect_device()` picks `cuda` → `mps` → `cpu`, so MPS is selected automatically when CUDA is not available and torch reports `mps.is_available()`.

!!! note "No CUDA on Mac"
    macOS has no CUDA support — the cu130 wheels would not work even if they were published for Mac. The MPS path is the only GPU acceleration on Apple Silicon.

## `uv run resona` vs installed `resona`

| | `uv run resona` (workspace) | `resona` (installed tool) |
|---|---|---|
| Source | Editable workspace packages | Copied into tool environment |
| Code changes | Picked up immediately | Requires re-install |
| PyTorch extras | Resolved via workspace index | May require manual index flag |
| Recommended for | Development | End-user deployment |
