# Directus Transcribe Backend Implementation Plan

> **Superseded (2026-06-21):** The `directus-transcribe` worker has been moved
> out of this monorepo into the separate `resona-pwa` repo (alongside Directus).
> resona is now a transcription-only backend (resona-api + engines). This plan
> is kept as a historical record; the worker now lives at `resona-pwa/worker/`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up Directus as the platform backend and build a Python worker (`resona-directus-transcribe`) that detects untranscribed recordings in Directus, transcribes them via `resona-api`, and writes the results back.

**Architecture:** A new src-layout workspace package `packages/directus-transcribe/`. An async poll loop claims `pending` recordings atomically (`status=transcribing`), downloads the audio asset from Directus, POSTs it to `resona-api` `POST /v1/audio/transcriptions` (`response_format=verbose_json`), writes a `transcripts` row, and sets the recording `status=done` (or `error`). Directus runs as an official Docker image wired into `docker-compose.resona.yml`; its schema is captured as a checked-in snapshot. The worker speaks only HTTP (Directus REST + resona-api) — no DB, no engine, no audio deletion.

**Tech Stack:** Python 3.12, `httpx` (async), `python-decouple` (`config()`), `pytest` + `respx` + `anyio` for tests, `asyncio` for the loop. Directus official Docker image. Reuses the repo's hatchling src-layout package convention.

**Async test convention (repo standard — no new deps):** the repo does **not** use `pytest-asyncio`. Async tests are marked `@pytest.mark.anyio` and rely on an `anyio_backend` fixture (`anyio` is already present transitively via httpx). Each test package provides this fixture in `conftest.py`:

```python
@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    return request.param
```

Every `async def` test below is decorated `@pytest.mark.anyio` (NOT `@pytest.mark.asyncio`). Do not add `pytest-asyncio`. **Async fixtures are avoided** — fixtures return plain data; tests construct/close async clients inline (matching `packages/cloud-stt/tests`).

**Spec:** `docs/superpowers/specs/2026-06-17-pwa-directus-platform-design.md`

---

## Scope

This plan covers **backend only** (this repo): the Directus compose service, the schema snapshot/bootstrap docs, the port remap to 7700–7800, and the `directus-transcribe` worker with full test coverage. The Nuxt 4 PWA is a **separate plan** in its own repo.

## Key constraints discovered from the codebase

- `POST /v1/audio/transcriptions` (`packages/api/src/resona_api/audio_routes.py`) accepts multipart `file`, and form fields `model`, `language`, `prompt`, `response_format`, `engine`, `profile`. With `response_format="verbose_json"` it returns `{text, language, duration, segments}`, **plus `structured`** only when the profile pipeline produced extraction data (an `extract` step). For the default profile `structured` is absent. The worker therefore stores `structured` **best-effort** — it reads `resp.get("structured")` and stores `null` when absent. No API change is needed.
- Config is read with `config()` from `python-decouple`, **never** `os.environ[]` (per CLAUDE.md). `resona-client` is the one exception (uses `os.getenv`), but the new worker is a service and MUST use `config()`.
- Tests mock HTTP with `respx` (already a dev dependency in the root `pyproject.toml`).
- Workspace members are `packages/*` and `apps/resona-cli` (root `pyproject.toml` `[tool.uv.workspace]`). A new `packages/directus-transcribe/` is auto-discovered.
- Engine compose services keep their `profiles:` gating and map host ports to container `:7001`. The remap changes only host ports.

## File Structure

```
packages/directus-transcribe/
├── pyproject.toml                                   ← new package manifest
├── README.md                                        ← short usage
└── src/resona_directus_transcribe/
    ├── __init__.py
    ├── client.py        ← DirectusClient: async httpx wrapper (list/claim/download/write/mark)
    ├── transcribe.py    ← TranscribeClient: async httpx wrapper around resona-api
    ├── worker.py        ← process_one() + poll loop + stale-claim recovery
    └── run.py           ← entry point (config() → run asyncio loop)
└── tests/
    ├── conftest.py      ← fixtures (recording dict, wav bytes)
    ├── test_client.py   ← DirectusClient via respx
    ├── test_transcribe.py ← TranscribeClient via respx
    └── test_worker.py   ← process_one happy/error/stale paths via respx

directus/
├── schema-snapshot.yaml ← exported Directus schema (collections + fields + policy)
└── bootstrap.md         ← how to init Directus (role, policy, service token)

docker-compose.resona.yml ← add `directus` + `directus-transcribe` services; remap engine/api host ports
.env.example              ← document new env vars (create if absent)
```

---

## Task 1: Remap host ports to 7700–7800 in compose

**Files:**
- Modify: `docker-compose.resona.yml` (ports only)

- [ ] **Step 1: Edit engine + api host ports**

In `docker-compose.resona.yml`, change ONLY the left-hand (host) side of each `ports:` mapping. Container ports stay `:7001`/`:7000`.

- `engine-faster-whisper`: `"7001:7001"` → `"7720:7001"`
- `engine-whisper`: `"7002:7001"` → `"7722:7001"`
- `engine-voxtral`: `"7003:7001"` → `"7723:7001"`
- `engine-parakeet`: `"7004:7001"` → `"7721:7001"`
- `api`: `"7000:7000"` → `"7710:7000"`

