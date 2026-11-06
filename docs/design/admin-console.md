---
version: 1
slug: "pi-src-resona-api-static-admin-index-html-1a9025cb"
primary_target: "packages/api/src/resona_api/static/admin/index.html"
related_targets: ["packages/api/src/resona_api/static/admin/admin.js","packages/api/src/resona_api/static/admin/admin.css"]
---

# /admin — hub management console

Scope: the static `/admin` page (index.html, admin.js, admin.css). Mode: Operate. Audience: the hub operator (mostly), occasionally org admins; desktop short sessions and phone quick checks. Task: see hub state (workers online/approved, tokens, job queue, settings, baselines) and act (approve, revoke, issue, cancel, delete, create org). Constraints: Web Awesome 3 vendored, strict CSP ('self' only), no build step, no innerHTML with API data, token shown once.

## Direction contract

THESIS: Every secret is handed over like registered mail: tracked, receipted once, stamped when cancelled. Refuses the grey SaaS dashboard with status dots and a blue accent.

OWN-WORLD: Receipt-white ground (#fbfaf6) with faint ruled stock; postal-navy (#1d2b4f) shell rail and header; carbon violet (#5b3e8c) for every machine identifier and numeral; registered-label red (#d7262e) only for stamps and destructive actions; signal green (#1f8a4c) for online/approved; amber for waiting. Condensed grotesk caps for labels, system sans for body, tabular mono for IDs grouped like tracking numbers (`wrk · f21e · 64ec · fd5a · 09db`).

STORY: The operator opens the console, sees which workers are on duty and which tokens are live, issues or cancels with confidence that nothing secret lingers.

FIRST VIEWPORT: Desktop: navy rail left (brand, role, organization list for operators with a New-organization field, Sign out). Main: org title with counts, Web Awesome tab group (Workers, Credentials, Jobs, Settings & baselines); Workers table first, status badges, capability tags, action buttons. Phone: rail collapses to a top bar with org select; tables become stacked row cards.

FORM: Web Awesome components (wa-tab-group, wa-button, wa-badge, wa-tag, wa-input, wa-select, wa-dialog for confirms, wa-copy-button, wa-relative-time, wa-callout for errors) themed through CSS custom properties. Seed: seed:da53125d530c12d4.

SIGNATURE: Issuing a token renders a perforated tear-off receipt slip (dashed perforation, red R label, tracking-number id, the token, Copy, "Shown once. Store it now."), dismissed by tearing it off (slide-out). Raises: revoked items stay listed under a REVOKED rubber stamp; pending jobs carry a wait-time bar whose length is exact duration; the action color appears only on pressable controls.
