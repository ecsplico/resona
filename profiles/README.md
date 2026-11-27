# Profile catalogue

Postprocess profiles that need a decision or setup by the organization, so they are not part of
the `resona-postprocess` wheel. The wheel bundles only `default` and the blocks
`dictation-commands-de`, `filler-words-de` and `filler-words-en`, which work with no setup. A hub
knows a catalogue profile only after an org admin pushed it:

```bash
resona profiles push profiles/                      # the whole catalogue, from a checkout of this repository
resona profiles push profiles/dictation-medical-de.json profiles/blocks/names-medical-de.json profiles/blocks/headings-medical-de.json
```

`resona profiles push` takes files and directories (a directory adds its `*.json` and
`blocks/*.json`), stores each file under its file name and pushes blocks before the profiles that
include them. An include must name a profile pushed in the same call, a bundled block or a profile
already stored on the hub; a cycle or a missing include stops the push before anything is uploaded.
Without a checkout, download the files of the release tag you run (`vX.Y.Z`) and push those.
Each push stores a new revision for the organization of the credential in use; jobs name the
profile with `--profile <name>` (or the organization's `postprocess_profile`).

## Profiles

Each profile except `cleanup-judge-de` is a composition: `include` steps the hub flattens at job admission.

| Profile | Includes | What it does | Needs |
|---------|----------|--------------|-------|
| `dictation-medical-de` | `names-medical-de`, `headings-medical-de`, `dictation-commands-de` | German medical dictation → Markdown: name corrections, the headings `Kapitel Verlauf`/`Medikation`/`Psychopathologischer Befund`/`Procedere` (no terminator needed), then the generic commands of `default`. Rules only; what `default` did before the split | Nothing: runs on every worker |
| `transcript-markdown-de` | `headings-medical-de`, `dictation-commands-de` | The command rules of `dictation-medical-de` (medical headings + generic commands), without its name corrections. Rules only | Nothing: runs on every worker |
| `transcript-polish-de` | `transcript-markdown-de`, `polish-punctuation-de` | The same rules, then a chunked LLM pass that may change only capitalization, punctuation and whitespace | The role `polish` |
| `document-polish-de` | `polish-ocr-de` | Chunked LLM pass over document Markdown: OCR corrections only | The role `polish` |
| `dictation-clean-de` | `filler-words-de`, `names-medical-de`, `headings-medical-de`, `dictation-commands-de`, `self-correction-de` | `dictation-medical-de` with German filler words (äh, ähm, hm, …) removed first and spoken self-corrections resolved last | The role `cleanup` |
| `cleanup-judge-de` | none | Judge of `resona eval cleanup`: one `extract` step `judge` reads a labelled document (rubric, text before/selection/after the insertion point, raw transcript, cleaned result) and returns `{"pass": bool, "reason": str}` in `structured.judge`; cosmetic punctuation differences pass, changed meaning or copied surrounding text fail. Temperature 0. See [Cleanup evaluation](../docs/guide/evaluation.md#cleanup-evaluation) | The role `judge` |

## Blocks

Blocks (`blocks/`, `"block": true`) are profiles meant to be included; a job may still name one.

| Block | What it does | Needs |
|-------|--------------|-------|
| `names-medical-de` | Name corrections (`Monique` → `Monic`, `Serafine`/`Serafina` → `Seraphine`, `Lucy` → `Lucie`, `Nele`/`Mele` → `Neele`). Rules only | Nothing |
| `headings-medical-de` | The four medical `Kapitel` headings, no terminator needed; a spoken `Absatz`/`Punkt`/`Doppelpunkt`/`Komma` after them is swallowed. Rules only | Nothing |
| `polish-punctuation-de` | Chunked LLM step on `@polish`: capitalization, punctuation and whitespace only; no word may change (12000 chars, 3 attempts, 10 % length guard, markup guard, temperature 0) | The role `polish` |
| `polish-ocr-de` | Chunked LLM step on `@polish`: OCR corrections only (12000 chars, 3 attempts, 30 % length guard, markup guard) | The role `polish` |
| `self-correction-de` | Chunked LLM step on `@cleanup`: resolves a spoken self-correction only where the cue `"nein, streich das"` or `"Korrektur"` clearly marks one, otherwise returns the text unchanged (12000 chars, 3 attempts, 50 % length guard, markup guard, temperature 0) | The role `cleanup` |

LLM steps name a model role (`"endpoint": "@polish"`), never an endpoint id or a model. An org
admin assigns each role to an approved endpoint in the organization settings (`model_roles`),
for example a `local_llm` endpoint with a loopback `url` and the approved worker's `worker_id`;
see [LLM polish on the worker host](../docs/server/linux-worker.md#llm-polish-on-the-worker-host).
Until then the hub refuses a job whose profile names an unassigned role (`role_unassigned`).

Profiles carry their rules inline: hosted profiles reject `source`.
`packages/postprocess/tests/test_dictation_rules.py` flattens the compositions over this
directory, `blocks/` and the bundled blocks, and keeps the flattened `dictation-medical-de` equal
to the rule list `default` used before the split and the transcript profiles' flattened rules
equal to its tail (everything after the name corrections). `test_profile_catalogue.py` in this
directory validates every file with `Profile.from_dict` and checks that each one flattens.

## Migrating from the flat catalogue

Needs hub release 0.2.8 or later (include steps, model roles); workers need no update, because the
hub flattens includes and resolves roles at admission. Stored profile revisions on a hub are never
touched by a release. An organization that pushed the earlier flat profiles (rules inline, LLM
steps on the endpoint id `local-llm`):

1. maps the roles to that endpoint, so nothing else changes: in the organization settings
   (`resona settings get`, edit, `resona settings set --file`) set
   `"model_roles": {"polish": "local-llm", "title": "local-llm", "cleanup": "local-llm"}`;
2. re-pushes the catalogue: `resona profiles push profiles/` (and the reference app's `title-de`,
   `just pwa push-profiles`).

The flattened profiles give the same output as the flat ones; only the pinned profile hash in a
job's provenance changes.
