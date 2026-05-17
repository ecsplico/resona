# Engine Selection

Resona supports three categories of engines: built-in local engines that run entirely on your
machine, resona-api server entries that proxy to a remote (or locally-managed) gateway, and cloud
provider entries that send audio to an external API. All three are addressed through the same
`--engine NAME` flag.

---

## Built-in local engines

The following engines are always available without any configuration. They are loaded in-process
(or as a subprocess if the optional extra is not installed).

| Engine | Package | Backend | Notes |
|--------|---------|---------|-------|
| `faster-whisper` | `resona-engine-faster-whisper` | CTranslate2 INT8 | Default, recommended, no PyTorch |
| `whisper` | `resona-engine-whisper` | OpenAI Whisper (PyTorch) | `[whisper]` extra required |
| `voxtral` | `resona-engine-voxtral` | HuggingFace Transformers | `[voxtral]` extra; supports Voxtral, Whisper-large, etc. |

All built-in engines are **always private** — no audio leaves the machine.

### Model selection

Each engine has a default model and reads a corresponding environment variable:

| Engine | Env var | Default |
|--------|---------|---------|
| `faster-whisper` | `DEFAULT_FASTWHISPER_MODEL` | `large-v3` |
| `whisper` | `DEFAULT_WHISPER_MODEL` | `large-v3` |
| `voxtral` | `DEFAULT_VOXTRAL_MODEL` | `openai/whisper-large-v3` |

Override per invocation with `--model`:

```bash
resona transcribe recording.mp3 --engine faster-whisper --model medium
resona transcribe recording.mp3 --engine voxtral --model openai/whisper-large-v3-turbo
```

!!! note "First-run download"
    Local engines download the model on first use (typically 1–3 GB). Subsequent runs use the
    cached model.

---

## Cloud STT providers

Cloud engines are registered in `~/.resona/config.json` and use external APIs. They appear
automatically in the resona-api catalogue when the corresponding API key environment variable
is set.

| Provider | `--engine` name | API key env var | Default model |
|----------|----------------|-----------------|---------------|
| Deepgram | `deepgram` | `DEEPGRAM_API_KEY` | `nova-3` |
| ElevenLabs | `elevenlabs` | `ELEVENLABS_API_KEY` | `scribe_v1` |
| OpenAI Whisper | `openai` | `OPENAI_API_KEY` | `whisper-1` |

Cloud engines are **never private** — audio is transmitted to the provider's servers.

### Registering cloud engines

```bash
export DEEPGRAM_API_KEY=dg_...
resona engines add deepgram --type cloud --provider deepgram
resona engines add elevenlabs --type cloud --provider elevenlabs
resona engines add openai --type cloud --provider openai
```

Once registered, use them with `--engine`:

```bash
resona transcribe recording.mp3 --engine deepgram
resona transcribe recording.mp3 --engine openai --language en
```

!!! tip "Cloud engine auto-activation in resona-api"
    When resona-api starts, it reads the API key environment variables. Any provider whose key is
    present is automatically included in the `GET /v1/engines` catalogue — no explicit configuration
    in `config.json` is needed on the server side. Set the key and the engine appears.

---

## The `--engine` flag and resolution order

`resona transcribe --engine NAME` accepts any of the following:

1. A built-in local engine name: `faster-whisper`, `whisper`, `voxtral`
2. A named entry in `~/.resona/config.json` (server or cloud type)
3. A cloud provider name used directly: `deepgram`, `elevenlabs`, `openai`

When `--engine` is not specified, the resolution order is:

1. `default_engine` field in `~/.resona/config.json`
2. Hardcoded default: `faster-whisper`

```bash
resona transcribe recording.mp3                      # faster-whisper (hardcoded default)
resona transcribe recording.mp3 --engine whisper     # OpenAI Whisper
resona transcribe recording.mp3 --engine deepgram    # Deepgram cloud
resona transcribe recording.mp3 --engine my-server   # named config.json entry
```

---

## config.json engine entries

Engine entries are stored in `~/.resona/config.json`. Edit them with `resona engines add` or
directly in the file.

### resona-api server entry

```json
{
  "engines": [
    {
      "name": "gpu-server",
      "type": "resona-api",
      "api_url": "http://gpu-machine:7000",
      "private": true
    }
  ]
}
```

### Cloud provider entry

```json
{
  "engines": [
    {
      "name": "deepgram",
      "type": "cloud",
      "provider": "deepgram",
      "model": "nova-3"
    }
  ]
}
```

API keys are **never stored in `config.json`** — they are read from environment variables at call
time.

### Setting a default engine

```json
{
  "default_engine": "gpu-server",
  "engines": [...]
}
```

With this config, `resona transcribe` will use `gpu-server` unless `--engine` overrides it.

---

## Managing engines with the CLI

### List engines

```bash
resona engines list
```

Output shows all built-in engines plus configured entries, with type, privacy status, and
reachability.

### Add a server entry

```bash
# Direct LAN connection
resona engines add gpu-server http://192.168.1.20:7000

# With SSH tunnel (port-forwarded automatically)
resona engines add home http://localhost:7000 --ssh user@myserver.example.com

# With Docker Compose auto-start
resona engines add local http://localhost:7000 --compose-dir ~/resona --private
```

### Add a cloud entry

```bash
resona engines add deepgram --type cloud --provider deepgram
resona engines add deepgram-nova --type cloud --provider deepgram --model nova-2
```

### Test reachability

```bash
resona engines test               # all configured engines
resona engines test gpu-server    # one specific entry
```

### Show live gateway catalogue

Queries the running resona-api gateway for its current engine catalogue, including
which engines are available right now and what they support.

```bash
resona engines status
```

---

## SSH tunnels and auto-start

**SSH tunnels** allow the CLI to reach a remote resona-api server by opening a port-forward.
Configure with `--ssh user@host`:

```bash
resona engines add remote http://localhost:7000 --ssh user@myserver.example.com
```

The CLI opens `ssh -L <local-port>:<host>:<remote-port> user@myserver.example.com` when needed.

**Auto-start** via Docker Compose brings up the server locally when it is not reachable. Configure
with `--compose-dir`:

```bash
resona engines add local http://localhost:7000 --compose-dir ~/resona
```

When the CLI detects the server is down, it runs `docker compose up -d` in the specified directory
before retrying.
