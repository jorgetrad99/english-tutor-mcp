#!/bin/sh
# Nightly logical backup of the tutor database plus the cluster's roles, 30-day retention.
# Host cron (crontab -e as the deploy user):
#   30 3 * * * /opt/tutor/deploy/backup.sh >> /var/log/tutor-backup.log 2>&1
# Files (mode 600, dir 700; the roles file holds password hashes, the OAuth tarball holds tokens):
#   tutor-<UTC stamp>.dump        pg_dump --format=custom of the tutor database
#   tutor-roles-<UTC stamp>.sql   pg_dumpall --roles-only (roles are cluster-level, not in the dump)
#   tutor-oauth-<UTC stamp>.tgz   the OAuth store volume (not in pg_dump; Fernet-encrypted entries,
#                                 but keep it as protected as TUTOR_OAUTH_STORAGE_KEY)
# Off-site copies must be encrypted (runbook, "Backups"). Alert on stale backups: check_backup.sh.
set -eu
umask 077
cd "$(dirname "$0")"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/tutor}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
dump="$BACKUP_DIR/tutor-$stamp.dump"
roles="$BACKUP_DIR/tutor-roles-$stamp.sql"
# compose.prod.yml by default. Under Coolify (docs/v0/coolify.md, "Backups") set DB_CONTAINER to
# the db container's name and OAUTH_VOLUME to the oauth volume's name.
if [ -n "${DB_CONTAINER:-}" ]; then
  db="docker exec -i $DB_CONTAINER"
else
  db="docker compose -f compose.prod.yml exec -T db"
fi
OAUTH_VOLUME="${OAUTH_VOLUME:-tutor_oauth}"
oauth="$BACKUP_DIR/tutor-oauth-$stamp.tgz"
# Pinned like the other images; bump together with the runbook.
ALPINE="alpine@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc"

# Write to .partial files and rename only when complete, so a failed run never leaves a file that
# looks like a backup (and never deletes older ones: retention runs last).
$db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' > "$dump.partial"
$db sh -c 'pg_dumpall -U "$POSTGRES_USER" --roles-only' > "$roles.partial"
# A dump that pg_restore cannot list, or a roles file without the app role, is not a backup.
$db pg_restore --list < "$dump.partial" > /dev/null
grep -q 'tutor_app' "$roles.partial"
docker run --rm -v "$OAUTH_VOLUME":/data/oauth:ro "$ALPINE" tar czf - -C /data oauth > "$oauth.partial"
tar tzf "$oauth.partial" > /dev/null
mv "$dump.partial" "$dump"
mv "$roles.partial" "$roles"
mv "$oauth.partial" "$oauth"

find "$BACKUP_DIR" -type f \( -name 'tutor-*.dump' -o -name 'tutor-roles-*.sql' -o -name 'tutor-oauth-*.tgz' \) \
  -mtime +"$RETENTION_DAYS" -delete
find "$BACKUP_DIR" -type f -name '*.partial' -mtime +1 -delete
echo "backup ok: $dump $roles $oauth"
