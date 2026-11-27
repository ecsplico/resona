# Hub profiles of this app

Postprocess profiles only this app uses. A Resona hub knows them after an org admin pushed them:

```bash
RESONA_API_URL=https://<hub-host> RESONA_API_KEY=<org-admin token> just push-profiles
```

(`just pwa push-profiles` from the monorepo root.) The recipe runs one `resona profiles push` for
every file here, the catalogue profiles the app uses: `dictation-medical-de` (the
`defaultProfile` new recordings are transcribed with) and those offered under "Nachbearbeiten"
(`transcript-markdown-de`, `transcript-polish-de`), and the catalogue blocks they include
(`profiles/blocks/`), all from the hub repository's `profiles/`; the CLI pushes the blocks first.
`RESONA_PROFILE_CATALOGUE` names that directory, `RESONA_CLI` the `resona` command.

| Profile | Used by | Needs |
|---------|---------|-------|
| `title-de` | `useTitleJob` (title suggestion after a live dictation): one `extract` step `title` on the model role `@title`, result at `structured.title.title` | The organization settings assign the role `title` to an approved endpoint (`model_roles`), e.g. a `local_llm` endpoint assigned to an approved worker |

Without the profile on the hub, or without the `title` role, the title job fails and the app keeps
its rule-based title (the hub answers `role_unassigned`).

An organization that pushed the earlier `title-de` (endpoint `local-llm`) re-pushes it and maps the
role to the same endpoint: `"model_roles": {"title": "local-llm"}` in the settings
(`resona settings get`, edit, `resona settings set --file`); see the hub's `profiles/README.md`. `test/titleProfile.spec.ts` checks the file against what `useTitleJob` reads.
