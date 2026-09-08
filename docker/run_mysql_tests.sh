#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env.test.local"
COMPOSE_FILE="${ROOT_DIR}/compose.test.yml"
MIGRATE_ONLY=false

while [[ "${1:-}" == --* ]]; do
  case "${1}" in
    --fresh)
      echo "拒绝删除测试卷；确需重建 schema 请使用 --create-db。" >&2
      exit 2
      ;;
    --clean)
      echo "请使用默认 --reuse-db；发布验证使用 --create-db。" >&2
      exit 2
      ;;
    --migrate-only)
      MIGRATE_ONLY=true
      ;;
    *)
      break
      ;;
  esac
  shift
done

for test_arg in "$@"; do
  case "$test_arg" in
    -n|--numprocesses|--numprocesses=*|-n[0-9]*|--no-migrations|--nomigrations|--fast-db)
      echo "拒绝非规范测试数据库或绕过迁移的参数。" >&2
      exit 2
      ;;
  esac
done

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "缺少 ${ENV_FILE}，请从 .env.test.example 复制并填写本地测试密码。" >&2
  exit 2
fi

set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

if [[ "${APP_ENV:-}" != "test" ]]; then
  echo "拒绝执行：APP_ENV 必须为 test。" >&2
  exit 2
fi
if [[ "${DB_HOST:-}" != "127.0.0.1" && "${DB_HOST:-}" != "localhost" ]]; then
  echo "拒绝执行：DB_HOST 必须指向本机隔离测试容器。" >&2
  exit 2
fi
if [[ "${DB_PORT:-}" != "${WMS_TEST_DB_PORT:-33306}" || "${DB_PORT:-}" != "33306" ]]; then
  echo "拒绝执行：测试数据库端口必须为 33306。" >&2
  exit 2
fi
if [[ "${DB_NAME:-}" != "wms_db_test" || "${DB_TEST_NAME:-}" != "wms_db_test" ]]; then
  echo "拒绝执行：数据库名称必须为 wms_db_test。" >&2
  exit 2
fi
if [[ "${DB_USER:-}" != "wms_test" ]]; then
  echo "拒绝执行：数据库用户必须为 wms_test。" >&2
  exit 2
fi
if [[ "${REDIS_HOST:-}" != "127.0.0.1" && "${REDIS_HOST:-}" != "localhost" ]]; then
  echo "拒绝执行：REDIS_HOST 必须指向本机隔离测试容器。" >&2
  exit 2
fi
if [[ "${REDIS_PORT:-}" != "${WMS_TEST_REDIS_PORT:-36379}" || "${REDIS_PORT:-}" != "36379" ]]; then
  echo "拒绝执行：测试 Redis 端口必须为 36379。" >&2
  exit 2
fi

docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" up -d --wait \
  --wait-timeout 300 mysql-test redis-test
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" \
  exec -T redis-test redis-cli ping >/dev/null
# Use the isolated container's root account for explicit test schema privileges.
export DB_USER=root
export DB_PASSWORD="${MYSQL_ROOT_PASSWORD}"
"${ROOT_DIR}/.venv/bin/python" -c 'import os; os.environ.setdefault("DJANGO_SETTINGS_MODULE", "wmsmaster.settings"); import django; django.setup(); from django.conf import settings; from wmsmaster.test_database import verify_test_database; verify_test_database(settings.DATABASES["default"])'
if [[ "${MIGRATE_ONLY}" == "true" ]]; then
  started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  started_seconds="${SECONDS}"
  echo "Starting isolated MySQL migrations at ${started_at}."
  "${ROOT_DIR}/.venv/bin/python" scripts/test_migrate.py \
    --artifacts "${ROOT_DIR}/var/system-tests/manual-migration-$(date -u +%Y%m%dT%H%M%S)-$$"
  echo "Completed isolated MySQL migrations in $((SECONDS - started_seconds)) seconds."
  exit 0
fi
PYTEST_BIN="${WMS_TEST_PYTEST_BIN:-${ROOT_DIR}/.venv/bin/pytest}"
if [[ ! -x "${PYTEST_BIN}" ]]; then
  echo "拒绝执行：pytest 可执行文件不存在或不可执行：${PYTEST_BIN}" >&2
  exit 2
fi
exec "${PYTEST_BIN}" --reuse-db "$@"
