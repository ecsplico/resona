# resona-api

resona-api is the central gateway service. It accepts audio uploads, manages an async job queue, stores replacements and initial prompts in SQLite, and exposes OpenAI-compatible `/v1/audio/*` routes that dispatch to local engine-servers or cloud providers.

- Default port: **7000**
- Database: SQLite at `$DATA_PATH/resona.db`
- Engine communication: HTTP to `RESONA_ENGINE_URLS` (default `http://localhost:7001`)

## Authentication

When `RESONA_API_KEY` is set, all endpoints require `X-API-Key: <key>` in the request header. If the env var is unset, all requests are accepted without a key.

!!! warning "Auth header"
    Pass the key as `X-API-Key`, not as a Bearer token. The CLI and `ResonaClient` send it automatically.

## Endpoints

### Health

```
GET /health
```

Returns liveness status.

```json
{"status": "ok"}
```

---

### Jobs

#### Submit audio for transcription

```
POST /jobs
Content-Type: multipart/form-data
```

Upload one or more audio files and register them for async transcription. Files are stored under `$DATA_PATH/files/`. Each file becomes one job.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `audio_files` | file(s) | yes | Audio to transcribe (mp3, wav, m4a, ogg, flac, aac, webm) |
| `keep` | bool | no | Keep the audio file after transcription (default: `true`) |
| `translate` | bool | no | Translate to English instead of transcribing (default: `false`) |
| `engine` | string | no | Route this job to a specific engine by name |

Response — array of job objects:

```json
[
  {
    "id": 42,
    "status": "pending",
    "transcript": null,
    "md": null,
    "language": null,
    "engine": "faster-whisper",
    "filename": "a3f8c1d2e5b7.mp3",
    "created_at": "2026-05-17T10:00:00",
    "updated_at": "2026-05-17T10:00:00"
  }
]
```

#### Register an existing file

```
POST /jobs/registerfile
Content-Type: application/json
```

Register a file that is already in the server's `files/` directory (e.g. after a manual copy or for reprocessing).

| Field | Type | Description |
|-------|------|-------------|
| `filename` | string | Filename as stored in `$DATA_PATH/files/` |

Returns a job object. Returns `404` if the file does not exist.

#### Get job

```
GET /job/{id}
```

Returns the job object for `id`. Returns `404` if not found.

#### List all jobs

```
GET /jobs/
```

Returns an array of all job objects.

#### Job object

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Auto-assigned job ID |
| `status` | string | `pending`, `processing`, `completed`, or `failed` |
| `transcript` | string\|null | Raw transcript text (set when completed) |
| `md` | string\|null | Formatted transcript with replacements applied |
| `language` | string\|null | Detected language code (e.g. `"de"`) |
| `engine` | string\|null | Engine that handled the job |
| `filename` | string | Server-side filename |
| `created_at` | string | ISO 8601 timestamp |
| `updated_at` | string | ISO 8601 timestamp |

#### Job state machine

```
PENDING
  └─► PROCESSING
        ├─► COMPLETED
        └─► FAILED
```

The background `TranscribeTask` dequeues `PENDING` jobs, calls the engine, applies the postprocessing pipeline (replacements from DB, optional LLM), and writes the result. Poll `GET /job/{id}` until `status` is `completed` or `failed`, or use `ResonaClient.wait_for_job()`.

---

### Engine catalogue

#### List engines

```
GET /v1/engines
```

Returns every engine the gateway knows about — local engine-servers plus any cloud provider whose API key is set.

Response:

