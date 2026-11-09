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
| `endpoint` | no | Id of an entry in the execution policy registry. Preferred form; hub profiles must use it |
| `model` | no | Explicit local CLI mode only (legacy step). Falls back to `RESONA_LLM_MODEL`; no default model exists |
| `api_base` | no | Explicit local CLI mode only (legacy step). Overrides `RESONA_LLM_API_BASE` |
| `temperature` | no | Sampling temperature |
| `max_tokens` | no | Maximum tokens to generate |
| `timeout` | no | Seconds per model call (default 120) |
| `chunk_chars` | no | Chunked mode (integer ≥ 1000): the text is sent in chunks of about this many characters, see below |
| `max_attempts` | no | Chunked mode only: calls per chunk before the step fails (1–10, default 3) |
| `max_length_deviation` | no | Chunked mode only: reject a chunk reply whose length differs from the chunk by more than this fraction (e.g. `0.3`) |
| `preserve_markup` | no | Chunked mode only: reject a chunk reply that changes HTML comments/placeholders, heading levels or the cell count of any table row |

#### Chunked mode (long Markdown)

A step with `chunk_chars` splits the Markdown with `chunking.split_markdown`: a chunk ends at the furthest blank-line (block) boundary that keeps it within `chunk_chars`; only a single block longer than that is cut at a line boundary. A pipe table or a fenced code block is never cut, even when it alone is longer than `chunk_chars`. The chunks concatenate to the input exactly; each chunk is sent without its surrounding whitespace, which is restored around the reply.

- A chunk call that fails is retried up to `max_attempts` calls in total. When a chunk still fails, the step raises `PostprocessStepFailed`. `run()` re-raises it like `PolicyDenied`, so the job fails (`postprocess_failed`, retryable) instead of silently skipping the polish.
- A reply that fails `max_length_deviation` or `preserve_markup` is not retried: the raw chunk is kept and the result gets the warning `polish_chunk_rejected:<chunk number>:<length|markup>`.
- A leading `<think>…</think>` block and a code fence wrapped around the whole reply are removed before the checks.

Without `chunk_chars` an `llm` step keeps its single-call behaviour: a failure is logged and the step is skipped (`postprocess_step_skipped:<name>`).

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

`temperature`, `max_tokens` and `timeout` apply as for `llm`; the call is made in JSON mode.

## PostprocessResult

`build_pipeline(profile, policy=policy).run(text)` returns a `PostprocessResult`:

```python
@dataclass
class PostprocessResult:
    text: str             # final postprocessed text (all llm/replacements steps applied)
    data: dict            # accumulated output from all extract steps, keyed by step name
    warnings: list[str]   # content-free warnings, e.g. polish_chunk_rejected:<n>:<reason>
```

A text step may return a `StepOutput(text, warnings)` instead of a string; chunked `llm` steps do.

## The bundled default profile

`resona-postprocess` ships a `profiles/default.json` (rules in `default_replacements.json`) that turns German dictation commands into Markdown. It is used automatically when no profile is specified.

| Spoken | Written |
|--------|---------|
| Komma | `,` |
| Punkt | `.` |
| Doppelpunkt | `:` |
| Ausrufezeichen | `!` |
| Fragezeichen | `?` |
| Absatz | blank line (Markdown paragraph) |
| Anstrich | `\n- ` (list item) |
| Kapitel *Titel* | `\n\n## Titel\n\n` |
| Klammer auf / zu, Eckige Klammer auf / zu | `(` `)` `[` `]` |

Every command pattern has word boundaries (`\b`); rules run case-insensitively, so without them `Zeitpunkt` became `Zeit.`. Order matters: the four medical heading rules (`Kapitel Verlauf|Medikation|Psychopathologischer Befund|Pro[cz]edere`, no terminator needed, an immediately spoken `Absatz`/`Punkt`/`Doppelpunkt`/`Komma` is swallowed) run first, then the general `Kapitel` rule, then brackets, then `Doppelpunkt` before `Punkt`, then `Absatz`. Three cleanup rules close the list: trailing spaces before a newline, runs of 3+ newlines → one blank line, leading/trailing whitespace of the whole text removed. Name corrections (`Monique` → `Monic`, …) come first and are part of `default` only.

