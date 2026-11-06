---
version: 2
slug: "pi-src-resona-api-static-admin-index-html-1a9025cb"
primary_target: "packages/api/src/resona_api/static/admin/index.html"
related_targets: ["packages/api/src/resona_api/static/admin/admin.js","packages/api/src/resona_api/static/admin/admin.css"]
---

# /admin - hub management console

Scope: the static `/admin` page (index.html, admin.js, admin.css). Mode: Operate. Audience: the hub operator (mostly), occasionally org admins; desktop short sessions and phone quick checks. Task: see hub state (workers online/approved, tokens, job queue, settings, baselines, testbench) and act (approve, revoke, issue, cancel, delete, create org). Product facts: [admin-product.md](admin-product.md).

## Direction

IBM Carbon played straight, for data-dense reading: a g100 shell, a g10 page, Carbon data tables for every list, Carbon tokens and spacing only. No custom palette, no decorative motifs. State comes before action: every row shows what is true (online, approved, revoked, queued) as a tag before any menu offers an action.

## Shell

- `cds-header` in the g100 zone: menu button (`cds-header-menu-button`, rail mode), product name "Resona hub", then global actions: the selected organization (mono), an organization switcher (operators only) whose `cds-header-panel` lists every organization plus "Create organization", the signed-in role (account panel with credential and organization), and Sign out.
- `cds-side-nav` with icon links: Overview, Workers, Credentials, Jobs, Settings, Baselines, Testbench. At 66rem and wider it is a 3rem rail beside the page; below that it is hidden until the menu button expands it, and choosing a section closes it.
- Hash routing: `#/overview`, `#/workers`, `#/credentials`, `#/jobs`, `#/settings`, `#/baselines`, `#/testbench`. Reload and the back button keep the section; an unknown or empty hash shows Overview.
- Sign-in: a g100 bar and one tile with a `cds-password-input` for the token and a Sign in button. A 401 clears the stored token and says "Token invalid or revoked"; a 403 from `/v1/admin/me` says the token is not an organization admin or hub operator token.

## Sections

Each section has a page header (organization label, title, one-sentence note, page tools such as Refresh) and its tables.

- **Overview**: a row of clickable tiles linking to their section (workers online with approved / not revoked counts, live credentials, pending jobs with the oldest wait, settings revision) and a "Needs attention" table derived only from loaded data: workers reporting capabilities without approval, approved workers that are offline, pending jobs with a queue reason, approved capabilities a worker no longer reports.
- **Workers**: name, ID, status (online/offline/revoked), last seen, approval, capability summary, runtime. The expanded row holds the facts and the capability list. Row menu: approve private, approve non-private, revoke.
- **Credentials**: credential ID, role, state, worker, issued. Toolbar actions issue a client or an org-admin token. Row menu: revoke; disabled with the reason for the caller's own credential and for worker credentials (revoked through their worker).
- **Jobs**: job, kind, status, waiting (duration and queue reason for pending jobs), execution profile, privacy, result availability, created. The expanded row holds the timeline, attempt, candidate, error and result facts. Cursor paging with "Load more" via `next_cursor`. Row menu (org admins in their own organization only): cancel, delete. Operators see metadata without a menu column.
- **Settings**: read-only. Revision tag, a structured list of the top-level keys with short values, and the full JSON in a multi-line `cds-code-snippet` with copy.
- **Baselines**: baselines table (execution profile, platform, revision, release, shortened corpus digest, created; full digest in the expanded row) and approved releases table.
- **Testbench**: org admins in their own organization only (operators see why it is closed). Status strip with enable/disable (GET `/v1/settings` ETag, PUT with `If-Match`), then a `cds-tabs` switch: Items (kind/status filters, items table, Contribute modal with `cds-file-uploader`), Runs (the open item: original preview from a blob URL for audio and PNG, download, reference `cds-textarea` with save, candidate checkboxes with worker counts, runs table, word diff), Leaderboard (audio and document tables, best value per column marked).

## Table pattern

Every list goes through one `dataTable(cfg)` helper in admin.js:

