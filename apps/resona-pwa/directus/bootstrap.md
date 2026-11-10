# Directus bootstrap

One-time setup for the Resona platform backend.

## Automated (default)

`docker compose up -d` runs the **`directus-bootstrap`** one-shot service
(`directus/bootstrap.mjs`) once Directus is healthy. It is idempotent — it
checks before every step and re-runs harmlessly — and brings a blank instance
to exactly the state documented below:

- the `recordings` + `transcripts` + `documents` collections, fields, and M2O
  relations (§1). Fields missing on an existing install (e.g. a volume created
  before `hub_job_id` / `documents` existed) are added and logged as
  `+ field <collection>.<field>`;
- the non-admin **`user`** role + `user-policy` + owner-scoped permissions (§2),
  plus `directus_files` upload/read/update for the owner (needed for audio and
  document files);
- the **create ownership guard flows** `resona-owner-guard-transcripts` and
  `resona-owner-guard-transcript_corrections` (§2): a user may only create a
  transcript or correction on their own recording;
- the custom field **`directus_users.hub_token`** (string, hidden) plus `user`-policy
  `directus_users` read/update restricted to `id = $CURRENT_USER` and the fields
  `id`/`hub_token`: the user's own Resona hub token, synced across their devices by
  the PWA (directus mode). Other users' rows and fields stay unreadable; admins are
  unaffected;
- the **`transcribe-worker`** admin user whose static token is **pinned to
  `DIRECTUS_TOKEN`** from `.env` — so the worker authenticates with no
  copy-paste (§3). `DIRECTUS_TOKEN` must be set (e.g. `openssl rand -hex 24`);
  compose refuses to start the bootstrap if it's empty.

The admin user itself is seeded by the Directus image from
`DIRECTUS_ADMIN_EMAIL` / `DIRECTUS_ADMIN_PASSWORD` on first boot (empty DB only).

Run it standalone against a running Directus with:

    DIRECTUS_URL=http://localhost:7700 \
    ADMIN_EMAIL=… ADMIN_PASSWORD=… DIRECTUS_TOKEN=… \
    node directus/bootstrap.mjs

The sections below document **what the script does** (and the manual UI path if
you ever need it). `bootstrap.mjs` is the source of truth for the live setup;
this prose and `schema-snapshot.yaml` are reference.

## 1. Collections

Create three collections (Settings → Data Model), or apply the committed
snapshot (§4).

