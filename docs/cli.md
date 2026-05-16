# CLI Reference

`resona` is the main command-line tool. Install via `uv sync --all-packages`.

```
resona [OPTIONS] COMMAND [ARGS]...
```

## Commands overview

| Command | Description |
|---------|-------------|
| `watch` | Watch a directory, auto-submit new audio files |
| `transcribe` | Transcribe files, glob patterns, or a directory |
| `rec` | Audio recorder TUI |
| `live` | Live transcription TUI (streams to resona-engine) |
| `ui` | Record-and-transcribe TUI (records, submits job, shows result) |
| `engines` | Manage local, server, and cloud engines |
| `replacements` | Manage text replacement rules |
| `prompts` | Manage initial prompt phrases |

---

## `resona watch`

Watch a directory and auto-submit any new audio files for transcription.

```bash
resona watch <directory> [OPTIONS]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--recursive` / `--no-recursive` | `False` | Watch subdirectories recursively |
| `--poll-interval` | `2.0` | Seconds between directory scans |
| `--extensions` | `.wav .mp3 .m4a .ogg .flac` | Comma-separated file extensions to watch |

**Example:**

```bash
resona watch ./inbox/ --recursive --poll-interval 5
```

---

## `resona transcribe`

Transcribe one or more audio files and wait for results. Accepts individual
files, glob patterns (quoted so the shell doesn't expand them), or directories.

```bash
resona transcribe <INPUTS...> [OPTIONS]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--recursive` / `-r` | `False` | Recurse into directories / use `**` in glob patterns |
| `--output-dir` | _(next to source)_ | Directory to save transcript files |
| `--model` | _(engine default)_ | Model name override (local fallback and cloud engines) |
| `--language` | `de` | Language hint (local fallback and cloud engines; resona-api servers use their own configured language) |
| `--engine-timeout` | `120.0` | Seconds to wait for local engine startup (local fallback only) |
| `--engine` | _(resolved from config)_ | Engine to use: a built-in local engine (`faster-whisper`, `whisper`, `voxtral`), or a `config.json` server/cloud entry |
| `--private` / `--no-private` | _(from `default_private`)_ | Require a private engine — non-private engines are skipped, and a non-private `--engine` is refused |

**Engine selection.** The `--engine` flag accepts a single unified name that may be
a built-in local engine, a configured resona-api server, or a configured cloud
provider. When `--engine` is omitted, the engine is resolved in this order:

1. `--engine NAME` flag
2. Configured engines in priority order (skipping non-private ones when private is required)
3. `default_engine` in `~/.resona/config.json`
4. Built-in default: `faster-whisper`

Local engines are always private. resona-api server entries are private only when
marked `private: true`. Cloud engines are never private.

**Examples:**

```bash
# Single file
resona transcribe recording.mp3

# Multiple explicit files
resona transcribe a.mp3 b.mp3 c.wav

# Quoted glob (expanded by Python — works regardless of shell)
resona transcribe "folder/*.mp3"

# Directory
resona transcribe ./recordings/ --output-dir ./transcripts/

# Local fallback with a specific engine
resona transcribe ./audio/ --engine whisper --language en

# Cloud provider engine (configured via `resona engines add --type cloud`)
resona transcribe ./audio/ --engine deepgram

# Require a private (local / own-infrastructure) engine
resona transcribe ./audio/ --private
```

---

## `resona rec`

Launch the audio recorder TUI. Records to WAV files in `FILE_PATH`.

```bash
resona rec
```

**Keybindings:**

| Key | Action |
|-----|--------|
| `Space` | Toggle record / pause |
| `s` | Save recording |
| `d` | Discard recording |
| `q` | Quit |

---

## `resona live`

Launch the live transcription TUI. Streams 16 kHz audio to resona-engine via `WS /ws/live`.

```bash
resona live
```

Reads `FILE_PATH`, `SAMPLE_RATE`, `CHANNELS` from environment / `.env`.

---

## `resona ui`

Record audio and automatically submit it for transcription. Displays results in tabs as jobs complete.

```bash
resona ui
```

**Keybindings:**

| Key | Action |
|-----|--------|
| `Space` | Toggle record / pause |
| `s` | Save and submit for transcription |
| `d` | Discard recording |
| `w` | Close active result tab |
| `q` | Quit |

---

## `resona engines`

Manage engines stored in `~/.resona/config.json`. An engine is one of three
types: a **built-in local** engine (always available, always private), a
**resona-api server** entry, or a **cloud** provider entry.

### `engines list`

```bash
resona engines list
```

Lists the built-in local engines plus every configured server/cloud entry, each
with its type, whether it counts as private, and a status column. For server
entries the status is reachability; for cloud entries it is whether the
provider's API key is set in the environment.

### `engines add`

```bash
resona engines add <name> [api_url] [OPTIONS]
```

`api_url` is required for `resona-api` engines and ignored for `cloud` engines.
The name must not collide with a built-in engine (`faster-whisper`, `whisper`,
`voxtral`).

**Options:**

| Option | Description |
|--------|-------------|
| `--key`, `-k` | API key (`X-API-Key` header; resona-api engines) |
| `--compose-dir`, `-c` | Docker Compose project dir for auto-start (resona-api engines) |
| `--ssh`, `-s` | SSH host to tunnel through (`user@host[:port]`; resona-api engines) |
| `--ssh-remote-port` | Remote port on SSH host (default: port from `api_url`) |
| `--type` | Engine type: `resona-api` (default) or `cloud` |
| `--provider` | Cloud provider: `deepgram`, `elevenlabs`, or `openai` (cloud engines) |
| `--model` | Provider model override (cloud engines) |
| `--private` | Mark a resona-api engine as private (own infrastructure) |
| `--option` | Provider option `KEY=VALUE`, repeatable (cloud engines) |

**Examples:**

```bash
# Direct LAN server
resona engines add lan http://192.168.1.10:7000

# Local docker-compose auto-start
resona engines add local http://localhost:7000 --compose-dir ~/resona

# Remote server over SSH tunnel
resona engines add remote http://localhost:7000 --ssh user@myserver.com

# Non-standard SSH port, different local forwarding port
resona engines add remote http://localhost:17000 \
  --ssh user@myserver.com:2222 \
  --ssh-remote-port 7000

# Private resona-api server (only used when --private is in effect)
resona engines add clinic http://10.0.0.5:7000 --private

# Cloud provider engine
resona engines add dg --type cloud --provider deepgram --model nova-3

# Cloud provider with extra options
resona engines add el --type cloud --provider elevenlabs \
  --option diarize=true
```

Cloud API keys are **never stored in `config.json`** — they are read from
environment variables (`DEEPGRAM_API_KEY`, `ELEVENLABS_API_KEY`,
`OPENAI_API_KEY`) at call time.

See [Engines & SSH](configuration/engines.md) for the full resolution logic.

### `engines remove`

```bash
resona engines remove <name>
```

### `engines test`

```bash
resona engines test [name] [--timeout 3.0]
```

Test one engine (or all if `name` is omitted). A resona-api engine is probed via
`GET /health`; a cloud engine passes when its provider API key is present in the
environment. Exits 0 if at least one engine checks out.

---

## `resona replacements`

Manage regex-based text replacement rules. Rules are applied post-transcription by resona-api (via `resona-postprocess`). The engine returns raw text; replacements are applied server-side in the API layer.

```bash
resona replacements list
resona replacements add <pattern> <replacement>
resona replacements delete <id>
```

**Example:**

```bash
resona replacements add "Komma" ","
resona replacements add "Punkt" "."
resona replacements list
resona replacements delete 3
```

Patterns are applied case-insensitively in order of creation.

---

## `resona prompts`

Manage Whisper initial prompt phrases. The active prompt biases recognition towards domain vocabulary.

```bash
resona prompts list
resona prompts add <phrase>
resona prompts activate <id>
resona prompts deactivate <id>
resona prompts remove <id>
```

**Example:**

```bash
resona prompts add "Befund, Diagnose, Therapie, Anamnese"
resona prompts activate 1
```

Only one prompt can be active at a time. `activate` deactivates all others automatically.

---

## Engine resolution

When `resona` needs to connect to a resona-api server, it resolves the engine in this order:

1. `RESONA_API_URL` environment variable — used directly, no config lookup
2. First reachable engine in `~/.resona/config.json`
3. Auto-start: SSH tunnel or `docker compose up -d` for the first configured engine

See [Engines & SSH](configuration/engines.md) for details.
