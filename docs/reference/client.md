# Client Library

The `ws-client` package provides the Python API for interacting with a ws-api server.

## Installation

`ws-client` is a workspace package. In production, install from the monorepo:

```bash
uv add ws-client --package my-project
```

## Quick example

```python
from ws_client.client import WhisperClient

# Connect using env vars WS_API_URL / WS_API_KEY
client = WhisperClient()

# Or auto-resolve from ~/.whisper-server/config.json
client = WhisperClient.from_config()

job = client.submit_job("recording.wav")
result = client.wait_for_job(job["id"])
print(result["md"])  # transcript with replacements applied
```

---

## WhisperClient

::: ws_client.client.WhisperClient

---

## Backend configuration

::: ws_client.config.BackendEntry

::: ws_client.config.BackendConfig

::: ws_client.config.resolve_backend

::: ws_client.config.is_reachable
