# resona-cloud-stt

`resona-cloud-stt` provides a thin, uniform interface for calling third-party speech-to-text APIs. Each provider module exposes a single `transcribe()` function with a consistent signature and return type. The registry resolves provider names to their modules and maps them to the env var that holds their API key.

resona-api uses this package internally for the `POST /v1/audio/transcriptions` route when a cloud engine is selected. You can also call it directly from Python.

## Providers

| Provider | Env var | Default model | Capabilities |
|----------|---------|--------------|--------------|
| `deepgram` | `DEEPGRAM_API_KEY` | `nova-3` | STT |
| `elevenlabs` | `ELEVENLABS_API_KEY` | `scribe_v1` | STT |
| `openai` | `OPENAI_API_KEY` | `whisper-1` | STT |

Cloud STT engines appear automatically in `GET /v1/engines` when their API key env var is set — no other configuration required.

## Direct usage

```python
from resona_cloud_stt.registry import get_provider, PROVIDER_ENV_KEYS
import os

provider = get_provider("deepgram")

result = provider.transcribe(
    "recording.wav",
    api_key=os.environ["DEEPGRAM_API_KEY"],
    model="nova-3",
    language="de",
    options={},
)

print(result["text"])
print(result["language"])
print(result["segments"])   # list of {start, end, text} dicts
```

## TranscriptionResult

Every provider `transcribe()` function returns a `TranscriptionResult` TypedDict:

| Field | Type | Description |
|-------|------|-------------|
| `text` | str | Full transcript |
| `language` | str | Detected or specified language code |
| `segments` | list[dict] | List of `{"start": float, "end": float, "text": str}` dicts |

## Error hierarchy

```
Exception
└─ CloudSTTError          # base — catch this to handle all cloud-stt errors
   ├─ MissingAPIKeyError  # API key env var not set
   └─ ProviderHTTPError   # non-2xx response from the provider
```

```python
from resona_cloud_stt.errors import CloudSTTError, MissingAPIKeyError, ProviderHTTPError

try:
    result = provider.transcribe(path, api_key=key, ...)
except MissingAPIKeyError as e:
    print(f"Set {e.env_var} in your environment")
except ProviderHTTPError as e:
    print(f"{e.provider} returned HTTP {e.status_code}: {e.body}")
except CloudSTTError as e:
    print(f"Cloud STT error: {e}")
```

When called through resona-api, `MissingAPIKeyError` maps to HTTP 503 and `ProviderHTTPError` maps to HTTP 502.

---

## Registry

::: resona_cloud_stt.registry

---

## Errors

::: resona_cloud_stt.errors
