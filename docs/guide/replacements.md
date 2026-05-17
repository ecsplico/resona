# Text Replacements

Text replacements convert spoken dictation commands into their written equivalents. They are a
`replacements` step inside a **postprocessing profile**, applied after the ASR engine returns raw
text. Matching is always case-insensitive.

---

## Bundled German dictation defaults

The bundled `default` profile applies these rules automatically with no configuration required:

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

---

## Defining replacement rules in a profile

Replacement rules live inside a profile's `replacements` step. Provide them inline (`rules`) or
point to a JSON file (`source`):

```json
{
  "name": "my-profile",
  "steps": [
    {
      "type": "replacements",
      "rules": [
        {"pattern": "\\s*Komma",      "replacement": ","},
        {"pattern": "\\s*Punkt",      "replacement": "."},
        {"pattern": "\\s*Absatz",     "replacement": "\n"},
        {"pattern": "Monique",        "replacement": "Monic"},
        {"pattern": "Dr\\.\\s*Maier", "replacement": "Dr. Meier"}
      ]
    }
  ]
}
```

Or with a separate rules file:

```json
{"type": "replacements", "source": "my-rules.json"}
```

Each rule object:

| Field | Description |
|-------|-------------|
| `pattern` | Regex pattern (Python `re` syntax, case-insensitive). Backslashes must be doubled in JSON. |
| `replacement` | Substitution string; supports `\n` for newlines and backreferences. |

---

## Capturing leading whitespace

The ASR engine often inserts a space before each word. The pattern `"\\s*Komma"` matches both
`" Komma"` and `"Komma"` and replaces the entire match with `","` (no leading space), keeping
punctuation attached to the preceding word. This is the recommended pattern for dictation commands.

---

## Medical section headings example

```json
[
  {"pattern": "\\s*Verlauf",    "replacement": "\n## Verlauf\n"},
  {"pattern": "\\s*Medikation", "replacement": "\n## Medikation\n"},
  {"pattern": "\\s*Procedere",  "replacement": "\n## Procedere\n"}
]
```

---

## Using custom rules

Create a profile with your rules and push it to the server:

```bash
resona profiles push my-profile.json
resona transcribe dictation.mp3 --profile my-profile
```

Or use it in local mode:

```bash
# Place the profile file in ~/.resona/profiles/
cp my-profile.json ~/.resona/profiles/
resona transcribe dictation.mp3 --profile my-profile
```

See [Postprocessing Profiles](postprocessing.md) for the complete profile documentation.
