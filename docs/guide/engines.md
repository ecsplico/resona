# Engine Selection

Hub jobs run on engines chosen by the job's **execution profile** — a list of approved engine
candidates stored in the organization settings (seeded: `private-auto` (default), `mac-private`,
`linux-cuda`, `cpu-private`). `~/.resona/config.json` only lists the hubs the CLI talks to;
built-in local engines run in-process for `resona live` and on workers.

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

Override per invocation with `--execution-profile`, `--engine` and `--model`. The hub accepts an
override only if it matches an approved candidate of the profile; otherwise it answers
`422 unapproved_override` and the CLI exits 1:

```bash
resona transcribe recording.mp3 --execution-profile cpu-private --engine faster-whisper --model medium
```

!!! note "First-run download"
    Local engines download the model on first use (typically 1–3 GB). Subsequent runs use the
    cached model.

---

## Cloud STT providers

Deepgram, ElevenLabs and OpenAI are cloud engines. They are **never private** — audio is
transmitted to the provider's servers. They run only on hub-approved non-private workers, for
jobs submitted with `--allow-cloud` in an organization with `cloud_enabled=true`; the CLI never
calls a provider directly, and provider API keys in the environment never enable cloud.

| Provider | Engine name | API key env var (worker) | Default model |
|----------|-------------|--------------------------|---------------|
| Deepgram | `deepgram` | `DEEPGRAM_API_KEY` | `nova-3` |
| ElevenLabs | `elevenlabs` | `ELEVENLABS_API_KEY` | `scribe_v1` |
| OpenAI Whisper | `openai` | `OPENAI_API_KEY` | `whisper-1` |

---

## config.json hub entries

Hub entries are stored in `~/.resona/config.json`. Edit them with `resona engines add` or
directly in the file (see [Config Files](../configuration/config-files.md)).

```json
{
  "engines": [
    {
      "name": "gpu-server",
      "api_url": "http://gpu-machine:7000"
    }
  ]
}
```

`default_engine` only selects the in-process engine for `resona live`.

---

## Managing engines with the CLI

### List engines

```bash
resona engines list
```

Output shows all built-in engines plus configured hub entries, with type and reachability.

### Add a hub entry

```bash
# Direct LAN connection
resona engines add gpu-server http://192.168.1.20:7000

# With SSH tunnel (port-forwarded automatically)
resona engines add home http://localhost:7000 --ssh user@myserver.example.com

# With Docker Compose auto-start
resona engines add local http://localhost:7000 --compose-dir ~/resona
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
