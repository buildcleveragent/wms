#!/bin/sh
set -eu

exec python manage.py billing_run_scheduler
