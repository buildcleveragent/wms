from rest_framework import permissions, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from allapp.accounts.access import AccessScope
from allapp.accounts.audit import record_audit_event

from .services_pda import (
    build_pda_throughput_detail_payload,
    build_pda_throughput_payload,
    normalize_pda_throughput_metric,
    parse_pda_throughput_range,
)


class CanViewThroughput(permissions.BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            return True
        scope = AccessScope.for_user(user)
        return scope.is_valid and (
            user.has_perm("reports.view_warehouse_operations")
            or user.has_perm("reports.view_owner_operations")
        )


class PdaThroughputApi(APIView):
    permission_classes = [CanViewThroughput]

    def _parse_int_param(self, request, name):
        raw = (request.query_params.get(name) or "").strip()
        if not raw:
            return None
        if not raw.isdigit():
            raise ValueError(f"{name} must be an integer id.")
        return int(raw)

    def _validate_scope(self, request, *, owner_id=None, warehouse_id=None):
        scope = AccessScope.for_user(request.user)
        if scope.owner_ids and owner_id and not scope.allows(owner_id=owner_id):
            raise PermissionDenied("No access to other owners.")
        if scope.warehouse_ids and warehouse_id and not scope.allows(warehouse_id=warehouse_id):
            raise PermissionDenied("No access to other warehouses.")

    def get(self, request):
        try:
            mode, start_date, end_date = parse_pda_throughput_range(
                request.query_params
            )
            owner_id = self._parse_int_param(request, "owner")
            warehouse_id = self._parse_int_param(request, "warehouse")
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        self._validate_scope(request, owner_id=owner_id, warehouse_id=warehouse_id)
        payload = build_pda_throughput_payload(
            user=request.user,
            mode=mode,
            start_date=start_date,
            end_date=end_date,
            owner_id=owner_id,
            warehouse_id=warehouse_id,
        )
        record_audit_event(
            action="QUERY",
            module="reports.pda.throughput",
            request=request,
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            metadata={"start_date": str(start_date), "end_date": str(end_date)},
        )
        return Response(payload)


class PdaThroughputDetailApi(PdaThroughputApi):
    def get(self, request):
        try:
            mode, start_date, end_date = parse_pda_throughput_range(
                request.query_params
            )
            metric = normalize_pda_throughput_metric(request.query_params.get("metric"))
            owner_id = self._parse_int_param(request, "owner")
            warehouse_id = self._parse_int_param(request, "warehouse")
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        self._validate_scope(request, owner_id=owner_id, warehouse_id=warehouse_id)
        payload = build_pda_throughput_detail_payload(
            user=request.user,
            mode=mode,
            start_date=start_date,
            end_date=end_date,
            metric=metric,
            owner_id=owner_id,
            warehouse_id=warehouse_id,
        )
        record_audit_event(
            action="QUERY_DETAIL",
            module="reports.pda.throughput",
            request=request,
            owner_id=owner_id,
            warehouse_id=warehouse_id,
            metadata={
                "start_date": str(start_date),
                "end_date": str(end_date),
                "metric": metric,
                "rows": len(payload.get("items", [])),
            },
        )
        return Response(payload)
