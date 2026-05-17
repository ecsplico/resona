# Config Files

Resona reads optional config files from `~/.resona/`. None are required — sensible defaults apply
when they are absent.

```
~/.resona/
├── config.json          # engines, auto-start settings, default_engine, default_profile, default_private
└── profiles/            # postprocessing profiles (JSON files)
    ├── default.json     # optional: override the bundled default profile
    └── my-profile.json  # any named profile
```

## config.json

Controls engine selection and the default profile for the `resona` CLI. Defines which resona-api
servers and cloud providers are available, which one is the default, and whether private-only mode
is active.

### Annotated example

```json
{
  "default_engine": "faster-whisper",
  "default_profile": "medical-de",
  "default_private": false,
  "engines": [
    {
      "name": "gpu-server",
      "type": "resona-api",
      "url": "http://gpu-machine:7000",
      "private": true
    },
    {
      "name": "deepgram",
      "type": "cloud",
      "provider": "deepgram"
    },
    {
      "name": "deepgram-nova",
      "type": "cloud",
      "provider": "deepgram",
      "model": "nova-2"
    }
  ]
}
```

### Top-level fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `default_engine` | string | `"faster-whisper"` | Engine name used when `--engine` is not passed on the CLI |
| `default_profile` | string | `"default"` | Profile name used when `--profile` is not passed; the bundled `default` profile applies if absent |
| `default_private` | boolean | `false` | When `true`, `resona transcribe` refuses non-private engines by default — equivalent to always passing `--private` |
| `engines` | array | `[]` | Ordered list of engine entries |

### Engine entry fields — `type: "resona-api"`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Identifier used with `--engine <name>` |
| `type` | string | yes | Must be `"resona-api"` |
| `url` | string | yes | Base URL of the resona-api server |
| `private` | boolean | no | When `true`, this engine is included when `--private` is required |
| `ssh` | string | no | SSH tunnel target, e.g. `"user@server.example.com"`. The CLI opens the tunnel before connecting. |

### Engine entry fields — `type: "cloud"`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Identifier used with `--engine <name>` |
| `type` | string | yes | Must be `"cloud"` |
| `provider` | string | yes | Cloud provider: `"deepgram"`, `"elevenlabs"`, or `"openai"` |
| `model` | string | no | Override the provider default model |
| `options` | object | no | Extra provider-specific options passed at call time |

!!! warning "API keys are never stored in config.json"
    Cloud provider API keys are always read from environment variables at call time (`DEEPGRAM_API_KEY`, `ELEVENLABS_API_KEY`, `OPENAI_API_KEY`). Do not put keys in `config.json`.

### Engine resolution order

When `resona transcribe` runs, it resolves the engine in this order:

1. `--engine NAME` CLI flag — matches a built-in local engine name (`faster-whisper`, `whisper`, `voxtral`), a `resona-api` entry, or a `cloud` entry (highest priority)
2. `--private` / `--no-private` — when private is required (via flag or `default_private: true`), non-private and cloud engines are skipped
3. `default_engine` in `config.json`
4. Hardcoded fallback: `"faster-whisper"`

## profiles/

The `~/.resona/profiles/` directory holds postprocessing profile JSON files for local (no-server)
use. Each file is named `<profile-name>.json`.

To add a profile:

```bash
mkdir -p ~/.resona/profiles/
cp my-profile.json ~/.resona/profiles/
resona transcribe dictation.mp3 --profile my-profile
```

When a resona-api server is running, profiles are stored server-side in `RESONA_PROFILES_DIR` and
can be managed with `resona profiles push/pull/list/show/delete`. The CLI passes the profile name
(or inline JSON) to the server with each job submission.

See [Postprocessing Profiles](../guide/postprocessing.md) for the profile file format and a full
example.
