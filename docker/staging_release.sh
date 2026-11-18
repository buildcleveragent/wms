#!/bin/sh
set -eu

: "${WMS_IMAGE:?set WMS_IMAGE to an immutable repository@sha256 digest}"
case "$WMS_IMAGE" in
  *@sha256:*) ;;
  *) echo "refusing mutable image reference" >&2; exit 2 ;;
esac
. ./docker/release_services.sh

deploy_started=false
release_exit() {
  result=$?
  trap - EXIT
  if [ "$result" -ne 0 ] && [ "$deploy_started" = true ]; then
    stop_release_services || true
    echo "staging release failed: all writers remain stopped; verify recovery before restarting" >&2
  fi
  exit "$result"
}
trap release_exit EXIT

compose config --quiet
compose pull
compose run --rm web python /app/docker/check_volume_access.py
compose run --rm web python manage.py check --deploy
compose run --rm web python manage.py migrate --plan
deploy_started=true
stop_release_services
compose run --rm web python manage.py migrate --noinput
compose run --rm web python manage.py sync_wms_role_groups
compose run --rm web python manage.py collectstatic --noinput
start_release_services
check_release_services
compose ps
