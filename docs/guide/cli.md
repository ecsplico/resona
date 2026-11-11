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
| `--instructions TEXT` / `--instructions-file PATH` | (none) | User instructions for the profile's LLM steps (user message; the profile's prompt stays the system message). Needs a `--profile` with an `llm`/`extract` step. Also on `resona doc` and `resona postprocess` |
| `--translate` | off | Translate to English |
| `--word-timestamps` | off | Request word timestamps |
| `--diarize` | off | Label who spoke when; see **Speaker diarization** below |
| `--local` | off | Process on this machine with cached hub settings instead of the hub |

**Outputs**

Each input produces two files, written as a pair: both are staged first and a failure leaves
neither new file (a file replaced under `--force` is restored). Inputs whose outputs would collide
(same stem, e.g. `a.wav` and `a.mp3`, or same name from two directories with one `--output-dir`)
stop the run with exit code 2 before any job is submitted.

- `<stem>.md` — Markdown with YAML frontmatter (`source`, `kind`, `job_id`, `language`, `engine`,
  `model`, `accelerator`, `execution_profile`, `private`, `worker`, `settings_revision`, `created_at`).
- `<stem>.result.json` — the job result (`schema_version: 1`: `kind`, `raw_text`, `markdown`,
  `language`, `segments`, `pages`, `warnings`, `structured`, `provenance`).

For every job one policy summary line goes to stderr, e.g.
`job 12: private=true execution_profile=cpu-private candidate=faster-whisper/large-v3@cpu status=completed`.

**Exit codes:** `0` success, `1` error or timeout, `2` `policy_denied` or colliding output names.

**Speaker diarization**

`--diarize` sets `options.diarize` on the job (hub and `--local` alike). The result's `segments`
are then speaker sections, each with a 1-based `speaker` number in order of first appearance, and
the Markdown has one paragraph per section starting with `**Sprecher N:** ` (job language `de`) or
`**Speaker N:** ` (other languages); long sections continue in further paragraphs without a label.
The provenance gains a `diarize` stage. Only a worker whose `prepare` verified the diarizer takes
such a job; until one is online it stays `pending` with `queue_reason: no_capable_worker`. An LLM
polish step with `preserve_markup` keeps the raw chunk when the model drops, merges or renames a
speaker label.

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

# Label speakers in a conversation
resona transcribe interview.m4a --diarize

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

