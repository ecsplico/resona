# Decouple Directus from Resona — PWA owns its data layer

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move Directus and the `directus-transcribe` worker out of the resona monorepo into the `resona-pwa` repo, so resona becomes a pure, Directus-agnostic transcription backend (api + engines) consumed only over HTTP.

**Architecture:** Two stacks, two repos. **resona** (monorepo) ships `docker-compose.resona.yml` with only `api` (:7710) + engines (:7720…) — it knows nothing about Directus. **resona-pwa** ships its own `docker-compose.yml` with `directus` (:7700) + the `directus-transcribe` worker, plus the Directus schema/bootstrap it depends on. The worker (standalone Python: only `httpx` + `python-decouple`, zero `resona_*` imports) bridges the PWA's Directus → resona-api over the network. The browser talks to Directus (auth/data/files) and to resona-api (live `/v1/listen`); the worker talks to both for the batch path.

**Tech Stack:** Docker Compose, Directus 11 (official image), Python 3.12 + uv (worker), httpx, python-decouple. PWA repo is otherwise bun/Nuxt/TypeScript — the worker is isolated under `worker/` with its own toolchain.

**Repos & paths:**
- **resona monorepo (worktree):** `/home/matthias/emdash/worktrees/resona/emdash/afraid-ghosts-feel-7335d` — commit on the current branch.
- **resona-pwa:** `/home/matthias/workBench/resona-pwa` — commits on `main`, push to `origin` (`ecsplico/resona-pwa`). End commit messages with the `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` trailer.

**Ordering rule:** Do **Phase 1** (stand up the PWA data layer) entirely before **Phase 2** (cut Directus + worker from resona). Never leave a window where neither stack owns Directus.

**Source of truth for the worker (copy 1:1, do not re-derive):**
- `packages/directus-transcribe/src/resona_directus_transcribe/{__init__,run,client,transcribe,worker}.py`
- `packages/directus-transcribe/tests/{conftest,test_import,test_run,test_client,test_transcribe,test_worker}.py`
- `packages/directus-transcribe/pyproject.toml`, `README.md`

---

## Phase 1 — resona-pwa: stand up the data layer

### Task 1: Lift the worker source into `resona-pwa/worker/`

The worker is standalone (deps: `httpx`, `python-decouple`; no `resona_*` imports), so this is a clean copy — no workspace surgery.

**Files (in `resona-pwa`):**
- Create: `worker/pyproject.toml` (copied; unchanged contents)
- Create: `worker/README.md` (copied)
- Create: `worker/src/resona_directus_transcribe/{__init__,run,client,transcribe,worker}.py` (copied verbatim)
- Create: `worker/tests/{conftest,test_import,test_run,test_client,test_transcribe,test_worker}.py` (copied verbatim)
- Create: `worker/Dockerfile` (new — standalone build, see Step 4)
- Create: `worker/.python-version` (optional: `3.12`)

- [ ] **Step 1: Copy the worker package tree into `worker/`**

```bash
cd /home/matthias/workBench/resona-pwa
mkdir -p worker
cp -R /home/matthias/emdash/worktrees/resona/emdash/afraid-ghosts-feel-7335d/packages/directus-transcribe/src worker/src
cp -R /home/matthias/emdash/worktrees/resona/emdash/afraid-ghosts-feel-7335d/packages/directus-transcribe/tests worker/tests
cp /home/matthias/emdash/worktrees/resona/emdash/afraid-ghosts-feel-7335d/packages/directus-transcribe/pyproject.toml worker/pyproject.toml
cp /home/matthias/emdash/worktrees/resona/emdash/afraid-ghosts-feel-7335d/packages/directus-transcribe/README.md worker/README.md
# strip any __pycache__ that rode along in the copy so it doesn't land in the first commit
find worker -name __pycache__ -prune -exec rm -rf {} +
```

- [ ] **Step 2: Verify the package has no monorepo coupling**

Run: `grep -rn "resona_asr\|resona_api\|resona_client\|workspace = true" worker/`
Expected: no output (confirms standalone; the only deps are `httpx` + `python-decouple` in `worker/pyproject.toml`).

- [ ] **Step 3: Install deps and run the worker test suite in isolation**

