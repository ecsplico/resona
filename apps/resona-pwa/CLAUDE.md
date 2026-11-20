# CLAUDE.md — Resona PWA development guide

## Project overview

Resona PWA is the **reference app** of the Resona hub: one maintained consumer of the
hub's published HTTP/WebSocket contract (jobs, live streaming, profiles), not part of the
hub. It is a Nuxt 4 SPA (`ssr: false`) for medical dictation, plus its Directus data layer,
project worker and dictation MCP. Users record or upload audio, the app sends it through
the hub as jobs, and users review/edit/export the result. Built on the Hearth design
system (plain Vue + Tailwind v4 CSS tokens — no component library).

**Two layers** ([ADR 0001](../../docs/adr/0001-hub-and-app-boundary.md)): the hub is a
transformation layer that knows organizations and credentials, not users, and keeps a
job's result for 24 hours only. This app owns everything a user keeps: users, dictations,
recordings, transcripts, documents, long-term storage and the UI. It gets no hub access
another app could not have: never read hub storage, the hub database or hub source, and
never ask the hub to store app entities. An app-only feature belongs here; a feature that
needs new hub behaviour is a contract change in the hub first.

**This app lives in the resona monorepo at `apps/resona-pwa/` until it moves to its own
repository, and it is a separate deployable with its own toolchain** (bun, own compose
files, own uv envs for `worker/` and `mcp/`).
It talks to the hub and its own Directus over HTTP/WebSocket only. Never import from
`packages/*` or any other Python code in the monorepo, and nothing there imports from
here — communicate over HTTP exclusively. Run every command below from `apps/resona-pwa/`
(or `just pwa <recipe>` from the monorepo root). Issues and tickets live on GitHub
(`ecsplico/resona`, label `area:pwa`); see the root `CLAUDE.md` → Agent skills.

The frontend is otherwise a pure client-rendered SPA. The **one** sanctioned server-side
exception is a thin Nitro layer (`server/api/**`) used **only** to hold confidential OAuth
secrets that cannot live in the browser — specifically the Google Calendar token
exchange/refresh (the OAuth client secret + the per-user refresh token). Nothing else goes
on the server: no business logic, no page rendering (`ssr: false` stays). Because these
routes execute server-side, production **must** be deployed via the Nitro Node/serverless
server (`nuxt build` → `node .output/server`), **not** `nuxt generate` static output (which
strips the server). Keep secrets in `runtimeConfig` (the private, non-`public` block).

**Temporary exception (diagnostics):** `server/api/debug-report.post.ts` accepts the
redacted mobile diagnostics report from `/debug` → "Mobil-Diagnose" → "An Server senden".
It is public and unauthenticated, so it allows 10 reports per client IP per 10 minutes
(in-memory fixed window, 429 beyond) and caps the body at 32 KB while reading it (413 even
when Content-Length is missing or wrong; 400 unless a JSON object). It re-redacts the report
(`shared/debugReport.ts`: no tokens, no transcript text), writes one stdout line
`[resona-debug] {"id","receivedAt","report"}` (Coolify logs) and returns `{id}`. It stores
nothing. Remove it once mobile live dictation and on-device title suggestion are diagnosed.
The checks live in `app/utils/debugProbe.ts` (`runDiagnose` must be called synchronously
from the click so its comparison AudioContext is created inside the user gesture; it
streams ~2 s of mic PCM to the hub `/v1/listen`). The client posts with plain `fetch`; the
route still adds `/api/debug-report` to Nitro's typed route union (see below).

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

### Hub profiles

`profiles/title-de.json` is this app's own postprocess profile (one `extract` step `title` on
endpoint `local-llm`; `useTitleJob` reads `structured.title.title`). A hub knows it only after
`just push-profiles` (`scripts/push-profiles.sh`: `resona profiles push` for `profiles/*.json`,
then the catalogue profiles offered under Nachbearbeiten, `transcript-markdown-de` and
`transcript-polish-de`, from `RESONA_PROFILE_CATALOGUE`, default `../../profiles`; `RESONA_CLI`
names the `resona` command; needs an org-admin `RESONA_API_KEY`). `test/titleProfile.spec.ts`
checks the file against the hub's documented profile format and `titleFromResult`, without
importing hub code.

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
it via `docker compose` (the `project-worker` service). A derived title (`title_auto`) is
best effort: a failed title write is logged (no text) and never marks the row `error`.

### Dictation MCP

