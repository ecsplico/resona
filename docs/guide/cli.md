# CLI Reference

The `resona` command is the main entry point for all end-user operations. It covers transcription,
directory watching, TUI-based recording, live transcription, speech synthesis, and management of
engines and postprocessing profiles.

Run `resona --help` or `resona <subcommand> --help` to see up-to-date flag descriptions at any
time.

---

## transcribe

Transcribe one or more audio files, applying postprocessing automatically.

```
resona transcribe [INPUTS...] [OPTIONS]
```

**Accepted inputs**

| Form | Example |
|------|---------|
| Single file | `recording.mp3` |
| Multiple files | `a.mp3 b.mp3 c.mp3` |
| Quoted glob | `"recordings/*.mp3"` |
| Directory | `./recordings/` |

Supported formats: `wav`, `webm`, `flac`, `mp3`, `m4a`, `ogg`, `aac`.

**Options**

| Flag | Default | Description |
|------|---------|-------------|
| `--engine NAME` | (auto) | Built-in engine name, `config.json` server entry, or cloud entry |
| `--profile NAME` | (from `config.json`) | Profile name, inline JSON profile, or path to a `.json` profile file |
| `--language LANG` | `de` | Language hint (ISO 639-1 code) |
| `--model NAME` | (engine default) | Model name override forwarded to the engine |
| `--output-dir DIR` | (input file directory) | Write transcripts here instead of next to the source file |
| `--recursive` / `-r` | off | Recurse into subdirectories; enables `**` in glob patterns |
| `--private` / `--no-private` | (from `config.json`) | Require a private (on-device) engine; `--no-private` overrides `default_private` |
| `--engine-timeout SECS` | `120` | Seconds to wait for a local engine subprocess to start |

**Examples**

```bash
# Transcribe a single file — engine and profile selected automatically
resona transcribe dictation.mp3

# Transcribe a directory, writing output to a separate folder
resona transcribe ./recordings/ --output-dir ./transcripts/

# English transcription with a cloud engine
resona transcribe interview.mp3 --engine openai --language en

# Use a specific Whisper model
resona transcribe meeting.mp3 --engine whisper --model medium

# Require a private (local) engine — refuse to use cloud
resona transcribe patient-notes.mp3 --private

# Apply a named postprocessing profile
resona transcribe dictation.mp3 --profile medical-de

# Apply an inline profile (replacements only, no server needed)
resona transcribe dictation.mp3 --profile '{"name":"x","steps":[{"type":"replacements","rules":[{"pattern":"Komma","replacement":","}]}]}'

# Recurse into subdirectories
resona transcribe ./inbox/ --recursive --output-dir ./out/
```

!!! note "Server vs local fallback"
    `resona transcribe` first tries to reach the resona-api gateway at `RESONA_API_URL`
    (default `http://localhost:7000`). If the gateway is not available it falls back to a local
    engine automatically — no flag needed. See [Local-Only Mode](../getting-started/local-only.md).

!!! tip "Glob quoting"
    Always quote glob patterns to prevent shell expansion: `resona transcribe "inbox/*.mp3"`.

---

## watch

Watch a directory for new audio files and submit them to the resona-api job queue automatically.
Falls back to local transcription when the server is not reachable.

```
resona watch DIRECTORY [OPTIONS]
```

**Options**

| Flag | Default | Description |
|------|---------|-------------|
| `--recursive` / `-r` | off | Watch subdirectories too |
| `--poll-interval SECS` | `1.0` | Seconds between directory scans |
| `--profile NAME` | (from `config.json`) | Profile name or inline JSON applied to each job submitted |
| `--output-dir DIR` | (input file directory) | Write transcripts here (local fallback mode only) |
| `--language LANG` | `de` | Language hint (local fallback mode only) |
| `--model NAME` | (engine default) | Whisper model name (local fallback mode only) |
| `--engine NAME` | (from `config.json`) | Engine for local fallback transcription |
| `--engine-timeout SECS` | `120` | Seconds to wait for local engine startup (local fallback) |

**Examples**

```bash
# Watch a directory, submitting new files to the server
resona watch ./inbox/

# Watch recursively, poll every 5 seconds
resona watch ./inbox/ --recursive --poll-interval 5

# Local fallback: transcribe without a running server
resona watch ./inbox/ --output-dir ./done/
```

!!! warning "Server mode vs local fallback"
    In server mode, `--model`, `--language`, and `--engine` are only used if the gateway is
    unreachable. Pass those flags to `resona transcribe` or configure them in the job queue
    if you want them applied server-side.

---

## rec

Launch the audio recorder TUI. Records from the default microphone and saves the audio file.
Does not transcribe — use `resona ui` if you want to record and transcribe in one step.

```
resona rec
```

No options. The TUI shows recording time and allows stopping with a keypress.

!!! note "Dependencies"
    Requires `textual`, `sounddevice`, and `soundfile` — all included in the default install.

---

## live

Launch the live transcription TUI. Audio is captured from the microphone, segmented by
VAD (voice activity detection), and sent to the engine over WebSocket for near-real-time
transcription.

```
resona live
```

No options. Configured via environment variables (`RESONA_API_URL`, `SAMPLE_RATE`, `CHANNELS`).

!!! note "Engine requirement"
    The `live` command connects to a running resona-engine-server WebSocket endpoint
    (`WS /ws/live`). A local engine or a server must be reachable.

