#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env.test.local"
BACKUP_FILE="${1:-${ROOT_DIR}/backups/wms_db_recovered_20260808_013200.sql}"
REPORT_FILE="${2:-${ROOT_DIR}/docs/reports/product-identifier-migration-rehearsal.json}"
DATABASE_NAME="wms_migration_rehearsal_$(date +%Y%m%d_%H%M%S)_$$"
PYTHON_BIN="${WMS_TEST_PYTHON_BIN:-${ROOT_DIR}/.venv/bin/python}"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "缺少本机测试环境文件。" >&2
  exit 2
fi
if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "拒绝执行：Python 解释器不可执行。" >&2
  exit 2
fi
if [[ "$("${PYTHON_BIN}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" != "3.12" ]]; then
  echo "拒绝执行：迁移演练必须使用 Python 3.12。" >&2
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
  echo "拒绝执行：只允许回环地址。" >&2
  exit 2
fi
if [[ "${DB_PORT:-}" != "33306" ]]; then
  echo "拒绝执行：只允许隔离测试端口 33306。" >&2
  exit 2
fi
if [[ ! "${DATABASE_NAME}" =~ ^wms_migration_rehearsal_[0-9_]+$ ]]; then
  echo "拒绝执行：一次性数据库名称不安全。" >&2
  exit 2
fi
if [[ "$(realpath "${BACKUP_FILE}")" != "${ROOT_DIR}/backups/wms_db_recovered_20260808_013200.sql" ]]; then
  echo "拒绝执行：仅允许已授权的本机备份。" >&2
  exit 2
fi
backup_mode="$(stat -c '%a' "${BACKUP_FILE}")"
if (( (8#${backup_mode}) & 8#222 )); then
  echo "拒绝执行：授权备份必须先设置为只读。" >&2
  exit 2
fi
if grep -Eq '^(CREATE DATABASE|DROP DATABASE|USE |CREATE USER|GRANT |SET GLOBAL)' \
  "${BACKUP_FILE}"; then
  echo "拒绝执行：备份包含可切换或修改隔离数据库之外状态的语句。" >&2
  exit 2
fi

case "$(realpath -m "$(dirname "${REPORT_FILE}")")" in
  "${ROOT_DIR}/docs/reports"|/tmp) ;;
  *)
    echo "拒绝执行：汇总报告只能写入 docs/reports 或 /tmp。" >&2
    exit 2
    ;;
esac

mkdir -p "$(dirname "${REPORT_FILE}")"
migration_log="$(mktemp)"
prepare_log="$(mktemp)"
audit_log="$(mktemp)"
backup_sha_before="$(sha256sum "${BACKUP_FILE}" | cut -d' ' -f1)"
database_created=false

mysql_cmd=(
  mysql
  --protocol=TCP
  --host="${DB_HOST}"
  --port="${DB_PORT}"
  --user=root
  --batch
  --skip-column-names
)

cleanup() {
  rm -f "${migration_log}" "${prepare_log}" "${audit_log}"
  if [[ "${database_created}" == "true" ]]; then
    MYSQL_PWD="${MYSQL_ROOT_PASSWORD}" "${mysql_cmd[@]}" \
      --execute="DROP DATABASE IF EXISTS \`${DATABASE_NAME}\`;" >/dev/null
  fi
}
trap cleanup EXIT

MYSQL_PWD="${MYSQL_ROOT_PASSWORD}" "${mysql_cmd[@]}" \
  --execute="CREATE DATABASE \`${DATABASE_NAME}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;" \
  >/dev/null
database_created=true

MYSQL_PWD="${MYSQL_ROOT_PASSWORD}" "${mysql_cmd[@]}" "${DATABASE_NAME}" \
  < "${BACKUP_FILE}" >/dev/null

sql_count() {
  MYSQL_PWD="${MYSQL_ROOT_PASSWORD}" "${mysql_cmd[@]}" "${DATABASE_NAME}" \
    --execute="$1"
}

migration_latest_raw="$(sql_count \
  "SELECT COALESCE(MAX(name), '') FROM django_migrations WHERE app='products';")"
migration_0008_present_raw="$(sql_count \
  "SELECT COUNT(*) FROM django_migrations
   WHERE app='products' AND name='0008_product_carton_package';")"
migration_0009_present="$(sql_count \
  "SELECT COUNT(*) FROM django_migrations
   WHERE app='products'
     AND name='0009_remove_productidentifierregistry_product_package_and_more';")"
if [[ ! "${migration_latest_raw}" =~ ^000[1-8]_ || "${migration_0009_present}" != "0" ]]; then
  echo "迁移演练拒绝继续：授权备份不在 products 0001–0008 的允许范围。" >&2
  exit 1
fi

products_before="$(sql_count 'SELECT COUNT(*) FROM products_product;')"
packages_before="$(sql_count 'SELECT COUNT(*) FROM products_productpackage;')"
legacy_values_before="$(sql_count \
  "SELECT
     (SELECT COUNT(*) FROM products_product WHERE TRIM(COALESCE(gtin, '')) <> '') +
     (SELECT COUNT(*) FROM products_product WHERE TRIM(COALESCE(unit_barcode, '')) <> '') +
     (SELECT COUNT(*) FROM products_product WHERE TRIM(COALESCE(carton_barcode, '')) <> '') +
     (SELECT COUNT(*) FROM products_product WHERE TRIM(COALESCE(external_code, '')) <> '') +
     (SELECT COUNT(*) FROM products_productpackage WHERE TRIM(COALESCE(barcode, '')) <> '');")"
cross_product_conflicts="$(sql_count \
  "SELECT COUNT(*) FROM (
     SELECT owner_id, normalized_value
     FROM (
       SELECT id AS product_id, owner_id, UPPER(TRIM(code)) AS normalized_value FROM products_product
       UNION ALL SELECT id, owner_id, UPPER(TRIM(sku)) FROM products_product
       UNION ALL SELECT id, owner_id, UPPER(TRIM(gtin)) FROM products_product WHERE TRIM(COALESCE(gtin, '')) <> ''
       UNION ALL SELECT id, owner_id, UPPER(TRIM(unit_barcode)) FROM products_product WHERE TRIM(COALESCE(unit_barcode, '')) <> ''
       UNION ALL SELECT id, owner_id, UPPER(TRIM(carton_barcode)) FROM products_product WHERE TRIM(COALESCE(carton_barcode, '')) <> ''
       UNION ALL SELECT id, owner_id, UPPER(TRIM(external_code)) FROM products_product WHERE TRIM(COALESCE(external_code, '')) <> ''
       UNION ALL SELECT pp.product_id, p.owner_id, UPPER(TRIM(pp.barcode))
         FROM products_productpackage pp
         JOIN products_product p ON p.id = pp.product_id
         WHERE TRIM(COALESCE(pp.barcode, '')) <> ''
     ) identifiers
     GROUP BY owner_id, normalized_value
     HAVING COUNT(DISTINCT product_id) > 1
   ) conflicts;")"
if ! env \
  APP_ENV=test \
  DB_NAME="${DATABASE_NAME}" \
  DB_TEST_NAME="${DATABASE_NAME}" \
  DB_USER=root \
  DB_PASSWORD="${MYSQL_ROOT_PASSWORD}" \
  DB_HOST="${DB_HOST}" \
  DB_PORT="${DB_PORT}" \
  "${PYTHON_BIN}" "${ROOT_DIR}/manage.py" migrate \
  products 0008_product_carton_package --noinput >"${prepare_log}" 2>&1; then
  echo "迁移演练拒绝继续：无法将授权备份准备到 products 0008；受限日志未输出。" >&2
  exit 1
fi
migration_0008_prepared="$(sql_count \
  "SELECT COUNT(*) FROM django_migrations
   WHERE app='products' AND name='0008_product_carton_package';")"
if [[ "${migration_0008_prepared}" != "1" ]]; then
  echo "迁移演练拒绝继续：products 0008 准备步骤未生效。" >&2
  exit 1
fi

unbound_cartons="$(sql_count \
  "SELECT COUNT(*)
   FROM products_product p
   LEFT JOIN products_productpackage pp ON pp.id = p.carton_package_id
   WHERE TRIM(COALESCE(p.carton_barcode, '')) <> ''
     AND (p.carton_package_id IS NULL OR pp.id IS NULL OR pp.product_id <> p.id);" )"
lock_waits_before="$(sql_count "SHOW GLOBAL STATUS LIKE 'Innodb_row_lock_waits';" | awk '{print $2}')"
deadlocks_before="$(sql_count "SHOW GLOBAL STATUS LIKE 'Innodb_deadlocks';" | awk '{print $2}')"

started_at="$(date +%s)"
migration_success=false
identifier_audit_success=false
carton_audit_success=false

if env \
  APP_ENV=test \
  DB_NAME="${DATABASE_NAME}" \
  DB_TEST_NAME="${DATABASE_NAME}" \
  DB_USER=root \
  DB_PASSWORD="${MYSQL_ROOT_PASSWORD}" \
  DB_HOST="${DB_HOST}" \
  DB_PORT="${DB_PORT}" \
  "${PYTHON_BIN}" "${ROOT_DIR}/manage.py" migrate --noinput \
  >"${migration_log}" 2>&1; then
  migration_success=true
  if env APP_ENV=test DB_NAME="${DATABASE_NAME}" DB_TEST_NAME="${DATABASE_NAME}" \
    DB_USER=root DB_PASSWORD="${MYSQL_ROOT_PASSWORD}" DB_HOST="${DB_HOST}" DB_PORT="${DB_PORT}" \
    "${PYTHON_BIN}" "${ROOT_DIR}/manage.py" audit_product_identifier_history \
    >"${audit_log}" 2>&1; then
    identifier_audit_success=true
  fi
  if env APP_ENV=test DB_NAME="${DATABASE_NAME}" DB_TEST_NAME="${DATABASE_NAME}" \
    DB_USER=root DB_PASSWORD="${MYSQL_ROOT_PASSWORD}" DB_HOST="${DB_HOST}" DB_PORT="${DB_PORT}" \
    "${PYTHON_BIN}" "${ROOT_DIR}/manage.py" audit_carton_package_bindings \
    >>"${audit_log}" 2>&1; then
    carton_audit_success=true
  fi
fi

duration_seconds="$(( $(date +%s) - started_at ))"
products_after="$(sql_count 'SELECT COUNT(*) FROM products_product;')"
packages_after="$(sql_count 'SELECT COUNT(*) FROM products_productpackage;')"
barcode_history_after="$(sql_count \
  "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='${DATABASE_NAME}' AND table_name='products_productbarcode';")"
if [[ "${barcode_history_after}" == "1" ]]; then
  barcode_history_after="$(sql_count 'SELECT COUNT(*) FROM products_productbarcode;')"
  external_history_after="$(sql_count 'SELECT COUNT(*) FROM products_productexternalidentifier;')"
  registry_after="$(sql_count 'SELECT COUNT(*) FROM products_productidentifierregistry;')"
  identifier_index_count="$(sql_count \
    "SELECT COUNT(DISTINCT index_name) FROM information_schema.statistics
     WHERE table_schema='${DATABASE_NAME}'
       AND table_name IN ('products_productidentifierregistry','products_productbarcode','products_productexternalidentifier');")"
else
  barcode_history_after=0
  external_history_after=0
  registry_after=0
  identifier_index_count=0
fi
lock_waits_after="$(sql_count "SHOW GLOBAL STATUS LIKE 'Innodb_row_lock_waits';" | awk '{print $2}')"
deadlocks_after="$(sql_count "SHOW GLOBAL STATUS LIKE 'Innodb_deadlocks';" | awk '{print $2}')"
backup_sha_after="$(sha256sum "${BACKUP_FILE}" | cut -d' ' -f1)"
backup_unchanged=false
if [[ "${backup_sha_before}" == "${backup_sha_after}" ]]; then
  backup_unchanged=true
fi

cat >"${REPORT_FILE}" <<JSON
{
  "database_prefix": "wms_migration_rehearsal_",
  "mysql_host": "loopback",
  "mysql_port": 33306,
  "products_migration_latest_raw": "${migration_latest_raw}",
  "products_0008_present_raw": $([[ "${migration_0008_present_raw}" == "1" ]] && echo true || echo false),
  "products_0008_prepared": true,
  "products_0009_present_before": false,
  "products_before": ${products_before},
  "packages_before": ${packages_before},
  "legacy_identifier_values_before": ${legacy_values_before},
  "cross_product_conflicts_before": ${cross_product_conflicts},
  "unbound_cartons_before": ${unbound_cartons},
  "migration_success": ${migration_success},
  "identifier_audit_success": ${identifier_audit_success},
  "carton_audit_success": ${carton_audit_success},
  "products_after": ${products_after},
  "packages_after": ${packages_after},
  "barcode_history_after": ${barcode_history_after},
  "external_identifier_history_after": ${external_history_after},
  "registry_rows_after": ${registry_after},
  "identifier_index_count": ${identifier_index_count},
  "duration_seconds": ${duration_seconds},
  "lock_wait_delta": $((lock_waits_after - lock_waits_before)),
  "deadlock_delta": $((deadlocks_after - deadlocks_before)),
  "backup_unchanged": ${backup_unchanged},
  "business_rows_in_report": false
}
JSON

if [[ "${migration_success}" != "true" || "${identifier_audit_success}" != "true" || "${carton_audit_success}" != "true" || "${backup_unchanged}" != "true" ]]; then
  echo "迁移演练未通过；受限日志未输出，汇总见 ${REPORT_FILE}。" >&2
  exit 1
fi

echo "迁移演练通过；汇总见 ${REPORT_FILE}。一次性数据库将立即销毁。"
