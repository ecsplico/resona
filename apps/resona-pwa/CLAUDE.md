# CLAUDE.md — Resona PWA development guide

## Project overview

Resona PWA is the **browser frontend** for the Resona transcription platform: a Nuxt 4
SPA (`ssr: false`) for medical dictation. Users record or upload audio, it gets
transcribed by the backend, and they review/edit/export the result. Built on the Hearth
design system (plain Vue + Tailwind v4 CSS tokens — no component library).

**This app lives in the resona monorepo at `apps/resona-pwa/`, but it is a separate
deployable with its own toolchain** (bun, own compose files, own uv env for `worker/`).
It talks to its backends over HTTP/WebSocket only. Never import from `packages/*` or any
other Python code in the monorepo, and nothing there imports from here — communicate over
HTTP exclusively. Run every command below from `apps/resona-pwa/` (or `just pwa <recipe>`
from the monorepo root). Issues and tickets live on GitHub (`ecsplico/resona`, label
`area:pwa`); see the root `CLAUDE.md` → Agent skills.

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
# Transcription: a Resona hub (resona-api from the monorepo root, e.g.
# docker-compose.mac.yml on :7710, or Coolify https://resona.ecsplico.de).
# This app runs no engines.

# Data layer — from apps/resona-pwa (Directus + worker, no PWA container):
cp .env.stack.example .env  # Directus + worker secrets (NOT the frontend .env)
docker compose -f docker-compose.backend.yml up -d   #   directus :7700 · worker

# Or bring up the full stack (PWA + data layer) in one command:
# bun run setup   # generates secrets, runs docker-compose.yml