Leave every `profiles:`, `healthcheck:` (which targets `localhost:7001`/`localhost:7000` *inside* the container), `RESONA_ENGINE_URLS` (uses service DNS names + container ports), and `depends_on` unchanged.

- [ ] **Step 2: Validate compose still parses**

Run: `docker compose -f docker-compose.resona.yml config >/dev/null && echo OK`
Expected: `OK` (no YAML/schema errors)

- [ ] **Step 3: Commit**

```bash
git add docker-compose.resona.yml
git commit -m "chore(compose): remap host ports to 7700-7800 range"
```

---

## Task 2: Add the Directus service to compose

**Files:**
- Modify: `docker-compose.resona.yml` (new `directus` service + volumes)

- [ ] **Step 1: Add the `directus` service**

Add under `services:` (before `volumes:`):

```yaml
  directus:
    image: directus/directus:11
    ports:
      - "7700:8055"
    volumes:
      - directus-db:/directus/database
      - directus-uploads:/directus/uploads
      - ./directus/schema-snapshot.yaml:/directus/schema-snapshot.yaml:ro
    environment:
      SECRET: ${DIRECTUS_SECRET:-change-me-in-env}
      ADMIN_EMAIL: ${DIRECTUS_ADMIN_EMAIL:-admin@example.com}
      ADMIN_PASSWORD: ${DIRECTUS_ADMIN_PASSWORD:-change-me}
      DB_CLIENT: sqlite3
      DB_FILENAME: /directus/database/data.db
      WEBSOCKETS_ENABLED: "true"
    healthcheck:
      test: ["CMD", "wget", "--spider", "-q", "http://localhost:8055/server/health"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 30s
    restart: unless-stopped
```

- [ ] **Step 2: Add the named volumes**

Under the existing `volumes:` block add:

```yaml
  directus-db:
  directus-uploads:
```

- [ ] **Step 3: Validate compose parses**

Run: `docker compose -f docker-compose.resona.yml config >/dev/null && echo OK`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add docker-compose.resona.yml
git commit -m "feat(compose): add Directus backend service on :7700"
```

---

## Task 3: Directus bootstrap doc + schema snapshot placeholder

This task is documentation + an infra artifact (not TDD — Directus schema is authored in the running instance, then exported). The snapshot file is generated once Directus is up; here we commit the bootstrap procedure and a placeholder so the path exists.

**Files:**
- Create: `directus/bootstrap.md`
- Create: `directus/schema-snapshot.yaml` (placeholder, regenerated by the operator)

- [ ] **Step 1: Write `directus/bootstrap.md`**

```markdown
# Directus bootstrap

One-time setup for the Resona platform backend. Assumes the `directus`
compose service is running (`docker compose -f docker-compose.resona.yml
--profile faster-whisper up directus`), reachable at http://localhost:7700.

## 1. Collections

Create two collections (Settings → Data Model), or apply the committed
snapshot (§4).

### `recordings`
| Field | Type | Notes |
|-------|------|-------|
| id | UUID | primary key |
| user_created | M2O → directus_users | "User Created" special field |
| date_created | timestamp | "Date Created" special field |
| title | string | |
| audio_file | M2O → directus_files | |
| duration_seconds | float | |
| language | string | default `de` |
| profile | string | default `default` |
| status | string (dropdown) | pending / transcribing / done / error; default `pending` |
| error_message | text | nullable |
| source | string (dropdown) | batch / live; default `batch` |

### `transcripts`
| Field | Type | Notes |
|-------|------|-------|
| id | UUID | primary key |
| recording | M2O → recordings | one per recording |
| text | text | |
| structured | json | nullable |
| segments | json | nullable |
| engine | string | |
| date_created / date_updated | timestamp | special fields |

## 2. Roles & access policy

Create a role **"user"** (non-admin). Add a policy with:
- `recordings`: read/create/update/delete where `user_created = $CURRENT_USER`
- `transcripts`: read/update/delete where `recording.user_created = $CURRENT_USER`;
  **create** validated against the same relational filter (parent recording
  owned by the current user)

The batch worker authenticates with a **static service token** (admin role),
which bypasses the user policy.

## 3. Service token for the worker

Create a dedicated user (admin role) "transcribe-worker" → generate a static
token (User detail → Token). Put it in `.env` as `DIRECTUS_TOKEN`.

## 4. Schema snapshot (reproducible deploys)

Export after authoring:

    docker compose -f docker-compose.resona.yml exec directus \
      npx directus schema snapshot --yes /directus/schema-snapshot.yaml

This writes to the bind-mounted `./directus/schema-snapshot.yaml`. Commit it.

Apply on a fresh instance:

    docker compose -f docker-compose.resona.yml exec directus \
      npx directus schema apply --yes /directus/schema-snapshot.yaml
