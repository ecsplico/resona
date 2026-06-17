# Resona PWA + Directus Platform — Design

**Date:** 2026-06-17

**Goal:** Build a multi-user dictation/transcription product on top of the
existing Resona stack: a **Nuxt 4 PWA** (record, upload, live, history, edit,
export) for mobile + desktop, with **Directus** as the full backend (users,
auth, recordings metadata, transcript storage, audio files), and a new Python
**glue worker** (`directus-transcribe`) that detects untranscribed recordings,
calls `resona-api`, and writes results back to Directus. The PWA adopts the
warm-editorial "Hearth" design system the user authored in Claude.

---

## 1. Scope and decomposition

In scope for v1 (all confirmed by the user):

- Record audio in the PWA (mic capture) and upload to Directus
- Upload existing audio files for transcription
- Live / streaming transcription (real-time text via `resona-api` `WS /v1/listen`)
- Transcript history — browse, search past recordings
- Edit transcripts in-app
- Profiles / postprocessing — pick a resona profile per recording
- Export transcript (TXT / Markdown)

Explicitly **out of scope** for v1 (noted where a v1 decision sets them up):

- Team/org sharing of recordings between users (v1 is per-user private data)
- Offline-first conflict resolution beyond a simple queue-and-sync
- TTS / playback of synthesized speech (resona-cloud-tts exists but unused here)
- Push notifications when a transcript completes (PWA polls instead)

## 2. Repo layout decision

**Approach B (chosen):** split toolchains.

- **`packages/directus-transcribe/`** — the Python glue worker lives **in this
  repo** as a new workspace package. It reuses `httpx`, `python-decouple`, and
  the existing `resona-client` patterns, deploys with the same `uv sync` and
  Docker Compose, and shares the engine/api lifecycle.
- **`resona-pwa` (separate repo)** — the Nuxt 4 app lives in its **own repo**,
  deployed independently (Vercel / Netlify / Docker). It has nothing to import
  from the Python workspace; it talks to Directus and `resona-api` purely over
  HTTP. Node tooling (`package.json`, `node_modules`, Nuxt config) stays out of
  the uv workspace.

Directus itself is **not vendored** — it runs as an official Docker image
(`directus/directus`) wired into `docker-compose.resona.yml`, with its schema
captured as a versioned snapshot file checked into this repo (see §6).

## 3. Port allocation (7700–7800 range)

| Port | Service | Source |
|------|---------|--------|
| `:7700` | Directus | official image |
| `:7710` | resona-api | this repo (remapped from `:7000`) |
| `:7720` | engine: faster-whisper | this repo (remapped from `:7001`) |
| `:7721` | engine: parakeet | this repo |
| `:7722` | engine: whisper | this repo |
| `:7723` | engine: voxtral | this repo |
| — | directus-transcribe (worker, no port) | this repo |

The remap is **compose-level only** (host port → container `:7001`/`:7000`);
no application code changes. Existing dev defaults (`:7000`/`:7001`) remain for
non-compose `uv run`.

## 4. Architecture

```
 Nuxt 4 PWA  ──auth + CRUD + file upload──▶  Directus  (:7700)
   (separate repo)                              │  recordings / transcripts / files / users
       │                                        │
       │──live: WS /v1/listen───────────▶  resona-api (:7710)  ◀──poll pending──┐
                                               │  POST /v1/audio/transcriptions   │
                                               ▼                                  │
                                         engine(s) (:7720+)            directus-transcribe (worker)
                                                                          │ download audio ◀── Directus
                                                                          │ write transcript ──▶ Directus
```

Two distinct transcription paths:

1. **Batch (record / upload):** PWA → upload audio to Directus → create a
   `recordings` row with `status=pending`. The `directus-transcribe` worker
   polls, claims, downloads, calls `resona-api`, writes a `transcripts` row,
   sets `status=done`. The PWA polls the recording until done.
2. **Live (streaming):** PWA opens a WebSocket **directly** to `resona-api`
   `WS /v1/listen` (Deepgram-compatible) for real-time partials. On stop, the
   final transcript + the recorded audio are saved to Directus as a normal
   recording with `status=done` (no re-transcription needed).

## 5. Directus data model

Built-in `directus_users` (auth) and `directus_files` (audio storage) are used
as-is. Two custom collections:

### `recordings`
| Field | Type | Notes |
|-------|------|-------|
| `id` | uuid PK | |
| `user_created` | M2O → directus_users | set by Directus |
| `date_created` | timestamp | |
| `title` | string | user-supplied at save |
| `audio_file` | M2O → directus_files | the raw audio |
| `duration_seconds` | float | |
| `language` | string | default `de` |
| `profile` | string | resona profile name, default `default` |
| `status` | string (enum) | `pending` \| `transcribing` \| `done` \| `error` |
| `error_message` | string, nullable | set on failure |
| `source` | string (enum) | `batch` \| `live` — how it was created |