```json
{
  "engines": [
    {
      "name": "faster-whisper",
      "kind": "local",
      "capabilities": ["stt"],
      "private": true,
      "available": true,
      "models": ["large-v3"]
    },
    {
      "name": "deepgram",
      "kind": "cloud",
      "capabilities": ["stt", "tts"],
      "private": false,
      "available": true,
      "models": ["nova-3"]
    }
  ],
  "default": "faster-whisper"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Engine identifier used in `engine` fields |
| `kind` | string | `"local"` (engine-server) or `"cloud"` (provider) |
| `capabilities` | string[] | `"stt"` and/or `"tts"` |
| `private` | bool | `true` for local engines; cloud engines are always `false` |
| `available` | bool | Local: /health reachable. Cloud: API key set |
| `models` | string[] | Known model names for this engine |

Cloud providers (deepgram, openai, elevenlabs) appear automatically when their respective `*_API_KEY` env var is set on the server. No extra configuration needed.

---

### Audio (v1 — synchronous)

These endpoints are synchronous: they call the engine and return the result directly, without creating a job.

#### Transcribe

```
POST /v1/audio/transcriptions
Content-Type: multipart/form-data
```

OpenAI-compatible speech-to-text. Replacements from the DB are applied to the transcript before it is returned.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file` | file | yes | Audio file |
| `model` | string | no | Model override (passed to engine) |
| `language` | string | no | BCP-47 language code (default: `"de"`) |
| `prompt` | string | no | Initial prompt / vocabulary hint |
| `temperature` | float | no | Sampling temperature (engine-dependent) |
| `response_format` | string | no | `"json"` (default), `"text"`, or `"verbose_json"` |
| `engine` | string | no | Engine name, or `"local"` to require a local engine |
| `private` | bool | no | Refuse cloud engines (default: `false`) |

`response_format` responses:

```json
// json (default)
{"text": "Das ist ein Test."}

// text
Das ist ein Test.

// verbose_json
{
  "text": "Das ist ein Test.",
  "language": "de",
  "duration": 3.2,
  "segments": [{"start": 0.0, "end": 3.2, "text": "Das ist ein Test."}]
}
```

#### Synthesise speech

```
POST /v1/audio/speech
Content-Type: application/json
```

OpenAI-compatible text-to-speech. Only cloud engines support TTS; requesting a local-only engine returns `400`.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `input` | string | yes | Text to synthesise |
| `model` | string | no | Provider model override |
| `voice` | string | no | Voice identifier |
| `response_format` | string | no | `"mp3"` (default), `"opus"`, `"aac"`, `"flac"`, `"wav"` |
| `speed` | float | no | Playback speed multiplier (provider-dependent) |
| `engine` | string | no | Engine name (`"openai"`, `"elevenlabs"`, `"deepgram"`) |
| `private` | bool | no | Always `false` for TTS (cloud only) |

Returns raw audio bytes with the appropriate `Content-Type` header.

---

### Replacements

Text replacement rules are regex patterns applied case-insensitively to every transcript (both the async job path and `/v1/audio/transcriptions`). They are stored in the DB and can be managed at runtime without restarting the server.

#### List replacements

```
GET /replacements/
```

Returns an array of replacement objects.

```json
[
  {"id": 1, "name": "Komma", "replacement": ",", "active": true},
  {"id": 2, "name": "Punkt", "replacement": ".", "active": true}
]
```

#### Add replacement

```
POST /replacements/
Content-Type: application/json
```

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Regex pattern (case-insensitive) |
| `replacement` | string | Substitution string |

Returns `409` if the pattern already exists. Returns the created replacement object.

#### Delete replacement

```
DELETE /replacements/{id}
```

Returns `{"ok": true}`. Returns `404` if not found.

---

### Initial prompts

Vocabulary hints sent to the engine as `initial_prompt`. Only one prompt can be active at a time; activating a prompt deactivates all others.

#### List prompts

```
GET /prompts/
```

Returns an array of prompt objects:

```json
[
  {"id": 1, "phrase": "Befund, Diagnose, Medikation", "active": true}
]
```

#### Add prompt

```
POST /prompts/
Content-Type: application/json
```

| Field | Type | Description |
|-------|------|-------------|
| `phrase` | string | Vocabulary hint text |

Returns `409` if the phrase already exists.

#### Activate prompt

```
PUT /prompts/{id}/activate
```

Sets the prompt active, deactivating all others. Returns `{"ok": true}`.

#### Deactivate prompt

```
PUT /prompts/{id}/deactivate
```

Sets the prompt inactive without activating another. Returns `{"ok": true}`.

#### Delete prompt

```
DELETE /prompts/{id}
```

Returns `{"ok": true}`. Returns `404` if not found.

---

## Error responses

| Status | Meaning |
|--------|---------|
| 400 | Bad request (unknown engine, capability not supported, bad input) |
| 404 | Resource not found |
| 409 | Conflict (duplicate replacement or prompt; no engine available) |
| 415 | Unsupported media type |
| 503 | Engine unavailable; or cloud API key not set |
| 502 | Cloud provider returned an error |

Error body:

```json
{"detail": "human-readable message"}
```