# Frontend — from apps/resona-pwa:
bun install
cp .env.example .env        # frontend NUXT_PUBLIC_* vars (NOT the stack .env)
bun run dev                 # → http://localhost:3000
```

The hub token is **per device**: enter it once in the app under Einstellungen →
Verbindung (stored in localStorage `resona.hub.settings`, never baked into the bundle).
All hub calls go through `useHub()` (`app/composables/useHub.ts`), which sends
`Authorization: Bearer <token>`.

**Token gating + per-user sync.** Without a hub token on the device recording, uploading
(recordings and documents) and sharing are refused with a German hint to Einstellungen →
Verbindung (`useHubTokenGate()`; `RecordingsPane` record/upload, `documents/index.vue`
upload, `share-target.vue` which keeps the stashed file until the token is entered;
continue-recording already checks `hub.hasToken()`). In **directus mode** the token is also
stored per user in `directus_users.hub_token` (bootstrap adds the field plus `user`-policy
read/update with filter `id = $CURRENT_USER`, fields limited to `hub_token`; admin
unaffected). `useHubTokenSync()` (`push` on change in the settings drawer, `pull` from
`useDirectus` after login/SSO/session restore) fills an empty local token from
`/users/me`; a device that has a token keeps it and uploads it only if the account has
none (`app/utils/hubTokenSync.ts`). Standalone mode has no account: purely per device.

Two `.env` consumers, both named `.env`: the **compose stack** is seeded from
`.env.stack.example` (`DIRECTUS_*` / `RESONA_API_URL`), the **Nuxt frontend** from
`.env.example` (`NUXT_PUBLIC_*`). Keys are disjoint, so they don't conflict.

The data layer self-bootstraps: `docker compose up` runs the one-shot
`directus-bootstrap` service (`directus/bootstrap.mjs`) once Directus is healthy,
which idempotently provisions collections, the `user` role/policy, and the
`transcribe-worker` user with its static token pinned to `DIRECTUS_TOKEN` (so set
that in `.env` first, e.g. `openssl rand -hex 24`). See `directus/bootstrap.md`.
CORS on Directus and the hub must allow the PWA origin (hub: `CORS_ORIGINS`).

### Worker

The `resona-project-worker` lives in `worker/` (package `resona_project_worker`) — a
standalone Python service (`httpx` + `python-decouple`, own `uv.lock`, not a member of the
monorepo's uv workspace, no imports from `packages/*`). It bridges
**this app's** Directus → the Resona hub: `pending` recordings and documents are
uploaded and submitted as durable hub jobs (`POST /v1/uploads` + `POST /v1/jobs`, job id
stored in `hub_job_id`), polled, and written back (recordings → `transcripts` row from the
result `markdown`; documents → `markdown` + `pages`), then the hub job is deleted. Same
state machine and German error messages as the standalone `localProcessor.ts`. Env:
`RESONA_API_URL`, `RESONA_API_KEY` (required hub client token), `WORKER_ENGINE` (empty =
hub default), `WORKER_POLL_INTERVAL`, `WORKER_BATCH`, `WORKER_STALE_MINUTES`. Build/run
it via `docker compose` (the `project-worker` service).

## Backends

- **Directus** (`:7700`) — auth, data, file storage via `@directus/sdk`. Holds the
  `recordings`, `transcripts` and `documents` collections.
- **Resona hub** (`NUXT_PUBLIC_RESONA_API_URL`) — external, org-scoped transcription hub.
  The PWA calls it only via `useHub()`: `/profiles`, `/v1/engines`, sync
  `/v1/audio/transcriptions` (push-to-talk) and the Deepgram-compatible live
  WebSocket `/v1/listen`.

### Two transcription paths

- **Batch** (record-then-save, or upload a file): PWA uploads audio to Directus + inserts
  a `recordings` row `status=pending, source=batch`. This repo's **project-worker**
  (in its compose stack) submits it as a durable hub job (`transcribing` + `hub_job_id`),
  writes the `transcripts` row when the job completes, flips the recording to `done`. The PWA polls until
  terminal (`done`/`error`). Without the worker running, batch rows stay `pending`
  forever.
- **Live**: PWA opens a WebSocket to the hub `/v1/listen`, streams 16 kHz PCM16 from
  the mic, renders rolling partial/final text. On stop the PWA itself writes the
  `recordings` (`source=live, status=done`) and `transcripts` rows.
  Live source (Einstellungen → Live, `useLiveSettings().source`): `hub` streams PCM from
  `usePcmCapture()` to `/v1/listen` using an engine from `/v1/listen/engines` discovery
  (`private=false` only for cloud entries; empty list → start disabled); `device` uses
  on-device Web Speech (`useDeviceSpeech`, `processLocally` always true). Transcript
  `engine` stamp: `web-speech` / selected hub engine / listen default / `hub-live`.
  Settings persist in localStorage `resona.live.settings` (`chunkSeconds` clamped 8–30);
  the drawer shows the device language-pack state (`availability`/`install`), `/debug` too.
  Live correction (`settings.correction`, `useLiveCorrection`): PCM is cut into blocks of
  `chunkSeconds` at final boundaries, each re-transcribed sequentially via hub
  `transcribeSync` (`correctionEngine`, empty → hub default); the page renders blocks
  (`live__block--corrected`/`--pending`), save awaits `finish()` (≤90 s, prompt shows
  `Korrektur läuft… (n Abschnitte)`) and stores `correction.text` stamped
  `<live stamp>+<correction engine | /v1/engines default | hub>` if any block was corrected.
- **Expert mode** (Einstellungen → Verbindung, `useHubSettings().expert`, per device): the project-worker / `localProcessor` store the hub `JobResult.provenance` (`worker_id`, per-stage engine/model/accelerator/seconds) in the `provenance` json field of `transcripts` and `documents` (bootstrap adds it to existing installs); the recording and document pages render it via `describeProvenance()` (`app/utils/provenance.ts`) only when expert mode is on. Live-dictation transcripts have no provenance.
- **Nachbearbeiten** (re-process a transcript with a hub postprocess profile, hub job kind
  `text`): `PostprocessPanel.vue` on the recording page lists `GET /profiles`
  (`useProfiles`) and writes `postprocessRequest(profile)` (`app/utils/postprocess.ts`) onto
  the transcript row; it is enabled only when the transcript is saved and no dictation /
  continue-recording is running, and the editor is read-only (`TranscriptEditor`
  `editable`) while the run is `pending|processing`. The profile always runs on the raw
  transcript (`raw_text ?? text`), never on an earlier profile's output; on success `text`
  becomes the job's `markdown`, `raw_text` keeps the raw transcript and `applied_profile`
  the profile, so the panel can show the raw text, re-run another profile or restore the
  raw text (`rawRestore`). Who runs it: Directus mode → the project-worker (`_run_postprocess`
  in `worker.py`: claim → `POST /v1/uploads` with the text as `transcript.md` →
  `POST /v1/jobs {kind:"text", profile}` → poll → write back → delete the hub job);
  standalone → `localProcessor.runLocalCycle` from the browser with the user's hub token.
  Same German messages in both: `profile_required`, `profile_not_found`, `invalid_profile`,
  `policy_denied` (with the hub reason) become `postprocess_error`; a pending job's
  `queue_reason` is stored in `postprocess_queue_reason` and shown
  (`no_capable_worker` → "Kein passender Worker online …"). `useRecording` keeps polling
  while a run is queued. Editing a profile's output does not record a transcript correction
  (the engine did not write that text).
- **Uploads and shares** (`useUpload.saveRecording`): the file's type is normalised
  (`utils/audioFile.ts`; phones send `""`, `application/octet-stream` or `audio/x-m4a`, and the
  file used to be stored as `<title>.webm` with that type, so Directus served an unplayable
  Content-Type) and the stored filename gets the matching extension. A `duration_seconds` of
  0 is replaced by the real length (`probeDuration`: `<audio>` metadata, fallback
  `decodeAudioData`; recorder callers pass their own elapsed time).
- **Continue recording** (`useContinueRecording`, own `useRecorder`): a new part of an
  existing recording is transcribed on its own as a hub job (upload → `createJob` with a
  per-attempt `continue:recordings:<id>:<uuid>` key → `getJob` every 2 s, no timeout →
  `getResult` → `deleteJob`), reusing `localProcessor.ts`'s exported job-status helpers;
  the caller appends the returned text. Then the stored audio + the part are merged
  (`utils/audioMerge.ts` `mergeToWav`: OfflineAudioContext decode, mono, 16 kHz, PCM16
  WAV, ~2 MB/min) and saved via `DataStore.replaceAudio` (Directus keeps the old file:
  no `directus_files` delete permission; the local store deletes the old blob). A failed
  transcription keeps the part for `retry`; a failed merge still returns the text
  (`audioMerged:false`, `mergeError`). Works in both data modes, needs the page open
  (`beforeunload` guard while recording/transcribing/merging; the detail page adds a
  route-leave/update `confirm` while a part is in flight or kept for retry). The page
  appends via `TranscriptEditor.appendParagraph` (caret and undo stay) and saves; a
  failed save keeps the text in the editor (`useTranscriptEdit` never rolls back).

### Live WebSocket contract (hub `/v1/listen`)

`ws://<host>/v1/listen?encoding=linear16&sample_rate=16000&interim_results=true&language=&engine=&token=<device token>`
- Browser WebSockets can't set headers → auth goes in `?token=` (the device token from
  `useHub().token()`). `engine` is only sent when one is chosen.
- Up: binary PCM16 LE, 16 kHz mono. Down: Deepgram JSON
  `{type:"Results",channel:{alternatives:[{transcript}]},is_final}` + `{type:"Metadata"}`.
- Finish: send `{"type":"CloseStream"}`, then close.

### Standalone mode (`NUXT_PUBLIC_DATA_MODE=standalone`)

No CMS: recordings, transcripts, documents and files live in IndexedDB
(`app/data/localStore.ts`; one idb-keyval database per collection,
`resona-local-recordings|transcripts|documents|files`, store `items`, files as
`{bytes, type, name}`), there is no login (`auth.global.ts` sends `/login` to `/`),
and the health indicator skips the Directus ping. `app/plugins/local-processor.client.ts`
runs `runLocalCycle()` (`app/data/localProcessor.ts`) every 5 s and on `online`: it
submits pending rows to the hub and polls their jobs, the same state machine as
`worker/`. Processing only happens while the app is open, and it needs a hub device
token (no token → nothing is processed).

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
├── app.vue                          # VitePwaManifest + layout; no phone frame
├── layouts/default.vue              # shell: AppHeader + page + SettingsDrawer; provides useShell; ≥960px on / and /recording/:id: RecordingsPane in the left aside, page in the right pane
├── middleware/auth.global.ts        # redirect unauthenticated → /login
├── pages/
│   ├── index.vue                    # phone: RecordingsPane; desktop pane: empty right-pane state
│   ├── login.vue                    # Directus email/password login
│   ├── live.vue                     # live transcription (reads ?title= to preset name)
│   ├── calendar.vue                 # Google Calendar: connect, filter, start dictation
│   ├── documents/index.vue          # Dokumente: upload PDF/images, search, status (both modes)
│   ├── documents/[id].vue           # document detail: markdown, retry, copy/.md export, delete
│   └── recording/[id].vue           # detail: player, transcript edit, export
├── composables/                     # useDirectus, useRecordings, useRecording,
│   │                                #   useRecorder, useUpload, useProfiles, useLive,
│   │                                #   useCalendar, useTranscriptEdit, useExport,
│   │                                #   useOfflineQueue
│   │                                # useShell: shell context (wide ≥ 960 px, paneInLayout) +
│   │                                #   useSettingsDrawer (drawer state owned by the layout)
│   │                                # useHub: the only hub HTTP client (token, engines, jobs)
│   │                                # useHubSettings: per-device hub token (localStorage)
│   │                                # useDataStore: picks directusStore | localStore by data mode
│   │                                # useLiveSettings: live source/engine/correction settings
│   │                                # usePcmCapture: mic → 16 kHz PCM16 frames for /v1/listen
│   │                                # useDeviceSpeech: on-device Web Speech live source
│   │                                # useLiveCorrection: re-transcribes live blocks via the hub
│   │                                # useContinueRecording: record more onto a recording
├── data/
│   ├── types.ts                     # DataStore interface shared by both backends
│   ├── directusStore.ts             # DataStore over Directus (directus mode)
│   ├── localStore.ts                # DataStore over IndexedDB (standalone mode)
│   └── localProcessor.ts            # runLocalCycle(): standalone hub job state machine
├── plugins/local-processor.client.ts # standalone: runs runLocalCycle() every 5 s + on online
├── components/
│   ├── hearth/                      # HButton, HCard, HChip, HField, ... (design system)
│   ├── AppHeader                    # wordmark, ServerStatus, section tabs, settings cog
│   ├── RecordingsPane               # recordings list: filters, queue, list, capture dock, record flow
│   └── RecordingListItem, QueuedListItem, SaveSheet, StatusChip
├── utils/pcm.ts                     # float32 → 16 kHz int16 PCM for live streaming
├── utils/audioMerge.ts              # mergeToWav(): decode + concat parts → 16 kHz mono WAV
├── utils/audioFile.ts               # uploads/shares: resolveAudioMime/normalizeAudio (playable type + extension), probeDuration (element metadata → decodeAudioData)
├── utils/hubTokenSync.ts            # pure per-user hub token sync (pull/push over a remote interface)
├── types/directus.ts                # Recording / Transcript / Schema
└── assets/css/                      # tokens.css + main.css

shared/calendar.ts                   # calendar wire types + helpers (app + server)
server/                              # the sanctioned Nitro OAuth layer (server only)
├── api/calendar/index.{get,delete}.ts  # single provider-agnostic endpoint
└── utils/calendar/                  # provider interface + registry + Google + session
```

Component auto-import is `pathPrefix: false` — components resolve by filename
(`<HButton>`, `<QueuedListItem>`), regardless of subdirectory.

### Calendar integration (Google + pluggable)

Off unless `NUXT_PUBLIC_CALENDAR_ENABLED` is `true` (`isCalendarEnabled` in
`shared/calendarEnabled.ts`): no Kalender link or settings tab, `/calendar`
aborts with a fatal 404 and `/api/calendar` returns 404
(`server/utils/calendar/enabled.ts`). The code stays.

`server/utils/calendar/` mirrors the Python repo's provider pattern: a
`CalendarProvider` interface (`types.ts`) + a `registry.ts` + one module per
backend (`google.ts`). Add a provider = one module + a registry entry; the
routes and client need no change. Tokens (incl. the refresh token) live in an
h3 **sealed session cookie** — no DB. The browser flow: `/calendar` connects via
`<a href="/api/calendar?provider=google&action=connect">`, lists events for a
date/text filter, and "Transkribieren" opens `/live?title=<event – date>` so the
recording is pre-named from the event.

**One Nitro route path on purpose** (`/api/calendar`, GET+DELETE; provider via
`?provider=`, OAuth provider carried in `state`): Nitro's typed `$fetch`
recurses over the route-path union, and extra paths push unrelated bare-string
`$fetch` calls (e.g. `useProfiles`) into a TS "excessive stack depth" error.
Keep client calls to the **literal** `/api/calendar` string (exact match is
cheap). The Google OAuth redirect URI is `<origin>/api/calendar`.

## Directus schema (source of truth: `directus/bootstrap.md`)

- **recordings**: `id, user_created, date_created, date_updated, title, audio_file,
  duration_seconds, language, profile, status (pending|transcribing|done|error),
  error_message, source (batch|live), review_status (editing|ready)` — **NO `engine`
  field**. `review_status` is the user's review workflow, independent of `status`.
- **transcripts**: `id, recording, text, structured, segments, engine, provenance, date_created,
  date_updated` — the `engine` field lives **HERE**, not on recordings. Re-processing
  ("Nachbearbeiten") adds `raw_text` (the raw transcript a profile ran on; null while `text`
  is raw), `applied_profile`, `postprocess_status (pending|processing|done|error)`,
  `postprocess_profile`, `postprocess_error`, `postprocess_queue_reason`,
  `postprocess_job_id`, `postprocess_job_key` (bootstrap adds them to existing installs).
- **recordings** also has `hub_job_id` (integer, the in-flight hub job) and `hub_job_key`
  (string, the hub Idempotency-Key of the current submission attempt; persisted before
  `POST /v1/jobs`, kept by stale recovery, cleared on `done`/`error`).
- **documents**: `id, user_created, date_created, date_updated, title, file (→
  directus_files), status (pending|processing|done|error), error_message, markdown,
  pages (json), hub_job_id, hub_job_key`.

The live path stamps the transcripts row's `engine` (`web-speech` / hub engine /
`hub-live`, `+<correction engine>` when corrected); the manual-save path writes it
without `engine`. The recordings payload must NOT include `engine`.

The Directus worker user is still named `transcribe-worker` (`transcribe-worker@resona.app`)
for existing installs, although it now processes documents too.

## Configuration

All public config flows through `runtimeConfig.public` (set via `NUXT_PUBLIC_*` env vars,
defaults in `nuxt.config.ts`). Read it with `useRuntimeConfig().public` — never hardcode
URLs. No token or engine is configured at build time — the hub token is per device
(Einstellungen → Verbindung). The 6 keys:

| Var | Default | Meaning |
|-----|---------|---------|
| `NUXT_PUBLIC_DIRECTUS_URL` | `http://localhost:7700` | Directus base URL |
| `NUXT_PUBLIC_RESONA_API_URL` | `http://localhost:7710` | Resona hub base URL |
| `NUXT_PUBLIC_DEFAULT_LANGUAGE` | `de` | default transcription language |
| `NUXT_PUBLIC_DEFAULT_PROFILE` | `default` | default postprocessing profile |
| `NUXT_PUBLIC_DATA_MODE` | `directus` | data backend: `directus` \| `standalone` |
| `NUXT_PUBLIC_CALENDAR_ENABLED` | `false` | enable the calendar integration (nav link, settings tab, `/calendar`, `/api/calendar`) |

The Google Calendar OAuth layer adds **server-only** (non-`public`) keys, read on
the server via `useRuntimeConfig(event)` — these never reach the browser:

| Var | Default | Meaning |
|-----|---------|---------|
| `NUXT_GOOGLE_CLIENT_ID` | (empty) | Google OAuth client id |
| `NUXT_GOOGLE_CLIENT_SECRET` | (empty) | Google OAuth client secret |
| `NUXT_GOOGLE_REDIRECT_URI` | (derived) | override; default `<origin>/api/calendar` |
| `NUXT_SESSION_PASSWORD` | (empty) | 32+ char secret sealing the token cookie |

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

- Don't import from / reach into the monorepo's Python code (`packages/*`, other `apps/*`) — HTTP only.
- Don't put server logic in the PWA beyond the one sanctioned thin Nitro OAuth layer
  (`server/api/**`, confidential Google token exchange/refresh only) — it's otherwise a
  pure SPA: no page rendering (`ssr: false`), no business logic server-side.
- Don't deploy via `nuxt generate` (static) now that Nitro routes exist — use the Nitro
  Node/serverless server (`nuxt build` → `node .output/server`).
- Don't hardcode backend URLs — use `useRuntimeConfig().public`.
- Don't set `engine` on a `recordings` row — it belongs on `transcripts`.
- Don't set live-WS auth via headers — browsers can't; use `?token=`.
- Don't commit `.env`.
