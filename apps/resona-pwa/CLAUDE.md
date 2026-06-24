# CLAUDE.md — Resona PWA development guide

## Project overview

Resona PWA is the **browser frontend** for the Resona transcription platform: a Nuxt 4
SPA (`ssr: false`) for medical dictation. Users record or upload audio, it gets
transcribed by the backend, and they review/edit/export the result. Built on the Hearth
design system (plain Vue + Tailwind v4 CSS tokens — no component library).

**This repo is standalone.** It is NOT part of the Python monorepo. It talks to its
backends over HTTP/WebSocket only. Never import from or reach into the Python monorepo —
communicate over HTTP exclusively.

The frontend is otherwise a pure client-rendered SPA. The **one** sanctioned server-side
exception is a thin Nitro layer (`server/api/**`) used **only** to hold confidential OAuth
secrets that cannot live in the browser — specifically the Google Calendar token
exchange/refresh (the OAuth client secret + the per-user refresh token). Nothing else goes
on the server: no business logic, no page rendering (`ssr: false` stays). Because these
routes execute server-side, production **must** be deployed via the Nitro Node/serverless
server (`nuxt build` → `node .output/server`), **not** `nuxt generate` static output (which
strips the server). Keep secrets in `runtimeConfig` (the private, non-`public` block).

## How to start

Two stacks are involved. See **README.md → Quick start** for the canonical steps. In
short:

```bash
# Transcription backend — from the resona monorepo root (resona-api + engine only):
docker compose -f docker-compose.resona.yml --profile faster-whisper up -d
#   resona-api :7710 · engine :7720

# Data layer — from this repo (Directus + the directus-transcribe worker):
cp .env.stack.example .env  # Directus + worker secrets (NOT the frontend .env)
docker compose up -d        #   directus :7700 · directus-transcribe worker

# Frontend — from this repo:
bun install
cp .env.example .env        # frontend NUXT_PUBLIC_* vars (NOT the stack .env)
bun run dev                 # → http://localhost:3000
```

Two `.env` consumers, both named `.env`: the **compose stack** is seeded from
`.env.stack.example` (`DIRECTUS_*` / `RESONA_API_URL`), the **Nuxt frontend** from
`.env.example` (`NUXT_PUBLIC_*`). Keys are disjoint, so they don't conflict.

The data layer self-bootstraps: `docker compose up` runs the one-shot
`directus-bootstrap` service (`directus/bootstrap.mjs`) once Directus is healthy,
which idempotently provisions collections, the `user` role/policy, and the
`transcribe-worker` user with its static token pinned to `DIRECTUS_TOKEN` (so set
that in `.env` first, e.g. `openssl rand -hex 24`). See `directus/bootstrap.md`.
CORS on Directus and resona-api must allow the PWA origin.

### Worker