---

## ui

Launch the record-and-transcribe TUI. Records audio, submits the file as a job to resona-api,
and displays the result when the job completes.

```
resona ui
```

No options. Requires resona-api to be running at `RESONA_API_URL`.

---

## speech

Synthesise speech from text using a cloud TTS provider via the resona-api gateway.

```
resona speech TEXT [OPTIONS]
```

**Options**

| Flag | Default | Description |
|------|---------|-------------|
| `--engine NAME` | (gateway default) | Engine name forwarded to the gateway |
| `--voice NAME` | `alloy` | Voice name |
| `--model NAME` | `tts-1` | TTS model name |
| `--format EXT` | `mp3` | Output format: `mp3`, `opus`, `aac`, `flac` |
| `--speed FLOAT` | `1.0` | Speech speed (0.25–4.0) |
| `--play` | off | Play audio immediately via `aplay`/`afplay`/`mpv` |
| `--output PATH` | `speech.mp3` | Output file path; use `-` for stdout |
| `--private` | off | Require a private engine |

**Examples**

```bash
# Synthesise and save to speech.mp3
resona speech "Good morning, Dr. Schmidt."

# Use a specific engine and voice
resona speech "Hello world" --engine openai --voice nova

# Play back immediately without saving
resona speech "Appointment at three o'clock." --play

# Save to a specific file
resona speech "Processing complete." --output alert.mp3

# ElevenLabs with Rachel voice
resona speech "Bitte warten Sie." --engine elevenlabs --voice Rachel

# Pipe raw audio to stdout
resona speech "Test" --output -
```

See [Speech Synthesis](tts.md) for full provider and format details.

---

## engines

Manage the engine catalogue: built-in local engines, resona-api server entries, and cloud
provider entries.

### engines list

List all engines — built-in local engines plus configured entries from `~/.resona/config.json`.

```bash
resona engines list
```

Output columns: `NAME`, `TYPE`, `PRIVATE`, `STATUS`.

### engines add

Add a resona-api server entry or a cloud provider entry to `~/.resona/config.json`.

```bash
resona engines add NAME [API_URL] [OPTIONS]
```

| Flag | Description |
|------|-------------|
| `--type TYPE` | `resona-api` (default) or `cloud` |
| `--provider NAME` | Cloud provider: `deepgram`, `elevenlabs`, `openai` (required for `--type cloud`) |
| `--model NAME` | Provider model override |
| `--key KEY` | API key for server authentication |
| `--private` | Mark a resona-api entry as private |
| `--compose-dir DIR` | Docker Compose project directory — enables auto-start |
| `--ssh HOST` | SSH host to tunnel through |
| `--ssh-remote-port PORT` | Remote port on the SSH host |
| `--option KEY=VALUE` | Provider-specific option (repeatable; cloud engines) |

**Examples**

```bash
# Add a remote LAN server
resona engines add gpu-server http://gpu-machine:7001

# Add a server reachable over SSH tunnel
resona engines add home http://localhost:7001 --ssh user@homeserver.example.com

# Add a Docker Compose stack with auto-start
resona engines add local http://localhost:7000 --compose-dir ~/resona --private

# Add cloud STT/TTS providers
resona engines add deepgram --type cloud --provider deepgram
resona engines add elevenlabs --type cloud --provider elevenlabs
resona engines add openai --type cloud --provider openai

# Cloud engine with a specific model
resona engines add deepgram-nova --type cloud --provider deepgram --model nova-2
```

### engines remove

Remove a configured engine entry.

```bash
resona engines remove NAME
```

### engines test

Test reachability of one or all configured engines. Exits with code 0 if at least one engine is
reachable, 1 otherwise.

```bash
resona engines test             # test all configured engines
resona engines test gpu-server  # test a specific entry
resona engines test --timeout 5 # custom timeout in seconds
```

### engines status

Query the live resona-api gateway catalogue and show which engines are currently available,
their capabilities, and their status. Requires a running gateway.

```bash
resona engines status
```

Output columns: `Name`, `Kind`, `Capabilities`, `Available`, `Models`.

---

## profiles

Manage postprocessing profiles stored on the resona-api server. A profile is a JSON file that
bundles an `initial_prompt` list and an ordered pipeline of steps (replacements, LLM formatting,
structured extraction). See [Postprocessing Profiles](postprocessing.md) for the full format.

!!! note "Server required"
    The `profiles` subcommands communicate with resona-api. For local-only use, place profile JSON
    files in `~/.resona/profiles/` and reference them with `--profile NAME`.

### profiles list

List all profiles stored on the server.

```bash
resona profiles list
```

Output columns: `NAME`, `DESCRIPTION`.

### profiles show

Display the full JSON of a profile stored on the server.

```bash
resona profiles show my-profile
```

### profiles push

Upload a local profile JSON file to the server.

```bash
resona profiles push my-profile.json
```

The profile's `name` field determines the server-side name. If a profile with the same name
already exists it is replaced.

### profiles pull

Download a profile from the server and save it to a local file.

```bash
resona profiles pull my-profile               # saves to my-profile.json
resona profiles pull my-profile output.json   # saves to output.json
```

### profiles delete

Delete a profile from the server.

```bash
resona profiles delete my-profile
```
