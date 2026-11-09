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
| `endpoint` | no | Id of an endpoint in the organization's settings registry; required in hub profiles |
| `model` | no | litellm model string (overrides `RESONA_LLM_MODEL`; explicit local CLI mode only) |
| `api_base` | no | Custom API endpoint (overrides `RESONA_LLM_API_BASE`; explicit local CLI mode only) |
| `timeout` | no | Seconds per model call (default 120) |
| `chunk_chars` | no | Process long Markdown in chunks of about this many characters (≥ 1000); never cuts a table. Enables `max_attempts`, `max_length_deviation`, `preserve_markup` |

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

For long documents, set `chunk_chars`. A chunk that keeps failing fails the job instead of being skipped; a chunk reply that changes the length too much or touches placeholders, headings or table shape is discarded, the raw chunk is kept and the result gets a `polish_chunk_rejected:<n>:<reason>` warning. Details: [Postprocessing Pipeline](../architecture/postprocessing.md#chunked-mode-long-markdown). The bundled `document-polish-de` profile uses this mode for OCR correction of German documents. It calls the endpoint id `local-llm`, which names no host: an org admin registers a `local_llm` endpoint with that id and a loopback `url` on the approved worker; [Linux Worker](../server/linux-worker.md#llm-polish-on-the-worker-host) shows how.

When a step changes the Markdown, the job result keeps the engine's Markdown before postprocessing in `raw_markdown`; `markdown` is the postprocessed text.

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

`temperature`, `max_tokens` and `timeout` apply as for `llm`.

Extracted data is stored in `job.structured` (server) or `PostprocessResult.data` (local).

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `RESONA_LLM_MODEL` | _(unset)_ | Explicit local CLI mode only: model for `llm` / `extract` steps that set no `model`. No default model; the hub never reads it |
| `RESONA_LLM_API_BASE` | _(unset)_ | Explicit local CLI mode only: API base for legacy LLM steps (e.g. local Ollama) |
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

Cloud models (any step whose endpoint is not loopback) are denied in private runs. They need the organization setting `cloud_enabled=true` and a job submitted with `--allow-cloud` (the same applies to `--local` runs).

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

In local CLI mode you can also set `RESONA_LLM_MODEL=ollama/llama3` and `RESONA_LLM_API_BASE=http://localhost:11434` globally.

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
resona profiles push my-profile my-profile.json
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

### Re-run a profile

Postprocessing normally runs inside an audio or document job, right after the engine. To apply a
profile to text you already have, without transcribing again, submit a `text` job: the worker runs
only the profile. Typical cases: a profile added after the transcript was made, or polishing with a
local LLM that only its assigned worker can reach (the job is claimable only by that worker).

```bash
# Re-run on an earlier job's result (its text before its own postprocessing)
resona postprocess --job 12 --profile transcript-markdown-de

# Text or Markdown files
resona postprocess notes.md --profile document-polish-de --output-dir ./polished/

# On this machine, with cached hub settings
resona postprocess notes.md --profile my-profile.json --local --output-dir ./out/
```

The profile is required and must be stored on the hub (`resona profiles push`), except with
`--local`. The result's `raw_text` is the input, `markdown` the profile output and `raw_markdown`
the input when the profile changed it. Over HTTP: upload the UTF-8 text to `POST /v1/uploads`, then
`POST /v1/jobs` with `kind: "text"` and `profile` (see [Text jobs](../reference/api.md#text-jobs)).

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
