#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  echo "usage: $0 <repository@sha256:digest>" >&2
  exit 2
fi
case "$1" in
  *@sha256:*) ;;
  *) echo "refusing mutable image reference: $1" >&2; exit 2 ;;
esac

: "${WMS_DB_BACKUP_SCRIPT:?set WMS_DB_BACKUP_SCRIPT to an executable verified backup script}"
: "${WMS_MEDIA_BACKUP_SCRIPT:?set WMS_MEDIA_BACKUP_SCRIPT to an executable verified backup script}"
test -x "$WMS_DB_BACKUP_SCRIPT"
test -x "$WMS_MEDIA_BACKUP_SCRIPT"

export WMS_IMAGE="$1"
observe_seconds="${WMS_OBSERVE_SECONDS:-3600}"
case "$observe_seconds" in
  ''|*[!0-9]*) echo "WMS_OBSERVE_SECONDS must be a non-negative integer" >&2; exit 2 ;;
esac

compose() {
  docker compose -f compose.production.yml "$@"
}

if command -v systemctl >/dev/null 2>&1; then
  for legacy_timer in wms-sale-mini-expire.timer wms-sale-mini-reconcile.timer; do
    if systemctl is-active --quiet "$legacy_timer"; then
      echo "refusing release while legacy timer is active: $legacy_timer" >&2
      exit 2
    fi
  done
fi

compose config --quiet
compose pull
compose run --rm web python manage.py check --deploy
compose run --rm web python manage.py migrate --plan
"$WMS_DB_BACKUP_SCRIPT"
"$WMS_MEDIA_BACKUP_SCRIPT"

compose stop scheduler web
compose run --rm web python manage.py migrate --noinput
compose run --rm web python manage.py sync_wms_role_groups
compose run --rm web python manage.py collectstatic --noinput
compose up -d web

ready_attempt=0
until compose exec -T web python -c \
  "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz/ready', timeout=5)"; do
  ready_attempt=$((ready_attempt + 1))
  if [ "$ready_attempt" -ge 24 ]; then
    echo "candidate readiness did not recover within 120 seconds" >&2
    compose stop web
    exit 1
  fi
  sleep 5
done

compose exec -T web python manage.py migrate --check
compose exec -T web python manage.py reconcile_data_accuracy --fail-on-issues
compose exec -T web python manage.py audit_dispatch_note_snapshots --fail-on-issues
compose up -d scheduler

scheduler_count="$(compose ps -q scheduler | wc -l | tr -d ' ')"
if [ "$scheduler_count" -ne 1 ]; then
  echo "expected exactly one scheduler container, found $scheduler_count" >&2
  compose stop scheduler web
  exit 1
fi

elapsed=0
while [ "$elapsed" -lt "$observe_seconds" ]; do
  if ! compose exec -T web python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz/ready', timeout=5)"; then
    echo "readiness failed during release observation; traffic must remain stopped" >&2
    compose stop scheduler web
    exit 1
  fi
  sleep 10
  elapsed=$((elapsed + 10))
done

compose exec -T web python manage.py reconcile_data_accuracy --fail-on-issues
compose exec -T web python manage.py audit_dispatch_note_snapshots --fail-on-issues
compose ps
