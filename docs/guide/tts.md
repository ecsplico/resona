# Speech Synthesis

Resona provides text-to-speech (TTS) synthesis through cloud providers and local
(offline) engines, accessible via the `resona speech` command and the resona-api
`POST /v1/audio/speech` endpoint. When the gateway is reachable, requests are
forwarded to the selected cloud or local-TTS engine. When **no gateway is
reachable**, `resona speech` falls back to a local in-process engine — by default
**Piper**, a torch-free ONNX engine that ships with `resona-cli` — so synthesis
works fully offline out of the box.

---

## `resona speech`

Synthesise speech from a text string and either save it to a file or play it back immediately.

```
resona speech TEXT [OPTIONS]
```

**Options**

| Flag | Default | Description |
|------|---------|-------------|
| `--engine NAME` | (gateway default) | Engine name; forwarded to the gateway, or a local TTS engine name when falling back |
| `--voice NAME` | (engine default) | Voice name; omit to let the engine pick its default |
| `--language`, `-l` | `de` | Language hint; used by local engines to select a voice |
| `--model NAME` | `tts-1` | TTS model name (gateway only) |
| `--format EXT` | `mp3` | Output audio format: `mp3`, `opus`, `aac`, `flac`. Offline fallback always writes WAV |
| `--speed FLOAT` | `1.0` | Speech speed multiplier (0.25–4.0) |
| `--play` | off | Play audio immediately instead of saving |
| `--output PATH` | `speech.mp3` (`speech.wav` offline) | Output file path; use `-` for stdout |
| `--private` | off | Require a private engine (no cloud) |

---

## Cloud TTS providers

| Provider | Engine name | API key env var | Default model | Default voice |
|----------|------------|-----------------|---------------|---------------|
| OpenAI | `openai` | `OPENAI_API_KEY` | `tts-1` | `alloy` |
| ElevenLabs | `elevenlabs` | `ELEVENLABS_API_KEY` | (voice-based) | `Rachel` |
| Deepgram | `deepgram` | `DEEPGRAM_API_KEY` | `aura-2` | `aura-asteria-en` |

API keys are read from environment variables at call time — they are never stored in
`~/.resona/config.json`.

---

## Examples

### OpenAI

```bash
export OPENAI_API_KEY=sk-...

# Synthesise with the default voice (alloy)
resona speech "Good morning, Dr. Schmidt."

# Specific voice
resona speech "Processing complete." --engine openai --voice nova

# Higher-quality model
resona speech "Welcome to Resona." --engine openai --model tts-1-hd --voice shimmer

# Save to a specific path
resona speech "Dictation started." --engine openai --output start.mp3
```

OpenAI voices: `alloy`, `echo`, `fable`, `onyx`, `nova`, `shimmer`.

### ElevenLabs

```bash
export ELEVENLABS_API_KEY=el-...

resona speech "Bitte warten Sie." --engine elevenlabs --voice Rachel
resona speech "Hello world." --engine elevenlabs --voice Adam --format mp3
```

Voice names correspond to ElevenLabs voice IDs or display names. Check the ElevenLabs dashboard
for available voices on your account.

### Deepgram

```bash
export DEEPGRAM_API_KEY=dg-...

resona speech "Appointment confirmed." --engine deepgram
resona speech "Beep." --engine deepgram --voice aura-luna-en
```

Deepgram voice names use the format `aura-<name>-<lang>`, for example `aura-asteria-en`,
`aura-luna-en`, `aura-stella-en`.

---

## Playback

The `--play` flag pipes the audio to the first available local player:

1. `aplay` (Linux, ALSA)
2. `afplay` (macOS)
3. `mpv` (cross-platform)

```bash
resona speech "Recording started." --play
resona speech "Fertig." --engine elevenlabs --voice Rachel --play
```

!!! warning "`--play` and `--output -` are mutually exclusive"
    You cannot pipe to stdout and play at the same time. Choose one or the other.

---

## Output formats

| Format | Flag value | Notes |
|--------|-----------|-------|
| MP3 | `mp3` | Default; widely compatible |
| Opus | `opus` | Low bitrate, good for voice |
| AAC | `aac` | Common on Apple devices |
| FLAC | `flac` | Lossless |

Not all formats are supported by all providers. OpenAI supports all four. ElevenLabs and Deepgram
support MP3 and a subset of others — check the provider documentation if you need a specific
format.

---

## Piping to stdout

Use `--output -` to write raw audio bytes to stdout. This is useful for piping into other tools:

```bash
resona speech "Hello world" --output - | ffmpeg -i pipe:0 output.ogg
```

---

## resona-api endpoint

The gateway exposes an OpenAI-compatible TTS endpoint.

```
POST /v1/audio/speech
Content-Type: application/json
```

**Request body**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `input` | string | (required) | Text to synthesise |
| `engine` | string | (gateway default) | Engine name |
| `voice` | string | `alloy` | Voice name |
| `model` | string | `tts-1` | TTS model |
| `response_format` | string | `mp3` | Audio format |
| `speed` | float | `1.0` | Speed multiplier |
| `private` | bool | `false` | Require a private engine |

**Example**

```bash
curl http://localhost:7000/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"input": "Hello world", "engine": "openai", "voice": "nova"}' \
  --output speech.mp3
```

The response is a binary audio stream. `Content-Type` is set to the MIME type of the requested
format (e.g. `audio/mpeg` for MP3).

---

## Local / offline TTS

`resona speech` does not require a running gateway. When no resona-api is
reachable (or the gateway connection fails), the command synthesises locally with
an in-process engine from `resona-tts-local`.

- **Default offline engine: Piper** (`piper`) — pure ONNX/onnxruntime, torch-free,
  CPU-realtime, cross-platform. It ships as a base dependency of `resona-cli`, so
  offline synthesis works with no extra install.
- Voices are downloaded on first use into `PIPER_VOICES_DIR` (default
  `<model cache>/piper`). The default voice is German
  (`de_DE-thorsten-medium`); `--language` selects a per-language default, and an
  explicit `--voice <piper-id>` (e.g. `de_DE-kerstin-low`) overrides it.
- Offline output is always **WAV** (a non-`wav` `--format` is ignored with a
  warning).

```bash
# No server needed — synthesise German offline with Piper
resona speech "Guten Tag, der Befund ist unauffällig." --play
resona speech "Hello world" --language en --output hello.wav
```

Higher-quality local engines (Kokoro, Chatterbox, Qwen) are available but pull
torch; install one with `just install-cli-kokoro` (or
`pip install 'resona-tts-local[kokoro]'`) and select it with `--engine kokoro`.

!!! note "`--private` and local TTS"
    Local engines are inherently private (no audio leaves the machine). Cloud
    providers are never private; if `--private` is set against the gateway and no
    private TTS engine is configured, the request fails with `400 Bad Request`.
