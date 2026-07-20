"""Reconcile user role-group memberships from explicit UserRoleScope rows."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from allapp.accounts.audit import record_audit_event
from allapp.accounts.role_memberships import (
    plan_user_role_membership,
    sync_user_role_membership,
)
from allapp.accounts.roles import CANONICAL_ROLE_GROUP_NAMES


class Command(BaseCommand):
    help = "按 UserRoleScope 同步用户的 WMS 规范角色组成员关系。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="只校验并显示计划，不修改数据库。",
        )

    def handle(self, *args, **options):
        self._validate_canonical_groups()
        users = list(get_user_model().objects.order_by("id"))
        plans = self._preflight(users)

        for user, change in plans:
            if change.changed:
                self.stdout.write(
                    f"{'[dry-run] ' if options['dry_run'] else ''}{user.username}："
                    f"角色 {change.role or '无'}；"
                    f"新增 {', '.join(change.added) or '无'}；"
                    f"移除 {', '.join(change.removed) or '无'}"
                )

        changed_count = sum(change.changed for _, change in plans)
        if options["dry_run"]:
            self.stdout.write(
                self.style.SUCCESS(
                    f"校验通过：共 {len(users)} 个用户，"
                    f"{changed_count} 个需要同步；未写入。"
                )
            )
            return

        with transaction.atomic():
            for user, planned_change in plans:
                if not planned_change.changed:
                    continue
                before_groups = sorted(user.groups.values_list("name", flat=True))
                actual_change = sync_user_role_membership(user)
                after_groups = sorted(user.groups.values_list("name", flat=True))
                record_audit_event(
                    action="USER_ROLE_GROUP_SYNC",
                    module="accounts.authorization",
                    obj=user,
                    before={"groups": before_groups},
                    after={"groups": after_groups},
                    metadata={
                        "source": "sync_wms_user_role_memberships",
                        "role": actual_change.role or "",
                        "desired_group": actual_change.desired_group or "",
                        "added": list(actual_change.added),
                        "removed": list(actual_change.removed),
                    },
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"同步完成：共 {len(users)} 个用户，已调整 {changed_count} 个。"
            )
        )

    def _validate_canonical_groups(self):
        existing = set(
            Group.objects.filter(name__in=CANONICAL_ROLE_GROUP_NAMES).values_list(
                "name", flat=True
            )
        )
        missing = sorted(CANONICAL_ROLE_GROUP_NAMES - existing)
        if missing:
            raise CommandError(
                "缺少规范角色组："
                + "、".join(missing)
                + "。请先执行 python manage.py sync_wms_role_groups。"
            )

    def _preflight(self, users):
        plans = []
        errors = []
        for user in users:
            try:
                plans.append((user, plan_user_role_membership(user)))
            except ValidationError as exc:
                errors.append(f"{user.username}: {'；'.join(exc.messages)}")
        if errors:
            raise CommandError(
                "用户角色范围校验失败，未执行同步：\n" + "\n".join(errors)
            )
        return plans
