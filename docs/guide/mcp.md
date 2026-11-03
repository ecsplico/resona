# MCP Server

`resona-mcp` exposes the hub's asynchronous job API to MCP clients (Claude, omp, SKAILE). It is a thin layer over `resona-client`: it never runs engines itself, and every call goes to the hub with an organization token, so privacy policy and organization isolation are enforced by the hub.

Two transports:

| Mode | Command | Credential | Local files |
|------|---------|------------|-------------|
| Local stdio | `resona-mcp stdio` | `RESONA_API_KEY` from the environment | only under `RESONA_MCP_ROOT` |
| Hosted HTTP | `resona-mcp http --host H --port P` | `Authorization: Bearer <org token>` on every request | never |

## Tools

| Tool | Purpose |
|------|---------|
| `create_upload_url()` | Single-use upload link, valid 10 minutes, for one file up to the hub limit (200 MiB) → `{upload_url, token_header, token, expires_at, max_bytes, curl}`. The agent runs `curl` in its shell with the file path filled in and gets `{upload_id, sha256, size}`. The file's bytes go straight to the hub and never pass through the model. |
| `upload_content(filename, content_base64, sha256)` | Upload a tiny file inline (at most 8 MiB decoded) → `{upload_id, sha256, size}`. `sha256` is the hex digest of the original file; if the decoded content does not match it, nothing is uploaded. Models copy long base64 strings unreliably, so prefer `create_upload_url` for anything but a few KB. |
| `transcribe_audio(upload_id \| path, execution_profile?, language?, profile?, options?, private=true, idempotency_key?)` | Submit an audio job → `{job_id, status, private}` immediately. `options`: `translate`, `word_timestamps`, `vad_filter`. |
| `convert_document(upload_id \| path, …, options?)` | Submit a PDF/image → Markdown job → `{job_id, status, private}`. `options`: `languages` (`de`, `en`, `fr`, `it`, `es`, `ko`), `ocr`, `ocr_engine`, `page_start`, `page_end`, `pipeline`, `image_scale` (1.0–4.0, OCR raster multiplier), `ocr_min_confidence` (0–1, `rapidocr` only); see the [API reference](../reference/api.md#document-options). |
| `get_job(job_id)` | Status (`pending` with `queue_reason`, `processing`, `completed`, `failed`, `cancelled`). |
| `get_result(job_id, offset=0, limit=32768)` | One Markdown chunk plus `next_offset`, `total_chars`, language, warnings, provenance. Each call is one hub range read (`GET /v1/jobs/{id}/result/markdown`), so the full result never passes through the MCP server. Concatenating chunks from offset 0 until `next_offset` is `null` reproduces the REST `markdown` exactly. `limit` is capped at 32 768 characters. |
| `cancel_job(job_id)` | Request cancellation; later worker output is rejected. |
| `list_engines()` | The organization's execution profiles (approved engine/model/accelerator candidates) and the engines it can use now (`GET /v1/engines`: tuples that an approved worker of the organization has tested; cloud providers only when cloud processing is enabled). |
| `get_settings()` | The organization's current non-secret settings revision (read-only). |

There are no tools that change settings or profiles, issue or revoke credentials, or enroll workers. Use `resona settings` and `resona-admin` for those.

Jobs are private by default. `private=false` only works when the organization has enabled cloud processing; otherwise the hub answers `policy_denied`. There is no fallback to another provider or to local processing.

A typical agent flow:

```text
create_upload_url()                            → {curl: "curl … -F 'file=@<PATH>' …"}
$ curl … -F 'file=@memo.wav' …                 → {upload_id}
transcribe_audio(upload_id=…)                  → {job_id: 12, status: "pending", private: true}
get_job(12)                                    → … status: "completed"
get_result(12)                                 → {markdown: "...", next_offset: 32768, total_chars: 51234}
get_result(12, offset=32768)                   → … until next_offset is null
```

## Local stdio

`RESONA_API_URL` and `RESONA_API_KEY` (an org client token from `resona-admin credential issue`) are required. `path` input is enabled only when `RESONA_MCP_ROOT` names an existing directory: the path (absolute or relative to the root) must resolve, after following symlinks, to a regular file inside the root. `..` escapes, symlinks pointing outside the root, directories and URLs are rejected. Without `RESONA_MCP_ROOT`, path input is disabled and only `create_upload_url`, `upload_content` and `upload_id` work. There is no default root. Logs go to stderr; stdout carries only the protocol.

omp (`~/.omp/agent/mcp.json`) or Claude (`~/.claude.json`, `mcpServers`):

```json
{
  "mcpServers": {
    "resona": {
      "type": "stdio",
      "command": "resona-mcp",
      "args": ["stdio"],
      "env": {
        "RESONA_API_URL": "https://resona.ecsplico.de",
        "RESONA_API_KEY": "<organization client token>",
        "RESONA_MCP_ROOT": "/Users/me/Dictations"
      }
    }
  }
}
```

From a workspace checkout, use `"command": "/path/to/resona/.venv/bin/resona-mcp"`.

## Hosted Streamable HTTP

```bash
RESONA_API_URL=http://127.0.0.1:7000 \
RESONA_MCP_ALLOWED_HOSTS=resona.ecsplico.de \
RESONA_MCP_ALLOWED_ORIGINS=https://resona.ecsplico.de \
  resona-mcp http --host 127.0.0.1 --port 7010
```

The endpoint is `/mcp` (Streamable HTTP). `resona_mcp.http:create_app()` returns the same ASGI app for embedding; its lifespan runs the MCP session manager, so run that lifespan when mounting it inside another application.

- **Authentication.** Every request needs `Authorization: Bearer <org token>`; missing or unknown tokens get 401. The server verifies the token against the hub (`GET /v1/settings`, so client and org-admin tokens work, worker tokens do not) and caches an accepted token for 30 seconds. Each tool call then uses that request's own token against the hub; the server has no key of its own. A token from another organization gets the hub's 404 for jobs it does not own.
- **Sessions.** A protocol session is bound to the token that opened it. A session id presented with a different token is answered as if the session did not exist (404).
- **Input.** Remote callers cannot pass `path` or URLs; use `create_upload_url` and send the file with `curl`, or `upload_content` for tiny files (request bodies up to 12 MiB, enough for 8 MiB of decoded content).
- **Host/Origin.** `Host` must match `RESONA_MCP_ALLOWED_HOSTS` (421 otherwise) and a supplied `Origin` must match `RESONA_MCP_ALLOWED_ORIGINS` (403 otherwise). Both are comma-separated; `host:*` / `scheme://host:*` allow any port. Defaults allow loopback only.

Client configuration for a hosted server:

```json
{
  "mcpServers": {
    "resona": {
      "type": "http",
      "url": "https://resona.ecsplico.de/mcp",
      "headers": { "Authorization": "Bearer ${RESONA_API_KEY}" }
    }
  }
}
```

| Variable | Mode | Default | Meaning |
|----------|------|---------|---------|
| `RESONA_API_URL` | both | stdio: required; http: `http://127.0.0.1:7000` | Hub base URL |
| `RESONA_API_KEY` | stdio | required | Organization client token |
| `RESONA_MCP_ROOT` | stdio | unset (path input disabled) | Directory local `path` inputs must stay inside |
| `RESONA_MCP_ALLOWED_HOSTS` | http | loopback | Allowed `Host` values |
| `RESONA_MCP_ALLOWED_ORIGINS` | http | loopback `http://` origins | Allowed `Origin` values |
