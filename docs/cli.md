# CLI Reference

`resona` is the main command-line tool. Install via `uv sync --all-packages`.

```
resona [OPTIONS] COMMAND [ARGS]...
```

## Commands overview

| Command | Description |
|---------|-------------|
| `watch` | Watch a directory, auto-submit new audio files |
| `batch` | Transcribe all files in a directory |
| `rec` | Audio recorder TUI |
| `live` | Live transcription TUI (streams to resona-engine) |
| `ui` | Record-and-transcribe TUI (records, submits job, shows result) |
| `backends` | Manage backend server addresses |
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

## `resona batch`

Submit all audio files in a directory and wait for results.

```bash
resona batch <directory> [OPTIONS]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--output-dir` | _(print to stdout)_ | Directory to save transcript files |
| `--translate` | `False` | Request English translation |
| `--timeout` | `3600` | Per-job timeout in seconds |

**Example:**

```bash
resona batch ./recordings/ --output-dir ./transcripts/
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

## `resona backends`

Manage backend server addresses stored in `~/.resona/config.json`.

### `backends list`

```bash
resona backends list
```

Shows all configured backends with reachability status (`✓` / `✗`).

### `backends add`

```bash
resona backends add <name> <api_url> [OPTIONS]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--key`, `-k` | API key (`X-API-Key` header) |
| `--compose-dir`, `-c` | Docker Compose project dir for auto-start |
| `--ssh`, `-s` | SSH host to tunnel through (`user@host[:port]`) |
| `--ssh-remote-port` | Remote port on SSH host (default: port from `api_url`) |

**Examples:**

```bash
# Direct LAN server
resona backends add lan http://192.168.1.10:7000

# Local docker-compose auto-start
resona backends add local http://localhost:7000 --compose-dir ~/resona

# Remote server over SSH tunnel
resona backends add remote http://localhost:7000 --ssh user@myserver.com

# Non-standard SSH port, different local forwarding port
resona backends add remote http://localhost:17000 \
  --ssh user@myserver.com:2222 \
  --ssh-remote-port 7000
```

See [Backends & SSH](configuration/backends.md) for the full resolution logic.

### `backends remove`

```bash
resona backends remove <name>
```

### `backends test`

```bash
resona backends test [name] [--timeout 3.0]
```

Test reachability of one backend (or all if `name` is omitted). Exits 0 if at least one is reachable.

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

## Backend resolution

When `resona` needs to connect to a resona-api server, it resolves the backend in this order:

1. `RESONA_API_URL` environment variable — used directly, no config lookup
2. First reachable backend in `~/.resona/config.json`
3. Auto-start: SSH tunnel or `docker compose up -d` for the first configured backend

See [Backends & SSH](configuration/backends.md) for details.
