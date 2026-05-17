# Text Replacements

Text replacements are regex patterns applied to the raw transcript after the ASR engine returns.
They convert spoken dictation commands (punctuation, formatting, medical headings) into their
written equivalents. Matching is always case-insensitive.

---

## Bundled German dictation defaults

The following rules are included out of the box and are active with no configuration required.

| Spoken | Written |
|--------|---------|
| Komma | `,` |
| Punkt | `.` |
| Absatz | (newline) |
| Kapitel | `#` (Markdown heading) |
| Klammer auf | `(` |
| Klammer zu | `)` |

In addition, the bundled defaults include German medical section headings
(`Verlauf`, `Medikation`, `Psychopathologischer Befund`, `Procedere`) and common name corrections.

!!! tip "Inspecting the defaults"
    The bundled rules live in
    `packages/postprocess/src/resona_postprocess/default_replacements.json`.
    You do not need to copy this file — creating your own `~/.resona/replacements.json` overrides
    it entirely.

---

## Two modes: server and local

### Server mode (resona-api)

When resona-api is running, replacements are stored in a SQLite database and managed with the
`resona replacements` commands. Every transcript produced by a server-side job has the active
rules applied automatically.

```bash
# List current rules
resona replacements list

# Add a rule
resona replacements add "\\bKomma\\b" ","
resona replacements add "\\bPunkt\\b" "."
resona replacements add "Monique" "Monic"

# Delete a rule by its ID
resona replacements delete 7
```

Rules can be active or inactive. Only active rules are applied.

### Local mode (`~/.resona/replacements.json`)

When running without a server (or to override the server-side defaults), create
`~/.resona/replacements.json`. The CLI reads this file during the local fallback path.

If this file exists, it **replaces the bundled defaults entirely** — add all rules you need.

---

## JSON format

`~/.resona/replacements.json` is a JSON array of objects. Each object has two fields:

| Field | Description |
|-------|-------------|
| `name` | The regex pattern to match (case-insensitive) |
| `replacement` | The literal string to substitute |

```json
[
  {"name": "\\s*Komma", "replacement": ","},
  {"name": "\\s*Punkt", "replacement": "."},
  {"name": "\\s*Absatz", "replacement": "\n"},
  {"name": "\\s*Klammer auf", "replacement": " ("},
  {"name": "\\s*Klammer zu", "replacement": ")"},
  {"name": "Monique", "replacement": "Monic"},
  {"name": "Dr\\. Maier", "replacement": "Dr. Meier"}
]
```

!!! note "Regex syntax"
    Patterns use Python's `re` module syntax. Backslashes must be doubled in JSON
    (`\\b` for word boundary, `\\s` for whitespace). The replacement is a plain string —
    backreferences are not supported.

### Capturing leading whitespace

The ASR engine often inserts a space before each word. The pattern `"\\s*Komma"` matches both
`" Komma"` and `"Komma"` and replaces them with `","` (no leading space), keeping punctuation
attached to the preceding word. This is the recommended pattern for punctuation commands.

### Medical section headings example

```json
[
  {"name": "\\s*Verlauf", "replacement": "\n## Verlauf\n"},
  {"name": "\\s*Medikation", "replacement": "\n## Medikation\n"},
  {"name": "\\s*Procedere", "replacement": "\n## Procedere\n"}
]
```

---

## Config resolution order

When resona-api is not running, the CLI selects the replacement source in this order:

1. `~/.resona/postprocess.json` — full pipeline config; if present, it controls replacements
   via its own `source` reference (see [Postprocessing](postprocessing.md))
2. `~/.resona/replacements.json` — replacements-only override
3. Bundled defaults — German dictation rules

If none of those files exist, the bundled defaults are used automatically.

---

## Replacement rules in the postprocessing pipeline

Replacements are just one step type in the full pipeline. To combine replacements with an LLM
formatting step, use `~/.resona/postprocess.json` instead:

```json
{
  "steps": [
    {"type": "replacements", "source": "replacements.json"},
    {"type": "llm", "name": "format", "prompt": "Format this as a clinical note.", "model": "ollama/llama3"}
  ]
}
```

See [Postprocessing](postprocessing.md) for the complete pipeline documentation.
