# Resona Architecture Redesign

**Date:** 2026-04-03
**Status:** Draft
**Scope:** Full rename from whisper-server to resona, modular transcription backends, shared postprocessing library with LLM support, updated local engine fallback.

## 1. Goals

1. **Modular transcription backends** — formalize the transcriber interface, make adding new backends (voxtral, future models) trivial via Python entry points. One backend per Docker image.
2. **Modular postprocessing** — extract postprocessing from the engine into a shared library usable by both the API (from DB) and CLI (from local config). Add LLM-based postprocessing alongside static replacements.
3. **Rename to Resona** — the project has outgrown "whisper-server". All packages, modules, env vars, config paths, and CLI commands get renamed.
4. **Preserve auto-start** — CLI can still spawn a local engine when no remote backend is reachable, now with backend selection.

## 2. Naming

| Old | New | Python module |
|-----|-----|---------------|
| whisper-server/ | resona/ | — |
| ws-engine | resona-engine-core | resona_engine_core |
| — | resona-engine-faster-whisper | resona_engine_faster_whisper |
| — | resona-engine-voxtral | resona_engine_voxtral |
| — | resona-engine-whisper | resona_engine_whisper |
| ws-api | resona-api | resona_api |
| ws-client | resona-client | resona_client |
| ws-cli | resona-cli | resona_cli |
| — | resona-postprocess | resona_postprocess |
| ~/.whisper-server/ | ~/.resona/ | config dir |

### Environment variables

| Old | New | Purpose |
|-----|-----|---------|
| WS_API_URL | RESONA_API_URL | API server URL |
| WS_API_KEY | RESONA_API_KEY | API auth key |
| ENGINE_API_KEY | RESONA_ENGINE_KEY | Engine auth key |
| ENGINE_URL | RESONA_ENGINE_URL | Engine URL (used by API) |
| ASR_MODE | RESONA_BACKEND | Backend selection |
| — | RESONA_LLM_MODEL | Default LLM model for postprocessing |
| — | RESONA_LLM_API_BASE | Custom LLM endpoint (e.g., Ollama) |

Standard provider keys (OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.) are read natively by litellm.

### CLI command

`ws-cli` → `resona`

All subcommands unchanged: `resona batch`, `resona watch`, `resona rec`, `resona live`, `resona ui`, `resona backends`, `resona replacements`, `resona prompts`.

## 3. Package Structure

```
resona/
├── pyproject.toml                    ← workspace root
├── uv.lock
├── docker-compose.yml
│
├── apps/
│   ├── cli/                          ← resona-cli
│   │   └── src/resona_cli/
│   │       ├── main.py
│   │       ├── local_engine.py
│   │       ├── batch.py
│   │       ├── watch.py
│   │       └── ...
│   └── web/                          ← browser UI
│
└── packages/
    ├── engine-core/                  ← resona-engine-core
    │   └── src/resona_engine_core/
    │       ├── app.py                  FastAPI app + /transcribe + /health + WS endpoints
    │       ├── protocol.py             Transcriber Protocol
    │       ├── registry.py             entry-point discovery + singleton
    │       ├── audio.py                load_audio()
    │       ├── live_transcriber.py     VAD + local agreement
    │       └── auth.py
    │
    ├── engine-faster-whisper/        ← resona-engine-faster-whisper
    │   ├── src/resona_engine_faster_whisper/
    │   │   └── transcriber.py
    │   ├── pyproject.toml
    │   └── Dockerfile                  nvidia/cuda base
    │
    ├── engine-voxtral/               ← resona-engine-voxtral
    │   ├── src/resona_engine_voxtral/
    │   │   └── transcriber.py
    │   ├── pyproject.toml
    │   └── Dockerfile
    │
    ├── engine-whisper/               ← resona-engine-whisper
    │   ├── src/resona_engine_whisper/
    │   │   └── transcriber.py
    │   ├── pyproject.toml
    │   └── Dockerfile
    │
    ├── postprocess/                  ← resona-postprocess
    │   └── src/resona_postprocess/
    │       ├── replacements.py         apply_replacements() — static regex
    │       ├── llm.py                  llm_postprocess() via litellm
    │       ├── pipeline.py             PostprocessPipeline: composable str→str chain
    │       └── sources.py              load from file or build from DB records
    │
    ├── api/                          ← resona-api
    │   ├── src/resona_api/
    │   │   └── ...                     same structure, uses resona-postprocess
    │   └── Dockerfile
    │
    └── client/                       ← resona-client
        └── src/resona_client/
            ├── client.py
            └── config.py               ~/.resona/config.json
```

