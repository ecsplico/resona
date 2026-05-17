# Local-Only Mode

Local-only mode lets you transcribe audio without running any server. The CLI handles everything in the same process (or a subprocess), reads postprocessing config from `~/.resona/`, and writes output files directly.

## What works in local-only mode

| Operation | Local-only | Requires server |
|-----------|-----------|-----------------|
| `resona transcribe` | Yes | — |
| `resona transcribe --engine <cloud>` | Yes (key from env) | — |
| `resona rec` | Yes | — |
| `resona live` | Yes | — |
| `resona watch` | No | resona-api (:7000) |
| Job queue (`GET /jobs`) | No | resona-api (:7000) |
| Profiles CRUD | No | resona-api (:7000) |

## How it activates

When you run `resona transcribe`, the CLI tries to reach resona-api at `RESONA_API_URL` (default `http://localhost:7000`). If the server is not reachable, it falls back automatically — no flag needed.

You can also force local-only by not starting any server, or by pointing `RESONA_API_URL` at a non-existent host.

## Engine resolution order

When running locally, the engine is selected in this order:

1. `--engine NAME` CLI flag — highest priority
2. `default_engine` in `~/.resona/config.json`
3. Hardcoded default: `faster-whisper`

`--engine` accepts a built-in engine name (`faster-whisper`, `whisper`, `voxtral`), a named server entry from `config.json`, or a cloud provider name (`deepgram`, `elevenlabs`, `openai`).

```bash
resona transcribe recording.mp3                         # faster-whisper (default)
resona transcribe recording.mp3 --engine whisper        # OpenAI Whisper (PyTorch)
resona transcribe recording.mp3 --engine deepgram       # cloud, key from env
```

!!! tip "Private engines"
    Pass `--private` to restrict to engines marked `"private": true` in `config.json`. Cloud engines are never considered private. Set `"default_private": true` in `config.json` to make this the default.

## InProcessEngine vs subprocess

The CLI has two ways to run a local engine:

- **InProcessEngine** — imports and calls the engine directly in the same Python process. Used by default when the engine's package is installed (it always is in the default install, which bundles faster-whisper).
- **Subprocess fallback** (`LocalEngine`) — spawns `uv run resona-engine-<name>` on a free port, waits for `/health` to respond, then sends HTTP requests to it. Used automatically if the engine package is not importable (e.g., you asked for `whisper` but the `[whisper]` extra is not installed).

You do not need to configure this — the CLI picks the right path automatically.

## Output files

By default, each transcript is written as a `.txt` file next to the input:

```
recordings/
├── note-2026-05-17.mp3
└── note-2026-05-17.txt
```

Override the output location with `--output-dir`:

```bash
resona transcribe ./recordings/ --output-dir ./transcripts/
```

## Postprocessing in local-only mode

Postprocessing is applied after the engine returns raw text. The pipeline is read from (in order):

1. `~/.resona/postprocess.json` — full pipeline config (replacements + optional LLM steps)
2. `~/.resona/replacements.json` — replacements-only override
3. Bundled defaults — German dictation replacements (Komma → `,`, Punkt → `.`, Absatz → newline, medical headings, etc.)

If none of those files exist, the bundled defaults are used automatically.

Example `~/.resona/replacements.json`:

```json
[
  {"pattern": "\\bKomma\\b", "replacement": ","},
  {"pattern": "\\bPunkt\\b", "replacement": "."}
]
```

For a full pipeline with LLM formatting, use `~/.resona/postprocess.json`:

```json
{
  "steps": [
    {"type": "replacements", "source": "replacements.json"},
    {"type": "llm", "name": "format", "prompt": "Format this medical text.", "model": "ollama/llama3"}
  ]
}
```

Relative `source` paths resolve relative to `~/.resona/`.

See [Postprocessing](../guide/postprocessing.md) for full pipeline documentation.
