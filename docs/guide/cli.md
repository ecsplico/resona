# CLI Reference

The `resona` command is the main entry point for all end-user operations. It covers transcription,
document conversion, directory watching, hub jobs and settings, TUI-based recording, live
transcription, speech synthesis, and management of hub entries and postprocessing profiles.

Run `resona --help` or `resona <subcommand> --help` to see up-to-date flag descriptions at any
time.

---

## transcribe

Upload one or more audio files to the hub as audio jobs and wait for the results.

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
| `--recursive` / `-r` | off | Recurse into subdirectories; enables `**` in glob patterns |
| `--output-dir DIR` | (input file directory) | Directory for `<stem>.md` and `<stem>.result.json` |
| `--force` | off | Overwrite existing outputs (otherwise they are never overwritten) |
| `--json` | off | Print a parseable JSON report on stdout |
| `--no-wait` | off | Submit and print job IDs; do not wait |
| `--timeout SECS` | (none) | Seconds to wait per job; the job keeps running on the hub afterwards |
| `--private` | on | Require private processing (the default) |
| `--allow-cloud` | off | Allow cloud stages; mutually exclusive with `--private` and still requires the organization setting `cloud_enabled=true` |
| `--execution-profile NAME` | (organization default) | Execution profile of approved engine candidates: `private-auto`, `mac-private`, `linux-cuda`, `cpu-private` |
| `--language LANG` | (organization setting) | Language hint (ISO 639-1 code) |
| `--engine NAME` | (profile) | Engine override; must match an approved candidate of the execution profile |
| `--model NAME` | (profile) | Model override; must match an approved candidate of the execution profile |
| `--profile NAME` | (none) | Stored hub postprocess profile name; a JSON file or inline JSON only with `--local` |
| `--translate` | off | Translate to English |
| `--word-timestamps` | off | Request word timestamps |
| `--local` | off | Process on this machine with cached hub settings instead of the hub |

**Outputs**

Each input produces two files, written atomically:

- `<stem>.md` — Markdown with YAML frontmatter (`source`, `kind`, `job_id`, `language`, `engine`,
  `model`, `accelerator`, `execution_profile`, `private`, `worker`, `settings_revision`, `created_at`).
- `<stem>.result.json` — the job result (`schema_version: 1`: `kind`, `raw_text`, `markdown`,
  `language`, `segments`, `pages`, `warnings`, `structured`, `provenance`).

For every job one policy summary line goes to stderr, e.g.
`job 12: private=true execution_profile=cpu-private candidate=faster-whisper/large-v3@cpu status=completed`.

**Exit codes:** `0` success, `1` error or timeout, `2` `policy_denied`.

**Examples**

```bash
# Transcribe a single file as a private hub job
resona transcribe dictation.mp3

# Transcribe a directory, writing output to a separate folder
resona transcribe ./recordings/ --output-dir ./transcripts/

# Pick an execution profile and an approved engine/model candidate
resona transcribe meeting.mp3 --execution-profile mac-private --engine mlx-whisper

# Submit only; check later with `resona jobs`
resona transcribe a.mp3 b.mp3 --no-wait

# Apply a stored postprocessing profile
resona transcribe dictation.mp3 --profile medical-de

# Allow cloud stages (organization must have cloud_enabled=true)
resona transcribe interview.mp3 --allow-cloud --language en

# Explicit local run with cached hub settings and an inline profile
resona transcribe dictation.mp3 --local --profile '{"name":"x","steps":[{"type":"replacements","rules":[{"pattern":"Komma","replacement":","}]}]}'
```

!!! note "No automatic fallback"
    The hub is configured with `RESONA_API_URL` or a hub entry in `~/.resona/config.json`. An
    unreachable hub, a timeout or a failed job is reported as an error and **never** replaced by
    a local or cloud run; a policy denial prints `policy_denied: ...` and exits with code 2.
    An `--engine`/`--model` that is not an approved candidate is rejected with `unapproved_override`
    (exit 1). Cloud processing only runs on hub-approved non-private workers, never directly from
    the CLI; provider API keys in the environment never enable cloud.

