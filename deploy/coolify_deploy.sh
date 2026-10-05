#!/bin/sh
# Trigger a Coolify deployment of the tutor application and wait for its result. Run by the CI
# deploy job (.github/workflows/ci.yml); also runnable by hand. Runbook: docs/v0/coolify.md.
# COOLIFY_URL       the dashboard origin, https, no trailing slash
# COOLIFY_TOKEN     API token with the read and deploy permissions only
# COOLIFY_APP_UUID  the application's uuid
# Coolify builds the head of the branch the application tracks (`production`); point it first.
# Exit 0 only when the deployment finished; failed, cancelled or timed out exit 1.
set -eu
: "${COOLIFY_URL:?}" "${COOLIFY_TOKEN:?}" "${COOLIFY_APP_UUID:?}"
case "$COOLIFY_URL" in
  https://*) ;;
  *) echo "COOLIFY_URL must be https (the token is sent with every call)" >&2; exit 1 ;;
esac
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-1500}"

api() {
  curl --fail-with-body --silent --show-error --max-time 30 \
    --header "Authorization: Bearer $COOLIFY_TOKEN" "$@"
}

deployment="$(api --get "$COOLIFY_URL/api/v1/deploy" \
  --data-urlencode "uuid=$COOLIFY_APP_UUID" --data-urlencode "force=false" \
  | jq -er '.deployments[0].deployment_uuid')"
echo "deployment $deployment queued"

waited=0
while :; do
  status="$(api "$COOLIFY_URL/api/v1/deployments/$deployment" | jq -er '.status')"
  case "$status" in
    finished)
      echo "deployment $deployment finished"
      exit 0
      ;;
    failed | cancelled*)
      echo "deployment $deployment $status; the build and container logs are in Coolify" >&2
      exit 1
      ;;
  esac
  if [ "$waited" -ge "$TIMEOUT_SECONDS" ]; then
    echo "deployment $deployment still $status after ${TIMEOUT_SECONDS}s" >&2
    exit 1
  fi
  sleep 15
  waited=$((waited + 15))
done
