# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

- **Hub operator (primary):** the owner of the Resona hub (`https://resona.ecsplico.de`). Uses `/admin` for occasional checks: is a worker online, issue or revoke a token, look at the job queue, read settings and baselines. Manages every organization.
- **Org admins (rare):** manage only their own organization's workers, credentials and jobs.

## Product Purpose

Resona is a private transcription and document-conversion hub: clients submit audio and documents as jobs; approved workers (Mac/Metal, Linux/CUDA, CPU) pull and run them; results return to the organization. `/admin` is the hub's management console for workers, credentials, jobs, settings and evaluation baselines. Success: the operator sees the hub's state at a glance and acts (approve, revoke, issue) without the CLI.

## Positioning

Privacy-first: jobs carry a privacy class, private jobs run only on approved private workers, and the hub never shows job content, filenames or tokens beyond the one-time issue response. Production models run only after an evaluation baseline is promoted per execution profile and platform.

## Operating Context

- Desktop browser for short sessions, and phone for quick checks (e.g. whether a worker is online).
- Sign-in with a bearer token kept in `sessionStorage`; server-side setup and partial worker approval stay in the `resona-admin` CLI.

## Capabilities and Constraints

- Static page served by the hub (`packages/api/src/resona_api/static/admin/`), no build step, vanilla JS.
- Strict CSP: `default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:`. No external assets; third-party code is vendored. No `innerHTML` with API data.
- Component library: Web Awesome 3 (vendored), chosen by the owner as a showcase.
- Terminology: organization, worker (approved / private / revoked, reported vs approved capabilities), credential (client, org_admin, hub_operator, worker), job (kind, status, queue reason, execution profile, candidate), settings revision, baseline (execution profile × platform, revision), approved release.

## Evidence on Hand

Live hub data (org `ecsplico`: workers lorien-1 and mac-matthias, five baselines). No customer names, metrics or testimonials exist; none may be invented.

## Product Principles

1. State before action: the console shows what is true (online, approved, revoked) before offering buttons.
2. Secrets are shown once and never logged or re-displayed.
3. Destructive actions confirm; foreign or missing IDs are indistinguishable.
4. Operator scope never includes job content.