Run:
```bash
cd /home/matthias/workBench/resona-pwa/worker
uv sync --extra dev 2>/dev/null || uv sync
uv run --with pytest --with respx pytest -q
```
Expected: all worker tests PASS (same suite that passed in the monorepo). The tests use `@pytest.mark.anyio` + an `anyio_backend` fixture (see `tests/conftest.py`) — the `anyio` pytest plugin ships transitively with `httpx>=0.28`, so **do not** add `pytest-asyncio` (it would be unused). If a clean dev install is wanted, add `pytest` + `respx` under `[dependency-groups] dev = [...]` in `worker/pyproject.toml` and commit that as part of Step 6.

- [ ] **Step 4: Write the standalone `worker/Dockerfile`**

The monorepo Dockerfile built from the workspace root with `uv sync --package …`. Standalone version builds from `worker/` itself:

```dockerfile
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
RUN pip install --no-cache-dir uv
# README.md is required: pyproject.toml declares `readme = "README.md"`, so uv sync
# aborts without it.
COPY pyproject.toml uv.lock* README.md ./
COPY src/ ./src/
RUN uv sync --frozen --no-dev || uv sync --no-dev
CMD ["uv", "run", "resona-directus-transcribe"]
```

- [ ] **Step 5: Generate a lockfile for reproducible builds**

Run:
```bash
cd /home/matthias/workBench/resona-pwa/worker
uv lock
```
Expected: `worker/uv.lock` created.

- [ ] **Step 6: Commit**

```bash
cd /home/matthias/workBench/resona-pwa
git add worker/
git commit -m "feat(worker): lift standalone directus-transcribe worker into the PWA repo"
```

---

### Task 2: Move the Directus schema + bootstrap into `resona-pwa`

The worker and the PWA both depend on the Directus collection schema; co-locate it with them.

**Files (in `resona-pwa`):**
- Create: `directus/bootstrap.md` (copied from monorepo)
- Create: `directus/schema-snapshot.yaml` (copied from monorepo)

- [ ] **Step 1: Copy the directus dir**

```bash
cd /home/matthias/workBench/resona-pwa
mkdir -p directus
cp /home/matthias/emdash/worktrees/resona/emdash/afraid-ghosts-feel-7335d/directus/bootstrap.md directus/bootstrap.md
cp /home/matthias/emdash/worktrees/resona/emdash/afraid-ghosts-feel-7335d/directus/schema-snapshot.yaml directus/schema-snapshot.yaml
```

- [ ] **Step 2: Fix the compose command in `directus/bootstrap.md`**

The bootstrap doc references `docker compose -f docker-compose.resona.yml … directus`. Update every such reference to the PWA stack's compose:
- `docker compose -f docker-compose.resona.yml --profile faster-whisper up directus` → `docker compose up -d directus`
- `docker compose -f docker-compose.resona.yml exec directus …` → `docker compose exec directus …`

Run: `grep -n "docker-compose.resona.yml" directus/bootstrap.md`
Expected: no matches after editing.

- [ ] **Step 3: Commit**

```bash
git add directus/
git commit -m "docs(directus): move bootstrap + schema snapshot into the PWA repo"
```

---

### Task 3: Author the PWA stack's `docker-compose.yml` + `.env.example`

**Files (in `resona-pwa`):**
- Create: `docker-compose.yml`
- Create: `.env.stack.example` (stack/backend env — kept separate from the existing frontend `.env.example` which holds `NUXT_PUBLIC_*`)

- [ ] **Step 1: Write `docker-compose.yml`**

