# ADR 0002: Profile composition resolves at admission

- Status: accepted
- Date: 2026-11-25
- Issues: [#146](https://github.com/ecsplico/resona/issues/146) (epic SBB), [#147](https://github.com/ecsplico/resona/issues/147) (`include` step), [#156](https://github.com/ecsplico/resona/issues/156) (hub includes)

Terms in this ADR are used as defined in the root `CONTEXT.md`.

## Context

Profiles grew by copying. `dictation-medical-de`, `transcript-markdown-de` and `transcript-polish-de` each carried the same dictation-command rules inline, and the tests had to keep the copies equal. Hosted profiles cannot share a rule list through `source`, because a `source` would read a file from the hub host.

Profiles now compose: a profile lists `{"type": "include", "profile": "<name>"}` steps, and a **block** (an organization's stored profile, else one bundled with the hub) supplies the steps at that position. That raises the question of where an include is resolved:

- a job is pinned at admission: the hub stores the profile document as an expiring artifact and the content-free execution snapshot carries its `sha256`, which the worker checks;
- workers run several releases at once, and the hub decides which worker may claim a job from content-free features;
- an organization edits and deletes its profiles at any time, also while jobs wait.

## Decision

**The hub flattens every profile at admission.** `POST /v1/jobs` (and the adapters on it, `/v1/audio/transcriptions` and `POST /v1/listen`) and the live `WS /v1/listen` replace each include step by the steps of the profile it names, recursively, before `build_pipeline`. The lookup is the organization's latest stored revision of that name, else the bundled block. The pinned document and its `sha256` are the flat profile; workers never see an include step and never look a profile up.

**The snapshot records what was included.** The postprocess meta gains `includes: [{name, revision}]` (revision 0 for a bundled block), in first-use order. It is provenance only: no job feature, no claim rule.

**Writes keep compositions resolvable.** `PUT /profiles/{name}` refuses (422 `invalid_profile`) a body whose includes do not resolve, form a cycle, nest deeper than four levels or reach a profile with `instructions` or `context`, and a body that would make a stored profile including it fail the same way. `DELETE /profiles/{name}` answers 409 `profile_in_use` while another stored profile includes it, directly or through others.

**The stored form stays readable.** `GET /profiles/{name}` returns the profile as stored, include steps and all; `?resolved=true` returns the flat document a job would pin, plus `includes`.

**Hosted profiles still refuse `source`.** Blocks are the hosted way to share a rule list. A bundled block may read its rules from a bundled file; flattening puts those rules inline.

## Consequences

- **Workers need no change.** Any release that ran a flat profile runs a composed one; the job feature check stays as it is.
- **A job keeps what it was admitted with.** Changing or deleting a block later does not change a pinned job, and the snapshot names the revisions that went in.
- **Pinned hashes change once.** Rewriting a catalogue profile as a composition changes its flat document only where the steps change; the golden tests compare the flattened rule lists with the pre-split ones, so outputs do not.
- **An organization's profile shadows a bundled block of the same name**, for its own compositions only. A bundled block can be overridden without a hub release.
- **Cost:** a block that a stored profile includes cannot be deleted first; the including profile goes first. A write is checked against every stored profile that includes it, so a broken dependent is reported at the write, not at the next job.

## Alternatives rejected

- **Resolve on the worker.** The worker would need the organization's profiles at run time, a new hub route and a new job feature, and a block edited between admission and claim would change what a waiting job runs. The pinned `sha256` would no longer describe the work.
- **Copy rules at write time.** Storing the flat document on `PUT` would hide which blocks a profile uses, and editing a block would leave every composition stale.
- **Allow `source` in hosted profiles, restricted to a hub directory.** Shares rule lists, but ties profiles to files on one hub host and to its deployment. Blocks live in the database (or the wheel) and travel with backups and restores.
