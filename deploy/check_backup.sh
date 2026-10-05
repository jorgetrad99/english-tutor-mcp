#!/bin/sh
# Exit 1 (and say why) when the newest database dump is missing or older than MAX_AGE_HOURS (26).
# Cron, an hour after the backup, with a mail or webhook on failure, for example:
#   30 4 * * * /opt/tutor/deploy/check_backup.sh || echo "tutor backup stale" | mail -s tutor <you>
set -eu
BACKUP_DIR="${BACKUP_DIR:-/var/backups/tutor}"
MAX_AGE_HOURS="${MAX_AGE_HOURS:-26}"
newest="$(ls -1t "$BACKUP_DIR"/tutor-2*.dump 2>/dev/null | head -n 1 || true)"
if [ -z "$newest" ]; then
  echo "no dump in $BACKUP_DIR" >&2
  exit 1
fi
if [ -n "$(find "$newest" -mmin +"$((MAX_AGE_HOURS * 60))")" ]; then
  echo "newest dump is older than ${MAX_AGE_HOURS} h: $newest" >&2
  exit 1
fi
echo "newest dump ok: $newest"
