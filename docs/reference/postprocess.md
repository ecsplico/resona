# resona-postprocess

`resona-postprocess` is the composable postprocessing library. It transforms raw engine output
through an ordered pipeline of steps driven by a **profile** — a JSON document that combines
vocabulary hints for the ASR engine with `replacements`, `llm`, and `extract` steps.

Postprocessing runs **after** the engine — never inside it. resona-api applies it when completing
async jobs and when serving `/v1/audio/transcriptions`. `resona transcribe` applies it locally
when no server is reachable.

## Bundled default profile

`resona-postprocess` ships `profiles/default.json`. It is used automatically when no profile is
specified and activates German medical dictation rules with no configuration needed.

| Spoken | Written |
|--------|---------|
| Komma | `,` |
| Punkt | `.` |
| Absatz | (newline) |
| Kapitel | `#` (heading) |
| Klammer auf / Klammer zu | `(` / `)` |
| Medikation, Verlauf, Procedere, ... | Section headings |

## Profile format

```json
{
  "name": "my-profile",
  "description": "Optional description",
  "initial_prompt": ["phrase1", "phrase2"],
  "steps": [
    {"type": "replacements", "rules": [{"pattern": "\\bKomma\\b", "replacement": ","}]},
    {"type": "llm", "name": "format", "prompt": "Format this medical text.", "model": "gpt-4o-mini"},
    {"type": "extract", "name": "codes", "prompt": "Return JSON {\"codes\": [...]} with ICD codes."}
  ]
}
```

See [Postprocessing Profiles](../guide/postprocessing.md) for the full format reference.

## Direct usage

```python
from resona_postprocess.profile import resolve_profile, bundled_default
from resona_postprocess.pipeline import build_pipeline, PostprocessResult

# Use the bundled default profile
profile = bundled_default()
result: PostprocessResult = build_pipeline(profile).run("Hallo Komma wie geht es Ihnen Punkt")
print(result.text)   # → "Hallo, wie geht es Ihnen."
print(result.data)   # → {} (no extract steps in default profile)

# Resolve a profile by name (from ~/.resona/profiles/ or bundled)
profile = resolve_profile("default", "~/.resona/profiles/")
result = build_pipeline(profile).run(raw_text)

# Inline profile
import json
profile = resolve_profile(json.dumps({
    "name": "x",
    "steps": [{"type": "replacements", "rules": [{"pattern": "hello", "replacement": "GOODBYE"}]}]
}), "/any/dir")
result = build_pipeline(profile).run("hello world")
print(result.text)   # → "GOODBYE world"

# Apply replacements directly (no pipeline needed)
from resona_postprocess.replacements import apply_replacements
rules = [{"pattern": "Komma", "replacement": ","}]
text = apply_replacements("Hallo Komma", rules)
# → "Hallo ,"
```

## LLM functions

```python
from resona_postprocess.llm import llm_transform, llm_extract

# Transform text via LLM
formatted = llm_transform(
    "Befund Absatz Patient kommt zur Kontrolle",
    prompt="Format this medical dictation text.",
    model="ollama/llama3",
    api_base="http://localhost:11434",
)

# Extract structured data via LLM
data = llm_extract(
    "ICD-10 F32.1 und F41.0",
    prompt="Return JSON {\"codes\": [...]} with ICD codes mentioned.",
    model="gpt-4o-mini",
)
```

Set `RESONA_LLM_MODEL` and `RESONA_LLM_API_BASE` to configure defaults without changing code.

---

## Pipeline

::: resona_postprocess.pipeline.PostprocessPipeline

::: resona_postprocess.pipeline.PostprocessResult

::: resona_postprocess.pipeline.build_pipeline

---

## Profile

::: resona_postprocess.profile.Profile

::: resona_postprocess.profile.resolve_profile

::: resona_postprocess.profile.bundled_default

::: resona_postprocess.profile.list_profiles

---

## Replacements

::: resona_postprocess.replacements.apply_replacements

---

## LLM

::: resona_postprocess.llm.llm_transform

::: resona_postprocess.llm.llm_extract
