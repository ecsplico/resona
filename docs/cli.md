# CLI Reference

`ws-cli` is the main command-line tool. Install via `uv sync --all-packages`.

```
ws-cli [OPTIONS] COMMAND [ARGS]...
```

## Commands overview

| Command | Description |
|---------|-------------|
| `watch` | Watch a directory, auto-submit new audio files |
| `batch` | Transcribe all files in a directory |
| `rec` | Audio recorder TUI |
| `live` | Live transcription TUI (streams to ws-engine) |
| `ui` | Record-and-transcribe TUI (records, submits job, shows result) |
| `backends` | Manage backend server addresses |
| `replacements` | Manage text replacement rules |
| `prompts` | Manage initial prompt phrases |

---

## `ws-cli watch`

Watch a directory and auto-submit any new audio files for transcription.

```bash
ws-cli watch <directory> [OPTIONS]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--recursive` / `--no-recursive` | `False` | Watch subdirectories recursively |
| `--poll-interval` | `2.0` | Seconds between directory scans |
| `--extensions` | `.wav .mp3 .m4a .ogg .flac` | Comma-separated file extensions to watch |

**Example:**

```bash
ws-cli watch ./inbox/ --recursive --poll-interval 5
```

---

## `ws-cli batch`

Submit all audio files in a directory and wait for results.

```bash
ws-cli batch <directory> [OPTIONS]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--output-dir` | _(print to stdout)_ | Directory to save transcript files |
| `--translate` | `False` | Request English translation |
| `--timeout` | `3600` | Per-job timeout in seconds |

**Example:**

```bash
ws-cli batch ./recordings/ --output-dir ./transcripts/
```

---

## `ws-cli rec`

Launch the audio recorder TUI. Records to WAV files in `FILE_PATH`.

```bash
ws-cli rec
```

**Keybindings:**

| Key | Action |
|-----|--------|
| `Space` | Toggle record / pause |
| `s` | Save recording |
| `d` | Discard recording |
| `q` | Quit |

---

## `ws-cli live`

Launch the live transcription TUI. Streams 16 kHz audio to ws-engine via `WS /ws/live`.

```bash
ws-cli live
```

Reads `FILE_PATH`, `SAMPLE_RATE`, `CHANNELS` from environment / `.env`.

---

## `ws-cli ui`

Record audio and automatically submit it for transcription. Displays results in tabs as jobs complete.

```bash
ws-cli ui
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

## `ws-cli backends`

Manage backend server addresses stored in `~/.whisper-server/config.json`.

### `backends list`

```bash
ws-cli backends list
```

Shows all configured backends with reachability status (`✓` / `✗`).

### `backends add`

```bash
ws-cli backends add <name> <api_url> [OPTIONS]
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
ws-cli backends add lan http://192.168.1.10:7000

# Local docker-compose auto-start
ws-cli backends add local http://localhost:7000 --compose-dir ~/whisper-server

# Remote server over SSH tunnel
ws-cli backends add remote http://localhost:7000 --ssh user@myserver.com

# Non-standard SSH port, different local forwarding port
ws-cli backends add remote http://localhost:17000 \
  --ssh user@myserver.com:2222 \
  --ssh-remote-port 7000
```

See [Backends & SSH](configuration/backends.md) for the full resolution logic.

### `backends remove`

```bash
ws-cli backends remove <name>
```

### `backends test`

```bash
ws-cli backends test [name] [--timeout 3.0]
```

Test reachability of one backend (or all if `name` is omitted). Exits 0 if at least one is reachable.

---

## `ws-cli replacements`

Manage regex-based text replacement rules. Rules are applied post-transcription by ws-engine.

```bash
ws-cli replacements list
ws-cli replacements add <pattern> <replacement>
ws-cli replacements delete <id>
```

**Example:**

```bash
ws-cli replacements add "Komma" ","
ws-cli replacements add "Punkt" "."
ws-cli replacements list
ws-cli replacements delete 3
```

Patterns are applied case-insensitively in order of creation.

---

## `ws-cli prompts`

Manage Whisper initial prompt phrases. The active prompt biases recognition towards domain vocabulary.

```bash
ws-cli prompts list
ws-cli prompts add <phrase>
ws-cli prompts activate <id>
ws-cli prompts deactivate <id>
ws-cli prompts remove <id>
```

**Example:**

```bash
ws-cli prompts add "Befund, Diagnose, Therapie, Anamnese"
ws-cli prompts activate 1
```

Only one prompt can be active at a time. `activate` deactivates all others automatically.

---

## Backend resolution

When ws-cli needs to connect to a ws-api server, it resolves the backend in this order:

1. `WS_API_URL` environment variable — used directly, no config lookup
2. First reachable backend in `~/.whisper-server/config.json`
3. Auto-start: SSH tunnel or `docker compose up -d` for the first configured backend

See [Backends & SSH](configuration/backends.md) for details.