### `recordings`
| Field | Type | Notes |
|-------|------|-------|
| id | UUID | primary key |
| user_created | M2O → directus_users | "User Created" special field |
| date_created | timestamp | "Date Created" special field |
| date_updated | timestamp | "Date Updated" special field — Directus bumps it on every update (e.g. the worker's `claim` PATCH); the worker's stale-claim recovery filters on it, so it MUST be present |
| title | string | |
| title_auto | boolean | default `false` — `title` is a date placeholder; the project-worker replaces it with a title derived from the finished transcript and clears the flag. User may create/update it (a rename sends `false`) |
| audio_file | M2O → directus_files | |
| duration_seconds | float | |
| language | string | default `de` |
| profile | string | default `default` |
| status | string (dropdown) | pending / transcribing / done / error; default `pending` |
| error_message | text | nullable |
| source | string (dropdown) | batch / live; default `batch` |
| review_status | string (dropdown) | editing / ready; default `editing` — user review workflow, independent of `status` |
| hub_job_id | integer | nullable — durable Resona hub job id set by the project-worker while the job runs; rows with it set are never reclaimed as stale |

### `transcripts`
| Field | Type | Notes |
|-------|------|-------|
| id | UUID | primary key |
| recording | M2O → recordings | one per recording |
| text | text | |
| structured | json | nullable |
| segments | json | nullable |
| engine | string | |
| date_created / date_updated | timestamp | special fields |
| provenance | json | nullable — hub `JobResult.provenance` (expert mode) |
| raw_text | text | nullable — the raw transcript a postprocess profile ran on ("Nachbearbeiten"); null while `text` is raw |
| applied_profile | string | nullable — profile whose output `text` is |
| processed_text | text | nullable — that profile's output as delivered (the PWA asks before a re-run/restore replaces edits made since) |
| postprocess_status | string | nullable — pending / processing / done / error; the PWA sets `pending` + `postprocess_profile`, the project-worker runs the hub text job |
| postprocess_profile | string | nullable — requested hub profile name |
| postprocess_error | text | nullable — German message of a failed run |
| postprocess_queue_reason | string | nullable — hub `queue_reason` while the text job waits (`no_capable_worker`, …) |
| postprocess_job_id / postprocess_job_key | integer / string | nullable — in-flight hub text job and its Idempotency-Key (like `hub_job_*` on recordings) |

### `documents`
| Field | Type | Notes |
|-------|------|-------|
| id | UUID | primary key |
| user_created | M2O → directus_users | "User Created" special field |
| date_created / date_updated | timestamp | special fields (stale recovery filters on `date_updated`) |
| title | string | |
| file | M2O → directus_files | PDF / PNG / JPEG / TIFF |
| status | string | pending / processing / done / error; default `pending` |
| error_message | text | nullable |
| markdown | text | nullable — hub result |
| pages | json | nullable — hub result pages |
| hub_job_id | integer | nullable — durable hub job id (project-worker) |

## 2. Roles & access policy

Create a role **"user"** (non-admin). Add a policy with:
- `recordings`, `documents`: read/create/update/delete where `user_created = $CURRENT_USER`
- `transcripts`: read/update/delete where `recording.user_created = $CURRENT_USER`;
  create without an item rule (see below)
- `transcript_corrections`: read own (`user_created`), create (see below); never
  updated or deleted by users
- create/update carry **explicit field lists**: the job bookkeeping (`hub_job_*`,
  `postprocess_job_*`, `postprocess_error`, `postprocess_queue_reason`) is the
  project-worker's, so a user can never point a row at another hub job of the
  organization. Users may write `postprocess_status` only as `pending` or null
  (validation). The bootstrap patches existing permissions whose fields/filters
  differ (`~ perm … updated`), so re-running it tightens older installs.

- `directus_users`: read (`id`, `hub_token`) and update (`hub_token`) where
  `id = $CURRENT_USER` — the user's own hub token, nothing else. Needs the custom
  field `hub_token` (string) on `directus_users`.

**Create ownership guards (Flows).** Directus 11 ignores item rules (`permissions`)
on create and checks `validation` against the request payload, where an M2O is a
bare id: a relational validation such as `recording.user_created = $CURRENT_USER`
therefore rejected every create ("Value is required"), and an item rule would let a
user attach rows to someone else's recording. The bootstrap instead provisions one
blocking filter flow per collection (`resona-owner-guard-transcripts`,
`resona-owner-guard-transcript_corrections`, trigger `items.create`): a condition
requires the parent ids (`recording`; for corrections also `transcript`), then a
*Read Data* operation per parent reads it with the caller's permissions
(`$trigger`); a correction's transcript must also belong to its `recording`. A
parent the user cannot read (someone else's, missing) rejects the flow and Directus
aborts the create with 403 (no parent id: 400); a batch is rolled back as a whole.
Admin tokens (the project-worker) read every parent. The bootstrap installs the
guards before it syncs the permissions and replaces a flow whose definition differs
(`~ flow …`) by building the new one inactive, activating it, then deleting the
old, so a collection is never unguarded. Flows are not part of
`schema-snapshot.yaml`: a manual setup needs the bootstrap (or the same flows built
in the Data Studio). Do not edit or disable these flows there.

The project-worker authenticates with a **static service token** (admin role),
which bypasses the user policy.

## 3. Service token for the worker

Create a dedicated user (admin role) "transcribe-worker" → generate a static
token (User detail → Token). Put it in `.env` as `DIRECTUS_TOKEN`.

## 4. Schema snapshot (reproducible deploys)

Export after authoring:

    docker compose exec directus \
      npx directus schema snapshot --yes /directus/schema-snapshot.yaml

This writes to the bind-mounted `./directus/schema-snapshot.yaml`. Commit it.
The snapshot only contains fields that have a `directus_fields` row, i.e. were
created with `meta`. `bootstrap.mjs` gives every field `meta` and backfills it on
older installs (`~ meta ...`), so run the bootstrap before exporting.

Apply on a fresh instance:

    docker compose exec directus \
      npx directus schema apply --yes /directus/schema-snapshot.yaml
