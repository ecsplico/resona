# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

## Structure labels

| Label  | Meaning |
| ------ | ------- |
| `epic` | An effort's plan; its tickets are native sub-issues (see `issue-tracker.md`) |

## Area labels

Every issue carries at least one `area:*` label: the component(s) it changes.

| Label             | Paths |
| ----------------- | ----- |
| `area:hub`        | `packages/api` (resona-api), `packages/job-core`, `packages/client` |
| `area:engines`    | `packages/asr-core`, `packages/engine-*`, `packages/cloud-stt`, `packages/cloud-tts`, `packages/tts-local` |
| `area:postprocess`| `packages/postprocess` |
| `area:worker`     | `apps/resona-worker` |
| `area:cli`        | `apps/resona-cli` |
| `area:mcp`        | `apps/resona-mcp` |
| `area:pwa`        | `apps/resona-pwa` (frontend, Directus, project worker) |
| `area:eval`       | `packages/eval`, `eval/`, `benchmarks/` |
| `area:deploy`     | `docker-compose.*.yml`, Dockerfiles, `scripts/`, `release/`, `.github/workflows/` |
| `area:docs`       | `docs/`, `mkdocs.yml`, READMEs |

Add a new `area:*` label (`gh label create area:<name> --color 1d76db --description "<paths>"`) together with a row here when a new top-level component appears.
