#!/bin/sh
# Nightly logical backup of the tutor database plus the cluster's roles, 30-day retention.
# Host cron (crontab -e as the deploy user):
#   30 3 * * * /opt/tutor/deploy/backup.sh >> /var/log/tutor-backup.log 2>&1
# Files (mode 600, dir 700; the roles file holds password hashes):
#   tutor-<UTC stamp>.dump        pg_dump --format=custom of the tutor database
#   tutor-roles-<UTC stamp>.sql   pg_dumpall --roles-only (roles are cluster-level, not in the dump)
# NOT included: the OAuth store volume `oauth` (see the runbook: losing it makes clients re-register).
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
compose="docker compose -f compose.prod.yml"

# Write to .partial files and rename only when complete, so a failed run never leaves a file that
# looks like a backup (and never deletes older ones: retention runs last).
$compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' \
  > "$dump.partial"
$compose exec -T db sh -c 'pg_dumpall -U "$POSTGRES_USER" --roles-only' > "$roles.partial"
# A dump that pg_restore cannot list, or a roles file without the app role, is not a backup.
$compose exec -T db pg_restore --list < "$dump.partial" > /dev/null
grep -q 'tutor_app' "$roles.partial"
mv "$dump.partial" "$dump"
mv "$roles.partial" "$roles"

find "$BACKUP_DIR" -type f \( -name 'tutor-*.dump' -o -name 'tutor-roles-*.sql' \) \
  -mtime +"$RETENTION_DAYS" -delete
find "$BACKUP_DIR" -type f -name '*.partial' -mtime +1 -delete
echo "backup ok: $dump $roles"
