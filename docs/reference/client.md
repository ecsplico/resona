# Client Library

The `resona-client` package provides the Python API for interacting with a resona-api server.

## Installation

`resona-client` is a workspace package. In production, install from the monorepo:

```bash
uv add resona-client --package my-project
```

## Quick example

```python
from resona_client.client import ResonaClient

# Connect using env vars RESONA_API_URL / RESONA_API_KEY
client = ResonaClient()

# Or auto-resolve from ~/.resona/config.json
client = ResonaClient.from_config()

job = client.submit_job("recording.wav")
result = client.wait_for_job(job["id"])
print(result["md"])  # transcript with replacements applied
```

---

## ResonaClient

::: resona_client.client.ResonaClient

---

## Engine configuration

::: resona_client.config.EngineEntry

::: resona_client.config.EngineConfig

::: resona_client.config.resolve_engine

::: resona_client.config.is_reachable

---

## Legacy: WhisperClient (ws-client)

The `ws-client` package and its `WhisperClient` class are retained for backward compatibility. New code should use `ResonaClient` from `resona-client`.

```python
# Legacy — still works, but deprecated
from ws_client.client import WhisperClient
client = WhisperClient()  # reads WS_API_URL / WS_API_KEY
```
