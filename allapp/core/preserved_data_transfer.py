"""Logical MySQL backup and fresh-database restore for preserved WMS data."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import django
from django.contrib.auth import get_user_model
from django.db import connections

from allapp.accounts.audit import record_audit_event
from allapp.core.business_data_purge import (
    PURGE_MANIFEST_VERSION,
    PurgeConfigurationError,
    ResolvedManifest,
    _actual_tables,
    canonical_target,
    resolve_manifest,
)

TRANSFER_FORMAT_VERSION = 1
SQL_FILENAME = "preserved-data.sql.gz"
MANIFEST_FILENAME = "manifest.json"

EXCLUDED_HISTORY_MODEL_LABELS = frozenset(
    {
        "accounts.auditevent",
        "accounts.systemlog",
        "admin.logentry",
        "strategies.strategylog",
    }
)

# These rows are created by migrate and may be replaced in an otherwise fresh DB.
RESTORE_REPLACEABLE_MODEL_LABELS = frozenset(
    {
        "auth.permission",
        "contenttypes.contenttype",
        "core.printconfig",
        "core.systemsetting",
    }
)

_INSERT_RE = re.compile(r"^INSERT INTO `([^`]+)` ")
_MYSQL_8_RE = re.compile(r"^8(?:\.|$)")


class PreservedDataError(RuntimeError):
    """Base error for preserved-data transfer operations."""


class PreservedDataBlockedError(PreservedDataError):
    """A safety preflight rejected a backup or restore."""


class PreservedDataToolError(PreservedDataError):
    """A MySQL client process failed."""


@dataclass(frozen=True)
class TransferScope:
    manifest: ResolvedManifest
    selected_model_labels: frozenset[str]
    selected_tables: frozenset[str]
    excluded_model_labels: frozenset[str]
    table_to_label: dict[str, str]


@dataclass(frozen=True)
class BackupPreflightReport:
    database_alias: str
    target: str
    scope: TransferScope
    actual_tables: frozenset[str]
    row_counts: dict[str, int]
    migrations: tuple[tuple[str, str], ...]
    mysql_version: str
    eligible_restore_operators: tuple[str, ...]
    unknown_tables: frozenset[str]
    missing_tables: frozenset[str]
    non_innodb_tables: tuple[tuple[str, str | None], ...]
    missing_tools: tuple[str, ...]

    @property
    def blocking_messages(self) -> tuple[str, ...]:
        messages = [f"未分类数据库表: {table}" for table in sorted(self.unknown_tables)]
        messages.extend(
            f"缺失数据库表: {table}" for table in sorted(self.missing_tables)
        )
        messages.extend(
            f"备份表不是 InnoDB: {table} ({engine or 'UNKNOWN'})"
            for table, engine in self.non_innodb_tables
        )
        messages.extend(f"缺少 MySQL 客户端工具: {tool}" for tool in self.missing_tools)
        if not _MYSQL_8_RE.match(self.mysql_version):
            messages.append(f"仅支持 MySQL 8，当前版本: {self.mysql_version}")
        if not self.eligible_restore_operators:
            messages.append("源库没有可用于恢复审计的有效超级管理员。")
        return tuple(messages)


@dataclass(frozen=True)
class RestorePreflightReport:
    database_alias: str
    target: str
    scope: TransferScope
    bundle_path: Path
    manifest_data: dict[str, Any]
    expected_row_counts: dict[str, int]
    current_row_counts: dict[str, int]
    unknown_tables: frozenset[str]
    missing_tables: frozenset[str]
    non_innodb_tables: tuple[tuple[str, str | None], ...]
    nonempty_forbidden_tables: tuple[tuple[str, int], ...]
    migration_mismatch: bool
    sql_hash_matches: bool
    sql_is_safe: bool
    sql_safety_error: str
    mysql_version: str
    missing_tools: tuple[str, ...]

    @property
    def sql_path(self) -> Path:
        return self.bundle_path / SQL_FILENAME

    @property
    def blocking_messages(self) -> tuple[str, ...]:
        messages = [f"未分类数据库表: {table}" for table in sorted(self.unknown_tables)]
        messages.extend(
            f"缺失数据库表: {table}" for table in sorted(self.missing_tables)
        )
        messages.extend(
            f"恢复表不是 InnoDB: {table} ({engine or 'UNKNOWN'})"
            for table, engine in self.non_innodb_tables
        )
        messages.extend(
            f"目标库不是空白库: {table} 有 {count} 行"
            for table, count in self.nonempty_forbidden_tables
        )
        messages.extend(f"缺少 MySQL 客户端工具: {tool}" for tool in self.missing_tools)
        if self.migration_mismatch:
            messages.append("目标库迁移集合与备份不完全一致。")
        if not self.sql_hash_matches:
            messages.append("SQL 文件 SHA-256 与清单不一致。")
        if not self.sql_is_safe:
            messages.append(f"SQL 文件安全检查失败: {self.sql_safety_error}")
        if not _MYSQL_8_RE.match(self.mysql_version):
            messages.append(f"仅支持 MySQL 8，当前版本: {self.mysql_version}")
        return tuple(messages)


def resolve_transfer_scope() -> TransferScope:
    manifest = resolve_manifest()
    if not EXCLUDED_HISTORY_MODEL_LABELS <= set(manifest.label_to_table):
        missing = EXCLUDED_HISTORY_MODEL_LABELS - set(manifest.label_to_table)
        raise PurgeConfigurationError(
            "备份排除清单包含不存在模型: " + "、".join(sorted(missing))
        )
    selected_labels = frozenset(
        label
        for label in manifest.label_to_table
        if label not in EXCLUDED_HISTORY_MODEL_LABELS
        and manifest.label_to_table[label] in manifest.preserved_tables
    )
    selected_tables = frozenset(
        manifest.label_to_table[label] for label in selected_labels
    )
    table_to_label = {
        manifest.label_to_table[label]: label for label in selected_labels
    }
    if len(table_to_label) != len(selected_labels):
        raise PurgeConfigurationError("多个保留模型映射到同一备份表。")
    return TransferScope(
        manifest=manifest,
        selected_model_labels=selected_labels,
        selected_tables=selected_tables,
        excluded_model_labels=EXCLUDED_HISTORY_MODEL_LABELS,
        table_to_label=table_to_label,
    )


def _mysql_version(connection) -> str:
    with connection.cursor() as cursor:
        cursor.execute("SELECT VERSION()")
        return str(cursor.fetchone()[0])


def _migration_set(connection) -> tuple[tuple[str, str], ...]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT app, name FROM django_migrations ORDER BY app, name")
        return tuple((str(app), str(name)) for app, name in cursor.fetchall())


def _table_engines(connection, tables: frozenset[str]) -> dict[str, str | None]:
    if not tables:
        return {}
    placeholders = ", ".join(["%s"] * len(tables))
    query = (
        "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (" + placeholders + ")"
    )
    with connection.cursor() as cursor:
        cursor.execute(query, sorted(tables))
        return {
            str(table): (str(engine) if engine is not None else None)
            for table, engine in cursor.fetchall()
        }


def _row_counts(connection, tables: frozenset[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    with connection.cursor() as cursor:
        for table in sorted(tables):
            cursor.execute(f"SELECT COUNT(*) FROM {connection.ops.quote_name(table)}")
            counts[table] = int(cursor.fetchone()[0])
    return counts


def _missing_tools(*names: str) -> tuple[str, ...]:
    return tuple(name for name in names if shutil.which(name) is None)


def _schema_signature(
    *, scope: TransferScope, migrations: tuple[tuple[str, str], ...]
) -> str:
    payload = {
        "manifest_version": PURGE_MANIFEST_VERSION,
        "models": [
            [label, scope.manifest.label_to_table[label]]
            for label in sorted(scope.selected_model_labels)
        ],
        "migrations": [list(item) for item in migrations],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def prepare_backup(database_alias: str = "default") -> BackupPreflightReport:
    connection = connections[database_alias]
    if connection.vendor != "mysql":
        raise PreservedDataBlockedError("保留数据备份仅支持 MySQL。")
    scope = resolve_transfer_scope()
    actual = _actual_tables(connection)
    missing = scope.manifest.classified_tables - actual
    unknown = actual - scope.manifest.classified_tables
    present_selected = scope.selected_tables & actual
    engines = _table_engines(connection, present_selected)
    non_innodb = tuple(
        (table, engines.get(table))
        for table in sorted(present_selected)
        if (engines.get(table) or "").upper() != "INNODB"
    )
    operators = tuple(
        get_user_model()
        ._default_manager.using(database_alias)
        .filter(is_active=True, is_superuser=True)
        .order_by("username")
        .values_list("username", flat=True)
    )
    return BackupPreflightReport(
        database_alias=database_alias,
        target=canonical_target(connection),
        scope=scope,
        actual_tables=actual,
        row_counts=_row_counts(connection, present_selected),
        migrations=_migration_set(connection),
        mysql_version=_mysql_version(connection),
        eligible_restore_operators=operators,
        unknown_tables=frozenset(unknown),
        missing_tables=frozenset(missing),
        non_innodb_tables=non_innodb,
        missing_tools=_missing_tools("mysqldump", "mysql"),
    )


def ensure_backup_allowed(report: BackupPreflightReport) -> None:
    if report.blocking_messages:
        raise PreservedDataBlockedError("；".join(report.blocking_messages))


def _quote_option_file_value(value: object) -> str:
    escaped = (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )
    return f'"{escaped}"'


@contextmanager
def mysql_defaults_file(connection, directory: Path):
    """Create a mode-0600 MySQL option file without exposing credentials."""

    fd, raw_path = tempfile.mkstemp(prefix=".mysql-client-", dir=directory)
    path = Path(raw_path)
    try:
        os.fchmod(fd, 0o600)
        config = connection.settings_dict
        options = config.get("OPTIONS") or {}
        lines = ["[client]"]
        values = {
            "user": config.get("USER") or "",
            "password": config.get("PASSWORD") or "",
            "host": config.get("HOST") or "localhost",
            "port": config.get("PORT") or "3306",
            "default-character-set": options.get("charset") or "utf8mb4",
        }
        socket = options.get("unix_socket")
        if socket:
            values["socket"] = socket
            values["protocol"] = "SOCKET"
        ssl = options.get("ssl") or {}
        for source, target in (
            ("ca", "ssl-ca"),
            ("cert", "ssl-cert"),
            ("key", "ssl-key"),
        ):
            if ssl.get(source):
                values[target] = ssl[source]
        lines.extend(
            f"{key}={_quote_option_file_value(value)}" for key, value in values.items()
        )
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        yield path
    finally:
        path.unlink(missing_ok=True)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_process_error(stderr: bytes) -> str:
    return stderr.decode("utf-8", errors="replace").strip()[-2000:]


def _dump_command(defaults_file: Path, connection, tables: list[str]) -> list[str]:
    executable = shutil.which("mysqldump") or "mysqldump"
    return [
        executable,
        f"--defaults-extra-file={defaults_file}",
        "--single-transaction",
        "--quick",
        "--compact",
        "--no-create-info",
        "--skip-triggers",
        "--skip-add-locks",
        "--skip-disable-keys",
        "--hex-blob",
        "--complete-insert",
        "--skip-extended-insert",
        "--set-gtid-purged=OFF",
        "--no-tablespaces",
        "--default-character-set=utf8mb4",
        str(connection.settings_dict["NAME"]),
        *tables,
    ]


def validate_dump_sql(
    sql_path: Path,
    *,
    selected_tables: frozenset[str],
    expected_row_counts: dict[str, int],
) -> tuple[bool, str]:
    """Allow only compact mysqldump comments and INSERT statements."""

    seen_tables: set[str] = set()
    try:
        with gzip.open(sql_path, "rt", encoding="utf-8", newline="") as handle:
            for line_number, line in enumerate(handle, 1):
                stripped = line.strip()
                if not stripped or stripped.startswith("--"):
                    continue
                match = _INSERT_RE.match(stripped)
                if not match:
                    return False, f"第 {line_number} 行不是允许的 INSERT 或注释。"
                table = match.group(1)
                if table not in selected_tables:
                    return False, f"第 {line_number} 行写入未授权表 {table}。"
                seen_tables.add(table)
    except (OSError, UnicodeError) as exc:
        return False, f"无法读取压缩 SQL: {exc}"

    missing_data_tables = {
        table
        for table, count in expected_row_counts.items()
        if count > 0 and table not in seen_tables
    }
    if missing_data_tables:
        return False, "有数据但 SQL 中没有 INSERT: " + "、".join(
            sorted(missing_data_tables)
        )
    return True, ""


def execute_backup(
    report: BackupPreflightReport,
    *,
    output_path: Path,
    operator,
) -> dict[str, Any]:
    ensure_backup_allowed(report)
    output_path = output_path.expanduser().resolve()
    if output_path.exists():
        raise PreservedDataBlockedError(f"输出路径已存在，拒绝覆盖: {output_path}")
    if not output_path.parent.is_dir():
        raise PreservedDataBlockedError(f"输出父目录不存在: {output_path.parent}")

    connection = connections[report.database_alias]
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output_path.name}.", dir=output_path.parent)
    )
    os.chmod(temporary, 0o700)
    sql_path = temporary / SQL_FILENAME
    try:
        with mysql_defaults_file(connection, temporary) as defaults_file:
            stderr_path = temporary / ".mysqldump.stderr"
            with stderr_path.open("wb") as stderr_handle:
                process = subprocess.Popen(
                    _dump_command(
                        defaults_file,
                        connection,
                        sorted(report.scope.selected_tables),
                    ),
                    stdout=subprocess.PIPE,
                    stderr=stderr_handle,
                )
                if process.stdout is None:  # pragma: no cover - defensive
                    process.kill()
                    process.wait()
                    raise PreservedDataToolError("无法读取 mysqldump 标准输出。")
                with process.stdout, gzip.open(
                    sql_path, "wb", compresslevel=6
                ) as output:
                    shutil.copyfileobj(process.stdout, output, length=1024 * 1024)
                return_code = process.wait()
        os.chmod(sql_path, 0o600)
        if return_code:
            raise PreservedDataToolError(
                "mysqldump 执行失败: " + _safe_process_error(stderr_path.read_bytes())
            )
        stderr_path.unlink(missing_ok=True)
        safe, safety_error = validate_dump_sql(
            sql_path,
            selected_tables=report.scope.selected_tables,
            expected_row_counts=report.row_counts,
        )
        if not safe:
            raise PreservedDataToolError(f"生成的 SQL 未通过安全检查: {safety_error}")

        sql_hash = _sha256_file(sql_path)
        created_at = datetime.now(timezone.utc).isoformat()
        backup_id = f"preserved-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:12]}"
        manifest_data: dict[str, Any] = {
            "format_version": TRANSFER_FORMAT_VERSION,
            "backup_id": backup_id,
            "created_at": created_at,
            "purge_manifest_version": PURGE_MANIFEST_VERSION,
            "django_version": django.get_version(),
            "mysql_version": report.mysql_version,
            "source": {
                "database_alias": report.database_alias,
                "target": report.target,
                "operator": operator.username,
            },
            "eligible_restore_operators": list(report.eligible_restore_operators),
            "excluded_model_labels": sorted(report.scope.excluded_model_labels),
            "selected_models": [
                {
                    "label": report.scope.table_to_label[table],
                    "table": table,
                    "row_count": report.row_counts[table],
                }
                for table in sorted(report.scope.selected_tables)
            ],
            "migrations": [
                {"app": app, "name": name} for app, name in report.migrations
            ],
            "schema_signature": _schema_signature(
                scope=report.scope, migrations=report.migrations
            ),
            "sql": {
                "filename": SQL_FILENAME,
                "sha256": sql_hash,
                "size_bytes": sql_path.stat().st_size,
            },
            "media_files_included": False,
        }
        manifest_path = temporary / MANIFEST_FILENAME
        with manifest_path.open("w", encoding="utf-8") as handle:
            json.dump(
                manifest_data, handle, ensure_ascii=False, indent=2, sort_keys=True
            )
            handle.write("\n")
        os.chmod(manifest_path, 0o600)
        os.replace(temporary, output_path)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise

    record_audit_event(
        action="PRESERVED_DATA_BACKUP",
        module="core.operations",
        user=operator,
        succeeded=True,
        after={"row_counts": report.row_counts},
        metadata={
            "source": "backup_preserved_data",
            "target": report.target,
            "backup_id": manifest_data["backup_id"],
            "output_path": str(output_path),
            "sql_sha256": manifest_data["sql"]["sha256"],
            "manifest_version": PURGE_MANIFEST_VERSION,
        },
        using=report.database_alias,
    )
    return manifest_data


def _load_manifest(bundle_path: Path) -> dict[str, Any]:
    manifest_path = bundle_path / MANIFEST_FILENAME
    sql_path = bundle_path / SQL_FILENAME
    if not bundle_path.is_dir():
        raise PreservedDataBlockedError(f"备份包目录不存在: {bundle_path}")
    if not manifest_path.is_file():
        raise PreservedDataBlockedError(f"备份清单不存在: {manifest_path}")
    if not sql_path.is_file():
        raise PreservedDataBlockedError(f"备份 SQL 不存在: {sql_path}")
    try:
        with manifest_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PreservedDataBlockedError(f"无法读取备份清单: {exc}") from exc
    if not isinstance(data, dict):
        raise PreservedDataBlockedError("备份清单根节点必须是对象。")
    return data


def _manifest_migrations(data: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    try:
        return tuple(
            sorted((str(item["app"]), str(item["name"])) for item in data["migrations"])
        )
    except (KeyError, TypeError) as exc:
        raise PreservedDataBlockedError("备份清单 migrations 格式无效。") from exc


def _validate_manifest_scope(
    data: dict[str, Any], scope: TransferScope
) -> dict[str, int]:
    if data.get("format_version") != TRANSFER_FORMAT_VERSION:
        raise PreservedDataBlockedError("不支持的备份格式版本。")
    if data.get("purge_manifest_version") != PURGE_MANIFEST_VERSION:
        raise PreservedDataBlockedError("备份清单版本与当前代码不一致。")
    if data.get("excluded_model_labels") != sorted(scope.excluded_model_labels):
        raise PreservedDataBlockedError("备份排除模型清单与当前代码不一致。")
    if (data.get("sql") or {}).get("filename") != SQL_FILENAME:
        raise PreservedDataBlockedError("备份 SQL 文件名无效。")
    if not isinstance(data.get("backup_id"), str) or not data["backup_id"].strip():
        raise PreservedDataBlockedError("备份 ID 无效。")
    source = data.get("source")
    if not isinstance(source, dict) or not isinstance(source.get("target"), str):
        raise PreservedDataBlockedError("备份来源信息无效。")
    eligible = data.get("eligible_restore_operators")
    if not isinstance(eligible, list) or not all(
        isinstance(username, str) and username for username in eligible
    ):
        raise PreservedDataBlockedError("备份超级管理员清单无效。")
    sql_hash = (data.get("sql") or {}).get("sha256")
    if not isinstance(sql_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", sql_hash):
        raise PreservedDataBlockedError("备份 SQL SHA-256 格式无效。")

    try:
        entries = data["selected_models"]
        mapping = {
            str(item["label"]): (str(item["table"]), int(item["row_count"]))
            for item in entries
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise PreservedDataBlockedError("备份模型清单格式无效。") from exc
    if not isinstance(entries, list) or len(entries) != len(mapping):
        raise PreservedDataBlockedError("备份模型清单包含重复项。")
    expected_mapping = {
        label: scope.manifest.label_to_table[label]
        for label in scope.selected_model_labels
    }
    if set(mapping) != set(expected_mapping) or any(
        mapping[label][0] != table for label, table in expected_mapping.items()
    ):
        raise PreservedDataBlockedError("备份模型及数据库表清单与当前代码不一致。")
    if any(count < 0 for _, count in mapping.values()):
        raise PreservedDataBlockedError("备份行数不能为负数。")
    return {table: mapping[label][1] for label, table in expected_mapping.items()}


def prepare_restore(
    database_alias: str = "default", *, bundle_path: Path
) -> RestorePreflightReport:
    connection = connections[database_alias]
    if connection.vendor != "mysql":
        raise PreservedDataBlockedError("保留数据恢复仅支持 MySQL。")
    scope = resolve_transfer_scope()
    bundle_path = bundle_path.expanduser().resolve()
    data = _load_manifest(bundle_path)
    expected_counts = _validate_manifest_scope(data, scope)
    backup_migrations = _manifest_migrations(data)
    if data.get("schema_signature") != _schema_signature(
        scope=scope, migrations=backup_migrations
    ):
        raise PreservedDataBlockedError("备份 schema_signature 无效。")

    actual = _actual_tables(connection)
    unknown = actual - scope.manifest.classified_tables
    missing = scope.manifest.classified_tables - actual
    present_classified = actual & scope.manifest.classified_tables
    counted_tables = frozenset(present_classified - {"django_migrations"})
    current_counts = _row_counts(connection, counted_tables)
    replaceable_tables = {
        scope.manifest.label_to_table[label]
        for label in RESTORE_REPLACEABLE_MODEL_LABELS
    }
    nonempty_forbidden = tuple(
        (table, count)
        for table, count in sorted(current_counts.items())
        if count and table not in replaceable_tables
    )
    engines = _table_engines(connection, scope.selected_tables & actual)
    non_innodb = tuple(
        (table, engines.get(table))
        for table in sorted(scope.selected_tables & actual)
        if (engines.get(table) or "").upper() != "INNODB"
    )
    sql_path = bundle_path / SQL_FILENAME
    expected_hash = str((data.get("sql") or {}).get("sha256") or "")
    actual_hash = _sha256_file(sql_path)
    sql_hash_matches = bool(expected_hash) and expected_hash == actual_hash
    sql_is_safe, sql_safety_error = validate_dump_sql(
        sql_path,
        selected_tables=scope.selected_tables,
        expected_row_counts=expected_counts,
    )
    return RestorePreflightReport(
        database_alias=database_alias,
        target=canonical_target(connection),
        scope=scope,
        bundle_path=bundle_path,
        manifest_data=data,
        expected_row_counts=expected_counts,
        current_row_counts=current_counts,
        unknown_tables=frozenset(unknown),
        missing_tables=frozenset(missing),
        non_innodb_tables=non_innodb,
        nonempty_forbidden_tables=nonempty_forbidden,
        migration_mismatch=_migration_set(connection) != backup_migrations,
        sql_hash_matches=sql_hash_matches,
        sql_is_safe=sql_is_safe,
        sql_safety_error=sql_safety_error,
        mysql_version=_mysql_version(connection),
        missing_tools=_missing_tools("mysql"),
    )


def ensure_restore_allowed(report: RestorePreflightReport) -> None:
    if report.blocking_messages:
        raise PreservedDataBlockedError("；".join(report.blocking_messages))


def ensure_restore_operator(report: RestorePreflightReport, username: str) -> None:
    eligible = report.manifest_data.get("eligible_restore_operators") or []
    if username not in eligible:
        raise PreservedDataBlockedError("--operator 必须是备份清单中的有效超级管理员。")


def _restore_header(connection, tables: frozenset[str]) -> bytes:
    statements = [
        "SET SESSION autocommit = 0;",
        "START TRANSACTION;",
        "SET @WMS_OLD_SQL_MODE = @@SESSION.SQL_MODE;",
        "SET SESSION SQL_MODE = CONCAT_WS(',', @@SESSION.SQL_MODE, "
        "'NO_AUTO_VALUE_ON_ZERO');",
        "SET SESSION FOREIGN_KEY_CHECKS = 0;",
    ]
    statements.extend(
        f"DELETE FROM {connection.ops.quote_name(table)};" for table in sorted(tables)
    )
    return ("\n".join(statements) + "\n").encode()


def _restore_footer(connection, row_counts: dict[str, int]) -> bytes:
    statements = [
        "SELECT JSON_EXTRACT("
        f"IF(COUNT(*) = {count}, 'null', 'WMS_ROW_COUNT_MISMATCH'), '$') "
        f"FROM {connection.ops.quote_name(table)};"
        for table, count in sorted(row_counts.items())
    ]
    statements.extend(
        [
            "SET SESSION FOREIGN_KEY_CHECKS = 1;",
            "SET SESSION SQL_MODE = @WMS_OLD_SQL_MODE;",
            "COMMIT;",
        ]
    )
    return ("\n" + "\n".join(statements) + "\n").encode()


def _mysql_restore_command(defaults_file: Path, connection) -> list[str]:
    executable = shutil.which("mysql") or "mysql"
    return [
        executable,
        f"--defaults-extra-file={defaults_file}",
        "--binary-mode",
        "--batch",
        "--default-character-set=utf8mb4",
        f"--database={connection.settings_dict['NAME']}",
    ]


def execute_restore(
    report: RestorePreflightReport,
    *,
    operator_username: str,
) -> dict[str, int]:
    ensure_restore_allowed(report)
    ensure_restore_operator(report, operator_username)
    connection = connections[report.database_alias]

    with tempfile.TemporaryDirectory(prefix="wms-preserved-restore-") as raw_temp:
        temporary = Path(raw_temp)
        with mysql_defaults_file(connection, temporary) as defaults_file:
            stderr_path = temporary / "mysql.stderr"
            with stderr_path.open("wb") as stderr_handle:
                process = subprocess.Popen(
                    _mysql_restore_command(defaults_file, connection),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=stderr_handle,
                )
                try:
                    if process.stdin is None:  # pragma: no cover - defensive
                        raise PreservedDataToolError("无法打开 mysql 标准输入。")
                    process.stdin.write(
                        _restore_header(connection, report.scope.selected_tables)
                    )
                    with gzip.open(report.sql_path, "rb") as sql_input:
                        shutil.copyfileobj(sql_input, process.stdin, length=1024 * 1024)
                    process.stdin.write(
                        _restore_footer(connection, report.expected_row_counts)
                    )
                    process.stdin.close()
                    return_code = process.wait()
                except Exception:
                    if process.stdin and not process.stdin.closed:
                        process.stdin.close()
                    process.kill()
                    process.wait()
                    raise
            if return_code:
                stderr = stderr_path.read_bytes()
                raise PreservedDataToolError(
                    "mysql 恢复失败，事务已回滚: " + _safe_process_error(stderr)
                )

    observed_counts = _row_counts(connection, report.scope.selected_tables)
    if observed_counts != report.expected_row_counts:
        raise PreservedDataToolError("恢复提交后的行数校验不一致。")
    operator = (
        get_user_model()
        ._default_manager.using(report.database_alias)
        .filter(
            username=operator_username,
            is_active=True,
            is_superuser=True,
        )
        .first()
    )
    if operator is None:
        raise PreservedDataToolError("恢复完成，但无法解析审计操作者。")
    record_audit_event(
        action="PRESERVED_DATA_RESTORE",
        module="core.operations",
        user=operator,
        succeeded=True,
        after={"row_counts": observed_counts},
        metadata={
            "source": "restore_preserved_data",
            "target": report.target,
            "backup_id": report.manifest_data["backup_id"],
            "source_target": report.manifest_data["source"]["target"],
            "sql_sha256": report.manifest_data["sql"]["sha256"],
            "manifest_version": PURGE_MANIFEST_VERSION,
        },
        using=report.database_alias,
    )
    return observed_counts
