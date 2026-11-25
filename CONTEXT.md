# Resona

Resona has two layers: the **hub**, which transforms audio, documents and text, and **apps** that build on it. The boundary between them is decided in [ADR 0001](docs/adr/0001-hub-and-app-boundary.md). This glossary fixes the words for each side so that hub code, docs and issues do not borrow app terms, and the other way round.

## Language

### Layers

- **Hub**: The transformation layer: `resona-api` with its workers, engines, postprocessing, CLI and job MCP. _Avoid_: server, backend, gateway, platform, "Resona" alone for this layer.
- **Reference app**: The PWA / Android / iOS app with its own data layer; one maintained consumer of the hub's contract, kept in its own repository. _Avoid_: the product, the frontend, the Resona app, "the PWA" as a name for the whole system.

### Hub terms

- **Job**: One unit of transformation work on the hub (kind audio, document or text), created from one upload and run by a worker. _Avoid_: dictation, task, transcription, request.
- **Upload**: An input file held by the hub until exactly one job consumes it; it expires if no job does. _Avoid_: recording, attachment, file.
- **Result**: The output of a completed job (Markdown, raw text, segments or pages, provenance), kept by the hub for 24 hours. _Avoid_: transcript, document, output.
- **Profile**: A named, ordered list of postprocessing steps (replacements, llm, extract) applied to the engine's output. _Avoid_: template, preset, prompt; never short for execution profile.
- **Block**: A profile meant to be included by others through an `include` step: an organization's stored profile, else one bundled with the hub. Including it puts its steps at that position; the hub resolves every include at admission, so a job always runs a flat profile ([ADR 0002](docs/adr/0002-profile-composition-resolves-at-admission.md)). `"block": true` only hides it from listings. _Avoid_: snippet, fragment, partial, module, template.
- **Execution profile**: A named list of approved candidates (engine, model, accelerator) in an organization's settings that decides where a job may run. _Avoid_: bare "profile", engine config, backend.
- **Model role**: A purpose name (`polish`, `cleanup`, `title`, `extract`, `judge`, or one the organization adds) that an organization's settings map to one of its endpoints; a profile step says `"endpoint": "@<role>"` and admission replaces it with that endpoint. _Avoid_: model alias, side model, model slot; not a credential role (client, org admin, …).
- **Organization**: The hub's tenant: it owns settings, profiles, credentials, workers and jobs. _Avoid_: tenant, account, team, customer, user.
- **Credential**: A token the hub issued to an organization with one role (client, org admin, worker, hub operator); the hub knows credentials, not people. _Avoid_: user, login, account, password.
- **Worker**: An outbound-only process that claims jobs from the hub under a lease and runs engine and postprocessing. _Avoid_: runner, node, engine; not the reference app's project worker.
- **Push (webhook)**: The hub notifies an organization's registered webhook endpoint, signed and without content, when a job reaches a terminal state. _Avoid_: callback, event, notification, push notification (that is the mobile kind).
- **Pull**: An app asks the hub for a job's state and fetches its result; the source of truth, with or without push. _Avoid_: sync, fetch-back; a worker taking a job is a claim, not a pull.

### App terms

The hub has none of these. Hub code, routes, docs and issues do not use them for hub things.

- **Dictation**: What a user of an app speaks and keeps: a recording with its transcript, stored long-term by the app. _Avoid_: job (the hub's word for the work a dictation triggers), memo, note.
- **Recording**: The audio a user recorded or imported in an app, stored by the app. _Avoid_: upload (the hub's short-lived copy), audio file.
- **Transcript**: The text an app stores for a recording, taken from a result or from live streaming and editable by the user. _Avoid_: result (the hub's 24-hour output), transcription (the act).
- **Document**: A PDF, image or Word file a user keeps in an app together with its converted Markdown. _Avoid_: scan, file; on the hub "document" only names a job kind, never a stored entity.

## Relationships

- An **organization** holds many **credentials**, **workers**, **profiles** and **execution profiles**.
- An **upload** is consumed by exactly one **job**; a completed **job** has one **result**.
- A **job** runs under one **execution profile** and at most one **profile**.
- A **profile** includes zero or more **blocks**; a **block** is itself a profile and may include others.
- An app turns a **recording** or **document** into an **upload** and a **job**, learns the outcome by **pull** or **push**, and stores the **result** as its own **transcript** or **document**.
- A **dictation** may cause several **jobs** (transcribe, postprocess again, title); a **job** knows nothing about the dictation it serves.

## Example dialogue

> **Dev:** "The user's dictation is stuck. Can the hub show me the dictation?"
> **Domain expert:** "The hub has no dictations. The app made a **job** from the **recording**; ask the hub for that job's state."
> **Dev:** "The job completed yesterday. So the transcript is safe on the hub?"
> **Domain expert:** "No. The hub keeps the **result** for 24 hours. The **transcript** exists only once the app has pulled the result and stored it."
