#!/bin/sh
# resona-hub-backup: consistent SQLite dump of the hub database + restic snapshot
# + retention.
#   resona-hub-backup schedule   run once at start (BACKUP_ON_START), then daily at BACKUP_TIME;
#                                idles when RESTIC_REPOSITORY is not set (backup off)
#   resona-hub-backup run        one backup now
#   resona-hub-backup healthy    container healthcheck
# Source (mounted by docker-compose.coolify.yml):
#   /source/hub   the hub's resona-data volume: db/jjobs.sqlite is dumped (never
#                 copied raw), organizations/*/testbench kept. Job payloads are
#                 transient (24 h) and not backed up.
set -eu

SOURCE_DIR=${BACKUP_SOURCE_DIR:-/source/hub}
STATE_DIR=${BACKUP_STATE_DIR:-/state}
DUMP_DIR=${BACKUP_DUMP_DIR:-/dumps}
HOST_TAG=${BACKUP_HOST_TAG:-resona-hub}
LAST_SUCCESS="$STATE_DIR/last-success"
# Epoch of the first backup attempt of this `schedule` run, removed once it ran:
# until then (plus BACKUP_GRACE_SECONDS) a missing or old last-success is no failure.
FIRST_DUE="$STATE_DIR/first-due"

log() { echo "$(date '+%Y-%m-%dT%H:%M:%S%z') resona-hub-backup: $*"; }
die() { log "ERROR: $*"; exit 1; }

enabled() { [ -n "${RESTIC_REPOSITORY:-}" ]; }

# The repository password always; S3 keys only for an s3: repository.
require_env() {
	names="RESTIC_REPOSITORY RESTIC_PASSWORD"
	case ${RESTIC_REPOSITORY:-} in
		s3:*) names="$names AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY" ;;
	esac
	for name in $names; do
		eval "value=\${$name:-}"
		[ -n "$value" ] || die "$name is not set (required once RESTIC_REPOSITORY is set)"
	done
}

# Online, consistent copy through the SQLite backup API on a read-only connection.
dump_sqlite() {
	src=$1
	dest=$2
	[ -f "$src" ] || die "database not found: $src"
	rm -f "$dest"
	sqlite3 -readonly "$src" ".timeout 10000" ".backup '$dest'" || die "dump failed: $src"
	check=$(sqlite3 "$dest" "PRAGMA quick_check;") || die "dump unreadable: $dest"
	[ "$check" = "ok" ] || die "dump of $src fails quick_check: $check"
}

ensure_repo() {
	if ! restic cat config >/dev/null 2>&1; then
		log "repository not initialised yet, running restic init"
		restic init || die "restic init failed (wrong password for an existing repository?)"
	fi
}

backup_once() {
	require_env
	mkdir -p "$STATE_DIR" "$DUMP_DIR"
	ensure_repo

	# Fails loudly when the mounted volume is not the hub's (no database inside).
	dump_sqlite "$SOURCE_DIR/db/jjobs.sqlite" "$DUMP_DIR/hub-jjobs.sqlite"
	set -- "$DUMP_DIR"
	for dir in "$SOURCE_DIR"/organizations/*/testbench; do
		if [ -d "$dir" ]; then set -- "$@" "$dir"; fi
	done

	log "backing up: $*"
	restic backup --host "$HOST_TAG" --tag resona-hub "$@" || die "restic backup failed"

	# One retention group per host: the path set changes whenever an organization
	# gets its first testbench item, and restic groups by host+paths by default.
	log "applying retention"
	restic forget --host "$HOST_TAG" --group-by host --prune \
		--keep-daily "${BACKUP_KEEP_DAILY:-7}" \
		--keep-weekly "${BACKUP_KEEP_WEEKLY:-4}" \
		--keep-monthly "${BACKUP_KEEP_MONTHLY:-6}" || die "restic forget --prune failed"

	# Weekly (Sunday) integrity check of the repository structure plus a sample of data.
	if [ "$(date +%u)" = "7" ]; then
		log "weekly restic check"
		restic check --read-data-subset "${BACKUP_CHECK_SUBSET:-5%}" || die "restic check failed"
	fi

	rm -f "${DUMP_DIR:?}/hub-jjobs.sqlite"
	date +%s >"$LAST_SUCCESS"
	log "backup finished"
}

# Seconds until the next BACKUP_TIME (HH:MM, local time in $TZ).
seconds_until_next() {
	target=${BACKUP_TIME:-02:30}
	echo "$target" | grep -Eq '^([01][0-9]|2[0-3]):[0-5][0-9]$' \
		|| die "BACKUP_TIME must be HH:MM (00:00-23:59), got '$target'"
	th=$(echo "$target" | cut -d: -f1 | sed 's/^0//')
	tm=$(echo "$target" | cut -d: -f2 | sed 's/^0//')
	nh=$(date +%H | sed 's/^0//')
	nm=$(date +%M | sed 's/^0//')
	ns=$(date +%S | sed 's/^0//')
	wait=$(( ( (${th:-0} * 3600 + ${tm:-0} * 60) - (${nh:-0} * 3600 + ${nm:-0} * 60 + ${ns:-0}) + 86400 ) % 86400 ))
	[ "$wait" -gt 0 ] || wait=86400
	echo "$wait"
}

# Backup off: stay up doing nothing, so the compose service needs no profile and
# a deployment without a repository shows a healthy, idle container.
idle() {
	log "RESTIC_REPOSITORY is not set: backup is off"
	trap 'exit 0' TERM INT
	while true; do
		sleep 86400 &
		wait $!
	done
}

# A failed run is logged and retried at the next slot; the container healthcheck
# turns unhealthy once the last success is older than BACKUP_MAX_AGE_SECONDS.
schedule() {
	enabled || idle
	require_env
	first_wait=$(seconds_until_next)
	mkdir -p "$STATE_DIR"
	if [ "${BACKUP_ON_START:-true}" = "true" ]; then
		date +%s >"$FIRST_DUE"
		( backup_once ) || log "backup failed, next attempt at ${BACKUP_TIME:-02:30}"
		rm -f "$FIRST_DUE"
	else
		echo $(( $(date +%s) + first_wait )) >"$FIRST_DUE"
	fi
	while true; do
		wait=$(seconds_until_next)
		log "next backup in ${wait}s (${BACKUP_TIME:-02:30} ${TZ:-UTC})"
		sleep "$wait"
		( backup_once ) || log "backup failed, next attempt at ${BACKUP_TIME:-02:30}"
		rm -f "$FIRST_DUE"
	done
}

# Healthy while the backup is off, the last success is younger than
# BACKUP_MAX_AGE_SECONDS, or this start's first backup is not yet due
# (+ BACKUP_GRACE_SECONDS for it to run).
healthy() {
	enabled || exit 0
	now=$(date +%s)
	if [ -f "$LAST_SUCCESS" ] && [ $(( now - $(cat "$LAST_SUCCESS") )) -lt "${BACKUP_MAX_AGE_SECONDS:-93600}" ]; then
		exit 0
	fi
	[ -f "$FIRST_DUE" ] && [ "$now" -lt $(( $(cat "$FIRST_DUE") + ${BACKUP_GRACE_SECONDS:-7200} )) ]
}

case ${1:-schedule} in
	schedule) schedule ;;
	run) backup_once ;;
	healthy) healthy ;;
	*) exec "$@" ;;
esac
