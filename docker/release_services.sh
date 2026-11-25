#!/bin/sh
# Shared by production and staging. Source from the repository root.
release_services="web scheduler boss-check-worker owner-archive-worker"

compose() {
  docker compose -f compose.production.yml "$@"
}

stop_release_services() {
  compose stop $release_services
}

check_release_instances() {
  expected_image=$(docker image inspect --format '{{.Id}}' "$WMS_IMAGE") || return 1
  [ -n "$expected_image" ] || return 1
  for service in "$@"; do
    containers=$(compose ps --all -q "$service") || return 1
    # Container IDs contain no shell metacharacters or whitespace.
    set -- $containers
    if [ "$#" -ne 1 ]; then
      echo "expected exactly one $service container, found $#" >&2
      return 1
    fi
    actual=$(docker inspect --format '{{.State.Running}} {{.Image}}' "$1") || return 1
    if [ "$actual" != "true $expected_image" ]; then
      echo "$service is stopped or is not running the candidate image" >&2
      return 1
    fi
  done
}

check_release_web() {
  timeout 15 docker compose -f compose.production.yml exec -T web python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz/ready', timeout=5)"
}

check_release_worker() {
  worker_id=$(compose ps --all -q boss-check-worker) || return 1
  worker_started=$(docker inspect --format '{{.State.StartedAt}}' "$worker_id") || return 1
  [ -n "$worker_started" ] || return 1
  timeout 15 docker compose -f compose.production.yml exec -T boss-check-worker \
    python manage.py check_boss_worker --started-after "$worker_started"
}

wait_for_release_probe() {
  deadline=$(($(date +%s) + 120))
  remaining=120
  until timeout "$remaining" sh -c '. ./docker/release_services.sh; "$@"' sh "$@"; do
    remaining=$((deadline - $(date +%s)))
    if [ "$remaining" -le 5 ]; then
      echo "$* did not recover within the release readiness window" >&2
      return 1
    fi
    sleep 5
    remaining=$((remaining - 5))
  done
}

start_release_services() {
  compose up -d web
  check_release_instances web
  wait_for_release_probe check_release_web
  compose exec -T web python manage.py migrate --check
  compose exec -T web python manage.py reconcile_data_accuracy --fail-on-issues
  compose exec -T web python manage.py audit_dispatch_note_snapshots --fail-on-issues
  compose up -d scheduler boss-check-worker owner-archive-worker
  check_release_instances $release_services
  wait_for_release_probe check_release_worker
}

check_release_services() {
  check_release_instances $release_services && check_release_web && check_release_worker
}
