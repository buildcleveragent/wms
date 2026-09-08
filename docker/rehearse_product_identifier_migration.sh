#!/usr/bin/env bash
# Historical production-backup replay is retired: use synthetic data in a new
# isolated MySQL instance whose only business schema is wms_db_test.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "$#" -ne 0 ]]; then
  echo "迁移验收只使用合成数据；不接受业务备份或其他数据库名。" >&2
  exit 2
fi
cd "$ROOT_DIR"
exec "${ROOT_DIR}/.venv/bin/python" -m pytest --reuse-db -q allapp/products/test_identifier_migrations.py
