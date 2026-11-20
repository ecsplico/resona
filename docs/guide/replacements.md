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
| Absatz | blank line (new Markdown paragraph) |
| Kapitel *Titel* | `## Titel` (title ≤ 5 words, ends at `Absatz`/`Punkt`/`.` …; see [Postprocessing](postprocessing.md#default)) |
| Klammer auf | `(` |
| Klammer zu | `)` |

In addition, the bundled defaults include `Doppelpunkt`, `Fragezeichen`, `Ausrufezeichen`,
`Anstrich` (list item), `Hashtag` and `Eckige Klammer auf`/`zu`. `default` holds only these
generic commands.

## Medical dictation: `dictation-medical-de`

The German medical section headings (`Kapitel Verlauf`, `Kapitel Medikation`,
`Kapitel Psychopathologischer Befund`, `Kapitel Procedere`, no terminator needed) and the name
corrections (`Monique` → `Monic`, …) live in the catalogue profile `dictation-medical-de`
(`profiles/dictation-medical-de.json` in the repository): name corrections first, then the
headings, then the generic rules of `default` inline, in that order. An org admin pushes it once
and selects it per job or as the organization's `postprocess_profile`:

```bash
resona profiles push dictation-medical-de profiles/dictation-medical-de.json
resona transcribe dictation.mp3 --profile dictation-medical-de
```

!!! warning "Release note: the split of `default`"
    Up to 0.2.7 `default` also carried the medical headings and the name corrections. Its output
    for text with only generic commands is unchanged; a medical dictation with `default` now keeps
    the names as spoken and leaves a heading without a terminator as text (`Kapitel Verlauf Komma
    stabil Punkt` → `Kapitel Verlauf, stabil.`). Fix: push and select `dictation-medical-de`, which
    produces the old `default` output. See [Postprocessing](postprocessing.md#dictation-medical-de).

!!! tip "Inspecting the rules"
    The generic rules live in
    `packages/postprocess/src/resona_postprocess/default_replacements.json`, the medical ones in
    `profiles/dictation-medical-de.json`.

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
