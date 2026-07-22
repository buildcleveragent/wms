"""Restore a preserved-data MySQL bundle into a freshly migrated database."""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.utils.connection import ConnectionDoesNotExist

from allapp.core.business_data_purge import (
    PURGE_MANIFEST_VERSION,
    acquire_database_maintenance_lock,
    canonical_target,
)
from allapp.core.preserved_data_transfer import (
    ensure_restore_allowed,
    ensure_restore_operator,
    execute_restore,
    prepare_restore,
)


class Command(BaseCommand):
    help = "将保留模型 MySQL SQL 备份包恢复到已迁移的全新空库。"

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--dry-run", action="store_true", help="只读预检（默认）。")
        mode.add_argument("--execute", action="store_true", help="正式恢复备份包。")
        parser.add_argument("--database", default="default", help="目标数据库别名。")
        parser.add_argument("--confirm-target", default="", help="确认目标库。")
        parser.add_argument("--operator", default="", help="备份内的超级管理员用户名。")
        parser.add_argument("--input", default="", help="备份包目录。")
        parser.add_argument(
            "--maintenance-confirmed",
            action="store_true",
            help="确认服务、任务和调度已经停止。",
        )
        parser.add_argument(
            "--fresh-database-confirmed",
            action="store_true",
            help="确认目标是仅执行过 migrate 的全新空库。",
        )

    def handle(self, *args, **options):
        alias = options["database"]
        connection = self._connection(alias)
        expected_target = canonical_target(connection)
        input_value = (options["input"] or "").strip()

        if not options["execute"]:
            if not input_value:
                self.stdout.write("模式: DRY-RUN（只读）")
                self.stdout.write(f"数据库别名: {alias}")
                self.stdout.write(f"确认目标: {expected_target}")
                self.stdout.write(
                    self.style.WARNING(
                        "未提供 --input；未检查备份包。提供备份包目录后可执行完整预检。"
                    )
                )
                return
            bundle = Path(input_value)
            report = self._prepare(alias, bundle)
            self._render(report, dry_run=True)
            return

        self._validate_execute_options(options, expected_target)
        bundle = Path(input_value)
        try:
            with acquire_database_maintenance_lock(alias, expected_target):
                report = self._prepare(alias, bundle)
                self._render(report, dry_run=False)
                ensure_restore_allowed(report)
                ensure_restore_operator(report, options["operator"].strip())
                counts = execute_restore(
                    report,
                    operator_username=options["operator"].strip(),
                )
        except Exception as exc:
            raise CommandError(f"保留数据恢复失败：{exc}") from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"恢复完成：备份 {report.manifest_data['backup_id']}；"
                f"{len(counts)} 张表，共 {sum(counts.values())} 行。"
            )
        )

    def _connection(self, alias):
        try:
            connection = connections[alias]
        except ConnectionDoesNotExist as exc:
            raise CommandError(f"数据库别名不存在：{alias}") from exc
        if connection.vendor != "mysql":
            raise CommandError("restore_preserved_data 仅支持 MySQL。")
        return connection

    def _prepare(self, alias, bundle):
        try:
            return prepare_restore(alias, bundle_path=bundle)
        except Exception as exc:
            raise CommandError(str(exc)) from exc

    def _validate_execute_options(self, options, expected_target):
        required = {
            "--confirm-target": options["confirm_target"],
            "--operator": options["operator"],
            "--input": options["input"],
        }
        missing = [name for name, value in required.items() if not str(value).strip()]
        if not options["maintenance_confirmed"]:
            missing.append("--maintenance-confirmed")
        if not options["fresh_database_confirmed"]:
            missing.append("--fresh-database-confirmed")
        if missing:
            raise CommandError("正式执行缺少参数：" + "、".join(missing))
        if options["confirm_target"].strip() != expected_target:
            raise CommandError(
                "目标库确认不匹配。请精确输入：" f"--confirm-target {expected_target}"
            )

    def _render(self, report, *, dry_run):
        mode = "DRY-RUN（只读）" if dry_run else "EXECUTE（正式执行）"
        self.stdout.write(f"模式: {mode}")
        self.stdout.write(f"数据库别名: {report.database_alias}")
        self.stdout.write(f"确认目标: {report.target}")
        self.stdout.write(f"备份 ID: {report.manifest_data.get('backup_id', '')}")
        self.stdout.write(
            f"备份来源: {report.manifest_data.get('source', {}).get('target', '')}"
        )
        self.stdout.write(f"清单版本: {PURGE_MANIFEST_VERSION}")
        self.stdout.write(f"MySQL: {report.mysql_version}")
        self.stdout.write(
            f"恢复表: {len(report.expected_row_counts)}；"
            f"合计行数: {sum(report.expected_row_counts.values())}"
        )
        self.stdout.write("\n[将替换的 migrate 默认数据表]")
        replaceable = {
            report.scope.manifest.label_to_table[label]
            for label in (
                "auth.permission",
                "contenttypes.contenttype",
                "core.printconfig",
                "core.systemsetting",
            )
        }
        for table in sorted(replaceable):
            self.stdout.write(
                f"  REPLACE {table}: 当前 {report.current_row_counts.get(table, 0)} 行"
            )
        if report.blocking_messages:
            self.stdout.write(self.style.ERROR("\n[阻塞问题]"))
            for message in report.blocking_messages:
                self.stdout.write(f"  BLOCK  {message}")
        else:
            suffix = "未写入数据库。" if dry_run else "允许进入恢复事务。"
            self.stdout.write(self.style.SUCCESS(f"预检通过；{suffix}"))