### `transcripts`
| Field | Type | Notes |
|-------|------|-------|
| `id` | uuid PK | |
| `recording` | O2O → recordings | one transcript per recording |
| `text` | text | full transcript (editable) |
| `structured` | json, nullable | from resona `extract` profile steps |
| `segments` | json, nullable | word/segment timestamps |
| `engine` | string | which engine produced it |
| `date_created` / `date_updated` | timestamp | |

**Status state machine:** `pending → transcribing → done`; any failure →
`error` with `error_message`. A live recording is written straight to `done`.

**Access policy:** a non-admin role "user" with row-level filter
`user_created = $CURRENT_USER` on both collections (read/create/update/delete
own rows only). `transcripts` access is gated through the parent recording's
ownership.

## 6. Directus schema management

The schema is created once in the Directus admin UI (or via the API), then
exported to **`directus/schema-snapshot.yaml`** (checked into this repo) using
`directus schema snapshot`. Deployments apply it with `directus schema apply`.
A small `directus/bootstrap.md` documents: create the "user" role + policy, the
two collections, and a static **service token** for the worker. This keeps the
backend reproducible without vendoring Directus source.

## 7. directus-transcribe worker

New workspace package `packages/directus-transcribe/`, src-layout
`src/resona_directus_transcribe/`:

- **`client.py`** — async `httpx` client for Directus: `list_pending()`,
  `claim(id)`, `download_audio(file_id) -> path`, `write_transcript(...)`,
  `mark_done(id)`, `mark_error(id, msg)`. Auth via static service token.
- **`worker.py`** — asyncio poll loop:
  1. `GET /items/recordings?filter[status][_eq]=pending&limit=N`
  2. **Claim atomically:** `PATCH status=transcribing` (guards against
     double-processing if multiple workers run)
  3. Download audio from `/assets/{file_id}` to a temp file
  4. `POST /v1/audio/transcriptions` (multipart: audio + language + profile) on
     `resona-api`
  5. `POST /items/transcripts` then `PATCH recordings/{id} status=done`
  6. On any exception → `PATCH status=error, error_message=…`; temp file always
     cleaned up
- **`run.py`** — entry point; `[project.scripts]` → `resona-directus-transcribe`.

Bounded concurrency (default 2 simultaneous jobs). Config via `config()`
(python-decouple), matching repo convention:

| Var | Default | Purpose |
|-----|---------|---------|
| `DIRECTUS_URL` | `http://localhost:7700` | |
| `DIRECTUS_TOKEN` | (required) | service token |
| `RESONA_API_URL` | `http://localhost:7710` | |
| `RESONA_API_KEY` | (unset) | if api auth enabled |
| `TRANSCRIBE_POLL_INTERVAL` | `5` | seconds |
| `TRANSCRIBE_CONCURRENCY` | `2` | parallel jobs |

The worker touches **no engine and no database directly** — it only speaks the
Directus REST API and the `resona-api` HTTP API. It does not delete audio.

## 8. Nuxt 4 PWA

Separate repo. Stack: **Nuxt 4** (Vue 3, Vite), **@directus/sdk** (typed REST +
auth), **@vite-pwa/nuxt** (service worker, manifest, offline), **@vueuse/core**
(`useMediaRecorder`, `useWebSocket`), **Nuxt UI** for primitives (themed to
Hearth tokens, see §9).

### Navigation model

Single main screen (recordings list) — **no bottom tab bar**. A recording opens
inline to its detail (transcript + player). A terracotta FAB starts recording;
Live mode is a secondary action. Login is a gate before the app shell.

### Pages
- **`/login`** — Directus email+password; JWT in cookie; redirect to `/`.
- **`/` (recordings)** — own recordings list with status chips + per-item Copy
  action; search + date filter; FAB → record; secondary → live.
- **`/recording/[id]`** — audio player dock + editable transcript (main area) +
  Copy + Export (TXT/MD) + delete.
- **`/live`** — real-time mic → `WS /v1/listen`; rolling transcript; on stop,
  prompt for title, save audio + final transcript to Directus as `done`.

### Composables
- **`useDirectus()`** — wraps `@directus/sdk`: auth, recordings/transcripts CRUD.
- **`useRecorder()`** — MediaRecorder capture, pause/resume, waveform levels,
  upload blob to Directus files + create recording row.
- **`useLive()`** — WebSocket to `/v1/listen`, parse Deepgram `Results`, expose
  rolling partial/final text.
