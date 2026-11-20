# Profile catalogue

Postprocess profiles that need a decision or setup by the organization, so they are not part of
the `resona-postprocess` wheel. The wheel bundles only `default`, which works with no setup. A hub
knows a catalogue profile only after an org admin pushed it:

```bash
resona profiles push <name> profiles/<name>.json    # from a checkout of this repository
```

Without a checkout, download the file of the release tag you run (`vX.Y.Z`) and push that.
Each push stores a new revision for the organization of the credential in use; jobs name the
profile with `--profile <name>` (or the organization's `postprocess_profile`).

| Profile | What it does | Needs |
|---------|--------------|-------|
| `dictation-medical-de` | German medical dictation → Markdown: name corrections, the headings `Kapitel Verlauf`/`Medikation`/`Psychopathologischer Befund`/`Procedere` (no terminator needed), then the generic commands of `default`. Rules only; what `default` did before the split | Nothing: runs on every worker |
| `transcript-markdown-de` | The command rules of `dictation-medical-de` (medical headings + generic commands), without its name corrections. Rules only | Nothing: runs on every worker |
| `transcript-polish-de` | The same rules, then a chunked LLM pass that may change only capitalization, punctuation and whitespace | A `local_llm` endpoint with the id `local-llm` |
| `document-polish-de` | Chunked LLM pass over document Markdown: OCR corrections only | A `local_llm` endpoint with the id `local-llm` |

The endpoint id `local-llm` names no host. An org admin registers a `local_llm` endpoint under
that id with a loopback `url` and the approved worker's `worker_id` in the organization settings;
see [LLM polish on the worker host](../docs/server/linux-worker.md#llm-polish-on-the-worker-host).
Until then the hub refuses a job that names one of the two polish profiles (`policy_denied`).

Profiles carry their rules inline: hosted profiles reject `source`.
`packages/postprocess/tests/test_dictation_rules.py` keeps `dictation-medical-de` equal to the rule
list `default` used before the split and the transcript profiles' inline rules equal to its tail
(everything after the name corrections), and `test_profile_catalogue.py` in this directory
validates every file with `Profile.from_dict`.

Stored profile revisions on a hub are never touched by a release. A hub that resolved these
profiles from the wheel before they moved here must push them once.
