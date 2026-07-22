"""Purge volatile WMS business data while preserving configuration."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.utils.connection import ConnectionDoesNotExist

from allapp.accounts.audit import record_audit_event
from allapp.core.business_data_purge import (
    PURGE_MANIFEST_VERSION,
    PurgeBlockedError,
    PurgeConfigurationError,
    PurgeLockError,
    acquire_purge_lock,
    canonical_target,
    ensure_preflight_allows_execution,
    execute_purge,
    prepare_purge,
)


class Command(BaseCommand):
    help = "清空易变业务数据，保留权限、基础档案、配置、表结构和迁移记录。"

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument(
            "--dry-run",
            action="store_true",
            help="只显示预检结果，不修改数据库（默认模式）。",
        )
        mode.add_argument(
            "--execute",
            action="store_true",
            help="正式执行清理；必须同时提供全部生产确认参数。",
        )
        parser.add_argument(
            "--database",
            default="default",
            help="Django 数据库别名，默认 default。",
        )
        parser.add_argument(
            "--confirm-target",
            default="",
            help="必须精确匹配 <host>:<port>/<database>。",
        )
        parser.add_argument(
            "--operator",
            default="",
            help="执行操作的有效超级管理员用户名。",
        )
        parser.add_argument(
            "--backup-reference",
            default="",
            help="已经核验的备份编号、工单号或路径。",
        )
        parser.add_argument(
            "--maintenance-confirmed",
            action="store_true",
            help="确认 Web、后台任务和计费调度已经停止。",
        )

    def handle(self, *args, **options):
        alias = options["database"]
        connection = self._connection(alias)
        if connection.vendor != "mysql":
            raise CommandError("purge_business_data 仅支持 MySQL。")

        if not options["execute"]:
            report = self._prepare(alias)
            self._render_report(report, dry_run=True)
            return

        expected_target = canonical_target(connection)
        backup_reference = (options["backup_reference"] or "").strip()
        self._validate_execution_options(
            options=options,
            expected_target=expected_target,
            backup_reference=backup_reference,
        )
        operator = self._operator(alias, options["operator"])

        try:
            with acquire_purge_lock(alias, expected_target):
                report = self._prepare(alias)
                self._render_report(report, dry_run=False)
                ensure_preflight_allows_execution(report)
                deleted_counts = execute_purge(
                    report,
                    operator=operator,
                    backup_reference=backup_reference,
                )
        except Exception as exc:
            self._record_failed_attempt(
                alias=alias,
                operator=operator,
                target=expected_target,
                backup_reference=backup_reference,
                error=exc,
            )
            if isinstance(
                exc,
                (
                    CommandError,
                    PurgeBlockedError,
                    PurgeConfigurationError,
                    PurgeLockError,
                ),
            ):
                raise CommandError(str(exc)) from exc
            raise CommandError(f"业务数据清理失败，事务已回滚：{exc}") from exc

        total_rows = sum(deleted_counts.values())
        self.stdout.write(
            self.style.SUCCESS(
                f"清理完成：{len(deleted_counts)} 张表，共删除 {total_rows} 行；"
                f"清单版本 {PURGE_MANIFEST_VERSION}。"
            )
        )

    def _connection(self, alias):
        try:
            return connections[alias]
        except ConnectionDoesNotExist as exc:
            raise CommandError(f"数据库别名不存在：{alias}") from exc

    def _prepare(self, alias):
        try:
            return prepare_purge(alias)
        except (PurgeConfigurationError, ConnectionDoesNotExist) as exc:
            raise CommandError(str(exc)) from exc

    def _validate_execution_options(
        self,
        *,
        options,
        expected_target,
        backup_reference,
    ):
        missing = []
        if not (options["confirm_target"] or "").strip():
            missing.append("--confirm-target")
        if not (options["operator"] or "").strip():
            missing.append("--operator")
        if not backup_reference:
            missing.append("--backup-reference")
        if not options["maintenance_confirmed"]:
            missing.append("--maintenance-confirmed")
        if missing:
            raise CommandError("正式执行缺少参数：" + "、".join(missing))
        if options["confirm_target"].strip() != expected_target:
            raise CommandError(
                "目标库确认不匹配。请先 dry-run，并精确输入："
                f"--confirm-target {expected_target}"
            )

    def _operator(self, alias, username):
        user = (
            get_user_model()
            ._default_manager.using(alias)
            .filter(username=username.strip(), is_active=True, is_superuser=True)
            .first()
        )
        if user is None:
            raise CommandError("--operator 必须是目标数据库中的有效超级管理员。")
        return user

    def _render_report(self, report, *, dry_run):
        mode = "DRY-RUN（只读）" if dry_run else "EXECUTE（正式执行）"
        self.stdout.write(f"模式: {mode}")
        self.stdout.write(f"数据库别名: {report.database_alias}")
        self.stdout.write(f"确认目标: {report.target}")
        self.stdout.write(f"清单版本: {PURGE_MANIFEST_VERSION}")
        self.stdout.write(
            f"保留表: {len(report.present_preserved_tables)}；"
            f"清理表: {len(report.present_purged_tables)}"
        )

        self.stdout.write("\n[清理表与预估行数]")
        for table in sorted(report.present_purged_tables):
            estimate = report.estimated_rows.get(table)
            estimate_text = "未知" if estimate is None else str(estimate)
            self.stdout.write(f"  DELETE {table}: 约 {estimate_text} 行")

        self.stdout.write("\n[保留表]")
        for table in sorted(report.present_preserved_tables):
            self.stdout.write(f"  KEEP   {table}")

        missing = sorted(report.missing_preserved_tables | report.missing_purged_tables)
        if missing:
            self.stdout.write(self.style.WARNING("\n[尚不存在，将跳过]"))
            for table in missing:
                self.stdout.write(f"  MISSING {table}")

        if report.blocking_messages:
            self.stdout.write(self.style.ERROR("\n[阻塞问题]"))
            for message in report.blocking_messages:
                self.stdout.write(f"  BLOCK  {message}")
            if dry_run:
                self.stdout.write(
                    self.style.WARNING("预检发现阻塞问题；未写入数据库。")
                )
        else:
            suffix = "未写入数据库。" if dry_run else "允许进入事务清理。"
            self.stdout.write(self.style.SUCCESS(f"预检通过；{suffix}"))

    def _record_failed_attempt(
        self,
        *,
        alias,
        operator,
        target,
        backup_reference,
        error,
    ):
        try:
            record_audit_event(
                action="BUSINESS_DATA_PURGE",
                module="core.operations",
                user=operator,
                succeeded=False,
                metadata={
                    "source": "purge_business_data",
                    "database_alias": alias,
                    "target": target,
                    "backup_reference": backup_reference,
                    "manifest_version": PURGE_MANIFEST_VERSION,
                    "error_type": type(error).__name__,
                    "error": str(error)[:1000],
                },
                using=alias,
            )
        except Exception as audit_exc:  # pragma: no cover - last-resort reporting
            self.stderr.write(self.style.WARNING(f"失败审计写入失败：{audit_exc}"))
