# Privacy

For sensitive audio and documents — medical dictation, legal recordings, confidential meetings —
it is important to ensure that data never reaches a third-party provider. Resona makes private
processing the default and enforces it on the hub.

---

## What "private" means

A **private** job is processed entirely by engines and postprocess stages on machines you control
(your own hub workers, or this machine with `--local`). No audio, document or text content
(including the text of a [text job](../reference/api.md#text-jobs) and every intermediate
transcript a postprocess step sees) is transmitted to any third-party cloud service.

A **non-private** job may use cloud stages that send data to an external provider's API
(Deepgram, ElevenLabs, OpenAI, or a cloud LLM for postprocessing). Those requests are subject to
the provider's data retention and privacy policies.

---

## Private by default

`resona transcribe`, `resona doc` and `resona watch` submit every job as private. `--private` is
the default and only states it explicitly:

```bash
resona transcribe patient-notes.mp3 --private
```

The hub checks each job's policy before admission. A job whose execution profile, engine/model
override or postprocess profile needs a cloud stage is rejected with `policy_denied`; the CLI
exits with code 2. Nothing is ever retried on another engine, in the cloud or locally: an
unreachable hub, a timeout or a failed job is reported as an error.

Each job prints one policy summary line on stderr, so the decision is visible:

```
job 12: private=true execution_profile=cpu-private candidate=faster-whisper/large-v3@cpu status=completed
```

---

## Allowing cloud processing

Cloud processing needs **both**:

1. the organization setting `cloud_enabled: true` (set by an `org_admin` in the settings file
   passed to `resona settings set --file settings.json`), and
2. `--allow-cloud` on the command (mutually exclusive with `--private`).

```bash
resona transcribe interview.mp3 --allow-cloud
```

Provider API keys in the environment never enable cloud. Cloud stages run only on hub-approved
non-private workers, never directly from the CLI.

---

## resona-api hub privacy

The hub enforces the same policy for every entry point — `POST /v1/jobs` and the
OpenAI-compatible `POST /v1/audio/transcriptions` adapter. `private` defaults to `true`; a private
job is only leased to private workers, and a job that would need a cloud stage is rejected with
`403 policy_denied`.

```bash
# POST /v1/audio/transcriptions (private=true is the default)
curl http://localhost:7000/v1/audio/transcriptions \
  -H "Authorization: Bearer $RESONA_API_KEY" \
  -F file=@patient-notes.mp3 \
  -F model=whisper-1
```

---

## Testbench contributions

The optional [testbench](testbench.md) is the one place where the hub keeps content beyond the normal retention: contributed recordings and scans, their drafts, references and bench-run results are **persisted until an org admin deletes the item**. It is off by default (`testbench_enabled`), only org admins can read the stored content (the hub operator never can), and all testbench jobs are private, so nothing reaches a cloud provider. Contribute only material you have consent to keep.

---

## Practical recommendations for medical use

!!! warning "Medical and legal audio"
    Audio containing patient data, personal health information, or privileged communications
    should only be processed privately. Keep `cloud_enabled=false` for the organization so that
    even an accidental `--allow-cloud` is denied.

1. Leave `cloud_enabled` off in the organization settings (`resona settings get`).
2. Use a private execution profile (`private-auto`, `mac-private`, `linux-cuda`, `cpu-private`).
3. Check the stderr policy line (`private=true`) or the `private` field in each `<stem>.md`.
4. If you need cloud quality for non-sensitive content, an `org_admin` enables `cloud_enabled`
   and you pass `--allow-cloud` explicitly so the intent is visible in your command history.

---

## Summary

| Scenario | Recommended setup |
|----------|------------------|
| Fully offline, no hub | `--local` with built-in private defaults or cached settings |
| Self-hosted hub | Default (private) jobs; `cloud_enabled=false` |
| Mix: private for sensitive, cloud for other | `cloud_enabled=true`; pass `--allow-cloud` only for non-sensitive runs |
