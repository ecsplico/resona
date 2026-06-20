# Resona PWA

A Nuxt 4 progressive web app for medical dictation and transcription, built on the
Hearth design system. It is a multi-user, installable, offline-capable front end that
records or uploads audio, runs it through Resona's transcription backend, and lets you
review, edit, and export the resulting transcripts. The app is browser-only
(`ssr: false`) and talks to its backends over HTTP/WebSocket — it holds no server
logic of its own.

## Quick start

The PWA is the **frontend** only. It needs the Resona **backend** stack (Directus +
resona-api + the directus-transcribe worker + a transcription engine) running first.
Those live in the **resona monorepo** and come up with one compose command.

### 1. Backend (resona monorepo)

From the monorepo root (the directory containing `docker-compose.resona.yml`):

```bash
docker compose -f docker-compose.resona.yml --profile faster-whisper up -d
```

This brings up:

| Service | Host port | Role |
|---------|-----------|------|
| `directus` | `:7700` | auth, data, file storage |
| `api` (resona-api) | `:7710` | transcription API + live `/v1/listen` WebSocket |
| `directus-transcribe` | — | worker that polls Directus and fills batch transcripts |
| `engine-faster-whisper` | `:7720` | the ASR engine resona-api calls |

`directus`, `api`, and `directus-transcribe` are always-on; the **engine** is selected by
a `--profile` flag (`faster-whisper`, `whisper`, `voxtral`, `parakeet`). First run also
needs Directus bootstrapped (collections + user role) — see the monorepo
`directus/bootstrap.md`. CORS on both Directus and resona-api must allow the PWA origin
(e.g. `http://localhost:3000`).

### 2. Frontend (this repo)

```bash
bun install
cp .env.example .env        # defaults already point at :7700 / :7710
bun run dev                 # → http://localhost:3000
```

Open `http://localhost:3000`, log in with a Directus user, and record/upload/dictate.

## Architecture

Resona PWA is a **standalone repository** (it is not part of the Python monorepo) and
communicates with two backend services over HTTP only:

- **Directus** (`:7700`) — auth, data, and file storage via `@directus/sdk`. Holds the
  `recordings` and `transcripts` collections and the user role. The PWA authenticates
  the end user, uploads audio files, creates `recordings` rows, and reads back
  transcripts.
- **resona-api** (`:7710`) — the transcription API. The PWA uses its live streaming
  WebSocket endpoint (`/v1/listen`, Deepgram-compatible) for real-time dictation.

There are **two transcription paths**:

- **Batch** (record-then-save, or upload an existing file): the PWA uploads the audio
  to Directus and inserts a `recordings` row with `status=pending`, `source=batch`. The
  **directus-transcribe** worker (running in the monorepo) polls Directus, claims the
  row, transcribes it via resona-api, writes the `transcripts` row, and flips the
  recording to `done`. The PWA polls the recording until it reaches a terminal state
  (`done`/`error`).
- **Live**: the PWA opens a WebSocket to resona-api `/v1/listen`, streams 16 kHz PCM
  from the microphone, and renders rolling partial/final text. On stop it writes the
  `recordings` (`source=live`, `status=done`) and `transcripts` rows itself.

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

The PWA requires a running Resona backend stack:

1. **Directus** with the `recordings` and `transcripts` collections and the end-user
   role provisioned. See the monorepo `directus/bootstrap.md` and the platform design
   spec `docs/superpowers/specs/2026-06-17-pwa-directus-platform-design.md`.
2. **resona-api** — for live transcription (and as the engine the worker calls).
3. **directus-transcribe** worker — required for the **batch** path; without it,
   uploaded/recorded files stay `pending` forever.

In development, **CORS on both Directus and resona-api must allow the PWA origin**
(e.g. `http://localhost:3000`).

## Setup

```bash
bun install
cp .env.example .env
# edit .env and set the NUXT_PUBLIC_* vars for your backends
```

## Environment variables

All public runtime config is provided via `NUXT_PUBLIC_*` env vars (see `.env.example`).
Defaults are defined in `nuxt.config.ts` under `runtimeConfig.public`.

| Variable | Default | Meaning |
|----------|---------|---------|
| `NUXT_PUBLIC_DIRECTUS_URL` | `http://localhost:7700` | Base URL of the Directus instance (auth, data, file storage). |
| `NUXT_PUBLIC_RESONA_API_URL` | `http://localhost:7710` | Base URL of resona-api (live transcription WebSocket `/v1/listen`). |
| `NUXT_PUBLIC_RESONA_API_KEY` | `` (empty) | Optional resona-api key, sent for live streaming when set. |
| `NUXT_PUBLIC_DEFAULT_LANGUAGE` | `de` | Default transcription language. |
| `NUXT_PUBLIC_DEFAULT_PROFILE` | `default` | Default postprocessing profile name. |
| `NUXT_PUBLIC_DEFAULT_ENGINE` | `faster-whisper` | Default engine for live transcription. |

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
├── layouts/default.vue
├── middleware/auth.global.ts        # redirects unauthenticated users to /login
├── pages/
│   ├── index.vue                    # recordings history + search/date filter, record FAB
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
│   ├── useTranscriptEdit.ts         # optimistic transcript editing
│   ├── useExport.ts                 # TXT/MD export + clipboard copy
│   └── useOfflineQueue.ts           # IndexedDB queue + auto-sync
├── components/
│   ├── hearth/                      # Hearth design-system primitives (HButton, HCard, ...)
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
- **Live transcription** — streams mic audio over WebSocket to resona-api `/v1/listen`,
  rolling partial/final text; saves `source=live`, `status=done` on stop.
- **Transcript editing** — optimistic edit that persists to Directus.
- **Export** — download as TXT or Markdown, plus Copy to clipboard.
- **Offline queue** — recordings made offline are queued in IndexedDB and auto-uploaded
  when connectivity returns.
- **Installable PWA** — Add to Home Screen on mobile and desktop (manifest + service
  worker via `@vite-pwa/nuxt`).

## Manual E2E checklist

Bring up the backend stack from the monorepo:

```bash
docker compose -f docker-compose.resona.yml --profile faster-whisper up
# plus a running Directus (:7700) and the directus-transcribe worker
```

Then verify against the running PWA (`bun run dev`):

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