```yaml
# PWA data layer: Directus (auth/data/files) + the directus-transcribe worker.
# Resona (api + engines) runs as a SEPARATE stack from the resona monorepo and is
# reached over the network via RESONA_API_URL below.
services:
  directus:
    image: directus/directus:11
    ports:
      - "7700:8055"
    volumes:
      - directus-db:/directus/database
      - directus-uploads:/directus/uploads
      # writable: `directus schema snapshot` (directus/bootstrap.md §4) exports here
      - ./directus/schema-snapshot.yaml:/directus/schema-snapshot.yaml
    environment:
      SECRET: ${DIRECTUS_SECRET:-change-me-in-env}
      ADMIN_EMAIL: ${DIRECTUS_ADMIN_EMAIL:-admin@example.com}
      ADMIN_PASSWORD: ${DIRECTUS_ADMIN_PASSWORD:-change-me}
      DB_CLIENT: sqlite3
      DB_FILENAME: /directus/database/data.db
      WEBSOCKETS_ENABLED: "true"
      # CORS so the browser PWA origin may call Directus directly.
      CORS_ENABLED: "true"
      CORS_ORIGIN: ${PWA_ORIGIN:-http://localhost:3000}
    healthcheck:
      test: ["CMD", "wget", "--spider", "-q", "http://localhost:8055/server/health"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 30s
    restart: unless-stopped

  directus-transcribe:
    build:
      context: ./worker
      dockerfile: Dockerfile
    environment:
      DIRECTUS_URL: http://directus:8055
      DIRECTUS_TOKEN: ${DIRECTUS_TOKEN}
      # resona-api lives in the separate resona stack — point this at its reachable URL.
      RESONA_API_URL: ${RESONA_API_URL:-http://host.docker.internal:7710}
      RESONA_API_KEY: ${RESONA_API_KEY:-}
      TRANSCRIBE_POLL_INTERVAL: ${TRANSCRIBE_POLL_INTERVAL:-5}
      TRANSCRIBE_CONCURRENCY: ${TRANSCRIBE_CONCURRENCY:-2}
      TRANSCRIBE_STALE_MINUTES: ${TRANSCRIBE_STALE_MINUTES:-15}
      LOGLEVEL: ${LOGLEVEL:-info}
    extra_hosts:
      # lets the container reach a resona-api running on the docker host (Linux)
      - "host.docker.internal:host-gateway"
    depends_on:
      directus:
        condition: service_healthy
    restart: unless-stopped

volumes:
  directus-db:
  directus-uploads:
```

- [ ] **Step 2: Write `.env.stack.example`**

```bash
# ─── resona-pwa backend stack (docker-compose.yml) ───────────────────────
# Copy to `.env` before `docker compose up`. (Frontend NUXT_PUBLIC_* vars live
# in .env.example — these are the Directus + worker stack vars.)

# Directus
DIRECTUS_SECRET=change-me
DIRECTUS_ADMIN_EMAIL=admin@example.com
DIRECTUS_ADMIN_PASSWORD=change-me
# Browser origin allowed by Directus CORS (the running PWA).
PWA_ORIGIN=http://localhost:3000

# Static service token for the worker (see directus/bootstrap.md §3).
DIRECTUS_TOKEN=

# directus-transcribe worker → resona-api (the separate resona stack).
RESONA_API_URL=http://host.docker.internal:7710
RESONA_API_KEY=
TRANSCRIBE_POLL_INTERVAL=5
TRANSCRIBE_CONCURRENCY=2
TRANSCRIBE_STALE_MINUTES=15
LOGLEVEL=info
```

- [ ] **Step 3: Confirm `.gitignore` excludes `.env`**

Run: `grep -nE '^\.env$|^\.env\b' /home/matthias/workBench/resona-pwa/.gitignore`
Expected: `.env` is ignored. (It already is — only `.env.example` is tracked. Do **not** commit a real `.env`.)

- [ ] **Step 4: Validate compose config parses**

Run: `cd /home/matthias/workBench/resona-pwa && cp .env.stack.example .env.tmp && docker compose --env-file .env.tmp config >/dev/null && rm .env.tmp && echo OK`
Expected: `OK` (no YAML/interpolation errors).

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml .env.stack.example
git commit -m "feat(stack): PWA-owned Directus + worker compose stack"
```

---

### Task 4: Bring up the PWA data layer and verify end-to-end

This is the integration gate. Requires a reachable resona-api (run the resona stack, still un-decoupled at this point — Phase 2 hasn't run yet, so the monorepo `api` is available on :7710).

- [ ] **Step 1: Start resona-api (separate stack, from the monorepo)**

```bash
cd <resona-monorepo-root>
docker compose -f docker-compose.resona.yml --profile faster-whisper up -d api engine-faster-whisper
```
Expected: `api` healthy on :7710. (We only need `api` reachable; the monorepo `directus`/worker are irrelevant here and will be removed in Phase 2.)

- [ ] **Step 2: Start the PWA data layer**

```bash
cd /home/matthias/workBench/resona-pwa
cp .env.stack.example .env   # then edit: set DIRECTUS_SECRET, admin creds, RESONA_API_URL
docker compose up -d directus
```
Expected: `directus` becomes healthy.

- [ ] **Step 3: Bootstrap Directus (one-time)**

Follow `directus/bootstrap.md`: apply the schema snapshot, create the `user` role + ownership policy, create the `transcribe-worker` admin user, generate its static token, put it in `.env` as `DIRECTUS_TOKEN`.

Run: `curl -s http://localhost:7700/server/health`
Expected: `{"status":"ok"}`

