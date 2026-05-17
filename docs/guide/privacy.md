# Privacy

For sensitive audio — medical dictation, legal recordings, confidential meetings — it is important
to ensure that audio never leaves the local machine. Resona provides explicit privacy controls
through the `--private` flag and `config.json` settings.

---

## What "private" means

A **private** engine processes audio entirely on the local machine or within a trusted network
boundary that you control. No audio bytes are transmitted to any third-party cloud service.

A **non-private** engine (cloud engine) sends audio to an external provider's API servers
(Deepgram, ElevenLabs, or OpenAI). Those requests are subject to the provider's data retention
and privacy policies.

---

## Which engines are private

| Engine category | Private? |
|----------------|----------|
| Built-in local (`faster-whisper`, `whisper`, `voxtral`) | Always |
| resona-api server entry marked `"private": true` | Yes |
| resona-api server entry without `"private": true` | No |
| Cloud entry (`deepgram`, `elevenlabs`, `openai`) | Never |

Built-in local engines run in-process or in a subprocess on the same machine — no network
traffic is generated for the audio data.

A resona-api server entry is private when you own and control the server (e.g. a machine on your
LAN, a home server reachable via SSH tunnel, or a Docker Compose stack on localhost) and you mark
it explicitly:

```bash
resona engines add local http://localhost:7000 --private
resona engines add home http://localhost:7000 --ssh user@homeserver.example.com --private
```

---

## The `--private` flag

Pass `--private` to `resona transcribe` to restrict engine selection to private engines only.
If the selected or default engine is not private, the command fails with an error rather than
silently falling back to a cloud engine.

```bash
resona transcribe patient-notes.mp3 --private
```

If the gateway is unreachable, the CLI falls back to a built-in local engine automatically,
which is always private — so `--private` is honoured in both server and local-only modes.

Pass `--no-private` to explicitly allow non-private engines, even if `default_private` is set
in `config.json`:

```bash
resona transcribe interview.mp3 --no-private --engine deepgram
```

---

## Making `--private` the default

Add `"default_private": true` to `~/.resona/config.json` to require a private engine for every
invocation of `resona transcribe` without having to pass `--private` each time:

```json
{
  "default_private": true,
  "engines": [
    {
      "name": "local",
      "type": "resona-api",
      "api_url": "http://localhost:7000",
      "private": true
    }
  ]
}
```

With this setting, running `resona transcribe recording.mp3` behaves as if you had passed
`--private`. Override it for a single invocation with `--no-private`.

---

## resona-api gateway privacy

The gateway enforces privacy at the engine resolution layer. When a request includes
`"private": true`, the registry skips all cloud engines and any server entries not marked
private. If no private engine is available, the gateway returns `400 Bad Request` rather than
routing the audio to a cloud provider.

```bash
# POST /v1/audio/transcriptions with private=true
curl http://localhost:7000/v1/audio/transcriptions \
  -F file=@patient-notes.mp3 \
  -F private=true
```

---

## Practical recommendations for medical use

!!! warning "Medical and legal audio"
    Audio containing patient data, personal health information, or privileged communications
    should only be processed with private engines. Setting `"default_private": true` in
    `config.json` prevents accidental cloud submission.

1. Set `"default_private": true` in `~/.resona/config.json`.
2. Use the built-in `faster-whisper` engine (default) or a self-hosted resona-api with
   `"private": true`.
3. Run `resona engines list` to confirm no cloud engine is set as the default.
4. If you need cloud quality for non-sensitive content, use `--no-private` explicitly so the
   intent is visible in your command history.

---

## Summary

| Scenario | Recommended setup |
|----------|------------------|
| Fully offline, no server | Built-in `faster-whisper` (default, always private) |
| Self-hosted server on LAN | `resona engines add ... --private` + `"default_private": true` |
| Mix: local for sensitive, cloud for other | `"default_private": true` in config; use `--no-private` for non-sensitive runs |
| Cloud only | No special config; `--no-private` or remove `default_private` |
