# Webhooks

A job can name a webhook endpoint. When the job ends (`completed`, `failed` or `cancelled`), the hub POSTs a small signed event to that URL. The event says *which* job ended and how; your backend then fetches the result with the normal job API. The full wire contract (routes, URL rules, headers, delivery log) is in the [HTTP API reference](../reference/api.md#webhook-endpoints-role-org_admin).

## Push or pull

| | Pull (`GET /v1/jobs/{id}`) | Push (webhook) |
|---|---|---|
| Who asks | Your client polls until the job is terminal | The hub calls your backend once the job ends |
| Needs | Nothing beyond a client credential | A public `https` endpoint and a stored signing secret |
| Good for | CLIs, scripts, a user waiting for one file | Backends handling many jobs, long document or diarization jobs |
| Source of truth | Yes | No: a notification only, always read the job afterwards |

Both work side by side: a job with a webhook can still be polled, and a missed or abandoned delivery never changes the job. If in doubt, poll; add a webhook when polling becomes wasteful.

## Register an endpoint

Endpoints belong to the organization and need an `org_admin` credential. The signing secret (`whsec_…`) is shown once, by `add`/`create_webhook` and by `rotate`/`rotate_webhook`; store it where your backend reads its secrets.

=== "CLI"

    ```bash
    resona webhooks add backend https://app.example.com/resona-hook > secret.txt
    resona webhooks list
    resona webhooks rotate backend > secret.txt   # the old secret stops signing at once
    resona webhooks remove backend                # revoke; pending deliveries are abandoned
    ```

=== "Python"

    ```python
    from resona_client import ResonaClient

    client = ResonaClient()  # RESONA_API_URL, RESONA_API_KEY (org_admin)
    hook = client.create_webhook("backend", "https://app.example.com/resona-hook")
    save_secret(hook.secret)  # shown only here and by rotate_webhook
    ```

Then name the endpoint on each job that should notify it:

=== "CLI"

    ```bash
    resona transcribe memo.m4a --webhook backend --no-wait
    ```

=== "Python"

    ```python
    job = client.submit_job("memo.m4a", kind="audio", webhook="backend")
    ```

An unknown or revoked name fails admission with `422 unknown_webhook`. Only production jobs notify; evaluation and testbench jobs never do. See the [CLI reference](cli.md#webhooks) and the [client reference](../reference/client.md#webhooks) for every option.

## The payload

```json
{"type": "job.completed", "job_id": 42, "kind": "audio", "status": "completed", "error_code": null, "finished_at": "2026-11-20T12:00:00Z"}
```

`type` is `job.completed`, `job.failed` or `job.cancelled`; `error_code` is the job's machine error code (`deadline_exceeded`, `deleted`, …) or null. The request carries the [Standard Webhooks](https://www.standardwebhooks.com/) headers `webhook-id`, `webhook-timestamp` and `webhook-signature` ([header table](../reference/api.md#webhook-endpoints-role-org_admin)).

### What the payload never contains

The event is built from identifiers, enums and a time only (`resona_job_core.webhooks.JobEvent` forbids extra fields), so it never carries:

- the transcript, Markdown, segments or any other result content
- the file name, upload, language hint, instructions or profile text
- error messages (only the error code)
- the organization, credential, worker or endpoint secret

A leaked or misdirected webhook therefore reveals that a job ended, never what was in it. The content stays behind the authenticated job API: `GET /v1/jobs/{id}/result`.

## Verify the signature

Verify every request before trusting it: check the signature over the **raw body bytes** (not re-serialized JSON), and reject a `webhook-timestamp` more than five minutes away from your clock. Answer any `2xx` once the event is handled.

### Python

`resona_client.webhooks.verify` does all checks with the same `sign` function the hub uses and returns the parsed `JobEvent`; any failure raises `InvalidWebhookSignature`:

```python
--8<-- "scripts/webhook_examples/verify_python.py"
```

Wire `handle_webhook` into your framework with the raw body, for example `await request.body()` in FastAPI or Starlette.

### Node

Without a Resona package, using only `node:crypto`:

```js
--8<-- "scripts/webhook_examples/verify_node.mjs"
```

Read the body as a `Buffer` (for example `express.raw({ type: "application/json" })`) before passing it in, and deduplicate on `webhook-id` as below. Both examples are tested against a hub-signed event in `scripts/tests/test_webhook_examples.py`.

## Retries and duplicates

Any answer other than `2xx` (including a redirect, which is never followed, a timeout after 10 s or a refused connection) is retried after 5 s, 30 s, 2 min, 10 min and 30 min, then hourly. A delivery still unsent 24 hours after the job ended is abandoned. One endpoint receives its events one at a time, in the order the jobs ended: an event waiting for its retry holds back the later ones. `resona webhooks deliveries <name>` (or `list_webhook_deliveries`) shows attempts, last HTTP status and last error per event.

Delivery is **at least once**: a hub restart during a send repeats the event. Every retry of one event carries the same `webhook-id`, so deduplicate on it, as the Python example does: remember handled ids (a unique column in your database, kept for at least a day) and answer `2xx` to a repeat without processing it again.

Because an event can be abandoned, a backend that must not miss a job should also poll its open jobs now and then (`GET /v1/jobs/{id}`): pull stays the source of truth.

## Private addresses

By default an endpoint URL must use `https` and its host must resolve to a public address; loopback, private (RFC 1918 and similar), link-local and unspecified addresses are refused with `422 invalid_webhook_url`, at registration and again before every attempt. This keeps a hub reachable from the internet from being pointed at its own network.

A hub that runs inside a private network and notifies a backend next to it can set `RESONA_WEBHOOK_ALLOW_PRIVATE=true` (hub operator, [environment variables](../configuration/environment.md#resona-api-7000)). Internal hosts are then accepted, over `https` or plain `http`; plain `http` to a public host stays refused.
