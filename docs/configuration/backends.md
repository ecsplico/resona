# Backends & SSH

`resona-client` (and all tools built on it) supports multiple backend servers with automatic fallback, SSH tunnelling, and Docker auto-start.

## Configuration file

Backends are stored in `~/.resona/config.json`:

```json
{
  "backends": [
    {
      "name": "local",
      "api_url": "http://localhost:7000",
      "api_key": "",
      "compose_dir": "/home/user/resona",
      "ssh_host": null,
      "ssh_remote_port": null
    }
  ]
}
```

Backends are tried in priority order (top to bottom). The first reachable one is used.

Manage with `resona backends`:

```bash
resona backends list          # show all + reachability
resona backends add ...       # add new entry
resona backends remove <name> # delete entry
resona backends test [name]   # probe health endpoint
```

## Resolution order

When `ResonaClient.from_config()` is called:

1. **`RESONA_API_URL` env var** — if set, used directly; config file is ignored.
2. **Immediate reachability check** — each backend is probed via `GET /health` with a 3 s timeout. First success wins.
3. **Auto-start** (if `auto_start=True`, the default) — for each backend that was unreachable:
   - **SSH backend** — opens a port-forward tunnel and waits up to 30 s.
   - **Compose backend** — runs `docker compose up -d` and waits up to 120 s.

## Backend types

### Direct / LAN

Simple URL + optional API key. No auto-start.

```bash
resona backends add lan http://192.168.1.10:7000 --key mysecret
```

### Docker Compose auto-start

Set `--compose-dir` to the project root. When the backend is unreachable, resona-client runs `docker compose up -d` in that directory and waits for the service to become healthy.

```bash
resona backends add local http://localhost:7000 \
  --compose-dir ~/resona
```

### SSH tunnel

Set `--ssh` to open a local port-forward before connecting. Requires key-based SSH auth (no password prompt).

```bash
# Remote server on myserver.com:7000, tunnel to localhost:7000
resona backends add remote http://localhost:7000 \
  --ssh user@myserver.com

# Non-standard SSH port
resona backends add remote http://localhost:7000 \
  --ssh user@myserver.com:2222

# Different remote port — tunnel local:17000 → remote:7000
resona backends add remote http://localhost:17000 \
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

List order matters. Put the preferred backend first:

```bash
# Prefer the fast LAN server; fall back to SSH tunnel if LAN is down
resona backends add lan    http://192.168.1.10:7000
resona backends add remote http://localhost:7000 --ssh user@myserver.com
```

If both are unreachable, auto-start is attempted for the SSH backend first (first in list order among startable backends).

## Using in Python

```python
from resona_client.client import ResonaClient

# Auto-resolves backend, starts tunnel or compose if needed
client = ResonaClient.from_config()

# Skip auto-start
client = ResonaClient.from_config(auto_start=False)
```

See the [Client Library reference](../reference/client.md) for the full API.