- [ ] **Step 4: Start the worker now that the token exists**

```bash
docker compose up -d directus-transcribe
docker compose logs directus-transcribe | tail -20
```
Expected: log line `directus-transcribe worker started (poll=5s, concurrency=2)` and no crash loop.

- [ ] **Step 5: End-to-end batch check**

With the PWA dev server running (`bun run dev`), log in, record/upload a short clip with transcribe on. Watch the recording row go `pending` → (worker claims) `transcribing` → `done`, with a transcript attached.
Expected: terminal state `done`, transcript text present. (If it stays `pending`, the worker can't reach Directus or resona-api — check `RESONA_API_URL` and `DIRECTUS_TOKEN`.)

- [ ] **Step 6: No commit** (runtime verification only). Record the result in the task notes.

---

### Task 5: Update `resona-pwa` README + CLAUDE.md for the new stack

**Files (in `resona-pwa`):**
- Modify: `README.md` (the Quick start "Backend" section now describes the PWA's own stack + a separate resona transcription backend)
- Modify: `CLAUDE.md` (How to start; note the worker now lives in `worker/`)

- [ ] **Step 1: Rewrite README "Quick start → Backend"**

Replace the single monorepo compose command with two stacks:
1. **Transcription backend (resona monorepo):** `docker compose -f docker-compose.resona.yml --profile faster-whisper up -d` → api :7710 + engine :7720 (no Directus anymore).
2. **PWA data layer (this repo):** `cp .env.stack.example .env` (fill in), bootstrap Directus per `directus/bootstrap.md`, then `docker compose up -d` → directus :7700 + worker.
3. **Frontend (this repo):** `bun install && cp .env.example .env && bun run dev` → :3000.

Update the services/ports table accordingly: Directus + worker are now in *this* repo's stack; resona-api is the remote transcription service.

- [ ] **Step 2: Update CLAUDE.md**

In "How to start", reflect the two-stack split. Add a short "Worker" subsection: the `directus-transcribe` worker lives in `worker/` (standalone Python, `httpx`+`python-decouple`, no monorepo coupling); it bridges this repo's Directus → the remote resona-api; build/run via `docker compose`. Keep the existing schema/`engine`-on-transcripts and live-WS notes.

- [ ] **Step 3: Commit and push**

```bash
git add README.md CLAUDE.md
git commit -m "docs: document the two-stack split (PWA data layer + remote resona)"
git push origin main
```

---

## Phase 2 — resona monorepo: remove Directus + the worker

Only after Phase 1 is verified (Task 4 green). All steps run in the monorepo worktree; commit on the current branch.

### Task 6: Drop `directus` + `directus-transcribe` from `docker-compose.resona.yml`

**Files:**
- Modify: `docker-compose.resona.yml` (remove the `directus` service lines 139–161, the `directus-transcribe` service lines 163–180, and the `directus-db` / `directus-uploads` volume entries)

- [ ] **Step 1: Remove both services and their volumes**

Delete the `directus:` service block, the `directus-transcribe:` service block, and the `directus-db:` + `directus-uploads:` entries under `volumes:`. Leave `resona-audio`, `resona-md`, `resona-db`, `resona-profiles`.

- [ ] **Step 2: Verify compose still parses and references nothing removed**

Run:
```bash
docker compose -f docker-compose.resona.yml config >/dev/null && echo OK
grep -n "directus" docker-compose.resona.yml || echo "no directus refs"
```
Expected: `OK` and `no directus refs`.

- [ ] **Step 3: Commit**

```bash
git add docker-compose.resona.yml
git commit -m "refactor(compose): drop directus + transcribe worker — resona is now transcription-only"
```

---

### Task 7: Remove the worker package + directus dir; regenerate the lockfile

**Files:**
- Delete: `packages/directus-transcribe/` (whole dir)
- Delete: `directus/bootstrap.md`, `directus/schema-snapshot.yaml` (moved to PWA repo in Task 2)
- Modify: `uv.lock` (regenerated)

The workspace member glob is `members = ["packages/*", "apps/resona-cli"]`, so removing the dir drops it from the workspace automatically — no `pyproject.toml` member edit needed.

- [ ] **Step 1: Remove the files**

```bash
cd <resona-monorepo-root>
git rm -r packages/directus-transcribe directus/
```

- [ ] **Step 2: Regenerate the lockfile**

Run: `uv lock`
Expected: `uv.lock` updated; `grep -c "resona-directus-transcribe" uv.lock` returns `0`.

- [ ] **Step 3: Verify the workspace still resolves**

Run: `uv sync --all-packages`
Expected: completes without error; no reference to the removed package.

- [ ] **Step 4: Run the monorepo test suite (smoke)**

Run: `uv run pytest -q packages/api/tests/ packages/engine-server/tests/`
Expected: PASS (these are unaffected; confirms removal broke nothing).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: remove directus-transcribe package + directus schema (moved to resona-pwa)"
```

---

### Task 8: Scrub Directus/worker config + docs from the monorepo

**Files:**
- Modify: `.env.example` (remove the "Directus backend" + "directus-transcribe worker" blocks, lines 77–87)
- Modify: `CLAUDE.md` (remove the `directus-transcribe` package entry from the structure list + its responsibilities section; update the Docker section that lists directus ports)

Keep `CORS_ORIGINS` in `.env.example` — it's resona-api config (the browser's live `/v1/listen` still needs the PWA origin allowed).

- [ ] **Step 1: Trim `.env.example`**

Remove the `# ── Directus backend ──` and `# ── directus-transcribe worker ──` sections. Run: `grep -niE "directus|transcribe_(poll|concurrency|stale)" .env.example` → expected: no matches.

- [ ] **Step 2: Trim `CLAUDE.md`**

Remove the `directus-transcribe/` line from the structure tree and the `### resona-directus-transcribe` responsibilities block. In the Docker section, drop Directus from the service/port descriptions. Add one sentence: "Directus and the transcribe worker live in the separate `resona-pwa` repo; resona exposes only resona-api + engines."

Run: `grep -niE "directus" CLAUDE.md`
Expected: only the single pointer sentence remains.

- [ ] **Step 3: Commit**

```bash
git add .env.example CLAUDE.md
git commit -m "docs: scrub Directus/worker config + references from resona monorepo"
```

---

### Task 9: Final verification — resona boots clean as a transcription-only backend

- [ ] **Step 1: Fresh config validation**

Run: `docker compose -f docker-compose.resona.yml config >/dev/null && echo OK`
Expected: `OK`.

- [ ] **Step 2: Boot the transcription backend**

```bash
docker compose -f docker-compose.resona.yml --profile faster-whisper up -d
docker compose -f docker-compose.resona.yml ps
curl -s http://localhost:7710/health
```
Expected: `api` healthy; only `api` + `engine-faster-whisper` present (no directus, no worker). Health endpoint returns ok.

- [ ] **Step 3: Confirm the PWA stack still reaches it**

Re-run the Task 4 Step 5 end-to-end batch check against the now-decoupled resona-api.
Expected: a batch recording still reaches `done` — proving the cross-stack wiring (`RESONA_API_URL`) works after decoupling.

- [ ] **Step 4: No commit** (verification). Record the result.

---

## Done criteria

- resona monorepo: `docker-compose.resona.yml` has no `directus`/`directus-transcribe`; `packages/directus-transcribe/` and `directus/` are gone; `uv.lock` regenerated; `.env.example` + `CLAUDE.md` scrubbed; api + engine tests pass; backend boots transcription-only.
- resona-pwa: owns `docker-compose.yml` (directus + worker), `worker/` (standalone source + tests passing), `directus/` (bootstrap + schema), `.env.stack.example`; README + CLAUDE.md document the two-stack split; pushed to `origin`.
- End-to-end batch transcription works across the two separated stacks.
```
