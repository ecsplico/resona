# resona-client

`resona-client` is the Python client library for talking to a resona-api server. It handles authentication, job submission, polling, and full CRUD for postprocessing profiles. Engine configuration — which servers to connect to, auto-start rules, and cloud provider registrations — lives in `~/.resona/config.json` and is managed through `EngineConfig` and `EngineEntry`.

## Installation

`resona-client` is a workspace package. Add it from the monorepo:

```bash
uv add resona-client --package my-project
```

## Quick example

```python
from resona_client.client import ResonaClient

# Connect using RESONA_API_URL / RESONA_API_KEY env vars
client = ResonaClient()

# Or auto-resolve from ~/.resona/config.json (tries reachable engines in order)
client = ResonaClient.from_config()

job = client.submit_job("recording.wav")
result = client.wait_for_job(job["id"])
print(result["md"])   # formatted transcript after postprocessing pipeline

# Submit with a named profile
job = client.submit_job("recording.wav", profile="medical-de")

# Submit with an inline profile
import json
profile_json = json.dumps({"name": "x", "steps": [{"type": "replacements", "rules": []}]})
job = client.submit_job("recording.wav", profile=profile_json)
```

## Authentication

The client sends `X-API-Key: <key>` with every request when `api_key` is provided or `RESONA_API_KEY` is set. If the server has auth disabled (no `RESONA_API_KEY` env var on the server), the header is omitted.

```python
client = ResonaClient(base_url="http://myserver:7000", api_key="secret")
```

## Job lifecycle

Jobs progress through the states `PENDING → PROCESSING → COMPLETED | FAILED`. Use `wait_for_job()` to poll until the job reaches a terminal state.

```python
job = client.submit_job("dictation.mp3", engine="deepgram")
result = client.wait_for_job(job["id"])

if result["status"] == "completed":
    print(result["transcript"])   # raw text
    print(result["md"])           # formatted text (replacements applied)
else:
    print("failed:", result)
```

## v1 Audio routes

The v1 routes call engines synchronously through the gateway and return results immediately — no job queue involved.

```python
# Transcribe synchronously
result = client.create_transcription("file.wav", engine="faster-whisper", language="de")
print(result["text"])

# Transcribe with verbose output (segments + duration)
result = client.create_transcription("file.wav", response_format="verbose_json")
print(result["segments"])

# Text-to-speech
audio_bytes = client.create_speech("Hallo Welt", voice="alloy", engine="openai")
with open("out.mp3", "wb") as f:
    f.write(audio_bytes)

# List available engines
engines = client.list_engines()
```

## Profile management

```python
# List all profiles stored on the server
profiles = client.list_profiles()
for p in profiles:
    print(p["name"], p["description"])

# Get the full JSON of a profile
profile = client.get_profile("medical-de")

# Upload a local profile to the server
import json
from pathlib import Path
profile_data = json.loads(Path("my-profile.json").read_text())
client.push_profile(profile_data)

# Download a profile from the server
profile = client.pull_profile("medical-de")
Path("medical-de.json").write_text(json.dumps(profile, ensure_ascii=False, indent=2))

# Delete a profile from the server
client.delete_profile("old-profile")
```

## Engine configuration

`EngineConfig` and `EngineEntry` describe which servers the client knows about. Manage them with `resona engines add/remove/list` from the CLI; or load and modify them programmatically.

```python
from resona_client.config import EngineConfig, EngineEntry, resolve_engine

cfg = EngineConfig.load()
print(cfg.default_engine)       # "faster-whisper"
print([e.name for e in cfg.engines])

# Check which entry would be used right now
entry = resolve_engine()
if entry:
    print(entry.api_url)
```

## Context manager

`ResonaClient` implements the context-manager protocol and closes the underlying httpx connection pool on exit:

```python
with ResonaClient() as client:
    job = client.submit_job("audio.wav")
    result = client.wait_for_job(job["id"])
```

---

## ResonaClient

::: resona_client.client.ResonaClient

---

## EngineConfig

::: resona_client.config.EngineConfig

---

## EngineEntry

::: resona_client.config.EngineEntry

---

## resolve\_engine

::: resona_client.config.resolve_engine

---

## is\_reachable

::: resona_client.config.is_reachable
