#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env.test.local"
COMPOSE_FILE="${ROOT_DIR}/compose.test.yml"
FRESH_DATABASE=false

if [[ "${1:-}" == "--fresh" ]]; then
  FRESH_DATABASE=true
  shift
fi

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

if [[ "${FRESH_DATABASE}" == "true" ]]; then
  docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" down --volumes
fi

docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" up -d --wait mysql-test
exec "${ROOT_DIR}/.venv/bin/pytest" --reuse-db "$@"
