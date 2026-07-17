from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from allapp.accounts.models import SystemLog


ASSISTED_PERMISSION = "outbound.process_warehouse_assisted_outbound"
TASK_CLAIM_PERMISSION = "tasking.claim_task_as_wh_operator"
VIEW_ALL_PERMISSION = "outbound.view_all_outbound_orders"

ROLE_PERMISSIONS = {
    "outbound_salesperson": "outbound.submit_outbound_as_owner_buyers",
    "outbound_owner_manager": "outbound.approve_outbound_as_owner_manager",
    "outbound_warehouse_manager": "outbound.approve_outbound_as_wh_manager",
    "task_operator": TASK_CLAIM_PERMISSION,
    "warehouse_assisted_operator": ASSISTED_PERMISSION,
    "global_outbound_viewer": VIEW_ALL_PERMISSION,
}

LEGACY_PERMISSION_PAIRS = {
    "inbound.submit_as_owner_buyers": "outbound.submit_outbound_as_owner_buyers",
    "inbound.approve_as_owner_manager": "outbound.approve_outbound_as_owner_manager",
    "inbound.approve_as_wh_manager": "outbound.approve_outbound_as_wh_manager",
}


class Command(BaseCommand):
    help = (
        "只读审计活跃账号的出库/任务权限与 owner/warehouse 绑定；"
        "结果以 JSON 或 CSV 输出，不修改任何数据。"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--format",
            choices=("json", "csv"),
            default="json",
            help="输出格式，默认 json。",
        )
        parser.add_argument(
            "--output",
            help="可选输出文件；不传时写到标准输出。",
        )
        parser.add_argument(
            "--since-days",
            type=int,
            default=30,
            help="检查 SystemLog 中最近多少天的出库/任务活动，默认 30。",
        )
        parser.add_argument(
            "--include-inactive",
            action="store_true",
            help="同时审计停用账号；默认仅审计活跃账号。",
        )

    def handle(self, *args, **options):
        since_days = options["since_days"]
        if since_days < 0:
            raise CommandError("--since-days 不能小于 0。")

        cutoff = timezone.now() - timedelta(days=since_days)
        activity = self._recent_activity(cutoff)
        users = self._users(include_inactive=options["include_inactive"])
        rows = [self._audit_user(user, activity.get(user.username, set())) for user in users]

        output_format = options["format"]
        if output_format == "csv":
            rendered = self._render_csv(rows)
        else:
            rendered = json.dumps(
                {
                    "generated_at": timezone.now().isoformat(),
                    "activity_since": cutoff.isoformat(),
                    "account_count": len(rows),
                    "accounts": rows,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )

        output = options.get("output")
        if output:
            path = Path(output).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rendered + ("" if rendered.endswith("\n") else "\n"), encoding="utf-8")
            self.stdout.write(str(path))
            return
        self.stdout.write(rendered)

    @staticmethod
    def _users(*, include_inactive):
        queryset = (
            get_user_model()
            .objects.select_related("owner", "warehouse")
            .prefetch_related("groups__permissions", "user_permissions")
            .order_by("id")
        )
        if not include_inactive:
            queryset = queryset.filter(is_active=True)
        return queryset

    @staticmethod
    def _recent_activity(cutoff):
        relevant = SystemLog.objects.filter(occurred_at__gte=cutoff).filter(
            Q(module__icontains="outbound")
            | Q(module__icontains="task")
            | Q(content__icontains="/api/outbound/")
            | Q(content__icontains="/api/pda/pick-tasks/")
            | Q(content__icontains="/api/tasks/")
        )
        activity = defaultdict(set)
        for username, module, content in relevant.values_list(
            "username", "module", "content"
        ):
            text = f"{module or ''} {content or ''}".lower()
            if "outbound" in text or "/api/outbound/" in text:
                activity[username].add("outbound")
            if "task" in text or "/api/pda/pick-tasks/" in text:
                activity[username].add("tasking")
        return activity

    @staticmethod
    def _audit_user(user, recent_activity):
        permissions = set(user.get_all_permissions())

        def has_perm(permission):
            return user.is_superuser or permission in permissions

        has_assisted = has_perm(ASSISTED_PERMISSION)
        has_task_claim = has_perm(TASK_CLAIM_PERMISSION)
        can_assist = (
            user.owner_id is None
            and user.warehouse_id is not None
            and has_assisted
            and has_task_claim
        )

        if user.is_superuser or has_perm(VIEW_ALL_PERMISSION):
            outbound_scope = "global_read"
        elif user.owner_id and user.warehouse_id:
            outbound_scope = "owner_warehouse_intersection"
        elif user.owner_id:
            outbound_scope = "owner"
        elif user.warehouse_id and has_perm("outbound.view_outboundorder"):
            outbound_scope = "warehouse"
        elif can_assist:
            outbound_scope = "warehouse_assisted_only"
        else:
            outbound_scope = "none"

        if user.is_superuser:
            task_scope = "global"
        elif user.owner_id and user.warehouse_id:
            task_scope = "owner_warehouse_intersection"
        elif user.owner_id:
            task_scope = "owner"
        elif user.warehouse_id and has_perm("tasking.view_wmstask"):
            task_scope = "warehouse"
        elif can_assist:
            task_scope = "warehouse_assisted_only"
        else:
            task_scope = "none"

        legacy_permission_gaps = sorted(
            f"{legacy}->{outbound}"
            for legacy, outbound in LEGACY_PERMISSION_PAIRS.items()
            if legacy in permissions and outbound not in permissions
        )
        would_deny_reasons = []
        if "outbound" in recent_activity and outbound_scope == "none":
            would_deny_reasons.append("recent_outbound_activity_has_no_new_scope")
        if "tasking" in recent_activity and task_scope == "none":
            would_deny_reasons.append("recent_task_activity_has_no_new_scope")

        risk_codes = []
        if user.owner_id is None and user.warehouse_id is None:
            risk_codes.append("UNBOUND_ACCOUNT")
        if (
            has_perm("products.manage_all_owner_products")
            and not has_task_claim
            and not user.is_superuser
        ):
            risk_codes.append("PRODUCT_MANAGER_WITHOUT_TASK_CLAIM")
        if legacy_permission_gaps:
            risk_codes.append("LEGACY_INBOUND_PERMISSION_GAP")
        if has_assisted and not user.is_superuser and not can_assist:
            if user.owner_id and user.warehouse_id:
                risk_codes.append("MIXED_BINDING_WITH_ASSISTED_PERMISSION")
            else:
                risk_codes.append("INVALID_ASSISTED_OPERATOR_BINDING_OR_PERMISSION")
        if would_deny_reasons:
            risk_codes.append("RECENT_ACTIVITY_WOULD_DENY")

        return {
            "user_id": user.id,
            "username": user.username,
            "is_active": user.is_active,
            "is_staff": user.is_staff,
            "is_superuser": user.is_superuser,
            "owner_id": user.owner_id,
            "warehouse_id": user.warehouse_id,
            "last_login": user.last_login.isoformat() if user.last_login else None,
            "roles": {
                name: has_perm(permission)
                for name, permission in ROLE_PERMISSIONS.items()
            },
            "can_process_warehouse_assisted_outbound": can_assist,
            "outbound_scope": outbound_scope,
            "task_scope": task_scope,
            "recent_activity": sorted(recent_activity),
            "legacy_permission_gaps": legacy_permission_gaps,
            "would_deny_reasons": would_deny_reasons,
            "risk_codes": risk_codes,
        }

    @staticmethod
    def _render_csv(rows):
        fieldnames = [
            "user_id",
            "username",
            "is_active",
            "is_staff",
            "is_superuser",
            "owner_id",
            "warehouse_id",
            "last_login",
            *ROLE_PERMISSIONS,
            "can_process_warehouse_assisted_outbound",
            "outbound_scope",
            "task_scope",
            "recent_activity",
            "legacy_permission_gaps",
            "would_deny_reasons",
            "risk_codes",
        ]
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            flat = {key: value for key, value in row.items() if key != "roles"}
            flat.update(row["roles"])
            for key in (
                "recent_activity",
                "legacy_permission_gaps",
                "would_deny_reasons",
                "risk_codes",
            ):
                flat[key] = json.dumps(flat[key], ensure_ascii=False)
            writer.writerow(flat)
        return output.getvalue()
