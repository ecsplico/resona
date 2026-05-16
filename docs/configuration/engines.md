# Engines & SSH

`resona-client` (and all tools built on it) supports multiple engine servers with automatic fallback, SSH tunnelling, and Docker auto-start. Engines come in three types: **built-in local** engines, **resona-api server** entries, and **cloud** provider entries.

## Configuration file

Engines are stored in `~/.resona/config.json`:

```json
{
  "default_engine": "local",
  "default_private": false,
  "engines": [
    {
      "name": "local",
      "type": "resona-api",
      "api_url": "http://localhost:7000",
      "api_key": "",
      "compose_dir": "/home/user/resona",
      "ssh_host": null,
      "ssh_remote_port": null,
      "private": false
    },
    {
      "name": "deepgram",
      "type": "cloud",
      "provider": "deepgram",
      "model": "nova-3",
      "options": {}
    }
  ]
}
```

Engines are tried in priority order (top to bottom). The first reachable one is used.

## Private engines

A "private" engine is one that runs under your own control — local hardware or
your own infrastructure — so audio never leaves it for a third party.

- **Built-in local engines** (`faster-whisper`, `whisper`, `voxtral`) are always private.
- **resona-api server entries** are private only when marked `"private": true`.
- **Cloud engines** are never private.

Set `"default_private": true` at the top level to make `resona transcribe` refuse
non-private engines by default — equivalent to always passing `--private`. The
`--private` / `--no-private` CLI flags override it per run.

## Cloud engines

A cloud engine entry has `"type": "cloud"`, a `provider` (`deepgram`,
`elevenlabs`, or `openai`), an optional `model`, and an optional `options` object
passed through to the provider. API keys are **never stored in `config.json`** —
they are read from environment variables (`DEEPGRAM_API_KEY`,
`ELEVENLABS_API_KEY`, `OPENAI_API_KEY`) at call time.

```bash
resona engines add deepgram --type cloud --provider deepgram --model nova-3
```

See [Environment Variables](environment.md) for the provider key variables.

!!! note "Backward compatibility"
    The `type` field defaults to `resona-api` when omitted, so pre-existing
    config files keep working unchanged.

!!! note "Backward compatibility"
    The legacy `backends` key (and `default_backend` at the top level) are still read for backward compatibility. New config files should use `engines` and `default_engine` instead.

Manage with `resona engines`:

```bash
resona engines list          # show all + reachability
resona engines add ...       # add new entry
resona engines remove <name> # delete entry
resona engines test [name]   # probe health endpoint
```

## Resolution order

When `ResonaClient.from_config()` is called:

1. **`RESONA_API_URL` env var** — if set, used directly; config file is ignored.
2. **Immediate reachability check** — each engine is probed via `GET /health` with a 3 s timeout. First success wins.
3. **Auto-start** (if `auto_start=True`, the default) — for each engine that was unreachable:
   - **SSH engine** — opens a port-forward tunnel and waits up to 30 s.
   - **Compose engine** — runs `docker compose up -d` and waits up to 120 s.

## Engine types

### Direct / LAN

Simple URL + optional API key. No auto-start.

```bash
resona engines add lan http://192.168.1.10:7000 --key mysecret
```

### Docker Compose auto-start

Set `--compose-dir` to the project root. When the engine is unreachable, resona-client runs `docker compose up -d` in that directory and waits for the service to become healthy.

```bash
resona engines add local http://localhost:7000 \
  --compose-dir ~/resona
```

### SSH tunnel

Set `--ssh` to open a local port-forward before connecting. Requires key-based SSH auth (no password prompt).

```bash
# Remote server on myserver.com:7000, tunnel to localhost:7000
resona engines add remote http://localhost:7000 \
  --ssh user@myserver.com

# Non-standard SSH port
resona engines add remote http://localhost:7000 \
  --ssh user@myserver.com:2222

# Different remote port — tunnel local:17000 → remote:7000
resona engines add remote http://localhost:17000 \
  --ssh user@myserver.com \
  --ssh-remote-port 7000
```

The SSH command used is:

```
ssh -N \
  -o ExitOnForwardFailure=yes \
  -o BatchMode=yes \
  -o StrictHostKeyChecking=accept-new \
  -L <local_port>:localhost:<remote_port> \
  [user@]host
```

SSH tunnels started this way are tracked and killed automatically when the process exits.

!!! note "SSH prerequisites"
    SSH key-based authentication must be configured (i.e. `ssh user@host` must work without a password prompt). The first-connect host key is automatically accepted (`StrictHostKeyChecking=accept-new`); subsequent changes are rejected.

## Priority ordering

List order matters. Put the preferred engine first:

```bash
# Prefer the fast LAN server; fall back to SSH tunnel if LAN is down
resona engines add lan    http://192.168.1.10:7000
resona engines add remote http://localhost:7000 --ssh user@myserver.com
```

If both are unreachable, auto-start is attempted for the SSH engine first (first in list order among startable engines).

## Using in Python

```python
from resona_client.client import ResonaClient

# Auto-resolves engine, starts tunnel or compose if needed
client = ResonaClient.from_config()

# Skip auto-start
client = ResonaClient.from_config(auto_start=False)
```

See the [Client Library reference](../reference/client.md) for the full API.