```

- [ ] **Step 2: Create the snapshot placeholder**

Create `directus/schema-snapshot.yaml` with a single comment line so the bind
mount path exists before first export:

```yaml
# Placeholder — regenerate with `directus schema snapshot` after authoring the
# recordings/transcripts collections (see bootstrap.md §4). Commit the result.
```

- [ ] **Step 3: Commit**

```bash
git add directus/bootstrap.md directus/schema-snapshot.yaml
git commit -m "docs(directus): bootstrap procedure + schema snapshot placeholder"
```

---

## Task 4: Scaffold the `directus-transcribe` package

**Files:**
- Create: `packages/directus-transcribe/pyproject.toml`
- Create: `packages/directus-transcribe/README.md`
- Create: `packages/directus-transcribe/src/resona_directus_transcribe/__init__.py`
- Test: `packages/directus-transcribe/tests/test_import.py`

- [ ] **Step 1: Write the failing import test**

`packages/directus-transcribe/tests/test_import.py`:

```python
def test_package_imports():
    import resona_directus_transcribe
    assert resona_directus_transcribe.__version__ == "0.1.0"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest packages/directus-transcribe/tests/test_import.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'resona_directus_transcribe'`

- [ ] **Step 3: Write `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "resona-directus-transcribe"
version = "0.1.0"
description = "Glue worker: transcribes pending Directus recordings via resona-api"
readme = "README.md"
license = { text = "Apache-2.0" }
authors = [{ name = "mortegro", email = "mortegro@gmx.de" }]
requires-python = ">=3.12"
dependencies = [
    "httpx>=0.28.1",
    "python-decouple>=3.8",
]

[project.scripts]
resona-directus-transcribe = "resona_directus_transcribe.run:main"

[tool.hatch.build.targets.wheel]
packages = ["src/resona_directus_transcribe"]
```

- [ ] **Step 4: Write `__init__.py` and `README.md`**

`src/resona_directus_transcribe/__init__.py`:

```python
"""Glue worker that transcribes pending Directus recordings via resona-api."""

__version__ = "0.1.0"
```

`README.md`:

```markdown
# resona-directus-transcribe

Background worker that polls Directus for `recordings` with `status=pending`,
transcribes them through resona-api, and writes the result back. See
`docs/superpowers/specs/2026-06-17-pwa-directus-platform-design.md`.
```

- [ ] **Step 5: Sync the workspace and run the test**

Run: `uv sync --all-packages && uv run pytest packages/directus-transcribe/tests/test_import.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add packages/directus-transcribe uv.lock
git commit -m "feat(directus-transcribe): scaffold worker package"
```

---

## Task 5: `DirectusClient` — list & claim

**Files:**
- Create: `packages/directus-transcribe/src/resona_directus_transcribe/client.py`
- Create: `packages/directus-transcribe/tests/conftest.py`
- Test: `packages/directus-transcribe/tests/test_client.py`

- [ ] **Step 1: Write `conftest.py` fixtures**

```python
import pytest


@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    # Repo convention: drive async tests with anyio (no pytest-asyncio dep).
    return request.param


@pytest.fixture
def recording():
    return {
        "id": "rec-1",
        "title": "Befund",
        "audio_file": "file-1",
        "language": "de",
        "profile": "default",
        "status": "pending",
    }


@pytest.fixture
def base_url():
    return "http://directus.test"
```

- [ ] **Step 2: Write failing tests for `list_pending` and `claim`**

`tests/test_client.py`:

```python
import httpx
import pytest
import respx

from resona_directus_transcribe.client import DirectusClient

TOKEN = "svc-token"


@respx.mock
@pytest.mark.anyio
async def test_list_pending_filters_and_authenticates(base_url, recording):
    route = respx.get(f"{base_url}/items/recordings").mock(
        return_value=httpx.Response(200, json={"data": [recording]})
    )
    client = DirectusClient(base_url=base_url, token=TOKEN)
    result = await client.list_pending(limit=10)
    await client.aclose()

    assert result == [recording]
    req = route.calls.last.request
    assert req.headers["Authorization"] == f"Bearer {TOKEN}"
    assert req.url.params["filter[status][_eq]"] == "pending"
    assert req.url.params["limit"] == "10"


@respx.mock
@pytest.mark.anyio
async def test_claim_patches_status_to_transcribing(base_url):
    route = respx.patch(f"{base_url}/items/recordings/rec-1").mock(
        return_value=httpx.Response(200, json={"data": {"id": "rec-1", "status": "transcribing"}})
    )
    client = DirectusClient(base_url=base_url, token=TOKEN)
    ok = await client.claim("rec-1")
    await client.aclose()

    assert ok is True
    import json
    assert json.loads(route.calls.last.request.content) == {"status": "transcribing"}
```

> **Note:** later tests in this file reuse the same pattern — construct
> `DirectusClient(base_url=base_url, token=TOKEN)` inside the test and
> `await client.aclose()` before asserting. Do not introduce an async fixture
> (it would require pytest-asyncio).

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest packages/directus-transcribe/tests/test_client.py -v`
Expected: FAIL — `ImportError`/`ModuleNotFoundError` for `DirectusClient`

- [ ] **Step 4: Implement `client.py` (list + claim)**

