# Releasing Resona

Resona ships **eleven Python packages** to PyPI and **four Docker images** to GHCR from a single tag push. The release workflow at `.github/workflows/release.yml` does everything; this document is the operator's checklist.

## Pre-flight checklist

Before tagging a release:

- [ ] **All tests pass** locally: `uv run pytest`
- [ ] **CI is green** on `main` (the `Test` and `Docker build (PR check)` workflows)
- [ ] **Audit is clean** — no tracked `.env`, no leaked secrets. See [`/tmp/resona-publish-audit.md`](/) for the criteria.
- [ ] **Version bumped** in every `pyproject.toml` to the new `X.Y.Z`:
  - `apps/resona-cli/pyproject.toml`
  - `packages/api/pyproject.toml`
  - `packages/asr-core/pyproject.toml`
  - `packages/client/pyproject.toml`
  - `packages/cloud-stt/pyproject.toml`
  - `packages/cloud-tts/pyproject.toml`
  - `packages/engine-faster-whisper/pyproject.toml`
  - `packages/engine-server/pyproject.toml`
  - `packages/engine-voxtral/pyproject.toml`
  - `packages/engine-whisper/pyproject.toml`
  - `packages/postprocess/pyproject.toml`
- [ ] **Inter-package version pins** updated — workspace deps in `[project.dependencies]` use `>=X.Y.Z` where appropriate (e.g. `resona-asr-core>=0.2.0`)
- [ ] `author` / `author email` fields in each `pyproject.toml` are no longer `TBD-by-user` placeholders
- [ ] `uv.lock` regenerated: `uv lock`
- [ ] `CHANGELOG.md` (if/when it exists) updated for the new version

## Cutting a release

```bash
# from a clean main with all the above completed
git tag -a vX.Y.Z -m "vX.Y.Z — short summary"
git push github vX.Y.Z
```

That's it. The `Release` workflow takes over.

## What happens when the tag pushes

`.github/workflows/release.yml` runs three jobs in parallel:

1. **`pypi`** — for each of the 11 publishable packages:
   - `uv build --package <name>` produces sdist + wheel into `dist/`
   - Build artifacts uploaded to the workflow run
   - `pypa/gh-action-pypi-publish@release/v1` uploads to PyPI via OIDC (no token secret needed — see one-time setup below)
2. **`docker`** — for each of the four images (`resona-engine-faster-whisper`, `resona-engine-whisper`, `resona-engine-voxtral`, `resona-api`):
   - Logs in to `ghcr.io` using the workflow's `GITHUB_TOKEN` (which has `packages: write` via the `permissions:` block)
   - Builds `linux/amd64` (engines are CUDA — no arm64) with GHA cache
   - Pushes `ghcr.io/ecsplico/<name>:<tag>`, `…:vX.Y`, and `…:latest`
3. **`release`** — once both above succeed, drafts a GitHub release at `vX.Y.Z` with auto-generated notes. It's a **draft**, so review and publish manually from the GitHub UI.

If a job fails mid-release, you can re-run individual jobs from the Actions UI. The PyPI publish step has `skip-existing: true` so re-runs won't error on already-uploaded versions.

## One-time setup

### PyPI trusted publishing (OIDC)

The `pypi` job uses PyPI's [trusted publishing](https://docs.pypi.org/trusted-publishers/) — GitHub Actions authenticates via short-lived OIDC tokens, so there's no long-lived API token to leak.

**You must register each package as a pending publisher on PyPI before the first release.** For each of the 11 packages:

1. Go to <https://pypi.org/manage/account/publishing/>
2. Click "Add a new pending publisher"
3. Fill in:
   - **PyPI project name**: `resona-asr-core` (or whichever package)
   - **Owner**: `ecsplico`
   - **Repository name**: `resona`
   - **Workflow name**: `Release` *(must exactly match the `name:` field in `release.yml`)*
   - **Environment name**: *(leave blank)*

Until trusted publishing is configured for a given package, the publish step for that package will fail with `invalid-publisher`. Other packages in the matrix continue independently (`fail-fast: false`).

### GHCR permissions

GHCR uses the `org.opencontainers.image.source` LABEL on each Dockerfile to link the image to the source repo. The Dockerfiles already declare this. On the very first push of each image, the package will appear under <https://github.com/orgs/ecsplico/packages> as **private** by default. To make it public:

1. Open the package settings (`https://github.com/orgs/ecsplico/packages/container/<name>/settings`)
2. Under "Danger Zone", change visibility to **Public**
3. (Optional) "Manage Actions access" → "Inherit access from source repository"

After this one-time toggle, every subsequent release tag will keep the package public.

### GitHub repository settings

In the `ecsplico/resona` repo settings:

- **Actions → General → Workflow permissions** → "Read and write permissions" (or rely on the per-workflow `permissions:` block in `release.yml`, which is already correct)
- **Actions → General → Fork pull request workflows** → require approval (default is fine)

## Hotfix / dry-run

To re-run a failed release without bumping the version, trigger `Release` manually from the Actions UI (it has `workflow_dispatch:`). PyPI `skip-existing: true` and GHCR's content-addressable tags make this idempotent.

## Rolling back

You can't unpublish from PyPI (releases are permanent). To recover from a bad release:

1. Yank the affected versions on PyPI (`pypi.org/manage/project/<name>/release/X.Y.Z/`) — yanked releases are excluded from default `pip install <name>` resolution but remain downloadable by exact-pin
2. Bump and re-release: `vX.Y.(Z+1)`
3. For GHCR, delete the bad tag from the package page or push a new image with the same tag to overwrite
