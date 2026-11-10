# Coolify Deployment (public hub)

The public hub runs on the ecsplico Coolify server from `docker-compose.coolify.yml` (Docker Compose build pack, **Raw Compose Deployment** on). It contains the hub and one internal live recognizer:

| Service | Image | Listens | Route |
|---------|-------|---------|-------|
| `hub` | `ghcr.io/ecsplico/resona-hub@sha256:<digest>` | `:7000` (`expose` only) | `https://resona-hub.ecsplico.de/*` |
| `mcp` | same image, `resona-mcp http` | `:7010` (`expose` only) | `https://resona-hub.ecsplico.de/mcp` |
| `engine-faster-whisper` | `ghcr.io/ecsplico/resona-engine-faster-whisper@sha256:<digest>` | `:7001` (`expose` only) | none: internal, reached only by `hub` |

There are no GPUs, Docker socket mounts, privileges or published ports. All containers run with a read-only root filesystem, all capabilities dropped and `no-new-privileges`; `hub` and `mcp` run as UID 1000, the engine as its image's root user with only its model cache volume `hf-cache` and `/tmp` writable. SQLite and the transient job payloads live on the named volume `resona-data` (`/data`); the database is never exposed. Queued jobs (uploads, documents, postprocessing) run on approved pull workers that connect outbound over HTTPS ([Mac worker](mac-worker.md), [Linux CUDA worker](full-stack.md)). When no worker is online, jobs wait in the queue. Live dictation (`WS /v1/listen`) does not go through workers: the hub bridges it to an engine-server, by default the internal `engine-faster-whisper` ([Live recognizer](#live-recognizer)).

TLS ends at Coolify's Caddy proxy. In raw mode Coolify adds no labels or networks, so the compose file declares the Caddy labels and the external `coolify` network itself.

## Hostnames and the PWA

