# HTTP API (client contract)

The published client contract of resona-api: every route with the `client` audience tag and the schemas those routes use (`ResonaAPI.openapi_client()`). Admin, evaluation, testbench and worker routes are internal and not part of it. The machine-readable document is committed as `contract/openapi.json` and published here as [openapi.json](openapi.json); `info.version` is the hub release id.

- Regenerate after changing a client route or schema: `just contract` (`uv run python scripts/gen_openapi.py`). `packages/api/tests/test_contract.py` fails on a stale document and names that command.
- CI job `contract` (`.github/workflows/test.yml`) compares the committed document with the one at the latest `v*` tag (`oasdiff breaking --fail-on ERR`). A pull request with a breaking change fails unless it carries the label `contract-break`; pushes to `main` only report. A tag that predates `contract/openapi.json` is skipped with a notice.
- TypeScript client: `clients/hub-ts` (`@ecsplico/resona-hub-client`, bun, ESM, runtime dependency `openapi-fetch` only) types every route from `src/schema.d.ts`, generated from this document by `openapi-typescript` (`just contract` regenerates both; CI workflow `clients.yml` fails when the committed types differ). On top of the typed `api` client it adds `HubError{status, code, message}`, `upload`, `submitJob` (one `Idempotency-Key` per call, reused on retries after transport errors and 502–504, as `resona-client` does), `waitForJob`, `resultMarkdown` (range reads joined), `listenPrerecorded` (`POST /v1/listen`, `SyncWaitTimeout` with `jobId` on 504), hand-written types and `openListen` for `WS /v1/listen`, and `verifyWebhook` (Standard Webhooks via WebCrypto). Quick start: `clients/hub-ts/README.md`.
- This page is rendered from the committed document at docs build time by the MkDocs hook `scripts/mkdocs_openapi.py` (no network).

<!-- openapi: contract/openapi.json -->
