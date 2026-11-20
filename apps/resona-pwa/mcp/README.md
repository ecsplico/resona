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
- Users create, replace and revoke their static token in the app under Einstellungen
  → MCP, which also shows setup snippets for Claude Code, Claude Desktop and Cursor.
  The token has the same rights as the user's login.
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
| `create_dictation_from_text(text, title?, language?)` | Saves the text as a finished dictation (`source` `live`, `status` `done`, like a saved live dictation) plus its transcript; no title → dated placeholder `Diktat DD.MM.YYYY HH:MM`, `title_auto` true. Text ≤ 200000 characters. |
| `create_audio_upload(filename, language?, profile?)` | The new dictation's `id`, a single-use `upload_url` (10 min), `max_bytes` and a `curl -T <file> <url>` command: the audio never passes the model. The upload creates the recording `pending` (`source` `batch`, `title_auto` true) and the project worker transcribes it. |
| `get_audio_link(id)` | `audio_url` to the dictation's audio, valid 10 minutes (Range requests work). |

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

## Upload and audio links

`create_audio_upload` and `get_audio_link` hand out `<DICTATIONS_MCP_PUBLIC_URL>/mcp/links/<upload|audio>/<token>`
(the PWA proxies `/mcp/links/*` with `server/routes/mcp/links/[...path].ts`, streaming the body).

- The token is `<kind>.<link id>.<expiry>.<HMAC-SHA256>` keyed with
  `DICTATIONS_MCP_LINK_SECRET`; forged, altered, expired or other-kind tokens are refused.
  It carries no identity and no Directus token: the user, a Directus client with their
  token and the recording id stay in this process, keyed by the link id, until expiry.
  The link route takes no `Authorization` header; it acts with that held token, so a
  token revoked in the meantime makes the link `401`.
- An upload link is spent by the first request that presents it, whatever its outcome
  (a second use is `410 link_used`). Before anything reaches Directus: `Content-Length`
  required (`411`), ≤ `DICTATIONS_MCP_MAX_UPLOAD_BYTES` (`413`), not empty (`422`), and the
  first bytes must be audio the hub admits (WAV, MP3, AAC/ADTS, Ogg, FLAC, MP4/M4A,
  WebM; else `415`). Then the body streams into Directus files as the user (type and
  extension from the sniffed format) and the recording row is created `pending`; `201`
  `{id, status: "pending"}`.
- An audio link is checked against the owner when it is issued (`get_audio_link` reads the
  row as the caller: another user's id is "not found") and read again as that user on
  every request; it works any number of times until it expires (`410 link_expired`).
- **Single instance assumed**: the link state lives in memory (at most 10000 open links).
  After a restart, or on another replica, a link is unknown and refused like a used one.
- Without `DICTATIONS_MCP_LINK_SECRET` the link routes answer `404 links_disabled` and the
  two link tools return an error; a secret shorter than 32 characters or a missing/invalid
  `DICTATIONS_MCP_PUBLIC_URL` stops the service at start. Access log lines show
  `/mcp/links/<kind>/…`, never the token; the service logs `link=<kind> user= outcome= ids= ms=`.

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
| `DICTATIONS_MCP_LINK_SECRET` | (empty = links off) | Signs upload/audio links; ≥ 32 characters (`openssl rand -hex 32`, `bun run setup` fills it). A new value voids open links |
| `DICTATIONS_MCP_PUBLIC_URL` | (required with a secret) | The app origin the links start with (`PWA_ORIGIN` in the compose files) |
| `DICTATIONS_MCP_MAX_UPLOAD_BYTES` | `209715200` (200 MiB, the hub's upload cap) | Largest audio upload |
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