```python
"""Async httpx wrapper around the Directus REST API used by the worker."""
from __future__ import annotations

import httpx


class DirectusClient:
    def __init__(self, base_url: str, token: str, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"Authorization": f"Bearer {token}"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def list_pending(self, limit: int = 10) -> list[dict]:
        resp = await self._client.get(
            f"{self.base_url}/items/recordings",
            params={
                "filter[status][_eq]": "pending",
                "limit": limit,
                "sort": "date_created",
            },
        )
        resp.raise_for_status()
        return resp.json()["data"]

    async def claim(self, recording_id: str) -> bool:
        """Atomically mark a recording as transcribing. Returns True on success."""
        resp = await self._client.patch(
            f"{self.base_url}/items/recordings/{recording_id}",
            json={"status": "transcribing"},
        )
        resp.raise_for_status()
        return resp.json()["data"]["status"] == "transcribing"
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest packages/directus-transcribe/tests/test_client.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add packages/directus-transcribe
git commit -m "feat(directus-transcribe): DirectusClient list_pending + claim"
```

---

## Task 6: `DirectusClient` — download, write transcript, mark done/error

**Files:**
- Modify: `packages/directus-transcribe/src/resona_directus_transcribe/client.py`
- Test: `packages/directus-transcribe/tests/test_client.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_client.py`:

```python
@respx.mock
@pytest.mark.anyio
async def test_download_audio_writes_temp_file(base_url, tmp_path):
    respx.get(f"{base_url}/assets/file-1").mock(
        return_value=httpx.Response(200, content=b"RIFFfake-wav-bytes")
    )
    client = DirectusClient(base_url=base_url, token=TOKEN)
    path = await client.download_audio("file-1", dest_dir=tmp_path)
    await client.aclose()
    assert path.exists()
    assert path.read_bytes() == b"RIFFfake-wav-bytes"


@respx.mock
@pytest.mark.anyio
async def test_write_transcript_posts_payload(base_url):
    route = respx.post(f"{base_url}/items/transcripts").mock(
        return_value=httpx.Response(200, json={"data": {"id": "t-1"}})
    )
    client = DirectusClient(base_url=base_url, token=TOKEN)
    await client.write_transcript(
        recording_id="rec-1", text="hallo", language="de",
        segments=[{"start": 0, "end": 1}], structured=None, engine="faster-whisper",
    )
    await client.aclose()
    import json
    body = json.loads(route.calls.last.request.content)
    assert body["recording"] == "rec-1"
    assert body["text"] == "hallo"
    assert body["engine"] == "faster-whisper"


@respx.mock
@pytest.mark.anyio
async def test_mark_done_and_error(base_url):
    done = respx.patch(f"{base_url}/items/recordings/rec-1").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    client = DirectusClient(base_url=base_url, token=TOKEN)
    await client.mark_done("rec-1")
    await client.mark_error("rec-1", "boom")
    await client.aclose()
    import json
    assert json.loads(done.calls[0].request.content) == {"status": "done"}
    err = json.loads(done.calls[1].request.content)
    assert err == {"status": "error", "error_message": "boom"}
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest packages/directus-transcribe/tests/test_client.py -v`
Expected: FAIL — missing methods

- [ ] **Step 3: Implement the methods**

Append to `DirectusClient` in `client.py` (add `from pathlib import Path` and `import uuid` at top):

```python
    async def download_audio(self, file_id: str, dest_dir: "Path") -> "Path":
        resp = await self._client.get(f"{self.base_url}/assets/{file_id}")
        resp.raise_for_status()
        dest = dest_dir / f"{file_id}-{uuid.uuid4().hex}.audio"
        dest.write_bytes(resp.content)
        return dest

    async def write_transcript(
        self, *, recording_id: str, text: str, language: str | None,
        segments: list | None, structured: dict | None, engine: str | None,
    ) -> None:
        resp = await self._client.post(
            f"{self.base_url}/items/transcripts",
            json={
                "recording": recording_id,
                "text": text,
                "segments": segments,
                "structured": structured,
                "engine": engine,
            },
        )
        resp.raise_for_status()

    async def mark_done(self, recording_id: str) -> None:
        resp = await self._client.patch(
            f"{self.base_url}/items/recordings/{recording_id}",
            json={"status": "done"},
        )
        resp.raise_for_status()

    async def mark_error(self, recording_id: str, message: str) -> None:
        resp = await self._client.patch(
            f"{self.base_url}/items/recordings/{recording_id}",
            json={"status": "error", "error_message": message[:1000]},
        )
        resp.raise_for_status()
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest packages/directus-transcribe/tests/test_client.py -v`
Expected: PASS (all client tests)

- [ ] **Step 5: Commit**

```bash
git add packages/directus-transcribe
git commit -m "feat(directus-transcribe): download, write transcript, mark done/error"
```

---

## Task 7: `TranscribeClient` — call resona-api

**Files:**
- Create: `packages/directus-transcribe/src/resona_directus_transcribe/transcribe.py`
- Test: `packages/directus-transcribe/tests/test_transcribe.py`

- [ ] **Step 1: Write failing test**

`tests/test_transcribe.py`:

