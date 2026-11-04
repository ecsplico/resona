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
  **create** validated against the same relational filter (parent recording
  owned by the current user)

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
