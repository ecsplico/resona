# resona-client

`resona-client` is the Python client library for talking to a resona-api server. It handles authentication, job submission, polling, and full CRUD for postprocessing profiles. Engine configuration — which servers to connect to, auto-start rules, and cloud provider registrations — lives in `~/.resona/config.json` and is managed through `EngineConfig` and `EngineEntry`.

## Installation

`resona-client` is a workspace package. Add it from the monorepo:

```bash
uv add resona-client --package my-project
```

## Quick example

```python
from resona_client.client import ResonaClient

# Connect using RESONA_API_URL / RESONA_API_KEY env vars (RESONA_API_KEY = org-bound token)
client = ResonaClient()

# Or use the resona-api entry in ~/.resona/config.json
client = ResonaClient.from_config()

# Upload + create job (private by default); returns JobRef(job_id, status)
ref = client.submit_job("recording.wav", kind="audio")
job = client.wait_for_job(ref.job_id)
if job.status == "completed":
    result = client.get_result(ref.job_id)
    print(result.raw_text)   # raw transcript
    print(result.markdown)   # after the postprocess profile

# Submit with a postprocess profile stored on the hub
ref = client.submit_job("recording.wav", kind="audio", profile="medical-de")
```

## Authentication

The client sends `Authorization: Bearer <token>` with every request when `api_key` is provided or `RESONA_API_KEY` is set. Tokens are org-bound and issued on the hub with `resona-admin credential issue --organization <id> --role client`. The hub derives the organization from the token; the client never sends a tenant identifier.

```python
client = ResonaClient(base_url="https://resona.example.com", api_key="<token>")
```

## Job lifecycle

Jobs progress through `pending → processing → completed | failed | cancelled`. `wait_for_job()` polls until a terminal state and raises `JobTimeout` when its `timeout` passes; the job keeps running on the hub and the client never falls back to local processing. Each `submit_job()` call sends one `Idempotency-Key` and reuses it on retries, so a lost response cannot create a duplicate job.

```python
ref = client.submit_job("dictation.mp3", kind="audio")
job = client.wait_for_job(ref.job_id, timeout=600)

if job.status == "completed":
    print(client.get_result(ref.job_id).markdown)
else:
    print("not completed:", job.status, job.error)

client.cancel_job(ref.job_id)   # fence the lease
client.delete_job(ref.job_id)   # any state: fence the lease, delete job, input and result now
```

`get_result()` raises `JobNotReady` (409) before completion and `ResultExpired` (410) after the 24-hour result retention.

To read a large Markdown result in bounded pieces, `get_result_markdown(job_id, offset=0, limit=32768, source="markdown")` returns a `MarkdownChunk(text, offset, next_offset, total_chars, kind, language, page_count, warnings, provenance, source)` from `GET /v1/jobs/{id}/result/markdown`; `limit` is at most 32 768 characters and `next_offset` is `None` at the end. `source="raw"` reads the unpolished `raw_markdown` when the result has one, else `markdown`; `MarkdownChunk.source` names the field read, and is `None` when the hub predates the parameter (such a hub ignores `source="raw"`). It raises the same `JobNotReady`/`ResultExpired`.

```python
offset, parts = 0, []
while offset is not None:
    chunk = client.get_result_markdown(ref.job_id, offset=offset)
    parts.append(chunk.text)
    offset = chunk.next_offset
```

