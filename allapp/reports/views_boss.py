from __future__ import annotations

from rest_framework import permissions, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from allapp.accounts.access import AccessScope
from allapp.accounts.audit import record_audit_event
from allapp.accounts.models import UserRoleScope

from .services_boss import (
    build_boss_alert_payload,
    build_boss_home_payload,
    build_boss_inventory_payload,
)


class CanViewBossDashboard(permissions.BasePermission):
    def has_permission(self, request, view):
        user = request.user
        scope = AccessScope.for_user(user)
        return bool(
            user
            and user.is_authenticated
            and (
                user.is_superuser
                or (
                    scope.is_valid
                    and UserRoleScope.Role.WAREHOUSE_BOSS in scope.roles
                    and bool(scope.warehouse_ids)
                    and user.has_perm("reports.view_boss_dashboard")
                )
            )
        )


class BossScopedApiMixin:
    permission_classes = [CanViewBossDashboard]

    def _parse_int_param(self, request, name: str):
        raw = (request.query_params.get(name) or "").strip()
        if not raw:
            return None
        if not raw.isdigit():
            raise ValueError(f"{name} must be an integer id.")
        return int(raw)

    def _validate_scope(self, request, *, owner_id=None, warehouse_id=None):
        scope = AccessScope.for_user(request.user)
        if scope.owner_ids and owner_id and not scope.allows(owner_id=owner_id):
            raise PermissionDenied("No access to other owners in boss dashboard.")
        if scope.warehouse_ids and warehouse_id and not scope.allows(warehouse_id=warehouse_id):
            raise PermissionDenied("No access to other warehouses in boss dashboard.")


class BossHomeApi(BossScopedApiMixin, APIView):
    def get(self, request):
        try:
            owner_id = self._parse_int_param(request, "owner")
            warehouse_id = self._parse_int_param(request, "warehouse")
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        self._validate_scope(request, owner_id=owner_id, warehouse_id=warehouse_id)
        payload = build_boss_home_payload(
            user=request.user,
            owner_id=owner_id,
            warehouse_id=warehouse_id,
        )
        record_audit_event(
            action="QUERY",
            module="reports.boss.home",
            request=request,
            owner_id=owner_id,
            warehouse_id=warehouse_id,
        )
        return Response(payload)


class BossAlertApi(BossScopedApiMixin, APIView):
    def get(self, request):
        try:
            owner_id = self._parse_int_param(request, "owner")
            warehouse_id = self._parse_int_param(request, "warehouse")
            item_limit = self._parse_int_param(request, "item_limit") or 8
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        item_limit = max(1, min(item_limit, 20))
        self._validate_scope(request, owner_id=owner_id, warehouse_id=warehouse_id)
        payload = build_boss_alert_payload(
            user=request.user,
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            item_limit=item_limit,
        )
        record_audit_event(
            action="QUERY",
            module="reports.boss.alerts",
            request=request,
            owner_id=owner_id,
            warehouse_id=warehouse_id,
        )
        return Response(payload)


class BossInventoryApi(BossScopedApiMixin, APIView):
    def get(self, request):
        try:
            owner_id = self._parse_int_param(request, "owner")
            warehouse_id = self._parse_int_param(request, "warehouse")
            item_limit = self._parse_int_param(request, "item_limit") or 8
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        item_limit = max(1, min(item_limit, 20))
        self._validate_scope(request, owner_id=owner_id, warehouse_id=warehouse_id)
        payload = build_boss_inventory_payload(
            user=request.user,
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            item_limit=item_limit,
        )
        record_audit_event(
            action="QUERY",
            module="reports.boss.inventory",
            request=request,
            owner_id=owner_id,
            warehouse_id=warehouse_id,
        )
        return Response(payload)