```python
import httpx
import pytest
import respx

from resona_directus_transcribe.transcribe import TranscribeClient

API = "http://resona-api.test"


@respx.mock
@pytest.mark.anyio
async def test_transcribe_posts_multipart_verbose_json(tmp_path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFFfake")
    route = respx.post(f"{API}/v1/audio/transcriptions").mock(
        return_value=httpx.Response(200, json={
            "text": "hallo welt", "language": "de",
            "segments": [{"start": 0, "end": 1}],
        })
    )
    client = TranscribeClient(base_url=API, api_key="")
    result = await client.transcribe(audio, language="de", profile="default")
    await client.aclose()

    assert result["text"] == "hallo welt"
    assert result["language"] == "de"
    assert result["segments"] == [{"start": 0, "end": 1}]
    # structured absent in response → defaults to None
    assert result["structured"] is None

    req = route.calls.last.request
    assert b'name="response_format"' in req.content
    assert b"verbose_json" in req.content
    assert b'name="language"' in req.content
    assert b'name="profile"' in req.content
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest packages/directus-transcribe/tests/test_transcribe.py -v`
Expected: FAIL — `ModuleNotFoundError` for `transcribe`

- [ ] **Step 3: Implement `transcribe.py`**

```python
"""Async client for the resona-api OpenAI-compatible transcription route."""
from __future__ import annotations

from pathlib import Path

import httpx


class TranscribeClient:
    def __init__(self, base_url: str, api_key: str = "", timeout: float = 3600.0):
        self.base_url = base_url.rstrip("/")
        headers = {"X-API-Key": api_key} if api_key else {}
        self._client = httpx.AsyncClient(timeout=timeout, headers=headers)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def transcribe(
        self, audio_path: Path, *, language: str = "de",
        profile: str = "default", engine: str | None = None,
    ) -> dict:
        data = {
            "response_format": "verbose_json",
            "language": language,
            "profile": profile,
        }
        if engine:
            data["engine"] = engine
        with open(audio_path, "rb") as f:
            resp = await self._client.post(
                f"{self.base_url}/v1/audio/transcriptions",
                files={"file": (audio_path.name, f, "audio/wav")},
                data=data,
            )
        resp.raise_for_status()
        body = resp.json()
        return {
            "text": body.get("text", ""),
            "language": body.get("language"),
            "segments": body.get("segments"),
            "structured": body.get("structured"),  # best-effort; route omits it today
            "engine": engine,
        }
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest packages/directus-transcribe/tests/test_transcribe.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/directus-transcribe
git commit -m "feat(directus-transcribe): TranscribeClient calls resona-api"
```

---

## Task 8: `process_one` — orchestrate one recording (happy + error paths)

**Files:**
- Create: `packages/directus-transcribe/src/resona_directus_transcribe/worker.py`
- Test: `packages/directus-transcribe/tests/test_worker.py`

- [ ] **Step 1: Write failing tests (happy + error + cleanup)**

`tests/test_worker.py`:

```python
import httpx
import pytest
import respx

from resona_directus_transcribe.client import DirectusClient
from resona_directus_transcribe.transcribe import TranscribeClient
from resona_directus_transcribe.worker import process_one

D = "http://directus.test"
A = "http://api.test"


def _clients():
    return (
        DirectusClient(base_url=D, token="t"),
        TranscribeClient(base_url=A, api_key=""),
    )


@respx.mock
@pytest.mark.anyio
async def test_process_one_happy_path(tmp_path, recording):
    respx.get(f"{D}/assets/file-1").mock(return_value=httpx.Response(200, content=b"RIFF"))
    respx.post(f"{A}/v1/audio/transcriptions").mock(
        return_value=httpx.Response(200, json={"text": "hi", "language": "de", "segments": []})
    )
    tx = respx.post(f"{D}/items/transcripts").mock(return_value=httpx.Response(200, json={"data": {}}))
    patch = respx.patch(f"{D}/items/recordings/rec-1").mock(return_value=httpx.Response(200, json={"data": {}}))

    d, a = _clients()
    await process_one(recording, d, a, tmp_dir=tmp_path)
    await d.aclose(); await a.aclose()

    assert tx.called
    import json
    assert json.loads(patch.calls.last.request.content) == {"status": "done"}
    # temp audio cleaned up
    assert list(tmp_path.iterdir()) == []


@respx.mock
@pytest.mark.anyio
async def test_process_one_marks_error_on_api_failure(tmp_path, recording):
    respx.get(f"{D}/assets/file-1").mock(return_value=httpx.Response(200, content=b"RIFF"))
    respx.post(f"{A}/v1/audio/transcriptions").mock(return_value=httpx.Response(500))
    patch = respx.patch(f"{D}/items/recordings/rec-1").mock(return_value=httpx.Response(200, json={"data": {}}))

    d, a = _clients()
    await process_one(recording, d, a, tmp_dir=tmp_path)
    await d.aclose(); await a.aclose()

    import json
    body = json.loads(patch.calls.last.request.content)
    assert body["status"] == "error"
    assert "error_message" in body
    assert list(tmp_path.iterdir()) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest packages/directus-transcribe/tests/test_worker.py -v`
Expected: FAIL — `process_one` missing

- [ ] **Step 3: Implement `process_one` in `worker.py`**

