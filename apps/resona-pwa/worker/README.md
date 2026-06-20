# resona-directus-transcribe

Background worker that polls Directus for `recordings` with `status=pending`,
transcribes them through resona-api, and writes the result back. See
`docs/superpowers/specs/2026-06-17-pwa-directus-platform-design.md`.
