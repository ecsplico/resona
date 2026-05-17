# Postprocessing

After the ASR engine returns raw text, Resona applies a composable postprocessing pipeline. Each
step is a `str → str` transformation. Steps run in order, each receiving the output of the
previous one.

---

## The pipeline concept

A pipeline consists of one or more steps:

- **`replacements`** — apply a set of regex rules from a JSON file
- **`llm`** — send the text to a language model for reformatting or correction

Steps are defined in `~/.resona/postprocess.json`. If that file does not exist, Resona falls back
to simpler sources (see [Config resolution](#config-resolution)).

---

## `postprocess.json` format

```json
{
  "steps": [
    {
      "type": "replacements",
      "source": "replacements.json"
    },
    {
      "type": "llm",
      "name": "format",
      "prompt": "Format this medical dictation as a structured clinical note. Preserve all clinical content.",
      "model": "ollama/llama3"
    }
  ]
}
```

The file lives at `~/.resona/postprocess.json`. Relative paths in `source` resolve relative to
`~/.resona/`.

---

## Step types

### replacements

Apply regex-based text replacements from a JSON file.

| Field | Required | Description |
|-------|----------|-------------|
| `type` | yes | `"replacements"` |
| `source` | yes | Path to the replacements JSON file |

```json
{"type": "replacements", "source": "replacements.json"}
```

The referenced file must be a JSON array in the format described in
[Text Replacements](replacements.md). Matching is case-insensitive.

### llm

Send the text to a language model via [litellm](https://github.com/BerriAI/litellm). litellm
provides a unified interface to 100+ providers.

| Field | Required | Description |
|-------|----------|-------------|
| `type` | yes | `"llm"` |
| `name` | no | Human-readable label for this step |
| `prompt` | yes | System prompt sent to the model |
| `model` | no | litellm model string (overrides `RESONA_LLM_MODEL`) |
| `api_base` | no | Custom API endpoint (overrides `RESONA_LLM_API_BASE`) |

```json
{
  "type": "llm",
  "name": "format",
  "prompt": "You are a medical transcription assistant. Reformat the following dictation as a structured clinical note with Markdown headings.",
  "model": "gpt-4o-mini"
}
```

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `RESONA_LLM_MODEL` | `gpt-4o-mini` | Default model for all `llm` steps that do not specify `model` |
| `RESONA_LLM_API_BASE` | (none) | Custom API base URL for all `llm` steps (e.g. local Ollama) |

---

## LLM provider examples

litellm's model string format determines which provider is used.

### OpenAI

```json
{
  "type": "llm",
  "prompt": "Format this clinical dictation.",
  "model": "gpt-4o-mini"
}
```

Requires `OPENAI_API_KEY` in the environment.

### Anthropic Claude

```json
{
  "type": "llm",
  "prompt": "Format this clinical dictation.",
  "model": "anthropic/claude-3-5-haiku-20241022"
}
```

Requires `ANTHROPIC_API_KEY` in the environment.

### Local Ollama

```json
{
  "type": "llm",
  "prompt": "Format this clinical dictation.",
  "model": "ollama/llama3",
  "api_base": "http://localhost:11434"
}
```

No API key required. Ollama must be running locally. You can also set
`RESONA_LLM_API_BASE=http://localhost:11434` globally instead of specifying `api_base` in each
step.

### Local vLLM or any OpenAI-compatible endpoint

```bash
export RESONA_LLM_MODEL=openai/mistral-7b
export RESONA_LLM_API_BASE=http://localhost:8000/v1
```

```json
{
  "type": "llm",
  "prompt": "Format this clinical dictation."
}
```

---

## Full pipeline example

```json
{
  "steps": [
    {
      "type": "replacements",
      "source": "replacements.json"
    },
    {
      "type": "llm",
      "name": "structure",
      "prompt": "You are a German medical transcription assistant. Convert the following raw transcript into a structured clinical note with Markdown section headings (Verlauf, Medikation, Procedere). Preserve all clinical content exactly.",
      "model": "ollama/llama3",
      "api_base": "http://localhost:11434"
    }
  ]
}
```

In this pipeline:

1. Spoken punctuation commands (`Komma`, `Punkt`, etc.) are replaced with their written equivalents.
2. The resulting text is sent to a local Llama 3 model for structure and formatting.

---

## Config resolution

The postprocessing source is selected in this order:

1. `~/.resona/postprocess.json` — full pipeline; takes precedence over everything else
2. `~/.resona/replacements.json` — replacements-only; used when `postprocess.json` is absent
3. Bundled defaults — German dictation replacements baked into the `resona-postprocess` package

If none of these files exist, the bundled defaults activate automatically with no configuration
required.

!!! note "Server-side postprocessing"
    When resona-api is running, replacements are stored in SQLite and applied server-side. The
    `postprocess.json` / `replacements.json` files are only consulted during local-only
    transcription (the fallback path). To add an LLM step server-side, configure
    `RESONA_LLM_MODEL` and `RESONA_LLM_API_BASE` on the server.
