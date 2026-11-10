# Encrypted off-site backup of the PWA data (Directus SQLite + uploads, optionally
# the hub's resona-data volume) to an S3-compatible restic repository.
# Runbook: docs/server/pwa-backups.md.
FROM restic/restic:0.18.1

# sqlite: consistent online dump via the SQLite backup API; tzdata: BACKUP_TIME is
# local time in $TZ.
RUN apk add --no-cache sqlite tzdata

COPY backup.sh /usr/local/bin/resona-backup
RUN chmod 0755 /usr/local/bin/resona-backup

ENTRYPOINT ["/usr/local/bin/resona-backup"]
CMD ["schedule"]
