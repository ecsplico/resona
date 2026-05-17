# resona-postprocess

`resona-postprocess` is the composable postprocessing pipeline. It transforms raw engine output through an ordered chain of `str → str` steps before results reach the user. Out of the box it applies regex-based text replacements (German dictation commands, medical headings, name corrections). An optional LLM step can be added via `~/.resona/postprocess.json`.

Postprocessing runs **after** the engine — never inside it. resona-api applies it when completing async jobs and when serving `/v1/audio/transcriptions`. `resona transcribe` applies it locally using `~/.resona/postprocess.json` (or bundled defaults).

## Default replacements

Bundled in `default_replacements.json`. Active out of the box — no configuration needed.

| Spoken | Written |
|--------|---------|
| Komma | `,` |
| Punkt | `.` |
| Absatz | (newline) |
| Kapitel | `#` (heading) |
| Klammer auf / Klammer zu | `(` / `)` |
| Medikation, Verlauf, Procedere, ... | Section headings |

Override by creating `~/.resona/replacements.json` with your own rules list.

## Pipeline configuration

For a full pipeline with LLM steps, create `~/.resona/postprocess.json`:

```json
{
  "steps": [
    {"type": "replacements", "source": "replacements.json"},
    {
      "type": "llm",
      "name": "format",
      "prompt": "Format this medical text into clean paragraphs.",
      "model": "ollama/llama3"
    }
  ]
}
```

Relative `source` paths resolve relative to `~/.resona/`. If `postprocess.json` does not exist, `build_pipeline_from_config()` falls back to `replacements.json` and then to the bundled defaults.

## Direct usage

```python
from resona_postprocess.sources import build_pipeline_from_config
from resona_postprocess.replacements import apply_replacements
from resona_postprocess.pipeline import PostprocessPipeline

# Build from config files (recommended)
pipeline = build_pipeline_from_config()
result = pipeline.run("Hallo Komma wie geht es Ihnen Punkt")
# → "Hallo, wie geht es Ihnen."

# Build manually
pipeline = PostprocessPipeline()
pipeline.add("clean", lambda t: t.strip())
pipeline.add("replacements", lambda t: apply_replacements(t, my_rules))
result = pipeline.run(raw_text)

# Apply replacements directly
rules = [{"name": "Komma", "replacement": ","}]
text = apply_replacements("Hallo Komma", rules)
# → "Hallo ,"
```

## LLM step

The LLM step uses [litellm](https://docs.litellm.ai/), which supports OpenAI, Ollama, Anthropic, and many other providers via a unified interface.

```python
from resona_postprocess.llm import llm_postprocess

formatted = llm_postprocess(
    "Befund Absatz Patient kommt zur Kontrolle",
    prompt="Format this medical dictation text.",
    model="ollama/llama3",     # or "gpt-4o", "anthropic/claude-3-5-haiku-20241022", ...
    api_base=None,
)
```

Set `RESONA_LLM_MODEL` and `RESONA_LLM_API_BASE` env vars to configure defaults without touching the config file.

---

## Pipeline

::: resona_postprocess.pipeline.PostprocessPipeline

---

## Replacements

::: resona_postprocess.replacements.apply_replacements

---

## LLM

::: resona_postprocess.llm.llm_postprocess

---

## Config

::: resona_postprocess.sources.build_pipeline_from_config

::: resona_postprocess.sources.load_replacements_from_file