- `cds-table` size `md` (`sm` for dense testbench tables) with title and description, a toolbar with `cds-table-toolbar-search` (filters the loaded rows client-side, including expanded-row text), a refresh button and the section's primary action.
- Sortable headers where the column has a meaningful order; sorting and filtering act on the loaded row array, so a re-render keeps them.
- Secondary detail lives in `cds-table-expanded-row`; expanded state survives a refresh.
- Loading shows `cds-table-skeleton`; an empty table shows one sentence that says what makes a row appear (for example: enroll a worker with `resona-admin worker enroll`).
- Status is a `cds-tag`: green online/approved/completed/live, gray offline, red revoked/failed, purple pending/queued, blue processing, warm-gray cancelled. Capabilities: green approved and reported, cool-gray reported but not approved, magenta approved but no longer reported; each tag carries a screen-reader text equivalent and a legend explains the colours.

## Row actions and modals

- One `cds-overflow-menu` per row instead of rows of buttons. Destructive items are `danger`; an action that is not allowed stays visible, disabled, with the reason in its label.
- Destructive actions confirm in a `cds-modal` with a danger primary button; the title names the object ("Revoke credential cred_…?"), the body states the consequence, the secondary button goes back.
- Issued token: a `cds-modal` that does not close on an outside click, with a warning inline notification "Shown once", the credential ID and the token in a `cds-code-snippet type=single` with copy. "Done" removes the token from the DOM and from memory. The token is never re-displayed or logged.
- Create organization and Contribute are small form modals.

## Feedback

Success and info: `cds-toast-notification` in the top-right stack. Errors: the page-level `cds-inline-notification` banner keeps the API code and message from the `{error, message}` body (fallbacks: `detail.message`, a validation `detail` list, the HTTP status text). Sign-in errors stay on the sign-in tile.

## Tokens and type

- Theme: g10 on `:root` (page), g100 zone class on the header and sign-in bar, `cds--layer-two` on panels that sit on a layer.
- Colours, spacing and type only through Carbon custom properties (`var(--cds-…)`), and only theme-level tokens: component tokens such as `--cds-tag-background-*` are not defined on `:root`. Word diff: missing words `--cds-text-error` with line-through, added words on `--cds-highlight` with a `--cds-support-success` underline (never colour alone).
- IBM Plex Sans for text; IBM Plex Mono (`.mono`) for every machine identifier: IDs, digests, revisions, model and candidate names, execution profiles. Numbers use tabular figures.
- admin.css stays small: layout, phone rules and the few things Carbon does not cover.

## Phone (under 42rem)

- Header shows the menu button, the organization (and the switcher for operators), account and sign out; the side nav opens from the menu button.
- No page-level horizontal scroll. Tables may scroll inside their own container; columns marked `secondary` are hidden and their values stay in the expanded row, which is pinned to the viewport width. Toolbar actions wrap onto their own line.
- Overview tiles stack in one column.

## Constraints

- CSP: `default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; font-src 'self'; img-src 'self' data: blob:; media-src blob:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`. No inline scripts, no `style="…"` or `on*` attributes in HTML, no external URLs. Setting `element.style` from JS is allowed.
- No `innerHTML`/`outerHTML`/`insertAdjacentHTML`: the DOM is built with `createElement`/`textContent`; icons are Carbon icon descriptors rendered with `createElementNS`.
- `admin.js` is an ES module (`<script type="module" src="/admin/admin.js">`) that imports icon descriptors from `/admin/vendor/carbon/carbon.js`; importing that bundle registers the `cds-*` elements. Component methods are called only after `customElements.whenDefined` (and `updateComplete` where the shadow DOM is needed).
- index.html loads `/admin/vendor/carbon/carbon.css`, then `/admin/admin.css`.
- The Carbon bundle is vendored prebuilt in `static/admin/vendor/carbon/` (script, theme CSS, Plex fonts, gzip twins). To add a component or icon, edit `packages/api/admin-vendor/src/entry.js` and run `packages/api/admin-vendor/build.sh` (bun); see `packages/api/admin-vendor/README.md`.
- Operator scope never includes job content: the testbench and job cancel/delete are closed to the hub operator role.
