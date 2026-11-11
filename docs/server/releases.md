# Releases, evaluations and worker updates

A Resona release is an immutable pin set: every wheel by name, version and SHA-256, every
image by `sha256:` digest, every model by exact revision. Release ids are package versions
(tag `v0.2.0` → release `0.2.0`); every workspace package, in particular `resona-api` and
`resona-worker`, carries it.

## Version bump

Nothing bumps the version automatically. A hub reports its `resona-api` version and a worker its
`resona-worker` version, and the hub applies promoted evaluations per release id. A build from a
commit after `vX.Y.Z` that still says `X.Y.Z` would therefore pass as the evaluated release while
running other code. CI (`test.yml`) fails on such a commit: once `vX.Y.Z` is tagged, the next
change to `main` must carry a new version.

```bash
just bump 0.3.0              # every pyproject.toml + uv.lock (scripts/release_version.py bump)
git commit -am "chore(release): 0.3.0" && git push   # via PR
git tag v0.3.0 <merged commit> && git push origin v0.3.0   # runs release.yml
```

`python3 scripts/release_version.py check --unreleased` runs the CI check locally. After the release
workflow, deploy its digests: the hub via `docker-compose.coolify.yml` (both `image:` lines,
[Coolify](coolify.md)), workers via `resona-worker update` (below).

## Release manifest

`release-manifest.json` (schema `release/manifest.schema.json`, model `resona_job_core.release.ReleaseManifest`):

```json
{"release_id": "0.2.0", "created_at": "...", "worker_protocol": 1,
 "wheels": [{"name": "resona-worker", "version": "0.2.0", "sha256": "<64 hex>"}],
 "images": [{"name": "ghcr.io/ecsplico/resona-hub", "digest": "sha256:<64 hex>"}],
 "models": [{"engine": "faster-whisper", "model": "Systran/faster-whisper-large-v3", "revision": "<commit>"}],
 "constraints": {"name": "worker-constraints.txt", "sha256": "<64 hex>"}}
```

Image names carry no tag; mutable revisions (`latest`, `cuda`, `main`, …) are rejected. Build one locally with
`uv run python release/build_manifest.py --release-id X --dist dist/ --image NAME=sha256:... --constraints dist/worker-constraints.txt --out dist/release-manifest.json`
(model pins come from `apps/resona-worker/src/resona_worker/models.json`, the same file `resona-worker prepare` uses by default). `worker-constraints.txt` is
`uv export --frozen --package resona-worker --all-extras --no-emit-workspace --no-hashes --no-header --no-annotate`:
the third-party versions from `uv.lock`, shipped next to the wheels. Without it a fresh worker environment resolves
whatever the index serves that day (observed: a newer `huggingface_hub` that rejects the cached model snapshots).

## Release pipeline (`.github/workflows/release.yml`)

On a `v*` tag: version check, privacy/lease/release/update tests, `tool-inventory.json` check →
`uv build --all-packages` → hub CPU, worker CPU and worker CUDA images (plus legacy engine images), pushed by version tag and recorded by digest →
`resona eval --profile cpu-private` on `eval/corpus` against `eval/baselines.json` → manifest + draft GitHub release
with wheels, manifest, `worker-constraints.txt`, schema and `tool-inventory.json` as assets.

Repository variables (all optional): `RESONA_IMAGE_NAMESPACE` (default `ghcr.io/ecsplico`, unverified),
`RESONA_PUBLISH_PYPI=true` (PyPI trusted publishing; namespace unverified, so GitHub release assets are canonical),
`RESONA_DISPATCH_MCP_RELEASE=true` with repo secrets `RELEASE_DISPATCH_APP_ID`/`RELEASE_DISPATCH_APP_KEY` to dispatch
`mcp-release` (`{mcp: "resona-hub", version, tools}`, same shape as `skaile-ai/excel-mcp`) to `skaile-ai/ai-assets`, whose
`mcp-release-bump.yml` opens the catalog bump PR for `mcp/resona-hub/MCP.md` and diffs its tool block against `tools`.
Not to `skaile-ai/platform`: its `mcp-release` handler only adopts flake-pinned stdio servers. The secrets are the
`skaile-release-dispatch` GitHub App's id and private key; in `skaile-ai` they are organization secrets, which this
`ecsplico` repository does not inherit, so a `skaile-ai` org owner must add them here (unset as of 2026-11-01).
Regenerate the inventory with `uv run python scripts/gen_tool_inventory.py` after changing MCP tools.

## Hub: staging, evaluation, approval

```bash
resona-admin release stage --manifest release-manifest.json   # admits the release for evaluation jobs only
resona-admin release list                                     # releases and approved (org, profile, platform) tuples
```

