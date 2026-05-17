# Postprocessing Pipeline

Postprocessing is the layer between raw engine output and the final stored transcript. The engine returns exactly what the model produced; everything else — text normalisation, dictation-command expansion, LLM reformatting, structured data extraction — is the pipeline's responsibility.

## Profiles

A **profile** is a JSON file (or inline JSON string) that groups three things:

1. An **initial_prompt** list — phrases passed to the ASR engine to bias vocabulary.
2. An ordered list of **steps** — the postprocessing pipeline.
3. Metadata: `name` and `description`.

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
      "prompt": "Format this medical dictation as a structured clinical note.",
      "model": "ollama/llama3"
    }
  ]
}
```

## Step types

### replacements

Applies a list of regex replacement rules in order. Rules are case-insensitive by default. Each rule has `pattern` and `replacement` keys; the replacement may reference capture groups.

Supply either:
- `rules` — inline array of rule objects
- `source` — path to a JSON file containing the rules array (relative to the profile's directory or `~/.resona/profiles/`)

```json
{"type": "replacements", "rules": [{"pattern": "\\bKomma\\b", "replacement": ","}]}
```

The standalone `apply_replacements(text, rules)` function is available for use outside a full pipeline.

### llm

Sends the current text to a language model using [litellm](https://github.com/BerriAI/litellm). The model's response replaces the text. Any OpenAI-compatible endpoint is supported.

```json
{
  "type": "llm",
  "name": "format",
  "prompt": "Format this as a structured medical report.",
  "model": "ollama/llama3",
  "api_base": "http://localhost:11434"
}
```

Fields:

| Field | Required | Description |
|-------|----------|-------------|
| `type` | yes | `"llm"` |
| `name` | no | Label for this step (used in logs) |
| `prompt` | yes | System prompt sent to the model |
| `model` | no | litellm model string; overrides `RESONA_LLM_MODEL` |
| `api_base` | no | Custom API endpoint; overrides `RESONA_LLM_API_BASE` |
| `temperature` | no | Sampling temperature |
| `max_tokens` | no | Maximum tokens to generate |

### extract

Sends the current text to a language model and returns **structured JSON data**. The text is not modified; instead the extracted data is stored alongside the transcript (in `job.structured` server-side).

```json
{
  "type": "extract",
  "name": "icd_codes",
  "prompt": "Return a JSON object {\"codes\": [...]} listing all ICD-10 codes mentioned.",
  "model": "gpt-4o-mini"
}
```

## PostprocessResult

`build_pipeline(profile).run(text)` returns a `PostprocessResult`:

```python
@dataclass
class PostprocessResult:
    text: str         # final postprocessed text (all llm/replacements steps applied)
    data: dict        # accumulated output from all extract steps, keyed by step name
```

## The bundled default profile

`resona-postprocess` ships a `profiles/default.json` that covers German medical dictation out of the box. It is used automatically when no profile is specified.

| Spoken | Written |
|--------|---------|
| Komma | `,` |
| Punkt | `.` |
| Ausrufezeichen | `!` |
| Fragezeichen | `?` |
| Absatz | newline |
| Kapitel | `#` (heading) |
| Klammer auf | `(` |
| Klammer zu | `)` |

Plus medical section headings (`Verlauf`, `Medikation`, `Psychopathologischer Befund`, `Procedere`) and common name corrections.

## Profile resolution

`resolve_profile(ref, profiles_dir)` accepts any of:
- A `Profile` object — returned as-is
- A `dict` — parsed as a profile
- A string starting with `{` — parsed as inline JSON
- A string ending with `.json` that exists on disk — loaded from that path
- A plain name string — looked up as `<profiles_dir>/<name>.json`; falls back to the bundled `default` profile when `name == "default"`

## Where the pipeline runs

### Server mode (TranscribeTask)

```
engine returns {text, language, segments}
    ↓
TranscribeTask resolves profile (job.profile or "default") from RESONA_PROFILES_DIR
    ↓
builds PostprocessPipeline from the profile
    ↓
result = pipeline.run(text)
    ↓
stores result.text as job.md
stores result.data as job.structured (JSON)
writes .md file to MD_PATH/
sets job status = COMPLETED
```

The engine receives `profile.initial_prompt_string()` as its `initial_prompt`. It never receives replacement rules — postprocessing is entirely the API layer's responsibility.

### Local mode (CLI process)

```
engine returns {text, language, segments}
    ↓
CLI resolves profile (--profile flag or config.json default_profile or bundled "default")
    ↓
result = build_pipeline(profile).run(text)
    ↓
writes <input>.txt  (or --output-dir/<name>.txt)
```

!!! note "Postprocessing never runs inside the engine"
    The engine is unaware of profiles, replacements, or LLM steps. This is enforced by the [Stateless Engine Contract](engine-contract.md).
