from rest_framework import permissions, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from .services_pda import build_pda_throughput_payload, parse_pda_throughput_range


class PdaThroughputApi(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _parse_int_param(self, request, name):
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
            raise PermissionDenied("No access to other owners.")
        if user_warehouse_id and warehouse_id and warehouse_id != user_warehouse_id:
            raise PermissionDenied("No access to other warehouses.")

    def get(self, request):
        try:
            mode, start_date, end_date = parse_pda_throughput_range(request.query_params)
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
        return Response(payload)
