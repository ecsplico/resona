# Encrypted off-site backup of the hub's own data (SQLite database + testbench
# items on the `resona-data` volume) to a restic repository. Service `backup` of
# docker-compose.coolify.yml. Runbook: docs/server/coolify.md#backup.
FROM restic/restic:0.18.1

# sqlite: consistent online dump via the SQLite backup API; tzdata: BACKUP_TIME is
# local time in $TZ.
RUN apk add --no-cache sqlite tzdata \
    && mkdir -p /state /cache && chown 1000:1000 /state /cache

COPY backup.sh /usr/local/bin/resona-hub-backup
RUN chmod 0755 /usr/local/bin/resona-hub-backup

# The hub's UID: dumps read the volume as its owner, no capabilities needed.
USER 1000:1000
ENV RESTIC_CACHE_DIR=/cache

ENTRYPOINT ["/usr/local/bin/resona-hub-backup"]
CMD ["schedule"]
