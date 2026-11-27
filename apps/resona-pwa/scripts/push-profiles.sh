#!/usr/bin/env bash
# Upload the postprocess profiles this app uses to a Resona hub (org admin credential).
#
#   RESONA_API_URL / RESONA_API_KEY   the hub and an org_admin token (else the hub of ~/.resona/config.json)
#   RESONA_CLI                        the hub CLI (default: resona), e.g. "uv run --project ../.. resona"
#   RESONA_PROFILE_CATALOGUE          the hub repository's profiles/ directory (default: ../../profiles)
#
# One `resona profiles push` call uploads the app's own profiles (profiles/*.json: title-de), the
# catalogue profiles it uses (dictation-medical-de, the defaultProfile of new recordings, and those
# it offers under "Nachbearbeiten") and the catalogue blocks (profiles/blocks/) they include; the
# CLI pushes blocks before the profiles that include them and uploads nothing when an include is
# missing. Pushing stores a new revision; it changes nothing else.
set -euo pipefail

app_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
read -r -a resona <<<"${RESONA_CLI:-resona}"
catalogue="${RESONA_PROFILE_CATALOGUE:-$app_dir/../../profiles}"
catalogue_profiles=(dictation-medical-de transcript-markdown-de transcript-polish-de)

if ! command -v "${resona[0]}" >/dev/null 2>&1; then
	echo "push-profiles: '${resona[0]}' not found; install resona-cli or set RESONA_CLI" >&2
	exit 1
fi

files=("$app_dir"/profiles/*.json)
missing=0
for name in "${catalogue_profiles[@]}"; do
	if [[ -f "$catalogue/$name.json" ]]; then
		files+=("$catalogue/$name.json")
	else
		echo "push-profiles: $name.json not found in $catalogue" >&2
		missing=1
	fi
done
if [[ "$missing" -ne 0 || ! -d "$catalogue/blocks" ]]; then
	echo "push-profiles: set RESONA_PROFILE_CATALOGUE to the profiles/ directory of a Resona hub checkout" >&2
	exit 1
fi
files+=("$catalogue"/blocks/*.json)

"${resona[@]}" profiles push "${files[@]}"
