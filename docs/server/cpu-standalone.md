# CPU Single-Host Stack (Linux, no GPU)

Run the hub, a live-dictation engine and a `cpu-private` worker on one Linux host without a GPU, from one compose file: `docker-compose.cpu-standalone.yml`. Platforms that deploy a compose service from a single path (Dokploy, Portainer) can use it as is; every knob is an environment variable, and there is no `env_file`.

| Service | Role | Port | Default |
|---|---|---|---|
| `api` | resona-api hub, data in the `resona-data` volume at `/data` | 7710 | on |
| `engine-faster-whisper` | live dictation (`/v1/listen`) and direct `/v1/audio/*`, model `small`, int8 | 7720 | on |
| `worker` | outbound-only pull worker, execution profile `cpu-private` (faster-whisper large-v3 + docling on CPU) | none | off (profile `worker`) |

Queued jobs (batch audio, documents) run only on an approved worker. The `worker` service uses host networking because the worker accepts plain `http` only for loopback; it reaches the hub at `http://127.0.0.1:${API_PORT}`.

## 1. Start the hub

```bash
docker compose -f docker-compose.cpu-standalone.yml up -d --build
curl -s http://127.0.0.1:7710/health
```

## 2. Organization and tokens

Tokens are printed once; write them straight to root-only files:

```bash
A="docker compose -f docker-compose.cpu-standalone.yml exec -T api"
install -d -m 0700 /etc/resona
$A resona-admin organization create acme
$A resona-admin credential issue --organization acme --role client    | sed -n 's/^token: //p' > /etc/resona/client.token
$A resona-admin credential issue --organization acme --role org_admin | sed -n 's/^token: //p' > /etc/resona/org_admin.token
$A resona-admin worker enroll cpu-box --organization acme             # prints worker_id and token
```

Write the worker token to `/etc/resona/worker.token` (or set `RESONA_WORKER_TOKEN_FILE`), mode `0600`, owned by UID 1000, the image's `resona` user.

## 3. Prepare and approve the worker

`prepare` downloads the pinned weights into the `worker-models` volume, runs a real CPU job per candidate, and writes the manifest to the `worker-state` volume:

```bash
F="-f docker-compose.cpu-standalone.yml --profile worker"
docker compose $F build worker
docker compose $F run --rm worker prepare --profile cpu-private --out /state/capabilities.json
docker compose $F run --rm worker capabilities /state/capabilities.json > caps-approve.json

docker compose -f docker-compose.cpu-standalone.yml cp caps-approve.json api:/tmp/caps.json
$A resona-admin worker approve <worker_id> --private --capabilities-file /tmp/caps.json
```

Then start it with the profile enabled, e.g. `COMPOSE_PROFILES=worker` in the deployment environment:

```bash
COMPOSE_PROFILES=worker docker compose -f docker-compose.cpu-standalone.yml up -d
```

## 4. Default execution profile

New organizations default to `private-auto`. On a CPU-only host, make `cpu-private` the default with the org_admin token (full document, `If-Match` = current revision):

```bash
H="Authorization: Bearer $(cat /etc/resona/org_admin.token)"
curl -s -H "$H" http://127.0.0.1:7710/v1/settings > settings.json
jq '.settings.default_execution_profile = "cpu-private" | .settings' settings.json > body.json
curl -s -X PUT -H "$H" -H "Content-Type: application/json" \
  -H "If-Match: \"$(jq -r .revision settings.json)\"" --data @body.json http://127.0.0.1:7710/v1/settings
```

## Add the PWA data layer

Directus and the project worker live in `apps/resona-pwa` (`docker-compose.backend.yml`). Point `RESONA_API_URL` at the hub (`http://host.docker.internal:7710` from that compose) and set `RESONA_API_KEY` to the client token. Each PWA device enters a client token under Einstellungen → Verbindung.
