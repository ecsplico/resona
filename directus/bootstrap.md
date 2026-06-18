# Directus bootstrap

One-time setup for the Resona platform backend. Assumes the `directus`
compose service is running (`docker compose -f docker-compose.resona.yml
--profile faster-whisper up directus`), reachable at http://localhost:7700.

## 1. Collections

Create two collections (Settings → Data Model), or apply the committed
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

## 2. Roles & access policy

Create a role **"user"** (non-admin). Add a policy with:
- `recordings`: read/create/update/delete where `user_created = $CURRENT_USER`
- `transcripts`: read/update/delete where `recording.user_created = $CURRENT_USER`;
  **create** validated against the same relational filter (parent recording
  owned by the current user)

The batch worker authenticates with a **static service token** (admin role),
which bypasses the user policy.

## 3. Service token for the worker

Create a dedicated user (admin role) "transcribe-worker" → generate a static
token (User detail → Token). Put it in `.env` as `DIRECTUS_TOKEN`.

## 4. Schema snapshot (reproducible deploys)

Export after authoring:

    docker compose -f docker-compose.resona.yml exec directus \
      npx directus schema snapshot --yes /directus/schema-snapshot.yaml

This writes to the bind-mounted `./directus/schema-snapshot.yaml`. Commit it.

Apply on a fresh instance:

    docker compose -f docker-compose.resona.yml exec directus \
      npx directus schema apply --yes /directus/schema-snapshot.yaml
