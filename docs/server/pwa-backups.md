# PWA backups and encryption at rest

The PWA stack (`apps/resona-pwa/docker-compose.coolify.yml`) keeps everything a user produces in two Docker volumes: `directus-db` (Directus SQLite, `data.db`) and `directus-uploads` (recordings and documents). The hub keeps its SQLite database and testbench items on `resona-data` (root `docker-compose.coolify.yml`). This page covers the opt-in encrypted off-site backup of those volumes, the restore drill, who holds the keys, and the state of disk encryption on the host.

## What the backup service does

The compose service `backup` (profile `backup`, image built from `apps/resona-pwa/backup/`) is [restic](https://restic.net) with a small script:

1. Dumps `data.db` through the SQLite backup API on a read-only connection (`sqlite3 -readonly … .backup`) and runs `PRAGMA quick_check` on the dump. The live file is never copied raw.
2. Optionally dumps the hub's `db/jjobs.sqlite` the same way and adds `organizations/*/testbench`. Hub job payloads are transient and not backed up (see [Coolify: Restore a backup](coolify.md#restore-a-backup)).
3. `restic backup` of the dumps and `directus-uploads` to the repository. restic encrypts client-side (AES-256 + Poly1305), so the storage provider only sees ciphertext.
4. `restic forget --prune --keep-daily 7 --keep-weekly 4 --keep-monthly 6`.
5. On Sundays, `restic check --read-data-subset 5%`.

It runs once at container start, then daily at `BACKUP_TIME` (local time in `BACKUP_TZ`, `00:00`–`23:59`). A failed run is logged and retried at the next slot. The container healthcheck turns **unhealthy** once the last successful backup is older than 26 h, so Coolify shows a failing backup. The time of the last success lives in the `backup-state` volume, so it survives restarts; until the first backup after a start is due (immediately, or with `BACKUP_ON_START=false` the next `BACKUP_TIME`) plus 2 h for it to run, a missing or old success does not count as unhealthy.

Snapshot layout (paths inside the repository):

|Path|Content|
|---|---|
|`/dumps/directus.db`|Directus database dump|
|`/dumps/hub-jjobs.sqlite`|hub database dump (only with `HUB_DATA_VOLUME`)|
|`/source/directus/uploads/`|Directus uploads|
|`/source/hub/organizations/<id>/testbench/`|hub testbench items (only with `HUB_DATA_VOLUME`)|

## Setup

1. **Bucket.** In the Hetzner console create an Object Storage bucket (private) in one location, e.g. `fsn1`, and an S3 credential pair. Use a bucket only for these backups. The endpoint is `https://<location>.your-objectstorage.com`.
2. **Repository password.** Generate it offline and store it as described in [Key custody](#key-custody) *before* the first run:

    ```bash
    openssl rand -base64 33
    ```

3. **Coolify env** of the `resona-pwa` app:

    |Variable|Value|
    |---|---|
    |`COMPOSE_PROFILES`|`backup` (enables the service; Compose reads it from the generated `.env`)|
    |`RESTIC_REPOSITORY`|`s3:https://fsn1.your-objectstorage.com/<bucket>/resona`|
    |`RESTIC_PASSWORD`|the repository password|
    |`BACKUP_S3_ACCESS_KEY_ID`|S3 access key|
    |`BACKUP_S3_SECRET_ACCESS_KEY`|S3 secret key|
    |`BACKUP_S3_REGION`|optional, e.g. `fsn1`|
    |`BACKUP_TIME`|optional, `HH:MM`, default `02:30`|
    |`BACKUP_TZ`|optional, default `Europe/Berlin`|
    |`BACKUP_KEEP_DAILY` / `_WEEKLY` / `_MONTHLY`|optional, default `7` / `4` / `6`|
    |`BACKUP_ON_START`|optional, default `true`|
    |`HUB_DATA_VOLUME`|optional, see below|

    The variables are checked by the script, not by Compose: a deployment without the `backup` profile needs none of them, and the backup container exits with a message naming the first missing one.

4. **Deploy.** The first run initialises the repository (`restic init`) when it is empty. Check the log of the `backup` container for `backup finished`, then list the snapshots:

    ```bash
    docker exec <app-uuid>-backup-1 restic snapshots
    docker exec <app-uuid>-backup-1 restic ls latest | head
    ```

### Including the hub's `resona-data` volume

The hub runs as a separate Coolify app on the same Docker host, so its volume has a Coolify-generated name (`<hub-app-uuid>_resona-data`). Find it on the host and set it in the PWA app env:

```bash
docker volume ls --format '{{.Name}}' | grep '_resona-data$'
```

```
HUB_DATA_VOLUME=<hub-app-uuid>_resona-data
```

The backup service then mounts that volume at `/source/hub` and sets `BACKUP_HUB=true`. Without `HUB_DATA_VOLUME` it mounts an empty placeholder volume `resona-pwa-backup-no-hub` and ignores it. If the mounted volume holds no `db/jjobs.sqlite` (wrong name, or Coolify renamed the volume), every run fails and the container turns unhealthy: confirm after the first run with `restic ls latest | grep hub-jjobs`.

If the hub moves to another host, run the same image there with only `/source/hub` mounted, a separate `BACKUP_HOST_TAG` and the same repository, or give it its own repository.

## Restore drill

Run it after setup and then every quarter, on a machine that is **not** the production host. It proves the password, the S3 keys and the dumps all work.

```bash
export RESTIC_REPOSITORY='s3:https://fsn1.your-objectstorage.com/<bucket>/resona'
export AWS_ACCESS_KEY_ID=… AWS_SECRET_ACCESS_KEY=…
read -rs RESTIC_PASSWORD && export RESTIC_PASSWORD
alias restic='docker run --rm -i -e RESTIC_REPOSITORY -e RESTIC_PASSWORD -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -v "$PWD/restore:/restore" restic/restic:0.18.1'

restic snapshots
restic restore latest --target /restore
sqlite3 restore/dumps/directus.db 'PRAGMA integrity_check; SELECT count(*) FROM recordings; SELECT count(*) FROM directus_files;'
ls restore/source/directus/uploads | wc -l
```

The two counts should roughly match the uploads count and the live app. Record date, snapshot ID and counts in the ops log.

### Restoring production

1. Stop the app: `coolify app stop <app-uuid>`.
2. Restore the snapshot on the host as above (`restic restore <snapshot-id> --target /restore`).
3. Copy the data into the volumes, owned by the `node` user (uid 1000) Directus runs as:

    ```bash
    docker run --rm -v <app-uuid>_directus-db:/d -v /restore:/r alpine \
      sh -c 'rm -f /d/data.db-journal /d/data.db-wal /d/data.db-shm && cp /r/dumps/directus.db /d/data.db && chown 1000:1000 /d/data.db'
    docker run --rm -v <app-uuid>_directus-uploads:/u -v /restore:/r alpine \
      sh -c 'cp -a /r/source/directus/uploads/. /u/ && chown -R 1000:1000 /u'
    ```

4. Start the app: `coolify app start <app-uuid>`. `directus-bootstrap` re-runs idempotently.
5. Hub (only if it is lost too): copy `/restore/dumps/hub-jjobs.sqlite` to `/data/backup.sqlite` on the hub volume and follow [Coolify: Restore a backup](coolify.md#restore-a-backup) (`resona-admin restore`; it revokes all credentials and workers).
6. Delete `/restore` from the host.

Users signed in through Keycloak keep their accounts (matched by e-mail); their sessions start fresh.

## Key custody

- **`RESTIC_PASSWORD` is the only key to the data.** Without it the repository cannot be decrypted, by us or anyone else. Keep it in at least two places outside the server: the team password manager (entry "Resona backup restic", readable by two named admins) and a sealed paper copy in the office safe. The copy in the Coolify env is only for the running service and lives on the same host it protects.
- **S3 keys** grant access to ciphertext only. Use keys limited to the backup bucket; rotate them in the Hetzner console and in the Coolify env.
- **Rotate the repository password** without re-uploading: `restic key add` (new password), update Coolify env and both offline copies, verify with `restic snapshots`, then `restic key list` and `restic key remove <old-id>`.
- On staff changes, rotate both the S3 keys and the repository password.
- restic stores data encrypted with a master key that the passwords unlock. A leaked password therefore stays dangerous for existing snapshots even after `key remove` if the attacker also copied the repository's `keys/` files; after a leak, start a new repository with a new password and delete the old one once the new one holds enough history.

## Encryption at rest (Verschlüsselung im Ruhezustand)

### Finding (2026-11-10, read-only inspection)

The Coolify host is a netcup KVM server (`/sys/class/dmi/id/sys_vendor` = `netcup`). Its disk is **not encrypted at the OS level**:

```
NAME   TYPE FSTYPE  SIZE MOUNTPOINT
vda    disk         320G
├─vda1 part           2M
├─vda2 part ext4      1G /boot
├─vda3 part ext4      9G /
└─vda4 part ext4    310G /srv
```

- No `crypt`/`luks`/device-mapper entries in `/proc/mounts`, `cryptsetup` is not installed.
- Docker's root is `/srv/docker` on plain ext4 `/dev/vda4`, so every volume (`directus-db`, `directus-uploads`, `resona-data`) is stored unencrypted. `/data` (Coolify, including its env secrets) is a symlink to `/srv/data`, and the 16 GB swap file is `/srv/swapfile` on the same partition.
- Whether netcup encrypts its storage backend is not visible from the guest and not something we can rely on.

The restic backups are encrypted; the live data is not. Protection against someone with access to the hypervisor storage or a discarded disk needs LUKS.

To repeat the check:

```bash
ssh root@<host> 'lsblk -o NAME,TYPE,FSTYPE,SIZE,MOUNTPOINT; grep -E "crypt|luks|mapper" /proc/mounts; docker info --format "{{.DockerRootDir}}"; findmnt -T "$(docker info --format "{{.DockerRootDir}}")"'
```

### LUKS migration outline

`/srv` holds everything worth protecting (Docker, Coolify, swap), so encrypting `vda4` is enough; `/` and `/boot` stay plain so the server can boot to an unlock prompt.

**Option A: new server, restore from backup (preferred).** Order a new server, install Debian/Ubuntu with an encrypted data partition, set up remote unlock (below), install Coolify, redeploy the apps and restore the volumes as in [Restoring production](#restoring-production). No in-place risk; the old server stays as fallback until the new one is verified.

**Option B: in-place encryption of `vda4`.** Requires a maintenance window of several hours for 310 GB.

1. Fresh backup and a successful restore drill. Also `restic` the parts of `/srv/data/coolify` you need to rebuild Coolify.
2. Install `cryptsetup` and a remote unlock method: `dropbear-initramfs` (SSH into the initramfs, type the passphrase) or `clevis` + a `tang` server on another host (automatic unlock while that host is reachable). netcup's VNC console works as a manual fallback.
3. Stop Coolify and Docker, `swapoff /srv/swapfile`, `umount /srv`.
4. `e2fsck -f /dev/vda4`, then shrink the filesystem by at least 32 MiB to make room for the LUKS2 header: `resize2fs /dev/vda4 <current size − 64M>`.
5. `cryptsetup reencrypt --encrypt --type luks2 --reduce-device-size 32M /dev/vda4` (online progress; do not interrupt, it resumes after a crash with `cryptsetup reencrypt --resume-only`).
6. `cryptsetup open /dev/vda4 srv`, `resize2fs /dev/mapper/srv`, then `/etc/crypttab`: `srv UUID=<luks-uuid> none luks,discard,initramfs` and in `/etc/fstab` mount `/dev/mapper/srv` on `/srv`.
7. `update-initramfs -u`, reboot, unlock remotely, check `lsblk` shows `crypt` under `vda4`, start Docker and Coolify, re-run the check above.
8. Store `cryptsetup luksHeaderBackup /dev/vda4 --header-backup-file …` and the passphrase with the same custody as `RESTIC_PASSWORD`.

Either way, every reboot then needs an unlock (manual or tang), which is the trade-off for encryption at rest.
