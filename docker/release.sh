#!/bin/sh
set -eu

python manage.py migrate --noinput
python manage.py sync_wms_role_groups