The general `Kapitel` rule fires only for a title of one to five words (characters other than whitespace and `.,;:!?()[]`; a `.` or `,` between digits is allowed, so `F32.1` stays whole; command words like `Komma` are excluded) followed by an optional comma and a terminator: `Absatz`, `Punkt`, `Doppelpunkt`, `Fragezeichen`, `Ausrufezeichen`, `!`, `?`, `:`, or a `.` with no digit on either side (`3. Mai` is no terminator). Negative lookbehinds skip `Kapitel` after articles, prepositions and ordinals (`im`, `das`, `dem`, `vom`, `zum`, `letzten`, …), so prose such as `wie im Kapitel zuvor beschrieben.` is untouched. Without a match the text stays as spoken. The punctuation commands absorb one adjacent punctuation mark on each side (`, Komma,` → `,`, `. Punkt.` → `.`), so Whisper's own punctuation is not doubled. Rules that consume leading whitespace use `(?<![ \t])[ \t]*` instead of `\s*`: they never cross a newline (only `Absatz` and the heading rules produce line breaks) and stay linear on long space runs. Whole-word commands still hit ordinary words (`der wichtigste Punkt`, `im dritten Absatz`); rules cannot avoid that.

This is a behaviour change for every organization using the bundled `default` (see the guide's "Behaviour change" note, which also shows how to keep the v0.1.0 rules as a stored profile). The bundled profile reaches workers as `source: default_replacements.json` and is resolved from each worker's installed `resona-postprocess`, so the snapshot hash does not cover the rule content and a mixed-release fleet can give different output for the same snapshot.

## The bundled transcript profiles

`profiles/transcript-markdown-de.json` (`transcript-markdown-de`) is one `replacements` step with the dictation command rules of `default_replacements.json` (without the name corrections), inline because hosted profiles reject `source`; a test keeps them equal to the tail of `default_replacements.json`. No LLM, runs on any worker.

`profiles/transcript-polish-de.json` (`transcript-polish-de`) runs the same rules, then a chunked `llm` step on endpoint `local-llm`. Its German prompt allows only changes to capitalization (sentence starts, after headings, list items, nouns), punctuation and whitespace; it forbids changing, adding or removing any word, even one that looks misrecognized, and names numbers, units, doses, drug names, diagnoses, codes and proper names as untouchable. Settings: `chunk_chars` 12000, `max_attempts` 3, `max_length_deviation` 0.1, `preserve_markup` true, `temperature` 0, `max_tokens` 8192, `timeout` 600. `preserve_markup` (`markup_signature`) compares only HTML comments, the heading-level sequence and per-row table cell counts; merged paragraphs, joined list items, changed heading text or a changed word or number pass it and are only bounded by the length guard. The endpoint registration is the same as for `document-polish-de` below.

## The bundled document polish profile

`profiles/document-polish-de.json` (`document-polish-de`) is a single chunked `llm` step for Docling Markdown of German documents. Its German prompt allows only OCR fixes: hyphenation across line breaks, misrecognized umlauts/ß, confused or stray characters, merged/split words. It forbids adding, removing, summarizing or rephrasing content and requires headings, tables, page markers and the `<!-- image -->` / `<!-- formula-not-decoded -->` placeholders to stay verbatim. Settings: endpoint `local-llm`, `chunk_chars` 12000, `max_attempts` 3, `max_length_deviation` 0.3, `preserve_markup` true, `temperature` 0, `max_tokens` 8192, `timeout` 600. The endpoint id names no host: an org admin registers a `local_llm` endpoint with the id `local-llm` and a loopback `url` on the approved worker. It is not resolved by name from the package: upload it to the hub as an organization profile (see [Linux Worker](../server/linux-worker.md#llm-polish-on-the-worker-host)).

## Profile resolution

`resolve_profile(ref, profiles_dir)` accepts any of:
- A `Profile` object — returned as-is
- A `dict` — parsed as a profile
- A string starting with `{` — parsed as inline JSON
- A string ending with `.json` that exists on disk — loaded from that path
- A plain name string — looked up as `<profiles_dir>/<name>.json`; falls back to the bundled `default` profile when `name == "default"`

## Where the pipeline runs

### Hub mode (resona-worker)

```
engine returns {text, language, segments}
    ↓
worker takes the postprocess profile revision from the job's execution_snapshot
    ↓
build_pipeline(profile, policy=<job ExecutionPolicy>) checks every stage first
    ↓
result = pipeline.run(text)   (PolicyDenied is raised, never swallowed)
    ↓
raw_text stays the engine's text; the engine's Markdown moves to raw_markdown when a step changed it; markdown is the polished text
    ↓
worker uploads the result; the hub marks the job completed
```

Before each `llm`/`extract` step the worker asks the hub for a fresh policy decision on that endpoint. After the job's last step on an endpoint that has an `unload_url`, the worker POSTs to that URL (best effort, 10 s timeout) so the model server frees the GPU for the next job. A 2xx answer is logged at info level; another status or a transport error is a warning naming the endpoint id and the status code or exception type, never content.

The engine receives `profile.initial_prompt_string()` as its `initial_prompt`. It never receives replacement rules — postprocessing is the worker's responsibility, not the engine's.

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