## 4. Transcriber Protocol

All backends implement this interface, defined in `resona_engine_core/protocol.py`:

```python
from typing import Protocol, TypedDict, runtime_checkable
import numpy as np

class TranscriptionResult(TypedDict):
    text: str
    language: str
    segments: list[dict]

@runtime_checkable
class Transcriber(Protocol):
    def transcribe(
        self,
        audio: np.ndarray,
        *,
        language: str = "de",
        task: str = "transcribe",
        initial_prompt: str | None = None,
        word_timestamps: bool = False,
        vad_filter: bool = False,
    ) -> TranscriptionResult:
        ...
```

- `@runtime_checkable` allows startup validation with `isinstance()`
- Keyword-only args after `audio` — backends ignore what they don't support
- Backends should accept `**kwargs` for forward compatibility

### Constructor contract

```python
def __init__(self, device: str, modelname: str | None = None): ...
```

`device` is "cuda" or "cpu" (auto-detected by registry). `modelname` comes from backend-specific env vars (e.g., `DEFAULT_FASTWHISPER_MODEL`).

## 5. Backend Discovery via Entry Points

Each backend registers itself in its `pyproject.toml`:

```toml
# packages/engine-voxtral/pyproject.toml
[project.entry-points."resona.backends"]
voxtral = "resona_engine_voxtral.transcriber:VoxtralTranscriber"

[project.scripts]
resona-engine-voxtral = "resona_engine_core.app:main"
```

The registry in `resona_engine_core/registry.py`:

```python
from importlib.metadata import entry_points

_transcriber: Transcriber | None = None
_lock = threading.Lock()

def get_transcriber(backend: str | None = None) -> Transcriber:
    global _transcriber
    if _transcriber is None:
        with _lock:
            if _transcriber is None:
                _transcriber = _load_from_entrypoint(backend)
    return _transcriber

def _load_from_entrypoint(backend: str | None = None) -> Transcriber:
    name = backend or config("RESONA_BACKEND", default="faster-whisper")
    eps = entry_points(group="resona.backends")
    for ep in eps:
        if ep.name == name:
            cls = ep.load()
            instance = cls(device=_detect_device())
            assert isinstance(instance, Transcriber)
            return instance
    raise ValueError(
        f"Backend '{name}' not found. Installed: {[e.name for e in eps]}"
    )
```

Each backend package's `[project.scripts]` points to `resona_engine_core.app:main`. When you run `uv run resona-engine-voxtral`, the core FastAPI app starts and `get_transcriber()` discovers the voxtral backend via the entry point that was registered by installing the package.

## 6. Postprocessing Library

### 6.1 Static replacements

`resona_postprocess/replacements.py` — moved from ws-engine, unchanged logic:

```python
def apply_replacements(text: str, replacements: list[dict[str, str]]) -> str:
    for r in replacements:
        text = re.sub(r["name"], r["replacement"], text, flags=re.IGNORECASE)
    return text
```

### 6.2 LLM postprocessing

`resona_postprocess/llm.py` — uses litellm for provider abstraction:

```python
import litellm
from decouple import config

def llm_postprocess(
    text: str,
    *,
    prompt: str,
    model: str | None = None,
    api_base: str | None = None,
) -> str:
    model = model or config("RESONA_LLM_MODEL", default="gpt-4o-mini")
    api_base = api_base or config("RESONA_LLM_API_BASE", default="") or None

    response = litellm.completion(
        model=model,
        api_base=api_base,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ],
    )
    return response.choices[0].message.content
```

