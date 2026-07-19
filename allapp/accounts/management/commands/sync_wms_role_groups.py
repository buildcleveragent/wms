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
            "--ensure-defaults",
            action="store_true",
            help=("为已有规范组补充缺失的模板权限，并保留人工配置的额外权限。"),
        )
        parser.add_argument(
            "--prune",
            action="store_true",
            help="将规范组权限精确恢复为模板，删除人工调整的额外权限。",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        ensure_defaults = options["ensure_defaults"]
        prune = options["prune"]
        if ensure_defaults and prune:
            raise CommandError("--ensure-defaults 与 --prune 不能同时使用。")
        resolved = self._resolve_permissions()

        if dry_run:
            for role, template in ROLE_GROUP_TEMPLATES.items():
                group = Group.objects.filter(name=template.group_name).first()
                if group is None:
                    state = f"创建并初始化 {len(resolved[role])} 项模板权限"
                else:
                    current_ids = set(group.permissions.values_list("id", flat=True))
                    template_ids = {permission.pk for permission in resolved[role]}
                    if prune:
                        state = (
                            f"恢复模板，新增 {len(template_ids - current_ids)} 项，"
                            f"删除 {len(current_ids - template_ids)} 项"
                        )
                    elif ensure_defaults:
                        state = f"补充 {len(template_ids - current_ids)} 项模板权限"
                    else:
                        state = f"保留现有 {len(current_ids)} 项权限"
                self.stdout.write(f"[dry-run] {template.group_name} ({role})：{state}")
            return

        with transaction.atomic():
            for role, template in ROLE_GROUP_TEMPLATES.items():
                group, created = Group.objects.get_or_create(name=template.group_name)
                permission_ids = {permission.pk for permission in resolved[role]}
                if created or prune:
                    group.permissions.set(permission_ids)
                elif ensure_defaults:
                    group.permissions.add(*permission_ids)
                if created:
                    action = "已创建并初始化"
                elif prune:
                    action = "已恢复模板"
                elif ensure_defaults:
                    action = "已补充模板权限"
                else:
                    action = "已保留现有配置"
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
