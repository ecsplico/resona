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
2. **`docker`** — for each of the four images (`resona-engine-faster-whisper`, `resona-engine-whisper`, `resona-engine-voxtral`, `resona-hub` from `packages/api/Dockerfile`):
   - Logs in to `ghcr.io` using the workflow's `GITHUB_TOKEN` (which has `packages: write` via the `permissions:` block)
   - Builds `linux/amd64` (engines are CUDA — no arm64) with GHA cache
   - Pushes `ghcr.io/ecsplico/<name>:<tag>`, `…:vX.Y`, and `…:latest`
3. **`release`** — once both above succeed, drafts a GitHub release at `vX.Y.Z` with auto-generated notes. It's a **draft**, so review and publish manually from the GitHub UI.
4. **`npm`** (opt-in, repository variable `RESONA_PUBLISH_NPM=true`) — the TypeScript hub client `clients/hub-ts`:
   - `bun install --frozen-lockfile`, generated-types drift check against `contract/openapi.json`, typecheck, test, build
   - `npm version <release id> --no-git-tag-version` (the committed `0.0.0` is a placeholder; the package version is the hub release id)
   - `npm publish --provenance --access public` as `@ecsplico/resona-hub-client`, authenticated by OIDC trusted publishing (no npm token anywhere — see one-time setup below)

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

### npm trusted publishing (OIDC)

The `npm` job publishes `@ecsplico/resona-hub-client` with npm's [trusted publishing](https://docs.npmjs.com/trusted-publishers): npm accepts the GitHub Actions OIDC token of `ecsplico/resona`'s `release.yml` instead of a token. The repository holds **no** `NPM_TOKEN` secret; do not add one. The job needs Node 24 (npm ≥ 11.5.1) and `permissions: id-token: write`, both set in `release.yml`.

A trust link can only be attached to a package that exists, so a new package needs one manual publish first. Run every npm command below on a **temporary web login** (2FA approved in the browser), from a directory without an `.npmrc` of its own, never with a stored automation token:

```bash
cd /tmp && npm login --auth-type=web --userconfig /tmp/npmrc-trust
export NPM_CONFIG_USERCONFIG=/tmp/npmrc-trust
```

1. **First publish** (only while `npm view @ecsplico/resona-hub-client` answers 404): in `clients/hub-ts`, `bun install && bun run build`, then `npm version 0.0.0-bootstrap.0 --no-git-tag-version && npm publish --access public --tag bootstrap`. Do not commit the version change. A prerelease under its own dist-tag keeps the release versions free for CI and leaves `latest` to the first CI publish.
2. **Trust link**: `npm trust github @ecsplico/resona-hub-client --file release.yml --repository ecsplico/resona --allow-publish` (no `--environment`: the job sets none). `--allow-publish` is required; without it the link only grants staged publishes. `npm trust list @ecsplico/resona-hub-client` shows it.
3. **Log out**: `npm logout; rm -f /tmp/npmrc-trust; unset NPM_CONFIG_USERCONFIG`.
4. **Enable the job**: repository variable `RESONA_PUBLISH_NPM=true` (Settings → Secrets and variables → Actions → Variables).
5. **Use the link within 2 days**: a new trust link expires unless a CI publish uses it within 2 days (a dry run does not count); an expired link can only be deleted and recreated. Push the next release tag within that window.

Done when `npm view @ecsplico/resona-hub-client@X.Y.Z _npmUser` names `GitHub Actions <npm-oidc-no-reply@github.com>`.

When the `npm` job fails:

- **`E404 … PUT https://registry.npmjs.org/@ecsplico%2fresona-hub-client`** (with `--loglevel verbose`: `OIDC token exchange error - package not found`): no trust link matches package, repository, workflow file and environment. Compare with `npm trust list` (temporary web login). Renaming `release.yml` or adding an `environment:` to the job breaks the link; recreate it with the new values. An expired link (never used within 2 days) shows the same error: delete and recreate it.
- **`E403 Granular access tokens that bypass two-factor authentication`**: a token was used instead of OIDC — a local command picked up a stored `.npmrc` token, or someone added `NODE_AUTH_TOKEN`/`NPM_TOKEN` to the workflow. Remove the token; CI must authenticate by OIDC only.
- **`E403 … cannot publish over the previously published versions`**: this release id is already on npm (e.g. the job is re-run after it succeeded). Nothing to do; npm versions are immutable, a fix needs a new release.
- **`npm publish` complains about provenance or the repository**: `repository.url` in `clients/hub-ts/package.json` must stay `git+https://github.com/ecsplico/resona.git`, the repository the workflow runs in.

The rest of the release does not depend on this job: wheels, images and the draft GitHub release complete when it fails, and the job can be re-run alone from the Actions UI.

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