litellm supports OpenAI, Anthropic, Mistral, Ollama, vLLM, llama.cpp, LM Studio, and 100+ other providers. The model string selects the provider: `ollama/llama3`, `gpt-4o`, `claude-sonnet-4-20250514`, etc. API keys are read from standard env vars (OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.).

### 6.3 Pipeline

`resona_postprocess/pipeline.py`:

```python
from typing import Callable

PostprocessStep = Callable[[str], str]

class PostprocessPipeline:
    def __init__(self):
        self._steps: list[tuple[str, PostprocessStep]] = []

    def add(self, name: str, step: PostprocessStep) -> "PostprocessPipeline":
        self._steps.append((name, step))
        return self

    def run(self, text: str) -> str:
        for name, step in self._steps:
            text = step(text)
        return text
```

### 6.4 Sources

`resona_postprocess/sources.py` — builds pipelines from config files or DB records:

```python
def load_replacements_from_file(path: Path | None = None) -> list[dict[str, str]]:
    path = path or Path.home() / ".resona" / "replacements.json"
    if not path.exists():
        return []
    return json.loads(path.read_text())

def build_pipeline_from_config(config_path: Path | None = None) -> PostprocessPipeline:
    config_path = config_path or Path.home() / ".resona" / "postprocess.json"
    if not config_path.exists():
        # Backward compat: try bare replacements.json
        rules = load_replacements_from_file()
        if rules:
            pipeline = PostprocessPipeline()
            pipeline.add("replacements", lambda t: apply_replacements(t, rules))
            return pipeline
        return PostprocessPipeline()

    cfg = json.loads(config_path.read_text())
    pipeline = PostprocessPipeline()

    for step in cfg.get("steps", []):
        if step["type"] == "replacements":
            rules = load_replacements_from_file(step.get("source"))
            pipeline.add("replacements", lambda t, r=rules: apply_replacements(t, r))
        elif step["type"] == "llm":
            prompt = step["prompt"]
            model = step.get("model")
            pipeline.add(
                step.get("name", "llm"),
                lambda t, p=prompt, m=model: llm_postprocess(t, prompt=p, model=m),
            )

    return pipeline
```

### 6.5 Config file format

`~/.resona/postprocess.json`:

```json
{
  "steps": [
    {
      "type": "replacements",
      "source": "replacements.json"
    },
    {
      "type": "llm",
      "name": "format-medical",
      "prompt": "Format this medical transcription with proper paragraphs, headings, and punctuation. Do not change the content.",
      "model": "ollama/llama3"
    }
  ]
}
```

`~/.resona/replacements.json`:

```json
[
  { "name": "\\behr\\b", "replacement": "Ehr" },
  { "name": "mfg", "replacement": "Mit freundlichen Grüßen" }
]
```

### 6.6 Server-side usage

resona-api uses the same `PostprocessPipeline` and `llm_postprocess()` but builds the pipeline from DB records instead of JSON files. LLM postprocess prompts become a new DB table, manageable via API endpoints and CLI commands. The existing `InitialPrompt` table (pre-transcription vocabulary hints) stays separate from postprocess prompts.

### 6.7 Key change: engine no longer does replacements

The engine returns raw `text` only. The `md` field is computed caller-side:

| Before | After |
|--------|-------|
| Engine applies replacements in `/transcribe` | Engine returns raw text only |
| ws-api sends replacements JSON to engine | resona-api applies replacements itself |
| CLI local fallback: no replacements | CLI reads ~/.resona/ config |
| WebSocket paths: no replacements | Callers can apply pipeline to WS results |
| `md` field computed in engine | `md` field computed caller-side |

## 7. Local Engine & Backend Auto-Start

### 7.1 LocalEngine class