The `directus-transcribe` worker lives in `worker/` — a standalone Python service
(`httpx` + `python-decouple`, no monorepo coupling). It bridges **this repo's** Directus
→ the remote resona-api: polls for `pending` recordings, transcribes them via
`RESONA_API_URL`, and writes the `transcripts` row. Build/run it via `docker compose`
(the `directus-transcribe` service in this repo's `docker-compose.yml`).

## Backends

- **Directus** (`:7700`) — auth, data, file storage via `@directus/sdk`. Holds the
  `recordings` and `transcripts` collections.
- **resona-api** (`:7710`) — transcription API. The PWA uses its Deepgram-compatible
  live WebSocket `/v1/listen` for real-time dictation.

### Two transcription paths

- **Batch** (record-then-save, or upload a file): PWA uploads audio to Directus + inserts
  a `recordings` row `status=pending, source=batch`. This repo's **directus-transcribe**
  worker (in its compose stack) polls Directus, transcribes via the remote resona-api,
  writes the `transcripts` row, flips the recording to `done`. The PWA polls until
  terminal (`done`/`error`). Without the worker running, batch rows stay `pending`
  forever.
- **Live**: PWA opens a WebSocket to resona-api `/v1/listen`, streams 16 kHz PCM16 from
  the mic, renders rolling partial/final text. On stop the PWA itself writes the
  `recordings` (`source=live, status=done`) and `transcripts` rows.

### Live WebSocket contract (resona-api `/v1/listen`)

`ws://<host>/v1/listen?encoding=linear16&sample_rate=16000&interim_results=true&language=&engine=&token=<RESONA_API_KEY>`
- Browser WebSockets can't set headers → auth goes in `?token=`.
- Up: binary PCM16 LE, 16 kHz mono. Down: Deepgram JSON
  `{type:"Results",channel:{alternatives:[{transcript}]},is_final}` + `{type:"Metadata"}`.
- Finish: send `{"type":"CloseStream"}`, then close.

## Tech stack

- **Nuxt 4** (`app/` dir layout, SPA `ssr: false`), **bun** (`bun@1.3.14`), TypeScript
- **Tailwind v4** via `@tailwindcss/vite` — CSS-first `@theme`, design tokens in
  `app/assets/css/tokens.css`. **No `tailwind.config.js`.**
- **@directus/sdk v22** — `createDirectus<Schema>(url).with(authentication("json")).with(rest())`
- **@vite-pwa/nuxt** — service worker + manifest (installable, offline)
- **@vueuse/nuxt** (useWebSocket), **@nuxt/fonts** (Fraunces / Instrument Sans / JetBrains Mono)
- **idb-keyval** — IndexedDB offline queue
- **Vitest 4** + **@nuxt/test-utils** (environment `nuxt`, happy-dom); **fake-indexeddb**
  polyfill

## Project structure

```
app/
├── app.vue, layouts/default.vue
├── middleware/auth.global.ts        # redirect unauthenticated → /login
├── pages/
│   ├── index.vue                    # history list + search/date filter + record FAB
│   ├── login.vue                    # Directus email/password login
│   ├── live.vue                     # live transcription
│   └── recording/[id].vue           # detail: player, transcript edit, export
├── composables/                     # useDirectus, useRecordings, useRecording,
│   │                                #   useRecorder, useUpload, useProfiles, useLive,
│   │                                #   useTranscriptEdit, useExport, useOfflineQueue
├── components/
│   ├── hearth/                      # HButton, HCard, HChip, HField, ... (design system)
│   └── RecordingListItem, QueuedListItem, SaveSheet, StatusChip
├── utils/pcm.ts                     # float32 → 16 kHz int16 PCM for live streaming
├── types/directus.ts                # Recording / Transcript / Schema
└── assets/css/                      # tokens.css + main.css
```

Component auto-import is `pathPrefix: false` — components resolve by filename
(`<HButton>`, `<QueuedListItem>`), regardless of subdirectory.

## Directus schema (source of truth: `directus/bootstrap.md`)

- **recordings**: `id, user_created, date_created, date_updated, title, audio_file,
  duration_seconds, language, profile, status (pending|transcribing|done|error),
  error_message, source (batch|live)` — **NO `engine` field**.
- **transcripts**: `id, recording, text, structured, segments, engine, date_created,
  date_updated` — the `engine` field lives **HERE**, not on recordings.

When writing the live path, set `engine` on the transcripts row only; the recordings
payload must NOT include `engine`.

## Configuration

All public config flows through `runtimeConfig.public` (set via `NUXT_PUBLIC_*` env vars,
defaults in `nuxt.config.ts`). Read it with `useRuntimeConfig().public` — never hardcode
URLs. The 6 keys:

| Var | Default | Meaning |
|-----|---------|---------|
| `NUXT_PUBLIC_DIRECTUS_URL` | `http://localhost:7700` | Directus base URL |
| `NUXT_PUBLIC_RESONA_API_URL` | `http://localhost:7710` | resona-api base URL |
| `NUXT_PUBLIC_RESONA_API_KEY` | (empty) | optional resona-api key for live streaming |
| `NUXT_PUBLIC_DEFAULT_LANGUAGE` | `de` | default transcription language |
| `NUXT_PUBLIC_DEFAULT_PROFILE` | `default` | default postprocessing profile |
| `NUXT_PUBLIC_DEFAULT_ENGINE` | `faster-whisper` | default live engine |

Never commit `.env` (gitignored); only `.env.example` is tracked.

## Commands

| Command | Description |
|---------|-------------|
| `bun run dev` | Nuxt dev server (`:3000`) |
| `bun run build` | Production build (generates SW + manifest) |
| `bun run preview` | Preview the production build |
| `bun run test` | Vitest once · `bun run test:watch` for watch mode |
| `bun run lint` | ESLint · `bun run lint:fix` to auto-fix |
| `bun run typecheck` | `nuxt typecheck` (vue-tsc) |

Gates before considering work done: **test, lint, typecheck** all green (plus `build`
for PWA-manifest/service-worker changes).

## Code style

`@nuxt/eslint` stylistic: **tabs, double quotes, semicolons, trailing commas
(always-multiline)**. Run `bun run lint:fix`.

## Testing notes

- Vitest `nuxt` environment → **happy-dom**. Guard all browser-only APIs
  (`MediaRecorder`, `AudioContext`, `IndexedDB`, `navigator.clipboard`) so specs don't
  crash; degrade gracefully when an API is absent.
- `mockNuxtImport` / `vi.mock` factories are **hoisted** → wrap referenced consts in
  `vi.hoisted(() => ...)`.
- `$fetch` can't be mocked via `mockNuxtImport` — stub `globalThis.$fetch`.
- Don't mock `useRuntimeConfig` (breaks `setupNuxt`); rely on real `nuxt.config` defaults.
- Composable singleton refs (e.g. `isAuthenticated`, `accessToken`) bleed across tests —
  reset them in `beforeEach`.
- The offline queue uses **`fake-indexeddb/auto`** in the specs that exercise it.

## What NOT to do

- Don't import from / reach into the Python monorepo — HTTP only.
- Don't put server logic in the PWA beyond the one sanctioned thin Nitro OAuth layer
  (`server/api/**`, confidential Google token exchange/refresh only) — it's otherwise a
  pure SPA: no page rendering (`ssr: false`), no business logic server-side.
- Don't deploy via `nuxt generate` (static) now that Nitro routes exist — use the Nitro
  Node/serverless server (`nuxt build` → `node .output/server`).
- Don't hardcode backend URLs — use `useRuntimeConfig().public`.
- Don't set `engine` on a `recordings` row — it belongs on `transcripts`.
- Don't set live-WS auth via headers — browsers can't; use `?token=`.
- Don't commit `.env`.
