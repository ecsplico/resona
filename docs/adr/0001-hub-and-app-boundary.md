# ADR 0001: Hub and app boundary

- Status: accepted
- Date: 2026-11-16
- Issues: [#73](https://github.com/ecsplico/resona/issues/73) (goal), [#75](https://github.com/ecsplico/resona/issues/75) (epic HBL), [#78](https://github.com/ecsplico/resona/issues/78) (repository layout)

Terms in this ADR are used as defined in the root `CONTEXT.md`.

## Context

Resona grew in one repository as two things at once: the hub (`resona-api`, workers, engines, postprocess, CLI, job MCP) and one product built on it (the PWA / Android / iOS app with its Directus data layer and project worker).

At runtime the two are already separate. No Python package imports the app or knows Directus, and the app talks to the hub over HTTP and WebSocket only. But nothing states this as a rule, so it erodes:

- the app's backup mounts the hub's data volume;
- profiles that only make sense for this one product ship inside the `resona-postprocess` wheel;
- guides and READMEs describe hub and app as one product, so a reader cannot tell which layer a feature belongs to.

Other backends should be able to build on the hub without reading its source. That needs a boundary that is written down and can be checked.

## Decision

Resona has two layers, and the rule between them is:

**The hub is a transformation layer.** It owns uploads, jobs, live streaming, profiles, execution policy, workers and evaluation. It knows organizations and credentials, not users. It keeps a job's result for 24 hours and stores nothing else on behalf of an app.

**An app owns everything a user keeps.** Users, dictations, recordings, transcripts, documents, long-term storage and the UI belong to the app.

**The only connection is the published contract.** An app talks to the hub through its HTTP and WebSocket API and nothing else.

- The hub never stores app entities and never calls app code.
- An app never reads hub storage, the hub database or hub source code.

**The PWA / Android / iOS app is the reference app.** It is a real, maintained consumer of the contract. It does not define what the hub is, and it gets no access another app could not have.

**The reference app lives in its own repository** (owner decision, 2026-11-16, [#78](https://github.com/ecsplico/resona/issues/78)). This repository becomes the hub's. Until the extraction lands, `apps/resona-pwa/` is treated as if it were already outside: same rule, same contract.

## Consequences

- **Push is needed.** The hub is pull-only today, so every app has to run its own poller to learn that a job finished. The hub gets signed webhooks for terminal job states next to pull; pull stays the source of truth ([#76](https://github.com/ecsplico/resona/issues/76)).
- **A published contract is needed.** "Without reading hub source" requires the HTTP contract as a committed OpenAPI document with generated clients; the reference app then uses those instead of hand-written request code ([#77](https://github.com/ecsplico/resona/issues/77)).
- **Product profiles leave hub packages.** A profile bundled in a hub wheel must work with no setup and for any app. Product-specific profiles move to a profile catalogue or into the reference app ([#78](https://github.com/ecsplico/resona/issues/78)).
- **The dictation MCP belongs to the app.** `resona-mcp` exposes hub jobs. A server that gives a user access to their own dictations needs users and dictations, which only the app has, so it is built in the reference app ([#79](https://github.com/ecsplico/resona/issues/79)).
- **Each layer backs up its own data.** The hub backs up hub data; the reference app's backup stops mounting the hub volume ([#83](https://github.com/ecsplico/resona/issues/83)).
- **The rule is enforced in CI**, not only documented ([#81](https://github.com/ecsplico/resona/issues/81)), and docs, READMEs and agent guides describe two layers ([#82](https://github.com/ecsplico/resona/issues/82)).
- **Cost:** a feature that spans both layers now needs a contract change first, then an app change, in two repositories with separate CI and issue trackers. The 24-hour result retention means an app that wants to keep a result must fetch and store it itself.

## Alternatives rejected

- **A `reference-app/` directory in this repository.** Makes the layers visible in the tree, but the app could still reach hub files, volumes and source, so the boundary would stay a convention. A separate repository leaves published artifacts as the only way in.
- **A `hub/` wrapper directory in this repository.** Moves every hub path (packages, compose files, CI, docs) for the same weak guarantee, and keeps both layers in one issue tracker and release cycle.
- **The hub stores dictations.** Would spare each app its own storage, but the hub would then need users, ownership, sharing, editing history and long-term retention of patient-related content. That turns the transformation layer into one product's backend and makes it unusable for others. The hub keeps its 24-hour result retention and nothing more.