For content already in memory (the MCP server's `upload_content`), upload and submit in two steps; the upload is consumed by exactly one job:

```python
up = client.upload_bytes("memo.wav", data)            # POST /v1/uploads → Upload(upload_id, sha256, size)
ref = client.submit_upload(up.upload_id, kind="audio", execution_profile="cpu-private")
```

## v1 Audio routes

The v1 routes call engines synchronously through the gateway and return results immediately — no job queue involved.

```python
# Transcribe synchronously
result = client.create_transcription("file.wav", engine="faster-whisper", language="de")
print(result["text"])

# Transcribe with verbose output (segments + duration)
result = client.create_transcription("file.wav", response_format="verbose_json")
print(result["segments"])

# Text-to-speech
audio_bytes = client.create_speech("Hallo Welt", voice="alloy", engine="openai")
with open("out.mp3", "wb") as f:
    f.write(audio_bytes)

# Engines the organization can use: tuples tested by its approved workers (GET /v1/engines)
engines = client.list_engines()
```

## Webhooks

Instead of polling, a job can name a webhook endpoint the hub notifies when the job ends. Endpoints are managed with an org-admin credential; the signing secret is returned only by `create_webhook` and `rotate_webhook`.

```python
hook = client.create_webhook("backend", "https://app.example.com/resona-hook")  # WebhookCreated(name, url, created_at, secret)
store_secret(hook.secret)                                    # shown once; not part of repr()
client.list_webhooks()                                       # [Webhook(name, url, created_at)], no secrets
client.rotate_webhook("backend").secret                      # new secret; the old one stops signing at once
client.list_webhook_deliveries("backend", limit=20)          # [WebhookDelivery(id, job_id, event, attempts, last_status, delivered_at, last_error)], newest first
client.delete_webhook("backend")

ref = client.submit_job("memo.m4a", kind="audio", webhook="backend")   # also submit_upload / create_transcription
```

`webhook` is sent only when set. Errors are `ResonaAPIError` codes: 422 `invalid_webhook_name` / `invalid_webhook_url` and 409 `webhook_exists` on create, 404 `webhook_not_found` on rotate/delete/deliveries, 422 `unknown_webhook` on a job naming no active endpoint.

The receiving backend verifies each request with `resona_client.webhooks.verify` (pure, no network). It checks the Standard Webhooks headers `webhook-id`, `webhook-timestamp` and `webhook-signature` against the raw body with the same `sign` function (`resona_job_core.webhooks`) the hub uses, compares in constant time, rejects a timestamp more than `tolerance_seconds` (default 300) away from now, and returns the `JobEvent` (`type`, `job_id`, `kind`, `status`, `error_code`, `finished_at`; identifiers only, never content). Any failure raises `InvalidWebhookSignature`.

```python
from resona_client.webhooks import InvalidWebhookSignature, verify

def handle(request):                                          # any web framework
    try:
        event = verify(request.headers, request.body, secret)  # body: the exact bytes received
    except InvalidWebhookSignature:
        return 400
    if event.type == "job.completed":
        result = client.get_result(event.job_id)
    return 204
```

## Testbench

```python
client.testbench_status()                                   # {enabled, items, corrected, runs}
item = client.testbench_contribute("memo.m4a", language="de", label="Brief")   # persists + queues a draft run
client.testbench_list_items(status="needs_correction")      # org_admin
client.testbench_set_reference(item["id"], "corrected text")
cands = client.testbench_candidates()["items"]              # tested worker tuples
client.testbench_run(item["id"], [c["candidate"] for c in cands[:2]])
client.testbench_get_run(item["id"], run_id)                # text, wer/cer, alignment
client.testbench_leaderboard()
client.testbench_delete_item(item["id"])
```

A disabled testbench raises `ResonaAPIError` with `code == "testbench_disabled"` (403). See [Testbench](../guide/testbench.md).

## Profile management

```python
# List all profiles stored on the server
profiles = client.list_profiles()
for p in profiles:
    print(p["name"], p["description"])

# Get the full JSON of a profile
profile = client.get_profile("medical-de")

# Upload a local profile to the server
import json
from pathlib import Path
profile_data = json.loads(Path("my-profile.json").read_text())
client.push_profile(profile_data)

# Download a profile from the server
profile = client.pull_profile("medical-de")
Path("medical-de.json").write_text(json.dumps(profile, ensure_ascii=False, indent=2))

# Delete a profile from the server
client.delete_profile("old-profile")
```

## Engine configuration

`EngineConfig` and `EngineEntry` describe which servers the client knows about. Manage them with `resona engines add/remove/list` from the CLI; or load and modify them programmatically.

```python
from resona_client.config import EngineConfig, EngineEntry, resolve_engine

cfg = EngineConfig.load()
print(cfg.default_engine)       # "faster-whisper"
print([e.name for e in cfg.engines])

# Check which entry would be used right now
entry = resolve_engine()
if entry:
    print(entry.api_url)
```

## Context manager

`ResonaClient` implements the context-manager protocol and closes the underlying httpx connection pool on exit:

```python
with ResonaClient() as client:
    ref = client.submit_job("audio.wav", kind="audio")
    job = client.wait_for_job(ref.job_id)
```

---

## ResonaClient

::: resona_client.client.ResonaClient

---

## EngineConfig

::: resona_client.config.EngineConfig

---

## EngineEntry

::: resona_client.config.EngineEntry

---

## resolve\_engine

::: resona_client.config.resolve_engine

---

## is\_reachable

::: resona_client.config.is_reachable