Updated to accept a backend name and derive the package to spawn:

```python
class LocalEngine:
    def __init__(
        self,
        backend: str = "faster-whisper",
        model: str | None = None,
        timeout: float = 120.0,
    ):
        self.backend = backend
        self._package = f"resona-engine-{backend}"

    def __enter__(self):
        self._process = subprocess.Popen(
            ["uv", "run", self._package],
            env={**os.environ, "PORT": str(self._port)},
            ...
        )
        self._wait_for_health()
        return self
```

### 7.2 Backend resolution order

1. `--backend` CLI flag (highest priority)
2. `default_backend` in `~/.resona/config.json`
3. Hardcoded default: `"faster-whisper"`

### 7.3 Full resolution flow

1. Try configured remote backends (GET /health)
2. If unreachable, try auto-start (SSH tunnel / docker compose)
3. If still unreachable, fall back to local engine:
   - Resolve backend name (flag → config → default)
   - Spawn `uv run resona-engine-{backend}`
   - Poll /health until ready
4. Transcribe via HTTP to local engine
5. Apply postprocessing pipeline from local config

### 7.4 Config layout

```
~/.resona/
├── config.json          ← remote backends + default_backend
├── replacements.json    ← static replacement rules
└── postprocess.json     ← full pipeline: replacements + LLM steps
```

If `postprocess.json` doesn't exist, the CLI falls back to just `replacements.json` for backward compatibility.

## 8. Docker Images

Each backend has its own Dockerfile in its package directory. Images are self-contained.

```
packages/engine-faster-whisper/Dockerfile   ← nvidia/cuda:12.8.0-runtime-ubuntu24.04
packages/engine-voxtral/Dockerfile          ← base TBD per model requirements
packages/engine-whisper/Dockerfile          ← nvidia/cuda base
packages/api/Dockerfile                     ← python:3.12-slim (unchanged)
```

Each Dockerfile:
- Uses the workspace root as build context (for uv workspace resolution)
- Copies only its own package + engine-core + shared deps
- Installs only the backend's dependencies
- Sets `RESONA_BACKEND` at build time (though entry-point discovery makes this optional)

## 9. Data Flow

### Server path

```
Client → POST /jobs → resona-api
  saves file, creates PENDING job

TranscribeTask (background)
  fetches initial_prompt from DB
  POSTs audio + initial_prompt to engine (no replacements sent)
  ↓
resona-engine-core + backend
  transcribes → {text, language, segments}
  ↓
resona-api
  builds PostprocessPipeline from DB (replacements + LLM prompts)
  md = pipeline.run(text)
  stores text + md + segments in job
  ↓
Client → GET /job/{id} → COMPLETED
```

### Local path

```
resona batch ./audio/ --backend voxtral
  no server reachable
  reads ~/.resona/config.json → default_backend
  --backend flag overrides
  spawns: uv run resona-engine-voxtral
  ↓
resona-engine-core + voxtral backend
  transcribes → {text, language, segments}
  ↓
resona-cli
  builds PostprocessPipeline from ~/.resona/postprocess.json
  md = pipeline.run(text)
  writes output to disk
```

## 10. Migration Path

This is a breaking change. The migration order should be:

1. Create resona-engine-core with Protocol, registry, app, audio loading
2. Create resona-postprocess with replacements, llm, pipeline, sources
3. Migrate existing transcriber implementations to separate backend packages
4. Update resona-api to use resona-postprocess (remove replacement sending to engine)
5. Update resona-client with new config paths and env vars
6. Update resona-cli with backend selection and local postprocessing
7. Rename all modules, env vars, config paths
8. Update Dockerfiles — one per backend
9. Update documentation and CLAUDE.md

## 11. Out of Scope

- Multi-model-per-process (registry loads one backend per process)
- Streaming LLM postprocessing (future enhancement)
- Web UI changes (uses API, unaffected by internal restructuring)
- Database migration tooling (SQLite schema unchanged, just table/column names)
