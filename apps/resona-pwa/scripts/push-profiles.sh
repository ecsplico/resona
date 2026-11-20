#!/usr/bin/env bash
# Upload the postprocess profiles this app uses to a Resona hub (org admin credential).
#
#   RESONA_API_URL / RESONA_API_KEY   the hub and an org_admin token (else the hub of ~/.resona/config.json)
#   RESONA_CLI                        the hub CLI (default: resona), e.g. "uv run --project ../.. resona"
#   RESONA_PROFILE_CATALOGUE          the hub repository's profiles/ directory (default: ../../profiles)
#
# The app's own profiles (profiles/*.json: title-de) are pushed first, then the catalogue
# profiles it uses: dictation-medical-de (defaultProfile of new recordings) and those it offers
# under "Nachbearbeiten". Pushing stores a new revision; it changes nothing else.
set -euo pipefail

app_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
read -r -a resona <<<"${RESONA_CLI:-resona}"
catalogue="${RESONA_PROFILE_CATALOGUE:-$app_dir/../../profiles}"
catalogue_profiles=(dictation-medical-de transcript-markdown-de transcript-polish-de)

if ! command -v "${resona[0]}" >/dev/null 2>&1; then
	echo "push-profiles: '${resona[0]}' not found; install resona-cli or set RESONA_CLI" >&2
	exit 1
fi

push() {
	"${resona[@]}" profiles push "$(basename "$1" .json)" "$1"
}

for file in "$app_dir"/profiles/*.json; do
	push "$file"
done

missing=0
for name in "${catalogue_profiles[@]}"; do
	if [[ -f "$catalogue/$name.json" ]]; then
		push "$catalogue/$name.json"
	else
		echo "push-profiles: $name.json not found in $catalogue" >&2
		missing=1
	fi
done
if [[ "$missing" -ne 0 ]]; then
	echo "push-profiles: set RESONA_PROFILE_CATALOGUE to the profiles/ directory of a Resona hub checkout" >&2
	exit 1
fi
