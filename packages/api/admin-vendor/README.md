# admin-vendor

Builds the IBM Carbon bundle that the hub's `/admin` page loads from
`/admin/vendor/carbon/`. The output in
`packages/api/src/resona_api/static/admin/vendor/carbon/` is committed; the hub
serves it as-is and never runs this build. Nothing here is a uv workspace member.

## What it produces

| File | Source |
|---|---|
| `carbon.js` (+ `carbon.js.gz`) | `src/entry.js` bundled by bun as one minified ES module. Importing it registers every `cds-*` element `/admin` uses and exports the Carbon icon descriptors it renders. |
| `carbon.css` (+ `carbon.css.gz`) | `src/theme.scss` compiled by sass: Carbon reset, body type, g10 theme tokens on `:root`, zone classes (`.cds--g10`, `.cds--g100`, …), layer classes, type custom properties and the IBM Plex `@font-face` rules. |
| `fonts/` | IBM Plex Sans 400/600 and IBM Plex Mono 400 (woff2). |
| `LICENSE-carbon`, `LICENSE-plex` | Apache-2.0 (Carbon), SIL OFL 1.1 (Plex). |

The hub's vendor route serves only `.js`, `.css` and `.woff2` files, and the gzip
twin of a `.js`/`.css` file to clients that accept gzip.

## Rebuild

Needs [bun](https://bun.sh). From the repository root:

```bash
bash packages/api/admin-vendor/build.sh
```

The script installs the pinned versions from `package.json` / `bun.lock`
(`--frozen-lockfile`), replaces the output directory and prints its size.

## Adding a component or icon

1. Add the import to `src/entry.js`: components as
   `import '@carbon/web-components/es/components/<name>/index.js';`, icons as
   `export { default as <Name><size> } from '@carbon/icons/es/<name>/<size>.js';`.
2. Run the build and commit the regenerated output together with the `entry.js` change.
3. In `admin.js`, import the icon from `/admin/vendor/carbon/carbon.js` and render it
   with the page's `icon()` helper (`document.createElementNS`, no `innerHTML`).

Upgrading Carbon: change the versions in `package.json`, run `bun install` to update
`bun.lock`, rebuild, then check `/admin` in a browser for console warnings
(deprecations are logged there).
