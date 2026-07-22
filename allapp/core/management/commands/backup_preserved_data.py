"""Back up explicitly preserved WMS models to a MySQL SQL bundle."""

from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.utils.connection import ConnectionDoesNotExist

from allapp.accounts.audit import record_audit_event
from allapp.core.business_data_purge import (
    PURGE_MANIFEST_VERSION,
    acquire_database_maintenance_lock,
    canonical_target,
)
from allapp.core.preserved_data_transfer import (
    ensure_backup_allowed,
    execute_backup,
    prepare_backup,
)


class Command(BaseCommand):
    help = "将保留模型数据导出为可恢复到全新空库的 MySQL SQL 备份包。"

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--dry-run", action="store_true", help="只读预检（默认）。")
        mode.add_argument("--execute", action="store_true", help="正式生成备份包。")
        parser.add_argument("--database", default="default", help="数据库别名。")
        parser.add_argument("--confirm-target", default="", help="确认目标库。")
        parser.add_argument("--operator", default="", help="有效超级管理员用户名。")
        parser.add_argument("--output", default="", help="新建备份包目录。")
        parser.add_argument(
            "--maintenance-confirmed",
            action="store_true",
            help="确认服务、任务和调度已经停止。",
        )

    def handle(self, *args, **options):
        alias = options["database"]
        connection = self._connection(alias)
        expected_target = canonical_target(connection)
        if not options["execute"]:
            report = self._prepare(alias)
            self._render(report, dry_run=True, output=options["output"])
            return

        self._validate_execute_options(options, expected_target)
        operator = self._operator(alias, options["operator"])
        output = Path(options["output"])
        try:
            with acquire_database_maintenance_lock(alias, expected_target):
                report = self._prepare(alias)
                self._render(report, dry_run=False, output=str(output))
                ensure_backup_allowed(report)
                manifest = execute_backup(report, output_path=output, operator=operator)
        except Exception as exc:
            self._record_failure(alias, operator, expected_target, output, exc)
            raise CommandError(f"保留数据备份失败：{exc}") from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"备份完成：{output.expanduser().resolve()}；"
                f"备份引用 {manifest['backup_id']}；"
                f"共 {len(manifest['selected_models'])} 张表。"
            )
        )

    def _connection(self, alias):
        try:
            connection = connections[alias]
        except ConnectionDoesNotExist as exc:
            raise CommandError(f"数据库别名不存在：{alias}") from exc
        if connection.vendor != "mysql":
            raise CommandError("backup_preserved_data 仅支持 MySQL。")
        return connection

    def _prepare(self, alias):
        try:
            return prepare_backup(alias)
        except Exception as exc:
            if isinstance(exc, CommandError):
                raise
            raise CommandError(str(exc)) from exc

    def _operator(self, alias, username):
        operator = (
            get_user_model()
            ._default_manager.using(alias)
            .filter(
                username=username.strip(),
                is_active=True,
                is_superuser=True,
            )
            .first()
        )
        if operator is None:
            raise CommandError("--operator 必须是源数据库中的有效超级管理员。")
        return operator

    def _validate_execute_options(self, options, expected_target):
        required = {
            "--confirm-target": options["confirm_target"],
            "--operator": options["operator"],
            "--output": options["output"],
        }
        missing = [name for name, value in required.items() if not str(value).strip()]
        if not options["maintenance_confirmed"]:
            missing.append("--maintenance-confirmed")
        if missing:
            raise CommandError("正式执行缺少参数：" + "、".join(missing))
        if options["confirm_target"].strip() != expected_target:
            raise CommandError(
                "目标库确认不匹配。请精确输入：" f"--confirm-target {expected_target}"
            )

    def _render(self, report, *, dry_run, output):
        mode = "DRY-RUN（只读）" if dry_run else "EXECUTE（正式执行）"
        self.stdout.write(f"模式: {mode}")
        self.stdout.write(f"数据库别名: {report.database_alias}")
        self.stdout.write(f"确认目标: {report.target}")
        self.stdout.write(f"清单版本: {PURGE_MANIFEST_VERSION}")
        self.stdout.write(f"MySQL: {report.mysql_version}")
        self.stdout.write(f"输出目录: {output or '未指定（正式执行时必填）'}")
        self.stdout.write(
            f"备份表: {len(report.scope.selected_tables)}；"
            f"合计行数: {sum(report.row_counts.values())}"
        )
        self.stdout.write("\n[排除的历史日志模型]")
        for label in sorted(report.scope.excluded_model_labels):
            self.stdout.write(f"  EXCLUDE {label}")
        self.stdout.write("\n[备份表]")
        for table in sorted(report.scope.selected_tables):
            self.stdout.write(f"  BACKUP {table}: {report.row_counts.get(table, 0)} 行")
        if report.blocking_messages:
            self.stdout.write(self.style.ERROR("\n[阻塞问题]"))
            for message in report.blocking_messages:
                self.stdout.write(f"  BLOCK  {message}")
        else:
            suffix = "未写入文件或数据库。" if dry_run else "允许生成备份包。"
            self.stdout.write(self.style.SUCCESS(f"预检通过；{suffix}"))

    def _record_failure(self, alias, operator, target, output, error):
        try:
            record_audit_event(
                action="PRESERVED_DATA_BACKUP",
                module="core.operations",
                user=operator,
                succeeded=False,
                metadata={
                    "source": "backup_preserved_data",
                    "target": target,
                    "output_path": str(output),
                    "manifest_version": PURGE_MANIFEST_VERSION,
                    "error_type": type(error).__name__,
                    "error": str(error)[:1000],
                },
                using=alias,
            )
        except Exception as audit_exc:  # pragma: no cover
            self.stderr.write(self.style.WARNING(f"失败审计写入失败：{audit_exc}"))
