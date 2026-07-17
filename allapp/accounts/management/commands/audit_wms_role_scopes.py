"""Report role/scope migration risks without changing authorization state."""

import csv
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from allapp.accounts.access import AccessScope
from allapp.accounts.roles import infer_user_roles


FIELDS = (
    "user_id",
    "username",
    "is_active",
    "owner_id",
    "warehouse_id",
    "explicit_roles",
    "inferred_roles",
    "scope_owner_ids",
    "scope_warehouse_ids",
    "is_valid",
    "denial_reason",
    "risk_codes",
)


class Command(BaseCommand):
    help = "只读审计 WMS 用户角色、Group 与 owner/warehouse 范围冲突。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--format",
            choices=("text", "csv"),
            default="text",
            help="输出格式，默认 text。",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="同时输出没有发现风险的普通账号。",
        )
        parser.add_argument(
            "--include-inactive",
            action="store_true",
            help="包含停用账号。",
        )

    def handle(self, *args, **options):
        users = get_user_model().objects.filter(is_superuser=False).order_by("id")
        if not options["include_inactive"]:
            users = users.filter(is_active=True)

        rows = []
        for user in users.iterator():
            row = self._audit_user(user)
            if options["all"] or row["risk_codes"]:
                rows.append(row)

        if options["format"] == "csv":
            self._write_csv(rows)
        else:
            self._write_text(rows)

    def _audit_user(self, user):
        explicit_rows = list(
            user.role_scopes.filter(is_active=True).values(
                "role", "owner_id", "warehouse_id"
            )
        )
        explicit_roles = frozenset(row["role"] for row in explicit_rows)
        inferred_roles = infer_user_roles(user)
        scope = AccessScope.for_user(user)
        risk_codes = []

        if user.owner_id and user.warehouse_id:
            risk_codes.append("LEGACY_OWNER_WAREHOUSE_MIXED")
        if not explicit_rows:
            risk_codes.append("MISSING_EXPLICIT_SCOPE")
        if (
            len(explicit_roles) > 1
            or len(inferred_roles) > 1
            or (explicit_roles and inferred_roles and explicit_roles != inferred_roles)
        ):
            risk_codes.append("ROLE_GROUP_CONFLICT")
        if not scope.is_valid:
            risk_codes.append("INVALID_ACCESS_SCOPE")

        return {
            "user_id": user.pk,
            "username": user.get_username(),
            "is_active": user.is_active,
            "owner_id": user.owner_id or "",
            "warehouse_id": user.warehouse_id or "",
            "explicit_roles": "|".join(sorted(explicit_roles)),
            "inferred_roles": "|".join(sorted(inferred_roles)),
            "scope_owner_ids": "|".join(str(value) for value in sorted(scope.owner_ids)),
            "scope_warehouse_ids": "|".join(
                str(value) for value in sorted(scope.warehouse_ids)
            ),
            "is_valid": scope.is_valid,
            "denial_reason": scope.denial_reason,
            "risk_codes": "|".join(risk_codes),
        }

    def _write_csv(self, rows):
        buffer = StringIO()
        writer = csv.DictWriter(buffer, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
        self.stdout.write(buffer.getvalue(), ending="")

    def _write_text(self, rows):
        if not rows:
            self.stdout.write("未发现角色范围风险。")
            return
        for row in rows:
            self.stdout.write(
                "user_id={user_id} username={username} risks={risk_codes} "
                "explicit={explicit_roles} inferred={inferred_roles} "
                "valid={is_valid} reason={denial_reason}".format(**row)
            )
        self.stdout.write(f"共发现 {len(rows)} 个需复核账号。")
