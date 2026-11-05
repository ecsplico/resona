# Resona

Resona is a modular audio transcription platform with pluggable ASR engines and a composable postprocessing pipeline. Designed for German medical dictation but usable for any language.

## Repository layout

One monorepo, two toolchains:

| Path | Toolchain | What |
|------|-----------|------|
| `packages/*` | uv workspace (Python) | engines, engine server, resona-api hub, postprocessing, client, eval |
| `apps/resona-cli`, `apps/resona-worker`, `apps/resona-mcp` | uv workspace (Python) | CLI, pull worker, MCP server |
| `apps/resona-pwa` | bun + Nuxt (own lockfile), `worker/` own uv env | browser PWA + Directus data layer; talks to resona-api over HTTP only |
| `docker-compose.*.yml`, `scripts/` | Docker / shell | deployments (GPU, CPU, Coolify hub, Apple Silicon single host) |
| `docs/`, `eval/`, `benchmarks/` | mkdocs / data | documentation site, evaluation corpus, benchmarks |

`just --list` shows the Python recipes, `just pwa --list` the PWA ones. Issues and tickets live on [GitHub Issues](https://github.com/ecsplico/resona/issues), one `area:*` label per component.

## Architecture

```
                    ┌──────────────────────┐
                    │     resona CLI       │
                    │  transcribe / watch /│
                    │  rec / live / ui     │
                    └─────────┬────────────┘
                              │ uses
                    ┌─────────▼────────────┐
                    │   resona-client      │
                    │   ResonaClient       │
                    └──┬───────────────┬───┘
                       │               │
            HTTP/REST  │               │  HTTP/REST
                       │               │
          ┌────────────▼──┐   ┌───────▼──────────────┐
          │  resona-api   │   │ resona-engine-server  │
          │  :7000        │──▶│  + engine plugin     │
          │               │   │                       │
          │ /v1/uploads   │   │ POST /transcribe      │
          │ /v1/jobs      │   │ WS   /ws/transcribe   │
          │ /v1/workers/* │   │ WS   /ws/live         │
          │ CRUD replace  │   │                       │
          │ CRUD prompts  │   │ Stateless, no DB      │
          │               │   │ depends on resona-    │
          │ SQLite DB     │   │ asr-core              │
          │ Postprocessing│   └───────────────────────┘
          └───────────────┘
```

## Packages

| Package | Port | Description |
|---------|------|-------------|
| `resona-asr-core` | -- | Lean ASR contracts: protocol, registry, audio, live transcriber |
| `resona-engine-server` | 7001 | FastAPI HTTP/WS server, hosts an ASR engine |
| `resona-engine-faster-whisper` | -- | CTranslate2 engine (default, recommended) |
| `resona-engine-whisper` | -- | Original OpenAI Whisper (PyTorch) engine |
| `resona-engine-voxtral` | -- | HuggingFace Transformers engine (Voxtral, Whisper, etc.) |
| `resona-cloud-stt` | -- | Cloud STT providers: Deepgram, ElevenLabs, OpenAI Whisper API |
| `resona-cloud-tts` | -- | Cloud TTS providers: OpenAI, ElevenLabs, Deepgram |
| `resona-postprocess` | -- | Composable pipeline: regex replacements + LLM via litellm |
| `resona-api` | 7000 | Job queue + SQLite + postprocessing, calls engine via HTTP |
| `resona-client` | -- | httpx client library for the resona-api REST interface |
| `resona-cli` | -- | CLI: `resona transcribe/watch/rec/live/ui/engines/replacements/prompts` |

## Quick start

### Local-only (no server)

```bash
uv sync --all-packages
uv run resona transcribe recording.mp3
```

No server needed — the CLI spawns a local `faster-whisper` engine automatically.

### Docker

```bash
cp .env.example .env
docker compose -f docker-compose.resona.yml up -d
# issue a token on the hub (printed once as "token: ..."), then export it for the CLI
docker compose -f docker-compose.resona.yml exec api resona-admin organization create acme
docker compose -f docker-compose.resona.yml exec api \
  resona-admin credential issue --organization acme --role client
export RESONA_API_KEY=<token>
# queued jobs need an approved resona-worker; see docs/server/full-stack.md
resona transcribe recording.mp3
```

### Apple Silicon single host

```bash
just mac-engine   # terminal 1: native MLX engine on :7720 (GPU)
just mac-up       # terminal 2: dockerized api on :7710
```

See [docs/server/mac-stack.md](docs/server/mac-stack.md).

### PWA

```bash
cd apps/resona-pwa && bun install && bun run dev
```

See [apps/resona-pwa/README.md](apps/resona-pwa/README.md) for the Directus data layer and deployment.

For end-user install (`uv tool install`), PyTorch extras, macOS, and per-persona variants see [Installation](https://ecsplico.github.io/resona/getting-started/installation/).

> Full documentation: https://ecsplico.github.io/resona/

## License

Apache License 2.0 — see [LICENSE](LICENSE).