Convert PDFs, images and Word documents (`pdf`, `png`, `jpg`, `tif`, `docx`) to Markdown as hub document jobs.
A DOCX takes no OCR or page flags: `--ocr` other than `off`, `--ocr-engine`, `--pages`, `--image-scale` and
`--ocr-min-confidence` are rejected (hub: `422 invalid_options`); see [Document options](../reference/api.md#document-options).
Inputs, outputs, exit codes and the shared flags (`--recursive`, `--output-dir`, `--force`,
`--json`, `--no-wait`, `--timeout`, `--private`, `--allow-cloud`, `--execution-profile`,
`--engine`, `--model`, `--profile`, `--local`) work as for [`transcribe`](#transcribe).

```
resona doc [INPUTS...] [OPTIONS]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--ocr MODE` | `auto` | `auto` (OCR the layout regions without visible text; a visible, digital text layer is kept, an invisible OCR text layer is not counted as text, so those regions are OCRed again and the new OCR text replaces it), `force` (full-page OCR, any text layer ignored) or `off` (no OCR: the text layer as it is, an invisible one included) |
| `--ocr-engine NAME` | (profile) | `ocrmac`, `rapidocr` or `tesseract`; must be available in the execution profile |
| `--languages LIST` (alias `--language`) | (organization setting) | Comma-separated document languages: `de`, `en`, `fr`, `it`, `es`, `ko` |
| `--pages RANGE` | (all) | Inclusive 1-based page range: `a-b`, `a-`, `-b` or a single page |
| `--image-scale X` | `1.0` | `1.0`–`4.0`: multiplies the resolution OCR reads the page at (`1.0` = 216 dpi, `2.0` = 432 dpi). Requires OCR |
| `--ocr-min-confidence X` | (engine default) | `0`–`1`: drop OCR text below this recognizer confidence. `rapidocr` only (its default is `0.5`) |

Not every OCR engine serves every language or option. A combination no worker can run fails the job
(`invalid_snapshot`); a gap of one host (a missing tesseract pack) hands the job to another worker.
Nothing is silently ignored:

| | `ocrmac` | `tesseract` | `rapidocr` |
|---|---|---|---|
| `de`, `en`, `fr`, `it`, `es` | yes | if the `deu`/`eng`/`fra`/`ita`/`spa` pack is installed | yes, one shared PP-OCRv6 recognizer |
| `ko` | yes | if the `kor` pack is installed | no: Korean weights are not bundled with the pinned rapidocr and jobs never download models |
| `--ocr-min-confidence` | no | no | yes (`text_score`) |

`--image-scale` and `--ocr-min-confidence` require OCR (`--ocr auto` or `force`). Memory and OCR time
grow with the square of the scale.

Measured on the evaluation corpus (gpu-box, `rapidocr`, 2026-11-02; CER = character error rate):

| Fixture | `auto` | `--image-scale 2` | `--ocr force` | `force` + scale 2 |
|---|---|---|---|---|
| small print scan (7 pt, 150 dpi) | 0.196 | 0.391 | **0.000** | 0.202 |
| degraded scan | 0.002 | 0.000 | 0.000 | 0.000 |
| digital two-column page with table | 0.000 | 0.000 | 0.263 | 0.265 |
| digital three-column page | 0.000 | 0.000 | 0.460 | 0.460 |

- For a pure scan read by `rapidocr`, `--ocr force` is the best setting: `auto` OCRs each layout
  region as a separate crop, and the recognizer loses small print at crop edges.
- Never use `force` on documents with a usable text layer: full-page OCR merges neighbouring columns
  and breaks reading order.
- `--image-scale 2` made small print worse with `rapidocr`. Raise it only after checking the result.
- `--ocr-min-confidence` 0.3 and 0.7 changed no score.
- `ocrmac` (Apple Vision, Mac worker) read every fixture of that corpus at CER 0.000, in `auto` too.
- A scan under a garbled invisible text layer (`bad-text-layer`) scored like a plain scan in `auto`
  (0.004 `rapidocr`, 0.000 `ocrmac`): the bad layer does not reach the output.
- French, Italian and Spanish scans (`rapidocr`, Mac CPU, 2026-11-04): `auto` 0.200 / 0.000 / 0.000,
  `force` 0.000 for all three. In `auto`, the French page lost most of its first line.
- Korean (`ko`, `ocrmac` only, Mac, 2026-11-04): a rendered page of two Hangul lines (Apple SD Gothic
  Neo 26 px, light blur) read at CER 0.000 in `auto` and `force`. The corpus has no Korean fixture
  because `rapidocr` ships no Korean weights.

```bash
resona doc scan.pdf --pages 1-3 --languages de,en
resona doc ./faxes/ --ocr force --ocr-engine tesseract
resona doc small-print-scan.pdf --ocr force --ocr-engine rapidocr
```

### Page orientation

With OCR on (`--ocr auto` or `force`), every page in the range is turned upright before conversion,
for every OCR engine. Tesseract's orientation detection (`osd` traineddata) reads each page at
200 dpi (about 0.3 s per page) and names the clockwise quarter turn that makes it upright; a turn
below orientation confidence 2.0, or a page with too little text, leaves the page as it is. PDF
pages are turned through their `/Rotate` entry (text layer untouched), images are rewritten turned.
Each turned page adds the result warning `page_rotated:<page>:<degrees>` (clockwise), and the
bounding boxes of its blocks refer to the upright page. `--ocr off` turns nothing.

Why: Docling runs layout, OCR and reading order on the page as scanned. An upside-down scan read
by `rapidocr` came out with its lines in reverse order and a garbled last line; Apple Vision read the
same simple page correctly. Detection read every page of the evaluation corpus and the engine test
fixtures correctly in all four orientations (Mac, tesseract 5, 2026-11-10).

---

## postprocess

Run a postprocess profile on text without transcribing again: each input becomes a hub `text`
job (see [Text jobs](../reference/api.md#text-jobs)) that runs only the profile. Outputs, exit
codes and the shared flags (`--recursive`, `--output-dir`, `--force`, `--json`, `--no-wait`,
`--timeout`, `--private`, `--allow-cloud`, `--language`, `--local`) work as for
[`transcribe`](#transcribe). There are no `--execution-profile`, `--engine` or `--model` flags.

```
resona postprocess [INPUTS...] --profile NAME [OPTIONS]
resona postprocess --job ID --profile NAME [OPTIONS]
```

| Flag | Default | Description |
|------|---------|-------------|
| `INPUTS` | | `.md`, `.markdown` or `.txt` files, globs or directories (UTF-8, at most 2 MiB each on the hub) |
| `--job ID` | | Instead of files: re-run on the result of an earlier job. Input is its `raw_markdown` (the text before its own postprocessing), else its `markdown`; the language defaults to that job's. Outputs are `job-<id>.<profile>.md` + `job-<id>.<profile>.result.json` in `--output-dir` (default: the current directory), because the hub keeps no source file names; `--json` reports the input as `job:<id>` |
| `--profile NAME` | (required) | Stored postprocess profile name; a file or inline JSON only with `--local` |
| `--instructions TEXT` / `--instructions-file PATH` | (none) | User instructions for the profile's LLM steps, see [`transcribe`](#transcribe) |

Outputs carry the profile in their name and sit next to the input unless `--output-dir` is
given: `resona postprocess notes.md --profile fix` writes `notes.fix.md` +
`notes.fix.result.json`, so the input is never replaced and runs of different profiles sit side
by side. The profile part is the stored profile's name, a profile file's stem, or an inline JSON
profile's `name` (`postprocess` if it has none), reduced to file-name-safe characters. Existing
outputs are kept unless `--force`. `--local` runs the profile in-process with cached hub settings
and the same policy checks; no execution subprocess is started.

```bash
# Re-run a new profile on job 12's transcript
resona postprocess --job 12 --profile transcript-markdown-de   # -> job-12.transcript-markdown-de.md

# Fix dictation commands in a note, next to it -> befund.fix.md
resona postprocess befund.md --profile fix

# Polish exported notes with the local LLM that only the assigned worker reaches
resona postprocess ./notes/*.md --profile document-polish-de --output-dir ./polished/
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
| `--diarize` | off | Label who spoke when, as `resona transcribe --diarize` |

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

## testbench

Contribute recordings and scans to the hub [testbench](testbench.md) and list what it holds.
Correcting references, bench runs and the leaderboard are done in the admin UI (`/admin`), not here.

```bash
resona testbench add memo.m4a scans/ --language de --label "Brief"   # files or directories; prints item id + status
resona testbench list --status needs_correction --kind audio --json   # org admin
resona testbench status                                               # enabled flag and counters
```

The organization must have `testbench_enabled=true` (the admin UI Testbench tab has an Enable button; CLI route in [Testbench](testbench.md#enabling-it)); otherwise `add` exits 1 with `testbench_disabled` and a hint. `add` exits 1 if any file was rejected.

---

## settings

Read and change the organization's revisioned settings on the hub (`GET /v1/settings`).

```bash
resona settings get                     # print current revision as JSON, refresh the cache
resona settings pull                    # cache the current revision for --local runs
resona settings get | jq .settings > settings.json   # edit the complete settings object …
resona settings set --file settings.json              # … and replace it on the hub
```

Settings are cached without secrets per hub URL and `organization_id` in
`~/.resona/settings-cache/<hash>.json` (`hub_url`, `organization_id`, `credential` — a SHA-256
prefix of the token, never the token —, `revision`, `etag`, `settings`, `fetched_at`, `stale`).

`set` takes only `--file` with the complete settings object (no `key=value` edits); it needs the
`org_admin` role, validates the document against `OrganizationSettings` and sends
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
| `--engine, -e` | Local engine to run in-process (default: fastest installed engine; on Apple Silicon `lightning-mlx` → `mlx-whisper` → `whisper-cpp` → `faster-whisper`). |
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