```python
"""Worker orchestration: poll loop + per-recording processing."""
from __future__ import annotations

import logging
from pathlib import Path

from .client import DirectusClient
from .transcribe import TranscribeClient

log = logging.getLogger(__name__)


async def process_one(
    recording: dict, directus: DirectusClient, transcribe: TranscribeClient,
    *, tmp_dir: Path,
) -> None:
    """Download → transcribe → write back. Marks error on any failure."""
    rec_id = recording["id"]
    audio_path: Path | None = None
    try:
        audio_path = await directus.download_audio(recording["audio_file"], dest_dir=tmp_dir)
        result = await transcribe.transcribe(
            audio_path,
            language=recording.get("language") or "de",
            profile=recording.get("profile") or "default",
        )
        await directus.write_transcript(
            recording_id=rec_id,
            text=result["text"],
            language=result["language"],
            segments=result["segments"],
            structured=result["structured"],
            engine=result["engine"],
        )
        await directus.mark_done(rec_id)
        log.info("transcribed recording %s", rec_id)
    except Exception as exc:  # noqa: BLE001 — worker must never crash on one job
        log.exception("failed to transcribe recording %s", rec_id)
        try:
            await directus.mark_error(rec_id, f"{type(exc).__name__}: {exc}")
        except Exception:
            log.exception("could not mark recording %s as error", rec_id)
    finally:
        if audio_path is not None and audio_path.exists():
            audio_path.unlink()
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest packages/directus-transcribe/tests/test_worker.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add packages/directus-transcribe
git commit -m "feat(directus-transcribe): process_one orchestration with error handling"
```

---

## Task 9: Poll loop + bounded concurrency + stale-claim recovery

**Files:**
- Modify: `packages/directus-transcribe/src/resona_directus_transcribe/client.py` (add `reclaim_stale`)
- Modify: `packages/directus-transcribe/src/resona_directus_transcribe/worker.py` (add `run_once`)
- Test: `packages/directus-transcribe/tests/test_worker.py`, `tests/test_client.py`

- [ ] **Step 1: Write failing test for `reclaim_stale`**

Append to `tests/test_client.py`:

```python
@respx.mock
@pytest.mark.anyio
async def test_reclaim_stale_resets_old_transcribing(base_url):
    respx.get(f"{base_url}/items/recordings").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "rec-old"}]})
    )
    patch = respx.patch(f"{base_url}/items/recordings/rec-old").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    client = DirectusClient(base_url=base_url, token=TOKEN)
    n = await client.reclaim_stale(older_than_minutes=15)
    await client.aclose()
    assert n == 1
    import json
    assert json.loads(patch.calls.last.request.content) == {"status": "pending"}
```

- [ ] **Step 2: Write failing test for `run_once` (claims then processes, respects concurrency)**

Append to `tests/test_worker.py`:

```python
@respx.mock
@pytest.mark.anyio
async def test_run_once_claims_and_processes_each(tmp_path):
    recs = [{"id": f"rec-{i}", "audio_file": f"f-{i}", "language": "de", "profile": "default"}
            for i in range(3)]
    # stale sweep (no stale)
    respx.get(f"{D}/items/recordings").mock(side_effect=[
        httpx.Response(200, json={"data": []}),       # reclaim_stale sweep
        httpx.Response(200, json={"data": recs}),     # list_pending
    ])
    for r in recs:
        respx.patch(f"{D}/items/recordings/{r['id']}").mock(return_value=httpx.Response(200, json={"data": {"status": "transcribing"}}))
        respx.get(f"{D}/assets/{r['audio_file']}").mock(return_value=httpx.Response(200, content=b"RIFF"))
    respx.post(f"{A}/v1/audio/transcriptions").mock(return_value=httpx.Response(200, json={"text": "x", "language": "de", "segments": []}))
    tx = respx.post(f"{D}/items/transcripts").mock(return_value=httpx.Response(200, json={"data": {}}))

    d, a = _clients()
    processed = await run_once(d, a, tmp_dir=tmp_path, concurrency=2, stale_minutes=15)
    await d.aclose(); await a.aclose()

    assert processed == 3
    assert tx.call_count == 3
```

Add `from resona_directus_transcribe.worker import run_once` to the imports.

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest packages/directus-transcribe/tests -v`
Expected: FAIL — `reclaim_stale` / `run_once` missing

- [ ] **Step 4: Implement `reclaim_stale` in `client.py`**

```python
    async def reclaim_stale(self, older_than_minutes: int = 15) -> int:
        """Reset recordings stuck in `transcribing` back to `pending`.

        Uses Directus relative-date filtering. Returns count reset.
        """
        resp = await self._client.get(
            f"{self.base_url}/items/recordings",
            params={
                "filter[status][_eq]": "transcribing",
                "filter[date_updated][_lt]": f"$NOW(-{older_than_minutes} minutes)",
                "limit": 100,
            },
        )
        resp.raise_for_status()
        stale = resp.json()["data"]
        for rec in stale:
            r = await self._client.patch(
                f"{self.base_url}/items/recordings/{rec['id']}",
                json={"status": "pending"},
            )
            r.raise_for_status()
        return len(stale)
```

- [ ] **Step 5: Implement `run_once` in `worker.py`**

```python
import asyncio


