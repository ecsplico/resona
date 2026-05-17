# resona-cloud-tts

`resona-cloud-tts` provides a uniform interface for calling third-party text-to-speech APIs. Each provider module exposes a `synthesize()` function with a consistent signature and return type. The registry resolves provider names to their modules and tracks default models, voices, and content types.

resona-api uses this package for the `POST /v1/audio/speech` route. You can also call it directly from Python.

## Providers

| Provider | Env var | Default model | Default voice | Output |
|----------|---------|--------------|--------------|--------|
| `openai` | `OPENAI_API_KEY` | `tts-1` | `alloy` | mp3 |
| `elevenlabs` | `ELEVENLABS_API_KEY` | `eleven_multilingual_v2` | Rachel (`21m00Tcm4TlvDq8ikWAM`) | mp3 |
| `deepgram` | `DEEPGRAM_API_KEY` | `aura-2-thalia-en` | encoded in model | mp3 |

!!! note "Deepgram voice selection"
    For Deepgram, the voice is encoded in the model name (e.g. `aura-2-thalia-en`). Pass the desired voice model as the `model` parameter; the `voice` parameter is ignored.

Cloud TTS engines appear in `GET /v1/engines` with `"tts"` in `capabilities` when their API key env var is set.

## Supported output formats

| Format | MIME type |
|--------|-----------|
| `mp3` | `audio/mpeg` |
| `opus` | `audio/opus` |
| `aac` | `audio/aac` |
| `flac` | `audio/flac` |
| `wav` | `audio/wav` |

Not all providers support all formats. Check provider documentation for format support.

## Direct usage

```python
from resona_cloud_tts.registry import get_provider
import os

provider = get_provider("openai")

result = provider.synthesize(
    "Hallo, wie geht es Ihnen?",
    api_key=os.environ["OPENAI_API_KEY"],
    model="tts-1",
    voice="alloy",
    response_format="mp3",
    options=None,
)

with open("speech.mp3", "wb") as f:
    f.write(result["audio"])

print(result["content_type"])   # "audio/mpeg"
```

## SpeechResult

Every provider `synthesize()` function returns a `SpeechResult` TypedDict:

| Field | Type | Description |
|-------|------|-------------|
| `audio` | bytes | Raw audio in the requested format |
| `content_type` | str | MIME type (e.g. `"audio/mpeg"`) |

## Error hierarchy

```
Exception
└─ CloudTTSError          # base — catch this to handle all cloud-tts errors
   ├─ MissingAPIKeyError  # API key env var not set
   └─ ProviderHTTPError   # non-2xx response from the provider
```

```python
from resona_cloud_tts.errors import CloudTTSError, MissingAPIKeyError, ProviderHTTPError

try:
    result = provider.synthesize(text, api_key=key, ...)
except MissingAPIKeyError as e:
    print(f"Set {e.env_var} in your environment")
except ProviderHTTPError as e:
    print(f"{e.provider} returned HTTP {e.status_code}: {e.body}")
except CloudTTSError as e:
    print(f"Cloud TTS error: {e}")
```

When called through resona-api, `MissingAPIKeyError` maps to HTTP 503 and `ProviderHTTPError` maps to HTTP 502.

---

## Registry

::: resona_cloud_tts.registry

---

## Errors

::: resona_cloud_tts.errors
