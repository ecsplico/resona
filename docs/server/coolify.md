# Coolify Deployment (public hub)

The public hub runs on the ecsplico Coolify server from `docker-compose.coolify.yml` (Docker Compose build pack, **Raw Compose Deployment** on). It contains only the hub:

| Service | Image | Listens | Route |
|---------|-------|---------|-------|
| `hub` | `packages/api/Dockerfile` | `:7000` (`expose` only) | `https://resona.ecsplico.de/*` |
| `mcp` | same image, `resona-mcp http` | `:7010` (`expose` only) | `https://resona.ecsplico.de/mcp` |

There are no models, GPUs, Docker socket mounts, privileges or published ports. Both containers run as UID 1000 with a read-only root filesystem, all capabilities dropped and `no-new-privileges`. SQLite and the transient job payloads live on the named volume `resona-data` (`/data`); the database is never exposed. Inference runs on approved pull workers that connect outbound over HTTPS ([Mac worker](mac-worker.md), [Linux CUDA worker](full-stack.md)). When no worker is online, jobs wait in the queue.

TLS ends at Coolify's Caddy proxy. In raw mode Coolify adds no labels or networks, so the compose file declares the Caddy labels and the external `coolify` network itself.

## Image

Coolify builds `packages/api/Dockerfile` from the deployed commit, so the running image is pinned by that commit. When release images are published to GHCR (see [Releases](releases.md)), replace `build:` with `image: ghcr.io/ecsplico/resona-api@sha256:<digest>` from the release manifest.

## Environment

No variable is required; the defaults serve `https://resona.ecsplico.de`.

| Variable | Default | Effect |
|----------|---------|--------|
| `RESONA_PUBLIC_URL` | `https://resona.ecsplico.de` | Caddy site, CORS origin, MCP `Origin` allowlist |
| `RESONA_PUBLIC_HOST` | `resona.ecsplico.de` | MCP `Host` allowlist |
| `RESONA_MAX_UPLOAD_BYTES` | `209715200` | Upload and worker-result cap. The app enforces it while streaming, independent of the proxy |
| `LOGLEVEL` | `info` | Uvicorn log level. Access lines never carry query strings |

`RESONA_BOOTSTRAP_ORGANIZATION_ID` is needed only when an old pre-identity database with job rows is migrated. A fresh volume does not need it.

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
curl https://resona.ecsplico.de/health       # {"status":"ok"}
curl https://resona.ecsplico.de/ready        # 200 {"status":"ready",...,"capacity":"degraded"|"available"}
curl https://resona.ecsplico.de/v1/version   # {"release_id":...,"hub_version":...,"worker_protocol":1}
```

`/ready` turns `503` when the schema is behind, `/data` is not writable or the maintenance loop stalls. Offline workers only change `capacity` to `degraded`.

## Provision credentials

`resona-admin` runs inside the `hub` container (Coolify terminal, a Coolify scheduled task on the `hub` service, or `docker exec` on the server). Generate tokens on your own machine and register only their SHA-256 hash, so the tokens never reach the server or its logs:

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

Without `--token-sha256`, `resona-admin` generates the token and prints it once.

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

The hub and MCP containers log request lines without query strings, plus counts from the maintenance loop. They never log transcripts, Markdown, original filenames or tokens.

## CPU fallback

The compose file has no CPU fallback worker. The Coolify host has 16 GB RAM and 6 cores, shared with other applications (measured 2026-11-01: 7.7 GB available, 8 GB of swap in use). A `faster-whisper large-v3` CPU worker would compete with those applications. Add one only after you measure it on this host and an operator approves it explicitly: enroll it as a separate worker, approve only the `cpu-private` candidates, and enable `cpu_fallback_enabled` in the organization settings.
