# Config Files

Resona reads three optional config files from `~/.resona/`. None are required — sensible defaults apply when they are absent.

```
~/.resona/
├── config.json          # engines, auto-start settings, default_engine, default_private
├── replacements.json    # override default text replacement rules
└── postprocess.json     # full pipeline: replacements + LLM steps
```

## config.json

Controls engine selection for the `resona` CLI. Defines which resona-api servers and cloud providers are available, which one is the default, and whether private-only mode is active.

### Annotated example

```json
{
  "default_engine": "faster-whisper",
  "default_private": false,
  "engines": [
    {
      "name": "gpu-server",
      "type": "resona-api",
      "url": "http://gpu-machine:7000",
      "private": true
    },
    {
      "name": "deepgram",
      "type": "cloud",
      "provider": "deepgram"
    },
    {
      "name": "deepgram-nova",
      "type": "cloud",
      "provider": "deepgram",
      "model": "nova-2"
    }
  ]
}
```

### Top-level fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `default_engine` | string | `"faster-whisper"` | Engine name used when `--engine` is not passed on the CLI |
| `default_private` | boolean | `false` | When `true`, `resona transcribe` refuses non-private engines by default — equivalent to always passing `--private` |
| `engines` | array | `[]` | Ordered list of engine entries |

### Engine entry fields — `type: "resona-api"`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Identifier used with `--engine <name>` |
| `type` | string | yes | Must be `"resona-api"` |
| `url` | string | yes | Base URL of the resona-api server |
| `private` | boolean | no | When `true`, this engine is included when `--private` is required |
| `ssh` | string | no | SSH tunnel target, e.g. `"user@server.example.com"`. The CLI opens the tunnel before connecting. |

### Engine entry fields — `type: "cloud"`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Identifier used with `--engine <name>` |
| `type` | string | yes | Must be `"cloud"` |
| `provider` | string | yes | Cloud provider: `"deepgram"`, `"elevenlabs"`, or `"openai"` |
| `model` | string | no | Override the provider default model |
| `options` | object | no | Extra provider-specific options passed at call time |

!!! warning "API keys are never stored in config.json"
    Cloud provider API keys are always read from environment variables at call time (`DEEPGRAM_API_KEY`, `ELEVENLABS_API_KEY`, `OPENAI_API_KEY`). Do not put keys in `config.json`.

### Engine resolution order

When `resona transcribe` runs, it resolves the engine in this order:

1. `--engine NAME` CLI flag — matches a built-in local engine name (`faster-whisper`, `whisper`, `voxtral`), a `resona-api` entry, or a `cloud` entry (highest priority)
2. `--private` / `--no-private` — when private is required (via flag or `default_private: true`), non-private and cloud engines are skipped
3. `default_engine` in `config.json`
4. Hardcoded fallback: `"faster-whisper"`

## replacements.json

A simple list of regex-based text substitutions applied after transcription. This file overrides the bundled German dictation defaults.

### Format

```json
[
  {"name": "\\s*Komma", "replacement": ","},
  {"name": "\\s*Punkt", "replacement": "."},
  {"name": "\\s*Absatz", "replacement": "\n"},
  {"name": "Monique", "replacement": "Monic"}
]
```

Each object has two fields:

| Field | Description |
|-------|-------------|
| `name` | Regular expression pattern (Python `re` syntax, case-insensitive) |
| `replacement` | Replacement string; supports `\n` for newlines and standard regex back-references |

Patterns are applied in order. Matching is case-insensitive.

### Override semantics

If `~/.resona/replacements.json` exists, it **replaces** the bundled `default_replacements.json` entirely. There is no merging. To extend the defaults, copy `default_replacements.json` from `packages/postprocess/src/resona_postprocess/default_replacements.json` into `~/.resona/replacements.json` and add your entries.

This file has no effect when `~/.resona/postprocess.json` exists and does not include a `replacements` step that references it.

## postprocess.json

Defines a full postprocessing pipeline as an ordered list of steps. Use this when you need more than simple replacements — for example, an LLM formatting step after replacements.

### Format

```json
{
  "steps": [
    {"type": "replacements", "source": "replacements.json"},
    {"type": "llm", "name": "format", "prompt": "Format this medical text.", "model": "ollama/llama3"}
  ]
}
```

### Step types

**`replacements`**

| Field | Required | Description |
|-------|----------|-------------|
| `type` | yes | `"replacements"` |
| `source` | yes | Path to a replacements JSON file. Relative paths resolve relative to `~/.resona/`. |

**`llm`**

| Field | Required | Description |
|-------|----------|-------------|
| `type` | yes | `"llm"` |
| `name` | no | Human-readable label for this step |
| `prompt` | yes | System prompt sent to the LLM along with the current text |
| `model` | no | litellm model name, e.g. `"gpt-4o-mini"`, `"ollama/llama3"`. Defaults to `RESONA_LLM_MODEL`. |

!!! tip "Relative paths in source"
    The `source` field in a `replacements` step resolves relative to `~/.resona/`. So `"source": "replacements.json"` reads `~/.resona/replacements.json`. You can also use an absolute path.

### Config resolution order

The postprocess pipeline is built from the first file found:

1. `~/.resona/postprocess.json` — full pipeline definition (takes priority)
2. `~/.resona/replacements.json` — replacements-only pipeline
3. Bundled `default_replacements.json` — German dictation defaults (Komma, Punkt, Absatz, medical headings)

If none of these files exist, the bundled defaults are active automatically and no configuration is needed.