async def run_once(
    directus: DirectusClient, transcribe: TranscribeClient, *,
    tmp_dir: Path, concurrency: int = 2, stale_minutes: int = 15,
) -> int:
    """One poll cycle: reclaim stale, claim pending, process with a concurrency cap."""
    await directus.reclaim_stale(older_than_minutes=stale_minutes)
    pending = await directus.list_pending(limit=concurrency * 5)

    sem = asyncio.Semaphore(concurrency)

    async def _guarded(rec: dict) -> bool:
        async with sem:
            if not await directus.claim(rec["id"]):
                return False
            await process_one(rec, directus, transcribe, tmp_dir=tmp_dir)
            return True

    results = await asyncio.gather(*(_guarded(r) for r in pending))
    return sum(1 for r in results if r)
```

- [ ] **Step 6: Run to verify pass**

Run: `uv run pytest packages/directus-transcribe/tests -v`
Expected: PASS (all tests)

- [ ] **Step 7: Commit**

```bash
git add packages/directus-transcribe
git commit -m "feat(directus-transcribe): poll cycle, concurrency cap, stale-claim recovery"
```

---

## Task 10: `run.py` entry point + config

**Files:**
- Create: `packages/directus-transcribe/src/resona_directus_transcribe/run.py`
- Test: `packages/directus-transcribe/tests/test_run.py`

- [ ] **Step 1: Write failing test for config loading**

`tests/test_run.py`:

```python
import pytest

from resona_directus_transcribe import run


def test_load_settings_reads_env(monkeypatch):
    monkeypatch.setenv("DIRECTUS_URL", "http://d:7700")
    monkeypatch.setenv("DIRECTUS_TOKEN", "tok")
    monkeypatch.setenv("RESONA_API_URL", "http://a:7710")
    monkeypatch.setenv("TRANSCRIBE_POLL_INTERVAL", "9")
    monkeypatch.setenv("TRANSCRIBE_CONCURRENCY", "4")
    s = run.load_settings()
    assert s.directus_url == "http://d:7700"
    assert s.directus_token == "tok"
    assert s.resona_api_url == "http://a:7710"
    assert s.poll_interval == 9
    assert s.concurrency == 4


