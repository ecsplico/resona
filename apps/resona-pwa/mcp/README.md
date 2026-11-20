# resona-dictations-mcp

MCP server that gives a user of the Resona app access to **their own dictations**
(recordings and transcripts in the app's Directus) and **documents** (converted PDFs and
images with their Markdown). It is a service of the app's
compose stack and is reached at `<app origin>/mcp` (Streamable HTTP), proxied by the
PWA (`server/routes/mcp.ts`). `resona-mcp` in the Resona monorepo is a different
server: it exposes hub jobs, not dictations.

Standalone mode keeps its data in the browser's IndexedDB; this server cannot reach it.

## How access works

- Every request needs `Authorization: Bearer <token>`: a Directus token of the user
  (a static token of their own user row, or an access token). No token, or one that
  Directus rejects, is `401` before any tool runs.
- The server checks the token with Directus `GET /users/me` and keeps an accepted
  token for 30 s (by SHA-256 hash). Each Directus call of a tool runs with the token
  itself, so a revoked token gets no data at once; the first such rejection drops the
  cache entry and the token is `401` from the next request on.
- An MCP session is bound to the token that opened it; another token gets `404`.
- The server holds **no Directus admin or worker token** and never calls the Resona
  hub. Directus' `user` policy and the owner-guard extension decide every access; the
  queries add no owner filter of their own (`directus.py` is the only module that
  talks to Directus).
- `Host` and `Origin` are checked against allowlists before authentication.

## Tools

| Tool | What it returns |
|------|-----------------|
| `list_dictations(limit≤50, cursor, status?, review_status?, since?)` | Newest first: id, title, created, duration, language, status, review status, text length. `next_cursor` for the next page. |
| `search_dictations(query, limit≤50)` | Dictations whose title or transcript text contains the query (case-insensitive), with `matched_in`. No text. |
| `get_dictation(id, offset=0, limit≤32768)` | Metadata plus one range of the text the app shows, with `next_offset` / `total_chars` and `text_sha256` (SHA-256 hex of the whole UTF-8 text). |
| `update_dictation_text(id, text, expected_sha256?)` | Replaces the text the app shows (`transcripts.text`; raw and profile output stay), or creates the transcript when there is none, as the app's editor does. A stale `expected_sha256` → `conflict`, nothing written; while a postprocess profile runs → `busy`. Metadata + new `text_sha256`. |
| `rename_dictation(id, title)` | Sets the trimmed title and `title_auto=false` (no automatic title replaces it). Metadata. |
| `set_review_status(id, "editing"\|"ready")` | Sets the review status. Metadata. |
| `delete_dictation(id, confirm)` | Cannot be undone. Without `confirm: true` → `confirm_required`, nothing deleted. Deletes the recording like the app (transcript, corrections and parts cascade; the audio files stay in file storage, which the `user` role cannot delete). Metadata of the deleted dictation. |
| `list_documents(limit≤50, cursor)` | The user's documents (converted PDFs/images), newest first: id, title, created, updated, status, Markdown length. `next_cursor` for the next page. |
| `get_document(id, offset=0, limit≤32768)` | Metadata (plus `error_message`, `page_count`) and one range of the document's Markdown, with `next_offset` / `total_chars`. |
| `rename_document(id, title)` | Sets the title; returns metadata only (id, title, created, updated, status). |
| `delete_document(id, confirm)` | Deletes the document row as the app does (the uploaded file stays in `directus_files`); `{id, title, deleted}`. Without `confirm: true` it deletes nothing (`confirm_required:` error). |

Everything a tool returns goes to the model of the client that calls it; the tool
descriptions say so. Another user's id and a missing id give the same "not found" error.
Write tools return metadata only, never text. Error codes prefix the tool error text
(`conflict: …`). The fields each dictation write tool changes are the app's
(`app/utils/dictationEdit.ts`, `useTranscriptEdit.ts`, `deleteRecording`):
`shared/dictationEdit.cases.json` drives both `tests/test_edit_tools.py` and the app's
`test/dictationEdit.spec.ts`. Renaming a document needs `title` in the `user`
policy's `documents` update fields (`directus/bootstrap.mjs`).

`tool-inventory.json` lists the tools; regenerate it with
`uv run python scripts/gen_tool_inventory.py` (`--check` in the tests).

## Logs

One line per tool call: `tool=<name> user=<id> outcome=<ok|not_found|…> ids=<row ids>
ms=<duration>`. Never transcript text, titles, search queries or tokens: list queries go
to Directus in a `SEARCH` body (not in a URL), the HTTP client's request log is off, and
`LOGLEVEL=debug` applies to this package only (third-party loggers stay at INFO, since
the MCP SDK logs message bodies at DEBUG).

## Configuration

| Variable | Default | Meaning |
|----------|---------|---------|
| `DIRECTUS_URL` | `http://127.0.0.1:7700` | Directus, reached over the internal network |
| `DICTATIONS_MCP_HOST` / `DICTATIONS_MCP_PORT` | `127.0.0.1` / `8000` | Listen address (`0.0.0.0` in the image) |
| `DICTATIONS_MCP_ALLOWED_HOSTS` | loopback | Accepted `Host` headers (comma-separated, `host:*` for any port) |
| `DICTATIONS_MCP_ALLOWED_ORIGINS` | loopback | Accepted `Origin` headers (requests without `Origin` pass) |
| `LOGLEVEL` | `info` | Log level of this package |

In the compose files the service has no published port (`docker-compose.backend.yml`
publishes it on loopback only, for a dev server without a PWA container: set
`NUXT_DICTATIONS_MCP_INTERNAL_URL=http://127.0.0.1:7740` for `bun run dev`).

## Tests

```bash
just pwa test-mcp          # or: cd mcp && uv run pytest -q
```

The two-user isolation tests run against a real Directus: `tests/conftest.py` starts
`directus` + `directus-bootstrap` from `docker-compose.backend.yml` under a throwaway
compose project on a free loopback port (`tests/compose.test.yml`) and removes it with
its volumes afterwards. Docker is required. To use a Directus that already runs and is
bootstrapped, set `DMC_TEST_DIRECTUS_URL`, `ADMIN_EMAIL` and `ADMIN_PASSWORD`; the admin
login is used by the tests only (to create users and revoke a token), never by the service.