- Hub + MCP: `https://resona-hub.ecsplico.de`.
- PWA: `https://resona.ecsplico.de`, Coolify app `resona-pwa` (raw compose `apps/resona-pwa/docker-compose.coolify.yml`, base directory `/apps/resona-pwa`): Directus (SQLite volumes `directus-db`/`directus-uploads`), `directus-bootstrap`, `pwa` and `project-worker`. Directus has **no public host**: the PWA proxies an allow-list of its API paths at `/directus/*` (`server/routes/directus/[...path].ts`; no admin UI). Admin tasks run inside the container (`docker exec <app-uuid>-directus-1 ...`).
- Login: Keycloak realm `ecsplico` through Directus' OpenID provider `keycloak` (Directus users are auto-created with the `user` role; password login stays as a fallback). Keycloak client `resona-pwa` (confidential, standard flow): redirect URI `https://resona.ecsplico.de/directus/auth/login/keycloak/callback`, web origin `https://resona.ecsplico.de`; its secret is the Coolify env `KEYCLOAK_CLIENT_SECRET`. Other required env: `DIRECTUS_SECRET`, `DIRECTUS_TOKEN`, `DIRECTUS_ADMIN_*`, `DIRECTUS_USER_ROLE_ID`, `PWA_ORIGIN`, `NUXT_PUBLIC_DIRECTUS_URL=https://resona.ecsplico.de/directus`, `NUXT_PUBLIC_RESONA_API_URL`, `RESONA_API_URL`, `RESONA_API_KEY` (client token of the `ecsplico` org, used by the project-worker).
- Session length: Directus requests `offline_access` (`AUTH_KEYCLOAK_SCOPE`, override with `KEYCLOAK_SCOPE`) and refreshes the stored Keycloak token on every `/auth/refresh` (every ~15 min while the PWA is open). With an offline token the PWA session lasts until `REFRESH_TOKEN_TTL` (`DIRECTUS_REFRESH_TOKEN_TTL`, default `30d`) of inactivity, instead of ending with the Keycloak SSO session (30 min idle / 10 h max). Realm admin, realm `ecsplico`:
  1. Clients → `resona-pwa` → Client scopes: `offline_access` assigned (Optional is enough, Directus requests it). Settings: "Consent required" off.
  2. Realm roles: `offline_access` stays in `default-roles-ecsplico`; users without that role get no offline token and the login fails.
  3. Realm settings → Sessions: Offline Session Idle ≥ `REFRESH_TOKEN_TTL` (default 30 days fits). Leave "Offline Session Max Limited" off, or set Offline Session Max (e.g. 365 days) to force a yearly re-login.
  4. Realm settings → Tokens: keep "Revoke Refresh Token" off (parallel refreshes from two tabs would revoke each other's token).
  5. Takes effect at each user's next SSO login.
  6. Logging out in the PWA does not end the Keycloak offline session: revoke it under Users → user → Sessions (Offline). Disabling a user in Keycloak ends their PWA session at the next refresh (≤ 15 min).
- Encrypted off-site backups and the encryption-at-rest finding: [PWA backups](pwa-backups.md) (compose profile `backup`).
- Legacy: the hub also answers `https://resona.ecsplico.de/v1/workers/*` (a `handle` route in the compose labels) so workers still configured with the old origin keep working. Re-point them with `--hub https://resona-hub.ecsplico.de`, then remove the `caddy_1*` labels.

## Image

Every service runs a released image from GHCR, pinned by digest; the compose file has no `build:`, so Coolify only pulls. The GHCR packages are **private**. The Coolify host (`<user>@<coolify-host>`) holds a GitHub token with the single scope `read:packages` in `/root/.docker/config.json` (check with `curl -sI -H "Authorization: token <t>" https://api.github.com/user | grep x-oauth-scopes`), which Coolify's `docker compose pull` uses for both `resona-hub` and `resona-engine-faster-whisper`. Never put a token with wider scopes there. Should that token be revoked, pull the pinned digests from your own logged-in machine through the remote daemon instead of logging the server in: `docker --host ssh://<user>@<coolify-host> pull ghcr.io/ecsplico/resona-hub@sha256:<digest>` (likewise for the engine image; the client sends the credential per request; nothing is stored on the server) and add `pull_policy: missing` to the services so Compose skips the pull of a digest that is already present.

Bump per release:

1. The release workflow ([Releases](releases.md)) pushes `ghcr.io/ecsplico/resona-hub:<version>` and `ghcr.io/ecsplico/resona-engine-faster-whisper:<version>` and records their digests in `release-manifest.json` (`images[].name`).
2. Replace the digest in **both** hub `image:` lines (`hub`, `mcp`) of `docker-compose.coolify.yml` and in the `engine-faster-whisper` `image:` line with those digests, update the `Current …` comment lines, commit and push to `main`.
3. Deploy (below), then confirm the running digests: `docker inspect --format '{{index .RepoDigests 0}}' <app-uuid>-hub-1` (and `<app-uuid>-engine-faster-whisper-1`) via a scheduled task on the host or the deployment log's pull lines.

Manual build outside the workflow (the first image, `0.1.0-d7405d3`, was built this way): build from a clean `git archive <commit>` tree, never the working tree, `docker buildx build --platform linux/amd64 -f packages/api/Dockerfile -t ghcr.io/ecsplico/resona-hub:<version>-<commit> --push --metadata-file meta.json .`, and pin `containerimage.digest` from `meta.json`. The Coolify server is amd64.

## Environment

No variable is required; the defaults serve `https://resona-hub.ecsplico.de`.

| Variable | Default | Effect |
|----------|---------|--------|
| `RESONA_PUBLIC_URL` | `https://resona-hub.ecsplico.de` | Caddy site, CORS origin, MCP `Origin` allowlist |
| `CORS_ORIGINS` | `https://resona.ecsplico.de,${RESONA_PUBLIC_URL}` (the PWA origin plus the hub) | Comma-separated browser origins allowed by CORS (`CORS_ORIGINS: ${CORS_ORIGINS:-${RESONA_PUBLIC_URL:-https://resona.ecsplico.de}}`). Set it to add the PWA origin, e.g. `https://resona.ecsplico.de,https://<pwa-host>` |
| `RESONA_PUBLIC_HOST` | `resona-hub.ecsplico.de` | MCP `Host` allowlist |
| `RESONA_MAX_UPLOAD_BYTES` | `209715200` | Upload and worker-result cap. The app enforces it while streaming, independent of the proxy |
| `RESONA_ENGINE_URLS` | `http://engine-faster-whisper:7001` | Hub: comma-separated engine-server base URLs for live dictation. Probed in order; the first reachable one per engine name wins ([Live recognizer](#live-recognizer)) |
| `DEFAULT_FASTWHISPER_MODEL` | `small` | Internal engine: faster-whisper model. `base` is lighter and faster, `medium` needs more CPU/RAM than the defaults below allow |
| `FASTWHISPER_COMPUTE_TYPE` | `int8` | Internal engine: CTranslate2 compute type |
| `FASTWHISPER_BEAM_SIZE` | `1` | Internal engine: greedy decoding |
| `FASTWHISPER_CPU_THREADS` | `3` | Internal engine: CTranslate2 threads; keep equal to `ENGINE_CPUS` |
| `FASTWHISPER_BATCHED` / `FASTWHISPER_BATCH_SIZE` | `true` / `4` | Internal engine: batched inference pipeline |
| `ENGINE_CPUS` | `3` | Internal engine: CPU limit (`cpus`) |
| `ENGINE_MEM_LIMIT` | `3g` | Internal engine: memory limit (`mem_limit`) |
| `LOGLEVEL` | `info` | Uvicorn log level (hub and engine). Access lines never carry query strings |

`RESONA_BOOTSTRAP_ORGANIZATION_ID` is needed only when an old pre-identity database with job rows is migrated. A fresh volume does not need it.

### Migrating a pre-identity database

The first start on an old (`/jobs`-era) database takes a SQLite backup, then runs the numbered migrations in one exclusive transaction. Every old job row is assigned to `RESONA_BOOTSTRAP_ORGANIZATION_ID`, keeps its integer ID and becomes `kind=audio`, `private=true`, with its old language or `de`:

- **Completed** rows: transcript, segments and Markdown move out of the database into result artifacts with the normal 24-hour result lifetime; the old payload columns are dropped.
- **Failed/cancelled** rows stay terminal; their input counts as deleted.
- **Non-terminal** rows (`pending`, `processing`) are **not** revalidated or requeued. They have no approved execution snapshot and no input in the new storage layout, so the migration fails them with error code `legacy_unprocessed` ("queued before the upgrade; resubmit the input"). Submit those inputs again as new jobs. Old input files under `FILE_PATH` are deleted after the migration commits.

After the converted results are verified, the pre-migration backup is deleted under the same retention rule as the content.

## Deploy

```bash
coolify app create public --server-uuid <server> --project-uuid <project> --environment-name production \
  --git-repository https://github.com/ecsplico/resona --git-branch main \
  --build-pack dockercompose --ports-exposes 7000 --is-raw-compose-deployment-enabled
coolify app update <app-uuid> --docker-compose-location /docker-compose.coolify.yml
coolify deploy uuid <app-uuid>
coolify deploy get <deployment-uuid> --format json   # until "status": "finished"
```

Check:

```bash
curl https://resona-hub.ecsplico.de/health       # {"status":"ok"}
curl https://resona-hub.ecsplico.de/ready        # 200 {"status":"ready",...,"capacity":"degraded"|"available"}
curl https://resona-hub.ecsplico.de/v1/version   # {"release_id":...,"hub_version":...,"worker_protocol":1}
```

`/ready` turns `503` when the schema is behind, `/data` is not writable or the maintenance loop stalls. Offline workers only change `capacity` to `degraded`.

## Live recognizer

Live dictation in the PWA (and `resona live --remote`) opens `wss://resona-hub.ecsplico.de/v1/listen`. The hub authenticates the org credential and bridges the audio to an engine-server's `/ws/live`; `GET /v1/listen/engines` lists the engines it can bridge to right now. Without a reachable engine-server that list is empty and live dictation cannot start, so the compose file ships one: `engine-faster-whisper`, CPU-only.

- **Reachability.** No Caddy labels, not on the `coolify` network, no published port: only containers of this compose project reach `engine-faster-whisper:7001`. The hub sends no engine key, and the engine's `/ws/live` and `/health` take none, so do not set `RESONA_ENGINE_KEY` on it; the network isolation is the protection.
- **Model and limits.** `small` with `int8`, greedy decoding and 3 CTranslate2 threads, capped at 3 CPUs and 3 GB RAM (`small` int8 uses roughly 1–1.5 GB). The host has 6 cores and 16 GB shared with other applications ([CPU fallback](#cpu-fallback)), so these caps keep the engine from starving them. All live sessions share the one loaded model and the 3 CPUs: one session runs near real time, several concurrent sessions lag. For more sessions or higher accuracy use a GPU box (below), not bigger CPU limits.
- **First start.** The engine downloads the model weights into the `hf-cache` volume (needs outbound HTTPS to huggingface.co) and loads them before it answers `/health`; the healthcheck allows 10 minutes. The image is CUDA-based (several GB) but runs on CPU. Later starts load from the volume.
- **Hub independence.** The hub does not depend on the engine: it starts and serves jobs without it, and probes `RESONA_ENGINE_URLS` per request (cached a few seconds). While the engine is down, `/v1/listen/engines` lists no available engine and live dictation cannot start.

### Switching to a GPU engine-server

Run a `resona-engine-faster-whisper` (or another engine) on a GPU host ([Full stack](full-stack.md)) and set the hub env in Coolify:

```
RESONA_ENGINE_URLS=https://<gpu-engine-host>,http://engine-faster-whisper:7001
```

The hub probes the URLs in order. Two engines reporting the same name (`faster-whisper`) are deduplicated, so the first reachable one serves and the internal CPU engine takes over when the GPU box is down. An unreachable URL costs up to 5 s per catalogue refresh. To drop the CPU engine entirely, list only the GPU URL and remove the `engine-faster-whisper` service.

The engine-server has no authentication on `/ws/live` and carries raw audio: reach the GPU host only over a private network (VPN, WireGuard/Tailscale) or behind TLS restricted to the Coolify host's address, never over plain `http://` across the internet. An `https://` URL is bridged as `wss://`.

### Rollout

1. Merge the compose change to `main`. The Coolify app (`resona-hub`) has no `RESONA_ENGINE_URLS` set; leave it unset to use the internal engine (or set it as above for a GPU box).
2. Make sure the host's `read:packages` token can pull `ghcr.io/ecsplico/resona-engine-faster-whisper` (same private org; see [Image](#image)).
3. Deploy (`coolify deploy uuid <app-uuid>`) and wait for `"status": "finished"`. The engine container turns healthy after the model download.
4. Verify (below). Rollback: redeploy the previous commit; the `hf-cache` volume can be deleted afterwards.

### Verify

```bash
export RESONA_API_KEY=$(cat ~/.resona/hub-client.token)
curl -s -H "Authorization: Bearer $RESONA_API_KEY" https://resona-hub.ecsplico.de/v1/listen/engines
# {"engines":[{"name":"faster-whisper","kind":"local","private":true,"models":["small"]}],"default":"faster-whisper"}
```

An entry named after a URL (`http://engine-faster-whisper:7001`) or an empty list means the hub cannot reach the engine: check `docker ps` health of `<app-uuid>-engine-faster-whisper-1` and its logs (model download, out-of-memory kills under `ENGINE_MEM_LIMIT`).

End-to-end `wss` test from a machine with a microphone and the CLI:

```bash
RESONA_API_KEY=$(cat ~/.resona/hub-client.token) \
  resona live --remote https://resona-hub.ecsplico.de --engine faster-whisper --language de
```

Speak; text appears with a short delay. Without a microphone, the handshake alone can be checked with any WebSocket client: connect to `wss://resona-hub.ecsplico.de/v1/listen?engine=faster-whisper&language=de&encoding=linear16&sample_rate=16000&token=<client token>`, send a few seconds of 16 kHz mono int16 PCM as binary frames, then `{"type":"CloseStream"}`; the hub answers with `Results` messages and a closing `Metadata`. An `Error` message names the reason (unknown engine, engine unavailable). Finally open live dictation in the PWA.

## Approve releases before production jobs

A freshly deployed hub runs no production jobs until an evaluation of each `(execution profile, platform)` has been promoted on **this** hub ([Releases](releases.md)); jobs submitted before that wait with `queue_reason: release_not_approved`. With the Mac worker online, run per profile the clients use (`private-auto` is the organization default, `mac-private` the worker profile):

```bash
export RESONA_API_URL=https://resona-hub.ecsplico.de RESONA_API_KEY=$(cat ~/.resona/hub-admin.token)
resona eval --profile private-auto --remote --output-dir /tmp/eval-private-auto
resona eval promote <evaluation-id> --report /tmp/eval-private-auto/report.json   # pins eval/baselines.json; commit it
```

Queued jobs are claimed as soon as the promotion lands. `resona-admin worker approve` on the hub, `/ready`, and `GET /v1/baselines?execution_profile=private-auto&platform=mac-metal` show the state (404 `baseline_required` until promoted).

## Provision credentials

`resona-admin` runs inside the `hub` container: `ssh <user>@<coolify-host> docker exec -i <app-uuid>-hub-1 resona-admin ...`. Without SSH, use a Coolify scheduled task on the hub container (`<app-uuid>-hub-1`), execute it once, then delete it. The container's root filesystem is read-only; `/tmp` is a tmpfs. Generate tokens on your own machine and register only their SHA-256 hash, so the tokens never reach the server or its logs:

```bash
# on your machine
(umask 077; python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > ~/.resona/hub-client.token)
shasum -a 256 < <(tr -d '\n' < ~/.resona/hub-client.token) | cut -d' ' -f1

# in the hub container
resona-admin organization create ecsplico
resona-admin credential issue --organization ecsplico --role client --token-sha256 <hex>
resona-admin worker enroll mac-studio --organization ecsplico --token-sha256 <hex>
resona-admin worker approve <worker-id> --private --capabilities-file caps.json
```

```bash
A=<app-uuid>
coolify app task create $A --name admin --container $A-hub-1 --frequency "0 0 1 1 *" \
  --command "resona-admin organization create ecsplico"
coolify app task execute $A <task-uuid>
coolify app task executions $A <task-uuid> --format json   # "status": "success"
coolify app task update $A <task-uuid> --command "<next resona-admin command>"   # repeat
coolify app task delete $A <task-uuid>
```

The task command is limited to about 250 characters. `worker approve` needs the capabilities file inside the container: write it to `/tmp` within the same command, for example `python -c "import base64,zlib;open('/tmp/c.json','wb').write(zlib.decompress(base64.b64decode('<b64>')))" && resona-admin worker approve ...`, split over several executions if needed. Check the result from your machine: `GET /v1/settings` with the new client token returns `200`, and a worker heartbeat returns its `approved_capabilities`.

Without `--token-sha256`, `resona-admin` generates the token and prints it once into the task output, so prefer the hash.

## Restore a backup

Back up the database with the SQLite backup API while the hub runs (never by copying the file), for example from the `hub` container:

```bash
python -c "import sqlite3; s=sqlite3.connect('/data/db/jjobs.sqlite'); d=sqlite3.connect('/data/backup.sqlite'); s.backup(d)"
```

Payload directories are transient and are not backed up.

Restore only while the hub is stopped. `resona-admin restore` refuses to run while a hub holds the lock file next to the database:

```bash
coolify app stop <app-uuid>
# on the server: one-off container on the same volume
docker run --rm -v <resona-data volume>:/data <hub image> resona-admin restore /data/backup.sqlite
coolify app start <app-uuid>
```

The restore:

1. copies the backup over the database with the SQLite backup API and migrates it to the current schema;
2. revokes **every** restored credential and worker. A credential that was revoked after the backup was taken therefore cannot come back. Issue fresh credentials and re-enroll the workers afterwards;
3. invalidates every active lease: processing jobs are requeued, or failed when no attempt or time is left;
4. marks jobs whose payload files are gone as `content_unavailable`: pending jobs without input fail with that code, and completed jobs without a result file answer `410 content_unavailable` on the result route. Results are never fabricated;
5. runs one maintenance pass, which reapplies content and metadata retention before the hub serves requests.

It prints counts only, never identifiers of content.

## Logs

The hub and MCP containers log request lines without query strings, plus counts from the maintenance loop. They never log transcripts, Markdown, original filenames or tokens. Reading the container logs through the Coolify API or CLI (`coolify app logs`) needs a Coolify token with the `read:sensitive` ability.

## CPU fallback

The compose file has no CPU fallback worker. The Coolify host has 16 GB RAM and 6 cores, shared with other applications (measured 2026-11-01: 7.7 GB available, 8 GB of swap in use), and the live recognizer already reserves up to 3 CPUs and 3 GB of it ([Live recognizer](#live-recognizer)). A `faster-whisper large-v3` CPU worker would compete with those applications. Add one only after you measure it on this host and an operator approves it explicitly: enroll it as a separate worker, approve only the `cpu-private` candidates, and enable `cpu_fallback_enabled` in the organization settings.
