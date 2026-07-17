"""Create or synchronize the five canonical WMS capability groups."""

from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from allapp.accounts.roles import ROLE_GROUP_TEMPLATES


class Command(BaseCommand):
    help = "创建或同步五类 WMS 角色组；只同步能力，不分配用户或数据范围。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="只校验并显示计划，不修改数据库。",
        )
        parser.add_argument(
            "--prune",
            action="store_true",
            help="删除模板之外的组权限；默认保留人工追加权限。",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        prune = options["prune"]
        resolved = self._resolve_permissions()

        if dry_run:
            for role, template in ROLE_GROUP_TEMPLATES.items():
                state = "更新" if Group.objects.filter(name=template.group_name).exists() else "创建"
                self.stdout.write(
                    f"[dry-run] {state} {template.group_name} ({role})，"
                    f"模板权限 {len(resolved[role])} 项"
                )
            return

        with transaction.atomic():
            for role, template in ROLE_GROUP_TEMPLATES.items():
                group, created = Group.objects.get_or_create(name=template.group_name)
                permission_ids = {permission.pk for permission in resolved[role]}
                if prune:
                    group.permissions.set(permission_ids)
                else:
                    group.permissions.add(*permission_ids)
                action = "已创建" if created else "已同步"
                self.stdout.write(
                    self.style.SUCCESS(
                        f"{action} {template.group_name} ({role})，"
                        f"模板权限 {len(permission_ids)} 项"
                    )
                )

    def _resolve_permissions(self):
        dotted_permissions = {
            permission
            for template in ROLE_GROUP_TEMPLATES.values()
            for permission in template.permissions
        }
        resolved_by_name = {}
        missing = []
        duplicates = []
        for dotted_name in sorted(dotted_permissions):
            app_label, codename = dotted_name.split(".", 1)
            matches = list(
                Permission.objects.select_related("content_type").filter(
                    content_type__app_label=app_label,
                    codename=codename,
                )
            )
            if not matches:
                missing.append(dotted_name)
            elif len(matches) > 1:
                duplicates.append(dotted_name)
            else:
                resolved_by_name[dotted_name] = matches[0]

        if missing or duplicates:
            details = []
            if missing:
                details.append(f"缺少权限：{', '.join(missing)}")
            if duplicates:
                details.append(f"权限码不唯一：{', '.join(duplicates)}")
            raise CommandError("；".join(details) + "。请先执行 migrate。")

        return {
            role: tuple(resolved_by_name[name] for name in template.permissions)
            for role, template in ROLE_GROUP_TEMPLATES.items()
        }
