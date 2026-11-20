# resona-dictations-mcp

MCP server that gives a user of the Resona app access to **their own dictations**
(recordings and transcripts in the app's Directus). It is a service of the app's
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
| `get_dictation(id, offset=0, limit≤32768)` | Metadata plus one range of the text the app shows, with `next_offset` / `total_chars`. |

Everything a tool returns goes to the model of the client that calls it; the tool
descriptions say so. Another user's id and a missing id give the same "not found" error.

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
