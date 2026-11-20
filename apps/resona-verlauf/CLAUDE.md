# CLAUDE.md — Resona Verlauf

Resona Verlauf is a second medical-dictation app built as a **Nuxt layer app on top of
`../resona-pwa`** (`extends: ["../resona-pwa"]`). Everything in
`apps/resona-pwa/CLAUDE.md` applies here (style: tabs, double quotes, semicolons,
trailing commas; German UI copy; Hearth components; `ssr: false`; one thin Nitro layer
only for confidential OAuth). Read it first. This file lists only the differences.

## What this app adds

- `/` (`app/pages/index.vue`, overrides the layer's index): day navigator (‹ Heute ›,
  `?date=YYYY-MM-DD`, default today in Europe/Berlin; hidden on Offen) and tabs
  (`?tab=offen|diktat`):
  - **Termine**: the day's appointments whose title starts with `EG`/`FG`
    (`NUXT_VERLAUF_TITLE_PREFIXES`), each with "Diktiert (n)" / "Kein Diktat" / "Offen" from
    the user's recordings joined on `recordings.calendar_event_id` (`useTermine`). Not
    connected → "Google-Kalender verbinden".
  - **Offen** (count badge): past EG/FG appointments of the last 4 Wochen / 3 Monate /
    6 Monate (`?zeitraum=3m|6m`) with no linked dictation and no "Kein Diktat" mark, newest
    first (`useOffeneTermine`: `?from=&to=` range, then `store.listRecordings({ eventIds })`
    so the join does not depend on the 100 newest recordings). Row → `/live` (`liveQuery`);
    "Kein Diktat" → `POST /api/termine` decline, with "Rückgängig" for the last one.
  - **Diktat**: "Diktat ohne Termin" (`/live`) plus the layer's `RecordingsPane`.
- `/live` context (`app/components/LiveEventContext.vue`, overrides the layer's empty
  extension point; `nuxt.config.ts` pins the layer components dir to priority 1 so this
  app's same-named component wins without a duplicate-name warning): in expert mode only
  (`useHubSettings().expert`), collapsible "Frühere Diktate" above the transcript (the
  same patient's earlier appointments that have dictations, 200-char transcript preview,
  newest last) and "Folgende Termine" below. No links, so nothing leads away from a running
  dictation. Data: `loadTerminContext` (`?context=<id>` + `listRecordings({ eventIds })`).
- `/termin/:id?date=` (`app/pages/termin/[id].vue`): refetches the day and picks the event
  (no patient name in this URL), lists its dictations, "Diktat starten" →
  `/live?title=<title – dd.mm.yyyy>&event=<id>&eventStart=<iso>&eventTitle=<title>`
  (`liveQuery`); the layer's live page stores the three `calendar_*` fields.
- `/desktop` (`app/pages/desktop.vue`): table Datum · Termin · Titel · Text (200-char
  preview, "Mehr") · Kopieren (clipboard, guarded) · Übernommen (`store.setAdopted`,
  `recordings.adopted_at`), toggle "nur offene".
- Own `app/layouts/default.vue` + `VerlaufHeader` (Heute · Desktop); the recordings aside
  stays only on `/recording/:id`.
- `shared/termine.ts`: pure helpers (prefix filter, Berlin day bounds and ranges, grouping,
  `/live` query, open appointments, same-patient matching), spec `test/termine.spec.ts`.
  Import it as `~~/shared/termine`.

## Server: `/api/termine` (the only route family this app adds)

`server/api/termine.get.ts`, `termine.post.ts` + `server/utils/verlauf/` (`config`, `auth`,
`tokenStore`, `google`) + `server/middleware/termine-cors.ts`:

- `GET /api/termine?date=YYYY-MM-DD` → `{ connected, events: TerminEvent[], error? }`;
  `TerminEvent.declined: true` marks "Kein Diktat".
- `GET /api/termine?from=YYYY-MM-DD&to=YYYY-MM-DD` → same shape, inclusive Berlin days,
  at most 200 days (`MAX_RANGE_DAYS`), else 400.
- `GET /api/termine?context=<event id>` → `{ connected, before, after, error? }`: the
  event (`events.get`), then 12 months back / 6 forward the same patient's EG/FG
  appointments, oldest first. Candidates: Google `q` = title without its prefix (built
  server-side; the client sends only the id) plus the instances of its recurring series.
  Same patient = same series (`recurringEventId`, or the id before `_`) or the same
  title without prefix (whitespace collapsed, case-insensitive), so `EG X` and `FG X` match.
- `POST /api/termine` `{ action: "decline" | "undecline", event: "<id>" }` →
  `{ event, declined }`: "Kein Diktat" marks in storage `verlauf` key `declines`
  (`{ [eventId]: { declinedAt, declinedBy: email } }`, never a title), shared by all users.
- `GET /api/termine?action=connect` → 302 to Google consent (offline, `prompt=consent`).
- `GET /api/termine?code=…&state=…` → OAuth callback, then `/?kalender=verbunden|abgebrochen|fehler`.
- Auth: `Authorization: Bearer <Directus access token>` (connect: also `?access_token=`),
  checked at `${NUXT_DIRECTUS_INTERNAL_URL}/users/me`; email must be on
  `NUXT_VERLAUF_ALLOWED_EMAILS` (empty = any signed-in user). The callback is authorised
  by its sealed state (10 min) plus a matching httpOnly nonce cookie.
- ONE calendar connection for the whole app: the refresh token is sealed with
  `NUXT_SESSION_PASSWORD` (iron) in Nitro storage `verlauf` (fs-lite on
  `NUXT_VERLAUF_DATA_DIR`, a volume in production); the access token lives in memory.
  Google `invalid_grant` deletes the stored token → `connected: false`.
- Token exchange and refresh reuse the layer's `server/utils/calendar/google.ts`; event
  reads (`googleEventParams`, `normalizeGoogleEvent`, `eventsEndpoint`) page through
  `nextPageToken` here (250 per page, ≤ 20 pages), since the layer's `listEvents` stops at
  50. Cancelled events are dropped; the prefix filter and the wire shape (no
  description/location) are `filterTermine`.
- **Privacy**: event titles hold patient names. Never log titles or transcripts;
  server logs carry only HTTP status codes.

## Layer pitfalls

- Aliases inside the layer's `nuxt.config` (`~`, `~~`, `#shared`) resolve against this
  app. Hence in `nuxt.config.ts`: `#shared` → `../resona-pwa/shared/`, the layer's
  `app/components` added explicitly, and `app/assets/css/main.css` forwarding to the
  layer stylesheet. Nitro has no per-layer alias rewriting: layer server code must use
  relative imports, never `~~/…`.
- `runtimeConfig.public.calendarEnabled` is false here (the layer's `/calendar` and
  `/api/calendar` stay off).
- Capacitor build (`bun run cap:build`, `RESONA_CAPACITOR=1`): no Nitro server, so set
  `NUXT_PUBLIC_VERLAUF_API_BASE` to the deployed origin; the CORS middleware admits
  `https://localhost` and `capacitor://localhost` for `/api/termine` only.

## Commands

Run from `apps/resona-verlauf/` after `bun install` in `../resona-pwa` and here:
`bun run dev` (:7731), `bun run test`, `bun run lint`, `bun run typecheck`, `bun run build`.
Docker: `apps/resona-verlauf/Dockerfile` with build context `apps/`.
