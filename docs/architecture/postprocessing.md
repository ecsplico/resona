# Postprocessing Pipeline

Postprocessing is the layer between raw engine output and the final stored transcript. The engine returns exactly what the model produced; everything else — text normalisation, dictation-command expansion, LLM reformatting — is the pipeline's responsibility.

## Core concept

The pipeline is a composable chain of `str → str` steps. Each step receives the output of the previous step and returns a transformed string. Steps are pure functions with no side effects; they never touch audio files or the database.

```
raw transcript
      │
      ▼
 ReplacementStep   ← regex rules, case-insensitive
      │
      ▼
 LLMStep (optional) ← litellm, any OpenAI-compatible endpoint
      │
      ▼
 ... more steps ...
      │
      ▼
 final Markdown output
```

## Step types

### ReplacementStep

Applies a list of regex replacement rules in order. Rules are case-insensitive by default. Each rule is a dict with `pattern` and `replacement` keys; the replacement may reference capture groups.

```python
from resona_postprocess.replacements import apply_replacements

rules = [
    {"pattern": r"\bKomma\b",  "replacement": ","},
    {"pattern": r"\bPunkt\b",  "replacement": "."},
    {"pattern": r"\bAbsatz\b", "replacement": "\n"},
]
result = apply_replacements(text, rules)
```

The standalone `apply_replacements(text, rules)` function is available for use outside a full pipeline.

### LLMStep

Sends text through a language model using [litellm](https://github.com/BerriAI/litellm). Any OpenAI-compatible endpoint is supported. The step is configured with a system prompt and an optional model identifier.

```json
{
  "type": "llm",
  "name": "format",
  "prompt": "Format this as a structured medical report.",
  "model": "ollama/llama3",
  "api_base": "http://localhost:11434"
}
```

LLM steps add latency. Use them only when rule-based replacements are insufficient.

## PostprocessPipeline

`PostprocessPipeline` accepts a list of step objects and chains them:

```python
from resona_postprocess.pipeline import PostprocessPipeline
from resona_postprocess.replacements import ReplacementStep
from resona_postprocess.llm import LLMStep

pipeline = PostprocessPipeline([
    ReplacementStep(rules),
    LLMStep(prompt="...", model="gpt-4o-mini"),
])
result = pipeline.run(raw_text)
```

## build_pipeline_from_config()

`build_pipeline_from_config()` in `resona_postprocess.sources` constructs a pipeline from the user's configuration, following this resolution order:

1. `~/.resona/postprocess.json` — full pipeline config with explicit steps
2. `~/.resona/replacements.json` — replacement rules only (no LLM steps)
3. Bundled `default_replacements.json` — German medical dictation defaults

Only the first matching source is used. If none of the user files exist, the bundled defaults are active automatically.

### postprocess.json format

```json
{
  "steps": [
    {"type": "replacements", "source": "replacements.json"},
    {
      "type": "llm",
      "name": "format",
      "prompt": "Format this medical text as structured Markdown.",
      "model": "ollama/llama3",
      "api_base": "http://localhost:11434"
    }
  ]
}
```

Relative paths in `source` resolve relative to `~/.resona/`.

## Where the pipeline runs

### Server mode (TranscribeTask)

```
engine returns {text, language, segments}
    ↓
TranscribeTask fetches active replacements from SQLite DB
    ↓
builds PostprocessPipeline from DB replacements
    ↓
md = pipeline.run(text)
    ↓
stores md in Job row, writes .md file to MD_PATH/
    ↓
sets job status = COMPLETED
```

The pipeline runs in the `resona-api` process, after the engine call completes and before results are stored. Replacements are managed via the CRUD API (`GET/POST/PATCH/DELETE /replacements`) and stored in SQLite.

### Local mode (CLI process)

```
engine returns {text, language, segments}
    ↓
CLI calls build_pipeline_from_config()
    reads ~/.resona/postprocess.json  (or bundled defaults)
    ↓
md = pipeline.run(text)
    ↓
writes <input>.txt  (or --output-dir/<name>.txt)
```

In local mode there is no database. The pipeline is built from config files every time `resona transcribe` runs.

!!! note "Postprocessing never runs inside the engine"
    The engine is unaware of replacements, prompts, or LLM steps. This is enforced by the [Stateless Engine Contract](engine-contract.md). If you find yourself wanting to add replacement logic to an engine, move it to `resona-postprocess` instead.

## Default replacements (bundled)

The bundled `default_replacements.json` covers German medical dictation commands out of the box:

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

To override the defaults, create `~/.resona/replacements.json` with your own rules, or create `~/.resona/postprocess.json` for a full pipeline with LLM steps.
