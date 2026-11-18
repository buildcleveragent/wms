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

. ./docker/release_services.sh

payment_timers="wms-sale-mini-expire.timer wms-sale-mini-reconcile.timer"
payment_services="wms-sale-mini-expire.service wms-sale-mini-reconcile.service"
command -v systemctl >/dev/null 2>&1 || {
  echo "payment scheduling requires the repository systemd units" >&2
  exit 2
}
for timer in $payment_timers; do
  systemctl cat "$timer" >/dev/null
 done

deploy_started=false
release_exit() {
  result=$?
  trap - EXIT
  if [ "$result" -ne 0 ] && [ "$deploy_started" = true ]; then
    systemctl stop $payment_timers || true
    stop_release_services || true
    echo "release failed: writers and payment timers remain stopped; restore the verified database/media backup and previous image before restarting all schedulers" >&2
  fi
  exit "$result"
}
trap release_exit EXIT

compose config --quiet
compose pull
compose run --rm web python /app/docker/check_volume_access.py
compose run --rm web python manage.py check --deploy
compose run --rm web python manage.py migrate --plan
"$WMS_DB_BACKUP_SCRIPT"
"$WMS_MEDIA_BACKUP_SCRIPT"

systemctl stop $payment_timers
waited=0
while systemctl is-active --quiet wms-sale-mini-expire.service || systemctl is-active --quiet wms-sale-mini-reconcile.service; do
  if [ "$waited" -ge 300 ]; then
    systemctl start $payment_timers
    echo "payment jobs did not quiesce; release aborted before stopping web or migrating" >&2
    exit 1
  fi
  sleep 5
  waited=$((waited + 5))
done
deploy_started=true
stop_release_services
compose run --rm web python manage.py migrate --noinput
compose run --rm web python manage.py sync_wms_role_groups
compose run --rm web python manage.py collectstatic --noinput
start_release_services

systemctl start $payment_timers
for timer in $payment_timers; do
  systemctl is-active --quiet "$timer"
done
# Exercise the actual commands once; a running timer alone is not proof of work.
for service in $payment_services; do
  systemctl start "$service"
done

elapsed=0
while [ "$elapsed" -lt "$observe_seconds" ]; do
  for timer in $payment_timers; do
    systemctl is-active --quiet "$timer"
  done
  if ! check_release_services; then
    echo "service readiness failed during release observation; traffic must remain stopped" >&2
    stop_release_services
    exit 1
  fi
  sleep 10
  elapsed=$((elapsed + 10))
done

compose exec -T web python manage.py reconcile_data_accuracy --fail-on-issues
compose exec -T web python manage.py audit_dispatch_note_snapshots --fail-on-issues
check_release_services
compose ps
