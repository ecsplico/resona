# Config Files

Resona reads optional config files from `~/.resona/`. None are required — sensible defaults apply
when they are absent.

```
~/.resona/
├── config.json          # hub entries, auto-start settings, default_engine, default_profile
├── settings-cache/      # non-secret cached organization settings (resona settings pull)
└── profiles/            # postprocessing profiles (JSON files)
    ├── default.json     # optional: override the bundled default profile
    └── my-profile.json  # any named profile
```

## config.json

Lists the hubs (resona-api servers) the `resona` CLI can use, plus the default local engine for
`resona live` and the default profile.

### Annotated example

```json
{
  "default_engine": "auto",
  "default_profile": "medical-de",
  "engines": [
    {
      "name": "gpu-server",
      "api_url": "http://gpu-machine:7000",
      "api_key": "",
      "ssh_host": "user@server.example.com",
      "ssh_remote_port": 7000
    },
    {
      "name": "local",
      "api_url": "http://localhost:7000",
      "compose_dir": "~/resona"
    }
  ]
}
```

### Top-level fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `default_engine` | string | `"auto"` | Local in-process engine for `resona live`; `auto` picks the platform default |
| `default_profile` | string | — | Default postprocessing profile name |
| `engines` | array | `[]` | Priority-ordered list of hub entries |

### Hub entry fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Identifier of the entry |
| `api_url` | string | yes | Base URL of the resona-api server |
| `api_key` | string | no | API key for the server |
| `compose_dir` | string | no | Docker Compose project directory; enables auto-start |
| `ssh_host` | string | no | SSH tunnel target, `"[user@]hostname[:port]"`. The CLI opens the tunnel before connecting. |
| `ssh_remote_port` | integer | no | Remote port on the SSH host (defaults to the port in `api_url`) |

Entries describe hubs only; legacy cloud provider entries are dropped with a warning on load.
Processing is private by default, and cloud processing is controlled by the organization setting
`cloud_enabled` plus `--allow-cloud`, never by local config or provider API keys.

## settings-cache/

`resona settings get` and `resona settings pull` cache the organization's revisioned settings per
hub URL and `organization_id` in `~/.resona/settings-cache/<hash>.json` (`hub_url`,
`organization_id`, `credential` — a SHA-256 prefix of the token, never the token —, `revision`,
`etag`, `settings`, `fetched_at`, `stale`). `--local` runs use this cache and set `stale: true`.

## profiles/

The `~/.resona/profiles/` directory holds postprocessing profile JSON files for `--local` runs.
Each file is named `<profile-name>.json`.

To add a profile:

```bash
mkdir -p ~/.resona/profiles/
cp my-profile.json ~/.resona/profiles/
resona transcribe dictation.mp3 --local --profile my-profile
```

Hub jobs use profiles stored server-side in `RESONA_PROFILES_DIR`, managed with
`resona profiles push/pull/list/show/delete`. The CLI passes only the stored profile name to the
hub; file paths and inline JSON are accepted only with `--local`.

See [Postprocessing Profiles](../guide/postprocessing.md) for the profile file format and a full
example.
