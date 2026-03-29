#!/bin/sh
set -eu

python manage.py migrate --noinput

python manage.py billing_run_scheduler &
scheduler_pid=$!

python manage.py runserver 0.0.0.0:8000 &
web_pid=$!

term_handler() {
  kill "$web_pid" 2>/dev/null || true
  kill "$scheduler_pid" 2>/dev/null || true
}

trap term_handler INT TERM

wait "$web_pid"
web_status=$?

kill "$scheduler_pid" 2>/dev/null || true
wait "$scheduler_pid" 2>/dev/null || true

exit "$web_status"