- `GET /v1/version` (no auth) → `{release_id, hub_version, worker_protocol}`.
- Evaluation jobs: `POST /v1/jobs` with `purpose: "evaluation"` needs an org_admin credential and a `release_id` the hub knows (staged or bundled), else 403 / 422 `release_not_staged`.
- `POST /v1/evaluations` (org_admin; metrics, corpus hash, gate only — no text) → `GET /v1/evaluations/{id}` → `POST /v1/evaluations/{id}/promote` with `expected_baseline_revision` (409 `baseline_revision_mismatch`, 422 `gate_failed`). `GET /v1/baselines?execution_profile=&platform=` → current baseline or 404 `baseline_required`.
- Complete metrics: every recorded evaluation carries `fixture_checks` (`{fixture_id: {kind, checks}}`, as the corpus manifest declares them; `resona eval --remote` sends it) and every fixture must have exactly the metrics its checks produce (`wer`→`wer`, `cer`→`cer`, `silence`→`silence_insertions`, `table_cells`→`missing_cells`, `reading_order`→`order_failures`; diarize audio fixtures: `speaker_attribution`→`speaker_attribution`, `der`→`der`, `speaker_count`→`speaker_count_error`), else 422 `incomplete_metrics`. An empty metrics object can therefore never pass the absolute checks.
- Gate: the first baseline needs passing absolute checks (no missing cells, order failures or silence insertions); later ones also have every baseline fixture and metric and no WER/CER/DER regression above 0.02 and no `speaker_attribution` (higher is better) more than 0.02 below the baseline. `speaker_count_error` is reported, not gated.
- Changed corpus: if the current baseline of the same (execution profile, platform) was measured on another `corpus_sha256`, promotion answers 409 `baseline_corpus_mismatch`, unless the admin sends `"rebaseline": true` (`resona eval promote <id> --rebaseline`). That promotion starts the new corpus's baseline on absolute checks only; later promotions compare against it.
- Promotion approves `(execution_profile, platform, release_id)` for the organization. Production audio and document jobs are claimed only by workers whose reported release is approved for that tuple; otherwise the job waits with `queue_reason: release_not_approved`.
- Text (postprocess-only) jobs run no engine, so no evaluation measures them: they need no promoted evaluation, but the worker's release must be one the hub knows (staged, or the bundled release). A worker on a never-staged release (dev build, unfinished update) claims no text jobs; they wait with `queue_reason: release_not_approved`.
- **Bootstrap:** the hub's own release (`RESONA_RELEASE_ID`, else the installed `resona-api` version) is the *bundled* release, recorded by migration 2 and at every startup. It may run evaluation and text jobs without being staged, but like any release it runs production audio and document jobs only for tuples approved by a promoted evaluation. A new deployment therefore runs `resona eval --remote` and `resona eval promote` once per (execution profile, platform) before those jobs are claimed. Staging the bundled release's real manifest replaces its placeholder and keeps it bundled.

## Worker protocol

Workers send `X-Resona-Worker-Protocol: 1`. Every worker route (heartbeat, claim, input, postprocess, policy, result, fail) answers 426 `protocol_incompatible` for any other major before touching content.

## Updating a worker

Run on the worker machine (the hub cannot trigger updates):

```bash
resona-worker update --version 0.2.0 --manifest https://.../release-manifest.json \
  --profile mac-private --hub https://resona.example --token-file ~/.resona/worker.token \
  --capabilities-file caps.json --work-dir ~/Library/Caches/resona-worker/work [--label <launchd label>]
```

Order: manifest names `--version` → hub reports the release staged/approved for this org with the same manifest digest →
wheels and `worker-constraints.txt` downloaded and SHA-256 verified → drain (the running worker finishes its current job,
then stops claiming) → install only the `resona-worker` wheel (profile extras) into `<releases-dir>/<id>/venv`, with every
other pinned wheel as a direct-URL constraint plus the locked third-party constraints → `prepare` with the manifest's
model pins into a new capabilities file.
Only if prepare succeeds is the capabilities file replaced, `current` repointed and the service restarted; on any
failure (e.g. a model revision the local cache does not hold) the previous release and capabilities stay, the drain is
released and the command exits nonzero.

The drain handshake lives in the work directory: the supervisor records its pid (`supervisor.pid`), `update` writes
`drain`, the supervisor answers `drained` between jobs. If no supervisor is alive the wait ends at once (nothing can be
claiming; the `drain` request stays, so a worker started meanwhile drains before its first claim). Without `--label`
there is no service to restart: after a successful update the old supervisor is told (`superseded`) to exit instead of
resuming on old code, and the command prints the new release's `run` line. Linux Docker workers update by pulling the
manifest's pinned image digest, not with this command.
