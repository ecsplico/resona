#!/usr/bin/env bash
# Rebuilds packages/api/src/resona_api/static/admin/vendor/carbon from the pinned
# packages in package.json. Needs bun. Output is committed; the hub serves it as-is.
set -euo pipefail
cd "$(dirname "$0")"
OUT=../src/resona_api/static/admin/vendor/carbon
bun install --frozen-lockfile
rm -rf "$OUT" && mkdir -p "$OUT/fonts"
bun build src/entry.js --outfile "$OUT/carbon.js" --format esm --minify --target browser --production
bunx sass --no-source-map --style=compressed --load-path=node_modules src/theme.scss "$OUT/carbon.css"
cp node_modules/@ibm/plex-sans/fonts/complete/woff2/IBMPlexSans-{Regular,SemiBold}.woff2 "$OUT/fonts/"
cp node_modules/@ibm/plex-mono/fonts/complete/woff2/IBMPlexMono-Regular.woff2 "$OUT/fonts/"
cp node_modules/@carbon/web-components/LICENSE "$OUT/LICENSE-carbon"
cp node_modules/@ibm/plex-sans/LICENSE.txt "$OUT/LICENSE-plex"
# Gzip twins: the hub's vendor route serves them to clients that accept gzip.
gzip -9 -n -k "$OUT/carbon.js" "$OUT/carbon.css"
du -sh "$OUT"
