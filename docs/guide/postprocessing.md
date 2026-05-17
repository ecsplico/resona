# Postprocessing Profiles

After the ASR engine returns raw text, Resona applies a **postprocessing profile**. A profile is a
JSON document that bundles vocabulary hints for the engine (`initial_prompt`) with an ordered list
of pipeline steps that transform the raw transcript into the final output.

---

## Profile file format

```json
{
  "name": "my-profile",
  "description": "German medical dictation with LLM formatting",
  "initial_prompt": ["Befund", "Diagnose", "Medikation"],
  "steps": [
    {
      "type": "replacements",
      "rules": [
        {"pattern": "\\bKomma\\b", "replacement": ","},
        {"pattern": "\\bPunkt\\b",  "replacement": "."},
        {"pattern": "\\bAbsatz\\b", "replacement": "\n"}
      ]
    },
    {
      "type": "llm",
      "name": "format",
      "prompt": "Format this medical dictation as a structured clinical note with Markdown headings.",
      "model": "gpt-4o-mini"
    }
  ]
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `name` | yes | Profile identifier (must match the filename when stored on disk) |
| `description` | no | Human-readable description |
| `initial_prompt` | no | List of phrases passed to the ASR engine as vocabulary hints |
| `steps` | no | Ordered list of postprocessing step objects |

---

## Step types

### replacements

Applies a set of regex rules in order, case-insensitively.

Provide either `rules` (inline array) or `source` (path to a JSON rules file):

```json
{"type": "replacements", "rules": [{"pattern": "\\bKomma\\b", "replacement": ","}]}
```

```json
{"type": "replacements", "source": "my-rules.json"}
```

Each rule object:

| Field | Description |
|-------|-------------|
| `pattern` | Regex pattern (case-insensitive, Python `re` syntax) |
| `replacement` | Substitution string; supports backreferences |

### llm

Sends the current text to a language model and replaces the text with the model's response.
Uses [litellm](https://github.com/BerriAI/litellm) — any OpenAI-compatible endpoint is supported.

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
  "name": "structure",
  "prompt": "You are a German medical transcription assistant. Convert the following raw transcript into a structured clinical note with Markdown section headings.",
  "model": "ollama/llama3",
  "api_base": "http://localhost:11434"
}
```

LLM steps add latency. Use them only when rule-based replacements are insufficient.

### extract

Sends the current text to a language model and stores the model's **structured JSON** response
alongside the transcript. The text is not modified.

```json
{
  "type": "extract",
  "name": "icd_codes",
  "prompt": "Return a JSON object {\"codes\": [...]} listing all ICD-10 codes mentioned in the text.",
  "model": "gpt-4o-mini"
}
```

Extracted data is stored in `job.structured` (server) or `PostprocessResult.data` (local).

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `RESONA_LLM_MODEL` | `gpt-4o-mini` | Default model for all `llm` / `extract` steps that do not specify `model` |
| `RESONA_LLM_API_BASE` | (none) | Custom API base URL for all `llm` / `extract` steps (e.g. local Ollama) |
| `RESONA_PROFILES_DIR` | `<DATA_PATH>/profiles` (server) / `~/.resona/profiles/` (CLI) | Directory where named profile JSON files are stored |

---

## Bundled default profile

When no profile is specified, the `default` profile bundled with `resona-postprocess` is used
automatically. It covers German medical dictation commands:

| Spoken | Written |
|--------|---------|
| Komma | `,` |
| Punkt | `.` |
| Absatz | (newline) |
| Kapitel | `#` (Markdown heading) |
| Klammer auf / Klammer zu | `(` / `)` |

Plus medical section headings (`Verlauf`, `Medikation`, `Psychopathologischer Befund`, `Procedere`)
and common name corrections.

---

## LLM provider examples

### OpenAI

```json
{"type": "llm", "prompt": "Format this clinical dictation.", "model": "gpt-4o-mini"}
```

Requires `OPENAI_API_KEY`.

### Anthropic Claude

```json
{"type": "llm", "prompt": "Format this.", "model": "anthropic/claude-3-5-haiku-20241022"}
```

Requires `ANTHROPIC_API_KEY`.

### Local Ollama

```json
{
  "type": "llm",
  "prompt": "Format this clinical dictation.",
  "model": "ollama/llama3",
  "api_base": "http://localhost:11434"
}
```

Or set `RESONA_LLM_MODEL=ollama/llama3` and `RESONA_LLM_API_BASE=http://localhost:11434` globally.

---

## Using profiles

### Server mode (resona-api)

Pass a `profile` field when submitting a job:

```bash
# Named profile (loaded from RESONA_PROFILES_DIR)
curl -F "audio_files=@dictation.wav" -F "profile=my-profile" http://localhost:7000/jobs

# Inline profile JSON
curl -F "audio_files=@dictation.wav" -F 'profile={"name":"x","steps":[...]}' http://localhost:7000/jobs
```

Manage named profiles with `resona profiles`:

```bash
resona profiles list
resona profiles push my-profile.json
resona profiles show my-profile
resona profiles pull my-profile my-profile.json
resona profiles delete my-profile
```

### CLI / local mode

```bash
# Named profile
resona transcribe dictation.mp3 --profile my-profile

# Inline JSON
resona transcribe dictation.mp3 --profile '{"name":"x","steps":[...]}'

# Default profile (no flag needed)
resona transcribe dictation.mp3
```

---

## Full profile example

```json
{
  "name": "medical-de",
  "description": "German medical dictation with replacement + LLM structuring",
  "initial_prompt": ["Dr. Müller", "Befund", "Diagnose", "Medikation", "Procedere"],
  "steps": [
    {
      "type": "replacements",
      "source": "default_replacements.json"
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

In this profile:

1. Spoken punctuation commands (`Komma`, `Punkt`, etc.) are replaced first.
2. The resulting text is sent to a local Llama 3 model for structure and formatting.

!!! note "Server-side postprocessing"
    When resona-api is running, profiles are stored in `RESONA_PROFILES_DIR` and applied
    server-side. The `--profile` flag on the CLI passes the profile name (or inline JSON) to the
    server with the job submission. In local (no-server) mode, profiles are resolved from
    `~/.resona/profiles/` or provided as inline JSON.
