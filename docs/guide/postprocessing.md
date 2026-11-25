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
        {"pattern": "\\bAbsatz\\b", "replacement": "\n\n"}
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
| `initial_prompt` | no | List of phrases used as vocabulary hints. Hub jobs on a cloud ASR candidate send them to the provider as keyterms (Deepgram `keyterm`/`keywords`, ElevenLabs `keyterms` on Scribe v2+, OpenAI `prompt`); at most 100 terms of ≤ 50 characters |
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

For long documents, set `chunk_chars`. A chunk that keeps failing fails the job instead of being skipped; a chunk reply that changes the length too much or touches placeholders, headings or table shape is discarded, the raw chunk is kept and the result gets a `polish_chunk_rejected:<n>:<reason>` warning. Details: [Postprocessing Pipeline](../architecture/postprocessing.md#chunked-mode-long-markdown). The catalogue profile `document-polish-de` (`profiles/` in the repository, see [Profile catalogue](#profile-catalogue)) uses this mode for OCR correction of German documents. It calls the endpoint id `local-llm`, which names no host: an org admin registers a `local_llm` endpoint with that id and a loopback `url` on the approved worker; [Linux Worker](../server/linux-worker.md#llm-polish-on-the-worker-host) shows how.

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

### include

Puts the steps of another profile at this position. Small reusable profiles (**blocks**) are
composed this way instead of copying their rules:

```json
{
  "name": "dictation-clean-de",
  "steps": [
    {"type": "include", "profile": "filler-words-de"},
    {"type": "include", "profile": "dictation-commands-de"}
  ]
}
```

- `profile` is a profile name (lowercase letters, digits, `-`, `_`). An optional top-level
  `"block": true` marks a profile as meant for including; it changes nothing else.
- Includes are resolved before anything runs (`resona_postprocess.compose.flatten`): the included
  steps go in at that position, recursively up to 4 levels deep, and `initial_prompt` lists are
  merged in order without duplicates (the outer profile's first). A `replacements` step that an
  included profile reads from `source` gets its rules inline.
- A cycle, a missing profile, deeper nesting, an included profile with `instructions` or `context`
  (only the outer profile or the job sets them) or two `extract` steps with the same name after flattening
  are errors. A pipeline refuses a profile that still has include steps.
- Lookup order in local CLI mode: `<RESONA_PROFILES_DIR>/<name>.json`, then the bundled blocks.

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `RESONA_LLM_MODEL` | _(unset)_ | Explicit local CLI mode only: model for `llm` / `extract` steps that set no `model`. No default model; the hub never reads it |
| `RESONA_LLM_API_BASE` | _(unset)_ | Explicit local CLI mode only: API base for legacy LLM steps (e.g. local Ollama) |
| `RESONA_PROFILES_DIR` | `<DATA_PATH>/profiles` (server) / `~/.resona/profiles/` (CLI) | Directory where named profile JSON files are stored |

---

## Bundled profiles

### default

When no profile is specified, the `default` profile bundled with `resona-postprocess` is used
automatically. It turns German dictation commands into Markdown:

| Spoken | Written |
|--------|---------|
| Komma | `,` |
| Punkt | `.` |
| Doppelpunkt | `:` |
| Fragezeichen / Ausrufezeichen | `?` / `!` |
| Absatz | blank line (new Markdown paragraph) |
| Anstrich | new line starting with `- ` (list item) |
| Kapitel *Titel* | `## Titel` on its own line, followed by a blank line |
| Klammer auf / Klammer zu | `(` / `)` |
| Eckige Klammer auf / zu | `[` / `]` |

Commands match whole words only: `Zeitpunkt`, `Standpunkt` and `Kommandant` stay as they are.
Whisper often punctuates the commands itself (`berichtet, Komma, er`, `Schmerzen. Punkt.`); each
punctuation command absorbs the punctuation right before and after it, so the result is
`berichtet, er` and `Schmerzen.`, never `,,` or `..`. Commands never remove a line break, except
`Absatz`, which becomes one.

`Kapitel` becomes a heading only when all of these hold:

- the title has at most five words and no punctuation inside (a dot or comma between digits is
  fine, so `Kapitel Diagnose F32.1 Absatz` gives `## Diagnose F32.1`); command words such as
  `Komma` never belong to the title;
- the title is followed directly (an optional comma in between) by `Absatz`, `Punkt`,
  `Doppelpunkt`, `Fragezeichen`, `Ausrufezeichen` or by `.`, `!`, `?`, `:` (a `.` next to a
  digit, as in `3. Mai`, does not count);
- `Kapitel` does not follow an article, preposition or ordinal (`im`, `das`, `dem`, `vom`, `zum`,
  `letzten`, …), so `wie im Kapitel zuvor beschrieben.` stays prose.

Otherwise the text stays exactly as spoken: `Kapitel Anamnese der Patient berichtet Komma er habe
Schmerzen Punkt` becomes `Kapitel Anamnese der Patient berichtet, er habe Schmerzen.`, not a
heading. Leading and trailing blank lines are removed. `default` holds only these generic
commands; the medical section headings and name corrections are in the catalogue profile
[`dictation-medical-de`](#dictation-medical-de).

```text
Zum Zeitpunkt der Aufnahme Kapitel Anamnese Absatz der Patient berichtet Komma er habe Schmerzen Punkt
```

becomes

```markdown
Zum Zeitpunkt der Aufnahme

## Anamnese

der Patient berichtet, er habe Schmerzen.
```

Rules cannot tell a command from the same word used in a sentence. `der wichtigste Punkt der
Untersuchung` becomes `der wichtigste. der Untersuchung`, `Punkt eins` becomes `. eins`, and `im
dritten Absatz` starts a new paragraph. Dictate around these words, or correct them afterwards.

!!! warning "Behaviour change of the bundled `default` profile"
    Since the release that introduced the transcript profiles, `default` produces different
    output for the same audio: `Absatz` was a single newline and is now a blank line (new
    Markdown paragraph); `Kapitel` was `#` up to the end of the line and is now a terminated
    `## Titel` heading (or nothing, see above); commands only match whole words (before,
    `Zeitpunkt` became `Zeit.`); `Doppelpunkt`, `Anstrich` and `Eckige Klammer` are new; doubled
    punctuation is merged. This affects every job that uses `profile=default` and every
    organization with `polish` on and `postprocess_profile` `default`.

    The bundled `default` includes the block `dictation-commands-de`; resolving it inlines that
    block's rules, so the job's pinned profile carries the rules themselves. Older hubs sent a
    reference to `default_replacements.json`, which each worker resolved from its own installed
    `resona-postprocess`: there, until every worker runs the same release, the same job can get
    old or new output depending on which worker claims it.

    To keep the old rules, store them under another name (the name `default` is reserved) and
    select that profile (`--profile`, or the organization's `postprocess_profile`):

    ```bash
    git show v0.1.0:packages/postprocess/src/resona_postprocess/default_replacements.json \
      | jq '{name: "dictation-v0-1", steps: [{type: "replacements", rules: .}]}' > dictation-v0-1.json
    resona profiles push dictation-v0-1 dictation-v0-1.json
    ```

### Bundled blocks

The wheel also ships blocks (`resona_postprocess/blocks/*.json`, `"block": true`) for `include`
steps; a local `resona postprocess --local --profile filler-words-de` runs one directly:

| Block | Does |
|-------|------|
| `dictation-commands-de` | the generic German dictation commands of `default` (table above) |
| `filler-words-de` | removes `äh`, `ähm`, `öh`, `öhm`, `hm`, `hmm`, `mhm` |
| `filler-words-en` | removes `um`, `uh`, `erm`, `hmm` |

The filler blocks only match a whole word (`Ähmlichkeit`, `umbrella`, `uh-huh`, `Hm-Laut` stay)
and never remove a line break. A filler takes one adjacent comma with it (`Ja, äh, ich` →
`Ja, ich`; `Äh, ich` → `ich`; `komme ähm, wenn` → `komme, wenn`); at the end of a sentence or
line the punctuation stays (`Zeit, äh.` → `Zeit.`). The German block does not touch `um`, a
German word.

### Profile catalogue

The `resona-postprocess` wheel bundles only `default` and the blocks above, because they work with no setup.
Profiles that need a decision or an endpoint of the organization live in the repository's root
directory [`profiles/`](https://github.com/ecsplico/resona/tree/main/profiles) and exist on a hub
only after an org admin pushed them:

| Profile | What it does | Needs |
|---------|--------------|-------|
| `dictation-medical-de` | Name corrections, medical section headings, then the generic commands of `default` (rules only; what `default` did up to 0.2.7) | Nothing |
| `transcript-markdown-de` | Dictation commands → Markdown, rules only | Nothing |
| `transcript-polish-de` | The same rules, then an LLM pass limited to capitalization, punctuation and whitespace | `local_llm` endpoint `local-llm` |
| `document-polish-de` | OCR corrections on document Markdown ([Linux Worker](../server/linux-worker.md#llm-polish-on-the-worker-host)) | `local_llm` endpoint `local-llm` |

```bash
git clone https://github.com/ecsplico/resona && cd resona   # or download the files of your release tag
resona profiles push dictation-medical-de profiles/dictation-medical-de.json
resona profiles push transcript-markdown-de profiles/transcript-markdown-de.json
resona profiles push transcript-polish-de profiles/transcript-polish-de.json
resona profiles push document-polish-de profiles/document-polish-de.json
```

!!! warning "Release note: `default` lost its medical headings and name corrections"
    Up to 0.2.7 the bundled `default` also turned `Kapitel Verlauf`, `Kapitel Medikation`,
    `Kapitel Psychopathologischer Befund` and `Kapitel Procedere`/`Prozedere` into headings without
    a terminator and corrected a few names (`Monique` → `Monic`, …). These rules moved to the
    catalogue profile `dictation-medical-de`; `default` keeps the generic commands, whose output is
    unchanged. A medical dictation with `profile=default` (or `postprocess_profile` `default`) now
    keeps the names uncorrected, and a medical heading without a terminator stays text:
    `Kapitel Verlauf Komma stabil Punkt` gave `## Verlauf` + `stabil.` and now gives
    `Kapitel Verlauf, stabil.` (with a terminator, `Kapitel Verlauf Absatz`, the generic rule still
    makes the heading). Fix: push `profiles/dictation-medical-de.json` and select
    `dictation-medical-de` (`--profile`, or the organization's `postprocess_profile`). The
    reference app selects it by default (`NUXT_PUBLIC_DEFAULT_PROFILE`) and pushes it with
    `just pwa push-profiles`.

!!! note "Release note: product profiles left the wheel"
    Up to 0.2.7 the wheel also contained `transcript-markdown-de.json`, `transcript-polish-de.json`,
    `document-polish-de.json` and `title-de.json`. They were never resolved by name, so jobs and
    the profile revisions stored on existing hubs are unaffected. Only the place to take the files
    from changed: the three above from `profiles/`, `title-de` from the reference app
    (`apps/resona-pwa/profiles/`, pushed with `just pwa push-profiles`). A setup script that read
    them from `resona_postprocess/profiles/` must read the catalogue instead, and a hub that never
    pushed them has to push them once.

### dictation-medical-de

`profiles/dictation-medical-de.json` is one `replacements` step with inline rules, in this order:
name corrections (`Monique` → `Monic`, `Serafine`/`Serafina` → `Seraphine`, `Lucy` → `Lucie`,
`Nele`/`Mele` → `Neele`), the medical section headings, then the generic commands of `default`.
The headings `Kapitel Verlauf`, `Kapitel Medikation`, `Kapitel Psychopathologischer Befund` and
`Kapitel Procedere` (also spoken `Prozedere`) need no terminator; a spoken `Absatz`, `Punkt`,
`Doppelpunkt` or `Komma` right after them is dropped. They run before the general `Kapitel` rule,
so `Kapitel Verlauf der Patient ist stabil Punkt` gives `## Verlauf` + `der Patient ist stabil.`.
Its output is what `default` produced up to 0.2.7.

### transcript-markdown-de and transcript-polish-de

`profiles/transcript-markdown-de.json` holds the rules of `dictation-medical-de` without its name
corrections and without any LLM step, so it runs on every worker.
`profiles/transcript-polish-de.json` runs the same rules, then a chunked `llm` step on the endpoint
id `local-llm`. Its prompt allows the model to change only capitalization (sentence starts, after
headings, list items, nouns), punctuation and whitespace. It must not change, add or remove any
word, not even a word that looks misrecognized; numbers, units, doses, drug names, diagnoses and
codes stay exactly as transcribed. Settings: `max_length_deviation` 0.1, `preserve_markup: true`,
`temperature` 0, `chunk_chars` 12000. Like `document-polish-de`, it needs an org admin to register
a `local_llm` endpoint `local-llm` on the approved worker.

The guards check less than the prompt asks for. `preserve_markup` compares only HTML comments,
the sequence of heading levels and the cell count of every table row; it does not notice merged
paragraphs, joined list items, a rewritten heading text or a changed word or number. A reply that
changes such things is kept as long as it stays within the 10 % length guard. Use the polish
profile only where a reviewer reads the result.

Both carry their rules inline, because hosted profiles reject `source`. Push them from the
catalogue as shown under [Profile catalogue](#profile-catalogue).

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
