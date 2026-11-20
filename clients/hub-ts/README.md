# @ecsplico/resona-hub-client

TypeScript client for the Resona hub's published HTTP contract ([`contract/openapi.json`](../../contract/openapi.json)). ESM with types; runs in browsers, Node 20+ and Bun. Its only runtime dependency is `openapi-fetch`.

## Quick start

```ts
import { readFile } from "node:fs/promises";
import { createHubClient, HubError } from "@ecsplico/resona-hub-client";

const hub = createHubClient({ baseUrl: "https://hub.example.org", token: process.env.RESONA_API_KEY! });

try {
  const file = new Blob([await readFile("memo.m4a")]); // or a browser File
  const { job_id } = await hub.submitJob({ kind: "audio", file, filename: "memo.m4a", language: "de" });
  const job = await hub.waitForJob(job_id, { pollMs: 2000, signal: AbortSignal.timeout(600_000) });
  if (job.status === "completed") console.log(await hub.resultMarkdown(job_id));

  const { data } = await hub.api.GET("/v1/engines"); // every contract route, typed
} catch (error) {
  if (error instanceof HubError) console.error(error.status, error.code, error.message);
  else throw error;
}
```

## What is in it

- `createHubClient({baseUrl, token, fetch?})`: `api` (typed `openapi-fetch` client), `upload`, `submitJob`, `getJob`, `waitForJob`, `resultMarkdown`. `submitJob` generates the `Idempotency-Key` once and reuses it when it retries after a transport error or a 502–504, like the Python `ResonaClient`. Errors are `HubError{status, code, message}` from the hub's `{error, message}` body.
- `listen`: types and `openListen` / `listenUrl` for the Deepgram-style `WS /v1/listen` (binary `linear16` frames, `CloseStream`; `Results`, `Metadata`, `Error`).
- `verifyWebhook(headers, body, secret)`: Standard Webhooks verification of job webhooks with WebCrypto, returning the `JobEvent`.

## Development

`src/schema.d.ts` is generated from the contract: `bun run generate` (or `just contract` at the repository root, which also rewrites the contract). CI fails when it differs from the committed file. `bun run build`, `bun run test`, `bun run typecheck`.
