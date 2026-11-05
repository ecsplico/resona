# Resona PWA

A Nuxt 4 progressive web app for medical dictation and transcription, built on the
Hearth design system. It is a multi-user, installable, offline-capable front end that
records or uploads audio, runs it through Resona's transcription backend, and lets you
review, edit, and export the resulting transcripts. The app is browser-only
(`ssr: false`) and talks to its backends over HTTP/WebSocket — it holds no server
logic of its own.

## Quick start

The platform is split across **two stacks**:

1. **Resona hub** — **external**, from the resona monorepo (local) or Coolify
   (`https://resona.ecsplico.de`). This repo runs no transcription engines.
2. **PWA data layer** — lives in **this repo**: Directus + the `project-worker`
   (`resona-project-worker`), plus the Nuxt frontend.

For **local development** you can bring up the data layer and the PWA container with
one command — see [Full local stack](#full-local-stack-one-command).

### 1. Resona hub (external)

Run the hub from the resona monorepo (see its README) or use the Coolify deployment.
Issue a client token for your organization there
(`resona-admin credential issue --organization <org> --role client`). The hub's
`CORS_ORIGINS` must allow the PWA origin (e.g. `http://localhost:3000`).

The token is entered **per device** in the PWA under **Einstellungen → Verbindung**
(stored in the browser's localStorage, never baked into the bundle). "Verbindung
testen" checks it against the hub. Without a token the home screen shows a notice;
push-to-talk and live dictation need it.

### 2. PWA data layer (this repo)

```bash
cp .env.stack.example .env  # then fill in secrets (Directus + worker)
docker compose up -d        # → directus :7700 + project-worker
```

> **Note on `.env`:** the **docker compose stack** reads `.env` seeded from
> `.env.stack.example` (Directus + worker secrets: `DIRECTUS_*`, `RESONA_API_URL`, …).
> The **frontend** (step 3) reads its own `.env` seeded from `.env.example`
> (`NUXT_PUBLIC_*` keys). Both files end up named `.env` but feed different consumers
> and their keys are disjoint, so they don't conflict — just be sure to seed each from
> the right example.

This brings up the data layer:

| Service | Host port | Role |
|---------|-----------|------|
| `directus` | `:7700` | auth, data, file storage |
| `directus-bootstrap` | — | one-shot: provisions collections, roles, and the worker token, then exits |
| `project-worker` | — | sends pending recordings + documents through the hub as durable jobs and writes results back |

The worker reaches the external Resona hub over the network via `RESONA_API_URL`
(default `http://host.docker.internal:7710`) and authenticates with
`RESONA_API_KEY`, a **required** org-bound hub client token. Optional:
`WORKER_ENGINE` (empty = hub default), `WORKER_POLL_INTERVAL`, `WORKER_BATCH`,
`WORKER_STALE_MINUTES` (see `worker/README.md`). **Set `DIRECTUS_TOKEN` in `.env`
before `up`** (e.g. `openssl rand -hex 24`) — `directus-bootstrap` pins it as the
worker's static token and the worker won't start without it. Bootstrapping
(collections + user role + worker token) is automatic and idempotent; see
`directus/bootstrap.md`. CORS on Directus must allow the PWA origin.

### 3. Frontend (this repo)

```bash
bun install
cp .env.example .env        # defaults already point at :7700 / :7710
bun run dev                 # → http://localhost:3000
```

Open `http://localhost:3000`, log in with a Directus user, and record/upload/dictate.

## Full local stack (one command)

For local development, `docker-compose.yml` brings up the PWA, the Directus data
layer and the worker in a single compose project, driven from this repo.
Transcription runs on the external hub (`RESONA_API_URL` / `RESONA_API_KEY` in `.env`).

```bash
bun run setup          # generate secrets → start the whole stack
```

`bun run setup` (a thin wrapper around `docker-compose.yml`) does two things:

1. Creates `.env` from `.env.stack.example` if missing, then **generates and
   persists** `DIRECTUS_TOKEN` + `DIRECTUS_SECRET` into it (idempotent — existing
   values are kept). The token is interpolated into both `directus-bootstrap`
   (which pins it as the worker's static token) and the worker, so there's no
   copy-paste.
2. Runs `docker compose -f docker-compose.yml up -d --build`.

| Service | Host port | Role |
|---------|-----------|------|
| `pwa` | `:7730` | Nuxt SPA served by its Nitro server |
| `directus` | `:7700` | auth, data, file storage |
| `directus-bootstrap` | — | one-shot: provisions collections, roles, worker token, then exits |
| `project-worker` | — | durable hub jobs: recordings + documents (Directus → hub → Directus) |

### `bun run setup` flags

| Command | Effect |
|---------|--------|
| `bun run setup` | Write secrets, start the stack. |
| `bun run setup --setup-only` | Only write `.env` (don't start anything). |
| `bun run setup --down` | Stop the stack (append `--volumes` to wipe data). |
| `PWA_PORT=7702 bun run setup` | Publish the PWA on a different host port. |

Extra args pass through to `docker compose`. Prerequisites: Docker and a reachable
Resona hub. Because the PWA has server routes (the Google Calendar OAuth layer), its
image runs the **Nitro server** (`nuxt build` → `node .output/server`), not static
`nuxt generate`.

## Architecture

Resona PWA is a **standalone repository** (it is not part of the Python monorepo) and
communicates with two backend services over HTTP only:

- **Directus** (`:7700`) — auth, data, and file storage via `@directus/sdk`. Holds the
  `recordings` and `transcripts` collections and the user role. The PWA authenticates
  the end user, uploads audio files, creates `recordings` rows, and reads back
  transcripts.
- **Resona hub** (`NUXT_PUBLIC_RESONA_API_URL`) — the external transcription hub. The
  PWA reaches it only through `useHub()` with the per-device token (`Authorization:
  Bearer`): profiles, push-to-talk via sync `/v1/audio/transcriptions`, and the live
  streaming WebSocket `/v1/listen` (Deepgram-compatible, token as `?token=`).

There are **two transcription paths**:

- **Batch** (record-then-save, or upload an existing file): the PWA uploads the audio
  to Directus and inserts a `recordings` row with `status=pending`, `source=batch`. The
  **project-worker** (running in this repo's compose stack) claims the row, uploads the
  audio to the hub and submits a durable job (job id kept in `hub_job_id`, idempotency key in
  `hub_job_key`, so restarts and lost responses never submit twice), writes the `transcripts` row from the result, flips the
  recording to `done`, and deletes the hub job. Uploaded **documents** (PDF/images)
  take the same path into `documents.markdown`/`pages`. The PWA polls the recording until it reaches a
  terminal state (`done`/`error`). The **Dokumente** page (`/documents`, linked from the
  app header) uploads PDFs/images, lists them with status, and shows the resulting
  markdown (copy, `.md` download, retry, delete) in both Directus and standalone mode.
- **Live**: the PWA opens a WebSocket to the hub `/v1/listen`, streams 16 kHz PCM
  from the microphone, and renders rolling partial/final text. On stop it writes the
  `recordings` (`source=live`, `status=done`) and `transcripts` rows itself.
  The live source is chosen in Einstellungen → Live: a hub stream (engine picked from
  the hub's `/v1/listen/engines`; the page refuses to start if that list is empty) or
  on-device recognition (local-only Web Speech; no cloud browser recognition).
  Optionally ("Mit besserem Modell korrigieren") the live text is re-transcribed in
  8–30 s chunks by a better hub audio engine; saving waits for pending chunks.

### Standalone mode

Set `NUXT_PUBLIC_DATA_MODE=standalone` to run without Directus: no CMS and no login,
all data is stored in the browser's IndexedDB. The browser itself submits pending
recordings/documents to the hub and fetches the results, so processing only happens
while the app is open, and a hub device token (Einstellungen → Verbindung) is required.

## Tech stack

- **Nuxt 4** (`app/` dir, SPA mode `ssr: false`)
- **bun** as package manager and runtime (`bun@1.3.14`)
- **TypeScript**
- **Tailwind CSS v4** via `@tailwindcss/vite` with CSS design tokens
- **@directus/sdk v22** — backend data/auth/files
- **@vite-pwa/nuxt** — service worker + web manifest (installable, offline)
- **@vueuse/nuxt**, **@nuxt/fonts** (Fraunces, Instrument Sans, JetBrains Mono)
- **idb-keyval** — IndexedDB offline queue
- **Vitest 4** + **@nuxt/test-utils** for unit/component tests

## Prerequisites / backend dependencies

The PWA requires both stacks running:

1. **Directus** (this repo's compose stack) with the `recordings`, `transcripts` and
   `documents` collections and the end-user role provisioned. See `directus/bootstrap.md` and the
   platform design spec
   `docs/superpowers/specs/2026-06-17-pwa-directus-platform-design.md`.
2. **Resona hub** (external) — for push-to-talk and live transcription (and as the API
   the worker calls). Enter the device token under Einstellungen → Verbindung.
3. **project-worker** (this repo's compose stack) — required for the **batch** and
   **document** paths; without it, uploaded/recorded files stay `pending` forever.

In development, **CORS on both Directus and the hub must allow the PWA origin**
(e.g. `http://localhost:3000`).

## Setup

```bash
bun install
cp .env.example .env
# edit .env and set the NUXT_PUBLIC_* vars for your backends
```

## Environment variables

All public runtime config is provided via `NUXT_PUBLIC_*` env vars (see `.env.example`).
Defaults are defined in `nuxt.config.ts` under `runtimeConfig.public`. No hub token or
engine is configured at build time — the token is per device (Einstellungen →
Verbindung).

| Variable | Default | Meaning |
|----------|---------|---------|
| `NUXT_PUBLIC_DIRECTUS_URL` | `http://localhost:7700` | Base URL of the Directus instance (auth, data, file storage). |
| `NUXT_PUBLIC_RESONA_API_URL` | `http://localhost:7710` | Base URL of the Resona hub. |
| `NUXT_PUBLIC_DEFAULT_LANGUAGE` | `de` | Default transcription language. |
| `NUXT_PUBLIC_DEFAULT_PROFILE` | `default` | Default postprocessing profile name. |
| `NUXT_PUBLIC_DATA_MODE` | `directus` | Data backend: `directus` \| `standalone`. |
| `NUXT_PUBLIC_CALENDAR_ENABLED` | `false` | Enable the calendar integration (nav link, settings tab, `/calendar`, `/api/calendar`; all 404 when off). Needs the `NUXT_GOOGLE_*` keys and `NUXT_SESSION_PASSWORD`. |

## Commands

| Command | Description |
|---------|-------------|
| `bun run dev` | Start the Nuxt dev server. |
| `bun run build` | Production build (generates the PWA service worker + web manifest). |
| `bun run generate` | Static site generation. |
| `bun run preview` | Preview the production build. |
| `bun run test` | Run the Vitest suite once. |
| `bun run test:watch` | Run Vitest in watch mode. |
| `bun run lint` | Lint with ESLint (`@nuxt/eslint`, stylistic: tabs, double quotes, semicolons, trailing commas). |
| `bun run lint:fix` | Lint and auto-fix. |
| `bun run typecheck` | Type-check with `nuxt typecheck` (vue-tsc). |

## Project structure

```
app/
├── app.vue
├── layouts/default.vue              # shell: header, page, settings drawer; desktop: recordings aside + detail pane
├── middleware/auth.global.ts        # redirects unauthenticated users to /login
├── pages/
│   ├── index.vue                    # phone: recordings list (RecordingsPane); desktop: empty detail pane
│   ├── login.vue                    # Directus email/password login
│   ├── live.vue                     # live transcription
│   └── recording/[id].vue           # detail: player, transcript edit, export
├── composables/
│   ├── useDirectus.ts               # @directus/sdk client + auth
│   ├── useRecordings.ts             # list + search/filter
│   ├── useRecording.ts              # single recording + transcript + status polling
│   ├── useRecorder.ts               # MediaRecorder capture + levels
│   ├── useUpload.ts                 # upload blob + create recording (batch)
│   ├── useProfiles.ts               # postprocessing profiles
│   ├── useLive.ts                   # live WS streaming to /v1/listen
│   ├── useTranscriptEdit.ts         # transcript editing + save (keeps text on failure)
│   ├── useExport.ts                 # TXT/MD export + clipboard copy
│   └── useOfflineQueue.ts           # IndexedDB queue + auto-sync
├── components/
│   ├── hearth/                      # Hearth design-system primitives (HButton, HCard, ...)
│   ├── AppHeader.vue, RecordingsPane.vue
│   ├── RecordingListItem.vue, QueuedListItem.vue, SaveSheet.vue, StatusChip.vue
├── utils/pcm.ts                     # float32 → 16 kHz int16 PCM for live streaming
├── types/directus.ts                # Recording / Transcript / Schema
└── assets/css/                      # tokens.css + main.css (Tailwind v4)
```

## Features

- **Auth / login** — Directus email/password, global auth middleware guards routes.
- **Recordings history** — list with text search and date filtering.
- **Record** — `MediaRecorder` capture with live level metering → save → batch upload.
- **Upload existing file** — same batch path as a recording.
- **Live transcription** — streams mic audio over WebSocket to the hub `/v1/listen`,
  rolling partial/final text; saves `source=live`, `status=done` on stop.
- **Transcript editing** — optimistic edit that persists to Directus.
- **Export** — download as TXT or Markdown, plus Copy to clipboard.
- **Offline queue** — recordings made offline are queued in IndexedDB and auto-uploaded
  when connectivity returns.
- **Installable PWA** — Add to Home Screen on mobile and desktop (manifest + service
  worker via `@vite-pwa/nuxt`).

## Manual E2E checklist

Start the external hub and bring up the data layer:

```bash
# Data layer — from this repo (Directus :7700 + project-worker):
docker compose up
```

Enter the device token under Einstellungen → Verbindung, then verify against the
running PWA (`bun run dev`):

- [ ] **1. Auth** — register/seed a Directus user; log in via the PWA.
- [ ] **2. Record (batch)** — Record → save with transcribe on → row appears `pending` →
      the worker fills it → PWA poll flips it to `done` with a transcript.
- [ ] **3. Upload (batch)** — upload an existing file → same path as step 2.
- [ ] **4. Live** — Live → speak → rolling text → stop → saved as `done` /
      `source=live` with transcript.
- [ ] **5. Edit & export** — edit a transcript → persists. Export TXT/MD + Copy work.
- [ ] **6. Offline** — go offline → record → queued → back online → uploads.
- [ ] **7. Install** — install the PWA (Add to Home Screen) on mobile + desktop.

## Testing notes

- Tests run under Vitest with the `nuxt` environment (`@nuxt/test-utils/config`), which
  uses **happy-dom**.
- Browser APIs are stubbed/polyfilled in the specs that need them: `MediaRecorder` and
  `AudioContext` are faked (levels degrade gracefully when `AudioContext` is absent),
  `navigator.clipboard` is stubbed for the export tests.
- The offline queue uses **`fake-indexeddb`** (`fake-indexeddb/auto`) so IndexedDB works
  under happy-dom.
