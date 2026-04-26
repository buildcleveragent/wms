from __future__ import annotations

from rest_framework import permissions, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from .services_boss import (
    build_boss_alert_payload,
    build_boss_home_payload,
    build_boss_inventory_payload,
)


class BossScopedApiMixin:
    permission_classes = [permissions.IsAuthenticated]

    def _parse_int_param(self, request, name: str):
        raw = (request.query_params.get(name) or "").strip()
        if not raw:
            return None
        if not raw.isdigit():
            raise ValueError(f"{name} must be an integer id.")
        return int(raw)

    def _validate_scope(self, request, *, owner_id=None, warehouse_id=None):
        user_owner_id = getattr(request.user, "owner_id", None)
        user_warehouse_id = getattr(request.user, "warehouse_id", None)
        if user_owner_id and owner_id and owner_id != user_owner_id and not user_warehouse_id:
            raise PermissionDenied("No access to other owners in boss dashboard.")
        if user_warehouse_id and warehouse_id and warehouse_id != user_warehouse_id:
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
        return Response(payload)