`resona-dictations-mcp` lives in `mcp/` (package `resona_dictations_mcp`, own `uv.lock`,
`mcp==2.3.0` like the monorepo's `resona-mcp`, no imports from it or `packages/*`): MCP
access to a user's **own dictations** over Streamable HTTP, compose service
`dictations-mcp` (no published port; `docker-compose.backend.yml` binds loopback `:7740`
for `bun run dev`), reached at `<app origin>/mcp` through `server/routes/mcp.ts`
(`NUXT_DICTATIONS_MCP_INTERNAL_URL`, empty = 404; forwards no cookies). Rules: it acts
only with the caller's Directus token (`Authorization: Bearer`, verified via `GET
/users/me`, accepted tokens cached 30 s by hash, session bound to the token hash, a
Directus 401 in a tool call drops the cache entry), holds no admin/worker token, never
calls the hub, and adds no owner filter (the `user` policy scopes every read; `directus.py`
is the only Directus client, list queries go in a `SEARCH` body so they reach no URL log).
Tools (`server.py`, `TOOL_NAMES`, `tool-inventory.json` via `scripts/gen_tool_inventory.py`):
`list_dictations` (cursor = last `date_created` + count of rows already returned at that
instant, since Directus has no `_lt` on uuid ids), `search_dictations`, `get_dictation`
(`transcripts.text` in ranges ≤ 32768). Logs: `tool= user= outcome= ids= ms=`, never text,
titles, queries or tokens; third-party loggers stay at INFO. Tests (`just pwa test-mcp`)
start `directus` + `directus-bootstrap` from `docker-compose.backend.yml` as a throwaway
compose project on a free port (needs Docker; `DMC_TEST_DIRECTUS_URL` reuses a running
one). Epic #79; details `mcp/README.md`.

### Backup

`backup/` (`Dockerfile` on `restic/restic`, script `backup.sh` = `resona-backup`) is the
`backup` service of `docker-compose.coolify.yml`, active only with compose profile `backup`:
SQLite dump of Directus + uploads (app data only, never the hub volume: the hub backs
up itself, `docs/server/coolify.md#backup`) → encrypted restic
snapshot in S3, daily at `BACKUP_TIME` (`HH:MM`, 00:00–23:59), with retention. `last-success`
lives in the `backup-state` volume; the healthcheck stays healthy until the first backup
of a start is due + 2 h. Runbook: `docs/server/pwa-backups.md`.

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
  On-device Web Speech exists only in desktop Chrome ≥ 139 (Safari/iOS has no
  `available()`/`processLocally` → `unsupported`; Chrome on Android hard-codes
  `unavailable`); non-ready states are worded once in `DEVICE_SPEECH_HINTS`
  (`useDeviceSpeech.ts`), used by the drawer label, the `start()` error and the live notice.
  Live correction (`settings.correction`, `useLiveCorrection`): PCM is cut into blocks of
  `chunkSeconds` at final boundaries, each re-transcribed sequentially via hub
  `transcribeSync` (`correctionEngine`, empty → hub default); the page renders blocks
  (`live__block--corrected`/`--pending`), save awaits `finish()` (≤90 s, prompt shows
  `Korrektur läuft… (n Abschnitte)`) and stores `correction.text` stamped
  `<live stamp>+<correction engine | /v1/engines default | hub>` if any block was corrected.
  Start checks (`live.vue`, notice `data-testid="live-notice"` + `data-kind`): no hub token,
  401/403 (token rejected), unreachable hub (start re-checks first), other HTTP error, hub
  without live engine, and device source `unsupported|unavailable` (start disabled) or
  `downloadable|downloading` (no auto-start) each get their own German message (device ones
  from `DEVICE_SPEECH_HINTS`). `useLive`
  exposes `closeInfo` ({code, reason} of the last close, cleared on reconnect);
  `describeClose(info)` words it, and after 3 failed reconnects `error` names it.
  Title: `suggestTitle(text, {calendarTitle, date})` (preset > `deriveTitle`, see
  **Automatic titles**, else `Diktat dd.mm.yyyy HH:mm`) is shown from the *final* (or
  corrected) text once `LIVE_SUGGESTION_MIN_WORDS` (20, about 10–15 s) meaningful words are in,
  so it does not flip with every early word; a calendar preset shows none. On stop the save
  field gets exactly the shown suggestion (frozen: a correction finishing later never changes
  it), without one the rule title of the captured text. **On-device refinement first**
  (`app/utils/titleRefinement.ts`, German only): when the browser's built-in language model
  (Prompt API `LanguageModel`, desktop Chrome) reports `available` for `de`, `refineTitle()`
  turns the text into a short formulated title (`acceptRefinedTitle`: one line, ≤ 60 chars /
  8 words, not cut off, Latin script, no English or Romance function words; else `null`).
  Once per recording, never with a calendar preset: when the final text reaches
  `DEVICE_REFINE_MIN_WORDS` (40), else after stop from `TITLE_REFINE_MIN_WORDS` (6) meaningful
  words. `create()` is only called in state `available`, so nothing is ever downloaded here;
  any other state, error or unusable answer silently keeps the rule title. No network: the
  model runs in the browser outside the page thread (not exposed in Web Workers), the page only
  awaits it, so capture and live transcript keep running. Only when it gives no title,
  `useTitleJob().suggestTitleRemote` (hub `kind:"text"`
  job, profile `title-de` = `profiles/title-de.json` here, first 4000 chars, reads `structured.title.title`, always deletes the
  job, null on any failure) may replace it ("Titel wird verfeinert…"). Either result replaces
  the shown suggestion and the untouched save field; a typed title, Verwerfen, Speichern, a new
  start or leaving the page abort both and a late answer is dropped. A title field cleared
  before saving saves under `suggestTitle` again.
  **Opt-in download** of that browser model (Einstellungen → Live → "Titelvorschläge
  (Browser-KI)", `useTitleModel()`, module-level state so a download survives closing the
  drawer): the section names the real state (`Aktiv`, `Aktivierbar` with the storage cost and
  a button, `Modell wird geladen… n %` with a `<progress>`, or "Auf diesem Gerät nicht möglich"
  with the reason and no button). Only the button calls `installTitleModel()`, which calls
  `LanguageModel.create({monitor})` before any await (the browser needs the click's user
  activation); success re-checks to `available`, failure/abort shows a hint and "Erneut
  versuchen" while still `downloadable`. A `downloading` state the app did not start is
  re-checked every 5 s while the drawer is open. Nothing else downloads; the next recording
  uses the model without a reload because `refineTitle` checks availability each time.
- **Automatic titles**: one rule, `deriveTitle(text)` (`app/utils/titleSuggestion.ts`, no
  network): first Markdown heading, else the first sentence/clause (a line break ends it;
  list markers, link targets, filler and dictation-command words dropped, no leading/trailing
  function word), ≤ 6 words / 60 chars; null without usable words. The project-worker ports
  it (`worker/src/resona_project_worker/title.py`, `derive_title`); both specs run the cases
  in `shared/titleSuggestion.cases.json`, so change the rule, the port and the fixture
  together. Title is optional in `SaveSheet` (also for shares without a name): a recording
  saved without one gets `placeholderTitle()` (`Aufnahme dd.mm.yyyy HH:mm`; live dictations
  use `Diktat …`) and, when it will be transcribed, `recordings.title_auto = true`. Once the
  job completes the project-worker or `localProcessor` replaces the title with
  `deriveTitle(markdown)` and clears the flag (no usable text → placeholder and flag stay).
  Both write conditionally on `title_auto` (worker: `set_auto_title`, a batch update filtered
  on `title_auto = true`; `localProcessor`: `replaceAutoTitle`, checked inside the IndexedDB
  write transaction), so a rename (which sends `title_auto: false`) made while the job
  completes wins (Directus: except a rename landing inside that one update request). Not
  used yet: refining that title with the `title-de` hub job (`useTitleJob`) after
  transcription, as the live page does.
- **Expert mode** (Einstellungen → Verbindung, `useHubSettings().expert`, per device): the project-worker / `localProcessor` store the hub `JobResult.provenance` (`worker_id`, per-stage engine/model/accelerator/seconds) in the `provenance` json field of `transcripts` and `documents` (bootstrap adds it to existing installs); the recording and document pages render it via `describeProvenance()` (`app/utils/provenance.ts`) only when expert mode is on. Live-dictation transcripts have no provenance.
- **Nachbearbeiten** (re-process a transcript with a hub postprocess profile, hub job kind
  `text`): `PostprocessPanel.vue` on the recording page shows the run's status (running,
  error, raw transcript) inline; its profile choice, run and restore live in a dialog the
  header's ⋯ menu opens (closed after a request is written). It lists `GET /profiles`
  (`useProfiles`, which exposes a load `error` instead of a fake one-entry list) and writes
  `postprocessRequest(profile)` (`app/utils/postprocess.ts`: only `postprocess_status:
  "pending"` + `postprocess_profile`) onto the transcript row; it is enabled only when the
  transcript is saved, no dictation / continue-recording is running and this device has a
  hub token (`useHubTokenGate`; standalone needs it to run the job). The editor is
  read-only (`TranscriptEditor` `editable`) while the run is `pending|processing`. The
  profile always runs on the raw transcript (`raw_text ?? text`), never on an earlier
  profile's output; on success `text` becomes the job's `markdown`, `raw_text` keeps the raw
  transcript, `applied_profile` the profile and `processed_text` the output as delivered.
  The page then shows `text` rendered (`MarkdownView.vue` + `utils/markdown.ts`: headings,
  paragraphs, lists as text nodes, never `v-html`) with a "Bearbeiten" toggle to the editor;
  dictation switches to the editor too. New raw content added after a run (PTT dictation,
  a continued part) goes into `text` *and* is appended to `raw_text` (`appendToRaw`), so a
  re-run or "Rohtranskript wiederherstellen" (`rawRestore`) keeps it; when `text` differs
  from `processed_text` (`editedSinceProfile`) both ask before replacing those edits. A save
  that only added raw parts to an unedited output (no `typed` event from `TranscriptEditor`
  since the last save, which the exposed dictation/append methods never emit) also writes
  `processed_text`, so nothing would be lost and no prompt appears.
  Who runs it: Directus mode → the project-worker (`_run_postprocess` in `worker.py`: claim
  → `POST /v1/uploads` with the text as `transcript.md` → `POST /v1/jobs {kind:"text",
  profile}` → poll → write back → delete the hub job); standalone →
  `localProcessor.runLocalCycle` from the browser with the user's hub token. A request
  repeated while a job runs deletes that job and submits under a fresh key; a
  re-transcription clears the `postprocess_*` fields and deletes a text job still running
  on the old transcript. Same German messages in both (`POSTPROCESS_ERRORS` in `worker.py`
  is kept equal to `utils/postprocess.ts` by `worker/tests/test_postprocess.py`):
  `profile_required`, `profile_not_found`, `invalid_profile`, `policy_denied` (with the hub
  reason) become `postprocess_error`; a pending job's `queue_reason` is stored in
  `postprocess_queue_reason` and shown (`no_capable_worker` → "Kein passender Worker online
  …"). `useRecording` keeps polling while a run is queued. Editing a profile's output does
  not record a transcript correction (the engine did not write that text). In Directus
  mode the profile list comes from the *user's* hub token while the project-worker submits
  with *its* `RESONA_API_KEY`: both must belong to the same hub organization, else a listed
  profile fails with `profile_not_found`.
- **Uploads and shares** (`useUpload.saveRecording`): the file's type is normalised
  (`utils/audioFile.ts`; phones send `""`, `application/octet-stream` or `audio/x-m4a`, and the
  file used to be stored as `<title>.webm` with that type, so Directus served an unplayable
  Content-Type) and the stored filename gets the matching extension. A `duration_seconds` of
  0 is replaced by the real length (`probeDuration`: `<audio>` metadata, fallback
  `decodeAudioData`; recorder callers pass their own elapsed time).
- **Re-dictation** (`useRedictation`, own `useRecorder`, record bar `HRecordBar` in place of the
  player dock): parts are recorded one at a time, but a stopped part never blocks. Each part
  is a durable `recording_parts` row (Directus; `resona-local-parts` in standalone) whose id
  is the chip's `partId` (`crypto.randomUUID()` at record start). On stop the page stores it
  (`DataStore.createPart`: own audio file, `seq`, language/profile, anchor = offset of the chip
  in the *saved* text via `rebaseOffset` + `sha256Hex` of that text; null offset = append),
  then discards the recorder's IndexedDB session. The processor transcribes it like a
  recording (project worker / `localProcessor.ts`, upload `part-<id>`; the result goes to the
  part's `text`, status `done`, no transcript row). The page polls `listParts` every 2 s and
  lands `done` parts at their chip (status → `applied`); after every write it re-anchors the
  chips still waiting (`reanchor`). Opened again, unfinished parts come back as chips at their
  anchor while the saved text's SHA matches, else at the end with a notice; `done` ones land at
  once. Audio is appended to the recording strictly in stop order (`utils/audioMerge.ts`
  `mergeToWav`: OfflineAudioContext decode, mono, 16 kHz, PCM16 WAV, ~2 MB/min; saved via
  `DataStore.replaceAudio`) and the part gets `offset_seconds`/`duration_seconds`; a part with
  null `offset_seconds` is appended again on the next open (`retryAudio` on the page). Part
  audio is deleted once the part is finished and appended (worker cleanup /
  `releasePartAudio`). A failed job keeps the part for `retry` (back to `pending`) /
  `discard` (`discarded`). Leaving is only guarded while recording, storing or appending
  (`busy`) or for a part that could not be stored (`failed`, lives only in memory, `retry`
  stores it); stored parts keep transcribing without the page. "Neu transkribieren" and
  "Nachbearbeiten" wait until no part is unsettled.
- **Moving paragraphs** (desktop only, `TranscriptEditor` prop `movable` = `useShell().wide`):
  Alt+↑/↓ or the up/down control in the left gutter swaps the caret's paragraph with its
  neighbour (one undo step, `typed`; chips move along). The audio keeps recording order.
  `diffEdits` puts moved paragraphs back first (same trimmed text, or ≥ half the words shared),
  so a move is no engine correction.
- **Recording resilience** (record sheet, live, re-dictation part; web recorder only, the
  Capacitor app's native recorder has the recovery and phone-call interruptions, see **Capacitor app**): `useWebRecorder` holds a screen
  wake lock (`useWakeLock`, re-requested on `visibilitychange` → visible, released on stop;
  unsupported → nothing) and records with a 4 s timeslice (`RECORDER_TIMESLICE_MS`). With
  `persist`, every chunk goes to IndexedDB (`data/unfinishedRecordings.ts`, databases
  `resona-unfinished-recordings|chunks`, both data modes); a page owns its sessions via a Web Lock
  `resona-unfinished:<id>`, so only sessions nobody holds are offered. The caller discards the
  session (`sessionId`) once the audio is stored for good (saved recording, appended part,
  cancel). `RecordingsPane` offers left-over sessions ("Nicht gespeichert …", Behalten → save
  sheet / Verwerfen). On a microphone the recorder opened itself (record sheet, re-dictation),
  track `mute`/`ended` or `pagehide`/hidden page while recording closes the part and sets
  `interruption` ("Aufnahme unterbrochen": `HRecordingSheet`/`HRecordBar` show Fortsetzen →
  `continueRecording()` new part, or Beenden); `stop()` merges several parts with `mergeToWav`
  into one WAV. Live passes its shared stream plus `reopen` (fresh `getUserMedia`, also fed to
  the capture), so it is interrupted the same way: transcription stops with the audio,
  `live-interrupted` offers Fortsetzen, and the page holds its own wake lock for the session.
- **"Weiter diktieren"** in the list row menu (done or failed recordings): `RecordingsPane` sets
  `useRedictationRequest()` (useShell) to the id and opens the recording (a blocked navigation
  clears it); the detail page answers it once the recording is loaded: it starts a part when
  it can take one right then, otherwise drops the request.
- **Recording page toolbar**: one record button (`HRecordButton`) sits at the right end of
  `HPlayerDock` (or alone when there is no audio; hidden while a profile runs). Tap
  (`tap`, < 400 ms, or Enter/Space) records a re-dictation part in `HRecordBar`; hold
  (`hold-start`/`hold-end`) is push-to-talk, like left Ctrl. Its `press` notes whether the
  editor had the focus, so a part/dictation goes to the caret while editing, else to the
  end. The text is read-only until the header pencil (`data-testid="edit"`) turns editing
  on; a profile's output shows rendered until then. While editing or with unsaved edits,
  save and discard icons stand in the pencil's place, and both end editing. Next to them:
  the review toggle (`data-testid="review"`, "Freigeben", `aria-pressed` =
  `review_status === "ready"`), Teilen, Kopieren and a ⋯ `HMenu` with Nachbearbeiten… (opens
  `PostprocessPanel`'s dialog, `v-model:open`), Als Text / Als Markdown herunterladen and
  Löschen. The title is edited in the page body. The dock's track shows a faint tick at each
  stored part's `offset_seconds` (`store.listParts`, re-read when `audio_file` changes); the
  editor draws a soft line above and below each part's text (`sections` prop,
  `utils/partSections.ts`) until that text is edited.
- **Dictation chips** (`utils/dictationPart.ts`, TipTap inline atom, attrs `partId` + `status`
  `recording|transcribing|error`): push-to-talk and every re-dictated part put a chip at the
  caret (or the end when not editing); it moves with edits and adds nothing to the plain text
  (`renderText: ""`), so `v-model` and `dirty` ignore it. Chip insert/status/remove never
  enter the undo history; a plugin keeps exactly one chip per running dictation (a deleted
  chip is put back, a chip of a finished dictation brought back by undo/redo is removed).
  `TranscriptEditor.resolveDictationPart` replaces it with the text (re-dictation: own
  paragraph) as one undo step, never emitting `typed`, and returns `{base, at, inserted}`;
  the page saves only that part on its own (`autosavePart` + `utils/correction.ts`
  `rebaseInsertion` onto the last *saved* text, so unsaved typing is never written along; a
  part inside unsaved typing stays unsaved). `dirty` counts only typed edits (the editor's
  `typed` event) and parts whose own save failed, so pure dictation never triggers the leave
  dialog; a dictation or re-dictation still in flight gets a leave `confirm`. `useTranscriptEdit` serializes all
  writes (`enqueue`; `saving` is true from the click, a second click joins the running
  save), never writes into the editor text and never rolls back; the correction diff runs
  after the next paint, not awaited.

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
`resona-local-recordings|transcripts|documents|files|corrections|parts`, store `items`, files as
`{bytes, type, name}`), there is no login (`auth.global.ts` sends `/login` to `/`),
and the health indicator skips the Directus ping. `app/plugins/local-processor.client.ts`
runs `runLocalCycle()` (`app/data/localProcessor.ts`) every 5 s and on `online`: it
submits pending rows to the hub and polls their jobs, the same state machine as
`worker/`. Processing only happens while the app is open, and it needs a hub device
token (no token → nothing is processed).

### Capacitor app (prototype, Android first)

`capacitor.config.ts` + committed native projects `android/`, `ios/` wrap the static SPA
(`bun run cap:build` = `RESONA_CAPACITOR=1 nuxt generate && cap sync`; that flag disables
`@vite-pwa/nuxt`, so no service worker). Debug APK: `bun run cap:apk`. Build, device test and
findings: `docs/guide/pwa-app.md` (monorepo root). Recording goes through the `Recorder`
interface: `useRecorder()` returns `useNativeRecorder()` inside the app
(`hasNativeRecorder()`: `@capgo/capacitor-audio-recorder` ≥ 8.4 with `format: "aac"` = ADTS,
`audio/aac`; on Android a
`microphone` foreground service with a notification runs for the whole recording, refusing
to record when it cannot start; elapsed is wall-clock based; no IndexedDB chunks,
`persist`/`reopen` options are ignored), `useWebRecorder(options)` (MediaRecorder with the
wake lock, chunks and interruptions above) elsewhere. Native interruptions = a phone call (or
another app) taking the microphone; locking never interrupts. Android 10+: the app-local plugin
`RecordingSilence` (`android/app/src/main/java/de/ecsplico/resona/RecordingSilencePlugin.java`,
registered in `MainActivity`; `AudioManager.registerAudioRecordingCallback` +
`AudioRecordingConfiguration.isClientSilenced()`, no `READ_PHONE_STATE`; `start()` right before
the recorder starts ignores recordings already running, then pins the first new `MIC` recording's
session id as ours and judges only it; all state on the main looper, and a callback queued
before a re-registration ignores itself) emits
`silencedChanged {silenced, at}` (`at` = native wall-clock ms); the recorder then pauses the
native recording. iOS: the plugin's `recordingInterruptionBegan` (it paused itself) /
`recordingInterruptionEnded` (`shouldResume`: it resumed on its own, so the recorder pauses it
again; `shouldResume: false` without a begin, which the plugin also sends late after the user
continued, interrupts only when `getRecordingStatus()` is `PAUSED`) and `recordingError` (the
recorder broke and cannot resume: `continueRecording()` is refused without calling the plugin,
the notice says Beenden keeps everything recorded so far). Both
set `interrupted = "call"` (reason text in `utils/recorderInterruption.ts`, shared with the web
recorder; the native detail texts live in `useNativeRecorder.ts`), stop the clock and wait: `continueRecording()` resumes the same file, Beenden stops
it. Android notice: "… Die Aufnahme ist angehalten; diese Zeit fehlt darin …" (the delay until
the callback stays in as silence); an event reaching the web view more than `LATE_SIGNAL_MS`
(3 s) after `at` (the web view stalls under the dialer/lock screen) says "… ein Teil davon ist
als Stille darin" and the clock counts that span; a refused pause says the call is in the audio
as silence. iOS notice promises nothing about the audio ("Die Aufnahme ist angehalten,
Bisheriges bleibt erhalten."): capgo 8.4.1 resumes after `shouldResume` before the app can
pause, so audio after the call until that pause lands is in the file (the clock counts it).
Events during start/resume are applied afterwards (a recording started or continued during a
call is interrupted at once). Below Android 10 nothing is detected. Recovery after a kill (Android only):
`data/nativeUnfinished.ts` lists `cache/capacitor-audio-recorder/recording-*.aac` files older
than the web view (`performance.timeOrigin`; none while the plugin reports a recording; empty
files deleted, `.m4a` ignored) as `UnfinishedRecording`s with id `native:<file>`;
`listUnfinished`/`loadUnfinished`/`discardUnfinished` merge them, so `RecordingsPane` offers
them like web sessions; load keeps only whole ADTS frames (`utils/adts.ts` `scanAdts`, also
the length), save or Verwerfen deletes the file. `stop()` does not delete a recorder file: it
sets `sessionId = native:<file>` (`nativeSessionId`), and callers read `sessionId` after
`stop()` and discard it once the audio is saved, so an app killed while the save sheet is open
still finds it. The hub admits ADTS (sniffed as MPEG audio). `live.vue` always uses
`useWebRecorder()`: live dictation stays in the web view and is foreground-only. Not in the
app: Nitro routes (calendar,
debug-report, `/directus` proxy), the service-worker share target (the app has its own, below),
Keycloak SSO (the login page shows a hint instead of the button; what a handover would need:
findings doc). The hub needs `https://localhost` in `CORS_ORIGINS`.
Directus login in the app (#41): `useDirectus` picks the auth mode once via
`utils/directusAuth.ts` `directusAuthMode()`: web = `cookie` (unchanged: httpOnly
`SameSite=Lax` refresh cookie, `autoRefresh`, session in localStorage `resona.auth`), app
(`Capacitor.isNativePlatform()`) = `json` with `preferencesAuthStorage()` (access + refresh
token in `@capacitor/preferences` key `resona.auth`, memory copy; a token-less write removes
the key) and `autoRefresh: false` (no timer that freezes in the background and races the
rotating refresh token on resume; the SDK's `getToken()` refreshes before each request within
30 s of expiry, `restoreSession()` after a restart). `ssoSupported` is false in the app.
Stored tokens go only when Directus rejects the refresh token (`refreshRejected`: 400/401 from
`/auth/refresh`); offline/timeout/5xx keep them and the session (offline cold start stays
signed in). A request 401 → `getToken()` first (joins an SDK refresh in flight; the SDK's
`refresh()` never does); a newer token than the request used → retry, else one single-flight
`refreshSession()` + retry; rejected → clear + `/login`. `logout()` always clears the stored
session (also when the request fails).
`assetUrl()` is async (awaits `getToken()`, which refreshes an expiring token): await it in
every asset fetch path (`directusStore.audioUrl`/`partAudio`). Android
excludes `CapacitorStorage.xml` from backup (`res/xml/backup_rules.xml`,
`data_extraction_rules.xml`). Directus `CORS_ORIGIN` = `PWA_ORIGIN` + `APP_ORIGINS` (default
`https://localhost,capacitor://localhost`) in every compose file.
Share target in the app (Android): `SEND` intent filter (`audio/*`, PDF, PNG, JPEG, TIFF, DOCX)
and `SEND_MULTIPLE` (`audio/*`, `application/*`, `image/*`: the common type of several files) →
`@capgo/capacitor-share-target` (`shareReceived`, retained for cold starts) →
`plugins/native-share.client.ts` → `utils/nativeShare.ts` `receiveNativeShare` reads every usable
cached copy (`convertFileSrc` + `fetch`, copies deleted), stashes them via `utils/shareStash.ts`
`stashShares` (Cache API `resona-share-target-v1`, keys `/share-target/item/<i>`; the service
worker writes its one audio file to `/share-target/audio`) and opens
`/share-target?count=<n>&n=<nonce>[&skipped=<m>]` (page keyed by `fullPath`). `share-target.vue`
(`takeShares`): documents → `createDocument` at once, then audio → one SaveSheet (`count` > 1: no
title field, each recording keeps its file name) → recordings; one file opens its page, several
open `/` (or `/documents` when only documents), failures and skipped files are listed; all behind
the hub token gate.

## Tech stack

- **Nuxt 4** (`app/` dir layout, SPA `ssr: false`), **bun** (`bun@1.3.14`), TypeScript
- **Tailwind v4** via `@tailwindcss/vite` — CSS-first `@theme`, design tokens in
  `app/assets/css/tokens.css`. **No `tailwind.config.js`.**
- **@directus/sdk v22** — `createDirectus<Schema>(url).with(authentication(mode)).with(rest())`;
  `mode` `cookie` on the web, `json` in the Capacitor app (`utils/directusAuth.ts`)
- **@vite-pwa/nuxt** — service worker + manifest (installable, offline)
- **Capacitor 8** (app shell only) — `@capgo/capacitor-audio-recorder`,
  `@capawesome-team/capacitor-android-foreground-service`, `@capacitor/filesystem`,
  `@capgo/capacitor-share-target`, `@capacitor/preferences` (Directus tokens)
- **@vueuse/nuxt** (useWebSocket), **@nuxt/fonts** (Fraunces / Instrument Sans / JetBrains Mono)
- **idb-keyval** — IndexedDB offline queue. Every new recording saved from the list
  (`RecordingsPane.onSave`) goes through it: the save sheet closes at once, the upload runs
  in the background (one drain at a time, 5 min per attempt, retried every 30 s from the
  poll and on `online`), the queued row shows "Wird hochgeladen..." / "Wartet (offline)".
  The recording id is picked client-side (`QueueInput.recordingId` → `createRecording({id})`,
  Directus create permission allows `id`), so a retry after a timed-out attempt that did
  land finds that row instead of creating a second one
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
│   │                                #   useRecorder (Recorder interface, web/native pick),
│   │                                #   useNativeRecorder (Capacitor app), useUpload,
│   │                                #   useProfiles, useLive, useCalendar,
│   │                                #   useTranscriptEdit, useExport, useOfflineQueue
│   │                                # useWakeLock: screen wake lock, re-requested when visible again
│   │                                # useShell: shell context (wide ≥ 960 px, paneInLayout) +
│   │                                #   useSettingsDrawer (drawer state owned by the layout)
│   │                                # useHub: the only hub HTTP client (token, engines, jobs)
│   │                                # useHubSettings: per-device hub token (localStorage)
│   │                                # useDataStore: picks directusStore | localStore by data mode
│   │                                # useLiveSettings: live source/engine/correction settings
│   │                                # useTitleModel: browser title model state + opt-in download (module-level)
│   │                                # usePcmCapture: mic → 16 kHz PCM16 frames for /v1/listen
│   │                                # useDeviceSpeech: on-device Web Speech live source
│   │                                # useLiveCorrection: re-transcribes live blocks via the hub
│   │                                # useRedictation: record more parts onto a recording (durable recording_parts, landed at their chip)
├── data/
│   ├── types.ts                     # DataStore interface shared by both backends
│   ├── directusStore.ts             # DataStore over Directus (directus mode)
│   ├── localStore.ts                # DataStore over IndexedDB (standalone mode)
│   ├── localProcessor.ts            # runLocalCycle(): standalone hub job state machine
│   ├── unfinishedRecordings.ts      # IndexedDB chunks of recordings in progress (recovery after a kill)
│   └── nativeUnfinished.ts          # Android app: recorder .aac files a killed app left in the cache
├── plugins/local-processor.client.ts # standalone: runs runLocalCycle() every 5 s + on online
├── components/
│   ├── hearth/                      # HButton, HCard, HChip, HField, HMenu (⋯ overflow menu), HRecordingSheet (Verwerfen · big Pausieren/Fortsetzen · Aufnahme beenden; "Unterbrochen" state emits `continue`), HRecordBar (docked one-row record bar for re-dictation, same buttons/test-ids and interrupted state, `pending` live-region count), ... (design system)
│   ├── AppHeader                    # wordmark, ServerStatus, section tabs, settings cog
│   ├── RecordingsPane               # recordings list: filters, queue, list, capture dock, record flow
│   └── RecordingListItem, QueuedListItem, SaveSheet, StatusChip   # list row: copy + ⋯ menu (Teilen / Als Markdown exportieren (done only) / Weiter diktieren (done or error) / Löschen)
├── utils/pcm.ts                     # float32 → 16 kHz int16 PCM for live streaming
├── utils/adts.ts                    # scanAdts(): whole ADTS frames + seconds of a cut-off .aac
├── utils/audioMerge.ts              # mergeToWav(): decode + concat parts → 16 kHz mono WAV
├── utils/audioFile.ts               # uploads/shares: resolveAudioMime/normalizeAudio (playable type + extension), probeDuration (element metadata → decodeAudioData)
├── utils/hubTokenSync.ts            # pure per-user hub token sync (pull/push over a remote interface)
├── utils/debugProbe.ts             # temporary mobile diagnostics (live + Prompt API) for /debug
├── utils/titleRefinement.ts         # on-device title refinement via the browser Prompt API (available-only, never downloads)
├── types/directus.ts                # Recording / Transcript / Schema
└── assets/css/                      # tokens.css + main.css

shared/calendar.ts                   # calendar wire types + helpers (app + server)
shared/debugReport.ts                # debug report size limit + redaction (app + server)
server/                              # the sanctioned Nitro OAuth layer (server only)
├── api/calendar/index.{get,delete}.ts  # single provider-agnostic endpoint
├── api/debug-report.post.ts         # temporary: logs the redacted /debug report as [resona-debug]
├── routes/directus/[...path].ts     # same-origin proxy to Directus (allow-listed API paths)
├── routes/mcp.ts                    # same-origin proxy to the dictations-mcp service (/mcp)
└── utils/calendar/                  # provider interface + registry + Google + session
```

Component auto-import is `pathPrefix: false` — components resolve by filename
(`<HButton>`, `<QueuedListItem>`), regardless of subdirectory.

Row overflow menu: `hearth/HMenu.vue` (menu button, `aria-haspopup="menu"`, arrow/Home/End;
Escape, Tab and choosing an item close it and return focus to the button, an outside tap
closes it without moving focus; it emits `open` when it opens; the menu is
`position: fixed` because `.h-list` clips with `overflow: hidden`; clicks are stopped so
the row never opens). `RecordingListItem` emits one `action` event with a typed
`RowAction` (`share` | `export` | `redictate` | `delete`) plus `menu-open`; `RecordingsPane` maps each
action to its handler. Teilen = `navigator.share({title, text})`, called synchronously in
the tap (iOS drops the user activation across an await): opening the menu of a done
recording prefetches its share text into a per-id cache (pruned when the row leaves the
list or stops being done, cleared on an explicit list refresh, dropped on delete); a tap
before it arrived only shows "Wird vorbereitet … bitte erneut tippen.". Without
`navigator.share` (or on refusal) the text is copied with a status note. Löschen asks in a
modal confirm dialog (Tab/Shift+Tab stay inside, Escape cancels, focus returns to the menu
button) and then calls `useRecordings().remove(id)` (`DataStore.deleteRecording` + drop the
row, invalidating a list request still in flight). Deleting the recording open in the right
pane first navigates to `/`, so that page's leave guard runs; a vetoed or failed navigation
cancels the delete.

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

**Keep Nitro route paths minimal** (`/api/calendar`, GET+DELETE; provider via
`?provider=`, OAuth provider carried in `state`): Nitro types every `server/api/**`
file into the route-path union its typed `$fetch` recurses over, and a growing union
has pushed unrelated bare-string `$fetch` calls (e.g. `useProfiles`) into a TS
"excessive stack depth" error. The temporary `/api/debug-report` route is the one known
exception: with these two paths `bun run typecheck` passes, and no client code calls it
through `$fetch`. Don't add further routes. Keep client calls to the **literal**
`/api/calendar` string (exact match is cheap). The Google OAuth redirect URI is
`<origin>/api/calendar`.

## Directus schema (source of truth: `directus/bootstrap.md`)

- **recordings**: `id, user_created, date_created, date_updated, title, title_auto, audio_file,
  duration_seconds, language, profile, status (pending|transcribing|done|error),
  error_message, source (batch|live), review_status (editing|ready)` — **NO `engine`
  field**. `review_status` is the user's review workflow, independent of `status`.
  `title_auto` (boolean, default false) marks a placeholder title the processor replaces.
- **transcripts**: `id, recording, text, structured, segments, engine, provenance, date_created,
  date_updated` — the `engine` field lives **HERE**, not on recordings. Re-processing
  ("Nachbearbeiten") adds `raw_text` (the raw transcript a profile ran on; null while `text`
  is raw), `applied_profile`, `postprocess_status (pending|processing|done|error)`,
  `postprocess_profile`, `postprocess_error`, `postprocess_queue_reason`,
  `postprocess_job_id`, `postprocess_job_key` (bootstrap adds them to existing installs).
- **recordings** also has `hub_job_id` (integer, the in-flight hub job) and `hub_job_key`
  (string, the hub Idempotency-Key of the current submission attempt; persisted before
  `POST /v1/jobs`, kept by stale recovery, cleared on `done`/`error`).
- **recordings** links to a calendar appointment (Resona Verlauf, `/live?event=…&eventStart=…&eventTitle=…`):
  `calendar_event_id` (string, Google event id; recurring instances `<base>_20260603T133000Z`),
  `calendar_event_start` (timestamp), `calendar_event_title` (string, raw event title: holds the
  patient name), all nullable, set on create only when `event` is present. `adopted_at`
  (timestamp, null = not yet) is the Verlauf desktop "Übernommen" mark, written by
  `store.setAdopted(id, adopted)`. The `user` policy may create the `calendar_*` fields and
  update them plus `adopted_at`; bootstrap adds fields and widens existing permission rows.
- **documents**: `id, user_created, date_created, date_updated, title, file (→
  directus_files), status (pending|processing|done|error), error_message, markdown,
  pages (json), hub_job_id, hub_job_key`.
- **recording_parts** (re-dictation): `id` (client-chosen uuid = chip id), `recording` (→
  recordings, CASCADE), `seq`, `audio` (→ directus_files, SET NULL), `language`, `profile`,
  `status (pending|transcribing|done|applied|error|discarded)`, `anchor_offset`,
  `anchor_text_sha`, `offset_seconds`, `duration_seconds`, `text`, `error_message`,
  `hub_job_id`, `hub_job_key`. Users create `pending` parts on their own recording with their
  own file and may set only `applied|discarded|pending` plus anchors/offsets; `text`, errors
  and job fields are the worker's. The owner guard checks `recording` and `audio`.
- **Create ownership** of `transcripts` / `transcript_corrections` (parent recording,
  and transcript, must be the user's) is enforced by the hook extension
  `directus/extensions/directus-extension-resona-owner-guard`, not by the permission:
  Directus 11 ignores item rules on create and validates relational rules against the
  bare-id payload (which rejected every create, #19). Directus therefore runs from
  `directus/Dockerfile.directus` (pinned minor + extension) in every compose file;
  `bootstrap.mjs` refuses to run without the extension; CI job `directus` runs
  `directus/verify-permissions.mjs` against it. Details: `directus/bootstrap.md` §2.

The live path stamps the transcripts row's `engine` (`web-speech` / hub engine /
`hub-live`, `+<correction engine>` when corrected); the manual-save path writes it
without `engine`. The recordings payload must NOT include `engine`.

The Directus worker user is still named `transcribe-worker` (`transcribe-worker@resona.app`)
for existing installs, although it now processes documents too.

## Configuration

All public config flows through `runtimeConfig.public` (set via `NUXT_PUBLIC_*` env vars,
defaults in `nuxt.config.ts`). Read it with `useRuntimeConfig().public` — never hardcode
URLs. No token or engine is configured at build time — the hub token is per device
(Einstellungen → Verbindung). The 7 keys:

| Var | Default | Meaning |
|-----|---------|---------|
| `NUXT_PUBLIC_DIRECTUS_URL` | `http://localhost:7700` | Directus base URL |
| `NUXT_PUBLIC_SSO_DIRECTUS_URL` | `` (= Directus URL) | Directus base URL used only to start the SSO login (set by the Verlauf app to the PWA origin's `/directus`) |
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
| `NUXT_DICTATIONS_MCP_INTERNAL_URL` | (empty) | dictation MCP service behind `/mcp` (set in the compose files); empty = 404 |

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
| `bun run cap:build` | Static SPA for the Capacitor app + `cap sync` (`docs/guide/pwa-app.md`) |
| `bun run cap:apk` | `cap:build` + Android debug APK (needs JDK 21 + Android SDK) |

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
  (`server/api/**`, confidential Google token exchange/refresh only) and the temporary
  diagnostics route `server/api/debug-report.post.ts` (rate-limited, size-capped, logs a
  redacted report, stores nothing; to be removed) — it's otherwise a pure SPA: no page
  rendering (`ssr: false`), no business logic server-side.
- Don't deploy the web app via `nuxt generate` (static) now that Nitro routes exist — use the
  Nitro Node/serverless server (`nuxt build` → `node .output/server`). The only static build
  is the Capacitor app's web assets (`bun run cap:build`).
- Don't give the `dictations-mcp` service a Directus admin/worker token or hub access, and
  don't log transcript text, titles, search queries or tokens there.
- Don't hardcode backend URLs — use `useRuntimeConfig().public`.
- Don't set `engine` on a `recordings` row — it belongs on `transcripts`.
- Don't set live-WS auth via headers — browsers can't; use `?token=`.
- Don't commit `.env`.