!!! note "`--local`"
    `--local` is explicit only, never automatic. It uses the settings cached by
    `resona settings pull` for the configured hub and credential (and marks the cache
    `stale: true`), or built-in private defaults when nothing is cached. The same policy check as
    on the hub runs before processing; the candidate runs in the worker's execution subprocess
    (`resona-worker` ships with `resona-cli`) using offline model caches.
    See [Local-Only Mode](../getting-started/local-only.md).

!!! tip "Glob quoting"
    Always quote glob patterns to prevent shell expansion: `resona transcribe "inbox/*.mp3"`.

---

## doc

Convert PDFs and images (`pdf`, `png`, `jpg`, `tif`) to Markdown as hub document jobs.
Inputs, outputs, exit codes and the shared flags (`--recursive`, `--output-dir`, `--force`,
`--json`, `--no-wait`, `--timeout`, `--private`, `--allow-cloud`, `--execution-profile`,
`--engine`, `--model`, `--profile`, `--local`) work as for [`transcribe`](#transcribe).

```
resona doc [INPUTS...] [OPTIONS]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--ocr MODE` | `auto` | `auto` (OCR bitmap regions only), `force` (full-page OCR) or `off` |
| `--ocr-engine NAME` | (profile) | `ocrmac`, `rapidocr` or `tesseract`; must be available in the execution profile |
| `--languages LIST` | (organization setting) | Comma-separated document languages: `de,en` |
| `--pages RANGE` | (all) | Inclusive 1-based page range: `a-b`, `a-`, `-b` or a single page |

```bash
resona doc scan.pdf --pages 1-3 --languages de,en
resona doc ./faxes/ --ocr force --ocr-engine tesseract
```

---

## watch

Watch a directory and submit each new audio file as a private hub job (or process it with
`--local`). Writes the same two outputs as `transcribe` and skips files whose outputs already
exist. Hub errors are reported per file and never replaced by a local run; a policy denial stops
watching with exit code 2.

```
resona watch DIRECTORY [OPTIONS]
```

**Options**

| Flag | Default | Description |
|------|---------|-------------|
| `--recursive` / `-r` | off | Watch subdirectories too |
| `--poll-interval SECS` | `1.0` | Seconds between directory scans |
| `--output-dir DIR` | (input file directory) | Directory for `<stem>.md` and `<stem>.result.json` |
| `--force` | off | Re-transcribe files whose outputs already exist |
| `--execution-profile NAME` | (organization default) | Execution profile (approved engine candidates) |
| `--language LANG` | (organization setting) | Language hint |
| `--engine NAME` / `--model NAME` | (profile) | Override; must match an approved candidate |
| `--profile NAME` | (none) | Stored postprocess profile name (file/inline JSON only with `--local`) |
| `--local` | off | Process on this machine with cached hub settings |

**Examples**

```bash
# Watch a directory, submitting new files to the hub
resona watch ./inbox/

# Watch recursively, poll every 5 seconds
resona watch ./inbox/ --recursive --poll-interval 5 --output-dir ./done/
```

---

## jobs

Inspect and manage hub jobs.

```bash
resona jobs get 12                      # job state as JSON
resona jobs wait 12 --timeout 300       # exit 0 completed, 2 policy denial, 1 otherwise
resona jobs result 12                   # print Markdown (--json for the full result)
resona jobs result 12 --output-dir out/ --stem interview   # write <stem>.md + .result.json
resona jobs cancel 12                   # fence the worker's lease
resona jobs delete 12                   # delete a terminal job's input, result and metadata
resona jobs list --limit 20 --cursor <cursor> --json
```

---

## settings

Read and change the organization's revisioned settings on the hub (`GET /v1/settings`).

```bash
resona settings get                     # print current revision as JSON, refresh the cache
resona settings pull                    # cache the current revision for --local runs
resona settings set language=en default_execution_profile=cpu-private
resona settings set --file settings.json
```

Settings are cached without secrets per hub URL and `organization_id` in
`~/.resona/settings-cache/<hash>.json` (`hub_url`, `organization_id`, `credential` — a SHA-256
prefix of the token, never the token —, `revision`, `etag`, `settings`, `fetched_at`, `stale`).

`set` needs the `org_admin` role, validates values against `OrganizationSettings` and sends
`If-Match` with the cached revision. A `409 revision_conflict` exits 1 with
"run `resona settings pull` and retry". Changes apply to new jobs only; existing jobs keep their
settings snapshot.

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

Launch the Resona Live TUI — the primary way to dictate. Each open document defaults to
**push-to-talk**: tap `F8` to record a segment, tap again to stop, and the transcript lands
back in the buffer once ready (at the cursor position from when you started recording, or at
the current cursor if you've since moved/typed elsewhere). Flip a document's **Live switch**
(`ctrl+l`, or click it) to switch that document to continuous streaming transcription instead —
confirmed text is spliced into the same buffer as you speak. Documents autosave as markdown
after every insertion, on `ctrl+s`, and on quit.

```
resona live                # prompts for a filename, then opens it
resona live notes.md       # opens (or creates) notes.md directly
resona live --engine mlx-whisper --language en
resona live --remote http://api-host:7000 --engine deepgram   # cloud streaming (Live-only)
```

| Option | Description |
|--------|-------------|
| `FILE` (optional) | Markdown document to open. Prompted for if omitted. |
| `--language, -l` | Transcription language (default `de`). |
| `--engine, -e` | Local engine to run in-process (default: platform best). |
| `--remote, -r` | Stream to a remote server instead of running locally. Forces every document into Live mode — push-to-talk is local-only, same as recording audio needs a local mic either way. |
| `--debug` | Show a Logs tab capturing internal log output (useful for diagnosing engine/transcription issues). |

**Keybindings:** `F8` record/stop · `ctrl+l` toggle Live mode for the active document ·
`ctrl+n` new document (prompts for a filename) · `ctrl+w` close the active tab ·
`ctrl+pagedown`/`ctrl+pageup` switch tabs · `ctrl+s` save · `ctrl+q` quit.

Each document's audio segments and a `manifest.json` (transcript, status, insertion point per
segment) are saved alongside it in a `<name>.dictation/` directory.

!!! note "Reuses an already-running local engine"
    Before loading its own in-process model, `live` checks `http://localhost:7720/health`
    (override the port with `RESONA_LOCAL_ENGINE_PORT`). If a `resona-engine-*` server is
    already running there, push-to-talk and Live mode both use it over HTTP/WebSocket instead
    of loading a redundant copy of the model.

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

Manage built-in local engines and hub (resona-api) entries in `~/.resona/config.json`.

### engines list

List all engines — built-in local engines plus configured hub entries from `~/.resona/config.json`.

```bash
resona engines list
```

Output columns: `NAME`, `TYPE`, `PRIVATE`, `STATUS`.

### engines add

Add a hub (resona-api server) entry to `~/.resona/config.json`.

```bash
resona engines add NAME API_URL [OPTIONS]
```

| Flag | Description |
|------|-------------|
| `--key KEY` | API key for server authentication |
| `--compose-dir DIR` | Docker Compose project directory — enables auto-start |
| `--ssh HOST` | SSH host to tunnel through |
| `--ssh-remote-port PORT` | Remote port on the SSH host |

**Examples**

```bash
# Add a remote LAN server
resona engines add gpu-server http://gpu-machine:7001

# Add a server reachable over SSH tunnel
resona engines add home http://localhost:7001 --ssh user@homeserver.example.com

# Add a Docker Compose stack with auto-start
resona engines add local http://localhost:7000 --compose-dir ~/resona
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
