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

# A second invocation must never restart writers inside the first one's backup window.
exec 9>"${WMS_RELEASE_LOCK_FILE:-/run/lock/wms-production-release.lock}"
flock -n 9 || { echo "Another production release holds the writer lock." >&2; exit 1; }
payment_timers="wms-sale-mini-expire.timer wms-sale-mini-reconcile.timer"
payment_services="wms-sale-mini-expire.service wms-sale-mini-reconcile.service"
command -v systemctl >/dev/null 2>&1 || {
  echo "payment scheduling requires the repository systemd units" >&2
  exit 2
}
for timer in $payment_timers; do
  systemctl cat "$timer" >/dev/null
done

previous_timers=""
previous_containers=""
phase=preflight
unit_state() { systemctl show --property=ActiveState --value "$1"; }
drain_payment_jobs() {
  waited=0
  while :; do
    pending=false
    for service in $payment_services; do
      state=$(unit_state "$service") || return 1
      case "$state" in inactive|failed) ;; *) pending=true ;; esac
    done
    [ "$pending" = true ] || return 0
    if [ "$waited" -ge 300 ]; then
      echo "Payment jobs did not quiesce within five minutes." >&2
      return 1
    fi
    sleep 5
    waited=$((waited + 5))
  done
}
verify_writers_stopped() {
  remaining=$(compose --profile oneshot ps --status running -q) || return 1
  if [ -n "$remaining" ]; then
    echo "Compose writers still running." >&2
    return 1
  fi
}
resume_previous_containers() {
  [ -n "$previous_containers" ] || return 0
  docker start $previous_containers || return 1
  for id in $previous_containers; do
    running=$(docker inspect --format '{{.State.Running}}' "$id") || return 1
    [ "$running" = true ] || return 1
  done
  web=$(compose ps --status running -q web) || return 1
  worker=$(compose ps --status running -q boss-check-worker) || return 1
  if [ -n "$web" ]; then wait_for_release_probe check_release_web || return 1; fi
  if [ -n "$worker" ]; then wait_for_release_probe check_release_worker || return 1; fi
  return 0
}
release_exit() {
  result=$?
  trap - EXIT HUP INT TERM
  if [ "$result" -ne 0 ] && [ "$phase" = migrating ]; then
    stopped=true
    systemctl stop $payment_timers || stopped=false
    stop_release_services || stopped=false
    if [ "$stopped" = true ] && drain_payment_jobs && verify_writers_stopped; then
      echo "release failed: writers and payment timers remain stopped; restore the verified database/media backup and previous image before restarting all schedulers" >&2
    else
      echo "Unable to confirm all writers stopped; drain remaining writers before any restore." >&2
    fi
  elif [ "$result" -ne 0 ] && [ "$phase" != preflight ]; then
    # Start the exact previous containers. 'compose up' would deploy the candidate.
    if { [ "$phase" != freezing ] || resume_previous_containers; } &&
       { [ -z "$previous_timers" ] || systemctl start $previous_timers; }; then
      echo "Release aborted before migration; previous writer state restored." >&2
    else
      systemctl stop $payment_timers || true
      stop_release_services || true
      echo "Failed to resume the previous writers; check the old containers before restoring traffic." >&2
    fi
  fi
  exit "$result"
}
trap release_exit EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

compose config --quiet
compose pull
compose run --rm web python /app/docker/check_volume_access.py
compose run --rm web python manage.py check --deploy
compose run --rm web python manage.py migrate --plan

legacy=$(systemctl list-units --no-legend --plain --state=active,activating,deactivating,reloading \
  wms-boss-deadlines.timer wms-boss-deadlines.service)
if [ -n "$legacy" ]; then
  echo "Retired Boss deadline units still active; drain them before release." >&2
  exit 1
fi
for timer in $payment_timers; do
  state=$(unit_state "$timer")
  case "$state" in
    active) previous_timers="$previous_timers $timer" ;;
    inactive|failed) ;;
    *) echo "Payment timer $timer is $state; retry after it settles." >&2; exit 1 ;;
  esac
done
container_ids=$(compose ps --status running -q $release_services)
for id in $container_ids; do
  oneoff=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.oneoff"}}' "$id")
  case "$oneoff" in
    False|false) previous_containers="${previous_containers:+$previous_containers }$id" ;;
    *) echo "Drain one-off or unidentified container $id before release." >&2; exit 1 ;;
  esac
done
phase=draining
systemctl stop $payment_timers
drain_payment_jobs
phase=freezing
stop_release_services
verify_writers_stopped
python3 docker/create_release_backup.py "${WMS_BACKUP_EVIDENCE_DIR:-/var/lib/wms/release-backups}" \
  "$WMS_DB_BACKUP_SCRIPT" "$WMS_MEDIA_BACKUP_SCRIPT"
phase=migrating
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
