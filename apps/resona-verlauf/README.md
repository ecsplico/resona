# Resona Verlauf

Medical dictation organised around the day's appointments. A Nuxt 4 layer app on top of
[`resona-pwa`](../resona-pwa/README.md): same Directus data layer, same Resona hub, same
live dictation page, plus

- **Heute**: the day's `EG`/`FG` appointments from one Google calendar, each marked
  "Diktiert" or "Offen"; tap one to start a dictation linked to it. Tab "Diktat" starts a
  dictation without an appointment and lists recent recordings.
- **Desktop**: table of dictations with "Kopieren" (transcript to the clipboard) and an
  "Übernommen" checkbox once the text is in the patient record.

## Quick start

```bash
# data layer + hub: see ../resona-pwa/README.md (Directus on :7700, hub on :7710)
cd ../resona-pwa && bun install && cd ../resona-verlauf
bun install
cp .env.example .env    # fill NUXT_GOOGLE_*, NUXT_SESSION_PASSWORD, NUXT_DIRECTUS_INTERNAL_URL
bun run dev             # → http://localhost:7731
```

Sign in with a Directus user, open "Heute" and click "Google-Kalender verbinden" once.
The refresh token is stored sealed on the server (`NUXT_VERLAUF_DATA_DIR`), so every
allowed user sees the same calendar afterwards.

## Google OAuth client

Google Cloud console → OAuth client "Web application", authorized redirect URI
`<origin>/api/termine` (e.g. `http://localhost:7731/api/termine`), scope
`calendar.readonly`. Put the id/secret into `NUXT_GOOGLE_CLIENT_ID` /
`NUXT_GOOGLE_CLIENT_SECRET`.

## Configuration

| Var | Default | Meaning |
|-----|---------|---------|
| `NUXT_GOOGLE_CLIENT_ID` / `NUXT_GOOGLE_CLIENT_SECRET` | (empty) | Google OAuth client |
| `NUXT_GOOGLE_REDIRECT_URI` | `<origin>/api/termine` | pin the redirect URI |
| `NUXT_SESSION_PASSWORD` | (empty) | ≥ 32 chars; seals the stored refresh token |
| `NUXT_DIRECTUS_INTERNAL_URL` | (empty) | Directus as the server reaches it (auth check, `/directus` proxy) |
| `NUXT_VERLAUF_CALENDAR_ID` | primary | Google calendar id |
| `NUXT_VERLAUF_TITLE_PREFIXES` | `EG,FG` | title prefixes of the shown appointments (case-sensitive) |
| `NUXT_VERLAUF_ALLOWED_EMAILS` | (empty = all) | Directus users allowed to read the calendar |
| `NUXT_VERLAUF_DATA_DIR` | `.data/verlauf` | directory of the sealed token (a volume in production) |
| `NUXT_PUBLIC_VERLAUF_API_BASE` | (same origin) | only for the Capacitor build |

The `NUXT_PUBLIC_*` keys of resona-pwa (Directus URL, hub URL, language, profile, data
mode) apply unchanged.

## Deployment

`Dockerfile` here, built with context `apps/` so the layer is present; service `verlauf`
in `../resona-pwa/docker-compose.coolify.yml`. Runs the Nitro server
(`node .output/server`), never `nuxt generate`.

Developer notes: [CLAUDE.md](CLAUDE.md).
