#!/bin/sh
set -eu

exec gunicorn wmsmaster.wsgi:application \
  --bind "${GUNICORN_BIND:-0.0.0.0:8000}" \
  --workers "${GUNICORN_WORKERS:-3}" \
  --timeout "${GUNICORN_TIMEOUT:-60}" \
  --access-logfile - \
  --error-logfile -
