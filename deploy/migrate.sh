#!/bin/sh
# One-shot `migrate` service: apply migrations as the database owner, then create or update
# the app's LOGIN role and the gate-report role. Needs MIGRATION_DATABASE_URL, APP_DB_USER,
# APP_DB_PASSWORD and REPORT_DB_PASSWORD (the migrate env file); the app service never sees them.
set -eu
alembic -c /app/alembic.ini upgrade head
exec python -m tutor.ops.provision_roles
