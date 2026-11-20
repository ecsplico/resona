# Hub profiles of this app

Postprocess profiles only this app uses. A Resona hub knows them after an org admin pushed them:

```bash
RESONA_API_URL=https://<hub-host> RESONA_API_KEY=<org-admin token> just push-profiles
```

(`just pwa push-profiles` from the monorepo root.) The recipe runs `resona profiles push` for
every file here and for the catalogue profiles the app uses: `dictation-medical-de` (the
`defaultProfile` new recordings are transcribed with) and those offered under "Nachbearbeiten"
(`transcript-markdown-de`, `transcript-polish-de`), all from the hub repository's `profiles/`;
`RESONA_PROFILE_CATALOGUE` names that directory, `RESONA_CLI` the `resona` command.

| Profile | Used by | Needs |
|---------|---------|-------|
| `title-de` | `useTitleJob` (title suggestion after a live dictation): one `extract` step `title`, result at `structured.title.title` | A `local_llm` endpoint with the id `local-llm` in the organization settings, assigned to an approved worker |

Without the profile on the hub, or without that endpoint, the title job fails and the app keeps
its rule-based title. `test/titleProfile.spec.ts` checks the file against what `useTitleJob` reads.