def test_load_settings_requires_token(monkeypatch):
    monkeypatch.delenv("DIRECTUS_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="DIRECTUS_TOKEN"):
        run.load_settings()
```

Note: `python-decouple`'s `config()` reads process env + a `.env` file. In
tests we set real env vars; `config()` picks them up. To avoid a stray repo
`.env` interfering, the test asserts on values it sets explicitly.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest packages/directus-transcribe/tests/test_run.py -v`
Expected: FAIL — `load_settings` missing

- [ ] **Step 3: Implement `run.py`**

```python
"""Entry point: load config, then run the async poll loop forever."""
from __future__ import annotations

import asyncio
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

from decouple import config

from .client import DirectusClient
from .transcribe import TranscribeClient
from .worker import run_once

log = logging.getLogger(__name__)


@dataclass
class Settings:
    directus_url: str
    directus_token: str
    resona_api_url: str
    resona_api_key: str
    poll_interval: int
    concurrency: int
    stale_minutes: int


def load_settings() -> Settings:
    token = config("DIRECTUS_TOKEN", default="")
    if not token:
        raise RuntimeError("DIRECTUS_TOKEN is required")
    return Settings(
        directus_url=config("DIRECTUS_URL", default="http://localhost:7700"),
        directus_token=token,
        resona_api_url=config("RESONA_API_URL", default="http://localhost:7710"),
        resona_api_key=config("RESONA_API_KEY", default=""),
        poll_interval=config("TRANSCRIBE_POLL_INTERVAL", default=5, cast=int),
        concurrency=config("TRANSCRIBE_CONCURRENCY", default=2, cast=int),
        stale_minutes=config("TRANSCRIBE_STALE_MINUTES", default=15, cast=int),
    )


async def _loop(settings: Settings) -> None:
    directus = DirectusClient(settings.directus_url, settings.directus_token)
    transcribe = TranscribeClient(settings.resona_api_url, settings.resona_api_key)
    tmp_dir = Path(tempfile.mkdtemp(prefix="resona-transcribe-"))
    log.info("directus-transcribe worker started (poll=%ss, concurrency=%s)",
             settings.poll_interval, settings.concurrency)
    try:
        while True:
            try:
                n = await run_once(
                    directus, transcribe, tmp_dir=tmp_dir,
                    concurrency=settings.concurrency, stale_minutes=settings.stale_minutes,
                )
                if n:
                    log.info("processed %s recording(s)", n)
            except Exception:
                log.exception("poll cycle failed; continuing")
            await asyncio.sleep(settings.poll_interval)
    finally:
        await directus.aclose()
        await transcribe.aclose()


def main() -> None:
    logging.basicConfig(
        level=config("LOGLEVEL", default="info").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(_loop(load_settings()))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest packages/directus-transcribe/tests/test_run.py -v`
Expected: PASS

- [ ] **Step 5: Run the full package test suite**

Run: `uv run pytest packages/directus-transcribe/tests -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add packages/directus-transcribe
git commit -m "feat(directus-transcribe): config loader + asyncio poll-loop entry point"
```

---

## Task 11: Add the worker service to compose + document env

**Files:**
- Modify: `docker-compose.resona.yml`
- Create: `packages/directus-transcribe/Dockerfile`
- Create/modify: `.env.example`

- [ ] **Step 1: Write the Dockerfile**

`packages/directus-transcribe/Dockerfile`:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock* ./
COPY packages/directus-transcribe/ ./packages/directus-transcribe/
RUN uv sync --package resona-directus-transcribe --frozen --no-dev
CMD ["uv", "run", "resona-directus-transcribe"]
```

- [ ] **Step 2: Add the `directus-transcribe` compose service**

Add under `services:`:

```yaml
  directus-transcribe:
    build:
      context: .
      dockerfile: packages/directus-transcribe/Dockerfile
    environment:
      DIRECTUS_URL: http://directus:8055
      DIRECTUS_TOKEN: ${DIRECTUS_TOKEN}
      RESONA_API_URL: http://api:7000
      RESONA_API_KEY: ${RESONA_API_KEY:-}
      TRANSCRIBE_POLL_INTERVAL: "5"
      TRANSCRIBE_CONCURRENCY: "2"
      LOGLEVEL: info
    depends_on:
      directus:
        condition: service_healthy
      api:
        condition: service_healthy
    restart: unless-stopped
```

Note: inside the compose network the worker reaches services by DNS name +
**container** port (`directus:8055`, `api:7000`), not the remapped host ports.

- [ ] **Step 3: Document env vars**

Append to `.env.example` (create if it doesn't exist):

```bash
# ── Directus backend ─────────────────────────────────────────────
DIRECTUS_SECRET=change-me
DIRECTUS_ADMIN_EMAIL=admin@example.com
DIRECTUS_ADMIN_PASSWORD=change-me
# Static service token for the transcribe worker (see directus/bootstrap.md §3)
DIRECTUS_TOKEN=

# ── directus-transcribe worker ───────────────────────────────────
TRANSCRIBE_POLL_INTERVAL=5
TRANSCRIBE_CONCURRENCY=2
TRANSCRIBE_STALE_MINUTES=15
```

- [ ] **Step 4: Validate compose parses**

Run: `docker compose -f docker-compose.resona.yml config >/dev/null && echo OK`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add docker-compose.resona.yml packages/directus-transcribe/Dockerfile .env.example
git commit -m "feat(compose): add directus-transcribe worker service + env docs"
```

---

## Task 12: Final verification

- [ ] **Step 1: Full worker test suite passes**

Run: `uv run pytest packages/directus-transcribe/tests -v`
Expected: PASS, no warnings about unawaited coroutines.

- [ ] **Step 2: Whole-repo tests still green**

Run: `uv run pytest`
Expected: PASS (no regressions in existing packages).

- [ ] **Step 3: Compose config is valid**

Run: `docker compose -f docker-compose.resona.yml config >/dev/null && echo OK`
Expected: `OK`

- [ ] **Step 4: Manual smoke (optional, requires Docker + GPU/CPU engine)**

```bash
# bring up directus + api + a CPU engine
docker compose -f docker-compose.resona.yml -f docker-compose.cpu.yml \
  --profile faster-whisper up -d directus engine-faster-whisper api
# author schema in the UI (bootstrap.md), export snapshot, set DIRECTUS_TOKEN in .env
docker compose -f docker-compose.resona.yml up -d directus-transcribe
# upload a recording row + audio via Directus, watch it move pending → done
docker compose -f docker-compose.resona.yml logs -f directus-transcribe
```

Expected: a `pending` recording transitions to `done` with a `transcripts` row.

- [ ] **Step 5: Update CLAUDE.md package list**

Add to the `packages/` tree and "Package responsibilities" sections in
`CLAUDE.md`: `directus-transcribe/ ← resona-directus-transcribe: glue worker
that polls Directus for pending recordings and transcribes them via resona-api`.

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude): document directus-transcribe package"
```

---

## Notes for the implementer

- **TDD throughout:** every worker module has a failing test first. The only
  non-TDD tasks are infra (compose, Directus schema authoring) which are
  verified with `docker compose config` and a manual smoke.
- **Async tests use `anyio`, NOT `pytest-asyncio`:** the repo standard (see
  `packages/engine-server/tests/conftest.py`) is `@pytest.mark.anyio` + an
  `anyio_backend` fixture; `anyio` is already available transitively via httpx.
  Do **not** add `pytest-asyncio`. Async fixtures are avoided — tests build and
  `aclose()` their clients inline. If an `async def` test errors with "no plugin
  or hook that handled it", the `anyio_backend` fixture is missing from that
  package's `conftest.py`.
- **`structured` is best-effort:** the transcription route returns it only for
  profiles with an `extract` step. The worker stores whatever the response
  contains (`None` otherwise). No API change needed.
- **Service token bypasses RLS:** the worker uses an admin static token, so it
  can write `transcripts` for any recording. The per-user access policy only
  governs the PWA's direct access (live path), covered in the frontend plan.
- **No audio deletion:** the worker deletes only its own temp download, never
  the Directus-stored asset (honors the repo's "do not delete audio" rule).
```