- **`useRecording(id)`** — fetch recording + transcript; poll `status` until
  `done`/`error` for batch jobs.

### Recording UX (per user's flow)
1. Tap FAB → full-screen **ink-dark recording mode**: live waveform, mono timer,
   **pause/resume**, **stop**, cancel.
2. On **stop** → bottom sheet: **title** input (required) + **Transcribe**
   toggle (on by default, shows language + profile) + Save / Discard.
3. **Save** uploads audio + creates `recordings` row (`status=pending` if
   transcribe on, else `done` with no transcript). Returns to list.
4. Tap a recording → detail: **player docked at bottom**, **editable transcript**
   in the main section, **Copy** button in the header.

### PWA / offline
Installable (standalone display). Recordings made offline are queued in
IndexedDB and uploaded when connectivity returns; the list shows a local
"queued" state until the Directus row exists. Live mode requires connectivity.

## 9. Design system — "Hearth"

Extracted verbatim from the user's `Resona.html` artifact. Delivered to the PWA
repo as a **design-tokens file** + a documented component mapping.

**Color tokens:**
`cream #FBF5EC`, `creamDeep #F4EBD9`, `white #FFFFFF`, `ink #2A1E16`,
`ink2 #4A3A2E`, `ink3 #75655A`, `tomato #D96846`, `tomatoDeep #B85032`,
`tomatoSoft #F3D1BE`, `olive #6B8E4E`, `oliveDeep #4E6B38`, `oliveSoft #C8D8B4`,
`butter #E8A838`, `butterSoft #F5D88F`, `crust #B8916A`, `crustDeep #8B6944`,
`crustSoft #E2D2BE`, `border rgba(139,105,68,0.18)`,
`borderStrong rgba(139,105,68,0.40)`.

**Type:** Fraunces (display/headings, section labels like "Procedere:"),
Instrument Sans (body/UI), JetBrains Mono (timers, durations, timestamps).

**Shape/shadow:** cards 16–20px radius, buttons 10–14px, chips/pills 999;
`shadowSoft 0 1px 2px rgba(88,52,28,.06), 0 2px 8px rgba(88,52,28,.04)`,
`shadowLift 0 4px 12px rgba(88,52,28,.10), 0 8px 24px rgba(88,52,28,.06)`.

**Status → tone mapping:** `done` → olive chip, `transcribing` → butter chip,
`pending` → neutral cream chip, `error` → tomato chip.

**Signature elements:** terracotta circular FAB / record button; full-screen
**ink-dark recording mode** (mirrors the kit's "cooking mode") with terracotta
waveform peaks and mono timer; glass-pill icon buttons on media headers;
audio-player dock in `creamDeep` with a terracotta scrubber.

The kit's React component set (`Button`, `Chip`, `Avatar`, `AppBar`,
`IconButton`, `Field`, list rows, iOS device frame) is **ported to Vue
components** in the PWA — same prop shapes, same tokens. The kit's recipe-app
demo screens are reference only and are not ported.

## 10. Error handling

- **Worker:** any step failure → recording `status=error` + `error_message`;
  temp audio cleaned in a `finally`. Transient `resona-api`/Directus HTTP errors
  are retried with bounded backoff before marking `error`. A recording stuck in
  `transcribing` past a TTL (e.g. 15 min) is reclaimed to `pending` on the next
  poll (stale-claim recovery).
- **PWA:** upload failures keep the recording queued locally with a retry
  affordance; live WebSocket drops show a reconnect banner; transcript edit
  saves are optimistic with rollback on Directus error.

## 11. Testing

- **directus-transcribe** (`packages/directus-transcribe/tests/`): mock both
  Directus and `resona-api` with `respx`; assert the claim→download→call→write
  →done sequence, the error path, stale-claim recovery, and concurrency bound.
  Audio fixtures: reuse a small 16 kHz mono WAV.
- **PWA:** component tests (Vitest + Vue Test Utils) for composables
  (`useRecorder`, `useLive` parsing Deepgram frames) and key screens; the
  Directus SDK and WebSocket are mocked.
- **Schema:** a smoke test that `directus schema apply` against the snapshot
  yields the two collections + policy (run in the PWA/infra CI, not pytest).

## 12. Build order (informs the implementation plan)

1. Directus compose service + schema snapshot + bootstrap doc + port remap
2. `directus-transcribe` worker (batch path end-to-end, with tests)
3. PWA scaffold (Nuxt 4 + PWA + Directus auth + Hearth tokens)
4. PWA recordings list + record/upload + detail (batch path)
5. PWA live transcription (`/v1/listen`)
6. PWA edit + export + offline queue
7. End-to-end verification across the full stack
